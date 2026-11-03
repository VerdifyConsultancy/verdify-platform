"""Migration 280 (VD-30) on a private TimescaleDB: grants, the wake and idempotency, as each login.

jvallery/agents#5994's campaign step starts a fresh server for each
tests/test_dashboard_db_*.py file and sets VERDIFY_ISOLATED_TEST_DSN; without
it this file skips, and a skip fails campaign qualification. The test loads
db/schema.sql, creates the ingestor and MCP duties and logins with migration
217's and 259's attributes, then runs the statements between the migration's
$predecessor$ and $successor$ blocks. Those two blocks compare production
digest literals, so the rolled-back native qualification on the production
catalog proves them and the first sync proves attestation live.
"""

import asyncio
import os
import uuid
from pathlib import Path

import asyncpg
import pytest
from conftest import isolated_mutation_test_dsn

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db/migrations/280-desired-state-activity-log.sql"
INGESTOR = "verdify_ingestor_runtime_login"
MCP = "verdify_mcp_runtime_login"
OWNER = "verdify"
DENIED = asyncpg.exceptions.InsufficientPrivilegeError  # SQLSTATE 42501

# Production's owner is the bootstrap superuser; db/schema.sql also grants to twin_ro.
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

# Migration 217 (ingestor) and 258/259 (MCP): a NOLOGIN NOINHERIT duty and an
# INHERIT login that holds only that duty; USAGE on public as both granted it.
RUNTIME_ROLES = """
DO $runtime$
DECLARE duty text;
BEGIN
    FOREACH duty IN ARRAY ARRAY['verdify_ingestor_runtime', 'verdify_mcp_runtime'] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = duty) THEN
            EXECUTE format('CREATE ROLE %I NOLOGIN', duty);
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = duty || '_login') THEN
            EXECUTE format('CREATE ROLE %I LOGIN', duty || '_login');
        END IF;
        EXECUTE format('ALTER ROLE %I NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE '
                       'NOREPLICATION NOBYPASSRLS', duty);
        EXECUTE format('ALTER ROLE %I LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE '
                       'NOREPLICATION NOBYPASSRLS', duty || '_login');
        EXECUTE format('ALTER ROLE %I SET search_path = pg_catalog, public, pg_temp', duty);
        EXECUTE format('ALTER ROLE %I SET search_path = pg_catalog, public, pg_temp', duty || '_login');
        EXECUTE format('GRANT %I TO %I WITH ADMIN FALSE, INHERIT TRUE, SET TRUE', duty, duty || '_login');
        EXECUTE format('GRANT USAGE ON SCHEMA public TO %I', duty);
    END LOOP;
END $runtime$;
-- The predecessor reads these checks rely on: MCP reads both tables (259, 261).
GRANT SELECT ON public.setpoint_changes, public.crop_band_anchors TO verdify_mcp_runtime;
"""


def migration_body() -> str:
    """The statements 280 runs between its predecessor and successor blocks."""
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


