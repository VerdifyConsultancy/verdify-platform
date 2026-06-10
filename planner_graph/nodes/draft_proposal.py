"""Initial proposal drafting for the planner graph.

This node chooses a first candidate action, rationale, confidence, and expected
effect using either the LLM path or the deterministic fallback. It connects
diagnosis to the rest of the planner safety and validation pipeline.
"""

from __future__ import annotations

from typing import cast

from planner_graph.nodes import copy_state
from planner_graph.runtime import PlannerRuntime
from planner_graph.state import PlannerState, SelectedAction, utc_now


def build_draft_proposal(runtime: PlannerRuntime):
    def draft_proposal(state: PlannerState) -> PlannerState:
        runtime.hooks.before_node("draft_proposal")
        draft = cast(
            dict[str, str | int | float | dict[str, float] | list[str] | bool],
            runtime.openai.draft_plan(dict(state)),
        )
        selected_action = draft.get("selected_action")
        rationale = draft.get("rationale")
        if not isinstance(selected_action, str) or not isinstance(rationale, str):
            raise ValueError("draft_proposal requires selected_action and rationale")
        next_state = copy_state(state)
        next_state["current_step"] = "draft_proposal"
        next_state["draft_plan"] = draft
        next_state["draft_action"] = selected_action
        next_state["draft_rationale"] = rationale
        next_state["selected_action"] = cast(SelectedAction, selected_action)
        next_state["tunable_changes"] = cast(
            dict[str, float], draft.get("tunable_changes", {})
        )
        next_state["expected_effect"] = cast(str, draft.get("expected_effect", ""))
        next_state["action_choice_reason"] = rationale
        next_state["proposal_decision_summary"] = (
            f"{selected_action} chosen because {rationale[:180]}"
        )
        next_state["updated_at"] = utc_now()
        runtime.hooks.update_run_context(
            selected_action=selected_action,
            proposal_decision_summary=next_state["proposal_decision_summary"],
        )
        return next_state

    return draft_proposal
