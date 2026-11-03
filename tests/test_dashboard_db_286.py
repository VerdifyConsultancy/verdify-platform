"""Migration 286 (VD-33) on a private TimescaleDB: the lighting policies ignore manual rows.

jvallery/agents#5994's campaign step starts a fresh server for each
tests/test_dashboard_db_*.py file and sets VERDIFY_ISOLATED_TEST_DSN; without
it this file skips, and a skip fails campaign qualification. The test loads
db/schema.sql (whose two lighting policy functions are byte-identical to
production's), replays 275's jit=off block (db/schema.sql predates it), and
runs the statements between 286's $predecessor$ and $successor$ blocks. Those
two blocks compare production digest literals, so the rolled-back native
qualification on the production catalog proves them.
"""

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import pytest
from conftest import isolated_mutation_test_dsn

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db/migrations/286-lighting-policy-ignores-manual.sql"
JIT_MIGRATION = ROOT / "db/migrations/275-lighting-minutes-policy-bounded-jit.sql"
OWNER = "verdify"
P_TS = datetime(2026, 11, 2, 18, 0, tzinfo=UTC)
CIRCUIT = "public.fn_lighting_circuit_policy(timestamptz,text)"
MINUTES = "public.fn_lighting_minutes_policy(timestamptz,text)"
# Every parameter either function reads from setpoint_changes (migrations 179 and 185).
LEGACY = {
    "gl_dli_target": 13.0,
    "gl_sunrise_hour": 6.0,
    "gl_sunset_hour": 19.0,
    "gl_lux_threshold": 41000.0,
    "gl_lux_hysteresis": 7000.0,
    "sw_gl_auto_mode": 1.0,
}
CIRCUIT_VALUES = {
    "main": {"dli_target": 12.0, "target_light_minutes": 720.0, "sunrise_hour": 6.0, "sunset_hour": 18.0},
    "grow": {"dli_target": 22.0, "target_light_minutes": 960.0, "sunrise_hour": 6.0, "sunset_hour": 22.0},
}
POLICY = {
    **LEGACY,
    **{
        f"gl_{key}_{name}": value
        for key, values in CIRCUIT_VALUES.items()
        for name, value in {
            **values,
            "lux_threshold": 40000.0,
            "lux_hysteresis": 8000.0,
            "min_on_s": 120.0,
            "min_off_s": 60.0,
        }.items()
    },
    "sw_gl_main_auto_mode": 1.0,
    "sw_gl_grow_auto_mode": 1.0,
}
# A person's values: every one differs from the policy's, gl_main_dli_target 14 among them.
MANUAL = {
    param: (1.0 - value if param.startswith("sw_") else 14.0 if param == "gl_main_dli_target" else value + 1.0)
    for param, value in POLICY.items()
}

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


def block(path: Path, start: str, end: str) -> str:
    sql = path.read_text()
    assert sql.count(start) == 1 and sql.count(end) == 1
    return sql.split(start, 1)[1].split(end, 1)[0]


def migration_body() -> str:
    """The statements 286 runs between its predecessor and successor blocks."""
    return block(MIGRATION, "END $predecessor$;", "DO $successor$")


def jit_block() -> str:
    """275's own jit=off step, which production has and db/schema.sql predates."""
    return "DO $bounded_jit$" + block(JIT_MIGRATION, "DO $bounded_jit$", "END $bounded_jit$;") + "END $bounded_jit$;"


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


async def as_user(dsn: str, sql: str, user: str | None = None) -> None:
    conn = await connect(dsn, user)
    try:
        await conn.execute(sql)
    finally:
        await conn.close()


async def install(dsn: str) -> None:
    admin = await connect(dsn)
    try:
        if await admin.fetchval("SELECT to_regprocedure($1) IS NOT NULL", MINUTES):
            pytest.fail("test_dashboard_db_286 needs a fresh database: the lighting policy already exists")
    finally:
        await admin.close()
    await as_user(dsn, PRODUCTION_ROLES)
    await as_user(dsn, schema_script())  # its own session: the dump leaves search_path empty
    await as_user(dsn, "SET search_path = pg_catalog, public, pg_temp;\n" + jit_block(), OWNER)
    await as_user(
        dsn,
        "INSERT INTO public.greenhouses (id, name) VALUES ('vallery', 'Vallery') ON CONFLICT (id) DO NOTHING",
        OWNER,
    )


@pytest.fixture(scope="module")
def dsn():
    value = private_dsn()
    asyncio.run(install(value))
    return value


async def insert_rows(conn: asyncpg.Connection, values: dict[str, float], source: str, at: datetime) -> None:
    """Confirmed setpoint_changes rows, as a delivered push leaves them."""
    await conn.executemany(
        """INSERT INTO public.setpoint_changes (ts, parameter, value, source, greenhouse_id, confirmed_at, delivery_status)
           VALUES ($1, $2, $3, $4, 'vallery', $5, 'confirmed')""",
        [(at, param, value, source, at + timedelta(seconds=30)) for param, value in values.items()],
    )


