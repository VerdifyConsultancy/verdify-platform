
#include <algorithm>
#include <array>
#include <cassert>
#include <cmath>
#include <cstdint>
#include <functional>
#include <string>
#include "policy_rom_baseline_generated.h"
using std::isnan;
using std::max;
#define id(x) x
#define ESP_LOGI(...) do {} while (false)
#define ESP_LOGE(...) do {} while (false)
uint32_t clock_ms = 1000000U;
uint32_t millis() { return clock_ms; }

// ESPHome switches deduplicate identical states and synchronously dispatch
// state callbacks. The actual center ON/OFF callback bodies are inserted below.
struct SwitchAdapter {
  bool state = false;
  unsigned starts = 0, stops = 0;
  std::function<void()> on_callback, off_callback;
  void turn_on() {
    if (state) return;
    state = true; ++starts;
    if (on_callback) on_callback();
  }
  void turn_off() {
    if (!state) return;
    state = false; ++stops;
    if (off_callback) off_callback();
  }
};
struct SensorAdapter { float state = 0; void publish_state(float value) { state = value; } };
struct Fixture {
  SwitchAdapter south_wall_mister, west_wall_mister;
  SwitchAdapter south_wall_mister_fertilized, west_wall_mister_fertilized;
  SwitchAdapter center_drips_fertilized, center_mister, fertilizer_master_valve;
  SensorAdapter mister_vpd_delta;
  bool climate_wet_controller_actuating = false;
  int irrig_state = 0, mister_state = 0, mister_pulse_zone = 0;
  int mister_selected_zone = 0, mister_rotation_index = 0, mister_duty_phase = 0;
  uint32_t mister_pulse_timer_ms = 0, mister_duty_timer_ms = 0;
  uint32_t humid_s1_duration_ms = 45000U, humid_s2_duration_ms = 0;
  uint32_t last_off_south_ms = 0, last_off_west_ms = 0, last_off_center_ms = 0;
  uint32_t last_fire_center_ms = 0;
  unsigned cnt_mister_center_today = 0;
  float mister_vpd_at_start = 3.5f;
  float mister_engage_delay_s = 45, mister_all_delay_s = 300;
  float mister_pulse_on_s = 60, mister_pulse_gap_s = 10, mister_min_off_s = 45;
  float mister_engage_kpa = 3.2f, mister_all_kpa = 3.8f;
  float mister_hysteresis_kpa = 0.1f, mister_vpd_weight = 1.5f;
  float vpd_target_south = 3.1f, vpd_target_west = 3.1f, vpd_target_center = 3.1f;
  struct { int center_burst = 0; } ctl_state;
  static constexpr int CENTER_BURST_NONE = 0;
  struct {
    bool experiment_active = false;
    std::array<float, verdify_policy::kPolicyFieldCount> values{};
  } psnap;
  float avg_vpd = 3.5f, south_vpd = 3.5f, west_vpd = 3.5f;
  bool center_wet_allowed = true, humidity_demand = true;
  bool mister_vent_ok = true, leak_block = false, budget_block = false;
  bool irrigation_block = false, occupancy_blocks = false;
  bool any_zone_stressed = true, global_stressed = true;
  int selected_hint = 3;

