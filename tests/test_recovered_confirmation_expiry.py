"""Recovered sent-window closure preserves transport truth across writer rollover."""

import copy
import importlib.util
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from test_bounded_reconcile import ReadOnlyFixture, bounded, shared

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "prepare_custody", ROOT / "scripts/prepare-recovered-confirmation-custody.py"
)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


class Database(ReadOnlyFixture):
    def __init__(self):
        super().__init__([])
        self.original = []
        self.rollback = []
        self.updated = []
        self.cas_fail = False
        self.fail_postcommit_readback = False
        self.heap = {
            "ts": datetime.now(UTC),
            "heap_bytes": 35.0,
            "heap_largest_free_block_kb": 18.0,
            "firmware_version": "same-firmware",
        }

    @asynccontextmanager
    async def transaction(self):
        before = copy.deepcopy(self.original)
        try:
            yield
        except BaseException:
            self.original = before
            raise

    async def fetch(self, sql, *args):
        if "FROM setpoint_snapshot" in sql:
            if self.ts < datetime.now(UTC) - timedelta(seconds=120):
                return []
            return await super().fetch(sql, *args)
        if "ts > $2" in sql:
            return self.rollback
        if "FROM setpoint_changes" in sql or "FROM v_runtime_setpoint_changes_write" in sql:
            if (
                self.fail_postcommit_readback
                and "FOR UPDATE" not in sql
                and any(r["delivery_status"] == "expired" for r in self.original)
            ):
                raise RuntimeError("simulated lost postcommit readback")
            return copy.deepcopy(self.original)
        raise AssertionError(sql)

    async def fetchrow(self, sql, *args):
        if "FROM diagnostics" in sql:
            return self.heap
        if sql.startswith("UPDATE"):
            assert "expired_at = clock_timestamp()" in sql
            assert "confirmed_at IS NULL AND expired_at IS NULL" in sql
            assert "confirmed_at =" not in sql
            if self.cas_fail and len(self.updated) == 1:
                return None
            row = next(r for r in self.original if (r["ts"], r["parameter"], r["value"]) == args)
            self.updated.append(copy.deepcopy(row))
            row["delivery_status"] = "expired"
            row["expired_at"] = datetime.now(UTC)
            # Simulate actual native DB transition time, not confirmation deadline.
            return copy.deepcopy(row)

        return next((r for r in self.rollback if (r["ts"], r["parameter"]) == args), None)


