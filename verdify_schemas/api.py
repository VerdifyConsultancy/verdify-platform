"""API response envelopes.

These are the **wire shapes** FastAPI returns — separate from the DB row
models because responses often fold in joined fields (e.g. `latest_health`
off of recent observations, `active_crops` count on zones).

Applied via `response_model=X` on the top 8 endpoints in `api/main.py`.
FastAPI coerces asyncpg Record → dict → Pydantic model at response time
and auto-populates the OpenAPI spec at /docs.
"""

from __future__ import annotations

from datetime import date as DateType
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from .crops import Observation
from .observed_minutes import ObservedMinuteEvidence


class APIStatus(BaseModel):
    """GET /api/v1/status — health check."""

    model_config = ConfigDict(extra="ignore")

    status: str
    active_crops: int = Field(..., ge=0)
    observations: int = Field(..., ge=0)
    latest_climate_ts: AwareDatetime | None = None


class PublicDataHealthCheck(BaseModel):
    """One public-safe data-health check row."""

    model_config = ConfigDict(extra="ignore")

    name: str
    status: Literal["ok", "warn", "fail"]
    metric_value: float | None = None
    threshold_value: float | None = None
    details: str | None = None


class PublicPipelineHealthSource(BaseModel):
    """Freshness/null-rate summary for one telemetry source."""

    model_config = ConfigDict(extra="ignore")

    source: str
    rows_1h: int = Field(..., ge=0)
    rows_24h: int = Field(..., ge=0)
    age_s: int | None = Field(default=None, ge=0)
    null_pct_1h: float | None = Field(default=None, ge=0, le=100)


class PublicDataHealthResponse(BaseModel):
    """GET /api/v1/public/data-health."""

    model_config = ConfigDict(extra="ignore")

    generated_at: AwareDatetime
    overall_status: Literal["ok", "warn", "fail"]
    checks: list[PublicDataHealthCheck]
    pipeline_sources: list[PublicPipelineHealthSource]


class PublicPlannerTrigger(BaseModel):
    """One expected planner trigger in the public planner-health surface."""

    model_config = ConfigDict(extra="ignore")

    id: int = Field(..., ge=1)
    event_type: str
    event_label: str | None = None
    instance: str | None = None
    expected_at: AwareDatetime
    due_at: AwareDatetime
    delivered_at: AwareDatetime | None = None
    resolved_at: AwareDatetime | None = None
    status: str
    expected_action: str
    trigger_id: str | None = None
    resulting_plan_id: str | None = None


class PublicPlannerDelivery(BaseModel):
    """One delivered planner trigger with gateway/model/session observability."""

    model_config = ConfigDict(extra="ignore")

    id: int = Field(..., ge=1)
    event_type: str
    event_label: str | None = None
    delivered_at: AwareDatetime
    status: str | None = None
    instance: str | None = None
    session_key: str | None = None
    wake_mode: str | None = None
    gateway_status: int | None = None
    hermes_run_id: str | None = None
    trigger_id: str | None = None
    resulting_plan_id: str | None = None
    plan_written_at: AwareDatetime | None = None
    planner_gateway: str | None = None
    planner_model_label: str | None = None


class PublicPlannerHealthResponse(BaseModel):
    """GET /api/v1/public/planner-health."""

    model_config = ConfigDict(extra="ignore")

    generated_at: AwareDatetime
    overall_status: Literal["ok", "warn", "fail"]
    missed_expected_count: int = Field(..., ge=0)
    overdue_delivered_count: int = Field(..., ge=0)
    required_failure_count: int = Field(..., ge=0)
    recent_expected_count: int = Field(..., ge=0)
    resolved_count: int = Field(..., ge=0)
    latest_required: list[dict] = Field(default_factory=list)
    last_expected_trigger: PublicPlannerTrigger | None = None
    last_delivered_trigger: PublicPlannerTrigger | None = None
    last_resolved_trigger: PublicPlannerTrigger | None = None
    pending_by_sla_age: dict[str, int] = Field(default_factory=dict)
    current_session_key: str | None = None
    current_model_label: str | None = None
    current_hermes_run_id: str | None = None
    active_plan_range_violation_count: int = Field(default=0, ge=0)
    recent_deliveries: list[PublicPlannerDelivery] = Field(default_factory=list)
    recent_triggers: list[PublicPlannerTrigger] = Field(default_factory=list)


