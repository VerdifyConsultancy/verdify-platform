"""
Test 07: Cron Jobs & Replan Flow — Scheduled tasks and deviation-triggered replanning.
"""

import subprocess
from pathlib import Path

import pytest

from conftest import db_query

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.legacy_vm
class TestCronJobs:
    """All expected cron jobs must be configured."""

    @pytest.fixture(scope="class")
    def crontab(self):
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=5)
        return result.stdout

    def test_planner_heartbeat(self, crontab):
        """Planner runs via ingestor planning_heartbeat task (event-driven), not cron."""
        import subprocess as sp

        result = sp.run(["systemctl", "is-active", "verdify-ingestor"], capture_output=True, text=True, timeout=5)
        assert result.stdout.strip() == "active", "Ingestor service not running (planning_heartbeat lives here)"

    def test_metrics_cron(self, crontab):
        """Verdify metrics collection must run every minute."""
        assert "verdify-metrics" in crontab, "Metrics collection cron not found"

    def test_daily_snapshot_cron(self, crontab):
        assert "daily-summary-snapshot" in crontab, "Daily snapshot cron not found"

    def test_backup_cron(self, crontab):
        assert "pg_dump" in crontab or "backup" in crontab.lower(), "Backup cron not found"


class TestReplanFlow:
    """Deviation detection → replan trigger → planner invocation chain."""

    @pytest.mark.live
    def test_deviation_thresholds_configured(self):
        """Deviation thresholds must be in the DB."""
        rows = db_query("SELECT count(*) FROM forecast_deviation_thresholds WHERE enabled = true")
        assert int(rows) >= 3, f"Only {rows} active deviation thresholds"

    def test_replan_trigger_script_exists(self):
        assert (REPO_ROOT / "scripts/check-replan-trigger.sh").is_file()

    def test_replan_trigger_routes_to_iris(self):
        """Replan trigger should route to the Hermes gateway."""
        content = (REPO_ROOT / "scripts/check-replan-trigger.sh").read_text()
        assert "/v1/runs" in content or "HERMES" in content, "Replan trigger doesn't route to Iris"

    @pytest.mark.live
    def test_planner_journal_has_recent_entries(self):
        """At least one plan should have been generated today."""
        count = db_query(
            "SELECT count(*) FROM plan_journal WHERE created_at::date = (now() AT TIME ZONE 'America/Denver')::date"
        )
        assert int(count) >= 1, "No plans generated today"

    @pytest.mark.live
    def test_plan_has_waypoints(self):
        """Most recent plan must have waypoints in setpoint_plan."""
        plan_id = db_query(
            "SELECT plan_id FROM plan_journal WHERE plan_id NOT LIKE 'iris-reactive%' ORDER BY created_at DESC LIMIT 1"
        )
        if plan_id:
            count = db_query(f"SELECT count(*) FROM setpoint_plan WHERE plan_id = '{plan_id}' AND is_active = true")
            assert int(count) >= 24, f"Plan {plan_id} has only {count} waypoints (expected >=24)"


class TestPlannerConfig:
    """Planner configuration must be correct."""

    def test_ai_config_loads(self):
        import sys

        repo_root = Path(__file__).resolve().parents[1]
        sys.path.insert(0, str(repo_root / "ingestor"))
        from ai_config import ai

        assert ai.model_name("planner") == "gpt-5.6-sol"
        assert ai.config["models"]["planner"]["provider"] == "openai"

    def test_planner_credential_is_secret_ref_not_retired_file(self):
        manifest = (REPO_ROOT / "deploy/k8s/components/planner/planner-deployment.yaml").read_text()
        assert "name: OPENAI_API_KEY" in manifest
        assert "secretKeyRef:" in manifest
        assert "/mnt/agents/shared/credentials/anthropic_api_key.txt" not in manifest

    @pytest.mark.live
    def test_planner_lessons_not_excessive(self):
        """Active lessons should be <= 25 (query caps at 10, but DB may have more).

        Counts the canonical LIVE set (``is_active = true AND superseded_by IS
        NULL``) — the exact predicate the planner read path uses. Migration 156
        (issue #38) canonicalizes that set from 57 down to <=25 via
        supersede/retire (provenance preserved, no hard delete). This asserts
        the migration's <=25 contract instead of the prior <=50 relaxation, so
        it goes green only once migration 156 has been APPLIED to the DB.
        """
        count = db_query("SELECT count(*) FROM planner_lessons WHERE is_active = true AND superseded_by IS NULL")
        assert int(count) <= 25, f"{count} active lessons (>25 = needs cleanup; apply migration 156)"
