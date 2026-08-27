#pragma once
/*
 * policy_injection.h — full-48 policy-vector injection surface for the OFFLINE
 * replay harnesses (test-only; NEVER compiled into the device image).
 *
 * This header defines no control behavior. It only writes the same Setpoints
 * members that firmware/greenhouse/controls.yaml writes before it calls
 * determine_mode()/resolve_equipment(), so a replay run can be forced to hold
 * a specific 48-field policy state instead of whatever the corpus recorded.
 *
 * Two injection routes, both optional, both fail-closed:
 *
 *   1. corpus columns — the historical `sp_*` route. Call sites in the harness
 *      stay literal (assign_float("sp_temp_hysteresis", sp.temp_hysteresis,
 *      "temp_hysteresis")) so source-scanning coverage probes keep working;
 *      the third argument names the executor component the column imposes so
 *      the harness can declare its own coverage truthfully.
 *   2. --policy-state FILE — a COMPLETE 48-field policy vector imposed on
 *      every row, ahead of validate_setpoints(), exactly like a committed
 *      policy vector on the device. All 48 canonical components are required;
 *      values are checked against the wire envelope in
 *      policy_vector_generated.h and against the three cross-field rules in
 *      policy_vector.h. A partial, off-scale, or out-of-envelope file is
 *      REJECTED — never silently defaulted, never clamped on entry.
 *
 * Coverage ceiling. Only 27 of the 48 components have a consumer inside the
 * compiled control logic:
 *
 *   * 25 land directly in a Setpoints member (see kFields below); and
 *   * enthalpy_open / enthalpy_close reach Setpoints::econ_block through the
 *     economiser deadband in controls.yaml:352-370, modelled here verbatim.
 *
 * The other 21 are read by ESPHome lambdas that live OUTSIDE
 * greenhouse_logic.h — relay min-on/off dwell wrappers, the zone-mister
 * cadence/arbiter, the post-resolve_equipment vent interlocks, and the on-chip
 * solar band bias. kFields records the reason per field, and the harness
 * prints it, so a "no invariant breach" run can never be misread as
 * certifying a component this harness cannot actually hold.
 */

#include "greenhouse_types.h"
#include "policy_rom_baseline_generated.h"
#include "policy_vector_generated.h"
#include "policy_sha256.h"

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <sstream>
#include <string>
#include <unordered_map>
#include <vector>

