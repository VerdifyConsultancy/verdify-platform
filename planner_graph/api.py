"""HTTP entrypoints for the planner service.

This module defines the public FastAPI routes that external callers hit for
health checks, run submission, and run inspection. It connects the outside
world to the planner service layer without exposing the internal graph directly.
"""

from __future__ import annotations

import json
from uuid import UUID, uuid4

from fastapi import APIRouter, FastAPI, HTTPException, Request, status

from planner_graph.contracts import (
    HealthResponse,
    MemoryIngestItem,
    MemoryIngestRejectionResponse,
    MemoryIngestRequest,
    MemoryIngestResponse,
    PlannerRunRequest,
    RunAccepted,
    RunStatusResponse,
)
from planner_graph.memory import MemoryIngestRejection, MemoryItem
from planner_graph.state import GRAPH_VERSION, PlannerState, utc_now

router = APIRouter()


def get_planner_service(request: Request):
    return request.app.state.planner_service


@router.get("/livez")
def livez() -> dict[str, object]:
    """Process-only liveness; worker/store truth belongs to /health readiness."""
    return {"live": True, "production_authority": "non-authoritative"}


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    planner_service = get_planner_service(request)
    worker_health = planner_service.worker.health()
    if not worker_health.alive or not worker_health.ready:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "service": "unavailable",
                "production_authority": "non-authoritative",
                "worker_alive": worker_health.alive,
                "worker_ready": worker_health.ready,
                "consecutive_store_failures": worker_health.consecutive_store_failures,
                "retry_delay_seconds": worker_health.retry_delay_seconds,
                "last_error_class": worker_health.last_error_class,
            },
        )
    checkpoint = (
        "postgres"
        if planner_service.settings.planner_store_backend == "postgres"
        else "in-memory"
    )
    openai_status = (
        "configured" if planner_service.runtime.openai.is_configured else "fallback"
    )
    return HealthResponse(
        checkpoint=checkpoint,
        openai=openai_status,
        worker="ready",
        db="ok",
        consecutive_store_failures=worker_health.consecutive_store_failures,
        retry_delay_seconds=worker_health.retry_delay_seconds,
        last_error_class=worker_health.last_error_class,
    )


