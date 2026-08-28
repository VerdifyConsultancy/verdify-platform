"""Cross-layer contract for the true pre-claim raw-reset proof rollover."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db/migrations/236-experiment-v2-direct-proof-preclaim-raw-reset-rollover.sql"
PREVIOUS = ROOT / "db/migrations/235-experiment-v2-direct-proof-raw-reset-rollover.sql"


def test_preclaim_reset_orders_from_immutable_aggressive_work_creation() -> None:
    sql = MIGRATION.read_text()
    assert "SELECT aggressive.created_at INTO v_aggressive_created_at" in sql
    assert "aggressive.work_id = v_aggressive_work_id" in sql
    assert "fault.recorded_at > v_aggressive_created_at" in sql
    assert "v_aggressive_created_at < v_fault_at AND v_fault_at < v_recovered_at" in sql
    assert "event.detail ->> 'v2_admission' = 'open'" not in sql


def test_preclaim_reset_requires_no_aggressive_exposure_and_no_open_exposure() -> None:
    sql = MIGRATION.read_text()
    assert "exposure.work_id = v_aggressive_work_id" in sql
    assert "closure.exposure_id IS NULL" in sql
    assert "recovery.parent_work_id IS NULL" in sql
    assert "count(DISTINCT receipt.receipt_id)::integer" in sql
    assert "verdify-direct-proof-startup-raw-reset-v2|" in sql


def test_preclaim_resolution_remains_append_only_and_never_credits_proof() -> None:
    sql = MIGRATION.read_text()
    assert "INSERT INTO public.experiment_v2_work_events" in sql
    assert "INSERT INTO public.experiment_v2_direct_proof_attempt_events" in sql
    assert "INSERT INTO public.experiment_v2_direct_proof_emergency_recovery_receipts" in sql
    assert "INSERT INTO public.experiment_v2_direct_proof_receipts" not in sql
    assert "UPDATE public.experiment_v2_direct_proof_authorizations" not in sql
    assert "DELETE FROM" not in sql
    assert "TO verdify_experiment_lifecycle" in sql


def test_applied_migration_235_is_not_rewritten() -> None:
    sql = PREVIOUS.read_text()
    assert "v_opened_at < v_fault_at AND v_fault_at < v_recovered_at" in sql
    assert "verdify-direct-proof-startup-raw-reset-v1|" in sql
