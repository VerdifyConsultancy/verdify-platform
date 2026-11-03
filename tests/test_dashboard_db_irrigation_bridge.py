"""VD-64 (#1177) on a private PostgreSQL: run and stop as the dashboard and MCP logins, and the ingestor's updates.

jvallery/agents#5994's campaign step runs each ``tests/test_dashboard_db_*.py`` file against a fresh loopback
PostgreSQL with TimescaleDB and hands it over in ``VERDIFY_ISOLATED_TEST_DSN``; without one this module skips.
``irrigation_db_support.prepare_irrigation_tables`` builds M4, the dashboard login (282) and M3 (283) as
#1153's test does. This file adds migration 280's statements (``activity_log``, verbatim), the two tables of
db/schema.sql the bridge reads that the support leaves out (``setpoint_snapshot``, and ``crop_band_anchors``
for 280's MCP grant), and stand-ins for grants production holds outside these migrations: MCP's 259 reads
of ``system_state``, ``setpoint_snapshot`` and ``equipment_state``, and the dashboard login's M2b grants
(VD-66's follow-up): SELECT on ``system_state`` and ``setpoint_snapshot``, SELECT and INSERT on
``activity_log`` with its sequence, and EXECUTE on ``fn_mcp_armed_assignment_context()``.

The ingestor's ``CommandStore`` runs from ``ingestor/tasks/irrigation.py``'s own source, executed without
importing or starting the ingestor. The writes commit (``recorded_write`` opens its own transaction), so each
test starts from the seed: L01 enabled, ``irrig_wall_duration_min`` 12, both switches on, ``drip_wall`` off
and ``irr_engine`` false.
"""

from __future__ import annotations

import ast
import asyncio
import datetime as dt
import json
import uuid

import irrigation_db_support as support
import pytest
from conftest import isolated_mutation_test_dsn

from verdify_services.actor import Actor
from verdify_services.irrigation import writes
from verdify_services.problems import ServiceError

asyncpg = pytest.importorskip("asyncpg")

try:
    DSN = isolated_mutation_test_dsn()
except ValueError:
    DSN = None

pytestmark = pytest.mark.skipif(DSN is None, reason="needs VERDIFY_ISOLATED_TEST_DSN (campaign private PostgreSQL)")

INGESTOR, MCP = "verdify_ingestor_runtime_login", "verdify_mcp_runtime_login"
DASHBOARD = "verdify_dashboard_runtime_login"
ACTIVITY_MIGRATION = support.REPO_ROOT / "db" / "migrations" / "280-desired-state-activity-log.sql"
TASK = support.REPO_ROOT / "ingestor" / "tasks" / "irrigation.py"
ACTORS = {DASHBOARD: Actor.person("jason", "operator"), MCP: Actor.agent("steward")}

STAND_IN_GRANTS = """
GRANT SELECT ON public.system_state, public.setpoint_snapshot, public.equipment_state TO verdify_mcp_runtime;
GRANT SELECT ON public.system_state, public.setpoint_snapshot TO verdify_dashboard_runtime;
GRANT SELECT, INSERT ON public.activity_log TO verdify_dashboard_runtime;
GRANT USAGE ON SEQUENCE public.activity_log_id_seq TO verdify_dashboard_runtime;
GRANT EXECUTE ON FUNCTION public.fn_mcp_armed_assignment_context() TO verdify_dashboard_runtime;
"""
SEED = """
DELETE FROM public.irrigation_ledger;
DELETE FROM public.irrigation_command;
DELETE FROM public.activity_log;
DELETE FROM public.setpoint_snapshot;
DELETE FROM public.equipment_state;
DELETE FROM public.system_state;
INSERT INTO public.setpoint_snapshot (ts, parameter, value, greenhouse_id) VALUES
    (now() - interval '30 seconds', 'irrig_wall_duration_min', 12, 'vallery'),
    (now() - interval '30 seconds', 'sw_irrig_enabled', 1, 'vallery'),
    (now() - interval '30 seconds', 'sw_irrig_wall_enabled', 1, 'vallery');
INSERT INTO public.equipment_state (ts, equipment, state, greenhouse_id)
    VALUES (now() - interval '1 hour', 'drip_wall', false, 'vallery');
INSERT INTO public.system_state (ts, entity, value, greenhouse_id)
    VALUES (now() - interval '1 hour', 'irr_engine', 'false', 'vallery');
"""