namespace policy_injection {

constexpr size_t kFieldCount = verdify_policy::kPolicyFieldCount;

// Where a component lands inside the compiled control surface.
enum class Sink : uint8_t {
    kNone,             // no compiled-logic consumer (see FieldSpec::note)
    kFloat,            // float Setpoints member, engineering units
    kBool,             // bool Setpoints member
    kU32,              // uint32_t Setpoints member, value already in member units
    kU32FromSeconds,   // uint32_t Setpoints member in ms; value is seconds
    kEconOpen,         // modelled: economiser unblock threshold → econ_block
    kEconClose         // modelled: economiser block threshold  → econ_block
};

struct FieldSpec {
    const char* component;        // canonical executor / wire name
    const char* corpus_column;    // tracked-corpus `sp_*` column, or nullptr
    const char* firmware_symbol;  // Setpoints member written, or nullptr
    const char* note;             // why uncoverable, or how it is modelled
    Sink sink;
    float    Setpoints::* fptr;
    bool     Setpoints::* bptr;
    uint32_t Setpoints::* uptr;
};

// Ordered EXACTLY like verdify_policy::kPolicyFields (ascending wire id); a
// static_assert below pins the two together so a wire-schema regeneration
// cannot silently desynchronize this table.
inline constexpr FieldSpec kFields[kFieldCount] = {
    {"band_track_fraction", nullptr, "Setpoints::band_track_fraction", nullptr,
     Sink::kFloat, &Setpoints::band_track_fraction, nullptr, nullptr},
    {"cold_vent_guard_delta_f", "sp_cold_vent_guard_delta_f", "Setpoints::cold_vent_guard_delta_f", nullptr,
     Sink::kFloat, &Setpoints::cold_vent_guard_delta_f, nullptr, nullptr},
    {"cool_exit_hysteresis_f", "sp_cool_exit_hysteresis_f", "Setpoints::cool_exit_hysteresis_f", nullptr,
     Sink::kFloat, &Setpoints::cool_exit_hysteresis_f, nullptr, nullptr},
    {"cool_stage2_exit_hysteresis_f", nullptr, "Setpoints::cool_stage2_exit_hysteresis_f", nullptr,
     Sink::kFloat, &Setpoints::cool_stage2_exit_hysteresis_f, nullptr, nullptr},
    {"cool_stage2_over_high_f", "sp_cool_stage2_over_high_f", "Setpoints::cool_stage2_over_high_f", nullptr,
     Sink::kFloat, &Setpoints::cool_stage2_over_high_f, nullptr, nullptr},
    {"direct_wet_stress_min_dew_margin_f", "sp_direct_wet_stress_min_dew_margin_f",
     "Setpoints::direct_wet_stress_min_dew_margin_f", nullptr,
     Sink::kFloat, &Setpoints::direct_wet_stress_min_dew_margin_f, nullptr, nullptr},
    {"direct_wet_stress_vpd_margin_kpa", "sp_direct_wet_stress_vpd_margin_kpa",
     "Setpoints::direct_wet_stress_vpd_margin_kpa", nullptr,
     Sink::kFloat, &Setpoints::direct_wet_stress_vpd_margin_kpa, nullptr, nullptr},
    {"dwell_gate_ms", "sp_dwell_gate_ms", "Setpoints::dwell_gate_ms", nullptr,
     Sink::kU32, nullptr, nullptr, &Setpoints::dwell_gate_ms},
    {"enthalpy_close", nullptr, "Setpoints::econ_block",
     "modelled from controls.yaml:352-370 economiser deadband (dH >= close blocks)",
     Sink::kEconClose, nullptr, nullptr, nullptr},
    {"enthalpy_open", nullptr, "Setpoints::econ_block",
     "modelled from controls.yaml:352-370 economiser deadband (dH <= open unblocks)",
     Sink::kEconOpen, nullptr, nullptr, nullptr},
    {"fog_escalation_kpa", "sp_fog_escalation_kpa", "Setpoints::fog_escalation_kpa", nullptr,
     Sink::kFloat, &Setpoints::fog_escalation_kpa, nullptr, nullptr},
    {"heat_hysteresis", "sp_heat_hysteresis", "Setpoints::heat_hysteresis", nullptr,
     Sink::kFloat, &Setpoints::heat_hysteresis, nullptr, nullptr},
    {"min_fan_off_s", nullptr, nullptr,
     "controls.yaml:80 MIN_FAN_OFF_MS relay-dwell wrapper; not a determine_mode/resolve_equipment input",
     Sink::kNone, nullptr, nullptr, nullptr},
    {"min_fan_on_s", nullptr, nullptr,
     "controls.yaml:79 MIN_FAN_ON_MS relay-dwell wrapper; not a determine_mode/resolve_equipment input",
     Sink::kNone, nullptr, nullptr, nullptr},
    {"min_fog_off_s", nullptr, nullptr,
     "controls.yaml:84 MIN_FOG_OFF_MS relay-dwell wrapper; not a determine_mode/resolve_equipment input",
     Sink::kNone, nullptr, nullptr, nullptr},
    {"min_fog_on_s", nullptr, nullptr,
     "controls.yaml:83 MIN_FOG_ON_MS relay-dwell wrapper; not a determine_mode/resolve_equipment input",
     Sink::kNone, nullptr, nullptr, nullptr},
    {"min_heat_off_s", nullptr, nullptr,
     "controls.yaml:78 MIN_HEAT_OFF_MS relay-dwell wrapper; not a determine_mode/resolve_equipment input",
     Sink::kNone, nullptr, nullptr, nullptr},
    {"min_heat_on_s", nullptr, nullptr,
     "controls.yaml:77 MIN_HEAT_ON_MS relay-dwell wrapper; not a determine_mode/resolve_equipment input",
     Sink::kNone, nullptr, nullptr, nullptr},
    {"min_vent_off_s", nullptr, nullptr,
     "controls.yaml:82 MIN_VENT_OFF_MS relay-dwell wrapper; not a determine_mode/resolve_equipment input",
     Sink::kNone, nullptr, nullptr, nullptr},
    {"min_vent_on_s", nullptr, nullptr,
     "controls.yaml:81 MIN_VENT_ON_MS relay-dwell wrapper; not a determine_mode/resolve_equipment input",
     Sink::kNone, nullptr, nullptr, nullptr},
    {"mist_backoff_s", "sp_mist_backoff_s", "Setpoints::mist_backoff_ms", nullptr,
     Sink::kU32FromSeconds, nullptr, nullptr, &Setpoints::mist_backoff_ms},
    {"mist_max_closed_vent_s", "sp_sealed_max_s", "Setpoints::sealed_max_ms", nullptr,
     Sink::kU32FromSeconds, nullptr, nullptr, &Setpoints::sealed_max_ms},
    {"mist_thermal_relief_s", "sp_relief_duration_s", "Setpoints::relief_duration_ms", nullptr,
     Sink::kU32FromSeconds, nullptr, nullptr, &Setpoints::relief_duration_ms},
    {"mister_all_delay_s", "sp_mist_s2_delay_s", "Setpoints::mist_s2_delay_ms", nullptr,
     Sink::kU32FromSeconds, nullptr, nullptr, &Setpoints::mist_s2_delay_ms},
    {"mister_all_kpa", nullptr, nullptr,
     "controls.yaml:1480/1509 zone-mister arbiter; runs downstream of resolve_equipment",
     Sink::kNone, nullptr, nullptr, nullptr},
    {"mister_center_penalty", nullptr, nullptr,
     "controls.yaml:1304 zone-mister weighting; runs downstream of resolve_equipment",
     Sink::kNone, nullptr, nullptr, nullptr},
    {"mister_engage_delay_s", nullptr, nullptr,
     "controls.yaml:973 MISTER_ENGAGE_DELAY_MS; runs downstream of resolve_equipment",
     Sink::kNone, nullptr, nullptr, nullptr},
    {"mister_engage_kpa", nullptr, nullptr,
     "controls.yaml:1128/1521 zone-mister engage threshold; runs downstream of resolve_equipment",
     Sink::kNone, nullptr, nullptr, nullptr},
    {"mister_min_off_s", nullptr, nullptr,
     "controls.yaml:1247 zone-mister dwell floor; runs downstream of resolve_equipment",
     Sink::kNone, nullptr, nullptr, nullptr},
    {"mister_pulse_gap_s", nullptr, nullptr,
     "controls.yaml:1232 zone-mister cadence; runs downstream of resolve_equipment",
     Sink::kNone, nullptr, nullptr, nullptr},
    {"mister_pulse_on_s", nullptr, nullptr,
     "controls.yaml:1230 zone-mister cadence; runs downstream of resolve_equipment",
     Sink::kNone, nullptr, nullptr, nullptr},
    {"mister_vpd_weight", nullptr, nullptr,
     "controls.yaml:1493 zone-mister pulse weighting; runs downstream of resolve_equipment",
     Sink::kNone, nullptr, nullptr, nullptr},
    {"mister_water_budget_gal", nullptr, nullptr,
     "controls.yaml:865 daily water ceiling; runs downstream of resolve_equipment",
     Sink::kNone, nullptr, nullptr, nullptr},
    {"night_vpd_bias_kpa", nullptr, nullptr,
     "controls.yaml:483-496 biases the ON-CHIP solar VPD band before it becomes Setpoints::vpd_low/high; "
     "the corpus sp_vpd_low/sp_vpd_high columns are already post-bias captures, so re-applying it here "
     "would double-count the bias",
     Sink::kNone, nullptr, nullptr, nullptr},
    {"outdoor_staleness_max_s", "sp_outdoor_staleness_max_s", "Setpoints::outdoor_staleness_max_s", nullptr,
     Sink::kU32, nullptr, nullptr, &Setpoints::outdoor_staleness_max_s},
    {"sw_cool_all_fans_at_high_enabled", "sp_cool_all_fans_at_high_enabled",
     "Setpoints::cool_all_fans_at_high_enabled", nullptr,
     Sink::kBool, nullptr, &Setpoints::cool_all_fans_at_high_enabled, nullptr},
    {"sw_direct_wet_gate_enabled", nullptr, nullptr,
     "controls.yaml:1164 wet-gate branch; evaluated in the ESPHome lambda, not in greenhouse_logic.h",
     Sink::kNone, nullptr, nullptr, nullptr},
    {"sw_direct_wet_stress_override_enabled", "sp_direct_wet_stress_override_enabled",
     "Setpoints::direct_wet_stress_override_enabled", nullptr,
     Sink::kBool, nullptr, &Setpoints::direct_wet_stress_override_enabled, nullptr},
    {"sw_dwell_gate_enabled", "sp_sw_dwell_gate_enabled", "Setpoints::sw_dwell_gate_enabled", nullptr,
     Sink::kBool, nullptr, &Setpoints::sw_dwell_gate_enabled, nullptr},
    {"sw_fog_closes_vent", nullptr, nullptr,
     "controls.yaml:921 post-resolve_equipment relay interlock; not a compiled-logic input",
     Sink::kNone, nullptr, nullptr, nullptr},
    {"sw_mister_closes_vent", nullptr, nullptr,
     "controls.yaml:992 post-resolve_equipment relay interlock; not a compiled-logic input",
     Sink::kNone, nullptr, nullptr, nullptr},
    {"sw_summer_vent_enabled", "sp_sw_summer_vent_enabled", "Setpoints::sw_summer_vent_enabled", nullptr,
     Sink::kBool, nullptr, &Setpoints::sw_summer_vent_enabled, nullptr},
    {"temp_hysteresis", "sp_temp_hysteresis", "Setpoints::temp_hysteresis", nullptr,
     Sink::kFloat, &Setpoints::temp_hysteresis, nullptr, nullptr},
    {"vent_exchange_fraction", nullptr, "Setpoints::vent_exchange_fraction", nullptr,
     Sink::kFloat, &Setpoints::vent_exchange_fraction, nullptr, nullptr},
    {"vent_prefer_dp_delta_f", "sp_vent_prefer_dp_delta_f", "Setpoints::vent_prefer_dp_delta_f", nullptr,
     Sink::kFloat, &Setpoints::vent_prefer_dp_delta_f, nullptr, nullptr},
    {"vent_prefer_temp_delta_f", "sp_vent_prefer_temp_delta_f", "Setpoints::vent_prefer_temp_delta_f", nullptr,
     Sink::kFloat, &Setpoints::vent_prefer_temp_delta_f, nullptr, nullptr},
    {"vpd_hysteresis", "sp_vpd_hysteresis", "Setpoints::vpd_hysteresis", nullptr,
     Sink::kFloat, &Setpoints::vpd_hysteresis, nullptr, nullptr},
    {"vpd_watch_dwell_s", "sp_watch_dwell_s", "Setpoints::vpd_watch_dwell_ms", nullptr,
     Sink::kU32FromSeconds, nullptr, nullptr, &Setpoints::vpd_watch_dwell_ms},
};

// ── compile-time consistency with the generated wire schema ─────────────────

constexpr bool streq_const(const char* a, const char* b) {
    while (*a != '\0' && *a == *b) { ++a; ++b; }
    return *a == *b;
}

constexpr bool table_matches_wire_schema() {
    for (size_t i = 0; i < kFieldCount; ++i) {
        if (!streq_const(kFields[i].component, verdify_policy::kPolicyFields[i].name)) return false;
    }
    return true;
}

static_assert(kFieldCount == 48, "policy wire schema is no longer 48 fields");
static_assert(table_matches_wire_schema(),
              "policy_injection::kFields drifted from verdify_policy::kPolicyFields — regenerate both");

constexpr size_t count_injectable() {
    size_t n = 0;
    for (size_t i = 0; i < kFieldCount; ++i) {
        if (kFields[i].sink != Sink::kNone) ++n;
    }
    return n;
}

// 25 direct Setpoints members + the modelled enthalpy pair.
constexpr size_t kInjectableCount = count_injectable();
static_assert(kInjectableCount == 27,
              "injectable-component count changed — update the mapping doc and the prefix-replay probe");

inline int index_of(const char* component) {
    for (size_t i = 0; i < kFieldCount; ++i) {
        if (std::strcmp(kFields[i].component, component) == 0) return (int)i;
    }
    return -1;
}

// ── Economiser deadband model (controls.yaml:352-370) ───────────────────────
//
// The ESP32 keeps a latched econ_block across loops: dH <= enthalpy_open
// unblocks, dH >= enthalpy_close blocks, in between the latch holds, and a
// NaN intake reading blocks. Reproduced verbatim so the enthalpy pair can be
// imposed on the compiled logic through Setpoints::econ_block.
struct EconLatch {
    bool blocked = false;
    void reset() { blocked = false; }
    bool step(float enthalpy_delta, float open_kjkg, float close_kjkg) {
        if (std::isnan(enthalpy_delta)) {
            blocked = true;
        } else {
            if (enthalpy_delta <= open_kjkg) blocked = false;
            if (enthalpy_delta >= close_kjkg) blocked = true;
        }
        return blocked;
    }
};

// ── Complete 48-field policy state ──────────────────────────────────────────

struct PolicyState {
    bool loaded = false;
    double values[kFieldCount] = {};   // engineering units
    int64_t raws[kFieldCount] = {};    // wire-scaled integers
    std::string path;
    std::string sha256_hex;
};

inline std::string hex32(const uint8_t digest[32]) {
    static const char* kHex = "0123456789abcdef";
    std::string out;
    out.reserve(64);
    for (int i = 0; i < 32; ++i) {
        out.push_back(kHex[digest[i] >> 4]);
        out.push_back(kHex[digest[i] & 0x0F]);
    }
    return out;
}

// Wire-envelope check for ONE field. Rejects (never rounds/clamps) a value
// that is off the field's wire scale or outside [min_raw, max_raw].
inline bool check_wire_value(size_t index, double value, int64_t& raw_out, std::string& err) {
    const verdify_policy::PolicyFieldDef& def = verdify_policy::kPolicyFields[index];
    if (!std::isfinite(value)) {
        err = std::string(def.name) + ": value is not finite";
        return false;
    }
    const double scaled = value * (double)def.scale;
    const double nearest = std::nearbyint(scaled);
    if (std::fabs(scaled - nearest) > 1e-6) {
        err = std::string(def.name) + ": value " + std::to_string(value)
            + " is off the wire scale (1/" + std::to_string(def.scale) + ")";
        return false;
    }
    const int64_t raw = (int64_t)nearest;
    if (raw < def.min_raw || raw > def.max_raw) {
        err = std::string(def.name) + ": raw " + std::to_string(raw)
            + " outside wire envelope [" + std::to_string(def.min_raw)
            + "," + std::to_string(def.max_raw) + "]";
        return false;
    }
    raw_out = raw;
    return true;
}

// The three physically-required relations from policy_vector.h:182-191.
inline bool check_cross_field(const int64_t raws[kFieldCount], std::string& err) {
    if (raws[verdify_policy::kPF_mister_engage_kpa] > raws[verdify_policy::kPF_mister_all_kpa]) {
        err = "cross-field: mister_engage_kpa > mister_all_kpa";
        return false;
    }
    if (raws[verdify_policy::kPF_enthalpy_open] >= raws[verdify_policy::kPF_enthalpy_close]) {
        err = "cross-field: enthalpy_open >= enthalpy_close";
        return false;
    }
    if (raws[verdify_policy::kPF_mister_engage_delay_s] > raws[verdify_policy::kPF_mister_all_delay_s]) {
        err = "cross-field: mister_engage_delay_s > mister_all_delay_s";
        return false;
    }
    return true;
}

// Validate a complete 48-field mapping. `provided` must contain every
// canonical component exactly once; anything missing, unknown, duplicated,
// off-scale, out of envelope, or cross-field invalid is REJECTED.
inline bool build_policy_state(const std::vector<std::pair<std::string, double>>& provided,
                               PolicyState& out, std::string& err) {
    bool seen[kFieldCount] = {false};
    double values[kFieldCount] = {};
    for (const auto& kv : provided) {
        const int index = index_of(kv.first.c_str());
        if (index < 0) {
            err = "unknown_component: " + kv.first;
            return false;
        }
        if (seen[index]) {
            err = "duplicate_component: " + kv.first;
            return false;
        }
        seen[index] = true;
        values[index] = kv.second;
    }
    std::string missing;
    for (size_t i = 0; i < kFieldCount; ++i) {
        if (!seen[i]) {
            if (!missing.empty()) missing += ",";
            missing += kFields[i].component;
        }
    }
    if (!missing.empty()) {
        err = "incomplete_state: missing=" + missing;
        return false;
    }
    int64_t raws[kFieldCount] = {};
    for (size_t i = 0; i < kFieldCount; ++i) {
        if (!check_wire_value(i, values[i], raws[i], err)) return false;
    }
    if (!check_cross_field(raws, err)) return false;
    for (size_t i = 0; i < kFieldCount; ++i) {
        out.values[i] = values[i];
        out.raws[i] = raws[i];
    }
    out.loaded = true;
    return true;
}

// Text format: one `name value` (or `name=value`) pair per line; `#` starts a
// comment; blank lines ignored. Deliberately dependency-free so the file can
// be produced by a shell script, a Python driver, or --print-policy-template.
inline bool load_policy_state_file(const std::string& path, PolicyState& out, std::string& err) {
    std::ifstream f(path, std::ios::binary);
    if (!f) {
        err = "cannot open policy state file: " + path;
        return false;
    }
    std::ostringstream raw;
    raw << f.rdbuf();
    const std::string text = raw.str();

    uint8_t digest[32];
    verdify_policy::Sha256::hash(reinterpret_cast<const uint8_t*>(text.data()), text.size(), digest);

    std::vector<std::pair<std::string, double>> provided;
    std::istringstream lines(text);
    std::string line;
    int line_no = 0;
    while (std::getline(lines, line)) {
        ++line_no;
        const size_t hash = line.find('#');
        if (hash != std::string::npos) line.erase(hash);
        for (char& c : line) {
            if (c == '=' || c == '\t' || c == ',' || c == '\r') c = ' ';
        }
        std::istringstream fields(line);
        std::string name, value;
        if (!(fields >> name)) continue;
        if (!(fields >> value)) {
            err = path + ":" + std::to_string(line_no) + ": '" + name + "' has no value";
            return false;
        }
        std::string extra;
        if (fields >> extra) {
            err = path + ":" + std::to_string(line_no) + ": trailing token '" + extra + "'";
            return false;
        }
        double parsed = 0.0;
        if (value == "true" || value == "t" || value == "on") {
            parsed = 1.0;
        } else if (value == "false" || value == "f" || value == "off") {
            parsed = 0.0;
        } else {
            try {
                size_t consumed = 0;
                parsed = std::stod(value, &consumed);
                if (consumed != value.size()) throw std::invalid_argument("trailing");
            } catch (...) {
                err = path + ":" + std::to_string(line_no) + ": '" + name
                    + "' value '" + value + "' is not a number";
                return false;
            }
        }
        provided.emplace_back(name, parsed);
    }
    if (!build_policy_state(provided, out, err)) {
        err = path + ": " + err;
        return false;
    }
    out.path = path;
    out.sha256_hex = hex32(digest);
    return true;
}

// The immutable registry-default vector from policy_rom_baseline_generated.h,
// useful as a known-good full-48 state for tests and templates.
inline PolicyState rom_baseline_state() {
    PolicyState state;
    for (size_t i = 0; i < kFieldCount; ++i) {
        state.raws[i] = verdify_policy::kRomBaselineRaws[i];
        state.values[i] = (double)state.raws[i] / (double)verdify_policy::kPolicyFields[i].scale;
    }
    state.loaded = true;
    state.path = "rom-baseline";
    state.sha256_hex = "";
    return state;
}

inline void print_policy_template(std::FILE* out, const PolicyState& state) {
    std::fprintf(out, "# replay_invariants --policy-state template (all %zu canonical components required)\n",
                 kFieldCount);
    std::fprintf(out, "# values are engineering units on the wire scale in policy_vector_generated.h\n");
    for (size_t i = 0; i < kFieldCount; ++i) {
        const verdify_policy::PolicyFieldDef& def = verdify_policy::kPolicyFields[i];
        std::fprintf(out, "%-38s %-12.6g  # wire_id=%u scale=%u raw=[%lld,%lld]%s\n",
                     kFields[i].component, state.values[i], (unsigned)def.wire_id, (unsigned)def.scale,
                     (long long)def.min_raw, (long long)def.max_raw,
                     kFields[i].sink == Sink::kNone ? " NOT-IMPOSABLE" : "");
    }
}

// ── Coverage declaration ────────────────────────────────────────────────────
//
// The harness declares, per run, exactly which components it actually imposed
// and where each value came from, so a downstream adjudicator never has to
// infer coverage from the harness source.
struct Coverage {
    std::string source[kFieldCount];   // "" | "corpus:sp_x" | "policy_state"
    long applied_rows[kFieldCount] = {0};
    bool column_in_header[kFieldCount] = {false};
    bool column_read_enabled[kFieldCount] = {false};
    bool policy_state_loaded = false;
    std::string policy_state_path;
    std::string policy_state_sha256;
    std::string status = "ok";
    std::string reject_reason;

