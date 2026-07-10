from __future__ import annotations

import ast
import asyncio
import importlib.util
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from uuid import uuid4

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from verdify_schemas import PublicPlannerDelivery, PublicPlannerHealthResponse

_MISSING_MODULE = object()
_MCP_STUB_MODULES = ("mcp", "mcp.server", "mcp.server.fastmcp")


def _install_fastmcp_test_stub() -> None:
    """Provide only the registration surface this isolated module test needs.

    The logic CI environment deliberately does not install the MCP runtime, and
    the repository's ``mcp/`` namespace shadows that distribution during test
    collection.  These tests call tool functions directly and monkeypatch all
    I/O, so a deterministic registration stub is the honest dependency boundary.
    """

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
        spec = importlib.util.spec_from_file_location("verdify_mcp_server_health_test", path)
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


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _ReadyConnection:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.closed = False

    async def fetchval(self, sql: str):
        assert sql == "SELECT 1"
        if self.fail:
            raise OSError("temporary DB refusal")
        return 1

    async def close(self):
        self.closed = True


class _RequiredTunableConnection:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self.closed = False

    def transaction(self):
        return _Transaction()

    async def fetchrow(self, sql: str, *_args):
        if "FROM plan_delivery_log" in sql:
            return {
                "id": 1,
                "trigger_id": "00000000-0000-0000-0000-000000000001",
                "event_type": "SUNRISE",
                "event_label": "Morning planning cycle",
                "status": "pending",
                "instance": "local",
            }
        assert "FROM planner_trigger_ledger" in sql
        return {
            "id": 2,
            "trigger_id": "00000000-0000-0000-0000-000000000001",
            "plan_delivery_log_id": 1,
            "event_type": "SUNRISE",
            "status": "delivered",
            "expected_action": "set_plan",
        }

    async def fetchval(self, sql: str, *args):
        if "INSERT INTO setpoint_plan" in sql:
            raise AssertionError(f"setpoint write reached after wrong action: {sql}")
        self.executed.append((sql, args))
        return 1 if "UPDATE plan_delivery_log" in sql else 2

    async def execute(self, sql: str, *args):
        self.executed.append((sql, args))

    async def close(self):
        self.closed = True


class _RequiredAckConnection:
    def __init__(self, event_type: str = "SUNSET") -> None:
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self.closed = False
        self.event_type = event_type

    def transaction(self):
        return _Transaction()

    async def fetchrow(self, sql: str, *_args):
        if "FROM plan_delivery_log" in sql:
            return {
                "id": 1,
                "trigger_id": "00000000-0000-0000-0000-000000000001",
                "event_type": self.event_type,
                "event_label": f"{self.event_type.title()} planning cycle",
                "instance": "local",
                "status": "pending",
            }
        if "FROM planner_trigger_ledger" in sql:
            return {
                "id": 2,
                "trigger_id": "00000000-0000-0000-0000-000000000001",
                "plan_delivery_log_id": 1,
                "event_type": self.event_type,
                "status": "delivered",
                "expected_action": "set_plan",
            }
        assert "UPDATE plan_delivery_log" in sql
        return {
            "id": 1,
            "event_type": self.event_type,
            "instance": "local",
            "delivered_at": datetime.now(UTC),
            "status": "neutral_fallback",
        }

    async def fetchval(self, sql: str, *args):
        self.executed.append((sql, args))
        return 2

    async def close(self):
        self.closed = True


class _AttemptConnection:
    def __init__(self, delivery: dict, ledger: dict | None) -> None:
        self.delivery = delivery
        self.ledger = ledger
        self.queries: list[str] = []

    async def fetchrow(self, sql: str, *_args):
        self.queries.append(sql)
        if "FROM plan_delivery_log" in sql:
            return self.delivery
        assert "FROM planner_trigger_ledger" in sql
        return self.ledger


