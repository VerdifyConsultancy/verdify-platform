"""The scheduled-pair restore carries and runs the blocking Timescale owner contract."""

from __future__ import annotations

import importlib.util
import os
import pwd
import re
import subprocess
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


def test_native_role_inventory_uses_transport_login_when_owner_is_peer_denied(private_pg):
    q = private_pg
    pg = Path(os.environ["CNPG_TEST_PG_BIN"])
    hba = Path(q("SHOW hba_file"))
    ident = Path(q("SHOW ident_file"))
    socket = q("SHOW unix_socket_directories")
    port = q("SHOW port")
    local_user = pwd.getpwuid(os.getuid()).pw_name
    # Only this disposable local fixture is modified. Real CNPG peer rules,
    # target passwords and product roles are untouched.
    ident.write_text(f"fixture_bootstrap {local_user} c5_fixture\n")
    hba.write_text("local all all peer map=fixture_bootstrap\n")
    assert q("SELECT pg_reload_conf()") == "t"
    source = (ROOT / "scripts/restore-backup-pair.sh").read_text()
    export = re.search(
        r'pg_dumpall --roles-only --no-role-passwords --no-comments --no-security-labels\s+-h "\$\{PGHOST\}" -p "\$\{PGPORT\}" -U "[^"\n]+" -l postgres',
        source.replace(chr(92) + chr(10), " "),
    )
    assert export is not None
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PG", "DB_", "POSTGRES_"))}
    env.update(
        PATH=str(pg) + os.pathsep + env["PATH"], PGHOST=socket, PGPORT=port, PGUSER="c5_fixture", owner="verdify"
    )
    denied_login = q("SELECT 1", user="verdify", database="postgres", check=False)
    assert denied_login.returncode != 0 and 'Peer authentication failed for user "verdify"' in denied_login.stderr
    denied = subprocess.run(
        ["/bin/bash", "-c", export[0].replace("${PGUSER}", "${owner}")],
        text=True,
        capture_output=True,
        env=env,
        timeout=30,
    )
    assert denied.returncode != 0 and "could not connect to database" in denied.stderr
    result = subprocess.run(["/bin/bash", "-c", export[0]], text=True, capture_output=True, env=env, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "CREATE ROLE verdify;" in result.stdout
    assert "PASSWORD" not in result.stdout
