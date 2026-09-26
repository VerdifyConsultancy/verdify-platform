#!/usr/bin/env python3
"""Reject inert Verdify PrometheusRule CRs; optionally verify the rule-file owner."""

import argparse
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
OWNER_FILES = Path("deploy/observability/prometheus")


def fail(message: str) -> int:
    sys.stderr.write(f"{message}\n")
    return 1


def document(path: Path) -> dict | None:
    try:
        value = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError):
        return None
    return value if isinstance(value, dict) else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deploy-root", type=Path, default=REPO_ROOT / "deploy/k8s")
    parser.add_argument(
        "--monitoring-root",
        type=Path,
        help="also check the owning monitoring-stack ConfigMap source (when available)",
    )
    args = parser.parse_args()

    deploy_root = args.deploy_root
    if not deploy_root.is_dir():
        return fail(f"missing Verdify deploy path: {deploy_root}")
    files = sorted((*deploy_root.rglob("*.yaml"), *deploy_root.rglob("*.yml")))
    for path in files:
        display = path.relative_to(deploy_root.parent.parent)
        try:
            documents = yaml.safe_load_all(path.read_text())
            for index, item in enumerate(documents, 1):
                if isinstance(item, dict) and item.get("kind") == "PrometheusRule":
                    return fail(f"inert PrometheusRule: {display}: document {index}")
        except (OSError, yaml.YAMLError):
            return fail(f"invalid Verdify manifest YAML: {display}")

    if args.monitoring_root is not None:
        owner = args.monitoring_root / OWNER_FILES
        config_path = owner / "prometheus-config.yaml"
        rules_path = owner / "prometheus-rules.yaml"
        for path in (config_path, rules_path):
            if not path.is_file():
                return fail(f"missing monitoring owner path: {path}")
        config = document(config_path)
        rules = document(rules_path)
        if not config or not rules:
            return fail("invalid monitoring owner ConfigMap YAML")
        for item, expected in ((config, "prometheus-config"), (rules, "prometheus-rules")):
            metadata = item.get("metadata") or {}
            if (
                item.get("kind") != "ConfigMap"
                or not isinstance(metadata, dict)
                or metadata.get("name") != expected
                or metadata.get("namespace") != "observability"
            ):
                return fail(f"invalid monitoring owner ConfigMap: {expected}")
        try:
            prometheus_config = yaml.safe_load((config.get("data") or {}).get("prometheus.yml") or "")
        except yaml.YAMLError:
            return fail("invalid monitoring owner prometheus.yml")
        if not isinstance(prometheus_config, dict) or "/etc/prometheus/rules/*.yml" not in prometheus_config.get(
            "rule_files", []
        ):
            return fail("monitoring owner does not load /etc/prometheus/rules/*.yml")
        if "verdify-backup.yml" not in (rules.get("data") or {}):
            return fail("monitoring owner lacks verdify-backup.yml rule-file source")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