def _trigger_payload() -> dict:
    now = datetime.now(UTC)
    return {
        "id": 1,
        "event_type": "SUNRISE",
        "event_label": "Morning planning cycle",
        "instance": "local",
        "expected_at": now - timedelta(minutes=10),
        "due_at": now + timedelta(minutes=20),
        "delivered_at": now - timedelta(minutes=9),
        "resolved_at": now - timedelta(minutes=5),
        "status": "plan_written",
        "expected_action": "set_plan",
        "trigger_id": "00000000-0000-0000-0000-000000000000",
        "resulting_plan_id": "iris-test",
    }


def test_public_planner_health_schema_includes_status_surface_fields():
    now = datetime.now(UTC)
    trigger = _trigger_payload()
    delivery = {
        "id": 1,
        "event_type": "FORECAST_DEVIATION",
        "event_label": "weather miss",
        "delivered_at": now,
        "status": "acked",
        "instance": "local",
        "session_key": "hermes:iris:main:trigger:00000000-0000-0000-0000-000000000000",
        "wake_mode": "now",
        "gateway_status": 202,
        "hermes_run_id": "run_test",
        "trigger_id": "00000000-0000-0000-0000-000000000000",
        "resulting_plan_id": None,
        "plan_written_at": None,
        "planner_gateway": "hermes-iris",
        "planner_model_label": "hermes-iris/openai:gpt-5.5/high",
    }

    response = PublicPlannerHealthResponse.model_validate(
        {
            "generated_at": now,
            "overall_status": "ok",
            "missed_expected_count": 0,
            "overdue_delivered_count": 0,
            "required_failure_count": 0,
            "recent_expected_count": 1,
            "resolved_count": 1,
            "latest_required": [trigger],
            "last_expected_trigger": trigger,
            "last_delivered_trigger": trigger,
            "last_resolved_trigger": trigger,
            "pending_by_sla_age": {
                "within_sla": 2,
                "overdue_lt_15m": 0,
                "overdue_15m_1h": 0,
                "overdue_gt_1h": 0,
            },
            "current_session_key": "hermes:iris:main:trigger:00000000-0000-0000-0000-000000000000",
            "current_model_label": "hermes-iris/openai:gpt-5.5/high",
            "current_hermes_run_id": "run_test",
            "active_plan_range_violation_count": 0,
            "recent_deliveries": [delivery],
            "recent_triggers": [trigger],
        }
    )

    assert response.last_expected_trigger is not None
    assert response.last_delivered_trigger is not None
    assert response.last_resolved_trigger is not None
    assert response.pending_by_sla_age["within_sla"] == 2
    assert response.current_model_label == "hermes-iris/openai:gpt-5.5/high"
    assert response.recent_deliveries[0].planner_gateway == "hermes-iris"
    assert response.active_plan_range_violation_count == 0


def test_public_planner_delivery_schema_proves_gateway_model_session():
    delivery = PublicPlannerDelivery.model_validate(
        {
            "id": 1,
            "event_type": "MANUAL",
            "delivered_at": datetime.now(UTC),
            "status": "acked",
            "session_key": "hermes:iris:main:trigger:00000000-0000-0000-0000-000000000000",
            "hermes_run_id": "run_test",
            "planner_gateway": "hermes-iris",
            "planner_model_label": "hermes-iris/openai:gpt-5.5/high",
        }
    )

    assert delivery.session_key is not None
    assert delivery.hermes_run_id == "run_test"
    assert delivery.planner_model_label == "hermes-iris/openai:gpt-5.5/high"


def test_public_planner_health_endpoint_queries_i_p1_1_sources():
    api_source = Path("api/main.py").read_text()
    endpoint = api_source[api_source.index("async def public_planner_health") :]

    for expected in (
        "last_expected_trigger",
        "last_delivered_trigger",
        "last_resolved_trigger",
        "pending_by_sla_age",
        "current_session_key",
        "current_model_label",
        "current_hermes_run_id",
        "active_plan_range_violation_count",
        "recent_deliveries",
        "plan_delivery_log",
        "session_key",
    ):
        assert expected in endpoint
    assert "registry_value_error" in api_source
    assert "planner_gateway" in api_source
    assert "planner_model_label" in api_source


