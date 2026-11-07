"""verdify_services.series and its routes (VD-56, VD-57): pure rules and SQL routing on a fake connection.

Covers the bucket choice, the daily switch, the retention marking, ``lane_runs()``, the band
step and carry, the lanes' unknown spans, the scorecard reasons, the forecast keys, API-06's
zone, the error codes and the routes against the committed OpenAPI. The database itself is
exercised by ``tests/test_dashboard_db_series.py`` and ``tests/test_dashboard_db_next_reads.py``
on the in-cluster campaign database; ``tests/test_verdify_services_band_preview.py`` has API-19.
"""

from __future__ import annotations

import asyncio
import importlib
import re
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import asyncpg
import pytest
import yaml
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from verdify_services import series as s
from verdify_services.labels import EQUIPMENT_LABELS, EQUIPMENT_LINE_IDS
from verdify_services.problems import ServiceError
from verdify_services.reads import Span

ROOT = Path(__file__).resolve().parents[1]
OPENAPI = yaml.safe_load((ROOT / "docs/design/dashboard/openapi/dashboard-api.v1.yaml").read_text())
NOW = datetime(2026, 11, 2, 18, 0, tzinfo=UTC)
MIN = timedelta(minutes=1)


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).astimezone(UTC)


class FakeConnection:
    """Routes each fetch by a marker in its SQL; records every call."""

    def __init__(self, routes=None, error=None):
        self.routes = list(routes or [])
        self.error = error
        self.fetches: list[tuple[str, tuple]] = []
        self.executed: list[str] = []
        self.readonly = None

    def transaction(self, readonly=False):
        self.readonly = readonly
        return _Transaction()

    async def execute(self, sql, *args):
        self.executed.append(sql)

    async def fetch(self, sql, *args):
        self.fetches.append((sql, args))
        if self.error is not None:
            raise self.error
        for marker, rows in self.routes:
            if marker in sql:
                return rows(*args) if callable(rows) else rows
        return []


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def run(coroutine):
    return asyncio.run(coroutine)


def code_of(call) -> tuple[str, int]:
    with pytest.raises(ServiceError) as caught:
        run(call) if asyncio.iscoroutine(call) else call()
    return caught.value.problem.code, caught.value.problem.status


# ─────────────────────────────────────────────────────────────── lane_runs()
def test_lane_runs_merge_repeats_order_rows_and_let_the_later_row_win():
    t = at("2026-11-01T10:00:00+00:00")
    rows = [(t + 30 * MIN, False), (t, True), (t + 5 * MIN, True), (t + 30 * MIN, True), (t + 40 * MIN, False)]
    spans = s.lane_runs(rows, t + 60 * MIN)
    # 10:30 holds two rows; the later one (True) wins, so no zero-length run and no edge there.
    assert spans == [Span(True, t, t + 40 * MIN), Span(False, t + 40 * MIN, t + 60 * MIN)]
    assert s.lane_runs([(t, "on"), (t + MIN, "on")], t + 2 * MIN) == [Span("on", t, t + 2 * MIN)]
    assert s.lane_runs([], t) == []


# ─────────────────────────────────────────────────────────────── buckets and the daily switch
@pytest.mark.parametrize(
    ("hours", "points", "bucket_s"),
    [
        (24, 1500, 60),
        (72, 1500, 300),
        (24 * 7, 1500, 900),  # "one point per 15 minutes" for 7 d at 1,500 points (SCR-02)
        (24 * 30, 1500, 3600),
        (24 * 30, 250, 10800),
        (24 * 30, 200, 86400),
    ],
)
def test_bucket_is_the_smallest_that_fits_the_points(hours, points, bucket_s):
    to = at("2026-11-02T18:00:00+00:00")
    assert s.choose_bucket(to - timedelta(hours=hours), to, points) == bucket_s


def test_day_buckets_follow_denver_midnights_and_count_the_25_hour_day():
    from_, to = at("2026-10-03T00:00:00-06:00"), at("2026-11-02T00:00:00-07:00")
    starts = s.denver_day_starts(from_, to)
    assert len(starts) == 30 and starts[0] == at("2026-10-03T06:00:00+00:00")
    fall_back = starts.index(at("2026-11-01T06:00:00+00:00"))
    assert starts[fall_back] == s.denver_midnight(date(2026, 11, 1))
    assert (s.denver_midnight(date(2026, 11, 2)) - starts[fall_back]) == timedelta(hours=25)
    assert s.choose_bucket(from_, to, 31) == s.DAY_S


def test_sub_day_buckets_align_like_timescaledb_and_cover_a_partial_first_bucket():
    starts = s.aligned_starts(at("2026-11-02T00:13:00+00:00"), at("2026-11-02T01:00:00+00:00"), 1500)
    # time_bucket('1500 s', '2026-11-02 00:13Z') is 1793577600 on TimescaleDB 2.25 (origin 2000-01-03).
    assert int(starts[0].timestamp()) == 1793577600
    assert len(starts) == s.bucket_count(at("2026-11-02T00:13:00+00:00"), at("2026-11-02T01:00:00+00:00"), 1500)


def test_daily_mode_over_31_days_beyond_retention_or_for_a_daily_key():
    temp, heap, card = (
        s.METRICS["climate.temp_avg"],
        s.METRICS["diagnostics.heap_free_kb"],
        s.METRICS["daily.water_gal"],
    )
    to = NOW
    assert not s.is_daily_mode([temp], to - timedelta(days=31), to, NOW)
    assert s.is_daily_mode([temp], to - timedelta(days=31, seconds=1), to, NOW)
    old_to = NOW - timedelta(days=380)
    assert s.is_daily_mode([temp], old_to - timedelta(days=7), old_to, NOW)
    # No daily binding: the old window keeps its points and is marked beyond retention instead.
    assert not s.is_daily_mode([heap], old_to - timedelta(days=7), old_to, NOW)
    assert s.is_daily_mode([card], to - timedelta(days=7), to, NOW)


def test_plan_reads_daily_summary_in_daily_mode_and_marks_raw_retention():
    old_to = NOW - timedelta(days=380)
    plan = s.plan_series(
        ["climate.temp_avg", "diagnostics.heap_free_kb"], old_to - timedelta(days=7), old_to, 1500, now=NOW
    )
    assert plan.daily_mode and plan.bucket_s == s.DAY_S
    temp, heap = plan.reads
    assert (temp.source, temp.retained_from) == ("daily_summary", None)
    assert (heap.source, heap.retained_from) == ("raw", NOW - timedelta(days=180))
    plan = s.plan_series(["climate.temp_avg"], NOW - timedelta(hours=24), NOW, 1500, now=NOW)
    assert not plan.daily_mode and plan.bucket_s == 60 and plan.reads[0].source == "raw"
    assert plan.reads[0].retained_from == NOW - timedelta(days=365)


