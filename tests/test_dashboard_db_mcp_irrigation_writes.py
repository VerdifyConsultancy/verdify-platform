"""VD-61 (#1174) on a private PostgreSQL: the five irrigation write tools as the MCP login.

jvallery/agents#5994's campaign step runs each ``tests/test_dashboard_db_*.py`` file against a fresh loopback PostgreSQL
and hands it over in ``VERDIFY_ISOLATED_TEST_DSN``; without one this module skips. The database, its seed and its grants
are tests/test_dashboard_db_irrigation_writes.py's: M3's lines and three vanda-degraded schedules, ``irr_engine`` false,
``irrig_wall_duration_min`` 12. The tools run through mcp/server.py under the FastMCP stub, each on a connection as the
MCP login, with the caller set as ``call_tool`` sets it. VD-41's test proves the services; this one proves what the tools
add: the manual default, the pause that carries no flag, the engine answers passed through, and that a water write leaves
a planning trigger alone. For that the ``plan_tables`` fixture adds set_plan's tables with 259's grants (checked).

The ingestor's ``CommandStore`` runs from ``ingestor/tasks/irrigation.py``'s own source, as the bridge's database test
loads it. Its device call for ``clear_fault`` is VD-42's (#1157; tests/test_irrigation_sync.py proves the service call).
"""

from __future__ import annotations

import datetime as dt
import json
import re
import uuid

import irrigation_db_support as support
import pytest
from test_dashboard_db_irrigation_bridge import OnePool, load_command_store
from test_dashboard_db_irrigation_writes import (
    DASHBOARD,
    DSN,
    MCP,
    PERSON,
    activity_rows,
    call,
    connect,
    counts,
    enable_line,
    openapi_errors,
    run,
    schedule_fields,
    seeded,  # noqa: F401
    seeds,  # noqa: F401
    set_engine,
    sql,
)
from test_mcp_audience_auth import clean_auth_env, mcp_server, mcp_types  # noqa: F401

from verdify_schemas.irrigation import IrrFault, IrrState, format_irr_state
from verdify_services.irrigation import reads, writes

asyncpg = pytest.importorskip("asyncpg")

pytestmark = pytest.mark.skipif(DSN is None, reason="needs VERDIFY_ISOLATED_TEST_DSN (campaign private PostgreSQL)")

INGESTOR = "verdify_ingestor_runtime_login"
IRIS = ("iris", "iris")
STEWARD = ("agent:steward", "steward")
ADMIN = ("agent:admin", "admin")
NO_ENGINE = "arrives with the irrigation firmware"
PLANNER = "local"

# What set_plan writes beyond the writes test's subset, from db/schema.sql in dependency order.
PLAN_OBJECTS = (
    ("plan_delivery_log", "TABLE"),
    ("plan_delivery_log_id_seq", "SEQUENCE"),
    ("plan_delivery_log_id_seq", "SEQUENCE OWNED BY"),
    ("plan_delivery_log id", "DEFAULT"),
    ("plan_delivery_log plan_delivery_log_pkey", "CONSTRAINT"),
    ("plan_delivery_log plan_delivery_log_trigger_id_key", "CONSTRAINT"),
    ("planner_trigger_ledger", "TABLE"),
    ("planner_trigger_ledger_id_seq", "SEQUENCE"),
    ("planner_trigger_ledger_id_seq", "SEQUENCE OWNED BY"),
    ("planner_trigger_ledger id", "DEFAULT"),
    ("planner_trigger_ledger planner_trigger_ledger_pkey", "CONSTRAINT"),
    ("setpoint_plan setpoint_plan_pkey", "CONSTRAINT"),
    ("plan_journal_one_effective_per_greenhouse", "INDEX"),
    ("normalize_param_name(text)", "FUNCTION"),
    ("normalize_plan_param()", "FUNCTION"),
    ("normalize_plan_delivery_terminal_state()", "FUNCTION"),
    ("normalize_planner_trigger_terminal_state()", "FUNCTION"),
    ("plan_delivery_log trg_normalize_plan_delivery_terminal_state", "TRIGGER"),
    ("planner_trigger_ledger trg_normalize_planner_trigger_terminal_state", "TRIGGER"),
    ("setpoint_plan trg_normalize_plan_param", "TRIGGER"),
)
# set_plan's context read plans fn_setpoint_at over the newest climate row. climate stays empty here, so it never runs,
# and db/schema.sql's body would pull in the whole band function chain.
FN_SETPOINT_AT = """
CREATE FUNCTION public.fn_setpoint_at(p_param text, p_ts timestamptz) RETURNS double precision
    LANGUAGE sql STABLE AS $$ SELECT NULL::double precision $$;
GRANT EXECUTE ON FUNCTION public.fn_setpoint_at(text, timestamptz) TO verdify_mcp_runtime;
"""
PLAN_RELATIONS = ("plan_delivery_log", "plan_journal", "planner_trigger_ledger", "setpoint_plan")
SUNRISE_HYPOTHESIS = json.dumps(
    {
        "conditions": {
            "outdoor_temp_peak_f": 92.0,
            "outdoor_rh_min_pct": 25.0,
            "solar_peak_w_m2": 900,
            "cloud_cover_avg_pct": 30,
        },
        "rationale": [
            {
                "parameter": "mister_engage_kpa",
                "new_value": 1.6,
                "forecast_anchor": "RH under 25% at noon",
                "expected_effect": "VPD stays in band",
            }
        ],
    }
)


