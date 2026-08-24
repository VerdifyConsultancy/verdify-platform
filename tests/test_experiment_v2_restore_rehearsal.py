"""Declarative recent-dump schema/ACL rehearsal for the v2 release candidate."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "deploy/k8s/components/experiment-v2-restore-rehearsal"
JOB = COMPONENT / "restore-rehearsal-job.yaml"
SCRIPT = COMPONENT / "restore-rehearsal-script.yaml"
NETWORK_POLICY = COMPONENT / "restore-rehearsal-network-policy.yaml"
PROD = ROOT / "deploy/k8s/overlays/prod/kustomization.yaml"


def _job() -> dict:
    return yaml.safe_load(JOB.read_text())


def _script() -> str:
    return yaml.safe_load(SCRIPT.read_text())["data"]["rehearse.sh"]


def _script_manifest() -> dict:
    return yaml.safe_load(SCRIPT.read_text())


def _network_policy() -> dict:
    return yaml.safe_load(NETWORK_POLICY.read_text())


def test_rehearsal_is_a_one_release_component_not_ambient_prod_work():
    kustomization = yaml.safe_load((COMPONENT / "kustomization.yaml").read_text())
    assert kustomization["kind"] == "Component"
    assert set(kustomization["resources"]) == {
        "restore-rehearsal-script.yaml",
        "restore-rehearsal-job.yaml",
        "restore-rehearsal-network-policy.yaml",
    }
    assert "../../components/experiment-v2-restore-rehearsal" not in PROD.read_text()


def test_rehearsal_is_gitops_hooked_bounded_and_non_networked():
    job = _job()
    annotations = job["metadata"]["annotations"]
    assert annotations["argocd.argoproj.io/hook"] == "PreSync"
    assert annotations["argocd.argoproj.io/hook-delete-policy"] == "BeforeHookCreation"
    script_annotations = _script_manifest()["metadata"]["annotations"]
    assert script_annotations["argocd.argoproj.io/hook"] == "PreSync"
    assert script_annotations["argocd.argoproj.io/hook-delete-policy"] == "BeforeHookCreation"
    assert int(script_annotations["argocd.argoproj.io/sync-wave"]) < int(annotations["argocd.argoproj.io/sync-wave"])
    assert job["spec"]["backoffLimit"] == 0
    assert job["spec"]["activeDeadlineSeconds"] == 3600
    pod = job["spec"]["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    assert "serviceAccountName" not in pod
    assert "secretKeyRef" not in json.dumps(pod)
    assert pod["imagePullSecrets"] == [{"name": "zot-origin-cluster-pull"}]
    dump_volume = next(v for v in pod["volumes"] if v["name"] == "dumps")
    assert dump_volume["persistentVolumeClaim"] == {
        "claimName": "verdify-db-dumps",
        "readOnly": True,
    }
    restore = pod["containers"][0]
    assert restore["image"].endswith("@sha256:0af03ecf697825f6ddae76fd275d16bf46007bed6d00eb3d754779cb7db96fa6")
    assert restore["securityContext"]["readOnlyRootFilesystem"] is True
    assert restore["securityContext"]["allowPrivilegeEscalation"] is False
    env = {entry["name"]: entry["value"] for entry in restore["env"]}
    assert "VERDIFY_MIGRATE_ALLOW_BASELINE" not in env

    policy = _network_policy()
    assert policy["apiVersion"] == "networking.k8s.io/v1"
    assert policy["kind"] == "NetworkPolicy"
    policy_annotations = policy["metadata"]["annotations"]
    assert policy_annotations["argocd.argoproj.io/hook"] == "PreSync"
    assert policy_annotations["argocd.argoproj.io/hook-delete-policy"] == "BeforeHookCreation"
    assert int(policy_annotations["argocd.argoproj.io/sync-wave"]) < int(annotations["argocd.argoproj.io/sync-wave"])
    assert policy["spec"]["podSelector"]["matchLabels"] == job["spec"]["template"]["metadata"]["labels"]
    assert set(policy["spec"]["policyTypes"]) == {"Ingress", "Egress"}
    assert policy["spec"]["ingress"] == []
    assert policy["spec"]["egress"] == []


def test_candidate_source_uses_the_prod_digest_transformer_key():
    job = _job()
    source_image = job["spec"]["template"]["spec"]["initContainers"][0]["image"]
    assert source_image == "ghcr.io/verdifyconsultancy/verdify-migrate"
    prod = yaml.safe_load(PROD.read_text())
    pin = next(image for image in prod["images"] if image["name"] == source_image)
    assert pin["newName"] == "registry.vallery.net/verdifyconsultancy/verdify-migrate"
    assert pin["digest"].startswith("sha256:")


def test_script_restores_only_the_bounded_latest_dump_and_runs_real_gates():
    script = _script()
    for required in (
        "find /backups -maxdepth 1 -type f -name 'verdify-*.dump'",
        "/backups/verdify-*.dump",
        "dump_age_seconds >= max_dump_age_seconds",
        "latest dump age must be >=0 and <26h",
        "timescaledb_pre_restore()",
        "pg_restore --exit-on-error --no-owner --no-privileges",
        "timescaledb_post_restore()",
        "/work/bin/apply-migrations.sh",
        "test-214-confirmed-component-experiment-v2.sql",
        "test-216-equipment-counter-source-ledger.sql",
        "test-217-runtime-role-boundary.sql",
        "fn_experiment_v2_ops_status()",
        "fn_record_equipment_counter_sample",
        "equipment_counter_samples",
        "fn_record_equipment_direct_state_snapshot",
        "equipment_direct_state_snapshots",
        "fn_record_equipment_state_source_receipt",
        "equipment_state_source_receipts",
        "verdify_experiment_equipment_source_collector",
        "verdify_experiment_v2_equipment_source_collector_login",
        "rolcanlogin",
        "rolinherit",
        "pg_auth_members",
        "aclexplode",
        "fn_experiment_v2_outcome_source_cycle",
        "experiment_v2_outcome_source_bindings",
        "has_table_privilege",
        "PASS: recent-dump schema/ACL replay and v2 vertical fixtures",
        "not proof of production-superuser denial",
        "restored_populated",
        "restored_ledger_count",
        "candidate_object_snapshot",
        "ledger_snapshot",
        "write_ledger_row_snapshot",
        "env -u VERDIFY_MIGRATE_ALLOW_BASELINE",
        "VERDIFY_MIGRATE_ALLOW_BASELINE=1 /work/bin/apply-migrations.sh",
        "populated DB with EMPTY ledger. Refusing to apply numbered migrations blind.",
        "exact empty-ledger refusal preserved candidate objects, ACLs, and ledger rows",
        "stamp_method = 'baseline'",
        "empty-ledger baseline refusal branch not applicable",
        "pre-existing restored ledger rows were preserved byte-for-byte",
        "comm -23",
    ):
        assert required in script
    assert script.count("/work/bin/apply-migrations.sh") == 4
    assert "ledger is current" in script
    assert "TRUNCATE" not in script.upper()
    for candidate_migration in (
        "214-confirmed-component-experiment-v2.sql",
        "215-experiment-v2-ops-observability.sql",
        "216-equipment-counter-source-ledger.sql",
        "217-runtime-role-boundary.sql",
    ):
        assert candidate_migration in script
    compact_script = " ".join(script.split())
    for source_signature in (
        "public.fn_record_equipment_counter_sample(uuid,timestamptz,text,text,text,double precision,text,uuid,double precision,uuid,bigint,text)",
        "public.fn_record_equipment_direct_state_snapshot(uuid,uuid,text,text,jsonb,double precision,uuid,bigint,text)",
        "public.fn_record_equipment_state_source_receipt(uuid,timestamptz,text,text,jsonb,boolean,uuid,bigint,text)",
    ):
        assert f"OR has_function_privilege( 'verdify', '{source_signature}', 'EXECUTE')" in compact_script
        assert (
            "OR NOT has_function_privilege( "
            f"'verdify_experiment_equipment_source_collector', '{source_signature}', 'EXECUTE')" in compact_script
        )
    for forbidden in ("kubectl", "device", "esp32", "PGPASSWORD=", "rm -rf"):
        assert forbidden not in script
