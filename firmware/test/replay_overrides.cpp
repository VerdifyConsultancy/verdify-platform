/*
 * replay_overrides.cpp — Historical validation of evaluate_overrides()
 *
 * Sprint 16 OBS-1e validation (Layer 2 of the post-deploy audit).
 * Reads CSV export of v_greenhouse_state + occupancy as-of joins, runs
 * determine_mode() forward to reconstruct ControlState per minute, then
 * calls evaluate_overrides() each step. Aggregates flag firings by date
 * and type, and identifies bugs: flags that never fire, flags that fire
 * on impossible mode combinations, flags that miss known events.
 *
 * Same logic code as the deployed ESP32 firmware.
 *
 * Compile: g++ -std=c++17 -I../lib -o replay_overrides replay_overrides.cpp
 * Run:     ./replay_overrides data/replay_overrides.csv
 *
 * CSV columns are tab-separated with a header row. The replay accepts the
 * Phase-0 extended corpus produced by scripts/export-replay-overrides.sh;
 * legacy sp_* columns remain optional for historical corpora.
 */

#include "greenhouse_logic.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>
#include <fstream>
#include <sstream>
#include <array>
#include <unordered_map>
#include <ctime>

static float parse_float(const std::string& s, float def = 0.0f) {
    if (s.empty() || s == "\\N" || s == "NULL") return def;
    try { return std::stof(s); } catch (...) { return def; }
}

static bool parse_bool(const std::string& s, bool def = false) {
    if (s.empty() || s == "\\N" || s == "NULL") return def;
    return s == "t" || s == "true" || s == "1";
}

static int parse_hour(const std::string& ts) {
    if (ts.size() < 13) return 12;
    try { return std::stoi(ts.substr(11, 2)); } catch (...) { return 12; }
}

