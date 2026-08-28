"""Source-contract gates for migration 228's current-generation boundary."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db/migrations/228-experiment-v2-recovery-generation-guard.sql"
FIXTURE = ROOT / "db/migrations/tests/test-228-experiment-v2-recovery-generation-guard.sql"


def test_successor_requires_a_newer_stable_generation_at_insert_boundary() -> None:
    sql = MIGRATION.read_text()
    for required in (
        "BEFORE INSERT",
        "NEW.recovery_attempt_number - 1",
        "v_generation.recorded_at <= v_predecessor_recorded_at",
        "v_generation.recorded_at > v_now - interval '4 minutes'",
        "fault.recorded_at > v_now - interval '2 minutes'",
        "current writer generation is not yet stable",
    ):
        assert required in sql
    assert sql.index("SELECT predecessor.recorded_at") < sql.index("SELECT generation.*")


def test_first_recovery_and_nonrecovery_resolutions_are_unchanged() -> None:
    sql = MIGRATION.read_text()
    assert "NEW.resolution_kind <> 'bounded_baseline_recovery'" in sql
    assert "NEW.recovery_attempt_number <= 1" in sql
    assert "RETURN NEW;" in sql


def test_guard_is_owner_bound_public_revoked_and_fixture_is_read_only() -> None:
    sql = MIGRATION.read_text()
    assert "SECURITY DEFINER" in sql
    assert "SET search_path = public, pg_temp" in sql
    assert "OWNER TO verdify_experiment_v2_owner" in sql
    assert "FROM PUBLIC CASCADE" in sql
    fixture = FIXTURE.read_text()
    assert fixture.startswith("-- Read-only catalog/ACL fixture")
    assert "BEGIN;" in fixture and fixture.rstrip().endswith("ROLLBACK;")
    for mutation in ("INSERT ", "UPDATE ", "DELETE "):
        assert mutation not in fixture
