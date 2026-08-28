"""Server-side MCP audience authorization (#585, audit §8.8) — pure-logic tests.

Covers, with no live DB or MCP runtime:
- token → audience resolution (constant-time comparison path);
- VERDIFY_MCP_AUTH_MODE behaviors: off (byte-identical bypass), log
  (denials recorded, never blocked), enforce (denials rejected);
- fail-closed enforce behavior for missing/unknown tokens and unknown modes;
- transport-level rejection before unauthenticated initialize/tools-list;
- the per-audience allow/deny matrix for every registered tool;
- the drift guard holding the hermes-config.yaml tools.include list, the
  readyz HERMES_REQUIRED_TOOLS set, and the server "iris" audience in
  lockstep;
- TOOL_AUDIENCES completeness against the actual @mcp.tool registrations;
- stateless two-replica GitOps rendering, call_tool dispatch wiring, and the
  /readyz auth surface.

Import pattern mirrors tests/test_17_planner_health_surface.py: the logic CI
environment deliberately does not install the MCP runtime, so server.py loads
under a minimal FastMCP stub that records tool registrations.
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import logging
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
HERMES_CONFIG_PATH = REPO_ROOT / "deploy" / "k8s" / "components" / "hermes-iris" / "hermes-config.yaml"
HERMES_EXPERIMENT_CONFIG_PATH = (
    REPO_ROOT / "deploy" / "k8s" / "components" / "hermes-iris-experiment" / "hermes-config.yaml"
)
MCP_DEPLOYMENT_PATH = REPO_ROOT / "deploy" / "k8s" / "base" / "mcp-deployment.yaml"
BASE_CONFIG_PATH = REPO_ROOT / "deploy" / "k8s" / "base" / "configmap.yaml"
PROD_OVERLAY_PATH = REPO_ROOT / "deploy" / "k8s" / "overlays" / "prod"

_MISSING_MODULE = object()
_MCP_STUB_MODULES = ("mcp", "mcp.server", "mcp.server.fastmcp")

_CALL_TOOL_SENTINEL = object()


def _install_fastmcp_test_stub() -> None:
    """Registration-surface stub; call_tool returns a sentinel for wiring tests."""

    class _FastMCP:
        def __init__(self, *_args, **_kwargs) -> None:
            self._registered_tools: list[SimpleNamespace] = []
            self._init_kwargs = _kwargs
            self.settings = SimpleNamespace(streamable_http_path="/mcp")

        def tool(self, *_args, **_kwargs):
            def decorator(func):
                self._registered_tools.append(SimpleNamespace(name=func.__name__))
                return func

            return decorator

        def custom_route(self, *_args, **_kwargs):
            return lambda func: func

        async def list_tools(self):
            return list(self._registered_tools)

        async def call_tool(self, name, arguments):
            return _CALL_TOOL_SENTINEL

        def run(self, *_args, **_kwargs) -> None:
            pass

    fastmcp_module = ModuleType("mcp.server.fastmcp")
    fastmcp_module.FastMCP = _FastMCP
    server_module = ModuleType("mcp.server")
    server_module.__path__ = []
    server_module.fastmcp = fastmcp_module
    mcp_module = ModuleType("mcp")
    mcp_module.__path__ = []
    mcp_module.server = server_module
    sys.modules["mcp"] = mcp_module
    sys.modules["mcp.server"] = server_module
    sys.modules["mcp.server.fastmcp"] = fastmcp_module


@pytest.fixture(scope="module")
def mcp_server():
    prior_modules = {name: sys.modules.get(name, _MISSING_MODULE) for name in _MCP_STUB_MODULES}
    _install_fastmcp_test_stub()
    try:
        path = REPO_ROOT / "mcp" / "server.py"
        spec = importlib.util.spec_from_file_location("verdify_mcp_server_audience_auth_test", path)
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


@pytest.fixture(autouse=True)
def clean_auth_env(monkeypatch):
    """No ambient auth config may leak into (or out of) any test."""
    monkeypatch.delenv("VERDIFY_MCP_AUTH_MODE", raising=False)
    import os

    for key in [k for k in os.environ if k.startswith("VERDIFY_MCP_TOKEN_")]:
        monkeypatch.delenv(key, raising=False)


def _configure(monkeypatch, mode: str | None = None, **tokens: str) -> None:
    if mode is not None:
        monkeypatch.setenv("VERDIFY_MCP_AUTH_MODE", mode)
    for audience, token in tokens.items():
        monkeypatch.setenv(f"VERDIFY_MCP_TOKEN_{audience.upper()}", token)


def _run(coro):
    import asyncio

    return asyncio.run(coro)


class _InventoryASGIApp:
    """Fake protocol endpoint whose response makes accidental exposure obvious."""

    def __init__(self) -> None:
        self.requests: list[dict] = []

    async def __call__(self, _scope, receive, send) -> None:
        request = await receive()
        self.requests.append(json.loads(request["body"]))
        body = b'{"jsonrpc":"2.0","id":1,"result":{"tools":[{"name":"private_tool"}]}}'
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": body})


def _transport_request(app, method: str, *, authorization: str | None = None, path: str = "/mcp"):
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": (
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "transport-test", "version": "1"},
            }
            if method == "initialize"
            else {}
        ),
    }
    body = json.dumps(payload).encode()
    headers = [(b"content-type", b"application/json")]
    if authorization is not None:
        headers.append((b"authorization", authorization.encode()))
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": headers,
    }
    messages = [{"type": "http.request", "body": body, "more_body": False}]
    sent = []

    async def receive():
        return messages.pop(0)

    async def send(message):
        sent.append(message)

    _run(app(scope, receive, send))
    status = next(message["status"] for message in sent if message["type"] == "http.response.start")
    response_body = b"".join(message.get("body", b"") for message in sent if message["type"] == "http.response.body")
    return status, response_body, sent


# ─── Token → audience resolution ─────────────────────────────────────────


class TestTokenResolution:
    def test_each_configured_audience_resolves(self, mcp_server, monkeypatch):
        _configure(monkeypatch, iris="tok-iris", experiment="tok-exp", admin="tok-admin")
        registry, unrecognized = mcp_server.audience_token_registry()
        assert unrecognized == []
        assert mcp_server.resolve_token_audience("tok-iris", registry) == "iris"
        assert mcp_server.resolve_token_audience("tok-exp", registry) == "experiment"
        assert mcp_server.resolve_token_audience("tok-admin", registry) == "admin"

    def test_unknown_missing_and_empty_tokens_resolve_to_none(self, mcp_server, monkeypatch):
        _configure(monkeypatch, iris="tok-iris")
        registry, _ = mcp_server.audience_token_registry()
        assert mcp_server.resolve_token_audience("wrong", registry) is None
        assert mcp_server.resolve_token_audience(None, registry) is None
        assert mcp_server.resolve_token_audience("", registry) is None

    def test_empty_valued_env_var_grants_nothing(self, mcp_server, monkeypatch):
        monkeypatch.setenv("VERDIFY_MCP_TOKEN_IRIS", "")
        registry, _ = mcp_server.audience_token_registry()
        assert registry == {}

    @pytest.mark.parametrize(
        "invalid_token",
        [
            "",
            "   ",
            "token with space",
            "token\twith-tab",
            "token\nwith-newline",
            "token\x7fwith-control",
            "token:with-delimiter",
            "token-with-ünicode",
        ],
    )
    def test_invalid_bearer_values_are_reported_by_env_name_only(self, mcp_server, monkeypatch, invalid_token):
        monkeypatch.setenv("VERDIFY_MCP_TOKEN_IRIS", invalid_token)
        registry, unrecognized, duplicates, invalid = mcp_server.audience_token_configuration()
        assert registry == {}
        assert unrecognized == []
        assert duplicates == []
        assert invalid == ["VERDIFY_MCP_TOKEN_IRIS"]

    def test_rfc6750_b64token_symbols_and_padding_are_valid(self, mcp_server, monkeypatch):
        monkeypatch.setenv("VERDIFY_MCP_TOKEN_IRIS", "Ab9-._~+/==")
        registry, unrecognized, duplicates, invalid = mcp_server.audience_token_configuration()
        assert registry == {"iris": "Ab9-._~+/=="}
        assert unrecognized == duplicates == invalid == []

    def test_unrecognized_audience_env_is_reported_by_name_not_matched(self, mcp_server, monkeypatch):
        monkeypatch.setenv("VERDIFY_MCP_TOKEN_SUPERUSER", "tok-x")
        registry, unrecognized = mcp_server.audience_token_registry()
        assert registry == {}
        assert unrecognized == ["VERDIFY_MCP_TOKEN_SUPERUSER"]

    def test_misspelled_known_audience_env_is_unrecognized(self, mcp_server, monkeypatch):
        monkeypatch.setenv("VERDIFY_MCP_TOKEN_iris", "tok-x")
        registry, unrecognized = mcp_server.audience_token_registry()
        assert registry == {}
        assert unrecognized == ["VERDIFY_MCP_TOKEN_iris"]

    def test_comparison_is_constant_time_full_scan(self, mcp_server):
        source = inspect.getsource(mcp_server.resolve_token_audience)
        assert "hmac.compare_digest" in source
        # The registry loop must not early-exit on a match.
        assert "break" not in source and "return audience" not in source

    def test_duplicate_tokens_remove_all_affected_audiences(self, mcp_server, monkeypatch):
        _configure(monkeypatch, iris="same", admin="same")
        registry, unrecognized, duplicates, invalid = mcp_server.audience_token_configuration()
        assert registry == {}
        assert unrecognized == []
        assert invalid == []
        assert duplicates == ["VERDIFY_MCP_TOKEN_ADMIN", "VERDIFY_MCP_TOKEN_IRIS"]
        assert mcp_server.resolve_token_audience("same", registry) is None


# ─── Mode behaviors ──────────────────────────────────────────────────────


class TestAuthModes:
    def test_default_mode_is_enforce(self, mcp_server):
        assert mcp_server.auth_mode() == "enforce"

    @pytest.mark.parametrize("mode", ["off", "log", "enforce"])
    def test_explicit_modes(self, mcp_server, monkeypatch, mode):
        _configure(monkeypatch, mode=mode)
        assert mcp_server.auth_mode() == mode

    @pytest.mark.parametrize("bogus", ["enforced", "ON", "true", "1", "audit"])
    def test_unrecognized_mode_fails_closed_to_enforce(self, mcp_server, monkeypatch, bogus):
        _configure(monkeypatch, mode=bogus)
        assert mcp_server.auth_mode() == "enforce"

    def test_off_mode_never_denies_and_never_reads_the_registry(self, mcp_server, monkeypatch):
        _configure(monkeypatch, mode="off")

        def boom():  # pragma: no cover - must not be reached
            raise AssertionError("off mode must not consult the token registry")

        monkeypatch.setattr(mcp_server, "audience_token_registry", boom)
        for tool in mcp_server.TOOL_AUDIENCES:
            mcp_server.authorize_tool_call(tool, None)
            mcp_server.authorize_tool_call(tool, "garbage")

    def test_log_mode_records_denial_but_does_not_block(self, mcp_server, monkeypatch, caplog):
        _configure(monkeypatch, mode="log", iris="tok-iris")
        with caplog.at_level(logging.WARNING, logger="verdify.mcp.auth"):
            mcp_server.authorize_tool_call("query", "tok-iris")  # denied for iris
        records = [json.loads(r.message) for r in caplog.records]
        assert len(records) == 1
        assert records[0] == {
            "event": "mcp_tool_authz_denial",
            "mode": "log",
            "enforced": False,
            "tool": "query",
            "audience": "iris",
            "reason": "tool_not_in_audience",
        }

    def test_log_mode_allowed_call_logs_nothing(self, mcp_server, monkeypatch, caplog):
        _configure(monkeypatch, mode="log", iris="tok-iris")
        with caplog.at_level(logging.WARNING, logger="verdify.mcp.auth"):
            mcp_server.authorize_tool_call("climate", "tok-iris")
        assert caplog.records == []

    def test_denial_log_never_contains_the_token(self, mcp_server, monkeypatch, caplog):
        _configure(monkeypatch, mode="log", iris="tok-secret-value")
        with caplog.at_level(logging.WARNING, logger="verdify.mcp.auth"):
            mcp_server.authorize_tool_call("query", "tok-secret-value")
            mcp_server.authorize_tool_call("climate", "presented-secret")
        assert caplog.records
        for record in caplog.records:
            assert "tok-secret-value" not in record.message
            assert "presented-secret" not in record.message


# ─── Enforce mode: fail closed ───────────────────────────────────────────


class TestEnforceFailClosed:
    def test_missing_token_denies_every_tool(self, mcp_server, monkeypatch):
        _configure(monkeypatch, mode="enforce", iris="tok-iris")
        for tool in mcp_server.TOOL_AUDIENCES:
            with pytest.raises(mcp_server.ToolAccessDenied):
                mcp_server.authorize_tool_call(tool, None)

    def test_unknown_token_denies_every_tool(self, mcp_server, monkeypatch):
        _configure(monkeypatch, mode="enforce", iris="tok-iris", admin="tok-admin")
        for tool in mcp_server.TOOL_AUDIENCES:
            with pytest.raises(mcp_server.ToolAccessDenied):
                mcp_server.authorize_tool_call(tool, "not-a-configured-token")

    def test_empty_registry_denies_every_tool(self, mcp_server, monkeypatch):
        _configure(monkeypatch, mode="enforce")
        with pytest.raises(mcp_server.ToolAccessDenied):
            mcp_server.authorize_tool_call("climate", "anything")

    def test_denial_message_names_tool_and_audience_only(self, mcp_server, monkeypatch):
        _configure(monkeypatch, mode="enforce", iris="tok-iris")
        with pytest.raises(mcp_server.ToolAccessDenied) as excinfo:
            mcp_server.authorize_tool_call("plan_run", "tok-iris")
        message = str(excinfo.value)
        assert "plan_run" in message and "iris" in message
        assert "tok-iris" not in message

    def test_admin_and_iris_duplicate_never_resolves_as_admin(self, mcp_server, monkeypatch):
        _configure(monkeypatch, mode="enforce", iris="shared", admin="shared")
        for tool in ("climate", "query"):
            with pytest.raises(mcp_server.ToolAccessDenied, match="fail-closed"):
                mcp_server.authorize_tool_call(tool, "shared")


# ─── Per-audience allow/deny matrix ──────────────────────────────────────


class TestAudienceMatrix:
    def test_iris_matrix(self, mcp_server, monkeypatch):
        _configure(monkeypatch, mode="enforce", iris="tok-iris")
        allowed = mcp_server.audience_allowlist("iris")
        assert allowed == mcp_server.HERMES_REQUIRED_TOOLS
        denied = set(mcp_server.TOOL_AUDIENCES) - allowed
        assert denied == {"outcome_kpi", "plan_run", "query", "policy_template_propose"}
        for tool in allowed:
            mcp_server.authorize_tool_call(tool, "tok-iris")
        for tool in denied:
            with pytest.raises(mcp_server.ToolAccessDenied):
                mcp_server.authorize_tool_call(tool, "tok-iris")

    def test_experiment_matrix(self, mcp_server, monkeypatch):
        _configure(monkeypatch, mode="enforce", experiment="tok-exp")
        allowed = mcp_server.audience_allowlist("experiment")
        # Audit §8.8: qualified reads + trigger acknowledgement + the ONE
        # actuation-eligible output (opaque template selection, Lane C #584).
        assert allowed == {
            "climate",
            "forecast",
            "topology",
            "position_current",
            "crop_history",
            "crop_lifecycle",
            "acknowledge_trigger",
            "policy_template_propose",
        }
        # Treatment-revealing reads and ordinary writes are denied.
        for tool in (
            "get_setpoints",
            "plan_status",
            "history",
            "scorecard",
            "outcome_kpi",
            "lessons",
            "lessons_search",
            "knowledge_search",
            "set_plan",
            "set_tunable",
            "plan_evaluate",
            "lessons_manage",
            "slack_ops",
            "query",
            "plan_run",
        ):
            with pytest.raises(mcp_server.ToolAccessDenied):
                mcp_server.authorize_tool_call(tool, "tok-exp")
        for tool in allowed:
            mcp_server.authorize_tool_call(tool, "tok-exp")

    def test_admin_allows_every_registered_tool(self, mcp_server, monkeypatch):
        _configure(monkeypatch, mode="enforce", admin="tok-admin")
        assert mcp_server.audience_allowlist("admin") == set(mcp_server.TOOL_AUDIENCES)
        for tool in mcp_server.TOOL_AUDIENCES:
            mcp_server.authorize_tool_call(tool, "tok-admin")

    def test_policy_template_propose_is_registered_experiment_and_admin_only(self, mcp_server):
        # Lane C tranche 2 (#584): moved out of PENDING into the live registry.
        assert mcp_server.TOOL_AUDIENCES["policy_template_propose"] == frozenset({"experiment", "admin"})
        assert "policy_template_propose" not in mcp_server.PENDING_TOOL_AUDIENCES
        assert not set(mcp_server.PENDING_TOOL_AUDIENCES) & set(mcp_server.TOOL_AUDIENCES)


# ─── Inventory completeness + hermes-config drift guard ──────────────────


class TestInventoryAndDriftGuards:
    def test_every_registered_tool_has_an_audience_entry(self, mcp_server):
        registered = {tool.name for tool in mcp_server.mcp._registered_tools}
        assert registered, "stub recorded no tool registrations"
        assert registered == set(mcp_server.TOOL_AUDIENCES)

    def test_audience_values_are_well_formed(self, mcp_server):
        for name, audiences in mcp_server.TOOL_AUDIENCES.items():
            assert audiences, name
            assert audiences <= mcp_server.KNOWN_AUDIENCES, name
            assert "admin" in audiences, name

    def test_startup_assertion_rejects_unlisted_tool(self, mcp_server, monkeypatch):
        registered = {tool.name for tool in mcp_server.mcp._registered_tools} | {"brand_new_tool"}
        monkeypatch.setattr(mcp_server, "_sync_registered_tool_names", lambda: frozenset(registered))
        with pytest.raises(AssertionError, match="brand_new_tool"):
            mcp_server._assert_tool_audience_registry_complete()

    def test_startup_assertion_rejects_stale_registry_entry(self, mcp_server, monkeypatch):
        registered = {tool.name for tool in mcp_server.mcp._registered_tools} - {"climate"}
        monkeypatch.setattr(mcp_server, "_sync_registered_tool_names", lambda: frozenset(registered))
        with pytest.raises(AssertionError, match="climate"):
            mcp_server._assert_tool_audience_registry_complete()

    def test_startup_completeness_reads_unfiltered_tool_manager(self, mcp_server):
        source = inspect.getsource(mcp_server._sync_registered_tool_names)
        assert 'getattr(mcp, "_tool_manager", None)' in source
        assert "manager.list_tools()" in source
        assert "mcp.list_tools()" not in source

    def test_iris_audience_matches_hermes_config_include_list(self, mcp_server):
        """Drift guard: client include list == server iris allowlist == readyz set.

        hermes-config.yaml is the ConfigMap that seeds the live Hermes profile;
        its mcp_servers.verdify_greenhouse.tools.include is the CLIENT-side
        boundary. The server-side iris audience must match it exactly — a tool
        added to one surface but not the other fails here before it ships.
        """
        configmap = yaml.safe_load(HERMES_CONFIG_PATH.read_text())
        hermes_config = yaml.safe_load(configmap["data"]["config.yaml"])
        include = hermes_config["mcp_servers"]["verdify_greenhouse"]["tools"]["include"]
        assert len(include) == len(set(include)), "duplicate entries in hermes include list"
        assert set(include) == mcp_server.audience_allowlist("iris")
        assert set(include) == mcp_server.HERMES_REQUIRED_TOOLS

    def test_experiment_audience_matches_hermes_experiment_config_include_list(self, mcp_server):
        """Drift guard (#585 tranche 2): the DARK experiment profile's client
        include list == the server `experiment` audience, exactly.

        deploy/k8s/components/hermes-iris-experiment/hermes-config.yaml is the
        Lane F flip target; its include list must carry the qualified reads +
        acknowledge_trigger + policy_template_propose surface and NOTHING
        else — a treatment-revealing tool added to either side fails here
        before it can ship.
        """
        configmap = yaml.safe_load(HERMES_EXPERIMENT_CONFIG_PATH.read_text())
        hermes_config = yaml.safe_load(configmap["data"]["config.yaml"])
        server = hermes_config["mcp_servers"]["verdify_greenhouse"]
        include = server["tools"]["include"]
        assert len(include) == len(set(include)), "duplicate entries in hermes experiment include list"
        assert set(include) == mcp_server.audience_allowlist("experiment")
        assert "policy_template_propose" in include
        # The experiment profile must present the EXPERIMENT audience
        # credential, not the iris one, at the same in-cluster MCP endpoint.
        assert server["headers"]["Authorization"] == "Bearer ${VERDIFY_MCP_TOKEN_EXPERIMENT}"
        live_configmap = yaml.safe_load(HERMES_CONFIG_PATH.read_text())
        live_config = yaml.safe_load(live_configmap["data"]["config.yaml"])
        assert server["url"] == live_config["mcp_servers"]["verdify_greenhouse"]["url"]
        # Provider/model selection is intentionally independent: the dark
        # experiment keeps its bounded Cortex route while live Iris uses the
        # OpenAI rollback profile. Audience/tool drift is the shared contract.
        assert hermes_config["model"]["default"] == "llm.primary.longctx"
        assert hermes_config["agent"]["reasoning_effort"] == "medium"
        assert live_config["model"]["default"] == "gpt-5.6-sol"
        assert live_config["agent"]["reasoning_effort"] == "xhigh"
        assert hermes_config["agent"]["max_turns"] == live_config["agent"]["max_turns"] == 30
        assert hermes_config["agent"]["disabled_toolsets"] == live_config["agent"]["disabled_toolsets"]
        # Treatment-revealing reads and quarantined writes can never appear.
        forbidden = {
            "get_setpoints",
            "plan_status",
            "history",
            "scorecard",
            "outcome_kpi",
            "lessons",
            "lessons_search",
            "knowledge_search",
            "set_plan",
            "set_tunable",
            "plan_evaluate",
            "lessons_manage",
            "slack_ops",
            "query",
            "plan_run",
        }
        assert not set(include) & forbidden

    def test_iris_planner_experiment_tools_match_server_experiment_audience(self, mcp_server):
        """The planner-side EXPERIMENT_MODE_TOOLS tuple (prompt + frozen tool
        manifest hash) is bound to the server audience registry."""
        planner_path = REPO_ROOT / "ingestor" / "iris_planner.py"
        spec = importlib.util.spec_from_file_location("iris_planner_experiment_tools_test", planner_path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert set(module.EXPERIMENT_MODE_TOOLS) == mcp_server.audience_allowlist("experiment")
        assert tuple(sorted(module.EXPERIMENT_MODE_TOOLS)) == module.EXPERIMENT_MODE_TOOLS


# ─── call_tool dispatch wiring ───────────────────────────────────────────


class TestCallToolWiring:
    def test_server_instance_is_the_authorized_subclass(self, mcp_server):
        assert isinstance(mcp_server.mcp, mcp_server.AudienceAuthorizedFastMCP)

    def test_off_mode_dispatch_is_a_pure_bypass(self, mcp_server, monkeypatch):
        _configure(monkeypatch, mode="off")

        def boom():  # pragma: no cover - must not be reached
            raise AssertionError("off mode must not consult the token registry")

        monkeypatch.setattr(mcp_server, "audience_token_registry", boom)
        assert _run(mcp_server.mcp.call_tool("query", {})) is _CALL_TOOL_SENTINEL

    def test_enforce_dispatch_denies_without_request_context(self, mcp_server, monkeypatch):
        _configure(monkeypatch, mode="enforce", admin="tok-admin")
        # The stub has no _mcp_server/request context → no bearer token → deny.
        with pytest.raises(mcp_server.ToolAccessDenied):
            _run(mcp_server.mcp.call_tool("climate", {}))

    def test_enforce_dispatch_allows_authorized_bearer(self, mcp_server, monkeypatch):
        _configure(monkeypatch, mode="enforce", admin="tok-admin")
        monkeypatch.setattr(mcp_server, "_request_bearer_token", lambda _server: "tok-admin")
        assert _run(mcp_server.mcp.call_tool("query", {})) is _CALL_TOOL_SENTINEL

    def test_enforce_dispatch_denies_out_of_audience_bearer(self, mcp_server, monkeypatch):
        _configure(monkeypatch, mode="enforce", iris="tok-iris")
        monkeypatch.setattr(mcp_server, "_request_bearer_token", lambda _server: "tok-iris")
        with pytest.raises(mcp_server.ToolAccessDenied):
            _run(mcp_server.mcp.call_tool("query", {}))

    def test_bearer_extraction_parses_authorization_header(self, mcp_server):
        def server_with_header(value):
            headers = {} if value is None else {"authorization": value}
            ctx = SimpleNamespace(request=SimpleNamespace(headers=headers))
            return SimpleNamespace(_mcp_server=SimpleNamespace(request_context=ctx))

        assert mcp_server._request_bearer_token(server_with_header("Bearer tok-1")) == "tok-1"
        assert mcp_server._request_bearer_token(server_with_header("bearer tok-2")) == "tok-2"
        assert mcp_server._request_bearer_token(server_with_header("Basic dXNlcg==")) is None
        assert mcp_server._request_bearer_token(server_with_header("Bearer ")) is None
        assert mcp_server._request_bearer_token(server_with_header(None)) is None
        assert mcp_server._request_bearer_token(SimpleNamespace()) is None


# ─── tools/list audience filtering ──────────────────────────────────────


class TestListToolsAudienceFiltering:
    @pytest.mark.parametrize("mode", ["log", "enforce"])
    @pytest.mark.parametrize(
        ("audience", "token", "expected_count"),
        [("iris", "tok-iris", 23), ("experiment", "tok-exp", 8), ("admin", "tok-admin", 27)],
    )
    def test_protocol_inventory_matches_exact_audience_allowlist(
        self, mcp_server, monkeypatch, mode, audience, token, expected_count
    ):
        _configure(monkeypatch, mode=mode, **{audience: token})
        monkeypatch.setattr(mcp_server, "_request_bearer_token", lambda _server: token)

        names = {tool.name for tool in _run(mcp_server.mcp.list_tools())}

        assert names == mcp_server.audience_allowlist(audience)
        assert len(names) == expected_count

    def test_internal_unfiltered_inventory_is_explicit_and_request_independent(self, mcp_server, monkeypatch):
        _configure(monkeypatch, mode="enforce", iris="tok-iris")
        monkeypatch.setattr(mcp_server, "_request_bearer_token", lambda _server: None)

        assert _run(mcp_server.mcp.list_tools()) == []
        assert {tool.name for tool in _run(mcp_server.mcp.list_tools_unfiltered())} == set(mcp_server.TOOL_AUDIENCES)

    def test_duplicate_configuration_exposes_no_inventory_even_in_log_mode(self, mcp_server, monkeypatch):
        _configure(monkeypatch, mode="log", iris="shared", admin="shared")
        monkeypatch.setattr(mcp_server, "_request_bearer_token", lambda _server: "shared")
        assert _run(mcp_server.mcp.list_tools()) == []


# ─── HTTP transport authentication ──────────────────────────────────────


class TestTransportAuthentication:
    @pytest.mark.parametrize("method", ["initialize", "tools/list"])
    def test_enforce_denies_before_protocol_or_inventory_dispatch(self, mcp_server, monkeypatch, method):
        _configure(monkeypatch, mode="enforce", iris="tok-iris")
        downstream = _InventoryASGIApp()
        app = mcp_server.MCPTransportAuthMiddleware(downstream)

        status, body, sent = _transport_request(app, method)

        assert status == 401
        assert body == b'{"error":"unauthorized"}'
        assert b"private_tool" not in body
        assert downstream.requests == []
        response_start = next(message for message in sent if message["type"] == "http.response.start")
        assert (b"www-authenticate", b"Bearer") in response_start["headers"]

    @pytest.mark.parametrize("method", ["initialize", "tools/list"])
    def test_authenticated_iris_reaches_protocol_and_keeps_tool_authz_layer(self, mcp_server, monkeypatch, method):
        _configure(monkeypatch, mode="enforce", iris="tok-iris")
        downstream = _InventoryASGIApp()
        app = mcp_server.MCPTransportAuthMiddleware(downstream)

        status, body, _sent = _transport_request(app, method, authorization="Bearer tok-iris")

        assert status == 200
        assert b"private_tool" in body
        assert [request["method"] for request in downstream.requests] == [method]
        mcp_server.authorize_tool_call("climate", "tok-iris")
        with pytest.raises(mcp_server.ToolAccessDenied):
            mcp_server.authorize_tool_call("query", "tok-iris")

    def test_unknown_credentials_fail_closed_without_secret_echo(self, mcp_server, monkeypatch, caplog):
        _configure(monkeypatch, mode="enforce", iris="tok-secret-value")
        downstream = _InventoryASGIApp()
        app = mcp_server.MCPTransportAuthMiddleware(downstream)

        with caplog.at_level(logging.WARNING, logger="verdify.mcp.auth"):
            status, body, _sent = _transport_request(app, "initialize", authorization="Bearer wrong-secret")

        assert status == 401
        assert downstream.requests == []
        assert b"wrong-secret" not in body
        assert all("wrong-secret" not in record.message for record in caplog.records)
        assert all("tok-secret-value" not in record.message for record in caplog.records)

    def test_duplicate_configuration_is_transport_fatal_even_in_log_mode(self, mcp_server, monkeypatch):
        _configure(monkeypatch, mode="log", iris="shared", admin="shared")
        downstream = _InventoryASGIApp()
        app = mcp_server.MCPTransportAuthMiddleware(downstream)

        status, body, _sent = _transport_request(app, "initialize", authorization="Bearer shared")

        assert status == 401
        assert body == b'{"error":"unauthorized"}'
        assert downstream.requests == []

    def test_readyz_stays_public_and_off_mode_is_a_pure_bypass(self, mcp_server, monkeypatch):
        downstream = _InventoryASGIApp()
        app = mcp_server.MCPTransportAuthMiddleware(downstream)

        _configure(monkeypatch, mode="enforce", iris="tok-iris")
        status, _body, _sent = _transport_request(app, "ready", path="/readyz")
        assert status == 200

        def boom():  # pragma: no cover - must not be reached
            raise AssertionError("off mode must not consult the token registry")

        _configure(monkeypatch, mode="off")
        monkeypatch.setattr(mcp_server, "audience_token_configuration", boom)
        status, _body, _sent = _transport_request(app, "initialize")
        assert status == 200

    def test_fastmcp_is_configured_for_supported_stateless_http(self, mcp_server):
        assert mcp_server.mcp._init_kwargs["stateless_http"] is True
        requirements = (REPO_ROOT / "mcp" / "requirements.txt").read_text()
        assert "mcp>=1.27,<2" in requirements


# ─── /readyz auth surface ────────────────────────────────────────────────


class _ReadyConnection:
    def __init__(self) -> None:
        self.closed = False

    async def fetchval(self, sql: str):
        assert sql == "SELECT 1"
        return 1

    async def close(self) -> None:
        self.closed = True


@pytest.fixture()
def ready_db(mcp_server, monkeypatch):
    async def db():
        return _ReadyConnection()

    monkeypatch.setattr(mcp_server, "_db", db)


class TestReadyzAuthSurface:
    def test_default_enforce_mode_without_tokens_is_not_ready(self, mcp_server, ready_db):
        response = _run(mcp_server.mcp_ready(None))
        payload = json.loads(response.body)
        assert response.status_code == 503
        assert payload["ready"] is False
        assert payload["auth_mode"] == "enforce"
        assert payload["auth_audiences_configured"] == []
        assert payload["auth_misconfigured"] is True

    def test_explicit_off_mode_is_ready_and_visible(self, mcp_server, ready_db, monkeypatch):
        _configure(monkeypatch, mode="off")
        response = _run(mcp_server.mcp_ready(None))
        payload = json.loads(response.body)
        assert response.status_code == 200
        assert payload["ready"] is True
        assert payload["auth_mode"] == "off"
        assert payload["auth_misconfigured"] is False

    def test_log_mode_with_tokens_is_ready(self, mcp_server, ready_db, monkeypatch):
        _configure(monkeypatch, mode="log", iris="tok-iris")
        response = _run(mcp_server.mcp_ready(None))
        payload = json.loads(response.body)
        assert response.status_code == 200
        assert payload["auth_mode"] == "log"
        assert payload["auth_audiences_configured"] == ["iris"]

    def test_enforce_without_tokens_reports_not_ready(self, mcp_server, ready_db, monkeypatch):
        _configure(monkeypatch, mode="enforce")
        response = _run(mcp_server.mcp_ready(None))
        payload = json.loads(response.body)
        assert response.status_code == 503
        assert payload["ready"] is False
        assert payload["auth_misconfigured"] is True

    def test_enforce_with_tokens_is_ready(self, mcp_server, ready_db, monkeypatch):
        _configure(monkeypatch, mode="enforce", iris="tok-iris", admin="tok-admin")
        response = _run(mcp_server.mcp_ready(None))
        payload = json.loads(response.body)
        assert response.status_code == 200
        assert payload["auth_audiences_configured"] == ["admin", "iris"]
        assert payload["auth_misconfigured"] is False

    @pytest.mark.parametrize("audience", ["admin", "experiment"])
    def test_enforce_requires_active_iris_audience(self, mcp_server, ready_db, monkeypatch, audience):
        _configure(monkeypatch, mode="enforce", **{audience: f"tok-{audience}"})
        response = _run(mcp_server.mcp_ready(None))
        payload = json.loads(response.body)
        assert response.status_code == 503
        assert payload["ready"] is False
        assert payload["auth_audiences_configured"] == [audience]
        assert payload["auth_misconfigured"] is True

    def test_enforce_allows_iris_without_optional_experiment_audience(self, mcp_server, ready_db, monkeypatch):
        _configure(monkeypatch, mode="enforce", iris="tok-iris")
        response = _run(mcp_server.mcp_ready(None))
        payload = json.loads(response.body)
        assert response.status_code == 200
        assert payload["ready"] is True
        assert payload["auth_audiences_configured"] == ["iris"]
        assert payload["auth_misconfigured"] is False

    def test_duplicate_tokens_report_names_only_and_fail_readiness(self, mcp_server, ready_db, monkeypatch):
        _configure(monkeypatch, mode="enforce", iris="shared-secret", admin="shared-secret")

        response = _run(mcp_server.mcp_ready(None))
        payload = json.loads(response.body)

        assert response.status_code == 503
        assert payload["ready"] is False
        assert payload["auth_audiences_configured"] == []
        assert payload["auth_duplicate_token_envs"] == [
            "VERDIFY_MCP_TOKEN_ADMIN",
            "VERDIFY_MCP_TOKEN_IRIS",
        ]
        assert payload["auth_misconfigured"] is True
        assert "shared-secret" not in response.body.decode()

    def test_readyz_uses_unfiltered_inventory_path(self, mcp_server, ready_db, monkeypatch):
        _configure(monkeypatch, mode="enforce", iris="tok-iris")

        async def boom():  # pragma: no cover - must not be reached
            raise AssertionError("readiness must not use audience-filtered tools/list")

        monkeypatch.setattr(mcp_server.mcp, "list_tools", boom)
        response = _run(mcp_server.mcp_ready(None))
        assert response.status_code == 200

    def test_readyz_reports_unrecognized_token_env_names_only(self, mcp_server, ready_db, monkeypatch):
        monkeypatch.setenv("VERDIFY_MCP_TOKEN_SUPERUSER", "tok-secret")
        response = _run(mcp_server.mcp_ready(None))
        payload = json.loads(response.body)
        assert response.status_code == 503
        assert payload["ready"] is False
        assert payload["auth_unrecognized_token_envs"] == ["VERDIFY_MCP_TOKEN_SUPERUSER"]
        assert payload["auth_misconfigured"] is True
        assert "tok-secret" not in response.body.decode()

    @pytest.mark.parametrize(
        "invalid_token",
        ["", "   ", "secret with space", "secret\twith-tab", "secret\x1fcontrol", "secret:delimiter"],
    )
    def test_readyz_reports_invalid_token_env_names_only(self, mcp_server, ready_db, monkeypatch, invalid_token):
        monkeypatch.setenv("VERDIFY_MCP_TOKEN_IRIS", invalid_token)
        response = _run(mcp_server.mcp_ready(None))
        payload = json.loads(response.body)
        assert response.status_code == 503
        assert payload["ready"] is False
        assert payload["auth_audiences_configured"] == []
        assert payload["auth_invalid_token_envs"] == ["VERDIFY_MCP_TOKEN_IRIS"]
        assert payload["auth_misconfigured"] is True
        if invalid_token:
            assert invalid_token not in response.body.decode()


def test_production_mcp_enforces_iris_audience_with_the_existing_hermes_credential() -> None:
    config = yaml.safe_load(BASE_CONFIG_PATH.read_text())
    assert config["data"]["VERDIFY_MCP_AUTH_MODE"] == "enforce"

    resources = list(yaml.safe_load_all(MCP_DEPLOYMENT_PATH.read_text()))
    deployment = next(resource for resource in resources if resource["kind"] == "Deployment")
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    env = {entry["name"]: entry for entry in container["env"]}
    token = env["VERDIFY_MCP_TOKEN_IRIS"]["valueFrom"]["secretKeyRef"]
    assert token == {"name": "verdify-hermes", "key": "VERDIFY_MCP_TOKEN"}
    assert "optional" not in token
    assert "VERDIFY_MCP_TOKEN_ADMIN" not in env


def test_rendered_prod_keeps_affinity_through_first_stateless_image_rollout() -> None:
    rendered = subprocess.run(
        ["kustomize", "build", str(PROD_OVERLAY_PATH)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    resources = [resource for resource in yaml.safe_load_all(rendered) if isinstance(resource, dict)]
    deployment = next(
        resource
        for resource in resources
        if resource.get("kind") == "Deployment" and resource.get("metadata", {}).get("name") == "verdify-mcp"
    )
    service = next(
        resource
        for resource in resources
        if resource.get("kind") == "Service" and resource.get("metadata", {}).get("name") == "verdify-mcp"
    )
    hermes = next(
        resource
        for resource in resources
        if resource.get("kind") == "Deployment" and resource.get("metadata", {}).get("name") == "verdify-hermes-iris"
    )
    ingress = next(
        resource
        for resource in resources
        if resource.get("kind") == "IngressRoute" and resource.get("metadata", {}).get("name") == "verdify-t2-mcp"
    )

    assert deployment["spec"]["replicas"] == 2
    assert deployment["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"] == "10"
    assert hermes["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"] == "20"
    # Keep compatibility with the previously pinned stateful MCP image during
    # the first stateless image rollout. Affinity cleanup is a later GitOps PR.
    assert service["spec"]["sessionAffinity"] == "ClientIP"
    assert service["spec"]["sessionAffinityConfig"] == {"clientIP": {"timeoutSeconds": 10800}}
    # The WAN route exposes only the protocol endpoint; internal Service users
    # and kube probes can still call /readyz directly.
    assert ingress["spec"]["routes"][0]["match"] == "Host(`mcp.verdify.ai`) && PathPrefix(`/mcp`)"
