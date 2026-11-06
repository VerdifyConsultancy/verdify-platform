// test_irrigation_program.cpp — native tests for the irrigation engine core
// (firmware/lib/irrigation_program.h; irrigation §4-§5, F1; VD-44, #1159) and its
// device wiring (VD-45, #1160).
//
// The decoder runs on #1151's golden vectors and on Python-computed reject cases
// (firmware/test/irrigation_program_vectors_generated.inc, rendered by
// scripts/gen-irrigation-program-vectors.py from the one golden-vector JSON). The
// engine runs scripted traces through irr_tick() with fake NVS save and sync
// functions that can fail, soft resets and power cuts. Every check failure prints
// and the binary exits non-zero, so a failure fails the native firmware step of
// scripts/ci-local.sh.
//
// Section F is the wiring's replay suite for the wet relays (the climate replay corpus
// has no drip relay columns): a device model with hardware.yaml's grant guards, the
// glue order of irrigation_engine_esphome.h (publish, grant bits, L03 order), the
// climate VPD pulse path and the services, run through day-long traces. Every exact
// irr_* string it expects is an EXPECT_IRR_* literal, which
// verdify_schemas/tests/test_irrigation_firmware_strings.py parses with #1151's parsers.
//
// Build + run (make test-irrigation-program, part of test-firmware and of the native
// firmware step of scripts/ci-local.sh):
//   cd firmware && g++ -std=c++17 -I lib -o test/test_irrigation_program test/test_irrigation_program.cpp
//   ./test/test_irrigation_program

#include "greenhouse_logic.h"  // the fog force-off (F9, #1161) the device model's climate side uses
#include "irrigation_program.h"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <initializer_list>
#include <type_traits>
#include <utility>

#include "irrigation_program_vectors_generated.inc"
#include "policy_vector.h"  // policy_hex_decode(), as the device's program services decode chunks

using namespace verdify_irr;

static int checks_run = 0;
static int checks_failed = 0;

#define CHECK(cond, label)                                     \
  do {                                                         \
    ++checks_run;                                              \
    if (!(cond)) {                                             \
      ++checks_failed;                                         \
      std::printf("FAIL: %s (line %d)\n", (label), __LINE__); \
    }                                                          \
  } while (0)

static_assert(std::is_trivially_copyable<IrrEngine>::value, "the engine is plain data, no heap");
static_assert(std::is_trivially_copyable<IrrOutputs>::value, "outputs are plain data");

// ── Fixtures ────────────────────────────────────────────────────────────────

// 2026-11-02 00:00 America/Denver (MST, UTC-7), a Monday: local day 20759.
static constexpr uint32_t kDay0 = 1793602800u;
static constexpr uint16_t kDayNum = 20759;
static constexpr int kMst = -420;
static constexpr int kMdt = -360;
static constexpr uint8_t kEveryDay = 0x7F;

static uint32_t at(int minute, int second = 0) { return kDay0 + uint32_t(minute) * 60u + uint32_t(second); }

static void put16(uint8_t* p, uint16_t v) {
  p[0] = uint8_t(v);
  p[1] = uint8_t(v >> 8);
}
static void put32(uint8_t* p, uint32_t v) {
  for (int i = 0; i < 4; ++i) p[i] = uint8_t(v >> (8 * i));
}

// A test-only packer (the controller never packs; the platform does, in Python).
static void pack(const IrrProgram& p, uint8_t out[kRecordLen]) {
  std::memset(out, 0, kRecordLen);
  std::memcpy(out, kMagic, 4);
  put16(out + 4, p.saf4_gal);
  out[6] = p.rh_ceiling_pct;
  out[7] = p.climate_yield_min;
  put16(out + 8, p.enabled_mask);
  out[10] = p.vpd_member_mask;
  out[11] = p.schedule_member_mask;
  out[12] = p.slot_count;
  for (int i = 0; i < kLineCount; ++i) {
    uint8_t* l = out + kLinesOffset + i * kLineLen;
    put16(l, p.lines[i].daily_cap_min);
    put16(l + 2, p.lines[i].vpd_reserve_min);
    put32(l + 4, p.lines[i].paused_until);
  }
  for (int i = 0; i < p.slot_count; ++i) {
    const IrrSlot& s = p.slots[i];
    uint8_t* b = out + kSlotsOffset + i * kSlotLen;
    put16(b, s.slot_id);
    b[2] = s.line;
    b[3] = s.weekday_mask;
    b[4] = s.start_kind;
    b[5] = s.flags;
    put16(b + 6, uint16_t(s.start));
    put16(b + 8, s.on_s);
    put16(b + 10, s.gap_s);
    put16(b + 12, s.prewet_s);
    put16(b + 14, s.flush_s);
    b[16] = s.count;
  }
}

static bool same_program(const IrrProgram& a, const IrrProgram& b) {
  uint8_t ra[kRecordLen], rb[kRecordLen];
  pack(a, ra);
  pack(b, rb);
  return std::memcmp(ra, rb, kRecordLen) == 0;
}

static void add_slot(IrrProgram& p, uint16_t id, uint8_t line, int16_t start, uint16_t on_s, uint8_t count = 1,
                     uint16_t gap_s = 0, uint16_t prewet_s = 0, uint16_t flush_s = 0, bool degraded = false,
                     uint8_t kind = 0, uint8_t days = kEveryDay) {
  IrrSlot& s = p.slots[p.slot_count++];
  s = IrrSlot{};
  s.slot_id = id;
  s.line = line;
  s.weekday_mask = days;
  s.start_kind = kind;
  s.flags = degraded ? kSlotFlagDegradedOnly : 0;
  s.start = start;
  s.on_s = on_s;
  s.gap_s = gap_s;
  s.prewet_s = prewet_s;
  s.flush_s = flush_s;
  s.count = count;
}

static void enable(IrrProgram& p, uint8_t line, bool on = true) {
  if (on) p.enabled_mask |= irr_bit(line);
  else p.enabled_mask &= uint16_t(~irr_bit(line));
}
static void members(IrrProgram& p, uint8_t line, bool vpd, bool sched) {
  const uint8_t b = uint8_t(1u << kMisterMaskBit[line]);
  p.vpd_member_mask = uint8_t(vpd ? (p.vpd_member_mask | b) : (p.vpd_member_mask & ~b));
  p.schedule_member_mask = uint8_t(sched ? (p.schedule_member_mask | b) : (p.schedule_member_mask & ~b));
}

static bool decode_vector(size_t i, IrrProgram& out) {
  IrrReject why;
  return irr_decode(kGoldenVectorBytes[i], kRecordLen, kGoldenVectorSha256[i], out, why);
}

// NVS as ESPHome has it: save() fills a cache, sync() writes flash; a power cut loses
// the cache. Either step can be made to fail.
struct Nvs {
  IrrClaims flash{};
  bool flash_has = false;
  IrrClaims cache{};
  bool cache_has = false;
  bool fail_save = false;
  bool fail_sync = false;
  int saves = 0;
  int syncs = 0;
};
static bool nvs_save(const IrrClaims& c, void* ctx) {
  Nvs* n = static_cast<Nvs*>(ctx);
  ++n->saves;
  if (n->fail_save) return false;
  n->cache = c;
  n->cache_has = true;
  return true;
}
static bool nvs_sync(void* ctx) {
  Nvs* n = static_cast<Nvs*>(ctx);
  ++n->syncs;
  if (n->fail_sync) return false;
  if (n->cache_has) {
    n->flash = n->cache;
    n->flash_has = true;
  }
  return true;
}

// A controller: the engine, its RTC block and NVS, the relays it drives, and a
// minimal climate side (a VPD pulse train and fog) that uses the engine's API.
struct Sim {
  IrrRtcBlock rtc{};
  Nvs nvs{};
  IrrEngine eng{};
  IrrInputs in = irr_inputs_idle();
  IrrOutputs out{};
  uint32_t ms = 100000u;
  uint32_t epoch = kDay0;
  int offset = kMst;
  bool clock_ok = true;
  uint16_t relays = 0;
  // Climate side.
  uint8_t vpd_want = kNoLine;  // the zone it wants to pulse (one pulse, then the gap)
  uint16_t vpd_on_s = 30;
  uint16_t vpd_gap_s = 0;
  uint8_t vpd_on = kNoLine;
  uint32_t vpd_until = 0;
  uint32_t vpd_next = 0;
  int vpd_grants = 0;
  int vpd_served = 0;
  bool fog_want = false;
  bool fog_follows_engine = false;  // the climate fogs for a fog run-once (#1343 follow-up), held by occupancy and SF
  // Records.
  uint32_t open_s[kLineCount] = {};  // valve-open seconds per line, sampled per tick
  uint16_t seq[1024] = {};           // the relay masks in order (each change)
  int seq_n = 0;
  char holders[256] = {};            // mist holder per change: W engine mister, V VPD pulse
  int holders_n = 0;
  int state_changes = 0;
  bool two_misters = false;
  bool drip_with_mist = false;
  bool l03_alone = false;
  bool l03_with_fog = false;
  bool l03_with_clean_mist = false;
  bool l03_with_clean = false;  // #1336: L03 open with any clean wet relay (L01, L04, L05, L07, L09, fog)
  bool ungranted = false;
  bool mist_in_sf = false;
  bool mist_occupied = false;  // an engine mister relay ON while occupancy is confirmed
  uint16_t disabled_opened = 0;  // engine relays ON while their line is disabled (L03 has its own rule)

  void boot(bool power_on, const IrrProgram* prog) {
    if (power_on) nvs.cache_has = false;  // the cache dies with the power
    nvs.cache = nvs.flash;
    nvs.cache_has = nvs.flash_has;
    IrrPersist p;
    p.save = nvs_save;
    p.sync = nvs_sync;
    p.ctx = &nvs;
    irr_engine_init(eng, &rtc, p, power_on, nvs.flash_has ? &nvs.flash : nullptr);
    if (prog != nullptr) {
      uint8_t rec[kRecordLen];
      uint8_t sha[32];
      pack(*prog, rec);
      verdify_policy::Sha256::hash(rec, kRecordLen, sha);
      IrrProgram decoded{};
      IrrReject why;
      const bool ok = irr_decode(rec, kRecordLen, sha, decoded, why);
      CHECK(ok, "test program decodes");
      if (ok) irr_use_program(eng, decoded, sha);
    }
    relays = 0;  // a reset turns every relay off
    vpd_on = kNoLine;
  }

  void push(const IrrProgram& prog) {
    uint8_t rec[kRecordLen];
    uint8_t sha[32];
    pack(prog, rec);
    verdify_policy::Sha256::hash(rec, kRecordLen, sha);
    IrrProgram decoded{};
    IrrReject why;
    const bool ok = irr_decode(rec, kRecordLen, sha, decoded, why);
    CHECK(ok, "pushed program decodes");
    if (ok) irr_stage_program(eng, decoded, sha);
  }

  // The climate side's fog follows the engine's flags as #1160/#1161 wire them:
  // irrigation_water_conflict (drip_active) and SAF-5 (l03_hold) force it off.
  void record() {
    const uint16_t climate = uint16_t((vpd_on != kNoLine ? irr_bit(vpd_on) : 0u) |
                                      (fog_want && !out.drip_active && !out.l03_hold ? kFogMask : 0u) |
                                      (fog_follows_engine && out.fog_run && !in.occupied && !in.sensor_fault &&
                                               !out.l03_hold
                                           ? kFogMask
                                           : 0u));
    const uint16_t now = uint16_t(out.relay_mask | climate);
    if ((out.relay_mask & ~out.grant_mask) != 0) ungranted = true;
    if (seq_n == 0 || seq[seq_n - 1] != now) {
      if (seq_n < 1024) seq[seq_n++] = now;
      const uint16_t mist = now & kMistManifoldMask;
      if (mist != 0 && holders_n < 255) {
        const char h = (out.relay_mask & kMistManifoldMask) ? 'W' : 'V';
        if (holders_n == 0 || holders[holders_n - 1] != h || (seq_n >= 2 && (seq[seq_n - 2] & kMistManifoldMask) == 0)) {
          holders[holders_n++] = h;
        }
      }
    }
    const uint16_t mist = now & kMistManifoldMask;
    if (mist & (mist - 1)) two_misters = true;
    if ((now & kDripManifoldMask) && (now & (kMistManifoldMask | kFogMask))) drip_with_mist = true;
    if ((now & irr_bit(kL03)) && !(now & (irr_bit(kL02) | irr_bit(kL06) | irr_bit(kL08) | irr_bit(kL10)))) {
      l03_alone = true;
    }
    if ((now & irr_bit(kL03)) && (now & kFogMask)) l03_with_fog = true;
    if ((now & irr_bit(kL03)) && (now & kDegradedOnlyMask)) l03_with_clean_mist = true;
    if ((now & irr_bit(kL03)) && (now & kCleanWetMask)) l03_with_clean = true;
    if (in.sensor_fault && (out.relay_mask & kMistManifoldMask)) mist_in_sf = true;
    if (in.occupied && (out.relay_mask & kMistManifoldMask)) mist_occupied = true;
    disabled_opened |= uint16_t(out.relay_mask & ~eng.prog.enabled_mask & ~irr_bit(kL03));
    relays = now;
  }

  void climate_step() {
    if (vpd_on != kNoLine && irr_reached(ms, vpd_until)) {
      irr_vpd_release(eng, vpd_on, ms, out);
      vpd_on = kNoLine;
      vpd_next = ms + uint32_t(vpd_gap_s) * 1000u;
    }
    if (vpd_on == kNoLine && vpd_want != kNoLine && irr_reached(ms, vpd_next)) {
      const IrrVpdResult r = irr_vpd_request(eng, vpd_want, vpd_on_s, ms, out);
      if (r.answer == IrrVpdAnswer::kGrant) {
        vpd_on = vpd_want;
        vpd_until = ms + uint32_t(vpd_on_s) * 1000u;
        ++vpd_grants;
      } else if (r.answer == IrrVpdAnswer::kServed) {
        ++vpd_served;
        vpd_next = ms + uint32_t(vpd_on_s + vpd_gap_s) * 1000u;
      }
    }
  }

  void tick(uint32_t step_s = 5) {
    for (int i = 0; i < kLineCount; ++i) {
      if (relays & irr_bit(uint8_t(i))) open_s[i] += step_s;
    }
    ms += step_s * 1000u;
    epoch += step_s;
    in.now_ms = ms;
    in.clock = clock_ok ? irr_clock_from_epoch(epoch, offset) : irr_clock_invalid();
    in.relay_on_mask = relays;
    irr_tick(eng, in, out);
    if (out.state_changed) ++state_changes;
    climate_step();
    record();
  }

  void run_for(uint32_t seconds, uint32_t step_s = 5) {
    for (uint32_t t = 0; t < seconds; t += step_s) tick(step_s);
  }

  // Run until local minute `minute` (and `second`) of the test day.
  void run_until(int minute, int second = 0) {
    const uint32_t target = at(minute, second);
    while (epoch < target) tick(5);
  }

  void stop(uint8_t line) {
    irr_stop(eng, line, ms, out);
    record();
  }
  IrrRunResult run_now(uint8_t line, uint16_t on_s, uint8_t count = 1, uint16_t gap_s = 0, uint16_t prewet_s = 0,
                       uint16_t flush_s = 0) {
    IrrRunArgs a{line, on_s, count, gap_s, prewet_s, flush_s};
    const IrrRunResult r = irr_run_now(eng, a, ms, out);
    record();
    return r;
  }

  int nonzero_seq(uint16_t* dst, int cap) const {
    int n = 0;
    for (int i = 0; i < seq_n && n < cap; ++i) {
      if (seq[i] != 0 && (n == 0 || dst[n - 1] != seq[i])) dst[n++] = seq[i];
    }
    return n;
  }

  // The newest ring entry, or nullptr.
  const IrrEventEntry* last_event() const {
    return rtc.ring_count ? &irr_ring_at(rtc, uint8_t(rtc.ring_count - 1)) : nullptr;
  }
  int count_events(uint16_t slot_id, uint8_t line) const {
    int n = 0;
    for (uint8_t i = 0; i < rtc.ring_count; ++i) {
      const IrrEventEntry& e = irr_ring_at(rtc, i);
      if (e.slot_id == slot_id && e.line == line) ++n;
    }
    return n;
  }
  const IrrEventEntry* find_event(uint16_t slot_id, uint8_t line) const {
    const IrrEventEntry* found = nullptr;
    for (uint8_t i = 0; i < rtc.ring_count; ++i) {
      const IrrEventEntry& e = irr_ring_at(rtc, i);
      if (e.slot_id == slot_id && e.line == line) found = &e;
    }
    return found;
  }
};

static bool event_is(const IrrEventEntry* e, IrrOutcome o, IrrReason r) {
  return e != nullptr && e->outcome == uint8_t(o) && e->reason == uint8_t(r);
}

static bool has_wait(const IrrOutputs& out, uint8_t line, IrrReason r) {
  for (uint8_t i = 0; i < out.state.wait_count; ++i) {
    if (out.state.waits[i].line == line && out.state.waits[i].reason == r) return true;
  }
  return false;
}

// A program with every wet line enabled, both mister flags on, caps off, no slots.
static IrrProgram open_program() {
  IrrProgram p = irr_rom_default();
  p.enabled_mask = 0x03FF;  // L01-L10
  p.vpd_member_mask = 0x07;
  p.schedule_member_mask = 0x07;
  for (IrrLine& l : p.lines) l = IrrLine{0, 0, 0};
  return p;
}

static SolarTimes day0_solar() { return irr_clock_from_epoch(kDay0 + 43200u, kMst).solar; }

// ── A. Fixed codes match verdify_schemas/irrigation.py ───────────────────────

static void codes_match_python() {
  CHECK(kRecordLen == kPyRecordLen && kChunkMax == kPyChunkMax, "record length and chunk size");
  CHECK(kLinesOffset == kPyLinesOffset && kSlotsOffset == kPySlotsOffset, "lines and slots offsets");
  CHECK(kMaxSlots == kPyMaxSlots && kLineCount == kPyLineCount, "24 slots, 11 lines");
  CHECK(std::memcmp(kMagic, kPyMagic, 4) == 0, "magic VIP1");
  CHECK(kSlotFlagDegradedOnly == kPySlotFlagDegradedOnly, "degraded_only flag bit");
  for (int i = 0; i < kLineCount; ++i) CHECK(std::strcmp(kLineIds[i], kPyLineIds[i]) == 0, "device_idx line ids");
  int mister_bits = 0;
  for (uint8_t i = 0; i < kLineCount; ++i) {
    if (kMisterMaskBit[i] != 255) ++mister_bits;
  }
  CHECK(mister_bits == int(sizeof(kPyMisterMaskBit) / sizeof(kPyMisterMaskBit[0])), "three mister mask bits");
  for (const auto& mb : kPyMisterMaskBit) CHECK(kMisterMaskBit[mb[0]] == mb[1], "MISTER_MASK_BIT L04 0, L07 1, L09 2");
  for (int k = 0; k < 4; ++k) {
    CHECK(kStartRange[k][0] == kPyStartRange[k][0] && kStartRange[k][1] == kPyStartRange[k][1], "START_RANGE");
  }
  CHECK(std::strcmp(kPyStartKinds[uint8_t(IrrStartKind::kClock)], "clock") == 0 &&
            std::strcmp(kPyStartKinds[uint8_t(IrrStartKind::kSunrise)], "sunrise") == 0 &&
            std::strcmp(kPyStartKinds[uint8_t(IrrStartKind::kSolarNoon)], "solar_noon") == 0 &&
            std::strcmp(kPyStartKinds[uint8_t(IrrStartKind::kSunset)], "sunset") == 0,
        "START_KIND 0-3");
  CHECK(kSchedulableMask == kPySchedulableMask, "SCHEDULABLE_IDX");
  CHECK(kDripManifoldMask == kPyDripManifoldMask && kMistManifoldMask == kPyMistManifoldMask, "manifold sets");
  CHECK(kDegradedOnlyMask == kPyDegradedOnlyMask, "DEGRADED_ONLY_IDX");
  CHECK(sizeof(kPyCauses) / sizeof(kPyCauses[0]) == kCauseCount, "seven device causes");
  for (int i = 0; i < kCauseCount; ++i) CHECK(std::strcmp(kCauseNames[i], kPyCauses[i]) == 0, "IrrigationCause order");
  CHECK(sizeof(kPyOutcomeCodes) / sizeof(kPyOutcomeCodes[0]) == kOutcomeCount, "seven outcome codes");
  for (int i = 0; i < kOutcomeCount; ++i) CHECK(std::strcmp(kOutcomeCodes[i], kPyOutcomeCodes[i]) == 0, "OUTCOME_CODES");
  CHECK(sizeof(kPyReasonCodes) / sizeof(kPyReasonCodes[0]) == kReasonCount - 1u, "twenty reason codes");
  for (int i = 1; i < kReasonCount; ++i) CHECK(std::strcmp(kReasonCodes[i], kPyReasonCodes[i - 1]) == 0, "REASON_WORDS order");
  CHECK(sizeof(kPyRejectCodes) / sizeof(kPyRejectCodes[0]) == 6, "six reject codes");
  for (int i = 0; i < 6; ++i) {
    CHECK(std::strcmp(irr_reject_token(IrrReject(i + 1)), kPyRejectCodes[i]) == 0, "REJECT_CODES order");
  }
  CHECK(std::strcmp(irr_reject_token(IrrReject::kNone), "") == 0, "no reject reads empty");
  CHECK(kRingSize == kPyEventsMax, "irr_events holds 8 outcomes");
  CHECK(std::strlen(kPyRomHash16) == 16, "ROM hash is 16 zeros");
  CHECK(kPyTextMaxLen == 255, "text sensors hold 255 characters");
  // Line kinds, clean partners and per-kind bounds: LINE_KIND, CLEAN_PARTNER and
  // BOUNDS_BY_KIND (irrigation §3, §4.3), so a bounds change in Python cannot leave the
  // decoder rejecting what the compiler packs.
  const char* const kind_names[] = {"drip", "fert_drip", "master", "mister", "fert_mister", "fog"};
  for (int i = 0; i < kLineCount; ++i) {
    CHECK(std::strcmp(kind_names[uint8_t(kLineKind[i])], kPyLineKind[i]) == 0, "LINE_KIND by device_idx");
    CHECK(kCleanPartner[i] == kPyCleanPartner[i], "CLEAN_PARTNER by device_idx");
  }
  const size_t kinds = sizeof(kPyKindBounds) / sizeof(kPyKindBounds[0]);
  CHECK(kinds == 4, "BOUNDS_BY_KIND covers the four schedulable kinds");
  for (size_t k = 0; k < kinds; ++k) {
    const PyKindBounds& py = kPyKindBounds[k];
    uint8_t kind = 0;
    while (kind < 6 && std::strcmp(kind_names[kind], py.kind) != 0) ++kind;
    CHECK(kind < 6, "BOUNDS_BY_KIND kind is known");
    if (kind >= 6) continue;
    const IrrKindBounds b = irr_kind_bounds(IrrLineKind(kind));
    CHECK(b.on_lo == py.field[0][0] && b.on_hi == py.field[0][1] && b.count_lo == py.field[1][0] &&
              b.count_hi == py.field[1][1] && b.gap_lo == py.field[2][0] && b.gap_hi == py.field[2][1] &&
              py.field[3][0] == 0 && b.prewet_hi == py.field[3][1] && py.field[4][0] == 0 &&
              b.flush_hi == py.field[4][1],
          "BOUNDS_BY_KIND equals the decoder's per-kind bounds");
  }
  CHECK(kMistZone[kL04] == 0 && kMistZone[kL07] == 1 && kMistZone[kL08] == 1 && kMistZone[kL09] == 2 &&
            kMistZone[kL10] == 2 && kMistZone[kL01] == 255,
        "mister zones center, south, west");
}

// ── B. Decoder ───────────────────────────────────────────────────────────────

static void decoder_accepts_the_golden_vectors() {
  CHECK(kGoldenVectorCount == 3, "three golden vectors");
  for (size_t v = 0; v < kGoldenVectorCount; ++v) {
    char label[96];
    std::snprintf(label, sizeof(label), "golden vector %s decodes", kGoldenVectorNames[v]);
    uint8_t digest[32];
    verdify_policy::Sha256::hash(kGoldenVectorBytes[v], kRecordLen, digest);
    CHECK(std::memcmp(digest, kGoldenVectorSha256[v], 32) == 0, "C++ SHA-256 equals Python's");
    IrrProgram p{};
    IrrReject why = IrrReject::kBadSlot;
    const bool ok = irr_decode(kGoldenVectorBytes[v], kRecordLen, kGoldenVectorSha256[v], p, why);
    CHECK(ok && why == IrrReject::kNone, label);
    const GoldenProgram& g = kGoldenPrograms[v];
    bool same = p.saf4_gal == g.saf4_gal && p.rh_ceiling_pct == g.rh_ceiling_pct &&
                p.climate_yield_min == g.climate_yield_min && p.enabled_mask == g.enabled_mask &&
                p.vpd_member_mask == g.vpd_member_mask && p.schedule_member_mask == g.schedule_member_mask &&
                p.slot_count == g.slot_count;
    for (int i = 0; i < kLineCount; ++i) {
      same = same && p.lines[i].daily_cap_min == g.lines[i].daily_cap_min &&
             p.lines[i].vpd_reserve_min == g.lines[i].vpd_reserve_min &&
             p.lines[i].paused_until == g.lines[i].paused_until;
    }
    for (int i = 0; i < g.slot_count; ++i) {
      const IrrSlot& s = p.slots[i];
      const GoldenSlot& gs = g.slots[i];
      same = same && s.slot_id == gs.slot_id && s.line == gs.line && s.weekday_mask == gs.weekday_mask &&
             s.start_kind == gs.start_kind && s.degraded_only() == gs.degraded_only && s.start == gs.start &&
             s.on_s == gs.on_s && s.gap_s == gs.gap_s && s.prewet_s == gs.prewet_s && s.flush_s == gs.flush_s &&
             s.count == gs.count;
    }
    std::snprintf(label, sizeof(label), "golden vector %s decodes to Python's ProgramRecord", kGoldenVectorNames[v]);
    CHECK(same, label);
    uint8_t repacked[kRecordLen];
    pack(p, repacked);
    std::snprintf(label, sizeof(label), "golden vector %s repacks byte for byte", kGoldenVectorNames[v]);
    CHECK(std::memcmp(repacked, kGoldenVectorBytes[v], kRecordLen) == 0, label);
    // irr_program_chunk: four chunks of at most 192 B cover the record in order.
    size_t covered = 0;
    for (size_t c = 0; c < kGoldenChunkCount; ++c) {
      CHECK(kGoldenChunks[v][c][0] == covered && kGoldenChunks[v][c][1] <= kChunkMax, "chunks are contiguous, <= 192 B");
      covered += kGoldenChunks[v][c][1];
    }
    CHECK(covered == kRecordLen, "chunks cover all 584 B");
  }
  // irr_program_id h= is the first 16 hex of the SHA-256 the decoder checks.
  for (size_t i = 0; i < kProgramIdSampleCount; ++i) {
    const char* text = kProgramIdSamples[i].text;
    if (std::strstr(text, "src=rom") != nullptr) {
      CHECK(std::strncmp(text + 2, kPyRomHash16, 16) == 0, "src=rom reads h=0000000000000000");
      continue;
    }
    const size_t v = std::strcmp(kProgramIdSamples[i].name, "seed_nvs") == 0 ? 1 : 2;
    char hex[17];
    for (int b = 0; b < 8; ++b) std::snprintf(hex + 2 * b, 3, "%02x", kGoldenVectorSha256[v][b]);
    CHECK(std::strncmp(text + 2, hex, 16) == 0, "irr_program_id h= is the first 16 hex of the SHA-256");
  }
}

static void decoder_rejects_length_and_hash() {
  IrrProgram keep = irr_rom_default();
  const IrrProgram before = keep;
  IrrReject why;
  // Acceptance: a truncated record (583 B) is bad_length.
  CHECK(!irr_decode(kGoldenVectorBytes[1], 583, kGoldenVectorSha256[1], keep, why) && why == IrrReject::kBadLength,
        "583 B -> bad_length");
  CHECK(!irr_decode(kGoldenVectorBytes[1], 585, kGoldenVectorSha256[1], keep, why) && why == IrrReject::kBadLength,
        "585 B -> bad_length");
  CHECK(!irr_decode(nullptr, kRecordLen, kGoldenVectorSha256[1], keep, why) && why == IrrReject::kBadLength,
        "no record -> bad_length");
  // Acceptance: one byte flipped (the hash from irr_program_begin no longer matches) is bad_hash.
  for (size_t at_byte : {size_t(4), size_t(100), size_t(200), size_t(583)}) {
    uint8_t rec[kRecordLen];
    std::memcpy(rec, kGoldenVectorBytes[2], kRecordLen);
    rec[at_byte] ^= 0x01;
    CHECK(!irr_decode(rec, kRecordLen, kGoldenVectorSha256[2], keep, why) && why == IrrReject::kBadHash,
          "one byte flipped -> bad_hash");
  }
  uint8_t sha[32];
  std::memcpy(sha, kGoldenVectorSha256[1], 32);
  sha[31] ^= 0x80;
  CHECK(!irr_decode(kGoldenVectorBytes[1], kRecordLen, sha, keep, why) && why == IrrReject::kBadHash,
        "a mis-hashed begin -> bad_hash");
  CHECK(!irr_decode(kGoldenVectorBytes[1], kRecordLen, nullptr, keep, why) && why == IrrReject::kBadHash,
        "no hash -> bad_hash");
  // The magic is checked before the hash.
  uint8_t rec[kRecordLen];
  std::memcpy(rec, kGoldenVectorBytes[1], kRecordLen);
  rec[0] = 'X';
  CHECK(!irr_decode(rec, kRecordLen, kGoldenVectorSha256[1], keep, why) && why == IrrReject::kBadMagic,
        "bad magic wins over the hash");
  CHECK(same_program(keep, before), "a failed decode leaves the program untouched");
}

