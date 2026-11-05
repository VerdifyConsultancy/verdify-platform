"""Migration 290 (#1358) on a private TimescaleDB: time in band graded against the band the controller served.

jvallery/agents#5994's campaign step starts a fresh server for each tests/test_dashboard_db_*.py file and sets
VERDIFY_ISOLATED_TEST_DSN; without it this file skips. The test loads db/schema.sql, creates the matview-refresh duty
and login with migration 276's shape, then runs the statements between 290's $predecessor$ and $successor$ blocks
(those compare production digest literals; the rolled-back native qualification proves them). It writes one
America/Denver day of 20 s climate with a hole and a NaN, and band_house_* audit rows every 300 s (+17 s, as the
dispatcher writes them) with a one-hour gap and a half-hour gap in vpd_high alone, then checks every 5-minute grade
against an independent Python reading of the definition, the day's rollup, the copy into daily_summary, and the
CronJob's call: only the duty login may make it, and it fills the missing days back to 2026-09-01.
"""

import asyncio
import bisect
import math
import os
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import asyncpg
import pytest
from conftest import isolated_mutation_test_dsn

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db/migrations/290-band-compliance.sql"
OWNER = "verdify"
LOGIN = "verdify_matview_refresh_runtime_login"
DENVER = ZoneInfo("America/Denver")
DAY = date(2026, 11, 4)
START = datetime(2026, 11, 4, 7, tzinfo=UTC)  # America/Denver midnight (MST)
END = START + timedelta(days=1)
ROLES = ("temp_low", "temp_high", "vpd_low", "vpd_high")
BASE = {"temp_low": 62.0, "temp_high": 68.0, "vpd_low": 0.6, "vpd_high": 0.9}
CARRY = timedelta(seconds=600)

PRODUCTION_ROLES = """
DO $roles$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'verdify') THEN
        CREATE ROLE verdify SUPERUSER LOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'twin_ro') THEN
        CREATE ROLE twin_ro NOLOGIN;
    END IF;
END $roles$;
"""

# Migration 276's matview-refresh duty: a NOLOGIN duty with USAGE on its own schema and a login that holds only it.
REFRESH_DUTY = """
DO $duty$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'verdify_matview_refresh_runtime') THEN
        CREATE ROLE verdify_matview_refresh_runtime NOLOGIN NOINHERIT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'verdify_matview_refresh_runtime_login') THEN
        CREATE ROLE verdify_matview_refresh_runtime_login LOGIN INHERIT;
    END IF;
END $duty$;
GRANT verdify_matview_refresh_runtime TO verdify_matview_refresh_runtime_login;
CREATE SCHEMA IF NOT EXISTS verdify_matview_refresh_runtime;
GRANT USAGE ON SCHEMA verdify_matview_refresh_runtime TO verdify_matview_refresh_runtime;
CREATE MATERIALIZED VIEW IF NOT EXISTS public.mv_zone_band_grade AS SELECT now() AS bucket WITH NO DATA;
"""


def migration_body() -> str:
    sql = MIGRATION.read_text()
    assert sql.count("END $predecessor$;") == 1 and sql.count("DO $successor$") == 1
    return sql.split("END $predecessor$;", 1)[1].split("DO $successor$", 1)[0]


def schema_script() -> str:
    # pg_dump wraps the dump in psql \restrict meta-commands; the server needs only the SQL.
    lines = (ROOT / "db/schema.sql").read_text().splitlines()
    return "\n".join(line for line in lines if not line.startswith("\\"))


def private_dsn() -> str:
    if not os.environ.get("VERDIFY_ISOLATED_TEST_DSN"):
        pytest.skip("needs the private TimescaleDB of the campaign step (VERDIFY_ISOLATED_TEST_DSN)")
    return isolated_mutation_test_dsn()


async def connect(dsn: str, user: str | None = None) -> asyncpg.Connection:
    return await asyncpg.connect(dsn, user=user) if user else await asyncpg.connect(dsn)


async def as_admin(dsn: str, sql: str) -> None:
    admin = await connect(dsn)
    try:
        await admin.execute(sql)
    finally:
        await admin.close()


def temp_at(ts: datetime) -> float:
    return 65 + 8 * math.sin(ts.timestamp() / 5000.0)


def vpd_at(ts: datetime) -> float:
    return 0.75 + 0.4 * math.cos(ts.timestamp() / 4000.0)


def band_at(role: str, ts: datetime) -> float:
    return BASE[role] + 0.5 * math.sin(ts.timestamp() / 9000.0)


def readings() -> list[tuple[datetime, float, float]]:
    rows, ts = [], START
    while ts < END:
        hole = START + timedelta(hours=13) <= ts < START + timedelta(hours=13, minutes=20)
        if not hole:
            rows.append((ts, temp_at(ts), float("nan") if ts == START + timedelta(hours=2) else vpd_at(ts)))
        ts += timedelta(seconds=20)
    return rows


