"""Source-contract gates for restart-safe direct-proof status migration 226."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db/migrations/226-experiment-v2-direct-proof-attempt-status.sql"
FIXTURE = ROOT / "db/migrations/tests/test-226-experiment-v2-direct-proof-attempt-status.sql"


def test_status_is_latest_attempt_only_and_treatment_free() -> None:
    sql = MIGRATION.read_text()
    assert "SECURITY DEFINER" in sql
    assert "ORDER BY authz.attempt_number DESC" in sql
    assert "LIMIT 1" in sql
    for field in (
        "aggressive_work_id",
        "baseline_after_work_id",
        "attempt_failed",
        "attempt_superseded",
        "resolution_kind",
        "recovery_valid_range",
        "emergency_recovery_complete",
        "proof_receipt_id",
    ):
        assert field in sql
    for forbidden in (
        "target_profile",
        "state_content_sha256",
        "observation_receipt_sha256",
        "outcome_value",
        "selector_choice",
    ):
        assert forbidden not in sql


def test_only_lifecycle_can_execute_status_surface() -> None:
    sql = MIGRATION.read_text()
    signature = "public.fn_experiment_v2_direct_proof_attempt_status(uuid)"
    assert f"ALTER FUNCTION {signature}" in sql
    assert "OWNER TO verdify_experiment_v2_owner" in sql
    assert "FROM PUBLIC CASCADE" in sql
    assert "TO verdify_experiment_lifecycle" in sql


def test_restore_fixture_is_read_only_and_calls_all_columns() -> None:
    fixture = FIXTURE.read_text()
    assert fixture.startswith("-- Read-only catalog/ACL fixture")
    assert "BEGIN;" in fixture and fixture.rstrip().endswith("ROLLBACK;")
    assert "INSERT " not in fixture
    assert "UPDATE " not in fixture
    assert "DELETE " not in fixture
    assert "PERFORM authorization_id, attempt_number" in fixture
