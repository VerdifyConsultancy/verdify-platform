"""Climate-intent controller contract.

This is the narrow AI-facing surface for the next controller architecture:
planner emits bounded semantic intent; firmware keeps relay truth, safety,
interlocks, and candidate-action selection.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
    "temp_target_f",
    "temp_band_f",
    "vpd_target_kpa",
    "vpd_band_kpa",
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

    temp_target_f: float = Field(..., ge=35.0, le=95.0)
    temp_band_f: float = Field(..., ge=3.0, le=12.0)
    vpd_target_kpa: float = Field(..., ge=0.35, le=2.8)
    vpd_band_kpa: float = Field(..., ge=0.35, le=1.2)
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

    def temp_band(self) -> tuple[float, float]:
        half_width = self.temp_band_f / 2.0
        return self.temp_target_f - half_width, self.temp_target_f + half_width

    def vpd_band(self) -> tuple[float, float]:
        half_width = self.vpd_band_kpa / 2.0
        return self.vpd_target_kpa - half_width, self.vpd_target_kpa + half_width


class ClimateCandidateProjection(BaseModel):
    """Firmware/shadow-evaluator projection for one candidate action."""

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
    """Published controller/shadow decision observability record."""

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
