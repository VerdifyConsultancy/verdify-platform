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
#include "greenhouse_solar.h"

// ── Firmware-v2 solar accessors ─────────────────────────────────────────
// Every time-of-day rule keys on SOLAR PHASE (greenhouse_solar.h), computed
// on-chip — never on wall-clock hours. Callers populate SensorInputs with the
// per-cycle ephemeris; tests/replay derive it from row timestamps. Defaults
// degrade gracefully (fallback phase from local_hour).
inline int effective_now_minute(const SensorInputs& in) noexcept {
    if (in.now_minute >= 0) return ((in.now_minute % 1440) + 1440) % 1440;
    return std::max(0, std::min(23, in.local_hour)) * 60;
}

inline float effective_solar_phase(const SensorInputs& in) noexcept {
    if (std::isfinite(in.solar_phase)) {
        float p = in.solar_phase;
        return (p >= 0.0f && p < 4.0f) ? p : fallback_solar_phase(in.local_hour);
    }
    return fallback_solar_phase(in.local_hour);
}

// Night = the solar night half (sunset → next sunrise).
inline bool is_night_phase(const SensorInputs& in) noexcept {
    return effective_solar_phase(in) >= 2.0f;
}

inline int effective_minutes_to_sunset(const SensorInputs& in) noexcept {
    const SolarTimes st = {in.sunrise_min, in.solar_noon_min, in.sunset_min};
    return minutes_to_sunset(effective_now_minute(in), st);
}

// ── R2-4: Plausibility validation — catches NaN, inf, and garbage ──
inline bool sensors_plausible(const SensorInputs& in) noexcept {
    return std::isfinite(in.temp_f)  && in.temp_f  > -20.0f && in.temp_f  < 140.0f
        && std::isfinite(in.rh_pct)  && in.rh_pct  >= 0.0f  && in.rh_pct  <= 100.0f
        && std::isfinite(in.vpd_kpa) && in.vpd_kpa >= 0.0f  && in.vpd_kpa < 10.0f
        && in.local_hour >= 0        && in.local_hour <= 23;
}

