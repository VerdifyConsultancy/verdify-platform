"""Combined C0/resource transition on native synthetic Timescale, not production."""

import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest
from test_c0_boundary_transition import DIGESTS, fixture_contract
from test_c0_migration_delivery import delivery
from test_c0_release_rehearsal import (
    assert_applied,
    attestation_probe,
    combined_baseline,
    install_actual_attestation_probe,
    replace_once,
    snapshot,
)
from test_shelly_source_intervals import NOW, insert_many, sample
from test_shelly_source_intervals import isolated_pg as isolated_pg
from test_shelly_source_intervals import predecessor as predecessor

ROOT = Path(__file__).resolve().parents[1]
RESOURCE_NAME = "248-shelly-source-interval-accounting.sql"
RESOURCE_VERSION = "c0-resource-boundary-transition-241-248-v1"
PATHS = [ROOT / "db/migrations" / name for name in (*delivery.transition.MIGRATIONS, RESOURCE_NAME)]


def state(query):
    return (
        snapshot(query),
        query("SELECT jsonb_agg(to_jsonb(r) ORDER BY login_name) FROM runtime_ordinary_login_attestation_receipts r"),
        query("SELECT jsonb_agg(to_jsonb(e) ORDER BY ts) FROM energy e"),
    )


@pytest.fixture
def cohort(predecessor, tmp_path):
    q = predecessor
    baseline = combined_baseline()
    # Compose existing synthetic climate fixtures with REAL migration-194
    # resource relations, not the old resource table stand-ins. No production
    # SQL body is rewritten and no migration is pre-stamped as applied.
    daily = re.search(r"CREATE TABLE public.daily_summary \(([^;]+)\);", baseline).group(0)
    existing = set(
        json.loads(
            q(
                "SELECT jsonb_agg(attname) FROM pg_attribute WHERE attrelid='public.daily_summary'::regclass AND attnum>0"
            )
        )
    )
    for declaration in daily.split("(", 1)[1].rsplit(")", 1)[0].split(", "):
        if declaration.split()[0] not in existing:
            q("ALTER TABLE public.daily_summary ADD COLUMN " + declaration)
    baseline = replace_once(baseline, daily, "")
    for role in (
        "verdify_api_runtime",
        "verdify_ingestor_runtime",
        "verdify_api_runtime_login",
        "verdify_ingestor_runtime_login",
    ):
        baseline = replace_once(baseline, f"CREATE ROLE {role};", "")
    for name in ("v_water_attribution_daily", "v_runtime_energy_daily"):
        declaration = re.search(rf"CREATE TABLE public\.{name} \([^;]+\);", baseline).group(0)
        baseline = replace_once(baseline, declaration, "")
        insertion = re.search(rf"INSERT INTO {name} VALUES [^;]+;", baseline).group(0)
        baseline = replace_once(baseline, insertion, "")
    climate = re.search(r"CREATE TABLE public.climate \([^;]+\);", baseline).group(0)
    baseline = replace_once(
        baseline,
        climate,
        """ALTER TABLE public.climate
        ADD COLUMN temp_avg float8, ADD COLUMN vpd_avg float8,
        ADD COLUMN house_temp_target_f float8 DEFAULT 75,
        ADD COLUMN house_vpd_target float8 DEFAULT 1;""",
    )
    shim = "CREATE FUNCTION time_bucket(interval, timestamptz) RETURNS timestamptz\nLANGUAGE sql IMMUTABLE AS $$ SELECT date_bin($1, $2, timestamptz '1970-01-01 UTC') $$;"
    baseline = replace_once(baseline, shim, "")
    q(baseline)
    q((ROOT / "db/ledger/schema_migrations.sql").read_text())
    q(
        f"SELECT stamp_migration('qualification/synthetic-combined-resource.sql','db/migrations',NULL,'{hashlib.sha256(baseline.encode()).hexdigest()}','manual')"
    )
    q("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    install_actual_attestation_probe(q)
    assert attestation_probe(q) == {"api": "t", "ingestor": "t"}
    contract = fixture_contract()
    contract["version"] = RESOURCE_VERSION
    contract["server_version_num"] = int(q("SHOW server_version_num"))
    contract["predecessor_ledger_sha256"] = q(delivery.transition.ledger_digest_sql())
    contract["before"] = json.loads(q(DIGESTS))
    before = state(q)
    contract["after"] = json.loads(
        q("BEGIN;\n" + "\n".join(path.read_text() for path in PATHS) + DIGESTS + "ROLLBACK;").splitlines()[-1]
    )
    assert state(q) == before
    assert all(contract["before"][login] != contract["after"][login] for login in contract["before"])
    directory = tmp_path / "cohort"
    directory.mkdir()
    for path in PATHS:
        shutil.copyfile(path, directory / path.name)
    contract_file = tmp_path / "synthetic-eight-file-contract.json"
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
        DB_HOST=q("SHOW unix_socket_directories"),
        DB_PORT="55472",
        DB_NAME="postgres",
        DB_USER="scorecard_fixture",
        DB_PASS="private-synthetic-fixture-only",  # noqa: S106 - private trust-auth fixture
        VERDIFY_MIGRATIONS_DIR=str(directory),
        VERDIFY_C0_BOUNDARY_CONTRACT=str(contract_file),
        VERDIFY_C0_BOUNDARY_CONTRACT_SHA256=hashlib.sha256(contract_file.read_bytes()).hexdigest(),
    )

    def run(*args, overrides=None):
        return subprocess.run(
            ["sh", str(ROOT / "db/apply-migrations.sh"), *args],
            env=dict(env, **(overrides or {})),
            text=True,
            capture_output=True,
            timeout=90,
        )

    return q, contract, contract_file, directory, env, run


