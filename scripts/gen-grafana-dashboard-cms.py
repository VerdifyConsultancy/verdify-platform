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

`--check` (the #392 CI gate) renders every CM in-memory and compares
byte-for-byte against the committed file — writes nothing, exits 1 on drift
(a dashboard JSON edited without regenerating, or a hand-edited CM data key).
"""

from __future__ import annotations

import argparse
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


def render_cm(cm_path: Path) -> tuple[str, int]:
    """Re-render cm_path's .json data keys from their sources; return (yaml text, dashboard count)."""
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
    return out, sum(1 for k in new_data if k.endswith(".json"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify committed CMs match a fresh render from dashboard sources; write nothing, exit 1 on drift",
    )
    args = parser.parse_args(argv)

    cms = sorted(GEN.glob("dashboards-cm-*.yaml"))
    if not cms:
        print("no dashboards-cm-*.yaml found", file=sys.stderr)
        return 1
    changed = 0
    drifted: list[str] = []
    for cm_path in cms:
        out, n_dashboards = render_cm(cm_path)
        size = len(out.encode())
        if size > CM_LIMIT:
            print(f"ERROR: {cm_path.name} is {size} bytes (> 1 MiB limit) — re-shard", file=sys.stderr)
            return 2
        if size > CM_LIMIT * 0.9:
            print(f"WARN: {cm_path.name} is {size} bytes (>90% of 1 MiB)")
        if out != cm_path.read_text():
            if args.check:
                drifted.append(cm_path.name)
            else:
                cm_path.write_text(out)
                changed += 1
                print(f"regenerated {cm_path.name} ({size} bytes, {n_dashboards} dashboards)")
    if args.check:
        if drifted:
            print(
                f"DRIFT: {', '.join(drifted)} out of sync with grafana dashboard JSON sources.",
                file=sys.stderr,
            )
            print(
                "Fix: python3 scripts/gen-grafana-dashboard-cms.py  (then commit the regenerated ConfigMaps)",
                file=sys.stderr,
            )
            return 1
        print(f"check OK: {len(cms)} ConfigMap(s) match dashboard sources")
        return 0
    print(f"done: {changed} ConfigMap(s) updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
