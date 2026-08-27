/*
 * test_policy_injection.cpp — Native x86 tests for the replay harness's
 * full-48 policy-vector injection surface (firmware/test/policy_injection.h).
 *
 * Test-harness surface only: nothing here exercises new firmware behavior, it
 * checks that an out-of-band 48-field policy state is (a) imposed exactly on
 * the compiled control surface, (b) rejected rather than repaired when it is
 * incomplete or off the wire grid, and (c) still able to produce an invariant
 * breach — a harness that cannot fail cannot certify.
 *
 * Compile: g++ -std=c++17 -I ../lib -o test_policy_injection test_policy_injection.cpp
 */

#include "greenhouse_logic.h"
#include "invariants.h"
#include "policy_injection.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <string>
#include <utility>
#include <vector>

static int tests_passed = 0;
static int tests_failed = 0;

struct TestEntry { const char* name; void (*fn)(); };
static std::vector<TestEntry> test_registry;

#define TEST(name) \
    static void test_##name(); \
    static struct Register_##name { \
        Register_##name() { test_registry.push_back({#name, test_##name}); } \
    } reg_##name; \
    static void test_##name()

#define ASSERT_EQ(a, b) do { if ((a) != (b)) { printf("  FAIL: %s != %s (line %d)\n", #a, #b, __LINE__); tests_failed++; return; } } while(0)
#define ASSERT_TRUE(x) do { if (!(x)) { printf("  FAIL: %s (line %d)\n", #x, __LINE__); tests_failed++; return; } } while(0)
#define ASSERT_FALSE(x) ASSERT_TRUE(!(x))
#define ASSERT_NEAR(a, b, eps) do { \
    if (std::fabs((double)(a) - (double)(b)) > (eps)) { \
        printf("  FAIL: %s (%g) != %s (%g) (line %d)\n", #a, (double)(a), #b, (double)(b), __LINE__); \
        tests_failed++; return; } } while(0)
#define PASS() tests_passed++

using policy_injection::Coverage;
using policy_injection::EconLatch;
using policy_injection::PolicyState;
using policy_injection::Sink;

// ── helpers ─────────────────────────────────────────────────────────────────

// A complete 48-field state: registry defaults with every INJECTABLE component
// moved off its default to a distinct, on-grid, in-envelope value. Each value
// is also inside validate_setpoints()' clamp for its member, so the round-trip
// below is exact rather than "exact unless the firmware repairs it".
static std::vector<std::pair<std::string, double>> distinct_full_state() {
    std::vector<std::pair<std::string, double>> out;
    for (size_t i = 0; i < policy_injection::kFieldCount; ++i) {
        out.emplace_back(policy_injection::kFields[i].component,
                         (double)verdify_policy::kRomBaselineRaws[i]
                             / (double)verdify_policy::kPolicyFields[i].scale);
    }
    const std::pair<const char*, double> overrides[] = {
        {"band_track_fraction", 0.25},
        {"cold_vent_guard_delta_f", 12.5},
        {"cool_exit_hysteresis_f", 2.5},
        {"cool_stage2_exit_hysteresis_f", 2.2},
        {"cool_stage2_over_high_f", 2.4},
        {"direct_wet_stress_min_dew_margin_f", 12.5},
        {"direct_wet_stress_vpd_margin_kpa", 0.35},
        {"dwell_gate_ms", 900000.0},
        {"enthalpy_close", 7.5},
        {"enthalpy_open", -3.5},
        {"fog_escalation_kpa", 0.3},
        {"heat_hysteresis", 2.1},
        {"mist_backoff_s", 1500.0},
        {"mist_max_closed_vent_s", 780.0},
        {"mist_thermal_relief_s", 150.0},
        {"mister_all_delay_s", 420.0},
        {"outdoor_staleness_max_s", 900.0},
        {"sw_cool_all_fans_at_high_enabled", 1.0},
        {"sw_direct_wet_stress_override_enabled", 0.0},
        {"sw_dwell_gate_enabled", 1.0},
        {"sw_summer_vent_enabled", 0.0},
        {"temp_hysteresis", 2.7},
        {"vent_exchange_fraction", 0.55},
        {"vent_prefer_dp_delta_f", 11.5},
        {"vent_prefer_temp_delta_f", 13.5},
        {"vpd_hysteresis", 0.45},
        {"vpd_watch_dwell_s", 105.0},
    };
    for (const auto& kv : overrides) {
        for (auto& entry : out) {
            if (entry.first == kv.first) { entry.second = kv.second; break; }
        }
    }
    return out;
}

