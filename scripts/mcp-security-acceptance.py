#!/usr/bin/env python3
"""Fail-closed, metadata-only acceptance for Verdify's stateless MCP surface."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HERMES_CONFIG = REPO_ROOT / "deploy/k8s/components/hermes-iris/hermes-config.yaml"
PROTOCOL_VERSION = "2025-11-25"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class AcceptanceError(RuntimeError):
    """A safe acceptance failure that never contains a credential or response body."""


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict[str, str]
    body: bytes


@dataclass(frozen=True)
class Endpoint:
    label: str
    url: str


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, _req, _fp, _code, _msg, _headers, _newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _read_bounded(response) -> bytes:
    body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise AcceptanceError("response exceeded the bounded acceptance size")
    return body


def _request(
    url: str, *, method: str, payload: dict | None = None, bearer: str | None = None, timeout: float
) -> Response:
    headers = {"accept": "application/json, text/event-stream", "user-agent": "verdify-mcp-acceptance/1"}
    data = None
    if payload is not None:
        headers["content-type"] = "application/json"
        data = json.dumps(payload, separators=(",", ":")).encode()
        if payload.get("method") != "initialize":
            headers["mcp-protocol-version"] = PROTOCOL_VERSION
    if bearer is not None:
        headers["authorization"] = f"Bearer {bearer}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            return Response(
                response.status,
                {key.lower(): value for key, value in response.headers.items()},
                _read_bounded(response),
            )
    except urllib.error.HTTPError as exc:
        return Response(exc.code, {key.lower(): value for key, value in exc.headers.items()}, _read_bounded(exc))
    except (TimeoutError, urllib.error.URLError, OSError) as exc:
        raise AcceptanceError(f"request failed before an HTTP response: {type(exc).__name__}")


def _rpc(method: str, request_id: int, params: dict | None = None) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}


def _initialize(request_id: int) -> dict:
    return _rpc(
        "initialize",
        request_id,
        {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "verdify-security-acceptance", "version": "1"},
        },
    )


def _protocol_payload(response: Response, *, check: str) -> dict:
    try:
        if response.headers.get("content-type", "").lower().startswith("text/event-stream"):
            lines = response.body.decode().splitlines()
            encoded = next(line.removeprefix("data: ") for line in lines if line.startswith("data: "))
            payload = json.loads(encoded)
        else:
            payload = json.loads(response.body)
    except (StopIteration, UnicodeDecodeError, json.JSONDecodeError):
        raise AcceptanceError(f"{check} did not return one parseable protocol payload")
    if not isinstance(payload, dict):
        raise AcceptanceError(f"{check} returned a non-object protocol payload")
    return payload


def _assert_no_session(response: Response, *, check: str) -> None:
    if "mcp-session-id" in response.headers:
        raise AcceptanceError(f"{check} exposed a stateful MCP session identifier")


def _assert_unauthorized(response: Response, *, check: str) -> None:
    _assert_no_session(response, check=check)
    if response.status != 401:
        raise AcceptanceError(f"{check} returned HTTP {response.status}, expected generic 401")
    if response.headers.get("www-authenticate") != "Bearer":
        raise AcceptanceError(f"{check} omitted the generic Bearer challenge")
    try:
        body = json.loads(response.body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise AcceptanceError(f"{check} returned a non-JSON denial")
    if body != {"error": "unauthorized"}:
        raise AcceptanceError(f"{check} returned a non-generic denial body")


def expected_iris_tools(config_path: Path = DEFAULT_HERMES_CONFIG) -> frozenset[str]:
    config_map = yaml.safe_load(config_path.read_text())
    profile = yaml.safe_load(config_map["data"]["config.yaml"])
    include = profile["mcp_servers"]["verdify_greenhouse"]["tools"]["include"]
    tools = frozenset(str(name) for name in include)
    if not tools or "query" in tools or "climate" not in tools:
        raise AcceptanceError("canonical Iris tool inventory is absent or unsafe")
    return tools


def _unauthenticated_checks(endpoint: Endpoint, *, repeats: int, timeout: float) -> int:
    checks = (
        ("initialize", _initialize(1)),
        ("tools-list", _rpc("tools/list", 2)),
        ("admin-query", _rpc("tools/call", 3, {"name": "query", "arguments": {"sql": "SELECT 1"}})),
    )
    completed = 0
    for _round in range(repeats):
        for name, payload in checks:
            response = _request(endpoint.url, method="POST", payload=payload, timeout=timeout)
            _assert_unauthorized(response, check=f"{endpoint.label}:{name}:missing-bearer")
            completed += 1

    unknown = secrets.token_urlsafe(32)
    for name, payload in checks:
        response = _request(endpoint.url, method="POST", payload=payload, bearer=unknown, timeout=timeout)
        _assert_unauthorized(response, check=f"{endpoint.label}:{name}:unknown-bearer")
        completed += 1
    return completed


def _authenticated_checks(
    endpoint: Endpoint,
    *,
    bearer: str,
    expected_tools: frozenset[str],
    safe_tool: str,
    repeats: int,
    timeout: float,
) -> int:
    if safe_tool not in expected_tools:
        raise AcceptanceError("selected safe tool is outside the canonical Iris audience")
    completed = 0
    for round_number in range(repeats):
        initialized = _request(
            endpoint.url,
            method="POST",
            payload=_initialize(100 + round_number),
            bearer=bearer,
            timeout=timeout,
        )
        _assert_no_session(initialized, check=f"{endpoint.label}:authenticated-initialize")
        if initialized.status != 200:
            raise AcceptanceError(
                f"{endpoint.label}:authenticated-initialize returned HTTP {initialized.status}, expected 200"
            )
        initialized_payload = _protocol_payload(initialized, check=f"{endpoint.label}:authenticated-initialize")
        if "result" not in initialized_payload:
            raise AcceptanceError(f"{endpoint.label}:authenticated-initialize returned no result")
        completed += 1

        listed = _request(
            endpoint.url,
            method="POST",
            payload=_rpc("tools/list", 200 + round_number),
            bearer=bearer,
            timeout=timeout,
        )
        _assert_no_session(listed, check=f"{endpoint.label}:authenticated-tools-list")
        if listed.status != 200:
            raise AcceptanceError(
                f"{endpoint.label}:authenticated-tools-list returned HTTP {listed.status}, expected 200"
            )
        list_payload = _protocol_payload(listed, check=f"{endpoint.label}:authenticated-tools-list")
        try:
            actual_tools = frozenset(tool["name"] for tool in list_payload["result"]["tools"])
        except (KeyError, TypeError):
            raise AcceptanceError(f"{endpoint.label}:authenticated-tools-list returned no inventory")
        if actual_tools != expected_tools:
            raise AcceptanceError(f"{endpoint.label}:authenticated-tools-list differs from the bounded Iris audience")
        completed += 1

        denied_query = _request(
            endpoint.url,
            method="POST",
            payload=_rpc("tools/call", 300 + round_number, {"name": "query", "arguments": {"sql": "SELECT 1"}}),
            bearer=bearer,
            timeout=timeout,
        )
        _assert_no_session(denied_query, check=f"{endpoint.label}:iris-admin-query")
        if denied_query.status != 200:
            raise AcceptanceError(
                f"{endpoint.label}:iris-admin-query returned HTTP {denied_query.status}, expected tool denial"
            )
        query_payload = _protocol_payload(denied_query, check=f"{endpoint.label}:iris-admin-query")
        try:
            query_denied = query_payload["result"]["isError"] is True
        except (KeyError, TypeError):
            query_denied = False
        if not query_denied:
            raise AcceptanceError(f"{endpoint.label}:Iris obtained the admin query tool")
        completed += 1

        safe_call = _request(
            endpoint.url,
            method="POST",
            payload=_rpc("tools/call", 400 + round_number, {"name": safe_tool, "arguments": {}}),
            bearer=bearer,
            timeout=timeout,
        )
        _assert_no_session(safe_call, check=f"{endpoint.label}:safe-tool")
        if safe_call.status != 200:
            raise AcceptanceError(f"{endpoint.label}:safe-tool returned HTTP {safe_call.status}, expected 200")
        safe_payload = _protocol_payload(safe_call, check=f"{endpoint.label}:safe-tool")
        try:
            safe_succeeded = safe_payload["result"].get("isError", False) is False
        except (KeyError, TypeError, AttributeError):
            safe_succeeded = False
        if not safe_succeeded:
            raise AcceptanceError(f"{endpoint.label}:safe-tool did not complete successfully")
        completed += 1
    return completed


def _readiness_check(endpoint: Endpoint, *, timeout: float) -> None:
    response = _request(endpoint.url, method="GET", timeout=timeout)
    if response.status != 200:
        raise AcceptanceError(f"{endpoint.label}:readiness returned HTTP {response.status}, expected 200")
    payload = _protocol_payload(response, check=f"{endpoint.label}:readiness")
    expected = {
        "ready": True,
        "auth_mode": "enforce",
        "db": "ok",
        "auth_misconfigured": False,
        "missing_tools": [],
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        raise AcceptanceError(f"{endpoint.label}:readiness did not prove auth enforcement and healthy dependencies")
    if "iris" not in payload.get("auth_audiences_configured", []):
        raise AcceptanceError(f"{endpoint.label}:readiness did not report the Iris audience")


def _endpoint(value: str) -> Endpoint:
    label, separator, url = value.partition("=")
    if not separator or not label or not url:
        raise argparse.ArgumentTypeError("endpoint must be LABEL=URL")
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise argparse.ArgumentTypeError("endpoint URL must be an http(s) URL without userinfo or a fragment")
    return Endpoint(label, url)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", action="append", required=True, type=_endpoint, help="MCP endpoint as LABEL=URL")
    parser.add_argument(
        "--readiness-url", action="append", default=[], type=_endpoint, help="internal readiness endpoint as LABEL=URL"
    )
    parser.add_argument("--token-env", default="VERDIFY_MCP_TOKEN", help="environment variable containing Iris bearer")
    parser.add_argument("--unauthenticated-only", action="store_true", help="run only missing/unknown bearer checks")
    parser.add_argument("--expected-tools-config", type=Path, default=DEFAULT_HERMES_CONFIG)
    parser.add_argument("--safe-tool", default="climate")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=15.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.repeats < 1 or args.repeats > 20:
        raise AcceptanceError("repeats must be between 1 and 20")
    expected_tools = expected_iris_tools(args.expected_tools_config)
    bearer = None if args.unauthenticated_only else os.environ.get(args.token_env)
    if not args.unauthenticated_only and not bearer:
        raise AcceptanceError(f"Iris bearer environment variable is absent: {args.token_env}")

    endpoint_receipts = []
    for endpoint in args.endpoint:
        unauthenticated_checks = _unauthenticated_checks(endpoint, repeats=args.repeats, timeout=args.timeout)
        authenticated_checks = 0
        if bearer is not None:
            authenticated_checks = _authenticated_checks(
                endpoint,
                bearer=bearer,
                expected_tools=expected_tools,
                safe_tool=args.safe_tool,
                repeats=args.repeats,
                timeout=args.timeout,
            )
        endpoint_receipts.append(
            {
                "label": endpoint.label,
                "unauthenticated_checks": unauthenticated_checks,
                "authenticated_checks": authenticated_checks,
            }
        )
    for endpoint in args.readiness_url:
        _readiness_check(endpoint, timeout=args.timeout)

    print(
        json.dumps(
            {
                "schema": "verdify.mcp-security-acceptance/v1",
                "checked_at": datetime.now(UTC).isoformat(),
                "status": "pass",
                "stateless": True,
                "expected_iris_tool_count": len(expected_tools),
                "safe_tool": args.safe_tool if bearer is not None else None,
                "endpoints": endpoint_receipts,
                "readiness_labels": [endpoint.label for endpoint in args.readiness_url],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AcceptanceError as exc:
        print(
            json.dumps({"schema": "verdify.mcp-security-acceptance/v1", "status": "fail", "error": str(exc)}),
            file=sys.stderr,
        )
        raise SystemExit(1)
