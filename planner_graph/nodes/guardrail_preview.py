"""Guardrail preview for drafted proposals.

This node estimates whether a proposal is likely to be clamped, revised, or
operationally questionable before it is returned. It connects contract-valid
proposal drafts to the planner's softer safety review step.
"""

from __future__ import annotations

from typing import cast

from planner_graph.nodes import copy_state
from planner_graph.runtime import PlannerRuntime
from planner_graph.state import PlannerState, utc_now


def build_guardrail_preview(runtime: PlannerRuntime):
    def guardrail_preview(state: PlannerState) -> PlannerState:
        runtime.hooks.before_node("guardrail_preview")
        selected_action = state.get("selected_action")
        would_clamp = False
        summary = "Proposal is expected to remain within guardrails."
        expected_clamps: list[str] = []
        hold_risk = "low"
        reasons: list[str] = []
        outcome = "pass"
        tunable_changes = state.get("tunable_changes", {})
        alerts_summary = cast(list[str], state.get("alerts_summary", []))
        lowered_alerts = " ".join(str(item).lower() for item in alerts_summary)
        guardrail_audit_summary = cast(
            dict[str, object], state.get("guardrail_audit_summary", {})
        )
        freshness = guardrail_audit_summary.get("readback_freshness_seconds")
        clamp_summary = cast(dict[str, object], state.get("clamp_summary", {}))
        active_clamps = clamp_summary.get("active_clamps_24h", 0)
        context_weaknesses = cast(list[str], state.get("context_weaknesses", []))
        if selected_action in {"set_plan", "set_tunable"} and context_weaknesses:
            reasons.extend(f"Weak context: {issue}." for issue in context_weaknesses)
        if (
            isinstance(freshness, (int, float))
            and freshness > 900
            and selected_action in {"set_plan", "set_tunable"}
        ):
            reasons.append("Readback freshness is too stale for a control proposal.")
        if (
            "sensor offline" in lowered_alerts or "telemetry stale" in lowered_alerts
        ) and selected_action in {
            "set_plan",
            "set_tunable",
        }:
            reasons.append("Telemetry quality alert blocks control proposals.")
        if (
            selected_action == "set_tunable"
            and isinstance(tunable_changes, dict)
            and tunable_changes
        ):
            parameter, value = next(iter(tunable_changes.items()))
            numeric_value = float(value)
            if numeric_value > 0.3:
                would_clamp = True
                expected_clamps = [
                    f"{parameter} would be clamped to a lower bound-safe value."
                ]
                hold_risk = "medium"
                summary = "Direct tunable change would likely be clamped; revise or acknowledge instead."
                reasons.append(f"{parameter} magnitude exceeds safe preview threshold.")
            if isinstance(active_clamps, (int, float)) and active_clamps >= 2:
                would_clamp = True
                hold_risk = "high"
                expected_clamps.append(
                    "Existing clamp activity suggests Verdify would likely reject or clamp the change."
                )
                reasons.append("Recent clamp activity is already elevated.")
        elif selected_action == "acknowledge_trigger":
            summary = "Acknowledge-only proposal is operationally neutral."
        elif selected_action == "fail":
            summary = "Fail-closed proposal returns no action to Verdify."
        if reasons and selected_action in {"set_plan", "set_tunable"}:
            if any(
                "stale" in reason.lower()
                or "telemetry" in reason.lower()
                or "weak context" in reason.lower()
                for reason in reasons
            ):
                outcome = "fail_closed"
                summary = "Proposal failed closed before return because context quality was too weak."
                hold_risk = "high"
                selected_action = "fail"
            elif would_clamp:
                outcome = "revise"
            else:
                outcome = "pass"
        next_state = copy_state(state)
        next_state["current_step"] = "guardrail_preview"
        next_state["guardrail_preview"] = {
            "would_clamp": would_clamp,
            "summary": summary,
        }
        next_state["guardrail_reasons"] = reasons
        next_state["guardrail_outcome"] = outcome
        next_state["expected_clamps"] = expected_clamps
        next_state["hold_risk"] = hold_risk
        next_state["transition_audit_refs"] = ["audit-planner-001"]
        if outcome == "fail_closed":
            guardrail_summary = (
                "; ".join(reasons)
                if reasons
                else "Guardrail preview rejected the proposal."
            )
            next_state["draft_action"] = "fail"
            next_state["selected_action"] = "fail"
            next_state["fail_closed_reason"] = guardrail_summary
            next_state["draft_rationale"] = (
                f"Planner failed closed before return because guardrail preview found unsafe context: {guardrail_summary}"
            )
            next_state["expected_effect"] = (
                "No action proposed. Verdify should retain local control."
            )
        runtime.hooks.update_run_context(
            selected_action=next_state.get("selected_action"),
            guardrail_outcome=outcome,
            guardrail_reasons=list(reasons),
        )
        next_state["updated_at"] = utc_now()
        return next_state

    return guardrail_preview
