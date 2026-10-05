"""Frozen #778 raw-row reproduction and fail-closed custody checks."""

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "wetting_incident_join", ROOT / "research/planner-efficacy/wetting_incident_join.py"
)
incident = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(incident)


def test_frozen_raw_join_matches_published_receipt_and_retains_unknowns():
    expected = json.loads((ROOT / "research/planner-efficacy/results-wetting-incident-2026-09-04-v4.json").read_bytes())
    actual = incident.analyze()
    assert actual == expected
    assert actual["reproduced_refusal"]["action_rows"] == 199
    assert actual["physical_wetting_proof_allowed"] is False
    assert actual["readiness_constraint"]["complete"] is False
    assert actual["counter_observations_are_effective_limits"] is False
    assert actual["unknowns"]
    assert all(row["action_row_sha256"] for row in actual["timeline"])


@pytest.mark.parametrize("file", ["climate.csv", "queries.json", "manifest.json"])
def test_changed_frozen_row_sql_or_manifest_fails_closed(tmp_path, file):
    inputs = tmp_path / "inputs"
    shutil.copytree(incident.INPUTS, inputs)
    path = inputs / file
    if file == "queries.json":
        queries = json.loads(path.read_bytes())
        queries["climate"] += " WHERE false"
        path.write_text(json.dumps(queries))
    else:
        path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError):
        incident.analyze(inputs)
