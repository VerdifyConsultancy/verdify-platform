"""Initial-only password lifecycle; execution never grants rotation authority."""

import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "six_bootstrap", Path(__file__).resolve().parents[1] / "scripts/bootstrap-six-runtime-roles.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("DB_ADMIN_USER", "verdify")
    monkeypatch.setenv("DB_ADMIN_PASSWORD", "owner-private-fixture")
    for index, duty in enumerate(module.DUTIES):
        monkeypatch.setenv(duty.upper() + "_DB_PASSWORD", ("A_" + str(index) + "-").ljust(64, "z"))
    return monkeypatch


def executor(monkeypatch, *, absent=True, denied=None):
    calls = []
    state = {"verdify_" + d + "_runtime_login": absent for d in module.DUTIES}

    def run(user, password, commands, **kwargs):
        calls.append((user, commands, kwargs))
        if "json_object_agg" in commands[0]:
            return json.dumps(state)
        if user == denied:
            raise module.BootstrapError("authentication refused")
        return "t"

    monkeypatch.setattr(module, "psql", run)
    return calls


def test_initial_only_transaction_and_exact_six_targets(configured):
    calls = executor(configured)
    module.bootstrap()
    installs = [c for c in calls if c[2].get("transactional")]
    assert len(installs) == 1
    _, commands, kwargs = installs[0]
    assert "LOCK TABLE pg_catalog.pg_authid" in commands[0]
    assert "IS DISTINCT FROM TRUE" in commands[0]
    assert "admin_option" in commands[0]
    assert commands[1:] == ["\\password verdify_" + d + "_runtime_login" for d in module.DUTIES]
    assert all(
        "api_runtime_login" not in c and "ingestor_runtime_login" not in c and "mcp_runtime_login" not in c
        for c in commands[1:]
    )
    for duty in module.DUTIES:
        secret = module.os.environ[duty.upper() + "_DB_PASSWORD"]
        assert secret not in str(commands)
        assert kwargs["password_input"].count(secret) == 2


def test_existing_verifiers_authenticate_without_reinstallation(configured):
    calls = executor(configured, absent=False)
    module.bootstrap()
    assert not any(c[2].get("transactional") for c in calls)
    assert {c[0] for c in calls if c[0] != "verdify"} == {"verdify_" + d + "_runtime_login" for d in module.DUTIES}


def test_mismatched_existing_secret_cannot_rotate(configured):
    calls = executor(configured, absent=False, denied="verdify_planner_runtime_login")
    with pytest.raises(module.BootstrapError):
        module.bootstrap()
    assert not any(c[2].get("transactional") for c in calls)


@pytest.mark.parametrize("bad", ["", "x" * 63, "x" * 65, "'" * 64, "\\" * 64])
def test_unsafe_password_shape_has_no_database_effect(configured, bad):
    configured.setenv("PLANNER_DB_PASSWORD", bad)
    calls = executor(configured)
    with pytest.raises(module.BootstrapError):
        module.bootstrap()
    assert calls == []


def test_duplicate_credentials_have_no_database_effect(configured):
    configured.setenv("PLANNER_DB_PASSWORD", module.os.environ["GRAFANA_DB_PASSWORD"])
    calls = executor(configured)
    with pytest.raises(module.BootstrapError):
        module.bootstrap()
    assert calls == []


def test_real_psql_boundary_keeps_values_out_of_argv_and_errors(configured):
    for name, value in {"DB_HOST": "db", "DB_PORT": "5432", "DB_NAME": "fixture"}.items():
        configured.setenv(name, value)
    secret = "private-password-fixture"
    observed = {}

    def run(args, **kwargs):
        observed.update(args=args, kwargs=kwargs)
        return type("Result", (), {"returncode": 1, "stdout": secret})()

    configured.setattr(module.subprocess, "run", run)
    with pytest.raises(module.BootstrapError, match="database bootstrap operation refused") as error:
        module.psql("verdify", secret, ["\\password verdify_planner_runtime_login"], password_input=secret)
    assert secret not in str(observed["args"])
    assert observed["kwargs"]["env"]["PGPASSWORD"] == secret
    assert observed["kwargs"]["stderr"] == module.subprocess.DEVNULL
    assert secret not in str(error.value)


