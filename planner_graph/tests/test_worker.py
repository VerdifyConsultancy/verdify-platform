"""Worker truthfulness and transient-store recovery for non-authoritative planner_graph."""

from __future__ import annotations

import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from planner_graph.store import InMemoryRunStore
from planner_graph.worker import BoundedBackoff, PlannerWorker


class _Logger:
    def log_claim(self, *_args, **_kwargs) -> None: ...
    def log_completed(self, *_args, **_kwargs) -> None: ...
    def log_failed(self, *_args, **_kwargs) -> None: ...


class _Hooks:
    def set_run_context(self, **_kwargs) -> None: ...
    def clear_run_context(self) -> None: ...


class _Graph:
    def invoke(self, state, config):
        del config
        return {
            **state,
            "status": "completed",
            "current_step": "report",
            "terminal_status": "proposal_ready_non_authoritative",
            "selected_action": "set_plan",
            "updated_at": state["updated_at"],
        }


class _RecoveringStore:
    def __init__(self, delegate: InMemoryRunStore, *, failing: bool = True) -> None:
        self.delegate = delegate
        self.failing = failing

    def initialize(self):
        return self.delegate.initialize()

    def create_or_resume(self, *args, **kwargs):
        return self.delegate.create_or_resume(*args, **kwargs)

    def claim_next(self, *args, **kwargs):
        if self.failing:
            raise OSError("temporary DNS failure")
        return self.delegate.claim_next(*args, **kwargs)

    def mark_completed(self, *args, **kwargs):
        return self.delegate.mark_completed(*args, **kwargs)

    def mark_failed(self, *args, **kwargs):
        return self.delegate.mark_failed(*args, **kwargs)

    def get(self, *args, **kwargs):
        return self.delegate.get(*args, **kwargs)


def _runtime():
    return SimpleNamespace(
        settings=SimpleNamespace(
            worker_lease_seconds=5,
            worker_poll_interval_seconds=0.01,
        ),
        planner_logger=_Logger(),
        hooks=_Hooks(),
    )


def _wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition did not become true")


def test_bounded_backoff_has_finite_ceiling() -> None:
    backoff = BoundedBackoff(base_seconds=0.25, max_seconds=2.0)

    assert [backoff.delay(n) for n in (1, 2, 3, 4, 20)] == [0.25, 0.5, 1.0, 2.0, 2.0]
    assert backoff.delay(100_000) == 2.0


def test_worker_stays_alive_not_ready_during_prolonged_store_outage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("planner_graph.worker.build_graph", lambda _runtime: _Graph())
    store = _RecoveringStore(InMemoryRunStore())
    worker = PlannerWorker(store, _runtime())
    worker._backoff = BoundedBackoff(base_seconds=0.01, max_seconds=0.04)

    worker.start()
    _wait_until(lambda: worker.health().consecutive_store_failures >= 4)
    failed_health = worker.health()

    assert failed_health.alive is True
    assert failed_health.ready is False
    assert failed_health.retry_delay_seconds == 0.04
    assert failed_health.last_error_class == "OSError"

    store.failing = False
    _wait_until(lambda: worker.health().ready)
    assert worker.health().consecutive_store_failures == 0
    worker.stop()


def test_shutdown_interrupts_retry_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("planner_graph.worker.build_graph", lambda _runtime: _Graph())
    worker = PlannerWorker(_RecoveringStore(InMemoryRunStore()), _runtime())
    worker._backoff = BoundedBackoff(base_seconds=2.0, max_seconds=2.0)
    worker.start()
    _wait_until(lambda: worker.health().consecutive_store_failures == 1)

    started = time.monotonic()
    worker.stop()

    assert time.monotonic() - started < 0.5
    assert worker.health().alive is False


def test_synthetic_non_authoritative_run_reaches_terminal_after_recovery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("planner_graph.worker.build_graph", lambda _runtime: _Graph())
    store = _RecoveringStore(InMemoryRunStore(), failing=True)
    worker = PlannerWorker(store, _runtime())
    worker._backoff = BoundedBackoff(base_seconds=0.01, max_seconds=0.02)
    trigger_id = uuid4()
    worker.submit(
        trigger_id,
        {
            "trigger_id": str(trigger_id),
            "thread_id": str(trigger_id),
            "run_mode": "production",
            "status": "queued",
            "updated_at": "2026-07-10T00:00:00+00:00",
        },
    )
    worker.start()
    _wait_until(lambda: worker.health().consecutive_store_failures >= 2)

    store.failing = False
    _wait_until(lambda: bool((record := store.get(trigger_id)) and record.status == "completed"))
    record = store.get(trigger_id)
    worker.stop()

    assert record is not None
    assert record.terminal_status == "proposal_ready_non_authoritative"
    assert record.state["selected_action"] == "set_plan"
    # This store is planner_graph_runs only; no Hermes delivery or MCP/device
    # acceptance surface is available to this worker by construction.
    assert not hasattr(store, "set_plan")
