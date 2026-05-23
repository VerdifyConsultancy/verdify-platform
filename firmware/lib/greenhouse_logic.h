#pragma once
/*
 * greenhouse_logic.h — Greenhouse Climate Controller Logic
 * =========================================================
 *
 * SINGLE SOURCE OF TRUTH. Same compiled code on ESP32 and x86.
 * ZERO ESPHome dependencies.
 *
 * determine_mode() mutates ControlState (timers, mode_prev, mist_stage).
 * resolve_equipment() is pure (reads only).
 *
 * HARDWARE DEPENDENCIES (enforced by caller, not here):
 *   - Relay min on/off times (ESPHome set_relay with min_on_ms/min_off_ms)
 *   - Gas heater min off time (MIN_HEAT_OFF_MS, typically 300s)
 *   - Vent actuator travel time (ESPHome min_vent_on_s/min_vent_off_s)
 *
 * SENSOR_FAULT: ALL relays off. Freeze protection must be handled by a
 * hardware thermostat wired in parallel, not blind software logic.
 * SENSOR_FAULT does NOT overwrite mode_prev — preserves hysteresis
 * context for graceful recovery from transient I2C glitches.
 *
 * OWNERSHIP: This code owns ControlState.mist_stage. ESPHome/controls.yaml
 * reads mist_stage to drive physical mister relays but does not write it.
 *
 * CONCURRENCY: This code is single-threaded. ControlState must not be
 * accessed from ISRs or other tasks without synchronization.
 */

#include "greenhouse_types.h"

// ── R2-4: Plausibility validation — catches NaN, inf, and garbage ──
inline bool sensors_plausible(const SensorInputs& in) noexcept {
    return std::isfinite(in.temp_f)  && in.temp_f  > -20.0f && in.temp_f  < 140.0f
        && std::isfinite(in.rh_pct)  && in.rh_pct  >= 0.0f  && in.rh_pct  <= 100.0f
        && std::isfinite(in.vpd_kpa) && in.vpd_kpa >= 0.0f  && in.vpd_kpa < 10.0f
        && in.local_hour >= 0        && in.local_hour <= 23;
}

// ── Occupancy quiet guards ──
// When occupied, do not seal for misting, fire fog, or run routine fans.
// Note: the caller (controls.yaml) already computes sp.occupancy_inhibit as
// (enabled && occupied). Anding `in.occupied` again here is intentional: it
// keeps the header standalone-correct for tests that set sp.occupancy_inhibit
// directly without a paired occupied flag.
inline bool moisture_blocked_by_occupancy(const SensorInputs& in, const Setpoints& sp) noexcept {
    return sp.occupancy_inhibit && in.occupied;
}

inline bool air_blocked_by_occupancy(const SensorInputs& in, const Setpoints& sp, Mode mode) noexcept {
    return sp.occupancy_inhibit
        && in.occupied
        && mode != SAFETY_COOL
        && mode != SAFETY_HEAT
        && mode != SENSOR_FAULT;
}

// ── Fog gating helpers (sprint-8) ──────────────────────────────────
// Consolidates the RH ceiling / min temp / hour-of-day predicates that
// previously lived in 5 places (MIST_S2→MIST_FOG entry, evaluate_overrides,
// SAFETY_COOL/SEALED_MIST/VENTILATE-FW-9b in resolve_equipment). Occupancy
// inhibit is a separate concern so evaluate_overrides can report fog_gate_*
// independently from the unified occupancy_blocks_equipment flag.

// Midnight-wrap-aware window check. start <= end → [start, end). Otherwise
// the window crosses midnight (e.g. start=22, end=6 → 22:00-05:59 local).
// Before sprint-8 this was two hardcoded comparisons that silently gated
// fog 24/7 whenever a planner setpoint produced start > end.
inline bool fog_hour_in_window(int hour, int start, int end) noexcept {
    return (start <= end) ? (hour >= start && hour < end)
                          : (hour >= start || hour < end);
}

inline int local_minute_of_day(int hour, int minute) noexcept {
    hour = std::max(0, std::min(23, hour));
    minute = std::max(0, std::min(59, minute));
    return hour * 60 + minute;
}

inline int clamp_day_minutes(int minutes) noexcept {
    return std::max(0, std::min(1440, minutes));
}

inline bool minute_in_window(int now_minute, int start_minute, int duration_minutes) noexcept {
    now_minute = ((now_minute % 1440) + 1440) % 1440;
    start_minute = ((start_minute % 1440) + 1440) % 1440;
    duration_minutes = clamp_day_minutes(duration_minutes);
    if (duration_minutes <= 0) return false;
    if (duration_minutes >= 1440) return true;
    const int end_minute = (start_minute + duration_minutes) % 1440;
    return (start_minute < end_minute)
        ? (now_minute >= start_minute && now_minute < end_minute)
        : (now_minute >= start_minute || now_minute < end_minute);
}

inline bool direct_wet_window_open(
    int now_minute,
    int activity_start_hour,
    int activity_start_minute,
    int activity_duration_min,
    int wet_start_offset_min,
    int drydown_before_off_min
) noexcept {
    const int activity_start = local_minute_of_day(activity_start_hour, activity_start_minute);
    activity_duration_min = clamp_day_minutes(activity_duration_min);
    wet_start_offset_min = std::max(0, wet_start_offset_min);
    drydown_before_off_min = std::max(0, drydown_before_off_min);
    const int wet_duration = activity_duration_min - wet_start_offset_min - drydown_before_off_min;
    if (wet_duration <= 0) return false;
    const int wet_start = (activity_start + wet_start_offset_min) % 1440;
    return minute_in_window(now_minute, wet_start, wet_duration);
}

inline bool day_mask_allows(int day_mask, int day_of_week_zero_sunday) noexcept {
    if (day_mask <= 0) return false;
    day_of_week_zero_sunday = ((day_of_week_zero_sunday % 7) + 7) % 7;
    return (day_mask & (1 << day_of_week_zero_sunday)) != 0;
}

// True iff all of RH, temp, and hour-of-day permit fogging. Occupancy is
// NOT checked here — see moisture_blocked_by_occupancy().
inline bool fog_permitted(const SensorInputs& in, const Setpoints& sp) noexcept {
    return (in.rh_pct  <= sp.fog_rh_ceiling)
        && (in.temp_f  >= sp.fog_min_temp)
        && fog_hour_in_window(in.local_hour, sp.fog_window_start, sp.fog_window_end);
}

// Unified band-first controller clamps VPD hysteresis against the actual band width. The
// legacy cascade allows hyst_vpd_kpa=0.4 with a 0.8-1.2 band, which makes
// SEALED_MIST exit only below 0.7 kPa. That turns normal high-VPD periods
// into timeout/backoff loops instead of band compliance.
inline float band_vpd_hysteresis(const Setpoints& sp) noexcept {
    const float vpd_width = std::max(0.2f, sp.vpd_high - sp.vpd_low);
    const float requested = std::max(0.05f, sp.vpd_hysteresis);
    const float cap = std::max(0.05f, vpd_width * 0.33f);
    return std::min(requested, cap);
}

// Unified band-first controller uses the crop/planner band itself as the temperature contract.
// Heat1 protects the lower quartile; heat2 protects the lower edge. The older
// d_heat_stage_2 margin is left to the legacy cascade and should not allow this path
// to sit several degrees below band before gas heat joins.
static constexpr float BAND_HEAT_TARGET_FRACTION = 0.25f;
inline float band_heat_target_f(const Setpoints& sp) noexcept {
    const float band_width = std::max(2.0f, sp.temp_high - sp.temp_low);
    return sp.temp_low + band_width * BAND_HEAT_TARGET_FRACTION;
}

// Unified band-first controller normally cools at the raw upper band edge. Stage-2 fan
// escalation is band-scaled so a 6°F crop band does not wait the full legacy
// d_cool_stage_2 margin before using both fans. The same derived margin is
// used only for cold-outdoor vent entry to avoid repeated cold-air slugs.
inline float band_cool_stage2_delta_f(const Setpoints& sp) noexcept {
    const float band_width = std::max(2.0f, sp.temp_high - sp.temp_low);
    return std::min(sp.dC2, std::max(1.0f, band_width * 0.25f));
}

