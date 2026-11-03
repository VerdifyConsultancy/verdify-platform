"""Graph reads on a production-sized fixture (VD-56): API-04 to API-06 and v_daily_scorecard.

Runs in the in-cluster campaign step on a private TimescaleDB (``VERDIFY_ISOLATED_TEST_DSN``,
jvallery/agents#5994), never on the Mac. It loads ``db/schema.sql``, replaces the unpopulated
``mv_zone_band_grade`` with a table it can fill, creates M6's view from the migration's own
text, then writes 31 days of climate and diagnostics at 60 s, equipment rows, band rows, a
recorded outage, 30 scorecard days and a week of daily summaries 380 days back.
"""

from __future__ import annotations

import asyncio
import math
import os
import re
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import asyncpg
import pytest

from verdify_services import series as s

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(
    not os.environ.get("VERDIFY_ISOLATED_TEST_DSN"),
    reason="needs the campaign's private TimescaleDB (VERDIFY_ISOLATED_TEST_DSN); never run against production",
)
SIX = [
    "climate.temp_avg",
    "climate.rh_avg",
    "climate.vpd_avg",
    "climate.co2_ppm",
    "climate.flow_gpm",
    "diagnostics.heap_free_kb",
]
DOLLAR = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$")


def run(coroutine):
    return asyncio.run(coroutine)


async def connect() -> asyncpg.Connection:
    from conftest import isolated_mutation_test_dsn

    return await asyncpg.connect(isolated_mutation_test_dsn())


def dump_statements(sql: str):
    """Split a pg_dump into statements: quotes, identifiers and dollar bodies keep their semicolons."""
    sql = re.sub(r"^\\[^\n]*$", "", sql, flags=re.MULTILINE)  # psql meta-commands (\restrict)
    start, index, size = 0, 0, len(sql)
    while index < size:
        char = sql[index]
        if char == "-" and sql.startswith("--", index):
            end = sql.find("\n", index)
            index = size if end < 0 else end
        elif char == "'":
            index += 1
            while index < size and not (sql[index] == "'" and not sql.startswith("''", index)):
                index += 2 if sql.startswith("''", index) else 1
            index += 1
        elif char == '"':
            index = sql.find('"', index + 1) + 1
        elif char == "$" and (match := DOLLAR.match(sql, index)):
            end = sql.find(match.group(0), match.end())
            index = size if end < 0 else end + len(match.group(0))
        elif char == ";":
            statement = sql[start : index + 1].strip()
            if statement.strip(";").strip():
                yield statement
            start = index = index + 1
        else:
            index += 1


def scorecard_view_sql() -> str:
    migration = (ROOT / "db/migrations/285-daily-scorecard-view.sql").read_text()
    body = migration.split("CREATE VIEW public.v_daily_scorecard AS", 1)[1].split(";\n", 1)[0]
    return "CREATE VIEW public.v_daily_scorecard AS" + body


@dataclass(frozen=True)
class Fixture:
    now: datetime
    today: date
    hole_start: datetime
    hole_end: datetime
    no_meter_day: date
    incomplete_day: date
    missing_day: date
    split_days: tuple[date, date]
    ledger_from: date
    missed_day: date
    alerts_from: date


async def _load_schema() -> None:
    conn = await connect()
    try:
        for statement in dump_statements((ROOT / "db/schema.sql").read_text()):
            try:
                await conn.execute(statement)
            except asyncpg.PostgresError:
                pass  # owners, grants and roles of the production estate do not exist here
    finally:
        await conn.close()


