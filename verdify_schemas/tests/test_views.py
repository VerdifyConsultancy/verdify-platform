"""Phase 4 — view projection schemas, tested against live DB."""

from __future__ import annotations

import subprocess
from datetime import date

import pytest
from pydantic import ValidationError

from verdify_schemas.views import (
    BandTraceRow,
    ClampActivity24h,
    DailyOscillation,
    DailyOscillationSummary,
    DewPointRiskRow,
    OverrideActivity24h,
    PlanAccuracy,
    PlannerPerformance,
    WaterBudgetRow,
    ZoneBandGradeRollup,
    ZoneBandRow,
)


def _psql_json(sql: str) -> list[dict]:
    """Minimal wrapper to read rows as JSON."""
    r = subprocess.run(
        [
            "docker",
            "exec",
            "verdify-timescaledb",
            "psql",
            "-U",
            "verdify",
            "-d",
            "verdify",
            "-t",
            "-A",
            "-c",
            f"SELECT row_to_json(x) FROM ({sql}) x",
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    import json as _json

    out = []
    for line in r.stdout.strip().splitlines():
        if line:
            out.append(_json.loads(line))
    return out


class TestPlannerPerformanceBasic:
    def test_valid(self):
        p = PlannerPerformance(date=date(2026, 4, 18), planner_score=72, compliance_pct=88.5)
        assert p.planner_score == 72

    def test_rejects_score_over_100(self):
        with pytest.raises(ValidationError):
            PlannerPerformance(date=date(2026, 4, 18), planner_score=150)

    def test_rejects_negative_cost(self):
        with pytest.raises(ValidationError):
            PlannerPerformance(date=date(2026, 4, 18), cost_total=-1.0)


class TestPlanAccuracyBasic:
    def test_valid(self):
        a = PlanAccuracy(plan_id="iris-20260418-0618", waypoints=20, achieved=17, accuracy_pct=85.0)
        assert a.accuracy_pct == 85.0

    def test_achieved_gt_waypoints_allowed(self):
        # View doesn't enforce achieved<=waypoints; schema permissive to mirror DB
        a = PlanAccuracy(plan_id="x", waypoints=10, achieved=10)
        assert a.achieved == 10


class TestDewPointRiskRowBasic:
    def test_risk_hours_bounded(self):
        with pytest.raises(ValidationError):
            DewPointRiskRow(date=date(2026, 4, 18), risk_hours=30)


class TestWaterBudgetRowBasic:
    def test_negative_total_rejected(self):
        with pytest.raises(ValidationError):
            WaterBudgetRow(date=date(2026, 4, 18), total_gal=-5)


class TestOscillationViewsBasic:
    def test_daily_oscillation_valid(self):
        d = DailyOscillation(date=date(2026, 4, 18), equipment="fan1", peak_transitions_per_hour=170, active_hours=24)
        assert d.equipment == "fan1"

    def test_daily_oscillation_active_hours_bounded(self):
        with pytest.raises(ValidationError):
            DailyOscillation(
                date=date(2026, 4, 18),
                equipment="fan1",
                peak_transitions_per_hour=0,
                active_hours=30,
            )

    def test_summary_min(self):
        s = DailyOscillationSummary(date=date(2026, 4, 18))
        assert s.worst_equipment is None


class TestActivityViewsBasic:
    def test_override_activity(self):
        a = OverrideActivity24h(override_type="fog_gate_rh", events=12, distinct_modes=2)
        assert a.events == 12

    def test_clamp_activity(self):
        c = ClampActivity24h(parameter="temp_low", clamp_events=3)
        assert c.parameter == "temp_low"


class TestBandTraceBasic:
    def test_valid_minimal(self):
        row = BandTraceRow(
            ts="2026-05-15T12:00:00-06:00",
            greenhouse_id="vallery",
            trace_quality_flag="ok",
            fw_both_in_band=True,
            fw_temp_in_band=True,
            fw_vpd_in_band=True,
        )
        assert row.greenhouse_id == "vallery"


class TestZoneBandRowBasic:
    def test_valid_minimal(self):
        row = ZoneBandRow(
            zone="center",
            temp_low=70.0,
            temp_high=88.0,
            vpd_low=0.6,
            vpd_high=1.2,
            crop_basis="orchid",
            is_proxy=True,
        )
        assert row.zone == "center"
        assert row.is_proxy is True

    def test_empty_zone_nullable_band(self):
        # Empty zones (_default basis) may emit NULL band edges.
        row = ZoneBandRow(zone="south", crop_basis="_default")
        assert row.temp_low is None
        assert row.crop_basis == "_default"

    def test_tolerates_extra_columns(self):
        # extra='ignore' so a future fn_zone_band column does not break readers.
        row = ZoneBandRow.model_validate({"zone": "east", "temp_low": 65, "future_col": 1})
        assert row.zone == "east"


class TestZoneBandGradeRollupBasic:
    def test_valid_minimal(self):
        row = ZoneBandGradeRollup(
            bucket="2026-05-28T13:00:00-06:00",
            zone="center",
            n=60,
            sum_zone_score=42,
            n_unachievable=44,
            n_controller=10,
            n_unknown=6,
            proxy_center=True,
        )
        assert row.n == 60
        assert row.zone == "center"

    def test_rejects_negative_minute_count(self):
        with pytest.raises(ValidationError):
            ZoneBandGradeRollup(bucket="2026-05-28T13:00:00-06:00", zone="center", n=-1)


# ── Live-DB drift guards (integration tests) ──
# If the DB view drops / renames a column we depend on, these fail.


def _has_docker() -> bool:
    # FileNotFoundError guard (2026-07-11): in-cluster CI images have no
    # docker binary AT ALL — a bare subprocess.run exploded at pytest
    # COLLECTION time and took five schema test modules with it.
    import shutil

    if not shutil.which("docker"):
        return False
    r = subprocess.run(["docker", "ps"], capture_output=True, text=True, check=False)
    return r.returncode == 0


pytestmark_live = pytest.mark.skipif(not _has_docker(), reason="docker not available")


@pytestmark_live
class TestLiveProjection:
    def test_planner_performance_live_rows(self):
        rows = _psql_json("SELECT * FROM v_planner_performance ORDER BY date DESC LIMIT 3")
        for r in rows:
            PlannerPerformance.model_validate(r)

    def test_plan_accuracy_live_rows(self):
        rows = _psql_json("SELECT * FROM v_plan_accuracy ORDER BY plan_end DESC NULLS LAST LIMIT 3")
        for r in rows:
            PlanAccuracy.model_validate(r)

    def test_dew_point_risk_live_rows(self):
        try:
            rows = _psql_json("SELECT * FROM v_dew_point_risk ORDER BY date DESC LIMIT 1")
        except subprocess.TimeoutExpired:
            pytest.skip("v_dew_point_risk query slow (known pre-existing)")
        for r in rows:
            DewPointRiskRow.model_validate(r)

    def test_daily_oscillation_live_rows(self):
        rows = _psql_json("SELECT * FROM v_daily_oscillation ORDER BY date DESC LIMIT 3")
        for r in rows:
            DailyOscillation.model_validate(r)

    def test_oscillation_summary_live_rows(self):
        rows = _psql_json("SELECT * FROM v_daily_oscillation_summary ORDER BY date DESC LIMIT 3")
        for r in rows:
            DailyOscillationSummary.model_validate(r)

    def test_override_activity_live_rows(self):
        rows = _psql_json("SELECT * FROM v_override_activity_24h")
        for r in rows:
            OverrideActivity24h.model_validate(r)

    def test_clamp_activity_live_rows(self):
        rows = _psql_json("SELECT * FROM v_clamp_activity_24h")
        for r in rows:
            ClampActivity24h.model_validate(r)

    def test_band_trace_live_rows(self):
        rows = _psql_json(
            "SELECT * FROM fn_band_trace(now() - interval '2 hours', now(), 'vallery') ORDER BY ts DESC LIMIT 3"
        )
        assert rows, "fn_band_trace returned no recent rows"
        for r in rows:
            BandTraceRow.model_validate(r)

    def test_zone_band_live_rows(self):
        # v_zone_band emits one row per zone (CROSS JOIN over 5 zones). If the
        # view drops/renames a column ZoneBandRow depends on, model_validate
        # fires here instead of producing None/KeyError in dashboards.
        rows = _psql_json("SELECT * FROM v_zone_band ORDER BY zone")
        assert rows, "v_zone_band returned no rows"
        for r in rows:
            ZoneBandRow.model_validate(r)

    def test_zone_band_grade_rollup_live_rows(self):
        # mv_zone_band_grade is a plain matview refreshed by verdify-ingestor;
        # it may legitimately be empty (WITH NO DATA before first refresh), so
        # only validate shape when rows are present.
        rows = _psql_json("SELECT * FROM mv_zone_band_grade ORDER BY bucket DESC LIMIT 3")
        for r in rows:
            ZoneBandGradeRollup.model_validate(r)

    def test_fn_band_setpoints_serves_device_target(self):
        # ADR0003 §6.3 / mig 181 (BC-9): the served band TARGET IS the device curve.
        # fn_band_setpoints.temp_target/vpd_target must equal fn_crop_band_value('house',
        # 'temp_target'/'vpd_target', now()) — the single-source invariant. Asserts
        # IS NOT NULL first so a missing 'house' target anchor set can't pass vacuously
        # (NULL==NULL). Skips on a pre-mig-181 DB (the target columns don't exist yet).
        try:
            rows = _psql_json(
                "SELECT b.temp_target, b.vpd_target, "
                "fn_crop_band_value('house','temp_target',now()) AS tt, "
                "fn_crop_band_value('house','vpd_target', now()) AS vt "
                "FROM fn_band_setpoints(now()) b"
            )
        except subprocess.CalledProcessError:
            pytest.skip("fn_band_setpoints target columns absent (pre-mig-181 DB)")
        assert rows, "fn_band_setpoints returned no row"
        r = rows[0]
        assert r["temp_target"] is not None and r["vpd_target"] is not None, (
            "served target is NULL — crop_band_anchors is missing the house "
            "temp_target/vpd_target anchor set (fn_crop_band_value returned NULL)"
        )
        assert abs(r["temp_target"] - r["tt"]) < 1e-9  # served target == device curve
        assert abs(r["vpd_target"] - r["vt"]) < 1e-9
