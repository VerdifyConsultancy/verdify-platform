"""Migration 305 (#1365) on a private TimescaleDB: the nightly dry-out flag in the 290 rollup.

jvallery/agents#5994's campaign step starts a fresh server for each tests/test_dashboard_db_*.py file and sets
VERDIFY_ISOLATED_TEST_DSN; without it this file skips. The test builds 290 as test_dashboard_db_band_compliance does
(db/schema.sql, the matview-refresh duty, 290's body), writes two nights of 20 s house readings with band_house_*
audit rows every 300 s, stores both days with 290's store, then runs the statements between 305's $predecessor$ and
$successor$ blocks and checks the refill, the store, the flag and the refresh. The floor is max(vpd_low, 0.74 kPa):

  2026-11-04 (6 h window, 07:00Z-13:00Z)  VPD 0.80 throughout except 0.70 from 05:30 local; vpd_low 0.60 except
                                          0.85 from 02:00 to 03:00 local -> 270 min at the floor, dried out.
  2026-11-01 (fall back, 7 h window,      VPD 0.80 for the first 3 h, then 0.70 -> 180 of 420 min, not dried out.
              06:00Z-13:00Z)
"""

import asyncio
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import asyncpg
import pytest
from test_dashboard_db_band_compliance import (
    LOGIN,
    OWNER,
    PRODUCTION_ROLES,
    REFRESH_DUTY,
    as_admin,
    connect,
    fetch,
    private_dsn,
    schema_script,
)
from test_dashboard_db_band_compliance import migration_body as body_290

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db/migrations/305-band-nightly-dryout.sql"
ROLES = ("temp_low", "temp_high", "vpd_low", "vpd_high")
DRY = date(2026, 11, 4)
DRY_OPEN = datetime(2026, 11, 4, 7, tzinfo=UTC)  # 00:00 MST
FALLBACK = date(2026, 11, 1)
FALLBACK_OPEN = datetime(2026, 11, 1, 6, tzinfo=UTC)  # 00:00 MDT; 06:00 MST is 13:00Z


def migration_body() -> str:
    sql = MIGRATION.read_text()
    assert sql.count("END $predecessor$;") == 1 and sql.count("DO $successor$") == 1
    return sql.split("END $predecessor$;", 1)[1].split("DO $successor$", 1)[0]


def vpd_at(ts: datetime) -> float:
    if DRY_OPEN <= ts < DRY_OPEN + timedelta(hours=6):
        return 0.80 if ts < DRY_OPEN + timedelta(hours=5, minutes=30) else 0.70
    if FALLBACK_OPEN <= ts < FALLBACK_OPEN + timedelta(hours=7):
        return 0.80 if ts < FALLBACK_OPEN + timedelta(hours=3) else 0.70
    return 0.70


def vpd_low_at(ts: datetime) -> float:
    if DRY_OPEN + timedelta(hours=2) <= ts < DRY_OPEN + timedelta(hours=3):
        return 0.85
    return 0.60


def windows() -> list[tuple[datetime, datetime]]:
    # Each night, from an hour before its window to an hour after, so the window edges are inside the data.
    return [
        (DRY_OPEN - timedelta(hours=1), DRY_OPEN + timedelta(hours=7)),
        (FALLBACK_OPEN - timedelta(hours=1), FALLBACK_OPEN + timedelta(hours=8)),
    ]


def readings() -> list[tuple[datetime, float, float]]:
    rows = []
    for start, stop in windows():
        ts = start
        while ts < stop:
            rows.append((ts, 65.0, vpd_at(ts)))
            ts += timedelta(seconds=20)
    return rows


def band_rows() -> list[tuple[datetime, str, float]]:
    base = {"temp_low": 60.0, "temp_high": 70.0, "vpd_high": 1.2}
    rows = []
    for start, stop in windows():
        ts = start - timedelta(minutes=10)
        while ts <= stop:
            for role in ROLES:
                value = vpd_low_at(ts) if role == "vpd_low" else base[role]
                rows.append((ts + timedelta(seconds=17), role, value))
            ts += timedelta(minutes=5)
    return rows


