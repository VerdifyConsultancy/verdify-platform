"""Climate durable acceptance and exact-identity replay, without live services."""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ingestor"))
for key, value in {
    "DB_USER": "test",
    "DB_PASSWORD": "test",
    "DB_HOST": "127.0.0.1",
    "DB_PORT": "5432",
    "DB_NAME": "test",
}.items():
    os.environ.setdefault(key, value)
import ingestor  # noqa: E402


class Pool:
    def __init__(self, *, fail=False, cancel=False):
        self.fail, self.cancel = fail, cancel
        self.events = {}
        self.calls = []

    def acquire(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def fetchval(self, query, *args):
        self.calls.append(args)
        if self.cancel:
            raise asyncio.CancelledError
        if self.fail:
            raise OSError("injected DB unavailable")
        if args[0] in self.events:
            assert self.events[args[0]] == args
            return False
        self.events[args[0]] = args
        return True


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("VERDIFY_CLIMATE_EVENT_SPOOL_ENABLED", "1")
    monkeypatch.setattr(ingestor, "STATE_DIR", tmp_path)
    monkeypatch.setattr(ingestor, "CLIMATE_SPOOL_PATH", tmp_path / "legacy.jsonl")
    monkeypatch.setattr(ingestor, "_climate_event_spool", None)
    monkeypatch.setattr(ingestor, "state", ingestor.State())
    monkeypatch.setattr(ingestor.shared, "transport_generation", 7)
    monkeypatch.setattr(ingestor, "_fanout_publish", lambda *args: None)
    yield
    if ingestor._climate_event_spool is not None:
        ingestor._climate_event_spool.close()


def test_clear_only_after_fsync_acceptance_and_replay_preserves_identity():
    ts = datetime(2000, 1, 1, tzinfo=UTC)
    ingestor.state.climate["temp_avg"] = 72
    asyncio.run(ingestor.write_climate(Pool(fail=True), ts))
    assert ingestor.state.climate == {}
    original = ingestor._get_climate_event_spool().rows()
    assert len(original) == 1
    ingestor._climate_event_spool.close()
    ingestor._climate_event_spool = None
    ingestor.state.climate_latest.clear()
    ingestor.shared.transport_generation = 99
    pool = Pool()
    asyncio.run(ingestor.write_climate(pool, datetime(2001, 1, 1, tzinfo=UTC)))
    assert pool.calls[0][0] == original[0][1]
    assert pool.calls[0][1] == ts
    assert pool.calls[0][4] == 7
    assert ingestor._get_climate_event_spool().rows() == []
    assert ingestor.state.cfg_readback == {}


def test_disk_full_acceptance_keeps_fresh_and_oldest(monkeypatch):
    monkeypatch.setattr(ingestor, "CLIMATE_SPOOL_MAX_ROWS", 1)
    ts = datetime(2000, 1, 1, tzinfo=UTC)
    ingestor.state.climate["temp_avg"] = 72
    asyncio.run(ingestor.write_climate(Pool(fail=True), ts))
    original = ingestor._get_climate_event_spool().rows()
    ingestor.state.climate["temp_avg"] = 73
    with pytest.raises(OSError, match="capacity"):
        asyncio.run(ingestor.write_climate(Pool(fail=True), datetime(2000, 1, 2, tzinfo=UTC)))
    assert ingestor.state.climate == {"temp_avg": 73}
    assert ingestor._get_climate_event_spool().rows() == original


def test_unknown_commit_retries_same_uuid_and_fans_out_once(monkeypatch):
    ts = datetime(2000, 1, 1, tzinfo=UTC)
    ingestor.state.climate["temp_avg"] = 72
    spool = ingestor._get_climate_event_spool()
    real_ack = spool.acknowledge
    monkeypatch.setattr(spool, "acknowledge", lambda _: (_ for _ in ()).throw(OSError("ack interrupted")))
    published = []
    monkeypatch.setattr(ingestor, "_fanout_publish", lambda *args: published.append(args))
    pool = Pool()
    asyncio.run(ingestor.write_climate(pool, ts))
    assert len(pool.events) == 1
    assert len(spool.rows()) == 1
    assert published == []
    monkeypatch.setattr(spool, "acknowledge", real_ack)
    asyncio.run(ingestor._drain_climate_event_spool(pool))
    assert pool.calls[0] == pool.calls[1]
    assert spool.rows() == []
    # Fanout is best-effort, not exactly-once. Unknown commits do not fanout
    # a duplicate or reconstruct confirmation from a replayed climate row.
    assert published == []


def test_cancelled_db_write_retains_event_for_restart():
    ingestor.state.climate["temp_avg"] = 72
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(ingestor.write_climate(Pool(cancel=True), datetime(2000, 1, 1, tzinfo=UTC)))
    assert len(ingestor._get_climate_event_spool().rows()) == 1


def test_fifo_replay_and_original_callback_provenance():
    pool = Pool(fail=True)
    for day in (1, 2):
        ingestor.state.climate["temp_avg"] = 70 + day
        ingestor.state.climate_provenance["temp_avg"] = {
            "received_at": f"2000-01-0{day}T00:00:00Z",
            "connection_generation": day,
            "runtime_instance_id": "original",
        }
        asyncio.run(ingestor.write_climate(pool, datetime(2000, 1, day, tzinfo=UTC)))
    original = ingestor._get_climate_event_spool().rows()
    pool = Pool()
    asyncio.run(ingestor._drain_climate_event_spool(pool))
    assert [call[0] for call in pool.calls] == [row[1] for row in original]
    assert '"connection_generation": 1' in pool.calls[0][6]
    assert '"connection_generation": 2' in pool.calls[1][6]


def test_legacy_jsonl_is_preserved_and_blocks_activation(tmp_path, monkeypatch):
    legacy = tmp_path / "legacy.jsonl"
    legacy.write_text('{"ts":"2000-01-01T00:00:00Z","temp_avg":72}\n')
    with pytest.raises(RuntimeError, match="lacks event identities"):
        ingestor._get_climate_event_spool()
    assert legacy.read_text() == '{"ts":"2000-01-01T00:00:00Z","temp_avg":72}\n'


def test_periodic_health_reports_stopped_backlog_and_failure_without_payload(caplog, monkeypatch):
    import json
    import logging

    monkeypatch.setattr(ingestor, "CLIMATE_SPOOL_MAX_ROWS", 1)
    monkeypatch.setattr(ingestor, "_source_spool", None)
    monkeypatch.setattr(ingestor, "_observation_spool", None)
    ingestor.state.climate["temp_avg"] = 72
    asyncio.run(ingestor.write_climate(Pool(fail=True), datetime(2000, 1, 1, tzinfo=UTC)))
    # No new acceptance is needed for the periodic warning to fire.
    caplog.clear()
    with caplog.at_level(logging.INFO):
        ingestor._report_spool_health()
    report = json.loads(next(r.message.split("ingestor_spool_health ", 1)[1] for r in caplog.records))
    assert report["queue"] == "climate" and report["backlog_high"] and report["rows"] == 1
    assert "temp_avg" not in str(report)
    asyncio.run(ingestor._drain_climate_event_spool(Pool()))
    caplog.clear()
    with caplog.at_level(logging.INFO):
        ingestor._report_spool_health()
    assert '"backlog_high": false' in caplog.text
    monkeypatch.setattr(ingestor._climate_event_spool, "health", lambda: (_ for _ in ()).throw(OSError("failure")))
    ingestor._report_spool_health()
    assert "ingestor_spool_health_failed queue=climate reason=OSError" in caplog.text
