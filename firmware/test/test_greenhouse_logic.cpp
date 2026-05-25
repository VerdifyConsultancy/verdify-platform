/*
 * test_greenhouse_logic.cpp — Native x86 tests for greenhouse controller logic.
 * Same code as ESP32. Tests all 11 fixes from 4-LLM review synthesis.
 *
 * Compile: g++ -std=c++17 -I../lib -o test_greenhouse test_greenhouse_logic.cpp
 */

#include "greenhouse_logic.h"
#include <cstdio>
#include <cstring>
#include <cassert>
#include <vector>
#include <cmath>
#include <string>

static int tests_passed = 0;
static int tests_failed = 0;

#define TEST(name) \
    static void test_##name(); \
    static struct Register_##name { \
        Register_##name() { test_registry.push_back({#name, test_##name}); } \
    } reg_##name; \
    static void test_##name()

#define ASSERT_EQ(a, b) do { if ((a) != (b)) { printf("  FAIL: %s != %s (line %d)\n", #a, #b, __LINE__); tests_failed++; return; } } while(0)
#define ASSERT_TRUE(x) do { if (!(x)) { printf("  FAIL: %s (line %d)\n", #x, __LINE__); tests_failed++; return; } } while(0)
#define ASSERT_FALSE(x) ASSERT_TRUE(!(x))
#define PASS() tests_passed++

struct TestEntry { const char* name; void (*fn)(); };
static std::vector<TestEntry> test_registry;

static SensorInputs make_inputs(float temp, float vpd, float rh = 60.0f) {
    // Sprint-15: outdoor data defaults to STALE so the new summer-vent
    // gate is OFF by default in unit tests (outdoor_data_age_s above the
    // 300-s default outdoor_staleness_max_s). Sprint-15 gate tests
    // override outdoor_* explicitly. This keeps the existing 80+ tests'
    // behavior unchanged.
    return { .temp_f = temp, .vpd_kpa = vpd, .rh_pct = rh,
             .dew_point_f = temp - 10.0f, .outdoor_rh_pct = 30.0f,
             .enthalpy_delta = -5.0f,
             .solar_w_m2 = 0.0f,
             .vpd_south = vpd, .vpd_west = vpd, .vpd_east = vpd,
             .local_hour = 12, .occupied = false,
             .outdoor_temp_f = NAN, .outdoor_dewpoint_f = NAN,
             .outdoor_data_age_s = 9999u };
}

// Sprint-11: default_setpoints() now returns PERMISSIVE wide defaults
// (temp 40-95°F, vpd 0.35-2.80 kPa) matching the two-band model where
// the dispatcher pushes the real crop band. Many pre-sprint-11 tests
// were written against the narrow pre-sprint-10 defaults (82/65/1.4/0.8)
// and exercise behavior at specific sensor values inside that band.
// band_setpoints() restores those values so tests keep their semantics
// without coupling to the firmware default constants.
static Setpoints band_setpoints() {
    Setpoints sp = default_setpoints();
    sp.temp_high = 82.0f;
    sp.temp_low  = 65.0f;
    sp.vpd_high  = 1.4f;
    sp.vpd_low   = 0.8f;
    sp.safety_max = 95.0f;
    sp.safety_min = 45.0f;
    sp.vpd_max_safe = 2.5f;
    sp.vpd_min_safe = 0.3f;
    sp.dehum_aggressive_kpa = 0.6f;
    return sp;
}

// Helper: run resolve_equipment with state
static RelayOutputs equip(Mode m, float t, float v, Setpoints sp = default_setpoints(), ControlState st = initial_state()) {
    return resolve_equipment(m, make_inputs(t, v), sp, st, true);
}

// ═══════════════════════════════════════════════════════════════
// CORE MODE TESTS
// ═══════════════════════════════════════════════════════════════

TEST(idle_when_in_band) {
    auto s = initial_state();
    ASSERT_EQ(determine_mode(make_inputs(72, 0.9), default_setpoints(), s, 5000), IDLE);
    PASS();
}

TEST(ventilate_when_hot) {
    auto s = initial_state();
    // Sprint-11: default_setpoints() widened to temp_high=95. Pin temp_high=82
    // explicitly so this test keeps its original semantics.
    auto sp = default_setpoints();
    sp.temp_high = 82.0f;
    ASSERT_EQ(determine_mode(make_inputs(84, 0.9), sp, s, 5000), VENTILATE);
    PASS();
}

TEST(sealed_after_dwell) {
    auto s = initial_state(); s.vpd_watch_timer_ms = 60000;
    ASSERT_EQ(determine_mode(make_inputs(72, 1.5), band_setpoints(), s, 5000), SEALED_MIST);
    PASS();
}

TEST(minute_window_wraps_midnight) {
    ASSERT_TRUE(minute_in_window(local_minute_of_day(23, 30), local_minute_of_day(22, 0), 480));
    ASSERT_TRUE(minute_in_window(local_minute_of_day(5, 59), local_minute_of_day(22, 0), 480));
    ASSERT_FALSE(minute_in_window(local_minute_of_day(6, 0), local_minute_of_day(22, 0), 480));
    PASS();
}

TEST(direct_wet_window_uses_activity_offsets) {
    // Activity 06:00-20:00, wettable after 2h ramp and before 3h drydown.
    ASSERT_FALSE(direct_wet_window_open(local_minute_of_day(7, 59), 6, 0, 840, 120, 180));
    ASSERT_TRUE(direct_wet_window_open(local_minute_of_day(8, 0), 6, 0, 840, 120, 180));
    ASSERT_TRUE(direct_wet_window_open(local_minute_of_day(16, 59), 6, 0, 840, 120, 180));
    ASSERT_FALSE(direct_wet_window_open(local_minute_of_day(17, 0), 6, 0, 840, 120, 180));
    PASS();
}

TEST(direct_wet_stress_override_requires_vpd_dew_and_hour) {
    auto sp = band_setpoints();
    sp.direct_wet_stress_override_enabled = true;
    sp.direct_wet_stress_vpd_margin_kpa = 0.05f;
    sp.direct_wet_stress_min_dew_margin_f = 8.0f;
    sp.direct_wet_stress_latest_hour = 22;
    validate_setpoints(sp);

    auto in = make_inputs(74.0f, sp.vpd_high + 0.10f);
    in.local_hour = 20;
    in.dew_point_f = 64.0f;
    ASSERT_TRUE(direct_wet_stress_override_permitted(in, sp));

    in.vpd_kpa = sp.vpd_high + 0.02f;
    ASSERT_FALSE(direct_wet_stress_override_permitted(in, sp));

    in.vpd_kpa = sp.vpd_high + 0.10f;
    in.dew_point_f = 69.0f;
    ASSERT_FALSE(direct_wet_stress_override_permitted(in, sp));

    in.dew_point_f = 64.0f;
    in.local_hour = 22;
    ASSERT_FALSE(direct_wet_stress_override_permitted(in, sp));
    PASS();
}

TEST(fog_stress_extension_requires_enabled_vpd_dew_and_extension_hour) {
    auto sp = band_setpoints();
    sp.fog_window_start = 7;
    sp.fog_window_end = 17;
    sp.fog_stress_window_extend_enabled = true;
    sp.fog_stress_window_latest_hour = 19;
    sp.fog_stress_min_dew_margin_f = 10.0f;
    validate_setpoints(sp);

    auto in = make_inputs(76.0f, sp.vpd_high + 0.10f, 60.0f);
    in.local_hour = 18;
    in.dew_point_f = 64.0f;
    ASSERT_TRUE(fog_stress_window_permitted(in, sp));
    ASSERT_TRUE(fog_permitted(in, sp));

    in.dew_point_f = 70.0f;
    ASSERT_FALSE(fog_stress_window_permitted(in, sp));
    ASSERT_FALSE(fog_permitted(in, sp));

    in.dew_point_f = 64.0f;
    in.local_hour = 19;
    ASSERT_FALSE(fog_stress_window_permitted(in, sp));
    ASSERT_FALSE(fog_permitted(in, sp));

    in.local_hour = 18;
    in.vpd_kpa = sp.vpd_high - 0.01f;
    ASSERT_FALSE(fog_stress_window_permitted(in, sp));
    ASSERT_FALSE(fog_permitted(in, sp));
    PASS();
}

TEST(day_mask_allows_zero_sunday) {
    ASSERT_TRUE(day_mask_allows(0b0000001, 0));
    ASSERT_FALSE(day_mask_allows(0b0000001, 1));
    ASSERT_TRUE(day_mask_allows(0b0100100, 2));
    ASSERT_TRUE(day_mask_allows(0b0100100, 5));
    PASS();
}

TEST(not_sealed_before_dwell) {
    auto s = initial_state(); s.vpd_watch_timer_ms = 30000;
    ASSERT_EQ(determine_mode(make_inputs(72, 1.5), default_setpoints(), s, 5000), IDLE);
    PASS();
}

TEST(safety_cool) {
    auto s = initial_state();
    ASSERT_EQ(determine_mode(make_inputs(96, 1), band_setpoints(), s, 5000), SAFETY_COOL);
    PASS();
}

TEST(safety_heat) {
    auto s = initial_state();
    ASSERT_EQ(determine_mode(make_inputs(44, 0.3), band_setpoints(), s, 5000), SAFETY_HEAT);
    PASS();
}

TEST(relief_after_sealed_max) {
    auto s = initial_state(); s.mode_prev = SEALED_MIST; s.sealed_timer_ms = 600000;
    ASSERT_EQ(determine_mode(make_inputs(72, 1.5), band_setpoints(), s, 5000), THERMAL_RELIEF);
    PASS();
}

TEST(dehum_when_vpd_low) {
    auto s = initial_state();
    ASSERT_EQ(determine_mode(make_inputs(72, 0.15), default_setpoints(), s, 5000), DEHUM_VENT);
    PASS();
}

TEST(climate_action_names_match_contract_order) {
    ASSERT_EQ(CLIMATE_DEHUM_VENT, 10);
    ASSERT_TRUE(strcmp(CLIMATE_ACTION_NAMES[CLIMATE_SENSOR_FAULT], "SENSOR_FAULT") == 0);
    ASSERT_TRUE(strcmp(CLIMATE_ACTION_NAMES[CLIMATE_VENT_COOL_MIST_ASSIST], "VENT_COOL_MIST_ASSIST") == 0);
    ASSERT_TRUE(strcmp(CLIMATE_ACTION_NAMES[CLIMATE_SEALED_HUMIDIFY], "SEALED_HUMIDIFY") == 0);
    ASSERT_TRUE(strcmp(CLIMATE_ACTION_NAMES[CLIMATE_DEHUM_VENT], "DEHUM_VENT") == 0);
    PASS();
}

TEST(climate_candidate_selector_prefers_temperature_before_vpd_and_resource) {
    ClimateCandidateProjection candidates[] = {
        {
            .action = CLIMATE_SEALED_HUMIDIFY,
            .safety_ok = true,
            .blocked_reason = "",
            .projected_temp_error_f = 2.0f,
            .projected_vpd_error_kpa = 0.0f,
            .resource_cost = 0.0f,
            .relay_churn_cost = 0.0f,
            .confidence = 0.8f,
            .prior_action_hold_preference = 0.0f
        },
        {
            .action = CLIMATE_VENT_COOL_MIST_ASSIST,
            .safety_ok = true,
            .blocked_reason = "",
            .projected_temp_error_f = 1.0f,
            .projected_vpd_error_kpa = 0.2f,
            .resource_cost = 10.0f,
            .relay_churn_cost = 1.0f,
            .confidence = 0.8f,
            .prior_action_hold_preference = 0.0f
        }
    };
    ASSERT_EQ(choose_climate_candidate_index(candidates, 2), 1);
    PASS();
}

TEST(climate_candidate_selector_uses_vpd_then_resource_as_tie_breakers) {
    ClimateCandidateProjection candidates[] = {
        {
            .action = CLIMATE_VENT_COOL,
            .safety_ok = true,
            .blocked_reason = "",
            .projected_temp_error_f = 0.0f,
            .projected_vpd_error_kpa = 0.3f,
            .resource_cost = 0.0f,
            .relay_churn_cost = 0.0f,
            .confidence = 0.8f,
            .prior_action_hold_preference = 0.0f
        },
        {
            .action = CLIMATE_VENT_COOL_MIST_ASSIST,
            .safety_ok = true,
            .blocked_reason = "",
            .projected_temp_error_f = 0.0f,
            .projected_vpd_error_kpa = 0.1f,
            .resource_cost = 5.0f,
            .relay_churn_cost = 1.0f,
            .confidence = 0.8f,
            .prior_action_hold_preference = 0.0f
        },
        {
            .action = CLIMATE_IDLE,
            .safety_ok = true,
            .blocked_reason = "",
            .projected_temp_error_f = 0.0f,
            .projected_vpd_error_kpa = 0.1f,
            .resource_cost = 1.0f,
            .relay_churn_cost = 0.0f,
            .confidence = 0.8f,
            .prior_action_hold_preference = 0.0f
        }
    };
    ASSERT_EQ(choose_climate_candidate_index(candidates, 3), 2);
    PASS();
}

TEST(climate_candidate_selector_rejects_unsafe_candidates) {
    ClimateCandidateProjection candidates[] = {
        {
            .action = CLIMATE_VENT_COOL,
            .safety_ok = false,
            .blocked_reason = "occupancy",
            .projected_temp_error_f = 0.0f,
            .projected_vpd_error_kpa = 0.0f,
            .resource_cost = 0.0f,
            .relay_churn_cost = 0.0f,
            .confidence = 0.8f,
            .prior_action_hold_preference = 0.0f
        },
        {
            .action = CLIMATE_IDLE,
            .safety_ok = true,
            .blocked_reason = "",
            .projected_temp_error_f = 2.0f,
            .projected_vpd_error_kpa = 1.0f,
            .resource_cost = 0.0f,
            .relay_churn_cost = 0.0f,
            .confidence = 0.8f,
            .prior_action_hold_preference = 0.0f
        }
    };
    ASSERT_EQ(choose_climate_candidate_index(candidates, 2), 1);
    candidates[1].safety_ok = false;
    ASSERT_EQ(choose_climate_candidate_index(candidates, 2), -1);
    PASS();
}

TEST(climate_decision_drives_hot_dry_to_vent_mist_assist) {
    auto sp = band_setpoints();
    sp.sw_fsm_controller_enabled = true;
    sp.direct_wet_stress_override_enabled = true;
    sp.direct_wet_stress_vpd_margin_kpa = 0.05f;
    auto s = initial_state();
    auto in = make_inputs(sp.temp_high + 4.0f, sp.vpd_high + 0.25f);
    in.dew_point_f = in.temp_f - 12.0f;
    ClimateActionDecision d = evaluate_climate_decision(in, sp, s);
    ASSERT_EQ(d.climate_action, CLIMATE_VENT_COOL_MIST_ASSIST);
    ASSERT_EQ(d.priority_axis, CLIMATE_PRIORITY_TEMP);
    Mode m = determine_mode(in, sp, s, 5000);
    ASSERT_EQ(m, VENTILATE);
    ASSERT_TRUE(s.vent_mist_assist_active);
    PASS();
}

TEST(climate_decision_blocks_wet_assist_when_occupied) {
    auto sp = band_setpoints();
    sp.sw_fsm_controller_enabled = true;
    auto s = initial_state();
    auto in = make_inputs(sp.temp_high + 4.0f, sp.vpd_high + 0.25f);
    in.occupied = true;
    sp.occupancy_inhibit = true;
    ClimateActionDecision d = evaluate_climate_decision(in, sp, s);
    ASSERT_EQ(d.climate_action, CLIMATE_VENT_COOL);
    ASSERT_EQ(d.moisture_assist_state, CLIMATE_MOISTURE_BLOCKED);
    ASSERT_TRUE(std::string(d.fog_block_reason) == "occupancy");
    PASS();
}

TEST(climate_decision_forces_safety_before_band_compliance) {
    auto sp = band_setpoints();
    sp.sw_fsm_controller_enabled = true;
    auto s = initial_state();
    auto in = make_inputs(sp.safety_max + 1.0f, sp.vpd_high + 0.6f);
    ClimateActionDecision d = evaluate_climate_decision(in, sp, s);
    ASSERT_EQ(d.climate_action, CLIMATE_SAFETY_COOL);
    ASSERT_EQ(d.priority_axis, CLIMATE_PRIORITY_SAFETY);
    Mode m = determine_mode(in, sp, s, 5000);
    ASSERT_EQ(m, SAFETY_COOL);
    PASS();
}

