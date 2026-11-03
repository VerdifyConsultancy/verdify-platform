"""VD-61 (#1174): irrigation_schedule_set, irrigation_line_set, irrigation_run, irrigation_stop and
irrigation_clear_fault, against stubbed services.

mcp/server.py loads under the FastMCP stub of tests/test_mcp_audience_auth.py, and a stub stands in for each function of
verdify_services.irrigation.writes, so these tests see what a tool passes and what it answers: the argument mapping, the
manual rule, the actor, the schemas, the descriptions and the lists. tests/test_dashboard_db_mcp_irrigation_writes.py
runs the same tools on PostgreSQL as the MCP login, and tests/test_mcp_transport_runtime.py runs them through the real
MCP SDK. tests/test_mcp_irrigation_read_tools.py's self-description and instructions checks cover them too.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, get_args, get_origin

import pytest
import yaml
from pydantic import BaseModel
from test_mcp_audience_auth import AGENT_TOOLS, clean_auth_env, mcp_server, mcp_types  # noqa: F401
from test_mcp_irrigation_read_tools import field_info, plain_type

from verdify_schemas.irrigation import (
    BOUNDS_BY_KIND,
    LINE_BOUNDS,
    LINE_IDS,
    MISTER_MASK_BIT,
    RUN_BOUNDS_BY_KIND,
    WEEKDAYS,
)
from verdify_services import activity
from verdify_services.actor import Actor
from verdify_services.irrigation import writes
from verdify_services.problems import PROBLEM_CODES, ServiceError, problem

ROOT = Path(__file__).resolve().parents[1]
OPENAPI = yaml.safe_load((ROOT / "docs/design/dashboard/openapi/dashboard-api.v1.yaml").read_text())
SCHEMAS = OPENAPI["components"]["schemas"]
NO_PLAN = "Changes watering only; it does not count as your plan for this trigger."
PAUSE = datetime(2026, 11, 3, 18, tzinfo=UTC)
REQUEST_ID = "00000000-0000-4000-8000-000000000061"
AUDIT = {"reason": "forecast 92 F", "request_id": REQUEST_ID, "trigger_id": "t-1", "planner_instance": "local"}
ACTORS = {"iris": ("iris", "iris"), "steward": ("agent:steward", "steward"), "admin": ("agent:admin", "admin")}
SIGNATURES = {
    "irrigation_schedule_set": (
        "slot_id",
        "delete",
        "line_id",
        "days",
        "start",
        "on_s",
        "count",
        "gap_s",
        "prewet_s",
        "flush_s",
        "degraded_only",
        "enabled",
        "label",
        "manual",
        "reason",
        "request_id",
        "trigger_id",
        "planner_instance",
    ),
    "irrigation_line_set": (
        "line_id",
        "enabled",
        "vpd_member",
        "schedule_member",
        "daily_cap_min",
        "vpd_reserve_min",
        "paused_until",
        "manual",
        "reason",
        "request_id",
        "trigger_id",
        "planner_instance",
    ),
    "irrigation_run": (
        "line_id",
        "minutes",
        "prewet_s",
        "flush_s",
        "on_s",
        "count",
        "gap_s",
        "reason",
        "request_id",
        "trigger_id",
        "planner_instance",
    ),
    "irrigation_stop": ("line_id", "reason", "request_id"),
    "irrigation_clear_fault": ("line_id", "reason", "request_id"),
}
WRITE_TOOLS = tuple(SIGNATURES)
# Each tool's REST twins and the codes its description must name (the issue's list for each).
TWINS = {
    "irrigation_schedule_set": (
        "API-IRR-03 (POST /v1/irrigation/schedules)",
        "API-IRR-04 (PUT /v1/irrigation/schedules/{slot_id})",
        "API-IRR-05 (DELETE /v1/irrigation/schedules/{slot_id})",
    ),
    "irrigation_line_set": ("API-IRR-08 (PUT /v1/irrigation/lines/{line_id})",),
    "irrigation_run": ("API-IRR-06 (POST /v1/irrigation/lines/{line_id}/run)",),
    "irrigation_stop": ("API-IRR-07 (POST /v1/irrigation/stop)",),
    "irrigation_clear_fault": ("API-IRR-13 (POST /v1/irrigation/clear-fault)",),
}
CODES = {
    "irrigation_schedule_set": (
        "invalid_request",
        "unknown_target",
        "out_of_range",
        "too_close",
        "too_many_schedules",
        "manual",
        "request_id_reused",
    ),
    "irrigation_line_set": (
        "out_of_range",
        "check_required",
        "invalid_request",
        "unknown_target",
        "manual",
        "request_id_reused",
    ),
    "irrigation_run": (
        "line_busy",
        "manifold_busy",
        "cap_reached",
        "line_disabled",
        "line_paused",
        "line_faulted",
        "not_schedule_member",
        "out_of_range",
        "invalid_request",
        "unknown_target",
        "unavailable",
        "request_id_reused",
    ),
    "irrigation_stop": ("unknown_target", "invalid_request", "request_id_reused", "unavailable"),
    "irrigation_clear_fault": ("unknown_target", "unavailable", "request_id_reused"),
}
SERVICES = ("schedule_create", "schedule_update", "schedule_delete", "line_update", "run", "stop", "clear_fault")


class Stored(BaseModel):
    """What a stubbed service answers with; the adapter only serializes it."""

    stored: bool = True


@dataclass
class Call:
    service: str
    actor: Actor
    args: tuple
    kwargs: dict


class Conn:
    closed = False

    async def close(self):
        self.closed = True


def run(coroutine):
    return asyncio.run(coroutine)


def payload(result) -> dict:
    [content] = result.content
    assert content.type == "text"
    return json.loads(content.text)


@pytest.fixture()
def conn(mcp_server, mcp_types, monkeypatch):  # noqa: F811
    connection = Conn()

    async def db():
        return connection

    monkeypatch.setattr(mcp_server, "_db", db)
    return connection


@pytest.fixture()
def seen(conn, monkeypatch) -> list[Call]:
    """Every service stubbed: the calls it took, on the tool's own connection."""
    calls: list[Call] = []

    def stub(name):
        async def service(connection, actor, *args, **kwargs):
            assert connection is conn
            calls.append(Call(name, actor, args, kwargs))
            return activity.WriteResult(value=Stored(), replayed=False)

        return service

    for name in SERVICES:
        monkeypatch.setattr(writes, name, stub(name))
    return calls