def test_combined_sources_rehearse_without_receipt_refresh(cohort):
    q, contract, _, _, _, _ = cohort
    assert len(PATHS) == 8
    assert attestation_probe(q) == {"api": "t", "ingestor": "t"}
    assert q("SELECT count(*) FROM schema_migrations WHERE stamp_method='runner'") == "0"
    assert contract["version"] == RESOURCE_VERSION


def repin(contract, path, env):
    path.write_text(json.dumps(contract, sort_keys=True))
    env["VERDIFY_C0_BOUNDARY_CONTRACT_SHA256"] = hashlib.sha256(path.read_bytes()).hexdigest()


def test_eight_file_owning_commit_readback_and_no_write_retry(cohort):
    q, _, _, _, _, run = cohort
    result = run()
    assert result.returncode == 0, result.stderr
    assert "eight exact stamps and both successor receipts/catalogs" in result.stdout
    assert_applied(q, PATHS)
    assert attestation_probe(q) == {"api": "t", "ingestor": "t"}
    assert (
        q(
            "SELECT count(*) FROM v_resource_accounting_health WHERE resource='energy_partial_meter' AND available_for_scoring"
        )
        == "0"
    )
    insert_many(q, [sample(), sample(NOW + timedelta(seconds=300))], duty=True)
    assert (
        q(
            "SET ROLE verdify_api_runtime; SELECT measured_kwh FROM v_energy_estimate_reconciliation WHERE greenhouse_id='vallery' AND date='2026-08-14'"
        )
        == "0.083"
    )
    stable = state(q)
    repeated = run()
    assert repeated.returncode == 0, repeated.stderr
    assert state(q) == stable
    assert "per-file" not in repeated.stdout


@pytest.mark.parametrize(
    "failure", ["old-version", "pin", "missing-resource", "unknown-version", "outside-pending", "wrong-successor"]
)
def test_explicit_profile_and_exact_successor_refusals_preserve_state(cohort, failure):
    q, contract, path, directory, env, run = cohort
    if failure == "old-version":
        contract["version"] = delivery.transition.VERSION
        repin(contract, path, env)
    elif failure == "pin":
        env["VERDIFY_C0_BOUNDARY_CONTRACT_SHA256"] = "0" * 64
    elif failure == "missing-resource":
        (directory / RESOURCE_NAME).unlink()
    elif failure == "unknown-version":
        contract["version"] = "unreviewed-resource-v2"
        repin(contract, path, env)
    elif failure == "outside-pending":
        name = "240-experiment-v2-readiness-reader-grants.sql"
        shutil.copyfile(ROOT / "db/migrations" / name, directory / name)
    else:
        contract["after"]["verdify_api_runtime_login"] = "0" * 64
        repin(contract, path, env)
    before = state(q)
    result = run()
    assert result.returncode != 0 and "no per-file fallback" in result.stderr
    assert state(q) == before
    assert attestation_probe(q) == {"api": "t", "ingestor": "t"}
    if failure == "old-version":
        assert "pending migration outside the qualified C0 bundle" in result.stderr
    if failure == "missing-resource":
        assert "complete exact selected release inventory required" in result.stderr


