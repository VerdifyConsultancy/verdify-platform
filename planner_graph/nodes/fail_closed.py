"""Fail-safe node for invalid or unsafe planner outcomes.

This node turns bad inputs, broken drafts, or unsafe proposals into an explicit
structured failure result rather than letting the planner bluff or crash. It
connects the planner's safety posture to its terminal recommendation behavior.
"""

from __future__ import annotations

from planner_graph.nodes import copy_state
from planner_graph.runtime import PlannerRuntime
from planner_graph.state import PlannerState, utc_now


def build_fail_closed(runtime: PlannerRuntime):
    def fail_closed(state: PlannerState) -> PlannerState:
        runtime.hooks.before_node("fail_closed")
        errors = state.get("validation_errors", [])
        error_summary = ", ".join(errors) if errors else "Unknown validation failure."
        next_state = copy_state(state)
        next_state["current_step"] = "fail_closed"
        next_state["draft_action"] = "fail"
        next_state["selected_action"] = "fail"
        next_state["fail_closed_reason"] = error_summary
        next_state["draft_rationale"] = (
            f"Planner could not produce a safe proposal: {error_summary}"
        )
        next_state["expected_effect"] = (
            "No action proposed. Verdify should retain local control."
        )
        next_state["updated_at"] = utc_now()
        runtime.hooks.update_run_context(
            selected_action="fail",
            fail_closed_reason=error_summary,
        )
        return next_state

    return fail_closed