@pytest.fixture(autouse=True)
def as_steward(mcp_server):  # noqa: F811
    token = mcp_server._REQUEST_ACTOR.set(mcp_server.McpActor(source="agent:steward", name="steward"))
    yield
    mcp_server._REQUEST_ACTOR.reset(token)


def bounds(tool, argument) -> tuple[int, int]:
    metadata = field_info(tool, argument).metadata
    limits = {name: getattr(item, name) for item in metadata for name in ("ge", "le") if hasattr(item, name)}
    return limits["ge"], limits["le"]


def literal_of(tool, argument) -> tuple[str, ...]:
    annotation = plain_type(inspect.signature(tool).parameters[argument].annotation)
    assert get_origin(annotation) is Literal, argument
    return get_args(annotation)


def widest(field: str) -> tuple[int, int]:
    kinds = BOUNDS_BY_KIND.values()
    return min(kind[field][0] for kind in kinds), max(kind[field][1] for kind in kinds)


# ── Audiences and lists ─────────────────────────────────────────────────────


@pytest.mark.parametrize("tool", WRITE_TOOLS)
def test_each_tool_is_for_iris_agents_and_admin_and_is_not_read_only(mcp_server, tool):  # noqa: F811
    assert mcp_server.TOOL_AUDIENCES[tool] == frozenset({"iris", "agent", "admin"})
    assert tool in mcp_server.HERMES_REQUIRED_TOOLS and tool in AGENT_TOOLS
    assert tool not in mcp_server._TOOL_READ_ONLY
    assert tool not in mcp_server.audience_allowlist("experiment")
    assert tool not in mcp_server.audience_allowlist("observer")
    assert tool in {registered.name for registered in mcp_server.mcp._registered_tools}
    assert inspect.signature(getattr(mcp_server, tool)).return_annotation is inspect.Signature.empty