class PublicHomeMetrics(BaseModel):
    """GET /api/v1/public/home-metrics — launch-safe proof counters."""

    model_config = ConfigDict(extra="ignore")

    generated_at: AwareDatetime
    greenhouse_id: str
    climate_rows: int = Field(..., ge=0)
    climate_days: float = Field(..., ge=0)
    active_crops: int = Field(..., ge=0)
    plan_count: int = Field(..., ge=0)
    lesson_count: int = Field(..., ge=0)
    latest_climate_ts: AwareDatetime | None = None
    latest_climate_age_s: int | None = Field(default=None, ge=0)
    indoor_temp_f: float | None = None
    indoor_vpd_kpa: float | None = None
    outdoor_temp_f: float | None = None
    outdoor_rh_pct: float | None = None
    last_plan_id: str | None = None
    last_plan_created_at: AwareDatetime | None = None
    last_plan_age_s: int | None = Field(default=None, ge=0)
    planner_score_today: float | None = None
    planner_score_scope: Literal["climate_only_resource_excluded", "climate_plus_resource"] = (
        "climate_only_resource_excluded"
    )
    planner_score_resource_weight_pct: float = Field(default=0, ge=0, le=100)
    planner_score_resource_terms_available: bool = False
    compliance_pct_today: float | None = None
    observed_minute_evidence: ObservedMinuteEvidence = Field(default_factory=ObservedMinuteEvidence)
    cost_today_usd: float | None = None
    cost_today_estimate_usd: float | None = None
    water_today_gal: float | None = None
    water_today_observed_gal: float | None = None
    water_today_quality: str = "unavailable"
    water_today_available_for_scoring: bool = False
    runtime_modeled_kwh: float | None = None
    runtime_modeled_kwh_low: float | None = None
    runtime_modeled_kwh_high: float | None = None
    runtime_model_quality: str = "unavailable"
    runtime_model_available_for_scoring: bool = False
    partial_measured_kwh: float | None = None
    partial_meter_coverage_pct: float | None = None
    partial_meter_quality: str = "unavailable"
    partial_meter_available_for_scoring: bool = False
    open_critical_high_alerts: int = Field(..., ge=0)
    climate_action_log_age_s: int | None = Field(default=None, ge=0)
    controller_climate_action: str | None = None
    controller_priority_axis: str | None = None
    controller_temp_target_delta_f: float | None = None
    controller_vpd_target_delta_kpa: float | None = None
    controller_temp_band_error_f: float | None = None
    controller_vpd_band_error_kpa: float | None = None
    controller_moisture_assist_state: str | None = None
    controller_wet_assist_allowed: bool | None = None
    controller_wet_assist_block_reason: str | None = None
    controller_fog_allowed: bool | None = None
    controller_fog_block_reason: str | None = None
    controller_relay_truth: dict[str, Any] | None = None
    controller_sensor_status: dict[str, Any] | None = None
    data_health_status: Literal["ok", "warn", "fail"]
    data_health_warnings: list[PublicDataHealthCheck] = Field(default_factory=list)