// A band wide enough that validate_setpoints() does not repair any injected
// value (notably vpd_hysteresis, capped at vpd_high * 0.5).
static Setpoints band_setpoints() {
    Setpoints sp = default_setpoints();
    sp.temp_low = 65.0f;
    sp.temp_high = 85.0f;
    sp.vpd_low = 0.60f;
    sp.vpd_high = 1.40f;
    sp.safety_max = 100.0f;
    sp.safety_min = 45.0f;
    sp.vpd_max_safe = 3.0f;
    sp.vpd_min_safe = 0.30f;
    sp.dehum_aggressive_kpa = 0.30f;
    return sp;
}

static double value_of(const PolicyState& state, const char* component) {
    const int index = policy_injection::index_of(component);
    return index < 0 ? NAN : state.values[index];
}

// ── 1. table / schema binding ───────────────────────────────────────────────

TEST(injection_table_matches_the_48_field_wire_schema) {
    ASSERT_EQ(policy_injection::kFieldCount, (size_t)48);
    for (size_t i = 0; i < policy_injection::kFieldCount; ++i) {
        ASSERT_EQ(std::strcmp(policy_injection::kFields[i].component,
                              verdify_policy::kPolicyFields[i].name), 0);
    }
    // Exactly 27 components reach the compiled control logic; the rest carry a
    // source-cited reason instead of an invented firmware symbol.
    size_t injectable = 0;
    for (size_t i = 0; i < policy_injection::kFieldCount; ++i) {
        const auto& spec = policy_injection::kFields[i];
        if (spec.sink == Sink::kNone) {
            ASSERT_TRUE(spec.firmware_symbol == nullptr);
            ASSERT_TRUE(spec.note != nullptr && spec.note[0] != '\0');
        } else {
            ASSERT_TRUE(spec.firmware_symbol != nullptr);
            ++injectable;
        }
    }
    ASSERT_EQ(injectable, policy_injection::kInjectableCount);
    ASSERT_EQ(injectable, (size_t)27);
    PASS();
}

// ── 2. full-48 injection round-trip ─────────────────────────────────────────