async def _fill(conn: asyncpg.Connection) -> Fixture:
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    today = now.astimezone(s.DENVER).date()
    start = now - timedelta(days=31)
    hole_start = now - timedelta(hours=6)
    hole_end = hole_start + timedelta(minutes=30)
    fixture = Fixture(
        now=now,
        today=today,
        hole_start=hole_start,
        hole_end=hole_end,
        no_meter_day=today - timedelta(days=5),
        incomplete_day=today - timedelta(days=7),
        missing_day=today - timedelta(days=10),
        split_days=(today - timedelta(days=2), today - timedelta(days=1)),
        ledger_from=today - timedelta(days=20),
        missed_day=today - timedelta(days=3),
        alerts_from=today - timedelta(days=15),
    )
    await conn.execute(
        "INSERT INTO public.greenhouses (id, name) VALUES ('vallery', 'Vallery') ON CONFLICT (id) DO NOTHING"
    )
    await conn.execute("DROP MATERIALIZED VIEW IF EXISTS public.mv_zone_band_grade CASCADE")
    await conn.execute(
        "CREATE TABLE public.mv_zone_band_grade (bucket timestamptz NOT NULL, zone text NOT NULL, "
        "n bigint, sum_zone_score numeric, PRIMARY KEY (bucket, zone))"
    )
    await conn.execute(scorecard_view_sql())
    # 31 days at 60 s with a 30-minute outage; solar_altitude_deg set so the solar trigger stays idle.
    await conn.execute(
        """INSERT INTO public.climate (ts, greenhouse_id, temp_avg, temp_north, rh_avg, vpd_avg, co2_ppm,
                                      flow_gpm, outdoor_temp_f, water_total_gal, solar_altitude_deg)
        SELECT ts, 'vallery',
               75 + 8 * sin(extract(epoch FROM ts) / 13751.0), 74 + 8 * sin(extract(epoch FROM ts) / 13751.0),
               60 + 10 * cos(extract(epoch FROM ts) / 13751.0), 1.0 + 0.3 * sin(extract(epoch FROM ts) / 13751.0),
               600 + 50 * sin(extract(epoch FROM ts) / 7000.0),
               CASE WHEN extract(minute FROM ts) < 5 THEN 1 ELSE 0 END,
               50 + 15 * sin(extract(epoch FROM ts) / 13751.0),
               CASE WHEN (ts AT TIME ZONE 'America/Denver')::date = $3::date THEN NULL
                    ELSE 1000 + floor(extract(epoch FROM ts - $1::timestamptz) / 3600.0) END,
               0
          FROM generate_series($1::timestamptz, $2::timestamptz - interval '60 seconds', interval '60 seconds') AS ts
         WHERE NOT (ts >= $4::timestamptz AND ts < $5::timestamptz)""",
        start,
        now,
        fixture.no_meter_day,
        hole_start,
        hole_end,
    )
    await conn.execute(
        """INSERT INTO public.diagnostics (ts, greenhouse_id, heap_bytes, heap_largest_free_block_kb, wifi_rssi, uptime_s)
        SELECT ts, 'vallery', 55 + (extract(epoch FROM ts)::bigint % 5), 30, -61, extract(epoch FROM ts - $1::timestamptz)
          FROM generate_series($1::timestamptz, $2::timestamptz - interval '60 seconds', interval '60 seconds') AS ts
         WHERE NOT (ts >= $3::timestamptz AND ts < $4::timestamptz)""",
        start,
        now,
        hole_start,
        hole_end,
    )
    await conn.execute(
        """INSERT INTO public.equipment_state (ts, equipment, state, greenhouse_id)
        SELECT ts, 'fan1', extract(hour FROM ts)::int % 2 = 0, 'vallery'
          FROM generate_series($1::timestamptz, $2::timestamptz - interval '1 hour', interval '1 hour') AS ts
        UNION ALL
        SELECT ts, light.name, extract(hour FROM ts AT TIME ZONE 'America/Denver') BETWEEN 6 AND 20, 'vallery'
          FROM generate_series($1::timestamptz, $2::timestamptz - interval '5 minutes', interval '5 minutes') AS ts
         CROSS JOIN (VALUES ('grow_light_main'), ('grow_light_grow')) AS light(name)
        UNION ALL
        SELECT ts + offset_minutes * interval '1 minute', 'drip_wall', offset_minutes = 0, 'vallery'
          FROM generate_series($1::timestamptz, $2::timestamptz - interval '6 hours', interval '6 hours') AS ts
         CROSS JOIN (VALUES (0), (12)) AS run(offset_minutes)""",
        start,
        now,
    )
    # The dispatcher's house band rows every pass, plus the 2026-11-01 passes of the acceptance.
    await conn.execute(
        """INSERT INTO public.setpoint_snapshot (ts, parameter, value, greenhouse_id, zone, band_role, target_value)
        SELECT ts + interval '17 seconds', 'band_house_' || role.name, role.base, 'vallery', 'house', role.name, role.base
          FROM generate_series($1::timestamptz, $2::timestamptz - interval '300 seconds', interval '300 seconds') AS ts
         CROSS JOIN (VALUES ('temp_low', 70.0), ('temp_high', 80.0), ('vpd_low', 0.8), ('vpd_high', 1.2))
               AS role(name, base)
         WHERE NOT (ts >= '2026-11-01 20:00+00' AND ts < '2026-11-02 00:00+00')
        UNION ALL
        SELECT pass.ts, 'band_house_' || pass.role, pass.value, 'vallery', 'house', pass.role, pass.value
          FROM (VALUES ('2026-11-01 21:44:19.081524+00'::timestamptz, 'temp_low', 74.98),
                       ('2026-11-01 21:44:19.081524+00', 'temp_high', 84.98),
                       ('2026-11-01 21:44:19.081524+00', 'vpd_low', 0.873),
                       ('2026-11-01 21:44:19.081524+00', 'vpd_high', 1.303),
                       ('2026-11-01 21:49:16.356363+00', 'temp_low', 74.92),
                       ('2026-11-01 21:49:16.356363+00', 'temp_high', 84.92),
                       ('2026-11-01 21:49:16.356363+00', 'vpd_low', 0.873),
                       ('2026-11-01 21:49:16.356363+00', 'vpd_high', 1.303),
                       ('2026-11-01 21:54:20.441848+00', 'temp_low', 74.85),
                       ('2026-11-01 21:54:20.441848+00', 'temp_high', 84.85),
                       ('2026-11-01 21:54:20.441848+00', 'vpd_low', 0.872),
                       ('2026-11-01 21:54:20.441848+00', 'vpd_high', 1.302)) AS pass(ts, role, value)""",
        start,
        now,
    )
    await conn.execute(
        "INSERT INTO public.data_gaps (start_ts, end_ts, duration_s, reason) VALUES ($1, $2, 1800, 'ingestor_restart')",
        hole_start,
        hole_end,
    )
    # 30 scorecard days, one of them missing; gallons null today, on a no-meter day and on a partial meter day.
    for back in range(30):
        day = today - timedelta(days=back)
        if day == fixture.missing_day:
            continue
        gallons = None if day in (today, fixture.no_meter_day, fixture.incomplete_day) else 10.0
        await conn.execute(
            """INSERT INTO public.daily_summary (date, greenhouse_id, temp_compliance_pct, vpd_compliance_pct,
                                                 water_used_gal, temp_avg, captured_at)
            VALUES ($1, 'vallery', $2, $3, $4, 70, $5)""",
            day,
            30.0 + back,
            60.0 + back,
            gallons,
            now,
        )
    for back in range(381, 388):
        await conn.execute(
            """INSERT INTO public.daily_summary (date, greenhouse_id, temp_avg, temp_min, temp_max, captured_at)
            VALUES ($1, 'vallery', $2, 55, 80, $3)""",
            today - timedelta(days=back),
            60.0 + (387 - back),
            now - timedelta(days=back),
        )
    # The band grade covers the two whole days before today: 80 % by day, 50 % by night.
    await conn.execute(
        """INSERT INTO public.mv_zone_band_grade (bucket, zone, n, sum_zone_score)
        SELECT bucket, zone.name, 60,
               CASE WHEN public.fn_solar_phase(bucket + interval '30 minutes') < 2 THEN 48 ELSE 30 END
          FROM generate_series($1::timestamptz, $2::timestamptz, interval '1 hour') AS bucket
         CROSS JOIN (VALUES ('center'), ('north')) AS zone(name)""",
        s.denver_midnight(fixture.split_days[0]),
        now.replace(minute=0) - timedelta(hours=1),
    )
    for back in range(21):
        day = today - timedelta(days=back)
        midnight = s.denver_midnight(day)
        for event, hours in (("MIDNIGHT", 1), ("SUNRISE", 7), ("SUNSET", 18)):
            status, action = "plan_written", "set_plan"
            if day == fixture.missed_day and event == "SUNRISE":
                status, action = "acked", "acknowledge_trigger"
            if day == fixture.missed_day and event == "SUNSET":
                status, action = "missed", "timeout"
            due = midnight + timedelta(hours=hours)
            await conn.execute(
                """INSERT INTO public.planner_trigger_ledger (greenhouse_id, event_type, expected_at, due_at, status,
                                                              expected_action, terminal_action, terminal_at)
                VALUES ('vallery', $1, $2, $3, $4, 'set_plan', $5, $3)""",
                event,
                due - timedelta(minutes=30),
                due,
                status,
                action,
            )
    for back, severity, count in ((15, "warning", 1), (12, "critical", 1), (4, "warning", 2), (1, "info", 1)):
        ts = s.denver_midnight(today - timedelta(days=back)) + timedelta(hours=12)
        for _ in range(count):
            await conn.execute(
                """INSERT INTO public.alert_log (ts, created_at, alert_type, severity, message, greenhouse_id)
                VALUES ($1, $1, 'fixture', $2, 'fixture alert', 'vallery')""",
                ts,
                severity,
            )
    await conn.execute("ANALYZE")
    return fixture