def test_the_counts_the_prompt_and_the_docs_state_are_the_iris_list(mcp_server):  # noqa: F811
    iris = mcp_server.audience_allowlist("iris")
    assert f"Exactly the {len(iris)} tools of" in (ROOT / "mcp/server.py").read_text()
    assert f"exactly the {len(iris)} tools in" in (ROOT / "docs/iris-planner-contract.md").read_text()

    planner = (ROOT / "ingestor/iris_planner.py").read_text()
    stated = re.search(r"Hermes exposes (\d+) production tools:(.*?)NEVER run psql", planner, re.S)
    assert stated
    listed = set(re.findall(r"`([a-z_]+)`", stated[2]))
    assert len(listed) == int(stated[1]) and listed <= iris and set(WRITE_TOOLS) <= listed
    assert f"{stated[1]} production tools" in (ROOT / "scripts/planner-dry.py").read_text()
    assert f"your {stated[1]} MCP tools" in (ROOT / "docs/planner/greenhouse-playbook.md").read_text()


def test_the_prompt_says_what_iris_needs_for_water(mcp_server):  # noqa: F811
    planner = (ROOT / "ingestor/iris_planner.py").read_text()
    assert "your irrigation tools only read" not in planner
    assert "Still end every trigger with `set_plan` or `acknowledge_trigger`." in planner
    for word in ("`manual` means a person holds", "`check_required`", "`irrigation_stop` always works"):
        assert word in planner, word


def test_the_instructions_give_the_writes_the_manual_rule_and_the_check(mcp_server):  # noqa: F811
    text = " ".join(mcp_server.SERVER_INSTRUCTIONS.split())
    for tool in WRITE_TOOLS:
        assert tool in text, tool
    assert "Iris, named agents and admin share the writes" in text
    assert "MCP creates and definition edits store false unless you pass true" in text
    assert "pause, resume, run, stop and clear-fault never change it" in text
    assert "only Iris is refused with `manual` on a manual item" in text
    assert "`check_required`: only a person records a line's check, on the dashboard" in text
    assert NO_PLAN in text


# ── Signatures, enums and bounds ────────────────────────────────────────────


@pytest.mark.parametrize("tool", WRITE_TOOLS)
def test_the_signature_is_the_designs_and_no_argument_names_an_identity_or_a_check(mcp_server, tool):  # noqa: F811
    parameters = inspect.signature(getattr(mcp_server, tool)).parameters
    assert tuple(parameters) == SIGNATURES[tool]
    for name in parameters:
        assert name not in {"checked", "check_note", "actor", "source"} and not name.endswith("_by"), name
    optional = {name: item.default for name, item in parameters.items() if item.default is not inspect.Parameter.empty}
    assert not [name for name, default in optional.items() if default is not None and name not in ("delete", "manual")]


def test_the_defaults_that_carry_a_rule(mcp_server):  # noqa: F811
    schedule = inspect.signature(mcp_server.irrigation_schedule_set).parameters
    assert (schedule["delete"].default, schedule["manual"].default) == (False, False)
    line = inspect.signature(mcp_server.irrigation_line_set).parameters
    assert line["line_id"].default is inspect.Parameter.empty and line["manual"].default is None
    assert "manual" not in inspect.signature(mcp_server.irrigation_run).parameters
    assert "manual" not in inspect.signature(mcp_server.irrigation_stop).parameters
    assert "manual" not in inspect.signature(mcp_server.irrigation_clear_fault).parameters


def test_the_line_ids_are_enums_of_the_lines_each_tool_takes(mcp_server):  # noqa: F811
    assert literal_of(mcp_server.irrigation_run, "line_id") == LINE_IDS
    assert literal_of(mcp_server.irrigation_stop, "line_id") == LINE_IDS
    assert literal_of(mcp_server.irrigation_line_set, "line_id") == (*LINE_IDS, "all")
    assert literal_of(mcp_server.irrigation_clear_fault, "line_id") == tuple(sorted(MISTER_MASK_BIT))
    # L03 opens only with a fertilizer run and L11 is climate only.
    schedulable = literal_of(mcp_server.irrigation_schedule_set, "line_id")
    assert schedulable == tuple(line for line in LINE_IDS if line not in ("L03", "L11"))
    assert all(re.fullmatch(SCHEMAS["ScheduleInput"]["properties"]["line_id"]["pattern"], line) for line in schedulable)


def test_days_and_start_are_typed_never_a_json_string(mcp_server):  # noqa: F811
    annotation = plain_type(inspect.signature(mcp_server.irrigation_schedule_set).parameters["days"].annotation)
    assert get_args(get_args(annotation)[0]) == WEEKDAYS
    assert annotation is not str
    start = plain_type(inspect.signature(mcp_server.irrigation_schedule_set).parameters["start"].annotation)
    assert start is mcp_server.ScheduleStart


