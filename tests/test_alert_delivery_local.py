"""#1352: Verdify alerts are alert_log rows (the dashboard) and page through Alertmanager, never Slack.

- No runtime sender is left: no code posts to chat.postMessage, and the ingestor no longer imports the Slack config
  or the operator-brief, policy and runbook helpers.
- The two former Slack-only pages are alert_log rows: ``mcp_unreachable`` (planning heartbeat, critical, opened once
  per outage and auto-resolved) and ``forecast_action_alert`` (forecast-action engine, warning). Both validate as
  ``AlertEnvelope`` and are written with a source other than ``system``, so the alert monitor never auto-resolves
  them as its own.
- The prod render mounts no Slack Secret, sets no Slack env and has no Slack relay port.
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import json
import re
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from verdify_schemas.alerts import AlertEnvelope

ROOT = Path(__file__).resolve().parents[1]
HEARTBEAT = ROOT / "ingestor/tasks/heartbeat.py"
ENGINE = ROOT / "scripts/forecast-action-engine.py"
RUNTIME_SOURCES = [
    *sorted((ROOT / "ingestor").rglob("*.py")),
    *sorted((ROOT / "api").rglob("*.py")),
    *sorted((ROOT / "verdify_services").rglob("*.py")),
    *sorted((ROOT / "scripts").glob("*.py")),
    *sorted((ROOT / "scripts").glob("*.sh")),
]


def test_no_runtime_code_posts_to_slack():
    for path in RUNTIME_SOURCES:
        text = path.read_text(encoding="utf-8")
        for marker in ("chat.postMessage", "slack.com/api", "_post_slack(", "post_slack("):
            assert marker not in text, f"{path.relative_to(ROOT)} still carries {marker!r}"


def test_the_ingestor_imports_no_slack_module():
    for path in sorted((ROOT / "ingestor").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert not name.startswith(("slack_config", "slack_ops")), f"{path.relative_to(ROOT)} imports {name}"
    assert not (ROOT / "ingestor/tasks/watch.py").exists()  # operator briefs and the midnight watch are retired


# ── mcp_unreachable (planning heartbeat) ─────────────────────────────────────


def heartbeat_functions() -> dict:
    tree = ast.parse(HEARTBEAT.read_text(encoding="utf-8"))
    wanted = {"mcp_unreachable_alert", "_open_mcp_unreachable_alert", "_resolve_mcp_unreachable_alert"}
    constants = {"MCP_UNREACHABLE_STREAK", "MCP_UNREACHABLE_SENSOR"}
    body = [
        node
        for node in tree.body
        if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in wanted)
        or (isinstance(node, ast.Assign) and {t.id for t in node.targets if isinstance(t, ast.Name)} & constants)
    ]
    ns = {"AlertEnvelope": AlertEnvelope, "json": json, "GREENHOUSE_ID": "vallery"}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(HEARTBEAT), "exec"), ns)  # noqa: S102
    assert wanted | constants <= set(ns)
    return ns


class Recorder:
    def __init__(self):
        self.calls = []

    async def execute(self, sql, *args):
        self.calls.append((" ".join(sql.split()), args))
        return "OK"


def test_mcp_unreachable_is_a_critical_alert_log_row_opened_once_per_outage():
    ns = heartbeat_functions()
    payload = ns["mcp_unreachable_alert"]("http://verdify-mcp:8000/mcp", 3)
    env = AlertEnvelope.model_validate(payload)
    assert (env.alert_type, env.severity, env.category, env.sensor_id) == (
        "mcp_unreachable",
        "critical",
        "system",
        "system.mcp",
    )
    conn = Recorder()
    asyncio.run(ns["_open_mcp_unreachable_alert"](conn, "http://verdify-mcp:8000/mcp", 4))
    [(sql, args)] = conn.calls
    assert sql.startswith("INSERT INTO alert_log") and "WHERE NOT EXISTS" in sql
    assert "'ingestor'" in sql and "'system'" not in sql  # the monitor auto-resolves only its own source='system' rows
    assert args[0] == "mcp_unreachable" and args[3] == "system.mcp" and json.loads(args[6])["consecutive_failures"] == 4


def test_mcp_reachable_again_resolves_the_open_row():
    ns = heartbeat_functions()
    conn = Recorder()
    asyncio.run(ns["_resolve_mcp_unreachable_alert"](conn))
    [(sql, args)] = conn.calls
    assert sql.startswith("UPDATE alert_log SET disposition = 'resolved'")
    assert "alert_type = 'mcp_unreachable'" in sql and "resolved_at IS NULL" in sql and args == ("system.mcp",)


def test_the_heartbeat_opens_at_the_streak_and_resolves_on_recovery():
    source = HEARTBEAT.read_text(encoding="utf-8")
    assert "elif _mcp_unreachable_streak >= MCP_UNREACHABLE_STREAK:" in source
    assert "await _open_mcp_unreachable_alert(conn, mcp_url, _mcp_unreachable_streak)" in source
    assert "await _resolve_mcp_unreachable_alert(conn)" in source
    # PLAN DELIVERY FAILED is logged only: planner_required_plan_missed is its alert_log row.
    assert "planner_required_plan_missed" in (ROOT / "ingestor/tasks/alerts.py").read_text(encoding="utf-8")


# ── forecast_action_alert (forecast-action engine) ──────────────────────────


@pytest.fixture(scope="module")
def engine():
    spec = importlib.util.spec_from_file_location("forecast_action_engine_local_alerts", ENGINE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_fired_alert_rule_is_a_warning_alert_log_payload(engine):
    at = datetime(2026, 11, 6, 11, 0, tzinfo=UTC)
    payload = engine.forecast_alert_payload(
        rule_id=7,
        name="frost_warning",
        metric="temp_f",
        op="<",
        threshold=35.0,
        value=31.5,
        forecast_at=at,
        window="24 hours",
    )
    env = AlertEnvelope.model_validate(payload)
    assert (env.alert_type, env.severity, env.category, env.sensor_id) == (
        "forecast_action_alert",
        "warning",  # dashboard only: warnings never page (#1352)
        "climate",
        "forecast.rule.frost_warning",
    )
    assert datetime.fromisoformat(env.details["forecast_at"]) == at and env.metric_value == 31.5
    assert engine.FORECAST_ALERT_SOURCE != "system"


class EngineConn:
    def __init__(self, open_id=None):
        self.open_id, self.calls = open_id, []

    async def fetchval(self, sql, *args):
        self.calls.append((" ".join(sql.split()), args))
        if sql.lstrip().startswith("UPDATE"):
            return self.open_id
        return 41


def test_the_rule_refreshes_its_open_row_else_inserts_one(engine):
    payload = engine.forecast_alert_payload(
        rule_id=7,
        name="frost_warning",
        metric="temp_f",
        op="<",
        threshold=35.0,
        value=31.5,
        forecast_at=datetime.now(UTC) + timedelta(hours=5),
        window="24 hours",
    )
    refreshed = EngineConn(open_id=12)
    assert asyncio.run(engine.record_forecast_alert(refreshed, payload)) == 12
    assert len(refreshed.calls) == 1 and refreshed.calls[0][0].startswith("UPDATE alert_log")
    inserted = EngineConn()
    assert asyncio.run(engine.record_forecast_alert(inserted, payload)) == 41
    assert [sql.split()[0] for sql, _ in inserted.calls] == ["UPDATE", "INSERT"]
    assert inserted.calls[1][1][7] == "forecast_engine"


def test_the_engine_resolves_alerts_whose_forecast_hour_passed(engine):
    source = ENGINE.read_text(encoding="utf-8")
    assert re.search(r"if not DRY_RUN:\s+await resolve_passed_forecast_alerts\(conn\)", source)
    assert "(details->>'forecast_at')::timestamptz < now()" in source


# ── the prod render ─────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def prod_render() -> str:
    return subprocess.run(
        ["kustomize", "build", str(ROOT / "deploy/k8s/overlays/prod")], check=True, capture_output=True, text=True
    ).stdout


def test_the_prod_render_carries_no_slack_env_secret_or_relay(prod_render):
    docs = [doc for doc in yaml.safe_load_all(prod_render) if doc]
    for doc in docs:
        spec = (doc.get("spec") or {}).get("template", {}).get("spec") or {}
        for container in [*spec.get("containers", []), *spec.get("initContainers", [])]:
            for env in container.get("env") or []:
                assert not env["name"].startswith("SLACK_") and env["name"] != "VERDIFY_SLACK_CONFIG", (
                    doc["metadata"]["name"],
                    env["name"],
                )
        for volume in spec.get("volumes") or []:
            secret = (volume.get("secret") or {}).get("secretName", "")
            assert "slack" not in secret, (doc["metadata"]["name"], secret)
    assert "slack.com" not in prod_render and "/etc/verdify/slack" not in prod_render


def test_hermes_cannot_send_a_message_and_iris_is_never_told_to():
    """#1352: Hermes keeps planning but has no messaging tool, and no Iris prompt or skill reference asks for a
    Slack or #greenhouse post (the brief is the run's final reply)."""
    canonical = yaml.safe_load((ROOT / "hermes/iris/config.yaml").read_text())
    manifest = yaml.safe_load((ROOT / "deploy/k8s/components/hermes-iris/hermes-config.yaml").read_text())
    embedded = yaml.safe_load(manifest["data"]["config.yaml"])
    for profile in (canonical, embedded):
        assert "messaging" in profile["agent"]["disabled_toolsets"]
        assert "slack" not in json.dumps(profile.get("platforms", {})).lower()
    for name in (
        "ingestor/iris_planner.py",
        "docs/planner/greenhouse-playbook.md",
        "hermes/iris/SOUL.md",
        "hermes/iris/SKILL.md",
        "deploy/k8s/components/hermes-iris/iris-inventory.yaml",
    ):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "#greenhouse" not in text, name
        assert not re.search(r"(post|posting)\b[^.\n]{0,40}\bslack|slack (post|brief)", text, re.IGNORECASE), name
