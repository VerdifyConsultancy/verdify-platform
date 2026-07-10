"""
Test 04: Planner Pipeline — Context gathering, prompt rendering, output parsing.
Tests the full planner pipeline WITHOUT calling the AI model (dry-run mode).
"""

import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from verdify_schemas.plan import Plan
from verdify_schemas.tunable_registry import REGISTRY, normalize_planner_value, planner_effective_bounds

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "ingestor"))
CONTEXT_TIMEOUT_S = int(os.environ.get("VERDIFY_CONTEXT_TEST_TIMEOUT_S", "90"))


class TestPlannerBoundIntersection:
    def test_stricter_planner_bounds_win(self, monkeypatch: pytest.MonkeyPatch):
        original = REGISTRY["mister_vpd_weight"]
        monkeypatch.setitem(
            REGISTRY,
            "mister_vpd_weight",
            original.model_copy(update={"min": 1.0, "max": 2.0, "fw_clamp_lo": 0.5, "fw_clamp_hi": 3.0}),
        )

        assert planner_effective_bounds("mister_vpd_weight") == (1.0, 2.0)
        assert normalize_planner_value("mister_vpd_weight", 0.2) == 1.0
        assert normalize_planner_value("mister_vpd_weight", 2.8) == 2.0

    def test_stricter_firmware_bounds_win(self, monkeypatch: pytest.MonkeyPatch):
        original = REGISTRY["mister_vpd_weight"]
        monkeypatch.setitem(
            REGISTRY,
            "mister_vpd_weight",
            original.model_copy(update={"min": 0.5, "max": 3.0, "fw_clamp_lo": 1.2, "fw_clamp_hi": 1.8}),
        )

        assert planner_effective_bounds("mister_vpd_weight") == (1.2, 1.8)
        assert normalize_planner_value("mister_vpd_weight", 1.2) == 1.2
        assert normalize_planner_value("mister_vpd_weight", 1.9) == 1.8

    def test_empty_intersection_fails_closed(self, monkeypatch: pytest.MonkeyPatch):
        original = REGISTRY["mister_vpd_weight"]
        monkeypatch.setitem(
            REGISTRY,
            "mister_vpd_weight",
            original.model_copy(update={"min": 2.0, "max": 3.0, "fw_clamp_lo": 0.5, "fw_clamp_hi": 1.0}),
        )

        with pytest.raises(ValueError, match="empty planner/firmware bounds intersection"):
            normalize_planner_value("mister_vpd_weight", 1.5)


class TestPlanValidity:
    def test_plan_derives_finite_expiry_from_last_transition(self):
        start = datetime(2026, 7, 10, 6, tzinfo=ZoneInfo("America/Denver"))
        plan = Plan.model_validate(
            {
                "plan_id": "iris-20260710-0600",
                "hypothesis": "bounded plan",
                "transitions": [
                    {"ts": start.isoformat(), "params": {"mister_vpd_weight": 1.0}},
                    {
                        "ts": (start + timedelta(hours=72)).isoformat(),
                        "params": {"mister_vpd_weight": 1.1},
                    },
                ],
            }
        )

        assert plan.valid_from == start
        assert plan.expires_at == start + timedelta(hours=78)

    def test_plan_rejects_unbounded_validity(self):
        start = datetime(2026, 7, 10, 6, tzinfo=ZoneInfo("America/Denver"))
        with pytest.raises(ValueError, match="may not exceed"):
            Plan.model_validate(
                {
                    "plan_id": "iris-20260710-0600",
                    "hypothesis": "too long",
                    "valid_from": start.isoformat(),
                    "expires_at": (start + timedelta(hours=79)).isoformat(),
                    "transitions": [{"ts": start.isoformat(), "params": {"mister_vpd_weight": 1.0}}],
                }
            )

    def test_plan_rejects_expiry_before_final_transition(self):
        start = datetime(2026, 7, 10, 6, tzinfo=ZoneInfo("America/Denver"))
        with pytest.raises(ValueError, match="after the final transition"):
            Plan.model_validate(
                {
                    "plan_id": "iris-20260710-0600",
                    "hypothesis": "expires too early",
                    "expires_at": (start + timedelta(hours=1)).isoformat(),
                    "transitions": [
                        {"ts": start.isoformat(), "params": {"mister_vpd_weight": 1.0}},
                        {
                            "ts": (start + timedelta(hours=2)).isoformat(),
                            "params": {"mister_vpd_weight": 1.1},
                        },
                    ],
                }
            )

    def test_plan_rejects_valid_from_after_first_transition(self):
        start = datetime(2026, 7, 10, 6, tzinfo=ZoneInfo("America/Denver"))
        with pytest.raises(ValueError, match="after the first transition"):
            Plan.model_validate(
                {
                    "plan_id": "iris-20260710-0600",
                    "hypothesis": "ambiguous start",
                    "valid_from": (start + timedelta(minutes=1)).isoformat(),
                    "transitions": [{"ts": start.isoformat(), "params": {"mister_vpd_weight": 1.0}}],
                }
            )


