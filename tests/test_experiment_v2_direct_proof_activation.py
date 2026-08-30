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
    assert activation["namespace"] == "verdify-prod"
    assert activation["components"] == ["../../components/experiment-v2-direct-proof"]
    assert activation["patches"] == [{"path": "activation-values.patch.yaml"}]
    assert set(yaml.safe_load((COMPONENT / "kustomization.yaml").read_text())["resources"]) == {
        "activation-configmap.yaml",
        "direct-proof-configmap.yaml",
        "direct-proof-job.yaml",
        "direct-proof-read-rbac.yaml",
        "direct-proof-networkpolicies.yaml",
    }

    rendered = _render(ACTIVATION)
    proof_resources = [
        document
        for document in rendered
        if document.get("metadata", {}).get("labels", {}).get("app.kubernetes.io/component")
        == "experiment-v2-direct-proof"
    ]
    assert proof_resources
    cluster_scoped = {"ClusterRole", "ClusterRoleBinding"}
    assert all(
        document["metadata"].get("namespace") == "verdify-prod"
        for document in proof_resources
        if document["kind"] not in cluster_scoped
    )


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
    assert job["spec"]["activeDeadlineSeconds"] == 7800
    pod = job["spec"]["template"]["spec"]
    assert pod["restartPolicy"] == "Never"
    assert pod["serviceAccountName"] == "verdify-experiment-v2-direct-proof"
    assert pod["automountServiceAccountToken"] is True
    assert pod["enableServiceLinks"] is False
    assert pod["securityContext"] == {
        "runAsNonRoot": True,
        "runAsUser": 1000,
        "runAsGroup": 1000,
        "fsGroup": 999,
        "seccompProfile": {"type": "RuntimeDefault"},
    }
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
        "READ_DB_USER": {
            "name": "verdify-app-secrets",
            "key": "VERDIFY_INGESTOR_RUNTIME_DB_USER",
        },
        "READ_DB_PASSWORD": {
            "name": "verdify-app-secrets",
            "key": "VERDIFY_INGESTOR_RUNTIME_DB_PASSWORD",
        },
        "VERDIFY_MCP_TOKEN": {
            "name": "verdify-hermes",
            "key": "VERDIFY_MCP_TOKEN",
        },
        "VERDIFY_EXPERIMENT_SELECTOR_API_KEY": {
            "name": "verdify-hermes",
            "key": "OPENAI_API_KEY",
        },
    }
    assert {row["configMapRef"]["name"] for row in container["envFrom"]} == {
        "verdify-config",
        "experiment-v2-direct-proof-activation",
    }
    volume_mounts = {row["name"]: row for row in container["volumeMounts"]}
    assert volume_mounts["backups"] == {
        "name": "backups",
        "mountPath": "/backups",
        "readOnly": True,
    }
    volumes = {row["name"]: row for row in pod["volumes"]}
    assert volumes["backups"] == {
        "name": "backups",
        "persistentVolumeClaim": {
            "claimName": "verdify-db-dumps",
            "readOnly": True,
        },
    }