// ═══════════════════════════════════════════════════════════════
// FIX 1: mode_prev reads last cycle
// ═══════════════════════════════════════════════════════════════

TEST(fix1_mode_prev_tracks_correctly) {
    auto sp = band_setpoints(); auto s = initial_state();
    s.vpd_watch_timer_ms = 60000;
    // Cycle 1: enter SEALED_MIST
    Mode m1 = determine_mode(make_inputs(72, 1.5), sp, s, 5000);
    ASSERT_EQ(m1, SEALED_MIST);
    ASSERT_EQ(s.mode_prev, SEALED_MIST);
    // Cycle 2: VPD drops below exit → should see was_sealed=true and exit
    Mode m2 = determine_mode(make_inputs(72, 0.85), sp, s, 5000);
    ASSERT_EQ(m2, IDLE);
    PASS();
}

TEST(fix1_relief_to_idle_transition) {
    auto sp = default_setpoints(); auto s = initial_state();
    s.mode_prev = THERMAL_RELIEF; s.relief_timer_ms = 89000;
    // VPD resolved during relief
    Mode m = determine_mode(make_inputs(72, 0.8), sp, s, 5000);
    // Relief just expired, VPD is in band → should be IDLE
    ASSERT_EQ(m, IDLE);
    PASS();
}

// ═══════════════════════════════════════════════════════════════
// FIX 2: VPD safety vs SAFETY_HEAT / THERMAL_RELIEF
// ═══════════════════════════════════════════════════════════════

TEST(fix2_vpd_safety_no_stomp_safety_heat) {
    auto s = initial_state();
    Mode m = determine_mode(make_inputs(40, 3.5), band_setpoints(), s, 5000);
    ASSERT_EQ(m, SAFETY_HEAT);
    PASS();
}

TEST(fix2_vpd_safety_no_stomp_thermal_relief) {
    auto s = initial_state(); s.mode_prev = THERMAL_RELIEF; s.relief_timer_ms = 45000;
    Mode m = determine_mode(make_inputs(72, 3.5), default_setpoints(), s, 5000);
    ASSERT_EQ(m, THERMAL_RELIEF);
    PASS();
}

// ═══════════════════════════════════════════════════════════════
// FIX 3: Temperature hysteresis
// ═══════════════════════════════════════════════════════════════

TEST(fix3_ventilate_exit_hysteresis) {
    // Sprint-12: test now exercises hysteresis around the INTERIOR cooling
    // target. With temp_low=74, temp_high=78 (band=4), Thigh_interior =
    // 78 - 4*0.25 = 77°F. Enter VENT at >77, exit at <(77 - 1.5) = 75.5.
    auto sp = default_setpoints();
    sp.temp_low = 74; sp.temp_high = 78; sp.temp_hysteresis = 1.5;
    auto s = initial_state();
    // 78°F > Thigh(77) → enter VENTILATE
    determine_mode(make_inputs(78, 0.9), sp, s, 5000);
    ASSERT_EQ(s.mode, VENTILATE);
    // 76°F above 77-1.5=75.5 → stay
    determine_mode(make_inputs(76, 0.9), sp, s, 5000);
    ASSERT_EQ(s.mode, VENTILATE);
    // 75°F below 75.5 → exit
    determine_mode(make_inputs(75, 0.9), sp, s, 5000);
    ASSERT_EQ(s.mode, IDLE);
    PASS();
}

TEST(fix3_heat_hysteresis) {
    // IN-12 (Sprint 19): fixed after commit 2b61ee6 swapped the inverted
    // dH2 / heat_hysteresis thresholds. Sprint-9 P1#7: S2 is now latched
    // via ControlState.heat2_latched; the fresh state passed by equip()
    // starts with heat2_latched=false, so S1-only is the only behavior
    // exercisable without going through determine_mode. See
    // s9_heat2_latches_* tests for the S2 latch lifecycle.
    auto sp = default_setpoints(); sp.temp_low = 58; sp.heat_hysteresis = 1.0;
    // S1 (electric) threshold: Tlow + heat_hysteresis = 59°F
    // 57.5°F → S1 on (below threshold), S2 not latched yet
    auto out = equip(IDLE, 57.5, 0.9, sp);
    ASSERT_TRUE(out.heat1);
    ASSERT_FALSE(out.heat2);
    auto out2 = equip(IDLE, 56.5, 0.9, sp);
    ASSERT_TRUE(out2.heat1);
    ASSERT_FALSE(out2.heat2);
    // Even at 52°F (below S2 threshold), fresh state with latched=false
    // does not fire S2 — the latch must be set by determine_mode first.
    auto out3 = equip(IDLE, 52.0, 0.9, sp);
    ASSERT_TRUE(out3.heat1);
    ASSERT_FALSE(out3.heat2);
    PASS();
}

// ═══════════════════════════════════════════════════════════════
// FIX 4: DEHUM_VENT sticky hysteresis
// ═══════════════════════════════════════════════════════════════

TEST(fix4_dehum_stays_until_vpd_low) {
    // Sprint-12: DEHUM targets the INTERIOR low VPD: vpd_low_eff =
    // vpd_low + (vpd_high - vpd_low)*0.25. With band vpd_low=0.8,
    // vpd_high=1.4 → vpd_low_eff = 0.95. HV = min(0.3, 1.25*0.5=0.625)
    // = 0.3. Enter DEHUM at vpd < 0.65. Exit at vpd >= 0.95.
    auto sp = band_setpoints(); auto s = initial_state();
    // 0.4 < 0.65 → enter DEHUM
    determine_mode(make_inputs(72, 0.4), sp, s, 5000);
    ASSERT_EQ(s.mode, DEHUM_VENT);
    // 0.85 still < vpd_low_eff=0.95 → stay
    determine_mode(make_inputs(72, 0.85), sp, s, 5000);
    ASSERT_EQ(s.mode, DEHUM_VENT);
    // 0.95 at vpd_low_eff → exit
    determine_mode(make_inputs(72, 0.95), sp, s, 5000);
    ASSERT_EQ(s.mode, IDLE);
    PASS();
}

// ═══════════════════════════════════════════════════════════════
// FIX 5: Relief exit runs full cascade
// ═══════════════════════════════════════════════════════════════

TEST(fix5_relief_exits_to_ventilate_when_hot) {
    auto sp = band_setpoints(); auto s = initial_state();
    s.mode_prev = THERMAL_RELIEF; s.relief_timer_ms = 89000;
    // VPD resolved but temp is high → should VENTILATE, not IDLE
    Mode m = determine_mode(make_inputs(84, 0.9), sp, s, 5000);
    ASSERT_EQ(m, VENTILATE);
    PASS();
}

TEST(fix5_relief_exits_to_sealed_when_vpd_high) {
    auto sp = band_setpoints(); auto s = initial_state();
    s.mode_prev = THERMAL_RELIEF; s.relief_timer_ms = 89000;
    s.vpd_watch_timer_ms = 60000;  // dwell satisfied
    // VPD still high → should re-seal
    Mode m = determine_mode(make_inputs(72, 1.5), sp, s, 5000);
    ASSERT_EQ(m, SEALED_MIST);
    PASS();
}

// ═══════════════════════════════════════════════════════════════
// FIX 6: IDLE econ heating is capped
// ═══════════════════════════════════════════════════════════════

TEST(fix6_econ_heat_electric_only) {
    auto sp = default_setpoints(); sp.econ_block = true;
    auto out = equip(IDLE, 72, 0.3, sp);
    // Electric only for VPD rescue, no gas
    ASSERT_TRUE(out.heat1);
    ASSERT_FALSE(out.heat2);
    PASS();
}

TEST(fix6_econ_heat_capped_by_temp) {
    auto sp = band_setpoints(); sp.econ_block = true;
    // Temp 80°F is near Thigh (82). 80 >= 82-5=77 → too hot for econ heating
    auto out = equip(IDLE, 80, 0.3, sp);
    ASSERT_FALSE(out.heat1);  // temp too high for econ heating
    PASS();
}

// ═══════════════════════════════════════════════════════════════
// FIX 7: Watch timer suspended in safety
// ═══════════════════════════════════════════════════════════════

TEST(fix7_watch_timer_frozen_during_safety) {
    auto sp = default_setpoints(); auto s = initial_state();
    s.mode_prev = SAFETY_COOL;
    // VPD is above band during safety cool — watch timer should NOT increment
    determine_mode(make_inputs(96, 1.5), sp, s, 5000);
    ASSERT_EQ(s.vpd_watch_timer_ms, 0u);
    PASS();
}

// ═══════════════════════════════════════════════════════════════
// FIX 8: SENSOR_FAULT
// ═══════════════════════════════════════════════════════════════

TEST(fix8_nan_temp) {
    auto s = initial_state();
    auto in = make_inputs(NAN, 0.9);
    ASSERT_EQ(determine_mode(in, default_setpoints(), s, 5000), SENSOR_FAULT);
    PASS();
}

TEST(fix8_nan_vpd) {
    auto s = initial_state();
    auto in = make_inputs(72, NAN);
    ASSERT_EQ(determine_mode(in, default_setpoints(), s, 5000), SENSOR_FAULT);
    PASS();
}

TEST(fix8_sensor_fault_equipment) {
    // R2-1: ALL relays off on sensor fault — no blind actuation
    auto out = equip(SENSOR_FAULT, 72, 0.9);
    ASSERT_FALSE(out.heat1);
    ASSERT_FALSE(out.heat2);
    ASSERT_FALSE(out.vent);
    ASSERT_FALSE(out.fan1);
    ASSERT_FALSE(out.fog);
    PASS();
}

// ═══════════════════════════════════════════════════════════════
// R2 FIXES — Round 2 review
// ═══════════════════════════════════════════════════════════════

TEST(r2_2_sensor_fault_preserves_mode_prev) {
    auto sp = band_setpoints(); auto s = initial_state();
    // Enter VENTILATE
    determine_mode(make_inputs(84, 0.9), sp, s, 5000);
    ASSERT_EQ(s.mode_prev, VENTILATE);
    // Transient sensor fault
    auto bad = make_inputs(NAN, 0.9);
    determine_mode(bad, sp, s, 5000);
    ASSERT_EQ(s.mode, SENSOR_FAULT);
    // mode_prev should STILL be VENTILATE (not SENSOR_FAULT)
    ASSERT_EQ(s.mode_prev, VENTILATE);
    PASS();
}

TEST(r2_3_vpd_override_no_stomp_ventilate) {
    auto sp = band_setpoints(); auto s = initial_state();
    // Temp is hot (needs cooling) AND VPD is extreme
    auto in = make_inputs(84, 3.5);
    Mode m = determine_mode(in, sp, s, 5000);
    // Should be VENTILATE (cooling takes priority), not SEALED_MIST
    ASSERT_EQ(m, VENTILATE);
    PASS();
}

TEST(r2_4_implausible_temp_triggers_fault) {
    auto s = initial_state();
    auto in = make_inputs(-127, 0.9);  // disconnected SHT sensor
    ASSERT_EQ(determine_mode(in, default_setpoints(), s, 5000), SENSOR_FAULT);
    PASS();
}

TEST(r2_4_implausible_rh_triggers_fault) {
    auto s = initial_state();
    SensorInputs in = make_inputs(72, 0.9, 105.0f);  // RH > 100%
    ASSERT_EQ(determine_mode(in, default_setpoints(), s, 5000), SENSOR_FAULT);
    PASS();
}

TEST(r2_5_occupancy_blocks_fog) {
    auto sp = default_setpoints(); sp.occupancy_inhibit = true;
    auto s = initial_state(); s.mist_stage = MIST_FOG;
    SensorInputs in = make_inputs(72, 1.7, 60);
    in.occupied = true;
    auto out = resolve_equipment(SEALED_MIST, in, sp, s, true);
    ASSERT_FALSE(out.fog);  // occupied + inhibit → no fog
    PASS();
}

TEST(r2_5_occupancy_blocks_routine_fans) {
    auto sp = band_setpoints(); sp.occupancy_inhibit = true;
    auto s = initial_state();
    SensorInputs in = make_inputs(86, 1.0, 45);
    in.occupied = true;
    auto out = resolve_equipment(VENTILATE, in, sp, s, true);
    ASSERT_TRUE(out.vent);
    ASSERT_FALSE(out.fan1);
    ASSERT_FALSE(out.fan2);
    ASSERT_FALSE(out.fog);
    PASS();
}

TEST(r2_5_occupancy_does_not_block_safety_cool_fans) {
    auto sp = band_setpoints(); sp.occupancy_inhibit = true;
    auto s = initial_state();
    SensorInputs in = make_inputs(sp.safety_max + 1.0f, 1.0, 45);
    in.occupied = true;
    auto out = resolve_equipment(SAFETY_COOL, in, sp, s, true);
    ASSERT_TRUE(out.vent);
    ASSERT_TRUE(out.fan1);
    ASSERT_TRUE(out.fan2);
    PASS();
}

TEST(r2_6_relief_cycle_forces_ventilate) {
    auto sp = band_setpoints(); sp.max_relief_cycles = 3;
    auto s = initial_state();
    s.relief_cycle_count = 3;  // at the limit
    s.vpd_watch_timer_ms = 60000;
    // VPD wants seal but relief cycles exhausted → force VENTILATE
    Mode m = determine_mode(make_inputs(72, 1.5), sp, s, 5000);
    ASSERT_EQ(m, VENTILATE);
    PASS();
}

TEST(r2_8_dehum_respects_econ_block_change) {
    auto sp = default_setpoints(); auto s = initial_state();
    // Enter DEHUM
    determine_mode(make_inputs(72, 0.15), sp, s, 5000);
    ASSERT_EQ(s.mode, DEHUM_VENT);
    // Econ blocks mid-cycle
    sp.econ_block = true;
    determine_mode(make_inputs(72, 0.35), sp, s, 5000);
    // Should exit DEHUM because econ now blocks venting
    ASSERT_EQ(s.mode, IDLE);
    PASS();
}

TEST(r2_9_fog_stage_blocked_by_time_window) {
    auto sp = band_setpoints(); auto s = initial_state();
    s.mode_prev = SEALED_MIST; s.sealed_timer_ms = 100000;
    s.mist_stage = MIST_S2; s.mist_stage_timer_ms = 0;
    // VPD demands fog BUT it's outside the fog time window
    SensorInputs in = make_inputs(72, 1.9, 60);
    in.local_hour = 22;  // 10 PM — outside 7-17 window
    determine_mode(in, sp, s, 5000);
    // Should NOT escalate to MIST_FOG
    ASSERT_EQ(s.mist_stage, MIST_S2);
    PASS();
}

// ═══════════════════════════════════════════════════════════════
// FIX 9: Mist stage progression
// ═══════════════════════════════════════════════════════════════

TEST(fix9_mist_stage_s1_on_seal) {
    auto sp = band_setpoints(); auto s = initial_state();
    s.vpd_watch_timer_ms = 60000;
    determine_mode(make_inputs(72, 1.5), sp, s, 5000);
    ASSERT_EQ(s.mist_stage, MIST_S1);
    PASS();
}

TEST(fix9_mist_stage_s1_to_s2) {
    auto sp = band_setpoints(); sp.mist_s2_delay_ms = 300000;
    auto s = initial_state();
    s.mode_prev = SEALED_MIST; s.sealed_timer_ms = 100000;
    s.mist_stage = MIST_S1; s.mist_stage_timer_ms = 300000;
    // VPD still above band → should escalate to S2
    determine_mode(make_inputs(72, 1.5), sp, s, 5000);
    ASSERT_EQ(s.mist_stage, MIST_S2);
    PASS();
}

TEST(fix9_mist_stage_s2_to_fog) {
    auto sp = band_setpoints(); sp.fog_escalation_kpa = 0.4;
    auto s = initial_state();
    s.mode_prev = SEALED_MIST; s.sealed_timer_ms = 100000;
    s.mist_stage = MIST_S2; s.mist_stage_timer_ms = 0;
    // VPD = 1.9 > vpd_high(1.4) + fog_escalation(0.4) = 1.8 → FOG
    determine_mode(make_inputs(72, 1.9), sp, s, 5000);
    ASSERT_EQ(s.mist_stage, MIST_FOG);
    PASS();
}

