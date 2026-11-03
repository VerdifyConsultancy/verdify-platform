"""Migration 282 (VD-66) on a private TimescaleDB: the dashboard login, its guardrails and sessions.

jvallery/agents#5994's campaign step starts a fresh server for each
tests/test_dashboard_db_*.py file and sets VERDIFY_ISOLATED_TEST_DSN; without
it this file skips, and a skip fails campaign qualification. The test loads
db/schema.sql, creates the ingestor and MCP duties as test_dashboard_db_280
does, adds empty owner-only stand-ins for the five experiment and policy tables
the dump predates (the attestation checks the login holds nothing on them),
then runs the statements between 280's and 282's $predecessor$ and $successor$
blocks. Those blocks compare production digest literals, so the rolled-back
native qualification proves the seals on the production catalog and the first
sync proves them live. 282 grants only on its own objects; the reads and writes
on existing objects arrive with their own successor and its database test.
"""

import asyncio
import hashlib
import os
from pathlib import Path

import asyncpg
import pytest
from conftest import isolated_mutation_test_dsn

ROOT = Path(__file__).resolve().parents[1]
PREDECESSOR = ROOT / "db/migrations/280-desired-state-activity-log.sql"
MIGRATION = ROOT / "db/migrations/282-dashboard-runtime-login.sql"
DUTY = "verdify_dashboard_runtime"
LOGIN = "verdify_dashboard_runtime_login"
MCP = "verdify_mcp_runtime_login"
OWNER = "verdify"
DENIED = asyncpg.exceptions.InsufficientPrivilegeError  # SQLSTATE 42501
ATTEST = f"SELECT {DUTY}.fn_attest_login()"
SETTINGS = {
    f"search_path={DUTY}, pg_catalog, public, pg_temp",
    "statement_timeout=10s",
    "idle_in_transaction_session_timeout=15s",
}
STAND_INS = (
    "control_experiments",
    "control_assignments",
    "effective_policy_vectors",
    "policy_delivery_outbox",
    "policy_exposures",
)

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

# Migration 217 (ingestor) and 258/259 (MCP), which 280's grants name: a NOLOGIN
# NOINHERIT duty and an INHERIT login that holds only that duty.
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
        EXECUTE format('GRANT %I TO %I WITH ADMIN FALSE, INHERIT TRUE, SET TRUE', duty, duty || '_login');
        EXECUTE format('GRANT USAGE ON SCHEMA public TO %I', duty);
    END LOOP;
