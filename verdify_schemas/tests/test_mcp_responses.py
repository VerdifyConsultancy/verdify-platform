"""MCP read-tool response schema tests."""

from __future__ import annotations

import os
import subprocess
from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from verdify_schemas.mcp_responses import (
    OUTCOME_COMPOSITE_METRICS,
    ClimateSnapshot,
    EquipmentStateRow,
    ForecastSummaryRow,
    HistoryRow,
    LessonSummary,
    OutcomeKpiActionRow,
    OutcomeKpiCoverage,
    OutcomeKpiResponse,
    PlanRunResponse,
    PlanStatusJournal,
    PlanStatusResponse,
    PlanStatusWaypoint,
    ScorecardResponse,
    SetpointSummary,
    ToolError,
)
from verdify_schemas.telemetry import DliEvidence


class TestClimateSnapshot:
    def test_valid(self):
        s = ClimateSnapshot(temp_f=72.5, vpd_kpa=0.8, mode="VENTILATE", age_seconds=12)
        assert s.mode == "VENTILATE"

    def test_minimal(self):
        s = ClimateSnapshot()
        assert s.mode is None


class TestScorecard:
    FULL_METRICS = {
        "planner_score": Decimal("72.5"),
        "planner_score_resource_weight_pct": Decimal("20"),
        "resource_terms_available": Decimal("1"),
        "compliance_pct": Decimal("88.0"),
        "temp_compliance_pct": Decimal("92.0"),
        "vpd_compliance_pct": Decimal("85.0"),
        "total_stress_h": Decimal("2.8"),
        "heat_stress_h": Decimal("0.0"),
        "cold_stress_h": Decimal("1.2"),
        "vpd_high_stress_h": Decimal("1.6"),
        "vpd_low_stress_h": Decimal("0.0"),
        "kwh": Decimal("22.3"),
        "therms": Decimal("3.1"),
        "water_gal": Decimal("280"),
        "mister_water_gal": Decimal("195"),
        "cost_electric": Decimal("2.48"),
        "cost_gas": Decimal("2.57"),
        "cost_water": Decimal("1.36"),
        "cost_total": Decimal("6.41"),
        "dp_margin_min_f": Decimal("7.2"),
        "dp_risk_hours": Decimal("0.0"),
        "7d_avg_score": Decimal("68.3"),
        "7d_avg_compliance": Decimal("81.2"),
        "7d_avg_cost": Decimal("5.84"),
        "7d_avg_kwh": Decimal("21.0"),
        "7d_avg_therms": Decimal("2.9"),
        "7d_avg_water_gal": Decimal("265"),
    }

    def test_full_day_roundtrip(self):
        """All canonical metrics round-trip with resource-score scope intact."""
        rows = [{"metric": m, "value": v} for m, v in self.FULL_METRICS.items()]
        s = ScorecardResponse.from_metric_rows(rows)
        assert s.planner_score == 72.5
        assert s.cost_total == 6.41
        assert s.planner_score_resource_weight_pct == 20
        assert s.resource_terms_available == 1
        # Alias field read via Python identifier
        assert s.avg_score_7d == 68.3
        # Wire format preserves the `7d_*` keys
        dumped = s.model_dump(by_alias=True)
        assert dumped["7d_avg_score"] == 68.3
        assert "avg_score_7d" not in dumped

    def test_partial_day_missing_fields_are_none(self):
        """Incomplete days emit fewer rows — missing metrics map to None, not error."""
        rows = [("planner_score", Decimal("60")), ("compliance_pct", Decimal("75"))]
        s = ScorecardResponse.from_metric_rows(rows)
        assert s.planner_score == 60.0
        assert s.cost_total is None
        assert s.avg_score_7d is None

    def test_sentinel_strings_become_none(self):
        """'n/a' and 'perfect' are DB-side sentinels for 'no data' — schema normalizes to None."""
        rows = [
            ("planner_score", None),
            ("dp_margin_min_f", "n/a"),
            ("cost_electric", "perfect"),
        ]
        s = ScorecardResponse.from_metric_rows(rows)
        assert s.planner_score is None
        assert s.dp_margin_min_f is None
        assert s.cost_electric is None

    def test_unknown_metric_rejected(self):
        """DB function growing a new metric must surface at the boundary."""
        with pytest.raises(ValidationError):
            ScorecardResponse.from_metric_rows([("totally_new_kpi", 42)])

    def test_metric_names_exposes_all_dialect_metrics(self):
        """Schema is a superset of both DB dialects:
        - The resource-aware function emits two explicit score-scope metrics.
        - Migration-076/077-era emitted 27 metrics —
          the deployed function dropped `7d_avg_stress` + `7d_avg_dp_risk`.
        Schema covers both until G15 resyncs migrations with live.

        Plus the 9 graded + feasibility metrics modelled schema-first for the
        migration-146/147 compliance rearchitecture (band-compliance §6-§7):
        compliance_v2_raw/attributable/unachievable_frac, graded temp/vpd, and
        4 graded_*_stress_h — accepted BEFORE fn_planner_scorecard emits them so
        the extra='forbid' contract cannot 500 the public /api/v1/scorecard.

        Plus the 6 ADR-0004 composite outcome-score metrics (schema-first,
        #388): outcome_score_composite + its component sub-scores — accepted
        BEFORE the #371 DB function emits them, same 500-proofing pattern."""
        names = ScorecardResponse.metric_names()
        assert len(names) == 27 + 9 + 2 + 6 + 1
        assert "scorecard_contract_version" in names
        assert "metric_semantics" not in names  # metadata is not a numeric SQL metric
        assert "planner_score" in names
        assert "7d_avg_score" in names
        assert "7d_avg_stress" in names  # CI-only until G15
        assert "7d_avg_dp_risk" in names  # CI-only until G15
        assert "planner_score_resource_weight_pct" in names
        assert "resource_terms_available" in names
        # Graded compliance metrics (schema-first, migration 146/147).
        assert "compliance_v2_attributable_pct" in names
        assert "graded_vpd_high_stress_h" in names
        # ADR-0004 composite outcome score (schema-first, #388).
        assert OUTCOME_COMPOSITE_METRICS <= names


