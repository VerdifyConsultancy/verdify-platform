// irrigation_engine_esphome.h — ESP32/ESPHome glue for the irrigation engine
// (irrigation §5 F1-F5, F11, F14; VD-45, #1160), the way policy_journal_esphome.h serves the
// policy engine.
//
// firmware/lib/irrigation_program.h holds the engine and the pure half of the wiring (the
// text formats, the grant plan, the program staging, the command arguments), all covered by
// the native tests. This header is the device half:
//   - the RTC_NOINIT_ATTR IrrRtcBlock: zeroed at boot after a power-on, a brown-out or an
//     unknown reset, validated (magic, version, CRC32) after any other reset;
//   - two NVS preferences, each saved then synced: the program record with its hash (src=nvs
//     when it decodes at boot, else the ROM default, src=rom) and the claims record;
//   - the bindings to the eleven relays, the three text sensors, irr_grant_mask and the
//     counter mirrors, set by the first run of the 5 s loop in irrigation_engine.yaml, which
//     starts the engine;
//   - the entry points of irrigation_engine.yaml (the tick and the four services) and of the
//     climate path in controls.yaml (irr_vpd_grant, irr_vpd_release, irr_drip_active, the caps,
//     irr_climate_zone_allowed for F6's per-zone rule and irr_mister_watchdog for D-IRP-19, #1161).
//
// Before the engine starts (the first ~2.5 s after boot) irr_grant_mask is 0, so every wet relay
// guard refuses; the climate helpers answer as if nothing runs and no grant is given, and the
// services log and ignore the call (the ingestor's 60 s pass pushes again).
//
// Host builds compile this header to nothing: everything device-specific is guarded by
// ESP_PLATFORM, as in policy_journal_esphome.h and heap_diagnostics.h.
#pragma once

#include "irrigation_program.h"
#include "policy_vector.h"
#include "zone_vpd.h"

#ifdef ESP_PLATFORM

#include <cstring>
#include <string>

#include "esp_attr.h"
#include "esp_system.h"

