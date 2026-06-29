#pragma once
/*
 * greenhouse_types.h — Shared types for greenhouse climate controller.
 * Used by both ESP32 firmware (via ESPHome) and native x86 tests.
 * NO ESPHome dependencies. Pure C++.
 *
 * All floats are single-precision by design — ESP32 has hardware FPU
 * for float but emulates double. Do not use double.
 */

#include <cstdint>
#include <cstddef>
#include <cmath>
#include <algorithm>

// ── Greenhouse operating modes (mutually exclusive, priority-ordered) ──
enum Mode {
    SENSOR_FAULT,     // NaN/invalid/implausible sensor readings — ALL relays off
    SAFETY_COOL,      // temp >= safety_max → vent open, both fans, fog allowed
    SAFETY_HEAT,      // temp <= safety_min → vent closed, both heaters
    SEALED_MIST,      // VPD > band ceiling → vent closed, fans off, misters pulsing
    THERMAL_RELIEF,   // sealed too long → mandatory vent burst for heat dump
    VENTILATE,        // temp above cooling threshold → vent open, fans on
    DEHUM_VENT,       // VPD < vpd_low → vent open for humidity dump
    IDLE              // everything in band → vent closed, no active equipment
};

static_assert(IDLE == 7, "Mode enum ordering changed — update MODE_NAMES and switch statements");

// Mister sub-stages within SEALED_MIST (owned by this code, not ESPHome).
// BC-14 (ADR0003 §6.6): FOG-FIRST ladder — humidification leads with fog (AquaFog
// ~0.26 GPM, gentlest, lowest water) and ESCALATES UP to the misters (~1 GPM/zone)
// when fog can't hold the VPD target, ordered by water GPM. So the integer order is
// WATCH(idle) → FOG(entry) → S1(misters single-zone) → S2(all-zone, heaviest water).
// The string LITERALS (WATCH/FOG/S1/S2) are unchanged so replay/grafana keep parsing;
// only the enum INTEGER order inverted vs the old misters-first ladder.
enum MistStage { MIST_WATCH, MIST_FOG, MIST_S1, MIST_S2 };
static_assert(MIST_S2 == 3, "MistStage order changed — update MIST_NAMES, invariant #12, controls.yaml mister_state gating");

// IRR-3/IRR-4 center-zone time-anchored cadence override. NONE = use the
// normal center pulse cadence; DAWN/MIDDAY = the controls.yaml center pulse
// uses the denser dawn/midday on/gap instead. Center-zone only — never alters
// west/south/east. Owned by this code (determine_mode), read by controls.yaml.
enum CenterBurst { CENTER_BURST_NONE, CENTER_BURST_DAWN, CENTER_BURST_MIDDAY };

inline constexpr const char* CENTER_BURST_NAMES[] = {"none", "dawn_rehydrate", "midday_drench"};

inline constexpr const char* MODE_NAMES[] = {
    "SENSOR_FAULT", "SAFETY_COOL", "SAFETY_HEAT", "SEALED_MIST",
    "THERMAL_RELIEF", "VENTILATE", "DEHUM_VENT", "IDLE"
};
inline constexpr const char* MIST_NAMES[] = {"WATCH", "FOG", "S1", "S2"};

// ── Sensor inputs (ONLY actual sensor readings, no config) ──
struct SensorInputs {
    float temp_f;           // indoor average temperature (°F)
    float vpd_kpa;          // indoor average VPD (kPa)
    float rh_pct;           // indoor average RH (%)
    float dew_point_f;      // indoor dew point (°F)
    float outdoor_rh_pct;   // outdoor RH (%)
    float enthalpy_delta;   // outdoor - indoor enthalpy (kJ/kg)
    float solar_w_m2;       // Tempest solar radiation (W/m²), used for cooling feed-forward
    float vpd_south;        // zone VPD sensors (NaN-tolerant — checked where used)
    float vpd_west;
    float vpd_east;
    int   local_hour;       // 0-23, from SNTP
    bool  occupied;         // greenhouse occupancy (from Sentinel)
    // Sprint-15: outdoor readings used by the summer-vent gate.
    // outdoor_temp_f + outdoor_dewpoint_f drive the cooler+drier comparator.
    // outdoor_data_age_s is the staleness check (when source goes silent we
    // fall back to the existing decision cascade).
    float    outdoor_temp_f;        // outdoor air temperature (°F, Tempest primary; legacy pulled fallback if present)
    float    outdoor_dewpoint_f;    // outdoor dewpoint (°F, computed from temp+RH at caller)
    uint32_t outdoor_data_age_s;    // seconds since last outdoor reading update
    // ── SAF-1 / SF1: sensor-degraded flag ──
    // Set true by the caller when the indoor AVERAGE temp/RH probes are
    // unavailable (avg_ok==false) but a fallback probe (case) still yields a
    // PLAUSIBLE temp — so sensors_plausible() is true but the VPD/RH the
    // controller is acting on is fabricated, not measured. This is BROADER
    // than the all-probes-NaN SENSOR_FAULT (which makes sensors_plausible()
    // false → all relays off). In the degraded regime the controller must NOT
    // chase a fabricated VPD with humidification/dehum/fog; instead it falls
    // back to conservative TIMED center wetting (the dawn/midday bursts at
    // their calibrated volume) and temp-only safety/vent/heat control. Defaults
    // to false so existing call sites and tests are unaffected.
    bool     sensor_degraded;
    // ── Firmware-v2 on-chip solar inputs (contract §B1) ──────────────────
    // The caller (controls.yaml / tests / replay) computes these ONCE per
    // cycle from the time source + compute_greenhouse_solar_times() and the
    // controller gates every time-of-day rule on SOLAR PHASE instead of
    // wall-clock hours, so the diurnal program tracks the drifting sun with
    // zero network. Defaults keep legacy aggregate initializations valid:
    // now_minute -1 → derive from local_hour; solar_phase NAN → crude
    // fallback_solar_phase(local_hour).
    int   now_minute = -1;        // minutes from local midnight
    int   sunrise_min = 360;      // on-chip ephemeris, minutes local
    int   solar_noon_min = 780;
    int   sunset_min = 1200;
    float solar_phase = NAN;      // [0,4): 0=SR 1=SM 2=SS 3=solar-midnight
};

