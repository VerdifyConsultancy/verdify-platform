/*
 * replay_invariants.cpp — Phase-0 bulletproof-firmware harness.
 * Reads extended replay CSV and runs all invariants from invariants.h.
 *
 * Sibling to replay_overrides.cpp (which replays evaluate_overrides counts).
 * This file focuses on the invariant suite; the dual-ref old-vs-new mode
 * diff lives in replay_diff.cpp (phase-0 second deliverable).
 *
 * Compile: g++ -std=c++17 -I../lib -o replay_invariants replay_invariants.cpp
 * Run:     ./replay_invariants data/replay_overrides.csv
 *
 * CSV columns (tab-separated, header row required). The extended Phase-0 export
 * script `scripts/export-replay-overrides.sh` produces all of these. Legacy
 * columns (sp_*) remain for compatibility with replay_overrides.cpp.
 *
 *   ts, temp_avg, vpd_avg, rh_avg, outdoor_rh_pct, enthalpy_delta,
 *   outdoor_temp_f, indoor_dew_point, solar_irradiance_w_m2,
 *   outdoor_data_age_s,
 *   sp_temp_high, sp_temp_low, sp_vpd_high, sp_vpd_low, sp_bias_cool,
 *   sp_vpd_hysteresis, sp_watch_dwell_s,
 *   occupied, greenhouse_state, mode_reason,
 *   eq_fog, eq_vent, eq_fan1, eq_fan2, eq_heat1, eq_heat2,
 *   eq_mister_south, eq_mister_west, eq_mister_center
 *
 * Exit code: 0 if all invariants pass, non-zero = count of distinct
 * invariants violated (so CI fails on any breach).
 */

#include "invariants.h"
#include "greenhouse_logic.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <sstream>
#include <string>
#include <unordered_map>
#include <vector>
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

// Parse ISO-8601 ts like "2026-04-21 10:55:48.223+00" → unix seconds
static uint64_t parse_ts_unix(const std::string& s) {
    if (s.size() < 19) return 0;
    struct tm tm{};
    // Expect "YYYY-MM-DD HH:MM:SS[.fff][tz]"
    if (sscanf(s.c_str(), "%d-%d-%d %d:%d:%d",
               &tm.tm_year, &tm.tm_mon, &tm.tm_mday,
               &tm.tm_hour, &tm.tm_min, &tm.tm_sec) != 6) return 0;
    tm.tm_year -= 1900;
    tm.tm_mon -= 1;
    return (uint64_t)timegm(&tm);
}

// Map column name → index (built from header row)
struct Header {
    std::unordered_map<std::string, size_t> idx;
    void parse(const std::string& line) {
        std::istringstream ss(line);
        std::string col;
        size_t i = 0;
        while (std::getline(ss, col, '\t')) {
            idx[col] = i++;
        }
    }
    size_t of(const std::string& name) const {
        auto it = idx.find(name);
        return it == idx.end() ? SIZE_MAX : it->second;
    }
};

// Per-row failure counter, keyed by invariant id, for summary report.
struct FailureStats {
    std::unordered_map<int, int> counts_by_id;
    std::unordered_map<int, std::string> first_row_by_id;
    int total = 0;
};

static FailureStats g_stats;

static void stats_report(int id, const char* name,
                         const invariants::TraceRow& row, const char* detail) {
    g_stats.counts_by_id[id]++;
    g_stats.total++;
    if (g_stats.first_row_by_id.find(id) == g_stats.first_row_by_id.end()) {
        char msg[256];
        std::snprintf(msg, sizeof(msg),
            "ts=%llu mode=%s reason=%s: %s",
            (unsigned long long)row.ts_unix_s,
            row.greenhouse_state.c_str(),
            row.mode_reason.c_str(),
            detail);
        g_stats.first_row_by_id[id] = msg;
        std::fprintf(stderr, "INVARIANT FAIL #%02d %s (first): %s\n", id, name, msg);
    }
}

