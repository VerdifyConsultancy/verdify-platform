"""Source-contract gates for restart-stable emergency recovery retry 227."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db/migrations/227-experiment-v2-emergency-recovery-retry.sql"
FIXTURE = ROOT / "db/migrations/tests/test-227-experiment-v2-emergency-recovery-retry.sql"


def _body(name: str) -> str:
    sql = MIGRATION.read_text()
    match = re.search(rf"CREATE OR REPLACE FUNCTION\s+public\.{re.escape(name)}\(", sql)
    assert match is not None
    start = match.start()
    end = sql.index("$body$;", start) + len("$body$;")
    return sql[start:end]


def test_recovery_successors_are_append_only_and_exactly_chained() -> None:
    sql = MIGRATION.read_text()
    assert "ADD COLUMN IF NOT EXISTS recovery_attempt_number integer NOT NULL DEFAULT 1" in sql
    assert "DROP CONSTRAINT IF EXISTS" in sql
    assert "uq_experiment_v2_direct_proof_emergency_recovery_attempt" in sql
    assert "CREATE TABLE IF NOT EXISTS\n    public.experiment_v2_direct_proof_emergency_recovery_attempt_events" in sql
    assert "failed_resolution_id uuid NOT NULL UNIQUE" in sql
    assert "successor_resolution_id uuid NOT NULL UNIQUE" in sql
    assert "BEFORE UPDATE OR DELETE" in sql


def test_retry_requires_failed_predecessor_stable_writer_and_closed_exposure() -> None:
    body = _body("fn_experiment_v2_direct_proof_retry_emergency_recovery")
    for required in (
        "v_latest.resolution_id <> v_failed.resolution_id",
        "failed_work.event_kind = 'failed'",
        "v_exp.admission_state <> 'emergency_hold'",
        "v_exp.component_enabled",
        "closure.exposure_id IS NULL",
        "v_generation.recorded_at > v_now - interval '4 minutes'",
        "fault.recorded_at > v_now - interval '2 minutes'",
        "current writer generation is not yet stable",
        "fn_experiment_v2_request_recovery_at",
        "fn_experiment_v2_set_admission",
    ):
        assert required in body
    assert body.index("experiment_v2_direct_proof_emergency_resolutions") < body.index("fn_experiment_v2_set_admission")


def test_status_and_successor_proof_consume_only_completed_recovery_chain() -> None:
    status = _body("fn_experiment_v2_direct_proof_attempt_status")
    begin = _body("fn_experiment_v2_direct_proof_begin")
    assert "ORDER BY candidate.recovery_attempt_number DESC" in status
    assert "receipt.resolution_id = resolution.resolution_id" in status
    assert "experiment_v2_direct_proof_emergency_recovery_receipts" in begin
    assert "receipt.authorization_id = v_previous.authorization_id" in begin
    assert "direct-proof retry requires one resolved, not-yet-superseded failed attempt" in begin


def test_only_lifecycle_can_retry_and_fixture_is_read_only() -> None:
    sql = MIGRATION.read_text()
    signature = (
        "public.fn_experiment_v2_direct_proof_retry_emergency_recovery("
        "uuid,uuid,uuid,text,bigint,tstzrange,text,text,text)"
    )
    assert f"'{signature}'::regprocedure" in sql
    assert "GRANT EXECUTE ON FUNCTION %s TO verdify_experiment_lifecycle" in sql
    assert "FROM PUBLIC CASCADE" in sql
    fixture = FIXTURE.read_text()
    assert fixture.startswith("-- Read-only catalog/data/ACL fixture")
    assert "BEGIN;" in fixture and fixture.rstrip().endswith("ROLLBACK;")
    for mutation in ("INSERT ", "UPDATE ", "DELETE "):
        assert mutation not in fixture
