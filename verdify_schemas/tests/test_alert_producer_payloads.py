"""Round-trip the EXACT alert payload shapes the ingestor producers build.

These payloads are literal copies of what the producers construct (captured
from prod logs on 2026-07-11, when both shapes were being silently dropped by
``extra=forbid`` validation). If a producer adds a field, add it HERE and to
the details model in the same PR — this file is the drift guard that makes
that contract explicit.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from verdify_schemas.alerts import (
    AlertEnvelope,
    ESP32PushFailedDetails,
    build_validation_failed_envelope,
)

NOW = "2026-07-11T03:00:54+00:00"


def test_dispatcher_esp32_push_failed_terminal_shape():
    """ingestor/tasks/dispatcher.py terminal-failure alert (no `error` field).

    This exact shape crashed the setpoint_dispatch task every cycle between
    2026-07-10 and 2026-07-11 (161 crashes/2h) because the schema required
    `error` and forbade `failure_reasons`/`parameters`.
    """
    env = AlertEnvelope.model_validate(
        {
            "alert_type": "esp32_push_failed",
            "severity": "warning",
            "category": "system",
            "message": "ESP32 direct push reached terminal failure for 4 command(s)",
            "details": {
                "failure_reasons": ["unroutable"],
                "parameters": [
                    "irrig_center_days_mask",
                    "irrig_center_fert_days_mask",
                    "irrig_wall_days_mask",
                    "irrig_wall_fert_days_mask",
                ],
                "change_count": 4,
            },
        }
    )
    assert env.details["failure_reasons"] == ["unroutable"]
    assert env.details["change_count"] == 4


def test_esp32_push_failed_legacy_error_shape_still_validates():
    env = AlertEnvelope.model_validate(
        {
            "alert_type": "esp32_push_failed",
            "severity": "warning",
            "category": "system",
            "message": "ESP32 direct push failed",
            "details": {"error": "timeout", "change_count": 3},
        }
    )
    assert env.details["error"] == "timeout"


def test_esp32_push_failed_rejects_reasonless_payload():
    with pytest.raises(ValidationError):
        ESP32PushFailedDetails.model_validate({"change_count": 1})


def test_heartbeat_required_plan_missed_shape():
    """ingestor/tasks/alerts.py miss dict incl. the #427 ledger columns.

    terminal_action/failure_class were added to the producer by the 2026-07-10
    delivery rewrite; their absence from the schema silently dropped every
    planner_required_plan_missed CRITICAL (verified in prod logs 2026-07-11
    09:08:29Z — last night's real SUNSET miss paged nobody).
    """
    env = AlertEnvelope.model_validate(
        {
            "alert_type": "planner_required_plan_missed",
            "severity": "critical",
            "category": "system",
            "sensor_id": "system.planner",
            "message": "SUNSET did not produce a plan by SLA (status=missed due=2026-07-11T03:00:54Z)",
            "details": {
                "misses": [
                    {
                        "id": 341631,
                        "event_type": "SUNSET",
                        "event_label": "Evening planning",
                        "instance": None,
                        "status": "missed",
                        "gateway_status": None,
                        "expected_at": "2026-07-11T02:30:54+00:00",
                        "due_at": NOW,
                        "delivered_at": None,
                        "gateway_body": "",
                        "plan_delivery_log_id": None,
                        "trigger_id": None,
                        "resulting_plan_id": None,
                        "terminal_action": "timeout",
                        "failure_class": "expected_trigger_not_delivered",
                        "had_required_failure": True,
                    }
                ]
            },
        }
    )
    assert env.severity == "critical"
    assert env.details["misses"][0]["failure_class"] == "expected_trigger_not_delivered"
    assert env.details["misses"][0]["had_required_failure"] is True


def test_required_plan_missed_legacy_payload_without_failure_history():
    """Earlier alert producers did not include the ledger failure-history flag."""
    env = AlertEnvelope.model_validate(
        {
            "alert_type": "planner_required_plan_missed",
            "severity": "critical",
            "category": "system",
            "message": "SUNRISE did not produce a plan by SLA",
            "details": {
                "misses": [
                    {
                        "id": 341631,
                        "event_type": "SUNRISE",
                        "status": "neutral_fallback",
                        "gateway_body": "",
                    }
                ]
            },
        }
    )
    assert env.details["misses"][0]["had_required_failure"] is False


def test_validation_failed_fallback_preserves_critical_severity():
    bad_payload = {
        "alert_type": "planner_required_plan_missed",
        "severity": "critical",
        "category": "system",
        "sensor_id": "system.planner",
        "message": "SUNSET did not produce a plan by SLA",
        "details": {
            "misses": [
                {"id": 1, "event_type": "SUNSET", "status": "missed", "gateway_body": "", "some_future_field": True}
            ]
        },
    }
    try:
        AlertEnvelope.model_validate(bad_payload)
        raise AssertionError("payload was expected to fail validation")
    except ValidationError as e:
        env = build_validation_failed_envelope(bad_payload, e, producer="alert_monitor")
    assert env.alert_type == "alert_validation_failed"
    assert env.severity == "critical"
    assert env.details["original_alert_type"] == "planner_required_plan_missed"
    assert "some_future_field" in env.details["validation_error"] or "extra" in env.details["validation_error"].lower()


def test_validation_failed_fallback_survives_garbage_payload():
    env = build_validation_failed_envelope({}, ValueError("boom"), producer="dispatcher")
    assert env.alert_type == "alert_validation_failed"
    assert env.severity == "warning"
    assert env.details["original_alert_type"] == "unknown"


def test_soil_probe_offline_per_probe_shape():
    """VD-72: one soil_sensor_offline row per probe, with the sync's lifecycle fields."""
    env = AlertEnvelope.model_validate(
        {
            "alert_type": "soil_sensor_offline",
            "severity": "warning",
            "category": "sensor",
            "sensor_id": "soil.west",
            "zone": "west",
            "message": "Soil probe `soil.west` (west zone) has no data for 30 min: soil_moisture_west, soil_temp_west",
            "details": {
                "sensor": "soil.west",
                "columns": ["soil_moisture_west", "soil_temp_west"],
                "occupancy": "occupied",
                "count": 30,
                "last_seen_at": NOW,
            },
            "metric_value": None,
            "threshold_value": 30.0,
        }
    )
    assert env.details["column"] is None and env.details["columns"] == ["soil_moisture_west", "soil_temp_west"]
    assert env.details["count"] == 30 and env.details["long_offline"] is False


def test_probe_offline_lifecycle_fields_on_stored_and_staleness_rows():
    legacy = {"column": "soil_moisture_west", "sensor": "soil.west", "occupancy": "occupied"}
    resolved = AlertEnvelope.model_validate(
        {
            "alert_type": "soil_sensor_offline",
            "severity": "warning",
            "category": "sensor",
            "message": "Soil sensor `soil_moisture_west` has no data for 30 min",
            "details": {**legacy, "long_offline": True},
        }
    )
    assert resolved.details["long_offline"] is True and resolved.details["count"] == 1
    stale = AlertEnvelope.model_validate(
        {
            "alert_type": "sensor_offline",
            "severity": "warning",
            "category": "sensor",
            "message": "Sensor `climate.hydro_ph` offline",
            "details": {"type": "hydroponic", "staleness_ratio": None, "count": 4, "last_seen_at": NOW},
        }
    )
    assert stale.details["count"] == 4 and stale.details["last_seen_at"].startswith("2026-07-11T03:00:54")
    with pytest.raises(ValidationError):
        AlertEnvelope.model_validate(
            {
                "alert_type": "sensor_offline",
                "severity": "warning",
                "category": "sensor",
                "message": "Sensor `climate.hydro_ph` offline",
                "details": {"type": "hydroponic", "count": 0},
            }
        )


def test_leak_detected_flow_stopped_shape():
    """VD-72: the leak row outlives the flag and records when the meter went quiet."""
    env = AlertEnvelope.model_validate(
        {
            "alert_type": "leak_detected",
            "severity": "critical",
            "category": "water",
            "sensor_id": "equipment.leak_detected",
            "message": "Flow stopped at 21:20",
            "details": {"since": NOW, "stopped_at": "2026-07-11T03:20:00+00:00"},
        }
    )
    assert env.details["stopped_at"].startswith("2026-07-11T03:20:00")
    flowing = AlertEnvelope.model_validate(
        {
            "alert_type": "leak_detected",
            "severity": "critical",
            "category": "water",
            "message": "Water flowing with every Verdify valve closed since 21:00",
            "details": {"since": NOW, "stopped_at": None},
        }
    )
    assert flowing.details["stopped_at"] is None
