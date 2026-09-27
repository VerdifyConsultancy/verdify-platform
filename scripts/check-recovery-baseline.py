#!/usr/bin/env python3
"""Read-only comparison of study baseline with one #433 desired-policy preview.

Refuses a stale or incomplete preview, any mismatch in the canonical vector,
or a desired command outside it. Does not use or print database credentials.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from verdify_schemas.component_executor import CANONICAL_FIELD_ORDER
from verdify_schemas.policy_vector import decode_policy_vector, encode_policy_vector

EXPERIMENT_ID = "45039c86-c1d9-52f6-a0a9-d94a17bc4b14"
REVISION = "3e26a2da1863bd14d255d58fe00d8e94aae9226f9b588ddd9d4f993e6bcb7016"


def compare(preview: dict, baseline_hex: str, now: datetime) -> tuple[list[str], bool]:
    if preview.get("version") != 1 or not isinstance(preview.get("readbacks"), dict):
        raise ValueError("invalid #433 preview")
    captured = datetime.fromisoformat(preview["captured_at"].replace("Z", "+00:00"))
    if captured.tzinfo is None:
        raise ValueError("preview lacks timezone")
    stale = captured > now + timedelta(seconds=15) or now - captured > timedelta(minutes=6)
    canonical = set(CANONICAL_FIELD_ORDER)
    readbacks = preview["readbacks"]
    if not canonical <= set(readbacks):
        raise ValueError(f"preview lacks {len(canonical - set(readbacks))} canonical readbacks")
    baseline_vector = bytes.fromhex(baseline_hex)
    baseline = decode_policy_vector(baseline_vector)
    desired = {name: readbacks[name] for name in CANONICAL_FIELD_ORDER}
    changed: set[str] = set()
    outside: list[str] = []
    for row in preview.get("changes", []):
        if not isinstance(row, list) or len(row) != 2 or row[0] in changed:
            raise ValueError("invalid or duplicate desired change")
        field, value = row
        changed.add(field)
        if field in canonical:
            desired[field] = value
        else:
            outside.append(field)
    desired_vector = encode_policy_vector(desired)
    desired_normalized = decode_policy_vector(desired_vector)
    unrepresentable = [
        f"off_grid_desired: {name} desired={desired[name]} wire={desired_normalized[name]}"
        for name in CANONICAL_FIELD_ORDER
        if not math.isclose(float(desired[name]), float(desired_normalized[name]), rel_tol=0, abs_tol=1e-6)
    ]
    mismatches = [
        f"{name}: frozen={baseline[name]} desired={desired_normalized[name]}"
        for name in CANONICAL_FIELD_ORDER
        if baseline[name] != desired_normalized[name]
    ]
    mismatches.extend(unrepresentable)
    mismatches.extend(f"outside_48: {name}" for name in outside)
    return mismatches, stale


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("preview", type=Path)
    args = parser.parse_args()
    preview = json.loads(args.preview.read_text())
    sql = (
        "SELECT encode(wire_vector,'hex') FROM public.experiment_v2_state_artifacts "
        f"WHERE experiment_id='{EXPERIMENT_ID}' AND revision_bundle_sha256='{REVISION}' "
        "AND profile='baseline'"
    )
    repo = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [str(repo / "scripts/verdify-db.sh"), "prod", "-t", "-A", "-c", sql],
        cwd=repo,
        text=True,
        capture_output=True,
        check=True,
    )
    rows = result.stdout.splitlines()
    if len(rows) != 1:
        raise RuntimeError("expected exactly one frozen study baseline")
    mismatches, stale = compare(preview, rows[0].strip(), datetime.now(UTC))
    print(f"preview_captured_at={preview['captured_at']}")
    print(f"canonical_mismatch_count={sum(': frozen=' in row for row in mismatches)}")
    print(f"off_grid_desired_count={sum(row.startswith('off_grid_desired:') for row in mismatches)}")
    print(f"outside_48_desired_command_count={sum(row.startswith('outside_48:') for row in mismatches)}")
    for row in mismatches:
        print(row)
    if stale:
        print("preview_stale=true")
    if mismatches or stale:
        raise SystemExit(1)
    print("baseline_matches_fresh_desired_all_layers=true")


if __name__ == "__main__":
    main()
