"""Context normalization for incoming planner requests.

This node takes the shaped request contract and maps it into the specific state
fields the downstream nodes expect. It connects the external request payload to
the internal, bounded planner state representation.
"""

from __future__ import annotations

import hashlib
import json
from typing import cast

from planner_graph.nodes import copy_state
from planner_graph.prompts import weak_context_signals
from planner_graph.runtime import PlannerRuntime
from planner_graph.state import PlannerState, utc_now


def build_context_pack(runtime: PlannerRuntime):
    def context_pack(state: PlannerState) -> PlannerState:
        runtime.hooks.before_node("context_pack")
        required = [
            "climate_snapshot",
            "scorecard_summary",
            "forecast_summary",
            "active_plan_summary",
            "alerts_summary",
            "clamp_summary",
            "guardrail_audit_summary",
        ]
        missing = [field for field in required if state.get(field) is None]
        if missing:
            raise ValueError(
                f"context_pack requires pre-shaped context sections: {', '.join(missing)}"
            )
        next_state = copy_state(state)
        climate_snapshot = cast(
            dict[str, str | float | int | bool], state.get("climate_snapshot")
        )
        scorecard_summary = cast(
            dict[str, str | float | int | bool], state.get("scorecard_summary")
        )
        forecast_summary = cast(
            dict[str, str | float | int | bool], state.get("forecast_summary")
        )
        active_plan_summary = cast(
            dict[str, str | float | int | bool], state.get("active_plan_summary")
        )
        alerts_summary = cast(list[str], state.get("alerts_summary"))
        clamp_summary = cast(
            dict[str, str | float | int | bool], state.get("clamp_summary")
        )
        guardrail_audit_summary = cast(
            dict[str, str | float | int | bool],
            state.get("guardrail_audit_summary"),
        )
        recent_delivery_summary = cast(
            dict[str, str | float | int | bool],
            state.get("recent_delivery_summary", {}),
        )
        operator_notes = cast(list[str], state.get("operator_notes", []))
        next_state["context_sections"] = required
        digest_payload = json.dumps(
            {
                "climate_snapshot": climate_snapshot,
                "scorecard_summary": scorecard_summary,
                "forecast_summary": forecast_summary,
                "active_plan_summary": active_plan_summary,
                "alerts_summary": alerts_summary,
                "clamp_summary": clamp_summary,
                "guardrail_audit_summary": guardrail_audit_summary,
                "recent_delivery_summary": recent_delivery_summary,
                "operator_notes": operator_notes,
            },
            sort_keys=True,
            default=str,
        ).encode("utf-8")
        weak_signals = weak_context_signals(cast(dict[str, object], state))
        next_state["context_digest"] = hashlib.sha256(digest_payload).hexdigest()[:16]
        next_state["context_completeness"] = (
            "complete" if not weak_signals else "degraded"
        )
        next_state["context_weaknesses"] = weak_signals
        next_state["climate_snapshot"] = climate_snapshot
        next_state["scorecard_summary"] = scorecard_summary
        next_state["forecast_summary"] = forecast_summary
        next_state["active_plan_summary"] = active_plan_summary
        next_state["alerts_summary"] = alerts_summary
        next_state["clamp_summary"] = clamp_summary
        next_state["guardrail_audit_summary"] = guardrail_audit_summary
        next_state["recent_delivery_summary"] = recent_delivery_summary
        next_state["operator_notes"] = operator_notes
        next_state["current_step"] = "context_pack"
        next_state["updated_at"] = utc_now()
        return next_state

    return context_pack
