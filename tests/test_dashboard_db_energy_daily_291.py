"""Migration 291's v_energy_daily and daily_summary backfill on a private PostgreSQL (#1362, data integrity).

The predecessor view is production's exact 279 text (tests/fixtures/291-v-energy-daily-predecessor.sql, sha-bound
by 291's predecessor block); 291's statements between its seal blocks then replace it in a private schema. The
synthetic day reproduces production's shape: the 1500 W heater reads -1,480 W on ch1, a 240 V load reads positive on
both channels, and one sample nets the two on ch1. The old view integrates a negative day; 291 integrates |ch0| +
|ch1|, keeps the signed integral in signed_kwh, counts the corrected samples, keeps the view's owner and ACL, and
refreshes daily_summary only inside its window, clearing a day the view has no row for. Runs only against
VERDIFY_ISOLATED_TEST_DSN (a fresh server per file in the campaign step); skipped elsewhere.
"""

from __future__ import annotations

import asyncio
import os
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

asyncpg = pytest.importorskip("asyncpg")

pytestmark = pytest.mark.skipif(
    not os.environ.get("VERDIFY_ISOLATED_TEST_DSN"),
    reason="needs the campaign step's private PostgreSQL (VERDIFY_ISOLATED_TEST_DSN)",
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "m291_energy_daily"
ROLE = "m291_energy_daily_ingestor"
MIGRATION = ROOT / "db/migrations/291-energy-daily-load-magnitude.sql"
PREDECESSOR_VIEW = ROOT / "tests/fixtures/291-v-energy-daily-predecessor.sql"
ENTITIES = (
    "sensor.shellyproem50_ac15186daafc_energy_meter_0_power",
    "sensor.shellyproem50_ac15186daafc_energy_meter_1_power",
)
DAY = date(2026, 10, 25)  # America/Denver (MDT): 06:00Z to 06:00Z next day
START = datetime(2026, 10, 25, 12, tzinfo=UTC)
STEP = timedelta(minutes=5)


def migration_body() -> str:
    sql = MIGRATION.read_text()
    body = sql.split("END $predecessor$;", 1)[1].split("DO $successor$", 1)[0]
    body = re.sub(r"--[^\n]*", "", body)
    return body.replace("public.", f"{SCHEMA}.")


def samples() -> list[tuple[datetime, float, float]]:
    rows = []
    at = START
    for _ in range(12):  # an hour of heater: ch1 reads it negative
        rows.append((at, 0.0, -1480.0))
        at += STEP
    for _ in range(12):  # an hour of a 240 V load, positive on both channels
        rows.append((at, 400.0, 390.0))
        at += STEP
    rows.append((at, 357.0, -1090.0))  # heater and the 390 W leg net on ch1
    at += STEP
    rows.append((at, 0.0, 10.0))  # the last sample holds nothing: no trailing extrapolation
    return rows


async def install(conn) -> None:
    await conn.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE; CREATE SCHEMA {SCHEMA}")
    await conn.execute(f"DO $$ BEGIN CREATE ROLE {ROLE} NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$")
    await conn.execute(
        f"""
        SET search_path = {SCHEMA};
        CREATE TABLE energy (
            ts timestamptz NOT NULL, watts_total float8, watts_heat float8, watts_fans float8, watts_other float8,
            kwh_today float8, greenhouse_id text DEFAULT 'vallery', measurement_revision text, ch0_power_w float8,
            ch1_power_w float8, ch0_source_ts timestamptz, ch1_source_ts timestamptz, ch0_entity_id text,
            ch1_entity_id text, ch0_quality text, ch1_quality text);
        CREATE TABLE daily_summary (
            date date NOT NULL, greenhouse_id text DEFAULT 'vallery', kwh_total float8, peak_kw float8,
            captured_at timestamptz);
        CREATE VIEW v_energy_daily AS {PREDECESSOR_VIEW.read_text()}
        GRANT USAGE ON SCHEMA {SCHEMA} TO {ROLE};
        GRANT SELECT ON v_energy_daily TO {ROLE};
        """
    )
    await conn.executemany(
        "INSERT INTO energy (ts, watts_total, ch0_power_w, ch1_power_w, ch0_source_ts, ch1_source_ts, "
        "ch0_entity_id, ch1_entity_id, ch0_quality, ch1_quality, measurement_revision) "
        "VALUES ($1, $2::float8 + $3::float8, $2, $3, $1, $1, $4, $5, 'ok', 'ok', 'shelly_modbus_source_v2')",
        [(at, a, b, *ENTITIES) for at, a, b in samples()],
    )
    await conn.execute(
        "INSERT INTO daily_summary (date, kwh_total, peak_kw) VALUES "
        "('2026-09-20', 5, 0.5), "  # before the window: untouched
        "('2026-10-25', -0.751, -0.0), "  # the synthetic day, negative as production shows
        "('2026-10-26', -3, 1)"  # inside the window, no meter row: cleared, as the daily refresh does
    )


async def remove(conn) -> None:
    await conn.execute(f"RESET search_path; DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
    await conn.execute(
        f"DO $$ BEGIN DROP OWNED BY {ROLE}; DROP ROLE {ROLE}; EXCEPTION WHEN undefined_object THEN NULL; END $$"
    )


async def day(conn):
    return await conn.fetchrow(f"SELECT * FROM {SCHEMA}.v_energy_daily WHERE date = $1", DAY)


async def scenario(conn) -> None:
    old = await day(conn)
    # The bug: the signed integral of a heater-dominated day is negative.
    assert old["measured_kwh"] == Decimal("-0.751") and old["peak_watts"] == Decimal("790.0")
    acl = await conn.fetchval(f"SELECT relacl::text FROM pg_class WHERE oid = '{SCHEMA}.v_energy_daily'::regclass")

    async with conn.transaction():
        await conn.execute(migration_body())

    new = await day(conn)
    # 12 x 300 s x 1480 W + 12 x 300 s x 790 W + 300 s x 1447 W = 2.390583 kWh; the last sample holds 0 s.
    assert new["measured_kwh"] == Decimal("2.391")
    assert new["signed_kwh"] == Decimal("-0.751")  # the old value stays visible
    assert new["peak_watts"] == Decimal("1480.0")
    assert new["avg_watts"] == Decimal("1147.5")  # 2.390583 kWh over 2.0833 observed hours
    assert new["sample_count"] == 25 and new["sign_corrected_samples"] == 13
    assert new["observed_hours"] == 2.083  # 25 x 300 s
    assert new["available_for_scoring"] is False and new["measured_scope"] == "partial_shelly_two_channels"
    # Same columns first, two appended; same owner and grants.
    columns = await conn.fetch(
        f"SELECT attname FROM pg_attribute WHERE attrelid = '{SCHEMA}.v_energy_daily'::regclass AND attnum > 0 "
        "ORDER BY attnum"
    )
    assert [c["attname"] for c in columns][-2:] == ["signed_kwh", "sign_corrected_samples"] and len(columns) == 13
    assert (
        await conn.fetchval(f"SELECT relacl::text FROM pg_class WHERE oid = '{SCHEMA}.v_energy_daily'::regclass") == acl
    )
    await conn.execute(f"SET ROLE {ROLE}")
    try:
        assert await conn.fetchval(f"SELECT measured_kwh FROM {SCHEMA}.v_energy_daily WHERE date = $1", DAY) > 0
    finally:
        await conn.execute("RESET ROLE")
    # No day is negative, and the backfill is bounded to 2026-09-27 onward.
    assert not await conn.fetchval(f"SELECT count(*) FROM {SCHEMA}.v_energy_daily WHERE measured_kwh < 0")
    summary = {
        r["date"]: (r["kwh_total"], r["peak_kw"], r["captured_at"] is not None)
        for r in await conn.fetch(f"SELECT * FROM {SCHEMA}.daily_summary")
    }
    assert summary == {
        date(2026, 9, 20): (5.0, 0.5, False),
        DAY: (2.391, 1.48, True),
        date(2026, 10, 26): (None, None, True),
    }


async def failure_rolls_back(conn) -> None:
    """A negative day left behind refuses the whole migration."""
    await conn.execute(f"INSERT INTO {SCHEMA}.daily_summary (date, kwh_total) VALUES ('2026-09-01', -1)")
    with pytest.raises(asyncpg.RaiseError, match="291 left a negative energy day"):
        async with conn.transaction():
            await conn.execute(migration_body())
    assert (await day(conn))["measured_kwh"] == Decimal("-0.751")  # the old view is still there


def run(check) -> None:
    async def main():
        conn = await asyncpg.connect(os.environ["VERDIFY_ISOLATED_TEST_DSN"], timeout=10)
        try:
            await install(conn)
            await check(conn)
        finally:
            await remove(conn)
            await conn.close()

    asyncio.run(main())


def test_291_integrates_load_magnitude_and_backfills_the_window():
    run(scenario)


def test_291_refuses_to_leave_a_negative_day():
    run(failure_rolls_back)
