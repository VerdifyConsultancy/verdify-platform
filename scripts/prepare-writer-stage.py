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
    if preview.get("version") != 1 or not preview.get("changes"):
        raise ValueError("expected a nonempty version-1 bounded delivery preview")
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
        # Retain the approved vector so the running writer can verify a fresh
        # candidate after the four solar-time VPD targets naturally move.
        "approved_preview": preview,
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


def prepare_forward(preview: dict, custody: dict, requests: list[dict], writer_custody: dict, now: datetime) -> dict:
    """Prepare explicit archive authority, never setter authority or a reset."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ingestor"))
    from tasks import bounded_reconcile as bounded

    # Use the same freshness/canonical baseline checks as a new stage proposal.
    prepare(preview, now)
    state = json.loads(custody[bounded.STATE_NAME])
    approval = json.loads(custody[bounded.APPROVAL_NAME])
    bounded._confirmed_halt_shape(state, approval)
    bounded._confirmed_halt_writer_custody(state, writer_custody)
    native = bounded._forward_request_receipt(requests, state, now)
    return {
        "version": 2,
        "authority": bounded.FORWARD_AUTHORITY,
        # Archive historical successful effects independently of desired
        # waypoint activation. This new binding grants no setter authority.
        "current_plan_rows": preview["plan_rows"],
        "source_run_id": state["run_id"],
        "original_writer_custody": writer_custody,
        "writer_custody_digest": bounded._digest(writer_custody),
        "state_digest": bounded._digest(state),
        "approval_digest": bounded._digest(approval),
        "original_approval": approval,
        "custody": custody,
        "custody_sha256": bounded._digest(custody),
        "native_requests": native,
        "observed_readbacks": preview["readbacks"],
        "current_identity": {key: preview[key] for key in ("source_revision", "session_id", "pod", "generation")},
        "approved_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=6)).isoformat(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("preview", type=Path)
    parser.add_argument("output", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--rollback-state", type=Path, help="halted state receipt for bounded last-stage rollback")
    mode.add_argument(
        "--confirmed-halt-custody", type=Path, help="raw original active-file custody for confirmed forward archive"
    )
    parser.add_argument(
        "--native-requests", type=Path, help="read-only export of the exact original confirmed requests"
    )
    parser.add_argument(
        "--original-writer-custody", type=Path, help="original full Pod and same-identity preview operator receipt"
    )
    args = parser.parse_args()
    preview = json.loads(args.preview.read_text())
    if args.confirmed_halt_custody:
        if not args.native_requests or not args.original_writer_custody:
            parser.error("confirmed halt requires --native-requests and --original-writer-custody")
        approval = prepare_forward(
            preview,
            json.loads(args.confirmed_halt_custody.read_text()),
            json.loads(args.native_requests.read_text()),
            json.loads(args.original_writer_custody.read_text()),
            datetime.now(UTC),
        )
    else:
        if args.native_requests or args.original_writer_custody:
            parser.error("native request custody is only valid with --confirmed-halt-custody")
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
