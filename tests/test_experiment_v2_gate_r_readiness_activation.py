"""Fail-closed contracts for the recovery-only Gate R readiness hook."""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PROD = ROOT / "deploy/k8s/overlays/prod"
ACTIVATION = ROOT / "deploy/k8s/activations/experiment-v2-gate-r-readiness"


def _render(path: Path) -> list[dict]:
    completed = subprocess.run(
        ["kustomize", "build", str(path)],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    return [row for row in yaml.safe_load_all(completed.stdout) if row]


def _document(rows: list[dict], kind: str, name: str) -> dict:
    return next(row for row in rows if row["kind"] == kind and row["metadata"]["name"] == name)


def test_ordinary_production_excludes_gate_r_readiness() -> None:
    names = {(row["kind"], row["metadata"]["name"]) for row in _render(PROD)}
    assert ("Job", "verdify-experiment-v2-gate-r-readiness") not in names
    assert ("ConfigMap", "experiment-v2-gate-r-readiness-activation") not in names


def test_activation_is_suspended_invalid_and_nonactuating() -> None:
    rows = _render(ACTIVATION)
    job = _document(rows, "Job", "verdify-experiment-v2-gate-r-readiness")
    assert job["metadata"]["namespace"] == "verdify-prod"
    assert job["spec"]["suspend"] is True
    container = job["spec"]["template"]["spec"]["containers"][0]
    assert container["image"] == "invalid.local/replace-before-activation:never"
    command = container["args"][0]
    assert "--mode recovery" in command
    assert "--boundary gate-r" in command
    assert "--derive-git-pin-from-argo" in command
    assert '"provenance"]["git_pin"]' in command
    assert "experiment_v2_proof_packet.py" in command
    assert "experiment_v2_readiness_guard.py" in command
    assert "provider" not in command.lower()
    secret_keys = {row["valueFrom"]["secretKeyRef"]["key"] for row in container["env"] if "valueFrom" in row}
    assert secret_keys == {"VERDIFY_INGESTOR_RUNTIME_DB_USER", "VERDIFY_INGESTOR_RUNTIME_DB_PASSWORD"}
    assert container["volumeMounts"][-1] == {"name": "backups", "mountPath": "/backups", "readOnly": True}

    activation = _document(rows, "ConfigMap", "experiment-v2-gate-r-readiness-activation")
    assert set(activation["data"]) == {
        "VERDIFY_GATE_R_READINESS_APPLICATION_SOURCE",
        "VERDIFY_GATE_R_READINESS_EXPERIMENT_ID",
    }
    assert set(activation["data"].values()) == {"REPLACE_BEFORE_ACTIVATION"}


def test_read_capability_and_network_are_exact() -> None:
    rows = _render(ACTIVATION)
    role = _document(rows, "Role", "verdify-experiment-v2-gate-r-readiness")
    assert all(set(rule["verbs"]).issubset({"get", "list"}) for rule in role["rules"])
    config_rule = next(rule for rule in role["rules"] if rule["resources"] == ["configmaps"])
    assert config_rule["resourceNames"] == ["verdify-config"]
    cluster_role = _document(rows, "ClusterRole", "verdify-prod-experiment-v2-gate-r-readiness-argo-read")
    assert cluster_role["rules"] == [
        {
            "apiGroups": ["argoproj.io"],
            "resources": ["applications"],
            "resourceNames": ["verdify-prod-dark"],
            "verbs": ["get"],
        }
    ]

    policy = _document(rows, "NetworkPolicy", "experiment-v2-gate-r-readiness")
    assert policy["spec"]["ingress"] == []
    assert policy["spec"]["policyTypes"] == ["Ingress", "Egress"]
    ip_blocks = [
        target["ipBlock"]["cidr"] for rule in policy["spec"]["egress"] for target in rule["to"] if "ipBlock" in target
    ]
    assert ip_blocks == ["10.43.0.1/32"]
