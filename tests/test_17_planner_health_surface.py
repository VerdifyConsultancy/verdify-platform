from __future__ import annotations

import ast
import asyncio
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import get_type_hints
from uuid import uuid4

import pytest
import yaml
from pydantic import TypeAdapter

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from verdify_schemas import PublicPlannerDelivery, PublicPlannerHealthResponse

_MISSING_MODULE = object()
_MCP_STUB_MODULES = ("mcp", "mcp.server", "mcp.server.fastmcp")
ACTIVE_PLANNER_MODEL_LABEL = "hermes-iris/custom:gpt-5.6-sol/xhigh"


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
        "planner_model_label": ACTIVE_PLANNER_MODEL_LABEL,
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
            "current_model_label": ACTIVE_PLANNER_MODEL_LABEL,
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
    assert response.current_model_label == ACTIVE_PLANNER_MODEL_LABEL
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
            "planner_model_label": ACTIVE_PLANNER_MODEL_LABEL,
        }
    )

    assert delivery.session_key is not None
    assert delivery.hermes_run_id == "run_test"
    assert delivery.planner_model_label == ACTIVE_PLANNER_MODEL_LABEL


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
    assert f'"{ACTIVE_PLANNER_MODEL_LABEL}"' in api_source


def _hermes_config_documents() -> tuple[dict, dict, str]:
    manifest = yaml.safe_load((REPO_ROOT / "deploy/k8s/components/hermes-iris/hermes-config.yaml").read_text())
    profile = yaml.safe_load(manifest["data"]["config.yaml"])
    readiness_source = manifest["data"]["tool-readiness.py"]
    return manifest, profile, readiness_source


def test_hermes_profile_pins_openai_gpt_5_6_sol_xhigh_at_the_runtime_key():
    _manifest, embedded, _readiness_source = _hermes_config_documents()
    canonical = yaml.safe_load((REPO_ROOT / "hermes/iris/config.yaml").read_text())
    experiment_manifest = yaml.safe_load(
        (REPO_ROOT / "deploy/k8s/components/hermes-iris-experiment/hermes-config.yaml").read_text()
    )
    experiment = yaml.safe_load(experiment_manifest["data"]["config.yaml"])

    assert (
        embedded["model"]
        == canonical["model"]
        == {
            "default": "gpt-5.6-sol",
            "provider": "custom",
            "base_url": "https://api.openai.com/v1",
        }
    )
    assert "reasoning_effort" not in embedded["model"]
    assert "reasoning_effort" not in canonical["model"]
    assert embedded["agent"]["reasoning_effort"] == canonical["agent"]["reasoning_effort"] == "xhigh"

    # The dark experiment selector deliberately remains on the separately
    # bounded Cortex route; rolling back the live planner must not change it.
    assert experiment["model"] == {
        "default": "llm.primary.longctx",
        "provider": "custom",
        "base_url": "https://cortex.vallery.net/v1",
        "context_length": 98304,
        "max_tokens": 16384,
    }
    assert experiment["agent"]["reasoning_effort"] == "medium"

    # The embedded k3s profile must remain structurally equivalent as parsed,
    # except for the intentionally environment-specific MCP endpoint.
    normalized = json.loads(json.dumps(embedded))
    normalized["mcp_servers"]["verdify_greenhouse"]["url"] = canonical["mcp_servers"]["verdify_greenhouse"]["url"]
    assert normalized == canonical


def test_planner_audit_metadata_and_current_docs_match_live_hermes_profile():
    """Keep secondary planner truth aligned without rewriting dated evidence."""
    _manifest, profile, _readiness_source = _hermes_config_documents()
    expected_audit = {
        "provider": profile["model"]["provider"],
        "model": profile["model"]["default"],
        "base_url": profile["model"]["base_url"],
        "reasoning_effort": profile["agent"]["reasoning_effort"],
        "purpose": "Audit metadata for the live Hermes Iris profile. Runtime source: hermes/iris/config.yaml.",
    }
    ai_config = yaml.safe_load((REPO_ROOT / "config/ai.yaml").read_text())
    vision_copy = yaml.safe_load((REPO_ROOT / "deploy/k8s/vision/src/ai.yaml").read_text())
    assert ai_config["models"]["planner"] == expected_audit
    assert vision_copy == ai_config

    current_docs = (
        "docs/iris-planner-contract.md",
        "docs/RUNBOOK.md",
        "docs/planner/greenhouse-playbook.md",
        "docs/planner/langgraph-external-implementation-context.md",
        "docs/SYSTEM-ARCHITECTURE.md",
    )
    for relative_path in current_docs:
        source = (REPO_ROOT / relative_path).read_text()
        assert "GPT-5.6 Sol" in source, relative_path
        assert "xhigh" in source, relative_path
        assert "llm.primary.longctx" not in source, relative_path
        assert "pending profile" not in source, relative_path

    current_code_surfaces = (
        "ingestor/iris_planner.py",
        "scripts/planner-dry.py",
        "tests/test_prompt_variants.py",
    )
    for relative_path in current_code_surfaces:
        source = (REPO_ROOT / relative_path).read_text()
        assert "GPT-5.6 Sol" in source, relative_path
        assert "Cortex" not in source, relative_path