async def install(dsn: str) -> None:
    admin = await connect(dsn)
    try:
        if await admin.fetchval("SELECT to_regclass('public.daily_band_compliance') IS NOT NULL"):
            pytest.fail("test_dashboard_db_band_dryout_305 needs a fresh database")
    finally:
        await admin.close()
    await as_admin(dsn, PRODUCTION_ROLES)
    await as_admin(dsn, schema_script())
    await as_admin(dsn, REFRESH_DUTY)
    owner = await connect(dsn, OWNER)
    try:
        await owner.execute("SET search_path = pg_catalog, public, pg_temp;\n" + body_290())
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
        # Both days are stored by 290 before 305, as production's are; 305 must fill them.
        for day in (DRY, FALLBACK):
            await owner.execute("SELECT public.fn_band_compliance_store($1)", day)
        await owner.execute("SET search_path = pg_catalog, public, pg_temp;\n" + migration_body())
    finally:
        await owner.close()


@pytest.fixture(scope="module")
def dsn():
    value = private_dsn()
    asyncio.run(install(value))
    return value


def run(coroutine):
    return asyncio.run(coroutine)


def stored(dsn: str, day: date) -> asyncpg.Record:
    [row] = run(fetch(dsn, "SELECT * FROM public.daily_band_compliance WHERE date = $1", day))
    return row


def test_the_migration_fills_the_days_already_stored(dsn):
    dry = stored(dsn, DRY)
    assert (dry["night_dryout_graded_min"], dry["night_dryout_min"], dry["night_dried_out"]) == (360, 270, True)
    fallback = stored(dsn, FALLBACK)
    assert (fallback["night_dryout_graded_min"], fallback["night_dryout_min"]) == (420, 180)
    assert fallback["night_dried_out"] is False


def test_the_floor_is_the_served_vpd_low_but_never_below_0_74(dsn):
    rows = run(
        fetch(
            dsn,
            "SELECT * FROM public.fn_band_compliance_5m($1, $2)",
            DRY_OPEN,
            DRY_OPEN + timedelta(hours=6),
        )
    )
    assert len(rows) == 72
    # vpd_low 0.60 is lifted to 0.74, so 0.80 counts; vpd_low 0.85 from 02:00 to 03:00 local keeps 0.80 out.
    at_floor = [r for r in rows if r["vpd_kpa"] >= max(r["vpd_low"], 0.74)]
    assert 5 * len(at_floor) == 270
    [window] = run(fetch(dsn, "SELECT * FROM public.fn_band_dryout_night($1)", DRY))
    assert (window["graded_min"], window["floor_min"]) == (360, 270)


def test_the_store_writes_the_window_and_storing_again_changes_nothing(dsn):
    before = stored(dsn, DRY)
    [again] = run(fetch(dsn, "SELECT * FROM public.fn_band_compliance_store($1)", DRY))
    assert {k: v for k, v in again.items() if k != "computed_at"} == {
        k: v for k, v in before.items() if k != "computed_at"
    }
    # 290's columns are untouched by 305.
    assert before["night_vpd_graded_min"] > 0 and before["definition"] == "served-band-5m-v1"


def test_a_window_that_has_not_closed_is_null(dsn):
    tomorrow = run(fetch(dsn, "SELECT ((now() AT TIME ZONE 'America/Denver')::date + 1) AS d"))[0]["d"]
    [window] = run(fetch(dsn, "SELECT * FROM public.fn_band_dryout_night($1)", tomorrow))
    assert window["graded_min"] is None and window["floor_min"] is None
    # A day with no readings in its window and a closed window grades nothing: 0 minutes, not dried out.
    [empty] = run(fetch(dsn, "SELECT * FROM public.fn_band_dryout_night($1)", date(2026, 10, 1)))
    assert (empty["graded_min"], empty["floor_min"]) == (0, 0)


def test_the_check_refuses_minutes_that_do_not_add_up(dsn):
    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        run(fetch(dsn, "UPDATE public.daily_band_compliance SET night_dryout_min = 400 WHERE date = $1", DRY))
    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        run(fetch(dsn, "UPDATE public.daily_band_compliance SET night_dryout_min = NULL WHERE date = $1", DRY))


def test_the_refresh_duty_still_refreshes_and_cannot_call_the_window_directly(dsn):
    [row] = run(fetch(dsn, "SELECT verdify_matview_refresh_runtime.fn_refresh_band_compliance() AS n", user=LOGIN))
    assert row["n"] >= 2
    with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
        run(fetch(dsn, "SELECT * FROM public.fn_band_dryout_night($1)", DRY, user=LOGIN))
    dry = stored(dsn, DRY)
    assert dry["night_dried_out"] is True
