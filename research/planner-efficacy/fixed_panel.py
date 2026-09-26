"""Frozen north/east/west historical sensitivity, not a locked trial endpoint.

Offline only. Inputs are explicitly exported database flush snapshots, not fresh
per-probe observations. No changing average, current crop resolver, interpolation,
device connection, assignment filtering or hidden missing-member renormalization.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

ZONES = ("north", "east", "west")
AXES = ("temp", "vpd")
FIELDS = tuple(f"{axis}_{zone}" for zone in ZONES for axis in AXES)
MINUTE = timedelta(minutes=1)
BIN = timedelta(minutes=15)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def keys(value, required, optional=()):
    if not isinstance(value, dict) or set(value) - set(required) - set(optional) or set(required) - set(value):
        raise ValueError("input object fields do not match the fixed-panel contract")


def timestamp(value):
    if not isinstance(value, str):
        raise TypeError("timestamp must be an explicit-offset ISO string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError("invalid timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp requires an explicit UTC offset")
    return parsed.astimezone(UTC)


def aligned(value):
    ts = timestamp(value)
    if ts.minute % 15 or ts.second or ts.microsecond:
        raise ValueError("window and target boundaries must align to UTC 15-minute bins")
    return ts


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value):
        raise ValueError("bounded version/contributor identifier required")


def sha(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("SHA-256 evidence identity required")


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def mean(values):
    # Deterministic input ordering and divide before summing to avoid a sum
    # overflow for otherwise finite observations. Invalid results fail closed.
    result = math.fsum(v / len(values) for v in sorted(values))
    if not math.isfinite(result):
        raise ValueError("nonfinite aggregate")
    return result


def validate(bundle, contract):
    keys(
        bundle,
        ("contract_version", "sample_basis", "greenhouse_id", "exported_at", "window_start", "window_end", "rows"),
    )
    if type(bundle["contract_version"]) is not int or bundle["contract_version"] != 1:
        raise ValueError("unsupported export contract")
    if bundle["sample_basis"] != "database_flush_snapshot" or bundle["greenhouse_id"] != "vallery":
        raise ValueError("explicit vallery database-flush snapshot basis required")
    start, end = aligned(bundle["window_start"]), aligned(bundle["window_end"])
    if not start < end <= start + timedelta(days=62) or timestamp(bundle["exported_at"]) < end:
        raise ValueError("completed positive window of at most 62 days required")
    keys(
        contract,
        (
            "contract_version",
            "panel_version",
            "target_version",
            "target_basis",
            "target_evidence_sha256",
            "minimum_minutes_per_bin",
            "members",
            "targets",
        ),
    )
    if type(contract["contract_version"]) is not int or contract["contract_version"] != 1:
        raise ValueError("unsupported measurement contract")
    for name in ("panel_version", "target_version"):
        identifier(contract[name])
    if contract["target_basis"] not in ("frozen_historical_crop_definition", "fixed_counterfactual_crop_definition"):
        raise ValueError(
            "explicit frozen crop target basis required; dispatched/current-resolver bands are not accepted"
        )
    sha(contract["target_evidence_sha256"])
    minimum = contract["minimum_minutes_per_bin"]
    if type(minimum) is not int or not 1 <= minimum <= 15:
        raise ValueError("declare a minimum of 1–15 distinct complete-panel minutes per bin")
    members = contract["members"]
    if not isinstance(members, list) or len(members) != 3:
        raise ValueError("exactly the fixed north/east/west panel is required")
    zones, contributors = [], []
    for member in members:
        keys(
            member,
            ("zone", "contributor_id", "identity_evidence_sha256", "valid_from", "valid_to", "temp_field", "vpd_field"),
        )
        zone = member["zone"]
        if zone not in ZONES or any(member[f"{axis}_field"] != f"{axis}_{zone}" for axis in AXES):
            raise ValueError("panel members must use their explicit north/east/west columns")
        identifier(member["contributor_id"])
        sha(member["identity_evidence_sha256"])
        if timestamp(member["valid_from"]) > start or timestamp(member["valid_to"]) < end:
            raise ValueError("one contributor identity per zone must cover the whole analysis window")
        zones.append(zone)
        contributors.append(member["contributor_id"])
    if set(zones) != set(ZONES) or len(set(contributors)) != 3:
        raise ValueError("zones and contributor identities must be unique")
    if not isinstance(contract["targets"], list) or not isinstance(bundle["rows"], list):
        raise TypeError("targets and rows must be arrays")
    targets = {}
    for target in contract["targets"]:
        keys(target, ("bucket_start", "temp_low", "temp_high", "vpd_low", "vpd_high"))
        ts = aligned(target["bucket_start"])
        if not start <= ts < end or ts in targets:
            raise ValueError("target bins must be unique and inside the declared window")
        targets[ts] = target
    return start, end, targets


def metrics(values, low, high):
    low_distance = max(low - values, 0.0)
    high_distance = max(values - high, 0.0)
    distance = low_distance + high_distance
    if not math.isfinite(distance):
        raise ValueError("nonfinite distance")
    return {
        "mean": values,
        "in_band": distance == 0,
        "low_distance": low_distance,
        "high_distance": high_distance,
        "outside_distance": distance,
    }


def longest_run(flags):
    current = longest = 0
    for flag in flags:
        current = current + 1 if flag else 0
        longest = max(longest, current)
    return longest


def analyze(bundle, contract):
    start, end, targets = validate(bundle, contract)
    by_exact = defaultdict(list)
    excluded = Counter()
    for row in bundle["rows"]:
        keys(row, ("ts", "greenhouse_id"), FIELDS)
        ts = timestamp(row["ts"])
        # Report exclusions; never silently borrow another house or edge row.
        if row["greenhouse_id"] != "vallery":
            excluded["other_greenhouse"] += 1
        elif not start <= ts < end:
            excluded["outside_window"] += 1
        else:
            by_exact[ts].append(row)
    by_minute = defaultdict(lambda: defaultdict(list))
    conflicts = defaultdict(set)
    for ts, rows in by_exact.items():
        minute = ts.replace(second=0, microsecond=0)
        for field in FIELDS:
            values = [row.get(field) for row in rows]
            # A finite/missing disagreement is also a conflict. Identical
            # finite duplicates collapse; absent data is not a measured zero.
            tokens = {(type(v).__name__, repr(v)) if not finite(v) else ("finite", float(v)) for v in values}
            if len(tokens) != 1:
                conflicts[minute].add(field)
            elif finite(values[0]):
                by_minute[minute][field].append(float(values[0]))
    minutes = {}
    for minute, fields in by_minute.items():
        minutes[minute] = {field: mean(values) for field, values in fields.items() if field not in conflicts[minute]}

    bins = []
    for index in range((end - start) // BIN):
        bucket = start + index * BIN
        slots = [bucket + offset * MINUTE for offset in range(15)]
        target = targets.get(bucket)
        row = {"bucket_start": bucket.isoformat(), "axes": {}, "joint": {}}
        joint_slots = []
        for axis in AXES:
            fields = [f"{axis}_{zone}" for zone in ZONES]
            eligible = [minute for minute in slots if all(field in minutes.get(minute, {}) for field in fields)]
            for minute in eligible:
                if all(field in minutes.get(minute, {}) for field in FIELDS):
                    joint_slots.append(minute)
            missing = {zone: sum(f"{axis}_{zone}" not in minutes.get(m, {}) for m in slots) for zone in ZONES}
            conflict_slots = sum(any(field in conflicts[m] for field in fields) for m in slots)
            reason = None
            if target is None:
                reason = "frozen_target_missing"
            elif not all(finite(target[f"{axis}_{bound}"]) for bound in ("low", "high")):
                reason = "frozen_target_nonfinite"
            elif target[f"{axis}_low"] > target[f"{axis}_high"]:
                reason = "frozen_target_inverted"
            elif len(eligible) < contract["minimum_minutes_per_bin"]:
                reason = "insufficient_complete_panel_minutes"
            result = {
                "unit": "degF" if axis == "temp" else "kPa",
                "complete_panel_minutes": len(eligible),
                "coverage_fraction": len(eligible) / 15,
                "missing_minutes_by_zone": missing,
                "conflicting_minutes": conflict_slots,
                "unavailable_reason": reason,
                "panel": None,
                "zones": None,
                "worst_zones": None,
                "worst_zone_distance": None,
                "mean_zone_distance": None,
            }
            if reason is None:
                # Exactly the SAME complete-panel slots contribute to all three
                # means. No zone can gain weight from extra polls or extra slots.
                zone_means = {zone: mean([minutes[m][f"{axis}_{zone}"] for m in eligible]) for zone in ZONES}
                low, high = target[f"{axis}_low"], target[f"{axis}_high"]
                zones = {zone: metrics(value, low, high) for zone, value in zone_means.items()}
                worst = max(value["outside_distance"] for value in zones.values())
                result.update(
                    panel=metrics(mean(list(zone_means.values())), low, high),
                    zones=zones,
                    worst_zones=[zone for zone in ZONES if zones[zone]["outside_distance"] == worst],
                    worst_zone_distance=worst,
                    mean_zone_distance=mean([value["outside_distance"] for value in zones.values()]),
                )
            row["axes"][axis] = result
        joint_slots = sorted(set(joint_slots))
        joint_reason = None
        if any(row["axes"][axis]["unavailable_reason"] for axis in AXES):
            joint_reason = "axis_unavailable"
        elif len(joint_slots) < contract["minimum_minutes_per_bin"]:
            joint_reason = "insufficient_joint_complete_panel_minutes"
        # Recompute BOTH axes on their intersection; separately eligible means
        # must not be combined into a purported jointly observed comparison.
        joint_panel = None
        if joint_reason is None:
            joint_panel = {}
            for axis in AXES:
                value = mean([mean([minutes[m][f"{axis}_{z}"] for z in ZONES]) for m in joint_slots])
                joint_panel[axis] = metrics(value, target[f"{axis}_low"], target[f"{axis}_high"])
        row["joint"] = {
            "complete_panel_minutes": len(joint_slots),
            "coverage_fraction": len(joint_slots) / 15,
            "unavailable_reason": joint_reason,
            "panel": joint_panel,
            "both_axes_in_band": None if joint_panel is None else all(x["in_band"] for x in joint_panel.values()),
        }
        bins.append(row)
    summary = {}
    for axis in AXES:
        available = [row["axes"][axis] for row in bins if row["axes"][axis]["unavailable_reason"] is None]
        summary[axis] = {
            "eligible_bins": len(available),
            "expected_bins": len(bins),
            "longest_unavailable_run_bins": longest_run(
                row["axes"][axis]["unavailable_reason"] is not None for row in bins
            ),
            "unavailable_reasons": dict(
                sorted(
                    Counter(
                        row["axes"][axis]["unavailable_reason"]
                        for row in bins
                        if row["axes"][axis]["unavailable_reason"]
                    ).items()
                )
            ),
            "panel_in_band_bin_pct": 100 * sum(r["panel"]["in_band"] for r in available) / len(available)
            if available
            else None,
            "mean_panel_outside_distance": mean([r["panel"]["outside_distance"] for r in available])
            if available
            else None,
            "mean_panel_low_distance": mean([r["panel"]["low_distance"] for r in available]) if available else None,
            "mean_panel_high_distance": mean([r["panel"]["high_distance"] for r in available]) if available else None,
            "mean_worst_zone_distance": mean([r["worst_zone_distance"] for r in available]) if available else None,
        }
    joint = [row["joint"] for row in bins if row["joint"]["unavailable_reason"] is None]
    summary["joint"] = {
        "expected_bins": len(bins),
        "eligible_bins": len(joint),
        "both_axes_in_band_bin_pct": 100 * sum(row["both_axes_in_band"] for row in joint) / len(joint)
        if joint
        else None,
        "longest_unavailable_run_bins": longest_run(row["joint"]["unavailable_reason"] is not None for row in bins),
    }
    return {
        "definition": "fixed-north-east-west-snapshot-bins-v1",
        "panel_version": contract["panel_version"],
        "target_version": contract["target_version"],
        "target_basis": contract["target_basis"],
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "minimum_minutes_per_bin": contract["minimum_minutes_per_bin"],
        "members": contract["members"],
        "target_evidence_sha256": contract["target_evidence_sha256"],
        "measurement_contract_sha256": digest(canonical(contract)),
        "input_canonical_sha256": digest(canonical(bundle)),
        "calculation_source_sha256": digest(Path(__file__).read_bytes()),
        "sample_basis": "database_flush_snapshot_not_per_probe_observation_time",
        "identity_evidence_status": "supplied_hashes_not_independently_authenticated",
        "physical_proof_eligible": False,
        "experiment_endpoint_eligible": False,
        "causal_effect_estimate": False,
        "center_measured": False,
        "excluded_rows": dict(sorted(excluded.items())),
        "input_rows": len(bundle["rows"]),
        "scoped_rows": sum(map(len, by_exact.values())),
        "summary": summary,
        "bins": bins,
    }


def export_sql(start_text, end_text):
    start, end = aligned(start_text), aligned(end_text)
    if not start < end <= start + timedelta(days=62):
        raise ValueError("positive export window of at most 62 days required")
    # Timestamp parsing/canonicalization precedes interpolation. No user-supplied
    # relation, column, role, file path or unparsed literal reaches SQL.
    return f"""BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '30s';