def test_raw_columns_mark_buckets_before_retention_and_fill_empty_buckets_with_null():
    axis = s.aligned_starts(at("2026-11-01T00:00:00+00:00"), at("2026-11-01T00:04:00+00:00"), 60)
    read = s.KeyRead(s.METRICS["climate.temp_avg"], "raw", axis[2])
    rows = [
        {"bucket": axis[0], "a0": 70.0, "n0": 69.0, "x0": 71.0, "c0": 1, "last_ts": axis[0] + timedelta(seconds=5)},
        {"bucket": axis[1], "a0": None, "n0": None, "x0": None, "c0": None, "last_ts": None},  # gap-filled
        {"bucket": axis[2], "a0": 72.5, "n0": 72.0, "x0": 73.0, "c0": 2, "last_ts": axis[2] + timedelta(seconds=40)},
    ]
    columns, watermark = s.raw_columns(axis, rows, [read])
    temp = columns["climate.temp_avg"]
    # Buckets 0 and 1 start before retained_from: beyond retention, never values and never gaps.
    assert temp.avg == [None, None, 72.5, None] and temp.count == [0, 0, 2, 0]
    assert temp.min == [None, None, 72.0, None] and temp.max == [None, None, 73.0, None]
    assert watermark == axis[2] + timedelta(seconds=40)


# ─────────────────────────────────────────────────────────────── series() on a fake connection
def test_a_week_ending_380_days_ago_reads_daily_summary_and_marks_the_rest_beyond_retention():
    old_to = s.denver_midnight(date(2025, 10, 18))
    from_ = s.denver_midnight(date(2025, 10, 11))
    summary = [
        {
            "date": date(2025, 10, 11) + timedelta(days=n),
            "temp_avg": 65.0 + n,
            "temp_min": 55.0,
            "temp_max": 75.0,
            "stamp": at("2026-06-12T17:00:00+00:00"),
        }
        for n in range(7)
        if n != 3  # a day with no summary row is a gap
    ]
    conn = FakeConnection([('"daily_summary"', summary), ("data_gaps", [])])
    result = run(s.series(conn, ["climate.temp_avg", "climate.temp_north"], from_, old_to, now=NOW))
    assert conn.readonly is True
    assert conn.executed == ["SET LOCAL statement_timeout = '10s'; SET LOCAL plan_cache_mode = force_custom_plan"]
    # temp_north's whole window is older than its 365 days: no raw query at all.
    assert not any('"climate"' in sql for sql, _ in conn.fetches)
    assert result.bucket_s == s.DAY_S and len(result.t) == 7
    temp, north = result.series["climate.temp_avg"], result.series["climate.temp_north"]
    assert temp.source == "daily_summary" and temp.retained_from is None
    assert temp.avg == [65.0, 66.0, 67.0, None, 69.0, 70.0, 71.0] and temp.count == [1, 1, 1, 0, 1, 1, 1]
    assert north.source == "raw" and north.retained_from == NOW - timedelta(days=365)
    assert north.avg == [None] * 7 and north.count == [0] * 7
    payload = result.model_dump(mode="json", by_alias=True)
    assert "reason" not in payload["series"]["climate.temp_avg"] and payload["from"] == "2025-10-11T06:00:00Z"
    assert result.watermark == at("2026-06-12T17:00:00+00:00")


def test_scorecard_keys_carry_their_reasons_and_leave_missing_days_as_gaps():
    days = [date(2026, 10, 30), date(2026, 10, 31), date(2026, 11, 1)]
    rows = [
        {
            "date": days[0],
            "water_gal": 5.0,
            "water_reason": None,
            "time_in_band_day_pct": None,
            "band_reason": "band grade kept 3 days",
            "stamp": at("2026-11-01T05:38:00+00:00"),
        },
        {
            "date": days[1],
            "water_gal": None,
            "water_reason": "incomplete",
            "time_in_band_day_pct": 81.5,
            "band_reason": None,
            "stamp": at("2026-11-02T06:37:00+00:00"),
        },
    ]
    conn = FakeConnection([('"v_daily_scorecard"', rows)])
    result = run(
        s.series(
            conn,
            ["daily.water_gal", "daily.time_in_band_day_pct"],
            s.denver_midnight(days[0]),
            s.denver_midnight(date(2026, 11, 2)),
            points=31,
            now=NOW,
        )
    )
    water, day = result.series["daily.water_gal"], result.series["daily.time_in_band_day_pct"]
    assert result.bucket_s == s.DAY_S and len(result.t) == 3
    assert water.source == day.source == "scorecard"
    assert water.avg == water.min == water.max == [5.0, None, None] and water.count == [1, 0, 0]
    assert water.reason == [None, "incomplete", None]  # 11-01 has no row: a gap with no reason
    assert day.avg == [None, 81.5, None] and day.reason == ["band grade kept 3 days", None, None]
    sql, args = conn.fetches[0]
    assert '"water_gal", "water_reason", "time_in_band_day_pct", "band_reason"' in sql
    assert args == (days[0], days[2])
    assert result.model_dump(mode="json", by_alias=True)["series"]["daily.water_gal"]["reason"][1] == "incomplete"


