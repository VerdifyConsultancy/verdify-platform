#!/usr/bin/env python3
"""Validate the production tier-2 proxy's general-worker HA contract."""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

DEFAULT_MANIFEST = Path("deploy/k8s/overlays/prod/traefik/verdify-traefik-deployment.yaml")


def load_deployment(path: Path) -> dict:
    documents = [document for document in yaml.safe_load_all(path.read_text()) if document]
    for document in documents:
        if document.get("kind") == "Deployment" and document.get("metadata", {}).get("name") == "verdify-traefik":
            return document
    raise ValueError("missing Deployment/verdify-traefik")


def placement_violations(deployment: dict) -> list[str]:
    violations: list[str] = []
    spec = deployment.get("spec", {})
    pod = spec.get("template", {}).get("spec", {})

    expected_selector = {"agentfleet.vallery.net/node-class": "general"}
    if pod.get("nodeSelector") != expected_selector:
        violations.append("verdify-traefik must require exactly the general-worker selector")

    strategy = spec.get("strategy", {})
    rolling = strategy.get("rollingUpdate", {})
    if (
        spec.get("replicas") != 2
        or strategy.get("type") != "RollingUpdate"
        or rolling.get("maxUnavailable") != 0
        or rolling.get("maxSurge") != 1
    ):
        violations.append("verdify-traefik must retain the two-replica zero-unavailable rollout")

    expected_spread = [
        {
            "maxSkew": 1,
            "topologyKey": "kubernetes.io/hostname",
            "whenUnsatisfiable": "DoNotSchedule",
            "matchLabelKeys": ["pod-template-hash"],
            "labelSelector": {"matchLabels": {"app.kubernetes.io/name": "verdify-traefik"}},
        }
    ]
    if pod.get("topologySpreadConstraints") != expected_spread:
        violations.append("verdify-traefik must retain hard hostname spread")

    if any(item.get("key") == "dedicated" for item in pod.get("tolerations", [])):
        violations.append("verdify-traefik must not tolerate a dedicated worker")

    persistent_claims = [
        volume.get("persistentVolumeClaim", {}).get("claimName")
        for volume in pod.get("volumes", [])
        if volume.get("persistentVolumeClaim")
    ]
    if persistent_claims:
        violations.append("verdify-traefik must remain stateless")

    return violations


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", nargs="?", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()

    try:
        deployment = load_deployment(args.manifest)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"verdify-traefik placement validation failed: {exc}")
        return 1

    violations = placement_violations(deployment)
    if violations:
        for violation in violations:
            print(f"verdify-traefik placement violation: {violation}")
        return 1

    print("verdify-traefik placement validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