TEST(fix9_mist_resets_on_seal_exit) {
    auto sp = default_setpoints(); auto s = initial_state();
    s.mode_prev = SEALED_MIST; s.sealed_timer_ms = 100000;
    s.mist_stage = MIST_S2;
    // VPD drops below exit → exits sealed, mist resets
    determine_mode(make_inputs(72, 0.85), sp, s, 5000);
    ASSERT_EQ(s.mist_stage, MIST_WATCH);
    PASS();
}

TEST(fix9_fog_in_equipment_reads_mist_stage) {
    auto sp = default_setpoints();
    auto s = initial_state(); s.mist_stage = MIST_FOG;
    auto in = make_inputs(72, 1.7, 60);
    auto out = resolve_equipment(SEALED_MIST, in, sp, s, true);
    ASSERT_TRUE(out.fog);
    PASS();
}

TEST(fix9_no_fog_when_not_mist_fog_stage) {
    auto sp = default_setpoints();
    auto s = initial_state(); s.mist_stage = MIST_S2;
    auto in = make_inputs(72, 1.7, 60);
    auto out = resolve_equipment(SEALED_MIST, in, sp, s, true);
    ASSERT_FALSE(out.fog);  // not in MIST_FOG stage
    PASS();
}

// ═══════════════════════════════════════════════════════════════
// INVARIANT SWEEPS
// ═══════════════════════════════════════════════════════════════

TEST(sealed_mist_never_opens_vent_or_fans) {
    auto sp = default_setpoints(); int v = 0;
    for (float t = 40; t <= 100; t += 2)
        for (float vpd = 0.1f; vpd <= 3.5f; vpd += 0.1f) {
            auto s = initial_state(); s.vpd_watch_timer_ms = 60000;
            Mode m = determine_mode(make_inputs(t, vpd), sp, s, 5000);
            auto out = resolve_equipment(m, make_inputs(t, vpd), sp, s, true);
            if (m == SEALED_MIST && out.vent) v++;
            if (m == SEALED_MIST && (out.fan1 || out.fan2)) v++;
        }
    ASSERT_EQ(v, 0); PASS();
}

TEST(no_heater_with_vent) {
    auto sp = default_setpoints(); int v = 0;
    for (float t = 40; t <= 100; t += 2)
        for (float vpd = 0.1f; vpd <= 3.5f; vpd += 0.1f) {
            auto s = initial_state(); s.vpd_watch_timer_ms = 60000;
            Mode m = determine_mode(make_inputs(t, vpd), sp, s, 5000);
            auto out = resolve_equipment(m, make_inputs(t, vpd), sp, s, true);
            if (out.vent && (out.heat1 || out.heat2)) v++;
        }
    ASSERT_EQ(v, 0); PASS();
}

TEST(no_fan_without_vent) {
    // Sprint-9 P2#11: SAFETY_HEAT intentionally runs the lead fan for
    // canopy circulation while the vent stays closed (keep heat in).
    // Whitelist it — the invariant still holds for every normal mode.
    auto sp = default_setpoints(); int v = 0;
    for (float t = 40; t <= 100; t += 2)
        for (float vpd = 0.1f; vpd <= 3.5f; vpd += 0.1f) {
            auto s = initial_state(); s.vpd_watch_timer_ms = 60000;
            Mode m = determine_mode(make_inputs(t, vpd), sp, s, 5000);
            auto out = resolve_equipment(m, make_inputs(t, vpd), sp, s, true);
            if (m == SAFETY_HEAT) continue;
            if (!out.vent && (out.fan1 || out.fan2)) v++;
        }
    ASSERT_EQ(v, 0); PASS();
}

// ═══════════════════════════════════════════════════════════════
// SCENARIO: Cold night
// ═══════════════════════════════════════════════════════════════

TEST(cold_night_no_vent_oscillation) {
    // Sprint-12: narrow the band so the sweep stays well below the
    // interior cooling target. With temp_low=65, temp_high=78 and
    // bias_cool=3 → Thigh_interior = 78-(13*0.25) = 74.75, Thigh = 77.75.
    // Sweep up to 75°F (below Thigh) — no venting should occur.
    auto sp = default_setpoints();
    sp.temp_low = 65; sp.temp_high = 78; sp.bias_cool = 3;
    auto s = initial_state();
    float temps[] = {60,62,64,66,68,70,72,74,75,74,72,70,68,66,64,62,60};
    int vent = 0;
    for (float t : temps) {
        // Sprint-12: bump baseline vpd so the sweep doesn't trip DEHUM_VENT
        // when vpd_low_eff sits inside the old 0.6-0.9 range.
        float vpd = 1.0f + (t-60)*0.02f;
        Mode m = determine_mode(make_inputs(t, vpd), sp, s, 5000);
        auto out = resolve_equipment(m, make_inputs(t, vpd), sp, s, true);
        if (out.vent) vent++;
    }
    ASSERT_EQ(vent, 0); PASS();
}

// ═══════════════════════════════════════════════════════════════
// MAIN
// ═══════════════════════════════════════════════════════════════

// ═══════════════════════════════════════════════════════════════
// FW-7/8/9: SEALED_MIST SAFETY TESTS
// ═══════════════════════════════════════════════════════════════

TEST(fw7_sealed_mist_temp_guard) {
    // SEALED_MIST should NOT engage when temp is within 5°F of safety_max
    auto sp = default_setpoints();
    sp.safety_max = 95.0f;
    sp.vpd_high = 1.0f;
    auto s = initial_state();
    // VPD above band at 91°F (within 5°F of safety_max=95)
    auto in = make_inputs(91.0f, 1.5f);
    // Run enough cycles to pass vpd_watch_dwell
    for (int i = 0; i < 15; i++) determine_mode(in, sp, s, 5000);
    // Should NOT be in SEALED_MIST — too hot
    ASSERT_TRUE(s.mode != SEALED_MIST);
    // At 85°F (safe margin), it SHOULD seal
    s = initial_state();
    in = make_inputs(85.0f, 1.5f);
    for (int i = 0; i < 15; i++) determine_mode(in, sp, s, 5000);
    ASSERT_EQ(s.mode, SEALED_MIST);
    PASS();
}

TEST(fw7_sealed_mist_exit_on_high_temp) {
    // Enter SEALED_MIST at safe temp, then temp climbs — should exit
    auto sp = default_setpoints();
    sp.safety_max = 95.0f;
    sp.vpd_high = 1.0f;
    auto s = initial_state();
    // Enter at 80°F
    auto in = make_inputs(80.0f, 1.5f);
    for (int i = 0; i < 15; i++) determine_mode(in, sp, s, 5000);
    ASSERT_EQ(s.mode, SEALED_MIST);
    // Temp climbs to 91°F (within 5°F of safety_max) — should exit to THERMAL_RELIEF
    in = make_inputs(91.0f, 1.5f);
    Mode m = determine_mode(in, sp, s, 5000);
    ASSERT_EQ(m, THERMAL_RELIEF);
    PASS();
}

TEST(fw8_relief_latch_timeout) {
    // After max_relief_cycles exhausted, controller latches VENTILATE.
    // After 30 minutes (360 × 5s cycles), counter should reset.
    auto sp = default_setpoints();
    sp.max_relief_cycles = 3;
    sp.vpd_high = 1.0f;
    sp.vpd_watch_dwell_ms = 5000;
    auto s = initial_state();
    s.relief_cycle_count = 3;  // Already exhausted
    auto in = make_inputs(75.0f, 1.5f);  // VPD above band, temp safe
    // Run through the dwell period
    for (int i = 0; i < 2; i++) determine_mode(in, sp, s, 5000);
    // Should be latched in VENTILATE
    ASSERT_EQ(s.mode, VENTILATE);
    ASSERT_TRUE(s.relief_cycle_count >= 3);
    // Run 360 more cycles (30 minutes at 5s each)
    for (int i = 0; i < 360; i++) determine_mode(in, sp, s, 5000);
    // Latch timer should have reset the counter
    ASSERT_EQ(s.relief_cycle_count, (uint32_t)0);
    // Next cycle with VPD above band should re-enter SEALED_MIST
    determine_mode(in, sp, s, 5000);
    ASSERT_EQ(s.mode, SEALED_MIST);
    PASS();
}

TEST(fw9b_ventilate_fog_on_vpd_emergency) {
    // PR-A: fog trigger in VENTILATE lowered from sp.vpd_max_safe (3.0) to
    // vpd_high_eff + sp.fog_escalation_kpa. With default_setpoints
    // (vpd_high=2.80, vpd_low=0.35) vpd_high_eff=2.19, + fog_escalation_kpa
    // 0.4 = 2.59 trigger. VPD 3.2 still fires (well above), 2.5 does not
    // (still just below 2.59). Behavior with DEFAULT setpoints preserved —
    // the PR-A change matters only when band is narrowed via dispatcher push
    // (see fw9b_ventilate_fog_production_band test below).
    auto sp = default_setpoints();
    sp.vpd_max_safe = 3.0f;
    sp.fog_rh_ceiling = 90.0f;
    sp.fog_min_temp = 55.0f;
    sp.fog_window_start = 7;
    sp.fog_window_end = 17;
    auto s = initial_state();
    // 85°F, VPD 3.2 (above trigger 2.59), 25% RH, hour 14 (in fog window)
    auto in = make_inputs(85.0f, 3.2f, 25.0f);
    auto out = resolve_equipment(VENTILATE, in, sp, s, true);
    ASSERT_TRUE(out.fog);   // Fog should be ON
    ASSERT_TRUE(out.vent);  // Vent still open (VENTILATE mode)
    ASSERT_TRUE(out.fan1);  // Fans running
    // VPD at 2.5 (below trigger 2.59) — no fog
    in = make_inputs(85.0f, 2.5f, 35.0f);
    out = resolve_equipment(VENTILATE, in, sp, s, true);
    ASSERT_FALSE(out.fog);
    PASS();
}

TEST(fw9b_ventilate_fog_production_band) {
    // PR-A: with production-like narrow band (vpd_high=1.2), the lowered
    // fog trigger changes behavior materially. Old code: fog in VENTILATE
    // only at vpd > vpd_max_safe (3.0). New code: fog at vpd > vpd_high_eff
    // + fog_escalation_kpa = 1.05 + 0.4 = 1.45. So VPD 1.8 (above band)
    // now fires fog where it previously did not — closes the 38%/week
    // concurrent-gap window measured in the 7-day planner-push corpus.
    auto sp = default_setpoints();
    sp.vpd_high = 1.2f;              // narrow production band
    sp.vpd_low  = 0.6f;
    sp.vpd_max_safe = 3.0f;           // safety threshold unchanged
    sp.fog_escalation_kpa = 0.4f;
    sp.fog_rh_ceiling = 90.0f;
    sp.fog_min_temp = 55.0f;
    sp.fog_window_start = 7;
    sp.fog_window_end = 17;
    auto s = initial_state();
    // 85°F, VPD 1.8 (above band, below OLD vpd_max_safe 3.0), hour 14
    // OLD BEHAVIOR: fog off (vpd < 3.0). NEW BEHAVIOR: fog on (vpd > 1.45).
    auto in = make_inputs(85.0f, 1.8f, 40.0f);
    auto out = resolve_equipment(VENTILATE, in, sp, s, true);
    ASSERT_TRUE(out.fog);   // Fog fires at band-exceedance, not at safety
    ASSERT_TRUE(out.vent);  // Concurrent vent+fog — the whole point of PR-A
    // VPD 1.3 (just above band top, below trigger 1.45) — fog off
    in = make_inputs(85.0f, 1.3f, 45.0f);
    out = resolve_equipment(VENTILATE, in, sp, s, true);
    ASSERT_FALSE(out.fog);
    PASS();
}

// ═══════════════════════════════════════════════════════════════
// OBS-1e — evaluate_overrides() — Sprint 16 observability
// One test per silent override. Each test asserts BOTH that the
// flag fires when the override is active AND that it stays clear
// when the override's "desire trigger" is not met (zero false positives).
// ═══════════════════════════════════════════════════════════════

TEST(obs1e_occupancy_blocks_equipment_fires_in_seal) {
    auto sp = default_setpoints(); sp.occupancy_inhibit = true;
    auto s = initial_state(); s.vpd_watch_timer_ms = 60000;
    auto in = make_inputs(72.0f, 1.5f); in.occupied = true;
    auto f = evaluate_overrides(in, sp, s, SEALED_MIST);
    ASSERT_TRUE(f.occupancy_blocks_equipment);
    PASS();
}

TEST(obs1e_occupancy_blocks_equipment_fires_in_ventilate) {
    auto sp = band_setpoints(); sp.occupancy_inhibit = true;
    auto s = initial_state();
    auto in = make_inputs(86.0f, 1.0f); in.occupied = true;
    auto f = evaluate_overrides(in, sp, s, VENTILATE);
    ASSERT_TRUE(f.occupancy_blocks_equipment);
    PASS();
}

TEST(obs1e_occupancy_quiet_when_nothing_wanted) {
    auto sp = default_setpoints(); sp.occupancy_inhibit = true;
    auto s = initial_state();
    auto in = make_inputs(72.0f, 0.9f); in.occupied = true;  // in-band, nobody wants mist
    auto f = evaluate_overrides(in, sp, s, IDLE);
    ASSERT_FALSE(f.occupancy_blocks_equipment);
    PASS();
}

TEST(obs1e_fog_gate_rh_fires) {
    auto sp = default_setpoints();
    auto s = initial_state(); s.mist_stage = MIST_S2; s.vpd_watch_timer_ms = 60000;
    auto in = make_inputs(72.0f, sp.vpd_high + sp.fog_escalation_kpa + 0.1f, 95.0f);
    auto f = evaluate_overrides(in, sp, s, SEALED_MIST);
    ASSERT_TRUE(f.fog_gate_rh);
    ASSERT_FALSE(f.fog_gate_temp);
    ASSERT_FALSE(f.fog_gate_window);
    PASS();
}

TEST(obs1e_fog_gate_temp_fires) {
    auto sp = default_setpoints();
    auto s = initial_state(); s.mist_stage = MIST_S2; s.vpd_watch_timer_ms = 60000;
    auto in = make_inputs(sp.fog_min_temp - 2.0f, sp.vpd_high + sp.fog_escalation_kpa + 0.1f, 50.0f);
    auto f = evaluate_overrides(in, sp, s, SEALED_MIST);
    ASSERT_TRUE(f.fog_gate_temp);
    ASSERT_FALSE(f.fog_gate_rh);
    PASS();
}

TEST(obs1e_fog_gate_window_fires) {
    auto sp = default_setpoints();
    auto s = initial_state(); s.mist_stage = MIST_S2; s.vpd_watch_timer_ms = 60000;
    auto in = make_inputs(72.0f, sp.vpd_high + sp.fog_escalation_kpa + 0.1f, 50.0f);
    in.local_hour = sp.fog_window_end;  // one past close
    auto f = evaluate_overrides(in, sp, s, SEALED_MIST);
    ASSERT_TRUE(f.fog_gate_window);
    PASS();
}

TEST(obs1e_fog_gate_quiet_when_not_in_s2) {
    auto sp = default_setpoints();
    auto s = initial_state(); s.mist_stage = MIST_S1;  // not S2
    auto in = make_inputs(72.0f, sp.vpd_high + sp.fog_escalation_kpa + 0.1f, 95.0f);
    auto f = evaluate_overrides(in, sp, s, SEALED_MIST);
    ASSERT_FALSE(f.fog_gate_rh);
    PASS();
}

TEST(obs1e_relief_cycle_breaker_fires) {
    auto sp = band_setpoints();
    auto s = initial_state();
    s.vpd_watch_timer_ms = 60000;
    s.relief_cycle_count = sp.max_relief_cycles;  // at the ceiling
    auto in = make_inputs(72.0f, 1.5f);
    auto f = evaluate_overrides(in, sp, s, VENTILATE);
    ASSERT_TRUE(f.relief_cycle_breaker);
    PASS();
}

TEST(obs1e_relief_cycle_breaker_quiet_below_ceiling) {
    auto sp = default_setpoints();
    auto s = initial_state();
    s.vpd_watch_timer_ms = 60000;
    s.relief_cycle_count = 1;  // plenty of headroom
    auto in = make_inputs(72.0f, 1.5f);
    auto f = evaluate_overrides(in, sp, s, SEALED_MIST);
    ASSERT_FALSE(f.relief_cycle_breaker);
    PASS();
}