def run(coro):
    return asyncio.run(coro)


async def connect(user: str | None = None):
    return await asyncpg.connect(DSN, user=user) if user else await asyncpg.connect(DSN)


async def admin(sql: str, *args):
    conn = await connect()
    try:
        return await (
            conn.fetch(sql, *args) if args or sql.lstrip().upper().startswith("SELECT") else conn.execute(sql)
        )
    finally:
        await conn.close()


async def counts() -> tuple[int, int]:
    [row] = await admin(
        "SELECT (SELECT count(*) FROM public.irrigation_command) AS commands, "
        "(SELECT count(*) FROM public.activity_log) AS activity"
    )
    return row["commands"], row["activity"]


@pytest.fixture(scope="module")
def database():
    async def setup():
        conn = await connect()
        try:
            await support.prepare_irrigation_tables(conn)
            sections = support.schema_sections()
            await conn.execute("SET ROLE verdify")
            try:
                await conn.execute(
                    "\n".join(
                        sections[(name, "TABLE", "public")] for name in ("setpoint_snapshot", "crop_band_anchors")
                    )
                )
                await conn.execute(
                    "SET search_path = pg_catalog, public, pg_temp;\n" + support.migration_body(ACTIVITY_MIGRATION)
                )
            finally:
                await conn.execute("RESET search_path")
                await conn.execute("RESET ROLE")
            await conn.execute(STAND_IN_GRANTS)
        finally:
            await conn.close()

    run(setup())


@pytest.fixture(autouse=True)
def seeded(database):
    run(admin(SEED))
    yield
    run(admin(SEED))


class Listener:
    """A LISTEN irrigation_changed session: the payloads delivered so far."""

    def __init__(self):
        self.payloads: list[str] = []
        self.conn = None

    async def __aenter__(self):
        self.conn = await connect()
        await self.conn.add_listener("irrigation_changed", lambda *args: self.payloads.append(args[3]))
        return self

    async def settle(self):
        await asyncio.sleep(0.3)
        return self.payloads

    async def __aexit__(self, *exc):
        await self.conn.close()
        return False


async def as_login(user: str, write):
    conn = await connect(user)
    try:
        return await write(conn)
    finally:
        await conn.close()


def load_command_store():
    """CommandStore from ingestor/tasks/irrigation.py's own source, without the ingestor's device imports."""
    tree = ast.parse(TASK.read_text())
    device_side = {"esp32_push", "shared"}
    body = [
        node
        for node in tree.body
        if not (isinstance(node, ast.Import) and {alias.name for alias in node.names} & device_side)
        and not (isinstance(node, ast.ImportFrom) and node.level)  # .dispatcher: the heap guard, not tested here
    ]
    namespace: dict = {"__name__": "irrigation_task_under_test"}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(TASK), "exec"), namespace)  # noqa: S102
    return namespace


class OnePool:
    """CommandStore's pool: every acquire lends the same ingestor-login connection."""

    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        conn = self.conn

        class Lend:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return Lend()


# ── the writes as each login ──────────────────────────────────────────────────


