"""
entity_map.py — Maps ESPHome object_id → database target

Object IDs are derived from the entity's friendly name by ESPHome's
str_sanitize() (lowercase, non-alphanumeric → '_', collapse repeats).
These were verified against live ESP32 entity enumeration 2026-03-22.

Structure:
  CLIMATE_MAP:        object_id → column name in `climate` table
  ESPHOME_FEEDBACK_MAP: optional feedback object_id → `climate` column
  EQUIPMENT_BINARY_MAP: object_id → equipment name in `equipment_state` (BinarySensor)
  EQUIPMENT_SWITCH_MAP: object_id → equipment name in `equipment_state` (Switch)
  STATE_MAP:          object_id → entity name in `system_state` table (TextSensor)
  SETPOINT_MAP:       object_id → parameter name in `setpoint_changes` (Number)
  DIAGNOSTIC_MAP:     object_id → column name in `diagnostics` table
  DAILY_ACCUM_MAP:    object_id → column name in `daily_summary` table
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from verdify_schemas.tunable_registry import (  # noqa: E402
    CFG_READBACK_ALIASES_REG,
    CFG_READBACK_MAP_REG,
    SETPOINT_MAP_REG,
)

# ──────────────────────────────────────────────────────────────
# Climate sensor columns (SensorInfo)
# ──────────────────────────────────────────────────────────────
CLIMATE_MAP: dict[str, str] = {
    # Temperature (°F)
    "avg_temp___f_": "temp_avg",
    "north_temp___f_": "temp_north",
    "south_temp___f_": "temp_south",
    "east_temp___f_": "temp_east",
    "west_temp___f_": "temp_west",
    "case_temp___f_": "temp_case",
    "control_tin___f_": "temp_control",
    "exterior_intake_temp___f_": "temp_intake",
    # Relative Humidity (%)
    "avg_rh____": "rh_avg",
    "north_rh____": "rh_north",
    "south_rh____": "rh_south",
    "east_rh____": "rh_east",
    "west_rh____": "rh_west",
    "case_rh____": "rh_case",
    # VPD (kPa)
    "avg_vpd__kpa_": "vpd_avg",
    "north_vpd__kpa_": "vpd_north",
    "south_vpd__kpa_": "vpd_south",
    "east_vpd__kpa_": "vpd_east",
    "west_vpd__kpa_": "vpd_west",
    "control_vpd__kpa_": "vpd_control",
    # Derived climate
    "dew_point___f_": "dew_point",
    "abs_humidity__g_m__": "abs_humidity",
    "enthalpy____kj_kg_": "enthalpy_delta",
    # Air quality
    "co___ppm_": "co2_ppm",
    # Light
    "light__lx_": "lux",
    "dli__mol_m___day___": "dli_today",
    # Water
    "water_flow__gpm_": "flow_gpm",
    "water_used__gal_": "water_total_gal",
    # Mister
    "mister_water_today": "mister_water_today",
    # Soil sensors (DFRobot SEN0600/SEN0601, Modbus RTU)
    # South 1 (addr 7, SEN0601): moisture + temp + EC
    # South 2 (addr 8, SEN0600): moisture + temp
    # West (addr 9, SEN0600): moisture + temp
    # object_ids verified via esphome.helpers.sanitize()
    "south_1_soil_moisture____": "soil_moisture_south_1",
    "south_1_soil_temp___f_": "soil_temp_south_1",
    "south_1_soil_ec___s_cm_": "soil_ec_south_1",
    "south_2_soil_moisture____": "soil_moisture_south_2",
    "south_2_soil_temp___f_": "soil_temp_south_2",
    "west_soil_moisture____": "soil_moisture_west",
    "west_soil_temp___f_": "soil_temp_west",
    # Exterior intake (additional sensors beyond temp)
    "exterior_intake_rh____": "intake_rh",
    "exterior_intake_vpd__kpa_": "intake_vpd",
    "outdoor_illuminance": "outdoor_illuminance",
    # Tempest weather (UDP direct from ESP32, replaces HA tempest_sync)
    "tempest_temperature": "outdoor_temp_f",
    "tempest_humidity": "outdoor_rh_pct",
    "tempest_wind_speed": "wind_speed_mph",
    "tempest_wind_direction": "wind_direction_deg",
    "tempest_wind_gust": "wind_gust_mph",
    "tempest_wind_lull": "wind_lull_mph",
    "tempest_pressure": "pressure_hpa",
    "tempest_solar_irradiance": "solar_irradiance_w_m2",
    "tempest_uv_index": "uv_index",
    "tempest_illuminance": "outdoor_lux",
    "tempest_precipitation_rate": "precip_in",  # NOTE: arrives as mm/min, DB expects in
    "tempest_lightning_count": "lightning_count",
    "tempest_lightning_distance": "lightning_avg_dist_mi",  # NOTE: arrives as km, DB expects mi
    # Firmware-v2 (#327) on-chip telemetry evidence surface (numeric). The chip
    # publishes these as template sensors (hardware.yaml ids gh_solar_*,
    # gh_house_*, gh_zone_vpd_*); keys here are the ESPHome object_ids derived
    # from the friendly NAME via sanitize (lowercase, non-[a-z0-9]→'_', no
    # collapse) — e.g. id gh_solar_sunrise_min / name "Solar Sunrise (min local)"
    # → object_id "solar_sunrise__min_local_". Columns added in migration 166.
    "solar_phase": "solar_phase",
    "solar_sunrise__min_local_": "solar_sunrise_min",
    "solar_noon__min_local_": "solar_noon_min",
    "solar_sunset__min_local_": "solar_sunset_min",
    "house_temp_target_f": "house_temp_target_f",
    "house_temp_delta_f": "house_temp_delta_f",
    "house_vpd_target_kpa": "house_vpd_target",
    "house_vpd_delta_kpa": "house_vpd_delta",
    "zone_vpd_target_center": "vpd_target_center",
    "zone_vpd_target_south": "vpd_target_south",
    "zone_vpd_target_west": "vpd_target_west",
    "zone_vpd_target_east": "vpd_target_east",
    "zone_vpd_delta_center": "vpd_delta_center",
    "zone_vpd_delta_south": "vpd_delta_south",
    "zone_vpd_delta_west": "vpd_delta_west",
    "zone_vpd_delta_east": "vpd_delta_east",
}

# Optional ESPHome feedback aliases for center root-zone/runoff instrumentation.
# These are intentionally outside CLIMATE_MAP until the current firmware emits
# them; the firmware drift guard treats CLIMATE_MAP as current ESP32 reality.
ESPHOME_FEEDBACK_MAP: dict[str, str] = {
    "center_soil_moisture____": "moisture_center",
    "center_root_zone_moisture____": "moisture_center",
    "center_root_zone_soil_moisture____": "moisture_center",
    "center_rootzone_moisture____": "moisture_center",
    "center_moisture____": "moisture_center",
    "center_vwc": "moisture_center",
    "center_substrate_vwc": "moisture_center",
    "center_substrate_moisture": "moisture_center",
    "center_substrate_moisture____": "moisture_center",
    "center_root_zone_vwc": "moisture_center",
    "middle_substrate_vwc": "moisture_center",
    "middle_substrate_moisture": "moisture_center",
    "middle_substrate_moisture____": "moisture_center",
    "center_runoff_ph": "ph_runoff_center",
    "center_runoff_p_h": "ph_runoff_center",
    "center_run_off_ph": "ph_runoff_center",
    "center_run_off_p_h": "ph_runoff_center",
    "center_drain_ph": "ph_runoff_center",
    "center_drain_p_h": "ph_runoff_center",
    "center_drainage_ph": "ph_runoff_center",
    "center_leachate_ph": "ph_runoff_center",
    "center_effluent_ph": "ph_runoff_center",
    "center_tray_ph": "ph_runoff_center",
    "center_runoff_ec": "ec_runoff_center",
    "center_runoff_ec_ms_cm": "ec_runoff_center",
    "center_runoff_ec_us_cm": "ec_runoff_center",
    "center_runoff_ec_u_s_cm": "ec_runoff_center",
    "center_runoff_ec___s_cm_": "ec_runoff_center",
    "center_runoff_ec____s___cm_": "ec_runoff_center",
    "center_run_off_ec": "ec_runoff_center",
    "center_run_off_ec_ms_cm": "ec_runoff_center",
    "center_run_off_ec_us_cm": "ec_runoff_center",
    "center_run_off_ec_u_s_cm": "ec_runoff_center",
    "center_run_off_ec___s_cm_": "ec_runoff_center",
    "center_run_off_ec____s___cm_": "ec_runoff_center",
    "center_runoff_conductivity": "ec_runoff_center",
    "center_runoff_electrical_conductivity": "ec_runoff_center",
    "center_drain_ec": "ec_runoff_center",
    "center_drain_ec_ms_cm": "ec_runoff_center",
    "center_drain_ec_us_cm": "ec_runoff_center",
    "center_drain_ec_u_s_cm": "ec_runoff_center",
    "center_drain_ec___s_cm_": "ec_runoff_center",
    "center_drain_ec____s___cm_": "ec_runoff_center",
    "center_drainage_ec": "ec_runoff_center",
    "center_leachate_ec": "ec_runoff_center",
    "center_effluent_ec": "ec_runoff_center",
    "center_tray_ec": "ec_runoff_center",
}

# Optional physical irrigation feedback sensors published directly to MQTT.
# Retained broker state is ignored by the ingestor; only live messages should
# populate climate. Legacy south topics are listed only in
# MQTT_FEEDBACK_CANDIDATES for diagnostics and are intentionally not accepted
# by MQTT_FEEDBACK_MAP.
MQTT_FEEDBACK_MAP: dict[str, str] = {
    "greenhouse/sensor/south_1_soil_moisture____/state": "soil_moisture_south_1",
    "greenhouse/sensor/south_1_soil_ec____s___cm_/state": "soil_ec_south_1",
    "greenhouse/sensor/south_1_soil_temp____f_/state": "soil_temp_south_1",
    "greenhouse/sensor/center_soil_moisture____/state": "moisture_center",
    "greenhouse/sensor/center_root_zone_moisture____/state": "moisture_center",
    "greenhouse/sensor/center_root_zone_soil_moisture____/state": "moisture_center",
    "greenhouse/sensor/center_rootzone_moisture____/state": "moisture_center",
    "greenhouse/sensor/center_moisture____/state": "moisture_center",
    "greenhouse/sensor/center_vwc/state": "moisture_center",
    "greenhouse/sensor/center_substrate_vwc/state": "moisture_center",
    "greenhouse/sensor/center_substrate_moisture/state": "moisture_center",
    "greenhouse/sensor/center_substrate_moisture____/state": "moisture_center",
    "greenhouse/sensor/center_root_zone_vwc/state": "moisture_center",
    "greenhouse/sensor/middle_substrate_vwc/state": "moisture_center",
    "greenhouse/sensor/middle_substrate_moisture/state": "moisture_center",
    "greenhouse/sensor/middle_substrate_moisture____/state": "moisture_center",
    "greenhouse/sensor/center_runoff_ph/state": "ph_runoff_center",
    "greenhouse/sensor/center_runoff_p_h/state": "ph_runoff_center",
    "greenhouse/sensor/center_run_off_ph/state": "ph_runoff_center",
    "greenhouse/sensor/center_run_off_p_h/state": "ph_runoff_center",
    "greenhouse/sensor/center_drain_ph/state": "ph_runoff_center",
    "greenhouse/sensor/center_drain_p_h/state": "ph_runoff_center",
    "greenhouse/sensor/center_drainage_ph/state": "ph_runoff_center",
    "greenhouse/sensor/center_leachate_ph/state": "ph_runoff_center",
    "greenhouse/sensor/center_effluent_ph/state": "ph_runoff_center",
    "greenhouse/sensor/center_tray_ph/state": "ph_runoff_center",
    "greenhouse/sensor/center_runoff_ec/state": "ec_runoff_center",
    "greenhouse/sensor/center_runoff_ec_ms_cm/state": "ec_runoff_center",
    "greenhouse/sensor/center_runoff_ec_us_cm/state": "ec_runoff_center",
    "greenhouse/sensor/center_runoff_ec_u_s_cm/state": "ec_runoff_center",
    "greenhouse/sensor/center_runoff_ec___s_cm_/state": "ec_runoff_center",
    "greenhouse/sensor/center_runoff_ec____s___cm_/state": "ec_runoff_center",
    "greenhouse/sensor/center_run_off_ec/state": "ec_runoff_center",
    "greenhouse/sensor/center_run_off_ec_ms_cm/state": "ec_runoff_center",
    "greenhouse/sensor/center_run_off_ec_us_cm/state": "ec_runoff_center",
    "greenhouse/sensor/center_run_off_ec_u_s_cm/state": "ec_runoff_center",
    "greenhouse/sensor/center_run_off_ec___s_cm_/state": "ec_runoff_center",
    "greenhouse/sensor/center_run_off_ec____s___cm_/state": "ec_runoff_center",
    "greenhouse/sensor/center_runoff_conductivity/state": "ec_runoff_center",
    "greenhouse/sensor/center_runoff_electrical_conductivity/state": "ec_runoff_center",
    "greenhouse/sensor/center_drain_ec/state": "ec_runoff_center",
    "greenhouse/sensor/center_drain_ec_ms_cm/state": "ec_runoff_center",
    "greenhouse/sensor/center_drain_ec_us_cm/state": "ec_runoff_center",
    "greenhouse/sensor/center_drain_ec_u_s_cm/state": "ec_runoff_center",
    "greenhouse/sensor/center_drain_ec___s_cm_/state": "ec_runoff_center",
    "greenhouse/sensor/center_drain_ec____s___cm_/state": "ec_runoff_center",
    "greenhouse/sensor/center_drainage_ec/state": "ec_runoff_center",
    "greenhouse/sensor/center_leachate_ec/state": "ec_runoff_center",
    "greenhouse/sensor/center_effluent_ec/state": "ec_runoff_center",
    "greenhouse/sensor/center_tray_ec/state": "ec_runoff_center",
}

MQTT_FEEDBACK_CANDIDATES: dict[str, tuple[str, ...]] = {
    "south_soil_probe_1": (
        "greenhouse/sensor/south_1_soil_moisture____/state",
        "greenhouse/sensor/south_1_soil_ec____s___cm_/state",
        "greenhouse/sensor/south_1_soil_temp____f_/state",
        "greenhouse/sensor/south_soil_moisture____/state",
        "greenhouse/sensor/south_soil_ec____s___cm_/state",
        "greenhouse/sensor/south_soil_temp____f_/state",
    ),
    "center_root_zone_moisture": (
        "greenhouse/sensor/center_soil_moisture____/state",
        "greenhouse/sensor/center_root_zone_moisture____/state",
        "greenhouse/sensor/center_root_zone_soil_moisture____/state",
        "greenhouse/sensor/center_rootzone_moisture____/state",
        "greenhouse/sensor/center_moisture____/state",
        "greenhouse/sensor/center_vwc/state",
        "greenhouse/sensor/center_substrate_vwc/state",
        "greenhouse/sensor/center_substrate_moisture/state",
        "greenhouse/sensor/center_substrate_moisture____/state",
        "greenhouse/sensor/center_root_zone_vwc/state",
        "greenhouse/sensor/middle_substrate_vwc/state",
        "greenhouse/sensor/middle_substrate_moisture/state",
        "greenhouse/sensor/middle_substrate_moisture____/state",
    ),
    "center_runoff_ph": (
        "greenhouse/sensor/center_runoff_ph/state",
        "greenhouse/sensor/center_runoff_p_h/state",
        "greenhouse/sensor/center_run_off_ph/state",
        "greenhouse/sensor/center_run_off_p_h/state",
        "greenhouse/sensor/center_drain_ph/state",
        "greenhouse/sensor/center_drain_p_h/state",
        "greenhouse/sensor/center_drainage_ph/state",
        "greenhouse/sensor/center_leachate_ph/state",
        "greenhouse/sensor/center_effluent_ph/state",
        "greenhouse/sensor/center_tray_ph/state",
    ),
    "center_runoff_ec": (
        "greenhouse/sensor/center_runoff_ec/state",
        "greenhouse/sensor/center_runoff_ec_ms_cm/state",
        "greenhouse/sensor/center_runoff_ec_us_cm/state",
        "greenhouse/sensor/center_runoff_ec_u_s_cm/state",
        "greenhouse/sensor/center_runoff_ec___s_cm_/state",
        "greenhouse/sensor/center_runoff_ec____s___cm_/state",
        "greenhouse/sensor/center_run_off_ec/state",
        "greenhouse/sensor/center_run_off_ec_ms_cm/state",
        "greenhouse/sensor/center_run_off_ec_us_cm/state",
        "greenhouse/sensor/center_run_off_ec_u_s_cm/state",
        "greenhouse/sensor/center_run_off_ec___s_cm_/state",
        "greenhouse/sensor/center_run_off_ec____s___cm_/state",
        "greenhouse/sensor/center_runoff_conductivity/state",
        "greenhouse/sensor/center_runoff_electrical_conductivity/state",
        "greenhouse/sensor/center_drain_ec/state",
        "greenhouse/sensor/center_drain_ec_ms_cm/state",
        "greenhouse/sensor/center_drain_ec_us_cm/state",
        "greenhouse/sensor/center_drain_ec_u_s_cm/state",
        "greenhouse/sensor/center_drain_ec___s_cm_/state",
        "greenhouse/sensor/center_drain_ec____s___cm_/state",
        "greenhouse/sensor/center_drainage_ec/state",
        "greenhouse/sensor/center_leachate_ec/state",
        "greenhouse/sensor/center_effluent_ec/state",
        "greenhouse/sensor/center_tray_ec/state",
    ),
}

FEEDBACK_VALUE_RANGES: dict[str, tuple[float | None, float | None]] = {
    "soil_moisture_south_1": (0.0, 100.0),
    "soil_ec_south_1": (0.0, None),
    "soil_temp_south_1": (-40.0, 180.0),
    "moisture_center": (0.0, 100.0),
    "ph_runoff_center": (0.0, 14.0),
    "ec_runoff_center": (0.0, None),
}


def normalize_feedback_value(column: str, value: object) -> float | None:
    """Return a finite in-range feedback value, or None when it must be ignored."""
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    limits = FEEDBACK_VALUE_RANGES.get(column)
    if not limits:
        return numeric
    low, high = limits
    if low is not None and numeric < low:
        return None
    if high is not None and numeric > high:
        return None
    return numeric


# ──────────────────────────────────────────────────────────────
# Equipment relay states — BinarySensor entities
# ──────────────────────────────────────────────────────────────
EQUIPMENT_BINARY_MAP: dict[str, str] = {
    "fan_1_running": "fan1",
    "fan_2_running": "fan2",
    "heat_1_running": "heat1",
    "heat_2_running": "heat2",
    "fog_running": "fog",
    "vent_open": "vent",
    "mister_running": "mister_any",
    "mister_budget_exceeded": "mister_budget_exceeded",
    "economiser_blocked": "economiser_blocked",
    "leak_detected": "leak_detected",  # SAFETY CRITICAL
    "water_flowing": "water_flowing",
    "heap_pressure_warning": "heap_pressure_warning",
    "heap_pressure_critical": "heap_pressure_critical",
    "fan_burst_active": "fan_burst_active",
    "fog_burst_active": "fog_burst_active",
    "vent_bypass_active": "vent_bypass_active",
    "occupancy_quiet_override_active": "occupancy_quiet_override_active",
}

# ──────────────────────────────────────────────────────────────
# Equipment relay states — Switch entities
# ──────────────────────────────────────────────────────────────
EQUIPMENT_SWITCH_MAP: dict[str, str] = {
    "greenhouse_occupied": "occupancy",
    "mister___south_wall": "mister_south",
    "mister___south_wall__fert__": "mister_south_fert",
    "mister___west_wall": "mister_west",
    "mister___west_wall__fert__": "mister_west_fert",
    "mister___center": "mister_center",
    "drip___wall": "drip_wall",
    "drip___wall__fert__": "drip_wall_fert",
    "drip___center": "drip_center",
    "drip___center__fert__": "drip_center_fert",
    "valve___fert__master": "fert_master_valve",
    "grow_light_main": "grow_light_main",
    "grow_light_grow": "grow_light_grow",
    # Config switches (boolean tunables exposed as HA switches)
    "economiser_enabled": "economiser_enabled",
    "fog_closes_vent": "fog_closes_vent",
    "irrigation_enabled": "irrigation_enabled",
    "irrigation_wall_enabled": "irrigation_wall_enabled",
    "irrigation_center_enabled": "irrigation_center_enabled",
    "irrigation_weather_skip": "irrigation_weather_skip",
    "gl_auto_mode": "gl_auto_mode",
    "occupancy_mist_inhibit": "occupancy_inhibit",
}

# Combined for legacy callers — not used by ingestor directly
EQUIPMENT_MAP: dict[str, str] = {**EQUIPMENT_BINARY_MAP, **EQUIPMENT_SWITCH_MAP}

# ──────────────────────────────────────────────────────────────
# State machine text entities (TextSensorInfo)
# ──────────────────────────────────────────────────────────────
STATE_MAP: dict[str, str] = {
    "greenhouse_state": "greenhouse_state",
    "lead_fan": "lead_fan",
    "last_state_transition": "last_transition",
    "mister_state": "mister_state",
    "mister_selected_zone": "mister_zone",
    # OBS-1e (Sprint 16): firmware silent-override audit.
    # Value is a comma-separated list of active override flags from
    # evaluate_overrides() (e.g. "occupancy_blocks_equipment,fog_gate_rh"
    # or "none"). Ingestor also diffs transitions and writes one row per
    # start event to the override_events table.
    "active_overrides": "overrides_active",
    # Sprint-15.1 fix 8: diagnostic trace — which branch of
    # determine_mode() chose the current mode. Lets post-hoc queries
    # distinguish `summer_vent_preempt` from `temp_vent` from
    # `seal_enter` etc. Populated by controls.yaml from
    # ctl_state.last_mode_reason on every cycle.
    "mode_reason": "mode_reason",
    # AI-control diagnostics: whether requested moisture assistance had an
    # actuator route available, and which gate blocked it when it did not.
    "moisture_block_reason": "moisture_block_reason",
    "vent_mist_assist_status": "vent_mist_assist_status",
    "direct_wet_zone_mask": "direct_wet_zone_mask",
    "fog_block_reason": "fog_block_reason",
    "climate_action": "climate_action",
    "climate_priority_axis": "climate_priority_axis",
    "climate_candidate_summary": "climate_candidate_summary",
    "climate_moisture_assist_state": "climate_moisture_assist_state",
    "climate_moisture_zone": "climate_moisture_zone",
    "climate_temp_error_f": "climate_temp_error_f",
    "climate_vpd_error_kpa": "climate_vpd_error_kpa",
    "climate_fog_margin_kpa": "climate_fog_margin_kpa",
    "climate_fog_block_reason": "climate_fog_block_reason",
    "climate_resource_cost_estimate": "climate_resource_cost_estimate",
    "climate_next_mist_eligible_s": "climate_next_mist_eligible_s",
    # JSON estimator payload; contract = verdify_schemas.MoistureExchangeTelemetry
    # (#327/#385/#410) — stored parsed+normalized under
    # climate_action_log.source_system_state, promoted to typed columns by
    # migration 187's v_moisture_estimator_telemetry view.
    "climate_moisture_exchange": "climate_moisture_exchange",
    "gl_main_state": "gl_main_state",
    "gl_main_reason": "gl_main_reason",
    "gl_main_decision_epoch": "gl_main_decision_epoch",  # TextSensorInfo exact epoch string
    "gl_grow_state": "gl_grow_state",
    "gl_grow_reason": "gl_grow_reason",
    "gl_grow_decision_epoch": "gl_grow_decision_epoch",  # TextSensorInfo exact epoch string
}

# ──────────────────────────────────────────────────────────────
# Setpoints (NumberInfo — write on change)
# ──────────────────────────────────────────────────────────────
# Derived from the registry. firmware-v2 (firmware/v2-solar-bands) dropped the
# legacy fog-stress-window / direct-wet-stress-latest-hour NUMBER entities from
# firmware, but their registry rows persist (the planner still pushes them as
# dry-stress / fog-stress policy). Those become dead firmware routes here;
# they're allow-listed in verdify_schemas/tests/test_firmware_drift.py until the
# policy is retired from the registry/planner.
SETPOINT_MAP: dict[str, str] = dict(SETPOINT_MAP_REG)

# ──────────────────────────────────────────────────────────────
# Diagnostics (SensorInfo + TextSensorInfo)
# ──────────────────────────────────────────────────────────────
DIAGNOSTIC_MAP: dict[str, str] = {
    "wifi_signal": "wifi_rssi",  # SensorInfo, dBm
    "free_heap": "heap_bytes",  # SensorInfo
    "minimum_free_heap": "heap_min_free_kb",  # SensorInfo, kB
    "largest_free_heap_block": "heap_largest_free_block_kb",  # SensorInfo, kB
    "uptime": "uptime_s",  # SensorInfo
    "probe_health": "probe_health",  # TextSensorInfo
    "reset_reason": "reset_reason",  # TextSensorInfo
    "firmware_version": "firmware_version",  # TextSensorInfo
    # FW-10 (Sprint 17): how many zone probes (north/south/east/west) are
    # currently contributing to the avg_temp/rh/vpd aggregates. Stale probes
    # (>5 min since last reading) are excluded. Planner distrusts aggregates
    # when count < 4.
    "active_probe_count": "active_probe_count",  # SensorInfo, 0-4
    # OBS-3 (Sprint 18): firmware ControlState breaker counters, exposed so
    # the planner can see how close firmware is to forcing a VENTILATE
    # latch (relief_cycle_count) and how long that latch has been active
    # (vent_latch_timer_s). See migration 082.
    "relief_cycle_count": "relief_cycle_count",  # SensorInfo, 0-max_relief_cycles
    "vent_latch_timer_s": "vent_latch_timer_s",  # SensorInfo, 0-1800 s
    # Band-first controller diagnostics (migration 094): expose the timers that drive
    # SEALED_MIST entry/backoff plus the hot/dry vent moisture-assist flag.
    "sealed_timer_s": "sealed_timer_s",
    "vpd_watch_timer_s": "vpd_watch_timer_s",
    "mist_backoff_timer_s": "mist_backoff_timer_s",
    "vent_mist_assist_active": "vent_mist_assist_active",
    "effective_heat_target___f_": "effective_heat_target_f",
    "effective_cool_stage_2_delta___f_": "effective_cool_stage2_delta_f",
    "effective_vpd_hysteresis__kpa_": "effective_vpd_hysteresis_kpa",
    "effective_dehum_aggressive__kpa_": "effective_dehum_aggressive_kpa",
    "controller_time_epoch": "controller_time_epoch",  # TextSensorInfo exact epoch string
    "controller_local_hour": "controller_local_hour",
    "sntp_valid": "sntp_valid",
    "sntp_miss_count": "sntp_miss_count",
    "last_sntp_sync_age_s": "last_sntp_sync_age_s",
    # Firmware-v2 (#327) on-chip text evidence surface. Chip ids
    # gh_zone_wet_granted / gh_band_source; keys are the sanitized friendly
    # NAMEs ("Zone Wet Granted" → "zone_wet_granted", "Band Source" →
    # "band_source"). Columns added in migration 166.
    "zone_wet_granted": "zone_wet_granted",  # TextSensorInfo
    "band_source": "band_source",  # TextSensorInfo
}

# ──────────────────────────────────────────────────────────────
# Daily accumulator sensors (SensorInfo, snapshot at midnight)
# ──────────────────────────────────────────────────────────────
DAILY_ACCUM_MAP: dict[str, str] = {
    # Cycle counts
    "cycles___fan_1__today_": "cycles_fan1",
    "cycles___fan_2__today_": "cycles_fan2",
    "cycles___heat_1__today_": "cycles_heat1",
    "cycles___heat_2__today_": "cycles_heat2",
    "cycles___fog_fan__today_": "cycles_fog",
    "cycles___vent__today_": "cycles_vent",
    "de_hum_cycles__today_": "cycles_dehum",
    "safety_de_hum_cycles__today_": "cycles_safety_dehum",
    "cycles___mister_south__today_": "cycles_mister_south",
    "cycles___mister_west__today_": "cycles_mister_west",
    "cycles___mister_center__today_": "cycles_mister_center",
    "cycles___drip_wall__today_": "cycles_drip_wall",
    "cycles___drip_center__today_": "cycles_drip_center",
    # Runtime (relay minutes)
    "runtime___fan_1__min_today_": "runtime_fan1_min",
    "runtime___fan_2__min_today_": "runtime_fan2_min",
    "runtime___heat_1__min_today_": "runtime_heat1_min",
    "runtime___heat_2__min_today_": "runtime_heat2_min",
    "runtime___fog__min_today_": "runtime_fog_min",
    "runtime___vent__min_today_": "runtime_vent_min",
    # Runtime (mister hours)
    "mister_south_wall_runtime__today_": "runtime_mister_south_h",
    "mister_west_wall_runtime__today_": "runtime_mister_west_h",
    "mister_center_runtime__today_": "runtime_mister_center_h",
    # Drip runtimes
    "wall_drips_runtime__today_": "runtime_drip_wall_h",
    "center_drips_runtime__today_": "runtime_drip_center_h",
    # Firmware sprint-2 fairness watchdog counter (resets at midnight)
    "mister_fairness_overrides__today_": "mister_fairness_overrides_today",
}

# ──────────────────────────────────────────────────────────────
# ESP32 Configured Value Readback (cfg_* diagnostic sensors)
# Maps ESP32 object_id → canonical DB parameter name
# Written to setpoint_snapshot table for ground-truth tracking
# ──────────────────────────────────────────────────────────────
# firmware-v2 (firmware/v2-solar-bands) added these cfg_* readback sensors
# (firmware/greenhouse/sensors.yaml). The underlying number/switch entities live
# only in firmware (no registry row), so route the readbacks here directly:
# ESPHome sensor `id:` → canonical setpoint param name. The firmware-v2-dropped
# fog-window / micropulse / dawn-rehydrate / midday-drench / overnight-micropulse
# / night-humidity-source cfg_* readbacks remain in the registry-derived base
# (their registry rows persist) but are now dead firmware routes — allow-listed
# in verdify_schemas/tests/test_firmware_drift.py.
_CFG_READBACK_ADDED_FW_V2 = {
    "cfg_night_stress_min_dew_margin_f": "night_stress_min_dew_margin_f",
    "cfg_sw_onchip_band_enabled": "sw_onchip_band_enabled",
    "cfg_sw_wet_taper_enabled": "sw_wet_taper_enabled",
    "cfg_sw_night_stress_wet_enabled": "sw_night_stress_wet_enabled",
    # F9 (issue 5): night econ-heat suppression switch — was enforced firmware-side
    # with no readback (fire-and-forget). sensors.yaml now publishes the cfg_*; map
    # it so the ingestor can confirm the device holds the operator's value.
    "cfg_sw_night_econ_heat_suppress_enabled": "sw_night_econ_heat_suppress_enabled",
    # Item-3 arbiter switch readback (cfg_arbiter_zone_enabled →
    # sw_arbiter_zone_enabled) now flows from the registry's cfg_readback_object_id
    # (CFG_READBACK_MAP_REG); the manual alias here was redundant and is removed.
}
CFG_READBACK_MAP: dict[str, str] = {
    **CFG_READBACK_MAP_REG,
    **CFG_READBACK_ALIASES_REG,
    **_CFG_READBACK_ADDED_FW_V2,
}

# ──────────────────────────────────────────────────────────────
# Inverse maps: DB parameter name → ESP32 object_id
# Auto-generated from SETPOINT_MAP. Used by dispatcher for push.
# ──────────────────────────────────────────────────────────────
PARAM_TO_ENTITY = {v: k for k, v in SETPOINT_MAP.items() if not v.startswith("sw_")}
SWITCH_TO_ENTITY = {v: k for k, v in SETPOINT_MAP.items() if v.startswith("sw_")}