@pytest.fixture()
def tools(mcp_server, mcp_types, monkeypatch):  # noqa: F811
    """The MCP tools, each on its own connection as the MCP login."""

    async def db():
        return await connect(MCP)

    monkeypatch.setattr(mcp_server, "_db", db)
    return mcp_server


def as_caller(server, who: tuple[str, str], coroutine):
    """Run a tool as this caller, as ``call_tool`` sets it from the caller's token."""
    token = server._REQUEST_ACTOR.set(server.McpActor(source=who[0], name=who[1]))
    try:
        return run(coroutine)
    finally:
        server._REQUEST_ACTOR.reset(token)


def answer(result) -> dict:
    assert result.isError is False, result.content[0].text
    return json.loads(result.content[0].text)


def refusal(result) -> dict:
    assert result.isError is True, result.content[0].text
    return json.loads(result.content[0].text)


def slot_row(slot_id: int) -> dict:
    [row] = sql("SELECT * FROM public.irrigation_slot WHERE slot_id = $1", slot_id)
    return dict(row)


def line_row(line_id: str) -> dict:
    [row] = sql("SELECT * FROM public.irrigation_line WHERE line_id = $1", line_id)
    return dict(row)


def person_holds_schedule(**fields) -> int:
    return call(DASHBOARD, writes.schedule_create, PERSON, schedule_fields(**fields), manual=True).value.slot_id


def person_holds_line(line_id: str) -> None:
    call(DASHBOARD, writes.line_update, PERSON, line_id, {"manual": True}, manual=True)


def controller_reports() -> None:
    """The architecture §7 test holds, so a line reads its irr_state: a fresh heartbeat and a fresh climate row."""
    sql(
        "INSERT INTO public.system_state (ts, entity, value, greenhouse_id) "
        "VALUES (now(), 'ingestor_heartbeat', 'ok', 'vallery')"
    )
    sql("INSERT INTO public.climate (ts, greenhouse_id) VALUES (now(), 'vallery')")


def hours_from_now(hours: float) -> dt.datetime:
    return dt.datetime.now(dt.UTC) + dt.timedelta(hours=hours)


def lines_of(body: dict) -> dict[str, dict]:
    return {line["line_id"]: line for line in body["lines"]}


# ── Schedules: the manual rule ──────────────────────────────────────────────


def test_an_agents_create_stores_false_unless_it_passes_true(tools):
    start = tools.ScheduleStart(kind="clock", clock="07:30")
    plain = answer(
        as_caller(tools, STEWARD, tools.irrigation_schedule_set(line_id="L01", days=["mon"], start=start, on_s=600))
    )
    assert plain["manual"] is False and plain["manual_before"] is None and slot_row(plain["slot_id"])["manual"] is False
    assert (plain["sync"], plain["updated_by"]) == ("requested", {"source": "agent:steward", "name": "steward"})

    held = answer(
        as_caller(
            tools,
            STEWARD,
            tools.irrigation_schedule_set(
                line_id="L01", days=["tue"], start=tools.ScheduleStart(kind="sunrise"), on_s=600, manual=True
            ),
        )
    )
    assert held["manual"] is True and slot_row(held["slot_id"])["manual"] is True
    assert held["start"] == {"kind": "sunrise", "offset_min": 0}


def test_an_agents_edit_of_a_manual_slot_returns_manual_before_and_activity_shows_it_cleared(tools):
    slot_id = person_holds_schedule(label="Wall MWF")
    body = answer(
        as_caller(tools, STEWARD, tools.irrigation_schedule_set(slot_id=slot_id, on_s=900, reason="forecast 92 F"))
    )
    # Omitted fields keep their value; manual, which the edit omits, is stored false.
    assert (body["on_s"], body["label"], body["days"]) == (900, "Wall MWF", ["mon", "wed"])
    assert (body["manual"], body["manual_before"]) == (False, True)
    stored = slot_row(slot_id)
    assert (stored["on_s"], stored["manual"], stored["source"], stored["updated_by"]) == (
        900,
        False,
        "agent:steward",
        "steward",
    )
    entry = activity_rows()[-1]
    assert (entry["source"], entry["action"], entry["reason"]) == (
        "agent:steward",
        "irrigation.schedule.update",
        "forecast 92 F",
    )
    # dashboard/app/lib/activity.ts reads this pair as "manual cleared".
    assert entry["detail"]["manual"] == {"before": True, "after": False}


def test_an_agents_edit_that_passes_true_keeps_the_flag(tools):
    slot_id = person_holds_schedule()
    body = answer(as_caller(tools, STEWARD, tools.irrigation_schedule_set(slot_id=slot_id, on_s=900, manual=True)))
    assert (body["manual"], body["manual_before"]) == (True, True)
    assert activity_rows()[-1]["detail"]["manual"] == {"before": True, "after": True}


