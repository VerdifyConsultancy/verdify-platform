"""The Next screens' reads and the grow-light row as the dashboard login (VD-57, #1170), on a private database.

jvallery/agents#5994's campaign step runs each tests/test_dashboard_db_*.py file against a fresh loopback PostgreSQL with
TimescaleDB and hands it over in VERDIFY_ISOLATED_TEST_DSN; without one this file skips. test_dashboard_db_284's
``install`` builds the database and runs migration 284's body, so every read below runs as
``verdify_dashboard_runtime_login`` under exactly M2's grants, and a grant these reads need and M2 lacks fails here.

Covers what needs PostgreSQL: API-06 with ``zone=south``, API-19's ``current`` against ``fn_crop_band_value`` at every
step, API-21's no-valve gallons and its gaps, the forecast's newest issue and ``issued_at``, API-13's reasons, and
API-20's activity row. The fixture replaces 284's sample rows with its own, placed relative to the server's now().
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pytest
from test_dashboard_db_284 import DSN, LOGIN, connect, install, run

from verdify_services import activity, lights, reads
from verdify_services import series as s
from verdify_services.actor import Actor
from verdify_services.problems import ServiceError

pytestmark = pytest.mark.skipif(DSN is None, reason="needs VERDIFY_ISOLATED_TEST_DSN (campaign private PostgreSQL)")

SEASONS = ("spring", "summer", "fall", "winter")
HOUSE = {
    "temp_low": (60.0, 72.0, 66.0, 58.0),
    "temp_target": (65.0, 77.0, 71.0, 63.0),
    "temp_high": (70.0, 82.0, 76.0, 68.0),
    "vpd_low": (0.6, 0.9, 0.8, 0.5),
    "vpd_target": (0.8, 1.2, 1.0, 0.7),
    "vpd_high": (1.0, 1.5, 1.3, 0.9),
}
ZONE_CROPS = {
    "cannabis": ((0.9, 1.3, 1.1, 0.8), 0.18, 0.22),
    "orchid": ((0.6, 0.9, 0.8, 0.5), 0.2, 0.35),
    "citrus": ((0.8, 1.2, 1.0, 0.7), 0.16, 0.22),
    "pepper": ((0.7, 1.1, 0.9, 0.6), 0.15, 0.23),
}
ANCHORS = ("sr", "sm", "ss", "mid")
INSERT_ANCHOR = """
INSERT INTO public.crop_band_anchors (crop_type, series, anchor, value, width_below, width_above, season, growth_stage)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
"""
INSERT_EVENT = """
INSERT INTO public.water_meter_events (ts, prior_ts, event_type, delta_gal, quality_flag, attribution_class,
                                       attributed_scope, attribution_quality)
