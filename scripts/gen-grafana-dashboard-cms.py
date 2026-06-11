#!/usr/bin/env python3
"""Regenerate the Grafana dashboard ConfigMaps from dashboard JSON sources.

The live graphs.verdify.ai Grafana provisions its dashboards from
deploy/k8s/components/grafana/generated/dashboards-cm-{0,1,2}.yaml — each holds
a subset of the public dashboards as ConfigMap data keys (split to stay under
the 1 MiB ConfigMap limit). This script refreshes every existing .json data key
from its source file, preserving the existing CM→dashboard assignment (so a JSON
edit re-renders without re-sharding).

Primary site dashboards come from grafana/dashboards/. A small set of legacy
dashboard UIDs are still embedded by lab.verdify.ai and are sourced from
grafana/provisioning/dashboards/json/.

Idempotent: run after editing any provisioned dashboard JSON, then commit the
CMs. Validates each source is parseable JSON and warns if a CM nears the 1 MiB
limit.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "grafana" / "dashboards"
LEGACY_SRC = REPO / "grafana" / "provisioning" / "dashboards" / "json"
GEN = REPO / "deploy" / "k8s" / "components" / "grafana" / "generated"
CM_LIMIT = 1024 * 1024


class LiteralStr(str):
    pass


def _literal_representer(dumper, data):
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")


yaml.add_representer(LiteralStr, _literal_representer)


def source_for_key(key: str) -> Path | None:
    for root in (SRC, LEGACY_SRC):
        src = root / key
        if src.exists():
            return src
    return None


def main() -> int:
    cms = sorted(GEN.glob("dashboards-cm-*.yaml"))
    if not cms:
        print("no dashboards-cm-*.yaml found", file=sys.stderr)
        return 1
    changed = 0
    for cm_path in cms:
        doc = yaml.safe_load(cm_path.read_text())
        data = doc.get("data") or {}
        new_data: dict[str, str] = {}
        for key in data:
            if key.endswith(".json"):
                src = source_for_key(key)
                if src is None:
                    print(f"WARN: {cm_path.name} key {key} has no dashboard source — keeping existing")
                    new_data[key] = data[key]
                    continue
                raw = src.read_text()
                json.loads(raw)  # parse-validate
                new_data[key] = LiteralStr(raw if raw.endswith("\n") else raw + "\n")
            else:
                new_data[key] = LiteralStr(data[key]) if isinstance(data[key], str) and "\n" in data[key] else data[key]
        doc["data"] = new_data
        out = yaml.dump(doc, sort_keys=False, width=10**9, allow_unicode=True)
        size = len(out.encode())
        if size > CM_LIMIT:
            print(f"ERROR: {cm_path.name} is {size} bytes (> 1 MiB limit) — re-shard", file=sys.stderr)
            return 2
        if size > CM_LIMIT * 0.9:
            print(f"WARN: {cm_path.name} is {size} bytes (>90% of 1 MiB)")
        if out != cm_path.read_text():
            cm_path.write_text(out)
            changed += 1
            print(
                f"regenerated {cm_path.name} ({size} bytes, {sum(1 for k in new_data if k.endswith('.json'))} dashboards)"
            )
    print(f"done: {changed} ConfigMap(s) updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
