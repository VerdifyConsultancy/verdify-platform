#!/usr/bin/env python3
"""CLI wrapper for descriptor-bound candidate promotion and recovery."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
from verdify_public.atomic_directory import (
    cleanup_stale_candidates,
    discard_candidate,
    promote,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("staged", nargs="?", type=Path)
    parser.add_argument("live", nargs="?", type=Path)
    parser.add_argument("--cleanup-stale", type=Path)
    parser.add_argument("--discard-candidate", type=Path)
    parser.add_argument("--min-age-seconds", type=int, default=3600)
    args = parser.parse_args()
    try:
        if args.cleanup_stale is not None:
            if (
                args.staged is not None
                or args.live is not None
                or args.discard_candidate is not None
                or args.min_age_seconds < 0
            ):
                raise ValueError("invalid cleanup arguments")
            cleanup_stale_candidates(args.cleanup_stale, min_age_seconds=args.min_age_seconds)
        elif args.discard_candidate is not None:
            if args.staged is not None or args.live is not None:
                raise ValueError("invalid discard arguments")
            discard_candidate(args.discard_candidate)
        elif args.staged is None or args.live is None:
            raise ValueError("promotion directories are required")
        else:
            promote(args.staged, args.live)
    except (OSError, ValueError):
        print("atomic directory promotion failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