namespace verdify_irr {

// NVS keys (u32, never reuse): "VIRP" the program record, "VIRC" the claims record.
constexpr uint32_t kProgramPrefKey = 0x56495250u;
constexpr uint32_t kClaimsPrefKey = 0x56495243u;

static_assert(std::is_trivially_default_constructible<IrrRtcBlock>::value,
              "RTC_NOINIT: no constructor may overwrite the block at boot");

// The RTC no-init block (irrigation §4.5): it survives soft resets (heap guard, WiFi reboot
// loop, panic, watchdog), never a power cut. irr_rtc_validate() decides what it is worth.
static RTC_NOINIT_ATTR IrrRtcBlock irr_rtc_block;

struct IrrEspBindings {
  esphome::switch_::Switch* relay[kLineCount];  // by device_idx; relay[kL11] is fog, read only
  esphome::text_sensor::TextSensor* program_id;
  esphome::text_sensor::TextSensor* state;
  esphome::text_sensor::TextSensor* events;
  uint16_t* grant_mask;                      // &id(irr_grant_mask)
  // Mirrors written from the RTC block, so their readers keep working.
  float* mister_water_today;                 // gallons
  uint32_t* runtime_mister_ms[kZoneCount];   // center, south, west
  uint32_t* runtime_wall_drip_ms;            // L01
  uint32_t* runtime_center_drip_ms;          // L05
  uint32_t* runtime_fert_master_ms;          // L03
  int* cnt_drip_wall_today;                  // L01 starts
  int* cnt_drip_center_today;                // L05 starts
};

struct IrrEsp {
  bool ready = false;
  IrrEspBindings b{};
  IrrEngine eng{};
  IrrOutputs out{};
  IrrInputs climate = irr_inputs_idle();  // the climate loop's part of the inputs (controls.yaml)
  IrrStateExtras extras{};
  IrrWatchdog wd{};                       // the D-IRP-19 mister watchdog's RAM (its faults are in the RTC block)
  IrrStaging staging{};
  uint16_t held = 0;                      // the engine relays the device opened at the last call
  float water_carry = 0.0f;               // the fraction below one centi-gallon
  uint32_t water_before_start = 0;        // centi-gallons counted before the engine started
  esphome::ESPPreferenceObject program_pref;
  esphome::ESPPreferenceObject claims_pref;
  char text[kTextBufLen] = {};            // the static 256 B buffer irr_state and irr_events format into
  char published_state[kTextBufLen] = {};
  char published_program_id[32] = {};
  bool state_published = false;
  bool events_published = false;
  uint16_t published_seq = 0;
  uint8_t published_ring_count = 0;
};

inline IrrEsp& irr_esp() {
  static IrrEsp g;
  return g;
}

namespace detail {

inline bool esp_save_claims(const IrrClaims& c, void* ctx) { return static_cast<IrrEsp*>(ctx)->claims_pref.save(&c); }
inline bool esp_sync(void*) { return esphome::global_preferences->sync(); }

inline uint16_t esp_relays_on(const IrrEsp& g) {
  uint16_t on = 0;
  for (uint8_t i = 0; i < kLineCount; ++i) {
    if (g.b.relay[i] != nullptr && g.b.relay[i]->state) on = uint16_t(on | irr_bit(i));
  }
  return on;
}

// The counter mirrors (mister_water_today, runtime_mister_*_ms, runtime_*_drip_ms): the block
// zeroes the daily counters on a new local day, so the mirrors need no midnight reset.
inline void esp_mirror(IrrEsp& g) {
  const IrrRtcBlock& r = *g.eng.rtc;
  if (g.b.mister_water_today != nullptr) *g.b.mister_water_today = float(r.water_centigal) / 100.0f;
  for (uint8_t z = 0; z < kZoneCount; ++z) {
    if (g.b.runtime_mister_ms[z] != nullptr) *g.b.runtime_mister_ms[z] = r.zone_pulse_s[z] * 1000u;
  }
  if (g.b.runtime_wall_drip_ms != nullptr) *g.b.runtime_wall_drip_ms = r.line_on_s[kL01] * 1000u;
  if (g.b.runtime_center_drip_ms != nullptr) *g.b.runtime_center_drip_ms = r.line_on_s[kL05] * 1000u;
  if (g.b.runtime_fert_master_ms != nullptr) *g.b.runtime_fert_master_ms = r.line_on_s[kL03] * 1000u;
}

// irr_program_id and irr_state when their text changes, irr_events when the newest sequence
// does. Called before any relay turns on, so every ON edge has its cause published first.
inline void esp_publish(IrrEsp& g) {
  char id[32];
  irr_format_program_id(g.out.program_nvs, g.out.program_hash8, id, sizeof(id));
  if (g.b.program_id != nullptr && std::strcmp(id, g.published_program_id) != 0) {
    std::memcpy(g.published_program_id, id, sizeof(id));
    g.b.program_id->publish_state(std::string(id));
  }
  irr_format_state(g.out.state, g.extras, g.text, sizeof(g.text));
  if (g.b.state != nullptr && (!g.state_published || std::strcmp(g.text, g.published_state) != 0)) {
    std::memcpy(g.published_state, g.text, sizeof(g.text));
    g.state_published = true;
    g.b.state->publish_state(std::string(g.text));
  }
  const IrrRtcBlock& r = *g.eng.rtc;
  if (g.b.events != nullptr &&
      (!g.events_published || r.next_seq != g.published_seq || r.ring_count != g.published_ring_count)) {
    irr_format_events(r, g.text, sizeof(g.text));
    g.events_published = true;
    g.published_seq = r.next_seq;
    g.published_ring_count = r.ring_count;
    g.b.events->publish_state(std::string(g.text));
  }
}

// Apply the engine's outputs to the relays (F5, irr_apply_plan()): publish, set the bits of the
// relays about to open, close what the engine let go (and a stop's scope), open the engine's
// relays, clear the bits of what closed.
inline void esp_apply(IrrEsp& g, uint16_t stop_scope) {
  static uint16_t unbound = 0;  // only before the bindings are set, when no relay can be granted
  uint16_t& grant = g.b.grant_mask != nullptr ? *g.b.grant_mask : unbound;
  const IrrApply a = irr_apply_plan(grant, g.held, esp_relays_on(g), g.out, stop_scope);
  esp_publish(g);
  grant = a.grant_before;
  for (uint8_t pass = 0; pass < 2; ++pass) {  // L03 closes last
    for (uint8_t i = 0; i < kLineCount; ++i) {
      if ((a.turn_off & irr_bit(i)) && (i == kL03) == (pass == 1) && g.b.relay[i] != nullptr) g.b.relay[i]->turn_off();
    }
  }
  for (uint8_t pass = 0; pass < 2; ++pass) {  // L03 opens first
    for (uint8_t i = 0; i < kLineCount; ++i) {
      if (!(a.turn_on & irr_bit(i)) || (i == kL03) != (pass == 0) || g.b.relay[i] == nullptr) continue;
      g.b.relay[i]->turn_on();
      if (i == kL01 && g.b.cnt_drip_wall_today != nullptr) ++*g.b.cnt_drip_wall_today;
      if (i == kL05 && g.b.cnt_drip_center_today != nullptr) ++*g.b.cnt_drip_center_today;
    }
  }
  grant = a.grant_after;
  g.held = uint16_t(g.out.relay_mask & kGrantableMask);
  esp_mirror(g);
}

// The watchdog's faults (the RTC block) into the engine at once and into irr_state's f= tokens:
// `faulted`, the misters just forced OFF, lose their open; then publish and apply.
inline void esp_faults_changed(IrrEsp& g, uint16_t faulted, uint32_t now_ms) {
  uint16_t backoff = 0, latch = 0;
  irr_fault_masks(*g.eng.rtc, backoff, latch);
  irr_fault_tokens(*g.eng.rtc, g.extras);
  irr_apply_faults(g.eng, faulted, backoff, latch, now_ms, g.out);
  esp_apply(g, 0);
}

}  // namespace detail

// Start the engine (the first 5 s tick): bind, then the RTC block by the reset reason, the
// claims record and the stored program.
inline void irr_esp_start(const IrrEspBindings& b) {
  IrrEsp& g = irr_esp();
  if (g.ready) return;
  g.b = b;
  const esp_reset_reason_t why = esp_reset_reason();
  const bool power_on = why == ESP_RST_POWERON || why == ESP_RST_BROWNOUT || why == ESP_RST_UNKNOWN;
  g.claims_pref = esphome::global_preferences->make_preference<IrrClaims>(kClaimsPrefKey, true);
  g.program_pref = esphome::global_preferences->make_preference<IrrProgramRecord>(kProgramPrefKey, true);
  IrrClaims claims{};
  const bool have_claims = g.claims_pref.load(&claims);
  IrrPersist persist;
  persist.save = detail::esp_save_claims;
  persist.sync = detail::esp_sync;
  persist.ctx = &g;
  irr_engine_init(g.eng, &irr_rtc_block, persist, power_on, have_claims ? &claims : nullptr);
  // The stored program runs when it decodes against its hash (src=nvs); otherwise the ROM
  // default (src=rom, h=0000000000000000), so the ingestor pushes the desired one.
  if (g.program_pref.load(&g.staging.record)) {
    IrrProgram p{};
    IrrReject r = IrrReject::kNone;
    if (irr_decode(g.staging.record.rec, kRecordLen, g.staging.record.sha256, p, r)) {
      irr_use_program(g.eng, p, g.staging.record.sha256);
    } else {
      ESP_LOGE("irrig", "Stored irrigation program refused (%s): running the ROM default", irr_reject_token(r));
    }
  }
  g.staging.open = false;
  if (g.water_before_start != 0) irr_count_water(g.eng, g.water_before_start);
  g.water_before_start = 0;
  g.ready = true;
  ESP_LOGW("irrig", "Irrigation engine started (reset %d%s, program src=%s)", int(why),
           power_on ? ", RTC block zeroed" : "", g.eng.prog_nvs ? "nvs" : "rom");
}

}  // namespace verdify_irr

