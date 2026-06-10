#!/usr/bin/env python3
"""ingestor-healthz.py — climate-freshness liveness/readiness probe.

Checks how stale the most recent ``climate`` row is and exits non-zero when the
ingestor has stopped persisting telemetry. Reuses the same freshness contract as
the API ``/health`` endpoint (``SELECT extract(epoch FROM now() - max(ts)) FROM
climate``; stale at > 300 s).

Designed for a k3s liveness/readiness probe — exec form, no HTTP server needed:

    livenessProbe:
      exec:
        command: ["python", "ingestor/ingestor-healthz.py"]
      initialDelaySeconds: 60     # let a fresh ingestor connect + write one row
      periodSeconds: 30
      failureThreshold: 5         # ~150 s of staleness before restart
      timeoutSeconds: 10

Exit codes:
    0  fresh   — max(ts) within the freshness window (or override)
    1  stale   — no rows, or max(ts) older than the freshness window
    2  error   — could not connect / query the database

SHADOW_MODE note: this probe only READS (``max(ts)``); it never writes the DB or
touches the device, so it is safe to run against the live DB from a shadow pod.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import asyncpg

from config import DB_DSN

# Same threshold the API /health endpoint uses to mark the ingestor "stale".
DEFAULT_FRESHNESS_S = 300


async def climate_age_seconds(dsn: str, timeout: float) -> float | None:
    """Return seconds since the most recent climate row, or None if empty.

    Reuses the API freshness query verbatim so the cluster probe and the API
    health report agree on what "fresh" means.
    """
    conn = await asyncpg.connect(dsn, timeout=timeout)
    try:
        return await conn.fetchval("SELECT extract(epoch FROM now() - max(ts))::float FROM climate")
    finally:
        await conn.close()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingestor climate-freshness probe")
    parser.add_argument(
        "--max-age",
        type=float,
        default=DEFAULT_FRESHNESS_S,
        help=f"Stale threshold in seconds (default: {DEFAULT_FRESHNESS_S})",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="DB connect/query timeout in seconds (default: 10)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the human-readable status line (exit code only)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        age = asyncio.run(climate_age_seconds(DB_DSN, args.timeout))
    except Exception as e:  # connection refused, auth, timeout, etc.
        if not args.quiet:
            print(f"ingestor-healthz: ERROR querying climate freshness: {e}", file=sys.stderr)
        return 2

    if age is None:
        if not args.quiet:
            print("ingestor-healthz: STALE — climate table is empty", file=sys.stderr)
        return 1

    if age > args.max_age:
        if not args.quiet:
            print(
                f"ingestor-healthz: STALE — climate is {age:.0f}s old (> {args.max_age:.0f}s)",
                file=sys.stderr,
            )
        return 1

    if not args.quiet:
        print(f"ingestor-healthz: OK — climate is {age:.0f}s old (<= {args.max_age:.0f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
