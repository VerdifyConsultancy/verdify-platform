from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/mcp-security-acceptance.py"


def _module():
    spec = importlib.util.spec_from_file_location("mcp_security_acceptance_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_canonical_iris_inventory_is_bounded_and_excludes_admin_query() -> None:
    module = _module()
    tools = module.expected_iris_tools()
    assert len(tools) == 23
    assert "climate" in tools
    assert "query" not in tools


def test_generic_unauthorized_contract_rejects_inventory_and_sessions() -> None:
    module = _module()
    accepted = module.Response(
        401,
        {"content-type": "application/json", "www-authenticate": "Bearer"},
        b'{"error":"unauthorized"}',
    )
    module._assert_unauthorized(accepted, check="public:initialize")

    with pytest.raises(module.AcceptanceError, match="HTTP 503"):
        module._assert_unauthorized(module.Response(503, {}, b"no available server"), check="public:initialize")
    with pytest.raises(module.AcceptanceError, match="stateful MCP session"):
        module._assert_unauthorized(
            module.Response(401, {"www-authenticate": "Bearer", "mcp-session-id": "opaque"}, accepted.body),
            check="public:initialize",
        )
    with pytest.raises(module.AcceptanceError, match="non-generic denial body"):
        module._assert_unauthorized(
            module.Response(
                401,
                {"www-authenticate": "Bearer"},
                b'{"error":"unauthorized","tools":[{"name":"query"}]}',
            ),
            check="public:tools-list",
        )


def test_protocol_payload_accepts_json_and_stateless_sse() -> None:
    module = _module()
    payload = {"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}
    encoded = json.dumps(payload).encode()
    assert (
        module._protocol_payload(module.Response(200, {"content-type": "application/json"}, encoded), check="json")
        == payload
    )
    sse = b"event: message\ndata: " + encoded + b"\n\n"
    assert (
        module._protocol_payload(module.Response(200, {"content-type": "text/event-stream"}, sse), check="sse")
        == payload
    )


def test_endpoint_parser_forbids_credentials_and_accepts_labeled_direct_urls() -> None:
    module = _module()
    endpoint = module._endpoint("replica-a=http://127.0.0.1:18001/mcp")
    assert endpoint.label == "replica-a"
    assert endpoint.url.endswith("/mcp")
    with pytest.raises(Exception, match="without userinfo"):
        module._endpoint("unsafe=https://bearer@example.test/mcp")


def test_failures_never_include_the_presented_bearer(monkeypatch) -> None:
    module = _module()
    bearer = "runtime-test-sensitive-bearer"

    def fake_request(*_args, **_kwargs):
        return module.Response(503, {}, bearer.encode())

    monkeypatch.setattr(module, "_request", fake_request)
    with pytest.raises(module.AcceptanceError) as excinfo:
        module._authenticated_checks(
            module.Endpoint("replica-a", "http://127.0.0.1:18001/mcp"),
            bearer=bearer,
            expected_tools=module.expected_iris_tools(),
            safe_tool="climate",
            repeats=1,
            timeout=1,
        )
    assert bearer not in str(excinfo.value)


def test_authenticated_helper_checks_exact_inventory_admin_denial_and_safe_read(monkeypatch) -> None:
    module = _module()
    tools = module.expected_iris_tools()
    calls = []

    def fake_request(_url, *, method, payload=None, bearer=None, timeout):
        assert method == "POST"
        assert bearer == "test-iris-bearer"
        assert timeout == 1
        calls.append(payload["method"])
        if payload["method"] == "initialize":
            body = {"jsonrpc": "2.0", "id": payload["id"], "result": {"protocolVersion": module.PROTOCOL_VERSION}}
        elif payload["method"] == "tools/list":
            body = {
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {"tools": [{"name": name} for name in sorted(tools)]},
            }
        elif payload["params"]["name"] == "query":
            body = {"jsonrpc": "2.0", "id": payload["id"], "result": {"isError": True, "content": []}}
        else:
            body = {"jsonrpc": "2.0", "id": payload["id"], "result": {"isError": False, "content": []}}
        return module.Response(200, {"content-type": "application/json"}, json.dumps(body).encode())

    monkeypatch.setattr(module, "_request", fake_request)
    completed = module._authenticated_checks(
        module.Endpoint("replica-a", "http://127.0.0.1:18001/mcp"),
        bearer="test-iris-bearer",
        expected_tools=tools,
        safe_tool="climate",
        repeats=2,
        timeout=1,
    )
    assert completed == 8
    assert calls == ["initialize", "tools/list", "tools/call", "tools/call"] * 2


def test_unauthenticated_helper_repeats_missing_and_unknown_bearers(monkeypatch) -> None:
    module = _module()
    bearers = []

    def fake_request(_url, *, method, payload=None, bearer=None, timeout):
        assert method == "POST" and payload and timeout == 1
        bearers.append(bearer)
        return module.Response(
            401,
            {"content-type": "application/json", "www-authenticate": "Bearer"},
            b'{"error":"unauthorized"}',
        )

    monkeypatch.setattr(module, "_request", fake_request)
    completed = module._unauthenticated_checks(
        module.Endpoint("public", "https://mcp.example.test/mcp"), repeats=2, timeout=1
    )
    assert completed == 9
    assert bearers[:6] == [None] * 6
    assert len(set(bearers[6:])) == 1 and bearers[6] is not None


def test_cli_exposes_no_literal_token_argument() -> None:
    module = _module()
    destinations = {action.dest for action in module.build_parser()._actions}
    assert "token" not in destinations
    assert "token_env" in destinations
