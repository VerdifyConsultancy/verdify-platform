"""Source-contract gates for migration 229's terminal-failure boundary."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db/migrations/229-experiment-v2-recovery-failure-boundary.sql"
FIXTURE = ROOT / "db/migrations/tests/test-229-experiment-v2-recovery-failure-boundary.sql"


def test_guard_uses_the_predecessor_recovery_terminal_failure() -> None:
    sql = MIGRATION.read_text()
    for required in (
        "JOIN public.experiment_v2_work_events failed_work",
        "failed_work.work_id = predecessor.recovery_work_id",
        "failed_work.event_kind = 'failed'",
        "max(failed_work.recorded_at)",
        "v_generation.recorded_at <= v_predecessor_failed_at",
        "v_generation.recorded_at > v_now - interval '4 minutes'",
        "fault.recorded_at > v_now - interval '2 minutes'",
    ):
        assert required in sql
    assert "predecessor.recorded_at INTO" not in sql


def test_missing_terminal_failure_is_rejected_separately() -> None:
    sql = MIGRATION.read_text()
    assert "IF v_predecessor_failed_at IS NULL" in sql
    assert "requires its predecessor terminal failure" in sql
    assert "current writer generation is not yet stable" in sql


def test_replacement_stays_owner_bound_and_fixture_checks_definition() -> None:
    sql = MIGRATION.read_text()
    assert "CREATE OR REPLACE FUNCTION" in sql
    assert "SECURITY DEFINER" in sql
    assert "SET search_path = public, pg_temp" in sql
    assert "OWNER TO verdify_experiment_v2_owner" in sql
    assert "FROM PUBLIC CASCADE" in sql
    fixture = FIXTURE.read_text()
    assert fixture.startswith("-- Read-only catalog/ACL fixture")
    assert "pg_get_functiondef" in fixture
    assert "BEGIN;" in fixture and fixture.rstrip().endswith("ROLLBACK;")
    for mutation in ("INSERT ", "UPDATE ", "DELETE "):
        assert mutation not in fixture
