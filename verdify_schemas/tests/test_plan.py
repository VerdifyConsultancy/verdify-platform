"""Plan + PlanTransition schema tests — the MCP set_plan contract."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from verdify_schemas.plan import (
    Conditions,
    ParamRationale,
    Plan,
    PlanEvaluation,
    PlanHypothesisStructured,
    PlanTransition,
    StressWindow,
)

NOW = datetime(2026, 4, 18, 12, 0, 0, tzinfo=UTC)


def _t(offset_min: int = 0) -> datetime:
    return NOW + timedelta(minutes=offset_min)


class TestPlanTransition:
    def test_valid_transition(self):
        t = PlanTransition(ts=_t(), params={"temp_low": 55.0, "temp_high": 80.0})
        assert t.params["temp_low"] == 55.0

    def test_switch_value_accepted(self):
        t = PlanTransition(ts=_t(), params={"sw_economiser_enabled": 1.0})
        assert t.params["sw_economiser_enabled"] == 1.0

    def test_switch_rejects_non_binary(self):
        with pytest.raises(ValidationError, match="must be 0.0 or 1.0"):
            PlanTransition(ts=_t(), params={"sw_economiser_enabled": 0.5})

    def test_rejects_unknown_param(self):
        with pytest.raises(ValidationError, match="Unknown tunable"):
            PlanTransition(ts=_t(), params={"total_nonsense": 1.0})

    def test_rejects_inverted_temp_band(self):
        with pytest.raises(ValidationError, match="temp_low.*must be < temp_high"):
            PlanTransition(ts=_t(), params={"temp_low": 80.0, "temp_high": 55.0})

    def test_rejects_inverted_vpd_band(self):
        with pytest.raises(ValidationError, match="vpd_low.*must be < vpd_high"):
            PlanTransition(ts=_t(), params={"vpd_low": 2.0, "vpd_high": 0.5})

    def test_rejects_inverted_safety_band(self):
        with pytest.raises(ValidationError, match="safety_min.*must be < safety_max"):
            PlanTransition(ts=_t(), params={"safety_min": 100.0, "safety_max": 40.0})

    def test_rejects_mister_engage_above_all(self):
        with pytest.raises(ValidationError, match="mister_engage_kpa.*must be <= mister_all_kpa"):
            PlanTransition(ts=_t(), params={"mister_engage_kpa": 2.0, "mister_all_kpa": 1.0})

    def test_rejects_registry_value_above_max(self):
        with pytest.raises(ValidationError, match="vpd_hysteresis=0.55 outside registry bounds"):
            PlanTransition(ts=_t(), params={"vpd_hysteresis": 0.55})

    def test_rejects_registry_value_below_min(self):
        with pytest.raises(ValidationError, match="mister_all_delay_s=10 outside registry bounds"):
            PlanTransition(ts=_t(), params={"mister_all_delay_s": 10})

    def test_registry_error_includes_nearest_safe_value(self):
        with pytest.raises(ValidationError, match="nearest_safe=0"):
            PlanTransition(ts=_t(), params={"enthalpy_open": 2.0})

    def test_requires_timezone_aware_ts(self):
        with pytest.raises(ValidationError):
            PlanTransition(ts=datetime(2026, 4, 18, 12), params={"temp_low": 55.0})

    def test_accepts_climate_intent_without_low_level_params(self):
        t = PlanTransition(
            ts=_t(),
            climate_intent={
                "temp_target_f": 72.0,
                "temp_band_f": 6.0,
                "vpd_target_kpa": 1.0,
                "vpd_band_kpa": 0.5,
                "forecast_temp_bias_f": 1.0,
                "forecast_vpd_bias_kpa": 0.1,
                "solar_precool_gain_f": 1.0,
                "thermal_lead_time_min": 30.0,
                "economizer_temp_advantage_f": 4.0,
                "economizer_dewpoint_advantage_f": 3.0,
                "moisture_engage_vpd_excess_kpa": 0.05,
                "mist_duty_limit_pct": 25.0,
                "fog_escalate_vpd_excess_kpa": 0.25,
                "dew_margin_floor_f": 8.0,
                "wet_cutoff_hour": 19.0,
                "daily_mist_budget_gal": 120.0,
                "resource_sensitivity": 0.4,
                "relay_churn_penalty": 0.6,
            },
        )

        assert t.params == {}
        assert t.climate_intent is not None
        assert t.climate_intent.temp_band() == (69.0, 75.0)

    def test_rejects_empty_params(self):
        with pytest.raises(ValidationError):
            PlanTransition(ts=_t(), params={})

    def test_rejects_extra_fields(self):
        with pytest.raises(ValidationError, match="extra"):
            PlanTransition(ts=_t(), params={"temp_low": 55.0}, typo_field=True)


class TestPlan:
    def _valid_plan_kwargs(self):
        return {
            "plan_id": "iris-20260418-1200",
            "hypothesis": "Test plan for schema validation",
            "transitions": [
                PlanTransition(ts=_t(0), params={"temp_low": 55.0}),
                PlanTransition(ts=_t(60), params={"temp_low": 58.0}),
            ],
        }

    def test_valid_plan(self):
        p = Plan(**self._valid_plan_kwargs())
        assert p.plan_id == "iris-20260418-1200"
        assert len(p.transitions) == 2

    def test_rejects_bad_plan_id(self):
        with pytest.raises(ValidationError, match="String should match pattern"):
            kw = self._valid_plan_kwargs()
            kw["plan_id"] = "manual-thing"
            Plan(**kw)

    def test_rejects_empty_transitions(self):
        with pytest.raises(ValidationError):
            kw = self._valid_plan_kwargs()
            kw["transitions"] = []
            Plan(**kw)

    def test_rejects_non_monotonic_transitions(self):
        with pytest.raises(ValidationError, match="strictly ts-ascending"):
            kw = self._valid_plan_kwargs()
            kw["transitions"] = [
                PlanTransition(ts=_t(60), params={"temp_low": 55.0}),
                PlanTransition(ts=_t(0), params={"temp_low": 58.0}),
            ]
            Plan(**kw)

    def test_rejects_duplicate_ts(self):
        with pytest.raises(ValidationError, match="strictly ts-ascending"):
            kw = self._valid_plan_kwargs()
            kw["transitions"] = [
                PlanTransition(ts=_t(0), params={"temp_low": 55.0}),
                PlanTransition(ts=_t(0), params={"vpd_low": 0.8}),
            ]
            Plan(**kw)

    def test_round_trip_json(self):
        p = Plan(**self._valid_plan_kwargs())
        serialized = p.model_dump_json()
        restored = Plan.model_validate_json(serialized)
        assert restored == p


class TestPlanHypothesisStructured:
    def _valid_conditions(self):
        return Conditions(
            outdoor_temp_peak_f=80.0,
            outdoor_rh_min_pct=10.0,
            solar_peak_w_m2=900.0,
            cloud_cover_avg_pct=20.0,
        )

    def _valid_rationale(self):
        return [
            ParamRationale(
                parameter="mister_engage_kpa",
                old_value=1.5,
                new_value=1.3,
                forecast_anchor="Sunday 3PM 4% RH outdoor",
                expected_effect="trigger misting earlier to keep VPD < 2.0",
            ),
        ]

    def test_valid_structured(self):
        s = PlanHypothesisStructured(
            conditions=self._valid_conditions(),
            stress_windows=[
                StressWindow(
                    kind="vpd_high",
                    start=_t(180),
                    end=_t(300),
                    severity="high",
                    mitigation="Pulse mister on 15s/gap 15s",
                ),
            ],
            rationale=self._valid_rationale(),
        )
        assert s.conditions.outdoor_temp_peak_f == 80.0
        assert len(s.stress_windows) == 1

    def test_rejects_empty_rationale(self):
        with pytest.raises(ValidationError):
            PlanHypothesisStructured(conditions=self._valid_conditions(), rationale=[])

    def test_stress_window_rejects_inverted_range(self):
        with pytest.raises(ValidationError, match="end.*must be after start"):
            StressWindow(
                kind="heat",
                start=_t(300),
                end=_t(180),
                severity="medium",
                mitigation="irrelevant",
            )

    def test_rationale_unknown_param_rejected(self):
        with pytest.raises(ValidationError):
            ParamRationale(
                parameter="mystery_param",
                new_value=1.0,
                forecast_anchor="a",
                expected_effect="b",
            )


class TestPlanEvaluation:
    def test_valid(self):
        ev = PlanEvaluation(
            plan_id="iris-20260418-1200",
            outcome_score=7,
            actual_outcome="held VPD within band 92% of daylight hours",
        )
        assert ev.outcome_score == 7

    def test_rejects_score_out_of_range(self):
        with pytest.raises(ValidationError):
            PlanEvaluation(
                plan_id="iris-20260418-1200",
                outcome_score=11,
                actual_outcome="x",
            )

    def test_rejects_score_zero(self):
        with pytest.raises(ValidationError):
            PlanEvaluation(
                plan_id="iris-20260418-1200",
                outcome_score=0,
                actual_outcome="x",
            )

    def test_rejects_empty_actual_outcome(self):
        with pytest.raises(ValidationError):
            PlanEvaluation(plan_id="iris-20260418-1200", outcome_score=5, actual_outcome="")
