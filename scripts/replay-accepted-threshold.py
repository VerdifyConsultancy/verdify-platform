#!/usr/bin/env python3
"""Print the replay-diff THRESHOLD_PCT a reviewed divergence record allows at HEAD.

The firmware replay gate in scripts/ci-local.sh runs at THRESHOLD_PCT=0. A
firmware change whose divergence was reviewed and accepted (for example the
40/100 °F guardrails, #1333/#1405) commits firmware/test/replay-accepted-divergence.json
with a ``logic_sha256`` over exactly the files the trigger check diffs. This
script prints that record's ``max_pct`` only when the hash matches the files in
the working tree; otherwise it prints 0. Any later edit to a firmware-logic file
changes the hash, so a record can never be reused for a different change.

Usage: replay-accepted-threshold.py [--record PATH] [--root DIR]
Prints the threshold on stdout; the ACCEPTED DIVERGENCE notice goes to stderr.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

# Same pathspecs as the ci-local.sh replay trigger check.
LOGIC_PATHSPECS = (
    "firmware/lib/*.h",
    "firmware/lib/*.cpp",
    "firmware/greenhouse/controls.yaml",
    "firmware/greenhouse/tunables.yaml",
    "firmware/greenhouse/globals.yaml",
)
DEFAULT_RECORD = "firmware/test/replay-accepted-divergence.json"


def logic_files(root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", *LOGIC_PATHSPECS],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout.decode()
    return sorted(name for name in out.split("\0") if name)


def logic_sha256(root: Path, files: list[str] | None = None) -> str:
    """SHA-256 over the sorted firmware-logic files: path, NUL, length, NUL, bytes, per file."""
    digest = hashlib.sha256()
    for name in sorted(files if files is not None else logic_files(root)):
        data = (root / name).read_bytes()
        digest.update(name.encode() + b"\0" + str(len(data)).encode() + b"\0" + data)
    return digest.hexdigest()


def accepted_threshold(root: Path, record_path: Path) -> tuple[float, str | None]:
    """Return (threshold, reason); (0.0, None) unless the record exists and binds this exact logic."""
    if not record_path.is_file():
        return 0.0, None
    record = json.loads(record_path.read_text())
    if record.get("logic_sha256") != logic_sha256(root):
        return 0.0, None
    max_pct = float(record["max_pct"])
    if not 0.0 <= max_pct <= 100.0:
        raise ValueError(f"max_pct out of range: {max_pct}")
    return max_pct, str(record.get("reason", ""))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=".")
    parser.add_argument("--record", default=None)
    parser.add_argument("--print-hash", action="store_true", help="print logic_sha256 at the root and exit")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    if args.print_hash:
        print(logic_sha256(root))
        return 0
    record = Path(args.record) if args.record else root / DEFAULT_RECORD
    threshold, reason = accepted_threshold(root, record)
    if reason is not None:
        print(
            f"ACCEPTED DIVERGENCE: replay threshold {threshold}% from {record.name} "
            f"(logic_sha256 matches HEAD). Reason: {reason}",
            file=sys.stderr,
        )
    elif record.is_file():
        print(f"replay record {record.name} does not match HEAD's firmware logic; threshold stays 0%", file=sys.stderr)
    print(f"{threshold:g}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
