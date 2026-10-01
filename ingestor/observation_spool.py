"""Durable historical observations; replay never restores live device state."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from source_spool import SourceSpool

KINDS = frozenset({"system_state", "override", "setpoint_observed", "esp32_log", "diagnostics"})


class ObservationSpool:
    def __init__(self, path: Path, *, max_rows: int = 30000, max_bytes: int = 128 * 1024 * 1024):
        self.queue = SourceSpool(path, max_rows=max_rows, max_bytes=max_bytes)

    def accept(
        self, events: list[tuple[str, dict]], observed_at: datetime, runtime: str, generation: int, greenhouse: str
    ):
        if observed_at.tzinfo is None or not 0 <= generation <= 9_007_199_254_740_991:
            raise ValueError("invalid observational lineage")
        entries = []
        for kind, payload in events:
            if kind not in KINDS:
                raise ValueError("unknown observational event kind")
            entries.append(
                (
                    kind,
                    str(uuid4()),
                    {
                        "observed_at": observed_at.isoformat(),
                        "runtime_instance_id": runtime,
                        "connection_generation": generation,
                        "greenhouse_id": greenhouse,
                        "payload": payload,
                    },
                )
            )
        # Related state/override events share one durable acceptance boundary.
        self.queue.put_many(entries)
        return [identity for _, identity, _ in entries]

    async def drain(self, pool, *, on_insert=None) -> None:
        for kind, identity, event in self.queue.rows():
            if kind not in KINDS:
                raise ValueError("unknown observational spool kind; preserve queue")
            async with pool.acquire() as conn:
                inserted = await conn.fetchval(
                    "SELECT public.fn_record_observational_source_event($1::uuid,$2::text,$3::timestamptz,$4::text,$5::uuid,$6::bigint,$7::jsonb)",
                    identity,
                    kind,
                    datetime.fromisoformat(event["observed_at"]),
                    event["greenhouse_id"],
                    event["runtime_instance_id"],
                    event["connection_generation"],
                    json.dumps(event["payload"], sort_keys=True, allow_nan=False),
                )
            # PostgreSQL's atomic insert-only API binds the exact immutable UUID.
            # Cancellation/unknown commit/ack failure leave that identity queued.
            self.queue.acknowledge([identity])
            if inserted and on_insert is not None:
                on_insert(kind, datetime.fromisoformat(event["observed_at"]), event["payload"])
