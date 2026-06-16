/*
 * replay_emit.cpp — Emit per-row mode decisions for a single firmware ref.
 * Reads Phase-0-extended replay CSV, drives determine_mode()+resolve_equipment()
 * forward, prints one TSV row per input row with the firmware's computed mode
 * and relay bitmask.
 *
 * Output consumed by replay_diff.sh (dual-ref wrapper) to produce HEAD-vs-base
 * diffs. Also runs invariants.h against the firmware's own output as a sanity
 * check (catches firmware bugs irrespective of reference comparison).
 *
 * This file is the single-ref "emitter". The diff wrapper builds TWO copies
 * (one from old ref, one from new ref via git worktree) and compares their
 * outputs offline. That sidesteps the header-namespacing problem described
 * in the Plan A report — simpler and more robust than dual-compile tricks.
 *
 * Compile: g++ -std=c++17 -I../lib -o replay_emit replay_emit.cpp
 * Run:     ./replay_emit data/replay_overrides.csv > trace.tsv
 * Output TSV columns:
 *   ts, mode, relay_bitmask, mist_stage, last_mode_reason,
 *   override_flags_bitmask
 *
 * --stream / follow build (digital-twin Phase 0, TWIN-1/TWIN-2):
 *   When compiled with -DREPLAY_EMIT_STREAM the binary (built as
 *   `replay_emit_follow`) keeps one resident ControlState and blocks on stdin
 *   instead of exiting at EOF, so a long-running twin driver can feed one
 *   telemetry line per tick and read back one decision row. The stream build
 *   also appends a `climate_action` column derived from the existing
 *   describe_effective_climate_decision() (greenhouse_logic.h) — no
 *   translation table.
 *
 *   This is gated entirely behind the build flag: the stock `replay_emit`
 *   binary (no -DREPLAY_EMIT_STREAM) is byte-for-byte unchanged — same 11
 *   columns, same code path — so the rule-8 firmware-replay-diff CI gate,
 *   firmware-invariants, and test-firmware are unaffected.
 *
 *   Compile: g++ -std=c++17 -DREPLAY_EMIT_STREAM -I../lib \
 *                -o replay_emit_follow replay_emit.cpp
 *   Run:     ./replay_emit_follow --stream --header-from data/replay_overrides.csv
 *            (then write one TSV data line per tick on stdin; one decision row
 *             is flushed per line. A header CSV path may also be passed
 *             positionally, in which case its data rows prime the resident
 *             state before stdin follow begins.)
 *
 *   This file is the OFFLINE replay harness only — it never touches the ESP32
 *   firmware path and is not part of any OTA artifact.
 */

#include "greenhouse_logic.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <unordered_map>
#include <vector>
#include <cmath>
#include <ctime>

static float parse_float(const std::string& s, float def) {
    if (s.empty() || s == "\\N" || s == "NULL") return def;
    try { return std::stof(s); } catch (...) { return def; }
}
static int parse_int(const std::string& s, int def) {
    if (s.empty() || s == "\\N" || s == "NULL") return def;
    try { return std::stoi(s); } catch (...) { return def; }
}
static bool parse_bool(const std::string& s, bool def) {
    if (s.empty() || s == "\\N" || s == "NULL") return def;
    return s == "t" || s == "true" || s == "1";
}
// TWIN-3 (#31): switch setpoints are forward-filled from setpoint_snapshot,
// whose `value` column is FLOAT — so an enabled switch arrives as "1" / "1.0"
// and disabled as "0" / "0.0". parse_bool() above only matches the literal
// "1"/"true"/"t", so use a float-threshold parser for the snapshot-sourced
// sp_sw_* columns. Empty / NULL keeps the firmware default.
static bool parse_bool_float(const std::string& s, bool def) {
    if (s.empty() || s == "\\N" || s == "NULL") return def;
    if (s == "t" || s == "true") return true;
    if (s == "f" || s == "false") return false;
    try { return std::stof(s) >= 0.5f; } catch (...) { return def; }
}

