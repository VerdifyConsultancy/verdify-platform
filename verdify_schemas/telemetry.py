"""Telemetry row schemas — 1:1 onto ESP32-backed DB tables.

Each model mirrors the shape of a hypertable the ingestor writes to:
- ClimateRow           → climate          (30 s cadence; 80 cols)
- Diagnostics          → diagnostics      (60 s cadence)
- EquipmentStateEvent  → equipment_state  (on-change)
- EnergySample         → energy           (5 min cadence)
- SystemStateRow       → system_state     (key/value state)
- OverrideEvent        → override_events  (firmware-emitted silent overrides)

Principles:
- `extra="ignore"` — tolerate DB column additions without breaking old readers.
- Most numeric fields are `float | None` because the schema is permissive; the
  ingestor's per-path validators enforce physical ranges (ClimateRow.rh_avg
  stays in [0,100], etc.) where it matters.
"""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from .climate_intent import ClimateAction, ClimatePriorityAxis, MoistureAssistState, MoistureZone

OVERRIDE_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "occupancy_blocks_equipment",
        "fog_gate_rh",
        "fog_gate_temp",
        "fog_gate_window",
        "relief_cycle_breaker",
        "seal_blocked_temp",
        "vpd_dry_override",
        # Firmware field is summer_vent_active; controls.yaml publishes the
        # historical short tag to override_events.
        "summer_vent",
        "vent_mist_assist",
        "fog_heat_assist",
    }
)