TEST(full_48_injection_round_trips_onto_every_injectable_firmware_symbol) {
    PolicyState state;
    std::string err;
    ASSERT_TRUE(policy_injection::build_policy_state(distinct_full_state(), state, err));
    ASSERT_TRUE(err.empty());

    Coverage coverage;
    EconLatch econ;
    Setpoints sp = band_setpoints();
    coverage.begin_row(0);
    policy_injection::apply_policy_state(state, sp, econ, -5.0f, coverage);

    // Every injectable component was declared imposed, and nothing else was.
    ASSERT_EQ(coverage.imposed_count(1), (size_t)27);
    for (size_t i = 0; i < policy_injection::kFieldCount; ++i) {
        const bool imposed = !coverage.source[i].empty() && coverage.applied_rows[i] > 0;
        ASSERT_EQ(imposed, policy_injection::kFields[i].sink != Sink::kNone);
        if (imposed) ASSERT_EQ(coverage.source[i], std::string("policy_state"));
    }

    // Direct Setpoints members carry the exact injected value.
    ASSERT_NEAR(sp.band_track_fraction, 0.25, 1e-6);
    ASSERT_NEAR(sp.cold_vent_guard_delta_f, 12.5, 1e-6);
    ASSERT_NEAR(sp.cool_exit_hysteresis_f, 2.5, 1e-6);
    ASSERT_NEAR(sp.cool_stage2_exit_hysteresis_f, 2.2, 1e-6);
    ASSERT_NEAR(sp.cool_stage2_over_high_f, 2.4, 1e-6);
    ASSERT_NEAR(sp.direct_wet_stress_min_dew_margin_f, 12.5, 1e-6);
    ASSERT_NEAR(sp.direct_wet_stress_vpd_margin_kpa, 0.35, 1e-6);
    ASSERT_EQ(sp.dwell_gate_ms, 900000u);
    ASSERT_NEAR(sp.fog_escalation_kpa, 0.3, 1e-6);
    ASSERT_NEAR(sp.heat_hysteresis, 2.1, 1e-6);
    ASSERT_EQ(sp.mist_backoff_ms, 1500000u);
    ASSERT_EQ(sp.sealed_max_ms, 780000u);
    ASSERT_EQ(sp.relief_duration_ms, 150000u);
    ASSERT_EQ(sp.mist_s2_delay_ms, 420000u);
    ASSERT_EQ(sp.outdoor_staleness_max_s, 900u);
    ASSERT_TRUE(sp.cool_all_fans_at_high_enabled);
    ASSERT_FALSE(sp.direct_wet_stress_override_enabled);
    ASSERT_TRUE(sp.sw_dwell_gate_enabled);
    ASSERT_FALSE(sp.sw_summer_vent_enabled);
    ASSERT_NEAR(sp.temp_hysteresis, 2.7, 1e-6);
    ASSERT_NEAR(sp.vent_exchange_fraction, 0.55, 1e-6);
    ASSERT_NEAR(sp.vent_prefer_dp_delta_f, 11.5, 1e-6);
    ASSERT_NEAR(sp.vent_prefer_temp_delta_f, 13.5, 1e-6);
    ASSERT_NEAR(sp.vpd_hysteresis, 0.45, 1e-6);
    ASSERT_EQ(sp.vpd_watch_dwell_ms, 105000u);

    // …and survives validate_setpoints() unrepaired, which is what makes the
    // imposed state the one the FSM actually runs on.
    validate_setpoints(sp);
    ASSERT_NEAR(sp.band_track_fraction, 0.25, 1e-6);
    ASSERT_NEAR(sp.cold_vent_guard_delta_f, 12.5, 1e-6);
    ASSERT_NEAR(sp.cool_exit_hysteresis_f, 2.5, 1e-6);
    ASSERT_NEAR(sp.cool_stage2_exit_hysteresis_f, 2.2, 1e-6);
    ASSERT_NEAR(sp.cool_stage2_over_high_f, 2.4, 1e-6);
    ASSERT_NEAR(sp.direct_wet_stress_min_dew_margin_f, 12.5, 1e-6);
    ASSERT_NEAR(sp.direct_wet_stress_vpd_margin_kpa, 0.35, 1e-6);
    ASSERT_EQ(sp.dwell_gate_ms, 900000u);
    ASSERT_NEAR(sp.fog_escalation_kpa, 0.3, 1e-6);
    ASSERT_NEAR(sp.heat_hysteresis, 2.1, 1e-6);
    ASSERT_EQ(sp.mist_backoff_ms, 1500000u);
    ASSERT_EQ(sp.sealed_max_ms, 780000u);
    ASSERT_EQ(sp.relief_duration_ms, 150000u);
    ASSERT_EQ(sp.mist_s2_delay_ms, 420000u);
    ASSERT_EQ(sp.outdoor_staleness_max_s, 900u);
    ASSERT_NEAR(sp.temp_hysteresis, 2.7, 1e-6);
    ASSERT_NEAR(sp.vent_exchange_fraction, 0.55, 1e-6);
    ASSERT_NEAR(sp.vent_prefer_dp_delta_f, 11.5, 1e-6);
    ASSERT_NEAR(sp.vent_prefer_temp_delta_f, 13.5, 1e-6);
    ASSERT_NEAR(sp.vpd_hysteresis, 0.45, 1e-6);
    ASSERT_EQ(sp.vpd_watch_dwell_ms, 105000u);
    PASS();
}