// ── Setpoints (band + planner tunables + firmware config) ──
struct Setpoints {
    float temp_high;
    float temp_low;
    float vpd_high;
    float vpd_low;
    float bias_cool;
    float bias_heat;
    float vpd_hysteresis;
    float temp_hysteresis;
    float heat_hysteresis;
    float dH2;
    float dC2;
    float safety_max;
    float safety_min;
    float vpd_max_safe;
    float vpd_min_safe;
    uint32_t sealed_max_ms;
    uint32_t relief_duration_ms;
    uint32_t vpd_watch_dwell_ms;
    uint32_t mist_s2_delay_ms;
    uint32_t max_relief_cycles;   // R2-6: consecutive sealed→relief cycles before forced vent
    float fog_escalation_kpa;
    float fog_rh_ceiling;
    float fog_min_temp;
    float dehum_aggressive_kpa;
    bool  occupancy_inhibit;
    bool  econ_block;
    // Sprint-10 0.4b: magic numbers extracted from greenhouse_logic.h.
    // Defaults in default_setpoints() match the pre-sprint-10 constants.
    uint32_t vent_latch_timeout_ms;    // was hardcoded 1800000 (30 min)
    float    safety_max_seal_margin_f; // was hardcoded 5.0 (two places)
    float    econ_heat_margin_f;       // was ECON_HEAT_MARGIN_F const
    // Sprint-11: day/night pairs removed. Two-band model: safety rails +
    // the one dispatcher-pushed band (temp_low/temp_high/vpd_low/vpd_high
    // above). Day/night phasing is the dispatcher's job — firmware does
    // not model temporal modes. See feedback_band_architecture memory.
    // Sprint-15: summer thermal-driven vent preference gate.
    // When sw_summer_vent_enabled AND outdoor temp is at least
    // vent_prefer_temp_delta_f below indoor AND outdoor dewpoint is at
    // least vent_prefer_dp_delta_f below indoor dewpoint AND outdoor
    // data is fresher than outdoor_staleness_max_s, the gate pre-empts
    // VPD-seal entry and falls through to VENTILATE. Safety rails,
    // THERMAL_RELIEF, and DEHUM_VENT remain untouched. See
    // docs/firmware-sprint-15-summer-vent-spec.md.
    bool     sw_summer_vent_enabled;       // master enable; default true
    float    vent_prefer_temp_delta_f;     // outdoor must be ≥ this much cooler (°F)
    float    vent_prefer_dp_delta_f;       // outdoor DP must be ≥ this much lower (°F)
    uint32_t outdoor_staleness_max_s;      // gate disables when outdoor data older than this (s)
    uint32_t summer_vent_min_runtime_s;    // min VENTILATE dwell after gate fires (s; reserved)
    // Phase-2 (firmware stabilization plan): mode-transition dwell gate.
    // When sw_dwell_gate_enabled is true, non-safety mode transitions are
    // held for at least dwell_gate_ms after the last accepted transition.
    // Safety rails, R2-3 dry override, and vpd_min_safe rescue preempt the
    // dwell. Projected 80% reduction in whipsaw events (Explore B: 59 mode
    // changes in 2h on 2026-04-17 stress window). Default OFF until replay
    // validation and operator approval.
    bool     sw_dwell_gate_enabled;
    uint32_t dwell_gate_ms;                // default 300000 (5 min)
    // Unified band-first controller. Kept as a compatibility/readback field,
    // but production firmware validates it ON so there is one live controller
    // path across planner, dispatcher, and ESP32.
    bool     sw_fsm_controller_enabled;
    // AI cooling aggression policy. These make the active band-first cooling
    // behavior explicit instead of deriving it from the legacy dC2/temp
    // hysteresis fields.
    float    cool_stage2_over_high_f;       // fan2 engages this far over temp_high
    float    cool_stage2_exit_hysteresis_f; // BC-8: fan2 de-escalation gap below the stage-2 entry (latch hold band)
    float    cool_exit_hysteresis_f;        // VENTILATE exits at temp_high - this
    float    cold_vent_guard_delta_f;       // outdoor < temp_low - delta is cold-vent guarded
    bool     cool_all_fans_at_high_enabled; // run both fans immediately above high edge
    // ── Firmware-v2 stress-wetting rule (replaces direct_wet_stress_* +
    // fog_stress_window_* clock windows; contract §B4). ONE generic rule:
    // wetting past the routine taper is permitted only while VPD exceeds the
    // band's stress-high edge by the margin AND the dew margin holds. The
    // band edges themselves are solar-curved, so "how dry is too dry" already
    // varies through the day — no clock caps needed.
    bool     direct_wet_stress_override_enabled; // stress-wetting master enable
    float    direct_wet_stress_vpd_margin_kpa;   // VPD excess over vpd_high required
    float    direct_wet_stress_min_dew_margin_f; // minimum temp-dewpoint spread for wetting
    uint32_t mist_backoff_ms;
    // ── Firmware-v2 night rule (generic; replaces the ENV-2 clock window) ──
    // Never heat to chase humidity at night (solar phase >= 2.0). The rule is
    // crop-agnostic: night heating for VPD wastes gas and collapses the
    // day/night drop every crop in this house wants. Safety heat untouched.
    bool     sw_night_econ_heat_suppress_enabled; // default true
    // ── Firmware-v2 wet taper (replaces the dusk_cutoff_hour clock rail) ──
    // Routine wetting ceases wet_taper_before_sunset_min before the ON-CHIP
    // sunset and stays off through the night half (phase >= 2), so foliage
    // dries before dark on every day of the year without a dispatcher push.
    // Night emergencies are handled by sw_night_stress_wet_enabled below.
    bool     sw_wet_taper_enabled;                // default true
    int      wet_taper_before_sunset_min;         // default 120
    // ── Firmware-v2 night emergency wetting (replaces the CYC-4 micropulse
    // path). At night the solar band's VPD edges are the LOW night band, so
    // "VPD above band-high + stress margin" IS the overnight emergency
    // definition. When enabled, the stress-wet rule may fire at night under a
    // stricter dew margin; the normal SEALED_MIST machinery + SAF-4 duty caps
    // + relay min-on/off bound the actual water. No bespoke pulse path.
    bool     sw_night_stress_wet_enabled;         // default true
    float    night_stress_min_dew_margin_f;       // BC-7: night wetting dew-margin (>= day); default 10.0
    // ── FRT-6 / FRT-7 (post-feed absorption hold) ──
    // Set true by controls.yaml while now_ms < feed_hold_until_ms (the
    // shared globals.yaml timestamp armed when a fertilizer feed completes).
    // During the hold, ALL clean wetting (center_mister, fog_rly, clean
    // drips) is blocked so the velamen absorbs the feed before a rinse.
    // The post-fert clean flush is relocated to fire AFTER the hold.
    bool     feed_hold_active;                     // default false; controls.yaml computes from feed_hold_until
    // ── IRR-3 (dawn rehydrate) / IRR-4 (midday drench) ──
    // Two time-anchored CENTER-ZONE mist cadence OVERRIDES for the bare-root
    // Vanda. They do NOT create a new relay path or a new mode — they only
    // make the existing center-mist pulse cadence (controls.yaml PULSE_ON/GAP)
    // denser inside two short pre-dusk windows, so the velamen re-saturates
    // after the overnight dry-down (dawn) and gets a deeper midday soak at the
    // solar/thermal peak. West/south/east cadence is untouched. Both windows
    // remain inside the FSM + every safety rail: the dawn/midday burst is
    // permitted ONLY when the center is already eligible to wet (climate wet
    // assist gate: not feed-hold, not past dusk, dew margin OK, VPD above the
    // center target), so the SAF-4 duty cap, daily-volume ceiling, FRT-6
    // absorption hold, CYC-1 dusk cutoff, and SENSOR_FAULT all still bound it.
    //
    // Firmware-v2: the boost windows anchor to the ON-CHIP solar times via
    // offsets (dawn = sunrise + dawn_boost_offset_min; midday = solar-noon +
    // midday_boost_offset_min) so they track the sun all year with no
    // dispatcher push. The cadence knobs are generic zone-boost parameters
    // the planner can tune; nothing Vanda-specific remains in the firmware.
    bool     sw_dawn_rehydrate_enabled;            // default true; dawn-boost master enable
    int      dawn_boost_offset_min;                // window start = sunrise + this (default 0)
    int      dawn_rehydrate_window_min;            // dawn window duration, minutes (default 12)
    int      dawn_rehydrate_on_s;                  // center pulse ON during dawn window (default 90s)
    int      dawn_rehydrate_gap_s;                 // center pulse GAP during dawn window (default 20s)
    bool     sw_midday_drench_enabled;             // default true; midday-boost master enable
    int      midday_boost_offset_min;              // window start = solar-noon + this (default 60)
    int      midday_drench_window_min;             // midday window duration, minutes (default 11)
    int      midday_drench_on_s;                   // center pulse ON during midday window (default 120s)
    int      midday_drench_gap_s;                  // center pulse GAP during midday window (default 25s)
    // ── Firmware-v2 served band targets (telemetry + delta tracking) ──────
    // Computed each cycle from the on-chip anchor curves alongside the
    // low/high edges above. The FSM still keys on the edges; the targets feed
    // the house delta telemetry and the normalized temp-vs-VPD arbitration.
    float    temp_target;
    float    vpd_target;
    // ADR-0004: control-band tracking tightness in [0,1]. 0 = float inside the
    // served crop corridor and act only at temp_low/high and vpd_low/high. Nonzero
    // pinches the CONTROL band toward temp_target/vpd_target for explicit operator
    // diagnostics. Safety rails and the served band are unchanged; only where the
    // controller chooses to act tightens.
    float    band_track_fraction;
    // BC-13 (ADR0003 §6.4): deterministic outdoor-aware moisture-exchange selector.
    float    dehum_vent_gain_margin_kpa;     // min projected VPD-toward-target gain to act (anti-thrash)
    uint32_t dehum_heat_assist_min_dwell_ms; // min continuous DEHUM_VENT dwell before heat1 co-run arms
    float    vent_exchange_fraction;         // AI-tunable single-cycle air-exchange fraction toward outdoor
};

