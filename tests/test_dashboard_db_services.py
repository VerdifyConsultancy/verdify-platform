"""verdify_services on a private TimescaleDB (VD-31, #1147): replay, request_id_reused and the experiment stamp.

jvallery/agents#5994's campaign step starts a fresh server for each tests/test_dashboard_db_*.py file and
sets VERDIFY_ISOLATED_TEST_DSN; without it this file skips. The database is prepared exactly as
tests/test_dashboard_db_280.py prepares it (its install(): db/schema.sql, the ingestor and MCP logins, then
migration 280's statements). db/schema.sql predates control_experiments and control_assignments (migration
207) and fn_mcp_armed_assignment_context() (259), so this file creates the columns that function reads and
the function from 259's own text, then arms and disarms a fixture experiment. Every write runs as the MCP
login, the first runtime login with 280's grants, through verdify_services.activity.recorded_write.
"""

import asyncio
import uuid
from datetime import UTC, datetime

import asyncpg
import pytest
from pydantic import ValidationError
from test_dashboard_db_280 import MCP, OWNER, ROOT, connect, install, private_dsn

from verdify_services import activity
from verdify_services.actor import Actor
from verdify_services.problems import ServiceError

ARMED_CONTEXT = ROOT / "db/migrations/259-mcp-ordinary-runtime-boundary.sql"
AGENT = Actor.agent("db-test")
REQUEST = {"value": 1.53, "manual": True, "reason": "campaign"}

# The columns fn_mcp_armed_assignment_context() reads (migration 207 has the full tables).
EXPERIMENT_COLUMNS = """
CREATE TABLE public.control_experiments (
    experiment_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    greenhouse_id text NOT NULL,
    status        text NOT NULL,
    armed_at      timestamptz
);
CREATE TABLE public.control_assignments (
    assignment_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    experiment_id uuid NOT NULL REFERENCES public.control_experiments (experiment_id),
    status        text NOT NULL DEFAULT 'active',
    valid_range   tstzrange NOT NULL
);
"""


def armed_context_sql() -> str:
    """Migration 259's CREATE FUNCTION fn_mcp_armed_assignment_context(), its REVOKE and its GRANT, verbatim."""
    sql = ARMED_CONTEXT.read_text()
    start = sql.index("CREATE FUNCTION public.fn_mcp_armed_assignment_context()")
    grant = sql.index("GRANT EXECUTE ON FUNCTION public.fn_mcp_armed_assignment_context()", start)
    text = sql[start : sql.index(";", grant) + 1]
    assert text.count("CREATE FUNCTION") == 1 and text.rstrip().endswith("TO verdify_mcp_runtime;")
    return text


async def owner_execute(dsn: str, sql: str) -> None:
    owner = await connect(dsn, OWNER)
    try:
        await owner.execute("SET search_path = pg_catalog, public, pg_temp;\n" + sql)
    finally:
        await owner.close()


async def owner_fetch(dsn: str, sql: str, *args):
    owner = await connect(dsn, OWNER)
    try:
        return await owner.fetch(sql, *args)
    finally:
        await owner.close()


@pytest.fixture(scope="module")
def dsn():
    value = private_dsn()
    asyncio.run(install(value))
    asyncio.run(owner_execute(value, EXPERIMENT_COLUMNS))
    asyncio.run(owner_execute(value, armed_context_sql()))
    return value


def run(coroutine):
    return asyncio.run(coroutine)


def setting_perform(actor: Actor, param: str, value: float, manual: bool, performed: list | None = None):
    """A setting write's domain row: one desired_state row, referenced from the activity detail."""

    async def perform(conn):
        row = await conn.fetchrow(
            "INSERT INTO public.desired_state (param, value, manual, source, actor) VALUES ($1, $2, $3, $4, $5) "
            "RETURNING id, param, value, manual",
            param,
            value,
            manual,
            actor.source,
            actor.name,
        )
        if performed is not None:
            performed.append(row["id"])
        return dict(row), {"ref": {"table": "desired_state", "id": row["id"]}, "value": value}

    return perform


