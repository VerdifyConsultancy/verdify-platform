"""Secret-free per-dispatch attribution; no live tools, DB or device calls."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from test_mcp_audience_auth import mcp_server as mcp_server


def setup(monkeypatch, server):
    monkeypatch.setenv("VERDIFY_MCP_AUTH_MODE", "enforce")
    monkeypatch.setenv("VERDIFY_MCP_TOKEN_IRIS", "private-audit-fixture-token")
    monkeypatch.setattr(server, "_request_bearer_token", lambda _: "private-audit-fixture-token")
    request = SimpleNamespace(
        client=SimpleNamespace(host="10.42.1.2"),
        headers={
            "cf-connecting-ip": "203.0.113.99",
            "x-forwarded-for": "198.51.100.1",
            "authorization": "Bearer private-audit-fixture-token",
        },
    )
    monkeypatch.setattr(
        server.mcp, "_mcp_server", SimpleNamespace(request_context=SimpleNamespace(request=request)), raising=False
    )


def events(caplog):
    return [json.loads(r.getMessage()) for r in caplog.records if r.name == "verdify.mcp.audit"]


@pytest.mark.parametrize(
    "tool,capability",
    [("climate", "read"), ("set_plan", "write_capable"), ("crops", "write_capable"), ("alerts", "write_capable")],
)
def test_one_completed_event_without_arguments_credentials_or_forged_ip(
    mcp_server, monkeypatch, caplog, tool, capability
):
    setup(monkeypatch, mcp_server)
    caplog.set_level("INFO", logger="verdify.mcp.audit")
    asyncio.run(mcp_server.mcp.call_tool(tool, {"private_argument": "never-log-this"}))
    rows = events(caplog)
    assert len(rows) == 1
    row = rows[0]
    assert row["audience"] == "iris" and row["tool"] == tool
    assert row["capability"] == capability and row["result"] == "completed"
    assert row["peer_ip"] == "10.42.1.2" and row["client_ip_source"] == "transport_peer"
    assert row["duration_ms"] >= 0
    assert not any(
        value in json.dumps(row)
        for value in ["private-audit-fixture-token", "never-log-this", "203.0.113.99", "198.51.100.1"]
    )


@pytest.mark.parametrize(
    "kind,expected", [("exception", "exception"), ("cancelled", "cancelled"), ("tool_error", "tool_error")]
)
def test_result_is_recorded_once_without_exception_or_result_payload(mcp_server, monkeypatch, caplog, kind, expected):
    setup(monkeypatch, mcp_server)
    caplog.set_level("INFO", logger="verdify.mcp.audit")

    async def dispatched(*args):
        if kind == "exception":
            raise RuntimeError("private-exception-payload")
        if kind == "cancelled":
            raise asyncio.CancelledError()
        return SimpleNamespace(isError=True, content="private-result-payload")

    monkeypatch.setattr(mcp_server.FastMCP, "call_tool", dispatched)
    if kind == "tool_error":
        asyncio.run(mcp_server.mcp.call_tool("climate", {}))
    else:
        with pytest.raises(RuntimeError if kind == "exception" else asyncio.CancelledError):
            asyncio.run(mcp_server.mcp.call_tool("climate", {}))
    assert len(events(caplog)) == 1 and events(caplog)[0]["result"] == expected
    assert "private-exception-payload" not in caplog.text and "private-result-payload" not in caplog.text


def test_denied_dispatch_has_no_allowed_call_event(mcp_server, monkeypatch, caplog):
    setup(monkeypatch, mcp_server)
    monkeypatch.setattr(mcp_server, "_request_bearer_token", lambda _: "unknown-fixture")
    with pytest.raises(mcp_server.ToolAccessDenied):
        asyncio.run(mcp_server.mcp.call_tool("set_plan", {}))
    assert not events(caplog)


def test_absent_context_has_no_guessed_peer(mcp_server, monkeypatch):
    monkeypatch.setattr(mcp_server.mcp, "_mcp_server", None, raising=False)
    assert mcp_server._tool_transport_peer(mcp_server.mcp) is None