async def _prepare() -> Fixture:
    await _load_schema()
    conn = await connect()
    try:
        return await _fill(conn)
    finally:
        await conn.close()


@pytest.fixture(scope="module")
def db() -> Fixture:
    return run(_prepare())


async def _with_connection(work):
    conn = await connect()
    try:
        return await work(conn)
    finally:
        await conn.close()


def test_a_thirty_day_six_metric_read_answers_under_two_seconds_at_p95(db):
    async def work(conn):
        timings, result = [], None
        for _ in range(20):
            started = time.perf_counter()
            result = await s.series(conn, SIX, db.now - timedelta(days=30), db.now, now=db.now)
            timings.append(time.perf_counter() - started)
        return sorted(timings), result

    timings, result = run(_with_connection(work))
    assert timings[math.ceil(0.95 * len(timings)) - 1] < 2.0, timings
    assert result.bucket_s == 3600 and set(result.series) == set(SIX)
    temp = result.series["climate.temp_avg"]
    assert temp.source == "raw" and sum(temp.count) > 40000
    assert all(value is None for value, count in zip(temp.avg, temp.count, strict=True) if count == 0)
    assert result.watermark is not None and result.watermark <= db.now


def test_the_outage_is_a_gap_with_its_recorded_reason(db):
    result = run(
        _with_connection(
            lambda conn: s.series(
                conn,
                ["climate.temp_avg"],
                db.hole_start - timedelta(hours=1),
                db.hole_end + timedelta(hours=1),
                now=db.now,
            )
        )
    )
    assert result.bucket_s == 60
    assert [(gap.start, gap.end, gap.reason) for gap in result.gaps] == [
        (db.hole_start, db.hole_end, "ingestor_restart")
    ]
    temp = result.series["climate.temp_avg"]
    inside = [i for i, t in enumerate(result.t) if db.hole_start.timestamp() <= t < db.hole_end.timestamp()]
    assert len(inside) == 30
    assert all(temp.avg[i] is None and temp.count[i] == 0 for i in inside)
    assert all(temp.count[i] == 1 for i in range(len(result.t)) if i not in inside)


