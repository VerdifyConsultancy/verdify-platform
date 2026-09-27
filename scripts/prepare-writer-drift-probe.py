#!/usr/bin/env python3
"""Build a one-shot #433 cfg-drift approval from the running writer preview.

This writes only a local manifest. Atomically place it in the same ingestor
pod's /srv/verdify/state/writer-drift-approval.json to arm the existing writer.
"""

from __future__ import annotations

import argparse
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path


def prepare(preview: dict, now: datetime) -> dict:
    captured = datetime.fromisoformat(preview["captured_at"])
    plan_expiry = datetime.fromisoformat(preview["earliest_plan_expiry"])
    if captured.tzinfo is None or plan_expiry.tzinfo is None:
        raise ValueError("preview timestamps must have an offset")
    if preview.get("version") != 1 or preview.get("parameter") != "outdoor_staleness_max_s":
        raise ValueError("not a supported drift preview")
    if now - captured > timedelta(minutes=6) or captured > now + timedelta(seconds=15):
        raise ValueError("drift preview stale or future-dated")
    if len(preview.get("readbacks", {})) < 48:
        raise ValueError("drift preview lacks canonical cfg baseline")
    desired = float(preview["desired"])
    if not 150 <= desired <= 1800 or desired % 30 or float(preview["probe_value"]) != desired - 30:
        raise ValueError("drift step outside outdoor-data freshness Number grid")
    if abs(float(preview["readbacks"]["outdoor_staleness_max_s"]) - desired) > 1e-5:
        raise ValueError("cfg already differs from source-owned desired")
    expires = min(now + timedelta(minutes=10), plan_expiry - timedelta(minutes=1))
    if expires <= now + timedelta(minutes=2):
        raise ValueError("active plan expires too soon")
    return {
        "version": 1,
        "run_id": uuid.uuid4().hex,
        "parameter": "outdoor_staleness_max_s",
        "session_id": preview["session_id"],
        "generation": preview["generation"],
        "fingerprint": preview["fingerprint"],
        "captured_at": preview["captured_at"],
        "expires_at": expires.isoformat(),
        "authority": "one_field_cfg_drift_proof",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("preview", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    approval = prepare(json.loads(args.preview.read_text()), datetime.now(UTC))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(approval, handle, sort_keys=True, indent=2)
        handle.write("\n")
    print(f"prepared run_id={approval['run_id']} expires_at={approval['expires_at']} output={args.output}")


if __name__ == "__main__":
    main()