def test_api_public_planner_model_label_is_declarative_and_matches_active_profile():
    _manifest, profile, _readiness_source = _hermes_config_documents()
    expected = (
        f"hermes-iris/{profile['model']['provider']}:"
        f"{profile['model']['default']}/{profile['agent']['reasoning_effort']}"
    )
    assert expected == ACTIVE_PLANNER_MODEL_LABEL

    api_source = (REPO_ROOT / "api/main.py").read_text()
    assert f'"VERDIFY_PLANNER_MODEL_LABEL", "{ACTIVE_PLANNER_MODEL_LABEL}"' in api_source

    api_documents = yaml.safe_load_all((REPO_ROOT / "deploy/k8s/base/api-deployment.yaml").read_text())
    api_deployment = next(document for document in api_documents if document.get("kind") == "Deployment")
    api_container = api_deployment["spec"]["template"]["spec"]["containers"][0]
    env_by_name = {item["name"]: item.get("value") for item in api_container["env"]}
    assert env_by_name["VERDIFY_PLANNER_MODEL_LABEL"] == ACTIVE_PLANNER_MODEL_LABEL


def test_argocd_sync_waves_gate_mcp_then_hermes_then_ingestor_without_pod_template_churn():
    sources = {
        "mcp": REPO_ROOT / "deploy/k8s/base/mcp-deployment.yaml",
        "hermes": REPO_ROOT / "deploy/k8s/components/hermes-iris/hermes-iris.yaml",
        "ingestor": REPO_ROOT / "deploy/k8s/base/ingestor-deployment.yaml",
    }
    deployments = {
        name: next(
            document for document in yaml.safe_load_all(path.read_text()) if document.get("kind") == "Deployment"
        )
        for name, path in sources.items()
    }
    annotation = "argocd.argoproj.io/sync-wave"
    waves = {name: int(deployment["metadata"]["annotations"][annotation]) for name, deployment in deployments.items()}

    assert waves == {"mcp": 10, "hermes": 20, "ingestor": 30}
    assert waves["mcp"] < waves["hermes"] < waves["ingestor"]
    for deployment in deployments.values():
        assert annotation not in deployment["spec"]["template"]["metadata"].get("annotations", {})


def test_hermes_profile_revision_rolls_and_reseeds_on_config_change():
    manifest, _profile, _readiness_source = _hermes_config_documents()
    expected_profile = hashlib.sha256(manifest["data"]["config.yaml"].encode()).hexdigest()[:12]
    workloads = yaml.safe_load_all((REPO_ROOT / "deploy/k8s/components/hermes-iris/hermes-iris.yaml").read_text())
    workload = next(document for document in workloads if document.get("kind") == "Deployment")

    assert workload["spec"]["template"]["metadata"]["annotations"] == {
        "verdify.io/hermes-profile-revision": expected_profile,
    }
    init_by_name = {item["name"]: item for item in workload["spec"]["template"]["spec"]["initContainers"]}
    assert init_by_name["seed-config"]["command"] == [
        "sh",
        "-c",
        "cp -f /etc/verdify/hermes-config/config.yaml /opt/data/config.yaml && "
        "echo 'seeded /opt/data/config.yaml from verdify-hermes-iris-config'",
    ]


def test_openai_rollback_removes_cortex_only_runtime_patch_machinery():
    manifest, _profile, _readiness_source = _hermes_config_documents()
    assert "hermes-compressor-oob-patch.py" not in manifest["data"]
    assert "hermes-request-estimator-oob-patch.py" not in manifest["data"]

    documents = yaml.safe_load_all((REPO_ROOT / "deploy/k8s/components/hermes-iris/hermes-iris.yaml").read_text())
    deployment = next(document for document in documents if document.get("kind") == "Deployment")
    pod_spec = deployment["spec"]["template"]["spec"]
    init_names = {item["name"] for item in pod_spec["initContainers"]}
    assert init_names == {"seed-config"}
    assert {volume["name"] for volume in pod_spec["volumes"]}.isdisjoint({"hermes-runtime-patch", "hermes-agent-patch"})
    main = pod_spec["containers"][0]
    assert {mount["name"] for mount in main["volumeMounts"]}.isdisjoint({"hermes-runtime-patch", "hermes-agent-patch"})
    env_names = {item["name"] for item in main["env"]}
    assert "HERMES_STREAM_STALE_TIMEOUT" not in env_names


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


def _hermes_mcp_log(stamp: str, message: str, level: str = "INFO", logger: str = "tools.mcp_tool") -> str:
    return f"{stamp} {level} {logger}: {message}\n"


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
        _hermes_mcp_log(
            "2026-07-10 11:59:00,000",
            "MCP server 'verdify_greenhouse' failed after 5 reconnection attempts, giving up: old",
            "WARNING",
        )
        + _hermes_mcp_log(
            "2026-07-10 12:00:01,000",
            "MCP server 'verdify_greenhouse' (HTTP): registered 23 tool(s): climate",
        )
    )

    state = client_state([str(log_path)], started_at)

    assert state["state"] == "connected"
    assert state["fatal"] is False
    assert state["signal_source"] == "full_discovery_log"


def test_hermes_persisted_state_does_not_inherit_immediate_prior_process_disconnect(tmp_path):
    _manifest, _profile, source = _hermes_config_documents()
    namespace = _hermes_probe_namespace(source)
    client_state = namespace["hermes_client_state"]
    first_started_at = datetime(2026, 7, 10, 11, 59, tzinfo=UTC).timestamp()
    replacement_started_at = datetime(2026, 7, 10, 12, tzinfo=UTC).timestamp()
    log_path = tmp_path / "agent.log"
    state_path = tmp_path / "probe-state.json"
    log_path.write_text(
        _hermes_mcp_log(
            "2026-07-10 11:59:59,500",
            "MCP server 'verdify_greenhouse' connection lost (attempt 4/5), reconnecting in 60s: old process",
            "WARNING",
        )
    )

    first = client_state([str(log_path)], first_started_at, state_path=state_path)
    log_path.replace(tmp_path / "agent.log.1")
    log_path.write_text("")
    replacement = client_state([str(log_path)], replacement_started_at, state_path=state_path)

    assert first["state"] == "disconnected"
    assert replacement["state"] == "unknown"
    assert replacement["process_started_at"] == replacement_started_at
    assert replacement["unacknowledged_disconnect_since"] is None