TEST(enthalpy_pair_reaches_econ_block_through_the_controls_yaml_deadband) {
    PolicyState state;
    std::string err;
    ASSERT_TRUE(policy_injection::build_policy_state(distinct_full_state(), state, err));
    ASSERT_NEAR(value_of(state, "enthalpy_open"), -3.5, 1e-9);
    ASSERT_NEAR(value_of(state, "enthalpy_close"), 7.5, 1e-9);

    Coverage coverage;
    EconLatch econ;
    Setpoints sp = band_setpoints();

    // dH below the open threshold → economiser unblocked.
    coverage.begin_row(0);
    policy_injection::apply_policy_state(state, sp, econ, -5.0f, coverage);
    ASSERT_FALSE(sp.econ_block);
    // Inside the deadband → the latch holds its previous value.
    coverage.begin_row(1);
    policy_injection::apply_policy_state(state, sp, econ, 0.0f, coverage);
    ASSERT_FALSE(sp.econ_block);
    // At/above the close threshold → blocked, and it latches through the band.
    coverage.begin_row(2);
    policy_injection::apply_policy_state(state, sp, econ, 9.0f, coverage);
    ASSERT_TRUE(sp.econ_block);
    coverage.begin_row(3);
    policy_injection::apply_policy_state(state, sp, econ, 0.0f, coverage);
    ASSERT_TRUE(sp.econ_block);
    // A NaN intake reading fails safe to blocked (controls.yaml:356-361).
    econ.reset();
    coverage.begin_row(4);
    policy_injection::apply_policy_state(state, sp, econ, NAN, coverage);
    ASSERT_TRUE(sp.econ_block);
    PASS();
}

TEST(rom_baseline_state_is_complete_and_wire_valid) {
    const PolicyState rom = policy_injection::rom_baseline_state();
    ASSERT_TRUE(rom.loaded);
    std::vector<std::pair<std::string, double>> provided;
    for (size_t i = 0; i < policy_injection::kFieldCount; ++i) {
        provided.emplace_back(policy_injection::kFields[i].component, rom.values[i]);
    }
    PolicyState rebuilt;
    std::string err;
    ASSERT_TRUE(policy_injection::build_policy_state(provided, rebuilt, err));
    for (size_t i = 0; i < policy_injection::kFieldCount; ++i) {
        ASSERT_EQ(rebuilt.raws[i], verdify_policy::kRomBaselineRaws[i]);
    }
    PASS();
}

// ── 3. invalid injections are rejected, never repaired ──────────────────────

static bool rejected(const std::vector<std::pair<std::string, double>>& provided, std::string& err) {
    PolicyState state;
    err.clear();
    const bool ok = policy_injection::build_policy_state(provided, state, err);
    return !ok && !err.empty() && !state.loaded;
}

static void set_field(std::vector<std::pair<std::string, double>>& state,
                      const char* component, double value) {
    for (auto& entry : state) {
        if (entry.first == component) { entry.second = value; return; }
    }
}

TEST(injection_outside_the_wire_envelope_is_rejected) {
    std::string err;
    auto over = distinct_full_state();
    set_field(over, "fog_escalation_kpa", 9.9);  // envelope is raw [1,5] @ scale 10
    ASSERT_TRUE(rejected(over, err));
    ASSERT_TRUE(err.find("outside wire envelope") != std::string::npos);

    auto under = distinct_full_state();
    set_field(under, "vpd_watch_dwell_s", 5.0);  // envelope is raw [15,120] @ scale 1
    ASSERT_TRUE(rejected(under, err));
    ASSERT_TRUE(err.find("outside wire envelope") != std::string::npos);

    auto negative = distinct_full_state();
    set_field(negative, "dwell_gate_ms", -1.0);
    ASSERT_TRUE(rejected(negative, err));
    PASS();
}

TEST(injection_off_the_wire_scale_is_rejected_not_rounded) {
    std::string err;
    auto off = distinct_full_state();
    set_field(off, "vpd_hysteresis", 0.123);  // scale 20 → only 0.05 steps exist
    ASSERT_TRUE(rejected(off, err));
    ASSERT_TRUE(err.find("off the wire scale") != std::string::npos);

    auto nonfinite = distinct_full_state();
    set_field(nonfinite, "temp_hysteresis", NAN);
    ASSERT_TRUE(rejected(nonfinite, err));
    ASSERT_TRUE(err.find("not finite") != std::string::npos);
    PASS();
}

