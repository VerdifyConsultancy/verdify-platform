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
 */

#include "greenhouse_logic.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
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

int main(int argc, char** argv) {
    if (argc < 2) { std::fprintf(stderr, "Usage: %s <replay.csv>\n", argv[0]); return 2; }
    std::ifstream f(argv[1]);
    if (!f) { std::fprintf(stderr, "Cannot open %s\n", argv[1]); return 2; }

    std::string line;
    if (!std::getline(f, line)) { std::fprintf(stderr, "Empty\n"); return 2; }
    Header h;
    h.parse(line);

    // Initialize controller state.
    ControlState state = initial_state();
    uint64_t last_ts_unix = 0;

    // Emit header
    std::printf("ts\tmode\trelay_fog\trelay_vent\trelay_fan1\trelay_fan2\trelay_heat1\trelay_heat2\tmist_stage\treason\toverride_bits\n");

    long rows = 0;
    while (std::getline(f, line)) {
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
        in.dew_point_f = parse_float(get("indoor_dew_point"), NAN);
        in.enthalpy_delta = parse_float(get("enthalpy_delta"), -5.0f);
        in.solar_w_m2 = parse_float(get("solar_irradiance_w_m2"), 0.0f);
        // zone vpds unavailable in this CSV; use avg
        in.vpd_south = in.vpd_kpa; in.vpd_west = in.vpd_kpa;
        in.vpd_east = in.vpd_kpa;
        const std::string ts = get("ts");
        int hr = 12;
        if (ts.size() >= 13) { try { hr = std::stoi(ts.substr(11, 2)); } catch (...) {} }
        in.local_hour = hr;
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
        // Production ESPHome locks the dwell gate ON before every control
        // tick. Keep replay aligned by default; set REPLAY_EMIT_FORCE_DWELL=0
        // only for explicit historical forensics.
        const char* force_dwell = std::getenv("REPLAY_EMIT_FORCE_DWELL");
        if (!force_dwell || !*force_dwell || *force_dwell != '0') {
            sp.sw_dwell_gate_enabled = true;
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

        std::printf("%s\t%s\t%d\t%d\t%d\t%d\t%d\t%d\t%d\t%s\t%d\n",
                    ts.c_str(),
                    MODE_NAMES[(int)mode],
                    r.fog, r.vent, r.fan1, r.fan2, r.heat1, r.heat2,
                    (int)state.mist_stage,
                    reason,
                    override_bits);
        rows++;
    }

    std::fprintf(stderr, "replay_emit: %ld rows emitted\n", rows);
    return 0;
}
