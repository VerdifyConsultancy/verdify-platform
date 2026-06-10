"""Trigger envelope intake and run initialization.

This node validates the minimum incoming trigger information and seeds the first
bounded planner state fields. It connects the external request envelope to the
internal graph lifecycle.
"""

from __future__ import annotations

from planner_graph.nodes import copy_state
from planner_graph.runtime import PlannerRuntime
from planner_graph.state import GRAPH_VERSION, PlannerState, utc_now


def build_trigger_intake(runtime: PlannerRuntime):
    def trigger_intake(state: PlannerState) -> PlannerState:
        runtime.hooks.before_node("trigger_intake")
        raw_trigger_id = state.get("trigger_id")
        greenhouse_id = state.get("greenhouse_id")
        event_type = state.get("event_type")
        if raw_trigger_id is None or greenhouse_id is None or event_type is None:
            raise ValueError(
                "trigger_intake requires trigger_id, greenhouse_id, and event_type"
            )
        next_state = copy_state(state)
        next_state["thread_id"] = raw_trigger_id
        next_state["graph_version"] = GRAPH_VERSION
        next_state["run_mode"] = state.get("run_mode", "production")
        next_state["status"] = "running"
        next_state["current_step"] = "trigger_intake"
        next_state["started_at"] = state.get("started_at", utc_now())
        next_state["updated_at"] = utc_now()
        next_state["errors"] = state.get("errors", [])
        next_state["warnings"] = state.get("warnings", [])
        next_state["revision_count"] = state.get("revision_count", 0)
        return next_state

    return trigger_intake