// Every reject Python's unpack_program() gives, the decoder gives too (codes from the include).
static void decoder_agrees_with_unpack_program() {
  CHECK(kRejectCaseCount >= 40, "the reject cases cover every section and field");
  for (size_t i = 0; i < kRejectCaseCount; ++i) {
    const RejectCase& c = kRejectCases[i];
    uint8_t rec[kRecordLen];
    std::memcpy(rec, kGoldenVectorBytes[c.base], kRecordLen);
    for (int k = 0; k < c.patch_count; ++k) std::memcpy(rec + c.offset[k], c.bytes[k], c.len[k]);
    uint8_t sha[32];
    verdify_policy::Sha256::hash(rec, kRecordLen, sha);
    IrrProgram p{};
    IrrReject why = IrrReject::kNone;
    const bool ok = irr_decode(rec, kRecordLen, sha, p, why);
    char label[128];
    std::snprintf(label, sizeof(label), "reject case %s -> %s (got %s)", c.name, c.code, irr_reject_token(why));
    CHECK(!ok && std::strcmp(irr_reject_token(why), c.code) == 0, label);
  }
}

// On top of unpack_program(), the per-kind bounds of irrigation §4.3 (VD-37's
// BOUNDS_BY_KIND): Python's record model allows these, the decoder does not.
static void decoder_applies_per_kind_bounds() {
  struct Case {
    const char* name;
    uint8_t vector;
    int slot;
    int field_offset;
    int width;
    int value;
  };
  const Case cases[] = {
      {"mister gap_s 0 (30-600)", 1, 0, 10, 2, 0},
      {"mister gap_s 29", 1, 0, 10, 2, 29},
      {"mister on_s 91 (10-90)", 1, 0, 8, 2, 91},
      {"mister prewet_s 1 (clean mister: 0)", 1, 0, 12, 2, 1},
      {"mister flush_s 1", 1, 0, 14, 2, 1},
      {"drip count 2 (1)", 2, 0, 16, 1, 2},
      {"drip gap_s 30 (0)", 2, 0, 10, 2, 30},
      {"drip prewet_s 10 (clean drip: 0)", 2, 0, 12, 2, 10},
      {"fert drip count 2", 2, 2, 16, 1, 2},
      {"fert drip gap_s 30", 2, 2, 10, 2, 30},
      {"fert mister on_s 91", 2, 5, 8, 2, 91},
      {"fert mister gap_s 0", 2, 5, 10, 2, 0},
  };
  for (const Case& c : cases) {
    uint8_t rec[kRecordLen];
    std::memcpy(rec, kGoldenVectorBytes[c.vector], kRecordLen);
    uint8_t* f = rec + kSlotsOffset + c.slot * kSlotLen + c.field_offset;
    if (c.width == 1) f[0] = uint8_t(c.value);
    else put16(f, uint16_t(c.value));
    uint8_t sha[32];
    verdify_policy::Sha256::hash(rec, kRecordLen, sha);
    IrrProgram p{};
    IrrReject why;
    CHECK(!irr_decode(rec, kRecordLen, sha, p, why) && why == IrrReject::kBadSlot, c.name);
  }
  // The edges themselves are accepted.
  IrrProgram p = open_program();
  add_slot(p, 1, kL01, 0, 10);
  add_slot(p, 2, kL02, 1, 7200, 1, 0, 1800, 1800);
  add_slot(p, 3, kL04, 2, 90, 60, 600);
  add_slot(p, 4, kL08, 3, 10, 1, 30, 0, 0);
  uint8_t rec[kRecordLen];
  uint8_t sha[32];
  pack(p, rec);
  verdify_policy::Sha256::hash(rec, kRecordLen, sha);
  IrrProgram q{};
  IrrReject why;
  CHECK(irr_decode(rec, kRecordLen, sha, q, why) && same_program(p, q), "every kind's bounds are inclusive");
  CHECK(!irr_run_args_in_bounds(kL03, 60, 1, 0, 0, 0) && !irr_run_args_in_bounds(kL11, 60, 1, 0, 0, 0) &&
            !irr_run_args_in_bounds(11, 60, 1, 0, 0, 0),
        "L03, L11 and unknown lines take no runs");
}

static void rom_default_is_todays_behaviour() {
  const IrrProgram rom = irr_rom_default();
  IrrProgram seed{};
  CHECK(decode_vector(1, seed), "seed decodes");
  seed.slot_count = 0;
  std::memset(seed.slots, 0, sizeof(seed.slots));
  CHECK(same_program(rom, seed), "ROM default == the seed program without its slots");
  CHECK(rom.enabled_mask == (irr_bit(kL01) | irr_bit(kL04)), "L01 (run-now) and L04 enabled, fertilizer off");
  CHECK(rom.vpd_member_mask == 1 && rom.schedule_member_mask == 0, "L04 VPD only");
  CHECK(rom.saf4_gal == 360 && rom.rh_ceiling_pct == 90 && rom.climate_yield_min == 15, "D-IRP-06 site values");
  CHECK(rom.lines[kL01].daily_cap_min == 60 && rom.lines[kL04].daily_cap_min == 120 &&
            rom.lines[kL04].vpd_reserve_min == 60 && rom.lines[kL05].daily_cap_min == 30 &&
            rom.lines[kL07].daily_cap_min == 45 && rom.lines[kL09].vpd_reserve_min == 15,
        "D-IRP-06 caps and reserves");
  Sim s;
  s.boot(true, nullptr);
  s.tick();
  CHECK(!s.out.program_nvs, "no stored program: src=rom");
  uint8_t zero[8] = {};
  CHECK(std::memcmp(s.out.program_hash8, zero, 8) == 0, "src=rom reads h=0000000000000000");
  // A pushed program takes over at the next tick: src=nvs with its hash.
  IrrProgram seedp{};
  decode_vector(1, seedp);
  irr_stage_program(s.eng, seedp, kGoldenVectorSha256[1]);
  s.tick();
  CHECK(s.out.program_nvs && std::memcmp(s.out.program_hash8, kGoldenVectorSha256[1], 8) == 0,
        "after a committed push: src=nvs and its hash");
  // A rejected push keeps the program and shows the reject.
  irr_set_reject(s.eng, IrrReject::kBadHash);
  s.tick();
  CHECK(s.out.state.reject == IrrReject::kBadHash && s.out.program_nvs, "a reject is shown, the program kept");
}

// ── C. Clock and due test ────────────────────────────────────────────────────

static void clock_fields() {
  const IrrClock c = irr_clock_from_epoch(kDay0, kMst);
  CHECK(c.valid && c.local_day == kDayNum && c.weekday == 0 && c.minute == 0, "2026-11-02 00:00 MST: day 20759, Monday");
  const IrrClock noon = irr_clock_from_epoch(kDay0 + 12 * 3600, kMst);
  CHECK(noon.minute == 720 && noon.local_day == kDayNum, "12:00 is minute 720");
  CHECK(noon.solar.sunrise_min > 360 && noon.solar.sunrise_min < 450 && noon.solar.sunset_min > 990 &&
            noon.solar.sunset_min < 1050 && noon.solar.solar_noon_min > 690 && noon.solar.solar_noon_min < 735,
        "early-November Longmont solar times");
  CHECK(!irr_clock_from_epoch(0, kMst).valid && !irr_clock_invalid().valid, "no time: invalid");
  const IrrClock sun = irr_clock_from_epoch(kDay0 - 3600, kMst);  // Sunday 23:00
  CHECK(sun.weekday == 6 && sun.local_day == kDayNum - 1 && sun.minute == 1380, "Sunday is weekday 6");
}

static void due_window() {
  IrrSlot s{};
  s.slot_id = 1;
  s.line = kL01;
  s.weekday_mask = 1;  // Monday
  s.start_kind = 0;
  s.start = 420;  // 07:00
  s.on_s = 600;
  s.count = 1;
  IrrOccurrence occ{};
  CHECK(!irr_due(s, irr_clock_from_epoch(at(419, 59), kMst), occ), "not due a second early");
  CHECK(irr_due(s, irr_clock_from_epoch(at(420), kMst), occ) && occ.occ_day == kDayNum &&
            occ.planned_epoch == at(420) && occ.due_until_epoch == at(480),
        "due at 07:00 with its 60 min window");
  CHECK(irr_due(s, irr_clock_from_epoch(at(479, 59), kMst), occ), "still due at 07:59:59");
  CHECK(!irr_due(s, irr_clock_from_epoch(at(480), kMst), occ), "not due at 08:00 (missed from then)");
  CHECK(!irr_due(s, irr_clock_from_epoch(at(420) + 86400, kMst), occ), "weekday mask: not due on Tuesday");
  CHECK(!irr_due(s, irr_clock_invalid(), occ), "no valid time: nothing is due");
  // Solar starts follow the clock's solar times.
  const SolarTimes st = day0_solar();
  s.start_kind = 2;
  s.start = -120;
  CHECK(irr_due(s, irr_clock_from_epoch(at(st.solar_noon_min - 120), kMst), occ) &&
            occ.planned_minute == st.solar_noon_min - 120,
        "solar noon -120");
  s.start_kind = 1;
  s.start = 15;
  CHECK(irr_due(s, irr_clock_from_epoch(at(st.sunrise_min + 15), kMst), occ), "sunrise +15");
  s.start_kind = 3;
  s.start = -240;
  CHECK(irr_due(s, irr_clock_from_epoch(at(st.sunset_min - 240), kMst), occ), "sunset -240");
  // A late start stays due past midnight under yesterday's day and weekday.
  s.start_kind = 0;
  s.start = 1430;        // 23:50
  s.weekday_mask = 1;    // Monday only
  CHECK(irr_due(s, irr_clock_from_epoch(at(1440 + 10), kMst), occ) && occ.occ_day == kDayNum,
        "Monday 23:50 is still due at Tuesday 00:10 as Monday's occurrence");
  CHECK(!irr_due(s, irr_clock_from_epoch(at(1440 + 50), kMst), occ), "and not at 00:50");
  s.weekday_mask = 2;  // Tuesday only
  CHECK(!irr_due(s, irr_clock_from_epoch(at(1440 + 10), kMst), occ), "Tuesday's 23:50 is not due at 00:10");
  // Sunset + 240 can land after midnight: due on the next day under its own day.
  s.start_kind = 3;
  s.start = 240;
  s.weekday_mask = kEveryDay;
  const IrrClock summer = irr_clock_from_epoch(1782460800u, kMdt);  // 2026-06-26 02:00 MDT
  CHECK(summer.minute == 120, "02:00 MDT");
  const int m = irr_planned_minute(s, summer.solar_prev);
  CHECK(m >= 1440, "a June sunset + 240 lands after midnight");
  IrrOccurrence o2{};
  const IrrClock past = irr_clock_from_epoch(summer.epoch - uint32_t((summer.minute - (m - 1440)) * 60), kMdt);
  CHECK(irr_due(s, past, o2) && o2.occ_day == uint16_t(past.local_day - 1),
        "sunset +240 past midnight belongs to the day before");
}

static void due_across_dst() {
  // Spring forward: 2026-03-08 02:00 MST -> 03:00 MDT (09:00 UTC).
  const uint32_t jump = 1772960400u;
  IrrSlot s{};
  s.slot_id = 1;
  s.line = kL01;
  s.weekday_mask = kEveryDay;
  s.start = 150;  // 02:30, inside the skipped hour
  s.on_s = 300;
  s.count = 1;
  IrrOccurrence occ{};
  CHECK(!irr_due(s, irr_clock_from_epoch(jump - 60, kMst), occ), "01:59 MST: not yet");
  const IrrClock after = irr_clock_from_epoch(jump, kMdt);
  CHECK(after.minute == 180, "the clock reads 03:00 MDT");
  CHECK(irr_due(s, after, occ) && occ.occ_day == after.local_day, "02:30 in the skipped hour is due at 03:00");

  // Fall back: 2026-11-01 01:30 happens twice (MDT, then MST); one local day.
  const uint32_t first = 1793518200u;   // 01:30 MDT
  const uint32_t second = 1793521800u;  // 01:30 MST
  const IrrClock c1 = irr_clock_from_epoch(first, kMdt);
  const IrrClock c2 = irr_clock_from_epoch(second, kMst);
  CHECK(c1.minute == 90 && c2.minute == 90 && c1.local_day == c2.local_day && c1.weekday == 6,
        "Sunday 01:30 twice, one local day");

  Sim sim;
  sim.offset = kMdt;
  sim.epoch = first - 120;
  IrrProgram p = irr_rom_default();
  p.lines[kL01].daily_cap_min = 0;
  add_slot(p, 7, kL01, 90, 300, 1, 0, 0, 0, false, 0, 0x40);  // Sundays 01:30
  sim.boot(true, &p);
  while (sim.epoch < second + 1800) {
    sim.offset = sim.epoch < 1793520000u ? kMdt : kMst;  // 08:00 UTC: back to MST
    sim.tick();
  }
  CHECK(sim.count_events(7, kL01) == 1 && event_is(sim.find_event(7, kL01), IrrOutcome::kOk, IrrReason::kNone),
        "the repeated hour runs the occurrence once (the per-day claim)");
  CHECK(sim.open_s[kL01] >= 295 && sim.open_s[kL01] <= 305, "five minutes of water, once");
}

// ── D. CRC, claims, RTC block, ring ──────────────────────────────────────────

static void crc32_check_value() {
  const char* s = "123456789";
  CHECK(irr_crc32(reinterpret_cast<const uint8_t*>(s), 9) == 0xCBF43926u, "CRC-32 check value");
}

static void claims_save_then_sync() {
  IrrClaims c = irr_claims_empty();
  CHECK(irr_claims_valid(c) && c.today_count == 0, "empty claims are valid");
  bool save_ok = true;
  bool sync_ok = true;
  int saves = 0;
  int syncs = 0;
  IrrClaims flash{};
  auto save = [&](const IrrClaims& rec) {
    ++saves;
    if (save_ok) flash = rec;
    return save_ok;
  };
  auto sync = [&]() {
    ++syncs;
    return sync_ok;
  };
  save_ok = false;
  CHECK(!irr_claim(c, 5, kDayNum, save, sync) && !irr_claimed(c, 5, kDayNum) && syncs == 0,
        "a failed save claims nothing and never syncs");
  save_ok = true;
  sync_ok = false;
  CHECK(!irr_claim(c, 5, kDayNum, save, sync) && !irr_claimed(c, 5, kDayNum), "a failed sync claims nothing");
  sync_ok = true;
  CHECK(irr_claim(c, 5, kDayNum, save, sync) && irr_claimed(c, 5, kDayNum) && irr_claims_valid(c),
        "save then sync: claimed");
  CHECK(std::memcmp(&flash, &c, sizeof(c)) == 0, "the saved record is the claimed one");
  CHECK(!irr_claimed(c, 5, kDayNum + 1) && !irr_claimed(c, 6, kDayNum), "keyed by (slot_id, local day)");
  // The next day: yesterday's claims move to prev and still answer.
  CHECK(irr_claim(c, 9, kDayNum + 1, save, sync) && c.local_day == kDayNum + 1, "a claim rolls the day");
  CHECK(irr_claimed(c, 5, kDayNum) && irr_claimed(c, 9, kDayNum + 1), "yesterday's claims are kept for late starts");
  CHECK(irr_claim(c, 11, kDayNum, save, sync) && irr_claimed(c, 11, kDayNum), "a late start claims under yesterday");
  CHECK(!irr_claim(c, 12, kDayNum - 1, save, sync), "older than yesterday: refused");
  // At most 24 a day.
  IrrClaims full = irr_claims_empty();
  for (uint16_t id = 1; id <= 24; ++id) irr_claim(full, id, kDayNum, save, sync);
  CHECK(full.today_count == 24 && !irr_claim(full, 25, kDayNum, save, sync), "24 claims a day, no more");
  IrrProgram live = irr_rom_default();
  add_slot(live, 25, kL01, 0, 60);
  IrrClaims cand = full;
  CHECK(irr_claims_add(cand, 25, kDayNum, &live) && irr_claimed(cand, 25, kDayNum) && cand.today_count == 24,
        "a full day drops a claim whose slot left the program");
  // Corruption is detected.
  IrrClaims bad = c;
  bad.today[0] ^= 1;
  CHECK(!irr_claims_valid(bad), "a flipped claim fails the CRC");
}

static void rtc_block_rules() {
  IrrRtcBlock b{};
  std::srand(20261102);
  uint8_t* raw = reinterpret_cast<uint8_t*>(&b);
  for (size_t i = 0; i < sizeof(b); ++i) raw[i] = uint8_t(std::rand() & 0xFF);
  CHECK(irr_rtc_validate(b, false, kDayNum), "random bytes fail magic, version or CRC32");
  bool zero = b.water_centigal == 0 && b.ring_count == 0 && b.next_seq == 0 && b.run.kind == 0;
  for (uint32_t v : b.line_on_s) zero = zero && v == 0;
  for (uint32_t v : b.zone_pulse_s) zero = zero && v == 0;
  CHECK(zero && b.local_day == kDayNum && b.magic == kRtcMagic, "power-on garbage gives zero counters");
  // A valid block survives a soft reset ...
  b.water_centigal = 12345;
  b.line_on_s[kL01] = 600;
  b.zone_pulse_s[0] = 900;
  irr_ring_push(b, kL01, 4, kDayNum, IrrOutcome::kOk, IrrReason::kNone);
  b.run.kind = 1;
  irr_rtc_seal(b);
  IrrRtcBlock soft = b;
  CHECK(!irr_rtc_validate(soft, false, kDayNum) && soft.water_centigal == 12345 && soft.ring_count == 1,
        "a soft reset keeps the block");
  // ... and is zeroed on power-on.
  IrrRtcBlock power = b;
  CHECK(irr_rtc_validate(power, true, kDayNum) && power.water_centigal == 0 && power.ring_count == 0,
        "power-on zeroes even a valid block");
  // A new local day zeroes only the daily counters.
  IrrRtcBlock next = b;
  CHECK(!irr_rtc_validate(next, false, kDayNum + 1), "a new day is not a reset");
  CHECK(next.water_centigal == 0 && next.line_on_s[kL01] == 0 && next.zone_pulse_s[0] == 0,
        "a new day zeroes the counters");
  CHECK(next.ring_count == 1 && next.next_seq == 1 && next.run.kind == 1 && next.local_day == kDayNum + 1,
        "the ring, its sequence and the active run stay");
  // A block whose day is not known yet adopts the first known day without zeroing.
  IrrRtcBlock unknown = b;
  unknown.local_day = 0;
  irr_rtc_seal(unknown);
  CHECK(!irr_rtc_validate(unknown, false, kDayNum) && unknown.water_centigal == 12345 && unknown.local_day == kDayNum,
        "the first valid time keeps counters gathered before it");
  // Corruption anywhere is caught.
  IrrRtcBlock flip = b;
  flip.zone_pulse_s[2] ^= 0x10;
  CHECK(irr_rtc_validate(flip, false, kDayNum) && flip.water_centigal == 0, "a flipped counter fails the CRC");

  // #1161: the mister watchdog's faults (D-IRP-19). Power-on garbage zeroes them; a soft reset
  // keeps them; a new local day zeroes each zone's count and latch, while a back-off still running
  // keeps its countdown, line and end time.
  IrrRtcBlock g{};
  for (size_t i = 0; i < sizeof(g); ++i) reinterpret_cast<uint8_t*>(&g)[i] = uint8_t(std::rand() & 0xFF);
  irr_rtc_validate(g, true, kDayNum);
  bool faults_zero = true;
  for (const IrrRtcFault& f : g.fault) {
    faults_zero = faults_zero && f.count == 0 && !f.latched && !f.backing_off && f.line == 0 &&
                  f.backoff_left_ms == 0 && f.until_epoch == 0;
  }
  CHECK(faults_zero, "power-on zeroes the watchdog faults");
  CHECK(kRtcVersion == 2, "the fault area is RTC block layout version 2");
  g.fault[0] = IrrRtcFault{3, 1, 0, kL04, 0, kDay0 + 86400u};             // center latched
  g.fault[1] = IrrRtcFault{1, 0, 1, kL07, 420000u, at(1430)};             // south backing off
  irr_rtc_seal(g);
  IrrRtcBlock kept = g;
  CHECK(!irr_rtc_validate(kept, false, kDayNum) && kept.fault[0].latched && kept.fault[1].backing_off,
        "a soft reset keeps the faults");
  IrrRtcBlock rolled = g;
  irr_rtc_validate(rolled, false, kDayNum + 1);
  CHECK(rolled.fault[0].count == 0 && !rolled.fault[0].latched && rolled.fault[0].until_epoch == 0,
        "local midnight ends the latch and zeroes the count");
  CHECK(rolled.fault[1].count == 0 && rolled.fault[1].backing_off && rolled.fault[1].backoff_left_ms == 420000u &&
            rolled.fault[1].line == kL07 && rolled.fault[1].until_epoch == at(1430),
        "a back-off runs across midnight to its own end");
  IrrRtcBlock torn = g;
  torn.fault[2].count = 7;  // not re-sealed
  CHECK(irr_rtc_validate(torn, false, kDayNum) && !torn.fault[0].latched, "a changed fault fails the CRC");
}

static void ring_sequence_and_grammar() {
  IrrRtcBlock b{};
  irr_rtc_validate(b, true, kDayNum);
  for (int i = 0; i < 10; ++i) irr_ring_push(b, kL01, uint16_t(i + 1), kDayNum, IrrOutcome::kOk, IrrReason::kNone);
  CHECK(b.ring_count == 8 && irr_ring_at(b, 0).seq == 2 && irr_ring_at(b, 7).seq == 9 && b.next_seq == 10,
        "the newest 8 outcomes, oldest first");
  b.next_seq = 65535;
  irr_ring_push(b, kL04, 2, kDayNum, IrrOutcome::kSkipped, IrrReason::kVok);
  irr_ring_push(b, kL02, 5, kDayNum, IrrOutcome::kShort, IrrReason::kRst);
  CHECK(irr_ring_at(b, 6).seq == 65535 && irr_ring_at(b, 7).seq == 0 && b.next_seq == 1, "65535 wraps to 0");
  // irr_format_events reproduces every irr_events golden sample from its parsed entries.
  for (size_t s = 0; s < kEventsSampleCount; ++s) {
    IrrRtcBlock r{};
    irr_rtc_validate(r, true, kDayNum);
    if (kEventsParsedCount[s] > 0) r.next_seq = kEventsParsed[s][0].seq;
    for (uint8_t i = 0; i < kEventsParsedCount[s]; ++i) {
      const GoldenEvent& g = kEventsParsed[s][i];
      CHECK(r.next_seq == g.seq, "golden events are consecutive sequence numbers");
      uint8_t o = 0;
      while (o < kOutcomeCount && std::strcmp(kOutcomeCodes[o], g.outcome) != 0) ++o;
      uint8_t rr = 0;
      while (rr < kReasonCount && std::strcmp(kReasonCodes[rr], g.reason) != 0) ++rr;
      CHECK(o < kOutcomeCount && rr < kReasonCount, "golden codes are known");
      irr_ring_push(r, g.line, g.slot_id, g.local_day, IrrOutcome(o), IrrReason(rr));
    }
    char buf[256];
    const size_t n = irr_format_events(r, buf, sizeof(buf));
    char label[96];
    std::snprintf(label, sizeof(label), "irr_events sample %s formats byte for byte", kEventsSamples[s].name);
    CHECK(std::strcmp(buf, kEventsSamples[s].text) == 0 && n == std::strlen(kEventsSamples[s].text), label);
    CHECK(n <= kPyTextMaxLen, "irr_events fits 255 characters");
  }
  char tiny[8];
  CHECK(irr_format_events(b, tiny, sizeof(tiny)) == 0 && tiny[0] == '\0', "a short buffer writes nothing");
}

// ── E. Engine traces ─────────────────────────────────────────────────────────

// Acceptance: power-on with random RTC bytes gives zero counters, and SAF-4 still trips
// at the configured gallons: 360 gal granted at 35999 centi-gallons, refused at 36000.
static void saf4_trips_at_the_program_gallons() {
  Sim s;
  uint8_t* raw = reinterpret_cast<uint8_t*>(&s.rtc);
  std::srand(4242);
  for (size_t i = 0; i < sizeof(s.rtc); ++i) raw[i] = uint8_t(std::rand() & 0xFF);
  IrrProgram p = irr_rom_default();
  members(p, kL04, true, true);
  add_slot(p, 1, kL04, 420, 30, 2, 30);  // 07:00 window
  s.epoch = at(419);
  s.boot(true, &p);
  bool zero = s.rtc.water_centigal == 0;
  for (uint32_t v : s.rtc.line_on_s) zero = zero && v == 0;
  CHECK(zero, "power-on over random RTC bytes: zero counters");
  irr_count_water(s.eng, 35999);
  s.tick();
  CHECK(!s.out.saf4_tripped, "35999 centi-gallons: under SAF-4");
  IrrVpdResult r = irr_vpd_request(s.eng, kL04, 45, s.ms, s.out);
  CHECK(r.answer == IrrVpdAnswer::kGrant && (s.out.grant_mask & irr_bit(kL04)), "mist granted at 35999");
  irr_vpd_release(s.eng, kL04, s.ms, s.out);
  irr_count_water(s.eng, 1);
  s.tick();
  CHECK(s.out.saf4_tripped && s.rtc.water_centigal == 36000, "36000 centi-gallons: SAF-4 tripped");
  r = irr_vpd_request(s.eng, kL04, 45, s.ms, s.out);
  CHECK(r.answer == IrrVpdAnswer::kRefuse && r.reason == IrrReason::kSaf, "mist refused at 36000");
  s.run_until(421);
  CHECK(event_is(s.find_event(1, kL04), IrrOutcome::kCap, IrrReason::kSaf) && s.open_s[kL04] == 0,
        "a scheduled window under SAF-4 ends cap (saf) and opens nothing");
  CHECK(s.rtc.water_centigal == 36000, "counters never reset by the tick");
  // Under the ceiling the same window runs.
  Sim t;
  t.epoch = at(419);
  t.boot(true, &p);
  irr_count_water(t.eng, 35999);
  t.run_until(425);
  CHECK(event_is(t.find_event(1, kL04), IrrOutcome::kOk, IrrReason::kNone) && t.open_s[kL04] >= 60,
        "at 35999 the window runs");
}

// Acceptance: a reboot 5 s after a claim does not repeat the occurrence. After a soft
// reset the run resumes its remainder; after a power-on that slot never starts again
// that local day.
static void reboot_after_a_claim() {
  IrrProgram p = irr_rom_default();
  add_slot(p, 10, kL01, 420, 600);  // L01 07:00, 10 min
  // Soft reset.
  {
    Sim s;
    s.epoch = at(419, 55);
    s.boot(true, &p);
    s.tick();  // 07:00:00: claimed, L01 opens
    CHECK(s.relays & irr_bit(kL01), "L01 opens at 07:00");
    CHECK(s.nvs.flash_has && irr_claimed(s.nvs.flash, 10, kDayNum), "the claim is in flash before the relay opens");
    CHECK(s.out.state.drip.valid && s.out.state.drip.line == kL01 && s.out.state.drip.cause == IrrCause::kSchedule &&
              s.out.state.drip.slot_id == 10 && s.out.state.drip.end_epoch == s.out.state.drip.start_epoch + 600,
          "irr_state d=0,schedule,10,start,start+600");
    s.tick();  // 07:00:05
    s.boot(false, &p);
    CHECK(s.rtc.run.kind == 1 && s.rtc.run.remaining_s == 595, "the RTC block holds the remainder across the reset");
    s.run_until(440);
    CHECK(s.open_s[kL01] >= 595 && s.open_s[kL01] <= 610, "the run resumed its remainder: 600 s in all");
    CHECK(s.count_events(10, kL01) == 1 && event_is(s.find_event(10, kL01), IrrOutcome::kOk, IrrReason::kNone),
          "one outcome, ok");
  }
  // Power-on.
  {
    Sim s;
    s.epoch = at(419, 55);
    s.boot(true, &p);
    s.tick();
    s.tick();
    const uint32_t before = s.open_s[kL01];
    s.boot(true, &p);  // the power cut zeroes the RTC block; the synced claim survives
    CHECK(s.rtc.run.kind == 0, "power-on: no remainder");
    s.run_until(1439);
    CHECK(s.open_s[kL01] == before, "power-on: the slot never starts again that day");
    CHECK(s.count_events(10, kL01) == 0, "and publishes no second outcome");
  }
  // A claim that saved but did not sync never opens the relay.
  {
    Sim s;
    s.epoch = at(419, 55);
    s.boot(true, &p);
    s.nvs.fail_sync = true;
    s.run_for(60);
    CHECK(s.open_s[kL01] == 0 && !(s.relays & irr_bit(kL01)), "no sync, no relay");
    CHECK(s.out.state.wait_count == 0 || !s.out.state.drip.valid, "the occurrence waits");
    s.nvs.fail_sync = false;
    s.tick();
    CHECK(s.relays & irr_bit(kL01), "once save and sync succeed, the relay opens");
  }
  {
    Sim s;
    s.epoch = at(419, 55);
    s.boot(true, &p);
    s.nvs.fail_save = true;
    s.run_for(30);
    CHECK(s.open_s[kL01] == 0 && s.nvs.syncs == 0, "a failed save never syncs and never opens");
  }
}

