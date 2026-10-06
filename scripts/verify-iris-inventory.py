#!/usr/bin/env python3
"""Read-only in-container Iris inventory; never invoke a model or expose content."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path


def inventory(home: Path) -> dict:
    skill = home / "skills/devops/greenhouse-planning-mcp/SKILL.md"
    refs = sorted(set(re.findall(r"references/[A-Za-z0-9_.-]+\.md", skill.read_text())))
    required = ["references/greenhouse-playbook.md", "references/full-plan-payload-preflight.md"]
    paths = [home / "config.yaml", home / "SOUL.md", skill]
    paths += [skill.parent / ref for ref in sorted(set(refs + required))]
    missing = [str(path.relative_to(home)) for path in paths if not path.is_file()]
    hashes = {
        str(path.relative_to(home)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths if path.is_file()
    }
    indexes = {
        str(path.relative_to(home)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((home / "skills").rglob("SKILL.md"))
    }
    sys.path.insert(0, "/opt/hermes")
    from tools.skills_tool import skill_view

    # Resolve using the same pinned implementation as the running worker.
    # Disable shell/template preprocessing; no tool wrapper or usage bump.
    resolutions = {}
    for ref in [None, *sorted(set(refs + required))]:
        response = json.loads(skill_view("greenhouse-planning-mcp", ref, preprocess=False))
        resolutions[ref or "SKILL.md"] = {
            "success": response.get("success", False),
            **({"error": response["error"]} if "error" in response else {}),
        }
    return {
        "schema": "verdify-iris-runtime-inventory-v1",
        "home": str(home),
        "skill_name": "greenhouse-planning-mcp",
        "missing": missing,
        "sha256": hashes,
        "skill_index_sha256": hashlib.sha256(json.dumps(indexes, sort_keys=True).encode()).hexdigest(),
        "skill_index_count": len(indexes),
        "resolutions": resolutions,
        "qualified": not missing and all(r["success"] for r in resolutions.values()),
        "plan_success": "not established by inventory; requires actual trigger/plan ledger",
    }


if __name__ == "__main__":
    receipt = inventory(Path(os.environ.get("HERMES_HOME", "/opt/data")))
    print(json.dumps(receipt, sort_keys=True))
    raise SystemExit(0 if receipt["qualified"] else 1)