static constexpr uint32_t STATE_SENTINEL = 0xBEEF0042;
// Sprint-10 0.4b: ECON_HEAT_MARGIN_F moved into Setpoints as
// econ_heat_margin_f (keeping the old constant removed intentionally to
// prevent accidental re-use). Same for the former `1800000` vent-latch
// timeout and the `5.0f` safety_max seal margin.

// ── Persistent control state ──
struct ControlState {
    uint32_t sentinel;
    Mode mode;
    Mode mode_prev;
    MistStage mist_stage;
    uint32_t sealed_timer_ms;
    uint32_t relief_timer_ms;
    uint32_t vpd_watch_timer_ms;
    uint32_t mist_stage_timer_ms;
    uint32_t relief_cycle_count;
    uint32_t vent_latch_timer_ms;  // FW-8: tracks time in relief-exhausted VENTILATE latch
    // OBS-1e (Sprint 16 patch): set by determine_mode() R2-3 dry-override path
    // on cycles where the firmware forces a SEAL the planner's dwell hadn't
    // yet sanctioned. Read by evaluate_overrides() — cannot be reconstructed
    // post-hoc because R2-3 mutates vpd_watch_timer_ms in the same cycle.
    bool dry_override_active;
    // Sprint-9 P1#7: Stage-2 heater latch. Set when temp < Tlow - dH2.
    // Cleared when temp >= Tlow + heat_hysteresis (S1 satisfied). Prevents
    // gas-valve rapid cycling in the hysteresis band between the two
    // thresholds. Managed by determine_mode; read by resolve_equipment.
    bool heat2_latched;
    // BC-8 (ADR0003 §6.5): cooling-fan stage-2 latch, symmetric twin of
    // heat2_latched. Set when temp ≥ temp_high + cool_stage2_over_high_f; cleared
    // when temp drops below that minus cool_stage2_exit_hysteresis_f. Holds in the
    // hysteresis band so the second fan cannot chatter (the 90s relay min-off floor
    // is a time dwell, not a temperature hysteresis). Managed by determine_mode;
    // read by resolve_equipment (const) — same contract as heat2_latched.
    bool fan2_latched;
    // BC-13 (ADR0003 §6.4): DEHUM_VENT heat-assist min-dwell timer (accrues only
    // while continuously in the too-wet dehum direction) + the resolve_equipment-
    // visible armed flag. Managed by determine_mode; read by resolve_equipment (const).
    uint32_t dehum_heat_assist_timer_ms;
    bool dehum_heat_assist_active;
    // Sprint-15: telemetry flag — set true on each cycle the summer-vent
    // gate is actively suppressing a VPD-seal entry. Read by
    // evaluate_overrides() and surfaced via OverrideFlags. No persisted
    // timer; freshly evaluated each cycle.
    bool override_summer_vent;
    // Sprint-15.1 fix 8: which branch of determine_mode() chose the
    // current mode. Short string literal — no heap allocation. Read by
    // controls.yaml for the `gh_mode_reason` text sensor. Defaults to
    // a benign literal on initial_state so consumers can safely publish
    // before determine_mode has run.
    const char* last_mode_reason;
    // Phase-2: monotonic tick count (ms, saturating) at which the most
    // recent mode transition was accepted. Drives the dwell gate —
    // new mode proposals within dwell_gate_ms of this timestamp are
    // held (unless safety preempts). Updated by determine_mode on each
    // non-held transition. Initialized to 0 which means "already past
    // dwell at boot" so the first real decision isn't gated.
    uint32_t last_transition_tick_ms;
    // band-first controller: lockout after a sealed humidification attempt times out.
    // During this window the firmware suppresses new SEALED_MIST entries while
    // still allowing normal temp/dehum control.
    uint32_t mist_backoff_timer_ms;
    // band-first controller: moisture assist while the temp band requires VENTILATE but
    // VPD is also above band. controls.yaml uses this to allow directional
    // misters during vent cooling without pretending the mode is SEALED_MIST.
    bool vent_mist_assist_active;
    // IRR-3/IRR-4: which (if any) time-anchored CENTER-zone cadence override is
    // active this cycle. Freshly evaluated every cycle by determine_mode from
    // center_burst_decision(); never persisted across a reset. controls.yaml
    // reads it to swap the center pulse ON/GAP for the denser dawn/midday
    // cadence. CENTER_BURST_NONE on safety/fault/blocked cycles.
    CenterBurst center_burst;
};