// Acceptance: in SENSOR_FAULT no mister relay opens (L08 and L10 included); run-now and
// degraded_only mist are refused the same way. Drips still run (D-IRP-02).
static void sensor_fault_refuses_all_engine_mist() {
  IrrProgram p = open_program();
  add_slot(p, 1, kL01, 420, 300);
  add_slot(p, 2, kL04, 420, 30, 4, 60);
  add_slot(p, 3, kL07, 421, 30, 2, 60);
  add_slot(p, 4, kL08, 422, 30, 2, 60, 0, 60);
  add_slot(p, 5, kL10, 423, 30, 2, 60, 30, 60);
  Sim s;
  s.epoch = at(419);
  s.boot(true, &p);
  s.in.sensor_fault = true;
  s.in.sensor_degraded = true;
  s.vpd_want = kL09;
  s.run_until(600);
  CHECK(!s.mist_in_sf, "no engine mister relay in SENSOR_FAULT");
  CHECK(s.open_s[kL04] == 0 && s.open_s[kL07] == 0 && s.open_s[kL08] == 0 && s.open_s[kL09] == 0 &&
            s.open_s[kL10] == 0 && s.open_s[kL03] == 0,
        "L04, L07, L08, L09, L10 and L03 never open");
  CHECK(s.vpd_grants == 0, "VPD pulses are refused too");
  CHECK(s.open_s[kL01] >= 295 && s.open_s[kL01] <= 305, "the L01 drip still runs");
  CHECK(event_is(s.find_event(2, kL04), IrrOutcome::kMissed, IrrReason::kSf) &&
            event_is(s.find_event(4, kL08), IrrOutcome::kMissed, IrrReason::kSf) &&
            event_is(s.find_event(5, kL10), IrrOutcome::kMissed, IrrReason::kSf),
        "the mist slots end missed (sf)");
  for (uint8_t line : {kL04, kL07, kL08, kL09, kL10}) {
    CHECK(s.run_now(line, 30, 2, 60) == IrrRunResult::kSensorFault, "run-now mist refused in SENSOR_FAULT");
    CHECK(event_is(s.last_event(), IrrOutcome::kSkipped, IrrReason::kSf) && s.last_event()->line == line &&
              s.last_event()->slot_id == 0,
          "the refused run-now ends skipped (sf), slot 0");
  }
  CHECK(!(s.out.relay_mask & kMistManifoldMask), "nothing opened");
  // degraded_only mist: the seed program in SENSOR_FAULT with sensing degraded, in daylight.
  IrrProgram seed{};
  decode_vector(1, seed);
  const SolarTimes st = day0_solar();
  Sim d;
  d.epoch = at(st.solar_noon_min - 121);
  d.boot(true, &seed);
  d.in.sensor_fault = true;
  d.in.sensor_degraded = true;
  d.run_until(st.solar_noon_min + 59);
  CHECK(!d.mist_in_sf && d.open_s[kL04] == 0, "degraded_only mist refused in SENSOR_FAULT");
  CHECK(event_is(d.find_event(1, kL04), IrrOutcome::kSkipped, IrrReason::kSf), "it ends skipped (sf)");
  // A running window stops at once when SENSOR_FAULT starts.
  IrrProgram w = open_program();
  add_slot(w, 2, kL04, 420, 60, 3, 60);
  Sim r;
  r.epoch = at(419, 55);
  r.boot(true, &w);
  r.run_for(10);
  CHECK(r.relays & irr_bit(kL04), "the window pulses");
  r.in.sensor_fault = true;
  r.tick();
  CHECK(!(r.relays & irr_bit(kL04)) && has_wait(r.out, kL04, IrrReason::kSf), "SENSOR_FAULT cuts the pulse: w=3,sf");
}

// Acceptance: on the seed program, with L04 schedule_member off and sensor_degraded in
// daylight, a due degraded_only slot runs with cause fallback; with L04 vpd_member off
// the same slot ends skipped (nvm).
static void degraded_only_fallback() {
  IrrProgram seed{};
  CHECK(decode_vector(1, seed), "seed decodes");
  CHECK(!(seed.schedule_member_mask & 1) && (seed.vpd_member_mask & 1), "seed: L04 VPD member, not schedule member");
  const SolarTimes st = day0_solar();
  {
    Sim s;
    s.epoch = at(st.solar_noon_min - 121);
    s.boot(true, &seed);
    s.in.sensor_degraded = true;
    s.tick();
    s.run_until(st.solar_noon_min - 120);
    CHECK(s.out.state.mist.valid && s.out.state.mist.line == kL04 && s.out.state.mist.cause == IrrCause::kFallback &&
              s.out.state.mist.slot_id == 1,
          "m=3,fallback,1: the degraded_only slot runs with cause fallback");
    CHECK(s.out.state.mist.end_epoch == s.out.state.mist.start_epoch + 360, "4 x 45 s with 60 s gaps: 360 s");
    s.run_until(st.solar_noon_min - 110);
    CHECK(event_is(s.find_event(1, kL04), IrrOutcome::kOk, IrrReason::kNone), "all four pulses: ok");
    CHECK(s.open_s[kL04] >= 175 && s.open_s[kL04] <= 185, "4 x 45 s of mist");
    CHECK(s.state_changes <= 3, "irr_state changes at the window's start and end, never per pulse");
  }
  {
    IrrProgram p = seed;
    members(p, kL04, false, false);
    Sim s;
    s.epoch = at(st.solar_noon_min - 121);
    s.boot(true, &p);
    s.in.sensor_degraded = true;
    s.run_until(st.solar_noon_min - 119);
    CHECK(event_is(s.find_event(1, kL04), IrrOutcome::kSkipped, IrrReason::kNvm) && s.open_s[kL04] == 0,
          "vpd_member off: skipped (nvm)");
  }
  {
    // VPD sensing healthy through the window: skipped (vok).
    Sim s;
    s.epoch = at(st.solar_noon_min - 121);
    s.boot(true, &seed);
    s.run_until(st.solar_noon_min - 100);
    CHECK(has_wait(s.out, kL04, IrrReason::kVok), "waiting: w=3,vok");
    s.run_until(st.solar_noon_min - 59);
    CHECK(event_is(s.find_event(1, kL04), IrrOutcome::kSkipped, IrrReason::kVok) && s.open_s[kL04] == 0,
          "healthy VPD sensing through the window: skipped (vok)");
  }
  {
    // schedule_member does not apply to degraded_only, and a clean L04 window needs it.
    IrrProgram p = seed;
    add_slot(p, 4, kL04, 600, 30, 2, 60);
    Sim s;
    s.epoch = at(599);
    s.boot(true, &p);
    s.run_until(601);
    CHECK(event_is(s.find_event(4, kL04), IrrOutcome::kSkipped, IrrReason::kNsm), "a plain window: skipped (nsm)");
    // Membership is settled before a pause, as the platform's agenda predicts (dis, nvm/nsm, pau).
    IrrProgram q = p;
    q.lines[kL04].paused_until = at(1200);
    add_slot(q, 5, kL04, 660, 30, 2, 60);
    Sim t;
    t.epoch = at(659);
    t.boot(true, &q);
    t.run_until(661);
    CHECK(event_is(t.find_event(5, kL04), IrrOutcome::kSkipped, IrrReason::kNsm), "paused and not a member: nsm");
  }
}

// #1271: the engine counts degraded sensing only after 300 s of continuous outage, so a reboot
// inside a due window opens no fallback mist, and a real outage still gets its window late.
static void degraded_sensing_grace() {
  {
    IrrDegradedGrace g{};
    const uint32_t t = 2000u;
    CHECK(!irr_degraded_after_grace(g, true, t - 1000u), "averages up: not degraded");
    CHECK(!irr_degraded_after_grace(g, false, t), "averages out: the count starts");
    CHECK(!irr_degraded_after_grace(g, false, t + kDegradedGraceMs - 1u), "not at 299.999 s");
    CHECK(irr_degraded_after_grace(g, false, t + kDegradedGraceMs), "counted at 300 s");
    CHECK(irr_degraded_after_grace(g, false, t + kDegradedGraceMs + 3600000u), "counted while they stay out");
    CHECK(!irr_degraded_after_grace(g, true, t + kDegradedGraceMs + 3601000u), "averages back: cleared at once");
    const uint32_t u = t + 5000000u;
    CHECK(!irr_degraded_after_grace(g, false, u), "a new outage counts from zero");
    CHECK(!irr_degraded_after_grace(g, false, u + 299000u), "299 s in");
    CHECK(!irr_degraded_after_grace(g, true, u + 300000u), "one good tick breaks the outage");
    CHECK(!irr_degraded_after_grace(g, false, u + 301000u), "the count starts again");
    CHECK(!irr_degraded_after_grace(g, false, u + 600999u), "299.999 s into the new outage");
    CHECK(irr_degraded_after_grace(g, false, u + 601000u), "300 s of the new outage");
  }
  {
    IrrDegradedGrace g{};
    const uint32_t t = 0xFFFFFFFFu - 100000u;  // 100 s before millis() wraps
    CHECK(!irr_degraded_after_grace(g, false, t), "the count starts before the wrap");
    CHECK(!irr_degraded_after_grace(g, false, t + 299999u), "it runs across the wrap");
    CHECK(irr_degraded_after_grace(g, false, t + 300000u), "and counts at 300 s");
    CHECK(irr_degraded_after_grace(g, false, t + 1000u), "49.7 days on the difference comes round: still counted");
  }
  IrrProgram seed{};
  CHECK(decode_vector(1, seed), "seed decodes");
  const SolarTimes st = day0_solar();
  // Healthy up to a minute into the slot's due window, then a soft reset.
  auto reboot_in_window = [&](Sim& s) {
    s.epoch = at(st.solar_noon_min - 121);
    s.boot(true, &seed);
    s.run_until(st.solar_noon_min - 119);
    CHECK(has_wait(s.out, kL04, IrrReason::kVok) && s.open_s[kL04] == 0, "before the reset: w=3,vok");
    s.boot(false, &seed);
  };
  // The climate loop's call, once per tick.
  auto step = [](Sim& s, IrrDegradedGrace& g, bool averages_ok) {
    s.in.sensor_degraded = irr_degraded_after_grace(g, averages_ok, s.ms);
    s.tick();
  };
  {
    // What the grace stops: the raw !avg_ok opens the center mister by time on the first tick.
    Sim s;
    reboot_in_window(s);
    s.in.sensor_degraded = true;
    s.tick();
    CHECK((s.relays & irr_bit(kL04)) && s.out.state.mist.cause == IrrCause::kFallback, "no grace: a fallback pulse");
  }
  {
    // The averages read NaN for the first 30 s after the reset, then answer.
    Sim s;
    reboot_in_window(s);
    IrrDegradedGrace g{};
    for (int i = 0; i < 6; ++i) step(s, g, false);
    CHECK(!(s.relays & irr_bit(kL04)) && s.open_s[kL04] == 0 && has_wait(s.out, kL04, IrrReason::kVok),
          "30 s out after the reset: L04 stays shut, w=3,vok");
    while (s.epoch < at(st.solar_noon_min - 59)) step(s, g, true);
    CHECK(event_is(s.find_event(1, kL04), IrrOutcome::kSkipped, IrrReason::kVok) && s.open_s[kL04] == 0,
          "the window passes: skipped (vok), no water");
  }
  {
    // A real outage from the reset on: L04 waits out the 300 s, then the window runs whole.
    Sim s;
    reboot_in_window(s);
    IrrDegradedGrace g{};
    bool opened_early = false;
    for (int i = 0; i < 60; ++i) {
      step(s, g, false);
      if (s.relays & irr_bit(kL04)) opened_early = true;
    }
    CHECK(!opened_early && has_wait(s.out, kL04, IrrReason::kVok), "300 s out: still waiting, L04 shut");
    step(s, g, false);
    CHECK((s.relays & irr_bit(kL04)) && s.out.state.mist.valid && s.out.state.mist.line == kL04 &&
              s.out.state.mist.cause == IrrCause::kFallback && s.out.state.mist.slot_id == 1,
          "past 300 s: m=3,fallback,1");
    while (s.epoch < at(st.solar_noon_min - 100)) step(s, g, false);
    CHECK(event_is(s.find_event(1, kL04), IrrOutcome::kOk, IrrReason::kNone), "all four pulses: ok");
    CHECK(s.open_s[kL04] >= 175 && s.open_s[kL04] <= 185, "4 x 45 s of mist");
  }
}

// Acceptance: two outcomes in one tick both get sequence numbers, consecutive, and
// 65535 wraps to 0, each with its line, slot and reason in #1151's grammar.
static void two_outcomes_in_one_tick() {
  IrrProgram p = irr_rom_default();
  p.lines[kL01].paused_until = at(600);  // L01 paused until 10:00
  add_slot(p, 7, kL05, 420, 300);        // L05 disabled (reserved)
  add_slot(p, 8, kL01, 420, 300);        // L01 paused
  Sim s;
  s.epoch = at(419, 55);
  s.boot(true, &p);
  s.rtc.next_seq = 65535;
  irr_rtc_seal(s.rtc);
  s.tick();
  CHECK(s.rtc.ring_count == 2 && s.out.events_changed, "two outcomes in one tick");
  char buf[256];
  irr_format_events(s.rtc, buf, sizeof(buf));
  CHECK(std::strcmp(buf, "65535,4,7,20759,sk,dis 0,0,8,20759,sk,pau") == 0, "consecutive, 65535 then 0, in the grammar");
  s.tick();
  CHECK(!s.out.events_changed && s.rtc.ring_count == 2, "settled once: no repeat");
}

// Acceptance: a fertilizer trace runs prewet on L01, L03 with L02, then the flush, in
// order; a stop during the feed ends it, closes L03 and still runs the flush; disabling
// L02 mid-feed runs no flush.
static void fertilizer_drip_sequence() {
  IrrProgram p = irr_rom_default();
  enable(p, kL02);
  p.lines[kL01].daily_cap_min = 0;
  add_slot(p, 5, kL02, 420, 300, 1, 0, 120, 240);  // prewet 120, feed 300, flush 240
  {
    Sim s;
    s.epoch = at(419, 55);
    s.boot(true, &p);
    s.tick();
    CHECK(s.out.state.drip.valid && s.out.state.drip.line == kL01 && s.out.state.drip.cause == IrrCause::kPrewet &&
              s.out.state.drip.slot_id == 5,
          "d=0,prewet,5");
    CHECK(IrrStage(s.eng.claims.fert_stage) == IrrStage::kPrewet && s.nvs.flash.fert_stage == 1,
          "the prewet stage is journaled before L01 opens");
    s.run_for(125);
    CHECK((s.relays & (irr_bit(kL02) | irr_bit(kL03))) == (irr_bit(kL02) | irr_bit(kL03)) &&
              s.out.state.drip.line == kL02 && s.out.state.drip.cause == IrrCause::kFeed,
          "d=1,feed: L03 with L02");
    CHECK(s.nvs.flash.fert_stage == 2, "the feed stage is journaled");
    s.run_for(600);
    uint16_t seq[16];
    const int n = s.nonzero_seq(seq, 16);
    CHECK(n == 3 && seq[0] == irr_bit(kL01) && seq[1] == (irr_bit(kL02) | irr_bit(kL03)) && seq[2] == irr_bit(kL01),
          "prewet L01 -> L03 + L02 -> flush L01, in order");
    CHECK(s.open_s[kL01] >= 355 && s.open_s[kL01] <= 370 && s.open_s[kL02] >= 295 && s.open_s[kL02] <= 305,
          "120 s prewet + 240 s flush on L01, 300 s feed on L02");
    CHECK(s.open_s[kL03] == s.open_s[kL02], "L03 opens exactly with the feed");
    CHECK(event_is(s.find_event(5, kL02), IrrOutcome::kOk, IrrReason::kNone), "the feed ends ok");
    CHECK(s.nvs.flash.fert_stage == 0, "the journal is cleared after the flush");
    CHECK(!s.drip_with_mist && !s.l03_alone && !s.ungranted, "no overlap, L03 never alone, every relay granted");
  }
  {
    // Stop during the feed: L03 and L02 close inside the call, the flush still runs.
    Sim s;
    s.epoch = at(419, 55);
    s.boot(true, &p);
    s.run_for(185);  // prewet done, 60 s into the feed
    CHECK(s.relays & irr_bit(kL03), "feeding");
    s.stop(kAllLines);
    CHECK(!(s.out.relay_mask & (irr_bit(kL03) | irr_bit(kL02))) && !s.out.l03_on, "the stop closes L03 at once");
    s.run_for(300);
    uint16_t seq[16];
    const int n = s.nonzero_seq(seq, 16);
    CHECK(n == 3 && seq[2] == irr_bit(kL01), "the flush still runs on L01 after the stop");
    CHECK(s.open_s[kL01] >= 355 && s.open_s[kL01] <= 370, "a full 240 s flush");
    CHECK(event_is(s.find_event(5, kL02), IrrOutcome::kStopped, IrrReason::kCmd), "stopped (cmd)");
    CHECK(s.nvs.flash.fert_stage == 0, "journal cleared");
  }
  {
    // Disabling L02 mid-feed: the run ends with no flush.
    Sim s;
    s.epoch = at(419, 55);
    s.boot(true, &p);
    s.run_for(185);
    IrrProgram off = p;
    enable(off, kL02, false);
    s.push(off);
    s.run_for(400);
    uint16_t seq[16];
    const int n = s.nonzero_seq(seq, 16);
    CHECK(n == 2 && seq[1] == (irr_bit(kL02) | irr_bit(kL03)), "no flush after a disable");
    CHECK(s.open_s[kL01] >= 115 && s.open_s[kL01] <= 130, "L01 ran the prewet only");
    CHECK(event_is(s.find_event(5, kL02), IrrOutcome::kStopped, IrrReason::kDis), "stopped (dis)");
    CHECK(s.nvs.flash.fert_stage == 0, "journal cleared");
  }
  {
    // A reboot during the feed: short (rst), and the flush runs; nothing resumes.
    Sim s;
    s.epoch = at(419, 55);
    s.boot(true, &p);
    s.run_for(185);
    s.boot(true, &p);
    s.run_for(400);
    CHECK(event_is(s.find_event(5, kL02), IrrOutcome::kShort, IrrReason::kRst), "a reset ends the feed short (rst)");
    CHECK(s.open_s[kL02] >= 55 && s.open_s[kL02] <= 70, "the feed never resumed");
    uint16_t seq[16];
    const int n = s.nonzero_seq(seq, 16);
    CHECK(n == 3 && seq[2] == irr_bit(kL01), "its flush runs after the reset");
    CHECK(s.count_events(5, kL02) == 1, "one outcome");
    // A reset during that flush repeats the flush, never the outcome.
    Sim t;
    t.epoch = at(419, 55);
    t.boot(true, &p);
    t.run_for(600);  // into the flush
    CHECK(t.relays & irr_bit(kL01), "flushing");
    CHECK(t.nvs.flash.fert_stage == uint8_t(IrrStage::kFlush), "the flush tail is journaled");
    t.boot(false, &p);  // soft reset: the ring survives
    t.run_for(400);
    CHECK(t.count_events(5, kL02) == 1 && event_is(t.find_event(5, kL02), IrrOutcome::kOk, IrrReason::kNone),
          "the outcome stays ok after a reset in the flush");
    CHECK(t.open_s[kL01] >= 120 + 240 + 55, "the flush ran again");
    CHECK(t.nvs.flash.fert_stage == 0, "the repeated flush clears the journal");
    Sim u;
    u.epoch = at(419, 55);
    u.boot(true, &p);
    u.run_for(600);
    u.boot(true, &p);  // power-on: the ring is gone, and no second outcome is made up
    u.run_for(400);
    CHECK(u.count_events(5, kL02) == 0 && u.nvs.flash.fert_stage == 0 && (u.open_s[kL01] >= 120 + 240 + 55),
          "power-on in the flush: the flush repeats, no outcome is repeated");
  }
  {
    // SAFETY_COOL ends the feed (preempted); the flush waits for it to clear.
    Sim s;
    s.epoch = at(419, 55);
    s.boot(true, &p);
    s.run_for(185);
    s.in.safety_cool = true;
    s.run_for(120);
    CHECK(!(s.relays & (irr_bit(kL02) | irr_bit(kL03) | irr_bit(kL01))), "SAFETY_COOL: nothing on the drips");
    CHECK(event_is(s.find_event(5, kL02), IrrOutcome::kPreempted, IrrReason::kNone), "preempted");
    s.in.safety_cool = false;
    s.run_for(300);
    CHECK(s.open_s[kL01] >= 355 && s.open_s[kL01] <= 370, "the flush runs after SAFETY_COOL");
  }
}

// Acceptance: an L08 trace with confirmed occupancy through its window skips every feed
// pulse, ends short and still runs the L07 flush. The prewet and the flush on L07 are mist
// too (D-IRP-10, F7): the flush waits for the occupancy to clear, so no mister opens on
// anyone.
static void l08_occupancy_skips_feed_pulses() {
  IrrProgram p = irr_rom_default();
  enable(p, kL07);
  enable(p, kL08);
  add_slot(p, 9, kL08, 420, 30, 3, 60, 60, 120);  // prewet 60 on L07, 3 x 30 s feed, flush 120 on L07
  {
    Sim s;
    s.epoch = at(419, 55);
    s.boot(true, &p);
    s.tick();  // 07:00: the prewet opens L07
    CHECK(s.relays & irr_bit(kL07), "the prewet runs on L07");
    s.run_for(55);
    s.in.occupied = true;  // confirmed from the feed's start through its window
    s.tick();               // 07:01: the prewet is done, the feed's first pulse is due
    CHECK(has_wait(s.out, kL08, IrrReason::kOcc), "w=7,occ while someone is inside");
    s.run_for(3600 + 180 + 10);
    CHECK(s.open_s[kL08] == 0 && s.open_s[kL03] == 0, "every feed pulse skipped: L08 and L03 never open");
    CHECK(event_is(s.find_event(9, kL08), IrrOutcome::kShort, IrrReason::kOcc), "the feed ends short (occ)");
    CHECK(s.open_s[kL07] >= 55 && s.open_s[kL07] <= 65 && has_wait(s.out, kL07, IrrReason::kOcc),
          "the L07 flush waits while someone is inside: w=6,occ");
    s.in.occupied = false;
    s.run_for(200);
    CHECK(s.open_s[kL07] >= 175 && s.open_s[kL07] <= 190, "the L07 flush still runs once the occupancy clears");
    uint16_t seq[16];
    const int n = s.nonzero_seq(seq, 16);
    CHECK(n == 1 && seq[0] == irr_bit(kL07), "only L07 ever opened");
    CHECK(!s.mist_occupied, "no mister open while occupancy is confirmed");
    CHECK(s.nvs.flash.fert_stage == 0 && s.count_events(9, kL08) == 1, "journal cleared, one outcome");
  }
  {
    // Occupancy arriving mid-prewet ends the run (it never pauses); the flush waits for it to clear.
    Sim s;
    s.epoch = at(419, 55);
    s.boot(true, &p);
    s.run_for(30);
    s.in.occupied = true;
    s.tick();
    CHECK(!(s.relays & irr_bit(kL07)) && event_is(s.find_event(9, kL08), IrrOutcome::kShort, IrrReason::kOcc),
          "occupancy mid-prewet: L07 closes, short (occ)");
    s.run_for(600);
    CHECK(!s.mist_occupied && s.open_s[kL08] == 0, "nothing mists while occupied, no feed");
    s.in.occupied = false;
    s.run_for(200);
    CHECK(s.open_s[kL07] >= 145 && s.open_s[kL07] <= 160, "the flush runs once it clears");
    CHECK(s.nvs.flash.fert_stage == 0, "journal cleared");
  }
  {
    // Occupancy at the start time: the occurrence waits (w=7,occ) and starts once it clears.
    Sim s;
    s.epoch = at(419, 55);
    s.boot(true, &p);
    s.in.occupied = true;
    s.run_for(300);
    CHECK(s.open_s[kL07] == 0 && has_wait(s.out, kL08, IrrReason::kOcc) && s.nvs.flash.fert_stage == 0,
          "occupied at 07:00: nothing opens or is journaled, w=7,occ");
    s.in.occupied = false;
    s.run_for(600);
    CHECK(event_is(s.find_event(9, kL08), IrrOutcome::kOk, IrrReason::kNone) && s.open_s[kL08] >= 85,
          "it runs once the occupancy clears, inside its 60 min");
  }
  // Without occupancy the feed pulses run with L03.
  Sim t;
  t.epoch = at(419, 55);
  t.boot(true, &p);
  t.run_for(600);
  CHECK(t.open_s[kL08] >= 85 && t.open_s[kL08] <= 95 && t.open_s[kL03] == t.open_s[kL08], "3 x 30 s with L03");
  CHECK(event_is(t.find_event(9, kL08), IrrOutcome::kOk, IrrReason::kNone) && !t.l03_alone, "ok");
}

// A prewet or flush on a mister is mist: SENSOR_FAULT, a latch or back-off, confirmed
// occupancy and SAF-4 hold it, and in a VPD emergency VPD goes first. The engine never
// opens a disabled line, so a fertilizer run needs its clean partner enabled.
static void fertilizer_partner_and_mist_rails() {
  IrrProgram p = irr_rom_default();
  enable(p, kL07);
  enable(p, kL08);
  add_slot(p, 9, kL08, 420, 30, 1, 60, 0, 600);  // one 30 s feed pulse, a 600 s flush on L07
  {
    // SAF-4 reached before the flush: it never opens, and is dropped 60 min later.
    Sim s;
    s.epoch = at(419, 55);
    s.boot(true, &p);
    s.run_for(40);
    CHECK(s.open_s[kL08] >= 25 && event_is(s.find_event(9, kL08), IrrOutcome::kOk, IrrReason::kNone),
          "the feed pulse ran: ok");
    irr_count_water(s.eng, 36000);
    s.run_for(600);
    CHECK(s.open_s[kL07] == 0 && has_wait(s.out, kL07, IrrReason::kSaf), "SAF-4 holds the mist flush: w=6,saf");
    s.run_for(3600);
    CHECK(s.open_s[kL07] == 0 && s.nvs.flash.fert_stage == 0 && s.eng.run.kind == IrrRunKind::kNone,
          "a flush that cannot run for 60 min is dropped");
  }
  {
    // A latch on the zone holds it; it runs once the latch clears.
    Sim s;
    s.epoch = at(419, 55);
    s.boot(true, &p);
    s.run_for(40);
    s.in.latch_mask = irr_bit(kL07);
    s.run_for(300);
    CHECK(s.open_s[kL07] == 0 && has_wait(s.out, kL07, IrrReason::kFlt), "a latched zone holds the flush: w=6,flt");
    s.in.latch_mask = 0;
    s.run_for(700);
    CHECK(s.open_s[kL07] >= 595 && s.open_s[kL07] <= 610 && s.nvs.flash.fert_stage == 0, "then the full flush runs");
    // An open mist flush closes for SENSOR_FAULT and finishes its remainder afterwards.
    Sim t;
    t.epoch = at(419, 55);
    t.boot(true, &p);
    t.run_for(200);
    CHECK(t.relays & irr_bit(kL07), "flushing on L07");
    t.in.sensor_fault = true;
    t.run_for(120);
    CHECK(!t.mist_in_sf && has_wait(t.out, kL07, IrrReason::kSf), "SENSOR_FAULT closes it: w=6,sf");
    t.in.sensor_fault = false;
    t.run_for(700);
    CHECK(t.open_s[kL07] >= 595 && t.open_s[kL07] <= 610, "the flush finishes its remainder");
  }
  {
    // VPD emergency: a mister fertilizer run waits to start, and an open mist flush yields.
    IrrProgram q = p;
    q.slots[0].prewet_s = 120;
    Sim s;
    s.epoch = at(419, 55);
    s.boot(true, &q);
    s.vpd_want = kL04;
    s.vpd_on_s = 30;
    s.vpd_gap_s = 30;
    s.in.vpd_emergency = true;
    s.run_for(300);
    CHECK(s.open_s[kL07] == 0 && s.open_s[kL08] == 0 && has_wait(s.out, kL08, IrrReason::kMan) && s.vpd_grants >= 3,
          "in a VPD emergency VPD goes first: the run waits (w=7,man)");
    s.in.vpd_emergency = false;
    s.vpd_want = kNoLine;
    s.run_for(300);
    CHECK(s.relays & irr_bit(kL07), "after it, the flush runs on L07");
    s.in.vpd_emergency = true;
    s.vpd_want = kL04;
    s.run_for(60);
    CHECK(!(s.relays & irr_bit(kL07)) && s.vpd_grants >= 4, "a new emergency: the mist flush yields to VPD pulses");
    s.in.vpd_emergency = false;
    s.vpd_want = kNoLine;
    s.run_for(900);
    CHECK(s.open_s[kL07] >= 715 && s.open_s[kL07] <= 735 && s.nvs.flash.fert_stage == 0 && !s.two_misters,
          "prewet 120 + flush 600 in all, one mister at a time");
  }
  {
    // A disabled clean partner: the run never starts and the partner never opens.
    IrrProgram d = irr_rom_default();
    enable(d, kL02);
    enable(d, kL01, false);
    add_slot(d, 5, kL02, 420, 300, 1, 0, 120, 240);
    Sim s;
    s.epoch = at(419, 55);
    s.boot(true, &d);
    s.run_for(900);
    CHECK(s.open_s[kL01] == 0 && s.open_s[kL02] == 0 && s.open_s[kL03] == 0 &&
              event_is(s.find_event(5, kL02), IrrOutcome::kSkipped, IrrReason::kDis),
          "L01 disabled: the L02 feed with a prewet and flush ends skipped (dis)");
    CHECK(s.run_now(kL02, 120, 1, 0, 30, 60) == IrrRunResult::kLineDisabled &&
              event_is(s.last_event(), IrrOutcome::kSkipped, IrrReason::kDis),
          "run-now with a prewet: line_disabled");
    CHECK(s.run_now(kL02, 60) == IrrRunResult::kStarted, "a feed with no prewet or flush needs no partner");
    s.run_for(120);
    CHECK(s.open_s[kL02] >= 55 && s.open_s[kL01] == 0 && s.disabled_opened == 0, "and never opens L01");
  }
  {
    // Disabling the partner mid-feed ends the run with no flush; a reset with it disabled flushes nothing.
    IrrProgram d = irr_rom_default();
    enable(d, kL02);
    add_slot(d, 5, kL02, 420, 300, 1, 0, 120, 240);
    IrrProgram off = d;
    enable(off, kL01, false);
    Sim s;
    s.epoch = at(419, 55);
    s.boot(true, &d);
    s.run_for(185);
    CHECK(s.relays & irr_bit(kL03), "feeding");
    s.push(off);
    s.run_for(600);
    CHECK(event_is(s.find_event(5, kL02), IrrOutcome::kStopped, IrrReason::kDis) && s.open_s[kL01] <= 130 &&
              s.disabled_opened == 0 && s.nvs.flash.fert_stage == 0,
          "partner disabled mid-feed: stopped (dis), no flush");
    Sim t;
    t.epoch = at(419, 55);
    t.boot(true, &d);
    t.run_for(185);
    t.boot(true, &off);
    t.run_for(600);
    CHECK(event_is(t.find_event(5, kL02), IrrOutcome::kShort, IrrReason::kRst) && t.open_s[kL01] <= 130 &&
              t.disabled_opened == 0 && t.nvs.flash.fert_stage == 0,
          "a reset with the partner disabled: short (rst), no flush");
  }
  {
    // A flush journal that fails to save is retried; the flush runs once it saves.
    IrrProgram f = irr_rom_default();
    enable(f, kL02);
    add_slot(f, 5, kL02, 420, 60, 1, 0, 0, 240);
    Sim s;
    s.epoch = at(419, 55);
    s.boot(true, &f);
    s.run_for(55);
    s.nvs.fail_save = true;
    s.run_for(30);
    CHECK(s.rtc.ring_count == 0 && !s.out.drip_active && !(s.relays & irr_bit(kL01)),
          "unjournaled: no outcome, no flush, the water is free meanwhile");
    s.nvs.fail_save = false;
    s.run_for(400);
    CHECK(s.open_s[kL01] >= 235 && s.open_s[kL01] <= 250 && s.count_events(5, kL02) == 1 &&
              event_is(s.find_event(5, kL02), IrrOutcome::kOk, IrrReason::kNone) && s.nvs.flash.fert_stage == 0,
          "then the flush runs, with one ok outcome");
    // NVS never saves: no flush, and no outcome while the journal still says feeding; the
    // engine frees the water after 60 + 10 min, and a later reset ends the run once.
    Sim t;
    t.epoch = at(419, 55);
    t.boot(true, &f);
    t.run_for(55);
    t.nvs.fail_save = true;
    t.run_for(3600 + 600 + 60);
    CHECK(t.open_s[kL01] == 0 && t.rtc.ring_count == 0 && t.eng.run.kind == IrrRunKind::kNone,
          "never saved: no flush, no outcome, and the engine is free again");
    t.nvs.fail_save = false;
    t.boot(false, &f);
    t.run_for(400);
    CHECK(t.count_events(5, kL02) == 1 && event_is(t.find_event(5, kL02), IrrOutcome::kShort, IrrReason::kRst) &&
              t.open_s[kL01] >= 235 && t.nvs.flash.fert_stage == 0,
          "a later reset ends it once, from its journal: short (rst), then the flush");
  }
  {
    // An outcome waits for its cleared journal to save, so a reset meanwhile never ends the run twice.
    IrrProgram g = irr_rom_default();
    enable(g, kL02);
    add_slot(g, 6, kL02, 420, 60);  // a 60 s feed with no prewet or flush
    Sim s;
    s.epoch = at(419, 55);
    s.boot(true, &g);
    s.run_for(55);
    s.nvs.fail_save = true;
    s.run_for(30);
    CHECK(s.rtc.ring_count == 0 && s.open_s[kL02] >= 55 && !(s.relays & kWetMask) && !s.out.drip_active,
          "the feed ended: everything closed, its outcome waits for the journal");
    s.nvs.fail_save = false;
    s.boot(false, &g);
    s.run_for(60);
    CHECK(s.count_events(6, kL02) == 1 && event_is(s.find_event(6, kL02), IrrOutcome::kShort, IrrReason::kRst) &&
              s.nvs.flash.fert_stage == 0,
          "a reset meanwhile: one outcome, short (rst)");
    Sim t;
    t.epoch = at(419, 55);
    t.boot(true, &g);
    t.run_for(55);
    t.nvs.fail_save = true;
    t.run_for(30);
    t.nvs.fail_save = false;
    t.run_for(10);
    CHECK(t.count_events(6, kL02) == 1 && event_is(t.find_event(6, kL02), IrrOutcome::kOk, IrrReason::kNone) &&
              t.nvs.flash.fert_stage == 0,
          "with no reset, ok goes out once the journal clears");
  }
}