def test_iris_is_refused_on_a_manual_schedule_a_delete_included_and_it_changes_nothing(tools):
    slot_id = person_holds_schedule()
    before = counts(), slot_row(slot_id)
    for write in (
        tools.irrigation_schedule_set(slot_id=slot_id, on_s=900),
        tools.irrigation_schedule_set(slot_id=slot_id, delete=True),
    ):
        problem = refusal(as_caller(tools, IRIS, write))
        assert (problem["code"], problem["status"], problem["retryable"], problem["field"]) == (
            "manual",
            422,
            False,
            "slot_id",
        )
    assert (counts(), slot_row(slot_id)) == before


def test_iris_writes_an_unheld_schedule_as_iris_with_its_trigger_only_in_the_activity_detail(tools):
    made = answer(
        as_caller(
            tools,
            IRIS,
            tools.irrigation_schedule_set(
                line_id="L01",
                days=["mon", "wed", "fri"],
                start=tools.ScheduleStart(kind="sunrise", offset_min=60),
                on_s=720,
                reason="wall MWF",
                trigger_id="trigger-1",
                planner_instance=PLANNER,
            ),
        )
    )
    assert made["updated_by"] == {"source": "iris", "name": "iris"}
    entry = activity_rows()[-1]
    assert (entry["source"], entry["actor"], entry["target"]) == ("iris", "iris", str(made["slot_id"]))
    assert (entry["detail"]["trigger_id"], entry["detail"]["planner_instance"]) == ("trigger-1", PLANNER)
    edited = answer(as_caller(tools, IRIS, tools.irrigation_schedule_set(slot_id=made["slot_id"], on_s=900)))
    assert (edited["on_s"], edited["manual_before"]) == (900, False)


def test_an_agents_delete_answers_what_rest_cannot_and_keeps_the_fields_in_activity(tools):
    slot_id = person_holds_schedule(label="Wall MWF")
    body = answer(as_caller(tools, STEWARD, tools.irrigation_schedule_set(slot_id=slot_id, delete=True)))
    assert body == {"slot_id": slot_id, "deleted": True, "manual_before": True}
    assert not sql("SELECT slot_id FROM public.irrigation_slot WHERE slot_id = $1", slot_id)
    entry = activity_rows()[-1]
    assert (entry["action"], entry["target"]) == ("irrigation.schedule.delete", str(slot_id))
    assert entry["detail"]["before"]["label"] == "Wall MWF"


@pytest.mark.parametrize(
    ("build", "code", "field"),
    [
        (lambda t: t.irrigation_schedule_set(slot_id=7, delete=True, on_s=600), "invalid_request", "on_s"),
        (lambda t: t.irrigation_schedule_set(delete=True), "invalid_request", "slot_id"),
        (lambda t: t.irrigation_schedule_set(line_id="L01"), "invalid_request", "days"),
        (
            # A clock start with no clock is refused, never read as midnight.
            lambda t: t.irrigation_schedule_set(
                line_id="L01", days=["mon"], start=t.ScheduleStart(kind="clock"), on_s=600
            ),
            "invalid_request",
            "start",
        ),
        (
            lambda t: t.irrigation_schedule_set(
                line_id="L01", days=["mon"], start=t.ScheduleStart(kind="sunrise", clock="07:30"), on_s=600
            ),
            "invalid_request",
            "start",
        ),
        (lambda t: t.irrigation_schedule_set(slot_id=65000, on_s=600), "unknown_target", "slot_id"),
        (lambda t: t.irrigation_schedule_set(slot_id=65000, delete=True), "unknown_target", "slot_id"),
        (
            lambda t: t.irrigation_schedule_set(
                line_id="L04", days=["mon"], start=t.ScheduleStart(kind="sunrise"), on_s=300, count=2, gap_s=60
            ),
            "out_of_range",
            "on_s",
        ),
    ],
)
def test_a_schedule_write_outside_the_rules_is_refused_and_stores_nothing(tools, build, code, field):
    problem = refusal(as_caller(tools, STEWARD, build(tools)))
    assert (problem["code"], problem["field"]) == (code, field)
    assert counts() == (0, 0, 0)


def test_too_close_and_the_bounds_it_gives_reach_the_agent(tools):
    start = tools.ScheduleStart(kind="clock", clock="07:30")
    near = tools.ScheduleStart(kind="clock", clock="07:45")
    answer(as_caller(tools, STEWARD, tools.irrigation_schedule_set(line_id="L01", days=["mon"], start=start, on_s=600)))
    problem = refusal(
        as_caller(tools, STEWARD, tools.irrigation_schedule_set(line_id="L01", days=["mon"], start=near, on_s=600))
    )
    assert (problem["code"], problem["retryable"]) == ("too_close", False)
    problem = refusal(
        as_caller(
            tools,
            STEWARD,
            tools.irrigation_schedule_set(
                line_id="L04", days=["tue"], start=near, on_s=300, count=2, gap_s=60, reason="too long a pulse"
            ),
        )
    )
    assert (problem["code"], problem["field"], problem["min"], problem["max"]) == ("out_of_range", "on_s", 10, 90)


# ── Lines: the manual rule ──────────────────────────────────────────────────


def test_an_agents_definition_edit_that_omits_manual_stores_false(tools):
    person_holds_line("L01")
    body = answer(as_caller(tools, STEWARD, tools.irrigation_line_set("L01", daily_cap_min=45)))
    line = lines_of(body)["L01"]
    assert (line["daily_cap_min"], line["manual"], line["manual_before"]) == (45, False, True)
    assert line_row("L01")["manual"] is False
    assert activity_rows()[-1]["detail"]["manual"] == {"before": True, "after": False}


