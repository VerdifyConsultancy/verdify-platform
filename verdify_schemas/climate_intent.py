"""Climate-intent controller contract.

This is the narrow AI-facing surface for the next controller architecture:
planner emits bounded semantic intent; firmware keeps relay truth, safety,
interlocks, and candidate-action selection.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .tunable_registry import REGISTRY, TIER1_REG, registry_value_error

CLIMATE_INTENT_CONTRACT_VERSION = "2026-05-24"

ClimateAction = Literal[
    "SENSOR_FAULT",
    "SAFETY_HEAT",
    "SAFETY_COOL",
    "HEAT",
    "IDLE",
    "VENT_COOL",
    "VENT_COOL_MIST_ASSIST",
    "VENT_COOL_FOG_ASSIST",
    "SEALED_HUMIDIFY",
    "SEALED_FOG",
    "DEHUM_VENT",
]

ClimatePriorityAxis = Literal["safety", "temp", "vpd", "resource"]
MoistureAssistState = Literal["inactive", "engage_delay", "pulse_on", "pulse_gap", "blocked", "served"]
MoistureZone = Literal["none", "south", "west", "center", "all"]
FogBlockReason = Literal[
    "none",
    "below_threshold",
    "time_window",
    "dew_margin",
    "rh_ceiling",
    "temp_low",
    "occupancy",
    "relay_min_off",
    "resource_budget",
]

CLIMATE_ACTIONS: tuple[str, ...] = (
    "SENSOR_FAULT",
    "SAFETY_HEAT",
    "SAFETY_COOL",
    "HEAT",
    "IDLE",
    "VENT_COOL",
    "VENT_COOL_MIST_ASSIST",
    "VENT_COOL_FOG_ASSIST",
    "SEALED_HUMIDIFY",
    "SEALED_FOG",
    "DEHUM_VENT",
)

CLIMATE_PRIORITY_ORDER: tuple[str, ...] = ("safety", "temp", "vpd", "resource")

CLIMATE_INTENT_FIELDS: tuple[str, ...] = (
    "forecast_temp_bias_f",
    "forecast_vpd_bias_kpa",
    "solar_precool_gain_f",
    "thermal_lead_time_min",
    "economizer_temp_advantage_f",
    "economizer_dewpoint_advantage_f",
    "moisture_engage_vpd_excess_kpa",
    "mist_duty_limit_pct",
    "fog_escalate_vpd_excess_kpa",
    "dew_margin_floor_f",
    "wet_cutoff_hour",
    "daily_mist_budget_gal",
    "resource_sensitivity",
    "relay_churn_penalty",
)


@dataclass(frozen=True)
class ClimateIntentFieldDoc:
    name: str
    meaning: str
    bounds: str
    firmware_impact: str


CLIMATE_INTENT_FIELD_DOCS: tuple[ClimateIntentFieldDoc, ...] = (
    ClimateIntentFieldDoc(
        "forecast_temp_bias_f",
        "Forecast-backed temperature pressure signal for the segment.",
        "-4..4F",
        "Raises or lowers cooling aggressiveness without changing dispatcher-owned temp_low/temp_target/temp_high.",
    ),
    ClimateIntentFieldDoc(
        "forecast_vpd_bias_kpa",
        "Forecast-backed dry/wet pressure signal for the segment.",
        "-0.4..0.4 kPa",
        "Shifts wet-action readiness without changing dispatcher-owned vpd_low/vpd_target/vpd_high.",
    ),
    ClimateIntentFieldDoc(
        "solar_precool_gain_f",
        "Solar ramp pressure that justifies cooling lead before peak heat.",
        "0..4F",
        "Tightens stage-2 cooling and fan readiness before high solar load arrives.",
    ),
    ClimateIntentFieldDoc(
        "thermal_lead_time_min",
        "How early forecast preconditioning may begin.",
        "0..90 min",
        "Planner audit context for lead timing; firmware safety and dispatcher timing still gate actuation.",
    ),
    ClimateIntentFieldDoc(
        "economizer_temp_advantage_f",
        "Outdoor temperature advantage needed before vent cooling is attractive.",
        "1..15F",
        "Materializes to vent preference and cold-vent guard thresholds.",
    ),
    ClimateIntentFieldDoc(
        "economizer_dewpoint_advantage_f",
        "Outdoor dewpoint advantage needed before dry-air decisions are attractive.",
        "1..15F",
        "Materializes to dewpoint preference for vent/dehumidification choices.",
    ),
    ClimateIntentFieldDoc(
        "moisture_engage_vpd_excess_kpa",
        "How far above dispatcher-owned vpd_high VPD may rise before mister assist is eligible.",
        "0..0.5 kPa",
        "Materializes mister and direct-wet thresholds relative to the active dispatcher VPD band.",
    ),
    ClimateIntentFieldDoc(
        "mist_duty_limit_pct",
        "Maximum climate-misting duty allowed during the segment.",
        "0..100%",
        "Materializes mister pulse duration, wet aggression, and resource budget gates.",
    ),
    ClimateIntentFieldDoc(
        "fog_escalate_vpd_excess_kpa",
        "How far above dispatcher-owned vpd_high VPD may rise before fog assist is eligible.",
        "0.1..0.8 kPa",
        "Materializes fog escalation and all-zone mister thresholds relative to the active VPD band.",
    ),
    ClimateIntentFieldDoc(
        "dew_margin_floor_f",
        "Minimum indoor air temperature minus dew point for wet climate actions.",
        "3..15F",
        "Materializes fog/direct-wet dew margin floors and blocks condensation-risk wetting.",
    ),
    ClimateIntentFieldDoc(
        "wet_cutoff_hour",
        "Latest local hour for climate wetting in this segment.",
        "17..24",
        "Materializes fog and direct-wet latest-hour limits.",
    ),
    ClimateIntentFieldDoc(
        "daily_mist_budget_gal",
        "Daily climate-water budget for mister use.",
        "0..300 gal",
        "Materializes the firmware mister water budget.",
    ),
    ClimateIntentFieldDoc(
        "resource_sensitivity",
        "Preference for conserving water/electricity after safety and band compliance.",
        "0..1",
        "Lengthens off dwell and reduces wet/cooling aggression when compliance allows it.",
    ),
    ClimateIntentFieldDoc(
        "relay_churn_penalty",
        "Preference for holding stable actions instead of changing modes frequently.",
        "0..1",
        "Materializes hysteresis, dwell, and mist delay values.",
    ),
)

CLIMATE_RELAY_FIELD_DENYLIST: frozenset[str] = frozenset(
    {
        "heat1",
        "heat2",
        "vent",
        "fan1",
        "fan2",
        "fog",
        "mister_south",
        "mister_west",
        "mister_center",
        "drip_wall",
        "drip_center",
        "fert_master",
    }
)

FOG_BLOCK_REASONS: tuple[str, ...] = (
    "none",
    "below_threshold",
    "time_window",
    "dew_margin",
    "rh_ceiling",
    "temp_low",
    "occupancy",
    "relay_min_off",
    "resource_budget",
)


class ClimateIntent(BaseModel):
    """Bounded semantic intent emitted by AI/planner for a forecast segment."""

    model_config = ConfigDict(extra="forbid")

    forecast_temp_bias_f: float = Field(0.0, ge=-4.0, le=4.0)
    forecast_vpd_bias_kpa: float = Field(0.0, ge=-0.4, le=0.4)
    solar_precool_gain_f: float = Field(0.0, ge=0.0, le=4.0)
    thermal_lead_time_min: float = Field(0.0, ge=0.0, le=90.0)
    economizer_temp_advantage_f: float = Field(2.0, ge=1.0, le=15.0)
    economizer_dewpoint_advantage_f: float = Field(2.0, ge=1.0, le=15.0)
    moisture_engage_vpd_excess_kpa: float = Field(0.05, ge=0.0, le=0.5)
    mist_duty_limit_pct: float = Field(25.0, ge=0.0, le=100.0)
    fog_escalate_vpd_excess_kpa: float = Field(0.25, ge=0.1, le=0.8)
    dew_margin_floor_f: float = Field(8.0, ge=3.0, le=15.0)
    wet_cutoff_hour: float = Field(19.0, ge=17.0, le=24.0)
    daily_mist_budget_gal: float = Field(300.0, ge=0.0, le=300.0)
    resource_sensitivity: float = Field(0.5, ge=0.0, le=1.0)
    relay_churn_penalty: float = Field(0.5, ge=0.0, le=1.0)


class ClimateCandidateProjection(BaseModel):
    """Firmware/replay-evaluator projection for one candidate action."""

    model_config = ConfigDict(extra="forbid")

    action: ClimateAction
    safety_ok: bool
    blocked_reasons: tuple[str, ...] = ()
    projected_temp_error_f: float = Field(..., ge=0.0)
    projected_vpd_error_kpa: float = Field(..., ge=0.0)
    resource_cost: float = Field(..., ge=0.0)
    relay_churn_cost: float = Field(..., ge=0.0)
    confidence: float = Field(..., ge=0.0, le=1.0)
    prior_action_hold_preference: float = Field(0.0, ge=0.0, le=1.0)


class ClimateResourceCostEstimate(BaseModel):
    """Estimated resource cost for the selected climate action."""

    model_config = ConfigDict(extra="forbid")

    water_gal: float = Field(0.0, ge=0.0)
    electric_kwh: float = Field(0.0, ge=0.0)
    gas_therm: float = Field(0.0, ge=0.0)


class ClimateActionDecision(BaseModel):
    """Published controller/replay decision observability record."""

    model_config = ConfigDict(extra="forbid")

    climate_action: ClimateAction
    priority_axis: ClimatePriorityAxis
    temp_error_f: float = Field(..., ge=0.0)
    vpd_error_kpa: float = Field(..., ge=0.0)
    candidate_summary: str = Field(..., min_length=1)
    moisture_assist_state: MoistureAssistState
    moisture_zone: MoistureZone = "none"
    next_mist_eligible_s: float | None = Field(None, ge=0.0)
    fog_margin_kpa: float | None = None
    fog_block_reason: str = "none"
    resource_cost_estimate: ClimateResourceCostEstimate = Field(default_factory=ClimateResourceCostEstimate)

    @field_validator("fog_block_reason")
    @classmethod
    def _validate_fog_block_reason(cls, value: str) -> str:
        reasons = [part.strip() for part in value.split(",") if part.strip()]
        if not reasons:
            raise ValueError("fog_block_reason must name at least one reason")
        unknown = sorted(set(reasons) - set(FOG_BLOCK_REASONS))
        if unknown:
            raise ValueError(f"unknown fog_block_reason values: {unknown}")
        if "none" in reasons and len(reasons) > 1:
            raise ValueError("fog_block_reason='none' cannot be combined with other reasons")
        return ",".join(reasons)


def climate_candidate_sort_key(candidate: ClimateCandidateProjection) -> tuple[float, float, float, float, float]:
    """Return the strict priority key for eligible candidate selection."""

    return (
        candidate.projected_temp_error_f,
        candidate.projected_vpd_error_kpa,
        candidate.resource_cost,
        candidate.relay_churn_cost,
        -candidate.prior_action_hold_preference,
    )


def choose_climate_candidate(candidates: Sequence[ClimateCandidateProjection]) -> ClimateCandidateProjection:
    """Choose the best safe candidate with lexicographic climate priority."""

    eligible = [candidate for candidate in candidates if candidate.safety_ok]
    if not eligible:
        raise ValueError("no safety-ok climate candidates")
    return min(eligible, key=climate_candidate_sort_key)


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool):
        numeric = 1.0 if value else 0.0
    elif isinstance(value, (int, float)):
        numeric = float(value)
    else:
        return None
    return numeric if math.isfinite(numeric) else None


def _clamp_tier1_value(parameter: str, value: float) -> float:
    spec = REGISTRY[parameter]
    if spec.kind == "switch":
        return 1.0 if value >= 0.5 else 0.0

    lo = spec.fw_clamp_lo if spec.fw_clamp_lo is not None else spec.min
    hi = spec.fw_clamp_hi if spec.fw_clamp_hi is not None else spec.max
    if lo is not None:
        value = max(float(lo), value)
    if hi is not None:
        value = min(float(hi), value)
    return float(value)


def _tier1_base_params(base_params: Mapping[str, object] | None = None) -> dict[str, float]:
    params = {name: float(REGISTRY[name].default) for name in TIER1_REG}
    for name, raw_value in (base_params or {}).items():
        if name not in TIER1_REG:
            continue
        numeric = _finite_number(raw_value)
        if numeric is not None:
            params[name] = _clamp_tier1_value(name, numeric)
    return params


def _base_registry_value(parameter: str, base_params: Mapping[str, object] | None = None) -> float:
    numeric = _finite_number((base_params or {}).get(parameter))
    if numeric is None:
        numeric = float(REGISTRY[parameter].default)
    return _clamp_tier1_value(parameter, numeric)


def materialize_climate_intent_tier1(
    intent: ClimateIntent,
    base_params: Mapping[str, object] | None = None,
) -> dict[str, float]:
    """Translate semantic ClimateIntent into the current Tier 1 firmware knobs.

    The end-state controller should consume ClimateIntent directly. Until that
    path replaces every legacy setpoint route, this adapter is the single bridge
    from the compact AI surface to the existing dispatcher/ESP32 contract.
    """

    params = _tier1_base_params(base_params)
    temp_high = _base_registry_value("temp_high", base_params)
    vpd_low = _base_registry_value("vpd_low", base_params)
    vpd_high = _base_registry_value("vpd_high", base_params)
    vpd_width = max(0.35, min(1.2, vpd_high - vpd_low))
    resource = max(0.0, min(1.0, intent.resource_sensitivity))
    churn = max(0.0, min(1.0, intent.relay_churn_penalty))
    duty = max(0.0, min(1.0, intent.mist_duty_limit_pct / 100.0))
    solar_pressure = max(0.0, min(1.0, intent.solar_precool_gain_f / 4.0))
    hot_forecast_pressure = max(0.0, min(1.0, intent.forecast_temp_bias_f / 4.0))
    dry_forecast_pressure = max(0.0, min(1.0, intent.forecast_vpd_bias_kpa / 0.4))
    wet_aggression = max(0.0, min(1.0, (duty + dry_forecast_pressure + (1.0 - resource)) / 3.0))

    params.update(
        {
            "cool_stage2_over_high_f": 1.8 - (0.7 * solar_pressure) - (0.4 * hot_forecast_pressure) + (0.3 * resource),
            "cool_exit_hysteresis_f": 0.7 + (1.8 * churn),
            "temp_hysteresis": 0.7 + (1.8 * churn),
            "sw_cool_all_fans_at_high_enabled": 1.0 if solar_pressure >= 0.5 or hot_forecast_pressure >= 0.5 else 0.0,
            "vent_prefer_temp_delta_f": intent.economizer_temp_advantage_f,
            "vent_prefer_dp_delta_f": intent.economizer_dewpoint_advantage_f,
            "cold_vent_guard_delta_f": max(6.0, min(15.0, intent.economizer_temp_advantage_f + 4.0)),
            "direct_wet_stress_vpd_margin_kpa": intent.moisture_engage_vpd_excess_kpa,
            "direct_wet_stress_min_dew_margin_f": intent.dew_margin_floor_f,
            "direct_wet_stress_latest_hour": intent.wet_cutoff_hour,
            "sw_direct_wet_stress_override_enabled": 1.0 if wet_aggression >= 0.35 else 0.0,
            "fog_escalation_kpa": intent.fog_escalate_vpd_excess_kpa,
            "fog_stress_min_dew_margin_f": intent.dew_margin_floor_f,
            "fog_stress_window_latest_hour": min(intent.wet_cutoff_hour, 22.0),
            "sw_fog_stress_window_extend_enabled": 1.0
            if intent.wet_cutoff_hour > 17.0 and wet_aggression >= 0.35
            else 0.0,
            "mister_engage_kpa": vpd_high + intent.moisture_engage_vpd_excess_kpa,
            "mister_all_kpa": vpd_high
            + max(intent.fog_escalate_vpd_excess_kpa, intent.moisture_engage_vpd_excess_kpa + 0.2),
            "mister_vpd_weight": 1.0 + (2.0 * wet_aggression),
            "mister_pulse_on_s": 15.0 + (75.0 * duty),
            "mister_pulse_gap_s": 15.0 + (75.0 * resource),
            "mister_engage_delay_s": 15.0 + (45.0 * churn),
            "mister_all_delay_s": 30.0 + (90.0 * churn),
            "mister_water_budget_gal": intent.daily_mist_budget_gal,
            "min_fog_on_s": 30.0 + (45.0 * max(dry_forecast_pressure, 1.0 - resource)),
            "min_fog_off_s": 30.0 + (120.0 * resource),
            "vpd_hysteresis": max(0.1, min(0.5, vpd_width * 0.25 + churn * 0.1)),
            "vpd_watch_dwell_s": 15.0 + (75.0 * churn),
            "dwell_gate_ms": 60000.0 + (300000.0 * churn),
            "sw_dwell_gate_enabled": 1.0,
            "sw_summer_vent_enabled": 1.0,
            "sw_fog_closes_vent": 1.0,
            "sw_mister_closes_vent": 0.0
            if temp_high >= 70.0 and wet_aggression >= 0.35
            else params["sw_mister_closes_vent"],
        }
    )

    materialized = {name: _clamp_tier1_value(name, params[name]) for name in TIER1_REG}
    errors = [error for name, value in materialized.items() if (error := registry_value_error(name, value))]
    if errors:
        raise ValueError("ClimateIntent materialization produced registry violations: " + "; ".join(errors))
    return {name: materialized[name] for name in sorted(materialized)}
