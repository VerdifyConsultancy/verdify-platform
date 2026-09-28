"""Feature-off, exact-study GitOps bootstrap for the accepted-risk launch."""

from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import subprocess
import sys
import types
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "deploy/k8s/components/experiment-v2-direct-launch-bootstrap"
CONFIG = COMPONENT / "bootstrap-configmap.yaml"
CREDENTIAL_JOB = ROOT / "deploy/k8s/components/experiment-v2-credential-bootstrap/bootstrap-job.yaml"
MIGRATION = ROOT / "db/migrations/221-experiment-v2-state-replay.sql"
MIGRATE_DOCKERFILE = ROOT / "db/Dockerfile.migrate"
PROFILE_SOURCE = ROOT / "research/planner-efficacy/baseline/planner-switchback-v2-profiles.json"
EXPERIMENT_ID = "45039c86-c1d9-52f6-a0a9-d94a17bc4b14"


def _config() -> dict:
    return yaml.safe_load(CONFIG.read_text())


def _spec() -> dict:
    return json.loads(_config()["data"]["bootstrap.json"])


def _script() -> str:
    return _config()["data"]["bootstrap.py"]


def _bootstrap_namespace(monkeypatch: pytest.MonkeyPatch) -> dict:
    # Execute the mounted source itself, omitting only its process entrypoint.
    monkeypatch.setitem(sys.modules, "asyncpg", types.SimpleNamespace(PostgresError=Exception))
    script = ast.parse(_script())
    assert isinstance(script.body[-1], ast.Expr)
    assert isinstance(script.body[-1].value, ast.Call)
    assert isinstance(script.body[-1].value.func, ast.Attribute)
    assert script.body[-1].value.func.attr == "run"
    script.body.pop()
    namespace: dict = {}
    exec(compile(ast.fix_missing_locations(script), "bootstrap.py", "exec"), namespace)  # noqa: S102
    return namespace


def _settled_hold() -> tuple[dict, dict]:
    candidate = _spec()["candidate"]
    revision = "a" * 64
    row = {
        "experiment_id": EXPERIMENT_ID,
        "protocol_version": 2,
        "experiment_kind": "randomized",
        "lifecycle_status": "draft",
        "execution_phase": "shadow",
        "admission_state": "emergency_hold",
        "component_enabled": False,
        "lease_generation": 18,
        "revision_bundle_sha256": revision,
        "design_lock_sha256": None,
        "open_exposure_count": 0,
        **candidate,
    }
    attempt = {
        "authorization_id": "116c391e-238d-4721-b4c2-6c0467e42a61",
        "attempt_number": 1,
        "revision_bundle_sha256": revision,
        "attempt_failed": True,
        "attempt_superseded": False,
        "resolution_kind": "bounded_baseline_recovery",
        "emergency_recovery_complete": True,
        "proof_receipt_id": None,
    }
    return row, attempt


def test_settled_emergency_hold_is_read_only_exact_replay(monkeypatch, capsys) -> None:
    namespace = _bootstrap_namespace(monkeypatch)
    row, attempt = _settled_hold()
    queries: list[str] = []

    class Lifecycle:
        async def fetchrow(self, query: str, experiment_id: str):
            assert experiment_id == EXPERIMENT_ID
            queries.append(query)
            if query == namespace["STATUS_SQL"]:
                return row
            assert query == namespace["ATTEMPT_SQL"]
            return attempt

        @asynccontextmanager
        async def transaction(self, *, isolation: str):
            assert isolation == "serializable"
            yield

        async def close(self):
            pass

    lifecycle = Lifecycle()

    async def connect(user_name: str, password_name: str):
        assert (user_name, password_name) == ("LIFECYCLE_DB_USER", "LIFECYCLE_DB_PASSWORD")
        return lifecycle

    namespace["connect"] = connect
    namespace["load_spec"] = _spec
    asyncio.run(namespace["main"]())
    assert queries == [namespace["STATUS_SQL"], namespace["STATUS_SQL"], namespace["ATTEMPT_SQL"]]
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["mode"] == "exact-settled-feature-off-emergency-hold"
    assert receipt["component_enabled"] is False


@pytest.mark.parametrize(
    ("scope", "field", "value"),
    [
        ("row", "experiment_id", "116c391e-238d-4721-b4c2-6c0467e42a61"),
        ("row", "component_enabled", True),
        ("row", "open_exposure_count", 1),
        ("row", "lease_generation", 0),
        ("row", "lease_generation", True),
        ("row", "firmware_revision", "different"),
        ("row", "revision_bundle_sha256", "bad"),
        ("attempt", "attempt_failed", False),
        ("attempt", "attempt_number", True),
        ("attempt", "attempt_superseded", True),
        ("attempt", "revision_bundle_sha256", "b" * 64),
        ("attempt", "resolution_kind", "facility_owned_safe_state"),
        ("attempt", "emergency_recovery_complete", False),
        ("attempt", "proof_receipt_id", "116c391e-238d-4721-b4c2-6c0467e42a61"),
    ],
)
def test_settled_emergency_hold_rejects_identity_or_recovery_drift(
    monkeypatch, scope: str, field: str, value: object
) -> None:
    namespace = _bootstrap_namespace(monkeypatch)
    row, attempt = _settled_hold()
    (row if scope == "row" else attempt)[field] = value
    with pytest.raises(RuntimeError, match="settled emergency hold"):
        namespace["assert_settled_emergency_hold"](row, attempt, _spec()["study"], _spec()["candidate"])


