"""Original source epochs cannot be replaced by flushes or other generations."""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ingestor"))
from tasks.c1_capture import BAND_SLUGS, NativeCapture

from verdify_schemas.component_executor import CANONICAL_FIELD_ORDER
from verdify_schemas.tunable_registry import REGISTRY


def collector():
    c = NativeCapture()
    c.configure({"request_id": str(uuid4())}, runtime=str(uuid4()), generation=3, entities=[])
    return c


def fill(c, moment, *, generation=3, exclude=None):
    for name in CANONICAL_FIELD_ORDER:
        if name != exclude:
            c.record(
                REGISTRY[name].cfg_readback_object_id, REGISTRY[name].default, observed_at=moment, generation=generation
            )


def test_full48_preserves_original_times_and_two_distinct_epochs():
    c = collector()
    moment = datetime.now(UTC)
    fill(c, moment)
    first = c.epochs[0]
    assert len(first["observed_components"]) == 48
    assert {r["observed_at"] for r in first["observed_components"].values()} == {moment.isoformat()}
    fill(c, moment)  # cached/same timestamp cannot advance an epoch
    assert len(c.epochs) == 1
    fill(c, moment + timedelta(seconds=30))
    assert len(c.epochs) == 2
    assert c.epochs[0]["source_epoch_id"] != c.epochs[1]["source_epoch_id"]
    assert c.epochs[0] == first


def test_missing_field_and_old_socket_cannot_complete_epoch():
    c = collector()
    moment = datetime.now(UTC)
    missing = CANONICAL_FIELD_ORDER[-1]
    fill(c, moment, exclude=missing)
    assert not c.epochs
    c.record(REGISTRY[missing].cfg_readback_object_id, REGISTRY[missing].default, observed_at=moment, generation=2)
    assert not c.epochs
    c.configure({"request_id": c.identity[0]}, runtime=c.identity[1], generation=4, entities=[])
    c.record(REGISTRY[missing].cfg_readback_object_id, REGISTRY[missing].default, observed_at=moment, generation=4)
    assert not c.epochs  # no partial old generation survived


def test_reset_invalidates_epoch_and_marks_new_source_receipt():
    c = collector()
    moment = datetime.now(UTC)
    c.record("uptime_s", 100, observed_at=moment, generation=3)
    fill(c, moment)
    c.record("uptime_s", 1, observed_at=moment + timedelta(seconds=1), generation=3)
    assert not c.epochs
    fill(c, moment + timedelta(seconds=2))
    assert c.epochs[0]["reset_detected"] is True


def test_offgrid_readback_is_retained_as_raw_evidence_not_normalized():
    c = collector()
    moment = datetime.now(UTC)
    name = "min_fog_on_s"
    fill(c, moment, exclude=name)
    c.record(REGISTRY[name].cfg_readback_object_id, 57.0, observed_at=moment, generation=3)
    assert c.epochs[0]["observed_components"][name]["value"] == 57.0


def test_band_sample_marker_does_not_retime_cached_computation():
    c = collector()
    moment = datetime.now(UTC)
    for slug in BAND_SLUGS.values():
        c.record(slug, 1.0, observed_at=moment, generation=3)
    c.record("band_source", "onchip_curve", observed_at=moment, generation=3)
    sample = str(int((moment - timedelta(minutes=5)).timestamp()))
    c.record("consumed_band_sample_epoch", sample, observed_at=moment, generation=3)
    fill(c, moment)
    assert c.epochs[0]["band_callbacks"]["consumed_band_sample_epoch"]["value"] == sample
    assert c.epochs[0]["band_callbacks"]["consumed_band_sample_epoch"]["observed_at"] == moment.isoformat()


def test_cached_state_replay_invalidates_entire_request(monkeypatch):
    import tasks.c1_capture as module

    c = collector()
    monkeypatch.setattr(module, "COLLECTOR", c)
    moment = datetime.now(UTC)
    fill(c, moment)
    assert c.epochs
    module.invalidate_for_cached_replay()
    assert not c.epochs
    assert c.blocked_reason == "cached_state_replay_during_capture"
    fill(c, moment + timedelta(seconds=30))
    assert not c.epochs
    c.configure({"request_id": c.identity[0]}, runtime=c.identity[1], generation=3, entities=[])
    assert c.blocked_reason  # scheduler cannot rearm the same request
    c.configure({"request_id": str(uuid4())}, runtime=c.identity[1], generation=3, entities=[])
    fill(c, moment + timedelta(seconds=60))
    assert len(c.epochs) == 1  # new reviewed request, naturally published values


def test_invalid_device_sample_and_callback_clocks_are_unavailable():
    import copy

    import pytest
    from tasks.c1_capture import _validated_band_sample

    now = datetime.now(UTC)
    valid = {
        "completed_at": now.isoformat(),
        "band_callbacks": {
            "consumed_band_sample_epoch": {"value": str(int(now.timestamp())), "observed_at": now.isoformat()},
            "consumed_temp_low_f": {"value": 70.0, "observed_at": now.isoformat()},
        },
    }
    assert _validated_band_sample(valid, now) <= now
    for marker in [
        "",
        "nan",
        "-1",
        "4294967296",
        str(int((now + timedelta(minutes=1)).timestamp())),
        str(int((now - timedelta(minutes=16)).timestamp())),
        None,
        1,
    ]:
        bad = copy.deepcopy(valid)
        bad["band_callbacks"]["consumed_band_sample_epoch"]["value"] = marker
        with pytest.raises(ValueError):
            _validated_band_sample(bad, now)
    for timestamp in [
        "invalid",
        now.replace(tzinfo=None).isoformat(),
        (now + timedelta(seconds=1)).isoformat(),
        (now - timedelta(minutes=16)).isoformat(),
    ]:
        bad = copy.deepcopy(valid)
        bad["band_callbacks"]["consumed_temp_low_f"]["observed_at"] = timestamp
        with pytest.raises(ValueError):
            _validated_band_sample(bad, now)
    assert valid["band_callbacks"]["consumed_temp_low_f"]["value"] == 70.0
