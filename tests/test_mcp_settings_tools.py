"""The MCP set_setting, settings and activity adapters and the alerts adapter (VD-58, #1171), with no database.

server.py loads under a FastMCP stub that dispatches to the registered tool functions, with a small mcp.types stub
for the result helpers. The services are stubbed where the adapter is the point: which function it calls, the Problem
JSON with isError, replayed, the actor rule, the argument checks, the schemas and the descriptions. alerts runs the
real verdify_services.alerts over FakeAlertDB's in-memory alert_log, and set_plan and set_tunable run over a fake
connection, for their one activity row. tests/test_dashboard_db_mcp_settings.py proves the same writes on PostgreSQL
as the MCP login's grants, and tests/test_mcp_transport_runtime.py the result pass-through under the real SDK.
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import re
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Literal, get_args, get_origin, get_type_hints

import asyncpg
import pytest
from test_mcp_named_agents import (
    _MCP_STUB_MODULES,
    _MISSING_MODULE,
    AlertConnection,
    _install_dispatching_fastmcp_stub,
)
from test_verdify_services_alerts import NOW as ALERTS_NOW
from test_verdify_services_alerts import FakeAlertDB

from verdify_services import alerts as alerts_service
from verdify_services.activity import ActivityEntry, ActivityPage, WriteResult
from verdify_services.actor import ActorRef
from verdify_services.problems import problem
from verdify_services.reads import SettingRows
from verdify_services.settings import DesiredSetting, Setting, SettingGroup, SettingHistory

REPO_ROOT = Path(__file__).resolve().parents[1]
STUB_MODULES = (*_MCP_STUB_MODULES, "mcp.types")
TRIGGER_ID = "00000000-0000-4000-8000-000000000001"
REQUEST_ID = "00000000-0000-4000-8000-000000000002"
CREATED = datetime(2026, 11, 2, 18, tzinfo=UTC)
PARAM = "mister_engage_kpa"
ALL_GROUPS = (
    "planner_policy",
    "scheduled_policy",
    "controller_safety",
    "crop_band",
    "site_water_caps",
    "read_only",
    "band_in_force",
)


@dataclass
class TextContent:
    type: str
    text: str


@dataclass
class CallToolResult:
    content: list
    isError: bool = False  # noqa: N815 (the MCP field name)


@pytest.fixture(scope="module")
def server():
    prior_modules = {name: sys.modules.get(name, _MISSING_MODULE) for name in STUB_MODULES}
    _install_dispatching_fastmcp_stub()
    types_module = ModuleType("mcp.types")
    types_module.TextContent = TextContent
    types_module.CallToolResult = CallToolResult
    sys.modules["mcp"].types = types_module
    sys.modules["mcp.types"] = types_module
    try:
        spec = importlib.util.spec_from_file_location(
            "verdify_mcp_server_settings_tools_test", REPO_ROOT / "mcp" / "server.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module
    finally:
        for name, previous in prior_modules.items():
            if previous is _MISSING_MODULE:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


class NoSqlConnection:
    """The adapters run no SQL of their own: every statement belongs to a stubbed service."""

    closed = False

    async def close(self):
        self.closed = True

    def __getattr__(self, name):
        raise AssertionError(f"an adapter called conn.{name}")


@pytest.fixture()
def opened(server, monkeypatch):
    connections: list[NoSqlConnection] = []

    async def db():
        connections.append(NoSqlConnection())
        return connections[-1]

    monkeypatch.setattr(server, "_db", db)
    return connections


def run_as(server, actor, coroutine):
    """Await a tool under a request actor, as AudienceAuthorizedFastMCP.call_tool sets it."""

    async def under_actor():
        token = server._REQUEST_ACTOR.set(actor)
        try:
            return await coroutine
        finally:
            server._REQUEST_ACTOR.reset(token)

    return asyncio.run(under_actor())


def iris(server):
    return server.McpActor(source="iris", name="iris")


def steward(server):
    return server.McpActor(source="agent:steward", name="steward")


def body(result) -> dict:
    assert isinstance(result, CallToolResult) and len(result.content) == 1
    assert result.content[0].type == "text"
    return json.loads(result.content[0].text)


def desired(**overrides) -> DesiredSetting:
    fields = {
        "id": 41,
        "param": PARAM,
        "value": 1.55,
        "snapped_from": 1.53,
        "manual": False,
        "manual_before": False,
        "state": "requested",
        "actor": ActorRef(source="agent:steward", name="steward"),
        "created_at": CREATED,
    }
    return DesiredSetting(**{**fields, **overrides})


@pytest.fixture()
def writes(server, monkeypatch):
    """settings.write and settings.release stubbed: the calls they got, and what they answer."""
    log = SimpleLog(answer=WriteResult(value=desired()))

    async def write(conn, actor, param, value, manual, reason, request_id, extra_detail=None):
        log.calls.append(("write", actor, param, value, manual, reason, request_id, extra_detail))
        return log.reply()

    async def release(conn, actor, param, reason, request_id, extra_detail=None):
        log.calls.append(("release", actor, param, reason, request_id, extra_detail))
        return log.reply()

    monkeypatch.setattr(server.settings_service, "write", write)
    monkeypatch.setattr(server.settings_service, "release", release)
    return log


@dataclass
class SimpleLog:
    answer: object

    def __post_init__(self):
        self.calls: list[tuple] = []

    def reply(self):
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


# ── The result helpers ──────────────────────────────────────────────────────


def test_tool_ok_is_one_text_block_of_the_rest_json_and_replayed_only_on_a_replay(server):
    stored = desired()
    fresh = server._tool_ok(stored)
    assert fresh.isError is False
    assert body(fresh) == stored.model_dump(mode="json")
    assert "replayed" not in body(fresh)
    again = server._tool_ok(stored, replayed=True)
    assert body(again) == {**stored.model_dump(mode="json"), "replayed": True}


def test_tool_problem_is_the_problem_json_with_is_error(server):
    refusal = problem("out_of_range", "Must be 0.5 to 2.5 kPa", field="value", min=0.5, max=2.5, current=1.6)
    result = server._tool_problem(refusal)
    assert result.isError is True
    assert body(result) == refusal.body()
    assert body(result)["status"] == 422 and body(result)["retryable"] is False


# ── set_setting: which service, with which flag ─────────────────────────────


def test_a_write_defaults_manual_to_false_and_returns_desired_setting(server, writes, opened):
    result = run_as(server, steward(server), server.set_setting(PARAM, 1.53))
    assert result.isError is False
    assert body(result) == desired().model_dump(mode="json")
    ((kind, actor, param, value, manual, reason, request_id, extra),) = writes.calls
    assert (kind, param, value, manual, reason, request_id, extra) == ("write", PARAM, 1.53, False, None, None, {})
    assert (actor.source, actor.name, actor.role) == ("agent:steward", "steward", None)
    assert [conn.closed for conn in opened] == [True]


def test_a_write_stores_the_manual_flag_it_is_given(server, writes, opened):
    run_as(server, steward(server), server.set_setting(PARAM, 1.5, manual=True, reason="hold", request_id=REQUEST_ID))
    assert writes.calls[0][4:7] == (True, "hold", REQUEST_ID)


def test_a_release_calls_release(server, writes, opened):
    writes.answer = WriteResult(value=desired(value=None, snapped_from=None, state="released", manual_before=True))
    result = run_as(server, steward(server), server.set_setting(PARAM, release=True, reason="done"))
    assert body(result)["state"] == "released" and body(result)["manual_before"] is True
    assert writes.calls == [
        ("release", writes.calls[0][1], PARAM, "done", None, {}),
    ]


@pytest.mark.parametrize(
    ("arguments", "field"),
    [
        ({}, "value"),
        ({"release": False}, "value"),
        ({"value": 1.5, "release": True}, "value"),
        ({"release": True, "manual": True}, "manual"),
    ],
    ids=["neither", "explicit-no-release", "both", "manual-release"],
)
def test_any_other_combination_is_invalid_request_before_the_database(server, writes, opened, arguments, field):
    result = run_as(server, steward(server), server.set_setting(PARAM, **arguments))
    assert result.isError is True
    assert (body(result)["code"], body(result)["field"], body(result)["status"]) == ("invalid_request", field, 400)
    assert writes.calls == [] and opened == []


@pytest.mark.parametrize("name", ["request_id", "trigger_id"])
@pytest.mark.parametrize("value", ["", "not-a-uuid", "1234"])
def test_request_and_trigger_ids_must_be_uuids(server, writes, opened, name, value):
    result = run_as(server, steward(server), server.set_setting(PARAM, 1.5, **{name: value}))
    assert (body(result)["code"], body(result)["field"]) == ("invalid_request", name)
    assert result.isError is True and writes.calls == [] and opened == []


def test_trigger_and_instance_reach_only_the_activity_detail(server, writes, opened):
    run_as(
        server,
        iris(server),
        server.set_setting(PARAM, 1.5, trigger_id=TRIGGER_ID.upper(), planner_instance="local"),
    )
    assert writes.calls[0][-1] == {"trigger_id": TRIGGER_ID, "planner_instance": "local"}
    source = inspect.getsource(inspect.unwrap(server.set_setting))
    for table in ("plan_delivery_log", "planner_trigger_ledger", "setpoint_plan", "setpoint_changes"):
        assert table not in source


# ── set_setting: the actor rule ─────────────────────────────────────────────


def test_no_argument_sets_the_actor(server):
    names = set(inspect.signature(server.set_setting).parameters)
    assert names == {
        "param",
        "value",
        "release",
        "manual",
        "reason",
        "request_id",
        "trigger_id",
        "planner_instance",
    }


def test_a_call_with_no_actor_is_forbidden_before_the_database(server, writes, opened):
    result = run_as(server, None, server.set_setting(PARAM, 1.5))
    assert result.isError is True
    assert (body(result)["code"], body(result)["status"]) == ("forbidden", 403)
    assert writes.calls == [] and opened == []


@pytest.mark.parametrize(
    ("audience", "agent", "source", "name"),
    [
        ("iris", None, "iris", "iris"),
        ("agent", "orbit", "agent:orbit", "orbit"),
        ("admin", None, "agent:admin", "admin"),
    ],
    ids=["iris", "named-agent", "admin"],
)
def test_the_actor_comes_from_the_caller(server, writes, opened, monkeypatch, audience, agent, source, name):
    """Through the dispatcher: the resolved caller sets the actor, so a call cannot choose its name."""
    monkeypatch.setenv("VERDIFY_MCP_AUTH_MODE", "enforce")
    monkeypatch.setattr(server, "_request_bearer_token", lambda _server: "fixture-token")
    monkeypatch.setattr(server, "resolve_caller", lambda _token: (audience, agent))
    monkeypatch.setattr(server, "authorize_tool_call", lambda *_args: None)
    asyncio.run(server.mcp.call_tool("set_setting", {"param": PARAM, "value": 1.5}))
    actor = writes.calls[0][1]
    assert (actor.source, actor.name, actor.role) == (source, name, None)


def test_the_planning_prompt_lists_exactly_irises_tools_but_slack(server):
    source = (REPO_ROOT / "ingestor" / "iris_planner.py").read_text()
    listed = source[source.index("Hermes exposes 25 production tools:") : source.index("NEVER run psql")]
    names = re.findall(r"`([a-z_]+)`", listed)
    assert len(names) == 25
    assert set(names) == server.HERMES_REQUIRED_TOOLS - {"slack_ops"}


def test_every_audience_of_the_three_tools(server):
    for tool in ("set_setting", "settings", "activity"):
        assert server.TOOL_AUDIENCES[tool] == {"iris", "agent", "admin"}
        assert tool in server.HERMES_REQUIRED_TOOLS
    assert {"settings", "activity"} <= server._TOOL_READ_ONLY
    assert "set_setting" not in server._TOOL_READ_ONLY


# ── set_setting: refusals and replays ───────────────────────────────────────


@pytest.mark.parametrize(
    "refusal",
    [
        problem("unknown_target", "'nope' is not a setting", field="param"),
        problem("read_only", "temp_low is read-only: legacy wide limit", field="param", min=30, max=120, current=60.0),
        problem("out_of_range", "Must be 0.5 to 2.5 kPa", field="value", min=0.5, max=2.5, current=1.6),
        problem("manual", "held manual by jason", field="param", min=0.5, max=2.5, current=1.7),
        problem("request_id_reused", "This request id was already used", field="request_id"),
        problem("forbidden", "A viewer cannot change anything"),
    ],
    ids=lambda refusal: refusal.code,
)
def test_a_service_refusal_is_its_problem_json_with_is_error(server, writes, opened, refusal):
    writes.answer = server.ServiceError(refusal)
    result = run_as(server, iris(server), server.set_setting(PARAM, 1.5))
    assert result.isError is True
    assert body(result) == refusal.body()
    assert [conn.closed for conn in opened] == [True]


def test_a_replay_returns_the_stored_json_with_replayed_true(server, writes, opened):
    writes.answer = WriteResult(value=desired(), replayed=True)
    result = run_as(server, steward(server), server.set_setting(PARAM, 1.53, request_id=REQUEST_ID))
    assert result.isError is False
    assert body(result) == {**desired().model_dump(mode="json"), "replayed": True}


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (asyncpg.QueryCanceledError("statement timeout"), "query_timeout"),
        (ConnectionRefusedError("refused"), "unavailable"),
        (asyncpg.CannotConnectNowError("the database system is starting up"), "unavailable"),
        (asyncpg.AdminShutdownError("terminating connection due to administrator command"), "unavailable"),
        (asyncpg.TooManyConnectionsError("too many connections for role"), "unavailable"),
    ],
    ids=["statement-timeout", "database-down", "starting-up", "shutting-down", "out-of-connections"],
)
def test_a_database_failure_is_a_retryable_problem(server, writes, opened, failure, code):
    writes.answer = failure
    result = run_as(server, steward(server), server.set_setting(PARAM, 1.5))
    assert result.isError is True
    assert (body(result)["code"], body(result)["retryable"]) == (code, True)
    assert str(failure) not in body(result)["detail"]


# ── settings ────────────────────────────────────────────────────────────────


def setting(param, group, *, writable=True, manual=None, applied_from="default") -> Setting:
    return Setting(
        param=param,
        label=param,
        control_class="readback_context" if group == "read_only" else "planner_policy",
        group=group,
        kind="numeric",
        min=0.5,
        max=2.5,
        step=0.05,
        writable=writable,
        readback_via="cfg",
        readback=None,
        desired=None if manual is None else desired(param=param, manual=manual),
        applied_from=applied_from,
        state="no_readback",
    )


@pytest.fixture()
def catalog(server, monkeypatch):
    rows = SettingRows(
        items=[
            setting("a", "planner_policy", manual=True, applied_from="manual"),
            setting("b", "planner_policy", manual=False, applied_from="desired"),
            setting("c", "crop_band"),
            setting("d", "read_only", writable=False),
        ],
        server_time=CREATED,
    )

    async def settings_rows(conn):
        return rows

    monkeypatch.setattr(server.reads_service, "settings_rows", settings_rows)
    return rows


def rows_of(server, **arguments) -> list[str]:
    return [row["param"] for row in body(asyncio.run(server.settings(**arguments)))["items"]]


@pytest.mark.parametrize(
    ("arguments", "selected"),
    [
        ({}, ["a", "b", "c", "d"]),
        ({"param": "c"}, ["c"]),
        ({"param": "nope"}, []),
        ({"group": "planner_policy"}, ["a", "b"]),
        ({"writable_only": True}, ["a", "b", "c"]),
        ({"manual_only": True}, ["a"]),
        ({"group": "planner_policy", "manual_only": True}, ["a"]),
        ({"group": "read_only", "writable_only": True}, []),
    ],
    ids=["all", "param", "unknown-param", "group", "writable", "manual", "group-manual", "none"],
)
def test_settings_filters_the_api_09_rows_in_the_adapter(server, catalog, opened, arguments, selected):
    assert rows_of(server, **arguments) == selected


def test_settings_returns_the_rest_json_of_every_row(server, catalog, opened):
    result = asyncio.run(server.settings(param="a"))
    assert result.isError is False
    assert body(result)["server_time"] == "2026-11-02T18:00:00Z"
    ((row),) = body(result)["items"]
    assert row == catalog.items[0].model_dump(mode="json")
    assert (row["applied_from"], row["min"], row["max"], row["step"]) == ("manual", 0.5, 2.5, 0.05)


def test_settings_with_a_whole_window_is_api_12_with_from_and_to(server, opened, monkeypatch):
    calls = []

    async def setting_history(conn, param, from_, to):
        calls.append((param, from_, to))
        return SettingHistory(
            param=param, from_=from_, to=to, desired=[desired()], readback=[], plan=[], unit="kPa", min=0.5, max=2.5
        )

    monkeypatch.setattr(server.reads_service, "setting_history", setting_history)
    since, until = "2026-11-01T00:00:00-07:00", "2026-11-02T00:00:00Z"
    result = asyncio.run(server.settings(param=PARAM, since=since, until=until, group="crop_band", manual_only=True))
    assert calls == [(PARAM, datetime.fromisoformat(since), datetime(2026, 11, 2, tzinfo=UTC))]
    answer = body(result)
    assert answer["from"] == "2026-11-01T00:00:00-07:00" and answer["to"] == "2026-11-02T00:00:00Z"
    assert "from_" not in answer and answer["desired"][0]["param"] == PARAM


@pytest.mark.parametrize(
    "arguments",
    [
        {"since": "2026-11-01T00:00:00Z"},
        {"until": "2026-11-01T00:00:00Z"},
        {"param": PARAM, "since": "2026-11-01T00:00:00Z"},
        {"param": PARAM, "until": "2026-11-01T00:00:00Z"},
        {"since": "2026-11-01T00:00:00Z", "until": "2026-11-02T00:00:00Z"},
    ],
    ids=["since", "until", "param-since", "param-until", "no-param"],
)
def test_a_partial_window_is_invalid_request_before_the_database(server, opened, arguments):
    result = asyncio.run(server.settings(**arguments))
    assert result.isError is True and body(result)["code"] == "invalid_request"
    assert opened == []


@pytest.mark.parametrize(
    ("since", "field"),
    [("yesterday", "since"), ("2026-11-01T00:00:00", "since"), ("", "since")],
    ids=["words", "no-offset", "empty"],
)
def test_a_bad_window_time_is_invalid_request_naming_the_argument(server, opened, since, field):
    result = asyncio.run(server.settings(param=PARAM, since=since, until="2026-11-02T00:00:00Z"))
    assert result.isError is True
    assert (body(result)["code"], body(result)["field"]) == ("invalid_request", field)
    assert opened == []


def test_a_history_refusal_from_the_service_is_problem_json(server, opened, monkeypatch):
    async def setting_history(conn, param, from_, to):
        raise server.ServiceError(problem("not_found", f"{param} is not a setting", field="param"))

    monkeypatch.setattr(server.reads_service, "setting_history", setting_history)
    result = asyncio.run(server.settings(param="nope", since="2026-11-01T00:00:00Z", until="2026-11-02T00:00:00Z"))
    assert result.isError is True and body(result)["code"] == "not_found"


# ── activity ────────────────────────────────────────────────────────────────


def test_activity_passes_the_filters_and_maps_since_to_from(server, opened, monkeypatch):
    calls = []
    entry = ActivityEntry(
        id=9,
        ts=CREATED,
        actor=ActorRef(source="agent:steward", name="steward"),
        action="setting.write",
        target=PARAM,
        detail={"manual": {"before": True, "after": False}},
        state="requested",
    )

    async def activity_page(conn, **filters):
        calls.append(filters)
        return ActivityPage(items=[entry], next_cursor="next")

    monkeypatch.setattr(server.reads_service, "activity_page", activity_page)
    result = asyncio.run(
        server.activity(
            source="agent",
            actor="steward",
            action="setting.",
            target=PARAM,
            since="2026-11-02T00:00:00+00:00",
            limit=10,
            cursor="abc",
        )
    )
    assert calls == [
        {
            "source": "agent",
            "actor": "steward",
            "action": "setting.",
            "target": PARAM,
            "from_": datetime(2026, 11, 2, tzinfo=UTC),
            "limit": 10,
            "cursor": "abc",
        }
    ]
    answer = body(result)
    assert answer["next_cursor"] == "next"
    assert answer["items"][0]["state"] == "requested"
    assert answer["items"][0]["actor"] == {"source": "agent:steward", "name": "steward"}
    assert answer["items"][0]["detail"]["manual"] == {"before": True, "after": False}


def test_activity_defaults_to_the_first_50_rows_of_everything(server, opened, monkeypatch):
    calls = []

    async def activity_page(conn, **filters):
        calls.append(filters)
        return ActivityPage(items=[])

    monkeypatch.setattr(server.reads_service, "activity_page", activity_page)
    asyncio.run(server.activity())
    assert calls == [
        {
            "source": None,
            "actor": None,
            "action": None,
            "target": None,
            "from_": None,
            "limit": 50,
            "cursor": None,
        }
    ]


def test_activity_refusals_are_problem_json(server, opened, monkeypatch):
    async def activity_page(conn, **filters):
        raise server.ServiceError(problem("invalid_request", "limit must be 1-200", field="limit", min=1, max=200))

    monkeypatch.setattr(server.reads_service, "activity_page", activity_page)
    result = asyncio.run(server.activity(limit=500))
    assert result.isError is True and body(result)["field"] == "limit"
    bad_since = asyncio.run(server.activity(since="not a time"))
    assert (bad_since.isError, body(bad_since)["field"]) == (True, "since")


# ── Schemas and descriptions (architecture §5.6, US-AGT-08) ─────────────────

UUID_LITERAL = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


@pytest.mark.parametrize(
    ("tool", "expected"),
    [
        (
            "set_setting",
            {
                "param": inspect.Parameter.empty,
                "value": None,
                "release": False,
                "manual": False,
                "reason": None,
                "request_id": None,
                "trigger_id": None,
                "planner_instance": None,
            },
        ),
        (
            "settings",
            {
                "param": None,
                "group": None,
                "writable_only": False,
                "manual_only": False,
                "since": None,
                "until": None,
            },
        ),
        (
            "activity",
            {
                "source": None,
                "actor": None,
                "action": None,
                "target": None,
                "since": None,
                "limit": 50,
                "cursor": None,
            },
        ),
    ],
    ids=["set_setting", "settings", "activity"],
)
def test_the_signature_is_the_design_and_has_no_return_annotation(server, tool, expected):
    signature = inspect.signature(getattr(server, tool))
    assert {name: parameter.default for name, parameter in signature.parameters.items()} == expected
    assert signature.return_annotation is inspect.Signature.empty
    for name in signature.parameters:
        assert name not in {"data", "payload", "json", "from"}, "no free-form JSON argument and no keyword"


def test_group_is_a_literal_of_the_setting_group_values(server):
    annotation = get_type_hints(inspect.unwrap(server.settings), include_extras=True)["group"]
    choices = next(arg for arg in get_args(get_args(annotation)[0]) if get_origin(arg) is Literal)
    assert set(get_args(choices)) == set(ALL_GROUPS) == set(get_args(SettingGroup))


def test_every_parameter_says_what_it_is(server):
    for tool in ("set_setting", "settings", "activity"):
        hints = get_type_hints(inspect.unwrap(getattr(server, tool)), include_extras=True)
        for name, annotation in hints.items():
            metadata = getattr(annotation, "__metadata__", ())
            assert any(getattr(item, "description", None) for item in metadata), (tool, name)


def test_the_set_setting_description_names_its_twins_the_default_and_the_codes(server):
    description = " ".join(server.set_setting.__doc__.split())
    for text in (
        "API-10",
        "PUT /v1/settings/{param}",
        "API-11",
        "DELETE /v1/settings/{param}",
        "manual defaults to false here; a write stores the flag it carries, "
        "so writing a manual setting without manual=true clears it",
        "it does not count as your plan for a trigger",
        "replayed: true",
    ):
        assert text in description, text
    for code in ("unknown_target", "read_only", "out_of_range", "manual", "request_id_reused", "forbidden"):
        assert f"`{code}`" in description, code


@pytest.mark.parametrize(
    ("tool", "texts"),
    [
        ("settings", ("API-09", "GET /v1/settings", "API-12", "/v1/settings/{param}/history", "applied_from", "from")),
        ("activity", ("API-17", "GET /v1/activity", "ActivityPage", "readback state", "from")),
    ],
)
def test_the_read_descriptions_name_their_twins(server, tool, texts):
    description = " ".join(getattr(server, tool).__doc__.split())
    for text in texts:
        assert text in description, text


@pytest.mark.parametrize("tool", ["set_setting", "settings", "activity"])
def test_a_description_never_carries_a_literal_uuid(server, tool):
    assert UUID_LITERAL.search(getattr(server, tool).__doc__) is None


def test_the_server_instructions_gain_the_sentence(server):
    instructions = " ".join(server.SERVER_INSTRUCTIONS.split())
    assert (
        "`set_setting` changes any writable setting within registry min/max and stores the `manual` flag it carries "
        "(default false); `settings` and `activity` read the catalog, history and every write; Iris's `set_setting` "
        "on a manual setting is refused with `manual`." in instructions
    )
    assert "instructions=SERVER_INSTRUCTIONS" in (REPO_ROOT / "mcp" / "server.py").read_text()
    admin = server.audience_allowlist("admin")
    named = set(re.findall(r"`([a-z_]+)`", instructions)) - {"manual"}
    assert named <= admin


# ── alerts: an adapter over verdify_services.alerts ─────────────────────────


@pytest.fixture()
def alert_rows(server, monkeypatch):
    db = FakeAlertDB()
    db.close = _async_noop
    db.fetchrow = _with_dependency_check(db.fetchrow, db)

    async def get_db():
        return db

    monkeypatch.setattr(server, "_db", get_db)
    return db


async def _async_noop():
    return None


def _with_dependency_check(fetchrow, db):
    async def wrapped(sql, *args):
        if sql.startswith("SELECT alert_type, source FROM alert_log"):
            found = [row for row in db.rows if row["id"] == args[0]]
            return {"alert_type": found[0]["alert_type"], "source": "system"} if found else None
        return await fetchrow(sql, *args)

    return wrapped


def test_list_returns_merged_rows_in_the_row_shape_with_their_count(server, alert_rows):
    repeats = [
        alert_rows.add("relay_stuck", sensor_id="mister_south", ts=ALERTS_NOW - timedelta(hours=h)) for h in (3, 2, 1)
    ]
    acknowledged = alert_rows.add("sensor_offline", sensor_id="rh_south", disposition="acknowledged", ts=ALERTS_NOW)
    resolved = alert_rows.add(
        "vpd_high", disposition="resolved", resolved_at=ALERTS_NOW - timedelta(hours=1), ts=ALERTS_NOW
    )
    rows = {row["alert_type"]: row for row in json.loads(asyncio.run(server.alerts("list", 0, '{"hours": 1}')))}
    assert set(rows) == {"relay_stuck", "sensor_offline", "vpd_high"}
    assert rows["relay_stuck"] == {
        "id": repeats[0]["id"],
        # 17:00Z, the newest repeat's time, in America/Denver (MST on 2026-11-02).
        "time": "11-02 10:00",
        "alert_type": "relay_stuck",
        "severity": "warning",
        "message": repeats[-1]["message"],
        "disposition": "open",
        "acknowledged": False,
        "resolved": False,
        "count": 3,
    }
    assert rows["sensor_offline"]["id"] == acknowledged["id"]
    assert (rows["sensor_offline"]["disposition"], rows["sensor_offline"]["acknowledged"]) == ("acknowledged", True)
    assert rows["vpd_high"]["id"] == resolved["id"]
    assert (rows["vpd_high"]["disposition"], rows["vpd_high"]["resolved"]) == ("resolved", True)


def test_the_unacknowledged_rows_equal_the_one_open_count(server, alert_rows):
    alert_rows.add("relay_stuck", sensor_id="a")
    alert_rows.add("relay_stuck", sensor_id="a")
    alert_rows.add("relay_stuck", sensor_id="b")
    alert_rows.add("sensor_offline", disposition="acknowledged")
    alert_rows.add("vpd_high", disposition="resolved", resolved_at=ALERTS_NOW - timedelta(hours=1))
    # An acknowledged repeat beside an open one: the merged row is still open.
    alert_rows.add("leak", ts=ALERTS_NOW - timedelta(hours=2))
    alert_rows.add("leak", ts=ALERTS_NOW - timedelta(hours=1), disposition="acknowledged")
    rows = json.loads(asyncio.run(server.alerts("list")))
    unacknowledged = [row for row in rows if not row["acknowledged"]]
    assert len(unacknowledged) == asyncio.run(alerts_service.open_count(alert_rows)).open == 3


def test_list_reads_every_page(server, alert_rows):
    for index in range(alerts_service.LIMIT_MAX + 5):
        alert_rows.add(f"alert_{index}")
    rows = json.loads(asyncio.run(server.alerts("list")))
    assert len(rows) == alerts_service.LIMIT_MAX + 5
    assert len({row["alert_type"] for row in rows}) == len(rows)


def test_an_empty_list_is_an_empty_array(server, alert_rows):
    assert json.loads(asyncio.run(server.alerts("list"))) == []


def test_acknowledge_acts_on_every_repeat_and_keeps_todays_reply(server, alert_rows):
    first = alert_rows.add("relay_stuck", sensor_id="a")
    second = alert_rows.add("relay_stuck", sensor_id="a")
    reply = run_as(server, steward(server), server.alerts("acknowledge", first["id"]))
    assert json.loads(reply) == {"ok": True, "alert_id": first["id"], "action": "acknowledged"}
    assert first["acknowledged_by"] == second["acknowledged_by"] == "agent:steward"
    assert [row["action"] for row in alert_rows.activity] == ["alert.ack"]


def test_acknowledging_a_resolved_row_is_already_resolved_and_acknowledges_nothing(server, alert_rows):
    resolved = alert_rows.add("relay_stuck", sensor_id="a", disposition="resolved", resolved_at=ALERTS_NOW)
    newer = alert_rows.add("relay_stuck", sensor_id="a")
    reply = run_as(server, steward(server), server.alerts("acknowledge", resolved["id"]))
    assert json.loads(reply) == {"ok": True, "alert_id": resolved["id"], "action": "already_resolved"}
    assert resolved["acknowledged_at"] is None
    assert (newer["disposition"], newer["acknowledged_at"]) == ("open", None)


@pytest.mark.parametrize("action", ["acknowledge", "resolve"])
@pytest.mark.parametrize("alert_id", [404, 2**31, -7], ids=["unknown", "beyond-integer", "negative"])
def test_an_unknown_id_is_todays_not_found_reply(server, alert_rows, action, alert_id):
    reply = run_as(server, steward(server), server.alerts(action, alert_id))
    assert json.loads(reply) == {"error": f"alert_id {alert_id} not found"}
    assert alert_rows.activity == []


@pytest.mark.parametrize(
    ("data", "resolution"),
    [
        (json.dumps({"resolution": "fan fixed", "resolved_by": "jason"}), "fan fixed"),
        ("fan fixed", "fan fixed"),
        ("123", "123"),
        ("", "Resolved by agent:steward"),
        (json.dumps({"other": 1}), "Resolved by agent:steward"),
    ],
    ids=["json", "plain-text", "json-number", "empty", "json-without-resolution"],
)
def test_resolve_takes_the_resolution_from_data_or_its_plain_text(server, alert_rows, data, resolution):
    first = alert_rows.add("relay_stuck", sensor_id="a")
    second = alert_rows.add("relay_stuck", sensor_id="a")
    reply = run_as(server, steward(server), server.alerts("resolve", first["id"], data))
    assert json.loads(reply) == {"ok": True, "alert_id": first["id"], "action": "resolved"}
    assert first["resolution"] == second["resolution"] == resolution
    assert first["resolved_by"] == "agent:steward"
    assert [row["action"] for row in alert_rows.activity] == ["alert.resolve"]


def test_a_resolution_postgresql_cannot_store_is_refused_and_resolves_nothing(server, alert_rows):
    row = alert_rows.add("relay_stuck", sensor_id="a")
    reply = json.loads(run_as(server, steward(server), server.alerts("resolve", row["id"], "a\x00b")))
    assert "NUL" in reply["error"]
    assert row["resolved_at"] is None and alert_rows.activity == []


def test_the_retrieval_check_runs_before_the_actor_check(server, monkeypatch):
    connection = AlertConnection(alert_type="planner_tool_dependency_failed", source="mcp")

    async def db():
        return connection

    monkeypatch.setattr(server, "_db", db)
    reply = json.loads(run_as(server, None, server.alerts("resolve", connection.row["id"], "planned recovery")))
    assert "successful same-tool" in reply["error"]
    assert connection.updates() == [] and connection.closed


def test_alerts_keeps_its_name_arguments_and_text_replies(server, alert_rows):
    assert list(inspect.signature(server.alerts).parameters) == ["action", "alert_id", "data"]
    reply = json.loads(asyncio.run(server.alerts("nope")))
    assert reply == {"error": "Unknown action 'nope'. Use: list, acknowledge, resolve"}


# ── set_tunable and set_plan: one activity row, the contract unchanged ──────


class Tx:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        self.conn.depth += 1
        return self

    async def __aexit__(self, *exc):
        self.conn.depth -= 1
        return False


class PlannerConnection:
    """The statements set_tunable and set_plan send, in order, each with the transaction depth it ran at."""

    def __init__(self, *, event_type="TRANSITION", expected_action="any"):
        self.event_type = event_type
        self.expected_action = expected_action
        self.statements: list[tuple[str, tuple, int]] = []
        self.depth = 0
        self.closed = False

    def transaction(self):
        return Tx(self)

    def activity_rows(self):
        return [(args, depth) for sql, args, depth in self.statements if "INTO public.activity_log" in sql]

    def plan_rows(self):
        return [(args, depth) for sql, args, depth in self.statements if "INSERT INTO setpoint_plan" in sql]

    async def fetch(self, sql, *args):
        self.statements.append((sql, args, self.depth))
        return []

    async def fetchrow(self, sql, *args):
        self.statements.append((sql, args, self.depth))
        if "FROM plan_delivery_log" in sql:
            return {
                "id": 1,
                "trigger_id": TRIGGER_ID,
                "event_type": self.event_type,
                "event_label": "fixture",
                "status": "pending",
                "instance": "local",
            }
        if "FROM planner_trigger_ledger" in sql:
            return {
                "id": 2,
                "trigger_id": TRIGGER_ID,
                "plan_delivery_log_id": 1,
                "event_type": self.event_type,
                "status": "delivered",
                "expected_action": self.expected_action,
            }
        return None

    async def fetchval(self, sql, *args):
        self.statements.append((sql, args, self.depth))
        if "SELECT now()" in sql:
            return datetime.now(UTC)
        if "SELECT 1 FROM plan_journal" in sql:
            return None
        if "RETURNING ts" in sql or "RETURNING created_at" in sql:
            return CREATED
        if "UPDATE plan_delivery_log" in sql:
            return 1
        if "UPDATE planner_trigger_ledger" in sql:
            return 2
        if "fn_mcp_armed_assignment_context" in sql:
            return None
        if "INTO public.activity_log" in sql:
            return 77
        raise AssertionError(f"unexpected statement: {sql}")

    async def execute(self, sql, *args):
        self.statements.append((sql, args, self.depth))

    async def close(self):
        self.closed = True


def activity_row(args) -> dict:
    greenhouse, source, actor, action, target, detail, reason, request_id, digest, experiment_id = args
    return {
        "source": source,
        "actor": actor,
        "action": action,
        "target": target,
        "detail": json.loads(detail),
        "reason": reason,
        "request_id": request_id,
        "experiment_id": experiment_id,
    }


@pytest.fixture()
def planner_conn(server, monkeypatch):
    for name in (
        "VERDIFY_POLICY_VECTOR_MODE",
        "VERDIFY_COMPONENT_EXPERIMENT_ENABLED",
        "VERDIFY_ACTIVE_EXPERIMENT_ID",
        "VERDIFY_LEGACY_DIRECT_POLICY_WRITES_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(server, "SITE_PUBLISH_TRIGGER_PATH", "")
    connection = PlannerConnection()

    async def db():
        return connection

    monkeypatch.setattr(server, "_db", db)
    return connection


TUNABLE_KEYS = {
    "ok",
    "parameter",
    "value",
    "reason",
    "plan_id",
    "trigger_id",
    "planner_instance",
    "delivery_status",
    "terminal_action",
    "note",
}


def call_set_tunable(server, actor, **overrides):
    arguments = {
        "parameter": "mister_vpd_weight",
        "value": 1.5,
        "reason": "fog is more effective",
        "trigger_id": TRIGGER_ID,
        "planner_instance": "local",
    }
    return json.loads(run_as(server, actor, server.set_tunable(**{**arguments, **overrides})))


def test_set_tunable_appends_one_plan_write_in_its_transaction_and_keeps_its_reply(server, planner_conn):
    reply = call_set_tunable(server, iris(server))
    assert set(reply) == TUNABLE_KEYS
    assert reply["plan_id"].startswith("iris-oneshot-") and reply["delivery_status"] == "action_completed"
    ((args, depth),) = planner_conn.activity_rows()
    row = activity_row(args)
    assert depth == 1 and planner_conn.plan_rows()[0][1] == 1
    assert row == {
        "source": "iris",
        "actor": "iris",
        "action": "plan.write",
        "target": reply["plan_id"],
        "detail": {
            "changes": [{"ts": "2026-11-02T18:00:00Z", "param": "mister_vpd_weight", "value": 1.5}],
            "trigger_id": TRIGGER_ID,
            "planner_instance": "local",
        },
        "reason": "fog is more effective",
        "request_id": None,
        "experiment_id": None,
    }


@pytest.mark.parametrize(
    ("actor", "source", "name"),
    [
        (None, "iris", "iris"),
        (("agent:admin", "admin"), "agent:admin", "admin"),
        (("iris", "iris"), "iris", "iris"),
    ],
    ids=["no-actor", "admin", "iris"],
)
def test_the_plan_row_actor_and_no_refusal_for_a_missing_one(server, planner_conn, actor, source, name):
    mcp_actor = None if actor is None else server.McpActor(source=actor[0], name=actor[1])
    reply = call_set_tunable(server, mcp_actor)
    assert reply["ok"] is True
    ((args, _depth),) = planner_conn.activity_rows()
    assert (activity_row(args)["source"], activity_row(args)["actor"]) == (source, name)


def test_the_plan_row_carries_the_armed_experiment(server, planner_conn, monkeypatch):
    armed = uuid.uuid4()
    original = planner_conn.fetchval

    async def fetchval(sql, *args):
        if "fn_mcp_armed_assignment_context" in sql:
            return armed
        return await original(sql, *args)

    monkeypatch.setattr(planner_conn, "fetchval", fetchval)
    call_set_tunable(server, iris(server))
    ((args, _depth),) = planner_conn.activity_rows()
    assert activity_row(args)["experiment_id"] == armed


def test_a_refused_set_tunable_appends_nothing(server, planner_conn):
    planner_conn.expected_action = "set_plan"
    planner_conn.event_type = "SUNRISE"
    reply = call_set_tunable(server, iris(server))
    assert reply["status"] == "wrong_action"
    assert planner_conn.activity_rows() == [] and planner_conn.plan_rows() == []

    out_of_bounds = call_set_tunable(server, iris(server), value=999.0)
    assert out_of_bounds["error"] == "Tunable value outside registry bounds"
    assert planner_conn.activity_rows() == []


def transitions(server) -> str:
    intent = server.ClimateIntent().model_dump(mode="json")
    start = datetime.now(UTC) - timedelta(minutes=1)
    return json.dumps(
        [
            {"ts": (start + timedelta(hours=6 * index)).isoformat(), "climate_intent": intent, "reason": "fixture"}
            for index in range(3)
        ]
    )


def call_set_plan(server, actor, **overrides):
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    arguments = {
        "plan_id": f"iris-{stamp}",
        "hypothesis": "fixture hypothesis",
        "transitions": transitions(server),
        "trigger_id": TRIGGER_ID,
        "planner_instance": "local",
    }
    return json.loads(run_as(server, actor, server.set_plan(**{**arguments, **overrides})))


def test_set_plan_appends_one_plan_write_listing_the_rows_it_wrote(server, planner_conn):
    reply = call_set_plan(server, iris(server))
    assert reply.get("ok") is True, reply
    assert "skipped_manual" not in reply
    ((args, depth),) = planner_conn.activity_rows()
    row = activity_row(args)
    assert depth == 1
    assert (row["source"], row["actor"], row["action"], row["target"]) == (
        "iris",
        "iris",
        "plan.write",
        reply["plan_id"],
    )
    written = planner_conn.plan_rows()
    assert reply["rows_written"] == len(written) == len(row["detail"]["changes"]) > 0
    assert {(change["param"], change["value"]) for change in row["detail"]["changes"]} == {
        (args[1], args[2]) for args, _depth in written
    }
    assert (row["detail"]["trigger_id"], row["detail"]["planner_instance"]) == (TRIGGER_ID, "local")


def test_a_refused_set_plan_appends_nothing(server, planner_conn):
    reply = call_set_plan(server, iris(server), transitions="[]")
    assert "error" in reply
    assert planner_conn.activity_rows() == [] and planner_conn.plan_rows() == []
