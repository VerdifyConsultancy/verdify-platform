"""Drift guards for typed alert detail payloads."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from verdify_schemas.alerts import (
    ALERT_ENVELOPE_ADAPTER,
    ALERT_TYPES,
    AlertEnvelope,
    BandAnchorDbReadFailedAlert,
    BandDeviceDbDivergenceAlert,
    BandFnNullAlert,
    ClimateActionProofStaleAlert,
    ESP32PushFailedAlert,
    ESP32RebootAlert,
    FirmwareReliefCeilingAlert,
    FirmwareVentLatchedAlert,
    FirmwareVersionMismatchAlert,
    ForecastDeviationAlert,
    HeapPressureCriticalAlert,
    HeapPressureWarningAlert,
    HeatManualOverrideAlert,
    HeatStagingInversionAlert,
    HouseBandDriftAlert,
    IrrigationFeedbackGapAlert,
    LeakDetectedAlert,
    LightingCfgThresholdDriftAlert,
    PlanContextFailedAlert,
    PlannerBandOwnershipDriftAlert,
    PlannerEvaluationMissedAlert,
    PlannerGatewayDeliveryFailedAlert,
    PlannerPlanHorizonMissingAlert,
    PlannerRequiredPlanMissedAlert,
    PlannerStaleAlert,
    PlannerTriggerSlaTimeoutAlert,
    PlannerTunableRangeDriftAlert,
    RelayStuckAlert,
    SafetyInvalidAlert,
    SensorOfflineAlert,
    SetpointUnconfirmedAlert,
    SoilDryoutAlert,
    SoilSensorOfflineAlert,
    TempSafetyAlert,
    TunableZeroVarianceAlert,
    VentMoistureCapacityLimitAlert,
    VentVpdMoistureGapAlert,
    VpdExtremeAlert,
    VpdStressAlert,
)

NOW = "2026-05-01T12:00:00+00:00"

CASES = {
    "band_anchor_db_read_failed": (
        BandAnchorDbReadFailedAlert,
        {"origin": "defaults(table-error)"},
    ),
    "band_device_db_divergence": (
        BandDeviceDbDivergenceAlert,
        {"max_temp_abs_diff": 4.1, "max_vpd_abs_diff": 0.52, "device_age_s": 35.0},
    ),
    "band_fn_null": (
        BandFnNullAlert,
        {"band_row_null": True, "zone_row_null": False, "house_row_null": True},
    ),
    "climate_action_proof_stale": (
        ClimateActionProofStaleAlert,
        {"age_s": 360, "latest_ts": NOW, "proof_missing": "relay_truth"},
    ),
    "esp32_push_failed": (
        ESP32PushFailedAlert,
        {"error": "timeout", "change_count": 3},
    ),
    "esp32_reboot": (
        ESP32RebootAlert,
        {"uptime_s": 42.0, "reset_reason": "poweron"},
    ),
    "firmware_relief_ceiling": (
        FirmwareReliefCeilingAlert,
        {"relief_cycle_count": 3, "ceiling_default": 3},
    ),
    "firmware_vent_latched": (
        FirmwareVentLatchedAlert,
        {"vent_latch_timer_s": 1200},
    ),
    "firmware_version_mismatch": (
        FirmwareVersionMismatchAlert,
        {
            "expected_firmware_version": "2026.4.27.2040.c1a6403",
            "live_firmware_version": "2026.4.27.2009.2b5f2a5",
            "diagnostics_ts": NOW,
            "pin_source": "state/expected-firmware-version",
        },
    ),
    "forecast_deviation": (
        ForecastDeviationAlert,
        {
            "deviations": [
                {
                    "parameter": "solar_w_m2",
                    "observed": 190.0,
                    "forecasted": 720.0,
                    "delta": 530.0,
                    "threshold": 180.0,
                    "unit": "W/m2",
                    "cooldown_min": 45,
                    "triggered": True,
                    "normalized_excess": 1.944,
                    "recent_cycles": 2,
                }
            ],
            "reason": "Forecast deviation: solar_w_m2",
            "max_abs_deviation": 1.944,
            "consecutive_cycles": 2,
            "planner_event_type": "FORECAST_DEVIATION",
            "source": "forecast_deviation_check",
        },
    ),
    "heap_pressure_critical": (
        HeapPressureCriticalAlert,
        {
            "equipment": "heap_pressure_critical",
            "equipment_ts": NOW,
            "last_true_ts": NOW,
            "heap_free_kb": 12.5,
            "heap_min_free_kb": 3.0,
            "heap_largest_free_block_kb": 18.0,
            "heap_low_watermark_warning": True,
            "heap_fragmentation_warning": True,
            "heap_diag_ts": NOW,
            "critical_logs_30m": 1,
            "healthy_heap_samples_after_event": 0,
            "last_critical_log_ts": NOW,
            "last_critical_log_message": "Heap pressure CRITICAL",
        },
    ),
    "heap_pressure_warning": (
        HeapPressureWarningAlert,
        {
            "equipment": "heap_pressure_warning",
            "equipment_ts": NOW,
            "last_true_ts": NOW,
            "heap_free_kb": 28.5,
            "heap_min_free_kb": 3.0,
            "heap_largest_free_block_kb": 29.0,
            "heap_low_watermark_warning": True,
            "heap_fragmentation_warning": False,
            "heap_diag_ts": NOW,
            "warning_logs_30m": 1,
            "healthy_heap_samples_after_event": 2,
            "last_warning_log_ts": NOW,
            "last_warning_log_message": "Heap pressure WARNING",
        },
    ),
    "heat_manual_override": (
        HeatManualOverrideAlert,
        {"watts": 1200},
    ),
    "heat_staging_inversion": (
        HeatStagingInversionAlert,
        {
            "heat2_on_since": NOW,
            "duration_s": 90.0,
            "temp_avg": 61.2,
            "temp_low": 62.0,
            "d_heat_stage_2": 3.0,
        },
    ),
    "house_band_drift": (
        HouseBandDriftAlert,
        {
            "actual_vpd": 0.74,
            "actual_rh": 86.0,
            "band_low": 0.9,
            "band_high": 1.25,
            "band_target": 1.05,
            "wet_frac": 0.84,
            "dry_frac": 0.0,
        },
    ),
    "irrigation_feedback_gap": (
        IrrigationFeedbackGapAlert,
        {
            "feedback_key": "south_soil_probe_1",
            "signal": "soil_moisture_south_1",
            "status": "stuck_zero",
            "latest_value": 0.0,
            "last_sample_ts": NOW,
            "required_action": "Repair or replace south soil probe 1.",
            "view_details": {"samples_24h": 1434, "positive_samples_24h": 0},
        },
    ),
    "leak_detected": (
        LeakDetectedAlert,
        {"since": NOW},
    ),
    "plan_context_failed": (
        PlanContextFailedAlert,
        {"reason": "timeout", "stderr": "planner context failed", "exit_code": 124},
    ),
    "planner_band_ownership_drift": (
        PlannerBandOwnershipDriftAlert,
        {
            "band_owned_params": ["temp_low", "temp_high", "vpd_low", "vpd_high"],
            "offenders": [{"parameter": "temp_low", "plan_id": "p1", "source": "iris", "rows": 1}],
        },
    ),
    "planner_gateway_delivery_failed": (
        PlannerGatewayDeliveryFailedAlert,
        {
            "failures": [
                {
                    "id": 42,
                    "event_type": "SUNRISE",
                    "event_label": "sunrise",
                    "instance": "opus",
                    "gateway_status": 500,
                    "delivered_at": NOW,
                    "gateway_body": "bad gateway",
                }
            ]
        },
    ),
    "planner_trigger_sla_timeout": (
        PlannerTriggerSlaTimeoutAlert,
        {
            "timeouts": [
                {
                    "id": 43,
                    "event_type": "SUNSET",
                    "event_label": "sunset",
                    "instance": "opus",
                    "gateway_status": 200,
                    "delivered_at": NOW,
                    "gateway_body": "accepted",
                    "trigger_id": "00000000-0000-0000-0000-000000000043",
                    "elapsed_seconds": 1800,
                    "hermes_run_id": "run_123",
                }
            ]
        },
    ),
    "planner_plan_horizon_missing": (
        PlannerPlanHorizonMissingAlert,
        {
            "active_plan_id": "iris-20260510-2058",
            "active_plan_created_at": NOW,
            "latest_waypoint_ts": NOW,
            "future_waypoints": 0,
            "next_required_event_type": "SUNRISE",
            "next_required_due_at": NOW,
        },
    ),
    "planner_required_plan_missed": (
        PlannerRequiredPlanMissedAlert,
        {
            "misses": [
                {
                    "id": 42,
                    "event_type": "SUNRISE",
                    "event_label": "sunrise",
                    "instance": "opus",
                    "status": "delivery_failed",
                    "gateway_status": 500,
                    "delivered_at": NOW,
                    "gateway_body": "bad gateway",
                }
            ]
        },
    ),
    "planner_stale": (
        PlannerStaleAlert,
        {"age_s": 50_500, "age_h": 14.0},
    ),
    "planner_evaluation_missed": (
        PlannerEvaluationMissedAlert,
        {"plan_id": "iris-20260509-0551", "age_hours": 27},
    ),
    "lighting_cfg_threshold_drift": (
        LightingCfgThresholdDriftAlert,
        {
            "offenders": [
                {
                    "parameter": "gl_main_lux_threshold",
                    "light_key": "main",
                    "desired": 40000.0,
                    "device_cfg": 3000.0,
                    "observed_ts": "2026-06-16T12:03:14-06:00",
                }
            ]
        },
    ),
    "planner_tunable_range_drift": (
        PlannerTunableRangeDriftAlert,
        {
            "violations": [
                {
                    "parameter": "sw_fsm_controller_enabled",
                    "value": 0.0,
                    "plan_id": "iris-20260508-0700",
                    "source": "iris",
                    "ts": NOW,
                    "reason": "legacy fallback",
                    "error": "controller_locked_on",
                }
            ]
        },
    ),
    "relay_stuck": (
        RelayStuckAlert,
        {
            "hours_on": 3.5,
            "threshold_hours": 3.0,
            "state_source": "commanded_equipment_state",
            "temp_avg": 72.0,
            "sp_temp_high": 74.0,
            "greenhouse_mode": "HEAT",
            "context_ts": NOW,
        },
    ),
    "safety_invalid": (
        SafetyInvalidAlert,
        {"parameter": "safety_min", "value": 0.0},
    ),
    "sensor_offline": (
        SensorOfflineAlert,
        {"type": "climate", "staleness_ratio": 8.5},
    ),
    "setpoint_unconfirmed": (
        SetpointUnconfirmedAlert,
        {
            "parameter": "vpd_target_south",
            "requested_value": 1.3,
            "last_cfg_readback": 1.2,
            "age_s": 360,
            "pushed_at": NOW,
        },
    ),
    "soil_dryout": (
        SoilDryoutAlert,
        {
            "column": "soil_moisture_west",
            "sensor": "soil.west",
            "zone": "west",
            "wilt_pct": 18.0,
            "latest_pct": 12.4,
            "min_pct": 11.0,
            "max_pct": 14.9,
            "duration_h": 2.4,
            "samples": 1700,
            "occupancy": "occupied",
        },
    ),
    "soil_sensor_offline": (
        SoilSensorOfflineAlert,
        {"column": "soil_moisture_south_1", "sensor": "soil.south_1"},
    ),
    "temp_safety": (
        TempSafetyAlert,
        {"temp_f": 101.5},
    ),
    "tunable_zero_variance": (
        TunableZeroVarianceAlert,
        {"parameter": "vpd_target_west", "sample_count": 33_000, "pinned_value": 1.2},
    ),
    "vent_moisture_capacity_limit": (
        VentMoistureCapacityLimitAlert,
        {
            "recent_minutes": 30,
            "samples": 30,
            "vent_samples": 30,
            "moisture_samples": 24,
            "capacity_limited_samples": 21,
            "capacity_limited_fraction": 0.70,
            "moisture_fraction": 0.80,
            "avg_temp_excess_f": 3.2,
            "max_temp_excess_f": 6.1,
            "avg_vpd_excess_kpa": 0.18,
            "max_vpd_excess_kpa": 0.42,
            "avg_outdoor_temp_f": 87.2,
            "avg_outdoor_rh_pct": 17.1,
            "avg_solar_w_m2": 760.0,
        },
    ),
    "vent_vpd_moisture_gap": (
        VentVpdMoistureGapAlert,
        {
            "recent_minutes": 15,
            "samples": 15,
            "vent_samples": 15,
            "high_no_moisture_samples": 10,
            "high_no_moisture_fraction": 0.67,
            "moisture_fraction": 0.20,
            "avg_vpd": 1.35,
            "avg_vpd_high": 1.07,
            "avg_temp": 76.2,
            "avg_temp_high": 72.9,
            "avg_outdoor_temp_f": 86.5,
            "avg_outdoor_rh_pct": 18.0,
        },
    ),
    "vpd_extreme": (
        VpdExtremeAlert,
        {"vpd_kpa": 3.2},
    ),
    "vpd_stress": (
        VpdStressAlert,
        {
            "vpd_stress_hours": 2.4,
            "recent_samples": 12,
            "recent_high_samples": 9,
            "recent_high_fraction": 0.75,
            "avg_vpd_15m": 2.4,
            "avg_vpd_high_15m": 1.8,
        },
    ),
}


def _envelope(alert_type: str, details: dict) -> dict:
    return {
        "alert_type": alert_type,
        "severity": "warning",
        "category": "system",
        "message": f"{alert_type} fired",
        "details": details,
    }


@pytest.mark.parametrize("alert_type", ALERT_TYPES)
def test_envelope_dispatches_per_type(alert_type: str):
    expected_cls, details = CASES[alert_type]

    typed = ALERT_ENVELOPE_ADAPTER.validate_python(_envelope(alert_type, details))

    assert isinstance(typed, expected_cls)


@pytest.mark.parametrize("alert_type", ALERT_TYPES)
def test_alert_envelope_keeps_legacy_model_validate_api(alert_type: str):
    _, details = CASES[alert_type]

    env = AlertEnvelope.model_validate(_envelope(alert_type, details))

    assert env.alert_type == alert_type
    assert isinstance(env.details, dict)


@pytest.mark.parametrize("alert_type", ALERT_TYPES)
def test_envelope_rejects_extra_detail_fields(alert_type: str):
    _, details = CASES[alert_type]

    with pytest.raises(ValidationError):
        AlertEnvelope.model_validate(_envelope(alert_type, {**details, "typo_field": "bad"}))


def test_every_alert_type_has_a_case():
    assert set(CASES) == set(ALERT_TYPES)


def test_schema_covers_alert_types_in_write_paths():
    root = Path(__file__).resolve().parents[2]
    # Issue #46 split ingestor/tasks.py into the ingestor/tasks/ package; expand
    # it to every submodule so the alert-type write-path scan still covers it.
    tasks_pkg = root / "ingestor" / "tasks"
    tasks_sources = sorted(tasks_pkg.glob("*.py")) if tasks_pkg.is_dir() else [root / "ingestor" / "tasks.py"]
    sources = [
        *tasks_sources,
        root / "ingestor" / "iris_planner.py",
        root / "api" / "main.py",
    ]
    found: set[str] = set()
    patterns = [
        re.compile(r'"alert_type"\s*:\s*"([^"]+)"'),
        re.compile(r"alert_type\s*=\s*'([^']+)'"),
        # Positional `INSERT INTO alert_log VALUES (alert_type, severity, ...)`.
        # The 2nd field MUST be a valid AlertSeverity — without that constraint
        # the pattern also matched unrelated inserts into other tables whose 2nd
        # column happens to be a quoted string (e.g. slack_notification_events
        # VALUES ('ingestor', 'operator_brief', ...)), a false alert_type.
        re.compile(r"VALUES\s*\('([^']+)',\s*'(?:info|warning|critical)',\s*'\w+'", re.MULTILINE),
    ]
    for source in sources:
        text = source.read_text()
        for pattern in patterns:
            found.update(pattern.findall(text))

    assert found <= set(ALERT_TYPES)
