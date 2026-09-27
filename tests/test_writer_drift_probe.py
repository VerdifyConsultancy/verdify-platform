"""The one-shot #433 probe must use the sole writer and stop on uncertainty."""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ingestor"))

import shared  # noqa: E402
from tasks import bounded_reconcile as bounded  # noqa: E402
from tasks import drift_probe  # noqa: E402

from verdify_schemas.policy_vector import wire_fields  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "prepare_writer_drift_probe", ROOT / "scripts" / "prepare-writer-drift-probe.py"
)
prepare_script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare_script)


class Fixture:
    def __init__(self):
        self.ts = datetime.now(UTC)
        self.expiry = self.ts + timedelta(minutes=40)
        self.plan_value = 600.0
        self.readbacks = {field.name: 0.0 for field in wire_fields()}
        self.readbacks[drift_probe.PARAMETER] = self.plan_value
        self.rows = {}

    def planned(self):
        return [{"parameter": drift_probe.PARAMETER, "value": self.plan_value, "plan_id": "iris-probe", "ts": self.ts}]

    async def fetch(self, sql, *_args):
        if "FROM setpoint_snapshot" in sql:
            return [{"parameter": name, "value": self.readbacks[name], "ts": self.ts} for name in self.readbacks]
        if "FROM setpoint_plan" in sql:
            return [
                {"parameter": drift_probe.PARAMETER, "plan_id": "iris-probe", "ts": self.ts, "expires_at": self.expiry}
            ]
        raise AssertionError(sql)

    async def fetchrow(self, sql, requested_at, parameter):
        assert "FROM setpoint_changes" in sql
        return self.rows.get((requested_at, parameter))


@pytest.fixture
def fixture(monkeypatch):
    db = Fixture()
    monkeypatch.setattr(shared, "transport_readbacks_ready", lambda generation: generation == 3)
    monkeypatch.setattr(shared, "current_cfg_readbacks", lambda generation: dict(db.readbacks))
    monkeypatch.setattr(shared, "writer_lease_strictly_held", lambda minimum_remaining_s=0: True)
    monkeypatch.setattr(shared, "esp32", {"client": object()})
    monkeypatch.setattr(bounded, "SESSION_ID", "drift-probe-test-session")
    return db


async def arm(db: Fixture, state_dir: Path):
    passive = await drift_probe.choose(db, [], db.planned(), db.plan_value, 3, False, state_dir)
    assert passive.action == "ordinary"
    preview = bounded._read(state_dir / drift_probe.PREVIEW_NAME)
    assert len(preview["readbacks"]) == 48
    approval = prepare_script.prepare(preview, datetime.now(UTC))
    bounded._write(state_dir / drift_probe.APPROVAL_NAME, approval)
    return approval


def record(decision):
    return {"parameter": drift_probe.PARAMETER, "value": decision.changes[0][1], "requested_at": datetime.now(UTC)}


@pytest.mark.asyncio
async def test_one_probe_then_exactly_one_confirmed_correction(fixture, tmp_path):
    db = fixture
    await arm(db, tmp_path)
    probe = await drift_probe.choose(db, [], db.planned(), 600.0, 3, False, tmp_path)
    assert probe.action == "send" and probe.phase == "probe"
    assert probe.changes == ((drift_probe.PARAMETER, 570.0),)
    assert bounded._read(tmp_path / drift_probe.STATE_NAME)["status"] == "probe_inflight"

    probe_record = record(probe)
    drift_probe.finish(tmp_path, probe, [probe_record], [])
    db.rows[(probe_record["requested_at"], drift_probe.PARAMETER)] = {"delivery_status": "sent", "confirmed_at": None}
    awaiting = await drift_probe.choose(db, [], db.planned(), 600.0, 3, False, tmp_path)
    assert awaiting.action == "hold"
    db.readbacks[drift_probe.PARAMETER] = 570.0
    db.rows[(probe_record["requested_at"], drift_probe.PARAMETER)] = {
        "delivery_status": "confirmed",
        "confirmed_at": datetime.now(UTC),
    }
    restore = await drift_probe.choose(db, [(drift_probe.PARAMETER, 600.0)], db.planned(), 600.0, 3, False, tmp_path)
    assert restore.action == "send" and restore.phase == "restore"
    assert restore.changes == ((drift_probe.PARAMETER, 600.0),)
    restore_record = record(restore)
    drift_probe.finish(tmp_path, restore, [restore_record], [])
    db.readbacks[drift_probe.PARAMETER] = 600.0
    db.rows[(restore_record["requested_at"], drift_probe.PARAMETER)] = {
        "delivery_status": "confirmed",
        "confirmed_at": datetime.now(UTC),
    }
    done = await drift_probe.choose(db, [], db.planned(), 600.0, 3, False, tmp_path)
    assert done.action == "ordinary"
    state = bounded._read(tmp_path / drift_probe.STATE_NAME)
    assert state["status"] == "complete"
    assert state["probe_record"]["requested_at"] == probe_record["requested_at"].isoformat()
    assert state["restore_record"]["requested_at"] == restore_record["requested_at"].isoformat()
    # A retained approval is one-shot: later ordinary desired work is untouched.
    assert (
        await drift_probe.choose(db, [("safety_max", 100.0)], db.planned(), 600.0, 3, False, tmp_path)
    ).action == "ordinary"
    (tmp_path / drift_probe.APPROVAL_NAME).write_text("malformed-retained-approval")
    assert (await drift_probe.choose(db, [], db.planned(), 600.0, 3, False, tmp_path)).action == "ordinary"