inline float effective_dehum_aggressive_kpa(const Setpoints& sp) noexcept {
    return std::max(0.05f, std::min(sp.vpd_low - 0.05f, sp.dehum_aggressive_kpa));
}

static constexpr uint32_t FAN_LEAD_RUNTIME_DEADBAND_MS = 600000U;

inline uint32_t relay_runtime_with_active_ms(
    uint32_t recorded_runtime_ms,
    bool relay_on,
    uint32_t on_stamp_ms,
    uint32_t now_ms
) noexcept {
    if (!relay_on || on_stamp_ms == 0) return recorded_runtime_ms;
    const uint32_t active_ms = now_ms >= on_stamp_ms ? now_ms - on_stamp_ms : 0;
    if (UINT32_MAX - recorded_runtime_ms < active_ms) return UINT32_MAX;
    return recorded_runtime_ms + active_ms;
}

inline bool choose_runtime_balanced_fan_lead(
    bool current_lead_is_fan1,
    uint32_t fan1_runtime_ms,
    uint32_t fan2_runtime_ms,
    bool any_fan_running,
    bool wall_clock_rotate_due,
    uint32_t deadband_ms = FAN_LEAD_RUNTIME_DEADBAND_MS
) noexcept {
    if (deadband_ms == 0) deadband_ms = 1;

    if (fan1_runtime_ms > fan2_runtime_ms && fan1_runtime_ms - fan2_runtime_ms > deadband_ms) {
        return false;
    }
    if (fan2_runtime_ms > fan1_runtime_ms && fan2_runtime_ms - fan1_runtime_ms > deadband_ms) {
        return true;
    }
    if (!any_fan_running && wall_clock_rotate_due) {
        return !current_lead_is_fan1;
    }
    return current_lead_is_fan1;
}

inline bool lighting_hour_in_window(int hour, int start, int end) noexcept {
    return (start <= end) ? (hour >= start && hour < end)
                          : (hour >= start || hour < end);
}

inline void validate_lighting_setpoints(LightingSetpoints& sp) noexcept {
    sp.target_light_minutes = std::max(uint32_t(0), std::min(uint32_t(1080), sp.target_light_minutes));
    sp.lux_on_threshold = std::max(100.0f, std::min(100000.0f, sp.lux_on_threshold));
    sp.lux_hysteresis = std::max(0.0f, std::min(25000.0f, sp.lux_hysteresis));
    sp.start_hour = std::max(0, std::min(23, sp.start_hour));
    sp.cutoff_hour = std::max(0, std::min(23, sp.cutoff_hour));
    sp.min_on_ms = std::max(uint32_t(0), std::min(uint32_t(3600000), sp.min_on_ms));
    sp.min_off_ms = std::max(uint32_t(0), std::min(uint32_t(3600000), sp.min_off_ms));
}

inline float lighting_dli_increment(
    float indoor_lux,
    float tempest_lux,
    bool main_light_on,
    bool grow_light_on,
    float dt_s
) noexcept {
    constexpr float LUX_TO_PPFD = 0.0185f;
    constexpr float INDOOR_LDR_CORRECTION = 3.5f;
    constexpr float TEMPEST_TO_PLANT_LUX = 0.16f;
    constexpr float MAIN_LIGHT_DLI_PER_HOUR = 0.3485f;
    constexpr float GROW_LIGHT_DLI_PER_HOUR = 0.4515f;

    const float indoor_equiv = (std::isfinite(indoor_lux) && indoor_lux > 10.0f)
        ? indoor_lux * INDOOR_LDR_CORRECTION
        : 0.0f;
    const float tempest_equiv = (std::isfinite(tempest_lux) && tempest_lux > 10.0f)
        ? tempest_lux * TEMPEST_TO_PLANT_LUX
        : 0.0f;
    const float natural_lux_equiv = std::max(indoor_equiv, tempest_equiv);
    const float natural_dli = natural_lux_equiv * LUX_TO_PPFD * std::max(0.0f, dt_s) / 1000000.0f;
    const float supplemental_dli_per_hour =
        (main_light_on ? MAIN_LIGHT_DLI_PER_HOUR : 0.0f)
        + (grow_light_on ? GROW_LIGHT_DLI_PER_HOUR : 0.0f);
    return natural_dli + supplemental_dli_per_hour * std::max(0.0f, dt_s) / 3600.0f;
}

inline LightingDecision evaluate_lighting(
    const LightingInputs& in,
    LightingSetpoints sp,
    LightingState& state,
    bool current_on,
    uint32_t now_ms,
    float dt_s = 60.0f
) noexcept {
    if (state.sentinel != LIGHT_STATE_SENTINEL) {
        state = initial_lighting_state();
    }
    validate_lighting_setpoints(sp);

    if (state.on != current_on) {
        state.on = current_on;
        state.last_transition_tick_ms = now_ms;
    }

    const float natural_lux = std::isfinite(in.natural_lux) ? std::max(0.0f, in.natural_lux) : 0.0f;
    const float exterior_lux = std::isfinite(in.exterior_lux) ? std::max(0.0f, in.exterior_lux) : 0.0f;
    const bool exterior_lux_available = in.exterior_lux_fresh && std::isfinite(in.exterior_lux);
    const float lux_off_threshold = sp.lux_on_threshold + sp.lux_hysteresis;
    const bool in_window = sp.auto_enabled && lighting_hour_in_window(in.local_hour, sp.start_hour, sp.cutoff_hour);
    const bool crossed_midnight = state.last_count_hour >= 0 && in.local_hour < state.last_count_hour;
    const bool reached_start_hour = in.local_hour == sp.start_hour && state.last_count_hour != sp.start_hour;
    if (crossed_midnight || reached_start_hour) {
        state.qualified_light_s = 0.0f;
        state.natural_qualified_s = 0.0f;
        state.switch_on_s = 0.0f;
        state.overlap_s = 0.0f;
    }

    const bool natural_qualified = natural_lux >= sp.lux_on_threshold;
    const float count_dt_s = std::isfinite(dt_s) ? std::max(0.0f, dt_s) : 0.0f;
    if (in_window && count_dt_s > 0.0f) {
        if (natural_qualified) {
            state.natural_qualified_s += count_dt_s;
        }
        if (current_on) {
            state.switch_on_s += count_dt_s;
        }
        if (natural_qualified && current_on) {
            state.overlap_s += count_dt_s;
        }
        if (natural_qualified || current_on) {
            state.qualified_light_s += count_dt_s;
        }
    }
    state.last_count_hour = in.local_hour;

    const float target_light_s = float(sp.target_light_minutes) * 60.0f;
    const bool minutes_below_target = state.qualified_light_s < target_light_s;
    const bool lux_below_on = natural_lux < sp.lux_on_threshold;
    const bool lux_below_off = natural_lux < lux_off_threshold;
    const bool exterior_lux_below_on = exterior_lux_available && exterior_lux < sp.lux_on_threshold;
    const bool exterior_lux_below_off = exterior_lux_available && exterior_lux < lux_off_threshold;
    const bool plant_supplement_demand = sp.auto_enabled
        && in_window
        && minutes_below_target
        && ((!current_on && lux_below_on) || (current_on && lux_below_off));
    const bool occupancy_task_light_demand = sp.auto_enabled
        && in.occupied
        && exterior_lux_available
        && ((!current_on && exterior_lux_below_on) || (current_on && exterior_lux_below_off));

    bool want_on = false;
    const char* reason = "auto_disabled";
    if (!sp.auto_enabled) {
        want_on = false;
        reason = "auto_disabled";
    } else if (occupancy_task_light_demand) {
        want_on = true;
        reason = (!current_on && exterior_lux_below_on) ? "occupancy_lux_low" : "occupancy_hysteresis_hold";
    } else if (plant_supplement_demand) {
        want_on = true;
        reason = (!current_on && lux_below_on) ? "plant_lux_low" : "plant_hysteresis_hold";
    } else if (in.occupied && !exterior_lux_available) {
        want_on = false;
        reason = "occupancy_lux_unavailable";
    } else if (in.occupied && exterior_lux_available) {
        want_on = false;
        reason = "lux_sufficient";
    } else if (!in_window) {
        want_on = false;
        reason = "outside_window";
    } else if (!minutes_below_target) {
        want_on = false;
        reason = "minutes_met";
    } else {
        want_on = false;
        reason = "lux_sufficient";
    }

    const uint32_t elapsed = state.last_transition_tick_ms == 0
        ? UINT32_MAX
        : (now_ms - state.last_transition_tick_ms);
    if (want_on && !current_on && elapsed < sp.min_off_ms) {
        want_on = false;
        reason = "min_off_hold";
    } else if (!want_on && current_on && elapsed < sp.min_on_ms) {
        want_on = true;
        reason = "min_on_hold";
    }

    if (want_on != state.on) {
        state.on = want_on;
        state.last_transition_tick_ms = now_ms;
    }
    state.last_reason = reason;

    return {
        .want_on = want_on,
        .in_window = in_window,
        .minutes_below_target = minutes_below_target,
        .natural_qualified = natural_qualified,
        .lux_below_on_threshold = lux_below_on,
        .lux_below_off_threshold = lux_below_off,
        .exterior_lux_available = exterior_lux_available,
        .occupancy_task_light_demand = occupancy_task_light_demand,
        .plant_supplement_demand = plant_supplement_demand,
        .lux_off_threshold = lux_off_threshold,
        .qualified_light_minutes = state.qualified_light_s / 60.0f,
        .target_light_minutes = float(sp.target_light_minutes),
        .remaining_light_minutes = std::max(0.0f, (target_light_s - state.qualified_light_s) / 60.0f),
        .reason = reason
    };
}

