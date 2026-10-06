"""Bounded, fsync-backed queue for UUID-idempotent equipment source evidence.

SQLite rollback journaling and synchronous=FULL retain either the pre-commit or
post-commit queue. PostgreSQL commit precedes local acknowledgement, so unknown
commits replay the original UUID/payload through the existing insert-only APIs.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
from pathlib import Path


class SourceSpool:
    def __init__(self, path: Path, *, max_rows: int = 30000, max_bytes: int = 128 * 1024 * 1024):
        self.path = path
        self.max_rows, self.max_bytes = max_rows, max_bytes
        if max_rows < 1 or max_bytes < 1:
            raise ValueError("source spool capacity must be positive")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.execute("PRAGMA journal_mode=DELETE")
        self.conn.execute("PRAGMA synchronous=FULL")
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            self.conn.close()
            raise ValueError("unsupported source spool format; preserve database")
        self.conn.execute("PRAGMA user_version=1")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS source_queue (seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, identity TEXT NOT NULL UNIQUE, payload TEXT NOT NULL)"
        )
        self.conn.commit()
        for parent in (path.parent, path.parent.parent):
            directory = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)

    def put(self, kind: str, identity: str, payload: dict, *, replace: tuple[str, ...] = ()) -> None:
        self.put_many([(kind, identity, payload)], replace=replace)

    def put_many(self, entries: list[tuple[str, str, dict]], *, replace: tuple[str, ...] = ()) -> None:
        encoded_entries = [
            (kind, identity, json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False))
            for kind, identity, payload in entries
        ]
        identities: dict[str, tuple[str, str]] = {}
        for kind, identity, encoded in encoded_entries:
            if identity in identities and identities[identity] != (kind, encoded):
                raise ValueError("source UUID reused with different immutable payload")
            identities[identity] = (kind, encoded)
        with self.conn:
            # Verify every existing identity before removing any replaced row.
            for kind, identity, encoded in encoded_entries:
                previous = self.conn.execute(
                    "SELECT kind,payload FROM source_queue WHERE identity=?", (identity,)
                ).fetchone()
                if previous is not None and previous != (kind, encoded):
                    raise ValueError("source UUID reused with different immutable payload")
            if encoded_entries and all(
                self.conn.execute("SELECT 1 FROM source_queue WHERE identity=?", (identity,)).fetchone()
                for _, identity, _ in encoded_entries
            ):
                return
            self.conn.executemany("DELETE FROM source_queue WHERE identity=?", [(item,) for item in replace])
            for kind, identity, encoded in encoded_entries:
                if self.conn.execute("SELECT 1 FROM source_queue WHERE identity=?", (identity,)).fetchone():
                    continue
                count, size = self.backlog()
                if count >= self.max_rows or size + len(encoded.encode()) > self.max_bytes:
                    raise OSError("source spool capacity exhausted; existing evidence retained")
                self.conn.execute(
                    "INSERT INTO source_queue(kind,identity,payload) VALUES(?,?,?)", (kind, identity, encoded)
                )

    def backlog(self) -> tuple[int, int]:
        return self.conn.execute(
            "SELECT count(*),coalesce(sum(length(CAST(payload AS BLOB))),0) FROM source_queue"
        ).fetchone()

    def rows(self):
        return [
            (kind, identity, json.loads(payload))
            for kind, identity, payload in self.conn.execute(
                "SELECT kind,identity,payload FROM source_queue ORDER BY seq"
            )
        ]

    def health(self) -> dict:
        """Bounded metadata only; does not consume rows or expose their payloads."""
        count, size = self.backlog()
        free = shutil.disk_usage(self.path.parent).free
        # Journals/high-water pages consume space beyond serialized payload.
        disk_bytes = sum(
            path.stat().st_size for path in (self.path, Path(str(self.path) + "-journal")) if path.exists()
        )
        utilization = max(count / self.max_rows, size / self.max_bytes)
        oldest = self.conn.execute(
            """SELECT min((julianday(coalesce(
                json_extract(payload,'$.observed_at'),
                json_extract(payload,'$.row.ts'),
                json_extract(payload,'$.source_observed_at'),
                json_extract(payload,'$.source_observed_through'),
                json_extract(payload,'$.observations[0][2]')
            ))-2440587.5)*86400.0) FROM source_queue"""
        ).fetchone()[0]
        # Historical source age, not wall time of enqueue. Legacy format has
        # no local accepted-at clock; absent clocks remain null, never guessed.
        source_age = max(0.0, time.time() - oldest) if oldest is not None else None
        return {
            "format": self.conn.execute("PRAGMA user_version").fetchone()[0],
            "rows": count,
            "payload_bytes": size,
            "disk_bytes": disk_bytes,
            "free_bytes": free,
            "max_rows": self.max_rows,
            "max_payload_bytes": self.max_bytes,
            "utilization": utilization,
            "backlog_high": utilization >= 0.8,
            "oldest_source_age_seconds": source_age,
            "backlog_stale": source_age is not None and source_age >= 300,
            # Allow a full-size new payload and rollback-journal copy. This is
            # headroom warning, not a promise that another write will succeed.
            "disk_headroom_low": free < 2 * self.max_bytes,
        }

    def acknowledge(self, identities: list[str]) -> None:
        with self.conn:
            self.conn.executemany("DELETE FROM source_queue WHERE identity=?", [(identity,) for identity in identities])

    def close(self) -> None:
        self.conn.close()
