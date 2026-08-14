from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]


def load(name: str) -> dict:
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def test_current_firmware_core_result_is_bounded_and_rejected() -> None:
    result = load("results-current-firmware-core-2026-08-14.json")
    assert result["study"]["firmware_version"] == "2026.7.10.1500.09ee886"
    assert result["study"]["evaluation_complete_days"] == 14
    assert result["inputs"]["climate"]["rows"] == 3264
    assert result["plans"]["plans"] == 84
    assert result["factual_strong_window"]["days"] == 34
    assert result["counterfactual_eligible"] is False
    replay = result["open_loop_decision_replay"]
    low_pct = 100 * (
        replay["candidate_min_requested_minutes_per_day"] / replay["executed_climate_actuator_minutes_per_day"] - 1
    )
    high_pct = 100 * (
        replay["candidate_max_requested_minutes_per_day"] / replay["executed_climate_actuator_minutes_per_day"] - 1
    )
    assert low_pct == pytest.approx(63.9677, abs=1e-4)
    assert high_pct == pytest.approx(137.5946, abs=1e-4)


def test_current_firmware_supplement_keeps_observational_guards() -> None:
    result = load("results-current-firmware-supplement-2026-08-14.json")
    assert result["schema_version"] == 2
    stale = result["stale_policy_interruption"]
    assert stale["retained_pairs"] == 93
    assert stale["unique_control_bins"] == 85
    assert stale["maximum_control_reuse"] == 2
    assert "hypothesis-generating" in stale["causal_status"]
    assert stale["contrasts"]["vpd_distance_outside_corridor_kpa"]["stale_minus_matched_fresh"] == pytest.approx(
        0.3079007962
    )
    assert stale["contrasts"]["six_core_device_minutes_per_bin"]["matched_fresh_pct_below_stale"] == pytest.approx(
        26.4304867564
    )
    waypoint = result["waypoint_survival_and_semantics"]["due_before_outcome_cutoff"]
    assert waypoint["genuinely_future_waypoints"] == 613
    assert waypoint["future_superseded_before_scheduled"] == 457
    assert waypoint["future_superseded_pct"] == pytest.approx(74.5513866232)
    experiment = result["proposed_experiment_screening"]
    assert experiment["historical_pair_origins"] == {"epoch_start": 17, "one_day_shift": 16}
    assert experiment["historical_climate_bins_below_12_samples"] == 1
    assert experiment["historical_climate_bins_ineligible"] == 1
    assert experiment["noncentral_t_lambda_80pct_power"] == pytest.approx(3.013271700466293)
    assert experiment["resource_evidence"]["water_attribution_eligible_days"] == 25
    assert experiment["resource_evidence"]["whole_runtime_energy_eligible_days"] == 0
    assert experiment["resource_evidence"][
        "expected_water_meter_eligible_days_per_15_day_arm_if_arm_independent"
    ] == pytest.approx(15 * 27 / 34)
    assert experiment["endpoints"]["mean_vpd_distance_outside_corridor_kpa"][
        "distance_from_decision_boundary_for_80pct_power"
    ] == pytest.approx(0.0949092598)
    assert (
        experiment["endpoints"]["mean_vpd_distance_outside_corridor_kpa"]["selected_conservative_pair_origin"]
        == "one_day_shift"
    )
    assert experiment["endpoints"]["six_core_device_minutes"][
        "distance_from_decision_boundary_for_80pct_power"
    ] == pytest.approx(724.731183)
    assert experiment["endpoints"]["nine_climate_device_minutes"][
        "distance_from_decision_boundary_for_80pct_power"
    ] == pytest.approx(827.034820)
    assert experiment["endpoints"]["nine_climate_device_minutes"]["optimistic_stable_pair_sensitivity"][
        "distance_from_decision_boundary_for_80pct_power"
    ] == pytest.approx(455.977191)
    gates = experiment["gate_operating_characteristics"]
    assert gates["vpd_noninferiority"]["marginal_power_if_true_ai_minus_frozen_is_zero"] == pytest.approx(0.3153698082)
    assert gates["vpd_noninferiority"]["largest_true_ai_minus_frozen_for_80pct_power"] == pytest.approx(-0.0449092598)
    assert gates["nine_device_superiority"]["marginal_power_at_stale_fresh_analogue"] == pytest.approx(0.5292523012)
    assert gates["illustrative_joint_advance_power_upper_bound"]["all_operational_day_scale"] == pytest.approx(
        0.3153698082
    )