def test_hermes_silent_background_reconnect_stays_latched_across_log_rotation(tmp_path):
    _manifest, _profile, source = _hermes_config_documents()
    namespace = _hermes_probe_namespace(source)
    client_state = namespace["hermes_client_state"]
    started_at = datetime(2026, 7, 10, 12, tzinfo=UTC).timestamp()
    log_path = tmp_path / "agent.log"
    state_path = tmp_path / "probe-state.json"
    log_path.write_text(
        _hermes_mcp_log(
            "2026-07-10 12:00:01,000",
            "MCP server 'verdify_greenhouse' (HTTP): registered 23 tool(s): climate",
        )
        + _hermes_mcp_log(
            "2026-07-10 12:01:00,000",
            "MCP server 'verdify_greenhouse' connection lost (attempt 1/5), reconnecting in 1s: reset",
            "WARNING",
        )
    )
    disconnected = client_state([str(log_path)], started_at, state_path=state_path)

    # Exact pinned upstream behavior: _run_http() can reconnect successfully,
    # but emits no new full-discovery/registered line. Rotate away the first
    # negative marker and leave only later retry chatter in the active log.
    log_path.replace(tmp_path / "agent.log.1")
    log_path.write_text(
        _hermes_mcp_log(
            "2026-07-10 12:02:00,000",
            "MCP server 'verdify_greenhouse' connection lost (attempt 4/5), reconnecting in 8s: reset",
            "WARNING",
        )
    )
    after_rotation = client_state([str(log_path)], started_at, state_path=state_path)
    (tmp_path / "agent.log.1").unlink()
    log_path.write_text("")
    after_original_marker_is_gone = client_state([str(log_path)], started_at, state_path=state_path)

    expected_since = datetime(2026, 7, 10, 12, 1, tzinfo=UTC).timestamp()
    for state in (disconnected, after_rotation, after_original_marker_is_gone):
        assert state["state"] == "disconnected"
        assert state["fatal"] is False
        assert state["unacknowledged_disconnect_since"] == expected_since
    assert after_rotation["last_event_at"] == datetime(2026, 7, 10, 12, 2, tzinfo=UTC).timestamp()
    assert state_path.stat().st_mode & 0o777 == 0o600


def test_hermes_real_full_rediscovery_marker_acknowledges_latched_disconnect(tmp_path):
    _manifest, _profile, source = _hermes_config_documents()
    namespace = _hermes_probe_namespace(source)
    client_state = namespace["hermes_client_state"]
    started_at = datetime(2026, 7, 10, 12, tzinfo=UTC).timestamp()
    log_path = tmp_path / "agent.log"
    state_path = tmp_path / "probe-state.json"
    log_path.write_text(
        _hermes_mcp_log(
            "2026-07-10 12:00:01,000",
            "MCP server 'verdify_greenhouse' (HTTP): registered 23 tool(s): climate",
        )
        + _hermes_mcp_log(
            "2026-07-10 12:01:00,000",
            "MCP server 'verdify_greenhouse' connection lost (attempt 1/5), reconnecting in 1s: reset",
            "WARNING",
        )
    )
    disconnected = client_state([str(log_path)], started_at, state_path=state_path)
    with log_path.open("a") as stream:
        stream.write(
            _hermes_mcp_log(
                "2026-07-10 12:03:00,000",
                "MCP server 'verdify_greenhouse' (HTTP): registered 23 tool(s): climate",
            )
        )
    acknowledged = client_state([str(log_path)], started_at, state_path=state_path)

    assert disconnected["state"] == "disconnected"
    assert acknowledged["state"] == "connected"
    assert acknowledged["unacknowledged_disconnect_since"] is None
    assert acknowledged["signal_source"] == "full_discovery_log"


def test_hermes_clean_reconnect_request_latches_silent_http_session_replacement(tmp_path):
    _manifest, _profile, source = _hermes_config_documents()
    namespace = _hermes_probe_namespace(source)
    client_state = namespace["hermes_client_state"]
    started_at = datetime(2026, 7, 10, 12, tzinfo=UTC).timestamp()
    log_path = tmp_path / "agent.log"
    log_path.write_text(
        _hermes_mcp_log(
            "2026-07-10 12:00:01,000",
            "MCP server 'verdify_greenhouse' (HTTP): registered 23 tool(s): climate",
        )
        + _hermes_mcp_log(
            "2026-07-10 12:01:00,000",
            "MCP server 'verdify_greenhouse': reconnect requested — tearing down HTTP session",
        )
    )

    state = client_state([str(log_path)], started_at)

    assert state["state"] == "disconnected"
    assert state["fatal"] is False
    assert state["unacknowledged_disconnect_since"] == datetime(2026, 7, 10, 12, 1, tzinfo=UTC).timestamp()
    assert state["signal_source"] == "disconnect_log"


