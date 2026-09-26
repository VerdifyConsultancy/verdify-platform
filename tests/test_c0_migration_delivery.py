"""Current owning-runner atomic C0 path; private PG16, not a restored deployment."""

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from test_c0_boundary_transition import DIGESTS, PATHS, fixture_contract, receipts
from test_c0_release_rehearsal import (
    assert_applied,
    attestation_probe,
    combined_baseline,
    install_actual_attestation_probe,
    snapshot,
)
from test_scorecard_semantics import isolated_pg as plain_isolated_pg

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "db/apply-migrations.sh"
SPEC = importlib.util.spec_from_file_location("c0_migration_delivery", ROOT / "scripts/c0-migration-delivery.py")
delivery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(delivery)


@pytest.fixture
def isolated_pg(tmp_path):
    # Actual Timescale is preloaded; no extension-presence mock or fake time_bucket.
    yield from plain_isolated_pg.__wrapped__(tmp_path, preload="timescaledb")


@pytest.fixture
def owning(isolated_pg, tmp_path):
    query = isolated_pg
    if int(query("SHOW server_version_num")) // 10000 != 16:
        pytest.skip("owning C0 delivery requires PostgreSQL 16")
    query("CREATE EXTENSION timescaledb")
    baseline = combined_baseline()
    shim = "CREATE FUNCTION time_bucket(interval, timestamptz) RETURNS timestamptz\nLANGUAGE sql IMMUTABLE AS $$ SELECT date_bin($1, $2, timestamptz '1970-01-01 UTC') $$;"
    assert baseline.count(shim) == 1
    # Use real native time_bucket before any baseline view is constructed.
    baseline = baseline.replace(shim, "")
    query(baseline)
    query((ROOT / "db/ledger/schema_migrations.sql").read_text())
    query(f"""SELECT stamp_migration('qualification/synthetic-timescale-c0-baseline.sql',
        'db/migrations',NULL,'{hashlib.sha256(baseline.encode()).hexdigest()}','manual')""")
    query("""
        CREATE TABLE public.equipment_state (fixture_only boolean);
        REVOKE CREATE ON SCHEMA public FROM PUBLIC;""")
    assert query("SELECT extversion FROM pg_extension WHERE extname='timescaledb'") == "2.25.2"
    install_actual_attestation_probe(query)
    contract = fixture_contract()
    contract["server_version_num"] = int(query("SHOW server_version_num"))
    contract["predecessor_ledger_sha256"] = query(delivery.transition.ledger_digest_sql())
    contract["before"] = json.loads(query(DIGESTS))
    before = snapshot(query)
    rehearsal_sql = "BEGIN;\n" + "\n".join(path.read_text() for path in PATHS) + DIGESTS + "ROLLBACK;"
    contract["after"] = json.loads(query(rehearsal_sql).splitlines()[-1])
    assert snapshot(query) == before
    directory = tmp_path / "qualified-migrations"
    directory.mkdir()
    for path in PATHS:
        shutil.copyfile(path, directory / path.name)
    contract_file = tmp_path / "synthetic-contract.json"
    contract_file.write_text(json.dumps(contract, sort_keys=True))
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(
            ("PG", "DB_", "POSTGRES_", "VERDIFY_C0", "VERDIFY_MIGR", "VERDIFY_LEDGER", "VERDIFY_SAFETY")
        )
    }
    env.update(
        PATH=f"{os.environ['SCORECARD_TEST_PG_BIN']}:{env['PATH']}",
        DB_HOST=query("SHOW unix_socket_directories"),
        DB_PORT="55472",
        DB_NAME="postgres",
        DB_USER="scorecard_fixture",
        DB_PASS="private-synthetic-fixture-only",  # noqa: S106 - private trust-auth fixture, not a credential
        VERDIFY_MIGRATIONS_DIR=str(directory),
        VERDIFY_LEDGER_DIR=str(ROOT / "db/ledger"),
        VERDIFY_C0_BOUNDARY_CONTRACT=str(contract_file),
        VERDIFY_C0_BOUNDARY_CONTRACT_SHA256=hashlib.sha256(contract_file.read_bytes()).hexdigest(),
    )

    def run(*args, overrides=None):
        actual = dict(env)
        actual.update(overrides or {})
        return subprocess.run(["sh", str(RUNNER), *args], env=actual, capture_output=True, text=True, timeout=60)

    return query, directory, contract_file, env, run