// Unified band-first controller.
//
// Policy: safety rails still preempt everything, but normal control prioritizes
// temp-band compliance, then VPD-band compliance. Failed sealed humidification
// enters a timed backoff instead of forcing VENTILATE. Venting is selected only
// for cooling/dehum/outdoor-exchange cases where it serves the active demand.
inline Mode determine_mode_band_first(
    const SensorInputs& in,
    const Setpoints& sp,
    ControlState& state,
    uint32_t dt_ms
) {
    if (state.sentinel != STATE_SENTINEL) {
        state = initial_state();
    }
    state.vent_mist_assist_active = false;

    if (!sensors_plausible(in)) {
        state.mode = SENSOR_FAULT;
        state.mist_stage = MIST_WATCH;
        state.sealed_timer_ms = 0;
        state.relief_timer_ms = 0;
        state.vpd_watch_timer_ms = 0;
        state.mist_stage_timer_ms = 0;
        state.relief_cycle_count = 0;
        state.vent_latch_timer_ms = 0;
        state.mist_backoff_timer_ms = 0;
        state.vent_mist_assist_active = false;
        state.last_mode_reason = "sensor_fault";
        return SENSOR_FAULT;
    }

    const Mode prev = state.mode_prev;
    const float temp_high = sp.temp_high;
    const float HV = band_vpd_hysteresis(sp);

    const bool safety_cool = in.temp_f >= sp.safety_max;
    const bool safety_heat = in.temp_f <= sp.safety_min;
    const bool was_cooling = (prev == VENTILATE) || (prev == THERMAL_RELIEF);
    const bool vpd_high = in.vpd_kpa > sp.vpd_high;
    const bool vpd_high_resolved = in.vpd_kpa <= (sp.vpd_high - HV);
    const bool outdoor_cold_for_vent =
        std::isfinite(in.outdoor_temp_f) && in.outdoor_temp_f < (sp.temp_low - 10.0f);
    const float cooling_exit_hysteresis =
        outdoor_cold_for_vent ? std::max(sp.temp_hysteresis, 3.0f) : sp.temp_hysteresis;
    const float cooling_entry_margin =
        outdoor_cold_for_vent ? band_cool_stage2_delta_f(sp) : 0.0f;
    const bool needs_cooling = was_cooling
        ? in.temp_f > (temp_high - cooling_exit_hysteresis)
        : in.temp_f > (temp_high + cooling_entry_margin);
    const float heat_target = band_heat_target_f(sp);
    const bool temp_below_band = in.temp_f < sp.temp_low;
    const bool needs_heating_s1 = in.temp_f < (heat_target + sp.heat_hysteresis);

    const bool cold_dehum_allowed =
        !outdoor_cold_for_vent || in.temp_f > (sp.temp_low + std::max(2.0f, sp.temp_hysteresis));
    const bool vpd_low_enter = in.vpd_kpa < (sp.vpd_low - HV) && !sp.econ_block && cold_dehum_allowed;
    const bool vpd_dehum_exit = in.vpd_kpa >= sp.vpd_low || !cold_dehum_allowed;
    const bool was_dehum = prev == DEHUM_VENT;
    const bool moisture_blocked = moisture_blocked_by_occupancy(in, sp);

    {
        if (temp_below_band) {
            state.heat2_latched = true;
        } else if (in.temp_f >= heat_target) {
            state.heat2_latched = false;
        }
    }

    if (vpd_high && !safety_cool && !safety_heat) {
        state.vpd_watch_timer_ms = sat_add(state.vpd_watch_timer_ms, dt_ms);
    } else if (!vpd_high) {
        state.vpd_watch_timer_ms = 0;
        state.relief_cycle_count = 0;
        state.vent_latch_timer_ms = 0;
        state.mist_backoff_timer_ms = 0;
    }
    const bool humidify_ready = vpd_high && state.vpd_watch_timer_ms >= sp.vpd_watch_dwell_ms;

    if (state.mist_backoff_timer_ms > 0) {
        if (!vpd_high) {
            state.mist_backoff_timer_ms = 0;
            state.relief_cycle_count = 0;
        } else if (state.mist_backoff_timer_ms >= sp.mist_backoff_ms) {
            state.mist_backoff_timer_ms = 0;
        } else {
            state.mist_backoff_timer_ms = sat_add(state.mist_backoff_timer_ms, dt_ms);
        }
    }

    state.override_summer_vent = false;
    {
        const bool outdoor_data_fresh = in.outdoor_data_age_s < sp.outdoor_staleness_max_s;
        const bool outdoor_cooler = in.outdoor_temp_f < (in.temp_f - sp.vent_prefer_temp_delta_f);
        const bool outdoor_drier_dp = in.outdoor_dewpoint_f < (in.dew_point_f - sp.vent_prefer_dp_delta_f);
        state.override_summer_vent = sp.sw_summer_vent_enabled
                                  && outdoor_data_fresh
                                  && outdoor_cooler
                                  && outdoor_drier_dp
                                  && needs_cooling
                                  && humidify_ready;
    }

    Mode mode = IDLE;
    state.dry_override_active = false;
    state.last_mode_reason = "idle";

    if (safety_cool) {
        mode = SAFETY_COOL;
        state.last_mode_reason = "safety_cool";
        state.sealed_timer_ms = 0;
        state.relief_timer_ms = 0;
        state.vpd_watch_timer_ms = 0;
        state.relief_cycle_count = 0;
        state.vent_latch_timer_ms = 0;
        state.mist_backoff_timer_ms = 0;
        state.vent_mist_assist_active = false;
    } else if (safety_heat) {
        mode = SAFETY_HEAT;
        state.last_mode_reason = "safety_heat";
        state.sealed_timer_ms = 0;
        state.relief_timer_ms = 0;
        state.vpd_watch_timer_ms = 0;
        state.relief_cycle_count = 0;
        state.vent_latch_timer_ms = 0;
        state.mist_backoff_timer_ms = 0;
        state.vent_mist_assist_active = false;
    } else if (prev == SEALED_MIST) {
        state.sealed_timer_ms = sat_add(state.sealed_timer_ms, dt_ms);
        if (vpd_high_resolved || moisture_blocked) {
            mode = needs_cooling ? VENTILATE : IDLE;
            state.last_mode_reason = moisture_blocked ? "moisture_blocked" : "humidify_resolved";
            state.sealed_timer_ms = 0;
            state.vpd_watch_timer_ms = 0;
            state.relief_cycle_count = 0;
            state.vent_latch_timer_ms = 0;
            state.mist_backoff_timer_ms = 0;
            state.mist_stage = MIST_WATCH;
            state.mist_stage_timer_ms = 0;
        } else if (needs_cooling || in.temp_f >= (sp.safety_max - sp.safety_max_seal_margin_f)) {
            mode = VENTILATE;
            state.last_mode_reason = "temp_preempts_humidify";
            state.sealed_timer_ms = 0;
            state.vent_latch_timer_ms = 0;
            state.mist_backoff_timer_ms = 0;
            state.mist_stage = MIST_WATCH;
            state.mist_stage_timer_ms = 0;
        } else if (state.sealed_timer_ms >= sp.sealed_max_ms) {
            mode = IDLE;
            state.last_mode_reason = "mist_backoff";
            state.relief_cycle_count = sat_add(state.relief_cycle_count, 1);
            state.sealed_timer_ms = 0;
            state.relief_timer_ms = 0;
            state.vent_latch_timer_ms = 0;
            state.mist_backoff_timer_ms = dt_ms;
            state.mist_stage = MIST_WATCH;
            state.mist_stage_timer_ms = 0;
        } else {
            mode = SEALED_MIST;
            state.last_mode_reason = "humidify_continue";
        }
    } else if (needs_cooling) {
        mode = VENTILATE;
        state.last_mode_reason = state.override_summer_vent ? "summer_vent" : "temp_high";
        state.sealed_timer_ms = 0;
    } else if (vpd_low_enter) {
        mode = DEHUM_VENT;
        state.last_mode_reason = "vpd_low";
        state.sealed_timer_ms = 0;
    } else if (was_dehum && !vpd_dehum_exit && !sp.econ_block) {
        mode = DEHUM_VENT;
        state.last_mode_reason = "dehum_continue";
        state.sealed_timer_ms = 0;
    } else if (state.mist_backoff_timer_ms > 0) {
        mode = IDLE;
        state.last_mode_reason = "mist_backoff";
        state.sealed_timer_ms = 0;
    } else if (humidify_ready
               && !moisture_blocked
               && !temp_below_band
               && in.temp_f < (sp.safety_max - sp.safety_max_seal_margin_f)) {
        mode = SEALED_MIST;
        state.last_mode_reason = "humidify_enter";
        state.sealed_timer_ms = dt_ms;
        state.mist_stage = MIST_S1;
        state.mist_stage_timer_ms = 0;
        state.vent_latch_timer_ms = 0;
    } else {
        mode = IDLE;
        if (needs_heating_s1) {
            state.last_mode_reason = state.heat2_latched ? "heat_stage2" : "heat_stage1";
        } else {
            state.last_mode_reason = "idle";
        }
        state.sealed_timer_ms = 0;
    }

    {
        const bool safety_preempts_dwell =
            (mode == SAFETY_COOL) || (mode == SAFETY_HEAT) || (mode == SENSOR_FAULT);
        const bool mode_would_change = mode != state.mode_prev;
        const bool in_dwell = state.last_transition_tick_ms < sp.dwell_gate_ms;
        if (sp.sw_dwell_gate_enabled
            && mode_would_change
            && in_dwell
            && !safety_preempts_dwell) {
            mode = state.mode_prev;
            state.last_mode_reason = "dwell_hold";
        }
        if (mode != state.mode_prev) {
            state.last_transition_tick_ms = 0;
        } else {
            state.last_transition_tick_ms = sat_add(state.last_transition_tick_ms, dt_ms);
        }
    }

    if (mode == SEALED_MIST) {
        state.mist_stage_timer_ms = sat_add(state.mist_stage_timer_ms, dt_ms);
        switch (state.mist_stage) {
            case MIST_WATCH:
                state.mist_stage = MIST_S1;
                state.mist_stage_timer_ms = 0;
                break;
            case MIST_S1:
                if (state.mist_stage_timer_ms >= sp.mist_s2_delay_ms && in.vpd_kpa > sp.vpd_high) {
                    state.mist_stage = MIST_S2;
                    state.mist_stage_timer_ms = 0;
                }
                break;
            case MIST_S2: {
                const bool fog_gated = !fog_permitted(in, sp) || moisture_blocked;
                if (in.vpd_kpa > sp.vpd_high + sp.fog_escalation_kpa && !fog_gated) {
                    state.mist_stage = MIST_FOG;
                    state.mist_stage_timer_ms = 0;
                } else if (vpd_high_resolved) {
                    state.mist_stage = MIST_S1;
                    state.mist_stage_timer_ms = 0;
                }
                break;
            }
            case MIST_FOG:
                if (in.vpd_kpa <= sp.vpd_high + sp.fog_escalation_kpa) {
                    state.mist_stage = MIST_S2;
                    state.mist_stage_timer_ms = 0;
                }
                break;
            default:
                state.mist_stage = MIST_WATCH;
                state.mist_stage_timer_ms = 0;
                break;
        }
    } else if (state.mist_stage != MIST_WATCH) {
        state.mist_stage = MIST_WATCH;
        state.mist_stage_timer_ms = 0;
    }

    state.vent_mist_assist_active =
        (mode == VENTILATE)
        && humidify_ready
        && !moisture_blocked
        && !safety_cool
        && !safety_heat
        && in.temp_f < (sp.safety_max - sp.safety_max_seal_margin_f);

    state.mode = mode;
    state.mode_prev = mode;
    return mode;
}

