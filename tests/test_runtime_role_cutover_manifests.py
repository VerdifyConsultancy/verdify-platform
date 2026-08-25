"""Staged migration-217 workload identity and owner-retirement contract."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parents[1]
PROD = REPO_ROOT / "deploy/k8s/overlays/prod"
STAGED = REPO_ROOT / "deploy/k8s/overlays/prod-runtime-role-boundary"
COMPONENT = REPO_ROOT / "deploy/k8s/components/runtime-role-boundary"


def _render(overlay: Path) -> list[dict]:
    result = subprocess.run(
        ["kustomize", "build", str(overlay)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [document for document in yaml.safe_load_all(result.stdout) if document]


def _deployment(documents: list[dict], name: str) -> dict:
    return next(
        document
        for document in documents
        if document.get("kind") == "Deployment" and document["metadata"]["name"] == name
    )


def _job(documents: list[dict], name: str) -> dict:
    return next(
        document for document in documents if document.get("kind") == "Job" and document["metadata"]["name"] == name
    )


def _pod_container_env(workload: dict, container_name: str) -> dict[str, dict]:
    container = next(
        item for item in workload["spec"]["template"]["spec"]["containers"] if item["name"] == container_name
    )
    return {item["name"]: item for item in container.get("env", [])}


def _env(deployment: dict, container_name: str) -> dict[str, dict]:
    return _pod_container_env(deployment, container_name)


def _assert_secret_key(item: dict, key: str) -> None:
    assert item["valueFrom"]["secretKeyRef"] == {
        "name": "verdify-app-secrets",
        "key": key,
    }


def _assert_config_key(item: dict, key: str) -> None:
    assert item["valueFrom"]["configMapKeyRef"] == {
        "name": "verdify-config",
        "key": key,
    }


def test_staged_cutover_removes_owner_password_and_binds_exact_runtime_keys():
    documents = _render(STAGED)
    api_env = _env(_deployment(documents, "verdify-api"), "api")
    ingestor_env = _env(_deployment(documents, "verdify-ingestor"), "ingestor")

    assert "POSTGRES_PASSWORD" not in api_env
    _assert_secret_key(api_env["DB_USER"], "VERDIFY_API_RUNTIME_DB_USER")
    _assert_secret_key(api_env["DB_PASS"], "VERDIFY_API_RUNTIME_DB_PASSWORD")
    assert api_env["VERDIFY_API_RUNTIME_DB_ROLE_REQUIRED"]["value"] == "1"

    assert "POSTGRES_PASSWORD" not in ingestor_env
    _assert_secret_key(ingestor_env["DB_USER"], "VERDIFY_INGESTOR_RUNTIME_DB_USER")
    _assert_secret_key(ingestor_env["DB_PASSWORD"], "VERDIFY_INGESTOR_RUNTIME_DB_PASSWORD")
    _assert_secret_key(ingestor_env["PGPASSWORD"], "VERDIFY_INGESTOR_RUNTIME_DB_PASSWORD")
    assert ingestor_env["VERDIFY_INGESTOR_RUNTIME_DB_ROLE_REQUIRED"]["value"] == "1"

    assert all(document.get("kind") != "Secret" for document in documents)


def test_staged_cutover_bootstraps_and_attests_both_actual_logins_before_sync():
    documents = _render(STAGED)
    bootstrap = _job(documents, "verdify-runtime-role-bootstrap")
    migration = _job(documents, "verdify-migrate")

    annotations = bootstrap["metadata"]["annotations"]
    assert annotations["argocd.argoproj.io/hook"] == "PreSync"
    assert annotations["argocd.argoproj.io/hook-delete-policy"] == "BeforeHookCreation,HookSucceeded"
    assert int(migration["metadata"]["annotations"].get("argocd.argoproj.io/sync-wave", "0")) == 0
    assert int(annotations["argocd.argoproj.io/sync-wave"]) == 1

    spec = bootstrap["spec"]
    assert spec["backoffLimit"] == 0
    assert spec["activeDeadlineSeconds"] == 180
    assert spec["ttlSecondsAfterFinished"] == 600
    pod = spec["template"]["spec"]
    assert pod["restartPolicy"] == "Never"
    assert pod["automountServiceAccountToken"] is False
    assert pod["enableServiceLinks"] is False
    assert "serviceAccountName" not in pod
    assert pod["imagePullSecrets"] == [{"name": "zot-origin-cluster-pull"}]
    assert spec["template"]["metadata"]["labels"]["app.kubernetes.io/component"] == "migrate"
    assert pod["securityContext"] == {
        "runAsNonRoot": True,
        "runAsUser": 1000,
        "runAsGroup": 1000,
        "seccompProfile": {"type": "RuntimeDefault"},
    }

    assert len(pod["containers"]) == 1
    container = pod["containers"][0]
    migration_container = next(
        item for item in migration["spec"]["template"]["spec"]["containers"] if item["name"] == "migrate"
    )
    assert container["image"] == migration_container["image"]
    assert container["image"].startswith("registry.vallery.net/verdifyconsultancy/verdify-migrate@sha256:")
    assert container["command"] == ["/bin/sh", "-ec"]
    assert container["securityContext"] == {
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": True,
        "capabilities": {"drop": ["ALL"]},
    }

    env = _pod_container_env(bootstrap, "bootstrap-and-attest")
    for key in ("DB_HOST", "DB_PORT", "DB_NAME"):
        _assert_config_key(env[key], key)
    _assert_config_key(env["DB_ADMIN_USER"], "DB_USER")
    _assert_secret_key(env["DB_ADMIN_PASSWORD"], "POSTGRES_PASSWORD")
    for key in (
        "VERDIFY_API_RUNTIME_DB_USER",
        "VERDIFY_API_RUNTIME_DB_PASSWORD",
        "VERDIFY_INGESTOR_RUNTIME_DB_USER",
        "VERDIFY_INGESTOR_RUNTIME_DB_PASSWORD",
    ):
        _assert_secret_key(env[key], key)
    assert env["PGAPPNAME"] == {
        "name": "PGAPPNAME",
        "value": "verdify-runtime-role-bootstrap",
    }

    script = container["args"][0]
    subprocess.run(
        ["/bin/sh", "-n"],
        input=script,
        text=True,
        check=True,
        capture_output=True,
    )
    assert "\\getenv api_runtime_password VERDIFY_API_RUNTIME_DB_PASSWORD" in script
    assert "\\getenv ingestor_runtime_password VERDIFY_INGESTOR_RUNTIME_DB_PASSWORD" in script
    assert "BEGIN;" in script and "COMMIT;" in script
    assert "ALTER ROLE verdify_api_runtime_login" in script
    assert "ALTER ROLE verdify_ingestor_runtime_login" in script
    assert script.count("current_user = session_user") == 2
    assert script.count("'pg_catalog, public, pg_temp'") == 2
    assert script.count("public.fn_runtime_attest_ordinary_login() IS TRUE") == 2
    assert script.count('PGPASSWORD="') == 3
    assert "-U verdify_api_runtime_login" in script
    assert "-U verdify_ingestor_runtime_login" in script
    for forbidden in (
        "set -x",
        "printenv",
        "postgresql://",
        "--password=",
        "--set=api_runtime_password",
        "--set=ingestor_runtime_password",
        "-v api_runtime_password",
        "-v ingestor_runtime_password",
        "ESP32",
        "MQTT",
    ):
        assert forbidden not in script
    assert all("PASSWORD}" not in line for line in script.splitlines() if "printf" in line or 'fail "' in line)


def test_synthetic_direct_prod_adoption_uses_the_prod_migrate_transformer(tmp_path: Path):
    prod = yaml.safe_load((PROD / "kustomization.yaml").read_text())
    migrate_pin = next(image for image in prod["images"] if image["name"].endswith("/verdify-migrate"))
    synthetic = tmp_path / "kustomization.yaml"
    synthetic.write_text(
        yaml.safe_dump(
            {
                "apiVersion": "kustomize.config.k8s.io/v1beta1",
                "kind": "Kustomization",
                "resources": [os.path.relpath(PROD, tmp_path)],
                "components": [os.path.relpath(COMPONENT, tmp_path)],
                "images": [migrate_pin],
            },
            sort_keys=False,
        )
    )
    result = subprocess.run(
        ["kustomize", "build", "--load-restrictor", "LoadRestrictionsNone", str(tmp_path)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    documents = [document for document in yaml.safe_load_all(result.stdout) if document]
    bootstrap = _job(documents, "verdify-runtime-role-bootstrap")
    migration = _job(documents, "verdify-migrate")
    bootstrap_image = bootstrap["spec"]["template"]["spec"]["containers"][0]["image"]
    migration_image = migration["spec"]["template"]["spec"]["containers"][0]["image"]
    assert bootstrap_image == migration_image
    assert bootstrap_image.startswith("registry.vallery.net/verdifyconsultancy/verdify-migrate@sha256:")
    assert not any(document.get("kind") == "Secret" for document in documents)


def test_cutover_is_not_active_in_the_current_production_overlay():
    documents = _render(PROD)
    api_env = _env(_deployment(documents, "verdify-api"), "api")
    ingestor_env = _env(_deployment(documents, "verdify-ingestor"), "ingestor")

    assert "VERDIFY_API_RUNTIME_DB_ROLE_REQUIRED" not in api_env
    assert "VERDIFY_INGESTOR_RUNTIME_DB_ROLE_REQUIRED" not in ingestor_env

    cutover_secret_keys = {
        "VERDIFY_API_RUNTIME_DB_USER",
        "VERDIFY_API_RUNTIME_DB_PASSWORD",
        "VERDIFY_INGESTOR_RUNTIME_DB_USER",
        "VERDIFY_INGESTOR_RUNTIME_DB_PASSWORD",
    }
    rendered_secret_keys = {
        secret_key_ref["key"]
        for environment in (api_env, ingestor_env)
        for item in environment.values()
        if (secret_key_ref := item.get("valueFrom", {}).get("secretKeyRef"))
    }
    assert cutover_secret_keys.isdisjoint(rendered_secret_keys)
    assert not any(
        document.get("kind") == "Job" and document.get("metadata", {}).get("name") == "verdify-runtime-role-bootstrap"
        for document in documents
    )


def test_production_presync_hook_explicitly_runs_ledgered_migrations():
    documents = _render(PROD)
    migration = _job(documents, "verdify-migrate")
    assert migration["metadata"]["annotations"]["argocd.argoproj.io/hook"] == "PreSync"
    migration_env = _pod_container_env(migration, "migrate")

    # This must be explicit on the newly rendered hook. A ConfigMap-only flag
    # would still be old while PreSync runs on the first schema rollout.
    assert migration_env["VERDIFY_MIGRATE_LEDGER"] == {"name": "VERDIFY_MIGRATE_LEDGER", "value": "1"}
    assert "VERDIFY_MIGRATE_ALLOW_BASELINE" not in migration_env
