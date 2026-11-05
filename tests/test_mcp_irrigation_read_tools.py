"""VD-60 (#1173): irrigation_status, irrigation_schedule and irrigation_history, against stubbed services.

mcp/server.py loads under the FastMCP stub of tests/test_mcp_audience_auth.py and each tool runs with a stand-in
connection. tests/test_dashboard_db_irrigation_context.py runs the same tools on PostgreSQL as the MCP login, and
tests/test_mcp_transport_runtime.py runs them through the real MCP SDK. Also here: the instructions check (a test fails
if the server instructions name a tool that tools/list for admin does not return) and the self-description check (a test
fails if a new tool takes a free-form JSON string or has an empty description).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import types
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal, Union, get_args, get_origin, get_type_hints

import asyncpg
import pytest
import yaml
from test_mcp_audience_auth import AGENT_TOOLS, clean_auth_env, mcp_server, mcp_types  # noqa: F401

from verdify_schemas.irrigation import LINE_IDS, IrrigationCause, RunOutcome, RunPurpose, bounds_by_kind
from verdify_services.irrigation import reads
from verdify_services.problems import PROBLEM_CODES, RETRYABLE_CODES, ServiceError, problem

ROOT = Path(__file__).resolve().parents[1]
OPENAPI = yaml.safe_load((ROOT / "docs/design/dashboard/openapi/dashboard-api.v1.yaml").read_text())
SCHEMAS = OPENAPI["components"]["schemas"]
READ_TOOLS = ("irrigation_status", "irrigation_schedule", "irrigation_history")
# The new tools of architecture §5.6, registered or designed; each one names what it may take as a plain str.
NEW_TOOLS = frozenset(name for name in AGENT_TOOLS if name.startswith("irrigation_")) | {
    "set_setting",
    "settings",
    "activity",
}
# Plain str arguments an agent can write without a schema: free text, an id or a time, never structured data.
PLAIN_STR_ARGUMENTS = frozenset(
    {
        "reason",  # free text
        "label",
        "request_id",  # ids
        "trigger_id",
        "planner_instance",
        "cursor",
        "param",
        "group",
        "source",
        "actor",
        "action",
        "target",
        "since",  # times
        "until",
    }
)
JSON_STRING_ARGUMENTS = frozenset({"data", "payload", "json"})


def run(coroutine):
    return asyncio.run(coroutine)


def payload(result) -> dict:
    [content] = result.content
    assert content.type == "text"
    return json.loads(content.text)


class Conn:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


@pytest.fixture()
def conn(mcp_server, monkeypatch):  # noqa: F811
    connection = Conn()

    async def db():
        return connection

    monkeypatch.setattr(mcp_server, "_db", db)
    return connection


@pytest.fixture()
def as_steward(mcp_server):  # noqa: F811
    token = mcp_server._REQUEST_ACTOR.set(mcp_server.McpActor(source="agent:steward", name="steward"))
    yield
    mcp_server._REQUEST_ACTOR.reset(token)


def agenda_model() -> reads.ScheduleAgenda:
    return reads.ScheduleAgenda(
        schedules=[],
        occurrences=[],
        counts=reads.AgendaCounts(schedules=3, max_schedules=24),
        bounds=bounds_by_kind(),
        solar=[],
    )


def minutes_model(first: date, last: date) -> reads.MinutesReport:
    return reads.MinutesReport.model_validate(
        {
            "from": first,
            "to": last,
            "group_by": "line",
            "periods": [{"start": first, "coverage": "complete"}],
            "rows": [],
        }
    )


def plain_type(annotation):
    """The argument's type: Annotated unwrapped, and None dropped from an optional."""
    while hasattr(annotation, "__metadata__"):
        annotation = get_args(annotation)[0]
    if get_origin(annotation) in (Union, types.UnionType):
        members = [arg for arg in get_args(annotation) if arg is not type(None)]
        annotation = members[0] if len(members) == 1 else annotation
    return annotation


def literal_values(tool, argument) -> tuple[str, ...]:
    annotation = plain_type(inspect.signature(tool).parameters[argument].annotation)
    assert get_origin(annotation) is Literal, argument
    return get_args(annotation)


def field_info(tool, argument):
    annotation = inspect.signature(tool).parameters[argument].annotation
    return next(item for item in annotation.__metadata__)


# ── Audiences ───────────────────────────────────────────────────────────────


