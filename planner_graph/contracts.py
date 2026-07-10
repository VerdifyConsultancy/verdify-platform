"""External request and response models for the planner API.

This module is the public wire contract for callers that talk to the planner
over HTTP. It connects the outside integration boundary to the internal planner
state by validating what may come in and what must go back out.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from planner_graph.state import PlannerStatus, RunMode


class TriggerEnvelope(BaseModel):
    trigger_id: UUID
    greenhouse_id: str
    event_type: str
    event_label: str | None = None
    expected_action: Literal[
        "set_plan", "set_tunable", "acknowledge_trigger", "any"
    ] = "any"
    triggered_at: str
    due_by: str | None = None
    planner_instance: str | None = None
    source: str | None = None


class PlannerRequestMetadata(BaseModel):
    run_mode: Literal["production"] = "production"
    contract_version: str
    context_version: str
    request_id: str | None = None
    trace_id: str | None = None
    compare_against: str | None = None


class PlannerContextPack(BaseModel):
    climate_snapshot: dict[str, str | float | int | bool]
    scorecard_summary: dict[str, str | float | int | bool]
    forecast_summary: dict[str, str | float | int | bool]
    active_plan_summary: dict[str, str | float | int | bool]
    alerts_summary: list[str]
    clamp_summary: dict[str, str | float | int | bool]
    guardrail_audit_summary: dict[str, str | float | int | bool]
    retrieval_refs: list[dict[str, str]] = Field(default_factory=list)
    recent_delivery_summary: dict[str, str | float | int | bool] = Field(
        default_factory=dict
    )
    operator_notes: list[str] = Field(default_factory=list)
    site_refs: list[dict[str, str]] = Field(default_factory=list)


class PlannerRunRequest(BaseModel):
    trigger: TriggerEnvelope
    planner: PlannerRequestMetadata
    context: PlannerContextPack


class RunAccepted(BaseModel):
    trigger_id: UUID
    thread_id: UUID
    status: str
    run_mode: RunMode = "production"
    queued: bool


class DiagnosisResponse(BaseModel):
    situation: str
    likely_cause: str
    risks: list[str]
    planning_intent: str


class PrimaryActionResponse(BaseModel):
    action_type: str
    payload: dict[str, object]
    rationale: str
    confidence: float
    expected_effect: str | None = None
    alternatives: list[dict[str, object]] = Field(default_factory=list)


class ValidationSummaryResponse(BaseModel):
    validation_status: str | None = None
    validation_errors: list[str] = Field(default_factory=list)
    registry_violations: list[str] = Field(default_factory=list)
    band_ownership_violations: list[str] = Field(default_factory=list)
    tier1_coverage_status: str | None = None


class GuardrailPreviewResponse(BaseModel):
    would_clamp: bool | None = None
    summary: str | None = None
    expected_clamps: list[str] = Field(default_factory=list)
    hold_risk: str | None = None
    transition_audit_refs: list[str] = Field(default_factory=list)


class PlannerMetadataResponse(BaseModel):
    contract_version: str | None = None
    context_version: str | None = None
    planner_graph_version: str | None = None
    run_mode: RunMode


class RunStatusResponse(BaseModel):
    trigger_id: UUID
    thread_id: UUID
    status: PlannerStatus
    run_mode: RunMode
    current_step: str | None = None
    terminal_status: str | None = None
    execution_owner: str | None = None
    last_error: str | None = None
    updated_at: str
    diagnosis: DiagnosisResponse | None = None
    primary_action: PrimaryActionResponse | None = None
    validation_summary: ValidationSummaryResponse = Field(
        default_factory=ValidationSummaryResponse
    )
    guardrail_preview: GuardrailPreviewResponse = Field(
        default_factory=GuardrailPreviewResponse
    )
    planner_metadata: PlannerMetadataResponse
    state: dict[str, object] = Field(default_factory=dict)


class HealthResponse(BaseModel):
    service: str = "ok"
    private_api: bool = True
    default_run_mode: RunMode = "production"
    worker: str = "ok"
    db: str = "ok"
    openai: str = "fallback"
    mcp: str = "verdify-executes"
    checkpoint: str = "durable-run-store"
    production_authority: Literal["non-authoritative"] = "non-authoritative"
    consecutive_store_failures: int = 0
    retry_delay_seconds: float = 0.0
    last_error_class: str | None = None


class MemoryIngestItem(BaseModel):
    memory_type: Literal["lesson", "support_doc", "prior_plan", "observed_outcome"]
    source_type: str
    source_id: str | None = None
    trigger_id: UUID | None = None
    event_type: str | None = None
    title: str
    summary: str
    body: str
    tags: list[str] = Field(default_factory=list)
    importance: int = 3
    confidence: float | None = None
    trust_level: Literal["observed_outcome", "verdify_context", "planner_inferred"] = (
        "verdify_context"
    )
    payload: dict[str, object] = Field(default_factory=dict)
    valid_from: datetime | None = None
    expires_at: datetime | None = None


class MemoryIngestRequest(BaseModel):
    greenhouse_id: str
    source_system: Literal["verdify"]
    batch_id: str
    items: list[MemoryIngestItem]


class MemoryIngestRejectionResponse(BaseModel):
    index: int
    reason: str


class MemoryIngestResponse(BaseModel):
    accepted_count: int
    rejected_count: int
    duplicate_count: int
    accepted_ids: list[UUID] = Field(default_factory=list)
    rejections: list[MemoryIngestRejectionResponse] = Field(default_factory=list)
    batch_id: str
