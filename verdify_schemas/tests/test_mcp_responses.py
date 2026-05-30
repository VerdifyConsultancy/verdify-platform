"""MCP read-tool response schema tests."""

from __future__ import annotations

import os
import subprocess
from decimal import Decimal

import pytest
from pydantic import ValidationError

from verdify_schemas.mcp_responses import (
    ClimateSnapshot,
    EquipmentStateRow,
    ForecastSummaryRow,
    HistoryRow,
    LessonSummary,
    PlanRunResponse,
    PlanStatusJournal,
    PlanStatusResponse,
    PlanStatusWaypoint,
    ScorecardResponse,
    SetpointSummary,
    ToolError,
)


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
        """All 25 metrics present — Decimal inputs coerce to float; by-alias wire format preserved."""
        rows = [{"metric": m, "value": v} for m, v in self.FULL_METRICS.items()]
        s = ScorecardResponse.from_metric_rows(rows)
        assert s.planner_score == 72.5
        assert s.cost_total == 6.41
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
        - Live deployed fn_planner_scorecard emits 25 metrics.
        - Migration-076/077-era (what CI's db/schema.sql serves) emits 27 —
          the deployed function dropped `7d_avg_stress` + `7d_avg_dp_risk`.
        Schema covers both until G15 resyncs migrations with live.

        Plus the 9 graded + feasibility metrics modelled schema-first for the
        migration-146/147 compliance rearchitecture (band-compliance §6-§7):
        compliance_v2_raw/attributable/unachievable_frac, graded temp/vpd, and
        4 graded_*_stress_h — accepted BEFORE fn_planner_scorecard emits them so
        the extra='forbid' contract cannot 500 the public /api/v1/scorecard."""
        names = ScorecardResponse.metric_names()
        assert len(names) == 27 + 9
        assert "planner_score" in names
        assert "7d_avg_score" in names
        assert "7d_avg_stress" in names  # CI-only until G15
        assert "7d_avg_dp_risk" in names  # CI-only until G15
        # Graded compliance metrics (schema-first, migration 146/147).
        assert "compliance_v2_attributable_pct" in names
        assert "graded_vpd_high_stress_h" in names


# ── Drift guard: live fn_planner_scorecard() metric names must be a subset of
#    ScorecardResponse.metric_names(). Skips if no DB is reachable. ─────
def _docker_available() -> bool:
    r = subprocess.run(["docker", "ps"], capture_output=True, text=True, check=False)
    return r.returncode == 0


def _ci_postgres_reachable() -> bool:
    return bool(os.environ.get("POSTGRES_HOST"))


@pytest.mark.skipif(
    not (_ci_postgres_reachable() or _docker_available()),
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