def test_the_three_reads_are_for_iris_agents_and_admin_and_join_the_read_only_tools(mcp_server):  # noqa: F811
    for tool in READ_TOOLS:
        assert mcp_server.TOOL_AUDIENCES[tool] == frozenset({"iris", "agent", "admin"}), tool
        assert tool in mcp_server._TOOL_READ_ONLY and tool in mcp_server.HERMES_REQUIRED_TOOLS
        assert tool in mcp_server.audience_allowlist("iris")
        assert tool in mcp_server.audience_allowlist("agent")
        assert tool in mcp_server.audience_allowlist("admin")
        assert tool not in mcp_server.audience_allowlist("experiment")
        assert inspect.signature(getattr(mcp_server, tool)).return_annotation is inspect.Signature.empty


# ── The adapters ────────────────────────────────────────────────────────────


def test_status_returns_the_services_json_read_as_the_request_actor(
    mcp_server,  # noqa: F811
    mcp_types,  # noqa: F811
    conn,
    as_steward,
    monkeypatch,
):
    seen = []

    async def status(connection, actor):
        seen.append((connection, actor))
        return agenda_model()  # the adapter only serializes what the service returned

    monkeypatch.setattr(reads, "status", status)
    result = run(mcp_server.irrigation_status())
    assert result.isError is False and payload(result) == agenda_model().model_dump(mode="json")
    [(connection, actor)] = seen
    assert connection is conn and conn.closed
    assert (actor.source, actor.name) == ("agent:steward", "steward")


def test_a_read_needs_no_resolved_caller(mcp_server, mcp_types, conn, monkeypatch):  # noqa: F811
    seen = []

    async def status(connection, actor):
        seen.append(actor)
        return agenda_model()

    monkeypatch.setattr(reads, "status", status)
    assert mcp_server.current_actor() is None
    assert run(mcp_server.irrigation_status()).isError is False
    assert seen == [None]


def test_schedule_passes_its_windows_and_returns_the_bounds_per_line_kind_and_24(
    mcp_server,  # noqa: F811
    mcp_types,  # noqa: F811
    conn,
    monkeypatch,
):
    seen = []

    async def agenda(connection, actor, days, past_days):
        seen.append((days, past_days))
        return agenda_model()

    monkeypatch.setattr(reads, "agenda", agenda)
    body = payload(run(mcp_server.irrigation_schedule()))
    assert body["counts"]["max_schedules"] == 24
    assert set(body["bounds"]) == {"drip", "fert_drip", "mister", "fert_mister"}
    assert body["bounds"] == json.loads(json.dumps(bounds_by_kind()))
    run(mcp_server.irrigation_schedule(days=2, past_days=7))
    assert seen == [(7, 0), (2, 7)] and conn.closed


def test_schedule_out_of_range_is_invalid_request_with_field(mcp_server, mcp_types, conn):  # noqa: F811
    # Direct calls reach the service's own check; through FastMCP, Field(ge, le) refuses first.
    body = payload(run(mcp_server.irrigation_schedule(days=9)))
    assert (body["code"], body["field"], body["min"], body["max"], body["current"]) == (
        "invalid_request",
        "days",
        1,
        7,
        9,
    )


def test_history_minutes_takes_local_dates_and_group_by(mcp_server, mcp_types, conn, monkeypatch):  # noqa: F811
    seen = []
    report = minutes_model(date(2026, 10, 1), date(2026, 10, 31))

    async def minutes(connection, actor, from_, to, group_by, purpose=None):
        seen.append((from_, to, group_by, purpose))
        return report

    monkeypatch.setattr(reads, "minutes", minutes)
    result = run(mcp_server.irrigation_history("minutes", "2026-10-01", "2026-10-31", group_by="line"))
    assert result.isError is False
    assert seen == [(date(2026, 10, 1), date(2026, 10, 31), "line", None)]
    run(mcp_server.irrigation_history("minutes", "2026-10-01", "2026-10-31", purpose="irrigation"))
    assert seen[-1] == (date(2026, 10, 1), date(2026, 10, 31), "zone", "irrigation")
    body = payload(result)
    assert body == report.model_dump(mode="json", by_alias=True) and body["from"] == "2026-10-01"


def test_history_runs_takes_date_times_and_its_filters(mcp_server, mcp_types, conn, monkeypatch):  # noqa: F811
    seen = []

    async def runs(connection, actor, from_, to, **filters):
        seen.append((from_, to, filters))
        return reads.RunPage(items=[], next_cursor="next")

    monkeypatch.setattr(reads, "runs", runs)
    result = run(
        mcp_server.irrigation_history(
            "runs",
            "2026-10-01T00:00:00-06:00",
            "2026-10-02T00:00:00Z",
            line="L01",
            cause="run_now",
            outcome="ok",
            cursor="abc",
        )
    )
    assert payload(result) == {"items": [], "next_cursor": "next"}
    [(from_, to, filters)] = seen
    assert from_ == datetime(2026, 10, 1, 6, tzinfo=UTC) and to == datetime(2026, 10, 2, tzinfo=UTC)
    assert filters == {"line": "L01", "cause": "run_now", "outcome": "ok", "cursor": "abc", "purpose": None}
    run(mcp_server.irrigation_history("runs", "2026-10-01T00:00:00-06:00", "2026-10-02T00:00:00Z", purpose="climate"))
    assert seen[-1][2]["purpose"] == "climate"


