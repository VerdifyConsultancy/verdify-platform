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


def test_reconnect_snapshot_uses_enumerated_canonical_keys_and_fresh_values(monkeypatch):
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

    monkeypatch.setattr(ingestor, "_request_current_state_burst", request_burst)
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
        )
    )

    assert removed == [True]
    query, rows = pool.connection.calls[0]
    assert "v_runtime_equipment_state_write" in query
    assert rows == ((observed_at, "fan1", False), (observed_at, "mister_south", True))
    assert _gap_status(pool) == "snapshot_taken"


@pytest.mark.parametrize("failure", ["missing", "invalid", "generation_changed", "no_enumeration"])
def test_reconnect_snapshot_marks_incomplete_without_inventing_state(monkeypatch, failure):
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
        elif failure == "generation_changed":
            callback(SimpleNamespace(key=2, state=False))
            ingestor.shared.transport_generation = 8
        return lambda: None

    monkeypatch.setattr(ingestor, "_request_current_state_burst", request_burst)
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
        )
    )

    assert requests == ([] if failure == "no_enumeration" else [True])
    assert _gap_status(pool) == "snapshot_incomplete"
    state_writes = [rows for query, rows in pool.connection.calls if "v_runtime_equipment_state_write" in query]
    if failure == "no_enumeration" or failure == "generation_changed":
        assert state_writes == []
    else:
        assert state_writes == [((observed_at, "fan1", False),)]