    Coverage() {
        for (size_t i = 0; i < kFieldCount; ++i) {
            lookup_[kFields[i].component] = (int)i;
            last_applied_row_[i] = -1;
        }
    }

    void begin_row(long row_index) { current_row_ = row_index; }

    void mark(const char* component, const char* src) {
        auto it = lookup_.find(component);
        if (it == lookup_.end()) return;
        const int index = it->second;
        // Corpus assignment may be overwritten later in the same row by the
        // out-of-band policy state. Count the effective row once, while keeping
        // the final source that actually reached validate_setpoints().
        source[index] = src;
        if (last_applied_row_[index] != current_row_) {
            applied_rows[index]++;
            last_applied_row_[index] = current_row_;
        }
    }

    // Recorded once, after the corpus header is parsed, so the unimposed
    // reasons can tell "the corpus has no such column" apart from "the column
    // is there but this run did not read it".
    void note_corpus_column(const char* component, bool in_header, bool read_enabled) {
        auto it = lookup_.find(component);
        if (it == lookup_.end()) return;
        column_in_header[it->second] = in_header;
        column_read_enabled[it->second] = read_enabled;
    }

    size_t imposed_count(long rows) const {
        size_t n = 0;
        for (size_t i = 0; i < kFieldCount; ++i) {
            if (rows > 0 && !source[i].empty() && applied_rows[i] == rows) ++n;
        }
        return n;
    }