struct RelayOutputs {
    bool heat1;
    bool heat2;
    bool fan1;
    bool fan2;
    bool fog;
    bool vent;
};

// ── Manual button-override layer (FANS / HUMID / VENT-BYPASS) ────────────
// The three momentary panel buttons each arm a deadline latch (handled in
// controls.yaml). ManualOverrides is the per-cycle EFFECTIVE latch state +
// whether a genuine fog safety block is active; apply_manual_overrides() turns
// it into forced relay outputs. The pure function makes the override precedence
// unit-testable (the replay corpus never exercises button presses).
struct ManualOverrides {
    bool fans_active;        // FANS button (or legacy manual_fan_active)
    bool vent_bypass_active; // VENT-BYPASS button (or legacy vent_lock_active)
    bool humid_active;       // HUMID button (or legacy manual_fog_active)
    bool fog_safety_blocked; // a real fog safety block (dew/RH/temp/time/leak) is up
};

// Which relays the override FORCE-applies (force_on bypasses relay min-off dwell
// so a press engages within one loop tick — the #289 fix). Returned by
// apply_manual_overrides() so the caller wires force_on= on exactly those relays.
struct ManualForce {
    bool fans;       // force both fans on
    bool fog;        // force fogger on
    bool vent_open;  // force vent open (normal FANS); NOT set during VENT-BYPASS
};

// ── ClimateIntent candidate-action controller contract ────────────────
// These types mirror verdify_schemas.climate_intent. Firmware uses them as the
// single climate decision surface; relay mechanics and interlocks remain local.
enum ClimateAction {
    CLIMATE_SENSOR_FAULT,
    CLIMATE_SAFETY_HEAT,
    CLIMATE_SAFETY_COOL,
    CLIMATE_HEAT,
    CLIMATE_IDLE,
    CLIMATE_VENT_COOL,
    CLIMATE_VENT_COOL_MIST_ASSIST,
    CLIMATE_VENT_COOL_FOG_ASSIST,
    CLIMATE_SEALED_HUMIDIFY,
    CLIMATE_SEALED_FOG,
    CLIMATE_DEHUM_VENT
};

static_assert(CLIMATE_DEHUM_VENT == 10, "ClimateAction enum changed — update CLIMATE_ACTION_NAMES and schemas");

inline constexpr const char* CLIMATE_ACTION_NAMES[] = {
    "SENSOR_FAULT",
    "SAFETY_HEAT",
    "SAFETY_COOL",
    "HEAT",
    "IDLE",
    "VENT_COOL",
    "VENT_COOL_MIST_ASSIST",
    "VENT_COOL_FOG_ASSIST",
    "SEALED_HUMIDIFY",
    "SEALED_FOG",
    "DEHUM_VENT"
};

enum ClimatePriorityAxis {
    CLIMATE_PRIORITY_SAFETY,
    CLIMATE_PRIORITY_TEMP,
    CLIMATE_PRIORITY_VPD,
    CLIMATE_PRIORITY_RESOURCE
};

inline constexpr const char* CLIMATE_PRIORITY_AXIS_NAMES[] = {
    "safety",
    "temp",
    "vpd",
    "resource"
};

enum ClimateMoistureAssistState {
    CLIMATE_MOISTURE_INACTIVE,
    CLIMATE_MOISTURE_ENGAGE_DELAY,
    CLIMATE_MOISTURE_PULSE_ON,
    CLIMATE_MOISTURE_PULSE_GAP,
    CLIMATE_MOISTURE_BLOCKED,
    CLIMATE_MOISTURE_SERVED
};

inline constexpr const char* CLIMATE_MOISTURE_ASSIST_STATE_NAMES[] = {
    "inactive",
    "engage_delay",
    "pulse_on",
    "pulse_gap",
    "blocked",
    "served"
};

enum ClimateMoistureZone {
    CLIMATE_ZONE_NONE,
    CLIMATE_ZONE_SOUTH,
    CLIMATE_ZONE_WEST,
    CLIMATE_ZONE_CENTER,
    CLIMATE_ZONE_ALL
};

inline constexpr const char* CLIMATE_MOISTURE_ZONE_NAMES[] = {
    "none",
    "south",
    "west",
    "center",
    "all"
};

struct ClimateCandidateProjection {
    ClimateAction action;
    bool safety_ok;
    const char* blocked_reason;
    float projected_temp_error_f;
    float projected_vpd_error_kpa;
    float resource_cost;
    float relay_churn_cost;
    float confidence;
    float prior_action_hold_preference;
    // ── Firmware-v2 normalized arbitration (owner's "equality statement") ──
    // Raw °F and kPa are incomparable units, so candidates are ordered by
    // BAND-NORMALIZED error (error / band half-width). vpd_axis_leads is set
    // per cycle from the CURRENT normalized deltas: whichever axis is further
    // from its band (in band-widths) is compared first.
    float norm_temp_error = 0.0f;
    float norm_vpd_error = 0.0f;
    bool  vpd_axis_leads = false;
};

struct ClimateResourceCostEstimate {
    float water_gal;
    float electric_kwh;
    float gas_therm;
};

