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


def qualified_collector(now):
    c = collector()
    c.request.update(
        qualification_worksheet_id=str(uuid4()),
        expires_at=(now + timedelta(minutes=5)).isoformat(),
        runtime_instance_id=c.identity[1],
        connection_generation=c.identity[2],
        source_revision="source-a",
    )
    worksheet = {
        "expires_at": c.request["expires_at"],
        "preview": {
            "identity": {
                key: c.request[key] for key in ("runtime_instance_id", "connection_generation", "source_revision")
            }
        },
    }
    return c, {
        "worksheet_id": c.request["qualification_worksheet_id"],
        "worksheet": worksheet,
        "status": "active",
        "validated_at": now.isoformat(),
    }


def test_unsettled_qualification_discards_all_epochs_and_resumes_fresh_only():
    from tasks.c1_capture import _qualification_ready

    now = datetime.now(UTC)
    c, q = qualified_collector(now)
    assert _qualification_ready(c, q, now)
    fill(c, now)
    assert c.epochs
    for status in ("inflight", "awaiting_confirmation"):
        q["status"] = status
        assert not _qualification_ready(c, q, now + timedelta(seconds=1))
        assert not c.epochs and not c.pending and not c.band
        fill(c, now + timedelta(seconds=2))
        assert not c.epochs
    q.update(status="active", validated_at=(now + timedelta(seconds=3)).isoformat())
    assert _qualification_ready(c, q, now + timedelta(seconds=3))
    fill(c, now + timedelta(seconds=2))  # a delayed pre-resume callback cannot count
    assert not c.pending
    fill(c, now + timedelta(seconds=4))
    fill(c, now + timedelta(seconds=34))
    assert len(c.epochs) == 2
    assert c.epochs[0]["completed_at"] == (now + timedelta(seconds=4)).isoformat()
    assert c.blocked_reason is None


def test_validation_age_pauses_without_extending_original_expiry():
    from tasks.c1_capture import _qualification_ready

    now = datetime.now(UTC)
    c, q = qualified_collector(now)
    assert _qualification_ready(c, q, now)
    fill(c, now)
    assert not _qualification_ready(c, q, now + timedelta(seconds=31))
    assert not c.epochs
    q["validated_at"] = (now + timedelta(seconds=32)).isoformat()
    assert _qualification_ready(c, q, now + timedelta(seconds=32))
    assert c.request["expires_at"] == (now + timedelta(minutes=5)).isoformat()
    assert not _qualification_ready(c, q, now + timedelta(minutes=5))
    q["validated_at"] = (now + timedelta(minutes=5)).isoformat()
    assert not _qualification_ready(c, q, now + timedelta(minutes=5))
    assert c.blocked_reason == "qualification authority expired"


def test_terminal_qualification_changes_cannot_resume_same_request():
    import copy

    from tasks.c1_capture import _qualification_ready

    now = datetime.now(UTC)
    for change in ("worksheet_id", "worksheet", "runtime", "yielded", "missing", "future_validation"):
        c, q = qualified_collector(now)
        original = copy.deepcopy(q)
        assert _qualification_ready(c, q, now)
        if change == "worksheet_id":
            q["worksheet_id"] = str(uuid4())
        elif change == "worksheet":
            q["worksheet"]["expires_at"] = (now + timedelta(minutes=6)).isoformat()
        elif change == "runtime":
            q["worksheet"]["preview"]["identity"]["connection_generation"] = 4
        elif change == "missing":
            q = None
        elif change == "future_validation":
            q["validated_at"] = (now + timedelta(seconds=1)).isoformat()
        else:
            q["status"] = "yielded"
        assert not _qualification_ready(c, q, now)
        if change == "future_validation":
            assert c.paused_reason  # future clock is unavailable, never credited
            continue
        assert c.blocked_reason
        assert not _qualification_ready(c, original, now)
        c.configure(dict(c.request), runtime=c.identity[1], generation=3, entities=[])
        assert c.blocked_reason


def test_callback_checks_live_authority_between_scheduler_calls(monkeypatch):
    import tasks.c1_capture as module

    now = datetime.now(UTC)
    c, q = qualified_collector(now)
    monkeypatch.setattr(module, "COLLECTOR", c)
    monkeypatch.setattr(module, "_current_qualification", lambda: q)
    monkeypatch.setattr(module.shared, "transport_generation", 3)
    monkeypatch.setattr(module.shared, "writer_lease_strictly_held", lambda: True)
    monkeypatch.setattr(module.shared, "transport_readbacks_ready", lambda generation: True)
    assert module._qualification_ready(c, q, now)
    name = CANONICAL_FIELD_ORDER[0]
    slug = REGISTRY[name].cfg_readback_object_id
    module.record_native_callback(slug, REGISTRY[name].default, observed_at=now, generation=3)
    assert c.pending
    q["status"] = "awaiting_confirmation"
    module.record_native_callback(slug, REGISTRY[name].default, observed_at=now, generation=3)
    assert not c.pending
    q.update(status="active", validated_at=datetime.now(UTC).isoformat())
    module.record_native_callback(slug, REGISTRY[name].default, observed_at=now, generation=3)
    assert not c.pending  # original pre-resume timestamp remains rejected
    module.record_native_callback(slug, REGISTRY[name].default, observed_at=datetime.now(UTC), generation=3)
    assert c.pending
    module.invalidate_for_cached_replay()
    assert not module._qualification_ready(c, q, datetime.now(UTC))
    assert c.blocked_reason == "cached_state_replay_during_capture"


