"""VD-41 (#1156) on a private PostgreSQL: the irrigation writes as the dashboard and MCP logins.

jvallery/agents#5994's campaign step runs each ``tests/test_dashboard_db_*.py`` file against a fresh loopback
PostgreSQL with TimescaleDB and hands it over in ``VERDIFY_ISOLATED_TEST_DSN``; without one this module skips.
``irrigation_db_support.prepare_irrigation_tables`` builds M4, the dashboard login (282) and M3 (283). This file adds
M1 and what the line reader touches from db/schema.sql, as VD-40's test does, and stand-ins for the dashboard login's
M2b grants (VD-74, #1233). The MCP login gets only what its own migrations give it, so a column it may not write (the
check) fails here as it would in production. The writes commit (``recorded_write`` opens its own transaction), so
each test starts from the seed: M3's lines and its three vanda-degraded schedules, ``irr_engine`` false.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import math
import uuid
from zoneinfo import ZoneInfo

import irrigation_db_support as support
import jsonschema
import pytest
import yaml
from conftest import isolated_mutation_test_dsn

from verdify_schemas.irrigation import IrrFault, IrrRun, IrrState, format_irr_state
from verdify_services.actor import Actor
from verdify_services.irrigation import writes
from verdify_services.problems import ServiceError

asyncpg = pytest.importorskip("asyncpg")

try:
    DSN = isolated_mutation_test_dsn()
except ValueError:
    DSN = None

pytestmark = pytest.mark.skipif(DSN is None, reason="needs VERDIFY_ISOLATED_TEST_DSN (campaign private PostgreSQL)")

DASHBOARD, MCP = "verdify_dashboard_runtime_login", "verdify_mcp_runtime_login"
LOGINS = (DASHBOARD, MCP)
PERSON = Actor.person("jason", "operator")
AGENT = Actor.agent("steward")
IRIS = Actor.iris()
ACTORS = {DASHBOARD: PERSON, MCP: AGENT}
OPENAPI = yaml.safe_load((support.REPO_ROOT / "docs/design/dashboard/openapi/dashboard-api.v1.yaml").read_text())
M1 = support.REPO_ROOT / "db/migrations/280-desired-state-activity-log.sql"
DENVER = ZoneInfo("America/Denver")

# What the line reader touches beyond irrigation_db_support's subset, from db/schema.sql in dependency order.
READS_OBJECTS = (
    ("idx_system_state_entity", "INDEX"),
    ("alert_log", "TABLE"),
    ("alert_log_id_seq", "SEQUENCE"),
    ("alert_log_id_seq", "SEQUENCE OWNED BY"),
    ("alert_log id", "DEFAULT"),
    ("alert_log alert_log_pkey", "CONSTRAINT"),
    ("idx_alert_log_type", "INDEX"),
    ("slack_alert_runbooks", "TABLE"),
    ("setpoint_snapshot", "TABLE"),
    ("setpoint_snapshot_ts_idx", "INDEX"),
    ("idx_snapshot_param", "INDEX"),
    ("setpoint_clamps", "TABLE"),
    ("plan_journal", "TABLE"),
    ("plan_journal plan_journal_pkey", "CONSTRAINT"),
    ("v_active_plan", "VIEW"),
    ("crop_band_anchors", "TABLE"),
    ("diagnostics", "TABLE"),
)
# 259 grants these to the MCP duty; VD-74 grants them, and the rest below, to the dashboard duty.
STAND_IN_GRANTS = """
GRANT SELECT ON public.alert_log, public.climate, public.equipment_state, public.setpoint_changes,
    public.setpoint_clamps, public.setpoint_snapshot, public.slack_alert_runbooks, public.system_state,
    public.v_active_plan, public.diagnostics TO verdify_mcp_runtime, verdify_dashboard_runtime;
GRANT SELECT ON public.v_desired_state_current, public.v_irrigation_day_coverage TO verdify_dashboard_runtime;
GRANT SELECT, INSERT ON public.activity_log TO verdify_dashboard_runtime;
GRANT USAGE ON SEQUENCE public.activity_log_id_seq TO verdify_dashboard_runtime;
GRANT EXECUTE ON FUNCTION public.fn_irrigation_minutes_daily(date, date),
    public.fn_mcp_armed_assignment_context() TO verdify_dashboard_runtime;