class TestOutcomeCompositeContract:
    """#388 — ADR-0004 composite outcome score, schema-first wire contract.

    OUTCOME_COMPOSITE_METRICS is the single pinned name set that the
    ScorecardResponse model (== MCP scorecard() wire keys, the emitter is a
    pass-through), the #371 DB function / daily_summary columns, and the #365
    planner reader all bind to. These guards run with NO DB so a rename fails
    everywhere, immediately."""

    def test_model_fields_exactly_match_pinned_contract(self):
        """Every outcome_* field on the model must be in the pinned set and
        vice versa — a rename on either side fails loud instead of silently
        NULLing the planner reward (#365)."""
        model_outcome_fields = {n for n in ScorecardResponse.model_fields if n.startswith("outcome_")}
        assert model_outcome_fields == set(OUTCOME_COMPOSITE_METRICS), (
            f"ScorecardResponse outcome_* fields {sorted(model_outcome_fields)} != "
            f"pinned OUTCOME_COMPOSITE_METRICS {sorted(OUTCOME_COMPOSITE_METRICS)}. "
            f"Update BOTH together (and #371's DB fn / #365's reader)."
        )

    def test_no_wire_alias_on_outcome_fields(self):
        """The MCP emitter dumps by_alias — outcome fields must have NO alias
        so the Python identifier, the wire key, and the DB metric/column name
        are the identical string."""
        for name in OUTCOME_COMPOSITE_METRICS:
            field = ScorecardResponse.model_fields[name]
            assert field.alias is None, f"{name} must not carry a wire alias"

    def test_outcome_fields_optional_and_default_none(self):
        """Transitional safety (migration-147-header pattern): all outcome
        fields are Optional and default to None, so days before the #371 DB
        fn populates them serve present-but-null instead of 500."""
        s = ScorecardResponse()
        for name in OUTCOME_COMPOSITE_METRICS:
            assert getattr(s, name) is None

    def test_outcome_metric_rows_roundtrip(self):
        """Once #371's fn emits the composite rows, they parse as floats and
        survive the by_alias wire dump under their pinned names."""
        rows = [
            ("outcome_score_composite", Decimal("61.4")),
            ("outcome_time_in_band", Decimal("87.5")),
            ("outcome_dli_grade", Decimal("72.0")),
            ("outcome_dif_grade", Decimal("90.0")),
            ("outcome_wet_dry_completion", Decimal("100")),
            ("outcome_cost_cycling_penalty", Decimal("12.3")),
        ]
        s = ScorecardResponse.from_metric_rows(rows)
        assert s.outcome_score_composite == 61.4
        assert s.outcome_cost_cycling_penalty == 12.3
        dumped = s.model_dump(by_alias=True)
        for name, value in rows:
            assert dumped[name] == float(value)

    def test_outcome_null_rows_accepted(self):
        """Present-but-null rows (fn emits the metric with NULL value on a
        partial day) must not raise — the /api/v1/scorecard 500-proofing the
        issue's acceptance calls out."""
        rows = [(name, None) for name in sorted(OUTCOME_COMPOSITE_METRICS)]
        s = ScorecardResponse.from_metric_rows(rows)
        assert s.outcome_score_composite is None
        assert s.outcome_time_in_band is None


