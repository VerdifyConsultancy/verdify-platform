"""API and end-to-end planner behavior tests.

This file exercises the main HTTP contract and the planner's high-level runtime
behavior, including submission, completion, and proposal shaping. It connects
the application boundary to confidence that the full planner stack works together.
"""

from __future__ import annotations

import time
from threading import Event
from typing import cast
from uuid import uuid4

from fastapi.testclient import TestClient
from tests.helpers import tier1_active_plan_summary

from planner_graph.app import PlannerService, create_app
from planner_graph.clients.openai import OpenAIPlannerClient
from planner_graph.runtime import ExecutionHooks
from planner_graph.state import PROTECTED_MCP_TOOLS
from planner_graph.verdify_contract import CLIMATE_INTENT_FIELD_NAMES


def planner_request(
    trigger_id: str,
    *,
    event_type: str = "SUNRISE",
    alerts_summary: list[str] | None = None,
    context_overrides: dict[str, object] | None = None,
) -> dict[str, object]:
    context: dict[str, object] = {
        "climate_snapshot": {"temp_f": 72.5, "vpd_kpa": 1.1, "rh_pct": 60},
        "scorecard_summary": {"planner_score": 80.0, "compliance_pct": 90.0},
        "forecast_summary": {
            "headline": "Hot and dry afternoon expected",
            "max_vpd_kpa": 1.8,
        },
        "active_plan_summary": tier1_active_plan_summary(future_waypoints=3),
        "alerts_summary": alerts_summary or ["warning: no blocking alerts"],
        "clamp_summary": {"active_clamps_24h": 0},
        "guardrail_audit_summary": {"readback_freshness_seconds": 45},
        "recent_delivery_summary": {"last_delivery_status": "not_delivered"},
        "operator_notes": ["Prefer narrow changes and hold if context is weak."],
        "retrieval_refs": [{"id": "lesson-1", "snippet": "Watch afternoon VPD peaks."}],
        "site_refs": [
            {
                "id": "playbook-1",
                "snippet": "Sunrise plans should bias for midday stress.",
            }
        ],
    }
    if context_overrides:
        context.update(context_overrides)
    return {
        "trigger": {
            "trigger_id": trigger_id,
            "greenhouse_id": "vallery",
            "event_type": event_type,
            "event_label": f"{event_type.title()} planning cycle",
            "expected_action": "set_plan",
            "triggered_at": "2026-05-19T06:00:00-06:00",
            "planner_instance": "planner_graph",
            "source": "solar",
        },
        "planner": {
            "run_mode": "production",
            "contract_version": "2026-05-24",
            "context_version": "v1",
            "request_id": f"req-{trigger_id[:8]}",
            "trace_id": f"trace-{trigger_id[:8]}",
        },
        "context": context,
    }


def wait_for_terminal(
    client: TestClient, trigger_id: str, timeout: float = 2.0
) -> dict[str, object]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        response = client.get(f"/planner-runs/{trigger_id}")
        payload = response.json()
        if payload["status"] in {"completed", "failed"}:
            return payload
        time.sleep(0.02)
    raise AssertionError("run did not reach a terminal state in time")


def response_state(payload: dict[str, object]) -> dict[str, object]:
    return cast(dict[str, object], payload["state"])


def primary_action(payload: dict[str, object]) -> dict[str, object]:
    return cast(dict[str, object], payload["primary_action"])


def diagnosis(payload: dict[str, object]) -> dict[str, object]:
    return cast(dict[str, object], payload["diagnosis"])


def validation_summary(payload: dict[str, object]) -> dict[str, object]:
    return cast(dict[str, object], payload["validation_summary"])


def guardrail_preview(payload: dict[str, object]) -> dict[str, object]:
    return cast(dict[str, object], payload["guardrail_preview"])


def planner_metadata(payload: dict[str, object]) -> dict[str, object]:
    return cast(dict[str, object], payload["planner_metadata"])


def test_health_endpoint_reports_production_service() -> None:
    with TestClient(create_app()) as client:
        deadline = time.time() + 1
        while True:
            response = client.get("/health")
            if response.status_code == 200 or time.time() >= deadline:
                break

    assert response.status_code == 200
    assert response.json() == {
        "service": "ok",
        "private_api": True,
        "default_run_mode": "production",
        "worker": "ready",
        "db": "ok",
        "openai": "fallback",
        "mcp": "verdify-executes",
        "checkpoint": "in-memory",
        "production_authority": "non-authoritative",
        "consecutive_store_failures": 0,
        "retry_delay_seconds": 0.0,
        "last_error_class": None,
    }