"""


def run(coro):
    return asyncio.run(coro)


async def connect(user: str | None = None):
    return await asyncpg.connect(DSN, user=user) if user else await asyncpg.connect(DSN)


async def admin(sql: str, *args):
    conn = await connect()
    try:
        return await (conn.fetch(sql, *args) if args or "SELECT" in sql[:12].upper() else conn.execute(sql))
    finally:
        await conn.close()


def sql(statement: str, *args):
    return run(admin(statement, *args))


def counts() -> tuple[int, int, int]:
    """Schedules past the three seeds, commands and activity rows."""
    [row] = sql(
        "SELECT (SELECT count(*) FROM public.irrigation_slot WHERE slot_id > 3) AS slots, "
        "(SELECT count(*) FROM public.irrigation_command) AS commands, "
        "(SELECT count(*) FROM public.activity_log) AS activity"
    )
    return row["slots"], row["commands"], row["activity"]


def activity_rows() -> list[dict]:
    rows = sql(
        "SELECT source, actor, action, target, detail::text AS detail, reason, request_id "
        "FROM public.activity_log ORDER BY id"
    )
    return [{**dict(row), "detail": json.loads(row["detail"])} for row in rows]


def openapi_errors(name: str, instance) -> list[str]:
    validator = jsonschema.Draft202012Validator(
        {"$ref": f"#/components/schemas/{name}", "components": OPENAPI["components"]}
    )
    return [error.message for error in validator.iter_errors(instance)]


@pytest.fixture(scope="module")
def seeds():
    async def setup():
        conn = await connect()
        try:
            await support.prepare_irrigation_tables(conn)
            sections = support.schema_sections()
            await conn.execute("SET ROLE verdify")
            try:
                await conn.execute("\n".join(sections[(name, kind, "public")] for name, kind in READS_OBJECTS))
                await conn.execute("SET search_path = pg_catalog, public, pg_temp;\n" + support.migration_body(M1))
            finally:
                await conn.execute("RESET search_path")
                await conn.execute("RESET ROLE")
            await conn.execute(STAND_IN_GRANTS)
            return [dict(row) for row in await conn.fetch("SELECT * FROM public.irrigation_line")]
        finally:
            await conn.close()

    return run(setup())


@pytest.fixture(autouse=True)
def seeded(seeds):
    async def reset():
        conn = await connect()
        try:
            await conn.execute(
                "TRUNCATE public.equipment_state, public.climate, public.system_state, public.data_gaps, public.diagnostics, "
                "public.setpoint_snapshot, public.activity_log, public.desired_state, public.irrigation_ledger, "
                "public.irrigation_command, public.irrigation_push"
            )
            await conn.execute("DELETE FROM public.irrigation_slot WHERE slot_id > 3")
            await conn.execute("UPDATE public.irrigation_slot SET manual = false, enabled = true")
            for row in seeds:
                await conn.execute(
                    "UPDATE public.irrigation_line SET enabled = $2, vpd_member = $3, schedule_member = $4, "
                    "daily_cap_min = $5, vpd_reserve_min = $6, paused_until = NULL, manual = false, checked_by = NULL, "
                    "checked_at = NULL, check_note = NULL WHERE line_id = $1",
                    row["line_id"],
                    row["enabled"],
                    row["vpd_member"],
                    row["schedule_member"],
                    row["daily_cap_min"],
                    row["vpd_reserve_min"],
                )
            await conn.execute(
                "INSERT INTO public.setpoint_snapshot (ts, parameter, value, greenhouse_id) VALUES "
                "(now() - interval '30 seconds', 'irrig_wall_duration_min', 12, 'vallery'), "
                "(now() - interval '30 seconds', 'sw_irrig_enabled', 1, 'vallery'), "
                "(now() - interval '30 seconds', 'sw_irrig_wall_enabled', 1, 'vallery')"
            )
            await conn.execute(
                "INSERT INTO public.system_state (ts, entity, value, greenhouse_id) "
                "VALUES (now() - interval '1 hour', 'irr_engine', 'false', 'vallery')"
            )
        finally:
            await conn.close()

    run(reset())


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
        await asyncio.sleep(0.2)
        return self.payloads

    async def __aexit__(self, *exc):
        await self.conn.close()
        return False


async def as_login(login: str, write, *args, **kwargs):
    conn = await connect(login)
    try:
        return await write(conn, *args, **kwargs)
    finally:
        await conn.close()


def call(login: str, write, *args, **kwargs):
    return run(as_login(login, write, *args, **kwargs))


def refused(login: str, write, *args, **kwargs):
    with pytest.raises(ServiceError) as excinfo:
        call(login, write, *args, **kwargs)
    return excinfo.value.problem


def schedule_fields(line_id="L01", clock="07:30", **extra) -> dict:
    return {
        "line_id": line_id,
        "days": ["mon", "wed"],
        "start": {"kind": "clock", "clock": clock},
        "on_s": 600,
        **extra,
    }


def set_engine(state_text: str | None = None) -> None:
    """The controller has the engine, with this irr_state as its newest."""
    sql(
        "INSERT INTO public.system_state (ts, entity, value, greenhouse_id) "
        "VALUES (now(), 'irr_engine', 'true', 'vallery')"
    )
    if state_text is not None:
        sql(
            "INSERT INTO public.system_state (ts, entity, value, greenhouse_id) "
            "VALUES (now(), 'irr_state', $1, 'vallery')",
            state_text,
        )


def enable_line(line_id: str, *, checked: bool = False, schedule_member: bool = False) -> None:
    sets = ["enabled = true"]
    if checked:
        sets += ["checked_by = 'jason'", "checked_at = now()"]
    if schedule_member:
        sets.append("schedule_member = true")
    sql(f"UPDATE public.irrigation_line SET {', '.join(sets)} WHERE line_id = '{line_id}'")


def next_local(hour: int, minute: int) -> dt.datetime:
    """The next time the America/Denver clock reads hour:minute at least five minutes on, so a run token's end stays
    ahead for the whole test."""
    now = dt.datetime.now(DENVER)
    moment = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return moment if moment > now + dt.timedelta(minutes=5) else moment + dt.timedelta(days=1)


def run_token(line_id: str, end: dt.datetime | None = None, cause: str = "schedule", slot_id: int | None = 7) -> IrrRun:
    end = end or dt.datetime.now(dt.UTC) + dt.timedelta(minutes=9)
    start = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)
    return IrrRun(
        line_id=line_id,
        cause=cause,
        slot_id=slot_id,
        start_epoch=int(start.timestamp()),
        end_epoch=int(end.timestamp()),
    )


# ── schedules ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("login", LOGINS)
def test_two_creates_at_23_schedules_one_wins_and_exactly_24_rows_exist(login):
    for minute in range(20):
        sql(
            "INSERT INTO public.irrigation_slot (line_id, enabled, days, start_kind, start_min, on_s, source, "
            "updated_by) VALUES ('L01', false, 127, 'clock', $1, 600, 'system', 'test')",
            minute,
        )
    actor = ACTORS[login]

    async def both():
        return await asyncio.gather(
            *(
                as_login(login, writes.schedule_create, actor, schedule_fields("L05", clock), manual=True)
                for clock in ("05:00", "15:00")
            ),
            return_exceptions=True,
        )

    results = run(both())
    errors = [result for result in results if isinstance(result, ServiceError)]
    created = [result for result in results if not isinstance(result, Exception)]
    assert len(created) == 1 and len(errors) == 1, results
    problem = errors[0].problem
    assert (problem.code, problem.status, problem.retryable) == ("too_many_schedules", 422, False)
    assert (problem.current, problem.max) == (24, 24)
    [row] = sql("SELECT count(*) AS n FROM public.irrigation_slot")
    assert row["n"] == 24
    assert len(activity_rows()) == 1


@pytest.mark.parametrize("login", LOGINS)
def test_a_create_stores_its_row_its_activity_row_and_a_wake(login):
    actor = ACTORS[login]

    async def check():
        async with Listener() as listener:
            result = await as_login(
                login,
                writes.schedule_create,
                actor,
                schedule_fields(label="wall", on_s=600.4),
                manual=True,
                reason="first run",
                trigger_id="t-1",
                planner_instance="opus",
            )
            return result, await listener.settle()

    result, payloads = run(check())
    assert payloads == [""]
    schedule = result.value
    body = result.rest_json()
    assert openapi_errors("Schedule", body) == []
    assert result.replayed is False and schedule.slot_id > 3
    assert (schedule.line_id, schedule.on_s, schedule.count, schedule.manual, schedule.sync) == (
        "L01",
        600,  # snapped to whole seconds
        1,
        True,
        "requested",  # applies with the irrigation firmware
    )
    assert body["start"] == {"kind": "clock", "clock": "07:30"} and body["days"] == ["mon", "wed"]
    assert body["updated_by"] == {"source": actor.source, "name": actor.name} and schedule.manual_before is None
    [row] = sql("SELECT * FROM public.irrigation_slot WHERE slot_id = $1", schedule.slot_id)
    assert (row["days"], row["start_kind"], row["start_min"], row["source"], row["updated_by"], row["reason"]) == (
        0b101,
        "clock",
        450,
        actor.source,
        actor.name,
        "first run",
    )
    [entry] = activity_rows()
    assert (entry["action"], entry["target"], entry["source"], entry["actor"], entry["reason"]) == (
        "irrigation.schedule.create",
        str(schedule.slot_id),
        actor.source,
        actor.name,
        "first run",
    )
    detail = entry["detail"]
    assert detail["ref"] == {"table": "irrigation_slot", "id": schedule.slot_id} and detail["line_id"] == "L01"
    assert detail["before"] is None and detail["value"]["on_s"] == 600
    assert detail["manual"] == {"before": None, "after": True}
    assert (detail["trigger_id"], detail["planner_instance"]) == ("t-1", "opus")


def test_the_flag_is_stored_as_given_and_a_create_must_give_one():
    made = call(MCP, writes.schedule_create, AGENT, schedule_fields(), manual=False).value
    assert made.manual is False
    problem = refused(MCP, writes.schedule_create, AGENT, schedule_fields("L05"))
    assert (problem.code, problem.field) == ("invalid_request", "manual")
    assert counts()[0] == 1


@pytest.mark.parametrize(
    ("fields", "code", "field", "low", "high"),
    [
        (schedule_fields(on_s=7201), "out_of_range", "on_s", 10, 7200),
        (schedule_fields("L04", on_s=91, gap_s=60), "out_of_range", "on_s", 10, 90),
        (schedule_fields("L04", on_s=60, gap_s=10), "out_of_range", "gap_s", 30, 600),
        (schedule_fields(count=2), "out_of_range", "count", 1, 1),
        (schedule_fields(prewet_s=30), "out_of_range", "prewet_s", 0, 0),
        (schedule_fields("L02", flush_s=1801), "out_of_range", "flush_s", 0, 1800),
        (schedule_fields(degraded_only=True), "out_of_range", "degraded_only", 0, 0),
        (
            {"line_id": "L01", "days": ["mon"], "start": {"kind": "sunset", "offset_min": 241}, "on_s": 60},
            "out_of_range",
            "start.offset_min",
            -240,
            240,
        ),
        (schedule_fields("L03"), "unknown_target", "line_id", None, None),
        (schedule_fields("L11"), "unknown_target", "line_id", None, None),
        (schedule_fields("L12"), "unknown_target", "line_id", None, None),
    ],
)
def test_a_create_outside_the_rules_is_refused_with_its_bounds_and_stores_nothing(fields, code, field, low, high):
    problem = refused(MCP, writes.schedule_create, AGENT, fields, manual=False)
    assert (problem.code, problem.status, problem.field, problem.min, problem.max) == (code, 422, field, low, high)
    assert counts() == (0, 0, 0)


def test_two_starts_on_one_line_under_30_minutes_apart_are_too_close_unless_one_is_disabled():
    call(MCP, writes.schedule_create, AGENT, schedule_fields(clock="07:30"), manual=False)
    problem = refused(MCP, writes.schedule_create, AGENT, schedule_fields(clock="07:45"), manual=False)
    assert (problem.code, problem.field, problem.retryable) == ("too_close", "start", False)
    assert call(MCP, writes.schedule_create, AGENT, schedule_fields(clock="08:00"), manual=False).value.enabled
    assert call(MCP, writes.schedule_create, AGENT, schedule_fields("L05", clock="07:35"), manual=False).value
    disabled = schedule_fields(clock="07:45", enabled=False)
    assert call(MCP, writes.schedule_create, AGENT, disabled, manual=False).value.enabled is False


def test_a_schedule_on_a_disabled_line_or_a_zone_off_schedule_is_stored():
    on_disabled_line = call(MCP, writes.schedule_create, AGENT, schedule_fields("L02"), manual=False).value
    zone_off = schedule_fields("L07", on_s=45, count=4, gap_s=60)
    assert on_disabled_line.line_id == "L02"
    assert call(MCP, writes.schedule_create, AGENT, zone_off, manual=False).value.line_id == "L07"
    assert counts()[0] == 2


@pytest.mark.parametrize("login", LOGINS)
def test_an_edit_applies_only_the_fields_it_carries(login):
    actor = ACTORS[login]
    made = call(login, writes.schedule_create, actor, schedule_fields(label="wall"), manual=False).value
    edited = call(login, writes.schedule_update, actor, made.slot_id, {"on_s": 900}, reason="longer")
    assert edited.value.on_s == 900 and edited.value.label == "wall" and edited.value.days == ["mon", "wed"]
    assert edited.value.manual is False and edited.value.manual_before is False  # no flag given: it stays
    assert openapi_errors("Schedule", edited.rest_json()) == []
    entry = activity_rows()[-1]
    assert (entry["action"], entry["target"], entry["reason"]) == (
        "irrigation.schedule.update",
        str(made.slot_id),
        "longer",
    )
    assert (entry["detail"]["before"]["on_s"], entry["detail"]["value"]["on_s"]) == (600, 900)
    assert entry["detail"]["manual"] == {"before": False, "after": False}
    disabled = call(login, writes.schedule_update, actor, made.slot_id, {"enabled": False}, manual=True).value
    assert (disabled.enabled, disabled.manual, disabled.on_s, disabled.manual_before) == (False, True, 900, False)
    problem = refused(login, writes.schedule_update, actor, 9999, {"enabled": False})
    assert (problem.code, problem.field) == ("unknown_target", "slot_id")


def test_iris_leaves_a_manual_schedule_alone_and_an_agent_may_clear_it():
    made = call(DASHBOARD, writes.schedule_create, PERSON, schedule_fields(), manual=True).value
    before = counts()
    problem = refused(MCP, writes.schedule_update, IRIS, made.slot_id, {"on_s": 900}, manual=False)
    assert (problem.code, problem.status, problem.retryable) == ("manual", 422, False)
    problem = refused(MCP, writes.schedule_delete, IRIS, made.slot_id)
    assert problem.code == "manual" and counts() == before
    [row] = sql("SELECT on_s, manual FROM public.irrigation_slot WHERE slot_id = $1", made.slot_id)
    assert (row["on_s"], row["manual"]) == (600, True)

    result = call(MCP, writes.schedule_update, AGENT, made.slot_id, {"on_s": 900}, manual=False).value
    assert (result.on_s, result.manual, result.manual_before) == (900, False, True)
    assert activity_rows()[-1]["detail"]["manual"] == {"before": True, "after": False}
    assert call(MCP, writes.schedule_update, IRIS, made.slot_id, {"on_s": 1200}, manual=False).value.on_s == 1200


@pytest.mark.parametrize("login", LOGINS)
def test_a_delete_keeps_the_fields_in_the_activity_row_and_never_reuses_the_id(login):
    actor = ACTORS[login]
    made = call(login, writes.schedule_create, actor, schedule_fields(), manual=True).value
    sql(
        "INSERT INTO public.irrigation_ledger (line_id, slot_id, local_day, cause, outcome) "
        "VALUES ('L01', $1, current_date, 'schedule', 'ok')",
        made.slot_id,
    )
    result = call(login, writes.schedule_delete, actor, made.slot_id, reason="not needed")
    assert (result.value.slot_id, result.value.manual_before, result.value.deleted) == (made.slot_id, True, True)
    assert counts()[0] == 0
    entry = activity_rows()[-1]
    assert (entry["action"], entry["target"], entry["reason"]) == (
        "irrigation.schedule.delete",
        str(made.slot_id),
        "not needed",
    )
    assert entry["detail"]["before"]["on_s"] == 600 and entry["detail"]["value"] is None
    [ledger] = sql("SELECT count(*) AS n FROM public.irrigation_ledger WHERE slot_id = $1", made.slot_id)
    assert ledger["n"] == 1
    assert call(login, writes.schedule_create, actor, schedule_fields(), manual=True).value.slot_id > made.slot_id
    assert refused(login, writes.schedule_delete, actor, made.slot_id).code == "unknown_target"
    assert refused(login, writes.schedule_delete, actor, 70000).code == "unknown_target"


def test_a_failed_activity_row_takes_the_schedule_with_it():
    async def check():
        await admin("REVOKE INSERT ON public.activity_log FROM verdify_dashboard_runtime")
        try:
            async with Listener() as listener:
                with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
                    await as_login(DASHBOARD, writes.schedule_create, PERSON, schedule_fields(), manual=True)
                return await listener.settle()
        finally:
            await admin("GRANT INSERT ON public.activity_log TO verdify_dashboard_runtime")

    assert run(check()) == []
    assert counts() == (0, 0, 0)


# ── lines: pause and manual ───────────────────────────────────────────────────


@pytest.mark.parametrize("login", LOGINS)
def test_a_pause_keeps_the_flag_and_writes_one_row_that_names_its_actor(login):
    actor = ACTORS[login]
    sql("UPDATE public.irrigation_line SET manual = true WHERE line_id = 'L01'")
    until = dt.datetime.now(dt.UTC) + dt.timedelta(hours=4)

    async def check():
        async with Listener() as listener:
            result = await as_login(
                login, writes.line_update, actor, "L01", {"paused_until": until.isoformat()}, reason="rain tonight"
            )
            return result, await listener.settle()

    result, payloads = run(check())
    assert payloads == [""]
    [line] = result.value.lines
    assert openapi_errors("IrrigationLine", line.model_dump(mode="json")) == []
    assert (line.line_id, line.manual, line.manual_before) == ("L01", True, True)
    assert line.paused_until == until
    assert line.paused_by.model_dump() == {"source": actor.source, "name": actor.name}
    assert line.pause_reason == "rain tonight" and line.sync == "requested"
    [entry] = activity_rows()
    assert (entry["action"], entry["target"], entry["reason"], entry["source"]) == (
        "irrigation.line.update",
        "L01",
        "rain tonight",
        actor.source,
    )
    assert dt.datetime.fromisoformat(entry["detail"]["paused_until"]) == until
    assert entry["detail"]["manual"] == {"before": True, "after": True}

    resumed = call(login, writes.line_update, actor, "L01", {"paused_until": None}).value.lines[0]
    assert resumed.paused_until is None and resumed.paused_by is None and resumed.pause_reason is None
    assert activity_rows()[-1]["detail"]["paused_until"] is None
    [row] = sql("SELECT paused_until, manual, updated_by FROM public.irrigation_line WHERE line_id = 'L01'")
    assert (row["paused_until"], row["manual"], row["updated_by"]) == (None, True, actor.name)


def test_a_pause_at_or_before_now_resumes():
    past = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=5)
    line = call(MCP, writes.line_update, AGENT, "L04", {"paused_until": past.isoformat()}).value.lines[0]
    assert line.paused_until is None
    assert activity_rows()[-1]["detail"]["paused_until"] is None


@pytest.mark.parametrize("login", LOGINS)
def test_all_pauses_every_line_and_each_names_the_actor(login):
    actor = ACTORS[login]
    until = dt.datetime.now(dt.UTC) + dt.timedelta(hours=2)
    sql("UPDATE public.irrigation_line SET manual = true WHERE line_id = 'L09'")
    result = call(login, writes.line_update, actor, "all", {"paused_until": until.isoformat()}, reason="work day")
    assert len(result.value.lines) == 11
    for line in result.value.lines:
        assert line.paused_until == until and line.pause_reason == "work day"
        assert line.paused_by.model_dump() == {"source": actor.source, "name": actor.name}
        assert line.manual_before is (line.line_id == "L09") and line.manual is (line.line_id == "L09")
    [entry] = activity_rows()
    assert (entry["target"], entry["reason"]) == ("all", "work day")
    assert dt.datetime.fromisoformat(entry["detail"]["paused_until"]) == until
    assert entry["detail"]["lines_before"]["L01"] == {"paused_until": None, "manual": False}
    resumed = call(login, writes.line_update, actor, "all", {"paused_until": None}).value.lines
    assert all(line.paused_until is None and line.paused_by is None for line in resumed)


@pytest.mark.parametrize(
    ("fields", "manual", "field"),
    [
        ({"paused_until": None, "enabled": True}, None, "enabled"),
        ({"daily_cap_min": 30}, None, "daily_cap_min"),
        ({"paused_until": None}, True, "manual"),
        ({}, None, "paused_until"),
    ],
)
def test_all_takes_only_paused_until_and_reason(fields, manual, field):
    problem = refused(DASHBOARD, writes.line_update, PERSON, "all", fields, manual=manual)
    assert (problem.code, problem.status, problem.field) == ("invalid_request", 400, field)
    assert counts() == (0, 0, 0)


def test_iris_is_refused_on_a_manual_line_and_on_all_while_any_is_manual_but_its_stop_is_accepted():
    sql("UPDATE public.irrigation_line SET manual = true WHERE line_id IN ('L01', 'L09')")
    until = (dt.datetime.now(dt.UTC) + dt.timedelta(hours=2)).isoformat()
    problem = refused(MCP, writes.line_update, IRIS, "L01", {"paused_until": until})
    assert (problem.code, problem.status, problem.retryable) == ("manual", 422, False)
    problem = refused(MCP, writes.line_update, IRIS, "all", {"paused_until": until})
    assert problem.code == "manual" and "L01" in problem.detail and "L09" in problem.detail
    [row] = sql("SELECT count(*) AS n FROM public.irrigation_line WHERE paused_until IS NOT NULL")
    assert row["n"] == 0 and counts() == (0, 0, 0)

    stop = call(MCP, writes.stop, IRIS, "L01").value
    assert (stop.kind, stop.status) == ("stop", "pending")
    assert call(MCP, writes.line_update, IRIS, "L04", {"paused_until": until}).value.lines[0].paused_by.name == "iris"
    assert call(MCP, writes.line_update, AGENT, "L01", {"paused_until": until}).value.lines[0].paused_by.name == (
        "steward"
    )


def test_an_agent_clears_a_manual_line_with_its_definition_edit_and_the_row_says_so():
    sql("UPDATE public.irrigation_line SET manual = true WHERE line_id = 'L01'")
    line = call(MCP, writes.line_update, AGENT, "L01", {"daily_cap_min": 45}, manual=False).value.lines[0]
    assert (line.daily_cap_min, line.manual, line.manual_before) == (45, False, True)
    detail = activity_rows()[-1]["detail"]
    assert detail["manual"] == {"before": True, "after": False}
    assert (detail["before"], detail["value"]) == ({"daily_cap_min": 60}, {"daily_cap_min": 45})


# ── lines: the one-time check, flags and caps ─────────────────────────────────


def test_enabling_needs_a_check_a_person_records_and_then_agents_change_the_line_freely():
    problem = refused(MCP, writes.line_update, AGENT, "L06", {"enabled": True}, manual=False)
    assert (problem.code, problem.status, problem.retryable, problem.field) == (
        "check_required",
        422,
        False,
        "enabled",
    )
    assert counts() == (0, 0, 0)

    noted = call(
        DASHBOARD, writes.line_update, PERSON, "L06", {"checked": True, "check_note": "valve and emitters good"}
    ).value.lines[0]
    assert noted.enabled is False and noted.manual is False  # the check alone neither enables nor changes manual
    assert (noted.check.checked_by, noted.check.note) == ("jason", "valve and emitters good")
    assert activity_rows()[-1]["detail"]["value"] == {"checked": True}

    line = call(MCP, writes.line_update, AGENT, "L06", {"enabled": True}, manual=False).value.lines[0]
    assert line.enabled is True and line.check.checked_by == "jason"


def test_the_same_body_may_carry_the_check_and_the_enable_only_from_a_person():
    problem = refused(DASHBOARD, writes.line_update, PERSON, "L02", {"enabled": True}, manual=True)
    assert problem.code == "check_required"
    line = call(DASHBOARD, writes.line_update, PERSON, "L02", {"enabled": True, "checked": True}, manual=True)
    assert line.value.lines[0].enabled is True and line.value.lines[0].check.checked_by == "jason"
    for fields in ({"checked": True}, {"checked": False}, {"check_note": "ok"}):
        problem = refused(MCP, writes.line_update, AGENT, "L08", fields)
        assert (problem.code, problem.status) == ("forbidden", 403)
    assert refused(MCP, writes.line_update, IRIS, "L08", {"checked": True}).code == "forbidden"
    problem = refused(DASHBOARD, writes.line_update, PERSON, "L08", {"check_note": "no check"})
    assert (problem.code, problem.field) == ("invalid_request", "check_note")


def test_schedule_on_a_mister_needs_the_check_and_vpd_does_not():
    problem = refused(MCP, writes.line_update, AGENT, "L04", {"schedule_member": True}, manual=False)
    assert (problem.code, problem.field) == ("check_required", "schedule_member")
    line = call(MCP, writes.line_update, AGENT, "L07", {"vpd_member": True}, manual=False).value.lines[0]
    assert line.vpd_member is True and line.enabled is False
    assert call(MCP, writes.line_update, AGENT, "L04", {"schedule_member": False}, manual=False).value


@pytest.mark.parametrize("field", ["vpd_member", "schedule_member"])
@pytest.mark.parametrize("line_id", ["L01", "L03", "L08", "L11"])
def test_the_mister_flags_exist_on_l04_l07_and_l09_only(field, line_id):
    problem = refused(DASHBOARD, writes.line_update, PERSON, line_id, {field: False, "checked": True})
    assert (problem.code, problem.status, problem.field) == ("invalid_request", 400, field)
    assert counts() == (0, 0, 0)


@pytest.mark.parametrize(
    ("fields", "field", "low", "high", "current"),
    [
        ({"daily_cap_min": 481}, "daily_cap_min", 0, 480, 120),
        ({"daily_cap_min": -1}, "daily_cap_min", 0, 480, 120),
        ({"vpd_reserve_min": 121}, "vpd_reserve_min", 0, 120, 60),
        ({"daily_cap_min": 30}, "vpd_reserve_min", 0, 30, 60),  # a lower cap under the stored reserve
        ({"daily_cap_min": 0}, "vpd_reserve_min", 0, 0, 60),
    ],
)
def test_the_caps_are_out_of_range_with_their_bounds(fields, field, low, high, current):
    problem = refused(MCP, writes.line_update, AGENT, "L04", fields, manual=False)
    assert (problem.code, problem.status, problem.field, problem.min, problem.max, problem.current) == (
        "out_of_range",
        422,
        field,
        low,
        high,
        current,
    )
    assert counts() == (0, 0, 0)


def test_a_cap_and_its_reserve_move_together():
    line = call(MCP, writes.line_update, AGENT, "L04", {"daily_cap_min": 30.4, "vpd_reserve_min": 10}, manual=False)
    assert (line.value.lines[0].daily_cap_min, line.value.lines[0].vpd_reserve_min) == (30, 10)
    assert refused(MCP, writes.line_update, AGENT, "L12", {"daily_cap_min": 30}).code == "unknown_target"


# ── before the engine ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("login", LOGINS)
def test_before_the_engine_only_the_bridge_runs_and_nothing_else_is_stored_but_a_stop(login):
    actor = ACTORS[login]
    for problem in (
        refused(login, writes.run, actor, "L04", on_s=30, count=2, gap_s=60),
        refused(login, writes.override, actor, "fans", minutes=10),
        refused(login, writes.override, actor, "restart"),
        refused(login, writes.clear_fault, actor, "L04"),
        refused(login, writes.clear_fault, actor),
    ):
        assert (problem.code, problem.status, problem.retryable) == ("unavailable", 503, False)
        assert "arrives with the irrigation firmware" in problem.detail
    assert counts() == (0, 0, 0)

    stop = call(login, writes.stop, actor, "L04").value
    assert (stop.kind, stop.line_id, stop.status, stop.args) == ("stop", "L04", "pending", {"bridge": True})
    ran = call(login, writes.run, actor, "L01").value
    assert ran.args == {"minutes": 12, "bridge": True}
    assert counts() == (0, 2, 2)


def test_schedule_and_line_writes_are_stored_before_the_engine():
    assert call(MCP, writes.schedule_create, AGENT, schedule_fields(enabled=False), manual=False).value.sync == (
        "requested"
    )
    assert call(MCP, writes.line_update, AGENT, "L01", {"daily_cap_min": 90}, manual=False).value.lines[0].sync == (
        "requested"
    )


# ── idempotency ───────────────────────────────────────────────────────────────


def test_each_write_replays_its_stored_object_and_refuses_another_payload():
    set_engine()
    enable_line("L02", checked=True)
    actor = PERSON
    made = call(DASHBOARD, writes.schedule_create, actor, schedule_fields("L05"), manual=True).value
    gone = call(DASHBOARD, writes.schedule_create, actor, schedule_fields("L05", clock="12:00"), manual=True).value
    writes_under_test = [
        (
            lambda key, on_s=600: call(
                DASHBOARD, writes.schedule_create, actor, schedule_fields(on_s=on_s), manual=True, request_id=key
            ),
            {"on_s": 601},
        ),
        (
            lambda key, on_s=900: call(
                DASHBOARD, writes.schedule_update, actor, made.slot_id, {"on_s": on_s}, manual=True, request_id=key
            ),
            {"on_s": 901},
        ),
        (
            lambda key, reason="gone": call(
                DASHBOARD, writes.schedule_delete, actor, gone.slot_id, reason=reason, request_id=key
            ),
            {"reason": "other"},
        ),
        (
            lambda key, daily_cap_min=90: call(
                DASHBOARD,
                writes.line_update,
                actor,
                "L01",
                {"daily_cap_min": daily_cap_min},
                manual=True,
                request_id=key,
            ),
            {"daily_cap_min": 91},
        ),
        (
            lambda key, minutes=5: call(
                DASHBOARD, writes.run, actor, "L02", minutes=minutes, prewet_s=120, flush_s=240, request_id=key
            ),
            {"minutes": 6},
        ),
        (lambda key, line_id="L04": call(DASHBOARD, writes.stop, actor, line_id, request_id=key), {"line_id": "L05"}),
        (
            lambda key, minutes=10: call(DASHBOARD, writes.override, actor, "fans", minutes=minutes, request_id=key),
            {"minutes": 11},
        ),
        (
            lambda key, line_id="L04": call(DASHBOARD, writes.clear_fault, actor, line_id, request_id=key),
            {"line_id": "L07"},
        ),
    ]
    for write, other in writes_under_test:
        key = uuid.uuid4()
        first = write(key)
        stored = counts()
        again = write(key)
        assert (first.replayed, again.replayed) == (False, True)
        assert again.rest_headers() == {"Idempotency-Replayed": "true"}
        assert again.value == first.value and counts() == stored
        with pytest.raises(ServiceError) as excinfo:
            write(key, **other)
        assert (excinfo.value.problem.code, excinfo.value.problem.status) == ("request_id_reused", 422)
        assert counts() == stored
    # A schedule deleted since its create still answers the create's replay; another source's key is its own.
    key = uuid.uuid4()
    created = call(
        DASHBOARD, writes.schedule_create, actor, schedule_fields("L05", clock="16:00"), manual=True, request_id=key
    )
    call(DASHBOARD, writes.schedule_delete, actor, created.value.slot_id)
    replay = call(
        DASHBOARD, writes.schedule_create, actor, schedule_fields("L05", clock="16:00"), manual=True, request_id=key
    )
    assert replay.replayed and replay.value == created.value
    other_source = call(
        MCP, writes.schedule_create, AGENT, schedule_fields("L05", clock="16:00"), manual=True, request_id=key
    )
    assert other_source.replayed is False and other_source.value.slot_id != created.value.slot_id


@pytest.mark.parametrize("login", LOGINS)
def test_the_same_request_twice_at_once_replays_and_is_never_refused_by_what_the_first_stored(login):
    """The lock comes before the replay read (irrigation §8.3 step order). Both requests wait behind a held lock, so
    both are past any read made before it: the first creates, runs or deletes, and the second must replay it, not meet
    its own schedule (too_close), its own run (line_busy) or its own delete (unknown_target)."""
    actor = ACTORS[login]
    set_engine()
    made = call(login, writes.schedule_create, actor, schedule_fields("L05"), manual=True).value

    async def twice(write, *args, **kwargs):
        key = uuid.uuid4()
        holder = await connect()
        try:
            async with holder.transaction():
                await holder.execute(writes._LOCK_SQL, "vallery")
                tasks = [
                    asyncio.create_task(as_login(login, write, actor, *args, request_id=key, **kwargs))
                    for _ in range(2)
                ]
                await asyncio.sleep(0.3)
            return await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            await holder.close()

    for write, args, kwargs in (
        (writes.schedule_create, (schedule_fields(),), {"manual": True}),
        (writes.run, ("L01",), {"minutes": 5}),
        (writes.schedule_delete, (made.slot_id,), {}),
        (writes.line_update, ("L01",), {"fields": {"daily_cap_min": 45}, "manual": True}),
        (writes.stop, ("L01",), {}),
    ):
        stored = counts()
        results = run(twice(write, *args, **kwargs))
        assert sorted(result.replayed for result in results if not isinstance(result, Exception)) == [False, True], (
            write.__name__,
            results,
        )
        assert results[0].value == results[1].value, write.__name__
        slots = {"schedule_create": 1, "schedule_delete": -1}.get(write.__name__, 0)
        commands = int(write in (writes.run, writes.stop))
        assert counts() == (stored[0] + slots, stored[1] + commands, stored[2] + 1), write.__name__


def test_a_request_hashes_alike_from_a_model_and_from_a_mapping_of_the_same_fields():
    model = writes.ScheduleCreate(**schedule_fields(), manual=False, reason="why")
    assert writes._hashed_request(model, True, "why") == writes._hashed_request(
        writes.ScheduleCreate.model_validate(schedule_fields()), True, "why"
    )


# ── run with the engine ───────────────────────────────────────────────────────


@pytest.mark.parametrize("login", LOGINS)
def test_the_run_body_per_line_kind_goes_into_the_commands_args(login):
    actor = ACTORS[login]
    set_engine()
    enable_line("L02", checked=True)
    enable_line("L08", checked=True)
    enable_line("L04", checked=True, schedule_member=True)
    for line_id, body in (
        ("L01", {"minutes": 5}),
        ("L02", {"minutes": 5, "prewet_s": 120, "flush_s": 240}),
        ("L04", {"on_s": 30, "count": 4, "gap_s": 60}),
        ("L08", {"on_s": 30, "count": 4, "gap_s": 60, "prewet_s": 120, "flush_s": 240}),
    ):
        command = call(login, writes.run, actor, line_id, reason="now", trigger_id="t-2", **body).value
        assert (command.kind, command.line_id, command.status, command.args, command.reason) == (
            "run",
            line_id,
            "pending",
            body,
            "now",
        )
        assert openapi_errors("Command", command.model_dump(mode="json")) == []
        entry = activity_rows()[-1]
        assert (entry["action"], entry["target"]) == ("irrigation.run", line_id)
        assert entry["detail"]["args"] == body and entry["detail"]["command_id"] == str(command.command_id)
        assert entry["detail"]["trigger_id"] == "t-2"
    [row] = sql("SELECT args::text AS args FROM public.irrigation_command WHERE line_id = 'L08'")
    assert json.loads(row["args"]) == {"on_s": 30, "count": 4, "gap_s": 60, "prewet_s": 120, "flush_s": 240}


@pytest.mark.parametrize(
    ("line_id", "body", "field"),
    [
        ("L01", {}, "minutes"),
        ("L01", {"on_s": 30, "count": 2, "gap_s": 60}, "minutes"),
        ("L01", {"minutes": 5, "prewet_s": 60}, "prewet_s"),
        ("L02", {"minutes": 5, "prewet_s": 60}, "flush_s"),
        ("L02", {"on_s": 30, "count": 4, "gap_s": 60, "prewet_s": 60, "flush_s": 60}, "minutes"),
        ("L04", {"minutes": 5}, "on_s"),
        ("L04", {"on_s": 30, "count": 4}, "gap_s"),
        ("L04", {"on_s": 30, "count": 4, "gap_s": 60, "prewet_s": 60}, "prewet_s"),
        ("L08", {"on_s": 30, "count": 4, "gap_s": 60, "prewet_s": 120, "flush_s": 240, "minutes": 5}, "minutes"),
        ("L08", {"on_s": 30, "count": 4, "gap_s": 60}, "prewet_s"),
        ("L01", {"minutes": 121}, "minutes"),
        ("L04", {"on_s": 91, "count": 4, "gap_s": 60}, "on_s"),
    ],
)
def test_a_body_outside_its_line_kind_is_invalid_request_naming_the_field(line_id, body, field):
    set_engine()
    problem = refused(MCP, writes.run, AGENT, line_id, **body)
    assert (problem.code, problem.status, problem.field) == ("invalid_request", 400, field)
    assert counts() == (0, 0, 0)


def test_a_fractional_run_snaps_to_whole_numbers():
    set_engine()
    assert call(MCP, writes.run, AGENT, "L01", minutes=5.4).value.args == {"minutes": 5}


@pytest.mark.parametrize("line_id", ["L03", "L11"])
def test_the_lines_that_take_no_runs_are_unknown_target(line_id):
    set_engine()
    problem = refused(MCP, writes.run, AGENT, line_id, minutes=5)
    assert (problem.code, problem.status, problem.field) == ("unknown_target", 422, "line_id")
    assert counts() == (0, 0, 0)


def test_a_run_refused_on_a_disabled_paused_faulted_or_non_member_line_in_that_order():
    latch = IrrFault(line_id="L08", fault="mister_fault", until_epoch=int(dt.datetime.now(dt.UTC).timestamp()) + 3600)
    busy = run_token("L07")
    set_engine(format_irr_state(IrrState(runs=(busy,), faults=(latch,))))
    body = {"on_s": 30, "count": 4, "gap_s": 60}
    # Everything at once on L07: disabled, paused, latched through its fertilizer line, Schedule off, running.
    sql("UPDATE public.irrigation_line SET paused_until = now() + interval '1 hour' WHERE line_id = 'L07'")
    problem = refused(MCP, writes.run, AGENT, "L07", **body)
    assert (problem.code, problem.status, problem.retryable) == ("line_disabled", 422, False)
    sql("UPDATE public.irrigation_line SET enabled = true WHERE line_id = 'L07'")
    problem = refused(MCP, writes.run, AGENT, "L07", **body)
    assert (problem.code, problem.retryable) == ("line_paused", False) and problem.current
    sql("UPDATE public.irrigation_line SET paused_until = NULL WHERE line_id = 'L07'")
    problem = refused(MCP, writes.run, AGENT, "L07", **body)
    assert (problem.code, problem.retryable) == ("line_faulted", False) and "L08" in problem.detail
    set_engine(format_irr_state(IrrState(runs=(busy,))))
    problem = refused(MCP, writes.run, AGENT, "L07", **body)
    assert (problem.code, problem.retryable) == ("not_schedule_member", False)
    sql("UPDATE public.irrigation_line SET schedule_member = true WHERE line_id = 'L07'")
    problem = refused(MCP, writes.run, AGENT, "L07", **body)
    assert (problem.code, problem.retryable) == ("line_busy", True) and problem.retry_after_s > 0
    assert counts() == (0, 0, 0)


@pytest.mark.parametrize(
    ("version", "accepted"),
    [
        (None, False),  # no diagnostics row in the last day: keep the refusal
        ("2026.11.5.0131.57fe2a2e", False),  # the firmware before #1343
        ("garbage", False),
        ("2026.11.6.0900.abcdef12", True),
        ("2026.12.1.1200.abcdef12", True),
    ],
)
def test_a_mister_runs_with_schedule_off_once_the_controller_runs_the_1343_firmware(version, accepted):
    set_engine()
    enable_line("L07")
    if version is not None:
        sql("INSERT INTO public.diagnostics (ts, firmware_version) VALUES (now() - interval '1 minute', $1)", version)
    body = {"on_s": 30, "count": 2, "gap_s": 60}
    if accepted:
        command = call(MCP, writes.run, AGENT, "L07", **body).value
        assert (command.kind, command.line_id, command.status, command.args) == ("run", "L07", "pending", body)
    else:
        problem = refused(MCP, writes.run, AGENT, "L07", **body)
        assert (problem.code, problem.retryable) == ("not_schedule_member", False)
        assert counts() == (0, 0, 0)


def test_a_back_off_does_not_refuse_a_run_and_a_fertilizer_mister_needs_no_schedule_flag():
    until = int(dt.datetime.now(dt.UTC).timestamp()) + 600
    set_engine(format_irr_state(IrrState(faults=(IrrFault(line_id="L04", fault="backing_off", until_epoch=until),))))
    enable_line("L04", schedule_member=True)
    enable_line("L08")
    assert call(MCP, writes.run, AGENT, "L04", on_s=30, count=2, gap_s=60).value.status == "pending"
    assert (
        call(MCP, writes.run, AGENT, "L08", on_s=30, count=2, gap_s=60, prewet_s=0, flush_s=0).value.status == "pending"
    )


def test_a_vpd_pulse_blocks_no_run_on_its_own_zone_or_any_other():
    pulse = run_token("L04", cause="vpd", slot_id=None)
    set_engine(format_irr_state(IrrState(runs=(pulse,))))
    enable_line("L04", schedule_member=True)
    enable_line("L07", schedule_member=True)
    assert call(MCP, writes.run, AGENT, "L04", on_s=30, count=2, gap_s=60).value.status == "pending"
    assert call(MCP, writes.run, AGENT, "L07", on_s=30, count=2, gap_s=60).value.status == "pending"
    assert call(MCP, writes.run, AGENT, "L01", minutes=5).value.status == "pending"


def test_a_requested_run_keeps_the_line_busy_until_it_shows_or_ends():
    set_engine()
    first = call(MCP, writes.run, AGENT, "L01", minutes=5).value
    problem = refused(MCP, writes.run, AGENT, "L01", minutes=5)
    assert (problem.code, problem.retryable, problem.retry_after_s) == ("line_busy", True, None)
    sql(
        "UPDATE public.irrigation_command SET status = 'sent', sent_at = now() - interval '10 seconds' "
        "WHERE command_id = $1",
        first.command_id,
    )
    assert refused(MCP, writes.run, AGENT, "L01", minutes=5).code == "line_busy"
    sql(
        "UPDATE public.irrigation_command SET sent_at = now() - interval '31 seconds' WHERE command_id = $1",
        first.command_id,
    )
    second = call(MCP, writes.run, AGENT, "L01", minutes=5).value
    sql(
        "UPDATE public.irrigation_command SET created_at = now() - interval '3 minutes' WHERE command_id = $1",
        second.command_id,
    )  # a pending run this old is never sent
    third = call(MCP, writes.run, AGENT, "L01", minutes=5).value
    assert refused(MCP, writes.run, AGENT, "L01", minutes=5).code == "line_busy"
    call(MCP, writes.stop, AGENT, "L01")  # ended by a later Stop
    assert call(MCP, writes.run, AGENT, "L01", minutes=5).value.command_id != third.command_id
    sql("UPDATE public.irrigation_command SET status = 'failed', fail_reason = 'too old'")
    assert call(MCP, writes.run, AGENT, "L01", minutes=5).value.status == "pending"


def test_two_runs_at_once_on_one_line_one_is_accepted():
    set_engine()

    async def both():
        return await asyncio.gather(
            as_login(DASHBOARD, writes.run, PERSON, "L01", minutes=5),
            as_login(MCP, writes.run, AGENT, "L01", minutes=5),
            return_exceptions=True,
        )

    results = run(both())
    errors = [result for result in results if isinstance(result, ServiceError)]
    assert len(errors) == 1 and errors[0].problem.code == "line_busy", results
    assert counts() == (0, 1, 1)


@pytest.mark.parametrize("login", LOGINS)
def test_another_lines_run_holds_the_manifold_naming_it_and_until_when(login):
    actor = ACTORS[login]
    end = next_local(7, 42)
    enable_line("L02", checked=True)
    enable_line("L08", checked=True)
    set_engine(format_irr_state(IrrState(runs=(run_token("L01", end),))))
    problem = refused(login, writes.run, actor, "L02", minutes=5, prewet_s=120, flush_s=240)
    assert (problem.code, problem.status, problem.retryable) == ("manifold_busy", 422, True)
    assert "L01" in problem.detail and "07:42" in problem.detail
    expected = (end - dt.datetime.now(dt.UTC)).total_seconds()
    assert abs(problem.retry_after_s - expected) < 15
    assert counts() == (0, 0, 0)
    # The engine holds one run at a time, so the drip run refuses a mist run too, as the controller would.
    assert refused(login, writes.run, actor, "L08", on_s=30, count=4, gap_s=60, prewet_s=0, flush_s=0).code == (
        "manifold_busy"
    )

    set_engine(format_irr_state(IrrState()))  # the manifolds idle
    fed = call(login, writes.run, actor, "L02", minutes=5, prewet_s=120, flush_s=240).value
    assert (fed.status, fed.args) == ("pending", {"minutes": 5, "prewet_s": 120, "flush_s": 240})
    misted = call(login, writes.run, actor, "L08", on_s=30, count=4, gap_s=60, prewet_s=120, flush_s=240).value
    assert misted.args == {"on_s": 30, "count": 4, "gap_s": 60, "prewet_s": 120, "flush_s": 240}


def test_an_irr_state_outside_the_grammar_is_no_reading():
    set_engine("not an irr_state")
    assert call(MCP, writes.run, AGENT, "L01", minutes=5).value.status == "pending"


def water_today(equipment: str) -> float:
    """A closed relay run today and a climate row, so M4 reads the day; returns the line's minutes as M4 counts them."""

    async def add():
        conn = await connect()
        try:
            await conn.execute("INSERT INTO public.climate (ts, greenhouse_id) VALUES (now(), 'vallery')")
            at = await conn.fetchval("SELECT now()")
            midnight = at.astimezone(DENVER).replace(hour=0, minute=0, second=0, microsecond=0)
            start = max(midnight.astimezone(dt.UTC), at - dt.timedelta(minutes=8))
            end = min(at, start + dt.timedelta(minutes=6))
            await conn.execute(
                "INSERT INTO public.equipment_state (ts, equipment, state, greenhouse_id) VALUES "
                "($1, $2, true, 'vallery'), ($3, $2, false, 'vallery')",
                start,
                equipment,
                end,
            )
            return await conn.fetchval(writes._TODAY_MINUTES_SQL, "vallery", {"drip_wall": "L01"}.get(equipment, "L04"))
        finally:
            await conn.close()

    return float(run(add()))