def _hermes_config_documents() -> tuple[dict, dict, str]:
    manifest = yaml.safe_load((REPO_ROOT / "deploy/k8s/components/hermes-iris/hermes-config.yaml").read_text())
    profile = yaml.safe_load(manifest["data"]["config.yaml"])
    readiness_source = manifest["data"]["tool-readiness.py"]
    return manifest, profile, readiness_source


def _readiness_required_tools(source: str) -> set[str]:
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "REQUIRED" for target in node.targets
        ):
            value = ast.literal_eval(node.value)
            return set(value)
    raise AssertionError("Hermes readiness REQUIRED set is missing")


def _hermes_probe_namespace(source: str) -> dict[str, object]:
    namespace = {"__name__": "hermes_probe_test"}
    exec(compile(source, "tool-readiness.py", "exec"), namespace)  # noqa: S102
    return namespace


def test_hermes_readiness_covers_the_exact_configured_mcp_allowlist(mcp_server):
    _manifest, profile, readiness_source = _hermes_config_documents()
    configured = set(profile["mcp_servers"]["verdify_greenhouse"]["tools"]["include"])

    assert _readiness_required_tools(readiness_source) == configured
    assert mcp_server.HERMES_REQUIRED_TOOLS == configured
    assert {"set_plan", "set_tunable", "acknowledge_trigger"} <= configured


def test_hermes_readiness_parses_exact_tool_names_not_substrings():
    manifest, _profile, source = _hermes_config_documents()
    namespace = _hermes_probe_namespace(source)
    configured_tool_names = namespace["configured_tool_names"]
    config_text = manifest["data"]["config.yaml"]
    without_lessons = config_text.replace("        - lessons\n", "")

    configured = configured_tool_names(without_lessons)

    assert "lessons_search" in configured
    assert "lessons" not in configured


def test_hermes_client_state_ignores_persistent_prestart_history(tmp_path):
    _manifest, _profile, source = _hermes_config_documents()
    namespace = _hermes_probe_namespace(source)
    client_state = namespace["hermes_client_state"]
    started_at = datetime(2026, 7, 10, 12, tzinfo=UTC).timestamp()
    log_path = tmp_path / "agent.log"
    log_path.write_text(
        "2026-07-10 11:59:00,000 WARNING MCP server 'verdify_greenhouse' "
        "failed after 5 reconnection attempts, giving up: old\n"
        "2026-07-10 12:00:01,000 INFO MCP server 'verdify_greenhouse' "
        "(HTTP): registered 23 tool(s): climate\n"
    )

    state = client_state([str(log_path)], started_at)

    assert state["state"] == "connected"
    assert state["fatal"] is False


def test_hermes_client_state_disconnect_then_recover(tmp_path):
    _manifest, _profile, source = _hermes_config_documents()
    namespace = _hermes_probe_namespace(source)
    client_state = namespace["hermes_client_state"]
    started_at = datetime(2026, 7, 10, 12, tzinfo=UTC).timestamp()
    log_path = tmp_path / "agent.log"
    log_path.write_text(
        "2026-07-10 12:00:01,000 INFO MCP server 'verdify_greenhouse' "
        "(HTTP): registered 23 tool(s): climate\n"
        "2026-07-10 12:01:00,000 WARNING MCP server 'verdify_greenhouse' "
        "connection lost (attempt 1/5), reconnecting in 1s: reset\n"
    )
    disconnected = client_state([str(log_path)], started_at)
    with log_path.open("a") as stream:
        stream.write(
            "2026-07-10 12:01:02,000 INFO MCP server 'verdify_greenhouse' (HTTP): registered 23 tool(s): climate\n"
        )

    recovered = client_state([str(log_path)], started_at)

    assert disconnected["state"] == "disconnected"
    assert disconnected["fatal"] is False
    assert recovered["state"] == "connected"
    assert recovered["fatal"] is False


