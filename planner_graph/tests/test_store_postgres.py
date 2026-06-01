"""Tests for the Postgres-backed run store.

This file exercises the durable storage backend under realistic lifecycle cases
like duplicate submission, completion, and lease reclaim. It connects planner
persistence guarantees to integration-style verification.
"""

from __future__ import annotations

import time
from typing import cast
from uuid import UUID, uuid4

from planner_graph.state import PlannerState, utc_now
from planner_graph.store import PostgresRunStore
from tests.helpers import tier1_active_plan_summary


def sample_state(trigger_id: UUID, *, event_type: str = "SUNRISE") -> PlannerState:
    return {
        "trigger_id": str(trigger_id),
        "thread_id": str(trigger_id),
        "greenhouse_id": "vallery",
        "event_type": event_type,
        "event_label": event_type,
        "expected_action": "set_plan",
        "triggered_at": "2026-05-19T06:00:00-06:00",
        "planner_instance": "planner_graph",
        "run_mode": "production",
        "contract_version": "2026-05-24",
        "context_version": "v1",
        "status": "queued",
        "started_at": utc_now(),
        "updated_at": utc_now(),
        "errors": [],
        "warnings": [],
        "revision_count": 0,
        "climate_snapshot": {"temp_f": 72.5, "vpd_kpa": 1.1, "rh_pct": 60},
        "scorecard_summary": {"planner_score": 80.0},
        "forecast_summary": {"headline": "Hot and dry afternoon expected"},
        "active_plan_summary": tier1_active_plan_summary(),
        "alerts_summary": ["warning: no blocking alerts"],
        "clamp_summary": {"active_clamps_24h": 0},
        "guardrail_audit_summary": {"readback_freshness_seconds": 45},
    }


def test_postgres_store_persists_run_lifecycle(postgres_dsn: str) -> None:
    store = PostgresRunStore(postgres_dsn)
    store.initialize()
    trigger_id = uuid4()
    initial_state = sample_state(trigger_id)

    created, should_enqueue = store.create_or_resume(trigger_id, initial_state)

    assert should_enqueue is True
    assert created.status == "queued"

    claimed = store.claim_next("worker-1", lease_seconds=30)

    assert claimed is not None
    assert claimed.trigger_id == trigger_id
    assert claimed.status == "running"
    assert claimed.execution_owner == "worker-1"

    final_state = {
        **initial_state,
        "status": "completed",
        "current_step": "report",
        "terminal_status": "proposal_ready",
        "selected_action": "set_plan",
        "proposed_payload": {"trigger_id": str(trigger_id)},
        "proposed_rationale": "Dry peak stress expected.",
        "proposed_confidence": 0.88,
        "updated_at": utc_now(),
    }
    completed = store.mark_completed(
        trigger_id, cast(PlannerState, final_state), "worker-1"
    )

    assert completed.status == "completed"
    assert completed.terminal_status == "proposal_ready"
    assert completed.current_step == "report"
    assert completed.state.get("selected_action") == "set_plan"


def test_postgres_store_does_not_requeue_running_or_completed_duplicates(
    postgres_dsn: str,
) -> None:
    store = PostgresRunStore(postgres_dsn)
    store.initialize()
    trigger_id = uuid4()
    initial_state = sample_state(trigger_id)

    store.create_or_resume(trigger_id, initial_state)
    claimed = store.claim_next("worker-1", lease_seconds=30)

    assert claimed is not None

    running_record, running_enqueue = store.create_or_resume(
        trigger_id, sample_state(trigger_id, event_type="SUNSET")
    )

    assert running_enqueue is False
    assert running_record.status == "running"
    assert running_record.submission_count == 2

    final_state = {
        **initial_state,
        "status": "completed",
        "current_step": "report",
        "terminal_status": "proposal_ready",
        "updated_at": utc_now(),
    }
    store.mark_completed(trigger_id, cast(PlannerState, final_state), "worker-1")

    completed_record, completed_enqueue = store.create_or_resume(
        trigger_id, sample_state(trigger_id, event_type="MIDNIGHT")
    )

    assert completed_enqueue is False
    assert completed_record.status == "completed"
    assert completed_record.submission_count == 3


def test_postgres_store_requeues_failed_runs_with_new_state(postgres_dsn: str) -> None:
    store = PostgresRunStore(postgres_dsn)
    store.initialize()
    trigger_id = uuid4()
    initial_state = sample_state(trigger_id)

    store.create_or_resume(trigger_id, initial_state)
    claimed = store.claim_next("worker-1", lease_seconds=30)

    assert claimed is not None

    failed = store.mark_failed(trigger_id, RuntimeError("boom"), "worker-1")

    assert failed.status == "failed"
    assert failed.last_error == "boom"

    replacement_state = sample_state(trigger_id, event_type="RECOVERY")
    retried, should_enqueue = store.create_or_resume(trigger_id, replacement_state)

    assert should_enqueue is True
    assert retried.status == "queued"
    assert retried.state.get("event_type") == "RECOVERY"
    assert retried.submission_count == 2


def test_postgres_store_reclaims_expired_leases(postgres_dsn: str) -> None:
    store = PostgresRunStore(postgres_dsn)
    store.initialize()
    trigger_id = uuid4()
    initial_state = sample_state(trigger_id)

    store.create_or_resume(trigger_id, initial_state)
    first_claim = store.claim_next("worker-1", lease_seconds=1)

    assert first_claim is not None
    assert first_claim.execution_owner == "worker-1"

    time.sleep(1.2)

    reclaimed = store.claim_next("worker-2", lease_seconds=30)

    assert reclaimed is not None
    assert reclaimed.trigger_id == trigger_id
    assert reclaimed.status == "running"
    assert reclaimed.execution_owner == "worker-2"
