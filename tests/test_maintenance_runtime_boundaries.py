"""Offline security/caller contracts; these tests never connect to a database."""

import hashlib
import importlib.util
import json
import os
import re
import shlex
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db/migrations/276-ordinary-maintenance-runtime-boundaries.sql"
spec = importlib.util.spec_from_file_location(
    "maintenance_bootstrap", ROOT / "scripts/bootstrap-maintenance-runtime-roles.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def watchdog():
    docs = yaml.safe_load_all((ROOT / "deploy/k8s/overlays/prod/writer-watchdog.yaml").read_text())
    job = next(d for d in docs if d["kind"] == "CronJob")
    return job, job["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("DB_ADMIN_USER", "verdify")
    monkeypatch.setenv("DB_ADMIN_PASSWORD", "private-owner-fixture")
    monkeypatch.setenv("DB_NAME", "verdify")
    for i, duty in enumerate(module.DUTIES):
        monkeypatch.setenv(duty.upper() + "_DB_PASSWORD", ("fixture_" + str(i)).ljust(64, "z"))
    return monkeypatch


def fake_psql(monkeypatch, *, nulls=None, refused=None):
    calls = []
    state = {"verdify_" + d + "_runtime_login": True for d in module.DUTIES} if nulls is None else nulls

    def run(user, password, commands, **kwargs):
        calls.append((user, commands, kwargs))
        if user == refused:
            raise module.BootstrapError("fixture authentication refused")
        if "json_object_agg" in commands[0]:
            return json.dumps(state)
        return "t"

    monkeypatch.setattr(module, "psql", run)
    return calls


def test_initial_install_is_atomic_fixed_two_and_private(configured):
    calls = fake_psql(configured)
    module.bootstrap()
    [(user, commands, kwargs)] = [c for c in calls if c[2].get("transactional")]
    assert user == "verdify"
    assert commands[1:] == ["\\password verdify_" + d + "_runtime_login" for d in module.DUTIES]
    assert "LOCK TABLE pg_catalog.pg_authid" in commands[0]
    assert "IS DISTINCT FROM TRUE" in commands[0]
    for protection in [
        "pg_stat_statements.track_utility=off",
        "pgaudit.log",
        "log_min_error_statement='panic'",
        "log_parameter_max_length_on_error=0",
        "password_encryption='scram-sha-256'",
    ]:
        assert protection in commands[0]
    for duty in module.DUTIES:
        value = os.environ[duty.upper() + "_DB_PASSWORD"]
        assert value not in str(commands)
        assert kwargs["password_input"].count(value) == 2
    assert not any("\\password verdify_api_" in str(c) for c in calls)


def test_matching_existing_verifiers_are_never_reinstalled(configured):
    state = {"verdify_" + d + "_runtime_login": False for d in module.DUTIES}
    calls = fake_psql(configured, nulls=state)
    module.bootstrap()
    assert not any(c[2].get("transactional") for c in calls)
    assert {c[0] for c in calls if c[0] != "verdify"} == set(state)


def test_wrong_existing_credential_blocks_missing_peer_install(configured):
    state = {"verdify_writer_watchdog_runtime_login": False, "verdify_matview_refresh_runtime_login": True}
    calls = fake_psql(configured, nulls=state, refused="verdify_writer_watchdog_runtime_login")
    with pytest.raises(module.BootstrapError):
        module.bootstrap()
    assert not any(c[2].get("transactional") for c in calls)


def test_duplicate_credentials_have_no_database_effect(configured):
    configured.setenv("WRITER_WATCHDOG_DB_PASSWORD", os.environ["MATVIEW_REFRESH_DB_PASSWORD"])
    calls = fake_psql(configured)
    with pytest.raises(module.BootstrapError):
        module.bootstrap()
    assert not calls


def test_ambiguous_install_transport_stops_without_replay(configured, capsys):
    calls = fake_psql(configured)
    original = module.psql
    installs = []

    def fail(user, password, commands, **kwargs):
        if kwargs.get("transactional"):
            installs.append(commands)
            raise module.BootstrapError("fixture transport stopped")
        return original(user, password, commands, **kwargs)

    configured.setattr(module, "psql", fail)
    with pytest.raises(module.BootstrapError):
        module.bootstrap()
    assert len(installs) == 1
    assert all(c[0] == "verdify" for c in calls)
    assert not capsys.readouterr().out


@pytest.mark.parametrize("answer", ["f", "", "NULL"])
def test_unknown_contract_cannot_reach_password_or_auth(configured, answer):
    calls = []
    configured.setattr(module, "psql", lambda *args, **kwargs: calls.append((args, kwargs)) or answer)
    with pytest.raises(module.BootstrapError):
        module.bootstrap()
    assert len(calls) == 1 and not calls[0][1]
    assert "\\password" not in str(calls)


@pytest.mark.parametrize("bad", ["", "x" * 63, "x" * 65, "'" * 64, "\\" * 64])
def test_bad_custody_has_no_database_effect(configured, bad):
    configured.setenv("WRITER_WATCHDOG_DB_PASSWORD", bad)
    calls = fake_psql(configured)
    with pytest.raises(module.BootstrapError):
        module.bootstrap()
    assert not calls


@pytest.mark.parametrize(
    "state",
    [
        {},
        {"verdify_writer_watchdog_runtime_login": None},
        {"verdify_writer_watchdog_runtime_login": 1, "verdify_matview_refresh_runtime_login": True},
    ],
)
def test_missing_null_or_nonboolean_metadata_never_grants_install(configured, state):
    calls = fake_psql(configured, nulls=state)
    with pytest.raises(module.BootstrapError):
        module.bootstrap()
    assert not any(c[2].get("transactional") for c in calls)


def test_exact_new_profile_retains_275_body_and_bounds_function_drift():
    ordinary = module._ordinary
    profile = ordinary.sealed_sql_276()
    assert profile.replace(
        ordinary.seal_profile_sql(276, ordinary.SUCCESSOR_275), ordinary.seal_profile_sql(275, ordinary.SUCCESSOR_275)
    ).startswith("(" + ordinary.sealed_sql_275())
    assert ordinary.MAINTENANCE_MIGRATION[2] == hashlib.sha256(MIGRATION.read_bytes()).hexdigest()
    assert "max(seq)" in profile and ")=276" in profile and ")=277" not in profile
    sql = MIGRATION.read_text()
    found = {}
    for m in re.finditer(r"CREATE FUNCTION ([\w.]+)\(([^)]*)\)(.*?)AS (\$\w+\$)(.*?)\4;", sql, re.S):
        name, args, attrs, _, body = m.groups()
        signature = name + "(" + ",".join(a.strip().split()[-1] for a in args.split(",") if a.strip()) + ")"
        found[signature] = (hashlib.sha256(body.encode()).hexdigest(), "SECURITY DEFINER" in attrs)
    assert found == module.FUNCTIONS and len(found) == 6
    for signature, (sha, _) in found.items():
        assert signature in module.contract_sql() and sha in module.contract_sql()
    assert "a.grantee NOT IN (p.proowner" in module.contract_sql()
    assert "count(*)=0 FROM pg_auth_members WHERE member=" in module.contract_sql()


def test_no_raw_or_cross_workload_grants_and_fixed_owner_objects():
    sql = MIGRATION.read_text()
    grants = re.findall(r"^\s*GRANT .*?;", sql, re.M)
    assert len(grants) == 10  # Two memberships, two schema USAGE, six EXECUTE.
    assert all(
        "PUBLIC" not in g and "verdify_ingestor_runtime" not in g and "verdify_api_runtime" not in g for g in grants
    )
    assert not any(re.search(r"GRANT (SELECT|INSERT|UPDATE|DELETE|TRUNCATE|CREATE|ALL)", g) for g in grants)
    assert "CREATE OR REPLACE" not in sql and "ALTER FUNCTION" not in sql
    assert "session_user <> 'verdify_writer_watchdog_runtime_login'" in sql
    assert sql.count("session_user <> 'verdify_matview_refresh_runtime_login'") == 3
    assert "source='writer_watchdog' AND resolved_at IS NULL" in sql
    assert "p_lease_state IS NULL" in sql and "p_lease_age_s<0" in sql
    assert "FROM public.climate" in sql and "p_climate" not in sql
    assert "EXECUTE " not in sql.replace("GRANT EXECUTE ", "")
    targets = re.findall(r"REFRESH MATERIALIZED VIEW(?: CONCURRENTLY)? public\.(\w+)", sql)
    assert sorted(targets) == sorted(["mv_band_curve", "mv_equipment_runtime_daily", "mv_daily_kpi"] * 2)
    assert "pg_temp.role643_maintenance_predecessors" in sql
    assert "276 changed an existing sealed runtime boundary" in sql


def test_only_two_callers_and_initial_hook_receive_new_custody():
    _, watch = watchdog()
    refresh = yaml.safe_load((ROOT / "deploy/k8s/components/grafana/band-curve-refresh-cronjob.yaml").read_text())
    refresh = refresh["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]
    for duty, container in [("writer_watchdog", watch), ("matview_refresh", refresh)]:
        env = {e["name"]: e for e in container["env"]}
        assert env["PGUSER"]["value"] == "verdify_" + duty + "_runtime_login"
        assert env["PGPASSWORD"]["valueFrom"]["secretKeyRef"] == {
            "name": "verdify-" + duty.replace("_", "-") + "-runtime-db",
            "key": "password",
        }
        command = container["command"][-1]
        assert "fn_attest_login()" in command and '[ "$attested" = t ]' in command
        assert (
            "INSERT INTO" not in command
            and "UPDATE alert_log" not in command
            and "REFRESH MATERIALIZED VIEW" not in command
        )
    hook = yaml.safe_load(
        (ROOT / "deploy/k8s/components/runtime-role-boundary/maintenance-bootstrap-job.yaml").read_text()
    )
    assert hook["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"] == "4"
    pod = hook["spec"]["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    assert pod["containers"][0]["command"] == ["python3", "/scripts/bootstrap-maintenance-runtime-roles.py"]
    refs = {
        e["name"]: e["valueFrom"]["secretKeyRef"]
        for e in pod["containers"][0]["env"]
        if "secretKeyRef" in e.get("valueFrom", {})
    }
    assert set(refs) == {"DB_ADMIN_PASSWORD", "WRITER_WATCHDOG_DB_PASSWORD", "MATVIEW_REFRESH_DB_PASSWORD"}


def maintenance_image_scripts(dockerfile):
    copied = {}
    for line in dockerfile.splitlines():
        if line.startswith("COPY "):
            *sources, destination = shlex.split(line)[1:]
            if destination == "/scripts/":
                copied.update({"/scripts/" + Path(source).name: source for source in sources})
    return copied


def test_maintenance_hook_and_sibling_import_are_actual_migrate_copy_inputs():
    hook = yaml.safe_load(
        (ROOT / "deploy/k8s/components/runtime-role-boundary/maintenance-bootstrap-job.yaml").read_text()
    )
    container = hook["spec"]["template"]["spec"]["containers"][0]
    assert container["image"].endswith("/verdify-migrate")
    images = yaml.safe_load((ROOT / ".agent-fleet/ci.yaml").read_text())["images"]
    image = next(i for i in images if i["name"] == "verdify-migrate")
    assert image["context"] == "."
    copied = maintenance_image_scripts((ROOT / image["dockerfile"]).read_text())
    required = {
        container["command"][1]: "scripts/bootstrap-maintenance-runtime-roles.py",
        "/scripts/bootstrap-six-runtime-roles.py": "scripts/bootstrap-six-runtime-roles.py",
    }
    for destination, source in required.items():
        assert copied[destination] == source and (ROOT / source).is_file()
    assert 'with_name("bootstrap-six-runtime-roles.py")' in (ROOT / required[container["command"][1]]).read_text()


@pytest.mark.parametrize("missing", ["bootstrap-maintenance-runtime-roles.py", "bootstrap-six-runtime-roles.py"])
def test_maintenance_packaging_detects_missing_entrypoint_or_sibling(missing):
    raw = (ROOT / "db/Dockerfile.migrate").read_text()
    without_required = raw.replace("scripts/" + missing + " ", "")
    assert "/scripts/" + missing not in maintenance_image_scripts(without_required)


@pytest.mark.parametrize("lease", ["ok 1');SELECT pg_sleep(99);--", "ok -1", "ok ", "ok 1\n2"])
def test_malicious_or_malformed_lease_never_reaches_psql(tmp_path, lease):
    _, container = watchdog()
    script = container["command"][-1]
    state = tmp_path / "lease"
    state.write_text(lease)
    script = script.replace("/shared/lease_state", str(state))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    called = tmp_path / "psql-called"
    for name, body in [("pg_isready", "exit 0"), ("psql", "touch " + shlex.quote(str(called)))]:
        p = bin_dir / name
        p.write_text("#!/bin/sh\n" + body + "\n")
        p.chmod(0o700)
    result = subprocess.run(
        ["/bin/sh", "-ceu", script], env={"PATH": str(bin_dir) + ":/usr/bin:/bin"}, capture_output=True, text=True
    )
    assert result.returncode != 0 and not called.exists()
    assert lease not in result.stdout + result.stderr


def test_valid_lease_uses_psql_stdin_quoting(tmp_path):
    _, container = watchdog()
    script = container["command"][-1]
    assert ":'lease_state'" in script and ":'lease_age'" in script
    assert '--set=lease_state="$LEASE_KIND"' in script
    assert "<<'SQL'" in script
    assert "${LEASE_AGE_S}" not in script.split("SELECT * FROM", 1)[1]