def test_current_runner_applies_one_atomic_bundle_reads_back_and_retries(owning):
    query, _, _, _, run = owning
    result = run()
    assert result.returncode == 0, result.stderr
    assert "C0 committed state verified" in result.stdout
    assert "bootstrapping" not in result.stdout and "applying 241" not in result.stdout
    assert_applied(query, PATHS)
    assert attestation_probe(query) == {"api": "t", "ingestor": "t"}
    stable = snapshot(query), receipts(query)
    repeated = run()
    assert repeated.returncode == 0, repeated.stderr
    assert (snapshot(query), receipts(query)) == stable


@pytest.mark.parametrize("missing", ["VERDIFY_C0_BOUNDARY_CONTRACT", "VERDIFY_C0_BOUNDARY_CONTRACT_SHA256"])
def test_missing_contract_or_pin_refuses_before_bootstrap(owning, missing):
    query, _, _, _, run = owning
    before = snapshot(query), receipts(query)
    result = run(overrides={missing: ""})
    assert result.returncode != 0 and "reviewed contract and independent hash pin" in result.stderr
    assert (snapshot(query), receipts(query)) == before


def test_plan_without_contract_is_read_only_and_not_a_qualification(owning):
    query, _, _, _, run = owning
    before = snapshot(query), receipts(query)
    result = run("--plan", overrides={"VERDIFY_C0_BOUNDARY_CONTRACT": "", "VERDIFY_C0_BOUNDARY_CONTRACT_SHA256": ""})
    assert result.returncode == 0, result.stderr
    assert "7 pending" in result.stdout and "contract_supplied=False" in result.stdout
    assert "remain unverified" in result.stdout
    assert (snapshot(query), receipts(query)) == before


@pytest.mark.parametrize("missing", ["timescale", "equipment_state"])
def test_missing_existing_core_or_extension_refuses_without_repair(owning, missing):
    query, _, _, _, run = owning
    # Remove only the two named synthetic baseline dependents before removing
    # the actual extension; never use CASCADE or alter extension catalog rows.
    query(
        "DROP VIEW v_forecast_accuracy, v_forecast_accuracy_lead_buckets RESTRICT; DROP EXTENSION timescaledb RESTRICT"
        if missing == "timescale"
        else "DROP TABLE equipment_state RESTRICT"
    )
    before = snapshot(query), receipts(query)
    result = run()
    assert result.returncode != 0 and "Timescale extension and existing core schema required" in result.stderr
    assert (snapshot(query), receipts(query)) == before


@pytest.mark.parametrize("failure", ["pin", "partial-inventory", "source-drift", "pending-other"])
def test_contract_inventory_and_other_pending_changes_cannot_fall_back(owning, failure):
    query, directory, _, _, run = owning
    overrides = {}
    if failure == "pin":
        overrides["VERDIFY_C0_BOUNDARY_CONTRACT_SHA256"] = "0" * 64
    elif failure == "partial-inventory":
        (directory / PATHS[-1].name).unlink()
    elif failure == "source-drift":
        with (directory / PATHS[0].name).open("a") as stream:
            stream.write("\n-- synthetic changed source\n")
    else:
        path = ROOT / "db/migrations/240-experiment-v2-readiness-reader-grants.sql"
        shutil.copyfile(path, directory / path.name)
    before = snapshot(query), receipts(query)
    result = run(overrides=overrides)
    assert result.returncode != 0 and "no per-file fallback" in result.stderr
    assert (snapshot(query), receipts(query)) == before


