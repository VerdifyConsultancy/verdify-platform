"""Build and validate the complete 48-field protocol-v2 profile artifact.

Wire quantization is intentionally insufficient: every value must also be an
exact point on the source-locked ESPHome entity grid.  The historical baseline
and template candidates are retained unchanged; this additive builder records
each explicit design choice needed to create v2 candidates and never rounds at
runtime.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

import yaml

RESEARCH_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = RESEARCH_ROOT.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from verdify_schemas.policy_vector import encode_policy_vector, wire_fields, wire_manifest_digest
from verdify_schemas.tunable_registry import WIRE_SCHEMA_VERSION

TREATMENT_ALLOWLIST = frozenset(
    {
        "cool_stage2_over_high_f",
        "sw_cool_all_fans_at_high_enabled",
        "fog_escalation_kpa",
        "min_fog_on_s",
        "min_fog_off_s",
        "mister_engage_kpa",
        "mister_all_kpa",
        "mister_all_delay_s",
        "mister_pulse_gap_s",
        "mister_pulse_on_s",
        "mister_water_budget_gal",
    }
)


def _esphome_object_id(name: str) -> str:
    """Mirror ESPHome's ASCII object-id spelling for the checked source names."""
    return "".join(char if char.isascii() and char.isalnum() else "_" for char in name.lower())


def source_entity_grid(repo_root: Path = REPO_ROOT) -> dict[str, tuple[float, float, float]]:
    """Parse every numeric wire field's exact min/max/step from firmware source."""
    path = repo_root / "firmware/greenhouse/tunables.yaml"
    document = yaml.safe_load(path.read_text())
    rows = document.get("number") if isinstance(document, dict) else None
    if not isinstance(rows, list):
        raise TypeError("firmware tunables source must contain a number list")
    by_object_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            raise TypeError("every firmware number entity must have a string name")
        object_id = _esphome_object_id(row["name"])
        if object_id in by_object_id:
            raise ValueError(f"duplicate firmware number object id {object_id!r}")
        by_object_id[object_id] = row

    grid: dict[str, tuple[float, float, float]] = {}
    for definition in wire_fields():
        if definition.wire_kind == "bool":
            continue
        object_id = definition.esp_object_id
        if not object_id or object_id not in by_object_id:
            raise ValueError(f"numeric wire field {definition.name!r} has no matching firmware number entity")
        row = by_object_id[object_id]
        values = tuple(row.get(key) for key in ("min_value", "max_value", "step"))
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
            raise ValueError(f"firmware grid for {definition.name!r} is not fully numeric")
        minimum, maximum, step = values
        if not all(math.isfinite(float(value)) for value in values) or minimum > maximum or step <= 0:
            raise ValueError(f"firmware grid for {definition.name!r} is invalid")
        if float(minimum) != definition.fw_clamp_lo or float(maximum) != definition.fw_clamp_hi:
            raise ValueError(f"registry clamp for {definition.name!r} differs from firmware entity source")
        grid[definition.name] = (minimum, maximum, step)
    return grid


# Compatibility export, but derived from the source file rather than duplicated.
SOURCE_ENTITY_GRID = source_entity_grid()

# Explicit source/design decisions, applied before validation.  They are not a
# generic normalizer and no unlisted off-grid value can pass.
COMMON_GRID_DECISIONS: dict[str, tuple[float, str]] = {
    "dwell_gate_ms": (
        240_000,
        "225000 ms lies halfway between entity points; select the longer 240000 ms anti-chatter dwell explicitly",
    ),
    "min_fog_on_s": (60, "historical 59 s cannot land on the 15 s entity grid; select the adjacent 60 s point"),
    "mister_all_delay_s": (90, "historical 80 s cannot land on the 30 s entity grid; select nearest 90 s"),
    "mister_engage_delay_s": (30, "historical 40 s cannot land on the 30 s entity grid; select nearest 30 s"),
    "vpd_watch_dwell_s": (60, "historical 56 s cannot land on the 15 s entity grid; select nearest 60 s"),
}
PROFILE_GRID_DECISIONS: dict[str, dict[str, tuple[float, str]]] = {
    "moderate": {
        "mister_pulse_gap_s": (
            40,
            "historical candidate 38 s cannot land on the 5 s entity grid; select nearest 40 s",
        )
    }
}


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _on_grid(
    name: str,
    value: float | bool,
    source_grid: dict[str, tuple[float, float, float]] = SOURCE_ENTITY_GRID,
) -> bool:
    if isinstance(value, bool):
        return name.startswith("sw_")
    minimum, maximum, step = source_grid[name]
    if not minimum <= float(value) <= maximum:
        return False
    position = (float(value) - minimum) / step
    return abs(position - round(position)) <= 1e-9


def _state_content_sha256(values: dict[str, float | bool]) -> str:
    digest = hashlib.sha256()
    digest.update(b"verdify-policy-state-content-v1")
    digest.update(b"\x00")
    digest.update(bytes([WIRE_SCHEMA_VERSION]))
    digest.update(wire_manifest_digest())
    digest.update(encode_policy_vector(values))
    return digest.hexdigest()