def test_settled_emergency_hold_requires_a_proof_attempt(monkeypatch) -> None:
    namespace = _bootstrap_namespace(monkeypatch)
    row, _ = _settled_hold()
    with pytest.raises(RuntimeError, match="settled emergency hold"):
        namespace["assert_settled_emergency_hold"](row, None, _spec()["study"], _spec()["candidate"])


def _rendered() -> list[dict]:
    result = subprocess.run(
        ["kubectl", "kustomize", str(ROOT / "deploy/k8s/overlays/prod")],
        check=True,
        capture_output=True,
        text=True,
    )
    return [document for document in yaml.safe_load_all(result.stdout) if document]


def test_bootstrap_spec_is_exactly_bound_to_source_profiles_and_frozen_candidate() -> None:
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
    # This launched candidate is immutable across later firmware config changes.
    # The production held-study readback still names this exact revision.
    assert spec["candidate"]["config_revision"] == "4dbb2e691d91"


def test_bootstrap_is_feature_off_function_bounded_and_secret_safe() -> None:
    script = _script()
    compile(script, "bootstrap.py", "exec")
    assert "fn_runtime_v1_create_experiment" in script
    assert "fn_experiment_v2_configure" in script
    assert "fn_experiment_v2_register_state" in script
    assert "fn_experiment_v2_api_status" in script
    assert "fn_experiment_v2_direct_proof_begin" in script
    assert '"mode": "exact-attended-proof-in-progress"' in script
    assert '"actual": configured_axes, "expected": expected_axes' in script
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

    assert "apk add --no-cache python3 py3-asyncpg" in MIGRATE_DOCKERFILE.read_text()

    patch = yaml.safe_load(CREDENTIAL_JOB.read_text())
    assert patch["metadata"]["name"] == "verdify-experiment-v2-credential-bootstrap"
    assert patch["spec"]["activeDeadlineSeconds"] >= 240
    container = patch["spec"]["template"]["spec"]["containers"][0]
    assert container["name"] == "bootstrap-and-attest"
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    env = {entry["name"]: entry for entry in container["env"]}
    direct_env = {
        "ORDINARY_API_DB_USER",
        "ORDINARY_API_DB_PASSWORD",
        "LIFECYCLE_DB_USER",
        "LIFECYCLE_DB_PASSWORD",
        "MCP_IRIS_TOKEN",
    }
    assert direct_env <= set(env)
    assert {env[name]["valueFrom"]["secretKeyRef"]["name"] for name in direct_env} == {
        "verdify-app-secrets",
        "verdify-hermes",
    }
    assert env["MCP_IRIS_TOKEN"]["valueFrom"]["secretKeyRef"] == {
        "name": "verdify-hermes",
        "key": "VERDIFY_MCP_TOKEN",
    }
    assert "MCP_IRIS_TOKEN" in script
    assert "python /etc/verdify/direct-bootstrap/bootstrap.py" in container["args"][0]
    volumes = {volume["name"]: volume for volume in patch["spec"]["template"]["spec"]["volumes"]}
    assert volumes["direct-launch-bootstrap"]["configMap"]["name"] == ("experiment-v2-direct-launch-bootstrap")
    assert "for attempt in range(60)" in script
    assert "await asyncio.sleep(2)" in script


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
    assert ("Job", "verdify-experiment-v2-direct-launch-bootstrap") not in by_kind_name
    job = by_kind_name[("Job", "verdify-experiment-v2-credential-bootstrap")]
    assert job["spec"]["activeDeadlineSeconds"] >= 240
    assert job["metadata"]["annotations"] == {
        "argocd.argoproj.io/hook": "PreSync",
        "argocd.argoproj.io/hook-delete-policy": "BeforeHookCreation",
        "argocd.argoproj.io/sync-wave": "2",
    }
    config = by_kind_name[("ConfigMap", "experiment-v2-direct-launch-bootstrap")]
    assert config["metadata"]["annotations"] == {
        "argocd.argoproj.io/hook": "PreSync",
        "argocd.argoproj.io/hook-delete-policy": "BeforeHookCreation",
        "argocd.argoproj.io/sync-wave": "1",
    }
    pod = job["spec"]["template"]
    assert pod["metadata"]["labels"]["app.kubernetes.io/component"] == "migrate"
    assert "hostNetwork" not in pod["spec"]
    assert pod["spec"]["automountServiceAccountToken"] is False
    assert "affinity" not in pod["spec"]
    containers = {container["name"]: container for container in pod["spec"]["containers"]}
    assert set(containers) == {"bootstrap-and-attest"}
    container = containers["bootstrap-and-attest"]
    assert container["image"].startswith("registry.vallery.net/verdifyconsultancy/verdify-migrate@sha256:")
    assert "python /etc/verdify/direct-bootstrap/bootstrap.py" in container["args"][0]
    feature = by_kind_name[("ConfigMap", "verdify-config")]["data"]
    assert feature["VERDIFY_COMPONENT_EXPERIMENT_ENABLED"] == "off"
    assert feature["VERDIFY_ACTIVE_EXPERIMENT_ID"] == ""
    assert feature["VERDIFY_POLICY_VECTOR_MODE"] == "off"
    assert feature["VERDIFY_MCP_AUTH_MODE"] == "enforce"
