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
