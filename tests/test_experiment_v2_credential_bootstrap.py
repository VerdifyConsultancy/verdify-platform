"""Gate-2 experiment credential install/attestation manifest contract."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parents[1]
PROD = REPO_ROOT / "deploy/k8s/overlays/prod"
JOB_NAME = "verdify-experiment-v2-credential-bootstrap"

LOGIN_DUTY = {
    "verdify_experiment_v2_lifecycle_login": "verdify_experiment_lifecycle",
    "verdify_experiment_v2_component_executor_login": "verdify_experiment_component_executor",
    "verdify_experiment_v2_equipment_source_collector_login": ("verdify_experiment_equipment_source_collector"),
    "verdify_experiment_v2_shadow_scheduler_login": "verdify_experiment_shadow_scheduler",
    "verdify_experiment_v2_randomizer_login": "verdify_experiment_randomizer",
    "verdify_experiment_v2_outcome_freezer_login": "verdify_experiment_outcome_freezer",
}


@pytest.fixture(scope="module")
def rendered() -> list[dict]:
    result = subprocess.run(
        ["kustomize", "build", str(PROD)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [document for document in yaml.safe_load_all(result.stdout) if document]


def _job(documents: list[dict], name: str) -> dict:
    return next(
        document for document in documents if document.get("kind") == "Job" and document["metadata"]["name"] == name
    )


def _container(job: dict) -> dict:
    containers = job["spec"]["template"]["spec"]["containers"]
    return next(
        (container for container in containers if container["name"] == "bootstrap-and-attest"),
        containers[0],
    )


def _env(job: dict) -> dict[str, dict]:
    return {item["name"]: item for item in _container(job)["env"]}


def _script(documents: list[dict]) -> str:
    return _container(_job(documents, JOB_NAME))["args"][0]


def _secret_ref(item: dict, name: str, key: str) -> None:
    assert item["valueFrom"]["secretKeyRef"] == {"name": name, "key": key}


def test_bootstrap_is_wave_ordered_bounded_and_uses_exact_migrate_image(rendered: list[dict]) -> None:
    migration = _job(rendered, "verdify-migrate")
    ordinary = _job(rendered, "verdify-runtime-role-bootstrap")
    experiment = _job(rendered, JOB_NAME)
    annotations = experiment["metadata"]["annotations"]

    assert experiment["metadata"]["namespace"] == "verdify-prod"
    assert annotations == {
        "argocd.argoproj.io/hook": "PreSync",
        "argocd.argoproj.io/hook-delete-policy": "BeforeHookCreation",
        "argocd.argoproj.io/sync-wave": "2",
    }
    assert int(migration["metadata"]["annotations"].get("argocd.argoproj.io/sync-wave", "0")) == 0
    assert ordinary["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"] == "1"
    assert experiment["spec"]["backoffLimit"] == 0
    assert experiment["spec"]["activeDeadlineSeconds"] == 240
    assert experiment["spec"]["ttlSecondsAfterFinished"] == 600

    pod = experiment["spec"]["template"]["spec"]
    assert pod["restartPolicy"] == "Never"
    assert "hostNetwork" not in pod
    assert pod["automountServiceAccountToken"] is False
    assert pod["enableServiceLinks"] is False
    assert "serviceAccountName" not in pod
    assert pod["imagePullSecrets"] == [{"name": "zot-origin-cluster-pull"}]
    assert experiment["spec"]["template"]["metadata"]["labels"]["app.kubernetes.io/component"] == "migrate"

    container = _container(experiment)
    assert container["image"] == _container(migration)["image"]
    assert container["image"].startswith("registry.vallery.net/verdifyconsultancy/verdify-migrate@sha256:")
    assert container["command"] == ["/bin/sh", "-ec"]
    assert container["securityContext"] == {
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": True,
        "capabilities": {"drop": ["ALL"]},
    }
    assert not any(document.get("kind") == "Secret" for document in rendered)


def test_bootstrap_consumes_only_the_locked_secret_names(rendered: list[dict]) -> None:
    env = _env(_job(rendered, JOB_NAME))
    for name in ("DB_HOST", "DB_PORT", "DB_NAME"):
        assert env[name]["valueFrom"]["configMapKeyRef"] == {
            "name": "verdify-config",
            "key": name,
        }
    assert env["DB_ADMIN_USER"]["valueFrom"]["configMapKeyRef"] == {
        "name": "verdify-config",
        "key": "DB_USER",
    }
    _secret_ref(env["DB_ADMIN_PASSWORD"], "verdify-app-secrets", "POSTGRES_PASSWORD")
    _secret_ref(
        env["ORDINARY_API_DB_PASSWORD"],
        "verdify-app-secrets",
        "VERDIFY_API_RUNTIME_DB_PASSWORD",
    )
    _secret_ref(
        env["ORDINARY_INGESTOR_DB_PASSWORD"],
        "verdify-app-secrets",
        "VERDIFY_INGESTOR_RUNTIME_DB_PASSWORD",
    )
    for name in (
        "VERDIFY_EXPERIMENT_LIFECYCLE_DB_USER",
        "VERDIFY_EXPERIMENT_LIFECYCLE_DB_PASSWORD",
        "VERDIFY_EXPERIMENT_COMPONENT_DB_USER",
        "VERDIFY_EXPERIMENT_COMPONENT_DB_PASSWORD",
        "VERDIFY_EXPERIMENT_EQUIPMENT_SOURCE_COLLECTOR_DB_USER",
        "VERDIFY_EXPERIMENT_EQUIPMENT_SOURCE_COLLECTOR_DB_PASSWORD",
        "VERDIFY_EXPERIMENT_API_TOKEN",
        "VERDIFY_EXPERIMENT_OPERATOR_TOKEN",
    ):
        _secret_ref(env[name], "verdify-app-secrets", name)

    named = {
        "VERDIFY_EXPERIMENT_SHADOW_SCHEDULER_DB_PASSWORD": ("verdify-experiment-v2-shadow-scheduler-db"),
        "VERDIFY_EXPERIMENT_RANDOMIZER_DB_PASSWORD": "verdify-experiment-v2-randomizer-db",
        "VERDIFY_EXPERIMENT_OUTCOME_FREEZER_DB_PASSWORD": ("verdify-experiment-v2-outcome-freezer-db"),
    }
    for env_name, secret_name in named.items():
        _secret_ref(env[env_name], secret_name, "password")
    assert "VERDIFY_EXPERIMENT_SELECTOR_API_KEY" not in env

    fixed_users = {
        "VERDIFY_EXPERIMENT_SHADOW_SCHEDULER_DB_USER": ("verdify_experiment_v2_shadow_scheduler_login"),
        "VERDIFY_EXPERIMENT_RANDOMIZER_DB_USER": "verdify_experiment_v2_randomizer_login",
        "VERDIFY_EXPERIMENT_OUTCOME_FREEZER_DB_USER": ("verdify_experiment_v2_outcome_freezer_login"),
    }
    for env_name, value in fixed_users.items():
        assert env[env_name] == {"name": env_name, "value": value}


def test_script_is_transactional_nonlogging_and_exact_duty_locked(rendered: list[dict]) -> None:
    script = _script(rendered)
    subprocess.run(
        ["/bin/sh", "-n"],
        input=script,
        text=True,
        check=True,
        capture_output=True,
    )
    assert "--single-transaction" in script
    assert "SET password_encryption = 'scram-sha-256'" in script
    for login, duty in LOGIN_DUTY.items():
        assert f"--command='\\password {login}'" in script
        assert f"attest_login {login}" in script
        assert duty in script
    assert script.count("--command='\\password verdify_experiment") == 6
    assert script.count("attest_login verdify_experiment") == 6
    assert script.count("CREDENTIAL_LABEL=") == 8
    assert "candidate.proname LIKE 'fn_experiment_v2_%'" in script
    assert "candidate.proname LIKE 'fn_record_equipment_%'" in script
    assert "has_protected_relation_privilege" not in script  # boolean is evaluated inline
    assert "has_table_privilege(" in script
    assert "has_sequence_privilege(" in script
    assert "six database logins installed and attested; API token shapes validated" in script

    for forbidden in (
        "set -x",
        "printenv",
        "postgresql://",
        "--password=",
        "ALTER ROLE",
        "ESP32",
        "MQTT",
        "VERDIFY_EXPERIMENT_SELECTOR_API_KEY",
    ):
        assert forbidden not in script
    for line in script.splitlines():
        if "psql " in line or "--set=" in line:
            assert "_PASSWORD}" not in line
            assert "_TOKEN}" not in line


def test_job_function_allowlists_are_lockstep_with_ledgered_grants(rendered: list[dict]) -> None:
    script = _script(rendered)
    pairs = re.findall(r"^\s*\('([^']+)', '([^']+)'\),?$", script, re.MULTILINE)
    job_allowlists: dict[str, set[str]] = {}
    for duty, signature in pairs:
        job_allowlists.setdefault(duty, set()).add(signature)

    blocks = []
    for migration_name in (
        "214-confirmed-component-experiment-v2.sql",
        "220-experiment-v2-direct-randomized-launch.sql",
        "222-experiment-v2-direct-physical-proof.sql",
    ):
        migration = (REPO_ROOT / "db/migrations" / migration_name).read_text()
        blocks.extend(
            re.findall(
                r"FOREACH fn IN ARRAY ARRAY\[(.*?)\] LOOP(.*?)END LOOP;",
                migration,
                re.DOTALL,
            )
        )

    def normalize(signature: str) -> str:
        return signature.replace("timestamptz", "timestamp with time zone")

    for duty in (
        "verdify_experiment_lifecycle",
        "verdify_experiment_shadow_scheduler",
        "verdify_experiment_randomizer",
        "verdify_experiment_component_executor",
        "verdify_experiment_outcome_freezer",
    ):
        matching = [array for array, grant in blocks if f"TO {duty}" in grant]
        assert len(matching) == (3 if duty == "verdify_experiment_lifecycle" else 1)
        expected = {signature for array in matching for signature in re.findall(r"'([^']+)'::regprocedure", array)}
        if duty == "verdify_experiment_outcome_freezer":
            # Migration 216 adds the least-information source-cycle grant.
            expected.add("public.fn_experiment_v2_outcome_source_cycle(uuid)")
        assert {normalize(item) for item in job_allowlists[duty]} == {normalize(item) for item in expected}

    assert job_allowlists["verdify_experiment_equipment_source_collector"] == {
        "public.fn_record_equipment_counter_sample(uuid,timestamp with time zone,text,text,text,double precision,text,uuid,double precision,uuid,bigint,text)",
        "public.fn_record_equipment_direct_state_snapshot(uuid,uuid,text,text,jsonb,double precision,uuid,bigint,text)",
        "public.fn_record_equipment_state_source_receipt(uuid,timestamp with time zone,text,text,jsonb,boolean,uuid,bigint,text)",
    }
    assert sum(map(len, job_allowlists.values())) == 56


def _activation_env(tmp_path: Path) -> tuple[dict[str, str], tuple[str, ...]]:
    activation_values = tuple(character * 64 for character in "12345678")
    environment = {
        **os.environ,
        "DB_HOST": "db.invalid",
        "DB_PORT": "5432",
        "DB_NAME": "verdify",
        "DB_ADMIN_USER": "verdify",
        "DB_ADMIN_PASSWORD": "owner-password-marker-never-printed",
        "ORDINARY_API_DB_PASSWORD": "a" * 64,
        "ORDINARY_INGESTOR_DB_PASSWORD": "b" * 64,
        "VERDIFY_EXPERIMENT_LIFECYCLE_DB_USER": "verdify_experiment_v2_lifecycle_login",
        "VERDIFY_EXPERIMENT_LIFECYCLE_DB_PASSWORD": activation_values[0],
        "VERDIFY_EXPERIMENT_COMPONENT_DB_USER": "verdify_experiment_v2_component_executor_login",
        "VERDIFY_EXPERIMENT_COMPONENT_DB_PASSWORD": activation_values[1],
        "VERDIFY_EXPERIMENT_EQUIPMENT_SOURCE_COLLECTOR_DB_USER": (
            "verdify_experiment_v2_equipment_source_collector_login"
        ),
        "VERDIFY_EXPERIMENT_EQUIPMENT_SOURCE_COLLECTOR_DB_PASSWORD": activation_values[2],
        "VERDIFY_EXPERIMENT_SHADOW_SCHEDULER_DB_USER": ("verdify_experiment_v2_shadow_scheduler_login"),
        "VERDIFY_EXPERIMENT_SHADOW_SCHEDULER_DB_PASSWORD": activation_values[3],
        "VERDIFY_EXPERIMENT_RANDOMIZER_DB_USER": "verdify_experiment_v2_randomizer_login",
        "VERDIFY_EXPERIMENT_RANDOMIZER_DB_PASSWORD": activation_values[4],
        "VERDIFY_EXPERIMENT_OUTCOME_FREEZER_DB_USER": ("verdify_experiment_v2_outcome_freezer_login"),
        "VERDIFY_EXPERIMENT_OUTCOME_FREEZER_DB_PASSWORD": activation_values[5],
        "VERDIFY_EXPERIMENT_API_TOKEN": activation_values[6],
        "VERDIFY_EXPERIMENT_OPERATOR_TOKEN": activation_values[7],
        "FAKE_PSQL_TRACE": str(tmp_path / "psql.trace"),
    }
    return environment, activation_values


def _install_fake_psql(tmp_path: Path) -> Path:
    fake = tmp_path / "psql"
    fake.write_text(
        r"""#!/bin/sh