class TestOutcomeKpi:
    def test_roundtrip_with_pending_metric_coverage(self):
        r = OutcomeKpiResponse(
            date=date(2026, 6, 23),
            greenhouse_id="vallery",
            semantics="ADR-0004 outcome view",
            coverage=OutcomeKpiCoverage(moisture_estimator="available", vpd_policy_sequences="available"),
            served_corridor={"attributable_pct": 91.2, "raw_pct": 84.0},
            pinched_corridor={"both_pct": None},
            vpd_misses_h={"high_stress_h": 2.4, "low_stress_h": 0.0},
            actuator_cycles={"fan1": 14, "fog": 3},
            actuator_runtime={"fan1_min": 185.5, "fog_min": 8.0},
            water_use_gal={"total": 12.2, "mister": 3.4},
            dli=DliEvidence(
                value_mol_m2_day=None,
                availability="unavailable",
                unavailable_reason="interior_light_sensor_broken",
                provenance="legacy_invalid_exterior_proxy_plus_fixture_estimate",
                validity_revision="dli-validity-v1",
                valid_from="2024-01-01T00:00:00Z",
                valid_to=None,
            ),
            dif={"day_night_temp_delta_f": None},
            dew_margin={"min_f": 6.2, "risk_h": 0.0},
            energy_cost={"total_usd": 4.85},
            action_scorecard=[
                OutcomeKpiActionRow(
                    climate_action="VENT_DRY",
                    decisions=7,
                    avg_abs_temp_error_before_f=1.2,
                    avg_abs_vpd_error_before_kpa=0.13,
                    avg_temp_abs_error_delta_15m_f=-0.4,
                    avg_vpd_abs_error_delta_15m_kpa=-0.02,
                    avg_wet_relay_duty_pct=0.0,
                    avg_vent_fan_duty_pct=88.0,
                    mister_water_delta_gal=0.0,
                    wet_blocked_decisions=2,
                    fog_blocked_decisions=1,
                )
            ],
            moisture_estimator={
                "sample_count": 7,
                "coverage": "available",
                "by_action_reason": [
                    {
                        "action": "vent_dehum",
                        "reason": "vent_plus_heat_hold",
                        "decisions": 7,
                        "avg_vent_vpd_gain_kpa": 0.12,
                        "avg_heat_vpd_gain_kpa": 0.2,
                        # #327/#410: held-temp co-run fields + selected gain
                        # + outdoor age (NULL-tolerant for pre-#410 emitters).
                        "avg_vent_held_vpd_gain_kpa": 0.16,
                        "avg_expected_vpd_gain_kpa": 0.16,
                        "hold_required_decisions": 7,
                        "outdoor_fresh_decisions": 7,
                        "avg_outdoor_age_s": None,
                        "vent_overcool_decisions": 0,
                        "heat_assist_corun_decisions": 7,
                        "heat_assist_active_decisions": 3,
                        "avg_heat_assist_timer_s": 180.0,
                    }
                ],
            },
            vpd_policy={
                "total_episodes": 42,
                "wetting_episodes": 6,
                "vent_dehum_episodes": 4,
                "heat_dehum_episodes": 2,
                "wet_to_dehum_episodes_30m": 3,
                "dehum_to_wet_episodes_30m": 1,
                "transition_window_min": 30,
                # #327: episode counters by estimator reason; pre-#385 rows
                # bucket as estimator_absent.
                "episodes_by_mx_reason": [
                    {
                        "mx_reason": "vent_plus_heat_hold",
                        "episodes": 4,
                        "samples": 120,
                        "vent_dehum_episodes": 4,
                        "heat_dehum_episodes": 0,
                        "wetting_episodes": 0,
                    },
                    {
                        "mx_reason": "estimator_absent",
                        "episodes": 38,
                        "samples": 900,
                        "vent_dehum_episodes": 0,
                        "heat_dehum_episodes": 2,
                        "wetting_episodes": 6,
                    },
                ],
            },
            source_tables=["daily_summary", "v_climate_action_daily_scorecard", "climate_action_log"],
        )

        dumped = r.model_dump()
        assert dumped["coverage"]["served_corridor"] == "available"
        assert dumped["coverage"]["pinched_corridor"] == "available"
        assert dumped["coverage"]["dif"] == "available"
        assert dumped["coverage"]["solar_phase_buckets"] == "available"
        assert dumped["coverage"]["moisture_estimator"] == "available"
        assert dumped["coverage"]["vpd_policy_sequences"] == "available"
        assert dumped["coverage"]["dli"] == "unavailable"
        assert dumped["dli"]["value_mol_m2_day"] is None
        assert dumped["dli"]["unavailable_reason"] == "interior_light_sensor_broken"
        assert dumped["served_corridor"]["attributable_pct"] == 91.2
        assert dumped["action_scorecard"][0]["decisions"] == 7
        assert dumped["moisture_estimator"]["sample_count"] == 7
        assert dumped["moisture_estimator"]["by_action_reason"][0]["action"] == "vent_dehum"
        assert dumped["moisture_estimator"]["by_action_reason"][0]["hold_required_decisions"] == 7
        assert dumped["moisture_estimator"]["by_action_reason"][0]["avg_vent_held_vpd_gain_kpa"] == 0.16
        assert dumped["vpd_policy"]["wet_to_dehum_episodes_30m"] == 3
        assert dumped["vpd_policy"]["episodes_by_mx_reason"][0]["mx_reason"] == "vent_plus_heat_hold"
        assert dumped["vpd_policy"]["episodes_by_mx_reason"][1]["mx_reason"] == "estimator_absent"
        assert '"date":"2026-06-23"' in r.model_dump_json()

    def test_action_row_rejects_extra_fields(self):
        with pytest.raises(ValidationError):
            OutcomeKpiActionRow.model_validate(
                {
                    "climate_action": "VENT_DRY",
                    "decisions": 1,
                    "unknown_metric": 42,
                }
            )


