"""Server-side MCP audience authorization (#585, audit §8.8) — pure-logic tests.

Covers, with no live DB or MCP runtime:
- token → audience resolution (constant-time comparison path);
- VERDIFY_MCP_AUTH_MODE behaviors: off (byte-identical bypass), log
  (denials recorded, never blocked), enforce (denials rejected);
- fail-closed enforce behavior for missing/unknown tokens and unknown modes;
- the per-audience allow/deny matrix for every registered tool;
- the drift guard holding the hermes-config.yaml tools.include list, the
  readyz HERMES_REQUIRED_TOOLS set, and the server "iris" audience in
  lockstep;
- TOOL_AUDIENCES completeness against the actual @mcp.tool registrations;
- the call_tool dispatch wiring and the /readyz auth surface.

Import pattern mirrors tests/test_17_planner_health_surface.py: the logic CI
environment deliberately does not install the MCP runtime, so server.py loads
under a minimal FastMCP stub that records tool registrations.
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import logging
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

_MISSING_MODULE = object()
_MCP_STUB_MODULES = ("mcp", "mcp.server", "mcp.server.fastmcp")

_CALL_TOOL_SENTINEL = object()


def _install_fastmcp_test_stub() -> None:
    """Registration-surface stub; call_tool returns a sentinel for wiring tests."""

    class _FastMCP:
        def __init__(self, *_args, **_kwargs) -> None:
            self._registered_tools: list[SimpleNamespace] = []

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

    def test_unrecognized_audience_env_is_reported_by_name_not_matched(self, mcp_server, monkeypatch):
        monkeypatch.setenv("VERDIFY_MCP_TOKEN_SUPERUSER", "tok-x")
        registry, unrecognized = mcp_server.audience_token_registry()
        assert registry == {}
        assert unrecognized == ["VERDIFY_MCP_TOKEN_SUPERUSER"]

    def test_comparison_is_constant_time_full_scan(self, mcp_server):
        source = inspect.getsource(mcp_server.resolve_token_audience)
        assert "hmac.compare_digest" in source
        # The registry loop must not early-exit on a match.
        assert "break" not in source and "return audience" not in source

    def test_duplicate_tokens_resolve_deterministically(self, mcp_server, monkeypatch):
        _configure(monkeypatch, iris="same", admin="same")
        registry, _ = mcp_server.audience_token_registry()
        # First audience in sorted order wins.
        assert mcp_server.resolve_token_audience("same", registry) == "admin"


# ─── Mode behaviors ──────────────────────────────────────────────────────


class TestAuthModes:
    def test_default_mode_is_off(self, mcp_server):
        assert mcp_server.auth_mode() == "off"

    @pytest.mark.parametrize("mode", ["off", "log", "enforce"])
    def test_explicit_modes(self, mcp_server, monkeypatch, mode):
        _configure(monkeypatch, mode=mode)
        assert mcp_server.auth_mode() == mode

    @pytest.mark.parametrize("bogus", ["enforced", "ON", "true", "1", "audit"])
    def test_unrecognized_mode_fails_closed_to_enforce(self, mcp_server, monkeypatch, bogus):
        _configure(monkeypatch, mode=bogus)
        assert mcp_server.auth_mode() == "enforce"

    def test_off_mode_never_denies_and_never_reads_the_registry(self, mcp_server, monkeypatch):
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
        assert (
            hermes_config["model"]
            == live_config["model"]
            == {
                "default": "llm.primary.longctx.mm",
                "provider": "custom",
                "base_url": "https://cortex.vallery.net/v1",
            }
        )
        assert hermes_config["agent"]["reasoning_effort"] == live_config["agent"]["reasoning_effort"] == "xhigh"
        assert hermes_config["agent"]["tool_use_enforcement"] is True
        assert live_config["agent"]["tool_use_enforcement"] is True
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
    def test_default_off_mode_is_ready_and_visible(self, mcp_server, ready_db):
        response = _run(mcp_server.mcp_ready(None))
        payload = json.loads(response.body)
        assert response.status_code == 200
        assert payload["ready"] is True
        assert payload["auth_mode"] == "off"
        assert payload["auth_audiences_configured"] == []
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

    def test_readyz_reports_unrecognized_token_env_names_only(self, mcp_server, ready_db, monkeypatch):
        monkeypatch.setenv("VERDIFY_MCP_TOKEN_SUPERUSER", "tok-secret")
        response = _run(mcp_server.mcp_ready(None))
        payload = json.loads(response.body)
        assert payload["auth_unrecognized_token_envs"] == ["VERDIFY_MCP_TOKEN_SUPERUSER"]
        assert "tok-secret" not in response.body.decode()
