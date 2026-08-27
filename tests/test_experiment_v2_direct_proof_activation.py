"""Contract gates for the one-sync attended direct physical proof."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "deploy/k8s/components/experiment-v2-direct-proof"
PROD = ROOT / "deploy/k8s/overlays/prod/kustomization.yaml"
EXPERIMENT_ID = "45039c86-c1d9-52f6-a0a9-d94a17bc4b14"


def _render() -> list[dict]:
    result = subprocess.run(
        ["kustomize", "build", "deploy/k8s/overlays/prod"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [document for document in yaml.safe_load_all(result.stdout) if document]


def _document(rendered: list[dict], kind: str, name: str) -> dict:
    return next(
        document for document in rendered if document.get("kind") == kind and document["metadata"]["name"] == name
    )


def test_activation_is_explicitly_included_as_one_attended_component() -> None:
    prod = yaml.safe_load(PROD.read_text())
    assert "../../components/experiment-v2-direct-proof" in prod["components"]
    assert set(yaml.safe_load((COMPONENT / "kustomization.yaml").read_text())["resources"]) == {
        "direct-proof-configmap.yaml",
        "direct-proof-job.yaml",
    }


def test_only_the_single_ingestor_gets_the_coarse_capability_override() -> None:
    rendered = _render()
    ingestor = _document(rendered, "Deployment", "verdify-ingestor")
    pod = ingestor["spec"]["template"]
    assert pod["metadata"]["annotations"]["verdify.io/direct-proof-activation"] == ("2026-08-27-jason-vallery")
    container = next(row for row in pod["spec"]["containers"] if row["name"] == "ingestor")
    env = {row["name"]: row for row in container["env"]}
    assert env["VERDIFY_POLICY_VECTOR_MODE"] == {
        "name": "VERDIFY_POLICY_VECTOR_MODE",
        "value": "off",
    }
    assert env["VERDIFY_COMPONENT_EXPERIMENT_ENABLED"] == {
        "name": "VERDIFY_COMPONENT_EXPERIMENT_ENABLED",
        "value": "enabled",
    }
    assert env["VERDIFY_ACTIVE_EXPERIMENT_ID"] == {
        "name": "VERDIFY_ACTIVE_EXPERIMENT_ID",
        "value": EXPERIMENT_ID,
    }
    for deployment in (
        document
        for document in rendered
        if document.get("kind") == "Deployment" and document["metadata"]["name"] != "verdify-ingestor"
    ):
        assert "verdify.io/direct-proof-activation" not in deployment["spec"]["template"]["metadata"].get(
            "annotations", {}
        )


def test_proof_job_is_bounded_postsync_nonprivileged_and_avoids_broken_nodes() -> None:
    rendered = _render()
    job = _document(rendered, "Job", "verdify-experiment-v2-direct-proof")
    annotations = job["metadata"]["annotations"]
    assert annotations["argocd.argoproj.io/hook"] == "PostSync"
    assert annotations["argocd.argoproj.io/hook-delete-policy"] == "BeforeHookCreation"
    assert annotations["argocd.argoproj.io/sync-wave"] == "1"
    assert job["spec"]["backoffLimit"] == 0
    assert job["spec"]["activeDeadlineSeconds"] == 4500
    pod = job["spec"]["template"]["spec"]
    assert pod["restartPolicy"] == "Never"
    assert pod["automountServiceAccountToken"] is False
    assert pod["enableServiceLinks"] is False
    assert "serviceAccountName" not in pod
    assert pod["affinity"]["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"] == [
        {
            "matchExpressions": [
                {
                    "key": "kubernetes.io/hostname",
                    "operator": "NotIn",
                    "values": ["vm-k3s-node4", "vm-k3s-node6"],
                }
            ]
        }
    ]
    container = pod["containers"][0]
    assert container["image"].startswith("registry.vallery.net/verdifyconsultancy/verdify-api@sha256:")
    assert container["securityContext"] == {
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": True,
        "capabilities": {"drop": ["ALL"]},
    }
    secret_refs = {row["name"]: row["valueFrom"]["secretKeyRef"] for row in container["env"]}
    assert secret_refs == {
        "LIFECYCLE_DB_USER": {
            "name": "verdify-app-secrets",
            "key": "VERDIFY_EXPERIMENT_LIFECYCLE_DB_USER",
        },
        "LIFECYCLE_DB_PASSWORD": {
            "name": "verdify-app-secrets",
            "key": "VERDIFY_EXPERIMENT_LIFECYCLE_DB_PASSWORD",
        },
    }


def test_proof_script_is_syntax_valid_exact_role_bound_and_non_provider() -> None:
    rendered = _render()
    config = _document(rendered, "ConfigMap", "experiment-v2-direct-proof")
    script = config["data"]["proof.py"]
    compile(script, "proof.py", "exec")
    assert 'EXPERIMENT_ID = "45039c86-c1d9-52f6-a0a9-d94a17bc4b14"' in script
    assert "datetime(2026, 8, 27, 21, 0, tzinfo=UTC)" in script
    assert "datetime(2026, 8, 28, 0, 0, tzinfo=UTC)" in script
    assert datetime(2026, 8, 27, 21, tzinfo=UTC) < datetime(2026, 8, 28, 0, tzinfo=UTC)
    assert script.count('"Jason Vallery"') == 2
    for function in (
        "fn_experiment_v2_direct_proof_begin(",
        "fn_experiment_v2_direct_proof_open_aggressive(",
        "fn_experiment_v2_direct_proof_begin_baseline_after(",
        "fn_experiment_v2_direct_proof_finish(",
        "fn_experiment_v2_set_admission(",
        "fn_experiment_v2_request_recovery(",
    ):
        assert function in script
    for forbidden in (
        "fn_experiment_v2_direct_launch_commit",
        "api.openai.com",
        "OPENAI_API_KEY",
        "VERDIFY_EXPERIMENT_SELECTOR_API_KEY",
        "http://",
        "https://api.",
        "print(password",
        "print(user",
        "set -x",
    ):
        assert forbidden not in script
    assert "direct proof cannot start after design lock" in script
    assert "experiment authority axes changed outside the direct proof" in script
    assert "open_exposures=0" in script
    assert 'row["admission_state"] == "open"' in script
    assert '"direct-proof-runner-failure"' in script
    assert '"safety-recovery-requested"' in script
    assert script.index("SET_RECOVERY_SQL") < script.index("REQUEST_RECOVERY_SQL")


def test_default_config_remains_coarse_off_for_the_removal_rollback() -> None:
    base = yaml.safe_load((ROOT / "deploy/k8s/base/configmap.yaml").read_text())["data"]
    assert base["VERDIFY_POLICY_VECTOR_MODE"] == "off"
    assert base["VERDIFY_COMPONENT_EXPERIMENT_ENABLED"] == "off"
    assert base["VERDIFY_ACTIVE_EXPERIMENT_ID"] == ""
