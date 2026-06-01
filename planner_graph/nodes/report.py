"""Final reporting node for completed planner runs.

This node captures the last reporting side effect and stamps the run with its
terminal planner status. It connects the tail of the graph to the final
bookkeeping that marks a run as completed.
"""

from __future__ import annotations

from typing import cast

from planner_graph.nodes import copy_state
from planner_graph.runtime import PlannerRuntime
from planner_graph.state import PlannerState, utc_now


def build_report(runtime: PlannerRuntime):
    def report(state: PlannerState) -> PlannerState:
        runtime.hooks.before_node("report")
        trigger_id = state.get("trigger_id")
        selected_action = state.get("selected_action")
        run_mode = state.get("run_mode")
        if trigger_id is None or selected_action is None or run_mode is None:
            raise ValueError(
                "report requires trigger_id, selected_action, and run_mode"
            )
        report_payload = cast(
            dict[str, str | bool],
            runtime.slack.send_report(
                trigger_id,
                f"{selected_action} completed in {run_mode} mode",
            ),
        )
        next_state = copy_state(state)
        next_state["current_step"] = "report"
        next_state["slack_report"] = report_payload
        next_state["status"] = "completed"
        next_state["terminal_status"] = (
            "proposal_failed" if selected_action == "fail" else "proposal_ready"
        )
        next_state["updated_at"] = utc_now()
        return next_state

    return report
