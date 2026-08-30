"""Fault-injection proof for the bounded sole-writer delivery queue (#433)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

INGESTOR_PATH = str(Path(__file__).resolve().parents[1] / "ingestor")
if INGESTOR_PATH not in sys.path:
    sys.path.insert(0, INGESTOR_PATH)

import esp32_push  # noqa: E402
import shared  # noqa: E402


@pytest.fixture(autouse=True)
def _writer_state(monkeypatch):
    monkeypatch.setenv("VERDIFY_DEVICE_WRITE_ENABLED", "1")
    monkeypatch.setattr(shared, "is_shadow_mode", lambda: False)
    monkeypatch.setattr(shared, "writer_lease_held", lambda: True)
    monkeypatch.setattr(shared, "transport_generation", 4)
    monkeypatch.setattr(shared, "recently_pushed", {})
    monkeypatch.setattr(shared, "recently_pushed_values", {})
    monkeypatch.setattr(shared, "esp32", {"client": None, "keys": {}, "services": {}})
    monkeypatch.setattr(esp32_push, "_MIN_COMMAND_INTERVAL_S", 0.0)
    monkeypatch.setattr(esp32_push, "_BATCH_PAUSE_S", 0.0)
    monkeypatch.setattr(esp32_push, "_COMMAND_TIMEOUT_S", 0.2)
    monkeypatch.setattr(esp32_push, "_LIFECYCLE_CALLBACK_TIMEOUT_S", 0.2)
    monkeypatch.setattr(esp32_push, "_LIFECYCLE_RETRY_S", 0.0)
    monkeypatch.setattr(esp32_push, "_DEVICE_WRITE_DISABLED_LOGGED", False)
    monkeypatch.setattr(shared, "writer_fatal_event", asyncio.Event())
    monkeypatch.setattr(shared, "writer_fatal_reason", None)


def _install_number_client(command):
    client = MagicMock()
    client.number_command = command
    client.switch_command = MagicMock(return_value=None)
    shared.esp32["client"] = client
    shared.esp32["keys"] = {f"cmd_{index}": index for index in range(1, 130)} | {"urgent": 999}
    return client


@pytest.mark.asyncio
async def test_partial_delivery_records_only_returned_commands_as_sent():
    calls: list[int] = []

    async def command(key, _value):
        calls.append(key)
        if key == 2:
            raise ConnectionError("fault injection")

    _install_number_client(command)
    states: list[esp32_push.DeviceCommandOutcome] = []

    async def on_state(outcomes):
        states.extend(outcomes)

    result = await esp32_push.push_to_esp32_detailed(
        [("cmd_1", 1.0, "number"), ("cmd_2", 2.0, "number"), ("cmd_3", 3.0, "number")],
        on_state=on_state,
    )

    assert calls == [1, 2, 3]
    assert [outcome.status for outcome in result.outcomes] == ["sent", "failed", "sent"]
    assert result.sent_count == 2
    assert result.failed_count == 1
    assert shared.recently_pushed_values == {"cmd_1": 1.0, "cmd_3": 3.0}
    assert [outcome.status for outcome in states[:3]] == ["queued", "queued", "queued"]
    assert "sent" not in [outcome.status for outcome in states[:3]]


@pytest.mark.asyncio
async def test_lifecycle_callback_retries_before_physical_delivery_advances():
    calls: list[int] = []
    callback_states: list[tuple[str, ...]] = []
    callback_attempts = 0

    async def command(key, _value):
        calls.append(key)

    _install_number_client(command)

    async def on_state(outcomes):
        nonlocal callback_attempts
        callback_attempts += 1
        callback_states.append(tuple(outcome.status for outcome in outcomes))
        if callback_attempts <= 2:
            assert calls == [], "physical command escaped before queued state was durable"
            raise ConnectionError("transient lifecycle store fault")

    result = await esp32_push.push_to_esp32_detailed(
        [("cmd_1", 1.0, "number")],
        on_state=on_state,
    )

    assert result.sent_count == 1
    assert calls == [1]
    assert callback_states == [("queued",), ("queued",), ("queued",), ("sent",)]


@pytest.mark.asyncio
async def test_persistent_queued_lifecycle_failure_requests_restart_without_send(monkeypatch):
    calls: list[int] = []
    callback_attempts = 0

    async def command(key, _value):
        calls.append(key)

    _install_number_client(command)
    monkeypatch.setattr(esp32_push, "_LIFECYCLE_CALLBACK_ATTEMPTS", 2)

    async def on_state(_outcomes):
        nonlocal callback_attempts
        callback_attempts += 1
        raise ConnectionError("persistent lifecycle store fault")

    with pytest.raises(esp32_push.LifecyclePersistenceError):
        await esp32_push.push_to_esp32_detailed(
            [("cmd_1", 1.0, "number")],
            on_state=on_state,
        )

    assert callback_attempts == 2
    assert calls == []
    assert shared.writer_fatal_reason == "lifecycle_persistence_unavailable"
    assert shared.writer_fatal_event.is_set()


@pytest.mark.asyncio
async def test_terminal_lifecycle_failure_is_bounded_and_fail_closes_writer(monkeypatch):
    calls: list[int] = []
    terminal_callback_attempts = 0

    async def command(key, _value):
        calls.append(key)

    _install_number_client(command)
    monkeypatch.setattr(esp32_push, "_LIFECYCLE_CALLBACK_ATTEMPTS", 2)

    async def on_state(outcomes):
        nonlocal terminal_callback_attempts
        if all(outcome.status == "queued" for outcome in outcomes):
            return
        terminal_callback_attempts += 1
        raise ConnectionError("persistent lifecycle store fault")

    first = await esp32_push.push_to_esp32_detailed(
        [("cmd_1", 1.0, "number")],
        on_state=on_state,
    )
    blocked = await esp32_push.push_to_esp32_detailed([("cmd_2", 2.0, "number")])

    assert first.sent_count == 1
    assert first.fatal_error == "lifecycle_persistence_unavailable"
    assert terminal_callback_attempts == 2
    assert blocked.failed_count == 1
    assert blocked.outcomes[0].reason == "lifecycle_persistence_unavailable"
    assert calls == [1]
    assert shared.recently_pushed == {}
    assert shared.recently_pushed_values == {}
    assert shared.writer_fatal_event.is_set()


@pytest.mark.asyncio
async def test_hung_terminal_lifecycle_callback_times_out_and_fail_closes(monkeypatch):
    calls: list[int] = []
    terminal_attempts = 0

    async def command(key, _value):
        calls.append(key)

    _install_number_client(command)
    monkeypatch.setattr(esp32_push, "_LIFECYCLE_CALLBACK_ATTEMPTS", 2)
    monkeypatch.setattr(esp32_push, "_LIFECYCLE_CALLBACK_TIMEOUT_S", 0.01)

    async def on_state(outcomes):
        nonlocal terminal_attempts
        if all(outcome.status == "queued" for outcome in outcomes):
            return
        terminal_attempts += 1
        await asyncio.Event().wait()

    result = await asyncio.wait_for(
        esp32_push.push_to_esp32_detailed([("cmd_1", 1.0, "number")], on_state=on_state),
        timeout=0.1,
    )

    assert result.sent_count == 1
    assert result.fatal_error == "lifecycle_persistence_unavailable"
    assert terminal_attempts == 2
    assert calls == [1]
    assert shared.writer_fatal_event.is_set()


@pytest.mark.asyncio
async def test_cancel_during_queued_persistence_never_sends_or_strands_worker():
    queued_entered = asyncio.Event()
    terminal = asyncio.Event()
    calls: list[int] = []
    states: list[esp32_push.DeviceCommandOutcome] = []

    async def command(key, _value):
        calls.append(key)

    _install_number_client(command)

    async def on_state(outcomes):
        statuses = {outcome.status for outcome in outcomes}
        if statuses == {"queued"}:
            queued_entered.set()
            await asyncio.Event().wait()
        states.extend(outcomes)
        if "cancelled" in statuses:
            terminal.set()

    task = asyncio.create_task(
        esp32_push.push_to_esp32_detailed(
            [("cmd_1", 1.0, "number")],
            on_state=on_state,
        )
    )
    await asyncio.wait_for(queued_entered.wait(), timeout=0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.wait_for(terminal.wait(), timeout=0.2)

    assert calls == []
    assert [outcome.status for outcome in states] == ["cancelled"]


@pytest.mark.asyncio
async def test_cancellation_keeps_inflight_truth_and_cancels_every_unsent_command():
    entered = asyncio.Event()
    release = asyncio.Event()
    terminal = asyncio.Event()
    calls: list[int] = []
    states: list[esp32_push.DeviceCommandOutcome] = []

    async def command(key, _value):
        calls.append(key)
        entered.set()
        await release.wait()

    _install_number_client(command)

    async def on_state(outcomes):
        states.extend(outcomes)
        if sum(outcome.status in {"sent", "failed", "cancelled", "superseded"} for outcome in states) >= 3:
            terminal.set()

    task = asyncio.create_task(
        esp32_push.push_to_esp32_detailed(
            [("cmd_1", 1.0, "number"), ("cmd_2", 2.0, "number"), ("cmd_3", 3.0, "number")],
            on_state=on_state,
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    release.set()
    await asyncio.wait_for(terminal.wait(), timeout=0.2)

    assert calls == [1]
    terminal_states = [outcome.status for outcome in states if outcome.status != "queued"]
    assert terminal_states == ["sent", "cancelled", "cancelled"]
    assert shared.recently_pushed_values == {"cmd_1": 1.0}


@pytest.mark.asyncio
async def test_timeout_has_same_truthful_terminalization_as_explicit_cancel():
    entered = asyncio.Event()
    release = asyncio.Event()
    terminal = asyncio.Event()
    states: list[esp32_push.DeviceCommandOutcome] = []

    async def command(_key, _value):
        entered.set()
        await release.wait()

    _install_number_client(command)

    async def on_state(outcomes):
        states.extend(outcomes)
        if any(outcome.status == "cancelled" for outcome in states):
            terminal.set()

    task = asyncio.create_task(
        asyncio.wait_for(
            esp32_push.push_to_esp32_detailed(
                [("cmd_1", 1.0, "number"), ("cmd_2", 2.0, "number")],
                on_state=on_state,
            ),
            timeout=0.01,
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=0.2)
    with pytest.raises(TimeoutError):
        await task
    release.set()
    await asyncio.wait_for(terminal.wait(), timeout=0.2)

    terminal_states = [outcome.status for outcome in states if outcome.status != "queued"]
    assert terminal_states == ["sent", "cancelled"]


@pytest.mark.asyncio
async def test_physical_command_timeout_is_terminal_unknown_and_writer_recovers(monkeypatch):
    never_returns = asyncio.Event()
    calls: list[int] = []

    async def command(key, _value):
        calls.append(key)
        if key == 1:
            await never_returns.wait()

    _install_number_client(command)
    monkeypatch.setattr(esp32_push, "_COMMAND_TIMEOUT_S", 0.01)

    timed_out = await esp32_push.push_to_esp32_detailed([("cmd_1", 1.0, "number")])
    recovered = await esp32_push.push_to_esp32_detailed([("cmd_2", 2.0, "number")])

    assert timed_out.failed_count == 1
    assert timed_out.outcomes[0].reason == "command_timeout_outcome_unknown"
    assert recovered.sent_count == 1
    assert calls == [1, 2]


@pytest.mark.asyncio
async def test_queued_request_cannot_cross_transport_generation():
    entered = asyncio.Event()
    release = asyncio.Event()
    calls: list[int] = []

    async def command(key, _value):
        calls.append(key)
        if key == 1:
            entered.set()
            await release.wait()

    _install_number_client(command)
    task = asyncio.create_task(esp32_push.push_to_esp32_detailed([("cmd_1", 1.0, "number"), ("cmd_2", 2.0, "number")]))
    await asyncio.wait_for(entered.wait(), timeout=0.2)
    shared.transport_generation = 5
    release.set()
    result = await task

    assert calls == [1]
    assert [outcome.status for outcome in result.outcomes] == ["sent", "failed"]
    assert result.outcomes[1].reason == "transport_generation_changed"
    assert all(outcome.connection_generation == 4 for outcome in result.outcomes)


@pytest.mark.asyncio
async def test_stale_dispatch_generation_is_rejected_before_queue_or_send():
    calls: list[int] = []

    async def command(key, _value):
        calls.append(key)

    _install_number_client(command)
    result = await esp32_push.push_to_esp32_detailed(
        [("cmd_1", 1.0, "number")],
        expected_connection_generation=3,
    )

    assert calls == []
    assert result.failed_count == 1
    assert result.outcomes[0].reason == "transport_generation_changed"
    assert result.outcomes[0].connection_generation == 3


@pytest.mark.asyncio
async def test_lease_is_rechecked_after_pacing_inside_physical_lock(monkeypatch):
    pace_entered = asyncio.Event()
    release_pace = asyncio.Event()
    lease = {"held": True}
    calls: list[int] = []

    async def pace():
        pace_entered.set()
        await release_pace.wait()

    async def command(key, _value):
        calls.append(key)

    _install_number_client(command)
    monkeypatch.setattr(shared, "writer_lease_held", lambda: lease["held"])
    monkeypatch.setattr(esp32_push, "_pace_command", pace)
    task = asyncio.create_task(esp32_push.push_to_esp32_detailed([("cmd_1", 1.0, "number")]))
    await asyncio.wait_for(pace_entered.wait(), timeout=0.2)
    lease["held"] = False
    release_pace.set()
    result = await task

    assert calls == []
    assert result.failed_count == 1
    assert result.outcomes[0].reason == "writer_lease_not_held"


@pytest.mark.asyncio
async def test_round_robin_pause_lets_urgent_request_overtake_long_batch():
    calls: list[int] = []
    first_quantum = asyncio.Event()

    async def command(key, _value):
        calls.append(key)
        if len(calls) == 2:
            first_quantum.set()
        await asyncio.sleep(0)

    _install_number_client(command)
    esp32_push._BATCH_PAUSE_S = 0.02

    long_task = asyncio.create_task(
        esp32_push.push_to_esp32_detailed([(f"cmd_{index}", float(index), "number") for index in range(1, 7)])
    )
    await asyncio.wait_for(first_quantum.wait(), timeout=0.2)
    urgent_task = asyncio.create_task(esp32_push.push_to_esp32_detailed([("urgent", 1.0, "number")]))
    long_result, urgent_result = await asyncio.gather(long_task, urgent_task)

    assert long_result.sent_count == 6
    assert urgent_result.sent_count == 1
    assert calls[:3] == [1, 2, 999]


@pytest.mark.asyncio
async def test_queue_bound_fails_closed_without_sending_overflow(monkeypatch):
    first_entered = asyncio.Event()
    release = asyncio.Event()

    async def command(key, _value):
        if key == 1:
            first_entered.set()
            await release.wait()

    _install_number_client(command)
    monkeypatch.setattr(esp32_push, "_MAX_PENDING_REQUESTS", 2)

    first = asyncio.create_task(esp32_push.push_to_esp32_detailed([("cmd_1", 1.0, "number")]))
    await asyncio.wait_for(first_entered.wait(), timeout=0.2)
    second = asyncio.create_task(esp32_push.push_to_esp32_detailed([("cmd_2", 2.0, "number")]))
    await asyncio.sleep(0)
    overflow = await esp32_push.push_to_esp32_detailed([("cmd_3", 3.0, "number")])

    assert overflow.failed_count == 1
    assert overflow.outcomes[0].reason == "queue_full"
    release.set()
    first_result, second_result = await asyncio.gather(first, second)
    assert first_result.sent_count == second_result.sent_count == 1


@pytest.mark.asyncio
async def test_newer_request_supersedes_only_older_unsent_same_parameter():
    entered = asyncio.Event()
    release = asyncio.Event()
    calls: list[tuple[int, float]] = []

    async def command(key, value):
        calls.append((key, value))
        if key == 1:
            entered.set()
            await release.wait()

    _install_number_client(command)
    old = asyncio.create_task(esp32_push.push_to_esp32_detailed([("cmd_1", 1.0, "number"), ("cmd_2", 2.0, "number")]))
    await asyncio.wait_for(entered.wait(), timeout=0.2)
    new = asyncio.create_task(esp32_push.push_to_esp32_detailed([("cmd_2", 20.0, "number")]))
    await asyncio.sleep(0)
    release.set()
    old_result, new_result = await asyncio.gather(old, new)

    assert [outcome.status for outcome in old_result.outcomes] == ["sent", "superseded"]
    assert new_result.sent_count == 1
    assert calls == [(1, 1.0), (2, 20.0)]


@pytest.mark.asyncio
async def test_old_retry_cannot_overwrite_newer_logical_request():
    calls: list[float] = []

    async def command(_key, value):
        calls.append(value)
        if value == 1.0 and calls.count(1.0) == 1:
            raise ConnectionError("first old attempt fails")

    _install_number_client(command)
    first_old = await esp32_push.push_to_esp32_detailed(
        [("cmd_1", 1.0, "number")],
        command_versions=[1.0],
    )
    newer = await esp32_push.push_to_esp32_detailed(
        [("cmd_1", 2.0, "number")],
        command_versions=[2.0],
    )
    old_retry = await esp32_push.push_to_esp32_detailed(
        [("cmd_1", 1.0, "number")],
        attempt=2,
        command_versions=[1.0],
    )

    assert first_old.failed_count == 1
    assert newer.sent_count == 1
    assert old_retry.outcomes[0].status == "superseded"
    assert calls == [1.0, 2.0]


@pytest.mark.asyncio
async def test_default_and_db_tokens_share_one_ordering_domain():
    calls: list[float] = []

    async def command(_key, value):
        calls.append(value)

    _install_number_client(command)
    first_default = await esp32_push.push_to_esp32_detailed([("cmd_1", 1.0, "number")])
    db_request = await esp32_push.push_to_esp32_detailed(
        [("cmd_1", 2.0, "number")],
        command_versions=[1_752_100_000.0],
    )
    later_default = await esp32_push.push_to_esp32_detailed([("cmd_1", 3.0, "number")])
    stale_db_retry = await esp32_push.push_to_esp32_detailed(
        [("cmd_1", 2.0, "number")],
        attempt=2,
        command_versions=[1_752_100_000.0],
    )

    assert first_default.sent_count == db_request.sent_count == later_default.sent_count == 1
    assert stale_db_retry.outcomes[0].status == "superseded"
    assert calls == [1.0, 2.0, 3.0]


@pytest.mark.asyncio
async def test_real_long_batch_keeps_concurrent_heartbeat_below_twice_cadence(monkeypatch):
    async def command(_key, _value):
        await asyncio.sleep(0)

    _install_number_client(command)
    monkeypatch.setattr(esp32_push, "_BATCH_PAUSE_S", 0.01)
    cadence = 0.02
    starts: list[float] = []

    async def heartbeat():
        loop = asyncio.get_running_loop()
        target = loop.time()
        for _ in range(5):
            target += cadence
            await asyncio.sleep(max(0.0, target - loop.time()))
            starts.append(loop.time())

    long_batch = asyncio.create_task(
        esp32_push.push_to_esp32_detailed([(f"cmd_{index}", float(index), "number") for index in range(1, 13)])
    )
    await heartbeat()
    result = await long_batch

    observed_intervals = [later - earlier for earlier, later in zip(starts, starts[1:], strict=False)]
    assert result.sent_count == 12
    assert observed_intervals
    assert max(observed_intervals) < cadence * 2