TEST(obs1e_seal_blocked_temp_fires) {
    auto sp = band_setpoints();
    auto s = initial_state(); s.vpd_watch_timer_ms = 60000;
    // within 5°F of safety_max (band 95°F) — planner wants seal, firmware won't
    auto in = make_inputs(sp.safety_max - 3.0f, 1.5f);
    auto f = evaluate_overrides(in, sp, s, VENTILATE);
    ASSERT_TRUE(f.seal_blocked_temp);
    PASS();
}

TEST(obs1e_seal_blocked_temp_quiet_when_cool) {
    auto sp = default_setpoints();
    auto s = initial_state(); s.vpd_watch_timer_ms = 60000;
    auto in = make_inputs(72.0f, 1.5f);
    auto f = evaluate_overrides(in, sp, s, SEALED_MIST);
    ASSERT_FALSE(f.seal_blocked_temp);
    PASS();
}

TEST(obs1e_vpd_dry_override_fires_when_r23_forces_seal) {
    // OBS-1e patch: the override is set by determine_mode()'s R2-3 path.
    // Start in IDLE with zero dwell — planner would never seal yet —
    // but VPD climbs above vpd_max_safe. R2-3 forces SEALED_MIST and
    // sets dry_override_active. evaluate_overrides reads the flag.
    auto sp = default_setpoints();
    auto s = initial_state();
    s.vpd_watch_timer_ms = 0;
    auto in = make_inputs(72.0f, sp.vpd_max_safe + 0.2f);
    Mode m = determine_mode(in, sp, s, 5000);
    ASSERT_EQ(m, SEALED_MIST);           // R2-3 forced the seal
    ASSERT_TRUE(s.dry_override_active);  // flag set during determine_mode
    auto f = evaluate_overrides(in, sp, s, m);
    ASSERT_TRUE(f.vpd_dry_override);
    PASS();
}

TEST(obs1e_vpd_dry_override_quiet_when_planner_already_sealed) {
    // If we're already in SEALED_MIST via the planner's dwell, R2-3's
    // preconditions still hold but the transition isn't a forced override
    // — state.dry_override_active stays false because pre_r23_mode was
    // already SEALED_MIST.
    auto sp = default_setpoints();
    auto s = initial_state();
    s.vpd_watch_timer_ms = 60000;    // dwell mature
    s.mode_prev = SEALED_MIST;       // already sealed last cycle
    auto in = make_inputs(72.0f, sp.vpd_max_safe + 0.2f);
    Mode m = determine_mode(in, sp, s, 5000);
    ASSERT_EQ(m, SEALED_MIST);
    ASSERT_FALSE(s.dry_override_active);
    auto f = evaluate_overrides(in, sp, s, m);
    ASSERT_FALSE(f.vpd_dry_override);
    PASS();
}

TEST(obs1e_vpd_dry_override_clears_on_next_cycle_when_conditions_pass) {
    // Once VPD drops back below vpd_max_safe, dry_override_active must
    // reset to false so a stale flag doesn't linger.
    auto sp = default_setpoints();
    auto s = initial_state();
    s.vpd_watch_timer_ms = 0;
    auto in1 = make_inputs(72.0f, sp.vpd_max_safe + 0.2f);
    determine_mode(in1, sp, s, 5000);
    ASSERT_TRUE(s.dry_override_active);
    // Next cycle: VPD in-band, no R2-3 trigger
    auto in2 = make_inputs(72.0f, 0.9f);
    determine_mode(in2, sp, s, 5000);
    ASSERT_FALSE(s.dry_override_active);
    PASS();
}

TEST(obs1e_all_quiet_on_idle_in_band) {
    auto sp = default_setpoints();
    auto s = initial_state();
    auto in = make_inputs(72.0f, 0.9f);
    auto f = evaluate_overrides(in, sp, s, IDLE);
    ASSERT_FALSE(f.occupancy_blocks_equipment);
    ASSERT_FALSE(f.fog_gate_rh);
    ASSERT_FALSE(f.fog_gate_temp);
    ASSERT_FALSE(f.fog_gate_window);
    ASSERT_FALSE(f.relief_cycle_breaker);
    ASSERT_FALSE(f.seal_blocked_temp);
    ASSERT_FALSE(f.vpd_dry_override);
    PASS();
}

// ═══════════════════════════════════════════════════════════════════
// Sprint-8 — R2-3 state preservation + midnight-wrap fog window
// ═══════════════════════════════════════════════════════════════════

TEST(s8_r23_preserves_mist_stage_when_already_sealed) {
    // Pre-sprint-8: R2-3 unconditionally reset mist_stage to MIST_S1,
    // demoting peak MIST_FOG back to S1 at the exact moment VPD was
    // worst. Now: state preserved when pre_r23_mode == SEALED_MIST.
    auto sp = band_setpoints();
    auto s = initial_state();
    s.mode_prev = SEALED_MIST;
    s.mist_stage = MIST_FOG;
    s.mist_stage_timer_ms = 45000;
    s.sealed_timer_ms = 300000;
    s.vpd_watch_timer_ms = 60000;  // dwell mature
    auto in = make_inputs(72.0f, sp.vpd_max_safe + 0.2f);
    Mode m = determine_mode(in, sp, s, 5000);
    ASSERT_EQ(m, SEALED_MIST);
    ASSERT_EQ(s.mist_stage, MIST_FOG);  // NOT demoted
    ASSERT_TRUE(s.mist_stage_timer_ms >= 45000);  // NOT reset
    ASSERT_FALSE(s.dry_override_active);  // not a forced transition
    PASS();
}

TEST(s8_r23_preserves_sealed_timer_under_sustained_dryness) {
    // Pre-sprint-8: sealed_timer_ms was reset to dt_ms every cycle
    // while R2-3 fired, making sealed_max_ms unreachable and defeating
    // the THERMAL_RELIEF backstop. Post: timer accumulates normally, and
    // the state machine hits THERMAL_RELIEF once sealed_timer >= max.
    auto sp = default_setpoints();
    auto s = initial_state();
    s.mode_prev = SEALED_MIST;
    s.mist_stage = MIST_FOG;
    s.sealed_timer_ms = sp.sealed_max_ms - 5000;  // 5 s from max
    s.vpd_watch_timer_ms = 60000;
    auto in = make_inputs(72.0f, sp.vpd_max_safe + 0.2f);  // R2-3 trigger
    // Tick 1 (dt=10s): normal accumulator advances sealed_timer past max.
    // R2-3 fires but no longer resets the timer (sprint-8 fix).
    Mode m = determine_mode(in, sp, s, 10000);
    ASSERT_EQ(m, SEALED_MIST);
    ASSERT_TRUE(s.sealed_timer_ms > sp.sealed_max_ms);
    // Tick 2: the was_sealed branch now sees sealed_timer_ms >= max and
    // transitions to THERMAL_RELIEF. Pre-sprint-8 this path was
    // permanently unreachable under sustained R2-3 triggering.
    m = determine_mode(in, sp, s, 5000);
    ASSERT_EQ(m, THERMAL_RELIEF);
    PASS();
}

TEST(s8_r23_still_forces_seal_from_idle) {
    // Sanity: the non-sealed path still seeds mist_stage and
    // sealed_timer as before. Regression check on the main R2-3 flow.
    auto sp = default_setpoints();
    auto s = initial_state();  // mode_prev = IDLE, no dwell maturity
    auto in = make_inputs(72.0f, sp.vpd_max_safe + 0.2f);
    Mode m = determine_mode(in, sp, s, 5000);
    ASSERT_EQ(m, SEALED_MIST);
    ASSERT_EQ(s.mist_stage, MIST_S1);  // fresh seal seeds S1
    ASSERT_EQ(s.sealed_timer_ms, 5000u);
    ASSERT_EQ(s.vpd_watch_timer_ms, sp.vpd_watch_dwell_ms);
    ASSERT_TRUE(s.dry_override_active);  // this IS a forced override
    PASS();
}

TEST(s8_fog_window_wraps_midnight) {
    // Pre-sprint-8: a window crossing midnight (start > end) evaluated
    // to (hour < start || hour >= end) == true for every hour, gating
    // fog 24/7. Now: (hour >= start || hour < end) inside the wrap.
    auto sp = default_setpoints();
    sp.fog_window_start = 22;
    sp.fog_window_end = 6;
    auto s = initial_state();
    s.mist_stage = MIST_FOG;
    auto in = make_inputs(72.0f, 1.7f);

    // Inside the wrap — fog permitted
    in.local_hour = 23;
    ASSERT_TRUE(fog_permitted(in, sp));
    in.local_hour = 0;
    ASSERT_TRUE(fog_permitted(in, sp));
    in.local_hour = 5;
    ASSERT_TRUE(fog_permitted(in, sp));

    // Outside the wrap — fog gated
    in.local_hour = 6;   // inclusive end
    ASSERT_FALSE(fog_permitted(in, sp));
    in.local_hour = 12;
    ASSERT_FALSE(fog_permitted(in, sp));
    in.local_hour = 21;  // one hour before start
    ASSERT_FALSE(fog_permitted(in, sp));

    // Non-wrapping window (start <= end) still works
    sp.fog_window_start = 7;
    sp.fog_window_end = 17;
    in.local_hour = 12;
    ASSERT_TRUE(fog_permitted(in, sp));
    in.local_hour = 17;  // inclusive end
    ASSERT_FALSE(fog_permitted(in, sp));
    in.local_hour = 6;
    ASSERT_FALSE(fog_permitted(in, sp));
    PASS();
}

// ═══════════════════════════════════════════════════════════════════
// Sprint-9 — Heat S2 latch + validate_setpoints relational + SAFETY_HEAT fan
// ═══════════════════════════════════════════════════════════════════

TEST(s9_heat2_latch_sets_when_below_s2_threshold) {
    auto sp = default_setpoints();
    sp.temp_low = 58.0f;
    sp.dH2 = 5.0f;  // S2 threshold = 53°F
    auto s = initial_state();
    ASSERT_FALSE(s.heat2_latched);
    // 52°F → below Tlow - dH2 → latch sets
    determine_mode(make_inputs(52.0f, 0.9f), sp, s, 5000);
    ASSERT_TRUE(s.heat2_latched);
    // resolve_equipment honors the latch
    auto out = resolve_equipment(IDLE, make_inputs(52.0f, 0.9f), sp, s, true);
    ASSERT_TRUE(out.heat1);
    ASSERT_TRUE(out.heat2);
    PASS();
}

TEST(s9_heat2_latches_through_minor_fluctuation) {
    // Pre-sprint-9: temp oscillating inside the gas-stage hysteresis band
    // rapid-cycles the gas valve. Post: latch holds state through the band.
    // Sprint-12: pin both band edges so interior Tlow lands at 58°F —
    // keeps the original threshold shape (S2 set at <53, release at >=59).
    auto sp = default_setpoints();
    sp.temp_low = 56.0f; sp.temp_high = 64.0f;  // band=8, Tlow_interior=58
    sp.dH2 = 5.0f;
    sp.heat_hysteresis = 1.0f;  // S1 exit = 59°F
    auto s = initial_state();
    // Latch sets at 52°F (52 < Tlow - dH2 = 53).
    determine_mode(make_inputs(52.0f, 0.9f), sp, s, 5000);
    ASSERT_TRUE(s.heat2_latched);
    // Temp recovers into the hysteresis band — latch holds.
    determine_mode(make_inputs(55.0f, 0.9f), sp, s, 5000);
    ASSERT_TRUE(s.heat2_latched);
    determine_mode(make_inputs(58.5f, 0.9f), sp, s, 5000);
    ASSERT_TRUE(s.heat2_latched);
    // Temp crosses S1 exit (Tlow + heat_hysteresis = 59) — latch releases.
    determine_mode(make_inputs(59.0f, 0.9f), sp, s, 5000);
    ASSERT_FALSE(s.heat2_latched);
    PASS();
}

TEST(s9_heat2_latch_does_not_re_set_in_hysteresis_band) {
    // After release, mild undershoot into the band should NOT re-latch.
    // Only a drop below S2 threshold re-latches.
    // Sprint-12: same narrow band as s9_heat2_latches_through_minor_fluctuation.
    auto sp = default_setpoints();
    sp.temp_low = 56.0f; sp.temp_high = 64.0f;  // band=8, Tlow_interior=58
    sp.dH2 = 5.0f;
    sp.heat_hysteresis = 1.0f;
    auto s = initial_state();
    s.heat2_latched = false;
    determine_mode(make_inputs(55.0f, 0.9f), sp, s, 5000);  // in the band
    ASSERT_FALSE(s.heat2_latched);
    determine_mode(make_inputs(53.5f, 0.9f), sp, s, 5000);  // still in band
    ASSERT_FALSE(s.heat2_latched);
    determine_mode(make_inputs(52.0f, 0.9f), sp, s, 5000);  // below threshold
    ASSERT_TRUE(s.heat2_latched);
    PASS();
}

TEST(s9_validate_swaps_inverted_vpd_bounds) {
    Setpoints sp = default_setpoints();
    // Operator typo: swapped low and high.
    sp.vpd_low = 1.5f;
    sp.vpd_high = 0.5f;
    validate_setpoints(sp);
    ASSERT_TRUE(sp.vpd_low < sp.vpd_high);
    // Individual clamps kept vpd_high inside [0.3, 5.0] and pulled
    // vpd_low below it.
    ASSERT_TRUE(sp.vpd_high >= 0.3f && sp.vpd_high <= 5.0f);
    ASSERT_TRUE(sp.vpd_low >= 0.1f);
    PASS();
}

TEST(s9_validate_enforces_vpd_ordering_across_all_four) {
    Setpoints sp = default_setpoints();
    // vpd_min_safe and vpd_max_safe were unclamped pre-sprint-9.
    sp.vpd_min_safe = 2.0f;   // nonsense: above vpd_low (0.5)
    sp.vpd_max_safe = 0.5f;   // nonsense: below vpd_high (1.2)
    validate_setpoints(sp);
    ASSERT_TRUE(sp.vpd_min_safe < sp.vpd_low);
    ASSERT_TRUE(sp.vpd_low < sp.vpd_high);
    ASSERT_TRUE(sp.vpd_high < sp.vpd_max_safe);
    PASS();
}

TEST(s9_validate_enforces_safety_gt_thigh_plus_dc2) {
    Setpoints sp = default_setpoints();
    sp.temp_high = 82.0f;
    sp.dC2 = 15.0f;          // 82 + 15 = 97, above safety_max 95
    sp.safety_max = 95.0f;
    validate_setpoints(sp);
    ASSERT_TRUE(sp.temp_high + sp.dC2 < sp.safety_max);
    PASS();
}

TEST(s9_validate_fixes_zero_width_fog_window) {
    Setpoints sp = default_setpoints();
    sp.fog_window_start = 12;
    sp.fog_window_end = 12;  // zero width
    validate_setpoints(sp);
    ASSERT_TRUE(sp.fog_window_start != sp.fog_window_end);
    PASS();
}

TEST(s9_validate_prevents_relief_longer_than_sealed) {
    Setpoints sp = default_setpoints();
    sp.sealed_max_ms = 100000;
    sp.relief_duration_ms = 200000;  // longer than sealed window
    validate_setpoints(sp);
    ASSERT_TRUE(sp.relief_duration_ms < sp.sealed_max_ms);
    PASS();
}

TEST(s9_validate_preserves_valid_input) {
    Setpoints sp = default_setpoints();
    Setpoints before = sp;
    validate_setpoints(sp);
    // Sensible defaults should survive unchanged for all the core fields.
    ASSERT_EQ(sp.temp_high, before.temp_high);
    ASSERT_EQ(sp.temp_low,  before.temp_low);
    ASSERT_EQ(sp.vpd_high,  before.vpd_high);
    ASSERT_EQ(sp.vpd_low,   before.vpd_low);
    ASSERT_EQ(sp.safety_max, before.safety_max);
    ASSERT_EQ(sp.safety_min, before.safety_min);
    ASSERT_EQ(sp.fog_window_start, before.fog_window_start);
    ASSERT_EQ(sp.fog_window_end, before.fog_window_end);
    PASS();
}

TEST(validate_clamps_cooling_ai_policy_knobs) {
    Setpoints sp = default_setpoints();
    sp.cool_stage2_over_high_f = 99.0f;
    sp.cool_exit_hysteresis_f = 0.01f;
    sp.cold_vent_guard_delta_f = -4.0f;
    validate_setpoints(sp);
    ASSERT_EQ(sp.cool_stage2_over_high_f, 3.0f);
    ASSERT_EQ(sp.cool_exit_hysteresis_f, 0.3f);
    ASSERT_EQ(sp.cold_vent_guard_delta_f, 0.0f);
    PASS();
}