SET LOCAL lock_timeout = '2s';
SELECT jsonb_build_object('contract_version', 1,
    'sample_basis', 'database_flush_snapshot', 'greenhouse_id', 'vallery',
    'exported_at', statement_timestamp(),
    'window_start', '{start.isoformat()}', 'window_end', '{end.isoformat()}',
    'rows', (SELECT coalesce(jsonb_agg(to_jsonb(r) ORDER BY r.ts, to_jsonb(r)::text), '[]')
        FROM (SELECT ts, greenhouse_id, {", ".join(FIELDS)} FROM public.climate
              WHERE greenhouse_id = 'vallery' AND ts >= '{start.isoformat()}'::timestamptz
              AND ts < '{end.isoformat()}'::timestamptz) r));
COMMIT;
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("emit-sql", help="print read-only SQL; never connect")
    export.add_argument("--start", required=True)
    export.add_argument("--end", required=True)
    replay = commands.add_parser("analyze")
    replay.add_argument("--input", type=Path, required=True)
    replay.add_argument("--contract", type=Path, required=True)
    replay.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "emit-sql":
            print(export_sql(args.start, args.end), end="")
        else:
            raw, frozen = args.input.read_bytes(), args.contract.read_bytes()
            report = analyze(json.loads(raw), json.loads(frozen))
            report.update(input_file_sha256=digest(raw), contract_file_sha256=digest(frozen))
            # Exclusive creation preserves prior reports and either input path.
            with args.output.open("xb") as output:
                output.write(canonical(report) + b"\n")
    except (ValueError, TypeError, OverflowError, OSError):
        parser.exit(2, "fixed-panel input/output contract rejected; no scientific acceptance claimed\n")


if __name__ == "__main__":
    main()