  Fixture(bool snapshot, uint32_t on_ms) {
    clock_ms = 1000000U;
    psnap.experiment_active = snapshot;
    psnap.values[verdify_policy::kPF_mister_engage_delay_s] = mister_engage_delay_s;
    psnap.values[verdify_policy::kPF_mister_all_delay_s] = mister_all_delay_s;
    psnap.values[verdify_policy::kPF_mister_pulse_gap_s] = mister_pulse_gap_s;
    psnap.values[verdify_policy::kPF_mister_min_off_s] = mister_min_off_s;
    psnap.values[verdify_policy::kPF_mister_engage_kpa] = mister_engage_kpa;
    psnap.values[verdify_policy::kPF_mister_all_kpa] = mister_all_kpa;
    psnap.values[verdify_policy::kPF_mister_vpd_weight] = mister_vpd_weight;
    const auto& contract = verdify_policy::kPolicyFields[verdify_policy::kPF_mister_pulse_on_s];
    const float seconds = on_ms / 1000.0f;
    assert(seconds * contract.scale >= contract.min_raw);
    assert(seconds * contract.scale <= contract.max_raw);
    if (snapshot) psnap.values[verdify_policy::kPF_mister_pulse_on_s] = seconds;
    else mister_pulse_on_s = seconds;
    center_mister.on_callback = [&]() {
if(!id(climate_wet_controller_actuating)
   || id(irrig_state) != 0
   || id(fertilizer_master_valve).state) {
  id(center_mister).turn_off();
  ESP_LOGE("irrig","Blocked non-climate center-mister relay ON");
  return;
}
};
center_mister.off_callback = [&]() {
id(last_off_center_ms) = millis();
    };
  }
  void assert_center_only() const {
    assert(!south_wall_mister.state && !west_wall_mister.state);
    assert(south_wall_mister.starts == 0 && west_wall_mister.starts == 0);
    assert(!south_wall_mister_fertilized.state && !west_wall_mister_fertilized.state);
    assert(!center_drips_fertilized.state);
    assert(south_wall_mister_fertilized.starts == 0);
    assert(west_wall_mister_fertilized.starts == 0 && center_drips_fertilized.starts == 0);
    assert(!climate_wet_controller_actuating);
  }
  void tick(uint32_t dt_ms) {
    clock_ms += dt_ms;
    const uint32_t now_ms = clock_ms;
    const bool south_wet_allowed = false, west_wet_allowed = false;
    const bool any_mister_wet_allowed = center_wet_allowed;
    const bool zone_mister_demand = humidity_demand && mister_vent_ok;
    const float HV = 0.25f;
const auto pol = [&](uint8_t f, float legacy) -> float {
              return psnap.experiment_active ? psnap.values[f] : legacy;
          };
          const uint32_t MISTER_ENGAGE_DELAY_MS = uint32_t(pol(verdify_policy::kPF_mister_engage_delay_s, id(mister_engage_delay_s))) * 1000UL;
            const uint32_t MISTER_ALL_DELAY_MS    =
                uint32_t(pol(verdify_policy::kPF_mister_all_delay_s, id(mister_all_delay_s))) * 1000UL;

            auto direct_wet_relay_watchdog = [&]() {
              // Climate owns center only. Non-wall fertilizer is never eligible,
              // even if stale HA state or a legacy retry attempts to restore it.
              if(id(irrig_state) == 0) {
                id(south_wall_mister).turn_off();
                id(west_wall_mister).turn_off();
              }
              id(south_wall_mister_fertilized).turn_off();
              id(west_wall_mister_fertilized).turn_off();
              id(center_drips_fertilized).turn_off();
              if(!center_wet_allowed) id(center_mister).turn_off();
              bool active_zone_gate_closed =
                  (id(mister_pulse_zone) == 1 && !south_wet_allowed) ||
                  (id(mister_pulse_zone) == 2 && !west_wet_allowed) ||
                  (id(mister_pulse_zone) == 3 && !center_wet_allowed);
              if(active_zone_gate_closed) {
                id(mister_pulse_zone) = 0;
                id(mister_pulse_timer_ms) = 0;
              }
            };
            direct_wet_relay_watchdog();
            auto first_allowed_mister_zone = [&]() -> int {
              if(center_wet_allowed) return 3;
              return 0;
            };

              if (!mister_vent_ok && id(mister_pulse_zone) > 0) {
              if(id(irrig_state) == 0) {
                id(south_wall_mister).turn_off();
                id(west_wall_mister).turn_off();
              }
                id(south_wall_mister_fertilized).turn_off();
                id(west_wall_mister_fertilized).turn_off();
                id(center_mister).turn_off();
                id(mister_pulse_zone) = 0;
                id(mister_pulse_timer_ms) = 0;
                ESP_LOGI("interlock","Mister suppressed — vent still open");
            }

            bool mister_blocked =
                  leak_block
                  || budget_block
                  || !zone_mister_demand
                  || !any_mister_wet_allowed
                  || irrigation_block
                  || occupancy_blocks;
              if(mister_blocked && id(mister_state) > 0){
              ESP_LOGI("mister","BLOCKED (budget=%.0f/%.0f humid=%d zone=%d occ=%d)",
                       id(mister_water_today), pol(verdify_policy::kPF_mister_water_budget_gal, id(mister_water_budget_gal)),
                       humidity_demand, any_zone_stressed, occupancy_blocks);
              if(id(irrig_state) == 0) {
                id(south_wall_mister).turn_off();
                id(west_wall_mister).turn_off();
              }
              id(south_wall_mister_fertilized).turn_off();
              id(west_wall_mister_fertilized).turn_off();
              id(center_mister).turn_off();
              id(mister_state) = 0;
              id(mister_selected_zone) = 0;
              id(mister_duty_phase) = 0;
              id(mister_duty_timer_ms) = 0;
              // NOTE: Do NOT reset humid_s1/s2 duration timers here.
              // Preserves escalation progress so misters re-engage quickly
              // when the blocking condition clears (e.g., COOL→IDLE transition).
            }

            
if (!mister_blocked) {
const uint32_t PULSE_ON_MS  =
                  uint32_t(pol(verdify_policy::kPF_mister_pulse_on_s, id(mister_pulse_on_s))) * 1000UL;
              const uint32_t PULSE_GAP_MS =
                  uint32_t(pol(verdify_policy::kPF_mister_pulse_gap_s, id(mister_pulse_gap_s))) * 1000UL;

              // Center mist is climate/VPD-only. Every zone uses the base pulse
              // cadence; there is no dawn or midday watering substitution.
              ctl_state.center_burst = CENTER_BURST_NONE;
              auto pulse_on_ms_for  = [&](int) -> uint32_t { return PULSE_ON_MS; };
              auto pulse_gap_ms_for = [&](int) -> uint32_t { return PULSE_GAP_MS; };

              const uint32_t MISTER_MIN_OFF_MS = (uint32_t)pol(verdify_policy::kPF_mister_min_off_s, id(mister_min_off_s)) * 1000UL;
              auto mister_zone_last_off = [&](int z) -> uint32_t {
                return z == 1 ? id(last_off_south_ms)
                     : z == 2 ? id(last_off_west_ms)
                     : id(last_off_center_ms);
              };
              auto mister_zone_can_on = [&](int z) -> bool {
                return z <= 0 || (now_ms - mister_zone_last_off(z)) >= MISTER_MIN_OFF_MS;
              };

              const float MISTER_HYST = fminf(HV, fmaxf(id(mister_hysteresis_kpa),
                  pol(verdify_policy::kPF_mister_engage_kpa, id(mister_engage_kpa)) * 0.25f));

              auto turn_on_zone = [&](int zone) {
                if(id(irrig_state) == 0) {
                  id(south_wall_mister).turn_off();
                  id(west_wall_mister).turn_off();
                }
                id(south_wall_mister_fertilized).turn_off();
                id(west_wall_mister_fertilized).turn_off();
                id(center_mister).turn_off();
                // Resolve every climate selection through the pure topology;
                // stale fairness/zone hints cannot route away from center.
                if(zone != 3) zone = first_allowed_mister_zone();
                if(zone == 3 && !center_wet_allowed) zone = first_allowed_mister_zone();
                if(zone == 0) { id(mister_pulse_zone) = 0; return; }
                // Mister re-fire dwell fence (mister_min_off_s): if the chosen
                // zone is still inside its min-off hold, try the other eligible
                // zone; if none is ready, hold this cycle (all misters stay off,
                // mister_pulse_zone=0). The caller arms mister_pulse_timer_ms =
                // pulse_on_ms_for(0) = PULSE_ON_MS, so the FSM re-evaluates ~one
                // gap later — by which point min_off has elapsed and it fires.
                // Suppression only DELAYS a re-fire; it never forces a relay on
                // and never extends a normal pulse, so the failure mode is benign.
                if(!mister_zone_can_on(zone)) {
                  int alt = first_allowed_mister_zone();
                  if(alt > 0 && alt != zone && mister_zone_can_on(alt)) {
                    zone = alt;
                  } else {
                    ESP_LOGI("mister","DWELL-HOLD zone=%d (min_off %us)", zone, (unsigned)pol(verdify_policy::kPF_mister_min_off_s, id(mister_min_off_s)));
                    id(mister_pulse_zone) = 0;
                    return;
                  }
                }
                // sprint-2: last-fire ms for fairness watchdog.
                // sprint-7: per-zone cycle counters at the rising edge
                //          (turn_on_zone is always preceded by all-off,
                //          so every call is an off→on transition).
                id(climate_wet_controller_actuating) = true;
                if(zone == 3) { id(center_mister).turn_on(); id(last_fire_center_ms) = now_ms; id(cnt_mister_center_today) += 1; }
                id(climate_wet_controller_actuating) = false;
                id(mister_pulse_zone) = zone;
                id(mister_selected_zone) = zone;
              };

              auto turn_off_all_misters = [&]() {
                // GPIO falling-edge callbacks own last-off stamps for every
                // stop path, including watchdog/irrigation cancellation. A
                // repeated OFF request must not restart the minimum-off fence.
                id(south_wall_mister).turn_off();
                id(south_wall_mister_fertilized).turn_off();
                id(west_wall_mister).turn_off();
                id(west_wall_mister_fertilized).turn_off();
                id(center_mister).turn_off();
                id(mister_pulse_zone) = 0;
              };

              const char* zone_names[] = {"none", "south", "west", "center"};

              
auto select_zone_for_pulse = [&]() -> int { return selected_hint; };
auto fallback_wet_zone = [&]() -> int {
                  if(south_wet_allowed) return 1;
                  if(west_wet_allowed) return 2;
                  if(center_wet_allowed) return 3;
                  return 0;
                };

              // --- S1 ENGAGE: misters start only under humidity demand; zone/global
              // stress decide whether a pulse is warranted and which zone gets it.
              // The elapsed interval precedes any phase armed in this tick.
              // A new pulse must receive its complete requested duration.
              bool mister_timer_armed_this_tick = false;
              if(id(mister_state) == 0
                 && humidity_demand
                 && id(humid_s1_duration_ms) >= MISTER_ENGAGE_DELAY_MS
                 && (any_zone_stressed || global_stressed)){

                int zone = select_zone_for_pulse();
                  if(zone == 0) zone = fallback_wet_zone();
                if(zone > 0) {  // Engage
                  id(mister_state) = 1;  // S1 = single zone pulse
                  id(mister_vpd_at_start) = avg_vpd;  // Track effectiveness
                  turn_on_zone(zone);
                  // IRR-3/IRR-4: cadence keyed on the ACTUAL fired zone
                  // (turn_on_zone may remap to first_allowed); center override
                  // only applies to zone 3.
                  id(mister_pulse_timer_ms) = pulse_on_ms_for(id(mister_pulse_zone));
                  mister_timer_armed_this_tick = true;
                  ESP_LOGI("mister","S1 MISTERS: pulse %s %ums (VPD=%.2f, target=%.2f)",
                           zone_names[zone], (unsigned)pulse_on_ms_for(id(mister_pulse_zone)), avg_vpd,
                           zone == 1 ? id(vpd_target_south) : zone == 2 ? id(vpd_target_west) : id(vpd_target_center));
                }
              }

              // --- ESCALATE S1 → S2: all zones rotating ---
              if(id(mister_state) == 1){
                id(humid_s2_duration_ms) += dt_ms;
                if(id(humid_s2_duration_ms) >= MISTER_ALL_DELAY_MS
                   && avg_vpd > pol(verdify_policy::kPF_mister_all_kpa, id(mister_all_kpa))){
                    id(mister_state) = 2;  // S2 = all zones rotating
                    id(mister_rotation_index) = 0;
                    int zone = select_zone_for_pulse();
                    if(zone == 0) zone = fallback_wet_zone();
                    turn_on_zone(zone);
                  // IRR-3/IRR-4: base ON keyed on actual fired zone (center
                  // burst override applies only to zone 3). The south/west
                  // VPD-weight multiplier is unchanged and never touches center.
                  uint32_t weighted_ms = pulse_on_ms_for(id(mister_pulse_zone));
                  float svpd = isnan(south_vpd) ? 0.0f : south_vpd;
                  float wvpd = isnan(west_vpd) ? 0.0f : west_vpd;
                  float max_vpd = max(max(svpd, wvpd), avg_vpd);
                  if(zone == 1 && svpd >= max_vpd * 0.95f) weighted_ms = uint32_t(PULSE_ON_MS * pol(verdify_policy::kPF_mister_vpd_weight, id(mister_vpd_weight)));
                  else if(zone == 2 && wvpd >= max_vpd * 0.95f) weighted_ms = uint32_t(PULSE_ON_MS * pol(verdify_policy::kPF_mister_vpd_weight, id(mister_vpd_weight)));
                  id(mister_pulse_timer_ms) = weighted_ms;
                  mister_timer_armed_this_tick = true;
                  ESP_LOGI("mister","S2 ALL-ROTATE: start %s %dms (VPD=%.2f)",
                           zone_names[zone], weighted_ms, avg_vpd);
                }else{
                }
              }

              // --- ESCALATE S2 → S3: FOG (heavy artillery) ---
              // Fog fires when misters alone can't control VPD.
              // fog_closes_vent controls whether fog forces the vent shut.
              // This is tracked separately — see willFog gate at line ~439.

              // --- DE-ESCALATE S2 → S1 ---
              if(id(mister_state) == 2
                 && avg_vpd < (pol(verdify_policy::kPF_mister_all_kpa, id(mister_all_kpa)) - MISTER_HYST)){
                ESP_LOGI("mister","S2→S1: VPD=%.2f", avg_vpd);
                  id(mister_state) = 1;
                  id(humid_s2_duration_ms) = 0;
                  int zone = select_zone_for_pulse();
                  if(zone == 0) zone = fallback_wet_zone();
                  turn_on_zone(zone);
                id(mister_pulse_timer_ms) = pulse_on_ms_for(id(mister_pulse_zone));
                mister_timer_armed_this_tick = true;
              }

              // --- DE-ESCALATE S1 → off ---
              if(id(mister_state) == 1 && !any_zone_stressed
                 && avg_vpd < (pol(verdify_policy::kPF_mister_engage_kpa, id(mister_engage_kpa)) - MISTER_HYST)){
                ESP_LOGI("mister","S1→off: VPD=%.2f", avg_vpd);
                turn_off_all_misters();
                id(mister_state) = 0;
                id(mister_selected_zone) = 0;
                id(mister_pulse_timer_ms) = 0;
                id(humid_s1_duration_ms) = 0;
                id(humid_s2_duration_ms) = 0;
              }

              // --- PULSE TIMER (both S2 and S3) ---
              if(id(mister_state) > 0){
                if(!mister_timer_armed_this_tick){
                  if(id(mister_pulse_timer_ms) > dt_ms){
                    id(mister_pulse_timer_ms) -= dt_ms;
                  }else{
                    id(mister_pulse_timer_ms) = 0;
                  }
                }

                if(id(mister_pulse_timer_ms) == 0){
                  if(id(mister_pulse_zone) > 0){
                    // === PULSE ON → GAP ===
                    // IRR-3/IRR-4: capture the just-finished zone BEFORE
                    // turn_off_all_misters() clears mister_pulse_zone, so the
                    // GAP after a CENTER pulse uses the denser center gap.
                    const int finished_zone = id(mister_pulse_zone);
                    // Track effectiveness: VPD change during this pulse
                    if(!isnan(id(mister_vpd_at_start)) && !isnan(avg_vpd)){
                      float delta = avg_vpd - id(mister_vpd_at_start);
                      id(mister_vpd_delta).publish_state(delta);
                      ESP_LOGI("mister","Pulse done: zone=%s delta=%.3f kPa (%.2f → %.2f)",
                               zone_names[finished_zone], delta,
                               id(mister_vpd_at_start), avg_vpd);
                    }
                    turn_off_all_misters();
                    id(mister_pulse_timer_ms) = pulse_gap_ms_for(finished_zone);

                  }else{
                    // === GAP → NEXT PULSE ===
                    if(id(mister_state) == 1){
                        // S1: re-read VPD, pulse most stressed zone
                        int zone = select_zone_for_pulse();
                        if(zone == 0) zone = fallback_wet_zone();
                      id(mister_vpd_at_start) = avg_vpd;  // Track effectiveness
                      turn_on_zone(zone);
                      id(mister_pulse_timer_ms) = pulse_on_ms_for(id(mister_pulse_zone));
                      ESP_LOGI("mister","PULSE: %s %ums (VPD=%.2f)",
                               zone_names[zone], (unsigned)pulse_on_ms_for(id(mister_pulse_zone)), avg_vpd);

                    }else if(id(mister_state) == 2){
                        // S2: pick the most stressed zone each pulse (not round-robin)
                        int zone = select_zone_for_pulse();
                        if(zone == 0) zone = fallback_wet_zone();
                      id(mister_vpd_at_start) = avg_vpd;  // Track effectiveness
                      turn_on_zone(zone);
                      // VPD-weighted duration for high-stress zones. Base ON is
                      // keyed on the actual fired zone (IRR-3/IRR-4 center burst
                      // override applies only to zone 3); the south/west VPD
                      // weight multiplier is unchanged and never touches center.
                      uint32_t weighted_ms = pulse_on_ms_for(id(mister_pulse_zone));
                      float zone_vpd = (zone == 1) ? south_vpd : (zone == 2) ? west_vpd : avg_vpd;
                      float zone_target = (zone == 1) ? id(vpd_target_south) : (zone == 2) ? id(vpd_target_west) : id(vpd_target_center);
                      if(!isnan(zone_vpd) && zone_target > 0 && zone_vpd > zone_target * 1.2f){
                        weighted_ms = uint32_t(PULSE_ON_MS * pol(verdify_policy::kPF_mister_vpd_weight, id(mister_vpd_weight)));
                      }
                      id(mister_pulse_timer_ms) = weighted_ms;
                      ESP_LOGI("mister","ROTATE: %s %dms (VPD=%.2f target=%.2f)",
                               zone_names[zone], weighted_ms,
                               isnan(zone_vpd) ? 0.0f : zone_vpd, zone_target);
                    }
                  }
                }
              }

              
    }
    assert_center_only();
  }
};

