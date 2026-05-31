"""Tests for the local Verdify contract mirror.

This file checks the planner-side copy of the Verdify-specific `set_plan`
contract details such as Tier 1 extraction and plan-id shaping. It connects
contract hardening to automated proof that the local mirror behaves correctly.
"""

from __future__ import annotations

import re

import pytest

from planner_graph.verdify_contract import (
    CLIMATE_INTENT_DEFAULTS,
    CLIMATE_INTENT_FIELD_NAMES,
    TIER1_PLAN_DEFAULTS,
    TIER1_PLAN_PARAM_NAMES,
    build_climate_intent,
    extract_tier1_plan_params,
    planner_plan_id,
)


def test_planner_plan_id_uses_triggered_local_timestamp() -> None:
    assert (
        planner_plan_id("2026-05-19T06:00:00-06:00", "trigger-1")
        == "iris-20260519-0600"
    )


def test_planner_plan_id_fallback_is_deterministic_and_matches_contract() -> None:
    first = planner_plan_id("not-a-date", "d107f399-64c2-4d26-a0eb-d0cf0e7fff61")
    second = planner_plan_id("not-a-date", "d107f399-64c2-4d26-a0eb-d0cf0e7fff61")
    assert first == second
    assert re.fullmatch(r"iris-\d{8}-\d{4}", first)


def test_tier1_extraction_filters_context_extras() -> None:
    extraction = extract_tier1_plan_params(
        {
            **TIER1_PLAN_DEFAULTS,
            "future_waypoints": 3,
        }
    )

    assert extraction.complete is True
    assert extraction.coverage_status == "complete"
    assert tuple(extraction.params) == TIER1_PLAN_PARAM_NAMES
    assert extraction.ignored == ("future_waypoints",)


def test_tier1_extraction_reports_missing_and_non_numeric_params() -> None:
    active_plan_summary: dict[str, object] = dict(TIER1_PLAN_DEFAULTS)
    active_plan_summary.pop("cool_stage2_over_high_f")
    active_plan_summary["cool_exit_hysteresis_f"] = "warm"

    extraction = extract_tier1_plan_params(active_plan_summary)

    assert extraction.complete is False
    assert extraction.missing == ("cool_stage2_over_high_f",)
    assert extraction.non_numeric == ("cool_exit_hysteresis_f",)
    assert extraction.coverage_status == "missing:1;non_numeric:1"


def test_build_climate_intent_uses_bounded_single_path_surface() -> None:
    active_plan_summary = {
        **TIER1_PLAN_DEFAULTS,
        "temp_low": 68.0,
        "temp_high": 76.0,
        "vpd_low": 0.7,
        "vpd_high": 1.3,
        "future_waypoints": 3,
    }

    intent = build_climate_intent(active_plan_summary)

    assert tuple(intent) == CLIMATE_INTENT_FIELD_NAMES
    assert intent["temp_target_f"] == 72.0
    assert intent["temp_band_f"] == 8.0
    assert intent["vpd_target_kpa"] == 1.0
    assert intent["vpd_band_kpa"] == pytest.approx(0.6)
    assert (
        intent["moisture_engage_vpd_excess_kpa"]
        == TIER1_PLAN_DEFAULTS["direct_wet_stress_vpd_margin_kpa"]
    )
    assert (
        intent["fog_escalate_vpd_excess_kpa"]
        == TIER1_PLAN_DEFAULTS["fog_escalation_kpa"]
    )
    assert (
        intent["dew_margin_floor_f"]
        == TIER1_PLAN_DEFAULTS["fog_stress_min_dew_margin_f"]
    )
    assert (
        intent["wet_cutoff_hour"]
        == TIER1_PLAN_DEFAULTS["fog_stress_window_latest_hour"]
    )


def test_build_climate_intent_defaults_when_tier1_context_is_missing() -> None:
    intent = build_climate_intent({"future_waypoints": 3})

    assert tuple(intent) == CLIMATE_INTENT_FIELD_NAMES
    assert intent["temp_target_f"] == CLIMATE_INTENT_DEFAULTS["temp_target_f"]
    assert intent["vpd_target_kpa"] == CLIMATE_INTENT_DEFAULTS["vpd_target_kpa"]
