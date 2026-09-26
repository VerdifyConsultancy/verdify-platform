"""Safety contract for the isolated, rollback-only C0 successor probe."""

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("c0_restore_probe", ROOT / "scripts/c0-restore-probe.py")
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def test_probe_has_exact_source_and_cannot_commit() -> None:
    sql = probe.emit_sql()
    assert sql.count("\n-- BEGIN EXACT SOURCE ") == 8
    assert sql.count("\nROLLBACK;\n") == 1
    assert not re.search(r"(?m)^COMMIT;\s*$", sql)
    assert "current_database() <> 'verdify_rehearsal'" in sql
    assert "seq BETWEEN 241 AND 248" in sql
    assert "INSERT INTO public.schema_migrations" not in sql
    assert "UPDATE public.runtime_ordinary_login_attestation_receipts" not in sql
    for stage in ("BEFORE", "AFTER"):
        for login in probe.boundary.LOGINS:
            assert sql.count(f"\\echo C0_PROBE_{stage}_{login}") == 1
    assert sql.rstrip().endswith("\\echo C0_PROBE_ROLLED_BACK")