async def install(dsn: str) -> None:
    admin = await connect(dsn)
    try:
        if await admin.fetchval("SELECT to_regclass('public.desired_state') IS NOT NULL"):
            pytest.fail("test_dashboard_db_280 needs a fresh database: public.desired_state already exists")
    finally:
        await admin.close()
    await as_admin(dsn, PRODUCTION_ROLES)
    # Its own session: the dump creates the extensions first and leaves search_path empty.
    await as_admin(dsn, schema_script())
    await as_admin(dsn, RUNTIME_ROLES)
    owner = await connect(dsn, OWNER)
    try:
        await owner.execute("SET search_path = pg_catalog, public, pg_temp;\n" + migration_body())
        await owner.execute(
            "INSERT INTO public.greenhouses (id, name) VALUES ('vallery', 'Vallery') ON CONFLICT (id) DO NOTHING"
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


async def denied(dsn: str, user: str, sql: str, *args) -> str:
    conn = await connect(dsn, user)
    try:
        with pytest.raises(DENIED) as raised:
            await conn.execute(sql, *args)
        return raised.value.sqlstate
    finally:
        await conn.close()


async def fetch(dsn: str, user: str | None, sql: str, *args):
    conn = await connect(dsn, user)
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


def test_mcp_writes_desired_state_and_never_setpoint_changes(dsn):
    async def check():
        rows = await fetch(
            dsn,
            MCP,
            "INSERT INTO public.desired_state (param, value, manual, source, actor, reason) "
            "VALUES ('mister_engage_kpa', 1.55, true, 'agent:db-test', 'db-test', 'campaign') "
            "RETURNING id, greenhouse_id, created_at",
        )
        assert len(rows) == 1 and rows[0]["id"] > 0 and rows[0]["greenhouse_id"] == "vallery"
        insert = "INSERT INTO public.setpoint_changes (ts, parameter, value, source) VALUES (now(), $1, $2, 'manual')"
        for login in (MCP, INGESTOR):
            assert await denied(dsn, login, insert, "mister_engage_kpa", 1.55) == "42501"

    run(check())


def test_new_tables_are_append_only_with_the_exact_grants(dsn):
    async def check():
        privileges = await fetch(
            dsn,
            None,
            """SELECT l.login, r.relation, array_agg(p.privilege ORDER BY p.privilege) FILTER (
                       WHERE has_table_privilege(l.login, r.relation, p.privilege)) AS granted
                 FROM unnest($1::text[]) l(login)
                 CROSS JOIN unnest($2::text[]) r(relation)
                 CROSS JOIN unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER'])
                      p(privilege)
                GROUP BY l.login, r.relation""",
            [MCP, INGESTOR, "public"],
            ["public.desired_state", "public.v_desired_state_current", "public.activity_log"],
        )
        granted = {(row["login"], row["relation"]): set(row["granted"] or ()) for row in privileges}
        assert granted == {
            (MCP, "public.desired_state"): {"INSERT", "SELECT"},
            (MCP, "public.v_desired_state_current"): {"SELECT"},
            (MCP, "public.activity_log"): {"INSERT", "SELECT"},
            (INGESTOR, "public.desired_state"): {"SELECT"},
            (INGESTOR, "public.v_desired_state_current"): {"SELECT"},
            (INGESTOR, "public.activity_log"): {"INSERT"},
            ("public", "public.desired_state"): set(),
            ("public", "public.v_desired_state_current"): set(),
            ("public", "public.activity_log"): set(),
        }
        sequences = await fetch(
            dsn,
            None,
            """SELECT has_sequence_privilege($1, 'public.desired_state_id_seq', 'USAGE') AS mcp_desired,
                      has_sequence_privilege($1, 'public.activity_log_id_seq', 'USAGE') AS mcp_activity,
                      has_sequence_privilege($2, 'public.desired_state_id_seq', 'USAGE,SELECT,UPDATE') AS ing_desired,
                      has_sequence_privilege($2, 'public.activity_log_id_seq', 'USAGE') AS ing_activity,
                      has_sequence_privilege($2, 'public.activity_log_id_seq', 'SELECT,UPDATE') AS ing_activity_more,
                      has_function_privilege('public', 'public.fn_desired_state_notify()', 'EXECUTE') AS public_notify""",
            MCP,
            INGESTOR,
        )
        assert dict(sequences[0]) == {
            "mcp_desired": True,
            "mcp_activity": True,
            "ing_desired": False,
            "ing_activity": True,
            "ing_activity_more": False,
            "public_notify": False,
        }
        for table in ("public.desired_state", "public.activity_log"):
            for statement in (f"UPDATE {table} SET reason = 'x'", f"DELETE FROM {table}", f"TRUNCATE {table}"):
                for login in (MCP, INGESTOR):
                    assert await denied(dsn, login, statement) == "42501"
        assert (
            await denied(
                dsn,
                INGESTOR,
                "INSERT INTO public.desired_state (param, value, source, actor) VALUES ('x', 1, 'x', 'x')",
            )
            == "42501"
        )
        assert await denied(dsn, INGESTOR, "SELECT * FROM public.activity_log") == "42501"
        # The forecast-rule rows of #1178: the ingestor appends through the sequence default.
        await fetch(
            dsn,
            INGESTOR,
            "INSERT INTO public.activity_log (source, actor, action, target, reason) "
            "VALUES ('system', 'forecast-engine', 'plan.write', 'preemptive-db-test', 'high wind')",
        )
        for relation in ("public.desired_state", "public.v_desired_state_current"):
            await fetch(dsn, INGESTOR, f"SELECT count(*) FROM {relation}")  # the dispatcher's reads

    run(check())


def test_current_view_returns_the_newest_row_and_the_checks_hold(dsn):
    async def check():
        insert = "INSERT INTO public.desired_state (param, value, manual, source, actor) VALUES ($1, $2, $3, $4, $5)"
        mcp = await connect(dsn, MCP)
        try:
            await mcp.execute(insert, "view_test", 1.0, False, "iris", "iris")
            await mcp.execute(insert, "view_test", 2.0, True, "dashboard", "jason")
            await mcp.execute(insert, "view_test", None, False, "dashboard", "jason")
            # Rows from one statement share created_at; the larger id wins.
            await mcp.execute(
                "INSERT INTO public.desired_state (param, value, source, actor) "
                "VALUES ('tie_test', 5, 'agent:db-test', 'db-test'), ('tie_test', 6, 'agent:db-test', 'db-test')"
            )
            with pytest.raises(asyncpg.exceptions.CheckViolationError):
                await mcp.execute(insert, "manual_release", None, True, "dashboard", "jason")
            reason = (
                "INSERT INTO public.desired_state (param, value, source, actor, reason) VALUES ('r', 1, 'x', 'x', $1)"
            )
            await mcp.execute(reason, "r" * 500)
            with pytest.raises(asyncpg.exceptions.CheckViolationError):
                await mcp.execute(reason, "r" * 501)
        finally:
            await mcp.close()
        rows = await fetch(
            dsn,
            INGESTOR,
            "SELECT param, value, manual, source FROM public.v_desired_state_current "
            "WHERE param IN ('view_test', 'tie_test') ORDER BY param",
        )
        assert [tuple(row) for row in rows] == [
            ("tie_test", 6.0, False, "agent:db-test"),
            ("view_test", None, False, "dashboard"),
        ]

    run(check())


def test_one_insert_statement_sends_exactly_one_empty_wake(dsn):
    async def check():
        trigger = await fetch(
            dsn,
            None,
            "SELECT tgtype, tgenabled FROM pg_trigger WHERE tgname = 'trg_desired_state_notify' "
            "AND tgrelid = 'public.desired_state'::regclass",
        )
        # 4 = AFTER INSERT FOR EACH STATEMENT (no ROW, BEFORE or INSTEAD bits).
        # asyncpg returns the "char" type as bytes.
        assert [tuple(row) for row in trigger] == [(4, b"O")]
        received: list[tuple[str, str]] = []
        first = asyncio.Event()

        def wake(_connection, _pid, channel, payload):
            received.append((channel, payload))
            first.set()

        listener = await connect(dsn, INGESTOR)
        writer = await connect(dsn, MCP)
        try:
            await listener.add_listener("desired_state_changed", wake)
            await writer.execute(
                "INSERT INTO public.desired_state (param, value, source, actor) VALUES "
                "('wake_a', 1, 'agent:db-test', 'db-test'), ('wake_b', 2, 'agent:db-test', 'db-test'), "
                "('wake_c', 3, 'agent:db-test', 'db-test')"
            )
            await asyncio.wait_for(first.wait(), timeout=10)
            await asyncio.sleep(1.0)  # any second notification would arrive in this window
        finally:
            await writer.close()
            await listener.close()
        assert received == [("desired_state_changed", "")]

    run(check())


def test_request_id_applies_once_per_source(dsn):
    async def check():
        insert = (
            "INSERT INTO public.activity_log (source, actor, action, target, detail, request_id, request_sha256, "
            "experiment_id) VALUES ($1, 'db-test', 'setting.write', 'mister_engage_kpa', '{\"value\": 1.55}', $2, "
            "$3, $4)"
        )
        request, experiment = uuid.uuid4(), uuid.uuid4()
        mcp = await connect(dsn, MCP)
        try:
            await mcp.execute(insert, "agent:db-test", request, b"\x01" * 32, experiment)
            with pytest.raises(asyncpg.exceptions.UniqueViolationError):
                await mcp.execute(insert, "agent:db-test", request, b"\x02" * 32, None)
            await mcp.execute(insert, "dashboard", request, b"\x01" * 32, None)  # another source may reuse it
            await mcp.execute(insert, "agent:db-test", None, None, None)
            await mcp.execute(insert, "agent:db-test", None, None, None)  # NULL request ids never collide
            rows = await mcp.fetch(
                "SELECT source, request_id, experiment_id, greenhouse_id FROM public.activity_log "
                "WHERE target = 'mister_engage_kpa' ORDER BY id"
            )
        finally:
            await mcp.close()
        assert [(row["source"], row["request_id"]) for row in rows] == [
            ("agent:db-test", request),
            ("dashboard", request),
            ("agent:db-test", None),
            ("agent:db-test", None),
        ]
        assert rows[0]["experiment_id"] == experiment and {row["greenhouse_id"] for row in rows} == {"vallery"}

    run(check())


def test_mcp_updates_only_the_three_band_columns(dsn):
    async def check():
        owner = await connect(dsn, OWNER)
        try:
            anchor = await owner.fetchval(
                "INSERT INTO public.crop_band_anchors (crop_type, series, anchor, value, width_below, width_above, "
                "updated_at) VALUES ('orchid', 'vpd_target', 'sm', 0.8, 0.1, 0.1, now() - interval '1 day') "
                "RETURNING id"
            )
        finally:
            await owner.close()
        mcp = await connect(dsn, MCP)
        try:
            status = await mcp.execute(
                "UPDATE public.crop_band_anchors SET value = 0.85, width_below = 0.12, width_above = 0.13 "
                "WHERE id = $1",
                anchor,
            )
            assert status == "UPDATE 1"
            row = await mcp.fetchrow(
                "SELECT value, width_below, width_above, updated_at > now() - interval '1 hour' AS touched "
                "FROM public.crop_band_anchors WHERE id = $1",
                anchor,
            )
        finally:
            await mcp.close()
        assert tuple(row) == (0.85, 0.12, 0.13, True)  # the existing trigger sets updated_at
        for statement in (
            "UPDATE public.crop_band_anchors SET crop_type = 'x' WHERE id = $1",
            "UPDATE public.crop_band_anchors SET updated_at = now() WHERE id = $1",
            "DELETE FROM public.crop_band_anchors WHERE id = $1",
        ):
            assert await denied(dsn, MCP, statement, anchor) == "42501"
        assert await denied(dsn, INGESTOR, "UPDATE public.crop_band_anchors SET value = 1 WHERE id = $1", anchor) == (
            "42501"
        )

    run(check())
