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
 *          ./replay_invariants data/replay_overrides.csv --policy-state state.txt
 *          ./replay_invariants --print-policy-template
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
 * Policy-vector injection (--policy-state, see policy_injection.h). The corpus
 * carries only a subset of the 48-field executor policy vector, and it is a
 * tracked 296k-row artifact that must not be rewritten to grow columns. So a
 * complete 48-field state can instead be supplied out of band and imposed on
 * EVERY row, ahead of validate_setpoints() — the same place controls.yaml
 * applies its own policy reads on the device. Corpus columns still populate
 * the band and the historical config; the policy state overrides them wherever
 * the two overlap. Without the flag the corpus-driven path is unchanged.
 *
 * Coverage self-declaration. Every run prints ONE machine-readable line
 * (`##replay-invariants-coverage-v1 {...}`) naming exactly which of the 48
 * components were actually imposed, from which source, on how many rows — and
 * the reason each remaining component was not. Only 27 of the 48 have a
 * consumer inside the compiled control logic at all; the other 21 are read by
 * ESPHome lambdas outside greenhouse_logic.h and can never be certified here.
 *
 * Exit code: 0 if all invariants pass, non-zero = count of distinct
 * invariants violated (so CI fails on any breach). Exit 2 keeps its existing
 * meaning — the corpus is unusable/predates the invariant schema — and
 * `.agent-fleet/ci.yaml` still special-cases it. Exit 64 (EX_USAGE) is new and
 * means the INVOCATION was rejected (bad option, or a policy state that is
 * incomplete / off the wire scale / outside the wire envelope); it is never a
 * statement about firmware safety.
 */

#include "invariants.h"
#include "greenhouse_logic.h"
#include "policy_injection.h"

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

// Existing meaning: the corpus is unusable / predates the invariant schema.
// `.agent-fleet/ci.yaml` maps this to "accepted legacy corpus" — do not reuse.
static constexpr int kExitCorpus = 2;
// New: the invocation itself was rejected (sysexits EX_USAGE). Never a safety
// verdict, and deliberately far above the highest possible invariant count.
static constexpr int kExitUsage = 64;

