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

            expected_counts = {"iris": 26, "experiment": 8, "admin": 32, "observer": 0}
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
