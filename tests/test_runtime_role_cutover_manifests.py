"""Active migration-217 workload identity and owner-retirement contract."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parents[1]
PROD = REPO_ROOT / "deploy/k8s/overlays/prod"
REVIEW_ALIAS = REPO_ROOT / "deploy/k8s/overlays/prod-runtime-role-boundary"


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


def test_review_alias_is_byte_equivalent_to_active_production_target():
    assert _render(REVIEW_ALIAS) == _render(PROD)


def test_active_cutover_removes_owner_password_and_binds_exact_runtime_keys():
    documents = _render(REVIEW_ALIAS)
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


def test_active_cutover_bootstraps_and_attests_both_actual_logins_before_sync():
    documents = _render(REVIEW_ALIAS)
    bootstrap = _job(documents, "verdify-runtime-role-bootstrap")
    migration = _job(documents, "verdify-migrate")

    annotations = bootstrap["metadata"]["annotations"]
    assert bootstrap["metadata"]["namespace"] == "verdify-prod"
    assert annotations["argocd.argoproj.io/hook"] == "PreSync"
    assert annotations["argocd.argoproj.io/hook-delete-policy"] == "BeforeHookCreation"
    assert int(migration["metadata"]["annotations"].get("argocd.argoproj.io/sync-wave", "0")) == 0
    assert int(annotations["argocd.argoproj.io/sync-wave"]) == 1

    spec = bootstrap["spec"]
    assert spec["backoffLimit"] == 0
    assert spec["activeDeadlineSeconds"] == 600
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

    assert {item["name"] for item in pod["containers"]} == {
        "bootstrap-and-attest",
        "experiment-bootstrap-and-attest",
        "direct-launch-bootstrap",
    }
    container = next(item for item in pod["containers"] if item["name"] == "bootstrap-and-attest")
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
    assert '[ "${#VERDIFY_API_RUNTIME_DB_PASSWORD}" -eq 64 ]' in script
    assert '[ "${#VERDIFY_INGESTOR_RUNTIME_DB_PASSWORD}" -eq 64 ]' in script
    assert script.count('*[!0-9a-f]*) fail "') == 2
    assert "printf '%s\\n%s\\n%s\\n%s\\n'" in script
    password_pipe = script.split("if ! printf", 1)[1].split("PGPASSWORD=", 1)[0]
    assert password_pipe.count('"${VERDIFY_API_RUNTIME_DB_PASSWORD}"') == 2
    assert password_pipe.count('"${VERDIFY_INGESTOR_RUNTIME_DB_PASSWORD}"') == 2
    assert password_pipe.rstrip().endswith("|")
    assert "--single-transaction" in script
    assert "--command=\"SET password_encryption = 'scram-sha-256'\"" in script
    assert "--command='\\password verdify_api_runtime_login'" in script
    assert "--command='\\password verdify_ingestor_runtime_login'" in script
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
        "\\getenv api_runtime_password",
        "\\getenv ingestor_runtime_password",
        "PASSWORD :'",
        "ALTER ROLE verdify_api_runtime_login",
        "ALTER ROLE verdify_ingestor_runtime_login",
        "ESP32",
        "MQTT",
    ):
        assert forbidden not in script
    assert all("PASSWORD}" not in line for line in script.splitlines() if "printf" in line or 'fail "' in line)


def test_bootstrap_pipes_raw_passwords_only_to_non_echoing_psql_prompts(tmp_path: Path):
    documents = _render(REVIEW_ALIAS)
    bootstrap = _job(documents, "verdify-runtime-role-bootstrap")
    container = next(
        item for item in bootstrap["spec"]["template"]["spec"]["containers"] if item["name"] == "bootstrap-and-attest"
    )
    script = container["args"][0]

    fake_psql = tmp_path / "psql"
    fake_psql.write_text(
        """#!/bin/sh