END $runtime$;
"""

# The dump predates the experiment and policy tables; production owns them with
# no dashboard grant. Each stand-in is an empty owner-only table of that name.
# Production's public schema gives PUBLIC USAGE and no CREATE, as PostgreSQL 15+ does.
STAND_IN_TABLES = "REVOKE CREATE ON SCHEMA public FROM PUBLIC;\n" + "\n".join(
    f"CREATE TABLE IF NOT EXISTS public.{name} (id bigint PRIMARY KEY);" for name in STAND_INS
)

# Each statement breaks one property the attestation must hold.
BROKEN = (
    f"GRANT INSERT ON public.setpoint_changes TO {DUTY}",
    f"GRANT DELETE ON public.setpoint_changes TO {LOGIN}",
    f"GRANT UPDATE (value) ON public.setpoint_plan TO {DUTY}",
    *(f"GRANT SELECT ON public.{name} TO {DUTY}" for name in STAND_INS),
    f"GRANT REFERENCES (id) ON public.control_experiments TO {DUTY}",
    f"GRANT CREATE ON SCHEMA {DUTY} TO {DUTY}",
    f"GRANT CREATE ON SCHEMA public TO {LOGIN}",
    f"GRANT twin_ro TO {LOGIN}",
    f"GRANT {DUTY} TO twin_ro",
    f"ALTER ROLE {DUTY} INHERIT",
    f"ALTER ROLE {LOGIN} CREATEDB",
    f"ALTER ROLE {LOGIN} CONNECTION LIMIT 20",
    f"ALTER ROLE {LOGIN} RESET statement_timeout",
    f"ALTER ROLE {LOGIN} SET idle_in_transaction_session_timeout = '1h'",
    f"ALTER ROLE {LOGIN} RESET ALL",  # no role settings left: false, not NULL
)


def migration_body(path: Path) -> str:
    """The statements a migration runs between its predecessor and successor blocks."""
    sql = path.read_text()
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
        fresh = await admin.fetchval(
            "SELECT to_regclass('public.dashboard_session') IS NULL AND to_regnamespace($1::text) IS NULL "
            "AND NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname IN ($1::text, $2::text))",
            DUTY,
            LOGIN,
        )
        if not fresh:
            pytest.fail("test_dashboard_db_282 needs a fresh server: a dashboard role or object already exists")
    finally:
        await admin.close()
    await as_admin(dsn, PRODUCTION_ROLES)
    # Its own session: the dump creates the extensions first and leaves search_path empty.
    await as_admin(dsn, schema_script())
    await as_admin(dsn, RUNTIME_ROLES)
    owner = await connect(dsn, OWNER)
    try:
        prefix = "SET search_path = pg_catalog, public, pg_temp;\n"
        await owner.execute(prefix + STAND_IN_TABLES)
        await owner.execute(prefix + migration_body(PREDECESSOR))
        await owner.execute(prefix + migration_body(MIGRATION))
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


async def attest_as_login_after(dsn: str, statement: str | None) -> bool:
    """fn_attest_login() under SET SESSION AUTHORIZATION, as the native qualification calls
    it, after one uncommitted change by the owner; the rollback undoes both."""
    owner = await connect(dsn, OWNER)
    transaction = owner.transaction()
    await transaction.start()
    try:
        if statement:
            await owner.execute(statement)
        await owner.execute(f"SET SESSION AUTHORIZATION {LOGIN}")
        return await owner.fetchval(ATTEST)
    finally:
        await transaction.rollback()
        await owner.close()


async def no_login_sessions(dsn: str) -> None:
    """Wait for earlier login backends to exit; they count against the connection limit."""
    admin = await connect(dsn)
    try:
        for _ in range(100):
            if not await admin.fetchval("SELECT count(*) FROM pg_stat_activity WHERE usename = $1", LOGIN):
                return
            await asyncio.sleep(0.1)
        pytest.fail("earlier dashboard login sessions did not end")
    finally:
        await admin.close()


def test_roles_settings_and_membership_are_exact(dsn):
    async def check():
        admin = await connect(dsn)
        try:
            roles = {
                row["rolname"]: row
                for row in await admin.fetch(
                    "SELECT r.rolname, r.rolcanlogin, r.rolinherit, r.rolsuper, r.rolcreatedb, r.rolcreaterole, "
                    "r.rolreplication, r.rolbypassrls, r.rolconnlimit, r.rolconfig, a.rolpassword IS NULL AS unset "
                    "FROM pg_roles r JOIN pg_authid a ON a.oid = r.oid WHERE r.rolname IN ($1, $2)",
                    DUTY,
                    LOGIN,
                )
            }
            members = await admin.fetch(
                "SELECT g.rolname AS role, m.rolname AS member, a.admin_option, a.inherit_option, a.set_option "
                "FROM pg_auth_members a JOIN pg_roles g ON g.oid = a.roleid JOIN pg_roles m ON m.oid = a.member "
                "WHERE g.rolname IN ($1, $2) OR m.rolname IN ($1, $2)",
                DUTY,
                LOGIN,
            )
        finally:
            await admin.close()
        flags = ("rolcanlogin", "rolinherit", "rolsuper", "rolcreatedb", "rolcreaterole", "rolreplication")
        assert [roles[DUTY][flag] for flag in (*flags, "rolbypassrls")] == [False] * 7
        assert [roles[LOGIN][flag] for flag in (*flags, "rolbypassrls")] == [True, True] + [False] * 5
        assert (roles[DUTY]["rolconnlimit"], roles[LOGIN]["rolconnlimit"]) == (-1, 8)
        assert roles[DUTY]["rolconfig"] is None and set(roles[LOGIN]["rolconfig"]) == SETTINGS
        assert len(roles[LOGIN]["rolconfig"]) == 3
        assert roles[DUTY]["unset"] and roles[LOGIN]["unset"]  # no password until the R1 wiring PR
        assert [tuple(row) for row in members] == [(DUTY, LOGIN, False, True, True)]

    run(check())


def test_the_login_session_carries_the_guardrails(dsn):
    async def check():
        conn = await connect(dsn, LOGIN)
        try:
            row = await conn.fetchrow(
                "SELECT current_user::text, session_user::text, current_setting('statement_timeout'), "
                "current_setting('idle_in_transaction_session_timeout'), current_setting('search_path')"
            )
        finally:
            await conn.close()
        assert tuple(row) == (LOGIN, LOGIN, "10s", "15s", f"{DUTY}, pg_catalog, public, pg_temp")

    run(check())


def test_the_ninth_concurrent_login_connection_is_refused(dsn):
    async def check():
        await no_login_sessions(dsn)
        held = []
        try:
            for _ in range(8):
                held.append(await connect(dsn, LOGIN))
            with pytest.raises(asyncpg.exceptions.TooManyConnectionsError):  # SQLSTATE 53300
                held.append(await connect(dsn, LOGIN))
        finally:
            for conn in held:
                await conn.close()

    run(check())


def test_the_login_attests_and_no_other_login_can_call_its_attestor(dsn):
    async def check():
        conn = await connect(dsn, LOGIN)
        try:
            assert await conn.fetchval(ATTEST) is True
        finally:
            await conn.close()
        assert await attest_as_login_after(dsn, None) is True
        assert await denied(dsn, MCP, ATTEST) == "42501"
        owner = await connect(dsn, OWNER)
        try:
            await owner.execute(f"SET ROLE {LOGIN}")  # current_user is the login, session_user is not
            assert await owner.fetchval(ATTEST) is False
        finally:
            await owner.close()

    run(check())


@pytest.mark.parametrize("statement", BROKEN)
def test_each_broken_boundary_fails_attestation(dsn, statement):
    assert run(attest_as_login_after(dsn, statement)) is False


def test_the_login_keeps_its_sessions(dsn):
    async def check():
        insert = (
            "INSERT INTO public.dashboard_session (token_sha256, username, display_name, role, expires_at) "
            "VALUES ($1, $2, $3, $4, now() + interval '12 hours')"
        )
        token, other = hashlib.sha256(b"vd-66 session").digest(), hashlib.sha256(b"vd-66 other").digest()
        conn = await connect(dsn, LOGIN)
        transaction = conn.transaction()
        await transaction.start()
        try:
            await conn.execute(insert, token, "jason", "Jason", "operator")
            await conn.execute(insert, other, "caretaker", None, "viewer")
            row = await conn.fetchrow(
                "SELECT username, display_name, role, created_at <= now() AS stamped, expires_at > now() AS live "
                "FROM public.dashboard_session WHERE token_sha256 = $1",
                token,
            )
            assert tuple(row) == ("jason", "Jason", "operator", True, True)
            assert (
                await conn.execute(
                    "UPDATE public.dashboard_session SET expires_at = now() - interval '1 second' WHERE token_sha256 = $1",
                    token,
                )
                == "UPDATE 1"
            )
            # The operator API's hourly sweep deletes only expired rows.
            assert await conn.execute("DELETE FROM public.dashboard_session WHERE expires_at <= now()") == "DELETE 1"
            assert await conn.fetchval("SELECT array_agg(username) FROM public.dashboard_session") == ["caretaker"]
            for statement, args, error in (
                (insert, (token, "jason", None, "admin"), asyncpg.exceptions.CheckViolationError),
                (insert, (other, "jason", None, "viewer"), asyncpg.exceptions.UniqueViolationError),
                (insert, (token, None, None, "viewer"), asyncpg.exceptions.NotNullViolationError),
                ("TRUNCATE public.dashboard_session", (), DENIED),
            ):
                with pytest.raises(error):
                    async with conn.transaction():  # a savepoint keeps the outer transaction usable
                        await conn.execute(statement, *args)
        finally:
            await transaction.rollback()
            await conn.close()

    run(check())


def test_the_login_cannot_write_setpoints_or_read_experiments(dsn):
    async def check():
        insert = "INSERT INTO public.setpoint_changes (ts, parameter, value, source) VALUES (now(), $1, $2, 'manual')"
        assert await denied(dsn, LOGIN, insert, "mister_engage_kpa", 1.55) == "42501"
        assert await denied(dsn, LOGIN, "SELECT * FROM public.control_experiments") == "42501"
        # 282 grants nothing on an existing object; the reads arrive with their own successor.
        for relation in ("public.climate", "public.setpoint_changes", "public.desired_state", "public.alert_log"):
            assert await denied(dsn, LOGIN, f"SELECT 1 FROM {relation} LIMIT 0") == "42501", relation
        for statement in ("CREATE TABLE public.vd66_probe (id int)", f"CREATE TABLE {DUTY}.vd66_probe (id int)"):
            assert await denied(dsn, LOGIN, statement) == "42501", statement

    run(check())


def test_282_grants_only_its_own_objects(dsn):
    async def check():
        admin = await connect(dsn)
        try:
            relations = await admin.fetch(
                """SELECT format('%I.%I', n.nspname, c.relname) AS relation,
                          array_agg(p.privilege ORDER BY p.privilege) AS privileges
                     FROM pg_class c
                     JOIN pg_namespace n ON n.oid = c.relnamespace
                    CROSS JOIN unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER'])
                          p(privilege)
                    WHERE n.nspname IN ('public', $2) AND c.relkind IN ('r','p','v','m','f')
                      AND has_table_privilege($1, c.oid, p.privilege)
                      AND NOT has_table_privilege('public', c.oid, p.privilege)
                    GROUP BY 1 ORDER BY 1""",
                LOGIN,
                DUTY,
            )
            columns = await admin.fetchval(
                """SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname IN ('public', $2) AND c.relkind IN ('r','p','v','m','f')
                      AND c.oid <> 'public.dashboard_session'::regclass
                      AND has_any_column_privilege($1, c.oid, 'SELECT,INSERT,UPDATE,REFERENCES')
                      AND NOT has_any_column_privilege('public', c.oid, 'SELECT,INSERT,UPDATE,REFERENCES')""",
                LOGIN,
                DUTY,
            )
            sequences = await admin.fetchval(
                """SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname IN ('public', $2)
                      AND CASE WHEN c.relkind = 'S'
                               THEN has_sequence_privilege($1, c.oid, 'USAGE,SELECT,UPDATE')
                                    AND NOT has_sequence_privilege('public', c.oid, 'USAGE,SELECT,UPDATE')
                               ELSE FALSE END""",
                LOGIN,
                DUTY,
            )
            functions = await admin.fetch(
                """SELECT format('%I.%I(%s)', n.nspname, p.proname, pg_get_function_identity_arguments(p.oid))
                     FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                    WHERE n.nspname IN ('public', $2) AND has_function_privilege($1, p.oid, 'EXECUTE')
                      AND NOT has_function_privilege('public', p.oid, 'EXECUTE') ORDER BY 1""",
                LOGIN,
                DUTY,
            )
            schemas = await admin.fetch(
                """SELECT n.nspname::text, has_schema_privilege($1, n.oid, 'CREATE') FROM pg_namespace n
                    WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
                      AND (has_schema_privilege($1, n.oid, 'CREATE')
                           OR (has_schema_privilege($1, n.oid, 'USAGE')
                               AND NOT has_schema_privilege('public', n.oid, 'USAGE')))""",
                LOGIN,
            )
            database_create = await admin.fetchval(
                "SELECT has_database_privilege($1, current_database(), 'CREATE')", LOGIN
            )
            owners = await admin.fetchrow(
                f"""SELECT (SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = '{DUTY}'),
                           (SELECT pg_get_userbyid(relowner) FROM pg_class
                             WHERE oid = 'public.dashboard_session'::regclass),
                           pg_get_userbyid(p.proowner), p.prosecdef, p.proconfig
                      FROM pg_proc p WHERE p.oid = '{DUTY}.fn_attest_login()'::regprocedure"""
            )
        finally:
            await admin.close()
        assert [tuple(row) for row in relations] == [
            ("public.dashboard_session", ["DELETE", "INSERT", "SELECT", "UPDATE"])
        ]
        assert columns == 0 and sequences == 0 and database_create is False
        assert [row[0] for row in functions] == [f"{DUTY}.fn_attest_login()"]
        assert [tuple(row) for row in schemas] == [(DUTY, False)]
        assert tuple(owners) == (OWNER, OWNER, OWNER, False, ["search_path=pg_catalog, public, pg_temp"])

    run(check())
