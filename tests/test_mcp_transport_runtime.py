"""Real MCP SDK 1.27+ protocol checks for transport auth and inventories."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
from pathlib import Path

import httpx
import pytest
from test_mcp_audience_auth import AGENT_TOOLS
from test_mcp_named_agents import AlertConnection

ROOT = Path(__file__).resolve().parents[1]


def _load_server():
    spec = importlib.util.spec_from_file_location("verdify_mcp_real_transport_test", ROOT / "mcp" / "server.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _protocol_payload(response: httpx.Response) -> dict:
    if response.headers.get("content-type", "").startswith("text/event-stream"):
        data = next(line.removeprefix("data: ") for line in response.text.splitlines() if line.startswith("data: "))
        return json.loads(data)
    return response.json()


def _request(method: str, request_id: int, params: dict | None = None) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["log", "enforce"])
async def test_real_mcp_127_stateless_protocol_filters_exact_audience_inventories(monkeypatch, mode):
    version = importlib.metadata.version("mcp")
    major, minor, *_rest = (int(part) for part in version.split(".") if part.isdigit())
    assert major == 1 and minor >= 27

    monkeypatch.setenv("VERDIFY_MCP_AUTH_MODE", mode)
    tokens = {
        "iris": "runtime-test-iris",
        "experiment": "runtime-test-experiment",
        "admin": "runtime-test-admin",
        "observer": "runtime-test-observer",
    }
    for audience, token in tokens.items():
        monkeypatch.setenv(f"VERDIFY_MCP_TOKEN_{audience.upper()}", token)

    server = _load_server()
    app = server.mcp.streamable_http_app()
    transport = httpx.ASGITransport(app=app)
    common_headers = {
        "accept": "application/json, text/event-stream",
        "content-type": "application/json",
    }
    initialize = _request(
        "initialize",
        1,
        {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "verdify-runtime-test", "version": "1"},
        },
    )

    async with app.app.router.lifespan_context(app.app):
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
            if mode == "enforce":
                denied_initialize = await client.post("/mcp", headers=common_headers, json=initialize)
                denied_list = await client.post(
                    "/mcp",
                    headers={**common_headers, "mcp-protocol-version": "2025-11-25"},
                    json=_request("tools/list", 2),
                )
                assert denied_initialize.status_code == denied_list.status_code == 401
                assert "tools" not in denied_initialize.text
                assert "tools" not in denied_list.text
            else:
                unauthenticated_list = await client.post(
                    "/mcp",
                    headers={**common_headers, "mcp-protocol-version": "2025-11-25"},
                    json=_request("tools/list", 2),
                )
                assert unauthenticated_list.status_code == 200
                assert _protocol_payload(unauthenticated_list)["result"]["tools"] == []

            expected_counts = {"iris": 33, "experiment": 8, "admin": 51, "observer": 0}
            for request_id, (audience, token) in enumerate(tokens.items(), start=10):
                auth_headers = {**common_headers, "authorization": f"Bearer {token}"}
                initialized = await client.post("/mcp", headers=auth_headers, json=initialize)
                listed = await client.post(
                    "/mcp",
                    headers={**auth_headers, "mcp-protocol-version": "2025-11-25"},
                    json=_request("tools/list", request_id),
                )

                assert initialized.status_code == listed.status_code == 200
                assert initialized.headers.get("mcp-session-id") is None
                assert listed.headers.get("mcp-session-id") is None
                names = {tool["name"] for tool in _protocol_payload(listed)["result"]["tools"]}
                assert names == server.audience_allowlist(audience)
                assert len(names) == expected_counts[audience]
                reads = {"irrigation_status", "irrigation_schedule", "irrigation_history"}
                assert reads <= names if audience in ("iris", "admin") else not reads & names
                band = {"band_profile", "band_profile_preview"}
                assert band <= names if audience == "admin" else not band & names

            if mode == "enforce":
                iris_query = await client.post(
                    "/mcp",
                    headers={
                        **common_headers,
                        "authorization": f"Bearer {tokens['iris']}",
                        "mcp-protocol-version": "2025-11-25",
                    },
                    json=_request("tools/call", 30, {"name": "query", "arguments": {"sql": "SELECT 1"}}),
                )
                query_result = _protocol_payload(iris_query)["result"]
                assert query_result["isError"] is True
                assert "iris" in query_result["content"][0]["text"]
                assert tokens["iris"] not in iris_query.text
                observer_call = await client.post(
                    "/mcp",
                    headers={
                        **common_headers,
                        "authorization": f"Bearer {tokens['observer']}",
                        "mcp-protocol-version": "2025-11-25",
                    },
                    json=_request("tools/call", 31, {"name": "query", "arguments": {"sql": "SELECT 1"}}),
                )
                observer_result = _protocol_payload(observer_call)["result"]
                assert observer_result["isError"] is True
                assert "unauthorized" in observer_result["content"][0]["text"]
                assert "observer" in observer_result["content"][0]["text"]
                assert tokens["observer"] not in observer_call.text


@pytest.mark.asyncio
async def test_real_mcp_127_duplicate_tokens_fail_before_protocol_dispatch(monkeypatch):
    monkeypatch.setenv("VERDIFY_MCP_AUTH_MODE", "enforce")
    monkeypatch.setenv("VERDIFY_MCP_TOKEN_IRIS", "runtime-test-shared")
    monkeypatch.setenv("VERDIFY_MCP_TOKEN_ADMIN", "runtime-test-shared")
    server = _load_server()
    app = server.mcp.streamable_http_app()
    transport = httpx.ASGITransport(app=app)
    headers = {
        "accept": "application/json, text/event-stream",
        "content-type": "application/json",
        "authorization": "Bearer runtime-test-shared",
    }

    async with app.app.router.lifespan_context(app.app):
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
            response = await client.post(
                "/mcp",
                headers=headers,
                json=_request(
                    "initialize",
                    1,
                    {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "clientInfo": {"name": "verdify-runtime-test", "version": "1"},
                    },
                ),
            )

    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}
    assert "runtime-test-shared" not in response.text


@pytest.mark.asyncio
async def test_real_mcp_named_agent_inventory_attribution_and_revocation(monkeypatch, tmp_path):
    """VD-59 through the real SDK: an agent key lists exactly the agent tools, its
    alerts acknowledgement is attributed from the request actor, and a removed key
    is refused at the transport after the 60 s cache while Iris keeps working."""
    import os

    for key in [k for k in os.environ if k.startswith("VERDIFY_MCP_TOKEN_")]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("VERDIFY_MCP_AUTH_MODE", "enforce")
    monkeypatch.setenv("VERDIFY_MCP_TOKEN_IRIS", "runtime-test-iris")
    keys = tmp_path / "mcp-agents"
    keys.mkdir()
    (keys / "steward").write_text("runtime-test-steward\n")
    monkeypatch.setenv("VERDIFY_MCP_AGENT_KEYS_DIR", str(keys))

    server = _load_server()
    now = [1000.0]
    monkeypatch.setattr(server, "_agent_keys_clock", lambda: now[0])

    alert_db = AlertConnection()

    async def db():
        return alert_db

    monkeypatch.setattr(server, "_db", db)
    app = server.mcp.streamable_http_app()
    transport = httpx.ASGITransport(app=app)
    common_headers = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
    initialize = _request(
        "initialize",
        1,
        {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "verdify-runtime-test", "version": "1"},
        },
    )

    def headers(token):
        return {**common_headers, "authorization": f"Bearer {token}", "mcp-protocol-version": "2025-11-25"}

    async with app.app.router.lifespan_context(app.app):
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
            assert (
                await client.post("/mcp", headers=headers("runtime-test-steward"), json=initialize)
            ).status_code == 200
            listed = await client.post("/mcp", headers=headers("runtime-test-steward"), json=_request("tools/list", 2))
            names = {tool["name"] for tool in _protocol_payload(listed)["result"]["tools"]}
            assert names == server.audience_allowlist("agent")
            # Within the architecture §5.6 agent column; later tools join by registering with `agent`.
            assert {"climate", "alerts"} <= names <= AGENT_TOOLS
            assert {"irrigation_status", "irrigation_schedule", "irrigation_history"} <= names

            acknowledged = await client.post(
                "/mcp",
                headers=headers("runtime-test-steward"),
                json=_request(
                    "tools/call",
                    3,
                    {
                        "name": "alerts",
                        "arguments": {
                            "action": "acknowledge",
                            "alert_id": alert_db.row["id"],
                            "data": '{"acknowledged_by": "jason"}',
                        },
                    },
                ),
            )
            result = _protocol_payload(acknowledged)["result"]
            assert result["isError"] is False
            assert json.loads(result["content"][0]["text"])["action"] == "acknowledged"
            assert alert_db.row["acknowledged_by"] == "agent:steward"
            assert "runtime-test-steward" not in acknowledged.text

            (keys / "steward").unlink()
            now[0] += 60
            revoked = await client.post("/mcp", headers=headers("runtime-test-steward"), json=initialize)
            assert revoked.status_code == 401
            assert (await client.post("/mcp", headers=headers("runtime-test-iris"), json=initialize)).status_code == 200


@pytest.mark.asyncio
async def test_real_mcp_irrigation_reads_are_typed_described_and_return_the_rest_or_problem_json(monkeypatch):
    """VD-60: through the real SDK, the tools' schemas carry their enums and bounds, and FastMCP passes the REST JSON
    and the Problem JSON with isError through unchanged."""
    from verdify_schemas.irrigation import LINE_IDS, bounds_by_kind
    from verdify_services.irrigation import reads

    monkeypatch.setenv("VERDIFY_MCP_AUTH_MODE", "enforce")
    monkeypatch.setenv("VERDIFY_MCP_TOKEN_IRIS", "runtime-test-iris")

    server = _load_server()

    class Connection:
        async def close(self):
            pass

    async def db():
        return Connection()

    async def agenda(_conn, _actor, days, past_days):
        return reads.ScheduleAgenda(
            schedules=[],
            occurrences=[],
            counts=reads.AgendaCounts(schedules=days + past_days, max_schedules=24),
            bounds=bounds_by_kind(),
            solar=[],
        )

    monkeypatch.setattr(server, "_db", db)
    monkeypatch.setattr(reads, "agenda", agenda)
    app = server.mcp.streamable_http_app()
    headers = {
        "accept": "application/json, text/event-stream",
        "content-type": "application/json",
        "authorization": "Bearer runtime-test-iris",
        "mcp-protocol-version": "2025-11-25",
    }

    async def call(client, name, arguments, request_id):
        response = await client.post(
            "/mcp", headers=headers, json=_request("tools/call", request_id, {"name": name, "arguments": arguments})
        )
        return _protocol_payload(response)["result"]

    async with app.app.router.lifespan_context(app.app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000"
        ) as client:
            listed = await client.post("/mcp", headers=headers, json=_request("tools/list", 1))
            tools = {tool["name"]: tool for tool in _protocol_payload(listed)["result"]["tools"]}
            assert "API-IRR-01" in tools["irrigation_status"]["description"]
            assert tools["irrigation_status"]["inputSchema"]["properties"] == {}
            schedule = tools["irrigation_schedule"]["inputSchema"]["properties"]
            assert (schedule["days"]["minimum"], schedule["days"]["maximum"], schedule["days"]["default"]) == (1, 7, 7)
            assert (schedule["past_days"]["minimum"], schedule["past_days"]["maximum"]) == (0, 7)
            history = tools["irrigation_history"]["inputSchema"]
            assert history["required"] == ["view", "since", "until"]
            assert history["properties"]["view"]["enum"] == ["minutes", "runs"]
            assert history["properties"]["group_by"]["enum"] == ["zone", "line", "cause"]
            line_enum = next(item for item in history["properties"]["line"]["anyOf"] if "enum" in item)["enum"]
            assert tuple(line_enum) == LINE_IDS

            ok = await call(client, "irrigation_schedule", {"days": 2, "past_days": 1}, 2)
            assert ok["isError"] is False
            body = json.loads(ok["content"][0]["text"])
            assert body["counts"] == {"schedules": 3, "max_schedules": 24}
            assert set(body["bounds"]) == {"drip", "fert_drip", "mister", "fert_mister"}

            refused = await call(
                client,
                "irrigation_history",
                {"view": "minutes", "since": "2026-10-01", "until": "2026-10-02", "outcome": "ok"},
                3,
            )
            assert refused["isError"] is True
            problem = json.loads(refused["content"][0]["text"])
            assert (problem["code"], problem["field"], problem["status"]) == ("invalid_request", "outcome", 400)

            out_of_range = await call(client, "irrigation_schedule", {"days": 9}, 4)
            assert out_of_range["isError"] is True
            bad_enum = await call(client, "irrigation_history", {"view": "days", "since": "a", "until": "b"}, 5)
            assert bad_enum["isError"] is True


@pytest.mark.asyncio
async def test_real_mcp_irrigation_writes_are_typed_and_reach_the_services_as_the_request_actor(monkeypatch):
    """VD-61: through the real SDK, the five tools' schemas carry their enums and bounds and no check argument; a nested
    start, a date-time and a fertilizer mister's fields reach the services; a value outside the schema is refused before
    the tool runs; and FastMCP passes the stored JSON and the Problem JSON with isError through unchanged."""
    from verdify_schemas.irrigation import LINE_IDS, Command
    from verdify_services import activity
    from verdify_services.irrigation import writes
    from verdify_services.problems import ServiceError, problem

    monkeypatch.setenv("VERDIFY_MCP_AUTH_MODE", "enforce")
    monkeypatch.setenv("VERDIFY_MCP_TOKEN_IRIS", "runtime-test-iris")

    server = _load_server()
    calls = []
    command = Command.model_validate(
        {
            "command_id": "8f0d6f8e-5c4e-4b52-9a51-2b0a6c4b8f10",
            "kind": "run",
            "line_id": "L08",
            "args": {},
            "status": "pending",
            "actor": {"source": "iris", "name": "iris"},
            "reason": None,
            "created_at": "2026-11-02T18:00:00Z",
            "sent_at": None,
        }
    )

    class Connection:
        async def close(self):
            pass

    async def db():
        return Connection()

    def service(name, value, replayed=False):
        async def call(_conn, actor, *args, **kwargs):
            calls.append((name, actor.source, args, kwargs))
            return activity.WriteResult(value=value, replayed=replayed)

        return call

    async def refused_create(_conn, _actor, *_args, **_kwargs):
        raise ServiceError(problem("manual", "Schedule 7 is manual, so Iris leaves it alone", field="slot_id"))

    monkeypatch.setattr(server, "_db", db)
    monkeypatch.setattr(writes, "run", service("run", command, replayed=True))
    monkeypatch.setattr(writes, "stop", service("stop", command))
    monkeypatch.setattr(writes, "clear_fault", service("clear_fault", command))
    monkeypatch.setattr(writes, "schedule_create", service("schedule_create", command))
    monkeypatch.setattr(writes, "schedule_update", refused_create)
    monkeypatch.setattr(writes, "schedule_delete", service("schedule_delete", writes.ScheduleDeleted(slot_id=7)))
    monkeypatch.setattr(writes, "line_update", service("line_update", writes.LinesUpdated(lines=[])))
    app = server.mcp.streamable_http_app()
    headers = {
        "accept": "application/json, text/event-stream",
        "content-type": "application/json",
        "authorization": "Bearer runtime-test-iris",
        "mcp-protocol-version": "2025-11-25",
    }

    async def call(client, name, arguments, request_id):
        response = await client.post(
            "/mcp", headers=headers, json=_request("tools/call", request_id, {"name": name, "arguments": arguments})
        )
        return _protocol_payload(response)["result"]

    def optional(schema, key="type", value="integer"):
        return next(item for item in schema["anyOf"] if item.get(key) == value)

    async with app.app.router.lifespan_context(app.app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000"
        ) as client:
            listed = await client.post("/mcp", headers=headers, json=_request("tools/list", 1))
            tools = {tool["name"]: tool for tool in _protocol_payload(listed)["result"]["tools"]}
            for name in (
                "irrigation_schedule_set",
                "irrigation_line_set",
                "irrigation_run",
                "irrigation_stop",
                "irrigation_clear_fault",
            ):
                assert tools[name]["description"].startswith(
                    "Changes watering only; it does not count as your plan for this trigger."
                )
                properties = tools[name]["inputSchema"]["properties"]
                assert not {"checked", "check_note", "actor", "source"} & set(properties)
            run_schema = tools["irrigation_run"]["inputSchema"]
            # #1336: line_id or circuit_id and variant name the run, so neither is required by the schema.
            assert "required" not in run_schema
            assert tuple(optional(run_schema["properties"]["line_id"], "type", "string")["enum"]) == LINE_IDS
            assert optional(run_schema["properties"]["variant"], "type", "string")["enum"] == ["clean", "fertilized"]
            on_s = optional(run_schema["properties"]["on_s"])
            assert (on_s["minimum"], on_s["maximum"]) == (10, 90)
            schedule = tools["irrigation_schedule_set"]["inputSchema"]
            assert "required" not in schedule
            days = optional(schedule["properties"]["days"], value="array")
            assert days["minItems"] == 1 and days["items"]["enum"] == ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
            start = schedule["$defs"]["ScheduleStart"]
            assert start["properties"]["kind"]["enum"] == ["clock", "sunrise", "solar_noon", "sunset"]
            assert optional(start["properties"]["offset_min"])["minimum"] == -240
            assert "L03" not in optional(schedule["properties"]["line_id"], "type", "string")["enum"]
            line = tools["irrigation_line_set"]["inputSchema"]
            assert line["required"] == ["line_id"]
            assert tuple(line["properties"]["line_id"]["enum"]) == (*LINE_IDS, "all")
            paused = optional(line["properties"]["paused_until"], "type", "string")
            assert paused["format"] == "date-time"
            clear = tools["irrigation_clear_fault"]["inputSchema"]["properties"]["line_id"]
            assert optional(clear, "type", "string")["enum"] == ["L04", "L07", "L09"]

            fields = {"on_s": 30, "count": 4, "gap_s": 60, "prewet_s": 120, "flush_s": 240}
            ran = await call(client, "irrigation_run", {"line_id": "L08", **fields}, 2)
            assert ran["isError"] is False
            assert json.loads(ran["content"][0]["text"])["replayed"] is True
            (name, source, args, kwargs) = calls.pop()
            assert (name, source, args) == ("run", "iris", ("L08",))
            assert {field: kwargs[field] for field in fields} == fields and kwargs["minutes"] is None

            stop = await call(client, "irrigation_stop", {}, 3)
            assert stop["isError"] is False and calls.pop()[:3] == ("stop", "iris", (None,))
            assert (await call(client, "irrigation_clear_fault", {"line_id": "L07"}, 4))["isError"] is False
            assert calls.pop()[2] == ("L07",)

            created = await call(
                client,
                "irrigation_schedule_set",
                {
                    "line_id": "L01",
                    "days": ["mon", "fri"],
                    "start": {"kind": "sunrise", "offset_min": 60},
                    "on_s": 720,
                },
                5,
            )
            assert created["isError"] is False
            (name, _source, (body,), kwargs) = calls.pop()
            assert name == "schedule_create" and kwargs["manual"] is False
            assert body == {
                "line_id": "L01",
                "days": ["mon", "fri"],
                "start": {"kind": "sunrise", "offset_min": 60},
                "on_s": 720,
            }

            refused = await call(client, "irrigation_schedule_set", {"slot_id": 7, "on_s": 720}, 6)
            assert refused["isError"] is True
            assert json.loads(refused["content"][0]["text"])["code"] == "manual"

            deleted = await call(client, "irrigation_schedule_set", {"slot_id": 7, "delete": True}, 7)
            assert json.loads(deleted["content"][0]["text"]) == {"slot_id": 7, "deleted": True, "manual_before": None}
            calls.clear()

            # A check argument from an old or careless client is dropped unread: the service never sees it.
            paused_line = await call(
                client,
                "irrigation_line_set",
                {"line_id": "all", "paused_until": "2026-11-03T18:00:00-07:00", "checked": True},
                8,
            )
            assert paused_line["isError"] is False
            assert json.loads(paused_line["content"][0]["text"]) == {"lines": []}
            (name, _source, (target, body), kwargs) = calls.pop()
            assert (name, target, kwargs["manual"]) == ("line_update", "all", None)
            assert set(body) == {"paused_until"} and body["paused_until"].utcoffset() is not None

            for tool, arguments in (
                ("irrigation_run", {"line_id": "L01", "minutes": 121}),
                ("irrigation_run", {"line_id": "L12"}),
                ("irrigation_schedule_set", {"line_id": "L03"}),
                ("irrigation_schedule_set", {"days": []}),
                ("irrigation_schedule_set", {"start": {"kind": "noon"}}),
                ("irrigation_schedule_set", {"start": {"kind": "clock", "clock": "25:00"}}),
                ("irrigation_schedule_set", {"start": {"kind": "sunrise", "offset_min": 241}}),
                ("irrigation_line_set", {"line_id": "L01", "daily_cap_min": 481}),
                ("irrigation_line_set", {"line_id": "L01", "paused_until": "2026-11-03T18:00:00"}),
                ("irrigation_clear_fault", {"line_id": "L08"}),
            ):
                result = await call(client, tool, arguments, 9)
                assert result["isError"] is True, (tool, arguments)
                assert calls == [], (tool, arguments)


@pytest.mark.asyncio
async def test_real_mcp_service_tools_list_typed_schemas_and_pass_their_results_through(monkeypatch, tmp_path):
    """VD-58 through the real SDK: no output schema, and FastMCP leaves the CallToolResult alone, so the REST JSON
    and a refusal's Problem JSON with isError reach the client unchanged, attributed from the request actor."""
    import os
    from datetime import UTC, datetime

    from verdify_services.activity import WriteResult
    from verdify_services.actor import ActorRef
    from verdify_services.settings import DesiredSetting

    for key in [k for k in os.environ if k.startswith("VERDIFY_MCP_TOKEN_")]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("VERDIFY_MCP_AUTH_MODE", "enforce")
    keys = tmp_path / "mcp-agents"
    keys.mkdir()
    (keys / "steward").write_text("runtime-test-steward\n")
    monkeypatch.setenv("VERDIFY_MCP_AGENT_KEYS_DIR", str(keys))
    server = _load_server()
    writes = []

    async def write(_conn, actor, param, value, manual, reason, request_id, extra_detail=None):
        writes.append((actor.source, param, value, manual))
        return WriteResult(
            value=DesiredSetting(
                id=1,
                param=param,
                value=1.55,
                snapped_from=value,
                manual=manual,
                manual_before=False,
                state="requested",
                actor=ActorRef(source=actor.source, name=actor.name),
                created_at=datetime(2026, 11, 2, 18, tzinfo=UTC),
            )
        )

    alert_db = AlertConnection()

    async def db():
        return alert_db

    monkeypatch.setattr(server, "_db", db)
    monkeypatch.setattr(server.settings_service, "write", write)
    app = server.mcp.streamable_http_app()
    transport = httpx.ASGITransport(app=app)
    headers = {
        "accept": "application/json, text/event-stream",
        "content-type": "application/json",
        "authorization": "Bearer runtime-test-steward",
        "mcp-protocol-version": "2025-11-25",
    }

    async def call(client, request_id, name, arguments):
        answered = await client.post(
            "/mcp", headers=headers, json=_request("tools/call", request_id, {"name": name, "arguments": arguments})
        )
        return _protocol_payload(answered)["result"]

    async with app.app.router.lifespan_context(app.app):
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as client:
            listed = await client.post("/mcp", headers=headers, json=_request("tools/list", 2))
            tools = {tool["name"]: tool for tool in _protocol_payload(listed)["result"]["tools"]}
            for name in ("set_setting", "settings", "activity"):
                assert "outputSchema" not in tools[name]
                assert tools[name]["description"]
            assert tools["set_setting"]["inputSchema"]["required"] == ["param"]
            assert {"since", "until", "group"} <= set(tools["settings"]["inputSchema"]["properties"])
            group = tools["settings"]["inputSchema"]["properties"]["group"]
            enum = next(option["enum"] for option in group["anyOf"] if "enum" in option)
            assert set(enum) == {
                "planner_policy",
                "scheduled_policy",
                "controller_safety",
                "crop_band",
                "site_water_caps",
                "read_only",
                "band_in_force",
            }

            stored = await call(client, 3, "set_setting", {"param": "mister_engage_kpa", "value": 1.53})
            assert stored["isError"] is False
            body = json.loads(stored["content"][0]["text"])
            assert (body["value"], body["snapped_from"], body["manual"], body["state"]) == (
                1.55,
                1.53,
                False,
                "requested",
            )
            assert body["actor"] == {"source": "agent:steward", "name": "steward"}
            assert writes == [("agent:steward", "mister_engage_kpa", 1.53, False)]

            refused = await call(client, 4, "set_setting", {"param": "mister_engage_kpa"})
            assert refused["isError"] is True
            assert json.loads(refused["content"][0]["text"])["code"] == "invalid_request"
            assert len(writes) == 1