def band_rows() -> list[tuple[datetime, str, float]]:
    rows, ts = [], START - timedelta(minutes=10)
    while ts <= END:
        for role in ROLES:
            gap = START + timedelta(hours=10) <= ts < START + timedelta(hours=11)
            vpd_high_gap = role == "vpd_high" and START + timedelta(hours=19) <= ts < START + timedelta(
                hours=19, minutes=30
            )
            if not (gap or vpd_high_gap):
                rows.append((ts + timedelta(seconds=17), role, band_at(role, ts)))
        ts += timedelta(minutes=5)
    return rows


async def install(dsn: str) -> None:
    admin = await connect(dsn)
    try:
        if await admin.fetchval("SELECT to_regclass('public.daily_band_compliance') IS NOT NULL"):
            pytest.fail("test_dashboard_db_band_compliance needs a fresh database")
    finally:
        await admin.close()
    await as_admin(dsn, PRODUCTION_ROLES)
    await as_admin(dsn, schema_script())
    await as_admin(dsn, REFRESH_DUTY)
    owner = await connect(dsn, OWNER)
    try:
        await owner.execute("SET search_path = pg_catalog, public, pg_temp;\n" + migration_body())
        await owner.execute(
            "INSERT INTO public.greenhouses (id, name) VALUES ('vallery', 'Vallery') ON CONFLICT (id) DO NOTHING"
        )
        await owner.executemany(
            "INSERT INTO public.climate (ts, greenhouse_id, temp_avg, vpd_avg) VALUES ($1, 'vallery', $2, $3)",
            readings(),
        )
        await owner.executemany(
            "INSERT INTO public.setpoint_snapshot (ts, parameter, value, greenhouse_id, zone, band_role) "
            "VALUES ($1, 'band_house_' || $2, $3, 'vallery', 'house', $2)",
            band_rows(),
        )
        await owner.execute(
            "INSERT INTO public.daily_summary (date, greenhouse_id, temp_compliance_pct, vpd_compliance_pct) "
            "VALUES ($1, 'vallery', 26.8, 72.3)",
            DAY,
        )
    finally:
        await owner.close()


@pytest.fixture(scope="module")
def dsn():
    value = private_dsn()
    asyncio.run(install(value))
    return value


def run(coroutine):
    return asyncio.run(coroutine)


async def fetch(dsn: str, sql: str, *args, user: str = OWNER):
    conn = await connect(dsn, user)
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


