"""Fail-closed contracts for the recovery-only Gate R readiness hook."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PROD = ROOT / "deploy/k8s/overlays/prod"
ACTIVATION = ROOT / "deploy/k8s/activations/experiment-v2-gate-r-readiness"
ATTENDED_PATCH = PROD / "experiment-v2-gate-r-readiness.patch.yaml"


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


def test_production_gate_r_readiness_is_absent_or_exactly_attended() -> None:
    rows = _render(PROD)
    names = {(row["kind"], row["metadata"]["name"]) for row in rows}
    job_key = ("Job", "verdify-experiment-v2-gate-r-readiness")
    activation_key = ("ConfigMap", "experiment-v2-gate-r-readiness-activation")
    if job_key not in names:
        assert activation_key not in names
        assert not ATTENDED_PATCH.exists()
        return

    assert activation_key in names
    assert ATTENDED_PATCH.is_file()
    job = _document(rows, *job_key)
    activation = _document(rows, *activation_key)
    assert job["spec"]["suspend"] is False
    marker = job["metadata"]["annotations"]["verdify.io/gate-r-readiness-activation"]
    assert re.fullmatch(r"m81-[0-9]{8}-[0-9a-f]{8}", marker)
    assert job["spec"]["template"]["metadata"]["annotations"] == {"verdify.io/gate-r-readiness-activation": marker}
    image = job["spec"]["template"]["spec"]["containers"][0]["image"]
    assert re.fullmatch(
        r"registry\.vallery\.net/verdifyconsultancy/verdify-experiment-v2-orchestrator@sha256:[0-9a-f]{64}",
        image,
    )
    prod = yaml.safe_load((PROD / "kustomization.yaml").read_text())
    pin = next(
        row for row in prod["images"] if row["name"] == "ghcr.io/verdifyconsultancy/verdify-experiment-v2-orchestrator"
    )
    assert image == f"{pin['newName']}@{pin['digest']}"
    assert activation["data"]["VERDIFY_GATE_R_READINESS_EXPERIMENT_ID"] == ("45039c86-c1d9-52f6-a0a9-d94a17bc4b14")
    assert re.fullmatch(r"[0-9a-f]{40}", activation["data"]["VERDIFY_GATE_R_READINESS_APPLICATION_SOURCE"])


def test_activation_state_is_exact_and_nonactuating() -> None:
    attended = ATTENDED_PATCH.is_file()
    rows = _render(PROD if attended else ACTIVATION)
    job = _document(rows, "Job", "verdify-experiment-v2-gate-r-readiness")
    assert job["metadata"]["namespace"] == "verdify-prod"
    assert job["spec"]["suspend"] is (not attended)
    container = job["spec"]["template"]["spec"]["containers"][0]
    if attended:
        assert re.fullmatch(
            r"registry\.vallery\.net/verdifyconsultancy/verdify-experiment-v2-orchestrator@sha256:[0-9a-f]{64}",
            container["image"],
        )
    else:
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
    if attended:
        assert "REPLACE_BEFORE_ACTIVATION" not in activation["data"].values()
    else:
        assert set(activation["data"].values()) == {"REPLACE_BEFORE_ACTIVATION"}


def test_read_capability_and_network_are_exact() -> None:
    rows = _render(PROD if ATTENDED_PATCH.is_file() else ACTIVATION)
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