def setup_case(tmp_path, monkeypatch):
    db = Database()
    now = datetime.now(UTC)
    start = now - timedelta(minutes=30)
    params = [
        "direct_wet_center_drydown_before_off_min",
        "safety_max",
        "safety_min",
        "cold_vent_guard_delta_f",
        "cool_exit_hysteresis_f",
        "cool_stage2_over_high_f",
        "direct_wet_stress_vpd_margin_kpa",
        "min_fog_on_s",
        "direct_wet_south_drydown_before_off_min",
        "direct_wet_west_start_offset_min",
        "direct_wet_west_drydown_before_off_min",
        "direct_wet_center_start_offset_min",
    ]
    values = [180.0, 100.0, 40.0, 9.0, 2.14, 2.01, 0.2, 39.0, 120.0, 60.0, 120.0, 120.0]
    db.readbacks.update(
        dict(zip(params, [0.0, 95.0, 45.0, 10.0, 1.5, 1.0, 0.05, 60.0, 0.0, 0.0, 0.0, 0.0], strict=True))
    )
    oldsession = "old-session"
    newsession = "new-session"
    original = [
        {
            "ts": start + timedelta(microseconds=i),
            "parameter": p,
            "value": values[i],
            "source": "plan" if p == "cold_vent_guard_delta_f" else "band",
            "delivery_status": "sent" if i < 4 else "failed",
            "confirmed_at": None,
            "expired_at": None,
        }
        for i, p in enumerate(params)
    ]
    rollback = [
        {
            "ts": now - timedelta(minutes=2, microseconds=i),
            "parameter": p,
            "value": 0.0,
            "source": "manual",
            "delivery_status": "confirmed",
            "confirmed_at": now - timedelta(minutes=1),
        }
        for i, p in enumerate(params[8:12])
    ]
    state = {
        "status": "rollback_complete",
        "run_id": "27a4e367aa9d4773b2581757d22963a8",
        "stage_started_at": start.isoformat(),
        "stage_parameters": params,
        "completed_values": {"gl_grow_dli_target": 21.0},
        "approved_preview": {"source_revision": "old-971", "pod": "old-pod", "readbacks": dict(db.readbacks)},
        "rollback_records": [
            {"parameter": r["parameter"], "value": r["value"], "requested_at": r["ts"].isoformat()} for r in rollback
        ],
    }
    db.readbacks["gl_grow_dli_target"] = 21.0
    recovery = {
        "source_run_id": state["run_id"],
        "parameters": params,
        "session_id": oldsession,
        "generation": 3,
        "approved_at": (now - timedelta(minutes=4)).isoformat(),
    }
    approval = {"run_id": state["run_id"]}
    for name, value in [
        (bounded.STATE_NAME, state),
        (bounded.APPROVAL_NAME, approval),
        (bounded.RECOVERY_NAME, recovery),
    ]:
        bounded._write(tmp_path / name, value)
    proof = {
        "run_id": state["run_id"],
        "status": "rollback_complete",
        "source": "old-971",
        "session": oldsession,
        "generation": 3,
        "diagnostics": {"firmware_version": "same-firmware"},
        "stage12_baseline": [{"parameter": p, "matches": True} for p in params],
        "first12_preserved": [{"parameter": "gl_grow_dli_target", "matches": True}],
    }
    (tmp_path / "proof.json").write_text(json.dumps(proof))

    def serialize(rows):
        return [{k: v.isoformat() if isinstance(v, datetime) else v for k, v in r.items()} for r in rows]

    (tmp_path / "requests.json").write_text(
        json.dumps({"original_requests": serialize(original), "rollback_confirmations": serialize(rollback)})
    )
    custody = tool.prepare(
        tmp_path / bounded.STATE_NAME,
        tmp_path / bounded.APPROVAL_NAME,
        tmp_path / bounded.RECOVERY_NAME,
        tmp_path / "proof.json",
        tmp_path / "requests.json",
    )
    bounded._write(tmp_path / "writer-stage-recovery-custody.json", custody)
    db.original = original
    db.rollback = rollback
    preview = {
        "source_revision": "new-source",
        "pod": "new-pod",
        "generation": 4,
        "session_id": newsession,
        "captured_at": now.isoformat(),
        "readbacks": dict(db.readbacks),
    }
    monkeypatch.setattr(bounded, "SESSION_ID", newsession)
    monkeypatch.setattr(shared, "transport_readbacks_ready", lambda g: g == 4)
    monkeypatch.setattr(shared, "current_cfg_readbacks", lambda g: dict(db.readbacks))
    monkeypatch.setattr(shared, "writer_lease_strictly_held", lambda minimum_remaining_s=0: True)
    return db, state, recovery, preview


@pytest.mark.asyncio
async def test_exact_recovered_stage_expiry_after_rollover_preserves_null_confirmation_and_sent_receipt(
    tmp_path, monkeypatch
):
    db, state, recovery, preview = setup_case(tmp_path, monkeypatch)
    rows = copy.deepcopy(db.original)
    result = await bounded._expire_recovered_confirmation_window(db, preview, state, recovery, rows, tmp_path)
    assert [r["delivery_status"] for r in result] == ["expired"] * 4 + ["failed"] * 8
    assert len(db.updated) == 4
    assert all(r["confirmed_at"] is None for r in db.original)
    assert [r["ts"] for r in db.original] == [r["ts"] for r in rows]
    receipt = bounded._read(tmp_path / f"writer-stage-expired-window-{state['run_id']}.json")
    assert [r["delivery_status"] for r in receipt["original_requests"]] == ["sent"] * 4 + ["failed"] * 8
    assert (
        receipt["confirmation_deadline_at"]
        == (datetime.fromisoformat(state["stage_started_at"]) + timedelta(minutes=8)).isoformat()
    )
    assert "physical dispatch outcome remains unknown" in receipt["meaning"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "defect",
    [
        "inflight",
        "custody_missing",
        "custody_changed",
        "source_changed",
        "firmware_changed",
        "stale48",
        "incomplete48",
        "cfg_changed",
        "first_stage_changed",
        "lease",
        "low_free",
        "small_block",
        "stale_diag",
        "rollback_unconfirmed",
        "later_unknown",
        "window_open",
        "cas_failure",
    ],
)
async def test_no_expiry_on_unsafe_or_uncertain_recovered_custody(tmp_path, monkeypatch, defect):
    db, state, recovery, preview = setup_case(tmp_path, monkeypatch)
    original = copy.deepcopy(db.original)
    if defect == "inflight":
        state["status"] = "rollback_inflight"
    if defect == "custody_missing":
        (tmp_path / "writer-stage-recovery-custody.json").unlink()
    if defect == "custody_changed":
        (tmp_path / bounded.RECOVERY_NAME).write_text("{}")
    if defect == "source_changed":
        state["approved_preview"]["source_revision"] = "different-original"
    if defect == "firmware_changed":
        db.heap["firmware_version"] = "different-firmware"
    if defect == "stale48":
        db.ts = datetime.now(UTC) - timedelta(minutes=10)

    if defect == "incomplete48":
        db.names.pop()
    if defect == "cfg_changed":
        db.readbacks[state["stage_parameters"][0]] = 5.0
    if defect == "first_stage_changed":
        db.readbacks[next(iter(state["completed_values"]))] = 0.0
    if defect == "lease":
        monkeypatch.setattr(shared, "writer_lease_strictly_held", lambda minimum_remaining_s=0: False)
    if defect == "low_free":
        db.heap["heap_bytes"] = 29.9
    if defect == "small_block":
        db.heap["heap_largest_free_block_kb"] = 17.9
    if defect == "stale_diag":
        db.heap["ts"] = datetime.now(UTC) - timedelta(minutes=3)
    if defect == "rollback_unconfirmed":
        db.rollback[0]["confirmed_at"] = None
    if defect == "later_unknown":
        db.rollback.append({"delivery_status": "sent", "confirmed_at": None})
    if defect == "window_open":
        state["stage_started_at"] = datetime.now(UTC).isoformat()
    if defect == "cas_failure":
        db.cas_fail = True
    with pytest.raises((ValueError, KeyError)):
        await bounded._expire_recovered_confirmation_window(
            db, preview, state, recovery, copy.deepcopy(db.original), tmp_path
        )
    assert db.original == original


