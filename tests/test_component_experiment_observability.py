"""Blinded-safe v2 safety/integrity board and pager contracts (#587)."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import get_args

import pytest

from verdify_schemas.alerts import AlertEnvelope, ComponentExperimentIntegrityDetails

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db/migrations/215-experiment-v2-ops-observability.sql"
ROLE_BOUNDARY_MIGRATION = ROOT / "db/migrations/217-runtime-role-boundary.sql"
DASHBOARD = ROOT / "grafana/provisioning/dashboards/json/confirmed-component-experiment-v2.json"

_ALERT_REASON_CASE = re.compile(
    r"CASE\s+"
    r"WHEN coalesce\(exposures\.open_count, 0\) > 1\s+"
    r"THEN '(multiple_open_exposures)'"
    r"(?P<remaining_branches>.*?)"
    r"\s+ELSE NULL\s+END,\s+v_now",
    re.DOTALL,
)


def _function_body(sql: str) -> str:
    return sql.split("AS $body$", 1)[1].split("$body$;", 1)[0]


def _db_integrity_reasons(migration: Path) -> frozenset[str]:
    migration_sql = migration.read_text()
    ops_function_sql = migration_sql.split("CREATE OR REPLACE FUNCTION public.fn_experiment_v2_ops_status()", 1)[1]
    match = _ALERT_REASON_CASE.search(_function_body(ops_function_sql))
    assert match is not None, f"alert-reason CASE not found in {migration.name}"
    return frozenset(
        [
            match.group(1),
            *re.findall(r"THEN '([a-z0-9_]+)'", match.group("remaining_branches")),
        ]
    )


DB_INTEGRITY_REASONS = sorted(_db_integrity_reasons(MIGRATION) | _db_integrity_reasons(ROLE_BOUNDARY_MIGRATION))


def _load_rollback_classifier():
    path = ROOT / "scripts/check_migration_rollback_safety.py"
    spec = importlib.util.spec_from_file_location("v2_ops_rollback_classifier", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_alert_helper():
    ingestor = str(ROOT / "ingestor")
    if ingestor not in sys.path:
        sys.path.insert(0, ingestor)
    from tasks.alerts import _component_experiment_integrity_alert

    return _component_experiment_integrity_alert


def _ops_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "experiment_id": "11111111-1111-4111-8111-111111111111",
        "lifecycle_status": "running",
        "execution_phase": "randomized",
        "admission_state": "baseline_recovery",
        "operation_kind": "baseline_recovery",
        "observation_age_seconds": 12,
        "observation_truth": "exact",
        "open_exposure_count": 0,
        "writer_generation": 4,
        "connection_generation": 8,
        "safety_state": "baseline_recovery",
        "outcomes_complete": False,
        "rollback_ready": True,
        "alert_severity": "warning",
        "alert_reason": "baseline_recovery_in_progress",
    }
    row.update(overrides)
    return row


def test_ops_migration_is_additive_rollback_safe_and_function_only():
    sql = MIGRATION.read_text()
    result = _load_rollback_classifier().classify(MIGRATION)
    assert result.self_committing is False
    assert "SECURITY DEFINER" in sql
    assert "REVOKE ALL ON FUNCTION public.fn_experiment_v2_ops_status() FROM PUBLIC" in sql
    assert "GRANT EXECUTE ON FUNCTION public.fn_experiment_v2_ops_status() TO verdify" in sql
    assert "GRANT SELECT ON" not in sql
    assert "GRANT UPDATE ON" not in sql


def test_ops_function_uses_one_server_clock_and_never_reads_quarantined_mapping():
    body = _function_body(MIGRATION.read_text())
    assert body.count("clock_timestamp()") == 1
    for forbidden in (
        "secret_bytes",
        "control_arm_resolutions",
        "mapping_payload",
        "revealed_secret",
        "physical_arm",
    ):
        assert forbidden not in body
    assert "future_masked" in body
    assert "CASE WHEN selected.future_masked THEN NULL ELSE selected.assignment_id END" in body


def test_ops_function_masks_only_not_yet_started_randomized_identity():
    body = _function_body(MIGRATION.read_text())
    assert "lower(w.valid_range) > v_now) AS future_masked" in body
    assert "NOT (v_now <@ w.valid_range)) AS future_masked" not in body


def test_ops_function_fails_closed_for_truth_outcomes_and_rollback():
    body = _function_body(MIGRATION.read_text())
    assert "WHEN selected.target_state_content_sha256 IS NULL THEN 'expected_state_missing'" in body
    assert "observed_state_content_sha256 IS DISTINCT FROM" in body
    assert "FROM public.control_assignments a" in body
    assert "FROM public.experiment_v2_outcomes o\n            LEFT JOIN" not in body
    assert "baseline_confirmation.present" in body
    assert "receipt.policy_state_content_sha256" in body
    assert ">= 2" in body


def test_ops_function_alerts_terminal_expiry_and_preexposure_faults():
    body = _function_body(MIGRATION.read_text())
    for reason in (
        "terminal_experiment_capability_enabled",
        "open_exposure_work_expired",
        "expired_work_not_terminal",
        "confirmed_baseline_recovery_missing",
        "preexposure_state_mismatch",
    ):
        assert reason in body
    assert "experiment_v2_preexposure_mismatch_epochs" in body


def test_migration_alert_reasons_and_typed_schema_stay_reconciled():
    migration_reasons = _db_integrity_reasons(MIGRATION)
    role_boundary_reasons = _db_integrity_reasons(ROLE_BOUNDARY_MIGRATION)
    schema_reasons = frozenset(get_args(ComponentExperimentIntegrityDetails.model_fields["reason"].annotation))

    assert migration_reasons == role_boundary_reasons
    assert migration_reasons == schema_reasons
    assert {
        "expired_work_not_terminal",
        "confirmed_baseline_recovery_missing",
    } <= schema_reasons


def test_dashboard_uses_only_the_blinded_safe_function():
    dashboard = json.loads(DASHBOARD.read_text())
    assert dashboard["uid"] == "confirmed-component-experiment-v2"
    sql_targets = [target["rawSql"] for panel in dashboard["panels"] for target in panel.get("targets", [])]
    assert len(sql_targets) >= 5
    assert all("fn_experiment_v2_ops_status()" in query for query in sql_targets)
    combined = "\n".join(sql_targets).lower()
    for forbidden in (
        "control_arm_resolutions",
        "secret_bytes",
        "revealed_secret",
        "mapping_payload",
        "physical_arm",
        "experiment_v2_randomization",
    ):
        assert forbidden not in combined


def test_actionable_ops_row_round_trips_the_typed_alert_envelope():
    payload = _load_alert_helper()(_ops_row())
    assert payload is not None
    envelope = AlertEnvelope.model_validate(payload)
    assert envelope.alert_type == "component_experiment_integrity"
    assert envelope.severity == "warning"
    assert envelope.details["reason"] == "baseline_recovery_in_progress"


@pytest.mark.parametrize("reason", DB_INTEGRITY_REASONS)
def test_every_db_integrity_reason_round_trips_instead_of_falling_back(reason: str):
    payload = _load_alert_helper()(_ops_row(alert_reason=reason))
    assert payload is not None
    envelope = AlertEnvelope.model_validate(payload)
    assert envelope.alert_type == "component_experiment_integrity"
    assert envelope.details["reason"] == reason


def test_nominal_ops_row_does_not_page():
    payload = _load_alert_helper()(_ops_row(alert_severity=None, alert_reason=None, admission_state="closed"))
    assert payload is None