class ClimateRow(BaseModel):
    """climate hypertable row — the 30 s telemetry sweep from the ESP32.

    ~80 columns today (zone temps + RH + VPD for N/S/E/W/case/control/intake,
    dew point, enthalpy, lux/DLI/PPFD, flow + water totals, outdoor (Tempest),
    hydroponics (YINMIK), soil moisture, leaf wetness, wind). Adding a new
    column in the DB is additive; the schema ignores what it doesn't know.
    """

    model_config = ConfigDict(extra="ignore")

    ts: AwareDatetime
    greenhouse_id: str = "vallery"

    # Zone temps
    temp_avg: float | None = None
    temp_north: float | None = None
    temp_south: float | None = None
    temp_east: float | None = None
    temp_west: float | None = None
    temp_case: float | None = None
    temp_control: float | None = None
    temp_intake: float | None = None

    # Zone RH
    rh_avg: float | None = Field(default=None, ge=0, le=100)
    rh_north: float | None = Field(default=None, ge=0, le=100)
    rh_south: float | None = Field(default=None, ge=0, le=100)
    rh_east: float | None = Field(default=None, ge=0, le=100)
    rh_west: float | None = Field(default=None, ge=0, le=100)
    rh_case: float | None = Field(default=None, ge=0, le=100)

    # Zone VPD
    vpd_avg: float | None = Field(default=None, ge=0, le=20)
    vpd_north: float | None = Field(default=None, ge=0, le=20)
    vpd_south: float | None = Field(default=None, ge=0, le=20)
    vpd_east: float | None = Field(default=None, ge=0, le=20)
    vpd_west: float | None = Field(default=None, ge=0, le=20)
    vpd_control: float | None = Field(default=None, ge=0, le=20)

    # Psychrometrics
    dew_point: float | None = None
    abs_humidity: float | None = None
    enthalpy_delta: float | None = None

    # Light
    lux: float | None = None
    solar_irradiance_w_m2: float | None = Field(default=None, ge=0)
    dli_today: float | None = None
    ppfd: float | None = None
    dli_par_today: float | None = None

    # Water
    flow_gpm: float | None = None
    water_total_gal: float | None = None
    mister_water_today: float | None = None

    # Outdoor (Tempest + HA)
    outdoor_temp_f: float | None = None
    outdoor_rh_pct: float | None = Field(default=None, ge=0, le=100)
    outdoor_lux: float | None = None
    outdoor_illuminance: float | None = None
    pressure_hpa: float | None = None
    air_density_kg_m3: float | None = Field(default=None, ge=0)
    precip_in: float | None = Field(default=None, ge=0)
    precip_intensity_in_h: float | None = Field(default=None, ge=0)
    uv_index: float | None = Field(default=None, ge=0, le=20)
    wind_speed_mph: float | None = Field(default=None, ge=0)
    wind_direction_deg: float | None = Field(default=None, ge=0, le=360)
    wind_gust_mph: float | None = Field(default=None, ge=0)
    wind_lull_mph: float | None = Field(default=None, ge=0)
    wind_speed_avg_mph: float | None = Field(default=None, ge=0)
    wind_direction_avg_deg: float | None = Field(default=None, ge=0, le=360)
    feels_like_f: float | None = None
    wet_bulb_temp_f: float | None = None
    vapor_pressure_inhg: float | None = Field(default=None, ge=0)
    lightning_count: int | None = None
    lightning_avg_dist_mi: float | None = Field(default=None, ge=0)
    solar_altitude_deg: float | None = None
    solar_azimuth_deg: float | None = None

    # Hydroponics (YINMIK)
    co2_ppm: float | None = Field(default=None, ge=0)
    hydro_ph: float | None = Field(default=None, ge=0, le=14)
    hydro_ec_us_cm: float | None = Field(default=None, ge=0)
    hydro_tds_ppm: float | None = Field(default=None, ge=0)
    hydro_water_temp_f: float | None = None
    hydro_orp_mv: float | None = None
    hydro_battery_pct: float | None = Field(default=None, ge=0, le=100)

    # Nutrient runoff (fertigation)
    ph_input: float | None = Field(default=None, ge=0, le=14)
    ec_input: float | None = Field(default=None, ge=0)
    ph_runoff_wall: float | None = Field(default=None, ge=0, le=14)
    ec_runoff_wall: float | None = Field(default=None, ge=0)
    ph_runoff_center: float | None = Field(default=None, ge=0, le=14)
    ec_runoff_center: float | None = Field(default=None, ge=0)

    # Soil moisture
    moisture_north: float | None = None
    moisture_south: float | None = None
    moisture_center: float | None = None
    soil_moisture_south_1: float | None = Field(default=None, ge=0, le=100)
    soil_temp_south_1: float | None = Field(default=None, ge=-40, le=160)
    soil_ec_south_1: float | None = Field(default=None, ge=0)
    soil_moisture_south_2: float | None = Field(default=None, ge=0, le=100)
    soil_temp_south_2: float | None = Field(default=None, ge=-40, le=160)
    soil_moisture_west: float | None = None
    soil_temp_west: float | None = None

    # Leaf telemetry
    leaf_temp_north: float | None = None
    leaf_temp_south: float | None = None
    leaf_wetness_north: float | None = None
    leaf_wetness_south: float | None = None

    # Intake sensor
    intake_rh: float | None = Field(default=None, ge=0, le=100)
    intake_vpd: float | None = Field(default=None, ge=0, le=20)

    # firmware-v2 on-chip telemetry (#327) — solar ephemeris + house targets +
    # per-zone VPD targets/deltas the ESP32 computes on-chip and publishes.
    solar_phase: float | None = None
    solar_sunrise_min: int | None = None
    solar_noon_min: int | None = None
    solar_sunset_min: int | None = None
    house_temp_target_f: float | None = None
    house_temp_delta_f: float | None = None
    house_vpd_target: float | None = None
    house_vpd_delta: float | None = None
    vpd_target_center: float | None = None
    vpd_target_south: float | None = None
    vpd_target_west: float | None = None
    vpd_target_east: float | None = None
    vpd_delta_center: float | None = None
    vpd_delta_south: float | None = None
    vpd_delta_west: float | None = None
    vpd_delta_east: float | None = None