 private:
    std::unordered_map<std::string, int> lookup_;
    long current_row_ = 0;
    long last_applied_row_[kFieldCount];
};

inline std::string json_escape(const char* s) {
    std::string out;
    for (const char* p = s; *p != '\0'; ++p) {
        switch (*p) {
            case '"':  out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n";  break;
            default:   out.push_back(*p);
        }
    }
    return out;
}

constexpr char kCoverageMarker[] = "##replay-invariants-coverage-v1";
constexpr char kCoverageSchema[] = "verdify-replay-invariants-coverage-v1";

// ONE machine-readable line on stdout. Shape:
//
//   ##replay-invariants-coverage-v1 {"schema":...,"field_count":48,
//     "injectable_count":27,"imposed_count":N,"full_coverage":bool,
//     "status":"ok"|"rejected","policy_state":path|null,
//     "policy_state_sha256":hex|null,
//     "full_injectable_coverage":bool,"invariant_violations":N,"distinct_invariants":M,
//     "imposed":{"component":{"source":"policy_state","rows":N,
//                             "firmware_symbol":"Setpoints::x"}, ...},
//     "unimposed":{"component":"reason", ...}}
//
// `imposed` lists ONLY components this run actually held on every evaluated
// row; `unimposed` carries the reason for each of the rest, so partial
// coverage can never be mistaken for certification.
//
// Exact violation counts are evidence, not process-code arithmetic: every
// safety breach exits 1 while the pre-existing corpus-unusable case keeps exit
// 2. Carry both counts here so callers never have to infer them from an exit
// code and cannot accidentally accept two distinct invariant failures.
inline void print_coverage_line(std::FILE* out, const Coverage& cov, long rows,
                                int invariant_violations = 0, size_t distinct_invariants = 0) {
    std::string imposed, unimposed;
    size_t imposed_n = 0;
    for (size_t i = 0; i < kFieldCount; ++i) {
        const FieldSpec& spec = kFields[i];
        const bool held = rows > 0 && !cov.source[i].empty() && cov.applied_rows[i] == rows;
        if (held) {
            if (!imposed.empty()) imposed += ",";
            imposed += "\"" + json_escape(spec.component) + "\":{\"source\":\""
                     + json_escape(cov.source[i].c_str()) + "\",\"rows\":"
                     + std::to_string(cov.applied_rows[i]) + ",\"firmware_symbol\":\""
                     + json_escape(spec.firmware_symbol ? spec.firmware_symbol : "") + "\"}";
            ++imposed_n;
            continue;
        }
        std::string reason;
        if (spec.sink == Sink::kNone) {
            reason = spec.note ? spec.note : "no compiled-logic consumer";
        } else if (!cov.source[i].empty() && cov.applied_rows[i] > 0) {
            reason = "effective assignment held " + std::to_string(cov.applied_rows[i])
                   + "/" + std::to_string(rows) + " rows; partial-row coverage is unqualified";
        } else if (!cov.source[i].empty()) {
            reason = std::string("corpus column ") + spec.corpus_column
                   + " read but carried no parseable value on any row";
        } else if (cov.column_in_header[i] && !cov.column_read_enabled[i]) {
            reason = std::string("corpus column ") + spec.corpus_column
                   + " present but not read by this run; pass --corpus-policy-columns"
                     " or --policy-state to impose it";
        } else if (cov.column_in_header[i]) {
            reason = std::string("corpus column ") + spec.corpus_column
                   + " read but carried no parseable value on any row";
        } else if (spec.corpus_column != nullptr) {
            reason = std::string("corpus column ") + spec.corpus_column
                   + " absent from this corpus; supply --policy-state to impose it";
        } else {
            reason = "no corpus column in the tracked corpus; supply --policy-state to impose it";
        }
        if (!unimposed.empty()) unimposed += ",";
        unimposed += "\"" + json_escape(spec.component) + "\":\"" + json_escape(reason.c_str()) + "\"";
    }
    std::fprintf(out,
        "%s {\"schema\":\"%s\",\"field_count\":%zu,\"injectable_count\":%zu,"
        "\"imposed_count\":%zu,\"full_coverage\":%s,\"full_injectable_coverage\":%s,"
        "\"rows\":%ld,\"status\":\"%s\","
        "\"policy_state\":%s%s%s,\"policy_state_sha256\":%s%s%s,\"reject_reason\":%s%s%s,"
        "\"invariant_violations\":%d,\"distinct_invariants\":%zu,"
        "\"imposed\":{%s},\"unimposed\":{%s}}\n",
        kCoverageMarker, kCoverageSchema, kFieldCount, kInjectableCount,
        imposed_n, imposed_n == kFieldCount ? "true" : "false",
        imposed_n == kInjectableCount ? "true" : "false", rows, cov.status.c_str(),
        cov.policy_state_path.empty() ? "" : "\"",
        cov.policy_state_path.empty() ? "null" : json_escape(cov.policy_state_path.c_str()).c_str(),
        cov.policy_state_path.empty() ? "" : "\"",
        cov.policy_state_sha256.empty() ? "" : "\"",
        cov.policy_state_sha256.empty() ? "null" : cov.policy_state_sha256.c_str(),
        cov.policy_state_sha256.empty() ? "" : "\"",
        cov.reject_reason.empty() ? "" : "\"",
        cov.reject_reason.empty() ? "null" : json_escape(cov.reject_reason.c_str()).c_str(),
        cov.reject_reason.empty() ? "" : "\"",
        invariant_violations, distinct_invariants,
        imposed.c_str(), unimposed.c_str());
}

// Impose the complete policy state on one row's Setpoints. Runs AFTER the
// corpus `sp_*` assignments and BEFORE validate_setpoints(), which is exactly
// where controls.yaml applies its own policy reads on the device.
inline void apply_policy_state(const PolicyState& state, Setpoints& sp,
                               EconLatch& econ, float enthalpy_delta, Coverage& cov) {
    if (!state.loaded) return;
    float econ_open = 0.0f;
    float econ_close = 0.0f;
    bool have_econ_open = false;
    bool have_econ_close = false;
    for (size_t i = 0; i < kFieldCount; ++i) {
        const FieldSpec& spec = kFields[i];
        const double value = state.values[i];
        switch (spec.sink) {
            case Sink::kFloat:
                sp.*(spec.fptr) = (float)value;
                cov.mark(spec.component, "policy_state");
                break;
            case Sink::kBool:
                sp.*(spec.bptr) = (value != 0.0);
                cov.mark(spec.component, "policy_state");
                break;
            case Sink::kU32:
                sp.*(spec.uptr) = (uint32_t)value;
                cov.mark(spec.component, "policy_state");
                break;
            case Sink::kU32FromSeconds:
                sp.*(spec.uptr) = (uint32_t)(value * 1000.0);
                cov.mark(spec.component, "policy_state");
                break;
            case Sink::kEconOpen:
                econ_open = (float)value;
                have_econ_open = true;
                break;
            case Sink::kEconClose:
                econ_close = (float)value;
                have_econ_close = true;
                break;
            case Sink::kNone:
                break;
        }
    }
    if (have_econ_open && have_econ_close) {
        sp.econ_block = econ.step(enthalpy_delta, econ_open, econ_close);
        cov.mark("enthalpy_open", "policy_state");
        cov.mark("enthalpy_close", "policy_state");
    }
}

}  // namespace policy_injection
