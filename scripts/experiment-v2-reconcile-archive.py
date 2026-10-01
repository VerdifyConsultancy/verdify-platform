#!/usr/bin/env python3
"""Index every retained blinded daily receipt offline; never connects to services."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from uuid import UUID

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "research/planner-efficacy/protocols/daily-reconciliation-v1.schema.json"
SPEC = importlib.util.spec_from_file_location("daily_reconciliation", ROOT / "scripts/experiment-v2-reconcile.py")
RECONCILE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RECONCILE)
DOMAIN = b"verdify-experiment-v2-reconciliation-archive-v1\0"


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def index_archive(directory: Path, experiment_id: str) -> dict:
    experiment_id = str(UUID(experiment_id))
    validator = jsonschema.Draft202012Validator(
        json.loads(SCHEMA_PATH.read_bytes()), format_checker=jsonschema.FormatChecker()
    )
    entries = []
    snapshots = {}
    locks = set()
    for path in sorted(directory.glob("receipt-*.json")):
        # Keep originals and duplicate files; refuse links rather than reading outside custody.
        if path.is_symlink() or not path.is_file():
            raise ValueError("receipt must be a regular owned archive file")
        if path.stat().st_size > 2_000_000:
            raise ValueError("receipt exceeds bounded archive contract")
        raw = path.read_bytes()
        value = json.loads(raw)
        validator.validate(value)
        receipt = value["receipt"]
        expected = hashlib.sha256(RECONCILE.DOMAIN + canonical(receipt)).hexdigest()
        if value["sha256"] != expected or receipt["experiment_id"] != experiment_id:
            raise ValueError("receipt hash or experiment differs from archive")
        stamp = datetime.fromisoformat(receipt["as_of"])
        if stamp.tzinfo is None:
            raise ValueError("receipt snapshot timestamp lacks offset")
        if stamp in snapshots and snapshots[stamp] != expected:
            raise ValueError("conflicting receipts for one snapshot instant")
        snapshots[stamp] = expected
        if receipt["design_lock_sha256"] is not None:
            locks.add(receipt["design_lock_sha256"])
        entries.append(
            {
                "file": path.name,
                "file_sha256": hashlib.sha256(raw).hexdigest(),
                "receipt_sha256": expected,
                "as_of": receipt["as_of"],
                "status": receipt["status"],
                "observed_assignments": receipt["observed_assignments"],
                "locked_assigned_day_denominator": receipt["locked_assigned_day_denominator"],
                "export_verified": receipt["export_verified"],
                "export_sha256": receipt["export_sha256"],
            }
        )
    if len(locks) > 1:
        raise ValueError("multiple design locks in one study archive; preserve and separate custody")
    entries.sort(key=lambda item: (datetime.fromisoformat(item["as_of"]), item["file"]))
    return {
        "schema": "verdify-experiment-v2-reconciliation-archive-v1",
        "experiment_id": experiment_id,
        "entries": entries,
        "retained_file_count": len(entries),
        "unique_snapshot_count": len(snapshots),
        "design_lock_sha256": next(iter(locks)) if locks else None,
        "latest_snapshot": entries[-1]["as_of"] if entries else None,
        "receipt_schema_sha256": hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest(),
        "index_tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "reconciler_tool_sha256": hashlib.sha256(
            (ROOT / "scripts/experiment-v2-reconcile.py").read_bytes()
        ).hexdigest(),
        "claim_scope": "archive integrity only; no assigned outcome, completeness, launch, reveal or pilot decision claim",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--experiment-id", required=True, type=UUID)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not args.directory.is_dir():
        raise ValueError("existing archive directory required")
    payload = index_archive(args.directory, str(args.experiment_id))
    wrapper = {"manifest": payload, "sha256": hashlib.sha256(DOMAIN + canonical(payload)).hexdigest()}
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(canonical(wrapper).decode() + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(args.output.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, jsonschema.ValidationError) as exc:
        print(f"archive refused: {type(exc).__name__}", file=sys.stderr)
        sys.exit(2)
