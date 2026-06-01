"""Context quality gate for the planner graph.

This node decides whether the incoming request has enough complete and trustworthy
context to continue through the rest of the planning loop. It connects intake
normalization to the planner's first explicit quality checkpoint.
"""

from __future__ import annotations

from planner_graph.nodes import copy_state
from planner_graph.runtime import PlannerRuntime
from planner_graph.state import PlannerState, utc_now


def build_context_gate(runtime: PlannerRuntime):
    def context_gate(state: PlannerState) -> PlannerState:
        runtime.hooks.before_node("context_gate")
        warnings = list(state.get("warnings", []))
        completeness = state.get("context_completeness", "unknown")
        if completeness != "complete":
            warnings.append("Running with degraded context.")
        warnings.extend(
            str(item)
            for item in state.get("context_weaknesses", [])
            if item not in warnings
        )
        next_state = copy_state(state)
        next_state["status"] = "running"
        next_state["current_step"] = "context_gate"
        next_state["warnings"] = warnings
        next_state["updated_at"] = utc_now()
        return next_state

    return context_gate
