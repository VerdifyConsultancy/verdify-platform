"""Phase 3 — full-row schemas for plan/setpoint tables."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from verdify_schemas.plan import PlanHypothesisStructured, PlanJournalRow
from verdify_schemas.setpoint import (
    SetpointChange,
    SetpointClamp,
    SetpointPlanRow,
    SetpointSnapshot,
)

NOW = datetime(2026, 4, 19, 1, 0, tzinfo=UTC)


class TestSetpointPlanRow:
    def test_valid(self):
        row = SetpointPlanRow(
            ts=NOW,
            parameter="temp_low",
            value=58.0,
            plan_id="iris-20260418-0618",
        )
        assert row.is_active is True
        assert row.source == "iris"

    def test_rejects_unknown_parameter(self):
        with pytest.raises(ValidationError):
            SetpointPlanRow(
                ts=NOW,
                parameter="bogus_knob",
                value=1.0,
                plan_id="iris-20260418-0618",
            )

    def test_rejects_empty_plan_id(self):
        with pytest.raises(ValidationError):
            SetpointPlanRow(
                ts=NOW,
                parameter="temp_low",
                value=58.0,
                plan_id="",
            )

    def test_rejects_value_outside_registry_bounds(self):
        with pytest.raises(ValidationError, match="vpd_hysteresis=0.55 outside registry bounds"):
            SetpointPlanRow(
                ts=NOW,
                parameter="vpd_hysteresis",
                value=0.55,
                plan_id="iris-20260418-0618",
            )

    def test_rejects_non_binary_switch_value(self):
        with pytest.raises(ValidationError, match="sw_economiser_enabled=0.5 outside registry switch values"):
            SetpointPlanRow(
                ts=NOW,
                parameter="sw_economiser_enabled",
                value=0.5,
                plan_id="iris-20260418-0618",
            )


class TestSetpointSnapshot:
    def test_valid(self):
        s = SetpointSnapshot(ts=NOW, parameter="temp_low", value=58.0)
        assert s.greenhouse_id == "vallery"

    def test_rejects_bad_parameter(self):
        with pytest.raises(ValidationError):
            SetpointSnapshot(ts=NOW, parameter="not_real", value=1.0)


class TestSetpointClamp:
    def test_band_clamp(self):
        c = SetpointClamp(
            parameter="temp_low",
            requested=52.0,
            applied=55.0,
            band_lo=55.0,
            band_hi=65.0,
            reason="band_lo",
        )
        assert c.reason == "band_lo"

    def test_invariant_violation(self):
        c = SetpointClamp(
            parameter="mister_water_budget_gal",
            requested=10000.0,
            applied=500.0,
            reason="invariant_violation",
        )
        assert c.band_lo is None

    def test_rejects_empty_reason(self):
        with pytest.raises(ValidationError):
            SetpointClamp(
                parameter="temp_low",
                requested=52.0,
                applied=55.0,
                reason="",
            )


class TestSetpointChangeExtended:
    def test_confirmed(self):
        sc = SetpointChange(
            ts=NOW,
            parameter="bias_heat",
            value=2.0,
            source="plan",
            confirmed_at=NOW,
        )
        assert sc.confirmed_at == NOW

    def test_source_includes_iris(self):
        # Sprint 21: 'iris' is now a valid source (was stripped in Sprint 20)
        sc = SetpointChange(ts=NOW, parameter="temp_low", value=58.0, source="iris")
        assert sc.source == "iris"

    def test_rejects_value_outside_registry_bounds(self):
        with pytest.raises(ValidationError, match="mister_all_delay_s=10 outside registry bounds"):
            SetpointChange(
                ts=NOW,
                parameter="mister_all_delay_s",
                value=10,
                source="plan",
            )

    def test_rejects_non_binary_switch_value(self):
        with pytest.raises(ValidationError, match="sw_economiser_enabled=0.5 outside registry switch values"):
            SetpointChange(
                ts=NOW,
                parameter="sw_economiser_enabled",
                value=0.5,
                source="plan",
            )


class TestPlanJournalRow:
    def test_minimal(self):
        row = PlanJournalRow(plan_id="iris-20260418-0618")
        assert row.outcome_score is None

    def test_with_structured_hypothesis(self):
        structured = PlanHypothesisStructured(
            conditions={
                "outdoor_temp_peak_f": 80.0,
                "outdoor_rh_min_pct": 10.0,
                "solar_peak_w_m2": 900.0,
                "cloud_cover_avg_pct": 20.0,
            },
            rationale=[
                {
                    "parameter": "mister_engage_kpa",
                    "new_value": 1.3,
                    "forecast_anchor": "x",
                    "expected_effect": "y",
                },
            ],
        )
        row = PlanJournalRow(
            plan_id="iris-20260418-0618",
            hypothesis="prose version",
            hypothesis_structured=structured,
            outcome_score=7,
        )
        assert row.outcome_score == 7
        assert row.hypothesis_structured.conditions.outdoor_temp_peak_f == 80.0

    def test_rejects_bad_plan_id(self):
        with pytest.raises(ValidationError):
            PlanJournalRow(plan_id="manual-thing")

    def test_rejects_outcome_score_out_of_range(self):
        with pytest.raises(ValidationError):
            PlanJournalRow(plan_id="iris-20260418-0618", outcome_score=11)
