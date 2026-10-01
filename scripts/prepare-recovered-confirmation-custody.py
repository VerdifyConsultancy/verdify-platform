#!/usr/bin/env python3
"""Prepare LOCAL custody for a stopped recovered stage; never access or change devices/DB."""

import argparse
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path


def prepare(state_path, approval_path, recovery_path, terminal_path, requests_path):
    paths = [state_path, approval_path, recovery_path]
    state, approval, recovery = [json.loads(p.read_text()) for p in paths]
    raw = terminal_path.read_text()
    proof = json.loads(raw)
    requests = json.loads(requests_path.read_text())
    parameters = state["stage_parameters"]
    assert state["status"] == proof["status"] == "rollback_complete"
    assert 1 <= len(parameters) <= 12
    assert state["run_id"] == approval["run_id"] == recovery["source_run_id"] == proof["run_id"]
    assert recovery["parameters"] == parameters
    assert proof["source"] == state["approved_preview"]["source_revision"]
    assert proof["session"] == recovery["session_id"] and proof["generation"] == recovery["generation"]
    assert {r["parameter"] for r in proof["stage12_baseline"]} == set(parameters)
    assert {r["parameter"] for r in proof["first12_preserved"]} == set(state["completed_values"])
    assert all(r["matches"] for r in proof["stage12_baseline"] + proof["first12_preserved"])

    def normalize(rows):
        return [
            {
                k: datetime.fromisoformat(v.replace("Z", "+00:00")).isoformat()
                if k in {"ts", "confirmed_at", "expired_at"} and v is not None
                else v
                for k, v in row.items()
            }
            for row in rows
        ]

    original = normalize(requests["original_requests"])
    rollback = normalize(requests["rollback_confirmations"])
    assert len(original) == len(parameters) and {r["parameter"] for r in original} == set(parameters)
    assert len(rollback) == len(state["rollback_records"]) > 0
    for record in state["rollback_records"]:
        matches = [r for r in rollback if r["parameter"] == record["parameter"] and r["ts"] == record["requested_at"]]
        assert len(matches) == 1 and matches[0]["value"] == record["value"]
        assert matches[0]["delivery_status"] == "confirmed" and matches[0]["confirmed_at"]
    return {
        "schema": "verdify-recovered-confirmation-custody-v1",
        "run_id": state["run_id"],
        "source_revision": proof["source"],
        "recovery_session_id": proof["session"],
        "recovery_generation": proof["generation"],
        "firmware_version": proof["diagnostics"]["firmware_version"],
        "retained_sha256": {
            name: hashlib.sha256(p.read_bytes()).hexdigest()
            for name, p in zip(
                ["writer-stage-state.json", "writer-stage-approval.json", "writer-stage-recovery.json"],
                paths,
                strict=True,
            )
        },
        "terminal_proof_raw": raw,
        "terminal_proof_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "original_requests": original,
        "rollback_confirmations": rollback,
        "meaning": "Close elapsed confirmation window only; no inferred physical delivery or failure and no device authority",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["state", "approval", "recovery", "terminal", "requests", "output"]:
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    result = prepare(args.state, args.approval, args.recovery, args.terminal, args.requests)
    fd = os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(result, f, indent=2, sort_keys=True)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    print("Prepared LOCAL lifecycle custody: " + str(args.output))
