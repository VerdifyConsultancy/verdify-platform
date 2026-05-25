#!/usr/bin/env /srv/greenhouse/.venv/bin/python3
"""One-off historical backfill for planner-owned memory.

The live ingestor handles incremental planner memory ingestion. This script is
for bounded, operator-run historical loads and support-doc seeding.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "ingestor"))

import planner_memory_ingest as pmi  # noqa: E402

from config import DB_DSN, GREENHOUSE_ID  # noqa: E402


def _parse_timestamp(raw: str | None) -> datetime | None:
    if not raw:
        return None
    value = raw.strip()
    if not value:
        return None
    if len(value) == 10:
        value = f"{value}T00:00:00+00:00"
    value = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _chunked(items: list[pmi.PlannerMemoryItem], size: int) -> Iterable[list[pmi.PlannerMemoryItem]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


async def _fetch_outcome_items(
    *,
    since: datetime | None,
    until: datetime | None,
    limit: int,
) -> list[pmi.PlannerMemoryItem]:
    conn = await asyncpg.connect(DB_DSN)
    try:
        rows = await conn.fetch(
            """
            SELECT pj.plan_id,
                   pj.trigger_id::text AS trigger_id,
                   pj.created_at,
                   pj.validated_at,
                   pj.expected_outcome,
                   pj.actual_outcome,
                   pj.outcome_score,
                   pj.anchor_score,
                   pj.lesson_extracted,
                   pj.planner_instance,
                   pj.hypothesis,
                   COALESCE(pdl.event_type, 'UNKNOWN') AS event_type
              FROM plan_journal pj
              LEFT JOIN plan_delivery_log pdl ON pdl.trigger_id = pj.trigger_id
             WHERE pj.validated_at IS NOT NULL
               AND pj.plan_id NOT LIKE 'iris-reactive%'
               AND (
                   NULLIF(trim(COALESCE(pj.actual_outcome, '')), '') IS NOT NULL
                   OR NULLIF(trim(COALESCE(pj.lesson_extracted, '')), '') IS NOT NULL
               )
               AND ($1::timestamptz IS NULL OR pj.validated_at >= $1::timestamptz)
               AND ($2::timestamptz IS NULL OR pj.validated_at < $2::timestamptz)
             ORDER BY pj.validated_at ASC
             LIMIT $3
            """,
            since,
            until,
            limit,
        )
    finally:
        await conn.close()
    return [pmi.build_outcome_memory_item(dict(row)) for row in rows]


async def _fetch_prior_plan_items(
    *,
    since: datetime | None,
    until: datetime | None,
    limit: int,
) -> list[pmi.PlannerMemoryItem]:
    conn = await asyncpg.connect(DB_DSN)
    try:
        rows = await conn.fetch(
            """
            SELECT pj.plan_id,
                   pj.trigger_id::text AS trigger_id,
                   pj.created_at,
                   pj.validated_at,
                   pj.expected_outcome,
                   pj.actual_outcome,
                   pj.outcome_score,
                   pj.anchor_score,
                   pj.lesson_extracted,
                   pj.planner_instance,
                   pj.hypothesis,
                   COALESCE(pdl.event_type, 'UNKNOWN') AS event_type
              FROM plan_journal pj
              LEFT JOIN plan_delivery_log pdl ON pdl.trigger_id = pj.trigger_id
             WHERE pj.plan_id NOT LIKE 'iris-reactive%'
               AND (
                   NULLIF(trim(COALESCE(pj.hypothesis, '')), '') IS NOT NULL
                   OR NULLIF(trim(COALESCE(pj.expected_outcome, '')), '') IS NOT NULL
               )
               AND ($1::timestamptz IS NULL OR pj.created_at >= $1::timestamptz)
               AND ($2::timestamptz IS NULL OR pj.created_at < $2::timestamptz)
             ORDER BY pj.created_at ASC
             LIMIT $3
            """,
            since,
            until,
            limit,
        )
    finally:
        await conn.close()
    return [pmi.build_prior_plan_memory_item(dict(row)) for row in rows]


def _dry_run_summary(
    *,
    mode: str,
    items: list[pmi.PlannerMemoryItem],
    batch_size: int,
    greenhouse_id: str,
) -> dict[str, Any]:
    sample_items = items[:batch_size]
    return {
        "mode": mode,
        "dry_run": True,
        "candidate_count": len(items),
        "source_ids": [item.source_id for item in items],
        "sample_payload": pmi.build_memory_ingest_request(
            greenhouse_id=greenhouse_id,
            batch_id=f"dry-run-{mode}",
            items=sample_items,
        ),
    }


async def _load_items(args: argparse.Namespace) -> list[pmi.PlannerMemoryItem]:
    since = _parse_timestamp(args.since)
    until = _parse_timestamp(args.until)
    if args.mode == "outcomes":
        return await _fetch_outcome_items(since=since, until=until, limit=args.limit)
    if args.mode == "prior-plans":
        return await _fetch_prior_plan_items(since=since, until=until, limit=args.limit)
    if not args.support_doc_file:
        raise SystemExit("--support-doc-file is required for --mode support-docs")
    return pmi.load_support_doc_items(Path(args.support_doc_file), greenhouse_id=args.greenhouse_id)[: args.limit]


async def _run(args: argparse.Namespace) -> int:
    if args.batch_size < 1:
        raise SystemExit("--batch-size must be >= 1")
    if args.limit < 1:
        raise SystemExit("--limit must be >= 1")

    items = await _load_items(args)
    if args.dry_run:
        print(
            json.dumps(
                _dry_run_summary(
                    mode=args.mode, items=items, batch_size=args.batch_size, greenhouse_id=args.greenhouse_id
                ),
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    pmi.GREENHOUSE_ID = args.greenhouse_id
    total = {"accepted_count": 0, "duplicate_count": 0, "rejected_count": 0}
    for batch_number, batch in enumerate(_chunked(items, args.batch_size), start=1):
        batch_id = f"historical-{args.mode}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{batch_number:04d}"
        result = pmi.ingest_planner_memory_batch(
            items=batch,
            batch_id=batch_id,
            base_url=args.base_url,
            timeout=args.timeout,
        )
        if result.status >= 400:
            print(json.dumps({"batch_id": batch_id, "status": result.status, "body": result.body}, sort_keys=True))
            return 1
        body = result.body if isinstance(result.body, dict) else {}
        accepted = int(body.get("accepted_count") or 0)
        duplicate = int(body.get("duplicate_count") or 0)
        rejected = int(body.get("rejected_count") or 0)
        total["accepted_count"] += accepted
        total["duplicate_count"] += duplicate
        total["rejected_count"] += rejected
        print(
            json.dumps(
                {
                    "mode": args.mode,
                    "batch_id": batch_id,
                    "item_count": len(batch),
                    "accepted_count": accepted,
                    "duplicate_count": duplicate,
                    "rejected_count": rejected,
                    "first_source_id": batch[0].source_id,
                    "last_source_id": batch[-1].source_id,
                    "elapsed_ms": result.elapsed_ms,
                },
                sort_keys=True,
            )
        )
        if rejected:
            return 1
    print(
        json.dumps(
            {"mode": args.mode, "batches": (len(items) + args.batch_size - 1) // args.batch_size, **total},
            sort_keys=True,
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("outcomes", "prior-plans", "support-docs"), required=True)
    parser.add_argument("--since", help="Lower timestamp bound, e.g. 2026-04-01 or 2026-04-01T00:00:00Z.")
    parser.add_argument("--until", help="Exclusive upper timestamp bound.")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--support-doc-file", help="JSON array seed file for support docs.")
    parser.add_argument("--greenhouse-id", default=GREENHOUSE_ID)
    parser.add_argument("--base-url", help="Override PLANNER_MEMORY_URL for writes.")
    parser.add_argument("--timeout", type=float, default=None)
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
