"""Runtime dependency container and logging hooks for the planner.

This module owns the service-level dependencies that graph nodes need at
execution time, such as database reads, model access, MCP stubs, and logging.
It connects the planner loop to observability and external adapters.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from planner_graph.config import AppSettings
from planner_graph.clients.db import VerdifyReadClient
from planner_graph.clients.mcp import MCPClient
from planner_graph.clients.openai import OpenAIPlannerClient
from planner_graph.clients.slack import SlackClient
from planner_graph.memory import (
    DisabledMemoryStore,
    InMemoryMemoryStore,
    PlannerMemoryStore,
    PostgresMemoryStore,
)

if TYPE_CHECKING:
    from planner_graph.state import PlannerState
    from planner_graph.store import RunRecord


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "event": getattr(record, "event", record.msg),
        }
        for key in (
            "trigger_id",
            "thread_id",
            "request_id",
            "trace_id",
            "current_step",
            "status",
            "terminal_status",
            "run_mode",
            "queued",
            "submission_count",
            "execution_owner",
            "revision_count",
            "duration_ms",
            "selected_action",
            "plan_id",
            "proposal_summary",
            "proposal_decision_summary",
            "guardrail_outcome",
            "guardrail_reasons",
            "contract_shape_rejection_reasons",
            "revision_reason",
            "fail_closed_reason",
            "context_completeness",
        ):
            value = getattr(record, key, None)
            if value is not None and value != "":
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, sort_keys=True, default=str)


def build_logger() -> logging.Logger:
    logger = logging.getLogger("planner_graph")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


@dataclass
class PlannerLogger:
    logger: logging.Logger = field(default_factory=build_logger)

    def info(self, event: str, **fields: object) -> None:
        self.logger.info(event, extra={"event": event, **fields})

    def exception(self, event: str, **fields: object) -> None:
        self.logger.exception(event, extra={"event": event, **fields})

    def _proposal_fields(self, state: "PlannerState") -> dict[str, object]:
        selected_action = state.get("selected_action")
        payload = state.get("proposed_payload", {})
        if not isinstance(payload, dict):
            payload = {}
        plan_id = (
            payload.get("plan_id")
            if isinstance(payload.get("plan_id"), str)
            else state.get("plan_id")
        )
        proposal_summary = ""
        if selected_action == "set_plan":
            transition_count = 0
            transitions = payload.get("transitions")
            if isinstance(transitions, list):
                transition_count = len(transitions)
            proposal_summary = (
                f"set_plan:{plan_id or 'unknown'} transitions={transition_count}"
            )
        elif selected_action == "set_tunable":
            parameter = payload.get("parameter")
            value = payload.get("value")
            if isinstance(parameter, str):
                proposal_summary = f"set_tunable:{parameter}={value}"
        elif selected_action == "acknowledge_trigger":
            proposal_summary = "acknowledge_trigger"
        elif selected_action == "fail":
            proposal_summary = "fail_closed"
        return {
            "selected_action": selected_action,
            "plan_id": plan_id,
            "proposal_summary": proposal_summary,
            "proposal_decision_summary": state.get("proposal_decision_summary", ""),
            "guardrail_outcome": state.get("guardrail_outcome", ""),
            "guardrail_reasons": state.get("guardrail_reasons", []),
            "contract_shape_rejection_reasons": state.get(
                "contract_shape_rejection_reasons", []
            ),
            "revision_reason": state.get("revision_reason", ""),
            "fail_closed_reason": state.get("fail_closed_reason", ""),
            "context_completeness": state.get("context_completeness", ""),
        }

    def log_submission(
        self,
        *,
        trigger_id: str,
        request_id: str,
        trace_id: str,
        queued: bool,
        status: str,
        run_mode: str,
    ) -> None:
        self.info(
            "planner_run_submitted",
            trigger_id=trigger_id,
            request_id=request_id,
            trace_id=trace_id,
            queued=queued,
            status=status,
            run_mode=run_mode,
        )

    def log_fetch(self, record: "RunRecord") -> None:
        state = record.state
        self.info(
            "planner_run_fetched",
            trigger_id=str(record.trigger_id),
            thread_id=str(record.thread_id),
            request_id=str(state.get("request_id", "")),
            trace_id=str(state.get("trace_id", "")),
            current_step=str(record.current_step or state.get("current_step", "")),
            status=record.status,
            terminal_status=str(record.terminal_status or ""),
            run_mode=record.run_mode,
            execution_owner=str(record.execution_owner or ""),
            revision_count=int(state.get("revision_count", 0) or 0),
            **self._proposal_fields(state),
        )

    def log_claim(self, record: "RunRecord", owner: str) -> None:
        state = record.state
        self.info(
            "planner_run_claimed",
            trigger_id=str(record.trigger_id),
            thread_id=str(record.thread_id),
            request_id=str(state.get("request_id", "")),
            trace_id=str(state.get("trace_id", "")),
            status=record.status,
            execution_owner=owner,
            submission_count=record.submission_count,
        )

    def log_node(self, node_name: str) -> None:
        self.info("planner_node_entered", current_step=node_name)

    def log_completed(self, record: "RunRecord", duration_ms: int) -> None:
        state = record.state
        self.info(
            "planner_run_completed",
            trigger_id=str(record.trigger_id),
            thread_id=str(record.thread_id),
            request_id=str(state.get("request_id", "")),
            trace_id=str(state.get("trace_id", "")),
            current_step=str(record.current_step or state.get("current_step", "")),
            status=record.status,
            terminal_status=str(record.terminal_status or ""),
            run_mode=record.run_mode,
            execution_owner=str(record.execution_owner or ""),
            revision_count=int(state.get("revision_count", 0) or 0),
            duration_ms=duration_ms,
            **self._proposal_fields(state),
        )

    def log_failed(
        self,
        *,
        trigger_id: str,
        owner: str,
        request_id: str,
        trace_id: str,
        duration_ms: int,
        error: BaseException,
    ) -> None:
        self.exception(
            "planner_run_failed",
            trigger_id=trigger_id,
            execution_owner=owner,
            request_id=request_id,
            trace_id=trace_id,
            duration_ms=duration_ms,
        )


@dataclass
class ExecutionHooks:
    pause_before_node: str | None = None
    release_event: threading.Event | None = None
    started_event: threading.Event | None = None
    planner_logger: PlannerLogger | None = None
    _local: threading.local = field(default_factory=threading.local)

    def set_run_context(
        self,
        *,
        trigger_id: str,
        thread_id: str,
        request_id: str,
        trace_id: str,
        run_mode: str,
        revision_count: int,
    ) -> None:
        self._local.run_context = {
            "trigger_id": trigger_id,
            "thread_id": thread_id,
            "request_id": request_id,
            "trace_id": trace_id,
            "run_mode": run_mode,
            "revision_count": revision_count,
            "status": "running",
        }

    def clear_run_context(self) -> None:
        if hasattr(self._local, "run_context"):
            del self._local.run_context

    def update_run_context(self, **fields: object) -> None:
        context = getattr(self._local, "run_context", None)
        if not isinstance(context, dict):
            return
        context.update(fields)

    def before_node(self, node_name: str) -> None:
        if self.planner_logger is not None:
            run_context = getattr(self._local, "run_context", {})
            if not isinstance(run_context, dict):
                run_context = {}
            self.planner_logger.info(
                "planner_node_entered",
                current_step=node_name,
                **run_context,
            )
        if self.pause_before_node != node_name:
            return
        if self.started_event is not None:
            self.started_event.set()
        if self.release_event is not None:
            self.release_event.wait(timeout=5)


@dataclass
class PlannerRuntime:
    settings: AppSettings = field(default_factory=AppSettings.from_env)
    db: VerdifyReadClient = field(default_factory=VerdifyReadClient)
    mcp: MCPClient = field(default_factory=MCPClient)
    openai: OpenAIPlannerClient = field(default_factory=OpenAIPlannerClient)
    slack: SlackClient = field(default_factory=SlackClient)
    memory: PlannerMemoryStore = field(default_factory=DisabledMemoryStore)
    hooks: ExecutionHooks = field(default_factory=ExecutionHooks)
    planner_logger: PlannerLogger = field(default_factory=PlannerLogger)

    def __post_init__(self) -> None:
        if self.db.dsn is None and self.settings.verdify_db_dsn is not None:
            self.db = VerdifyReadClient(self.settings.verdify_db_dsn)
        if self.openai.api_key is None and self.settings.openai_api_key is not None:
            self.openai = OpenAIPlannerClient(
                api_key=self.settings.openai_api_key,
                base_url=self.settings.openai_base_url,
                model=self.settings.openai_model,
                reasoning_effort=self.settings.openai_reasoning_effort,
                timeout_seconds=self.settings.openai_timeout_seconds,
            )
        if isinstance(self.memory, DisabledMemoryStore):
            if self.settings.planner_memory_backend == "memory":
                self.memory = InMemoryMemoryStore()
            elif self.settings.planner_memory_backend == "postgres":
                if self.settings.planner_memory_db_dsn is None:
                    raise RuntimeError(
                        "PLANNER_MEMORY_BACKEND=postgres requires PLANNER_MEMORY_DB_DSN or PLANNER_DB_DSN"
                    )
                self.memory = PostgresMemoryStore(self.settings.planner_memory_db_dsn)
        self.hooks.planner_logger = self.planner_logger