// D-IRP-05: VPD and scheduled pulses alternate at pulse boundaries; a VPD emergency
// goes first; one pulse serves both on the same zone. One mister at a time.
static void mist_manifold_alternation() {
  IrrProgram p = open_program();
  add_slot(p, 2, kL04, 420, 30, 4, 30);
  {
    Sim s;
    s.in.mister_min_off_s = 30;
    s.epoch = at(419, 55);
    s.boot(true, &p);
    s.vpd_want = kL07;
    s.vpd_on_s = 30;
    s.run_for(600);
    CHECK(event_is(s.find_event(2, kL04), IrrOutcome::kOk, IrrReason::kNone), "the window delivers its 4 pulses");
    CHECK(!s.two_misters, "one mister relay at a time");
    CHECK(s.vpd_grants >= 3, "VPD pulses run between them");
    // Holders alternate W V W V ... while both want the manifold.
    bool alternates = true;
    for (int i = 0; i + 1 < s.holders_n; ++i) {
      if (s.holders[i] == 'W' && s.holders[i + 1] == 'W') alternates = false;
    }
    CHECK(alternates && s.holders[0] == 'W', "no two scheduled pulses without a VPD pulse between them");
    CHECK(s.state_changes >= 2, "irr_state names each holder before its relay opens");
  }
  {
    // VPD emergency: VPD goes first, windows on other zones wait.
    Sim s;
    s.in.mister_min_off_s = 30;
    s.epoch = at(419, 55);
    s.boot(true, &p);
    s.vpd_want = kL07;
    s.in.vpd_emergency = true;
    s.run_for(5);  // the window's first pulse took the free manifold at 07:00
    s.run_for(600);
    CHECK(s.open_s[kL04] <= 35, "in an emergency the window waits after its first pulse");
    CHECK(has_wait(s.out, kL04, IrrReason::kMan), "w=3,man");
    s.in.vpd_emergency = false;
    s.vpd_want = kNoLine;
    s.run_for(600);
    CHECK(event_is(s.find_event(2, kL04), IrrOutcome::kOk, IrrReason::kNone), "it finishes once the emergency ends");
  }
  {
    // Same zone: a window pulse on L04 serves the VPD request for L04.
    Sim s;
    s.in.mister_min_off_s = 30;
    s.epoch = at(419, 55);
    s.boot(true, &p);
    s.vpd_want = kL04;
    s.vpd_on_s = 30;
    s.vpd_gap_s = 30;
    s.run_for(300);
    CHECK(s.vpd_served >= 1, "one pulse at the window's on_s serves both");
    CHECK(!s.two_misters, "one mister at a time");
  }
}

// D-IRP-03: a clean drip holds climate wetting for at most climate_yield_min, then yields
// one VPD pulse and resumes its remainder.
static void drip_yields_to_climate() {
  IrrProgram p = irr_rom_default();
  p.lines[kL01].daily_cap_min = 0;
  add_slot(p, 3, kL01, 420, 1800);  // 30 min
  Sim s;
  s.epoch = at(419, 55);
  s.boot(true, &p);
  s.tick();
  CHECK(s.out.drip_active, "the drip holds the water");
  s.vpd_want = kL04;
  s.vpd_on_s = 45;
  s.vpd_gap_s = 600;
  s.in.climate_demand = true;
  s.run_until(434);
  CHECK(s.vpd_grants == 0 && (s.relays & irr_bit(kL01)), "under 15 min the drip keeps the water");
  bool both = false;
  uint32_t moved_end = 0;
  const uint32_t planned_end = s.out.state.drip.end_epoch;
  while (s.epoch < at(437)) {
    s.tick();
    if (s.out.state.drip.valid && s.out.state.mist.valid && s.out.state.mist.cause == IrrCause::kVpd) both = true;
    if (s.relays & irr_bit(kL01)) moved_end = s.out.state.drip.end_epoch;
  }
  CHECK(s.vpd_grants == 1, "after 15 continuous minutes it yields one VPD pulse");
  CHECK(both, "irr_state shows the paused drip and the VPD pulse together");
  CHECK(moved_end > planned_end, "the drip's planned end moves by the pause");
  s.in.climate_demand = false;
  s.vpd_want = kNoLine;
  s.run_until(460);
  CHECK(s.open_s[kL01] >= 1795 && s.open_s[kL01] <= 1810, "the drip delivers its full 30 min");
  CHECK(event_is(s.find_event(3, kL01), IrrOutcome::kOk, IrrReason::kNone), "ok");
  CHECK(!s.drip_with_mist, "drip and mist never overlap");
  // The clean flush tail of a fertilizer run is a drip too: it yields after 15 min, then finishes.
  IrrProgram f = irr_rom_default();
  f.lines[kL01].daily_cap_min = 0;
  enable(f, kL02);
  add_slot(f, 5, kL02, 420, 60, 1, 0, 0, 1800);  // 60 s feed, 30 min flush on L01
  Sim t;
  t.epoch = at(419, 55);
  t.boot(true, &f);
  t.run_for(70);
  CHECK((t.relays & irr_bit(kL01)) && t.out.state.drip.cause == IrrCause::kFlush, "flushing on L01");
  t.vpd_want = kL04;
  t.vpd_on_s = 45;
  t.vpd_gap_s = 600;
  t.in.climate_demand = true;
  t.run_for(14 * 60);
  CHECK(t.vpd_grants == 0, "under 15 min the flush keeps the water");
  t.run_for(3 * 60);
  CHECK(t.vpd_grants == 1, "after 15 min it yields one VPD pulse");
  t.in.climate_demand = false;
  t.vpd_want = kNoLine;
  t.run_for(20 * 60);
  CHECK(t.open_s[kL01] >= 1795 && t.open_s[kL01] <= 1810 && !t.drip_with_mist && t.nvs.flash.fert_stage == 0,
        "the flush delivers its full 30 min");
}

// D-IRP-03, -05: SAFETY_COOL and a VPD emergency pause a clean drip at once; it resumes.
static void drip_pauses_for_survival_fog_and_emergency() {
  IrrProgram p = irr_rom_default();
  add_slot(p, 3, kL01, 420, 600);
  Sim s;
  s.epoch = at(419, 55);
  s.boot(true, &p);
  s.run_for(120);
  s.in.safety_cool = true;
  s.fog_want = true;
  s.tick();
  CHECK(!(s.relays & irr_bit(kL01)) && !s.out.drip_active && (s.relays & kFogMask), "SAFETY_COOL: fog, drip paused");
  CHECK(s.out.state.drip.valid, "the paused drip keeps its d= token");
  s.run_for(180);
  s.in.safety_cool = false;
  s.fog_want = false;
  s.run_for(60);
  s.in.vpd_emergency = true;
  s.run_for(60);
  CHECK(!(s.relays & irr_bit(kL01)), "a VPD emergency pauses it too");
  s.in.vpd_emergency = false;
  s.run_for(900);
  CHECK(s.open_s[kL01] >= 595 && s.open_s[kL01] <= 610, "it resumes its remainder");
  CHECK(event_is(s.find_event(3, kL01), IrrOutcome::kOk, IrrReason::kNone) && !s.drip_with_mist, "ok, no overlap");
}

static void caps_and_reserve() {
  {
    // Drip cap: a run that hits L01's 60 min ends cap.
    IrrProgram p = irr_rom_default();
    add_slot(p, 3, kL01, 420, 600);
    Sim s;
    s.epoch = at(419, 55);
    s.boot(true, &p);
    s.rtc.line_on_s[kL01] = 3300;  // 55 of 60 min used
    irr_rtc_seal(s.rtc);
    s.run_for(900);
    CHECK(event_is(s.find_event(3, kL01), IrrOutcome::kCap, IrrReason::kCap), "the drip ends cap");
    CHECK(s.open_s[kL01] >= 295 && s.open_s[kL01] <= 310, "at the cap");
    CHECK(s.run_now(kL01, 60) == IrrRunResult::kCapReached, "run-now beyond the remaining minutes: cap_reached");
  }
  {
    // Run-now beyond the remaining minutes is refused; within them it starts.
    IrrProgram p = irr_rom_default();
    Sim s;
    s.epoch = at(600);
    s.boot(true, &p);
    s.rtc.line_on_s[kL01] = 3000;  // 10 min left
    irr_rtc_seal(s.rtc);
    s.tick();
    CHECK(s.run_now(kL01, 900) == IrrRunResult::kCapReached, "15 min with 10 left: cap_reached");
    CHECK(event_is(s.last_event(), IrrOutcome::kSkipped, IrrReason::kCap), "skipped (cap)");
    CHECK(s.run_now(kL01, 600) == IrrRunResult::kStarted, "10 min with 10 left: started");
  }
  {
    // Zone duty and the VPD reserve: schedules stop at cap - reserve, VPD at the cap.
    IrrProgram p = irr_rom_default();
    members(p, kL04, true, true);
    add_slot(p, 2, kL04, 420, 30, 2, 60);
    Sim s;
    s.epoch = at(419, 55);
    s.boot(true, &p);
    s.rtc.zone_pulse_s[0] = 3600;  // 60 of center's 120 min: the reserve is all that is left
    irr_rtc_seal(s.rtc);
    s.tick();
    CHECK(event_is(s.find_event(2, kL04), IrrOutcome::kCap, IrrReason::kRes), "a window at cap - reserve: cap (res)");
    IrrVpdResult r = irr_vpd_request(s.eng, kL04, 45, s.ms, s.out);
    CHECK(r.answer == IrrVpdAnswer::kGrant, "VPD may still use the reserve");
    irr_vpd_release(s.eng, kL04, s.ms, s.out);
    s.rtc.zone_pulse_s[0] = 7200;
    irr_rtc_seal(s.rtc);
    s.ms += 60000;
    s.tick();
    r = irr_vpd_request(s.eng, kL04, 45, s.ms, s.out);
    CHECK(r.answer == IrrVpdAnswer::kRefuse && r.reason == IrrReason::kCap, "at the zone cap VPD stops too");
    CHECK(s.run_now(kL04, 30, 1, 30) == IrrRunResult::kCapReached, "run-now mist at the cap: cap_reached");
  }
  {
    // The soft budget stops schedules always and VPD except in an emergency.
    IrrProgram p = irr_rom_default();
    members(p, kL04, true, true);
    add_slot(p, 2, kL04, 420, 30, 2, 60);
    Sim s;
    s.in.soft_budget_centigal = 30000;
    s.epoch = at(419, 55);
    s.boot(true, &p);
    irr_count_water(s.eng, 30000);
    s.tick();
    CHECK(event_is(s.find_event(2, kL04), IrrOutcome::kCap, IrrReason::kBud) && s.out.soft_budget_reached,
          "a window over the soft budget: cap (bud)");
    IrrVpdResult r = irr_vpd_request(s.eng, kL04, 45, s.ms, s.out);
    CHECK(r.answer == IrrVpdAnswer::kRefuse && r.reason == IrrReason::kBud, "VPD stops at the budget");
    s.in.vpd_emergency = true;
    s.tick();
    r = irr_vpd_request(s.eng, kL04, 45, s.ms, s.out);
    CHECK(r.answer == IrrVpdAnswer::kGrant, "except in a VPD emergency");
  }
  {
    // Every relay that reads ON counts, whatever opened it: mister seconds toward the
    // zone (L08 is south), and per line; the sub-second rest carries.
    IrrProgram p = open_program();
    IrrRtcBlock rtc{};
    IrrEngine e{};
    irr_engine_init(e, &rtc, IrrPersist{}, true, nullptr);
    irr_use_program(e, p, kGoldenVectorSha256[0]);
    IrrInputs in = irr_inputs_idle();
    IrrOutputs out{};
    in.clock = irr_clock_from_epoch(at(600), kMst);
    in.now_ms = 1000;
    irr_tick(e, in, out);
    for (int i = 0; i < 4; ++i) {
      in.now_ms += 2500;
      in.relay_on_mask = irr_bit(kL08);
      irr_tick(e, in, out);
    }
    CHECK(rtc.zone_pulse_s[1] == 10 && rtc.line_on_s[kL08] == 10 && rtc.zone_pulse_s[0] == 0,
          "L08 counts toward the south zone, 2.5 s ticks carried");
    in.now_ms += 5000;
    in.relay_on_mask = irr_bit(kL11);
    irr_tick(e, in, out);
    CHECK(rtc.line_on_s[kL11] == 5 && rtc.zone_pulse_s[1] == 10, "fog counts on its own line, not a zone");
  }
}

static void run_now_refusals() {
  IrrProgram p = irr_rom_default();
  enable(p, kL05);
  p.lines[kL05].daily_cap_min = 0;
  p.lines[kL01].daily_cap_min = 0;
  Sim s;
  s.epoch = at(600);
  s.boot(true, &p);
  s.tick();
  CHECK(s.run_now(kL01, 600) == IrrRunResult::kStarted && (s.out.relay_mask & irr_bit(kL01)),
        "run-now starts at once");
  CHECK(s.out.state.drip.valid && s.out.state.drip.cause == IrrCause::kRunNow && s.out.state.drip.slot_id == 0,
        "d=0,run_now,0");
  const uint8_t before = s.rtc.ring_count;
  CHECK(s.run_now(kL01, 600) == IrrRunResult::kLineBusy && s.rtc.ring_count == before,
        "a repeated send: line_busy, nothing new");
  CHECK(s.run_now(kL05, 600) == IrrRunResult::kManifoldBusy &&
            event_is(s.last_event(), IrrOutcome::kSkipped, IrrReason::kMan),
        "another line: manifold_busy");
  s.stop(kAllLines);
  CHECK(event_is(s.find_event(0, kL01), IrrOutcome::kStopped, IrrReason::kCmd), "stop: stopped (cmd)");
  CHECK(s.run_now(kL06, 600, 1, 0, 0, 0) == IrrRunResult::kLineDisabled &&
            event_is(s.last_event(), IrrOutcome::kSkipped, IrrReason::kDis),
        "line_disabled");
  CHECK(s.run_now(kL01, 600, 2) == IrrRunResult::kBadArgs && s.run_now(kL03, 60) == IrrRunResult::kBadArgs &&
            s.run_now(kL11, 30) == IrrRunResult::kBadArgs && s.run_now(kL04, 91, 1, 30) == IrrRunResult::kBadArgs,
        "outside the bounds or not a schedulable line: bad_args");
  IrrProgram q = p;
  q.lines[kL01].paused_until = at(1200);
  members(q, kL04, true, false);  // Schedule off: run-now does not need it (#1343)
  s.push(q);
  s.tick();
  CHECK(s.run_now(kL01, 600) == IrrRunResult::kLinePaused &&
            event_is(s.last_event(), IrrOutcome::kSkipped, IrrReason::kPau),
        "line_paused");
  s.in.latch_mask = irr_bit(kL04);
  s.tick();
  CHECK(s.run_now(kL04, 30, 2, 60) == IrrRunResult::kLineFaulted &&
            event_is(s.last_event(), IrrOutcome::kSkipped, IrrReason::kFlt),
        "line_faulted while the zone is latched");
  s.in.latch_mask = 0;
  s.tick();
  CHECK(s.run_now(kL04, 30, 2, 60) == IrrRunResult::kStarted, "a mister run-now is a one-off window, Schedule off");
  s.run_for(200);
  CHECK(event_is(s.find_event(0, kL04), IrrOutcome::kOk, IrrReason::kNone) && s.open_s[kL04] >= 55,
        "the window delivers its pulses");
  // Fertilizer run-now: prewet, feed, flush, journaled.
  IrrProgram f = p;
  enable(f, kL02);
  s.push(f);
  s.tick();
  CHECK(s.run_now(kL02, 120, 1, 0, 30, 60) == IrrRunResult::kStarted && s.nvs.flash.fert_stage == 1 &&
            s.nvs.flash.fert_slot == 0,
        "a fertilizer run-now journals its prewet");
  s.run_for(400);
  CHECK(event_is(s.find_event(0, kL02), IrrOutcome::kOk, IrrReason::kNone) && s.nvs.flash.fert_stage == 0,
        "and completes");
  s.nvs.fail_save = true;
  CHECK(s.run_now(kL02, 120, 1, 0, 30, 60) == IrrRunResult::kStorage && !(s.out.relay_mask & kWetMask),
        "no journal, no fertilizer");
}

// #1343: a one-time run on any enabled mister, Schedule on or off (supersedes D-IRP-11).
// It is a one-off window under the scheduled rails: the zone's duty cap less its VPD
// reserve, SAF-4 and sensor fault still refuse it.
static void run_now_mister_without_schedule() {
  IrrProgram p = irr_rom_default();
  enable(p, kL07);
  members(p, kL07, false, false);
  p.lines[kL07].daily_cap_min = 10;
  p.lines[kL07].vpd_reserve_min = 8;
  Sim s;
  s.epoch = at(600);
  s.boot(true, &p);
  s.tick();
  CHECK(!(s.eng.prog.schedule_member_mask & (1u << kMisterMaskBit[kL07])), "L07 Schedule off");
  // 10 min cap less the 8 min reserve leaves 120 s for a window: 3 x 45 s does not fit.
  CHECK(s.run_now(kL07, 45, 3, 30) == IrrRunResult::kCapReached &&
            event_is(s.last_event(), IrrOutcome::kSkipped, IrrReason::kRes),
        "L07 run-now beyond the zone cap less the VPD reserve: cap_reached (res)");
  s.in.sensor_fault = true;
  s.tick();
  CHECK(s.run_now(kL07, 45, 2, 30) == IrrRunResult::kSensorFault &&
            event_is(s.last_event(), IrrOutcome::kSkipped, IrrReason::kSf),
        "sensor fault still refuses an L07 run-now");
  s.in.sensor_fault = false;
  s.tick();
  CHECK(s.run_now(kL07, 45, 2, 30) == IrrRunResult::kStarted && (s.out.relay_mask & irr_bit(kL07)),
        "L07 with Schedule off: run-now starts");
  s.run_for(200);
  CHECK(event_is(s.find_event(0, kL07), IrrOutcome::kOk, IrrReason::kNone) && s.open_s[kL07] >= 85 &&
            s.open_s[kL07] <= 95,
        "the window delivers its two pulses and ends ok");
}

// #1336 (Jason, 2026-11-05): the master valve L03 opens only for a fertilized variant and never
// with clean water. The pure rule, then a fertilized mister and a clean drip run end to end.
static void master_valve_only_with_fertilized() {
  CHECK(irr_master_rule(irr_bit(kL02)) == (irr_bit(kL02) | irr_bit(kL03)) &&
            irr_master_rule(irr_bit(kL08) | irr_bit(kL03)) == (irr_bit(kL08) | irr_bit(kL03)),
        "a fertilized relay brings L03");
  CHECK(irr_master_rule(irr_bit(kL01) | irr_bit(kL03)) == irr_bit(kL01) &&
            irr_master_rule(irr_bit(kL07) | irr_bit(kL03)) == irr_bit(kL07) &&
            irr_master_rule(irr_bit(kL11) | irr_bit(kL03)) == irr_bit(kL11) && irr_master_rule(irr_bit(kL03)) == 0,
        "a clean relay (or nothing) never has L03");
  CHECK(irr_master_rule(irr_bit(kL02) | irr_bit(kL01) | irr_bit(kL03)) == (irr_bit(kL02) | irr_bit(kL01)),
        "a mixed set keeps L03 shut");
  for (uint8_t i = 0; i < kLineCount; ++i) {
    const bool fert = (kFertRelayMask & irr_bit(i)) != 0;
    CHECK(fert == (kLineKind[i] == IrrLineKind::kFertDrip || kLineKind[i] == IrrLineKind::kFertMister) &&
              fert == (kCleanPartner[i] != kNoLine),
          "the fertilized relays are exactly the fert kinds with a clean partner");
  }
  IrrProgram p = open_program();
  Sim s;
  s.epoch = at(600);
  s.boot(true, &p);
  s.tick();
  CHECK(s.run_now(kL08, 30, 2, 30, 60, 60) == IrrRunResult::kStarted, "a fertilized South mister run starts");
  s.run_for(400);
  CHECK(event_is(s.find_event(0, kL08), IrrOutcome::kOk, IrrReason::kNone), "it feeds and ends ok");
  CHECK(s.open_s[kL03] == s.open_s[kL08] && s.open_s[kL03] > 0, "L03 opens exactly with L08");
  CHECK(s.open_s[kL07] >= 115, "prewet and flush on L07, without L03");
  CHECK(s.run_now(kL01, 120) == IrrRunResult::kStarted, "a clean Wall drip run starts");
  s.run_for(200);
  CHECK(s.open_s[kL01] >= 115 && s.open_s[kL03] == s.open_s[kL08], "the clean run never opened L03");
  CHECK(!s.l03_with_clean && !s.l03_alone && !s.ungranted, "L03 never with a clean relay, never alone");
}

// #1343 follow-up (Jason, 2026-11-05): fog (L11) runs once, under the mister rails that apply to
// it. The climate path fogs while the engine asks (fog_run); the run counts the fog relay's ON time.
static void fog_run_once() {
  IrrProgram p = open_program();
  Sim s;
  s.fog_follows_engine = true;
  s.epoch = at(600);
  s.boot(true, &p);
  s.tick();
  CHECK(s.run_now(kL11, 120) == IrrRunResult::kLineDisabled &&
            event_is(s.last_event(), IrrOutcome::kSkipped, IrrReason::kDis),
        "fog disabled: line_disabled, skipped (dis) on L11");
  enable(p, kL11);
  s.push(p);
  s.tick();
  CHECK(s.run_now(kL11, 30) == IrrRunResult::kBadArgs && s.run_now(kL11, 1801) == IrrRunResult::kBadArgs &&
            s.run_now(kL11, 120, 2, 30) == IrrRunResult::kBadArgs &&
            s.run_now(kL11, 120, 1, 0, 60) == IrrRunResult::kBadArgs,
        "fog bounds: 60-1800 s, one continuous run, no gap, prewet or flush");
  s.in.sensor_fault = true;
  s.tick();
  CHECK(s.run_now(kL11, 120) == IrrRunResult::kSensorFault &&
            event_is(s.last_event(), IrrOutcome::kSkipped, IrrReason::kSf),
        "sensor fault refuses a fog run, as it refuses mist");
  s.in.sensor_fault = false;
  s.tick();
  const uint32_t water = s.rtc.water_centigal;
  s.rtc.water_centigal = uint32_t(s.eng.prog.saf4_gal) * 100u;
  irr_rtc_seal(s.rtc);
  CHECK(s.run_now(kL11, 120) == IrrRunResult::kCapReached &&
            event_is(s.last_event(), IrrOutcome::kSkipped, IrrReason::kSaf),
        "SAF-4 refuses a fog run (cap_reached, saf)");
  s.rtc.water_centigal = water;
  irr_rtc_seal(s.rtc);
  CHECK(s.run_now(kL01, 600) == IrrRunResult::kStarted, "a drip holds the water");
  CHECK(s.run_now(kL11, 120) == IrrRunResult::kManifoldBusy, "fog waits for nobody: manifold_busy");
  s.stop(kL01);
  s.tick();
  CHECK(s.run_now(kL11, 120) == IrrRunResult::kStarted && s.out.fog_run, "fog runs once");
  CHECK(s.out.state.mist.valid && s.out.state.mist.line == kL11 && s.out.state.mist.cause == IrrCause::kRunNow,
        "published as m=10,run_now");
  char buf[kTextBufLen];
  IrrStateExtras x{};
  irr_format_state(s.out.state, x, buf, sizeof(buf));
  CHECK(std::strncmp(buf, "m=10,run_now,0,", 15) == 0, "irr_state names the fog run on m=");
  CHECK(s.run_now(kL11, 120) == IrrRunResult::kLineBusy, "a second fog run: line_busy");
  CHECK(s.run_now(kL07, 30, 1, 30) == IrrRunResult::kManifoldBusy, "one engine run at a time");
  CHECK(!(s.out.relay_mask & kFogMask) && !(s.out.grant_mask & kFogMask), "the engine never switches fog itself");
  s.run_for(130);
  CHECK(event_is(s.find_event(0, kL11), IrrOutcome::kOk, IrrReason::kNone) && !s.out.fog_run &&
            s.open_s[kL11] >= 115 && s.open_s[kL11] <= 130,
        "120 s of fog, then ok");
  // Occupancy holds the climate's fog: the run waits to its deadline and ends short (occ).
  s.in.occupied = true;
  s.tick();
  CHECK(s.run_now(kL11, 60) == IrrRunResult::kStarted, "occupied: the run starts and waits");
  s.run_for(60 + 3600 + 10);
  CHECK(event_is(s.find_event(0, kL11), IrrOutcome::kShort, IrrReason::kOcc), "never fogged: short (occ)");
  s.in.occupied = false;
  s.tick();
  uint16_t scope = 99;
  CHECK(irr_stop_scope(kL11, scope) && scope == 0, "a stop on L11 closes no relay itself");
  CHECK(s.run_now(kL11, 600) == IrrRunResult::kStarted, "a long fog run");
  s.run_for(30);
  s.stop(kL11);
  CHECK(event_is(s.find_event(0, kL11), IrrOutcome::kStopped, IrrReason::kCmd) && !s.out.fog_run, "Stop ends it");
  CHECK(s.run_now(kL11, 600) == IrrRunResult::kStarted, "again");
  s.run_for(20);
  s.in.sensor_fault = true;
  s.tick();
  CHECK(event_is(s.find_event(0, kL11), IrrOutcome::kShort, IrrReason::kSf), "sensor fault ends it short (sf)");
  s.in.sensor_fault = false;
  CHECK(!s.l03_with_clean && !s.ungranted, "no L03 with fog, nothing ungranted");
}

static void stop_disables_nothing() {
  IrrProgram p = irr_rom_default();
  p.lines[kL01].daily_cap_min = 0;
  add_slot(p, 3, kL01, 420, 600);
  add_slot(p, 4, kL01, 480, 300);
  Sim s;
  s.epoch = at(419, 55);
  s.boot(true, &p);
  s.run_for(60);
  s.stop(kL04);
  CHECK(s.out.relay_mask & irr_bit(kL01), "a stop on another line leaves L01 running");
  s.stop(kL01);
  CHECK(!(s.out.relay_mask & irr_bit(kL01)), "stop closes L01 inside the call");
  CHECK(event_is(s.find_event(3, kL01), IrrOutcome::kStopped, IrrReason::kCmd), "stopped (cmd)");
  s.run_for(120);
  CHECK(!(s.relays & irr_bit(kL01)), "the stopped occurrence does not restart");
  s.run_until(481);
  CHECK(s.relays & irr_bit(kL01), "the next scheduled run still starts on its own");
  // A VPD pulse in scope loses its grant.
  s.run_until(500);
  IrrVpdResult r = irr_vpd_request(s.eng, kL04, 45, s.ms, s.out);
  CHECK(r.answer == IrrVpdAnswer::kGrant && (s.out.grant_mask & irr_bit(kL04)), "VPD granted");
  s.stop(kAllLines);
  CHECK(!(s.out.grant_mask & irr_bit(kL04)), "stop all revokes the VPD grant");
}

