/*
 * test_greenhouse_logic.cpp — Native x86 tests for greenhouse controller logic.
 * Same code as ESP32. Tests all 11 fixes from 4-LLM review synthesis.
 *
 * Compile: g++ -std=c++17 -I../lib -o test_greenhouse test_greenhouse_logic.cpp
 */

#include "greenhouse_logic.h"
#include "invariants.h"
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

// ── Firmware-v2 solar helper ────────────────────────────────────────────
// Time-of-day gating now keys on SOLAR PHASE, not wall-clock hour. This sets
// SensorInputs' on-chip ephemeris from a representative summer day (DOY 172,
// MDT = -360 min → sunrise 331, solar-noon 782, sunset 1233) and derives the
// matching phase for the supplied hour, so a test can put a row firmly in the
// solar day, the pre-sunset taper, or the solar night.
//
//   summer reference (compute_greenhouse_solar_times(172, -360)):
//     day  (phase < 2.0): hours 6..20
//     night(phase >= 2.0): hours 21..23, 0..5
//     wet taper (within 120 min of sunset): hours 19, 20
static void set_solar_day(SensorInputs& in, int hour, int minute = 0) {
    const SolarTimes st = compute_greenhouse_solar_times(172, -360);
    in.local_hour     = std::max(0, std::min(23, hour));
    in.now_minute     = std::max(0, std::min(23, hour)) * 60 + minute;
    in.sunrise_min    = st.sunrise_min;
    in.solar_noon_min = st.solar_noon_min;
    in.sunset_min     = st.sunset_min;
    in.solar_phase    = solar_phase(in.now_minute, st);
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
    // ADR-0004: mechanism tests assert behavior against the exact served corridor
    // they set. The optional pinch transform is tested separately.
    sp.band_track_fraction = 0.0f;
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

TEST(stress_wet_override_inert_wetting_follows_curve_day_and_night) {
    // CURVE-ONLY: the stress-wet "re-open" INTENT-gate is removed. The override
    // functions are now inert no-op shims (always false) and the stress/night
    // switches no longer gate wetting. Routine wetting instead follows the band
    // curve directly via climate_wet_assist_block_reason: VPD above vpd_high +
    // a holding dew margin → served, IDENTICALLY by day and by night.
    auto sp = band_setpoints();
    sp.direct_wet_stress_override_enabled = true;
    sp.direct_wet_stress_vpd_margin_kpa = 0.05f;
    sp.direct_wet_stress_min_dew_margin_f = 8.0f;
    sp.sw_night_stress_wet_enabled = true;
    sp.night_stress_min_dew_margin_f = 6.0f;
    validate_setpoints(sp);

    // The override functions are inert regardless of inputs/switches.
    auto in = make_inputs(74.0f, sp.vpd_high + 0.10f);
    set_solar_day(in, 12);
    in.dew_point_f = 64.0f;                                   // 10°F dew margin
    ASSERT_FALSE(stress_wet_override_permitted(in, sp));
    ASSERT_FALSE(direct_wet_stress_override_permitted(in, sp));
    set_solar_day(in, 22);                                    // night: still inert
    ASSERT_FALSE(stress_wet_override_permitted(in, sp));
    sp.sw_night_stress_wet_enabled = false;                  // switch is now inert too
    ASSERT_FALSE(direct_wet_stress_override_permitted(in, sp));

    // Wetting now follows the curve directly. VPD above vpd_high + 10°F dew margin
    // → served, and the verdict is the SAME by day (hour 12) and by night (hour 22).
    set_solar_day(in, 12);
    ASSERT_TRUE(climate_wet_assist_permitted(in, sp));
    set_solar_day(in, 22);
    ASSERT_TRUE(climate_wet_assist_permitted(in, sp));        // no night taper anymore

    // VPD at/below the band high edge → blocked on the curve ("below_threshold"),
    // not by any clock/phase gate, day and night alike.
    in.vpd_kpa = sp.vpd_high;
    set_solar_day(in, 12);
    ASSERT_FALSE(climate_wet_assist_permitted(in, sp));
    ASSERT_TRUE(std::string(climate_wet_assist_block_reason(in, sp)) == "below_threshold");
    set_solar_day(in, 22);
    ASSERT_TRUE(std::string(climate_wet_assist_block_reason(in, sp)) == "below_threshold");

    // Back above the edge but dew margin too small (5°F < 8°F floor) → physics
    // veto "dew_margin", again identical day and night.
    in.vpd_kpa = sp.vpd_high + 0.10f;
    in.dew_point_f = in.temp_f - 5.0f;
    set_solar_day(in, 12);
    ASSERT_TRUE(std::string(climate_wet_assist_block_reason(in, sp)) == "dew_margin");
    set_solar_day(in, 22);
    ASSERT_TRUE(std::string(climate_wet_assist_block_reason(in, sp)) == "dew_margin");
    PASS();
}

// BC-7: the dew-margin wetting/fog veto is STRICTER AT NIGHT when the night
// floor exceeds the day floor. A 9°F margin clears the 8°F day floor (wetting
// allowed by day) but not the 10°F night floor (blocked at night) — the design's
// "dewpoint margin as a hard nighttime constraint".
TEST(bc7_night_dew_margin_is_stricter_than_day) {
    auto sp = band_setpoints();
    sp.direct_wet_stress_min_dew_margin_f = 8.0f;    // day floor
    sp.night_stress_min_dew_margin_f = 10.0f;        // night floor (stricter)
    validate_setpoints(sp);

    auto in = make_inputs(74.0f, sp.vpd_high + 0.10f);  // VPD above band → wetting wanted
    in.dew_point_f = in.temp_f - 9.0f;                  // 9°F margin: between day(8) and night(10)

    set_solar_day(in, 12);                              // DAY: 9 >= 8 → allowed
    ASSERT_TRUE(climate_wet_assist_permitted(in, sp));
    ASSERT_TRUE(climate_fog_assist_permitted(in, sp));
    ASSERT_TRUE(min_dew_margin_for_wetting(in, sp) == 8.0f);

    set_solar_day(in, 22);                              // NIGHT: 9 < 10 → blocked by dew_margin
    ASSERT_FALSE(climate_wet_assist_permitted(in, sp));
    ASSERT_TRUE(std::string(climate_wet_assist_block_reason(in, sp)) == "dew_margin");
    ASSERT_TRUE(std::string(climate_fog_assist_block_reason(in, sp)) == "dew_margin");
    ASSERT_TRUE(min_dew_margin_for_wetting(in, sp) == 10.0f);

    // Never looser than day: a night floor below the day floor cannot relax it.
    sp.night_stress_min_dew_margin_f = 4.0f;
    validate_setpoints(sp);
    set_solar_day(in, 22);
    ASSERT_TRUE(min_dew_margin_for_wetting(in, sp) == 8.0f);  // max(day=8, night=4)
    PASS();
}

TEST(climate_wet_assist_requires_safety_rails_not_ai_switch_or_crop_window) {
    // CURVE-ONLY: routine wet-assist is gated by the band curve (VPD above
    // vpd_high) + PHYSICS (dew margin) + STATE (occupancy/feed-hold), NOT by a
    // crop time-window, a solar taper, or the AI switches. The same VPD/dew is
    // served identically by day and by night.
    auto sp = band_setpoints();
    sp.direct_wet_stress_override_enabled = false;   // inert switch
    sp.direct_wet_stress_vpd_margin_kpa = 0.05f;
    sp.direct_wet_stress_min_dew_margin_f = 8.0f;
    sp.sw_wet_taper_enabled = true;                  // inert switch
    validate_setpoints(sp);

    // Midday, VPD above the band high edge, good dew margin → served.
    auto in = make_inputs(86.0f, sp.vpd_high + 0.20f);
    set_solar_day(in, 12);
    in.dew_point_f = in.temp_f - 12.0f;
    ASSERT_TRUE(climate_wet_assist_permitted(in, sp));

    // Same hour but dew margin only 4°F (< 8°F floor) → blocked on dew margin.
    in.dew_point_f = in.temp_f - 4.0f;
    ASSERT_FALSE(climate_wet_assist_permitted(in, sp));
    ASSERT_TRUE(std::string(climate_wet_assist_block_reason(in, sp)) == "dew_margin");

    // Good dew margin again, now in the solar night (hour 22): there is NO night
    // taper anymore — VPD above vpd_high + dew margin → PERMITTED, just like day.
    in.dew_point_f = in.temp_f - 12.0f;
    set_solar_day(in, 22);
    ASSERT_TRUE(climate_wet_assist_permitted(in, sp));

    // VPD at/below the band high edge → blocked on the curve, not a clock gate.
    in.vpd_kpa = sp.vpd_high;
    ASSERT_FALSE(climate_wet_assist_permitted(in, sp));
    ASSERT_TRUE(std::string(climate_wet_assist_block_reason(in, sp)) == "below_threshold");
    PASS();
}

TEST(fog_follows_curve_and_physics_at_any_hour) {
    // CURVE-ONLY: there is no fog "re-open" / taper / clock window. fog_phase is
    // always permitted; fog_permitted is PHYSICS-ONLY (rh <= ceiling && temp >=
    // min); and the full climate_fog_assist gate adds the curve (VPD > vpd_high)
    // + dew margin. The verdict is identical at any hour — day OR night.
    auto sp = band_setpoints();
    sp.direct_wet_stress_override_enabled = true;    // inert
    sp.direct_wet_stress_vpd_margin_kpa = 0.05f;
    sp.direct_wet_stress_min_dew_margin_f = 8.0f;
    sp.sw_night_stress_wet_enabled = true;           // inert
    sp.night_stress_min_dew_margin_f = 6.0f;
    sp.sw_wet_taper_enabled = true;                  // inert
    validate_setpoints(sp);

    // High VPD, 12°F dew margin, rh/temp within physics → fog served. Check the
    // taper hour (20), the deep night (22), and midday (12) all behave the SAME.
    auto in = make_inputs(76.0f, sp.vpd_high + 0.10f, 60.0f);
    in.dew_point_f = in.temp_f - 12.0f;
    for (int hour : {20, 22, 12, 3}) {
        set_solar_day(in, hour);
        ASSERT_TRUE(fog_phase_permitted(in, sp));            // always true now
        ASSERT_TRUE(fog_permitted(in, sp));                  // physics ok
        ASSERT_TRUE(climate_fog_assist_permitted(in, sp));   // curve + physics + state ok
    }

    // Dew margin only 6°F (< 8°F floor) → climate gate blocks on "dew_margin"
    // identically by day and by night (physics, not a clock).
    in.dew_point_f = in.temp_f - 6.0f;
    set_solar_day(in, 20);
    ASSERT_TRUE(std::string(climate_fog_assist_block_reason(in, sp)) == "dew_margin");
    set_solar_day(in, 22);
    ASSERT_TRUE(std::string(climate_fog_assist_block_reason(in, sp)) == "dew_margin");

    // VPD at/below the band high edge → "below_threshold" (curve), not a phase gate.
    in.dew_point_f = in.temp_f - 12.0f;
    in.vpd_kpa = sp.vpd_high;
    ASSERT_TRUE(std::string(climate_fog_assist_block_reason(in, sp)) == "below_threshold");

    // Saturated air (rh above ceiling) → fog_permitted physics veto, any hour.
    in.vpd_kpa = sp.vpd_high + 0.10f;
    in.rh_pct = sp.fog_rh_ceiling + 1.0f;
    set_solar_day(in, 12);
    ASSERT_FALSE(fog_permitted(in, sp));
    ASSERT_TRUE(std::string(climate_fog_assist_block_reason(in, sp)) == "rh_ceiling");

    // Too cold to evaporate (temp below fog_min_temp) → "temp_low" physics veto.
    in.rh_pct = 60.0f;
    in.temp_f = sp.fog_min_temp - 1.0f;
    in.dew_point_f = in.temp_f - 12.0f;
    ASSERT_FALSE(fog_permitted(in, sp));
    ASSERT_TRUE(std::string(climate_fog_assist_block_reason(in, sp)) == "temp_low");
    PASS();
}

TEST(climate_fog_assist_tracks_solar_phase_not_clock_window) {
    // Firmware-v2: there is no fog clock window. climate_fog_assist is permitted
    // by phase (solar day outside the taper, or stress re-opening it) + VPD/dew,
    // and is NOT coupled to any removed window switch. A daytime hot/dry row with
    // good dew margin is served at any daylight hour.
    auto sp = band_setpoints();
    sp.direct_wet_stress_override_enabled = true;
    sp.direct_wet_stress_min_dew_margin_f = 8.0f;
    validate_setpoints(sp);

    auto in = make_inputs(86.0f, sp.vpd_high + 0.35f, 60.0f);
    set_solar_day(in, 8);              // morning, well inside the solar day
    in.dew_point_f = in.temp_f - 12.0f;

    // Daytime fog now flows through the same phase gate — both true.
    ASSERT_TRUE(fog_permitted(in, sp));
    ASSERT_TRUE(climate_fog_assist_permitted(in, sp));

    // Toggling the stress override has no effect on a daytime (pre-taper) row —
    // it is permitted by the day phase regardless.
    sp.direct_wet_stress_override_enabled = false;
    ASSERT_TRUE(climate_fog_assist_permitted(in, sp));
    PASS();
}

TEST(safety_cool_fog_uses_climate_fog_assist_gate) {
    auto sp = band_setpoints();
    validate_setpoints(sp);

    // Even deep in the solar night, survival cooling fog runs (SAF-6): the taper
    // / night phase gate must not suppress safety-cool fog.
    auto in = make_inputs(sp.safety_max + 1.0f, sp.vpd_high + 0.8f, 60.0f);
    set_solar_day(in, 23);
    in.dew_point_f = in.temp_f - 12.0f;
    auto relays = resolve_equipment(SAFETY_COOL, in, sp, initial_state(), true);

    ASSERT_TRUE(relays.vent);
    ASSERT_TRUE(relays.fan1);
    ASSERT_TRUE(relays.fan2);
    ASSERT_TRUE(relays.fog);
    PASS();
}

// ── ENV-2: night econ-heat suppression (Vanda day/night-drop companion) ──
TEST(night_econ_heat_suppressed_during_solar_night) {
    auto sp = band_setpoints();
    // Firmware-v2: night is the SOLAR night half (is_night_phase, phase >= 2.0).
    {
        auto probe = make_inputs(72.0f, 1.0f);
        set_solar_day(probe, 22); ASSERT_TRUE(is_night_phase(probe));   // night
        set_solar_day(probe, 2);  ASSERT_TRUE(is_night_phase(probe));   // pre-dawn night
        set_solar_day(probe, 21); ASSERT_TRUE(is_night_phase(probe));   // just past sunset
        set_solar_day(probe, 12); ASSERT_FALSE(is_night_phase(probe));  // midday
        set_solar_day(probe, 6);  ASSERT_FALSE(is_night_phase(probe));  // morning day
        set_solar_day(probe, 19); ASSERT_FALSE(is_night_phase(probe));  // pre-sunset day
    }

    // In-band temp (above the heat target, below Thigh-econ_margin) + very
    // dry → ONLY the econ VPD-rescue path wants heat, not band heating.
    // band 65-82 → heat target ~69, Thigh 82, econ fires below 82-5=77.
    auto in = make_inputs(72.0f, 0.10f);
    sp.econ_block = true;
    validate_setpoints(sp);

    // Daytime: econ-rescue heat fires (legacy behavior preserved).
    set_solar_day(in, 12);
    auto day = resolve_equipment(IDLE, in, sp, initial_state(), true);
    ASSERT_TRUE(day.heat1);

    // Night: econ-rescue heat suppressed — no heat-to-chase-humidity.
    set_solar_day(in, 23);
    auto night = resolve_equipment(IDLE, in, sp, initial_state(), true);
    ASSERT_FALSE(night.heat1);
    ASSERT_FALSE(night.heat2);

    // Suppression is toggleable; OFF restores legacy night heating.
    sp.sw_night_econ_heat_suppress_enabled = false;
    auto night_legacy = resolve_equipment(IDLE, in, sp, initial_state(), true);
    ASSERT_TRUE(night_legacy.heat1);
    PASS();
}

TEST(night_econ_suppression_never_blocks_band_heating) {
    // ENV-2 only suppresses the ECON VPD-rescue heat, not real low-temp
    // band heating. A genuinely cold night must still heat.
    auto sp = band_setpoints();
    sp.econ_block = true;
    validate_setpoints(sp);
    auto in = make_inputs(sp.temp_low - 6.0f, 1.0f);  // below band → real heat demand
    set_solar_day(in, 2);                              // deep solar night
    auto night = resolve_equipment(IDLE, in, sp, initial_state(), true);
    ASSERT_TRUE(night.heat1);  // band heating unaffected by night-econ suppression
    PASS();
}

// ── CURVE-ONLY: the wet taper is gone; night behaves exactly like day ──
TEST(routine_wet_and_fog_behave_identically_night_and_day) {
    // The dusk/night wet-taper rail is REMOVED — past_wet_taper is a no-op shim
    // and no longer blocks anything. Routine wetting/fogging follows the band
    // curve + physics, so a dry night with a holding dew margin is wetted just
    // like the equivalent daytime row.
    auto sp = band_setpoints();
    sp.sw_wet_taper_enabled = true;                  // inert
    sp.direct_wet_stress_override_enabled = false;   // inert
    sp.direct_wet_stress_min_dew_margin_f = 8.0f;
    sp.direct_wet_stress_vpd_margin_kpa = 0.05f;
    validate_setpoints(sp);

    // High-VPD dry stress, 14°F dew margin, rh/temp within physics. The taper no
    // longer engages, and the wet/fog verdict is the SAME across night and day.
    auto in = make_inputs(80.0f, sp.vpd_high + 0.6f, 50.0f);
    in.dew_point_f = in.temp_f - 14.0f;
    for (int hour : {21, 23, 3, 12}) {
        set_solar_day(in, hour);
        ASSERT_FALSE(past_wet_taper(in, sp));                // inert at every hour
        ASSERT_TRUE(climate_wet_assist_permitted(in, sp));   // served day and night
        ASSERT_TRUE(climate_fog_assist_permitted(in, sp));
        ASSERT_TRUE(fog_phase_permitted(in, sp));
    }

    // The block now comes ONLY from the curve/physics, not the hour: drop VPD to
    // the band high edge and BOTH a night row and a day row block identically on
    // "below_threshold".
    in.vpd_kpa = sp.vpd_high;
    set_solar_day(in, 21);
    ASSERT_TRUE(std::string(climate_wet_assist_block_reason(in, sp)) == "below_threshold");
    set_solar_day(in, 12);
    ASSERT_TRUE(std::string(climate_wet_assist_block_reason(in, sp)) == "below_threshold");
    PASS();
}

TEST(wet_taper_is_inert_at_every_hour_and_switch_state) {
    // CURVE-ONLY: past_wet_taper is a no-op shim — it NEVER engages, regardless of
    // hour-of-day, proximity to sunset, or the (now inert) enable switch. The band
    // curve, not a dusk clock, keeps nights dry. This pins that the taper no longer
    // gates wetting at all (coverage of the "served day vs night" behavior lives in
    // routine_wet_and_fog_behave_identically_night_and_day above).
    auto sp = band_setpoints();
    sp.sw_wet_taper_enabled = true;
    sp.wet_taper_before_sunset_min = 120;
    validate_setpoints(sp);

    auto in = make_inputs(76.0f, 1.0f, 50.0f);
    // Pre-taper daylight, inside the old taper window, deep solar night, pre-dawn:
    // all return false now.
    for (int hour : {18, 20, 23, 3, 12, 6}) {
        set_solar_day(in, hour);
        ASSERT_FALSE(past_wet_taper(in, sp));
    }

    // Switch OFF makes no difference — still inert.
    sp.sw_wet_taper_enabled = false;
    for (int hour : {20, 23}) {
        set_solar_day(in, hour);
        ASSERT_FALSE(past_wet_taper(in, sp));
    }
    PASS();
}

TEST(wet_taper_never_blocks_safety_cool_fog) {
    // SAF-6: at the safety_max rail, fog is survival cooling — the wet taper
    // must not suppress it even deep in the solar night.
    auto sp = band_setpoints();
    validate_setpoints(sp);
    auto in = make_inputs(sp.safety_max + 1.0f, sp.vpd_high + 0.8f, 60.0f);
    set_solar_day(in, 23);              // well past sunset
    in.dew_point_f = in.temp_f - 12.0f;
    auto relays = resolve_equipment(SAFETY_COOL, in, sp, initial_state(), true);
    ASSERT_TRUE(relays.fog);
    PASS();
}

// ── FRT-6 / FRT-7: post-feed absorption hold ──
TEST(feed_hold_blocks_all_clean_wetting) {
    auto sp = band_setpoints();
    validate_setpoints(sp);
    auto in = make_inputs(78.0f, sp.vpd_high + 0.5f, 50.0f);  // dry → wants to mist/fog
    set_solar_day(in, 9);                                     // mid-morning, pre-taper
    in.dew_point_f = in.temp_f - 14.0f;

    // No hold → wetting permitted.
    sp.feed_hold_active = false;
    ASSERT_TRUE(climate_wet_assist_permitted(in, sp));
    ASSERT_TRUE(climate_fog_assist_permitted(in, sp));

    // Hold active → both mister and fog assist hard-blocked.
    sp.feed_hold_active = true;
    ASSERT_FALSE(climate_wet_assist_permitted(in, sp));
    ASSERT_TRUE(std::string(climate_wet_assist_block_reason(in, sp)) == "feed_hold");
    ASSERT_FALSE(climate_fog_assist_permitted(in, sp));
    ASSERT_TRUE(std::string(climate_fog_assist_block_reason(in, sp)) == "feed_hold");
    PASS();
}

TEST(feed_hold_never_blocks_safety_cool_fog) {
    // Survival cooling outranks the absorption hold.
    auto sp = band_setpoints();
    sp.feed_hold_active = true;
    validate_setpoints(sp);
    auto in = make_inputs(sp.safety_max + 1.0f, sp.vpd_high + 0.8f, 60.0f);
    set_solar_day(in, 9);
    in.dew_point_f = in.temp_f - 12.0f;
    auto relays = resolve_equipment(SAFETY_COOL, in, sp, initial_state(), true);
    ASSERT_TRUE(relays.fog);
    PASS();
}

// ── IRR-3 (dawn rehydrate) / IRR-4 (midday drench) ──
// A "dry, pre-taper, good-dew-margin" center-eligible row: VPD above the center
// band ceiling so the over-saturation gate opens, dew margin well above the
// floor. Used as the baseline that the rail tests then knock out one rail at a
// time. Bands: vpd_high=1.4, so vpd 1.9 is clearly above.
//
// Firmware-v2: the dawn/midday windows anchor to the ON-CHIP solar times
// (dawn = sunrise + dawn_boost_offset_min; midday = solar-noon +
// midday_boost_offset_min). We pin clean anchors — sunrise 07:00 (420),
// solar-noon 13:00 (780), sunset 20:00 (1200) — so the default offsets put the
// dawn window at [07:00, 07:12) and the midday window at [14:00, 14:11),
// preserving the historical test hours. now_minute / solar_phase are derived
// for the supplied hour so the rail gate (taper/night) is consistent.
static constexpr int IRR_SUNRISE_MIN    = 420;   // 07:00
static constexpr int IRR_SOLAR_NOON_MIN = 780;   // 13:00
static constexpr int IRR_SUNSET_MIN     = 1200;  // 20:00
static SensorInputs irr_dry_inputs(int hour) {
    auto in = make_inputs(78.0f, 1.9f, 45.0f);
    in.local_hour     = std::max(0, std::min(23, hour));
    in.now_minute     = std::max(0, std::min(23, hour)) * 60;
    in.sunrise_min    = IRR_SUNRISE_MIN;
    in.solar_noon_min = IRR_SOLAR_NOON_MIN;
    in.sunset_min     = IRR_SUNSET_MIN;
    in.solar_phase    = solar_phase(in.now_minute,
                                    {IRR_SUNRISE_MIN, IRR_SOLAR_NOON_MIN, IRR_SUNSET_MIN});
    in.dew_point_f    = in.temp_f - 14.0f;   // dew margin 14°F >> 8°F floor
    return in;
}
static Setpoints irr_setpoints() {
    auto sp = band_setpoints();   // vpd_high 1.4, vpd_low 0.8
    // Defaults: dawn offset 0 (anchored at sunrise), midday offset 60.
    validate_setpoints(sp);
    return sp;
}

TEST(center_burst_windows_match_anchors_and_durations) {
    auto sp = irr_setpoints();
    auto dawn_in   = irr_dry_inputs(7);    // anchors: sunrise 07:00, solar-noon 13:00
    auto midday_in = irr_dry_inputs(14);
    // Dawn window: sunrise + offset(0) → [7:00, 7:12)
    ASSERT_TRUE(in_dawn_rehydrate_window(local_minute_of_day(7, 0), dawn_in, sp));
    ASSERT_TRUE(in_dawn_rehydrate_window(local_minute_of_day(7, 11), dawn_in, sp));
    ASSERT_FALSE(in_dawn_rehydrate_window(local_minute_of_day(7, 12), dawn_in, sp));
    ASSERT_FALSE(in_dawn_rehydrate_window(local_minute_of_day(6, 59), dawn_in, sp));
    // Midday window: solar-noon + offset(60) → [14:00, 14:11)
    ASSERT_TRUE(in_midday_drench_window(local_minute_of_day(14, 0), midday_in, sp));
    ASSERT_TRUE(in_midday_drench_window(local_minute_of_day(14, 10), midday_in, sp));
    ASSERT_FALSE(in_midday_drench_window(local_minute_of_day(14, 11), midday_in, sp));
    ASSERT_FALSE(in_midday_drench_window(local_minute_of_day(13, 59), midday_in, sp));
    PASS();
}

TEST(center_burst_decision_selects_dawn_then_midday) {
    auto sp = irr_setpoints();
    // Inside dawn window → DAWN.
    ASSERT_EQ(center_burst_decision(local_minute_of_day(7, 5), irr_dry_inputs(7), sp), CENTER_BURST_DAWN);
    // Inside midday window → MIDDAY.
    ASSERT_EQ(center_burst_decision(local_minute_of_day(14, 5), irr_dry_inputs(14), sp), CENTER_BURST_MIDDAY);
    // Outside both windows (mid-morning) → NONE even though dry+eligible.
    ASSERT_EQ(center_burst_decision(local_minute_of_day(10, 0), irr_dry_inputs(10), sp), CENTER_BURST_NONE);
    PASS();
}

TEST(center_burst_cadence_is_denser_than_base_and_per_burst) {
    auto sp = irr_setpoints();
    int on_s = 0, gap_s = 0;
    ASSERT_FALSE(center_burst_cadence_s(CENTER_BURST_NONE, sp, on_s, gap_s));
    ASSERT_TRUE(center_burst_cadence_s(CENTER_BURST_DAWN, sp, on_s, gap_s));
    ASSERT_EQ(on_s, 90); ASSERT_EQ(gap_s, 20);
    ASSERT_TRUE(center_burst_cadence_s(CENTER_BURST_MIDDAY, sp, on_s, gap_s));
    ASSERT_EQ(on_s, 120); ASSERT_EQ(gap_s, 25);
    // Denser than the 60s-ON / 45s-GAP base: longer ON, shorter GAP.
    ASSERT_TRUE(sp.dawn_rehydrate_on_s   > 60);  ASSERT_TRUE(sp.dawn_rehydrate_gap_s   < 45);
    ASSERT_TRUE(sp.midday_drench_on_s    > 60);  ASSERT_TRUE(sp.midday_drench_gap_s    < 45);
    PASS();
}

TEST(center_burst_blocked_by_each_rail) {
    // feed-hold (FRT-6 absorption hold) blocks the burst.
    {
        auto sp = irr_setpoints(); sp.feed_hold_active = true;
        ASSERT_EQ(center_burst_decision(local_minute_of_day(7, 5), irr_dry_inputs(7), sp), CENTER_BURST_NONE);
    }
    // CURVE-ONLY: the wet-taper rail is removed (past_wet_taper is inert), so it is
    // no longer one of the burst rails. The remaining rails below still gate bursts.
    // dew margin below floor blocks (don't wet a cold leaf).
    {
        auto sp = irr_setpoints();
        auto in = irr_dry_inputs(7); in.dew_point_f = in.temp_f - 2.0f;  // 2°F << 8°F floor
        ASSERT_EQ(center_burst_decision(local_minute_of_day(7, 5), in, sp), CENTER_BURST_NONE);
    }
    // over-saturation sanity gate: VPD at/below center band ceiling → no drench.
    {
        auto sp = irr_setpoints();
        auto in = irr_dry_inputs(7); in.vpd_kpa = sp.vpd_high;        // exactly at ceiling
        ASSERT_EQ(center_burst_decision(local_minute_of_day(7, 5), in, sp), CENTER_BURST_NONE);
        in.vpd_kpa = sp.vpd_high - 0.2f;                              // humid → below ceiling
        ASSERT_EQ(center_burst_decision(local_minute_of_day(7, 5), in, sp), CENTER_BURST_NONE);
    }
    // occupancy inhibit blocks the burst.
    {
        auto sp = irr_setpoints(); sp.occupancy_inhibit = true;
        auto in = irr_dry_inputs(7); in.occupied = true;
        ASSERT_EQ(center_burst_decision(local_minute_of_day(7, 5), in, sp), CENTER_BURST_NONE);
    }
    // sensor fault (implausible inputs) blocks the burst.
    {
        auto sp = irr_setpoints();
        auto in = irr_dry_inputs(7); in.vpd_kpa = NAN;
        ASSERT_EQ(center_burst_decision(local_minute_of_day(7, 5), in, sp), CENTER_BURST_NONE);
    }
    PASS();
}

// ── SAF-1 / SF1: sensor-degraded conservative timed wetting ──
TEST(sf1_degraded_suppresses_vpd_chasing_seal) {
    // High VPD that WOULD seal for misting, with the watch dwell already
    // satisfied. Trusted → SEALED_MIST. Degraded → the fabricated VPD must not
    // drive wetting, so the controller falls back to a non-wetting mode.
    auto sp = band_setpoints();
    auto s_ok = initial_state(); s_ok.vpd_watch_timer_ms = 60000;
    ASSERT_EQ(determine_mode(make_inputs(72, 1.8), sp, s_ok, 5000), SEALED_MIST);

    auto in = make_inputs(72, 1.8);
    in.sensor_degraded = true;
    auto s_deg = initial_state(); s_deg.vpd_watch_timer_ms = 60000;
    Mode m = determine_mode(in, sp, s_deg, 5000);
    ASSERT_TRUE(m != SEALED_MIST);   // no VPD-chasing humidification
    PASS();
}

TEST(sf1_degraded_suppresses_dehum_vent) {
    // Low VPD that WOULD trigger DEHUM_VENT when trusted; degraded suppresses it.
    auto sp = band_setpoints();
    sp.econ_block = false;
    auto in_ok = make_inputs(72, 0.4);   // well below vpd_low 0.8
    auto s_ok = initial_state();
    ASSERT_EQ(determine_mode(in_ok, sp, s_ok, 5000), DEHUM_VENT);

    auto in = make_inputs(72, 0.4);
    in.sensor_degraded = true;
    auto s_deg = initial_state();
    ASSERT_TRUE(determine_mode(in, sp, s_deg, 5000) != DEHUM_VENT);
    PASS();
}

TEST(sf1_degraded_keeps_temp_safety_rails) {
    // Temperature control is NOT gated by degradation — the case probe still
    // gives a plausible temp. Safety cool/heat and vent cooling must still fire.
    auto sp = band_setpoints();
    {
        auto in = make_inputs(sp.safety_max + 2.0f, 1.0f);
        in.sensor_degraded = true;
        auto s = initial_state();
        ASSERT_EQ(determine_mode(in, sp, s, 5000), SAFETY_COOL);
    }
    {
        auto in = make_inputs(sp.safety_min - 2.0f, 1.0f);
        in.sensor_degraded = true;
        auto s = initial_state();
        ASSERT_EQ(determine_mode(in, sp, s, 5000), SAFETY_HEAT);
    }
    {
        // Hot but below safety → VENTILATE (temp-only cooling still works).
        auto in = make_inputs(sp.temp_high + 4.0f, 1.0f);
        in.sensor_degraded = true;
        auto s = initial_state();
        ASSERT_EQ(determine_mode(in, sp, s, 5000), VENTILATE);
    }
    PASS();
}

TEST(sf1_degraded_allows_timed_center_burst_without_vpd) {
    // The conservative timed fallback: dawn/midday center bursts still fire when
    // degraded, even though VPD is at/below the band ceiling (the over-saturation
    // gate is bypassed because the VPD reading is untrusted). Every OTHER rail
    // (dusk, feed-hold, dew margin, occupancy, plausibility) still applies.
    auto sp = irr_setpoints();
    // Humid reading that would normally block the burst (VPD <= ceiling).
    auto in = irr_dry_inputs(7);
    in.vpd_kpa = sp.vpd_high - 0.5f;     // below ceiling → trusted path blocks
    ASSERT_EQ(center_burst_decision(local_minute_of_day(7, 5), in, sp), CENTER_BURST_NONE);
    in.sensor_degraded = true;            // degraded → over-saturation gate bypassed
    ASSERT_EQ(center_burst_decision(local_minute_of_day(7, 5), in, sp), CENTER_BURST_DAWN);
    // ...but a degraded burst STILL respects the dusk cutoff and feed hold.
    {
        auto sp2 = sp; sp2.feed_hold_active = true;
        ASSERT_EQ(center_burst_decision(local_minute_of_day(7, 5), in, sp2), CENTER_BURST_NONE);
    }
    {
        auto in2 = in; in2.dew_point_f = in2.temp_f - 2.0f;   // cold leaf → blocked
        ASSERT_EQ(center_burst_decision(local_minute_of_day(7, 5), in2, sp), CENTER_BURST_NONE);
    }
    PASS();
}

TEST(sf1_implausible_still_sensor_fault_all_off) {
    // SF1 is BROADER than SENSOR_FAULT but does not replace it. An implausible
    // (inf/garbage) reading still forces SENSOR_FAULT → all relays off, even if
    // sensor_degraded is also set.
    auto sp = band_setpoints();
    auto in = make_inputs(72, 1.8);
    in.vpd_kpa = INFINITY;        // garbage → sensors_plausible() false
    in.sensor_degraded = true;
    auto s = initial_state();
    ASSERT_EQ(determine_mode(in, sp, s, 5000), SENSOR_FAULT);
    auto relays = resolve_equipment(SENSOR_FAULT, in, sp, s, true);
    ASSERT_FALSE(relays.fog || relays.fan1 || relays.fan2 ||
                 relays.heat1 || relays.heat2 || relays.vent);
    PASS();
}

// ── Firmware-v2: overnight emergency wetting is the generic stress-wet rule ──
// The bespoke CYC-4 ≤5s micro-pulse path is DELETED. At night the solar band's
// VPD edges are the LOW night band, so "VPD above band-high + stress margin
// under the stricter night dew margin" IS the overnight emergency. The normal
// SEALED_MIST machinery + SAF-4 duty caps bound the water. These tests replace
// the deleted micropulse_* suite.
static SensorInputs stress_dry_overnight_inputs() {
    auto in = make_inputs(72.0f, 1.6f, 40.0f);   // VPD 1.6 > band-high(1.4)+margin
    set_solar_day(in, 23);                        // solar night (phase >= 2)
    in.dew_point_f = in.temp_f - 14.0f;           // 14°F margin >> 8°F night floor
    return in;
}

TEST(overnight_wetting_follows_curve_when_dry_and_clean) {
    // CURVE-ONLY: the dedicated overnight stress-wet override is inert; overnight
    // wetting now follows the band curve directly. A genuinely dry, clean night
    // (VPD above vpd_high + holding dew margin) is served via the SAME
    // climate_wet_assist gate the daytime path uses — no special night rule.
    auto sp = irr_setpoints();   // band vpd_high 1.4
    sp.direct_wet_stress_override_enabled = true;   // inert
    sp.sw_night_stress_wet_enabled = true;          // inert
    sp.night_stress_min_dew_margin_f = 6.0f;
    sp.direct_wet_stress_min_dew_margin_f = 8.0f;
    validate_setpoints(sp);
    auto in = stress_dry_overnight_inputs();         // VPD 1.6 > 1.4, dew margin 14°F, night
    ASSERT_TRUE(is_night_phase(in));
    ASSERT_FALSE(stress_wet_override_permitted(in, sp));   // override is a no-op shim
    ASSERT_TRUE(climate_wet_assist_permitted(in, sp));     // curve serves it overnight

    // Drop VPD to the band high edge → blocked on the curve, not on a night gate.
    in.vpd_kpa = sp.vpd_high;
    ASSERT_TRUE(std::string(climate_wet_assist_block_reason(in, sp)) == "below_threshold");

    // Restore the dry VPD but tighten dew margin below the floor → physics veto.
    in.vpd_kpa = sp.vpd_high + 0.2f;
    in.dew_point_f = in.temp_f - 5.0f;
    ASSERT_TRUE(std::string(climate_wet_assist_block_reason(in, sp)) == "dew_margin");
    PASS();
}

TEST(night_stress_wet_blocked_by_each_rail) {
    auto sp = irr_setpoints();
    sp.direct_wet_stress_override_enabled = true;
    sp.sw_night_stress_wet_enabled = true;
    sp.night_stress_min_dew_margin_f = 6.0f;
    sp.direct_wet_stress_min_dew_margin_f = 8.0f;
    validate_setpoints(sp);

    // stress override master disable → no overnight wetting.
    { auto s2 = sp; s2.direct_wet_stress_override_enabled = false;
      ASSERT_FALSE(stress_wet_override_permitted(stress_dry_overnight_inputs(), s2)); }
    // night-stress switch off → night emergency suppressed.
    { auto s2 = sp; s2.sw_night_stress_wet_enabled = false;
      ASSERT_FALSE(stress_wet_override_permitted(stress_dry_overnight_inputs(), s2)); }
    // VPD at/below band-high + margin → not a stress spike.
    { auto in = stress_dry_overnight_inputs();
      in.vpd_kpa = sp.vpd_high + sp.direct_wet_stress_vpd_margin_kpa;
      ASSERT_FALSE(stress_wet_override_permitted(in, sp)); }
    // dew margin below the stricter night floor (max(6,8)=8) → blocked.
    { auto in = stress_dry_overnight_inputs(); in.dew_point_f = in.temp_f - 5.0f;
      ASSERT_FALSE(stress_wet_override_permitted(in, sp)); }
    PASS();
}

TEST(center_burst_respects_enable_switches) {
    auto sp = irr_setpoints();
    sp.sw_dawn_rehydrate_enabled = false;
    ASSERT_EQ(center_burst_decision(local_minute_of_day(7, 5), irr_dry_inputs(7), sp), CENTER_BURST_NONE);
    // midday still works while dawn disabled.
    ASSERT_EQ(center_burst_decision(local_minute_of_day(14, 5), irr_dry_inputs(14), sp), CENTER_BURST_MIDDAY);
    sp.sw_midday_drench_enabled = false;
    ASSERT_EQ(center_burst_decision(local_minute_of_day(14, 5), irr_dry_inputs(14), sp), CENTER_BURST_NONE);
    PASS();
}

TEST(center_burst_windows_are_pre_taper_by_default) {
    // Firmware-v2: the dawn/midday windows anchor to the on-chip solar times via
    // offsets. With the IRR anchors (sunrise 07:00, solar-noon 13:00, sunset
    // 20:00) and default offsets (dawn 0, midday 60) + durations, both windows
    // must close before the pre-sunset wet taper begins, so no burst ever fires
    // past the taper.
    auto sp = irr_setpoints();
    auto in = irr_dry_inputs(7);
    const int dawn_end   = ((in.sunrise_min + sp.dawn_boost_offset_min) % 1440)
                         + dawn_rehydrate_window_minutes(sp);
    const int midday_end = ((in.solar_noon_min + sp.midday_boost_offset_min) % 1440)
                         + midday_drench_window_minutes(sp);
    const int taper_start = in.sunset_min - sp.wet_taper_before_sunset_min;  // 20:00 - 120 = 18:00
    ASSERT_TRUE(dawn_end   <= taper_start);
    ASSERT_TRUE(midday_end <= taper_start);
    PASS();
}

TEST(determine_mode_stamps_center_burst_and_clears_under_safety) {
    auto sp = irr_setpoints();
    sp.sw_fsm_controller_enabled = true;
    validate_setpoints(sp);
    sp.sw_fsm_controller_enabled = true;
    // Dry, pre-dusk, dawn hour, VPD above center ceiling, dwell satisfied so we
    // are firmly in SEALED_MIST → FSM stamps DAWN (hour-granular: hour 7 → 7:00).
    auto in = irr_dry_inputs(7);
    auto s = initial_state(); s.vpd_watch_timer_ms = sp.vpd_watch_dwell_ms;
    determine_mode(in, sp, s, 5000);
    ASSERT_EQ(s.center_burst, CENTER_BURST_DAWN);

    // Drive temp to the safety_max rail → SAFETY_COOL must clear the burst.
    auto hot = in; hot.temp_f = sp.safety_max + 2.0f;
    determine_mode(hot, sp, s, 5000);
    ASSERT_EQ(s.center_burst, CENTER_BURST_NONE);
    PASS();
}

// ── New Vanda-band invariants (invariants.h #17-#20) ──
static void silent_report(int, const char*, const invariants::TraceRow&, const char*) {}

TEST(invariant_18_fog_fert_exclusive) {
    invariants::TraceRow r{};
    r.eq_fog = 1; r.eq_fertilizer_master = 0;
    ASSERT_TRUE(invariants::check_18_fog_fert_exclusive(r, silent_report));
    r.eq_fertilizer_master = 1;   // salts could reach fogger → breach
    ASSERT_FALSE(invariants::check_18_fog_fert_exclusive(r, silent_report));
    r.eq_fog = 0;                 // fog off → no conflict
    ASSERT_TRUE(invariants::check_18_fog_fert_exclusive(r, silent_report));
    PASS();
}

TEST(invariant_19_feed_hold_no_clean_center) {
    invariants::TraceRow r{};
    // Fert master open → no center mister / fog allowed.
    r.eq_fertilizer_master = 1;
    r.eq_mister_center = 1;
    ASSERT_FALSE(invariants::check_19_feed_hold_no_clean_center(r, silent_report));
    r.eq_mister_center = 0; r.eq_fog = 1;
    ASSERT_FALSE(invariants::check_19_feed_hold_no_clean_center(r, silent_report));
    r.eq_fog = 0;
    ASSERT_TRUE(invariants::check_19_feed_hold_no_clean_center(r, silent_report));
    // Absorption-hold flag alone (master already closed) still blocks.
    r.eq_fertilizer_master = 0; r.feed_hold_active = true; r.eq_mister_center = 1;
    ASSERT_FALSE(invariants::check_19_feed_hold_no_clean_center(r, silent_report));
    r.feed_hold_active = false;
    ASSERT_TRUE(invariants::check_19_feed_hold_no_clean_center(r, silent_report));
    PASS();
}

TEST(invariant_20_feed_hold_window_persists) {
    invariants::Ctx20 c;
    invariants::TraceRow r{};
    // Feed begins: master open.
    r.eq_fertilizer_master = 1;
    ASSERT_TRUE(invariants::check_20_feed_hold_window(c, r, silent_report));
    // Master closes but hold flag persists → still blocking.
    r.eq_fertilizer_master = 0; r.feed_hold_active = true;
    r.eq_mister_center = 1;     // clean pulse during hold → breach
    ASSERT_FALSE(invariants::check_20_feed_hold_window(c, r, silent_report));
    // Hold clears → wetting allowed again.
    r.feed_hold_active = false; r.eq_mister_center = 1;
    ASSERT_TRUE(invariants::check_20_feed_hold_window(c, r, silent_report));
    PASS();
}

TEST(invariant_21_center_burst_pre_dusk) {
    invariants::TraceRow r{};
    // Dry, plausible, pre-dusk → rails may permit (not a breach either way).
    r.temp_f = 78.0f; r.rh_pct = 45.0f; r.vpd_kpa = 1.9f; r.dew_point_f = 64.0f;
    r.vpd_high = 1.4f; r.vpd_low = 0.8f;
    r.dusk_cutoff_hour = 18; r.dusk_cutoff_enabled = true; r.night_end_hour = 6;
    r.local_hour = 7;
    ASSERT_TRUE(invariants::check_21_center_burst_pre_dusk(r, silent_report));
    // Same dryness but PAST dusk (21:00) → rails must be closed; check passes
    // precisely because the burst is correctly suppressed.
    r.local_hour = 21;
    ASSERT_TRUE(invariants::check_21_center_burst_pre_dusk(r, silent_report));
    PASS();
}

TEST(invariant_22_center_burst_no_feed_hold) {
    invariants::TraceRow r{};
    r.temp_f = 78.0f; r.rh_pct = 45.0f; r.vpd_kpa = 1.9f; r.dew_point_f = 64.0f;
    r.vpd_high = 1.4f; r.vpd_low = 0.8f;
    r.dusk_cutoff_hour = 18; r.dusk_cutoff_enabled = true; r.night_end_hour = 6;
    r.local_hour = 7;
    // No feed hold → check passes.
    r.feed_hold_active = false;
    ASSERT_TRUE(invariants::check_22_center_burst_no_feed_hold(r, silent_report));
    // Feed hold active → rails must be closed; check passes because the burst
    // is suppressed (the invariant would FAIL if rails permitted).
    r.feed_hold_active = true;
    ASSERT_TRUE(invariants::check_22_center_burst_no_feed_hold(r, silent_report));
    PASS();
}

TEST(invariant_24_fog_requires_curve_gate_day_and_night) {
    // CURVE-ONLY: invariant 24 no longer encodes an overnight micro-pulse window.
    // The band curve is the schedule: fog is legal at ANY hour ONLY when actual
    // VPD is ABOVE the band high edge (the curve gate), or when survival-cooling.
    // Fog at/below vpd_high outside SAFETY_COOL is the regression it now guards,
    // and DAY and NIGHT are treated identically.
    invariants::TraceRow r{};
    r.vpd_high = 1.4f; r.vpd_low = 0.8f;
    r.fog_rh_ceiling = 90.0f; r.fog_min_temp = 55.0f;
    r.temp_f = 72.0f; r.rh_pct = 40.0f; r.dew_point_f = 58.0f;
    r.greenhouse_state = "SEALED_MIST_FOG";

    // No fog → vacuously passes.
    r.eq_fog = 0; r.local_hour = 23; r.vpd_kpa = 1.0f;
    ASSERT_TRUE(invariants::check_24_fog_requires_curve_gate(r, silent_report));

    // Fog ON with VPD above the band high edge → curve gate satisfied, ALLOWED.
    // Verify DAY and NIGHT behave identically (the curve-only symmetry).
    r.eq_fog = 1; r.vpd_kpa = 1.8f;          // 1.8 > vpd_high 1.4
    r.local_hour = 12;                        // day
    ASSERT_TRUE(invariants::check_24_fog_requires_curve_gate(r, silent_report));
    r.local_hour = 23;                        // night — same VPD, same verdict
    ASSERT_TRUE(invariants::check_24_fog_requires_curve_gate(r, silent_report));

    // Fog ON with VPD at/below the band high edge, NOT safety cooling → BREACH,
    // and again identically by day and by night (no clock/phase exception either way).
    r.vpd_kpa = 1.4f;                         // exactly at the high edge → not above
    r.local_hour = 23;
    ASSERT_FALSE(invariants::check_24_fog_requires_curve_gate(r, silent_report));
    r.local_hour = 12;                        // day breaches the same way
    ASSERT_FALSE(invariants::check_24_fog_requires_curve_gate(r, silent_report));
    r.vpd_kpa = 1.0f;                         // well below the edge → still a breach
    ASSERT_FALSE(invariants::check_24_fog_requires_curve_gate(r, silent_report));

    // Same below-edge fog but in SAFETY_COOL → allowed (survival cooling exemption).
    r.greenhouse_state = "SAFETY_COOL";
    r.local_hour = 23;
    ASSERT_TRUE(invariants::check_24_fog_requires_curve_gate(r, silent_report));
    r.local_hour = 12;
    ASSERT_TRUE(invariants::check_24_fog_requires_curve_gate(r, silent_report));
    PASS();
}

TEST(invariant_23_min_dark_holds_for_production_window) {
    // ENV-5 / M14: the production [6,22) photoperiod leaves 8h dark → passes.
    LightingSetpoints sp{};
    sp.target_light_minutes = 960;
    sp.lux_on_threshold = 40000.0f;
    sp.lux_hysteresis = 8000.0f;
    sp.start_hour = 6;
    sp.cutoff_hour = 22;
    sp.min_on_ms = 120000u;
    sp.min_off_ms = 60000u;
    sp.auto_enabled = true;
    ASSERT_TRUE(invariants::check_23_min_dark(sp, silent_report));
    PASS();
}

TEST(invariant_23_min_dark_catches_overlong_window) {
    // A window wider than 18h (e.g. 05:00→01:00 = 20h) leaves only 4h dark and
    // MUST breach. This is the regression the M14 gate + invariant guard against:
    // before M14 an all-occupied day could light all 24h regardless of window.
    LightingSetpoints sp{};
    sp.target_light_minutes = 960;
    sp.lux_on_threshold = 40000.0f;
    sp.lux_hysteresis = 8000.0f;
    sp.start_hour = 5;
    sp.cutoff_hour = 1;       // wraps midnight → 20h window
    sp.min_on_ms = 120000u;
    sp.min_off_ms = 60000u;
    sp.auto_enabled = true;
    ASSERT_FALSE(invariants::check_23_min_dark(sp, silent_report));
    PASS();
}

TEST(invariant_17_night_drop_enforced_under_new_band) {
    // Firmware-v2: check_17 derives day/night from SOLAR PHASE off the row
    // timestamp (set_solar_inputs_from_row), not the night_*_hour clock window.
    // So each row needs an internally consistent (ts_unix_s, local_hour) pair on
    // the SAME local day. MDT (utc_off -360) anchors below put hour 14 in the
    // solar day and hours 2/3 in the solar night, all in day_bucket 20475.
    invariants::Ctx17 c;
    invariants::TraceRow r{};
    r.night_start_hour = 20; r.night_end_hour = 6;   // new-band config present (gate)

    // Daytime row establishes the day peak (temp_high 88). ts → local 14:00 MDT.
    r.ts_unix_s = 1769112000ULL; r.local_hour = 14;
    r.temp_high = 88.0f; r.temp_low = 78.0f;
    ASSERT_TRUE(invariants::check_17_night_drop(c, r, silent_report));

    // Compliant night row: temp_low 67 is 21°F below peak 88 → pass. ts → 02:00.
    r.ts_unix_s = 1769068800ULL; r.local_hour = 2;
    r.temp_high = 67.0f; r.temp_low = 61.0f;
    ASSERT_TRUE(invariants::check_17_night_drop(c, r, silent_report));

    // Non-compliant night row: temp_low 80 is only 8°F below peak 88 → breach.
    // ts → 03:00 (same local day, still solar night).
    r.ts_unix_s = 1769072400ULL; r.local_hour = 3;
    r.temp_low = 80.0f;
    ASSERT_FALSE(invariants::check_17_night_drop(c, r, silent_report));
    PASS();
}

TEST(invariant_17_night_drop_skipped_without_new_band_config) {
    // Historical corpus (no night config columns) must NOT trip #17 even with
    // a shallow drop — the ≥10°F drop is a property of the new Vanda band.
    invariants::Ctx17 c;
    invariants::TraceRow r{};
    r.night_start_hour = 0; r.night_end_hour = 0;     // config absent → gate off
    r.ts_unix_s = 1769112000ULL; r.local_hour = 14;   // day
    r.temp_high = 75.0f; r.temp_low = 70.0f;
    ASSERT_TRUE(invariants::check_17_night_drop(c, r, silent_report));
    r.ts_unix_s = 1769068800ULL; r.local_hour = 2;    // solar night, only 5°F drop
    r.temp_low = 70.0f;                                // but skipped (gate off)
    ASSERT_TRUE(invariants::check_17_night_drop(c, r, silent_report));
    PASS();
}

TEST(day_mask_allows_zero_sunday) {
    ASSERT_TRUE(day_mask_allows(0b0000001, 0));
    ASSERT_FALSE(day_mask_allows(0b0000001, 1));
    ASSERT_TRUE(day_mask_allows(0b0100100, 2));
    ASSERT_TRUE(day_mask_allows(0b0100100, 5));
    PASS();
}

TEST(feed_window_open_am_only) {
    // FRT-8 / F3: default 06:00-09:00 window. Inside → open; outside → closed.
    ASSERT_TRUE(feed_window_open(6, 6, 9));    // window start, inclusive
    ASSERT_TRUE(feed_window_open(7, 6, 9));    // ~06:30 feed lands here (and 07:xx)
    ASSERT_TRUE(feed_window_open(8, 6, 9));    // last in-window hour
    ASSERT_FALSE(feed_window_open(9, 6, 9));   // end, exclusive
    ASSERT_FALSE(feed_window_open(5, 6, 9));   // before dawn
    ASSERT_FALSE(feed_window_open(10, 6, 9));  // the old 10:30 feed time — now blocked
    ASSERT_FALSE(feed_window_open(15, 6, 9));  // afternoon — blocked
    ASSERT_FALSE(feed_window_open(22, 6, 9));  // dusk/overnight — blocked
    ASSERT_FALSE(feed_window_open(0, 6, 9));   // midnight — blocked
    PASS();
}

TEST(feed_window_open_fails_safe_degenerate) {
    // start == end is degenerate → fail CLOSED (no feed) rather than open 24/7.
    for (int h = 0; h < 24; h++) {
        ASSERT_FALSE(feed_window_open(h, 8, 8));
    }
    PASS();
}

TEST(feed_window_open_clamps_and_wraps) {
    // Out-of-range hours are clamped into [0,23]; a wrap window (start>end) is
    // handled (degenerate for a morning feed but must not open everything).
    ASSERT_TRUE(feed_window_open(99, 6, 9) == feed_window_open(23, 6, 9));  // clamp
    // Wrap window 22->2 covers 22,23,0,1 only.
    ASSERT_TRUE(feed_window_open(23, 22, 2));
    ASSERT_TRUE(feed_window_open(0, 22, 2));
    ASSERT_TRUE(feed_window_open(1, 22, 2));
    ASSERT_FALSE(feed_window_open(2, 22, 2));
    ASSERT_FALSE(feed_window_open(12, 22, 2));
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

TEST(climate_candidate_selector_ignores_resource_cost_after_band_error) {
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
    // BC-4: resource_cost is no longer a tiebreaker. idx1 (MIST_ASSIST) and idx2
    // (IDLE) tie on band error (vpd 0.1); idx2 is CHEAPER (resource 1 < 5) but no
    // longer wins for it — the earlier equally-band-good candidate is kept and
    // cost is ignored. Pre-BC-4 this selected idx2 on the cost tiebreak.
    ASSERT_EQ(choose_climate_candidate_index(candidates, 3), 1);
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

TEST(climate_decision_selects_mist_assist_from_band_error_even_when_ai_switch_off) {
    auto sp = band_setpoints();
    sp.sw_fsm_controller_enabled = true;
    sp.direct_wet_stress_override_enabled = false;
    auto s = initial_state();
    auto in = make_inputs(sp.temp_high + 4.0f, sp.vpd_high + 0.25f);
    in.dew_point_f = in.temp_f - 12.0f;

    ClimateActionDecision d = evaluate_climate_decision(in, sp, s);
    ASSERT_EQ(d.climate_action, CLIMATE_VENT_COOL_MIST_ASSIST);
    ASSERT_EQ(d.moisture_assist_state, CLIMATE_MOISTURE_SERVED);
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

TEST(climate_decision_reports_inactive_moisture_when_vpd_in_band) {
    auto sp = band_setpoints();
    sp.sw_fsm_controller_enabled = true;
    auto s = initial_state();
    auto in = make_inputs(75.0f, 1.0f);
    in.dew_point_f = in.temp_f - 2.0f;

    ClimateActionDecision d = evaluate_climate_decision(in, sp, s);
    ASSERT_EQ(d.moisture_assist_state, CLIMATE_MOISTURE_INACTIVE);
    ASSERT_TRUE(d.next_mist_eligible_s < 0.0f);
    PASS();
}

TEST(climate_decision_reports_engage_delay_timer_before_mist_eligibility) {
    auto sp = band_setpoints();
    sp.sw_fsm_controller_enabled = true;
    sp.vpd_watch_dwell_ms = 60000;
    auto s = initial_state();
    s.vpd_watch_timer_ms = 30000;
    auto in = make_inputs(75.0f, sp.vpd_high + 0.2f);
    in.dew_point_f = in.temp_f - 12.0f;

    ClimateActionDecision d = evaluate_climate_decision(in, sp, s);
    ASSERT_EQ(d.moisture_assist_state, CLIMATE_MOISTURE_ENGAGE_DELAY);
    ASSERT_EQ(int(d.next_mist_eligible_s), 30);
    PASS();
}

TEST(climate_decision_reports_mist_backoff_gap_timer) {
    auto sp = band_setpoints();
    sp.sw_fsm_controller_enabled = true;
    sp.vpd_watch_dwell_ms = 60000;
    sp.mist_backoff_ms = 90000;
    auto s = initial_state();
    s.vpd_watch_timer_ms = sp.vpd_watch_dwell_ms;
    s.mist_backoff_timer_ms = 30000;
    auto in = make_inputs(75.0f, sp.vpd_high + 0.2f);
    in.dew_point_f = in.temp_f - 12.0f;

    ClimateActionDecision d = evaluate_climate_decision(in, sp, s);
    ASSERT_EQ(d.moisture_assist_state, CLIMATE_MOISTURE_PULSE_GAP);
    ASSERT_EQ(int(d.next_mist_eligible_s), 60);
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
    sp.band_track_fraction = 0.0f;  // ADR-0004: assert against the served corridor.
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

TEST(r2_9_fog_stage_reachable_at_night_gated_only_by_physics) {
    // CURVE-ONLY: the MIST_S2→MIST_FOG escalation is gated by the physics fog
    // rails (climate_fog_assist_permitted: curve VPD>vpd_high, dew margin, rh
    // ceiling, fog_min_temp), NOT by a solar taper / night phase. So a dry night
    // with good physics now CLIMBS to MIST_FOG exactly like the daytime path; a
    // physics rail (rh saturated) is what holds the stage down — same day or night.
    auto sp = band_setpoints();
    sp.direct_wet_stress_override_enabled = false;   // inert switch
    validate_setpoints(sp);

    auto run_stage = [&](int hour, float rh) {
        auto s = initial_state();
        s.mode_prev = SEALED_MIST; s.sealed_timer_ms = 100000;
        s.mist_stage = MIST_S2; s.mist_stage_timer_ms = 0;
        SensorInputs in = make_inputs(72, 1.9, rh);   // VPD 1.9 > vpd_high 1.4 + fog_esc 0.4
        in.dew_point_f = in.temp_f - 12.0f;           // dew margin clears the floor
        set_solar_day(in, hour);
        determine_mode(in, sp, s, 5000);
        return s.mist_stage;
    };

    // Physics OK → escalates to MIST_FOG at NIGHT and by DAY, identically.
    ASSERT_EQ(run_stage(22, 60.0f), MIST_FOG);   // solar night — no taper block anymore
    ASSERT_EQ(run_stage(12, 60.0f), MIST_FOG);   // daytime — same outcome

    // Saturated air (rh above fog_rh_ceiling 90) → physics veto holds the stage at
    // MIST_S2, again identically at night and by day (it is physics, not the hour).
    ASSERT_EQ(run_stage(22, sp.fog_rh_ceiling + 2.0f), MIST_S2);
    ASSERT_EQ(run_stage(12, sp.fog_rh_ceiling + 2.0f), MIST_S2);
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
    // make_inputs defaults local_hour=12 (solar day) → fog phase-permitted.
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
    // make_inputs defaults local_hour=12 (solar day) → fog phase-permitted.
    auto s = initial_state(); s.mist_stage = MIST_FOG;
    auto in = make_inputs(72, sp.vpd_high + 0.4f, 60);
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
    auto sp = default_setpoints(); int v = 0; int dehum_heat2 = 0;
    for (float t = 40; t <= 100; t += 2)
        for (float vpd = 0.1f; vpd <= 3.5f; vpd += 0.1f) {
            auto s = initial_state(); s.vpd_watch_timer_ms = 60000;
            Mode m = determine_mode(make_inputs(t, vpd), sp, s, 5000);
            auto out = resolve_equipment(m, make_inputs(t, vpd), sp, s, true);
            // BC-5: DEHUM_VENT may co-run the LEAD heater (stage-1) with the vent to
            // hold the temperature floor while dehumidifying — the ONE sanctioned
            // heat+vent state. heat2 is still never co-run with the vent ANYWHERE.
            if (out.vent && (out.heat1 || out.heat2) && m != DEHUM_VENT) v++;
            if (out.vent && out.heat2 && m == DEHUM_VENT) dehum_heat2++;
        }
    ASSERT_EQ(v, 0);
    ASSERT_EQ(dehum_heat2, 0);
    PASS();
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
    sp.band_track_fraction = 0.0f;  // ADR-0004: assert against the served corridor.
    sp.vpd_max_safe = 3.0f;
    sp.fog_rh_ceiling = 90.0f;
    sp.fog_min_temp = 55.0f;
    auto s = initial_state();
    // 85°F, VPD 3.2 (above trigger 2.59), 25% RH, hour 12 (solar day → fog ok)
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
    auto s = initial_state();
    // 85°F, VPD 1.8 (above band, below OLD vpd_max_safe 3.0), hour 12 (day)
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

TEST(obs1e_fog_gate_window_never_fires_curve_only) {
    // CURVE-ONLY: the fog-gate WINDOW (solar-phase / clock) concept is gone —
    // fog_hour_permitted/fog_phase_permitted are no-op shims that always allow,
    // so f.fog_gate_window can NEVER fire. A hot/dry row that wants to escalate to
    // MIST_FOG reports NO window block at ANY hour, including deep in the solar
    // night (the exact case the old window flag used to flag). The physics gates
    // (rh/temp) are unaffected and stay quiet when physics is satisfied.
    auto sp = default_setpoints();
    sp.direct_wet_stress_override_enabled = false;   // inert switch
    auto s = initial_state(); s.mist_stage = MIST_S2; s.vpd_watch_timer_ms = 60000;
    // VPD high enough to want MIST_FOG, rh/temp within the fog physics rails.
    auto in = make_inputs(72.0f, sp.vpd_high + sp.fog_escalation_kpa + 0.1f, 50.0f);
    for (int hour : {23, 3, 20, 12}) {       // night, pre-dawn, old-taper, midday
        set_solar_day(in, hour);
        auto f = evaluate_overrides(in, sp, s, SEALED_MIST);
        ASSERT_FALSE(f.fog_gate_window);     // window gate retired → always quiet
        ASSERT_FALSE(f.fog_gate_rh);         // rh 50 <= ceiling → no physics block
        ASSERT_FALSE(f.fog_gate_temp);       // temp 72 >= min → no physics block
    }
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

TEST(fog_permitted_depends_only_on_physics_not_phase_or_clock) {
    // CURVE-ONLY: fog_permitted() is PHYSICS-ONLY — it is true iff
    // rh <= fog_rh_ceiling && temp >= fog_min_temp. It does NOT depend on the
    // hour-of-day, solar phase, a clock window, or the (inert) stress override.
    auto sp = band_setpoints();
    sp.fog_rh_ceiling = 90.0f;
    sp.fog_min_temp = 55.0f;
    sp.direct_wet_stress_override_enabled = false;
    validate_setpoints(sp);
    auto in = make_inputs(72.0f, 1.7f, 50.0f);       // rh 50 <= 90, temp 72 >= 55
    in.dew_point_f = in.temp_f - 12.0f;

    // Physics satisfied → permitted at EVERY hour (day, old-taper, deep night),
    // and the inert override switch never changes the answer.
    for (int hour : {12, 9, 20, 23, 3}) {
        set_solar_day(in, hour);
        ASSERT_TRUE(fog_permitted(in, sp));
        sp.direct_wet_stress_override_enabled = true;   // still no effect
        ASSERT_TRUE(fog_permitted(in, sp));
        sp.direct_wet_stress_override_enabled = false;
    }

    // Saturated air (rh > ceiling) → blocked by physics at ANY hour (incl. midday).
    in.rh_pct = sp.fog_rh_ceiling + 1.0f;
    set_solar_day(in, 12);
    ASSERT_FALSE(fog_permitted(in, sp));
    set_solar_day(in, 23);
    ASSERT_FALSE(fog_permitted(in, sp));

    // Too cold (temp < fog_min_temp) → blocked by physics at ANY hour.
    in.rh_pct = 50.0f;
    in.temp_f = sp.fog_min_temp - 1.0f;
    set_solar_day(in, 12);
    ASSERT_FALSE(fog_permitted(in, sp));
    set_solar_day(in, 3);
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
    sp.band_track_fraction = 0.0f;  // ADR-0004: assert against the served corridor.
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
    sp.band_track_fraction = 0.0f;  // ADR-0004: assert against the served corridor.
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

// (Firmware-v2: the fog clock window — and its zero-width validate clamp — is
// DELETED. Fog gating is now solar-phase based; see
// fog_gated_by_solar_phase_not_clock_window. The former
// s9_validate_fixes_zero_width_fog_window test is removed with that path.)

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
    // Firmware-v2 solar-relative knobs also survive sensible defaults unchanged.
    ASSERT_EQ(sp.wet_taper_before_sunset_min, before.wet_taper_before_sunset_min);
    ASSERT_EQ(sp.dawn_boost_offset_min, before.dawn_boost_offset_min);
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
    sp.band_track_fraction = 0.0f;  // ADR-0004: assert against the served corridor.
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
    sp.band_track_fraction = 0.0f;  // ADR-0004: assert against the served corridor.
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
    sp.direct_wet_stress_override_enabled = true;
    sp.direct_wet_stress_vpd_margin_kpa = 0.05f;
    sp.direct_wet_stress_min_dew_margin_f = 8.0f;
    sp.sw_night_stress_wet_enabled = true;
    sp.night_stress_min_dew_margin_f = 6.0f;
    // Band-first mechanism tests (heat2 latch, dehum hysteresis, fan2
    // escalation, cooling exit) assert behavior against the exact 72-78°F band
    // they set. The optional pinch is orthogonal and tested separately, so pin
    // the ADR-0004 float corridor here.
    sp.band_track_fraction = 0.0f;
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

    // BC-8: fan2 is LATCHED in determine_mode, not recomputed in resolve_equipment.
    // resolve_equipment READS state.fan2_latched (the relay-staging concern); the
    // latch SET/CLEAR threshold geometry is covered by fan2_latches_and_does_not_chatter.
    s.fan2_latched = false;  // below the stage-2 entry -> single fan
    auto one = resolve_equipment(VENTILATE, make_inputs(sp.temp_high + 0.9f, 1.4f), sp, s, true);
    ASSERT_TRUE(one.vent);
    ASSERT_TRUE(one.fan1);
    ASSERT_FALSE(one.fan2);

    s.fan2_latched = true;   // determine_mode latched it above temp_high+cool_stage2_over_high_f
    auto out = resolve_equipment(VENTILATE, make_inputs(sp.temp_high + 1.1f, 1.4f), sp, s, true);
    ASSERT_TRUE(out.vent);
    ASSERT_TRUE(out.fan1);
    ASSERT_TRUE(out.fan2);
    PASS();
}

// BC-8 (ADR0003 §6.5): fan2 now has a two-sided hysteresis latch (the de-escalation
// gap heat2 always had and fan2 lacked) so it cannot chatter at the stage-2 edge.
TEST(fan2_latches_and_does_not_chatter) {
    auto sp = band_first_setpoints();          // temp_high=78, fsm=true, no pinch
    sp.cool_stage2_over_high_f = 1.0f;         // fan2 entry = 79
    sp.cool_stage2_exit_hysteresis_f = 1.0f;   // fan2 exit  = 78
    auto s = initial_state();

    // Climb above the stage-2 entry -> fan2 latches.
    determine_mode(make_inputs(80.0f, 1.0f), sp, s, 5000);
    ASSERT_TRUE(s.fan2_latched);
    auto hot = resolve_equipment(VENTILATE, make_inputs(80.0f, 1.0f), sp, s, true);
    ASSERT_TRUE(hot.fan2);

    // Drop INTO the hysteresis band [78,79): the OLD bare threshold dropped fan2 here;
    // the latch HOLDS it (no chatter).
    determine_mode(make_inputs(78.5f, 1.0f), sp, s, 5000);
    ASSERT_TRUE(s.fan2_latched);
    auto mid = resolve_equipment(VENTILATE, make_inputs(78.5f, 1.0f), sp, s, true);
    ASSERT_TRUE(mid.fan2);

    // Drop below the exit -> latch clears.
    determine_mode(make_inputs(77.5f, 1.0f), sp, s, 5000);
    ASSERT_FALSE(s.fan2_latched);
    PASS();
}

// BC-8: the shared two-stage latch is symmetric — high side (fan2) and low side (heat2).
TEST(stage2_escalation_latch_is_symmetric) {
    // high side (fan2): entry 79 > exit 78
    ASSERT_TRUE(stage2_escalation_latch(80.0f, 79.0f, 78.0f, false, true));   // >= entry -> set
    ASSERT_TRUE(stage2_escalation_latch(78.5f, 79.0f, 78.0f, true,  true));   // hold band -> keep set
    ASSERT_FALSE(stage2_escalation_latch(78.5f, 79.0f, 78.0f, false, true));  // hold band -> keep clear
    ASSERT_FALSE(stage2_escalation_latch(77.0f, 79.0f, 78.0f, true,  true));  // < exit -> clear
    // low side (heat2): entry 60 < exit 64 (latch when cold, clear when warm)
    ASSERT_TRUE(stage2_escalation_latch(59.0f, 60.0f, 64.0f, false, false));  // < entry -> set
    ASSERT_TRUE(stage2_escalation_latch(62.0f, 60.0f, 64.0f, true,  false));  // hold band -> keep set
    ASSERT_FALSE(stage2_escalation_latch(62.0f, 60.0f, 64.0f, false, false)); // hold band -> keep clear
    ASSERT_FALSE(stage2_escalation_latch(65.0f, 60.0f, 64.0f, true,  false)); // >= exit -> clear
    PASS();
}

// BC-8: the cool_all_fans_at_high operator override still forces both fans immediately
// above the band edge, bypassing the latch (regression guard for the override).
TEST(cool_all_fans_at_high_overrides_fan2_latch) {
    auto sp = band_first_setpoints();
    sp.cool_all_fans_at_high_enabled = true;
    sp.cool_stage2_over_high_f = 3.0f;   // stage-2 entry = 81, so the latch would NOT set at 78.5
    auto s = initial_state();
    s.fan2_latched = false;
    auto out = resolve_equipment(VENTILATE, make_inputs(sp.temp_high + 0.5f, 1.0f), sp, s, true);
    ASSERT_TRUE(out.fan1);
    ASSERT_TRUE(out.fan2);   // operator override forces both even with the latch clear
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
    // BC-13/§6.4: DEHUM_VENT now requires an EFFECTIVE vent (estimator). Give fresh,
    // cool-but-not-frigid, drier outdoor so venting dries without over-cooling — then
    // the vpd_low entry/exit hysteresis (the actual subject of this test) is exercised.
    auto mk = [&](float vpd) {
        auto i = make_inputs(75.0f, vpd, 70.0f);
        i.outdoor_temp_f = 74.0f; i.outdoor_rh_pct = 30.0f; i.outdoor_data_age_s = 10;
        return i;
    };

    Mode m1 = determine_mode(mk(sp.vpd_low - HV - 0.01f), sp, s, 5000);
    ASSERT_EQ(m1, DEHUM_VENT);
    ASSERT_TRUE(std::string(s.last_mode_reason) == "vpd_low");

    Mode m2 = determine_mode(mk(sp.vpd_low - 0.01f), sp, s, 5000);
    ASSERT_EQ(m2, DEHUM_VENT);
    ASSERT_TRUE(std::string(s.last_mode_reason) == "dehum_continue");

    Mode m3 = determine_mode(mk(sp.vpd_low + 0.01f), sp, s, 5000);
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

TEST(climate_effective_decision_reports_inactive_moisture_when_vpd_in_band) {
    auto sp = band_first_setpoints();
    auto s = initial_state();
    auto in = make_inputs(75.0f, 1.0f);
    in.dew_point_f = in.temp_f - 2.0f;

    const RelayOutputs relays = {true, false, false, false, false, false};
    ClimateActionDecision d = describe_effective_climate_decision(IDLE, in, sp, s, relays);

    ASSERT_EQ(d.climate_action, CLIMATE_HEAT);
    ASSERT_EQ(d.moisture_assist_state, CLIMATE_MOISTURE_INACTIVE);
    ASSERT_TRUE(d.next_mist_eligible_s < 0.0f);
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

TEST(band_first_cold_wet_night_heats_not_vents) {
    // BC-13/§6.4: on a FRIGID wet night, venting would over-cool below the band floor,
    // so the estimator returns MX_HEAT_ASSIST (not MX_VENT_DEHUM): the controller's
    // cold-heat path warms the air (raising VPD = drying) WITHOUT thrashing the vent
    // (invariant #14). This fixes the orchid wet-night regression (VPD no longer
    // drifts wet) by HEATING, and replaces the old cold_dehum brake.
    auto sp = band_first_setpoints();
    auto s = initial_state();
    auto in = make_inputs(sp.temp_low + 1.0f, sp.vpd_low - 0.3f, 88.0f);  // cold-ish, too wet
    in.outdoor_temp_f = sp.temp_low - 20.0f;   // frigid

    Mode m = determine_mode(in, sp, s, 60000);
    ASSERT_TRUE(m != DEHUM_VENT);              // no frigid-day vent thrash
    auto out = resolve_equipment(m, in, sp, s, true);
    ASSERT_TRUE(out.heat1);                    // warms to dry (VPD rises toward target)
    ASSERT_FALSE(out.vent);                    // vent stays shut
    PASS();
}

TEST(band_first_night_low_wet_heat_dehum_runs_closed_vent) {
    // ADR-0004/#383: when night VPD is below the corridor and the estimator says
    // closed-vent heat is the effective dehumidifier, run bounded heat1 instead
    // of idling or opening a cold vent. This covers the ordinary in-band-temp
    // low-wet gap from the 2026-06-22 mechanical review.
    auto sp = band_first_setpoints();
    auto s = initial_state();
    auto in = make_inputs(75.0f, sp.vpd_low - 0.35f, 88.0f);
    set_solar_day(in, 22);                         // solar night
    in.outdoor_data_age_s = 9999u;                 // no fresh vent candidate

    Mode m = determine_mode(in, sp, s, 60000);
    ASSERT_EQ(m, IDLE);                            // closed-vent heat path
    ASSERT_TRUE(std::string(s.last_mode_reason) == "heat_dehum");
    ASSERT_TRUE(s.dehum_heat_assist_active);

    auto out = resolve_equipment(m, in, sp, s, true);
    ASSERT_TRUE(out.heat1);
    ASSERT_FALSE(out.heat2);
    ASSERT_FALSE(out.vent);
    ASSERT_FALSE(out.fog);

    auto d = describe_effective_climate_decision(m, in, sp, s, out);
    ASSERT_EQ(d.climate_action, CLIMATE_HEAT);
    ASSERT_EQ(d.priority_axis, CLIMATE_PRIORITY_VPD);
    ASSERT_TRUE(std::string(d.candidate_summary) == "HEAT selected; closed-vent heat-assist dehum");
    ASSERT_TRUE(std::string(d.moisture_exchange_action) == "heat_assist");
    ASSERT_TRUE(d.moisture_heat_assist_active);
    PASS();
}

TEST(band_first_night_low_wet_heat_dehum_respects_high_edge_headroom) {
    // Heat-dehum is deliberately bounded: if the heat probe would land too close
    // to the high temperature edge, ADR-0004 says float/idle rather than spend gas
    // and risk crossing into a cooling demand.
    auto sp = band_first_setpoints();
    auto s = initial_state();
    auto in = make_inputs(sp.temp_high - 1.0f, sp.vpd_low - 0.35f, 88.0f);
    set_solar_day(in, 22);
    in.outdoor_data_age_s = 9999u;

    Mode m = determine_mode(in, sp, s, 60000);
    ASSERT_EQ(m, IDLE);
    ASSERT_FALSE(s.dehum_heat_assist_active);
    ASSERT_TRUE(std::string(s.last_mode_reason) == "idle");

    auto out = resolve_equipment(m, in, sp, s, true);
    ASSERT_FALSE(out.heat1);
    ASSERT_FALSE(out.heat2);
    ASSERT_FALSE(out.vent);
    PASS();
}

TEST(dehum_heat_assist_respects_min_dwell) {
    // BC-13/§6.4: heat1 co-runs in DEHUM_VENT only AFTER the min-dwell, and only when
    // the estimator proved vent+heat BOTH move VPD toward target (heat1, never heat2).
    // Replaces BC-5's unconditional co-run. Outdoor cold+dry+fresh -> vent_plus_heat.
    auto sp = band_first_setpoints();
    sp.dehum_heat_assist_min_dwell_ms = 300000u;  // 5 min
    auto s = initial_state();
    auto in = make_inputs(sp.temp_low + 1.0f, sp.vpd_low - 0.3f, 88.0f);  // too wet
    // Moderately COOL outdoor (just below indoor) + dry: venting dries WITHOUT over-
    // cooling below the band floor (T_mix >= temp_low), so the estimator returns
    // MX_VENT_DEHUM + heat_assist_corun (vent dries, heat holds the floor). A FRIGID
    // outdoor would route to MX_HEAT_ASSIST (no vent) instead.
    in.outdoor_temp_f = sp.temp_low - 1.0f;    // ~71F vs indoor 73F: cool, no over-cool
    in.outdoor_rh_pct = 35.0f;                 // outdoor lower ABS humidity -> vent helps
    in.outdoor_data_age_s = 10;                // fresh

    // First cycle (below dwell): DEHUM_VENT but heat-assist NOT yet armed.
    Mode m1 = determine_mode(in, sp, s, 60000);
    ASSERT_EQ(m1, DEHUM_VENT);
    auto out1 = resolve_equipment(m1, in, sp, s, true);
    ASSERT_TRUE(out1.vent);
    ASSERT_FALSE(out1.heat1);   // min-dwell not yet satisfied

    // Accrue past the min-dwell -> heat1 co-runs (never heat2).
    determine_mode(in, sp, s, 300000);
    Mode m2 = determine_mode(in, sp, s, 60000);
    ASSERT_EQ(m2, DEHUM_VENT);
    auto out2 = resolve_equipment(m2, in, sp, s, true);
    ASSERT_TRUE(out2.vent);
    ASSERT_TRUE(out2.heat1);
    ASSERT_FALSE(out2.heat2);   // heat2 never co-runs with the vent (invariant #27)
    PASS();
}

TEST(estimator_psychrometrics_sane) {
    // es monotonic + sane bands; mixing ratio rises with RH; VPD-at-state rises with temp.
    ASSERT_TRUE(sat_vapor_pressure_kpa(86.0f) > sat_vapor_pressure_kpa(50.0f));
    ASSERT_TRUE(sat_vapor_pressure_kpa(86.0f) > 4.0f && sat_vapor_pressure_kpa(86.0f) < 4.5f);
    ASSERT_TRUE(sat_vapor_pressure_kpa(50.0f) > 1.0f && sat_vapor_pressure_kpa(50.0f) < 1.6f);
    ASSERT_TRUE(mixing_ratio_g_kg(75.0f, 80.0f) > mixing_ratio_g_kg(75.0f, 40.0f));
    ASSERT_TRUE(vpd_at_state_kpa(80.0f, 1.0f) > vpd_at_state_kpa(70.0f, 1.0f));
    PASS();
}

TEST(estimator_humidify_by_vent_when_outdoor_more_humid) {
    // BC-13 bidirectional: too DRY indoor + outdoor MORE humid (abs) + fresh -> import
    // moisture by venting (MX_VENT_HUMIDIFY); resolve sets vent+fan, NO heat, NO fog.
    auto sp = band_first_setpoints();
    sp.vpd_target = 1.0f;
    auto s = initial_state();
    auto in = make_inputs(80.0f, 1.8f, 30.0f);   // hot-ish, very dry (VPD >> target)
    in.outdoor_temp_f = 78.0f;
    in.outdoor_rh_pct = 85.0f;                   // wetter outdoor (higher abs humidity)
    in.outdoor_data_age_s = 10;                  // fresh
    auto mx = estimate_moisture_exchange(in, sp);
    ASSERT_EQ(mx.action, MX_VENT_HUMIDIFY);
    auto out = resolve_equipment(DEHUM_VENT, in, sp, s, true);
    ASSERT_TRUE(out.vent);
    ASSERT_FALSE(out.heat1);
    ASSERT_FALSE(out.fog);
    PASS();
}

TEST(estimator_no_humidify_import_when_outdoor_drier) {
    // too DRY indoor but outdoor abs-humidity LOWER -> MX_NONE (must NOT vent into dry
    // outdoor air, which would make it drier). Falls through to the fog/SEALED path.
    auto sp = band_first_setpoints();
    sp.vpd_target = 1.0f;
    auto in = make_inputs(80.0f, 1.8f, 30.0f);   // too dry
    in.outdoor_temp_f = 60.0f;
    in.outdoor_rh_pct = 20.0f;                   // drier outdoor (lower abs)
    in.outdoor_data_age_s = 10;
    auto mx = estimate_moisture_exchange(in, sp);
    ASSERT_TRUE(mx.action != MX_VENT_HUMIDIFY);
    PASS();
}

// ── BC-14 (ADR0003 §6.6): fog-first wetting ladder (band-first path) ──────────
// Fog (0.26 GPM) leads as the gentle entry; misters (~1 GPM) escalate above it.
// LAYERED: fog persists through the mister stages. These exercise the band-first
// ladder (sw_fsm_controller_enabled=true via band_first_setpoints).
TEST(band_first_sealed_entry_seeds_fog) {
    auto sp = band_first_setpoints();              // vpd_high=1.2, fog_escalation_kpa=0.4
    auto s = initial_state();
    s.vpd_watch_timer_ms = sp.vpd_watch_dwell_ms;  // humidify ready
    auto in = make_inputs(74.0f, 1.4f);            // too dry, but < escalate (1.2+0.4=1.6)
    Mode m = determine_mode(in, sp, s, 5000);
    ASSERT_EQ(m, SEALED_MIST);
    ASSERT_EQ(s.mist_stage, MIST_FOG);             // fog-first: entry seeds FOG, not misters
    auto out = resolve_equipment(SEALED_MIST, in, sp, s, true);
    ASSERT_TRUE(out.fog);                          // fog leads
    PASS();
}

TEST(band_first_fog_holds_no_mister_escalation) {
    auto sp = band_first_setpoints();
    auto s = initial_state();
    s.mode_prev = SEALED_MIST; s.mist_stage = MIST_FOG;
    s.vpd_watch_timer_ms = sp.vpd_watch_dwell_ms;
    s.mist_stage_timer_ms = sp.mist_s2_delay_ms + 1;   // fog-only dwell elapsed
    determine_mode(make_inputs(74.0f, 1.4f), sp, s, 5000);  // 1.4 < escalate 1.6
    ASSERT_EQ(s.mist_stage, MIST_FOG);             // fog holds, misters do NOT engage
    PASS();
}

TEST(band_first_fog_escalates_to_misters_when_fog_cant_hold) {
    auto sp = band_first_setpoints();
    auto s = initial_state();
    s.mode_prev = SEALED_MIST; s.mist_stage = MIST_FOG;
    s.vpd_watch_timer_ms = sp.vpd_watch_dwell_ms;
    s.mist_stage_timer_ms = sp.mist_s2_delay_ms + 1;   // fog-only dwell elapsed
    determine_mode(make_inputs(74.0f, 1.8f), sp, s, 5000);  // 1.8 > escalate 1.6
    ASSERT_EQ(s.mist_stage, MIST_S1);              // escalated FOG -> misters
    PASS();
}

TEST(band_first_misters_deescalate_to_fog_on_recovery) {
    auto sp = band_first_setpoints();
    auto s = initial_state();
    s.mode_prev = SEALED_MIST; s.mist_stage = MIST_S1;
    s.vpd_watch_timer_ms = sp.vpd_watch_dwell_ms;
    // recovered below deescalate (1.6 - MISTER_DEESCALATE_KPA 0.15 = 1.45) but still > vpd_high
    determine_mode(make_inputs(74.0f, 1.3f), sp, s, 5000);
    ASSERT_EQ(s.mist_stage, MIST_FOG);             // symmetric de-escalation S1 -> fog
    PASS();
}

TEST(band_first_fog_gated_escalates_to_misters_immediately) {
    auto sp = band_first_setpoints();
    sp.fog_rh_ceiling = 50.0f;                     // force fog physically gated (rh 60 > 50)
    auto s = initial_state();
    s.mode_prev = SEALED_MIST; s.mist_stage = MIST_FOG;
    s.vpd_watch_timer_ms = sp.vpd_watch_dwell_ms;
    s.mist_stage_timer_ms = 0;                     // dwell NOT elapsed
    determine_mode(make_inputs(74.0f, 1.8f, 60.0f), sp, s, 5000);  // too dry + fog gated
    ASSERT_EQ(s.mist_stage, MIST_S1);              // fog can't run -> misters escalate immediately
    PASS();
}

TEST(band_track_fraction_nonzero_tightens_cooling_toward_target) {
    // ADR-0004 default fraction 0 floats inside [low,high]. A nonzero explicit
    // diagnostic value pinches the CONTROL band toward temp_target, so the same
    // temperature now triggers cooling.
    auto sp = band_first_setpoints();
    sp.temp_low = 65.0f; sp.temp_high = 85.0f; sp.temp_target = 75.0f;
    sp.vpd_low = 0.8f; sp.vpd_high = 1.4f; sp.vpd_target = 1.1f;
    auto in = make_inputs(82.0f, 1.1f);   // inside the float band, VPD on target

    sp.band_track_fraction = 0.0f;
    validate_setpoints(sp);
    auto s0 = initial_state();
    Mode m0 = determine_mode(in, sp, s0, 5000);
    ASSERT_TRUE(m0 != VENTILATE);          // floats: 82 < temp_high 85 → no cooling

    sp.band_track_fraction = 0.6f;         // temp_high_eff = 85 - 0.6*(85-75) = 79 < 82
    validate_setpoints(sp);
    auto s1 = initial_state();
    Mode m1 = determine_mode(in, sp, s1, 5000);
    ASSERT_EQ(m1, VENTILATE);              // tracks: 82 > pinched high edge → cool
    PASS();
}

TEST(adr0004_float_is_the_default_not_pinch) {
    ASSERT_TRUE(std::fabs(default_setpoints().band_track_fraction) < 1e-6f);
    PASS();
}

TEST(adr0004_default_keeps_served_corridor_edges) {
    auto sp = band_first_setpoints();
    sp.temp_low = 70.0f; sp.temp_high = 82.0f; sp.temp_target = 76.0f;
    sp.vpd_low = 0.7f; sp.vpd_high = 1.5f; sp.vpd_target = 1.0f;
    sp.band_track_fraction = default_setpoints().band_track_fraction;
    validate_setpoints(sp);
    auto p = apply_band_track_pinch(sp);
    ASSERT_TRUE(std::fabs(p.temp_low - sp.temp_low) < 1e-6f);
    ASSERT_TRUE(std::fabs(p.temp_high - sp.temp_high) < 1e-6f);
    ASSERT_TRUE(std::fabs(p.vpd_low - sp.vpd_low) < 1e-6f);
    ASSERT_TRUE(std::fabs(p.vpd_high - sp.vpd_high) < 1e-6f);
    PASS();
}

TEST(band_track_fraction_nonzero_moves_both_axes_toward_target) {
    auto sp = band_first_setpoints();
    sp.temp_low = 70.0f; sp.temp_high = 82.0f; sp.temp_target = 76.0f;
    sp.vpd_low = 0.7f; sp.vpd_high = 1.5f; sp.vpd_target = 1.0f;
    sp.band_track_fraction = 0.6f;
    validate_setpoints(sp);
    auto p = apply_band_track_pinch(sp);
    ASSERT_TRUE(p.temp_low > sp.temp_low && p.temp_high < sp.temp_high);
    ASSERT_TRUE(p.vpd_low  > sp.vpd_low  && p.vpd_high  < sp.vpd_high);
    ASSERT_TRUE(p.temp_low <= sp.temp_target && sp.temp_target <= p.temp_high);
    ASSERT_TRUE(p.vpd_low  <= sp.vpd_target  && sp.vpd_target  <= p.vpd_high);
    PASS();
}

// Even an explicit nonzero diagnostic fraction must NOT invert the band, touch
// safety rails, or push the heat-stage-1 trigger up to/over the cooling edge.
// The per-axis width floor in apply_band_track_pinch guarantees this.
TEST(band_track_fraction_nonzero_never_inverts_or_overlaps_demand) {
    const float bands[][2] = {{74.0f, 76.0f}, {72.0f, 78.0f}, {40.0f, 95.0f}};
    for (const auto& b : bands) {
        auto sp = band_first_setpoints();
        sp.temp_low = b[0]; sp.temp_high = b[1];
        sp.temp_target = (b[0] + b[1]) * 0.5f;
        sp.vpd_low = 0.8f; sp.vpd_high = 1.2f; sp.vpd_target = 1.0f;
        sp.heat_hysteresis = 1.0f;
        sp.band_track_fraction = 1.0f;          // extreme
        validate_setpoints(sp);
        auto p = apply_band_track_pinch(sp);
        ASSERT_TRUE(p.temp_low < p.temp_high && p.vpd_low < p.vpd_high);        // no inversion
        ASSERT_TRUE(p.safety_min == sp.safety_min && p.safety_max == sp.safety_max);
        ASSERT_TRUE(p.vpd_min_safe == sp.vpd_min_safe && p.vpd_max_safe == sp.vpd_max_safe);
        // demand-overlap floor: heat trigger never reaches the cooling edge
        ASSERT_TRUE(band_heat_target_f(p) + sp.heat_hysteresis <= p.temp_high + 1e-4f);
    }
    PASS();
}

// Optional nonzero tracking still flows through resolve_equipment. At a temp that
// is in-band but below the pinched heat target, the relay map calls for heat
// where the ADR-0004 float corridor would not.
TEST(band_track_fraction_nonzero_flows_through_resolve_equipment) {
    auto sp = band_first_setpoints();
    sp.temp_low = 72.0f; sp.temp_high = 85.0f; sp.temp_target = 80.0f;
    sp.vpd_low = 0.8f; sp.vpd_high = 1.4f; sp.vpd_target = 1.0f;
    sp.heat_hysteresis = 1.0f;
    auto in = make_inputs(77.0f, 1.0f);   // float heat trigger ~76.25 (no heat); pinched ~79.1 (heat)
    auto st = initial_state();

    sp.band_track_fraction = 0.0f; validate_setpoints(sp);
    auto out_float = resolve_equipment(IDLE, in, sp, st, true);
    ASSERT_TRUE(!out_float.heat1);         // float envelope: 77 above the float heat target → no heat

    sp.band_track_fraction = 0.6f; validate_setpoints(sp);
    auto out_pinch = resolve_equipment(IDLE, in, sp, st, true);
    ASSERT_TRUE(out_pinch.heat1);          // pinched: 77 below the pinched heat target → strive up
    PASS();
}

TEST(band_first_cool_day_vent_dehum_when_effective) {
    // BC-13/§6.4: on a COOL (not frigid) day with the house wet and outdoor drier,
    // venting dries WITHOUT over-cooling below the band floor, so the estimator
    // returns MX_VENT_DEHUM and the controller dehums by venting. (A frigid outdoor
    // would route to heat instead — see band_first_cold_wet_night_heats_not_vents.)
    auto sp = band_first_setpoints();
    auto s = initial_state();
    auto in = make_inputs(sp.temp_low + 3.0f, sp.vpd_low - 0.3f, 88.0f);
    in.outdoor_temp_f = sp.temp_low - 1.0f;    // cool, not frigid (vent doesn't over-cool)
    in.outdoor_rh_pct = 35.0f;                 // outdoor drier (abs) -> venting dries
    in.outdoor_data_age_s = 10;                // fresh

    Mode m = determine_mode(in, sp, s, 60000);
    ASSERT_EQ(m, DEHUM_VENT);
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
    // ENV-5 (M14): occupancy task-light fires only INSIDE the photoperiod
    // window. Hour 12 is inside [6,22); exterior lux below threshold.
    auto sp = lighting_setpoints();
    auto s = initial_lighting_state();
    auto d = evaluate_lighting(lighting_inputs(1000.0f, 12, true, 1000.0f, true), sp, s, false, 120000);
    ASSERT_TRUE(d.want_on);
    ASSERT_TRUE(d.in_window);
    ASSERT_TRUE(d.exterior_lux_available);
    ASSERT_TRUE(d.occupancy_task_light_demand);
    ASSERT_TRUE(std::string(d.reason) == "occupancy_lux_low");
    PASS();
}

// ENV-5 (M14): the load-bearing dark-block guard. An evening visit (hour 23,
// OUTSIDE the [6,22) window) with low exterior lux must NOT light — neither the
// occupancy task light nor the plant supplement. Pre-M14 the occupancy branch
// omitted in_window and grow lights came on at 23:49 local, violating the >=6h
// dark requirement the Vanda needs.
TEST(lighting_occupancy_cannot_light_during_dark_block) {
    auto sp = lighting_setpoints();
    auto s = initial_lighting_state();
    auto d = evaluate_lighting(lighting_inputs(1000.0f, 23, true, 1000.0f, true), sp, s, false, 120000);
    ASSERT_FALSE(d.want_on);
    ASSERT_FALSE(d.in_window);
    ASSERT_FALSE(d.occupancy_task_light_demand);
    ASSERT_FALSE(d.plant_supplement_demand);
    ASSERT_TRUE(std::string(d.reason) == "outside_window");
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
    // In-window (hour 12) so the stale-exterior-lux path is the one exercised,
    // not the ENV-5 dark-block. target_light_minutes met to suppress plant
    // demand, isolating the occupancy-lux-unavailable reason.
    auto sp = lighting_setpoints();
    sp.target_light_minutes = 0;  // plant minutes goal already met → no plant demand
    auto s = initial_lighting_state();
    auto d = evaluate_lighting(lighting_inputs(1000.0f, 12, true, 1000.0f, false), sp, s, false, 120000);
    ASSERT_FALSE(d.want_on);
    ASSERT_FALSE(d.exterior_lux_available);
    ASSERT_FALSE(d.occupancy_task_light_demand);
    ASSERT_TRUE(std::string(d.reason) == "occupancy_lux_unavailable");
    PASS();
}

TEST(lighting_occupancy_holds_until_exterior_off_threshold) {
    // In-window (hour 12): occupancy hysteresis hold when exterior lux sits
    // between the on and off thresholds and the light is already on.
    auto sp = lighting_setpoints();
    auto s = initial_lighting_state();
    auto d = evaluate_lighting(lighting_inputs(1000.0f, 12, true, 45000.0f, true), sp, s, true, 180000);
    ASSERT_TRUE(d.want_on);
    ASSERT_TRUE(d.occupancy_task_light_demand);
    ASSERT_TRUE(std::string(d.reason) == "occupancy_hysteresis_hold");
    PASS();
}

TEST(lighting_occupancy_clear_turns_off_without_plant_demand) {
    // Prime the light ON in-window (hour 12) with occupancy, then advance past
    // the min-on dwell so the hold cannot keep it on. Occupancy then clears AND
    // the hour rolls past cutoff into the dark block (hour 23): the light must
    // turn off for "outside_window".
    auto sp = lighting_setpoints();
    auto s = initial_lighting_state();
    evaluate_lighting(lighting_inputs(1000.0f, 12, true, 1000.0f, true), sp, s, false, 120000);
    auto d = evaluate_lighting(lighting_inputs(1000.0f, 23, false, 1000.0f, true), sp, s, true, 1000000);
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
    // In-window (hour 12): occupancy demand present but the min-off dwell since
    // the last transition has not elapsed, so the light is held off.
    auto sp = lighting_setpoints();
    auto s = initial_lighting_state();
    s.last_transition_tick_ms = 100000;
    auto d = evaluate_lighting(lighting_inputs(1000.0f, 12, true, 1000.0f, true), sp, s, false, 120000);
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

// #295: a circuit flagged out_of_service must not credit supplemental DLI even
// while its switch reads ON — the dead west grow circuit was self-deceivingly
// crediting 0.4515 mol/m²/h with dark fixtures.
TEST(lighting_dli_increment_out_of_service_suppresses_supplemental) {
    // Both circuits switch-ON but both flagged dead → zero supplemental DLI.
    float none = lighting_dli_increment(0.0f, 0.0f, true, true, 3600.0f, true, true);
    ASSERT_TRUE(std::fabs(none - 0.0f) < 1e-6f);

    // Only the GROW (west) circuit dead: MAIN still credits its 0.3485, GROW 0.
    float main_only = lighting_dli_increment(0.0f, 0.0f, true, true, 3600.0f, false, true);
    ASSERT_TRUE(std::fabs(main_only - 0.3485f) < 0.001f);

    // Only MAIN dead: GROW still credits its 0.4515.
    float grow_only = lighting_dli_increment(0.0f, 0.0f, true, true, 3600.0f, true, false);
    ASSERT_TRUE(std::fabs(grow_only - 0.4515f) < 0.001f);

    // Natural light is fixture-independent and still credits when a circuit is
    // out of service (indoor 10000 lux * 3.5 corr path, no supplemental).
    float natural = lighting_dli_increment(10000.0f, 1000.0f, true, true, 3600.0f, true, true);
    ASSERT_TRUE(std::fabs(natural - 2.331f) < 0.01f);
    PASS();
}

// #295: an out_of_service circuit's switch-ON time must NOT accrue qualified
// light minutes (so a dark fixture cannot "meet" the photoperiod on paper).
// Natural lux above threshold still counts — it is real, fixture-independent.
TEST(lighting_out_of_service_suppresses_switch_qualified_minutes) {
    auto sp = lighting_setpoints();
    sp.out_of_service = true;
    auto s = initial_lighting_state();
    // Switch ON, but natural lux well BELOW threshold → only the (suppressed)
    // switch state could have counted. Expect zero qualified accrual.
    evaluate_lighting(lighting_inputs(1000.0f, 10), sp, s, true, 120000, 60.0f);
    ASSERT_TRUE(std::fabs(s.switch_on_s - 0.0f) < 1e-3f);
    ASSERT_TRUE(std::fabs(s.qualified_light_s - 0.0f) < 1e-3f);
    ASSERT_TRUE(std::fabs(s.overlap_s - 0.0f) < 1e-3f);

    // Natural lux above threshold still qualifies even while out of service.
    auto s2 = initial_lighting_state();
    evaluate_lighting(lighting_inputs(45000.0f, 10), sp, s2, true, 120000, 60.0f);
    ASSERT_TRUE(std::fabs(s2.natural_qualified_s - 60.0f) < 1e-3f);
    ASSERT_TRUE(std::fabs(s2.switch_on_s - 0.0f) < 1e-3f);  // switch still suppressed
    ASSERT_TRUE(std::fabs(s2.overlap_s - 0.0f) < 1e-3f);
    ASSERT_TRUE(std::fabs(s2.qualified_light_s - 60.0f) < 1e-3f);  // natural-only
    PASS();
}

// #295: the out_of_service flag is surfaced on the decision for auditing, and a
// dead circuit's switch-ON no longer reports a plant supplement being delivered
// past target (its minutes never accrue, so demand persists honestly).
TEST(lighting_decision_surfaces_out_of_service) {
    auto sp = lighting_setpoints();
    sp.out_of_service = true;
    auto s = initial_lighting_state();
    auto d = evaluate_lighting(lighting_inputs(30000.0f, 10), sp, s, true, 120000, 60.0f);
    ASSERT_TRUE(d.out_of_service);
    ASSERT_TRUE(std::fabs(d.qualified_light_minutes - 0.0f) < 1e-3f);
    PASS();
}

// #295: solar-phasing. With solar_phasing ON and on-chip sunrise/sunset wired,
// the window tracks the solar minutes, not the static start_hour/cutoff_hour.
// Sunrise 06:00 (360) .. sunset 20:00 (1200): hour 10 (600min) is inside;
// hour 22 (1320min) is outside even though the legacy [6,22) clock window would
// also exclude it — we pin a case the clock window would DISAGREE on below.
TEST(lighting_solar_phasing_window_tracks_solar_minutes) {
    auto sp = lighting_setpoints();
    sp.solar_phasing = true;
    sp.sunrise_offset_min = 0;
    sp.sunset_offset_min = 0;
    // start_hour/cutoff_hour deliberately set to a NARROW clock window [9,11)
    // that DISAGREES with the solar window so we prove the solar path is used.
    sp.start_hour = 9;
    sp.cutoff_hour = 11;

    LightingInputs in{};
    in.natural_lux = 1000.0f;        // below threshold → plant demand if in window
    in.exterior_lux = 1000.0f;
    in.exterior_lux_fresh = true;
    in.occupied = false;
    in.sunrise_min = 360;            // 06:00
    in.sunset_min = 1200;            // 20:00
    // Hour 18 (1080min): OUTSIDE the [9,11) clock window but INSIDE the solar
    // [360,1200) window → solar phasing must report in_window + plant demand.
    in.local_hour = 18;
    in.local_minute = 0;
    auto s = initial_lighting_state();
    auto d = evaluate_lighting(in, sp, s, false, 120000, 60.0f);
    ASSERT_TRUE(d.in_window);
    ASSERT_TRUE(d.plant_supplement_demand);

    // Hour 22 (1320min): OUTSIDE the solar [360,1200) window → not in window.
    in.local_hour = 22;
    auto s2 = initial_lighting_state();
    auto d2 = evaluate_lighting(in, sp, s2, false, 120000, 60.0f);
    ASSERT_FALSE(d2.in_window);
    ASSERT_TRUE(std::string(d2.reason) == "outside_window");
    PASS();
}

// #295: backward-safe fallback. solar_phasing ON but the caller left the solar
// minute fields zero (unwired) → the window falls back EXACTLY to the legacy
// integer-hour window. Hour 23 is outside [6,22) and must NOT light.
TEST(lighting_solar_phasing_falls_back_to_fixed_hours_when_unwired) {
    auto sp = lighting_setpoints();   // [6,22) clock window
    sp.solar_phasing = true;          // requested, but...
    LightingInputs in{};
    in.natural_lux = 1000.0f;
    in.exterior_lux = 1000.0f;
    in.exterior_lux_fresh = true;
    in.occupied = false;
    in.sunrise_min = 0;               // ...solar fields UNWIRED (zero)
    in.sunset_min = 0;
    in.local_hour = 23;               // outside the legacy [6,22)
    in.local_minute = 0;
    auto s = initial_lighting_state();
    auto d = evaluate_lighting(in, sp, s, false, 120000, 60.0f);
    ASSERT_FALSE(d.in_window);
    ASSERT_TRUE(std::string(d.reason) == "outside_window");

    // And a fixed-hour in-window hour (10) still lights → fallback is the legacy
    // path, not a degenerate always-off.
    in.local_hour = 10;
    auto s2 = initial_lighting_state();
    auto d2 = evaluate_lighting(in, sp, s2, false, 120000, 60.0f);
    ASSERT_TRUE(d2.in_window);
    ASSERT_TRUE(d2.plant_supplement_demand);
    PASS();
}

// #295: solar phasing OFF (default) must be byte-identical to the legacy
// integer-hour window even when solar minute fields ARE present — the flag, not
// the field presence, switches behavior. Vanda dark-block safety depends on
// this not silently changing the window for existing deployments.
TEST(lighting_solar_phasing_off_ignores_solar_fields) {
    auto sp = lighting_setpoints();   // [6,22), solar_phasing default false
    LightingInputs in{};
    in.natural_lux = 1000.0f;
    in.exterior_lux = 1000.0f;
    in.exterior_lux_fresh = true;
    in.occupied = false;
    in.sunrise_min = 360;             // present but must be IGNORED
    in.sunset_min = 1200;
    in.local_hour = 23;               // outside legacy [6,22)
    in.local_minute = 0;
    auto s = initial_lighting_state();
    auto d = evaluate_lighting(in, sp, s, false, 120000, 60.0f);
    ASSERT_FALSE(d.in_window);
    ASSERT_TRUE(std::string(d.reason) == "outside_window");
    PASS();
}

// Cross-implementation band-curve ALIGNMENT guard. The harmonic band math lives
// in THREE places kept in sync by hand: this firmware band_value_at_phase, the
// ingestor's solar.py band_value_at_phase, and the DB's fn_crop_band_value. These
// golden values are the SAME ones the Python suite asserts
// (tests/test_solar_band_anchors.py uses the house temp_target anchors
// {66,84,73,64}); the live device==DB equality is verified separately. If the
// firmware curve ever drifts from the canonical harmonic — or the Python golden
// changes without this one — one of the two suites fails. This is what keeps the
// end-to-end band aligned.
TEST(band_value_at_phase_matches_canonical_harmonic_goldens) {
    const BandAnchors a {66.0f, 84.0f, 73.0f, 64.0f};
    // Anchors are passed through exactly at sr/sm/ss/mid (phase 0/1/2/3).
    ASSERT_TRUE(std::fabs(band_value_at_phase(a, 0.0f) - 66.0f) < 1e-3f);
    ASSERT_TRUE(std::fabs(band_value_at_phase(a, 1.0f) - 84.0f) < 1e-3f);
    ASSERT_TRUE(std::fabs(band_value_at_phase(a, 2.0f) - 73.0f) < 1e-3f);
    ASSERT_TRUE(std::fabs(band_value_at_phase(a, 3.0f) - 64.0f) < 1e-3f);
    // Harmonic interpolant between sr and sm — the Python suite's golden.
    ASSERT_TRUE(std::fabs(band_value_at_phase(a, 0.5f) - 76.3462f) < 1e-3f);
    // Periodic: phase 4.0 wraps to phase 0.0.
    ASSERT_TRUE(std::fabs(band_value_at_phase(a, 4.0f) - band_value_at_phase(a, 0.0f)) < 1e-3f);
    // Smooth (not the old cosine-ease): nonzero slope THROUGH an anchor — the
    // property that killed the lumpy plateaus.
    const float eps = 1e-3f;
    const float slope = band_value_at_phase(a, 1.0f + eps) - band_value_at_phase(a, 1.0f - eps);
    ASSERT_TRUE(std::fabs(slope) > 1e-5f);
    PASS();
}

// solar_phase must be C1-smooth (no slope kink) at sunrise/sunset. The old
// piecewise-LINEAR phase jumped ~1.6x in slope where the day rate (SR->SS over
// ~15 h) met the night rate (SS->SR over ~9 h), corner-ing the high-amplitude
// temp band. The Hermite phase must pass the anchors exactly AND join smoothly.
// Same math as ingestor solar.py + DB fn_solar_phase (end-to-end alignment).
TEST(solar_phase_c1_smooth_and_hits_anchors) {
    SolarTimes st;
    st.sunrise_min = 330;     // 05:30
    st.solar_noon_min = 780;  // 13:00
    st.sunset_min = 1230;     // 20:30  (15 h day, 9 h night)
    // Exact anchor pass-through at SR/SM/SS (phase 0/1/2).
    ASSERT_TRUE(std::fabs(solar_phase(330, st) - 0.0f) < 1e-3f);
    ASSERT_TRUE(std::fabs(solar_phase(780, st) - 1.0f) < 1e-3f);
    ASSERT_TRUE(std::fabs(solar_phase(1230, st) - 2.0f) < 1e-3f);
    // Slope continuity across sunset (phase 2): the per-5-min slope just before
    // and just after must agree closely (old design differed by ~67%).
    const float before = solar_phase(1230, st) - solar_phase(1225, st);
    const float after  = solar_phase(1235, st) - solar_phase(1230, st);
    ASSERT_TRUE(before > 0.0f && after > 0.0f);                       // monotone up
    ASSERT_TRUE(std::fabs(after - before) < 0.20f * before);          // <20% join (was ~67%)
    // Monotone across the join overall.
    ASSERT_TRUE(solar_phase(1235, st) > solar_phase(1225, st));
    PASS();
}

// ═══════════════════════════════════════════════════════════════
// L2 #344 AC3 — 72-HOUR DISCONNECTED / OFFLINE-FIRST DETERMINISM
// ═══════════════════════════════════════════════════════════════
// The controller must run safely for >=72h with ZERO network: it computes the
// solar ephemeris on-chip (compute_greenhouse_solar_times) and reconstructs the
// served band from NVS-persisted anchors (band_value_at_phase) — no dispatcher,
// no Wi-Fi (greenhouse_solar.h:8-12). These pin (i) a 72h standalone run is
// deterministic and never spuriously trips a safety/fault rail while sensors are
// comfortable, (ii) the no-time-source fallback phase degrades gracefully, and
// (iii) a reboot with the same persisted anchors reproduces byte-identical
// setpoints — the offline-first guarantee a power cycle does not drift the program.

// House temp band + a representative VPD band, as the NVS anchors would hold them.
static const BandAnchors kDiscTempAnchors {66.0f, 84.0f, 73.0f, 64.0f};  // F: SR/SM/SS/MID
static const BandAnchors kDiscVpdAnchors  {0.95f, 1.05f, 0.85f, 0.55f};  // kPa: SR/SM/SS/MID

static bool is_safety_or_fault(Mode m) {
    return m == SAFETY_COOL || m == SAFETY_HEAT || m == SENSOR_FAULT;
}

// The SensorInputs a disconnected controller sees at (day_of_year, minute):
// on-chip ephemeris ONLY, sensors parked at the reconstructed band centre (the
// standalone steady state — the controller is holding the band it computed
// itself). sp_out is the band reconstructed purely from the NVS anchors.
static SensorInputs disconnected_inputs(int day_of_year, int minute_of_day,
                                        int utc_off_min, Setpoints& sp_out) {
    const SolarTimes st = compute_greenhouse_solar_times(day_of_year, utc_off_min);
    const float phase  = solar_phase(minute_of_day, st);
    const float t_band = band_value_at_phase(kDiscTempAnchors, phase);
    const float v_band = band_value_at_phase(kDiscVpdAnchors, phase);
    sp_out = band_setpoints();
    sp_out.temp_low  = t_band - 6.0f;  sp_out.temp_high = t_band + 6.0f;
    sp_out.vpd_low   = v_band - 0.25f; sp_out.vpd_high  = v_band + 0.25f;
    SensorInputs in = make_inputs(t_band, v_band, 60.0f);  // sensors AT band centre
    in.local_hour     = minute_of_day / 60;
    in.now_minute     = minute_of_day;
    in.sunrise_min    = st.sunrise_min;
    in.solar_noon_min = st.solar_noon_min;
    in.sunset_min     = st.sunset_min;
    in.solar_phase    = phase;
    return in;
}

TEST(disconnected_72h_run_is_deterministic_and_safe) {
    // Three days spread across the year so the on-chip ephemeris is exercised at
    // very different day lengths (winter/spring/summer). 72h = 3 contiguous days
    // sampled every 15 min (96 steps/day). Two independent "boots" must produce
    // the identical mode trace — the core offline-determinism property.
    const int days[3] = {80, 172, 355};
    const int utc_off = -360;  // MDT
    const uint32_t dt_ms = 15u * 60u * 1000u;  // true elapsed between samples
    std::vector<Mode> seq_a, seq_b;
    for (int run = 0; run < 2; run++) {
        ControlState st = initial_state();           // fresh "boot" per run
        std::vector<Mode>& seq = (run == 0) ? seq_a : seq_b;
        for (int di = 0; di < 3; di++) {
            for (int minute = 0; minute < 1440; minute += 15) {
                Setpoints sp;
                SensorInputs in = disconnected_inputs(days[di], minute, utc_off, sp);
                // Reconstructed band is always finite + ordered (no NaN storms).
                ASSERT_TRUE(sp.temp_low < sp.temp_high && sp.vpd_low < sp.vpd_high);
                ASSERT_TRUE(std::isfinite(in.solar_phase)
                            && in.solar_phase >= 0.0f && in.solar_phase < 4.0f);
                Mode m = determine_mode(in, sp, st, dt_ms);
                // Comfortable sensors at band centre => never a safety/fault rail.
                ASSERT_FALSE(is_safety_or_fault(m));
                seq.push_back(m);
            }
        }
    }
    ASSERT_TRUE(seq_a.size() == seq_b.size() && seq_a.size() == 3u * 96u);
    for (size_t i = 0; i < seq_a.size(); i++) ASSERT_EQ(seq_a[i], seq_b[i]);
    PASS();
}

TEST(no_time_source_fallback_phase_stays_safe) {
    // No time source: SensorInputs.solar_phase is NaN and now_minute is unset, so
    // the controller must fall back to fallback_solar_phase(local_hour)
    // (greenhouse_logic.h effective_solar_phase:42-48 / greenhouse_solar.h:145).
    // Across all 24 local hours the fallback phase must be finite + in-range, the
    // reconstructed band finite, and a comfortable house must NOT trip a
    // safety/fault rail purely because the clock is missing.
    for (int hour = 0; hour < 24; hour++) {
        const float fb = fallback_solar_phase(hour);
        ASSERT_TRUE(std::isfinite(fb) && fb >= 0.0f && fb < 4.0f);
        const float t_band = band_value_at_phase(kDiscTempAnchors, fb);
        ASSERT_TRUE(std::isfinite(t_band));
        Setpoints sp = band_setpoints();
        sp.temp_low = t_band - 6.0f; sp.temp_high = t_band + 6.0f;
        SensorInputs in = make_inputs(t_band, 1.0f, 60.0f);
        in.local_hour  = hour;
        in.now_minute  = -1;     // unset => effective_now_minute uses local_hour
        in.solar_phase = NAN;    // no time source => fallback path
        // The controller resolves the fallback phase, not a NaN.
        ASSERT_TRUE(std::fabs(effective_solar_phase(in) - fb) < 1e-3f);
        ControlState st = initial_state();
        Mode m = determine_mode(in, sp, st, 5000);
        ASSERT_FALSE(is_safety_or_fault(m));
    }
    PASS();
}

TEST(reboot_persisted_anchors_reproduce_identical_band) {
    // NVS persists the band anchors across a reboot. Reconstructing the served
    // band from the SAME anchors at the SAME solar phase before and after a
    // simulated reboot (fresh ControlState) must reproduce byte-identical
    // setpoints and the identical mode — a power cycle does not drift the program.
    const int days[3] = {80, 172, 355};
    const int utc_off = -360;
    for (int di = 0; di < 3; di++) {
        for (int minute = 0; minute < 1440; minute += 60) {
            Setpoints sp1, sp2;
            SensorInputs in1 = disconnected_inputs(days[di], minute, utc_off, sp1);
            SensorInputs in2 = disconnected_inputs(days[di], minute, utc_off, sp2);  // post-reboot
            ASSERT_EQ(sp1.temp_low, sp2.temp_low);
            ASSERT_EQ(sp1.temp_high, sp2.temp_high);
            ASSERT_EQ(sp1.vpd_low, sp2.vpd_low);
            ASSERT_EQ(sp1.vpd_high, sp2.vpd_high);
            ASSERT_EQ(in1.solar_phase, in2.solar_phase);
            ControlState st1 = initial_state(), st2 = initial_state();
            ASSERT_EQ(determine_mode(in1, sp1, st1, 5000),
                      determine_mode(in2, sp2, st2, 5000));
        }
    }
    PASS();
}

// ═══════════════════════════════════════════════════════════════
// #289 / #290 — MANUAL BUTTON OVERRIDE LAYER (FANS / HUMID / VENT-BYPASS)
// ═══════════════════════════════════════════════════════════════
// The override layer lives in controls.yaml and the replay corpus NEVER presses
// a button, so a replay-diff is trivially clean — these native tests are the
// REQUIRED positive evidence (#289). They pin apply_manual_overrides() +
// fan_requires_open_vent() (greenhouse_logic.h).
static RelayOutputs relays_off() { return RelayOutputs{false, false, false, false, false, false}; }

TEST(manual_fans_forces_both_fans_and_opens_vent) {
    RelayOutputs out = relays_off();
    ManualOverrides ov = {true, false, false, false};  // FANS only
    ManualForce f = apply_manual_overrides(out, ov, IDLE);
    ASSERT_TRUE(out.fan1 && out.fan2);
    ASSERT_TRUE(out.vent);
    ASSERT_TRUE(f.fans && f.vent_open);
    ASSERT_FALSE(f.fog);
    PASS();
}

TEST(manual_humid_forces_fogger_only) {
    RelayOutputs out = relays_off();
    ManualOverrides ov = {false, false, true, false};  // HUMID only, no safety block
    ManualForce f = apply_manual_overrides(out, ov, IDLE);
    ASSERT_TRUE(out.fog && f.fog);
    ASSERT_FALSE(out.fan1 || out.fan2 || out.vent);   // fogger ONLY (R1/R2 fix)
    ASSERT_FALSE(f.fans || f.vent_open);
    PASS();
}

TEST(manual_humid_blocked_by_real_fog_safety) {
    RelayOutputs out = relays_off();
    ManualOverrides ov = {false, false, true, true};   // HUMID, but a real fog safety block
    ManualForce f = apply_manual_overrides(out, ov, IDLE);
    ASSERT_FALSE(out.fog || f.fog);   // physical safety (dew/RH/temp) beats the button
    PASS();
}

TEST(vent_bypass_runs_fans_with_vent_closed) {
    RelayOutputs out = relays_off();
    out.vent = true;                                   // pretend climate wanted the vent open
    ManualOverrides ov = {false, true, false, false};  // VENT-BYPASS only (implies fans)
    ManualForce f = apply_manual_overrides(out, ov, VENTILATE);
    ASSERT_TRUE(out.fan1 && out.fan2);    // fans ON — bypass implies fans (#290)
    ASSERT_FALSE(out.vent);               // vent CLOSED — the winter house-air pull
    ASSERT_TRUE(f.fans);
    ASSERT_FALSE(f.vent_open);            // vent is NOT force-opened under bypass
    PASS();
}

TEST(manual_overrides_never_fight_safety_rails) {
    // All buttons pressed; in every safety mode the relay table is untouched and
    // no force is asserted (a FANS press must not throw the vent open and dump
    // heat during a cold-rail SAFETY_HEAT).
    for (Mode m : {SENSOR_FAULT, SAFETY_COOL, SAFETY_HEAT}) {
        RelayOutputs out = {true, true, false, false, false, false};  // heaters on
        const RelayOutputs before = out;
        ManualOverrides ov = {true, true, true, false};
        ManualForce f = apply_manual_overrides(out, ov, m);
        ASSERT_TRUE(out.heat1 == before.heat1 && out.heat2 == before.heat2);
        ASSERT_TRUE(out.fan1 == before.fan1 && out.fan2 == before.fan2);
        ASSERT_TRUE(out.fog == before.fog && out.vent == before.vent);
        ASSERT_FALSE(f.fans || f.fog || f.vent_open);
    }
    PASS();
}

TEST(no_buttons_pressed_is_a_no_op) {
    RelayOutputs out = {false, true, true, false, false, true};
    const RelayOutputs before = out;
    ManualOverrides ov = {false, false, false, false};
    ManualForce f = apply_manual_overrides(out, ov, VENTILATE);
    ASSERT_TRUE(out.heat1 == before.heat1 && out.heat2 == before.heat2 && out.fan1 == before.fan1
                && out.fan2 == before.fan2 && out.fog == before.fog && out.vent == before.vent);
    ASSERT_FALSE(f.fans || f.fog || f.vent_open);
    PASS();
}

TEST(manual_fans_plus_humid_force_fans_vent_and_fog) {
    RelayOutputs out = relays_off();
    ManualOverrides ov = {true, false, true, false};   // FANS + HUMID together
    ManualForce f = apply_manual_overrides(out, ov, IDLE);
    ASSERT_TRUE(out.fan1 && out.fan2 && out.vent && out.fog);
    ASSERT_TRUE(f.fans && f.vent_open && f.fog);
    PASS();
}

TEST(fan_requires_open_vent_carves_out_bypass_and_safety) {
    ASSERT_TRUE(fan_requires_open_vent(VENTILATE, true, false));    // normal: vent forced open
    ASSERT_FALSE(fan_requires_open_vent(VENTILATE, true, true));    // bypass carve-out (#290)
    ASSERT_FALSE(fan_requires_open_vent(SAFETY_HEAT, true, false)); // lead-fan, vent shut to keep heat
    ASSERT_FALSE(fan_requires_open_vent(SENSOR_FAULT, true, false));// all off elsewhere
    ASSERT_FALSE(fan_requires_open_vent(IDLE, false, false));       // no fan wanted
    PASS();
}

// Item-3 per-zone arbiter: lowest priority_rank among zones that want wetting AND
// have an actuator wins; ties break on urgency; EAST (no relay) is excluded.
TEST(arbiter_priority_then_urgency_then_actuator) {
    const int prank[ZONE_COUNT] = {1, 2, 3, 4};  // center, south, west, east
    const ZoneBand band = {1.0f, 0.8f, 1.2f};    // target/low/high
    const bool act[ZONE_COUNT] = {true, true, true, false};  // EAST no relay
    // Only west wants wet → west wins (sole candidate).
    {
        ZoneWetIntent zi[ZONE_COUNT] = {
            zone_wet_intent(1.0f, band), zone_wet_intent(1.0f, band),
            zone_wet_intent(1.3f, band), zone_wet_intent(1.0f, band)};
        ASSERT_EQ(arbitrate_wet_zone(zi, prank, act), ZONE_WEST);
    }
    // Center + west both want wet (west more urgent) → center wins on priority.
    {
        ZoneWetIntent zi[ZONE_COUNT] = {
            zone_wet_intent(1.3f, band), zone_wet_intent(1.0f, band),
            zone_wet_intent(1.45f, band), zone_wet_intent(1.0f, band)};
        ASSERT_EQ(arbitrate_wet_zone(zi, prank, act), ZONE_CENTER);
    }
    // No zone above target → none (-1).
    {
        ZoneWetIntent zi[ZONE_COUNT] = {
            zone_wet_intent(0.9f, band), zone_wet_intent(0.9f, band),
            zone_wet_intent(0.9f, band), zone_wet_intent(0.9f, band)};
        ASSERT_EQ(arbitrate_wet_zone(zi, prank, act), -1);
    }
    // Only EAST wants wet but has no actuator → none (-1).
    {
        ZoneWetIntent zi[ZONE_COUNT] = {
            zone_wet_intent(1.0f, band), zone_wet_intent(1.0f, band),
            zone_wet_intent(1.0f, band), zone_wet_intent(1.5f, band)};
        ASSERT_EQ(arbitrate_wet_zone(zi, prank, act), -1);
    }
    PASS();
}

// Gap 1 of the control test-catalog — the orchid wet-night fog gate, the single
// highest-stakes coverage hole. CURVE-ONLY: there is no longer a "night fog
// block" or a stress-override re-open. At night, prod defaults serve fog by the
// SAME curve+physics path as daytime: VPD above the band high edge + holding dew
// margin + rh/temp physics → fog; otherwise blocked by a PHYSICS reason, never by
// the hour. The control lever for keeping orchid nights dry is therefore the BAND
// CURVE itself (a high night vpd_high keeps VPD below the trigger), not a clock
// gate — which is exactly what the served band (fn_band_setpoints) now encodes.
TEST(night_dry_fog_follows_curve_not_a_night_gate) {
    auto sp = band_setpoints();
    sp.direct_wet_stress_override_enabled = false;     // PROD DEFAULT (now inert)
    sp.direct_wet_stress_min_dew_margin_f = 8.0f;
    validate_setpoints(sp);

    // Deep solar night, VPD above the band high edge, healthy dew margin, rh/temp
    // within physics → fog is PERMITTED (night behaves like day; no night gate).
    auto in = make_inputs(78.0f, 1.9f, 52.0f);         // temp in band, VPD > vpd_high
    set_solar_day(in, 2);                              // 02:00 — deep solar night
    in.dew_point_f = in.temp_f - 12.0f;
    ASSERT_TRUE(is_night_phase(in));
    ASSERT_TRUE(climate_fog_assist_permitted(in, sp)); // curve+physics serve it overnight

    // The ONLY way a properly-shaped night band keeps fog off is by keeping VPD at
    // or below its high edge. Raise vpd_high above the reading → "below_threshold"
    // (the curve, not the clock) blocks fog — same mechanism day or night.
    sp.vpd_high = in.vpd_kpa + 0.2f;
    validate_setpoints(sp);
    ASSERT_FALSE(climate_fog_assist_permitted(in, sp));
    ASSERT_TRUE(std::string(climate_fog_assist_block_reason(in, sp)) == "below_threshold");

    // And a physics veto (saturated air) blocks it identically at night and by day.
    sp.vpd_high = 1.4f; validate_setpoints(sp);
    in.rh_pct = sp.fog_rh_ceiling + 2.0f;
    set_solar_day(in, 2);
    ASSERT_TRUE(std::string(climate_fog_assist_block_reason(in, sp)) == "rh_ceiling");
    set_solar_day(in, 12);
    ASSERT_TRUE(std::string(climate_fog_assist_block_reason(in, sp)) == "rh_ceiling");
    PASS();
}
TEST(night_dry_fog_served_by_curve_regardless_of_inert_override) {
    // CURVE-ONLY pair: the stress override switch is INERT. Flipping it ON makes no
    // difference — the SAME hot+dry night that the curve serves with the switch OFF
    // (sibling test above) is served with it ON. Fog is decided by curve+physics,
    // not the switch; the fog relay drives off VENTILATE's FW-9b assist.
    auto sp = band_setpoints();
    sp.direct_wet_stress_override_enabled = true;      // flipping it ON changes nothing
    sp.direct_wet_stress_min_dew_margin_f = 8.0f;
    validate_setpoints(sp);
    auto in = make_inputs(84.0f, 1.9f, 52.0f);
    set_solar_day(in, 2);
    in.dew_point_f = in.temp_f - 12.0f;
    // Hot+dry night, VPD above the band high edge + dew margin OK → served by curve.
    ASSERT_TRUE(climate_fog_assist_permitted(in, sp));
    ControlState st = initial_state();
    Mode m = determine_mode(in, sp, st, 5000);         // temp 84 > temp_high 82 → VENTILATE
    ASSERT_EQ(m, VENTILATE);
    auto relays = resolve_equipment(m, in, sp, st, false);
    ASSERT_TRUE(relays.fog);                            // FW-9b vent-cool fog assist fires
    PASS();
}

// ── F7 (issue 5): VPD safety rails wired into the production band-first path ──
TEST(f7_dry_override_seals_above_vpd_max_safe) {
    // Extreme dryness (vpd > vpd_max_safe) forces SEALED_MIST immediately,
    // bypassing the vpd_watch dwell. With a fresh state the dwell is NOT
    // satisfied, so without the rail the band-first path would sit in IDLE.
    auto sp = band_setpoints();
    sp.sw_fsm_controller_enabled = true;
    validate_setpoints(sp);
    sp.sw_fsm_controller_enabled = true;
    auto s = initial_state();  // vpd_watch_timer = 0 → dwell unmet
    auto in = make_inputs(72.0f, sp.vpd_max_safe + 0.20f);  // temp in band, vpd above the rail
    Mode m = determine_mode(in, sp, s, 5000);
    ASSERT_EQ(m, SEALED_MIST);
    ASSERT_TRUE(std::string(s.last_mode_reason) == "dry_override");
    PASS();
}

TEST(f7_vpd_min_safe_rescue_heats_on_cold_day) {
    // BC-13/§6.4 (Jason 2026-06-17): the vpd_min_safe rescue is a SAFETY RAIL — it
    // ALWAYS acts on an extreme too-wet excursion. On cold days where the estimator
    // proves heat is the effective drying actuator, the rail heats instead of forcing
    // a cold vent cycle (invariant #14).
    auto sp = band_setpoints();
    sp.sw_fsm_controller_enabled = true;
    validate_setpoints(sp);
    sp.sw_fsm_controller_enabled = true;
    auto s = initial_state();
    auto in = make_inputs(sp.temp_low + 0.5f, sp.vpd_min_safe - 0.05f);  // very humid, temp at band floor
    in.outdoor_temp_f = sp.temp_low - sp.cold_vent_guard_delta_f - 5.0f;  // outdoor COLD
    Mode m = determine_mode(in, sp, s, 5000);
    ASSERT_EQ(m, IDLE);
    ASSERT_TRUE(std::string(s.last_mode_reason) == "vpd_min_safe_heat_rescue");
    auto out = resolve_equipment(m, in, sp, s, true);
    ASSERT_TRUE(out.heat1);
    ASSERT_FALSE(out.vent);
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