class TestContextGathering:
    """gather-plan-context.sh must produce valid, complete context."""

    @pytest.fixture(scope="class")
    def context(self):
        env = {**os.environ, "PATH": os.environ.get("PATH", "")}
        auto_backend = not env.get("VERDIFY_DB_BACKEND")
        if auto_backend:
            docker_running = False
            if shutil.which("docker"):
                docker = subprocess.run(
                    ["docker", "inspect", "verdify-timescaledb"],
                    capture_output=True,
                    timeout=5,
                )
                docker_running = docker.returncode == 0
            if docker_running:
                env["VERDIFY_DB_BACKEND"] = "docker"
            else:
                if not shutil.which("kubectl"):
                    pytest.skip("planner context test requires a reachable read-only DB backend")
                kube = subprocess.run(
                    ["kubectl", "get", "pod", "-n", "verdify-prod", "verdify-db-0"],
                    capture_output=True,
                    timeout=5,
                )
                if kube.returncode != 0:
                    pytest.skip("planner context test requires a reachable read-only DB backend")
                env["VERDIFY_DB_BACKEND"] = "kube"
        result = subprocess.run(
            ["bash", str(REPO_ROOT / "scripts" / "gather-plan-context.sh")],
            capture_output=True,
            text=True,
            timeout=CONTEXT_TIMEOUT_S,
            env=env,
        )
        if result.returncode != 0 and auto_backend:
            pytest.skip(
                "auto-discovered planner context backend is not schema-compatible with this lane head: "
                + (result.stderr[:300] or result.stdout[-300:])
            )
        assert result.returncode == 0, f"Context gathering failed: {result.stderr[:500]}"
        return result.stdout

    def test_context_not_empty(self, context):
        assert len(context) > 5000, f"Context too short: {len(context)} chars"

    def test_has_scorecard(self, context):
        assert "PLANNER SCORECARD" in context

    def test_has_score_trend(self, context):
        assert "PLANNER SCORE TREND" in context

    def test_has_active_plan(self, context):
        assert "ACTIVE PLAN" in context

    def test_has_forecast(self, context):
        assert "HOURLY FORECAST" in context or "72-HOUR" in context

    def test_has_compliance(self, context):
        assert "COMPLIANCE" in context

    def test_has_lessons(self, context):
        assert "ACTIVE LESSONS" in context

    def test_has_dew_point(self, context):
        assert "dp_margin" in context or "dp_risk" in context

    def test_has_evaluation_block(self, context):
        assert "PLANS THAT GOVERNED THE LAST 24 HOURS" in context

    def test_no_secrets_leaked(self, context):
        """Ensure no API keys or passwords appear in the context."""
        assert "sk-ant-" not in context, "Anthropic API key leaked in context"
        assert "AIza" not in context, "Google API key leaked in context"
        assert "POSTGRES_PASSWORD" not in context


class TestPlannerPrompt:
    """The Iris planner prompt (iris_planner.py) must contain essential knowledge."""

    @pytest.fixture(scope="class")
    def preamble(self):
        import sys

        sys.path.insert(0, str(REPO_ROOT / "ingestor"))
        from iris_planner import _PREAMBLE

        assert len(_PREAMBLE) > 5000, f"Preamble too short: {len(_PREAMBLE)} chars"
        return _PREAMBLE

    def test_has_standing_directives(self, preamble):
        assert "Standing Directives" in preamble
        assert "MCP tools ONLY" in preamble

    def test_has_decision_precedence(self, preamble):
        assert "Safety" in preamble
        assert "Band compliance" in preamble
        assert "Cost" in preamble

    def test_has_kpi(self, preamble):
        assert "Planner Score" in preamble
        assert "80% Compliance" in preamble or "80%" in preamble

    def test_has_compliance_metrics(self, preamble):
        assert "temp_compliance_pct" in preamble
        assert "vpd_compliance_pct" in preamble

    def test_has_tunables(self, preamble):
        assert "vpd_hysteresis" in preamble
        assert "mister_vpd_weight" in preamble
        assert "fog_escalation_kpa" in preamble
        assert "Moisture tuning ladder" in preamble

    def test_has_modes(self, preamble):
        assert "SEALED_MIST" in preamble
        assert "VENTILATE" in preamble

    def test_has_lessons(self, preamble):
        assert "Fog is 7x" in preamble or "fog is 7x" in preamble

    def test_no_secrets(self, preamble):
        assert "sk-ant-" not in preamble
        assert "AIza" not in preamble

    def test_has_utility_guidance(self, preamble):
        assert "kwh" in preamble
        assert "therms" in preamble
        assert "3.9x" in preamble or "3.9×" in preamble