// ── irrigation_engine.yaml: the 5 s tick and the four services ───────────────

// The engine tick (F1). `clock_valid` false: nothing starts on schedule (irrigation §4.7).
inline void irr_esp_tick(const verdify_irr::IrrEspBindings& b, bool clock_valid, uint32_t epoch, int utc_offset_min) {
  verdify_irr::IrrEsp& g = verdify_irr::irr_esp();
  verdify_irr::irr_esp_start(b);
  verdify_irr::IrrInputs in = g.climate;
  in.now_ms = esphome::millis();
  in.clock = clock_valid ? verdify_irr::irr_clock_from_epoch(epoch, utc_offset_min) : verdify_irr::irr_clock_invalid();
  in.relay_on_mask = verdify_irr::detail::esp_relays_on(g);
  // The watchdog's back-offs and latches (D-IRP-19, #1161) from the RTC block.
  verdify_irr::irr_fault_masks(*g.eng.rtc, in.backoff_mask, in.latch_mask);
  verdify_irr::irr_tick(g.eng, in, g.out);
  // irr_state's f= tokens after the tick: a new local day has cleared the latches.
  verdify_irr::irr_fault_tokens(*g.eng.rtc, g.extras);
  verdify_irr::detail::esp_apply(g, 0);
}

// irr_program_begin(len, sha256_hex) (F2): `len` must be 584; the hash is 64 hex digits.
inline void irr_esp_program_begin(int32_t len, const std::string& sha256_hex) {
  verdify_irr::IrrEsp& g = verdify_irr::irr_esp();
  if (!g.ready) {
    ESP_LOGW("irrig", "irr_program_begin before the engine started: ignored");
    return;
  }
  uint8_t hash[32];
  size_t n = 0;
  const bool ok = verdify_policy::policy_hex_decode(sha256_hex.c_str(), sha256_hex.size(), hash, sizeof(hash), &n) &&
                  n == sizeof(hash);
  verdify_irr::irr_staging_begin(g.staging, len, ok ? hash : nullptr);
}