def test_raw_series_reads_one_bounded_gapfill_per_table_and_returns_the_window_outages():
    from_, to = at("2026-11-01T21:00:00+00:00"), at("2026-11-01T21:05:00+00:00")
    climate = [
        {
            "bucket": from_ + n * MIN,
            "a0": 80.0 + n,
            "n0": 79.0,
            "x0": 81.0,
            "c0": 1,
            "a1": None,
            "n1": None,
            "x1": None,
            "c1": None,
            "last_ts": from_ + n * MIN + timedelta(seconds=30),
        }
        for n in range(5)
    ]
    diagnostics = [{"bucket": from_, "a0": 57.6, "n0": 57.6, "x0": 57.6, "c0": 1, "last_ts": from_}]
    gaps = [
        {
            "start_ts": at("2026-11-01T21:01:00+00:00"),
            "end_ts": at("2026-11-01T21:03:00+00:00"),
            "reason": "ingestor_restart",
        }
    ]
    conn = FakeConnection([('"climate"', climate), ('"diagnostics"', diagnostics), ("data_gaps", gaps)])
    keys = ["climate.temp_avg", "climate.rh_avg", "diagnostics.heap_free_kb"]
    result = run(s.series(conn, keys, from_, to, now=NOW))
    climate_sql, climate_args = conn.fetches[0]
    assert 'avg("temp_avg")' in climate_sql and 'avg("rh_avg")' in climate_sql
    assert "time_bucket_gapfill($3::interval, ts, $1::timestamptz, $2::timestamptz)" in climate_sql
    assert "WHERE ts >= $1::timestamptz AND ts < $2::timestamptz" in climate_sql
    assert climate_args == (from_, to, timedelta(seconds=60))
    assert 'avg("heap_bytes")' in conn.fetches[1][0]
    assert result.bucket_s == 60 and result.t == [int((from_ + n * MIN).timestamp()) for n in range(5)]
    assert result.series["climate.temp_avg"].avg == [80.0, 81.0, 82.0, 83.0, 84.0]
    assert result.series["climate.rh_avg"].avg == [None] * 5 and result.series["climate.rh_avg"].count == [0] * 5
    assert result.series["diagnostics.heap_free_kb"].avg == [57.6, None, None, None, None]
    assert [(gap.start, gap.reason) for gap in result.gaps] == [(gaps[0]["start_ts"], "ingestor_restart")]
    assert result.watermark == from_ + 4 * MIN + timedelta(seconds=30) and result.server_time == NOW


def test_day_buckets_use_the_denver_gapfill_without_a_width_parameter():
    sql = s.raw_series_sql("diagnostics", ["heap_bytes"], day_buckets=True)
    assert "time_bucket_gapfill('1 day'::interval, ts, 'America/Denver', $1::timestamptz, $2::timestamptz)" in sql
    assert "$3" not in sql
    with pytest.raises(ValueError):
        s.raw_series_sql("climate", ["temp_avg; DROP TABLE climate"], day_buckets=False)


def test_series_validation_codes():
    to = NOW
    conn = FakeConnection()
    assert code_of(s.series(conn, ["climate.nope"], to - MIN, to, now=NOW)) == ("unknown_target", 422)
    assert code_of(s.series(conn, [], to - MIN, to, now=NOW)) == ("invalid_request", 400)
    assert code_of(s.series(conn, ["climate.temp_avg"] * 9, to - MIN, to, now=NOW)) == ("invalid_request", 400)
    assert code_of(s.series(conn, ["climate.temp_avg"], to - timedelta(days=401), to, now=NOW)) == (
        "window_too_large",
        422,
    )
    assert code_of(s.series(conn, ["climate.temp_avg"], to - MIN, to, points=5, now=NOW)) == ("invalid_request", 400)
    assert code_of(s.series(conn, ["climate.temp_avg"], to, to, now=NOW)) == ("invalid_request", 400)
    naive = datetime(2026, 11, 1, 12, 0)
    assert code_of(s.series(conn, ["climate.temp_avg"], naive, naive + MIN, now=NOW)) == ("invalid_request", 400)
    assert conn.fetches == []


def test_a_statement_timeout_is_503_query_timeout():
    conn = FakeConnection(error=asyncpg.QueryCanceledError("canceling statement due to statement timeout"))
    with pytest.raises(ServiceError) as caught:
        run(s.series(conn, ["climate.temp_avg"], NOW - timedelta(days=30), NOW, now=NOW))
    problem = caught.value.problem
    assert (problem.code, problem.status, problem.retryable) == ("query_timeout", 503, True)
    assert "Narrow" in problem.detail


# ─────────────────────────────────────────────────────────────── the registry (API-03)
def test_registry_bindings_follow_the_issue():
    items = s.metric_list().items
    assert len({item.key for item in items}) == len(items) == len(s.METRICS)
    groups = {"house climate", "zones", "water and soil", "light", "outdoor", "targets", "controller", "daily"}
    assert {item.group for item in items} == groups
    for binding in s.METRICS.values():
        if binding.key.startswith("climate."):
            assert binding.raw.column == binding.key.removeprefix("climate.") and binding.retention_days == 365
            assert binding.cadence_s == (300 if binding.raw.column.startswith("hydro_") else 60)
        elif binding.key.startswith("diagnostics."):
            assert binding.retention_days == 180 and binding.cadence_s == 60 and binding.group == "controller"
        elif binding.key.startswith("forecast."):
            assert binding.raw is None and binding.daily is None and binding.provenance == "DERIVED"
            assert binding.forecast.column == binding.key.removeprefix("forecast.")
            assert (binding.retention_days, binding.cadence_s, binding.group) == (400, 3600, "outdoor")
        else:
            assert binding.key.startswith("daily.") and binding.raw is None
            assert (binding.retention_days, binding.cadence_s, binding.group) == (400, 86400, "daily")
            assert binding.daily.view == "v_daily_scorecard" and binding.daily.source == "scorecard"
    controller = {key: s.METRICS[key].raw.column for key in s.METRICS if key.startswith("diagnostics.")}
    assert controller == {
        "diagnostics.heap_free_kb": "heap_bytes",
        "diagnostics.heap_largest_block_kb": "heap_largest_free_block_kb",
        "diagnostics.rssi_dbm": "wifi_rssi",
        "diagnostics.uptime_s": "uptime_s",
    }
    summary = {key: b.daily for key, b in s.METRICS.items() if b.daily and b.daily.source == "daily_summary"}
    assert {key: (d.avg, d.min, d.max) for key, d in summary.items()} == {
        "climate.temp_avg": ("temp_avg", "temp_min", "temp_max"),
        "climate.rh_avg": ("rh_avg", "rh_min", "rh_max"),
        "climate.vpd_avg": ("vpd_avg", "vpd_min", "vpd_max"),
        "climate.co2_ppm": ("co2_avg", None, None),
        "climate.outdoor_temp_f": (None, "outdoor_temp_min", "outdoor_temp_max"),
    }
    reasons = {key: b.daily.reason for key, b in s.METRICS.items() if key.startswith("daily.")}
    assert reasons == {
        "daily.temp_in_band_pct": None,
        "daily.vpd_in_band_pct": None,
        "daily.time_in_band_day_pct": "band_reason",
        "daily.time_in_band_night_pct": "band_reason",
        "daily.temp_below_band_pct": None,
        "daily.vpd_below_band_pct": None,
        "daily.vpd_in_band_day_pct": None,
        "daily.vpd_in_band_night_pct": None,
        "daily.water_gal": "water_reason",
        "daily.iris_required_written": None,
        "daily.iris_required_missed": None,
        "daily.alerts_critical": None,
        "daily.alerts_warning": None,
        "daily.alerts_info": None,
    }
    provenance = {
        key: s.METRICS[key].provenance
        for key in ("climate.temp_avg", "climate.vpd_avg", "climate.dew_point", "climate.house_vpd_target")
    }
    assert provenance == {
        "climate.temp_avg": "MEASURED",
        "climate.vpd_avg": "DERIVED",
        "climate.dew_point": "DERIVED",
        "climate.house_vpd_target": "DERIVED",
    }
    targets = {key for key, b in s.METRICS.items() if b.group == "targets"}
    assert {
        "climate.house_temp_target_f",
        "climate.house_vpd_target",
        "climate.vpd_target_center",
        "climate.house_temp_delta_f",
        "climate.vpd_delta_east",
    } <= targets


