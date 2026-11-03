"""Named MCP agents (VD-59, architecture §5.6): keys, request actors, readiness and network.

Covers, with no live database, cluster or device:
- the mounted key directory (Secret verdify-mcp-agents): two keys, a removal
  seen only after the 60 s cache, a kubelet-style ``..data`` swap (removal and
  rotation), rejected names and shared tokens;
- resolve_caller, and a removed key refused at the transport (401) and at
  dispatch while Iris and the other agents keep working;
- the request actor (iris, agent:<name>, agent:admin), current_actor() and the
  mcp_tool_call audit line;
- /readyz: auth_agents_configured and auth_agent_keys_rejected, which never
  change readiness, and VERDIFY_MCP_TOKEN_AGENT staying unrecognized;
- `alerts` acknowledgements and resolutions attributed from the request actor,
  with a fake connection;
- the rendered allow-mcp-from-named-agents policy and the optional Secret mount.

server.py loads under a FastMCP stub that dispatches to the registered tool
functions, so a call through AudienceAuthorizedFastMCP.call_tool runs the real
tool body under the request actor.
"""

from __future__ import annotations

import asyncio
import dataclasses
import importlib.util
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
import yaml
from test_mcp_audience_auth import AGENT_TOOLS

REPO_ROOT = Path(__file__).resolve().parents[1]
PROD_OVERLAY = REPO_ROOT / "deploy" / "k8s" / "overlays" / "prod"

# The reads and alerts VD-59 grants. The rest of the architecture §5.6 agent
# column (AGENT_TOOLS) joins as #1171 registers it (#1173, #1174 and #1177 have).
VD59_AGENT_TOOLS = frozenset(
    {
        "climate",
        "forecast",
        "equipment_state",
        "history",
        "plan_status",
        "get_setpoints",
        "scorecard",
        "topology",
        "position_current",
        "alerts",
    }
)

IRIS_TOKEN = "fixture-iris-token"
ADMIN_TOKEN = "fixture-admin-token"
STEWARD_TOKEN = "fixture-steward-token"
SCOUT_TOKEN = "fixture-scout-token"
ALL_FIXTURE_TOKENS = (IRIS_TOKEN, ADMIN_TOKEN, STEWARD_TOKEN, SCOUT_TOKEN)

_MISSING_MODULE = object()
_MCP_STUB_MODULES = ("mcp", "mcp.server", "mcp.server.fastmcp")


