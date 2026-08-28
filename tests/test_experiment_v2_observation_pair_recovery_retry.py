"""Cross-layer contract for fresh observation pairing and recovery retry."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db/migrations/232-experiment-v2-observation-pair-recovery-retry.sql"
PROOF = ROOT / "deploy/k8s/components/experiment-v2-direct-proof/direct-proof-configmap.yaml"
EXECUTOR = ROOT / "ingestor/tasks/component_experiment.py"


def _function(sql: str, name: str) -> str:
    start = sql.index(f"public.{name}(")
    end = sql.index("$body$;", start) + len("$body$;")
    return sql[start:end]


def test_window_returns_earliest_and_latest_fresh_bundle_epochs() -> None:
    body = _function(MIGRATION.read_text(), "fn_experiment_v2_read_observation_window")
    assert "ranked_post_epochs" in body
    assert "earliest_rank" in body and "latest_rank" in body
    assert "ranked.earliest_rank = 1 OR ranked.latest_rank = 1" in body
    assert "v_now - e.last_observed_at <= interval '90 seconds'" in body
    assert "LIMIT 2" not in body
    assert "r.work_id = e.work_id" in body
    assert "r.bundle_id = e.bundle_id" in body


def test_too_close_pair_is_pending_until_a_qualifying_latest_epoch_exists() -> None:
    source = EXECUTOR.read_text()
    assert 'ConfirmationResult(False, True, "observation_epoch_separation_too_short")' in source


def test_retry_chains_only_the_exact_terminal_failed_recovery() -> None:
    body = _function(
        MIGRATION.read_text(),
        "fn_experiment_v2_direct_proof_retry_emergency_recovery",
    )
    assert "failed_work.event_kind = 'failed'" in body
    assert "v_exp.admission_state = 'emergency_hold'" in body
    assert "v_exp.admission_state = 'baseline_recovery'" in body
    assert "closure.exposure_id IS NULL" in body
    assert "IF v_exp.admission_state = 'emergency_hold' THEN" in body
    assert "TO verdify_experiment_lifecycle" in MIGRATION.read_text()


def test_proof_finishes_or_append_only_retries_a_persisted_recovery() -> None:
    script = PROOF.read_text()
    settle = script.index('status["admission_state"] in ("emergency_hold", "baseline_recovery")')
    finish = script.index("FINISH_EMERGENCY_RECOVERY_SQL", settle)
    retry = script.index("RETRY_EMERGENCY_RECOVERY_SQL", finish)
    assert settle < finish < retry
    assert "emergency retry authority changed outside the recovery chain" in script
    assert "failed predecessor, matching authority, and no open exposure" in script
