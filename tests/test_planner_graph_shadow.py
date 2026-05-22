from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "ingestor"))

from planner_graph_shadow import (  # noqa: E402
    build_planner_request,
    compare_outputs,
    default_active_plan_summary,
    eligible_event,
    validate_remote_action,
)

from verdify_schemas.tunable_registry import REGISTRY, TIER1_REG  # noqa: E402


def _context() -> str:
    return """
=== GREENHOUSE PLANNING CONTEXT ===
--- SYSTEM HEALTH ---
ok
--- ZONE CONDITIONS ---
temp_f=72.5 vpd_kpa=1.1 outdoor_lux=45000
--- PLANNER SCORECARD (today) ---
planner_score=80 compliance_pct=90
--- ACTIVE PLAN (future transitions only) ---
3 waypoints
--- FORECAST ALERTS ---
warning: hot and dry afternoon
--- RECENT CLAMPS (dispatcher rejections, last 24h, top 10 params) ---
none
--- GUARDRAIL-AWARE TRANSITION AUDIT (last 36h) ---
readbacks fresh
--- YOUR RECENT DELIVERIES (last 24h from plan_delivery_log) ---
SUNRISE plan_written
"""


def _full_plan_payload(trigger_id: str, plan_id: str = "iris-20260520-1200") -> dict[str, object]:
    params = {name: float(REGISTRY[name].default) for name in sorted(TIER1_REG)}
    return {
        "plan_id": plan_id,
        "hypothesis": "Shadow validation plan",
        "experiment": None,
        "expected_outcome": "Plan validates structurally.",
        "trigger_id": trigger_id,
        "planner_instance": "planner_graph",
        "transitions": [
            {
                "ts": "2026-05-20T12:00:00-06:00",
                "params": params,
                "reason": "Fixture transition with full Tier 1 coverage.",
            }
        ],
    }


def test_build_planner_request_preserves_trigger_id_and_required_sections():
    trigger_id = str(uuid.uuid4())

    payload = build_planner_request(
        event_type="SUNRISE",
        event_label="Sunrise planning cycle",
        context=_context(),
        trigger_id=trigger_id,
        planner_instance="local",
    )

    assert payload["trigger"]["trigger_id"] == trigger_id
    assert payload["trigger"]["expected_action"] == "set_plan"
    assert payload["planner"]["run_mode"] == "shadow"
    assert payload["planner"]["contract_version"] == "2026-05-19"
    assert set(payload["context"]) >= {
        "climate_snapshot",
        "scorecard_summary",
        "forecast_summary",
        "active_plan_summary",
        "alerts_summary",
        "clamp_summary",
        "guardrail_audit_summary",
    }
    assert "outdoor_lux=45000" in payload["context"]["climate_snapshot"]["summary"]


def test_validate_remote_action_rejects_bad_plan_id():
    trigger_id = str(uuid.uuid4())
    action = {
        "action_type": "set_plan",
        "payload": _full_plan_payload(trigger_id, plan_id="iris-not-a-timestamp"),
        "rationale": "bad fixture",
    }

    outcome = validate_remote_action(action, trigger_id)

    assert outcome["would_accept_remote"] is False
    assert any("Plan validation failed" in reason for reason in outcome["rejection_reasons"])


def test_build_planner_request_can_send_execution_shaped_active_plan_summary():
    trigger_id = str(uuid.uuid4())
    active = default_active_plan_summary()

    payload = build_planner_request(
        event_type="SUNRISE",
        event_label="Sunrise planning cycle",
        context=_context(),
        trigger_id=trigger_id,
        planner_instance="local",
        active_plan_summary=active,
    )

    assert payload["context"]["active_plan_summary"] == active
    assert set(payload["context"]["active_plan_summary"]) == set(TIER1_REG)


def test_validate_remote_action_accepts_full_set_plan_shape():
    trigger_id = str(uuid.uuid4())
    action = {
        "action_type": "set_plan",
        "payload": _full_plan_payload(trigger_id),
        "rationale": "valid fixture",
    }

    outcome = validate_remote_action(action, trigger_id)

    assert outcome == {"would_accept_remote": True, "rejection_reasons": []}


def test_validate_remote_action_accepts_transitions_json_string():
    trigger_id = str(uuid.uuid4())
    payload = _full_plan_payload(trigger_id)
    payload["transitions"] = json.dumps(payload["transitions"])
    action = {
        "action_type": "set_plan",
        "payload": payload,
        "rationale": "valid fixture",
    }

    outcome = validate_remote_action(action, trigger_id)

    assert outcome["would_accept_remote"] is True


def test_compare_outputs_flags_remote_rejection_as_worse():
    trigger_id = str(uuid.uuid4())
    remote = {
        "status": "completed",
        "primary_action": {
            "action_type": "set_plan",
            "payload": _full_plan_payload(trigger_id, plan_id="iris-bad"),
            "rationale": "bad fixture",
        },
    }
    validation = {"would_accept_remote": False, "rejection_reasons": ["bad plan_id"]}
    local = {"action_type": "set_plan", "payload": _full_plan_payload(trigger_id)}

    diff = compare_outputs(remote_terminal=remote, local_output=local, validation=validation)

    assert diff["remote_action_type"] == "set_plan"
    assert diff["local_action_type"] == "set_plan"
    assert diff["would_accept_remote"] is False
    assert diff["judgement"] == "worse"


def test_event_filter_defaults_to_required_shadow_cohort(monkeypatch):
    monkeypatch.delenv("PLANNER_GRAPH_SHADOW_EVENT_TYPES", raising=False)

    assert eligible_event("SUNRISE") is True
    assert eligible_event("HEARTBEAT") is False