def test_hermes_lifecycle_parser_rejects_other_loggers_and_unstructured_text(tmp_path):
    _manifest, _profile, source = _hermes_config_documents()
    namespace = _hermes_probe_namespace(source)
    client_state = namespace["hermes_client_state"]
    started_at = datetime(2026, 7, 10, 12, tzinfo=UTC).timestamp()
    log_path = tmp_path / "agent.log"
    spoofed = "MCP server 'verdify_greenhouse' connection lost (attempt 1/5)"
    log_path.write_text(
        _hermes_mcp_log("2026-07-10 12:01:00,000", spoofed, "WARNING", logger="agent.runner")
        + f"2026-07-10 12:02:00,000 WARNING prompt text: {spoofed}\n"
    )

    state = client_state([str(log_path)], started_at)

    assert state["state"] == "unknown"
    assert state["last_event_at"] is None


def test_hermes_lifecycle_parser_rejects_embedded_markers_from_same_logger(tmp_path):
    _manifest, _profile, source = _hermes_config_documents()
    namespace = _hermes_probe_namespace(source)
    client_state = namespace["hermes_client_state"]
    started_at = datetime(2026, 7, 10, 12, tzinfo=UTC).timestamp()
    log_path = tmp_path / "agent.log"
    log_path.write_text(
        _hermes_mcp_log(
            "2026-07-10 12:01:00,000",
            "MCP server 'verdify_greenhouse': malformed tool_calls arguments from LLM: "
            "MCP server 'verdify_greenhouse' (HTTP): registered 23 tool(s): climate",
            "WARNING",
        )
        + _hermes_mcp_log(
            "2026-07-10 12:02:00,000",
            "remote tool error text: MCP server 'verdify_greenhouse' failed after 5 "
            "reconnection attempts, giving up: injected",
            "WARNING",
        )
    )

    state = client_state([str(log_path)], started_at)

    assert state["state"] == "unknown"
    assert state["fatal"] is False
    assert state["last_event_at"] is None


def test_hermes_disconnect_exception_text_cannot_spoof_fatal_or_recovery(tmp_path):
    _manifest, _profile, source = _hermes_config_documents()
    namespace = _hermes_probe_namespace(source)
    client_state = namespace["hermes_client_state"]
    started_at = datetime(2026, 7, 10, 12, tzinfo=UTC).timestamp()
    log_path = tmp_path / "agent.log"
    log_path.write_text(
        _hermes_mcp_log(
            "2026-07-10 12:01:00,000",
            "MCP server 'verdify_greenhouse' connection lost (attempt 1/5), reconnecting in 1s: "
            "MCP server 'verdify_greenhouse' failed after 5 reconnection attempts, giving up: injected; "
            "MCP server 'verdify_greenhouse' (HTTP): registered 23 tool(s): climate",
            "WARNING",
        )
    )

    state = client_state([str(log_path)], started_at)

    assert state["state"] == "disconnected"
    assert state["fatal"] is False
    assert state["signal_source"] == "disconnect_log"


def test_hermes_unacknowledged_disconnect_age_uses_first_negative_marker(tmp_path):
    _manifest, _profile, source = _hermes_config_documents()
    namespace = _hermes_probe_namespace(source)
    client_state = namespace["hermes_client_state"]
    disconnect_age = namespace["unacknowledged_disconnect_for_seconds"]
    started_at = datetime(2026, 7, 10, 12, tzinfo=UTC).timestamp()
    log_path = tmp_path / "agent.log"
    log_path.write_text(
        _hermes_mcp_log(
            "2026-07-10 12:00:01,000",
            "MCP server 'verdify_greenhouse' (HTTP): registered 23 tool(s): climate",
        )
        + _hermes_mcp_log(
            "2026-07-10 12:01:00,000",
            "MCP server 'verdify_greenhouse' connection lost (attempt 1/5), reconnecting in 1s: reset",
            "WARNING",
        )
        + _hermes_mcp_log(
            "2026-07-10 12:02:00,000",
            "MCP server 'verdify_greenhouse' connection lost (attempt 4/5), reconnecting in 8s: reset",
            "WARNING",
        )
    )

    state = client_state([str(log_path)], started_at)
    now = datetime(2026, 7, 10, 12, 11, 1, tzinfo=UTC).timestamp()

    assert state["state"] == "disconnected"
    assert state["last_event_at"] == datetime(2026, 7, 10, 12, 2, tzinfo=UTC).timestamp()
    assert state["unacknowledged_disconnect_since"] == datetime(2026, 7, 10, 12, 1, tzinfo=UTC).timestamp()
    assert disconnect_age(state, now=now) == 601


def test_hermes_liveness_replaces_only_prolonged_unacknowledged_disconnect_or_fatal(monkeypatch, capsys):
    _manifest, _profile, source = _hermes_config_documents()
    namespace = _hermes_probe_namespace(source)
    monkeypatch.setenv("HERMES_PROCESS_START_EPOCH", "0")
    monkeypatch.setenv("HERMES_MCP_UNACKNOWLEDGED_DISCONNECT_RESTART_SECONDS", "600")
    namespace["time"] = SimpleNamespace(time=lambda: 1_000.0)

    client = {
        "state": "disconnected",
        "fatal": False,
        "last_event_at": 500.0,
        "unacknowledged_disconnect_since": 401.0,
    }
    namespace["hermes_client_state"] = lambda *_args, **_kwargs: client
    assert namespace["main"](["--mode", "liveness"]) == 0
    recent = json.loads(capsys.readouterr().out)
    assert recent["alive"] is True
    assert recent["restart_reason"] is None

    client["unacknowledged_disconnect_since"] = 400.0
    assert namespace["main"](["--mode", "liveness"]) == 1
    stale = json.loads(capsys.readouterr().out)
    assert stale["alive"] is False
    assert stale["unacknowledged_disconnect_for_seconds"] == 600
    assert stale["restart_reason"] == "mcp_client_unacknowledged_disconnect_timeout"

    client.update({"state": "unknown", "unacknowledged_disconnect_since": None})
    assert namespace["main"](["--mode", "liveness"]) == 0
    unknown = json.loads(capsys.readouterr().out)
    assert unknown["alive"] is True

    client.update({"state": "fatal", "fatal": True})
    assert namespace["main"](["--mode", "liveness"]) == 1
    fatal = json.loads(capsys.readouterr().out)
    assert fatal["restart_reason"] == "fatal_reconnect_exhaustion"


