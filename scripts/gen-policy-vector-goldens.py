#!/usr/bin/env python3
"""Generate verdify_schemas/tests/fixtures/policy_vector_goldens.json.

Lane A of #582 (audit §8.9). The golden fixture was originally produced by a
one-off at wire schema v1; this script is the committed regeneration path so a
wire-schema change (contract v2 retired wire_id 6, #588) rebuilds the JSON
deterministically from the tunable registry:

- ``registry_defaults``  — every wire field at its registry default;
- ``wire_min_bounds``    — every wire field at its wire-envelope minimum;
- ``wire_max_bounds``    — every wire field at its wire-envelope maximum;
- ``mixed_realistic``    — the frozen realistic posture below (values on the
  wire grid; carried over from the v1 fixture minus retired fields).

The activation context, treatment octets, and hash chain are computed through
``verdify_schemas.policy_vector`` — the same code the tests verify — so the
fixture is a cross-language anchor: ``firmware/test/test_policy_vector.cpp``
consumes it via ``firmware/test/policy_vector_goldens_generated.inc``
(scripts/gen-policy-vector-header.py). Run this script FIRST after a registry
wire change, then gen-policy-vector-header.py.

Run with ``--check`` to verify the committed fixture matches regeneration.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from verdify_schemas import policy_vector as pv  # noqa: E402
from verdify_schemas.tunable_registry import WIRE_SCHEMA_VERSION  # noqa: E402

FIXTURE_PATH = REPO_ROOT / "verdify_schemas" / "tests" / "fixtures" / "policy_vector_goldens.json"

# Policy revision ids bound into every golden content hash. The Lane G
# baseline builder (research/planner-efficacy/baseline/baseline.py
# POLICY_REVISION_IDS) pins the identical mapping; a cross-check test in
# research/planner-efficacy/tests/test_baseline.py enforces it.
REVISION_IDS = {
    "registry_rev": "wire-v2-retire-wire-id-6",
    "schema_rev": "efa85343",
}

# Frozen synthetic exposure context shared with the C++ golden test.
ACTIVATION_CONTEXT = {
    "experiment_id": "11111111-2222-4333-8444-555555555555",
    "assignment_id": "66666666-7777-4888-9999-aaaaaaaaaaaa",
    "generation": 7,
    "valid_from_us": 1788325200000000,
    "valid_to_us": 1790917200000000,
}

# §8.9 treatment octets exercised by the goldens (hex).
TREATMENTS = {
    "randomized_x": "0158",
    "qualification_analyzed": "0201aaaaaaaabbbb4ccc8dddeeeeeeeeeeee123456789abc4def8123456789abcdef02",
    "aa_lane0": "0300",
}

# Frozen realistic posture (wire schema v2): the v1 fixture's mixed_realistic
# values minus the retired direct_wet_stress_latest_hour row. All values sit
# on the wire grid — the round-trip test asserts quantize == identity.
MIXED_REALISTIC: dict[str, float | bool] = {
    "band_track_fraction": 0.25,
    "cold_vent_guard_delta_f": 10.0,
    "cool_exit_hysteresis_f": 1.2,
    "cool_stage2_exit_hysteresis_f": 1.0,
    "cool_stage2_over_high_f": 0.8,
    "direct_wet_stress_min_dew_margin_f": 8.0,
    "direct_wet_stress_vpd_margin_kpa": 0.05,
    "dwell_gate_ms": 420000.0,
    "enthalpy_close": 2.5,
    "enthalpy_open": -3.5,
    "fog_escalation_kpa": 0.4,
    "heat_hysteresis": 1.0,
    "min_fan_off_s": 90.0,
    "min_fan_on_s": 120.0,
    "min_fog_off_s": 90.0,
    "min_fog_on_s": 60.0,
    "min_heat_off_s": 180.0,
    "min_heat_on_s": 120.0,
    "min_vent_off_s": 60.0,
    "min_vent_on_s": 60.0,
    "mist_backoff_s": 600.0,
    "mist_max_closed_vent_s": 600.0,
    "mist_thermal_relief_s": 90.0,
    "mister_all_delay_s": 300.0,
    "mister_all_kpa": 1.75,
    "mister_center_penalty": 0.5,
    "mister_engage_delay_s": 45.0,
    "mister_engage_kpa": 1.45,
    "mister_min_off_s": 45.0,
    "mister_pulse_gap_s": 30.0,
    "mister_pulse_on_s": 45.0,
    "mister_vpd_weight": 1.5,
    "mister_water_budget_gal": 220.5,
    "night_vpd_bias_kpa": 0.07,
    "outdoor_staleness_max_s": 600.0,
    "sw_cool_all_fans_at_high_enabled": False,
    "sw_direct_wet_gate_enabled": True,
    "sw_direct_wet_stress_override_enabled": False,
    "sw_dwell_gate_enabled": True,
    "sw_fog_closes_vent": True,
    "sw_mister_closes_vent": True,
    "sw_summer_vent_enabled": True,
    "temp_hysteresis": 1.2,
    "vent_exchange_fraction": 0.3,
    "vent_prefer_dp_delta_f": 5.0,
    "vent_prefer_temp_delta_f": 5.0,
    "vpd_hysteresis": 0.25,
    "vpd_watch_dwell_s": 60.0,
}


def _vector_values() -> dict[str, dict[str, float | bool]]:
    defaults: dict[str, float | bool] = {}
    mins: dict[str, float | bool] = {}
    maxs: dict[str, float | bool] = {}
    for defn in pv.wire_fields():
        if defn.wire_kind == "bool":
            defaults[defn.name] = bool(defn.default)
            mins[defn.name] = False
            maxs[defn.name] = True
        else:
            defaults[defn.name] = float(defn.default)
            lo, hi = pv.wire_value_bounds(defn.name)
            mins[defn.name] = float(lo)
            maxs[defn.name] = float(hi)
    return {
        "registry_defaults": defaults,
        "wire_min_bounds": mins,
        "wire_max_bounds": maxs,
        "mixed_realistic": dict(MIXED_REALISTIC),
    }


def build_fixture() -> dict:
    vectors = []
    for name, values in _vector_values().items():
        canonical = pv.quantize_policy_values(values)
        if canonical != values:
            raise SystemExit(f"golden vector {name!r} is not on the wire grid: fix the frozen values")
        blob = pv.encode_policy_vector(canonical)
        content = pv.content_sha256(blob, schema_version=WIRE_SCHEMA_VERSION, policy_revision_ids=REVISION_IDS)
        activation = {
            t_name: pv.activation_sha256(
                content,
                experiment_id=ACTIVATION_CONTEXT["experiment_id"],
                assignment_id=ACTIVATION_CONTEXT["assignment_id"],
                treatment_bytes=bytes.fromhex(t_hex),
                generation=ACTIVATION_CONTEXT["generation"],
                valid_from_us=ACTIVATION_CONTEXT["valid_from_us"],
                valid_to_us=ACTIVATION_CONTEXT["valid_to_us"],
            ).hex()
            for t_name, t_hex in TREATMENTS.items()
        }
        vectors.append(
            {
                "name": name,
                "values": {defn.name: canonical[defn.name] for defn in pv.wire_fields()},
                "raws_by_wire_id": pv._raws_from_values(canonical),
                "vector_hex": blob.hex(),
                "content_sha256": content.hex(),
                "activation_sha256": activation,
            }
        )

    return {
        "_comment": (
            "Golden cross-language fixtures for the canonical policy-vector codec "
            "(Lane A, #582; audit 8.9). Generated by scripts/gen-policy-vector-goldens.py "
            "— DO NOT EDIT BY HAND. Consumed by verdify_schemas/tests/test_policy_vector.py "
            "and (via the generated firmware/test/policy_vector_goldens_generated.inc) by "
            "firmware/test/test_policy_vector.cpp. Values are canonical (on-grid); "
            "raws_by_wire_id is ordered by ascending wire_id."
        ),
        "wire_schema_version": WIRE_SCHEMA_VERSION,
        "vector_size": pv.POLICY_VECTOR_SIZE,
        "wire_manifest_digest_sha256": pv.wire_manifest_digest().hex(),
        "wire_manifest": pv.wire_manifest(),
        "revision_ids": REVISION_IDS,
        "revision_ids_canonical_json": pv.canonical_revision_ids_bytes(REVISION_IDS).decode(),
        "activation_context": ACTIVATION_CONTEXT,
        "treatments": TREATMENTS,
        "vectors": vectors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify the committed fixture matches regeneration")
    args = parser.parse_args()

    content = json.dumps(build_fixture(), indent=2, sort_keys=False) + "\n"
    if args.check:
        current = FIXTURE_PATH.read_text() if FIXTURE_PATH.exists() else None
        if current != content:
            print("DRIFT: regenerate with python3 scripts/gen-policy-vector-goldens.py")
            return 1
        print("policy-vector goldens fixture is up to date")
        return 0
    FIXTURE_PATH.write_text(content)
    print(f"wrote {FIXTURE_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
