"""Source-contract gates for append-only direct-proof retry migration 225."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db/migrations/225-experiment-v2-direct-proof-retry.sql"
FIXTURE = ROOT / "db/migrations/tests/test-225-experiment-v2-direct-proof-retry.sql"


def _sql() -> str:
    return MIGRATION.read_text()


def _body(name: str) -> str:
    sql = _sql()
    start = sql.index(f"CREATE OR REPLACE FUNCTION public.{name}(")
    end = sql.index("$body$;", start) + len("$body$;")
    return sql[start:end]


def test_attempt_ledgers_are_append_only_and_preserve_attempt_one() -> None:
    sql = _sql()
    assert "ADD COLUMN IF NOT EXISTS attempt_number integer NOT NULL DEFAULT 1" in sql
    assert "DROP CONSTRAINT IF EXISTS experiment_v2_direct_proof_authorizations_experiment_id_key" in sql
    assert "uq_experiment_v2_direct_proof_attempt_number" in sql
    for table in (
        "experiment_v2_direct_proof_attempt_work",
        "experiment_v2_direct_proof_attempt_events",
        "experiment_v2_direct_proof_emergency_resolutions",
        "experiment_v2_direct_proof_emergency_recovery_receipts",
    ):
        assert f"CREATE TABLE IF NOT EXISTS public.{table}" in sql
        assert f"ALTER TABLE public.{table}\n    OWNER TO verdify_experiment_v2_owner" in sql
    assert "BEFORE UPDATE OR DELETE" in sql
    assert "migration 225 cannot deterministically bind" in sql
    assert "migration 225 found a cross-attempt direct-proof work/evidence binding" in sql


def test_bounded_recovery_requires_exact_preconditions_and_real_receipts() -> None:
    begin = _body("fn_experiment_v2_direct_proof_begin_emergency_recovery")
    finish = _body("fn_experiment_v2_direct_proof_finish_emergency_recovery")
    assert "p_expected_revision_bundle_sha256" in begin
    assert "p_expected_emergency_lease_generation" in begin
    assert "v_exp.admission_state <> 'emergency_hold'" in begin
    assert "p_recovery_valid_range" in begin
    assert "interval '3 minutes'" in begin
    assert "interval '30 minutes'" in begin
    assert "p_experiment_id, NULL, p_recovery_valid_range" in begin
    assert begin.index("experiment_v2_direct_proof_emergency_resolutions") < begin.index(
        "fn_experiment_v2_set_admission"
    )
    assert "recovered.event_kind = 'recovered'" in finish
    assert "v_receipt_count < 2" in finish
    assert "recovery.work_id = v_resolution.recovery_work_id" in finish
    assert finish.index("fn_experiment_v2_close_exposure") < finish.index("fn_experiment_v2_set_admission")
    assert "SET execution_phase = 'shadow', component_enabled = false" in finish


def test_successor_is_one_active_attempt_and_never_reuses_work() -> None:
    begin = _body("fn_experiment_v2_direct_proof_begin")
    assert "ORDER BY authz.attempt_number DESC" in begin
    assert "direct proof is already complete and cannot be retried" in begin
    assert "direct-proof retry requires one resolved, not-yet-superseded failed attempt" in begin
    assert "coalesce(v_previous.attempt_number + 1, 1)" in begin
    assert "'superseded'" in begin
    assert "successor_authorization_id" in begin
    assert begin.count("INSERT INTO public.experiment_v2_direct_proof_attempt_work") == 2


def test_every_proof_stage_and_receipt_selects_the_exact_active_attempt() -> None:
    for name in (
        "fn_experiment_v2_direct_proof_open_aggressive",
        "fn_experiment_v2_direct_proof_begin_baseline_after",
        "fn_experiment_v2_direct_proof_finish",
    ):
        body = _body(name)
        assert "experiment_v2_direct_proof_attempt_work" in body
        assert "experiment_v2_direct_proof_attempt_events" in body
        assert "receipt.authorization_id = authz.authorization_id" in body
    binding = _body("fn_experiment_v2_direct_proof_receipt_attempt_binding")
    for stage in ("baseline_before", "aggressive", "baseline_after"):
        assert f"mapped.stage = '{stage}'" in binding
    launch = _body("fn_experiment_v2_direct_launch_commit")
    waiver = _body("fn_experiment_v2_direct_waiver_proof_binding")
    assert "authorization_id = v_receipt.authorization_id" in launch
    assert "authorization_id = v_receipt.authorization_id" in waiver
    assert "WHERE experiment_id = p_experiment_id;" not in launch.split("SELECT * INTO v_auth", 1)[1]


def test_restore_fixture_is_read_only_and_checks_replay_invariants() -> None:
    fixture = FIXTURE.read_text()
    assert fixture.startswith("-- PostgreSQL catalog/data invariant fixture")
    assert "BEGIN;" in fixture and fixture.rstrip().endswith("ROLLBACK;")
    assert "INSERT " not in fixture
    assert "UPDATE " not in fixture
    assert "DELETE " not in fixture
    assert "more than one direct-proof attempt is active" in fixture
    assert "a successor lacks a completed immutable emergency resolution" in fixture
    assert "cross-attempt work/evidence binding exists" in fixture


def test_only_lifecycle_receives_the_three_new_entrypoints() -> None:
    sql = _sql()
    expected = {
        "public.fn_experiment_v2_direct_proof_resolve_emergency(uuid,uuid,text,bigint,text,text,text,text)",
        "public.fn_experiment_v2_direct_proof_begin_emergency_recovery(uuid,uuid,text,bigint,tstzrange,text,text,text)",
        "public.fn_experiment_v2_direct_proof_finish_emergency_recovery(uuid,uuid,text)",
    }
    for signature in expected:
        assert f"'{signature}'::regprocedure" in sql
        assert signature in sql
    assert "GRANT EXECUTE ON FUNCTION %s TO verdify_experiment_lifecycle" in sql
    assert "FROM PUBLIC CASCADE" in sql