def test_liveness_is_process_only_and_explicitly_non_authoritative() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/livez")

    assert response.status_code == 200
    assert response.json() == {"live": True, "production_authority": "non-authoritative"}


def test_planner_run_endpoint_accepts_request_and_returns_quickly() -> None:
    release = Event()
    started = Event()
    hooks = ExecutionHooks(
        pause_before_node="trigger_intake",
        release_event=release,
        started_event=started,
    )
    trigger_id = str(uuid4())

    with TestClient(create_app(hooks=hooks)) as client:
        started_at = time.perf_counter()
        response = client.post("/planner-runs", json=planner_request(trigger_id))
        elapsed = time.perf_counter() - started_at
        assert started.wait(timeout=1)
        release.set()
        terminal = wait_for_terminal(client, trigger_id)

    assert response.status_code == 202
    assert elapsed < 0.25
    assert terminal["thread_id"] == trigger_id


def test_run_status_returns_structured_proposal_sections() -> None:
    trigger_id = str(uuid4())

    with TestClient(create_app()) as client:
        client.post("/planner-runs", json=planner_request(trigger_id))
        payload = wait_for_terminal(client, trigger_id)

    action = primary_action(payload)
    diag = diagnosis(payload)
    validation = validation_summary(payload)
    guardrail = guardrail_preview(payload)
    metadata = planner_metadata(payload)
    assert payload["thread_id"] == trigger_id
    assert diag["planning_intent"]
    assert action["action_type"] == "set_plan"
    action_payload = cast(dict[str, object], action["payload"])
    assert action_payload["trigger_id"] == trigger_id
    assert action_payload["plan_id"] == "iris-20260519-0600"
    transitions = cast(list[dict[str, object]], action_payload["transitions"])
    intent = cast(dict[str, object], transitions[0]["climate_intent"])
    assert tuple(intent) == CLIMATE_INTENT_FIELD_NAMES
    assert "future_waypoints" not in intent
    assert "params" not in transitions[0]
    assert validation["validation_status"] == "passed"
    assert validation["tier1_coverage_status"] == "climate_intent"
    assert guardrail["summary"]
    assert metadata["contract_version"] == "2026-05-24"


def test_duplicate_run_requests_keep_same_thread_id() -> None:
    release = Event()
    hooks = ExecutionHooks(pause_before_node="trigger_intake", release_event=release)
    trigger_id = str(uuid4())

    with TestClient(create_app(hooks=hooks)) as client:
        first = client.post("/planner-runs", json=planner_request(trigger_id))
        second = client.post("/planner-runs", json=planner_request(trigger_id))
        release.set()
        terminal = wait_for_terminal(client, trigger_id)

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["thread_id"] == trigger_id
    assert second.json()["thread_id"] == trigger_id
    assert terminal["thread_id"] == trigger_id


def test_worker_owns_execution_not_request_handler() -> None:
    trigger_id = str(uuid4())
    service = PlannerService()

    with TestClient(create_app(service=service)) as client:
        response = client.post("/planner-runs", json=planner_request(trigger_id))
        payload = wait_for_terminal(client, trigger_id)

    assert response.status_code == 202
    assert payload["execution_owner"].startswith("planner-worker:")
    assert payload["status"] == "completed"


def test_planner_never_calls_production_mcp_write_tools() -> None:
    trigger_id = str(uuid4())
    service = PlannerService()

    with TestClient(create_app(service=service)) as client:
        client.post("/planner-runs", json=planner_request(trigger_id))
        payload = wait_for_terminal(client, trigger_id)

    action = primary_action(payload)
    state = response_state(payload)
    assert cast(dict[str, object], action["payload"])["trigger_id"] == trigger_id
    assert cast(dict[str, object], state["mcp_result"])["write_skipped"] is True
    assert service.runtime.mcp.calls == []
    assert PROTECTED_MCP_TOOLS == {
        "set_plan",
        "set_tunable",
        "acknowledge_trigger",
        "plan_evaluate",
    }


def test_guardrail_revision_revises_tunable_plan_to_acknowledge() -> None:
    trigger_id = str(uuid4())

    with TestClient(create_app()) as client:
        client.post(
            "/planner-runs",
            json=planner_request(
                trigger_id,
                event_type="ALERT",
                alerts_summary=["critical: VPD excursion risk"],
            ),
        )
        payload = wait_for_terminal(client, trigger_id)

    action = primary_action(payload)
    state = response_state(payload)
    guardrail = guardrail_preview(payload)
    assert action["action_type"] == "acknowledge_trigger"
    assert guardrail["summary"] == "Acknowledge-only proposal is operationally neutral."
    assert state["selected_action"] == "acknowledge_trigger"
    assert state["validation_status"] == "passed"


