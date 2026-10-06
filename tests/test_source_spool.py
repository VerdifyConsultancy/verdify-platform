"""Crash/replay behavior of the UUID-idempotent equipment source spool."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ingestor"))
from source_spool import SourceSpool  # noqa: E402


def test_abrupt_process_exit_retains_original_order_and_lineage(tmp_path):
    path = tmp_path / "spool.sqlite"
    program = """
import os,sys
from pathlib import Path
from source_spool import SourceSpool
q=SourceSpool(Path(sys.argv[1]))
q.put('counter','old',{'observed_at':'2000-01-01T00:00:00Z','runtime':'old-runtime','generation':4})
q.put('counter','new',{'observed_at':'2000-01-02T00:00:00Z','runtime':'new-runtime','generation':1})
os._exit(23)
"""
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "ingestor")}
    result = subprocess.run([sys.executable, "-c", program, str(path)], env=env, check=False)
    assert result.returncode == 23
    reopened = SourceSpool(path)
    assert [(identity, value["runtime"], value["generation"]) for _, identity, value in reopened.rows()] == [
        ("old", "old-runtime", 4),
        ("new", "new-runtime", 1),
    ]
    reopened.close()


def test_unknown_database_commit_replays_identical_uuid_until_ack(tmp_path):
    path = tmp_path / "spool.sqlite"
    spool = SourceSpool(path)
    spool.put("receipt", "original-uuid", {"observed_at": "2000-01-01T00:00:00Z"})
    # DB accepted, process stopped before local ack. Existing UUID function
    # handles this duplicate; the local queue must not invent a new UUID/time.
    original = spool.rows()
    spool.close()
    spool = SourceSpool(path)
    assert spool.rows() == original
    spool.put("receipt", "original-uuid", original[0][2])
    assert spool.rows() == original
    spool.acknowledge(["original-uuid"])
    spool.close()
    spool = SourceSpool(path)
    assert spool.rows() == []
    spool.close()


def test_interrupted_local_ack_keeps_all_unacknowledged(tmp_path):
    spool = SourceSpool(tmp_path / "spool.sqlite")
    for identity in ("first", "second", "third"):
        spool.put("snapshot", identity, {"identity": identity})
    # Simulate a killed/failed transaction before its commit.
    spool.conn.execute("BEGIN")
    spool.conn.execute("DELETE FROM source_queue WHERE identity='first'")
    spool.conn.rollback()
    assert [row[1] for row in spool.rows()] == ["first", "second", "third"]
    spool.acknowledge(["first"])
    assert [row[1] for row in spool.rows()] == ["second", "third"]
    spool.close()


def test_full_capacity_and_uuid_collision_preserve_accepted_rows(tmp_path):
    spool = SourceSpool(tmp_path / "spool.sqlite", max_rows=1)
    spool.put("counter", "id", {"generation": 2})
    with pytest.raises(ValueError, match="different immutable"):
        spool.put("counter", "id", {"generation": 3})
    with pytest.raises(OSError, match="capacity"):
        spool.put("counter", "new", {"generation": 3})
    assert spool.rows() == [("counter", "id", {"generation": 2})]
    spool.close()


def test_disk_write_failure_does_not_ack_or_remove_existing(tmp_path):
    spool = SourceSpool(tmp_path / "spool.sqlite")
    spool.put("counter", "id", {"generation": 2})
    spool.conn.execute("PRAGMA query_only=ON")
    import sqlite3

    with pytest.raises(sqlite3.OperationalError):
        spool.acknowledge(["id"])
    assert spool.rows() == [("counter", "id", {"generation": 2})]
    spool.close()


def test_failed_atomic_seal_rolls_back_event_removal(tmp_path):
    spool = SourceSpool(tmp_path / "spool.sqlite", max_bytes=20)
    spool.put("event", "event", {"x": 1})
    with pytest.raises(OSError, match="capacity"):
        spool.put("receipt", "receipt", {"payload": "too large to fit"}, replace=("event",))
    assert spool.rows() == [("event", "event", {"x": 1})]
    spool.close()


def test_sqlite_disk_full_rolls_back_without_losing_accepted_evidence(tmp_path):
    import sqlite3

    spool = SourceSpool(tmp_path / "spool.sqlite")
    spool.put("counter", "original", {"generation": 2})
    pages = spool.conn.execute("PRAGMA page_count").fetchone()[0]
    spool.conn.execute(f"PRAGMA max_page_count={pages}")
    with pytest.raises(sqlite3.OperationalError, match="full"):
        spool.put("counter", "new", {"payload": "x" * 1000000})
    assert spool.rows() == [("counter", "original", {"generation": 2})]
    spool.close()


def test_process_death_during_ack_transaction_retains_every_uncommitted_row(tmp_path):
    path = tmp_path / "interrupted.sqlite"
    program = """