@pytest.mark.asyncio
async def test_lease_loss_or_other_candidate_blocks_before_any_probe_command(fixture, tmp_path, monkeypatch):
    db = fixture
    await arm(db, tmp_path)
    monkeypatch.setattr(shared, "writer_lease_strictly_held", lambda minimum_remaining_s=0: False)
    blocked = await drift_probe.choose(db, [], db.planned(), 600.0, 3, False, tmp_path)
    assert blocked.action == "hold" and "lease" in blocked.reason
    assert not (tmp_path / drift_probe.STATE_NAME).exists()

    monkeypatch.setattr(shared, "writer_lease_strictly_held", lambda minimum_remaining_s=0: True)
    blocked = await drift_probe.choose(db, [("safety_max", 100.0)], db.planned(), 600.0, 3, False, tmp_path)
    assert blocked.action == "hold" and "nonempty" in blocked.reason
    assert not (tmp_path / drift_probe.STATE_NAME).exists()


@pytest.mark.asyncio
async def test_generation_or_plan_change_halts_without_correction(fixture, tmp_path):
    db = fixture
    await arm(db, tmp_path)
    probe = await drift_probe.choose(db, [], db.planned(), 600.0, 3, False, tmp_path)
    drift_probe.finish(tmp_path, probe, [record(probe)], [])
    assert (await drift_probe.choose(db, [], db.planned(), 600.0, 4, True, tmp_path)).action == "hold"
    assert bounded._read(tmp_path / drift_probe.STATE_NAME)["status"] == "halted"

    other = tmp_path / "plan"
    await arm(db, other)
    probe = await drift_probe.choose(db, [], db.planned(), 600.0, 3, False, other)
    drift_probe.finish(other, probe, [record(probe)], [])
    db.plan_value = 570.0
    assert (await drift_probe.choose(db, [], db.planned(), 570.0, 3, False, other)).action == "hold"
    assert bounded._read(other / drift_probe.STATE_NAME)["status"] == "halted"


@pytest.mark.asyncio
async def test_uncertain_or_failed_dispatch_stops_and_does_not_retry(fixture, tmp_path):
    db = fixture
    await arm(db, tmp_path)
    probe = await drift_probe.choose(db, [], db.planned(), 600.0, 3, False, tmp_path)
    assert (await drift_probe.choose(db, [], db.planned(), 600.0, 3, False, tmp_path)).action == "hold"
    drift_probe.finish(tmp_path, probe, [], [(drift_probe.PARAMETER, "command_timeout_outcome_unknown")])
    assert bounded._read(tmp_path / drift_probe.STATE_NAME)["status"] == "halted"
    assert (await drift_probe.choose(db, [], db.planned(), 600.0, 3, False, tmp_path)).action == "hold"
    source = (ROOT / "ingestor" / "tasks" / "dispatcher.py").read_text()
    assert 'max_attempts = 1 if probe_decision.action == "send" else 3' in source


@pytest.mark.asyncio
async def test_lease_loss_after_probe_halts_before_correction(fixture, tmp_path, monkeypatch):
    db = fixture
    await arm(db, tmp_path)
    probe = await drift_probe.choose(db, [], db.planned(), 600.0, 3, False, tmp_path)
    drift_probe.finish(tmp_path, probe, [record(probe)], [])
    monkeypatch.setattr(shared, "writer_lease_strictly_held", lambda minimum_remaining_s=0: False)
    blocked = await drift_probe.choose(db, [(drift_probe.PARAMETER, 600.0)], db.planned(), 600.0, 3, False, tmp_path)
    assert blocked.action == "hold" and "lease" in blocked.reason
    assert bounded._read(tmp_path / drift_probe.STATE_NAME)["status"] == "halted"


@pytest.mark.asyncio
async def test_partial_cfg_batch_waits_without_replaying_probe(fixture, tmp_path, monkeypatch):
    db = fixture
    await arm(db, tmp_path)
    probe = await drift_probe.choose(db, [], db.planned(), 600.0, 3, False, tmp_path)
    drift_probe.finish(tmp_path, probe, [record(probe)], [])
    original_fetch = db.fetch

    async def partial_fetch(sql, *args):
        rows = await original_fetch(sql, *args)
        if "FROM setpoint_snapshot" in sql:
            rows[0] = {**rows[0], "ts": db.ts + timedelta(seconds=1)}
        return rows

    monkeypatch.setattr(db, "fetch", partial_fetch)
    pending = await drift_probe.choose(db, [], db.planned(), 600.0, 3, False, tmp_path)
    assert pending.action == "hold" and "atomic cfg batch" in pending.reason
    assert bounded._read(tmp_path / drift_probe.STATE_NAME)["status"] == "probe_awaiting"


def test_prepare_rejects_stale_preview(fixture, tmp_path):
    preview = {
        "version": 1,
        "parameter": drift_probe.PARAMETER,
        "desired": 600.0,
        "probe_value": 570.0,
        "readbacks": {str(n): 600.0 for n in range(48)},
        "captured_at": (datetime.now(UTC) - timedelta(minutes=7)).isoformat(),
        "earliest_plan_expiry": (datetime.now(UTC) + timedelta(minutes=40)).isoformat(),
    }
    with pytest.raises(ValueError, match="stale"):
        prepare_script.prepare(preview, datetime.now(UTC))