@pytest.mark.parametrize("login", [DASHBOARD, MCP])
def test_a_bridge_run_and_a_stop_commit_with_their_activity_rows_and_wake_the_ingestor(login):
    actor = ACTORS[login]

    async def check():
        async with Listener() as listener:
            key = uuid.uuid4()
            run_result = await as_login(
                login, lambda conn: writes.run(conn, actor, "L01", reason="wall is dry", request_id=key)
            )
            assert await listener.settle() == [""]
            stop_result = await as_login(login, lambda conn: writes.stop(conn, actor, "L01"))
            assert await listener.settle() == ["", ""]
            replay = await as_login(
                login, lambda conn: writes.run(conn, actor, "L01", reason="wall is dry", request_id=key)
            )
            assert await listener.settle() == ["", ""]  # a replay stores and wakes nothing
        rows = await admin(
            "SELECT c.command_id, c.kind, c.line_id, c.args::text AS args, c.status, c.source, c.actor, c.reason, "
            "a.action, a.target, a.detail::text AS detail, a.request_id, a.source AS activity_source "
            "FROM public.irrigation_command c JOIN public.activity_log a "
            "ON a.detail->>'command_id' = c.command_id::text ORDER BY c.created_at"
        )
        return run_result, stop_result, replay, rows, key

    run_result, stop_result, replay, rows, key = run(check())
    assert (run_result.value.kind, run_result.value.args, run_result.value.status) == (
        "run",
        {"minutes": 12, "bridge": True},
        "pending",
    )
    assert (stop_result.value.kind, stop_result.value.line_id, stop_result.value.args) == (
        "stop",
        "L01",
        {"bridge": True},
    )
    assert replay.replayed and replay.value == run_result.value
    assert [(r["kind"], r["action"], r["target"], r["source"], r["actor"], r["activity_source"]) for r in rows] == [
        ("run", "irrigation.run", "L01", actor.source, actor.name, actor.source),
        ("stop", "irrigation.stop", "L01", actor.source, actor.name, actor.source),
    ]
    assert rows[0]["request_id"] == key and json.loads(rows[0]["args"]) == {"minutes": 12, "bridge": True}
    assert json.loads(rows[0]["detail"])["ref"] == {"table": "irrigation_command", "id": str(rows[0]["command_id"])}


def test_the_command_rolls_back_with_its_activity_row():
    """One transaction: when the activity row cannot be written, the command row and the wake go with it."""

    async def check():
        await admin("REVOKE INSERT ON public.activity_log FROM verdify_dashboard_runtime")
        try:
            async with Listener() as listener:
                with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
                    await as_login(DASHBOARD, lambda conn: writes.stop(conn, ACTORS[DASHBOARD], None))
                return await listener.settle(), await counts()
        finally:
            await admin("GRANT INSERT ON public.activity_log TO verdify_dashboard_runtime")

    assert run(check()) == ([], (0, 0))


@pytest.mark.parametrize(
    ("setup", "kwargs", "code"),
    [
        ("", {"line_id": "L04"}, "unavailable"),
        ("", {"minutes": 10}, "out_of_range"),
        (
            "INSERT INTO public.setpoint_snapshot (ts, parameter, value, greenhouse_id) "
            "VALUES (now(), 'sw_irrig_wall_enabled', 0, 'vallery')",
            {},
            "line_disabled",
        ),
        ("UPDATE public.irrigation_line SET enabled = false WHERE line_id = 'L01'", {}, "line_disabled"),
        (
            "UPDATE public.irrigation_line SET paused_until = now() + interval '1 hour' WHERE line_id = 'L01'",
            {},
            "line_paused",
        ),
        (
            "INSERT INTO public.equipment_state (ts, equipment, state, greenhouse_id) "
            "VALUES (now() - interval '2 minutes', 'drip_wall', true, 'vallery')",
            {},
            "line_busy",
        ),
        (
            "INSERT INTO public.system_state (ts, entity, value, greenhouse_id) "
            "VALUES (now(), 'irr_engine', 'true', 'vallery')",
            {},  # with the engine a run takes the body per line kind (VD-41), so L01 needs its minutes
            "invalid_request",
        ),
    ],
)
def test_each_refusal_stores_nothing_and_wakes_nothing(setup, kwargs, code):
    kwargs = dict(kwargs)
    line_id = kwargs.pop("line_id", "L01")

    async def check():
        if setup:
            await admin(setup)
        try:
            async with Listener() as listener:
                with pytest.raises(ServiceError) as excinfo:
                    await as_login(MCP, lambda conn: writes.run(conn, ACTORS[MCP], line_id, **kwargs))
                return excinfo.value.problem, await listener.settle(), await counts()
        finally:
            await admin("UPDATE public.irrigation_line SET enabled = true, paused_until = NULL WHERE line_id = 'L01'")

    problem, payloads, stored = run(check())
    assert problem.code == code and payloads == [] and stored == (0, 0)
    if code == "out_of_range":
        assert (problem.min, problem.max, problem.field) == (12, 12, "minutes")
    if code == "line_busy":
        assert problem.retryable and 595 <= problem.retry_after_s <= 600  # relay on 2 min of a 12 min run
    if code == "line_disabled" and setup.startswith("INSERT"):
        assert "sw_irrig_wall_enabled" in problem.detail


