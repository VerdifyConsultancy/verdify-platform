"""Tests for planner memory retrieval and persistence seams."""

from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from planner_graph.app import PlannerService, create_app
from planner_graph.config import AppSettings
from planner_graph.memory import (
    DisabledMemoryStore,
    InMemoryMemoryStore,
    MemoryItem,
    MemoryQuery,
)
from planner_graph.nodes.persist_memory import build_persist_memory
from planner_graph.nodes.retrieve_memory import build_retrieve_memory
from planner_graph.runtime import PlannerRuntime
from planner_graph.state import PlannerState


def sample_state() -> PlannerState:
    return {
        "trigger_id": str(uuid4()),
        "greenhouse_id": "vallery",
        "event_type": "SUNRISE",
        "planner_instance": "planner_graph",
        "retrieved_lessons": [
            {"id": "verdify-lesson", "snippet": "Verdify says stay conservative."}
        ],
        "retrieved_docs": [
            {"id": "verdify-doc", "snippet": "Verdify sent a support doc."}
        ],
        "retrieved_plan_refs": [
            {"id": "verdify-plan", "snippet": "Previous Verdify plan ref."}
        ],
        "warnings": [],
        "selected_action": "set_plan",
        "proposed_payload": {"plan_id": "plan-1"},
        "proposed_rationale": "Reason",
        "proposed_confidence": 0.8,
        "expected_effect": "Lower heat stress",
        "validation_status": "passed",
        "guardrail_outcome": "pass",
        "diagnosis": {"planning_intent": "Bias cool"},
    }


def test_retrieve_memory_disabled_preserves_existing_refs() -> None:
    runtime = PlannerRuntime(
        settings=AppSettings(planner_memory_backend="disabled"),
        memory=DisabledMemoryStore(),
    )

    next_state = build_retrieve_memory(runtime)(sample_state())

    assert next_state.get("retrieved_lessons") == [
        {"id": "verdify-lesson", "snippet": "Verdify says stay conservative."}
    ]
    assert next_state.get("retrieved_docs") == [
        {"id": "verdify-doc", "snippet": "Verdify sent a support doc."}
    ]
    assert next_state.get("retrieved_plan_refs") == [
        {"id": "verdify-plan", "snippet": "Previous Verdify plan ref."}
    ]


def test_retrieve_memory_merges_in_memory_results_with_existing_refs() -> None:
    memory = InMemoryMemoryStore(
        seed_items=[
            MemoryItem(
                memory_id=uuid4(),
                greenhouse_id="vallery",
                memory_type="lesson",
                title="Lesson",
                summary="Planner runs must not write.",
                snippet="Planner runs must not write.",
                source_type="seed",
                event_type="SUNRISE",
            ),
            MemoryItem(
                memory_id=uuid4(),
                greenhouse_id="vallery",
                memory_type="support_doc",
                title="Doc",
                summary="Sunrise plans should bias for midday stress.",
                snippet="Sunrise plans should bias for midday stress.",
                source_type="seed",
                event_type="SUNRISE",
            ),
            MemoryItem(
                memory_id=uuid4(),
                greenhouse_id="vallery",
                memory_type="prior_plan",
                title="Plan",
                summary="Ack-only rehearsal plan.",
                snippet="Ack-only rehearsal plan.",
                source_type="seed",
                event_type="SUNRISE",
            ),
        ]
    )
    runtime = PlannerRuntime(
        settings=AppSettings(
            planner_memory_backend="memory",
            planner_memory_top_k=3,
            planner_memory_max_snippet_chars=80,
        ),
        memory=memory,
    )

    next_state = build_retrieve_memory(runtime)(sample_state())

    assert (
        next_state.get("retrieved_lessons", [])[0]["snippet"]
        == "Planner runs must not write."
    )
    assert next_state.get("retrieved_lessons", [])[1]["id"] == "verdify-lesson"
    assert (
        next_state.get("retrieved_docs", [])[0]["snippet"]
        == "Sunrise plans should bias for midday stress."
    )
    assert (
        next_state.get("retrieved_plan_refs", [])[0]["snippet"]
        == "Ack-only rehearsal plan."
    )


def test_retrieve_memory_failure_is_non_blocking() -> None:
    class FailingMemoryStore:
        def initialize(self) -> None:
            return None

        def retrieve(self, query):  # type: ignore[no-untyped-def]
            del query
            raise RuntimeError("memory db down")

        def persist_run_summary(self, state):  # type: ignore[no-untyped-def]
            del state
            return None

    runtime = PlannerRuntime(
        settings=AppSettings(planner_memory_backend="memory"),
        memory=FailingMemoryStore(),  # type: ignore[arg-type]
    )

    next_state = build_retrieve_memory(runtime)(sample_state())

    assert next_state.get("retrieved_lessons", [])[0]["id"] == "verdify-lesson"
    assert next_state.get("warnings") == [
        "memory retrieval unavailable: memory db down"
    ]


