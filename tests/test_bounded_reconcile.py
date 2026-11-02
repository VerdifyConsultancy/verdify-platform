"""Helpers kept in tasks.bounded_reconcile after plain convergence (VD-62).

Ordinary dispatch no longer stages (see tests/test_dispatcher_plain_convergence.py).
These cases cover the shared helpers the dispatcher, the C1 overlay and the
drift probe still use, and the absence of the retired ordinary staging.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ingestor"))

from tasks import bounded_reconcile as bounded  # noqa: E402
from tasks import dispatcher  # noqa: E402


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
    assert dispatcher._without_equivalent_duration_candidates(quantized + residual, readbacks) == residual
    # No redundant physical command after the cfg callback.
    assert dispatcher._without_equivalent_duration_candidates(quantized, readbacks) == []
    changed = dict(readbacks, min_fog_on_s=52.0)
    assert dispatcher._without_equivalent_duration_candidates(quantized, changed) == [("min_fog_on_s", 54.75)]
    missing = dict(readbacks)
    missing.pop("min_fog_on_s")  # stale generations are absent from current_cfg_readbacks
    assert ("min_fog_on_s", 54.75) in dispatcher._without_equivalent_duration_candidates(quantized, missing)


def test_ordinary_staging_entry_points_are_retired():
    for name in (
        "choose_stage",
        "finish_stage",
        "record_hold",
        "hold_observation",
        "completion_observation",
        "note_ordinary_handoff",
        "record_ordinary_handoff",
        "approval_file_stamp",
        "APPROVAL_NAME",
        "Decision",
    ):
        assert not hasattr(bounded, name), name
    assert not hasattr(dispatcher, "_choose_bounded_stage_if_required")
    assert not (ROOT / "scripts" / "prepare-writer-stage.py").exists()
    assert not hasattr(dispatcher.c1_overlay, "owns_bounded_stage")


def test_atomic_write_is_private_and_round_trips(tmp_path):
    path = tmp_path / "nested" / "receipt.json"
    bounded._write(path, {"b": 1, "a": [1.5]})
    assert bounded._read(path) == {"a": [1.5], "b": 1}
    assert path.stat().st_mode & 0o777 == 0o600
    assert bounded._read(tmp_path / "absent.json") is None
    (tmp_path / "list.json").write_text("[]")
    with pytest.raises(ValueError):
        bounded._read(tmp_path / "list.json")
    assert sorted(p.name for p in path.parent.iterdir()) == ["receipt.json"]  # no temporary left behind


class Rows:
    def __init__(self, rows):
        self.rows = rows

    async def fetchrow(self, sql, ts, parameter):
        assert "FROM setpoint_changes" in sql
        return self.rows.get((ts, parameter))


@pytest.mark.asyncio
async def test_check_records_confirms_waits_or_fails_closed(monkeypatch):
    wakes = []
    monkeypatch.setattr(bounded, "_wake_for_confirmation", lambda: wakes.append(True))
    started = datetime.now(UTC)
    record = {"parameter": "mister_vpd_weight", "value": 0.75, "requested_at": started.isoformat()}
    key = (started, "mister_vpd_weight")

    waiting = Rows({key: {"delivery_status": "sent", "confirmed_at": None}})
    assert await bounded._check_records(waiting, [record], {"mister_vpd_weight": 0.5}, started.isoformat()) is False
    assert wakes == [True]

    confirmed = Rows({key: {"delivery_status": "confirmed", "confirmed_at": started}})
    assert await bounded._check_records(confirmed, [record], {"mister_vpd_weight": 0.7501}, started.isoformat())
    with pytest.raises(ValueError, match="readback mismatch"):
        await bounded._check_records(confirmed, [record], {"mister_vpd_weight": 0.5}, started.isoformat())

    failed = Rows({key: {"delivery_status": "failed", "confirmed_at": None}})
    with pytest.raises(ValueError, match="delivery failed"):
        await bounded._check_records(failed, [record], {}, started.isoformat())
    late = (started - bounded.CONFIRM_DEADLINE - timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError, match="deadline"):
        await bounded._check_records(waiting, [record], {}, late)


@pytest.mark.asyncio
async def test_wake_for_confirmation_uses_the_existing_dispatch_event(monkeypatch):
    event = asyncio.Event()
    monkeypatch.setattr(bounded.shared, "setpoint_dispatch_requested", event)
    monkeypatch.setattr(bounded, "CONFIRM_RECHECK_SECONDS", 0.01)
    bounded._wake_for_confirmation()
    await asyncio.wait_for(event.wait(), timeout=1)