@pytest.mark.parametrize(
    ("view", "since", "until", "extra", "code", "field"),
    [
        # An argument that does not belong to the view.
        ("minutes", "2026-10-01", "2026-10-02", {"line": "L01"}, "invalid_request", "line"),
        ("minutes", "2026-10-01", "2026-10-02", {"cause": "vpd"}, "invalid_request", "cause"),
        ("minutes", "2026-10-01", "2026-10-02", {"outcome": "ok"}, "invalid_request", "outcome"),
        ("minutes", "2026-10-01", "2026-10-02", {"cursor": "abc"}, "invalid_request", "cursor"),
        ("runs", "2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z", {"group_by": "line"}, "invalid_request", "group_by"),
        # A window in the other view's shape, or not a time at all.
        ("minutes", "2026-10-01T00:00:00Z", "2026-10-02", {}, "invalid_request", "since"),
        ("minutes", "2026-10-01", "tomorrow", {}, "invalid_request", "until"),
        ("runs", "2026-10-01", "2026-10-02T00:00:00Z", {}, "invalid_request", "since"),
        ("runs", "2026-10-01T00:00:00Z", "2026-10-02T00:00:00", {}, "invalid_request", "until"),
    ],
)
def test_history_refuses_with_field_before_any_database_call(
    mcp_server,  # noqa: F811
    mcp_types,  # noqa: F811
    monkeypatch,
    view,
    since,
    until,
    extra,
    code,
    field,
):
    async def db():
        raise AssertionError("no connection for a bad argument")

    monkeypatch.setattr(mcp_server, "_db", db)
    result = run(mcp_server.irrigation_history(view, since, until, **extra))
    body = payload(result)
    assert result.isError is True and (body["code"], body["field"]) == (code, field)
    assert body["status"] == 400 and body["retryable"] is False


@pytest.mark.parametrize(
    ("view", "since", "until", "limit"),
    [
        ("minutes", "2025-01-01", "2026-10-01", 400),
        ("runs", "2026-08-01T00:00:00Z", "2026-10-01T00:00:00Z", 31),
    ],
)
def test_a_wider_window_is_window_too_large_naming_since(
    mcp_server,  # noqa: F811
    mcp_types,  # noqa: F811
    conn,
    view,
    since,
    until,
    limit,
):
    result = run(mcp_server.irrigation_history(view, since, until))
    body = payload(result)
    assert result.isError is True
    assert (body["code"], body["field"], body["max"]) == ("window_too_large", "since", limit)
    assert conn.closed


def test_a_cursor_that_is_not_a_next_cursor_is_invalid_request(mcp_server, mcp_types, conn):  # noqa: F811
    result = run(mcp_server.irrigation_history("runs", "2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z", cursor="@"))
    assert (payload(result)["code"], payload(result)["field"]) == ("invalid_request", "cursor")


@pytest.mark.parametrize("tool", READ_TOOLS)
def test_a_query_over_the_limit_is_query_timeout_and_retryable(
    mcp_server,  # noqa: F811
    mcp_types,  # noqa: F811
    conn,
    monkeypatch,
    tool,
):
    async def slow(*_args, **_kwargs):
        raise asyncpg.QueryCanceledError("canceling statement due to statement timeout")

    for name in ("status", "agenda", "minutes"):
        monkeypatch.setattr(reads, name, slow)
    arguments = ("minutes", "2026-10-01", "2026-10-02") if tool == "irrigation_history" else ()
    result = run(getattr(mcp_server, tool)(*arguments))
    body = payload(result)
    assert result.isError is True and (body["code"], body["status"], body["retryable"]) == ("query_timeout", 503, True)
    assert conn.closed


def test_a_database_outage_is_unavailable_and_names_nothing_of_the_failure(
    mcp_server,  # noqa: F811
    mcp_types,  # noqa: F811
    monkeypatch,
    capsys,
):
    async def db():
        raise OSError("connection refused to the host in the dsn")

    monkeypatch.setattr(mcp_server, "_db", db)
    result = run(mcp_server.irrigation_status())
    body = payload(result)
    assert result.isError is True and (body["code"], body["retryable"]) == ("unavailable", True)
    assert "refused" not in json.dumps(body)
    assert "refused" not in capsys.readouterr().err  # the log says the failure's type, never its text