set -eu
args=" $* "
for argument in "$@"; do
  for secret in \
    "$DB_ADMIN_PASSWORD" \
    "$ORDINARY_API_DB_PASSWORD" \
    "$ORDINARY_INGESTOR_DB_PASSWORD" \
    "$VERDIFY_EXPERIMENT_LIFECYCLE_DB_PASSWORD" \
    "$VERDIFY_EXPERIMENT_COMPONENT_DB_PASSWORD" \
    "$VERDIFY_EXPERIMENT_EQUIPMENT_SOURCE_COLLECTOR_DB_PASSWORD" \
    "$VERDIFY_EXPERIMENT_SHADOW_SCHEDULER_DB_PASSWORD" \
    "$VERDIFY_EXPERIMENT_RANDOMIZER_DB_PASSWORD" \
    "$VERDIFY_EXPERIMENT_OUTCOME_FREEZER_DB_PASSWORD" \
    "$VERDIFY_EXPERIMENT_API_TOKEN" \
    "$VERDIFY_EXPERIMENT_OPERATOR_TOKEN"
  do
    case "$argument" in *"$secret"*) exit 80 ;; esac
  done
done

case "$args" in
  *" --single-transaction "*)
    [ "$PGPASSWORD" = "$DB_ADMIN_PASSWORD" ] || exit 81
    IFS= read -r lifecycle_1 && IFS= read -r lifecycle_2
    IFS= read -r component_1 && IFS= read -r component_2
    IFS= read -r source_1 && IFS= read -r source_2
    IFS= read -r shadow_1 && IFS= read -r shadow_2
    IFS= read -r randomizer_1 && IFS= read -r randomizer_2
    IFS= read -r freezer_1 && IFS= read -r freezer_2 || exit 82
    [ "$lifecycle_1:$lifecycle_2" = "$VERDIFY_EXPERIMENT_LIFECYCLE_DB_PASSWORD:$VERDIFY_EXPERIMENT_LIFECYCLE_DB_PASSWORD" ] || exit 83
    [ "$component_1:$component_2" = "$VERDIFY_EXPERIMENT_COMPONENT_DB_PASSWORD:$VERDIFY_EXPERIMENT_COMPONENT_DB_PASSWORD" ] || exit 84
    [ "$source_1:$source_2" = "$VERDIFY_EXPERIMENT_EQUIPMENT_SOURCE_COLLECTOR_DB_PASSWORD:$VERDIFY_EXPERIMENT_EQUIPMENT_SOURCE_COLLECTOR_DB_PASSWORD" ] || exit 85
    [ "$shadow_1:$shadow_2" = "$VERDIFY_EXPERIMENT_SHADOW_SCHEDULER_DB_PASSWORD:$VERDIFY_EXPERIMENT_SHADOW_SCHEDULER_DB_PASSWORD" ] || exit 86
    [ "$randomizer_1:$randomizer_2" = "$VERDIFY_EXPERIMENT_RANDOMIZER_DB_PASSWORD:$VERDIFY_EXPERIMENT_RANDOMIZER_DB_PASSWORD" ] || exit 87
    [ "$freezer_1:$freezer_2" = "$VERDIFY_EXPERIMENT_OUTCOME_FREEZER_DB_PASSWORD:$VERDIFY_EXPERIMENT_OUTCOME_FREEZER_DB_PASSWORD" ] || exit 88
    if IFS= read -r unexpected; then exit 89; fi
    printf '%s\n' install >>"$FAKE_PSQL_TRACE"
    ;;
  *)
    login=
    previous=
    for argument in "$@"; do
      if [ "$previous" = "-U" ]; then login="$argument"; fi
      previous="$argument"
    done
    case "$login" in
      verdify_experiment_v2_lifecycle_login) expected="$VERDIFY_EXPERIMENT_LIFECYCLE_DB_PASSWORD" ;;
      verdify_experiment_v2_component_executor_login) expected="$VERDIFY_EXPERIMENT_COMPONENT_DB_PASSWORD" ;;
      verdify_experiment_v2_equipment_source_collector_login) expected="$VERDIFY_EXPERIMENT_EQUIPMENT_SOURCE_COLLECTOR_DB_PASSWORD" ;;
      verdify_experiment_v2_shadow_scheduler_login) expected="$VERDIFY_EXPERIMENT_SHADOW_SCHEDULER_DB_PASSWORD" ;;
      verdify_experiment_v2_randomizer_login) expected="$VERDIFY_EXPERIMENT_RANDOMIZER_DB_PASSWORD" ;;
      verdify_experiment_v2_outcome_freezer_login) expected="$VERDIFY_EXPERIMENT_OUTCOME_FREEZER_DB_PASSWORD" ;;
      *) exit 90 ;;
    esac
    [ "$PGPASSWORD" = "$expected" ] || exit 91
    payload="$(cat)"
    for secret in \
      "$DB_ADMIN_PASSWORD" \
      "$VERDIFY_EXPERIMENT_LIFECYCLE_DB_PASSWORD" \
      "$VERDIFY_EXPERIMENT_COMPONENT_DB_PASSWORD" \
      "$VERDIFY_EXPERIMENT_EQUIPMENT_SOURCE_COLLECTOR_DB_PASSWORD" \
      "$VERDIFY_EXPERIMENT_SHADOW_SCHEDULER_DB_PASSWORD" \
      "$VERDIFY_EXPERIMENT_RANDOMIZER_DB_PASSWORD" \
      "$VERDIFY_EXPERIMENT_OUTCOME_FREEZER_DB_PASSWORD"
    do
      case "$payload" in *"$secret"*) exit 92 ;; esac
    done
    printf '%s\n' "$login" >>"$FAKE_PSQL_TRACE"
    printf '%s\n' t
    ;;