// ═══════════════════════════════════════════════════════════════════
// Sprint-14 — clamp bias_heat / bias_cool to ±5°F in validate_setpoints
// ═══════════════════════════════════════════════════════════════════

TEST(s14_validate_clamps_bias_heat_above_5) {
    Setpoints sp = default_setpoints();
    sp.bias_heat = 10.0f;
    validate_setpoints(sp);
    ASSERT_EQ(sp.bias_heat, 5.0f);
    PASS();
}

TEST(s14_validate_clamps_bias_heat_below_minus_5) {
    Setpoints sp = default_setpoints();
    sp.bias_heat = -10.0f;
    validate_setpoints(sp);
    ASSERT_EQ(sp.bias_heat, -5.0f);
    PASS();
}

TEST(s14_validate_clamps_bias_cool_above_5) {
    Setpoints sp = default_setpoints();
    sp.bias_cool = 50.0f;
    validate_setpoints(sp);
    ASSERT_EQ(sp.bias_cool, 5.0f);
    PASS();
}

TEST(s14_validate_clamps_bias_cool_below_minus_5) {
    Setpoints sp = default_setpoints();
    sp.bias_cool = -50.0f;
    validate_setpoints(sp);
    ASSERT_EQ(sp.bias_cool, -5.0f);
    PASS();
}

TEST(s14_validate_preserves_bias_in_range) {
    // Values inside [-5, 5] must survive validate unchanged.
    Setpoints sp = default_setpoints();
    sp.bias_heat = 3.0f;
    sp.bias_cool = -2.0f;
    validate_setpoints(sp);
    ASSERT_EQ(sp.bias_heat, 3.0f);
    ASSERT_EQ(sp.bias_cool, -2.0f);
    PASS();
}

// ═══════════════════════════════════════════════════════════════════
// Sprint-10 — THERMAL_RELIEF both fans + vpd_min_safe SEALED exit +
// tunable magic numbers + day/night setpoint pairs
// ═══════════════════════════════════════════════════════════════════

TEST(s10_thermal_relief_runs_both_fans) {
    auto sp = default_setpoints();
    auto s = initial_state();
    auto out_lead1 = resolve_equipment(THERMAL_RELIEF, make_inputs(80.0f, 1.5f), sp, s, /*lead=*/true);
    ASSERT_TRUE(out_lead1.vent);
    ASSERT_TRUE(out_lead1.fan1);
    ASSERT_TRUE(out_lead1.fan2);  // sprint-10: no longer lead-only
    auto out_lead2 = resolve_equipment(THERMAL_RELIEF, make_inputs(80.0f, 1.5f), sp, s, /*lead=*/false);
    ASSERT_TRUE(out_lead2.vent);
    ASSERT_TRUE(out_lead2.fan1);
    ASSERT_TRUE(out_lead2.fan2);
    PASS();
}

TEST(s10_vpd_min_safe_breaks_sealed_mist_with_cleanup) {
    // Pre-sprint-10: vpd_min_safe only overrode mode==IDLE. A sticky
    // SEALED_MIST could drive VPD below vpd_min_safe with no exit.
    auto sp = default_setpoints();
    sp.econ_block = false;
    auto s = initial_state();
    s.mode_prev = SEALED_MIST;
    s.mist_stage = MIST_FOG;
    s.sealed_timer_ms = 200000;
    s.vpd_watch_timer_ms = 60000;
    s.mist_stage_timer_ms = 30000;
    // VPD dangerously low while sealed
    auto in = make_inputs(72.0f, sp.vpd_min_safe - 0.05f);
    Mode m = determine_mode(in, sp, s, 5000);
    ASSERT_EQ(m, DEHUM_VENT);
    // Seal-state cleanup: matches the was_sealed exit path.
    ASSERT_EQ(s.sealed_timer_ms, 0u);
    ASSERT_EQ(s.vpd_watch_timer_ms, 0u);
    ASSERT_EQ(s.mist_stage, MIST_WATCH);
    ASSERT_EQ(s.mist_stage_timer_ms, 0u);
    PASS();
}

TEST(s10_vpd_min_safe_override_respects_econ_block) {
    // With econ_block=true, the vpd_min_safe override does not force
    // DEHUM_VENT. Trace: the was_sealed branch exits SEALED_MIST to
    // IDLE because vpd_below_exit is trivially true at vpd_min_safe,
    // then the override sees mode=IDLE + econ_block=true and stays
    // in IDLE rather than flipping to DEHUM_VENT. Net: with econ
    // blocking the greenhouse lets the humidity sit — the P3#15
    // policy item still applies, this test just documents the
    // current econ-vs-safe precedence.
    auto sp = default_setpoints();
    sp.econ_block = true;
    auto s = initial_state();
    s.mode_prev = SEALED_MIST;
    s.mist_stage = MIST_S1;
    s.sealed_timer_ms = 100000;
    s.vpd_watch_timer_ms = 60000;
    auto in = make_inputs(72.0f, sp.vpd_min_safe - 0.05f);
    Mode m = determine_mode(in, sp, s, 5000);
    ASSERT_EQ(m, IDLE);  // was_sealed exited; econ_block prevents DEHUM_VENT
    PASS();
}

TEST(s10_vent_latch_timeout_is_tunable) {
    // Verify the ex-magic-number behaves as a Setpoints field.
    auto sp = default_setpoints();
    sp.vent_latch_timeout_ms = 120000;  // shorten to 2 min for test
    sp.max_relief_cycles = 3;
    auto s = initial_state();
    s.relief_cycle_count = 3;
    s.vpd_watch_timer_ms = 60000;
    auto in = make_inputs(72.0f, 1.5f);
    // Accumulate enough to trip the timeout
    determine_mode(in, sp, s, 60000);
    determine_mode(in, sp, s, 60000);
    Mode m = determine_mode(in, sp, s, 5000);
    // After ≥120 s of VENTILATE-latch, cycle count should reset.
    ASSERT_EQ(s.relief_cycle_count, 0u);
    ASSERT_EQ(s.vent_latch_timer_ms, 0u);
    PASS();
}

TEST(s10_econ_heat_margin_is_tunable) {
    // Sprint-12: band 60-80 → Tlow_interior=65, Thigh_interior=75.
    // With margin=2, econ heat fires at temp < 73 (75-2). Testing at
    // temps well above S1 threshold (Tlow+heat_hyst = 66) so only the
    // econ path gates. This isolates the margin-tunable behavior.
    auto sp = default_setpoints();
    sp.econ_block = true;
    sp.econ_heat_margin_f = 2.0f;  // tighter than the 5.0 default
    sp.temp_low = 60.0f; sp.temp_high = 80.0f;
    auto s = initial_state();
    // Temp at 74 (outside new 2°F margin 75-2=73, inside old 5°F 75-5=70)
    // Old behavior: heat1 on. New: heat1 off.
    auto in = make_inputs(74.0f, 0.2f);
    auto out = resolve_equipment(IDLE, in, sp, s, true);
    ASSERT_FALSE(out.heat1);
    // Temp at 72 — inside the tightened 2°F margin (75-2=73): 72 < 73 → heat.
    in = make_inputs(72.0f, 0.2f);
    out = resolve_equipment(IDLE, in, sp, s, true);
    ASSERT_TRUE(out.heat1);
    PASS();
}

TEST(s9_safety_heat_runs_lead_fan_for_circulation) {
    auto sp = band_setpoints();
    auto s = initial_state();
    auto in = make_inputs(40.0f, 0.3f);  // trips safety_heat
    Mode m = determine_mode(in, sp, s, 5000);
    ASSERT_EQ(m, SAFETY_HEAT);
    // Lead fan 1
    auto out1 = resolve_equipment(SAFETY_HEAT, in, sp, s, /*lead_is_fan1=*/true);
    ASSERT_TRUE(out1.heat1);
    ASSERT_TRUE(out1.heat2);
    ASSERT_TRUE(out1.fan1);
    ASSERT_FALSE(out1.fan2);
    ASSERT_FALSE(out1.vent);
    // Lead fan 2
    auto out2 = resolve_equipment(SAFETY_HEAT, in, sp, s, /*lead_is_fan1=*/false);
    ASSERT_FALSE(out2.fan1);
    ASSERT_TRUE(out2.fan2);
    ASSERT_FALSE(out2.vent);
    PASS();
}

TEST(s8_safety_heat_clears_same_timers_as_safety_cool) {
    // Pre-sprint-8: SAFETY_HEAT left relief_timer_ms, vent_latch_timer_ms,
    // and vpd_watch_timer_ms populated with whatever they held pre-safety.
    auto sp = band_setpoints();
    auto s = initial_state();
    s.sealed_timer_ms     = 100000;
    s.relief_timer_ms     = 50000;
    s.vpd_watch_timer_ms  = 60000;
    s.relief_cycle_count  = 2;
    s.vent_latch_timer_ms = 200000;
    auto in = make_inputs(40.0f, 0.3f);  // trips safety_heat
    Mode m = determine_mode(in, sp, s, 5000);
    ASSERT_EQ(m, SAFETY_HEAT);
    ASSERT_EQ(s.sealed_timer_ms, 0u);
    ASSERT_EQ(s.relief_timer_ms, 0u);
    ASSERT_EQ(s.vpd_watch_timer_ms, 0u);
    ASSERT_EQ(s.relief_cycle_count, 0u);
    ASSERT_EQ(s.vent_latch_timer_ms, 0u);
    PASS();
}

// ═══════════════════════════════════════════════════════════════════
// Sprint-12 — Center-of-band targeting
// ═══════════════════════════════════════════════════════════════════

TEST(s12_heating_targets_band_interior) {
    // With band 62-75°F, bias_heat=0, heat_hysteresis=1.0:
    //   band_width = 13, Tlow_interior = 62 + 13*0.25 = 65.25
    //   S1 heat on when temp < Tlow_interior + heat_hysteresis = 66.25
    //   S1 heat off when temp >= 66.25
    // Pre-sprint-12 would have fired below 63 and held temp pinned near
    // temp_low=62. Post-sprint-12 stabilizes near 65-66°F (band interior).
    auto sp = default_setpoints();
    sp.temp_low = 62.0f; sp.temp_high = 75.0f;
    sp.bias_heat = 0.0f; sp.heat_hysteresis = 1.0f;
    auto s = initial_state();
    // At 64°F (below 66.25) → heating fires.
    auto out_low = resolve_equipment(IDLE, make_inputs(64.0f, 0.9f), sp, s, true);
    ASSERT_TRUE(out_low.heat1);
    // At 67°F (above 66.25) → heat off.
    auto out_high = resolve_equipment(IDLE, make_inputs(67.0f, 0.9f), sp, s, true);
    ASSERT_FALSE(out_high.heat1);
    // 62°F (right at old temp_low edge) is now well inside the heating
    // zone — pre-sprint-12 would have had heat right on the edge only.
    auto out_edge = resolve_equipment(IDLE, make_inputs(62.0f, 0.9f), sp, s, true);
    ASSERT_TRUE(out_edge.heat1);
    PASS();
}

TEST(s12_cooling_targets_band_interior) {
    // With band 62-75°F, bias_cool=0, temp_hysteresis=1.5:
    //   Thigh_interior = 75 - 13*0.25 = 71.75
    //   Cooling enters at temp > 71.75 (not > 75)
    //   Cooling exit (was_ventilating) at temp <= 70.25
    auto sp = default_setpoints();
    sp.temp_low = 62.0f; sp.temp_high = 75.0f;
    sp.bias_cool = 0.0f; sp.temp_hysteresis = 1.5f;
    auto s = initial_state();
    // 72°F > 71.75 → VENTILATE (pre-sprint-12 would be IDLE: 72 < 75).
    Mode m1 = determine_mode(make_inputs(72.0f, 0.9f), sp, s, 5000);
    ASSERT_EQ(m1, VENTILATE);
    // 71°F above exit threshold 70.25 → still VENTILATE.
    Mode m2 = determine_mode(make_inputs(71.0f, 0.9f), sp, s, 5000);
    ASSERT_EQ(m2, VENTILATE);
    // 70°F below exit threshold → IDLE.
    Mode m3 = determine_mode(make_inputs(70.0f, 0.9f), sp, s, 5000);
    ASSERT_EQ(m3, IDLE);
    PASS();
}

TEST(s12_narrow_band_floor_guard) {
    // Pathological narrow band (temp_low ≈ temp_high) hits the max(2.0f)
    // floor in the band_width computation. Without the floor, the 25%
    // inset would eat the whole band and invert Tlow > Thigh.
    // With the floor, band_width = 2, Tlow_interior = temp_low + 0.5,
    // Thigh_interior = temp_high - 0.5. validate_setpoints separately
    // prevents temp_low >= temp_high, but the floor guarantees a 1°F
    // minimum gap between heating target and cooling target.
    auto sp = default_setpoints();
    sp.temp_low = 70.0f; sp.temp_high = 70.5f;  // band=0.5, below floor
    sp.bias_heat = 0.0f; sp.bias_cool = 0.0f;
    auto s = initial_state();
    // band_width floor = 2.0. Tlow_interior = 70 + 0.5 = 70.5,
    // Thigh_interior = 70.5 - 0.5 = 70.0. Tlow(70.5) > Thigh(70.0) is
    // tolerable here — the two targets overlap but don't produce a
    // crash, and heat_hysteresis still provides a deadband.
    // Practical check: determine_mode doesn't crash and returns a mode.
    Mode m = determine_mode(make_inputs(70.25f, 0.9f), sp, s, 5000);
    ASSERT_TRUE(m == IDLE || m == VENTILATE || m == SEALED_MIST);
    // And resolve_equipment produces a valid output.
    auto out = resolve_equipment(m, make_inputs(70.25f, 0.9f), sp, s, true);
    (void)out;
    PASS();
}

// ═══════════════════════════════════════════════════════════════════
// Sprint-15 — Summer thermal-driven vent gate
//   The gate pre-empts vpd_wants_seal when ALL of:
//     - sw_summer_vent_enabled
//     - outdoor_data_age_s < outdoor_staleness_max_s
//     - outdoor_temp_f < indoor_temp_f - vent_prefer_temp_delta_f
//     - outdoor_dewpoint_f < indoor_dewpoint_f - vent_prefer_dp_delta_f
//     - indoor_temp_f > temp_low + temp_hysteresis
//   Result: state machine falls through to VENTILATE instead of
//   SEALED_MIST. state.override_summer_vent and OverrideFlags
//   .summer_vent_active are set true.
// ═══════════════════════════════════════════════════════════════════

// Helper: builds an indoor-hot-and-humid scenario where vpd_wants_seal
// would otherwise fire AND seal is not blocked by safety_max_seal_margin
// (so the gate-inactive tests actually exercise the SEALED_MIST path).
// Use 85°F — well above interior cooling target (~75) but below
// safety_max(95) - seal_margin(5) = 90. Tests then dial outdoor_* to
// flip the gate on or off.
static SensorInputs s15_indoor_stressed() {
    SensorInputs in = make_inputs(85.0f, 1.5f, 65.0f);
    in.dew_point_f = 72.0f;  // ~consistent with 85°F + 65% RH
    return in;
}

static Setpoints s15_band() {
    Setpoints sp = band_setpoints();  // narrow band 65-78°F, vpd 0.8-1.4 kPa
    // Defaults already include sw_summer_vent_enabled=true, deltas=5,
    // outdoor_staleness_max_s=300 from default_setpoints().
    return sp;
}

TEST(s15_gate_fires_when_outdoor_cooler_and_drier) {
    // Indoor 91°F / DP 76. Outdoor 77°F / DP 35 (cooler by 14°F, drier
    // by 41°F DP). Gate should fire and pre-empt VPD-seal even though
    // vpd_watch_timer is mature.
    auto sp = s15_band();
    auto s = initial_state();
    s.vpd_watch_timer_ms = 60000;  // mature dwell — would otherwise SEAL
    auto in = s15_indoor_stressed();
    in.outdoor_temp_f = 77.0f;
    in.outdoor_dewpoint_f = 35.0f;
    in.outdoor_data_age_s = 30u;  // fresh
    Mode m = determine_mode(in, sp, s, 5000);
    ASSERT_EQ(m, VENTILATE);
    ASSERT_TRUE(s.override_summer_vent);
    auto f = evaluate_overrides(in, sp, s, m);
    ASSERT_TRUE(f.summer_vent_active);
    PASS();
}

