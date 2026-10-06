#!/usr/bin/env python3
"""Device-free real-process/filesystem fault measurements; no DB/model/ESP client.

Run on an exclusively owned directory. This proves the SQLite component boundary,
not PostgreSQL commit semantics or physical Longhorn/node-loss recovery.
"""

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ingestor"))
from source_spool import SourceSpool


def digest(rows):
    return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--rows", type=int, default=1000)
    args = parser.parse_args()
    if not 1 <= args.rows <= 30000:
        parser.error("rows must be between 1 and 30000")
    args.directory.mkdir(parents=True, exist_ok=False)
    child = """
import os,sys
from pathlib import Path
from source_spool import SourceSpool
q=SourceSpool(Path(sys.argv[1]))
if sys.argv[2]=='accept':
 for i in range(int(sys.argv[3])):
  q.put('qualification',str(i),{'source_observed_at':'2000-01-01T00:00:00Z','runtime':'prior-runtime','generation':7,'ordinal':i})
else:
 q.conn.execute('BEGIN IMMEDIATE')
 q.conn.execute('DELETE FROM source_queue')
os._exit(23)
"""
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "ingestor")}
    result = {"scope": "SQLite component on actual filesystem; no database/device/physical-node proof", "queues": {}}
    for name in ("equipment-source", "climate-events", "observational-events"):
        path = args.directory / (name + "-v1.sqlite")
        started = time.monotonic()
        accepted = subprocess.run([sys.executable, "-c", child, str(path), "accept", str(args.rows)], env=env)
        assert accepted.returncode == 23
        accept_seconds = time.monotonic() - started
        started = time.monotonic()
        queue = SourceSpool(path)
        original = queue.rows()
        assert [identity for _, identity, _ in original] == [str(i) for i in range(args.rows)]
        assert all(
            payload["generation"] == 7 and payload["source_observed_at"] == "2000-01-01T00:00:00Z"
            for _, _, payload in original
        )
        restored_seconds = time.monotonic() - started
        original_hash = digest(original)
        health = queue.health()
        queue.close()
        interrupted = subprocess.run([sys.executable, "-c", child, str(path), "interrupt_ack", "0"], env=env)
        assert interrupted.returncode == 23
        queue = SourceSpool(path)
        assert digest(queue.rows()) == original_hash
        assert queue.conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        pages = queue.conn.execute("PRAGMA page_count").fetchone()[0]
        queue.conn.execute(f"PRAGMA max_page_count={pages}")
        try:
            queue.put("qualification", "full-disk-rejected", {"payload": "x" * 1000000})
        except sqlite3.OperationalError as exc:
            assert "full" in str(exc)
        else:
            raise AssertionError("SQLITE_FULL fault did not occur")
        assert digest(queue.rows()) == original_hash
        backup = args.directory / (name + "-rollback-v1.sqlite")
        with sqlite3.connect(backup) as conn:
            queue.conn.backup(conn)
        queue.conn.execute("PRAGMA user_version=999")
        queue.close()
        try:
            SourceSpool(path)
        except ValueError:
            pass
        else:
            raise AssertionError("incompatible format was accepted")
        with sqlite3.connect(path) as conn:
            assert conn.execute("SELECT count(*) FROM source_queue").fetchone()[0] == args.rows
        queue = SourceSpool(backup)
        assert digest(queue.rows()) == original_hash
        started = time.monotonic()
        queue.acknowledge([identity for _, identity, _ in original])
        assert queue.rows() == []
        ack_seconds = time.monotonic() - started
        queue.close()
        result["queues"][name] = {
            "accepted_rows": args.rows,
            "unexplained_loss": 0,
            "accepted_sha256": original_hash,
            "enqueue_seconds": accept_seconds,
            "restart_reopen_decode_seconds": restored_seconds,
            "local_ack_seconds": ack_seconds,
            "before_drain_health": health,
            "abrupt_process_exit": "passed",
            "interrupted_ack_hot_journal": "passed",
            "sqlite_full_preserves_accepted": "passed",
            "incompatible_format_preserved": "passed",
            "v1_backup_restoration": "passed",
            "timestamp_generation_fifo": "passed",
        }
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
