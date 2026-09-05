"""Positive and adversarial planning checks; no network or device access."""

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from planning.schema import Backlog, load, validate_delivery

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def data():
    return load(ROOT / "planning/backlog.yaml").model_dump()


def test_complete_published_plan():
    plan = load(ROOT / "planning/backlog.yaml")
    validate_delivery(plan, ROOT)
    assert len(plan.original_open_issues) == 68
    assert len(plan.issues) >= 78


@pytest.mark.parametrize("field", ["what", "why", "delivery", "rollback", "decision"])
def test_empty_detail_rejected(data, field):
    data["issues"][0][field] = " "
    with pytest.raises(ValidationError, match="empty"):
        Backlog.model_validate(data)


@pytest.mark.parametrize("field", ["how", "acceptance", "paths"])
def test_empty_list_rejected(data, field):
    data["issues"][0][field] = []
    with pytest.raises(ValidationError, match="empty"):
        Backlog.model_validate(data)


def test_lost_original_issue_rejected(data):
    data["issues"] = [i for i in data["issues"] if i["key"] != "747"]
    with pytest.raises(ValidationError, match="coverage"):
        Backlog.model_validate(data)


def test_duplicate_rejected(data):
    data["issues"].append(data["issues"][0])
    with pytest.raises(ValidationError, match="duplicate"):
        Backlog.model_validate(data)


def test_unresolved_dependency_rejected(data):
    data["issues"][0]["depends_on"] = ["missing"]
    with pytest.raises(ValidationError, match="unknown issue"):
        Backlog.model_validate(data)


def test_cycle_rejected(data):
    items = {i["key"]: i for i in data["issues"]}
    items["747"]["depends_on"] = ["749"]
    items["749"]["depends_on"] = ["747"]
    with pytest.raises(ValidationError, match="cycle"):
        Backlog.model_validate(data)


def test_parent_gate_rejected(data):
    item = next(i for i in data["issues"] if i["key"] == "641")
    item["depends_on"].append(item["parent"])
    with pytest.raises(ValidationError, match="child blocked by parent"):
        Backlog.model_validate(data)


def test_hierarchy_cycle_rejected(data):
    data["issues"][0]["parent"] = "581"
    with pytest.raises(ValidationError, match="hierarchy cycle"):
        Backlog.model_validate(data)


def test_unknown_prose_reference_rejected(data):
    data["issues"][0]["what"] += " #missing_task"
    with pytest.raises(ValidationError, match="unknown prose"):
        Backlog.model_validate(data)


def test_missing_source_path_rejected(data):
    data["issues"][0]["paths"] = ["missing/campaign/path"]
    with pytest.raises(ValueError, match="source path"):
        validate_delivery(Backlog.model_validate(data), ROOT)


def test_no_lock_launch_outcome_cycle(data):
    items = {i["key"]: i for i in data["issues"]}
    assert "integration" in items["588"]["depends_on"]
    assert items["642"]["depends_on"] == ["588"]
    assert items["640"]["depends_on"] == ["642"]
    assert items["pilot_run"]["depends_on"] == ["640"]
    assert items["readout"]["depends_on"] == ["pilot_run"]


def test_generated_files_are_current():
    from planning.render import outputs

    plan = load(ROOT / "planning/backlog.yaml")
    for path, expected in outputs(plan).items():
        assert (ROOT / path).read_text() == expected, f"regenerate {path}"


def test_archived_source_is_byte_exact():
    archive = ROOT / "planning/archive/2026-09-05"
    manifest = json.loads((archive / "source-manifest.json").read_text())
    assert len(manifest["files"]) == 10
    for record in manifest["files"]:
        assert hashlib.sha256((archive / record["archive_path"]).read_bytes()).hexdigest() == record["sha256"]


def test_issue_rollback_snapshot_covers_original_metadata():
    plan = load(ROOT / "planning/backlog.yaml")
    records = json.loads((ROOT / "planning/archive/2026-09-05/issues.json").read_text())
    assert {i["number"] for i in records} == set(plan.original_open_issues)
    for record in records:
        assert record["title"] and record["body"] and record["updated_at"]
        assert isinstance(record["labels"], list)
        assert isinstance(record["assignees"], list)
        assert "milestone" in record