@pytest.mark.asyncio
async def test_native_archive_after_rollover_expires_exact_old_window_once_and_requires_new_approval(
    tmp_path, monkeypatch
):
    db, state, recovery, preview = setup_case(tmp_path, monkeypatch)
    approval = bounded._read(tmp_path / bounded.APPROVAL_NAME)
    admitted = await bounded._settled_recovery_archive(db, preview, state, approval, tmp_path)
    assert admitted is False  # This transition grants no new device authority.
    archive_path = tmp_path / f"writer-stage-recovered-{state['run_id']}.json"
    archive = bounded._read(archive_path)
    assert archive["state"] == state
    assert [r["delivery_status"] for r in archive["known_requests"]] == ["expired"] * 4 + ["failed"] * 8
    assert all(r["confirmed_at"] is None for r in archive["known_requests"])
    assert len(db.updated) == 4
    assert await bounded._settled_recovery_archive(db, preview, state, approval, tmp_path) is False
    assert bounded._read(archive_path) == archive and len(db.updated) == 4
    # Independently validated new approvals remain the existing choose_stage boundary.
    assert await bounded._settled_recovery_archive(db, preview, state, {"run_id": "new"}, tmp_path) is True


@pytest.mark.asyncio
async def test_aborted_cas_then_changed_generation_keeps_prior_receipt_and_retries_truthfully(tmp_path, monkeypatch):
    db, state, recovery, preview = setup_case(tmp_path, monkeypatch)
    original = copy.deepcopy(db.original)
    db.cas_fail = True
    with pytest.raises(ValueError, match="compare-and-set"):
        await bounded._expire_recovered_confirmation_window(
            db, preview, state, recovery, copy.deepcopy(db.original), tmp_path
        )
    assert db.original == original
    prior_path = tmp_path / f"writer-stage-expired-window-{state['run_id']}.json"
    prior_bytes = prior_path.read_bytes()
    db.cas_fail = False
    preview.update(generation=5, session_id="next-session", source_revision="next-source")
    monkeypatch.setattr(bounded, "SESSION_ID", "next-session")
    monkeypatch.setattr(shared, "transport_readbacks_ready", lambda g: g == 5)
    result = await bounded._expire_recovered_confirmation_window(
        db, preview, state, recovery, copy.deepcopy(db.original), tmp_path
    )
    assert prior_path.read_bytes() == prior_bytes
    assert len(list(tmp_path.glob("writer-stage-expiry-attempt-*.json"))) == 2
    assert all(r["confirmed_at"] is None for r in result)
    assert all(
        r["expired_at"] > datetime.fromisoformat(state["stage_started_at"]) + timedelta(minutes=8) for r in result[:4]
    )


