"""Real MCP SDK 1.27+ protocol checks for transport auth and inventories."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
from pathlib import Path

import httpx
import pytest

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

            expected_counts = {"iris": 23, "experiment": 8, "admin": 27}
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
