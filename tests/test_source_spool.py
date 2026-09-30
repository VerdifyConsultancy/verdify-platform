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
