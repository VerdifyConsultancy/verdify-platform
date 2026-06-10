"""Local mirror of Verdify-specific plan contract details.

This module captures the planner-side copy of the Verdify contract details that
matter for `set_plan`, especially Tier 1 parameters and plan-id shaping. It
connects planner proposal materialization to Verdify-compatible payload output.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping


# Mirrors Verdify's Tier 1 tunable registry for contract version 2026-05-24.
# The planner is standalone, so this list is intentionally copied instead of
# importing Verdify packages at runtime.
TIER1_PLAN_DEFAULTS: dict[str, float] = {
    "cold_vent_guard_delta_f": 10.0,
    "cool_exit_hysteresis_f": 1.5,
    "cool_stage2_over_high_f": 1.0,
    "direct_wet_stress_latest_hour": 22.0,
    "direct_wet_stress_min_dew_margin_f": 8.0,
    "direct_wet_stress_vpd_margin_kpa": 0.05,
    "dwell_gate_ms": 300000.0,
    "enthalpy_close": 1.0,
    "enthalpy_open": -2.0,
    "fog_escalation_kpa": 0.4,
    "fog_stress_min_dew_margin_f": 10.0,
    "fog_stress_window_latest_hour": 19.0,
    "heat_hysteresis": 1.0,
    "min_fog_off_s": 60.0,
    "min_fog_on_s": 60.0,
    "mist_backoff_s": 600.0,
    "mist_max_closed_vent_s": 600.0,
    "mist_thermal_relief_s": 90.0,
    "mister_all_delay_s": 300.0,
    "mister_all_kpa": 1.9,
    "mister_engage_delay_s": 45.0,
    "mister_engage_kpa": 1.6,
    "mister_pulse_gap_s": 45.0,
    "mister_pulse_on_s": 60.0,
    "mister_vpd_weight": 1.5,
    "mister_water_budget_gal": 300.0,
    "outdoor_staleness_max_s": 600.0,
    "sw_cool_all_fans_at_high_enabled": 0.0,
    "sw_direct_wet_stress_override_enabled": 0.0,
    "sw_dwell_gate_enabled": 0.0,
    "sw_fog_closes_vent": 1.0,
    "sw_fog_stress_window_extend_enabled": 0.0,
    "sw_mister_closes_vent": 0.0,
    "sw_summer_vent_enabled": 1.0,
    "temp_hysteresis": 1.5,
    "vent_prefer_dp_delta_f": 5.0,
    "vent_prefer_temp_delta_f": 5.0,
    "vpd_hysteresis": 0.3,
    "vpd_watch_dwell_s": 60.0,
}

TIER1_PLAN_PARAM_NAMES: tuple[str, ...] = tuple(TIER1_PLAN_DEFAULTS)

CLIMATE_INTENT_CONTRACT_VERSION = "2026-05-24"

CLIMATE_INTENT_DEFAULTS: dict[str, float] = {
    "temp_target_f": 72.0,
    "temp_band_f": 6.0,
    "vpd_target_kpa": 1.0,
    "vpd_band_kpa": 0.5,
    "forecast_temp_bias_f": 0.0,
    "forecast_vpd_bias_kpa": 0.0,
    "solar_precool_gain_f": 0.0,
    "thermal_lead_time_min": 30.0,
    "economizer_temp_advantage_f": 5.0,
    "economizer_dewpoint_advantage_f": 5.0,
    "moisture_engage_vpd_excess_kpa": 0.05,
    "all_zone_vpd_excess_kpa": 0.25,
    "mist_duty_limit_pct": 25.0,
    "fog_escalate_vpd_excess_kpa": 0.4,
    "dew_margin_floor_f": 10.0,
    "wet_cutoff_hour": 19.0,
    "daily_mist_budget_gal": 300.0,
    "resource_sensitivity": 0.5,
    "relay_churn_penalty": 0.5,
}

CLIMATE_INTENT_FIELD_NAMES: tuple[str, ...] = tuple(CLIMATE_INTENT_DEFAULTS)

CLIMATE_INTENT_RANGES: dict[str, tuple[float, float]] = {
    "temp_target_f": (35.0, 95.0),
    "temp_band_f": (3.0, 12.0),
    "vpd_target_kpa": (0.35, 2.8),
    "vpd_band_kpa": (0.35, 1.2),
    "forecast_temp_bias_f": (-4.0, 4.0),
    "forecast_vpd_bias_kpa": (-0.4, 0.4),
    "solar_precool_gain_f": (0.0, 4.0),
    "thermal_lead_time_min": (0.0, 90.0),
    "economizer_temp_advantage_f": (1.0, 15.0),
    "economizer_dewpoint_advantage_f": (1.0, 15.0),
    "moisture_engage_vpd_excess_kpa": (0.0, 0.5),
    "all_zone_vpd_excess_kpa": (0.05, 0.8),
    "mist_duty_limit_pct": (0.0, 100.0),
    "fog_escalate_vpd_excess_kpa": (0.1, 0.8),
    "dew_margin_floor_f": (3.0, 15.0),
    "wet_cutoff_hour": (17.0, 24.0),
    "daily_mist_budget_gal": (0.0, 300.0),
    "resource_sensitivity": (0.0, 1.0),
    "relay_churn_penalty": (0.0, 1.0),
}


@dataclass(frozen=True)
class Tier1PlanExtraction:
    params: dict[str, float]
    missing: tuple[str, ...]
    non_numeric: tuple[str, ...]
    ignored: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return not self.missing and not self.non_numeric

    @property
    def coverage_status(self) -> str:
        if self.complete:
            return "complete"
        parts: list[str] = []
        if self.missing:
            parts.append(f"missing:{len(self.missing)}")
        if self.non_numeric:
            parts.append(f"non_numeric:{len(self.non_numeric)}")
        return ";".join(parts)


def planner_plan_id(triggered_at: str | None, trigger_id: str | None = None) -> str:
    if triggered_at:
        try:
            timestamp = datetime.fromisoformat(triggered_at.replace("Z", "+00:00"))
            return f"iris-{timestamp.strftime('%Y%m%d-%H%M')}"
        except ValueError:
            pass

    # Keep the fallback deterministic so retries/replays produce the same
    # `plan_id` even when the source timestamp is malformed or absent.
    stable_source = trigger_id or "planner-fallback"
    digest = hashlib.sha256(stable_source.encode("utf-8")).digest()
    date_component = int.from_bytes(digest[:4], "big") % 100_000_000
    time_component = int.from_bytes(digest[4:6], "big") % 10_000
    return f"iris-{date_component:08d}-{time_component:04d}"


def _coerce_plan_number(value: object) -> float | None:
    if isinstance(value, bool):
        numeric = 1.0 if value else 0.0
    elif isinstance(value, (int, float)):
        numeric = float(value)
    else:
        return None
    if not math.isfinite(numeric):
        return None
    return numeric


def _bounded(name: str, value: float) -> float:
    lo, hi = CLIMATE_INTENT_RANGES[name]
    return min(hi, max(lo, value))


def _mapping_number(source: object, name: str) -> float | None:
    if not isinstance(source, Mapping):
        return None
    return _coerce_plan_number(source.get(name))


def _center_and_width(
    source: object,
    low_name: str,
    high_name: str,
    default_center: float,
    default_width: float,
) -> tuple[float, float]:
    low = _mapping_number(source, low_name)
    high = _mapping_number(source, high_name)
    if low is None or high is None or low >= high:
        return default_center, default_width
    return (low + high) / 2.0, high - low


def build_climate_intent(active_plan_summary: object) -> dict[str, float]:
    """Build the bounded Verdify ClimateIntent payload for a set_plan transition.

    The standalone planner emits this compact semantic surface. Verdify MCP is
    the single write boundary: it validates the intent, materializes it into the
    current Tier 1 firmware contract, and stores the original intent for audit.
    """

    params = dict(TIER1_PLAN_DEFAULTS)
    extraction = extract_tier1_plan_params(active_plan_summary)
    params.update(extraction.params)

    temp_target, temp_band = _center_and_width(
        active_plan_summary,
        "temp_low",
        "temp_high",
        CLIMATE_INTENT_DEFAULTS["temp_target_f"],
        CLIMATE_INTENT_DEFAULTS["temp_band_f"],
    )
    vpd_target, vpd_band = _center_and_width(
        active_plan_summary,
        "vpd_low",
        "vpd_high",
        CLIMATE_INTENT_DEFAULTS["vpd_target_kpa"],
        CLIMATE_INTENT_DEFAULTS["vpd_band_kpa"],
    )

    pulse_on = max(0.0, params["mister_pulse_on_s"])
    pulse_gap = max(0.0, params["mister_pulse_gap_s"])
    duty_pct = (
        100.0 * pulse_on / (pulse_on + pulse_gap) if pulse_on + pulse_gap > 0 else 0.0
    )
    dwell = params["dwell_gate_ms"]

    # all_zone_vpd_excess_kpa is the inverse of the canonical materializer
    # relation `mister_all_kpa = vpd_high + all_zone_vpd_excess_kpa`
    # (verdify_schemas.climate_intent.materialize_climate_intent_tier1). vpd_high
    # is the top of the active VPD band (vpd_target + vpd_band/2). The canonical
    # ClimateIntent enforces the moisture ladder all_zone >= moisture_engage, so
    # we floor the recovered value at the engage excess before range-clamping.
    moisture_engage_excess = params["direct_wet_stress_vpd_margin_kpa"]
    vpd_high = vpd_target + (vpd_band / 2.0)
    all_zone_vpd_excess_kpa = max(
        params["mister_all_kpa"] - vpd_high, moisture_engage_excess
    )

    intent = {
        **CLIMATE_INTENT_DEFAULTS,
        "temp_target_f": temp_target,
        "temp_band_f": temp_band,
        "vpd_target_kpa": vpd_target,
        "vpd_band_kpa": vpd_band,
        "economizer_temp_advantage_f": params["vent_prefer_temp_delta_f"],
        "economizer_dewpoint_advantage_f": params["vent_prefer_dp_delta_f"],
        "moisture_engage_vpd_excess_kpa": moisture_engage_excess,
        "all_zone_vpd_excess_kpa": all_zone_vpd_excess_kpa,
        "mist_duty_limit_pct": duty_pct,
        "fog_escalate_vpd_excess_kpa": params["fog_escalation_kpa"],
        "dew_margin_floor_f": max(
            params["direct_wet_stress_min_dew_margin_f"],
            params["fog_stress_min_dew_margin_f"],
        ),
        "wet_cutoff_hour": min(
            params["direct_wet_stress_latest_hour"],
            params["fog_stress_window_latest_hour"],
        ),
        "daily_mist_budget_gal": params["mister_water_budget_gal"],
        "resource_sensitivity": 1.0 - min(1.0, max(0.0, duty_pct / 100.0)),
        "relay_churn_penalty": (dwell - 60000.0) / (1800000.0 - 60000.0),
    }

    return {
        name: _bounded(name, float(intent[name])) for name in CLIMATE_INTENT_FIELD_NAMES
    }


def extract_tier1_plan_params(active_plan_summary: object) -> Tier1PlanExtraction:
    if not isinstance(active_plan_summary, Mapping):
        return Tier1PlanExtraction(
            params={},
            missing=TIER1_PLAN_PARAM_NAMES,
            non_numeric=(),
            ignored=(),
        )

    params: dict[str, float] = {}
    missing: list[str] = []
    non_numeric: list[str] = []
    for name in TIER1_PLAN_PARAM_NAMES:
        if name not in active_plan_summary:
            missing.append(name)
            continue
        numeric = _coerce_plan_number(active_plan_summary[name])
        if numeric is None:
            non_numeric.append(name)
            continue
        params[name] = numeric

    ignored = tuple(
        sorted(
            str(key) for key in active_plan_summary if key not in TIER1_PLAN_DEFAULTS
        )
    )
    return Tier1PlanExtraction(
        params=params,
        missing=tuple(missing),
        non_numeric=tuple(non_numeric),
        ignored=ignored,
    )