def test_a_week_ending_380_days_ago_reads_daily_summary_and_marks_raw_retention(db):
    to = s.denver_midnight(db.today - timedelta(days=380))
    from_ = s.denver_midnight(db.today - timedelta(days=387))
    result = run(
        _with_connection(
            lambda conn: s.series(conn, ["climate.temp_avg", "diagnostics.heap_free_kb"], from_, to, now=db.now)
        )
    )
    temp, heap = result.series["climate.temp_avg"], result.series["diagnostics.heap_free_kb"]
    assert result.bucket_s == 86400 and len(result.t) == 7
    assert temp.source == "daily_summary" and temp.avg == [60.0 + n for n in range(7)]
    assert temp.min == [55.0] * 7 and temp.max == [80.0] * 7 and temp.count == [1] * 7
    assert heap.source == "raw" and heap.retained_from == db.now - timedelta(days=180)
    assert heap.avg == [None] * 7 and heap.count == [0] * 7  # beyond retention, not gaps


def test_states_cover_a_day_with_unknown_spans_only_where_the_controller_was_silent(db):
    from_, to = db.now - timedelta(hours=24), db.now
    lanes = run(_with_connection(lambda conn: s.states(conn, from_, to, now=db.now))).lanes
    assert len(lanes) == 21
    by_name = {lane.equipment: lane for lane in lanes}
    fan = [(i.state, i.start, i.end) for i in by_name["fan1"].intervals]
    assert {"on", "off"} <= {state for state, _, _ in fan}
    silent = (db.hole_start - timedelta(minutes=1) + timedelta(seconds=300), db.hole_end)
    assert ("unknown", *silent) in fan
    assert fan[0][1] == from_ and fan[-1][2] == to
    for light in ("grow_light_main", "grow_light_grow"):
        assert all(i.state != "unknown" for i in by_name[light].intervals)
    drip = by_name["drip_wall"]
    assert drip.line_id == "L01" and drip.label == "Wall drip L01"
    assert {i.state for i in drip.intervals} >= {"on", "off"} and all(i.cause is None for i in drip.intervals)
    for lane in lanes:
        assert all(a.end == b.start for a, b in zip(lane.intervals, lane.intervals[1:], strict=False))