def test_persist_memory_failure_is_non_blocking() -> None:
    class FailingMemoryStore:
        def initialize(self) -> None:
            return None

        def retrieve(self, query):  # type: ignore[no-untyped-def]
            del query
            return None

        def persist_run_summary(self, state):  # type: ignore[no-untyped-def]
            del state
            raise RuntimeError("persist failed")

    runtime = PlannerRuntime(
        settings=AppSettings(
            planner_memory_backend="memory",
            planner_memory_persist_run_summaries=True,
        ),
        memory=FailingMemoryStore(),  # type: ignore[arg-type]
    )

    next_state = build_persist_memory(runtime)(sample_state())

    assert next_state.get("warnings") == [
        "memory persistence unavailable: persist failed"
    ]


def memory_ingest_payload(
    *, body: str = "Watch dry sunrise ramps before midday VPD stress."
) -> dict[str, object]:
    return {
        "greenhouse_id": "vallery",
        "source_system": "verdify",
        "batch_id": "batch-1",
        "items": [
            {
                "memory_type": "lesson",
                "source_type": "verdify_lesson",
                "source_id": "lesson-1",
                "trigger_id": None,
                "event_type": "SUNRISE",
                "title": "Dry sunrise lesson",
                "summary": "Dry sunrise ramps can need conservative midday planning.",
                "body": body,
                "tags": ["sunrise", "vpd"],
                "importance": 4,
                "confidence": 0.8,
                "trust_level": "verdify_context",
                "payload": {"origin": "test"},
                "valid_from": None,
                "expires_at": None,
            }
        ],
    }


def test_memory_ingest_endpoint_persists_in_memory_items() -> None:
    settings = AppSettings(
        planner_memory_backend="memory",
        planner_memory_ingest_enabled=True,
    )
    service = PlannerService(settings=settings)

    with TestClient(create_app(service=service)) as client:
        response = client.post("/planner-memory/ingest", json=memory_ingest_payload())

    assert response.status_code == 200
    payload = response.json()
    assert payload["accepted_count"] == 1
    assert payload["rejected_count"] == 0
    assert payload["duplicate_count"] == 0
    assert payload["batch_id"] == "batch-1"
    assert payload["accepted_ids"]

    result = service.runtime.memory.retrieve(
        MemoryQuery(
            greenhouse_id="vallery",
            event_type="SUNRISE",
            query_text="dry sunrise",
            top_k=3,
        )
    )
    assert result.lessons[0].title == "Dry sunrise lesson"


def test_memory_ingest_endpoint_is_idempotent_for_duplicates() -> None:
    settings = AppSettings(
        planner_memory_backend="memory",
        planner_memory_ingest_enabled=True,
    )
    service = PlannerService(settings=settings)

    with TestClient(create_app(service=service)) as client:
        first = client.post("/planner-memory/ingest", json=memory_ingest_payload())
        second = client.post("/planner-memory/ingest", json=memory_ingest_payload())

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["accepted_count"] == 1
    assert second.json()["accepted_count"] == 0
    assert second.json()["duplicate_count"] == 1
    assert second.json()["accepted_ids"] == first.json()["accepted_ids"]


def test_memory_ingest_rejects_oversized_body() -> None:
    settings = AppSettings(
        planner_memory_backend="memory",
        planner_memory_ingest_enabled=True,
        planner_memory_ingest_max_body_chars=12,
    )
    service = PlannerService(settings=settings)

    with TestClient(create_app(service=service)) as client:
        response = client.post(
            "/planner-memory/ingest", json=memory_ingest_payload(body="x" * 13)
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["accepted_count"] == 0
    assert payload["rejected_count"] == 1
    assert payload["rejections"] == [
        {"index": 0, "reason": "body is required and must be at most 12 characters"}
    ]


def test_memory_ingest_endpoint_disabled_returns_404() -> None:
    settings = AppSettings(
        planner_memory_backend="memory",
        planner_memory_ingest_enabled=False,
    )
    service = PlannerService(settings=settings)

    with TestClient(create_app(service=service)) as client:
        response = client.post("/planner-memory/ingest", json=memory_ingest_payload())

    assert response.status_code == 404
    assert response.json()["detail"] == "Planner memory ingestion is disabled."


def test_memory_ingest_backend_disabled_returns_clear_error() -> None:
    settings = AppSettings(
        planner_memory_backend="disabled",
        planner_memory_ingest_enabled=True,
    )
    service = PlannerService(settings=settings)

    with TestClient(create_app(service=service)) as client:
        response = client.post("/planner-memory/ingest", json=memory_ingest_payload())

    assert response.status_code == 503
    assert response.json()["detail"] == "Planner memory backend is not active."