TEST(incomplete_unknown_or_duplicate_state_is_rejected) {
    std::string err;
    auto incomplete = distinct_full_state();
    incomplete.pop_back();
    ASSERT_TRUE(rejected(incomplete, err));
    ASSERT_TRUE(err.find("incomplete_state") != std::string::npos);

    auto unknown = distinct_full_state();
    unknown.emplace_back("temp_target", 75.0);  // migration-182 publish, not a component
    ASSERT_TRUE(rejected(unknown, err));
    ASSERT_TRUE(err.find("unknown_component") != std::string::npos);

    auto duplicate = distinct_full_state();
    duplicate.emplace_back("temp_hysteresis", 1.5);
    ASSERT_TRUE(rejected(duplicate, err));
    ASSERT_TRUE(err.find("duplicate_component") != std::string::npos);
    PASS();
}

TEST(cross_field_violations_are_rejected) {
    std::string err;
    auto enthalpy = distinct_full_state();
    set_field(enthalpy, "enthalpy_open", 0.0);
    set_field(enthalpy, "enthalpy_close", -1.0);
    ASSERT_TRUE(rejected(enthalpy, err));
    ASSERT_TRUE(err.find("enthalpy_open >= enthalpy_close") != std::string::npos);

    auto mister = distinct_full_state();
    set_field(mister, "mister_engage_kpa", 2.5);
    set_field(mister, "mister_all_kpa", 2.0);
    ASSERT_TRUE(rejected(mister, err));
    ASSERT_TRUE(err.find("mister_engage_kpa > mister_all_kpa") != std::string::npos);

    auto delay = distinct_full_state();
    set_field(delay, "mister_engage_delay_s", 300.0);
    set_field(delay, "mister_all_delay_s", 120.0);
    ASSERT_TRUE(rejected(delay, err));
    ASSERT_TRUE(err.find("mister_engage_delay_s > mister_all_delay_s") != std::string::npos);
    PASS();
}

// ── 4. the harness can still fail ───────────────────────────────────────────

