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
import sys
from pathlib import Path
from typing import Any

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

# (entity minimum, entity maximum, entity step), source-locked from
# firmware/greenhouse/tunables.yaml.  Switches are exact booleans and omitted.
SOURCE_ENTITY_GRID: dict[str, tuple[float, float, float]] = {
    "band_track_fraction": (0, 1, 0.05),
    "cold_vent_guard_delta_f": (0, 15, 0.5),
    "cool_exit_hysteresis_f": (0.3, 3, 0.1),
    "cool_stage2_exit_hysteresis_f": (0.3, 3, 0.1),
    "cool_stage2_over_high_f": (0, 3, 0.1),
    "direct_wet_stress_min_dew_margin_f": (3, 15, 0.5),
    "direct_wet_stress_vpd_margin_kpa": (0, 0.5, 0.05),
    "dwell_gate_ms": (60_000, 1_800_000, 30_000),
    "enthalpy_close": (-5, 20, 0.5),
    "enthalpy_open": (-5, 0, 0.5),
    "fog_escalation_kpa": (0.1, 0.5, 0.1),
    "heat_hysteresis": (0, 3, 0.1),
    "min_fan_off_s": (30, 300, 10),
    "min_fan_on_s": (30, 300, 10),
    "min_fog_off_s": (15, 300, 15),
    "min_fog_on_s": (15, 300, 15),
    "min_heat_off_s": (60, 600, 10),
    "min_heat_on_s": (30, 300, 10),
    "min_vent_off_s": (10, 300, 10),
    "min_vent_on_s": (10, 300, 10),
    "mist_backoff_s": (60, 3_600, 60),
    "mist_max_closed_vent_s": (120, 900, 60),
    "mist_thermal_relief_s": (30, 300, 30),
    "mister_all_delay_s": (60, 600, 30),
    "mister_all_kpa": (1, 2.5, 0.05),
    "mister_center_penalty": (0, 1, 0.1),
    "mister_engage_delay_s": (30, 300, 30),
    "mister_engage_kpa": (0.5, 2.5, 0.05),
    "mister_min_off_s": (30, 120, 5),
    "mister_pulse_gap_s": (10, 60, 5),
    "mister_pulse_on_s": (30, 90, 5),
    "mister_vpd_weight": (0.5, 3, 0.5),
    "mister_water_budget_gal": (100, 300, 10),
    "night_vpd_bias_kpa": (0, 0.25, 0.01),
    "outdoor_staleness_max_s": (120, 1_800, 30),
    "temp_hysteresis": (0.5, 3, 0.1),
    "vent_exchange_fraction": (0.1, 0.6, 0.05),
    "vent_prefer_dp_delta_f": (2, 15, 0.5),
    "vent_prefer_temp_delta_f": (2, 15, 0.5),
    "vpd_hysteresis": (0.05, 0.5, 0.05),
    "vpd_watch_dwell_s": (15, 120, 15),
}

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


def _on_grid(name: str, value: float | bool) -> bool:
    if isinstance(value, bool):
        return name.startswith("sw_")
    minimum, maximum, step = SOURCE_ENTITY_GRID[name]
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


def validate_profiles(profiles: dict[str, dict[str, float | bool]]) -> None:
    fields = {definition.name for definition in wire_fields()}
    if len(fields) != 48:
        raise ValueError(f"protocol v2 requires 48 registry fields, got {len(fields)}")
    numeric_fields = {definition.name for definition in wire_fields() if definition.wire_kind != "bool"}
    if set(SOURCE_ENTITY_GRID) != numeric_fields:
        raise ValueError(
            "source entity-grid manifest does not exactly cover numeric wire fields: "
            f"missing={sorted(numeric_fields - set(SOURCE_ENTITY_GRID))} extra={sorted(set(SOURCE_ENTITY_GRID) - numeric_fields)}"
        )
    for profile_id, values in profiles.items():
        if set(values) != fields:
            raise ValueError(f"{profile_id} is not an exact 48-field profile")
        off_grid = sorted(name for name, value in values.items() if not _on_grid(name, value))
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
    validate_profiles(profiles)
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
            for name, (minimum, maximum, step) in sorted(SOURCE_ENTITY_GRID.items())
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
