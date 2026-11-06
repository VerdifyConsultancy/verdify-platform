"""Migration 304 (#1283) on a private TimescaleDB: a water day ends at the next America/Denver midnight.

jvallery/agents#5994's campaign step starts a fresh server for each tests/test_dashboard_db_*.py file and sets
VERDIFY_ISOLATED_TEST_DSN; without it this file skips. The test loads db/schema.sql, whose v_water_meter_daily is the
view 304 replaces, writes 5-minute meter samples for four local days and reads them in a UTC session, as production
runs: first through the old view, to show the defect, then through the statements between 304's $predecessor$ and
$successor$ blocks (those compare production digest literals; the rolled-back native qualification proves them).

  2026-11-01  25 hours (fall back), last hour missing: the old view reads it complete, 304 incomplete.
  2025-11-02  25 hours, every sample: complete either way.
  2026-03-08  23 hours (spring forward), every sample: the old view reads it incomplete, 304 complete.
  2026-10-31  24 hours, every sample: complete either way.
"""

import asyncio
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import asyncpg
import pytest
from test_dashboard_db_band_compliance import PRODUCTION_ROLES, as_admin, private_dsn, schema_script

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db/migrations/304-water-day-local-end.sql"
OWNER = "verdify"
STEP = timedelta(minutes=5)
# local day -> (its local midnight in UTC, the first instant with no sample)
DAYS = {
    date(2026, 11, 1): (datetime(2026, 11, 1, 6, tzinfo=UTC), datetime(2026, 11, 2, 6, tzinfo=UTC)),
    date(2025, 11, 2): (datetime(2025, 11, 2, 6, tzinfo=UTC), datetime(2025, 11, 3, 7, tzinfo=UTC)),
    date(2026, 3, 8): (datetime(2026, 3, 8, 7, tzinfo=UTC), datetime(2026, 3, 9, 6, tzinfo=UTC)),
    date(2026, 10, 31): (datetime(2026, 10, 31, 6, tzinfo=UTC), datetime(2026, 11, 1, 6, tzinfo=UTC)),
}
COLUMNS = """
SELECT attname, format_type(atttypid, atttypmod) FROM pg_attribute
 WHERE attrelid = 'public.v_water_meter_daily'::regclass AND attnum > 0 AND NOT attisdropped ORDER BY attnum
"""
READ = """
SELECT (day AT TIME ZONE 'America/Denver')::date AS local_day, day, first_raw_ts, last_raw_ts, is_complete_day,
       quality, available_for_scoring
  FROM public.v_water_meter_daily
 ORDER BY day
"""


def migration_body() -> str:
    sql = MIGRATION.read_text()
    assert sql.count("END $predecessor$;") == 1 and sql.count("DO $successor$") == 1
    return sql.split("END $predecessor$;", 1)[1].split("DO $successor$", 1)[0]


def samples() -> list[tuple[datetime]]:
    rows = []
    for start, stop in DAYS.values():
        ts = start
        while ts < stop:
            rows.append((ts,))
            ts += STEP
    return rows


async def connect(dsn: str, timezone: str = "UTC") -> asyncpg.Connection:
    return await asyncpg.connect(dsn, user=OWNER, server_settings={"timezone": timezone})


async def read(dsn: str, timezone: str = "UTC") -> dict[date, asyncpg.Record]:
    conn = await connect(dsn, timezone)
    try:
        return {row["local_day"]: row for row in await conn.fetch(READ)}
    finally:
        await conn.close()


async def columns(dsn: str) -> list[tuple[str, str]]:
    conn = await connect(dsn)
    try:
        return [tuple(row) for row in await conn.fetch(COLUMNS)]
    finally:
        await conn.close()


async def install(dsn: str) -> tuple[dict[date, asyncpg.Record], list[tuple[str, str]]]:
    admin = await asyncpg.connect(dsn)
    try:
        if await admin.fetchval("SELECT to_regclass('public.v_water_meter_daily') IS NOT NULL"):
            pytest.fail("test_dashboard_db_water_day_304 needs a fresh database")
    finally:
        await admin.close()
    await as_admin(dsn, PRODUCTION_ROLES)
    await as_admin(dsn, schema_script())
    owner = await connect(dsn)
    try:
        await owner.execute(
            "INSERT INTO public.greenhouses (id, name) VALUES ('vallery', 'Vallery') ON CONFLICT (id) DO NOTHING"
        )
        await owner.executemany(
            "INSERT INTO public.climate (ts, greenhouse_id, water_total_gal) VALUES ($1, 'vallery', 100)", samples()
        )
        await owner.execute(
            "INSERT INTO public.water_meter_materializer_state (greenhouse_id, last_source_ts) VALUES ('vallery', now())"
        )
    finally:
        await owner.close()
    before = await read(dsn), await columns(dsn)
    owner = await connect(dsn)
    try:
        await owner.execute("SET search_path = pg_catalog, public, pg_temp;\n" + migration_body())
    finally:
        await owner.close()
    return before


@pytest.fixture(scope="module")
def world():
    dsn = private_dsn()
    before, before_columns = asyncio.run(install(dsn))
    return dsn, before, asyncio.run(read(dsn)), before_columns


def test_the_old_view_shows_the_defect_in_a_utc_session(world):
    _, before, _, _ = world
    # The fall-back day with its last hour missing read complete: its end was 24 h, not 25 h, after its start.
    assert before[date(2026, 11, 1)]["is_complete_day"] is True
    assert before[date(2026, 11, 1)]["quality"] == "ok"
    # The spring-forward day with every sample read incomplete: its end was an hour past its last sample.
    assert before[date(2026, 3, 8)]["is_complete_day"] is False
    assert before[date(2026, 3, 8)]["quality"] == "incomplete"


def test_the_25_hour_day_with_its_last_hour_missing_is_incomplete(world):
    _, _, after, _ = world
    day = after[date(2026, 11, 1)]
    assert day["day"] == datetime(2026, 11, 1, 6, tzinfo=UTC)
    assert day["last_raw_ts"] == datetime(2026, 11, 2, 5, 55, tzinfo=UTC)  # an hour short of 07:00Z
    assert day["is_complete_day"] is False and day["available_for_scoring"] is False
    assert day["quality"] == "incomplete"


def test_complete_23_24_and_25_hour_days_read_complete(world):
    _, _, after, _ = world
    for local_day in (date(2025, 11, 2), date(2026, 3, 8), date(2026, 10, 31)):
        day = after[local_day]
        assert day["day"] == DAYS[local_day][0], local_day
        assert day["is_complete_day"] is True, local_day
        assert day["quality"] == "ok", local_day
        assert day["available_for_scoring"] is True, local_day


def test_the_session_time_zone_changes_nothing(world):
    dsn, _, after, _ = world
    for timezone in ("America/Denver", "Pacific/Kiritimati", "Pacific/Pago_Pago"):
        assert asyncio.run(read(dsn, timezone)) == after, timezone


def test_the_view_keeps_its_columns_names_and_types_in_order(world):
    dsn, before, after, before_columns = world
    assert set(after) == set(before) == set(DAYS)
    assert asyncio.run(columns(dsn)) == before_columns
    assert len(before_columns) == 19
