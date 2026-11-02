"""Migration M4 (VD-39, #1154): minutes by zone and day coverage, on a private database.

jvallery/agents#5994's campaign step runs each ``tests/test_dashboard_db_*.py``
file against a fresh loopback PostgreSQL with TimescaleDB and hands it over in
``VERDIFY_ISOLATED_TEST_DSN``. Without one the module skips, and a skip fails
campaign qualification. The migration chain cannot replay there (254 and later
check production digest literals), so the test creates the roles it needs,
loads the production objects M4 reads from ``db/schema.sql`` and runs the
statements between 281's ``$predecessor$`` and ``$successor$`` blocks as the
owner. Attestation needs the production catalog: the rolled-back native
qualification proves it (``tests/test_281_irrigation_minutes_delivery.py``).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import math
import time

import irrigation_db_support as support
import pytest
from conftest import isolated_mutation_test_dsn

asyncpg = pytest.importorskip("asyncpg")

try:
    DSN = isolated_mutation_test_dsn()
except ValueError:
    DSN = None

pytestmark = pytest.mark.skipif(DSN is None, reason="needs VERDIFY_ISOLATED_TEST_DSN (campaign private PostgreSQL)")

COLUMNS = [
    ("greenhouse_id", "text"),
    ("local_day", "date"),
    ("zone", "text"),
    ("line_id", "text"),
    ("cause", "text"),
    ("chemistry", "text"),
    ("slot_id", "integer"),
    ("minutes", "double precision"),
    ("pulses", "integer"),
    ("gal", "integer"),
    ("partial", "boolean"),
]
DAY = dt.date


def run(coro):
    return asyncio.run(coro)


async def connect():
    return await asyncpg.connect(DSN)


@pytest.fixture(scope="module")
def database():
    async def setup():
        conn = await connect()
        try:
            timescale = await support.prepare_database(conn)
            await conn.execute(support.DAYS_FIXTURE.read_text())
            # Today is the newest climate day; the fixture carries no climate rows.
            await conn.execute("INSERT INTO public.climate (ts, greenhouse_id) VALUES (now(), 'vallery')")
            await conn.execute("ANALYZE")
            return timescale
        finally:
            await conn.close()

    return run(setup())


async def fetch_snapshot(sql="SELECT * FROM public.v_irrigation_minutes_daily", *args):
    """The view's rows and the edge walk's expectation, under one now()."""
    conn = await connect()
    try:
        async with conn.transaction():
            inputs = await support.read_inputs(conn)
            rows = {tuple(r) for r in await conn.fetch(sql, *args)}
            coverage = {
                r["local_day"]: r["coverage"]
                for r in await conn.fetch("SELECT local_day, coverage FROM public.v_irrigation_day_coverage")
            }
        return rows, support.expected_minutes(*inputs), coverage, inputs[-1]
    finally:
        await conn.close()


def by_line(rows, day):
    return {r[3]: r for r in rows if r[1] == day}


def test_view_columns_are_the_final_contract(database):
    async def check():
        conn = await connect()
        try:
            got = [
                (r["column_name"], r["data_type"])
                for r in await conn.fetch(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'v_irrigation_minutes_daily' ORDER BY ordinal_position"
                )
            ]
            result = await conn.fetch(
                "SELECT p.proname, pg_get_function_result(p.oid) AS result, p.prosecdef, p.provolatile::text AS provolatile, p.proconfig "
                "FROM pg_proc p WHERE p.oid = 'public.fn_irrigation_minutes_daily(date, date)'::regprocedure"
            )
            coverage = [
                (r["column_name"], r["data_type"])
                for r in await conn.fetch(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'v_irrigation_day_coverage' ORDER BY ordinal_position"
                )
            ]
            return got, result[0], coverage
        finally:
            await conn.close()

    got, function, coverage = run(check())
    assert got == COLUMNS
    assert coverage == [("greenhouse_id", "text"), ("local_day", "date"), ("coverage", "text")]
    assert function["result"] == "TABLE(" + ", ".join(f"{n} {t}" for n, t in COLUMNS) + ")"
    assert function["prosecdef"] and function["provolatile"] == "s"
    assert function["proconfig"] == ["search_path=pg_catalog, public, pg_temp"]


def test_real_days_match_the_edge_walk_and_the_pinned_values(database):
    rows, expected, coverage, _ = run(fetch_snapshot())
    assert rows == expected
    # 2026-05-29: the wall drip's last run (L01), with every other line exercised.
    may = by_line(rows, DAY(2026, 5, 29))
    assert may["L01"] == ("vallery", DAY(2026, 5, 29), "wall", "L01", "unknown", "clean", None, 1.9, 1, 3, False)
    assert may["L02"][4:] == ("feed", "fert", None, 6.0, 1, 1, False)
    assert may["L03"][2:] == ("supply", "L03", "feed", "fert", None, 23.6, 4, None, False)
    assert may["L04"][7:] == (37.0, 52, 4, False)
    assert may["L11"][2:] == ("fog", "L11", "vpd", "clean", None, 77.6, 60, 10, False)
    # 2026-07-10: south and west misting, on a day an ingestor gap touched.
    july = by_line(rows, DAY(2026, 7, 10))
    assert july["L07"][2:] == ("south", "L07", "vpd", "clean", None, 31.0, 11, 1, True)
    assert july["L09"][2:] == ("west", "L09", "vpd", "clean", None, 4.9, 4, 1, True)
    assert set(july) == {"L04", "L07", "L09", "L11"}
    # October center misting.
    assert by_line(rows, DAY(2026, 10, 12))["L04"][7:] == (3.6, 7, 0, False)
    assert by_line(rows, DAY(2026, 10, 17))["L04"][7:] == (1.2, 2, 0, False)
    assert by_line(rows, DAY(2026, 10, 23))["L04"][7:] == (2.3, 5, 1, True)
    assert by_line(rows, DAY(2026, 10, 23))["L11"][7:] == (11.2, 7, 5, True)
    assert [coverage[DAY(2026, 10, d)] for d in (22, 23, 24)] == ["complete", "partial", "partial"]


def test_fall_back_day_splits_at_local_midnight(database):
    rows, expected, coverage, _ = run(fetch_snapshot())
    assert rows == expected
    halloween, fall_back = by_line(rows, DAY(2026, 10, 31)), by_line(rows, DAY(2026, 11, 1))
    # 23:50 on 10-31 to 00:10 on 11-01: ten minutes each side, one pulse, on its start day.
    # Its only overlapping delta is ambiguous: 0 gallons, not NULL, though
    # 10-31 has no delta of its own.
    assert halloween["L05"][7:] == (10.0, 1, 0, False)
    assert fall_back["L05"][7:] == (10.0, 0, 0, False)
    # All of 2026-11-01 is 25 hours.
    assert fall_back["L06"][7:] == (1500.0, 1, 3, False)
    assert coverage[DAY(2026, 11, 1)] == "complete"


def test_outage_partial_no_data_and_today_are_honest(database):
    rows, expected, coverage, now = run(fetch_snapshot())
    assert rows == expected
    outage = by_line(rows, DAY(2026, 9, 15))
    assert coverage[DAY(2026, 9, 15)] == "partial"
    assert set(outage) == {"L04", "L11"}  # never zero-filled
    assert outage["L04"][7:] == (1.5, 1, 1, True)
    assert outage["L11"][7:] == (3.0, 1, 0, True)
    # One gap covers 09-20 whole: no_data and no minutes row, though the drip ran through it.
    assert coverage[DAY(2026, 9, 20)] == "no_data"
    assert not by_line(rows, DAY(2026, 9, 20))
    assert by_line(rows, DAY(2026, 9, 19))["L01"][7:] == (150.0, 1, None, True)
    assert by_line(rows, DAY(2026, 9, 21))["L01"][7:] == (30.0, 0, None, True)
    # A complete day without runs has a coverage row and no minutes rows.
    assert coverage[DAY(2026, 9, 1)] == "complete" and not by_line(rows, DAY(2026, 9, 1))
    assert coverage[support.local_day(now)] == "partial"


def test_gallons_whole_null_and_zero(database):
    rows, _, _, _ = run(fetch_snapshot())
    feed = by_line(rows, DAY(2026, 9, 25))
    assert feed["L03"][2:] == ("supply", "L03", "feed", "fert", None, 8.0, 1, None, False)
    # Only a high_delta and a phantom zero: no accepted delta that day.
    assert feed["L02"][7:] == (6.0, 1, None, False)
    assert all(r[9] is None for r in rows if r[3] == "L03")
    assert all(isinstance(r[9], int) for r in rows if r[3] != "L03" and r[9] is not None)


def test_days_after_the_newest_climate_row_have_no_data(database):
    async def scenario():
        conn = await connect()
        try:
            tr = conn.transaction()
            await tr.start()
            try:
                await conn.execute("DELETE FROM public.climate")
                await conn.execute(
                    "INSERT INTO public.climate (ts, greenhouse_id) VALUES "
                    "(('2026-10-18 12:00'::timestamp AT TIME ZONE 'America/Denver'), 'vallery')"
                )
                coverage = {
                    r["local_day"]: r["coverage"]
                    for r in await conn.fetch("SELECT local_day, coverage FROM public.v_irrigation_day_coverage")
                }
                days = {
                    r["local_day"] for r in await conn.fetch("SELECT local_day FROM public.v_irrigation_minutes_daily")
                }
                inputs = await support.read_inputs(conn)
                rows = {tuple(r) for r in await conn.fetch("SELECT * FROM public.v_irrigation_minutes_daily")}
                return coverage, days, rows, support.expected_minutes(*inputs)
            finally:
                await tr.rollback()
        finally:
            await conn.close()

    coverage, days, rows, expected = run(scenario())
    assert rows == expected
    assert coverage[DAY(2026, 10, 17)] == "complete"
    assert coverage[DAY(2026, 10, 18)] == "partial"
    assert all(state == "no_data" for day, state in coverage.items() if day > DAY(2026, 10, 18))
    assert max(days) == DAY(2026, 10, 18)


def test_function_returns_the_views_rows_for_any_range(database):
    async def compare():
        conn = await connect()
        try:
            async with conn.transaction():
                today = await conn.fetchval("SELECT (now() AT TIME ZONE 'America/Denver')::date")
                ranges = [
                    (DAY(2026, 5, 29), DAY(2026, 5, 29)),
                    (DAY(2026, 5, 27), DAY(2026, 7, 11)),
                    (DAY(2026, 9, 19), DAY(2026, 9, 21)),
                    (DAY(2026, 9, 20), DAY(2026, 9, 20)),
                    (DAY(2026, 10, 31), DAY(2026, 11, 1)),
                    (DAY(2026, 11, 1), today),
                    (DAY(2025, 1, 1), today),
                ]
                out = []
                for low, high in ranges:
                    view = {
                        tuple(r)
                        for r in await conn.fetch(
                            "SELECT * FROM public.v_irrigation_minutes_daily WHERE local_day BETWEEN $1 AND $2",
                            low,
                            high,
                        )
                    }
                    function = {
                        tuple(r)
                        for r in await conn.fetch("SELECT * FROM public.fn_irrigation_minutes_daily($1, $2)", low, high)
                    }
                    out.append((low, high, view, function))
                empty = await conn.fetch(
                    "SELECT * FROM public.fn_irrigation_minutes_daily($1, $2)", today, DAY(2026, 1, 1)
                )
                null = await conn.fetch("SELECT * FROM public.fn_irrigation_minutes_daily(NULL, $1)", today)
                return out, empty, null
        finally:
            await conn.close()

    out, empty, null = run(compare())
    for low, high, view, function in out:
        assert view == function, (low, high)
    assert sum(len(view) for *_, view, _ in out) > 100
    assert not empty and not null


def test_gallons_reconcile_with_run_accounting(database):
    async def compare():
        conn = await connect()
        try:
            async with conn.transaction():
                accounting = await conn.fetch(
                    "SELECT (r.run_start AT TIME ZONE 'America/Denver')::date AS local_day, r.relay_slug, "
                    "round(sum(r.meter_attributed_gal)::numeric)::int AS gal "
                    "FROM public.v_water_run_accounting r GROUP BY 1, 2 HAVING sum(r.meter_attributed_gal) IS NOT NULL"
                )
                view = await conn.fetch(
                    "SELECT local_day, line_id, gal FROM public.v_irrigation_minutes_daily WHERE pulses > 0"
                )
                return accounting, view
        finally:
            await conn.close()

    accounting, view = run(compare())
    lines = {relay: line for relay, (line, *_) in support.LINE_MAP.items()}
    mine = {(r["local_day"], r["line_id"]): r["gal"] for r in view}
    checked = 0
    for r in accounting:
        key = (r["local_day"], lines[r["relay_slug"]])
        if key in mine:  # no_data days have no minutes rows
            assert mine[key] == r["gal"], key
            checked += 1
    assert checked > 20


def test_runtime_logins_read_and_the_api_login_is_denied(database):
    async def as_login(login):
        conn = await connect()
        try:
            await conn.execute(f'SET ROLE "{login}"')
            results = {}
            for name, sql in (
                ("minutes", "SELECT count(*) FROM public.v_irrigation_minutes_daily"),
                ("coverage", "SELECT count(*) FROM public.v_irrigation_day_coverage"),
                ("function", "SELECT count(*) FROM public.fn_irrigation_minutes_daily('2026-05-28', '2026-05-30')"),
                ("raw", "SELECT count(*) FROM public.equipment_state"),
            ):
                try:
                    results[name] = await conn.fetchval(sql)
                except asyncpg.PostgresError as exc:
                    results[name] = exc.sqlstate
            return results
        finally:
            await conn.close()

    async def acl():
        conn = await connect()
        try:
            relations = await conn.fetch(
                "SELECT c.relname, a.grantee::regrole::text AS grantee, a.privilege_type "
                "FROM pg_class c CROSS JOIN LATERAL aclexplode(c.relacl) a "
                "WHERE c.oid IN ('public.v_irrigation_minutes_daily'::regclass, 'public.v_irrigation_day_coverage'::regclass)"
            )
            function = await conn.fetch(
                "SELECT a.grantee, a.grantee::regrole::text AS name, a.privilege_type FROM pg_proc p "
                "CROSS JOIN LATERAL aclexplode(p.proacl) a "
                "WHERE p.oid = 'public.fn_irrigation_minutes_daily(date, date)'::regprocedure"
            )
            return relations, function
        finally:
            await conn.close()

    for login in ("verdify_mcp_runtime_login", "verdify_ingestor_runtime_login"):
        results = run(as_login(login))
        assert results["minutes"] > 0 and results["coverage"] > 0 and results["function"] > 0, (login, results)
        assert results["raw"] == "42501", login  # the views and function need no base-table grant
    api = run(as_login("verdify_api_runtime_login"))
    assert api == {"minutes": "42501", "coverage": "42501", "function": "42501", "raw": "42501"}
    relations, function = run(acl())
    readers = {(r["relname"], r["grantee"]) for r in relations if r["grantee"] != "verdify"}
    assert readers == {
        (view, duty)
        for view in ("v_irrigation_minutes_daily", "v_irrigation_day_coverage")
        for duty in ("verdify_mcp_runtime", "verdify_ingestor_runtime")
    }
    assert {r["privilege_type"] for r in relations if r["grantee"] != "verdify"} == {"SELECT"}
    assert {r["name"] for r in function} == {"verdify", "verdify_mcp_runtime", "verdify_ingestor_runtime"}
    assert all(r["grantee"] != 0 and r["privilege_type"] == "EXECUTE" for r in function)


def p95(samples):
    ordered = sorted(samples)
    return ordered[math.ceil(0.95 * len(ordered)) - 1]


def test_production_sized_thirty_day_read_stays_under_two_seconds_at_p95(database):
    """Two years of edges and meter events at the April-July 2026 density.

    The plain view rebuilds every run since the first edge on each read, so
    API-IRR-10 reads fn_irrigation_minutes_daily; its 30-day p95 is the gate.
    The view's time is reported for M3 (#1153), which keeps this case.
    """

    async def scenario():
        conn = await connect()
        try:
            tr = conn.transaction()
            await tr.start()
            try:
                for table in ("equipment_state", "water_meter_events", "data_gaps"):
                    await conn.execute(f"DELETE FROM public.{table}")
                now = await conn.fetchval("SELECT now()")
                edges, events, gaps = support.production_sized_rows(now)
                await support.load_production_sized(conn, edges, events, gaps)
                today = support.local_day(now)
                low = today - dt.timedelta(days=29)
                function, view = [], []
                for _ in range(20):
                    started = time.perf_counter()
                    rows = await conn.fetch("SELECT * FROM public.fn_irrigation_minutes_daily($1, $2)", low, today)
                    function.append(time.perf_counter() - started)
                for _ in range(3):
                    started = time.perf_counter()
                    view_rows = await conn.fetch(
                        "SELECT * FROM public.v_irrigation_minutes_daily WHERE local_day BETWEEN $1 AND $2", low, today
                    )
                    view.append(time.perf_counter() - started)
                started = time.perf_counter()
                long_rows = await conn.fetch(
                    "SELECT * FROM public.fn_irrigation_minutes_daily($1, $2)", today - dt.timedelta(days=399), today
                )
                long_read = time.perf_counter() - started
                return (
                    len(edges),
                    len(events),
                    {tuple(r) for r in rows},
                    {tuple(r) for r in view_rows},
                    function,
                    view,
                    long_rows,
                    long_read,
                )
            finally:
                await tr.rollback()
        finally:
            await conn.close()

    edges, events, rows, view_rows, function, view, long_rows, long_read = run(scenario())
    report = (
        f"hypertable={database} edges={edges} meter_events={events} function_p95={p95(function):.3f}s "
        f"view_max={max(view):.3f}s read_400d={long_read:.3f}s"
    )
    print(report)
    assert edges > 200_000 and events > 150_000, report
    assert rows == view_rows and len(rows) > 100, report
    assert len({r[1] for r in long_rows}) == 399, report
    assert p95(function) < 2.0, report
    assert long_read < 10.0, report
