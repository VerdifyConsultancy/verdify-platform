#pragma once
/*
 * invariants.h — 16 firmware behavioral invariants enforced against replay
 * traces. Each invariant is a pure function over a stream of per-minute
 * TraceRow records. First breach fails the replay run.
 *
 * See plan file at .claude-agents/iris-dev/plans/yo-iris-dev-you-help-humming-stonebraker.md
 * Appendix A for the canonical list + rationale.
 *
 * Design notes:
 *   - Invariants are PROPERTY checks over rolling windows of the replay,
 *     not unit tests. Catastrophic breach (invariant violated) produces a
 *     first-offending-row report and returns false.
 *   - Data-driven thresholds (e.g. "≤30 transitions/hr in stable conditions")
 *     are hard-coded here for simplicity; derive p99 × 1.5 from 30-day
 *     baseline and update these constants when corpus changes seasonally.
 *   - Pure functions: no globals, no I/O outside the report callback.
 *
 * Not all invariants require the full SensorInputs/Setpoints/ControlState
 * tuple — some need only equipment state + mode + computed thresholds.
 * Each check_*(…) takes the minimum it needs.
 */

#include "greenhouse_logic.h"
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>

namespace invariants {

// ─────────────────────────────────────────────────────────────────────────
// TraceRow — one per minute (or CSV row cadence), built from replay CSV.
// Fields match the Phase-0-extended CSV schema.
// ─────────────────────────────────────────────────────────────────────────
struct TraceRow {
    // Time
    uint64_t ts_unix_s;      // parsed from CSV ts column
    int      local_hour;     // 0-23 MDT

    // Climate
    float temp_f;
    float rh_pct;
    float vpd_kpa;
    float dew_point_f;       // indoor

    // Outdoor (may be NaN if data missing)
    float outdoor_temp_f;
    float outdoor_rh_pct;
    float outdoor_dewpoint_f;
    int   outdoor_data_age_s;  // -1 if NULL in CSV
    float solar_w_m2;

    // Setpoints (band) — what the firmware was configured with
    float temp_low, temp_high;
    float vpd_low,  vpd_high;
    float temp_hysteresis, vpd_hysteresis;
    float vpd_max_safe;      // aka safety_vpd_max
    float vpd_min_safe;      // aka safety_vpd_min
    float safety_max;
    float safety_min;
    float bias_heat, bias_cool;
    float fog_escalation_kpa;
    float fog_rh_ceiling;
    float fog_min_temp;
    uint32_t sealed_max_ms;
    uint32_t relief_duration_ms;
    uint32_t outdoor_staleness_max_s;

    // State (observed from telemetry)
    std::string greenhouse_state;  // "SEALED_MIST_S1"/"VENTILATE"/...
    std::string mode_reason;       // sprint-15.1 diagnostic
    bool summer_vent_active;        // determine_mode() set override_summer_vent
    bool vent_mist_assist_active;   // band-first controller humidification assist while ventilating

    // Equipment (0/1)
    int eq_fog, eq_vent, eq_fan1, eq_fan2, eq_heat1, eq_heat2;
    int eq_mister_south, eq_mister_west, eq_mister_center;

