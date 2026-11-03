"""VD-60 (#1173) on a private PostgreSQL: section 11 as the ingestor login, against the MCP tools as the MCP login.

jvallery/agents#5994's campaign step runs each ``tests/test_dashboard_db_*.py`` file against a fresh loopback PostgreSQL
and hands it over in ``VERDIFY_ISOLATED_TEST_DSN``; without one this module skips. The database and its fixture rows are
tests/test_dashboard_db_irrigation_reads.py's. The ingestor login gets M3's and M4's grants from their bodies and the
three relations below from 217's gather read inventory (checked), so a missing M3 grant fails here.

``load_context`` runs from ``ingestor/tasks/irrigation.py``'s own source, without the ingestor's device imports, and the
tools through ``mcp/server.py`` under the FastMCP stub. Section 11's line fields must equal ``irrigation_status``'s.
"""

from __future__ import annotations

import ast
import json
import re
import sys
import types
import uuid

import irrigation_db_support as support
import pytest
from test_dashboard_db_irrigation_reads import (
    DSN,
    MCP,
    MIN,
    PERSON,
    H,
    S,
    as_login,
    connect,
    database,  # noqa: F401
    run,
    scenario,
)
from test_mcp_audience_auth import mcp_server, mcp_types  # noqa: F401

from verdify_schemas import irrigation as irr
from verdify_services.irrigation import reads as water

asyncpg = pytest.importorskip("asyncpg")

pytestmark = pytest.mark.skipif(DSN is None, reason="needs VERDIFY_ISOLATED_TEST_DSN (campaign private PostgreSQL)")

INGESTOR = "verdify_ingestor_runtime_login"
TASK = support.REPO_ROOT / "ingestor" / "tasks" / "irrigation.py"
INGESTOR_READS = ("climate", "equipment_state", "system_state")


def ingestor_reads_217() -> set[str]:
    text = (support.REPO_ROOT / "db" / "migrations" / "217-runtime-role-boundary.sql").read_text()
    start = text.index("mounted canonical gather script read")
    block = re.search(r"GRANT SELECT ON TABLE\s+(.*?)\s+TO verdify_ingestor_runtime;", text[start:], re.S)
    assert block, "217's gather read inventory moved"
    return {name.strip().removeprefix("public.") for name in block.group(1).split(",")}


def load_section():
    """Section 11 from ingestor/tasks/irrigation.py's own source, as the bridge's database test loads CommandStore."""
    tree = ast.parse(TASK.read_text())
    device_side = {"esp32_push", "shared"}
    body = [
        node
        for node in tree.body
        if not (isinstance(node, ast.Import) and {alias.name for alias in node.names} & device_side)
        and not (isinstance(node, ast.ImportFrom) and node.level)  # .dispatcher: the heap guard, not used here
    ]
    module = types.ModuleType("irrigation_task_under_test")
    sys.modules[module.__name__] = module  # pydantic resolves the models' annotations through it
    exec(compile(ast.Module(body=body, type_ignores=[]), str(TASK), "exec"), module.__dict__)  # noqa: S102
    return module


section = load_section()


@pytest.fixture(scope="module")
def context_db(database):  # noqa: F811
    assert set(INGESTOR_READS) <= ingestor_reads_217()

    async def grant():
        conn = await connect()
        try:
            relations = ", ".join(f"public.{name}" for name in INGESTOR_READS)
            await conn.execute(f"GRANT SELECT ON {relations} TO verdify_ingestor_runtime")
        finally:
            await conn.close()

    run(grant())
    return database


@pytest.fixture()
def tools(mcp_server, mcp_types, monkeypatch):  # noqa: F811
    """The MCP tools on a connection as the MCP login."""

    async def db():
        return await connect(MCP)

    monkeypatch.setattr(mcp_server, "_db", db)
    return mcp_server


async def load(hours: int = 24) -> section.IrrigationContext:
    return await as_login(INGESTOR, lambda conn: section.load_context(conn, "vallery", hours))


def tool_body(result) -> dict:
    assert result.isError is False, result.content[0].text
    return json.loads(result.content[0].text)


async def status_of(tools_module) -> water.IrrigationStatus:
    return water.IrrigationStatus.model_validate(tool_body(await tools_module.irrigation_status()))