@pytest.mark.parametrize(
    ("configured", "expected_error"),
    [
        (None, None),
        ("not-a-number", "not_numeric"),
        ("nan", "outside_60_86400_seconds"),
        ("-1", "outside_60_86400_seconds"),
        ("59", "outside_60_86400_seconds"),
        ("86401", "outside_60_86400_seconds"),
    ],
)
def test_hermes_unacknowledged_disconnect_timeout_is_opt_in_and_invalid_values_fail_open(
    configured, expected_error, monkeypatch, capsys
):
    _manifest, _profile, source = _hermes_config_documents()
    namespace = _hermes_probe_namespace(source)
    monkeypatch.setenv("HERMES_PROCESS_START_EPOCH", "0")
    if configured is None:
        monkeypatch.delenv("HERMES_MCP_UNACKNOWLEDGED_DISCONNECT_RESTART_SECONDS", raising=False)
    else:
        monkeypatch.setenv("HERMES_MCP_UNACKNOWLEDGED_DISCONNECT_RESTART_SECONDS", configured)
    namespace["time"] = SimpleNamespace(time=lambda: 10_000.0)
    namespace["hermes_client_state"] = lambda *_args, **_kwargs: {
        "state": "disconnected",
        "fatal": False,
        "last_event_at": 100.0,
        "unacknowledged_disconnect_since": 100.0,
    }

    assert namespace["main"](["--mode", "liveness"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["alive"] is True
    assert payload["unacknowledged_disconnect_restart_seconds"] is None
    assert payload["timeout_config_error"] == expected_error
    assert payload["restart_reason"] is None


def test_hermes_client_state_fatal_reconnect_exhaustion(tmp_path):
    _manifest, _profile, source = _hermes_config_documents()
    namespace = _hermes_probe_namespace(source)
    client_state = namespace["hermes_client_state"]
    started_at = datetime(2026, 7, 10, 12, tzinfo=UTC).timestamp()
    log_path = tmp_path / "errors.log"
    log_path.write_text(
        _hermes_mcp_log(
            "2026-07-10 12:00:01,000",
            "MCP server 'verdify_greenhouse' (HTTP): registered 23 tool(s): climate",
        )
        + _hermes_mcp_log(
            "2026-07-10 12:02:00,000",
            "MCP server 'verdify_greenhouse' failed after 5 reconnection attempts, giving up: reset",
            "WARNING",
        )
    )

    state = client_state([str(log_path)], started_at)

    assert state["state"] == "fatal"
    assert state["fatal"] is True


def test_hermes_readiness_main_fails_closed_across_client_server_and_config_matrix(monkeypatch, capsys):
    manifest, _profile, source = _hermes_config_documents()
    namespace = _hermes_probe_namespace(source)
    config_text = manifest["data"]["config.yaml"]
    missing_config = config_text.replace("        - lessons\n", "")
    unexpected_config = config_text.replace(
        "        - lessons\n",
        "        - lessons\n        - unexpected_tool\n",
    )
    monkeypatch.setenv("HERMES_PROCESS_START_EPOCH", "0")

    cases = [
        ("connected", False, (True, [], None), config_text, True),
        ("unknown", False, (True, [], None), config_text, False),
        ("disconnected", False, (True, [], None), config_text, False),
        ("fatal", True, (True, [], None), config_text, False),
        ("connected", False, (False, [], "TimeoutError"), config_text, False),
        ("connected", False, (False, ["set_plan"], None), config_text, False),
        ("connected", False, (True, [], None), missing_config, False),
        ("connected", False, (True, [], None), unexpected_config, False),
    ]
    for state, fatal, server_result, configured, expected_ready in cases:
        namespace["hermes_client_state"] = lambda *_args, _state=state, _fatal=fatal, **_kwargs: {
            "state": _state,
            "fatal": _fatal,
            "last_event_at": 1.0,
            "unacknowledged_disconnect_since": 1.0 if _state in {"disconnected", "fatal"} else None,
        }
        namespace["mcp_server_ready"] = lambda _result=server_result: _result
        namespace["open"] = lambda *_args, _configured=configured, **_kwargs: io.StringIO(_configured)

        return_code = namespace["main"](["--mode", "readiness"])
        payload = json.loads(capsys.readouterr().out)

        assert return_code == (0 if expected_ready else 1)
        assert payload["ready"] is expected_ready


def test_hermes_mcp_server_ready_checks_http_status_payload_and_required_tools(monkeypatch):
    _manifest, _profile, source = _hermes_config_documents()
    namespace = _hermes_probe_namespace(source)

    class Response(io.StringIO):
        def __init__(self, payload, status=200):
            super().__init__(json.dumps(payload))
            self.status = status

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    required = sorted(namespace["REQUIRED"])
    monkeypatch.setattr(
        namespace["urllib"].request,
        "urlopen",
        lambda *_args, **_kwargs: Response({"ready": True, "required_tools": required}),
    )
    assert namespace["mcp_server_ready"]() == (True, [], None)

    monkeypatch.setattr(
        namespace["urllib"].request,
        "urlopen",
        lambda *_args, **_kwargs: Response({"ready": True, "required_tools": required[1:]}),
    )
    ready, missing, error = namespace["mcp_server_ready"]()
    assert ready is False
    assert missing == [required[0]]
    assert error is None

    monkeypatch.setattr(
        namespace["urllib"].request,
        "urlopen",
        lambda *_args, **_kwargs: Response({"ready": True, "required_tools": required}, status=503),
    )
    assert namespace["mcp_server_ready"]()[0] is False

    def timeout(*_args, **_kwargs):
        raise TimeoutError("bounded test timeout")

    monkeypatch.setattr(namespace["urllib"].request, "urlopen", timeout)
    assert namespace["mcp_server_ready"]() == (False, [], "TimeoutError")


def test_actual_python3_liveness_probe_replaces_unacknowledged_disconnect_or_fatal(tmp_path):
    manifest, _profile, source = _hermes_config_documents()
    probe_path = tmp_path / "tool-readiness.py"
    log_path = tmp_path / "agent.log"
    probe_path.write_text(source)
    started_at = datetime(2026, 7, 10, 12, tzinfo=UTC).timestamp()
    log_path.write_text(
        _hermes_mcp_log(
            "2026-07-10 11:59:00,000",
            "MCP server 'verdify_greenhouse' failed after 5 reconnection attempts, giving up: old",
            "WARNING",
        )
    )
    env = {
        **os.environ,
        "HERMES_PROCESS_START_EPOCH": str(started_at),
        "HERMES_CLIENT_LOG_PATHS": str(log_path),
        "HERMES_MCP_PROBE_STATE_PATH": str(tmp_path / "probe-state.json"),
        "HERMES_MCP_UNACKNOWLEDGED_DISCONNECT_RESTART_SECONDS": "600",
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
            _hermes_mcp_log(
                "2026-07-10 12:01:00,000",
                "MCP server 'verdify_greenhouse' connection lost (attempt 1/5), reconnecting in 1s: reset",
                "WARNING",
            )
        )
    disconnected = subprocess.run(
        ["python3", str(probe_path), "--mode", "liveness"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    with log_path.open("a") as stream:
        stream.write(
            _hermes_mcp_log(
                "2026-07-10 12:02:00,000",
                "MCP server 'verdify_greenhouse' failed after 5 reconnection attempts, giving up: reset",
                "WARNING",
            )
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
    assert disconnected.returncode == 1
    assert json.loads(disconnected.stdout)["restart_reason"] == ("mcp_client_unacknowledged_disconnect_timeout")
    assert fatal.returncode == 1
    assert json.loads(fatal.stdout)["client"]["fatal"] is True
    assert container["data"]["tool-readiness.py"] == source
    assert probes["readinessProbe"]["exec"]["command"][0] == "python3"
    assert probes["livenessProbe"]["exec"]["command"][-1] == "liveness"
    assert probes["livenessProbe"]["failureThreshold"] == 2
    env_by_name = {item["name"]: item["value"] for item in probes["env"]}
    assert env_by_name["HERMES_MCP_UNACKNOWLEDGED_DISCONNECT_RESTART_SECONDS"] == "600"
    assert workload["spec"]["replicas"] == 1
    assert workload["spec"]["strategy"]["type"] == "Recreate"
    expected_digest = (
        "nousresearch/hermes-agent@sha256:a7111ab1cc43b5a1bc76090a505d6462aa1af4b43f603f0113bf5eb121aec72e"
    )
    assert probes["image"] == expected_digest
    init_by_name = {item["name"]: item for item in workload["spec"]["template"]["spec"]["initContainers"]}
    assert init_by_name["seed-config"]["image"] == expected_digest


def test_rendered_prod_hermes_supervision_preserves_singleton_exact_pin_and_both_probes():
    rendered = subprocess.run(
        ["kustomize", "build", "deploy/k8s/overlays/prod"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    resources = [resource for resource in yaml.safe_load_all(rendered) if isinstance(resource, dict)]
    workload = next(
        resource
        for resource in resources
        if resource.get("kind") == "Deployment" and resource.get("metadata", {}).get("name") == "verdify-hermes-iris"
    )
    pvc_by_name = {
        resource["metadata"]["name"]: resource
        for resource in resources
        if resource.get("kind") == "PersistentVolumeClaim"
    }
    container = workload["spec"]["template"]["spec"]["containers"][0]
    env_by_name = {item["name"]: item.get("value") for item in container["env"]}
    init_by_name = {item["name"]: item for item in workload["spec"]["template"]["spec"]["initContainers"]}
    volume_by_name = {item["name"]: item for item in workload["spec"]["template"]["spec"]["volumes"]}
    mount_by_name = {item["name"]: item for item in container["volumeMounts"]}
    expected_digest = (
        "nousresearch/hermes-agent@sha256:a7111ab1cc43b5a1bc76090a505d6462aa1af4b43f603f0113bf5eb121aec72e"
    )
    readiness = container["readinessProbe"]
    liveness = container["livenessProbe"]

    assert workload["spec"]["replicas"] == 1
    assert workload["spec"]["strategy"]["type"] == "Recreate"
    assert container["image"] == expected_digest
    assert init_by_name["seed-config"]["image"] == expected_digest
    assert readiness == {
        "exec": {"command": ["python3", "/etc/verdify/hermes-config/tool-readiness.py", "--mode", "readiness"]},
        "failureThreshold": 1,
        "initialDelaySeconds": 15,
        "periodSeconds": 10,
        "timeoutSeconds": 5,
    }
    assert liveness == {
        "exec": {"command": ["python3", "/etc/verdify/hermes-config/tool-readiness.py", "--mode", "liveness"]},
        "failureThreshold": 2,
        "initialDelaySeconds": 45,
        "periodSeconds": 15,
        "timeoutSeconds": 5,
    }
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert volume_by_name["tmp"]["emptyDir"] == {}
    assert volume_by_name["data"]["persistentVolumeClaim"]["claimName"] == (
        "verdify-hermes-iris-data-portable-20260801"
    )
    portable_pvc = pvc_by_name["verdify-hermes-iris-data-portable-20260801"]
    assert portable_pvc["metadata"]["annotations"]["argocd.argoproj.io/sync-options"] == "Prune=false"
    assert portable_pvc["spec"] == {
        "accessModes": ["ReadWriteOnce"],
        "resources": {"requests": {"storage": "5Gi"}},
        "storageClassName": "longhorn-v1-rebuildable-rwo",
        "volumeMode": "Filesystem",
        "volumeName": "pvc-6b6b2f30-c414-4325-a803-87b19010851f",
    }
    assert portable_pvc["metadata"]["labels"]["storage.vallery.net/policy"] == "rebuildable-replicated-r2"
    assert "verdify-hermes-iris-data" not in pvc_by_name
    assert mount_by_name["tmp"]["mountPath"] == "/tmp"  # noqa: S108 - asserted ephemeral emptyDir mount
    assert env_by_name["HERMES_MCP_UNACKNOWLEDGED_DISCONNECT_RESTART_SECONDS"] == "600"
    assert env_by_name["HERMES_MCP_PROBE_STATE_PATH"] == (
        "/tmp/verdify-hermes-mcp-probe-state.json"  # noqa: S108 - non-secret per-process probe state
    )


def test_prod_source_cannot_recreate_retired_hermes_claim():
    rendered = subprocess.run(
        ["kustomize", "build", "deploy/k8s/overlays/prod"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    resources = [resource for resource in yaml.safe_load_all(rendered) if isinstance(resource, dict)]
    pvc_names = {
        resource["metadata"]["name"] for resource in resources if resource.get("kind") == "PersistentVolumeClaim"
    }
    assert "verdify-hermes-iris-data-portable-20260801" in pvc_names
    assert "verdify-hermes-iris-data" not in pvc_names

    application = yaml.safe_load((REPO_ROOT / "deploy/k8s/argocd/apps/verdify-prod-dark.yaml").read_text())
    ignored_pvcs = {
        item.get("name")
        for item in application["spec"].get("ignoreDifferences", [])
        if item.get("kind") == "PersistentVolumeClaim"
    }
    assert "verdify-hermes-iris-data" not in ignored_pvcs
    assert "verdify-hermes-iris-data-portable-20260801" in ignored_pvcs


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


def _schema_max_length(schema: dict[str, object]) -> int:
    if "maxLength" in schema:
        return int(schema["maxLength"])
    for branch in schema.get("anyOf", []):
        if "maxLength" in branch:
            return int(branch["maxLength"])
    raise AssertionError(f"schema does not advertise maxLength: {schema}")


def test_set_plan_tool_schema_advertises_compact_argument_limits(mcp_server):
    hints = get_type_hints(mcp_server.set_plan, include_extras=True)
    expected_max_lengths = {
        "plan_id": 18,
        "hypothesis": mcp_server.SET_PLAN_HYPOTHESIS_MAX_CHARS,
        "transitions": mcp_server.SET_PLAN_TRANSITIONS_MAX_CHARS,
        "experiment": mcp_server.SET_PLAN_EXPERIMENT_MAX_CHARS,
        "expected_outcome": mcp_server.SET_PLAN_EXPECTED_OUTCOME_MAX_CHARS,
        "trigger_id": 36,
        "planner_instance": 5,
        "valid_from": 40,
        "expires_at": 40,
    }

    schemas = {name: TypeAdapter(hints[name]).json_schema() for name in expected_max_lengths}
    advertised_total = sum(_schema_max_length(schemas[name]) for name in expected_max_lengths)

    assert {name: _schema_max_length(schema) for name, schema in schemas.items()} == expected_max_lengths
    assert advertised_total < 9000
    assert "3-8 time-ordered waypoints" in schemas["transitions"]["description"]
    assert "at most 2 stress windows and 3 rationales" in schemas["hypothesis"]["description"]


def test_set_plan_waypoint_contract_accepts_three_to_eight_complete_intents(mcp_server):
    intent = mcp_server.ClimateIntent().model_dump()

    def waypoints(count: int, *, reason: str = "compact posture") -> list[dict[str, object]]:
        return [
            {
                "ts": f"2026-08-{25 + (idx // 3):02d}T{(idx % 3) * 8:02d}:00:00+00:00",
                "climate_intent": dict(intent),
                "reason": reason,
            }
            for idx in range(count)
        ]

    assert mcp_server._climate_intent_waypoint_errors(waypoints(3)) == []
    assert mcp_server._climate_intent_waypoint_errors(waypoints(8)) == []

    for invalid_count in (2, 9):
        errors = mcp_server._climate_intent_waypoint_errors(waypoints(invalid_count))
        assert errors[0]["transition_count"] == invalid_count
        assert errors[0]["error"] == "set_plan requires 3-8 transitions"

    errors = mcp_server._climate_intent_waypoint_errors(
        waypoints(3, reason="r" * (mcp_server.SET_PLAN_REASON_MAX_CHARS + 1))
    )
    assert errors[0]["reason_chars"] == mcp_server.SET_PLAN_REASON_MAX_CHARS + 1
    assert errors[0]["error"] == "reason must be at most 120 characters"

    incomplete = waypoints(3)
    missing_field = mcp_server.CLIMATE_INTENT_FIELDS[0]
    del incomplete[1]["climate_intent"][missing_field]
    errors = mcp_server._climate_intent_waypoint_errors(incomplete)
    missing_error = next(error for error in errors if error["transition_index"] == 1)
    assert missing_error["error"] == "climate_intent must explicitly set every field"
    assert missing_error["missing_fields"] == [missing_field]


def test_canonical_eight_waypoint_tool_call_fits_final_completion_ceiling(mcp_server):
    intent = mcp_server.ClimateIntent().model_dump()
    transitions = json.dumps(
        [
            {
                "ts": (datetime(2026, 8, 25, tzinfo=UTC) + timedelta(hours=8 * idx)).isoformat(),
                "climate_intent": intent,
                "reason": "Forecast-anchored compact posture; preserve safety and band compliance.",
            }
            for idx in range(8)
        ],
        separators=(",", ":"),
    )
    hypothesis = json.dumps(
        {
            "conditions": {
                "outdoor_temp_peak_f": 95,
                "outdoor_rh_min_pct": 12,
                "solar_peak_w_m2": 900,
                "cloud_cover_avg_pct": 15,
                "notes": "hot, dry, clear",
            },
            "stress_windows": [
                {
                    "kind": "vpd_high",
                    "start": "2026-08-25T10:00:00-06:00",
                    "end": "2026-08-25T16:00:00-06:00",
                    "severity": "high",
                    "mitigation": "early wet assist with safe dew margin",
                },
                {
                    "kind": "heat_stress",
                    "start": "2026-08-25T12:00:00-06:00",
                    "end": "2026-08-25T17:00:00-06:00",
                    "severity": "medium",
                    "mitigation": "solar pre-cooling and stage-2 readiness",
                },
            ],
            "rationale": [
                {
                    "parameter": "fog_escalation_kpa",
                    "old_value": 0.4,
                    "new_value": 0.25,
                    "forecast_anchor": "RH below 15% at peak",
                    "expected_effect": "reduce VPD-high stress under 2h",
                },
                {
                    "parameter": "cool_stage2_over_high_f",
                    "old_value": 1,
                    "new_value": 0.5,
                    "forecast_anchor": "95F peak with clear solar",
                    "expected_effect": "start stage 2 before heat peak",
                },
                {
                    "parameter": "mister_pulse_gap_s",
                    "old_value": 45,
                    "new_value": 25,
                    "forecast_anchor": "dry ventilation period",
                    "expected_effect": "increase safe moisture duty",
                },
            ],
        },
        separators=(",", ":"),
    )
    arguments = {
        "plan_id": "iris-20260825-1700",
        "hypothesis": hypothesis,
        "transitions": transitions,
        "experiment": "Compare compact forecast-aware posture to the prior day.",
        "expected_outcome": "Reduce VPD-high stress below 2h without a dew-margin or water-budget violation.",
        "trigger_id": "00000000-0000-0000-0000-000000000001",
        "planner_instance": "local",
        "valid_from": None,
        "expires_at": None,
    }

    decoded_chars = mcp_server._set_plan_decoded_argument_chars(**arguments)
    serialized_tool_arguments = json.dumps(arguments, separators=(",", ":"))

    assert len(hypothesis) <= mcp_server.SET_PLAN_HYPOTHESIS_MAX_CHARS
    assert len(transitions) <= mcp_server.SET_PLAN_TRANSITIONS_MAX_CHARS
    assert decoded_chars < 9000
    assert len(serialized_tool_arguments) < 10000
    assert len(serialized_tool_arguments) < 16384


@pytest.mark.asyncio
async def test_set_plan_rejects_only_oversized_decoded_arguments_before_db(mcp_server, monkeypatch):
    async def unexpected_db():
        raise AssertionError("decoded argument guard must run before database access")

    monkeypatch.setattr(mcp_server, "_db", unexpected_db)
    result = json.loads(
        await mcp_server.set_plan(
            plan_id="iris-20260825-1700",
            hypothesis="x" * mcp_server.SET_PLAN_ARGUMENTS_MAX_CHARS,
            transitions="[]",
            trigger_id="00000000-0000-0000-0000-000000000001",
            planner_instance="local",
        )
    )

    assert result["error"] == "set_plan decoded arguments exceed the compact tool-call contract"
    assert result["decoded_argument_chars"] > mcp_server.SET_PLAN_ARGUMENTS_MAX_CHARS
    assert result["max_decoded_argument_chars"] == mcp_server.SET_PLAN_ARGUMENTS_MAX_CHARS


@pytest.mark.asyncio
async def test_mcp_readiness_fails_closed_when_a_required_tool_is_missing(mcp_server, monkeypatch):
    missing = "set_plan"
    available = mcp_server.HERMES_REQUIRED_TOOLS - {missing}
    connection = _ReadyConnection()

    async def list_tools():
        return [SimpleNamespace(name=name) for name in available]

    async def db():
        return connection

    monkeypatch.setenv("VERDIFY_MCP_AUTH_MODE", "off")
    monkeypatch.setattr(mcp_server.mcp, "list_tools_unfiltered", list_tools)
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

    monkeypatch.setenv("VERDIFY_MCP_AUTH_MODE", "off")
    monkeypatch.setattr(mcp_server.mcp, "list_tools_unfiltered", list_tools)
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