def test_proof_read_capability_is_exact_and_nonmutating() -> None:
    rendered = _render(ACTIVATION)
    service_account = _document(rendered, "ServiceAccount", "verdify-experiment-v2-direct-proof")
    assert service_account["automountServiceAccountToken"] is True

    role = _document(rendered, "Role", "verdify-experiment-v2-direct-proof-read")
    assert role["rules"] == [
        {
            "apiGroups": [""],
            "resources": ["configmaps"],
            "resourceNames": ["verdify-config"],
            "verbs": ["get"],
        },
        {"apiGroups": [""], "resources": ["pods"], "verbs": ["get", "list"]},
        {"apiGroups": [""], "resources": ["pods/log"], "verbs": ["get"]},
        {
            "apiGroups": ["apps"],
            "resources": ["deployments", "statefulsets"],
            "verbs": ["get"],
        },
        {
            "apiGroups": ["batch"],
            "resources": ["cronjobs"],
            "resourceNames": ["verdify-db-backup"],
            "verbs": ["get"],
        },
        {"apiGroups": ["batch"], "resources": ["jobs"], "verbs": ["get", "list"]},
        {
            "apiGroups": ["coordination.k8s.io"],
            "resources": ["leases"],
            "resourceNames": ["verdify-ingestor-writer"],
            "verbs": ["get"],
        },
    ]
    role_binding = _document(rendered, "RoleBinding", "verdify-experiment-v2-direct-proof-read")
    assert role_binding["subjects"] == [
        {
            "kind": "ServiceAccount",
            "name": "verdify-experiment-v2-direct-proof",
            "namespace": "verdify-prod",
        }
    ]
    assert role_binding["roleRef"] == {
        "apiGroup": "rbac.authorization.k8s.io",
        "kind": "Role",
        "name": "verdify-experiment-v2-direct-proof-read",
    }

    cluster_role = _document(
        rendered,
        "ClusterRole",
        "verdify-prod-experiment-v2-direct-proof-argo-read",
    )
    assert cluster_role["rules"] == [
        {
            "apiGroups": ["argoproj.io"],
            "resources": ["applications"],
            "resourceNames": ["verdify-prod-dark"],
            "verbs": ["get"],
        }
    ]
    cluster_binding = _document(
        rendered,
        "ClusterRoleBinding",
        "verdify-prod-experiment-v2-direct-proof-argo-read",
    )
    assert cluster_binding["subjects"] == [
        {
            "kind": "ServiceAccount",
            "name": "verdify-experiment-v2-direct-proof",
            "namespace": "verdify-prod",
        }
    ]
    assert cluster_binding["roleRef"] == {
        "apiGroup": "rbac.authorization.k8s.io",
        "kind": "ClusterRole",
        "name": "verdify-prod-experiment-v2-direct-proof-argo-read",
    }

    mutating_verbs = {"create", "update", "patch", "delete", "deletecollection"}
    assert not any(mutating_verbs & set(rule["verbs"]) for rule in role["rules"] + cluster_role["rules"])


def test_proof_network_reach_excludes_device_and_private_networks() -> None:
    rendered = _render(ACTIVATION)
    policies = {
        document["metadata"]["name"]: document
        for document in rendered
        if document.get("kind") == "NetworkPolicy"
        and document.get("metadata", {}).get("labels", {}).get("app.kubernetes.io/component")
        == "experiment-v2-direct-proof"
    }
    assert set(policies) == {
        "experiment-v2-direct-proof",
        "allow-db-from-experiment-v2-direct-proof",
        "allow-mcp-from-experiment-v2-direct-proof",
    }
    expected = {
        "allow-db-from-experiment-v2-direct-proof": ("db", 5432),
        "allow-mcp-from-experiment-v2-direct-proof": ("mcp", 8000),
    }
    for name, (target, port) in expected.items():
        policy = policies[name]
        assert policy["spec"] == {
            "podSelector": {"matchLabels": {"app.kubernetes.io/component": target}},
            "policyTypes": ["Ingress"],
            "ingress": [
                {
                    "from": [
                        {"podSelector": {"matchLabels": {"app.kubernetes.io/component": "experiment-v2-direct-proof"}}}
                    ],
                    "ports": [{"protocol": "TCP", "port": port}],
                }
            ],
        }

    egress = policies["experiment-v2-direct-proof"]["spec"]
    assert egress["podSelector"] == {"matchLabels": {"app.kubernetes.io/component": "experiment-v2-direct-proof"}}
    assert egress["policyTypes"] == ["Ingress", "Egress"]
    assert egress["ingress"] == []
    assert len(egress["egress"]) == 5
    destinations = [target for rule in egress["egress"] for target in rule["to"]]
    ip_blocks = [target["ipBlock"] for target in destinations if "ipBlock" in target]
    assert {block["cidr"] for block in ip_blocks} == {"10.43.0.1/32", "0.0.0.0/0"}
    public = next(block for block in ip_blocks if block["cidr"] == "0.0.0.0/0")
    assert {
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "169.254.0.0/16",
    }.issubset(set(public["except"]))
    assert all(not block["cidr"].startswith("192.168.") for block in ip_blocks)


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
        "VERDIFY_DIRECT_PROOF_APPLICATION_SOURCE",
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
    assert 'row["admission_state"] in ("open", "baseline_recovery")' in script
    assert '"direct-proof-runner-failure"' in script
    assert '"safety-exposure-closed-hold-entered"' in script
    assert '"safety-recovery-requested"' in script
    assert "'emergency_hold'::text" in script
    fallback = script.index("except Exception:")
    close_first = script.index("SET_EMERGENCY_HOLD_SQL", fallback)
    hold_receipt = script.index('"safety-exposure-closed-hold-entered"', close_first)
    bounded_recovery = script.index("BEGIN_EMERGENCY_RECOVERY_SQL", hold_receipt)
    assert fallback < close_first < hold_receipt < bounded_recovery
    assert '"proof-already-sealed"' in script
    assert '"emergency-recovery-admitted"' in script
    assert '"emergency-recovery-retry-admitted"' in script
    assert "RUNTIME_REGISTRATION_SETTLE_SECONDS = 30 * 60" in script
    assert '"emergency-recovery-runtime-settle-wait"' in script
    assert '"emergency-recovery-runtime-settle-complete"' in script
    assert script.index('"emergency-recovery-runtime-settle-complete"') < script.index(
        '"emergency-recovery-retry-admitted"'
    )
    assert "current writer generation is not yet stable" in script
    assert "experiment_v2_proof_packet.py" in script
    assert "experiment_v2_readiness_guard.py" in script
    assert '"--derive-git-pin-from-argo"' in script
    assert 'json.loads(packet.read_text())["provenance"]["git_pin"]' in script
    assert 'run_readiness_boundary("gate-p")' in script
    assert 'run_readiness_boundary("baseline-before")' in script
    assert 'readiness_boundary="aggressive"' in script
    assert 'readiness_boundary="baseline-after"' in script
    gate_p = script.index('run_readiness_boundary("gate-p")')
    baseline_before = script.index('run_readiness_boundary("baseline-before")')
    begin = script.index("aggressive_work_id = await connection.fetchval(", baseline_before)
    baseline_commit = script.index(
        'commit_readiness_boundary(baseline_before_pending, "baseline-before")',
        begin,
    )
    assert gate_p < baseline_before < begin < baseline_commit
    retry_guard = script.index("run_readiness_boundary(readiness_boundary)")
    retry_sql = script.index("result = await connection.fetchval(sql, *args)", retry_guard)
    retry_commit = script.index(
        "commit_readiness_boundary(pending_readiness, readiness_boundary)",
        retry_sql,
    )
    assert retry_guard < retry_sql < retry_commit
    assert 'attempt["baseline_after_work_id"] is None' in script
    assert "direct aggressive admission requires the active exact attempt " in script
    assert "and its receipt-confirmed baseline-before work" in script
    assert "direct baseline-after requires the active exact attempt, completed " in script
    assert "aggressive exposure, and its baseline-before evidence" in script


