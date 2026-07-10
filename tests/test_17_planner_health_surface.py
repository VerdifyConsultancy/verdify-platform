from __future__ import annotations

import ast
import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from verdify_schemas import PublicPlannerDelivery, PublicPlannerHealthResponse


@pytest.fixture(scope="module")
def mcp_server():
    path = REPO_ROOT / "mcp" / "server.py"
    spec = importlib.util.spec_from_file_location("verdify_mcp_server_health_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
        assert "expected_action" in sql
        return {
            "trigger_id": "00000000-0000-0000-0000-000000000001",
            "status": "pending",
            "instance": "local",
            "expected_action": "set_plan",
        }

    async def fetchval(self, sql: str, *_args):
        raise AssertionError(f"setpoint write reached after wrong action: {sql}")

    async def execute(self, sql: str, *args):
        self.executed.append((sql, args))

    async def close(self):
        self.closed = True


class _RequiredAckConnection:
    def __init__(self, event_type: str = "SUNSET") -> None:
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self.closed = False
        self.event_type = event_type

    async def fetchrow(self, sql: str, *_args):
        if sql.lstrip().startswith("SELECT"):
            return {
                "id": 1,
                "event_type": self.event_type,
                "event_label": f"{self.event_type.title()} planning cycle",
                "instance": "local",
                "status": "pending",
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

    async def execute(self, sql: str, *args):
        self.executed.append((sql, args))

    async def close(self):
        self.closed = True


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


def test_hermes_readiness_covers_the_exact_configured_mcp_allowlist(mcp_server):
    _manifest, profile, readiness_source = _hermes_config_documents()
    configured = set(profile["mcp_servers"]["verdify_greenhouse"]["tools"]["include"])

    assert _readiness_required_tools(readiness_source) == configured
    assert mcp_server.HERMES_REQUIRED_TOOLS == configured
    assert {"set_plan", "set_tunable", "acknowledge_trigger"} <= configured


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