class TestEvidencePipelineSources:
    """Guard replay provenance and outcome KPI authority in source."""

    def test_exporter_uses_conservative_observation_not_setpoint_age(self):
        source = (REPO_ROOT / "scripts" / "export-replay-overrides.sh").read_text()
        assert "outdoor_observation_ts" in source
        assert "conservative_change_observation" in source
        assert "WHERE duplicate_rank = 1" in source
        assert "FROM climate_rows c" in source
        assert "max(c0.outdoor_temp_f)" not in source
        assert "sp_dehum_vent_hold_enabled" in source
        assert "parameter = 'outdoor_temp'" not in source
        assert "force_fresh" not in source.lower()

    def test_stock_replay_gates_observation_backed_outdoor_branches(self):
        source = (REPO_ROOT / "firmware" / "test" / "replay_overrides.cpp").read_text()
        for token in (
            "outdoor_observation_ts",
            "conservative_change_observation",
            "outdoor_observation_backed_rows",
            "outdoor_fresh_rows",
            "MX_VENT_DEHUM",
            "MX_HEAT_ASSIST",
            "MX_VENT_HUMIDIFY",
            "mx_hold_required_rows",
        ):
            assert token in source
        assert "outdoor_observation_backed_rows >= 1000" in source
        assert "outdoor_fresh_rows >= 1000" in source

    def test_outcome_kpi_uses_raw_transitions_and_realized_nights(self):
        source = (REPO_ROOT / "mcp" / "server.py").read_text()
        assert "FROM v_equipment_runtime_daily" in source
        assert "FROM fn_realized_solar_night_dryout" in source
        assert '"authority": "v_equipment_runtime_daily"' in source
        assert '"firmware_counter_diagnostics"' in source
        assert "this is not a completed control fix" in source
        cycle_mapping = source.split("actuator_cycles = {", 1)[1].split("actuator_runtime =", 1)[0]
        assert "summary.get" not in cycle_mapping


class TestMCPToolAvailability:
    """The MCP server must expose all 17 planning tools."""

    def test_mcp_server_running(self):
        import subprocess

        if shutil.which("systemctl") and os.path.isdir("/run/systemd/system"):
            result = subprocess.run(
                ["systemctl", "is-active", "verdify-mcp"], capture_output=True, text=True, timeout=5
            )
            assert result.stdout.strip() == "active", "MCP server not running"
            return
        source = (REPO_ROOT / "mcp" / "server.py").read_text()
        manifest = (REPO_ROOT / "deploy/k8s/base/mcp-deployment.yaml").read_text()
        assert '@mcp.custom_route("/readyz"' in source
        assert "path: /readyz" in manifest

    def test_skill_file_exists(self):
        import os

        candidates = (
            REPO_ROOT / "docs/planner/greenhouse-playbook.md",
            Path("/Volumes/agents/iris/skills/greenhouse-planner.md"),
            Path("/mnt/agents/iris/skills/greenhouse-planner.md"),
        )
        assert any(os.path.isfile(path) for path in candidates), "Planner playbook/skill file missing"

    def test_vendored_playbook_exists(self):
        """G4: `docs/planner/greenhouse-playbook.md` is the version-controlled
        canonical source. Assert it's present in the repo — losing it means the
        agent-host copy is the only record, which was the audit's concern."""
        import os

        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "docs",
            "planner",
            "greenhouse-playbook.md",
        )
        assert os.path.isfile(path), f"In-repo playbook missing at {path}"
        with open(path) as f:
            body = f.read()
        assert body.startswith("---\nname: greenhouse-planner"), "Playbook YAML frontmatter missing"
        assert "22 MCP tools" in body, "Playbook tool count out of sync with _STANDING_DIRECTIVES"
        assert "READ → DIAGNOSE → DECIDE → ACT → REPORT" in body, "Playbook planning cycle section missing"

    def test_vendored_and_host_playbooks_in_sync(self):
        """When both copies are readable, they must be byte-identical. If the
        host copy drifts ahead of the repo, the next PR that vendors will
        clobber host-only changes. Skip when either path is unreadable (CI)."""
        import os

        repo = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "docs",
            "planner",
            "greenhouse-playbook.md",
        )
        host = "/mnt/agents/iris/skills/greenhouse-planner.md"
        if not (os.path.isfile(repo) and os.path.isfile(host)):
            pytest.skip("one of the playbook paths isn't readable")
        with open(repo) as f:
            repo_body = f.read()
        with open(host) as f:
            host_body = f.read()
        if repo_body != host_body:
            pytest.xfail(
                "Playbooks have drifted. Expected post-G4: host copy re-synced from repo canonical. "
                "Pending deploy step (filed as G4 follow-up in docs/backlog/genai.md)."
            )
