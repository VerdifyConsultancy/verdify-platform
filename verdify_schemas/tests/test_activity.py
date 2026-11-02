"""verdify_schemas.activity (VD-31): the activity_log and desired_state row models of migration 280."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

import verdify_schemas
from verdify_schemas.activity import ActivityLogRow, DesiredStateRow

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "db/migrations/280-desired-state-activity-log.sql"


def migration_columns(table: str) -> set[str]:
    """Column names in migration 280's CREATE TABLE for ``table`` (constraint lines left out)."""
    body = MIGRATION.read_text().split(f"CREATE TABLE public.{table} (", 1)[1].split("\n);", 1)[0]
    words = (line.split()[0] for line in body.splitlines() if line.strip())
    return {word for word in words if word not in {"CHECK", "UNIQUE", "PRIMARY", "CONSTRAINT"}}


def test_the_models_declare_every_migration_280_column():
    assert set(ActivityLogRow.model_fields) == migration_columns("activity_log")
    assert set(DesiredStateRow.model_fields) == migration_columns("desired_state")
    assert verdify_schemas.ActivityLogRow is ActivityLogRow and verdify_schemas.DesiredStateRow is DesiredStateRow


def test_an_activity_row_validates_a_keyed_agent_write():
    key, experiment = uuid.uuid4(), uuid.uuid4()
    row = ActivityLogRow(
        source="agent:steward",
        actor="steward",
        action="setting.write",
        target="mister_engage_kpa",
        detail={"ref": {"table": "desired_state", "id": 7}, "value": 1.55},
        reason="dry afternoon",
        request_id=str(key),
        request_sha256=b"\x01" * 32,
        experiment_id=experiment,
        ts=datetime(2026, 11, 2, 18, tzinfo=UTC),
    )
    assert row.greenhouse_id == "vallery" and row.request_id == key and row.experiment_id == experiment
    assert row.model_dump(mode="json")["request_sha256"] == "01" * 32
    forecast = ActivityLogRow(source="system", actor="forecast-engine", action="plan.write", target="preemptive-1")
    assert forecast.detail == {} and forecast.request_id is None


@pytest.mark.parametrize(
    "action",
    ["setting.write", "setting.release", "plan.write", "alert.ack", "alert.resolve", "light.on", "light.off"]
    + ["irrigation.run", "irrigation.stop", "irrigation.schedule.update", "irrigation.clear_fault"],
)
def test_the_architecture_9_actions_are_accepted(action):
    assert ActivityLogRow(source="iris", actor="iris", action=action, target="x").action == action


@pytest.mark.parametrize(
    "change",
    [
        {"source": "admin"},
        {"source": "agent:"},
        {"source": "Agent:Steward"},
        {"source": "system-x"},
        {"actor": ""},
        {"actor": "x" * 65},
        {"action": "settings.write"},
        {"action": "irrigation"},
        {"action": "irrigation."},
        {"action": "light.dim"},
        {"target": ""},
        {"reason": "r" * 501},
        {"request_id": uuid.uuid4()},  # without its hash
        {"request_sha256": b"\x01" * 32},  # without its id
        {"request_id": uuid.uuid4(), "request_sha256": b"\x01" * 31},
        {"ts": datetime(2026, 11, 2, 18)},  # naive
    ],
)
def test_an_activity_row_rejects(change):
    with pytest.raises(ValidationError):
        ActivityLogRow(**{"source": "iris", "actor": "iris", "action": "plan.write", "target": "p-1", **change})


def test_a_desired_state_row_holds_a_value_or_a_release():
    write = DesiredStateRow(param="mister_engage_kpa", value=1.55, manual=True, source="dashboard", actor="jason")
    assert write.greenhouse_id == "vallery" and write.manual is True
    release = DesiredStateRow(param="mister_engage_kpa", value=None, source="agent:steward", actor="steward")
    assert release.value is None and release.manual is False
    assert DesiredStateRow(param="x", value=1, source="iris", actor="iris", reason="r" * 500).reason == "r" * 500


@pytest.mark.parametrize(
    "change",
    [
        {"value": None, "manual": True},  # migration 280: CHECK (value IS NOT NULL OR NOT manual)
        {"value": float("nan")},
        {"value": float("inf")},
        {"param": "Mister_engage_kpa"},
        {"param": "1mister"},
        {"param": ""},
        {"source": "admin"},
        {"actor": ""},
        {"reason": "r" * 501},  # migration 280: CHECK (length(reason) <= 500)
    ],
)
def test_a_desired_state_row_rejects(change):
    with pytest.raises(ValidationError):
        DesiredStateRow(**{"param": "mister_engage_kpa", "value": 1.55, "source": "iris", "actor": "iris", **change})
