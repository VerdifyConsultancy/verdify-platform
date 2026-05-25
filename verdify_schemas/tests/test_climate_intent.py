"""Drift guards for the ClimateIntent controller contract."""

from __future__ import annotations

import pathlib
import re

import pytest
from pydantic import ValidationError

from verdify_schemas.climate_intent import (
    CLIMATE_ACTIONS,
    CLIMATE_INTENT_FIELD_DOCS,
    CLIMATE_INTENT_FIELDS,
    CLIMATE_PRIORITY_ORDER,
    CLIMATE_RELAY_FIELD_DENYLIST,
    ClimateActionDecision,
    ClimateCandidateProjection,
    ClimateIntent,
    choose_climate_candidate,
    materialize_climate_intent_tier1,
)
from verdify_schemas.tunable_registry import TIER1_REG, registry_value_error

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
DESIGN_DOC = REPO_ROOT / "docs" / "firmware-climate-intent-controller-final-design-2026-05-24.md"


def _section(text: str, start: str, end: str) -> str:
    return text.split(start, 1)[1].split(end, 1)[0]


def _table_codes(section: str) -> tuple[str, ...]:
    values: list[str] = []
    for line in section.splitlines():
        if not line.startswith("| `"):
            continue
        match = re.match(r"\| `([^`]+)`", line)
        if match:
            values.append(match.group(1))
    return tuple(values)


def _valid_intent(**overrides: float) -> ClimateIntent:
    data = {
        "forecast_temp_bias_f": -1.0,
        "forecast_vpd_bias_kpa": 0.05,
        "solar_precool_gain_f": 1.5,
        "thermal_lead_time_min": 45.0,
        "economizer_temp_advantage_f": 4.0,
        "economizer_dewpoint_advantage_f": 3.0,
        "moisture_engage_vpd_excess_kpa": 0.05,
        "mist_duty_limit_pct": 20.0,
        "fog_escalate_vpd_excess_kpa": 0.25,
        "dew_margin_floor_f": 8.0,
        "wet_cutoff_hour": 19.0,
        "daily_mist_budget_gal": 120.0,
        "resource_sensitivity": 0.4,
        "relay_churn_penalty": 0.6,
    }
    data.update(overrides)
    return ClimateIntent(**data)


def test_design_doc_is_canonical_and_schema_matches_intent_surface() -> None:
    text = DESIGN_DOC.read_text()
    intent_section = _section(text, "## ClimateIntent Surface", "## Context Inputs For AI")

    assert _table_codes(intent_section) == CLIMATE_INTENT_FIELDS
    assert set(ClimateIntent.model_fields) == set(CLIMATE_INTENT_FIELDS)
    assert tuple(doc.name for doc in CLIMATE_INTENT_FIELD_DOCS) == CLIMATE_INTENT_FIELDS
    assert all(doc.firmware_impact for doc in CLIMATE_INTENT_FIELD_DOCS)
    assert all(doc.planner_guidance for doc in CLIMATE_INTENT_FIELD_DOCS)
    assert any(doc.materialized_knobs for doc in CLIMATE_INTENT_FIELD_DOCS)


def test_design_doc_action_table_matches_schema_actions() -> None:
    text = DESIGN_DOC.read_text()
    action_section = _section(text, "## Physical Action Set", "## Candidate Evaluation")

    assert _table_codes(action_section) == CLIMATE_ACTIONS


def test_firmware_climate_action_names_match_schema_actions() -> None:
    header = (REPO_ROOT / "firmware" / "lib" / "greenhouse_types.h").read_text()
    action_names = header.split("inline constexpr const char* CLIMATE_ACTION_NAMES[] = {", 1)[1].split("};", 1)[0]

    assert tuple(re.findall(r'"([^"]+)"', action_names)) == CLIMATE_ACTIONS
    assert "choose_climate_candidate_index" in header


def test_climate_intent_excludes_raw_relay_commands() -> None:
    assert not (set(CLIMATE_INTENT_FIELDS) & CLIMATE_RELAY_FIELD_DENYLIST)

    with pytest.raises(ValidationError):
        _valid_intent(fog=1.0)  # type: ignore[call-arg]


def test_climate_intent_ranges_are_bounded() -> None:
    _valid_intent()

    with pytest.raises(ValidationError):
        _valid_intent(temp_target_f=72.0)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        _valid_intent(forecast_vpd_bias_kpa=0.9)
    with pytest.raises(ValidationError):
        _valid_intent(resource_sensitivity=2.0)