async def load_setting(conn, row):
    ref = row.detail["ref"]
    assert ref["table"] == "desired_state"
    stored = await conn.fetchrow("SELECT id, param, value, manual FROM public.desired_state WHERE id = $1", ref["id"])
    return dict(stored)


async def write(conn, actor, param, request, request_id, *, action="setting.write", perform=None):
    return await activity.recorded_write(
        conn,
        actor,
        op="API-10",
        action=action,
        target=param,
        request=request,
        request_id=request_id,
        reason=request.get("reason"),
        perform=perform or setting_perform(actor, param, request["value"], request["manual"]),
        load=load_setting,
    )


async def counts(conn, param: str, key: uuid.UUID) -> tuple[int, int]:
    desired = await conn.fetchval("SELECT count(*) FROM public.desired_state WHERE param = $1", param)
    logged = await conn.fetchval("SELECT count(*) FROM public.activity_log WHERE request_id = $1", key)
    return desired, logged


def test_a_replay_returns_the_stored_object_and_another_payload_is_request_id_reused(dsn):
    async def check():
        mcp = await connect(dsn, MCP)
        try:
            key = uuid.uuid4()
            first = await write(mcp, AGENT, "replay_param", REQUEST, key)
            again = await write(mcp, AGENT, "replay_param", dict(reversed(list(REQUEST.items()))), key)
            assert (first.replayed, again.replayed) == (False, True) and again.value == first.value
            assert again.rest_headers() == {"Idempotency-Replayed": "true"}
            assert again.mcp_payload() == {**first.value, "replayed": True}
            assert await counts(mcp, "replay_param", key) == (1, 1)
            stored = await mcp.fetchrow(
                "SELECT source, actor, action, target, reason, detail::text AS detail, request_sha256, experiment_id "
                "FROM public.activity_log WHERE request_id = $1",
                key,
            )
            assert (stored["source"], stored["actor"], stored["action"], stored["target"], stored["reason"]) == (
                "agent:db-test",
                "db-test",
                "setting.write",
                "replay_param",
                "campaign",
            )
            assert stored["request_sha256"] == activity.request_hash("API-10", "replay_param", REQUEST)
            assert f'"id": {first.value["id"]}' in stored["detail"] and stored["experiment_id"] is None

            with pytest.raises(ServiceError) as refused:
                await write(mcp, AGENT, "replay_param", {**REQUEST, "value": 1.6}, key)
            problem = refused.value.problem
            assert (problem.code, problem.status, problem.retryable) == ("request_id_reused", 422, False)
            assert await counts(mcp, "replay_param", key) == (1, 1)

            person = await write(mcp, Actor.person("jason", "operator"), "replay_param", REQUEST, key)
            assert person.replayed is False  # idempotency is per source
            assert await counts(mcp, "replay_param", key) == (2, 2)
        finally:
            await mcp.close()

    run(check())


def test_an_armed_experiment_is_stamped_and_nothing_is_refused(dsn):
    async def check():
        mcp = await connect(dsn, MCP)
        try:
            with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
                await mcp.fetch("SELECT experiment_id FROM public.control_experiments")  # the blinded firewall
            [armed] = await owner_fetch(
                dsn,
                "INSERT INTO public.control_experiments (greenhouse_id, status, armed_at) "
                "VALUES ('vallery', 'armed', now()) RETURNING experiment_id",
            )
            experiment = armed["experiment_id"]
            stamped = []
            for status in ("armed", "running", "completed"):
                await owner_fetch(
                    dsn,
                    "UPDATE public.control_experiments SET status = $1 WHERE experiment_id = $2",
                    status,
                    experiment,
                )
                key = uuid.uuid4()
                result = await write(mcp, AGENT, f"experiment_{status}", REQUEST, key)
                assert result.replayed is False  # never refused
                stamped.append(
                    await mcp.fetchval("SELECT experiment_id FROM public.activity_log WHERE request_id = $1", key)
                )
            assert stamped == [experiment, experiment, None]
        finally:
            await mcp.close()

    run(check())


