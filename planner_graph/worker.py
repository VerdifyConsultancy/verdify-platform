"""Background worker for asynchronous planner execution.

This module polls the run store, claims queued work, and invokes the graph to
completion. It connects HTTP-submitted planner runs to actual background
execution and terminal state updates.
"""

from __future__ import annotations

import time
import threading
from typing import cast
from uuid import UUID

from planner_graph.graph import build_graph
from planner_graph.runtime import PlannerRuntime
from planner_graph.state import GRAPH_VERSION, PlannerState, utc_now
from planner_graph.store import RunRecord, RunStore


class PlannerWorker:
    def __init__(self, repository: RunStore, runtime: PlannerRuntime) -> None:
        self.repository = repository
        self.runtime = runtime
        self.graph = build_graph(runtime)
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="planner-worker",
            daemon=True,
        )

    def start(self) -> None:
        if not self._thread.is_alive():
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def submit(self, trigger_id: UUID, initial_state: PlannerState) -> bool:
        _, should_enqueue = self.repository.create_or_resume(trigger_id, initial_state)
        return should_enqueue

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            owner = threading.current_thread().name
            record = self.repository.claim_next(
                owner, self.runtime.settings.worker_lease_seconds
            )
            if record is None:
                time.sleep(self.runtime.settings.worker_poll_interval_seconds)
                continue
            self.runtime.planner_logger.log_claim(record, owner)
            self.execute(record)

    def execute(self, record: RunRecord) -> None:
        trigger_id = record.trigger_id
        owner = threading.current_thread().name
        started = time.perf_counter()
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
            final_state = cast(
                PlannerState,
                self.graph.invoke(
                    initial_state,
                    config={"configurable": {"thread_id": str(trigger_id)}},
                ),
            )
        except Exception as error:  # pragma: no cover - surfaced in API state
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
        finally:
            self.runtime.hooks.clear_run_context()
        completed = self.repository.mark_completed(trigger_id, final_state, owner)
        duration_ms = int((time.perf_counter() - started) * 1000)
        self.runtime.planner_logger.log_completed(completed, duration_ms)
