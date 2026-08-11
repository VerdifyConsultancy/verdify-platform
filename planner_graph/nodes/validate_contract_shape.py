"""Planner-owned contract preflight for draft proposals.

This node checks whether the planner's draft is structurally coherent and safe
enough to return as a proposal before Verdify or another caller ever sees it.
It connects proposal generation to strict contract-shape validation.
"""

from __future__ import annotations

import math

from planner_graph.nodes import copy_state
from planner_graph.runtime import PlannerRuntime
from planner_graph.state import PlannerState, utc_now
from planner_graph.verdify_contract import build_climate_intent


OVER_ASSERTIVE_PHRASES = (
    "guarantee",
    "guaranteed",
    "certain",
    "definitely",
    "eliminate all risk",
    "validated",
    "safe to execute",
)


def build_validate_contract_shape(runtime: PlannerRuntime):
    def validate_contract_shape(state: PlannerState) -> PlannerState:
        runtime.hooks.before_node("validate_contract_shape")
        errors: list[str] = []
        tier1_coverage_status = "not_applicable"
        selected_action = state.get("draft_action")
        rationale = str(state.get("draft_rationale", "")).strip()
        expected_effect = str(state.get("expected_effect", "")).strip()
        tunable_changes = state.get("tunable_changes", {})
        draft_plan = state.get("draft_plan")
        confidence = (
            draft_plan.get("confidence", 0.0) if isinstance(draft_plan, dict) else 0.0
        )
        if selected_action not in {
            "set_plan",
            "set_tunable",
            "acknowledge_trigger",
            "fail",
        }:
            errors.append("Unsupported planner action.")
        if not rationale:
            errors.append("Proposal rationale is required.")
        if selected_action != "fail" and not expected_effect:
            errors.append("expected_effect is required for non-fail proposals.")
        if not isinstance(confidence, (str, int, float, bool)):
            errors.append("confidence must be numeric.")
        else:
            try:
                numeric_confidence = float(confidence)
                if (
                    not math.isfinite(numeric_confidence)
                    or numeric_confidence < 0
                    or numeric_confidence > 1
                ):
                    errors.append("confidence must be a finite value between 0 and 1.")
            except (TypeError, ValueError):
                errors.append("confidence must be numeric.")
        lowered_language = f"{rationale} {expected_effect}".lower()
        if any(phrase in lowered_language for phrase in OVER_ASSERTIVE_PHRASES):
            errors.append("Proposal language is too certain for a production planner.")
        if selected_action == "set_tunable":
            if not isinstance(tunable_changes, dict) or not tunable_changes:
                errors.append("set_tunable requires at least one tunable change.")
            elif len(tunable_changes) != 1:
                errors.append("set_tunable must propose exactly one tunable change.")
            else:
                parameter, raw_value = next(iter(tunable_changes.items()))
                if not isinstance(parameter, str) or not parameter.strip():
                    errors.append("set_tunable requires a named parameter.")
                if not isinstance(raw_value, (str, int, float, bool)):
                    errors.append("set_tunable value must be numeric.")
                else:
                    try:
                        numeric_value = float(raw_value)
                        if not math.isfinite(numeric_value):
                            errors.append("set_tunable value must be finite.")
                        if numeric_value <= 0:
                            errors.append(
                                "set_tunable value must be greater than zero."
                            )
                    except (TypeError, ValueError):
                        errors.append("set_tunable value must be numeric.")
        if selected_action == "set_plan":
            build_climate_intent(state.get("active_plan_summary"))
            tier1_coverage_status = "climate_intent"
            if isinstance(tunable_changes, dict) and tunable_changes:
                errors.append("set_plan must not include tunable changes.")
        if selected_action == "acknowledge_trigger":
            if not rationale:
                errors.append("acknowledge_trigger requires a rationale.")
            if isinstance(tunable_changes, dict) and tunable_changes:
                errors.append("acknowledge_trigger must not include tunable changes.")
        if (
            selected_action == "fail"
            and isinstance(tunable_changes, dict)
            and tunable_changes
        ):
            errors.append("fail must not include tunable changes.")
        next_state = copy_state(state)
        next_state["current_step"] = "validate_contract_shape"
        next_state["validation_status"] = "passed" if not errors else "failed"
        next_state["validation_errors"] = errors
        next_state["contract_shape_rejection_reasons"] = list(errors)
        next_state["registry_violations"] = []
        next_state["band_ownership_violations"] = []
        next_state["tier1_coverage_status"] = tier1_coverage_status
        next_state["updated_at"] = utc_now()
        runtime.hooks.update_run_context(contract_shape_rejection_reasons=list(errors))
        return next_state

    return validate_contract_shape