// ── SAF-1 / SF1: VPD-control trust gate ────────────────────────────────
// VPD/RH control (humidify, dehum, fog-for-humidity, the VPD over-saturation
// burst gate) is only trustworthy when the indoor average RH/VPD probes are
// live. When in.sensor_degraded is set the VPD reading is fabricated from a
// fallback probe, so every VPD-CHASING path is suppressed and the controller
// falls back to conservative TIMED wetting + temp-only control. Temperature
// control (safety rails, vent cooling, heat) is NOT gated — the case probe
// still yields a plausible temp.
inline bool vpd_control_trusted(const SensorInputs& in) noexcept {
    return !in.sensor_degraded;
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

inline float dew_margin_f(const SensorInputs& in) noexcept {
    if (!std::isfinite(in.temp_f) || !std::isfinite(in.dew_point_f)) return -999.0f;
    return in.temp_f - in.dew_point_f;
}

// Forward decl: the moisture-exchange estimator's humid-import dew guard uses this
// (defined just below, after the psychrometrics block).
inline float min_dew_margin_for_wetting(const SensorInputs& in, const Setpoints& sp) noexcept;

// ── Psychrometrics (pure; °F/RH% in, kPa/g·kg⁻¹ out). Single-precision, ESP32-FPU-safe.
// CRITICAL: the Magnus b-constant here is 237.3 — it MUST match the device's
// authoritative VPD `es` (controls.yaml:167 `0.6108*expf(17.27*tc/(tc+237.3))`).
// Do NOT "correct" it to the 237.7 used by the separate dewpoint INVERSION
// (controls.yaml:458) — that would silently shift every VPD-gain threshold here.
inline float sat_vapor_pressure_kpa(float temp_f) noexcept {
    const float Tc = (temp_f - 32.0f) * (5.0f / 9.0f);
    return 0.6108f * std::exp((17.27f * Tc) / (Tc + 237.3f));   // kPa
}
inline float vapor_pressure_kpa(float temp_f, float rh_pct) noexcept {
    const float rh = std::max(0.0f, std::min(100.0f, rh_pct));
    return sat_vapor_pressure_kpa(temp_f) * (rh * 0.01f);
}
// Station pressure at Longmont (~1500 m ≈ 85 kPa). The estimator decides on
// vapor-pressure VPD (pressure-independent) and on the SIGN of w_out−w_in (where
// P cancels), so this constant does not bias the decision — it is named/correct
// for any future absolute-mixing-ratio telemetry use.
static constexpr float GH_STATION_PRESSURE_KPA = 85.0f;
inline float mixing_ratio_g_kg(float temp_f, float rh_pct) noexcept {
    const float e = vapor_pressure_kpa(temp_f, rh_pct);
    return 621.97f * e / std::max(1e-3f, (GH_STATION_PRESSURE_KPA - e));
}
// VPD at a hypothetical (temp, conserved-vapor) state: sensible-only heating adds
// no moisture, so VPD' = es(T') − e_current.
inline float vpd_at_state_kpa(float temp_f, float vapor_e_kpa) noexcept {
    return std::max(0.0f, sat_vapor_pressure_kpa(temp_f) - vapor_e_kpa);
}
// Per-cycle temperature rise the gas heater realistically delivers — a PROBE for
// the heat candidate's VPD gain. Named (NOT heat_hysteresis, which is a band not a
// rate — using it would understate heat and mis-select vent on marginal cold rows).
static constexpr float GH_HEAT_ASSIST_PROBE_DF = 1.5f;

// ── ADR0003 §6.4 moisture-exchange selector ─────────────────────────────────
// Deterministic per-cycle estimate of which action moves VPD toward target.
enum MoistureExchangeAction { MX_NONE, MX_VENT_DEHUM, MX_HEAT_ASSIST, MX_VENT_HUMIDIFY };
struct MoistureExchangeEstimate {
    MoistureExchangeAction action;
    bool   heat_assist_corun;   // heat1+vent both proven to move VPD toward target
    float  vent_vpd_gain_kpa;   // signed, + = toward target
    float  heat_vpd_gain_kpa;
    bool   outdoor_fresh;
    bool   vent_overcools;
    const char* reason;
};

inline const char* moisture_exchange_action_name(MoistureExchangeAction action) noexcept {
    switch (action) {
        case MX_VENT_DEHUM: return "vent_dehum";
        case MX_HEAT_ASSIST: return "heat_assist";
        case MX_VENT_HUMIDIFY: return "vent_humidify";
        case MX_NONE:
        default: return "none";
    }
}

inline MoistureExchangeEstimate estimate_moisture_exchange(
    const SensorInputs& in, const Setpoints& sp) noexcept {
    MoistureExchangeEstimate r{MX_NONE, false, 0.0f, 0.0f, false, false, "in_band"};
    if (!vpd_control_trusted(in)) { r.reason = "vpd_untrusted"; return r; }  // SAF-1/SF1
    const bool fresh = std::isfinite(in.outdoor_temp_f) && std::isfinite(in.outdoor_rh_pct)
                     && in.outdoor_data_age_s < sp.outdoor_staleness_max_s;
    r.outdoor_fresh = fresh;
    const float e_in   = vapor_pressure_kpa(in.temp_f, in.rh_pct);
    // Use the RH-consistent current VPD as the gain BASELINE (es(T) − e). On-device
    // this equals in.vpd_kpa (both come from the same temp+RH reading); using it keeps
    // the gain projections self-consistent with the candidate VPDs (also computed from
    // es/e). DIRECTION (too-wet vs too-dry) still uses the authoritative sensor in.vpd_kpa.
    const float vpd_now = std::max(0.0f, sat_vapor_pressure_kpa(in.temp_f) - e_in);
    const float margin = sp.dehum_vent_gain_margin_kpa;
    const float k = sp.vent_exchange_fraction;   // AI-tunable single-cycle exchange fraction

    // ── too WET (VPD below target): dehum direction ──
    if (in.vpd_kpa < sp.vpd_target) {
        // heat candidate: sensible warm by the named probe step (vapor conserved).
        r.heat_vpd_gain_kpa = vpd_at_state_kpa(in.temp_f + GH_HEAT_ASSIST_PROBE_DF, e_in) - vpd_now;
        bool vent_overcools = false;
        if (fresh) {  // vent candidate: exchange toward outdoor (only helps if outdoor drier in abs)
            const float e_out = vapor_pressure_kpa(in.outdoor_temp_f, in.outdoor_rh_pct);
            const float e_mix = e_in + k * (e_out - e_in);
            const float T_mix = in.temp_f + k * (in.outdoor_temp_f - in.temp_f);
            r.vent_vpd_gain_kpa = vpd_at_state_kpa(T_mix, e_mix) - vpd_now;
            // Venting frigid air to dehumidify OVER-COOLS the house below the band
            // floor — counterproductive (you then need heat to recover) and it thrashes
            // the vent on a continuously-cold day (invariant #14). When venting would
            // drop below temp_low, prefer the HEAT candidate (which dries AND warms) —
            // physics, NOT a cost-brake (heat-assist still dehumidifies, regime goal met).
            vent_overcools = T_mix < sp.temp_low;
        }
        r.vent_overcools = vent_overcools;
        const bool vent_helps = fresh && r.vent_vpd_gain_kpa > margin && !vent_overcools;
        const bool heat_helps = r.heat_vpd_gain_kpa > margin;
        if (vent_helps && heat_helps) { r.action = MX_VENT_DEHUM; r.heat_assist_corun = true; r.reason = "vent_plus_heat"; }
        else if (vent_helps && r.vent_vpd_gain_kpa >= r.heat_vpd_gain_kpa) { r.action = MX_VENT_DEHUM; r.reason = "vent_dehum"; }
        else if (heat_helps) { r.action = MX_HEAT_ASSIST; r.reason = "heat_assist"; }
        else if (vent_helps) { r.action = MX_VENT_DEHUM; r.reason = "vent_dehum"; }
        else { r.reason = "no_effective_action"; }
        return r;
    }

    // ── too DRY (VPD above target) AND outdoor MORE humid: import moisture by vent ──
    // Jason 2026-06-17: cap at target (no overshoot) + dew-margin guard (no condensation).
    if (in.vpd_kpa > sp.vpd_target && fresh) {
        const float w_in  = mixing_ratio_g_kg(in.temp_f, in.rh_pct);
        const float w_out = mixing_ratio_g_kg(in.outdoor_temp_f, in.outdoor_rh_pct);
        const bool dew_safe = dew_margin_f(in) >= min_dew_margin_for_wetting(in, sp);
        // Don't import air much colder than indoor: it over-cools the house (fogging
        // is the better humidifier on a cold day) and its row-to-row marginal w_out>w_in
        // flips thrash the vent (invariant #14). A physics sanity limit on the IMPORT
        // direction only — NOT a cost-brake on dehum (the too-wet dehum still runs cold).
        const bool not_too_cold = in.outdoor_temp_f >= (in.temp_f - sp.cold_vent_guard_delta_f);
        if (w_out > w_in && dew_safe && not_too_cold) {
            const float e_out = vapor_pressure_kpa(in.outdoor_temp_f, in.outdoor_rh_pct);
            const float e_mix = e_in + k * (e_out - e_in);
            const float T_mix = in.temp_f + k * (in.outdoor_temp_f - in.temp_f);
            r.vent_vpd_gain_kpa = vpd_now - vpd_at_state_kpa(T_mix, e_mix);  // + = VPD drops toward target
            if (r.vent_vpd_gain_kpa > margin) { r.action = MX_VENT_HUMIDIFY; r.reason = "vent_humidify"; }
        }
    }
    return r;
}

// BC-7 (band-compliance): the dew-margin veto that blocks wetting/fog to prevent
// condensation is STRICTER AT NIGHT. After dark there is no solar load, so leaf
// surfaces sit closest to the dewpoint and a wider temp-dewpoint spread is
// required before any wetting is allowed. Day uses direct_wet_stress_min_dew_margin_f;
// night requires at least night_stress_min_dew_margin_f (defaulted >= the day
// margin) — never looser than day. One generic helper, all wetting gates share it.
inline float min_dew_margin_for_wetting(const SensorInputs& in, const Setpoints& sp) noexcept {
    return is_night_phase(in)
        ? std::max(sp.direct_wet_stress_min_dew_margin_f, sp.night_stress_min_dew_margin_f)
        : sp.direct_wet_stress_min_dew_margin_f;
}

// ── Firmware-v2: night econ-heat rule (generic, solar-phase) ────────────
// Never heat to chase humidity at night. The econ-rescue heat
// (resolve_equipment IDLE: fires heat1 when vpd < vpd_low_eff && econ_block)
// is suppressed for the whole solar night half. Safety heat untouched.
inline bool night_econ_heat_suppressed(const SensorInputs& in, const Setpoints& sp) noexcept {
    return sp.sw_night_econ_heat_suppress_enabled && is_night_phase(in);
}

// ── Firmware-v2: wet taper (replaces the dusk_cutoff_hour clock rail) ────
// Routine (non-stress) wetting ceases wet_taper_before_sunset_min before the
// ON-CHIP sunset and stays off through the night half, so foliage dries
// before dark on every day of the year with zero network. Stress wetting has
// its own gate below.
// CURVE-ONLY (gate removal): the dusk/night wet-taper INTENT-gate is gone. The
// band curve is the schedule — a properly-shaped night band (high vpd_high) keeps
// nights dry by NOT tripping the wet trigger, and only wets a genuinely too-dry
// night. No-op shim retained so consumers compile until the field/switch cleanup.
inline bool past_wet_taper(const SensorInputs&, const Setpoints&) noexcept {
    return false;
}

// ── Firmware-v2: ONE stress-wetting rule (replaces direct_wet_stress_* +
// fog_stress_window_* clock windows AND the CYC-4 overnight micro-pulse).
// Stress = VPD above the solar band's high edge by the margin. By day the
// rule may extend wetting through the taper; at night it is the emergency
// path (the night band edge is LOW, so exceeding it + margin is a genuine
// spike) under a stricter dew margin. The normal SEALED_MIST machinery,
// SAF-4 duty caps, daily-volume ceiling, and relay min-on/off bound the
// actual water — no bespoke pulse path exists anymore.
// CURVE-ONLY: the stress-override INTENT-gate is gone — there is no separate
// "re-open wetting" path. Wetting follows VPD vs the band curve directly (see
// climate_wet_assist_block_reason), physics-vetoed only. No-op shim.
inline bool stress_wet_override_permitted(const SensorInputs&, const Setpoints&) noexcept {
    return false;
}

// Back-compat alias (tests/controls reference the historical name).
inline bool direct_wet_stress_override_permitted(const SensorInputs& in, const Setpoints& sp) noexcept {
    return stress_wet_override_permitted(in, sp);
}

// CURVE-ONLY wet-assist gate: wet when actual VPD is above the band's high edge
// (the curve), vetoed only by STATE (occupancy, fertigation feed-hold) and
// PHYSICS (condensation dew margin). No taper, no clock window, no override
// switch, no extra threshold-margin — the band curve + its hysteresis are the
// schedule and the anti-chatter.
inline const char* climate_wet_assist_block_reason(const SensorInputs& in, const Setpoints& sp) noexcept {
    if (moisture_blocked_by_occupancy(in, sp)) return "occupancy";          // state
    if (sp.feed_hold_active) return "feed_hold";                            // state: FRT-6 absorption hold
    if (in.vpd_kpa <= sp.vpd_high) return "below_threshold";               // curve: only above the band high edge
    if (dew_margin_f(in) < min_dew_margin_for_wetting(in, sp)) return "dew_margin";  // physics: condensation
    return "";
}

inline bool climate_wet_assist_permitted(const SensorInputs& in, const Setpoints& sp) noexcept {
    return climate_wet_assist_block_reason(in, sp)[0] == '\0';
}

// CURVE-ONLY: no phase/taper/clock gate on fog. Fog is decided by the band curve
// (VPD above the high edge) and the PHYSICS vetoes (dew margin, RH saturation,
// fog_min_temp, survival) in climate_fog_assist_block_reason. No-op shim.
inline bool fog_phase_permitted(const SensorInputs&, const Setpoints&) noexcept {
    return true;
}

// Back-compat alias (historical name used by controls/tests).
inline bool fog_hour_permitted(const SensorInputs& in, const Setpoints& sp) noexcept {
    return fog_phase_permitted(in, sp);
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

// ── FRT-8 / F3 (AM-only feed window) ───────────────────────────────────
// Returns true iff `hour` is inside the morning feed window [start, end).
// This is the authoritative, VPD-INDEPENDENT rail that gates every
// fertilizer job (scheduled fert states 2/4/7/8 AND the manual fert
// buttons). Bare-root Vanda must be fed once per day in the morning so the
// velamen has the full daylight period to absorb and the surfaces dry well
// before the dusk cutoff; an afternoon/dusk/overnight feed leaves salts on
// wet roots into the night (rot/burn risk). Wrap-aware like
// fog_hour_in_window: when start <= end the window is the simple [start,
// end); a start > end would cross midnight (degenerate for a morning feed,
// but handled for safety). A degenerate start == end is treated as "always
// closed" so a misconfigured pair fails SAFE (no feed) rather than open.
inline bool feed_window_open(int hour, int feed_start_hour, int feed_end_hour) noexcept {
    hour = std::max(0, std::min(23, hour));
    feed_start_hour = std::max(0, std::min(23, feed_start_hour));
    feed_end_hour   = std::max(0, std::min(24, feed_end_hour));
    if (feed_start_hour == feed_end_hour) return false;  // degenerate → fail closed
    return (feed_start_hour < feed_end_hour)
        ? (hour >= feed_start_hour && hour < feed_end_hour)
        : (hour >= feed_start_hour || hour < feed_end_hour);
}

// True iff all of RH, temp, and hour-of-day permit fogging. Occupancy is
// NOT checked here — see moisture_blocked_by_occupancy().
inline bool fog_permitted(const SensorInputs& in, const Setpoints& sp) noexcept {
    return (in.rh_pct  <= sp.fog_rh_ceiling)   // physics: don't fog saturated air
        && (in.temp_f  >= sp.fog_min_temp);    // physics: warm enough to evaporate
}

inline const char* climate_fog_assist_block_reason(const SensorInputs& in, const Setpoints& sp) noexcept {
    if (moisture_blocked_by_occupancy(in, sp)) return "occupancy";
    // SAF-6: at/above the safety_max rail, evaporative fog is a survival
    // cooling aid — neither the absorption hold nor the wet taper may
    // suppress it. (SAFETY_COOL in resolve_equipment calls this gate.)
    const bool safety_cool_active = in.temp_f >= sp.safety_max;
    // FRT-6: fogger is clean-water-only; block it during the absorption hold
    // so a feed is not immediately rinsed/diluted at the canopy.
    if (!safety_cool_active && sp.feed_hold_active) return "feed_hold";
    if (in.vpd_kpa <= sp.vpd_high) return "below_threshold";  // curve: only above the band high edge
    if (dew_margin_f(in) < min_dew_margin_for_wetting(in, sp)) return "dew_margin";  // physics
    // CURVE-ONLY: the solar taper / night phase gate is removed — fog follows the
    // band curve, not a dry-down clock. Only physics rails below remain.
    if (in.rh_pct > sp.fog_rh_ceiling) return "rh_ceiling";
    if (in.temp_f < sp.fog_min_temp) return "temp_low";
    return "";
}

inline bool climate_fog_assist_permitted(const SensorInputs& in, const Setpoints& sp) noexcept {
    return climate_fog_assist_block_reason(in, sp)[0] == '\0';
}

// ── IRR-3 (dawn rehydrate) / IRR-4 (midday drench) ─────────────────────
// Two time-anchored CENTER-zone mist cadence overrides for the bare-root
// Vanda. These do NOT pick a mode or fire a relay; they only report whether
// the current minute is inside a burst window AND every rail still permits
// CENTER wetting, so controls.yaml can swap the center pulse ON/GAP for the
// denser dawn/midday cadence. West/south/east are never consulted here.
//
// RAILS (each is a hard precondition — a burst can fire only when ALL hold):
//   * master enable switch (sw_dawn_rehydrate_enabled / sw_midday_drench_enabled)
//   * sensors plausible (SENSOR_FAULT ⇒ no burst)
//   * NOT during the FRT-6 post-feed absorption hold (feed_hold_active)
//   * NOT at/after the CYC-1 dusk cutoff (past_dusk_cutoff)
//   * NOT occupancy-inhibited
//   * dew margin >= the center wet-assist dew floor (don't wet onto a cold leaf)
//   * VPD STRICTLY above the center target proxy (vpd_high): the over-saturation
//     sanity gate — if the air is already at/below the center band the velamen
//     is humid enough and we do not drench.
// The SAF-4 duty cap + mister_daily_volume_max_gal ceiling are enforced in
// controls.yaml (cumulative-runtime/volume state lives there); a burst counts
// toward and is bounded by them — it can never exceed the cap.
//
// Note: these mirror climate_wet_assist_block_reason()'s rails but use a
// STRICT VPD-above-target test (not the +margin stress test) because a dawn
// rehydrate intentionally engages at a lower stress threshold than the dry-
// stress override — the point is to pre-empt the dry-down, not chase an
// emergency. The dusk/feed-hold/dew/occupancy rails are identical.
inline int dawn_rehydrate_window_minutes(const Setpoints& sp) noexcept {
    return clamp_day_minutes(sp.dawn_rehydrate_window_min);
}
inline int midday_drench_window_minutes(const Setpoints& sp) noexcept {
    return clamp_day_minutes(sp.midday_drench_window_min);
}

// Firmware-v2: the boost windows anchor to the ON-CHIP solar times so they
// track the sun all year. Dawn window opens sunrise + dawn_boost_offset_min;
// midday window opens solar-noon + midday_boost_offset_min.
inline bool in_dawn_rehydrate_window(int now_minute, const SensorInputs& in, const Setpoints& sp) noexcept {
    const int start = (((in.sunrise_min + sp.dawn_boost_offset_min) % 1440) + 1440) % 1440;
    return minute_in_window(now_minute, start, dawn_rehydrate_window_minutes(sp));
}
inline bool in_midday_drench_window(int now_minute, const SensorInputs& in, const Setpoints& sp) noexcept {
    const int start = (((in.solar_noon_min + sp.midday_boost_offset_min) % 1440) + 1440) % 1440;
    return minute_in_window(now_minute, start, midday_drench_window_minutes(sp));
}

// The shared per-cycle rail gate for either burst (everything except the
// window membership + per-burst enable). Center-zone only.
inline bool center_burst_rails_permit(const SensorInputs& in, const Setpoints& sp) noexcept {
    if (!sensors_plausible(in)) return false;
    if (moisture_blocked_by_occupancy(in, sp)) return false;
    if (sp.feed_hold_active) return false;        // FRT-6 absorption hold
    if (past_wet_taper(in, sp)) return false;     // solar taper (boosts are routine wetting)
    if (dew_margin_f(in) < min_dew_margin_for_wetting(in, sp)) return false;
    // Over-saturation sanity gate: only burst when the air is drier than the
    // center band ceiling. If VPD <= vpd_high the canopy is already humid.
    // SAF-1 / SF1: when the VPD reading is degraded (fabricated) this gate is
    // BYPASSED — the whole point of the degraded fallback is conservative
    // *timed* wetting that does NOT chase VPD. The dawn/midday windows + every
    // other rail (dusk, feed-hold, dew margin, occupancy, plausibility) plus
    // the controls.yaml SAF-4 duty cap / daily-volume ceiling still bound it,
    // so this stays safe; it just no longer requires a trusted dry reading to
    // run the calibrated morning/midday drench.
    if (vpd_control_trusted(in) && in.vpd_kpa <= sp.vpd_high) return false;
    return true;
}

// Decide which CENTER-zone burst (if any) is active for the supplied minute.
// `now_minute` is minutes-from-local-midnight (the caller derives it from SNTP,
// matching the climate wet-assist gate). Dawn takes precedence if both windows
// somehow overlap (a misconfiguration the clamps guard against).
inline CenterBurst center_burst_decision(int now_minute, const SensorInputs& in, const Setpoints& sp) noexcept {
    if (!center_burst_rails_permit(in, sp)) return CENTER_BURST_NONE;
    if (sp.sw_dawn_rehydrate_enabled && in_dawn_rehydrate_window(now_minute, in, sp)) {
        return CENTER_BURST_DAWN;
    }
    if (sp.sw_midday_drench_enabled && in_midday_drench_window(now_minute, in, sp)) {
        return CENTER_BURST_MIDDAY;
    }
    return CENTER_BURST_NONE;
}

// Center pulse ON / GAP (seconds) for the active burst. Returns false when no
// burst is active (caller keeps the base mister_pulse_on_s / mister_pulse_gap_s).
inline bool center_burst_cadence_s(CenterBurst burst, const Setpoints& sp, int& on_s, int& gap_s) noexcept {
    switch (burst) {
        case CENTER_BURST_DAWN:
            on_s = sp.dawn_rehydrate_on_s;  gap_s = sp.dawn_rehydrate_gap_s;  return true;
        case CENTER_BURST_MIDDAY:
            on_s = sp.midday_drench_on_s;   gap_s = sp.midday_drench_gap_s;   return true;
        case CENTER_BURST_NONE:
        default:
            return false;
    }
}

// (Firmware-v2: the CYC-4 overnight micro-pulse path is DELETED. Night
// emergencies are governed by the solar band itself — VPD above the LOW night
// band edge + stress margin fires the normal stress-wet rule under the night
// dew margin, and the standard SEALED_MIST machinery + SAF-4 duty caps +
// relay min-on/off bound the water. One wetting path, no bespoke timers.)

// Unified band-first controller clamps VPD hysteresis against the actual band width. The
// legacy cascade allows hyst_vpd_kpa=0.4 with a 0.8-1.2 band, which makes
// SEALED_MIST exit only below 0.7 kPa. That turns normal high-VPD periods
// into timeout/backoff loops instead of band compliance.
// Cap VPD hysteresis at this fraction of the band width so a wide hysteresis
// can't push the SEALED_MIST exit below a narrow band (structural, not tunable).
static constexpr float VPD_HYST_BAND_FRACTION = 0.33f;
inline float band_vpd_hysteresis(const Setpoints& sp) noexcept {
    const float vpd_width = std::max(0.2f, sp.vpd_high - sp.vpd_low);
    const float requested = std::max(0.05f, sp.vpd_hysteresis);
    const float cap = std::max(0.05f, vpd_width * VPD_HYST_BAND_FRACTION);
    return std::min(requested, cap);
}

// ADR-0004 floating corridor: the normal value is 0, which leaves the served
// crop corridor unchanged and lets the controller act only at the edges. Nonzero
// values are an explicit operator/diagnostic escape hatch that pinches the
// CONTROL band toward temp_target/vpd_target. Safety rails and the served band
// are untouched.
//
// Pinch geometry: the low edge moves up by f·(target−low) and the high edge down
// by f·(high−target), so the pinched width = served_width·(1−f) regardless of
// where the target sits between the edges.
//
// DEMAND-OVERLAP FLOOR (critic must-fix): the heat-stage-1 trigger is
// band_heat_target_f + heat_hysteresis; the cooling trigger is temp_high. If the
// pinched temp band narrows so far that band_heat_target_f(pinched)+heat_hysteresis
// meets/exceeds the pinched temp_high, the heater and the vent/fans get demanded at
// the SAME temperature → thrash. band_heat_target_f = low + 0.25·max(2,W), so the
// smallest safe width is heat_hysteresis/0.75 (when ≥2) else 0.5+heat_hysteresis.
// We cap the EFFECTIVE temp fraction so pinched_temp_width ≥ that floor (and a small
// positive floor on VPD so the relative vpd-hysteresis cap stays sane). If the
// served band is already narrower than the floor, the axis simply does not pinch.
inline Setpoints apply_band_track_pinch(const Setpoints& sp) noexcept {
    const float f = std::max(0.0f, std::min(1.0f, sp.band_track_fraction));
    if (f <= 0.0f) return sp;

    const float temp_w = std::max(0.01f, sp.temp_high - sp.temp_low);
    const float vpd_w  = std::max(0.01f, sp.vpd_high  - sp.vpd_low);
    const float hyst   = std::max(0.0f, sp.heat_hysteresis);
    const float min_temp_w = (hyst / 0.75f >= 2.0f) ? (hyst / 0.75f) : (0.5f + hyst);
    const float min_vpd_w  = 0.20f;
    // pinched_width = width·(1−f_eff); keep it ≥ floor → f_eff ≤ 1 − floor/width.
    const float f_temp = std::min(f, std::max(0.0f, 1.0f - min_temp_w / temp_w));
    const float f_vpd  = std::min(f, std::max(0.0f, 1.0f - min_vpd_w  / vpd_w));

    Setpoints out = sp;
    out.temp_low  = sp.temp_low  + f_temp * (sp.temp_target - sp.temp_low);
    out.temp_high = sp.temp_high - f_temp * (sp.temp_high   - sp.temp_target);
    out.vpd_low   = sp.vpd_low   + f_vpd  * (sp.vpd_target  - sp.vpd_low);
    out.vpd_high  = sp.vpd_high  - f_vpd  * (sp.vpd_high    - sp.vpd_target);
    return out;
}

// Unified band-first controller uses the crop/planner band itself as the temperature contract.
// Heat1 protects the lower quartile; heat2 protects the lower edge. The older
// d_heat_stage_2 margin is left to the legacy cascade and should not allow this path
// to sit several degrees below band before gas heat joins.
static constexpr float BAND_HEAT_TARGET_FRACTION = 0.25f;
// Cold-regime SAFETY floors (F6, issue 5): named, NOT per-crop tunables. When
// outdoor air is cold enough to risk vent over-cooling, the vent cooling-exit
// hysteresis is held at least this wide (anti-chatter / vent-motor protection),
// and cold-dehum is only allowed once temp is at least this far above the band
// floor. They FLOOR (never lower) the operator tunables in the cold-vent regime;
// making them planner-pushable would over-parameterize a mechanical-protection
// minimum. Previously bare 3.0f / 2.0f literals — now traceable.
static constexpr float COLD_VENT_COOL_EXIT_HYST_MIN_F = 3.0f;
static constexpr float COLD_DEHUM_TEMP_MARGIN_MIN_F = 2.0f;
// BC-14 (ADR0003 §6.6): fog->mister de-escalation hysteresis. Misters drop back to
// fog-only when VPD recovers to (vpd_high + fog_escalation_kpa - this), giving a
// symmetric non-chattering gap (WS-B style) at the fog/mister boundary. Named
// mechanical anti-chatter minimum (not a tunable); the fog-only DWELL is the tunable.
static constexpr float MISTER_DEESCALATE_KPA = 0.15f;
inline float band_heat_target_f(const Setpoints& sp) noexcept {
    const float band_width = std::max(2.0f, sp.temp_high - sp.temp_low);
    return sp.temp_low + band_width * BAND_HEAT_TARGET_FRACTION;
}

// Unified band-first controller normally cools at the raw upper band edge.
// Fan-2 escalation is now explicit AI policy instead of a hidden transform of
// legacy d_cool_stage_2. Keep the old function name as a compatibility shim
// for diagnostics/tests until downstream contracts are renamed.
inline float band_cool_stage2_delta_f(const Setpoints& sp) noexcept {
    return sp.cool_stage2_over_high_f;
}

// BC-8 (ADR0003 §6.5): ONE shared two-stage escalation latch for every two-stage
// actuator (heat1→heat2, fan1→fan2). `value` crossing ENTRY latches stage 2; it
// only drops once `value` passes EXIT (the de-escalation hysteresis), so stage 2
// cannot chatter at the threshold regardless of how tight the (pinched) band is.
//  • high_side (cooling fans): latch when value ≥ entry, clear when value < exit
//    (entry > exit; entry = temp_high + over_high, exit = entry − exit_hyst).
//  • low_side (heaters): latch when value < entry, clear when value ≥ exit
//    (entry < exit; entry = temp_low, exit = heat_target) — exactly the prior
//    heat2 geometry, so the heat path is replay-band-identical.
// In the hysteresis band the prior latch state holds.
inline bool stage2_escalation_latch(float value, float entry_thr, float exit_thr,
                                    bool latched, bool high_side) noexcept {
    if (high_side) {
        if (value >= entry_thr) return true;
        if (value <  exit_thr)  return false;
    } else {
        if (value <  entry_thr) return true;
        if (value >= exit_thr)  return false;
    }
    return latched;
}

inline float cold_vent_cooling_entry_margin_f(const Setpoints& sp) noexcept {
    const float band_width = std::max(2.0f, sp.temp_high - sp.temp_low);
    const float legacy_cold_margin = std::max(1.0f, band_width * 0.25f);
    return std::max(sp.cool_stage2_over_high_f, legacy_cold_margin);
}

inline float effective_dehum_aggressive_kpa(const Setpoints& sp) noexcept {
    return std::max(0.05f, std::min(sp.vpd_low - 0.05f, sp.dehum_aggressive_kpa));
}

inline float climate_band_error(float value, float low, float high) noexcept {
    if (value < low) return low - value;
    if (value > high) return value - high;
    return 0.0f;
}

inline bool climate_reason_is(const char* reason, const char* expected) noexcept {
    if (!reason || !expected) return false;
    while (*reason != '\0' && *expected != '\0' && *reason == *expected) {
        reason++;
        expected++;
    }
    return *reason == '\0' && *expected == '\0';
}

inline ClimateResourceCostEstimate climate_resource_estimate(ClimateAction action) noexcept {
    float water_gal = 0.0f;
    if (action == CLIMATE_VENT_COOL_MIST_ASSIST || action == CLIMATE_SEALED_HUMIDIFY) {
        water_gal = 0.04f;
    }
    if (action == CLIMATE_VENT_COOL_FOG_ASSIST || action == CLIMATE_SEALED_FOG) {
        water_gal = std::max(water_gal, 0.02f);
    }

    float electric_kwh = 0.0f;
    if (action == CLIMATE_VENT_COOL
        || action == CLIMATE_VENT_COOL_MIST_ASSIST
        || action == CLIMATE_VENT_COOL_FOG_ASSIST
        || action == CLIMATE_DEHUM_VENT
        || action == CLIMATE_SAFETY_COOL) {
        electric_kwh = 0.006f;
    }
    if (action == CLIMATE_VENT_COOL_FOG_ASSIST || action == CLIMATE_SEALED_FOG) {
        electric_kwh += 0.002f;
    }

    const float gas_therm = (action == CLIMATE_HEAT || action == CLIMATE_SAFETY_HEAT) ? 0.002f : 0.0f;
    return {
        .water_gal = water_gal,
        .electric_kwh = electric_kwh,
        .gas_therm = gas_therm
    };
}

inline ClimateCandidateProjection climate_projection(
    ClimateAction action,
    const SensorInputs& in,
    const Setpoints& sp,
    float temp_effect_f,
    float vpd_effect_kpa,
    float resource_cost,
    float relay_churn_cost,
    const char* blocked_reason,
    ClimateAction prior_action
) noexcept {
    const float projected_temp = in.temp_f + temp_effect_f;
    const float projected_vpd = in.vpd_kpa + vpd_effect_kpa;
    return {
        .action = action,
        .safety_ok = blocked_reason == nullptr || blocked_reason[0] == '\0',
        .blocked_reason = blocked_reason ? blocked_reason : "",
        .projected_temp_error_f = climate_band_error(projected_temp, sp.temp_low, sp.temp_high),
        .projected_vpd_error_kpa = climate_band_error(projected_vpd, sp.vpd_low, sp.vpd_high),
        .resource_cost = resource_cost,
        .relay_churn_cost = relay_churn_cost,
        .confidence = 0.65f,
        .prior_action_hold_preference = action == prior_action ? 1.0f : 0.0f
    };
}

inline const char* climate_summary_for_action(ClimateAction action) noexcept {
    switch (action) {
        case CLIMATE_SENSOR_FAULT: return "SENSOR_FAULT selected; sensor plausibility failed";
        case CLIMATE_SAFETY_HEAT: return "SAFETY_HEAT selected; hard low-temperature rail";
        case CLIMATE_SAFETY_COOL: return "SAFETY_COOL selected; hard high-temperature rail";
        case CLIMATE_HEAT: return "HEAT selected; temperature recovery or heat-assist dehum";
        case CLIMATE_IDLE: return "IDLE selected; band satisfied or resource tie-break";
        case CLIMATE_VENT_COOL: return "VENT_COOL selected; temperature priority";
        case CLIMATE_VENT_COOL_MIST_ASSIST: return "VENT_COOL_MIST_ASSIST selected; temp priority with VPD assist";
        case CLIMATE_VENT_COOL_FOG_ASSIST: return "VENT_COOL_FOG_ASSIST selected; temp priority with fog assist";
        case CLIMATE_SEALED_HUMIDIFY: return "SEALED_HUMIDIFY selected; VPD recovery while temp safe";
        case CLIMATE_SEALED_FOG: return "SEALED_FOG selected; severe VPD recovery while temp safe";
        case CLIMATE_DEHUM_VENT: return "DEHUM_VENT selected; VPD below band";
    }
    return "unknown climate action";
}

inline bool climate_wet_block_is_hard(const char* wet_block_reason) noexcept {
    return wet_block_reason
        && wet_block_reason[0] != '\0'
        && !climate_reason_is(wet_block_reason, "below_threshold");
}

inline ClimateMoistureAssistState climate_moisture_state_for_decision(
    bool selected_wet,
    float dry_excess,
    const char* wet_block_reason,
    const ControlState& state,
    const Setpoints& sp
) noexcept {
    if (selected_wet) return CLIMATE_MOISTURE_SERVED;
    if (dry_excess <= 0.0f) return CLIMATE_MOISTURE_INACTIVE;
    if (climate_wet_block_is_hard(wet_block_reason)) return CLIMATE_MOISTURE_BLOCKED;
    if (state.mist_backoff_timer_ms > 0) return CLIMATE_MOISTURE_PULSE_GAP;
    if (state.vpd_watch_timer_ms < sp.vpd_watch_dwell_ms) return CLIMATE_MOISTURE_ENGAGE_DELAY;
    return CLIMATE_MOISTURE_ENGAGE_DELAY;
}

inline float climate_next_mist_eligible_seconds(
    bool selected_wet,
    float dry_excess,
    const char* wet_block_reason,
    const ControlState& state,
    const Setpoints& sp
) noexcept {
    if (selected_wet) return 0.0f;
    if (dry_excess <= 0.0f) return -1.0f;
    if (climate_wet_block_is_hard(wet_block_reason)) return -1.0f;
    if (climate_reason_is(wet_block_reason, "below_threshold")) return -1.0f;
    if (state.mist_backoff_timer_ms > 0) {
        if (state.mist_backoff_timer_ms >= sp.mist_backoff_ms) return 0.0f;
        return float(sp.mist_backoff_ms - state.mist_backoff_timer_ms) / 1000.0f;
    }
    if (state.vpd_watch_timer_ms < sp.vpd_watch_dwell_ms) {
        return float(sp.vpd_watch_dwell_ms - state.vpd_watch_timer_ms) / 1000.0f;
    }
    return 0.0f;
}

inline ClimateActionDecision evaluate_climate_decision(
    const SensorInputs& in,
    const Setpoints& sp,
    const ControlState& state
) noexcept {
    const bool sensor_fault = !sensors_plausible(in);
    // SAF-1 / SF1: when the average RH/VPD probes are degraded, the VPD reading
    // is fabricated. Neutralize every VPD-chasing input so the controller does
    // NOT humidify/dehum/fog on a guess — it falls back to temp-only control +
    // the conservative timed center bursts. Temperature inputs stay live.
    const bool vpd_trusted = vpd_control_trusted(in);
    const float temp_error = climate_band_error(in.temp_f, sp.temp_low, sp.temp_high);
    const float vpd_error = vpd_trusted ? climate_band_error(in.vpd_kpa, sp.vpd_low, sp.vpd_high) : 0.0f;
    const float dry_excess = vpd_trusted ? std::max(0.0f, in.vpd_kpa - sp.vpd_high) : 0.0f;
    const float temp_high_excess = std::max(0.0f, in.temp_f - sp.temp_high);
    const float temp_low_excess = std::max(0.0f, sp.temp_low - in.temp_f);
    const float outdoor_cooling_advantage = std::isfinite(in.outdoor_temp_f)
        ? std::max(0.0f, in.temp_f - in.outdoor_temp_f)
        : 0.0f;
    const float vent_cooling_effect = -std::max(0.2f, std::min(4.0f, outdoor_cooling_advantage * 0.35f + 0.6f));
    const bool outdoor_dewpoint_advantage =
        std::isfinite(in.outdoor_dewpoint_f)
        && std::isfinite(in.dew_point_f)
        && in.outdoor_dewpoint_f < in.dew_point_f;
    const bool was_cooling = state.mode_prev == VENTILATE || state.mode_prev == THERMAL_RELIEF;
    const bool outdoor_cold_for_vent =
        std::isfinite(in.outdoor_temp_f) && in.outdoor_temp_f < (sp.temp_low - sp.cold_vent_guard_delta_f);
    const float cooling_exit_hysteresis =
        outdoor_cold_for_vent ? std::max(sp.cool_exit_hysteresis_f, COLD_VENT_COOL_EXIT_HYST_MIN_F)
                              : sp.cool_exit_hysteresis_f;
    const float cooling_entry_margin =
        outdoor_cold_for_vent ? cold_vent_cooling_entry_margin_f(sp) : 0.0f;
    const bool needs_cooling = was_cooling
        ? in.temp_f > (sp.temp_high - cooling_exit_hysteresis)
        : in.temp_f > (sp.temp_high + cooling_entry_margin);
    const bool heat_demand =
        state.heat2_latched || in.temp_f < (band_heat_target_f(sp) + sp.heat_hysteresis);
    const bool humidify_ready = dry_excess > 0.0f && state.vpd_watch_timer_ms >= sp.vpd_watch_dwell_ms;
    const bool sealed_backoff = state.mist_backoff_timer_ms > 0;
    // The cold-dehum brake is RETAINED: it triggers on exactly invariant #14's
    // condition (outdoor < temp_low - cold_vent_guard_delta_f), so it IS the
    // cold-day vent-thrash protection, not a cost-brake. On non-extreme nights it
    // does not trigger, so dehum already runs to hold the VPD target; only
    // extreme-continuous-cold-day vent-dehum is restrained (a wear/temp/VPD trade
    // needing a replay-tuned long dehum min-dwell + a crop-priority call — tracked
    // under BC-4, deferred from this OTA so invariant #14 stays green).
    // BC-13 (ADR0003 §6.4): DEHUM_VENT fires only when VENTING is the effective dehum
    // action (MX_VENT_DEHUM). The estimator returns MX_HEAT_ASSIST instead when venting
    // would over-cool (frigid outdoor) — in that case the controller's normal cold-heat
    // path warms the air, which raises VPD (dries) WITHOUT opening the vent, so frigid
    // days do not thrash the vent (invariant #14). Replaces the cold_dehum_allowed brake:
    // a moderately-cool effective vent-dehum is no longer blocked; a frigid over-cooling
    // vent is routed to heat instead of being forced.
    const MoistureExchangeEstimate mx = estimate_moisture_exchange(in, sp);
    const bool dehum_effective = (mx.action == MX_VENT_DEHUM);
    const float dehum_hysteresis = band_vpd_hysteresis(sp);
    const bool dehum_enter = in.vpd_kpa < (sp.vpd_low - dehum_hysteresis);
    const bool dehum_continue = state.mode_prev == DEHUM_VENT && in.vpd_kpa < sp.vpd_low;
    const bool dehum_wanted = vpd_trusted
        && !sp.econ_block
        && dehum_effective
        && (dehum_enter || dehum_continue);

    const char* wet_block_reason = climate_wet_assist_block_reason(in, sp);
    const char* fog_block_reason = "none";
    const char* climate_fog_block = climate_fog_assist_block_reason(in, sp);
    if (climate_fog_block[0] == 'o' && climate_fog_block[1] == 'c') {
        fog_block_reason = "occupancy";
    } else if (dry_excess < sp.fog_escalation_kpa) {
        fog_block_reason = "below_threshold";
    } else {
        fog_block_reason = climate_fog_block[0] == '\0' ? "none" : climate_fog_block;
    }
    const char* fog_candidate_block = fog_block_reason[0] == 'n' && fog_block_reason[1] == 'o' ? "" : fog_block_reason;
    const char* sealed_block_reason = !humidify_ready
        ? "engage_delay"
        : (sealed_backoff ? "mist_backoff" : wet_block_reason);

    const ClimateAction prior_action = (state.mode == VENTILATE)
        ? (state.vent_mist_assist_active ? CLIMATE_VENT_COOL_MIST_ASSIST : CLIMATE_VENT_COOL)
        : (state.mode == SEALED_MIST
            ? (state.mist_stage == MIST_FOG ? CLIMATE_SEALED_FOG : CLIMATE_SEALED_HUMIDIFY)
            : (state.mode == DEHUM_VENT ? CLIMATE_DEHUM_VENT : CLIMATE_IDLE));

    ClimateCandidateProjection candidates[] = {
        climate_projection(CLIMATE_SENSOR_FAULT, in, sp, 0.0f, 0.0f, 0.0f, 0.0f,
                           sensor_fault ? "" : "not_faulted", prior_action),
        climate_projection(CLIMATE_SAFETY_HEAT, in, sp, 2.5f, 0.05f, 8.0f, 1.0f,
                           in.temp_f <= sp.safety_min ? "" : "not_safety_heat", prior_action),
        climate_projection(CLIMATE_SAFETY_COOL, in, sp, vent_cooling_effect - 0.5f, dry_excess > 0.0f ? -0.05f : 0.05f, 4.0f, 1.0f,
                           in.temp_f >= sp.safety_max ? "" : "not_safety_cool", prior_action),
        climate_projection(CLIMATE_HEAT, in, sp, std::min(2.0f, temp_low_excess), 0.05f, 6.0f, 1.0f,
                           heat_demand ? "" : "temp_not_low", prior_action),
        climate_projection(CLIMATE_IDLE, in, sp, 0.0f, 0.0f, 0.0f, 0.0f,
                           (!needs_cooling && !heat_demand && !dehum_wanted && !(humidify_ready && dry_excess > 0.0f))
                               ? "" : "active_demand", prior_action),
        climate_projection(CLIMATE_VENT_COOL, in, sp, vent_cooling_effect, outdoor_dewpoint_advantage ? 0.08f : 0.0f, 2.0f, 1.0f,
                           needs_cooling ? "" : "temp_not_high", prior_action),
        climate_projection(CLIMATE_VENT_COOL_MIST_ASSIST, in, sp, vent_cooling_effect - 0.4f, -std::min(dry_excess, 0.18f), 5.0f, 1.3f,
                           (needs_cooling && dry_excess >= sp.direct_wet_stress_vpd_margin_kpa)
                               ? wet_block_reason : "temp_or_vpd_below_assist", prior_action),
        climate_projection(CLIMATE_VENT_COOL_FOG_ASSIST, in, sp, vent_cooling_effect - 0.7f, -std::min(dry_excess, 0.3f), 7.0f, 1.5f,
                           (needs_cooling && dry_excess >= sp.fog_escalation_kpa)
                               ? fog_candidate_block : "below_threshold", prior_action),
        climate_projection(CLIMATE_SEALED_HUMIDIFY, in, sp, temp_high_excess > 0.0f ? 0.2f : 0.0f, -std::min(dry_excess, 0.16f), 4.0f, 1.2f,
                           (!needs_cooling && dry_excess > 0.0f)
                               ? sealed_block_reason : "temp_priority_blocks_seal", prior_action),
        climate_projection(CLIMATE_SEALED_FOG, in, sp, temp_high_excess > 0.0f ? 0.3f : 0.0f, -std::min(dry_excess, 0.3f), 6.0f, 1.4f,
                           (!needs_cooling && dry_excess >= sp.fog_escalation_kpa)
                               ? (sealed_block_reason[0] == '\0' ? fog_candidate_block : sealed_block_reason) : "below_threshold", prior_action),
        climate_projection(CLIMATE_DEHUM_VENT, in, sp, vent_cooling_effect * 0.4f, 0.15f, 2.5f, 1.0f,
                           dehum_wanted ? "" : "vpd_not_low", prior_action)
    };

    // ── Firmware-v2 normalized arbitration (owner's "equality statement") ──
    // °F and kPa are incomparable; divide each error by its band half-width
    // and let the axis with the LARGER current normalized delta lead the
    // candidate ordering. Safety still preempts everything below.
    {
        const float temp_hw = std::max(1.0f, (sp.temp_high - sp.temp_low) * 0.5f);
        const float vpd_hw  = std::max(0.05f, (sp.vpd_high - sp.vpd_low) * 0.5f);
        const bool vpd_leads = (vpd_error / vpd_hw) > (temp_error / temp_hw);
        for (auto& c : candidates) {
            c.norm_temp_error = c.projected_temp_error_f / temp_hw;
            c.norm_vpd_error  = c.projected_vpd_error_kpa / vpd_hw;
            c.vpd_axis_leads  = vpd_leads;
        }
    }

    int selected_index = -1;
    if (sensor_fault) {
        selected_index = 0;
    } else if (in.temp_f >= sp.safety_max) {
        selected_index = 2;
    } else if (in.temp_f <= sp.safety_min) {
        selected_index = 1;
    } else {
        selected_index = choose_climate_candidate_index(candidates, sizeof(candidates) / sizeof(candidates[0]));
    }
    if (selected_index < 0) selected_index = 4;  // IDLE fallback; should not be reached.

    const ClimateAction action = candidates[selected_index].action;
    const ClimatePriorityAxis axis = (sensor_fault || action == CLIMATE_SAFETY_COOL || action == CLIMATE_SAFETY_HEAT)
        ? CLIMATE_PRIORITY_SAFETY
        : ((action == CLIMATE_HEAT
            || action == CLIMATE_VENT_COOL
            || action == CLIMATE_VENT_COOL_MIST_ASSIST
            || action == CLIMATE_VENT_COOL_FOG_ASSIST)
            ? CLIMATE_PRIORITY_TEMP
            : ((action == CLIMATE_SEALED_HUMIDIFY
                || action == CLIMATE_SEALED_FOG
                || action == CLIMATE_DEHUM_VENT)
                ? CLIMATE_PRIORITY_VPD
                : (temp_error > 0.0f ? CLIMATE_PRIORITY_TEMP : (vpd_error > 0.0f ? CLIMATE_PRIORITY_VPD : CLIMATE_PRIORITY_RESOURCE))));

    const bool selected_wet = action == CLIMATE_VENT_COOL_MIST_ASSIST
        || action == CLIMATE_VENT_COOL_FOG_ASSIST
        || action == CLIMATE_SEALED_HUMIDIFY
        || action == CLIMATE_SEALED_FOG;
    const ClimateMoistureZone moisture_zone = (action == CLIMATE_VENT_COOL_MIST_ASSIST || action == CLIMATE_SEALED_HUMIDIFY)
        ? CLIMATE_ZONE_CENTER
        : CLIMATE_ZONE_NONE;

    return {
        .climate_action = action,
        .priority_axis = axis,
        .temp_error_f = temp_error,
        .vpd_error_kpa = vpd_error,
        .candidate_summary = climate_summary_for_action(action),
        .moisture_assist_state = climate_moisture_state_for_decision(selected_wet, dry_excess, wet_block_reason, state, sp),
        .moisture_zone = moisture_zone,
        .next_mist_eligible_s = climate_next_mist_eligible_seconds(selected_wet, dry_excess, wet_block_reason, state, sp),
        .fog_margin_kpa = dry_excess - sp.fog_escalation_kpa,
        .fog_block_reason = fog_block_reason,
        .resource_cost_estimate = climate_resource_estimate(action),
        .moisture_exchange_action = moisture_exchange_action_name(mx.action),
        .moisture_exchange_reason = mx.reason,
        .moisture_vent_vpd_gain_kpa = mx.vent_vpd_gain_kpa,
        .moisture_heat_vpd_gain_kpa = mx.heat_vpd_gain_kpa,
        .moisture_outdoor_fresh = mx.outdoor_fresh,
        .moisture_vent_overcools = mx.vent_overcools,
        .moisture_heat_assist_corun = mx.heat_assist_corun,
        .moisture_heat_assist_active = state.dehum_heat_assist_active,
        .moisture_heat_assist_timer_ms = state.dehum_heat_assist_timer_ms
    };
}

inline Mode climate_action_to_mode(ClimateAction action) noexcept {
    switch (action) {
        case CLIMATE_SENSOR_FAULT: return SENSOR_FAULT;
        case CLIMATE_SAFETY_COOL: return SAFETY_COOL;
        case CLIMATE_SAFETY_HEAT: return SAFETY_HEAT;
        case CLIMATE_VENT_COOL:
        case CLIMATE_VENT_COOL_MIST_ASSIST:
        case CLIMATE_VENT_COOL_FOG_ASSIST:
            return VENTILATE;
        case CLIMATE_SEALED_HUMIDIFY:
        case CLIMATE_SEALED_FOG:
            return SEALED_MIST;
        case CLIMATE_DEHUM_VENT:
            return DEHUM_VENT;
        case CLIMATE_HEAT:
        case CLIMATE_IDLE:
            return IDLE;
    }
    return IDLE;
}

inline ClimateAction effective_climate_action_for_mode(
    Mode mode,
    const ControlState& state,
    const RelayOutputs& relay_out
) noexcept {
    switch (mode) {
        case SENSOR_FAULT:
            return CLIMATE_SENSOR_FAULT;
        case SAFETY_COOL:
            return CLIMATE_SAFETY_COOL;
        case SAFETY_HEAT:
            return CLIMATE_SAFETY_HEAT;
        case VENTILATE:
            if (climate_reason_is(state.last_mode_reason, "vent_fog_assist")) {
                return CLIMATE_VENT_COOL_FOG_ASSIST;
            }
            if (state.vent_mist_assist_active
                || climate_reason_is(state.last_mode_reason, "vent_mist_assist")) {
                return CLIMATE_VENT_COOL_MIST_ASSIST;
            }
            return CLIMATE_VENT_COOL;
        case SEALED_MIST:
            return state.mist_stage == MIST_FOG ? CLIMATE_SEALED_FOG : CLIMATE_SEALED_HUMIDIFY;
        case DEHUM_VENT:
            return CLIMATE_DEHUM_VENT;
        case THERMAL_RELIEF:
            return CLIMATE_VENT_COOL;
        case IDLE:
            return (relay_out.heat1 || relay_out.heat2) ? CLIMATE_HEAT : CLIMATE_IDLE;
    }
    return CLIMATE_IDLE;
}

inline ClimatePriorityAxis climate_priority_axis_for_effective_action(
    ClimateAction action,
    float temp_error_f,
    float vpd_error_kpa
) noexcept {
    if (action == CLIMATE_SENSOR_FAULT
        || action == CLIMATE_SAFETY_COOL
        || action == CLIMATE_SAFETY_HEAT) {
        return CLIMATE_PRIORITY_SAFETY;
    }
    if (action == CLIMATE_HEAT
        || action == CLIMATE_VENT_COOL
        || action == CLIMATE_VENT_COOL_MIST_ASSIST
        || action == CLIMATE_VENT_COOL_FOG_ASSIST) {
        return CLIMATE_PRIORITY_TEMP;
    }
    if (action == CLIMATE_SEALED_HUMIDIFY
        || action == CLIMATE_SEALED_FOG
        || action == CLIMATE_DEHUM_VENT) {
        return CLIMATE_PRIORITY_VPD;
    }
    return temp_error_f > 0.0f
        ? CLIMATE_PRIORITY_TEMP
        : (vpd_error_kpa > 0.0f ? CLIMATE_PRIORITY_VPD : CLIMATE_PRIORITY_RESOURCE);
}

inline const char* climate_summary_for_effective_action(
    ClimateAction action,
    const ControlState& state
) noexcept {
    if (action == CLIMATE_HEAT && climate_reason_is(state.last_mode_reason, "heat_dehum")) {
        return "HEAT selected; closed-vent heat-assist dehum";
    }
    if (action == CLIMATE_IDLE && climate_reason_is(state.last_mode_reason, "dwell_hold")) {
        return "IDLE selected; dwell gate holding prior mode";
    }
    if (action == CLIMATE_IDLE && climate_reason_is(state.last_mode_reason, "mist_backoff")) {
        return "IDLE selected; mist backoff";
    }
    return climate_summary_for_action(action);
}

inline ClimateActionDecision describe_effective_climate_decision(
    Mode mode,
    const SensorInputs& in,
    const Setpoints& sp,
    const ControlState& state,
    const RelayOutputs& relay_out
) noexcept {
    const ClimateAction action = effective_climate_action_for_mode(mode, state, relay_out);
    const float temp_error = climate_band_error(in.temp_f, sp.temp_low, sp.temp_high);
    const float vpd_error = climate_band_error(in.vpd_kpa, sp.vpd_low, sp.vpd_high);
    const float dry_excess = std::max(0.0f, in.vpd_kpa - sp.vpd_high);
    const char* fog_block_reason = "none";
    const char* wet_block_reason = climate_wet_assist_block_reason(in, sp);
    const char* climate_fog_block = climate_fog_assist_block_reason(in, sp);
    if (climate_fog_block[0] == 'o' && climate_fog_block[1] == 'c') {
        fog_block_reason = "occupancy";
    } else if (dry_excess < sp.fog_escalation_kpa) {
        fog_block_reason = "below_threshold";
    } else {
        fog_block_reason = climate_fog_block[0] == '\0' ? "none" : climate_fog_block;
    }
    const bool selected_wet = action == CLIMATE_VENT_COOL_MIST_ASSIST
        || action == CLIMATE_VENT_COOL_FOG_ASSIST
        || action == CLIMATE_SEALED_HUMIDIFY
        || action == CLIMATE_SEALED_FOG;
    const ClimateMoistureZone moisture_zone = (action == CLIMATE_VENT_COOL_MIST_ASSIST || action == CLIMATE_SEALED_HUMIDIFY)
        ? CLIMATE_ZONE_CENTER
        : CLIMATE_ZONE_NONE;
    const MoistureExchangeEstimate mx = estimate_moisture_exchange(in, sp);

    ClimatePriorityAxis priority_axis = climate_priority_axis_for_effective_action(action, temp_error, vpd_error);
    if (action == CLIMATE_HEAT && climate_reason_is(state.last_mode_reason, "heat_dehum")) {
        priority_axis = CLIMATE_PRIORITY_VPD;
    }

    return {
        .climate_action = action,
        .priority_axis = priority_axis,
        .temp_error_f = temp_error,
        .vpd_error_kpa = vpd_error,
        .candidate_summary = climate_summary_for_effective_action(action, state),
        .moisture_assist_state = climate_moisture_state_for_decision(selected_wet, dry_excess, wet_block_reason, state, sp),
        .moisture_zone = moisture_zone,
        .next_mist_eligible_s = climate_next_mist_eligible_seconds(selected_wet, dry_excess, wet_block_reason, state, sp),
        .fog_margin_kpa = dry_excess - sp.fog_escalation_kpa,
        .fog_block_reason = fog_block_reason,
        .resource_cost_estimate = climate_resource_estimate(action),
        .moisture_exchange_action = moisture_exchange_action_name(mx.action),
        .moisture_exchange_reason = mx.reason,
        .moisture_vent_vpd_gain_kpa = mx.vent_vpd_gain_kpa,
        .moisture_heat_vpd_gain_kpa = mx.heat_vpd_gain_kpa,
        .moisture_outdoor_fresh = mx.outdoor_fresh,
        .moisture_vent_overcools = mx.vent_overcools,
        .moisture_heat_assist_corun = mx.heat_assist_corun,
        .moisture_heat_assist_active = state.dehum_heat_assist_active,
        .moisture_heat_assist_timer_ms = state.dehum_heat_assist_timer_ms
    };
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

// #295: solar-phased per-circuit photoperiod membership. Mirrors the
// in_dawn_rehydrate_window() pattern — the window anchors to the ON-CHIP solar
// minute fields so each circuit tracks real sunrise/sunset all year instead of
// a static clock hour. Backward-safe: if solar phasing is OFF or the caller did
// not wire the solar minute fields (sunrise_min/sunset_min still 0), it falls
// back EXACTLY to the legacy integer-hour lighting_hour_in_window().
inline bool lighting_in_window(const LightingInputs& in, const LightingSetpoints& sp) noexcept {
    const bool solar_available =
        sp.solar_phasing && (in.sunrise_min != 0 || in.sunset_min != 0);
    if (!solar_available) {
        return lighting_hour_in_window(in.local_hour, sp.start_hour, sp.cutoff_hour);
    }
    const int now_minute = local_minute_of_day(in.local_hour, in.local_minute);
    const int start = (((in.sunrise_min + sp.sunrise_offset_min) % 1440) + 1440) % 1440;
    const int end = (((in.sunset_min + sp.sunset_offset_min) % 1440) + 1440) % 1440;
    // Reuse the wrap-safe minute window: duration = (end - start) mod 1440. A
    // zero/degenerate span (start == end) means no photoperiod → never in window.
    int duration = ((end - start) % 1440 + 1440) % 1440;
    if (duration == 0) return false;
    return minute_in_window(now_minute, start, duration);
}

inline void validate_lighting_setpoints(LightingSetpoints& sp) noexcept {
    sp.target_light_minutes = std::max(uint32_t(0), std::min(uint32_t(1080), sp.target_light_minutes));
    sp.lux_on_threshold = std::max(100.0f, std::min(100000.0f, sp.lux_on_threshold));
    sp.lux_hysteresis = std::max(0.0f, std::min(25000.0f, sp.lux_hysteresis));
    sp.start_hour = std::max(0, std::min(23, sp.start_hour));
    sp.cutoff_hour = std::max(0, std::min(23, sp.cutoff_hour));
    sp.min_on_ms = std::max(uint32_t(0), std::min(uint32_t(3600000), sp.min_on_ms));
    sp.min_off_ms = std::max(uint32_t(0), std::min(uint32_t(3600000), sp.min_off_ms));
    // #295: bound the solar-phase offsets to +/- one day so a corrupt push
    // cannot make the window math diverge wildly; minute_in_window wraps anyway.
    sp.sunrise_offset_min = std::max(-1440, std::min(1440, sp.sunrise_offset_min));
    sp.sunset_offset_min = std::max(-1440, std::min(1440, sp.sunset_offset_min));
}

// #295: per-circuit dead-fixture / phantom-DLI suppression. When a circuit is
// flagged out_of_service (its physical fixture is shorted/unplugged), its
// switch-ON state must NOT credit supplemental DLI — the dark fixture was
// otherwise self-deceivingly "meeting" the photoperiod on paper. main/grow
// out_of_service default to false so legacy call sites are byte-identical.
inline float lighting_dli_increment(
    float indoor_lux,
    float tempest_lux,
    bool main_light_on,
    bool grow_light_on,
    float dt_s,
    bool main_out_of_service = false,
    bool grow_out_of_service = false
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
    const bool main_credits = main_light_on && !main_out_of_service;
    const bool grow_credits = grow_light_on && !grow_out_of_service;
    const float supplemental_dli_per_hour =
        (main_credits ? MAIN_LIGHT_DLI_PER_HOUR : 0.0f)
        + (grow_credits ? GROW_LIGHT_DLI_PER_HOUR : 0.0f);
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
    // #295: window membership is now solar-phased when enabled+wired; otherwise
    // it is byte-identical to the legacy integer-hour window.
    const bool in_window = sp.auto_enabled && lighting_in_window(in, sp);
    const bool crossed_midnight = state.last_count_hour >= 0 && in.local_hour < state.last_count_hour;
    const bool reached_start_hour = in.local_hour == sp.start_hour && state.last_count_hour != sp.start_hour;
    if (crossed_midnight || reached_start_hour) {
        state.qualified_light_s = 0.0f;
        state.natural_qualified_s = 0.0f;
        state.switch_on_s = 0.0f;
        state.overlap_s = 0.0f;
    }

    const bool natural_qualified = natural_lux >= sp.lux_on_threshold;
    // #295: a dead/out-of-service fixture emits NO light, so its switch-ON state
    // must not be credited as delivered light. switch_on_qualifies gates every
    // place the physical switch state would otherwise count toward DLI/minutes;
    // natural lux (which is real, fixture-independent) still counts. This stops
    // the dark west grow circuit from "meeting" its photoperiod on paper.
    const bool switch_on_qualifies = current_on && !sp.out_of_service;
    const float count_dt_s = std::isfinite(dt_s) ? std::max(0.0f, dt_s) : 0.0f;
    if (in_window && count_dt_s > 0.0f) {
        if (natural_qualified) {
            state.natural_qualified_s += count_dt_s;
        }
        if (switch_on_qualifies) {
            state.switch_on_s += count_dt_s;
        }
        if (natural_qualified && switch_on_qualifies) {
            state.overlap_s += count_dt_s;
        }
        if (natural_qualified || switch_on_qualifies) {
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
    // ENV-5 (M14): occupancy task-light is GATED on in_window so an evening
    // visit cannot turn grow lights on during the protected dark period.
    // Empirically grow lights were observed on at 23:49 local because this
    // branch (evaluated BEFORE the !in_window guard below) omitted the window
    // test. The window [start_hour, cutoff_hour) is the dispatcher-pushed
    // photoperiod (Vanda-driven, not the highest-DLI crop); keeping the
    // occupancy convenience light inside it guarantees the >=6h dark block.
    const bool occupancy_task_light_demand = sp.auto_enabled
        && in_window
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
    } else if (!in_window) {
        // ENV-5 (M14): outside the photoperiod window NOTHING lights — neither
        // the plant supplement nor an occupancy task light. Reported before the
        // occupancy fallbacks so a visit during the dark block is auditable as
        // "outside_window" rather than "lux_sufficient".
        want_on = false;
        reason = "outside_window";
    } else if (in.occupied && !exterior_lux_available) {
        want_on = false;
        reason = "occupancy_lux_unavailable";
    } else if (in.occupied && exterior_lux_available) {
        want_on = false;
        reason = "lux_sufficient";
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
        .out_of_service = sp.out_of_service,
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
        state.center_burst = CENTER_BURST_NONE;  // IRR-3/IRR-4: no burst under fault
        state.last_mode_reason = "sensor_fault";
        return SENSOR_FAULT;
    }

    const Mode prev = state.mode_prev;
    const float temp_high = sp.temp_high;
    const float HV = band_vpd_hysteresis(sp);

    const bool safety_cool = in.temp_f >= sp.safety_max;
    const bool safety_heat = in.temp_f <= sp.safety_min;
    const bool was_cooling = (prev == VENTILATE) || (prev == THERMAL_RELIEF);
    // SAF-1 / SF1: a degraded VPD reading is fabricated — treat VPD as "not
    // high" so the vpd_watch timer never accrues, humidify/summer-vent never
    // arm, and the FSM mirrors the VPD-suppressed evaluate_climate_decision.
    // vpd_high_resolved is forced TRUE so any in-flight sealed state exits
    // cleanly (rather than latching on the fabricated reading).
    const bool vpd_trusted = vpd_control_trusted(in);
    const bool vpd_high = vpd_trusted && in.vpd_kpa > sp.vpd_high;
    const bool vpd_high_resolved = !vpd_trusted || in.vpd_kpa <= (sp.vpd_high - HV);
    const bool outdoor_cold_for_vent =
        std::isfinite(in.outdoor_temp_f) && in.outdoor_temp_f < (sp.temp_low - sp.cold_vent_guard_delta_f);
    const float cooling_exit_hysteresis =
        outdoor_cold_for_vent ? std::max(sp.cool_exit_hysteresis_f, COLD_VENT_COOL_EXIT_HYST_MIN_F)
                              : sp.cool_exit_hysteresis_f;
    const float cooling_entry_margin =
        outdoor_cold_for_vent ? cold_vent_cooling_entry_margin_f(sp) : 0.0f;
    const bool needs_cooling = was_cooling
        ? in.temp_f > (temp_high - cooling_exit_hysteresis)
        : in.temp_f > (temp_high + cooling_entry_margin);
    const float heat_target = band_heat_target_f(sp);
    const bool needs_heating_s1 = in.temp_f < (heat_target + sp.heat_hysteresis);

    // BC-13 (ADR0003 §6.4): the cold-day dehum decision is the deterministic
    // outdoor-aware moisture-exchange estimator, NOT the cold_dehum_allowed brake.
    // Cold nights now dehum WHEN venting or heat-assist physically moves VPD toward
    // target (the orchid wet-night fix); an ineffective vent (outdoor wetter) is not
    // chosen. mx also drives the humidify-by-vent import override + the heat-assist
    // min-dwell below.
    const MoistureExchangeEstimate mx = estimate_moisture_exchange(in, sp);
    const bool dehum_effective = (mx.action == MX_VENT_DEHUM);
    const bool vpd_dehum_exit = in.vpd_kpa >= sp.vpd_low || !dehum_effective;
    const bool was_dehum = prev == DEHUM_VENT;
    const bool moisture_blocked = moisture_blocked_by_occupancy(in, sp);
    const bool closed_heat_dehum_wanted =
        vpd_trusted
        && !sp.econ_block
        && mx.action == MX_HEAT_ASSIST
        && is_night_phase(in)
        && in.vpd_kpa < (sp.vpd_low - HV)
        && in.temp_f >= sp.temp_low
        && (in.temp_f + GH_HEAT_ASSIST_PROBE_DF) <= (sp.temp_high - sp.temp_hysteresis)
        && !needs_cooling
        && !needs_heating_s1
        && !safety_cool
        && !safety_heat;

    {
        // BC-8 (ADR0003 §6.5): heat2 and fan2 share ONE two-stage escalation latch
        // so neither can chatter at the stage-2 threshold.
        // heat2 = LOW side — latch when temp < temp_low, clear at temp ≥ heat_target.
        // Geometry preserved EXACTLY (was: if temp<temp_low set; elif temp≥heat_target
        // clear; else hold) so the heat path is replay-band-identical.
        state.heat2_latched = stage2_escalation_latch(
            in.temp_f, sp.temp_low, heat_target, state.heat2_latched, /*high_side=*/false);
        // fan2 = HIGH side — latch when temp ≥ temp_high + cool_stage2_over_high_f,
        // clear at that minus cool_stage2_exit_hysteresis_f (the de-escalation gap
        // heat2 always had and fan2 lacked). Evaluated on the PINCHED band edge
        // (temp_high), so tighter tracking preserves the hysteresis WIDTH. Reset out
        // of any cooling context so it cannot stale-latch into a later VENTILATE entry.
        const float fan2_entry = temp_high + sp.cool_stage2_over_high_f;
        const float fan2_exit  = fan2_entry - sp.cool_stage2_exit_hysteresis_f;
        state.fan2_latched = (needs_cooling || was_cooling)
            ? stage2_escalation_latch(in.temp_f, fan2_entry, fan2_exit, state.fan2_latched, /*high_side=*/true)
            : false;
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

    const ClimateActionDecision climate_decision = evaluate_climate_decision(in, sp, state);
    ClimateAction selected_action = climate_decision.climate_action;
    Mode mode = climate_action_to_mode(selected_action);
    state.dry_override_active = false;
    state.last_mode_reason = "idle";

    switch (selected_action) {
        case CLIMATE_SENSOR_FAULT: state.last_mode_reason = "sensor_fault"; break;
        case CLIMATE_SAFETY_COOL: state.last_mode_reason = "safety_cool"; break;
        case CLIMATE_SAFETY_HEAT: state.last_mode_reason = "safety_heat"; break;
        case CLIMATE_HEAT: state.last_mode_reason = state.heat2_latched ? "heat_stage2" : "heat_stage1"; break;
        case CLIMATE_IDLE: state.last_mode_reason = "idle"; break;
        case CLIMATE_VENT_COOL: state.last_mode_reason = state.override_summer_vent ? "summer_vent" : "temp_high"; break;
        case CLIMATE_VENT_COOL_MIST_ASSIST: state.last_mode_reason = "vent_mist_assist"; break;
        case CLIMATE_VENT_COOL_FOG_ASSIST: state.last_mode_reason = "vent_fog_assist"; break;
        case CLIMATE_SEALED_HUMIDIFY: state.last_mode_reason = prev == SEALED_MIST ? "humidify_continue" : "humidify_enter"; break;
        case CLIMATE_SEALED_FOG: state.last_mode_reason = prev == SEALED_MIST ? "fog_continue" : "fog_enter"; break;
        case CLIMATE_DEHUM_VENT: state.last_mode_reason = was_dehum && !vpd_dehum_exit ? "dehum_continue" : "vpd_low"; break;
    }

    // ── F7 (issue 5): VPD SAFETY-RAIL hard overrides ─────────────────────────
    // Wire the operator VPD safety rails into the PRODUCTION band-first path.
    // They were inert here (only the now-deleted legacy cascade consulted them):
    // the band edges (vpd_high/vpd_low) drive normal humidify/dehum, but the
    // WIDER rails (vpd_max_safe/vpd_min_safe) are the backstop for an EXTREME
    // excursion and must preempt the dwell gate (a rail firing implies vpd_error
    // > 0 ⇒ compliance_preempts_dwell below, so dwell never reverts it). VPD must
    // be trusted so a fabricated reading cannot force a seal/dehum. Mirrors the
    // legacy R2-3 dry override + vpd_min_safe rescue, now active in prod. Inserted
    // before the sealed-state handling so the forced mode flows through the
    // normal sealed_timer / mist_stage setup (no bespoke state seeding).
    {
        const bool can_seal_for_dryness =
            (mode != SAFETY_COOL) && (mode != SAFETY_HEAT)
            && (mode != THERMAL_RELIEF) && (mode != VENTILATE)
            && !needs_cooling && !moisture_blocked
            && (in.temp_f < (sp.temp_high - sp.temp_hysteresis));
        if (vpd_trusted && in.vpd_kpa > sp.vpd_max_safe && can_seal_for_dryness) {
            state.dry_override_active = (mode != SEALED_MIST);
            selected_action = CLIMATE_SEALED_HUMIDIFY;
            mode = SEALED_MIST;
            state.last_mode_reason = "dry_override";
        } else if (vpd_trusted && in.vpd_kpa < sp.vpd_min_safe
                   && (mode == IDLE || mode == SEALED_MIST) && !sp.econ_block) {
            // BC-13 (ADR0003 §6.4, Jason 2026-06-17): the vpd_min_safe rescue is a
            // SAFETY RAIL — it ALWAYS acts on an extreme too-wet excursion. When the
            // estimator proves sensible heat is the effective drying actuator, route
            // through the normal heat path instead of forcing cold-day vent cycling
            // (invariant #14). Otherwise keep the forced DEHUM_VENT rescue.
            if (mx.action == MX_HEAT_ASSIST) {
                selected_action = CLIMATE_HEAT;
                mode = climate_action_to_mode(selected_action);
                state.last_mode_reason = "vpd_min_safe_heat_rescue";
            } else {
                selected_action = CLIMATE_DEHUM_VENT;
                mode = DEHUM_VENT;
                state.last_mode_reason = "vpd_min_safe_rescue";
            }
        }
    }

    // ADR-0004 / #383: ordinary low-wet nights can otherwise idle when the
    // estimator proves closed-vent heat is the effective dehumidifier. Allow a
    // bounded heat1-only nudge while still inside the served temperature band;
    // vent remains closed, heat2 remains reserved for temperature recovery, and
    // the 1.5F probe must stay below the high edge.
    if (mode == IDLE && closed_heat_dehum_wanted) {
        selected_action = CLIMATE_HEAT;
        mode = IDLE;
        state.last_mode_reason = "heat_dehum";
    }

    // BC-13 (ADR0003 §6.4): humid-outside / dry-inside — when the controller would
    // humidify (too dry) but the estimator proves importing moist outdoor air by
    // venting beats fogging, redirect SEALED_MIST -> DEHUM_VENT(import). Vent-only, NO
    // heat (heat raises VPD, the wrong direction). Capped at vpd_target + dew-margin
    // guard inside the estimator (Jason 2026-06-17). Post-selection override, like F7.
    if (mode == SEALED_MIST && mx.action == MX_VENT_HUMIDIFY
        && !needs_cooling && !moisture_blocked) {
        selected_action = CLIMATE_DEHUM_VENT;
        mode = DEHUM_VENT;
        state.last_mode_reason = "vent_humidify";
    }

    // BC-13: DEHUM_VENT heat-assist min-dwell. The timer accrues only while continuously
    // in the too-wet dehum direction; resets on exit / when the estimate flips. heat1
    // co-run arms once the dwell is satisfied AND the estimator proved heat+vent both help.
    const bool closed_heat_dehum_active =
        (mode == IDLE) && climate_reason_is(state.last_mode_reason, "heat_dehum");
    if (mode == DEHUM_VENT && mx.action == MX_VENT_DEHUM) {
        state.dehum_heat_assist_timer_ms = sat_add(state.dehum_heat_assist_timer_ms, dt_ms);
    } else {
        state.dehum_heat_assist_timer_ms = 0;
    }
    state.dehum_heat_assist_active =
        closed_heat_dehum_active
        || ((mode == DEHUM_VENT) && mx.heat_assist_corun
            && state.dehum_heat_assist_timer_ms >= sp.dehum_heat_assist_min_dwell_ms);
    if (mode == DEHUM_VENT && state.dehum_heat_assist_active) {
        state.last_mode_reason = "dehum_vent_heat";
    }

    if (mode == SAFETY_COOL || mode == SAFETY_HEAT || mode == SENSOR_FAULT) {
        state.sealed_timer_ms = 0;
        state.relief_timer_ms = 0;
        state.vpd_watch_timer_ms = 0;
        state.relief_cycle_count = 0;
        state.vent_latch_timer_ms = 0;
        state.mist_backoff_timer_ms = 0;
        state.vent_mist_assist_active = false;
    }
    bool entered_sealed_this_cycle = false;
    if (mode == SEALED_MIST) {
        if (prev == SEALED_MIST) {
            state.sealed_timer_ms = sat_add(state.sealed_timer_ms, dt_ms);
        } else {
            entered_sealed_this_cycle = true;
            state.sealed_timer_ms = dt_ms;
            state.mist_stage = MIST_FOG;   // BC-14 fog-first: FOG is the entry humidifier
            state.mist_stage_timer_ms = 0;
            state.vent_latch_timer_ms = 0;
        }
        if (state.sealed_timer_ms >= sp.sealed_max_ms) {
            selected_action = CLIMATE_IDLE;
            mode = IDLE;
            state.last_mode_reason = "mist_backoff";
            state.relief_cycle_count = sat_add(state.relief_cycle_count, 1);
            state.sealed_timer_ms = 0;
            state.relief_timer_ms = 0;
            state.vent_latch_timer_ms = 0;
            state.mist_backoff_timer_ms = dt_ms;
            state.mist_stage = MIST_WATCH;
            state.mist_stage_timer_ms = 0;
        }
    } else {
        if (prev == SEALED_MIST) {
            state.vpd_watch_timer_ms = vpd_high_resolved || moisture_blocked ? 0 : state.vpd_watch_timer_ms;
            state.relief_cycle_count = vpd_high_resolved ? 0 : state.relief_cycle_count;
            state.vent_latch_timer_ms = 0;
            state.mist_stage = MIST_WATCH;
            state.mist_stage_timer_ms = 0;
            if (vpd_high_resolved) {
                state.last_mode_reason = "humidify_resolved";
            } else if (moisture_blocked) {
                state.last_mode_reason = "moisture_blocked";
            }
        }
        state.sealed_timer_ms = 0;
    }

    {
        const bool safety_preempts_dwell =
            (mode == SAFETY_COOL) || (mode == SAFETY_HEAT) || (mode == SENSOR_FAULT);
        const bool compliance_preempts_dwell =
            safety_preempts_dwell
            || climate_decision.temp_error_f > 0.0f
            || climate_decision.vpd_error_kpa > 0.0f;
        const bool mode_would_change = mode != state.mode_prev;
        const bool in_dwell = state.last_transition_tick_ms < sp.dwell_gate_ms;
        if (sp.sw_dwell_gate_enabled
            && mode_would_change
            && in_dwell
            && !compliance_preempts_dwell) {
            mode = state.mode_prev;
            state.last_mode_reason = "dwell_hold";
        }
        if (mode != state.mode_prev) {
            state.last_transition_tick_ms = 0;
        } else {
            state.last_transition_tick_ms = sat_add(state.last_transition_tick_ms, dt_ms);
        }
    }

    // BC-14 (ADR0003 §6.6): FOG-FIRST escalation ladder (lowest-GPM-first). Fog
    // (~0.26 GPM) LEADS as the gentle entry; misters (~1 GPM/zone) ESCALATE above it
    // only when fog can't hold the VPD target. LAYERED (Jason 2026-06-17): fog stays
    // ON through the mister stages (resolve_equipment drives out.fog for FOG/S1/S2).
    //   escalate: vpd > vpd_high + fog_escalation_kpa, sustained >= mist_s2_delay_ms (the
    //     existing planner-pushable stage-dwell tunable, now also the fog-only window — the
    //     AI tunes fog-escalation aggressiveness via it; a dedicated knob is a follow-up).
    //   de-escalate: vpd <= vpd_high + fog_escalation_kpa - MISTER_DEESCALATE_KPA (symmetric
    //   hysteresis, WS-B style, so the fog<->mister boundary cannot chatter).
    if (mode == SEALED_MIST && !entered_sealed_this_cycle) {
        state.mist_stage_timer_ms = sat_add(state.mist_stage_timer_ms, dt_ms);
        const float escalate_at   = sp.vpd_high + sp.fog_escalation_kpa;
        const float deescalate_at = escalate_at - MISTER_DEESCALATE_KPA;
        const bool  too_dry_for_misters = in.vpd_kpa > escalate_at;
        const bool  recovered = in.vpd_kpa <= deescalate_at || vpd_high_resolved;
        const bool  fog_gated = !climate_fog_assist_permitted(in, sp) || moisture_blocked;
        switch (state.mist_stage) {
            case MIST_WATCH:
                state.mist_stage = MIST_FOG;   // re-seed the entry if we somehow idled while sealed
                state.mist_stage_timer_ms = 0;
                break;
            case MIST_FOG:   // ENTRY — fog leads; misters join only if fog can't hold
                // Escalate after a fog-only dwell, OR immediately if fog is physically
                // gated (RH ceiling / dew / time) while still too dry — then misters are
                // the only humidifier available.
                if (too_dry_for_misters
                    && (state.mist_stage_timer_ms >= sp.mist_s2_delay_ms || fog_gated)) {
                    state.mist_stage = MIST_S1;
                    state.mist_stage_timer_ms = 0;
                }
                break;
            case MIST_S1:    // ESCALATE-1 misters single-zone
                if (too_dry_for_misters && state.mist_stage_timer_ms >= sp.mist_s2_delay_ms) {
                    state.mist_stage = MIST_S2;
                    state.mist_stage_timer_ms = 0;
                } else if (recovered) {
                    state.mist_stage = MIST_FOG;   // symmetric de-escalation back to fog-only
                    state.mist_stage_timer_ms = 0;
                }
                break;
            case MIST_S2:    // ESCALATE-2 all-zone rotation (heaviest water)
                if (recovered) {
                    state.mist_stage = MIST_S1;
                    state.mist_stage_timer_ms = 0;
                }
                break;
            default:
                state.mist_stage = MIST_WATCH;
                state.mist_stage_timer_ms = 0;
                break;
        }
    } else if (mode != SEALED_MIST && state.mist_stage != MIST_WATCH) {
        state.mist_stage = MIST_WATCH;
        state.mist_stage_timer_ms = 0;
    }

    state.vent_mist_assist_active =
        (mode == VENTILATE)
        && (selected_action == CLIMATE_VENT_COOL_MIST_ASSIST
            || selected_action == CLIMATE_VENT_COOL_FOG_ASSIST)
        && !moisture_blocked
        && !safety_cool
        && !safety_heat
        && in.temp_f < (sp.safety_max - sp.safety_max_seal_margin_f);

    // IRR-3/IRR-4: evaluate the CENTER-zone time-anchored cadence override.
    // SensorInputs carries only local_hour, so the FSM evaluates the window at
    // hour granularity (minute 0). This is the telemetry/replay-visible value;
    // controls.yaml re-evaluates with minute-precise SNTP before driving the
    // center pulse cadence (it has the minute the FSM does not). Safety modes
    // never burst — a burst is a routine wetting cadence, not a safety action.
    if (mode == SAFETY_COOL || mode == SAFETY_HEAT || mode == SENSOR_FAULT) {
        state.center_burst = CENTER_BURST_NONE;
    } else {
        state.center_burst = center_burst_decision(effective_now_minute(in), in, sp);
    }

    state.mode = mode;
    state.mode_prev = mode;
    return mode;
}

// ═══════════════════════════════════════════════════════════════════
// determine_mode()
// ═══════════════════════════════════════════════════════════════════
inline Mode determine_mode(
    const SensorInputs& in,
    const Setpoints& sp_raw,
    ControlState& state,
    uint32_t dt_ms
) {
    // Apply the optional control-band pinch once at controller entry. At the
    // ADR-0004 default (fraction 0) this is a no-op, so downstream decisions see
    // the served crop corridor.
    const Setpoints sp = apply_band_track_pinch(sp_raw);
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

    // SAF-1 / SF1: when the average RH/VPD probes are degraded the VPD is
    // fabricated — suppress VPD-CHASING entry (no seal-for-mist, no dehum-vent)
    // and force the exits TRUE so any in-flight VPD state unwinds. Temperature
    // control (safety, vent, heat) is untouched. (Legacy cascade mirror of the
    // band-first gating; production runs band-first, but the rollback path must
    // be safe too.)
    const bool vpd_trusted = vpd_control_trusted(in);
    bool safety_cool    = in.temp_f >= sp.safety_max;
    bool safety_heat    = in.temp_f <= sp.safety_min;
    bool vpd_above_band = vpd_trusted && in.vpd_kpa > vpd_high_eff;
    bool vpd_below_exit = !vpd_trusted || in.vpd_kpa < (vpd_high_eff - HV);

    bool vpd_too_low_enter = vpd_trusted && in.vpd_kpa < (vpd_low_eff - HV) && !sp.econ_block;
    bool vpd_dehum_exit    = !vpd_trusted || in.vpd_kpa >= vpd_low_eff;

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

        // SAF-1 / SF1: the R2-3 dry override is VPD-driven; suppress it when
        // the VPD is fabricated (degraded probes) so a guess cannot force a seal.
        if (vpd_trusted && in.vpd_kpa > sp.vpd_max_safe && can_seal_for_dryness) {
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
    // SAF-1 / SF1: the vpd_min_safe rescue is VPD-driven; gated on a trusted
    // reading so a fabricated low VPD cannot force DEHUM_VENT.
    if (vpd_trusted && in.vpd_kpa < sp.vpd_min_safe && (mode == IDLE || mode == SEALED_MIST)) {
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
    // Dwell-gate preview: default sp.sw_dwell_gate_enabled=false. The
    // accumulator still runs so operators can enable the gate without a
    // cold-start timing artifact after replay validation.
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
        // Update accumulator regardless of flag state so post-flip the first
        // transition has correct dwell timing.
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
                    const bool fog_gated = !climate_fog_assist_permitted(in, sp)
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

    // IRR-3/IRR-4: same center-burst evaluation as the band-first path, so the
    // legacy controller path keeps the field defined (no burst under safety).
    if (mode == SAFETY_COOL || mode == SAFETY_HEAT || mode == SENSOR_FAULT) {
        state.center_burst = CENTER_BURST_NONE;
    } else {
        state.center_burst = center_burst_decision(effective_now_minute(in), in, sp);
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
        && !fog_hour_permitted(in, sp);

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
        && climate_fog_assist_permitted(in, sp)
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
    const Setpoints& sp_raw,
    const ControlState& state,
    bool lead_is_fan1
) {
    // Match determine_mode's optional pinch. At the ADR-0004 default this is a
    // no-op and relay staging uses the served crop corridor.
    const Setpoints sp = apply_band_track_pinch(sp_raw);
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
            out.fog = climate_fog_assist_permitted(in, sp)
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
            // BC-14 fog-first (LAYERED, Jason 2026-06-17): fog is the gentle ENTRY
            // humidifier and PERSISTS through the mister escalation stages (it adds to,
            // not replaces, the misters). So fog runs at any active humidification stage
            // (FOG/S1/S2), still gated by the curve-only fog permit (RH ceiling / dew /
            // time / feed-hold). Misters are driven in controls.yaml off mist_stage>=MIST_S1.
            out.fog = (state.mist_stage == MIST_FOG
                       || state.mist_stage == MIST_S1
                       || state.mist_stage == MIST_S2)
                   && climate_fog_assist_permitted(in, sp);
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
            // BC-8 (ADR0003 §6.5): in band-first control fan2 is LATCHED in
            // determine_mode (state.fan2_latched) — a two-sided hysteresis twin of
            // heat2_latched, so it cannot chatter at the stage-2 edge (the 90s relay
            // min-off floor is a time dwell, not a temperature hysteresis). resolve_
            // equipment takes a const ControlState&, so it READS the latch (never
            // writes). Legacy path (fsm off, the test default) keeps the bare dC2
            // threshold. cool_all_fans_at_high is an explicit operator override that
            // still forces both fans immediately above the band edge, bypassing the latch.
            bool needs_both = (sp.sw_fsm_controller_enabled
                                   ? state.fan2_latched
                                   : in.temp_f > (Thigh + stage2_delta))
                           || (sp.cool_all_fans_at_high_enabled && in.temp_f > Thigh);
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
            if (in.vpd_kpa > (vpd_high_eff + sp.fog_escalation_kpa)) {
                out.fog = climate_fog_assist_permitted(in, sp);
            }
            break;
        }

        case DEHUM_VENT: {
            out.vent = true;
            const MoistureExchangeEstimate mx = estimate_moisture_exchange(in, sp);
            if (mx.action == MX_VENT_HUMIDIFY) {
                // BC-13 (ADR0003 §6.4) humid-import: too-dry indoor + wetter outdoor —
                // vent only, lead fan to move air; NO heat (heat raises VPD, the wrong
                // direction when importing moisture to LOWER it). No aggressive both-fans.
                if (lead_is_fan1) out.fan1 = true; else out.fan2 = true;
                break;
            }
            // BC-13: estimator-gated stage-1 heat-assist (heat1 only, NEVER heat2) —
            // replaces BC-5's unconditional co-run. Arms only after the min-dwell
            // (state.dehum_heat_assist_active, set in determine_mode once the estimator
            // proved heat+vent BOTH move VPD toward target) AND a real heating demand.
            // controls.yaml exempts DEHUM_VENT from the heat<->air interlock so this
            // co-runs with the vent (the sanctioned exception); SAFETY_HEAT owns the rail.
            if (state.dehum_heat_assist_active && needs_heating_s1) { out.heat1 = true; }
            // Aggressive dehum (both fans) when vpd is below the interior target minus
            // the aggressive margin; else the lead fan only.
            if (in.vpd_kpa < vpd_low_eff - sp.dehum_aggressive_kpa) {
                out.fan1 = true; out.fan2 = true;
            } else {
                if (lead_is_fan1) out.fan1 = true; else out.fan2 = true;
            }
            break;
        }

        case IDLE:
            if (needs_heating_s2) { out.heat1 = true; out.heat2 = true; }
            else if (needs_heating_s1) { out.heat1 = true; }
            else if (state.dehum_heat_assist_active
                     && climate_reason_is(state.last_mode_reason, "heat_dehum")) {
                out.heat1 = true;
            }
            // Econ-block VPD rescue: electric heat if VPD is below the
            // interior target AND temp is below the interior cooling
            // target minus econ_heat_margin_f. Same semantics as before,
            // retargeted to the band interior.
            //
            // ENV-2: suppress this path overnight. It is the only
            // heat-to-chase-humidity path in the controller; raising the
            // night VPD floor (DB diurnal curve) would make it fire across
            // the dark hours and erase the ≥10°F day/night drop the Vanda
            // needs. Safety heat (handled by SAFETY_HEAT mode) is untouched.
            // SAF-1 / SF1: this is the only VPD-driven heat path; suppress it
            // when the VPD is fabricated so degraded probes cannot chase
            // humidity with heat (also the ENV-2 night-suppression concern).
            if (vpd_control_trusted(in)
                && in.vpd_kpa < vpd_low_eff && sp.econ_block
                && in.temp_f < Thigh - sp.econ_heat_margin_f
                && !night_econ_heat_suppressed(in, sp)) {
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

// ── Manual button-override layer (FANS / HUMID / VENT-BYPASS) ────────────
// Applies the absolute-priority manual override on top of the resolved relay
// table. PRECEDENCE: the hard safety rails win — SENSOR_FAULT / SAFETY_COOL /
// SAFETY_HEAT are never overridden (a FANS press during a cold-rail SAFETY_HEAT
// must NOT throw both fans + the vent open and dump the heat). In every other
// mode the override SUPERSEDES climate automation:
//   FANS        → both fans ON, vent OPEN.
//   VENT-BYPASS → both fans ON, vent CLOSED (winter house-air pull). It implies
//                 fans, so the button alone produces the documented behavior.
//   HUMID       → fogger ON, unless a genuine fog safety block (dew margin / RH
//                 ceiling / temp floor / time window / leak) is active.
// Returns the ManualForce flags identifying which relays are force-applied so
// the caller passes force_on= and the press engages within one loop tick,
// bypassing relay min-off dwell (the #289 root-cause fix: a FANS press was
// applied with force_on=false and got stuck behind the fan min-off timer).
inline ManualForce apply_manual_overrides(RelayOutputs& out,
                                          const ManualOverrides& ov,
                                          Mode mode) noexcept {
    ManualForce f = {false, false, false};
    if (mode == SENSOR_FAULT || mode == SAFETY_COOL || mode == SAFETY_HEAT) {
        return f;  // safety rails are absolute — no manual override
    }
    const bool fans_requested = ov.fans_active || ov.vent_bypass_active;
    if (fans_requested) {
        out.fan1 = true;
        out.fan2 = true;
        f.fans = true;
        if (ov.vent_bypass_active) {
            out.vent = false;            // VENT-BYPASS: vent CLOSED, fans pull house air
        } else {
            out.vent = true;             // FANS: vent OPEN
            f.vent_open = true;
        }
    }
    if (ov.humid_active && !ov.fog_safety_blocked) {
        out.fog = true;
        f.fog = true;
    }
    return f;
}

// Whether the fan->vent interlock should force the vent OPEN this cycle. The
// interlock holds the vent open while fans run so a closed-vent fan does not
// leave the vent shut against the fan's airflow — EXCEPT (a) SAFETY_HEAT
// (lead-fan circulation with the vent shut to keep heat in) and (b) an active
// VENT-BYPASS (the deliberate fans-on / vent-closed carve-out, #290).
// SENSOR_FAULT drives every relay off elsewhere.
inline bool fan_requires_open_vent(Mode mode,
                                   bool fan_on_or_wanted,
                                   bool vent_bypass_active) noexcept {
    return mode != SENSOR_FAULT
        && mode != SAFETY_HEAT
        && !vent_bypass_active
        && fan_on_or_wanted;
}