int main(int argc, char** argv) {
    if (argc < 2) {
        std::fprintf(stderr, "Usage: %s <replay.csv>\n", argv[0]);
        return 2;
    }
    std::ifstream f(argv[1]);
    if (!f) { std::fprintf(stderr, "Cannot open %s\n", argv[1]); return 2; }

    std::string line;
    if (!std::getline(f, line)) { std::fprintf(stderr, "Empty file\n"); return 2; }
    Header h;
    h.parse(line);

    // Required input columns for the invariant suite. Firmware outputs are
    // computed by this harness from greenhouse_logic.h so the gate validates
    // the candidate firmware, not whatever historical firmware happened to
    // emit at that timestamp.
    const char* required[] = {
        "ts", "temp_avg", "vpd_avg", "rh_avg", "occupied"
    };
    for (auto name : required) {
        if (h.of(name) == SIZE_MAX) {
            std::fprintf(stderr, "Missing required column: %s\n", name);
            std::fprintf(stderr, "Run scripts/export-replay-overrides.sh to regenerate CSV.\n");
            return 2;
        }
    }

    invariants::Runner runner;
    ControlState state = initial_state();
    uint64_t last_ts_unix = 0;
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

        invariants::TraceRow r{};
        std::string ts = get("ts");
        r.ts_unix_s = parse_ts_unix(ts);
        if (ts.size() >= 13) {
            try { r.local_hour = std::stoi(ts.substr(11, 2)); } catch (...) { r.local_hour = 12; }
        }

        r.temp_f  = parse_float(get("temp_avg"), 70.0f);
        r.rh_pct  = parse_float(get("rh_avg"), 50.0f);
        r.vpd_kpa = parse_float(get("vpd_avg"), 0.8f);
        r.dew_point_f = parse_float(get("indoor_dew_point"), r.temp_f - 10.0f);

        r.outdoor_temp_f     = parse_float(get("outdoor_temp_f"), NAN);
        r.outdoor_rh_pct     = parse_float(get("outdoor_rh_pct"), NAN);
        r.outdoor_dewpoint_f = parse_float(get("outdoor_dewpoint_f"), NAN);
        r.outdoor_data_age_s = parse_int  (get("outdoor_data_age_s"), -1);
        r.solar_w_m2         = parse_float(get("solar_irradiance_w_m2"), 0.0f);

        r.occupied = parse_bool(get("occupied"), false);

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
        assign_positive_float("sp_fog_rh_ceiling", sp.fog_rh_ceiling);
        assign_positive_float("sp_fog_min_temp", sp.fog_min_temp);
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
        const char* force_fsm = std::getenv("REPLAY_INVARIANTS_FORCE_FSM");
        if (!force_fsm || *force_fsm != '0') {
            sp.sw_fsm_controller_enabled = true;
        }
        validate_setpoints(sp);

        r.temp_low  = sp.temp_low;
        r.temp_high = sp.temp_high;
        r.vpd_low   = sp.vpd_low;
        r.vpd_high  = sp.vpd_high;
        r.temp_hysteresis = sp.temp_hysteresis;
        r.vpd_hysteresis  = sp.vpd_hysteresis;
        r.vpd_max_safe    = sp.vpd_max_safe;
        r.vpd_min_safe    = sp.vpd_min_safe;
        r.safety_max      = sp.safety_max;
        r.safety_min      = sp.safety_min;
        r.bias_heat       = sp.bias_heat;
        r.bias_cool       = sp.bias_cool;
        r.fog_escalation_kpa = sp.fog_escalation_kpa;
        r.fog_rh_ceiling  = sp.fog_rh_ceiling;
        r.fog_min_temp    = sp.fog_min_temp;
        r.sealed_max_ms   = sp.sealed_max_ms;
        r.relief_duration_ms = sp.relief_duration_ms;
        r.outdoor_staleness_max_s = sp.outdoor_staleness_max_s;

        SensorInputs in{};
        in.temp_f = r.temp_f;
        in.rh_pct = r.rh_pct;
        in.vpd_kpa = r.vpd_kpa;
        in.dew_point_f = r.dew_point_f;
        in.outdoor_rh_pct = r.outdoor_rh_pct;
        in.enthalpy_delta = parse_float(get("enthalpy_delta"), -5.0f);
        in.solar_w_m2 = r.solar_w_m2;
        in.vpd_south = r.vpd_kpa;
        in.vpd_west = r.vpd_kpa;
        in.vpd_east = r.vpd_kpa;
        in.local_hour = r.local_hour;
        in.occupied = r.occupied;
        in.outdoor_temp_f = r.outdoor_temp_f;
        in.outdoor_dewpoint_f = r.outdoor_dewpoint_f;
        in.outdoor_data_age_s = (r.outdoor_data_age_s < 0)
            ? 99999u
            : (uint32_t)r.outdoor_data_age_s;

        uint64_t delta_s = (last_ts_unix > 0 && r.ts_unix_s > last_ts_unix)
            ? r.ts_unix_s - last_ts_unix
            : 60;
        if (delta_s > 600) {
            state = initial_state();
            runner = invariants::Runner{};
            delta_s = 60;
        }
        if (delta_s > UINT32_MAX / 1000ULL) delta_s = UINT32_MAX / 1000ULL;
        const uint32_t dt_ms = (uint32_t)(delta_s * 1000ULL);
        last_ts_unix = r.ts_unix_s;

        Mode mode = determine_mode(in, sp, state, dt_ms);
        RelayOutputs out = resolve_equipment(mode, in, sp, state, true);
        if (mode == SEALED_MIST) {
            r.greenhouse_state = std::string("SEALED_MIST_") + MIST_NAMES[(int)state.mist_stage];
        } else {
            r.greenhouse_state = MODE_NAMES[(int)mode];
        }
        r.mode_reason = state.last_mode_reason ? state.last_mode_reason : "";
        r.summer_vent_active = state.override_summer_vent;
        r.vent_mist_assist_active = state.vent_mist_assist_active;

        r.eq_fog = out.fog ? 1 : 0;
        r.eq_vent = out.vent ? 1 : 0;
        r.eq_fan1 = out.fan1 ? 1 : 0;
        r.eq_fan2 = out.fan2 ? 1 : 0;
        r.eq_heat1 = out.heat1 ? 1 : 0;
        r.eq_heat2 = out.heat2 ? 1 : 0;
        const bool any_mister = (mode == SEALED_MIST) || state.vent_mist_assist_active;
        r.eq_mister_south = any_mister ? 1 : 0;
        r.eq_mister_west = (mode == SEALED_MIST && state.mist_stage >= MIST_S2) ? 1 : 0;
        r.eq_mister_center = (mode == SEALED_MIST && state.mist_stage >= MIST_S2) ? 1 : 0;

        // SAF-5 / FRT-6: fertilizer master + absorption-hold are controls.yaml
        // concerns the C++ replay does not reconstruct. Read them from the
        // corpus when present (export-replay-overrides.sh may add the columns);
        // default 0/false otherwise so #18/#19/#20 are vacuously true on the
        // climate-only corpus and become active once exported.
        r.eq_fertilizer_master = parse_int(get("eq_fertilizer_master"), 0);
        r.feed_hold_active = parse_bool(get("feed_hold_active"), false);

        // ENV-2 night-drop / dusk-cutoff config. Absent → invariant #17 uses
        // its built-in 20:00→06:00 default night window (Ctx17).
        r.night_start_hour = parse_int(get("sp_night_start_hour"), 0);
        r.night_end_hour   = parse_int(get("sp_night_end_hour"), 0);
        r.dusk_cutoff_hour = parse_int(get("sp_dusk_cutoff_hour"), 18);
        r.dusk_cutoff_enabled = parse_bool(get("sp_dusk_cutoff_enabled"), true);

        runner.run(r, stats_report);
        rows++;
    }

    // Summary
    std::printf("\n═══ Invariant summary — %ld rows ═══\n", rows);
    if (g_stats.counts_by_id.empty()) {
        std::printf("  ✓ All invariants passed.\n");
        return 0;
    }
    std::printf("  %d total violations across %zu distinct invariants.\n",
                g_stats.total, g_stats.counts_by_id.size());
    for (auto& [id, count] : g_stats.counts_by_id) {
        std::printf("    invariant #%02d: %d violations\n", id, count);
        auto it = g_stats.first_row_by_id.find(id);
        if (it != g_stats.first_row_by_id.end()) {
            std::printf("      first: %s\n", it->second.c_str());
        }
    }
    return (int)g_stats.counts_by_id.size();
}