def test_target_deltas_read_as_the_distance_from_target():
    # The firmware publishes each *_delta as the measured value minus its target, not a band width.
    controls = (ROOT / "firmware/greenhouse/controls.yaml").read_text()
    assert "id(gh_house_temp_delta).publish_state(Tin - setpts.temp_target);" in controls
    assert "id(gh_house_vpd_delta).publish_state(VPD - setpts.vpd_target);" in controls
    assert "id(gh_zone_vpd_delta_center).publish_state(zvpd[ZONE_CENTER] - zb[ZONE_CENTER].target);" in controls
    deltas = {key: b.label for key, b in s.METRICS.items() if b.group == "targets" and "_delta" in key}
    assert len(deltas) == 6 and all(label.endswith("minus target") for label in deltas.values()), deltas


def test_the_scorecard_view_has_every_bound_column():
    # The newest definition of the view: 290 replaced 285's (#1358).
    sql = (ROOT / "db/migrations/290-band-compliance.sql").read_text()
    body = sql.split("CREATE OR REPLACE VIEW public.v_daily_scorecard AS", 1)[1].split(";\n", 1)[0]
    for binding in s.METRICS.values():
        if binding.daily is not None and binding.daily.view == s.SCORECARD_VIEW:
            for column in (binding.daily.avg, binding.daily.reason, binding.daily.stamp):
                if column:
                    assert re.search(rf"(?: AS {column}|[db]\.{column}),?\n", body), column


# ─────────────────────────────────────────────────────────────── the forecast keys (API-04)
FORECAST = ["forecast.temp_f", "forecast.rh_pct", "forecast.vpd_kpa"]


def forecast_row(ts, fetched_at, temp_f=None, rh_pct=None, vpd_kpa=None):
    return {"ts": ts, "temp_f": temp_f, "rh_pct": rh_pct, "vpd_kpa": vpd_kpa, "fetched_at": fetched_at}


def test_the_forecast_keys_are_in_the_registry_and_say_what_they_are():
    items = {item.key: item for item in s.metric_list().items}
    assert {key: items[key].unit for key in FORECAST} == {
        "forecast.temp_f": "°F",
        "forecast.rh_pct": "%",
        "forecast.vpd_kpa": "kPa",
    }
    assert all(items[key].daily is False and items[key].group == "outdoor" for key in FORECAST)
    assert (items["forecast.solar_w_m2"].unit, items["forecast.solar_w_m2"].group) == ("W/m²", "outdoor")


def test_a_forecast_key_never_turns_a_window_daily_and_stays_a_forecast_when_it_is_daily():
    from_, to = NOW, NOW + timedelta(days=3)
    plan = s.plan_series(FORECAST, from_, to, 1500, now=NOW)
    assert not plan.daily_mode and plan.bucket_s == 300
    assert [read.source for read in plan.reads] == ["forecast"] * 3
    assert all(read.retained_from is None for read in plan.reads)
    wide = s.plan_series(["forecast.temp_f", "climate.temp_avg"], NOW - timedelta(days=60), NOW, 1500, now=NOW)
    assert wide.daily_mode and wide.bucket_s == s.DAY_S
    assert [read.source for read in wide.reads] == ["forecast", "daily_summary"]


def test_below_an_hour_a_forecast_value_sits_only_in_the_bucket_that_holds_its_hour():
    from_ = at("2026-11-03T00:30:00+00:00")
    to = at("2026-11-03T02:30:00+00:00")
    rows = [
        forecast_row(at("2026-11-03T01:00:00+00:00"), NOW, temp_f=48.8),
        forecast_row(at("2026-11-03T02:00:00+00:00"), NOW + MIN, temp_f=47.1),
    ]
    conn = FakeConnection([("weather_forecast", rows)])
    result = run(s.series(conn, ["forecast.temp_f"], from_, to, now=NOW))
    assert result.bucket_s == 60 and len(result.t) == 120
    temp = result.series["forecast.temp_f"]
    assert temp.source == "forecast" and temp.retained_from is None
    assert {result.t[i]: temp.avg[i] for i, count in enumerate(temp.count) if count} == {
        int(at("2026-11-03T01:00:00+00:00").timestamp()): 48.8,
        int(at("2026-11-03T02:00:00+00:00").timestamp()): 47.1,
    }
    assert sum(temp.count) == 2 and temp.issued_at == NOW + MIN


def test_the_forecast_query_is_the_newest_issue_per_hour_bounded_by_the_window():
    from_, to = NOW, NOW + timedelta(hours=6)
    conn = FakeConnection([("weather_forecast", [])])
    result = run(s.series(conn, FORECAST, from_, to, now=NOW))
    sql, args = next(call for call in conn.fetches if "weather_forecast" in call[0])
    assert sql == (
        'SELECT DISTINCT ON (ts) ts, "temp_f", "rh_pct", "vpd_kpa", fetched_at FROM public.weather_forecast '
        "WHERE ts >= $1::timestamptz AND ts < $2::timestamptz ORDER BY ts, fetched_at DESC"
    )
    assert args == (from_, to)
    assert [call[0] for call in conn.fetches if '"climate"' in call[0] or '"diagnostics"' in call[0]] == []
    for key in FORECAST:  # the window holds no forecast row: every bucket is a gap, and no issue time
        column = result.series[key]
        assert column.avg == [None] * len(result.t) and column.count == [0] * len(result.t)
        assert column.issued_at is None
    assert result.watermark is None


