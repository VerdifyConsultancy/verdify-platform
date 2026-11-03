"""Real MCP SDK 1.27+ protocol checks for transport auth and inventories."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
from pathlib import Path

import httpx
import pytest
from test_mcp_audience_auth import AGENT_TOOLS

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
                reads = {"irrigation_status", "irrigation_schedule", "irrigation_history"}
                assert reads <= names if audience in ("iris", "admin") else not reads & names

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

    class AlertConnection:
        updates: list[tuple] = []

        async def fetchrow(self, sql, *args):
            assert sql.startswith("UPDATE alert_log SET acknowledged_at")
            self.updates.append(args)
            return {"id": args[0], "disposition": "acknowledged"}

        async def close(self):
            pass

    async def db():
        return AlertConnection()

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
                        "arguments": {"action": "acknowledge", "alert_id": 7, "data": '{"acknowledged_by": "jason"}'},
                    },
                ),
            )
            result = _protocol_payload(acknowledged)["result"]
            assert result["isError"] is False
            assert json.loads(result["content"][0]["text"])["action"] == "acknowledged"
            assert AlertConnection.updates == [(7, "agent:steward")]
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