// irr_program_chunk(offset, data_hex) (F2): at most 192 B, with offset + n <= 584.
inline void irr_esp_program_chunk(int32_t offset, const std::string& data_hex) {
  verdify_irr::IrrEsp& g = verdify_irr::irr_esp();
  if (!g.ready) return;
  static uint8_t chunk[verdify_irr::kChunkMax];
  size_t n = 0;
  const bool ok = verdify_policy::policy_hex_decode(data_hex.c_str(), data_hex.size(), chunk, sizeof(chunk), &n);
  if (!verdify_irr::irr_staging_chunk(g.staging, offset, ok ? chunk : nullptr, ok ? n : 0)) {
    ESP_LOGW("irrig", "irr_program_chunk@%d refused (%u hex chars)", int(offset), unsigned(data_hex.size()));
  }
}

// irr_program_commit() (F2): decode; on success save to NVS, sync, and run it from the next tick
// (irr_program_id then reads src=nvs with its hash). A rejected record keeps the old program and
// id and shows its token in irr_state (j=). A save or sync that fails keeps the old program too;
// with no reject token, the ingestor's next pass pushes again.
inline void irr_esp_program_commit() {
  verdify_irr::IrrEsp& g = verdify_irr::irr_esp();
  if (!g.ready) {
    ESP_LOGW("irrig", "irr_program_commit before the engine started: ignored");
    return;
  }
  verdify_irr::IrrProgram p{};
  verdify_irr::IrrReject why = verdify_irr::IrrReject::kNone;
  switch (verdify_irr::irr_staging_commit(g.staging, p, why)) {
    case verdify_irr::IrrCommit::kNotOpen:
      ESP_LOGW("irrig", "irr_program_commit with no irr_program_begin: ignored");
      return;
    case verdify_irr::IrrCommit::kRejected:
      verdify_irr::irr_set_reject(g.eng, why);
      ESP_LOGW("irrig", "Irrigation program refused: %s", verdify_irr::irr_reject_token(why));
      break;
    case verdify_irr::IrrCommit::kAccepted:
      if (!g.program_pref.save(&g.staging.record) || !esphome::global_preferences->sync()) {
        ESP_LOGE("irrig", "Irrigation program not saved to NVS: the old program keeps running");
        return;
      }
      verdify_irr::irr_stage_program(g.eng, p, g.staging.record.sha256);
      ESP_LOGW("irrig", "Irrigation program accepted (%u slots); it runs from the next tick", unsigned(p.slot_count));
      break;
  }
  verdify_irr::irr_refresh_outputs(g.eng, esphome::millis(), g.out);
  verdify_irr::detail::esp_apply(g, 0);
}

