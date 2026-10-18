"""Exercise the actual outcome_kpi projection without importing service runtime.

Inputs here are synthetic SQL-result rows, not measured greenhouse episodes.
"""

import ast
from pathlib import Path

import pytest


def project(rows):
    source = Path(__file__).resolve().parents[1] / "mcp" / "server.py"
    tree = ast.parse(source.read_text())
    function = next(
        node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == "outcome_kpi"
    )
    assignments = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Assign)
        and any(ast.unparse(target) == "vpd_policy['realized_solar_night_dryout']" for target in node.targets)
    ]
    assert len(assignments) == 1
    namespace = {"dryout": rows, "vpd_policy": {}}
    # Execute only this repository-owned assignment; never supplied input code.
    exec(compile(ast.Module(body=assignments, type_ignores=[]), str(source), "exec"), namespace)  # noqa: S102
    return namespace["vpd_policy"]["realized_solar_night_dryout"]


def episode(status, disposition="insufficient_evidence", daytime=11, held=0):
    return {
        "safety_gate_status": status,
        "dryout_disposition": disposition,
        "daytime_dry_action_samples": daytime,
        "daytime_hold_admission_samples": held,
        "evidence_status": "incomplete",
        "confound_reasons": ["missing_outdoor_ah"],
        "observed_indoor_ah_delta_10_20m_g_m3": None,
    }


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ([], "pending"),
        (["pass"], "pass"),
        (["pass", "pass"], "pass"),
        (["incomplete"], "incomplete"),
        (["pass", "incomplete"], "incomplete"),
        (["fail"], "fail"),
        (["pass", "incomplete", "fail"], "fail"),
        (["fail", "incomplete", "pass"], "fail"),
        (["unknown"], "incomplete"),
        ([None], "incomplete"),
        (["pass", "unknown"], "incomplete"),
        (["fail", None], "fail"),
    ],
)
def test_quality_is_not_promoted_to_pass(statuses, expected):
    assert project([episode(status) for status in statuses])["safety_gate_status"] == expected


def test_measured_rows_and_repeated_day_counts_survive_projection():
    rows = [
        episode("pass", "effective"),
        episode("fail", "ineffective", held=2),
        episode("pass", "blocked"),
        episode("incomplete"),
    ]
    result = project(rows)
    assert result["episodes"] == rows
    assert result["dispositions"] == {
        "effective": 1,
        "ineffective": 1,
        "blocked": 1,
        "insufficient_evidence": 1,
    }
    # SQL repeats these adjacent-day counts on each episode. Do not sum them.
    assert result["daytime_dry_action_samples"] == 11
    assert result["daytime_hold_admission_samples"] == 2
    assert result["safety_gate_status"] == "fail"


def test_no_rows_is_pending_not_measured_zero_daytime_admission():
    result = project([])
    assert result["episodes"] == []
    assert result["safety_gate_status"] == "pending"
    assert all(count == 0 for count in result["dispositions"].values())


def test_pass_safety_does_not_convert_incomplete_outcome_to_effective():
    result = project([episode("pass")])
    assert result["safety_gate_status"] == "pass"
    assert result["dispositions"]["insufficient_evidence"] == 1
    assert result["dispositions"]["effective"] == 0
    assert result["episodes"][0]["evidence_status"] == "incomplete"
