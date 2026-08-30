"""Least-privilege contract for migration 240's readiness read surface."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db/migrations/240-experiment-v2-readiness-reader-grants.sql"


def _sql() -> str:
    return MIGRATION.read_text()


def test_readiness_grants_are_exactly_the_collector_reads() -> None:
    sql = re.sub(r"--.*$", "", _sql(), flags=re.MULTILINE)
    grants = re.findall(r"GRANT\s+(.+?)\s+TO\s+verdify_ingestor_runtime\s*;", sql, re.DOTALL | re.IGNORECASE)
    normalized = {" ".join(grant.split()) for grant in grants}
    assert normalized == {
        "EXECUTE ON FUNCTION public.fn_experiment_v2_api_status(uuid)",
        "EXECUTE ON FUNCTION public.fn_experiment_v2_executor_runtime(uuid, text)",
        "SELECT ON TABLE public.experiment_v2_runtime_generations",
        "SELECT ON TABLE public.v_open_alerts",
    }


def test_readiness_grants_add_no_role_or_write_capability() -> None:
    sql = _sql().upper()
    assert "CREATE ROLE" not in sql
    assert "ALTER ROLE" not in sql
    assert "SECURITY DEFINER" not in sql
    for capability in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
        assert f"GRANT {capability}" not in sql
    assert "ACQUIRED A WRITE PRIVILEGE" in sql


def test_readiness_grants_attest_the_inherited_login_surface() -> None:
    sql = _sql()
    assert sql.count("verdify_ingestor_runtime_login") == 6
    assert sql.count("has_function_privilege(") == 2
    assert sql.count("has_table_privilege(") == 4
