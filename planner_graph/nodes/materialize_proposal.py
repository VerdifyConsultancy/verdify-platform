"""Proposal materialization for outward-facing planner results.

This node converts the planner's internal choice into the exact structured
payload shape that the public contract returns. It connects graph-internal
decision state to the external proposal that Verdify or other callers inspect.
"""

from __future__ import annotations

from typing import cast

from planner_graph.nodes import copy_state
from planner_graph.runtime import PlannerRuntime
from planner_graph.state import PlannerState, utc_now
from planner_graph.verdify_contract import (
    CLIMATE_INTENT_CONTRACT_VERSION,
    build_climate_intent,
    planner_plan_id,
)


def build_materialize_proposal(runtime: PlannerRuntime):
    def materialize_proposal(state: PlannerState) -> PlannerState:
        runtime.hooks.before_node("materialize_proposal")
        trigger_id = state.get("trigger_id")
        selected_action = state.get("selected_action")
        planner_instance = state.get("planner_instance")
        rationale = state.get("draft_rationale")
        if trigger_id is None or selected_action is None or rationale is None:
            raise ValueError(
                "materialize_proposal requires trigger_id, selected_action, and draft_rationale"
            )
        payload: dict[str, object]
        if selected_action == "set_tunable":
            tunable_changes = state.get("tunable_changes", {})
            if not tunable_changes:
                tunable_changes = {"fog_escalation_kpa": 0.4}
            parameter, value = next(iter(tunable_changes.items()))
            payload = {
                "parameter": parameter,
                "value": value,
                "reason": rationale,
                "trigger_id": trigger_id,
                "planner_instance": planner_instance,
            }
        elif selected_action == "set_plan":
            climate_intent = build_climate_intent(state.get("active_plan_summary"))
            payload = {
                "plan_id": planner_plan_id(
                    cast(str | None, state.get("triggered_at")),
                    trigger_id,
                ),
                "hypothesis": rationale,
                "transitions": [
                    {
                        "ts": state.get("triggered_at") or utc_now(),
                        "climate_intent": climate_intent,
                        "reason": rationale,
                    }
                ],
                "experiment": None,
                "expected_outcome": state.get("expected_effect"),
                "trigger_id": trigger_id,
                "planner_instance": planner_instance,
                "climate_intent_version": CLIMATE_INTENT_CONTRACT_VERSION,
            }
        elif selected_action == "acknowledge_trigger":
            payload = {
                "trigger_id": trigger_id,
                "reason": rationale,
                "planner_instance": planner_instance,
            }
        elif selected_action == "fail":
            payload = {
                "trigger_id": trigger_id,
                "reason": rationale,
                "planner_instance": planner_instance,
                "validation_errors": state.get("validation_errors", []),
                "guardrail_reasons": state.get("guardrail_reasons", []),
            }
        else:
            payload = {
                "trigger_id": trigger_id,
                "reason": rationale,
                "planner_instance": planner_instance,
            }
        next_state = copy_state(state)
        next_state["current_step"] = "materialize_proposal"
        next_state["proposed_payload"] = payload
        next_state["proposed_rationale"] = rationale
        draft_plan = state.get("draft_plan")
        confidence = 0.0
        if isinstance(draft_plan, dict):
            raw_confidence = draft_plan.get("confidence", 0.0)
            confidence = float(cast(int | float | str | bool, raw_confidence))
        next_state["proposed_confidence"] = confidence
        next_state["mcp_request"] = {
            "selected_action": selected_action,
            "trigger_id": trigger_id,
        }
        next_state["mcp_result"] = {
            "mode": "verdify_authoritative",
            "write_skipped": True,
            "reason": "Planner returns one bounded payload; Verdify MCP is the only write boundary.",
        }
        next_state["updated_at"] = utc_now()
        runtime.hooks.update_run_context(
            selected_action=selected_action,
            proposal_summary=payload.get("plan_id", selected_action),
        )
        return next_state

    return materialize_proposal