def test_invalid_proposal_fails_closed_without_breaking_contract() -> None:
    class InvalidPlanner(OpenAIPlannerClient):
        def diagnose(self, state: dict[str, object]) -> dict[str, object]:
            return {
                "situation": "invalid planner test",
                "likely_cause": "forced invalid action",
                "risks": ["test risk"],
                "planning_intent": "exercise fail-closed branch",
            }

        def draft_plan(self, state: dict[str, object]) -> dict[str, object]:
            return {
                "selected_action": "unsupported_action",
                "rationale": "invalid branch test",
                "confidence": 0.1,
                "tunable_changes": {},
                "expected_effect": "none",
            }

    trigger_id = str(uuid4())
    service = PlannerService()
    service.runtime.openai = InvalidPlanner()

    with TestClient(create_app(service=service)) as client:
        client.post("/planner-runs", json=planner_request(trigger_id))
        payload = wait_for_terminal(client, trigger_id)

    action = primary_action(payload)
    state = response_state(payload)
    assert payload["status"] == "completed"
    assert payload["terminal_status"] == "proposal_failed"
    assert action["action_type"] == "fail"
    assert state["validation_status"] == "failed"
    assert state["selected_action"] == "fail"
    validation_errors = cast(
        list[str], cast(dict[str, object], action["payload"])["validation_errors"]
    )
    assert "Unsupported planner action." in validation_errors


def test_set_plan_uses_climate_intent_without_complete_tier1_params() -> None:
    trigger_id = str(uuid4())

    with TestClient(create_app()) as client:
        client.post(
            "/planner-runs",
            json=planner_request(
                trigger_id,
                context_overrides={
                    "active_plan_summary": {
                        "temp_low": 66.0,
                        "temp_high": 84.0,
                        "future_waypoints": 3,
                    },
                },
            ),
        )
        payload = wait_for_terminal(client, trigger_id)

    action = primary_action(payload)
    state = response_state(payload)
    validation = validation_summary(payload)
    assert action["action_type"] == "set_plan"
    assert state["validation_status"] == "passed"
    assert validation["tier1_coverage_status"] == "climate_intent"
    action_payload = cast(dict[str, object], action["payload"])
    transitions = cast(list[dict[str, object]], action_payload["transitions"])
    intent = cast(dict[str, object], transitions[0]["climate_intent"])
    assert intent["temp_target_f"] == 75.0
    assert intent["temp_band_f"] == 12.0


def test_stale_context_fails_closed_before_return() -> None:
    trigger_id = str(uuid4())

    with TestClient(create_app()) as client:
        client.post(
            "/planner-runs",
            json=planner_request(
                trigger_id,
                event_type="ALERT",
                alerts_summary=["critical: telemetry stale after network flap"],
                context_overrides={
                    "guardrail_audit_summary": {"readback_freshness_seconds": 2400},
                },
            ),
        )
        payload = wait_for_terminal(client, trigger_id)

    action = primary_action(payload)
    state = response_state(payload)
    assert action["action_type"] == "fail"
    assert state["guardrail_outcome"] == "fail_closed"
    assert "Readback freshness is too stale for a control proposal." in cast(
        list[str], state["guardrail_reasons"]
    )
    assert "Weak context: readback is stale." in cast(
        list[str], state["guardrail_reasons"]
    )


def test_overassertive_rationale_is_rejected_by_contract_validation() -> None:
    class CertainPlanner(OpenAIPlannerClient):
        def diagnose(self, state: dict[str, object]) -> dict[str, object]:
            return {
                "situation": "certainty test",
                "likely_cause": "forced language",
                "risks": ["test risk"],
                "planning_intent": "exercise contract validation",
            }

        def draft_plan(self, state: dict[str, object]) -> dict[str, object]:
            return {
                "selected_action": "set_plan",
                "rationale": "This guarantees the greenhouse will remain compliant.",
                "confidence": 0.8,
                "tunable_changes": {},
                "expected_effect": "Guaranteed stable outcome after execution validation.",
            }

    trigger_id = str(uuid4())
    service = PlannerService()
    service.runtime.openai = CertainPlanner()

    with TestClient(create_app(service=service)) as client:
        client.post("/planner-runs", json=planner_request(trigger_id))
        payload = wait_for_terminal(client, trigger_id)

    action = primary_action(payload)
    state = response_state(payload)
    assert action["action_type"] == "fail"
    assert state["validation_status"] == "failed"
    assert "Proposal language is too certain for a production planner." in cast(
        list[str], state["validation_errors"]
    )