// ═══════════════════════════════════════════════════════════════════
// determine_mode()
// ═══════════════════════════════════════════════════════════════════
inline Mode determine_mode(
    const SensorInputs& in,
    const Setpoints& sp,
    ControlState& state,
    uint32_t dt_ms
) {
    if (sp.sw_fsm_controller_enabled) {
        return determine_mode_band_first(in, sp, state, dt_ms);
    }

    // ── Sentinel check — detect state corruption ──
    if (state.sentinel != STATE_SENTINEL) {
        state = initial_state();
    }
    state.vent_mist_assist_active = false;

    // ── R2-4: Plausibility guard ──
    if (!sensors_plausible(in)) {
        state.mode = SENSOR_FAULT;
        // R2-2: Preserve mode_prev for recovery hysteresis.
        // But scrub ALL active control state — especially mist_stage,
        // because ESPHome reads mist_stage to drive physical relays.
        // A stale MIST_S2 during SENSOR_FAULT = misters running with no feedback.
        state.mist_stage = MIST_WATCH;
        state.sealed_timer_ms = 0;
        state.relief_timer_ms = 0;
        state.vpd_watch_timer_ms = 0;
        state.mist_stage_timer_ms = 0;
        state.relief_cycle_count = 0;
        state.vent_latch_timer_ms = 0;
        state.mist_backoff_timer_ms = 0;
        state.vent_mist_assist_active = false;
        return SENSOR_FAULT;
    }

    // ── Capture previous mode BEFORE any logic ──
    const Mode prev = state.mode_prev;

    // Sprint-12: target the interior of the band, not the edges. 25% of
    // band width inward on each side → plants operate in the middle 50%
    // of the operator-pushed (temp_low, temp_high) band. Example with
    // temp_low=62, temp_high=75: heating target ~65.25°F, cooling target
    // ~71.75°F. bias_heat / bias_cool still apply as symmetric offsets
    // from the interior target. The max(2.0f) floor prevents inversion
    // under pathologically narrow bands (dispatcher could push temp_low
    // ≈ temp_high); validate_setpoints already forbids it, but we
    // belt-and-suspender here so the controller can't divide into a
    // degenerate Tlow > Thigh state from bad input.
    const float band_width = std::max(2.0f, sp.temp_high - sp.temp_low);
    const float Tlow_interior  = sp.temp_low  + band_width * 0.25f;
    const float Thigh_interior = sp.temp_high - band_width * 0.25f;
    const float Thigh = Thigh_interior + sp.bias_cool;

    const float vpd_width    = std::max(0.2f, sp.vpd_high - sp.vpd_low);
    const float vpd_low_eff  = sp.vpd_low  + vpd_width * 0.25f;
    const float vpd_high_eff = sp.vpd_high - vpd_width * 0.25f;
    const float HV    = std::min(sp.vpd_hysteresis, vpd_high_eff * 0.5f);

    bool safety_cool    = in.temp_f >= sp.safety_max;
    bool safety_heat    = in.temp_f <= sp.safety_min;
    bool vpd_above_band = in.vpd_kpa > vpd_high_eff;
    bool vpd_below_exit = in.vpd_kpa < (vpd_high_eff - HV);

    bool vpd_too_low_enter = in.vpd_kpa < (vpd_low_eff - HV) && !sp.econ_block;
    bool vpd_dehum_exit    = in.vpd_kpa >= vpd_low_eff;

    bool was_ventilating = (prev == VENTILATE);
    bool needs_cooling   = was_ventilating
        ? in.temp_f > (Thigh - sp.temp_hysteresis)
        : in.temp_f > Thigh;

    bool was_sealed = (prev == SEALED_MIST);
    bool was_dehum  = (prev == DEHUM_VENT);
    bool in_thermal_relief = (prev == THERMAL_RELIEF);

    // ── Sprint-9 P1#7: Heat S2 latch ──
    // Set when temp drops below Tlow - dH2 (gas-stage demand).
    // Clear when S1 is satisfied (temp >= Tlow + heat_hysteresis).
    // In between the two thresholds the latch holds its state,
    // preventing gas-valve rapid-cycling in the hysteresis band.
    {
        // Sprint-12: Tlow now references the band interior (25% up from
        // temp_low) rather than the edge. S2 gas demand fires at
        // Tlow_interior + bias_heat - dH2 — still below the heating target
        // but now inside the band instead of below it.
        const float Tlow = Tlow_interior + sp.bias_heat;
        if (in.temp_f < (Tlow - sp.dH2)) {
            state.heat2_latched = true;
        } else if (in.temp_f >= (Tlow + sp.heat_hysteresis)) {
            state.heat2_latched = false;
        }
        // Else: hysteresis band — leave latch as-is.
    }

    // ── VPD watch timer — suspended during safety modes ──
    if (vpd_above_band
        && prev != SEALED_MIST && prev != THERMAL_RELIEF
        && prev != SAFETY_COOL && prev != SAFETY_HEAT) {
        state.vpd_watch_timer_ms = sat_add(state.vpd_watch_timer_ms, dt_ms);
    } else if (!vpd_above_band
        && prev != SEALED_MIST && prev != THERMAL_RELIEF) {
        state.vpd_watch_timer_ms = 0;
        // VPD is below band and we're not in a sealed/relief cycle.
        // Reset the relief cycle breaker so misting can re-engage next time.
        // Without this, hitting max_relief_cycles permanently latches
        // VENTILATE and the greenhouse can never mist again.
        state.relief_cycle_count = 0;
        state.vent_latch_timer_ms = 0;  // FW-8
    }
    bool vpd_wants_seal = vpd_above_band && state.vpd_watch_timer_ms >= sp.vpd_watch_dwell_ms;

    // ── Sprint-15: summer thermal-driven vent preference gate ──
    // When the screen-door intake is open in summer, outdoor-air exchange
    // is a real heat sink. Pre-sprint-15 logic prioritized VPD-seal over
    // thermal-vent unconditionally; on hot dry days (today: indoor 91°F /
    // 65% RH, outdoor 77°F / 8% RH) that sealed the greenhouse against its
    // own best cooling. The gate pre-empts vpd_wants_seal when:
    //   1. operator hasn't disabled the feature
    //   2. outdoor reading is fresh
    //   3. outdoor air is at least vent_prefer_temp_delta_f cooler
    //   4. outdoor dewpoint is at least vent_prefer_dp_delta_f lower
    //   5. indoor temp is above the heating-target hysteresis (otherwise
    //      we'd vent into a cold night)
    // Falls through to existing VENTILATE path. Safety rails, THERMAL_RELIEF,
    // and DEHUM_VENT all still pre-empt this gate (they're checked first).
    // See docs/firmware-sprint-15-summer-vent-spec.md.
    //
    // Sprint-15.1 fix 2: gate now pre-empts BOTH new seal entries AND
    // ongoing sealed cycles. Pre-15.1 the gate only set vpd_wants_seal=false
    // which didn't affect the was_sealed sticky path (around line 258) —
    // so once firmware was in SEALED_MIST for even one cycle (stale
    // outdoor data, dwell just matured, etc.), the gate was toothless
    // until normal exit conditions fired. Matches the observed
    // 2026-04-20 23:20 → 05:30 MDT whipsaw. The `was_sealed` branch
    // below now also sees vent_preferred semantics: we clean up the
    // sealed state (mirror of the vpd_below_exit exit path) and force
    // was_sealed=false so the cascade falls through to VENTILATE.
    state.override_summer_vent = false;
    {
        const bool outdoor_data_fresh = (in.outdoor_data_age_s < sp.outdoor_staleness_max_s);
        const bool outdoor_cooler     = (in.outdoor_temp_f      < (in.temp_f      - sp.vent_prefer_temp_delta_f));
        const bool outdoor_drier_dp   = (in.outdoor_dewpoint_f  < (in.dew_point_f - sp.vent_prefer_dp_delta_f));
        const bool temp_above_band    = (in.temp_f > (sp.temp_low + sp.temp_hysteresis));
        const bool vent_preferred     = sp.sw_summer_vent_enabled
                                     && outdoor_data_fresh
                                     && outdoor_cooler
                                     && outdoor_drier_dp
                                     && temp_above_band;
        if (vent_preferred && (vpd_wants_seal || was_sealed)) {
            // Pre-empt the seal — clear entry dwell AND clean up ongoing
            // sealed state. Telemetry flag is read by evaluate_overrides()
            // and surfaced via active_overrides = "summer_vent".
            vpd_wants_seal = false;
            state.override_summer_vent = true;
            if (was_sealed) {
                // Mirror the vpd_below_exit cleanup (was_sealed → IDLE/VENT
                // exit path) so the normal cascade treats this like a
                // clean seal exit. Needed because the was_sealed branch
                // below doesn't consult vpd_wants_seal.
                state.sealed_timer_ms = 0;
                state.vpd_watch_timer_ms = 0;
                state.relief_cycle_count = 0;
                state.vent_latch_timer_ms = 0;
                state.mist_stage = MIST_WATCH;
                state.mist_stage_timer_ms = 0;
                was_sealed = false;  // force normal-cascade path
            }
        }
    }

    // ── Priority-ordered mode determination ──
    Mode mode = IDLE;
    bool relief_just_expired = false;
    // Sprint-15.1 fix 8: track which branch chose the current mode so we
    // can RCA gate/seal/idle decisions post-hoc via gh_mode_reason.
    state.last_mode_reason = "idle_default";

    if (safety_cool) {
        mode = SAFETY_COOL;
        state.last_mode_reason = "safety_cool";
        state.sealed_timer_ms = 0;
        state.relief_timer_ms = 0;
        state.vpd_watch_timer_ms = 0;   // sprint-8: match "suspended during safety" comment
        state.relief_cycle_count = 0;
        state.vent_latch_timer_ms = 0;  // FW-8
    } else if (safety_heat) {
        mode = SAFETY_HEAT;
        state.last_mode_reason = "safety_heat";
        state.sealed_timer_ms = 0;
        state.relief_timer_ms = 0;      // sprint-8 P1#5: match SAFETY_COOL
        state.vpd_watch_timer_ms = 0;   // sprint-8: match SAFETY_COOL
        state.relief_cycle_count = 0;
        state.vent_latch_timer_ms = 0;  // sprint-8 P1#5: match SAFETY_COOL
    } else if (in_thermal_relief) {
        state.relief_timer_ms = sat_add(state.relief_timer_ms, dt_ms);
        if (state.relief_timer_ms >= sp.relief_duration_ms) {
            state.relief_timer_ms = 0;
            state.sealed_timer_ms = 0;
            state.vpd_watch_timer_ms = 0;
            relief_just_expired = true;
            if (vpd_above_band) state.relief_cycle_count++;
            else state.relief_cycle_count = 0;
        } else {
            mode = THERMAL_RELIEF;
            state.last_mode_reason = "thermal_relief";
        }
    }

    const bool moisture_blocked = moisture_blocked_by_occupancy(in, sp);

    if (mode != SAFETY_COOL && mode != SAFETY_HEAT && mode != THERMAL_RELIEF) {
        if (was_sealed && !relief_just_expired) {
            // Exit sealed if: VPD resolved, sealed too long, OR someone is present
            if (vpd_below_exit || moisture_blocked) {
                mode = needs_cooling ? VENTILATE : IDLE;
                state.last_mode_reason = "seal_exit";
                state.sealed_timer_ms = 0;
                state.vpd_watch_timer_ms = 0;
                state.relief_cycle_count = 0;
                state.vent_latch_timer_ms = 0;  // FW-8
                state.mist_stage = MIST_WATCH;
                state.mist_stage_timer_ms = 0;
            } else if (state.sealed_timer_ms >= sp.sealed_max_ms
                       || in.temp_f >= (sp.safety_max - sp.safety_max_seal_margin_f)) {  // FW-7: bail if too hot
                mode = THERMAL_RELIEF;
                state.last_mode_reason = "thermal_relief_forced";
                state.relief_timer_ms = 0;
            } else {
                mode = SEALED_MIST;
                state.last_mode_reason = "seal_continue";
                state.sealed_timer_ms = sat_add(state.sealed_timer_ms, dt_ms);
            }
        // R2-6: Gate seal entry by relief cycle count AND occupancy
        // FW-7: Also gate by temperature — don't seal when within
        // safety_max_seal_margin_f of safety_max (sprint-10 0.4b: tunable).
        } else if (vpd_wants_seal && !moisture_blocked
                   && state.relief_cycle_count < sp.max_relief_cycles
                   && in.temp_f < (sp.safety_max - sp.safety_max_seal_margin_f)) {
            mode = SEALED_MIST;
            state.last_mode_reason = "seal_enter";
            state.sealed_timer_ms = dt_ms;
            state.mist_stage = MIST_S1;
            state.mist_stage_timer_ms = 0;
            state.vent_latch_timer_ms = 0;  // FW-8: reset on successful seal entry
        } else if (vpd_wants_seal && state.relief_cycle_count >= sp.max_relief_cycles) {
            // R2-6: Exceeded max consecutive sealed→relief. Force vent to break cycle.
            mode = VENTILATE;
            state.last_mode_reason = "relief_cycle_breaker";
            // FW-8: Timeout — if latched past vent_latch_timeout_ms (sprint-10
            // 0.4b: tunable; default 30 min) with VPD still above band, retry.
            state.vent_latch_timer_ms = sat_add(state.vent_latch_timer_ms, dt_ms);
            if (state.vent_latch_timer_ms >= sp.vent_latch_timeout_ms) {
                state.relief_cycle_count = 0;
                state.vent_latch_timer_ms = 0;
            }
        } else if (vpd_too_low_enter) {
            mode = DEHUM_VENT;
            state.last_mode_reason = "vpd_too_low";
        } else if (was_dehum && !vpd_dehum_exit && !sp.econ_block) {
            // R2-8: Sticky dehum respects econ_block changes mid-cycle
            mode = DEHUM_VENT;
            state.last_mode_reason = "dehum_continue";
        } else if (needs_cooling) {
            mode = VENTILATE;
            // If sprint-15 gate pre-empted a seal this cycle, mark the
            // reason accordingly so observers can distinguish thermal
            // vent from gate-driven vent.
            state.last_mode_reason = state.override_summer_vent
                ? "summer_vent_preempt"
                : "temp_vent";
        } else {
            mode = IDLE;
            state.last_mode_reason = "idle_default";
        }
    }

    // ── R2-3: VPD dry override — cannot stomp active cooling or safety ──
    //
    // Sprint-9 P1#4: R2-3 intentionally bypasses `max_relief_cycles`.
    // Under sustained extreme dryness (VPD > vpd_max_safe) plant damage
    // outranks actuator thrash; the relief-cycle breaker protects the
    // vent motor, but if we've fallen through to R2-3 the priority is
    // moisture delivery. Temp-adjacent-safety is implicitly covered by
    // the existing `temp_f < Thigh - temp_hysteresis` precondition
    // (stricter than `safety_max - 5` under validate_setpoints'd bounds).
    {
        const bool can_seal_for_dryness =
            (mode != SAFETY_COOL)
            && (mode != SAFETY_HEAT)
            && (mode != THERMAL_RELIEF)
            && (mode != VENTILATE)
            && !needs_cooling
            && !moisture_blocked
            && (in.temp_f < (Thigh - sp.temp_hysteresis));

        // OBS-1e patch: capture pre-override mode so we can tell whether
        // R2-3 forced a seal the planner's dwell hadn't yet sanctioned.
        const Mode pre_r23_mode = mode;
        state.dry_override_active = false;

        if (in.vpd_kpa > sp.vpd_max_safe && can_seal_for_dryness) {
            mode = SEALED_MIST;
            // Sprint-15.1 fix 8: tag this path distinctly so gh_mode_reason
            // shows "dry_override" rather than the prior branch's reason.
            state.last_mode_reason = "dry_override";
            // sprint-8 P0#1/P0#2: only seed "new seal" state when we weren't
            // already in SEALED_MIST. Otherwise:
            //   - resetting mist_stage would demote MIST_S2/MIST_FOG to
            //     MIST_S1 at exactly the moment peak VPD needs peak misting;
            //   - resetting sealed_timer_ms every cycle would make
            //     sealed_max_ms unreachable under sustained extreme
            //     dryness, silently defeating the THERMAL_RELIEF backstop.
            // The override flag still follows the prior semantics (only
            // set when R2-3 actually forced the transition).
            if (pre_r23_mode != SEALED_MIST) {
                state.vpd_watch_timer_ms = sp.vpd_watch_dwell_ms;
                state.sealed_timer_ms = dt_ms;
                state.mist_stage = MIST_S1;
                state.mist_stage_timer_ms = 0;
            }
            state.dry_override_active = (pre_r23_mode != SEALED_MIST);
        }
    }
    // Sprint-10 0.4c: dangerous-humidity override. Pre-sprint-10 only
    // fired from IDLE; a sticky SEALED_MIST could drive VPD below
    // vpd_min_safe with no exit path. Now also breaks out of SEALED_MIST
    // with state cleanup (mirrors the normal was_sealed → exit path so
    // the next seal cycle starts clean, not inheriting stale timers or
    // a stale mist_stage).
    if (in.vpd_kpa < sp.vpd_min_safe && (mode == IDLE || mode == SEALED_MIST)) {
        if (!sp.econ_block) {
            if (mode == SEALED_MIST) {
                state.sealed_timer_ms = 0;
                state.vpd_watch_timer_ms = 0;
                state.relief_cycle_count = 0;
                state.vent_latch_timer_ms = 0;
                state.mist_stage = MIST_WATCH;
                state.mist_stage_timer_ms = 0;
            }
            mode = DEHUM_VENT;
            state.last_mode_reason = "vpd_min_safe_rescue";  // sprint-15.1 fix 8
        }
        // else econ_block=true → stay in current mode; policy choice
        // documented in backlog (P3#15 still open).
    }

    // ── Phase-2 dwell gate ────────────────────────────────────────────
    // Hold non-safety mode transitions for at least sp.dwell_gate_ms after
    // the most recent accepted transition. Closes the whipsaw pattern
    // observed 2026-04-17 (59 mode changes in 2h stable window) and
    // 2026-04-20 (relief_cycle_breaker thrashing). Replay projects 80%
    // reduction in stable-conditions transitions.
    //
    // Preempts: safety rails (SAFETY_COOL/HEAT), FAULT_HOLD-equivalent
    // (SENSOR_FAULT), R2-3 dry override, vpd_min_safe rescue. Safety
    // must ALWAYS fire immediately — no dwell gate on life-safety paths.
    //
    // Shadow mode: default sp.sw_dwell_gate_enabled=false. Firmware
    // logs what it WOULD decide (via last_mode_reason suffix) but still
    // applies the transition. After 14d shadow-mode bake, flip to true.
    // See plan Phase 2 gate criteria.
    //
    // Accounting: last_transition_tick_ms is a "ms since last accepted
    // transition" accumulator. Each cycle: += dt_ms if mode unchanged,
    // reset to 0 when we accept a new transition.
    {
        // THERMAL_RELIEF is transient-by-design (relief_timer cap, default
        // 90s). Holding it past its designed duration makes the firmware
        // re-enter the in_thermal_relief branch every tick, bumping
        // relief_cycle_count once per relief_duration window and tripping
        // the max_relief_cycles breaker faster than it would without the
        // gate. Both directions (into AND out of THERMAL_RELIEF) must
        // bypass dwell so relief runs its designed course.
        // Learned 2026-04-21 19:14-19:50 live trial; see plan Phase 2.
        const bool transient_relief =
            (mode == THERMAL_RELIEF) || (state.mode_prev == THERMAL_RELIEF);

        const bool safety_preempts_dwell =
            (mode == SAFETY_COOL) || (mode == SAFETY_HEAT) ||
            (mode == SENSOR_FAULT) ||
            transient_relief ||
            state.dry_override_active ||
            (in.vpd_kpa < sp.vpd_min_safe);

        const bool mode_would_change = (mode != state.mode_prev);
        const bool in_dwell = state.last_transition_tick_ms < sp.dwell_gate_ms;

        if (sp.sw_dwell_gate_enabled
            && mode_would_change
            && in_dwell
            && !safety_preempts_dwell) {
            // Hold. Report via last_mode_reason so diagnostics see it.
            mode = state.mode_prev;
            state.last_mode_reason = "dwell_hold";
        }
        // Update accumulator regardless of flag state — shadow mode needs
        // the counter so post-flip the first transition has correct dwell.
        if (mode != state.mode_prev) {
            state.last_transition_tick_ms = 0;
        } else {
            state.last_transition_tick_ms = sat_add(state.last_transition_tick_ms, dt_ms);
        }
    }

    // ── Mist stage progression ──
    if (mode == SEALED_MIST) {
        // Occupancy blocks ALL moisture — freeze mist stage if occupied
        if (moisture_blocked) {
            state.mist_stage = MIST_WATCH;
            state.mist_stage_timer_ms = 0;
        } else {
            state.mist_stage_timer_ms = sat_add(state.mist_stage_timer_ms, dt_ms);
            switch (state.mist_stage) {
                case MIST_WATCH:
                    state.mist_stage = MIST_S1;
                    state.mist_stage_timer_ms = 0;
                    break;
                case MIST_S1:
                    // Sprint-12: escalate at interior vpd target, not raw edge.
                    if (state.mist_stage_timer_ms >= sp.mist_s2_delay_ms
                        && in.vpd_kpa > vpd_high_eff) {
                        state.mist_stage = MIST_S2;
                        state.mist_stage_timer_ms = 0;
                    }
                    break;
                case MIST_S2: {
                    const bool fog_gated = !fog_permitted(in, sp)
                                        || moisture_blocked_by_occupancy(in, sp);
                    if (in.vpd_kpa > vpd_high_eff + sp.fog_escalation_kpa && !fog_gated) {
                        state.mist_stage = MIST_FOG;
                        state.mist_stage_timer_ms = 0;
                    }
                    if (in.vpd_kpa < vpd_high_eff - HV) {
                        state.mist_stage = MIST_S1;
                        state.mist_stage_timer_ms = 0;
                    }
                    break;
                }
                case MIST_FOG:
                    if (in.vpd_kpa <= vpd_high_eff + sp.fog_escalation_kpa) {
                        state.mist_stage = MIST_S2;
                        state.mist_stage_timer_ms = 0;
                    }
                    break;
                default:
                    state.mist_stage = MIST_WATCH;
                    state.mist_stage_timer_ms = 0;
                    break;
            }
        }
    } else {
        if (state.mist_stage != MIST_WATCH) {
            state.mist_stage = MIST_WATCH;
            state.mist_stage_timer_ms = 0;
        }
    }

    state.mode = mode;
    state.mode_prev = mode;
    return mode;
}