def test_forecast_buckets_aggregate_like_raw_keys_with_each_columns_own_issue():
    from_ = at("2026-11-03T00:00:00+00:00")
    axis = s.aligned_starts(from_, from_ + timedelta(hours=6), 10800)
    reads = [
        s.KeyRead(s.METRICS["forecast.temp_f"], "forecast", None),
        s.KeyRead(s.METRICS["forecast.rh_pct"], "forecast", None),
    ]
    newest, older = NOW, NOW - timedelta(hours=1)
    rows = [
        forecast_row(from_, older, temp_f=40.0, rh_pct=30.0),
        forecast_row(from_ + timedelta(hours=1), older, temp_f=44.0),
        forecast_row(from_ + timedelta(hours=2), older, temp_f=48.0, rh_pct=float("nan")),
        forecast_row(from_ + timedelta(hours=3), newest, temp_f=50.0),  # a null rh_pct is not used for rh_pct
    ]
    columns = s.forecast_columns(axis, rows, reads)
    temp, rh = columns["forecast.temp_f"], columns["forecast.rh_pct"]
    assert (temp.avg, temp.min, temp.max, temp.count) == ([44.0, 50.0], [40.0, 50.0], [48.0, 50.0], [3, 1])
    assert (rh.avg, rh.count) == ([30.0, None], [1, 0])
    assert (temp.issued_at, rh.issued_at) == (newest, older)


def test_forecast_day_buckets_are_denver_days_and_the_25_hour_day_counts_its_hours():
    from_, to = s.denver_midnight(date(2026, 10, 1)), s.denver_midnight(date(2026, 11, 3))
    rows = [
        forecast_row(s.denver_midnight(date(2026, 11, 1)) + timedelta(hours=hour), NOW, vpd_kpa=1.0 + hour)
        for hour in range(25)
    ]
    conn = FakeConnection([("weather_forecast", rows)])
    result = run(s.series(conn, ["forecast.vpd_kpa"], from_, to, now=NOW))
    assert result.bucket_s == s.DAY_S and len(result.t) == 33
    column = result.series["forecast.vpd_kpa"]
    assert sum(column.count) == 25 and column.count[-2] == 25 and column.max[-2] == 25.0


def test_a_measured_key_keeps_its_watermark_beside_a_forecast_and_issued_at_is_only_on_forecast_keys():
    from_, to = NOW - timedelta(minutes=5), NOW + timedelta(minutes=5)
    measured_at = NOW - timedelta(minutes=1)
    climate = [
        {"bucket": from_, "a0": 50.0, "n0": 50.0, "x0": 50.0, "c0": 1, "last_ts": measured_at},
    ]
    forecast = [forecast_row(NOW + timedelta(minutes=2), NOW, temp_f=48.0)]
    conn = FakeConnection([('"climate"', climate), ("weather_forecast", forecast)])
    result = run(s.series(conn, ["climate.outdoor_temp_f", "forecast.temp_f"], from_, to, now=NOW))
    assert result.watermark == measured_at  # never the forecast's hour ahead, nor its issue time
    payload = result.model_dump(mode="json", by_alias=True)
    assert "issued_at" not in payload["series"]["climate.outdoor_temp_f"]
    assert payload["series"]["forecast.temp_f"]["issued_at"] == "2026-11-02T18:00:00Z"
    empty = s.series(FakeConnection(), ["forecast.temp_f"], from_, to, now=NOW)
    payload = run(empty).model_dump(mode="json", by_alias=True)["series"]["forecast.temp_f"]
    assert payload["issued_at"] is None and payload["source"] == "forecast"  # present, and null


# ─────────────────────────────────────────────────────────────── API-05 lanes
def test_not_reporting_spans_follow_the_300_s_climate_rule():
    start, end = at("2026-11-01T10:00:00+00:00"), at("2026-11-01T12:00:00+00:00")
    spans = s.not_reporting_spans(
        at("2026-11-01T09:56:00+00:00"),
        at("2026-11-01T11:50:00+00:00"),
        [(at("2026-11-01T11:20:00+00:00"), at("2026-11-01T11:40:00+00:00"))],
        start,
        end,
    )
    assert spans == [
        (at("2026-11-01T11:25:00+00:00"), at("2026-11-01T11:40:00+00:00")),
        (at("2026-11-01T11:55:00+00:00"), end),
    ]
    assert s.not_reporting_spans(None, None, [], start, end) == [(start, end)]
    assert s.not_reporting_spans(at("2026-11-01T10:10:00+00:00"), end - MIN, [], start, end)[0] == (
        start,
        at("2026-11-01T10:10:00+00:00"),
    )


def test_light_rows_go_stale_600_s_after_the_newest_row():
    start, end = at("2026-11-01T10:00:00+00:00"), at("2026-11-01T12:00:00+00:00")
    times = [start + n * 5 * MIN for n in range(1, 13)]  # 10:05 to 11:00
    assert s.stale_spans(times, start - 2 * MIN, start, end) == [(at("2026-11-01T11:10:00+00:00"), end)]
    # No row in the 600 s before the window: unknown until the first row.
    assert s.stale_spans(times, None, start, end)[0] == (start, start + 5 * MIN)


def test_lane_intervals_seed_collapse_and_never_turn_unknown_into_off():
    start, end = at("2026-11-01T10:00:00+00:00"), at("2026-11-01T12:00:00+00:00")
    rows = [(start + 30 * MIN, True), (start + 45 * MIN, False), (start + 46 * MIN, False)]
    unknown = [(at("2026-11-01T11:25:00+00:00"), at("2026-11-01T11:40:00+00:00"))]
    assert s.lane_intervals(False, rows, unknown, start, end) == [
        ("off", start, start + 30 * MIN),
        ("on", start + 30 * MIN, start + 45 * MIN),
        ("off", start + 45 * MIN, unknown[0][0]),
        ("unknown", unknown[0][0], unknown[0][1]),
        ("off", unknown[0][1], end),
    ]
    # No row ever before the window: unknown until the first row, never off.
    assert s.lane_intervals(None, [(start + 20 * MIN, True)], [], start, end) == [
        ("unknown", start, start + 20 * MIN),
        ("on", start + 20 * MIN, end),
    ]