@pytest.mark.parametrize("login", LOGINS)
def test_a_drip_past_its_cap_less_today_is_cap_reached(login):
    actor = ACTORS[login]
    set_engine()
    used = water_today("drip_wall")
    cap = math.ceil(used) + 5  # 5 to just under 6 minutes left
    sql("UPDATE public.irrigation_line SET daily_cap_min = $1 WHERE line_id = 'L01'", cap)
    problem = refused(login, writes.run, actor, "L01", minutes=6)
    assert (problem.code, problem.status, problem.retryable, problem.field) == ("cap_reached", 422, False, "minutes")
    assert problem.max == pytest.approx(cap - used, abs=0.11) and problem.current == pytest.approx(used, abs=0.11)
    assert counts() == (0, 0, 0)
    assert call(login, writes.run, actor, "L01", minutes=5).value.status == "pending"


def test_a_mister_counts_its_pulses_and_its_reserve_and_a_cap_of_0_is_none():
    set_engine()
    used = water_today("mister_center")
    cap = math.ceil(used) + 10
    sql(
        "UPDATE public.irrigation_line SET enabled = true, schedule_member = true, daily_cap_min = $1, "
        "vpd_reserve_min = 5 WHERE line_id = 'L04'",
        cap,
    )  # 5 to just under 6 minutes left to runs
    problem = refused(MCP, writes.run, AGENT, "L04", on_s=60, count=6, gap_s=60)
    assert (problem.code, problem.field) == ("cap_reached", "on_s") and "reserve" in problem.detail
    assert call(MCP, writes.run, AGENT, "L04", on_s=60, count=5, gap_s=60).value.status == "pending"
    # No cap on L02 (0, as seeded): any length runs.
    enable_line("L02", checked=True)
    assert call(MCP, writes.run, AGENT, "L02", minutes=120, prewet_s=0, flush_s=0).value.status == "pending"