// ═══════════════════════════════════════════════════════════════════
// evaluate_overrides() — Pure function. OBS-1e (Sprint 16).
//
// Inspects the just-resolved mode + state + inputs and flags each
// firmware-side decision that blocks or supersedes planner intent.
// Every flag is "desire-triggered" — only fires when the planner
// would have wanted the blocked action, not every cycle the
// condition technically holds. Evaluator is pure: no state mutation.
// ═══════════════════════════════════════════════════════════════════
inline OverrideFlags evaluate_overrides(
    const SensorInputs& in,
    const Setpoints& sp,
    const ControlState& state,
    Mode mode
) noexcept {
    OverrideFlags f{};
    if (!sensors_plausible(in)) return f;

    // Sprint-12: mirror the interior-targeting in determine_mode so the
    // observability flags report against the same thresholds the state
    // machine actually uses.
    const float vpd_width    = std::max(0.2f, sp.vpd_high - sp.vpd_low);
    const float vpd_high_eff = sp.vpd_high - vpd_width * 0.25f;
    const float HV = std::min(sp.vpd_hysteresis, vpd_high_eff * 0.5f);
    const bool vpd_above_band = in.vpd_kpa > vpd_high_eff;
    const bool vpd_wants_seal =
        vpd_above_band && state.vpd_watch_timer_ms >= sp.vpd_watch_dwell_ms;
    const bool moisture_blocked = moisture_blocked_by_occupancy(in, sp);

    const bool occupancy_blocks_mister_path =
        moisture_blocked && (mode == SEALED_MIST || vpd_wants_seal);
    const bool occupancy_blocks_fan_path =
        air_blocked_by_occupancy(in, sp, mode)
        && (mode == THERMAL_RELIEF || mode == VENTILATE || mode == DEHUM_VENT);
    f.occupancy_blocks_equipment =
        occupancy_blocks_mister_path || occupancy_blocks_fan_path;

    // Fog gates — only meaningful while in MIST_S2 and VPD has climbed far
    // enough that the firmware would escalate to MIST_FOG. Any one of the
    // three gates blocks that transition.
    const bool fog_wanted =
        (mode == SEALED_MIST)
        && (state.mist_stage == MIST_S2)
        && (in.vpd_kpa > vpd_high_eff + sp.fog_escalation_kpa);
    f.fog_gate_rh     = fog_wanted && (in.rh_pct  > sp.fog_rh_ceiling);
    f.fog_gate_temp   = fog_wanted && (in.temp_f  < sp.fog_min_temp);
    f.fog_gate_window = fog_wanted
        && !fog_hour_in_window(in.local_hour, sp.fog_window_start, sp.fog_window_end);

    // Relief-cycle breaker: firmware forces VENTILATE instead of the seal
    // the planner's dwell was setting up.
    f.relief_cycle_breaker =
        vpd_wants_seal && state.relief_cycle_count >= sp.max_relief_cycles;

    // Seal blocked by temp: within safety_max_seal_margin_f of safety_max
    // means the firmware refuses to close the vents for VPD misting.
    f.seal_blocked_temp =
        vpd_wants_seal && in.temp_f >= (sp.safety_max - sp.safety_max_seal_margin_f);

    // VPD dry override: read the flag determine_mode() sets in its R2-3
    // path. Cannot be reconstructed post-hoc because R2-3 matures the
    // dwell timer in the same cycle it fires, so `!vpd_wants_seal` is
    // false by the time evaluate_overrides() sees state.
    f.vpd_dry_override = state.dry_override_active;

    // Sprint-15: summer-vent gate active. Set by determine_mode() when the
    // outdoor-cooler-and-drier comparator pre-empted a VPD-seal entry.
    // Same reason as vpd_dry_override above: the gate consumes
    // vpd_wants_seal in the same cycle, so reconstruction post-hoc would
    // miss the firing.
    f.summer_vent_active = state.override_summer_vent;
    f.vent_mist_assist = state.vent_mist_assist_active;

    // band-first controller cold/dry assist: in SEALED_MIST_FOG, fog may run while
    // heat holds the temp band. Recompute the resolve_equipment() intent so
    // active_overrides makes the overlap explicit without mutating state.
    const float temp_band_width = std::max(2.0f, sp.temp_high - sp.temp_low);
    const float heat_target = sp.sw_fsm_controller_enabled
        ? band_heat_target_f(sp)
        : sp.temp_low + temp_band_width * 0.25f + sp.bias_heat;
    const bool heat_suppressed_by_upper_band = in.temp_f >= sp.temp_high;
    const bool heat1_would_run =
        !heat_suppressed_by_upper_band
        && in.temp_f < (heat_target + sp.heat_hysteresis);
    const bool heat2_would_run = !heat_suppressed_by_upper_band && state.heat2_latched;
    const bool fog_would_run =
        (mode == SEALED_MIST)
        && (state.mist_stage == MIST_FOG)
        && fog_permitted(in, sp)
        && !moisture_blocked;
    f.fog_heat_assist = fog_would_run && (heat1_would_run || heat2_would_run);

    (void)HV;  // reserved for future hysteresis-sensitive gates
    return f;
}

