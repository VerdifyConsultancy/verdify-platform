"""Migration 306 (#1433) on a private PostgreSQL: the SQL mirror of verdify_schemas.vpd_envelope, bit for bit.

jvallery/agents#5994's campaign step starts a fresh server for each tests/test_dashboard_db_*.py file and sets
VERDIFY_ISOLATED_TEST_DSN; without it this file skips. The test runs the statements between 306's $predecessor$ and
$successor$ blocks (the private server has no ledger or runtime receipts), then compares every function with its
Python twin over representative inputs: rounding ties, the Tetens pair, fits on noisy and exact training sets (with
skipped rows, too few hours and a singular set), the ribbon over a grid of temperatures and weather, the achievable
band and the grade. Floats travel as binary float8, so equality is exact.
"""

from __future__ import annotations

import asyncio
import math
import random
from pathlib import Path

import asyncpg
import pytest
from conftest import isolated_mutation_test_dsn

from verdify_schemas import vpd_envelope as ve

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db/migrations/306-vpd-envelope-sql-mirror.sql"
FUNCTIONS = 9


def migration_body() -> str:
    sql = MIGRATION.read_text()
    assert sql.count("END $predecessor$;") == 1 and sql.count("DO $successor$") == 1
    return sql.split("END $predecessor$;", 1)[1].split("DO $successor$", 1)[0]


def run(coro):
    return asyncio.run(coro)


async def fetch(dsn: str, sql: str, *args):
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


@pytest.fixture(scope="module")
def dsn():
    import os

    if not os.environ.get("VERDIFY_ISOLATED_TEST_DSN"):
        pytest.skip("needs the private PostgreSQL of the campaign step (VERDIFY_ISOLATED_TEST_DSN)")
    dsn = isolated_mutation_test_dsn()

    async def install():
        conn = await asyncpg.connect(dsn)
        try:
            async with conn.transaction():
                await conn.execute(migration_body())
        finally:
            await conn.close()

    run(install())
    return dsn


def column(dsn: str, sql: str, *args) -> list:
    return [row[0] for row in run(fetch(dsn, sql, *args))]


def rows(seed: int, count: int, *, noise: float, holes: bool) -> list[dict]:
    """Deep-night-like hours: house 50-70 °F, outside 15-55 °F at 25-98 % RH, indoor dewpoint near a linear model."""
    rng = random.Random(seed)  # noqa: S311 - reproducible test inputs
    out = []
    for index in range(count):
        t_in, t_out, rh_out = rng.uniform(50, 70), rng.uniform(15, 55), rng.uniform(25, 98)
        td_out = ve.outdoor_dewpoint_f(t_out, rh_out)
        td_in = 4.2 + 0.55 * t_in + 0.08 * t_out + 0.31 * td_out + rng.gauss(0, noise)
        row = {"t_in": t_in, "td_in": td_in, "t_out": t_out, "rh_out": rh_out}
        if holes and index % 17 == 3:
            row[("t_in", "td_in", "t_out", "rh_out")[index % 4]] = None
        if holes and index % 29 == 5:
            row["rh_out"] = 0.0
        out.append(row)
    return out


def sql_fit(dsn: str, training: list[dict]):
    found = run(
        fetch(
            dsn,
            "SELECT * FROM public.fn_vpd_envelope_fit($1::float8[], $2::float8[], $3::float8[], $4::float8[])",
            *([row[key] for row in training] for key in ("t_in", "td_in", "t_out", "rh_out")),
        )
    )
    assert len(found) <= 1
    if not found:
        return None
    row = found[0]
    return ve.DewpointFit((row["a"], row["b"], row["c"], row["e"]), row["rmse_f"], row["hours"], row["dewpoint_p10_f"])


def test_the_functions_are_verdifys_own_with_no_grant(dsn):
    found = run(
        fetch(
            dsn,
            "SELECT p.proname, p.prosecdef, p.provolatile::text AS provolatile, "
            "EXISTS(SELECT 1 FROM aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner))) x "
            "WHERE x.grantee <> p.proowner) AS granted "
            "FROM pg_proc p WHERE p.pronamespace = 'public'::regnamespace AND p.proname LIKE 'fn\\_vpd\\_envelope\\_%'",
        )
    )
    assert len(found) == FUNCTIONS
    assert all(not row["prosecdef"] and row["provolatile"] == "i" and not row["granted"] for row in found)


def test_round_is_pythons_round_including_ties(dsn):
    rng = random.Random(306)  # noqa: S311 - reproducible test inputs
    values = [0.0, -0.0, 0.0625, 0.1875, -0.0625, 0.7425, 0.7435, 2.675, -2.675, 1.0005, 0.0005, 0.5, 2.5, 1e-7]
    values += [k / 1000 + 0.0005 for k in range(-1500, 1500)]
    values += [k / 100 + 0.005 for k in range(-1500, 1500)]
    values += [k / 16 for k in range(-200, 200)]
    values += [rng.uniform(-80, 120) for _ in range(3000)]
    values += [math.nextafter(v, math.inf) for v in values[:200]] + [math.nextafter(v, -math.inf) for v in values[:200]]
    for digits in (0, 2, 3):
        got = column(
            dsn,
            "SELECT public.fn_vpd_envelope_round(v, $2) FROM unnest($1::float8[]) WITH ORDINALITY u(v, i) ORDER BY i",
            values,
            digits,
        )
        mismatched = [(v, g, round(v, digits)) for v, g in zip(values, got, strict=True) if g != round(v, digits)]
        assert not mismatched[:5], digits