# ── override and clear-fault with the engine ──────────────────────────────────


@pytest.mark.parametrize("login", LOGINS)
@pytest.mark.parametrize(
    ("kind", "minutes", "args"),
    [
        ("fans", 10, {"kind": "fans", "minutes": 10}),
        ("humidify", 1, {"kind": "humidify", "minutes": 1}),
        ("vent_bypass", 120, {"kind": "vent_bypass", "minutes": 120}),
        ("clear", None, {"kind": "clear"}),
        ("restart", None, {"kind": "restart"}),
    ],
)
def test_an_override_is_one_pending_command_on_no_line(login, kind, minutes, args):
    actor = ACTORS[login]
    set_engine()
    command = call(login, writes.override, actor, kind, minutes=minutes, reason="hot").value
    assert (command.kind, command.line_id, command.status, command.args, command.reason) == (
        "override",
        None,
        "pending",
        args,
        "hot",
    )
    [entry] = activity_rows()
    assert (entry["action"], entry["target"], entry["detail"]["args"]) == ("irrigation.override", kind, args)
    assert entry["detail"]["command_id"] == str(command.command_id)


@pytest.mark.parametrize(
    ("kind", "minutes", "field"),
    [
        ("fans", None, "minutes"),
        ("fans", 0, "minutes"),
        ("humidify", 121, "minutes"),
        ("clear", 5, "minutes"),
        ("restart", 5, "minutes"),
        ("lights", 5, "kind"),
    ],
)
def test_an_override_outside_its_shape_is_invalid_request(kind, minutes, field):
    set_engine()
    problem = refused(MCP, writes.override, AGENT, kind, minutes=minutes)
    assert (problem.code, problem.status, problem.field) == ("invalid_request", 400, field)
    assert counts() == (0, 0, 0)


