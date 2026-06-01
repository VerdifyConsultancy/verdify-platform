"""Persist bounded planner memory without blocking run completion.

This node stores compact planner-owned memory after the proposal has already
been materialized and verified. Persistence failures are downgraded to warnings
so memory never prevents run completion.
"""

from __future__ import annotations

from typing import cast

from planner_graph.nodes import copy_state
from planner_graph.runtime import PlannerRuntime
from planner_graph.state import PlannerState, utc_now


def build_persist_memory(runtime: PlannerRuntime):
    def persist_memory(state: PlannerState) -> PlannerState:
        runtime.hooks.before_node("persist_memory")
        next_state = copy_state(state)
        next_state["current_step"] = "persist_memory"
        if not runtime.settings.planner_memory_persist_run_summaries:
            next_state["updated_at"] = utc_now()
            return next_state
        try:
            runtime.memory.persist_run_summary(state)
        except Exception as error:  # pragma: no cover - validated via unit tests
            warnings = list(cast(list[str], next_state.get("warnings", [])))
            warnings.append(f"memory persistence unavailable: {error}")
            next_state["warnings"] = warnings
        next_state["updated_at"] = utc_now()
        return next_state

    return persist_memory