def test_climate_intent_materializes_to_complete_bounded_tier1_params() -> None:
    intent = _valid_intent(
        forecast_temp_bias_f=3.0,
        forecast_vpd_bias_kpa=0.25,
        solar_precool_gain_f=3.0,
        mist_duty_limit_pct=80.0,
        resource_sensitivity=0.2,
        relay_churn_penalty=0.25,
    )

    params = materialize_climate_intent_tier1(
        intent,
        {"fog_escalation_kpa": 0.2, "temp_low": 64.0, "temp_high": 68.0, "vpd_low": 0.55, "vpd_high": 0.8},
    )

    assert set(params) == set(TIER1_REG)
    assert params["sw_dwell_gate_enabled"] == 1.0
    assert params["sw_summer_vent_enabled"] == 1.0
    assert params["vent_prefer_temp_delta_f"] == intent.economizer_temp_advantage_f
    assert params["direct_wet_stress_min_dew_margin_f"] == intent.dew_margin_floor_f
    assert params["fog_stress_window_latest_hour"] == intent.wet_cutoff_hour
    assert params["mister_water_budget_gal"] == intent.daily_mist_budget_gal
    assert params["fog_escalation_kpa"] == intent.fog_escalate_vpd_excess_kpa
    assert params["mister_engage_kpa"] == pytest.approx(0.85)
    assert all(registry_value_error(name, value) is None for name, value in params.items())


def test_materializer_forces_wet_assist_when_live_vpd_is_above_band_and_dew_is_safe() -> None:
    intent = _valid_intent(
        forecast_vpd_bias_kpa=0.0,
        mist_duty_limit_pct=0.0,
        fog_escalate_vpd_excess_kpa=0.5,
        wet_cutoff_hour=17.0,
        daily_mist_budget_gal=0.0,
        resource_sensitivity=1.0,
        relay_churn_penalty=1.0,
    )

    params = materialize_climate_intent_tier1(
        intent,
        {
            "temp_low": 72.0,
            "temp_high": 78.0,
            "vpd_low": 0.8,
            "vpd_high": 1.2,
            "temp_actual_f": 82.0,
            "vpd_actual_kpa": 1.55,
            "dew_margin_f": 12.0,
        },
    )

    assert params["sw_direct_wet_stress_override_enabled"] == 1.0
    assert params["sw_fog_stress_window_extend_enabled"] == 1.0
    assert params["direct_wet_stress_vpd_margin_kpa"] == pytest.approx(0.05)
    assert params["fog_escalation_kpa"] == pytest.approx(0.2)
    assert params["direct_wet_stress_latest_hour"] >= 19.0
    assert params["mister_water_budget_gal"] >= 120.0


def test_materializer_does_not_force_wet_assist_when_dew_margin_is_unsafe() -> None:
    intent = _valid_intent(
        forecast_vpd_bias_kpa=0.0,
        mist_duty_limit_pct=0.0,
        wet_cutoff_hour=17.0,
        daily_mist_budget_gal=0.0,
        resource_sensitivity=1.0,
    )

    params = materialize_climate_intent_tier1(
        intent,
        {
            "temp_high": 78.0,
            "vpd_low": 0.8,
            "vpd_high": 1.2,
            "temp_actual_f": 82.0,
            "vpd_actual_kpa": 1.55,
            "dew_margin_f": 4.0,
        },
    )

    assert params["sw_direct_wet_stress_override_enabled"] == 0.0
    assert params["sw_fog_stress_window_extend_enabled"] == 0.0


def test_candidate_selection_is_lexicographic_not_weighted_sum() -> None:
    assert CLIMATE_PRIORITY_ORDER == ("safety", "temp", "vpd", "resource")

    cheap_vpd_better = ClimateCandidateProjection(
        action="SEALED_HUMIDIFY",
        safety_ok=True,
        projected_temp_error_f=2.0,
        projected_vpd_error_kpa=0.0,
        resource_cost=0.0,
        relay_churn_cost=0.0,
        confidence=0.8,
    )
    temp_better_expensive = ClimateCandidateProjection(
        action="VENT_COOL_MIST_ASSIST",
        safety_ok=True,
        projected_temp_error_f=1.0,
        projected_vpd_error_kpa=0.2,
        resource_cost=10.0,
        relay_churn_cost=1.0,
        confidence=0.8,
    )
    unsafe_best_projection = ClimateCandidateProjection(
        action="VENT_COOL",
        safety_ok=False,
        blocked_reasons=("occupancy",),
        projected_temp_error_f=0.0,
        projected_vpd_error_kpa=0.0,
        resource_cost=0.0,
        relay_churn_cost=0.0,
        confidence=0.8,
    )

    assert choose_climate_candidate([cheap_vpd_better, temp_better_expensive, unsafe_best_projection]).action == (
        "VENT_COOL_MIST_ASSIST"
    )


def test_action_decision_validates_observability_block_reasons() -> None:
    decision = ClimateActionDecision(
        climate_action="VENT_COOL_MIST_ASSIST",
        priority_axis="temp",
        temp_error_f=1.0,
        vpd_error_kpa=0.05,
        candidate_summary="VENT_COOL_MIST_ASSIST selected; FOG below threshold",
        moisture_assist_state="pulse_gap",
        moisture_zone="center",
        next_mist_eligible_s=12.0,
        fog_margin_kpa=-0.02,
        fog_block_reason="below_threshold,time_window",
    )

    assert decision.fog_block_reason == "below_threshold,time_window"

    with pytest.raises(ValidationError):
        ClimateActionDecision(
            climate_action="VENT_COOL_MIST_ASSIST",
            priority_axis="temp",
            temp_error_f=1.0,
            vpd_error_kpa=0.05,
            candidate_summary="bad block reason",
            moisture_assist_state="blocked",
            fog_block_reason="none,time_window",
        )
