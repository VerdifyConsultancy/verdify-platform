"""Forward-only contract for the retained orphaned preclaim recovery lineage."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db/migrations/239-experiment-v2-orphaned-preclaim-recovery.sql"
PREVIOUS = ROOT / "db/migrations/237-experiment-v2-direct-proof-recovery-range-rollover.sql"
FIXTURE = ROOT / "db/migrations/tests/test-239-experiment-v2-orphaned-preclaim-recovery.sql"
PROOF = ROOT / "deploy/k8s/components/experiment-v2-direct-proof/direct-proof-configmap.yaml"


def test_orphaned_recovery_requires_exact_retained_noncausal_lineage() -> None:
    sql = MIGRATION.read_text()
    for required in (
        "45039c86-c1d9-52f6-a0a9-d94a17bc4b14",
        "recovery.created_at > v_aggressive.created_at",
        "recovery.created_at > upper(v_auth.proof_valid_range)",
        "recovery.parent_work_id IS NULL",
        "recovery.operation_kind = 'baseline_recovery'",
        "recovery.target_profile = 'baseline'",
        "recovery.created_by = 'verdify-component-executor-v2'",
        "recovery.revision_bundle_sha256 = v_exp.revision_bundle_sha256",
        "recovery.lease_generation = v_aggressive.lease_generation",
        "v_exp.lease_generation - 1",
        "upper(recovery.valid_range) - lower(recovery.valid_range) =",
        "interval '5 minutes'",
        "recovered.recorded_at <@ recovery.valid_range",
        "(recovered.detail->>'confirmed_at')::timestamptz <@",
        "fault.recovery_work_id = recovery.work_id",
        "count(DISTINCT observation.receipt_id)",
        "ORDER BY recovery.created_at DESC, recovered.recorded_at DESC",
        "LIMIT 1",
    ):
        assert required in sql
    assert "fault.reported_fault_kind = 'reboot'" not in sql
    assert "fault.fault_source = 'raw_reset_epoch'" not in sql


def test_orphaned_resolution_is_append_only_nonproof_and_truthful() -> None:
    sql = MIGRATION.read_text()
    for required in (
        "INSERT INTO public.experiment_v2_direct_proof_emergency_resolutions",
        "INSERT INTO public.experiment_v2_work_events",
        "INSERT INTO public.experiment_v2_direct_proof_attempt_events",
        "INSERT INTO public.experiment_v2_direct_proof_emergency_recovery_receipts",
        "verdify-direct-proof-orphaned-preclaim-recovery-v1|",
        "Orphaned preclaim recovery; runtime-fault receipt absent and cause unproven",
        "orphaned_preclaim_recovery_cause_unproven",
        "'runtime_fault_receipt', 'absent'",
        "'recovery_cause', 'unproven'",
    ):
        assert required in sql
    assert "INSERT INTO public.experiment_v2_direct_proof_receipts" not in sql
    assert "UPDATE public.experiment_v2_direct_proof_authorizations" not in sql
    assert "DELETE FROM" not in sql
    assert "verdify-direct-proof-startup-raw-reset-" not in sql


def test_resolution_atomically_closes_and_fences_authority() -> None:
    sql = MIGRATION.read_text()
    receipt_insert = sql.index("INSERT INTO public.experiment_v2_direct_proof_emergency_recovery_receipts")
    authority_update = sql.index("UPDATE public.control_experiments", receipt_insert)
    assert receipt_insert < authority_update
    for required in (
        "execution_phase = 'shadow', admission_state = 'closed'",
        "component_enabled = false",
        "lease_generation = lease_generation + 1",
        "SET execution_phase = 'shadow'",
    ):
        assert required in sql
    assert "fn_experiment_v2_set_admission" not in sql


def test_resolution_keeps_exact_function_acl() -> None:
    sql = MIGRATION.read_text()
    assert "LANGUAGE plpgsql" in sql
    assert "SECURITY DEFINER" in sql
    assert "SET search_path = public, pg_temp" in sql
    assert "OWNER TO verdify_experiment_v2_owner" in sql
    assert "FROM PUBLIC CASCADE" in sql
    assert "TO verdify_experiment_lifecycle" in sql


def test_applied_recovery_range_migration_is_not_rewritten() -> None:
    sql = PREVIOUS.read_text()
    assert "fault.fault_source = 'raw_reset_epoch'" in sql
    assert "fault.reported_fault_kind = 'reboot'" in sql
    assert "verdify-direct-proof-startup-raw-reset-v3|" in sql


def test_restored_fixture_executes_exact_lineage_and_rolls_back() -> None:
    sql = FIXTURE.read_text()
    for required in (
        "d00304d1-74f9-4872-857e-6944de53ac46",
        "7aa1f560-a309-4d17-b9ad-57a20574f05d",
        "7093f8c3-a36e-49f2-8b4b-443d32a9a51b",
        "0fa6d172de87cf2008d5908ff4a3517eeca1d1cd4811e86461fc349c25f41b91",
        "public.fn_experiment_v2_set_admission(",
        "public.fn_experiment_v2_direct_proof_resolve_startup_rollover(",
        "v_after.execution_phase <> 'shadow'",
        "v_after.admission_state <> 'closed'",
        "v_after.lease_generation <> v_before.lease_generation + 1",
        "runtime_fault_receipt' = 'absent'",
        "orphaned_preclaim_recovery_cause_unproven",
        "FROM public.experiment_v2_direct_proof_receipts proof",
    ):
        assert required in sql
    assert sql.rstrip().endswith("ROLLBACK;")


def test_proof_runner_retries_only_the_new_exact_not_ready_contract() -> None:
    proof = re.sub(r'"\s+"', "", PROOF.read_text())
    assert (
        "orphaned-preclaim attempt, its recovered root baseline without a runtime-fault receipt, and zero open exposure"
    ) in " ".join(proof.split())