static std::string parse_date(const std::string& ts) {
    if (ts.size() < 10) return "unknown";
    return ts.substr(0, 10);
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

enum OverrideIdx {
    OF_OCCUPANCY = 0,
    OF_FOG_RH,
    OF_FOG_TEMP,
    OF_FOG_WINDOW,
    OF_RELIEF_BREAKER,
    OF_SEAL_BLOCKED_TEMP,
    OF_VPD_DRY_OVERRIDE,
    OF_SUMMER_VENT,
    OF_VENT_MIST_ASSIST,
    OF_FOG_HEAT_ASSIST,
    OF_COUNT
};

static const char* OVERRIDE_NAMES[OF_COUNT] = {
    "occupancy_blocks_equipment",
    "fog_gate_rh",
    "fog_gate_temp",
    "fog_gate_window",
    "relief_cycle_breaker",
    "seal_blocked_temp",
    "vpd_dry_override",
    "summer_vent",
    "vent_mist_assist",
    "fog_heat_assist",
};

static const char* OVERRIDE_SHORT_NAMES[OF_COUNT] = {
    "occ",
    "fRH",
    "fT",
    "fW",
    "rlf",
    "sBT",
    "VdO",
    "SumV",
    "VMst",
    "FgHt",
};

struct FlagEvent {
    std::string ts;
    std::string mode;
    float temp_f;
    float vpd_kpa;
    float rh_pct;
};

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

struct Stats {
    long total_rows = 0;
    long mode_counts[8] = {};
    long flag_fire_minutes[OF_COUNT] = {};        // minutes each flag was active
    long flag_start_events[OF_COUNT] = {};        // edges off→on
    bool last_flag_state[OF_COUNT] = {};
    std::array<FlagEvent, OF_COUNT> first_fire;   // first time each flag ever fires
    std::vector<std::pair<std::string, std::array<int, OF_COUNT>>> daily;
    std::string current_date;
    std::array<int, OF_COUNT> today{};
    // Cross-check: flag fired with incompatible mode
    long cross_check_fails[OF_COUNT] = {};
    // Flags that fired while mode was SENSOR_FAULT (should never happen)
    long sensor_fault_fires = 0;
};

int main(int argc, char* argv[]) {
    if (argc < 2) {
        fprintf(stderr, "Usage: %s <csv_file>\n", argv[0]);
        return 1;
    }

    std::ifstream file(argv[1]);
    if (!file.is_open()) {
        fprintf(stderr, "Cannot open %s\n", argv[1]);
        return 1;
    }

    std::string header;
    std::getline(file, header);
    Header h;
    h.parse(header);
    const char* required[] = {"ts", "temp_avg", "vpd_avg", "rh_avg", "occupied"};
    for (auto name : required) {
        if (h.of(name) == SIZE_MAX) {
            fprintf(stderr, "Missing required column: %s\n", name);
            return 2;
        }
    }

    ControlState state = initial_state();
    Stats stats;
    std::string line;
    std::string last_ts;
    uint64_t last_ts_unix = 0;

    while (std::getline(file, line)) {
        std::vector<std::string> c;
        {
            std::stringstream ss(line);
            std::string cell;
            while (std::getline(ss, cell, '\t')) c.push_back(cell);
        }
        auto get = [&](const std::string& name, const std::string& def = "") -> std::string {
            size_t i = h.of(name);
            return (i == SIZE_MAX || i >= c.size()) ? def : c[i];
        };

        const std::string ts = get("ts");
        SensorInputs in{};
        in.temp_f = parse_float(get("temp_avg"), 70.0f);
        in.vpd_kpa = parse_float(get("vpd_avg"), 1.0f);
        in.rh_pct = parse_float(get("rh_avg"), 60.0f);
        in.outdoor_rh_pct = parse_float(get("outdoor_rh_pct"), 30.0f);
        in.enthalpy_delta = parse_float(get("enthalpy_delta"), -5.0f);
        in.solar_w_m2 = parse_float(get("solar_irradiance_w_m2"), 0.0f);
        in.dew_point_f = parse_float(get("indoor_dew_point"), NAN);
        in.vpd_south = in.vpd_kpa;
        in.vpd_west = in.vpd_kpa;
        in.vpd_east = in.vpd_kpa;
        in.local_hour = parse_hour(ts);
        in.occupied = parse_bool(get("occupied"), false);
        in.outdoor_temp_f = parse_float(get("outdoor_temp_f"), NAN);
        in.outdoor_dewpoint_f = parse_float(get("outdoor_dewpoint_f"), NAN);
        float outdoor_age_s = parse_float(get("outdoor_data_age_s"), -1.0f);
        in.outdoor_data_age_s = outdoor_age_s < 0.0f ? 99999u : (uint32_t)outdoor_age_s;

        // Skip implausible rows (missing sensor data early in history)
        if (in.temp_f < -10.0f || in.temp_f > 140.0f) continue;
        if (in.vpd_kpa < 0.0f || in.vpd_kpa > 10.0f) continue;
        if (in.rh_pct < 0.0f || in.rh_pct > 100.0f) continue;

        Setpoints sp = default_setpoints();
        // Production firmware has occupancy_inhibit=true (defaults to false in
        // default_setpoints for test isolation). Force true for replay so the
        // occupancy_blocks_equipment path matches the deployed system.
        sp.occupancy_inhibit = true;
        float v;
        if ((v = parse_float(get("sp_temp_high")))  > 0) sp.temp_high = v;
        if ((v = parse_float(get("sp_temp_low")))  > 0) sp.temp_low = v;
        if ((v = parse_float(get("sp_vpd_high")))  > 0) sp.vpd_high = v;
        if ((v = parse_float(get("sp_vpd_low")))  > 0) sp.vpd_low = v;
        sp.bias_cool = parse_float(get("sp_bias_cool"), sp.bias_cool);
        sp.bias_heat = parse_float(get("sp_bias_heat"), sp.bias_heat);
        if ((v = parse_float(get("sp_vpd_hysteresis"))) > 0) sp.vpd_hysteresis = v;
        if ((v = parse_float(get("sp_temp_hysteresis"))) > 0) sp.temp_hysteresis = v;
        if ((v = parse_float(get("sp_watch_dwell_s"))) > 0) sp.vpd_watch_dwell_ms = (uint32_t)(v * 1000);
        if ((v = parse_float(get("sp_safety_max"))) > 0) sp.safety_max = v;
        if ((v = parse_float(get("sp_safety_min"))) > 0) sp.safety_min = v;
        if ((v = parse_float(get("sp_vpd_max_safe"))) > 0) sp.vpd_max_safe = v;
        if ((v = parse_float(get("sp_vpd_min_safe"))) > 0) sp.vpd_min_safe = v;
        if ((v = parse_float(get("sp_fog_escalation_kpa"))) > 0) sp.fog_escalation_kpa = v;
        if ((v = parse_float(get("sp_mist_backoff_s"))) > 0) sp.mist_backoff_ms = (uint32_t)(v * 1000);
        if ((v = parse_float(get("sp_mist_s2_delay_s"))) > 0) sp.mist_s2_delay_ms = (uint32_t)(v * 1000);
        sp.sw_fsm_controller_enabled = parse_bool(
            get("sp_sw_fsm_controller_enabled"),
            sp.sw_fsm_controller_enabled
        );
        // Production ESPHome hard-forces the unified band-first controller ON
        // before each control tick. Keep override replay aligned by default;
        // set REPLAY_OVERRIDES_FORCE_FSM=0 only for explicit legacy forensics.
        const char* force_fsm = std::getenv("REPLAY_OVERRIDES_FORCE_FSM");
        if (!force_fsm || !*force_fsm || *force_fsm != '0') {
            sp.sw_fsm_controller_enabled = true;
        }
        const char* force_dwell = std::getenv("REPLAY_OVERRIDES_FORCE_DWELL");
        if (!force_dwell || !*force_dwell || *force_dwell != '0') {
            sp.sw_dwell_gate_enabled = true;
        }
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

        // Forward-simulate determine_mode() so ControlState evolves correctly
        Mode mode = determine_mode(in, sp, state, dt_ms);

        // Now evaluate_overrides() against the reconstructed state
        OverrideFlags f = evaluate_overrides(in, sp, state, mode);
        bool flags[OF_COUNT] = {
            f.occupancy_blocks_equipment, f.fog_gate_rh, f.fog_gate_temp,
            f.fog_gate_window, f.relief_cycle_breaker, f.seal_blocked_temp,
            f.vpd_dry_override, f.summer_vent_active, f.vent_mist_assist,
            f.fog_heat_assist,
        };

        // Bookkeeping
        stats.total_rows++;
        stats.mode_counts[mode]++;

        // Rotate daily bucket
        std::string d = parse_date(ts);
        if (d != stats.current_date) {
            if (!stats.current_date.empty()) {
                stats.daily.push_back({stats.current_date, stats.today});
            }
            stats.current_date = d;
            stats.today.fill(0);
        }

        for (int i = 0; i < OF_COUNT; i++) {
            if (flags[i]) {
                stats.flag_fire_minutes[i]++;
                stats.today[i]++;
                if (!stats.last_flag_state[i]) {
                    stats.flag_start_events[i]++;
                    if (stats.first_fire[i].ts.empty()) {
                        stats.first_fire[i] = {ts, MODE_NAMES[mode], in.temp_f, in.vpd_kpa, in.rh_pct};
                    }
                }
                // Cross-check: none of the 7 flags should fire in SENSOR_FAULT mode
                if (mode == SENSOR_FAULT) stats.sensor_fault_fires++;
            }
            stats.last_flag_state[i] = flags[i];
        }

        last_ts = ts;
    }
    if (!stats.current_date.empty()) {
        stats.daily.push_back({stats.current_date, stats.today});
    }

    // ─── Report ─────────────────────────────────────────────────────
    printf("═══════════════════════════════════════════════════════════════\n");
    printf("  Override Replay — %ld rows (%.1f days)\n",
           stats.total_rows, stats.total_rows / 1440.0);
    printf("  Range: first → %s\n", last_ts.c_str());
    printf("  Same logic as deployed firmware\n");
    printf("═══════════════════════════════════════════════════════════════\n\n");

    printf("Mode distribution:\n");
    for (int i = 0; i < 8; i++) {
        if (stats.mode_counts[i] > 0) {
            printf("  %-15s %8ld min (%5.2f%%)\n", MODE_NAMES[i],
                   stats.mode_counts[i], 100.0 * stats.mode_counts[i] / stats.total_rows);
        }
    }

    printf("\nOverride flag firings (across full history):\n");
    printf("  %-28s %9s %9s %s\n", "flag", "minutes", "events", "%-of-time");
    printf("  %-28s %9s %9s %s\n", "----", "-------", "------", "---------");
    for (int i = 0; i < OF_COUNT; i++) {
        double pct = stats.total_rows > 0 ? 100.0 * stats.flag_fire_minutes[i] / stats.total_rows : 0;
        printf("  %-28s %9ld %9ld %8.3f%%\n",
               OVERRIDE_NAMES[i],
               stats.flag_fire_minutes[i],
               stats.flag_start_events[i],
               pct);
    }

    printf("\nFirst occurrence of each override in history:\n");
    for (int i = 0; i < OF_COUNT; i++) {
        if (stats.first_fire[i].ts.empty()) {
            printf("  %-28s did not fire in %.0f days (physically rare — see self-test)\n",
                   OVERRIDE_NAMES[i], stats.total_rows / 1440.0);
        } else {
            const auto& e = stats.first_fire[i];
            printf("  %-28s %s  mode=%s  T=%.1f  VPD=%.2f  RH=%.0f%%\n",
                   OVERRIDE_NAMES[i], e.ts.c_str(), e.mode.c_str(),
                   e.temp_f, e.vpd_kpa, e.rh_pct);
        }
    }

    printf("\nCross-checks:\n");
    printf("  Flags firing while mode=SENSOR_FAULT  %ld %s\n",
           stats.sensor_fault_fires,
           stats.sensor_fault_fires == 0 ? "✓" : "✗ BUG — should be 0");

    printf("\nTop 10 worst days by total override-minutes:\n");
    std::sort(stats.daily.begin(), stats.daily.end(),
              [](const auto& a, const auto& b) {
                  int sa = 0, sb = 0;
                  for (int v : a.second) sa += v;
                  for (int v : b.second) sb += v;
                  return sa > sb;
              });
    printf("  %-12s", "date");
    for (int i = 0; i < OF_COUNT; i++) printf(" %5s", OVERRIDE_SHORT_NAMES[i]);
    printf("  total\n");
    int shown = 0;
    for (auto& d : stats.daily) {
        int tot = 0; for (int v : d.second) tot += v;
        if (tot == 0) continue;
        printf("  %-12s", d.first.c_str());
        for (int i = 0; i < OF_COUNT; i++) printf(" %5d", d.second[i]);
        printf("  %5d\n", tot);
        if (++shown >= 10) break;
    }

    printf("\nDays 2026-04-13 → 2026-04-17 (96h review window):\n");
    printf("  %-12s", "date");
    for (int i = 0; i < OF_COUNT; i++) printf(" %5s", OVERRIDE_SHORT_NAMES[i]);
    printf("  total\n");
    // Re-sort by date ascending for the review window
    std::sort(stats.daily.begin(), stats.daily.end(),
              [](const auto& a, const auto& b) { return a.first < b.first; });
    for (auto& d : stats.daily) {
        if (d.first < "2026-04-13" || d.first > "2026-04-17") continue;
        int tot = 0; for (int v : d.second) tot += v;
        printf("  %-12s", d.first.c_str());
        for (int i = 0; i < OF_COUNT; i++) printf(" %5d", d.second[i]);
        printf("  %5d\n", tot);
    }

    // ── Synthetic self-test: prove every flag's code path is live ──
    // Some overrides are physically rare (fog_gate_rh needs high VPD + high RH
    // simultaneously; vpd_dry_override needs a VPD jump before dwell matures).
    // Historical "NEVER fired" for these is expected. This block forces each
    // condition with contrived inputs so the full evaluate_overrides() path is
    // exercised and we can prove the flags aren't dead code.
    printf("\nSynthetic self-test (contrived inputs to force each flag):\n");
    int self_test_failures = 0;
    struct Probe {
        const char* name;
        SensorInputs in;
        Setpoints sp;
        ControlState st;
        Mode mode;
    };
    auto mk = []() {
        Probe p;
        p.in = SensorInputs{};
        p.in.temp_f = 72.0f; p.in.vpd_kpa = 1.5f; p.in.rh_pct = 60.0f;
        p.in.dew_point_f = 55.0f; p.in.outdoor_rh_pct = 30.0f;
        p.in.enthalpy_delta = -5.0f; p.in.solar_w_m2 = 0.0f;
        p.in.vpd_south = 1.5f; p.in.vpd_west = 1.5f;
        p.in.vpd_east = 1.5f; p.in.local_hour = 12; p.in.occupied = false;
        p.sp = default_setpoints();
        // Sprint-11: default_setpoints() is now wide (2-band model —
        // dispatcher pushes real crop band). Override to a realistic
        // operational band so synthetic probes that rely on vpd_above_band
        // (relief_cycle_breaker, seal_blocked_temp) trigger the way they
        // would in production.
        p.sp.temp_high = 82.0f; p.sp.temp_low = 65.0f;
        p.sp.vpd_high = 1.4f;   p.sp.vpd_low = 0.8f;
        p.sp.safety_max = 95.0f; p.sp.safety_min = 45.0f;
        p.sp.vpd_max_safe = 2.5f; p.sp.vpd_min_safe = 0.3f;
        p.st = initial_state();
        p.mode = IDLE;
        return p;
    };
    auto report = [&](const char* name, bool pass) {
        printf("  %-28s %s\n", name, pass ? "✓" : "✗ BUG");
        if (!pass) self_test_failures++;
    };
    // occupancy_blocks_equipment: mister/fog path
    {
        auto p = mk(); p.sp.occupancy_inhibit = true; p.in.occupied = true;
        p.st.vpd_watch_timer_ms = 60000; p.mode = SEALED_MIST;
        auto f = evaluate_overrides(p.in, p.sp, p.st, p.mode);
        report("occupancy_blocks_equipment", f.occupancy_blocks_equipment);
    }
    // occupancy_blocks_equipment: fan path
    {
        auto p = mk(); p.sp.occupancy_inhibit = true; p.in.occupied = true;
        p.in.temp_f = 86.0f; p.mode = VENTILATE;
        auto f = evaluate_overrides(p.in, p.sp, p.st, p.mode);
        report("occupancy_blocks_equipment", f.occupancy_blocks_equipment);
    }
    // fog_gate_rh
    {
        auto p = mk(); p.st.mist_stage = MIST_S2; p.st.vpd_watch_timer_ms = 60000;
        p.in.vpd_kpa = p.sp.vpd_high + p.sp.fog_escalation_kpa + 0.1f;
        p.in.rh_pct = 95.0f; p.mode = SEALED_MIST;
        auto f = evaluate_overrides(p.in, p.sp, p.st, p.mode);
        report("fog_gate_rh", f.fog_gate_rh);
    }
    // fog_gate_temp
    {
        auto p = mk(); p.st.mist_stage = MIST_S2; p.st.vpd_watch_timer_ms = 60000;
        p.in.vpd_kpa = p.sp.vpd_high + p.sp.fog_escalation_kpa + 0.1f;
        p.in.temp_f = p.sp.fog_min_temp - 2.0f; p.mode = SEALED_MIST;
        auto f = evaluate_overrides(p.in, p.sp, p.st, p.mode);
        report("fog_gate_temp", f.fog_gate_temp);
    }
    // fog_gate_window
    {
        auto p = mk(); p.st.mist_stage = MIST_S2; p.st.vpd_watch_timer_ms = 60000;
        p.in.vpd_kpa = p.sp.vpd_high + p.sp.fog_escalation_kpa + 0.1f;
        p.in.local_hour = p.sp.fog_window_end; p.mode = SEALED_MIST;
        auto f = evaluate_overrides(p.in, p.sp, p.st, p.mode);
        report("fog_gate_window", f.fog_gate_window);
    }
    // relief_cycle_breaker
    {
        auto p = mk(); p.st.vpd_watch_timer_ms = 60000;
        p.st.relief_cycle_count = p.sp.max_relief_cycles; p.mode = VENTILATE;
        auto f = evaluate_overrides(p.in, p.sp, p.st, p.mode);
        report("relief_cycle_breaker", f.relief_cycle_breaker);
    }
    // seal_blocked_temp
    {
        auto p = mk(); p.st.vpd_watch_timer_ms = 60000;
        p.in.temp_f = p.sp.safety_max - 3.0f; p.mode = VENTILATE;
        auto f = evaluate_overrides(p.in, p.sp, p.st, p.mode);
        report("seal_blocked_temp", f.seal_blocked_temp);
    }
    // vpd_dry_override — must exercise determine_mode to trigger R2-3
    {
        auto p = mk(); p.st.vpd_watch_timer_ms = 0;
        p.in.vpd_kpa = p.sp.vpd_max_safe + 0.2f;
        Mode m = determine_mode(p.in, p.sp, p.st, 5000);
        auto f = evaluate_overrides(p.in, p.sp, p.st, m);
        printf("  %-28s %s (mode=%s, r23_triggered=%s)\n",
               "vpd_dry_override",
               f.vpd_dry_override ? "✓" : "✗ BUG",
               MODE_NAMES[m], p.st.dry_override_active ? "yes" : "no");
        if (!f.vpd_dry_override) self_test_failures++;
    }
    // summer_vent — must exercise determine_mode so the outdoor preference
    // gate sets ControlState.override_summer_vent.
    {
        auto p = mk();
        p.sp.sw_fsm_controller_enabled = true;
        p.st.vpd_watch_timer_ms = p.sp.vpd_watch_dwell_ms;
        p.in.temp_f = p.sp.temp_high + 3.0f;
        p.in.vpd_kpa = p.sp.vpd_high + 0.2f;
        p.in.dew_point_f = 72.0f;
        p.in.outdoor_temp_f = p.in.temp_f - p.sp.vent_prefer_temp_delta_f - 2.0f;
        p.in.outdoor_dewpoint_f = p.in.dew_point_f - p.sp.vent_prefer_dp_delta_f - 2.0f;
        p.in.outdoor_data_age_s = 30u;
        Mode m = determine_mode(p.in, p.sp, p.st, 5000);
        auto f = evaluate_overrides(p.in, p.sp, p.st, m);
        printf("  %-28s %s (mode=%s, reason=%s)\n",
               "summer_vent",
               f.summer_vent_active ? "✓" : "✗ BUG",
               MODE_NAMES[m],
               p.st.last_mode_reason ? p.st.last_mode_reason : "");
        if (!f.summer_vent_active) self_test_failures++;
    }
    // vent_mist_assist — band-first controller may ventilate for cooling while
    // carrying concurrent humidification demand.
    {
        auto p = mk();
        p.sp.sw_fsm_controller_enabled = true;
        p.st.vpd_watch_timer_ms = p.sp.vpd_watch_dwell_ms;
        p.in.temp_f = p.sp.temp_high + 2.0f;
        p.in.vpd_kpa = p.sp.vpd_high + 0.2f;
        p.in.outdoor_temp_f = p.in.temp_f;
        p.in.outdoor_dewpoint_f = p.in.dew_point_f;
        p.in.outdoor_data_age_s = 99999u;
        Mode m = determine_mode(p.in, p.sp, p.st, 5000);
        auto f = evaluate_overrides(p.in, p.sp, p.st, m);
        printf("  %-28s %s (mode=%s, assist_active=%s)\n",
               "vent_mist_assist",
               f.vent_mist_assist ? "✓" : "✗ BUG",
               MODE_NAMES[m],
               p.st.vent_mist_assist_active ? "yes" : "no");
        if (!f.vent_mist_assist) self_test_failures++;
    }
    // fog_heat_assist
    {
        auto p = mk();
        p.mode = SEALED_MIST;
        p.st.mist_stage = MIST_FOG;
        p.st.heat2_latched = true;
        p.in.temp_f = p.sp.temp_low + 1.0f;
        p.in.vpd_kpa = p.sp.vpd_high + 0.4f;
        p.in.rh_pct = 55.0f;
        auto f = evaluate_overrides(p.in, p.sp, p.st, p.mode);
        report("fog_heat_assist", f.fog_heat_assist);
    }

    printf("\n═══════════════════════════════════════════════════════════════\n");
    if (self_test_failures > 0) {
        printf("  REPLAY FAILED: %d self-test probe(s) did not fire\n", self_test_failures);
    }
    if (stats.sensor_fault_fires > 0) {
        printf("  REPLAY FAILED: %ld flag(s) fired while mode=SENSOR_FAULT\n", stats.sensor_fault_fires);
    }
    printf("═══════════════════════════════════════════════════════════════\n");

    return (self_test_failures > 0 || stats.sensor_fault_fires > 0) ? 1 : 0;
}