struct ClimateActionDecision {
    ClimateAction climate_action;
    ClimatePriorityAxis priority_axis;
    float temp_error_f;
    float vpd_error_kpa;
    const char* candidate_summary;
    ClimateMoistureAssistState moisture_assist_state;
    ClimateMoistureZone moisture_zone;
    float next_mist_eligible_s;
    float fog_margin_kpa;
    const char* fog_block_reason;
    ClimateResourceCostEstimate resource_cost_estimate;
    const char* moisture_exchange_action;
    const char* moisture_exchange_reason;
    float moisture_vent_vpd_gain_kpa;
    float moisture_heat_vpd_gain_kpa;
    bool moisture_outdoor_fresh;
    bool moisture_vent_overcools;
    bool moisture_heat_assist_corun;
    bool moisture_heat_assist_active;
    uint32_t moisture_heat_assist_timer_ms;
};

inline bool climate_candidate_precedes(
    const ClimateCandidateProjection& a,
    const ClimateCandidateProjection& b
) noexcept {
    // Firmware-v2: order by band-normalized error, leading axis chosen by the
    // larger CURRENT normalized delta (a and b carry the same vpd_axis_leads,
    // set once per cycle in evaluate_climate_decision). Falls back to the raw
    // lexicographic ordering when normalization was not populated (legacy
    // callers/tests construct projections with zero norms).
    const bool has_norms = (a.norm_temp_error != 0.0f || a.norm_vpd_error != 0.0f
                         || b.norm_temp_error != 0.0f || b.norm_vpd_error != 0.0f);
    if (has_norms) {
        const float a1 = a.vpd_axis_leads ? a.norm_vpd_error  : a.norm_temp_error;
        const float b1 = b.vpd_axis_leads ? b.norm_vpd_error  : b.norm_temp_error;
        const float a2 = a.vpd_axis_leads ? a.norm_temp_error : a.norm_vpd_error;
        const float b2 = b.vpd_axis_leads ? b.norm_temp_error : b.norm_vpd_error;
        if (a1 != b1) return a1 < b1;
        if (a2 != b2) return a2 < b2;
    } else {
        if (a.projected_temp_error_f != b.projected_temp_error_f) {
            return a.projected_temp_error_f < b.projected_temp_error_f;
        }
        if (a.projected_vpd_error_kpa != b.projected_vpd_error_kpa) {
            return a.projected_vpd_error_kpa < b.projected_vpd_error_kpa;
        }
    }
    // BC-4 (band-compliance regime): resource_cost / relay_churn_cost are NO
    // LONGER arbitration tiebreakers — cost/energy is not a driver here, band
    // tracking is. After band-normalized error, the only tiebreak is the
    // hold-preference (prefer the prior action), which is anti-chatter, not
    // cost. Relay wear is bounded by per-relay min-on/off dwell, not by biasing
    // the climate decision toward cheaper/idler actions.
    return a.prior_action_hold_preference > b.prior_action_hold_preference;
}

inline int choose_climate_candidate_index(
    const ClimateCandidateProjection* candidates,
    std::size_t count
) noexcept {
    int selected = -1;
    for (std::size_t i = 0; i < count; i++) {
        if (!candidates[i].safety_ok) continue;
        if (selected < 0 || climate_candidate_precedes(candidates[i], candidates[selected])) {
            selected = static_cast<int>(i);
        }
    }
    return selected;
}

// ── Supplemental lighting state machines ───────────────────────────────
// Each Lutron circuit is evaluated independently. The crop/DLI policy can seed
// both circuits with a default photoperiod, but runtime control is based on
// qualified light minutes: natural lux above the circuit threshold OR actual
// switch-on time. Natural and switch light are counted once via OR semantics.
struct LightingInputs {
    float natural_lux;      // Plant-control lux; caller may use Tempest or indoor fallback.
    float exterior_lux;     // Tempest outdoor lux, only valid for occupancy when fresh.
    bool exterior_lux_fresh;
    int local_hour;         // 0-23, from SNTP
    bool occupied;          // Occupancy enables lux-gated task-light demand.
    // #295: on-chip ephemeris (minutes local). When solar_phasing is enabled
    // (LightingSetpoints.solar_phasing) the photoperiod window is anchored to
    // these instead of the static start_hour/cutoff_hour. Default 0 → caller did
    // not wire the solar fields → the window falls back to the fixed clock hours.
    int local_minute = 0;   // 0-59, from SNTP (with local_hour gives minute-of-day)
    int sunrise_min = 0;    // on-chip sunrise, minutes local; 0 ⇒ unavailable
    int sunset_min = 0;     // on-chip sunset, minutes local; 0 ⇒ unavailable
};

struct LightingSetpoints {
    uint32_t target_light_minutes;
    float lux_on_threshold;
    float lux_hysteresis;
    int start_hour;
    int cutoff_hour;
    uint32_t min_on_ms;
    uint32_t min_off_ms;
    bool auto_enabled;
    // #295: solar-phase the per-circuit photoperiod. When true AND the caller
    // supplied non-zero sunrise/sunset minute fields, the window tracks real
    // sunrise+start_offset_min .. sunset+cutoff_offset_min instead of the static
    // clock hours. Backward-safe: false (or zero solar fields) ⇒ fixed-hour path.
    bool solar_phasing = false;
    int sunrise_offset_min = 0;  // window opens sunrise + this (may be negative)
    int sunset_offset_min = 0;   // window closes sunset + this (may be negative)
    // #295: dead-fixture / phantom-DLI suppression. When a circuit's physical
    // fixture is known-dead (shorted/unplugged), the operator sets this so the
    // circuit no longer credits supplemental DLI or accrues qualified-light
    // minutes — the dark fixture can no longer "meet" the photoperiod on paper.
    bool out_of_service = false;
};

static constexpr uint32_t LIGHT_STATE_SENTINEL = 0xBEEF0043;

struct LightingState {
    uint32_t sentinel;
    bool on;
    uint32_t last_transition_tick_ms;
    float qualified_light_s;
    float natural_qualified_s;
    float switch_on_s;
    float overlap_s;
    int last_count_hour;
    const char* last_reason;
};

struct LightingDecision {
    bool want_on;
    bool in_window;
    bool minutes_below_target;
    bool natural_qualified;
    bool lux_below_on_threshold;
    bool lux_below_off_threshold;
    bool exterior_lux_available;
    bool occupancy_task_light_demand;
    bool plant_supplement_demand;
    bool out_of_service;        // #295: circuit flagged dead → switch-ON credits no light
    float lux_off_threshold;
    float qualified_light_minutes;
    float target_light_minutes;
    float remaining_light_minutes;
    const char* reason;
};