struct Header {
    std::unordered_map<std::string, size_t> idx;
    void parse(const std::string& line) {
        std::istringstream ss(line);
        std::string col;
        size_t i = 0;
        while (std::getline(ss, col, '\t')) idx[col] = i++;
    }
    size_t of(const std::string& name) const {
        auto it = idx.find(name);
        return it == idx.end() ? SIZE_MAX : it->second;
    }
};

// ── TWIN-3 (#31): setpoint-coverage assertion ────────────────────────────────
// Single source of truth for the sp_* columns the current Setpoints struct
// expects from the replay CSV. Every dispatcher-pushed field with an active
// greenhouse_logic.h path is here. Fields that are default-only or
// firmware-derived (econ_block, the ENV-2 night window, CYC-1 dusk cutoff,
// FRT-6 feed_hold_active, IRR-3 dawn_rehydrate_start_hour) are intentionally
// absent — the twin agrees with the firmware on them by construction, so there
// is no sp_* column to require.
static const char* const EXPECTED_SP_COLUMNS[] = {
    // pre-existing coverage (the original ~17)
    "sp_temp_low", "sp_temp_high", "sp_vpd_low", "sp_vpd_high",
    "sp_bias_cool", "sp_bias_heat", "sp_vpd_hysteresis", "sp_temp_hysteresis",
    "sp_safety_max", "sp_safety_min", "sp_vpd_max_safe", "sp_vpd_min_safe",
    "sp_fog_escalation_kpa", "sp_watch_dwell_s", "sp_mist_backoff_s",
    "sp_mist_s2_delay_s", "sp_sw_fsm_controller_enabled",
    // TWIN-3 additions
    "sp_heat_hysteresis", "sp_sealed_max_s", "sp_relief_duration_s",
    "sp_max_relief_cycles", "sp_fog_rh_ceiling", "sp_fog_min_temp",
    "sp_dehum_aggressive_kpa",
    "sp_occupancy_inhibit", "sp_vent_latch_timeout_ms",
    "sp_safety_max_seal_margin_f", "sp_econ_heat_margin_f",
    "sp_sw_summer_vent_enabled", "sp_vent_prefer_temp_delta_f",
    "sp_vent_prefer_dp_delta_f", "sp_outdoor_staleness_max_s",
    "sp_summer_vent_min_runtime_s", "sp_sw_dwell_gate_enabled", "sp_dwell_gate_ms",
    "sp_cool_stage2_over_high_f", "sp_cool_exit_hysteresis_f",
    "sp_cold_vent_guard_delta_f", "sp_cool_all_fans_at_high_enabled",
    "sp_direct_wet_stress_override_enabled", "sp_direct_wet_stress_vpd_margin_kpa",
    "sp_direct_wet_stress_min_dew_margin_f",
    // Firmware-v2: solar wet taper + night-stress emergency wetting (replaces
    // the direct_wet_stress_latest_hour / fog_stress_window_* clock windows).
    "sp_sw_wet_taper_enabled", "sp_wet_taper_before_sunset_min",
    "sp_sw_night_stress_wet_enabled", "sp_night_stress_min_dew_margin_f",
    // Firmware-v2: solar-anchored dawn/midday boost windows (offsets replace the
    // dawn_rehydrate_start_hour / midday_drench_hour clock anchors).
    "sp_sw_dawn_rehydrate_enabled", "sp_dawn_boost_offset_min",
    "sp_dawn_rehydrate_window_min", "sp_dawn_rehydrate_on_s", "sp_dawn_rehydrate_gap_s",
    "sp_sw_midday_drench_enabled", "sp_midday_boost_offset_min",
    "sp_midday_drench_window_min", "sp_midday_drench_on_s", "sp_midday_drench_gap_s",
};