def test_a_concurrent_duplicate_applies_once_and_the_loser_replays(dsn):
    async def check():
        key = uuid.uuid4()
        both_inside = asyncio.Barrier(2)
        performed: list[int] = []
        base = setting_perform(AGENT, "race_param", 1.55, True, performed)

        async def racing(conn):
            outcome = await base(conn)
            if len(performed) <= 2:  # both transactions hold their domain row before either appends
                await asyncio.wait_for(both_inside.wait(), timeout=10)
            return outcome

        first, second = await connect(dsn, MCP), await connect(dsn, MCP)
        try:
            results = await asyncio.wait_for(
                asyncio.gather(
                    write(first, AGENT, "race_param", REQUEST, key, perform=racing),
                    write(second, AGENT, "race_param", REQUEST, key, perform=racing),
                ),
                timeout=30,
            )
            assert sorted(result.replayed for result in results) == [False, True]
            assert results[0].value == results[1].value and len(performed) == 2
            assert await counts(first, "race_param", key) == (1, 1)  # the loser's domain row rolled back
        finally:
            await first.close()
            await second.close()

    run(check())


def test_the_domain_row_and_its_activity_row_commit_together_or_not_at_all(dsn):
    async def check():
        mcp = await connect(dsn, MCP)
        try:
            performed: list[int] = []
            with pytest.raises(ValidationError):
                await write(
                    mcp,
                    AGENT,
                    "atomic_param",
                    REQUEST,
                    uuid.uuid4(),
                    action="setting.bogus",  # the activity row fails after the domain row was written
                    perform=setting_perform(AGENT, "atomic_param", 1.55, True, performed),
                )
            assert len(performed) == 1
            assert await mcp.fetchval("SELECT count(*) FROM public.desired_state WHERE param = 'atomic_param'") == 0
            assert await mcp.fetchval("SELECT count(*) FROM public.activity_log WHERE target = 'atomic_param'") == 0
        finally:
            await mcp.close()

    run(check())


def test_list_reads_newest_first_with_filters_and_cursor_pages(dsn):
    def at(minute: int) -> datetime:
        return datetime(2030, 1, 1, 10, minute, tzinfo=UTC)

    async def check():
        fixture = [
            (at(0), "agent:alpha", "alpha", "irrigation.run", "L01"),
            (at(1), "agent:beta", "beta", "irrigation.schedule.update", "L01"),
            (at(1), "iris", "iris", "setting.write", "mister_engage_kpa"),  # same ts: the larger id is newer
            (at(2), "dashboard", "jason", "irrigation.stop", "L01"),
            (at(3), "system", "forecast-engine", "plan.write", "preemptive-1"),
        ]
        ids = []
        for row in fixture:
            [inserted] = await owner_fetch(
                dsn,
                "INSERT INTO public.activity_log (ts, source, actor, action, target) VALUES ($1, $2, $3, $4, $5) "
                "RETURNING id",
                *row,
            )
            ids.append(inserted["id"])
        a, b, c, d, e = ids
        window = {"from_": datetime(2030, 1, 1, tzinfo=UTC), "to": datetime(2030, 1, 2, tzinfo=UTC)}
        mcp = await connect(dsn, MCP)
        try:

            async def read(**kwargs):
                page = await activity.list(mcp, **{**window, **kwargs})
                return [item.id for item in page.items], page.next_cursor

            pages, cursor = [], None
            while True:
                page_ids, cursor = await read(limit=2, cursor=cursor)
                pages.append(page_ids)
                if cursor is None:
                    break
            assert pages == [[e, d], [c, b], [a]]
            assert (await read(source="agent"))[0] == [b, a]
            assert (await read(source="iris"))[0] == [c]
            assert (await read(action="irrigation."))[0] == [d, b, a]
            assert (await read(target="L01"))[0] == [d, b, a]
            assert (await read(actor="jason"))[0] == [d]
            assert (await read(to=at(1)))[0] == [a]  # to is exclusive
            entry = (await activity.list(mcp, **window, source="system")).items[0]
            assert (entry.actor.source, entry.actor.name, entry.detail, entry.reason) == (
                "system",
                "forecast-engine",
                {},
                None,
            )
        finally:
            await mcp.close()

    run(check())
