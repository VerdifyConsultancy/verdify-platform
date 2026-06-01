"""Tests for the eval CLI and expectation logic.

This file checks that the eval script interprets fixture expectations the way
the repo intends. It connects planner quality tooling to reliable regression
checks around scoring behavior.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.eval_openai_planner import expectation_failures


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_expectation_failures_supports_richer_expectations() -> None:
    diagnosis = {
        "situation": "stale telemetry review",
        "likely_cause": "sensor offline",
        "risks": ["weak context"],
        "planning_intent": "fail closed when telemetry is weak",
    }
    draft = {
        "selected_action": "acknowledge_trigger",
        "rationale": "Fallback planner chose acknowledge_trigger for SENSOR.",
        "confidence": 0.35,
        "tunable_changes": {},
        "expected_effect": "No production side effects. OpenAI planner credentials were not configured.",
    }

    failures = expectation_failures(
        diagnosis,
        draft,
        {
            "selected_action_in": ["acknowledge_trigger", "fail"],
            "max_confidence": 0.5,
            "rationale_not_contains": ["approved"],
            "diagnosis_not_contains": ["guaranteed"],
            "empty_payload_fields": ["tunable_changes.fog_escalation_kpa"],
        },
    )

    assert failures == []


def test_eval_script_runs_all_fixtures_and_writes_summary(tmp_path: Path) -> None:
    output_path = tmp_path / "eval-summary.json"
    fixture_paths = sorted(str(path) for path in Path("fixtures/evals").glob("*.json"))
    result = subprocess.run(
        [
            sys.executable,
            "scripts/eval_openai_planner.py",
            *fixture_paths,
            "--output",
            str(output_path),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    summary = json.loads(output_path.read_text())
    assert summary["passed"] == len(fixture_paths)
    assert summary["failed"] == 0
    assert summary["results"][0]["failures"] == []
    assert len(summary["results"]) == len(fixture_paths)
