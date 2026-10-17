"""Critical #433 staged writer boundary: approval, cap, confirmation, stop."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ingestor"))

import shared  # noqa: E402
from tasks import bounded_reconcile as bounded  # noqa: E402
from tasks import dispatcher  # noqa: E402

from verdify_schemas.policy_vector import wire_fields  # noqa: E402

spec = importlib.util.spec_from_file_location("prepare_writer_stage", ROOT / "scripts" / "prepare-writer-stage.py")
prepare_writer_stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare_writer_stage)


class ReadOnlyFixture:
    def __init__(self, parameters: list[str]):
        self.names = [field.name for field in wire_fields()]
        self.readbacks = {name: 0.0 for name in self.names}
        self.snapshot_readbacks = None
        self.parameters = parameters
        self.ts = datetime.now(UTC)
        self.expiry = self.ts + timedelta(minutes=40)
        self.rows = {}
        self.plan_values = {}
        self.stage_rows = []

    async def fetch(self, sql, *_args):
        if "FROM setpoint_snapshot" in sql:
            snapshot = self.snapshot_readbacks if self.snapshot_readbacks is not None else self.readbacks
            return [{"parameter": name, "value": snapshot[name], "ts": self.ts} for name in self.names]
        if "FROM setpoint_plan" in sql:
            return [
                {"parameter": param, "plan_id": "iris-test", "ts": self.ts, "expires_at": self.expiry}
                for param in self.parameters
            ]
        if "FROM setpoint_changes" in sql:
            return self.stage_rows
        raise AssertionError(sql)

    async def fetchrow(self, sql, ts, parameter):
        assert "FROM setpoint_changes" in sql
        return self.rows.get((ts, parameter))

    def planned(self):
        return [
            {"parameter": param, "value": self.plan_values.get(param, 1.0), "plan_id": "iris-test", "ts": self.ts}
            for param in self.parameters
        ]


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
async def test_broad_desired_change_stages_without_reconnect_and_archives_completed_run(fixture, tmp_path):
    db = fixture
    db.parameters = [
        name for name in db.names if name not in bounded.DYNAMIC_MOISTURE_GUARDRAIL_PARAMS | bounded.ZONE_VPD_TARGETS
    ][:15]
    assert len(db.parameters) == 15
    first_candidate = [(param, 1.0) for param in db.parameters]

    async def choose(changes):
        return await dispatcher._choose_bounded_stage_if_required(db, changes, db.planned(), 3, tmp_path, None, {})

    # Generation 3 is already reconciled; the broad desired candidate must
    # still produce an approval-bound preview and send nothing yet.
    ordinary = await choose(first_candidate)
    assert ordinary.action == "ordinary"
    preview = bounded._read(tmp_path / bounded.PREVIEW_NAME)
    assert len(preview["changes"]) == 15 and len(preview["readbacks"]) == 48
    approve(preview, tmp_path)
    approval = bounded._read(tmp_path / bounded.APPROVAL_NAME)

    first = await choose(first_candidate)
    assert first.action == "send" and len(first.changes) == 12, first
    first_records = records(first.changes)
    bounded.finish_stage(tmp_path, first, first_records, [])
    for record in first_records:
        db.readbacks[record["parameter"]] = record["value"]
        db.rows[(record["requested_at"], record["parameter"])] = {
            "delivery_status": "confirmed",
            "confirmed_at": datetime.now(UTC),
        }
    second = await choose(first_candidate[12:])
    assert second.action == "send" and second.changes == tuple(first_candidate[12:])
    second_records = records(second.changes)
    bounded.finish_stage(tmp_path, second, second_records, [])
    for record in second_records:
        db.readbacks[record["parameter"]] = record["value"]
        db.rows[(record["requested_at"], record["parameter"])] = {
            "delivery_status": "confirmed",
            "confirmed_at": datetime.now(UTC),
        }
    completed = await choose([])
    assert completed.action == "complete" and completed.changes == ()
    old_state = bounded._read(tmp_path / bounded.STATE_NAME)
    assert old_state["status"] == "complete"

    # A later 15-command plan in the same pod must require a second approval,
    # while retaining the first run's completion and approval receipts.
    db.ts = datetime.now(UTC)
    db.expiry = db.ts + timedelta(minutes=40)
    db.plan_values = {param: 2.0 for param in db.parameters}
    next_candidate = [(param, 2.0) for param in db.parameters]
    next_preview_only = await choose(next_candidate)
    assert next_preview_only.action == "ordinary"
    assert not (tmp_path / bounded.APPROVAL_NAME).exists()
    assert not (tmp_path / bounded.STATE_NAME).exists()
    archived = bounded._read(tmp_path / f"writer-stage-completed-{old_state['run_id']}.json")
    assert archived == {"schema": "verdify-writer-stage-completed-v1", "approval": approval, "state": old_state}
    next_preview = bounded._read(tmp_path / bounded.PREVIEW_NAME)
    assert next_preview["changes"] == [[param, 2.0] for param in db.parameters]
    approve(next_preview, tmp_path)
    next_first = await choose(next_candidate)
    assert next_first.action == "send" and len(next_first.changes) == 12
    assert bounded._read(tmp_path / f"writer-stage-completed-{old_state['run_id']}.json") == archived


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
async def test_atomic_snapshot_lag_waits_only_for_pending_confirmation_within_deadline(fixture, tmp_path):
    db = fixture
    full = [(param, 1.0) for param in db.parameters]
    await bounded.choose_stage(db, full, db.planned(), 3, tmp_path, 12)
    approve(bounded._read(tmp_path / bounded.PREVIEW_NAME), tmp_path)
    first = await bounded.choose_stage(db, full, db.planned(), 3, tmp_path, 12)
    assert first.action == "send" and len(first.changes) == 12
    inflight = tmp_path.parent / "snapshot-lag-inflight"
    shutil.copytree(tmp_path, inflight)
    first_records = records(first.changes)
    bounded.finish_stage(tmp_path, first, first_records, [])
    old_snapshot = dict(db.readbacks)
    for record in first_records:
        db.readbacks[record["parameter"]] = record["value"]
        db.rows[(record["requested_at"], record["parameter"])] = {
            "delivery_status": "confirmed",
            "confirmed_at": datetime.now(UTC),
        }
    db.snapshot_readbacks = old_snapshot  # one atomic 48-field batch behind live cfg callbacks

    lagged = await bounded.choose_stage(db, full[12:], db.planned(), 3, tmp_path, 12)
    assert lagged.action == "hold" and "awaiting fresh atomic snapshot" in lagged.reason
    pending = bounded._read(tmp_path / bounded.STATE_NAME)
    assert pending["status"] == "awaiting_confirmation"
    assert pending["completed"] == [] and len(pending["records"]) == 12

    other = await bounded.choose_stage(db, full[12:], db.planned(), 3, inflight, 12)
    assert other.action == "hold" and "snapshot does not match" in other.reason
    assert bounded._read(inflight / bounded.STATE_NAME)["status"] == "halted"

    missing = tmp_path.parent / "snapshot-lag-missing-readback"
    shutil.copytree(tmp_path, missing)
    removed = db.readbacks.pop(db.names[0])
    absent = await bounded.choose_stage(db, full[12:], db.planned(), 3, missing, 12)
    assert absent.action == "hold" and "missing readback" in absent.reason
    assert bounded._read(missing / bounded.STATE_NAME)["status"] == "halted"
    db.readbacks[db.names[0]] = removed

    changed_plan = tmp_path.parent / "snapshot-lag-plan-change"
    shutil.copytree(tmp_path, changed_plan)
    db.expiry += timedelta(minutes=1)
    changed = await bounded.choose_stage(db, full[12:], db.planned(), 3, changed_plan, 12)
    assert changed.action == "hold" and "effective plan" in changed.reason
    assert bounded._read(changed_plan / bounded.STATE_NAME)["status"] == "halted"
    db.expiry -= timedelta(minutes=1)

    deadline = tmp_path.parent / "snapshot-lag-deadline"
    shutil.copytree(tmp_path, deadline)
    expired = bounded._read(deadline / bounded.STATE_NAME)
    expired["stage_started_at"] = (datetime.now(UTC) - bounded.CONFIRM_DEADLINE - timedelta(seconds=1)).isoformat()
    bounded._write(deadline / bounded.STATE_NAME, expired)
    timed_out = await bounded.choose_stage(db, full[12:], db.planned(), 3, deadline, 12)
    assert timed_out.action == "hold" and "deadline exceeded" in timed_out.reason
    assert bounded._read(deadline / bounded.STATE_NAME)["status"] == "halted"

    db.snapshot_readbacks = dict(db.readbacks)
    second = await bounded.choose_stage(db, full[12:], db.planned(), 3, tmp_path, 12)
    assert second.action == "send" and second.changes == (full[12],)


@pytest.mark.asyncio
async def test_confirmed_quantized_seconds_do_not_block_eight_remaining_or_repushed(fixture, tmp_path):
    db = fixture
    db.parameters = db.names[:44]
    quantized = {
        "min_fog_on_s": (54.75, 54.0),
        "mister_engage_delay_s": (46.5, 47.0),
        "mister_pulse_gap_s": (48.75, 48.0),
    }
    desired = {param: quantized[param][0] if param in quantized else 1.0 for param in db.parameters}
    full = [(param, desired[param]) for param in db.parameters]
    await bounded.choose_stage(db, full, db.planned(), 3, tmp_path, 12)
    approve(bounded._read(tmp_path / bounded.PREVIEW_NAME), tmp_path)
    for stage in range(3):
        selected = await bounded.choose_stage(db, full[stage * 12 :], db.planned(), 3, tmp_path, 12)
        assert selected.action == "send" and len(selected.changes) == 12
        stage_records = records(selected.changes)
        bounded.finish_stage(tmp_path, selected, stage_records, [])
        for record in stage_records:
            db.readbacks[record["parameter"]] = quantized.get(record["parameter"], (0, record["value"]))[1]
            db.rows[(record["requested_at"], record["parameter"])] = {
                "delivery_status": "confirmed",
                "confirmed_at": datetime.now(UTC),
            }
    reappeared = [(param, desired[param]) for param in quantized]
    candidate = full[36:] + reappeared
    assert len(candidate) == 11
    drift = tmp_path.parent / "quantized-true-drift"
    shutil.copytree(tmp_path, drift)
    bad = [(param, value + 1.0 if param == "min_fog_on_s" else value) for param, value in candidate]
    rejected = await bounded.choose_stage(db, bad, db.planned(), 3, drift, 12)
    assert rejected.action == "hold" and "completed fixed candidate drifted" in rejected.reason
    assert bounded._read(drift / bounded.STATE_NAME)["status"] == "halted"

    wrong_record = tmp_path.parent / "quantized-wrong-confirmed-value"
    shutil.copytree(tmp_path, wrong_record)
    corrupted = bounded._read(wrong_record / bounded.STATE_NAME)
    corrupted["completed_values"]["min_fog_on_s"] = 60.0
    bounded._write(wrong_record / bounded.STATE_NAME, corrupted)
    rejected = await bounded.choose_stage(db, candidate, db.planned(), 3, wrong_record, 12)
    assert rejected.action == "hold"
    assert bounded._read(wrong_record / bounded.STATE_NAME)["status"] == "halted"

    final_stage = await bounded.choose_stage(db, candidate, db.planned(), 3, tmp_path, 12)
    assert final_stage.action == "send" and final_stage.changes == tuple(full[36:])
    final_records = records(final_stage.changes)
    bounded.finish_stage(tmp_path, final_stage, final_records, [])
    for record in final_records:
        db.readbacks[record["parameter"]] = record["value"]
        db.rows[(record["requested_at"], record["parameter"])] = {
            "delivery_status": "confirmed",
            "confirmed_at": datetime.now(UTC),
        }
    complete = await bounded.choose_stage(db, reappeared, db.planned(), 3, tmp_path, 12)
    assert complete.action == "complete" and complete.changes == ()
    again = await bounded.choose_stage(db, reappeared, db.planned(), 3, tmp_path, 12)
    assert again.action == "complete" and again.changes == ()


@pytest.mark.asyncio
async def test_completed_live_moisture_cap_can_lift_and_reengage_under_same_plan(fixture, tmp_path):
    db = fixture
    fog = "fog_escalation_kpa"
    zones = ["vpd_target_south", "vpd_target_west"]
    db.parameters = [param for param in db.names[:13] if param != fog][:11] + [fog] + zones
    db.readbacks.update({param: 0.0 for param in zones})
    zone_targets = {param: 1.0 for param in zones}
    db.plan_values[fog] = 0.35
    initial = [(param, 0.3 if param == fog else 1.0) for param in db.parameters]
    await bounded.choose_stage(db, initial, db.planned(), 3, tmp_path, 12, zone_targets, {fog: 0.3})
    approve(bounded._read(tmp_path / bounded.PREVIEW_NAME), tmp_path)
    first = await bounded.choose_stage(db, initial, db.planned(), 3, tmp_path, 12, zone_targets, {fog: 0.3})
    assert first.action == "send" and len(first.changes) == 12 and first.changes[-1] == (fog, 0.3)
    first_records = records(first.changes)
    bounded.finish_stage(tmp_path, first, first_records, [])
    for record in first_records:
        db.readbacks[record["parameter"]] = record["value"]
        db.rows[(record["requested_at"], record["parameter"])] = {
            "delivery_status": "confirmed",
            "confirmed_at": datetime.now(UTC),
        }

    source_mismatch = tmp_path.parent / "guardrail-source-mismatch"
    shutil.copytree(tmp_path, source_mismatch)
    changed = [(param, 1.0) for param in zones] + [(fog, 0.4)]
    held = await bounded.choose_stage(db, changed, db.planned(), 3, source_mismatch, 12, zone_targets, {fog: 0.35})
    assert held.action == "hold" and "outside source" in held.reason

    fixed_mismatch = tmp_path.parent / "ordinary-fixed-mismatch"
    shutil.copytree(tmp_path, fixed_mismatch)
    held = await bounded.choose_stage(
        db,
        changed[:-1] + [(db.parameters[0], 2.0), (fog, 0.35)],
        db.planned(),
        3,
        fixed_mismatch,
        12,
        zone_targets,
        {fog: 0.35},
    )
    assert held.action == "hold" and "completed fixed candidate drifted" in held.reason

    lifted = [(param, 1.0) for param in zones] + [(fog, 0.35)]
    second = await bounded.choose_stage(db, lifted, db.planned(), 3, tmp_path, 12, zone_targets, {fog: 0.35})
    assert second.action == "send" and second.changes == ((fog, 0.35), *[(param, 1.0) for param in zones])
    second_records = records(second.changes)
    bounded.finish_stage(tmp_path, second, second_records, [])
    for record in second_records:
        db.readbacks[record["parameter"]] = record["value"]
        db.rows[(record["requested_at"], record["parameter"])] = {
            "delivery_status": "confirmed",
            "confirmed_at": datetime.now(UTC),
        }

    capped_again = await bounded.choose_stage(db, [(fog, 0.3)], db.planned(), 3, tmp_path, 12, zone_targets, {fog: 0.3})
    assert capped_again.action == "send" and capped_again.changes == ((fog, 0.3),)
    third_record = records(capped_again.changes)
    bounded.finish_stage(tmp_path, capped_again, third_record, [])
    db.readbacks[fog] = 0.3
    db.rows[(third_record[0]["requested_at"], fog)] = {
        "delivery_status": "confirmed",
        "confirmed_at": datetime.now(UTC),
    }
    done = await bounded.choose_stage(db, [], db.planned(), 3, tmp_path, 12, zone_targets, {fog: 0.3})
    assert done.action == "complete" and done.changes == ()


def test_fresh_reconnect_skips_only_equivalent_quantized_durations():
    changes = [
        ("min_fog_on_s", 54.75),
        ("mister_engage_delay_s", 46.5),
        ("mister_pulse_gap_s", 48.75),
        ("min_fog_off_s", 99.0),
    ]
    readbacks = {
        "min_fog_on_s": 54.0,
        "mister_engage_delay_s": 47.0,
        "mister_pulse_gap_s": 48.0,
        "min_fog_off_s": 80.0,
    }
    assert dispatcher._without_equivalent_duration_candidates(changes, readbacks) == [("min_fog_off_s", 99.0)]


def test_dynamic_stage_fields_cover_only_the_live_moisture_guardrail():
    live = dispatcher._vpd_high_moisture_guardrails(
        {"vpd_low": 0.26, "vpd_high": 0.81},
        {"temp_avg": 66.3, "dew_point": 53.5, "vpd_avg": 0.95},
    )
    assert set(live) == bounded.DYNAMIC_MOISTURE_GUARDRAIL_PARAMS


def test_equivalent_seconds_are_filtered_again_on_later_cfg_drift_pass():
    quantized = [
        ("min_fog_on_s", 54.75),
        ("mister_engage_delay_s", 46.5),
        ("mister_pulse_gap_s", 48.75),
    ]
    readbacks = {
        "min_fog_on_s": 54.0,
        "mister_engage_delay_s": 47.0,
        "mister_pulse_gap_s": 48.0,
        "min_fog_off_s": 80.0,
    }
    residual = [("min_fog_off_s", 99.0)]
    first = dispatcher._without_equivalent_duration_replays(
        quantized + residual,
        readbacks,
        reconnect_pending=True,
        drift_pending=False,
        staged_state_exists=False,
    )
    assert first == residual
    second = dispatcher._without_equivalent_duration_replays(
        quantized,
        readbacks,
        reconnect_pending=False,
        drift_pending=True,
        staged_state_exists=False,
    )
    assert second == []  # no redundant physical commands after cfg callback

    changed = dict(readbacks, min_fog_on_s=52.0)
    assert dispatcher._without_equivalent_duration_replays(
        quantized,
        changed,
        reconnect_pending=False,
        drift_pending=True,
        staged_state_exists=False,
    ) == [("min_fog_on_s", 54.75)]
    missing = dict(readbacks)
    missing.pop("min_fog_on_s")  # stale generations are absent from current_cfg_readbacks
    assert ("min_fog_on_s", 54.75) in dispatcher._without_equivalent_duration_replays(
        quantized,
        missing,
        reconnect_pending=False,
        drift_pending=True,
        staged_state_exists=False,
    )
    assert (
        dispatcher._without_equivalent_duration_replays(
            quantized,
            readbacks,
            reconnect_pending=False,
            drift_pending=True,
            staged_state_exists=True,
        )
        == []
    )
    assert (
        dispatcher._without_equivalent_duration_replays(
            quantized,
            readbacks,
            reconnect_pending=False,
            drift_pending=False,
            staged_state_exists=False,
        )
        == []
    )


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
@pytest.mark.parametrize("rollback", [False, True])
async def test_partial_dispatch_retains_request_identity_without_confirmation_or_replay(fixture, tmp_path, rollback):
    db = fixture
    full = [(param, 1.0) for param in db.parameters]
    await bounded.choose_stage(db, full, db.planned(), 3, tmp_path, 12)
    approve(bounded._read(tmp_path / bounded.PREVIEW_NAME), tmp_path)
    first = await bounded.choose_stage(db, full, db.planned(), 3, tmp_path, 12)
    if rollback:
        state = bounded._read(tmp_path / bounded.STATE_NAME)
        state["status"] = "rollback_inflight"
        bounded._write(tmp_path / bounded.STATE_NAME, state)
        first = bounded.Decision("send", first.changes, run_id=first.run_id, rollback=True)
    sent = records(first.changes[:8])
    failed = [(param, "transport_disconnected") for param, _value in first.changes[8:]]
    bounded.finish_stage(tmp_path, first, sent, failed)
    state = bounded._read(tmp_path / bounded.STATE_NAME)
    assert state["status"] == ("rollback_failed" if rollback else "halted")
    key = "rollback_records" if rollback else "records"
    assert state[key] == [
        {"parameter": r["parameter"], "value": r["value"], "requested_at": r["requested_at"].isoformat()} for r in sent
    ]
    assert state["completed"] == []
    assert db.rows == {}  # Retained request keys do not mint database confirmations.
    retry = await bounded.choose_stage(db, full, db.planned(), 3, tmp_path, 12)
    assert retry.action == "hold" and not retry.changes
    assert bounded._read(tmp_path / bounded.STATE_NAME)[key] == state[key]


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


@pytest.mark.asyncio
async def test_noncanonical_baseline_and_only_crop_vpd_target_can_move(fixture, tmp_path):
    db = fixture
    fixed = db.names[:10] + ["safety_max", "direct_wet_min_temp_f"]
    db.parameters = fixed + ["vpd_target_south"]
    for param in db.parameters[10:]:
        assert param not in db.names
        db.readbacks[param] = 0.0
    db.readbacks["outdoor_dewpoint_f"] = 39.102
    full = [(param, 1.0) for param in fixed] + [("vpd_target_south", 1.0)]
    await bounded.choose_stage(db, full, db.planned(), 3, tmp_path, 12, {"vpd_target_south": 1.0})
    preview = bounded._read(tmp_path / bounded.PREVIEW_NAME)
    assert len(preview["readbacks"]) == 51
    assert len(preview["diagnostic_readbacks"]) == 52
    approval = prepare_writer_stage.prepare(preview, datetime.now(UTC))
    bounded._write(tmp_path / bounded.APPROVAL_NAME, approval)

    db.readbacks["outdoor_dewpoint_f"] = 38.5665
    current = full[:-1] + [("vpd_target_south", 1.01)]
    first = await bounded.choose_stage(db, current, db.planned(), 3, tmp_path, 12, {"vpd_target_south": 1.01})
    assert first.action == "send" and first.changes == tuple(full[:-1])
    assert bounded._read(tmp_path / bounded.STATE_NAME)["approved_preview"]["fingerprint"] == preview["fingerprint"]
    first_records = records(first.changes)
    bounded.finish_stage(tmp_path, first, first_records, [])
    for record in first_records:
        db.readbacks[record["parameter"]] = record["value"]
        db.rows[(record["requested_at"], record["parameter"])] = {
            "delivery_status": "confirmed",
            "confirmed_at": datetime.now(UTC),
        }
    second = await bounded.choose_stage(
        db, [("vpd_target_south", 1.02)], db.planned(), 3, tmp_path, 12, {"vpd_target_south": 1.02}
    )
    assert second.action == "send" and second.changes == (("vpd_target_south", 1.02),)
    second_records = records(second.changes)
    bounded.finish_stage(tmp_path, second, second_records, [])
    db.readbacks["vpd_target_south"] = 1.02
    db.rows[(second_records[0]["requested_at"], "vpd_target_south")] = {
        "delivery_status": "confirmed",
        "confirmed_at": datetime.now(UTC),
    }
    reappeared = tmp_path.parent / "reappeared"
    omitted_drift = tmp_path.parent / "omitted-drift"
    shutil.copytree(tmp_path, reappeared)
    shutil.copytree(tmp_path, omitted_drift)
    handoff = await bounded.choose_stage(
        db, [("vpd_target_south", 1.03)], db.planned(), 3, reappeared, 12, {"vpd_target_south": 1.03}
    )
    assert handoff.action == "complete"
    assert bounded._read(reappeared / bounded.PREVIEW_NAME)["changes"] == [["vpd_target_south", 1.03]]
    over_limit = tmp_path.parent / "over-limit"
    shutil.copytree(tmp_path, over_limit)
    held = await bounded.choose_stage(
        db, [("vpd_target_south", 1.03)], db.planned(), 3, over_limit, 0, {"vpd_target_south": 1.03}
    )
    assert held.action == "hold" and "ordinary handoff" in held.reason
    held = await bounded.choose_stage(db, [], db.planned(), 3, omitted_drift, 12, {"vpd_target_south": 1.5})
    assert held.action == "hold" and "omitted zone VPD target" in held.reason
    done = await bounded.choose_stage(db, [], db.planned(), 3, tmp_path, 12, {"vpd_target_south": 1.02})
    assert done.action == "complete"


@pytest.mark.asyncio
async def test_unapproved_fixed_drift_and_unverified_vpd_source_halt(fixture, tmp_path):
    db = fixture
    db.parameters = ["safety_max", "vpd_target_south"] + db.names[:11]
    db.readbacks.update({"safety_max": 0.0, "vpd_target_south": 0.0})
    full = [(param, 1.0) for param in db.parameters]
    await bounded.choose_stage(db, full, db.planned(), 3, tmp_path, 12, {"vpd_target_south": 1.0})
    bounded._write(
        tmp_path / bounded.APPROVAL_NAME,
        prepare_writer_stage.prepare(bounded._read(tmp_path / bounded.PREVIEW_NAME), datetime.now(UTC)),
    )
    changed = [(param, 2.0 if param == "safety_max" else val) for param, val in full]
    held = await bounded.choose_stage(db, changed, db.planned(), 3, tmp_path, 12, {"vpd_target_south": 1.0})
    assert held.action == "hold" and "fixed candidate" in held.reason
    assert not (tmp_path / bounded.STATE_NAME).exists()
    held = await bounded.choose_stage(
        db,
        full[:-12] + [("vpd_target_south", 1.01)] + full[-11:],
        db.planned(),
        3,
        tmp_path,
        12,
        {"vpd_target_south": 1.02},
    )
    assert held.action == "hold" and "zone VPD source" in held.reason
    assert not (tmp_path / bounded.STATE_NAME).exists()


@pytest.mark.asyncio
async def test_entire_durable_heap_deferral_resumes_under_same_approval_without_replaying_rows(fixture, tmp_path):
    db = fixture
    full = [(p, 1.0) for p in db.parameters]
    await bounded.choose_stage(db, full, db.planned(), 3, tmp_path, 12)
    approve(bounded._read(tmp_path / bounded.PREVIEW_NAME), tmp_path)
    first = await bounded.choose_stage(db, full, db.planned(), 3, tmp_path, 12)
    old = bounded._read(tmp_path / bounded.STATE_NAME)
    deferred = [{**r, "delivery_status": "deferred_heap_pressure"} for r in records(first.changes)]
    bounded.finish_stage(tmp_path, first, [], [], deferred_records=deferred, api_dispatch_attempts=0)
    ready = bounded._read(tmp_path / bounded.STATE_NAME)
    assert ready["status"] == "ready" and ready["completed"] == []
    assert ready["expires_at"] == old["expires_at"] and ready["approved_preview"] == old["approved_preview"]
    assert ready["deferred_stages"][0]["records"][0]["requested_at"] == deferred[0]["requested_at"].isoformat()
    resumed = await bounded.choose_stage(db, full, db.planned(), 3, tmp_path, 12)
    assert resumed.action == "send" and resumed.run_id == first.run_id and resumed.changes == first.changes
    assert bounded._read(tmp_path / bounded.STATE_NAME)["deferred_stages"] == ready["deferred_stages"]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["empty", "partial", "api_attempt", "failure", "mixed", "bad_status"])
async def test_zero_records_only_truthful_entire_deferral_is_resumable(fixture, tmp_path, case):
    db = fixture
    full = [(p, 1.0) for p in db.parameters]
    await bounded.choose_stage(db, full, db.planned(), 3, tmp_path, 12)
    approve(bounded._read(tmp_path / bounded.PREVIEW_NAME), tmp_path)
    first = await bounded.choose_stage(db, full, db.planned(), 3, tmp_path, 12)
    deferred = [{**r, "delivery_status": "deferred_heap_pressure"} for r in records(first.changes)]
    if case == "empty":
        deferred = []
    if case == "partial":
        deferred.pop()
    if case == "bad_status":
        deferred[0]["delivery_status"] = "requested"
    bounded.finish_stage(
        tmp_path,
        first,
        records(first.changes[:1]) if case == "mixed" else [],
        [("x", "failure")] if case == "failure" else [],
        deferred_records=deferred,
        api_dispatch_attempts=1 if case == "api_attempt" else 0,
    )
    assert bounded._read(tmp_path / bounded.STATE_NAME)["status"] == "halted"


@pytest.mark.asyncio
async def test_handoff_retains_each_native_request_and_checks_latest_readback(fixture, tmp_path, monkeypatch):
    _held, done = await completed_linked_run(fixture, tmp_path, monkeypatch)
    state = bounded._read(tmp_path / bounded.STATE_NAME)
    parameter = state["completed"][0]
    assert not dispatcher.readback_values_equivalent(parameter, state["completed_values"][parameter], 3.0)
    requests = []
    for value in (2.0, 3.0):
        handoff = bounded.Decision("complete", ((parameter, value),), run_id=done.run_id)
        bounded.note_ordinary_handoff(handoff, 3)
        record = {**records(handoff.changes)[0], "source": "planner"}
        requests.append(record)
        bounded.record_ordinary_handoff(handoff, 3, [record], [])
        fixture.rows[(record["requested_at"], parameter)] = {
            "value": value,
            "source": "planner",
            "delivery_status": "confirmed",
            "confirmed_at": datetime.now(UTC),
            "expired_at": None,
        }
    fixture.readbacks[parameter] = 3.0
    first = fixture.rows[(requests[0]["requested_at"], parameter)]
    first["delivery_status"] = "sent"
    assert await bounded.completion_observation(fixture, tmp_path, done, 3) is None
    first["delivery_status"] = "confirmed"
    complete = await bounded.completion_observation(fixture, tmp_path, done, 3)
    assert complete["handoff_requested_count"] == complete["handoff_confirmed_count"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "defect", [None, "unknown", "baseline", "incomplete", "changed_plan", "rollover", "bad_approval"]
)
async def test_settled_recovery_preserves_history_and_requires_fresh_guarded_residual_approval(
    fixture, tmp_path, defect, monkeypatch
):
    db = fixture
    full = [(p, 1.0) for p in db.parameters]
    await bounded.choose_stage(db, full, db.planned(), 3, tmp_path, 12)
    approve(bounded._read(tmp_path / bounded.PREVIEW_NAME), tmp_path)
    first = await bounded.choose_stage(db, full, db.planned(), 3, tmp_path, 12)
    bounded.finish_stage(tmp_path, first, [], [])  # Historical halt; no fabricated successful send.
    await bounded.choose_stage(db, full, db.planned(), 3, tmp_path, 12)
    state = bounded._read(tmp_path / bounded.STATE_NAME)
    preview = bounded._read(tmp_path / bounded.PREVIEW_NAME)
    recovery = prepare_writer_stage.prepare_rollback(preview, state, datetime.now(UTC))
    bounded._write(tmp_path / bounded.RECOVERY_NAME, recovery)
    settled = await bounded.choose_stage(db, full, db.planned(), 3, tmp_path, 12)
    assert settled.action == "hold"
    state = bounded._read(tmp_path / bounded.STATE_NAME)
    assert state["status"] == "rollback_complete" and state["completed"] == []
    db.stage_rows = [
        {
            "ts": datetime.now(UTC),
            "parameter": p,
            "value": v,
            "delivery_status": "deferred_heap_pressure",
            "confirmed_at": None,
        }
        for p, v in first.changes
    ]
    generation = 3
    if defect == "rollover":
        generation = 4
        monkeypatch.setattr(bounded, "SESSION_ID", uuid.uuid4().hex)
        monkeypatch.setattr(shared, "transport_readbacks_ready", lambda g: g == generation)
        monkeypatch.setenv("HOSTNAME", "replacement-writer")
        monkeypatch.setenv("VERDIFY_GIT_SHA", "replacement-source")
    held = await bounded.choose_stage(db, full, db.planned(), generation, tmp_path, 12)
    assert held.action == "hold" and "fresh approval" in held.reason
    archive_path = tmp_path / f"writer-stage-recovered-{state['run_id']}.json"
    archive = bounded._read(archive_path)
    assert archive["state"] == state and archive["recovery"] == recovery
    residual = full[:6]
    await bounded.choose_stage(db, residual, db.planned(), generation, tmp_path, 12)
    fresh = prepare_writer_stage.prepare(bounded._read(tmp_path / bounded.PREVIEW_NAME), datetime.now(UTC))
    if defect == "bad_approval":
        fresh["fingerprint"] = "invalid"
    bounded._write(tmp_path / bounded.APPROVAL_NAME, fresh)
    if defect == "unknown":
        db.stage_rows[0]["delivery_status"] = "sent"
    if defect == "baseline":
        db.readbacks[first.changes[0][0]] = 1.0
    if defect == "incomplete":
        db.stage_rows.pop()
    if defect == "changed_plan":
        db.expiry += timedelta(minutes=1)
    new = await bounded.choose_stage(db, residual, db.planned(), generation, tmp_path, 12)
    assert bounded._read(archive_path) == archive
    if defect not in (None, "rollover"):
        assert new.action == "hold"
        assert bounded._read(tmp_path / bounded.STATE_NAME) == state
    else:
        assert new.action == "send" and new.run_id == fresh["run_id"] and new.changes == tuple(residual)
        assert bounded._read(tmp_path / bounded.STATE_NAME)["completed"] == []
        assert not (tmp_path / bounded.RECOVERY_NAME).exists()
        if defect == "rollover":
            assert fresh["generation"] == 4 and fresh["session_id"] == bounded.SESSION_ID
            assert archive["approval"]["session_id"] != fresh["session_id"]
            assert fresh["approved_preview"]["pod"] == "replacement-writer"
            assert fresh["approved_preview"]["source_revision"] == "replacement-source"


@pytest.mark.asyncio
async def test_deferred_stage_revalidates_original_expiry_before_any_retry(fixture, tmp_path):
    db = fixture
    full = [(p, 1.0) for p in db.parameters]
    await bounded.choose_stage(db, full, db.planned(), 3, tmp_path, 12)
    approve(bounded._read(tmp_path / bounded.PREVIEW_NAME), tmp_path)
    first = await bounded.choose_stage(db, full, db.planned(), 3, tmp_path, 12)
    deferred = [{**r, "delivery_status": "deferred_heap_pressure"} for r in records(first.changes)]
    bounded.finish_stage(tmp_path, first, [], [], deferred_records=deferred, api_dispatch_attempts=0)
    state = bounded._read(tmp_path / bounded.STATE_NAME)
    state["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    bounded._write(tmp_path / bounded.STATE_NAME, state)
    stopped = await bounded.choose_stage(db, full, db.planned(), 3, tmp_path, 12)
    assert stopped.action == "hold" and "expired" in stopped.reason
    assert bounded._read(tmp_path / bounded.STATE_NAME)["completed"] == []


def test_unapproved_equivalent_duration_requires_unchanged_original_plan_and_cfg():
    import copy

    approved = {
        "changes": [["temp_low", 65.0]],
        "readbacks": {"temp_low": 64.0, "min_fog_on_s": 60.0},
        "plan_rows": [{"parameter": "min_fog_on_s", "value": 59.25}],
    }
    state = {"approved_preview": approved, "completed": [], "completed_values": {}}
    preview = copy.deepcopy(approved)
    preview["changes"].append(["min_fog_on_s", 59.25])
    assert bounded._validated_residual(preview, state, None, 12) == [("temp_low", 65.0)]
    for change in ("value", "readback", "plan", "missing"):
        drift = copy.deepcopy(preview)
        if change == "value":
            drift["changes"][-1][1] = 58.0
        elif change == "readback":
            drift["readbacks"]["min_fog_on_s"] = 59.5
        elif change == "plan":
            drift["plan_rows"][0]["value"] = 59.5
        else:
            del drift["readbacks"]["min_fog_on_s"]
        with pytest.raises((ValueError, TypeError)):
            bounded._validated_residual(drift, state, None, 12)
    # An approved duration is never removed from exact desired binding.
    approved["changes"].append(["min_fog_on_s", 59.25])
    preview["changes"][-1][1] = 59.0
    with pytest.raises(ValueError):
        bounded._validated_residual(preview, state, None, 12)


async def confirmed_halt_fixture(db, tmp_path):
    import json

    db.parameters = [
        p for p in db.names if p not in bounded.DYNAMIC_MOISTURE_GUARDRAIL_PARAMS | bounded.ZONE_VPD_TARGETS
    ][:13]
    changes = [(p, 1.0) for p in db.parameters]
    preview = await bounded._preview(db, changes, db.planned(), 3)
    approval = prepare_writer_stage.prepare(preview, datetime.now(UTC))
    started = datetime.now(UTC) - timedelta(seconds=3)
    selected = db.parameters[:12]
    state = {
        "version": 1,
        "run_id": approval["run_id"],
        "status": "halted",
        "approved_preview": preview,
        "expires_at": approval["expires_at"],
        "stage_parameters": selected,
        "stage_started_at": started.isoformat(),
        "completed": selected,
        "completed_values": {p: 1.0 for p in selected},
        "records": [],
        "halt_reason": "desired fixed candidate changed or completed field reappeared",
    }
    for p in selected:
        db.readbacks[p] = 1.0
    requests = [
        {
            "ts": started + timedelta(microseconds=i),
            "parameter": p,
            "value": 1.0,
            "source": "planner",
            "delivery_status": "confirmed",
            "confirmed_at": started + timedelta(seconds=1),
            "expired_at": None,
        }
        for i, p in enumerate(selected)
    ]
    db.stage_rows = requests
    native_fetch = db.fetch

    async def fetch(sql, *args):
        if "AND ts > $2" in sql:
            return []
        return await native_fetch(sql, *args)

    db.fetch = fetch
    for name, value in ((bounded.STATE_NAME, state), (bounded.APPROVAL_NAME, approval)):
        bounded._write(tmp_path / name, value)
    custody = {name: (tmp_path / name).read_text() for name in (bounded.STATE_NAME, bounded.APPROVAL_NAME)}
    fresh = await bounded._preview(db, changes[12:], db.planned(), 3)
    serialized = json.loads(json.dumps(requests, default=lambda value: value.isoformat()))
    writer_custody = {
        "pod": {
            "kind": "Pod",
            "metadata": {"uid": str(uuid.uuid4()), "namespace": "verdify-prod", "name": preview["pod"]},
            "spec": {},
            "status": {},
        },
        "current": {"pod": preview["pod"], "source": preview["source_revision"], "preview": preview},
    }
    forward = prepare_writer_stage.prepare_forward(fresh, custody, serialized, writer_custody, datetime.now(UTC))
    bounded._write(tmp_path / bounded.RECOVERY_NAME, forward)
    return state, approval, forward, changes[12:]


async def completed_linked_run(db, tmp_path, monkeypatch):
    """Exercise native archival, distinct admission, and confirmed completion."""
    monkeypatch.setenv("VERDIFY_GIT_SHA", "a" * 40)
    monkeypatch.setenv("HOSTNAME", "historical-writer")
    old, old_approval, _forward, remaining = await confirmed_halt_fixture(db, tmp_path)
    old_bytes = (tmp_path / bounded.STATE_NAME).read_bytes()
    result = await bounded.choose_stage(db, remaining, db.planned(), 3, tmp_path, 12)
    assert result.action == "hold" and "fresh approval required" in result.reason
    archive = tmp_path / f"writer-stage-confirmed-halt-{old['run_id']}.json"
    archive_bytes = archive.read_bytes()
    assert (tmp_path / bounded.STATE_NAME).read_bytes() == old_bytes
    assert bounded._read(tmp_path / bounded.APPROVAL_NAME) == old_approval

    # The deployed process observes retained history without inheriting its
    # expired authority. A distinct current approval is still indispensable.
    monkeypatch.setenv("VERDIFY_GIT_SHA", "b" * 40)
    monkeypatch.setenv("HOSTNAME", "writer-pod")
    monkeypatch.setattr(bounded, "SESSION_ID", uuid.uuid4().hex)
    monkeypatch.setattr(shared, "transport_generation", 3)
    monkeypatch.setattr(shared, "reconciled_transport_generation", 3)
    monkeypatch.setattr(shared, "cfg_drift_versions", {})
    held = await bounded.choose_stage(db, remaining, db.planned(), 3, tmp_path, 12)
    assert held.action == "hold"
    observation = bounded.hold_observation(tmp_path, held, 3)
    assert observation["run_id"] == old["run_id"]
    assert observation["archive_sha256"] == bounded.hashlib.sha256(archive_bytes).hexdigest()
    assert await bounded.completion_observation(db, tmp_path, held, 3) is None
    preview = bounded._read(tmp_path / bounded.PREVIEW_NAME)
    approval = prepare_writer_stage.prepare(preview, datetime.now(UTC))
    bounded._write(tmp_path / bounded.APPROVAL_NAME, approval)
    selected = await bounded.choose_stage(db, remaining, db.planned(), 3, tmp_path, 12)
    assert selected.action == "send" and selected.run_id != old["run_id"]
    assert await bounded.completion_observation(db, tmp_path, selected, 3) is None
    stage_records = records(selected.changes)
    bounded.finish_stage(tmp_path, selected, stage_records, [])
    for record in stage_records:
        db.rows[(record["requested_at"], record["parameter"])] = {"delivery_status": "sent", "confirmed_at": None}
    pending = await bounded.choose_stage(db, remaining, db.planned(), 3, tmp_path, 12)
    assert pending.action == "hold"
    assert await bounded.completion_observation(db, tmp_path, pending, 3) is None
    for record in stage_records:
        db.readbacks[record["parameter"]] = record["value"]
        db.rows[(record["requested_at"], record["parameter"])] = {
            "delivery_status": "confirmed",
            "confirmed_at": datetime.now(UTC),
        }
    done = await bounded.choose_stage(db, [], db.planned(), 3, tmp_path, 12)
    assert done.action == "complete" and not done.changes
    assert archive.read_bytes() == archive_bytes
    return observation, done


@pytest.mark.asyncio
async def test_actual_producer_completion_settles_only_linked_hold_in_collector(fixture, tmp_path, monkeypatch):
    held, done = await completed_linked_run(fixture, tmp_path, monkeypatch)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    complete = await bounded.completion_observation(fixture, tmp_path, done, 3)
    assert complete["supersedes"] == {
        "run_id": held["run_id"],
        "approved_fingerprint": held["approved_fingerprint"],
        "state_sha256": held["state_sha256"],
        "archive_sha256": held["archive_sha256"],
    }
    assert complete["completed_count"] == 1
    assert complete["handoff_requested_count"] == complete["handoff_confirmed_count"] == 0
    assert complete["handoff_settled"] is True
    assert not any(key in complete for key in ("changes", "readbacks", "session_id", "values"))
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before
    assert await bounded.completion_observation(fixture, tmp_path, done, 3) is None

    spec = importlib.util.spec_from_file_location(
        "run_observation_collector", ROOT / "scripts/experiment_v2_proof_packet.py"
    )
    collector = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = collector
    spec.loader.exec_module(collector)
    now = datetime.now(UTC)
    start = now - timedelta(seconds=10)
    prefix = start.isoformat() + " INFO "
    logs = [
        prefix
        + "writer_reconcile reason=transport_reconnect generation=3 action=awaiting_readbacks expected_readback_count=207",
        prefix
        + "writer_reconcile reason=transport_reconnect generation=3 seeded=241 unobserved_restore_limit=0 comparison=current_generation_readbacks",
        held["observed_at"]
        + " ERROR writer_reconcile reason=retry generation=3 action=blocked_stage reason=confirmed halt archived; fresh approval required reconciliation_receipt="
        + json.dumps(held),
        complete["observed_at"] + " INFO writer_reconciliation_complete receipt=" + json.dumps(complete),
    ]
    stable, errors = collector.writer_stability(
        "\n".join(logs), start.isoformat(), 3, observed_at=now, source_revision="b" * 40, pod="writer-pod"
    )
    assert stable == datetime.fromisoformat(complete["observed_at"])
    assert errors == 0
    assert now - stable < timedelta(minutes=30)  # Completion starts, never bypasses, quiet time.


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status", ["requested", "sent", "deferred_heap_pressure", "failed", "cancelled", "superseded", "expired", "unknown"]
)
async def test_ordinary_handoff_requires_every_actual_row_confirmed(fixture, tmp_path, monkeypatch, status):
    _held, done = await completed_linked_run(fixture, tmp_path, monkeypatch)
    parameter = fixture.names[-1]
    handoff = bounded.Decision("complete", ((parameter, 2.0),), run_id=done.run_id)
    bounded.note_ordinary_handoff(handoff, 3)
    assert await bounded.completion_observation(fixture, tmp_path, handoff, 3) is None
    assert await bounded.completion_observation(fixture, tmp_path, done, 3) is None  # Still unrecorded.
    record = {**records(handoff.changes)[0], "source": "planner"}
    bounded.record_ordinary_handoff(handoff, 3, [record], [])
    native = {"value": 2.0, "source": "planner", "delivery_status": status, "confirmed_at": None, "expired_at": None}
    fixture.rows[(record["requested_at"], parameter)] = native
    fixture.readbacks[parameter] = 2.0  # Equality alone never establishes delivery.
    assert await bounded.completion_observation(fixture, tmp_path, done, 3) is None
    native.update(delivery_status="confirmed", confirmed_at=datetime.now(UTC))
    complete = await bounded.completion_observation(fixture, tmp_path, done, 3)
    assert complete["handoff_requested_count"] == complete["handoff_confirmed_count"] == 1
    state = bounded._read(tmp_path / bounded.STATE_NAME)
    assert complete["effects_sha256"] == bounded._digest(
        {
            "completed": state["completed"],
            "values": state["completed_values"],
            "ordinary_handoff": [
                {
                    "requested_at": record["requested_at"].isoformat(),
                    "parameter": parameter,
                    "value": record["value"],
                    "source": record["source"],
                }
            ],
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "contradiction",
    [
        "missing",
        "value",
        "source",
        "expired",
        "readback",
        "failure",
        "exception",
        "state_race",
        "staged_readback_race",
        "bookkeeping",
        "missing_source",
    ],
)
async def test_handoff_observation_failures_do_not_mutate_or_wake_control(
    fixture, tmp_path, monkeypatch, contradiction
):
    from unittest.mock import Mock

    _held, done = await completed_linked_run(fixture, tmp_path, monkeypatch)
    parameter = fixture.names[-1]
    handoff = bounded.Decision("complete", ((parameter, 2.0),), run_id=done.run_id)
    bounded.note_ordinary_handoff(handoff, 3)
    record = {**records(handoff.changes)[0], "source": "planner"}
    bounded.record_ordinary_handoff(handoff, 3, [record], ["failed"] if contradiction == "failure" else [])
    native = {
        "value": 2.0,
        "source": "planner",
        "delivery_status": "confirmed",
        "confirmed_at": datetime.now(UTC),
        "expired_at": None,
    }
    fixture.rows[(record["requested_at"], parameter)] = native
    fixture.readbacks[parameter] = 2.0
    if contradiction == "missing":
        fixture.rows.clear()
    elif contradiction == "value":
        native["value"] = 3.0
    elif contradiction == "source":
        native["source"] = "other"
    elif contradiction == "expired":
        native["expired_at"] = datetime.now(UTC)
    elif contradiction == "readback":
        fixture.readbacks[parameter] = 0.0
    elif contradiction == "exception":

        async def failed(*_args):
            raise TimeoutError("fixture only")

        fixture.fetchrow = failed
    elif contradiction == "state_race":

        async def racing(*_args):
            state = bounded._read(tmp_path / bounded.STATE_NAME)
            state["status"] = "halted"
            bounded._write(tmp_path / bounded.STATE_NAME, state)
            return native

        fixture.fetchrow = racing
    elif contradiction == "staged_readback_race":

        async def readback_racing(*_args):
            state = bounded._read(tmp_path / bounded.STATE_NAME)
            fixture.readbacks[state["completed"][0]] = None
            return native

        fixture.fetchrow = readback_racing
    elif contradiction == "bookkeeping":
        bounded.record_ordinary_handoff(handoff, 3, [{"parameter": parameter}], [])
    elif contradiction == "missing_source":
        bounded.record_ordinary_handoff(
            handoff, 3, [{key: value for key, value in record.items() if key != "source"}], []
        )
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    wake = Mock(side_effect=AssertionError("metadata cannot wake control"))
    monkeypatch.setattr(bounded, "_wake_for_confirmation", wake)
    assert await bounded.completion_observation(fixture, tmp_path, done, 3) is None
    wake.assert_not_called()
    if contradiction != "state_race":
        assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "contradiction",
    ["source", "pod", "process", "generation", "fingerprint", "lease", "readbacks", "drift", "pending", "rollback"],
)
async def test_complete_observation_binds_current_run_and_writer(fixture, tmp_path, monkeypatch, contradiction):
    _held, done = await completed_linked_run(fixture, tmp_path, monkeypatch)
    state = bounded._read(tmp_path / bounded.STATE_NAME)
    if contradiction in {"source", "pod"}:
        monkeypatch.setenv("VERDIFY_GIT_SHA" if contradiction == "source" else "HOSTNAME", "different")
    elif contradiction == "process":
        monkeypatch.setattr(bounded, "SESSION_ID", uuid.uuid4().hex)
    elif contradiction == "generation":
        monkeypatch.setattr(shared, "transport_generation", 4)
    elif contradiction == "fingerprint":
        state["approved_preview"]["fingerprint"] = "0" * 64
    elif contradiction == "lease":
        monkeypatch.setattr(shared, "writer_lease_strictly_held", lambda **_kwargs: False)
    elif contradiction == "readbacks":
        monkeypatch.setattr(shared, "transport_readbacks_ready", lambda _generation: False)
    elif contradiction == "drift":
        monkeypatch.setattr(shared, "cfg_drift_versions", {"temp_low": 1})
    elif contradiction == "pending":
        state["records"] = [{"parameter": "temp_low"}]
    elif contradiction == "rollback":
        state["rollback_records"] = [{"parameter": "temp_low"}]
    bounded._write(tmp_path / bounded.STATE_NAME, state)
    before = (tmp_path / bounded.STATE_NAME).read_bytes()
    assert await bounded.completion_observation(fixture, tmp_path, done, 3) is None
    assert (tmp_path / bounded.STATE_NAME).read_bytes() == before


@pytest.mark.asyncio
async def test_confirmed_halt_archives_original_effects_and_requires_distinct_fresh_admission(
    fixture, tmp_path, monkeypatch
):
    state, original, forward, remaining = await confirmed_halt_fixture(fixture, tmp_path)
    old_state_bytes = (tmp_path / bounded.STATE_NAME).read_bytes()
    old_approval_bytes = (tmp_path / bounded.APPROVAL_NAME).read_bytes()
    result = await bounded.choose_stage(fixture, remaining, fixture.planned(), 3, tmp_path, 12)
    assert result.action == "hold" and result.reason == "confirmed halt archived; fresh approval required"
    archive_path = tmp_path / f"writer-stage-confirmed-halt-{state['run_id']}.json"
    archive_bytes = archive_path.read_bytes()
    assert (tmp_path / bounded.STATE_NAME).read_bytes() == old_state_bytes
    assert (tmp_path / bounded.APPROVAL_NAME).read_bytes() == old_approval_bytes
    assert bounded._read(archive_path)["forward"]["custody"] == forward["custody"]
    # A deployment may change writer identity. It never inherits old setter
    # authority: independently capture/approve the new current generation.
    monkeypatch.setattr(bounded, "SESSION_ID", uuid.uuid4().hex)
    monkeypatch.setenv("VERDIFY_GIT_SHA", "new-reviewed-source")
    preview = await bounded._preview(fixture, remaining, fixture.planned(), 3)
    new = prepare_writer_stage.prepare(preview, datetime.now(UTC))
    assert new["run_id"] != original["run_id"]
    bounded._write(tmp_path / bounded.APPROVAL_NAME, new)
    result = await bounded.choose_stage(fixture, remaining, fixture.planned(), 3, tmp_path, 12)
    assert result.action == "send" and result.changes == tuple(remaining)
    assert result.run_id == new["run_id"]
    assert bounded._read(tmp_path / bounded.STATE_NAME)["confirmed_halt_link"] == {
        "run_id": state["run_id"],
        "approved_fingerprint": state["approved_preview"]["fingerprint"],
        "state_sha256": bounded._digest(state),
        "archive_sha256": bounded.hashlib.sha256(archive_bytes).hexdigest(),
    }
    assert archive_path.read_bytes() == archive_bytes
    assert not (tmp_path / bounded.RECOVERY_NAME).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "contradiction",
    ["sent", "missing", "duplicate", "expired", "value", "readback", "untouched", "plan", "custody", "later"],
)
async def test_confirmed_halt_forward_rejects_unknown_or_changed_native_history(fixture, tmp_path, contradiction):
    state, original, forward, remaining = await confirmed_halt_fixture(fixture, tmp_path)
    if contradiction == "sent":
        fixture.stage_rows[0]["delivery_status"] = "sent"
    elif contradiction == "missing":
        fixture.stage_rows[0]["confirmed_at"] = None
    elif contradiction == "duplicate":
        fixture.stage_rows.append(fixture.stage_rows[0])
    elif contradiction == "expired":
        fixture.stage_rows[0]["expired_at"] = datetime.now(UTC)
    elif contradiction == "value":
        fixture.stage_rows[0]["value"] = 2.0
    elif contradiction == "readback":
        fixture.readbacks[state["completed"][0]] = 2.0
    elif contradiction == "untouched":
        fixture.readbacks[fixture.parameters[-1]] = 2.0
    elif contradiction == "plan":
        fixture.plan_values[fixture.parameters[-1]] = 2.0
    elif contradiction == "custody":
        forward["custody"][bounded.STATE_NAME] += " "
        bounded._write(tmp_path / bounded.RECOVERY_NAME, forward)
    else:
        prior = fixture.fetch

        async def later(sql, *args):
            return [{"ts": datetime.now(UTC)}] if "AND ts > $2" in sql else await prior(sql, *args)

        fixture.fetch = later
    result = await bounded.choose_stage(fixture, remaining, fixture.planned(), 3, tmp_path, 12)
    assert result.action == "hold"
    assert result.reason != "confirmed halt archived; fresh approval required"
    assert not list(tmp_path.glob("writer-stage-confirmed-halt-*.json"))
    assert bounded._read(tmp_path / bounded.APPROVAL_NAME) == original


@pytest.mark.asyncio
async def test_confirmed_halt_archive_resumes_after_crash_without_reinterpreting_admission_identity(
    fixture, tmp_path, monkeypatch
):
    state, original, forward, remaining = await confirmed_halt_fixture(fixture, tmp_path)
    preview = await bounded._preview(fixture, remaining, fixture.planned(), 3)
    assert not await bounded._confirmed_halt_archive(fixture, preview, state, original, tmp_path)
    archive = tmp_path / f"writer-stage-confirmed-halt-{state['run_id']}.json"
    retained = archive.read_bytes()
    # Simulate restart after durable archive but before installing a distinct
    # approval. Old manifest identity remains historical; it grants no send.
    monkeypatch.setattr(bounded, "SESSION_ID", uuid.uuid4().hex)
    again = await bounded.choose_stage(fixture, remaining, fixture.planned(), 3, tmp_path, 12)
    assert again.action == "hold" and "fresh approval required" in again.reason
    assert archive.read_bytes() == retained
    new_preview = bounded._read(tmp_path / bounded.PREVIEW_NAME)
    assert new_preview["session_id"] != forward["current_identity"]["session_id"]
    new = prepare_writer_stage.prepare(new_preview, datetime.now(UTC))
    bounded._write(tmp_path / bounded.APPROVAL_NAME, new)
    result = await bounded.choose_stage(fixture, remaining, fixture.planned(), 3, tmp_path, 12)
    assert result.action == "send" and result.changes == tuple(remaining)
    assert archive.read_bytes() == retained


@pytest.mark.asyncio
async def test_confirmed_halt_failed_fresh_admission_preserves_original_state_and_archive(fixture, tmp_path):
    state, original, forward, remaining = await confirmed_halt_fixture(fixture, tmp_path)
    old = (tmp_path / bounded.STATE_NAME).read_bytes()
    await bounded.choose_stage(fixture, remaining, fixture.planned(), 3, tmp_path, 12)
    archive = tmp_path / f"writer-stage-confirmed-halt-{state['run_id']}.json"
    retained = archive.read_bytes()
    new = prepare_writer_stage.prepare(bounded._read(tmp_path / bounded.PREVIEW_NAME), datetime.now(UTC))
    new["generation"] = 99
    bounded._write(tmp_path / bounded.APPROVAL_NAME, new)
    result = await bounded.choose_stage(fixture, remaining, fixture.planned(), 3, tmp_path, 12)
    assert result.action == "hold" and "another writer generation" in result.reason
    assert (tmp_path / bounded.STATE_NAME).read_bytes() == old
    assert archive.read_bytes() == retained
    assert (tmp_path / bounded.RECOVERY_NAME).exists()


@pytest.mark.asyncio
async def test_confirmed_halt_missing_current_snapshot_preserves_native_stopped_receipt(fixture, tmp_path, monkeypatch):
    state, original, forward, remaining = await confirmed_halt_fixture(fixture, tmp_path)
    old = (tmp_path / bounded.STATE_NAME).read_bytes()
    monkeypatch.setattr(shared, "transport_readbacks_ready", lambda generation: False)
    result = await bounded.choose_stage(fixture, remaining, fixture.planned(), 3, tmp_path, 12)
    assert result.action == "hold" and "replay incomplete" in result.reason
    assert (tmp_path / bounded.STATE_NAME).read_bytes() == old
    assert not list(tmp_path.glob("writer-stage-confirmed-halt-*.json"))


@pytest.mark.asyncio
async def test_confirmed_halt_custody_retains_later_same_process_transport_generation(fixture, tmp_path):
    state, original, forward, remaining = await confirmed_halt_fixture(fixture, tmp_path)
    custody = forward["original_writer_custody"]
    # Keep the original approved preview immutable while capturing a genuine
    # later connection generation from the same Pod/source/process.
    custody["current"]["preview"] = dict(custody["current"]["preview"], generation=4)
    forward["writer_custody_digest"] = bounded._digest(custody)
    bounded._write(tmp_path / bounded.RECOVERY_NAME, forward)
    old = (tmp_path / bounded.STATE_NAME).read_bytes()
    result = await bounded.choose_stage(fixture, remaining, fixture.planned(), 3, tmp_path, 12)
    assert result.action == "hold" and result.changes == () and "fresh approval required" in result.reason
    assert (tmp_path / bounded.STATE_NAME).read_bytes() == old
    archive = bounded._read(tmp_path / f"writer-stage-confirmed-halt-{state['run_id']}.json")
    assert archive["state"]["approved_preview"]["generation"] == 3
    assert archive["forward"]["original_writer_custody"]["current"]["preview"]["generation"] == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("generation", [2, "4", True, None])
async def test_confirmed_halt_custody_rejects_backwards_or_invalid_generation(fixture, tmp_path, generation):
    state, original, forward, remaining = await confirmed_halt_fixture(fixture, tmp_path)
    custody = forward["original_writer_custody"]
    custody["current"]["preview"] = dict(custody["current"]["preview"], generation=generation)
    with pytest.raises(ValueError, match="custody identity mismatch"):
        bounded._confirmed_halt_writer_custody(state, custody)


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("session_id", "another-process"), ("source_revision", "f" * 40)])
async def test_confirmed_halt_later_generation_cannot_change_process_or_source(fixture, tmp_path, field, value):
    state, original, forward, remaining = await confirmed_halt_fixture(fixture, tmp_path)
    custody = forward["original_writer_custody"]
    custody["current"]["preview"] = dict(custody["current"]["preview"], generation=4, **{field: value})
    with pytest.raises(ValueError, match="custody identity mismatch"):
        bounded._confirmed_halt_writer_custody(state, custody)


@pytest.mark.asyncio
async def test_confirmed_halt_pod_custody_identity_cannot_be_relabelled(fixture, tmp_path):
    state, original, forward, remaining = await confirmed_halt_fixture(fixture, tmp_path)
    forward["original_writer_custody"]["pod"]["metadata"]["name"] = "another-writer"
    forward["writer_custody_digest"] = bounded._digest(forward["original_writer_custody"])
    bounded._write(tmp_path / bounded.RECOVERY_NAME, forward)
    old = (tmp_path / bounded.STATE_NAME).read_bytes()
    result = await bounded.choose_stage(fixture, remaining, fixture.planned(), 3, tmp_path, 12)
    assert result.action == "hold" and "custody identity mismatch" in result.reason
    assert (tmp_path / bounded.STATE_NAME).read_bytes() == old


@pytest.mark.asyncio
async def test_forward_new_admission_lease_failure_halts_new_run_without_rewriting_archive(
    fixture, tmp_path, monkeypatch
):
    state, original, forward, remaining = await confirmed_halt_fixture(fixture, tmp_path)
    await bounded.choose_stage(fixture, remaining, fixture.planned(), 3, tmp_path, 12)
    archive = tmp_path / f"writer-stage-confirmed-halt-{state['run_id']}.json"
    retained = archive.read_bytes()
    new = prepare_writer_stage.prepare(bounded._read(tmp_path / bounded.PREVIEW_NAME), datetime.now(UTC))
    bounded._write(tmp_path / bounded.APPROVAL_NAME, new)
    monkeypatch.setattr(shared, "writer_lease_strictly_held", lambda minimum_remaining_s=0: False)
    result = await bounded.choose_stage(fixture, remaining, fixture.planned(), 3, tmp_path, 12)
    current = bounded._read(tmp_path / bounded.STATE_NAME)
    assert result.action == "hold" and "lease not strictly held" in result.reason
    assert current["run_id"] == new["run_id"] and current["status"] == "halted"
    assert archive.read_bytes() == retained


@pytest.mark.asyncio
async def test_original_halt_protection_does_not_hide_interrupted_rollback_transition(fixture, tmp_path, monkeypatch):
    state, original, forward, remaining = await confirmed_halt_fixture(fixture, tmp_path)
    preview = await bounded._preview(fixture, remaining, fixture.planned(), 3)
    recovery = prepare_writer_stage.prepare_rollback(preview, state, datetime.now(UTC))
    bounded._write(tmp_path / bounded.RECOVERY_NAME, recovery)
    write = bounded._write

    def interrupted(path, value):
        write(path, value)
        if path.name == bounded.STATE_NAME and value.get("status") == "rollback_inflight":
            raise RuntimeError("rollback persistence acknowledgement unknown")

    monkeypatch.setattr(bounded, "_write", interrupted)
    result = await bounded.choose_stage(fixture, remaining, fixture.planned(), 3, tmp_path, 12)
    current = bounded._read(tmp_path / bounded.STATE_NAME)
    assert result.action == "hold" and "acknowledgement unknown" in result.reason
    assert current["run_id"] == state["run_id"] and current["status"] == "rollback_failed"


@pytest.mark.asyncio
async def test_confirmed_halt_archive_binds_changed_waypoint_without_old_plan_authority(fixture, tmp_path):
    state, original, forward, remaining = await confirmed_halt_fixture(fixture, tmp_path)
    # A single effective journal can have later, genuinely changed waypoints.
    # Eight value changes must not be normalized into the original plan.
    for index, parameter in enumerate(fixture.parameters[:8]):
        fixture.plan_values[parameter] = 2.0 + index
    refreshed = await bounded._preview(fixture, remaining, fixture.planned(), 3)
    assert (
        len(
            [
                (a, b)
                for a, b in zip(state["approved_preview"]["plan_rows"], refreshed["plan_rows"], strict=True)
                if a["value"] != b["value"]
            ]
        )
        == 8
    )
    forward = prepare_writer_stage.prepare_forward(
        refreshed,
        forward["custody"],
        forward["native_requests"],
        forward["original_writer_custody"],
        datetime.now(UTC),
    )
    assert forward["version"] == 2 and forward["current_plan_rows"] == refreshed["plan_rows"]
    assert forward["original_approval"]["approved_preview"]["plan_rows"] == state["approved_preview"]["plan_rows"]
    bounded._write(tmp_path / bounded.RECOVERY_NAME, forward)
    old_state = (tmp_path / bounded.STATE_NAME).read_bytes()
    old_approval = (tmp_path / bounded.APPROVAL_NAME).read_bytes()
    result = await bounded.choose_stage(fixture, remaining, fixture.planned(), 3, tmp_path, 12)
    assert result.action == "hold" and result.changes == () and "fresh approval required" in result.reason
    assert (tmp_path / bounded.STATE_NAME).read_bytes() == old_state
    assert (tmp_path / bounded.APPROVAL_NAME).read_bytes() == old_approval
    archive = bounded._read(tmp_path / f"writer-stage-confirmed-halt-{state['run_id']}.json")
    assert archive["state"]["approved_preview"]["plan_rows"] != archive["forward"]["current_plan_rows"]
    # Reusing the original approval remains a no-send hold.
    again = await bounded.choose_stage(fixture, remaining, fixture.planned(), 3, tmp_path, 12)
    assert again.action == "hold" and again.changes == ()
    # Re-admission is a new current-policy run, not the old residual vector.
    # Changed completed fields may receive NEW values through fresh authority;
    # none of the original successful (parameter, value) effects is replayed.
    current_changes = [
        (parameter, fixture.plan_values.get(parameter, 1.0))
        for parameter in fixture.parameters
        if fixture.readbacks[parameter] != fixture.plan_values.get(parameter, 1.0)
    ]
    current_preview = await bounded._preview(fixture, current_changes, fixture.planned(), 3)
    fresh_approval = prepare_writer_stage.prepare(current_preview, datetime.now(UTC))
    bounded._write(tmp_path / bounded.APPROVAL_NAME, fresh_approval)
    native_before = list(fixture.stage_rows)
    admitted = await bounded.choose_stage(fixture, current_changes, fixture.planned(), 3, tmp_path, 12)
    assert admitted.action == "send" and admitted.changes == tuple(current_changes)
    assert admitted.run_id != original["run_id"]
    original_effects = set(state["completed_values"].items())
    assert not original_effects.intersection(admitted.changes)
    assert fixture.stage_rows == native_before  # no native row replay/update
    new_state = bounded._read(tmp_path / bounded.STATE_NAME)
    assert new_state["approved_preview"]["plan_rows"] == current_preview["plan_rows"]
    assert new_state["completed"] == [] and new_state["run_id"] == fresh_approval["run_id"]
    retained = bounded._read(tmp_path / f"writer-stage-confirmed-halt-{state['run_id']}.json")
    assert retained["state"] == state and retained["native_requests"] == forward["native_requests"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["missing", "value", "timestamp", "legacy", "after_archive"])
async def test_confirmed_halt_current_plan_binding_is_required_at_archive_and_admission(fixture, tmp_path, mutation):
    state, original, forward, remaining = await confirmed_halt_fixture(fixture, tmp_path)
    old_state = (tmp_path / bounded.STATE_NAME).read_bytes()
    if mutation == "after_archive":
        result = await bounded.choose_stage(fixture, remaining, fixture.planned(), 3, tmp_path, 12)
        assert "fresh approval required" in result.reason
        fixture.plan_values[fixture.parameters[-1]] = 2.0
        current = await bounded._preview(fixture, remaining, fixture.planned(), 3)
        bounded._write(tmp_path / bounded.APPROVAL_NAME, prepare_writer_stage.prepare(current, datetime.now(UTC)))
    else:
        if mutation == "missing":
            del forward["current_plan_rows"]
        elif mutation == "legacy":
            forward["version"] = 1
        elif mutation == "value":
            forward["current_plan_rows"][0]["value"] = 2.0
        else:
            forward["current_plan_rows"][0]["ts"] = (fixture.ts - timedelta(hours=5)).isoformat()
        bounded._write(tmp_path / bounded.RECOVERY_NAME, forward)
    result = await bounded.choose_stage(fixture, remaining, fixture.planned(), 3, tmp_path, 12)
    assert result.action == "hold" and result.changes == ()
    assert (tmp_path / bounded.STATE_NAME).read_bytes() == old_state
    if mutation != "after_archive":
        assert not list(tmp_path.glob("writer-stage-confirmed-halt-*.json"))


@pytest.mark.asyncio
async def test_autonomous_policy_admits_fresh_source_bound_stages_and_keeps_cap(fixture, tmp_path, monkeypatch):
    db = fixture
    monkeypatch.setenv("VERDIFY_AUTONOMOUS_POLICY_STAGING", "1")
    monkeypatch.setenv("VERDIFY_GIT_SHA", "a" * 40)
    candidate = [(p, 1.0) for p in db.parameters]
    first = await bounded.choose_stage(db, candidate, db.planned(), 3, tmp_path, 12)
    assert first.action == "send" and len(first.changes) == 12
    approval = bounded._read(tmp_path / bounded.APPROVAL_NAME)
    assert approval["authority"] == "source_bound_ordinary_policy_v1"
    assert approval["fingerprint"] == approval["approved_preview"]["fingerprint"]
    assert approval["approved_preview"]["source_revision"] == "a" * 40
    requests = records(first.changes)
    bounded.finish_stage(tmp_path, first, requests, [])
    for r in requests:
        db.readbacks[r["parameter"]] = r["value"]
        db.rows[(r["requested_at"], r["parameter"])] = {
            "delivery_status": "confirmed",
            "confirmed_at": datetime.now(UTC),
        }
    second = await bounded.choose_stage(db, candidate[12:], db.planned(), 3, tmp_path, 12)
    assert second.action == "send" and len(second.changes) == 1


@pytest.mark.asyncio
async def test_autonomous_policy_never_replaces_terminal_failure(fixture, tmp_path, monkeypatch):
    db = fixture
    monkeypatch.setenv("VERDIFY_AUTONOMOUS_POLICY_STAGING", "1")
    monkeypatch.setenv("VERDIFY_GIT_SHA", "a" * 40)
    candidate = [(p, 1.0) for p in db.parameters]
    first = await bounded.choose_stage(db, candidate, db.planned(), 3, tmp_path, 12)
    requests = records(first.changes)
    bounded.finish_stage(tmp_path, first, requests, ["failed"])
    state_before = (tmp_path / bounded.STATE_NAME).read_bytes()
    approval_before = (tmp_path / bounded.APPROVAL_NAME).read_bytes()
    next_decision = await bounded.choose_stage(db, candidate, db.planned(), 3, tmp_path, 12)
    assert next_decision.action == "hold"
    assert (tmp_path / bounded.STATE_NAME).read_bytes() == state_before
    assert (tmp_path / bounded.APPROVAL_NAME).read_bytes() == approval_before


@pytest.mark.asyncio
async def test_autonomous_policy_requires_exact_source_and_active_plan(fixture, tmp_path, monkeypatch):
    monkeypatch.setenv("VERDIFY_AUTONOMOUS_POLICY_STAGING", "1")
    monkeypatch.setenv("VERDIFY_GIT_SHA", "unknown")
    db = fixture
    candidate = [(p, 1.0) for p in db.parameters]
    refused = await bounded.choose_stage(db, candidate, db.planned(), 3, tmp_path, 12)
    assert refused.action == "hold" and "exact source" in refused.reason
    assert not (tmp_path / bounded.APPROVAL_NAME).exists()
    monkeypatch.setenv("VERDIFY_GIT_SHA", "a" * 40)
    refused = await bounded.choose_stage(db, candidate, [], 3, tmp_path, 12)
    assert refused.action == "hold" and "active plan" in refused.reason
    assert not (tmp_path / bounded.APPROVAL_NAME).exists()


def test_hold_receipt_preserves_desired_and_readback_sources(fixture, tmp_path):
    db = fixture
    parameter = db.parameters[0]
    bounded.record_hold(tmp_path, [(parameter, 1.0)], db.planned(), 3, "plan changed")
    receipt = bounded._read(tmp_path / "writer-policy-hold.json")
    assert receipt["disposition"] == "bounded_hold_no_dispatch"
    field = receipt["fields"][0]
    assert field["resolved_desired"] == 1.0
    assert field["current_generation_readback"] == 0.0
    assert field["plan"]["plan_id"] == "iris-test"
    assert field["equivalent"] is False


@pytest.mark.asyncio
async def test_declared_guardrail_can_emerge_after_admission_under_same_plan(fixture, tmp_path, monkeypatch):
    db = fixture
    emerging = ["mister_all_kpa", "mister_engage_kpa"]
    fixed = [p for p in db.names if p not in bounded.DYNAMIC_MOISTURE_GUARDRAIL_PARAMS | bounded.ZONE_VPD_TARGETS][:13]
    db.parameters = fixed + emerging
    initial = [(p, 1.0) for p in fixed]
    source = {p: 0.0 for p in emerging}
    await bounded.choose_stage(db, initial, db.planned(), 3, tmp_path, 12, guardrail_values=source)
    approve(bounded._read(tmp_path / bounded.PREVIEW_NAME), tmp_path)
    first = await bounded.choose_stage(db, initial, db.planned(), 3, tmp_path, 12, guardrail_values=source)
    assert first.action == "send" and len(first.changes) == 12
    sent = records(first.changes)
    bounded.finish_stage(tmp_path, first, sent, [])
    for record in sent:
        db.readbacks[record["parameter"]] = record["value"]
        db.rows[(record["requested_at"], record["parameter"])] = {
            "delivery_status": "confirmed",
            "confirmed_at": datetime.now(UTC),
        }
    source = {emerging[0]: 0.3, emerging[1]: 0.4}
    candidate = [(fixed[-1], 1.0), *source.items()]
    for contradiction in ("source", "plan", "generation", "baseline"):
        target = tmp_path.parent / contradiction
        shutil.copytree(tmp_path, target)
        changed = list(candidate)
        planned = db.planned()
        generation = 3
        if contradiction == "source":
            changed[-1] = (emerging[-1], 0.5)
        elif contradiction == "plan":
            planned[-1] = {**planned[-1], "value": 2.0}
        elif contradiction == "generation":
            generation = 4
        else:
            state = bounded._read(target / bounded.STATE_NAME)
            state["approved_preview"]["readbacks"].pop(emerging[-1])
            bounded._write(target / bounded.STATE_NAME, state)
        held = await bounded.choose_stage(db, changed, planned, generation, target, 12, guardrail_values=source)
        assert held.action == "hold"
    writer_source = tmp_path.parent / "writer-source-fence"
    shutil.copytree(tmp_path, writer_source)
    with monkeypatch.context() as changed_writer:
        changed_writer.setenv("VERDIFY_GIT_SHA", "e" * 40)
        held = await bounded.choose_stage(db, candidate, db.planned(), 3, writer_source, 12, guardrail_values=source)
    assert held.action == "hold" and "source revision changed" in held.reason
    second = await bounded.choose_stage(db, candidate, db.planned(), 3, tmp_path, 12, guardrail_values=source)
    assert second.action == "send" and second.changes == tuple(candidate)
    sent = records(second.changes)
    bounded.finish_stage(tmp_path, second, sent, [])
    for record in sent:
        db.readbacks[record["parameter"]] = record["value"]
        db.rows[(record["requested_at"], record["parameter"])] = {
            "delivery_status": "confirmed",
            "confirmed_at": datetime.now(UTC),
        }
    complete = await bounded.choose_stage(db, [], db.planned(), 3, tmp_path, 12, guardrail_values=source)
    assert complete.action == "complete"


async def confirmed_two_stage_fixture(db, tmp_path):
    import json

    state, original, _forward, _remaining = await confirmed_halt_fixture(db, tmp_path)
    parameters = [p for p in db.names if p not in bounded.DYNAMIC_MOISTURE_GUARDRAIL_PARAMS | bounded.ZONE_VPD_TARGETS][
        :15
    ]
    db.parameters = parameters + ["mister_all_kpa", "mister_engage_kpa"]
    changes = [(p, 1.0) for p in parameters]
    preview = await bounded._preview(db, changes, db.planned(), 3)
    original = prepare_writer_stage.prepare(preview, datetime.now(UTC))
    admitted = datetime.now(UTC) - timedelta(seconds=6)
    preview["captured_at"] = admitted.isoformat()
    # Refresh the prepared fingerprint, whose canonical identity excludes time.
    original["approved_preview"] = preview
    last_started = admitted + timedelta(seconds=3)
    state.update(
        {
            "run_id": original["run_id"],
            "approved_preview": preview,
            "expires_at": original["expires_at"],
            "completed": parameters,
            "completed_values": {p: 1.0 for p in parameters},
            "stage_parameters": parameters[12:],
            "stage_started_at": last_started.isoformat(),
        }
    )
    requests = []
    for i, parameter in enumerate(parameters):
        started = admitted + timedelta(seconds=1 if i < 12 else 4)
        db.readbacks[parameter] = 1.0
        requests.append(
            {
                "ts": started + timedelta(microseconds=i),
                "parameter": parameter,
                "value": 1.0,
                "source": "planner",
                "delivery_status": "confirmed",
                "confirmed_at": started + timedelta(milliseconds=100),
                "expired_at": None,
            }
        )
    db.stage_rows = requests
    for name, value in ((bounded.STATE_NAME, state), (bounded.APPROVAL_NAME, original)):
        bounded._write(tmp_path / name, value)
    custody = {name: (tmp_path / name).read_text() for name in (bounded.STATE_NAME, bounded.APPROVAL_NAME)}
    fresh = await bounded._preview(db, [("mister_all_kpa", 1.0), ("mister_engage_kpa", 1.0)], db.planned(), 3)
    serialized = json.loads(json.dumps(requests, default=lambda v: v.isoformat()))
    writer = {
        "pod": {
            "kind": "Pod",
            "metadata": {"uid": str(uuid.uuid4()), "namespace": "verdify-prod", "name": preview["pod"]},
        },
        "current": {"pod": preview["pod"], "source": preview["source_revision"], "preview": preview},
    }
    with pytest.raises(ValueError):
        prepare_writer_stage.prepare_forward(fresh, custody, serialized, writer, datetime.now(UTC))
    forward = prepare_writer_stage.prepare_forward(
        fresh, custody, serialized, writer, datetime.now(UTC), two_stage=True
    )
    bounded._write(tmp_path / bounded.RECOVERY_NAME, forward)
    return state, original, forward


@pytest.mark.asyncio
async def test_two_stage_confirmed_halt_archives_15_original_effects_without_replay(fixture, tmp_path, monkeypatch):
    state, original, forward = await confirmed_two_stage_fixture(fixture, tmp_path)
    state_bytes = (tmp_path / bounded.STATE_NAME).read_bytes()
    approval_bytes = (tmp_path / bounded.APPROVAL_NAME).read_bytes()
    result = await bounded.choose_stage(fixture, [], fixture.planned(), 3, tmp_path, 12)
    assert result.action == "hold" and result.reason == "confirmed halt archived; fresh approval required"
    archive = bounded._read(tmp_path / f"writer-stage-confirmed-halt-{state['run_id']}.json")
    assert archive["schema"] == "verdify-writer-stage-confirmed-halt-v3"
    assert len(archive["native_requests"]) == 15 and archive["forward"] == forward
    assert (tmp_path / bounded.STATE_NAME).read_bytes() == state_bytes
    assert (tmp_path / bounded.APPROVAL_NAME).read_bytes() == approval_bytes
    assert original["run_id"] == state["run_id"]
    archive_bytes = (tmp_path / f"writer-stage-confirmed-halt-{state['run_id']}.json").read_bytes()
    monkeypatch.setattr(bounded, "SESSION_ID", uuid.uuid4().hex)
    monkeypatch.setenv("VERDIFY_GIT_SHA", "e" * 40)
    remaining = [("mister_all_kpa", 1.0), ("mister_engage_kpa", 1.0)]
    fresh = await bounded._preview(fixture, remaining, fixture.planned(), 3)
    new_authority = prepare_writer_stage.prepare(fresh, datetime.now(UTC))
    assert new_authority["run_id"] != original["run_id"]
    bounded._write(tmp_path / bounded.APPROVAL_NAME, new_authority)
    result = await bounded.choose_stage(fixture, remaining, fixture.planned(), 3, tmp_path, 12)
    assert result.action == "send" and result.changes == tuple(remaining)
    assert result.run_id == new_authority["run_id"]
    assert (tmp_path / f"writer-stage-confirmed-halt-{state['run_id']}.json").read_bytes() == archive_bytes
    assert not (tmp_path / bounded.RECOVERY_NAME).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("contradiction", ["missing", "failed", "sent", "unconfirmed", "first_after_last", "oversize"])
async def test_two_stage_archive_rejects_incomplete_or_unbounded_history(fixture, tmp_path, contradiction):
    state, original, _forward = await confirmed_two_stage_fixture(fixture, tmp_path)
    if contradiction == "missing":
        fixture.stage_rows.pop()
    elif contradiction in {"failed", "sent"}:
        fixture.stage_rows[0]["delivery_status"] = contradiction
    elif contradiction == "unconfirmed":
        fixture.stage_rows[0]["confirmed_at"] = None
    elif contradiction == "first_after_last":
        fixture.stage_rows[0]["confirmed_at"] = datetime.fromisoformat(state["stage_started_at"]) + timedelta(seconds=1)
    else:
        state["stage_parameters"] = state["completed"][:1]
        bounded._write(tmp_path / bounded.STATE_NAME, state)
    result = await bounded.choose_stage(fixture, [], fixture.planned(), 3, tmp_path, 12)
    assert result.action == "hold"
    assert not list(tmp_path.glob("writer-stage-confirmed-halt-*.json"))
    assert bounded._read(tmp_path / bounded.STATE_NAME)["status"] == "halted"


async def changed_archived_plan(db, tmp_path, monkeypatch):
    """Retain twelve old effects; approve twenty NEW values in a new writer/generation."""
    monkeypatch.setenv("VERDIFY_GIT_SHA", "a" * 40)
    monkeypatch.setenv("HOSTNAME", "historical-writer")
    old, original, _forward, remaining = await confirmed_halt_fixture(db, tmp_path)
    held = await bounded.choose_stage(db, remaining, db.planned(), 3, tmp_path, 12)
    assert held.action == "hold" and "fresh approval required" in held.reason
    path = tmp_path / f"writer-stage-confirmed-halt-{old['run_id']}.json"
    raw = path.read_bytes()
    db.parameters = [
        p for p in db.names if p not in bounded.DYNAMIC_MOISTURE_GUARDRAIL_PARAMS | bounded.ZONE_VPD_TARGETS
    ][:20]
    db.plan_values = dict.fromkeys(db.parameters, 2.0)
    changes = [(p, 2.0) for p in db.parameters]
    monkeypatch.setenv("VERDIFY_GIT_SHA", "b" * 40)
    monkeypatch.setenv("HOSTNAME", "current-writer")
    monkeypatch.setattr(bounded, "SESSION_ID", uuid.uuid4().hex)
    monkeypatch.setattr(shared, "transport_readbacks_ready", lambda generation: generation == 10)
    monkeypatch.setattr(shared, "transport_generation", 10)
    monkeypatch.setattr(shared, "reconciled_transport_generation", 10)
    monkeypatch.setattr(shared, "cfg_drift_versions", {})
    preview = await bounded._preview(db, changes, db.planned(), 10)
    assert preview["plan_rows"] != old["approved_preview"]["plan_rows"]
    approval = prepare_writer_stage.prepare(preview, datetime.now(UTC), confirmed_halt_archive=raw)
    assert approval["run_id"] != original["run_id"]
    return old, changes, approval, path, raw


@pytest.mark.asyncio
@pytest.mark.parametrize("completion_damage", [None, "approval", "archive", "missing_approval", "state_link"])
async def test_explicit_archive_link_admits_twenty_current_values_with_confirmation_between_stages(
    fixture, tmp_path, monkeypatch, completion_damage
):
    old, changes, approval, path, raw = await changed_archived_plan(fixture, tmp_path, monkeypatch)
    # The old hold is observed by this new writer before explicit admission.
    held = await bounded.choose_stage(fixture, changes, fixture.planned(), 10, tmp_path, 12)
    assert held.action == "hold"
    held_observation = bounded.hold_observation(tmp_path, held, 10)
    native_before = json.dumps(fixture.stage_rows, default=str)
    bounded._write(tmp_path / bounded.APPROVAL_NAME, approval)
    sizes = []
    while changes:
        selection = await bounded.choose_stage(fixture, changes, fixture.planned(), 10, tmp_path, 12)
        assert selection.action == "send"
        assert selection.run_id == approval["run_id"] != old["run_id"]
        assert 0 < len(selection.changes) <= 12
        assert not set(selection.changes).intersection(old["completed_values"].items())
        sizes.append(len(selection.changes))
        stage_records = records(selection.changes)
        bounded.finish_stage(tmp_path, selection, stage_records, [])
        for record in stage_records:
            fixture.rows[(record["requested_at"], record["parameter"])] = {
                "delivery_status": "sent",
                "confirmed_at": None,
            }
        pending = await bounded.choose_stage(fixture, changes, fixture.planned(), 10, tmp_path, 12)
        assert pending.action == "hold" and pending.reason == "awaiting durable confirmation"
        for record in stage_records:
            fixture.readbacks[record["parameter"]] = record["value"]
            fixture.rows[(record["requested_at"], record["parameter"])] = {
                "delivery_status": "confirmed",
                "confirmed_at": datetime.now(UTC),
            }
        changes = [(p, value) for p, value in changes if fixture.readbacks[p] != value]
    done = await bounded.choose_stage(fixture, [], fixture.planned(), 10, tmp_path, 12)
    assert done.action == "complete" and not done.changes
    assert sizes == [12, 8]
    assert path.read_bytes() == raw
    assert json.dumps(fixture.stage_rows, default=str) == native_before
    state = bounded._read(tmp_path / bounded.STATE_NAME)
    assert state["confirmed_halt_link_required"] is True
    assert state["confirmed_halt_link"] == approval["confirmed_halt_link"]
    assert len(state["completed"]) == 20
    if completion_damage == "approval":
        del approval["confirmed_halt_link"]
        bounded._write(tmp_path / bounded.APPROVAL_NAME, approval)
    elif completion_damage == "archive":
        path.write_bytes(raw + b"\n")
    elif completion_damage == "missing_approval":
        (tmp_path / bounded.APPROVAL_NAME).unlink()
    elif completion_damage == "state_link":
        del state["confirmed_halt_link"]
        bounded._write(tmp_path / bounded.STATE_NAME, state)
    complete = await bounded.completion_observation(fixture, tmp_path, done, 10)
    if completion_damage is not None:
        assert complete is None  # Actual completion cannot attest damaged linked authority.
        return
    assert complete["supersedes"] == approval["confirmed_halt_link"]
    assert complete["supersedes"]["run_id"] == held_observation["run_id"]
    assert complete["handoff_settled"] is True and complete["completed_count"] == 20


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage",
    [
        "run_id",
        "approved_fingerprint",
        "state_sha256",
        "archive_sha256",
        "null",
        "list",
        "extra",
        "missing",
        "old_run",
        "old_run_alias",
        "authority",
        "preview",
        "missing_archive",
        "archive_bytes",
    ],
)
async def test_explicit_archive_link_denies_before_replacing_old_state(fixture, tmp_path, monkeypatch, damage):
    _old, changes, approval, path, raw = await changed_archived_plan(fixture, tmp_path, monkeypatch)
    protected = {name: (tmp_path / name).read_bytes() for name in (bounded.STATE_NAME, bounded.RECOVERY_NAME)}
    if damage in {"run_id", "approved_fingerprint", "state_sha256", "archive_sha256"}:
        approval["confirmed_halt_link"][damage] = uuid.uuid4().hex if damage == "run_id" else "0" * 64
    elif damage == "null":
        approval["confirmed_halt_link"] = None
    elif damage == "list":
        approval["confirmed_halt_link"] = []
    elif damage == "extra":
        approval["confirmed_halt_link"]["unexpected"] = True
    elif damage == "missing":
        del approval["confirmed_halt_link"]["archive_sha256"]
    elif damage == "old_run":
        approval["run_id"] = approval["confirmed_halt_link"]["run_id"]
    elif damage == "old_run_alias":
        approval["run_id"] = str(uuid.UUID(approval["confirmed_halt_link"]["run_id"]))
    elif damage == "authority":
        approval["authority"] = "source_bound_ordinary_policy_v1"
    elif damage == "preview":
        del approval["approved_preview"]
    elif damage == "missing_archive":
        path.unlink()
    else:
        path.write_bytes(raw + b"\n")
    # Best-effort observation deliberately fails; it cannot authorize admission.
    monkeypatch.setattr(bounded, "_confirmed_halt_link", lambda *_args: None)
    bounded._write(tmp_path / bounded.APPROVAL_NAME, approval)
    result = await bounded.choose_stage(fixture, changes, fixture.planned(), 10, tmp_path, 12)
    assert result.action == "hold" and not result.changes
    assert {name: (tmp_path / name).read_bytes() for name in protected} == protected
    if damage == "missing_archive":
        assert not path.exists()
    else:
        assert path.read_bytes() == (raw + b"\n" if damage == "archive_bytes" else raw)


@pytest.mark.asyncio
async def test_linked_approval_cannot_start_without_retained_halt(fixture, tmp_path, monkeypatch):
    _old, changes, approval, path, raw = await changed_archived_plan(fixture, tmp_path, monkeypatch)
    (tmp_path / bounded.STATE_NAME).unlink()
    bounded._write(tmp_path / bounded.APPROVAL_NAME, approval)
    result = await bounded.choose_stage(fixture, changes, fixture.planned(), 10, tmp_path, 12)
    assert result.action == "hold" and "no retained transition" in result.reason
    assert not (tmp_path / bounded.STATE_NAME).exists()
    assert path.read_bytes() == raw


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage",
    [
        "source",
        "session",
        "generation",
        "plan",
        "candidate",
        "stale",
        "future",
        "expired",
        "duration",
        "readback",
        "untouched",
        "native",
        "later",
    ],
)
async def test_linked_admission_preserves_current_and_historical_fences(fixture, tmp_path, monkeypatch, damage):
    _old, changes, approval, path, raw = await changed_archived_plan(fixture, tmp_path, monkeypatch)
    protected = (tmp_path / bounded.STATE_NAME).read_bytes()
    generation = 10
    if damage == "source":
        monkeypatch.setenv("VERDIFY_GIT_SHA", "c" * 40)
    elif damage == "session":
        monkeypatch.setattr(bounded, "SESSION_ID", uuid.uuid4().hex)
    elif damage == "generation":
        generation = 11
        monkeypatch.setattr(shared, "transport_readbacks_ready", lambda _generation: True)
    elif damage == "plan":
        fixture.plan_values[fixture.parameters[0]] = 3.0
    elif damage == "candidate":
        changes[0] = (changes[0][0], 3.0)
    elif damage in {"stale", "future"}:
        approval["approved_preview"]["captured_at"] = (
            datetime.now(UTC) + (timedelta(minutes=-7) if damage == "stale" else timedelta(seconds=30))
        ).isoformat()
    elif damage in {"expired", "duration"}:
        approval["expires_at"] = (
            datetime.now(UTC) + (timedelta(seconds=-1) if damage == "expired" else timedelta(minutes=31))
        ).isoformat()
    elif damage == "readback":
        fixture.readbacks[fixture.parameters[0]] = 9.0
    elif damage == "untouched":
        fixture.readbacks[fixture.names[-1]] = 9.0
    elif damage == "native":
        fixture.stage_rows[0]["delivery_status"] = "sent"
    else:
        fetch = fixture.fetch

        async def later(sql, *args):
            return [{"ts": datetime.now(UTC)}] if "AND ts > $2" in sql else await fetch(sql, *args)

        fixture.fetch = later
    bounded._write(tmp_path / bounded.APPROVAL_NAME, approval)
    result = await bounded.choose_stage(fixture, changes, fixture.planned(), generation, tmp_path, 12)
    assert result.action == "hold" and not result.changes
    assert (tmp_path / bounded.STATE_NAME).read_bytes() == protected
    assert path.read_bytes() == raw


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["client", "lease"])
async def test_linked_admission_without_client_or_lease_never_sends(fixture, tmp_path, monkeypatch, missing):
    _old, changes, approval, path, raw = await changed_archived_plan(fixture, tmp_path, monkeypatch)
    bounded._write(tmp_path / bounded.APPROVAL_NAME, approval)
    if missing == "client":
        monkeypatch.setattr(shared, "esp32", {"client": None})
    else:
        monkeypatch.setattr(shared, "writer_lease_strictly_held", lambda **_kwargs: False)
    result = await bounded.choose_stage(fixture, changes, fixture.planned(), 10, tmp_path, 12)
    assert result.action == "hold" and not result.changes
    state = bounded._read(tmp_path / bounded.STATE_NAME)
    assert state["run_id"] == approval["run_id"] and state["status"] == "halted"
    assert path.read_bytes() == raw


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["removed", "link", "preview", "expiry", "archive"])
async def test_linked_authority_is_checked_before_confirmation_or_next_stage(fixture, tmp_path, monkeypatch, damage):
    _old, changes, approval, path, raw = await changed_archived_plan(fixture, tmp_path, monkeypatch)
    bounded._write(tmp_path / bounded.APPROVAL_NAME, approval)
    selected = await bounded.choose_stage(fixture, changes, fixture.planned(), 10, tmp_path, 12)
    assert selected.action == "send"
    bounded.finish_stage(tmp_path, selected, records(selected.changes), [])
    pending_records = bounded._read(tmp_path / bounded.STATE_NAME)["records"]
    if damage == "removed":
        del approval["confirmed_halt_link"]
    elif damage == "link":
        approval["confirmed_halt_link"]["state_sha256"] = "0" * 64
    elif damage == "preview":
        approval["approved_preview"]["changes"][0][1] = 3.0
    elif damage == "expiry":
        approval["expires_at"] = (datetime.now(UTC) + timedelta(minutes=20)).isoformat()
    else:
        path.write_bytes(raw + b"\n")
    bounded._write(tmp_path / bounded.APPROVAL_NAME, approval)
    result = await bounded.choose_stage(fixture, changes, fixture.planned(), 10, tmp_path, 12)
    assert result.action == "hold" and not result.changes
    state = bounded._read(tmp_path / bounded.STATE_NAME)
    assert state["status"] == "halted" and state["records"] == pending_records


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["before_ready", "after_ready", "after_inflight", "recovery_unlink"])
async def test_linked_transition_interruption_retains_custody_and_unknown_outcome(
    fixture, tmp_path, monkeypatch, failure
):
    old, changes, approval, path, raw = await changed_archived_plan(fixture, tmp_path, monkeypatch)
    bounded._write(tmp_path / bounded.APPROVAL_NAME, approval)
    write = bounded._write

    def interrupted(target, value):
        target_state = target.name == bounded.STATE_NAME and value.get("run_id") == approval["run_id"]
        if target_state and value.get("status") == "ready" and failure == "before_ready":
            raise OSError("new state write failed before persistence")
        write(target, value)
        if target_state and (
            value.get("status") == "ready"
            and failure == "after_ready"
            or value.get("status") == "inflight"
            and failure == "after_inflight"
        ):
            raise OSError("new state persistence acknowledgement unknown")

    monkeypatch.setattr(bounded, "_write", interrupted)
    if failure == "recovery_unlink":
        unlink = Path.unlink

        def interrupted_unlink(target, *args, **kwargs):
            if target.name == bounded.RECOVERY_NAME:
                raise OSError("recovery unlink acknowledgement unknown")
            return unlink(target, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", interrupted_unlink)
    result = await bounded.choose_stage(fixture, changes, fixture.planned(), 10, tmp_path, 12)
    assert result.action == "hold" and not result.changes
    state = bounded._read(tmp_path / bounded.STATE_NAME)
    assert state["status"] == "halted"
    assert state["run_id"] == (old["run_id"] if failure == "before_ready" else approval["run_id"])
    assert path.read_bytes() == raw
    if failure != "before_ready":
        # Unknown durable new state must not turn back into a first send.
        again = await bounded.choose_stage(fixture, changes, fixture.planned(), 10, tmp_path, 12)
        assert again.action == "hold" and not again.changes


@pytest.mark.asyncio
async def test_explicit_archive_preparer_cli_is_opt_in_and_exclusive(fixture, tmp_path, monkeypatch):
    _old, _changes, _approval, path, raw = await changed_archived_plan(fixture, tmp_path, monkeypatch)
    preview = await bounded._preview(fixture, _changes, fixture.planned(), 10)
    preview_path = tmp_path / "captured-preview.json"
    bounded._write(preview_path, preview)
    output = tmp_path / "new-approval.json"
    monkeypatch.setattr(sys, "argv", ["prepare", str(preview_path), str(output), "--confirmed-halt-archive", str(path)])
    prepare_writer_stage.main()
    prepared = bounded._read(output)
    assert prepared["confirmed_halt_link"] == bounded.confirmed_halt_archive_link(raw)
    before = output.read_bytes()
    with pytest.raises(FileExistsError):
        prepare_writer_stage.main()
    assert output.read_bytes() == before
    assert path.read_bytes() == raw
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prepare",
            str(preview_path),
            str(output),
            "--confirmed-halt-archive",
            str(path),
            "--rollback-state",
            str(tmp_path / bounded.STATE_NAME),
        ],
    )
    with pytest.raises(SystemExit) as error:
        prepare_writer_stage.main()
    assert error.value.code == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["json", "schema", "custody", "completed", "pod", "native", "cutoff", "version"])
async def test_archive_preparer_rejects_damaged_historical_custody(fixture, tmp_path, monkeypatch, damage):
    _old, changes, _approval, path, raw = await changed_archived_plan(fixture, tmp_path, monkeypatch)
    archive = json.loads(raw)
    if damage == "schema":
        archive["schema"] = "unrecognized"
    elif damage == "custody":
        archive["forward"]["custody"][bounded.STATE_NAME] += " "
    elif damage == "completed":
        name = archive["state"]["completed"][0]
        archive["state"]["completed_values"][name] = 9.0
    elif damage == "pod":
        archive["forward"]["original_writer_custody"]["pod"]["metadata"]["uid"] = "not-a-uuid"
    elif damage == "native":
        archive["native_requests"][0]["delivery_status"] = "sent"
    elif damage == "cutoff":
        archive["native_requests"][0]["confirmed_at"] = (datetime.now(UTC) + timedelta(minutes=1)).isoformat()
    elif damage == "version":
        archive["forward"]["version"] = 1
    damaged = b"{" if damage == "json" else json.dumps(archive).encode()
    preview = await bounded._preview(fixture, changes, fixture.planned(), 10)
    with pytest.raises((ValueError, KeyError, TypeError)):
        prepare_writer_stage.prepare(preview, datetime.now(UTC), confirmed_halt_archive=damaged)
    assert path.read_bytes() == raw


@pytest.mark.asyncio
async def test_explicit_link_also_retains_existing_two_stage_archive(fixture, tmp_path, monkeypatch):
    state, _original, _forward = await confirmed_two_stage_fixture(fixture, tmp_path)
    held = await bounded.choose_stage(fixture, [], fixture.planned(), 3, tmp_path, 12)
    assert held.action == "hold" and "fresh approval required" in held.reason
    path = tmp_path / f"writer-stage-confirmed-halt-{state['run_id']}.json"
    raw = path.read_bytes()
    parameter = fixture.parameters[-1]
    fixture.plan_values[parameter] = 2.0
    changes = [(parameter, 2.0)]
    monkeypatch.setattr(bounded, "SESSION_ID", uuid.uuid4().hex)
    preview = await bounded._preview(fixture, changes, fixture.planned(), 3)
    approval = prepare_writer_stage.prepare(preview, datetime.now(UTC), confirmed_halt_archive=raw)
    bounded._write(tmp_path / bounded.APPROVAL_NAME, approval)
    result = await bounded.choose_stage(fixture, changes, fixture.planned(), 3, tmp_path, 12)
    assert result.action == "send" and result.run_id != state["run_id"]
    assert result.changes == tuple(changes)
    assert bounded._read(tmp_path / bounded.STATE_NAME)["confirmed_halt_link_required"] is True
    assert path.read_bytes() == raw


@pytest.mark.asyncio
async def test_linked_run_preserves_existing_settled_rollback_fresh_admission(fixture, tmp_path, monkeypatch):
    _old, changes, approval, path, raw = await changed_archived_plan(fixture, tmp_path, monkeypatch)
    bounded._write(tmp_path / bounded.APPROVAL_NAME, approval)
    first = await bounded.choose_stage(fixture, changes, fixture.planned(), 10, tmp_path, 12)
    assert first.action == "send"
    bounded.finish_stage(tmp_path, first, [], [])  # No successful effects claimed for this new stage.
    await bounded.choose_stage(fixture, changes, fixture.planned(), 10, tmp_path, 12)
    state = bounded._read(tmp_path / bounded.STATE_NAME)
    preview = bounded._read(tmp_path / bounded.PREVIEW_NAME)
    recovery = prepare_writer_stage.prepare_rollback(preview, state, datetime.now(UTC))
    bounded._write(tmp_path / bounded.RECOVERY_NAME, recovery)
    settled = await bounded.choose_stage(fixture, changes, fixture.planned(), 10, tmp_path, 12)
    assert settled.action == "hold" and settled.reason == "last stage already matches baseline"
    state = bounded._read(tmp_path / bounded.STATE_NAME)
    assert state["status"] == "rollback_complete"
    fixture.stage_rows = [
        {
            "ts": datetime.fromisoformat(state["stage_started_at"]),
            "parameter": parameter,
            "value": value,
            "delivery_status": "deferred_heap_pressure",
            "confirmed_at": None,
        }
        for parameter, value in first.changes
    ]
    archived = await bounded.choose_stage(fixture, changes, fixture.planned(), 10, tmp_path, 12)
    assert archived.action == "hold" and "fresh approval required" in archived.reason
    recovered_path = tmp_path / f"writer-stage-recovered-{state['run_id']}.json"
    recovered_raw = recovered_path.read_bytes()
    fresh = prepare_writer_stage.prepare(bounded._read(tmp_path / bounded.PREVIEW_NAME), datetime.now(UTC))
    bounded._write(tmp_path / bounded.APPROVAL_NAME, fresh)
    resumed = await bounded.choose_stage(fixture, changes, fixture.planned(), 10, tmp_path, 12)
    assert resumed.action == "send" and resumed.run_id == fresh["run_id"] != approval["run_id"]
    assert recovered_path.read_bytes() == recovered_raw and path.read_bytes() == raw


@pytest.mark.asyncio
async def test_strict_archive_validation_rejects_coherent_custody_with_false_original_fingerprint(
    fixture, tmp_path, monkeypatch
):
    _old, changes, _approval, path, raw = await changed_archived_plan(fixture, tmp_path, monkeypatch)
    archive = json.loads(raw)
    state, forward = archive["state"], archive["forward"]
    state["approved_preview"]["fingerprint"] = "0" * 64
    original = archive["approval"]
    original["approved_preview"] = state["approved_preview"]
    original["fingerprint"] = "0" * 64
    forward["original_approval"] = original
    forward["custody"][bounded.STATE_NAME] = json.dumps(state)
    forward["custody"][bounded.APPROVAL_NAME] = json.dumps(original)
    forward["state_digest"] = bounded._digest(state)
    forward["approval_digest"] = bounded._digest(original)
    forward["custody_sha256"] = bounded._digest(forward["custody"])
    # Even self-consistent containers cannot replace the approved full-vector hash.
    preview = await bounded._preview(fixture, changes, fixture.planned(), 10)
    with pytest.raises(ValueError, match="original preview fingerprint"):
        prepare_writer_stage.prepare(preview, datetime.now(UTC), confirmed_halt_archive=json.dumps(archive).encode())
    assert path.read_bytes() == raw


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["history", "later"])
@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "archive_changed",
        "archive_removed",
        "approval_removed",
        "approval_replaced",
        "state_changed",
        "recovery_removed",
        "recovery_changed",
    ],
)
async def test_admission_rechecks_custody_after_each_native_await(fixture, tmp_path, monkeypatch, phase, mutation):
    _old, changes, approval, path, _raw = await changed_archived_plan(fixture, tmp_path, monkeypatch)
    bounded._write(tmp_path / bounded.APPROVAL_NAME, approval)
    names = (bounded.STATE_NAME, bounded.APPROVAL_NAME, bounded.RECOVERY_NAME, path.name)

    def custody():
        return {name: (tmp_path / name).read_bytes() if (tmp_path / name).exists() else None for name in names}

    expected = custody()
    calls = []
    fetch = fixture.fetch

    async def mutate_during_fetch(sql, *args):
        rows = await fetch(sql, *args)
        if "FROM setpoint_changes" not in sql:
            return rows
        current_phase = "later" if "AND ts > $2" in sql else "history"
        calls.append(current_phase)
        if current_phase == phase and mutation is not None:
            if mutation == "archive_changed":
                path.write_bytes(path.read_bytes() + b"\n")
            elif mutation == "archive_removed":
                path.unlink()
            elif mutation == "approval_removed":
                (tmp_path / bounded.APPROVAL_NAME).unlink()
            elif mutation == "approval_replaced":
                replacement = {**approval, "run_id": uuid.uuid4().hex}
                bounded._write(tmp_path / bounded.APPROVAL_NAME, replacement)
            elif mutation == "state_changed":
                changed = bounded._read(tmp_path / bounded.STATE_NAME)
                changed["halt_reason"] = "external operator changed custody"
                bounded._write(tmp_path / bounded.STATE_NAME, changed)
            elif mutation == "recovery_removed":
                (tmp_path / bounded.RECOVERY_NAME).unlink()
            else:
                target = tmp_path / bounded.RECOVERY_NAME
                target.write_bytes(target.read_bytes() + b"\n")
            expected.update(custody())
        return rows

    fixture.fetch = mutate_during_fetch
    result = await bounded.choose_stage(fixture, changes, fixture.planned(), 10, tmp_path, 12)
    if mutation is None:
        assert result.action == "send" and len(result.changes) == 12
        assert calls == ["history", "later"]
    else:
        assert result.action == "hold" and not result.changes
        assert "changed during native validation" in result.reason
        assert calls == (["history"] if phase == "history" else ["history", "later"])
        # Denying stale authority must not rewrite the retained or concurrently
        # changed files, even when the old state's digest no longer matches.
        assert custody() == expected


@pytest.mark.asyncio
async def test_generic_approval_cannot_reuse_old_uuid_through_hyphenated_alias(fixture, tmp_path):
    old, _original, _forward, changes = await confirmed_halt_fixture(fixture, tmp_path)
    held = await bounded.choose_stage(fixture, changes, fixture.planned(), 3, tmp_path, 12)
    assert held.action == "hold"
    state_raw = (tmp_path / bounded.STATE_NAME).read_bytes()
    path = tmp_path / f"writer-stage-confirmed-halt-{old['run_id']}.json"
    archive_raw = path.read_bytes()
    preview = await bounded._preview(fixture, changes, fixture.planned(), 3)
    approval = prepare_writer_stage.prepare(preview, datetime.now(UTC))
    approval["run_id"] = str(uuid.UUID(old["run_id"]))
    assert approval["run_id"] != old["run_id"]
    bounded._write(tmp_path / bounded.APPROVAL_NAME, approval)
    result = await bounded.choose_stage(fixture, changes, fixture.planned(), 3, tmp_path, 12)
    assert result.action == "hold" and not result.changes
    assert (tmp_path / bounded.STATE_NAME).read_bytes() == state_raw
    assert path.read_bytes() == archive_raw


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation", [None, "approval_removed", "approval_replaced", "archive_changed", "archive_removed", "state_changed"]
)
async def test_completion_rechecks_linked_authority_after_nonempty_handoff_await(
    fixture, tmp_path, monkeypatch, mutation
):
    _old, changes, approval, path, _raw = await changed_archived_plan(fixture, tmp_path, monkeypatch)
    bounded._write(tmp_path / bounded.APPROVAL_NAME, approval)
    while changes:
        selected = await bounded.choose_stage(fixture, changes, fixture.planned(), 10, tmp_path, 12)
        assert selected.action == "send"
        stage = records(selected.changes)
        bounded.finish_stage(tmp_path, selected, stage, [])
        for record in stage:
            fixture.readbacks[record["parameter"]] = record["value"]
            fixture.rows[(record["requested_at"], record["parameter"])] = {
                "delivery_status": "confirmed",
                "confirmed_at": datetime.now(UTC),
            }
        changes = [(p, value) for p, value in changes if fixture.readbacks[p] != value]
    done = await bounded.choose_stage(fixture, [], fixture.planned(), 10, tmp_path, 12)
    assert done.action == "complete"
    parameter = bounded._read(tmp_path / bounded.STATE_NAME)["completed"][0]
    handoff = bounded.Decision("complete", ((parameter, 3.0),), run_id=done.run_id)
    bounded.note_ordinary_handoff(handoff, 10)
    record = {**records(handoff.changes)[0], "source": "planner"}
    bounded.record_ordinary_handoff(handoff, 10, [record], [])
    fixture.readbacks[parameter] = 3.0
    fixture.rows[(record["requested_at"], parameter)] = {
        "value": 3.0,
        "source": "planner",
        "delivery_status": "confirmed",
        "confirmed_at": datetime.now(UTC),
        "expired_at": None,
    }
    calls = []
    fetchrow = fixture.fetchrow

    async def mutate_during_fetchrow(sql, *args):
        row = await fetchrow(sql, *args)
        calls.append(args)
        if mutation == "approval_removed":
            (tmp_path / bounded.APPROVAL_NAME).unlink()
        elif mutation == "approval_replaced":
            bounded._write(tmp_path / bounded.APPROVAL_NAME, {**approval, "run_id": uuid.uuid4().hex})
        elif mutation == "archive_changed":
            path.write_bytes(path.read_bytes() + b"\n")
        elif mutation == "archive_removed":
            path.unlink()
        elif mutation == "state_changed":
            state = bounded._read(tmp_path / bounded.STATE_NAME)
            state["external_change"] = True
            bounded._write(tmp_path / bounded.STATE_NAME, state)
        return row

    fixture.fetchrow = mutate_during_fetchrow
    complete = await bounded.completion_observation(fixture, tmp_path, done, 10)
    assert len(calls) == 1  # Every mutation happens inside a real awaited handoff read.
    key = (bounded.SESSION_ID, 10, done.run_id)
    if mutation is None:
        assert complete["handoff_settled"] is True
        assert complete["handoff_confirmed_count"] == complete["handoff_requested_count"] == 1
        assert complete["supersedes"] == approval["confirmed_halt_link"]
    else:
        assert complete is None
        assert key not in bounded._observed_completions
        assert key in bounded._handoffs  # Failed attestation retains pending custody.


async def completed_reapplied_guard_history(db, tmp_path, monkeypatch):
    """Sanitized runtime shape: 19 candidates become 12+11 confirmed applications."""
    initial_guard = {
        "fog_escalation_kpa": 0.3,
        "min_fog_off_s": 90.0,
        "mister_all_delay_s": 90.0,
        "mister_all_kpa": 1.3,
        "mister_engage_delay_s": 45.0,
        "mister_engage_kpa": 0.9,
        "mister_pulse_gap_s": 30.0,
    }
    db.readbacks.update({param: value * 2 for param, value in initial_guard.items()})
    db.readbacks["mister_pulse_gap_s"] = 30.0
    old, _changes, _approval, archive, raw = await changed_archived_plan(db, tmp_path, monkeypatch)
    fixed = {
        "cool_exit_hysteresis_f": 2.0,
        "cool_stage2_over_high_f": 2.5,
        "direct_wet_stress_vpd_margin_kpa": 0.2,
        "dwell_gate_ms": 120000.0,
        "min_fog_on_s": 30.0,
        "mister_vpd_weight": 1.5,
        "temp_hysteresis": 2.0,
        "vpd_hysteresis": 0.2,
        "vpd_watch_dwell_s": 90.0,
    }
    zones = dict(zip(sorted(bounded.ZONE_VPD_TARGETS), (0.7, 0.8, 0.9, 1.0), strict=True))
    db.readbacks.update(dict.fromkeys(zones, 0.0))
    rebased_guard = {
        **initial_guard,
        "fog_escalation_kpa": 0.35,
        "min_fog_off_s": 120.0,
        "mister_all_delay_s": 120.0,
        "mister_pulse_gap_s": 45.0,
    }
    db.parameters = [*fixed, *initial_guard, *zones]
    db.plan_values = {**fixed, **rebased_guard, **zones}
    initial = [*zones.items(), *fixed.items(), *list(initial_guard.items())[:-1]]
    assert len(initial) == 19
    preview = await bounded._preview(db, initial, db.planned(), 10)
    approval = prepare_writer_stage.prepare(preview, datetime.now(UTC), confirmed_halt_archive=raw)
    bounded._write(tmp_path / bounded.APPROVAL_NAME, approval)
    native_history = []
    candidate, guard = initial, initial_guard
    for expected_count in (12, 11):
        selected = await bounded.choose_stage(db, candidate, db.planned(), 10, tmp_path, 12, zones, guard)
        assert selected.action == "send" and len(selected.changes) == expected_count
        assert selected.run_id == approval["run_id"] != old["run_id"]
        stage = records(selected.changes)
        native_history.extend(stage)
        bounded.finish_stage(tmp_path, selected, stage, [])
        for record in stage:
            db.readbacks[record["parameter"]] = record["value"]
            db.rows[(record["requested_at"], record["parameter"])] = {
                "delivery_status": "sent",
                "confirmed_at": None,
            }
        # Matching cfg values cannot advance a stage without native confirmation.
        pending = await bounded.choose_stage(db, candidate, db.planned(), 10, tmp_path, 12, zones, guard)
        assert pending.action == "hold" and pending.reason == "awaiting durable confirmation"
        for record in stage:
            db.rows[(record["requested_at"], record["parameter"])].update(
                delivery_status="confirmed", confirmed_at=datetime.now(UTC)
            )
        candidate, guard = [*zones.items(), *rebased_guard.items()], rebased_guard
    done = await bounded.choose_stage(db, [], db.planned(), 10, tmp_path, 12, zones, guard)
    assert done.action == "complete" and not done.changes
    state = bounded._read(tmp_path / bounded.STATE_NAME)
    assert state["completed"] == [record["parameter"] for record in native_history]
    assert len(state["completed"]) == 23 and len(state["completed_values"]) == 20
    assert archive.read_bytes() == raw
    return done, approval, native_history


@pytest.mark.asyncio
async def test_guardrail_reapplication_completion_preserves_confirmed_application_history(
    fixture, tmp_path, monkeypatch
):
    from unittest.mock import Mock

    done, approval, history = await completed_reapplied_guard_history(fixture, tmp_path, monkeypatch)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    wake = Mock(side_effect=AssertionError("metadata cannot wake control"))
    monkeypatch.setattr(bounded, "_wake_for_confirmation", wake)
    monkeypatch.setattr(shared, "esp32", {"client": Mock(spec=[])})
    complete = await bounded.completion_observation(fixture, tmp_path, done, 10)
    assert complete is not None
    state = bounded._read(tmp_path / bounded.STATE_NAME)
    assert complete["completed_count"] == len(history) == 23
    assert complete["supersedes"] == approval["confirmed_halt_link"]
    assert complete["handoff_settled"] is True
    assert complete["state_sha256"] == bounded._digest(state)
    assert complete["effects_sha256"] == bounded._digest(
        {"completed": state["completed"], "values": state["completed_values"], "ordinary_handoff": []}
    )
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before
    wake.assert_not_called()
    assert shared.esp32["client"].mock_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["fixed", "not_planned", "not_read_back", "latest_value_keys"])
async def test_guardrail_reapplication_observation_rejects_unbound_repetitions(fixture, tmp_path, monkeypatch, damage):
    await completed_reapplied_guard_history(fixture, tmp_path, monkeypatch)
    state = bounded._read(tmp_path / bounded.STATE_NAME)
    if damage == "fixed":
        state["completed"].append("cool_exit_hysteresis_f")
    elif damage == "not_planned":
        state["approved_preview"]["plan_rows"] = [
            row for row in state["approved_preview"]["plan_rows"] if row["parameter"] != "fog_escalation_kpa"
        ]
    elif damage == "not_read_back":
        del state["approved_preview"]["readbacks"]["fog_escalation_kpa"]
    else:
        del state["completed_values"]["fog_escalation_kpa"]
    # Exercise formatter eligibility itself, rather than only a changed hash.
    approved = state["approved_preview"]
    approved["fingerprint"] = bounded._digest(
        {
            k: approved[k]
            for k in ("session_id", "pod", "source_revision", "generation", "changes", "plan_rows", "readbacks")
        }
    )
    with pytest.raises(ValueError, match="observation run binding unavailable"):
        bounded._observation(state, tmp_path, 10, "complete", 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["binding", "process", "final_value", "unrecorded", "unconfirmed"])
async def test_guardrail_reapplication_completion_retains_settlement_boundaries(fixture, tmp_path, monkeypatch, damage):
    done, _approval, _history = await completed_reapplied_guard_history(fixture, tmp_path, monkeypatch)
    parameter = "vpd_target_south"
    native = None
    if damage == "binding":
        state = bounded._read(tmp_path / bounded.STATE_NAME)
        state["approved_preview"]["plan_rows"][0]["plan_id"] = "changed-plan"
        bounded._write(tmp_path / bounded.STATE_NAME, state)
    elif damage == "process":
        monkeypatch.setattr(bounded, "SESSION_ID", uuid.uuid4().hex)
    elif damage == "final_value":
        fixture.readbacks["fog_escalation_kpa"] = 0.0
    else:
        handoff = bounded.Decision("complete", ((parameter, 1.2),), run_id=done.run_id)
        bounded.note_ordinary_handoff(handoff, 10)
        if damage == "unconfirmed":
            record = {**records(handoff.changes)[0], "source": "planner"}
            bounded.record_ordinary_handoff(handoff, 10, [record], [])
            native = {
                "value": 1.2,
                "source": "planner",
                "delivery_status": "sent",
                "confirmed_at": None,
                "expired_at": None,
            }
            fixture.rows[(record["requested_at"], parameter)] = native
            fixture.readbacks[parameter] = 1.2
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    assert await bounded.completion_observation(fixture, tmp_path, done, 10) is None
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before
    if native is not None:
        native.update(delivery_status="confirmed", confirmed_at=datetime.now(UTC))
        complete = await bounded.completion_observation(fixture, tmp_path, done, 10)
        assert complete["handoff_requested_count"] == complete["handoff_confirmed_count"] == 1
