"""Tests for prompt construction and context packing.

This file protects the planner's prompt layer from accidental drift by checking
that important context and prompt structure still appear where expected. It
connects prompt editing to stable downstream planner behavior.
"""

from __future__ import annotations

from planner_graph.prompts import (
    PROMPT_VERSION,
    bounded_planner_context,
    planner_user_prompt,
    weak_context_signals,
)
from tests.helpers import tier1_active_plan_summary


def sample_state() -> dict[str, object]:
    return {
        "trigger_id": "11111111-1111-1111-1111-111111111111",
        "greenhouse_id": "vallery",
        "event_type": "SUNRISE",
        "event_label": "Sunrise planning cycle",
        "expected_action": "set_plan",
        "triggered_at": "2026-05-19T06:00:00-06:00",
        "planner_instance": "planner_graph",
        "climate_snapshot": {"temp_f": 72.5},
        "scorecard_summary": {"planner_score": 80.0},
        "forecast_summary": {"headline": "Hot and dry afternoon expected"},
        "active_plan_summary": tier1_active_plan_summary(),
        "alerts_summary": ["warning: no blocking alerts"],
        "clamp_summary": {"active_clamps_24h": 0},
        "guardrail_audit_summary": {"readback_freshness_seconds": 45},
        "recent_delivery_summary": {"last_delivery_status": "not_delivered"},
        "operator_notes": ["Hold narrow proposals unless evidence is strong."],
        "retrieved_lessons": [
            {"id": "lesson-1", "snippet": "Watch afternoon VPD peaks."}
        ],
        "retrieved_docs": [
            {"id": "doc-1", "snippet": "Sunrise plans should bias for midday stress."}
        ],
        "retrieved_plan_refs": [
            {
                "id": "plan-1",
                "snippet": "Use the prior sunrise plan as a bounded reference.",
            }
        ],
    }


def test_bounded_planner_context_includes_prompt_version() -> None:
    bounded = bounded_planner_context(sample_state(), include_diagnosis=False)

    assert bounded["prompt_version"] == PROMPT_VERSION
    assert "diagnosis" not in bounded
    assert bounded["planner_policy"] == {
        "single_controller_path": True,
        "verdify_executes": True,
        "prefer_narrow_actions": True,
        "fail_closed_on_weak_context": True,
    }


def test_planner_user_prompt_includes_diagnosis_when_requested() -> None:
    state = sample_state()
    state["diagnosis"] = {"planning_intent": "Bias for midday stress window"}

    prompt = planner_user_prompt(state, include_diagnosis=True)

    assert "Bias for midday stress window" in prompt
    assert "recent_delivery_summary" in prompt
    assert "operator_notes" in prompt
    assert "retrieved_plan_refs" in prompt


def test_bounded_planner_context_keeps_plan_refs() -> None:
    bounded = bounded_planner_context(sample_state(), include_diagnosis=False)

    assert bounded["retrieved_plan_refs"] == [
        {
            "id": "plan-1",
            "snippet": "Use the prior sunrise plan as a bounded reference.",
        }
    ]


def test_weak_context_signals_identify_stale_readback_and_sensor_alerts() -> None:
    state = sample_state()
    state["guardrail_audit_summary"] = {"readback_freshness_seconds": 3600}
    state["alerts_summary"] = ["warning: sensor offline in zone 3"]

    assert weak_context_signals(state) == [
        "readback is stale",
        "telemetry quality alert present",
    ]