inline LightingState initial_lighting_state() {
    return {
        .sentinel = LIGHT_STATE_SENTINEL,
        .on = false,
        .last_transition_tick_ms = 0,
        .qualified_light_s = 0.0f,
        .natural_qualified_s = 0.0f,
        .switch_on_s = 0.0f,
        .overlap_s = 0.0f,
        .last_count_hour = -1,
        .last_reason = "init"
    };
}

// ── OBS-1e: firmware silent-override audit (Sprint 16) ──
// Each flag captures a specific firmware-side decision that negates or
// blocks planner intent without going through a planner-visible channel.
// Evaluated after determine_mode() by evaluate_overrides(); published to
// ingestor as a comma-separated string, routed to override_events table.
struct OverrideFlags {
    bool occupancy_blocks_equipment; // occupancy inhibit active while quiet equipment was wanted
    bool fog_gate_rh;                // fog wanted but in.rh_pct > fog_rh_ceiling
    bool fog_gate_temp;              // fog wanted but in.temp_f < fog_min_temp
    bool fog_gate_window;            // fog wanted but outside fog_window_start/end
    bool relief_cycle_breaker;       // seal wanted but relief_cycle_count maxed → forced VENTILATE
    bool seal_blocked_temp;          // seal wanted but within 5°F of safety_max
    bool vpd_dry_override;           // firmware sealed for VPD safety without planner dwell
    bool summer_vent_active;         // sprint-15 gate suppressed a VPD-seal in favor of VENTILATE
    bool vent_mist_assist;           // band-first controller VENTILATE is carrying concurrent mister demand
    bool fog_heat_assist;            // band-first controller SEALED_MIST_FOG is humidifying while heat maintains temp
};

// ── Saturating addition (prevents uint32_t overflow at 49.7 days) ──
inline uint32_t sat_add(uint32_t a, uint32_t b) noexcept {
    return (a > UINT32_MAX - b) ? UINT32_MAX : a + b;
}

// ── Defaults ──
// Sprint-11 widening: firmware defaults are PERMISSIVE, bounded only by
// safety rails. The dispatcher pushes the real crop band every planner
// cycle; if the dispatcher is silent, we sit in a wide "don't narrow"
// regime so safety rails are the only active constraint. Two-band model:
// safety rails + one dispatcher-pushed band.
inline Setpoints default_setpoints() {
    return {
        .temp_high = 95.0f, .temp_low = 40.0f,    // wide — dispatcher narrows
        .vpd_high = 2.80f,  .vpd_low = 0.35f,      // wide — dispatcher narrows
        .bias_cool = 0.0f, .bias_heat = 0.0f,
        .vpd_hysteresis = 0.3f, .temp_hysteresis = 1.5f, .heat_hysteresis = 1.0f,  // Phase-2: push temp_hysteresis=2.0 via dispatcher at sw_dwell_gate_enabled flip
        .dH2 = 5.0f, .dC2 = 3.0f,
        .safety_max = 100.0f, .safety_min = 35.0f,
        .vpd_max_safe = 3.0f, .vpd_min_safe = 0.3f,
        .sealed_max_ms = 600000, .relief_duration_ms = 90000,
        .vpd_watch_dwell_ms = 60000, .mist_s2_delay_ms = 300000,
        .max_relief_cycles = 3,
        .fog_escalation_kpa = 0.4f, .fog_rh_ceiling = 90.0f,
        .fog_min_temp = 55.0f,
        .dehum_aggressive_kpa = 0.3f,              // must be < vpd_low
        .occupancy_inhibit = false, .econ_block = false,
        .vent_latch_timeout_ms = 1800000u,
        .safety_max_seal_margin_f = 5.0f,
        .econ_heat_margin_f = 5.0f,
        // Sprint-15: summer-vent gate defaults. Master enable ON by
        // default — pre-sprint-15 firmware sealed against its own
        // best heat sink in summer; explicit operator opt-out is the
        // safer default. Thresholds matched to spec defaults.
        .sw_summer_vent_enabled = true,
        .vent_prefer_temp_delta_f = 5.0f,
        .vent_prefer_dp_delta_f = 5.0f,
        // Sprint-15.1 fix 3: raised from 300s → 600s (2× dispatcher
        // cadence). Pre-15.1 at exactly 300s the staleness comparison
        // flipped false on any dispatcher push jitter, intermittently
        // disabling the gate.
        .outdoor_staleness_max_s = 600u,
        .summer_vent_min_runtime_s = 180u,
        // Phase-2 dwell gate. Default OFF until replay validation and
        // operator approval. 5-min dwell projected to reduce whipsaw 80%.
        .sw_dwell_gate_enabled = false,  // BC-8: armed LIVE via the dispatcher tunable (registry sw_dwell_gate_enabled), not the boot default
        .dwell_gate_ms = 300000u,
        // Library fallback remains legacy so old unit tests/replay rows can
        // still cover the rollback cascade directly. The ESPHome production
        // control loop forces the unified band-first path ON.
        .sw_fsm_controller_enabled = false,
        .cool_stage2_over_high_f = 1.0f,
        .cool_stage2_exit_hysteresis_f = 1.0f,
        .cool_exit_hysteresis_f = 1.5f,
        .cold_vent_guard_delta_f = 10.0f,
        .cool_all_fans_at_high_enabled = false,
        // Firmware-v2 stress-wetting: ON by default — the band edges are now
        // solar-curved, so "above band-high + margin" is a true stress signal
        // at every hour. Dew margin keeps wetting off cold foliage.
        .direct_wet_stress_override_enabled = true,
        .direct_wet_stress_vpd_margin_kpa = 0.05f,
        .direct_wet_stress_min_dew_margin_f = 8.0f,
        .mist_backoff_ms = 600000u,
        // Firmware-v2 night rule: never heat-to-chase-humidity at night
        // (solar phase >= 2). Generic, crop-agnostic.
        .sw_night_econ_heat_suppress_enabled = true,
        // Firmware-v2 wet taper: routine wetting ceases 120 min before the
        // ON-CHIP sunset and through the night half. Tracks the sun all year.
        .sw_wet_taper_enabled = true,
        .wet_taper_before_sunset_min = 120,
        // Firmware-v2 night emergency wetting (replaces CYC-4): the night
        // band's own high edge + stress margin defines the emergency; the
        // normal SEALED_MIST machinery + duty caps bound the water.
        .sw_night_stress_wet_enabled = true,
        .night_stress_min_dew_margin_f = 10.0f,
        // FRT-6: absorption hold inactive until controls.yaml arms it.
        .feed_hold_active = false,
        // Dawn boost: window opens AT on-chip sunrise (offset 0), 12 min,
        // 90s-ON/20s-GAP — denser than the 60/45 base. Generic zone boost.
        .sw_dawn_rehydrate_enabled = true,
        .dawn_boost_offset_min = 0,
        .dawn_rehydrate_window_min = 12,
        .dawn_rehydrate_on_s = 90,
        .dawn_rehydrate_gap_s = 20,
        // Midday boost: window opens solar-noon + 60 min (the thermal peak
        // lags solar max), 11 min, deeper 120s-ON/25s-GAP soak.
        .sw_midday_drench_enabled = true,
        .midday_boost_offset_min = 60,
        .midday_drench_window_min = 11,
        .midday_drench_on_s = 120,
        .midday_drench_gap_s = 25,
        // Served-band targets: neutral defaults; the on-chip curve overwrites
        // these every cycle (they are telemetry/arbitration inputs, not knobs).
        .temp_target = 75.0f,
        .vpd_target = 1.0f,
        // ADR-0004: default float. The target line is a telemetry/arbitration
        // reference, not a control objective. Nonzero tracking remains a deliberate
        // operator/diagnostic escape hatch, but source defaults must not chase the line.
        .band_track_fraction = 0.0f,
        // BC-13 (ADR0003 §6.4): moisture-exchange selector defaults.
        .dehum_vent_gain_margin_kpa = 0.02f,
        .dehum_heat_assist_min_dwell_ms = 300000u,   // 5 min
        .vent_exchange_fraction = 0.30f
    };
}