def test_sql_failure_rolls_back_and_owning_runner_resumes(owning):
    query, _, contract_file, env, run = owning
    original = contract_file.read_bytes()
    changed = json.loads(original)
    changed["after"]["verdify_ingestor_runtime_login"] = "0" * 64
    contract_file.write_text(json.dumps(changed))
    bad_pin = hashlib.sha256(contract_file.read_bytes()).hexdigest()
    before = snapshot(query), receipts(query)
    failed = run(overrides={"VERDIFY_C0_BOUNDARY_CONTRACT_SHA256": bad_pin})
    assert failed.returncode != 0 and "SQLSTATE P0001" in failed.stderr
    assert "CONTEXT:" not in failed.stderr
    assert (snapshot(query), receipts(query)) == before
    contract_file.write_bytes(original)
    assert env["VERDIFY_C0_BOUNDARY_CONTRACT_SHA256"] == hashlib.sha256(original).hexdigest()
    resumed = run()
    assert resumed.returncode == 0, resumed.stderr
    assert_applied(query, PATHS)
    assert attestation_probe(query) == {"api": "t", "ingestor": "t"}


def test_readback_failure_after_commit_never_triggers_per_file_retry(owning, monkeypatch):
    query, directory, _, env, run = owning
    original = delivery.psql
    count = 0

    def fail_readback(sql, bindings):
        nonlocal count
        count += 1
        if count == 4:
            raise delivery.DeliveryError("synthetic lost readback")
        return original(sql, bindings)

    monkeypatch.setattr(delivery, "psql", fail_readback)
    with pytest.raises(delivery.DeliveryError, match="lost readback"):
        delivery.deliver(directory, environment=env)
    assert_applied(query, PATHS)
    committed = snapshot(query), receipts(query)
    result = run()
    assert result.returncode == 0, result.stderr
    assert (snapshot(query), receipts(query)) == committed


def test_post_commit_predecessor_ledger_drift_is_reported_unverified(owning, monkeypatch):
    query, directory, _, env, _ = owning
    original = delivery.psql
    count = 0

    def drift_after_commit(sql, bindings):
        nonlocal count
        count += 1
        if count == 4:
            query("UPDATE schema_migrations SET sha256=repeat('0',64) WHERE seq IS NULL")
        return original(sql, bindings)

    monkeypatch.setattr(delivery, "psql", drift_after_commit)
    with pytest.raises(delivery.DeliveryError, match="SQLSTATE P0001"):
        delivery.deliver(directory, environment=env)
    assert_applied(query, PATHS)
    assert query("SELECT sha256 FROM schema_migrations WHERE seq IS NULL") == "0" * 64


@pytest.mark.parametrize("kind", ["api", "ingestor"])
def test_ordinary_login_cannot_use_owning_runner(owning, kind):
    query, _, _, _, run = owning
    before = snapshot(query), receipts(query)
    result = run(overrides={"DB_USER": f"verdify_{kind}_runtime_login"})
    assert result.returncode != 0 and "SQLSTATE 42501" in result.stderr
    assert (snapshot(query), receipts(query)) == before