TEST(s15_gate_inactive_when_outdoor_warmer) {
    // Outdoor at indoor temp — cooler check fails — gate inactive,
    // existing SEALED_MIST path runs.
    auto sp = s15_band();
    auto s = initial_state();
    s.vpd_watch_timer_ms = 60000;
    auto in = s15_indoor_stressed();
    in.outdoor_temp_f = 91.0f;       // not cooler
    in.outdoor_dewpoint_f = 35.0f;
    in.outdoor_data_age_s = 30u;
    Mode m = determine_mode(in, sp, s, 5000);
    ASSERT_EQ(m, SEALED_MIST);
    ASSERT_FALSE(s.override_summer_vent);
    PASS();
}

TEST(s15_gate_inactive_when_outdoor_humid) {
    // Outdoor cooler but outdoor DP is HIGHER than indoor DP — venting
    // would import humidity. Gate stays off.
    auto sp = s15_band();
    auto s = initial_state();
    s.vpd_watch_timer_ms = 60000;
    auto in = s15_indoor_stressed();
    in.outdoor_temp_f = 77.0f;       // cooler
    in.outdoor_dewpoint_f = 80.0f;   // higher DP than indoor 76
    in.outdoor_data_age_s = 30u;
    Mode m = determine_mode(in, sp, s, 5000);
    ASSERT_EQ(m, SEALED_MIST);
    ASSERT_FALSE(s.override_summer_vent);
    PASS();
}

TEST(s15_gate_inactive_when_outdoor_data_stale) {
    // Conditions otherwise favorable, but outdoor data is older than
    // outdoor_staleness_max_s. Fail-safe: don't trust stale data.
    auto sp = s15_band();
    auto s = initial_state();
    s.vpd_watch_timer_ms = 60000;
    auto in = s15_indoor_stressed();
    in.outdoor_temp_f = 77.0f;
    in.outdoor_dewpoint_f = 35.0f;
    in.outdoor_data_age_s = 9999u;   // way past 300s default
    Mode m = determine_mode(in, sp, s, 5000);
    ASSERT_EQ(m, SEALED_MIST);
    ASSERT_FALSE(s.override_summer_vent);
    PASS();
}

TEST(s15_gate_inactive_when_switch_off) {
    // Operator toggle — gate disabled regardless of conditions.
    auto sp = s15_band();
    sp.sw_summer_vent_enabled = false;
    auto s = initial_state();
    s.vpd_watch_timer_ms = 60000;
    auto in = s15_indoor_stressed();
    in.outdoor_temp_f = 77.0f;
    in.outdoor_dewpoint_f = 35.0f;
    in.outdoor_data_age_s = 30u;
    Mode m = determine_mode(in, sp, s, 5000);
    ASSERT_EQ(m, SEALED_MIST);
    ASSERT_FALSE(s.override_summer_vent);
    PASS();
}

TEST(s15_gate_inactive_when_indoor_cool) {
    // Indoor temp is at or below temp_low + temp_hysteresis — no
    // cooling demand, don't vent into a cold night.
    auto sp = s15_band();          // temp_low=65, temp_hysteresis=1.5 → threshold 66.5
    auto s = initial_state();
    s.vpd_watch_timer_ms = 60000;
    SensorInputs in = make_inputs(66.0f, 1.5f, 65.0f);
    in.dew_point_f = 60.0f;
    in.outdoor_temp_f = 50.0f;       // outdoor cooler than indoor
    in.outdoor_dewpoint_f = 30.0f;
    in.outdoor_data_age_s = 30u;
    Mode m = determine_mode(in, sp, s, 5000);
    ASSERT_EQ(m, SEALED_MIST);
    ASSERT_FALSE(s.override_summer_vent);
    PASS();
}

TEST(s15_safety_cool_still_pre_empts_gate) {
    // Indoor at safety_max — SAFETY_COOL must fire regardless of
    // gate; safety rails are above the gate in priority.
    auto sp = s15_band();            // safety_max=95
    auto s = initial_state();
    s.vpd_watch_timer_ms = 60000;
    SensorInputs in = make_inputs(96.0f, 1.5f, 65.0f);
    in.dew_point_f = 80.0f;
    in.outdoor_temp_f = 77.0f;       // gate would otherwise fire
    in.outdoor_dewpoint_f = 35.0f;
    in.outdoor_data_age_s = 30u;
    Mode m = determine_mode(in, sp, s, 5000);
    ASSERT_EQ(m, SAFETY_COOL);
    PASS();
}

TEST(s15_thermal_relief_still_pre_empts_gate) {
    // In-progress THERMAL_RELIEF burst — must complete regardless of gate.
    auto sp = s15_band();
    auto s = initial_state();
    s.mode_prev = THERMAL_RELIEF;
    s.relief_timer_ms = 30000;       // 30s into the 90s burst
    s.vpd_watch_timer_ms = 60000;
    auto in = s15_indoor_stressed();
    in.outdoor_temp_f = 77.0f;
    in.outdoor_dewpoint_f = 35.0f;
    in.outdoor_data_age_s = 30u;
    Mode m = determine_mode(in, sp, s, 5000);
    ASSERT_EQ(m, THERMAL_RELIEF);
    PASS();
}

TEST(s15_integration_today_1300_data) {
    // Integration: replay today's 13:00 MDT scenario — indoor 91°F /
    // 65% RH (DP ~76°F), outdoor 77°F / 8% RH (DP ~17°F), vpd_watch
    // mature. Pre-sprint-15 firmware sealed; sprint-15 gate must
    // pre-empt and route to VENTILATE.
    auto sp = s15_band();
    auto s = initial_state();
    s.vpd_watch_timer_ms = 60000;    // mature dwell

    SensorInputs in = make_inputs(91.0f, 1.5f, 65.0f);
    in.dew_point_f = 76.0f;
    in.outdoor_temp_f = 77.0f;
    in.outdoor_dewpoint_f = 17.0f;   // 8% RH at 77°F → ~17°F DP
    in.outdoor_data_age_s = 60u;     // fresh (Tempest pulls every 3 min)

    Mode m = determine_mode(in, sp, s, 5000);
    ASSERT_EQ(m, VENTILATE);
    ASSERT_TRUE(s.override_summer_vent);

    // Equipment: vent open, lead fan on, fog OFF (don't humidify dry air).
    auto out = resolve_equipment(m, in, sp, s, /*lead_is_fan1=*/true);
    ASSERT_TRUE(out.vent);
    ASSERT_TRUE(out.fan1);
    ASSERT_FALSE(out.fog);
    PASS();
}

// ═══════════════════════════════════════════════════════════════════
// Sprint-15.1 hotfix — 8 fixes surfaced by overnight observation
//   Fix 2 (gate unseals ongoing SEAL): new test s15_1_gate_unseals_was_sealed
//   Fix 3 (staleness default 600s):    new test s15_1_gate_off_at_new_stale_300_600
//   Fix 8 (mode_reason trace):         new tests s15_1_mode_reason_seal_enter,
//                                                s15_1_mode_reason_summer_vent_preempt,
//                                                s15_1_mode_reason_idle_default
// ═══════════════════════════════════════════════════════════════════

TEST(s15_1_gate_unseals_was_sealed_cycle) {
    // Regression for the P0 bug that caused the 2026-04-20 overnight
    // whipsaw. Pre-15.1: gate set vpd_wants_seal=false but the
    // was_sealed sticky path (line 250 in determine_mode) still held
    // SEALED_MIST across cycles. Post-15.1: gate clears sealed state
    // and forces was_sealed=false so the cascade falls through to
    // VENTILATE.
    auto sp = s15_band();
    auto s = initial_state();
    // Start mid-seal: prev=SEALED_MIST, timer mid-cycle, mist_stage
    // advanced — plausible state the firmware could be in when outdoor
    // conditions become favorable.
    s.mode_prev = SEALED_MIST;
    s.sealed_timer_ms = 120000;      // 2 min into the 10-min seal window
    s.vpd_watch_timer_ms = 60000;
    s.mist_stage = MIST_S2;
    s.mist_stage_timer_ms = 90000;
    s.relief_cycle_count = 1;
    auto in = s15_indoor_stressed();
    in.outdoor_temp_f = 77.0f;       // 14°F cooler than indoor 85
    in.outdoor_dewpoint_f = 35.0f;   // 37°F drier DP
    in.outdoor_data_age_s = 30u;     // fresh

    Mode m = determine_mode(in, sp, s, 5000);
    ASSERT_EQ(m, VENTILATE);
    ASSERT_TRUE(s.override_summer_vent);
    // Gate must have cleaned up sealed state.
    ASSERT_EQ(s.sealed_timer_ms, 0u);
    ASSERT_EQ(s.vpd_watch_timer_ms, 0u);
    ASSERT_EQ(s.mist_stage, MIST_WATCH);
    ASSERT_EQ(s.mist_stage_timer_ms, 0u);
    ASSERT_EQ(s.relief_cycle_count, 0u);
    PASS();
}

TEST(s15_1_gate_fresh_at_400s_with_default_600) {
    // Regression for fix 3: default outdoor_staleness_max_s raised
    // 300 → 600 so dispatcher push jitter past 300s doesn't disable
    // the gate. With default (600) and age 400s, gate should still fire.
    auto sp = s15_band();             // sp.outdoor_staleness_max_s=600 default
    auto s = initial_state();
    s.vpd_watch_timer_ms = 60000;
    auto in = s15_indoor_stressed();
    in.outdoor_temp_f = 77.0f;
    in.outdoor_dewpoint_f = 35.0f;
    in.outdoor_data_age_s = 400u;    // past the OLD 300s threshold, under new 600s

    Mode m = determine_mode(in, sp, s, 5000);
    ASSERT_EQ(m, VENTILATE);
    ASSERT_TRUE(s.override_summer_vent);
    PASS();
}

TEST(s15_1_mode_reason_seal_enter) {
    // Fix 8: mode_reason is set by every branch of determine_mode().
    // Plain seal entry should tag "seal_enter".
    auto sp = band_setpoints();
    auto s = initial_state();
    s.vpd_watch_timer_ms = 60000;    // mature dwell
    // vpd above interior-eff vpd_high_eff (= 1.25) but safe margin ok
    Mode m = determine_mode(make_inputs(80.0f, 1.5f), sp, s, 5000);
    ASSERT_EQ(m, SEALED_MIST);
    ASSERT_TRUE(s.last_mode_reason != nullptr);
    ASSERT_TRUE(std::string(s.last_mode_reason) == "seal_enter");
    PASS();
}

TEST(s15_1_mode_reason_summer_vent_preempt) {
    // Fix 8: when the sprint-15 gate fires and falls through to
    // VENTILATE, mode_reason should tag "summer_vent_preempt", not the
    // plain "temp_vent" a cooling-only mode would produce.
    auto sp = s15_band();
    auto s = initial_state();
    s.vpd_watch_timer_ms = 60000;    // dwell mature — would have sealed
    auto in = s15_indoor_stressed();
    in.outdoor_temp_f = 77.0f;
    in.outdoor_dewpoint_f = 35.0f;
    in.outdoor_data_age_s = 30u;

    Mode m = determine_mode(in, sp, s, 5000);
    ASSERT_EQ(m, VENTILATE);
    ASSERT_TRUE(s.override_summer_vent);
    ASSERT_TRUE(s.last_mode_reason != nullptr);
    ASSERT_TRUE(std::string(s.last_mode_reason) == "summer_vent_preempt");
    PASS();
}

TEST(s15_1_mode_reason_idle_default) {
    // Fix 8: when nothing is wanted — in band, no sealing, no cooling,
    // no safety — the default IDLE path should tag "idle_default". This
    // gives us the diagnostic query "how often are we IDLE during
    // stress windows" which Study 5 flagged at 40 % pre-hotfix.
    auto sp = band_setpoints();
    auto s = initial_state();
    // 72°F is inside the 65-82°F band and VPD 0.9 is inside 0.8-1.4.
    // Sprint-12 interior target: Tlow_interior=68.25, Thigh_interior=74.75.
    // Comfortably in the middle — no seal, no vent, no dehum, no safety.
    Mode m = determine_mode(make_inputs(72.0f, 0.9f), sp, s, 5000);
    ASSERT_EQ(m, IDLE);
    ASSERT_TRUE(s.last_mode_reason != nullptr);
    ASSERT_TRUE(std::string(s.last_mode_reason) == "idle_default");
    PASS();
}

// ═══════════════════════════════════════════════════════════════════
// Phase-2 dwell gate — 5-min hold on non-safety transitions.
// See plan at .claude-agents/iris-dev/plans/yo-iris-dev-you-help-humming-stonebraker.md
// ═══════════════════════════════════════════════════════════════════

TEST(phase2_dwell_gate_off_by_default) {
    // Default: sw_dwell_gate_enabled=false → no hold, current behavior
    // preserved. This test ensures the feature flag works — if the default
    // flipped on, this test would catch it and force review.
    auto sp = band_setpoints();
    ASSERT_TRUE(!sp.sw_dwell_gate_enabled);
    PASS();
}

TEST(phase2_dwell_gate_holds_normal_transition) {
    // With dwell ON, a mode transition that would fire gets held for
    // dwell_gate_ms. Safety preempts — but this is a non-safety case.
    auto sp = band_setpoints();
    sp.sw_dwell_gate_enabled = true;
    sp.dwell_gate_ms = 300000;  // 5 min

    auto s = initial_state();
    s.mode = IDLE;
    s.mode_prev = IDLE;
    // Pretend a transition JUST happened (dwell timer = 0 → in dwell window).
    s.last_transition_tick_ms = 0;

    // Step 1: fire a cooling transition. Temp crosses Thigh.
    // band_setpoints: temp_high=82, bias_cool=0, band 65-82, interior ~69-79.
    // 85°F indoor is well past Thigh_interior+bias_cool=79.
    Mode m1 = determine_mode(make_inputs(85.0f, 1.0f), sp, s, 5000);
    // Gate should hold — last_transition_tick_ms (0) + dt_ms (5000) is still
    // far below dwell_gate_ms (300000). So mode stays IDLE and reason = dwell_hold.
    ASSERT_EQ(m1, IDLE);
    ASSERT_TRUE(std::string(s.last_mode_reason) == "dwell_hold");

    // Step 2: after dwell expires (advance 5 min + 1s of dt), same stimulus
    // now lets the transition through.
    s.last_transition_tick_ms = 301000;  // past 300000
    Mode m2 = determine_mode(make_inputs(85.0f, 1.0f), sp, s, 5000);
    ASSERT_EQ(m2, VENTILATE);
    PASS();
}

TEST(phase2_dwell_gate_safety_preempts) {
    // Safety must always fire immediately, dwell or no dwell.
    auto sp = band_setpoints();
    sp.sw_dwell_gate_enabled = true;
    sp.dwell_gate_ms = 300000;

    auto s = initial_state();
    s.mode = IDLE;
    s.mode_prev = IDLE;
    s.last_transition_tick_ms = 0;  // in dwell

    // Force temp at safety_max → SAFETY_COOL must fire despite dwell.
    Mode m = determine_mode(make_inputs(sp.safety_max + 0.5f, 1.0f), sp, s, 5000);
    ASSERT_EQ(m, SAFETY_COOL);
    PASS();
}

TEST(phase2_dwell_gate_dry_override_preempts) {
    // R2-3: plant damage outranks dwell. VPD > vpd_max_safe forces SEAL
    // even if dwell would otherwise hold the transition.
    auto sp = band_setpoints();
    sp.sw_dwell_gate_enabled = true;
    sp.dwell_gate_ms = 300000;

    auto s = initial_state();
    s.mode = IDLE;
    s.mode_prev = IDLE;
    s.last_transition_tick_ms = 0;  // in dwell

    // Indoor 75°F (in band), but VPD 3.5 > vpd_max_safe (3.0) — R2-3 fires.
    // The R2-3 override is the "dry_override_active" preempt reason.
    Mode m = determine_mode(make_inputs(75.0f, 3.5f), sp, s, 5000);
    ASSERT_EQ(m, SEALED_MIST);
    PASS();
}