@pytest.mark.parametrize("login", LOGINS)
def test_clear_fault_takes_a_clean_mister_or_every_mister(login):
    actor = ACTORS[login]
    set_engine()
    for line_id in ("L04", "L07", "L09"):
        command = call(login, writes.clear_fault, actor, line_id).value
        assert (command.kind, command.line_id, command.status) == ("clear_fault", line_id, "pending")
    every = call(login, writes.clear_fault, actor, reason="checked the nozzles").value
    assert (every.line_id, every.reason) == (None, "checked the nozzles")
    assert [(row["action"], row["target"]) for row in activity_rows()] == [
        ("irrigation.clear_fault", "L04"),
        ("irrigation.clear_fault", "L07"),
        ("irrigation.clear_fault", "L09"),
        ("irrigation.clear_fault", "all"),
    ]
    for line_id in ("L01", "L08", "L10", "L11", "L12"):
        problem = refused(login, writes.clear_fault, actor, line_id)
        assert (problem.code, problem.field) == ("unknown_target", "line_id")
    assert counts() == (0, 4, 4)


def test_commands_never_check_manual():
    set_engine()
    sql("UPDATE public.irrigation_line SET manual = true")
    enable_line("L04", schedule_member=True)
    assert call(MCP, writes.run, IRIS, "L01", minutes=5).value.status == "pending"
    assert call(MCP, writes.stop, IRIS).value.status == "pending"
    assert call(MCP, writes.clear_fault, IRIS, "L04").value.status == "pending"
    assert call(MCP, writes.override, IRIS, "fans", minutes=5).value.status == "pending"


