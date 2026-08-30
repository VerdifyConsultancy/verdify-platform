"""Forward contract for separate #642 authorization and blinded launch status."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db/migrations/238-experiment-v2-separate-day1-authorization.sql"


def _classifier():
    name = "check_migration_rollback_safety_day1_authorization"
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts/check_migration_rollback_safety.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _body(name: str) -> str:
    sql = MIGRATION.read_text()
    marker = f"CREATE OR REPLACE FUNCTION public.{name}("
    start = sql.index(marker)
    body = sql.index("AS $body$", start)
    return sql[body : sql.index("$body$;", body)]


def test_forward_migration_is_table_preserving_and_rollback_wrap_safe() -> None:
    result = _classifier().classify(MIGRATION)
    assert result.self_committing is False, result.reasons
    sql = MIGRATION.read_text()
    assert not re.search(r"\b(DROP|TRUNCATE)\s+TABLE\b", sql, re.IGNORECASE)
    assert "DELETE FROM" not in sql
    assert "UPDATE public.experiment_v2_randomization" not in sql


def test_scheduler_finalizes_once_then_waits_for_separate_api_approval() -> None:
    body = _body("fn_experiment_v2_direct_launch_cycle")
    assert "fn_experiment_v2_finalize_randomization" in body
    assert "awaiting_separate_day1_approval" in body
    assert "approval.approved_by LIKE 'verdify-api:%'" in body
    assert "fn_experiment_v2_direct_launch_approve_day1" not in body
    for forbidden_input in ("p_secret", "p_rng", "p_random_source", "p_redraw", "p_replace"):
        assert forbidden_input not in body
    sql = MIGRATION.read_text()
    assert "fn_experiment_v2_direct_launch_cycle_pre238" in sql
    assert "FROM verdify_experiment_shadow_scheduler CASCADE" in sql


def test_no_randomized_context_choice_or_work_can_bypass_day1_gate() -> None:
    sql = MIGRATION.read_text()
    for relation in (
        "public.experiment_v2_selector_contexts",
        "public.experiment_v2_selector_choices",
        "public.experiment_v2_work",
    ):
        assert f"ON {relation}" in sql
    assert "WHEN (NEW.operation_kind = 'randomized_assignment')" in sql
    approval_gate = _body("fn_experiment_v2_require_day1_approval")
    assert "approval.approval_kind = 'randomized_day_1'" in approval_gate
    assert "approval.issue_number = 642" in approval_gate
    audit_gate = _body("fn_experiment_v2_day1_approval_audit_binding")
    assert "^verdify-api:" in audit_gate


def test_launch_status_is_blinded_and_names_actionable_kill_rollback_steps() -> None:
    body = _body("fn_experiment_v2_launch_gate_status")
    for required in (
        "awaiting_design_lock",
        "awaiting_internal_finalization",
        "awaiting_separate_day1_approval",
        "set_admission:emergency_hold",
        "exposure_close_first",
        "facility_authorized_baseline_recovery",
        "coarse_disable_after_baseline",
    ):
        assert required in body
    for forbidden in (
        "secret_bytes",
        "x_physical_arm",
        "y_physical_arm",
        "mapping_payload",
        "physical_arm",
        "comparative",
        "efficacy",
    ):
        assert forbidden not in body
    sql = MIGRATION.read_text()
    assert "GRANT EXECUTE ON FUNCTION\n    public.fn_experiment_v2_launch_gate_status()\n    TO verdify" in sql
