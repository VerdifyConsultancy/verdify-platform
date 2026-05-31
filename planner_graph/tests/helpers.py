"""Shared test-data builders for planner fixtures.

This module keeps repeated fixture fragments in one place so tests do not each
have to rebuild the same contract-shaped payloads. It connects many tests to a
common set of realistic planner inputs.
"""

from __future__ import annotations

from typing import TypeAlias

from planner_graph.verdify_contract import TIER1_PLAN_DEFAULTS

PlanValue: TypeAlias = str | float | int | bool


def tier1_active_plan_summary(**extra: PlanValue) -> dict[str, PlanValue]:
    return {**TIER1_PLAN_DEFAULTS, **extra}
