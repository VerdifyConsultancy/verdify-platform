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

    async def fetch(self, sql, *_args):
        if "FROM setpoint_snapshot" in sql:
            snapshot = self.snapshot_readbacks if self.snapshot_readbacks is not None else self.readbacks
            return [{"parameter": name, "value": snapshot[name], "ts": self.ts} for name in self.names]
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
        == quantized
    )
    assert (
        dispatcher._without_equivalent_duration_replays(
            quantized,
            readbacks,
            reconnect_pending=False,
            drift_pending=False,
            staged_state_exists=False,
        )
        == quantized
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