def test_unobserved_delivery_stage_discards_prior_active_interval():
    from tasks.c1_capture import _qualification_ready

    now = datetime.now(UTC)
    c, q = qualified_collector(now)
    assert _qualification_ready(c, q, now)
    fill(c, now)
    assert c.epochs
    q.update(
        stage_started_at=(now + timedelta(seconds=1)).isoformat(), validated_at=(now + timedelta(seconds=2)).isoformat()
    )
    assert _qualification_ready(c, q, now + timedelta(seconds=2))
    assert not c.epochs  # a stage happened between collector invocations
    fill(c, now + timedelta(seconds=1))
    assert not c.pending


def test_reset_during_pause_remains_disqualifying_after_resume():
    from tasks.c1_capture import _qualification_ready

    now = datetime.now(UTC)
    c, q = qualified_collector(now)
    assert _qualification_ready(c, q, now)
    c.record("uptime_s", 100, observed_at=now, generation=3)
    q["status"] = "awaiting_confirmation"
    assert not _qualification_ready(c, q, now)
    c.record("uptime_s", 1, observed_at=now + timedelta(seconds=1), generation=3)
    q.update(status="active", validated_at=(now + timedelta(seconds=2)).isoformat())
    assert _qualification_ready(c, q, now + timedelta(seconds=2))
    fill(c, now + timedelta(seconds=3))
    assert c.epochs[0]["reset_detected"] is True


def test_same_request_cannot_rebind_runtime_or_extend_expiry():
    from tasks.c1_capture import _qualification_ready

    now = datetime.now(UTC)
    for change in ("runtime", "expiry"):
        c, q = qualified_collector(now)
        assert _qualification_ready(c, q, now)
        request = dict(c.request)
        if change == "expiry":
            request["expires_at"] = (now + timedelta(minutes=6)).isoformat()
        c.configure(request, runtime=c.identity[1], generation=4 if change == "runtime" else 3, entities=[])
        assert c.blocked_reason == "capture request or runtime changed"
        assert not _qualification_ready(c, q, now)


def test_stage_change_during_native_db_read_cannot_publish_prior_epoch(monkeypatch, tmp_path):
    import asyncio
    import json
    from types import SimpleNamespace

    import tasks.c1_capture as module
    import tasks.component_experiment as component

    now = datetime.now(UTC)
    c, q = qualified_collector(now)
    c.request["schema"] = "verdify-c1-native-capture-request-v1"
    assert module._qualification_ready(c, q, now)
    moment = datetime.now(UTC)
    for slug in BAND_SLUGS.values():
        c.record(slug, 1.0, observed_at=moment, generation=3)
    c.record("band_source", "onchip_curve", observed_at=moment, generation=3)
    c.record("consumed_band_sample_epoch", str(int(moment.timestamp())), observed_at=moment, generation=3)
    c.record("firmware_version", "fw-a", observed_at=moment, generation=3)
    fill(c, moment)
    assert len(c.epochs) == 1
    epoch_id = c.epochs[0]["source_epoch_id"]
    (tmp_path / module.REQUEST_NAME).write_text(json.dumps(c.request))
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.setenv("VERDIFY_GIT_SHA", "source-a")
    monkeypatch.setattr(module, "COLLECTOR", c)
    monkeypatch.setattr(module, "_current_qualification", lambda: q)
    monkeypatch.setattr(module, "_source_revisions", lambda: {})
    monkeypatch.setattr(component, "RUNTIME_INSTANCE_ID", c.identity[1])
    monkeypatch.setattr(component, "_component_grid_inventory", [])
    monkeypatch.setattr(
        component, "component_entity_grid_attestation", lambda: SimpleNamespace(firmware_revision="fw-a")
    )
    monkeypatch.setattr(module.shared, "transport_generation", 3)
    monkeypatch.setattr(module.shared, "writer_lease_strictly_held", lambda: True)
    monkeypatch.setattr(module.shared, "transport_readbacks_ready", lambda generation: True)
    client = object()
    monkeypatch.setitem(module.shared.esp32, "client", client)
    monkeypatch.setitem(module.shared.esp32, "state_subscription_client", client)

    class Connection:
        def transaction(self, **kwargs):
            assert kwargs == {"readonly": True}
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def fetchrow(self, *args):
            q.update(stage_started_at=datetime.now(UTC).isoformat(), validated_at=datetime.now(UTC).isoformat())
            return dict.fromkeys(BAND_SLUGS, 1.0)

        async def fetchval(self, *args):
            return "resolver-a"

    pool = SimpleNamespace(acquire=lambda: Connection())
    asyncio.run(module.capture_native_source(pool))
    output = tmp_path / "c1-capture" / c.request["request_id"]
    assert (output / (epoch_id + ".native.json")).exists()  # original unqualified truth retained
    assert not (output / (epoch_id + ".input.json")).exists()
    assert not c.epochs