# ── Drift guard: live fn_planner_scorecard() metric names must be a subset of
#    ScorecardResponse.metric_names(). Skips if no DB is reachable. ─────
def _docker_timescaledb_reachable() -> bool:
    import shutil

    if not shutil.which("docker"):
        return False
    r = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", "verdify-timescaledb"],
        capture_output=True,
        text=True,
        check=False,
    )
    return r.returncode == 0 and r.stdout.strip() == "true"


def _ci_postgres_reachable() -> bool:
    return bool(os.environ.get("POSTGRES_HOST"))


@pytest.mark.skipif(
    not (_ci_postgres_reachable() or _docker_timescaledb_reachable()),
    reason="no DB backend available",
)
def test_scorecard_metric_names_match_live_function():
    """Every metric fn_planner_scorecard() emits must be modelled in
    ScorecardResponse. A new DB-side metric without a schema field is
    the exact drift case this test is here to catch."""
    sql = "SELECT DISTINCT metric FROM fn_planner_scorecard()"
    if _ci_postgres_reachable():
        env = os.environ.copy()
        env.setdefault("PGHOST", env.get("POSTGRES_HOST", "localhost"))
        env.setdefault("PGPORT", env.get("POSTGRES_PORT", "5432"))
        env.setdefault("PGUSER", env.get("POSTGRES_USER", "verdify"))
        env.setdefault("PGPASSWORD", env.get("POSTGRES_PASSWORD", "verdify"))
        env.setdefault("PGDATABASE", env.get("POSTGRES_DB", "verdify"))
        r = subprocess.run(["psql", "-t", "-A", "-c", sql], capture_output=True, text=True, timeout=15, env=env)
        if r.returncode != 0:
            if 'relation "v_daily_kpi" does not exist' in r.stderr:
                pytest.skip("CI Postgres bootstrap did not create v_daily_kpi")
            r.check_returncode()
    else:
        r = subprocess.run(
            ["docker", "exec", "verdify-timescaledb", "psql", "-U", "verdify", "-d", "verdify", "-t", "-A", "-c", sql],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
    live = {ln.strip() for ln in r.stdout.splitlines() if ln.strip()}
    if not live:
        pytest.skip("fn_planner_scorecard() returned 0 rows for today (not enough data)")
    modeled = ScorecardResponse.metric_names()
    unmodeled = sorted(live - modeled)
    assert not unmodeled, (
        f"fn_planner_scorecard() emits metric(s) ScorecardResponse doesn't model: {unmodeled}. "
        f"Add field(s) to verdify_schemas/mcp_responses.py:ScorecardResponse."
    )


class TestEquipmentStateRow:
    def test_valid(self):
        r = EquipmentStateRow(equipment="fan1", state=True, since="14:32:01")
        assert r.state is True

    def test_rejects_missing_state(self):
        with pytest.raises(ValidationError):
            EquipmentStateRow(equipment="fan1", since="14:32:01")


class TestForecastSummaryRow:
    def test_valid(self):
        f = ForecastSummaryRow(time="Sun 14:00", temp=82, rh=12, vpd=2.3, cloud=10, solar=900)
        assert f.solar == 900


class TestHistoryRow:
    def test_open_extra(self):
        h = HistoryRow(time="2026-04-19T01:00:00+00:00", temp_f=72, rh_pct=55)
        assert h.model_dump().get("temp_f") == 72


class TestSetpointSummary:
    def test_valid(self):
        s = SetpointSummary(parameter="temp_low", value=58.0, source="plan", updated="06:18")
        assert s.source == "plan"


class TestPlanStatus:
    def test_with_plan(self):
        j = PlanStatusJournal(
            plan_id="iris-20260418-0618",
            created="04-18 06:18",
            hypothesis="x",
        )
        wp = PlanStatusWaypoint(time="Sun 14:00", params=10)
        r = PlanStatusResponse(plan=j, future_waypoints=[wp])
        assert r.plan.plan_id.startswith("iris-")
        assert r.future_waypoints[0].params == 10

    def test_no_active_plan(self):
        r = PlanStatusResponse()
        assert r.plan is None
        assert r.future_waypoints == []

    def test_rejects_negative_params(self):
        with pytest.raises(ValidationError):
            PlanStatusWaypoint(time="x", params=-1)


class TestLessonSummary:
    def test_valid(self):
        ls = LessonSummary(
            category="misting",
            condition="dry day <20% RH",
            lesson="engage 1.3, gap 30s",
            confidence="medium",
            times_validated=4,
        )
        assert ls.confidence == "medium"

    def test_rejects_bad_confidence(self):
        with pytest.raises(ValidationError):
            LessonSummary(
                category="x",
                condition="x",
                lesson="x",
                confidence="maybe",
                times_validated=1,
            )


class TestPlanRunResponse:
    def test_ok(self):
        r = PlanRunResponse(ok=True, note="sent")
        assert r.ok is True

    def test_audit_fields(self):
        r = PlanRunResponse(
            ok=True,
            trigger_id="00000000-0000-0000-0000-000000000001",
            event_type="MANUAL",
            planner_instance="local",
            session_key="agent:iris-planner:main",
            status="pending",
        )
        assert r.event_type == "MANUAL"
        assert r.planner_instance == "local"

    def test_error(self):
        r = PlanRunResponse(ok=False, error="Hermes unreachable")
        assert r.error.startswith("Hermes")


class TestToolError:
    def test_with_details(self):
        e = ToolError(error="validation failed", details={"field": "x"})
        assert e.details["field"] == "x"