// irr_command(cmd, line, on_s, count, gap_s, prewet_s, flush_s) (F4), line the device_idx or 255.
// run: irr_run_now() starts it at once or refuses it (a repeated send is line_busy and starts
// nothing). stop: ends the runs in scope (stopped), clears their grant bits and turns their relays
// off inside this call, never deferred. clear_fault: zeroes a mister zone's watchdog fault count
// and latch (D-IRP-19, #1161; irr_fault_clear()), or every zone's with 255.
inline void irr_esp_command(const std::string& cmd, int32_t line, int32_t on_s, int32_t count, int32_t gap_s,
                            int32_t prewet_s, int32_t flush_s) {
  using verdify_irr::IrrCmd;
  verdify_irr::IrrEsp& g = verdify_irr::irr_esp();
  if (!g.ready) {
    ESP_LOGW("irrig", "irr_command %s before the engine started: ignored", cmd.c_str());
    return;
  }
  const uint32_t now_ms = esphome::millis();
  switch (verdify_irr::irr_parse_cmd(cmd.c_str())) {
    case IrrCmd::kRun: {
      verdify_irr::IrrRunArgs a{};
      verdify_irr::IrrRunResult res = verdify_irr::IrrRunResult::kBadArgs;
      if (verdify_irr::irr_run_args_from(line, on_s, count, gap_s, prewet_s, flush_s, a)) {
        res = verdify_irr::irr_run_now(g.eng, a, now_ms, g.out);
      }
      verdify_irr::detail::esp_apply(g, 0);
      ESP_LOGW("irrig", "irr_command run line=%d: %s", int(line), verdify_irr::kRunResultTokens[uint8_t(res)]);
      return;
    }
    case IrrCmd::kStop: {
      uint16_t scope = 0;
      if (!verdify_irr::irr_stop_scope(line, scope)) {
        ESP_LOGW("irrig", "irr_command stop line=%d: not a wet line", int(line));
        return;
      }
      const uint8_t ended = verdify_irr::irr_stop(g.eng, uint8_t(line), now_ms, g.out);
      verdify_irr::detail::esp_apply(g, scope);
      ESP_LOGW("irrig", "irr_command stop line=%d: %u ended", int(line), unsigned(ended));
      return;
    }
    case IrrCmd::kClearFault: {
      // A latch is a mister zone's (D-IRP-19): zero the line's zone's count and latch, or every
      // zone's (255). A back-off still running ends at its own time.
      const bool all = line == verdify_irr::kAllLines;
      if (!all && !(line >= 0 && line < verdify_irr::kLineCount && verdify_irr::kMistZone[line] < verdify_irr::kZoneCount)) {
        ESP_LOGW("irrig", "irr_command clear_fault line=%d: not a mister", int(line));
        return;
      }
      const bool cleared = verdify_irr::irr_fault_clear(*g.eng.rtc, all ? verdify_irr::kAllLines : uint8_t(line));
      verdify_irr::detail::esp_faults_changed(g, 0, now_ms);
      ESP_LOGW("irrig", "irr_command clear_fault line=%d: %s", int(line), cleared ? "cleared" : "nothing to clear");
      return;
    }
    case IrrCmd::kUnknown:
    default:
      ESP_LOGW("irrig", "irr_command: unknown cmd '%s'", cmd.c_str());
      return;
  }
}