def line_of(items, line_id):
    return next(item for item in items if item.line_id == line_id)


def assert_line_fields_equal(context, status, open_runs=()):
    """Every field section 11 prints for a line, against ``irrigation_status``. Two reads a moment apart can differ by
    the last 0.1 min of a run still open, so ``open_runs`` compare within that."""
    assert [line.line_id for line in context.lines] == [line.line_id for line in status.lines] == list(irr.LINE_IDS)
    assert context.engine == status.engine
    for ours, theirs in zip(context.lines, status.lines, strict=True):
        name = ours.line_id
        assert ours.enabled == theirs.enabled, name
        assert ours.vpd_member == theirs.vpd_member, name
        assert ours.schedule_member == theirs.schedule_member, name
        assert ours.manual == theirs.manual, name
        assert ours.paused_until == theirs.paused_until, name
        assert ours.fault == theirs.fault, name
        assert ours.fault_until == theirs.fault_until, name
        assert ours.daily_cap_min == theirs.daily_cap_min, name
        assert ours.state == theirs.state, name
        if name in open_runs:
            assert ours.minutes_today == pytest.approx(theirs.today.minutes, abs=0.11), name
        else:
            assert ours.minutes_today == theirs.today.minutes, name
        if theirs.last_outcome is None:
            assert ours.last_outcome is None, name
        else:
            mine = ours.last_outcome
            assert (mine.outcome, mine.at, mine.reason) == (
                theirs.last_outcome.outcome,
                theirs.last_outcome.at,
                theirs.last_outcome.reason,
            ), name


def rows_of(context) -> list[str]:
    return section.render_context(context).splitlines()


# ── The line fields equal irrigation_status's ───────────────────────────────


def test_before_the_engine_a_bridge_run_reads_run_now_in_both(context_db, tools):
    async def body(f):
        await f.reporting()
        await f.state("irr_engine", "false", f.at - H)
        sent = f.at - 8 * MIN
        await f.command(sent)
        await f.edge("drip_wall", sent + 5 * S, True)
        # A reconnect snapshot: a newer ON row, which the view reads as relay_at.
        await f.edge("drip_wall", f.at - 2 * MIN, True)
        await f.run("mister_center", f.today_at(0.2), f.today_at(0.3))
        await f.run("fert_master_valve", f.today_at(0.7))
        context, status = await load(), await status_of(tools)
        assert_line_fields_equal(context, status, open_runs=("L01", "L03"))
        assert context.engine is False and context.reporting is True
        l01 = line_of(context.lines, "L01")
        assert (l01.state.code, l01.state.cause) == ("running", irr.IrrigationCause.RUN_NOW)
        assert l01.state.observed_at == sent + 5 * S
        assert line_of(context.lines, "L04").state.code == "idle"
        assert line_of(context.lines, "L03").state.cause == irr.IrrigationCause.FEED
        rows = rows_of(context)
        assert rows[0] == "--- IRRIGATION (lines, next 24 h, manual items) ---"
        assert next(row for row in rows if row.startswith("L01|")).endswith("|running cause=run_now")
        assert len(rows) <= section.CONTEXT_MAX_LINES

    scenario(context_db, body)


def test_a_run_with_no_command_keeps_its_topology_cause_in_both(context_db, tools):
    async def body(f):
        await f.reporting()
        await f.state("irr_engine", "false", f.at - H)
        await f.command(f.at - 9 * MIN)  # more than 30 s before the ON edge
        await f.edge("drip_wall", f.at - 8 * MIN, True)
        context, status = await load(), await status_of(tools)
        assert_line_fields_equal(context, status, open_runs=("L01",))
        assert line_of(context.lines, "L01").state.cause == irr.IrrigationCause.UNKNOWN

    scenario(context_db, body)