def test_the_start_model_is_the_openapi_schedule_start(mcp_server):  # noqa: F811
    schema = mcp_server.ScheduleStart.model_json_schema()
    wanted = SCHEMAS["ScheduleStart"]["properties"]
    assert set(schema["properties"]) == set(wanted) and schema["required"] == SCHEMAS["ScheduleStart"]["required"]
    assert schema["properties"]["kind"]["enum"] == wanted["kind"]["enum"]
    [clock] = [item for item in schema["properties"]["clock"]["anyOf"] if item["type"] == "string"]
    assert clock["pattern"] == wanted["clock"]["pattern"]
    [offset] = [item for item in schema["properties"]["offset_min"]["anyOf"] if item["type"] == "integer"]
    assert (offset["minimum"], offset["maximum"]) == (wanted["offset_min"]["minimum"], wanted["offset_min"]["maximum"])
    assert schema["additionalProperties"] is False


def test_the_run_bounds_are_the_run_tables_and_the_openapi(mcp_server):  # noqa: F811
    table = {**RUN_BOUNDS_BY_KIND["fert_drip"], **RUN_BOUNDS_BY_KIND["fert_mister"]}
    assert set(table) == {"minutes", "prewet_s", "flush_s", "on_s", "count", "gap_s"}
    for field, limits in table.items():
        assert bounds(mcp_server.irrigation_run, field) == limits, field
        wanted = SCHEMAS["RunRequest"]["properties"][field]
        assert limits == (wanted["minimum"], wanted["maximum"]), field


def test_the_schedule_bounds_are_the_widest_of_the_line_kinds_and_the_openapi(mcp_server):  # noqa: F811
    for field in ("on_s", "count", "gap_s", "prewet_s", "flush_s"):
        assert bounds(mcp_server.irrigation_schedule_set, field) == widest(field), field
        wanted = SCHEMAS["ScheduleInput"]["properties"][field]
        assert widest(field) == (wanted["minimum"], wanted["maximum"]), field
    assert bounds(mcp_server.irrigation_schedule_set, "slot_id") == writes.SLOT_ID_RANGE


def test_the_line_bounds_are_the_line_table(mcp_server):  # noqa: F811
    for field, limits in LINE_BOUNDS.items():
        assert bounds(mcp_server.irrigation_line_set, field) == limits, field


def test_paused_until_needs_its_utc_offset(mcp_server):  # noqa: F811
    from pydantic import TypeAdapter, ValidationError

    annotation = inspect.signature(mcp_server.irrigation_line_set).parameters["paused_until"].annotation
    adapter = TypeAdapter(annotation)
    assert adapter.validate_python("2026-11-03T18:00:00Z") == PAUSE
    with pytest.raises(ValidationError):
        adapter.validate_python("2026-11-03T18:00:00")


# ── Descriptions ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("tool", WRITE_TOOLS)
def test_each_description_starts_with_the_rule_and_names_its_twin_and_codes(mcp_server, tool):  # noqa: F811
    doc = " ".join(getattr(mcp_server, tool).__doc__.split())
    assert doc.startswith(NO_PLAN)
    for twin in TWINS[tool]:
        assert twin in doc, twin
    for code in CODES[tool]:
        assert code in PROBLEM_CODES and re.search(rf"\b{code}\b", doc), code
    assert "isError" in doc and "request_id" in doc and "replayed: true" in doc
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-", doc), "a literal example uuid"
    assert "manual" in doc


@pytest.mark.parametrize("tool", WRITE_TOOLS)
def test_each_description_names_only_tools_an_agent_can_call(mcp_server, tool):  # noqa: F811
    words = set(re.findall(r"\b[a-z_]+\b", getattr(mcp_server, tool).__doc__))
    named = words & set(mcp_server.TOOL_AUDIENCES)
    assert named <= mcp_server.audience_allowlist("agent"), named
    assert named <= mcp_server.audience_allowlist("iris"), named


def test_the_descriptions_say_each_manual_default(mcp_server):  # noqa: F811
    schedule = " ".join(mcp_server.irrigation_schedule_set.__doc__.split())
    assert "stores false on every create and edit unless you pass true" in schedule
    assert 'Activity shows "manual cleared"' in schedule
    assert "Only Iris is refused with manual, on an edit or a delete" in schedule
    line = " ".join(mcp_server.irrigation_line_set.__doc__.split())
    assert "false by default" in line and "A pause or resume never changes it" in line
    assert "Only Iris is refused with manual: on a manual line, a pause included" in line
    assert "never carry manual" in mcp_server.irrigation_run.__doc__
    assert "never carry manual" in mcp_server.irrigation_stop.__doc__
    assert "never carries or checks manual" in mcp_server.irrigation_clear_fault.__doc__