@router.post(
    "/planner-runs",
    response_model=RunAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_planner_run(payload: PlannerRunRequest, request: Request) -> RunAccepted:
    planner_service = get_planner_service(request)
    trigger_id = payload.trigger.trigger_id
    initial_state: PlannerState = {
        "trigger_id": str(trigger_id),
        "greenhouse_id": payload.trigger.greenhouse_id,
        "event_type": payload.trigger.event_type,
        "event_label": payload.trigger.event_label or payload.trigger.event_type,
        "expected_action": payload.trigger.expected_action,
        "triggered_at": payload.trigger.triggered_at,
        "thread_id": str(trigger_id),
        "graph_version": GRAPH_VERSION,
        "planner_instance": payload.trigger.planner_instance
        or planner_service.settings.planner_instance,
        "run_mode": payload.planner.run_mode,
        "contract_version": payload.planner.contract_version,
        "context_version": payload.planner.context_version,
        "request_id": payload.planner.request_id or "",
        "trace_id": payload.planner.trace_id or "",
        "compare_against": payload.planner.compare_against or "",
        "source": payload.trigger.source or "",
        "status": "queued",
        "started_at": utc_now(),
        "updated_at": utc_now(),
        "errors": [],
        "warnings": [],
        "revision_count": 0,
        "climate_snapshot": payload.context.climate_snapshot,
        "scorecard_summary": payload.context.scorecard_summary,
        "forecast_summary": payload.context.forecast_summary,
        "active_plan_summary": payload.context.active_plan_summary,
        "alerts_summary": payload.context.alerts_summary,
        "clamp_summary": payload.context.clamp_summary,
        "guardrail_audit_summary": payload.context.guardrail_audit_summary,
        "recent_delivery_summary": payload.context.recent_delivery_summary,
        "operator_notes": payload.context.operator_notes,
        "retrieved_lessons": payload.context.retrieval_refs,
        "retrieved_docs": payload.context.site_refs,
        "retrieved_plan_refs": [],
    }
    if payload.trigger.due_by is not None:
        initial_state["due_by"] = payload.trigger.due_by
    queued = planner_service.worker.submit(trigger_id, initial_state)
    record = planner_service.repository.get(trigger_id)
    if record is None:
        raise HTTPException(status_code=500, detail="Run record missing after submit.")
    planner_service.runtime.planner_logger.log_submission(
        trigger_id=str(trigger_id),
        request_id=initial_state["request_id"],
        trace_id=initial_state["trace_id"],
        queued=queued,
        status=record.status,
        run_mode=record.run_mode,
    )
    return RunAccepted(
        trigger_id=trigger_id,
        thread_id=record.thread_id,
        status=record.status,
        queued=queued,
    )


@router.get(
    "/planner-runs/{trigger_id}",
    response_model=RunStatusResponse,
    response_model_exclude_none=True,
)
def get_run(trigger_id: UUID, request: Request) -> RunStatusResponse:
    planner_service = get_planner_service(request)
    record = planner_service.repository.get(trigger_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    planner_service.runtime.planner_logger.log_fetch(record)
    return record.response()


MAX_MEMORY_TITLE_CHARS = 160
MAX_MEMORY_SUMMARY_CHARS = 800
MAX_MEMORY_TAGS = 20
MAX_MEMORY_TAG_CHARS = 64
MAX_MEMORY_SOURCE_CHARS = 160
MAX_MEMORY_PAYLOAD_BYTES = 8000


def _validate_ingest_item(
    item: MemoryIngestItem,
    *,
    max_body_chars: int,
    allow_prior_plans: bool,
    allow_support_docs: bool,
) -> str | None:
    if item.memory_type == "prior_plan" and not allow_prior_plans:
        return "prior plan ingestion is disabled"
    if item.memory_type == "support_doc" and not allow_support_docs:
        return "support document ingestion is disabled"
    if item.trust_level == "planner_inferred":
        return "Verdify ingestion may not submit planner_inferred trust_level"
    if not item.source_type.strip() or len(item.source_type) > MAX_MEMORY_SOURCE_CHARS:
        return "source_type is required and must be bounded"
    if item.source_id is not None and len(item.source_id) > MAX_MEMORY_SOURCE_CHARS:
        return "source_id is too long"
    if not item.title.strip() or len(item.title) > MAX_MEMORY_TITLE_CHARS:
        return "title is required and must be bounded"
    if not item.summary.strip() or len(item.summary) > MAX_MEMORY_SUMMARY_CHARS:
        return "summary is required and must be bounded"
    if not item.body.strip() or len(item.body) > max_body_chars:
        return f"body is required and must be at most {max_body_chars} characters"
    if len(item.tags) > MAX_MEMORY_TAGS:
        return f"tags must contain at most {MAX_MEMORY_TAGS} entries"
    if any(not tag.strip() or len(tag) > MAX_MEMORY_TAG_CHARS for tag in item.tags):
        return "tags must be non-empty and bounded"
    if item.importance < 1 or item.importance > 5:
        return "importance must be between 1 and 5"
    if item.confidence is not None and (item.confidence < 0 or item.confidence > 1):
        return "confidence must be between 0 and 1"
    if (
        item.valid_from is not None
        and item.expires_at is not None
        and item.expires_at <= item.valid_from
    ):
        return "expires_at must be after valid_from"
    payload_bytes = len(
        json.dumps(item.payload, sort_keys=True, default=str).encode("utf-8")
    )
    if payload_bytes > MAX_MEMORY_PAYLOAD_BYTES:
        return f"payload must be at most {MAX_MEMORY_PAYLOAD_BYTES} bytes"
    return None


def _to_memory_item(greenhouse_id: str, item: MemoryIngestItem) -> MemoryItem:
    return MemoryItem(
        memory_id=uuid4(),
        greenhouse_id=greenhouse_id,
        memory_type=item.memory_type,
        title=item.title.strip(),
        summary=item.summary.strip(),
        snippet=item.body.strip(),
        source_type=item.source_type.strip(),
        source_id=item.source_id.strip() if isinstance(item.source_id, str) else None,
        trigger_id=str(item.trigger_id) if item.trigger_id is not None else None,
        event_type=item.event_type.strip()
        if isinstance(item.event_type, str)
        else None,
        tags=tuple(tag.strip() for tag in item.tags),
        importance=item.importance,
        confidence=item.confidence,
        trust_level=item.trust_level,
        payload=item.payload,
        valid_from=item.valid_from.isoformat() if item.valid_from is not None else None,
        expires_at=item.expires_at.isoformat() if item.expires_at is not None else None,
    )


@router.post("/planner-memory/ingest", response_model=MemoryIngestResponse)
def ingest_planner_memory(
    payload: MemoryIngestRequest, request: Request
) -> MemoryIngestResponse:
    planner_service = get_planner_service(request)
    settings = planner_service.settings
    if not settings.planner_memory_ingest_enabled:
        raise HTTPException(
            status_code=404, detail="Planner memory ingestion is disabled."
        )
    if settings.planner_memory_backend == "disabled":
        raise HTTPException(
            status_code=503, detail="Planner memory backend is not active."
        )
    if len(payload.items) > settings.planner_memory_ingest_max_items:
        return MemoryIngestResponse(
            accepted_count=0,
            rejected_count=len(payload.items),
            duplicate_count=0,
            accepted_ids=[],
            rejections=[
                MemoryIngestRejectionResponse(
                    index=index,
                    reason=(
                        "batch contains "
                        f"{len(payload.items)} items; max is {settings.planner_memory_ingest_max_items}"
                    ),
                )
                for index, _ in enumerate(payload.items)
            ],
            batch_id=payload.batch_id,
        )

    accepted_items: list[MemoryItem] = []
    rejections: list[MemoryIngestRejection] = []
    for index, item in enumerate(payload.items):
        reason = _validate_ingest_item(
            item,
            max_body_chars=settings.planner_memory_ingest_max_body_chars,
            allow_prior_plans=settings.planner_memory_ingest_allow_verdify_prior_plans,
            allow_support_docs=settings.planner_memory_ingest_allow_support_docs,
        )
        if reason is not None:
            rejections.append(MemoryIngestRejection(index=index, reason=reason))
            continue
        accepted_items.append(_to_memory_item(payload.greenhouse_id, item))

    ingest_result = (
        planner_service.runtime.memory.ingest_items(accepted_items)
        if accepted_items
        else None
    )
    store_rejections = ingest_result.rejections if ingest_result is not None else []
    all_rejections = [*rejections, *store_rejections]
    return MemoryIngestResponse(
        accepted_count=ingest_result.accepted_count if ingest_result is not None else 0,
        rejected_count=len(all_rejections),
        duplicate_count=ingest_result.duplicate_count
        if ingest_result is not None
        else 0,
        accepted_ids=ingest_result.accepted_ids if ingest_result is not None else [],
        rejections=[
            MemoryIngestRejectionResponse(
                index=rejection.index, reason=rejection.reason
            )
            for rejection in all_rejections
        ],
        batch_id=payload.batch_id,
    )


def include_api(app: FastAPI) -> None:
    app.include_router(router)