set -eu
args=" $* "
for arg in "$@"; do
  for secret in \
    "$DB_ADMIN_PASSWORD" \
    "$VERDIFY_API_RUNTIME_DB_PASSWORD" \
    "$VERDIFY_INGESTOR_RUNTIME_DB_PASSWORD"
  do
    case "$arg" in
      *"$secret"*) exit 90 ;;
    esac
  done
done

case "$args" in
  *" --single-transaction "*)
    [ "$PGPASSWORD" = "$DB_ADMIN_PASSWORD" ] || exit 91
    have_scram=0
    have_api=0
    have_ingestor=0
    for arg in "$@"; do
      case "$arg" in
        "--command=SET password_encryption = 'scram-sha-256'") have_scram=1 ;;
        '--command=\\password verdify_api_runtime_login') have_api=1 ;;
        '--command=\\password verdify_ingestor_runtime_login') have_ingestor=1 ;;
      esac
    done
    [ "$have_scram:$have_api:$have_ingestor" = "1:1:1" ] || exit 92
    IFS= read -r api_first || exit 93
    IFS= read -r api_second || exit 94
    IFS= read -r ingestor_first || exit 95
    IFS= read -r ingestor_second || exit 96
    [ "$api_first" = "$VERDIFY_API_RUNTIME_DB_PASSWORD" ] || exit 97
    [ "$api_second" = "$VERDIFY_API_RUNTIME_DB_PASSWORD" ] || exit 98
    [ "$ingestor_first" = "$VERDIFY_INGESTOR_RUNTIME_DB_PASSWORD" ] || exit 99
    [ "$ingestor_second" = "$VERDIFY_INGESTOR_RUNTIME_DB_PASSWORD" ] || exit 100
    if IFS= read -r unexpected; then exit 101; fi
    printf '%s\n' install >>"$FAKE_PSQL_TRACE"
    ;;
  *" -U verdify_api_runtime_login "*)
    [ "$PGPASSWORD" = "$VERDIFY_API_RUNTIME_DB_PASSWORD" ] || exit 102
    payload="$(cat)"
    case "$payload" in
      *"$DB_ADMIN_PASSWORD"*|*"$VERDIFY_API_RUNTIME_DB_PASSWORD"*|*"$VERDIFY_INGESTOR_RUNTIME_DB_PASSWORD"*)
        exit 103
        ;;
    esac
    printf '%s\n' api-attest >>"$FAKE_PSQL_TRACE"
    printf '%s\n' t
    ;;
  *" -U verdify_ingestor_runtime_login "*)
    [ "$PGPASSWORD" = "$VERDIFY_INGESTOR_RUNTIME_DB_PASSWORD" ] || exit 104
    payload="$(cat)"
    case "$payload" in
      *"$DB_ADMIN_PASSWORD"*|*"$VERDIFY_API_RUNTIME_DB_PASSWORD"*|*"$VERDIFY_INGESTOR_RUNTIME_DB_PASSWORD"*)
        exit 105
        ;;
    esac
    printf '%s\n' ingestor-attest >>"$FAKE_PSQL_TRACE"
    printf '%s\n' t
    ;;
  *) exit 106 ;;
esac
"""
    )
    fake_psql.chmod(0o755)
    trace = tmp_path / "psql.trace"
    api_password = "a" * 64
    ingestor_password = "b" * 64
    admin_password = "owner-password-marker-never-printed"
    environment = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "DB_HOST": "db.invalid",
        "DB_PORT": "5432",
        "DB_NAME": "verdify",
        "DB_ADMIN_USER": "verdify",
        "DB_ADMIN_PASSWORD": admin_password,
        "VERDIFY_API_RUNTIME_DB_USER": "verdify_api_runtime_login",
        "VERDIFY_API_RUNTIME_DB_PASSWORD": api_password,
        "VERDIFY_INGESTOR_RUNTIME_DB_USER": "verdify_ingestor_runtime_login",
        "VERDIFY_INGESTOR_RUNTIME_DB_PASSWORD": ingestor_password,
        "FAKE_PSQL_TRACE": str(trace),
    }
    result = subprocess.run(
        ["/bin/sh", "-ec", script],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    combined_output = result.stdout + result.stderr
    assert "both ordinary logins installed and attested" in combined_output
    for secret in (admin_password, api_password, ingestor_password):
        assert secret not in combined_output
    assert trace.read_text().splitlines() == ["install", "api-attest", "ingestor-attest"]


def test_bootstrap_rejects_noncanonical_passwords_before_psql(tmp_path: Path):
    documents = _render(REVIEW_ALIAS)
    bootstrap = _job(documents, "verdify-runtime-role-bootstrap")
    container = next(
        item for item in bootstrap["spec"]["template"]["spec"]["containers"] if item["name"] == "bootstrap-and-attest"
    )
    script = container["args"][0]

    fake_psql = tmp_path / "psql"
    fake_psql.write_text(
        """#!/bin/sh
