"""alert_log row + alert envelope schemas.

- AlertLogRow: permissive 1:1 shape for historical alert_log rows.
- AlertEnvelope: writable alert envelope validated against per-alert detail
  payloads before INSERT.
- AlertAction: MCP `alerts` tool envelope — replaces free-form (action, data: str).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from .tunables import TunableParameter

AlertSeverity = Literal["info", "warning", "critical"]
AlertCategory = Literal["sensor", "equipment", "climate", "water", "system"]
AlertDisposition = Literal["open", "acknowledged", "resolved", "suppressed"]
AlertType = Literal[
    "band_anchor_db_read_failed",
    "band_fn_null",
    "esp32_push_failed",
    "esp32_reboot",
    "firmware_relief_ceiling",
    "firmware_vent_latched",
    "firmware_version_mismatch",
    "forecast_deviation",
    "heap_pressure_critical",
    "heap_pressure_warning",
    "heat_manual_override",
    "heat_staging_inversion",
    "house_band_drift",
    "irrigation_feedback_gap",
    "leak_detected",
    "lighting_cfg_threshold_drift",
    "climate_action_proof_stale",
    "plan_context_failed",
    "planner_band_ownership_drift",
    "planner_evaluation_missed",
    "planner_gateway_delivery_failed",
    "planner_plan_horizon_missing",
    "planner_required_plan_missed",
    "planner_stale",
    "planner_trigger_sla_timeout",
    "planner_tunable_range_drift",
    "relay_stuck",
    "safety_invalid",
    "sensor_offline",
    "setpoint_unconfirmed",
    "soil_dryout",
    "soil_sensor_offline",
    "temp_safety",
    "tunable_zero_variance",
    "vent_moisture_capacity_limit",
    "vent_vpd_moisture_gap",
    "vpd_extreme",
    "vpd_stress",
]

ALERT_TYPES: tuple[str, ...] = (
    "band_anchor_db_read_failed",
    "band_fn_null",
    "esp32_push_failed",
    "esp32_reboot",
    "firmware_relief_ceiling",
    "firmware_vent_latched",
    "firmware_version_mismatch",
    "forecast_deviation",
    "heap_pressure_critical",
    "heap_pressure_warning",
    "heat_manual_override",
    "heat_staging_inversion",
    "house_band_drift",
    "irrigation_feedback_gap",
    "leak_detected",
    "lighting_cfg_threshold_drift",
    "climate_action_proof_stale",
    "plan_context_failed",
    "planner_band_ownership_drift",
    "planner_evaluation_missed",
    "planner_gateway_delivery_failed",
    "planner_plan_horizon_missing",
    "planner_required_plan_missed",
    "planner_stale",
    "planner_trigger_sla_timeout",
    "planner_tunable_range_drift",
    "relay_stuck",
    "safety_invalid",
    "sensor_offline",
    "setpoint_unconfirmed",
    "soil_dryout",
    "soil_sensor_offline",
    "temp_safety",
    "tunable_zero_variance",
    "vent_moisture_capacity_limit",
    "vent_vpd_moisture_gap",
    "vpd_extreme",
    "vpd_stress",
)


class _DetailsBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SensorOfflineDetails(_DetailsBase):
    type: str
    staleness_ratio: float | None = None


class IrrigationFeedbackGapDetails(_DetailsBase):
    feedback_key: str
    signal: str
    status: Literal["missing", "stuck_zero", "stale"]
    latest_value: float | None = None
    last_sample_ts: AwareDatetime | None = None
    required_action: str
    view_details: dict[str, Any] = Field(default_factory=dict)


class RelayStuckDetails(_DetailsBase):
    hours_on: float = Field(..., ge=0)
    threshold_hours: float = Field(..., ge=0)
    state_source: str
    temp_avg: float | None = None
    vpd_avg: float | None = None
    sp_temp_high: float | None = None
    sp_vpd_low: float | None = None
    sp_vpd_high: float | None = None
    greenhouse_mode: str | None = None
    context_ts: AwareDatetime | None = None


class VpdStressDetails(_DetailsBase):
    vpd_stress_hours: float = Field(..., ge=0)
    recent_samples: int = Field(..., ge=0)
    recent_high_samples: int = Field(..., ge=0)
    recent_high_fraction: float = Field(..., ge=0)
    avg_vpd_15m: float | None = None
    avg_vpd_high_15m: float | None = None


class VentVpdMoistureGapDetails(_DetailsBase):
    recent_minutes: int = Field(..., ge=1)
    samples: int = Field(..., ge=0)
    vent_samples: int = Field(..., ge=0)
    high_no_moisture_samples: int = Field(..., ge=0)
    high_no_moisture_fraction: float = Field(..., ge=0)
    moisture_fraction: float = Field(..., ge=0)
    avg_vpd: float | None = None
    avg_vpd_high: float | None = None
    avg_temp: float | None = None
    avg_temp_high: float | None = None
    avg_outdoor_temp_f: float | None = None
    avg_outdoor_rh_pct: float | None = None


class VentMoistureCapacityLimitDetails(_DetailsBase):
    recent_minutes: int = Field(..., ge=1)
    samples: int = Field(..., ge=0)
    vent_samples: int = Field(..., ge=0)
    moisture_samples: int = Field(..., ge=0)
    capacity_limited_samples: int = Field(..., ge=0)
    capacity_limited_fraction: float = Field(..., ge=0)
    moisture_fraction: float = Field(..., ge=0)
    avg_temp_excess_f: float | None = None
    max_temp_excess_f: float | None = None
    avg_vpd_excess_kpa: float | None = None
    max_vpd_excess_kpa: float | None = None
    avg_outdoor_temp_f: float | None = None
    avg_outdoor_rh_pct: float | None = None
    avg_solar_w_m2: float | None = None


class TempSafetyDetails(_DetailsBase):
    temp_f: float


class VpdExtremeDetails(_DetailsBase):
    vpd_kpa: float = Field(..., ge=0)


class LeakDetectedDetails(_DetailsBase):
    since: AwareDatetime


class ClimateActionProofStaleDetails(_DetailsBase):
    age_s: int | None = Field(default=None, ge=0)
    latest_ts: AwareDatetime | None = None
    proof_missing: str | None = None


class ESP32RebootDetails(_DetailsBase):
    uptime_s: float = Field(..., ge=0)
    reset_reason: str = ""


class PlannerStaleDetails(_DetailsBase):
    age_s: int = Field(..., ge=0)
    age_h: float = Field(..., ge=0)


class PlannerEvaluationMissedDetails(_DetailsBase):
    plan_id: str
    age_hours: int = Field(..., ge=0)


class PlanDeliveryFailureDetails(_DetailsBase):
    id: int
    event_type: str
    event_label: str | None = None
    instance: str | None = None
    gateway_status: int | None = None
    delivered_at: AwareDatetime | None = None
    gateway_body: str = ""


class PlannerGatewayDeliveryFailedDetails(_DetailsBase):
    failures: list[PlanDeliveryFailureDetails]


class PlanDeliveryTimeoutDetails(PlanDeliveryFailureDetails):
    trigger_id: str | None = None
    elapsed_seconds: int = Field(..., ge=0)
    hermes_run_id: str | None = None


class PlannerTriggerSlaTimeoutDetails(_DetailsBase):
    timeouts: list[PlanDeliveryTimeoutDetails]


class PlannerPlanHorizonMissingDetails(_DetailsBase):
    active_plan_id: str | None = None
    active_plan_created_at: AwareDatetime | None = None
    latest_waypoint_ts: AwareDatetime | None = None
    future_waypoints: int = Field(..., ge=0)
    next_required_event_type: str | None = None
    next_required_due_at: AwareDatetime | None = None


class RequiredPlanMissDetails(PlanDeliveryFailureDetails):
    status: str
    expected_at: AwareDatetime | None = None
    due_at: AwareDatetime | None = None
    plan_delivery_log_id: int | None = None
    trigger_id: str | None = None
    resulting_plan_id: str | None = None


class PlannerRequiredPlanMissedDetails(_DetailsBase):
    misses: list[RequiredPlanMissDetails]


class BandOwnershipOffender(_DetailsBase):
    parameter: TunableParameter
    plan_id: str
    source: str
    rows: int = Field(..., ge=1)


class PlannerBandOwnershipDriftDetails(_DetailsBase):
    band_owned_params: list[TunableParameter]
    offenders: list[BandOwnershipOffender]


class PlannerTunableRangeViolation(_DetailsBase):
    parameter: str
    value: float
    plan_id: str
    source: str
    ts: AwareDatetime
    reason: str = ""
    error: str


class PlannerTunableRangeDriftDetails(_DetailsBase):
    violations: list[PlannerTunableRangeViolation]


class LightingCfgThresholdDriftOffender(_DetailsBase):
    parameter: str
    light_key: str
    desired: float
    device_cfg: float
    observed_ts: str | None = None


class LightingCfgThresholdDriftDetails(_DetailsBase):
    offenders: list[LightingCfgThresholdDriftOffender]


class SafetyInvalidDetails(_DetailsBase):
    parameter: TunableParameter
    value: float | None = None


class HeatManualOverrideDetails(_DetailsBase):
    watts: int = Field(..., ge=0)


class SoilSensorOfflineDetails(_DetailsBase):
    column: str
    sensor: str
    # 'occupied' = zone has an active crop → genuine sensor fault (warning).
    # 'unpotted' = zone empty (e.g. Canna on the patio) → probe dangling, no
    # action needed (downgraded to info). Vanda zone-control design §5.4.
    occupancy: Literal["occupied", "unpotted"] | None = None


class SoilDryoutDetails(_DetailsBase):
    # A LIVE root-zone probe (non-stuck, non-missing) that has read continuously
    # below its zone wilt threshold for > duration_h hours. Read-side paging only:
    # this never actuates irrigation, it pages an operator (audit §7-#1, the west
    # probe that crashed 11 days with no value-below-wilt rule to catch it).
    column: str
    sensor: str
    zone: str
    wilt_pct: float = Field(..., ge=0)
    latest_pct: float = Field(..., ge=0)
    min_pct: float = Field(..., ge=0)
    max_pct: float = Field(..., ge=0)
    duration_h: float = Field(..., ge=0)
    samples: int = Field(..., ge=0)
    # 'occupied' = zone has an active crop -> genuine dryout, page critical. The
    # rule is suppressed entirely for unpotted/empty zones (mirrors
    # soil_sensor_offline occupancy semantics), so this is always 'occupied' on a
    # fired alert; carried for symmetry + dashboard context.
    occupancy: Literal["occupied"] | None = None


class HeatStagingInversionDetails(_DetailsBase):
    heat2_on_since: AwareDatetime
    duration_s: float = Field(..., ge=0)
    temp_avg: float | None = None
    temp_low: float | None = None
    d_heat_stage_2: float | None = None


class HouseBandDriftDetails(_DetailsBase):
    actual_vpd: float = Field(..., ge=0)
    actual_rh: float = Field(..., ge=0, le=100)
    band_low: float = Field(..., ge=0)
    band_high: float = Field(..., ge=0)
    band_target: float = Field(..., ge=0)
    wet_frac: float = Field(..., ge=0, le=1)
    dry_frac: float = Field(..., ge=0, le=1)


class FirmwareReliefCeilingDetails(_DetailsBase):
    relief_cycle_count: int = Field(..., ge=0)
    ceiling_default: int = Field(..., ge=1)


class FirmwareVentLatchedDetails(_DetailsBase):
    vent_latch_timer_s: int = Field(..., ge=0)


class FirmwareVersionMismatchDetails(_DetailsBase):
    expected_firmware_version: str
    live_firmware_version: str
    diagnostics_ts: AwareDatetime | None = None
    pin_source: str


class ForecastDeviationItem(_DetailsBase):
    parameter: str
    observed: float
    forecasted: float
    delta: float = Field(..., ge=0)
    threshold: float = Field(..., ge=0)
    unit: str
    cooldown_min: int = Field(..., ge=0)
    triggered: Literal[True] = True
    normalized_excess: float = Field(..., ge=0)
    recent_cycles: int = Field(..., ge=1)


class ForecastDeviationDetails(_DetailsBase):
    deviations: list[ForecastDeviationItem]
    reason: str
    max_abs_deviation: float = Field(..., ge=0)
    consecutive_cycles: int = Field(..., ge=1)
    planner_event_type: Literal["FORECAST_DEVIATION"] = "FORECAST_DEVIATION"
    source: Literal["forecast_deviation_check"] = "forecast_deviation_check"


class HeapPressureDetails(_DetailsBase):
    equipment: Literal["heap_pressure_critical", "heap_pressure_warning"]
    equipment_ts: AwareDatetime | None = None
    last_true_ts: AwareDatetime | None = None
    heap_free_kb: float | None = None
    heap_min_free_kb: float | None = None
    heap_largest_free_block_kb: float | None = None
    heap_low_watermark_warning: bool = False
    heap_fragmentation_warning: bool = False
    heap_diag_ts: AwareDatetime | None = None
    healthy_heap_samples_after_event: int = Field(..., ge=0)


class HeapPressureCriticalDetails(HeapPressureDetails):
    equipment: Literal["heap_pressure_critical"]
    critical_logs_30m: int = Field(..., ge=0)
    last_critical_log_ts: AwareDatetime | None = None
    last_critical_log_message: str | None = None


class HeapPressureWarningDetails(HeapPressureDetails):
    equipment: Literal["heap_pressure_warning"]
    warning_logs_30m: int = Field(..., ge=0)
    last_warning_log_ts: AwareDatetime | None = None
    last_warning_log_message: str | None = None


class TunableZeroVarianceDetails(_DetailsBase):
    parameter: TunableParameter
    sample_count: int = Field(..., ge=0)
    pinned_value: float


class SetpointUnconfirmedDetails(_DetailsBase):
    parameter: TunableParameter
    requested_value: float
    last_cfg_readback: float | None = None
    age_s: int = Field(..., ge=0)
    pushed_at: AwareDatetime


class ESP32PushFailedDetails(_DetailsBase):
    error: str
    change_count: int = Field(..., ge=0)


class BandAnchorDbReadFailedDetails(_DetailsBase):
    # crop_band_anchors read failed; the dispatcher HELD the device NVS band
    # rather than push the registry fallback envelope (data-path review F1).
    origin: str


class PlanContextFailedDetails(_DetailsBase):
    reason: str
    stderr: str = ""
    exit_code: int | None = None


class BandFnNullDetails(_DetailsBase):
    band_row_null: bool
    zone_row_null: bool
    house_row_null: bool = False
    lighting_row_null: bool = False


class _AlertBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: AlertSeverity
    category: AlertCategory
    sensor_id: str | None = None
    zone: str | None = None
    zone_id: int | None = None
    message: str = Field(..., min_length=1)
    metric_value: float | None = None
    threshold_value: float | None = None


class SensorOfflineAlert(_AlertBase):
    alert_type: Literal["sensor_offline"]
    details: SensorOfflineDetails


class RelayStuckAlert(_AlertBase):
    alert_type: Literal["relay_stuck"]
    details: RelayStuckDetails


class VpdStressAlert(_AlertBase):
    alert_type: Literal["vpd_stress"]
    details: VpdStressDetails


class VentVpdMoistureGapAlert(_AlertBase):
    alert_type: Literal["vent_vpd_moisture_gap"]
    details: VentVpdMoistureGapDetails


class VentMoistureCapacityLimitAlert(_AlertBase):
    alert_type: Literal["vent_moisture_capacity_limit"]
    details: VentMoistureCapacityLimitDetails


class TempSafetyAlert(_AlertBase):
    alert_type: Literal["temp_safety"]
    details: TempSafetyDetails


class VpdExtremeAlert(_AlertBase):
    alert_type: Literal["vpd_extreme"]
    details: VpdExtremeDetails


class LeakDetectedAlert(_AlertBase):
    alert_type: Literal["leak_detected"]
    details: LeakDetectedDetails


class ClimateActionProofStaleAlert(_AlertBase):
    alert_type: Literal["climate_action_proof_stale"]
    details: ClimateActionProofStaleDetails


class ESP32RebootAlert(_AlertBase):
    alert_type: Literal["esp32_reboot"]
    details: ESP32RebootDetails


class PlannerStaleAlert(_AlertBase):
    alert_type: Literal["planner_stale"]
    details: PlannerStaleDetails


class PlannerEvaluationMissedAlert(_AlertBase):
    alert_type: Literal["planner_evaluation_missed"]
    details: PlannerEvaluationMissedDetails


class PlannerGatewayDeliveryFailedAlert(_AlertBase):
    alert_type: Literal["planner_gateway_delivery_failed"]
    details: PlannerGatewayDeliveryFailedDetails


class PlannerTriggerSlaTimeoutAlert(_AlertBase):
    alert_type: Literal["planner_trigger_sla_timeout"]
    details: PlannerTriggerSlaTimeoutDetails


class PlannerPlanHorizonMissingAlert(_AlertBase):
    alert_type: Literal["planner_plan_horizon_missing"]
    details: PlannerPlanHorizonMissingDetails


class PlannerRequiredPlanMissedAlert(_AlertBase):
    alert_type: Literal["planner_required_plan_missed"]
    details: PlannerRequiredPlanMissedDetails


class PlannerBandOwnershipDriftAlert(_AlertBase):
    alert_type: Literal["planner_band_ownership_drift"]
    details: PlannerBandOwnershipDriftDetails


class PlannerTunableRangeDriftAlert(_AlertBase):
    alert_type: Literal["planner_tunable_range_drift"]
    details: PlannerTunableRangeDriftDetails


class LightingCfgThresholdDriftAlert(_AlertBase):
    alert_type: Literal["lighting_cfg_threshold_drift"]
    details: LightingCfgThresholdDriftDetails


class SafetyInvalidAlert(_AlertBase):
    alert_type: Literal["safety_invalid"]
    details: SafetyInvalidDetails


class HeatManualOverrideAlert(_AlertBase):
    alert_type: Literal["heat_manual_override"]
    details: HeatManualOverrideDetails


class SoilSensorOfflineAlert(_AlertBase):
    alert_type: Literal["soil_sensor_offline"]
    details: SoilSensorOfflineDetails


class SoilDryoutAlert(_AlertBase):
    alert_type: Literal["soil_dryout"]
    details: SoilDryoutDetails


class HeatStagingInversionAlert(_AlertBase):
    alert_type: Literal["heat_staging_inversion"]
    details: HeatStagingInversionDetails


class HouseBandDriftAlert(_AlertBase):
    alert_type: Literal["house_band_drift"]
    details: HouseBandDriftDetails


class IrrigationFeedbackGapAlert(_AlertBase):
    alert_type: Literal["irrigation_feedback_gap"]
    details: IrrigationFeedbackGapDetails


class FirmwareReliefCeilingAlert(_AlertBase):
    alert_type: Literal["firmware_relief_ceiling"]
    details: FirmwareReliefCeilingDetails


class FirmwareVentLatchedAlert(_AlertBase):
    alert_type: Literal["firmware_vent_latched"]
    details: FirmwareVentLatchedDetails


class FirmwareVersionMismatchAlert(_AlertBase):
    alert_type: Literal["firmware_version_mismatch"]
    details: FirmwareVersionMismatchDetails


class ForecastDeviationAlert(_AlertBase):
    alert_type: Literal["forecast_deviation"]
    details: ForecastDeviationDetails


class HeapPressureCriticalAlert(_AlertBase):
    alert_type: Literal["heap_pressure_critical"]
    details: HeapPressureCriticalDetails


class HeapPressureWarningAlert(_AlertBase):
    alert_type: Literal["heap_pressure_warning"]
    details: HeapPressureWarningDetails


class TunableZeroVarianceAlert(_AlertBase):
    alert_type: Literal["tunable_zero_variance"]
    details: TunableZeroVarianceDetails


class SetpointUnconfirmedAlert(_AlertBase):
    alert_type: Literal["setpoint_unconfirmed"]
    details: SetpointUnconfirmedDetails


class ESP32PushFailedAlert(_AlertBase):
    alert_type: Literal["esp32_push_failed"]
    details: ESP32PushFailedDetails


class BandAnchorDbReadFailedAlert(_AlertBase):
    alert_type: Literal["band_anchor_db_read_failed"]
    details: BandAnchorDbReadFailedDetails


class PlanContextFailedAlert(_AlertBase):
    alert_type: Literal["plan_context_failed"]
    details: PlanContextFailedDetails


class BandFnNullAlert(_AlertBase):
    alert_type: Literal["band_fn_null"]
    details: BandFnNullDetails


AlertEnvelopeUnion = Annotated[
    BandAnchorDbReadFailedAlert
    | BandFnNullAlert
    | ESP32PushFailedAlert
    | ESP32RebootAlert
    | FirmwareReliefCeilingAlert
    | FirmwareVentLatchedAlert
    | FirmwareVersionMismatchAlert
    | ForecastDeviationAlert
    | HeapPressureCriticalAlert
    | HeapPressureWarningAlert
    | HeatManualOverrideAlert
    | HeatStagingInversionAlert
    | HouseBandDriftAlert
    | IrrigationFeedbackGapAlert
    | LeakDetectedAlert
    | LightingCfgThresholdDriftAlert
    | ClimateActionProofStaleAlert
    | PlanContextFailedAlert
    | PlannerBandOwnershipDriftAlert
    | PlannerEvaluationMissedAlert
    | PlannerGatewayDeliveryFailedAlert
    | PlannerTriggerSlaTimeoutAlert
    | PlannerPlanHorizonMissingAlert
    | PlannerRequiredPlanMissedAlert
    | PlannerStaleAlert
    | PlannerTunableRangeDriftAlert
    | RelayStuckAlert
    | SafetyInvalidAlert
    | SensorOfflineAlert
    | SetpointUnconfirmedAlert
    | SoilDryoutAlert
    | SoilSensorOfflineAlert
    | TempSafetyAlert
    | TunableZeroVarianceAlert
    | VentMoistureCapacityLimitAlert
    | VentVpdMoistureGapAlert
    | VpdExtremeAlert
    | VpdStressAlert,
    Field(discriminator="alert_type"),
]

ALERT_ENVELOPE_ADAPTER: TypeAdapter[AlertEnvelopeUnion] = TypeAdapter(AlertEnvelopeUnion)


class AlertEnvelope(BaseModel):
    """In-memory alert struct validated before INSERT.

    The public class keeps the existing ``AlertEnvelope.model_validate(...)``
    API for ingestor callers, while the post-validator dispatches through the
    stricter tagged union above. New alert types and detail-shape drift fail at
    construction time instead of landing in ``alert_log.details`` silently.

    Sprint 22 (migration 086) added: zone_id FK to alert_log.
    """

    model_config = ConfigDict(extra="forbid")

    alert_type: AlertType
    severity: AlertSeverity
    category: AlertCategory
    sensor_id: str | None = None
    zone: str | None = None
    zone_id: int | None = None
    message: str = Field(..., min_length=1)
    details: dict[str, Any]
    metric_value: float | None = None
    threshold_value: float | None = None

    @model_validator(mode="after")
    def _validate_typed_details(self) -> AlertEnvelope:
        typed = ALERT_ENVELOPE_ADAPTER.validate_python(self.model_dump(mode="python"))
        self.details = typed.details.model_dump(mode="json")
        return self


class AlertLogRow(BaseModel):
    """Full row as persisted.

    Historical rows can carry old alert types or old detail payloads, so the
    persisted-read shape intentionally remains looser than the write envelope.
    """

    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    ts: AwareDatetime | None = None
    alert_type: str = Field(..., min_length=1)
    severity: AlertSeverity
    category: AlertCategory
    sensor_id: str | None = None
    zone: str | None = None
    zone_id: int | None = None
    message: str = Field(..., min_length=1)
    details: dict[str, Any] | None = None
    metric_value: float | None = None
    threshold_value: float | None = None
    source: str = "system"
    disposition: AlertDisposition = "open"
    acknowledged_at: AwareDatetime | None = None
    acknowledged_by: str | None = None
    resolved_at: AwareDatetime | None = None
    resolved_by: str | None = None
    resolution: str | None = None
    slack_ts: str | None = None
    notes: str | None = None
    greenhouse_id: str = "vallery"


# ── MCP action envelope ────────────────────────────────────────────
#
# Replaces the current `action: str, data: str` contract in server.py
# `alerts` tool, where `data` is ad hoc JSON parsed after the fact.
# The tool migration in Phase 7 builds an AlertAction from the raw args,
# which forces every downstream branch to see a typed payload.

AlertActionKind = Literal["list", "acknowledge", "resolve"]


class AlertAckPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    acknowledged_by: str = Field(..., min_length=1, max_length=100)


class AlertResolvePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolved_by: str = Field(..., min_length=1, max_length=100)
    resolution: str | None = Field(default=None, max_length=2000)


class AlertAction(BaseModel):
    """MCP `alerts` tool input envelope."""

    model_config = ConfigDict(extra="forbid")

    action: AlertActionKind
    alert_id: int | None = None  # ignored for list
    data: AlertAckPayload | AlertResolvePayload | None = None