def test_the_2026_11_01_band_reads_74_92_to_84_92(db):
    from_, to = datetime(2026, 11, 1, 21, 0, tzinfo=UTC), datetime(2026, 11, 1, 23, 0, tzinfo=UTC)
    measured_at = datetime(2026, 11, 2, 18, 0, tzinfo=UTC)
    result = run(_with_connection(lambda conn: s.band(conn, from_, to, now=measured_at)))
    assert result.step_s == 300 and len(result.t) == 24
    at = {t: i for i, t in enumerate(result.t)}
    index = at[int(datetime(2026, 11, 1, 21, 45, tzinfo=UTC).timestamp())]
    assert (result.band.temp_low[index], result.band.temp_high[index]) == (74.92, 84.92)
    assert (result.band.vpd_low[index], result.band.vpd_high[index]) == (0.873, 1.303)
    after = [result.band.temp_high[at[int(datetime(2026, 11, 1, 21, m, tzinfo=UTC).timestamp())]] for m in (50, 55)]
    assert after == [84.85, 84.85]  # 21:55 carries the 21:54:20 pass
    assert result.band.temp_high[at[int(datetime(2026, 11, 1, 22, 5, tzinfo=UTC).timestamp())]] is None
    assert len(result.crop.temp_low) == len(result.t)


def test_the_scorecard_returns_every_day_with_its_reasons_inside_the_timeout(db):
    from_ = s.denver_midnight(db.today - timedelta(days=29))
    to = s.denver_midnight(db.today + timedelta(days=1))
    keys = [key for key in s.METRICS if key.startswith("daily.")]

    async def work(conn):
        started = time.perf_counter()
        first = await s.series(conn, keys[:8], from_, to, points=31, now=db.now)
        second = await s.series(conn, keys[8:], from_, to, points=31, now=db.now)
        return time.perf_counter() - started, {**first.series, **second.series}, first.t

    elapsed, columns, axis = run(_with_connection(work))
    assert elapsed < 2.0
    days = [datetime.fromtimestamp(t, UTC).astimezone(s.DENVER).date() for t in axis]
    assert len(days) == 30 and days[-1] == db.today
    column = {key: dict(zip(days, range(len(days)), strict=True)) for key in columns}

    def cell(key, day):
        position = column[key][day]
        reason = columns[key].reason[position] if columns[key].reason is not None else None
        return columns[key].avg[position], columns[key].count[position], reason

    for day in db.split_days:
        assert cell("daily.time_in_band_day_pct", day) == (80.0, 1, None)
        assert cell("daily.time_in_band_night_pct", day) == (50.0, 1, None)
    for day in (db.today - timedelta(days=5), db.today):
        assert cell("daily.time_in_band_day_pct", day) == (None, 0, "band grade kept 3 days")
    assert cell("daily.water_gal", db.no_meter_day) == (None, 0, "unavailable")
    assert cell("daily.water_gal", db.incomplete_day) == (None, 0, "incomplete")
    assert cell("daily.water_gal", db.today) == (None, 0, "partial_day")
    assert cell("daily.water_gal", db.today - timedelta(days=2)) == (10.0, 1, None)
    for key in keys:  # a day with no summary row is a gap with no reason
        assert cell(key, db.missing_day) == (None, 0, None)
    assert cell("daily.temp_in_band_pct", db.today - timedelta(days=3)) == (33.0, 1, None)
    assert cell("daily.vpd_in_band_pct", db.today - timedelta(days=3)) == (63.0, 1, None)
    assert cell("daily.iris_required_written", db.missed_day)[0] == 1.0
    assert cell("daily.iris_required_missed", db.missed_day)[0] == 2.0
    assert cell("daily.iris_required_written", db.today - timedelta(days=4))[0] == 3.0
    assert cell("daily.iris_required_missed", db.today - timedelta(days=4))[0] == 0.0
    assert cell("daily.iris_required_written", db.ledger_from - timedelta(days=1)) == (None, 0, None)
    assert cell("daily.alerts_critical", db.today - timedelta(days=12))[0] == 1.0
    assert cell("daily.alerts_warning", db.today - timedelta(days=4))[0] == 2.0
    assert cell("daily.alerts_info", db.today - timedelta(days=4))[0] == 0.0
    assert cell("daily.alerts_warning", db.alerts_from - timedelta(days=1)) == (None, 0, None)


def test_registry_columns_exist_in_the_database(db):
    async def work(conn):
        rows = await conn.fetch(
            "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'public' "
            "AND table_name IN ('climate', 'diagnostics', 'daily_summary', 'v_daily_scorecard')"
        )
        return {(row["table_name"], row["column_name"]) for row in rows}

    columns = run(_with_connection(work))
    for binding in s.METRICS.values():
        if binding.raw is not None:
            assert (binding.raw.table, binding.raw.column) in columns, binding.key
        if binding.daily is not None:
            for column in (binding.daily.avg, binding.daily.min, binding.daily.max, binding.daily.reason):
                if column:
                    assert (binding.daily.view, column) in columns, binding.key
