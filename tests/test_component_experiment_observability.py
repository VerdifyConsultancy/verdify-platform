"""Blinded-safe v2 safety/integrity board and pager contracts (#587)."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import get_args

import pytest

from verdify_schemas.alerts import (
    AlertEnvelope,
    ComponentExperimentIntegrityReason,
    ComponentExperimentObservationTruth,
)

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db/migrations/215-experiment-v2-ops-observability.sql"
DASHBOARD = ROOT / "grafana/provisioning/dashboards/json/confirmed-component-experiment-v2.json"

_OPS_STATUS_SIGNATURE = "CREATE OR REPLACE FUNCTION public.fn_experiment_v2_ops_status()"
_FUNCTION_BODY_START = re.compile(r"\bAS\s+(?P<tag>\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$)")
_ALERT_CASES = re.compile(
    r"e\.admission_state\s*<>\s*'emergency_hold',\s*"
    r"(?P<severity_case>CASE\s+WHEN\s+.*?\s+ELSE\s+NULL\s+END),\s*"
    r"(?P<reason_case>CASE\s+WHEN\s+.*?\s+ELSE\s+NULL\s+END),\s*"
    r"v_now\s+FROM\s+public\.control_experiments",
    re.DOTALL,
)
_CASE_BRANCH = re.compile(
    r"\bWHEN\s+(?P<condition>.*?)\s+THEN\s+'(?P<value>[a-z0-9_]+)'",
    re.DOTALL,
)
_OBSERVATION_TRUTH_CASE = re.compile(
    r"(?P<truth_case>CASE\s+WHEN\s+selected\.future_masked\s+THEN\s+"
    r"'future_identity_masked'.*?ELSE\s+'exact'\s+END),\s*"
    r"coalesce\(exposures\.open_count",
    re.DOTALL,
)


@dataclass(frozen=True)
class _CaseBranch:
    condition: str
    value: str


def _function_body(sql: str) -> str:
    return sql.split("AS $body$", 1)[1].split("$body$;", 1)[0]


def _migration_order(path: Path) -> str:
    # db/apply-migrations.sh uses `LC_ALL=C sort` on complete filenames so
    # historical suffixed migrations (for example 095a) retain their real
    # execution order. Repository filenames are ASCII, so Python string order
    # is the same ordering here.
    return path.name


def _ops_status_definitions(migration: Path) -> tuple[str, ...]:
    sql = migration.read_text()
    definitions: list[str] = []
    cursor = 0
    while (signature_at := sql.find(_OPS_STATUS_SIGNATURE, cursor)) >= 0:
        body_start = _FUNCTION_BODY_START.search(sql, signature_at + len(_OPS_STATUS_SIGNATURE))
        assert body_start is not None, f"function body marker missing in {migration.name}"
        tag = body_start.group("tag")
        body_end = sql.find(f"{tag};", body_start.end())
        assert body_end >= 0, f"function body terminator missing in {migration.name}"
        definitions.append(sql[body_start.end() : body_end])
        cursor = body_end + len(tag) + 1
    return tuple(definitions)


def _latest_ops_status_definition() -> tuple[Path, str]:
    effective: tuple[Path, str] | None = None
    migrations = sorted((ROOT / "db/migrations").glob("*.sql"), key=_migration_order)
    for migration in migrations:
        for body in _ops_status_definitions(migration):
            effective = migration, body
    assert effective is not None, "fn_experiment_v2_ops_status has no migration definition"
    return effective


def _case_branches(case_sql: str) -> tuple[_CaseBranch, ...]:
    return tuple(
        _CaseBranch(condition=match.group("condition"), value=match.group("value"))
        for match in _CASE_BRANCH.finditer(case_sql)
    )


def _condition_key(condition: str) -> str:
    return re.sub(r"[\s()]", "", condition)


def _reason_is_covered_by_severity(reason: _CaseBranch, severity: _CaseBranch) -> bool:
    reason_condition = _condition_key(reason.condition)
    severity_condition = _condition_key(severity.condition)
    if reason_condition in severity_condition:
        return True

    # The critical CASE likewise folds the three observation faults under one
    # shared open-exposure predicate. A later OR sibling can sit between the
    # shared predicate and this reason's specific observation predicate.
    open_exposure_prefix = "coalesceexposures.open_count,0>0AND"
    if reason_condition.startswith(open_exposure_prefix):
        observation_fault = reason_condition.removeprefix(open_exposure_prefix)
        if open_exposure_prefix in severity_condition and observation_fault in severity_condition:
            return True

    # The critical branch intentionally folds three component-readiness faults
    # into `component_enabled AND NOT (baseline AND confirmation AND runtime)`.
    # Preserve that SQL relationship rather than assigning a severity in the
    # parametrized test data.
    enabled_prefix = "e.component_enabledAND"
    if not reason_condition.startswith(enabled_prefix) or "e.component_enabledANDNOT" not in severity_condition:
        return False
    failed_requirement = reason_condition.removeprefix(enabled_prefix)
    if failed_requirement.startswith("NOT"):
        required_condition = failed_requirement.removeprefix("NOT")
    elif failed_requirement.endswith("ISNULL"):
        required_condition = f"{failed_requirement.removesuffix('ISNULL')}ISNOTNULL"
    else:
        return False
    return required_condition in severity_condition


def _db_integrity_branches(function_body: str) -> tuple[tuple[str, str], ...]:
    match = _ALERT_CASES.search(function_body)
    assert match is not None, "ordered alert severity/reason CASE expressions not found"
    severity_branches = _case_branches(match.group("severity_case"))
    reason_branches = _case_branches(match.group("reason_case"))
    assert severity_branches, "alert severity CASE has no branches"
    assert reason_branches, "alert reason CASE has no branches"

    emitted: list[tuple[str, str]] = []
    for reason in reason_branches:
        matching_severities = tuple(
            severity.value for severity in severity_branches if _reason_is_covered_by_severity(reason, severity)
        )
        assert len(matching_severities) == 1, (
            f"reason branch {reason.value!r} maps to {matching_severities!r}; "
            "severity/reason CASE drift must be reconciled explicitly"
        )
        emitted.append((reason.value, matching_severities[0]))
    return tuple(emitted)


def _db_observation_truth_values(function_body: str) -> tuple[str, ...]:
    match = _OBSERVATION_TRUTH_CASE.search(function_body)
    assert match is not None, "observation-truth CASE expression not found"
    branches = tuple(branch.value for branch in _case_branches(match.group("truth_case")))
    assert "ELSE 'exact'" in match.group("truth_case")
    return (*branches, "exact")


EFFECTIVE_OPS_MIGRATION, EFFECTIVE_OPS_BODY = _latest_ops_status_definition()
DB_INTEGRITY_BRANCHES = _db_integrity_branches(EFFECTIVE_OPS_BODY)


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
    db_reason_branches = tuple(reason for reason, _severity in DB_INTEGRITY_BRANCHES)
    unique_db_reasons = tuple(dict.fromkeys(db_reason_branches))
    schema_reasons = get_args(ComponentExperimentIntegrityReason)

    assert schema_reasons == unique_db_reasons
    assert {
        "expired_work_not_terminal",
        "confirmed_baseline_recovery_missing",
    } <= set(schema_reasons)


def test_migration_observation_truth_and_typed_producer_stay_reconciled():
    assert get_args(ComponentExperimentObservationTruth) == _db_observation_truth_values(EFFECTIVE_OPS_BODY)


@pytest.mark.parametrize("observation_truth", get_args(ComponentExperimentObservationTruth))
def test_every_db_observation_truth_round_trips_the_typed_alert(observation_truth: str):
    payload = _load_alert_helper()(_ops_row(observation_truth=observation_truth))
    assert payload is not None
    envelope = AlertEnvelope.model_validate(payload)
    assert envelope.details["observation_truth"] == observation_truth


def test_alert_contract_comes_from_the_latest_ordered_migration_definition():
    definitions = [
        (migration, body)
        for migration in sorted((ROOT / "db/migrations").glob("*.sql"), key=_migration_order)
        for body in _ops_status_definitions(migration)
    ]
    assert definitions
    assert definitions[-1] == (EFFECTIVE_OPS_MIGRATION, EFFECTIVE_OPS_BODY)


def test_case_branch_parser_preserves_order_and_duplicate_outputs():
    branches = _case_branches(
        "CASE WHEN first_predicate THEN 'critical' "
        "WHEN second_predicate THEN 'critical' "
        "WHEN third_predicate THEN 'warning' ELSE NULL END"
    )
    assert tuple((branch.condition, branch.value) for branch in branches) == (
        ("first_predicate", "critical"),
        ("second_predicate", "critical"),
        ("third_predicate", "warning"),
    )


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


@pytest.mark.parametrize(
    ("reason", "severity"),
    DB_INTEGRITY_BRANCHES,
    ids=[f"branch-{index}-{reason}-{severity}" for index, (reason, severity) in enumerate(DB_INTEGRITY_BRANCHES)],
)
def test_every_db_integrity_branch_round_trips_instead_of_falling_back(reason: str, severity: str):
    payload = _load_alert_helper()(_ops_row(alert_reason=reason, alert_severity=severity))
    assert payload is not None
    envelope = AlertEnvelope.model_validate(payload)
    assert envelope.alert_type == "component_experiment_integrity"
    assert envelope.severity == severity
    assert envelope.details["reason"] == reason


def test_nominal_ops_row_does_not_page():
    payload = _load_alert_helper()(_ops_row(alert_severity=None, alert_reason=None, admission_state="closed"))
    assert payload is None