class Diagnostics(BaseModel):
    """diagnostics hypertable row — ESP32 health heartbeat every 60 s."""

    model_config = ConfigDict(extra="ignore")

    ts: AwareDatetime
    greenhouse_id: str = "vallery"
    wifi_rssi: float | None = Field(default=None, ge=-120, le=0)
    heap_bytes: float | None = Field(default=None, ge=0)
    heap_min_free_kb: float | None = Field(default=None, ge=0)
    heap_largest_free_block_kb: float | None = Field(default=None, ge=0)
    uptime_s: float | None = Field(default=None, ge=0)
    probe_health: str | None = None
    reset_reason: str | None = None
    firmware_version: str | None = None
    # FW-10 + OBS-3 additions
    active_probe_count: int | None = Field(default=None, ge=0, le=4)
    relief_cycle_count: int | None = Field(default=None, ge=0)
    vent_latch_timer_s: int | None = Field(default=None, ge=0, le=1800)
    sealed_timer_s: int | None = Field(default=None, ge=0)
    vpd_watch_timer_s: int | None = Field(default=None, ge=0)
    mist_backoff_timer_s: int | None = Field(default=None, ge=0)
    vent_mist_assist_active: int | None = Field(default=None, ge=0, le=1)
    effective_heat_target_f: float | None = Field(default=None, ge=0, le=120)
    effective_cool_stage2_delta_f: float | None = Field(default=None, ge=0, le=30)
    effective_vpd_hysteresis_kpa: float | None = Field(default=None, ge=0, le=5)
    effective_dehum_aggressive_kpa: float | None = Field(default=None, ge=0, le=5)
    controller_time_epoch: int | None = Field(default=None, ge=0)
    controller_local_hour: int | None = Field(default=None, ge=0, le=23)
    sntp_valid: int | None = Field(default=None, ge=0, le=1)
    sntp_miss_count: int | None = Field(default=None, ge=0)
    last_sntp_sync_age_s: int | None = Field(default=None, ge=0)


# Every equipment_state row asserts one of these. Must cover every value in
# ingestor/entity_map.py EQUIPMENT_BINARY_MAP + EQUIPMENT_SWITCH_MAP plus the
# HA-sync emission set in tasks.py (lights, config switches, occupancy).
# Sprint 24 hotfix: added the 16 names topology Sprint 22 missed — without
# these, equipment_state events were silently dropped at INSERT time.
EquipmentId = Literal[
    # Core relays (ESP32 BinarySensor)
    "fan1",
    "fan2",
    "vent",
    "fog",
    "heat1",
    "heat2",
    # Misting zones (ESP32 Switch)
    "mister_south",
    "mister_west",
    "mister_center",
    "mister_any",
    "mister_south_fert",
    "mister_west_fert",
    # Drip zones (ESP32 Switch)
    "drip_wall",
    "drip_center",
    "drip_wall_fert",
    "drip_center_fert",
    "fert_master_valve",
    # Water-safety status (ESP32 BinarySensor, derived in ingestor tasks)
    "water_flowing",
    "leak_detected",
    # Grow lights — both the legacy short names and the live entity_map ones
    "gl1",
    "gl2",
    "grow_light",
    "grow_light_main",
    "grow_light_grow",
    # Internal controller modes (not emitted today; reserved)
    "dehum",
    "safety_dehum",
    # Occupancy + door
    "occupancy",
    "door_open",
    # Firmware breaker / burst states (ESP32 BinarySensor)
    "fan_burst_active",
    "fog_burst_active",
    "vent_bypass_active",
    "occupancy_quiet_override_active",
    # Legacy firmware time-validity equipment stream retained in live history
    # and sensor_registry; current firmware uses diagnostics.sntp_valid.
    "sntp_status",
    # Firmware gates / health (ESP32 BinarySensor)
    "mister_budget_exceeded",
    "economiser_blocked",
    "heap_pressure_warning",
    "heap_pressure_critical",
    # Config switches (ESP32 Switch / HA switch sync)
    "economiser_enabled",
    "fog_closes_vent",
    "gl_auto_mode",
    "irrigation_enabled",
    "irrigation_wall_enabled",
    "irrigation_center_enabled",
    "irrigation_weather_skip",
    "occupancy_inhibit",
]


