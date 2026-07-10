"""Issue #433: transport-generation and periodic-scheduler contracts."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

INGESTOR_PATH = str(Path(__file__).resolve().parents[1] / "ingestor")
if INGESTOR_PATH not in sys.path:
    sys.path.insert(0, INGESTOR_PATH)

for key, value in {
    "DB_USER": "verdify-test",
    "DB_PASSWORD": "not-a-secret",
    "DB_HOST": "127.0.0.1",
    "DB_PORT": "5432",
    "DB_NAME": "verdify-test",
}.items():
    os.environ.setdefault(key, value)

import shared  # noqa: E402
from esp32_push import DeviceCommandOutcome, PushBatchResult  # noqa: E402

import ingestor  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_reconcile_state(monkeypatch):
    monkeypatch.setattr(shared, "transport_generation", 0)
    monkeypatch.setattr(shared, "reconciled_transport_generation", 0)
    monkeypatch.setattr(shared, "cfg_drift_versions", {})
    monkeypatch.setattr(shared, "_cfg_drift_version", 0)
    monkeypatch.setattr(shared, "force_setpoint_push", asyncio.Event())
    monkeypatch.setattr(shared, "setpoint_dispatch_requested", asyncio.Event())
    monkeypatch.setattr(shared, "setpoint_dispatch_lock", asyncio.Lock())
    monkeypatch.setattr(shared, "writer_fatal_event", asyncio.Event())
    monkeypatch.setattr(shared, "writer_fatal_reason", None)
    monkeypatch.setattr(shared, "cfg_readback", {})
    monkeypatch.setattr(ingestor.state, "cfg_readback", {})


def test_cfg_drift_never_advances_transport_generation(caplog):
    shared.transport_generation = 7
    shared.reconciled_transport_generation = 7
    wire_id = "cfg___mister_vpd_weight"

    assert ingestor._record_cfg_readback(wire_id, 42.0) is True
    with caplog.at_level(logging.INFO, logger="ingestor"):
        assert ingestor._record_cfg_readback(wire_id, 43.0) is True

    assert shared.transport_generation == 7
    assert shared.reconciled_transport_generation == 7
    assert not shared.force_setpoint_push.is_set()
    assert shared.setpoint_dispatch_requested.is_set()
    assert shared.cfg_drift_versions == {"mister_vpd_weight": 1}
    assert any("reason=cfg_drift" in record.getMessage() for record in caplog.records)


def test_dynamic_readback_only_context_never_wakes_writer_dispatch():
    wire_id = "cfg___outdoor_dewpoint___f_"

    assert ingestor._record_cfg_readback(wire_id, 42.0) is True
    assert ingestor._record_cfg_readback(wire_id, 43.0) is True

    assert shared.cfg_readback["outdoor_dewpoint_f"] == 43.0
    assert shared.cfg_drift_versions == {}
    assert not shared.setpoint_dispatch_requested.is_set()


def test_json_notify_parser_preserves_numeric_zero():
    assert ingestor._parse_setpoint_notification(
        '{"parameter":"sw_fsm_controller_enabled","value":0,"source":"operator"}'
    ) == ("sw_fsm_controller_enabled", 0.0, "operator")


@pytest.mark.asyncio
async def test_notify_unknown_timeout_is_not_retried_and_opens_alert(monkeypatch):
    requested_at = datetime(2026, 7, 9, 20, 0, tzinfo=UTC)

    class Connection:
        def __init__(self):
            self.statuses: list[str] = []
            self.alerts: list[tuple] = []
            self.claim_query = ""

        async def fetchval(self, query, *args):
            if "WITH candidate AS" in query:
                self.claim_query = query
                return requested_at
            if "UPDATE setpoint_changes" in query:
                self.statuses.append(args[0])
                return args[0]
            if "SELECT min(ts)" in query:
                raise AssertionError("non-retryable timeout must not query/retry")
            if "SELECT delivery_status" in query:
                return self.statuses[-1] if self.statuses else "requested"
            return None

        async def execute(self, query, *args):
            if "INSERT INTO alert_log" in query:
                self.alerts.append(args)
            return "INSERT 0 1"

    class Acquire:
        def __init__(self, connection):
            self.connection = connection

        async def __aenter__(self):
            return self.connection

        async def __aexit__(self, *_exc):
            return False

    connection = Connection()
    pool = MagicMock()
    pool.acquire.return_value = Acquire(connection)
    push_calls = 0

    async def failed_push(_changes, *, attempt, on_state, command_versions):
        nonlocal push_calls
        push_calls += 1
        queued = DeviceCommandOutcome(
            0,
            "vpd_mister_weight",
            0.75,
            "number",
            "mister_vpd_weight",
            "queued",
            "bounded_queue_accepted",
            attempt,
            4,
            command_versions[0],
        )
        failed = DeviceCommandOutcome(
            0,
            "vpd_mister_weight",
            0.75,
            "number",
            "mister_vpd_weight",
            "failed",
            "command_timeout_outcome_unknown",
            attempt,
            4,
            command_versions[0],
        )
        await on_state((queued,))
        await on_state((failed,))
        return PushBatchResult((failed,))

    monkeypatch.setattr(ingestor, "push_to_esp32_detailed", failed_push)
    monkeypatch.setattr(shared, "recently_pushed", {})
    monkeypatch.setattr(shared, "recently_pushed_values", {})
    await ingestor._handle_setpoint_notification(
        pool,
        '{"parameter":"mister_vpd_weight","value":0.75,"source":"operator"}',
    )

    assert push_calls == 1
    assert connection.statuses == ["queued", "failed"]
    assert len(connection.alerts) == 1
    assert "FOR UPDATE SKIP LOCKED" in connection.claim_query
    assert "COALESCE(delivery_status, 'pending') = 'pending'" in connection.claim_query


@pytest.mark.asyncio
async def test_notify_cas_rejects_delayed_regression_from_superseded():
    requested_at = datetime(2026, 7, 9, 20, 0, tzinfo=UTC)

    class Connection:
        async def fetchval(self, query, *_args):
            if "UPDATE setpoint_changes" in query:
                return None
            if "SELECT delivery_status" in query:
                return "superseded"
            return None

    class Acquire:
        async def __aenter__(self):
            return Connection()

        async def __aexit__(self, *_exc):
            return False

    pool = MagicMock()
    pool.acquire.return_value = Acquire()

    with pytest.raises(RuntimeError, match="superseded.*queued"):
        await ingestor._cas_notify_delivery_state(pool, requested_at, "mister_vpd_weight", "queued")


@pytest.mark.asyncio
async def test_notify_cas_preserves_superseded_when_inflight_send_returns():
    requested_at = datetime(2026, 7, 9, 20, 0, tzinfo=UTC)

    class Connection:
        async def fetchval(self, query, *_args):
            if "UPDATE setpoint_changes" in query:
                return None
            if "SELECT delivery_status" in query:
                return "superseded"
            return None

    class Acquire:
        async def __aenter__(self):
            return Connection()

        async def __aexit__(self, *_exc):
            return False

    pool = MagicMock()
    pool.acquire.return_value = Acquire()

    persisted = await ingestor._cas_notify_delivery_state(pool, requested_at, "mister_vpd_weight", "sent")

    assert persisted == "superseded"


@pytest.mark.asyncio
async def test_writer_fatal_monitor_forces_supervised_process_failure():
    shared.note_writer_fatal("lifecycle_persistence_unavailable")

    with pytest.raises(RuntimeError, match="fatal device-writer state: lifecycle_persistence_unavailable"):
        await ingestor._writer_failure_monitor()


def test_real_connect_generation_is_monotonic_and_never_erases_newer_reconnect():
    first = shared.note_transport_connected()
    second = shared.note_transport_connected()

    assert (first, second) == (1, 2)
    assert shared.force_setpoint_push.is_set()
    assert shared.setpoint_dispatch_requested.is_set()

    shared.mark_transport_reconciled(first)
    assert shared.reconciled_transport_generation == 1
    assert shared.force_setpoint_push.is_set(), "generation 1 must not erase pending generation 2"

    shared.mark_transport_reconciled(second)
    assert shared.reconciled_transport_generation == 2
    assert not shared.force_setpoint_push.is_set()


def test_cfg_drift_clear_is_version_safe():
    observed = {"mister_vpd_weight": shared.note_cfg_drift("mister_vpd_weight")}
    newer = shared.note_cfg_drift("mister_vpd_weight")

    shared.clear_cfg_drift(observed)

    assert shared.cfg_drift_versions == {"mister_vpd_weight": newer}
    assert shared.setpoint_dispatch_requested.is_set()


def test_old_drift_completion_cannot_erase_newer_reconnect_wake():
    observed = {"mister_vpd_weight": shared.note_cfg_drift("mister_vpd_weight")}
    first_generation = shared.note_transport_connected()
    shared.mark_transport_reconciled(first_generation)
    newer_generation = shared.note_transport_connected()

    shared.clear_cfg_drift(observed)

    assert newer_generation == 2
    assert shared.reconciled_transport_generation == 1
    assert shared.cfg_drift_versions == {}
    assert shared.setpoint_dispatch_requested.is_set()


def test_failed_dispatch_consumes_only_its_immediate_wake_without_claiming_reconcile():
    generation = shared.note_transport_connected()
    observed = {"mister_vpd_weight": shared.note_cfg_drift("mister_vpd_weight")}

    shared.defer_failed_dispatch(generation, observed)

    assert shared.reconciled_transport_generation == 0
    assert shared.force_setpoint_push.is_set()
    assert shared.cfg_drift_versions == observed
    assert not shared.setpoint_dispatch_requested.is_set()

    shared.setpoint_dispatch_requested.set()
    newer_generation = shared.note_transport_connected()
    shared.defer_failed_dispatch(generation, observed)
    assert newer_generation == 2
    assert shared.setpoint_dispatch_requested.is_set()


@pytest.mark.asyncio
async def test_long_writer_does_not_delay_short_periodic_task_past_cadence():
    writer_started = asyncio.Event()
    release_writer = asyncio.Event()
    heartbeat_starts: list[float] = []

    async def long_writer(_pool):
        writer_started.set()
        await release_writer.wait()

    async def heartbeat(_pool):
        heartbeat_starts.append(asyncio.get_running_loop().time())

    tasks = [
        ("setpoint_dispatch", 300.0, long_writer),
        ("planning_heartbeat", 1.0, heartbeat),
    ]
    last_run = {name: 0.0 for name, _interval, _fn in tasks}
    running: dict[str, asyncio.Task[None]] = {}
    pool = MagicMock()

    started = ingestor._launch_due_tasks(pool, tasks, last_run, running, 400.0, {"setpoint_dispatch": 300.0})
    assert started == ["setpoint_dispatch", "planning_heartbeat"]
    await asyncio.wait_for(writer_started.wait(), timeout=0.2)
    await asyncio.sleep(0)
    assert len(heartbeat_starts) == 1
    assert not running["setpoint_dispatch"].done()

    # Reap the first heartbeat and launch its next cadence while the writer is
    # still deliberately blocked.  The simulated start is only 0.1s late,
    # comfortably below the 2x cadence (2.0s) acceptance bound.
    await asyncio.sleep(0)
    first_heartbeat = running["planning_heartbeat"]
    await first_heartbeat
    started = ingestor._launch_due_tasks(pool, tasks, last_run, running, 401.1, {"setpoint_dispatch": 300.0})
    assert started == ["planning_heartbeat"]
    await asyncio.sleep(0)
    assert len(heartbeat_starts) == 2
    assert 401.1 - 401.0 <= 1.0

    release_writer.set()
    await asyncio.gather(*running.values())


@pytest.mark.asyncio
async def test_failed_forced_dispatch_is_throttled_not_relaunched_each_tick():
    runs = 0

    async def dispatcher(_pool):
        nonlocal runs
        runs += 1

    tasks = [("setpoint_dispatch", 300.0, dispatcher)]
    last_run = {"setpoint_dispatch": 100.0}
    running: dict[str, asyncio.Task[None]] = {}
    pool = MagicMock()

    assert (
        ingestor._launch_due_tasks(
            pool,
            tasks,
            last_run,
            running,
            101.0,
            {"setpoint_dispatch": 300.0},
            {"setpoint_dispatch"},
        )
        == []
    )
    assert (
        ingestor._launch_due_tasks(
            pool,
            tasks,
            last_run,
            running,
            129.9,
            {"setpoint_dispatch": 300.0},
            {"setpoint_dispatch"},
        )
        == []
    )
    assert ingestor._launch_due_tasks(
        pool,
        tasks,
        last_run,
        running,
        130.0,
        {"setpoint_dispatch": 300.0},
        {"setpoint_dispatch"},
    ) == ["setpoint_dispatch"]
    await asyncio.gather(*running.values())
    assert runs == 1


@pytest.mark.asyncio
async def test_restart_terminalizes_only_unsent_in_memory_queue_states():
    class Connection:
        def __init__(self):
            self.query = ""

        async def fetch(self, query):
            self.query = query
            return [{"parameter": "one"}, {"parameter": "two"}]

    class Acquire:
        def __init__(self, connection):
            self.connection = connection

        async def __aenter__(self):
            return self.connection

        async def __aexit__(self, *_exc):
            return False

    connection = Connection()
    pool = MagicMock()
    pool.acquire.return_value = Acquire(connection)

    count = await ingestor.reconcile_interrupted_device_writes(pool)

    assert count == 2
    assert "delivery_status IN ('requested', 'queued', 'retrying')" in connection.query
    assert "delivery_status = 'failed'" in connection.query
    assert "'sent'" not in connection.query.split("delivery_status IN", 1)[1]