def _install_dispatching_fastmcp_stub() -> None:
    """A FastMCP stand-in whose call_tool awaits the registered tool function."""

    class _FastMCP:
        def __init__(self, *_args, **_kwargs) -> None:
            self._functions: dict = {}
            self.settings = SimpleNamespace(streamable_http_path="/mcp")

        def tool(self, *_args, **_kwargs):
            def decorator(func):
                self._functions[func.__name__] = func
                return func

            return decorator

        def custom_route(self, *_args, **_kwargs):
            return lambda func: func

        async def list_tools(self):
            return [SimpleNamespace(name=name) for name in self._functions]

        async def call_tool(self, name, arguments):
            return await self._functions[name](**arguments)

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
def server():
    prior_modules = {name: sys.modules.get(name, _MISSING_MODULE) for name in _MCP_STUB_MODULES}
    _install_dispatching_fastmcp_stub()
    try:
        spec = importlib.util.spec_from_file_location(
            "verdify_mcp_server_named_agents_test", REPO_ROOT / "mcp" / "server.py"
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


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class KubeletSecretVolume:
    """The kubelet AtomicWriter layout of a Secret volume.

    Each key is a top-level link `<name> -> ..data/<name>`; `..data` links to a
    timestamped payload directory and is swapped atomically on every update,
    after which stale top-level links are removed and the old payload deleted.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.generation = 0
        root.mkdir(parents=True, exist_ok=True)

    def publish(self, keys: dict[str, str], *, cleanup: bool = True) -> None:
        self.generation += 1
        payload = self.root / f"..2026_11_02_12_00_{self.generation:02d}.000000001"
        payload.mkdir()
        for name, value in keys.items():
            (payload / name).write_text(value)
        swap = self.root / "..data_tmp"
        swap.symlink_to(payload.name)
        os.replace(swap, self.root / "..data")
        for name in keys:
            link = self.root / name
            if not link.is_symlink():
                link.symlink_to(Path("..data") / name)
        if cleanup:
            self.cleanup(keys, keep=payload.name)

    def cleanup(self, keys: dict[str, str], *, keep: str | None = None) -> None:
        keep = keep or os.readlink(self.root / "..data")
        for entry in self.root.iterdir():
            if entry.name.startswith(".."):
                if entry.name not in {"..data", keep} and entry.is_dir() and not entry.is_symlink():
                    for child in entry.iterdir():
                        child.unlink()
                    entry.rmdir()
            elif entry.name not in keys:
                entry.unlink()


@pytest.fixture(autouse=True)
def clock(server, monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr(server, "_agent_keys_clock", fake)
    monkeypatch.setattr(server, "_agent_key_scan", None)
    return fake


@pytest.fixture(autouse=True)
def auth_env(monkeypatch, tmp_path):
    for key in [k for k in os.environ if k.startswith("VERDIFY_MCP_TOKEN_")]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("VERDIFY_MCP_AUTH_MODE", "enforce")
    monkeypatch.setenv("VERDIFY_MCP_TOKEN_IRIS", IRIS_TOKEN)
    monkeypatch.setenv("VERDIFY_MCP_TOKEN_ADMIN", ADMIN_TOKEN)
    monkeypatch.setenv("VERDIFY_MCP_AGENT_KEYS_DIR", str(tmp_path / "mcp-agents"))


@pytest.fixture()
def volume(tmp_path):
    return KubeletSecretVolume(tmp_path / "mcp-agents")


@pytest.fixture()
def two_agents(volume):
    volume.publish({"steward": STEWARD_TOKEN + "\n", "scout": SCOUT_TOKEN})
    return volume


def _run(coro):
    return asyncio.run(coro)


def _as_caller(server, monkeypatch, token: str | None) -> None:
    monkeypatch.setattr(server, "_request_bearer_token", lambda _server: token)


# ─── The key directory ─────────────────────────────────────────────────────


class TestKeyDirectory:
    def test_two_keys_resolve_to_their_own_agents(self, server, two_agents):
        registry, rejected = server.agent_key_registry()
        assert sorted(registry) == ["scout", "steward"]
        assert rejected == []
        assert server.resolve_caller(STEWARD_TOKEN) == ("agent", "steward")
        assert server.resolve_caller(SCOUT_TOKEN) == ("agent", "scout")
        assert server.resolve_caller(IRIS_TOKEN) == ("iris", None)
        assert server.resolve_caller(ADMIN_TOKEN) == ("admin", None)
        assert server.resolve_caller("fixture-unknown-token") == (None, None)
        assert server.resolve_caller(None) == (None, None)
        assert server.resolve_caller("") == (None, None)

    def test_missing_or_empty_directory_means_no_agents(self, server, monkeypatch, tmp_path):
        assert server.agent_key_registry() == ({}, [])
        empty = tmp_path / "empty"
        empty.mkdir()
        monkeypatch.setenv("VERDIFY_MCP_AGENT_KEYS_DIR", str(empty))
        assert server.agent_key_registry() == ({}, [])
        monkeypatch.delenv("VERDIFY_MCP_AGENT_KEYS_DIR")
        assert server.DEFAULT_MCP_AGENT_KEYS_DIR == "/etc/verdify/mcp-agents"

    def test_removal_is_seen_after_the_60s_cache_without_a_restart(self, server, two_agents, clock):
        assert server.resolve_caller(SCOUT_TOKEN) == ("agent", "scout")
        two_agents.publish({"steward": STEWARD_TOKEN})
        clock.advance(59.9)
        assert server.resolve_caller(SCOUT_TOKEN) == ("agent", "scout"), "re-read before the 60 s cache expired"
        clock.advance(0.1)
        assert server.resolve_caller(SCOUT_TOKEN) == (None, None)
        assert server.resolve_caller(STEWARD_TOKEN) == ("agent", "steward")
        assert server.resolve_caller(IRIS_TOKEN) == ("iris", None)

    def test_unknown_tokens_never_force_an_early_reread(self, server, two_agents, clock):
        assert server.resolve_caller(STEWARD_TOKEN) == ("agent", "steward")
        two_agents.publish({"steward": STEWARD_TOKEN, "scout": SCOUT_TOKEN, "late": "fixture-late-token"})
        for _ in range(5):
            clock.advance(1)
            assert server.resolve_caller("fixture-late-token") == (None, None)
        clock.advance(55)
        assert server.resolve_caller("fixture-late-token") == ("agent", "late")

    def test_kubelet_data_swap_mid_window_and_rotation(self, server, two_agents, clock):
        two_agents.publish({"steward": "fixture-steward-rotated"}, cleanup=False)
        # Mid-swap: the stale `scout` link dangles and the old payload remains.
        assert (two_agents.root / "scout").is_symlink() and not (two_agents.root / "scout").exists()
        clock.advance(60)
        registry, rejected = server.agent_key_registry()
        assert sorted(registry) == ["steward"] and rejected == []
        assert server.resolve_caller(SCOUT_TOKEN) == (None, None)
        assert server.resolve_caller(STEWARD_TOKEN) == (None, None), "the rotated-out value still admitted"
        assert server.resolve_caller("fixture-steward-rotated") == ("agent", "steward")
        two_agents.cleanup({"steward": "fixture-steward-rotated"})
        clock.advance(60)
        assert server.agent_key_registry()[1] == []
        assert server.resolve_caller("fixture-steward-rotated") == ("agent", "steward")

    def test_rejections_are_by_name_and_admit_no_call(self, server, volume):
        volume.root.mkdir(parents=True, exist_ok=True)
        files = {
            "admin": "fixture-reserved-token",
            "Bad Name": "fixture-bad-name-token",
            "Upper": "fixture-upper-token",
            "-dash-first": "fixture-dash-token",
            "n" * 64: "fixture-long-name-token",
            "spaced-token": "fixture spaced token",
            "empty-token": "\n",
            "unicode-token": "fixture-tökén",
            "huge-token": "a" * 5000,
            "twin-a": "fixture-shared-token",
            "twin-b": "fixture-shared-token",
            "iris-copy": IRIS_TOKEN,
            "good": "  fixture-good-token \n",
            "n" * 63: "fixture-max-name-token",
        }
        for name, value in files.items():
            (volume.root / name).write_text(value)
        (volume.root / "nested").mkdir()
        (volume.root / ".hidden").write_text("fixture-hidden-token")
        registry, rejected = server.agent_key_registry()
        assert sorted(registry) == sorted(["good", "n" * 63])
        assert registry["good"] == "fixture-good-token"
        assert rejected == sorted(set(files) - {"good", "n" * 63} | {"nested"})
        for token in (
            "fixture-reserved-token",
            "fixture-bad-name-token",
            "fixture-upper-token",
            "fixture-dash-token",
            "fixture-long-name-token",
            "fixture-shared-token",
            "fixture-hidden-token",
        ):
            assert server.resolve_caller(token) == (None, None), token
        # The environment audience whose token an agent file copied is untouched.
        assert server.resolve_caller(IRIS_TOKEN) == ("iris", None)
        assert server.resolve_caller("fixture-good-token") == ("agent", "good")

    def test_agent_token_env_var_stays_unrecognized_and_admits_nothing(self, server, monkeypatch):
        monkeypatch.setenv("VERDIFY_MCP_TOKEN_AGENT", "fixture-env-agent-token")
        registry, unrecognized, duplicates, invalid = server.audience_token_configuration()
        assert sorted(registry) == ["admin", "iris"]
        assert unrecognized == ["VERDIFY_MCP_TOKEN_AGENT"] and duplicates == invalid == []
        assert server.resolve_caller("fixture-env-agent-token") == (None, None)
        assert server._transport_request_denial("fixture-env-agent-token") == {
            "audience": None,
            "reason": "unknown_token",
        }
        with pytest.raises(server.ToolAccessDenied):
            server.authorize_tool_call("climate", "fixture-env-agent-token")

    def test_registry_scan_and_resolution_never_log_token_values(self, server, two_agents, caplog):
        with caplog.at_level(logging.DEBUG):
            server.agent_key_registry()
            server.resolve_caller(STEWARD_TOKEN)
            server.resolve_caller("fixture-unknown-token")
        assert not any(token in caplog.text for token in ALL_FIXTURE_TOKENS)


# ─── Transport and dispatch ────────────────────────────────────────────────


class _ProtocolApp:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, _scope, receive, send) -> None:
        await receive()
        self.calls += 1
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})


def _transport_status(server, app, token: str | None) -> int:
    headers = [(b"content-type", b"application/json")]
    if token is not None:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    scope = {"type": "http", "method": "POST", "path": "/mcp", "headers": headers}
    messages = [{"type": "http.request", "body": b"{}", "more_body": False}]
    sent: list[dict] = []

    async def receive():
        return messages.pop(0)

    async def send(message):
        sent.append(message)

    _run(server.MCPTransportAuthMiddleware(app)(scope, receive, send))
    return next(message["status"] for message in sent if message["type"] == "http.response.start")


class TestTransportAndDispatch:
    def test_removed_key_is_refused_at_the_transport_and_at_dispatch(self, server, two_agents, clock, monkeypatch):
        app = _ProtocolApp()
        assert _transport_status(server, app, SCOUT_TOKEN) == 200
        server.authorize_tool_call("climate", SCOUT_TOKEN)

        two_agents.publish({"steward": STEWARD_TOKEN})
        clock.advance(60)

        assert _transport_status(server, app, SCOUT_TOKEN) == 401
        with pytest.raises(server.ToolAccessDenied):
            server.authorize_tool_call("climate", SCOUT_TOKEN)
        _as_caller(server, monkeypatch, SCOUT_TOKEN)
        with pytest.raises(server.ToolAccessDenied):
            _run(server.mcp.call_tool("climate", {}))
        assert _run(server.mcp.list_tools()) == []
        # Iris and the remaining agent keep working with no restart.
        for token in (IRIS_TOKEN, STEWARD_TOKEN):
            assert _transport_status(server, app, token) == 200
            server.authorize_tool_call("climate", token)

    def test_agent_tools_list_is_exactly_the_agent_allowlist(self, server, two_agents, monkeypatch):
        _as_caller(server, monkeypatch, STEWARD_TOKEN)
        names = {tool.name for tool in _run(server.mcp.list_tools())}
        registered = set(server.mcp._functions)
        assert names == server.audience_allowlist("agent") == AGENT_TOOLS & registered
        assert VD59_AGENT_TOOLS <= names

    def test_out_of_audience_denial_names_the_agent_never_the_token(self, server, two_agents, caplog):
        with caplog.at_level(logging.WARNING, logger="verdify.mcp.auth"):
            with pytest.raises(server.ToolAccessDenied, match="audience: agent") as excinfo:
                server.authorize_tool_call("set_plan", STEWARD_TOKEN)
        records = [json.loads(record.getMessage()) for record in caplog.records]
        assert records == [
            {
                "event": "mcp_tool_authz_denial",
                "mode": "enforce",
                "enforced": True,
                "tool": "set_plan",
                "audience": "agent",
                "agent": "steward",
                "reason": "tool_not_in_audience",
            }
        ]
        assert STEWARD_TOKEN not in str(excinfo.value) and STEWARD_TOKEN not in caplog.text

    @pytest.mark.parametrize("name", ["admin", "Bad Name"])
    def test_rejected_agent_files_admit_no_call(self, server, volume, monkeypatch, name):
        volume.root.mkdir(parents=True, exist_ok=True)
        (volume.root / name).write_text("fixture-rejected-token")
        assert _transport_status(server, _ProtocolApp(), "fixture-rejected-token") == 401
        with pytest.raises(server.ToolAccessDenied):
            server.authorize_tool_call("climate", "fixture-rejected-token")
        _as_caller(server, monkeypatch, "fixture-rejected-token")
        assert _run(server.mcp.list_tools()) == []


# ─── Request actor and audit ───────────────────────────────────────────────


class TestRequestActor:
    def test_actor_mapping(self, server):
        actor = server.McpActor
        assert server.request_actor("iris") == actor(source="iris", name="iris")
        assert server.request_actor("agent", "steward") == actor(source="agent:steward", name="steward")
        assert server.request_actor("admin") == actor(source="agent:admin", name="admin")
        for audience in ("experiment", "observer", None):
            assert server.request_actor(audience) is None
        assert server.request_actor("agent", None) is None
        with pytest.raises(dataclasses.FrozenInstanceError):
            server.request_actor("iris").source = "agent:admin"

    @pytest.mark.parametrize(
        ("token", "expected"),
        [
            (IRIS_TOKEN, ("iris", "iris")),
            (STEWARD_TOKEN, ("agent:steward", "steward")),
            (SCOUT_TOKEN, ("agent:scout", "scout")),
            (ADMIN_TOKEN, ("agent:admin", "admin")),
        ],
        ids=["iris", "steward", "scout", "admin"],
    )
    def test_call_tool_publishes_the_actor_for_the_call_only(self, server, two_agents, monkeypatch, token, expected):
        seen = []

        async def climate():
            seen.append(server.current_actor())
            return "ok"

        monkeypatch.setitem(server.mcp._functions, "climate", climate)
        _as_caller(server, monkeypatch, token)
        assert server.current_actor() is None
        assert _run(server.mcp.call_tool("climate", {})) == "ok"
        assert [(actor.source, actor.name) for actor in seen] == [expected]
        assert server.current_actor() is None

    @pytest.mark.parametrize("mode", ["off", "log"])
    def test_unresolved_callers_have_no_actor(self, server, two_agents, monkeypatch, mode):
        seen = []

        async def climate():
            seen.append(server.current_actor())
            return "ok"

        monkeypatch.setenv("VERDIFY_MCP_AUTH_MODE", mode)
        monkeypatch.setitem(server.mcp._functions, "climate", climate)
        _as_caller(server, monkeypatch, "fixture-unknown-token" if mode == "log" else STEWARD_TOKEN)
        assert _run(server.mcp.call_tool("climate", {})) == "ok"
        assert seen == [None]

    def test_experiment_audience_has_no_actor(self, server, monkeypatch):
        seen = []

        async def climate():
            seen.append(server.current_actor())
            return "ok"

        monkeypatch.setenv("VERDIFY_MCP_TOKEN_EXPERIMENT", "fixture-experiment-token")
        monkeypatch.setitem(server.mcp._functions, "climate", climate)
        _as_caller(server, monkeypatch, "fixture-experiment-token")
        assert _run(server.mcp.call_tool("climate", {})) == "ok"
        assert seen == [None]

    def test_actor_is_reset_when_the_tool_raises(self, server, two_agents, monkeypatch):
        async def climate():
            raise RuntimeError("fixture failure")

        monkeypatch.setitem(server.mcp._functions, "climate", climate)
        _as_caller(server, monkeypatch, STEWARD_TOKEN)
        with pytest.raises(RuntimeError):
            _run(server.mcp.call_tool("climate", {}))
        assert server.current_actor() is None

    def test_audit_line_attributes_each_agent_and_carries_no_token(self, server, two_agents, monkeypatch, caplog):
        async def climate():
            return "ok"

        monkeypatch.setitem(server.mcp._functions, "climate", climate)
        caplog.set_level("INFO", logger="verdify.mcp.audit")
        for token in (STEWARD_TOKEN, SCOUT_TOKEN, ADMIN_TOKEN, IRIS_TOKEN):
            _as_caller(server, monkeypatch, token)
            _run(server.mcp.call_tool("climate", {}))
        rows = [json.loads(record.getMessage()) for record in caplog.records if record.name == "verdify.mcp.audit"]
        assert [(row["audience"], row["actor"]) for row in rows] == [
            ("agent", "agent:steward"),
            ("agent", "agent:scout"),
            ("admin", "agent:admin"),
            ("iris", "iris"),
        ]
        assert all(row["event"] == "mcp_tool_call" and row["result"] == "completed" for row in rows)
        assert not any(token in caplog.text for token in ALL_FIXTURE_TOKENS)


# ─── /readyz ───────────────────────────────────────────────────────────────


class _ReadyConnection:
    async def fetchval(self, sql: str):
        assert sql == "SELECT 1"
        return 1

    async def close(self) -> None:
        pass


@pytest.fixture()
def ready_db(server, monkeypatch):
    async def db():
        return _ReadyConnection()

    monkeypatch.setattr(server, "_db", db)


def _readyz(server):
    response = _run(server.mcp_ready(None))
    return response.status_code, json.loads(response.body), response.body.decode()


class TestReadyz:
    def test_two_agents_are_listed_by_name_only(self, server, two_agents, ready_db):
        status, payload, body = _readyz(server)
        assert status == 200 and payload["ready"] is True
        assert payload["auth_agents_configured"] == ["scout", "steward"]
        assert payload["auth_agent_keys_rejected"] == []
        assert payload["auth_audiences_configured"] == ["admin", "iris"]
        assert not any(token in body for token in ALL_FIXTURE_TOKENS)

    def test_no_agents_mounted_is_ready(self, server, ready_db):
        status, payload, _body = _readyz(server)
        assert status == 200
        assert payload["auth_agents_configured"] == [] and payload["auth_agent_keys_rejected"] == []

    def test_rejected_agent_files_are_listed_and_leave_readyz_at_200(self, server, volume, ready_db):
        volume.root.mkdir(parents=True, exist_ok=True)
        (volume.root / "admin").write_text("fixture-reserved-token")
        (volume.root / "Bad Name").write_text("fixture-bad-name-token")
        (volume.root / "iris-copy").write_text(IRIS_TOKEN)
        status, payload, body = _readyz(server)
        assert status == 200 and payload["ready"] is True
        assert payload["auth_agents_configured"] == []
        assert payload["auth_agent_keys_rejected"] == ["Bad Name", "admin", "iris-copy"]
        assert payload["auth_audiences_configured"] == ["admin", "iris"]
        assert "fixture-reserved-token" not in body and IRIS_TOKEN not in body

    def test_agent_token_env_var_is_unrecognized_and_not_ready(self, server, two_agents, ready_db, monkeypatch):
        monkeypatch.setenv("VERDIFY_MCP_TOKEN_AGENT", "fixture-env-agent-token")
        status, payload, body = _readyz(server)
        assert status == 503 and payload["ready"] is False
        assert payload["auth_unrecognized_token_envs"] == ["VERDIFY_MCP_TOKEN_AGENT"]
        assert payload["auth_misconfigured"] is True
        assert "fixture-env-agent-token" not in body


# ─── alerts attribution ────────────────────────────────────────────────────


class AlertConnection:
    def __init__(self, *, alert_type: str = "vpd_high", source: str = "system") -> None:
        self.alert_type = alert_type
        self.source = source
        self.statements: list[tuple[str, tuple]] = []
        self.closed = False

    async def fetchrow(self, sql, *args):
        self.statements.append((sql, args))
        if sql.startswith("UPDATE alert_log SET acknowledged_at"):
            return {"id": args[0], "disposition": "acknowledged"}
        if sql.startswith("SELECT alert_type, source FROM alert_log"):
            return {"alert_type": self.alert_type, "source": self.source}
        raise AssertionError(f"unexpected fetchrow: {sql}")

    async def fetch(self, sql, *args):
        self.statements.append((sql, args))
        return []

    async def execute(self, sql, *args):
        self.statements.append((sql, args))
        return "UPDATE 1"

    async def close(self) -> None:
        self.closed = True

    def updates(self) -> list[tuple[str, tuple]]:
        return [(sql, args) for sql, args in self.statements if sql.startswith("UPDATE")]


@pytest.fixture()
def alert_db(server, monkeypatch):
    connection = AlertConnection()

    async def db():
        return connection

    monkeypatch.setattr(server, "_db", db)
    return connection


class TestAlertsAttribution:
    @pytest.mark.parametrize(
        ("token", "recorded"),
        [(STEWARD_TOKEN, "agent:steward"), (SCOUT_TOKEN, "agent:scout"), (ADMIN_TOKEN, "agent:admin")],
        ids=["steward", "scout", "admin"],
    )
    def test_acknowledge_records_the_caller_not_the_data(
        self, server, two_agents, alert_db, monkeypatch, token, recorded
    ):
        _as_caller(server, monkeypatch, token)
        arguments = {"action": "acknowledge", "alert_id": 7, "data": json.dumps({"acknowledged_by": "jason"})}
        result = json.loads(_run(server.mcp.call_tool("alerts", arguments)))
        assert result == {"ok": True, "alert_id": 7, "action": "acknowledged"}
        ((_sql, args),) = alert_db.updates()
        assert args == (7, recorded)

    def test_iris_acknowledge_still_records_iris(self, server, alert_db, monkeypatch):
        _as_caller(server, monkeypatch, IRIS_TOKEN)
        _run(server.mcp.call_tool("alerts", {"action": "acknowledge", "alert_id": 3, "data": ""}))
        ((_sql, args),) = alert_db.updates()
        assert args == (3, "iris")

    def test_resolve_records_the_caller_and_keeps_the_resolution_text(self, server, two_agents, alert_db, monkeypatch):
        _as_caller(server, monkeypatch, STEWARD_TOKEN)
        data = json.dumps({"resolved_by": "jason", "resolution": "fixture fixed the fan"})
        result = json.loads(_run(server.mcp.call_tool("alerts", {"action": "resolve", "alert_id": 9, "data": data})))
        assert result == {"ok": True, "alert_id": 9, "action": "resolved"}
        ((_sql, args),) = alert_db.updates()
        assert args == (9, "agent:steward", "fixture fixed the fan")

    @pytest.mark.parametrize(
        ("token", "resolution"),
        [(IRIS_TOKEN, ("iris", "Resolved by Iris")), (ADMIN_TOKEN, ("agent:admin", "Resolved by agent:admin"))],
        ids=["iris", "admin"],
    )
    def test_default_resolution_names_the_caller(self, server, alert_db, monkeypatch, token, resolution):
        _as_caller(server, monkeypatch, token)
        _run(server.mcp.call_tool("alerts", {"action": "resolve", "alert_id": 4, "data": ""}))
        ((_sql, args),) = alert_db.updates()
        assert args == (4, *resolution)

    @pytest.mark.parametrize("action", ["acknowledge", "resolve"])
    def test_writes_without_a_resolved_caller_are_forbidden(self, server, alert_db, action):
        # A direct call has no request actor (as auth mode off or an unresolved token would).
        data = json.dumps({"acknowledged_by": "iris", "resolved_by": "iris"})
        result = json.loads(_run(server.alerts(action, 5, data)))
        assert result == {"error": "forbidden: no resolved caller"}
        assert alert_db.updates() == []
        assert alert_db.closed

    def test_list_needs_no_actor(self, server, alert_db):
        assert json.loads(_run(server.alerts("list"))) == []

    def test_semantic_dependency_refusal_runs_before_the_actor(self, server, two_agents, monkeypatch):
        connection = AlertConnection(alert_type="planner_tool_dependency_failed", source="mcp")

        async def db():
            return connection

        monkeypatch.setattr(server, "_db", db)
        _as_caller(server, monkeypatch, STEWARD_TOKEN)
        result = json.loads(_run(server.mcp.call_tool("alerts", {"action": "resolve", "alert_id": 12, "data": ""})))
        assert "successful same-tool" in result["error"]
        assert connection.updates() == []


# ─── Rendered manifests ────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def rendered():
    output = subprocess.run(
        ["kustomize", "build", str(PROD_OVERLAY)], cwd=REPO_ROOT, check=True, capture_output=True, text=True
    ).stdout
    return [document for document in yaml.safe_load_all(output) if isinstance(document, dict)]


def _named(rendered, kind: str, name: str) -> dict:
    return next(x for x in rendered if x.get("kind") == kind and x.get("metadata", {}).get("name") == name)


def _selected(selector: dict, labels: dict) -> bool:
    for expression in selector.get("matchExpressions", []):
        key, operator, values = expression["key"], expression["operator"], expression.get("values", [])
        matched = {
            "In": lambda: labels.get(key) in values,
            "NotIn": lambda: labels.get(key) not in values,
            "Exists": lambda: key in labels,
            "DoesNotExist": lambda: key not in labels,
        }[operator]()
        if not matched:
            return False
    return all(labels.get(key) == value for key, value in selector.get("matchLabels", {}).items())


MCP_POD_LABELS = {"app.kubernetes.io/component": "mcp", "app.kubernetes.io/part-of": "verdify"}


def _admitted_to_mcp(rendered, namespace: str, pod_labels: dict, port: int = 8000) -> bool:
    """Whether any rendered ingress policy on MCP admits this pod (pod IP in 10.42.0.0/16)."""
    import ipaddress

    pod_ip = ipaddress.ip_address("10.42.7.7")
    namespace_labels = {"kubernetes.io/metadata.name": namespace}
    for policy in rendered:
        if policy.get("kind") != "NetworkPolicy" or "Ingress" not in policy["spec"].get("policyTypes", []):
            continue
        if not _selected(policy["spec"]["podSelector"], MCP_POD_LABELS):
            continue
        policy_namespace = policy["metadata"].get("namespace")
        for rule in policy["spec"].get("ingress", []):
            ports = rule.get("ports")
            if ports and not any(p.get("port") == port and p.get("protocol", "TCP") == "TCP" for p in ports):
                continue
            peers = rule.get("from")
            if not peers:
                return True
            for peer in peers:
                if "ipBlock" in peer:
                    if pod_ip in ipaddress.ip_network(peer["ipBlock"]["cidr"]):
                        return True
                    continue
                namespace_ok = (
                    namespace == policy_namespace
                    if "namespaceSelector" not in peer
                    else _selected(peer["namespaceSelector"], namespace_labels)
                )
                pod_ok = "podSelector" not in peer or _selected(peer["podSelector"], pod_labels)
                if namespace_ok and pod_ok:
                    return True
    return False


class TestRenderedNetworkAndMount:
    def test_named_agent_policy_is_exact_additive_and_ingress_only(self, rendered):
        policy = _named(rendered, "NetworkPolicy", "allow-mcp-from-named-agents")
        assert policy["metadata"]["namespace"] == "verdify-prod"
        assert policy["spec"] == {
            "podSelector": {"matchLabels": {"app.kubernetes.io/component": "mcp"}},
            "policyTypes": ["Ingress"],
            "ingress": [
                {
                    "from": [
                        {
                            "namespaceSelector": {},
                            "podSelector": {"matchLabels": {"verdify.ai/mcp-agent": "true"}},
                        }
                    ],
                    "ports": [{"protocol": "TCP", "port": 8000}],
                }
            ],
        }
        internal = _named(rendered, "NetworkPolicy", "allow-mcp-internal")
        assert internal["spec"]["ingress"] == [
            {
                "from": [
                    {
                        "podSelector": {
                            "matchExpressions": [
                                {
                                    "key": "app.kubernetes.io/component",
                                    "operator": "In",
                                    "values": ["api", "ingestor", "hermes-iris"],
                                }
                            ]
                        }
                    }
                ],
                "ports": [{"protocol": "TCP", "port": 8000}],
            }
        ]
        egress_on_mcp = [
            x["metadata"]["name"]
            for x in rendered
            if x.get("kind") == "NetworkPolicy"
            and "Egress" in x["spec"].get("policyTypes", [])
            and _selected(x["spec"]["podSelector"], MCP_POD_LABELS)
        ]
        assert egress_on_mcp == ["verdify-mcp-egress"]

    @pytest.mark.parametrize("namespace", ["verdify-prod", "agent-fleet"])
    def test_only_the_labelled_pod_spec_reaches_mcp(self, rendered, namespace):
        probe = {"app.kubernetes.io/name": "verdify-mcp-agent-probe"}
        assert _admitted_to_mcp(rendered, namespace, {**probe, "verdify.ai/mcp-agent": "true"})
        assert not _admitted_to_mcp(rendered, namespace, probe)
        assert not _admitted_to_mcp(rendered, namespace, {**probe, "verdify.ai/mcp-agent": "false"})
        assert not _admitted_to_mcp(rendered, namespace, {**probe, "verdify.ai/mcp-agent": "true"}, port=5432)

    def test_mcp_mounts_the_optional_agent_secret_without_subpath(self, server, rendered):
        deployment = _named(rendered, "Deployment", "verdify-mcp")
        spec = deployment["spec"]["template"]["spec"]
        assert {
            "name": "mcp-agents",
            "secret": {"secretName": "verdify-mcp-agents", "optional": True, "defaultMode": 0o440},
        } in spec["volumes"]
        (container,) = spec["containers"]
        mounts = [mount for mount in container["volumeMounts"] if mount["name"] == "mcp-agents"]
        assert mounts == [{"name": "mcp-agents", "mountPath": server.DEFAULT_MCP_AGENT_KEYS_DIR, "readOnly": True}]
        assert spec["securityContext"]["fsGroup"] == 1000
        assert spec["automountServiceAccountToken"] is False
        env = {entry["name"]: entry for entry in container["env"]}
        assert "VERDIFY_MCP_TOKEN_AGENT" not in env and "VERDIFY_MCP_AGENT_KEYS_DIR" not in env
        assert env["VERDIFY_MCP_TOKEN_IRIS"]["valueFrom"]["secretKeyRef"] == {
            "name": "verdify-hermes",
            "key": "VERDIFY_MCP_TOKEN",
        }
        assert not any(x.get("kind") == "Secret" for x in rendered)