class EquipmentStateEvent(BaseModel):
    """equipment_state hypertable row — on-change relay events.

    `equipment` is the closed set defined by the `EquipmentId` Literal. A
    drift test in tests/test_drift_guards.py confirms it against the
    dispatcher's emission set.
    """

    model_config = ConfigDict(extra="ignore")

    ts: AwareDatetime
    equipment: EquipmentId
    state: bool
    greenhouse_id: str = "vallery"


class EnergySample(BaseModel):
    """energy hypertable row — 5 min from Shelly EM50 + derived breakdown."""

    model_config = ConfigDict(extra="ignore")

    ts: AwareDatetime
    greenhouse_id: str = "vallery"
    watts_total: float | None = None  # Signed (may be negative during export)
    watts_heat: float | None = None
    watts_fans: float | None = None
    watts_other: float | None = None
    kwh_today: float | None = Field(default=None, ge=0)


class SystemStateRow(BaseModel):
    """system_state hypertable row — key/value persistent state
    (e.g., greenhouse_state, occupancy_active, door_open)."""

    model_config = ConfigDict(extra="ignore")

    ts: AwareDatetime
    entity: str
    value: str  # Free-form — mode names, booleans-as-strings, floats-as-strings all land here
    greenhouse_id: str = "vallery"


class ClimateActionLogRow(BaseModel):
    """climate_action_log hypertable row — structured controller decision.

    This is the durable counterpart to firmware ClimateIntent text sensors.
    It captures selected action, band error, wet/fog authority, relay truth, and
    plan correlation so controller decisions can be graphed and audited without
    reconstructing latest key/value state.
    """

    model_config = ConfigDict(extra="ignore")

    ts: AwareDatetime
    greenhouse_id: str = "vallery"
    climate_action: ClimateAction
    priority_axis: ClimatePriorityAxis
    temp_low_f: float | None = None
    temp_target_f: float | None = None
    temp_high_f: float | None = None
    vpd_low_kpa: float | None = None
    vpd_target_kpa: float | None = None
    vpd_high_kpa: float | None = None
    temp_target_delta_f: float | None = None
    vpd_target_delta_kpa: float | None = None
    temp_band_error_f: float | None = None
    vpd_band_error_kpa: float | None = None
    moisture_assist_state: MoistureAssistState | None = None
    moisture_zone: MoistureZone = "none"
    wet_assist_allowed: bool = False
    wet_assist_block_reason: str | None = None
    fog_allowed: bool = False
    fog_block_reason: str | None = None
    relay_truth: dict = Field(default_factory=dict)
    resource_cost_estimate: dict = Field(default_factory=dict)
    climate_intent_version: str | None = None
    plan_id: str | None = None
    trigger_id: str | None = None
    planner_instance: str | None = None
    sensor_status: dict = Field(default_factory=dict)
    candidate_summary: str | None = None
    source_system_state: dict = Field(default_factory=dict)


class OverrideEvent(BaseModel):
    """override_events hypertable row — OBS-1e silent firmware overrides.

    `override_type` is a comma-separated set of flag names (see
    firmware/lib/greenhouse_types.h OverrideFlags). `details` is a JSONB blob
    with the per-flag state at emission time.
    """

    model_config = ConfigDict(extra="ignore")

    ts: AwareDatetime
    override_type: str
    mode: str | None = None  # controller mode at emission (SEALED_MIST, VENTILATE, ...)
    details: dict | None = None
    greenhouse_id: str = "vallery"

    @field_validator("override_type")
    @classmethod
    def known_override_type(cls, v: str) -> str:
        parts = [part.strip() for part in v.split(",") if part.strip()]
        if not parts or parts == ["none"]:
            raise ValueError("override_type must contain at least one active override flag")
        unknown = sorted(part for part in parts if part not in OVERRIDE_EVENT_TYPES)
        if unknown:
            raise ValueError(f"Unknown override_type(s): {unknown}; expected one of {sorted(OVERRIDE_EVENT_TYPES)}")
        return v
