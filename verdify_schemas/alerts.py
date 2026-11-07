"""alert_log row + alert envelope schemas.

- AlertLogRow: permissive 1:1 shape for historical alert_log rows.
- AlertEnvelope: writable alert envelope validated against per-alert detail
  payloads before INSERT.
- AlertAction: MCP `alerts` tool envelope — replaces free-form (action, data: str).
- flow_stopped_at: when the site meter last went quiet (the leak alert's
  "Flow stopped at HH:MM", shared by the alert monitor and the Now read).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from .tunables import TunableParameter

AlertSeverity = Literal["info", "warning", "critical"]
AlertCategory = Literal["sensor", "equipment", "climate", "water", "system"]
AlertDisposition = Literal["open", "acknowledged", "resolved", "suppressed"]
ComponentExperimentIntegrityReason = Literal[
    "multiple_open_exposures",
    "terminal_experiment_capability_enabled",
    "open_exposure_work_expired",
    "expired_work_not_terminal",
    "baseline_artifact_missing",
    "confirmed_baseline_recovery_missing",
    "runtime_generation_missing",
    "facility_emergency_hold",
    "preexposure_state_mismatch",
    "open_exposure_observation_missing",
    "open_exposure_observation_stale",
    "open_exposure_state_mismatch",
    "open_admission_without_current_work",
    "completed_with_incomplete_outcomes",
    "baseline_recovery_in_progress",
    "runtime_fault_requires_recovery",
]
ComponentExperimentObservationTruth = Literal[
    "future_identity_masked",
    "no_current_work",
    "expected_state_missing",
    "unobserved",
    "stale",
    "mismatch",
    "exact",
]
AlertType = Literal[
    "alert_validation_failed",
    "band_anchor_db_read_failed",
    "band_device_db_divergence",
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
    "component_experiment_integrity",
    "plan_context_failed",
    "planner_band_ownership_drift",
    "planner_evaluation_missed",
    "planner_gateway_delivery_failed",
    "planner_tool_dependency_failed",
    "planner_plan_horizon_missing",
    "planner_required_plan_missed",
    "planner_stale",
    "planner_trigger_sla_timeout",
    "planner_tunable_range_drift",
    "relay_stuck",
    "safety_invalid",
    "sensor_offline",
    "setpoint_delivery_stuck",
    "setpoint_unconfirmed",
    "site_content_stale",
    "soil_dryout",
    "soil_sensor_offline",
    "temp_safety",
    "tunable_zero_variance",
    "vent_moisture_capacity_limit",
    "vent_vpd_moisture_gap",
    "vpd_extreme",
    "vpd_stress",
    "writer_absent",
    "telemetry_stall",
    "deploy_override",
    "irrigation_fence_breach",
    "irrigation_run_missed",
    "irrigation_line_faulted",
    "mcp_unreachable",
    "forecast_action_alert",
]

ALERT_TYPES: tuple[str, ...] = (
    "alert_validation_failed",
    "band_anchor_db_read_failed",
    "band_device_db_divergence",
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
    "component_experiment_integrity",
    "plan_context_failed",
    "planner_band_ownership_drift",
    "planner_evaluation_missed",
    "planner_gateway_delivery_failed",
    "planner_tool_dependency_failed",
    "planner_plan_horizon_missing",
    "planner_required_plan_missed",
    "planner_stale",
    "planner_trigger_sla_timeout",
    "planner_tunable_range_drift",
    "relay_stuck",
    "safety_invalid",
    "sensor_offline",
    "setpoint_delivery_stuck",
    "setpoint_unconfirmed",
    "site_content_stale",
    "soil_dryout",
    "soil_sensor_offline",
    "temp_safety",
    "tunable_zero_variance",
    "vent_moisture_capacity_limit",
    "vent_vpd_moisture_gap",
    "vpd_extreme",
    "vpd_stress",
    "writer_absent",
    "telemetry_stall",
    "deploy_override",
    "irrigation_fence_breach",
    "irrigation_run_missed",
    "irrigation_line_faulted",
    "mcp_unreachable",
    "forecast_action_alert",
)


class _DetailsBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _ProbeOfflineLifecycle(_DetailsBase):
    """One alert_log row per offline probe (VD-72, Round 2b C).

    A repeat failure re-opens or refreshes the probe's row instead of inserting
    a new one: ``count`` is how many times the row was raised (first insert plus
    re-opens), ``last_seen_at`` when it was last raised. ``long_offline`` marks a
    row the monitor resolved because the probe stayed offline past the
    configured period; the mark clears once the probe reports again.
    """

    count: int = Field(default=1, ge=1)
    last_seen_at: AwareDatetime | None = None
    long_offline: bool = False


class SensorOfflineDetails(_ProbeOfflineLifecycle):
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
    # Set once the meter has read under 0.1 gal/min for two readings after the
    # flag cleared ("Flow stopped at HH:MM"); null while water flows. The row
    # stays open until a person or agent resolves it (US-WAT-18, J6).
    stopped_at: AwareDatetime | None = None


class ClimateActionProofStaleDetails(_DetailsBase):
    age_s: int | None = Field(default=None, ge=0)
    latest_ts: AwareDatetime | None = None
    proof_missing: str | None = None


class SiteContentStaleDetails(_DetailsBase):
    # site_content RAG snapshot watermark did not advance back inside the
    # cadence window after a refresh pass (#43/#400): the corpus roots were
    # unavailable in the runtime environment (docs tree missing from the
    # image, website snapshot unmounted), so Iris keeps serving a stale
    # corpus. age_s/max_updated_at None == empty table (nothing to serve).
    age_s: int | None = Field(default=None, ge=0)
    max_updated_at: AwareDatetime | None = None
    rows_refreshed: int = Field(..., ge=0)
    window_s: int = Field(..., gt=0)
    corpus_roots: list[str] = Field(default_factory=list)


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
    # #1351: the fixed class of a failed Hermes run's provider error (never its text), e.g. provider_billing.
    error_class: str | None = Field(default=None, pattern=r"^[a-z_]{1,40}$")


class PlannerGatewayDeliveryFailedDetails(_DetailsBase):
    failures: list[PlanDeliveryFailureDetails]
    error_classes: list[str] = Field(default_factory=list)


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
    # Ledger disposition columns surfaced by the #427 delivery rewrite. Their
    # absence from this model silently dropped every planner_required_plan_missed
    # CRITICAL between 2026-07-10 and 2026-07-11 (drop-on-ValidationError in
    # ingestor/tasks/alerts.py).
    terminal_action: str | None = None
    failure_class: str | None = None
    had_required_failure: bool = False


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


class SoilSensorOfflineDetails(_ProbeOfflineLifecycle):
    # Per-probe rows (VD-72) list the silent columns in ``columns``; ``column``
    # is kept for the per-column rows raised before that change.
    column: str | None = None
    columns: list[str] = Field(default_factory=list)
    sensor: str
    # 'occupied' = zone has an active crop → genuine sensor fault (warning).
    # 'unpotted' = zone empty (e.g. Canna on the patio): historical rows only;
    # since VD-72 an unpotted zone raises nothing. Vanda zone-control design §5.4.
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
    # The since-boot low-water-mark event that raised the warning: when the
    # mark first fell to a new low under 10 KB and that low (kB).
    heap_low_watermark_event_ts: AwareDatetime | None = None
    heap_low_watermark_event_kb: float | None = None


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


class SetpointDeliveryStuckDetails(_DetailsBase):
    # Plain convergence (VD-62): settings whose outgoing value has differed
    # from the controller's readback for more than 15 min. Clamped and
    # experiment-policy-held settings are left out by the dispatcher.
    differing: int = Field(..., ge=1)
    params: list[str] = Field(..., min_length=1, max_length=20)
    oldest_since: AwareDatetime


class ESP32PushFailedDetails(_DetailsBase):
    # `error` is the legacy single-string shape; the dispatcher's terminal-failure
    # path reports per-parameter `failure_reasons`/`parameters` instead. At least
    # one of the two shapes must be present (enforced below) so an empty payload
    # still fails loudly.
    error: str | None = None
    failure_reasons: list[str] = Field(default_factory=list)
    parameters: list[str] = Field(default_factory=list)
    change_count: int = Field(..., ge=0)

    @model_validator(mode="after")
    def _require_some_reason(self) -> ESP32PushFailedDetails:
        if self.error is None and not self.failure_reasons:
            raise ValueError("esp32_push_failed details need error or failure_reasons")
        return self


class BandAnchorDbReadFailedDetails(_DetailsBase):
    # crop_band_anchors read failed; the dispatcher HELD the device NVS band
    # rather than push the registry fallback envelope (data-path review F1).
    origin: str


class BandDeviceDbDivergenceDetails(_DetailsBase):
    # The device's resolved on-chip band drifted from the DB-served band beyond
    # the firmware-approximation tolerance, or the device band readback is stale
    # (data-path review issue 2; from v_band_device_divergence).
    max_temp_abs_diff: float = Field(..., ge=0)
    max_vpd_abs_diff: float = Field(..., ge=0)
    device_age_s: float = Field(..., ge=0)


class PlanContextFailedDetails(_DetailsBase):
    reason: str
    stderr: str = ""
    exit_code: int | None = None


class BandFnNullDetails(_DetailsBase):
    band_row_null: bool
    zone_row_null: bool
    house_row_null: bool = False
    lighting_row_null: bool = False


class AlertValidationFailedDetails(_DetailsBase):
    # Fallback envelope written whenever a producer's alert payload fails
    # AlertEnvelope validation. The original severity is preserved on the
    # envelope so a dropped critical still pages as critical; the original
    # payload shape is summarized here for repair.
    original_alert_type: str
    original_severity: str
    validation_error: str
    producer: str | None = None


class ComponentExperimentIntegrityDetails(_DetailsBase):
    experiment_id: str = Field(..., min_length=36, max_length=36)
    lifecycle_status: str
    execution_phase: str
    admission_state: str
    safety_state: str
    reason: ComponentExperimentIntegrityReason
    operation_kind: str | None = None
    observation_truth: ComponentExperimentObservationTruth
    observation_age_seconds: int | None = Field(default=None, ge=0)
    open_exposure_count: int = Field(..., ge=0)
    writer_generation: int | None = Field(default=None, ge=0)
    connection_generation: int | None = Field(default=None, ge=0)
    outcomes_complete: bool
    rollback_ready: bool


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


class SiteContentStaleAlert(_AlertBase):
    alert_type: Literal["site_content_stale"]
    details: SiteContentStaleDetails


class ESP32RebootAlert(_AlertBase):
    alert_type: Literal["esp32_reboot"]
    details: ESP32RebootDetails


class PlannerStaleAlert(_AlertBase):
    alert_type: Literal["planner_stale"]
    details: PlannerStaleDetails


class PlannerEvaluationMissedAlert(_AlertBase):
    alert_type: Literal["planner_evaluation_missed"]
    details: PlannerEvaluationMissedDetails


class PlannerToolDependencyFailedDetails(_DetailsBase):
    tool: Literal["lessons_search", "knowledge_search"]
    reason: Literal[
        "embedding_config_invalid",
        "embedding_credential_missing",
        "embedding_provider_failed",
        "embedding_invalid_response",
        "semantic_query_failed",
    ]
    # Cortex embed.main (2560) since #1351; the OpenAI pair stays readable for rows written before it.
    embedding_model: Literal["embed.main", "text-embedding-3-large"]
    embedding_dimensions: Literal[2560, 3072]


class PlannerToolDependencyFailedAlert(_AlertBase):
    alert_type: Literal["planner_tool_dependency_failed"]
    details: PlannerToolDependencyFailedDetails


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


class SetpointDeliveryStuckAlert(_AlertBase):
    alert_type: Literal["setpoint_delivery_stuck"]
    details: SetpointDeliveryStuckDetails


class ESP32PushFailedAlert(_AlertBase):
    alert_type: Literal["esp32_push_failed"]
    details: ESP32PushFailedDetails


class BandAnchorDbReadFailedAlert(_AlertBase):
    alert_type: Literal["band_anchor_db_read_failed"]
    details: BandAnchorDbReadFailedDetails


class BandDeviceDbDivergenceAlert(_AlertBase):
    alert_type: Literal["band_device_db_divergence"]
    details: BandDeviceDbDivergenceDetails


class PlanContextFailedAlert(_AlertBase):
    alert_type: Literal["plan_context_failed"]
    details: PlanContextFailedDetails


class BandFnNullAlert(_AlertBase):
    alert_type: Literal["band_fn_null"]
    details: BandFnNullDetails


class AlertValidationFailedAlert(_AlertBase):
    alert_type: Literal["alert_validation_failed"]
    details: AlertValidationFailedDetails


class ComponentExperimentIntegrityAlert(_AlertBase):
    alert_type: Literal["component_experiment_integrity"]
    details: ComponentExperimentIntegrityDetails


class WriterAbsentDetails(_DetailsBase):
    # Out-of-band writer-presence check (2026-07-11 audit P0): the in-ingestor
    # alert engine self-silences when the writer dies, so this row is written
    # by the verdify-writer-watchdog CronJob from the Lease age.
    lease_found: bool
    lease_age_s: float | None = None
    threshold_s: float


class WriterAbsentAlert(_AlertBase):
    alert_type: Literal["writer_absent"]
    details: WriterAbsentDetails


class TelemetryStallDetails(_DetailsBase):
    # Writer holds the connection but rows stopped landing (wedged loop /
    # dead DB conn). Measured out-of-band from climate max(ts).
    climate_age_s: float
    threshold_s: float


class TelemetryStallAlert(_AlertBase):
    alert_type: Literal["telemetry_stall"]
    details: TelemetryStallDetails


class DeployOverrideDetails(_DetailsBase):
    # Audit record (pre-resolved info row) written by firmware-deploy-preflight
    # whenever an operator override is accepted — host-independent counterpart
    # of the local override log file.
    gate: str
    reason: str
    sha: str | None = None


class DeployOverrideAlert(_AlertBase):
    alert_type: Literal["deploy_override"]
    details: DeployOverrideDetails


class IrrigationFenceBreachDetails(_DetailsBase):
    # Live-truth invariant on equipment_state (2026-07-11 audit: replay/
    # invariants cannot see irrigation, so the #450 fencing contracts are
    # asserted against what the relays actually did). With an applied program
    # (VD-42) the last two rules apply; before the engine, or on src=rom, the
    # first three.
    rule: Literal[
        "blocked_fert_path_on",
        "fert_master_without_wall_drip",
        "center_drip_while_disabled",
        "relay_on_line_disabled",
        "fert_master_without_fert_run",
    ]
    equipment: str
    observed_at: AwareDatetime
    context: dict[str, Any] = Field(default_factory=dict)


class IrrigationFenceBreachAlert(_AlertBase):
    alert_type: Literal["irrigation_fence_breach"]
    details: IrrigationFenceBreachDetails


_IrrigationLineId = Annotated[str, Field(pattern=r"^L(0[1-9]|1[01])$")]


class IrrigationRunMissedDetails(_DetailsBase):
    # irrigation §6.3 (VD-42): the controller reported the occurrence missed, or it has
    # no outcome 2 h after its planned start, under a window the ingestor fully watched.
    line_id: _IrrigationLineId
    slot_id: int = Field(..., ge=1, le=65535)
    local_day: date
    planned_start: AwareDatetime
    rule: Literal["reported_missed", "no_outcome"]
    reason: str | None = None


class IrrigationRunMissedAlert(_AlertBase):
    alert_type: Literal["irrigation_run_missed"]
    details: IrrigationRunMissedDetails


class IrrigationLineFaultedDetails(_DetailsBase):
    # irrigation §6.3 (VD-42): irr_state's f= latch on a mister (D-IRP-19). fault_until
    # is local midnight; null until the controller has a valid clock to fill it in.
    line_id: _IrrigationLineId
    fault_until: AwareDatetime | None = None


class IrrigationLineFaultedAlert(_AlertBase):
    alert_type: Literal["irrigation_line_faulted"]
    details: IrrigationLineFaultedDetails


class McpUnreachableDetails(_DetailsBase):
    """The planning heartbeat's MCP Service probe (#1352): replaces its former chat-only page."""

    url: str = Field(..., min_length=1)
    consecutive_failures: int = Field(..., ge=1)


