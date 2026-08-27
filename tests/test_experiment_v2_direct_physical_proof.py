"""Source-contract gates for the one-study attended physical proof."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db/migrations/222-experiment-v2-direct-physical-proof.sql"


def _sql() -> str:
    return MIGRATION.read_text()


def test_direct_proof_ledgers_are_immutable_and_bound_to_one_exact_study() -> None:
    sql = _sql()
    assert "experiment_id uuid NOT NULL UNIQUE" in sql
    assert "CHECK (experiment_id = '45039c86-c1d9-52f6-a0a9-d94a17bc4b14'::uuid)" in sql
    for table in (
        "experiment_v2_direct_proof_authorizations",
        "experiment_v2_direct_proof_receipts",
    ):
        assert f"CREATE TABLE IF NOT EXISTS public.{table}" in sql
        assert f"trg_{table}_immutable" in sql
        assert f"REVOKE ALL PRIVILEGES ON TABLE public.{table}" in sql


def test_begin_creates_baseline_before_under_jasons_exact_attended_roles() -> None:
    sql = _sql()
    begin = sql[sql.index("CREATE OR REPLACE FUNCTION public.fn_experiment_v2_direct_proof_begin(") :]
    begin = begin[: begin.index("CREATE OR REPLACE FUNCTION", 20)]
    assert "p_supervisor_role IS DISTINCT FROM 'Jason Vallery'" in begin
    assert "p_rescue_owner_role IS DISTINCT FROM 'Jason Vallery'" in begin
    assert "admission_state = 'baseline_recovery'" in begin
    assert "'direct-proof-baseline-before'" in begin
    assert begin.index("INSERT INTO public.experiment_v2_work") < begin.index(
        "public.fn_experiment_v2_request_recovery_at("
    )
    assert "NOT v_now <@ p_proof_valid_range" in begin
    assert "interval '3 minutes'" in begin
    assert "interval '12 hours'" in begin


def test_aggressive_and_baseline_after_are_separate_fail_closed_steps() -> None:
    sql = _sql()
    opened = sql[sql.index("CREATE OR REPLACE FUNCTION public.fn_experiment_v2_direct_proof_open_aggressive(") :]
    opened = opened[: opened.index("CREATE OR REPLACE FUNCTION", 20)]
    assert "v_exp.admission_state <> 'baseline_recovery'" in opened
    assert "recovered.event_kind = 'recovered'" in opened
    assert "HAVING count(*) >= 2" in opened
    assert "SET admission_state = 'open'" in opened
    assert "experiment_v2_approvals" not in opened

    after = sql[sql.index("CREATE OR REPLACE FUNCTION public.fn_experiment_v2_direct_proof_begin_baseline_after(") :]
    after = after[: after.index("CREATE OR REPLACE FUNCTION", 20)]
    assert "v_exp.admission_state <> 'open'" in after
    assert "completed.event_kind = 'completed'" in after
    assert "closure.exposure_id IS NULL" in after
    assert "'direct-proof-baseline-after'" in after
    assert "v_now - v_before_at < interval '151 seconds'" in after
    assert "interval '90 seconds'" in after
    assert "SET admission_state = 'baseline_recovery'" in after


def test_finish_seals_actual_three_state_evidence_then_returns_feature_off() -> None:
    sql = _sql()
    finish = sql[sql.index("CREATE OR REPLACE FUNCTION public.fn_experiment_v2_direct_proof_finish(") :]
    finish = finish[: finish.index("CREATE OR REPLACE FUNCTION", 20)]
    assert "v_recovery_count <> 2" in finish
    assert "v_before_at < v_aggressive_at AND v_aggressive_at < v_after_at" in finish
    assert "v_before_count < 2 OR v_aggressive_count < 2 OR v_after_count < 2" in finish
    assert "v_proof_range := tstzrange(" in finish
    assert "NOT v_proof_range <@ v_auth.proof_valid_range" in finish
    assert "upper(v_proof_range) - lower(v_proof_range) < interval '3 minutes'" in finish
    assert "proof_valid_range, proof_receipt_sha256" in finish
    assert finish.index("public.fn_experiment_v2_close_exposure(") < finish.index(
        "SET execution_phase = 'shadow', admission_state = 'closed'"
    )
    assert "component_enabled = false" in finish


def test_commit_accepts_design_only_and_trigger_binds_the_sealed_receipt() -> None:
    sql = _sql()
    commit = sql[sql.index("CREATE OR REPLACE FUNCTION public.fn_experiment_v2_direct_launch_commit(") :]
    commit = commit[: commit.index("CREATE OR REPLACE FUNCTION", 20)]
    signature = commit[: commit.index(") RETURNS")]
    for forbidden in (
        "p_authorization_ref",
        "p_qualification_artifact_sha256",
        "p_baseline_before_evidence_sha256",
        "p_aggressive_evidence_sha256",
        "p_baseline_after_evidence_sha256",
        "p_proof_valid_range",
        "p_supervisor_role",
        "p_rescue_owner_role",
    ):
        assert forbidden not in signature
    assert "v_receipt.proof_valid_range" in commit
    assert "v_receipt.proof_receipt_sha256" in commit

    binding = sql[sql.index("CREATE OR REPLACE FUNCTION public.fn_experiment_v2_direct_waiver_proof_binding(") :]
    assert "BEFORE INSERT ON public.experiment_v2_direct_launch_waivers" in binding
    assert "v_receipt.proof_valid_range" in binding
    assert "RAISE EXCEPTION 'direct-launch waiver must consume the exact sealed physical proof'" in binding


def test_only_the_attested_lifecycle_role_receives_the_five_entrypoints() -> None:
    sql = _sql()
    expected = {
        "public.fn_experiment_v2_direct_proof_begin(uuid,text,tstzrange,text,text,text)",
        "public.fn_experiment_v2_direct_proof_open_aggressive(uuid,uuid,text)",
        "public.fn_experiment_v2_direct_proof_begin_baseline_after(uuid,uuid,text)",
        "public.fn_experiment_v2_direct_proof_finish(uuid,text)",
        "public.fn_experiment_v2_direct_launch_commit(uuid,date,integer,time without time zone,text,text,text,text,text,text,text,text,text,text,text)",
    }
    grant_block = sql[sql.rindex("FOREACH fn IN ARRAY ARRAY[") :]
    assert all(f"'{signature}'::regprocedure" in grant_block for signature in expected)
    assert grant_block.count("'public.fn_experiment_v2_") == len(expected)
    assert "GRANT EXECUTE ON FUNCTION %s TO verdify_experiment_lifecycle" in grant_block
    assert "FROM PUBLIC CASCADE" in sql
