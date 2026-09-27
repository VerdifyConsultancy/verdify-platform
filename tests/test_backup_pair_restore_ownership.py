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
    assert "_timescaledb_catalog.chunk" in scripts["check-timescale-ownership.sql"]
    assert "compressed_hypertable_id" in scripts["check-timescale-ownership.sql"]
    assert "REASSIGN OWNED BY test_672_rogue" in scripts["test-timescale-parent-owner.sql"]
    assert "ALTER TABLE _timescaledb_internal" not in scripts["test-timescale-parent-owner.sql"]

    container = job["spec"]["template"]["spec"]["containers"][0]
    env = {row["name"]: row["value"] for row in container["env"]}
    assert env["OWNERSHIP_SQL"] == "/scripts/check-timescale-ownership.sql"
    assert env["OWNER_REPAIR_TEST_SQL"] == "/scripts/test-timescale-parent-owner.sql"
    restore = scripts["restore-backup-pair.sh"]
    assert restore.index("timescaledb_post_restore()") < restore.index('-f "${OWNERSHIP_SQL}"')
    assert restore.index('-f "${OWNERSHIP_SQL}"') < restore.index('-f "${OWNER_REPAIR_TEST_SQL}"')
    assert restore.index('-f "${OWNER_REPAIR_TEST_SQL}"') < restore.index('-f "${AUDIT_SQL}"')
    assert policy["spec"]["policyTypes"] == ["Ingress", "Egress"]
    assert job["spec"]["template"]["spec"]["automountServiceAccountToken"] is False