def test_the_descriptions_say_what_each_tool_does_before_the_engine(mcp_server):  # noqa: F811
    for tool in ("irrigation_schedule_set", "irrigation_line_set"):
        text = " ".join(getattr(mcp_server, tool).__doc__.split())
        assert "the write is stored and reaches the controller with the first program push" in text, tool
    run_doc = " ".join(mcp_server.irrigation_run.__doc__.split())
    assert "Before the irrigation firmware, run works on L01 only" in run_doc
    assert "never minutes" in run_doc  # the fertilizer misters
    stop_doc = " ".join(mcp_server.irrigation_stop.__doc__.split())
    assert "irrigation_stop always works" in stop_doc
    assert "After a Stop, VPD misting may pulse again; to keep a line off, disable it." in stop_doc
    # The OpenAPI API-IRR-07 and irrigation §4.7: Stop ends what runs now and disables nothing, and this says so.
    assert "A Stop disables nothing: survival fog and the next scheduled run can still start." in stop_doc
    clear_doc = " ".join(mcp_server.irrigation_clear_fault.__doc__.split())
    assert "before the irrigation firmware, not retryable, and nothing is stored" in clear_doc


def test_the_line_arguments_say_what_a_pause_and_a_disable_do_to_vpd_misting(mcp_server):  # noqa: F811
    # irrigation §4.2: an agent told "to keep a line off, disable it" must see which of the two stops VPD misting.
    assert "VPD misting included" in field_info(mcp_server.irrigation_line_set, "enabled").description
    assert "VPD misting carries on" in field_info(mcp_server.irrigation_line_set, "paused_until").description
    assert "a pause ignores it" in field_info(mcp_server.irrigation_line_set, "manual").description


def test_the_line_description_says_only_a_person_records_the_check(mcp_server):  # noqa: F811
    text = " ".join(mcp_server.irrigation_line_set.__doc__.split())
    assert "Only a person records a line's one-time check, on the dashboard" in text
    assert "check_required" in text and "the same call succeeds once it is recorded" in text


# ── irrigation_run ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("line_id", "body"),
    [
        ("L08", {"on_s": 30, "count": 4, "gap_s": 60, "prewet_s": 120, "flush_s": 240}),  # a fertilizer mister
        ("L10", {"on_s": 10, "count": 1, "gap_s": 30, "prewet_s": 0, "flush_s": 0}),
        ("L04", {"on_s": 45, "count": 8, "gap_s": 60}),
        ("L02", {"minutes": 5, "prewet_s": 60, "flush_s": 240}),
        ("L01", {"minutes": 12}),
        ("L01", {}),  # before the engine, the controller's irrig_wall_duration_min
    ],
)
def test_run_passes_the_body_to_the_service_unchanged(mcp_server, seen, line_id, body):  # noqa: F811
    assert run(mcp_server.irrigation_run(line_id, **body, **AUDIT)).isError is False
    [call] = seen
    assert (call.service, call.args) == ("run", (line_id,))
    fields = ("minutes", "prewet_s", "flush_s", "on_s", "count", "gap_s")
    assert {field: call.kwargs[field] for field in fields} == {field: body.get(field) for field in fields}
    assert {name: call.kwargs[name] for name in AUDIT} == AUDIT
    assert "manual" not in call.kwargs


# ── irrigation_stop and irrigation_clear_fault ──────────────────────────────


@pytest.mark.parametrize(("tool", "service"), [("irrigation_stop", "stop"), ("irrigation_clear_fault", "clear_fault")])
@pytest.mark.parametrize("line_id", [None, "L07"])
def test_stop_and_clear_fault_pass_the_line_and_never_a_trigger_or_a_flag(mcp_server, seen, tool, service, line_id):  # noqa: F811
    result = run(getattr(mcp_server, tool)(line_id, reason="hose", request_id=REQUEST_ID))
    assert result.isError is False
    [call] = seen
    assert (call.service, call.args, call.kwargs) == (service, (line_id,), {"reason": "hose", "request_id": REQUEST_ID})