def test_mixed_existing_and_missing_never_reinstalls_existing(configured):
    calls = executor(configured)
    original = module.psql

    def partial(user, password, commands, **kwargs):
        if "json_object_agg" in commands[0]:
            return json.dumps({"verdify_" + d + "_runtime_login": d != "grafana" for d in module.DUTIES})
        return original(user, password, commands, **kwargs)

    configured.setattr(module, "psql", partial)
    module.bootstrap()
    ((_, commands, kwargs),) = [c for c in calls if c[2].get("transactional")]
    assert "\\password verdify_grafana_runtime_login" not in commands
    assert module.os.environ["GRAFANA_DB_PASSWORD"] not in kwargs["password_input"]


def test_native_hook_order_and_exact_secret_custody_contract():
    import yaml

    root = Path(__file__).resolve().parents[1]
    job = yaml.safe_load(
        (root / "deploy/k8s/components/runtime-role-boundary/six-workload-bootstrap-job.yaml").read_text()
    )
    assert job["metadata"]["annotations"]["argocd.argoproj.io/hook"] == "PreSync"
    assert job["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"] == "3"
    pod = job["spec"]["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    container = pod["containers"][0]
    assert container["command"] == ["python3", "/scripts/bootstrap-six-runtime-roles.py"]
    assert container["image"] == "ghcr.io/verdifyconsultancy/verdify-migrate"
    refs = {
        e["name"]: e["valueFrom"]["secretKeyRef"]
        for e in container["env"]
        if e.get("valueFrom", {}).get("secretKeyRef")
    }
    for duty in module.DUTIES:
        assert refs[duty.upper() + "_DB_PASSWORD"] == {
            "name": "verdify-" + duty.replace("_", "-") + "-runtime-db",
            "key": "password",
        }
    assert set(refs) == {"DB_ADMIN_PASSWORD", *(d.upper() + "_DB_PASSWORD" for d in module.DUTIES)}


@pytest.mark.parametrize("answer", ["f", "", "NULL"])
def test_missing_or_unknown_prerequisite_cannot_reach_any_password_command(configured, answer):
    calls = []

    def refuse(user, password, commands, **kwargs):
        calls.append((commands, kwargs))
        return answer

    configured.setattr(module, "psql", refuse)
    with pytest.raises(module.BootstrapError, match="immutable migration/seal prerequisite"):
        module.bootstrap()
    assert len(calls) == 1
    assert not calls[0][1].get("transactional")
    assert not any("\\password" in c for c in calls[0][0])


def test_guard_sql_requires_both_exact_receipts_and_null_role_rows_fail_closed():
    sql = module.sealed_sql()
    assert "count(*)=2" in sql
    for duty in ("api", "ingestor"):
        assert "r.login_name='verdify_" + duty + "_runtime_login'" in sql
        assert "decode('" + module.PREDECESSORS[duty] + "','hex')" in sql
    assert "bool_and(COALESCE((" in module.admin_role_contract_sql()
    root = Path(__file__).resolve().parents[1]
    migration = (root / "db/migrations/268-six-runtime-workload-role-boundaries.sql").read_bytes()
    import hashlib

    assert hashlib.sha256(migration).hexdigest() == module.MIGRATION_SHA


def test_bootstrap_requires_exact_facility_status_successor_after_atomic_migration():
    import hashlib

    root = Path(__file__).resolve().parents[1]
    migration = (root / "db/migrations/270-facility-safe-ops-projection.sql").read_bytes()
    assert hashlib.sha256(migration).hexdigest() == module.OPS_MIGRATION_SHA
    assert "seq=270" in module.sealed_sql()
    assert module.OPS_MIGRATION_SHA in module.sealed_sql()