def validate_profiles(
    profiles: dict[str, dict[str, float | bool]],
    source_grid: dict[str, tuple[float, float, float]] = SOURCE_ENTITY_GRID,
) -> None:
    fields = {definition.name for definition in wire_fields()}
    if len(fields) != 48:
        raise ValueError(f"protocol v2 requires 48 registry fields, got {len(fields)}")
    numeric_fields = {definition.name for definition in wire_fields() if definition.wire_kind != "bool"}
    if set(source_grid) != numeric_fields:
        raise ValueError(
            "source entity-grid manifest does not exactly cover numeric wire fields: "
            f"missing={sorted(numeric_fields - set(source_grid))} extra={sorted(set(source_grid) - numeric_fields)}"
        )
    for profile_id, values in profiles.items():
        if set(values) != fields:
            raise ValueError(f"{profile_id} is not an exact 48-field profile")
        off_grid = sorted(name for name, value in values.items() if not _on_grid(name, value, source_grid))
        if off_grid:
            raise ValueError(f"{profile_id} contains off-entity-grid values: {off_grid}")
        encode_policy_vector(values)  # strict bounds/types/wire representability
    baseline = profiles["baseline"]
    for profile_id in ("moderate", "aggressive"):
        differences = {name for name in fields if profiles[profile_id][name] != baseline[name]}
        if not differences <= TREATMENT_ALLOWLIST:
            raise ValueError(f"{profile_id} changes non-treatment fields: {sorted(differences - TREATMENT_ALLOWLIST)}")


def build_profile_artifact(repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    baseline_path = repo_root / "research/planner-efficacy/baseline/frozen-fsm-baseline-candidate-2026-08-14.json"
    candidates_path = repo_root / "research/planner-efficacy/baseline/ai-template-candidates-2026-08-14.json"
    firmware_grid_path = repo_root / "firmware/greenhouse/tunables.yaml"
    source_grid = source_entity_grid(repo_root)
    baseline_source = json.loads(baseline_path.read_text())
    candidates_source = json.loads(candidates_path.read_text())
    original_baseline = {name: row["quantized_value"] for name, row in baseline_source["fields"].items()}
    baseline = dict(original_baseline)
    decisions: list[dict[str, Any]] = []
    for name, (selected, rationale) in COMMON_GRID_DECISIONS.items():
        previous = baseline[name]
        baseline[name] = selected
        decisions.append(
            {"field": name, "from": previous, "profile_scope": "all", "rationale": rationale, "to": selected}
        )
    profiles: dict[str, dict[str, float | bool]] = {"baseline": baseline}
    for profile_id in ("moderate", "aggressive"):
        values = dict(baseline)
        for name, row in candidates_source["templates"][profile_id]["fields"].items():
            values[name] = row["value"]
        for name, (selected, rationale) in PROFILE_GRID_DECISIONS.get(profile_id, {}).items():
            previous = values[name]
            values[name] = selected
            decisions.append(
                {"field": name, "from": previous, "profile_scope": profile_id, "rationale": rationale, "to": selected}
            )
        profiles[profile_id] = values
    validate_profiles(profiles, source_grid)
    artifact_profiles = {}
    for profile_id, values in profiles.items():
        wire = encode_policy_vector(values)
        artifact_profiles[profile_id] = {
            "field_count": len(values),
            "policy_state_content_sha256": _state_content_sha256(values),
            "values": dict(sorted(values.items())),
            "wire_bytes": len(wire),
            "wire_hex": wire.hex(),
        }
    return {
        "schema": "verdify-switchback-v2-profile-set",
        "version": 1,
        "status": "SOURCE-GRID CANDIDATE — running-device grid and multidisciplinary physical approval still required",
        "profiles": artifact_profiles,
        "treatment_allowlist": sorted(TREATMENT_ALLOWLIST),
        "explicit_grid_design_decisions": decisions,
        "source_entity_grid": {
            name: {"max": maximum, "min": minimum, "step": step}
            for name, (minimum, maximum, step) in sorted(source_grid.items())
        },
        "source_evidence": {
            "historical_baseline_path": str(baseline_path.relative_to(repo_root)),
            "historical_baseline_sha256": _sha256_file(baseline_path),
            "historical_template_path": str(candidates_path.relative_to(repo_root)),
            "historical_template_sha256": _sha256_file(candidates_path),
            "source_entity_grid_path": str(firmware_grid_path.relative_to(repo_root)),
            "source_entity_grid_sha256": _sha256_file(firmware_grid_path),
            "audited_deployed_firmware_revision": "2026.7.10.1500.09ee886",
            "source_vs_audited_firmware_grid_comparison": (
                "all 48 candidate values were checked against the source entity grid associated with the audited "
                "firmware path; six off-grid historical inputs were replaced only by the explicit decisions above"
            ),
            "running_device_entity_grid_verified": False,
            "claim_limit": (
                "source/deployed-firmware-code comparison only, not a raw running-entity read; issue 641 physical "
                "verification is still required"
            ),
        },
        "wire_schema": {
            "field_count": 48,
            "manifest_digest_sha256": wire_manifest_digest().hex(),
            "version": WIRE_SCHEMA_VERSION,
        },
        "runtime_rounding_allowed": False,
    }


if __name__ == "__main__":
    print(json.dumps(build_profile_artifact(), indent=2, sort_keys=True))
