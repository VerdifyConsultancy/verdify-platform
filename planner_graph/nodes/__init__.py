"""Shared helpers for planner graph nodes.

This module contains small utilities that every node can use to work with the
bounded internal planner state consistently. It connects the whole graph to one
common pattern for copying and updating state safely.
"""

from __future__ import annotations

from typing import cast

from planner_graph.state import PlannerState


def copy_state(state: PlannerState) -> PlannerState:
    return cast(PlannerState, dict(state))
