"""Regression gates for the exact direct-proof work-trigger exception."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db/migrations/223-experiment-v2-direct-proof-work-binding.sql"


def test_direct_proof_exception_is_exact_and_keeps_ordinary_canary_gate() -> None:
    sql = MIGRATION.read_text()
    direct = sql[
        sql.index("v_direct_proof :=") : sql.index(
            "IF NEW.operation_kind = 'commissioning_canary'", sql.index("v_direct_proof :=")
        )
    ]
    for required in (
        "NEW.target_profile = 'aggressive'",
        "NEW.assignment_id IS NULL",
        "NEW.parent_work_id IS NULL",
        "45039c86-c1d9-52f6-a0a9-d94a17bc4b14",
        "v_exp.status = 'draft'",
        "v_exp.execution_phase = 'commissioning'",
        "v_exp.admission_state = 'baseline_recovery'",
        "authorization.proof_valid_range = NEW.valid_range",
        "NEW.expires_at = upper(authorization.proof_valid_range)",
        "clock_timestamp() <@ authorization.proof_valid_range",
        "authorization.supervisor_role = 'Jason Vallery'",
        "authorization.rescue_owner_role = 'Jason Vallery'",
        "authorization.authorized_by = NEW.created_by",
    ):
        assert required in direct
    assert "NOT v_direct_proof AND NOT EXISTS" in sql
    assert "a.approval_kind = 'combined_physical'" in sql
    assert "INSERT INTO public.experiment_v2_approvals" not in sql


def test_forward_migration_preserves_all_other_work_binding_guards() -> None:
    sql = MIGRATION.read_text()
    for operation in (
        "shadow_preview",
        "commissioning_probe",
        "commissioning_canary",
        "aa_baseline_rehearsal",
        "randomized_assignment",
        "baseline_recovery",
    ):
        assert operation in sql
    assert "work identity/revision/phase must bind the current frozen v2 state" in sql
    assert "randomized work target does not match hidden A/B plus daily choice" in sql
    assert "ALTER FUNCTION public.fn_experiment_v2_work_insert_binding()" in sql
