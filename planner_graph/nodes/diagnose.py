"""Diagnosis step for the planner graph.

This node interprets the current trigger, environment, and retrieved context to
produce a structured understanding of what is happening. It connects raw context
to the planner's first real reasoning artifact before action selection.
"""

from __future__ import annotations

from typing import cast

from planner_graph.nodes import copy_state
from planner_graph.runtime import PlannerRuntime
from planner_graph.state import PlannerState, utc_now


def build_diagnose(runtime: PlannerRuntime):
    def diagnose(state: PlannerState) -> PlannerState:
        runtime.hooks.before_node("diagnose")
        diagnosis = cast(
            dict[str, str | list[str]],
            runtime.openai.diagnose(dict(state)),
        )
        next_state = copy_state(state)
        next_state["current_step"] = "diagnose"
        next_state["diagnosis"] = diagnosis
        next_state["updated_at"] = utc_now()
        return next_state

    return diagnose