def test_image_layout_has_all_imports_and_source_for_delivery(owning, tmp_path):
    query, directory, _, env, _ = owning
    image = tmp_path / "image-layout"
    (image / "scripts").mkdir(parents=True)
    shutil.copytree(ROOT / "db/migrations", image / "db/migrations")
    for name in ("c0-migration-delivery.py", "c0-boundary-transition.py", "ordinary-boundary-diff.py"):
        shutil.copyfile(ROOT / "scripts" / name, image / "scripts" / name)
    result = subprocess.run(
        [sys.executable, str(image / "scripts/c0-migration-delivery.py"), "--migrations-dir", str(directory)],
        cwd=image,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert_applied(query, PATHS)
    assert attestation_probe(query) == {"api": "t", "ingestor": "t"}


def test_entrypoint_image_and_early_dispatch_source_contract():
    runner = RUNNER.read_text()
    assert runner.index('exec python3 "$C0_DELIVERY"') < runner.index('q "SELECT 1"')
    assert "VERDIFY_C0_BYPASS" not in runner
    dockerfile = (ROOT / "db/Dockerfile.migrate").read_text()
    assert (
        "COPY scripts/c0-migration-delivery.py scripts/c0-boundary-transition.py scripts/ordinary-boundary-diff.py /scripts/"
        in dockerfile
    )
    assert 'ENTRYPOINT ["/usr/local/bin/migrate.sh"]' in dockerfile
    entrypoint = (ROOT / "db/migrate.sh").read_text()
    assert entrypoint.index("exec /usr/local/bin/apply-migrations.sh") < entrypoint.index("have_core=$")
    assert 'if [ "${VERDIFY_MIGRATE_LEDGER:-0}" = "1" ]; then' in entrypoint


def test_entrypoint_dispatches_before_core_or_fresh_schema_writes(owning, tmp_path):
    query, _, _, env, _ = owning
    source = (ROOT / "db/migrate.sh").read_text()
    # Relocate only the image's absolute executable path for the private host
    # fixture. No branch/query/argument is changed, and the real runner executes.
    assert source.count("/usr/local/bin/apply-migrations.sh") == 2
    script = tmp_path / "relocated-entrypoint.sh"
    script.write_text(source.replace("/usr/local/bin/apply-migrations.sh", str(RUNNER)))
    actual = dict(env, VERDIFY_MIGRATE_LEDGER="1")
    result = subprocess.run(["sh", str(script)], env=actual, text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr
    # The wrapper verifies the actual extension/core; legacy schema replay and
    # bootstrap still must not execute or mutate the qualified predecessor.
    assert "C0 committed state verified" in result.stdout
    assert "verify-not-rebuild" not in result.stdout and "replaying" not in result.stdout
    assert_applied(query, PATHS)


def test_missing_contract_on_fresh_target_cannot_replay_schema(owning, tmp_path):
    _, _, _, env, _ = owning
    source = (ROOT / "db/migrate.sh").read_text()
    script = tmp_path / "relocated-entrypoint.sh"
    script.write_text(source.replace("/usr/local/bin/apply-migrations.sh", str(RUNNER)))
    # Even an unreachable/fresh target is not contacted without the contract.
    actual = dict(
        env, VERDIFY_MIGRATE_LEDGER="1", VERDIFY_C0_BOUNDARY_CONTRACT="", DB_HOST="/nonexistent-private-fixture-socket"
    )
    result = subprocess.run(["sh", str(script)], env=actual, text=True, capture_output=True, timeout=60)
    assert result.returncode != 0 and "reviewed contract and independent hash pin" in result.stderr
    assert "replaying" not in result.stdout and "could not connect" not in result.stderr


def test_server_error_values_never_reach_job_log(monkeypatch, capsys, tmp_path):
    def secret_failure(*args, **kwargs):
        raise subprocess.TimeoutExpired(["psql", "sensitive-argument-canary"], 1200, output="sensitive-output-canary")

    monkeypatch.setattr(delivery, "deliver", secret_failure)
    assert delivery.main(["--migrations-dir", str(tmp_path)]) == 2
    assert "sensitive-" not in capsys.readouterr().err
    monkeypatch.setattr(
        delivery.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(
            a, 1, "sensitive-output-canary", "ERROR:  23514: sensitive-row-canary\nCONTEXT: sensitive-context-canary"
        ),
    )
    with pytest.raises(delivery.DeliveryError) as failure:
        delivery.psql("SELECT 1", dict.fromkeys(("DB_HOST", "DB_NAME", "DB_USER", "DB_PASS"), "synthetic"))
    assert str(failure.value) == "database command refused (SQLSTATE 23514)"


def test_missing_contract_refuses_without_a_database_process(monkeypatch, tmp_path):
    monkeypatch.setattr(delivery, "inventory", lambda _: {})
    monkeypatch.setattr(delivery, "psql", lambda *args: pytest.fail("database was contacted"))
    with pytest.raises(delivery.DeliveryError, match="reviewed contract"):
        delivery.deliver(tmp_path, environment={})