// ═══════════════════════════════════════════════════════════════════
// resolve_equipment() — Pure function. No side effects.
// ═══════════════════════════════════════════════════════════════════
inline RelayOutputs resolve_equipment(
    Mode mode,
    const SensorInputs& in,
    const Setpoints& sp,
    const ControlState& state,
    bool lead_is_fan1
) {
    // Sprint-12 legacy: interior targets (25% inside band). band-first controller
    // uses the same lower-quartile heat target while cooling at the raw high
    // edge; heat2 still protects the lower edge.
    const float band_width = std::max(2.0f, sp.temp_high - sp.temp_low);
    const float Tlow  = sp.sw_fsm_controller_enabled
        ? band_heat_target_f(sp)
        : sp.temp_low + band_width * 0.25f + sp.bias_heat;
    const float Thigh = sp.sw_fsm_controller_enabled
        ? sp.temp_high
        : sp.temp_high - band_width * 0.25f + sp.bias_cool;

    const float vpd_width    = std::max(0.2f, sp.vpd_high - sp.vpd_low);
    const float vpd_low_eff  = sp.vpd_low  + vpd_width * 0.25f;
    const float vpd_high_eff = sp.vpd_high - vpd_width * 0.25f;

    const bool heat_suppressed_by_upper_band = in.temp_f >= sp.temp_high;
    bool needs_heating_s1 = !heat_suppressed_by_upper_band
                          && in.temp_f < (Tlow + sp.heat_hysteresis);
    // Sprint-9 P1#7: S2 is latched (see determine_mode). Reading the latch
    // instead of recomputing the threshold gives us the hysteresis band.
    bool needs_heating_s2 = !heat_suppressed_by_upper_band && state.heat2_latched;

    RelayOutputs out = {false, false, false, false, false, false};

    switch (mode) {
        case SENSOR_FAULT:
            // R2-1: ALL relays off. No actuator should run without sensor feedback.
            // Freeze protection: hardware thermostat wired in parallel.
            break;

        case SAFETY_COOL:
            out.vent = true;
            out.fan1 = true; out.fan2 = true;
            out.fog = fog_permitted(in, sp)
                   && !moisture_blocked_by_occupancy(in, sp)
                   && in.vpd_kpa > vpd_high_eff;
            break;

        case SAFETY_HEAT:
            out.heat1 = true; out.heat2 = true;
            // Sprint-9 P2#11: run the lead fan for canopy circulation.
            // Without it, cold air pockets near the temp probe hold the
            // safety condition indefinitely while the burner runs wide
            // open near the probe. Vent stays closed (keep heat in).
            // Violates the "no fan without vent" invariant by design —
            // test `no_fan_without_vent` whitelists SAFETY_HEAT.
            if (lead_is_fan1) out.fan1 = true; else out.fan2 = true;
            break;

        case SEALED_MIST:
            if (needs_heating_s2) { out.heat1 = true; out.heat2 = true; }
            else if (needs_heating_s1) { out.heat1 = true; }
            out.fog = (state.mist_stage == MIST_FOG)
                   && fog_permitted(in, sp)
                   && !moisture_blocked_by_occupancy(in, sp);
            break;

        case THERMAL_RELIEF:
            // Sprint-10 0.2: both fans. If we've fallen into thermal relief,
            // the greenhouse is past the "gentle purge" regime — the seal
            // max-timer just fired or temp is near safety. Running a single
            // fan leaves half the capacity on the table when purge needs to
            // move the most air possible.
            out.vent = true;
            out.fan1 = true; out.fan2 = true;
            break;

        case VENTILATE: {
            out.vent = true;
            const float stage2_delta = sp.sw_fsm_controller_enabled
                ? band_cool_stage2_delta_f(sp)
                : sp.dC2;
            bool needs_both = in.temp_f > (Thigh + stage2_delta);
            if (lead_is_fan1) { out.fan1 = true; out.fan2 = needs_both; }
            else              { out.fan2 = true; out.fan1 = needs_both; }
            // FW-9b (PR-A lowered): fire fog concurrently with vent when VPD
            // is above band. Original FW-9b only fired at vpd > vpd_max_safe
            // (3.0 kPa — safety territory); data from 2026-04-16..23 showed
            // 653 min (38% of VENTILATE time) had VPD above band with fog off.
            // New trigger matches SEAL path's fog-stage threshold for symmetry:
            //     vpd > vpd_high_eff + fog_escalation_kpa  (~2.2 kPa default)
            // Fog is surfaced in the pure relay table. Zone mister pulses are
            // handled in controls.yaml through vent_mist_assist_active, which
            // bypasses the normal closed-vent mister interlock only for this
            // explicit VENTILATE assist path.
            if (in.vpd_kpa > (vpd_high_eff + sp.fog_escalation_kpa) && !moisture_blocked_by_occupancy(in, sp)) {
                out.fog = fog_permitted(in, sp);
            }
            break;
        }

        case DEHUM_VENT:
            out.vent = true;
            // Aggressive dehum (both fans) kicks in if vpd is below the
            // INTERIOR target minus the aggressive margin — keeps the
            // trigger consistent with the rest of the interior-targeting
            // logic. dehum_aggressive_kpa remains the margin from the
            // (now interior) target at which we open both fans.
            if (in.vpd_kpa < vpd_low_eff - sp.dehum_aggressive_kpa) {
                out.fan1 = true; out.fan2 = true;
            } else {
                if (lead_is_fan1) out.fan1 = true; else out.fan2 = true;
            }
            break;

        case IDLE:
            if (needs_heating_s2) { out.heat1 = true; out.heat2 = true; }
            else if (needs_heating_s1) { out.heat1 = true; }
            // Econ-block VPD rescue: electric heat if VPD is below the
            // interior target AND temp is below the interior cooling
            // target minus econ_heat_margin_f. Same semantics as before,
            // retargeted to the band interior.
            if (in.vpd_kpa < vpd_low_eff && sp.econ_block
                && in.temp_f < Thigh - sp.econ_heat_margin_f) {
                out.heat1 = true;
            }
            break;

        default:
            // Corrupted mode — all off (same as SENSOR_FAULT)
            break;
    }

    if (air_blocked_by_occupancy(in, sp, mode)) {
        out.fan1 = false;
        out.fan2 = false;
        out.fog = false;
    }

    return out;
}
