"""Contract for current-lease recovery-range startup rollover."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db/migrations/237-experiment-v2-direct-proof-recovery-range-rollover.sql"
PREVIOUS = ROOT / "db/migrations/236-experiment-v2-direct-proof-preclaim-raw-reset-rollover.sql"


def test_rollover_binds_fault_and_receipts_to_exact_recovery_range() -> None:
    sql = MIGRATION.read_text()
    for required in (
        "fault.recorded_at <@ recovery.valid_range",
        "recovered.recorded_at <@ recovery.valid_range",
        "recovery.created_at >= v_aggressive_created_at",
        "upper(recovery.valid_range) - lower(recovery.valid_range) =",
        "interval '5 minutes'",
        "recovery.expires_at = upper(recovery.valid_range)",
        "recovery.lease_generation = v_exp.lease_generation",
        "recovery.revision_bundle_sha256 = v_exp.revision_bundle_sha256",
    ):
        assert required in sql
    assert "fault.recorded_at <@ v_auth.proof_valid_range" not in sql
    assert "recovered.recorded_at <@ v_auth.proof_valid_range" not in sql


def test_rollover_retains_preclaim_and_zero_exposure_gates() -> None:
    sql = MIGRATION.read_text()
    assert "fault.recorded_at > v_aggressive_created_at" in sql
    assert "exposure.work_id = v_aggressive_work_id" in sql
    assert "closure.exposure_id IS NULL" in sql
    assert "recovery.parent_work_id IS NULL" in sql
    assert "count(DISTINCT receipt.receipt_id)::integer" in sql
    assert "verdify-direct-proof-startup-raw-reset-v3|" in sql


def test_recovery_range_resolution_is_append_only_and_not_proof() -> None:
    sql = MIGRATION.read_text()
    assert "INSERT INTO public.experiment_v2_direct_proof_attempt_events" in sql
    assert "INSERT INTO public.experiment_v2_direct_proof_emergency_recovery_receipts" in sql
    assert "INSERT INTO public.experiment_v2_direct_proof_receipts" not in sql
    assert "DELETE FROM" not in sql
    assert "TO verdify_experiment_lifecycle" in sql


def test_applied_migration_236_is_not_rewritten() -> None:
    sql = PREVIOUS.read_text()
    assert "fault.recorded_at <@ v_auth.proof_valid_range" in sql
    assert "recovered.recorded_at <@ v_auth.proof_valid_range" in sql
    assert "verdify-direct-proof-startup-raw-reset-v2|" in sql