VALUES ($1, $2, 'delta', $3, 'ok', $4, $5, 'ok')
"""
CLEARED = (
    "public.crop_band_anchors",
    "public.climate",
    "public.equipment_state",
    "public.system_state",
    "public.setpoint_snapshot",
    "public.water_meter_events",
    "public.water_meter_materializer_state",
    "public.weather_forecast",
)


@dataclass(frozen=True)
class World:
    now: datetime
    today: date
    hour: datetime
    complete: date
    unavailable: date
    absent: date
    partial: date
    newest_issue: datetime
    older_issue: datetime


def day_hours(day: date) -> float:
    return (s.denver_midnight(day + timedelta(days=1)) - s.denver_midnight(day)).total_seconds() / 3600


async def _fill_anchors(conn) -> None:
    current = await conn.fetchval("SELECT public.fn_current_season()")
    other = next(season for season in SEASONS if season != current)
    rows = [
        ("house", series, anchor, value, None, None, "all", "default")
        for series, values in HOUSE.items()
        for anchor, value in zip(ANCHORS, values, strict=True)
    ]
    for crop, (values, below, above) in ZONE_CROPS.items():
        rows += [
            (crop, "vpd_target", anchor, value, below, above, "all", "default")
            for anchor, value in zip(ANCHORS, values, strict=True)
        ]
        rows += [(crop, "temp_target", anchor, 70.0, None, None, "all", "default") for anchor in ANCHORS]
    # What fn_crop_band_value resolves past: the current season beats all; another season and another stage are not read.
    rows += [
        ("house", "temp_low", "sm", 74.0, None, None, current, "default"),
        ("house", "temp_low", "sr", 11.0, None, None, other, "default"),
        ("house", "temp_low", "ss", 22.0, None, None, "all", "flowering"),
        ("cannabis", "vpd_target", "sm", 1.4, 0.18, 0.22, current, "default"),
    ]
    await conn.executemany(INSERT_ANCHOR, rows)


async def _fill_band(conn, now: datetime) -> None:
    await conn.execute(
        """INSERT INTO public.setpoint_snapshot (ts, parameter, value, zone, band_role)
        SELECT ts, 'band_' || zone.name || '_' || role.name, role.base + zone.shift, zone.name, role.name
          FROM generate_series($1::timestamptz, $2::timestamptz, interval '5 minutes') AS ts
         CROSS JOIN (VALUES ('temp_low', 60.0::double precision), ('temp_high', 70.0::double precision),
                            ('vpd_low', 0.5::double precision), ('vpd_high', 1.0::double precision)) AS role(name, base)
         CROSS JOIN (VALUES ('house', 10.0::double precision), ('south', 0.0::double precision)) AS zone(name, shift)""",
        now - timedelta(hours=2),
        now,
    )


async def _fill_forecast(conn, hour: datetime, newest: datetime, older: datetime) -> None:
    # The older issue covers hours +1 to +3 and the newest only +1 and +2.
    await conn.executemany(
        "INSERT INTO public.weather_forecast (ts, fetched_at, temp_f, rh_pct, vpd_kpa) VALUES ($1, $2, $3, $4, $5)",
        [
            (hour + timedelta(hours=1), older, 40.0, 30.0, 0.4),
            (hour + timedelta(hours=2), older, 41.0, 31.0, 0.5),
            (hour + timedelta(hours=3), older, 42.0, 32.0, 0.6),
            (hour + timedelta(hours=1), newest, 50.0, 40.0, 0.7),
            (hour + timedelta(hours=2), newest, 51.0, 41.0, 0.8),
        ],
    )


async def _fill_water(conn, world: World) -> None:
    # A complete day: 5-minute meter samples, the ledger past them, and two deltas, one of them in a wall drip run.
    start = s.denver_midnight(world.complete)
    end = s.denver_midnight(world.complete + timedelta(days=1))
    await conn.execute(
        """INSERT INTO public.climate (ts, greenhouse_id, water_total_gal)
        SELECT ts, 'vallery', 100 FROM generate_series($1::timestamptz, $2::timestamptz, interval '5 minutes') AS ts""",
        start,
        end - timedelta(minutes=5),
    )
    run_start = start + timedelta(hours=10)
    await conn.executemany(
        "INSERT INTO public.equipment_state (ts, equipment, state, greenhouse_id) VALUES ($1, 'drip_wall', $2, 'vallery')",
        [(run_start, True), (run_start + timedelta(minutes=10), False)],
    )
    manual = start + timedelta(hours=8)
    await conn.execute(INSERT_EVENT, manual, manual - timedelta(minutes=5), 5.0, "manual_or_unattributed", None)
    await conn.execute(
        INSERT_EVENT, run_start + timedelta(minutes=5), run_start, 12.0, "meter_attributed", "wall_irrigation"
    )
    # A day with meter events and no raw samples: the views read it unavailable and coalesce its gallons.
    hose = s.denver_midnight(world.unavailable) + timedelta(hours=9)
    await conn.execute(INSERT_EVENT, hose, hose - timedelta(minutes=5), 3.0, "manual_or_unattributed", None)
    # A day with hourly samples only: discontinuous, so partial, with no run. Hour 22 is inside even a 23-hour day.
    start = s.denver_midnight(world.partial)
    await conn.execute(
        """INSERT INTO public.climate (ts, greenhouse_id, water_total_gal)
        SELECT ts, 'vallery', 100 FROM generate_series($1::timestamptz, $2::timestamptz, interval '1 hour') AS ts""",
        start,
        start + timedelta(hours=22),
    )
    leak = start + timedelta(hours=9)
    await conn.execute(INSERT_EVENT, leak, leak - timedelta(hours=1), 7.0, "manual_or_unattributed", None)
    # Today: a sample at now(), so today is a partial day with nothing used yet.
    await conn.execute(
        "INSERT INTO public.climate (ts, greenhouse_id, water_total_gal) VALUES ($1, 'vallery', 100)", world.now
    )
    await conn.execute(
        "INSERT INTO public.water_meter_materializer_state (greenhouse_id, last_source_ts) VALUES ('vallery', $1)",
        world.now,
    )


async def _prepare() -> World:
    await install()
    conn = await connect()
    try:
        for table in CLEARED:
            await conn.execute(f"DELETE FROM {table}")
        now = await conn.fetchval("SELECT now()")
        today = now.astimezone(s.DENVER).date()
        complete = today - timedelta(days=1)
        if day_hours(complete) < 24:  # the view measures a day's end as day + 1 day, past the 23-hour day's last sample
            complete -= timedelta(days=1)
        world = World(
            now=now,
            today=today,
            hour=now.replace(minute=0, second=0, microsecond=0),
            complete=complete,
            unavailable=complete - timedelta(days=1),
            absent=complete - timedelta(days=2),
            partial=complete - timedelta(days=3),
            newest_issue=now - timedelta(hours=1),
            older_issue=now - timedelta(hours=2),
        )
        await _fill_anchors(conn)
        await _fill_band(conn, now)
        await _fill_forecast(conn, world.hour, world.newest_issue, world.older_issue)
        await _fill_water(conn, world)
        await conn.execute("ANALYZE")
        return world
    finally:
        await conn.close()


@pytest.fixture(scope="module")
def world() -> World:
    return run(_prepare())


def as_login(work):
    """``work(conn)`` on a connection of the dashboard login, which holds only what M2 grants."""

    async def session():
        conn = await connect(LOGIN)
        try:
            return await work(conn)
        finally:
            await conn.close()

    return run(session())


# ── API-06: zone=south ──────────────────────────────────────────────────────


def test_zone_south_reads_south_band_rows_and_its_crop_band(world):
    from_, to = world.now - timedelta(hours=1), world.now

    async def work(conn):
        south = await s.band(conn, from_, to, "south", now=world.now)
        house = await s.band(conn, from_, to, now=world.now)
        steps = [datetime.fromtimestamp(t, world.now.tzinfo) for t in south.t]
        rows = await conn.fetch(
            """SELECT b.temp_low, b.temp_high, z.vpd_target_south AS target
                 FROM unnest($1::timestamptz[]) WITH ORDINALITY AS step(ts, ord)
                CROSS JOIN LATERAL public.fn_band_setpoints(step.ts) AS b
                CROSS JOIN LATERAL public.fn_zone_vpd_targets(step.ts) AS z
                ORDER BY step.ord""",
            steps,
        )
        return south, house, rows

    south, house, rows = as_login(work)
    assert south.step_s == 300 and len(south.t) >= 12
    assert {value for value in south.band.temp_low if value is not None} == {60.0}
    assert {value for value in south.band.vpd_high if value is not None} == {1.0}
    assert {value for value in house.band.temp_low if value is not None} == {70.0}
    assert south.band.temp_low[0] is not None
    for index, row in enumerate(rows):
        assert south.crop.temp_low[index] == pytest.approx(row["temp_low"])
        assert south.crop.temp_high[index] == pytest.approx(row["temp_high"])
        assert south.crop.vpd_low[index] == pytest.approx(row["target"] - 0.18)
        assert south.crop.vpd_high[index] == pytest.approx(row["target"] + 0.22)
    assert south.crop.temp_low == house.crop.temp_low  # one air mass


# ── API-19: current against fn_crop_band_value at every step ────────────────


async def _function_values(conn, crop: str, series: str, t: list[int], tz) -> list[float | None]:
    steps = [datetime.fromtimestamp(value, tz) for value in t]
    rows = await conn.fetch(
        """SELECT public.fn_crop_band_value($1::text, $2::text, step.ts) AS value
             FROM unnest($3::timestamptz[]) WITH ORDINALITY AS step(ts, ord) ORDER BY step.ord""",
        crop,
        series,
        steps,
    )
    return [row["value"] for row in rows]


def test_a_house_previews_current_equals_fn_crop_band_value_at_every_step(world):
    async def work(conn):
        result = await s.band_preview(conn, "band_temp_low_sm", 80.0, world.today, now=world.now)
        expected = {
            name: await _function_values(conn, "house", f"temp_{name}", result.t, world.now.tzinfo)
            for name in ("low", "target", "high")
        }
        return result, expected

    result, expected = as_login(work)
    assert result.unit == "°F" and result.step_s == 600 and result.day == world.today
    assert len(result.t) == day_hours(world.today) * 6
    assert result.t[0] == int(s.denver_midnight(world.today).timestamp())
    assert result.current.low == pytest.approx(expected["low"], abs=1e-9)
    assert result.current.target == pytest.approx(expected["target"], abs=1e-9)
    assert result.current.high == pytest.approx(expected["high"], abs=1e-9)
    # The draft moves its own series around solar noon and nothing else.
    assert max(result.draft.low) > max(result.current.low)
    assert result.draft.target == result.current.target and result.draft.high == result.current.high


def test_a_zone_previews_current_is_the_function_and_its_widths_and_the_draft_a_width_moves_one_side(world):
    async def work(conn):
        target = await s.band_preview(conn, "zone_vpd_target_south_sm", 1.6, world.today, now=world.now)
        width = await s.band_preview(conn, "zone_vpd_width_below_south", 0.3, world.today, now=world.now)
        return target, width, await _function_values(conn, "cannabis", "vpd_target", target.t, world.now.tzinfo)

    target, width, function = as_login(work)
    assert target.unit == "kPa"
    assert target.current.target == pytest.approx(function, abs=1e-9)
    assert target.current.low == pytest.approx([value - 0.18 for value in function], abs=1e-9)
    assert target.current.high == pytest.approx([value + 0.22 for value in function], abs=1e-9)
    assert width.draft.target == width.current.target and width.draft.high == width.current.high
    assert width.draft.low == pytest.approx([value - 0.3 for value in function], abs=1e-9)


def test_a_preview_writes_nothing(world):
    async def count(conn):
        return await conn.fetchval("SELECT count(*) FROM public.crop_band_anchors WHERE value = 80")

    assert as_login(count) == 0


# ── API-21: the no-valve gallons, and gaps as null ──────────────────────────


def test_water_use_returns_the_no_valve_gallons_apart_and_missing_days_as_gaps(world):
    result = as_login(lambda conn: reads.water_daily(conn, days=90))
    assert (result.from_, result.to) == (world.today - timedelta(days=89), world.today)
    assert [day.date for day in result.days] == [result.from_ + timedelta(days=n) for n in range(90)]
    days = {day.date: day for day in result.days}
    complete = days[world.complete]
    assert (complete.coverage, complete.meter_gal, complete.no_valve_gal) == ("complete", 17, 5)
    assert (complete.by_line, complete.by_cause) == ({"L01": 12}, {"unknown": 12})
    assert complete.by_purpose == {"irrigation": 12}  # #1348: L01's unknown-cause run waters plants
    nothing = {
        "coverage": "no_data",
        "meter_gal": None,
        "by_line": None,
        "by_cause": None,
        "by_purpose": None,
        "no_valve_gal": None,
    }
    assert days[world.unavailable].model_dump(exclude={"date"}) == nothing  # the view reads 3 gal, unavailable
    assert days[world.absent].model_dump(exclude={"date"}) == nothing  # no row
    partial = days[world.partial]
    assert (partial.coverage, partial.meter_gal, partial.no_valve_gal) == ("partial", 7, 7)
    assert (partial.by_line, partial.by_cause) == ({}, {})  # data, and no run
    assert days[world.today].coverage == "partial" and days[world.today].meter_gal == 0
    assert days[result.from_].coverage == "no_data"  # before the first sample


def test_the_thirty_day_read_ends_today_too(world):
    result = as_login(lambda conn: reads.water_daily(conn))
    assert len(result.days) == 30 and result.days[-1].date == world.today


# ── API-04: the forecast's newest issue and issued_at ───────────────────────


def test_a_forecast_key_reads_the_newest_issue_per_hour_with_its_issue_time(world):
    from_, to = world.hour + timedelta(hours=1), world.hour + timedelta(hours=4)

    async def work(conn):
        return await s.series(conn, ["forecast.temp_f", "forecast.vpd_kpa"], from_, to, points=10, now=world.now)

    result = as_login(work)
    assert result.bucket_s == 3600 and len(result.t) == 3
    temp, vpd = result.series["forecast.temp_f"], result.series["forecast.vpd_kpa"]
    assert temp.source == vpd.source == "forecast" and temp.retained_from is None
    assert temp.avg == [50.0, 51.0, 42.0] and temp.count == [1, 1, 1]  # +3 is only in the older issue
    assert vpd.avg == pytest.approx([0.7, 0.8, 0.6])
    assert temp.issued_at == world.newest_issue
    assert result.watermark is None


def test_issued_at_is_the_newest_issue_the_window_used_and_null_with_no_row(world):
    async def work(conn):
        older = await s.series(
            conn,
            ["forecast.rh_pct"],
            world.hour + timedelta(hours=3),
            world.hour + timedelta(hours=4),
            points=10,
            now=world.now,
        )
        empty = await s.series(
            conn,
            ["forecast.rh_pct"],
            world.hour + timedelta(days=30),
            world.hour + timedelta(days=30, hours=1),
            points=10,
            now=world.now,
        )
        return older, empty

    older, empty = as_login(work)
    assert older.series["forecast.rh_pct"].issued_at == world.older_issue
    assert empty.series["forecast.rh_pct"].issued_at is None
    assert empty.series["forecast.rh_pct"].avg == [None] * len(empty.t)


# ── API-13: the reasons ─────────────────────────────────────────────────────


async def _reason_rows(conn, now: datetime) -> None:
    await conn.execute("DELETE FROM public.system_state")
    await conn.executemany(
        "INSERT INTO public.system_state (ts, entity, value) VALUES ($1, $2, $3)",
        [
            (now, "ingestor_heartbeat", "alive"),
            (now - timedelta(hours=3), "mode_reason", "seal_enter"),
            (now - timedelta(hours=2), "mode_reason", "temp_high"),
            (now - timedelta(hours=1), "mode_reason", "temp_high"),
            (now - timedelta(hours=1), "fog_block_reason", "none"),
        ],
    )
    await conn.execute("INSERT INTO public.climate (ts, greenhouse_id, temp_avg) VALUES ($1, 'vallery', 70)", now)


def test_the_reasons_read_their_newest_value_and_when_it_began(world):
    async def fill():
        conn = await connect()
        try:
            now = await conn.fetchval("SELECT now()")
            await _reason_rows(conn, now)
            return now
        finally:
            await conn.close()

    now = run(fill())
    result = as_login(reads.equipment)
    assert result.mode_reason.code == "temp_high"
    assert result.mode_reason.since == now - timedelta(hours=2)
    assert result.mode_reason.observed_at == now - timedelta(hours=1)
    assert (result.fog_block_reason.code, result.fog_block_reason.since) == ("none", None)
    assert result.fog_block_reason.observed_at == now - timedelta(hours=1)
    assert result.moisture_block_reason.model_dump() == {"code": None, "since": None, "observed_at": None}


def test_while_the_controller_is_silent_a_reason_keeps_only_its_newest_time(world):
    async def silent():
        conn = await connect()
        transaction = conn.transaction()
        await transaction.start()
        try:
            now = await conn.fetchval("SELECT now()")
            await _reason_rows(conn, now)
            await conn.execute("DELETE FROM public.system_state WHERE entity = 'ingestor_heartbeat'")
            await conn.execute(f"SET LOCAL ROLE {LOGIN}")
            return now, await reads.equipment(conn)
        finally:
            await transaction.rollback()
            await conn.close()

    now, result = run(silent())
    assert result.mode_reason.model_dump() == {
        "code": None,
        "since": None,
        "observed_at": now - timedelta(hours=1),
    }


# ── API-20: the activity row ────────────────────────────────────────────────


def test_a_switch_is_one_activity_row_the_activity_log_shows_and_its_key_replays(world):
    jason = Actor.person("jason", "operator")
    key = uuid.uuid4()

    async def work(conn):
        assert await lights.stored(conn, jason, "main", "on", "why", key) is None
        written = await lights.record(conn, jason, "main", "on", "confirmed", "why", key)
        replay = await lights.stored(conn, jason, "main", "on", "why", key)
        with pytest.raises(ServiceError) as reused:
            await lights.stored(conn, jason, "main", "off", "why", key)
        failed = await lights.record(conn, jason, "main", "off", "timeout")
        page = await activity.list(conn, target="grow_light_main", action="light.")
        return written, replay, reused.value.problem.code, failed, page

    written, replay, reused, failed, page = as_login(work)
    assert (written.replayed, written.value.result) == (False, "confirmed")
    assert (replay.replayed, replay.value) == (True, written.value)
    assert reused == "request_id_reused"
    assert failed.value.result == "timeout" and failed.value.refusal().code == "unavailable"
    newest, oldest = page.items[0], page.items[1]
    assert (oldest.id, oldest.action, oldest.target) == (written.value.activity_id, "light.on", "grow_light_main")
    assert oldest.detail == {"light": "main", "state": "on", "result": "confirmed"}
    assert (oldest.actor.source, oldest.actor.name, oldest.reason) == ("dashboard", "jason", "why")
    assert (newest.action, newest.detail["result"]) == ("light.off", "timeout")
