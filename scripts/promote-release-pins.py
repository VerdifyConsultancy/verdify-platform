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

import re
import sys
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


def main(argv: list[str]) -> int:
    selected = argv or list(IMAGES)
    unknown = sorted(set(selected) - set(IMAGES))
    if unknown:
        print(f"not an actuator-managed image: {', '.join(unknown)}", file=sys.stderr)
        return 2
    kustomization = yaml.safe_load((PROD / "kustomization.yaml").read_text())
    candidates = {
        row["name"].removeprefix(CANONICAL): row["digest"]
        for row in kustomization["images"]
        if row["name"].removeprefix(CANONICAL) in IMAGES
    }
    pins_path = PROD / "release-pins.yaml"
    text = pins_path.read_text()
    for image in selected:
        pattern = re.compile(rf"(  name: {re.escape(ZOT + image)}\n  digest: )(sha256:[0-9a-f]{{64}})\n")
        match = pattern.search(text)
        if match is None or image not in candidates:
            print(f"{image}: release pin or candidate not found", file=sys.stderr)
            return 1
        old, new = match.group(2), candidates[image]
        text = text[: match.start(2)] + new + text[match.end(2) :]
        print(f"{image}: {old[:19]} -> {new[:19]}" if old != new else f"{image}: unchanged {new[:19]}")
    pins_path.write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