def test_orchestrator_image_carries_only_the_read_only_proof_runtime_surfaces() -> None:
    dockerfile = (ROOT / "experiment_orchestrator/Dockerfile").read_text()
    requirements = (ROOT / "experiment_orchestrator/requirements.txt").read_text().splitlines()
    assert "pyyaml==6.0.3" in requirements
    for expected in (
        "scripts/experiment_v2_proof_packet.py",
        "scripts/experiment_v2_readiness_guard.py",
        "scripts/mcp-security-acceptance.py",
        "/app/readiness-source/scripts/verify_component_proof_packet.py",
        "/app/readiness-source/research/planner-efficacy/switchback/v2_selector.py",
        "/app/readiness-source/research/planner-efficacy/switchback/v2_outcomes.py",
        "/app/readiness-source/ingestor/tasks/component_experiment.py",
        "/app/readiness-source/deploy/k8s/components/hermes-iris/hermes-config.yaml",
        "/app/readiness-source/tests/fixtures/experiment-v2-readiness/base-proof.json",
    ):
        assert expected in dockerfile
    job = yaml.safe_load((COMPONENT / "direct-proof-job.yaml").read_text())
    assert job["spec"]["template"]["spec"]["containers"][0]["image"] == (
        "ghcr.io/verdifyconsultancy/verdify-experiment-v2-orchestrator"
    )


def test_default_config_remains_coarse_off_for_the_removal_rollback() -> None:
    base = yaml.safe_load((ROOT / "deploy/k8s/base/configmap.yaml").read_text())["data"]
    assert base["VERDIFY_POLICY_VECTOR_MODE"] == "off"
    assert base["VERDIFY_COMPONENT_EXPERIMENT_ENABLED"] == "off"
    assert base["VERDIFY_ACTIVE_EXPERIMENT_ID"] == ""


def test_dormant_activation_values_are_invalid_placeholders() -> None:
    rendered = _render(ACTIVATION)
    activation = _document(rendered, "ConfigMap", "experiment-v2-direct-proof-activation")
    assert activation["data"]
    assert "VERDIFY_DIRECT_PROOF_GIT_PIN" not in activation["data"]
    assert set(activation["data"].values()) == {"REPLACE_BEFORE_ACTIVATION"}