def test_a_service_refusal_keeps_its_problem_json(mcp_server, mcp_types, conn, monkeypatch):  # noqa: F811
    refusal = problem("invalid_request", "past_days must be 0-7", field="past_days", min=0, max=7)

    async def agenda(*_args, **_kwargs):
        raise ServiceError(refusal)

    monkeypatch.setattr(reads, "agenda", agenda)
    result = run(mcp_server.irrigation_schedule())
    assert result.isError is True and payload(result) == refusal.body()


# ── Typed arguments ─────────────────────────────────────────────────────────


def test_the_arguments_are_the_signature_the_design_gives(mcp_server):  # noqa: F811
    assert list(inspect.signature(mcp_server.irrigation_status).parameters) == []
    assert list(inspect.signature(mcp_server.irrigation_schedule).parameters) == ["days", "past_days"]
    history = inspect.signature(mcp_server.irrigation_history).parameters
    assert list(history) == ["view", "since", "until", "group_by", "line", "cause", "outcome", "cursor", "purpose"]
    assert history["view"].default is inspect.Parameter.empty
    assert history["since"].default is inspect.Parameter.empty and history["until"].default is inspect.Parameter.empty
    assert history["group_by"].default == "zone"
    assert all(history[name].default is None for name in ("line", "cause", "outcome", "cursor", "purpose"))
    schedule = inspect.signature(mcp_server.irrigation_schedule).parameters
    assert (schedule["days"].default, schedule["past_days"].default) == (7, 0)


def test_the_windows_are_bounded_as_the_rest_twin(mcp_server):  # noqa: F811
    def bounds(argument):
        metadata = field_info(mcp_server.irrigation_schedule, argument).metadata
        return {name: getattr(item, name) for item in metadata for name in ("ge", "le") if hasattr(item, name)}

    assert bounds("days") == {"ge": 1, "le": 7}
    assert bounds("past_days") == {"ge": 0, "le": 7}


def test_the_enums_equal_the_openapi(mcp_server):  # noqa: F811
    history = mcp_server.irrigation_history
    assert literal_values(history, "view") == ("minutes", "runs")
    assert literal_values(history, "group_by") == ("zone", "line", "cause")
    assert literal_values(history, "line") == LINE_IDS
    assert all(re.fullmatch(SCHEMAS["LineId"]["pattern"], line) for line in LINE_IDS)
    assert list(literal_values(history, "cause")) == SCHEMAS["IrrigationCause"]["enum"]
    assert list(literal_values(history, "cause")) == [cause.value for cause in IrrigationCause]
    assert list(literal_values(history, "outcome")) == SCHEMAS["RunOutcome"]["enum"]
    assert list(literal_values(history, "outcome")) == [outcome.value for outcome in RunOutcome]
    assert list(literal_values(history, "purpose")) == SCHEMAS["RunPurpose"]["enum"]
    assert list(literal_values(history, "purpose")) == [purpose.value for purpose in RunPurpose]


def test_each_description_names_its_twin_says_it_reads_lists_its_codes_and_says_null_never_0(mcp_server):  # noqa: F811
    for tool, twins in (
        ("irrigation_status", ("API-IRR-01", "GET /v1/irrigation")),
        ("irrigation_schedule", ("API-IRR-02", "GET /v1/irrigation/schedules")),
        ("irrigation_history", ("API-IRR-10", "GET /v1/irrigation/minutes", "API-IRR-11", "GET /v1/irrigation/runs")),
    ):
        doc = getattr(mcp_server, tool).__doc__
        for twin in twins:
            assert twin in doc, (tool, twin)
        assert "only reads" in doc, tool
        assert "does not count as your plan for a trigger" in doc, tool
        assert "null, never 0" in doc, tool
        assert "isError" in doc, tool
        for code in ("invalid_request", "window_too_large", "query_timeout", "unavailable"):
            assert code in doc, (tool, code)
    history = mcp_server.irrigation_history
    assert "since and until are the REST from and to" in history.__doc__
    assert "400 days" in history.__doc__ and "31 days" in history.__doc__
    assert "max_schedules 24" in mcp_server.irrigation_schedule.__doc__
    assert field_info(history, "since").description.startswith("The REST from.")
    assert field_info(history, "until").description.startswith("The REST to.")


# ── The instructions check ──────────────────────────────────────────────────


