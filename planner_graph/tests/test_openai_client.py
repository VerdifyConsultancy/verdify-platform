"""Tests for the OpenAI planner client and fallback behavior.

This file verifies the model adapter can handle both the real structured-output
path and the local deterministic fallback. It connects planner generation logic
to confidence that the client layer behaves predictably.
"""

from __future__ import annotations

import pytest

from planner_graph.clients.openai import OpenAIPlannerClient, PlannerLLMError
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
        "climate_snapshot": {"temp_f": 72.5, "vpd_kpa": 1.1, "rh_pct": 60},
        "scorecard_summary": {"planner_score": 80.0, "compliance_pct": 90.0},
        "forecast_summary": {
            "headline": "Hot and dry afternoon expected",
            "max_vpd_kpa": 1.8,
        },
        "active_plan_summary": tier1_active_plan_summary(future_waypoints=3),
        "alerts_summary": ["warning: no blocking alerts"],
        "clamp_summary": {"active_clamps_24h": 0},
        "guardrail_audit_summary": {"readback_freshness_seconds": 45},
    }


def test_openai_client_falls_back_without_api_key() -> None:
    client = OpenAIPlannerClient()

    diagnosis = client.diagnose(sample_state())
    draft = client.draft_plan(sample_state())

    assert "Fallback planner path" in str(diagnosis["likely_cause"])
    assert draft["selected_action"] == "set_plan"
    assert draft["confidence"] == 0.35


def test_openai_client_parses_structured_responses() -> None:
    client = OpenAIPlannerClient(api_key="test-key")
    responses = [
        {
            "output": [
                {
                    "content": [
                        {
                            "type": "output_text",
                            "text": (
                                '{"situation":"Dry afternoon expected",'
                                '"likely_cause":"Forecast VPD spike",'
                                '"risks":["Stress window"],'
                                '"planning_intent":"Bias toward proactive moisture control"}'
                            ),
                        }
                    ]
                }
            ]
        },
        {
            "output_text": (
                '{"selected_action":"set_tunable",'
                '"rationale":"Preempt VPD spike",'
                '"confidence":0.82,'
                '"tunable_changes":{"fog_escalation_kpa":0.4},'
                '"expected_effect":"Reduce dry-stress risk"}'
            )
        },
    ]

    def fake_post(payload: dict[str, object]) -> dict[str, object]:
        assert payload["model"] == "gpt-5.5"
        return responses.pop(0)

    client._post_responses = fake_post  # type: ignore[method-assign]
    state = sample_state()
    diagnosis = client.diagnose(state)
    state["diagnosis"] = diagnosis
    draft = client.draft_plan(state)

    assert diagnosis["likely_cause"] == "Forecast VPD spike"
    assert draft["selected_action"] == "set_tunable"
    assert draft["tunable_changes"] == {"fog_escalation_kpa": 0.4}


def test_openai_client_raises_on_refusal() -> None:
    client = OpenAIPlannerClient(api_key="test-key")

    client._post_responses = lambda payload: {"refusal": "safety refusal"}  # type: ignore[method-assign]

    with pytest.raises(PlannerLLMError, match="Model refusal"):
        client.diagnose(sample_state())