// Warns loudly (and, when REPLAY_EMIT_REQUIRE_FULL_SETPOINTS=1, aborts) if the
// CSV header is missing any sp_* column the Setpoints struct expects. The live
// twin driver sets the env var so a schema drift (a new dispatcher-pushed field
// the export forgot to add) fails loudly instead of silently defaulting and
// manufacturing false divergence — the exact failure mode TWIN-3 closes. The
// offline rule-8 replay-diff runs against the prior corpus (which legitimately
// predates these columns), so the default is a non-fatal warning: additive
// coverage must never break the freeze gate.
static int check_setpoint_coverage(const Header& h) {
    std::vector<std::string> missing;
    for (const char* col : EXPECTED_SP_COLUMNS) {
        if (h.of(col) == SIZE_MAX) missing.push_back(col);
    }
    if (missing.empty()) return 0;
    const char* require = std::getenv("REPLAY_EMIT_REQUIRE_FULL_SETPOINTS");
    const bool hard = require && *require && *require != '0';
    std::fprintf(stderr,
        "[replay_emit] %s: %zu sp_* column(s) the Setpoints struct expects are "
        "absent from the CSV header (TWIN-3 setpoint-coverage). These fields will "
        "fall back to default_setpoints() and can manufacture false divergence:\n",
        hard ? "FATAL" : "WARNING", missing.size());
    for (const auto& m : missing) std::fprintf(stderr, "    - %s\n", m.c_str());
    if (hard) {
        std::fprintf(stderr,
            "[replay_emit] aborting: REPLAY_EMIT_REQUIRE_FULL_SETPOINTS is set and "
            "the export is missing columns. Re-run scripts/export-replay-overrides.sh.\n");
        return 1;
    }
    return 0;
}

static uint64_t parse_ts_unix(const std::string& s) {
    if (s.size() < 19) return 0;
    struct tm tm{};
    if (sscanf(s.c_str(), "%d-%d-%d %d:%d:%d",
               &tm.tm_year, &tm.tm_mon, &tm.tm_mday,
               &tm.tm_hour, &tm.tm_min, &tm.tm_sec) != 6) return 0;
    tm.tm_year -= 1900;
    tm.tm_mon -= 1;
    return (uint64_t)timegm(&tm);
}

// MODE_NAMES is already defined in greenhouse_types.h (included via greenhouse_logic.h)