def test_states_reads_seeds_rows_and_climate_edges_and_labels_every_lane():
    start, end = at("2026-11-01T10:00:00+00:00"), at("2026-11-01T12:00:00+00:00")
    light_times = [start + n * 5 * MIN - 2 * MIN for n in range(1, 14)]  # 10:03 to 11:03
    rows = [
        {"equipment": "fan1", "ts": start + 30 * MIN, "state": True},
        {"equipment": "fan1", "ts": start + 45 * MIN, "state": False},
        *({"equipment": "grow_light_main", "ts": ts, "state": True} for ts in light_times),
    ]
    edges = [
        {"prev_ts": None, "ts": at("2026-11-01T09:56:00+00:00"), "next_ts": at("2026-11-01T09:57:00+00:00")},
        {
            "prev_ts": at("2026-11-01T11:20:00+00:00"),
            "ts": at("2026-11-01T11:40:00+00:00"),
            "next_ts": at("2026-11-01T11:41:00+00:00"),
        },
        {"prev_ts": at("2026-11-01T11:58:00+00:00"), "ts": at("2026-11-01T11:59:00+00:00"), "next_ts": None},
    ]
    conn = FakeConnection(
        [
            (
                "fn_equip_at",
                [
                    {"equipment": "fan1", "state": False},
                    {"equipment": "grow_light_main", "state": True},
                    {"equipment": "drip_wall", "state": None},
                ],
            ),
            ("max(ts) AS ts", [{"equipment": "grow_light_main", "ts": start - 2 * MIN}]),
            ("SELECT equipment, ts, state", rows),
            ("lag(ts)", edges),
        ]
    )
    result = run(s.states(conn, start, end, ["grow_light_main", "drip_wall", "fan1"], now=NOW))
    assert [lane.equipment for lane in result.lanes] == ["fan1", "drip_wall", "grow_light_main"]
    fan, drip, light = result.lanes
    assert (fan.label, fan.group, fan.line_id) == ("Fan 1", "climate", None)
    assert (drip.label, drip.group, drip.line_id) == ("Wall drip", "water", "L01")
    assert [(i.state, i.start, i.end) for i in fan.intervals] == [
        ("off", start, start + 30 * MIN),
        ("on", start + 30 * MIN, start + 45 * MIN),
        ("off", start + 45 * MIN, at("2026-11-01T11:25:00+00:00")),
        ("unknown", at("2026-11-01T11:25:00+00:00"), at("2026-11-01T11:40:00+00:00")),
        ("off", at("2026-11-01T11:40:00+00:00"), end),
    ]
    assert [(i.state, i.start, i.end, i.cause) for i in drip.intervals] == [("unknown", start, end, None)]
    # The Lutron lane ignores the controller's silence and goes unknown 600 s after its last row.
    assert [(i.state, i.start, i.end) for i in light.intervals] == [
        ("on", start, at("2026-11-01T11:13:00+00:00")),
        ("unknown", at("2026-11-01T11:13:00+00:00"), end),
    ]
    seed_sql, seed_args = conn.fetches[0]
    assert seed_args == (["fan1", "drip_wall", "grow_light_main"], start)
    assert conn.fetches[1][1] == (["grow_light_main"], start - timedelta(seconds=600), start)
    assert conn.fetches[3][1] == (start - timedelta(seconds=300), end, timedelta(seconds=300))


def test_states_clamps_to_now_and_validates_window_and_equipment():
    start = NOW - timedelta(hours=1)
    conn = FakeConnection(
        [
            ("fn_equip_at", [{"equipment": "fan1", "state": True}]),
            (
                "lag(ts)",
                [
                    {"prev_ts": None, "ts": start - MIN, "next_ts": start},
                    {"prev_ts": NOW - 2 * MIN, "ts": NOW - MIN, "next_ts": None},
                ],
            ),
        ]
    )
    result = run(s.states(conn, start, NOW + timedelta(hours=12), ["fan1"], now=NOW))
    assert [(i.state, i.start, i.end) for i in result.lanes[0].intervals] == [("on", start, NOW)]
    assert len(run(s.states(FakeConnection(), start, NOW, now=NOW)).lanes) == 23
    assert code_of(s.states(conn, NOW - timedelta(days=32), NOW, now=NOW)) == ("window_too_large", 422)
    assert code_of(s.states(conn, start, NOW, ["fan9"], now=NOW)) == ("unknown_target", 422)
    future = run(s.states(FakeConnection(), NOW + MIN, NOW + timedelta(hours=1), now=NOW))
    assert all(lane.intervals == [] for lane in future.lanes)


def test_lane_list_and_labels_match_the_equipment_map():
    assert [name for name, _ in s.LANES] == [
        "fan1",
        "fan2",
        "vent",
        "heat1",
        "heat2",
        *EQUIPMENT_LINE_IDS,
        "grow_light_main",
        "grow_light_grow",
        "grow_light_main_intent",
        "grow_light_grow_intent",
        "occupancy",
        "leak_detected",
        "water_flowing",
    ]
    assert set(EQUIPMENT_LABELS) == {name for name, _ in s.LANES}
    assert s.LUTRON_LANES == {"grow_light_main", "grow_light_grow"}
    assert list(EQUIPMENT_LINE_IDS.values()) == [f"L{n:02d}" for n in range(1, 12)]


# ─────────────────────────────────────────────────────────────── API-06 band
@pytest.mark.parametrize(("hours", "step_s"), [(24, 300), (24 * 7, 600), (24 * 30, 1500), (24 * 400, 17400)])
def test_band_step_is_the_smallest_multiple_of_300_with_at_most_2000_points(hours, step_s):
    to = at("2026-11-02T18:00:00+00:00")
    from_ = to - timedelta(hours=hours)
    assert s.band_step(from_, to) == step_s
    assert len(s.aligned_starts(from_, to, step_s)) <= 2000
    assert step_s == 300 or len(s.aligned_starts(from_, to, step_s - 300)) > 2000


def test_band_step_counts_the_partial_first_bucket():
    from_ = at("2026-11-01T00:00:10+00:00")
    assert s.band_step(from_, from_ + timedelta(seconds=2000 * 300)) == 600


def test_band_carries_a_late_pass_at_most_600_s_and_nulls_past_retention():
    axis = s.aligned_starts(at("2026-11-01T21:30:00+00:00"), at("2026-11-01T22:10:00+00:00"), 300)

    def row(start, value, ts):
        return {
            "bucket": start,
            **{role: value for role in s.BAND_ROLES},
            **{f"{role}_ts": ts for role in s.BAND_ROLES},
        }

    rows = [
        row(axis[0], 75.09, at("2026-11-01T21:34:18+00:00")),
        row(axis[1], 75.04, at("2026-11-01T21:39:19+00:00")),
        row(axis[3], 74.92, at("2026-11-01T21:49:16+00:00")),
    ]
    band = s.band_columns(axis, rows, {}, retained_from=axis[0] - timedelta(days=1))
    assert band.temp_low == [75.09, 75.04, 75.04, 74.92, 74.92, 74.92, None, None]
    assert s.band_columns(axis, rows, {}, retained_from=axis[2]).temp_low[:3] == [None, None, 75.04]
    seeded = s.band_columns(axis, [], {"temp_low": (75.2, axis[0] - timedelta(seconds=200))}, axis[0] - MIN)
    assert seeded.temp_low[:3] == [75.2, 75.2, None] and seeded.temp_high == [None] * len(axis)