async def outputs(conn: asyncpg.Connection) -> dict[str, list[dict]]:
    result = {}
    for name in ("fn_lighting_circuit_policy", "fn_lighting_minutes_policy"):
        rows = await conn.fetch(
            f"SELECT to_jsonb(t)::text AS row FROM public.{name}($1, 'vallery') t ORDER BY t.light_key", P_TS
        )
        result[name] = [json.loads(row["row"]) for row in rows]
    return result


async def catalog(conn: asyncpg.Connection) -> dict[str, dict]:
    # The migration's own comparison: every column but the source, the defaults deparsed.
    rows = await conn.fetch(
        """SELECT p.oid::regprocedure::text AS subject,
                  ((to_jsonb(p) - 'prosrc' - 'proargdefaults')
                       || jsonb_build_object('arguments', pg_catalog.pg_get_function_arguments(p.oid)))::text
                      AS metadata,
                  encode(sha256(convert_to(p.prosrc, 'UTF8')), 'hex') AS prosrc_sha256
             FROM pg_catalog.pg_proc p WHERE p.oid IN ($1::regprocedure, $2::regprocedure)""",
        CIRCUIT,
        MINUTES,
    )
    return {row["subject"]: {"metadata": row["metadata"], "sha": row["prosrc_sha256"]} for row in rows}


def test_manual_rows_stop_moving_either_lighting_policy_and_jit_stays_off(dsn):
    async def check():
        owner = await connect(dsn, OWNER)
        try:
            await owner.execute("SET search_path = pg_catalog, public, pg_temp")
            # The dispatcher's own confirmed pushes of the policy (source band), as production has.
            await insert_rows(owner, POLICY, "band", P_TS - timedelta(hours=3))
            before = await outputs(owner)
            assert len(before["fn_lighting_circuit_policy"]) == len(before["fn_lighting_minutes_policy"]) == 2
            main = next(row for row in before["fn_lighting_circuit_policy"] if row["light_key"] == "main")
            assert main["dli_target"] == POLICY["gl_main_dli_target"]

            # Before 286 a confirmed manual row is an override that sticks after its release.
            transaction = owner.transaction()
            await transaction.start()
            await insert_rows(owner, MANUAL, "manual", P_TS - timedelta(hours=1))
            sticky = await outputs(owner)
            await transaction.rollback()
            for name in before:
                assert sticky[name] != before[name], name
            main = next(row for row in sticky["fn_lighting_circuit_policy"] if row["light_key"] == "main")
            assert main["dli_target"] == 14.0

            old_catalog = await catalog(owner)
            await owner.execute(migration_body())
            new_catalog = await catalog(owner)
            for subject in old_catalog:  # owner, ACL, ROWS, volatility and SET clauses unchanged
                assert new_catalog[subject]["metadata"] == old_catalog[subject]["metadata"], subject
                assert new_catalog[subject]["sha"] != old_catalog[subject]["sha"], subject
            config = await owner.fetchval(
                "SELECT proconfig FROM pg_catalog.pg_proc WHERE oid = $1::regprocedure", MINUTES
            )
            assert config is not None and "jit=off" in config
            assert (
                await owner.fetchval("SELECT proconfig FROM pg_catalog.pg_proc WHERE oid = $1::regprocedure", CIRCUIT)
                is None
            )
            assert await outputs(owner) == before  # nothing moves without a manual row

            # After 286 the same manual rows leave both functions where they were.
            await insert_rows(owner, MANUAL, "manual", P_TS - timedelta(hours=1))
            assert await outputs(owner) == before

            # Only manual rows are ignored: a newer plan row still overrides.
            await insert_rows(owner, {"gl_main_dli_target": 16.0}, "plan", P_TS - timedelta(minutes=30))
            after_plan = await outputs(owner)
            main = next(row for row in after_plan["fn_lighting_circuit_policy"] if row["light_key"] == "main")
            minutes = next(row for row in after_plan["fn_lighting_minutes_policy"] if row["light_key"] == "main")
            assert main["dli_target"] == minutes["legacy_dli_target"] == 16.0
        finally:
            await owner.close()

    asyncio.run(check())


def test_286_refuses_a_second_run_on_its_own_successor(dsn):
    """Its predecessor digests no longer match once it ran: the body is not re-runnable."""

    async def check():
        owner = await connect(dsn, OWNER)
        try:
            await owner.execute("SET search_path = pg_catalog, public, pg_temp")
            if (
                await owner.fetchval(
                    "SELECT encode(sha256(convert_to(prosrc, 'UTF8')), 'hex') FROM pg_catalog.pg_proc "
                    "WHERE oid = $1::regprocedure",
                    CIRCUIT,
                )
                == "8d4688c4ff0386b3b939b968ae815b50bacb7eb73a9d18cbbdddf95861d17c96"
            ):
                await owner.execute(migration_body())  # this test ran first: bring the database to 286
            with pytest.raises(asyncpg.RaiseError, match="286 refuses an unexpected lighting policy predecessor"):
                await owner.execute(migration_body())
        finally:
            await owner.close()

    asyncio.run(check())