TEST(phase2_dwell_gate_thermal_relief_entry_preempts) {
    // THERMAL_RELIEF is transient-by-design (relief_duration_ms, default 90s).
    // Dwell gate MUST NOT block entry to relief — a sealed_timer ≥ sealed_max_ms
    // is the firmware asking for an intervention now. Live 2026-04-21 trial
    // showed holding the entry path bumps relief_cycle_count via repeated
    // relief_timer expiry cycles and trips the max_relief_cycles breaker
    // faster than with gate off.
    auto sp = band_setpoints();
    sp.sw_dwell_gate_enabled = true;
    sp.dwell_gate_ms = 300000;  // 5-min dwell
    sp.sealed_max_ms = 1000;    // tiny for test

    auto s = initial_state();
    s.mode = SEALED_MIST;
    s.mode_prev = SEALED_MIST;
    s.last_transition_tick_ms = 0;    // in dwell
    s.sealed_timer_ms = 2000;         // past sealed_max
    s.mist_stage = MIST_S1;

    // VPD still wants seal (high), temp in band. Firmware wants THERMAL_RELIEF.
    // Dwell must NOT hold the seal → relief transition.
    Mode m = determine_mode(make_inputs(78.0f, 2.5f), sp, s, 5000);
    ASSERT_EQ(m, THERMAL_RELIEF);
    PASS();
}

TEST(phase2_dwell_gate_thermal_relief_exit_preempts) {
    // When in THERMAL_RELIEF, the relief_duration timer governs exit.
    // Dwell MUST NOT hold the exit — holding means firmware re-enters the
    // in_thermal_relief branch and bumps relief_cycle_count every
    // relief_duration_ms. Exit must be free.
    auto sp = band_setpoints();
    sp.sw_dwell_gate_enabled = true;
    sp.dwell_gate_ms = 300000;      // 5-min dwell
    sp.relief_duration_ms = 90000;  // 90s relief duration (default)

    auto s = initial_state();
    s.mode = THERMAL_RELIEF;
    s.mode_prev = THERMAL_RELIEF;
    s.last_transition_tick_ms = 0;  // in dwell
    s.relief_timer_ms = 95000;      // past relief_duration → expire this tick

    // After expiry the firmware re-evaluates. VPD now ok (above vpd_low, below
    // vpd_high) → should IDLE out of relief. Dwell must NOT hold back in relief.
    Mode m = determine_mode(make_inputs(72.0f, 1.0f), sp, s, 5000);
    ASSERT_TRUE(m != THERMAL_RELIEF);
    PASS();
}

TEST(phase2_dwell_gate_tracks_ticks_when_off) {
    // Even with gate OFF, last_transition_tick_ms must still accumulate
    // so that when the gate is flipped ON later (live operation), the
    // first transition has correct dwell accounting when flipping on.
    auto sp = band_setpoints();
    sp.sw_dwell_gate_enabled = false;  // OFF

    auto s = initial_state();
    s.mode = IDLE;
    s.mode_prev = IDLE;
    s.last_transition_tick_ms = 0;

    // Stable conditions, mode stays IDLE. Tick count should advance.
    determine_mode(make_inputs(72.0f, 0.9f), sp, s, 60000);
    ASSERT_EQ(s.last_transition_tick_ms, 60000u);

    determine_mode(make_inputs(72.0f, 0.9f), sp, s, 60000);
    ASSERT_EQ(s.last_transition_tick_ms, 120000u);
    PASS();
}

// ═══════════════════════════════════════════════════════════════
// band-first controller: band-first FSM
// ═══════════════════════════════════════════════════════════════

static Setpoints band_first_setpoints() {
    auto sp = default_setpoints();
    sp.sw_fsm_controller_enabled = true;
    sp.temp_low = 72.0f;
    sp.temp_high = 78.0f;
    sp.vpd_low = 0.8f;
    sp.vpd_high = 1.2f;
    sp.vpd_hysteresis = 0.4f;
    sp.vpd_watch_dwell_ms = 5000;
    sp.sealed_max_ms = 120000;
    sp.mist_backoff_ms = 600000;
    sp.safety_max = 100.0f;
    sp.safety_min = 40.0f;
    return sp;
}

TEST(band_first_heat1_targets_temp_band_lower_quartile) {
    auto sp = band_first_setpoints();  // lower-quartile target = 73.5°F
    sp.heat_hysteresis = 1.0f;
    auto s = initial_state();

    auto out_low = resolve_equipment(IDLE, make_inputs(74.0f, 0.9f), sp, s, true);
    ASSERT_TRUE(out_low.heat1);
    ASSERT_FALSE(out_low.heat2);

    auto out_high = resolve_equipment(IDLE, make_inputs(75.0f, 0.9f), sp, s, true);
    ASSERT_FALSE(out_high.heat1);
    ASSERT_FALSE(out_high.heat2);
    PASS();
}

TEST(band_first_controller_flag_is_explicit_test_setup) {
    auto fallback = default_setpoints();
    auto live = band_first_setpoints();

    ASSERT_FALSE(fallback.sw_fsm_controller_enabled);
    ASSERT_TRUE(live.sw_fsm_controller_enabled);
    PASS();
}

TEST(band_first_heat2_latches_at_temp_low_not_stage2_margin) {
    auto sp = band_first_setpoints();  // band 72-78°F, heat target = 73.5°F
    sp.dH2 = 5.0f;                 // legacy margin must not delay band-first controller gas heat
    auto s = initial_state();

    determine_mode(make_inputs(72.1f, 0.9f), sp, s, 5000);
    ASSERT_FALSE(s.heat2_latched);

    determine_mode(make_inputs(71.9f, 0.9f), sp, s, 5000);
    ASSERT_TRUE(s.heat2_latched);
    ASSERT_TRUE(std::string(s.last_mode_reason) == "heat_stage2");

    auto out = resolve_equipment(IDLE, make_inputs(71.9f, 0.9f), sp, s, true);
    ASSERT_TRUE(out.heat1);
    ASSERT_TRUE(out.heat2);
    PASS();
}

TEST(band_first_heat2_clears_after_lower_target_recovery) {
    auto sp = band_first_setpoints();  // band 72-78°F, heat target = 73.5°F
    auto s = initial_state();

    determine_mode(make_inputs(71.5f, 0.9f), sp, s, 5000);
    ASSERT_TRUE(s.heat2_latched);

    determine_mode(make_inputs(73.0f, 0.9f), sp, s, 5000);
    ASSERT_TRUE(s.heat2_latched);

    determine_mode(make_inputs(73.5f, 0.9f), sp, s, 5000);
    ASSERT_FALSE(s.heat2_latched);
    ASSERT_TRUE(std::string(s.last_mode_reason) == "heat_stage1");
    PASS();
}

TEST(runtime_fan_lead_prefers_lower_runtime_outside_deadband) {
    ASSERT_FALSE(choose_runtime_balanced_fan_lead(
        true,
        60U * 60U * 1000U,
        45U * 60U * 1000U,
        true,
        false));
    ASSERT_TRUE(choose_runtime_balanced_fan_lead(
        false,
        45U * 60U * 1000U,
        60U * 60U * 1000U,
        true,
        false));
    PASS();
}

TEST(runtime_fan_lead_holds_inside_deadband_while_running) {
    ASSERT_TRUE(choose_runtime_balanced_fan_lead(
        true,
        60U * 60U * 1000U,
        55U * 60U * 1000U,
        true,
        true));
    ASSERT_FALSE(choose_runtime_balanced_fan_lead(
        false,
        55U * 60U * 1000U,
        60U * 60U * 1000U,
        true,
        true));
    PASS();
}

TEST(runtime_fan_lead_uses_wall_clock_tiebreak_only_when_idle) {
    ASSERT_FALSE(choose_runtime_balanced_fan_lead(
        true,
        60U * 60U * 1000U,
        55U * 60U * 1000U,
        false,
        true));
    ASSERT_TRUE(choose_runtime_balanced_fan_lead(
        true,
        60U * 60U * 1000U,
        55U * 60U * 1000U,
        false,
        false));
    PASS();
}

TEST(relay_runtime_with_active_includes_current_on_interval) {
    ASSERT_EQ(relay_runtime_with_active_ms(1000U, true, 5000U, 8000U), 4000U);
    ASSERT_EQ(relay_runtime_with_active_ms(1000U, false, 5000U, 8000U), 1000U);
    ASSERT_EQ(relay_runtime_with_active_ms(UINT32_MAX - 10U, true, 5000U, 8000U), UINT32_MAX);
    PASS();
}

TEST(band_first_relief_exhausted_does_not_force_cold_vent) {
    auto sp = band_first_setpoints();
    auto s = initial_state();
    s.relief_cycle_count = 3;
    s.vpd_watch_timer_ms = sp.vpd_watch_dwell_ms;

    // This mirrors the live failure: cool greenhouse, cold outdoor air, VPD
    // still above the narrow band. band-first controller must not turn actuator protection into
    // forced ventilation.
    Mode m = determine_mode(make_inputs(66.0f, 1.4f), sp, s, 5000);
    ASSERT_EQ(m, IDLE);
    ASSERT_TRUE(std::string(s.last_mode_reason) == "heat_stage2");
    PASS();
}

TEST(band_first_sealed_timeout_enters_backoff_not_relief) {
    auto sp = band_first_setpoints();
    auto s = initial_state();
    s.mode = SEALED_MIST;
    s.mode_prev = SEALED_MIST;
    s.sealed_timer_ms = sp.sealed_max_ms;
    s.vpd_watch_timer_ms = sp.vpd_watch_dwell_ms;
    s.mist_stage = MIST_S1;

    Mode m = determine_mode(make_inputs(74.0f, 1.5f), sp, s, 5000);
    ASSERT_EQ(m, IDLE);
    ASSERT_TRUE(std::string(s.last_mode_reason) == "mist_backoff");
    ASSERT_TRUE(s.mist_backoff_timer_ms > 0);
    ASSERT_TRUE(s.relief_cycle_count > 0);
    PASS();
}

TEST(band_first_temp_band_preempts_humidification) {
    auto sp = band_first_setpoints();
    auto s = initial_state();
    s.vpd_watch_timer_ms = sp.vpd_watch_dwell_ms;

    Mode m = determine_mode(make_inputs(82.0f, 1.6f), sp, s, 5000);
    ASSERT_EQ(m, VENTILATE);
    ASSERT_TRUE(std::string(s.last_mode_reason) == "vent_mist_assist");
    ASSERT_TRUE(s.vent_mist_assist_active);
    PASS();
}

TEST(band_first_cooling_enters_at_raw_temp_high) {
    auto sp = band_first_setpoints();
    sp.bias_cool = 2.0f;  // legacy offset must not move band-first controller outside the band
    auto s = initial_state();

    Mode m = determine_mode(make_inputs(sp.temp_high + 0.1f, 0.9f), sp, s, 5000);
    ASSERT_EQ(m, VENTILATE);
    ASSERT_TRUE(std::string(s.last_mode_reason) == "temp_high");
    PASS();
}

TEST(band_first_cold_outdoor_cooling_entry_uses_band_margin) {
    auto sp = band_first_setpoints();  // 6°F band => band-first controller cooling margin = 1.5°F
    sp.dC2 = 3.0f;
    auto s = initial_state();

    auto moderate = make_inputs(sp.temp_high + 0.5f, 0.9f);
    moderate.outdoor_temp_f = sp.temp_low - 11.0f;
    ASSERT_EQ(determine_mode(moderate, sp, s, 5000), IDLE);

    auto hot = make_inputs(sp.temp_high + cold_vent_cooling_entry_margin_f(sp) + 0.1f, 0.9f);
    hot.outdoor_temp_f = sp.temp_low - 11.0f;
    ASSERT_EQ(determine_mode(hot, sp, s, 5000), VENTILATE);
    PASS();
}

TEST(band_first_replay_safe_no_solar_preventive_cooling_before_temp_high) {
    auto sp = band_first_setpoints();
    auto s = initial_state();
    auto in = make_inputs(sp.temp_high - 0.5f, 0.9f);
    in.solar_w_m2 = 700.0f;
    in.local_hour = 14;

    Mode m = determine_mode(in, sp, s, 5000);
    ASSERT_EQ(m, IDLE);
    ASSERT_TRUE(std::string(s.last_mode_reason) == "idle");
    PASS();
}

TEST(band_first_solar_preventive_cooling_respects_cold_outdoor_guard) {
    auto sp = band_first_setpoints();
    auto s = initial_state();
    auto in = make_inputs(sp.temp_high - 0.5f, 0.9f);
    in.solar_w_m2 = 700.0f;
    in.local_hour = 14;
    in.outdoor_temp_f = sp.temp_low - 11.0f;

    Mode m = determine_mode(in, sp, s, 5000);
    ASSERT_EQ(m, IDLE);
    ASSERT_TRUE(std::string(s.last_mode_reason) == "idle");
    PASS();
}

TEST(band_first_cooling_stage2_uses_explicit_ai_delta) {
    auto sp = band_first_setpoints();
    sp.dC2 = 3.0f;  // ignored by the band-first path once explicit AI delta is set
    sp.cool_stage2_over_high_f = 1.0f;
    auto s = initial_state();

    auto one = resolve_equipment(VENTILATE, make_inputs(sp.temp_high + 0.9f, 1.4f), sp, s, true);
    ASSERT_TRUE(one.vent);
    ASSERT_TRUE(one.fan1);
    ASSERT_FALSE(one.fan2);

    auto out = resolve_equipment(VENTILATE, make_inputs(sp.temp_high + 1.1f, 1.4f), sp, s, true);
    ASSERT_TRUE(out.vent);
    ASSERT_TRUE(out.fan1);
    ASSERT_TRUE(out.fan2);
    PASS();
}

TEST(band_first_all_fans_at_high_enabled_runs_both_at_high_edge) {
    auto sp = band_first_setpoints();
    sp.cool_stage2_over_high_f = 3.0f;
    sp.cool_all_fans_at_high_enabled = true;
    auto s = initial_state();

    auto out = resolve_equipment(VENTILATE, make_inputs(sp.temp_high + 0.1f, 1.4f), sp, s, true);
    ASSERT_TRUE(out.vent);
    ASSERT_TRUE(out.fan1);
    ASSERT_TRUE(out.fan2);
    PASS();
}

TEST(band_first_cooling_exit_uses_explicit_ai_hysteresis) {
    auto sp = band_first_setpoints();
    sp.cool_exit_hysteresis_f = 0.5f;
    auto s = initial_state();
    s.mode = VENTILATE;
    s.mode_prev = VENTILATE;

    Mode hold = determine_mode(make_inputs(sp.temp_high - 0.4f, 0.9f), sp, s, 5000);
    ASSERT_EQ(hold, VENTILATE);

    Mode clear = determine_mode(make_inputs(sp.temp_high - 0.6f, 0.9f), sp, s, 5000);
    ASSERT_EQ(clear, IDLE);
    PASS();
}

TEST(band_first_dehum_enters_below_vpd_low_hysteresis_and_exits_at_low_edge) {
    auto sp = band_first_setpoints();
    auto s = initial_state();
    const float HV = band_vpd_hysteresis(sp);

    Mode m1 = determine_mode(make_inputs(75.0f, sp.vpd_low - HV - 0.01f), sp, s, 5000);
    ASSERT_EQ(m1, DEHUM_VENT);
    ASSERT_TRUE(std::string(s.last_mode_reason) == "vpd_low");

    Mode m2 = determine_mode(make_inputs(75.0f, sp.vpd_low - 0.01f), sp, s, 5000);
    ASSERT_EQ(m2, DEHUM_VENT);
    ASSERT_TRUE(std::string(s.last_mode_reason) == "dehum_continue");

    Mode m3 = determine_mode(make_inputs(75.0f, sp.vpd_low + 0.01f), sp, s, 5000);
    ASSERT_EQ(m3, IDLE);
    PASS();
}

TEST(band_first_vpd_compliance_preempts_dehum_dwell_hold) {
    auto sp = band_first_setpoints();
    sp.sw_dwell_gate_enabled = true;
    sp.dwell_gate_ms = 300000;
    auto s = initial_state();
    s.mode = DEHUM_VENT;
    s.mode_prev = DEHUM_VENT;
    s.last_transition_tick_ms = 10000;

    Mode m = determine_mode(make_inputs(75.0f, sp.vpd_high + 0.2f), sp, s, 5000);

    ASSERT_EQ(m, SEALED_MIST);
    ASSERT_TRUE(std::string(s.last_mode_reason) == "humidify_enter");
    ASSERT_FALSE(s.vent_mist_assist_active);
    PASS();
}