// Drive the same per-row pipeline replay_invariants.cpp runs: impose the state,
// validate, step the FSM, mirror the row into a TraceRow, run the invariants.
// `hot_minutes >= 0` switches the row VPD from `vpd_kpa` to `vpd_settled`
// after that many minutes, which is how a seal that the FSM wants to leave is
// produced without any safety/compliance condition preempting the dwell gate.
static int replay_minutes(const PolicyState& state, int minutes, float temp_f, float vpd_kpa,
                          int hot_minutes = -1, float vpd_settled = 1.0f) {
    invariants::Runner runner;
    ControlState control = initial_state();
    EconLatch econ;
    Coverage coverage;
    int violations = 0;
    auto report = [](int, const char*, const invariants::TraceRow&, const char*) {};

    for (int minute = 0; minute < minutes; ++minute) {
        const float row_vpd = (hot_minutes >= 0 && minute >= hot_minutes) ? vpd_settled : vpd_kpa;
        Setpoints sp = band_setpoints();
        coverage.begin_row(minute);
        policy_injection::apply_policy_state(state, sp, econ, -5.0f, coverage);
        sp.sw_fsm_controller_enabled = true;
        validate_setpoints(sp);

        SensorInputs in{};
        in.temp_f = temp_f;
        in.vpd_kpa = row_vpd;
        in.rh_pct = 35.0f;
        in.dew_point_f = temp_f - 30.0f;
        in.outdoor_rh_pct = 20.0f;
        in.enthalpy_delta = -5.0f;
        in.solar_w_m2 = 500.0f;
        in.vpd_south = row_vpd;
        in.vpd_west = row_vpd;
        in.vpd_east = row_vpd;
        in.local_hour = 12;
        in.occupied = false;
        in.outdoor_temp_f = NAN;
        in.outdoor_dewpoint_f = NAN;
        in.outdoor_data_age_s = 99999u;

        const Mode mode = determine_mode(in, sp, control, 60000u);
        const RelayOutputs out = resolve_equipment(mode, in, sp, control, true);

        invariants::TraceRow r{};
        r.ts_unix_s = 1700000000ull + (uint64_t)minute * 60ull;
        r.local_hour = 12;
        r.temp_f = in.temp_f;
        r.rh_pct = in.rh_pct;
        r.vpd_kpa = in.vpd_kpa;
        r.dew_point_f = in.dew_point_f;
        r.outdoor_temp_f = NAN;
        r.outdoor_rh_pct = NAN;
        r.outdoor_dewpoint_f = NAN;
        r.outdoor_data_age_s = -1;
        r.solar_w_m2 = in.solar_w_m2;
        r.temp_low = sp.temp_low;
        r.temp_high = sp.temp_high;
        r.vpd_low = sp.vpd_low;
        r.vpd_high = sp.vpd_high;
        r.temp_hysteresis = sp.temp_hysteresis;
        r.vpd_hysteresis = sp.vpd_hysteresis;
        r.vpd_max_safe = sp.vpd_max_safe;
        r.vpd_min_safe = sp.vpd_min_safe;
        r.safety_max = sp.safety_max;
        r.safety_min = sp.safety_min;
        r.bias_heat = sp.bias_heat;
        r.bias_cool = sp.bias_cool;
        r.fog_escalation_kpa = sp.fog_escalation_kpa;
        r.fog_rh_ceiling = sp.fog_rh_ceiling;
        r.fog_min_temp = sp.fog_min_temp;
        r.sealed_max_ms = sp.sealed_max_ms;
        r.relief_duration_ms = sp.relief_duration_ms;
        r.outdoor_staleness_max_s = sp.outdoor_staleness_max_s;
        r.greenhouse_state = (mode == SEALED_MIST)
            ? std::string("SEALED_MIST_") + MIST_NAMES[(int)control.mist_stage]
            : MODE_NAMES[(int)mode];
        r.mode_reason = control.last_mode_reason ? control.last_mode_reason : "";
        r.summer_vent_active = control.override_summer_vent;
        r.vent_mist_assist_active = control.vent_mist_assist_active;
        r.eq_fog = out.fog ? 1 : 0;
        r.eq_vent = out.vent ? 1 : 0;
        r.eq_fan1 = out.fan1 ? 1 : 0;
        r.eq_fan2 = out.fan2 ? 1 : 0;
        r.eq_heat1 = out.heat1 ? 1 : 0;
        r.eq_heat2 = out.heat2 ? 1 : 0;
        const bool any_mister = (mode == SEALED_MIST) || control.vent_mist_assist_active;
        r.eq_mister_south = any_mister ? 1 : 0;
        r.eq_mister_west = (mode == SEALED_MIST && control.mist_stage >= MIST_S2) ? 1 : 0;
        r.eq_mister_center = r.eq_mister_west;
        r.eq_fertilizer_master = 0;
        r.feed_hold_active = false;
        r.night_start_hour = 0;
        r.night_end_hour = 0;
        r.dusk_cutoff_hour = 18;
        r.dusk_cutoff_enabled = true;

        const int before = runner.failures;
        runner.run(r, report);
        violations += runner.failures - before;
    }
    return violations;
}

TEST(a_safe_full_48_injection_holds_every_invariant) {
    PolicyState state;
    std::string err;
    ASSERT_TRUE(policy_injection::build_policy_state(distinct_full_state(), state, err));
    // 780 s seal window, 900 s dwell gate: the seal-timeout exit is reachable.
    ASSERT_EQ(replay_minutes(state, 240, 76.0f, 2.0f), 0);
    PASS();
}