static void missed_and_fcfs() {
  IrrProgram p = open_program();
  add_slot(p, 1, kL01, 420, 4800);       // 80 min drip
  add_slot(p, 2, kL04, 425, 30, 2, 60);  // a window due during it
  add_slot(p, 3, kL05, 421, 60);         // a drip due a minute after L01
  Sim s;
  s.epoch = at(419, 55);
  s.boot(true, &p);
  s.run_until(430);
  CHECK(has_wait(s.out, kL04, IrrReason::kMan) && has_wait(s.out, kL05, IrrReason::kMan), "both wait for the manifold");
  bool until_ok = false;
  for (uint8_t i = 0; i < s.out.state.wait_count; ++i) {
    if (s.out.state.waits[i].line == kL05) until_ok = s.out.state.waits[i].until_epoch == s.out.state.drip.end_epoch;
  }
  CHECK(until_ok, "w=4,man names the holder's planned end");
  CHECK(s.out.state.waits[0].line == kL05, "earliest due first");
  s.run_until(486);
  CHECK(event_is(s.find_event(3, kL05), IrrOutcome::kMissed, IrrReason::kMan) &&
            event_is(s.find_event(2, kL04), IrrOutcome::kMissed, IrrReason::kMan),
        "60 min late: missed (man)");
  s.run_until(510);
  CHECK(event_is(s.find_event(1, kL01), IrrOutcome::kOk, IrrReason::kNone), "the long drip completes");
  CHECK(s.open_s[kL05] == 0 && s.open_s[kL04] == 0, "missed occurrences never open later");
  // First come, first served: the earlier start goes first, the later one runs after.
  IrrProgram q = open_program();
  add_slot(q, 5, kL05, 421, 120);
  add_slot(q, 6, kL01, 420, 120);
  Sim t;
  t.epoch = at(419, 55);
  t.boot(true, &q);
  t.run_until(430);
  uint16_t seq[8];
  const int n = t.nonzero_seq(seq, 8);
  CHECK(n == 2 && seq[0] == irr_bit(kL01) && seq[1] == irr_bit(kL05), "L01 (07:00) before L05 (07:01)");
  CHECK(event_is(t.find_event(5, kL05), IrrOutcome::kOk, IrrReason::kNone), "the second starts late, inside 60 min");
}

static void no_valid_time() {
  IrrProgram p = irr_rom_default();
  add_slot(p, 3, kL01, 420, 300);
  Sim s;
  s.epoch = at(419, 55);
  s.clock_ok = false;
  s.boot(true, &p);
  s.run_until(500);
  CHECK(s.open_s[kL01] == 0 && s.rtc.ring_count == 0, "no valid time: nothing starts on schedule");
  CHECK(s.run_now(kL01, 120) == IrrRunResult::kStarted && (s.out.relay_mask & irr_bit(kL01)), "run-now still works");
  s.stop(kAllLines);
  CHECK(!(s.out.relay_mask & irr_bit(kL01)), "and stop");
  // A running drip finishes on its own timer when time is lost.
  Sim t;
  t.epoch = at(419, 55);
  t.boot(true, &p);
  t.run_for(60);
  t.clock_ok = false;
  t.run_for(400);
  CHECK(t.open_s[kL01] >= 295 && t.open_s[kL01] <= 305 &&
            event_is(t.find_event(3, kL01), IrrOutcome::kOk, IrrReason::kNone),
        "a running drip finishes on its own timer");
}

static void midnight_rollover() {
  IrrProgram p = irr_rom_default();
  p.lines[kL01].daily_cap_min = 0;
  add_slot(p, 3, kL01, 1430, 1800);  // 23:50, 30 min: past midnight
  Sim s;
  s.epoch = at(1429, 55);
  s.boot(true, &p);
  s.run_until(1439, 55);
  CHECK(s.rtc.line_on_s[kL01] >= 590, "counting before midnight");
  irr_count_water(s.eng, 5000);
  s.run_until(1440 + 5);
  CHECK(s.rtc.water_centigal == 0 && s.rtc.line_on_s[kL01] <= 300 && s.rtc.local_day == kDayNum + 1,
        "local midnight zeroes the counters");
  s.run_until(1440 + 60);
  CHECK(s.count_events(3, kL01) == 1 && s.find_event(3, kL01)->local_day == kDayNum &&
            event_is(s.find_event(3, kL01), IrrOutcome::kOk, IrrReason::kNone),
        "the outcome carries its occurrence's day, once");
  CHECK(s.open_s[kL01] >= 1795 && s.open_s[kL01] <= 1810, "the run crossed midnight whole");
}

static void more_engine_rules() {
  {
    // SAF-5 on a mister feed: L03 never opens while fog runs; l03_hold asks for fog OFF.
    IrrProgram p = irr_rom_default();
    enable(p, kL07);
    enable(p, kL08);
    add_slot(p, 9, kL08, 420, 30, 3, 60, 60, 60);
    Sim s;
    s.epoch = at(419, 55);
    s.boot(true, &p);
    s.fog_want = true;
    s.run_for(5);
    CHECK((s.relays & kFogMask) && (s.relays & irr_bit(kL07)), "fog runs beside the clean prewet");
    CHECK(s.out.mist_on == kL07 && s.out.mist_cmd_s == 60, "mist_cmd_s: the prewet's commanded 60 s");
    bool held = false;
    bool pulse_len = false;
    for (int i = 0; i < 120; ++i) {
      s.tick();
      if (s.out.l03_hold && !s.out.l03_on) held = true;
      if (s.out.mist_on == kL08 && s.out.mist_cmd_s == 30) pulse_len = true;
    }
    CHECK(held, "a feed pulse waiting on fog raises l03_hold first");
    CHECK(pulse_len, "mist_cmd_s: a feed pulse's commanded 30 s");
    CHECK(!s.l03_with_fog && !s.l03_with_clean_mist, "L03 never open with fog or clean mist");
    CHECK(event_is(s.find_event(9, kL08), IrrOutcome::kOk, IrrReason::kNone) && s.open_s[kL08] >= 85,
          "the feed still completes");
  }
  {
    // Fertilizer run-now in SAFETY_COOL would be preempted at once: refused.
    IrrProgram p = irr_rom_default();
    enable(p, kL02);
    Sim s;
    s.epoch = at(600);
    s.boot(true, &p);
    s.in.safety_cool = true;
    s.tick();
    CHECK(s.run_now(kL02, 120, 1, 0, 30, 60) == IrrRunResult::kManifoldBusy &&
              event_is(s.last_event(), IrrOutcome::kSkipped, IrrReason::kMan) && s.nvs.flash.fert_stage == 0,
          "fertilizer run-now in SAFETY_COOL: manifold_busy, nothing journaled");
    CHECK(s.run_now(kL01, 120) == IrrRunResult::kStarted && !(s.out.relay_mask & irr_bit(kL01)),
          "a clean drip run-now starts paused, to resume after SAFETY_COOL");
  }
  {
    // An outcome whose claim never saves is retried, then dropped 10 min past its deadline.
    IrrProgram p = irr_rom_default();
    add_slot(p, 7, kL05, 420, 300);  // L05 disabled: skipped (dis), once its claim saves
    Sim s;
    s.epoch = at(419, 55);
    s.boot(true, &p);
    s.nvs.fail_save = true;
    s.run_until(430);
    CHECK(s.rtc.ring_count == 0 && s.nvs.saves > 0, "no claim, no outcome: retried");
    s.run_until(491);
    const int saves = s.nvs.saves;
    s.run_until(500);
    CHECK(s.nvs.saves == saves && s.rtc.ring_count == 0, "dropped unpublished 10 min past the deadline");
  }
  {
    // A pushed program that disables the running window's line stops it (dis).
    IrrProgram p = open_program();
    add_slot(p, 2, kL04, 420, 30, 6, 60);
    Sim s;
    s.epoch = at(419, 55);
    s.boot(true, &p);
    s.run_for(100);
    IrrProgram off = p;
    enable(off, kL04, false);
    s.push(off);
    s.tick();
    CHECK(event_is(s.find_event(2, kL04), IrrOutcome::kStopped, IrrReason::kDis) && !(s.relays & irr_bit(kL04)),
          "disabling the line stops the window: stopped (dis)");
  }
  {
    // A window cut by a soft reset resumes its remaining pulses.
    IrrProgram p = open_program();
    add_slot(p, 2, kL04, 420, 30, 4, 60);
    Sim s;
    s.epoch = at(419, 55);
    s.boot(true, &p);
    s.run_for(100);  // pulse 1 done, pulse 2 under way
    CHECK(s.rtc.run.kind == 2 && s.rtc.run.done == 1, "the RTC block holds the window's progress");
    s.boot(false, &p);
    s.run_for(400);
    CHECK(event_is(s.find_event(2, kL04), IrrOutcome::kOk, IrrReason::kNone) && s.count_events(2, kL04) == 1,
          "the window finishes once after a soft reset");
    CHECK(s.open_s[kL04] >= 120 && s.open_s[kL04] <= 155, "four pulses, the cut one delivered again");
  }
  {
    // Sensing recovers mid-fallback: the remaining pulses wait (vok) and the window ends short.
    IrrProgram seed{};
    decode_vector(1, seed);
    const SolarTimes st = day0_solar();
    Sim s;
    s.epoch = at(st.solar_noon_min - 121);
    s.boot(true, &seed);
    s.in.sensor_degraded = true;
    s.run_until(st.solar_noon_min - 120, 50);
    s.in.sensor_degraded = false;
    s.run_until(st.solar_noon_min - 50);
    CHECK(event_is(s.find_event(1, kL04), IrrOutcome::kShort, IrrReason::kVok), "short (vok)");
    CHECK(s.open_s[kL04] >= 40 && s.open_s[kL04] <= 95, "only the pulses delivered while degraded");
  }
  {
    // A fallback window still running at sunset ends there: short, with no reason.
    IrrProgram p = irr_rom_default();
    add_slot(p, 4, kL04, -2, 45, 4, 60, 0, 0, true, 3);  // sunset - 2 min, 4 x 45 s every 105 s
    const SolarTimes st = day0_solar();
    Sim s;
    s.epoch = at(st.sunset_min - 3);
    s.boot(true, &p);
    s.in.sensor_degraded = true;
    s.run_until(st.sunset_min + 10);
    CHECK(event_is(s.find_event(4, kL04), IrrOutcome::kShort, IrrReason::kNone), "sunset: short, no reason");
    CHECK(s.open_s[kL04] >= 85 && s.open_s[kL04] <= 95, "the two daylight pulses only");
  }
  {
    // A run waiting on a VPD pulse names that pulse's planned end, never its own.
    IrrProgram p = open_program();
    add_slot(p, 2, kL04, 420, 30, 4, 60);
    Sim s;
    s.in.mister_min_off_s = 30;
    s.epoch = at(419, 55);
    s.boot(true, &p);
    s.run_for(35);
    s.vpd_want = kL07;
    s.vpd_on_s = 90;
    s.vpd_gap_s = 600;
    s.run_until(421, 35);
    bool named = false;
    for (uint8_t i = 0; i < s.out.state.wait_count; ++i) {
      const IrrWait& w = s.out.state.waits[i];
      if (w.line == kL04 && w.reason == IrrReason::kMan) {
        named = w.until_epoch == s.out.state.mist.end_epoch && w.until_epoch != s.eng.run.end_epoch;
      }
    }
    CHECK(s.out.state.mist.cause == IrrCause::kVpd && named, "w=3,man,<the VPD pulse's end>");
  }
}

// A day of the full golden program with weather, VPD demand, occupancy and faults: the
// manifold rules hold every tick.
static void full_program_day_invariants() {
  IrrProgram full{};
  CHECK(decode_vector(2, full), "full decodes");
  for (IrrLine& l : full.lines) l.paused_until = 0;
  Sim s;
  s.epoch = kDay0 + 86400u * 2u;  // a Wednesday
  s.boot(true, &full);
  s.in.mister_min_off_s = 30;
  s.vpd_want = kL09;
  s.vpd_on_s = 45;
  s.vpd_gap_s = 60;
  for (int h = 0; h < 24; ++h) {
    s.in.occupied = (h == 9);
    s.in.sensor_fault = (h == 14);
    s.in.safety_cool = (h == 15);
    s.in.vpd_emergency = (h == 16);
    s.in.climate_demand = (h >= 10 && h < 18);
    s.in.rh_pct = h == 6 ? 99.0f : NAN;
    s.fog_want = (h >= 12 && h < 17);
    s.run_for(3600, 5);
  }
  CHECK(!s.two_misters, "never two misters");
  CHECK(!s.drip_with_mist, "never a drip with mist or fog");
  CHECK(!s.l03_alone, "L03 only with a fertilizer relay");
  CHECK(!s.l03_with_fog && !s.l03_with_clean_mist && !s.l03_with_clean,
        "SAF-5: no fog and no clean mist while L03 is open; #1336: no clean relay at all");
  CHECK(!s.ungranted, "every engine relay granted");
  CHECK(!s.mist_in_sf, "no engine mist in SENSOR_FAULT");
  CHECK(!s.mist_occupied, "no engine mist while occupancy is confirmed (D-IRP-10)");
  CHECK(s.disabled_opened == 0, "no engine relay on a disabled line");
  CHECK(s.rtc.ring_count > 0 && s.rtc.next_seq > 8, "the day settled its occurrences");
  char buf[256];
  CHECK(irr_format_events(s.rtc, buf, sizeof(buf)) > 0, "irr_events formats");
}

// ── F. Engine wiring (VD-45, #1160) ──────────────────────────────────────────

// The exact text the controller publishes. verdify_schemas/tests/test_irrigation_firmware_strings.py
// extracts every literal of these three macros and parses it with #1151's parsers.
#define EXPECT_IRR_PROGRAM_ID(actual, literal) \
  CHECK(std::strcmp((actual), (literal)) == 0, "irr_program_id reads " literal)
#define EXPECT_IRR_STATE(actual, literal) CHECK(std::strcmp((actual), (literal)) == 0, "irr_state reads " literal)
#define EXPECT_IRR_EVENTS(actual, literal) CHECK(std::strcmp((actual), (literal)) == 0, "irr_events reads " literal)

// Q-05, F14: leak_detected is not an engine input, so nothing the engine decides can read it.
template <typename T, typename = void>
struct HasLeakDetected : std::false_type {};
template <typename T>
struct HasLeakDetected<T, std::void_t<decltype(&T::leak_detected)>> : std::true_type {};
template <typename T, typename = void>
struct HasLeak : std::false_type {};
template <typename T>
struct HasLeak<T, std::void_t<decltype(&T::leak)>> : std::true_type {};
static_assert(!HasLeakDetected<IrrInputs>::value && !HasLeak<IrrInputs>::value,
              "leak_detected is not an engine input (Q-05, F14)");

static void hex_encode(const uint8_t* p, size_t n, char* out) {
  static const char kH[] = "0123456789abcdef";
  for (size_t i = 0; i < n; ++i) {
    out[2 * i] = kH[p[i] >> 4];
    out[2 * i + 1] = kH[p[i] & 0x0F];
  }
  out[2 * n] = '\0';
}

// America/Denver for this test's span (2026): MDT from 2026-03-08 09:00 UTC to 2026-11-01 08:00 UTC.
static int denver_offset(uint32_t epoch) { return (epoch >= 1772960400u && epoch < 1793520000u) ? kMdt : kMst; }
// The local fields ESPHome's ESPTime gives for `epoch` (localtime()).
static void denver_fields(uint32_t epoch, int& h, int& m, int& s) {
  const int64_t local = int64_t(epoch) + int64_t(denver_offset(epoch)) * 60;
  const int64_t sod = ((local % 86400) + 86400) % 86400;
  h = int(sod / 3600);
  m = int(sod % 3600 / 60);
  s = int(sod % 60);
}

static uint8_t cause_index(const char* s) {
  for (uint8_t i = 0; i < kCauseCount; ++i) {
    if (std::strcmp(kCauseNames[i], s) == 0) return i;
  }
  return 255;
}
static uint8_t reason_index(const char* s) {
  for (uint8_t i = 1; i < kReasonCount; ++i) {
    if (std::strcmp(kReasonCodes[i], s) == 0) return i;
  }
  return 0;
}
static IrrReject reject_of(const char* s) {
  for (uint8_t i = 1; i < 7; ++i) {
    if (std::strcmp(irr_reject_token(IrrReject(i)), s) == 0) return IrrReject(i);
  }
  return IrrReject::kNone;
}
static IrrRunToken golden_run(const GoldenRun& g) {
  IrrRunToken t{};
  t.valid = g.valid;
  if (g.valid) {
    t.line = g.line;
    t.cause = IrrCause(cause_index(g.cause));
    t.slot_id = g.slot_id;
    t.start_epoch = g.start_epoch;
    t.end_epoch = g.end_epoch;
  }
  return t;
}
static void golden_state(const GoldenState& g, IrrStateFields& s, IrrStateExtras& x) {
  s = IrrStateFields{};
  x = IrrStateExtras{};
  s.drip = golden_run(g.drip);
  s.mist = golden_run(g.mist);
  for (uint8_t i = 0; i < g.wait_count; ++i) {
    s.waits[s.wait_count++] = {g.waits[i].line, IrrReason(reason_index(g.waits[i].reason)), g.waits[i].until_epoch};
  }
  for (uint8_t i = 0; i < g.fault_count; ++i) {
    x.faults[x.fault_count++] = {g.faults[i].line, g.faults[i].kind == 'l', g.faults[i].until_epoch};
  }
  if (g.vpd[0] != '\0') std::memcpy(x.vpd_source, g.vpd, 3);
  s.reject = reject_of(g.reject);
}

// The device's program services in order (irr_program_begin, irr_program_chunk per <= 192 B,
// irr_program_commit), hex as the ingestor sends it and policy_hex_decode() as the device decodes
// it. `skip_chunk` leaves one chunk out.
static IrrCommit push_hex(IrrStaging& st, const uint8_t* rec, int32_t declared_len, const char* sha_hex, IrrProgram& out,
                          IrrReject& why, int skip_chunk = -1) {
  uint8_t hash[32];
  size_t n = 0;
  const bool ok = sha_hex != nullptr &&
                  verdify_policy::policy_hex_decode(sha_hex, std::strlen(sha_hex), hash, sizeof(hash), &n) && n == 32;
  irr_staging_begin(st, declared_len, ok ? hash : nullptr);
  char hex[2 * kChunkMax + 1];
  int k = 0;
  for (size_t off = 0; off < kRecordLen; off += kChunkMax, ++k) {
    const size_t len = std::min(kChunkMax, kRecordLen - off);
    CHECK(off == kGoldenChunks[1][k][0] && len == kGoldenChunks[1][k][1], "the chunks are #1151's (offset, length)");
    if (k == skip_chunk) continue;
    hex_encode(rec + off, len, hex);
    uint8_t chunk[kChunkMax];
    size_t cn = 0;
    const bool cok = verdify_policy::policy_hex_decode(hex, std::strlen(hex), chunk, sizeof(chunk), &cn);
    CHECK(irr_staging_chunk(st, int32_t(off), cok ? chunk : nullptr, cok ? cn : 0), "each chunk stages");
  }
  return irr_staging_commit(st, out, why);
}

static void wiring_program_id_text() {
  char buf[32];
  CHECK(irr_format_program_id(false, nullptr, buf, sizeof(buf)) == 26, "irr_program_id is 26 characters");
  EXPECT_IRR_PROGRAM_ID(buf, "h=0000000000000000 src=rom");
  const uint8_t any[8] = {1, 2, 3, 4, 5, 6, 7, 8};
  irr_format_program_id(false, any, buf, sizeof(buf));
  EXPECT_IRR_PROGRAM_ID(buf, "h=0000000000000000 src=rom");
  irr_format_program_id(true, kGoldenVectorSha256[1], buf, sizeof(buf));
  EXPECT_IRR_PROGRAM_ID(buf, "h=7ae03efe564082b7 src=nvs");
  CHECK(std::strcmp(buf, kProgramIdSamples[0].text) == 0, "the seed_nvs golden sample, byte for byte");
  irr_format_program_id(true, kGoldenVectorSha256[2], buf, sizeof(buf));
  EXPECT_IRR_PROGRAM_ID(buf, "h=3b76a2316e2f39d8 src=nvs");
  CHECK(std::strcmp(buf, kProgramIdSamples[1].text) == 0, "the full_nvs golden sample, byte for byte");
  irr_format_program_id(false, nullptr, buf, sizeof(buf));
  CHECK(std::strcmp(buf, kProgramIdSamples[2].text) == 0, "the rom_default golden sample, byte for byte");
  char tiny[26];
  CHECK(irr_format_program_id(false, nullptr, tiny, sizeof(tiny)) == 0 && tiny[0] == '\0', "a short buffer: nothing");
}

// irr_format_state() reproduces every golden irr_state sample from its parsed form, the overflow
// sample from the state it was formatted from (the same w= tokens drop).
static void wiring_state_text_matches_python() {
  CHECK(sizeof(kStateParsed) / sizeof(kStateParsed[0]) == kStateSampleCount, "one formatter input per sample");
  for (size_t i = 0; i < kStateSampleCount; ++i) {
    IrrStateFields s{};
    IrrStateExtras x{};
    golden_state(kStateParsed[i], s, x);
    char buf[kTextBufLen];
    const size_t n = irr_format_state(s, x, buf, sizeof(buf));
    char label[96];
    std::snprintf(label, sizeof(label), "irr_state sample %s formats byte for byte", kStateSamples[i].name);
    CHECK(std::strcmp(buf, kStateSamples[i].text) == 0 && n == std::strlen(kStateSamples[i].text), label);
    CHECK(n <= kTextMax, "irr_state fits 255 characters");
  }
}

// What the parsers refuse is never written; short buffers and overflow stay grammatical.
static void wiring_state_text_rules() {
  IrrStateFields s{};
  IrrStateExtras x{};
  char buf[kTextBufLen];
  s.drip = {true, kL04, IrrCause::kSchedule, 1, 100, 200};  // a mister on the drip manifold: dropped
  s.mist = {true, kL01, IrrCause::kSchedule, 1, 100, 200};  // a drip on the mist manifold: dropped
  s.waits[s.wait_count++] = {kL03, IrrReason::kMan, 0};      // L03 takes no runs
  s.waits[s.wait_count++] = {kL11, IrrReason::kMan, 0};      // nor does fog
  s.waits[s.wait_count++] = {kL01, IrrReason::kNone, 0};     // no reason, no token
  s.waits[s.wait_count++] = {kL01, IrrReason::kMan, 0};
  x.faults[x.fault_count++] = {kL01, true, 50};  // not a mister
  x.faults[x.fault_count++] = {kL04, false, 100};
  x.faults[x.fault_count++] = {kL04, true, 200};  // one f= per line
  x.vpd_source[0] = 'h';
  x.vpd_source[1] = 'x';  // unknown code: no v=
  x.vpd_source[2] = 'o';
  irr_format_state(s, x, buf, sizeof(buf));
  EXPECT_IRR_STATE(buf, "w=0,man f=3,b,100");
  // An end before the start publishes the start (the engine's tokens never do; the parser refuses it).
  s = IrrStateFields{};
  x = IrrStateExtras{};
  s.drip = {true, kL02, IrrCause::kFeed, 0, 1793631600, 1793631000};
  irr_format_state(s, x, buf, sizeof(buf));
  EXPECT_IRR_STATE(buf, "d=1,feed,0,1793631600,1793631600");
  // 25 w= tokens and a full tail: the latest-due w= drop, whole tokens only.
  s = IrrStateFields{};
  x = IrrStateExtras{};
  s.drip = {true, kL01, IrrCause::kSchedule, 65535, 4000000000u, 4000003600u};
  s.mist = {true, kL04, IrrCause::kVpd, 0, 4000000100u, 4000000145u};
  for (uint8_t i = 0; i < kMaxSlots + 1; ++i) s.waits[s.wait_count++] = {kL05, IrrReason::kMan, 4000003600u + i};
  for (uint8_t line : {kL04, kL07, kL08, kL09, kL10}) x.faults[x.fault_count++] = {line, line == kL08, 4000086400u};
  std::memcpy(x.vpd_source, "hpo", 3);
  s.reject = IrrReject::kBadHeader;
  const size_t n = irr_format_state(s, x, buf, sizeof(buf));
  const char* tail = " f=3,b,4000086400 f=6,b,4000086400 f=7,l,4000086400 f=8,b,4000086400 f=9,b,4000086400 v=hpo j=bad_header";
  CHECK(n <= kTextMax && n == std::strlen(buf), "the overflow fits 255 characters");
  CHECK(n > std::strlen(tail) && std::strcmp(buf + n - std::strlen(tail), tail) == 0, "the tail survives whole");
  int kept = 0;
  for (const char* p = std::strstr(buf, " w="); p; p = std::strstr(p + 1, " w=")) ++kept;
  CHECK(kept > 0 && kept < kMaxSlots + 1, "some w= tokens drop, latest due first");
  char last[40];
  std::snprintf(last, sizeof(last), " w=4,man,%lu ", static_cast<unsigned long>(4000003600u + kept - 1));
  CHECK(std::strstr(buf, last) != nullptr, "the earliest-due w= tokens are the ones kept, in order");
  // A short buffer writes whole tokens only.
  char small[24];
  irr_format_state(s, x, small, sizeof(small));
  CHECK(std::strlen(small) < sizeof(small) && std::strchr(small, ',') == nullptr, "a short buffer: no partial token");
}

static void wiring_apply_plan() {
  IrrOutputs out{};
  // The prewet's L01 closes as the feed opens L02 with L03: the bits first, L01 off, then L02 and L03.
  out.relay_mask = uint16_t(irr_bit(kL02) | irr_bit(kL03));
  out.grant_mask = out.relay_mask;
  IrrApply a = irr_apply_plan(irr_bit(kL01), irr_bit(kL01), irr_bit(kL01), out, 0);
  CHECK(a.grant_before == uint16_t(irr_bit(kL01) | irr_bit(kL02) | irr_bit(kL03)) && a.turn_off == irr_bit(kL01) &&
            a.turn_on == uint16_t(irr_bit(kL02) | irr_bit(kL03)) && a.grant_after == out.relay_mask,
        "bits before the turn_on, cleared after the turn_off");
  // A climate VPD grant changes the bits only; the climate path opens the relay itself.
  out = IrrOutputs{};
  out.grant_mask = irr_bit(kL04);
  a = irr_apply_plan(0, 0, 0, out, 0);
  CHECK(a.grant_before == irr_bit(kL04) && a.grant_after == irr_bit(kL04) && a.turn_on == 0 && a.turn_off == 0,
        "a VPD grant: its bit, no relay change");
  // Outside a stop the engine never closes what it does not hold (the climate's VPD pulse).
  out = IrrOutputs{};
  a = irr_apply_plan(irr_bit(kL04), 0, uint16_t(irr_bit(kL04) | kFogMask), out, 0);
  CHECK(a.turn_off == 0 && a.grant_after == 0, "a released VPD bit clears; the relay is the climate path's");
  // A stop closes every open wet relay in its scope inside the call, never fog.
  uint16_t scope = 0;
  CHECK(irr_stop_scope(kAllLines, scope), "stop 255");
  a = irr_apply_plan(uint16_t(irr_bit(kL04) | irr_bit(kL01)), irr_bit(kL01),
                     uint16_t(irr_bit(kL04) | irr_bit(kL01) | kFogMask), out, scope);
  CHECK(a.turn_off == uint16_t(irr_bit(kL04) | irr_bit(kL01)) && !(a.turn_off & kFogMask) && a.grant_after == 0,
        "stop 255: L01 and L04 off, their bits cleared, fog untouched");
  // A relay the engine wants and already reads ON is left alone.
  out.relay_mask = irr_bit(kL05);
  out.grant_mask = irr_bit(kL05);
  a = irr_apply_plan(irr_bit(kL05), irr_bit(kL05), irr_bit(kL05), out, 0);
  CHECK(a.turn_on == 0 && a.turn_off == 0 && a.grant_after == irr_bit(kL05), "a held relay stays as it is");
}

