"""Tests for the replay CLI.

This file verifies that replaying a saved planner request still works end to
end and still produces a useful artifact. It connects local debugging tooling
to reliable automated coverage.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from tests.helpers import tier1_active_plan_summary


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_replay_script_replays_fixture_and_writes_output(tmp_path: Path) -> None:
    fixture_path = tmp_path / "fixture.json"
    output_path = tmp_path / "result.json"
    trigger_id = str(uuid4())
    fixture_payload = {
        "trigger": {
            "trigger_id": trigger_id,
            "greenhouse_id": "vallery",
            "event_type": "SUNRISE",
            "event_label": "Sunrise planning cycle",
            "expected_action": "set_plan",
            "triggered_at": "2026-05-19T06:00:00-06:00",
            "planner_instance": "planner_graph",
            "source": "fixture",
        },
        "planner": {
            "run_mode": "production",
            "contract_version": "2026-05-24",
            "context_version": "v1",
            "request_id": "fixture-test-001",
            "trace_id": "trace-fixture-test-001",
        },
        "context": {
            "climate_snapshot": {"temp_f": 72.5, "vpd_kpa": 1.1, "rh_pct": 60},
            "scorecard_summary": {"planner_score": 80.0, "compliance_pct": 90.0},
            "forecast_summary": {
                "headline": "Hot and dry afternoon expected",
                "max_vpd_kpa": 1.8,
            },
            "active_plan_summary": tier1_active_plan_summary(future_waypoints=3),
            "alerts_summary": ["warning: no blocking alerts"],
            "clamp_summary": {"active_clamps_24h": 0},
            "guardrail_audit_summary": {"readback_freshness_seconds": 45},
            "retrieval_refs": [
                {"id": "lesson-1", "snippet": "Watch afternoon VPD peaks."}
            ],
            "site_refs": [
                {
                    "id": "playbook-1",
                    "snippet": "Sunrise plans should bias for midday stress.",
                }
            ],
        },
    }
    fixture_path.write_text(json.dumps(fixture_payload))

    result = subprocess.run(
        [
            sys.executable,
            "scripts/replay_request.py",
            str(fixture_path),
            "--app-factory",
            "planner_graph.app:create_app",
            "--output",
            str(output_path),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    output = json.loads(output_path.read_text())
    assert output["trigger_id"] == trigger_id
    assert output["status"] == "completed"
