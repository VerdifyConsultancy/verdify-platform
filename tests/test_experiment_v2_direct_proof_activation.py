"""Contract gates for the dormant, one-sync attended physical-proof surface."""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "deploy/k8s/components/experiment-v2-direct-proof"
PROD = ROOT / "deploy/k8s/overlays/prod/kustomization.yaml"
ACTIVATION = ROOT / "deploy/k8s/activations/experiment-v2-direct-proof"


def _render(path: Path) -> list[dict]:
    result = subprocess.run(
        ["kustomize", "build", str(path)],
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


def test_ordinary_production_excludes_all_direct_proof_activation_resources() -> None:
    prod = yaml.safe_load(PROD.read_text())
    assert "../../components/experiment-v2-direct-proof" not in prod["components"]
    rendered = _render(PROD.parent)
    names = {(document.get("kind"), document.get("metadata", {}).get("name")) for document in rendered}
    assert ("Job", "verdify-experiment-v2-direct-proof") not in names
    assert ("ConfigMap", "experiment-v2-direct-proof") not in names
    assert ("ConfigMap", "experiment-v2-direct-proof-activation") not in names

    ingestor = _document(rendered, "Deployment", "verdify-ingestor")
    annotations = ingestor["spec"]["template"]["metadata"].get("annotations", {})
    assert "verdify.io/direct-proof-activation" not in annotations
    container = next(row for row in ingestor["spec"]["template"]["spec"]["containers"] if row["name"] == "ingestor")
    explicit_env = {row["name"] for row in container["env"]}
    assert (
        not {
            "VERDIFY_POLICY_VECTOR_MODE",
            "VERDIFY_COMPONENT_EXPERIMENT_ENABLED",
            "VERDIFY_ACTIVE_EXPERIMENT_ID",
        }
        & explicit_env
    )
    config = _document(rendered, "ConfigMap", "verdify-config")["data"]
    assert config["VERDIFY_POLICY_VECTOR_MODE"] == "off"
    assert config["VERDIFY_COMPONENT_EXPERIMENT_ENABLED"] == "off"
    assert config["VERDIFY_ACTIVE_EXPERIMENT_ID"] == ""


def test_dormant_activation_is_explicit_and_self_contained() -> None:
    activation = yaml.safe_load((ACTIVATION / "kustomization.yaml").read_text())
    assert activation["resources"] == ["../../overlays/prod"]
    assert activation["components"] == ["../../components/experiment-v2-direct-proof"]
    assert activation["patches"] == [{"path": "activation-values.patch.yaml"}]
    assert set(yaml.safe_load((COMPONENT / "kustomization.yaml").read_text())["resources"]) == {
        "activation-configmap.yaml",
        "direct-proof-configmap.yaml",
        "direct-proof-job.yaml",
    }


def test_dormant_render_grants_no_ingestor_capability() -> None:
    rendered = _render(ACTIVATION)
    ingestor = _document(rendered, "Deployment", "verdify-ingestor")
    pod = ingestor["spec"]["template"]
    assert pod["metadata"]["annotations"]["verdify.io/direct-proof-activation"] == "dormant-no-authority"
    container = next(row for row in pod["spec"]["containers"] if row["name"] == "ingestor")
    env = {row["name"]: row for row in container["env"]}
    assert env["VERDIFY_POLICY_VECTOR_MODE"] == {
        "name": "VERDIFY_POLICY_VECTOR_MODE",
        "value": "off",
    }
    assert env["VERDIFY_COMPONENT_EXPERIMENT_ENABLED"] == {
        "name": "VERDIFY_COMPONENT_EXPERIMENT_ENABLED",
        "value": "off",
    }
    assert env["VERDIFY_ACTIVE_EXPERIMENT_ID"] == {
        "name": "VERDIFY_ACTIVE_EXPERIMENT_ID",
        "value": "",
    }
    for deployment in (
        document
        for document in rendered
        if document.get("kind") == "Deployment" and document["metadata"]["name"] != "verdify-ingestor"
    ):
        assert "verdify.io/direct-proof-activation" not in deployment["spec"]["template"]["metadata"].get(
            "annotations", {}
        )


def test_proof_job_is_suspended_bounded_postsync_and_nonprivileged() -> None:
    rendered = _render(ACTIVATION)
    job = _document(rendered, "Job", "verdify-experiment-v2-direct-proof")
    annotations = job["metadata"]["annotations"]
    assert annotations["argocd.argoproj.io/hook"] == "PostSync"
    assert annotations["argocd.argoproj.io/hook-delete-policy"] == "BeforeHookCreation"
    assert annotations["argocd.argoproj.io/sync-wave"] == "1"
    assert job["spec"]["suspend"] is True
    assert job["spec"]["backoffLimit"] == 0
    assert job["spec"]["activeDeadlineSeconds"] == 6600
    pod = job["spec"]["template"]["spec"]
    assert pod["restartPolicy"] == "Never"
    assert pod["automountServiceAccountToken"] is False
    assert pod["enableServiceLinks"] is False
    assert pod["securityContext"] == {
        "runAsNonRoot": True,
        "runAsUser": 1000,
        "runAsGroup": 1000,
        "seccompProfile": {"type": "RuntimeDefault"},
    }
    assert "serviceAccountName" not in pod
    assert "affinity" not in pod
    assert "operations.vallery.net/temporary-node-exclusion" not in annotations
    container = pod["containers"][0]
    assert container["image"] == "invalid.local/replace-before-activation:never"
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
    assert {row["configMapRef"]["name"] for row in container["envFrom"]} == {
        "verdify-config",
        "experiment-v2-direct-proof-activation",
    }


def test_proof_script_is_syntax_valid_runtime_bound_and_non_provider() -> None:
    rendered = _render(ACTIVATION)
    config = _document(rendered, "ConfigMap", "experiment-v2-direct-proof")
    script = config["data"]["proof.py"]
    compile(script, "proof.py", "exec")
    for variable in (
        "VERDIFY_DIRECT_PROOF_EXPERIMENT_ID",
        "VERDIFY_DIRECT_PROOF_AUTHORIZATION_REF",
        "VERDIFY_DIRECT_PROOF_FACILITY_AUTHORIZATION_REF",
        "VERDIFY_DIRECT_PROOF_AUTHORIZED_FROM",
        "VERDIFY_DIRECT_PROOF_AUTHORIZED_TO",
        "VERDIFY_DIRECT_PROOF_SUPERVISOR_ROLE",
        "VERDIFY_DIRECT_PROOF_RESCUE_OWNER_ROLE",
        "VERDIFY_DIRECT_PROOF_ACTOR",
        "VERDIFY_DIRECT_PROOF_CONFIG_REVISION",
    ):
        assert variable in script
    assert "required_activation_value" in script
    assert "activation_time" in script
    assert "activation window must be between 3 minutes and 12 hours" in script
    assert "SUPERVISOR_ROLE" in script and "RESCUE_OWNER_ROLE" in script
    assert "2026-08-28" not in script
    assert "2026-08-29" not in script
    assert "extended-through-23:00-MT" not in script
    assert "4dbb2e691d91" not in script
    for function in (
        "fn_experiment_v2_direct_proof_begin(",
        "fn_experiment_v2_direct_proof_open_aggressive(",
        "fn_experiment_v2_direct_proof_begin_baseline_after(",
        "fn_experiment_v2_direct_proof_finish(",
        "fn_experiment_v2_direct_proof_attempt_status(",
        "fn_experiment_v2_direct_proof_begin_emergency_recovery(",
        "fn_experiment_v2_direct_proof_retry_emergency_recovery(",
        "fn_experiment_v2_direct_proof_finish_emergency_recovery(",
        "fn_experiment_v2_direct_proof_resolve_startup_rollover(",
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
    assert '"actual": actual, "expected": expected' in script
    assert "open_exposures=0" in script
    assert 'row["admission_state"] == "open"' in script
    assert '"direct-proof-runner-failure"' in script
    assert '"safety-recovery-requested"' in script
    assert script.index("SET_RECOVERY_SQL") < script.index("REQUEST_RECOVERY_SQL")
    assert '"proof-already-sealed"' in script
    assert '"emergency-recovery-admitted"' in script
    assert '"emergency-recovery-retry-admitted"' in script
    assert "RUNTIME_REGISTRATION_SETTLE_SECONDS = 5 * 60" in script
    assert '"emergency-recovery-runtime-settle-wait"' in script
    assert '"emergency-recovery-runtime-settle-complete"' in script
    assert script.index('"emergency-recovery-runtime-settle-complete"') < script.index(
        '"emergency-recovery-retry-admitted"'
    )
    assert "current writer generation is not yet stable" in script
    assert 'attempt["baseline_after_work_id"] is None' in script
    assert "direct aggressive admission requires the active exact attempt " in script
    assert "and its receipt-confirmed baseline-before work" in script
    assert "direct baseline-after requires the active exact attempt, completed " in script
    assert "aggressive exposure, and its baseline-before evidence" in script


def test_default_config_remains_coarse_off_for_the_removal_rollback() -> None:
    base = yaml.safe_load((ROOT / "deploy/k8s/base/configmap.yaml").read_text())["data"]
    assert base["VERDIFY_POLICY_VECTOR_MODE"] == "off"
    assert base["VERDIFY_COMPONENT_EXPERIMENT_ENABLED"] == "off"
    assert base["VERDIFY_ACTIVE_EXPERIMENT_ID"] == ""


def test_dormant_activation_values_are_invalid_placeholders() -> None:
    rendered = _render(ACTIVATION)
    activation = _document(rendered, "ConfigMap", "experiment-v2-direct-proof-activation")
    assert activation["data"]
    assert set(activation["data"].values()) == {"REPLACE_BEFORE_ACTIVATION"}