def test_an_agents_definition_edit_stores_the_flag_it_passes_and_a_flag_alone_sets_it(tools):
    body = answer(as_caller(tools, STEWARD, tools.irrigation_line_set("L01", daily_cap_min=45, manual=True)))
    assert (lines_of(body)["L01"]["manual"], lines_of(body)["L01"]["manual_before"]) == (True, False)
    body = answer(as_caller(tools, STEWARD, tools.irrigation_line_set("L01", manual=False)))
    assert (lines_of(body)["L01"]["manual"], lines_of(body)["L01"]["manual_before"]) == (False, True)
    assert line_row("L01")["daily_cap_min"] == 45


@pytest.mark.parametrize("line_id", ["L01", "all"])
def test_an_agents_pause_and_resume_leave_manual_true_and_activity_records_no_change(tools, line_id):
    person_holds_line("L01")
    until = hours_from_now(2)
    body = answer(as_caller(tools, STEWARD, tools.irrigation_line_set(line_id, paused_until=until, reason="rain")))
    line = lines_of(body)["L01"]
    assert (line["manual"], line["manual_before"]) == (True, True)
    assert line["paused_by"]["name"] == "steward"
    assert dt.datetime.fromisoformat(line["paused_until"]) == until
    stored = line_row("L01")
    assert stored["manual"] is True and stored["paused_until"] == until
    entry = activity_rows()[-1]
    assert (entry["action"], entry["target"]) == ("irrigation.line.update", line_id)
    assert entry["detail"]["manual"] == {"before": True, "after": True}

    # A time at or before now resumes, as REST's null or now does.
    resumed = answer(as_caller(tools, STEWARD, tools.irrigation_line_set(line_id, paused_until=hours_from_now(-1))))
    assert lines_of(resumed)["L01"]["paused_until"] is None
    stored = line_row("L01")
    assert stored["manual"] is True and stored["paused_until"] is None
    assert activity_rows()[-1]["detail"]["manual"] == {"before": True, "after": True}


def test_all_leaves_every_lines_flag_as_it_was_and_pauses_each(tools):
    person_holds_line("L01")
    until = hours_from_now(2)
    body = answer(as_caller(tools, STEWARD, tools.irrigation_line_set("all", paused_until=until)))
    assert len(body["lines"]) == 11
    rows = sql("SELECT line_id, manual, paused_until FROM public.irrigation_line ORDER BY line_id")
    assert [row["line_id"] for row in rows if row["manual"]] == ["L01"]
    assert {row["paused_until"] for row in rows} == {until}


@pytest.mark.parametrize("manual", [True, False])
def test_a_pause_on_one_line_never_changes_the_flag_even_with_manual_beside_it(tools, manual):
    person_holds_line("L01")
    entries = len(activity_rows())
    body = answer(
        as_caller(tools, STEWARD, tools.irrigation_line_set("L01", paused_until=hours_from_now(2), manual=manual))
    )
    assert lines_of(body)["L01"]["manual"] is True and line_row("L01")["manual"] is True
    assert len(activity_rows()) == entries + 1
    assert activity_rows()[-1]["detail"]["manual"] == {"before": True, "after": True}


def test_iris_is_refused_on_a_manual_line_a_pause_included_and_on_all_while_any_is_manual(tools):
    person_holds_line("L09")
    before, stored = counts(), line_row("L09")
    until = hours_from_now(2)
    for write, detail in (
        (tools.irrigation_line_set("L09", daily_cap_min=30), "West mister"),
        (tools.irrigation_line_set("L09", paused_until=until), "West mister"),
        (tools.irrigation_line_set("all", paused_until=until), "West mister"),
    ):
        problem = refusal(as_caller(tools, IRIS, write))
        assert (problem["code"], problem["status"], problem["retryable"]) == ("manual", 422, False)
        assert detail in problem["detail"]  # the line's name, never its relay code (#1341)
    assert (counts(), line_row("L09")) == (before, stored)
    assert not [row for row in sql("SELECT paused_until FROM public.irrigation_line") if row["paused_until"]]


def test_iris_changes_an_unheld_line_and_a_pause_on_all_once_none_is_held(tools):
    until = hours_from_now(2)
    body = answer(
        as_caller(
            tools,
            IRIS,
            tools.irrigation_line_set(
                "all", paused_until=until, reason="rain", trigger_id="trigger-1", planner_instance=PLANNER
            ),
        )
    )
    assert lines_of(body)["L04"]["paused_by"]["name"] == "iris"
    entry = activity_rows()[-1]
    assert (entry["source"], entry["detail"]["trigger_id"], entry["detail"]["planner_instance"]) == (
        "iris",
        "trigger-1",
        PLANNER,
    )
    body = answer(as_caller(tools, IRIS, tools.irrigation_line_set("L01", daily_cap_min=45)))
    assert lines_of(body)["L01"]["daily_cap_min"] == 45