def test_tetens_pair_and_outdoor_dewpoint_match(dsn):
    temps = [t / 4 for t in range(-80, 480)]
    vapours = [v / 400 for v in range(0, 2400)]
    assert column(
        dsn,
        "SELECT public.fn_vpd_envelope_es_kpa(t) FROM unnest($1::float8[]) WITH ORDINALITY u(t, i) ORDER BY i",
        temps,
    ) == [ve.es_kpa(t) for t in temps]
    assert column(
        dsn,
        "SELECT public.fn_vpd_envelope_dewpoint_f(v) FROM unnest($1::float8[]) WITH ORDINALITY u(v, i) ORDER BY i",
        vapours,
    ) == [ve.dewpoint_f(v) for v in vapours]
    pairs = [(t, rh) for t in range(-10, 100, 3) for rh in (1, 7.5, 25, 42.3, 60, 88.8, 100)]
    assert column(
        dsn,
        "SELECT public.fn_vpd_envelope_outdoor_dewpoint_f(t, rh) "
        "FROM unnest($1::float8[], $2::float8[]) WITH ORDINALITY u(t, rh, i) ORDER BY i",
        [float(p[0]) for p in pairs],
        [float(p[1]) for p in pairs],
    ) == [ve.outdoor_dewpoint_f(t, rh) for t, rh in pairs]


def test_is_night_and_grade_match(dsn):
    phases = [None, 0.0, 1.0, 1.999, 2.0, 2.5, 3.0, 3.99]
    assert [
        row[0]
        for row in run(fetch(dsn, "SELECT public.fn_vpd_envelope_is_night(p) FROM unnest($1::float8[]) p", phases))
    ] == [ve.is_night(p) for p in phases]
    cases = [
        (None, 0.7, 1.1),
        (0.9, None, 1.1),
        (0.9, 0.7, None),
        (0.69, 0.7, 1.1),
        (0.7, 0.7, 1.1),
        (0.9, 0.7, 1.1),
        (1.1, 0.7, 1.1),
        (1.11, 0.7, 1.1),
    ]
    for value, low, high in cases:
        got = run(fetch(dsn, "SELECT public.fn_vpd_envelope_grade($1, $2, $3)", value, low, high))[0][0]
        assert got == ve.grade(value, low, high), (value, low, high)


def test_fit_matches_bit_for_bit_and_refuses_where_python_does(dsn):
    sets = {
        "noisy 14 nights": rows(1, 14 * 11, noise=1.4, holes=True),
        "noisy small": rows(2, 40, noise=2.5, holes=True),
        "exact": rows(3, 60, noise=0.0, holes=False),
        "exactly 24": rows(4, 24, noise=0.8, holes=False),
        "23 after skips": rows(5, 24, noise=0.8, holes=False)[:23]
        + [{"t_in": 60.0, "td_in": 40.0, "t_out": 30.0, "rh_out": 0.0}],
        "singular": [{"t_in": 60.0, "td_in": 40.0 + i % 3, "t_out": 30.0, "rh_out": 50.0} for i in range(30)],
        "none": [],
    }
    for name, training in sets.items():
        assert sql_fit(dsn, training) == ve.fit_dewpoint(training), name
    assert ve.fit_dewpoint(sets["noisy 14 nights"]) is not None
    assert ve.fit_dewpoint(sets["exactly 24"]).hours == 24
    assert ve.fit_dewpoint(sets["23 after skips"]) is None and ve.fit_dewpoint(sets["singular"]) is None


def test_the_ribbon_and_the_achievable_band_match(dsn):
    fits = [ve.fit_dewpoint(rows(seed, 120, noise=noise, holes=True)) for seed, noise in ((11, 1.2), (12, 3.0))]
    fits.append(ve.DewpointFit((5.0, 0.6, 0.1, 0.2), 0.0, 24, 40.0))
    grid = [
        (temp, t_out, ve.outdoor_dewpoint_f(t_out, rh))
        for temp in (48.0, 52.5, 55.0, 58.25, 60.8, 61.8, 63.0, 66.5, 70.0, 75.0)
        for t_out in (12.0, 25.0, 43.0, 55.0)
        for rh in (20.0, 55.0, 95.0)
    ]
    for fit in fits:
        a, b, c, e = fit.coef
        got = run(
            fetch(
                dsn,
                "SELECT (public.fn_vpd_envelope_expected($1, $2, $3, $4, $5, t, o, d)).* "
                "FROM unnest($6::float8[], $7::float8[], $8::float8[]) WITH ORDINALITY u(t, o, d, i) ORDER BY i",
                a,
                b,
                c,
                e,
                fit.rmse_f,
                *([point[k] for point in grid] for k in range(3)),
            )
        )
        expected = [ve.expected_vpd(fit, *point) for point in grid]
        assert [(row["p25"], row["p75"]) for row in got] == expected
    ribbons = [(0.31, 0.52), (0.8, 0.9), (0.7425, 1.2), (0.1, 1.3335), (0.0625, 0.1875), None]
    bands = [(0.74, 1.17), (0.7425, 1.1675), (None, 1.17), (0.74, None), (0.0, 0.0)]
    for low, high in bands:
        for ribbon in ribbons:
            p25, p75 = ribbon if ribbon is not None else (None, None)
            row = run(
                fetch(dsn, "SELECT * FROM public.fn_vpd_envelope_achievable_band($1, $2, $3, $4)", low, high, p25, p75)
            )[0]
            assert (row["vpd_low"], row["vpd_high"]) == ve.achievable_band(low, high, ribbon), (low, high, ribbon)
