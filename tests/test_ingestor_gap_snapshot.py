"""Reconnect gap rows describe a fresh, persisted controller relay burst (#835)."""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ingestor"))
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_HOST", "127.0.0.1")
os.environ.setdefault("DB_PORT", "5432")
os.environ.setdefault("DB_NAME", "test")

import ingestor  # noqa: E402


class _Connection:
    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []

    def transaction(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def executemany(self, query, rows):
        self.calls.append((query, tuple(rows)))

    async def execute(self, query, *args):
        self.calls.append((query, args))


class _Pool:
    def __init__(self):
        self.connection = _Connection()

    def acquire(self):
        return self.connection


def _fenced_source(monkeypatch, entity_map: dict[int, tuple[str, str]]):
    client = object()
    monkeypatch.setattr(ingestor.shared, "transport_generation", 7)
    monkeypatch.setitem(ingestor.shared.esp32, "client", client)
    monkeypatch.setitem(ingestor.shared.esp32, "state_subscription_client", client)
    monkeypatch.setitem(ingestor.shared.esp32, "state_subscription_generation", 7)
    monkeypatch.setattr(ingestor.state, "key_to_object_id", {key: obj_id for key, (obj_id, _) in entity_map.items()})
    monkeypatch.setattr(ingestor.state, "key_to_type", {key: kind for key, (_, kind) in entity_map.items()})
    return client


def _gap_status(pool):
    query, args = pool.connection.calls[-1]
    assert "INSERT INTO data_gaps" in query
    return args[-1]


@pytest.mark.parametrize("observe_initial", [False, True])
def test_reconnect_snapshot_uses_enumerated_canonical_keys_and_fresh_values(monkeypatch, observe_initial):
    client = _fenced_source(monkeypatch, {1: ("fan_1_running", "binary"), 2: ("mister___south_wall", "switch")})
    monkeypatch.setattr(ingestor.state, "equipment", {"fan1": True, "mister_south": False})
    observed_at = datetime.now(UTC)
    monkeypatch.setattr(ingestor, "_equipment_source_now", lambda: observed_at)
    removed = []

    def request_burst(actual_client, callback):
        assert actual_client is client
        assert ingestor.state.key_to_object_id[1] == "fan_1_running"
        callback(SimpleNamespace(key=1, state=False))
        callback(SimpleNamespace(key=2, state=True))
        return lambda: removed.append(True)

    monkeypatch.setattr(
        ingestor,
        "_observe_current_state_messages" if observe_initial else "_request_current_state_burst",
        request_burst,
    )
    pool = _Pool()
    asyncio.run(
        ingestor.backfill_gap(
            pool,
            observed_at - timedelta(minutes=5),
            observed_at - timedelta(minutes=1),
            "ingestor_process_restart",
            client=client,
            generation=7,
            connection_lost=asyncio.Event(),
            observe_initial_subscription=observe_initial,
        )
    )

    assert removed == [True]
    query, rows = pool.connection.calls[0]
    assert "v_runtime_equipment_state_write" in query
    assert rows == ((observed_at, "fan1", False), (observed_at, "mister_south", True))
    assert _gap_status(pool) == "snapshot_taken"


@pytest.mark.parametrize("observe_initial", [False, True])
@pytest.mark.parametrize("failure", ["missing", "invalid", "conflicting", "generation_changed", "no_enumeration"])
def test_reconnect_snapshot_marks_incomplete_without_inventing_state(monkeypatch, failure, observe_initial):
    entity_map = {} if failure == "no_enumeration" else {1: ("fan_1_running", "binary"), 2: ("vent_open", "binary")}
    client = _fenced_source(monkeypatch, entity_map)
    monkeypatch.setattr(ingestor.state, "equipment", {"fan1": True, "vent": True})
    observed_at = datetime.now(UTC)
    monkeypatch.setattr(ingestor, "_equipment_source_now", lambda: observed_at)
    requests = []

    def request_burst(_client, callback):
        requests.append(True)
        callback(SimpleNamespace(key=1, state=False))
        if failure == "invalid":
            callback(SimpleNamespace(key=2, state=None))
        elif failure == "conflicting":
            callback(SimpleNamespace(key=1, state=True))
        elif failure == "generation_changed":
            callback(SimpleNamespace(key=2, state=False))
            ingestor.shared.transport_generation = 8
        return lambda: None

    monkeypatch.setattr(
        ingestor,
        "_observe_current_state_messages" if observe_initial else "_request_current_state_burst",
        request_burst,
    )
    if failure == "missing":

        async def immediate_timeout(_awaitable, *, timeout):
            assert timeout == 20
            _awaitable.close()
            raise TimeoutError

        monkeypatch.setattr(ingestor.asyncio, "wait_for", immediate_timeout)

    pool = _Pool()
    asyncio.run(
        ingestor.backfill_gap(
            pool,
            observed_at - timedelta(minutes=5),
            observed_at - timedelta(minutes=1),
            "ingestor_restart",
            client=client,
            generation=7,
            connection_lost=asyncio.Event(),
            observe_initial_subscription=observe_initial,
        )
    )

    assert requests == ([] if failure == "no_enumeration" else [True])
    assert _gap_status(pool) == "snapshot_incomplete"
    state_writes = [rows for query, rows in pool.connection.calls if "v_runtime_equipment_state_write" in query]
    if failure in {"no_enumeration", "generation_changed", "conflicting"}:
        assert state_writes == []
    else:
        assert state_writes == [((observed_at, "fan1", False),)]


def test_pinned_primary_subscribe_and_initial_observer_send_exactly_one_request(monkeypatch):
    """Real protobuf parser/primary API method, device-denied synthetic transport."""
    from aioesphomeapi.api_pb2 import BinarySensorStateResponse, SubscribeStatesRequest
    from aioesphomeapi.client import APIClient

    class Wire:
        def __init__(self):
            self.messages = []
            self.handlers = []

        def add_message_callback(self, callback, types):
            item = (callback, types)
            self.handlers.append(item)
            return lambda: self.handlers.remove(item)

        def emit(self, message):
            for callback, types in list(self.handlers):
                if type(message) in types:
                    callback(message)

        def send_message_callback_response(self, message, callback, types):
            self.messages.append(message)
            remove = self.add_message_callback(callback, types)
            loop = asyncio.get_running_loop()
            loop.call_soon(self.emit, BinarySensorStateResponse(key=1, state=False))
            loop.call_soon(self.emit, BinarySensorStateResponse(key=2, state=True))
            return remove

    class Client:
        subscribe_states = APIClient.subscribe_states

        def _get_connection(self):
            return wire

    wire = Wire()
    client = Client()
    _fenced_source(monkeypatch, {1: ("fan_1_running", "binary"), 2: ("vent_open", "binary")})
    for key in ("client", "state_subscription_client"):
        monkeypatch.setitem(ingestor.shared.esp32, key, client)
    # These deliberately stale cached values must not become a gap snapshot.
    monkeypatch.setattr(ingestor.state, "equipment", {"fan1": True, "vent": False})
    observed_at = datetime.now(UTC)
    monkeypatch.setattr(ingestor, "_equipment_source_now", lambda: observed_at)
    pool = _Pool()
    primary = []

    async def startup():
        client.subscribe_states(primary.append)
        # Match the real startup call: no event-loop yield before observer registration.
        await ingestor.backfill_gap(
            pool,
            observed_at - timedelta(minutes=5),
            observed_at,
            "ingestor_restart",
            client=client,
            generation=7,
            connection_lost=asyncio.Event(),
            observe_initial_subscription=True,
        )

    asyncio.run(startup())
    assert len(wire.messages) == 1 and isinstance(wire.messages[0], SubscribeStatesRequest)
    assert len(wire.handlers) == 1  # Only the persistent primary callback remains.
    assert [(state.key, state.state) for state in primary] == [(1, False), (2, True)]
    assert pool.connection.calls[0][1] == ((observed_at, "fan1", False), (observed_at, "vent", True))
    assert _gap_status(pool) == "snapshot_taken"


def test_initial_observer_cancelled_wait_removes_callback_and_writes_no_gap(monkeypatch):
    client = _fenced_source(monkeypatch, {1: ("fan_1_running", "binary")})
    removed = []
    monkeypatch.setattr(ingestor, "_observe_current_state_messages", lambda *_: lambda: removed.append(True))

    async def cancelled(awaitable, *, timeout):
        awaitable.close()
        raise asyncio.CancelledError

    monkeypatch.setattr(ingestor.asyncio, "wait_for", cancelled)
    pool = _Pool()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            ingestor.backfill_gap(
                pool,
                datetime.now(UTC) - timedelta(minutes=5),
                datetime.now(UTC),
                "ingestor_restart",
                client=client,
                generation=7,
                connection_lost=asyncio.Event(),
                observe_initial_subscription=True,
            )
        )
    assert removed == [True]
    assert pool.connection.calls == []


