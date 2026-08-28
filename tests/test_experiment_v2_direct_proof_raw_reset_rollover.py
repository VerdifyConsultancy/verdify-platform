"""Cross-layer contract for the pre-claim raw-reset proof rollover."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db/migrations/235-experiment-v2-direct-proof-raw-reset-rollover.sql"
PROOF = ROOT / "deploy/k8s/components/experiment-v2-direct-proof/direct-proof-configmap.yaml"


def test_raw_reset_resolution_requires_exact_runtime_fault_and_recovery() -> None:
    sql = MIGRATION.read_text()
    for required in (
        "fault.fault_source = 'raw_reset_epoch'",
        "fault.reported_fault_kind = 'reboot'",
        "fault.admission_state_after = 'baseline_recovery'",
        "fault.authority_hold_required",
        "NOT fault.facility_authority_yielded",
        "recovery.parent_work_id IS NULL",
        "recovery.work_id <> v_baseline_before_work_id",
        "v_opened_at < v_fault_at AND v_fault_at < v_recovered_at",
        "count(DISTINCT receipt.receipt_id)::integer",
        "closure.exposure_id IS NULL",
        "startup_raw_reset_before_aggressive_claim",
        "lease_generation = lease_generation + 1",
    ):
        assert required in sql


def test_raw_reset_resolution_is_append_only_and_does_not_credit_proof() -> None:
    sql = MIGRATION.read_text()
    assert "INSERT INTO public.experiment_v2_work_events" in sql
    assert "INSERT INTO public.experiment_v2_direct_proof_attempt_events" in sql
    assert "INSERT INTO public.experiment_v2_direct_proof_emergency_recovery_receipts" in sql
    assert "INSERT INTO public.experiment_v2_direct_proof_receipts" not in sql
    assert "UPDATE public.experiment_v2_direct_proof_authorizations" not in sql
    assert "DELETE FROM" not in sql
    assert "TO verdify_experiment_lifecycle" in sql


def test_proof_retries_the_raw_reset_not_ready_contract() -> None:
    proof = PROOF.read_text()
    assert "raw-reset attempt, its recovered root baseline, and zero open exposure" in proof
