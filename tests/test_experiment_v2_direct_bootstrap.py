"""Feature-off, exact-study GitOps bootstrap for the accepted-risk launch."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "deploy/k8s/components/experiment-v2-direct-launch-bootstrap"
CONFIG = COMPONENT / "bootstrap-configmap.yaml"
JOB = COMPONENT / "bootstrap-job.yaml"
MIGRATION = ROOT / "db/migrations/221-experiment-v2-state-replay.sql"
PROFILE_SOURCE = ROOT / "research/planner-efficacy/baseline/planner-switchback-v2-profiles.json"
EXPERIMENT_ID = "45039c86-c1d9-52f6-a0a9-d94a17bc4b14"


def _config() -> dict:
    return yaml.safe_load(CONFIG.read_text())


def _spec() -> dict:
    return json.loads(_config()["data"]["bootstrap.json"])


def _script() -> str:
    return _config()["data"]["bootstrap.py"]


def _rendered() -> list[dict]:
    result = subprocess.run(
        ["kubectl", "kustomize", str(ROOT / "deploy/k8s/overlays/prod")],
        check=True,
        capture_output=True,
        text=True,
    )
    return [document for document in yaml.safe_load_all(result.stdout) if document]


def test_bootstrap_spec_is_exactly_bound_to_source_profiles_and_current_config() -> None:
    spec = _spec()
    source_raw = PROFILE_SOURCE.read_bytes()
    source = json.loads(source_raw)
    assert spec["schema"] == "verdify-experiment-v2-direct-bootstrap-v1"
    assert spec["study"] == {
        "experiment_id": EXPERIMENT_ID,
        "greenhouse_id": "vallery",
        "kind": "randomized",
        "name": "Verdify confirmed-component AI-vs-frozen-FSM switchback v2",
        "timezone": "America/Denver",
        "study_id": "verdify-confirmed-component-switchback-v2-2026-08",
        "assignment_namespace_uuid": "0c162b58-5a4c-5ddb-91fd-7d0ca68ff81f",
    }
    assert spec["profiles_artifact_sha256"] == hashlib.sha256(source_raw).hexdigest()
    assert spec["wire_schema"] == source["wire_schema"]
    assert set(spec["profiles"]) == {"baseline", "moderate", "aggressive"}
    for name, profile in spec["profiles"].items():
        source_profile = source["profiles"][name]
        assert profile == {
            key: source_profile[key] for key in ("field_count", "wire_bytes", "wire_hex", "policy_state_content_sha256")
        }
    revision = subprocess.run(
        ["bash", "scripts/gen-config-revision.sh", "--print"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert spec["candidate"]["config_revision"] == revision


def test_bootstrap_is_feature_off_function_bounded_and_secret_safe() -> None:
    script = _script()
    compile(script, "bootstrap.py", "exec")
    assert "fn_runtime_v1_create_experiment" in script
    assert "fn_experiment_v2_configure" in script
    assert "fn_experiment_v2_register_state" in script
    assert "fn_experiment_v2_api_status" in script
    assert 'transaction(isolation="serializable")' in script
    assert 'component_enabled": False' in script
    assert 'profile_count": 3' in script
    assert "exact replay was not idempotent" in script
    for forbidden in (
        "fn_experiment_v2_transition",
        "fn_experiment_v2_set_admission",
        "fn_experiment_v2_finalize_randomization",
        "fn_experiment_v2_create_work",
        "set_tunable",
        "set_plan",
        "kubectl",
        "Secret",
    ):
        assert forbidden not in script

    job = yaml.safe_load(JOB.read_text())
    assert job["metadata"]["annotations"] == {
        "argocd.argoproj.io/hook": "PreSync",
        "argocd.argoproj.io/hook-delete-policy": "BeforeHookCreation",
        "argocd.argoproj.io/sync-wave": "3",
    }
    pod = job["spec"]["template"]
    assert pod["metadata"]["labels"]["app.kubernetes.io/component"] == "migrate"
    assert pod["spec"]["automountServiceAccountToken"] is False
    assert pod["spec"]["containers"][0]["securityContext"]["readOnlyRootFilesystem"] is True
    env = {entry["name"]: entry for entry in pod["spec"]["containers"][0]["env"]}
    assert set(env) == {
        "ORDINARY_API_DB_USER",
        "ORDINARY_API_DB_PASSWORD",
        "LIFECYCLE_DB_USER",
        "LIFECYCLE_DB_PASSWORD",
        "MCP_IRIS_TOKEN",
    }
    assert {entry["valueFrom"]["secretKeyRef"]["name"] for entry in env.values()} == {
        "verdify-app-secrets",
        "verdify-hermes",
    }
    assert env["MCP_IRIS_TOKEN"]["valueFrom"]["secretKeyRef"] == {
        "name": "verdify-hermes",
        "key": "VERDIFY_MCP_TOKEN",
    }


def test_state_registration_exact_replay_is_idempotent_but_changed_bytes_fail() -> None:
    sql = MIGRATION.read_text()
    assert "CREATE OR REPLACE FUNCTION public.fn_experiment_v2_register_state(" in sql
    assert "IS NOT DISTINCT FROM" in sql
    assert "RETURN v_row;" in sql
    assert "state artifact is immutable and exact replay differs" in sql
    assert "FOR UPDATE" in sql
    assert "SECURITY DEFINER" in sql
    assert "TO verdify_experiment_lifecycle" in sql
    assert "DROP TABLE" not in sql.upper()


def test_prod_render_contains_bootstrap_and_keeps_experiment_disabled() -> None:
    resources = _rendered()
    by_kind_name = {(resource["kind"], resource["metadata"]["name"]): resource for resource in resources}
    job = by_kind_name[("Job", "verdify-experiment-v2-direct-launch-bootstrap")]
    assert job["spec"]["activeDeadlineSeconds"] >= 600
    container = job["spec"]["template"]["spec"]["containers"][0]
    assert container["image"].startswith("registry.vallery.net/verdifyconsultancy/verdify-api@sha256:")
    feature = by_kind_name[("ConfigMap", "verdify-config")]["data"]
    assert feature["VERDIFY_COMPONENT_EXPERIMENT_ENABLED"] == "off"
    assert feature["VERDIFY_ACTIVE_EXPERIMENT_ID"] == ""
    assert feature["VERDIFY_POLICY_VECTOR_MODE"] == "off"
    assert feature["VERDIFY_MCP_AUTH_MODE"] == "enforce"