// process_row advances the resident ControlState by exactly one telemetry row
// and prints one TSV decision line. The logic is identical for the batch path
// and the stream/follow path — only the call site differs (a file loop vs a
// stdin loop). `emit_climate_action` appends the additive climate_action
// column derived from describe_effective_climate_decision(); it is only set on
// the -DREPLAY_EMIT_STREAM build so the stock batch output stays byte-for-byte
// identical and the rule-8 firmware-replay-diff gate is unaffected.
static void process_row(const Header& h,
                        const std::string& line,
                        ControlState& state,
                        uint64_t& last_ts_unix,
                        bool emit_climate_action) {
    std::vector<std::string> cols;
    {
        std::istringstream ss(line);
        std::string tok;
        while (std::getline(ss, tok, '\t')) cols.push_back(std::move(tok));
    }
    auto get = [&](const std::string& name, const std::string& def = "") -> std::string {
        size_t i = h.of(name);
        return (i == SIZE_MAX || i >= cols.size()) ? def : cols[i];
    };

    SensorInputs in{};
        in.temp_f = parse_float(get("temp_avg"), 70.0f);
        in.rh_pct = parse_float(get("rh_avg"), 50.0f);
        in.vpd_kpa = parse_float(get("vpd_avg"), 0.8f);
        in.dew_point_f = parse_float(get("indoor_dew_point"), in.temp_f - 10.0f);
        in.enthalpy_delta = parse_float(get("enthalpy_delta"), -5.0f);
        in.solar_w_m2 = parse_float(get("solar_irradiance_w_m2"), 0.0f);
        // zone vpds unavailable in this CSV; use avg
        in.vpd_south = in.vpd_kpa; in.vpd_west = in.vpd_kpa;
        in.vpd_east = in.vpd_kpa;
        const std::string ts = get("ts");
        int hr = 12;
        if (ts.size() >= 13) { try { hr = std::stoi(ts.substr(11, 2)); } catch (...) {} }
        in.local_hour = hr;
        // ── Firmware-v2: on-chip solar inputs from the row timestamp ──────────
        // Every time-of-day rule now keys on SOLAR PHASE. Derive the per-cycle
        // ephemeris (sunrise/solar-noon/sunset + phase + minute-of-day) from the
        // row's unix time exactly as the on-chip ESP32 path would, so the replay
        // twin gates on real solar phase instead of the fallback hour proxy.
        {
            const uint64_t row_ts_unix = parse_ts_unix(ts);
            if (row_ts_unix > 0) {
                const int utc_off = infer_utc_offset_min(row_ts_unix, hr);
                const SolarTimes st = solar_times_from_unix(row_ts_unix, utc_off);
                in.now_minute     = local_minute_from_unix(row_ts_unix, utc_off);
                in.sunrise_min    = st.sunrise_min;
                in.solar_noon_min = st.solar_noon_min;
                in.sunset_min     = st.sunset_min;
                in.solar_phase    = solar_phase(in.now_minute, st);
            }
        }
        in.occupied = parse_bool(get("occupied"), false);
        in.outdoor_temp_f = parse_float(get("outdoor_temp_f"), NAN);
        in.outdoor_dewpoint_f = parse_float(get("outdoor_dewpoint_f"), NAN);
        int age = parse_int(get("outdoor_data_age_s"), -1);
        in.outdoor_data_age_s = (age < 0) ? 99999u : (uint32_t)age;

        Setpoints sp = default_setpoints();
        auto assign_positive_float = [&](const std::string& name, float& field) {
            float value = parse_float(get(name), NAN);
            if (!std::isnan(value) && value > 0.0f) field = value;
        };
        auto assign_float = [&](const std::string& name, float& field) {
            float value = parse_float(get(name), NAN);
            if (!std::isnan(value)) field = value;
        };
        assign_positive_float("sp_temp_low", sp.temp_low);
        assign_positive_float("sp_temp_high", sp.temp_high);
        assign_positive_float("sp_vpd_low", sp.vpd_low);
        assign_positive_float("sp_vpd_high", sp.vpd_high);
        assign_float("sp_bias_cool", sp.bias_cool);
        assign_float("sp_bias_heat", sp.bias_heat);
        assign_positive_float("sp_vpd_hysteresis", sp.vpd_hysteresis);
        assign_positive_float("sp_temp_hysteresis", sp.temp_hysteresis);
        assign_positive_float("sp_safety_max", sp.safety_max);
        assign_positive_float("sp_safety_min", sp.safety_min);
        assign_positive_float("sp_vpd_max_safe", sp.vpd_max_safe);
        assign_positive_float("sp_vpd_min_safe", sp.vpd_min_safe);
        assign_positive_float("sp_fog_escalation_kpa", sp.fog_escalation_kpa);
        float watch_dwell_s = parse_float(get("sp_watch_dwell_s"), NAN);
        if (!std::isnan(watch_dwell_s) && watch_dwell_s > 0.0f) {
            sp.vpd_watch_dwell_ms = (uint32_t)(watch_dwell_s * 1000.0f);
        }
        float mist_backoff_s = parse_float(get("sp_mist_backoff_s"), NAN);
        if (!std::isnan(mist_backoff_s) && mist_backoff_s > 0.0f) {
            sp.mist_backoff_ms = (uint32_t)(mist_backoff_s * 1000.0f);
        }
        float mist_s2_delay_s = parse_float(get("sp_mist_s2_delay_s"), NAN);
        if (!std::isnan(mist_s2_delay_s) && mist_s2_delay_s > 0.0f) {
            sp.mist_s2_delay_ms = (uint32_t)(mist_s2_delay_s * 1000.0f);
        }

        // ── TWIN-3 (#31): close the setpoint-coverage gap ──────────────────
        // Wire every remaining dispatcher-pushed Setpoints field from its new
        // sp_* column. These are ADDITIVE: a column absent from the CSV header
        // (e.g. the older checked-in corpus) makes get() return "" and the
        // field keeps its default_setpoints() value — byte-for-byte identical
        // to the prior behavior, so the rule-8 replay-diff stays
        // THRESHOLD_PCT=0 green. The fields only bind when a freshly-exported
        // corpus carries them. Mirrors controls.yaml's Setpoints construction:
        // *_s columns are seconds (×1000 → ms), *_ms columns are already ms.
        auto assign_int = [&](const std::string& name, int& field) {
            float value = parse_float(get(name), NAN);
            if (!std::isnan(value)) field = (int)value;
        };
        auto assign_positive_u32_from_s = [&](const std::string& name, uint32_t& field) {
            float secs = parse_float(get(name), NAN);
            if (!std::isnan(secs) && secs > 0.0f) field = (uint32_t)(secs * 1000.0f);
        };
        auto assign_positive_u32_ms = [&](const std::string& name, uint32_t& field) {
            float ms = parse_float(get(name), NAN);
            if (!std::isnan(ms) && ms > 0.0f) field = (uint32_t)ms;
        };
        auto assign_positive_u32 = [&](const std::string& name, uint32_t& field) {
            float value = parse_float(get(name), NAN);
            if (!std::isnan(value) && value >= 0.0f) field = (uint32_t)value;
        };
        auto assign_switch = [&](const std::string& name, bool& field) {
            field = parse_bool_float(get(name), field);
        };

        assign_positive_float("sp_heat_hysteresis", sp.heat_hysteresis);
        assign_positive_u32_from_s("sp_sealed_max_s", sp.sealed_max_ms);
        assign_positive_u32_from_s("sp_relief_duration_s", sp.relief_duration_ms);
        assign_positive_u32("sp_max_relief_cycles", sp.max_relief_cycles);
        assign_positive_float("sp_fog_rh_ceiling", sp.fog_rh_ceiling);
        assign_positive_float("sp_fog_min_temp", sp.fog_min_temp);
        assign_positive_float("sp_dehum_aggressive_kpa", sp.dehum_aggressive_kpa);
        assign_switch("sp_occupancy_inhibit", sp.occupancy_inhibit);
        assign_positive_u32_ms("sp_vent_latch_timeout_ms", sp.vent_latch_timeout_ms);
        assign_positive_float("sp_safety_max_seal_margin_f", sp.safety_max_seal_margin_f);
        assign_positive_float("sp_econ_heat_margin_f", sp.econ_heat_margin_f);
        assign_switch("sp_sw_summer_vent_enabled", sp.sw_summer_vent_enabled);
        assign_positive_float("sp_vent_prefer_temp_delta_f", sp.vent_prefer_temp_delta_f);
        assign_positive_float("sp_vent_prefer_dp_delta_f", sp.vent_prefer_dp_delta_f);
        assign_positive_u32("sp_outdoor_staleness_max_s", sp.outdoor_staleness_max_s);
        assign_positive_u32("sp_summer_vent_min_runtime_s", sp.summer_vent_min_runtime_s);
        assign_switch("sp_sw_dwell_gate_enabled", sp.sw_dwell_gate_enabled);
        assign_positive_u32_ms("sp_dwell_gate_ms", sp.dwell_gate_ms);
        assign_float("sp_cool_stage2_over_high_f", sp.cool_stage2_over_high_f);
        assign_positive_float("sp_cool_exit_hysteresis_f", sp.cool_exit_hysteresis_f);
        assign_float("sp_cold_vent_guard_delta_f", sp.cold_vent_guard_delta_f);
        assign_switch("sp_cool_all_fans_at_high_enabled", sp.cool_all_fans_at_high_enabled);
        assign_switch("sp_direct_wet_stress_override_enabled", sp.direct_wet_stress_override_enabled);
        assign_float("sp_direct_wet_stress_vpd_margin_kpa", sp.direct_wet_stress_vpd_margin_kpa);
        assign_positive_float("sp_direct_wet_stress_min_dew_margin_f", sp.direct_wet_stress_min_dew_margin_f);
        // Firmware-v2: solar wet taper + night-stress emergency wetting.
        assign_switch("sp_sw_wet_taper_enabled", sp.sw_wet_taper_enabled);
        assign_int("sp_wet_taper_before_sunset_min", sp.wet_taper_before_sunset_min);
        assign_switch("sp_sw_night_stress_wet_enabled", sp.sw_night_stress_wet_enabled);
        assign_positive_float("sp_night_stress_min_dew_margin_f", sp.night_stress_min_dew_margin_f);
        // Firmware-v2: solar-anchored dawn/midday boost windows.
        assign_switch("sp_sw_dawn_rehydrate_enabled", sp.sw_dawn_rehydrate_enabled);
        assign_int("sp_dawn_boost_offset_min", sp.dawn_boost_offset_min);
        assign_int("sp_dawn_rehydrate_window_min", sp.dawn_rehydrate_window_min);
        assign_int("sp_dawn_rehydrate_on_s", sp.dawn_rehydrate_on_s);
        assign_int("sp_dawn_rehydrate_gap_s", sp.dawn_rehydrate_gap_s);
        assign_switch("sp_sw_midday_drench_enabled", sp.sw_midday_drench_enabled);
        assign_int("sp_midday_boost_offset_min", sp.midday_boost_offset_min);
        assign_int("sp_midday_drench_window_min", sp.midday_drench_window_min);
        assign_int("sp_midday_drench_on_s", sp.midday_drench_on_s);
        assign_int("sp_midday_drench_gap_s", sp.midday_drench_gap_s);

        sp.sw_fsm_controller_enabled = parse_bool(
            get("sp_sw_fsm_controller_enabled"),
            sp.sw_fsm_controller_enabled
        );
        // Production ESPHome forces the unified band-first controller ON before
        // every control tick. Keep replay aligned by default; set
        // REPLAY_EMIT_FORCE_FSM=0 only for explicit historical forensics.
        const char* force_fsm = std::getenv("REPLAY_EMIT_FORCE_FSM");
        if (!force_fsm || !*force_fsm || *force_fsm != '0') {
            sp.sw_fsm_controller_enabled = true;
        }
        // Phase-2 preview hook: DWELL_ENABLED=1 env var flips the dwell-gate
        // master switch + bumps temp_hysteresis to 2.0°F (the two bundled
        // Phase-2 knobs). Default off — run with flag on to see projected
        // whipsaw reduction against the same corpus/same setpoints.
        static const bool dwell_preview_on = []{
            const char* e = std::getenv("DWELL_ENABLED");
            return e && *e && *e != '0';
        }();
        if (dwell_preview_on) {
            sp.sw_dwell_gate_enabled = true;
            sp.temp_hysteresis = 2.0f;
        }
        // ── Band-curve behavioral test mode (REPLAY_EMIT_BAND_DERIVE=1) ────────
        // The corpus path above feeds the band from recorded sp_* columns, so it
        // NEVER exercises band_value_at_phase() — a change to the band CURVE shows
        // ZERO replay divergence (the exact gap that let the lumpy/wet-night curve
        // ship blind). This mode DERIVES the band on-chip-style from
        // band_value_at_phase() at each row's reconstructed solar phase, using
        // fixed representative anchors, so a change to the curve math
        // (cosine-ease → harmonic, an anchor-resolution change, etc.) produces a
        // real mode/relay diff. Off by default → the stock replay-diff stays
        // byte-identical at THRESHOLD_PCT=0.
        static const bool band_derive = []{
            const char* e = std::getenv("REPLAY_EMIT_BAND_DERIVE");
            return e && *e && *e != '0';
        }();
        if (band_derive) {
            // Fixed test-fixture anchors {SR, SM, SS, MID} spanning the realistic
            // diurnal range. The VALUES are fixtures (stable across band edits);
            // only the band_value_at_phase() math under test varies between refs.
            const BandAnchors temp_low_a {60.0f, 76.0f, 66.0f, 62.0f};
            const BandAnchors temp_high_a{72.0f, 86.0f, 80.0f, 70.0f};
            const BandAnchors vpd_low_a  {0.90f, 0.95f, 0.90f, 0.88f};
            const BandAnchors vpd_high_a {1.25f, 1.50f, 1.25f, 1.22f};
            const float ph = in.solar_phase;
            sp.temp_low  = band_value_at_phase(temp_low_a,  ph);
            sp.temp_high = band_value_at_phase(temp_high_a, ph);
            sp.vpd_low   = band_value_at_phase(vpd_low_a,   ph);
            sp.vpd_high  = band_value_at_phase(vpd_high_a,  ph);
        }
        // validate_setpoints applies firmware clamps
        validate_setpoints(sp);

        uint64_t ts_unix = parse_ts_unix(ts);
        uint64_t delta_s = (last_ts_unix > 0 && ts_unix > last_ts_unix)
            ? ts_unix - last_ts_unix
            : 60;
        if (delta_s > 600) {
            state = initial_state();
            delta_s = 60;
        }
        if (delta_s > UINT32_MAX / 1000ULL) delta_s = UINT32_MAX / 1000ULL;
        const uint32_t dt_ms = (uint32_t)(delta_s * 1000ULL);
        last_ts_unix = ts_unix;

        // Advance state machine
        Mode mode = determine_mode(in, sp, state, dt_ms);
        RelayOutputs r = resolve_equipment(mode, in, sp, state, true);
        OverrideFlags of = evaluate_overrides(in, sp, state, mode);

        const char* reason = state.last_mode_reason ? state.last_mode_reason : "";
        int override_bits = (of.occupancy_blocks_equipment << 0) | (of.fog_gate_rh << 1)
                          | (of.fog_gate_temp << 2) | (of.fog_gate_window << 3)
                          | (of.relief_cycle_breaker << 4) | (of.seal_blocked_temp << 5)
                          | (of.vpd_dry_override << 6) | (of.summer_vent_active << 7)
                          | (of.fog_heat_assist << 8) | (of.vent_mist_assist << 9);

        if (emit_climate_action) {
            // Additive twin column (TWIN-2). Reuse the firmware's own mapping —
            // describe_effective_climate_decision() in greenhouse_logic.h — so the
            // twin's climate_action joins 1:1 against climate_action_log with no
            // translation table.
            const ClimateActionDecision decision =
                describe_effective_climate_decision(mode, in, sp, state, r);
            std::printf("%s\t%s\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%s\t%d\t%s\n",
                        ts.c_str(),
                        MODE_NAMES[(int)mode],
                        r.fog, r.vent, r.fan1, r.fan2, r.heat1, r.heat2,
                        (int)state.mist_stage,
                        reason,
                        override_bits,
                        CLIMATE_ACTION_NAMES[(int)decision.climate_action]);
        } else {
            std::printf("%s\t%s\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%s\t%d\n",
                        ts.c_str(),
                        MODE_NAMES[(int)mode],
                        r.fog, r.vent, r.fan1, r.fan2, r.heat1, r.heat2,
                        (int)state.mist_stage,
                        reason,
                        override_bits);
        }
}