def test_the_2026_11_01_band_reads_74_92_to_84_92_with_the_crop_band():
    from_, to = at("2026-11-01T21:00:00+00:00"), at("2026-11-01T23:00:00+00:00")
    bucket = at("2026-11-01T21:45:00+00:00")
    passed = at("2026-11-01T21:49:16+00:00")
    rows = [
        {
            "bucket": bucket,
            "temp_low": 74.92,
            "temp_low_ts": passed,
            "temp_high": 84.92,
            "temp_high_ts": passed,
            "vpd_low": 0.873,
            "vpd_low_ts": passed,
            "vpd_high": 1.303,
            "vpd_high_ts": passed,
        }
    ]

    def crop(steps):
        return [{"ts": t, "temp_low": 74.97, "temp_high": 84.97, "vpd_low": 0.8732, "vpd_high": 1.3032} for t in steps]

    conn = FakeConnection([("DISTINCT ON (band_role)", []), ("fn_band_setpoints", crop), ("setpoint_snapshot", rows)])
    result = run(s.band(conn, from_, to, now=NOW))
    assert result.step_s == 300 and len(result.t) == 24
    index = result.t.index(int(bucket.timestamp()))
    assert (result.band.temp_low[index], result.band.temp_high[index]) == (74.92, 84.92)
    assert (result.band.vpd_low[index], result.band.vpd_high[index]) == (0.873, 1.303)
    assert result.crop.temp_low == [74.97] * 24
    band_sql, band_args = next(call for call in conn.fetches if "time_bucket_gapfill" in call[0])
    assert "zone = 'house'" in band_sql and band_args == (from_, to, timedelta(seconds=300))
    # Both band reads use the parameter index, never a scan of every readback row.
    indexed = (
        "parameter IN ('band_house_temp_low', 'band_house_temp_high', 'band_house_vpd_low', 'band_house_vpd_high')"
    )
    seed_sql = next(sql for sql, _ in conn.fetches if "DISTINCT ON (band_role)" in sql)
    assert indexed in band_sql and indexed in seed_sql
    payload = result.model_dump(mode="json", by_alias=True)
    assert set(payload) == {"from", "to", "step_s", "t", "band", "crop", "achievable"}
    assert payload["achievable"] is None  # only when asked for (#1433)


def test_band_parameters_are_the_names_emit_band_audit_writes():
    source = (ROOT / "ingestor/tasks/band_anchors.py").read_text()
    emit = source.split("async def emit_band_audit", 1)[1].split("\n\n\n", 1)[0]
    assert '(f"band_{zone}_{role}", value, zone, role, value)' in emit
    assert set(s.BAND_SQL) == set(s.BAND_SEED_SQL) == {"house", "center", "south", "west", "east"}
    for zone, sql in s.BAND_SQL.items():
        names = ", ".join(f"'band_{zone}_{role}'" for role in s.BAND_ROLES)
        for statement in (sql, s.BAND_SEED_SQL[zone]):
            assert f"parameter IN ({names})" in statement and f"zone = '{zone}'" in statement


def test_a_zone_reads_its_own_band_rows_and_the_house_temperature_with_its_vpd_widths():
    from_, to = at("2026-11-01T21:00:00+00:00"), at("2026-11-01T22:00:00+00:00")
    passed = at("2026-11-01T21:44:19+00:00")
    bucket = at("2026-11-01T21:40:00+00:00")
    rows = [
        {
            "bucket": bucket,
            "temp_low": 64.34,
            "temp_low_ts": passed,
            "temp_high": 74.34,
            "temp_high_ts": passed,
            "vpd_low": 0.612,
            "vpd_low_ts": passed,
            "vpd_high": 1.012,
            "vpd_high_ts": passed,
        }
    ]
    anchors = [
        {
            "series": "vpd_target",
            "anchor": anchor,
            "season": "all",
            "value": 0.8,
            "width_below": 0.18,
            "width_above": 0.22,
            "current_season": "fall",
        }
        for anchor in ("sr", "sm", "ss", "mid")
    ]

    def crop(steps, below, above):
        assert (below, above) == (0.18, 0.22)
        return [{"ts": t, "temp_low": 70.0, "temp_high": 80.0, "vpd_low": 0.6, "vpd_high": 1.0} for t in steps]

    conn = FakeConnection(
        [
            ("DISTINCT ON (band_role)", []),
            ("crop_band_anchors", anchors),
            ("fn_zone_vpd_targets", crop),
            ("setpoint_snapshot", rows),
        ]
    )
    result = run(s.band(conn, from_, to, "south", now=NOW))
    index = result.t.index(int(bucket.timestamp()))
    assert (result.band.temp_low[index], result.band.vpd_high[index]) == (64.34, 1.012)
    assert result.crop.temp_low == [70.0] * 12 and result.crop.vpd_high == [1.0] * 12
    band_sql = next(sql for sql, _ in conn.fetches if "time_bucket_gapfill" in sql)
    assert "zone = 'south'" in band_sql and "'band_south_temp_low'" in band_sql and "band_house" not in band_sql
    crop_sql = next(sql for sql, _ in conn.fetches if "fn_zone_vpd_targets" in sql)
    assert "target.vpd_target_south - $2" in crop_sql and "target.vpd_target_south + $3" in crop_sql
    assert "crop.temp_low" in crop_sql and "fn_band_setpoints" in crop_sql
    assert "fn_band_timeline" not in crop_sql and "fn_zone_band" not in crop_sql
    anchor_args = next(args for sql, args in conn.fetches if "crop_band_anchors" in sql)
    assert anchor_args == ("vallery", "cannabis", ["vpd_target"])


def test_the_house_needs_no_widths_and_a_zone_outside_the_list_is_refused():
    conn = FakeConnection([("fn_band_setpoints", lambda steps: [])])
    run(s.band(conn, NOW - timedelta(hours=1), NOW, now=NOW))
    assert not any("crop_band_anchors" in sql or "fn_zone_vpd_targets" in sql for sql, _ in conn.fetches)
    assert code_of(s.band(conn, NOW - timedelta(hours=1), NOW, "north", now=NOW)) == ("invalid_request", 400)


