"""Prompt definitions and prompt builders for the LLM planner path.

This module holds the reusable system prompts and context packing rules for the
OpenAI-backed planner flow. It connects model-facing prompt design to planner
quality work such as evals, prompt iteration, and bounded context shaping.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import cast


PROMPT_VERSION = "2026-05-20.v2"

DIAGNOSIS_SYSTEM_PROMPT = """You are planner_graph, Verdify's production planning service.

Your job is to interpret a bounded Verdify planning request and summarize:
- the current situation
- the likely cause or decision driver
- the main planning risks
- the planning intent

Rules:
- You are a planner, not an actuator.
- Do not recommend bypassing Verdify validation or dispatcher authority.
- Ground your reasoning in the provided context pack, not generic greenhouse advice.
- Keep risks concrete and decision-relevant.
- Do not invent missing telemetry, missing policies, or missing downstream results.
"""

PROPOSAL_SYSTEM_PROMPT = """You are planner_graph, Verdify's production planning service.

Return exactly one bounded action payload for Verdify to validate and execute locally.

Available action types:
- set_plan
- set_tunable
- acknowledge_trigger
- fail

Decision rules:
- Prefer the narrowest action that reasonably addresses the trigger.
- Use set_plan for schedule/trajectory changes across time.
- Use set_tunable for a specific parameter change only when the request clearly supports it.
- Use acknowledge_trigger when no safe control change is justified from the provided context.
- Use fail when the planner cannot produce a coherent proposal from the available context.
- If the context is weak, stale, contradictory, or missing execution-critical evidence, choose acknowledge_trigger or fail instead of inventing control changes.
- If you are unsure between a control action and a no-op, fail closed toward the no-op.

Contract rules:
- Mirror Verdify's downstream execution payload shape as closely as possible.
- Do not propose direct greenhouse writes.
- Do not claim the action is approved for execution.
- Do not describe Verdify validation as already passed, guaranteed, or certain.
- Keep rationale concrete and grounded in the provided context.
- expected_effect should describe the intended operational outcome, not a certainty.
- For acknowledge_trigger and fail, tunable_changes must be empty.
- For set_plan, tunable_changes must be empty and each transition must use bounded climate_intent.
- For set_tunable, include only one narrow tunable change and avoid speculative magnitude jumps.
- Never turn the planner into an actuator, dispatcher, or validator.
"""


@dataclass(frozen=True)
class PromptBundle:
    version: str = PROMPT_VERSION
    diagnosis_system_prompt: str = DIAGNOSIS_SYSTEM_PROMPT
    proposal_system_prompt: str = PROPOSAL_SYSTEM_PROMPT


def weak_context_signals(state: dict[str, object]) -> list[str]:
    weaknesses: list[str] = []
    climate_snapshot = state.get("climate_snapshot")
    if not isinstance(climate_snapshot, dict) or not climate_snapshot:
        weaknesses.append("climate snapshot missing")
    forecast_summary = state.get("forecast_summary")
    if not isinstance(forecast_summary, dict) or not forecast_summary:
        weaknesses.append("forecast summary missing")
    active_plan_summary = state.get("active_plan_summary")
    if not isinstance(active_plan_summary, dict) or not active_plan_summary:
        weaknesses.append("active plan summary missing")
    guardrail_audit_summary = state.get("guardrail_audit_summary")
    if isinstance(guardrail_audit_summary, dict):
        freshness = guardrail_audit_summary.get("readback_freshness_seconds")
        if isinstance(freshness, (int, float)) and freshness > 900:
            weaknesses.append("readback is stale")
    alerts_summary = state.get("alerts_summary")
    if isinstance(alerts_summary, list):
        lowered = " ".join(str(item).lower() for item in alerts_summary)
        if "sensor offline" in lowered or "telemetry stale" in lowered:
            weaknesses.append("telemetry quality alert present")
    return weaknesses


def bounded_planner_context(
    state: dict[str, object], *, include_diagnosis: bool
) -> dict[str, object]:
    weak_signals = weak_context_signals(state)
    bounded_state: dict[str, object] = {
        "prompt_version": PROMPT_VERSION,
        "trigger_id": state.get("trigger_id"),
        "greenhouse_id": state.get("greenhouse_id"),
        "event_type": state.get("event_type"),
        "event_label": state.get("event_label"),
        "expected_action": state.get("expected_action"),
        "triggered_at": state.get("triggered_at"),
        "planner_instance": state.get("planner_instance"),
        "climate_snapshot": state.get("climate_snapshot", {}),
        "scorecard_summary": state.get("scorecard_summary", {}),
        "forecast_summary": state.get("forecast_summary", {}),
        "active_plan_summary": state.get("active_plan_summary", {}),
        "alerts_summary": state.get("alerts_summary", []),
        "clamp_summary": state.get("clamp_summary", {}),
        "guardrail_audit_summary": state.get("guardrail_audit_summary", {}),
        "recent_delivery_summary": state.get("recent_delivery_summary", {}),
        "operator_notes": state.get("operator_notes", []),
        "retrieved_lessons": state.get("retrieved_lessons", []),
        "retrieved_docs": state.get("retrieved_docs", []),
        "retrieved_plan_refs": state.get("retrieved_plan_refs", []),
        "context_completeness": state.get("context_completeness", "unknown"),
        "context_weaknesses": state.get("context_weaknesses", weak_signals),
        "planner_policy": {
            "single_controller_path": True,
            "verdify_executes": True,
            "prefer_narrow_actions": True,
            "fail_closed_on_weak_context": True,
        },
    }
    if include_diagnosis:
        bounded_state["diagnosis"] = state.get("diagnosis", {})
    return bounded_state


def planner_user_prompt(state: dict[str, object], *, include_diagnosis: bool) -> str:
    return (
        "Use this bounded greenhouse planning state to produce one production planning payload.\n"
        f"{json.dumps(bounded_planner_context(state, include_diagnosis=include_diagnosis), sort_keys=True, default=str)}"
    )


def extract_selected_action(draft: dict[str, object]) -> str:
    return cast(str, draft["selected_action"])
