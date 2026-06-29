"""One-pass revision step for draft proposals.

This node gives the planner a constrained chance to narrow or soften a proposal
after guardrails object to it. It connects safety feedback to a guarded retry
before the planner decides to fail closed.
"""

from __future__ import annotations

from planner_graph.nodes import copy_state
from planner_graph.runtime import PlannerRuntime
from planner_graph.state import PlannerState, utc_now


def build_revise_proposal(runtime: PlannerRuntime):
    def revise_proposal(state: PlannerState) -> PlannerState:
        runtime.hooks.before_node("revise_proposal")
        rationale = state.get("draft_rationale")
        if rationale is None:
            raise ValueError("revise_proposal requires draft_rationale")
        next_state = copy_state(state)
        next_state["current_step"] = "revise_proposal"
        next_state["revision_count"] = state.get("revision_count", 0) + 1
        next_state["draft_action"] = "acknowledge_trigger"
        next_state["selected_action"] = "acknowledge_trigger"
        next_state["tunable_changes"] = {}
        next_state["revision_reason"] = (
            "Guardrail preview indicated the original proposal would likely be clamped."
        )
        next_state["draft_rationale"] = (
            f"{rationale} Revised to acknowledge only because the original proposal "
            "would likely be clamped by guardrails."
        )
        next_state["expected_effect"] = "No control change. Hold for Verdify review."
        next_state["updated_at"] = utc_now()
        runtime.hooks.update_run_context(
            selected_action="acknowledge_trigger",
            revision_count=next_state["revision_count"],
            revision_reason=next_state["revision_reason"],
        )
        return next_state

    return revise_proposal