# ── irrigation_schedule_set ─────────────────────────────────────────────────


def test_no_slot_id_creates_with_the_fields_it_carries_and_manual_false(mcp_server, seen):  # noqa: F811
    start = mcp_server.ScheduleStart(kind="sunrise", offset_min=15)
    arguments = {"line_id": "L04", "days": ["mon", "wed"], "on_s": 60, "count": 8, "gap_s": 60}
    assert run(mcp_server.irrigation_schedule_set(start=start, **arguments, **AUDIT)).isError is False
    [call] = seen
    assert call.service == "schedule_create"
    assert call.args == ({**arguments, "start": {"kind": "sunrise", "offset_min": 15}},)
    assert call.kwargs == {"manual": False, **AUDIT}


def test_a_clock_start_carries_only_its_clock_and_passes_true_when_asked(mcp_server, seen):  # noqa: F811
    start = mcp_server.ScheduleStart(kind="clock", clock="07:30")
    run(mcp_server.irrigation_schedule_set(line_id="L01", days=["sun"], start=start, on_s=600, manual=True))
    [call] = seen
    assert call.args[0]["start"] == {"kind": "clock", "clock": "07:30"}
    assert call.kwargs["manual"] is True


def test_a_slot_id_edits_with_only_the_fields_it_carries(mcp_server, seen):  # noqa: F811
    run(
        mcp_server.irrigation_schedule_set(
            slot_id=7, on_s=900, enabled=False, degraded_only=False, prewet_s=0, label="", **AUDIT
        )
    )
    [call] = seen
    assert (call.service, call.args[0]) == ("schedule_update", 7)
    assert call.args[1] == {"on_s": 900, "enabled": False, "degraded_only": False, "prewet_s": 0, "label": ""}
    assert call.kwargs == {"manual": False, **AUDIT}


@pytest.mark.parametrize("manual", [False, True])
def test_an_edit_with_no_field_still_passes_manual(mcp_server, seen, manual):  # noqa: F811
    run(mcp_server.irrigation_schedule_set(slot_id=7, manual=manual))
    [call] = seen
    assert (call.service, call.args, call.kwargs["manual"]) == ("schedule_update", (7, {}), manual)


def test_delete_passes_no_flag_and_answers_with_what_the_service_returns(mcp_server, conn, monkeypatch):  # noqa: F811
    seen_calls = []

    async def schedule_delete(connection, actor, slot_id, **kwargs):
        seen_calls.append((slot_id, kwargs))
        return activity.WriteResult(value=writes.ScheduleDeleted(slot_id=slot_id, manual_before=True), replayed=False)

    monkeypatch.setattr(writes, "schedule_delete", schedule_delete)
    result = run(mcp_server.irrigation_schedule_set(slot_id=7, delete=True, **AUDIT))
    assert result.isError is False
    assert payload(result) == {"slot_id": 7, "deleted": True, "manual_before": True}
    assert seen_calls == [(7, AUDIT)]


@pytest.mark.parametrize(
    ("arguments", "field"),
    [
        ({}, "slot_id"),
        ({"line_id": "L01"}, "slot_id"),
        ({"slot_id": 7, "on_s": 600}, "on_s"),
        ({"slot_id": 7, "enabled": False}, "enabled"),
        ({"slot_id": 7, "label": ""}, "label"),
        ({"slot_id": 7, "manual": True}, "manual"),
    ],
)
def test_a_delete_of_the_wrong_shape_is_invalid_request_before_any_database_call(
    mcp_server,  # noqa: F811
    mcp_types,  # noqa: F811
    monkeypatch,
    arguments,
    field,
):
    async def db():
        raise AssertionError("no connection for a bad argument")

    monkeypatch.setattr(mcp_server, "_db", db)
    result = run(mcp_server.irrigation_schedule_set(delete=True, **arguments))
    body = payload(result)
    assert result.isError is True and (body["code"], body["status"], body["field"]) == ("invalid_request", 400, field)
    assert body["retryable"] is False


