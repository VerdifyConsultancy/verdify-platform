"""Internal state and status definitions for planner runs.

This module defines the bounded state shape that travels through the planner
graph and the enums/literals around run status. It connects every node, store,
and API projection to a shared understanding of planner state.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from typing_extensions import TypedDict

RunMode = Literal["production"]
PlannerStatus = Literal["queued", "running", "completed", "failed"]
SelectedAction = Literal["set_plan", "set_tunable", "acknowledge_trigger", "fail"]

GRAPH_VERSION = "climate-intent-v1"
PROTECTED_MCP_TOOLS = {
    "set_plan",
    "set_tunable",
    "acknowledge_trigger",
    "plan_evaluate",
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class PlannerState(TypedDict, total=False):
    trigger_id: str
    greenhouse_id: str
    event_type: str
    event_label: str
    expected_action: str
    triggered_at: str
    due_by: str
    source: str
    planner_instance: str
    thread_id: str
    graph_version: str
    run_mode: RunMode
    contract_version: str
    context_version: str
    request_id: str
    trace_id: str
    compare_against: str
    status: str
    current_step: str
    started_at: str
    updated_at: str
    errors: list[str]
    warnings: list[str]
    revision_count: int
    context_digest: str
    context_sections: list[str]
    context_completeness: str
    context_weaknesses: list[str]
    climate_snapshot: dict[str, str | float | int | bool]
    scorecard_summary: dict[str, str | float | int | bool]
    forecast_summary: dict[str, str | float | int | bool]
    active_plan_summary: dict[str, str | float | int | bool]
    alerts_summary: list[str]
    clamp_summary: dict[str, str | float | int | bool]
    guardrail_audit_summary: dict[str, str | float | int | bool]
    recent_delivery_summary: dict[str, str | float | int | bool]
    operator_notes: list[str]
    retrieval_queries: list[str]
    retrieved_lessons: list[dict[str, str]]
    retrieved_docs: list[dict[str, str]]
    retrieved_plan_refs: list[dict[str, str]]
    diagnosis: dict[str, str | list[str]]
    draft_plan: dict[str, str | int | float | dict[str, float] | list[str] | bool]
    draft_action: str
    draft_rationale: str
    validation_status: str
    validation_errors: list[str]
    contract_shape_rejection_reasons: list[str]
    registry_violations: list[str]
    band_ownership_violations: list[str]
    tier1_coverage_status: str
    guardrail_preview: dict[str, str | bool | list[str]]
    guardrail_reasons: list[str]
    guardrail_outcome: str
    expected_clamps: list[str]
    hold_risk: str
    transition_audit_refs: list[str]
    selected_action: SelectedAction
    proposed_payload: dict[str, object]
    proposed_rationale: str
    proposed_confidence: float
    expected_effect: str
    mcp_request: dict[str, str | dict[str, float]]
    mcp_result: dict[str, str | bool]
    plan_id: str
    tunable_changes: dict[str, float]
    delivery_status: str
    readback_status: str
    slack_report: dict[str, str | bool]
    terminal_status: str
    action_choice_reason: str
    proposal_decision_summary: str
    revision_reason: str
    fail_closed_reason: str