// Caller must invoke before passing setpoints to determine_mode().
// This function is NOT called automatically — the contract is explicit.
//
// Sprint-9 P2#8: extended with relational asserts so one mis-entered HA
// number can't silently create unreachable modes or mode thrash. Each
// relational clamp picks the "safe side" when the input is inverted so
// the controller stays responsive while logs flag the violation.
inline void validate_setpoints(Setpoints& sp) {
    // --- individual-value clamps (pre-sprint-9) ---
    sp.temp_high = std::max(50.0f, std::min(110.0f, sp.temp_high));
    sp.temp_low = std::max(30.0f, std::min(90.0f, sp.temp_low));
    if (sp.temp_low >= sp.temp_high) sp.temp_low = sp.temp_high - 5.0f;
    sp.vpd_high = std::max(0.3f, std::min(5.0f, sp.vpd_high));
    sp.vpd_low = std::max(0.1f, std::min(sp.vpd_high - 0.1f, sp.vpd_low));
    sp.vpd_hysteresis = std::max(0.05f, std::min(sp.vpd_high * 0.5f, sp.vpd_hysteresis));
    sp.temp_hysteresis = std::max(0.5f, std::min(5.0f, sp.temp_hysteresis));
    sp.heat_hysteresis = std::max(0.0f, std::min(3.0f, sp.heat_hysteresis));
    // Sprint-14: clamp biases to ±5°F. Pre-sprint-14 these were
    // unclamped — a dispatcher push of bias_heat = -50 would disable
    // heating entirely. 30-day observed operational range was
    // bias_cool ∈ [-1, 5], bias_heat ∈ [0, 5]; [-5, 5] covers observed
    // usage and matches the ±5°F margin the rest of validate uses.
    sp.bias_heat = std::max(-5.0f, std::min(5.0f, sp.bias_heat));
    sp.bias_cool = std::max(-5.0f, std::min(5.0f, sp.bias_cool));
    sp.safety_max = std::max(sp.temp_high + 5.0f, std::min(120.0f, sp.safety_max));
    sp.safety_min = std::max(30.0f, std::min(sp.temp_low - 5.0f, sp.safety_min));
    sp.sealed_max_ms = std::max(uint32_t(60000), std::min(uint32_t(1800000), sp.sealed_max_ms));
    sp.relief_duration_ms = std::max(uint32_t(15000), std::min(uint32_t(600000), sp.relief_duration_ms));
    sp.max_relief_cycles = std::max(uint32_t(1), std::min(uint32_t(10), sp.max_relief_cycles));

    // --- sprint-9 P2#8: relational asserts + previously-unclamped scalars ---
    // vpd_min_safe and vpd_max_safe were completely unclamped before
    // sprint-9, which meant bad HA values could make either bound useless.
    sp.vpd_max_safe = std::max(sp.vpd_high + 0.1f, std::min(8.0f, sp.vpd_max_safe));
    sp.vpd_min_safe = std::max(0.05f, std::min(sp.vpd_low - 0.05f, sp.vpd_min_safe));
    // Full ordering: vpd_min_safe < vpd_low < vpd_high < vpd_max_safe.
    // The three clamps above already enforce adjacent pairs; no further
    // fix-up needed as long as vpd_low stayed strictly < vpd_high.

    // Thigh + dC2 must be below safety_max or the planner can demand
    // aggressive cooling right up to the safety threshold with no buffer.
    sp.dC2 = std::max(1.0f, std::min(sp.safety_max - sp.temp_high - 1.0f, sp.dC2));
    if (sp.dC2 < 1.0f) sp.dC2 = 1.0f;  // floor after the relational clamp
    sp.dH2 = std::max(1.0f, std::min(20.0f, sp.dH2));

    // Relief must complete inside the sealed window, else the was_sealed
    // branch's THERMAL_RELIEF backstop can't meaningfully gate re-seal.
    if (sp.relief_duration_ms >= sp.sealed_max_ms) {
        sp.relief_duration_ms = sp.sealed_max_ms / 4;  // 25% of seal window
        if (sp.relief_duration_ms < 15000) sp.relief_duration_ms = 15000;
    }

    // vpd_watch_dwell and mist_s2_delay need non-zero values; zero would
    // make the watchdog fire on every cycle and the S1→S2 promotion
    // happen instantly.
    sp.vpd_watch_dwell_ms = std::max(uint32_t(1000), std::min(uint32_t(600000), sp.vpd_watch_dwell_ms));
    sp.mist_s2_delay_ms   = std::max(uint32_t(1000), std::min(uint32_t(1800000), sp.mist_s2_delay_ms));

    // Fog-specific clamps
    sp.fog_escalation_kpa = std::max(0.1f, std::min(0.5f, sp.fog_escalation_kpa));
    sp.fog_rh_ceiling     = std::max(30.0f, std::min(100.0f, sp.fog_rh_ceiling));
    sp.fog_min_temp       = std::max(30.0f, std::min(100.0f, sp.fog_min_temp));

    // Dehum aggressiveness delta must fit under vpd_low so the
    // "dehum_aggressive" engage threshold (vpd_low - dehum_aggressive_kpa)
    // is positive.
    sp.dehum_aggressive_kpa = std::max(0.05f, std::min(sp.vpd_low - 0.05f, sp.dehum_aggressive_kpa));

    // --- sprint-10 0.4b: magic-number clamps ---
    sp.vent_latch_timeout_ms = std::max(uint32_t(60000),
                                        std::min(uint32_t(7200000), sp.vent_latch_timeout_ms));
    sp.safety_max_seal_margin_f = std::max(1.0f, std::min(15.0f, sp.safety_max_seal_margin_f));
    sp.econ_heat_margin_f       = std::max(1.0f, std::min(15.0f, sp.econ_heat_margin_f));

    // --- sprint-15: summer-vent gate clamps (match spec ranges) ---
    sp.vent_prefer_temp_delta_f = std::max(2.0f, std::min(15.0f, sp.vent_prefer_temp_delta_f));
    sp.vent_prefer_dp_delta_f   = std::max(2.0f, std::min(15.0f, sp.vent_prefer_dp_delta_f));
    // Sprint-15.1 fix 3: floor raised 60→120s (anything below dispatcher
    // cadence disqualifies the gate on every jitter tick).
    sp.outdoor_staleness_max_s  = std::max(uint32_t(120), std::min(uint32_t(1800), sp.outdoor_staleness_max_s));
    sp.summer_vent_min_runtime_s = std::max(uint32_t(60), std::min(uint32_t(600), sp.summer_vent_min_runtime_s));

    // --- Phase-2 dwell gate clamp ---
    // 60s floor (shorter than control-cycle cadence makes the gate useless),
    // 1800s (30-min) ceiling (longer than sealed_max_ms would starve exits).
    sp.dwell_gate_ms = std::max(uint32_t(60000), std::min(uint32_t(1800000), sp.dwell_gate_ms));

    // --- band-first controller timing/cooling clamps ---
    sp.cool_stage2_over_high_f = std::max(0.0f, std::min(3.0f, sp.cool_stage2_over_high_f));
    sp.cool_stage2_exit_hysteresis_f = std::max(0.3f, std::min(3.0f, sp.cool_stage2_exit_hysteresis_f));
    sp.cool_exit_hysteresis_f = std::max(0.3f, std::min(3.0f, sp.cool_exit_hysteresis_f));
    sp.cold_vent_guard_delta_f = std::max(0.0f, std::min(15.0f, sp.cold_vent_guard_delta_f));
    sp.direct_wet_stress_vpd_margin_kpa = std::max(0.0f, std::min(0.5f, sp.direct_wet_stress_vpd_margin_kpa));
    sp.direct_wet_stress_min_dew_margin_f = std::max(3.0f, std::min(15.0f, sp.direct_wet_stress_min_dew_margin_f));
    sp.mist_backoff_ms = std::max(uint32_t(60000), std::min(uint32_t(3600000), sp.mist_backoff_ms));

    // --- Firmware-v2 solar-relative clamps ---
    // Taper window: 0 (off at sunset exactly) to 6h before sunset.
    sp.wet_taper_before_sunset_min = std::max(0, std::min(360, sp.wet_taper_before_sunset_min));
    sp.night_stress_min_dew_margin_f = std::max(3.0f, std::min(15.0f, sp.night_stress_min_dew_margin_f));
    // Boost-window offsets: dawn within [0, 4h] after sunrise; midday within
    // [-2h, +4h] of solar noon. Windows to [0, 120] min so a bad push can't
    // open an all-day burst. ON >= 1s; gap floored at 5s (relay chatter).
    // Cadence knobs only — SAF-4 duty cap + daily-volume ceiling still rail.
    sp.dawn_boost_offset_min       = std::max(0, std::min(240, sp.dawn_boost_offset_min));
    sp.dawn_rehydrate_window_min   = std::max(0, std::min(120, sp.dawn_rehydrate_window_min));
    sp.dawn_rehydrate_on_s         = std::max(1, std::min(600, sp.dawn_rehydrate_on_s));
    sp.dawn_rehydrate_gap_s        = std::max(5, std::min(600, sp.dawn_rehydrate_gap_s));
    sp.midday_boost_offset_min     = std::max(-120, std::min(240, sp.midday_boost_offset_min));
    sp.midday_drench_window_min    = std::max(0, std::min(120, sp.midday_drench_window_min));
    sp.midday_drench_on_s          = std::max(1, std::min(600, sp.midday_drench_on_s));
    sp.midday_drench_gap_s         = std::max(5, std::min(600, sp.midday_drench_gap_s));
    // Served-band targets stay inside their band edges.
    sp.temp_target = std::max(sp.temp_low, std::min(sp.temp_high, sp.temp_target));
    sp.vpd_target  = std::max(sp.vpd_low,  std::min(sp.vpd_high,  sp.vpd_target));
    sp.band_track_fraction = std::max(0.0f, std::min(1.0f, sp.band_track_fraction));
    // BC-13: moisture-exchange selector clamps.
    sp.dehum_vent_gain_margin_kpa = std::max(0.0f, std::min(0.2f, sp.dehum_vent_gain_margin_kpa));
    sp.dehum_heat_assist_min_dwell_ms = std::max<uint32_t>(
        60000u, std::min<uint32_t>(1800000u, sp.dehum_heat_assist_min_dwell_ms));
    sp.vent_exchange_fraction = std::max(0.1f, std::min(0.6f, sp.vent_exchange_fraction));
}

inline ControlState initial_state() {
    return {
        .sentinel = STATE_SENTINEL,
        .mode = IDLE, .mode_prev = IDLE,
        .mist_stage = MIST_WATCH,
        .sealed_timer_ms = 0, .relief_timer_ms = 0,
        .vpd_watch_timer_ms = 0, .mist_stage_timer_ms = 0,
        .relief_cycle_count = 0, .vent_latch_timer_ms = 0,
        .dry_override_active = false,
        .heat2_latched = false,
        .fan2_latched = false,
        .dehum_heat_assist_timer_ms = 0,
        .dehum_heat_assist_active = false,
        .override_summer_vent = false,
        .last_mode_reason = "init",
        .last_transition_tick_ms = 0,
        .mist_backoff_timer_ms = 0,
        .vent_mist_assist_active = false,
        .center_burst = CENTER_BURST_NONE
    };
}
