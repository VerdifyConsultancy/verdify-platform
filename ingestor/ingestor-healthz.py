#!/usr/bin/env python3
"""Mode-specific kubelet probes for the singleton Verdify ingestor.

The no-argument/default ``freshness`` mode preserves the legacy climate query
for rollout compatibility. ``liveness`` is standard-library-only and reads a
local event-loop status file. ``readiness`` requires a healthy singleton writer
before applying the same climate-freshness contract; ``--require-empty-spool``
adds a fail-closed point-in-time spool observation. It cannot quiesce the writer
or authorize deletion of restart-volatile state.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from process_health import (
    DEFAULT_HEARTBEAT_MAX_AGE_S,
    DEFAULT_STATUS_PATH,
    evaluate_liveness,
    evaluate_writer_readiness,
    read_runtime_status,
)

DEFAULT_FRESHNESS_S = 300
DEFAULT_CLIMATE_SPOOL_PATH = Path(
    os.environ.get(
        "CLIMATE_SPOOL_PATH",
        str(Path(os.environ.get("STATE_DIR", "/srv/verdify/state")) / "spool" / "climate.jsonl"),
    )
)


async def climate_age_seconds(dsn: str, timeout: float) -> float | None:
    """Return seconds since the most recent climate row, or ``None``."""
    import asyncpg

    conn = await asyncpg.connect(dsn, timeout=timeout)
    try:
        return await conn.fetchval("SELECT extract(epoch FROM now() - max(ts))::float FROM climate")
    finally:
        await conn.close()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingestor liveness/readiness probe")
    parser.add_argument(
        "--mode",
        choices=("freshness", "liveness", "readiness"),
        default="freshness",
        help="Legacy climate freshness, local event-loop liveness, or writer readiness",
    )
    parser.add_argument(
        "--max-age",
        type=float,
        default=DEFAULT_FRESHNESS_S,
        help=f"Climate stale threshold in seconds (default: {DEFAULT_FRESHNESS_S})",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="DB connect/query timeout in seconds (default: 10)",
    )
    parser.add_argument(
        "--status-file",
        type=Path,
        default=DEFAULT_STATUS_PATH,
        help=f"Local runtime status path (default: {DEFAULT_STATUS_PATH})",
    )
    parser.add_argument(
        "--heartbeat-max-age",
        type=float,
        default=DEFAULT_HEARTBEAT_MAX_AGE_S,
        help=f"Liveness heartbeat threshold in seconds (default: {DEFAULT_HEARTBEAT_MAX_AGE_S:.0f})",
    )
    parser.add_argument(
        "--require-empty-spool",
        action="store_true",
        help="readiness only: reject even a fresh write-fence contention spool at probe time",
    )
    parser.add_argument(
        "--spool-path",
        type=Path,
        default=None,
        help=f"strict point-in-time spool path (default: {DEFAULT_CLIMATE_SPOOL_PATH})",
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress the human-readable status line")
    args = parser.parse_args(argv)
    if args.mode != "readiness" and (args.require_empty_spool or args.spool_path is not None):
        parser.error("--require-empty-spool and --spool-path are valid only with --mode readiness")
    if args.spool_path is not None and not args.require_empty_spool:
        parser.error("--spool-path requires --require-empty-spool")
    if args.spool_path is None:
        args.spool_path = DEFAULT_CLIMATE_SPOOL_PATH
    return args


def _strict_spool_pending(path: Path) -> bool:
    """Fail closed on a nonempty or unreadable point-in-time spool path."""
    try:
        return path.stat().st_size > 0
    except FileNotFoundError:
        return False
    except OSError:
        return True


def _runtime_probe(args: argparse.Namespace, *, require_writer: bool) -> int:
    status = read_runtime_status(args.status_file)
    alive, liveness_reason, age = evaluate_liveness(status, max_age_seconds=args.heartbeat_max_age)
    writer_ready, writer_reason = evaluate_writer_readiness(
        status,
        require_empty_spool=args.require_empty_spool,
    )
    if require_writer and args.require_empty_spool and _strict_spool_pending(args.spool_path):
        writer_ready = False
        writer_reason = "climate_spool_pending_strict"
    if not alive or (require_writer and not writer_ready):
        if not args.quiet:
            print(
                f"ingestor-healthz: NOT READY — liveness={liveness_reason} heartbeat_age={age} writer={writer_reason}",
                file=sys.stderr,
            )
        return 1
    if not args.quiet:
        print(f"ingestor-healthz: ALIVE — liveness={liveness_reason} heartbeat_age={age:.1f}s")
    return 0


def _freshness_probe(args: argparse.Namespace) -> int:
    from config import DB_DSN

    try:
        age = asyncio.run(climate_age_seconds(DB_DSN, args.timeout))
    except Exception as exc:  # connection refused, auth, timeout, etc.
        if not args.quiet:
            print(f"ingestor-healthz: ERROR querying climate freshness: {exc}", file=sys.stderr)
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


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.mode == "liveness":
        return _runtime_probe(args, require_writer=False)
    if args.mode == "readiness":
        runtime_result = _runtime_probe(args, require_writer=True)
        if runtime_result:
            return runtime_result
        freshness_result = _freshness_probe(args)
        if freshness_result:
            return freshness_result
        # Close the query-sized TOCTOU window: climate may have been spooled
        # while the DB freshness check was in flight. This remains a final
        # point-in-time observation, not authorization to delete a live writer.
        if args.require_empty_spool and _strict_spool_pending(args.spool_path):
            if not args.quiet:
                print("ingestor-healthz: NOT READY — writer=climate_spool_pending_strict", file=sys.stderr)
            return 1
        return 0
    return _freshness_probe(args)


if __name__ == "__main__":
    raise SystemExit(main())
