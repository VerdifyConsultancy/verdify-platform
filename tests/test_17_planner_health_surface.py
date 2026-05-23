from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from verdify_schemas import PublicPlannerHealthResponse


def _trigger_payload() -> dict:
    now = datetime.now(UTC)
    return {
        "id": 1,
        "event_type": "SUNRISE",
        "event_label": "Morning planning cycle",
        "instance": "local",
        "expected_at": now - timedelta(minutes=10),
        "due_at": now + timedelta(minutes=20),
        "delivered_at": now - timedelta(minutes=9),
        "resolved_at": now - timedelta(minutes=5),
        "status": "plan_written",
        "expected_action": "set_plan",
        "trigger_id": "00000000-0000-0000-0000-000000000000",
        "resulting_plan_id": "iris-test",
    }


def test_public_planner_health_schema_includes_status_surface_fields():
    now = datetime.now(UTC)
    trigger = _trigger_payload()

    response = PublicPlannerHealthResponse.model_validate(
        {
            "generated_at": now,
            "overall_status": "ok",
            "missed_expected_count": 0,
            "overdue_delivered_count": 0,
            "required_failure_count": 0,
            "recent_expected_count": 1,
            "resolved_count": 1,
            "latest_required": [trigger],
            "last_expected_trigger": trigger,
            "last_delivered_trigger": trigger,
            "last_resolved_trigger": trigger,
            "pending_by_sla_age": {
                "within_sla": 2,
                "overdue_lt_15m": 0,
                "overdue_15m_1h": 0,
                "overdue_gt_1h": 0,
            },
            "current_session_key": "hermes:iris:main:trigger:00000000-0000-0000-0000-000000000000",
            "current_model_label": "hermes-iris/openai:gpt-5.5/high",
            "current_hermes_run_id": "run_test",
            "active_plan_range_violation_count": 0,
            "recent_triggers": [trigger],
        }
    )

    assert response.last_expected_trigger is not None
    assert response.last_delivered_trigger is not None
    assert response.last_resolved_trigger is not None
    assert response.pending_by_sla_age["within_sla"] == 2
    assert response.current_model_label == "hermes-iris/openai:gpt-5.5/high"
    assert response.active_plan_range_violation_count == 0


def test_public_planner_health_endpoint_queries_i_p1_1_sources():
    api_source = Path("api/main.py").read_text()
    endpoint = api_source[api_source.index("async def public_planner_health") :]

    for expected in (
        "last_expected_trigger",
        "last_delivered_trigger",
        "last_resolved_trigger",
        "pending_by_sla_age",
        "current_session_key",
        "current_model_label",
        "current_hermes_run_id",
        "active_plan_range_violation_count",
        "plan_delivery_log",
        "session_key",
    ):
        assert expected in endpoint
    assert "registry_value_error" in api_source