class McpUnreachableAlert(_AlertBase):
    alert_type: Literal["mcp_unreachable"]
    details: McpUnreachableDetails


class ForecastActionAlertDetails(_DetailsBase):
    """A fired forecast_action_rules row whose action is ``alert`` (#1352): replaces its former chat-only post."""

    rule_id: int
    rule_name: str = Field(..., min_length=1)
    metric: str = Field(..., min_length=1)
    operator: Literal["<", ">", "<=", ">="]
    threshold: float
    forecast_value: float
    forecast_at: AwareDatetime
    window: str


class ForecastActionAlert(_AlertBase):
    alert_type: Literal["forecast_action_alert"]
    details: ForecastActionAlertDetails


AlertEnvelopeUnion = Annotated[
    AlertValidationFailedAlert
    | BandAnchorDbReadFailedAlert
    | BandDeviceDbDivergenceAlert
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
    | ComponentExperimentIntegrityAlert
    | PlanContextFailedAlert
    | PlannerBandOwnershipDriftAlert
    | PlannerEvaluationMissedAlert
    | PlannerGatewayDeliveryFailedAlert
    | PlannerToolDependencyFailedAlert
    | PlannerTriggerSlaTimeoutAlert
    | PlannerPlanHorizonMissingAlert
    | PlannerRequiredPlanMissedAlert
    | PlannerStaleAlert
    | PlannerTunableRangeDriftAlert
    | RelayStuckAlert
    | SafetyInvalidAlert
    | SensorOfflineAlert
    | SetpointDeliveryStuckAlert
    | SetpointUnconfirmedAlert
    | SiteContentStaleAlert
    | SoilDryoutAlert
    | SoilSensorOfflineAlert
    | TempSafetyAlert
    | TunableZeroVarianceAlert
    | VentMoistureCapacityLimitAlert
    | VentVpdMoistureGapAlert
    | VpdExtremeAlert
    | VpdStressAlert
    | WriterAbsentAlert
    | TelemetryStallAlert
    | DeployOverrideAlert
    | IrrigationFenceBreachAlert
    | IrrigationRunMissedAlert
    | IrrigationLineFaultedAlert
    | McpUnreachableAlert
    | ForecastActionAlert,
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


def build_validation_failed_envelope(payload: dict[str, Any], error: Exception, producer: str) -> AlertEnvelope:
    """Fallback envelope for a payload that failed :class:`AlertEnvelope` validation.

    Producer/schema drift must never silently drop an alert (the 2026-07-10/11
    planner_required_plan_missed criticals were lost exactly this way). The
    fallback preserves the ORIGINAL severity so a dropped critical still pages
    as critical, and records enough of the original shape to repair the drift.
    """
    original_type = str(payload.get("alert_type", "unknown"))
    original_severity = str(payload.get("severity", "warning"))
    severity = original_severity if original_severity in ("info", "warning", "critical") else "warning"
    original_message = str(payload.get("message", ""))[:200]
    return AlertEnvelope.model_validate(
        {
            "alert_type": "alert_validation_failed",
            "severity": severity,
            "category": "system",
            "sensor_id": payload.get("sensor_id"),
            "message": (
                f"Alert payload for `{original_type}` failed schema validation; "
                f"original alert not recorded: {original_message}"
            ),
            "details": {
                "original_alert_type": original_type,
                "original_severity": original_severity,
                "validation_error": str(error)[:1500],
                "producer": producer,
            },
        }
    )


def flow_stopped_at(
    readings: Iterable[tuple[datetime, float | None]],
    below_gpm: float = 0.1,
    needed: int = 2,
) -> datetime | None:
    """When the site meter went quiet: the ``needed``-th reading of the trailing quiet run.

    ``readings`` are ``(ts, gpm)`` pairs, oldest first; a ``None`` gpm is no
    reading and is skipped. The trailing run is the readings after the last one
    at or above ``below_gpm``. Its ``needed``-th reading is returned, or None
    while the run is shorter, so flow that starts again ends the run and the
    result goes back to None. Pure: the alert monitor's leak lifecycle (VD-72)
    and the Now read's leak block (VD-54) share it.
    """
    if needed < 1:
        raise ValueError("needed must be at least 1")
    quiet: list[datetime] = []
    for ts, gpm in readings:
        if gpm is None:
            continue
        if gpm < below_gpm:
            if len(quiet) < needed:
                quiet.append(ts)
        else:
            quiet = []
    return quiet[needed - 1] if len(quiet) >= needed else None


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


class AlertRunbookRow(BaseModel):
    """A runbook row the dashboard renders under an alert (table ``slack_alert_runbooks`` until renamed, #1352)."""

    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    greenhouse_id: str = "vallery"
    alert_type: str
    severity: str | None = None
    title: str
    summary: str
    runbook_url: str | None = None
    steps: list[str] = Field(default_factory=list)
    is_active: bool = True
    created_at: AwareDatetime | None = None
    updated_at: AwareDatetime | None = None
