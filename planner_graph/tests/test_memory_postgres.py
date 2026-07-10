"""Postgres integration tests for planner-owned memory."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import psycopg

from planner_graph.memory import MemoryItem, MemoryQuery, PostgresMemoryStore
from planner_graph.state import PlannerState

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "001_planner_memory.sql"
)


def completed_state(trigger_id: UUID, *, confidence: float = 0.8) -> PlannerState:
    return {
        "trigger_id": str(trigger_id),
        "greenhouse_id": "vallery",
        "event_type": "SUNRISE",
        "selected_action": "set_plan",
        "plan_id": "iris-20260519-0600",
        "proposed_payload": {
            "plan_id": "iris-20260519-0600",
            "trigger_id": str(trigger_id),
        },
        "proposed_rationale": "Use prior dry sunrise plan as a bounded reference.",
        "proposed_confidence": confidence,
        "expected_effect": "Reduce midday VPD stress without bypassing Verdify validation.",
        "validation_status": "passed",
        "guardrail_outcome": "pass",
        "diagnosis": {"planning_intent": "Prepare for dry midday stress."},
    }


def test_postgres_memory_store_schema_has_production_indexes_and_constraints(
    postgres_dsn: str,
) -> None:
    store = PostgresMemoryStore(postgres_dsn)
    store.initialize()

    with psycopg.connect(postgres_dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT indexname
                  FROM pg_indexes
                 WHERE schemaname = 'public'
                   AND tablename IN ('planner_memory_items', 'planner_memory_retrievals')
                """
            )
            indexes = {row[0] for row in cur.fetchall()}
            cur.execute(
                """
                SELECT conname
                  FROM pg_constraint
                  JOIN pg_class ON pg_class.oid = pg_constraint.conrelid
                 WHERE pg_class.relname = 'planner_memory_items'
                """
            )
            constraints = {row[0] for row in cur.fetchall()}

    assert "planner_memory_items_unique_content_idx" in indexes
    assert "planner_memory_items_unique_source_idx" in indexes
    assert "planner_memory_items_lookup_idx" in indexes
    assert "planner_memory_items_search_idx" in indexes
    assert "planner_memory_retrievals_trigger_idx" in indexes
    assert "planner_memory_retrievals_greenhouse_idx" in indexes
    assert "planner_memory_items_importance_check" in constraints
    assert "planner_memory_items_confidence_check" in constraints
    assert "planner_memory_items_type_check" in constraints
    assert "planner_memory_items_trust_level_check" in constraints
    assert "planner_memory_items_valid_window_check" in constraints


def test_planner_memory_migration_file_applies_idempotently(postgres_dsn: str) -> None:
    migration_sql = MIGRATION_PATH.read_text(encoding="utf-8")

    with psycopg.connect(postgres_dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(cast(Any, migration_sql))
            cur.execute(cast(Any, migration_sql))
        conn.commit()

    with psycopg.connect(postgres_dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT to_regclass('planner_memory_items'), to_regclass('planner_memory_retrievals')"
            )
            row = cur.fetchone()

    assert row == ("planner_memory_items", "planner_memory_retrievals")


def test_postgres_memory_store_persists_idempotent_run_summary_and_audits_retrieval(
    postgres_dsn: str,
) -> None:
    store = PostgresMemoryStore(postgres_dsn)
    store.initialize()
    trigger_id = uuid4()

    first = store.persist_run_summary(completed_state(trigger_id, confidence=0.7))
    second = store.persist_run_summary(completed_state(trigger_id, confidence=0.9))

    assert first is not None
    assert second is not None
    assert second.memory_id == first.memory_id
    assert second.confidence == 0.9

    result = store.retrieve(
        MemoryQuery(
            greenhouse_id="vallery",
            event_type="SUNRISE",
            query_text="dry sunrise plan",
            top_k=2,
            trigger_id=str(trigger_id),
        )
    )

    assert result.plan_refs
    assert result.plan_refs[0].memory_id == first.memory_id
    assert result.plan_refs[0].snippet.startswith("Planner selected set_plan")

    with psycopg.connect(postgres_dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*), max(last_used_at) IS NOT NULL FROM planner_memory_items WHERE source_id = %s",
                (str(trigger_id),),
            )
            row = cur.fetchone()
            cur.execute(
                "SELECT trigger_id, result_ids FROM planner_memory_retrievals ORDER BY created_at DESC LIMIT 1"
            )
            retrieval = cur.fetchone()

    assert row == (1, True)
    assert retrieval is not None
    assert retrieval[0] == trigger_id
    assert first.memory_id in retrieval[1]


def test_postgres_memory_store_ingests_items_idempotently(postgres_dsn: str) -> None:
    store = PostgresMemoryStore(postgres_dsn)
    store.initialize()
    item = MemoryItem(
        memory_id=uuid4(),
        greenhouse_id="vallery",
        memory_type="observed_outcome",
        title="Observed dry sunrise outcome",
        summary="Verdify observed improved VPD recovery after a dry sunrise plan.",
        snippet="Observed outcome should be available as planner memory.",
        source_type="verdify_outcome",
        source_id="outcome-1",
        event_type="SUNRISE",
        tags=("sunrise", "observed"),
        importance=5,
        confidence=0.9,
        trust_level="observed_outcome",
        payload={"metric": "vpd_recovery"},
    )

    first = store.ingest_items([item])
    second = store.ingest_items([item])

    assert first.accepted_count == 1
    assert first.duplicate_count == 0
    assert second.accepted_count == 0
    assert second.duplicate_count == 1
    assert second.accepted_ids == first.accepted_ids

    result = store.retrieve(
        MemoryQuery(
            greenhouse_id="vallery",
            event_type="SUNRISE",
            query_text="dry sunrise outcome",
            top_k=2,
        )
    )

    assert result.lessons
    assert result.lessons[0].memory_type == "observed_outcome"