TEST(a_deliberately_unsafe_full_48_injection_is_flagged_by_the_invariants) {
    // The seal window is pinned at its grid MINIMUM (120 s) while the mode
    // dwell gate is pinned at its grid MAXIMUM (1800 s) and enabled. Both
    // values are individually legal and on-grid — the pair is not. One minute
    // of high VPD opens a seal; once VPD settles back inside the band nothing
    // preempts the dwell gate any more, so `dwell_hold` pins SEALED_MIST far
    // past sealed_max_ms. That is invariant #04, and it is the same breach the
    // tracked corpus produces under --corpus-policy-columns.
    auto unsafe = distinct_full_state();
    set_field(unsafe, "mist_max_closed_vent_s", 120.0);
    set_field(unsafe, "mist_thermal_relief_s", 30.0);
    set_field(unsafe, "sw_dwell_gate_enabled", 1.0);
    set_field(unsafe, "dwell_gate_ms", 1800000.0);
    set_field(unsafe, "vpd_watch_dwell_s", 15.0);
    PolicyState state;
    std::string err;
    ASSERT_TRUE(policy_injection::build_policy_state(unsafe, state, err));
    ASSERT_TRUE(replay_minutes(state, 60, 76.0f, 2.0f, 1, 1.0f) > 0);

    // Control: the SAME disturbance with the dwell gate off is clean, so the
    // breach above is the injected state's doing and not the fixture's.
    auto safe = unsafe;
    set_field(safe, "sw_dwell_gate_enabled", 0.0);
    PolicyState safe_state;
    ASSERT_TRUE(policy_injection::build_policy_state(safe, safe_state, err));
    ASSERT_EQ(replay_minutes(safe_state, 60, 76.0f, 2.0f, 1, 1.0f), 0);
    PASS();
}

// ── 5. the coverage declaration is machine-readable ─────────────────────────

static std::string capture_coverage_line(const Coverage& coverage, long rows,
                                         int violations = 0, size_t distinct = 0) {
    std::FILE* tmp = std::tmpfile();
    if (tmp == nullptr) return "";
    policy_injection::print_coverage_line(tmp, coverage, rows, violations, distinct);
    std::rewind(tmp);
    std::string out;
    char buffer[4096];
    size_t got = 0;
    while ((got = std::fread(buffer, 1, sizeof(buffer), tmp)) > 0) out.append(buffer, got);
    std::fclose(tmp);
    return out;
}

TEST(coverage_line_declares_exactly_what_the_run_imposed) {
    PolicyState state;
    std::string err;
    ASSERT_TRUE(policy_injection::build_policy_state(distinct_full_state(), state, err));

    Coverage coverage;
    EconLatch econ;
    for (int row = 0; row < 17; ++row) {
        Setpoints sp = band_setpoints();
        coverage.begin_row(row);
        policy_injection::apply_policy_state(state, sp, econ, -5.0f, coverage);
    }
    coverage.policy_state_loaded = true;
    coverage.policy_state_path = "state.txt";

    const std::string line = capture_coverage_line(coverage, 17);
    ASSERT_TRUE(line.rfind(policy_injection::kCoverageMarker, 0) == 0);
    ASSERT_EQ(std::count(line.begin(), line.end(), '\n'), 1);
    ASSERT_TRUE(line.find("\"schema\":\"verdify-replay-invariants-coverage-v1\"") != std::string::npos);
    ASSERT_TRUE(line.find("\"field_count\":48") != std::string::npos);
    ASSERT_TRUE(line.find("\"injectable_count\":27") != std::string::npos);
    ASSERT_TRUE(line.find("\"imposed_count\":27") != std::string::npos);
    // 48-of-48 is unreachable from the compiled logic — 21 components have no
    // consumer in it — so `full_coverage` stays false even at the ceiling and
    // `full_injectable_coverage` is the signal that means "held everything
    // this harness CAN hold".
    ASSERT_TRUE(line.find("\"full_coverage\":false") != std::string::npos);
    ASSERT_TRUE(line.find("\"full_injectable_coverage\":true") != std::string::npos);
    ASSERT_TRUE(line.find("\"rows\":17") != std::string::npos);
    ASSERT_TRUE(line.find("\"status\":\"ok\"") != std::string::npos);
    ASSERT_TRUE(line.find("\"policy_state\":\"state.txt\"") != std::string::npos);
    // Imposed entries name their source and the firmware symbol they wrote.
    ASSERT_TRUE(line.find("\"temp_hysteresis\":{\"source\":\"policy_state\"") != std::string::npos);
    ASSERT_TRUE(line.find("\"firmware_symbol\":\"Setpoints::temp_hysteresis\"") != std::string::npos);
    // …and every uncoverable component carries its source-cited reason.
    ASSERT_TRUE(line.find("\"min_fan_on_s\":\"controls.yaml:79") != std::string::npos);
    ASSERT_TRUE(line.find("\"night_vpd_bias_kpa\":\"controls.yaml:483-496") != std::string::npos);
    PASS();
}