class PublicBandTraceLatest(BaseModel):
    """Latest sample from the canonical band trace surface."""

    model_config = ConfigDict(extra="ignore")

    ts: AwareDatetime
    greenhouse_id: str
    temp_avg: float | None = None
    vpd_avg: float | None = None
    temp_avg_smooth_15m: float | None = None
    vpd_avg_smooth_30m: float | None = None
    crop_temp_low: float | None = None
    crop_temp_high: float | None = None
    crop_vpd_low: float | None = None
    crop_vpd_high: float | None = None
    house_vpd_low: float | None = None
    house_vpd_high: float | None = None
    fw_temp_low: float | None = None
    fw_temp_high: float | None = None
    fw_vpd_low: float | None = None
    fw_vpd_high: float | None = None
    rb_temp_low: float | None = None
    rb_temp_high: float | None = None
    rb_vpd_low: float | None = None
    rb_vpd_high: float | None = None
    crop_both_in_band: bool | None = None
    fw_both_in_band: bool | None = None
    readback_matches_fw_band: bool | None = None
    trace_quality_flag: str


class PublicBandTraceSummary(BaseModel):
    """Recent compliance summary derived from canonical band trace rows."""

    model_config = ConfigDict(extra="ignore")

    hours: int = Field(..., ge=1)
    sample_count: int = Field(..., ge=0)
    crop_temp_compliance_pct: float | None = Field(default=None, ge=0, le=100)
    crop_vpd_compliance_pct: float | None = Field(default=None, ge=0, le=100)
    crop_both_compliance_pct: float | None = Field(default=None, ge=0, le=100)
    fw_temp_compliance_pct: float | None = Field(default=None, ge=0, le=100)
    fw_vpd_compliance_pct: float | None = Field(default=None, ge=0, le=100)
    fw_both_compliance_pct: float | None = Field(default=None, ge=0, le=100)
    readback_match_pct: float | None = Field(default=None, ge=0, le=100)
    ok_trace_pct: float | None = Field(default=None, ge=0, le=100)


class PublicBandTraceResponse(BaseModel):
    """GET /api/v1/public/band-trace."""

    model_config = ConfigDict(extra="ignore")

    generated_at: AwareDatetime
    greenhouse_id: str
    latest: PublicBandTraceLatest | None = None
    summary: PublicBandTraceSummary


class PublicGpuPowerPoint(BaseModel):
    """One bucketed GPU power sample for public inference-fleet charts."""

    model_config = ConfigDict(extra="ignore")

    ts: AwareDatetime
    host: str
    vm_name: str | None = None
    gpu: str
    watts: float


class PublicGpuPowerLatest(BaseModel):
    """Latest mirrored GPU telemetry for one host/GPU."""

    model_config = ConfigDict(extra="ignore")

    ts: AwareDatetime
    host: str
    vm_name: str | None = None
    purpose: str | None = None
    gpu: str
    device: str | None = None
    model_name: str | None = None
    watts: float
    gpu_util_pct: float | None = None
    temperature_c: float | None = None
    memory_used_mb: float | None = None
    memory_free_mb: float | None = None
    age_s: int | None = Field(default=None, ge=0)


class PublicInfraCpuPoint(BaseModel):
    """One bucketed CPU/memory sample for public infrastructure charts."""

    model_config = ConfigDict(extra="ignore")

    ts: AwareDatetime
    host: str
    vm_name: str | None = None
    cpu_util_pct: float | None = None
    memory_used_pct: float | None = None


class PublicInfraCpuLatest(BaseModel):
    """Latest mirrored CPU/memory telemetry for one host."""

    model_config = ConfigDict(extra="ignore")

    ts: AwareDatetime
    host: str
    vm_name: str | None = None
    purpose: str | None = None
    cpu_util_pct: float | None = None
    load1: float | None = None
    cores: int | None = Field(default=None, ge=0)
    memory_used_pct: float | None = None
    age_s: int | None = Field(default=None, ge=0)