@pytest.mark.parametrize(
    ("build", "field"),
    [
        (lambda t, until: t.irrigation_line_set("all", paused_until=until, manual=False), "manual"),
        (lambda t, until: t.irrigation_line_set("all", paused_until=until, manual=True), "manual"),
        (lambda t, until: t.irrigation_line_set("all", paused_until=until, enabled=False), "enabled"),
        (lambda t, until: t.irrigation_line_set("all"), "paused_until"),
        (lambda t, until: t.irrigation_line_set("L01", vpd_member=True), "vpd_member"),
        (lambda t, until: t.irrigation_line_set("L08", schedule_member=False), "schedule_member"),
    ],
)
def test_all_with_any_other_field_and_a_mister_flag_elsewhere_are_invalid_request(tools, build, field):
    problem = refusal(as_caller(tools, STEWARD, build(tools, hours_from_now(2))))
    assert (problem["code"], problem["status"], problem["field"]) == ("invalid_request", 400, field)
    assert counts() == (0, 0, 0)


def test_a_cap_outside_its_bounds_is_out_of_range_with_them(tools):
    # The seed's L04 keeps 60 of its 120 minutes for VPD, so a cap of 30 leaves the reserve above it.
    problem = refusal(as_caller(tools, STEWARD, tools.irrigation_line_set("L04", daily_cap_min=30)))
    assert problem["code"] == "out_of_range" and problem["retryable"] is False
    assert line_row("L04")["daily_cap_min"] == 120


# ── The check ───────────────────────────────────────────────────────────────


def test_enabling_before_the_check_is_check_required_and_after_the_dashboard_records_it_the_same_call_succeeds(tools):
    enable = lambda: tools.irrigation_line_set("L06", enabled=True)  # noqa: E731
    problem = refusal(as_caller(tools, STEWARD, enable()))
    assert (problem["code"], problem["status"], problem["retryable"], problem["field"]) == (
        "check_required",
        422,
        False,
        "enabled",
    )
    assert line_row("L06")["enabled"] is False and counts() == (0, 0, 0)

    call(DASHBOARD, writes.line_update, PERSON, "L06", {"checked": True, "check_note": "tank and injector"})
    body = answer(as_caller(tools, STEWARD, enable()))
    assert lines_of(body)["L06"]["enabled"] is True
    stored = line_row("L06")
    assert (stored["enabled"], stored["manual"], stored["checked_by"]) == (True, False, "jason")


def test_first_turning_schedule_on_needs_the_check_too(tools):
    enable_line("L07")
    on = lambda: tools.irrigation_line_set("L07", schedule_member=True)  # noqa: E731
    problem = refusal(as_caller(tools, IRIS, on()))
    assert (problem["code"], problem["field"]) == ("check_required", "schedule_member")
    call(DASHBOARD, writes.line_update, PERSON, "L07", {"checked": True})
    assert lines_of(answer(as_caller(tools, IRIS, on())))["L07"]["schedule_member"] is True


# ── The engine flag ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("flag", ["false", None])
def test_before_the_engine_run_on_l04_and_clear_fault_are_unavailable_and_store_nothing_while_stop_is_accepted(
    tools, flag
):
    if flag is None:
        sql("DELETE FROM public.system_state WHERE entity = 'irr_engine'")
    for write in (
        tools.irrigation_run("L04", on_s=30, count=4, gap_s=60),
        tools.irrigation_clear_fault(),
        tools.irrigation_clear_fault("L07"),
    ):
        problem = refusal(as_caller(tools, STEWARD, write))
        assert (problem["code"], problem["status"], problem["retryable"]) == ("unavailable", 503, False)
        assert NO_ENGINE in problem["detail"]
    assert counts() == (0, 0, 0)

    stop = answer(as_caller(tools, STEWARD, tools.irrigation_stop("L04")))
    assert (stop["kind"], stop["line_id"], stop["status"], stop["args"]) == ("stop", "L04", "pending", {"bridge": True})
    assert counts() == (0, 1, 1)


def test_before_the_engine_l01_runs_its_configured_minutes_with_none_sent(tools):
    body = answer(as_caller(tools, STEWARD, tools.irrigation_run("L01", trigger_id="t-1", planner_instance=PLANNER)))
    assert (body["kind"], body["line_id"], body["status"]) == ("run", "L01", "pending")
    assert body["args"] == {"minutes": 12, "bridge": True}
    assert (activity_rows()[-1]["detail"]["trigger_id"], activity_rows()[-1]["detail"]["minutes"]) == ("t-1", 12)


def test_before_the_engine_another_length_on_l01_is_out_of_range_at_that_length(tools):
    problem = refusal(as_caller(tools, STEWARD, tools.irrigation_run("L01", minutes=5)))
    assert (problem["code"], problem["min"], problem["max"], problem["current"]) == ("out_of_range", 12, 12, 12)
    assert counts() == (0, 0, 0)


def test_before_the_engine_schedule_and_line_writes_are_stored_for_the_first_push(tools):
    body = answer(
        as_caller(
            tools,
            STEWARD,
            tools.irrigation_schedule_set(
                line_id="L01", days=["mon"], start=tools.ScheduleStart(kind="clock", clock="07:30"), on_s=600
            ),
        )
    )
    assert body["sync"] == "requested" and slot_row(body["slot_id"])["on_s"] == 600
    body = answer(as_caller(tools, STEWARD, tools.irrigation_line_set("L01", paused_until=hours_from_now(2))))
    assert lines_of(body)["L01"]["sync"] == "requested"