void initial(bool snapshot, uint32_t on_ms) {
  Fixture f(snapshot, on_ms);
  f.tick(1000U);  // Entire interval predates admission.
  assert(f.center_mister.state && f.mister_state == 1 && f.mister_pulse_zone == 3);
  assert(f.mister_pulse_timer_ms == on_ms && f.center_mister.starts == 1);
  f.tick(on_ms - 1U);
  assert(f.center_mister.state && f.mister_pulse_timer_ms == 1U);
  f.tick(1U);
  assert(!f.center_mister.state && f.mister_pulse_zone == 0);
  assert(f.mister_pulse_timer_ms == 10000U && f.center_mister.stops == 1);
  assert(f.last_off_center_ms == clock_ms);
}

void rearm(bool snapshot, uint32_t on_ms, bool escalation) {
  Fixture f(snapshot, on_ms);
  f.mister_state = escalation ? 1 : 2;
  f.mister_pulse_zone = 0; f.mister_pulse_timer_ms = 5000U;
  f.humid_s2_duration_ms = 299000U;
  f.avg_vpd = escalation ? 4.0f : 3.3f;
  f.tick(1000U);
  assert(f.mister_state == (escalation ? 2 : 1));
  assert(f.center_mister.state && f.mister_pulse_zone == 3);
  assert(f.mister_pulse_timer_ms == on_ms && f.center_mister.starts == 1);
  f.tick(on_ms - 1U);
  assert(f.center_mister.state && f.mister_pulse_timer_ms == 1U);
  f.tick(1U);
  assert(!f.center_mister.state && f.mister_pulse_timer_ms == 10000U);
}

