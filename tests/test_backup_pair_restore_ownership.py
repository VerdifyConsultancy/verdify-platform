"""The scheduled-pair restore carries and runs the blocking Timescale owner contract."""

from __future__ import annotations

import importlib.util
from pathlib import Path

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
