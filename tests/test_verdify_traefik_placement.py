from __future__ import annotations

import copy
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "deploy/k8s/overlays/prod/traefik/verdify-traefik-deployment.yaml"
VALIDATOR = ROOT / "scripts/validate-verdify-traefik-placement.py"


def load_documents() -> list[dict]:
    return [document for document in yaml.safe_load_all(SOURCE.read_text()) if document]


def deployment(documents: list[dict]) -> dict:
    return next(
        document
        for document in documents
        if document.get("kind") == "Deployment" and document.get("metadata", {}).get("name") == "verdify-traefik"
    )


def run_validator(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VALIDATOR), str(path)],
        check=False,
        capture_output=True,
        text=True,
    )


def test_source_contract_passes() -> None:
    result = run_validator(SOURCE)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("gpu", "exactly the general-worker selector"),
        ("hard_hostname", "exactly the general-worker selector"),
        ("soft_spread", "retain hard hostname spread"),
        ("allow_unavailable", "two-replica zero-unavailable rollout"),
        ("pvc", "must remain stateless"),
        ("dedicated_toleration", "must not tolerate a dedicated worker"),
    ],
)
def test_negative_placement_contracts(tmp_path: Path, mutation: str, expected: str) -> None:
    documents = copy.deepcopy(load_documents())
    workload = deployment(documents)
    pod = workload["spec"]["template"]["spec"]

    if mutation == "gpu":
        pod["nodeSelector"]["agentfleet.vallery.net/node-class"] = "gpu"
    elif mutation == "hard_hostname":
        pod["nodeSelector"]["kubernetes.io/hostname"] = "vm-k3s-node5"
    elif mutation == "soft_spread":
        pod["topologySpreadConstraints"][0]["whenUnsatisfiable"] = "ScheduleAnyway"
    elif mutation == "allow_unavailable":
        workload["spec"]["strategy"]["rollingUpdate"]["maxUnavailable"] = 1
    elif mutation == "pvc":
        pod["volumes"] = [{"name": "data", "persistentVolumeClaim": {"claimName": "unexpected"}}]
    elif mutation == "dedicated_toleration":
        pod["tolerations"] = [{"key": "dedicated", "operator": "Exists", "effect": "NoSchedule"}]
    else:
        raise AssertionError(f"unhandled mutation: {mutation}")

    candidate = tmp_path / "verdify-traefik.yaml"
    candidate.write_text(yaml.safe_dump_all(documents, sort_keys=False))
    result = run_validator(candidate)

    assert result.returncode != 0
    assert expected in result.stdout + result.stderr