def expected_grades(dsn: str) -> dict[datetime, tuple[str, int | None, int | None]]:
    """The definition read independently: 5-minute means, the newest audit row within the carry, else the crop."""
    buckets: dict[datetime, list[tuple[float, float]]] = defaultdict(list)
    for ts, temp, vpd in readings():
        buckets[START + timedelta(minutes=5 * ((ts - START) // timedelta(minutes=5)))].append((temp, vpd))
    audit = defaultdict(list)
    for ts, role, value in sorted(band_rows()):
        audit[role].append((ts, value))
    out = {}
    for bucket, rows in buckets.items():
        temps = [t for t, _ in rows if math.isfinite(t)]
        vpds = [v for _, v in rows if math.isfinite(v)]
        band = {}
        for role in ROLES:
            times = [ts for ts, _ in audit[role]]
            index = bisect.bisect_left(times, bucket + timedelta(minutes=5)) - 1
            in_carry = index >= 0 and audit[role][index][0] >= bucket - CARRY
            band[role] = audit[role][index][1] if in_carry else None
        source = "audit"
        if None in band.values():
            source = "reconstructed"
            [crop] = run(fetch(dsn, "SELECT * FROM public.fn_band_setpoints($1)", bucket)) or [None]
            band = {role: (crop[role] if crop else None) for role in ROLES}

        def grade(value, low, high):
            if value is None or low is None or high is None or low > high:
                return None
            return -1 if value < low else 1 if value > high else 0

        temp = sum(temps) / len(temps) if temps else None
        vpd = sum(vpds) / len(vpds) if vpds else None
        out[bucket] = (
            source,
            grade(temp, band["temp_low"], band["temp_high"]),
            grade(vpd, band["vpd_low"], band["vpd_high"]),
        )
    return out


def test_every_bucket_is_graded_against_the_band_in_force(dsn):
    got = {
        row["bucket"]: (row["band_source"], row["temp_grade"], row["vpd_grade"])
        for row in run(fetch(dsn, "SELECT * FROM public.fn_band_compliance_5m($1, $2)", START, END))
    }
    expected = expected_grades(dsn)
    assert got == expected
    assert len(got) == 288 - 4  # the four buckets of the 20-minute hole have no reading and are not graded
    assert {bucket for bucket, (source, _, _) in got.items() if source == "reconstructed"} >= {
        START + timedelta(hours=10, minutes=10) + timedelta(minutes=5 * n) for n in range(10)
    }
    assert {grade for _, grade, _ in got.values()} >= {-1, 0, 1}


def test_the_day_rolls_up_in_graded_minutes_and_is_copied_into_daily_summary(dsn):
    rows = run(fetch(dsn, "SELECT * FROM public.fn_band_compliance_5m($1, $2)", START, END))
    [stored] = run(fetch(dsn, "SELECT * FROM public.fn_band_compliance_store($1)", DAY))

    def minutes(predicate):
        return 5 * sum(1 for row in rows if predicate(row))

    assert stored["temp_graded_min"] == minutes(lambda r: r["temp_grade"] is not None)
    assert stored["temp_in_min"] == minutes(lambda r: r["temp_grade"] == 0)
    assert stored["vpd_below_min"] == minutes(lambda r: r["vpd_grade"] == -1)
    assert stored["night_vpd_in_min"] == minutes(lambda r: not r["is_day"] and r["vpd_grade"] == 0)
    assert stored["day_both_in_min"] == minutes(lambda r: r["is_day"] and r["temp_grade"] == 0 and r["vpd_grade"] == 0)
    assert stored["reconstructed_min"] == minutes(
        lambda r: r["band_source"] == "reconstructed" and (r["temp_grade"] is not None or r["vpd_grade"] is not None)
    )
    assert stored["temp_in_band_pct"] == round(100.0 * stored["temp_in_min"] / stored["temp_graded_min"], 1)
    assert 0 < stored["day_temp_graded_min"] < stored["temp_graded_min"]  # both halves of the day are graded
    [summary] = run(fetch(dsn, "SELECT * FROM public.daily_summary WHERE date = $1", DAY))
    assert summary["temp_compliance_pct"] == stored["temp_in_band_pct"]
    assert summary["vpd_compliance_pct"] == stored["vpd_in_band_pct"]
    assert summary["compliance_pct"] == stored["both_in_band_pct"]
    assert summary["stress_hours_cold"] == round(stored["temp_below_min"] / 60.0, 2)
    assert summary["stress_hours_vpd_high"] == round(stored["vpd_above_min"] / 60.0, 2)
    # Storing again recomputes the same day.
    [again] = run(fetch(dsn, "SELECT * FROM public.fn_band_compliance_store($1)", DAY))
    assert {k: v for k, v in again.items() if k != "computed_at"} == {
        k: v for k, v in stored.items() if k != "computed_at"
    }


def test_only_the_duty_login_refreshes_and_it_fills_the_missing_days(dsn):
    with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
        run(fetch(dsn, "SELECT verdify_matview_refresh_runtime.fn_refresh_band_compliance()"))
    today = datetime.now(DENVER).date()
    first = run(fetch(dsn, "SELECT verdify_matview_refresh_runtime.fn_refresh_band_compliance() AS n", user=LOGIN))
    assert first[0]["n"] == 2 + 31
    while (
        run(fetch(dsn, "SELECT verdify_matview_refresh_runtime.fn_refresh_band_compliance() AS n", user=LOGIN))[0]["n"]
        > 2
    ):
        pass
    days = {row["date"] for row in run(fetch(dsn, "SELECT date FROM public.daily_band_compliance"))}
    wanted = {date(2026, 9, 1) + timedelta(days=n) for n in range((today - date(2026, 9, 1)).days + 1)}
    assert wanted <= days
    with pytest.raises(asyncpg.exceptions.InvalidParameterValueError):
        run(fetch(dsn, "SELECT public.fn_band_compliance_store($1)", today + timedelta(days=1)))
    # The duty login reaches the rollup only through the refresh.
    with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
        run(fetch(dsn, "SELECT count(*) FROM public.daily_band_compliance", user=LOGIN))
    with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
        run(fetch(dsn, "SELECT public.fn_band_compliance_store($1)", DAY, user=LOGIN))


def test_the_scorecard_reads_the_rollup(dsn):
    [row] = run(fetch(dsn, "SELECT * FROM public.v_daily_scorecard WHERE date = $1", DAY))
    [stored] = run(fetch(dsn, "SELECT * FROM public.daily_band_compliance WHERE date = $1", DAY))
    assert row["temp_in_band_pct"] == stored["temp_in_band_pct"]
    assert row["time_in_band_day_pct"] == stored["both_in_band_day_pct"]
    assert row["time_in_band_night_pct"] == stored["both_in_band_night_pct"]
    assert row["vpd_below_band_pct"] == stored["vpd_below_band_pct"]
    assert row["band_reconstructed_pct"] == stored["reconstructed_pct"] and row["band_reason"] is None
