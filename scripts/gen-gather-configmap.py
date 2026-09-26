#!/usr/bin/env python3
"""Refresh the two mounted planner scripts without changing ConfigMap metadata."""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIGMAP = Path("deploy/k8s/components/ingestor-gather-script/gather-script-configmap.yaml")
SOURCES = {
    "gather-plan-context.sh": Path("scripts/gather-plan-context.sh"),
    "psql-verdify.sh": Path("scripts/lib/psql-verdify.sh"),
}


def render(text: str, sources: dict[str, str]) -> str:
    """Refuse unexpected structure instead of silently removing other data."""
    existing = yaml.safe_load(text)
    if (
        not isinstance(existing, dict)
        or existing.get("kind") != "ConfigMap"
        or existing.get("metadata", {}).get("name") != "verdify-ingestor-gather-script"
        or set(existing.get("data", {})) != set(SOURCES)
        or set(sources) != set(SOURCES)
    ):
        raise ValueError("unexpected gather ConfigMap structure")
    prefix, marker, _ = text.partition("\ndata:\n")
    if not marker or any(not value.endswith("\n") for value in sources.values()):
        raise ValueError("expected data block and newline-terminated source scripts")
    blocks = []
    for key, value in sources.items():
        body = "".join(line if not line.strip("\r\n") else f"    {line}" for line in value.splitlines(keepends=True))
        blocks.append(f"  {key}: |\n{body}")
    rendered = prefix + marker + "".join(blocks)
    if yaml.safe_load(rendered) != {**existing, "data": sources}:
        raise ValueError("generation would change metadata or script bytes")
    return rendered


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="check only; never write")
    args = parser.parse_args()
    path = ROOT / CONFIGMAP
    original = path.read_text()
    expected = render(original, {key: (ROOT / source).read_text() for key, source in SOURCES.items()})
    if original == expected:
        print("gather ConfigMap matches both source scripts")
        return 0
    if args.check:
        print("stale gather ConfigMap; run scripts/gen-gather-configmap.py then scripts/gen-config-revision.sh")
        return 1
    path.write_text(expected)
    print(f"updated {CONFIGMAP}; run scripts/gen-config-revision.sh before committing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