def test_with_the_engine_each_line_kind_runs_its_own_fields(tools):
    set_engine()
    enable_line("L02", checked=True)
    enable_line("L04", checked=True, schedule_member=True)
    enable_line("L08", checked=True)
    for line_id, fields in (
        ("L01", {"minutes": 5}),
        ("L02", {"minutes": 5, "prewet_s": 120, "flush_s": 240}),
        ("L04", {"on_s": 30, "count": 4, "gap_s": 60}),
        ("L08", {"on_s": 30, "count": 4, "gap_s": 60, "prewet_s": 120, "flush_s": 240}),
    ):
        body = answer(as_caller(tools, STEWARD, tools.irrigation_run(line_id, **fields)))
        assert (body["kind"], body["line_id"], body["status"], body["args"]) == ("run", line_id, "pending", fields)


@pytest.mark.parametrize(
    ("line_id", "fields", "code", "field"),
    [
        ("L08", {"minutes": 5}, "invalid_request", "on_s"),
        (
            "L08",
            {"on_s": 30, "count": 4, "gap_s": 60, "prewet_s": 60, "flush_s": 60, "minutes": 5},
            "invalid_request",
            "minutes",
        ),
        ("L01", {}, "invalid_request", "minutes"),
        ("L03", {"minutes": 5}, "unknown_target", "line_id"),
        # #1343 follow-up: fog runs once only on a build whose irr_caps says so; none is published here.
        ("L11", {"minutes": 5}, "unavailable", "line_id"),
    ],
)
def test_with_the_engine_the_wrong_fields_for_a_line_are_refused(tools, line_id, fields, code, field):
    set_engine()
    enable_line("L08", checked=True)
    problem = refusal(as_caller(tools, STEWARD, tools.irrigation_run(line_id, **fields)))
    assert (problem["code"], problem["field"]) == (code, field)
    assert counts() == (0, 0, 0)


def test_with_the_engine_a_run_a_person_holds_manual_is_not_refused(tools):
    set_engine()
    person_holds_line("L01")
    assert answer(as_caller(tools, IRIS, tools.irrigation_run("L01", minutes=5)))["status"] == "pending"
    assert line_row("L01")["manual"] is True


# ── Stop and clear-fault ────────────────────────────────────────────────────


@pytest.mark.parametrize("engine", [False, True])
@pytest.mark.parametrize("who", [IRIS, STEWARD, ADMIN])
def test_stop_is_accepted_from_every_agent_with_the_engine_on_and_off(tools, who, engine):
    if engine:
        set_engine()
    sql("UPDATE public.irrigation_line SET manual = true")  # stop never checks the flag
    for line_id, target in ((None, "all"), ("L04", "L04")):
        body = answer(as_caller(tools, who, tools.irrigation_stop(line_id, reason="hose")))
        assert (body["kind"], body["line_id"], body["status"]) == ("stop", line_id, "pending")
        assert body["args"] == ({} if engine else {"bridge": True})
        assert body["actor"] == {"source": who[0], "name": who[1]}
        entry = activity_rows()[-1]
        assert (entry["source"], entry["action"], entry["target"]) == (who[0], "irrigation.stop", target)


def test_a_latched_mister_clears_through_the_tool_the_ingestors_store_and_the_next_state(tools):
    until = int(hours_from_now(1).timestamp())
    controller_reports()
    set_engine(format_irr_state(IrrState(faults=(IrrFault(line_id="L07", fault="mister_fault", until_epoch=until),))))

    async def fault_of(line_id):
        conn = await connect(MCP)
        try:
            [line] = await reads.lines(conn, None, [line_id])
            return line.fault
        finally:
            await conn.close()

    assert run(fault_of("L07")) == "mister_fault"
    body = answer(as_caller(tools, IRIS, tools.irrigation_clear_fault("L07", reason="nozzles checked")))
    assert (body["kind"], body["line_id"], body["status"], body["args"]) == ("clear_fault", "L07", "pending", {})

    # The ingestor's own store, as its login, finds the row and marks it sent before the device call.
    task = load_command_store()

    async def ingestor_sends():
        conn = await connect(INGESTOR)
        try:
            store = task["CommandStore"](OnePool(conn))
            [pending] = await store.pending("clear_fault")
            assert (str(pending.command_id), pending.line_id) == (body["command_id"], "L07")
            assert await store.mark_run_sent(pending, dt.datetime.now(dt.UTC)) is True
        finally:
            await conn.close()

    run(ingestor_sends())
    [row] = sql("SELECT status, sent_at FROM public.irrigation_command WHERE kind = 'clear_fault'")
    assert row["status"] == "sent" and row["sent_at"] is not None

    # The controller publishes irr_state on change: the latch is gone from the next one.
    set_engine(format_irr_state(IrrState()))
    assert run(fault_of("L07")) is None


def test_clear_fault_with_no_line_is_one_pending_command_for_every_mister(tools):
    set_engine()
    body = answer(as_caller(tools, STEWARD, tools.irrigation_clear_fault()))
    assert (body["kind"], body["line_id"], body["status"]) == ("clear_fault", None, "pending")
    entry = activity_rows()[-1]
    assert (entry["action"], entry["target"]) == ("irrigation.clear_fault", "all")


# ── The answers ─────────────────────────────────────────────────────────────