static void wiring_program_push_staging() {
  for (size_t v = 0; v < kGoldenVectorCount; ++v) {
    IrrStaging st{};
    char sha[65];
    hex_encode(kGoldenVectorSha256[v], 32, sha);
    IrrProgram p{};
    IrrReject why = IrrReject::kBadMagic;
    IrrProgram expect{};
    CHECK(push_hex(st, kGoldenVectorBytes[v], int32_t(kRecordLen), sha, p, why) == IrrCommit::kAccepted &&
              why == IrrReject::kNone && decode_vector(v, expect) && same_program(p, expect),
          "each golden vector pushes in four chunks and decodes");
    CHECK(std::memcmp(st.record.rec, kGoldenVectorBytes[v], kRecordLen) == 0 &&
              std::memcmp(st.record.sha256, kGoldenVectorSha256[v], 32) == 0 && !st.open,
          "the staged record and hash are what NVS keeps; the push is closed");
    CHECK(irr_staging_commit(st, p, why) == IrrCommit::kNotOpen, "a second commit has nothing to commit");
  }
  char sha[65];
  hex_encode(kGoldenVectorSha256[2], 32, sha);
  IrrStaging st{};
  IrrProgram p{};
  IrrReject why = IrrReject::kNone;
  CHECK(irr_staging_commit(st, p, why) == IrrCommit::kNotOpen, "a commit with no begin");
  CHECK(push_hex(st, kGoldenVectorBytes[2], 583, sha, p, why) == IrrCommit::kRejected && why == IrrReject::kBadLength,
        "len must be 584: bad_length");
  CHECK(push_hex(st, kGoldenVectorBytes[2], 600, sha, p, why) == IrrCommit::kRejected && why == IrrReject::kBadLength,
        "len 600: bad_length");
  char wrong[65];
  std::memcpy(wrong, sha, sizeof(wrong));
  wrong[5] = wrong[5] == '0' ? '1' : '0';
  CHECK(push_hex(st, kGoldenVectorBytes[2], int32_t(kRecordLen), wrong, p, why) == IrrCommit::kRejected &&
            why == IrrReject::kBadHash,
        "a hash that does not match: bad_hash");
  CHECK(push_hex(st, kGoldenVectorBytes[2], int32_t(kRecordLen), "zz", p, why) == IrrCommit::kRejected &&
            why == IrrReject::kBadHash,
        "a sha256_hex that is not 64 hex digits: bad_hash");
  CHECK(push_hex(st, kGoldenVectorBytes[2], int32_t(kRecordLen), sha, p, why, 0) == IrrCommit::kRejected &&
            why == IrrReject::kBadMagic,
        "the first chunk missing: bad_magic");
  CHECK(push_hex(st, kGoldenVectorBytes[2], int32_t(kRecordLen), sha, p, why, 2) == IrrCommit::kRejected &&
            why == IrrReject::kBadHash,
        "a later chunk missing: bad_hash");
  // Chunk rules.
  IrrStaging c{};
  uint8_t data[kChunkMax + 1] = {};
  CHECK(!irr_staging_chunk(c, 0, data, 1), "a chunk with no begin is refused");
  irr_staging_begin(c, int32_t(kRecordLen), kGoldenVectorSha256[1]);
  CHECK(!irr_staging_chunk(c, 0, data, kChunkMax + 1), "more than 192 B is refused");
  CHECK(!irr_staging_chunk(c, int32_t(kRecordLen) - 7, data, 8), "bytes past 584 are refused");
  CHECK(!irr_staging_chunk(c, -1, data, 4), "a negative offset is refused");
  CHECK(!irr_staging_chunk(c, 0, nullptr, 4), "an undecodable chunk is refused");
  CHECK(!irr_staging_chunk(c, 0, data, 0), "an empty chunk is refused");
  CHECK(irr_staging_chunk(c, int32_t(kRecordLen) - 8, data, 8) && irr_staging_chunk(c, 0, data, kChunkMax),
        "the last 8 bytes and a full 192 B chunk fit");
  uint8_t odd[1];
  size_t on = 0;
  CHECK(!verdify_policy::policy_hex_decode("abc", 3, odd, sizeof(odd), &on) &&
            !verdify_policy::policy_hex_decode("zz", 2, odd, sizeof(odd), &on),
        "odd-length or non-hex chunks do not decode (and are refused)");
}

static void wiring_commands_and_clock() {
  CHECK(irr_parse_cmd("run") == IrrCmd::kRun && irr_parse_cmd("stop") == IrrCmd::kStop &&
            irr_parse_cmd("clear_fault") == IrrCmd::kClearFault,
        "run, stop and clear_fault");
  CHECK(irr_parse_cmd("Run") == IrrCmd::kUnknown && irr_parse_cmd("") == IrrCmd::kUnknown &&
            irr_parse_cmd(nullptr) == IrrCmd::kUnknown && irr_parse_cmd("override") == IrrCmd::kUnknown,
        "anything else is unknown");
  IrrRunArgs a{};
  CHECK(irr_run_args_from(kL01, 600, 1, 0, 0, 0, a) && a.line == kL01 && a.on_s == 600 && a.count == 1 && a.gap_s == 0,
        "L01 for 10 min");
  CHECK(irr_run_args_from(kL08, 20, 4, 60, 60, 120, a) && a.prewet_s == 60 && a.flush_s == 120, "an L08 feed");
  CHECK(!irr_run_args_from(-1, 600, 1, 0, 0, 0, a) && !irr_run_args_from(11, 600, 1, 0, 0, 0, a) &&
            !irr_run_args_from(kAllLines, 600, 1, 0, 0, 0, a),
        "a run names one line, L01-L11");
  CHECK(!irr_run_args_from(kL04, 65536, 1, 30, 0, 0, a) && !irr_run_args_from(kL04, 30, 256, 30, 0, 0, a) &&
            !irr_run_args_from(kL04, 30, 2, -60, 0, 0, a) && !irr_run_args_from(kL02, 30, 1, 0, 0, -1, a),
        "values outside their field are bad_args");
  uint16_t scope = 0;
  CHECK(irr_stop_scope(kAllLines, scope) && scope == kGrantableMask && !(scope & kFogMask),
        "stop 255: every wet relay, never fog");
  CHECK(irr_stop_scope(kL03, scope) && scope == irr_bit(kL03), "stop one line");
  CHECK(irr_stop_scope(kL11, scope) && scope == 0, "stop L11: ends a fog run-once, switches no relay (#1343 follow-up)");
  CHECK(!irr_stop_scope(-1, scope) && scope == 0 && !irr_stop_scope(254, scope), "any other value stops nothing");
  // The device's local offset from one ESPTime reading, across both 2026 changeovers.
  const uint32_t jump = 1772960400u, back = 1793520000u;
  for (uint32_t ep : {kDay0, kDay0 + 25199u, kDay0 + 25200u, jump - 1u, jump, jump + 3599u, back - 1u, back,
                      back + 3600u, 1782460800u}) {
    int h, m, s;
    denver_fields(ep, h, m, s);
    char label[64];
    std::snprintf(label, sizeof(label), "utc offset at %lu", static_cast<unsigned long>(ep));
    CHECK(irr_utc_offset_min(h, m, s, ep) == denver_offset(ep), label);
  }
}

static void wiring_water_centigal() {
  float carry = 0.0f;
  uint64_t total = 0;
  for (int i = 0; i < 3600; ++i) total += irr_water_centigal(1.0f, 1000u, carry);
  CHECK(std::fabs(double(total) + carry - 6000.0) < 0.05 && carry >= 0.0f && carry < 1.0f,
        "1 gpm for an hour is 60 gal (6000 centi-gallons), fraction carried");
  carry = 0.0f;
  total = 0;
  for (int i = 0; i < 6000; ++i) total += irr_water_centigal(0.01f, 1000u, carry);
  CHECK(std::fabs(double(total) + carry - 100.0) < 0.05, "a trickle below a centi-gallon per tick is not lost");
  CHECK(irr_water_centigal(NAN, 1000u, carry) == 0 && irr_water_centigal(-2.0f, 1000u, carry) == 0 &&
            irr_water_centigal(2.0f, 0u, carry) == 0,
        "no flow, no water");
  carry = NAN;
  CHECK(irr_water_centigal(6.0f, 1000u, carry) == 10 && carry >= 0.0f && carry < 0.01f, "a bad carry restarts at 0");
}

// The RH and dew margin the engine reads come from one measured pair (the averages, else the
// case probe): the dewpoint_avg sensor's own Magnus form, and nothing from a missing reading.
static void wiring_air_inputs() {
  auto dewpoint_avg = [](float t_f, float rh) {  // sensors.yaml dewpoint_avg, verbatim
    float tc = (t_f - 32.0f) * 5.0f / 9.0f;
    float lnR = logf(rh / 100.0f);
    float dpC = (237.3f * (lnR + ((17.27f * tc) / (237.3f + tc)))) / (17.27f - lnR - ((17.27f * tc) / (237.3f + tc)));
    return dpC * 9.0f / 5.0f + 32.0f;
  };
  IrrAirInputs a = irr_air_inputs(77.0f, 50.0f, 5.0f);
  CHECK(a.dew_margin_known && a.rh_pct == 50.0f && !a.dew_breach, "77 F, 50 %: a 20 F margin, no breach");
  CHECK(irr_air_inputs(77.0f, 50.0f, 20.5f).dew_breach && !irr_air_inputs(77.0f, 50.0f, 19.5f).dew_breach,
        "the margin is 77 less the dewpoint_avg dew point (about 57 F)");
  CHECK(std::fabs(77.0f - dewpoint_avg(77.0f, 50.0f) - 20.05f) < 0.05f, "dewpoint_avg gives the same margin");
  a = irr_air_inputs(70.0f, 95.0f, 4.0f);
  CHECK(a.dew_margin_known && a.dew_breach, "70 F, 95 %: a 1.5 F margin breaches 4 F");
  for (float t : {40.0f, 65.0f, 95.0f}) {
    for (float rh : {20.0f, 60.0f, 99.0f}) {
      const float margin = t - dewpoint_avg(t, rh);
      CHECK(irr_air_inputs(t, rh, margin + 0.01f).dew_breach && !irr_air_inputs(t, rh, margin - 0.01f).dew_breach,
            "the breach edge sits on the dewpoint_avg margin");
    }
  }
  for (const auto& bad : {std::pair<float, float>{NAN, 50.0f}, {70.0f, NAN}, {70.0f, 0.0f}, {70.0f, 100.5f}}) {
    a = irr_air_inputs(bad.first, bad.second, 4.0f);
    CHECK(std::isnan(a.rh_pct) && !a.dew_margin_known && !a.dew_breach,
          "no measured pair: RH missing (never blocks), dew margin unknown");
  }
  a = irr_air_inputs(70.0f, 100.0f, 0.5f);
  CHECK(a.dew_margin_known && a.dew_breach, "saturated air has no margin");
  CHECK(!irr_air_inputs(70.0f, 60.0f, NAN).dew_breach, "no threshold, no breach");
}

// ── G. The mist path (VD-46, #1161): the D-IRP-19 mister watchdog and the per-zone rules ──────

// The watchdog alone, called once a second as the climate loop calls it on the device. The clock
// starts at 10:00 on day 0; next_midnight is day 1's local midnight (a latch's end).
struct Wd {
  IrrRtcBlock rtc{};
  IrrWatchdog w{};
  IrrWatchdogInputs in{};
  Wd() {
    irr_rtc_validate(rtc, true, kDayNum);
    in.now_ms = 5000000u;
    in.epoch = at(600);
    in.next_midnight = kDay0 + 86400u;
    in.vpd_members = irr_bit(kL04);
    in.engine_line = kNoLine;
    in.vpd_line = kNoLine;
  }
  IrrWatchdogResult step() { return irr_watchdog_step(w, rtc, in); }
  void second() {
    in.now_ms += 1000u;
    if (in.epoch != 0) in.epoch += 1u;
  }
  void on(uint8_t line) {
    in.relay_on = uint16_t(in.relay_on | irr_bit(line));
    in.grant = uint16_t(in.grant | irr_bit(line));
  }
  void off(uint8_t line) {
    in.relay_on = uint16_t(in.relay_on & ~irr_bit(line));
    in.grant = uint16_t(in.grant & ~irr_bit(line));
    if (in.vpd_line == line) in.vpd_line = kNoLine;
    if (in.engine_line == line) in.engine_line = kNoLine;
  }
  // A climate VPD pulse granted now (irr_vpd_grant() carries its commanded length).
  void vpd_pulse(uint8_t line, uint16_t on_s) {
    in.vpd_line = line;
    in.vpd_on_s = on_s;
    in.vpd_since_ms = in.now_ms;
    on(line);
  }
  // An engine open (a window or run-now pulse, a feed pulse, a mist prewet or flush).
  void engine_open(uint8_t line, uint16_t cmd_s) {
    in.engine_line = line;
    in.engine_cmd_s = cmd_s;
    on(line);
  }
  // Seconds from now until a call forces `line` OFF (the relay then reads OFF), or -1.
  int until_forced(uint8_t line, int max_s, IrrWatchdogResult* last = nullptr) {
    for (int s = 0; s <= max_s; ++s) {
      const IrrWatchdogResult r = step();
      if (r.force_off & irr_bit(line)) {
        if (last != nullptr) *last = r;
        off(line);
        return s;
      }
      second();
    }
    return -1;
  }
  // Seconds from now until `zone` stops backing off, or -1.
  int until_backoff_ends(uint8_t zone, int max_s) {
    for (int s = 0; s <= max_s; ++s) {
      second();
      step();
      if (!rtc.fault[zone].backing_off) return s + 1;
    }
    return -1;
  }
  // irr_state as the device publishes it with these faults and v=hoo.
  void state(char* buf, size_t cap) const {
    IrrStateExtras x{};
    std::memcpy(x.vpd_source, "hoo", 3);
    irr_fault_tokens(rtc, x);
    irr_format_state(IrrStateFields{}, x, buf, cap);
  }
  bool rtc_sealed() const { return rtc.crc32 == irr_rtc_crc(rtc); }
};

// Acceptance (F10): a mister held ON beyond its commanded pulse + 30 s is forced OFF. On a VPD
// member the first two faults of the day back the zone off 10 and 30 min and the third latches it
// until clear_fault (or local midnight); each end time is in irr_state.
static void watchdog_backs_a_vpd_member_off_then_latches() {
  Wd d;
  char text[kTextBufLen];
  // A 60 s VPD pulse on L04 that the climate path never closes: OFF at exactly 60 + 30 s.
  d.vpd_pulse(kL04, 60);
  IrrWatchdogResult r{};
  CHECK(d.until_forced(kL04, 400, &r) == 90, "a 60 s VPD pulse is forced OFF at its grant + 90 s");
  CHECK(r.faulted == irr_bit(kL04) && r.changed, "an overrun is a fault");
  CHECK(d.rtc.fault[0].count == 1 && d.rtc.fault[0].backing_off && !d.rtc.fault[0].latched &&
            d.rtc.fault[0].line == kL04 && d.rtc.fault[0].until_epoch == at(600, 90) + 600u && d.rtc_sealed(),
        "the first fault backs center off 10 min, in the RTC block");
  d.state(text, sizeof(text));
  EXPECT_IRR_STATE(text, "f=3,b,1793639490 v=hoo");
  uint16_t backoff = 0, latch = 0;
  irr_fault_masks(d.rtc, backoff, latch);
  CHECK(backoff == irr_bit(kL04) && latch == 0, "the back-off mask names the zone's line");
  CHECK(d.until_backoff_ends(0, 700) == 600, "the back-off ends 600 s after the fault");
  d.state(text, sizeof(text));
  EXPECT_IRR_STATE(text, "v=hoo");
  CHECK(d.rtc.fault[0].count == 1 && d.rtc.fault[0].until_epoch == 0, "the count stays; the end is gone");
  // The second fault: 30 min.
  d.second();
  d.vpd_pulse(kL04, 45);
  CHECK(d.until_forced(kL04, 400) == 75, "a 45 s pulse is forced OFF at + 75 s");
  CHECK(d.rtc.fault[0].count == 2 && d.rtc.fault[0].backing_off &&
            d.rtc.fault[0].backoff_left_ms == kBackoffSecondMs,
        "the second fault backs off 30 min");
  CHECK(d.until_backoff_ends(0, 2000) == 1800, "the second back-off ends after 1800 s");
  // The third fault latches until local midnight.
  d.second();
  d.vpd_pulse(kL04, 30);
  CHECK(d.until_forced(kL04, 400) == 60, "a 30 s pulse is forced OFF at + 60 s");
  CHECK(d.rtc.fault[0].count == 3 && d.rtc.fault[0].latched && !d.rtc.fault[0].backing_off &&
            d.rtc.fault[0].until_epoch == kDay0 + 86400u,
        "the third fault latches center until midnight");
  d.state(text, sizeof(text));
  EXPECT_IRR_STATE(text, "f=3,l,1793689200 v=hoo");
  irr_fault_masks(d.rtc, backoff, latch);
  CHECK(latch == irr_bit(kL04) && backoff == 0, "the latch mask");
  for (int s = 0; s < 3600; ++s) {
    d.second();
    d.step();
  }
  CHECK(d.rtc.fault[0].latched, "a latch does not time out");
  // clear_fault zeroes the zone's count and latch; the next fault backs off 10 min again.
  CHECK(irr_fault_clear(d.rtc, kL04) && d.rtc_sealed(), "clear_fault on L04");
  CHECK(d.rtc.fault[0].count == 0 && !d.rtc.fault[0].latched, "count and latch zeroed");
  d.state(text, sizeof(text));
  EXPECT_IRR_STATE(text, "v=hoo");
  CHECK(!irr_fault_clear(d.rtc, kL04), "nothing left to clear");
  d.vpd_pulse(kL04, 30);
  CHECK(d.until_forced(kL04, 400) == 60 && d.rtc.fault[0].backing_off &&
            d.rtc.fault[0].backoff_left_ms == kBackoffFirstMs,
        "after a clear the next fault backs off 10 min");
  // Local midnight zeroes a latch (irr_rtc_validate() on the engine's tick).
  d.rtc.fault[0] = IrrRtcFault{3, 1, 0, kL04, 0, kDay0 + 86400u};
  irr_rtc_seal(d.rtc);
  irr_rtc_validate(d.rtc, false, kDayNum + 1);
  irr_fault_masks(d.rtc, backoff, latch);
  CHECK(latch == 0 && d.rtc.fault[0].count == 0, "a new local day ends the latch");
}

// Acceptance (F10): a zone that is not a VPD member (schedule-only), and the fertilizer misters L08
// and L10, latch on their first fault; the limit is the engine's commanded length + 30 s, or 300 s
// when no length is known.
static void watchdog_latches_schedule_only_and_fertilizer_misters_at_once() {
  {
    Wd d;  // L07 schedule-only: an engine window pulse of 30 s that stays ON.
    d.engine_open(kL07, 30);
    CHECK(d.until_forced(kL07, 400) == 60, "a 30 s window pulse is forced OFF at + 60 s");
    CHECK(d.rtc.fault[1].count == 1 && d.rtc.fault[1].latched && d.rtc.fault[1].until_epoch == kDay0 + 86400u,
          "a schedule-only zone latches on its first fault");
    char text[kTextBufLen];
    d.state(text, sizeof(text));
    EXPECT_IRR_STATE(text, "f=6,l,1793689200 v=hoo");
  }
  {
    Wd d;  // L08, south's fertilizer mister, latches even though L07 is a VPD member.
    d.in.vpd_members = uint16_t(irr_bit(kL04) | irr_bit(kL07));
    d.engine_open(kL08, 20);
    CHECK(d.until_forced(kL08, 400) == 50, "a 20 s feed pulse is forced OFF at + 50 s");
    CHECK(d.rtc.fault[1].latched && d.rtc.fault[1].line == kL08, "L08 latches the south zone on its first fault");
    uint16_t backoff = 0, latch = 0;
    irr_fault_masks(d.rtc, backoff, latch);
    CHECK(latch == irr_bit(kL08), "the latch names L08");
    IrrProgram p = irr_rom_default();
    enable(p, kL07);
    members(p, kL07, true, false);
    CHECK(!irr_vpd_zone_allowed(p, d.rtc, kL07, IrrVpdZoneGate{true, true, false, backoff, latch}),
          "L07's VPD pulses wait for the south latch");
    char text[kTextBufLen];
    d.state(text, sizeof(text));
    EXPECT_IRR_STATE(text, "f=7,l,1793689200 v=hoo");
    CHECK(irr_fault_clear(d.rtc, kL07) && !d.rtc.fault[1].latched, "clear_fault names either line of the zone");
  }
  {
    Wd d;  // L10 and a mist prewet on L09 of 1800 s: the limit follows the engine's length.
    d.engine_open(kL09, 1800);
    CHECK(d.until_forced(kL09, 2000) == 1830, "a 1800 s mist flush is forced OFF only at + 1830 s");
    CHECK(d.rtc.fault[2].latched, "west is not a VPD member here: it latches");
    d.engine_open(kL10, 15);
    CHECK(d.until_forced(kL10, 400) == 45 && d.rtc.fault[2].count == 2, "L10 counts on the west zone");
  }
  {
    Wd d;  // A mister ON with its grant bit but no known holder: the 300 s ceiling.
    d.on(kL09);
    CHECK(d.until_forced(kL09, 400) == 300, "no commanded length: 300 s");
  }
}

// F10: the grant rail and the edge rules.
static void watchdog_grant_rail_and_edges() {
  {
    // Any wet relay ON without its grant bit goes OFF at once, and it is not a fault.
    Wd d;
    d.in.relay_on = uint16_t(irr_bit(kL01) | irr_bit(kL07) | irr_bit(kL03) | irr_bit(kL11));
    d.in.grant = 0;
    const IrrWatchdogResult r = d.step();
    CHECK(r.force_off == uint16_t(irr_bit(kL01) | irr_bit(kL07) | irr_bit(kL03)) && r.faulted == 0,
          "every ungranted wet relay is forced OFF; fog (L11) is not watched");
    CHECK(d.rtc.fault[0].count == 0 && d.rtc.fault[1].count == 0, "no fault counted");
  }
  {
    // A new VPD grant on a relay the watchdog never saw OFF restarts its clock.
    Wd d;
    d.vpd_pulse(kL04, 60);
    for (int s = 0; s < 50; ++s) {
      d.step();
      d.second();
    }
    d.in.vpd_since_ms = d.in.now_ms;  // regranted without an OFF sample
    CHECK(d.until_forced(kL04, 400) == 90, "the regrant's own 90 s");
  }
  {
    // A drip running for an hour is never a mister fault; only its grant matters.
    Wd d;
    d.on(kL01);
    bool forced = false;
    for (int s = 0; s < 3600; ++s) {
      forced = forced || (d.step().force_off & irr_bit(kL01));
      d.second();
    }
    CHECK(!forced, "a granted drip is never forced OFF");
  }
  {
    // With no valid clock the end time waits for one; millis() wraps safely.
    Wd d;
    d.in.epoch = 0;
    d.in.next_midnight = 0;
    d.in.now_ms = 0xFFFFFFFFu - 30000u;
    d.vpd_pulse(kL04, 30);
    CHECK(d.until_forced(kL04, 400) == 60, "the limit holds across the millis() wrap");
    CHECK(d.rtc.fault[0].backing_off && d.rtc.fault[0].until_epoch == 0, "no clock: no end time yet");
    d.in.epoch = at(700);
    d.in.next_midnight = kDay0 + 86400u;
    d.second();
    const IrrWatchdogResult r = d.step();
    CHECK(r.changed && d.rtc.fault[0].until_epoch == at(700, 1) + 599u,
          "a valid clock gives the remaining back-off its end time");
  }
  {
    // A soft reset: the watchdog's RAM is lost, the RTC block keeps the back-off, and the first
    // call after the boot counts nothing (a reboot's seconds are not counted down).
    Wd d;
    d.vpd_pulse(kL04, 30);
    d.until_forced(kL04, 400);
    for (int s = 0; s < 100; ++s) {
      d.second();
      d.step();
    }
    const uint32_t left = d.rtc.fault[0].backoff_left_ms;
    d.w = IrrWatchdog{};
    d.in.now_ms = 1000u;
    d.step();
    CHECK(d.rtc.fault[0].backoff_left_ms == left && d.rtc.fault[0].backing_off, "a reboot keeps the back-off");
    CHECK(d.until_backoff_ends(0, 700) == int(left / 1000u), "and it runs on from where it was");
  }
}

// F6: irr_vpd_zone_allowed() reads the same masks the engine does, and the effective membership.
static void vpd_zone_allowed_follows_membership_and_faults() {
  IrrRtcBlock rtc{};
  irr_rtc_validate(rtc, true, kDayNum);
  const IrrVpdZoneGate go{true, true, false, 0, 0};
  IrrProgram rom = irr_rom_default();
  CHECK(irr_vpd_zone_allowed(rom, rtc, kL04, go) && !irr_vpd_zone_allowed(rom, rtc, kL07, go) &&
            !irr_vpd_zone_allowed(rom, rtc, kL09, go),
        "the ROM default: center only, as before");
  IrrProgram p = open_program();
  for (uint8_t line : {kL04, kL07, kL09}) CHECK(irr_vpd_zone_allowed(p, rtc, line, go), "every member");
  CHECK(irr_vpd_member_lines(p) == uint16_t(irr_bit(kL04) | irr_bit(kL07) | irr_bit(kL09)), "member lines");
  enable(p, kL09, false);
  CHECK(!irr_vpd_zone_allowed(p, rtc, kL09, go), "a disabled line is not an effective member");
  members(p, kL07, false, true);
  CHECK(!irr_vpd_zone_allowed(p, rtc, kL07, go), "a schedule-only zone takes no VPD pulse");
}

// Acceptance (F6, §4.4): a center window opens L04 once center is a schedule member, and ends
// skipped ("not a schedule member") before that.
static void center_window_needs_schedule_membership() {
  IrrProgram p = irr_rom_default();  // L04: VPD member only
  add_slot(p, 1, kL04, 420, 30, 3, 60);
  Sim s;
  s.epoch = at(419, 55);
  s.boot(true, &p);
  s.run_for(120);
  CHECK(event_is(s.find_event(1, kL04), IrrOutcome::kSkipped, IrrReason::kNsm) && s.open_s[kL04] == 0,
        "07:00: L04 is not a schedule member, the window ends skipped (nsm) and nothing opens");
  IrrProgram q = p;
  members(q, kL04, true, true);
  add_slot(q, 2, kL04, 480, 30, 3, 60);  // 08:00 (slot 1's occurrence today is claimed: skipped)
  s.push(q);
  s.run_until(485);
  CHECK(event_is(s.find_event(2, kL04), IrrOutcome::kOk, IrrReason::kNone) && s.open_s[kL04] >= 90,
        "08:00: with schedule_member on, the window opens L04 and ends ok");
}

// The controller as irrigation_engine.yaml, irrigation_engine_esphome.h, hardware.yaml and the
// climate path of controls.yaml wire it: the engine with its RTC block and NVS (claims and the
// program record), the eleven relays and their guards, irr_grant_mask, the three text sensors,
// the 5 s tick with the 1 s climate loop between ticks, and the services.
struct Dev {
  IrrRtcBlock rtc{};
  Nvs nvs{};
  IrrProgramRecord stored{};
  bool stored_has = false;
  IrrEngine eng{};
  IrrOutputs out{};
  IrrInputs climate = irr_inputs_idle();
  IrrStateExtras extras{};
  IrrStaging staging{};
  uint16_t grant = 0;   // irr_grant_mask
  uint16_t held = 0;
  uint16_t relays = 0;  // bit = device_idx; fog is bit 10
  uint32_t ms = 100000u;
  uint32_t epoch = kDay0;
  bool denver = false;  // derive the offset from America/Denver local fields, as the device does
  int offset = kMst;
  // Published text.
  char program_id[32] = "";
  char state[kTextBufLen] = "";
  char events[kTextBufLen] = "";
  bool state_pub = false;
  bool events_pub = false;
  uint16_t pub_seq = 0;
  uint8_t pub_count = 0;
  int state_publishes = 0;
  // Relay audit.
  int on_edges[kLineCount] = {};
  int refused = 0;         // turn_on attempts the guards refused
  int uncaused = 0;        // ON edges whose line no published irr_state token named first
  int ungranted_held = 0;  // ticks with an engine relay ON and no grant bit
  int zero_start = 0;      // published run tokens with no start time
  bool two_misters = false;
  bool drip_with_mist = false;
  bool l03_alone = false;
  // The climate path of controls.yaml (#1161): one VPD pulse train through
  // irr_vpd_grant()/irr_vpd_release(), held by a drip, confirmed occupancy and SENSOR_FAULT as
  // mister_blocked holds it; fog through the shared force-off (fog_force_off_reason(), F9); the
  // flow meter counted into SAF-4 while any mister or fog relay is ON (F6); and the D-IRP-19
  // mister watchdog each second (irr_mister_watchdog()). Nothing here reads a leak (Q-05).
  bool fog_want = false;
  uint8_t vpd_want = kNoLine;  // the zone it pulses, or with vpd_auto the F6 zone choice
  bool vpd_auto = false;       // irr_rank_vpd_zone() over the zones irr_vpd_zone_allowed() allows
  ZoneWetIntent intents[ZONE_COUNT] = {};
  int ranks[ZONE_COUNT] = {1, 2, 3, 4};  // zone_priority_* (center rank 1)
  bool vpd_stuck = false;      // a fault: the climate path never closes its own pulse
  uint16_t vpd_on_s = 30;
  uint16_t vpd_gap_s = 45;
  uint8_t vpd_on = kNoLine;
  uint32_t vpd_until = 0;
  uint32_t vpd_next = 0;
  int vpd_pulses = 0;
  float gpm = 0.0f;            // the site meter's flow while a mister or fog relay is ON
  float water_carry = 0.0f;
  int fog_s = 0;               // seconds fog (L11) ran
  IrrWatchdog wd{};
  uint16_t faults_seen = 0;    // misters the watchdog faulted
  int forced_off[kLineCount] = {};
  char mist_edges[512] = "";   // the mister ON edges in order: C center, S south, W west, s L08, w L10
  bool started = false;  // the device grants nothing before the engine's first tick

  Dev() { std::memcpy(extras.vpd_source, "hoo", 3); }

