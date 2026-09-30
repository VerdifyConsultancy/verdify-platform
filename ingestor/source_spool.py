"""Bounded, fsync-backed queue for UUID-idempotent equipment source evidence.

SQLite rollback journaling and synchronous=FULL retain either the pre-commit or
post-commit queue. PostgreSQL commit precedes local acknowledgement, so unknown
commits replay the original UUID/payload through the existing insert-only APIs.
"""

from __future__ import annotations

import json
import os
import sqlite3
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
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        with self.conn:
            previous = self.conn.execute(
                "SELECT kind,payload FROM source_queue WHERE identity=?", (identity,)
            ).fetchone()
            if previous is not None:
                if previous != (kind, encoded):
                    raise ValueError("source UUID reused with different immutable payload")
                return
            self.conn.executemany("DELETE FROM source_queue WHERE identity=?", [(item,) for item in replace])
            count, size = self.conn.execute(
                "SELECT count(*),coalesce(sum(length(CAST(payload AS BLOB))),0) FROM source_queue"
            ).fetchone()
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

    def acknowledge(self, identities: list[str]) -> None:
        with self.conn:
            self.conn.executemany("DELETE FROM source_queue WHERE identity=?", [(identity,) for identity in identities])

    def close(self) -> None:
        self.conn.close()