@pytest.mark.parametrize(
    ("started", "ended", "cause"),
    [
        # A rollback to the bridge with an engine row whose end never arrived: the ledger goes first, in both.
        (3 * H, None, irr.IrrigationCause.UNKNOWN),
        (3 * H, 2 * H, irr.IrrigationCause.RUN_NOW),  # it ended before the run began
        (25 * H, None, irr.IrrigationCause.RUN_NOW),  # past M3's 24 h bound
    ],
)
def test_a_ledger_row_over_the_run_goes_before_the_bridge_command_in_both(context_db, tools, started, ended, cause):
    async def body(f):
        await f.reporting()
        await f.state("irr_engine", "false", f.at - H)
        edge = f.at - 8 * MIN
        await f.ledger("L01", "schedule", f.at - started, None if ended is None else f.at - ended)
        await f.command(edge - 5 * S)
        await f.edge("drip_wall", edge, True)
        context, status = await load(), await status_of(tools)
        assert_line_fields_equal(context, status, open_runs=("L01",))
        assert line_of(context.lines, "L01").state.cause == cause

    scenario(context_db, body)


def test_with_the_engine_the_tokens_pause_fault_and_last_outcome_equal_the_tool(context_db, tools):
    async def body(f):
        await f.reporting()
        await f.set_line("L07", enabled=True, schedule_member=True)
        await f.set_line("L05", enabled=True, reserved=False)
        await f.pause("L05", f.at + 2 * H, reason="fixing a fitting")
        await f.set_line("L09", manual=True)
        epoch = int(f.at.timestamp())
        await f.engine_on(
            irr_state=(
                f"d=0,schedule,4,{epoch - 60},{epoch + 540} m=3,fallback,1,{epoch - 30},{epoch + 15} "
                f"w=6,occ f=8,l,{epoch + 3600} v=oph"
            )
        )
        slot = await f.slot("L01")
        await f.run("fog", f.today_at(0.9))
        await f.run("drip_wall", f.today_at(0.2), f.today_at(0.3))
        await f.ledger("L01", "schedule", f.at - H, f.at - 50 * MIN, slot_id=slot, outcome="short", reason="cap")
        await f.ledger("L04", "fallback", None, None, slot_id=1, outcome="skipped", reason="vok", open_s=0)
        await f.ledger("L07", "run_now", f.at - 3 * H, f.at - 2 * H)
        context, status = await load(), await status_of(tools)
        assert_line_fields_equal(context, status, open_runs=("L11",))
        assert context.engine is True
        assert line_of(context.lines, "L01").state.cause == irr.IrrigationCause.SCHEDULE
        assert line_of(context.lines, "L05").state.code == "paused"
        assert line_of(context.lines, "L07").state.code == "waiting"
        l09 = line_of(context.lines, "L09")
        assert (l09.fault, l09.manual) == ("mister_fault", True)
        l01 = line_of(context.lines, "L01")
        assert (l01.last_outcome.outcome, l01.last_outcome.reason) == (irr.RunOutcome.SHORT, "cap reached")
        assert line_of(context.lines, "L07").last_outcome.outcome == irr.RunOutcome.UNKNOWN

    scenario(context_db, body)


def test_not_reporting_every_line_reads_unknown_in_both(context_db, tools):
    async def body(f):
        await f.reporting(climate_age=6 * MIN)
        await f.run("drip_wall", f.at - H, f.at - 50 * MIN)
        await f.edge("drip_wall", f.at - 10 * MIN, True)
        await f.state("irr_engine", "false", f.at - H)
        context, status = await load(), await status_of(tools)
        assert context.reporting is False
        assert_line_fields_equal(context, status, open_runs=("L01",))
        assert {line.state.code for line in context.lines} == {"unknown"}

    scenario(context_db, body)


def test_a_day_with_no_climate_row_is_null_in_both_never_0(context_db, tools):
    async def body(f):
        await f.state("ingestor_heartbeat", "alive", f.at - 30 * S)
        await f.run("drip_wall", f.today_at(0.2), f.today_at(0.3))
        context, status = await load(), await status_of(tools)
        assert_line_fields_equal(context, status)
        assert {line.minutes_today for line in context.lines} == {None}
        row = next(row for row in rows_of(context) if row.startswith("L01|"))
        assert "|null/60|" in row and "|0.0/60|" not in row

    scenario(context_db, body)