def test_zone_widths_take_the_current_seasons_row_and_stay_null_when_no_row_holds_one():
    row = {"width_below": None, "width_above": None, "current_season": "fall"}
    assert s.zone_widths([]) == (None, None)
    assert s.zone_widths([{**row, "season": "all"}]) == (None, None)
    rows = [
        {**row, "season": "all", "width_below": 0.18, "width_above": 0.22},
        {**row, "season": "fall", "width_below": 0.3},
    ]
    assert s.zone_widths(rows) == (0.3, 0.22)


def test_band_skips_the_rows_query_past_retention_and_still_reconstructs_the_crop_band():
    to = NOW - timedelta(days=100)
    conn = FakeConnection([("fn_band_setpoints", lambda steps: [])])
    result = run(s.band(conn, to - timedelta(days=1), to, now=NOW))
    assert all(value is None for value in result.band.temp_low)
    assert [sql for sql, _ in conn.fetches if "time_bucket_gapfill" in sql] == []
    assert any("fn_band_setpoints" in sql for sql, _ in conn.fetches)
    assert code_of(s.band(conn, NOW - timedelta(days=401), NOW, now=NOW)) == ("window_too_large", 422)


# ─────────────────────────────────────────────────────────────── routes and the committed contract
class FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        conn = self.conn

        class _Acquired:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _Acquired()


def route_module():
    api = str(ROOT / "api")
    if api not in sys.path:
        sys.path.append(api)
    return importlib.import_module("operator_routes.series")


def client(conn) -> TestClient:
    app = FastAPI()
    app.include_router(route_module().router, prefix="/v1")
    app.state.pool = FakePool(conn)

    @app.exception_handler(ServiceError)
    async def _problem(request, exc):
        problem = exc.problem
        return JSONResponse(
            problem.model_dump(mode="json", exclude_none=True),
            status_code=problem.status,
            media_type="application/problem+json",
        )

    return TestClient(app)


def test_routes_serve_the_committed_operations():
    app = FastAPI()
    app.include_router(route_module().router, prefix="/v1")
    served = {
        (path, method): operation["operationId"]
        for path, item in app.openapi()["paths"].items()
        for method, operation in item.items()
    }
    assert served == {
        ("/v1/metrics", "get"): "API-03",
        ("/v1/series", "get"): "API-04",
        ("/v1/states", "get"): "API-05",
        ("/v1/band", "get"): "API-06",
        ("/v1/band/preview", "get"): "API-19",
        ("/v1/lighting", "get"): "API-22",
        ("/v1/band/profile", "get"): "API-23",  # tests/test_band_profile.py
    }
    for (path, method), operation_id in served.items():
        assert OPENAPI["paths"][path][method]["operationId"] == operation_id


@pytest.mark.parametrize(
    ("model", "component"),
    [
        (s.MetricDef, "MetricDef"),
        (s.Series, "Series"),
        (s.SeriesColumns, "SeriesColumns"),
        (s.StateLanes, "StateLanes"),
        (s.BandTimeline, "BandTimeline"),
        (s.BandColumns, "BandColumns"),
        (s.BandPreview, "BandPreview"),
        (s.BandCurve, "BandCurve"),
    ],
)
def test_response_models_follow_the_committed_schemas(model, component):
    committed = OPENAPI["components"]["schemas"][component]
    schema = model.model_json_schema(by_alias=True, mode="serialization")
    assert set(schema["properties"]) <= set(committed["properties"])
    assert set(committed.get("required", [])) <= set(schema["properties"])


def test_lane_and_enum_values_stay_inside_the_committed_enums():
    lanes = OPENAPI["components"]["schemas"]["StateLanes"]["properties"]["lanes"]["items"]
    interval = lanes["properties"]["intervals"]["items"]["properties"]
    assert {"on", "off", "unknown"} == set(interval["state"]["enum"])
    assert {group for _, group in s.LANES} <= set(lanes["properties"]["group"]["enum"])
    columns = OPENAPI["components"]["schemas"]["SeriesColumns"]["properties"]
    assert {"raw", "daily_summary", "scorecard", "forecast"} <= set(columns["source"]["enum"])
    assert set(s.BUCKETS_S) == set(OPENAPI["components"]["schemas"]["Series"]["properties"]["bucket_s"]["enum"])
    provenance = set(OPENAPI["components"]["schemas"]["MetricDef"]["properties"]["provenance"]["enum"])
    assert {binding.provenance for binding in s.METRICS.values()} <= provenance


def test_routes_parse_queries_call_the_service_and_render_problems():
    conn = FakeConnection([('"climate"', []), ("data_gaps", [])])
    with client(conn) as http:
        metrics = http.get("/v1/metrics")
        assert metrics.status_code == 200 and metrics.json()["items"][0]["key"] == "climate.temp_avg"
        ok = http.get(
            "/v1/series",
            params={
                "metric": ["climate.temp_avg", "climate.rh_avg"],
                "from": "2026-11-01T00:00:00-06:00",
                "to": "2026-11-01T01:00:00-06:00",
            },
        )
        assert ok.status_code == 200
        body = ok.json()
        assert body["from"] == "2026-11-01T06:00:00Z" and body["bucket_s"] == 60 and len(body["t"]) == 60
        assert set(body["series"]) == {"climate.temp_avg", "climate.rh_avg"} and body["gaps"] == []
        unknown = http.get(
            "/v1/series",
            params={"metric": "climate.nope", "from": "2026-11-01T00:00:00Z", "to": "2026-11-01T01:00:00Z"},
        )
        assert unknown.status_code == 422 and unknown.json()["code"] == "unknown_target"
        assert unknown.headers["content-type"].startswith("application/problem+json")
        wide = http.get("/v1/states", params={"from": "2026-09-01T00:00:00Z", "to": "2026-11-01T00:00:00Z"})
        assert wide.status_code == 422 and wide.json()["code"] == "window_too_large"
        naive = http.get("/v1/band", params={"from": "2026-11-01T00:00:00", "to": "2026-11-01T01:00:00"})
        assert naive.status_code == 422  # FastAPI's own validation; the operator API maps it to 400
        window = {"from": "2026-11-01T00:00:00Z", "to": "2026-11-01T01:00:00Z"}
        assert http.get("/v1/band", params=window).status_code == 200
        assert http.get("/v1/band", params={**window, "zone": "south"}).status_code == 200
        assert http.get("/v1/band", params={**window, "zone": "north"}).status_code == 422  # not offered
