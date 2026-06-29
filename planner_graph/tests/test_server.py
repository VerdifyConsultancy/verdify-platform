"""Tests for runtime startup rules and structured logging.

This file checks service-level behavior such as environment gating and emitted
log fields rather than graph reasoning itself. It connects operational safety
and observability expectations to automated coverage.
"""

from __future__ import annotations

import pytest

from planner_graph.app import PlannerService
from planner_graph.config import AppSettings
from planner_graph.runtime import ExecutionHooks
from planner_graph.server import port_from_env


def test_port_from_env_defaults_to_cloud_run_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PORT", raising=False)

    assert port_from_env() == 8080


def test_port_from_env_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORT", "9090")

    assert port_from_env() == 9090


def test_port_from_env_rejects_invalid_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORT", "abc")

    with pytest.raises(ValueError, match="PORT must be an integer"):
        port_from_env()


def test_in_memory_store_is_allowed_in_development() -> None:
    settings = AppSettings(app_env="development", planner_store_backend="memory")

    service = PlannerService(settings=settings)

    assert service.settings.planner_store_backend == "memory"


def test_in_memory_store_is_rejected_outside_development() -> None:
    settings = AppSettings(app_env="production", planner_store_backend="memory")

    with pytest.raises(RuntimeError, match="InMemoryRunStore is only allowed"):
        PlannerService(settings=settings)


def test_postgres_store_requires_dsn_in_non_development() -> None:
    settings = AppSettings(
        app_env="production", planner_store_backend="postgres", planner_db_dsn=None
    )

    with pytest.raises(RuntimeError, match="PLANNER_STORE_BACKEND=postgres requires"):
        PlannerService(settings=settings)


def test_postgres_memory_store_requires_dsn_when_enabled() -> None:
    settings = AppSettings(
        app_env="development",
        planner_memory_backend="postgres",
        planner_memory_db_dsn=None,
        planner_db_dsn=None,
    )

    with pytest.raises(RuntimeError, match="PLANNER_MEMORY_BACKEND=postgres requires"):
        PlannerService(settings=settings)


def test_memory_ingest_settings_read_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PLANNER_MEMORY_INGEST_ENABLED", "true")
    monkeypatch.setenv("PLANNER_MEMORY_INGEST_MAX_ITEMS", "25")
    monkeypatch.setenv("PLANNER_MEMORY_INGEST_MAX_BODY_CHARS", "900")
    monkeypatch.setenv("PLANNER_MEMORY_INGEST_ALLOW_VERDIFY_PRIOR_PLANS", "false")
    monkeypatch.setenv("PLANNER_MEMORY_INGEST_ALLOW_SUPPORT_DOCS", "false")

    settings = AppSettings.from_env()

    assert settings.planner_memory_ingest_enabled is True
    assert settings.planner_memory_ingest_max_items == 25
    assert settings.planner_memory_ingest_max_body_chars == 900
    assert settings.planner_memory_ingest_allow_verdify_prior_plans is False
    assert settings.planner_memory_ingest_allow_support_docs is False


def test_execution_hooks_log_node_with_run_context() -> None:
    captured: list[dict[str, object]] = []

    class FakePlannerLogger:
        def info(self, event: str, **fields: object) -> None:
            captured.append({"event": event, **fields})

    hooks = ExecutionHooks(planner_logger=FakePlannerLogger())  # type: ignore[arg-type]
    hooks.set_run_context(
        trigger_id="trigger-1",
        thread_id="thread-1",
        request_id="request-1",
        trace_id="trace-1",
        run_mode="production",
        revision_count=2,
    )

    hooks.before_node("diagnose")
    hooks.clear_run_context()

    assert captured == [
        {
            "event": "planner_node_entered",
            "current_step": "diagnose",
            "trigger_id": "trigger-1",
            "thread_id": "thread-1",
            "request_id": "request-1",
            "trace_id": "trace-1",
            "run_mode": "production",
            "revision_count": 2,
            "status": "running",
        }
    ]


def test_planner_logger_adds_proposal_fields_to_completion_logs() -> None:
    captured: list[dict[str, object]] = []

    class FakeLogger:
        def info(self, message: str, *, extra: dict[str, object]) -> None:
            captured.append({"message": message, **extra})

    from uuid import uuid4

    from planner_graph.runtime import PlannerLogger
    from planner_graph.store import RunRecord

    trigger_id = uuid4()
    record = RunRecord(
        trigger_id=trigger_id,
        thread_id=trigger_id,
        status="completed",
        current_step="report",
        terminal_status="proposal_ready",
        execution_owner="planner-worker",
        state={
            "request_id": "request-1",
            "trace_id": "trace-1",
            "revision_count": 1,
            "selected_action": "set_plan",
            "context_completeness": "degraded",
            "proposal_decision_summary": "set_plan chosen because the sunrise branch should prepare for midday stress.",
            "guardrail_outcome": "pass",
            "guardrail_reasons": ["Weak context: readback is stale."],
            "contract_shape_rejection_reasons": [],
            "proposed_payload": {
                "plan_id": "iris-20260519-0600",
                "transitions": [{"ts": "2026-05-19T06:00:00-06:00"}],
            },
        },
    )
    planner_logger = PlannerLogger(logger=FakeLogger())  # type: ignore[arg-type]

    planner_logger.log_completed(record, duration_ms=57)

    assert captured == [
        {
            "message": "planner_run_completed",
            "event": "planner_run_completed",
            "trigger_id": str(trigger_id),
            "thread_id": str(trigger_id),
            "request_id": "request-1",
            "trace_id": "trace-1",
            "current_step": "report",
            "status": "completed",
            "terminal_status": "proposal_ready",
            "run_mode": "production",
            "execution_owner": "planner-worker",
            "revision_count": 1,
            "duration_ms": 57,
            "selected_action": "set_plan",
            "plan_id": "iris-20260519-0600",
            "proposal_summary": "set_plan:iris-20260519-0600 transitions=1",
            "proposal_decision_summary": "set_plan chosen because the sunrise branch should prepare for midday stress.",
            "guardrail_outcome": "pass",
            "guardrail_reasons": ["Weak context: readback is stale."],
            "contract_shape_rejection_reasons": [],
            "revision_reason": "",
            "fail_closed_reason": "",
            "context_completeness": "degraded",
        }
    ]
