"""Cross-layer contract for baseline-only emergency recovery sealing."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db/migrations/231-experiment-v2-emergency-recovery-zero-exposure.sql"
EXECUTOR = ROOT / "ingestor/tasks/component_experiment.py"


def test_finish_seals_only_from_zero_open_exposure() -> None:
    sql = MIGRATION.read_text()
    assert "recovered.event_kind = 'recovered'" in sql
    assert "v_receipt_count < 2" in sql
    assert "IF EXISTS (" in sql
    assert "closure.exposure_id IS NULL" in sql
    assert "requires zero open exposure before sealing" in sql
    assert "fn_experiment_v2_close_exposure" not in sql
    assert "TO verdify_experiment_lifecycle" in sql


def test_recovery_executor_records_evidence_without_opening_exposure() -> None:
    source = EXECUTOR.read_text()
    recovery = source.index("if work.operation_kind == WORK_KIND_RECOVERY:")
    recovered = source.index('"recovered",', recovery)
    result = source.index('return ExecutorResult("recovered", "baseline_confirmed"', recovered)
    ordinary_exposure = source.index("await self.store.open_exposure(work, bundle)", result)
    assert recovery < recovered < result < ordinary_exposure