def test_eight_file_plan_requires_explicit_profile_and_does_not_write(cohort):
    q, _, _, _, _, run = cohort
    before = state(q)
    result = run("--plan")
    assert result.returncode == 0, result.stderr
    assert "8 pending; atomic bundle=241-248; contract_supplied=True" in result.stdout
    assert "remain unverified" in result.stdout
    assert state(q) == before
    implicit = run("--plan", overrides={"VERDIFY_C0_BOUNDARY_CONTRACT": "", "VERDIFY_C0_BOUNDARY_CONTRACT_SHA256": ""})
    assert implicit.returncode != 0
    assert state(q) == before


def test_already_committed_seven_file_release_is_not_eight_file_predecessor(cohort):
    q, contract, _, _, env, run = cohort
    seven = dict(contract, version=delivery.transition.VERSION)
    seven["after"] = json.loads(
        q("BEGIN;\n" + "\n".join(path.read_text() for path in PATHS[:-1]) + DIGESTS + "ROLLBACK;").splitlines()[-1]
    )
    # Apply and stamp the actual seven sources through their reviewed-profile
    # emitter on this fixture. Never simulate this with fabricated ledger rows.
    pin = hashlib.sha256(json.dumps(seven, sort_keys=True).encode()).hexdigest()
    delivery.psql(delivery.transition.emit_sql(seven, pin), env)
    assert_applied(q, PATHS[:-1])
    assert attestation_probe(q) == {"api": "t", "ingestor": "t"}
    before = state(q)
    result = run()
    assert result.returncode != 0 and "partial C0 release must not resume per-file" in result.stderr
    assert state(q) == before


def test_committed_but_lost_readback_is_unverified_and_retry_is_no_write(cohort, monkeypatch):
    q, _, _, directory, env, run = cohort
    execute = delivery.psql
    committed = False

    def lose_readback(sql, environment):
        nonlocal committed
        if committed and sql == delivery.LEDGER_SQL:
            raise delivery.DeliveryError("synthetic committed readback loss")
        result = execute(sql, environment)
        if sql.startswith("-- " + RESOURCE_VERSION):
            committed = True
        return result

    monkeypatch.setattr(delivery, "psql", lose_readback)
    with pytest.raises(delivery.DeliveryError, match="committed readback loss"):
        delivery.deliver(directory, environment=env)
    assert committed
    assert_applied(q, PATHS)
    assert attestation_probe(q) == {"api": "t", "ingestor": "t"}
    before = state(q)
    result = run()
    assert result.returncode == 0, result.stderr
    assert state(q) == before


def test_profiles_are_separate_and_contract_cannot_supply_migration_sources():
    transition = delivery.transition
    assert len(transition.release_migrations(transition.VERSION)) == 7
    assert len(transition.release_migrations(RESOURCE_VERSION)) == 8
    assert RESOURCE_NAME not in transition.release_migrations(transition.VERSION)
    assert transition.checked_sources() == transition.checked_sources(transition.VERSION)
    contract = fixture_contract()
    contract["migrations"] = {RESOURCE_NAME: "untrusted"}
    with pytest.raises(ValueError, match="unexpected contract fields"):
        transition.validate(contract)


def test_seven_file_emission_bytes_match_pre_resource_parent():
    # Recorded by executing the actual parent c4fdf4318 emitter with this fixed
    # synthetic contract/pin. This binds the whole output, not selected strings.
    sql = delivery.transition.emit_sql(fixture_contract(), "4" * 64)
    assert (
        hashlib.sha256(sql.encode()).hexdigest() == "2f7fcede85a772814f1a219389f258ee75a0f8f92e2e5971a388e59a93f47625"
    )


def test_resource_emission_and_readback_both_check_supported_extension():
    resource = dict(fixture_contract(), version=RESOURCE_VERSION)
    sql = delivery.transition.emit_sql(resource, "4" * 64)
    assert sql.count("C0 resource transition refuses unsupported TimescaleDB version") == 2
    assert "C0 resource transition refuses unsupported TimescaleDB version" in delivery.successor_probe(resource)


def test_resource_extension_version_refuses_before_mutating_sql(cohort, monkeypatch):
    q, _, _, directory, env, _ = cohort
    before = state(q)
    observed = []

    def unsupported(sql, environment):
        observed.append(sql)
        assert sql == delivery.CORE_SQL
        return json.dumps({"timescale": True, "core": True, "timescale_version": "2.24.0"})

    # An injected read-only version observation, not a claim to execute another
    # Timescale binary. The acceptance cases use the real installed extension.
    monkeypatch.setattr(delivery, "psql", unsupported)
    with pytest.raises(delivery.DeliveryError, match="TimescaleDB 2.25.2"):
        delivery.deliver(directory, environment=env)
    assert observed == [delivery.CORE_SQL]
    assert state(q) == before