void active(bool snapshot, uint32_t on_ms, bool gap) {
  Fixture f(snapshot, on_ms);
  f.mister_state = 1;
  f.mister_pulse_timer_ms = gap ? 10000U : on_ms;
  f.mister_pulse_zone = gap ? 0 : 3;
  f.center_mister.state = !gap;
  f.tick((gap ? 10000U : on_ms) - 1U);
  assert(f.center_mister.state == !gap && f.mister_pulse_timer_ms == 1U);
  f.tick(1U);
  assert(f.center_mister.state == gap);
  assert(f.mister_pulse_timer_ms == (gap ? on_ms : 10000U));
  assert(f.mister_pulse_zone == (gap ? 3 : 0));
}

void dwell(bool snapshot, uint32_t on_ms, bool boundary) {
  Fixture f(snapshot, on_ms);
  f.mister_state = 1; f.mister_pulse_timer_ms = 1U;
  f.last_off_center_ms = clock_ms - (boundary ? 44999U : 44998U);
  f.tick(1U);
  assert(f.center_mister.state == boundary);
  assert(f.mister_pulse_zone == (boundary ? 3 : 0));
  assert(f.mister_pulse_timer_ms == on_ms);
  const auto last_off = f.last_off_center_ms;
  f.tick(1U);  // Repeated OFF requests must not slide the dwell timestamp.
  assert(f.last_off_center_ms == last_off);
}