def test_initial_observer_late_packet_after_timeout_cannot_complete_persisted_gap(monkeypatch):
    from aioesphomeapi.api_pb2 import BinarySensorStateResponse

    handlers = []

    class Wire:
        def add_message_callback(self, callback, types):
            item = (callback, types)
            handlers.append(item)
            return lambda: handlers.remove(item)

    class Client:
        def _get_connection(self):
            return Wire()

    _fenced_source(monkeypatch, {1: ("fan_1_running", "binary")})
    client = Client()
    for key in ("client", "state_subscription_client"):
        monkeypatch.setitem(ingestor.shared.esp32, key, client)
    monkeypatch.setattr(ingestor.state, "equipment", {"fan1": True})

    async def timeout(awaitable, *, timeout):
        assert len(handlers) == 1
        awaitable.close()
        raise TimeoutError

    monkeypatch.setattr(ingestor.asyncio, "wait_for", timeout)
    pool = _Pool()
    asyncio.run(
        ingestor.backfill_gap(
            pool,
            datetime.now(UTC) - timedelta(minutes=5),
            datetime.now(UTC),
            "ingestor_restart",
            client=client,
            generation=7,
            connection_lost=asyncio.Event(),
            observe_initial_subscription=True,
        )
    )
    assert handlers == []
    persisted = list(pool.connection.calls)
    late = BinarySensorStateResponse(key=1, state=True)
    for callback, types in list(handlers):
        if type(late) in types:
            callback(late)
    assert pool.connection.calls == persisted
    assert _gap_status(pool) == "snapshot_incomplete"
    assert len(persisted) == 1  # No stale cached equipment row was invented.
