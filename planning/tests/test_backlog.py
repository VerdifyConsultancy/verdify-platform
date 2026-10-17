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


@pytest.fixture
def execution_data(data):
    data["execution_plan"] = {
        "reviewed_at": "2026-10-12",
        "source_revision": "b5bdb3c65b243c230b7864fa81b95f926b2e68ff",
        "capacity_hours_per_week": 40,
        "review_path": "planning/reviews/2026-10-12/README.md",
        "open_issues": [396, 317],
        "sprints": [
            {
                "id": "S2",
                "title": "S2 — Current recovery",
                "goal": "Qualify a contained recovery target",
                "exit": "Current recovery evidence is sealed",
                "kind": "sprint",
                "capacity_hours": 40,
            },
            {
                "id": "OWNER",
                "title": "Owner follow-through",
                "goal": "Track external implementation",
                "exit": "The scoped owner correction is verified",
                "kind": "ongoing",
                "capacity_hours": None,
            },
        ],
        "allocations": [
            {
                "issue_number": 396,
                "sprint": "S2",
                "hours_min": 12,
                "hours_max": 20,
                "next_action": "Refresh storage admission before isolated recovery",
                "owner_role": "Data/platform lead",
            },
            {
                "issue_number": 317,
                "sprint": "OWNER",
                "hours_min": None,
                "hours_max": None,
                "next_action": "Retain the upstream correction and safe sync mechanics",
                "owner_role": "Platform lead",
            },
        ],
    }
    return data


def test_execution_plan_covers_open_work_and_renders_current_sprints(execution_data):
    from planning.render import outputs

    plan = Backlog.model_validate(execution_data)
    rendered = outputs(plan)
    for path in ["PROJECT_BOARD.md", "SPRINTS.md", "MILESTONES.md", "LANES.md"]:
        text = rendered[path]
        assert text.index("## Current execution plan") < text.index("## Campaign stage contracts")
        assert "planning/reviews/2026-10-12/README.md" in text
        assert "2 open issues" in text
    sprints = rendered["SPRINTS.md"]
    assert "## S2 — Current recovery" in sprints
    assert "S2 — S2" not in sprints
    assert "Goal: Qualify a contained recovery target" in sprints
    assert "Exit: Current recovery evidence is sealed" in sprints
    assert "Data/platform lead | 12–20 | Refresh storage admission" in sprints
    assert "Platform lead | Unestimated | Retain the upstream correction" in sprints
    assert "## C0 —" in sprints
    assert "[current reviewed native graph](reviews/2026-10-12/DEPENDENCIES.md)" in rendered["planning/DEPENDENCIES.md"]


def test_execution_plan_preserves_issue_bodies_and_manifest(execution_data):
    from planning.render import issue_body, outputs

    current = Backlog.model_validate(execution_data)
    execution_data.pop("execution_plan")
    legacy = Backlog.model_validate(execution_data)
    assert legacy.execution_plan is None
    assert outputs(current)["planning/issues_manifest.json"] == outputs(legacy)["planning/issues_manifest.json"]
    assert [issue_body(i, current) for i in current.issues] == [issue_body(i, legacy) for i in legacy.issues]


@pytest.mark.parametrize("minimum,maximum", [(None, 2), (2, None), (-1, 2), (3, 2)])
def test_execution_bad_hour_range_rejected(execution_data, minimum, maximum):
    allocation = execution_data["execution_plan"]["allocations"][0]
    allocation.update(hours_min=minimum, hours_max=maximum)
    with pytest.raises(ValidationError, match="nonnegative range"):
        Backlog.model_validate(execution_data)


@pytest.mark.parametrize("change", ["missing", "extra", "duplicate", "duplicate_open"])
def test_execution_allocation_coverage_rejected(execution_data, change):
    execution = execution_data["execution_plan"]
    if change == "missing":
        execution["allocations"].pop()
    elif change == "extra":
        execution["allocations"].append({**execution["allocations"][0], "issue_number": 801})
    elif change == "duplicate":
        execution["allocations"].append(execution["allocations"][0])
    else:
        execution["open_issues"].append(396)
    with pytest.raises(ValidationError, match="coverage|duplicate"):
        Backlog.model_validate(execution_data)


@pytest.mark.parametrize("change", ["unknown_sprint", "duplicate_sprint", "unknown_issue", "zero_capacity"])
def test_execution_invalid_reference_or_capacity_rejected(execution_data, change):
    execution = execution_data["execution_plan"]
    if change == "unknown_sprint":
        execution["allocations"][0]["sprint"] = "missing"
    elif change == "duplicate_sprint":
        execution["sprints"].append(execution["sprints"][0])
    elif change == "unknown_issue":
        execution["open_issues"][0] = execution["allocations"][0]["issue_number"] = 999999
    else:
        execution["capacity_hours_per_week"] = 0
    with pytest.raises(ValidationError, match="unknown|duplicate|positive"):
        Backlog.model_validate(execution_data)