@pytest.mark.asyncio
async def test_commit_then_lost_readback_recovers_archive_without_repeat_transition(tmp_path, monkeypatch):
    db, state, recovery, preview = setup_case(tmp_path, monkeypatch)
    db.fail_postcommit_readback = True
    with pytest.raises(RuntimeError, match="postcommit"):
        await bounded._expire_recovered_confirmation_window(
            db, preview, state, recovery, copy.deepcopy(db.original), tmp_path
        )
    assert len(db.updated) == 4 and all(r["delivery_status"] == "expired" for r in db.original[:4])
    actual_times = [r["expired_at"] for r in db.original[:4]]
    db.fail_postcommit_readback = False
    assert (
        await bounded._settled_recovery_archive(
            db, preview, state, bounded._read(tmp_path / bounded.APPROVAL_NAME), tmp_path
        )
        is False
    )
    archive = bounded._read(tmp_path / f"writer-stage-recovered-{state['run_id']}.json")
    assert [datetime.fromisoformat(r["expired_at"]) for r in archive["known_requests"][:4]] == actual_times
    assert len(db.updated) == 4


def test_prepare_custody_accepts_postgres_short_fraction_without_fabricating_time(tmp_path, monkeypatch):
    db, state, recovery, preview = setup_case(tmp_path, monkeypatch)
    request = json.loads((tmp_path / "requests.json").read_text())
    row = request["rollback_confirmations"][0]
    moment = datetime.fromisoformat(row["ts"]).replace(microsecond=852810)
    row["ts"] = moment.isoformat().replace(".852810", ".85281")
    state["rollback_records"][0]["requested_at"] = moment.isoformat()
    bounded._write(tmp_path / bounded.STATE_NAME, state)
    (tmp_path / "requests.json").write_text(json.dumps(request))
    prepared = tool.prepare(
        tmp_path / bounded.STATE_NAME,
        tmp_path / bounded.APPROVAL_NAME,
        tmp_path / bounded.RECOVERY_NAME,
        tmp_path / "proof.json",
        tmp_path / "requests.json",
    )
    assert prepared["rollback_confirmations"][0]["ts"] == moment.isoformat()


@pytest.mark.asyncio
async def test_legacy_v1_archive_projection_is_preserved_byte_for_byte_across_rollover(tmp_path, monkeypatch):
    db, state, recovery, preview = setup_case(tmp_path, monkeypatch)
    for row in db.original[:4]:
        row["delivery_status"] = "expired"
        row["expired_at"] = datetime.now(UTC)
    approval = bounded._read(tmp_path / bounded.APPROVAL_NAME)
    fields = ("ts", "parameter", "value", "delivery_status", "confirmed_at")
    old_archive = {
        "schema": "verdify-writer-stage-recovered-v1",
        "state": state,
        "approval": approval,
        "recovery": recovery,
        "settled_baseline": {p: preview["readbacks"][p] for p in state["stage_parameters"]},
        "known_requests": [
            {k: r[k].isoformat() if isinstance(r[k], datetime) else r[k] for k in fields} for r in db.original
        ],
    }
    path = tmp_path / f"writer-stage-recovered-{state['run_id']}.json"
    bounded._write(path, old_archive)
    prior = path.read_bytes()
    assert await bounded._settled_recovery_archive(db, preview, state, {"run_id": "new"}, tmp_path) is True
    assert path.read_bytes() == prior and not db.updated


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["nan", "unregistered", "out_of_bounds", "naive_timestamp", "source_missing"])
async def test_schema_invalid_original_request_never_reaches_dml(tmp_path, monkeypatch, defect):
    db, state, recovery, preview = setup_case(tmp_path, monkeypatch)
    row = db.original[0]
    if defect == "nan":
        row["value"] = float("nan")
    if defect == "unregistered":
        row["parameter"] = "not_registered"
    if defect == "out_of_bounds":
        row["value"] = 100000.0
    if defect == "naive_timestamp":
        row["ts"] = row["ts"].replace(tzinfo=None)
    if defect == "source_missing":
        row.pop("source")
    # The custody comparison itself may reject changed facts before the
    # schema boundary; direct validation separately proves the model rail.
    from verdify_schemas.setpoint import SetpointChange

    with pytest.raises((ValueError, KeyError)):
        SetpointChange.model_validate({**row, "source": row["source"], "delivery_status": "expired"})
    with pytest.raises((ValueError, KeyError)):
        await bounded._expire_recovered_confirmation_window(
            db, preview, state, recovery, copy.deepcopy(db.original), tmp_path
        )
    assert not db.updated