# ── irrigation_line_set ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("line_id", "arguments", "sent"),
    [
        # A definition field: the flag it carries, false when left out, even when the field's own value is falsy.
        ("L01", {"enabled": False}, False),
        ("L01", {"enabled": True}, False),
        ("L04", {"vpd_member": False}, False),
        ("L04", {"schedule_member": False}, False),
        ("L01", {"daily_cap_min": 0}, False),
        ("L04", {"vpd_reserve_min": 0}, False),
        ("L01", {"enabled": False, "manual": True}, True),
        ("L01", {"enabled": False, "manual": False}, False),
        ("L01", {"daily_cap_min": 50, "paused_until": PAUSE}, False),
        ("L01", {"daily_cap_min": 50, "paused_until": PAUSE, "manual": True}, True),
        # Only a pause, on one line or all: none, so the stored flag stays.
        ("L01", {"paused_until": PAUSE}, None),
        ("all", {"paused_until": PAUSE}, None),
        # A pause never changes it, even with manual beside it, as REST's line_edit_manual.
        ("L01", {"paused_until": PAUSE, "manual": True}, None),
        ("L01", {"paused_until": PAUSE, "manual": False}, None),
        # manual alone sets it; nothing but a reason passes none.
        ("L01", {"manual": True}, True),
        ("L01", {"manual": False}, False),
        ("L01", {"reason": "why"}, None),
        ("L01", {}, None),
        # What the service refuses on all reaches it as sent.
        ("all", {"paused_until": PAUSE, "manual": False}, False),
        ("all", {"paused_until": PAUSE, "manual": True}, True),
        ("all", {"manual": True}, True),
        ("all", {"enabled": False}, False),
    ],
)
def test_the_manual_rule(mcp_server, seen, line_id, arguments, sent):  # noqa: F811
    assert run(mcp_server.irrigation_line_set(line_id, **arguments)).isError is False
    [call] = seen
    assert call.service == "line_update" and call.args[0] == line_id
    assert call.kwargs["manual"] is sent


def test_a_line_write_carries_only_its_fields_and_the_audit(mcp_server, seen):  # noqa: F811
    run(
        mcp_server.irrigation_line_set(
            "L04",
            enabled=False,
            vpd_member=True,
            schedule_member=False,
            daily_cap_min=0,
            vpd_reserve_min=0,
            paused_until=PAUSE,
            **AUDIT,
        )
    )
    [call] = seen
    assert call.args[1] == {
        "enabled": False,
        "vpd_member": True,
        "schedule_member": False,
        "daily_cap_min": 0,
        "vpd_reserve_min": 0,
        "paused_until": PAUSE,
    }
    assert call.kwargs == {"manual": False, **AUDIT}


def test_a_pause_carries_only_paused_until(mcp_server, seen):  # noqa: F811
    run(mcp_server.irrigation_line_set("all", paused_until=PAUSE))
    [call] = seen
    assert call.args == ("all", {"paused_until": PAUSE})


# ── Shared by all five ──────────────────────────────────────────────────────


def call_each(server):
    start = server.ScheduleStart(kind="clock", clock="07:30")
    return (
        ("schedule_create", server.irrigation_schedule_set(line_id="L01", days=["mon"], start=start, on_s=600)),
        ("schedule_update", server.irrigation_schedule_set(slot_id=7, on_s=600)),
        ("schedule_delete", server.irrigation_schedule_set(slot_id=7, delete=True)),
        ("line_update", server.irrigation_line_set("L01", enabled=False)),
        ("run", server.irrigation_run("L01")),
        ("stop", server.irrigation_stop()),
        ("clear_fault", server.irrigation_clear_fault()),
    )


@pytest.mark.parametrize("who", list(ACTORS))
def test_the_actor_comes_only_from_the_request(mcp_server, seen, who):  # noqa: F811
    source, name = ACTORS[who]
    token = mcp_server._REQUEST_ACTOR.set(mcp_server.McpActor(source=source, name=name))
    try:
        for service, call in call_each(mcp_server):
            assert run(call).isError is False, service
    finally:
        mcp_server._REQUEST_ACTOR.reset(token)
    assert tuple(call.service for call in seen) == SERVICES
    for call in seen:
        assert call.actor == Actor(source=source, name=name)
        assert call.actor.role is None


def test_without_a_resolved_caller_every_tool_is_forbidden_before_any_database_call(
    mcp_server,  # noqa: F811
    mcp_types,  # noqa: F811
    monkeypatch,
):
    async def db():
        raise AssertionError("no connection without an actor")

    monkeypatch.setattr(mcp_server, "_db", db)
    token = mcp_server._REQUEST_ACTOR.set(None)
    try:
        for service, call in call_each(mcp_server):
            result = run(call)
            assert result.isError is True, service
            assert (payload(result)["code"], payload(result)["status"]) == ("forbidden", 403), service
    finally:
        mcp_server._REQUEST_ACTOR.reset(token)