// ── controls.yaml: the climate path ──────────────────────────────────────────

// The climate loop's part of the engine inputs, written every 1 s tick: modes and measurements
// only (there is no leak input, Q-05). The 5 s tick adds the clock, the relays and the faults.
inline verdify_irr::IrrInputs& irr_climate_inputs() { return verdify_irr::irr_esp().climate; }

// The VPD source per mister zone for irr_state's v= token (D-IRP-13). Center has no probe and
// always reads the house average.
inline void irr_set_vpd_sources(VpdSource south, VpdSource west) {
  auto code = [](VpdSource s) { return s == VpdSource::OWN ? 'o' : s == VpdSource::PEER ? 'p' : 'h'; };
  char* v = verdify_irr::irr_esp().extras.vpd_source;
  v[0] = 'h';
  v[1] = code(south);
  v[2] = code(west);
}

// A drip holds the water: climate mist and fog wait (irrigation_water_conflict).
inline bool irr_drip_active() {
  const verdify_irr::IrrEsp& g = verdify_irr::irr_esp();
  return g.ready && verdify_irr::irr_drip_active(g.eng);
}

// L03 is open or about to open: fog stays OFF (SAF-5; the engine's l03_hold).
inline bool irr_l03_hold() {
  const verdify_irr::IrrEsp& g = verdify_irr::irr_esp();
  return g.ready && g.out.l03_hold;
}

// SAF-4 (F11): the program's ceiling against the RTC block's centi-gallons.
inline bool irr_saf4_tripped() {
  const verdify_irr::IrrEsp& g = verdify_irr::irr_esp();
  return g.ready && g.eng.rtc->water_centigal >= uint32_t(g.eng.prog.saf4_gal) * 100u;
}
inline unsigned irr_saf4_gal() {
  const verdify_irr::IrrEsp& g = verdify_irr::irr_esp();
  return g.ready ? unsigned(g.eng.prog.saf4_gal) : 0u;
}

// F6 (#1161): may a climate VPD pulse use clean mister `idx` (L04, L07, L09) now?
// irr_vpd_zone_allowed() on the running program: the climate's demand and safety gate, an
// effective VPD member, no watchdog back-off or latch on the zone, its duty cap (F11: the clean
// line's daily_cap_min against every mister second on the zone, any origin) and SAF-4. Before the
// engine starts nothing may pulse (no grant could be given either).
inline bool irr_climate_zone_allowed(uint8_t idx, bool demand, bool safety_ok, bool saf4_tripped) {
  const verdify_irr::IrrEsp& g = verdify_irr::irr_esp();
  if (!g.ready) return false;
  verdify_irr::IrrVpdZoneGate gate{demand, safety_ok, saf4_tripped, 0, 0};
  verdify_irr::irr_fault_masks(*g.eng.rtc, gate.backoff_mask, gate.latch_mask);
  return verdify_irr::irr_vpd_zone_allowed(g.eng.prog, *g.eng.rtc, idx, gate);
}

