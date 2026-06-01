"""Execution-boundary verification before terminal reporting.

This node records planner-side verification and audit metadata without making
the planner a greenhouse writer. Verdify remains the only execution boundary.
"""

from __future__ import annotations

from planner_graph.nodes import copy_state
from planner_graph.runtime import PlannerRuntime
from planner_graph.state import PlannerState, utc_now


def build_execution_verify(runtime: PlannerRuntime):
    def execution_verify(state: PlannerState) -> PlannerState:
        runtime.hooks.before_node("execution_verify")
        trigger_id = state.get("trigger_id")
        if trigger_id is None:
            raise ValueError("execution_verify requires trigger_id")
        verification = runtime.db.verification_snapshot(trigger_id)
        next_state = copy_state(state)
        next_state["current_step"] = "execution_verify"
        next_state["delivery_status"] = verification["delivery_status"]
        next_state["readback_status"] = verification["readback_status"]
        next_state["plan_id"] = verification["plan_id"]
        next_state["updated_at"] = utc_now()
        return next_state

    return execution_verify