: >"$FAKE_PSQL_CALLED"
exit 0
"""
    )
    fake_psql.chmod(0o755)
    invalid_cases = (
        ("a" * 63, "b" * 64),
        ("A" + "a" * 63, "b" * 64),
        ("a" * 63 + "\n", "b" * 64),
        ("a" * 64, "g" + "b" * 63),
    )

    for index, (api_password, ingestor_password) in enumerate(invalid_cases):
        called = tmp_path / f"psql-called-{index}"
        environment = {
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "DB_HOST": "db.invalid",
            "DB_PORT": "5432",
            "DB_NAME": "verdify",
            "DB_ADMIN_USER": "verdify",
            "DB_ADMIN_PASSWORD": "owner-password-marker-never-printed",
            "VERDIFY_API_RUNTIME_DB_USER": "verdify_api_runtime_login",
            "VERDIFY_API_RUNTIME_DB_PASSWORD": api_password,
            "VERDIFY_INGESTOR_RUNTIME_DB_USER": "verdify_ingestor_runtime_login",
            "VERDIFY_INGESTOR_RUNTIME_DB_PASSWORD": ingestor_password,
            "FAKE_PSQL_CALLED": str(called),
        }
        result = subprocess.run(
            ["/bin/sh", "-ec", script],
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 1
        assert not called.exists()
        combined_output = result.stdout + result.stderr
        assert "runtime credential shape mismatch" in combined_output
        assert api_password not in combined_output
        assert ingestor_password not in combined_output


def test_cutover_is_active_in_production_with_experiment_still_off():
    documents = _render(PROD)
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

    bootstrap = _job(documents, "verdify-runtime-role-bootstrap")
    migration = _job(documents, "verdify-migrate")
    bootstrap_image = next(
        item["image"]
        for item in bootstrap["spec"]["template"]["spec"]["containers"]
        if item["name"] == "bootstrap-and-attest"
    )
    migration_image = migration["spec"]["template"]["spec"]["containers"][0]["image"]
    assert bootstrap_image == migration_image
    assert bootstrap_image.startswith("registry.vallery.net/verdifyconsultancy/verdify-migrate@sha256:")

    config = next(
        document
        for document in documents
        if document.get("kind") == "ConfigMap" and document["metadata"]["name"] == "verdify-config"
    )["data"]
    assert config["VERDIFY_POLICY_VECTOR_MODE"] == "off"
    assert config.get("VERDIFY_COMPONENT_EXPERIMENT_ENABLED", "off") == "off"
    assert not any(document.get("kind") == "Secret" for document in documents)


def test_production_presync_hook_explicitly_runs_ledgered_migrations():
    documents = _render(PROD)
    migration = _job(documents, "verdify-migrate")
    assert migration["metadata"]["annotations"]["argocd.argoproj.io/hook"] == "PreSync"
    migration_env = _pod_container_env(migration, "migrate")

    # This must be explicit on the newly rendered hook. A ConfigMap-only flag
    # would still be old while PreSync runs on the first schema rollout.
    assert migration_env["VERDIFY_MIGRATE_LEDGER"] == {"name": "VERDIFY_MIGRATE_LEDGER", "value": "1"}
    assert "VERDIFY_MIGRATE_ALLOW_BASELINE" not in migration_env