def test_a_stop_is_stored_while_l01_runs_and_while_the_engine_is_on():
    async def check():
        await admin(
            "INSERT INTO public.equipment_state (ts, equipment, state, greenhouse_id) "
            "VALUES (now(), 'drip_wall', true, 'vallery')"
        )
        first = await as_login(DASHBOARD, lambda conn: writes.stop(conn, ACTORS[DASHBOARD], None))
        await admin(
            "INSERT INTO public.system_state (ts, entity, value, greenhouse_id) "
            "VALUES (now(), 'irr_engine', 'true', 'vallery')"
        )
        second = await as_login(MCP, lambda conn: writes.stop(conn, ACTORS[MCP], "L09", reason="nozzles"))
        return first.value, second.value, await counts()

    first, second, stored = run(check())
    assert (first.line_id, first.args) == (None, {"bridge": True})
    assert (second.line_id, second.args, second.reason) == ("L09", {}, "nozzles")
    assert stored == (2, 2)


def test_a_recent_bridge_toggle_explains_an_off_sw_irrig_enabled_reading():
    """Only a sent Stop that recorded its toggle in the last 2 min does: the SQL behind ``stop_toggled``."""
    insert_stop = (
        "INSERT INTO public.irrigation_command (kind, line_id, args, status, source, actor, sent_at) "
        "VALUES ('stop', 'L01', $1::text::jsonb, 'sent', 'agent:steward', 'steward', now() - $2::interval)"
    )
    toggled = '{"relays_on": [], "restore_irrig_enabled": true}'

    async def attempt():
        try:
            return (await as_login(MCP, lambda conn: writes.run(conn, ACTORS[MCP], "L01"))).value.status
        except ServiceError as refused:
            return refused.problem.code

    async def check():
        await admin(
            "INSERT INTO public.setpoint_snapshot (ts, parameter, value, greenhouse_id) "
            "VALUES (now(), 'sw_irrig_enabled', 0, 'vallery')"
        )
        outcomes = [await attempt()]  # no Stop: the switch reads off
        await admin(insert_stop, toggled, dt.timedelta(minutes=3))
        outcomes.append(await attempt())  # toggled too long ago
        await admin(insert_stop, '{"relays_on": []}', dt.timedelta(seconds=30))
        outcomes.append(await attempt())  # a recent Stop that never toggled
        await admin(insert_stop, toggled, dt.timedelta(seconds=30))
        outcomes.append(await attempt())
        return outcomes

    assert run(check()) == ["line_disabled", "line_disabled", "line_disabled", "pending"]


def test_two_concurrent_runs_store_one_command_under_the_irrigation_lock():
    async def attempt(login):
        try:
            return await as_login(login, lambda conn: writes.run(conn, ACTORS[login], "L01"))
        except ServiceError as refused:
            return refused.problem.code

    async def check():
        outcomes = await asyncio.gather(attempt(DASHBOARD), attempt(MCP))
        return outcomes, await counts()

    outcomes, stored = run(check())
    assert sorted(o if isinstance(o, str) else "stored" for o in outcomes) == ["line_busy", "stored"]
    assert stored == (1, 1)


# ── the ingestor's updates as its login ──────────────────────────────────────