static void usage(const char* argv0) {
    std::fprintf(stderr,
        "Usage: %s <replay.csv> [--corpus-policy-columns] [--policy-state FILE|rom-baseline]\n"
        "       %s --print-policy-template [--policy-state FILE|rom-baseline]\n"
        "\n"
        "  --corpus-policy-columns\n"
        "                        also read the canonical `sp_*` component columns the\n"
        "                        corpus carries beyond the legacy set. OFF by default:\n"
        "                        it changes the HISTORICAL replay, not just coverage.\n"
        "  --policy-state FILE   impose a complete 48-field policy vector on every\n"
        "                        row (one `name value` pair per line; `#` comments).\n"
        "                        `rom-baseline` uses the registry-default vector.\n"
        "                        Also read from REPLAY_INVARIANTS_POLICY_STATE.\n"
        "  --print-policy-template\n"
        "                        write a complete, annotated state file to stdout.\n",
        argv0, argv0);
}

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
    const char* csv_path = nullptr;
    std::string policy_state_arg;
    bool print_template = false;
    bool corpus_policy_columns = false;
    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        if (arg == "--corpus-policy-columns") {
            corpus_policy_columns = true;
        } else if (arg == "--policy-state") {
            if (i + 1 >= argc) { usage(argv[0]); return kExitUsage; }
            policy_state_arg = argv[++i];
        } else if (arg.rfind("--policy-state=", 0) == 0) {
            policy_state_arg = arg.substr(std::strlen("--policy-state="));
        } else if (arg == "--print-policy-template") {
            print_template = true;
        } else if (arg == "-h" || arg == "--help") {
            usage(argv[0]);
            return 0;
        } else if (!arg.empty() && arg[0] == '-') {
            std::fprintf(stderr, "Unknown option: %s\n", arg.c_str());
            usage(argv[0]);
            return kExitUsage;
        } else if (csv_path == nullptr) {
            csv_path = argv[i];
        } else {
            std::fprintf(stderr, "Unexpected extra argument: %s\n", arg.c_str());
            usage(argv[0]);
            return kExitUsage;
        }
    }
    if (policy_state_arg.empty()) {
        const char* from_env = std::getenv("REPLAY_INVARIANTS_POLICY_STATE");
        if (from_env != nullptr && *from_env != '\0') policy_state_arg = from_env;
    }

    policy_injection::Coverage coverage;
    policy_injection::PolicyState policy_state;
    if (!policy_state_arg.empty()) {
        std::string err;
        if (policy_state_arg == "rom-baseline") {
            policy_state = policy_injection::rom_baseline_state();
        } else if (!policy_injection::load_policy_state_file(policy_state_arg, policy_state, err)) {
            std::fprintf(stderr, "POLICY STATE REJECTED: %s\n", err.c_str());
            coverage.status = "rejected";
            coverage.reject_reason = err;
            coverage.policy_state_path = policy_state_arg;
            policy_injection::print_coverage_line(stdout, coverage, 0);
            return kExitUsage;
        }
        coverage.policy_state_loaded = true;
        coverage.policy_state_path = policy_state.path;
        coverage.policy_state_sha256 = policy_state.sha256_hex;
    }
    if (print_template) {
        policy_injection::print_policy_template(
            stdout, policy_state.loaded ? policy_state : policy_injection::rom_baseline_state());
        return 0;
    }

    if (csv_path == nullptr) {
        usage(argv[0]);
        return kExitCorpus;
    }
    std::ifstream f(csv_path);
    if (!f) { std::fprintf(stderr, "Cannot open %s\n", csv_path); return kExitCorpus; }

    std::string line;
    if (!std::getline(f, line)) { std::fprintf(stderr, "Empty file\n"); return kExitCorpus; }
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
            return kExitCorpus;
        }
    }

    // The `sp_*` component columns this harness reads without
    // --corpus-policy-columns; kept in step with the unconditional assign_*
    // block below so the coverage line can say why a present column went
    // unread instead of pretending the corpus lacks it.
    static const char* kLegacyCorpusComponents[] = {
        "vpd_hysteresis", "temp_hysteresis", "fog_escalation_kpa",
        "vpd_watch_dwell_s", "mist_backoff_s", "mister_all_delay_s",
    };
    for (size_t i = 0; i < policy_injection::kFieldCount; ++i) {
        const auto& spec = policy_injection::kFields[i];
        if (spec.corpus_column == nullptr) continue;
        bool legacy = false;
        for (auto name : kLegacyCorpusComponents) {
            if (std::strcmp(name, spec.component) == 0) { legacy = true; break; }
        }
        coverage.note_corpus_column(spec.component,
                                    h.of(spec.corpus_column) != SIZE_MAX,
                                    legacy || corpus_policy_columns);
    }

    invariants::Runner runner;
    ControlState state = initial_state();
    policy_injection::EconLatch econ;
    uint64_t last_ts_unix = 0;
    long rows = 0;

    // #23 (ENV-5 / M14): config-level min-dark guarantee. Runs ONCE, before the
    // climate replay loop, because it is a property of the lighting photoperiod
    // window (no per-row climate input). Uses the production Vanda-era window
    // [6, 22) (16h photoperiod → 8h dark, well over the >=6h floor). A regression
    // that widened the window past 18h, or re-introduced the un-windowed
    // occupancy branch, fails here.
    {
        LightingSetpoints light_sp{};
        light_sp.target_light_minutes = 960;
        light_sp.lux_on_threshold = 40000.0f;
        light_sp.lux_hysteresis = 8000.0f;
        light_sp.start_hour = 6;
        light_sp.cutoff_hour = 22;
        light_sp.min_on_ms = 120000u;
        light_sp.min_off_ms = 60000u;
        light_sp.auto_enabled = true;
        if (!invariants::check_23_min_dark(light_sp, stats_report)) {
            // stats_report already recorded it; the summary below reports it.
        }
    }
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
        const float enthalpy_delta = parse_float(get("enthalpy_delta"), -5.0f);

        // Corpus-gap detection. Hoisted above the setpoint block (it depends
        // only on the timestamps) so a gap resets the economiser latch on the
        // same row it resets ControlState — a gap stands in for a reboot.
        uint64_t delta_s = (last_ts_unix > 0 && r.ts_unix_s > last_ts_unix)
            ? r.ts_unix_s - last_ts_unix
            : 60;
        if (delta_s > 600) {
            state = initial_state();
            runner = invariants::Runner{};
            econ.reset();
            delta_s = 60;
        }
        if (delta_s > UINT32_MAX / 1000ULL) delta_s = UINT32_MAX / 1000ULL;
        const uint32_t dt_ms = (uint32_t)(delta_s * 1000ULL);
        last_ts_unix = r.ts_unix_s;

        Setpoints sp = default_setpoints();
        // ADR-0004 default float keeps the recorded served band intact. The stock
        // corpus carries band edges but no temp_target/vpd_target columns, so
        // nonzero pinch behavior is exercised only by native tests and the
        // band-derived replay override with real derived targets.
        sp.band_track_fraction = 0.0f;
        // Every helper takes the executor component name the column imposes (or
        // nullptr when the column is not one of the 48 canonical components), so
        // the coverage line below reports what this run genuinely held rather
        // than what the source looks like it might hold.
        auto assign_positive_float = [&](const std::string& name, float& field,
                                         const char* component = nullptr) {
            float value = parse_float(get(name), NAN);
            if (!std::isnan(value) && value > 0.0f) {
                field = value;
                if (component) coverage.mark(component, ("corpus:" + name).c_str());
            }
        };
        auto assign_float = [&](const std::string& name, float& field,
                                const char* component = nullptr) {
            float value = parse_float(get(name), NAN);
            if (!std::isnan(value)) {
                field = value;
                if (component) coverage.mark(component, ("corpus:" + name).c_str());
            }
        };
        auto assign_bool = [&](const std::string& name, bool& field,
                               const char* component = nullptr) {
            const std::string cell = get(name);
            if (cell.empty() || cell == "\\N" || cell == "NULL") return;
            field = parse_bool(cell, field);
            if (component) coverage.mark(component, ("corpus:" + name).c_str());
        };
        auto assign_positive_unsigned = [&](const std::string& name, uint32_t& field,
                                       const char* component = nullptr) {
            float value = parse_float(get(name), NAN);
            if (!std::isnan(value) && value > 0.0f) {
                field = (uint32_t)value;
                if (component) coverage.mark(component, ("corpus:" + name).c_str());
            }
        };
        auto assign_positive_seconds_ms = [&](const std::string& name, uint32_t& field,
                                              const char* component = nullptr) {
            float value = parse_float(get(name), NAN);
            if (!std::isnan(value) && value > 0.0f) {
                field = (uint32_t)(value * 1000.0f);
                if (component) coverage.mark(component, ("corpus:" + name).c_str());
            }
        };
        // ── band + safety rails (not policy-vector components) ──
        assign_positive_float("sp_temp_low", sp.temp_low);
        assign_positive_float("sp_temp_high", sp.temp_high);
        assign_positive_float("sp_vpd_low", sp.vpd_low);
        assign_positive_float("sp_vpd_high", sp.vpd_high);
        assign_float("sp_bias_cool", sp.bias_cool);
        assign_float("sp_bias_heat", sp.bias_heat);
        assign_positive_float("sp_safety_max", sp.safety_max);
        assign_positive_float("sp_safety_min", sp.safety_min);
        assign_positive_float("sp_vpd_max_safe", sp.vpd_max_safe);
        assign_positive_float("sp_vpd_min_safe", sp.vpd_min_safe);
        assign_positive_float("sp_fog_rh_ceiling", sp.fog_rh_ceiling);
        assign_positive_float("sp_fog_min_temp", sp.fog_min_temp);
        // ── canonical executor components the LEGACY corpus path already read ──
        // Unchanged from the pre-injection harness (sp_watch_dwell_s /
        // sp_mist_backoff_s / sp_mist_s2_delay_s only moved from open-coded
        // blocks into the seconds→ms helper; same positive-only semantics).
        assign_positive_float("sp_vpd_hysteresis", sp.vpd_hysteresis, "vpd_hysteresis");
        assign_positive_float("sp_temp_hysteresis", sp.temp_hysteresis, "temp_hysteresis");
        assign_positive_float("sp_fog_escalation_kpa", sp.fog_escalation_kpa, "fog_escalation_kpa");
        assign_positive_seconds_ms("sp_watch_dwell_s", sp.vpd_watch_dwell_ms, "vpd_watch_dwell_s");
        assign_positive_seconds_ms("sp_mist_backoff_s", sp.mist_backoff_ms, "mist_backoff_s");
        assign_positive_seconds_ms("sp_mist_s2_delay_s", sp.mist_s2_delay_ms, "mister_all_delay_s");

        // ── the remaining canonical components the tracked corpus carries ──
        //
        // OPT-IN (--corpus-policy-columns), because reading them changes the
        // HISTORICAL replay, not just the injection surface: the corpus mixes
        // config snapshots taken at different times, and honouring all of them
        // at once reproduces states the device never actually ran in. On the
        // 296,698-row tracked corpus it produces 578 invariant #04 breaches
        // (`dwell_hold: SEALED_MIST continuous > sealed_max_ms`) — a recorded
        // 120 s mist_max_closed_vent_s against a recorded 300 s dwell_gate_ms,
        // a combination the two snapshots never held simultaneously. Leaving
        // them off by default keeps `make firmware-invariants` and the
        // `.agent-fleet/ci.yaml` gate bit-identical to the pre-injection
        // harness. A prefix-replay driver that rewrites these columns to ONE
        // self-consistent state should pass this flag; a driver that would
        // rather not rewrite a 296k-row tracked artifact at all should use
        // --policy-state, which reaches every injectable component.
        //
        // Kept in canonical (wire-id) order, one literal call per column, so a
        // source-scanning coverage probe reads the same set this run reports.
        if (corpus_policy_columns) {
            assign_positive_float("sp_cold_vent_guard_delta_f", sp.cold_vent_guard_delta_f,
                                  "cold_vent_guard_delta_f");
            assign_positive_float("sp_cool_exit_hysteresis_f", sp.cool_exit_hysteresis_f,
                                  "cool_exit_hysteresis_f");
            // 0 is a legal grid point for these three — assign_float, not the
            // positive-only variant, or a legitimate 0 would read as "unset".
            assign_float("sp_cool_stage2_over_high_f", sp.cool_stage2_over_high_f,
                         "cool_stage2_over_high_f");
            assign_positive_float("sp_direct_wet_stress_min_dew_margin_f",
                                  sp.direct_wet_stress_min_dew_margin_f,
                                  "direct_wet_stress_min_dew_margin_f");
            assign_float("sp_direct_wet_stress_vpd_margin_kpa", sp.direct_wet_stress_vpd_margin_kpa,
                         "direct_wet_stress_vpd_margin_kpa");
            assign_positive_unsigned("sp_dwell_gate_ms", sp.dwell_gate_ms, "dwell_gate_ms");
            assign_float("sp_heat_hysteresis", sp.heat_hysteresis, "heat_hysteresis");
            assign_positive_seconds_ms("sp_sealed_max_s", sp.sealed_max_ms, "mist_max_closed_vent_s");
            assign_positive_seconds_ms("sp_relief_duration_s", sp.relief_duration_ms,
                                       "mist_thermal_relief_s");
            assign_positive_unsigned("sp_outdoor_staleness_max_s", sp.outdoor_staleness_max_s,
                                "outdoor_staleness_max_s");
            assign_bool("sp_cool_all_fans_at_high_enabled", sp.cool_all_fans_at_high_enabled,
                        "sw_cool_all_fans_at_high_enabled");
            assign_bool("sp_direct_wet_stress_override_enabled", sp.direct_wet_stress_override_enabled,
                        "sw_direct_wet_stress_override_enabled");
            assign_bool("sp_sw_dwell_gate_enabled", sp.sw_dwell_gate_enabled, "sw_dwell_gate_enabled");
            assign_bool("sp_sw_summer_vent_enabled", sp.sw_summer_vent_enabled, "sw_summer_vent_enabled");
            assign_positive_float("sp_vent_prefer_dp_delta_f", sp.vent_prefer_dp_delta_f,
                                  "vent_prefer_dp_delta_f");
            assign_positive_float("sp_vent_prefer_temp_delta_f", sp.vent_prefer_temp_delta_f,
                                  "vent_prefer_temp_delta_f");
        }

        sp.sw_fsm_controller_enabled = parse_bool(
            get("sp_sw_fsm_controller_enabled"),
            sp.sw_fsm_controller_enabled
        );
        const char* force_fsm = std::getenv("REPLAY_INVARIANTS_FORCE_FSM");
        if (!force_fsm || *force_fsm != '0') {
            sp.sw_fsm_controller_enabled = true;
        }
        // The out-of-band 48-field state wins over the corpus wherever the two
        // overlap, and lands here — after the corpus columns, before
        // validate_setpoints() — exactly like controls.yaml's policy reads.
        policy_injection::apply_policy_state(policy_state, sp, econ, enthalpy_delta, coverage);
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
        in.enthalpy_delta = enthalpy_delta;
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
    } else {
        std::printf("  %d total violations across %zu distinct invariants.\n",
                    g_stats.total, g_stats.counts_by_id.size());
        for (auto& [id, count] : g_stats.counts_by_id) {
            std::printf("    invariant #%02d: %d violations\n", id, count);
            auto it = g_stats.first_row_by_id.find(id);
            if (it != g_stats.first_row_by_id.end()) {
                std::printf("      first: %s\n", it->second.c_str());
            }
        }
    }

    // Coverage declaration — printed on EVERY run, pass or fail, so a clean
    // exit can only be read as certifying the components it actually held.
    const size_t imposed = coverage.imposed_count();
    std::printf("\n═══ Policy-vector injection coverage — %zu/%zu components imposed"
                " (compiled-logic ceiling %zu) ═══\n",
                imposed, policy_injection::kFieldCount, policy_injection::kInjectableCount);
    if (imposed < policy_injection::kInjectableCount) {
        std::printf("  ! partial coverage: this run can falsify a policy state but cannot certify one.\n");
    }
    policy_injection::print_coverage_line(stdout, coverage, rows,
                                          g_stats.total, g_stats.counts_by_id.size());
    return (int)g_stats.counts_by_id.size();
}