    bool occupied;
};

using ReportFn = void(*)(int invariant_id, const char* name,
                         const TraceRow& row, const char* detail);

inline void default_report(int id, const char* name, const TraceRow& row, const char* detail) {
    std::fprintf(stderr,
        "INVARIANT FAIL #%02d %s at ts_unix=%llu: %s\n",
        id, name, (unsigned long long)row.ts_unix_s, detail);
}

// ─────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────
inline bool mode_is_sealed(const std::string& s) {
    return s.rfind("SEALED_MIST", 0) == 0;
}
inline bool mode_is_ventilate(const std::string& s) { return s == "VENTILATE"; }
inline bool mode_is_safety_cool(const std::string& s) { return s == "SAFETY_COOL"; }
inline bool mode_is_thermal_relief(const std::string& s) { return s == "THERMAL_RELIEF"; }
inline bool mode_is_idle(const std::string& s) { return s == "IDLE"; }

// ─────────────────────────────────────────────────────────────────────────
// Single-row invariants — evaluated independently on each row.
// Return true = this row passes. Report emitted on first failure.
// ─────────────────────────────────────────────────────────────────────────

// #1: fog never fires with vent open except in explicit ventilation/safety
// cooling paths where fog is serving an active high-VPD demand.
inline bool check_1_fog_vent_exclusive(const TraceRow& r, ReportFn report = default_report) {
    if (r.eq_fog && r.eq_vent) {
        const float vpd_width = std::max(0.2f, r.vpd_high - r.vpd_low);
        const float vpd_high_eff = r.vpd_high - vpd_width * 0.25f;
        const bool fw9b_assist = mode_is_ventilate(r.greenhouse_state)
                              && r.vpd_kpa > (vpd_high_eff + r.fog_escalation_kpa);
        const bool safety_cool_emergency = mode_is_safety_cool(r.greenhouse_state)
                                        && r.vpd_kpa > 0.5f * r.vpd_max_safe;
        if (!fw9b_assist && !safety_cool_emergency) {
            report(1, "fog_vent_exclusive", r,
                   "fog AND vent simultaneously ON outside FW-9b/SAFETY_COOL emergency");
            return false;
        }
    }
    return true;
}

// #2: mister_* only fires in SEALED_MIST (or safety cool for mist-fog edge)
inline bool check_2_mister_only_sealed(const TraceRow& r, ReportFn report = default_report) {
    const bool any_mister = r.eq_mister_south || r.eq_mister_west || r.eq_mister_center;
    const bool vent_mist_assist_ok = mode_is_ventilate(r.greenhouse_state)
                                  && r.vent_mist_assist_active;
    if (any_mister && !mode_is_sealed(r.greenhouse_state) && !vent_mist_assist_ok) {
        report(2, "mister_only_in_sealed", r,
               "mister_* ON in non-SEALED_MIST mode");
        return false;
    }
    return true;
}

// #3: heat1/heat2 never fires when temp > temp_high
// NOTE: Relaxed slightly — firmware can have a cooldown/relay-bounce within
// min_heat_off window. Invariant fails only if heat was ON for >60s at
// temp > temp_high + 1°F. Single-row form here flags the egregious case.
inline bool check_3_heat_off_when_hot(const TraceRow& r, ReportFn report = default_report) {
    if ((r.eq_heat1 || r.eq_heat2) && r.temp_f > r.temp_high + 1.0f) {
        report(3, "heat_off_when_hot", r,
               "heat_* ON while temp > temp_high+1°F");
        return false;
    }
    return true;
}

// #7: SAFETY_COOL always engaged when temp >= safety_max
inline bool check_7_safety_cool_engaged(const TraceRow& r, ReportFn report = default_report) {
    if (r.temp_f >= r.safety_max && !mode_is_safety_cool(r.greenhouse_state)) {
        report(7, "safety_cool_must_engage", r,
               "temp >= safety_max but mode != SAFETY_COOL");
        return false;
    }
    return true;
}

// #9: override_summer_vent never fires when outdoor_data_age_s >= outdoor_staleness_max_s
inline bool check_9_summer_vent_requires_fresh_outdoor(const TraceRow& r, ReportFn report = default_report) {
    if (r.summer_vent_active
        && r.outdoor_data_age_s >= 0
        && (uint32_t)r.outdoor_data_age_s >= r.outdoor_staleness_max_s) {
        report(9, "summer_vent_stale_outdoor", r,
               "override_summer_vent true with stale outdoor data");
        return false;
    }
    return true;
}

// #11: fog + heat is allowed only as a sealed cold/dry assist. The useful
// overlap is: SEALED_MIST_FOG, high VPD demand still present, below the upper
// temp band, and under the configured fog RH ceiling. Anything outside that
// envelope is contradictory actuator output.
inline bool check_11_fog_heat_exclusive(const TraceRow& r, ReportFn report = default_report) {
    if (r.eq_fog && (r.eq_heat1 || r.eq_heat2)) {
        const float vpd_width = std::max(0.2f, r.vpd_high - r.vpd_low);
        const float vpd_high_eff = r.vpd_high - vpd_width * 0.25f;
        const bool sealed_fog_stage = r.greenhouse_state == "SEALED_MIST_FOG";
        const bool assist_envelope =
            mode_is_sealed(r.greenhouse_state)
            && sealed_fog_stage
            && r.vpd_kpa > vpd_high_eff
            && r.temp_f < r.temp_high
            && r.rh_pct <= r.fog_rh_ceiling
            && r.temp_f >= r.fog_min_temp;
        if (!assist_envelope) {
            report(11, "fog_heat_assist_envelope", r,
                   "fog+heat outside sealed cold/dry assist envelope");
            return false;
        }
    }
    return true;
}

// #15: equipment-mode consistency — mode implies relay set.
// Loose form: flag gross violations (e.g., IDLE with fan1=ON sustained).
// Tight form: resolve_equipment(mode, in, sp, st, lead) matches eq_* bitmask.
// For now, use loose form; the tight form requires reconstructing ControlState
// and setpoints, which replay_diff.cpp already does per-row via determine_mode.
inline bool check_15_mode_equipment_consistent(const TraceRow& r, ReportFn report = default_report) {
    if (mode_is_idle(r.greenhouse_state) && (r.eq_fan1 || r.eq_fan2 || r.eq_vent || r.eq_fog
                                              || r.eq_mister_south || r.eq_mister_west || r.eq_mister_center)) {
        report(15, "idle_with_active_relay", r,
               "mode=IDLE but fan/vent/fog/mister ON");
        return false;
    }
    return true;
}

// #16: heat2 is a second-stage heat output and is never valid without heat1.
// Gas heat without the primary heat path is a staging/executor fault, not a
// tuning posture.
inline bool check_16_heat2_requires_heat1(const TraceRow& r, ReportFn report = default_report) {
    if (r.eq_heat2 && !r.eq_heat1) {
        report(16, "heat2_requires_heat1", r, "heat2 ON while heat1 OFF");
        return false;
    }
    return true;
}

// ─────────────────────────────────────────────────────────────────────────
// Windowed invariants — evaluated over rolling windows. Helpers maintain
// per-check state via a small context struct. Caller iterates rows and
// invokes each window_check with the context + row.
// ─────────────────────────────────────────────────────────────────────────

// #4: no SEALED_MIST hold > sealed_max_ms. Tracks consecutive sealed rows.
struct Ctx4 { uint64_t sealed_entry_ts = 0; bool in_sealed = false; };
inline bool check_4_sealed_max_timeout(Ctx4& c, const TraceRow& r, ReportFn report = default_report) {
    const bool now_sealed = mode_is_sealed(r.greenhouse_state);
    if (now_sealed && !c.in_sealed) {
        c.sealed_entry_ts = r.ts_unix_s;
        c.in_sealed = true;
    } else if (!now_sealed) {
        c.in_sealed = false;
    } else {
        // still sealed
        const uint64_t elapsed_ms = (r.ts_unix_s - c.sealed_entry_ts) * 1000ULL;
        // Allow 10s slack for transition-log lag.
        if (elapsed_ms > r.sealed_max_ms + 10000ULL) {
            report(4, "sealed_max_exceeded", r, "SEALED_MIST continuous > sealed_max_ms");
            return false;
        }
    }
    return true;
}

// #5: IDLE never selected when temp > temp_high + hysteresis for > 5 min continuous
struct Ctx5 { uint64_t first_bad_ts = 0; bool tracking = false; };
inline bool check_5_no_idle_when_overshoot(Ctx5& c, const TraceRow& r, ReportFn report = default_report) {
    const bool overshoot_idle = mode_is_idle(r.greenhouse_state)
                             && r.temp_f > r.temp_high + std::max(r.temp_hysteresis, r.bias_cool);
    if (overshoot_idle) {
        if (!c.tracking) { c.first_bad_ts = r.ts_unix_s; c.tracking = true; }
        else if (r.ts_unix_s - c.first_bad_ts > 300) {  // 5 min
            report(5, "idle_during_overshoot", r,
                   "IDLE held > 5 min while temp > temp_high + hysteresis");
            return false;
        }
    } else {
        c.tracking = false;
    }
    return true;
}

// #6: mode transitions ≤ 30/hour in stable conditions (stdev(temp) < 0.5°F over hour)
// Approximation: count distinct greenhouse_state values per hour bucket.
// Stable condition: temp range in hour < 3°F. Threshold from p99 × 1.5 of
// 30-day baseline (Plan C derivation — refresh when corpus advances).
struct Ctx6 {
    uint64_t hour_start_ts = 0;
    int      transitions_this_hour = 0;
    std::string last_mode;
    float    min_t = 1e9f, max_t = -1e9f;
};
inline bool check_6_transition_cap(Ctx6& c, const TraceRow& r, ReportFn report = default_report) {
    const uint64_t hour_bucket = r.ts_unix_s / 3600ULL;
    const uint64_t cur_hour = c.hour_start_ts / 3600ULL;
    if (hour_bucket != cur_hour) {
        // hour boundary — emit check for the completed hour, then reset
        const bool was_stable = (c.max_t - c.min_t) < 3.0f;
        const bool was_capacity_exceeded = c.transitions_this_hour > 30;
        bool ok = true;
        if (was_stable && was_capacity_exceeded) {
            char detail[160];
            std::snprintf(detail, sizeof(detail),
                "%d mode transitions in stable hour (range %.1f°F)",
                c.transitions_this_hour, c.max_t - c.min_t);
            report(6, "transition_cap", r, detail);
            ok = false;
        }
        c.hour_start_ts = r.ts_unix_s;
        c.transitions_this_hour = 0;
        c.min_t = r.temp_f; c.max_t = r.temp_f;
        c.last_mode = r.greenhouse_state;
        if (!ok) return false;
    } else {
        if (r.greenhouse_state != c.last_mode) {
            c.transitions_this_hour++;
            c.last_mode = r.greenhouse_state;
        }
        if (r.temp_f < c.min_t) c.min_t = r.temp_f;
        if (r.temp_f > c.max_t) c.max_t = r.temp_f;
    }
    return true;
}

// #8: THERMAL_RELIEF exits within sp.relief_duration_ms + slack
struct Ctx8 { uint64_t relief_entry_ts = 0; bool in_relief = false; };
inline bool check_8_thermal_relief_duration(Ctx8& c, const TraceRow& r, ReportFn report = default_report) {
    const bool now_relief = mode_is_thermal_relief(r.greenhouse_state);
    if (now_relief && !c.in_relief) {
        c.relief_entry_ts = r.ts_unix_s; c.in_relief = true;
    } else if (!now_relief) {
        c.in_relief = false;
    } else {
        const uint64_t elapsed_ms = (r.ts_unix_s - c.relief_entry_ts) * 1000ULL;
        // Slack: 2x expected duration to account for log lag + successive relief cycles.
        if (elapsed_ms > 2 * r.relief_duration_ms) {
            report(8, "thermal_relief_stuck", r,
                   "THERMAL_RELIEF held > 2x relief_duration_ms");
            return false;
        }
    }
    return true;
}

// #10: any equipment toggle is attributable to a changed state/reason or to
//      a known actuator-context reason from determine_mode().
struct Ctx10 {
    bool initialized = false;
    int prev_eq_bitmask = 0;
    std::string prev_mode;
    std::string prev_reason;
};
inline bool check_10_equipment_toggle_auditable(Ctx10& c, const TraceRow& r, ReportFn report = default_report) {
    const int cur_eq = (r.eq_fog << 0) | (r.eq_vent << 1) | (r.eq_fan1 << 2)
                     | (r.eq_fan2 << 3) | (r.eq_heat1 << 4) | (r.eq_heat2 << 5)
                     | (r.eq_mister_south << 6) | (r.eq_mister_west << 7) | (r.eq_mister_center << 8);
    if (!c.initialized) {
        c.initialized = true;
        c.prev_eq_bitmask = cur_eq;
        c.prev_mode = r.greenhouse_state;
        c.prev_reason = r.mode_reason;
        return true;
    }
    if (cur_eq != c.prev_eq_bitmask) {
        // Equipment changed; mode/state or reason must have changed, OR the
        // reason must identify a known dwell/override path that can legally
        // change relay output without changing the top-level state label.
        const bool mode_changed = r.greenhouse_state != c.prev_mode;
        const bool reason_changed = r.mode_reason != c.prev_reason;
        const bool reason_auditable = r.mode_reason == "dehum_continue"
                                   || r.mode_reason == "dry_override"
                                   || r.mode_reason == "dwell_expired"
                                   || r.mode_reason == "dwell_hold"
                                   || r.mode_reason == "fog_continue"
                                   || r.mode_reason == "fog_enter"
                                   || r.mode_reason == "heat_stage1"
                                   || r.mode_reason == "heat_stage2"
                                   || r.mode_reason == "humidify_continue"
                                   || r.mode_reason == "humidify_enter"
                                   || r.mode_reason == "humidify_resolved"
                                   || r.mode_reason == "idle"
                                   || r.mode_reason == "idle_default"
                                   || r.mode_reason == "mist_backoff"
                                   || r.mode_reason == "moisture_blocked"
                                   || r.mode_reason == "relief_cycle_breaker"
                                   || r.mode_reason == "safety_cool"
                                   || r.mode_reason == "safety_heat"
                                   || r.mode_reason == "seal_continue"
                                   || r.mode_reason == "seal_enter"
                                   || r.mode_reason == "seal_exit"
                                   || r.mode_reason == "sensor_fault"
                                   || r.mode_reason == "summer_vent"
                                   || r.mode_reason == "summer_vent_preempt"
                                   || r.mode_reason == "temp_high"
                                   || r.mode_reason == "temp_preempts_humidify"
                                   || r.mode_reason == "thermal_relief"
                                   || r.mode_reason == "thermal_relief_forced"
                                   || r.mode_reason == "vent_fog_assist"
                                   || r.mode_reason == "vent_mist_assist"
                                   || r.mode_reason == "vpd_low"
                                   || r.mode_reason == "vpd_min_safe_rescue"
                                   || r.mode_reason == "vpd_too_low";
        if (!mode_changed && !reason_changed && !reason_auditable) {
            report(10, "equipment_toggle_without_reason", r,
                   "relay bitmask changed without a changed state, changed reason, or auditable mode_reason");
            return false;
        }
    }
    c.prev_eq_bitmask = cur_eq;
    c.prev_mode = r.greenhouse_state;
    c.prev_reason = r.mode_reason;
    return true;
}

// #12: MIST_S2 only reachable from MIST_S1 (no level-skipping)
struct Ctx12 { std::string prev_state; };
inline bool check_12_mist_progression(Ctx12& c, const TraceRow& r, ReportFn report = default_report) {
    if (r.greenhouse_state == "SEALED_MIST_S2"
        && c.prev_state != "SEALED_MIST_S1"
        && c.prev_state != "SEALED_MIST_S2"
        && c.prev_state != "SEALED_MIST_FOG") {
        // Allow entering S2 only via S1, or by staying at S2/FOG. Other
        // entries (IDLE→S2, VENTILATE→S2) indicate level-skipping.
        report(12, "mist_level_skip", r, "entered SEALED_MIST_S2 without passing S1");
        return false;
    }
    c.prev_state = r.greenhouse_state;
    return true;
}

// #14: vent open/close cycles ≤ 12/day on days outdoor_temp_f < temp_low - 10 continuously
struct Ctx14 {
    uint64_t day_bucket = 0;
    int vent_open_cycles = 0;
    int prev_vent = 0;
    bool day_was_cold = true;
    bool day_has_outdoor = false;
};
inline bool check_14_vent_cold_day_cap(Ctx14& c, const TraceRow& r, ReportFn report = default_report) {
    const uint64_t day = r.ts_unix_s / 86400ULL;
    if (day != c.day_bucket) {
        // day transition: emit check for completed day, reset
        bool ok = true;
        if (c.day_has_outdoor && c.day_was_cold && c.vent_open_cycles > 12) {
            char detail[120];
            std::snprintf(detail, sizeof(detail),
                "%d vent open cycles on cold day", c.vent_open_cycles);
            report(14, "vent_cold_day_thrash", r, detail);
            ok = false;
        }
        c.day_bucket = day;
        c.vent_open_cycles = 0;
        c.day_was_cold = true;
        c.day_has_outdoor = false;
        c.prev_vent = r.eq_vent;
        if (!ok) return false;
    }
    // Per-row: accumulate toggles + check cold condition
    if (r.eq_vent && !c.prev_vent) c.vent_open_cycles++;
    c.prev_vent = r.eq_vent;
    if (std::isnan(r.outdoor_temp_f)) {
        c.day_was_cold = false;
    } else {
        c.day_has_outdoor = true;
        if (r.outdoor_temp_f >= r.temp_low - 10.0f) {
            c.day_was_cold = false;
        }
    }
    return true;
}

// #13 — dry_override_active must clear within vpd_dry_override_max_ms of setting.
// Not currently observable from replay CSV (would need ControlState snapshot).
// Deferred to replay_diff.cpp which has full ControlState; leave a stub here.
struct Ctx13 { /* unused in CSV-only replay */ };
inline bool check_13_dry_override_clear(Ctx13& /*c*/, const TraceRow& /*r*/) { return true; }

// ─────────────────────────────────────────────────────────────────────────
// Public entry point — iterate all 16 invariants over a trace.
// Returns 0 on pass, non-zero = count of violated invariants.
// ─────────────────────────────────────────────────────────────────────────
struct Runner {
    Ctx4 c4; Ctx5 c5; Ctx6 c6; Ctx8 c8; Ctx10 c10; Ctx12 c12; Ctx13 c13; Ctx14 c14;
    int failures = 0;

