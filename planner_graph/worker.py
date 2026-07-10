"""Background worker for asynchronous planner execution.

This module polls the run store, claims queued work, and invokes the graph to
completion. It connects HTTP-submitted planner runs to actual background
execution and terminal state updates.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from dataclasses import dataclass
from math import ceil, log2
from typing import cast
from uuid import UUID, uuid4

from planner_graph.graph import build_graph
from planner_graph.runtime import PlannerRuntime
from planner_graph.state import GRAPH_VERSION, PlannerState, utc_now
from planner_graph.store import LeaseLostError, RunRecord, RunStore


@dataclass(frozen=True)
class WorkerHealth:
    alive: bool
    ready: bool
    consecutive_store_failures: int
    retry_delay_seconds: float
    last_error_class: str | None


class BoundedBackoff:
    """Deterministic exponential retry with a finite ceiling and no stop race."""

    def __init__(self, base_seconds: float = 0.25, max_seconds: float = 30.0) -> None:
        self.base_seconds = base_seconds
        self.max_seconds = max_seconds

    def delay(self, failures: int) -> float:
        if self.base_seconds <= 0 or self.max_seconds <= 0:
            return 0.0
        if self.base_seconds >= self.max_seconds:
            return self.max_seconds
        # Cap the exponent before calculating it.  A worker can remain in an
        # outage for months; computing ``2 ** failures`` first eventually
        # overflows and would kill the retry loop that is meant to be
        # indefinite.
        ceiling_exponent = max(0, ceil(log2(self.max_seconds / self.base_seconds)))
        exponent = min(max(0, failures - 1), ceiling_exponent)
        return min(self.max_seconds, self.base_seconds * (2**exponent))


def _default_worker_id() -> str:
    pod_name = os.environ.get("POD_NAME") or socket.gethostname()
    pod_uid = os.environ.get("POD_UID") or "local"
    return f"planner-worker:{pod_name}:{pod_uid}:{os.getpid()}:{uuid4().hex[:12]}"


class PlannerWorker:
    def __init__(
        self,
        repository: RunStore,
        runtime: PlannerRuntime,
        *,
        worker_id: str | None = None,
    ) -> None:
        self.repository = repository
        self.runtime = runtime
        self.graph = build_graph(runtime)
        self.worker_id = worker_id or _default_worker_id()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lifecycle_lock = threading.Lock()
        self._health_lock = threading.Lock()
        self._ready = False
        self._consecutive_store_failures = 0
        self._retry_delay_seconds = 0.0
        self._last_error_class: str | None = None
        self._backoff = BoundedBackoff()

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                name="planner-worker",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5)
        with self._health_lock:
            self._ready = False

    def health(self) -> WorkerHealth:
        thread = self._thread
        with self._health_lock:
            return WorkerHealth(
                alive=bool(thread and thread.is_alive()),
                ready=self._ready,
                consecutive_store_failures=self._consecutive_store_failures,
                retry_delay_seconds=self._retry_delay_seconds,
                last_error_class=self._last_error_class,
            )

    def _record_store_success(self) -> None:
        with self._health_lock:
            self._ready = True
            self._consecutive_store_failures = 0
            self._retry_delay_seconds = 0.0
            self._last_error_class = None

    def _record_store_failure(self, error: Exception) -> float:
        with self._health_lock:
            self._ready = False
            self._consecutive_store_failures += 1
            self._retry_delay_seconds = self._backoff.delay(self._consecutive_store_failures)
            self._last_error_class = type(error).__name__
            return self._retry_delay_seconds

    def submit(self, trigger_id: UUID, initial_state: PlannerState) -> bool:
        _, should_enqueue = self.repository.create_or_resume(trigger_id, initial_state)
        return should_enqueue

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            owner = self.worker_id
            try:
                record = self.repository.claim_next(owner, self.runtime.settings.worker_lease_seconds)
            except Exception as error:
                delay = self._record_store_failure(error)
                # Event.wait is interruptible, unlike time.sleep: shutdown
                # during a prolonged DB/DNS outage never races a sleeping loop.
                self._stop.wait(delay)
                continue
            self._record_store_success()
            if record is None:
                self._stop.wait(self.runtime.settings.worker_poll_interval_seconds)
                continue
            self.runtime.planner_logger.log_claim(record, owner)
            try:
                self.execute(record)
            except Exception as error:
                # Repository failure while recording terminal state belongs to
                # the same retryable infrastructure class as claim failure.
                delay = self._record_store_failure(error)
                self._stop.wait(delay)

    def execute(self, record: RunRecord) -> None:
        trigger_id = record.trigger_id
        owner = self.worker_id
        started = time.perf_counter()
        lease_seconds = self.runtime.settings.worker_lease_seconds
        renewal_stop = threading.Event()
        renewal_lost = threading.Event()
        renewal_errors: list[Exception] = []

        def renew_until_stopped() -> None:
            interval = max(0.1, lease_seconds / 3)
            while not renewal_stop.wait(interval):
                try:
                    renewed = self.repository.renew_lease(
                        trigger_id,
                        owner,
                        lease_seconds,
                    )
                except Exception as error:  # pragma: no cover - store-specific
                    renewal_errors.append(error)
                    renewal_lost.set()
                    # Readiness must reflect the store outage while the graph
                    # is still running. Waiting for ``execute`` to return can
                    # leave a slow or hung graph false-green for minutes.
                    self._record_store_failure(error)
                    return
                if not renewed:
                    error = LeaseLostError(
                        f"planner run lease lost for trigger {trigger_id} and owner {owner}"
                    )
                    renewal_errors.append(error)
                    renewal_lost.set()
                    self._record_store_failure(error)
                    return
                self._record_store_success()

        renewal_thread = threading.Thread(
            target=renew_until_stopped,
            name=f"planner-lease-renew-{str(trigger_id)[:8]}",
            daemon=True,
        )
        renewal_thread.start()

        def stop_and_refresh_lease() -> None:
            renewal_stop.set()
            renewal_thread.join(timeout=2)
            if renewal_errors:
                raise renewal_errors[0]
            if renewal_lost.is_set() or not self.repository.renew_lease(
                trigger_id,
                owner,
                lease_seconds,
            ):
                raise LeaseLostError(
                    f"planner run lease lost for trigger {trigger_id} and owner {owner}"
                )

        initial_state: PlannerState = {
            "trigger_id": str(trigger_id),
            "thread_id": str(trigger_id),
            "run_mode": "production",
            "graph_version": GRAPH_VERSION,
            "status": "running",
            "started_at": utc_now(),
            "updated_at": utc_now(),
            "errors": [],
            "warnings": [],
            "revision_count": 0,
        }
        if record.state:
            initial_state.update(record.state)
            initial_state["status"] = "running"
            initial_state["updated_at"] = utc_now()
        self.runtime.hooks.set_run_context(
            trigger_id=str(trigger_id),
            thread_id=str(trigger_id),
            request_id=str(initial_state.get("request_id", "")),
            trace_id=str(initial_state.get("trace_id", "")),
            run_mode=str(initial_state.get("run_mode", "production")),
            revision_count=int(initial_state.get("revision_count", 0) or 0),
        )
        try:
            try:
                final_state = cast(
                    PlannerState,
                    self.graph.invoke(
                        initial_state,
                        config={"configurable": {"thread_id": str(trigger_id)}},
                    ),
                )
            except Exception as error:  # pragma: no cover - surfaced in API state
                stop_and_refresh_lease()
                self.repository.mark_failed(trigger_id, error, owner)
                duration_ms = int((time.perf_counter() - started) * 1000)
                self.runtime.planner_logger.log_failed(
                    trigger_id=str(trigger_id),
                    owner=owner,
                    request_id=str(initial_state.get("request_id", "")),
                    trace_id=str(initial_state.get("trace_id", "")),
                    duration_ms=duration_ms,
                    error=error,
                )
                return
            stop_and_refresh_lease()
            completed = self.repository.mark_completed(trigger_id, final_state, owner)
            duration_ms = int((time.perf_counter() - started) * 1000)
            self.runtime.planner_logger.log_completed(completed, duration_ms)
        finally:
            renewal_stop.set()
            renewal_thread.join(timeout=2)
            self.runtime.hooks.clear_run_context()
