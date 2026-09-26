"""Freeze allowlisted historical resource summaries; never commission an endpoint.

Offline, standard-library only. Decimal quantities are serialized as strings.
The supplied manifest hash binds the extraction, not the physical measurements.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

VERSION = "historical-resource-ledger-v1"
HOUSE = "vallery"
MEASURED_SCOPE = "partial_shelly_two_channels"
MODELED_SCOPE = "whole_controlled_equipment_runtime"
WATER_FIELDS = (
    "quality_filtered_meter_gal",
    "attributed_gal",
    "ambiguous_gal",
    "manual_or_unattributed_gal",
    "climate_wetting_gal",
    "wall_irrigation_gal",
    "wall_fertigation_gal",
    "unsupported_path_gal",
    "conservation_error_gal",
)
SOURCE_FILES = (
    "ingestor/tasks/ha.py",
    "ingestor/tasks/_common.py",
    "verdify_schemas/telemetry.py",
    "verdify_schemas/external.py",
    "db/migrations/194-scope-aware-resource-accounting.sql",
    "api/main.py",
)


class ContractError(ValueError):
    """Messages contain fixed labels only, never raw input values."""


def require(condition, label):
    if not condition:
        raise ContractError(label)


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def valid_hash(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def _constant(_value):
    raise ContractError("nonfinite JSON constant")


def parse(raw):
    try:
        return json.loads(raw, parse_float=Decimal, parse_constant=_constant, object_pairs_hook=_object)
    except (ValueError, UnicodeError, RecursionError):
        raise ContractError("invalid JSON input") from None


def read_bytes(path, limit):
    require(not path.is_symlink() and path.is_file(), "input must be a regular non-symlink file")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    require(len(raw) <= limit, "input exceeds size limit")
    return raw


def iso_date(value):
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ContractError("invalid ISO date") from None
    require(parsed.isoformat() == value, "noncanonical ISO date")
    return parsed


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise ContractError("invalid timestamp") from None
    require(parsed.tzinfo is not None and parsed.utcoffset() is not None, "timestamp requires timezone")
    return parsed


def number(row, field, *, nonnegative=True, percentage=False):
    value = row.get(field)
    if value is None:
        return None
    require(type(value) in (int, Decimal), "resource quantity must be numeric or null")
    value = Decimal(value)
    require(value.is_finite(), "resource quantity must be finite")
    require(not nonnegative or value >= 0, "resource quantity must be nonnegative")
    require(not percentage or value <= 100, "coverage exceeds 100 percent")
    return value


def flag(row, field):
    value = row.get(field)
    require(value is None or type(value) is bool, "source eligibility must be boolean or null")
    return value


def quality(row, field):
    value = row.get(field)
    require(value is None or (isinstance(value, str) and re.fullmatch(r"[a-z_]{1,64}", value)), "invalid quality label")
    return value


def section(snapshot, field, day):
    row = snapshot.get(field)
    if row is None:
        return {}, True
    require(isinstance(row, dict), "resource section must be an object or null")
    require(row.get("date") == day and row.get("greenhouse_id") == HOUSE, "nested resource identity mismatch")
    return row, False


def residual(total, parts):
    if total is None or any(value is None for value in parts):
        return None
    return total - sum(parts, Decimal(0))


def daily(snapshot, day):
    require(isinstance(snapshot, dict), "snapshot must be an object")
    require(snapshot.get("date") == day and snapshot.get("greenhouse_id") == HOUSE, "snapshot identity mismatch")
    water, water_missing = section(snapshot, "water", day)
    energy, energy_missing = section(snapshot, "energy", day)
    w = {field: number(water, field, nonnegative=field != "conservation_error_gal") for field in WATER_FIELDS}
    w.update(
        section_missing=water_missing,
        source_available_for_scoring=flag(water, "available_for_scoring"),
        ledger_quality=quality(water, "ledger_quality"),
        resource_quality=quality(water, "resource_quality"),
    )
    w["computed_conservation_error_gal"] = residual(
        w["quality_filtered_meter_gal"],
        [w[key] for key in ("attributed_gal", "ambiguous_gal", "manual_or_unattributed_gal")],
    )
    w["computed_attribution_scope_error_gal"] = residual(
        w["attributed_gal"],
        [
            w[key]
            for key in ("climate_wetting_gal", "wall_irrigation_gal", "wall_fertigation_gal", "unsupported_path_gal")
        ],
    )
    commands = water.get("command_only_runs")
    require(commands is None or (type(commands) is int and commands >= 0), "invalid command-only count")
    w["command_only_runs"] = commands
    for field, expected in (("measured_scope", MEASURED_SCOPE), ("modeled_scope", MODELED_SCOPE)):
        require(energy.get(field) in (None, expected), "unsupported electricity scope")
    e = {
        "section_missing": energy_missing,
        "measured_scope": energy.get("measured_scope"),
        "modeled_scope": energy.get("modeled_scope"),
        # Signed meter values can represent export. Never abs or clamp them here.
        "measured_kwh": number(energy, "measured_kwh", nonnegative=False),
        "modeled_kwh": number(energy, "kwh_estimated"),
        "modeled_kwh_low": number(energy, "modeled_kwh_low"),
        "modeled_kwh_high": number(energy, "modeled_kwh_high"),
        "meter_coverage_pct": number(energy, "meter_coverage_pct", percentage=True),
        "runtime_coverage_pct": number(energy, "runtime_coverage_pct", percentage=True),
        "source_measured_available_for_scoring": flag(energy, "measured_available_for_scoring"),
        "source_modeled_available_for_scoring": flag(energy, "modeled_available_for_scoring"),
        "measured_quality": quality(energy, "measured_quality"),
        "model_quality": quality(energy, "model_quality"),
    }
    issues = []
    if water_missing:
        issues.append("water_section_unavailable")
    if energy_missing:
        issues.append("energy_section_unavailable")
    for key in WATER_FIELDS:
        if w[key] is None:
            issues.append(f"missing_{key}")
    for key in ("computed_conservation_error_gal", "computed_attribution_scope_error_gal"):
        if w[key] is not None and abs(w[key]) > Decimal("0.001"):
            issues.append(key)
    if (
        w["conservation_error_gal"] is not None
        and w["computed_conservation_error_gal"] is not None
        and abs(w["conservation_error_gal"] - w["computed_conservation_error_gal"]) > Decimal("0.001")
    ):
        issues.append("reported_conservation_error_mismatch")
    if w["source_available_for_scoring"] is True and (
        any(w[key] is None for key in WATER_FIELDS)
        or any("conservation" in issue or "attribution" in issue for issue in issues)
        or w["resource_quality"] != "ok"
        or w["ledger_quality"] != "ok"
    ):
        issues.append("source_eligible_water_requires_review")
    for prefix in ("measured", "modeled"):
        if e[f"{prefix}_kwh"] is not None and e[f"{prefix}_scope"] is None:
            issues.append(f"{prefix}_value_without_scope")
        if e[f"source_{prefix}_available_for_scoring"] is True and (
            e[f"{prefix}_kwh"] is None or e[f"{prefix}_scope"] is None
        ):
            issues.append(f"{prefix}_source_eligible_without_value_or_scope")
    low, point, high = (e[key] for key in ("modeled_kwh_low", "modeled_kwh", "modeled_kwh_high"))
    if all(value is not None for value in (low, point, high)) and not low <= point <= high:
        issues.append("modeled_bounds_do_not_bracket_point")
    return {"date": day, "water": w, "energy": e, "audit_issues": issues}


def aggregate(rows, section_name, field, eligibility=None, scope=None):
    selected = [row[section_name] for row in rows if eligibility is None or row[section_name][eligibility] is True]
    values = [
        row[field] for row in selected if row[field] is not None and (scope is None or row.get(scope[0]) == scope[1])
    ]
    subtotal = sum(values, Decimal(0)) if values else None
    return {
        "selected_days": len(selected),
        "observed_days": len(values),
        "missing_days": len(selected) - len(values),
        "observed_subtotal": subtotal,
        "complete_total": subtotal if values and len(values) == len(selected) else None,
    }


def build(evidence_dir, manifest_path, expected_manifest_hash, start, end):
    first, stop = iso_date(start), iso_date(end)
    require(0 < (stop - first).days <= 62, "window must contain 1 to 62 days, end exclusive")
    require(valid_hash(expected_manifest_hash), "manifest SHA256 required")
    manifest_raw = read_bytes(manifest_path, 5_000_000)
    require(sha256(manifest_raw) == expected_manifest_hash, "manifest hash mismatch")
    manifest = parse(manifest_raw)
    require(isinstance(manifest, dict) and isinstance(manifest.get("requests"), list), "invalid extraction manifest")
    completed = timestamp(manifest.get("completed_at"))
    names = [f"resources-{(first + timedelta(days=index)).isoformat()}.json" for index in range((stop - first).days)]
    index = {}
    for entry in manifest["requests"]:
        require(isinstance(entry, dict) and isinstance(entry.get("file"), str), "invalid manifest request")
        name = entry["file"]
        require(Path(name).name == name and name not in (".", ".."), "manifest paths must be basenames")
        if name in names:
            require(name not in index, "duplicate resource date in manifest")
            index[name] = entry
    rows, inputs = [], []
    for name in names:
        require(name in index, "missing requested day in manifest")
        entry = index[name]
        day = name.removeprefix("resources-").removesuffix(".json")
        require(type(entry.get("status")) is int and entry["status"] == 200, "snapshot HTTP status must be 200")
        require(entry.get("content_type") == "application/json", "snapshot content type must be JSON")
        require(
            entry.get("url") == f"https://api.verdify.ai/api/v1/resources/daily?date={day}", "snapshot URL mismatch"
        )
        requested = timestamp(entry.get("requested_at"))
        require(requested <= completed, "request follows extraction completion")
        require(
            iso_date(day) < requested.astimezone(ZoneInfo("America/Denver")).date(),
            "snapshot day not completed at extraction",
        )
        require(valid_hash(entry.get("sha256")), "invalid snapshot hash")
        raw = read_bytes(evidence_dir / name, 2_000_000)
        require(type(entry.get("bytes")) is int and len(raw) == entry["bytes"], "snapshot byte count mismatch")
        require(sha256(raw) == entry["sha256"], "snapshot hash mismatch")
        rows.append(daily(parse(raw), day))
        inputs.append({key: entry[key] for key in ("file", "url", "requested_at", "status", "bytes", "sha256")})
    repository = Path(__file__).resolve().parents[2]
    return {
        "contract_version": VERSION,
        "greenhouse_id": HOUSE,
        "window": {"start_inclusive": start, "end_exclusive": end, "timezone": "America/Denver", "days": len(rows)},
        "provenance": {
            "manifest_sha256": expected_manifest_hash,
            "extraction_completed_at": manifest["completed_at"],
            "tool_sha256": sha256(Path(__file__).read_bytes()),
            "source_sha256": {name: sha256((repository / name).read_bytes()) for name in SOURCE_FILES},
            "inputs": inputs,
        },
        "eligibility": {
            "water_commissioned": False,
            "partial_electricity_commissioned": False,
            "whole_resource_claim_eligible": False,
            "cost_claim_eligible": False,
            "physical_proof_eligible": False,
            "experiment_endpoint_eligible": False,
            "reason": "daily_rollups_do_not_establish_calibration_circuit_identity_or_raw_sample_continuity",
            "gas_therms": None,
            "interior_dli": None,
            "resource_cost": None,
            "measured_uncertainty": None,
            "scientific_minimum_coverage": None,
        },
        "summary": {
            "water": {key: aggregate(rows, "water", key) for key in WATER_FIELDS},
            "source_eligible_water": aggregate(
                rows, "water", "quality_filtered_meter_gal", "source_available_for_scoring"
            ),
            "partial_electricity": {
                "scope": MEASURED_SCOPE,
                **aggregate(rows, "energy", "measured_kwh", scope=("measured_scope", MEASURED_SCOPE)),
            },
            "modeled_electricity": {
                "scope": MODELED_SCOPE,
                **aggregate(rows, "energy", "modeled_kwh", scope=("modeled_scope", MODELED_SCOPE)),
            },
            "source_measured_eligible_days": sum(
                row["energy"]["source_measured_available_for_scoring"] is True for row in rows
            ),
            "source_modeled_eligible_days": sum(
                row["energy"]["source_modeled_available_for_scoring"] is True for row in rows
            ),
            "water_quality_days": dict(
                sorted(Counter(row["water"]["ledger_quality"] or "unavailable" for row in rows).items())
            ),
            "days_with_audit_issues": sum(bool(row["audit_issues"]) for row in rows),
        },
        "days": rows,
        "limitations": [
            "Hashes bind supplied bytes; they do not authenticate the server, historical deployed source, or physical sensors.",
            "Source hashes identify the audited checkout, not the code running at historical extraction time.",
            "Missing values remain null; an observed subtotal is not a complete-period total.",
            "Source scoring flags are retained as reported and never promoted to scientific commissioning.",
            "Model coefficient bounds are not meter uncertainty or statistical confidence intervals.",
            "No subtraction or addition of partial measured and whole modeled electricity is defined.",
            "Runtime and vent-open minutes are burden diagnostics, not motor energy, gallons, savings, or cost.",
            "Current health freshness is deliberately excluded from completed-day evidence.",
        ],
    }


def encode(value):
    def decimal_text(item):
        if isinstance(item, Decimal):
            return format(item, "f")
        raise TypeError("unsupported output type")

    return (json.dumps(value, indent=2, sort_keys=True, default=decimal_text, allow_nan=False) + "\n").encode()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True, help="exclusive local date")
    parser.add_argument("--output", type=Path, required=True, help="new file only")
    args = parser.parse_args(argv)
    try:
        result = build(args.evidence_dir, args.manifest, args.manifest_sha256, args.start, args.end)
        raw = encode(result)
        with args.output.open("xb") as stream:
            stream.write(raw)
    except (ContractError, OSError, OverflowError):
        print(
            "Resource ledger refused: invalid/unavailable input or output already exists; no input values disclosed.",
            file=sys.stderr,
        )
        return 2
    print(f"{VERSION}: {len(result['days'])} days; output sha256={sha256(raw)}; commissioned=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
