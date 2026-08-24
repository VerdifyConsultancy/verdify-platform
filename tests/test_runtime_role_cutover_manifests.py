"""Staged migration-217 workload identity and owner-retirement contract."""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parents[1]
PROD = REPO_ROOT / "deploy/k8s/overlays/prod"
STAGED = REPO_ROOT / "deploy/k8s/overlays/prod-runtime-role-boundary"


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


def test_production_presync_hook_explicitly_runs_ledgered_migrations():
    documents = _render(PROD)
    migration = _job(documents, "verdify-migrate")
    assert migration["metadata"]["annotations"]["argocd.argoproj.io/hook"] == "PreSync"
    migration_env = _pod_container_env(migration, "migrate")

    # This must be explicit on the newly rendered hook. A ConfigMap-only flag
    # would still be old while PreSync runs on the first schema rollout.
    assert migration_env["VERDIFY_MIGRATE_LEDGER"] == {"name": "VERDIFY_MIGRATE_LEDGER", "value": "1"}
    assert "VERDIFY_MIGRATE_ALLOW_BASELINE" not in migration_env
