"""Regression contract for the function-only observation window join."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "db" / "migrations"
FUNCTION = "CREATE OR REPLACE FUNCTION public.fn_experiment_v2_read_observation_window("


def _body(path: Path) -> str:
    sql = path.read_text()
    start = sql.index(FUNCTION)
    return sql[start : sql.index("$body$;", start)]


def test_observation_window_uses_unambiguous_bundle_identity() -> None:
    body = _body(MIGRATIONS / "230-experiment-v2-observation-window-bundle-join.sql")
    assert "JOIN public.experiment_v2_observation_receipts r USING" not in body
    assert "delivery_bundle_completions completion USING (bundle_id)" not in body
    assert "ON r.source_epoch_id = e.source_epoch_id" in body
    assert "AND r.work_id = e.work_id" in body
    assert "AND r.bundle_id = e.bundle_id" in body
    assert "ON completion.bundle_id = e.bundle_id" in body


def test_applied_migration_214_remains_byte_exact() -> None:
    import hashlib

    assert (
        hashlib.sha256((MIGRATIONS / "214-confirmed-component-experiment-v2.sql").read_bytes()).hexdigest()
        == "ac155aa5d6c02218e755e4ca7386e4477cf4b5791d9e1efef49c0dc427c12bda"
    )


def test_forward_migration_preserves_function_only_executor_grant() -> None:
    sql = (MIGRATIONS / "230-experiment-v2-observation-window-bundle-join.sql").read_text()
    assert "OWNER TO verdify_experiment_v2_owner" in sql
    assert "FROM PUBLIC CASCADE" in sql
    assert "TO verdify_experiment_component_executor" in sql