@pytest.mark.parametrize("service", SERVICES)
def test_a_replay_is_the_stored_json_with_replayed_true(mcp_server, conn, monkeypatch, service):  # noqa: F811
    async def replay(*_args, **_kwargs):
        return activity.WriteResult(value=Stored(), replayed=True)

    monkeypatch.setattr(writes, service, replay)
    calls = dict(call_each(mcp_server))
    result = run(calls.pop(service))
    for other in calls.values():
        other.close()
    assert result.isError is False and payload(result) == {"stored": True, "replayed": True}
    assert conn.closed


def test_a_first_write_has_no_replayed_key(mcp_server, seen):  # noqa: F811
    assert payload(run(mcp_server.irrigation_stop())) == {"stored": True}


@pytest.mark.parametrize(
    ("refusal", "wanted"),
    [
        (
            problem("line_busy", "L01 is already running", field="line_id", retry_after_s=90),
            {"code": "line_busy", "retryable": True, "retry_after_s": 90, "status": 422},
        ),
        (
            problem("manifold_busy", "L04 holds the manifold until 07:42", retry_after_s=30),
            {"code": "manifold_busy", "retryable": True, "retry_after_s": 30},
        ),
        (
            problem("manual", "Schedule 7 is manual", field="slot_id"),
            {"code": "manual", "retryable": False, "field": "slot_id"},
        ),
        (
            problem("check_required", "L06 has no check", field="enabled"),
            {"code": "check_required", "retryable": False, "field": "enabled"},
        ),
        (
            problem("unavailable", "Run-now on L04 arrives with the irrigation firmware", retryable=False),
            {"code": "unavailable", "retryable": False, "status": 503},
        ),
        (
            problem("request_id_reused", "A different request", field="request_id"),
            {"code": "request_id_reused", "retryable": False},
        ),
        (
            problem("out_of_range", "on_s must be 10-90", field="on_s", min=10, max=90, current=300),
            {"code": "out_of_range", "min": 10, "max": 90, "current": 300},
        ),
    ],
)
def test_a_refusal_is_the_problem_json_with_is_error(mcp_server, conn, monkeypatch, refusal, wanted):  # noqa: F811
    async def refuse(*_args, **_kwargs):
        raise ServiceError(refusal)

    for service in SERVICES:
        monkeypatch.setattr(writes, service, refuse)
    for service, call in call_each(mcp_server):
        result = run(call)
        assert result.isError is True, service
        assert payload(result) == refusal.body(), service
        assert wanted.items() <= payload(result).items(), service
    assert conn.closed


def test_a_database_failure_is_a_retryable_unavailable_that_names_nothing(mcp_server, mcp_types, monkeypatch, capsys):  # noqa: F811
    async def db():
        raise OSError("connection refused to the host in the dsn")

    monkeypatch.setattr(mcp_server, "_db", db)
    for service, call in call_each(mcp_server):
        body = payload(run(call))
        assert (body["code"], body["retryable"]) == ("unavailable", True), service
        assert "refused" not in json.dumps(body), service
    assert "refused" not in capsys.readouterr().err


# ── What no tool touches ────────────────────────────────────────────────────


def test_no_tool_or_service_touches_the_trigger_tables_or_reads_the_writes_switch(mcp_server):  # noqa: F811
    sources = {tool: inspect.getsource(getattr(mcp_server, tool)) for tool in WRITE_TOOLS}
    sources["_irrigation_write"] = inspect.getsource(mcp_server._irrigation_write)
    sources["writes.py"] = (ROOT / "verdify_services/irrigation/writes.py").read_text()
    for name, source in sources.items():
        assert "plan_delivery_log" not in source, name
        assert "planner_trigger_ledger" not in source, name
        assert "os.environ" not in source and "getenv" not in source, name
    assert "DASHBOARD_WRITES_ENABLED" not in (ROOT / "mcp/server.py").read_text()


def test_no_tool_exposes_the_bursts(mcp_server):  # noqa: F811
    assert "irrigation_override" not in mcp_server.TOOL_AUDIENCES
    for tool in WRITE_TOOLS:
        assert "writes.override" not in inspect.getsource(getattr(mcp_server, tool)), tool
