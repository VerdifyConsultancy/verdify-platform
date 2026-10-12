"""Canonical 48-field deployed ESPHome entity grids for source writers and strict execution.

These are physical Number/Switch setter steps. Policy-wire scales may be finer.
The ordinary climate-intent materializer snaps its own generated commands to
this grid; the experiment executor continues to reject off-grid input.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

type EntityType = Literal["number", "switch"]

@dataclass(frozen=True)
class EntityGrid:
    minimum: Decimal | None
    maximum: Decimal | None
    step: Decimal | None
    entity_type: EntityType = "number"


def _grid(minimum: str, maximum: str, step: str) -> EntityGrid:
    return EntityGrid(Decimal(minimum), Decimal(maximum), Decimal(step))


_SWITCH = EntityGrid(None, None, None, "switch")

# Permanent wire-id order.  Wire id 6 is retired and intentionally absent.
ENTITY_GRIDS: dict[str, EntityGrid] = {
    "band_track_fraction": _grid("0", "1", "0.05"),
    "cold_vent_guard_delta_f": _grid("0", "15", "0.5"),
    "cool_exit_hysteresis_f": _grid("0.3", "3", "0.1"),
    "cool_stage2_exit_hysteresis_f": _grid("0.3", "3", "0.1"),
    "cool_stage2_over_high_f": _grid("0", "3", "0.1"),
    "direct_wet_stress_min_dew_margin_f": _grid("3", "15", "0.5"),
    "direct_wet_stress_vpd_margin_kpa": _grid("0", "0.5", "0.05"),
    "dwell_gate_ms": _grid("60000", "1800000", "30000"),
    "enthalpy_close": _grid("-5", "20", "0.5"),
    "enthalpy_open": _grid("-5", "0", "0.5"),
    "fog_escalation_kpa": _grid("0.1", "0.5", "0.1"),
    "heat_hysteresis": _grid("0", "3", "0.1"),
    "min_fan_off_s": _grid("30", "300", "10"),
    "min_fan_on_s": _grid("30", "300", "10"),
    "min_fog_off_s": _grid("15", "300", "15"),
    "min_fog_on_s": _grid("15", "300", "15"),
    "min_heat_off_s": _grid("60", "600", "10"),
    "min_heat_on_s": _grid("30", "300", "10"),
    "min_vent_off_s": _grid("10", "300", "10"),
    "min_vent_on_s": _grid("10", "300", "10"),
    "mist_backoff_s": _grid("60", "3600", "60"),
    "mist_max_closed_vent_s": _grid("120", "900", "60"),
    "mist_thermal_relief_s": _grid("30", "300", "30"),
    "mister_all_delay_s": _grid("60", "600", "30"),
    "mister_all_kpa": _grid("1", "2.5", "0.05"),
    "mister_center_penalty": _grid("0", "1", "0.1"),
    "mister_engage_delay_s": _grid("30", "300", "30"),
    "mister_engage_kpa": _grid("0.5", "2.5", "0.05"),
    "mister_min_off_s": _grid("30", "120", "5"),
    "mister_pulse_gap_s": _grid("10", "60", "5"),
    "mister_pulse_on_s": _grid("30", "90", "5"),
    "mister_vpd_weight": _grid("0.5", "3", "0.5"),
    "mister_water_budget_gal": _grid("100", "300", "10"),
    "night_vpd_bias_kpa": _grid("0", "0.25", "0.01"),
    "outdoor_staleness_max_s": _grid("120", "1800", "30"),
    "sw_cool_all_fans_at_high_enabled": _SWITCH,
    "sw_direct_wet_gate_enabled": _SWITCH,
    "sw_direct_wet_stress_override_enabled": _SWITCH,
    "sw_dwell_gate_enabled": _SWITCH,
    "sw_fog_closes_vent": _SWITCH,
    "sw_mister_closes_vent": _SWITCH,
    "sw_summer_vent_enabled": _SWITCH,
    "temp_hysteresis": _grid("0.5", "3", "0.1"),
    "vent_exchange_fraction": _grid("0.1", "0.6", "0.05"),
    "vent_prefer_dp_delta_f": _grid("2", "15", "0.5"),
    "vent_prefer_temp_delta_f": _grid("2", "15", "0.5"),
    "vpd_hysteresis": _grid("0.05", "0.5", "0.05"),
    "vpd_watch_dwell_s": _grid("15", "120", "15"),
}