# ── The next hours ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("engine", [False, True])
def test_the_next_hours_match_the_schedule_tool(context_db, tools, engine):
    async def body(f):
        await f.reporting()
        if engine:
            await f.engine_on()
        else:
            await f.state("irr_engine", "false", f.at - H)
        local = f.at.astimezone(support.DENVER)
        # 150 wall-clock minutes are at least 90 real ones, so past the next hour even across the spring clock change.
        await f.slot("L01", minute=(local.hour * 60 + local.minute + 150) % 1440)
        await f.slot("L05", minute=(local.hour * 60 + local.minute + 155) % 1440)  # L05 is seeded disabled
        context = await load()
        agenda = tool_body(await tools.irrigation_schedule(days=2))
        until = f.at + 24 * H
        due = [
            irr_item
            for irr_item in map(water.Occurrence.model_validate, agenda["occurrences"])
            if context.at <= irr_item.planned_start < until
        ]
        assert [(item.slot_id, item.line_id, item.planned_start, item.status) for item in context.occurrences] == [
            (item.slot_id, item.line_id, item.planned_start, item.status) for item in due
        ]
        assert {item.line_id for item in context.occurrences} >= {"L01", "L05"}
        if not engine:
            assert all("arrives with the irrigation firmware" in item.reason for item in context.occurrences)
            assert "blocked" in {item.status for item in context.occurrences}
        assert all(context.at <= item.planned_start < until for item in context.occurrences)
        short = await load(hours=1)
        assert {item.line_id for item in short.occurrences} <= {"L04"}

    scenario(context_db, body)


# ── Manual items and the armed line ─────────────────────────────────────────


def test_the_manual_schedules_and_lines_say_who_and_since(context_db, tools):
    async def body(f):
        await f.reporting()
        await f.state("irr_engine", "false", f.at - H)
        since = f.at - 3 * H
        await f.set_line("L04", manual=True, updated_by="jason", source="dashboard", updated_at=since)
        slot = await f.slot("L01", manual=True, label="wall morning", updated=since)
        context = await load()
        lines = [(item.item, item.updated_by, item.source, item.updated_at) for item in context.manual_items]
        assert lines == [
            ("line L04", "jason", "dashboard", since),
            (f"schedule {slot} on L01 (wall morning)", "jason", "dashboard", since),
        ]
        rows = rows_of(context)
        assert f"schedule {slot} on L01 (wall morning)|jason|dashboard|{section._local_time(since)}" in rows

    scenario(context_db, body)


def test_the_armed_line_reads_the_experiment_or_none(context_db):
    experiment = uuid.uuid4()

    async def body(f):
        await f.reporting()
        await f.state("irr_engine", "false", f.at - H)
        assert "experiment_armed=none" in rows_of(await load())
        await f.conn.execute(
            "INSERT INTO public.control_experiments (experiment_id, greenhouse_id, status, armed_at) "
            "VALUES ($1, 'vallery', 'armed', now())",
            experiment,
        )
        try:
            context = await load()
            assert context.armed_experiment_id == str(experiment)
            assert f"experiment_armed={experiment}" in rows_of(context)
        finally:
            await f.conn.execute("DELETE FROM public.control_experiments WHERE experiment_id = $1", experiment)

    scenario(context_db, body)


def test_the_most_that_can_be_stored_stays_within_60_lines(context_db):
    async def body(f):
        await f.reporting()
        await f.state("irr_engine", "false", f.at - H)
        await f.conn.execute("UPDATE public.irrigation_line SET manual = true")
        await f.conn.execute("UPDATE public.irrigation_slot SET manual = true")
        for index in range(21):  # with the 3 seeded schedules, the 24 the controller holds
            await f.slot("L01", minute=index * 60 + 30, manual=True, label=f"schedule {index}")
        context = await load()
        assert len(context.manual_items) == 35
        assert len(context.occurrences) >= 20  # a daily start can fall just outside 24 h across a clock change
        rows = rows_of(context)
        assert len(rows) <= section.CONTEXT_MAX_LINES
        assert any(row.startswith("... and ") for row in rows)
        assert sum(row.startswith("L") and row[3] == "|" for row in rows) == 11

    scenario(context_db, body)


