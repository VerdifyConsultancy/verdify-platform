#!/usr/bin/env python3
"""Approve one fresh, in-pod #433 preview for bounded sole-writer delivery.

This only writes a local JSON manifest. Copy it atomically into the running
ingestor's /srv/verdify/state/writer-stage-approval.json to start a run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path


def prepare(preview: dict, now: datetime) -> dict:
    captured = datetime.fromisoformat(preview["captured_at"])
    expiry = datetime.fromisoformat(preview["earliest_plan_expiry"])
    if captured.tzinfo is None or expiry.tzinfo is None:
        raise ValueError("preview timestamps must include UTC offset")
    if preview.get("version") != 1 or len(preview.get("changes", [])) <= 12:
        raise ValueError("expected a version-1 broad-restore preview")
    if now - captured > timedelta(minutes=6) or captured > now + timedelta(seconds=15):
        raise ValueError("preview is stale or future-dated")
    if len(preview.get("readbacks", {})) < 48:
        raise ValueError("preview lacks the canonical readback baseline")
    deadline = min(now + timedelta(minutes=30), expiry - timedelta(minutes=5))
    if deadline <= now + timedelta(minutes=1):
        raise ValueError("effective plan expires before bounded delivery can begin")
    return {
        "version": 1,
        "run_id": uuid.uuid4().hex,
        "session_id": preview["session_id"],
        "generation": preview["generation"],
        "fingerprint": preview["fingerprint"],
        "approved_at": now.isoformat(),
        "expires_at": deadline.isoformat(),
        "authority": "desired_all_five_layers",
    }


def prepare_rollback(preview: dict, state: dict, now: datetime) -> dict:
    captured = datetime.fromisoformat(preview["captured_at"])
    if now - captured > timedelta(minutes=6) or captured > now + timedelta(seconds=15):
        raise ValueError("rollback preview is stale or future-dated")
    if state.get("status") != "halted":
        raise ValueError("rollback requires a halted bounded run")
    parameters = list(state.get("stage_parameters", []))
    if not 1 <= len(parameters) <= 12:
        raise ValueError("rollback covers exactly one prior bounded stage")
    baseline = state["approved_preview"]["readbacks"]
    identity = {
        "source_run_id": state["run_id"],
        "session_id": preview["session_id"],
        "generation": preview["generation"],
        "parameters": parameters,
        "baseline": {param: baseline[param] for param in parameters},
        "observed": {param: preview["readbacks"][param] for param in parameters},
    }
    fingerprint = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    return {
        "version": 1,
        "source_run_id": state["run_id"],
        "session_id": preview["session_id"],
        "generation": preview["generation"],
        "parameters": parameters,
        "fingerprint": fingerprint,
        "approved_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=10)).isoformat(),
        "authority": "last_stage_baseline_rollback",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("preview", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--rollback-state", type=Path, help="halted state receipt for bounded last-stage rollback")
    args = parser.parse_args()
    preview = json.loads(args.preview.read_text())
    approval = (
        prepare_rollback(preview, json.loads(args.rollback_state.read_text()), datetime.now(UTC))
        if args.rollback_state
        else prepare(preview, datetime.now(UTC))
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(approval, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(
        f"prepared run_id={approval.get('run_id', approval.get('source_run_id'))} "
        f"expires_at={approval['expires_at']} output={args.output}"
    )


if __name__ == "__main__":
    main()
