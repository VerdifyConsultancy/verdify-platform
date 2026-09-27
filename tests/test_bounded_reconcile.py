"""Critical #433 staged writer boundary: approval, cap, confirmation, stop."""

from __future__ import annotations

import importlib.util
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ingestor"))

import shared  # noqa: E402
from tasks import bounded_reconcile as bounded  # noqa: E402

from verdify_schemas.policy_vector import wire_fields  # noqa: E402

spec = importlib.util.spec_from_file_location("prepare_writer_stage", ROOT / "scripts" / "prepare-writer-stage.py")
prepare_writer_stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare_writer_stage)


class ReadOnlyFixture:
    def __init__(self, parameters: list[str]):
        self.names = [field.name for field in wire_fields()]
        self.readbacks = {name: 0.0 for name in self.names}
        self.parameters = parameters
        self.ts = datetime.now(UTC)
        self.expiry = self.ts + timedelta(minutes=40)
        self.rows = {}

    async def fetch(self, sql, *_args):
        if "FROM setpoint_snapshot" in sql:
            return [{"parameter": name, "value": value, "ts": self.ts} for name, value in self.readbacks.items()]
        if "FROM setpoint_plan" in sql:
            return [
                {"parameter": param, "plan_id": "iris-test", "ts": self.ts, "expires_at": self.expiry}
                for param in self.parameters
            ]
        raise AssertionError(sql)

    async def fetchrow(self, sql, ts, parameter):
        assert "FROM setpoint_changes" in sql
        return self.rows.get((ts, parameter))

    def planned(self):
        return [{"parameter": param, "value": 1.0, "plan_id": "iris-test", "ts": self.ts} for param in self.parameters]


@pytest.fixture
def fixture(monkeypatch):
    names = [field.name for field in wire_fields()]
    assert len(names) == 48
    db = ReadOnlyFixture(names[:13])
    monkeypatch.setattr(shared, "transport_readbacks_ready", lambda generation: generation == 3)
    monkeypatch.setattr(shared, "current_cfg_readbacks", lambda generation: dict(db.readbacks))
    monkeypatch.setattr(shared, "writer_lease_strictly_held", lambda minimum_remaining_s=0: True)
    monkeypatch.setattr(shared, "esp32", {"client": object()})
    monkeypatch.setattr(bounded, "SESSION_ID", uuid.uuid4().hex)
    return db


