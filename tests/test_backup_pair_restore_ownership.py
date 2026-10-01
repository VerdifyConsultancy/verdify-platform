"""The scheduled-pair restore carries and runs the blocking Timescale owner contract."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytest_plugins = ["test_cnpg_target_runtime_transition"]

ROOT = Path(__file__).resolve().parents[1]


def test_rendered_restore_uses_source_bound_owner_checks():
    module_path = ROOT / "scripts/render-backup-pair-restore-job.py"
    spec = importlib.util.spec_from_file_location("backup_pair_restore", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    configmap, policy, job = module.render("verdify-20260927T081703Z", "owner-check")
    scripts = configmap["data"]
    assert "check-timescale-ownership.sql" in scripts
    assert "test-timescale-parent-owner.sql" in scripts
    assert "test-restored-timescale-parent-owner.sql" in scripts
    assert "_timescaledb_catalog.chunk" in scripts["check-timescale-ownership.sql"]
    assert "compressed_hypertable_id" in scripts["check-timescale-ownership.sql"]
    assert "REASSIGN OWNED BY test_672_rogue" in scripts["test-timescale-parent-owner.sql"]
    assert "ALTER TABLE _timescaledb_internal" not in scripts["test-timescale-parent-owner.sql"]
    real_fixture = scripts["test-restored-timescale-parent-owner.sql"]
    assert "runtime_ordinary_login_attestation_receipts" in real_fixture
    assert "REASSIGN OWNED BY test_672_restored_rogue" in real_fixture
    assert "217-runtime-role-boundary.sql" in real_fixture
    assert "\\ir ../217-runtime-role-boundary.sql" not in real_fixture
    assert "ALTER TABLE _timescaledb_internal" not in real_fixture

    container = job["spec"]["template"]["spec"]["containers"][0]
    env = {row["name"]: row["value"] for row in container["env"]}
    assert env["OWNERSHIP_SQL"] == "/scripts/check-timescale-ownership.sql"
    assert env["OWNER_REPAIR_TEST_SQL"] == "/scripts/test-timescale-parent-owner.sql"
    assert env["RESTORED_OWNER_TEST_SQL"] == "/scripts/test-restored-timescale-parent-owner.sql"
    restore = scripts["restore-backup-pair.sh"]
    assert restore.index("timescaledb_post_restore()") < restore.index('-f "${OWNERSHIP_SQL}"')
    assert restore.index('-f "${OWNERSHIP_SQL}"') < restore.index('-f "${OWNER_REPAIR_TEST_SQL}"')
    assert restore.index('-f "${OWNER_REPAIR_TEST_SQL}"') < restore.index('-f "${AUDIT_SQL}"')
    assert restore.index('-f "${OWNER_REPAIR_TEST_SQL}"') < restore.index('-f "${RESTORED_OWNER_TEST_SQL}"')
    assert restore.index('-f "${RESTORED_OWNER_TEST_SQL}"') < restore.index('-f "${AUDIT_SQL}"')
    assert policy["spec"]["policyTypes"] == ["Ingress", "Egress"]
    assert job["spec"]["template"]["spec"]["automountServiceAccountToken"] is False


def test_optional_v2_interface_audit_uses_same_denied_restore_and_blocks_on_drift():
    module_path = ROOT / "scripts/render-backup-pair-restore-job.py"
    spec = importlib.util.spec_from_file_location("backup_pair_restore_v2", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    _, _, ordinary = module.render("verdify-20260927T081703Z", "ordinary")
    configmap, policy, audited = module.render("verdify-20260927T081703Z", "v2-interface", v2_interface_audit=True)
    ordinary_env = {row["name"] for row in ordinary["spec"]["template"]["spec"]["containers"][0]["env"]}
    audit_env = {row["name"]: row["value"] for row in audited["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert "V2_INTERFACE_SQL" not in ordinary_env
    assert audit_env["V2_INTERFACE_SQL"] == "/scripts/qualify-v2-restored-interface.sql"
    assert "qualify-v2-restored-interface.sql" in configmap["data"]
    assert "BEGIN TRANSACTION READ ONLY" in configmap["data"]["qualify-v2-restored-interface.sql"]
    restore = configmap["data"]["restore-backup-pair.sh"]
    assert restore.index('-f "${AUDIT_SQL}"') < restore.index('first_interface="$(psql')
    assert "first_interface}" in restore and "second_interface}" in restore
    assert policy["spec"]["policyTypes"] == ["Ingress", "Egress"]
    assert audited["spec"]["template"]["spec"]["automountServiceAccountToken"] is False


@pytest.mark.parametrize("database_owner", ["verdify", 'fixture owner"quoted'])
def test_native_fixture_repairs_to_recorded_database_owner_not_executor(private_pg, database_owner):
    q = private_pg
    if database_owner != "verdify":
        quoted = '"' + database_owner.replace('"', '""') + '"'
        q(f"CREATE ROLE {quoted}; ALTER DATABASE verdify_rehearsal OWNER TO {quoted}", user="c5_fixture")
    source = (ROOT / "scripts/test-timescale-parent-owner.sql").read_text()
    repair = (
        "DO $repair_owner$" + source.split("DO $repair_owner$", 1)[1].split("$repair_owner$;", 1)[0] + "$repair_owner$;"
    )
    sql = (
        """
BEGIN;
CREATE ROLE test_672_rogue NOLOGIN;
CREATE ROLE test_672_reader NOLOGIN;
CREATE TABLE public.test_672_owner_contract(value integer);
CREATE TABLE public.unrelated_owner_canary(value integer);
ALTER TABLE public.unrelated_owner_canary OWNER TO verdify;
ALTER TABLE public.test_672_owner_contract OWNER TO test_672_rogue;
GRANT SELECT ON public.test_672_owner_contract TO test_672_reader;
REASSIGN OWNED BY test_672_rogue TO CURRENT_USER;
SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid='public.test_672_owner_contract'::regclass;
ALTER TABLE public.test_672_owner_contract OWNER TO test_672_rogue;
"""
        + repair
        + """
SELECT relowner=(SELECT datdba FROM pg_database WHERE datname=current_database()),
       has_table_privilege('test_672_reader','public.test_672_owner_contract','SELECT')
  FROM pg_class WHERE oid='public.test_672_owner_contract'::regclass;
SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid='public.unrelated_owner_canary'::regclass;
ROLLBACK;
"""
    )
    assert q(sql, user="c5_fixture").splitlines() == ["c5_fixture", "t|t", "verdify"]
    assert q("SELECT to_regclass('public.test_672_owner_contract') IS NULL") == "t"
    assert q("SELECT count(*) FROM pg_roles WHERE rolname LIKE 'test_672_%'") == "0"