# ── The grants, and the snapshot ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("privilege", "target"),
    [
        ("SELECT", "public.v_irrigation_line_state"),
        ("SELECT", "public.irrigation_slot"),
        ("SELECT", "public.irrigation_command"),
        ("SELECT", "public.irrigation_ledger"),
        ("EXECUTE", "FUNCTION public.fn_mcp_armed_assignment_context()"),
        ("EXECUTE", "FUNCTION public.fn_irrigation_minutes_daily(date, date)"),
    ],
)
def test_a_missing_grant_fails_here(context_db, privilege, target):
    async def body(f):
        await f.reporting()
        await f.state("irr_engine", "false", f.at - H)
        await f.edge("drip_wall", f.at - MIN, True)  # L01 running, so the section reads its command
        await load()
        await f.conn.execute(f"REVOKE {privilege} ON {target} FROM verdify_ingestor_runtime")
        try:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await load()
        finally:
            await f.conn.execute(f"GRANT {privilege} ON {target} TO verdify_ingestor_runtime")

    scenario(context_db, body)


class SnapshotSpy:
    """A connection that notes the transaction's modes each time the section reads its clock."""

    def __init__(self, conn):
        self.conn = conn
        self.modes = []

    def __getattr__(self, name):
        return getattr(self.conn, name)

    async def fetchrow(self, sql, *args):
        isolation = await self.conn.fetchval("SHOW transaction_isolation")
        self.modes.append((isolation, await self.conn.fetchval("SHOW transaction_read_only")))
        return await self.conn.fetchrow(sql, *args)


def test_the_section_reads_in_one_read_only_snapshot_and_leaves_no_transaction(context_db):
    async def body(f):
        await f.reporting()

        async def read(conn):
            spy = SnapshotSpy(conn)
            await section.load_context(spy, "vallery", 24)
            assert not conn.is_in_transaction()
            return spy.modes

        assert await as_login(INGESTOR, read) == [("repeatable read", "on")]

    scenario(context_db, body)


# ── The tools end to end, as the MCP login ──────────────────────────────────


def test_the_tools_return_the_services_json(context_db, tools):
    async def body(f):
        await f.reporting(water_total_gal=100.0, flow_gpm=0.0, mister_water_today=12.0)
        await f.state("irr_engine", "false", f.at - H)
        started, ended = f.today_at(0.3), f.today_at(0.4)
        await f.run("drip_wall", started, ended)
        today = support.local_day(f.at)

        async def direct(conn):
            return (
                await water.agenda(conn, PERSON, days=7, past_days=0),
                await water.minutes(conn, PERSON, today, today, "line"),
                await water.runs(conn, PERSON, f.at - 24 * H, f.at),
            )

        agenda, minutes, runs = await as_login(MCP, direct)
        schedule = tool_body(await tools.irrigation_schedule())
        assert schedule == agenda.model_dump(mode="json", by_alias=True)
        assert schedule["counts"]["max_schedules"] == 24
        assert set(schedule["bounds"]) == {"drip", "fert_drip", "mister", "fert_mister"}
        by_day = tool_body(
            await tools.irrigation_history("minutes", today.isoformat(), today.isoformat(), group_by="line")
        )
        assert by_day == minutes.model_dump(mode="json", by_alias=True)
        assert by_day["from"] == today.isoformat() and any(row["group"] == "L01" for row in by_day["rows"])
        page = tool_body(
            await tools.irrigation_history("runs", (f.at - 24 * H).isoformat(), f.at.isoformat(), line="L01")
        )
        assert page == runs.model_dump(mode="json", by_alias=True)
        assert [item["line_id"] for item in page["items"]] == ["L01"]

    scenario(context_db, body)


def test_the_tools_refuse_as_the_design_says(context_db, tools):
    async def body(f):
        await f.reporting()
        wide = await tools.irrigation_history("runs", (f.at - 40 * 24 * H).isoformat(), f.at.isoformat())
        assert wide.isError is True and json.loads(wide.content[0].text)["code"] == "window_too_large"
        foreign = await tools.irrigation_history("minutes", "2026-11-01", "2026-11-02", outcome="ok")
        problem_body = json.loads(foreign.content[0].text)
        assert foreign.isError is True and (problem_body["code"], problem_body["field"]) == (
            "invalid_request",
            "outcome",
        )

    scenario(context_db, body)