// F10 (D-IRP-19, #1161): the mister watchdog, from the climate loop each second
// (irr_watchdog_step()). Every wet relay that reads ON without its grant bit, and every mister ON
// longer than its commanded length + 30 s (300 s when none is known), is turned OFF here; an
// overrun faults its zone (back-off or latch, in the RTC block), the engine drops the faulted
// open at once (irr_apply_faults()) and irr_state carries the end time. Returns the misters that
// faulted, so the climate path ends its own pulse train on them.
inline uint16_t irr_mister_watchdog() {
  verdify_irr::IrrEsp& g = verdify_irr::irr_esp();
  if (!g.ready || g.b.grant_mask == nullptr) return 0;
  const uint32_t now_ms = esphome::millis();
  const verdify_irr::IrrWatchdogInputs in = verdify_irr::irr_watchdog_inputs(
      g.eng, g.out, now_ms, verdify_irr::detail::esp_relays_on(g), *g.b.grant_mask);
  const verdify_irr::IrrWatchdogResult r = verdify_irr::irr_watchdog_step(g.wd, *g.eng.rtc, in);
  for (uint8_t i = 0; i < verdify_irr::kLineCount; ++i) {
    if (!(r.force_off & verdify_irr::irr_bit(i)) || g.b.relay[i] == nullptr) continue;
    g.b.relay[i]->turn_off();
    if (r.faulted & verdify_irr::irr_bit(i)) {
      ESP_LOGE("irrig", "Mister watchdog: %s ON past its commanded pulse + 30 s, forced OFF (D-IRP-19)",
               verdify_irr::kLineIds[i]);
    } else {
      ESP_LOGE("irrig", "Mister watchdog: %s ON without its engine grant, forced OFF", verdify_irr::kLineIds[i]);
    }
  }
  if (r.changed || r.faulted != 0) verdify_irr::detail::esp_faults_changed(g, r.faulted, now_ms);
  return r.faulted;
}

// Flow-meter water for SAF-4 and the soft budget: centi-gallons into the RTC block, the fraction
// carried to the next call; mister_water_today mirrors it at once.
inline void irr_add_water(float gpm, uint32_t dt_ms) {
  verdify_irr::IrrEsp& g = verdify_irr::irr_esp();
  const uint32_t centigal = verdify_irr::irr_water_centigal(gpm, dt_ms, g.water_carry);
  if (centigal == 0) return;
  if (!g.ready) {
    g.water_before_start += centigal;
    return;
  }
  verdify_irr::irr_count_water(g.eng, centigal);
  verdify_irr::detail::esp_mirror(g);
}

// The engine's own run (a scheduled window, a feed, a mist prewet or flush) holds this relay.
inline bool irr_engine_holds(uint8_t idx) {
  const verdify_irr::IrrEsp& g = verdify_irr::irr_esp();
  return g.ready && (g.out.relay_mask & verdify_irr::irr_bit(idx)) != 0;
}

// A climate VPD pulse asks the engine for the mist manifold before it turns its zone's mister on
// (F5; L04, L07 or L09, F6): true only on a grant, whose bit is then set and whose m=<idx>,vpd
// token is published.
// `pulse_ms` is the pulse's commanded length: the token's planned end, and the grant lapses
// 30 s after it if never released.
inline bool irr_vpd_grant(uint8_t idx, uint32_t pulse_ms) {
  verdify_irr::IrrEsp& g = verdify_irr::irr_esp();
  if (!g.ready) return false;
  const uint32_t s = (pulse_ms + 999u) / 1000u;
  const verdify_irr::IrrVpdResult r =
      verdify_irr::irr_vpd_request(g.eng, idx, uint16_t(s > 0xFFFFu ? 0xFFFFu : s), esphome::millis(), g.out);
  verdify_irr::detail::esp_apply(g, 0);
  return r.answer == verdify_irr::IrrVpdAnswer::kGrant;
}

// The climate VPD pulse on `idx` ended and its relay is off: hand the manifold back (the bit
// clears now, after the turn_off).
inline void irr_vpd_release(uint8_t idx) {
  verdify_irr::IrrEsp& g = verdify_irr::irr_esp();
  if (!g.ready) return;
  verdify_irr::irr_vpd_release(g.eng, idx, esphome::millis(), g.out);
  verdify_irr::detail::esp_apply(g, 0);
}

// The climate path closes a mister: never one the engine's own run holds; closing its own VPD
// pulse also hands the manifold back.
inline void irr_climate_close(esphome::switch_::Switch* sw, uint8_t idx) {
  if (sw == nullptr || idx >= verdify_irr::kLineCount || irr_engine_holds(idx)) return;
  if (sw->state) sw->turn_off();
  const verdify_irr::IrrEsp& g = verdify_irr::irr_esp();
  if (g.ready && g.eng.vpd_line == idx) irr_vpd_release(idx);
}

#endif  // ESP_PLATFORM