def test_the_ingestor_login_expires_sends_stops_and_records_through_command_store():
    task = load_command_store()

    async def check():
        mcp_actor = ACTORS[MCP]
        before = await as_login(MCP, lambda conn: writes.run(conn, mcp_actor, "L01"))
        stop = await as_login(MCP, lambda conn: writes.stop(conn, mcp_actor, None))
        await admin(
            "INSERT INTO public.irrigation_command (kind, line_id, args, source, actor, created_at) "
            "VALUES ('run', 'L01', '{\"minutes\": 12, \"bridge\": true}', 'agent:steward', 'steward', "
            "now() - interval '3 minutes')"
        )
        conn = await connect(INGESTOR)
        try:
            store = task["CommandStore"](OnePool(conn))
            expired = await store.expire()
            pending_stops = await store.pending("stop")
            pending_runs = await store.pending("run")
            sent_at = dt.datetime.now(dt.UTC)
            first = await store.mark_stop_sent(pending_stops[0], sent_at, ["drip_wall"])
            again = await store.mark_stop_sent(pending_stops[0], sent_at, ["drip_wall"])
            [sent_stop] = await store.sent_stops()
            await store.merge_args(sent_stop, {"restore_irrig_enabled": True})
            await store.merge_args(sent_stop, {"restored": True})
            [restored_stop] = await store.sent_stops()
            after = await as_login(MCP, lambda c: writes.run(c, mcp_actor, "L01"))
            [pending_after] = await store.pending("run")
            run_sent = await store.mark_run_sent(pending_after, dt.datetime.now(dt.UTC))
            [sent_run] = await store.sent_runs()
            await store.mark_failed(sent_run, "entity_key_unavailable")
        finally:
            await conn.close()
        rows = {
            r["command_id"]: (r["status"], r["fail_reason"])
            for r in await admin("SELECT command_id, status, fail_reason FROM public.irrigation_command")
        }
        return before, stop, expired, pending_stops, pending_runs, first, again, restored_stop, after, run_sent, rows

    before, stop, expired, stops, runs, first, again, restored_stop, after, run_sent, rows = run(check())
    assert [(row.kind, row.status, row.fail_reason) for row in expired] == [("run", "failed", "too old")]
    assert [row.command_id for row in stops] == [stop.value.command_id]
    assert [row.command_id for row in runs] == [before.value.command_id]
    assert (first, again, run_sent) == (True, False, True)
    assert restored_stop.args == {
        "bridge": True,
        "relays_on": ["drip_wall"],
        "restore_irrig_enabled": True,
        "restored": True,
    }
    assert rows[stop.value.command_id] == ("sent", None)
    assert rows[before.value.command_id] == ("failed", "stopped")  # requested before the Stop: never pressed
    assert rows[after.value.command_id] == ("failed", "entity_key_unavailable")
    assert sorted(rows.values()).count(("failed", "too old")) == 1


def test_a_row_the_model_refuses_never_keeps_the_ingestor_login_from_sending_a_stop():
    """A hand-typed row passes the table's checks but not the model: it must not wedge the pass, and it expires."""
    task = load_command_store()
    insert = (
        "INSERT INTO public.irrigation_command (kind, line_id, args, source, actor, created_at) "
        "VALUES ('stop', NULL, '{}', 'typed by hand', 'someone', now() - interval %r)"
    )

    async def check():
        stop = await as_login(MCP, lambda conn: writes.stop(conn, ACTORS[MCP], "L04"))
        await admin(insert % "10 seconds")
        await admin(insert % "3 minutes")
        conn = await connect(INGESTOR)
        try:
            store = task["CommandStore"](OnePool(conn))
            expired = await store.expire()
            pending = await store.pending("stop")
        finally:
            await conn.close()
        statuses = await admin("SELECT status, count(*) AS n FROM public.irrigation_command GROUP BY status")
        return stop, expired, pending, {row["status"]: row["n"] for row in statuses}

    stop, expired, pending, statuses = run(check())
    assert expired == []  # the only expired row is the refused one: failed, and not returned
    assert [row.command_id for row in pending] == [stop.value.command_id]
    assert statuses == {"pending": 2, "failed": 1}