def test_the_answers_are_the_rest_json(tools):
    made = answer(
        as_caller(
            tools,
            STEWARD,
            tools.irrigation_schedule_set(
                line_id="L01", days=["mon"], start=tools.ScheduleStart(kind="sunrise", offset_min=60), on_s=600
            ),
        )
    )
    assert openapi_errors("Schedule", made) == []
    edited = answer(as_caller(tools, STEWARD, tools.irrigation_schedule_set(slot_id=made["slot_id"], on_s=720)))
    assert openapi_errors("Schedule", edited) == []
    lines = answer(as_caller(tools, STEWARD, tools.irrigation_line_set("L01", daily_cap_min=45)))
    assert set(lines) == {"lines"} and openapi_errors("IrrigationLine", lines["lines"][0]) == []
    assert openapi_errors("Command", answer(as_caller(tools, STEWARD, tools.irrigation_run("L01")))) == []
    assert openapi_errors("Command", answer(as_caller(tools, STEWARD, tools.irrigation_stop()))) == []


# ── request_id ──────────────────────────────────────────────────────────────


# Each builds one write from its request_id and a number; another number is another payload.
REPLAYS = {
    "create": lambda t, key, n: t.irrigation_schedule_set(
        line_id="L01",
        days=["mon"],
        start=t.ScheduleStart(kind="clock", clock="07:30"),
        on_s=600 + n,
        request_id=key,
    ),
    "edit": lambda t, key, n: t.irrigation_schedule_set(slot_id=1, on_s=45 + n, request_id=key),
    "line": lambda t, key, n: t.irrigation_line_set("L01", daily_cap_min=45 + n, request_id=key),
    "pause": lambda t, key, n: t.irrigation_line_set(
        "all", paused_until=dt.datetime(2099, 1, 1 + n, tzinfo=dt.UTC), request_id=key
    ),
    "run": lambda t, key, n: t.irrigation_run("L01", minutes=5 + n, request_id=key),
    "stop": lambda t, key, n: t.irrigation_stop(("L01", "L04")[n], request_id=key),
    "clear": lambda t, key, n: t.irrigation_clear_fault(("L04", "L07")[n], request_id=key),
}


@pytest.mark.parametrize("write", list(REPLAYS))
def test_the_same_request_id_replays_with_replayed_true_and_another_payload_is_refused(tools, write):
    set_engine()
    build, key = REPLAYS[write], str(uuid.uuid4())
    first = answer(as_caller(tools, STEWARD, build(tools, key, 0)))
    assert "replayed" not in first
    stored = counts()
    again = answer(as_caller(tools, STEWARD, build(tools, key, 0)))
    assert again.pop("replayed") is True
    assert counts() == stored
    if write in ("create", "edit", "run", "stop", "clear"):
        assert again == first
    else:
        assert [(line["line_id"], line["manual"], line["paused_until"]) for line in again["lines"]] == [
            (line["line_id"], line["manual"], line["paused_until"]) for line in first["lines"]
        ]

    problem = refusal(as_caller(tools, STEWARD, build(tools, key, 1)))
    assert (problem["code"], problem["status"], problem["retryable"], problem["field"]) == (
        "request_id_reused",
        422,
        False,
        "request_id",
    )
    assert counts() == stored


def test_a_delete_replays_after_the_schedule_is_gone(tools):
    made = person_holds_schedule()
    key = str(uuid.uuid4())
    delete = lambda reason=None: tools.irrigation_schedule_set(  # noqa: E731
        slot_id=made, delete=True, reason=reason, request_id=key
    )
    deleted = answer(as_caller(tools, STEWARD, delete()))
    assert deleted == {"slot_id": made, "deleted": True, "manual_before": True}
    assert answer(as_caller(tools, STEWARD, delete())) == {**deleted, "replayed": True}
    assert refusal(as_caller(tools, STEWARD, delete("another reason")))["code"] == "request_id_reused"


def test_a_request_id_that_is_not_a_uuid_is_invalid_request(tools):
    problem = refusal(as_caller(tools, STEWARD, tools.irrigation_stop(request_id="not-a-uuid")))
    assert (problem["code"], problem["field"]) == ("invalid_request", "request_id")
    assert counts() == (0, 0, 0)


# ── A water write is not a plan ─────────────────────────────────────────────


