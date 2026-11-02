// test_irrigation_program.cpp — native tests for the irrigation engine core
// (firmware/lib/irrigation_program.h; irrigation §4-§5, F1; VD-44, #1159).
//
// The decoder runs on #1151's golden vectors and on Python-computed reject cases
// (firmware/test/irrigation_program_vectors_generated.inc, rendered by
// scripts/gen-irrigation-program-vectors.py from the one golden-vector JSON). The
// engine runs scripted traces through irr_tick() with fake NVS save and sync
// functions that can fail, soft resets and power cuts. Every check failure prints
// and the binary exits non-zero, so a failure fails the native firmware step of
// scripts/ci-local.sh.
//
// Build + run (make test-irrigation-program, part of test-firmware and of the native
// firmware step of scripts/ci-local.sh):
//   cd firmware && g++ -std=c++17 -I lib -o test/test_irrigation_program test/test_irrigation_program.cpp
//   ./test/test_irrigation_program

#include "irrigation_program.h"

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <initializer_list>
#include <type_traits>
#include <utility>

#include "irrigation_program_vectors_generated.inc"

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
                                      (fog_want && !out.drip_active && !out.l03_hold ? kFogMask : 0u));
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
  // Line kinds and partners (irrigation §3).
  CHECK(kCleanPartner[kL02] == kL01 && kCleanPartner[kL06] == kL05 && kCleanPartner[kL08] == kL07 &&
            kCleanPartner[kL10] == kL09,
        "clean partners L02/L01, L06/L05, L08/L07, L10/L09");
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
  CHECK(s.run_now(kL04, 30, 2, 60) == IrrRunResult::kNotScheduleMember &&
            event_is(s.last_event(), IrrOutcome::kSkipped, IrrReason::kNsm),
        "not_schedule_member");
  CHECK(s.run_now(kL01, 600, 2) == IrrRunResult::kBadArgs && s.run_now(kL03, 60) == IrrRunResult::kBadArgs &&
            s.run_now(kL11, 60) == IrrRunResult::kBadArgs && s.run_now(kL04, 91, 1, 30) == IrrRunResult::kBadArgs,
        "outside the bounds or not a schedulable line: bad_args");
  IrrProgram q = p;
  q.lines[kL01].paused_until = at(1200);
  members(q, kL04, true, true);
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
  CHECK(s.run_now(kL04, 30, 2, 60) == IrrRunResult::kStarted, "a mister run-now is a one-off window");
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
  CHECK(!s.l03_with_fog && !s.l03_with_clean_mist, "SAF-5: no fog and no clean mist while L03 is open");
  CHECK(!s.ungranted, "every engine relay granted");
  CHECK(!s.mist_in_sf, "no engine mist in SENSOR_FAULT");
  CHECK(!s.mist_occupied, "no engine mist while occupancy is confirmed (D-IRP-10)");
  CHECK(s.disabled_opened == 0, "no engine relay on a disabled line");
  CHECK(s.rtc.ring_count > 0 && s.rtc.next_seq > 8, "the day settled its occurrences");
  char buf[256];
  CHECK(irr_format_events(s.rtc, buf, sizeof(buf)) > 0, "irr_events formats");
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
  std::printf("test_irrigation_program: %d checks, %d failed\n", checks_run, checks_failed);
  return checks_failed == 0 ? 0 : 1;
}