  bool named(uint8_t i) const {
    if (i == kL03) return std::strstr(state, ",feed,") != nullptr;  // L03 opens with a feed, which names its line
    char d[8], m[8];
    std::snprintf(d, sizeof(d), "d=%u,", unsigned(i));
    std::snprintf(m, sizeof(m), "m=%u,", unsigned(i));
    for (const char* needle : {static_cast<const char*>(d), static_cast<const char*>(m)}) {
      for (const char* p = std::strstr(state, needle); p != nullptr; p = std::strstr(p + 1, needle)) {
        if (p == state || p[-1] == ' ') return true;
      }
    }
    return false;
  }
  // hardware.yaml: a wet relay turns on only with its bit in irr_grant_mask, L04 never with L03 open.
  void turn_on(uint8_t i) {
    if (relays & irr_bit(i)) return;
    if (i != kL11 && (!(grant & irr_bit(i)) || (i == kL04 && (relays & irr_bit(kL03))))) {
      ++refused;
      return;
    }
    relays = uint16_t(relays | irr_bit(i));
    ++on_edges[i];
    if (i != kL11 && !named(i)) ++uncaused;
    const char* code = i == kL04 ? "C" : i == kL07 ? "S" : i == kL09 ? "W" : i == kL08 ? "s" : i == kL10 ? "w" : "";
    if (code[0] != '\0' && std::strlen(mist_edges) + 1 < sizeof(mist_edges)) std::strcat(mist_edges, code);
  }
  // The fertilizer relays' on_turn_off closes L03 with the last of them.
  void turn_off(uint8_t i) {
    if (!(relays & irr_bit(i))) return;
    relays = uint16_t(relays & ~irr_bit(i));
    const uint16_t fert = uint16_t(irr_bit(kL02) | irr_bit(kL06) | irr_bit(kL08) | irr_bit(kL10));
    if ((irr_bit(i) & fert) && !(relays & fert)) turn_off(kL03);
  }
  void publish() {
    irr_format_program_id(out.program_nvs, out.program_hash8, program_id, sizeof(program_id));
    char s[kTextBufLen];
    irr_format_state(out.state, extras, s, sizeof(s));
    if (!state_pub || std::strcmp(s, state) != 0) {
      std::memcpy(state, s, sizeof(state));
      state_pub = true;
      ++state_publishes;
      // irr_state carries absolute times: with the clock valid (always, in these traces) no
      // d= or m= token goes out with a zero start.
      for (const char* t = s; *t != '\0'; t = std::strchr(t, ' ') ? std::strchr(t, ' ') + 1 : t + std::strlen(t)) {
        unsigned idx = 0, slot = 0;
        char cause[16];
        unsigned long start = 0, end = 0;
        if ((t[0] == 'd' || t[0] == 'm') && t[1] == '=' &&
            std::sscanf(t + 2, "%u,%15[a-z_],%u,%lu,%lu", &idx, cause, &slot, &start, &end) == 5 && start == 0) {
          ++zero_start;
        }
      }
    }
    if (!events_pub || rtc.next_seq != pub_seq || rtc.ring_count != pub_count) {
      irr_format_events(rtc, events, sizeof(events));
      events_pub = true;
      pub_seq = rtc.next_seq;
      pub_count = rtc.ring_count;
    }
  }
  // irrigation_engine_esphome.h esp_apply(): publish, bits up, close (L03 last), open (L03 first), bits down.
  void apply(uint16_t stop_scope = 0) {
    const IrrApply a = irr_apply_plan(grant, held, relays, out, stop_scope);
    publish();
    grant = a.grant_before;
    for (int pass = 0; pass < 2; ++pass) {
      for (uint8_t i = 0; i < kLineCount; ++i) {
        if ((a.turn_off & irr_bit(i)) && (i == kL03) == (pass == 1)) turn_off(i);
      }
    }
    for (int pass = 0; pass < 2; ++pass) {
      for (uint8_t i = 0; i < kLineCount; ++i) {
        if ((a.turn_on & irr_bit(i)) && (i == kL03) == (pass == 0)) turn_on(i);
      }
    }
    grant = a.grant_after;
    held = uint16_t(out.relay_mask & kGrantableMask);
  }
  void store(const IrrProgram& p) {
    pack(p, stored.rec);
    verdify_policy::Sha256::hash(stored.rec, kRecordLen, stored.sha256);
    stored_has = true;
  }
  // irr_esp_start(): the RTC block by the reset reason, the claims, the stored program; every
  // relay off (on_boot) and irr_grant_mask 0.
  void boot(bool power_on) {
    if (power_on) nvs.cache_has = false;
    nvs.cache = nvs.flash;
    nvs.cache_has = nvs.flash_has;
    IrrPersist p;
    p.save = nvs_save;
    p.sync = nvs_sync;
    p.ctx = &nvs;
    irr_engine_init(eng, &rtc, p, power_on, nvs.flash_has ? &nvs.flash : nullptr);
    if (stored_has) {
      IrrProgram prog{};
      IrrReject why;
      if (irr_decode(stored.rec, kRecordLen, stored.sha256, prog, why)) irr_use_program(eng, prog, stored.sha256);
    }
    staging = IrrStaging{};
    out = IrrOutputs{};
    relays = grant = held = 0;
    vpd_on = kNoLine;
    state_pub = events_pub = false;
    started = false;
    wd = IrrWatchdog{};  // RAM: lost on every reboot (the faults themselves are in the RTC block)
    extras.fault_count = 0;
  }
  bool saf4() const { return rtc.water_centigal >= uint32_t(eng.prog.saf4_gal) * 100u; }
  bool soft_budget() const {
    return climate.soft_budget_centigal > 0 && rtc.water_centigal >= climate.soft_budget_centigal &&
           !climate.vpd_emergency;
  }
  // The zone of the next climate VPD pulse: vpd_want, or with vpd_auto the F6 choice of
  // controls.yaml's select_zone_for_pulse() (the fairness override aside): irr_rank_vpd_zone()
  // among the zones irr_vpd_zone_allowed() allows, else the one zone allowed.
  uint8_t vpd_zone() const {
    if (!vpd_auto) return vpd_want;
    IrrVpdZoneGate g{true, true, saf4(), 0, 0};
    irr_fault_masks(rtc, g.backoff_mask, g.latch_mask);
    const uint8_t line_of[3] = {kL04, kL07, kL09};  // ZoneId center, south, west
    bool allowed[ZONE_COUNT] = {};
    for (int z = 0; z < 3; ++z) allowed[z] = irr_vpd_zone_allowed(eng.prog, rtc, line_of[z], g);
    int z = irr_rank_vpd_zone(allowed, intents, ranks);
    for (int k = 2; z < 0 && k >= 0; --k) {
      if (allowed[k]) z = k;
    }
    return z < 0 ? kNoLine : line_of[z];
  }
  // irr_mister_watchdog() (irrigation_engine_esphome.h): the step, the forced OFFs, then the
  // faults into the engine and irr_state.
  uint16_t watchdog() {
    if (!started) return 0;
    const IrrWatchdogResult r = irr_watchdog_step(wd, rtc, irr_watchdog_inputs(eng, out, ms, relays, grant));
    for (uint8_t i = 0; i < kLineCount; ++i) {
      if (!(r.force_off & irr_bit(i)) || !(relays & irr_bit(i))) continue;
      ++forced_off[i];
      turn_off(i);
    }
    if (r.changed || r.faulted != 0) {
      uint16_t backoff = 0, latch = 0;
      irr_fault_masks(rtc, backoff, latch);
      irr_fault_tokens(rtc, extras);
      irr_apply_faults(eng, r.faulted, backoff, latch, ms, out);
      apply();
    }
    faults_seen = uint16_t(faults_seen | r.faulted);
    return r.faulted;
  }
  // irr_command("clear_fault", line, ...).
  void clear_fault(uint8_t line) {
    irr_fault_clear(rtc, line);
    uint16_t backoff = 0, latch = 0;
    irr_fault_masks(rtc, backoff, latch);
    irr_fault_tokens(rtc, extras);
    irr_apply_faults(eng, 0, backoff, latch, ms, out);
    apply();
  }
  // controls.yaml's 1 s loop, in its order: the fog gate (F9), the mister watchdog (F10), the VPD
  // pulse train (F6, mister_blocked), the flow meter (F6).
  void climate_second() {
    ms += 1000u;
    epoch += 1u;
    const bool drip = irr_drip_active(eng);
    const bool budget = soft_budget();
    const Mode mode = climate.sensor_fault ? SENSOR_FAULT : climate.safety_cool ? SAFETY_COOL : SEALED_MIST;
    const FogForceOffInputs rails{climate.sensor_fault,
                                  true,
                                  climate.occupied,
                                  (relays & irr_bit(kL03)) != 0 || out.l03_hold,
                                  (relays & kDripManifoldMask) != 0,
                                  drip,
                                  budget,
                                  saf4()};
    const bool fog = fog_want && !fog_forced_off(mode, rails);
    relays = fog ? uint16_t(relays | kFogMask) : uint16_t(relays & ~kFogMask);
    if (fog) ++fog_s;
    watchdog();
    // The climate's own pulse closed by someone else (a stop, the watchdog): it sees its relay OFF.
    if (vpd_on != kNoLine && !(relays & irr_bit(vpd_on))) {
      vpd_on = kNoLine;
      vpd_next = ms + uint32_t(vpd_gap_s) * 1000u;
    }
    // The VPD pulse closes at its end or when mister_blocked closes it (the soft budget, a drip,
    // confirmed occupancy, SENSOR_FAULT); a stuck pulse never closes on its own.
    const bool vpd_blocked = budget || drip || climate.occupied || climate.sensor_fault;
    if (vpd_on != kNoLine && !vpd_stuck && (irr_reached(ms, vpd_until) || vpd_blocked)) {
      turn_off(vpd_on);
      irr_vpd_release(eng, vpd_on, ms, out);
      apply();
      vpd_on = kNoLine;
      vpd_next = ms + uint32_t(vpd_gap_s) * 1000u;
    }
    // turn_on_zone(): the zone (F6), then the grant; with none the FSM asks again one pulse later.
    const uint8_t zone_line = vpd_zone();
    if (started && vpd_on == kNoLine && zone_line != kNoLine && irr_reached(ms, vpd_next) && !vpd_blocked) {
      const IrrVpdResult r = irr_vpd_request(eng, zone_line, vpd_on_s, ms, out);
      apply();
      if (r.answer == IrrVpdAnswer::kGrant) turn_on(zone_line);
      if (r.answer == IrrVpdAnswer::kGrant && (relays & irr_bit(zone_line))) {
        vpd_on = zone_line;
        vpd_until = ms + uint32_t(vpd_on_s) * 1000u;
        ++vpd_pulses;
      } else {
        vpd_next = ms + uint32_t(vpd_on_s) * 1000u;
      }
    }
    // F6: the flow meter counts into SAF-4 and the soft budget while any mister or fog relay is ON.
    if (relays & (kMistManifoldMask | kFogMask)) irr_count_water(eng, irr_water_centigal(gpm, 1000u, water_carry));
  }
  // The climate loop running while the engine's 5 s tick does not (a stalled engine interval).
  void climate_only(int seconds) {
    for (int k = 0; k < seconds; ++k) climate_second();
  }
  void tick() {
    for (int k = 0; k < 5; ++k) climate_second();
    IrrInputs in = climate;
    in.now_ms = ms;
    int off = offset;
    if (denver) {
      int h, m, s;
      denver_fields(epoch, h, m, s);
      off = irr_utc_offset_min(h, m, s, epoch);
    }
    in.clock = irr_clock_from_epoch(epoch, off);
    in.relay_on_mask = relays;
    // irr_esp_tick(): the watchdog's back-offs and latches from the RTC block, the f= tokens after.
    uint16_t backoff = 0, latch = 0;
    irr_fault_masks(rtc, backoff, latch);
    in.backoff_mask = uint16_t(in.backoff_mask | backoff);
    in.latch_mask = uint16_t(in.latch_mask | latch);
    irr_tick(eng, in, out);
    started = true;
    irr_fault_tokens(rtc, extras);
    apply();
    const uint16_t mist = uint16_t(relays & kMistManifoldMask);
    if (mist & (mist - 1)) two_misters = true;
    if ((relays & kDripManifoldMask) && (relays & (kMistManifoldMask | kFogMask))) drip_with_mist = true;
    if ((relays & irr_bit(kL03)) &&
        !(relays & (irr_bit(kL02) | irr_bit(kL06) | irr_bit(kL08) | irr_bit(kL10)))) {
      l03_alone = true;
    }
    if (relays & held & ~grant) ++ungranted_held;
  }
  void run_until(int minute, int second = 0) {
    const uint32_t target = at(minute, second);
    while (epoch < target) tick();
  }
  void run_until_epoch(uint32_t target) {
    while (epoch < target) tick();
  }
  void run_for_minutes(int minutes) { run_until_epoch(epoch + uint32_t(minutes) * 60u); }
  const IrrEventEntry* last_event() const {
    return rtc.ring_count ? &irr_ring_at(rtc, uint8_t(rtc.ring_count - 1)) : nullptr;
  }
  int count_events(uint16_t slot_id, uint8_t line) const {
    int n = 0;
    for (uint8_t i = 0; i < rtc.ring_count; ++i) {
      const IrrEventEntry& e = irr_ring_at(rtc, i);
      if (e.slot_id == slot_id && e.line == line) ++n;
    }
    return n;
  }
  // irr_command run / stop.
  IrrRunResult run(int32_t line, int32_t on_s, int32_t count = 1, int32_t gap_s = 0, int32_t prewet_s = 0,
                   int32_t flush_s = 0) {
    IrrRunArgs a{};
    IrrRunResult r = IrrRunResult::kBadArgs;
    if (irr_run_args_from(line, on_s, count, gap_s, prewet_s, flush_s, a)) r = irr_run_now(eng, a, ms, out);
    apply();
    return r;
  }
  uint8_t stop(int32_t line) {
    uint16_t scope = 0;
    if (!irr_stop_scope(line, scope)) return 0;
    const uint8_t n = irr_stop(eng, uint8_t(line), ms, out);
    apply(scope);
    if (vpd_on != kNoLine && !(relays & irr_bit(vpd_on))) {  // the climate sees its pulse closed
      vpd_on = kNoLine;
      vpd_next = ms + uint32_t(vpd_gap_s) * 1000u;
    }
    return n;
  }
  // irr_program_begin, _chunk and _commit, then what irr_esp_program_commit() does with the result.
  IrrCommit push(const uint8_t* rec, const char* sha_hex, int32_t len = int32_t(kRecordLen), int skip_chunk = -1) {
    IrrProgram p{};
    IrrReject why = IrrReject::kNone;
    const IrrCommit c = push_hex(staging, rec, len, sha_hex, p, why, skip_chunk);
    if (c == IrrCommit::kRejected) irr_set_reject(eng, why);
    if (c == IrrCommit::kAccepted) {
      stored = staging.record;
      stored_has = true;
      irr_stage_program(eng, p, staging.record.sha256);
    }
    irr_refresh_outputs(eng, ms, out);
    apply();
    return c;
  }
  IrrCommit push_program(const IrrProgram& prog) {
    uint8_t rec[kRecordLen];
    uint8_t sha[32];
    char sha_hex[65];
    pack(prog, rec);
    verdify_policy::Sha256::hash(rec, kRecordLen, sha);
    hex_encode(sha, 32, sha_hex);
    return push(rec, sha_hex);
  }
};

// Acceptance: irr_state is published only on transitions. A 29-minute mist window publishes at
// its start and its end, and at reason changes, never per pulse or per tick.
static void wiring_state_published_on_transitions_only() {
  IrrProgram p = irr_rom_default();
  members(p, kL04, true, true);
  add_slot(p, 1, kL04, 420, 60, 15, 60);  // 07:00: 15 pulses of 60 s, 60 s apart, end 07:29
  {
    Dev d;
    d.store(p);
    d.epoch = at(418);
    d.boot(true);
    d.tick();
    EXPECT_IRR_STATE(d.state, "v=hoo");
    const int base = d.state_publishes;
    d.run_until(420);
    EXPECT_IRR_STATE(d.state, "m=3,schedule,1,1793628000,1793629740 v=hoo");
    d.run_until(428, 30);  // mid-window, mid-pulse
    EXPECT_IRR_STATE(d.state, "m=3,schedule,1,1793628000,1793629740 v=hoo");
    CHECK(d.state_publishes == base + 1 && (d.relays & irr_bit(kL04)), "no publish per pulse or per tick");
    d.run_until(460);
    EXPECT_IRR_STATE(d.state, "v=hoo");
    CHECK(d.state_publishes == base + 2, "the window published twice: at its start and at its end");
    CHECK(d.on_edges[kL04] == 15 && d.refused == 0 && d.uncaused == 0, "15 pulses, each granted, its cause first");
    EXPECT_IRR_EVENTS(d.events, "0,3,1,20759,ok");
  }
  {
    // Someone inside (Frigate) for three minutes: the reason changes publish, the pulses never do.
    Dev d;
    d.store(p);
    d.epoch = at(418);
    d.boot(true);
    d.run_until(420);
    const int base = d.state_publishes;
    d.run_until(430);
    d.climate.occupied = true;
    d.run_until(433);
    EXPECT_IRR_STATE(d.state, "w=3,occ v=hoo");
    CHECK(!(d.relays & irr_bit(kL04)), "confirmed occupancy cuts the pulse");
    d.climate.occupied = false;
    d.run_until(434);
    EXPECT_IRR_STATE(d.state, "m=3,schedule,1,1793628000,1793629740 v=hoo");
    d.run_until(480);
    EXPECT_IRR_STATE(d.state, "v=hoo");
    CHECK(d.state_publishes == base + 3, "publishes at the reason changes and the end only");
    CHECK(d.on_edges[kL04] == 16 && d.refused == 0 && d.uncaused == 0, "the cut pulse is delivered again");
    EXPECT_IRR_EVENTS(d.events, "0,3,1,20759,ok");
  }
  {
    // A gap under mister_min_off_s (gap_s 30 against the 45 s fence): the fence counts from each
    // pulse's own close, so the next tick's relay sample never re-fences the zone. The pulses run
    // 45 s apart (07:00:00, 07:01:15, ..., the 20th at 07:23:45) and irr_state never flips to a
    // w=3,man token between them.
    IrrProgram q = irr_rom_default();
    members(q, kL04, true, true);
    add_slot(q, 1, kL04, 420, 30, 20, 30);
    Dev d;
    d.store(q);
    d.epoch = at(418);
    d.boot(true);
    d.tick();
    const int base = d.state_publishes;
    d.run_until(444, 10);
    CHECK(d.on_edges[kL04] == 20 && (d.relays & irr_bit(kL04)), "07:24:10: the 20th pulse (opened 07:23:45) is open");
    d.run_until(444, 15);
    CHECK(d.on_edges[kL04] == 20 && !(d.relays & irr_bit(kL04)), "20 pulses, 45 s apart, done at 07:24:15");
    EXPECT_IRR_STATE(d.state, "v=hoo");
    EXPECT_IRR_EVENTS(d.events, "0,3,1,20759,ok");
    CHECK(d.state_publishes == base + 2 && d.refused == 0 && d.uncaused == 0,
          "a short-gap window publishes at its start and end only, never per pulse");
  }
}

// A run whose relay cannot open yet is a w= token, never a run token with no start: an L01
// schedule that comes due while a climate VPD pulse holds the mist manifold waits until the
// pulse's end, and opens at the next tick once the climate path closed it.
static void wiring_run_waiting_for_the_manifold_is_a_wait() {
  IrrProgram p = irr_rom_default();
  p.lines[kL01].daily_cap_min = 0;
  add_slot(p, 1, kL01, 420, 300);  // L01 at 07:00 for 5 min
  Dev d;
  d.store(p);
  d.epoch = at(418);
  d.boot(true);
  d.vpd_want = kL04;
  d.vpd_on_s = 60;
  d.vpd_gap_s = 45;
  d.run_until(420);
  EXPECT_IRR_STATE(d.state, "m=3,vpd,0,1793627991,1793628051 w=0,man,1793628051 v=hoo");
  CHECK(!(d.relays & irr_bit(kL01)) && (d.relays & irr_bit(kL04)), "07:00: the VPD pulse holds the water");
  d.run_until(420, 5);
  EXPECT_IRR_STATE(d.state, "d=0,schedule,1,1793628005,1793628305 v=hoo");
  CHECK((d.relays & irr_bit(kL01)) && !(d.relays & irr_bit(kL04)), "07:00:05: the drip opens");
  d.run_until(430);
  CHECK(d.zero_start == 0 && d.refused == 0 && d.uncaused == 0, "no run token without its start");
  EXPECT_IRR_EVENTS(d.events, "0,0,1,20759,ok");
}

// Acceptance: irr_program_id reads src=rom with no stored program and src=nvs with the record's
// hash after a committed push; a commit with a bad hash keeps the old program and id and shows the
// reject token in irr_state.
static void wiring_program_id_rom_then_nvs() {
  Dev d;
  d.epoch = at(600);
  d.boot(true);
  d.tick();
  EXPECT_IRR_PROGRAM_ID(d.program_id, "h=0000000000000000 src=rom");
  EXPECT_IRR_STATE(d.state, "v=hoo");
  char seed[65], full[65];
  hex_encode(kGoldenVectorSha256[1], 32, seed);
  hex_encode(kGoldenVectorSha256[2], 32, full);
  CHECK(d.push(kGoldenVectorBytes[1], seed) == IrrCommit::kAccepted, "the seed program commits");
  CHECK(d.stored_has && std::memcmp(d.stored.rec, kGoldenVectorBytes[1], kRecordLen) == 0 &&
            std::memcmp(d.stored.sha256, kGoldenVectorSha256[1], 32) == 0,
        "saved to NVS with its hash before it runs");
  EXPECT_IRR_PROGRAM_ID(d.program_id, "h=0000000000000000 src=rom");  // it runs from the next tick
  d.tick();
  EXPECT_IRR_PROGRAM_ID(d.program_id, "h=7ae03efe564082b7 src=nvs");
  // The seed's degraded_only window at solar noon -120 is due and waits: VPD sensing is healthy.
  EXPECT_IRR_STATE(d.state, "w=3,vok v=hoo");
  // A hash that does not match: the old program and id stay, irr_state carries the reject.
  char bad[65];
  std::memcpy(bad, full, sizeof(bad));
  bad[0] = bad[0] == '0' ? '1' : '0';
  CHECK(d.push(kGoldenVectorBytes[2], bad) == IrrCommit::kRejected, "a bad hash is rejected");
  EXPECT_IRR_STATE(d.state, "w=3,vok v=hoo j=bad_hash");
  d.tick();
  EXPECT_IRR_PROGRAM_ID(d.program_id, "h=7ae03efe564082b7 src=nvs");
  EXPECT_IRR_STATE(d.state, "w=3,vok v=hoo j=bad_hash");
  CHECK(std::memcmp(d.stored.rec, kGoldenVectorBytes[1], kRecordLen) == 0, "NVS keeps the old record");
  CHECK(d.push(kGoldenVectorBytes[2], full, 583) == IrrCommit::kRejected, "len 583");
  EXPECT_IRR_STATE(d.state, "w=3,vok v=hoo j=bad_length");
  CHECK(d.push(kGoldenVectorBytes[2], full, int32_t(kRecordLen), 0) == IrrCommit::kRejected, "no first chunk");
  EXPECT_IRR_STATE(d.state, "w=3,vok v=hoo j=bad_magic");
  // A good push clears the reject and runs from the next tick.
  CHECK(d.push(kGoldenVectorBytes[2], full) == IrrCommit::kAccepted, "the full program commits");
  CHECK(std::strstr(d.state, "j=") == nullptr, "a committed push clears the reject");
  d.tick();
  EXPECT_IRR_PROGRAM_ID(d.program_id, "h=3b76a2316e2f39d8 src=nvs");
  // A soft reset and a power cut both run the stored program again.
  d.boot(false);
  d.tick();
  EXPECT_IRR_PROGRAM_ID(d.program_id, "h=3b76a2316e2f39d8 src=nvs");
  d.boot(true);
  d.tick();
  EXPECT_IRR_PROGRAM_ID(d.program_id, "h=3b76a2316e2f39d8 src=nvs");
  // A stored record that no longer matches its hash runs the ROM default, and is pushed again.
  d.stored.rec[200] ^= 0x01;
  d.boot(true);
  d.tick();
  EXPECT_IRR_PROGRAM_ID(d.program_id, "h=0000000000000000 src=rom");
}

// A degraded_only window (D-IRP-12) runs while VPD sensing is degraded and is published with cause
// fallback, which is how the platform reads that the fallback is active.
static void wiring_fallback_run_is_published_as_fallback() {
  Dev d;
  d.epoch = at(600);
  d.boot(true);
  d.tick();
  char seed[65];
  hex_encode(kGoldenVectorSha256[1], 32, seed);
  CHECK(d.push(kGoldenVectorBytes[1], seed) == IrrCommit::kAccepted, "the seed program");
  d.tick();
  EXPECT_IRR_STATE(d.state, "w=3,vok v=hoo");
  d.climate.sensor_degraded = true;
  d.tick();
  EXPECT_IRR_STATE(d.state, "m=3,fallback,1,1793638815,1793639175 v=hoo");
  CHECK((d.relays & irr_bit(kL04)) && d.refused == 0 && d.uncaused == 0, "the fallback pulse opens, granted");
  d.run_for_minutes(10);
  EXPECT_IRR_STATE(d.state, "v=hoo");
  EXPECT_IRR_EVENTS(d.events, "0,3,1,20759,ok");
}

// Acceptance (the replay suite): no wet relay turns on without the engine grant, through a day
// with every line kind, VPD demand on center, fog, occupancy, SENSOR_FAULT, SAFETY_COOL, a VPD
// emergency, run-now, stops and a push in the middle.
static void wiring_no_relay_without_the_grant() {
  IrrProgram p = open_program();
  add_slot(p, 1, kL01, 360, 900);                    // 06:00 L01 15 min
  add_slot(p, 2, kL02, 400, 300, 1, 0, 120, 240);    // 06:40 L02 feed: prewet 2, feed 5, flush 4 min
  add_slot(p, 3, kL04, 480, 45, 8, 60);              // 08:00 L04 window
  add_slot(p, 4, kL07, 540, 30, 6, 90);              // 09:00 L07 window (occupied 09:00-10:00)
  add_slot(p, 5, kL08, 600, 20, 4, 60, 60, 120);     // 10:00 L08 feed, prewet and flush on L07
  add_slot(p, 6, kL05, 660, 600);                    // 11:00 L05 10 min
  add_slot(p, 7, kL06, 720, 300, 1, 0, 0, 300);      // 12:00 L06 feed, flush on L05
  add_slot(p, 8, kL09, 780, 40, 5, 60);              // 13:00 L09 window
  add_slot(p, 9, kL10, 840, 15, 3, 45, 0, 60);       // 14:00 L10 feed, flush on L09
  add_slot(p, 10, kL01, 900, 1800);                  // 15:00 L01 30 min under SAFETY_COOL and demand
  Dev d;
  d.store(p);
  d.epoch = kDay0;
  d.boot(true);
  d.vpd_want = kL04;
  d.vpd_on_s = 45;
  d.vpd_gap_s = 60;
  for (int h = 0; h < 24; ++h) {
    d.climate.occupied = (h == 9);
    d.climate.safety_cool = (h == 15);
    d.climate.vpd_emergency = (h == 16);
    d.climate.climate_demand = (h >= 10 && h < 18);
    d.fog_want = (h >= 12 && h < 17);
    for (int quarter = 0; quarter < 4; ++quarter) {
      d.climate.sensor_fault = (h == 14 && quarter == 2);
      if (h == 5 && quarter == 0) CHECK(d.run(kL01, 1800) == IrrRunResult::kStarted, "05:00 run L01 for 30 min");
      if (h == 5 && quarter == 1) CHECK(d.stop(kAllLines) >= 1 && !(d.relays & kGrantableMask), "05:15 stop all");
      if (h == 11 && quarter == 2) d.run(kL04, 30, 3, 60);
      if (h == 13 && quarter == 2) d.run(kL02, 120, 1, 0, 60, 60);
      if (h == 16 && quarter == 2) {
        d.stop(kAllLines);
        CHECK(!(d.relays & kGrantableMask) && d.grant == 0, "16:30 stop all: every wet relay off, every bit clear");
      }
      if (h == 17 && quarter == 0) {
        IrrProgram q = p;
        enable(q, kL07, false);
        CHECK(d.push_program(q) == IrrCommit::kAccepted, "17:00 a push disables L07");
      }
      d.run_for_minutes(15);
    }
  }
  CHECK(d.refused == 0, "no wet relay turned on without its grant bit");
  CHECK(d.uncaused == 0, "every ON edge had its cause in irr_state first");
  CHECK(d.zero_start == 0, "every published run token carried its start time");
  CHECK(d.ungranted_held == 0, "no engine relay stayed on without its bit");
  CHECK(!d.two_misters && !d.drip_with_mist && !d.l03_alone, "manifold rules");
  for (uint8_t line : {kL01, kL02, kL03, kL04, kL05, kL06, kL07, kL08, kL09, kL10}) {
    char label[48];
    std::snprintf(label, sizeof(label), "L%02u opened during the day", unsigned(line + 1));
    CHECK(d.on_edges[line] > 0, label);
  }
  CHECK(d.vpd_pulses > 20, "center VPD misting carried on");
}

// Acceptance (Q-05, F14; #1161): water flowing with every Verdify valve closed (the hose, sink or
// hydro on the one site meter, leak_detected) changes nothing. No input of the engine, of this
// device model's climate side or of the fog force-off reads it (IrrInputs has no leak field, the
// static_assert above; FogForceOffInputs has none, test_greenhouse_logic.cpp), so with fog and
// center VPD demand up the whole time, as on a hot afternoon with the hose running, a running L02
// feed (prewet, feed with L03, flush) and the 07:00 L01 schedule take the water in turn, fog and
// the VPD pulses carry on around them, and the drips never overlap mist or fog.
static void wiring_leak_changes_nothing_in_irrigation() {
  IrrProgram p = irr_rom_default();
  enable(p, kL02);
  p.lines[kL01].daily_cap_min = 0;
  add_slot(p, 1, kL01, 420, 600);  // L01 at 07:00 for 10 min
  Dev d;
  d.store(p);
  d.epoch = at(405);
  d.boot(true);
  d.vpd_want = kL04;
  d.fog_want = true;
  d.run_until(410);
  const int vpd_before = d.vpd_pulses;
  const int fog_before = d.fog_s;
  CHECK(vpd_before > 0 && (d.relays & kFogMask), "06:50: fog and center VPD pulses run");
  CHECK(d.run(kL02, 300, 1, 0, 120, 240) == IrrRunResult::kStarted, "06:50 an L02 feed starts");
  uint16_t seq[64];
  int n = 0;
  uint16_t last = 0;
  if (d.relays & kDripManifoldMask) seq[n++] = last = uint16_t(d.relays & (kDripManifoldMask | irr_bit(kL03)));
  while (d.epoch < at(450)) {
    d.tick();
    const uint16_t drips = uint16_t(d.relays & (kDripManifoldMask | irr_bit(kL03)));
    if (drips != last && n < 64 && (n > 0 || drips != 0)) seq[n++] = drips;
    last = drips;
    // Fog was on when the feed was asked for, so its prewet opens at the next tick, once the
    // drip's water conflict has turned fog off; each stage names its relay before it opens.
    if (d.epoch == at(411)) EXPECT_IRR_STATE(d.state, "d=0,prewet,0,1793627405,1793627525 v=hoo");
    if (d.epoch == at(415)) EXPECT_IRR_STATE(d.state, "d=1,feed,0,1793627525,1793627825 v=hoo");
    if (d.epoch == at(420, 30)) {
      EXPECT_IRR_STATE(d.state, "d=0,flush,0,1793627830,1793628070 w=0,man,1793628070 v=hoo");
    }
    if (d.epoch == at(425)) EXPECT_IRR_STATE(d.state, "d=0,schedule,1,1793628070,1793628670 v=hoo");
  }
  const uint16_t prewet = irr_bit(kL01), feed = uint16_t(irr_bit(kL02) | irr_bit(kL03));
  // The flush hands L01 straight to the 07:00 schedule that waited for it (one relay edge).
  const uint16_t expect[] = {prewet, feed, 0, prewet, 0};
  CHECK(n == 5 && std::memcmp(seq, expect, sizeof(expect)) == 0,
        "prewet, feed with L03, the flush, then the 07:00 L01 schedule");
  EXPECT_IRR_EVENTS(d.events, "0,1,0,20759,ok 1,0,1,20759,ok");
  CHECK(d.vpd_pulses > vpd_before && d.fog_s > fog_before && (d.relays & kFogMask),
        "fog and center VPD misting carried on around the drips");
  CHECK(d.refused == 0 && d.uncaused == 0 && !d.drip_with_mist && !d.l03_alone, "granted, with causes, no overlap");
  CHECK(std::strstr(d.state, "leak") == nullptr, "no irr_state token names a leak");
}

