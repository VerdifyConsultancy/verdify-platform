"""Cross-layer contract for the baseline-after zero-exposure proof seal."""

import ast
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db/migrations/233-experiment-v2-direct-proof-zero-exposure-seal.sql"
PROOF = ROOT / "deploy/k8s/components/experiment-v2-direct-proof/direct-proof-configmap.yaml"
EXECUTOR = ROOT / "ingestor/tasks/component_experiment.py"


def test_finish_retains_three_state_evidence_and_requires_zero_exposure() -> None:
    sql = MIGRATION.read_text()
    assert "v_before_at < v_aggressive_at AND v_aggressive_at < v_after_at" in sql
    assert "v_before_count < 2 OR v_aggressive_count < 2 OR v_after_count < 2" in sql
    assert "closure.exposure_id IS NULL" in sql
    assert "requires zero open exposure before sealing" in sql
    assert "fn_experiment_v2_close_exposure" not in sql
    assert "TO verdify_experiment_lifecycle" in sql


def test_baseline_after_executor_recovers_without_opening_exposure() -> None:
    source = EXECUTOR.read_text()
    recovery = source.index("if work.operation_kind == WORK_KIND_RECOVERY:")
    recovered = source.index('"recovered",', recovery)
    result = source.index('return ExecutorResult("recovered", "baseline_confirmed"', recovered)
    ordinary_exposure = source.index("await self.store.open_exposure(work, bundle)", result)
    assert recovery < recovered < result < ordinary_exposure


def test_proof_retries_every_current_finish_not_ready_condition() -> None:
    script = yaml.safe_load(PROOF.read_text())["data"]["proof.py"]
    constants = {
        node.value
        for node in ast.walk(ast.parse(script))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    for fragment in (
        "direct proof requires exact-attempt baseline-before, aggressive, baseline-after terminal order",
        "direct proof requires zero open exposure before sealing",
        "direct proof requires distinct receipt-bound two-epoch evidence for all three exact-attempt states",
        "direct proof evidence must span one completed 3-minute-to-12-hour interval inside the active attempt authorization",
    ):
        assert fragment in constants
    assert "isinstance(retry_fragment, str)" in script