esac
"""
    )
    fake.chmod(0o755)
    return fake


def test_script_pipes_verifiers_and_attests_without_exposing_values(rendered: list[dict], tmp_path: Path) -> None:
    _install_fake_psql(tmp_path)
    environment, activation_values = _activation_env(tmp_path)
    environment["PATH"] = f"{tmp_path}:{os.environ['PATH']}"
    result = subprocess.run(
        ["/bin/sh", "-ec", _script(rendered)],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    output = result.stdout + result.stderr
    assert "six database logins installed and attested; API token shapes validated" in output
    for secret in (
        environment["DB_ADMIN_PASSWORD"],
        environment["ORDINARY_API_DB_PASSWORD"],
        environment["ORDINARY_INGESTOR_DB_PASSWORD"],
        *activation_values,
    ):
        assert secret not in output
    assert (tmp_path / "psql.trace").read_text().splitlines() == [
        "install",
        *LOGIN_DUTY,
    ]


@pytest.mark.parametrize("failure_kind", ["shape", "duplicate", "owner-reuse"])
def test_script_rejects_bad_activation_material_before_psql(
    rendered: list[dict], tmp_path: Path, failure_kind: str
) -> None:
    fake = tmp_path / "psql"
    fake.write_text('#!/bin/sh\n: >"$FAKE_PSQL_CALLED"\nexit 0\n')
    fake.chmod(0o755)
    environment, _ = _activation_env(tmp_path)
    environment["PATH"] = f"{tmp_path}:{os.environ['PATH']}"
    environment["FAKE_PSQL_CALLED"] = str(tmp_path / "called")
    if failure_kind == "shape":
        environment["VERDIFY_EXPERIMENT_LIFECYCLE_DB_PASSWORD"] = "A" * 64
    elif failure_kind == "duplicate":
        environment["VERDIFY_EXPERIMENT_COMPONENT_DB_PASSWORD"] = environment[
            "VERDIFY_EXPERIMENT_LIFECYCLE_DB_PASSWORD"
        ]
    else:
        environment["VERDIFY_EXPERIMENT_RANDOMIZER_DB_PASSWORD"] = environment["ORDINARY_API_DB_PASSWORD"]

    result = subprocess.run(
        ["/bin/sh", "-ec", _script(rendered)],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert not (tmp_path / "called").exists()
    output = result.stdout + result.stderr
    assert "fail-closed" in output
    for name, value in environment.items():
        if name.endswith("_PASSWORD") or name.endswith("_TOKEN"):
            assert value not in output
