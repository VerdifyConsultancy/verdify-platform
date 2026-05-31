#!/usr/bin/env python3
"""ingestor-healthz (#25) — k8s exec-probe for the ingestor.

The ingestor is a connect-OUT worker with no inbound port, so its k8s probe is
an ``exec`` command, not an ``httpGet``. This script is that command. It performs
ZERO writes (DB or device), so it is identical-safe against a prod pod and a
SHADOW_MODE/parallel-run pod.

Exit code 0 = healthy, 1 = unhealthy (k8s treats non-zero as a failed probe).

Usage (k8s):
  livenessProbe:
    exec:
      command: ["python", "ingestor-healthz.py", "--liveness"]
  readinessProbe:
    exec:
      command: ["python", "ingestor-healthz.py", "--readiness"]

Flags:
  --liveness    heartbeat freshness only (no DB) — cheap, for livenessProbe.
  --readiness   heartbeat + read-only DB ping (default) — for readinessProbe.
  --stale-after SECONDS  heartbeat staleness threshold (default 90).
  --quiet       suppress the status line (exit code only).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Resolve sibling imports (healthz, config) exactly like ingestor.py: CWD is the
# ingestor dir in the container, but support running from elsewhere too.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
_REPO_ROOT = _HERE.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from healthz import DEFAULT_STALE_AFTER_S, check_health  # noqa: E402


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Verdify ingestor health probe (write-free).")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--liveness",
        action="store_true",
        help="Heartbeat freshness only (no DB ping).",
    )
    mode.add_argument(
        "--readiness",
        action="store_true",
        help="Heartbeat + read-only DB ping (default).",
    )
    p.add_argument(
        "--stale-after",
        type=float,
        default=DEFAULT_STALE_AFTER_S,
        help=f"Heartbeat staleness threshold in seconds (default {DEFAULT_STALE_AFTER_S:.0f}).",
    )
    p.add_argument("--quiet", action="store_true", help="Exit code only, no status line.")
    return p.parse_args(argv)


async def _run(args: argparse.Namespace) -> int:
    # Liveness => heartbeat only. Default/readiness => also ping the DB (read-only).
    check_database = not args.liveness
    result = await check_health(
        check_database=check_database,
        stale_after_s=args.stale_after,
    )
    if not args.quiet:
        print(result.summary())
    return 0 if result.ok else 1


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