# ── the REST bodies ───────────────────────────────────────────────────────────


def test_the_rest_bodies_reach_the_tables_as_a_mapping_of_the_same_fields_does():
    created = writes.ScheduleCreate(**schedule_fields(label="wall", start={"kind": "sunrise", "offset_min": 15}))
    made = call(DASHBOARD, writes.schedule_create, PERSON, created, manual=True, reason=created.reason).value
    assert made.start.model_dump(exclude_none=True) == {"kind": "sunrise", "offset_min": 15} and made.label == "wall"
    edited = writes.ScheduleInput(start={"kind": "clock", "clock": "06:15"})
    changed = call(DASHBOARD, writes.schedule_update, PERSON, made.slot_id, edited, manual=True).value
    assert changed.start.clock == "06:15" and (changed.on_s, changed.label) == (600, "wall")
    until = dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)
    pause = writes.LineUpdate(paused_until=until, reason="rain")
    [line] = call(DASHBOARD, writes.line_update, PERSON, "L01", pause).value.lines
    assert line.paused_until == until and line.pause_reason == "rain" and line.manual is False
    assert activity_rows()[-1]["reason"] == "rain"


# ── what the database cannot hold ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("write", "args", "kwargs", "field"),
    [
        (writes.schedule_create, (schedule_fields(label="a\x00b"),), {"manual": True}, "label"),
        (writes.schedule_create, (schedule_fields(),), {"manual": True, "reason": "a\x00b"}, "reason"),
        (writes.schedule_create, (schedule_fields(),), {"manual": True, "trigger_id": "a\x00b"}, "trigger_id"),
        (writes.schedule_update, (1, {"label": "a\x00b"}), {}, "label"),
        (writes.schedule_delete, (1,), {"reason": "a\x00b"}, "reason"),
        (writes.line_update, ("L06", {"checked": True, "check_note": "a\x00b"}), {}, "check_note"),
        (writes.line_update, ("L06", {"paused_until": None}), {"reason": "a\x00b"}, "reason"),
        (writes.line_update, ("L06", {"paused_until": None}), {"planner_instance": "a\x00b"}, "planner_instance"),
        (writes.line_update, ("all", {"paused_until": None}), {"reason": "a\x00b"}, "reason"),
        (writes.line_update, ("L06", {"paused_until": "9999-12-31T23:59:59-12:00"}), {}, "paused_until"),
        (writes.line_update, ("all", {"paused_until": "9999-12-31T23:59:59-12:00"}), {}, "paused_until"),
        (writes.run, ("L01",), {"reason": "a\x00b"}, "reason"),
        (writes.stop, ("L01",), {"reason": "a\x00b"}, "reason"),
        (writes.clear_fault, ("L04",), {"reason": "a\x00b"}, "reason"),
        (writes.override, ("fans",), {"minutes": 5, "reason": "a\x00b"}, "reason"),
    ],
)
def test_text_or_a_time_the_database_cannot_hold_is_invalid_request_and_stores_nothing(write, args, kwargs, field):
    problem = refused(DASHBOARD, write, PERSON, *args, **kwargs)
    assert (problem.code, problem.status, problem.field) == ("invalid_request", 400, field)
    assert counts() == (0, 0, 0)