void cancellation(bool snapshot, uint32_t on_ms, const std::string& reason) {
  Fixture f(snapshot, on_ms);
  f.tick(1000U);
  assert(f.center_mister.state);
  if (reason == "leak") f.leak_block = true;
  else if (reason == "budget") f.budget_block = true;
  else if (reason == "occupancy") f.occupancy_blocks = true;
  else if (reason == "irrigation") { f.irrigation_block = true; f.irrig_state = 1; }
  else if (reason == "vent") f.mister_vent_ok = false;
  else if (reason == "topology") f.center_wet_allowed = false;
  else if (reason == "demand") f.humidity_demand = false;
  else assert(false);
  f.tick(1U);  // Hard cancellation must not wait for the armed ON timer.
  assert(!f.center_mister.state && f.mister_state == 0);
  const uint32_t stopped = f.last_off_center_ms;
  f.tick(1U);
  assert(!f.center_mister.state && f.last_off_center_ms == stopped);
  assert(f.center_mister.starts == 1 && f.center_mister.stops == 1);
}

void reentry(bool snapshot, uint32_t on_ms) {
  Fixture f(snapshot, on_ms);
  f.tick(1000U);
  f.leak_block = true; f.tick(1U);
  assert(!f.center_mister.state && f.mister_state == 0);
  f.leak_block = false;
  f.tick(45000U);  // Prior idle time must not consume the newly armed phase.
  assert(f.center_mister.state && f.mister_state == 1);
  assert(f.mister_pulse_timer_ms == on_ms && f.center_mister.starts == 2);
}