#ifdef REPLAY_EMIT_STREAM
// ── Stream / follow build (TWIN-1) ───────────────────────────────────────────
// Gated entirely behind -DREPLAY_EMIT_STREAM (built as `replay_emit_follow`).
// Keeps one resident ControlState and blocks on stdin instead of exiting at
// EOF, so a long-running twin driver can feed one telemetry line per tick and
// read back one decision row. Emits the additive climate_action column.
//
// Header source: from the positional CSV argument's first line, or from
// `--header-from <csv>`. If a positional CSV is given, its DATA rows are
// replayed first to prime the resident ControlState before stdin follow
// begins (so a driver can hand the harness a warm-up window then stream).

static int usage_stream(const char* argv0) {
    std::fprintf(stderr,
        "Usage: %s [--stream] [--header-from <csv> | <csv>]\n"
        "  --stream            follow stdin (default in this build)\n"
        "  --header-from <csv> read the TSV header from <csv>, do not replay its rows\n"
        "  <csv>               read header from <csv> AND replay its data rows to\n"
        "                      prime ControlState, then follow stdin\n"
        "Reads one TSV telemetry line per stdin line; flushes one decision row\n"
        "(with the climate_action column) per line. Resident ControlState.\n",
        argv0);
    return 2;
}

int main(int argc, char** argv) {
    std::string header_csv;     // CSV used only for its header line
    std::string prime_csv;      // CSV whose data rows prime ControlState
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--stream" || arg == "--follow") {
            continue;  // follow is the only mode this build offers
        } else if (arg == "--header-from") {
            if (i + 1 >= argc) return usage_stream(argv[0]);
            header_csv = argv[++i];
        } else if (arg == "-h" || arg == "--help") {
            return usage_stream(argv[0]);
        } else if (arg[0] == '-') {
            std::fprintf(stderr, "Unknown flag: %s\n", arg.c_str());
            return usage_stream(argv[0]);
        } else {
            prime_csv = arg;          // positional CSV: header + prime rows
            if (header_csv.empty()) header_csv = arg;
        }
    }
    if (header_csv.empty()) {
        std::fprintf(stderr, "Stream build needs a header: pass <csv> or --header-from <csv>.\n");
        return usage_stream(argv[0]);
    }

    Header h;
    {
        std::ifstream hf(header_csv);
        if (!hf) { std::fprintf(stderr, "Cannot open %s\n", header_csv.c_str()); return 2; }
        std::string header_line;
        if (!std::getline(hf, header_line)) { std::fprintf(stderr, "Empty header CSV\n"); return 2; }
        h.parse(header_line);
    }
    if (check_setpoint_coverage(h) != 0) return 3;

    ControlState state = initial_state();
    uint64_t last_ts_unix = 0;

    // Header includes the additive climate_action column.
    std::printf("ts\tmode\trelay_fog\trelay_vent\trelay_fan1\trelay_fan2\trelay_heat1\trelay_heat2\tmist_stage\treason\toverride_bits\tclimate_action\n");
    std::fflush(stdout);

    // Prime: replay the data rows of the positional CSV (if any) to warm up
    // ControlState before following stdin.
    long primed = 0;
    if (!prime_csv.empty()) {
        std::ifstream pf(prime_csv);
        if (!pf) { std::fprintf(stderr, "Cannot open %s\n", prime_csv.c_str()); return 2; }
        std::string line;
        std::getline(pf, line);  // discard header
        while (std::getline(pf, line)) {
            process_row(h, line, state, last_ts_unix, /*emit_climate_action=*/true);
            primed++;
        }
        std::fflush(stdout);
    }

    // Follow: block on stdin, one decision row per input line, flush each tick
    // so a driver reading the pipe sees the result immediately.
    std::string line;
    long ticks = 0;
    while (std::getline(std::cin, line)) {
        if (line.empty()) continue;
        process_row(h, line, state, last_ts_unix, /*emit_climate_action=*/true);
        std::fflush(stdout);
        ticks++;
    }

    std::fprintf(stderr, "replay_emit_follow: primed %ld, streamed %ld rows\n", primed, ticks);
    return 0;
}

#else  // batch build (stock replay_emit — unchanged behavior)

int main(int argc, char** argv) {
    if (argc < 2) { std::fprintf(stderr, "Usage: %s <replay.csv>\n", argv[0]); return 2; }
    std::ifstream f(argv[1]);
    if (!f) { std::fprintf(stderr, "Cannot open %s\n", argv[1]); return 2; }

    std::string line;
    if (!std::getline(f, line)) { std::fprintf(stderr, "Empty\n"); return 2; }
    Header h;
    h.parse(line);
    if (check_setpoint_coverage(h) != 0) return 3;

    // Initialize controller state.
    ControlState state = initial_state();
    uint64_t last_ts_unix = 0;

    // Emit header
    std::printf("ts\tmode\trelay_fog\trelay_vent\trelay_fan1\trelay_fan2\trelay_heat1\trelay_heat2\tmist_stage\treason\toverride_bits\n");

    long rows = 0;
    while (std::getline(f, line)) {
        process_row(h, line, state, last_ts_unix, /*emit_climate_action=*/false);
        rows++;
    }

    std::fprintf(stderr, "replay_emit: %ld rows emitted\n", rows);
    return 0;
}

#endif  // REPLAY_EMIT_STREAM