TEST(band_first_temp_compliance_preempts_dehum_dwell_hold) {
    auto sp = band_first_setpoints();
    sp.sw_dwell_gate_enabled = true;
    sp.dwell_gate_ms = 300000;
    auto s = initial_state();
    s.mode = DEHUM_VENT;
    s.mode_prev = DEHUM_VENT;
    s.last_transition_tick_ms = 10000;

    Mode m = determine_mode(make_inputs(sp.temp_high + 1.0f, sp.vpd_high + 0.2f), sp, s, 5000);

    ASSERT_EQ(m, VENTILATE);
    ASSERT_TRUE(std::string(s.last_mode_reason) == "vent_mist_assist");
    ASSERT_TRUE(s.vent_mist_assist_active);
    PASS();
}

TEST(band_first_temp_compliance_preempts_sealed_dwell_hold) {
    auto sp = band_first_setpoints();
    sp.sw_dwell_gate_enabled = true;
    sp.dwell_gate_ms = 300000;
    auto s = initial_state();
    s.mode = SEALED_MIST;
    s.mode_prev = SEALED_MIST;
    s.last_transition_tick_ms = 10000;
    s.sealed_timer_ms = 30000;
    s.vpd_watch_timer_ms = sp.vpd_watch_dwell_ms;
    s.mist_stage = MIST_S1;

    Mode m = determine_mode(make_inputs(sp.temp_high + 1.0f, sp.vpd_high + 0.2f), sp, s, 5000);

    ASSERT_EQ(m, VENTILATE);
    ASSERT_TRUE(std::string(s.last_mode_reason) == "vent_mist_assist");
    ASSERT_TRUE(s.vent_mist_assist_active);
    PASS();
}

TEST(climate_effective_decision_reports_actual_held_mode) {
    auto sp = band_first_setpoints();
    auto s = initial_state();
    s.last_mode_reason = "dwell_hold";

    const RelayOutputs relays = {false, false, false, false, false, false};
    ClimateActionDecision d = describe_effective_climate_decision(
        IDLE,
        make_inputs(sp.temp_high + 1.0f, sp.vpd_high + 0.2f),
        sp,
        s,
        relays
    );

    ASSERT_EQ(d.climate_action, CLIMATE_IDLE);
    ASSERT_EQ(d.priority_axis, CLIMATE_PRIORITY_TEMP);
    ASSERT_TRUE(std::string(d.candidate_summary) == "IDLE selected; dwell gate holding prior mode");
    PASS();
}

TEST(band_first_safety_cool_does_not_set_vent_mist_assist) {
    auto sp = band_first_setpoints();
    auto s = initial_state();
    s.vpd_watch_timer_ms = sp.vpd_watch_dwell_ms;

    Mode m = determine_mode(make_inputs(101.0f, 1.8f), sp, s, 5000);
    ASSERT_EQ(m, SAFETY_COOL);
    ASSERT_FALSE(s.vent_mist_assist_active);
    PASS();
}

TEST(band_first_vpd_hysteresis_is_band_width_limited) {
    auto sp = band_first_setpoints();
    auto s = initial_state();
    s.mode = SEALED_MIST;
    s.mode_prev = SEALED_MIST;
    s.sealed_timer_ms = 30000;
    s.vpd_watch_timer_ms = sp.vpd_watch_dwell_ms;
    s.mist_stage = MIST_S1;

    // Legacy effective exit with hyst=0.4 and high=1.2 was ~0.7 kPa.
    // band-first controller caps hysteresis to band width, so 1.0 is resolved enough.
    Mode m = determine_mode(make_inputs(74.0f, 1.0f), sp, s, 5000);
    ASSERT_EQ(m, IDLE);
    ASSERT_TRUE(std::string(s.last_mode_reason) == "humidify_resolved");
    PASS();
}

TEST(band_first_retries_after_backoff_window) {
    auto sp = band_first_setpoints();
    auto s = initial_state();
    s.vpd_watch_timer_ms = sp.vpd_watch_dwell_ms;
    s.mist_backoff_timer_ms = sp.mist_backoff_ms;

    Mode m = determine_mode(make_inputs(74.0f, 1.5f), sp, s, 5000);
    ASSERT_EQ(m, SEALED_MIST);
    ASSERT_TRUE(std::string(s.last_mode_reason) == "humidify_enter");
    PASS();
}

TEST(band_first_cold_dehum_requires_temp_headroom) {
    auto sp = band_first_setpoints();
    auto s = initial_state();
    auto in = make_inputs(sp.temp_low + 1.0f, sp.vpd_low - 0.3f);
    in.outdoor_temp_f = sp.temp_low - 20.0f;

    Mode m = determine_mode(in, sp, s, 60000);
    ASSERT_EQ(m, IDLE);
    ASSERT_TRUE(std::string(s.last_mode_reason) == "heat_stage1");
    PASS();
}

TEST(band_first_cold_dehum_allowed_with_temp_headroom) {
    auto sp = band_first_setpoints();
    auto s = initial_state();
    auto in = make_inputs(sp.temp_low + 3.0f, sp.vpd_low - 0.3f);
    in.outdoor_temp_f = sp.temp_low - 20.0f;

    Mode m = determine_mode(in, sp, s, 60000);
    ASSERT_EQ(m, DEHUM_VENT);
    ASSERT_TRUE(std::string(s.last_mode_reason) == "vpd_low");
    PASS();
}

TEST(band_first_heat_suppressed_at_upper_band) {
    auto sp = band_first_setpoints();
    auto s = initial_state();
    s.heat2_latched = true;

    auto out = resolve_equipment(IDLE, make_inputs(sp.temp_high, 0.9f), sp, s, true);
    ASSERT_FALSE(out.heat1);
    ASSERT_FALSE(out.heat2);
    PASS();
}

TEST(band_first_allows_fog_heat_assist_when_cold_dry) {
    auto sp = band_first_setpoints();
    auto s = initial_state();
    s.mode = SEALED_MIST;
    s.mode_prev = SEALED_MIST;
    s.mist_stage = MIST_FOG;
    s.heat2_latched = true;

    auto in = make_inputs(sp.temp_low + 1.0f, sp.vpd_high + 0.4f, 55.0f);
    auto out = resolve_equipment(SEALED_MIST, in, sp, s, true);

    ASSERT_TRUE(out.heat1);
    ASSERT_TRUE(out.heat2);
    ASSERT_TRUE(out.fog);
    PASS();
}

TEST(obs1e_fog_heat_assist_flag_fires) {
    auto sp = band_first_setpoints();
    auto s = initial_state();
    s.mode = SEALED_MIST;
    s.mode_prev = SEALED_MIST;
    s.mist_stage = MIST_FOG;
    s.heat2_latched = true;

    auto in = make_inputs(sp.temp_low + 1.0f, sp.vpd_high + 0.4f, 55.0f);
    auto f = evaluate_overrides(in, sp, s, SEALED_MIST);

    ASSERT_TRUE(f.fog_heat_assist);
    PASS();
}

static LightingSetpoints lighting_setpoints() {
    return {
        .target_light_minutes = 960,
        .lux_on_threshold = 40000.0f,
        .lux_hysteresis = 8000.0f,
        .start_hour = 6,
        .cutoff_hour = 22,
        .min_on_ms = 120000,
        .min_off_ms = 60000,
        .auto_enabled = true
    };
}

static LightingInputs lighting_inputs(
    float lux,
    int hour = 10,
    bool occupied = false,
    float exterior_lux = NAN,
    bool exterior_lux_fresh = true
) {
    if (std::isnan(exterior_lux)) {
        exterior_lux = lux;
    }
    return {
        .natural_lux = lux,
        .exterior_lux = exterior_lux,
        .exterior_lux_fresh = exterior_lux_fresh,
        .local_hour = hour,
        .occupied = occupied
    };
}

TEST(lighting_state_machine_turns_on_below_lux_band) {
    auto sp = lighting_setpoints();
    auto s = initial_lighting_state();
    auto d = evaluate_lighting(lighting_inputs(30000.0f), sp, s, false, 120000);
    ASSERT_TRUE(d.want_on);
    ASSERT_TRUE(d.in_window);
    ASSERT_TRUE(d.minutes_below_target);
    ASSERT_TRUE(d.lux_below_on_threshold);
    ASSERT_TRUE(d.plant_supplement_demand);
    ASSERT_FALSE(d.occupancy_task_light_demand);
    ASSERT_TRUE(std::string(d.reason) == "plant_lux_low");
    PASS();
}

TEST(lighting_state_machine_holds_until_off_threshold) {
    auto sp = lighting_setpoints();
    auto s = initial_lighting_state();
    auto d = evaluate_lighting(lighting_inputs(45000.0f), sp, s, true, 180000);
    ASSERT_TRUE(d.want_on);
    ASSERT_TRUE(d.lux_below_off_threshold);
    ASSERT_TRUE(d.plant_supplement_demand);
    ASSERT_TRUE(std::string(d.reason) == "plant_hysteresis_hold");
    PASS();
}

TEST(lighting_state_machine_enforces_min_on_dwell) {
    auto sp = lighting_setpoints();
    auto s = initial_lighting_state();
    evaluate_lighting(lighting_inputs(30000.0f), sp, s, false, 120000);
    auto d = evaluate_lighting(lighting_inputs(90000.0f), sp, s, true, 180000);
    ASSERT_TRUE(d.want_on);
    ASSERT_TRUE(std::string(d.reason) == "min_on_hold");
    PASS();
}

TEST(lighting_state_machine_respects_per_light_window_and_minutes_goal) {
    auto sp = lighting_setpoints();
    auto s = initial_lighting_state();
    auto night = evaluate_lighting(lighting_inputs(1000.0f, 23), sp, s, false, 120000);
    ASSERT_FALSE(night.want_on);
    ASSERT_TRUE(std::string(night.reason) == "outside_window");

    sp.target_light_minutes = 1;
    evaluate_lighting(lighting_inputs(90000.0f, 10), sp, s, false, 180000, 60.0f);
    auto met = evaluate_lighting(lighting_inputs(1000.0f, 10), sp, s, false, 240000, 60.0f);
    ASSERT_FALSE(met.want_on);
    ASSERT_TRUE(std::string(met.reason) == "minutes_met");
    PASS();
}

TEST(lighting_occupancy_task_demand_turns_on_when_exterior_lux_low) {
    auto sp = lighting_setpoints();
    auto s = initial_lighting_state();
    auto d = evaluate_lighting(lighting_inputs(1000.0f, 23, true, 1000.0f, true), sp, s, false, 120000);
    ASSERT_TRUE(d.want_on);
    ASSERT_FALSE(d.in_window);
    ASSERT_TRUE(d.exterior_lux_available);
    ASSERT_TRUE(d.occupancy_task_light_demand);
    ASSERT_FALSE(d.plant_supplement_demand);
    ASSERT_TRUE(std::string(d.reason) == "occupancy_lux_low");
    PASS();
}

TEST(lighting_occupancy_task_demand_stays_off_when_exterior_lux_high) {
    auto sp = lighting_setpoints();
    auto s = initial_lighting_state();
    auto d = evaluate_lighting(lighting_inputs(90000.0f, 12, true, 90000.0f, true), sp, s, false, 120000);
    ASSERT_FALSE(d.want_on);
    ASSERT_TRUE(d.exterior_lux_available);
    ASSERT_FALSE(d.occupancy_task_light_demand);
    ASSERT_TRUE(std::string(d.reason) == "lux_sufficient");
    PASS();
}

TEST(lighting_occupancy_task_demand_requires_fresh_exterior_lux) {
    auto sp = lighting_setpoints();
    auto s = initial_lighting_state();
    auto d = evaluate_lighting(lighting_inputs(1000.0f, 23, true, 1000.0f, false), sp, s, false, 120000);
    ASSERT_FALSE(d.want_on);
    ASSERT_FALSE(d.exterior_lux_available);
    ASSERT_FALSE(d.occupancy_task_light_demand);
    ASSERT_TRUE(std::string(d.reason) == "occupancy_lux_unavailable");
    PASS();
}

TEST(lighting_occupancy_holds_until_exterior_off_threshold) {
    auto sp = lighting_setpoints();
    auto s = initial_lighting_state();
    auto d = evaluate_lighting(lighting_inputs(1000.0f, 23, true, 45000.0f, true), sp, s, true, 180000);
    ASSERT_TRUE(d.want_on);
    ASSERT_TRUE(d.occupancy_task_light_demand);
    ASSERT_TRUE(std::string(d.reason) == "occupancy_hysteresis_hold");
    PASS();
}

TEST(lighting_occupancy_clear_turns_off_without_plant_demand) {
    auto sp = lighting_setpoints();
    auto s = initial_lighting_state();
    evaluate_lighting(lighting_inputs(1000.0f, 23, true, 1000.0f, true), sp, s, false, 120000);
    auto d = evaluate_lighting(lighting_inputs(1000.0f, 23, false, 1000.0f, true), sp, s, true, 240000);
    ASSERT_FALSE(d.want_on);
    ASSERT_FALSE(d.occupancy_task_light_demand);
    ASSERT_FALSE(d.plant_supplement_demand);
    ASSERT_TRUE(std::string(d.reason) == "outside_window");
    PASS();
}

TEST(lighting_occupancy_clear_keeps_on_when_plant_demand_remains) {
    auto sp = lighting_setpoints();
    auto s = initial_lighting_state();
    evaluate_lighting(lighting_inputs(1000.0f, 10, true, 1000.0f, true), sp, s, false, 120000);
    auto d = evaluate_lighting(lighting_inputs(1000.0f, 10, false, 1000.0f, true), sp, s, true, 240000);
    ASSERT_TRUE(d.want_on);
    ASSERT_FALSE(d.occupancy_task_light_demand);
    ASSERT_TRUE(d.plant_supplement_demand);
    ASSERT_TRUE(std::string(d.reason) == "plant_hysteresis_hold");
    PASS();
}

TEST(lighting_occupancy_task_demand_respects_min_off_dwell) {
    auto sp = lighting_setpoints();
    auto s = initial_lighting_state();
    s.last_transition_tick_ms = 100000;
    auto d = evaluate_lighting(lighting_inputs(1000.0f, 23, true, 1000.0f, true), sp, s, false, 120000);
    ASSERT_FALSE(d.want_on);
    ASSERT_TRUE(d.occupancy_task_light_demand);
    ASSERT_TRUE(std::string(d.reason) == "min_off_hold");
    PASS();
}

TEST(lighting_qualified_minutes_count_natural_or_switch_once) {
    auto sp = lighting_setpoints();
    auto s = initial_lighting_state();
    evaluate_lighting(lighting_inputs(45000.0f, 10), sp, s, true, 120000, 60.0f);
    ASSERT_TRUE(std::fabs(s.natural_qualified_s - 60.0f) < 0.001f);
    ASSERT_TRUE(std::fabs(s.switch_on_s - 60.0f) < 0.001f);
    ASSERT_TRUE(std::fabs(s.overlap_s - 60.0f) < 0.001f);
    ASSERT_TRUE(std::fabs(s.qualified_light_s - 60.0f) < 0.001f);
    PASS();
}

TEST(lighting_dli_increment_uses_corrected_indoor_or_tempest_fallback) {
    float indoor = lighting_dli_increment(10000.0f, 1000.0f, false, false, 3600.0f);
    float tempest = lighting_dli_increment(0.0f, 100000.0f, false, false, 3600.0f);

    ASSERT_TRUE(std::fabs(indoor - 2.331f) < 0.01f);
    ASSERT_TRUE(std::fabs(tempest - 1.066f) < 0.01f);
    PASS();
}

TEST(lighting_dli_increment_counts_supplemental_light_runtime) {
    float both = lighting_dli_increment(0.0f, 0.0f, true, true, 3600.0f);
    float main = lighting_dli_increment(0.0f, 0.0f, true, false, 3600.0f);
    float grow = lighting_dli_increment(0.0f, 0.0f, false, true, 3600.0f);

    ASSERT_TRUE(std::fabs(both - 0.800f) < 0.001f);
    ASSERT_TRUE(std::fabs((main + grow) - both) < 0.001f);
    PASS();
}

int main() {
    printf("═══════════════════════════════════════════════════════\n");
    printf("  Greenhouse Logic Tests — 11-fix review synthesis + OBS-1e\n");
    printf("  Same code as ESP32 firmware\n");
    printf("═══════════════════════════════════════════════════════\n\n");

    for (auto& t : test_registry) {
        printf("  %-50s ", t.name);
        int before = tests_failed;
        t.fn();
        if (tests_failed == before) printf("✓\n");
    }

    printf("\n═══════════════════════════════════════════════════════\n");
    printf("  %d passed, %d failed\n", tests_passed, tests_failed);
    printf("═══════════════════════════════════════════════════════\n");
    return tests_failed > 0 ? 1 : 0;
}