import os,sys
from pathlib import Path
from source_spool import SourceSpool
q=SourceSpool(Path(sys.argv[1]))
q.put_many([('event',str(i),{'generation':i,'observed_at':'2000-01-01T00:00:00Z'}) for i in range(100)])
q.conn.execute('BEGIN IMMEDIATE')
q.conn.execute('DELETE FROM source_queue WHERE seq <= 50')
os._exit(23)
"""
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "ingestor")}
    result = subprocess.run([sys.executable, "-c", program, str(path)], env=env, check=False)
    assert result.returncode == 23
    q = SourceSpool(path)
    assert [identity for _, identity, _ in q.rows()] == [str(i) for i in range(100)]
    assert q.conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    q.close()


def test_health_counts_payload_bytes_not_characters_and_high_water_disk(tmp_path):
    q = SourceSpool(tmp_path / "health.sqlite", max_rows=5)
    for i in range(4):
        q.put("event", str(i), {"value": "é" * 50})
    health = q.health()
    assert health["rows"] == 4 and health["backlog_high"]
    assert health["disk_bytes"] >= health["payload_bytes"] > 0
    assert health["free_bytes"] > 0
    q.acknowledge([str(i) for i in range(4)])
    drained = q.health()
    assert drained["rows"] == drained["payload_bytes"] == 0
    assert not drained["backlog_high"]
    # SQLite high-water allocation remains visible after the logical drain.
    assert drained["disk_bytes"] > 0
    q.close()


def test_unsupported_format_retained_and_v1_backup_restores_accepted_identity(tmp_path):
    import sqlite3

    path = tmp_path / "format.sqlite"
    backup = tmp_path / "v1-backup.sqlite"
    q = SourceSpool(path)
    q.put("event", "accepted", {"generation": 7, "observed_at": "2000-01-01T00:00:00Z"})
    original = q.rows()
    with sqlite3.connect(backup) as saved:
        q.conn.backup(saved)
    q.conn.execute("PRAGMA user_version=999")
    q.close()
    with pytest.raises(ValueError, match="unsupported"):
        SourceSpool(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone() == (999,)
        assert conn.execute("SELECT count(*) FROM source_queue").fetchone() == (1,)
    restored = SourceSpool(backup)
    assert restored.rows() == original
    restored.close()


@pytest.mark.parametrize(
    "payload",
    [
        {"observed_at": "2000-01-01T00:00:00Z"},
        {"row": {"ts": "2000-01-01T00:00:00+00:00"}},
        {"source_observed_at": "2000-01-01T00:00:00Z"},
        {"source_observed_through": "2000-01-01T00:00:00Z"},
        {"observations": [["state", 1, "2000-01-01T00:00:00Z"]]},
    ],
)
def test_health_uses_retained_source_clock_and_empty_queue_clears_age(tmp_path, payload):
    q = SourceSpool(tmp_path / "clock.sqlite")
    q.put("event", "original", payload)
    report = q.health()
    assert report["oldest_source_age_seconds"] > 86400
    assert report["backlog_stale"]
    q.acknowledge(["original"])
    assert q.health()["oldest_source_age_seconds"] is None
    assert not q.health()["backlog_stale"]
    q.put("event", "no-clock", {"generation": 7})
    assert q.health()["oldest_source_age_seconds"] is None
    q.close()