TEST(coverage_counts_the_final_effective_assignment_once_per_row) {
    Coverage coverage;
    const int index = policy_injection::index_of("temp_hysteresis");
    ASSERT_TRUE(index >= 0);

    coverage.begin_row(0);
    coverage.mark("temp_hysteresis", "corpus:sp_temp_hysteresis");
    coverage.mark("temp_hysteresis", "policy_state");
    ASSERT_EQ(coverage.applied_rows[index], 1L);
    ASSERT_EQ(coverage.source[index], std::string("policy_state"));
    ASSERT_EQ(coverage.imposed_count(1), (size_t)1);

    // One effective row out of two is partial evidence and is never credited.
    ASSERT_EQ(coverage.imposed_count(2), (size_t)0);
    const std::string line = capture_coverage_line(coverage, 2);
    ASSERT_TRUE(line.find("\"imposed_count\":0") != std::string::npos);
    ASSERT_TRUE(line.find("effective assignment held 1/2 rows; partial-row coverage is unqualified")
                != std::string::npos);
    PASS();
}

TEST(coverage_line_reports_invariant_counts_independently_of_the_exit_code) {
    // Safety failures now use stable exit 1 so they cannot collide with the
    // legacy corpus-only exit 2. Exact counts remain evidence, not process-code
    // arithmetic, and must be carried in the machine record.
    Coverage coverage;
    const std::string breach = capture_coverage_line(coverage, 296698, 2740, 2);
    ASSERT_TRUE(breach.find("\"invariant_violations\":2740") != std::string::npos);
    ASSERT_TRUE(breach.find("\"distinct_invariants\":2") != std::string::npos);
    const std::string clean = capture_coverage_line(coverage, 296698, 0, 0);
    ASSERT_TRUE(clean.find("\"invariant_violations\":0") != std::string::npos);
    ASSERT_TRUE(clean.find("\"distinct_invariants\":0") != std::string::npos);
    PASS();
}

TEST(coverage_line_reports_a_rejected_invocation_without_claiming_coverage) {
    Coverage coverage;
    coverage.status = "rejected";
    coverage.reject_reason = "incomplete_state: missing=night_vpd_bias_kpa";
    coverage.policy_state_path = "bad.txt";
    const std::string line = capture_coverage_line(coverage, 0);
    ASSERT_TRUE(line.find("\"status\":\"rejected\"") != std::string::npos);
    ASSERT_TRUE(line.find("\"imposed_count\":0") != std::string::npos);
    ASSERT_TRUE(line.find("\"imposed\":{}") != std::string::npos);
    ASSERT_TRUE(line.find("incomplete_state") != std::string::npos);
    PASS();
}

int main() {
    printf("═══════════════════════════════════════════════════════\n");
    printf("  Replay-harness policy-vector injection surface\n");
    printf("  (test-only; imposes the 48-field executor state)\n");
    printf("═══════════════════════════════════════════════════════\n\n");

    for (auto& t : test_registry) {
        printf("  %-62s ", t.name);
        const int before = tests_failed;
        t.fn();
        if (tests_failed == before) printf("✓\n");
    }

    printf("\n═══════════════════════════════════════════════════════\n");
    printf("  %d passed, %d failed\n", tests_passed, tests_failed);
    printf("═══════════════════════════════════════════════════════\n");
    return tests_failed > 0 ? 1 : 0;
}
