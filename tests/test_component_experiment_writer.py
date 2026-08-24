"""Sole-writer and lifecycle fault tests for exclusive component bundles."""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

_INGESTOR_PATH = str(Path(__file__).resolve().parents[1] / "ingestor")
if _INGESTOR_PATH not in sys.path:
    sys.path.insert(0, _INGESTOR_PATH)

import esp32_push  # noqa: E402
import shared  # noqa: E402
from esp32_push import (  # noqa: E402
    ComponentBundleCall,
    LifecyclePersistenceError,
    component_authority_hold,
    push_component_bundle,
    push_to_esp32_detailed,
    set_component_authority_hold,
)


def call(parameter: str, object_id: str, value: float = 1.0) -> ComponentBundleCall:
    return ComponentBundleCall(parameter, object_id, value, "number")


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.fail_key: str | None = None
        self.block_key: str | None = None
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.after_call = None

    async def number_command(self, key, value):
        self.calls.append((key, value))
        if self.block_key == key:
            self.started.set()
            await self.release.wait()
        if self.fail_key == key:
            raise RuntimeError("injected")
        if self.after_call is not None:
            self.after_call(key)

    async def switch_command(self, key, value):
        self.calls.append((key, value))


@pytest.fixture(autouse=True)
def writer_state(monkeypatch):
    monkeypatch.setenv("VERDIFY_DEVICE_WRITE_ENABLED", "1")
    monkeypatch.setattr(shared, "is_shadow_mode", lambda: False)
    monkeypatch.setattr(shared, "writer_lease_held", lambda: True)
    monkeypatch.setattr(shared, "writer_lease_strictly_held", lambda minimum_remaining_s=0: True)
    monkeypatch.setattr(shared, "transport_generation", 7)
    monkeypatch.setattr(shared, "esp32", {"client": FakeClient(), "keys": {}, "services": {}})
    monkeypatch.setattr(esp32_push, "_pace_command", lambda: asyncio.sleep(0))
    monkeypatch.setattr(esp32_push, "_LIFECYCLE_RETRY_S", 0.0)
    set_component_authority_hold(False)
    yield
    set_component_authority_hold(False)


async def recorder(bucket, outcomes):
    bucket.extend(outcomes)


@pytest.mark.asyncio
async def test_fixed_prefix_is_exclusive_and_ordinary_writer_runs_only_after_bundle():
    client: FakeClient = shared.esp32["client"]
    client.block_key = "component_1"
    shared.esp32["keys"] = {
        "component_1": "component_1",
        "component_2": "component_2",
        "ordinary": "ordinary",
    }
    lifecycle = []
    bundle_task = asyncio.create_task(
        push_component_bundle(
            [call("field_1", "component_1"), call("field_2", "component_2")],
            on_state=lambda outcomes: recorder(lifecycle, outcomes),
            expected_writer_generation=31,
            expected_connection_generation=7,
        )
    )
    await client.started.wait()
    ordinary_task = asyncio.create_task(push_to_esp32_detailed([("ordinary", 2.0, "number")]))
    await asyncio.sleep(0)
    assert client.calls == [("component_1", 1.0)]
    client.release.set()
    bundle, ordinary = await asyncio.gather(bundle_task, ordinary_task)
    assert bundle.ok
    assert ordinary.sent_count == 1
    assert client.calls == [("component_1", 1.0), ("component_2", 1.0), ("ordinary", 2.0)]
    assert {outcome.writer_generation for outcome in lifecycle} == {31}
    assert {outcome.connection_generation for outcome in lifecycle} == {7}