// Acceptance: irr_command("stop", 255, ...) turns every open wet relay off inside the service call;
// a second run on a running line is refused line_busy and starts nothing.
static void wiring_stop_and_run_now_commands() {
  IrrProgram p = open_program();
  {
    // A climate VPD pulse on L04: stop 255 closes it in the call; VPD may pulse again afterwards.
    Dev d;
    d.store(p);
    d.epoch = at(600);
    d.boot(true);
    d.vpd_want = kL04;
    d.vpd_on_s = 60;
    while (!(d.relays & irr_bit(kL04))) d.tick();
    EXPECT_IRR_STATE(d.state, "m=3,vpd,0,1793638806,1793638866 v=hoo");
    CHECK(d.stop(kAllLines) == 1 && !(d.relays & kGrantableMask) && d.grant == 0, "stop 255 closes the VPD pulse now");
    EXPECT_IRR_STATE(d.state, "v=hoo");
    const int pulses = d.vpd_pulses;
    d.run_until(605);
    CHECK(d.vpd_pulses > pulses, "VPD misting pulses again after a stop (a stop disables nothing)");
  }
  {
    // An L02 feed with L03 open: stop 255 closes both in the call; the flush follows on L01.
    Dev d;
    d.store(p);
    d.epoch = at(600);
    d.boot(true);
    d.tick();
    CHECK(d.run(kL02, 300, 1, 0, 0, 120) == IrrRunResult::kStarted, "an L02 feed");
    EXPECT_IRR_STATE(d.state, "d=1,feed,0,1793638805,1793639105 v=hoo");
    CHECK((d.relays & irr_bit(kL02)) && (d.relays & irr_bit(kL03)), "L02 and L03 open");
    CHECK(d.run(kL02, 300, 1, 0, 0, 120) == IrrRunResult::kLineBusy && d.rtc.ring_count == 0,
          "a repeated send is line_busy: nothing starts, no outcome");
    CHECK(d.run(kL01, 300) == IrrRunResult::kManifoldBusy && d.rtc.ring_count == 1,
          "another drip is manifold_busy, which is an outcome");
    d.run_for_minutes(1);
    CHECK(d.stop(kAllLines) == 1 && !(d.relays & kGrantableMask), "stop 255 closes L02 and L03 in the call");
    CHECK(d.grant == 0, "their grant bits are cleared");
    d.tick();
    EXPECT_IRR_EVENTS(d.events, "0,0,0,20759,sk,man 1,1,0,20759,st,cmd");
    d.tick();
    CHECK(d.relays & irr_bit(kL01), "the clean flush follows the stopped feed (D-IRP-15)");
    CHECK(d.stop(kL01) == 1 && !(d.relays & irr_bit(kL01)), "stopping the flush closes L01");
  }
  {
    // Run-now on L01 twice; stop on one line; stop on fog does nothing.
    Dev d;
    d.store(p);
    d.epoch = at(600);
    d.boot(true);
    d.tick();
    CHECK(d.run(kL01, 600) == IrrRunResult::kStarted && (d.relays & irr_bit(kL01)), "L01 runs now");
    EXPECT_IRR_STATE(d.state, "d=0,run_now,0,1793638805,1793639405 v=hoo");
    const uint16_t seq_before = d.rtc.next_seq;
    CHECK(d.run(kL01, 600) == IrrRunResult::kLineBusy && d.rtc.next_seq == seq_before && d.on_edges[kL01] == 1,
          "a second run on the running line: line_busy, nothing started, no outcome");
    CHECK(d.run(kL04, 91, 1, 30) == IrrRunResult::kBadArgs, "a mister pulse past 90 s is bad_args (BOUNDS_BY_KIND)");
    CHECK(d.stop(kL11) == 0 && (d.relays & irr_bit(kL01)), "stop on fog stops nothing");
    CHECK(d.stop(kL05) == 0 && (d.relays & irr_bit(kL01)), "stop on an idle line stops nothing");
    CHECK(d.stop(kL01) == 1 && !(d.relays & irr_bit(kL01)) && d.grant == 0, "stop L01");
    EXPECT_IRR_EVENTS(d.events, "0,0,0,20759,st,cmd");
  }
}

// Local time through the device clock (ESPTime's local fields and UTC epoch, irr_utc_offset_min):
// the rules of VD-37's compiler. A start in the spring-forward gap runs at the jump, a repeated
// fall-back time runs once, and a solar start past midnight runs the next morning as the
// occurrence of its schedule day.
static void wiring_local_time_rules() {
  {
    IrrProgram p = irr_rom_default();
    p.lines[kL01].daily_cap_min = 0;
    add_slot(p, 1, kL01, 150, 300);  // 02:30, every day
    Dev d;
    d.denver = true;
    d.store(p);
    d.epoch = 1772960400u - 1800u;  // 2026-03-08 01:30 MST
    d.boot(true);
    d.run_until_epoch(1772960400u - 5u);
    CHECK(d.on_edges[kL01] == 0, "01:59:55 MST: not yet");
    d.run_until_epoch(1772960400u);
    CHECK((d.relays & irr_bit(kL01)) && d.on_edges[kL01] == 1, "02:30 in the skipped hour opens at the 03:00 jump");
    d.run_until_epoch(1772960400u + 3600u);
    CHECK(d.on_edges[kL01] == 1 && d.count_events(1, kL01) == 1, "and runs once");
  }
  {
    IrrProgram p = irr_rom_default();
    p.lines[kL01].daily_cap_min = 0;
    add_slot(p, 1, kL01, 90, 300);  // 01:30, every day
    Dev d;
    d.denver = true;
    d.store(p);
    d.epoch = 1793518200u - 600u;  // 2026-11-01 01:20 MDT
    d.boot(true);
    d.run_until_epoch(1793521800u + 3600u);  // past the second 01:30 (MST)
    CHECK(d.on_edges[kL01] == 1 && d.count_events(1, kL01) == 1, "the repeated 01:30 runs once");
  }
  {
    // Sunset + 240 in late June lands after midnight: it runs that early morning as the occurrence
    // of its own (previous) day and weekday.
    const uint32_t evening = 1782442800u;  // 2026-06-25 21:00 MDT
    int h, m, s;
    denver_fields(evening, h, m, s);
    const IrrClock c = irr_clock_from_epoch(evening, denver_offset(evening));
    IrrProgram p = irr_rom_default();
    p.lines[kL01].daily_cap_min = 0;
    add_slot(p, 1, kL01, 240, 300, 1, 0, 0, 0, false, uint8_t(IrrStartKind::kSunset), uint8_t(1u << c.weekday));
    Dev d;
    d.denver = true;
    d.store(p);
    d.epoch = evening;
    d.boot(true);
    d.run_until_epoch(evening + 9u * 3600u);
    const IrrEventEntry* e = d.last_event();
    CHECK(d.on_edges[kL01] == 1 && e != nullptr && e->slot_id == 1 && e->local_day == c.local_day &&
              e->outcome == uint8_t(IrrOutcome::kOk),
          "a June sunset + 240 runs after midnight under its schedule day");
    CHECK(h == 21 && m == 0, "the trace starts at 21:00 MDT");
  }
}

// ── The mist path on the device model (VD-46, #1161) ─────────────────────────

static ZoneWetIntent intent_at(float vpd) { return zone_wet_intent(vpd, ZoneBand{1.0f, 0.8f, 1.2f}); }

// Acceptance (F6): with L04, L07 and L09 enabled VPD members, all wanting water and none past the
// 10 min fairness window, the rank arbiter picks the zone for every VPD pulse: center at rank 1,
// although south's VPD is furthest above its band. No switch is read (sw_arbiter_zone_enabled is
// retired). With center latched by the watchdog, south (rank 2) takes the pulses.
static void wiring_arbiter_picks_the_zone() {
  IrrProgram p = irr_rom_default();
  for (uint8_t line : {kL04, kL07, kL09}) {
    enable(p, line);
    members(p, line, true, false);
  }
  Dev d;
  d.store(p);
  d.epoch = at(600);
  d.boot(true);
  d.vpd_auto = true;
  for (ZoneWetIntent& z : d.intents) z = intent_at(1.5f);
  d.intents[ZONE_SOUTH] = intent_at(1.9f);
  d.vpd_on_s = 45;
  d.vpd_gap_s = 45;
  d.run_until(630);
  CHECK(d.vpd_pulses >= 15 && d.on_edges[kL04] == d.vpd_pulses && d.on_edges[kL07] == 0 && d.on_edges[kL09] == 0,
        "every VPD pulse on center (rank 1)");
  // Center latched (as a third watchdog fault of the day leaves it): south, rank 2.
  d.rtc.fault[0] = IrrRtcFault{3, 1, 0, kL04, 0, kDay0 + 86400u};
  irr_rtc_seal(d.rtc);
  const int center = d.on_edges[kL04];
  d.run_until(660);
  CHECK(d.on_edges[kL04] <= center + 1 && d.on_edges[kL07] >= 15 && d.on_edges[kL09] == 0,
        "with center latched every pulse goes to south");
  EXPECT_IRR_STATE(d.state, "f=3,l,1793689200 v=hoo");
  CHECK(d.refused == 0 && d.uncaused == 0 && !d.two_misters, "granted, with causes, one mister at a time");
}

// Acceptance (F6, D-IRP-05): a scheduled pulse and a VPD pulse both pending alternate at the pulse
// boundary. L07's window (Schedule on) and center's VPD pulses share the manifold: from the
// window's first pulse to its last, no two window pulses run without a VPD pulse between them.
static void wiring_vpd_and_schedule_alternate_at_the_boundary() {
  IrrProgram p = irr_rom_default();
  enable(p, kL07);
  members(p, kL07, false, true);
  add_slot(p, 1, kL07, 600, 30, 6, 30);  // 10:00, six 30 s pulses
  Dev d;
  d.store(p);
  d.epoch = at(590);
  d.boot(true);
  d.vpd_want = kL04;
  d.vpd_on_s = 30;
  d.vpd_gap_s = 30;
  d.run_until(630);
  const char* first = std::strchr(d.mist_edges, 'S');
  const char* last = std::strrchr(d.mist_edges, 'S');
  CHECK(first != nullptr && d.on_edges[kL07] == 6, "the window delivered its six pulses");
  bool alternates = true;
  for (const char* c = first; c != nullptr && c < last; ++c) {
    if (c[0] == 'S' && c[1] == 'S') alternates = false;
  }
  CHECK(alternates, "no two window pulses without a VPD pulse between them");
  CHECK(d.count_events(1, kL07) == 1 && event_is(d.last_event(), IrrOutcome::kOk, IrrReason::kNone),
        "the window ends ok");
  CHECK(!d.two_misters && d.refused == 0 && d.uncaused == 0, "one mister relay at a time, granted");
}

// Acceptance (F6, F11): L07 enabled with Schedule on and VPD control off, no humidity demand: its
// window opens L07, and the pulses add to the south duty minutes and to the SAF-4 count (the site
// meter's water while a mister relay is ON, whatever opened it).
static void wiring_schedule_only_window_counts_duty_and_saf4() {
  IrrProgram p = irr_rom_default();
  enable(p, kL07);
  members(p, kL07, false, true);
  add_slot(p, 1, kL07, 600, 40, 5, 60);  // 10:00, five 40 s pulses
  Dev d;
  d.store(p);
  d.epoch = at(595);
  d.boot(true);
  d.gpm = 1.2f;  // 2 centi-gallons a second
  d.run_until(600, 5);
  EXPECT_IRR_STATE(d.state, "m=6,schedule,1,1793638800,1793639240 v=hoo");
  d.run_until(620);
  CHECK(d.on_edges[kL07] == 5 && d.vpd_pulses == 0, "five pulses open L07 with no VPD demand");
  CHECK(d.rtc.zone_pulse_s[1] == 200 && d.rtc.line_on_s[kL07] == 200, "200 s on the south duty minutes");
  CHECK(d.rtc.water_centigal == 400, "4 gal into the SAF-4 count");
  CHECK(event_is(d.last_event(), IrrOutcome::kOk, IrrReason::kNone), "the window ends ok");
}

// Acceptance (F7, D-IRP-10): with occupancy unknown (no confirmed detection) VPD and scheduled mist
// run; with Frigate confirming someone inside both wait, and the drips still run.
static void wiring_occupancy_holds_every_mist_but_not_drips() {
  IrrProgram p = irr_rom_default();
  enable(p, kL07);
  members(p, kL07, false, true);
  p.lines[kL01].daily_cap_min = 0;
  add_slot(p, 1, kL07, 590, 30, 3, 60);   // 09:50 L07 window
  add_slot(p, 2, kL07, 605, 30, 3, 60);   // 10:05 L07 window
  add_slot(p, 3, kL01, 610, 300);         // 10:10 L01 for 5 min
  Dev d;
  d.store(p);
  d.epoch = at(585);
  d.boot(true);
  d.vpd_want = kL04;
  d.vpd_on_s = 30;
  d.vpd_gap_s = 30;
  d.run_until(600);
  CHECK(d.on_edges[kL07] == 3 && d.vpd_pulses >= 4 && d.count_events(1, kL07) == 1,
        "occupancy unknown: the 09:50 window and the VPD pulses run");
  d.climate.occupied = true;  // Frigate confirms someone inside
  const int vpd = d.vpd_pulses;
  d.run_until(607);
  EXPECT_IRR_STATE(d.state, "w=6,occ v=hoo");
  d.run_until(612);
  EXPECT_IRR_STATE(d.state, "d=0,schedule,3,1793639400,1793639700 w=6,man,1793639700 v=hoo");
  CHECK((d.relays & irr_bit(kL01)) && !(d.relays & kMistManifoldMask), "10:12: the drip runs, no mister");
  d.run_until(620);
  CHECK(d.vpd_pulses == vpd && d.on_edges[kL07] == 3, "confirmed occupancy: no VPD or window pulse");
  CHECK(d.count_events(3, kL01) == 1, "the drip ran to its end");
  d.climate.occupied = false;
  d.run_until(640);
  CHECK(d.on_edges[kL07] == 6 && d.vpd_pulses > vpd, "once it clears, the window and VPD resume");
  CHECK(!d.drip_with_mist && d.refused == 0 && d.uncaused == 0, "no overlap, granted");
}

// Acceptance (F9, D-IRP-04, Q-05): survival fog runs in SAFETY_COOL past the SAF-4 ceiling and the
// soft budget, and stays OFF with L03 open or with confirmed occupancy. Outside SAFETY_COOL the same
// caps hold it.
static void wiring_survival_fog_runs_past_the_caps() {
  IrrProgram p = irr_rom_default();
  enable(p, kL08);
  enable(p, kL07);
  Dev d;
  d.store(p);
  d.epoch = at(780);
  d.boot(true);
  d.fog_want = true;
  d.climate.soft_budget_centigal = 30000;     // 300 gal
  d.rtc.water_centigal = 36000;               // the SAF-4 ceiling (360 gal) and the budget reached
  irr_rtc_seal(d.rtc);
  d.tick();
  CHECK(!(d.relays & kFogMask), "13:00 SEALED: SAF-4 and the soft budget hold fog");
  d.climate.safety_cool = true;
  d.tick();
  CHECK((d.relays & kFogMask) && d.fog_s > 0, "SAFETY_COOL: survival fog runs past both");
  d.gpm = 0.3f;
  const uint32_t before = d.rtc.water_centigal;
  d.run_for_minutes(2);
  CHECK(d.rtc.water_centigal > before, "its water still counts (D-IRP-04)");
  d.climate.occupied = true;
  d.tick();
  CHECK(!(d.relays & kFogMask), "confirmed occupancy holds survival fog");
  d.climate.occupied = false;
  d.tick();
  CHECK(d.relays & kFogMask, "it resumes");
  // L03 open with a feed: SAFETY_COOL ends a feed at once, so the L08 run-now is refused; a feed
  // already running when the house is fine holds fog (SAF-5).
  d.climate.safety_cool = false;
  d.rtc.water_centigal = 0;
  irr_rtc_seal(d.rtc);
  d.tick();
  CHECK(d.run(kL08, 20, 2, 60) == IrrRunResult::kStarted, "an L08 feed");
  d.climate.safety_cool = true;
  bool fog_with_l03 = false;
  for (int k = 0; k < 24; ++k) {
    d.tick();
    if ((d.relays & kFogMask) && (d.relays & irr_bit(kL03))) fog_with_l03 = true;
  }
  CHECK(!fog_with_l03, "fog never runs with L03 open (SAF-5)");
  CHECK(d.relays & kFogMask, "survival fog runs once the feed has ended");
}

// Acceptance (F10, D-IRP-19) end to end: a center VPD pulse the climate path never closes is forced
// OFF at its commanded length + 30 s, center backs off 10 min (irr_state carries the end) and VPD
// misting resumes after it; a schedule-only zone whose window pulse overruns (the engine's tick
// stalled) latches on its first fault, run-now there is refused line_faulted, and clear_fault
// frees it.
static void wiring_watchdog_backs_off_then_resumes_and_latches() {
  IrrProgram p = irr_rom_default();
  enable(p, kL07);
  members(p, kL07, false, true);
  add_slot(p, 1, kL07, 660, 40, 4, 60);  // 11:00 L07 window
  Dev d;
  d.store(p);
  d.epoch = at(600);
  d.boot(true);
  d.vpd_want = kL04;
  d.vpd_on_s = 60;
  d.vpd_gap_s = 45;
  d.vpd_stuck = true;
  while (!(d.relays & irr_bit(kL04))) d.tick();
  EXPECT_IRR_STATE(d.state, "m=3,vpd,0,1793638806,1793638866 v=hoo");
  while (d.relays & irr_bit(kL04)) d.climate_only(1);
  CHECK(d.epoch == 1793638806u + 90u, "the stuck 60 s pulse is forced OFF at its grant + 90 s");
  EXPECT_IRR_STATE(d.state, "f=3,b,1793639496 v=hoo");
  CHECK(d.forced_off[kL04] == 1 && d.faults_seen == irr_bit(kL04), "forced OFF by the watchdog, a fault");
  d.vpd_stuck = false;
  const int pulses = d.vpd_pulses;
  d.run_until_epoch(1793639496u - 5u);
  CHECK(d.vpd_pulses == pulses && d.rtc.fault[0].backing_off, "center backs off: no VPD pulse for 10 min");
  d.run_for_minutes(2);
  CHECK(d.vpd_pulses > pulses && std::strstr(d.state, "f=") == nullptr, "after 10 min VPD misting resumes");
  CHECK(d.rtc.fault[0].count == 1, "the day's count stays at one");
  // The engine's 5 s tick stalls with L07's window pulse open; the climate loop's watchdog closes
  // it at 40 + 30 s, and the schedule-only zone latches on this first fault.
  d.vpd_want = kNoLine;
  d.run_until(660, 5);
  CHECK(d.relays & irr_bit(kL07), "11:00:05 the window pulse is open");
  d.climate_only(80);
  CHECK(!(d.relays & irr_bit(kL07)) && d.forced_off[kL07] == 1 && d.rtc.fault[1].latched,
        "the overrun pulse is forced OFF and the schedule-only zone latches");
  EXPECT_IRR_STATE(d.state, "w=6,flt f=6,l,1793689200 v=hoo");
  // Nothing reopens L07 while it is latched; the window ends short at its deadline.
  d.run_until(730);
  CHECK(d.on_edges[kL07] == 1, "no pulse while latched");
  CHECK(event_is(d.last_event(), IrrOutcome::kShort, IrrReason::kFlt), "the window ends short (flt)");
  CHECK(d.run(kL07, 30, 2, 60) == IrrRunResult::kLineFaulted, "run-now on the latched zone: line_faulted");
  CHECK(event_is(d.last_event(), IrrOutcome::kSkipped, IrrReason::kFlt), "the refusal is an outcome");
  d.clear_fault(kL07);
  CHECK(!d.rtc.fault[1].latched && d.rtc.fault[1].count == 0 && std::strstr(d.state, "f=") == nullptr,
        "clear_fault frees the zone and clears f=");
  CHECK(d.run(kL07, 30, 2, 60) == IrrRunResult::kStarted && (d.relays & irr_bit(kL07)), "run-now runs again");
  CHECK(d.refused == 0 && d.uncaused == 0 && !d.two_misters, "granted, with causes");
}

// F10: a wet relay ON without its grant bit (nothing the engine or the climate path opened) is
// forced OFF by the next watchdog call, which is not a fault.
static void wiring_ungranted_relay_forced_off() {
  Dev d;
  d.epoch = at(600);
  d.boot(true);
  d.tick();
  d.relays = uint16_t(d.relays | irr_bit(kL05) | irr_bit(kL09));  // driven around every guard
  d.climate_only(1);
  CHECK(!(d.relays & (irr_bit(kL05) | irr_bit(kL09))) && d.faults_seen == 0,
        "both are forced OFF at once, with no fault counted");
}

// #1313: an OTA upload holds the loop, its on_begin hook closes every relay (#1276), then the
// upload aborts. The engine's drip is still open but its valve read OFF for the whole hold: the
// run is not charged for that time, the valve re-opens, and the drip still delivers its length.
static void aborted_ota_close_is_not_charged() {
  IrrProgram p = irr_rom_default();
  p.lines[kL01].daily_cap_min = 0;
  Sim s;
  s.epoch = at(600);
  s.boot(true, &p);
  s.tick();
  CHECK(s.run_now(kL01, 600) == IrrRunResult::kStarted, "#1313: the drip starts");
  s.run_for(120);
  CHECK(s.relays & irr_bit(kL01), "#1313: the valve is open before the upload");
  s.relays = 0;  // on_begin closed it; the upload then held the loop for 300 s and aborted
  s.tick(300);
  CHECK(s.out.relay_mask & irr_bit(kL01), "#1313: the engine re-opens the valve after the abort");
  s.run_for(900);
  CHECK(!(s.relays & irr_bit(kL01)), "#1313: the drip finished");
  CHECK(s.open_s[kL01] >= 600 && s.open_s[kL01] <= 610,
        "#1313: valve-open time equals the requested 600 s (one tick of slack), not 600 s less the hold");
  // A valve the engine opened but that never read ON keeps the old accounting and times out.
  Sim r;
  r.epoch = at(600);
  r.boot(true, &p);
  r.tick();
  CHECK(r.run_now(kL01, 60) == IrrRunResult::kStarted, "#1313: a second drip starts");
  for (int i = 0; i < 40; ++i) {
    r.relays = 0;  // refused: never reads ON
    r.tick();
  }
  CHECK(!(r.out.relay_mask & irr_bit(kL01)), "#1313: a refused valve still times out");
}

// #299: the per-zone mister re-fire fence (mister_min_off_s) against every origin. A VPD
// pulse on L09 closes; a manual run-now on the same zone, and a second VPD request, wait out
// the fence and open at its expiry, never before. A reset clears the fence: the engine's
// zone-off stamps are not kept across a boot (the RTC block holds runs and counters, not
// zone stamps), so after a reboot the zone may re-fire at once. Current behaviour, encoded.
static void mister_refire_fence_by_origin_and_reset() {
  IrrProgram p = open_program();
  const uint16_t fence_s = 45;
  // Manual origin: run-now inside the fence waits; it opens once the fence expires.
  {
    Sim s;
    s.in.mister_min_off_s = fence_s;
    s.epoch = at(600);
    s.boot(true, &p);
    s.tick();
    CHECK(irr_vpd_request(s.eng, kL09, 30, s.ms, s.out).answer == IrrVpdAnswer::kGrant, "#299: VPD pulse granted");
    s.record();
    s.run_for(30);
    irr_vpd_release(s.eng, kL09, s.ms, s.out);
    s.record();
    const uint32_t closed = s.ms;
    CHECK(!(s.relays & irr_bit(kL09)), "#299: the VPD pulse closed");
    CHECK(s.run_now(kL09, 45, 2, 30) == IrrRunResult::kStarted, "#299: run-now accepted inside the fence");
    CHECK(!(s.out.relay_mask & irr_bit(kL09)), "#299: run-now does not open inside the fence");
    uint32_t opened = 0;
    for (int i = 0; i < 40 && opened == 0; ++i) {
      s.tick();
      if (s.out.relay_mask & irr_bit(kL09)) opened = s.ms;
    }
    CHECK(opened != 0 && opened - closed >= uint32_t(fence_s) * 1000u,
          "#299: the manual pulse opens only after mister_min_off_s from the close");
    CHECK(opened - closed < uint32_t(fence_s) * 1000u + 10000u, "#299: and within two ticks of the fence expiry");
  }
  // VPD origin: a second request inside the fence is not granted; at the fence it is.
  {
    Sim s;
    s.in.mister_min_off_s = fence_s;
    s.epoch = at(600);
    s.boot(true, &p);
    s.tick();
    CHECK(irr_vpd_request(s.eng, kL09, 30, s.ms, s.out).answer == IrrVpdAnswer::kGrant, "#299: first VPD pulse");
    s.record();
    s.run_for(30);
    irr_vpd_release(s.eng, kL09, s.ms, s.out);
    s.record();
    const uint32_t closed = s.ms;
    s.run_for(40);
    CHECK(irr_vpd_request(s.eng, kL09, 30, s.ms, s.out).answer != IrrVpdAnswer::kGrant,
          "#299: a VPD re-fire 40 s after the close is refused (45 s fence)");
    while (s.ms - closed < uint32_t(fence_s) * 1000u) s.tick();
    CHECK(irr_vpd_request(s.eng, kL09, 30, s.ms, s.out).answer == IrrVpdAnswer::kGrant,
          "#299: at the fence expiry the VPD pulse is granted");
  }
  // Restart: the fence does not survive a reset (engine state re-initialised at boot).
  {
    Sim s;
    s.in.mister_min_off_s = fence_s;
    s.epoch = at(600);
    s.boot(true, &p);
    s.tick();
    CHECK(irr_vpd_request(s.eng, kL09, 30, s.ms, s.out).answer == IrrVpdAnswer::kGrant, "#299: pulse before reset");
    s.record();
    s.run_for(30);
    irr_vpd_release(s.eng, kL09, s.ms, s.out);
    s.record();
    s.run_for(10);
    s.boot(false, &p);  // a software reset 10 s after the close: RTC block kept
    s.tick();
    CHECK(irr_vpd_request(s.eng, kL09, 30, s.ms, s.out).answer == IrrVpdAnswer::kGrant,
          "#299 (current behaviour): after a reset the zone may re-fire before mister_min_off_s");
  }
}

int main() {
  codes_match_python();
  decoder_accepts_the_golden_vectors();
  decoder_rejects_length_and_hash();
  decoder_agrees_with_unpack_program();
  decoder_applies_per_kind_bounds();
  rom_default_is_todays_behaviour();
  clock_fields();
  due_window();
  due_across_dst();
  crc32_check_value();
  claims_save_then_sync();
  rtc_block_rules();
  ring_sequence_and_grammar();
  saf4_trips_at_the_program_gallons();
  reboot_after_a_claim();
  sensor_fault_refuses_all_engine_mist();
  degraded_only_fallback();
  degraded_sensing_grace();
  two_outcomes_in_one_tick();
  fertilizer_drip_sequence();
  l08_occupancy_skips_feed_pulses();
  fertilizer_partner_and_mist_rails();
  mist_manifold_alternation();
  drip_yields_to_climate();
  drip_pauses_for_survival_fog_and_emergency();
  caps_and_reserve();
  run_now_refusals();
  stop_disables_nothing();
  missed_and_fcfs();
  no_valid_time();
  midnight_rollover();
  more_engine_rules();
  full_program_day_invariants();
  wiring_program_id_text();
  wiring_state_text_matches_python();
  wiring_state_text_rules();
  wiring_apply_plan();
  wiring_program_push_staging();
  wiring_commands_and_clock();
  wiring_water_centigal();
  wiring_air_inputs();
  wiring_state_published_on_transitions_only();
  wiring_run_waiting_for_the_manifold_is_a_wait();
  wiring_program_id_rom_then_nvs();
  wiring_fallback_run_is_published_as_fallback();
  wiring_no_relay_without_the_grant();
  wiring_leak_changes_nothing_in_irrigation();
  wiring_stop_and_run_now_commands();
  wiring_local_time_rules();
  watchdog_backs_a_vpd_member_off_then_latches();
  watchdog_latches_schedule_only_and_fertilizer_misters_at_once();
  watchdog_grant_rail_and_edges();
  vpd_zone_allowed_follows_membership_and_faults();
  center_window_needs_schedule_membership();
  run_now_mister_without_schedule();
  master_valve_only_with_fertilized();
  fog_run_once();
  wiring_arbiter_picks_the_zone();
  wiring_vpd_and_schedule_alternate_at_the_boundary();
  wiring_schedule_only_window_counts_duty_and_saf4();
  wiring_occupancy_holds_every_mist_but_not_drips();
  wiring_survival_fog_runs_past_the_caps();
  wiring_watchdog_backs_off_then_resumes_and_latches();
  wiring_ungranted_relay_forced_off();
  aborted_ota_close_is_not_charged();
  mister_refire_fence_by_origin_and_reset();
  std::printf("test_irrigation_program: %d checks, %d failed\n", checks_run, checks_failed);
  return checks_failed == 0 ? 0 : 1;
}