    bool run(const TraceRow& r, ReportFn report = default_report) {
        bool ok = true;
        if (!check_1_fog_vent_exclusive(r, report))                 { failures++; ok = false; }
        if (!check_2_mister_only_sealed(r, report))                 { failures++; ok = false; }
        if (!check_3_heat_off_when_hot(r, report))                  { failures++; ok = false; }
        if (!check_4_sealed_max_timeout(c4, r, report))             { failures++; ok = false; }
        if (!check_5_no_idle_when_overshoot(c5, r, report))         { failures++; ok = false; }
        if (!check_6_transition_cap(c6, r, report))                 { failures++; ok = false; }
        if (!check_7_safety_cool_engaged(r, report))                { failures++; ok = false; }
        if (!check_8_thermal_relief_duration(c8, r, report))        { failures++; ok = false; }
        if (!check_9_summer_vent_requires_fresh_outdoor(r, report)) { failures++; ok = false; }
        if (!check_10_equipment_toggle_auditable(c10, r, report))   { failures++; ok = false; }
        if (!check_11_fog_heat_exclusive(r, report))                { failures++; ok = false; }
        if (!check_12_mist_progression(c12, r, report))             { failures++; ok = false; }
        check_13_dry_override_clear(c13, r);   // deferred
        if (!check_14_vent_cold_day_cap(c14, r, report))            { failures++; ok = false; }
        if (!check_15_mode_equipment_consistent(r, report))         { failures++; ok = false; }
        if (!check_16_heat2_requires_heat1(r, report))              { failures++; ok = false; }
        return ok;
    }
};

}  // namespace invariants