def approve(preview: dict, state_dir: Path) -> None:
    bounded._write(
        state_dir / bounded.APPROVAL_NAME,
        {
            "version": 1,
            "run_id": uuid.uuid4().hex,
            "session_id": preview["session_id"],
            "generation": preview["generation"],
            "fingerprint": preview["fingerprint"],
            "expires_at": (datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
        },
    )


def records(selection):
    now = datetime.now(UTC)
    return [
        {"parameter": param, "value": value, "requested_at": now + timedelta(microseconds=index)}
        for index, (param, value) in enumerate(selection)
    ]


@pytest.mark.asyncio
async def test_approved_run_advances_only_after_confirmation_and_current_readback(fixture, tmp_path):
    db = fixture
    full = [(param, 1.0) for param in db.parameters]
    ordinary = await bounded.choose_stage(db, full, db.planned(), 3, tmp_path, 12)
    assert ordinary.action == "ordinary"
    preview = bounded._read(tmp_path / bounded.PREVIEW_NAME)
    assert len(preview["readbacks"]) == 48
    approve(preview, tmp_path)

    first = await bounded.choose_stage(db, full, db.planned(), 3, tmp_path, 12)
    assert first.action == "send" and len(first.changes) == 12
    assert bounded._read(tmp_path / bounded.STATE_NAME)["status"] == "inflight"
    first_records = records(first.changes)
    bounded.finish_stage(tmp_path, first, first_records, [])
    for record in first_records:
        db.rows[(record["requested_at"], record["parameter"])] = {"delivery_status": "sent", "confirmed_at": None}
    awaiting = await bounded.choose_stage(db, full, db.planned(), 3, tmp_path, 12)
    assert awaiting.action == "hold" and "awaiting" in awaiting.reason

    for record in first_records:
        db.readbacks[record["parameter"]] = record["value"]
        db.rows[(record["requested_at"], record["parameter"])] = {
            "delivery_status": "confirmed",
            "confirmed_at": datetime.now(UTC),
        }
    second = await bounded.choose_stage(db, full[12:], db.planned(), 3, tmp_path, 12)
    assert second.action == "send" and second.changes == (full[12],)
    second_records = records(second.changes)
    bounded.finish_stage(tmp_path, second, second_records, [])
    db.readbacks[second_records[0]["parameter"]] = 1.0
    db.rows[(second_records[0]["requested_at"], second_records[0]["parameter"])] = {
        "delivery_status": "confirmed",
        "confirmed_at": datetime.now(UTC),
    }
    done = await bounded.choose_stage(db, [], db.planned(), 3, tmp_path, 12)
    assert done.action == "complete"
    assert bounded._read(tmp_path / bounded.STATE_NAME)["status"] == "complete"


@pytest.mark.asyncio
async def test_crash_after_stage_claim_fails_closed(fixture, tmp_path):
    db = fixture
    full = [(param, 1.0) for param in db.parameters]
    await bounded.choose_stage(db, full, db.planned(), 3, tmp_path, 12)
    approve(bounded._read(tmp_path / bounded.PREVIEW_NAME), tmp_path)
    assert (await bounded.choose_stage(db, full, db.planned(), 3, tmp_path, 12)).action == "send"
    retry = await bounded.choose_stage(db, full, db.planned(), 3, tmp_path, 12)
    assert retry.action == "hold" and "outcome unknown" in retry.reason
    assert bounded._read(tmp_path / bounded.STATE_NAME)["status"] == "halted"


@pytest.mark.asyncio
async def test_source_change_or_failed_delivery_stops_next_stage(fixture, tmp_path):
    db = fixture
    full = [(param, 1.0) for param in db.parameters]
    await bounded.choose_stage(db, full, db.planned(), 3, tmp_path, 12)
    approve(bounded._read(tmp_path / bounded.PREVIEW_NAME), tmp_path)
    first = await bounded.choose_stage(db, full, db.planned(), 3, tmp_path, 12)
    bounded.finish_stage(tmp_path, first, records(first.changes), [("x", "failed")])
    assert (await bounded.choose_stage(db, full, db.planned(), 3, tmp_path, 12)).action == "hold"

    # A fresh approval in a new pod still cannot reuse the previous fingerprint.
    other = tmp_path / "other"
    await bounded.choose_stage(db, full, db.planned(), 3, other, 12)
    approve(bounded._read(other / bounded.PREVIEW_NAME), other)
    db.expiry += timedelta(minutes=1)
    assert (await bounded.choose_stage(db, full, db.planned(), 3, other, 12)).action == "hold"


@pytest.mark.asyncio
async def test_halted_stage_can_restore_only_its_baseline_through_same_writer(fixture, tmp_path):
    db = fixture
    full = [(param, 1.0) for param in db.parameters]
    await bounded.choose_stage(db, full, db.planned(), 3, tmp_path, 12)
    approve(bounded._read(tmp_path / bounded.PREVIEW_NAME), tmp_path)
    first = await bounded.choose_stage(db, full, db.planned(), 3, tmp_path, 12)
    bounded.finish_stage(tmp_path, first, records(first.changes), [(first.changes[0][0], "failed")])
    db.readbacks[first.changes[0][0]] = 1.0  # one command landed before the stage stopped
    await bounded.choose_stage(db, full[1:], db.planned(), 3, tmp_path, 12)
    preview = bounded._read(tmp_path / bounded.PREVIEW_NAME)
    state = bounded._read(tmp_path / bounded.STATE_NAME)
    recovery = prepare_writer_stage.prepare_rollback(preview, state, datetime.now(UTC))
    bounded._write(tmp_path / bounded.RECOVERY_NAME, recovery)
    rollback = await bounded.choose_stage(db, full[1:], db.planned(), 3, tmp_path, 12)
    assert rollback.action == "send" and rollback.rollback
    assert rollback.changes == ((first.changes[0][0], 0.0),)
    rollback_records = records(rollback.changes)
    bounded.finish_stage(tmp_path, rollback, rollback_records, [])
    db.readbacks[first.changes[0][0]] = 0.0
    db.rows[(rollback_records[0]["requested_at"], rollback_records[0]["parameter"])] = {
        "delivery_status": "confirmed",
        "confirmed_at": datetime.now(UTC),
    }
    stopped = await bounded.choose_stage(db, full, db.planned(), 3, tmp_path, 12)
    assert stopped.action == "hold" and "rollback confirmed" in stopped.reason
    assert bounded._read(tmp_path / bounded.STATE_NAME)["status"] == "rollback_complete"


@pytest.mark.asyncio
async def test_near_plan_expiry_and_lease_loss_block_first_stage(fixture, tmp_path, monkeypatch):
    db = fixture
    full = [(param, 1.0) for param in db.parameters]
    db.expiry = datetime.now(UTC) + timedelta(seconds=20)
    await bounded.choose_stage(db, full, db.planned(), 3, tmp_path, 12)
    approve(bounded._read(tmp_path / bounded.PREVIEW_NAME), tmp_path)
    blocked = await bounded.choose_stage(db, full, db.planned(), 3, tmp_path, 12)
    assert blocked.action == "hold" and "expiry" in blocked.reason

    other = tmp_path / "lease"
    db.expiry = datetime.now(UTC) + timedelta(minutes=40)
    await bounded.choose_stage(db, full, db.planned(), 3, other, 12)
    approve(bounded._read(other / bounded.PREVIEW_NAME), other)
    monkeypatch.setattr(shared, "writer_lease_strictly_held", lambda minimum_remaining_s=0: False)
    blocked = await bounded.choose_stage(db, full, db.planned(), 3, other, 12)
    assert blocked.action == "hold" and "lease" in blocked.reason
