"""Worker truthfulness and transient-store recovery for non-authoritative planner_graph."""

from __future__ import annotations

import threading
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

    def renew_lease(self, *args, **kwargs):
        return self.delegate.renew_lease(*args, **kwargs)

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


def test_workers_get_process_unique_fencing_identities(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("planner_graph.worker.build_graph", lambda _runtime: _Graph())

    first = PlannerWorker(InMemoryRunStore(), _runtime())
    second = PlannerWorker(InMemoryRunStore(), _runtime())

    assert first.worker_id.startswith("planner-worker:")
    assert second.worker_id.startswith("planner-worker:")
    assert first.worker_id != second.worker_id


def test_long_graph_renews_lease_before_terminal_write(monkeypatch: pytest.MonkeyPatch) -> None:
    class SlowGraph(_Graph):
        def invoke(self, state, config):
            time.sleep(0.75)
            return super().invoke(state, config)

    class CountingStore(InMemoryRunStore):
        renewals = 0

        def renew_lease(self, *args, **kwargs):
            self.renewals += 1
            return super().renew_lease(*args, **kwargs)

    monkeypatch.setattr("planner_graph.worker.build_graph", lambda _runtime: SlowGraph())
    runtime = _runtime()
    runtime.settings.worker_lease_seconds = 1
    store = CountingStore()
    worker = PlannerWorker(store, runtime, worker_id="planner-worker:test")
    trigger_id = uuid4()
    worker.submit(trigger_id, {"updated_at": "2026-07-10T00:00:00+00:00"})
    claimed = store.claim_next(worker.worker_id, lease_seconds=1)

    assert claimed is not None
    worker.execute(claimed)

    assert store.renewals >= 2
    assert store.get(trigger_id).status == "completed"  # type: ignore[union-attr]


def test_inflight_renewal_failure_immediately_fails_readiness_and_recovers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph_started = threading.Event()
    release_graph = threading.Event()

    class BlockingGraph(_Graph):
        def invoke(self, state, config):
            graph_started.set()
            assert release_graph.wait(2), "test did not release blocking graph"
            return super().invoke(state, config)

    class OneRenewalFailureStore(_RecoveringStore):
        def __init__(self, delegate: InMemoryRunStore) -> None:
            super().__init__(delegate, failing=False)
            self.renew_failed = threading.Event()

        def renew_lease(self, *args, **kwargs):
            if not self.renew_failed.is_set():
                self.renew_failed.set()
                raise OSError("renewal database outage")
            return self.delegate.renew_lease(*args, **kwargs)

    monkeypatch.setattr("planner_graph.worker.build_graph", lambda _runtime: BlockingGraph())
    runtime = _runtime()
    runtime.settings.worker_lease_seconds = 1
    store = OneRenewalFailureStore(InMemoryRunStore())
    worker = PlannerWorker(store, runtime, worker_id="planner-worker:test")
    trigger_id = uuid4()
    worker.submit(trigger_id, {"updated_at": "2026-07-10T00:00:00+00:00"})

    worker.start()
    assert graph_started.wait(1)
    _wait_until(lambda: store.renew_failed.is_set())
    _wait_until(lambda: not worker.health().ready)
    failed_health = worker.health()

    assert failed_health.alive is True
    assert failed_health.consecutive_store_failures >= 1
    assert failed_health.last_error_class == "OSError"

    release_graph.set()
    _wait_until(lambda: worker.health().ready)
    recovered_health = worker.health()
    worker.stop()

    assert recovered_health.consecutive_store_failures == 0
    assert recovered_health.last_error_class is None