def test_a_pause_far_ahead_is_stored_and_one_from_before_the_calendar_starts_resumes():
    far = call(MCP, writes.line_update, AGENT, "L01", {"paused_until": "2200-01-01T00:00:00+02:00"}).value.lines[0]
    assert far.paused_until == dt.datetime(2199, 12, 31, 22, tzinfo=dt.UTC)
    early = call(MCP, writes.line_update, AGENT, "L01", {"paused_until": "0001-01-01T00:00:00+12:00"}).value.lines[0]
    assert early.paused_until is None


# ── who writes ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("actor", [None, Actor.person("caretaker", "viewer")])
def test_a_write_without_an_actor_or_by_a_viewer_is_forbidden_and_stores_nothing(actor):
    for problem in (
        refused(DASHBOARD, writes.schedule_create, actor, schedule_fields(), manual=True),
        refused(DASHBOARD, writes.schedule_update, actor, 1, {"on_s": 60}),
        refused(DASHBOARD, writes.schedule_delete, actor, 1),
        refused(DASHBOARD, writes.line_update, actor, "L01", {"daily_cap_min": 30}),
        refused(DASHBOARD, writes.override, actor, "fans", minutes=5),
        refused(DASHBOARD, writes.clear_fault, actor),
    ):
        assert (problem.code, problem.status) == ("forbidden", 403)
    assert counts() == (0, 0, 0)


def test_identity_fields_in_a_body_are_dropped_unread():
    fields = {**schedule_fields(), "actor": "mallory", "source": "iris", "updated_by": "mallory"}
    made = call(DASHBOARD, writes.schedule_create, PERSON, fields, manual=True).value
    assert made.updated_by.model_dump() == {"source": "dashboard", "name": "jason"}
    assert activity_rows()[-1]["source"] == "dashboard"
    problem = refused(DASHBOARD, writes.schedule_create, PERSON, {**schedule_fields(), "typo": 1}, manual=True)
    assert (problem.code, problem.field) == ("invalid_request", "typo")
