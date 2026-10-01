"""Critical #433 staged writer boundary: approval, cap, confirmation, stop."""

from __future__ import annotations

import importlib.util
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