@pytest.fixture()
def plan_tables(seeds, monkeypatch):  # noqa: F811
    """set_plan's tables on top of the writes test's database; the MCP login holds what 259 gives it."""
    boundary = support.MCP_BOUNDARY_MIGRATION.read_text()
    reads_block = re.search(r"GRANT SELECT ON\s+(public\.alert_log.*?)\s+TO verdify_mcp_runtime;", boundary, re.S)
    writes_block = re.search(
        r"GRANT INSERT, UPDATE ON\s+(public\.alert_log.*?)\s+TO verdify_mcp_runtime;", boundary, re.S
    )
    for block in (reads_block, writes_block):
        assert block, "259's grants moved"
        assert {f"public.{name}" for name in PLAN_RELATIONS} <= {name.strip() for name in block[1].split(",")}

    async def install():
        conn = await connect()
        try:
            if await conn.fetchval("SELECT to_regclass('public.plan_delivery_log') IS NULL"):
                sections = support.schema_sections()
                await conn.execute("SET ROLE verdify")
                try:
                    await conn.execute("\n".join(sections[(name, kind, "public")] for name, kind in PLAN_OBJECTS))
                    await conn.execute(FN_SETPOINT_AT)
                finally:
                    await conn.execute("RESET ROLE")
                relations = ", ".join(f"public.{name}" for name in PLAN_RELATIONS)
                await conn.execute(f"GRANT SELECT, INSERT, UPDATE ON {relations} TO verdify_mcp_runtime")
            await conn.execute(f"TRUNCATE {', '.join(f'public.{name}' for name in PLAN_RELATIONS)} RESTART IDENTITY")
        finally:
            await conn.close()

    run(install())
    for name in (
        "VERDIFY_POLICY_VECTOR_MODE",
        "VERDIFY_COMPONENT_EXPERIMENT_ENABLED",
        "VERDIFY_ACTIVE_EXPERIMENT_ID",
        "VERDIFY_LEGACY_DIRECT_POLICY_WRITES_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)


def pending_sunrise() -> str:
    """A delivered SUNRISE trigger: a pending plan_delivery_log row and its delivered ledger row."""
    trigger_id = str(uuid.uuid4())
    [delivery] = sql(
        "INSERT INTO public.plan_delivery_log (event_type, event_label, status, instance, trigger_id) "
        "VALUES ('SUNRISE', 'fixture', 'pending', $1, $2::uuid) RETURNING id",
        PLANNER,
        trigger_id,
    )
    sql(
        "INSERT INTO public.planner_trigger_ledger (event_type, instance, expected_at, due_at, delivered_at, status, "
        "expected_action, plan_delivery_log_id, trigger_id) "
        "VALUES ('SUNRISE', $1, now(), now() + interval '1 hour', now(), 'delivered', 'set_plan', $2, $3::uuid) "
        "RETURNING id",
        PLANNER,
        delivery["id"],
        trigger_id,
    )
    return trigger_id


def trigger_state() -> tuple[list[dict], list[dict]]:
    columns = "status, terminal_action, resulting_plan_id"
    delivery = sql(f"SELECT {columns} FROM public.plan_delivery_log")
    ledger = sql(f"SELECT {columns} FROM public.planner_trigger_ledger")
    return [dict(row) for row in delivery], [dict(row) for row in ledger]


def transitions(server) -> str:
    intent = server.ClimateIntent().model_dump(mode="json")
    start = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)
    return json.dumps(
        [
            {"ts": (start + dt.timedelta(hours=6 * index)).isoformat(), "climate_intent": intent, "reason": "fixture"}
            for index in range(3)
        ]
    )


def test_a_sunrise_delivery_in_which_iris_only_changes_water_stays_pending_until_set_plan(tools, plan_tables):
    trigger_id = pending_sunrise()
    pending = trigger_state()
    assert pending == (
        [{"status": "pending", "terminal_action": None, "resulting_plan_id": None}],
        [{"status": "delivered", "terminal_action": None, "resulting_plan_id": None}],
    )
    audit = {"trigger_id": trigger_id, "planner_instance": PLANNER}

    made = answer(
        as_caller(
            tools,
            IRIS,
            tools.irrigation_schedule_set(
                line_id="L01",
                days=["mon"],
                start=tools.ScheduleStart(kind="sunrise", offset_min=60),
                on_s=900,
                reason="forecast 92 F",
                **audit,
            ),
        )
    )
    assert trigger_state() == pending
    answer(as_caller(tools, IRIS, tools.irrigation_schedule_set(slot_id=made["slot_id"], on_s=720, **audit)))
    answer(as_caller(tools, IRIS, tools.irrigation_line_set("L01", daily_cap_min=45, **audit)))
    answer(as_caller(tools, IRIS, tools.irrigation_run("L01", **audit)))
    answer(as_caller(tools, IRIS, tools.irrigation_stop("L01")))
    answer(as_caller(tools, IRIS, tools.irrigation_schedule_set(slot_id=made["slot_id"], delete=True, **audit)))
    assert trigger_state() == pending
    for entry in activity_rows():
        if entry["action"] != "irrigation.stop":
            assert (entry["detail"]["trigger_id"], entry["detail"]["planner_instance"]) == (trigger_id, PLANNER)

    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%d-%H%M")
    reply = json.loads(
        as_caller(
            tools,
            IRIS,
            tools.set_plan(
                plan_id=f"iris-{stamp}",
                hypothesis=SUNRISE_HYPOTHESIS,
                transitions=transitions(tools),
                trigger_id=trigger_id,
                planner_instance=PLANNER,
            ),
        )
    )
    assert reply.get("ok") is True, reply
    assert reply["delivery_status"] == "plan_written"
    delivery, ledger = trigger_state()
    assert [row["status"] for row in delivery] == ["plan_written"]
    assert [row["resulting_plan_id"] for row in delivery] == [reply["plan_id"]]
    assert [row["resulting_plan_id"] for row in ledger] == [reply["plan_id"]]


def test_a_water_write_does_not_look_its_trigger_up(tools, plan_tables):
    # No delivery has this id: the tool stores it in the activity detail and asks nothing of the trigger tables.
    answer(as_caller(tools, IRIS, tools.irrigation_run("L01", trigger_id="not-a-delivered-trigger")))
    assert activity_rows()[-1]["detail"]["trigger_id"] == "not-a-delivered-trigger"
    assert trigger_state() == ([], [])
