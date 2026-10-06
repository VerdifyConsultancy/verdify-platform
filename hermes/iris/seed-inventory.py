"""Seed only source-owned Iris files; retain learned skill and run-state custody."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

FILES = {
    "SOUL.md": "SOUL.md",
    "greenhouse-playbook.md": "skills/devops/greenhouse-planning-mcp/references/greenhouse-playbook.md",
    "full-plan-payload-preflight.md": "skills/devops/greenhouse-planning-mcp/references/full-plan-payload-preflight.md",
}


def seed(source: Path, home: Path) -> dict:
    # Read all required inputs before altering a profile. A missing source must
    # never leave a partial persona/reference update.
    inputs = {name: (source / name).read_bytes() for name in (*FILES, "SKILL.md")}
    skill = home / "skills/devops/greenhouse-planning-mcp/SKILL.md"
    preserved = skill.exists()
    for name, relative in FILES.items():
        destination = home / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(inputs[name])
    if not preserved:
        skill.write_bytes(inputs["SKILL.md"])
    # Hashes identify installed public source content; no environment, model
    # prompts, tokens or historical learned content is written to the receipt.
    return {
        "schema": "verdify-iris-inventory-v1",
        "skill_name": "greenhouse-planning-mcp",
        "learned_skill_preserved": preserved,
        "sha256": {relative: hashlib.sha256(inputs[name]).hexdigest() for name, relative in FILES.items()},
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("/etc/verdify/iris-inventory"))
    parser.add_argument("--home", type=Path, default=Path("/opt/data"))
    args = parser.parse_args()
    print(json.dumps(seed(args.source, args.home), sort_keys=True))