class PublicGpuPowerResponse(BaseModel):
    """GET /api/v1/public/gpu-power."""

    model_config = ConfigDict(extra="ignore")

    generated_at: AwareDatetime
    greenhouse_id: str
    source: str
    hours: int = Field(..., ge=1, le=168)
    step_minutes: int = Field(..., ge=1, le=60)
    latest_total_watts: float | None = None
    latest_gpu_count: int = Field(..., ge=0)
    latest_avg_gpu_util_pct: float | None = Field(default=None, ge=0, le=100)
    peak_total_watts: float | None = None
    avg_total_watts: float | None = None
    latest_avg_cpu_util_pct: float | None = Field(default=None, ge=0, le=100)
    peak_avg_cpu_util_pct: float | None = Field(default=None, ge=0, le=100)
    latest: list[PublicGpuPowerLatest] = Field(default_factory=list, max_length=32)
    series: list[PublicGpuPowerPoint] = Field(default_factory=list, max_length=12000)
    cpu_latest: list[PublicInfraCpuLatest] = Field(default_factory=list, max_length=32)
    cpu_series: list[PublicInfraCpuPoint] = Field(default_factory=list, max_length=12000)


class ZoneListItem(BaseModel):
    """GET /api/v1/zones — one row per zone."""

    model_config = ConfigDict(extra="ignore")

    zone: str
    active_crops: int = Field(..., ge=0)
    current_temp: float | None = None


class ZoneObservation(BaseModel):
    """Nested observation row for zone detail response."""

    model_config = ConfigDict(extra="ignore")

    ts: AwareDatetime
    obs_type: str
    health_score: float | None = None
    notes: str | None = None
    crop_name: str | None = None


class ZoneDetail(BaseModel):
    """GET /api/v1/zones/{zone}."""

    model_config = ConfigDict(extra="ignore")

    zone: str
    crops: list[dict]  # Permissive — zone-detail returns a mix of crop fields
    recent_observations: list[ZoneObservation]


class CropListItem(BaseModel):
    """GET /api/v1/crops — crop row + joined `latest_health`."""

    model_config = ConfigDict(extra="ignore")

    id: int
    name: str
    variety: str | None = None
    position: str
    zone: str
    planted_date: DateType
    expected_harvest: DateType | None = None
    stage: str = "seed"
    count: int | None = None
    seed_lot_id: str | None = None
    supplier: str | None = None
    base_temp_f: float = 50.0
    target_dli: float | None = None
    target_vpd_low: float | None = None
    target_vpd_high: float | None = None
    notes: str | None = None
    is_active: bool = True
    created_at: AwareDatetime | None = None
    updated_at: AwareDatetime | None = None
    greenhouse_id: str = "vallery"
    latest_health: float | None = Field(default=None, ge=0, le=1)


class CropRecentObservation(BaseModel):
    """Lightweight observation summary for crop-detail responses."""

    model_config = ConfigDict(extra="ignore")

    ts: AwareDatetime
    obs_type: str
    health_score: float | None = None
    notes: str | None = None
    source: str | None = None


class CropDetail(CropListItem):
    """GET /api/v1/crops/{id} — crop row + recent observations."""

    recent_observations: list[CropRecentObservation] = Field(default_factory=list)


class CropHealthSummaryItem(BaseModel):
    """GET /api/v1/health/summary — one row per active crop."""

    model_config = ConfigDict(extra="ignore")

    name: str
    zone: str
    position: str
    stage: str
    avg_health: float | None = Field(default=None, ge=0, le=1)
    obs_count: int = Field(..., ge=0)
    last_observed: AwareDatetime | None = None


class HealthTrendPoint(BaseModel):
    """GET /api/v1/crops/{id}/health — one observation on the health timeline."""

    model_config = ConfigDict(extra="ignore")

    ts: AwareDatetime
    health_score: float | None = Field(default=None, ge=0, le=1)
    obs_type: str
    notes: str | None = None
    source: str | None = None


class ObservationWithCrop(Observation):
    """GET /api/v1/observations/recent — observation row joined with crop fields."""

    model_config = ConfigDict(extra="ignore")

    crop_name: str | None = None
    crop_zone: str | None = None


# GET /setpoints returns a flat dict[str, float] — FastAPI handles that
# natively; no response model needed (the endpoint returns a plain dict).