def test_hermes_client_state_fatal_reconnect_exhaustion(tmp_path):
    _manifest, _profile, source = _hermes_config_documents()
    namespace = _hermes_probe_namespace(source)
    client_state = namespace["hermes_client_state"]
    started_at = datetime(2026, 7, 10, 12, tzinfo=UTC).timestamp()
    log_path = tmp_path / "errors.log"
    log_path.write_text(
        "2026-07-10 12:00:01,000 INFO MCP server 'verdify_greenhouse' "
        "(HTTP): registered 23 tool(s): climate\n"
        "2026-07-10 12:02:00,000 WARNING MCP server 'verdify_greenhouse' "
        "failed after 5 reconnection attempts, giving up: reset\n"
    )

    state = client_state([str(log_path)], started_at)

    assert state["state"] == "fatal"
    assert state["fatal"] is True


def test_actual_python3_liveness_probe_restarts_only_on_poststart_fatal(tmp_path):
    manifest, _profile, source = _hermes_config_documents()
    probe_path = tmp_path / "tool-readiness.py"
    log_path = tmp_path / "agent.log"
    probe_path.write_text(source)
    started_at = datetime(2026, 7, 10, 12, tzinfo=UTC).timestamp()
    log_path.write_text(
        "2026-07-10 11:59:00,000 WARNING MCP server 'verdify_greenhouse' "
        "failed after 5 reconnection attempts, giving up: old\n"
    )
    env = {
        **os.environ,
        "HERMES_PROCESS_START_EPOCH": str(started_at),
        "HERMES_CLIENT_LOG_PATHS": str(log_path),
    }
    live = subprocess.run(
        ["python3", str(probe_path), "--mode", "liveness"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    with log_path.open("a") as stream:
        stream.write(
            "2026-07-10 12:02:00,000 WARNING MCP server 'verdify_greenhouse' "
            "failed after 5 reconnection attempts, giving up: reset\n"
        )
    fatal = subprocess.run(
        ["python3", str(probe_path), "--mode", "liveness"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    container = manifest  # retain the rendered ConfigMap document for the assertion below
    workloads = yaml.safe_load_all((REPO_ROOT / "deploy/k8s/components/hermes-iris/hermes-iris.yaml").read_text())
    workload = next(document for document in workloads if document.get("kind") == "Deployment")
    probes = workload["spec"]["template"]["spec"]["containers"][0]

    assert live.returncode == 0
    assert fatal.returncode == 1
    assert json.loads(fatal.stdout)["client"]["fatal"] is True
    assert container["data"]["tool-readiness.py"] == source
    assert probes["readinessProbe"]["exec"]["command"][0] == "python3"
    assert probes["livenessProbe"]["exec"]["command"][-1] == "liveness"


def test_materializer_runs_one_canonical_final_normalization_per_output(mcp_server, monkeypatch):
    calls: list[str] = []
    original = mcp_server.normalize_planner_value

    def normalize(parameter: str, value: float) -> float:
        calls.append(parameter)
        return original(parameter, value)

    monkeypatch.setattr(mcp_server, "normalize_planner_value", normalize)
    waypoints, _records = mcp_server._materialize_climate_intent_waypoints(
        [
            {
                "ts": "2026-07-10T06:00:00-06:00",
                "climate_intent": {},
                "reason": "canonical normalization proof",
            }
        ],
        {},
    )
    params = set(waypoints[0]["params"])

    assert set(calls) == params
    assert len(calls) == len(params)


@pytest.mark.asyncio
async def test_mcp_readiness_fails_closed_when_a_required_tool_is_missing(mcp_server, monkeypatch):
    missing = "set_plan"
    available = mcp_server.HERMES_REQUIRED_TOOLS - {missing}
    connection = _ReadyConnection()

    async def list_tools():
        return [SimpleNamespace(name=name) for name in available]

    async def db():
        return connection

    monkeypatch.setattr(mcp_server.mcp, "list_tools", list_tools)
    monkeypatch.setattr(mcp_server, "_db", db)
    response = await mcp_server.mcp_ready(None)
    payload = json.loads(response.body)

    assert response.status_code == 503
    assert payload["ready"] is False
    assert payload["missing_tools"] == [missing]
    assert connection.closed is True


@pytest.mark.asyncio
async def test_mcp_readiness_recovers_after_prolonged_db_refusal(mcp_server, monkeypatch):
    attempts = iter([True, True, True, False])

    async def list_tools():
        return [SimpleNamespace(name=name) for name in mcp_server.HERMES_REQUIRED_TOOLS]

    async def db():
        return _ReadyConnection(fail=next(attempts))

    monkeypatch.setattr(mcp_server.mcp, "list_tools", list_tools)
    monkeypatch.setattr(mcp_server, "_db", db)
    responses = [await mcp_server.mcp_ready(None) for _ in range(4)]

    assert [response.status_code for response in responses] == [503, 503, 503, 200]
    assert json.loads(responses[-1].body)["ready"] is True


@pytest.mark.asyncio
async def test_mcp_db_connections_enforce_server_side_query_budget(mcp_server, monkeypatch):
    captured: dict[str, object] = {}
    connection = object()

    async def connect(dsn, **kwargs):
        captured["dsn"] = dsn
        captured.update(kwargs)
        return connection

    monkeypatch.setattr(mcp_server.asyncpg, "connect", connect)
    result = await mcp_server._db()

    assert result is connection
    assert captured["dsn"] == mcp_server.DB_DSN
    assert captured["server_settings"] == {
        "application_name": "verdify-mcp",
        "statement_timeout": f"{mcp_server.MCP_DB_STATEMENT_TIMEOUT_MS}ms",
    }
    assert mcp_server.MCP_DB_STATEMENT_TIMEOUT_MS == 15000


@pytest.mark.asyncio
async def test_required_set_plan_rejects_set_tunable_without_writing_a_waypoint(mcp_server, monkeypatch):
    connection = _RequiredTunableConnection()

    async def db():
        return connection

    monkeypatch.setattr(mcp_server, "_db", db)
    result = json.loads(
        await mcp_server.set_tunable(
            parameter="mister_vpd_weight",
            value=1.5,
            trigger_id="00000000-0000-0000-0000-000000000001",
            planner_instance="local",
        )
    )

    assert result["status"] == "wrong_action"
    assert result["terminal_action"] == "wrong_action"
    assert len(connection.executed) == 2
    assert all("INSERT INTO setpoint_plan" not in sql for sql, _args in connection.executed)
    assert connection.closed is True


@pytest.mark.asyncio
async def test_set_tunable_rejects_value_outside_stricter_firmware_bound(mcp_server, monkeypatch):
    from verdify_schemas.tunable_registry import REGISTRY

    original = REGISTRY["mister_vpd_weight"]
    monkeypatch.setitem(
        REGISTRY,
        "mister_vpd_weight",
        original.model_copy(update={"min": 0.5, "max": 3.0, "fw_clamp_lo": 1.0, "fw_clamp_hi": 2.0}),
    )
    result = json.loads(
        await mcp_server.set_tunable(
            parameter="mister_vpd_weight",
            value=2.5,
            trigger_id="00000000-0000-0000-0000-000000000001",
            planner_instance="local",
        )
    )

    assert result["error"] == "Tunable value outside strict planner/firmware bounds"
    assert result["nearest_safe"] == 2.0


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type", ["SUNRISE", "SUNSET"])
async def test_required_set_plan_accepts_only_explicit_neutral_ack_fallback(mcp_server, monkeypatch, event_type):
    connection = _RequiredAckConnection(event_type)

    async def db():
        return connection

    monkeypatch.setattr(mcp_server, "_db", db)
    result = json.loads(
        await mcp_server.acknowledge_trigger(
            trigger_id="00000000-0000-0000-0000-000000000001",
            reason="MCP input unavailable; deterministic firmware remains authoritative",
            planner_instance="local",
            neutral_fallback=True,
        )
    )

    assert result["status"] == "neutral_fallback"
    assert result["terminal_action"] == "neutral_fallback"
    assert result["neutral"] is True
    assert len(connection.executed) == 1
    assert connection.closed is True


@pytest.mark.asyncio
async def test_current_attempt_fence_rejects_timed_out_delivery(mcp_server):
    connection = _AttemptConnection(
        {
            "id": 1,
            "trigger_id": "00000000-0000-0000-0000-000000000001",
            "event_type": "SUNRISE",
            "event_label": "Morning planning cycle",
            "status": "timed_out",
            "instance": "local",
        },
        None,
    )

    _delivery, _ledger, error = await mcp_server._lock_current_planner_attempt(
        connection,
        "00000000-0000-0000-0000-000000000001",
        "local",
    )

    assert error["error"] == "trigger_id is not the current writable attempt"
    assert error["status"] == "timed_out"
    assert len(connection.queries) == 1
    assert "FOR UPDATE" in connection.queries[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type", ["SUNSET", "WEEKLY", "TRANSITION"])
async def test_current_attempt_fence_rejects_old_scheduled_attempt_after_retry(mcp_server, event_type):
    connection = _AttemptConnection(
        {
            "id": 1,
            "trigger_id": "00000000-0000-0000-0000-000000000001",
            "event_type": event_type,
            "event_label": "Scheduled planning cycle",
            "status": "pending",
            "instance": "local",
        },
        None,
    )

    _delivery, _ledger, error = await mcp_server._lock_current_planner_attempt(
        connection,
        "00000000-0000-0000-0000-000000000001",
        "local",
    )

    assert error["error"] == "scheduled trigger attempt is stale or superseded"
    assert len(connection.queries) == 2
    assert all("FOR UPDATE" in query for query in connection.queries)


def test_all_terminal_actions_share_locked_attempt_and_conditional_pair_fences():
    source = (REPO_ROOT / "mcp" / "server.py").read_text()
    for function_name, next_name in (
        ("set_tunable", "plan_status"),
        ("set_plan", "acknowledge_trigger"),
        ("acknowledge_trigger", "plan_evaluate"),
    ):
        body = source[source.index(f"async def {function_name}") : source.index(f"async def {next_name}")]
        assert "_lock_current_planner_attempt(" in body
        assert "plan_delivery_log_id" in body
        assert "status = 'delivered'" in body
        assert "RETURNING id" in body
    set_plan_body = source[source.index("async def set_plan") : source.index("async def acknowledge_trigger")]
    assert 'db_now = await conn.fetchval("SELECT now()")' in set_plan_body
    assert set_plan_body.index("plan_current_coverage_error(plan, db_now)") < set_plan_body.index("UPDATE plan_journal")


@pytest.mark.asyncio
@pytest.mark.parametrize("winner_status", ["plan_written", "action_completed", "acked"])
async def test_two_connection_terminal_winner_fences_late_competitor(mcp_server, winner_status):
    dsn = os.environ.get("PLANNER_FENCE_TEST_DSN")
    if not dsn:
        pytest.skip("PLANNER_FENCE_TEST_DSN is required for real two-connection fencing proof")
    asyncpg = pytest.importorskip("asyncpg")
    schema = f"planner_fence_{uuid4().hex}"
    admin = await asyncpg.connect(dsn)
    trigger_id = uuid4()
    try:
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        await admin.execute(
            f"""
            CREATE TABLE "{schema}".plan_delivery_log (
                id bigint PRIMARY KEY,
                trigger_id uuid UNIQUE NOT NULL,
                event_type text NOT NULL,
                event_label text,
                status text NOT NULL,
                instance text
            );
            CREATE TABLE "{schema}".planner_trigger_ledger (
                id bigint PRIMARY KEY,
                trigger_id uuid,
                plan_delivery_log_id bigint,
                event_type text NOT NULL,
                expected_action text NOT NULL,
                status text NOT NULL
            );
            """
        )
        await admin.execute(
            f"""
            INSERT INTO "{schema}".plan_delivery_log
                (id, trigger_id, event_type, event_label, status, instance)
            VALUES (1, $1, 'SUNRISE', 'Morning planning cycle', 'pending', 'local')
            """,
            trigger_id,
        )
        await admin.execute(
            f"""
            INSERT INTO "{schema}".planner_trigger_ledger
                (id, trigger_id, plan_delivery_log_id, event_type, expected_action, status)
            VALUES (2, $1, 1, 'SUNRISE', 'set_plan', 'delivered')
            """,
            trigger_id,
        )
        connection_settings = {"search_path": schema}
        winner = await asyncpg.connect(dsn, server_settings=connection_settings)
        late = await asyncpg.connect(dsn, server_settings=connection_settings)
        try:

            async def late_attempt():
                async with late.transaction():
                    return await mcp_server._lock_current_planner_attempt(late, str(trigger_id), "local")

            async with winner.transaction():
                _delivery, _ledger, error = await mcp_server._lock_current_planner_attempt(
                    winner, str(trigger_id), "local"
                )
                assert error is None
                blocked = asyncio.create_task(late_attempt())
                await asyncio.sleep(0.1)
                assert blocked.done() is False
                await winner.execute(
                    "UPDATE plan_delivery_log SET status = $1 WHERE id = 1",
                    winner_status,
                )
                await winner.execute(
                    "UPDATE planner_trigger_ledger SET status = $1 WHERE id = 2",
                    winner_status,
                )
            _late_delivery, _late_ledger, late_error = await blocked
            assert late_error["error"] == "trigger_id is not the current writable attempt"
            assert late_error["status"] == winner_status
        finally:
            await winner.close()
            await late.close()
    finally:
        await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await admin.close()


def test_forecast_comparator_uses_observed_outdoor_temp_rh_and_derived_vpd():
    source = (REPO_ROOT / "ingestor/tasks/forecast.py").read_text()
    body = source[source.index("async def forecast_deviation_check") :]

    assert "SELECT outdoor_temp_f, outdoor_rh_pct" in body
    assert 'observed_temp = _first_float(current["outdoor_temp_f"])' in body
    assert 'observed_rh = _first_float(current["outdoor_rh_pct"])' in body
    assert "_outdoor_vpd_kpa(observed_temp, observed_rh)" in body
    assert 'current["temp_avg"]' not in body
    assert 'current["rh_avg"]' not in body


def test_plan_status_and_context_exclude_expired_plan_rows():
    mcp_source = (REPO_ROOT / "mcp/server.py").read_text()
    plan_status_source = mcp_source[mcp_source.index("async def plan_status") : mcp_source.index("async def lessons")]
    context_source = (REPO_ROOT / "scripts/gather-plan-context.sh").read_text()

    assert "lifecycle_status = 'effective'" in plan_status_source
    assert "expires_at > now()" in plan_status_source
    assert "expires_at > now()" in context_source


def test_required_trigger_fired_state_waits_for_terminal_full_plan():
    heartbeat = (REPO_ROOT / "ingestor/tasks/heartbeat.py").read_text()

    assert 'if disposition == "complete"' in heartbeat
    assert "if delivered and not required_full_plan" in heartbeat
    assert "fired-state waits for set_plan" in heartbeat
    assert "WHEN $3 = 'delivered' THEN NULL" in heartbeat
    assert "'wrong_action', 'neutral_fallback', 'timed_out'" in heartbeat
    assert "another SUNRISE plan exists within last 4h" not in heartbeat