void topology(bool snapshot, uint32_t on_ms, int stale_hint) {
  Fixture f(snapshot, on_ms); f.selected_hint = stale_hint;
  f.tick(1000U);
  assert(f.center_mister.state && f.mister_pulse_zone == 3);
  assert(f.mister_pulse_timer_ms == on_ms && f.center_mister.starts == 1);
}

void callback_conflict(bool snapshot, uint32_t on_ms, bool manual) {
  Fixture f(snapshot, on_ms);
  f.climate_wet_controller_actuating = !manual;
  if (!manual) f.fertilizer_master_valve.state = true;
  f.center_mister.turn_on();
  // Actual ON callback rejects the request synchronously. This is a logical
  // callback assertion, not proof that a physical GPIO never briefly changed.
  assert(!f.center_mister.state && f.center_mister.stops == 1);
  assert(f.last_off_center_ms == clock_ms);
  f.climate_wet_controller_actuating = false;
  f.assert_center_only();
}

int main(int argc, char** argv) {
  assert(argc == 4);
  const std::string scenario = argv[1];
  const bool snapshot = std::string(argv[2]) == "snapshot";
  const uint32_t on_ms = std::stoul(argv[3]);
  if (scenario == "initial") initial(snapshot, on_ms);
  else if (scenario == "escalation") rearm(snapshot, on_ms, true);
  else if (scenario == "deescalation") rearm(snapshot, on_ms, false);
  else if (scenario == "active") active(snapshot, on_ms, false);
  else if (scenario == "gap") active(snapshot, on_ms, true);
  else if (scenario == "dwell_before") dwell(snapshot, on_ms, false);
  else if (scenario == "dwell_at") dwell(snapshot, on_ms, true);
  else if (scenario == "reentry") reentry(snapshot, on_ms);
  else if (scenario == "hint_south") topology(snapshot, on_ms, 1);
  else if (scenario == "hint_west") topology(snapshot, on_ms, 2);
  else if (scenario == "hint_none") topology(snapshot, on_ms, 0);
  else if (scenario == "manual") callback_conflict(snapshot, on_ms, true);
  else if (scenario == "fertilizer") callback_conflict(snapshot, on_ms, false);
  else if (scenario.rfind("cancel_", 0) == 0) cancellation(snapshot, on_ms, scenario.substr(7));
  else return 2;
}
