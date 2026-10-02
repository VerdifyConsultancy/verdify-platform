"""
Test 07: Cron Jobs & Replan Flow — Scheduled tasks and deviation-triggered replanning.
"""

from pathlib import Path

from conftest import db_query


class TestCronJobs:
    """Current schedules live in k3s and the ingestor, not the host crontab."""

    def test_planner_and_summary_native_scheduler(self):
        source = (Path(__file__).resolve().parents[1] / "ingestor/ingestor.py").read_text()
        assert '("planning_heartbeat", 60, planning_heartbeat)' in source
        assert '("daily_summary_live", 1800, daily_summary_live)' in source

    def test_backup_native_schedule(self):
        from test_01_infrastructure import production_documents

        backup = next(
            x for x in production_documents() if x["kind"] == "CronJob" and x["metadata"]["name"] == "verdify-db-backup"
        )
        assert backup["spec"]["schedule"] == "17 2 * * *"
        assert backup["spec"]["concurrencyPolicy"] == "Forbid"


class TestReplanFlow:
    """Deviation detection → replan trigger → planner invocation chain."""

    def test_deviation_thresholds_configured(self):
        """Deviation thresholds must be in the DB."""
        rows = db_query("SELECT count(*) FROM forecast_deviation_thresholds WHERE enabled = true")
        assert int(rows) >= 3, f"Only {rows} active deviation thresholds"

    def test_replan_trigger_script_exists(self):
        assert (Path(__file__).resolve().parents[1] / "scripts/check-replan-trigger.sh").is_file()

    def test_replan_trigger_routes_to_iris(self):
        """Replan trigger should route to the Hermes gateway."""
        with (Path(__file__).resolve().parents[1] / "scripts/check-replan-trigger.sh").open() as f:
            content = f.read()
        assert "/v1/runs" in content or "HERMES" in content, "Replan trigger doesn't route to Iris"

    def test_planner_journal_has_recent_entries(self):
        """At least one plan should have been generated today."""
        count = db_query(
            "SELECT count(*) FROM plan_journal WHERE created_at::date = (now() AT TIME ZONE 'America/Denver')::date"
        )
        assert int(count) >= 1, "No plans generated today"

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

        planner = ai.model("planner")
        assert planner == {
            "provider": "custom",
            "model": "gpt-5.6-luna",
            "base_url": "https://api.openai.com/v1",
            "reasoning_effort": "xhigh",
            "purpose": "Audit metadata for the live Hermes Iris profile. Runtime source: hermes/iris/config.yaml.",
        }

    def test_planner_declares_current_credential_owner(self):
        import yaml

        root = Path(__file__).resolve().parents[1]
        document = next(
            x
            for x in yaml.safe_load_all((root / "deploy/k8s/components/hermes-iris/hermes-iris.yaml").read_text())
            if x and x["kind"] == "Deployment"
        )
        container = document["spec"]["template"]["spec"]["containers"][0]
        assert {"secretRef": {"name": "verdify-hermes"}} in container["envFrom"]
        # Values stay in central KSOPS; a filesystem API-key test is obsolete.

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