@pytest.mark.asyncio
async def test_prefix_failure_records_sent_failed_cancelled_and_stops():
    client: FakeClient = shared.esp32["client"]
    client.fail_key = "component_2"
    shared.esp32["keys"] = {name: name for name in ("component_1", "component_2", "component_3")}
    lifecycle = []
    result = await push_component_bundle(
        [
            call("field_1", "component_1"),
            call("field_2", "component_2"),
            call("field_3", "component_3"),
        ],
        on_state=lambda outcomes: recorder(lifecycle, outcomes),
        expected_writer_generation=4,
        expected_connection_generation=7,
    )
    assert [outcome.status for outcome in result.outcomes] == ["sent", "failed", "cancelled"]
    assert result.outcomes[1].reason == "command_error:RuntimeError"
    assert client.calls == [("component_1", 1.0), ("component_2", 1.0)]
    assert [outcome.status for outcome in lifecycle] == [
        "requested",
        "requested",
        "requested",
        "queued",
        "sent",
        "queued",
        "failed",
        "cancelled",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_index", range(48))
async def test_every_full_recovery_failure_prefix_stops_without_interleave(failure_index: int):
    client: FakeClient = shared.esp32["client"]
    calls = [call(f"field_{index}", f"component_{index}") for index in range(48)]
    shared.esp32["keys"] = {item.object_id: item.object_id for item in calls}
    client.fail_key = f"component_{failure_index}"
    result = await push_component_bundle(
        calls,
        on_state=lambda outcomes: recorder([], outcomes),
        expected_writer_generation=4,
        expected_connection_generation=7,
    )
    assert [outcome.status for outcome in result.outcomes[:failure_index]] == ["sent"] * failure_index
    assert result.outcomes[failure_index].status == "failed"
    assert [outcome.status for outcome in result.outcomes[failure_index + 1 :]] == ["cancelled"] * (47 - failure_index)
    assert [key for key, _value in client.calls] == [f"component_{index}" for index in range(failure_index + 1)]


@pytest.mark.asyncio
async def test_timeout_is_unknown_and_never_advances_prefix(monkeypatch):
    client: FakeClient = shared.esp32["client"]
    client.block_key = "component_1"
    shared.esp32["keys"] = {"component_1": "component_1", "component_2": "component_2"}
    monkeypatch.setattr(esp32_push, "_COMMAND_TIMEOUT_S", 0.01)
    result = await push_component_bundle(
        [call("field_1", "component_1"), call("field_2", "component_2")],
        on_state=lambda outcomes: recorder([], outcomes),
        expected_writer_generation=4,
        expected_connection_generation=7,
    )
    assert [outcome.status for outcome in result.outcomes] == ["failed", "cancelled"]
    assert result.outcomes[0].reason == "command_timeout_outcome_unknown"
    assert client.calls == [("component_1", 1.0)]


@pytest.mark.asyncio
async def test_expired_work_deadline_fails_before_any_setter():
    client: FakeClient = shared.esp32["client"]
    shared.esp32["keys"] = {"component_1": "component_1", "component_2": "component_2"}
    result = await push_component_bundle(
        [call("field_1", "component_1"), call("field_2", "component_2")],
        on_state=lambda outcomes: recorder([], outcomes),
        expected_writer_generation=4,
        expected_connection_generation=7,
        work_deadline=datetime.now(UTC) - timedelta(seconds=1),
    )
    assert [outcome.status for outcome in result.outcomes] == ["failed", "cancelled"]
    assert result.outcomes[0].reason == "component_work_expired"
    assert client.calls == []


@pytest.mark.asyncio
async def test_authoritative_queued_fence_runs_after_pacing_and_before_setter(monkeypatch):
    client: FakeClient = shared.esp32["client"]
    shared.esp32["keys"] = {"component_1": "component_1"}
    events: list[str] = []

    async def paced() -> None:
        events.append("paced")

    async def superseded_after_pacing(outcomes) -> None:
        status = outcomes[0].status
        events.append(status)
        if status == "queued":
            raise RuntimeError("writer generation superseded")

    monkeypatch.setattr(esp32_push, "_pace_command", paced)
    monkeypatch.setattr(esp32_push, "_LIFECYCLE_CALLBACK_ATTEMPTS", 1)

    with pytest.raises(LifecyclePersistenceError):
        await push_component_bundle(
            [call("field_1", "component_1")],
            on_state=superseded_after_pacing,
            expected_writer_generation=4,
            expected_connection_generation=7,
        )

    assert events == ["requested", "paced", "queued"]
    assert client.calls == []


@pytest.mark.asyncio
async def test_lease_loss_and_reconnect_fence_each_remaining_prefix(monkeypatch):
    for fault in ("lease", "reconnect"):
        held = {"value": True}
        monkeypatch.setattr(shared, "writer_lease_held", lambda: held["value"])
        monkeypatch.setattr(
            shared,
            "writer_lease_strictly_held",
            lambda minimum_remaining_s=0: held["value"],
        )
        shared.transport_generation = 7
        client = FakeClient()
        shared.esp32 = {
            "client": client,
            "keys": {"component_1": "component_1", "component_2": "component_2"},
            "services": {},
        }

        def after_first(_key):
            if fault == "lease":
                held["value"] = False
            else:
                shared.transport_generation = 8

        client.after_call = after_first
        result = await push_component_bundle(
            [call("field_1", "component_1"), call("field_2", "component_2")],
            on_state=lambda outcomes: recorder([], outcomes),
            expected_writer_generation=9,
            expected_connection_generation=7,
        )
        assert result.outcomes[0].status == "failed"
        assert result.outcomes[0].reason == (
            "writer_lease_not_held_after_command_outcome_unknown"
            if fault == "lease"
            else "transport_generation_changed_after_command_outcome_unknown"
        )
        assert result.outcomes[1].status == "cancelled"


@pytest.mark.asyncio
async def test_queued_database_fence_latency_cannot_overrun_bundle_budget(monkeypatch):
    client: FakeClient = shared.esp32["client"]
    shared.esp32["keys"] = {"component_1": "component_1"}

    async def slow_queued(outcomes) -> None:
        if outcomes[0].status == "queued":
            await asyncio.sleep(0.02)

    monkeypatch.setattr(esp32_push, "_pace_command", lambda: asyncio.sleep(0))
    result = await push_component_bundle(
        [call("field_1", "component_1")],
        on_state=slow_queued,
        expected_writer_generation=4,
        expected_connection_generation=7,
        budget_s=0.001,
    )

    assert result.failure is not None
    assert result.failure.reason == "component_bundle_budget_exceeded"
    assert client.calls == []


@pytest.mark.asyncio
async def test_component_bundle_requires_strict_cross_pod_lease(monkeypatch):
    client: FakeClient = shared.esp32["client"]
    shared.esp32["keys"] = {"component_1": "component_1"}
    monkeypatch.setattr(shared, "writer_lease_strictly_held", lambda minimum_remaining_s=0: False)

    result = await push_component_bundle(
        [call("field_1", "component_1")],
        on_state=lambda outcomes: recorder([], outcomes),
        expected_writer_generation=4,
        expected_connection_generation=7,
    )

    assert result.failure is not None
    assert result.failure.reason == "writer_lease_not_held"
    assert client.calls == []


@pytest.mark.asyncio
async def test_queued_ordinary_write_rechecks_long_lived_hold_inside_lock():
    client: FakeClient = shared.esp32["client"]
    shared.esp32["keys"] = {"ordinary": "ordinary"}
    # Force the ordinary worker to pass its first hold check and wait at the
    # physical lock. Authority then arms before it acquires the lock.
    esp32_push._reset_queue_for_loop(asyncio.get_running_loop())
    await esp32_push._PUSH_LOCK.acquire()
    try:
        ordinary_task = asyncio.create_task(push_to_esp32_detailed([("ordinary", 2.0, "number")]))
        await asyncio.sleep(0)
        set_component_authority_hold(True, ["ordinary"])
    finally:
        esp32_push._PUSH_LOCK.release()
    ordinary = await ordinary_task
    assert ordinary.failed_count == 1
    assert ordinary.outcomes[0].reason == "component_authority_hold"
    assert client.calls == []
    assert component_authority_hold() == (True, frozenset({"ordinary"}))


@pytest.mark.asyncio
async def test_callback_db_failure_self_fences_and_does_not_starve_queued_writer(monkeypatch):
    client: FakeClient = shared.esp32["client"]
    shared.esp32["keys"] = {"component": "component", "ordinary": "ordinary"}
    monkeypatch.setattr(esp32_push, "_LIFECYCLE_CALLBACK_ATTEMPTS", 1)
    sent_callback_entered = asyncio.Event()
    release_callback = asyncio.Event()

    async def failing_callback(outcomes):
        if outcomes[0].status == "sent":
            sent_callback_entered.set()
            await release_callback.wait()
            raise RuntimeError("db outage")

    component_task = asyncio.create_task(
        push_component_bundle(
            [call("field", "component")],
            on_state=failing_callback,
            expected_writer_generation=3,
            expected_connection_generation=7,
        )
    )
    await sent_callback_entered.wait()
    ordinary_task = asyncio.create_task(push_to_esp32_detailed([("ordinary", 2.0, "number")]))
    release_callback.set()
    with pytest.raises(LifecyclePersistenceError):
        await asyncio.wait_for(component_task, timeout=1)
    ordinary = await asyncio.wait_for(ordinary_task, timeout=1)
    assert ordinary.fatal_error == "component_lifecycle_persistence_unavailable"
    assert client.calls == [("component", 1.0)]


@pytest.mark.asyncio
async def test_preflight_and_shape_failures_make_zero_device_calls():
    client: FakeClient = shared.esp32["client"]
    shared.esp32["keys"] = {"component": "component"}
    no_callback = await push_component_bundle(
        [call("field", "component")],
        on_state=None,
        expected_writer_generation=1,
        expected_connection_generation=7,
    )
    assert no_callback.failure.reason == "component_lifecycle_callback_required"
    duplicate = await push_component_bundle(
        [call("field", "component"), call("field", "component")],
        on_state=lambda outcomes: recorder([], outcomes),
        expected_writer_generation=1,
        expected_connection_generation=7,
    )
    assert duplicate.failure.reason == "duplicate_component_in_bundle"
    assert client.calls == []