def instructions_named(text: str) -> set[str]:
    """Backticked names, and bare ones with an underscore, which no English word has ("irrigation_stop always works")."""
    return set(re.findall(r"`([a-z_]+)`", text)) | set(re.findall(r"\b[a-z]+(?:_[a-z]+)+\b", text))


def instructions_problems(text: str, tool_names: frozenset[str], admin_tools: frozenset[str]) -> list[str]:
    """Every name ``text`` gives that is a tool name, registered or designed, and that admin cannot call."""
    return sorted(name for name in instructions_named(text) if name in tool_names and name not in admin_tools)


def test_the_server_instructions_name_only_tools_admin_can_call(mcp_server):  # noqa: F811
    admin = mcp_server.audience_allowlist("admin")
    assert admin == set(mcp_server.TOOL_AUDIENCES)
    assert instructions_problems(mcp_server.SERVER_INSTRUCTIONS, NEW_TOOLS | admin, admin) == []
    named = instructions_named(mcp_server.SERVER_INSTRUCTIONS) & (NEW_TOOLS | admin)
    assert named >= {t for t in mcp_server.TOOL_AUDIENCES if t.startswith("irrigation_")}


def test_the_instructions_check_fails_on_a_tool_that_is_not_registered_yet(mcp_server):  # noqa: F811
    admin = frozenset(READ_TOOLS)
    text = "Use `irrigation_status`, `irrigation_line_set` and `set_setting`; `request_id` is no tool."
    assert instructions_problems(text, NEW_TOOLS | admin, admin) == ["irrigation_line_set", "set_setting"]
    # Without backticks too: the design's own sentence names a tool bare.
    assert instructions_problems("irrigation_clear_fault always works.", NEW_TOOLS | admin, admin) == [
        "irrigation_clear_fault"
    ]
    # An ordinary word that is also a designed tool's name only counts when it is backticked.
    assert instructions_problems("Recent activity and settings.", NEW_TOOLS | admin, admin) == []
    assert instructions_problems("See `activity`.", NEW_TOOLS | admin, admin) == ["activity"]


def test_the_instructions_are_the_constant_and_say_what_the_design_gives(mcp_server):  # noqa: F811
    text = mcp_server.SERVER_INSTRUCTIONS
    assert mcp_server.mcp._init_kwargs["instructions"] == text
    assert "Band params (temp_low, temp_high, vpd_low, vpd_high) are dispatcher-owned" in text
    assert "run works on L01 only" in text and "irrigation_stop always works" in text
    assert "`request_id`" in text and "`replayed: true`" in text
    assert "ProblemCode" in text and "`retryable` is true only where waiting helps" in text
    assert RETRYABLE_CODES < set(PROBLEM_CODES)


# ── The self-description check ──────────────────────────────────────────────


def self_description_problems(name: str, tool) -> list[str]:
    """Why a new tool does not describe itself: an empty description, or a free-form JSON string argument."""
    problems = [] if (tool.__doc__ or "").strip() else [f"{name}: empty description"]
    hints = get_type_hints(tool, include_extras=True)
    for argument in inspect.signature(tool).parameters:
        if argument in JSON_STRING_ARGUMENTS:
            problems.append(f"{name}.{argument}: a JSON string argument")
        if plain_type(hints.get(argument)) is str and argument not in PLAIN_STR_ARGUMENTS:
            problems.append(f"{name}.{argument}: a plain str that is not free text, an id or a time")
    return problems


def test_each_new_tool_describes_itself_and_takes_no_json_string(mcp_server):  # noqa: F811
    registered = {tool.name for tool in mcp_server.mcp._registered_tools}
    new = sorted(name for name in registered if name.startswith("irrigation_") or name in NEW_TOOLS)
    assert {*READ_TOOLS, "irrigation_run", "irrigation_stop"} <= set(new)
    problems = [item for name in new for item in self_description_problems(name, getattr(mcp_server, name))]
    assert problems == []


def test_the_self_description_check_fails_on_a_json_string_or_an_empty_description():
    async def empty(reason: str | None = None):
        pass

    async def stringly(data: str, payload: str | None = None, blob: str | None = None):
        """Has a description."""

    assert self_description_problems("empty", empty) == ["empty: empty description"]
    assert self_description_problems("stringly", stringly) == [
        "stringly.data: a JSON string argument",
        "stringly.data: a plain str that is not free text, an id or a time",
        "stringly.payload: a JSON string argument",
        "stringly.payload: a plain str that is not free text, an id or a time",
        "stringly.blob: a plain str that is not free text, an id or a time",
    ]
