#pragma once
/*
 * invariants.h — firmware behavioral invariants enforced against replay
 * traces (originally 16; +#17-#20 Vanda band-compliance, +#21-#22 IRR-3/IRR-4
 * center-burst rails, +#24 curve-only fog gate, +#25 SAFETY_HEAT cold rail,
 * +#26 SENSOR_FAULT all-off). Each invariant is a pure function over a stream
 * of per-minute TraceRow records. First breach fails the replay run.
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
    // SAF-5 / FRT-6: fertilizer master valve state. Defaults to 0 when the
    // replay corpus does not export it (current C++ replay reconstructs
    // climate relays only; fertilizer relays live in controls.yaml). When
    // export-replay-overrides.sh adds an `eq_fertilizer_master` column the
    // SAF-5 / feed-hold invariants below become active over history.
    int eq_fertilizer_master;
    // FRT-6: absorption hold flag (controls.yaml computes now_ms <
    // feed_hold_until_ms). Default 0 absent the column.
    bool feed_hold_active;

    // Night-window / dusk-cutoff config the firmware was running with
    // (defaults applied when absent; see Setpoints). Used by the ENV-2
    // night-drop and dusk-cutoff invariants.
    int night_start_hour;
    int night_end_hour;
    int dusk_cutoff_hour;
    bool dusk_cutoff_enabled;

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

// #18 (SAF-5): the fogger is clean-water-only by plumbing (no fog_fert relay).
// Fertilizer must NEVER reach the AquaFog. Positive interlock: fog_rly and
// fertilizer_master_valve are mutually exclusive. (Vacuously true until the
// corpus exports eq_fertilizer_master; active thereafter.)
inline bool check_18_fog_fert_exclusive(const TraceRow& r, ReportFn report = default_report) {
    if (r.eq_fog && r.eq_fertilizer_master) {
        report(18, "fog_fert_exclusive", r,
               "fog_rly ON while fertilizer_master_valve ON (salts could reach fogger)");
        return false;
    }
    return true;
}

// #19 (FRT-6): during the post-feed absorption hold, no clean center wetting.
// fertilizer_master ON OR the hold flag set ⇒ center_mister and fog_rly must
// be OFF so the velamen absorbs the feed before any rinse. (Single-row form;
// the windowed companion #20 enforces it for the full hold duration.)
inline bool check_19_feed_hold_no_clean_center(const TraceRow& r, ReportFn report = default_report) {
    const bool feeding_or_holding = r.eq_fertilizer_master || r.feed_hold_active;
    if (feeding_or_holding && (r.eq_mister_center || r.eq_fog)) {
        report(19, "feed_hold_no_clean_center", r,
               "center_mister/fog ON while fertilizer_master ON or feed-hold active");
        return false;
    }
    return true;
}

// Build the minimal SensorInputs/Setpoints needed to evaluate the IRR-3/IRR-4
// center-burst RAIL gate from a TraceRow. Only the rail-relevant fields are
// populated (the dusk-cutoff, feed-hold, dew-margin, over-saturation, and
// occupancy preconditions); the dawn/midday WINDOW config is intentionally
// left at defaults because #21/#22 assert rails that are window-independent.
// Firmware-v2: populate the on-chip solar inputs on a SensorInputs from a
// replay row's unix timestamp (the same ephemeris the live firmware computes).
inline void set_solar_inputs_from_row(const TraceRow& r, SensorInputs& in) {
    const int utc_off = infer_utc_offset_min(r.ts_unix_s, r.local_hour);
    const SolarTimes st = solar_times_from_unix(r.ts_unix_s, utc_off);
    in.now_minute     = local_minute_from_unix(r.ts_unix_s, utc_off);
    in.sunrise_min    = st.sunrise_min;
    in.solar_noon_min = st.solar_noon_min;
    in.sunset_min     = st.sunset_min;
    in.solar_phase    = solar_phase(in.now_minute, st);
}

inline void irr_burst_rail_view(const TraceRow& r, SensorInputs& in, Setpoints& sp) {
    sp = default_setpoints();
    sp.vpd_high = r.vpd_high;
    sp.vpd_low  = r.vpd_low;
    sp.feed_hold_active = r.feed_hold_active;
    in = SensorInputs{};
    in.temp_f = r.temp_f;
    in.rh_pct = r.rh_pct;
    in.vpd_kpa = r.vpd_kpa;
    in.dew_point_f = r.dew_point_f;
    in.local_hour = r.local_hour;
    in.occupied = r.occupied;
    in.outdoor_temp_f = NAN;
    in.outdoor_dewpoint_f = NAN;
    in.outdoor_data_age_s = 99999u;
    set_solar_inputs_from_row(r, in);
}

// #21 (firmware-v2): a CENTER-zone dawn/midday boost can NEVER be permitted
// past the solar wet taper. center_burst_rails_permit() must be false on any
// row that is past_wet_taper() (within taper of the on-chip sunset, or in the
// solar night half), regardless of VPD. Proves the boost respects the
// dry-before-dark rail across the full history, on solar time.
inline bool check_21_center_burst_pre_dusk(const TraceRow& r, ReportFn report = default_report) {
    SensorInputs in; Setpoints sp;
    irr_burst_rail_view(r, in, sp);
    if (!sensors_plausible(in)) return true;             // SENSOR_FAULT handled elsewhere
    if (past_wet_taper(in, sp) && center_burst_rails_permit(in, sp)) {
        report(21, "center_burst_pre_taper", r,
               "center-boost rails permitted past the solar wet taper");
        return false;
    }
    return true;
}

// #22 (IRR-3/IRR-4 × FRT-6): a CENTER-zone dawn/midday burst can NEVER be
// permitted during the post-feed absorption hold. center_burst_rails_permit()
// must be false whenever feed_hold_active. (Vacuously true until the corpus
// exports feed_hold_active; active thereafter — same as #19/#20.)
inline bool check_22_center_burst_no_feed_hold(const TraceRow& r, ReportFn report = default_report) {
    SensorInputs in; Setpoints sp;
    irr_burst_rail_view(r, in, sp);
    if (!sensors_plausible(in)) return true;
    if (r.feed_hold_active && center_burst_rails_permit(in, sp)) {
        report(22, "center_burst_no_feed_hold", r,
               "IRR-3/IRR-4 center-burst rails permitted during the absorption hold");
        return false;
    }
    return true;
}

// #23 (ENV-5 / M14): MIN-DARK GUARANTEE. The supplemental-lighting controller
// must never be able to drive the grow lights for more than 18 hours of the
// day, i.e. it must leave >=6h of guaranteed dark. This is a CONFIG-level
// property over LightingSetpoints, not a per-climate-row check (the replay
// corpus carries no lighting columns). It sweeps all 24 local hours under the
// adversarial assumption that the greenhouse is occupied with low exterior lux
// at EVERY hour (the worst case for the occupancy task-light branch that M14
// gated) and counts how many hours `evaluate_lighting` would permit the light.
// Pre-M14 the occupancy branch omitted in_window, so an all-occupied day could
// light all 24 hours → 0h dark (verified empirically: grow lights ON at 23:49
// local). Post-M14 the permitted hours equal the photoperiod window width,
// which must be <= 18.
inline bool check_23_min_dark(const LightingSetpoints& sp_in,
                              ReportFn report = default_report) {
    LightingSetpoints sp = sp_in;
    validate_lighting_setpoints(sp);
    int permitted_hours = 0;
    for (int hour = 0; hour < 24; hour++) {
        LightingInputs in{};
        in.natural_lux = 0.0f;          // pitch dark indoors
        in.exterior_lux = 0.0f;         // pitch dark outdoors → exterior_lux_below_on
        in.exterior_lux_fresh = true;   // fresh so the occupancy branch is eligible
        in.local_hour = hour;
        in.occupied = true;             // adversarial: someone present every hour
        LightingState st = initial_lighting_state();
        // current_on=false: ask whether the controller would TURN the light on.
        LightingDecision d = evaluate_lighting(in, sp, st, false, 120000u, 60.0f);
        if (d.want_on) permitted_hours++;
    }
    const int dark_hours = 24 - permitted_hours;
    if (dark_hours < 6) {
        TraceRow row{};
        row.local_hour = 0;
        char detail[160];
        std::snprintf(detail, sizeof(detail),
            "lighting can be ON %d/24h (window %d..%d) → only %dh dark (<6h min)",
            permitted_hours, sp.start_hour, sp.cutoff_hour, dark_hours);
        report(23, "min_dark", row, detail);
        return false;
    }
    return true;
}

// #24 (CURVE-ONLY: replaces the deleted CYC-4 overnight-micro-pulse rule):
// FOG NEVER FIRES BELOW THE BAND HIGH EDGE. With the dusk/night fog taper, the
// clock window, and the dedicated overnight micro-pulse path all gone, the band
// curve IS the schedule: routine fog is legal at ANY hour (day or night
// identically) but ONLY when actual VPD is ABOVE the band's high edge (the curve
// gate) — i.e. the air is genuinely too dry for the served band. Survival
// cooling (SAFETY_COOL) is the only exception (evaporative fog as a heat-rail
// aid). Fog firing at/below vpd_high is the regression this guards: a properly
// shaped (high-vpd_high) night band keeps nights dry precisely BECAUSE this rule
// holds, so a curve change that let fog run on already-humid air would breach
// here. This is a single-row property; the per-minute corpus cannot see sub-
// minute pulse length, so it asserts the legality of fog being on at all, which
// is the dark-dry / curve-fidelity property that matters. Applies every hour,
// not just overnight — the day/night symmetry is the whole point of curve-only.
inline bool check_24_fog_requires_curve_gate(const TraceRow& r,
                                             ReportFn report = default_report) {
    if (!r.eq_fog) return true;
    SensorInputs in;
    in = SensorInputs{};
    in.temp_f = r.temp_f;
    in.rh_pct = r.rh_pct;
    in.vpd_kpa = r.vpd_kpa;
    in.dew_point_f = r.dew_point_f;
    in.local_hour = r.local_hour;
    in.occupied = r.occupied;
    in.outdoor_temp_f = NAN;
    in.outdoor_dewpoint_f = NAN;
    in.outdoor_data_age_s = 99999u;
    set_solar_inputs_from_row(r, in);

    if (!sensors_plausible(in)) return true;       // SENSOR_FAULT handled elsewhere
    // Survival cooling fog is exempt: at the safety_max rail evaporative fog is a
    // heat-rail aid, not band humidity control, so it may run regardless of VPD.
    const bool safety_cooling = mode_is_safety_cool(r.greenhouse_state);
    // The curve gate: fog is only legal when actual VPD is ABOVE the band high
    // edge (climate_fog/wet_assist's "below_threshold" rail). At/below vpd_high
    // the air is already inside the served band and no fog should fire.
    const bool above_band_high = r.vpd_kpa > r.vpd_high;
    if (!above_band_high && !safety_cooling) {
        report(24, "fog_requires_curve_gate", r,
               "fog ON with VPD at/below the band high edge outside SAFETY_COOL "
               "(curve-only: fog must follow the band curve, not a clock/phase gate)");
        return false;
    }
    return true;
}

// #25: SAFETY_HEAT always engaged when temp <= safety_min — the cold-rail
// symmetric twin of #7. The determine_mode rail preempt
// (greenhouse_logic.h:683-691) is a strict ladder:
//   SENSOR_FAULT  >  SAFETY_COOL (temp >= safety_max)  >
//   SAFETY_HEAT (temp <= safety_min)  >  climate candidate.
// So below the cold rail — when sensors are trustworthy and we are not already
// at/above the hot rail — the mode MUST be SAFETY_HEAT. Excludes SENSOR_FAULT
// (higher-precedence rail; relays all-off by design) and the degenerate
// safety_min >= safety_max misconfig (the `temp_f < safety_max` guard). Light
// on the spring corpus (the house is heated) but a standing guard so a future
// edit can never silently demote the cold rail below climate arbitration.
inline bool check_25_safety_heat_engaged(const TraceRow& r, ReportFn report = default_report) {
    if (r.greenhouse_state == "SENSOR_FAULT") return true;   // higher-precedence rail
    if (!std::isfinite(r.temp_f) || !std::isfinite(r.safety_min)) return true;
    if (r.temp_f <= r.safety_min && r.temp_f < r.safety_max
        && r.greenhouse_state != "SAFETY_HEAT") {
        report(25, "safety_heat_must_engage", r,
               "temp <= safety_min but mode != SAFETY_HEAT");
        return false;
    }
    return true;
}

// #26: SENSOR_FAULT means ALL relays OFF. With no trustworthy sensor feedback
// the controller drives nothing (greenhouse_logic.h:2007-2010); freeze
// protection is an out-of-band hardware thermostat wired in parallel, never
// blind software. Any relay ON while the recorded mode is SENSOR_FAULT is a
// fault. (Vacuous on corpora with no SENSOR_FAULT rows — a standing guard like
// #18/#19; it pins the all-off contract so a future executor change cannot let
// an actuator run during a sensor blackout.)
inline bool check_26_sensor_fault_all_off(const TraceRow& r, ReportFn report = default_report) {
    if (r.greenhouse_state != "SENSOR_FAULT") return true;
    if (r.eq_fog || r.eq_vent || r.eq_fan1 || r.eq_fan2 || r.eq_heat1 || r.eq_heat2
        || r.eq_mister_south || r.eq_mister_west || r.eq_mister_center) {
        report(26, "sensor_fault_all_off", r,
               "relay ON while mode == SENSOR_FAULT (must be all-off)");
        return false;
    }
    return true;
}

// #27: heat <-> air-exchange exclusivity. The deterministic controller must never
// run a heater together with the vent or a fan EXCEPT in two sanctioned states:
// SAFETY_HEAT (lead fan for canopy circulation, vent closed) and DEHUM_VENT (BC-5
// bounded stage-1 heat-assist holding the temp floor while dehumidifying). This is
// the pure-replay codification of the controls.yaml heat<->air interlock (BC-11),
// so a regression that re-opens heater-vs-vent/fan fighting fails CI offline, not
// only on-device. heat2 with the vent is never allowed (DEHUM_VENT runs heat1 only).
inline bool check_27_heat_air_exchange_exclusive(const TraceRow& r, ReportFn report = default_report) {
    const bool heating = r.eq_heat1 || r.eq_heat2;
    const bool air_exchange = r.eq_vent || r.eq_fan1 || r.eq_fan2;
    if (!heating || !air_exchange) return true;
    if (r.greenhouse_state == "SAFETY_HEAT") return true;                  // circulation fan, vent closed
    if (r.greenhouse_state == "DEHUM_VENT" && !r.eq_heat2) return true;    // BC-5 stage-1 heat-assist
    report(27, "heat_air_exchange_exclusive", r,
           "heater ON with vent/fan outside SAFETY_HEAT / DEHUM_VENT-stage1");
    return false;
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

// #17 (ENV-2): NIGHT-DROP INVARIANT. The served band must preserve a ≥10°F
// day/night drop — the night temp_low must sit at least 10°F below the day's
// peak served temp_high. This is the firmware companion to the DB diurnal
// curve raising the night VPD floor: without a guaranteed drop, raising night
// humidity would flatten the diurnal cycle the Vanda needs to silver its
// velamen overnight. Implemented per local-day: track the day-peak temp_high
// (over daytime rows), and for night-window rows assert
//   night temp_low <= day_peak_temp_high − 10.
// A 1°F slack absorbs interpolation/rounding at the band endpoints.
struct Ctx17 {
    uint64_t day_bucket = 0;
    float    day_peak_high = -1e9f;   // max temp_high seen in daytime rows this day
    bool     have_peak = false;
};
inline bool check_17_night_drop(Ctx17& c, const TraceRow& r, ReportFn report = default_report) {
    // FORWARD invariant. The ≥10°F day/night drop is a property of the NEW
    // solar diurnal band, not of the pre-fix telemetry. The historical replay
    // corpus served the OLD broken band (night ~66.5 vs day-peak ~75 → only
    // ~8.5°F) and is EXPECTED to violate this. Gated on the new-band config
    // being present (the night_*_hour columns survive in the corpus only when
    // it was refreshed under the new band). Night is now derived from SOLAR
    // PHASE, not a clock window. The native unit test exercises it directly.
    const bool new_band_config = !(r.night_start_hour == 0 && r.night_end_hour == 0);
    if (!new_band_config) return true;

    const uint64_t day = r.ts_unix_s / 86400ULL;
    if (day != c.day_bucket) {
        c.day_bucket = day;
        c.day_peak_high = -1e9f;
        c.have_peak = false;
    }
    SensorInputs nin{};
    nin.local_hour = r.local_hour;
    set_solar_inputs_from_row(r, nin);
    const bool night = is_night_phase(nin);
    if (!night) {
        if (r.temp_high > c.day_peak_high) { c.day_peak_high = r.temp_high; c.have_peak = true; }
        return true;
    }
    // Night row: require ≥10°F drop from the established day peak. Skip until
    // we have at least one daytime sample so cold-start nights are not flagged.
    if (c.have_peak && r.temp_low > c.day_peak_high - 10.0f + 1.0f) {
        char detail[160];
        std::snprintf(detail, sizeof(detail),
            "night temp_low %.1f°F not >=10°F below day-peak temp_high %.1f°F",
            r.temp_low, c.day_peak_high);
        report(17, "night_drop", r, detail);
        return false;
    }
    return true;
}

// #20 (FRT-6 windowed): once a fertilizer feed/hold begins, no clean center
// wetting (center_mister/fog) may fire until the hold clears. Tracks the
// holding state across rows so a mid-hold clean pulse is caught even on a row
// where the fert master has already closed but the absorption hold persists.
struct Ctx20 { bool holding = false; };
inline bool check_20_feed_hold_window(Ctx20& c, const TraceRow& r, ReportFn report = default_report) {
    const bool holding_now = r.eq_fertilizer_master || r.feed_hold_active;
    if (holding_now) c.holding = true;
    else c.holding = false;
    if (c.holding && (r.eq_mister_center || r.eq_fog)) {
        report(20, "feed_hold_window", r,
               "clean center_mister/fog fired during active feed/absorption hold");
        return false;
    }
    return true;
}

// ─────────────────────────────────────────────────────────────────────────
// Public entry point — iterate all 16 invariants over a trace.
// Returns 0 on pass, non-zero = count of violated invariants.
// ─────────────────────────────────────────────────────────────────────────
struct Runner {
    Ctx4 c4; Ctx5 c5; Ctx6 c6; Ctx8 c8; Ctx10 c10; Ctx12 c12; Ctx13 c13; Ctx14 c14;
    Ctx17 c17; Ctx20 c20;
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
        // ── New Vanda-band-compliance invariants ──
        if (!check_17_night_drop(c17, r, report))                   { failures++; ok = false; }
        if (!check_18_fog_fert_exclusive(r, report))                { failures++; ok = false; }
        if (!check_19_feed_hold_no_clean_center(r, report))         { failures++; ok = false; }
        if (!check_20_feed_hold_window(c20, r, report))             { failures++; ok = false; }
        // ── IRR-3 / IRR-4 center-burst rail invariants ──
        if (!check_21_center_burst_pre_dusk(r, report))             { failures++; ok = false; }
        if (!check_22_center_burst_no_feed_hold(r, report))         { failures++; ok = false; }
        // ── Curve-only fog gate (replaces the deleted CYC-4 micro-pulse rule) ──
        if (!check_24_fog_requires_curve_gate(r, report))           { failures++; ok = false; }
        // ── Cold-rail + sensor-fault safety rails (L2 #344 AC4 test-rail) ──
        if (!check_25_safety_heat_engaged(r, report))               { failures++; ok = false; }
        if (!check_26_sensor_fault_all_off(r, report))              { failures++; ok = false; }
        if (!check_27_heat_air_exchange_exclusive(r, report))       { failures++; ok = false; }
        return ok;
    }
};

}  // namespace invariants
