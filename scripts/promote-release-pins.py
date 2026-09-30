#!/usr/bin/env python3
"""Promote pin-actuator build candidates to production release pins (#808).

The verdify-platform-ci pin actuator writes each build's api, mcp, ingestor,
migrate and experiment-v2-orchestrator digests to the `images:` block of
deploy/k8s/overlays/prod/kustomization.yaml. Those are build candidates; the prod
render runs deploy/k8s/overlays/prod/release-pins.yaml. This script copies the
candidates into the release pins and prints each change. Commit the result as a
digest-only change, merge it, then run the gated verdify-prod-dark sync in the
same attended session (docs/runbooks/laptop-operator.md §2).

    python3 scripts/promote-release-pins.py                 # all five images
    python3 scripts/promote-release-pins.py verdify-mcp     # only the named ones

Promote all five together unless you know the schema allows otherwise: the
migrate hook carries the migrations the api, mcp and ingestor digests expect.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PROD = ROOT / "deploy/k8s/overlays/prod"
CANONICAL = "ghcr.io/verdifyconsultancy/"
ZOT = "registry.vallery.net/verdifyconsultancy/"
IMAGES = (
    "verdify-api",
    "verdify-mcp",
    "verdify-ingestor",
    "verdify-migrate",
    "verdify-experiment-v2-orchestrator",
)


def source_sha() -> str:
    """Bind a receipt to clean tracked source, not an arbitrary caller label."""
    for args in (("diff", "--quiet"), ("diff", "--cached", "--quiet")):
        subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("receipt requires an exact Git source SHA")
    return sha


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", nargs="*")
    parser.add_argument("--receipt", type=Path, help="Create a new immutable source-only JSON receipt; never overwrite")
    args = parser.parse_args(argv)
    selected = args.images or list(IMAGES)
    unknown = sorted(set(selected) - set(IMAGES))
    if unknown:
        print(f"not an actuator-managed image: {', '.join(unknown)}", file=sys.stderr)
        return 2
    candidate_bytes = (PROD / "kustomization.yaml").read_bytes()
    kustomization = yaml.safe_load(candidate_bytes)
    candidates = {
        row["name"].removeprefix(CANONICAL): row["digest"]
        for row in kustomization["images"]
        if row["name"].removeprefix(CANONICAL) in IMAGES
    }
    pins_path = PROD / "release-pins.yaml"
    text = pins_path.read_text()
    before = text
    rollback_pins = {}
    for image in IMAGES:
        pattern = re.compile(rf"(  name: {re.escape(ZOT + image)}\n  digest: )(sha256:[0-9a-f]{{64}})\n")
        matches = list(pattern.finditer(before))
        if len(matches) != 1:
            print(f"{image}: exactly one valid release pin required", file=sys.stderr)
            return 1
        rollback_pins[image] = matches[0].group(2)
    changes = {}
    for image in selected:
        pattern = re.compile(rf"(  name: {re.escape(ZOT + image)}\n  digest: )(sha256:[0-9a-f]{{64}})\n")
        match = pattern.search(text)
        if match is None or image not in candidates:
            print(f"{image}: release pin or candidate not found", file=sys.stderr)
            return 1
        old, new = match.group(2), candidates[image]
        if not isinstance(new, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", new) is None:
            print(f"{image}: invalid candidate digest; release pins unchanged", file=sys.stderr)
            return 1
        text = text[: match.start(2)] + new + text[match.end(2) :]
        changes[image] = {"before": old, "after": new}
    receipt = None
    if args.receipt:
        try:
            receipt = {
                "schema": "verdify-release-pin-promotion-v1",
                "recorded_at": datetime.now(UTC).isoformat(),
                "promotion_input_source_sha": source_sha(),
                "candidate_file_sha256": hashlib.sha256(candidate_bytes).hexdigest(),
                "release_pins_before_sha256": hashlib.sha256(before.encode()).hexdigest(),
                "release_pins_after_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "changes": changes,
                "rollback_pins": rollback_pins,
                "evidence_scope": "source-only pin promotion; build provenance, CI and live adoption remain separate",
            }
            # Reserve before any pin mutation. Existing/unwritable receipts fail
            # closed; an interrupted write leaves an incomplete receipt, never
            # an invented complete one or a overwritten previous receipt.
            output = args.receipt.open("x")
        except (OSError, ValueError, subprocess.CalledProcessError) as exc:
            print(f"receipt refused; release pins unchanged: {exc}", file=sys.stderr)
            return 1
    else:
        output = None
    try:
        pins_path.write_text(text)
        if output is not None:
            json.dump(receipt, output, sort_keys=True, indent=2)
            output.write("\n")
    finally:
        if output is not None:
            output.close()
    for image, change in changes.items():
        old, new = change["before"], change["after"]
        print(f"{image}: {old[:19]} -> {new[:19]}" if old != new else f"{image}: unchanged {new[:19]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
