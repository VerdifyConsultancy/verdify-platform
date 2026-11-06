#pragma once
/*
 * irrigation_program.h — the irrigation engine core (irrigation §4-§5, F1; VD-44, #1159)
 * ======================================================================================
 *
 * Pure C++17: no ESPHome, Arduino or heap. Fixed arrays only. The same code
 * compiles into the ESP32 image (wired by firmware/greenhouse/irrigation_engine.yaml,
 * #1160) and into the native tests (firmware/test/test_irrigation_program.cpp), which
 * run on the golden vectors of verdify_schemas/irrigation.py (#1151).
 *
 * What lives here:
 *   - the 584 B program record VIP1: decoder (irr_decode) with #1151's reject codes,
 *     plus the per-kind bounds of irrigation §4.3, and the ROM default (irr_rom_default);
 *   - the due test (irr_due) on the America/Denver clock and the on-chip solar times;
 *   - exactly-once claims (IrrClaims, irr_claim): saved, then synced, before a relay opens;
 *   - the RTC no-init block (IrrRtcBlock): daily integer counters, the active clean run
 *     and the irr_events outcome ring, with magic, version, local day and CRC32;
 *   - the engine (irr_tick, irr_run_now, irr_stop, irr_vpd_request/irr_vpd_release):
 *     drip and mist manifolds, caps and the VPD reserve, the climate yield, the
 *     fertilizer sequence and the outcomes;
 *   - the pure half of the device wiring (VD-45, #1160; the ESP32 half is
 *     irrigation_engine_esphome.h): the irr_program_id, irr_state and irr_events text
 *     (irr_format_program_id, irr_format_state, irr_format_events), the relay grant plan
 *     (irr_apply_plan), the program push staging (IrrStaging), the irr_command arguments,
 *     the clock offset and the flow-meter centi-gallons;
 *   - the mist path (VD-46, #1161): which zone a climate VPD pulse may use
 *     (irr_vpd_zone_allowed, irr_rank_vpd_zone) and the D-IRP-19 mister watchdog
 *     (irr_watchdog_step, with its per-zone faults in the RTC block, irr_apply_faults,
 *     irr_fault_masks, irr_fault_tokens, irr_fault_clear).
 *
 * It touches no relay. Every entry point fills IrrOutputs: the grant mask (bit =
 * device_idx) that the wet relay guards check, the relays the engine wants ON, the
 * flags the climate path reads (drip_active for irrigation_water_conflict, l03_hold
 * for SAF-5, saf4_tripped for the fog force-off), the commanded length of each mister
 * it opens (for the D-IRP-19 watchdog), the irr_state fields and the irr_program_id
 * facts. leak_detected is not an input (Q-05, F14).
 *
 * Engine rules this header settles (irrigation §4.5-§4.7; see also §4.8):
 *   - One engine run at a time across both manifolds (drips never overlap mist or
 *     fog), first come first served by planned start. Climate VPD pulses are the
 *     climate path's; they ask for the mist manifold through irr_vpd_request() and
 *     alternate with scheduled pulses at pulse boundaries.
 *   - Every occurrence ends in exactly one outcome, claimed in NVS before it is
 *     published or its relay opens, so a reboot never repeats a run or an outcome.
 *   - A fertilizer run's outcome is published when its feed ends; its flush then
 *     runs as the run's tail (journaled: a reset repeats the flush, never the outcome).
 *   - The engine never opens a disabled line, and a prewet or flush on a mister is
 *     mist: the rails that hold any mist hold it.
 */

#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <type_traits>

#include "greenhouse_solar.h"
#include "policy_sha256.h"

namespace verdify_irr {

// ── Record layout (verdify_schemas/irrigation.py PROGRAM_*) ─────────────────

constexpr size_t kRecordLen = 584;
constexpr size_t kHeaderLen = 16;
constexpr size_t kLinesOffset = 16;
constexpr size_t kLineLen = 8;
constexpr size_t kSlotsOffset = 104;
constexpr size_t kSlotLen = 20;
constexpr uint8_t kLineCount = 11;
constexpr uint8_t kMaxSlots = 24;
constexpr size_t kChunkMax = 192;
constexpr uint8_t kMagic[4] = {'V', 'I', 'P', '1'};
constexpr uint8_t kSlotFlagDegradedOnly = 0x01;

static_assert(kLinesOffset == kHeaderLen, "lines follow the 16 B header");
static_assert(kSlotsOffset == kLinesOffset + kLineCount * kLineLen, "slots follow the 11 lines");
static_assert(kRecordLen == kSlotsOffset + kMaxSlots * kSlotLen, "584 B record");

// ── Fixed codes (copied from verdify_schemas/irrigation.py; the native tests
//    compare every table with the generated include) ─────────────────────────

// device_idx: L01 = 0 to L11 = 10.
enum : uint8_t {
  kL01 = 0, kL02 = 1, kL03 = 2, kL04 = 3, kL05 = 4, kL06 = 5,
  kL07 = 6, kL08 = 7, kL09 = 8, kL10 = 9, kL11 = 10,
};
constexpr uint8_t kNoLine = 255;
constexpr uint8_t kAllLines = 255;  // irr_stop(): every line
constexpr const char* kLineIds[kLineCount] = {"L01", "L02", "L03", "L04", "L05", "L06",
                                              "L07", "L08", "L09", "L10", "L11"};

constexpr uint16_t irr_bit(uint8_t idx) { return idx < 16 ? uint16_t(1u << idx) : uint16_t(0); }

// Bits of vpd_member_mask and schedule_member_mask (MISTER_MASK_BIT): L04 0, L07 1, L09 2.
constexpr uint8_t kMisterMaskBit[kLineCount] = {255, 255, 255, 0, 255, 255, 1, 255, 2, 255, 255};
// START_KIND and START_RANGE.
enum class IrrStartKind : uint8_t { kClock = 0, kSunrise = 1, kSolarNoon = 2, kSunset = 3 };
constexpr int16_t kStartRange[4][2] = {{0, 1439}, {-240, 240}, {-240, 240}, {-240, 240}};
// SCHEDULABLE_IDX: L01, L02 and L04-L10 (L03 opens with fertilizer runs; L11 fog is climate only).
constexpr uint16_t kSchedulableMask = irr_bit(kL01) | irr_bit(kL02) | irr_bit(kL04) | irr_bit(kL05) |
                                      irr_bit(kL06) | irr_bit(kL07) | irr_bit(kL08) | irr_bit(kL09) |
                                      irr_bit(kL10);
// DRIP_MANIFOLD_IDX and MIST_MANIFOLD_IDX.
constexpr uint16_t kDripManifoldMask = irr_bit(kL01) | irr_bit(kL02) | irr_bit(kL05) | irr_bit(kL06);
constexpr uint16_t kMistManifoldMask = irr_bit(kL04) | irr_bit(kL07) | irr_bit(kL08) | irr_bit(kL09) |
                                       irr_bit(kL10);
// DEGRADED_ONLY_IDX: the clean misters.
constexpr uint16_t kDegradedOnlyMask = irr_bit(kL04) | irr_bit(kL07) | irr_bit(kL09);
constexpr uint16_t kFogMask = irr_bit(kL11);
constexpr uint16_t kWetMask = 0x07FF;
// #1336 (Jason, 2026-11-05): the fertilizer master valve L03 connects the fertilizer tank and
// forces the flow through the feeder, so it opens only with a fertilized variant (L02, L06, L08,
// L10) and never with clean water (L01, L04, L05, L07, L09, fog).
constexpr uint16_t kFertRelayMask = irr_bit(kL02) | irr_bit(kL06) | irr_bit(kL08) | irr_bit(kL10);
constexpr uint16_t kCleanWetMask = irr_bit(kL01) | irr_bit(kL04) | irr_bit(kL05) | irr_bit(kL07) |
                                   irr_bit(kL09) | irr_bit(kL11);
static_assert((kFertRelayMask | kCleanWetMask | irr_bit(kL03)) == kWetMask, "every wet relay is clean, fert or L03");
static_assert((kFertRelayMask & kCleanWetMask) == 0, "no relay is both clean and fertilized");

// The master-valve rule on a set of relays the engine wants ON: L03 is ON exactly when a
// fertilized relay is and no clean one is. A clean relay never opens with L03 (a mixed set
// keeps L03 shut), and a fertilized relay always brings L03.
constexpr uint16_t irr_master_rule(uint16_t relays) {
  return ((relays & kFertRelayMask) != 0 && (relays & kCleanWetMask) == 0)
             ? uint16_t(relays | irr_bit(kL03))
             : uint16_t(relays & ~irr_bit(kL03));
}

// Line kinds (irrigation §3) and clean partners (prewet and flush).
enum class IrrLineKind : uint8_t { kDrip, kFertDrip, kMaster, kMister, kFertMister, kFog };
constexpr IrrLineKind kLineKind[kLineCount] = {
    IrrLineKind::kDrip,   IrrLineKind::kFertDrip,   IrrLineKind::kMaster,  IrrLineKind::kMister,
    IrrLineKind::kDrip,   IrrLineKind::kFertDrip,   IrrLineKind::kMister,  IrrLineKind::kFertMister,
    IrrLineKind::kMister, IrrLineKind::kFertMister, IrrLineKind::kFog};
constexpr uint8_t kCleanPartner[kLineCount] = {kNoLine, kL01, kNoLine, kNoLine, kNoLine, kL05,
                                               kNoLine, kL07, kNoLine, kL09, kNoLine};
// Mister zones: center (L04), south (L07, L08), west (L09, L10). Duty and reserve
// come from the zone's clean mister line.
constexpr uint8_t kZoneCount = 3;
constexpr uint8_t kMistZone[kLineCount] = {255, 255, 255, 0, 255, 255, 1, 1, 2, 2, 255};
constexpr uint8_t kZoneCleanLine[kZoneCount] = {kL04, kL07, kL09};

// Per-kind schedule bounds (irrigation §4.3; #1152's BOUNDS_BY_KIND), inclusive.
struct IrrKindBounds {
  uint16_t on_lo, on_hi;
  uint8_t count_lo, count_hi;
  uint16_t gap_lo, gap_hi;
  uint16_t prewet_hi, flush_hi;
};
inline IrrKindBounds irr_kind_bounds(IrrLineKind kind) {
  switch (kind) {
    case IrrLineKind::kDrip:       return {10, 7200, 1, 1, 0, 0, 0, 0};
    case IrrLineKind::kFertDrip:   return {10, 7200, 1, 1, 0, 0, 1800, 1800};
    case IrrLineKind::kMister:     return {10, 90, 1, 60, 30, 600, 0, 0};
    case IrrLineKind::kFertMister: return {10, 90, 1, 60, 30, 600, 1800, 1800};
    case IrrLineKind::kMaster:
    case IrrLineKind::kFog:
    default:                       return {1, 0, 1, 0, 1, 0, 0, 0};  // empty: nothing fits
  }
}

// IrrigationCause (the device's seven; "unknown" is the platform's).
enum class IrrCause : uint8_t { kVpd, kSchedule, kRunNow, kFeed, kPrewet, kFlush, kFallback };
constexpr uint8_t kCauseCount = 7;
constexpr const char* kCauseNames[kCauseCount] = {"vpd",   "schedule", "run_now", "feed",
                                                  "prewet", "flush",   "fallback"};
// irr_events outcome codes (OUTCOME_CODES), in RunOutcome order.
enum class IrrOutcome : uint8_t { kOk, kShort, kStopped, kPreempted, kSkipped, kMissed, kCap };
constexpr uint8_t kOutcomeCount = 7;
constexpr const char* kOutcomeCodes[kOutcomeCount] = {"ok", "sh", "st", "pe", "sk", "mi", "ca"};
// Reason codes shared by w= tokens and irr_events (REASON_WORDS order); kNone writes no reason.
enum class IrrReason : uint8_t {
  kNone, kDis, kPau, kNsm, kNvm, kVok, kBof, kFlt, kOcc, kCap, kRes,
  kRh, kDew, kClk, kMan, kSf, kBud, kSaf, kL03, kRst, kCmd,
};
constexpr uint8_t kReasonCount = 21;  // kNone included
constexpr const char* kReasonCodes[kReasonCount] = {"",    "dis", "pau", "nsm", "nvm", "vok", "bof",
                                                    "flt", "occ", "cap", "res", "rh",  "dew", "clk",
                                                    "man", "sf",  "bud", "saf", "l03", "rst", "cmd"};
// Push rejects: the j= vocabulary (REJECT_CODES).
enum class IrrReject : uint8_t { kNone, kBadLength, kBadMagic, kBadHash, kBadHeader, kBadLine, kBadSlot };
constexpr const char* kRejectTokens[7] = {"",           "bad_length", "bad_magic", "bad_hash",
                                          "bad_header", "bad_line",   "bad_slot"};
inline const char* irr_reject_token(IrrReject why) {
  const uint8_t i = uint8_t(why);
  return i < 7 ? kRejectTokens[i] : "";
}

// ── Program record ───────────────────────────────────────────────────────────

struct IrrLine {
  uint16_t daily_cap_min;    // 0 = no cap
  uint16_t vpd_reserve_min;  // at most daily_cap_min
  uint32_t paused_until;     // UTC epoch seconds, 0 = not paused
};

struct IrrSlot {
  uint16_t slot_id;  // 1-65535, ascending
  uint8_t line;      // device_idx
  uint8_t weekday_mask;  // bit 0 = Monday
  uint8_t start_kind;    // IrrStartKind
  uint8_t flags;         // bit 0 = degraded_only
  int16_t start;         // clock minute, or the offset from the solar time
  uint16_t on_s;
  uint16_t gap_s;
  uint16_t prewet_s;
  uint16_t flush_s;
  uint8_t count;
  bool degraded_only() const { return (flags & kSlotFlagDegradedOnly) != 0; }
};

struct IrrProgram {
  uint16_t saf4_gal;
  uint8_t rh_ceiling_pct;
  uint8_t climate_yield_min;
  uint16_t enabled_mask;          // bit = device_idx
  uint8_t vpd_member_mask;        // kMisterMaskBit
  uint8_t schedule_member_mask;   // kMisterMaskBit
  uint8_t slot_count;
  IrrLine lines[kLineCount];
  IrrSlot slots[kMaxSlots];
};
static_assert(std::is_trivially_copyable<IrrProgram>::value, "plain value type, no heap");

inline uint16_t irr_rd16(const uint8_t* p) { return uint16_t(uint16_t(p[0]) | (uint16_t(p[1]) << 8)); }
inline int16_t irr_rd16s(const uint8_t* p) {
  const uint16_t u = irr_rd16(p);
  return u < 0x8000u ? int16_t(u) : int16_t(int32_t(u) - 0x10000);
}
inline uint32_t irr_rd32(const uint8_t* p) {
  return uint32_t(p[0]) | (uint32_t(p[1]) << 8) | (uint32_t(p[2]) << 16) | (uint32_t(p[3]) << 24);
}

inline bool irr_all_zero(const uint8_t* p, size_t n) {
  for (size_t i = 0; i < n; ++i) {
    if (p[i] != 0) return false;
  }
  return true;
}

// Is a decoded slot inside its line kind's bounds (irrigation §4.3)? The decoder and
// irr_run_now() use the same table.
inline bool irr_run_args_in_bounds(uint8_t line, uint16_t on_s, uint8_t count, uint16_t gap_s, uint16_t prewet_s,
                                   uint16_t flush_s) {
  if (line >= kLineCount || !(kSchedulableMask & irr_bit(line))) return false;
  const IrrKindBounds b = irr_kind_bounds(kLineKind[line]);
  return on_s >= b.on_lo && on_s <= b.on_hi && count >= b.count_lo && count <= b.count_hi && gap_s >= b.gap_lo &&
         gap_s <= b.gap_hi && prewet_s <= b.prewet_hi && flush_s <= b.flush_hi;
}

// #1343 follow-up (Jason, 2026-11-05): fog (L11) may be run once. Fog is not schedulable (no
// slot ever names L11); its run-now is one continuous fog of 1-30 min (on_s 60-1800, count 1,
// no gap, prewet or flush). The climate path drives the fog relay, so the engine asks for fog
// (IrrOutputs::fog_run) and its rails still hold it.
constexpr uint16_t kFogRunOnLo = 60;
constexpr uint16_t kFogRunOnHi = 1800;
// What this build takes that an older one refuses, published as irr_caps
// ("fw=<fw_version> caps=<these>", verdify_schemas.irrigation.IRR_CAPS). The API
// offers a capability only while the controller reports it for the version it runs,
// so a build without it (or a rollback) fails closed (#1343 follow-up).
constexpr const char* kIrrCaps = "fog_run,run_any_mister";
inline bool irr_fog_run_args_in_bounds(uint16_t on_s, uint8_t count, uint16_t gap_s, uint16_t prewet_s,
                                       uint16_t flush_s) {
  return on_s >= kFogRunOnLo && on_s <= kFogRunOnHi && count == 1 && gap_s == 0 && prewet_s == 0 && flush_s == 0;
}

// Decode and validate a 584 B record. Little-endian by byte arithmetic, never by
// casting a packed struct. Checks, in unpack_program()'s section order: length,
// magic, then the SHA-256 that irr_program_begin carried, then header, lines and
// slots, so a record Python rejects is rejected here with the same code. On top of
// unpack_program() it applies the per-kind bounds of irrigation §4.3 (bad_slot).
// `out` is written only on success; `why` names the first failing section.
inline bool irr_decode(const uint8_t* rec, size_t len, const uint8_t sha256[32], IrrProgram& out, IrrReject& why) {
  why = IrrReject::kNone;
  if (rec == nullptr || len != kRecordLen) {
    why = IrrReject::kBadLength;
    return false;
  }
  if (std::memcmp(rec, kMagic, sizeof(kMagic)) != 0) {
    why = IrrReject::kBadMagic;
    return false;
  }
  uint8_t digest[32];
  verdify_policy::Sha256::hash(rec, len, digest);
  if (sha256 == nullptr || std::memcmp(digest, sha256, sizeof(digest)) != 0) {
    why = IrrReject::kBadHash;
    return false;
  }

  IrrProgram p{};
  p.saf4_gal = irr_rd16(rec + 4);
  p.rh_ceiling_pct = rec[6];
  p.climate_yield_min = rec[7];
  p.enabled_mask = irr_rd16(rec + 8);
  p.vpd_member_mask = rec[10];
  p.schedule_member_mask = rec[11];
  p.slot_count = rec[12];
  if (!irr_all_zero(rec + 13, 3) || p.slot_count > kMaxSlots || p.saf4_gal < 100 || p.saf4_gal > 600 ||
      p.rh_ceiling_pct < 75 || p.rh_ceiling_pct > 98 || p.climate_yield_min < 5 || p.climate_yield_min > 30 ||
      p.enabled_mask > kWetMask || p.vpd_member_mask > 0x07 || p.schedule_member_mask > 0x07) {
    why = IrrReject::kBadHeader;
    return false;
  }

  for (uint8_t i = 0; i < kLineCount; ++i) {
    const uint8_t* l = rec + kLinesOffset + i * kLineLen;
    IrrLine& line = p.lines[i];
    line.daily_cap_min = irr_rd16(l);
    line.vpd_reserve_min = irr_rd16(l + 2);
    line.paused_until = irr_rd32(l + 4);
    if (line.daily_cap_min > 480 || line.vpd_reserve_min > line.daily_cap_min) {
      why = IrrReject::kBadLine;
      return false;
    }
  }

  for (uint8_t i = 0; i < kMaxSlots; ++i) {
    const uint8_t* s = rec + kSlotsOffset + i * kSlotLen;
    if (i >= p.slot_count) {
      if (!irr_all_zero(s, kSlotLen)) {
        why = IrrReject::kBadSlot;
        return false;
      }
      continue;
    }
    IrrSlot& slot = p.slots[i];
    slot.slot_id = irr_rd16(s);
    slot.line = s[2];
    slot.weekday_mask = s[3];
    slot.start_kind = s[4];
    slot.flags = s[5];
    slot.start = irr_rd16s(s + 6);
    slot.on_s = irr_rd16(s + 8);
    slot.gap_s = irr_rd16(s + 10);
    slot.prewet_s = irr_rd16(s + 12);
    slot.flush_s = irr_rd16(s + 14);
    slot.count = s[16];
    const bool ok = irr_all_zero(s + 17, 3) && slot.slot_id != 0 &&
                    (i == 0 || slot.slot_id > p.slots[i - 1].slot_id) && slot.line < kLineCount &&
                    (kSchedulableMask & irr_bit(slot.line)) != 0 && slot.weekday_mask != 0 &&
                    (slot.weekday_mask & 0x80) == 0 && slot.start_kind <= 3 &&
                    (slot.flags & ~kSlotFlagDegradedOnly) == 0 &&
                    (!slot.degraded_only() || (kDegradedOnlyMask & irr_bit(slot.line)) != 0) &&
                    slot.start >= kStartRange[slot.start_kind <= 3 ? slot.start_kind : 0][0] &&
                    slot.start <= kStartRange[slot.start_kind <= 3 ? slot.start_kind : 0][1] &&
                    irr_run_args_in_bounds(slot.line, slot.on_s, slot.count, slot.gap_s, slot.prewet_s,
                                           slot.flush_s);
    if (!ok) {
      why = IrrReject::kBadSlot;
      return false;
    }
  }
  out = p;
  return true;
}

// Today's behaviour when no valid program is stored (irrigation §4.7 ROM default):
// L04 a VPD member, L01 enabled for run-now, no slots, fertilizer lines off, and the
// D-IRP-06 caps and reserves. It is the seed program without its three slots.
inline IrrProgram irr_rom_default() {
  IrrProgram p{};
  p.saf4_gal = 360;
  p.rh_ceiling_pct = 90;
  p.climate_yield_min = 15;
  p.enabled_mask = irr_bit(kL01) | irr_bit(kL04);
  p.vpd_member_mask = uint8_t(1u << kMisterMaskBit[kL04]);
  p.schedule_member_mask = 0;
  p.slot_count = 0;
  p.lines[kL01] = {60, 0, 0};
  p.lines[kL04] = {120, 60, 0};
  p.lines[kL05] = {30, 0, 0};
  p.lines[kL07] = {45, 15, 0};
  p.lines[kL09] = {45, 15, 0};
  return p;
}

// ── Clock and due test ──────────────────────────────────────────────────────

// The America/Denver clock from NTP (D-IRP-25: no RTC hardware). local_day counts
// days since 1970-01-01 in local time, as irr_events does.
struct IrrClock {
  bool valid;
  uint32_t epoch;           // UTC seconds
  int16_t utc_offset_min;   // local minus UTC, DST included (-420 MST, -360 MDT)
  uint16_t local_day;
  uint8_t weekday;          // 0 = Monday
  uint16_t minute;          // 0-1439, local
  SolarTimes solar;         // today's sunrise, solar noon and sunset (local minutes)
  SolarTimes solar_prev;    // yesterday's, for a late start whose 60 min run past midnight
};

inline IrrClock irr_clock_invalid() {
  IrrClock c{};
  c.valid = false;
  c.solar = {360, 780, 1200};
  c.solar_prev = c.solar;
  return c;
}

// The clock fields from UTC epoch seconds and the local offset the time component
// reports (the caller derives it each cycle, so DST is correct by construction).
inline IrrClock irr_clock_from_epoch(uint32_t epoch, int utc_offset_min) {
  IrrClock c{};
  const int64_t local_s = int64_t(epoch) + int64_t(utc_offset_min) * 60;
  if (epoch == 0 || local_s < 86400) return irr_clock_invalid();
  c.valid = true;
  c.epoch = epoch;
  c.utc_offset_min = int16_t(utc_offset_min);
  c.local_day = uint16_t(local_s / 86400);
  c.weekday = uint8_t((c.local_day + 3) % 7);  // 1970-01-01 was a Thursday
  c.minute = uint16_t((local_s % 86400) / 60);
  c.solar = compute_greenhouse_solar_times(civil_from_unix(epoch, utc_offset_min).day_of_year, utc_offset_min);
  c.solar_prev =
      compute_greenhouse_solar_times(civil_from_unix(epoch - 86400u, utc_offset_min).day_of_year, utc_offset_min);
  return c;
}

// UTC epoch of local midnight on `local_day`, in the clock's current offset.
inline int64_t irr_local_midnight(const IrrClock& c, uint16_t local_day) {
  return int64_t(local_day) * 86400 - int64_t(c.utc_offset_min) * 60;
}

// A slot's planned start in minutes after its day's local midnight (may be past
// 1439 when sunset + offset crosses midnight).
inline int irr_planned_minute(const IrrSlot& s, const SolarTimes& st) {
  switch (s.start_kind) {
    case 1: return st.sunrise_min + s.start;
    case 2: return st.solar_noon_min + s.start;
    case 3: return st.sunset_min + s.start;
    case 0:
    default: return s.start;
  }
}

inline bool irr_daylight(const IrrClock& c) {
  return c.valid && c.minute >= c.solar.sunrise_min && c.minute < c.solar.sunset_min;
}

struct IrrOccurrence {
  uint16_t occ_day;        // the local day the occurrence belongs to (claims, irr_events)
  int planned_minute;      // after that day's local midnight
  uint32_t planned_epoch;  // UTC
  uint32_t due_until_epoch;  // planned + 60 min: past it the occurrence is missed
};

constexpr uint32_t kDueWindowS = 3600;  // a start may be up to 60 min late

// Is `s` due now? A slot is due from its planned start until 60 min after it, on the
// weekdays of its mask (bit 0 = Monday). With no valid time nothing is due. Due-ness
// is judged on the local minute, so a start inside a skipped DST hour is due at the
// next minute (03:00), and a repeated hour is stopped by the per-day claim. A late
// start of yesterday's occurrence (23:01 or later, or sunset + offset past midnight)
// stays due into today under yesterday's day.
inline bool irr_due(const IrrSlot& s, const IrrClock& c, IrrOccurrence& occ) {
  if (!c.valid) return false;
  auto fill = [&](uint16_t day, int planned) {
    occ.occ_day = day;
    occ.planned_minute = planned;
    const int64_t at = irr_local_midnight(c, day) + int64_t(planned) * 60;
    occ.planned_epoch = at > 0 ? uint32_t(at) : 0u;
    occ.due_until_epoch = occ.planned_epoch + kDueWindowS;
  };
  const int now = int(c.minute);
  if (s.weekday_mask & (1u << c.weekday)) {
    const int m = irr_planned_minute(s, c.solar);
    if (m <= now && now < m + 60) {
      fill(c.local_day, m);
      return true;
    }
  }
  const uint8_t wd_prev = uint8_t((c.weekday + 6) % 7);
  if (c.local_day > 0 && (s.weekday_mask & (1u << wd_prev))) {
    const int m = irr_planned_minute(s, c.solar_prev);
    if (m - 1440 <= now && now < m - 1440 + 60) {
      fill(uint16_t(c.local_day - 1), m);
      return true;
    }
  }
  return false;
}

// ── CRC-32 (IEEE 802.3, reflected 0xEDB88320) ────────────────────────────────
// A small local copy: policy_crc32 lives in policy_vector.h, which pulls in the
// generated policy headers.

inline uint32_t irr_crc32_update(uint32_t crc, const uint8_t* data, size_t len) {
  crc = ~crc;
  for (size_t i = 0; i < len; ++i) {
    crc ^= data[i];
    for (int b = 0; b < 8; ++b) crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u)));
  }
  return ~crc;
}
inline uint32_t irr_crc32(const uint8_t* data, size_t len) { return irr_crc32_update(0, data, len); }

// CRC-32 of a POD with its crc32 field (4 bytes at `crc_offset`) left out.
template <typename T>
inline uint32_t irr_crc_without_field(const T& v, size_t crc_offset) {
  const uint8_t* p = reinterpret_cast<const uint8_t*>(&v);
  uint32_t crc = irr_crc32_update(0, p, crc_offset);
  return irr_crc32_update(crc, p + crc_offset + 4, sizeof(T) - crc_offset - 4);
}

// ── Claims: exactly once (NVS, saved then synced before a relay opens) ──────

constexpr uint32_t kClaimsMagic = 0x31434956u;  // "VIC1" in memory order
constexpr uint16_t kClaimsVersion = 1;

// Active fertilizer stage, journaled before each relay change of a fertilizer run.
enum class IrrStage : uint8_t {
  kNone = 0,
  kPrewet = 1,  // clean water on the partner, before any fertilizer
  kFeed = 2,    // L03 with the fertilizer relay
  kFlush = 3,   // the clean flush tail: the run's outcome is already published
};

struct IrrClaims {
  uint32_t magic;
  uint16_t version;
  uint16_t local_day;     // the day of today[]; prev[] holds local_day - 1
  uint8_t today_count;
  uint8_t prev_count;
  uint8_t fert_stage;     // IrrStage of the active fertilizer run
  uint8_t fert_line;      // its fertilizer line (L02, L06, L08 or L10)
  uint16_t fert_slot;     // slot_id, 0 = run-now
  uint16_t fert_day;      // local day of its occurrence
  uint16_t fert_flush_s;  // the flush that follows an interruption
  uint16_t reserved;
  uint16_t today[kMaxSlots];
  uint16_t prev[kMaxSlots];
  uint32_t crc32;
};
static_assert(sizeof(IrrClaims) == 120, "IrrClaims NVS layout");
static_assert(std::is_trivially_copyable<IrrClaims>::value, "IrrClaims is saved byte for byte");
constexpr size_t kClaimsCrcOffset = offsetof(IrrClaims, crc32);

inline uint32_t irr_claims_crc(const IrrClaims& c) { return irr_crc_without_field(c, kClaimsCrcOffset); }
inline void irr_claims_seal(IrrClaims& c) { c.crc32 = irr_claims_crc(c); }

inline IrrClaims irr_claims_empty() {
  IrrClaims c{};
  c.magic = kClaimsMagic;
  c.version = kClaimsVersion;
  c.fert_line = kNoLine;
  irr_claims_seal(c);
  return c;
}

inline bool irr_claims_valid(const IrrClaims& c) {
  return c.magic == kClaimsMagic && c.version == kClaimsVersion && c.today_count <= kMaxSlots &&
         c.prev_count <= kMaxSlots && c.fert_stage <= uint8_t(IrrStage::kFlush) && c.crc32 == irr_claims_crc(c);
}

inline bool irr_claimed(const IrrClaims& c, uint16_t slot_id, uint16_t occ_day) {
  const uint16_t* list = nullptr;
  uint8_t n = 0;
  if (occ_day == c.local_day) {
    list = c.today;
    n = c.today_count;
  } else if (uint16_t(occ_day + 1) == c.local_day) {
    list = c.prev;
    n = c.prev_count;
  }
  for (uint8_t i = 0; i < n; ++i) {
    if (list[i] == slot_id) return true;
  }
  return false;
}

// Roll `c` forward so today[] is `day` (yesterday's claims move to prev[]).
inline void irr_claims_roll(IrrClaims& c, uint16_t day) {
  if (day <= c.local_day) return;
  if (c.local_day != 0 && day == uint16_t(c.local_day + 1)) {
    std::memcpy(c.prev, c.today, sizeof(c.prev));
    c.prev_count = c.today_count;
  } else {
    std::memset(c.prev, 0, sizeof(c.prev));
    c.prev_count = 0;
  }
  std::memset(c.today, 0, sizeof(c.today));
  c.today_count = 0;
  c.local_day = day;
}

// Add (slot_id, day) to a candidate record. When a day already holds 24 claims, a
// claim whose slot_id is not in `live` (the current program's slot ids, optional)
// is dropped first: such a slot cannot be due again.
inline bool irr_claims_add(IrrClaims& c, uint16_t slot_id, uint16_t occ_day, const IrrProgram* live = nullptr) {
  irr_claims_roll(c, occ_day);
  uint16_t* list = nullptr;
  uint8_t* n = nullptr;
  if (occ_day == c.local_day) {
    list = c.today;
    n = &c.today_count;
  } else if (uint16_t(occ_day + 1) == c.local_day) {
    list = c.prev;
    n = &c.prev_count;
  } else {
    return false;  // older than yesterday: never due again
  }
  for (uint8_t i = 0; i < *n; ++i) {
    if (list[i] == slot_id) return true;
  }
  if (*n >= kMaxSlots && live != nullptr) {
    for (uint8_t i = 0; i < *n; ++i) {
      bool in_program = false;
      for (uint8_t k = 0; k < live->slot_count; ++k) in_program = in_program || live->slots[k].slot_id == list[i];
      if (!in_program) {
        list[i] = list[*n - 1];
        list[*n - 1] = 0;
        --*n;
        break;
      }
    }
  }
  if (*n >= kMaxSlots) return false;
  list[(*n)++] = slot_id;
  return true;
}

// Save the record, then sync (global_preferences->sync() on the device); `claims`
// changes only when both succeed, and only then may the caller open a relay.
template <typename SaveFn, typename SyncFn>
inline bool irr_commit_claims(IrrClaims& claims, IrrClaims candidate, SaveFn&& save, SyncFn&& sync) {
  irr_claims_seal(candidate);
  if (!save(static_cast<const IrrClaims&>(candidate)) || !sync()) return false;
  claims = candidate;
  return true;
}

// Claim the occurrence (slot_id, local_day) exactly once, in the save-then-sync shape
// of the retired wall-feed journal (irr_commit_claims()). At most 24 claims a day.
template <typename SaveFn, typename SyncFn>
inline bool irr_claim(IrrClaims& claims, uint16_t slot_id, uint16_t local_day, SaveFn&& save, SyncFn&& sync) {
  IrrClaims candidate = claims;
  if (!irr_claims_add(candidate, slot_id, local_day)) return false;
  return irr_commit_claims(claims, candidate, save, sync);
}

// ── RTC no-init block: daily counters, the active clean run, the outcome ring ──

constexpr uint32_t kRtcMagic = 0x31524956u;  // "VIR1" in memory order
// 2: the mister watchdog's per-zone faults (#1161). A block of another version is zeroed.
constexpr uint16_t kRtcVersion = 2;
constexpr uint8_t kRingSize = 8;

struct IrrEventEntry {
  uint16_t seq;        // u16, wraps 65535 -> 0
  uint16_t slot_id;    // 0 = run-now
  uint16_t local_day;  // America/Denver days since 1970-01-01
  uint8_t line;        // device_idx
  uint8_t outcome;     // IrrOutcome
  uint8_t reason;      // IrrReason (kNone writes none)
  uint8_t reserved;
};
static_assert(sizeof(IrrEventEntry) == 10, "ring entry layout");

// The active clean run (a drip or a mist window), so a soft reset resumes its remainder.
struct IrrRtcRun {
  uint8_t kind;        // 0 none, 1 drip, 2 window (IrrRunKind)
  uint8_t line;
  uint8_t cause;       // IrrCause
  uint8_t count;       // window: pulses planned
  uint16_t slot_id;    // 0 = run-now
  uint16_t occ_day;
  uint16_t on_s;
  uint16_t gap_s;
  uint16_t done;       // window: pulses delivered
  uint16_t remaining_s;  // drip: valve-open seconds still owed
  uint32_t start_epoch;
  uint32_t reserved;
};
static_assert(sizeof(IrrRtcRun) == 24, "active-run layout");

// One mister zone's D-IRP-19 watchdog state (F10, #1161; irr_watchdog_step()). The fault count
// and the latch are daily: local midnight zeroes them, and clear_fault does. A back-off is a
// countdown that runs across midnight and survives a soft reset with the block (the seconds a
// reboot takes are not counted down). A power cut zeroes the whole block, faults included.
struct IrrRtcFault {
  uint8_t count;             // watchdog faults on the zone today
  uint8_t latched;           // 1: latched until local midnight or clear_fault
  uint8_t backing_off;       // 1: backing off for backoff_left_ms more
  uint8_t line;              // the mister line of the zone's last fault (its f= token names it)
  uint32_t backoff_left_ms;
  uint32_t until_epoch;      // the published end: the back-off's end or the latch's local midnight
                             // (UTC seconds; 0 until a valid clock gives it)
};
static_assert(sizeof(IrrRtcFault) == 12, "watchdog fault layout");

struct IrrRtcBlock {
  uint32_t magic;
  uint16_t version;
  uint16_t local_day;  // the day the daily counters belong to (0 = not yet known)
  uint32_t crc32;      // over the whole block with this field left out
  // Daily counters, integers only, zeroed at local midnight.
  uint32_t water_centigal;            // SAF-4: every mister relay plus climate fog, survival fog included
  uint32_t line_on_s[kLineCount];     // valve-open seconds per line
  uint32_t zone_pulse_s[kZoneCount];  // mister seconds per zone (center, south, west), any origin
  // The active clean run.
  IrrRtcRun run;
  // The irr_events ring and its sequence; they survive midnight.
  IrrEventEntry ring[kRingSize];
  uint8_t ring_count;
  uint8_t ring_head;  // index of the oldest entry
  uint16_t next_seq;
  // The mister watchdog's faults per zone (center, south, west; D-IRP-19).
  IrrRtcFault fault[kZoneCount];
};
static_assert(sizeof(IrrRtcBlock) == 216, "IrrRtcBlock layout (RTC_NOINIT_ATTR on the device)");
static_assert(std::is_trivially_copyable<IrrRtcBlock>::value, "IrrRtcBlock is a POD");
constexpr size_t kRtcCrcOffset = offsetof(IrrRtcBlock, crc32);

inline uint32_t irr_rtc_crc(const IrrRtcBlock& b) { return irr_crc_without_field(b, kRtcCrcOffset); }
inline void irr_rtc_seal(IrrRtcBlock& b) { b.crc32 = irr_rtc_crc(b); }

inline void irr_rtc_zero_daily(IrrRtcBlock& b) {
  b.water_centigal = 0;
  std::memset(b.line_on_s, 0, sizeof(b.line_on_s));
  std::memset(b.zone_pulse_s, 0, sizeof(b.zone_pulse_s));
  // The watchdog's daily fault counts and latches (D-IRP-19); a back-off runs to its end.
  for (IrrRtcFault& f : b.fault) {
    f.count = 0;
    f.latched = 0;
    if (!f.backing_off) f = IrrRtcFault{};
  }
}

// On power-on, or a bad magic, version or CRC32 (or an impossible ring index), zero
// the whole block. On a new local day zero only the daily counters (the watchdog's
// fault counts and latches among them): the ring, its sequence, the active clean run
// and a running back-off stay. local_day 0 means the time is not known yet; a block
// whose day is still unknown adopts the first known day without zeroing.
// Returns true when the whole block was zeroed.
inline bool irr_rtc_validate(IrrRtcBlock& b, bool power_on, uint16_t local_day) {
  const bool bad = power_on || b.magic != kRtcMagic || b.version != kRtcVersion || b.crc32 != irr_rtc_crc(b) ||
                   b.ring_count > kRingSize || b.ring_head >= kRingSize;
  if (bad) {
    std::memset(&b, 0, sizeof(b));
    b.magic = kRtcMagic;
    b.version = kRtcVersion;
    b.local_day = local_day;
    irr_rtc_seal(b);
    return true;
  }
  if (local_day != 0 && b.local_day != local_day) {
    if (b.local_day != 0) irr_rtc_zero_daily(b);
    b.local_day = local_day;
    irr_rtc_seal(b);
  }
  return false;
}

// Append one outcome; the oldest drops past 8. The sequence increments per outcome
// and wraps at 65535.
inline void irr_ring_push(IrrRtcBlock& b, uint8_t line, uint16_t slot_id, uint16_t local_day, IrrOutcome outcome,
                          IrrReason reason) {
  uint8_t pos;
  if (b.ring_count < kRingSize) {
    pos = uint8_t((b.ring_head + b.ring_count) % kRingSize);
    ++b.ring_count;
  } else {
    pos = b.ring_head;
    b.ring_head = uint8_t((b.ring_head + 1) % kRingSize);
  }
  IrrEventEntry& e = b.ring[pos];
  e.seq = b.next_seq;
  e.slot_id = slot_id;
  e.local_day = local_day;
  e.line = line;
  e.outcome = uint8_t(outcome);
  e.reason = uint8_t(reason);
  e.reserved = 0;
  b.next_seq = uint16_t(b.next_seq + 1);
  irr_rtc_seal(b);
}

// The i-th oldest ring entry.
inline const IrrEventEntry& irr_ring_at(const IrrRtcBlock& b, uint8_t i) {
  return b.ring[(b.ring_head + i) % kRingSize];
}

// irr_events text: "<seq>,<idx>,<slot>,<local_day>,<outcome>[,<reason>]" per entry,
// oldest first, one space apart (#1151's grammar; eight full entries fit in 223).
// Returns the length written, or 0 when `cap` is too small (`buf` then reads "").
inline size_t irr_format_events(const IrrRtcBlock& b, char* buf, size_t cap) {
  if (buf == nullptr || cap == 0) return 0;
  size_t n = 0;
  buf[0] = '\0';
  const uint8_t count = b.ring_count <= kRingSize ? b.ring_count : kRingSize;
  for (uint8_t i = 0; i < count; ++i) {
    const IrrEventEntry& e = irr_ring_at(b, i);
    const char* outcome = e.outcome < kOutcomeCount ? kOutcomeCodes[e.outcome] : "ok";
    const char* reason = e.reason < kReasonCount ? kReasonCodes[e.reason] : "";
    char item[40];
    int w = std::snprintf(item, sizeof(item), "%s%u,%u,%u,%u,%s%s%s", i ? " " : "", unsigned(e.seq),
                          unsigned(e.line), unsigned(e.slot_id), unsigned(e.local_day), outcome,
                          reason[0] ? "," : "", reason);
    if (w < 0 || n + size_t(w) + 1 > cap) {
      buf[0] = '\0';
      return 0;
    }
    std::memcpy(buf + n, item, size_t(w) + 1);
    n += size_t(w);
  }
  return n;
}

// ── Engine ───────────────────────────────────────────────────────────────────

constexpr uint32_t kWindowGraceMs = 3600000u;   // a window ends 60 min after its last planned pulse
constexpr uint32_t kFlushGiveUpMs = 3600000u;   // a flush that cannot run for 60 min is dropped
constexpr uint32_t kFogYieldMaxMs = 120000u;    // a drip yields at most 120 s of fog
constexpr uint32_t kYieldIdleMs = 60000u;       // a yield nobody uses ends after 60 s
constexpr uint32_t kVpdFreshMs = 10000u;        // a waiting VPD request keeps its claim on the turn this long
constexpr uint32_t kVpdGrantSlackMs = 30000u;   // a VPD grant never released lapses after on_s + 30 s
constexpr uint32_t kSettleGiveUpMs = 600000u;   // an outcome whose claim never saves is dropped 10 min late

inline bool irr_reached(uint32_t now_ms, uint32_t t_ms) { return int32_t(now_ms - t_ms) >= 0; }

// What the caller samples for every tick. Everything here is a measurement or a
// mode; there is no leak input (Q-05, F14), no latch and no force-off.
struct IrrInputs {
  uint32_t now_ms;                // monotonic millis (wrap-safe)
  IrrClock clock;                 // America/Denver from NTP; !valid: nothing starts on schedule
  bool sensor_fault;              // controller mode SENSOR_FAULT: no engine mist at all (invariant 26)
  bool sensor_degraded;           // VPD sensing degraded (SF1) past its 300 s grace: degraded_only slots may run
  bool safety_cool;               // controller mode SAFETY_COOL: survival fog preempts drips
  bool vpd_emergency;             // climate_vpd_emergency: VPD first, clean drips pause
  bool climate_demand;            // VPD mist or fog demand the climate path holds back for a drip
  bool occupied;                  // Frigate confirms occupancy (occupancy_moisture_block)
  float rh_pct;                   // measured indoor RH, NaN = missing (does not block)
  bool dew_margin_known;          // the dew margin can be computed (degraded_only needs it)
  bool dew_breach;                // a measured dew-margin breach
  uint32_t soft_budget_centigal;  // mister_water_budget_gal x 100, 0 = none
  uint16_t mister_min_off_s;      // re-fire fence: the shortest gap on a zone
  uint16_t relay_on_mask;         // physical state of every wet relay, fog (bit 10) included
  uint16_t backoff_mask;          // mister lines backing off (D-IRP-19, #1161)
  uint16_t latch_mask;            // mister lines latched (D-IRP-19, #1161)
};

inline IrrInputs irr_inputs_idle() {
  IrrInputs in{};
  in.clock = irr_clock_invalid();
  in.rh_pct = NAN;
  in.dew_margin_known = true;
  in.mister_min_off_s = 45;
  return in;
}

// An irr_state run token: d= (drip manifold) or m= (mist manifold).
struct IrrRunToken {
  bool valid;
  uint8_t line;  // the relay whose run it is (a fertilizer run names its partner in prewet and flush)
  IrrCause cause;
  uint16_t slot_id;  // 0 = run-now or a VPD pulse
  uint32_t start_epoch;
  uint32_t end_epoch;  // planned end
};

// A w= token: a due run waiting or blocked.
struct IrrWait {
  uint8_t line;
  IrrReason reason;
  uint32_t until_epoch;  // 0 = not known
};

struct IrrStateFields {
  IrrRunToken drip;
  IrrRunToken mist;
  IrrWait waits[kMaxSlots + 1];  // earliest due first
  uint8_t wait_count;
  IrrReject reject;  // last push reject (j=)
};

inline bool irr_state_equal(const IrrStateFields& a, const IrrStateFields& b) {
  auto tok = [](const IrrRunToken& x, const IrrRunToken& y) {
    return x.valid == y.valid && (!x.valid || (x.line == y.line && x.cause == y.cause && x.slot_id == y.slot_id &&
                                                x.start_epoch == y.start_epoch && x.end_epoch == y.end_epoch));
  };
  if (!tok(a.drip, b.drip) || !tok(a.mist, b.mist) || a.wait_count != b.wait_count || a.reject != b.reject) {
    return false;
  }
  for (uint8_t i = 0; i < a.wait_count; ++i) {
    if (a.waits[i].line != b.waits[i].line || a.waits[i].reason != b.waits[i].reason ||
        a.waits[i].until_epoch != b.waits[i].until_epoch) {
      return false;
    }
  }
  return true;
}

struct IrrOutputs {
  uint16_t grant_mask;   // every relay the engine opens or lets VPD pulse (bit = device_idx)
  uint16_t relay_mask;   // the relays the engine wants ON now (a VPD pulse is the climate path's)
  uint8_t drip_on;       // the drip relay ON, or kNoLine
  uint8_t mist_on;       // the engine's mister relay ON, or kNoLine
  uint16_t mist_cmd_s;   // the commanded length of that open (a pulse, a prewet or a flush), for D-IRP-19
  bool l03_on;           // L03 open (SAF-5)
  bool l03_hold;         // L03 open or about to open: clean mist and fog stay OFF (SAF-5)
  bool drip_active;      // a drip run holds the water: climate mist and fog wait (irrigation_water_conflict)
  bool saf4_tripped;     // SAF-4 reached: all mist and non-survival fog off
  bool soft_budget_reached;
  bool fog_run;          // a fog run-once wants fog now (#1343 follow-up): the climate path fogs, under its rails
  IrrStateFields state;
  bool state_changed;    // publish irr_state (before applying relay_mask)
  uint16_t next_seq;     // the ring's next sequence number
  bool events_changed;   // publish irr_events
  bool program_nvs;      // irr_program_id src=nvs (false: src=rom, h=0000000000000000)
  uint8_t program_hash8[8];
};

// How the engine persists its claims record (one NVS preference on the device).
struct IrrPersist {
  bool (*save)(const IrrClaims& record, void* ctx) = nullptr;
  bool (*sync)(void* ctx) = nullptr;
  void* ctx = nullptr;
};

// kFog: a fog run-once (#1343 follow-up). Not mirrored in the RTC block: a reset ends it unpublished.
enum class IrrRunKind : uint8_t { kNone = 0, kDrip = 1, kWindow = 2, kFert = 3, kFog = 4 };

struct IrrRun {
  IrrRunKind kind;
  IrrStage stage;     // fertilizer runs
  uint8_t line;       // the occurrence's line
  uint8_t partner;    // clean partner of a fertilizer line
  IrrCause cause;     // clean runs: schedule, run_now or fallback
  uint16_t slot_id;   // 0 = run-now
  uint16_t occ_day;
  uint16_t on_s, gap_s, prewet_s, flush_s;
  uint8_t count;
  bool degraded_only;
  // A continuous open (a drip, a prewet, a drip feed or a flush).
  uint32_t remaining_ms;
  bool open;
  uint32_t open_since_ms;
  // Pulses (mist windows and mister feeds).
  uint8_t done;
  bool pulse_open;
  uint32_t pulse_end_ms;
  uint32_t next_pulse_ms;
  uint32_t deadline_ms;
  // Clean-drip pauses: SAFETY_COOL or a VPD emergency (paused), or the climate yield.
  bool paused;
  bool yielding;
  uint32_t yield_accum_ms;
  uint32_t yield_start_ms;
  uint32_t yield_fog_ms;
  bool yield_climate_seen;
  bool yield_vpd_done;
  // Fertilizer: the outcome settled when the feed ended, and a flush waiting to be journaled
  // (to_flush) or a cleared journal waiting to be saved before the outcome is out (to_settle).
  bool to_flush;
  bool to_settle;
  IrrOutcome fert_outcome;
  IrrReason fert_reason;
  uint32_t flush_since_ms;
  uint32_t settle_since_ms;
  bool fog_wait;  // the next feed open waits for fog to read OFF (SAF-5: L03 never opens with fog)
  // irr_state.
  uint32_t start_epoch;
  uint32_t end_epoch;
  IrrReason block;       // why the next relay cannot open now (kNone: not blocked)
  IrrReason last_block;  // the last such reason, for `short`
};

struct IrrPending {
  bool used;
  uint16_t slot_id;
  uint16_t occ_day;
  uint32_t planned_epoch;
  uint32_t deadline_ms;
  IrrReason reason;
  uint32_t until_epoch;
};

struct IrrEngine {
  IrrRtcBlock* rtc = nullptr;
  IrrPersist persist{};
  IrrClaims claims{};
  IrrProgram prog{};
  bool prog_nvs = false;
  uint8_t prog_hash[32]{};
  bool staged = false;
  IrrProgram staged_prog{};
  uint8_t staged_hash[32]{};
  IrrReject reject = IrrReject::kNone;
  IrrInputs in{};
  bool have_tick = false;
  bool recovered = false;
  uint32_t carry_ms[kLineCount]{};
  uint16_t engine_mask = 0;  // relays the engine left ON after its last call
  IrrRun run{};
  IrrPending pending[kMaxSlots]{};
  // Mist manifold: the VPD pulse granted now, a VPD request waiting for its turn,
  // and whose turn the next free pulse boundary is.
  uint8_t vpd_line = kNoLine;
  uint32_t vpd_since_ms = 0;
  uint16_t vpd_on_s = 0;
  uint32_t vpd_start_epoch = 0;
  uint32_t vpd_end_epoch = 0;
  uint8_t vpd_wait_line = kNoLine;
  uint32_t vpd_wait_ms = 0;
  bool sched_turn = false;
  bool zone_off_known[kZoneCount]{};
  uint32_t zone_off_ms[kZoneCount]{};
  // Mister relays whose close the engine stamped when it happened since the last tick (an
  // engine pulse, a VPD release, a stop): the next tick's relay sample does not stamp them again.
  uint16_t off_stamped = 0;
  // Published state.
  IrrStateFields last_state{};
  bool last_state_valid = false;
  uint16_t last_seq = 0;
  uint8_t last_ring_count = 0;
};

// ── Engine helpers ───────────────────────────────────────────────────────────

namespace detail {

inline bool is_mister(uint8_t idx) { return idx < kLineCount && (kMistManifoldMask & irr_bit(idx)) != 0; }
inline bool is_drip_line(uint8_t idx) { return idx < kLineCount && (kDripManifoldMask & irr_bit(idx)) != 0; }
inline bool is_clean_mister(uint8_t idx) { return idx < kLineCount && kMisterMaskBit[idx] != 255; }
inline bool is_fert(uint8_t idx) { return idx < kLineCount && kCleanPartner[idx] != kNoLine; }

inline bool line_enabled(const IrrEngine& e, uint8_t idx) { return (e.prog.enabled_mask & irr_bit(idx)) != 0; }
inline bool vpd_member(const IrrEngine& e, uint8_t idx) {
  return is_clean_mister(idx) && (e.prog.vpd_member_mask & (1u << kMisterMaskBit[idx])) != 0;
}
inline bool schedule_member(const IrrEngine& e, uint8_t idx) {
  return is_clean_mister(idx) && (e.prog.schedule_member_mask & (1u << kMisterMaskBit[idx])) != 0;
}

inline uint32_t epoch_now(const IrrEngine& e, uint32_t now_ms) {
  if (!e.in.clock.valid) return 0;
  const int32_t since = int32_t(now_ms - e.in.now_ms);
  return e.in.clock.epoch + (since > 0 ? uint32_t(since) / 1000u : 0u);
}
inline uint16_t today(const IrrEngine& e) {
  return e.in.clock.valid ? e.in.clock.local_day : (e.rtc ? e.rtc->local_day : uint16_t(0));
}
inline uint32_t cap_s(const IrrEngine& e, uint8_t idx) { return uint32_t(e.prog.lines[idx].daily_cap_min) * 60u; }
inline uint32_t reserve_s(const IrrEngine& e, uint8_t idx) {
  return uint32_t(e.prog.lines[idx].vpd_reserve_min) * 60u;
}
inline bool saf4_reached(const IrrEngine& e) {
  return e.rtc->water_centigal >= uint32_t(e.prog.saf4_gal) * 100u;
}
inline bool budget_reached(const IrrEngine& e) {
  return e.in.soft_budget_centigal > 0 && e.rtc->water_centigal >= e.in.soft_budget_centigal;
}
inline bool paused_at(const IrrEngine& e, uint8_t idx, uint32_t epoch) {
  const uint32_t until = e.prog.lines[idx].paused_until;
  if (until == 0) return false;
  return epoch == 0 || epoch < until;  // with no clock a set pause holds
}

// Why a mist pulse on `idx` may not run at all today: SAF-4, the soft budget (not
// for VPD in an emergency), the zone duty cap, the VPD reserve (scheduled pulses),
// and the fertilizer mister's own line cap.
inline IrrReason mist_cap_reason(const IrrEngine& e, uint8_t idx, bool scheduled) {
  if (saf4_reached(e)) return IrrReason::kSaf;
  if (budget_reached(e) && (scheduled || !e.in.vpd_emergency)) return IrrReason::kBud;
  const uint8_t z = kMistZone[idx];
  if (z < kZoneCount) {
    const uint8_t clean = kZoneCleanLine[z];
    const uint32_t zcap = cap_s(e, clean);
    if (zcap > 0) {
      if (e.rtc->zone_pulse_s[z] >= zcap) return IrrReason::kCap;
      if (scheduled && e.rtc->zone_pulse_s[z] + reserve_s(e, clean) >= zcap) return IrrReason::kRes;
    }
  }
  if (is_fert(idx) && cap_s(e, idx) > 0 && e.rtc->line_on_s[idx] >= cap_s(e, idx)) return IrrReason::kCap;
  return IrrReason::kNone;
}

inline IrrReason drip_cap_reason(const IrrEngine& e, uint8_t idx) {
  const uint32_t cap = cap_s(e, idx);
  return cap > 0 && e.rtc->line_on_s[idx] >= cap ? IrrReason::kCap : IrrReason::kNone;
}

// Seconds the line may still run today under its own cap (UINT32_MAX: no cap).
inline uint32_t cap_left_s(const IrrEngine& e, uint8_t idx) {
  const uint32_t cap = cap_s(e, idx);
  if (cap == 0) return UINT32_MAX;
  return e.rtc->line_on_s[idx] >= cap ? 0u : cap - e.rtc->line_on_s[idx];
}

// Seconds scheduled pulses may still use on the zone of `idx` (cap minus reserve).
inline uint32_t zone_sched_left_s(const IrrEngine& e, uint8_t idx) {
  const uint8_t z = kMistZone[idx];
  if (z >= kZoneCount) return UINT32_MAX;
  const uint8_t clean = kZoneCleanLine[z];
  const uint32_t zcap = cap_s(e, clean);
  if (zcap == 0) return UINT32_MAX;
  const uint32_t used = e.rtc->zone_pulse_s[z] + reserve_s(e, clean);
  return used >= zcap ? 0u : zcap - used;
}

inline bool zone_fenced(const IrrEngine& e, uint8_t idx, uint32_t now_ms) {
  const uint8_t z = kMistZone[idx];
  if (z >= kZoneCount || !e.zone_off_known[z]) return false;
  return !irr_reached(now_ms, e.zone_off_ms[z] + uint32_t(e.in.mister_min_off_s) * 1000u);
}

inline uint16_t zone_lines(uint8_t idx) {
  const uint8_t z = kMistZone[idx];
  uint16_t m = 0;
  for (uint8_t i = 0; i < kLineCount; ++i) {
    if (kMistZone[i] == z) m |= irr_bit(i);
  }
  return m;
}

// Transient reasons a scheduled mist pulse waits (irrigation §4.6), in the order they
// are reported. `fert_feed` pulses (L08, L10) skip on occupancy and faults only: no
// climate sensor gates a fertilizer run. A missing reading never blocks.
inline IrrReason pulse_skip(const IrrEngine& e, uint8_t idx, bool fert_feed, bool degraded_only) {
  if (e.in.sensor_fault) return IrrReason::kSf;
  if (e.in.latch_mask & zone_lines(idx)) return IrrReason::kFlt;
  if (e.in.backoff_mask & zone_lines(idx)) return IrrReason::kBof;
  if (degraded_only) {
    if (!e.in.sensor_degraded) return IrrReason::kVok;
    if (!e.in.dew_margin_known) return IrrReason::kDew;
  }
  if (e.in.occupied) return IrrReason::kOcc;
  if (fert_feed) return IrrReason::kNone;
  if (std::isfinite(e.in.rh_pct) && e.in.rh_pct >= float(e.prog.rh_ceiling_pct)) return IrrReason::kRh;
  if (e.in.dew_breach) return IrrReason::kDew;
  return IrrReason::kNone;
}

// Why a prewet or flush on a mister partner (L07 for L08, L09 for L10) may not run now.
// It is mist, so the rails that hold any mist hold it: SENSOR_FAULT (invariant 26), a
// latch or back-off (D-IRP-19), confirmed occupancy (D-IRP-10) and the SAF-4 ceiling.
// The soft budget, the zone duty cap and the drip caps count it but do not stop it.
inline IrrReason mist_tail_hold(const IrrEngine& e, uint8_t partner) {
  if (e.in.sensor_fault) return IrrReason::kSf;
  if (e.in.latch_mask & zone_lines(partner)) return IrrReason::kFlt;
  if (e.in.backoff_mask & zone_lines(partner)) return IrrReason::kBof;
  if (e.in.occupied) return IrrReason::kOcc;
  if (saf4_reached(e)) return IrrReason::kSaf;
  return IrrReason::kNone;
}

// The engine never opens a disabled line (irrigation §4.2, §6.3), so a fertilizer run
// with a prewet or a flush needs its clean partner enabled.
inline bool partner_disabled(const IrrEngine& e, uint8_t line, uint16_t prewet_s, uint16_t flush_s) {
  return is_fert(line) && (prewet_s > 0 || flush_s > 0) && !line_enabled(e, kCleanPartner[line]);
}

// Does a running fertilizer run still use its partner (a prewet or flush under way or to come)?
inline bool fert_uses_partner(const IrrRun& r) {
  return r.stage == IrrStage::kPrewet || r.stage == IrrStage::kFlush || r.flush_s > 0;
}

inline uint16_t physical_misters(const IrrEngine& e) {
  return uint16_t(e.in.relay_on_mask & kMistManifoldMask & ~e.engine_mask);
}

// The relays the active run holds open now.
inline uint16_t run_relays(const IrrRun& r) {
  uint16_t m = 0;
  switch (r.kind) {
    case IrrRunKind::kDrip:
      if (r.open) m |= irr_bit(r.line);
      break;
    case IrrRunKind::kWindow:
      if (r.pulse_open) m |= irr_bit(r.line);
      break;
    case IrrRunKind::kFert:
      if (r.stage == IrrStage::kFeed) {
        if (r.open || r.pulse_open) m |= irr_bit(r.line) | irr_bit(kL03);
      } else if (r.open) {
        m |= irr_bit(r.partner);
      }
      break;
    case IrrRunKind::kNone:
    default:
      break;
  }
  return m;
}

// The relay the run's current stage uses (for stop scope and line_busy).
inline uint8_t run_stage_line(const IrrRun& r) {
  if (r.kind == IrrRunKind::kFert && r.stage != IrrStage::kFeed) return r.partner;
  return r.line;
}

inline bool run_on_drip_manifold(const IrrRun& r) {
  return r.kind != IrrRunKind::kNone && is_drip_line(run_stage_line(r));
}

inline void emit(IrrEngine& e, uint8_t line, uint16_t slot_id, uint16_t day, IrrOutcome o, IrrReason r) {
  irr_ring_push(*e.rtc, line, slot_id, day, o, r);
}

template <typename Mutate>
inline bool commit(IrrEngine& e, Mutate&& mutate) {
  if (e.persist.save == nullptr || e.persist.sync == nullptr) return false;
  IrrClaims candidate = e.claims;
  if (!mutate(candidate)) return false;
  return irr_commit_claims(
      e.claims, candidate, [&](const IrrClaims& c) { return e.persist.save(c, e.persist.ctx); },
      [&]() { return e.persist.sync(e.persist.ctx); });
}

inline bool commit_claim(IrrEngine& e, uint16_t slot_id, uint16_t day) {
  if (irr_claimed(e.claims, slot_id, day)) return true;
  return commit(e, [&](IrrClaims& c) { return irr_claims_add(c, slot_id, day, &e.prog); });
}

inline bool commit_fert(IrrEngine& e, IrrStage stage, const IrrRun& r, bool with_claim) {
  return commit(e, [&](IrrClaims& c) {
    if (with_claim && r.slot_id != 0 && !irr_claims_add(c, r.slot_id, r.occ_day, &e.prog)) return false;
    c.fert_stage = uint8_t(stage);
    c.fert_line = stage == IrrStage::kNone ? kNoLine : r.line;
    c.fert_slot = stage == IrrStage::kNone ? 0 : r.slot_id;
    c.fert_day = stage == IrrStage::kNone ? 0 : r.occ_day;
    c.fert_flush_s = stage == IrrStage::kNone ? 0 : r.flush_s;
    return true;
  });
}

// Mirror the active clean run into the RTC block (or clear it).
inline void rtc_mirror_run(IrrEngine& e) {
  IrrRtcRun m{};
  const IrrRun& r = e.run;
  if (r.kind == IrrRunKind::kDrip || r.kind == IrrRunKind::kWindow) {
    m.kind = uint8_t(r.kind);
    m.line = r.line;
    m.cause = uint8_t(r.cause);
    m.count = r.count;
    m.slot_id = r.slot_id;
    m.occ_day = r.occ_day;
    m.on_s = r.on_s;
    m.gap_s = r.gap_s;
    m.done = r.done;
    m.remaining_s = uint16_t((r.remaining_ms + 999u) / 1000u);
    m.start_epoch = r.start_epoch;
  }
  if (std::memcmp(&m, &e.rtc->run, sizeof(m)) != 0) {
    e.rtc->run = m;
    irr_rtc_seal(*e.rtc);
  }
}

// Valve-open time delivered by a continuous open since it was last accounted.
inline void account_open(IrrRun& r, uint32_t now_ms) {
  if (!r.open) return;
  const uint32_t d = now_ms - r.open_since_ms;
  r.remaining_ms = d >= r.remaining_ms ? 0u : r.remaining_ms - d;
  r.open_since_ms = now_ms;
}

inline void open_continuous(IrrEngine& e, IrrRun& r, uint32_t now_ms) {
  r.open = true;
  r.open_since_ms = now_ms;
  const uint32_t ep = epoch_now(e, now_ms);
  if (r.start_epoch == 0) r.start_epoch = ep;
  r.end_epoch = ep == 0 ? 0u : ep + (r.remaining_ms + 999u) / 1000u;
  if (r.end_epoch < r.start_epoch) r.end_epoch = r.start_epoch;
}

inline void close_continuous(IrrRun& r, uint32_t now_ms) {
  account_open(r, now_ms);
  r.open = false;
}

inline void set_zone_off(IrrEngine& e, uint8_t idx, uint32_t now_ms) {
  const uint8_t z = kMistZone[idx];
  if (z < kZoneCount) {
    e.zone_off_known[z] = true;
    e.zone_off_ms[z] = now_ms;
  }
}

// A mister relay closes now, in this call: stamp its zone at the moment it closed. The next
// tick sees it go OFF in its relay sample and leaves this stamp alone, so the zone fence (and
// a window's pulse spacing) counts from the real close, not from the tick after it.
inline void stamp_close(IrrEngine& e, uint8_t idx, uint32_t now_ms) {
  set_zone_off(e, idx, now_ms);
  e.off_stamped = uint16_t(e.off_stamped | irr_bit(idx));
}

inline void close_pulse(IrrEngine& e, IrrRun& r, uint32_t now_ms) {
  if (!r.pulse_open) return;
  r.pulse_open = false;
  stamp_close(e, r.line, now_ms);
}

inline void clear_run(IrrEngine& e) {
  e.run = IrrRun{};
  rtc_mirror_run(e);
}

// Settle a clean run (drip or window): publish its outcome and free the manifold.
inline void end_clean_run(IrrEngine& e, uint32_t now_ms, IrrOutcome o, IrrReason why) {
  IrrRun& r = e.run;
  close_continuous(r, now_ms);
  close_pulse(e, r, now_ms);
  emit(e, r.line, r.slot_id, r.occ_day, o, why);
  clear_run(e);
}

// Clear the journal, then publish the fertilizer run's settled outcome. The outcome goes
// out only once the cleared journal is saved and synced, as a claim does, so a reset
// never ends the run a second time from a stale journal (recover()). A clear that does
// not save is retried each tick; 10 min on, the outcome is dropped unpublished, and a
// reset would end the run once, from its journal.
inline void settle_fert(IrrEngine& e, uint32_t now_ms) {
  IrrRun& r = e.run;
  if (!r.to_settle) {
    r.to_settle = true;
    r.settle_since_ms = now_ms;
  }
  if (commit_fert(e, IrrStage::kNone, r, false)) {
    emit(e, r.line, r.slot_id, r.occ_day, r.fert_outcome, r.fert_reason);
    clear_run(e);
  } else if (irr_reached(now_ms, r.settle_since_ms + kSettleGiveUpMs)) {
    clear_run(e);
  }
}

// End the fertilizer part of a run (prewet or feed): the outcome is settled and the
// flush follows, journaled at the next step. `flush` false: disabling the line ended
// it, so no flush (D-IRP-15).
inline void end_fert_part(IrrEngine& e, uint32_t now_ms, IrrOutcome o, IrrReason why, bool flush) {
  IrrRun& r = e.run;
  close_continuous(r, now_ms);
  close_pulse(e, r, now_ms);
  r.open = false;
  r.fert_outcome = o;
  r.fert_reason = why;
  if (flush && r.flush_s > 0) {
    r.to_flush = true;
    r.flush_since_ms = now_ms;
    return;
  }
  settle_fert(e, now_ms);
}

// Journal the flush tail, publish the outcome, and arm the flush on the partner.
inline void begin_flush(IrrEngine& e, uint32_t now_ms) {
  IrrRun& r = e.run;
  r.to_flush = false;
  if (!line_enabled(e, r.partner)) {
    // Never open a disabled line: the outcome stands, with no flush.
    end_fert_part(e, now_ms, r.fert_outcome, r.fert_reason, false);
    return;
  }
  if (!commit_fert(e, IrrStage::kFlush, r, false)) {
    // Not journaled: try again next tick for as long as a flush may wait, then publish
    // the outcome with no flush (an unjournaled flush cut by a reset would end the run twice).
    if (!irr_reached(now_ms, r.flush_since_ms + kFlushGiveUpMs)) {
      r.to_flush = true;
      return;
    }
    end_fert_part(e, now_ms, r.fert_outcome, r.fert_reason, false);
    return;
  }
  emit(e, r.line, r.slot_id, r.occ_day, r.fert_outcome, r.fert_reason);
  r.stage = IrrStage::kFlush;
  r.remaining_ms = uint32_t(r.flush_s) * 1000u;
  r.open = false;
  r.pulse_open = false;
  r.paused = false;
  r.flush_since_ms = now_ms;
  r.start_epoch = 0;
  r.end_epoch = 0;
  r.block = IrrReason::kNone;
}

inline void finish_flush(IrrEngine& e, uint32_t now_ms) {
  IrrRun& r = e.run;
  close_continuous(r, now_ms);
  commit_fert(e, IrrStage::kNone, r, false);  // best effort: a stale journal only repeats the flush
  clear_run(e);
}

// Can a continuous open on the drip manifold start now? Drips never overlap mist or
// fog, so every mister and fog relay must read OFF and no VPD pulse may hold a grant.
inline bool drip_clear(const IrrEngine& e) {
  return e.vpd_line == kNoLine && (e.in.relay_on_mask & (kMistManifoldMask | kFogMask) & ~e.engine_mask) == 0;
}

// Can the engine open a mister now? One mister relay at a time; at a free pulse
// boundary a waiting VPD request takes its turn (always, in a VPD emergency).
inline bool mist_free_for(const IrrEngine& e, uint8_t idx, uint32_t now_ms) {
  if (e.vpd_line != kNoLine) return false;
  if (physical_misters(e) != 0) return false;
  if (e.vpd_wait_line != kNoLine && !irr_reached(now_ms, e.vpd_wait_ms + kVpdFreshMs) &&
      kMistZone[e.vpd_wait_line] != kMistZone[idx] && (!e.sched_turn || e.in.vpd_emergency)) {
    return false;
  }
  return !zone_fenced(e, idx, now_ms);
}

inline void start_pulse(IrrEngine& e, IrrRun& r, uint32_t now_ms) {
  r.pulse_open = true;
  r.pulse_end_ms = now_ms + uint32_t(r.on_s) * 1000u;
  r.block = IrrReason::kNone;
  e.sched_turn = false;  // after a scheduled pulse the next free boundary is VPD's
  if (r.start_epoch == 0) {
    const uint32_t ep = epoch_now(e, now_ms);
    r.start_epoch = ep;
    const uint32_t span = uint32_t(r.count) * r.on_s + uint32_t(r.count - 1) * r.gap_s;
    r.end_epoch = ep == 0 ? 0u : ep + span;
  }
}

// The pulse train of a window or a mister feed: close, then open the next pulse when
// it is due and nothing blocks it. Returns true while pulses remain.
inline bool advance_pulses(IrrEngine& e, IrrRun& r, uint32_t now_ms, bool fert_feed) {
  const uint16_t min_off = e.in.mister_min_off_s;
  if (r.pulse_open) {
    IrrReason cut = IrrReason::kNone;
    if (e.in.sensor_fault) cut = IrrReason::kSf;
    else if (e.in.occupied) cut = IrrReason::kOcc;
    else if (e.in.latch_mask & zone_lines(r.line)) cut = IrrReason::kFlt;
    if (irr_reached(now_ms, r.pulse_end_ms)) {
      close_pulse(e, r, now_ms);
      ++r.done;
      const uint32_t gap = uint32_t(r.gap_s > min_off ? r.gap_s : min_off) * 1000u;
      r.next_pulse_ms = now_ms + gap;
    } else if (cut != IrrReason::kNone) {
      close_pulse(e, r, now_ms);  // not counted: delivered again when it may run
      r.block = cut;
      r.last_block = cut;
      r.next_pulse_ms = now_ms;
    }
  }
  r.fog_wait = false;
  if (r.pulse_open) return true;
  if (r.done >= r.count) return false;
  if (!irr_reached(now_ms, r.next_pulse_ms)) {
    r.block = IrrReason::kNone;
    return true;
  }
  IrrReason why = pulse_skip(e, r.line, fert_feed, r.degraded_only);
  // A fallback pulse needs daylight; with a valid clock advance_run ends the window at sunset.
  if (why == IrrReason::kNone && r.degraded_only && !irr_daylight(e.in.clock)) why = IrrReason::kClk;
  if (why == IrrReason::kNone && !mist_free_for(e, r.line, now_ms)) why = IrrReason::kMan;
  if (why == IrrReason::kNone && fert_feed && (e.in.relay_on_mask & kFogMask)) {
    // A feed pulse opens L03: fog must read OFF first. l03_hold asks the climate path for it.
    r.fog_wait = true;
    why = IrrReason::kMan;
  }
  if (why != IrrReason::kNone) {
    r.block = why;
    r.last_block = why;
    return true;
  }
  start_pulse(e, r, now_ms);
  return true;
}

}  // namespace detail

// ── Engine set-up ────────────────────────────────────────────────────────────

// Initialise the engine at boot. `rtc` is the RTC_NOINIT block (a plain struct in the
// tests); `power_on` zeroes it. `stored_claims` is the NVS claims record, if any. The
// program starts as the ROM default until irr_use_program() loads a stored one.
inline void irr_engine_init(IrrEngine& e, IrrRtcBlock* rtc, const IrrPersist& persist, bool power_on,
                            const IrrClaims* stored_claims) {
  e = IrrEngine{};
  e.rtc = rtc;
  e.persist = persist;
  irr_rtc_validate(*rtc, power_on, 0);
  e.claims = (stored_claims != nullptr && irr_claims_valid(*stored_claims)) ? *stored_claims : irr_claims_empty();
  e.prog = irr_rom_default();
  e.prog_nvs = false;
  e.in = irr_inputs_idle();
}

// Run a stored program (src=nvs). The caller decoded it with irr_decode().
inline void irr_use_program(IrrEngine& e, const IrrProgram& p, const uint8_t sha256[32]) {
  e.prog = p;
  e.prog_nvs = true;
  std::memcpy(e.prog_hash, sha256, 32);
}

// Run the ROM default (src=rom, h=0000000000000000).
inline void irr_use_rom_default(IrrEngine& e) {
  e.prog = irr_rom_default();
  e.prog_nvs = false;
  std::memset(e.prog_hash, 0, sizeof(e.prog_hash));
}

// A committed push: the new program becomes active at the next irr_tick().
inline void irr_stage_program(IrrEngine& e, const IrrProgram& p, const uint8_t sha256[32]) {
  e.staged_prog = p;
  std::memcpy(e.staged_hash, sha256, 32);
  e.staged = true;
  e.reject = IrrReject::kNone;
}

// A rejected push keeps the old program; irr_state carries the reject (j=).
inline void irr_set_reject(IrrEngine& e, IrrReject why) { e.reject = why; }

// Climate mister water and fog water (centi-gallons; the caller carries the fraction).
inline void irr_count_water(IrrEngine& e, uint32_t centigal) {
  if (e.rtc == nullptr || centigal == 0) return;
  const uint32_t sum = e.rtc->water_centigal + centigal;
  e.rtc->water_centigal = sum < e.rtc->water_centigal ? UINT32_MAX : sum;
  irr_rtc_seal(*e.rtc);
}

// A drip run holds the water (irrigation_water_conflict for climate mist and fog).
inline bool irr_drip_active(const IrrEngine& e) {
  const IrrRun& r = e.run;
  // A run whose flush is not journaled yet, or whose outcome waits on its journal, holds nothing.
  if (!detail::run_on_drip_manifold(r) || r.to_flush || r.to_settle) return false;
  if (r.kind == IrrRunKind::kDrip || r.stage == IrrStage::kFlush) return !r.paused && !r.yielding;
  return true;  // a fertilizer prewet or feed is never paused
}

// ── Outputs ──────────────────────────────────────────────────────────────────

namespace detail {

inline void add_wait(IrrStateFields& s, uint8_t line, IrrReason why, uint32_t until) {
  if (s.wait_count >= kMaxSlots + 1) return;
  s.waits[s.wait_count++] = {line, why, until};
}

inline IrrRunToken run_token(const IrrRun& r) {
  IrrRunToken t{};
  t.valid = true;
  t.line = run_stage_line(r);
  t.slot_id = r.slot_id;
  t.start_epoch = r.start_epoch;
  t.end_epoch = r.end_epoch < r.start_epoch ? r.start_epoch : r.end_epoch;
  if (r.kind == IrrRunKind::kFert) {
    t.cause = r.stage == IrrStage::kPrewet ? IrrCause::kPrewet
              : r.stage == IrrStage::kFeed ? IrrCause::kFeed
                                           : IrrCause::kFlush;
  } else {
    t.cause = r.cause;
  }
  return t;
}

inline uint32_t holder_end_epoch(const IrrEngine& e) {
  if (e.run.kind != IrrRunKind::kNone) return e.run.end_epoch;
  if (e.vpd_line != kNoLine) return e.vpd_end_epoch;
  return 0;
}

inline void build_outputs(IrrEngine& e, uint32_t now_ms, IrrOutputs& out) {
  out = IrrOutputs{};
  const IrrRun& r = e.run;
  // #1336: L03 opens only with a fertilized relay and never with a clean one.
  const uint16_t relays = irr_master_rule(run_relays(r));
  e.engine_mask = relays;
  out.relay_mask = relays;
  out.drip_on = kNoLine;
  out.mist_on = kNoLine;
  for (uint8_t i = 0; i < kLineCount; ++i) {
    if (!(relays & irr_bit(i))) continue;
    if (is_drip_line(i)) out.drip_on = i;
    if (is_mister(i)) out.mist_on = i;
  }
  if (out.mist_on != kNoLine) {
    out.mist_cmd_s = r.pulse_open                  ? r.on_s
                     : r.stage == IrrStage::kPrewet ? r.prewet_s
                     : r.stage == IrrStage::kFlush  ? r.flush_s
                                                    : r.on_s;
  }
  out.l03_on = (relays & irr_bit(kL03)) != 0;
  out.l03_hold = out.l03_on || (r.kind == IrrRunKind::kFert && r.stage == IrrStage::kFeed && r.fog_wait);
  out.grant_mask = uint16_t(relays | (e.vpd_line != kNoLine ? irr_bit(e.vpd_line) : 0u));
  out.drip_active = irr_drip_active(e);
  out.saf4_tripped = saf4_reached(e);
  out.soft_budget_reached = budget_reached(e);
  out.fog_run = r.kind == IrrRunKind::kFog && r.remaining_ms > 0;

  IrrStateFields& s = out.state;
  s.reject = e.reject;
  // A run whose current stage has not opened yet (a drip waiting for a VPD pulse or fog to end,
  // a feed waiting for fog, a flush the next tick opens) has no start to publish. With a valid
  // clock it reads as waiting for the manifold (w=), never as a run token with no start time.
  const bool pulse_train =
      r.kind == IrrRunKind::kWindow || (r.kind == IrrRunKind::kFert && r.stage == IrrStage::kFeed && is_mister(r.line));
  const bool run_unopened = r.kind != IrrRunKind::kNone && r.block == IrrReason::kNone && !r.to_flush &&
                            !r.to_settle && e.in.clock.valid && r.start_epoch == 0 && run_relays(r) == 0 &&
                            (!pulse_train || r.done == 0);
  const bool run_blocked = r.kind != IrrRunKind::kNone && (r.block != IrrReason::kNone || run_unopened);
  if (r.kind != IrrRunKind::kNone && !run_blocked && !r.to_flush && !r.to_settle) {
    const IrrRunToken t = run_token(r);
    if (is_drip_line(t.line)) s.drip = t;
    else s.mist = t;
  }
  if (e.vpd_line != kNoLine) {
    s.mist = {true, e.vpd_line, IrrCause::kVpd, 0, e.vpd_start_epoch,
              e.vpd_end_epoch < e.vpd_start_epoch ? e.vpd_start_epoch : e.vpd_end_epoch};
  }
  if (run_blocked) {
    // The active run waits on someone else: a VPD pulse names its planned end; fog or an
    // unknown holder names none.
    const IrrReason why = r.block != IrrReason::kNone ? r.block : IrrReason::kMan;
    const uint32_t until = why == IrrReason::kMan && e.vpd_line != kNoLine ? s.mist.end_epoch : 0u;
    add_wait(s, run_stage_line(r), why, until);
  }
  // Pending occurrences, earliest due first.
  bool listed[kMaxSlots] = {};
  for (;;) {
    int best = -1;
    for (int i = 0; i < kMaxSlots; ++i) {
      const IrrPending& p = e.pending[i];
      if (!p.used || listed[i]) continue;
      if (best < 0 || p.planned_epoch < e.pending[best].planned_epoch ||
          (p.planned_epoch == e.pending[best].planned_epoch && p.slot_id < e.pending[best].slot_id)) {
        best = i;
      }
    }
    if (best < 0) break;
    listed[best] = true;
    const IrrPending& p = e.pending[best];
    uint8_t line = kNoLine;
    for (uint8_t k = 0; k < e.prog.slot_count; ++k) {
      if (e.prog.slots[k].slot_id == p.slot_id) line = e.prog.slots[k].line;
    }
    if (line != kNoLine && p.reason != IrrReason::kNone) add_wait(s, line, p.reason, p.until_epoch);
  }
  (void)now_ms;

  out.state_changed = !e.last_state_valid || !irr_state_equal(s, e.last_state);
  e.last_state = s;
  e.last_state_valid = true;
  out.next_seq = e.rtc->next_seq;
  out.events_changed = e.rtc->next_seq != e.last_seq || e.rtc->ring_count != e.last_ring_count;
  e.last_seq = e.rtc->next_seq;
  e.last_ring_count = e.rtc->ring_count;
  out.program_nvs = e.prog_nvs;
  if (e.prog_nvs) std::memcpy(out.program_hash8, e.prog_hash, 8);
}

// Runtime per line and per mister zone from the relays that read ON, whatever
// opened them (a VPD pulse included). Integer seconds; the sub-second rest carries.
inline void accrue(IrrEngine& e, uint16_t on_mask, uint32_t dt_ms) {
  if (dt_ms == 0 || (on_mask & kWetMask) == 0) return;
  for (uint8_t i = 0; i < kLineCount; ++i) {
    if (!(on_mask & irr_bit(i))) continue;
    e.carry_ms[i] += dt_ms;
    const uint32_t s = e.carry_ms[i] / 1000u;
    e.carry_ms[i] %= 1000u;
    if (s == 0) continue;
    e.rtc->line_on_s[i] += s;
    if (kMistZone[i] < kZoneCount) e.rtc->zone_pulse_s[kMistZone[i]] += s;
  }
  irr_rtc_seal(*e.rtc);
}

// Boot recovery, on the first tick. A journaled fertilizer stage means a reset cut a
// fertilizer run: it never resumes. Its outcome is short (rst) unless only its flush
// was left, and its flush runs again. Otherwise a clean run in the RTC block (a soft
// reset kept it) resumes its remainder, if its claim is in NVS.
inline void recover(IrrEngine& e, uint32_t now_ms) {
  IrrClaims& c = e.claims;
  const auto stage = IrrStage(c.fert_stage);
  if (stage != IrrStage::kNone && c.fert_line < kLineCount && is_fert(c.fert_line)) {
    IrrRun r{};
    r.kind = IrrRunKind::kFert;
    r.line = c.fert_line;
    r.partner = kCleanPartner[c.fert_line];
    r.slot_id = c.fert_slot;
    r.occ_day = c.fert_day;
    r.flush_s = c.fert_flush_s;
    r.stage = stage;
    e.run = r;
    rtc_mirror_run(e);
    // The flush never opens a disabled partner (the engine never opens a disabled line).
    const bool partner_on = line_enabled(e, r.partner);
    if (stage == IrrStage::kFlush) {
      // The outcome is already out: run the flush again, quietly.
      e.run.remaining_ms = uint32_t(r.flush_s) * 1000u;
      e.run.flush_since_ms = now_ms;
      if (r.flush_s == 0 || !partner_on) finish_flush(e, now_ms);
    } else {
      end_fert_part(e, now_ms, IrrOutcome::kShort, IrrReason::kRst, partner_on);
      if (e.run.kind == IrrRunKind::kFert && e.run.to_flush) begin_flush(e, now_ms);
    }
    return;
  }
  if (stage != IrrStage::kNone) commit_fert(e, IrrStage::kNone, IrrRun{}, false);  // unusable journal

  const IrrRtcRun& m = e.rtc->run;
  const auto kind = IrrRunKind(m.kind);
  const bool shape_ok = (kind == IrrRunKind::kDrip && m.line < kLineCount &&
                         (kLineKind[m.line] == IrrLineKind::kDrip)) ||
                        (kind == IrrRunKind::kWindow && is_clean_mister(m.line) && m.count >= 1 &&
                         m.done < m.count && m.on_s >= 10);
  if (!shape_ok || m.cause >= kCauseCount) {
    clear_run(e);
    return;
  }
  if (m.slot_id != 0 && !irr_claimed(c, m.slot_id, m.occ_day)) {
    clear_run(e);  // never claimed: never resumed
    return;
  }
  IrrRun r{};
  r.kind = kind;
  r.line = m.line;
  r.partner = kNoLine;
  r.cause = IrrCause(m.cause);
  r.slot_id = m.slot_id;
  r.occ_day = m.occ_day;
  r.on_s = m.on_s;
  r.gap_s = m.gap_s;
  r.count = m.count;
  r.done = uint8_t(m.done);
  r.degraded_only = r.cause == IrrCause::kFallback;
  r.start_epoch = m.start_epoch;
  r.remaining_ms = uint32_t(m.remaining_s) * 1000u;
  r.next_pulse_ms = now_ms;
  if (kind == IrrRunKind::kWindow) {
    const uint32_t left = uint32_t(r.count - r.done);
    const uint32_t span = left * r.on_s + (left - 1u) * r.gap_s;
    r.deadline_ms = now_ms + (span - r.on_s) * 1000u + kWindowGraceMs;
    r.end_epoch = r.start_epoch == 0 ? 0u : epoch_now(e, now_ms) + span;
  } else {
    r.end_epoch = r.start_epoch == 0 ? 0u : epoch_now(e, now_ms) + m.remaining_s;
  }
  e.run = r;
  if (!line_enabled(e, r.line)) {
    end_clean_run(e, now_ms, IrrOutcome::kStopped, IrrReason::kDis);
    return;
  }
  if (kind == IrrRunKind::kDrip && r.remaining_ms == 0) end_clean_run(e, now_ms, IrrOutcome::kOk, IrrReason::kNone);
}

// A pushed program takes over. A run whose line is now disabled ends: a clean run
// `stopped` (dis), a fertilizer run with no flush (D-IRP-15). A fertilizer run whose
// clean partner is disabled while it still needs it ends the same way, because the
// engine never opens a disabled line; a feed that already ended keeps its outcome.
// Membership changes do not cut a running window; they apply at the next start.
inline void apply_program(IrrEngine& e, uint32_t now_ms) {
  e.prog = e.staged_prog;
  std::memcpy(e.prog_hash, e.staged_hash, 32);
  e.prog_nvs = true;
  e.staged = false;
  IrrRun& r = e.run;
  const bool line_off = r.kind != IrrRunKind::kNone && !line_enabled(e, r.line);
  const bool partner_off =
      r.kind == IrrRunKind::kFert && fert_uses_partner(r) && !line_enabled(e, r.partner);
  if ((line_off || partner_off) && !r.to_settle) {
    if (r.kind == IrrRunKind::kFert) {
      if (r.stage == IrrStage::kFlush) {
        finish_flush(e, now_ms);
      } else if (r.to_flush) {
        end_fert_part(e, now_ms, r.fert_outcome, r.fert_reason, false);
      } else {
        end_fert_part(e, now_ms, IrrOutcome::kStopped, IrrReason::kDis, false);
      }
    } else {
      end_clean_run(e, now_ms, IrrOutcome::kStopped, IrrReason::kDis);
    }
  }
  if (e.vpd_line != kNoLine && (!line_enabled(e, e.vpd_line) || !vpd_member(e, e.vpd_line))) {
    e.vpd_line = kNoLine;
  }
  // Pending occurrences whose slot left the program are dropped (never started, no outcome).
  for (IrrPending& p : e.pending) {
    if (!p.used) continue;
    bool live = false;
    for (uint8_t k = 0; k < e.prog.slot_count; ++k) live = live || e.prog.slots[k].slot_id == p.slot_id;
    if (!live) p = IrrPending{};
  }
}

// The climate VPD pulse loses its grant on SENSOR_FAULT, SAF-4, a disabled line, a
// lost VPD membership or a latch, or when it was never released.
inline void vpd_housekeeping(IrrEngine& e, uint32_t now_ms) {
  if (e.vpd_line != kNoLine) {
    const uint8_t v = e.vpd_line;
    const bool lapsed = irr_reached(now_ms, e.vpd_since_ms + uint32_t(e.vpd_on_s) * 1000u + kVpdGrantSlackMs);
    if (e.in.sensor_fault || saf4_reached(e) || !line_enabled(e, v) || !vpd_member(e, v) ||
        (e.in.latch_mask & irr_bit(v)) || lapsed) {
      e.vpd_line = kNoLine;
      set_zone_off(e, v, now_ms);
      e.sched_turn = true;
    }
  }
  if (e.vpd_wait_line != kNoLine && irr_reached(now_ms, e.vpd_wait_ms + kVpdFreshMs)) e.vpd_wait_line = kNoLine;
}

inline bool vpd_waiting(const IrrEngine& e, uint32_t now_ms) {
  return e.vpd_wait_line != kNoLine && !irr_reached(now_ms, e.vpd_wait_ms + kVpdFreshMs);
}

// A clean drip (or the clean flush tail on a drip partner): SAFETY_COOL and a VPD
// emergency pause it at once and it resumes its remainder; it also yields to climate
// demand after climate_yield_min continuous minutes, for one VPD pulse or up to 120 s
// of fog (D-IRP-03). A fertilizer prewet or feed is never paused, so it never comes here.
inline void advance_drip_like(IrrEngine& e, IrrRun& r, uint32_t now_ms, uint32_t dt_ms) {
  const bool pause_now = e.in.safety_cool || e.in.vpd_emergency;
  if (r.open) {
    account_open(r, now_ms);
    if (r.remaining_ms == 0) return;  // the caller finishes the run
    if (pause_now) {
      close_continuous(r, now_ms);
      r.paused = true;
      const uint32_t ep = epoch_now(e, now_ms);
      r.end_epoch = ep == 0 ? 0u : ep + (r.remaining_ms + 999u) / 1000u;
      return;
    }
    const bool demand = e.in.climate_demand || vpd_waiting(e, now_ms);
    r.yield_accum_ms = demand ? r.yield_accum_ms + dt_ms : 0u;
    if (r.yield_accum_ms >= uint32_t(e.prog.climate_yield_min) * 60000u) {
      close_continuous(r, now_ms);
      r.yielding = true;
      r.yield_start_ms = now_ms;
      r.yield_fog_ms = 0;
      r.yield_climate_seen = false;
      r.yield_vpd_done = false;
      r.yield_accum_ms = 0;
      const uint32_t ep = epoch_now(e, now_ms);
      r.end_epoch = ep == 0 ? 0u : ep + (r.remaining_ms + 999u) / 1000u;
    }
    return;
  }
  if (r.yielding) {
    const bool fog_on = (e.in.relay_on_mask & kFogMask) != 0;
    const bool mist_on = (e.in.relay_on_mask & kMistManifoldMask) != 0 || e.vpd_line != kNoLine;
    if (fog_on) r.yield_fog_ms += dt_ms;
    if (fog_on || mist_on) r.yield_climate_seen = true;
    const bool done = r.yield_vpd_done || r.yield_fog_ms >= kFogYieldMaxMs ||
                      (!r.yield_climate_seen && irr_reached(now_ms, r.yield_start_ms + kYieldIdleMs)) ||
                      (r.yield_climate_seen && !fog_on && !mist_on && !e.in.climate_demand);
    if (!done) return;
    r.yielding = false;
  }
  if (r.paused && !pause_now) r.paused = false;
  if (r.paused || pause_now) {
    r.paused = true;
    return;
  }
  if (drip_clear(e)) open_continuous(e, r, now_ms);
}

inline void advance_run(IrrEngine& e, uint32_t now_ms, uint32_t dt_ms) {
  IrrRun& r = e.run;
  switch (r.kind) {
    case IrrRunKind::kNone:
      return;

    case IrrRunKind::kDrip: {
      account_open(r, now_ms);
      if (drip_cap_reason(e, r.line) != IrrReason::kNone && r.remaining_ms > 0) {
        end_clean_run(e, now_ms, IrrOutcome::kCap, IrrReason::kCap);
        return;
      }
      if (r.remaining_ms == 0) {
        end_clean_run(e, now_ms, IrrOutcome::kOk, IrrReason::kNone);
        return;
      }
      advance_drip_like(e, r, now_ms, dt_ms);
      if (r.remaining_ms == 0) end_clean_run(e, now_ms, IrrOutcome::kOk, IrrReason::kNone);
      return;
    }

    case IrrRunKind::kWindow: {
      const IrrReason cap = mist_cap_reason(e, r.line, true);
      if (cap != IrrReason::kNone && r.done < r.count) {
        end_clean_run(e, now_ms, IrrOutcome::kCap, cap);
        return;
      }
      if (r.degraded_only && e.in.clock.valid && !irr_daylight(e.in.clock) && !r.pulse_open && r.done < r.count) {
        // A fallback window runs in daylight only (D-IRP-12): at sunset it ends where it is.
        end_clean_run(e, now_ms, IrrOutcome::kShort, IrrReason::kNone);
        return;
      }
      const bool more = advance_pulses(e, r, now_ms, false);
      if (!more) {
        end_clean_run(e, now_ms, IrrOutcome::kOk, IrrReason::kNone);
        return;
      }
      if (!r.pulse_open && irr_reached(now_ms, r.deadline_ms)) {
        end_clean_run(e, now_ms, IrrOutcome::kShort, r.last_block);
      }
      return;
    }

    case IrrRunKind::kFog: {
      // A fog run-once: the climate path fogs while it asks (fog_run) and its rails allow, and
      // the run counts the seconds the fog relay reads ON. SAF-4 and the soft budget end it
      // `cap`, SENSOR_FAULT ends it `short`; occupancy and the other climate holds keep it
      // waiting until its deadline (`short`, with the last hold).
      const bool fog_on = (e.in.relay_on_mask & kFogMask) != 0;
      if (fog_on) r.remaining_ms = dt_ms >= r.remaining_ms ? 0u : r.remaining_ms - dt_ms;
      if (r.remaining_ms == 0) {
        end_clean_run(e, now_ms, IrrOutcome::kOk, IrrReason::kNone);
        return;
      }
      const IrrReason cap = saf4_reached(e) ? IrrReason::kSaf : budget_reached(e) ? IrrReason::kBud : IrrReason::kNone;
      if (cap != IrrReason::kNone) {
        end_clean_run(e, now_ms, IrrOutcome::kCap, cap);
        return;
      }
      if (e.in.sensor_fault) {
        end_clean_run(e, now_ms, IrrOutcome::kShort, IrrReason::kSf);
        return;
      }
      if (!fog_on && dt_ms > 0) r.last_block = e.in.occupied ? IrrReason::kOcc : IrrReason::kMan;
      if (irr_reached(now_ms, r.deadline_ms)) end_clean_run(e, now_ms, IrrOutcome::kShort, r.last_block);
      return;
    }

    case IrrRunKind::kFert: {
      if (r.to_settle) {
        settle_fert(e, now_ms);
        return;
      }
      if (r.to_flush) {
        begin_flush(e, now_ms);
        return;
      }
      const bool mist_partner = is_mister(r.partner);
      if (r.stage == IrrStage::kFlush) {
        // The clean flush tail: the soft budget and the caps count it but do not stop it.
        // A mist flush is mist: it waits out SENSOR_FAULT, a latch or back-off, confirmed
        // occupancy, the SAF-4 ceiling and a VPD emergency (VPD goes first). A drip flush
        // pauses for SAFETY_COOL and a VPD emergency.
        account_open(r, now_ms);
        if (r.open && r.remaining_ms == 0) {
          finish_flush(e, now_ms);
          return;
        }
        if (mist_partner) {
          IrrReason hold = mist_tail_hold(e, r.partner);
          if (hold == IrrReason::kNone && e.in.vpd_emergency) hold = IrrReason::kMan;
          if (r.open && hold != IrrReason::kNone) {
            close_continuous(r, now_ms);
            r.block = hold;
          } else if (!r.open) {
            if (hold != IrrReason::kNone) {
              r.block = hold;
            } else if (e.vpd_line != kNoLine || physical_misters(e) != 0 || zone_fenced(e, r.partner, now_ms)) {
              r.block = IrrReason::kMan;
            } else {
              r.block = IrrReason::kNone;
              open_continuous(e, r, now_ms);
            }
          }
        } else {
          advance_drip_like(e, r, now_ms, dt_ms);
          r.block = IrrReason::kNone;
        }
        if (r.remaining_ms == 0) {
          finish_flush(e, now_ms);
          return;
        }
        if (!r.open && irr_reached(now_ms, r.flush_since_ms + kFlushGiveUpMs)) finish_flush(e, now_ms);
        return;
      }

      // Prewet or feed: never paused, never resumed. Stop, SAFETY_COOL or a reset ends it.
      if (e.in.safety_cool) {
        end_fert_part(e, now_ms, IrrOutcome::kPreempted, IrrReason::kNone, true);
        if (e.run.kind == IrrRunKind::kFert && e.run.to_flush) begin_flush(e, now_ms);
        return;
      }
      if (r.stage == IrrStage::kPrewet) {
        account_open(r, now_ms);
        // A prewet on a mister is mist (mist_tail_hold). Once open, a fertilizer run never
        // pauses: a hold ends it (SAF-4 `cap`, the others `short`) and the flush follows.
        // Before it opens it waits, at most 60 min from the run's start.
        const IrrReason hold = mist_partner ? mist_tail_hold(e, r.partner) : IrrReason::kNone;
        if (r.remaining_ms > 0 && hold != IrrReason::kNone && (r.open || hold == IrrReason::kSaf)) {
          end_fert_part(e, now_ms, hold == IrrReason::kSaf ? IrrOutcome::kCap : IrrOutcome::kShort, hold, true);
          if (e.run.kind == IrrRunKind::kFert && e.run.to_flush) begin_flush(e, now_ms);
          return;
        }
        if (r.open && r.remaining_ms == 0) {
          close_continuous(r, now_ms);
          if (!commit_fert(e, IrrStage::kFeed, r, false)) {
            end_fert_part(e, now_ms, IrrOutcome::kShort, IrrReason::kNone, false);
            return;
          }
          r.stage = IrrStage::kFeed;
          r.start_epoch = 0;
          r.remaining_ms = uint32_t(r.on_s) * 1000u;
          r.done = 0;
          r.next_pulse_ms = now_ms;
          if (mist_partner) {
            const uint32_t span = uint32_t(r.count) * r.on_s + uint32_t(r.count - 1) * r.gap_s;
            r.deadline_ms = now_ms + (span - r.on_s) * 1000u + kWindowGraceMs;
          }
        } else if (!r.open) {
          IrrReason wait = hold;
          if (wait == IrrReason::kNone) {
            const bool ready = mist_partner ? (!e.in.vpd_emergency && e.vpd_line == kNoLine &&
                                               physical_misters(e) == 0 && !zone_fenced(e, r.partner, now_ms))
                                            : drip_clear(e);
            if (ready) {
              r.block = IrrReason::kNone;
              open_continuous(e, r, now_ms);
              return;
            }
            wait = IrrReason::kMan;
          }
          r.block = wait;
          r.last_block = wait;
          if (irr_reached(now_ms, r.deadline_ms)) {
            end_fert_part(e, now_ms, IrrOutcome::kShort, wait, true);
            if (e.run.kind == IrrRunKind::kFert && e.run.to_flush) begin_flush(e, now_ms);
          }
          return;
        } else {
          return;
        }
      }
      // Feed.
      if (!mist_partner) {
        account_open(r, now_ms);
        const uint32_t cap = cap_s(e, r.line);
        if (cap > 0 && e.rtc->line_on_s[r.line] >= cap && r.remaining_ms > 0) {
          end_fert_part(e, now_ms, IrrOutcome::kCap, IrrReason::kCap, true);
        } else if (r.open && r.remaining_ms == 0) {
          end_fert_part(e, now_ms, IrrOutcome::kOk, IrrReason::kNone, true);
        } else if (!r.open) {
          r.fog_wait = !drip_clear(e);
          if (!r.fog_wait) open_continuous(e, r, now_ms);
          return;
        } else {
          return;
        }
      } else {
        const IrrReason capr = mist_cap_reason(e, r.line, true);
        if (capr != IrrReason::kNone && r.done < r.count) {
          end_fert_part(e, now_ms, IrrOutcome::kCap, capr, true);
        } else if (!advance_pulses(e, r, now_ms, true)) {
          end_fert_part(e, now_ms, IrrOutcome::kOk, IrrReason::kNone, true);
        } else if (!r.pulse_open && irr_reached(now_ms, r.deadline_ms)) {
          end_fert_part(e, now_ms, IrrOutcome::kShort, r.last_block, true);
        } else {
          return;
        }
      }
      if (e.run.kind == IrrRunKind::kFert && e.run.to_flush) begin_flush(e, now_ms);
      return;
    }
  }
}

inline int find_pending(const IrrEngine& e, uint16_t slot_id, uint16_t day) {
  for (int i = 0; i < kMaxSlots; ++i) {
    if (e.pending[i].used && e.pending[i].slot_id == slot_id && e.pending[i].occ_day == day) return i;
  }
  return -1;
}

inline const IrrSlot* slot_by_id(const IrrEngine& e, uint16_t slot_id) {
  for (uint8_t k = 0; k < e.prog.slot_count; ++k) {
    if (e.prog.slots[k].slot_id == slot_id) return &e.prog.slots[k];
  }
  return nullptr;
}

// Settle an occurrence that never opened a valve: claim it, then publish its outcome.
// A failed claim publishes nothing; the occurrence is tried again next tick.
inline bool settle(IrrEngine& e, IrrPending& p, uint8_t line, IrrOutcome o, IrrReason why) {
  if (!commit_claim(e, p.slot_id, p.occ_day)) return false;
  emit(e, line, p.slot_id, p.occ_day, o, why);
  p = IrrPending{};
  return true;
}

// Outcomes known at run time without opening a valve (irrigation §4.3): a disabled
// line, mist membership, a pause, and the caps, in the order the platform's agenda
// predicts them (verdify_schemas/irrigation.py _skip_code). kNone: it may run.
inline IrrReason settle_reason(const IrrEngine& e, const IrrSlot& s, const IrrPending& p, IrrOutcome& o) {
  o = IrrOutcome::kSkipped;
  if (!line_enabled(e, s.line) || partner_disabled(e, s.line, s.prewet_s, s.flush_s)) return IrrReason::kDis;
  if (is_clean_mister(s.line)) {
    if (s.degraded_only()) {
      if (!vpd_member(e, s.line)) return IrrReason::kNvm;
    } else if (!schedule_member(e, s.line)) {
      return IrrReason::kNsm;
    }
  }
  if (paused_at(e, s.line, p.planned_epoch)) return IrrReason::kPau;
  o = IrrOutcome::kCap;
  const IrrLineKind kind = kLineKind[s.line];
  if (kind == IrrLineKind::kDrip || kind == IrrLineKind::kFertDrip) {
    const IrrReason c = drip_cap_reason(e, s.line);
    if (c != IrrReason::kNone) return c;
  } else {
    const IrrReason c = mist_cap_reason(e, s.line, true);
    if (c != IrrReason::kNone) return c;
  }
  o = IrrOutcome::kOk;
  return IrrReason::kNone;
}

// Why a pending occurrence cannot start now (it keeps waiting); kNone: start it.
inline IrrReason start_block(const IrrEngine& e, const IrrSlot& s, uint32_t now_ms, uint32_t& until) {
  until = 0;
  if (!e.in.clock.valid) return IrrReason::kClk;
  const IrrLineKind kind = kLineKind[s.line];
  const bool mist = is_mister(s.line);
  if (mist && e.in.sensor_fault) return IrrReason::kSf;
  if (e.run.kind != IrrRunKind::kNone) {
    until = holder_end_epoch(e);
    return IrrReason::kMan;
  }
  if (kind == IrrLineKind::kMister) {
    const IrrReason skip = pulse_skip(e, s.line, false, s.degraded_only());
    if (skip != IrrReason::kNone) return skip;
    if (!mist_free_for(e, s.line, now_ms)) {
      until = holder_end_epoch(e);
      return IrrReason::kMan;
    }
    return IrrReason::kNone;
  }
  if (kind == IrrLineKind::kFertMister) {
    // Its prewet and feed are mist on the zone (a prewet on L07 or L09, the feed on L08
    // or L10): the same rails hold its start.
    const IrrReason skip = pulse_skip(e, s.line, true, false);
    if (skip != IrrReason::kNone) return skip;
    // SAFETY_COOL would end it at once (never paused), and in a VPD emergency VPD goes
    // first: wait, as a drip does.
    if (e.in.safety_cool || e.in.vpd_emergency || e.vpd_line != kNoLine || physical_misters(e) != 0 ||
        zone_fenced(e, s.line, now_ms)) {
      until = holder_end_epoch(e);
      return IrrReason::kMan;
    }
    return IrrReason::kNone;
  }
  // Drips and fertilizer drips: survival fog and a VPD emergency hold the water.
  if (e.in.safety_cool || e.in.vpd_emergency) return IrrReason::kMan;
  return IrrReason::kNone;
}

// Start an occurrence or a run-now: claim (and journal a fertilizer stage) first;
// only then is the run active and its first relay may open.
inline bool start_run(IrrEngine& e, IrrRun r, uint32_t now_ms) {
  const IrrLineKind kind = kLineKind[r.line];
  r.partner = kCleanPartner[r.line];
  r.next_pulse_ms = now_ms;
  if (kind == IrrLineKind::kFertDrip || kind == IrrLineKind::kFertMister) {
    r.kind = IrrRunKind::kFert;
    r.stage = r.prewet_s > 0 ? IrrStage::kPrewet : IrrStage::kFeed;
    r.remaining_ms = uint32_t(r.prewet_s > 0 ? r.prewet_s : r.on_s) * 1000u;
    if (r.stage == IrrStage::kPrewet) {
      r.deadline_ms = now_ms + kWindowGraceMs;  // the prewet opens within 60 min, or the run ends short
    } else if (kind == IrrLineKind::kFertMister) {
      const uint32_t span = uint32_t(r.count) * r.on_s + uint32_t(r.count - 1) * r.gap_s;
      r.deadline_ms = now_ms + (span - r.on_s) * 1000u + kWindowGraceMs;
    }
    if (!commit_fert(e, r.stage, r, true)) return false;
    e.run = r;
  } else {
    if (r.slot_id != 0 && !commit_claim(e, r.slot_id, r.occ_day)) return false;
    if (kind == IrrLineKind::kFog) {
      // A fog run-once: it holds the water from now (one engine run at a time), so it starts
      // with its start and planned end published.
      r.kind = IrrRunKind::kFog;
      r.remaining_ms = uint32_t(r.on_s) * 1000u;
      r.deadline_ms = now_ms + r.remaining_ms + kWindowGraceMs;
      r.last_block = IrrReason::kMan;
      const uint32_t ep = epoch_now(e, now_ms);
      r.start_epoch = ep;
      r.end_epoch = ep == 0 ? 0u : ep + r.on_s;
    } else if (kind == IrrLineKind::kDrip) {
      r.kind = IrrRunKind::kDrip;
      r.remaining_ms = uint32_t(r.on_s) * 1000u;
    } else {
      r.kind = IrrRunKind::kWindow;
      const uint32_t span = uint32_t(r.count) * r.on_s + uint32_t(r.count - 1) * r.gap_s;
      r.deadline_ms = now_ms + (span - r.on_s) * 1000u + kWindowGraceMs;
    }
    e.run = r;
  }
  // Open at once when the manifold allows it.
  advance_run(e, now_ms, 0);
  rtc_mirror_run(e);
  return true;
}

inline void scan_and_start(IrrEngine& e, uint32_t now_ms) {
  const IrrClock& c = e.in.clock;
  // New due occurrences become pending.
  if (c.valid) {
    for (uint8_t k = 0; k < e.prog.slot_count; ++k) {
      const IrrSlot& s = e.prog.slots[k];
      IrrOccurrence occ{};
      if (!irr_due(s, c, occ)) continue;
      if (irr_claimed(e.claims, s.slot_id, occ.occ_day)) continue;
      if (e.run.kind != IrrRunKind::kNone && e.run.slot_id == s.slot_id && e.run.occ_day == occ.occ_day) continue;
      if (find_pending(e, s.slot_id, occ.occ_day) >= 0) continue;
      for (IrrPending& p : e.pending) {
        if (p.used) continue;
        p.used = true;
        p.slot_id = s.slot_id;
        p.occ_day = occ.occ_day;
        p.planned_epoch = occ.planned_epoch;
        const int32_t left_s = int32_t(occ.due_until_epoch) - int32_t(c.epoch);
        p.deadline_ms = now_ms + uint32_t(left_s > 0 ? left_s : 0) * 1000u;
        p.reason = IrrReason::kNone;
        p.until_epoch = 0;
        break;
      }
    }
  }
  // Settle what is known now, and expire what waited past its 60 minutes. An outcome
  // whose claim cannot be saved is retried each tick, for at most 10 minutes past the
  // deadline; then it is dropped unpublished (the platform shows a gap, never a guess).
  for (IrrPending& p : e.pending) {
    if (!p.used) continue;
    const IrrSlot* s = slot_by_id(e, p.slot_id);
    if (s == nullptr || irr_claimed(e.claims, p.slot_id, p.occ_day) ||
        irr_reached(now_ms, p.deadline_ms + kSettleGiveUpMs)) {
      p = IrrPending{};
      continue;
    }
    IrrOutcome o;
    const IrrReason why = settle_reason(e, *s, p, o);
    if (why != IrrReason::kNone) {
      settle(e, p, s->line, o, why);
      continue;
    }
    if (s->degraded_only() && is_clean_mister(s->line) && c.valid && !irr_daylight(c)) {
      settle(e, p, s->line, IrrOutcome::kSkipped, IrrReason::kNone);  // fallback runs in daylight only
      continue;
    }
    if (irr_reached(now_ms, p.deadline_ms)) {
      // degraded_only slots that never fired end skipped: VPD sensing stayed healthy (vok).
      if (s->degraded_only()) {
        settle(e, p, s->line, IrrOutcome::kSkipped, p.reason == IrrReason::kNone ? IrrReason::kVok : p.reason);
      } else {
        settle(e, p, s->line, IrrOutcome::kMissed, p.reason);
      }
    }
  }
  // Start the earliest-planned occurrence that may run now; the rest wait.
  int best = -1;
  for (int i = 0; i < kMaxSlots; ++i) {
    IrrPending& p = e.pending[i];
    if (!p.used) continue;
    const IrrSlot* s = slot_by_id(e, p.slot_id);
    if (s == nullptr) continue;
    uint32_t until = 0;
    p.reason = start_block(e, *s, now_ms, until);
    p.until_epoch = until;
    if (p.reason != IrrReason::kNone) continue;
    if (best < 0 || p.planned_epoch < e.pending[best].planned_epoch ||
        (p.planned_epoch == e.pending[best].planned_epoch && p.slot_id < e.pending[best].slot_id)) {
      best = i;
    }
  }
  if (best < 0) return;
  const IrrSlot& s = *slot_by_id(e, e.pending[best].slot_id);
  IrrRun r{};
  r.line = s.line;
  r.slot_id = s.slot_id;
  r.occ_day = e.pending[best].occ_day;
  r.on_s = s.on_s;
  r.gap_s = s.gap_s;
  r.prewet_s = s.prewet_s;
  r.flush_s = s.flush_s;
  r.count = s.count;
  r.degraded_only = s.degraded_only();
  r.cause = r.degraded_only ? IrrCause::kFallback : IrrCause::kSchedule;
  if (!start_run(e, r, now_ms)) {
    e.pending[best].reason = IrrReason::kNone;  // the claim did not stick: try again next tick
    return;
  }
  e.pending[best] = IrrPending{};
  // One engine run at a time: the others now wait for the manifold.
  for (IrrPending& q : e.pending) {
    if (!q.used) continue;
    const IrrSlot* qs = slot_by_id(e, q.slot_id);
    if (qs == nullptr) continue;
    uint32_t until = 0;
    q.reason = start_block(e, *qs, now_ms, until);
    q.until_epoch = until;
  }
}

}  // namespace detail

// ── Engine tick (every 5 s on the device; rate-agnostic) ─────────────────────

// One engine step. Runtime accrues from the relay states sampled here, so a VPD
// pulse that turns on and off between ticks is counted to the tick.
inline void irr_tick(IrrEngine& e, const IrrInputs& in, IrrOutputs& out) {
  const uint32_t dt = e.have_tick ? in.now_ms - e.in.now_ms : 0u;
  const uint16_t prev_relays = e.in.relay_on_mask;
  e.in = in;
  e.have_tick = true;
  IrrRtcBlock& b = *e.rtc;
  // Integrity first (a corrupted block is zeroed, never re-sealed), then the elapsed
  // interval is counted to the day it mostly belongs to, then local midnight rolls.
  irr_rtc_validate(b, false, 0);
  // A mister relay that went OFF since the last tick fences its zone (mister_min_off_s),
  // unless its close was already stamped when it happened (stamp_close()).
  const uint16_t went_off = uint16_t(prev_relays & ~in.relay_on_mask & kMistManifoldMask & ~e.off_stamped);
  e.off_stamped = 0;
  for (uint8_t i = 0; i < kLineCount; ++i) {
    if (went_off & irr_bit(i)) detail::set_zone_off(e, i, in.now_ms);
  }
  // #1313: a valve of the open continuous run read ON at the last tick and OFF now, and the
  // engine did not close it: something else did (an OTA upload's on_begin close, #1276, then an
  // aborted upload). The interval since the last accounting is not water delivered, so the run
  // is not charged for it; build_outputs re-opens the valve. At most one tick is left uncounted.
  // A valve that never read ON keeps the old accounting, so a refused relay still times out.
  if (e.run.open && (prev_relays & ~in.relay_on_mask & detail::run_relays(e.run)) != 0) {
    e.run.open_since_ms = in.now_ms;
  }
  detail::accrue(e, in.relay_on_mask, dt);
  irr_rtc_validate(b, false, in.clock.valid ? in.clock.local_day : uint16_t(0));
  if (e.staged) detail::apply_program(e, in.now_ms);
  if (!e.recovered) {
    e.recovered = true;
    detail::recover(e, in.now_ms);
  }
  detail::vpd_housekeeping(e, in.now_ms);
  detail::advance_run(e, in.now_ms, dt);
  detail::scan_and_start(e, in.now_ms);
  detail::rtc_mirror_run(e);
  detail::build_outputs(e, in.now_ms, out);
}

// ── Run-now and stop (irr_command, #1160) ────────────────────────────────────

struct IrrRunArgs {
  uint8_t line;       // device_idx
  uint16_t on_s;      // drips: minutes x 60; misters: the pulse
  uint8_t count;      // misters: pulses; drips: 1
  uint16_t gap_s;     // misters: the gap; drips: 0
  uint16_t prewet_s;  // fertilizer lines
  uint16_t flush_s;   // fertilizer lines
};

enum class IrrRunResult : uint8_t {
  kStarted,
  kLineBusy,           // that line is already running
  kManifoldBusy,       // another run holds the water (irr_state names it and until when)
  kCapReached,         // beyond the line's (or zone's) remaining daily minutes, or SAF-4 / budget
  kLineDisabled,
  kLinePaused,
  kLineFaulted,        // a latched mister zone
  kNotScheduleMember,  // retired (#1343): never returned; kept so the token indices stay stable
  kSensorFault,        // device only: no engine mist in SENSOR_FAULT
  kBadArgs,            // device only: outside the line kind's bounds, or not a schedulable line
  kStorage,            // device only: a fertilizer journal that did not save and sync
};
constexpr const char* kRunResultTokens[] = {"started",         "line_busy",          "manifold_busy",
                                            "cap_reached",     "line_disabled",      "line_paused",
                                            "line_faulted",    "not_schedule_member", "sensor_fault",
                                            "bad_args",        "storage"};

// Run a line now: any enabled wet line once (#1343, superseding D-IRP-11's schedule-member
// rule). On a clean mister it is a one-off window whether or not Schedule is on, under the
// same rails as a scheduled one: the zone's duty cap less its VPD reserve
// (zone_sched_left_s), SAF-4, the watchdog latch and sensor fault.
// Fog (L11) runs once too (#1343 follow-up), under the mister rails that apply to it: enabled,
// not paused, no SENSOR_FAULT, SAF-4 and the soft budget not reached, and no other run holding
// the water; its bounds are irr_fog_run_args_in_bounds().
// Starts at once or is refused; nothing queues. A drip in SAFETY_COOL or a VPD
// emergency starts paused and resumes after it; a fertilizer run in SAFETY_COOL is
// refused manifold_busy (it would end at once). A refusal other than line_busy,
// bad_args and storage also ends the request `skipped` in irr_events (slot 0) with
// its reason, so the platform sees why it did not start.
inline IrrRunResult irr_run_now(IrrEngine& e, const IrrRunArgs& a, uint32_t now_ms, IrrOutputs& out) {
  using namespace detail;
  IrrRunResult res = IrrRunResult::kStarted;
  IrrReason why = IrrReason::kNone;
  const uint8_t line = a.line;
  const uint32_t ep = epoch_now(e, now_ms);
  const bool fog = line == kL11;
  if (fog ? !irr_fog_run_args_in_bounds(a.on_s, a.count, a.gap_s, a.prewet_s, a.flush_s)
          : !irr_run_args_in_bounds(line, a.on_s, a.count, a.gap_s, a.prewet_s, a.flush_s)) {
    res = IrrRunResult::kBadArgs;
  } else if (e.run.kind != IrrRunKind::kNone && (e.run.line == line || run_stage_line(e.run) == line)) {
    res = IrrRunResult::kLineBusy;
  } else if (e.run.kind != IrrRunKind::kNone) {
    res = IrrRunResult::kManifoldBusy;
    why = IrrReason::kMan;
  } else if (!line_enabled(e, line) || partner_disabled(e, line, a.prewet_s, a.flush_s)) {
    res = IrrRunResult::kLineDisabled;  // a prewet or flush would open a disabled partner
    why = IrrReason::kDis;
  } else if (paused_at(e, line, ep)) {
    res = IrrRunResult::kLinePaused;
    why = IrrReason::kPau;
  } else if (is_mister(line) && (e.in.latch_mask & zone_lines(line))) {
    res = IrrRunResult::kLineFaulted;
    why = IrrReason::kFlt;
  } else if ((is_mister(line) || fog) && e.in.sensor_fault) {
    res = IrrRunResult::kSensorFault;
    why = IrrReason::kSf;
  } else if (is_fert(line) && e.in.safety_cool) {
    // SAFETY_COOL would end a fertilizer run at once (D-IRP-03): survival fog holds the water.
    res = IrrRunResult::kManifoldBusy;
    why = IrrReason::kMan;
  } else {
    const IrrLineKind kind = kLineKind[line];
    if (fog) {
      // Fog water counts in SAF-4 and the soft budget, as climate fog does.
      if (saf4_reached(e)) why = IrrReason::kSaf;
      else if (budget_reached(e)) why = IrrReason::kBud;
    } else if (kind == IrrLineKind::kDrip || kind == IrrLineKind::kFertDrip) {
      if (uint32_t(a.on_s) > cap_left_s(e, line)) why = IrrReason::kCap;
    } else {
      const uint32_t want = uint32_t(a.count) * a.on_s;
      const IrrReason c = mist_cap_reason(e, line, true);
      if (c != IrrReason::kNone) why = c;
      else if (want > zone_sched_left_s(e, line)) why = IrrReason::kRes;
      else if (kind == IrrLineKind::kFertMister && want > cap_left_s(e, line)) why = IrrReason::kCap;
    }
    if (why != IrrReason::kNone) res = IrrRunResult::kCapReached;
  }
  if (res == IrrRunResult::kStarted) {
    IrrRun r{};
    r.line = line;
    r.slot_id = 0;
    r.occ_day = today(e);
    r.on_s = a.on_s;
    r.count = a.count;
    r.gap_s = a.gap_s;
    r.prewet_s = a.prewet_s;
    r.flush_s = a.flush_s;
    r.cause = IrrCause::kRunNow;
    if (!start_run(e, r, now_ms)) res = IrrRunResult::kStorage;
  } else if (why != IrrReason::kNone) {
    emit(e, line, 0, today(e), IrrOutcome::kSkipped, why);
  }
  build_outputs(e, now_ms, out);
  return res;
}

// Stop what is open now on one line (or every line: kAllLines). Never deferred and
// disables nothing: VPD misting, survival fog and the next scheduled run can still
// start. A fertilizer run stopped in its prewet or feed ends `stopped` and its flush
// follows (journaled at the next tick); a stop during the flush ends the flush. A VPD
// pulse in scope loses its grant. Returns how many runs or pulses it ended.
inline uint8_t irr_stop(IrrEngine& e, uint8_t line, uint32_t now_ms, IrrOutputs& out) {
  using namespace detail;
  uint8_t ended = 0;
  IrrRun& r = e.run;
  const bool all = line == kAllLines;
  if (r.kind != IrrRunKind::kNone &&
      (all || r.line == line || run_stage_line(r) == line ||
       (r.kind == IrrRunKind::kFert && r.stage == IrrStage::kFeed && line == kL03))) {
    ++ended;
    if (r.kind == IrrRunKind::kFert) {
      if (r.stage == IrrStage::kFlush) {
        finish_flush(e, now_ms);
      } else if (!r.to_flush && !r.to_settle) {  // otherwise nothing is open and its outcome is settled
        end_fert_part(e, now_ms, IrrOutcome::kStopped, IrrReason::kCmd, true);
      }
    } else {
      end_clean_run(e, now_ms, IrrOutcome::kStopped, IrrReason::kCmd);
    }
  }
  if (e.vpd_line != kNoLine && (all || e.vpd_line == line)) {
    stamp_close(e, e.vpd_line, now_ms);  // the stop closes it inside the call
    e.vpd_line = kNoLine;
    e.sched_turn = true;
    ++ended;
  }
  rtc_mirror_run(e);
  build_outputs(e, now_ms, out);
  return ended;
}

// ── Climate VPD pulses on the mist manifold (#1160 wraps these as irr_vpd_grant
//    and irr_vpd_release; #1161 calls them at each pulse boundary) ─────────────

enum class IrrVpdAnswer : uint8_t {
  kGrant,   // pulse now: the grant bit is set until irr_vpd_release()
  kServed,  // a scheduled pulse on this zone is open now and serves both; count it once
  kWait,    // the manifold or the turn is the schedule's; ask again at the next boundary
  kRefuse,  // not now: see `reason`
};

struct IrrVpdResult {
  IrrVpdAnswer answer;
  IrrReason reason;
};

// A climate VPD pulse of `on_s` seconds on clean mister `idx` (L04, L07 or L09). The
// grant goes only to an enabled VPD member while the mist manifold is free, no drip
// holds the water and L03 is closed. Pending VPD and scheduled pulses alternate at
// boundaries; in a VPD emergency VPD goes first and windows on other zones wait.
inline IrrVpdResult irr_vpd_request(IrrEngine& e, uint8_t idx, uint16_t on_s, uint32_t now_ms, IrrOutputs& out) {
  using namespace detail;
  IrrVpdResult res{IrrVpdAnswer::kRefuse, IrrReason::kNone};
  const IrrRun& r = e.run;
  auto wait = [&](IrrReason why) {
    res = {IrrVpdAnswer::kWait, why};
    e.vpd_wait_line = idx;
    e.vpd_wait_ms = now_ms;
  };
  if (!is_clean_mister(idx)) {
    res.reason = IrrReason::kNone;
  } else if (e.in.sensor_fault) {
    res.reason = IrrReason::kSf;
  } else if (!line_enabled(e, idx)) {
    res.reason = IrrReason::kDis;
  } else if (!vpd_member(e, idx)) {
    res.reason = IrrReason::kNvm;
  } else if (e.in.latch_mask & zone_lines(idx)) {
    res.reason = IrrReason::kFlt;
  } else if (e.in.backoff_mask & zone_lines(idx)) {
    res.reason = IrrReason::kBof;
  } else if (mist_cap_reason(e, idx, false) != IrrReason::kNone) {
    res.reason = mist_cap_reason(e, idx, false);
  } else if (e.vpd_line == idx) {
    res = {IrrVpdAnswer::kGrant, IrrReason::kNone};
  } else if (e.vpd_line != kNoLine) {
    wait(IrrReason::kMan);
  } else if (run_relays(r) & irr_bit(kL03)) {
    wait(IrrReason::kL03);
  } else if (r.kind == IrrRunKind::kFog) {
    wait(IrrReason::kMan);  // a fog run-once holds the water: one engine run at a time
  } else if (run_on_drip_manifold(r) && irr_drip_active(e)) {
    wait(IrrReason::kMan);
  } else if (r.kind != IrrRunKind::kNone && (run_relays(r) & kMistManifoldMask)) {
    if (r.kind == IrrRunKind::kWindow && r.pulse_open && kMistZone[r.line] == kMistZone[idx]) {
      res = {IrrVpdAnswer::kServed, IrrReason::kNone};
    } else {
      wait(IrrReason::kMan);
    }
  } else if ((physical_misters(e) & ~irr_bit(idx)) || zone_fenced(e, idx, now_ms)) {
    wait(IrrReason::kMan);
  } else {
    // A scheduled pulse due now goes first when it is the schedule's turn (not in a
    // VPD emergency), or when it is on the same zone (one pulse serves both).
    bool sched_due = false;
    if ((r.kind == IrrRunKind::kWindow ||
         (r.kind == IrrRunKind::kFert && r.stage == IrrStage::kFeed && !r.to_flush && !r.to_settle)) &&
        is_mister(r.line) && !r.pulse_open && r.done < r.count && irr_reached(now_ms, r.next_pulse_ms) &&
        pulse_skip(e, r.line, r.kind == IrrRunKind::kFert, r.degraded_only) == IrrReason::kNone &&
        !zone_fenced(e, r.line, now_ms)) {
      sched_due = (e.sched_turn && !e.in.vpd_emergency) || kMistZone[r.line] == kMistZone[idx];
    }
    if (sched_due) {
      wait(IrrReason::kMan);
    } else {
      e.vpd_line = idx;
      e.vpd_since_ms = now_ms;
      e.vpd_on_s = on_s;
      const uint32_t ep = epoch_now(e, now_ms);
      e.vpd_start_epoch = ep;
      e.vpd_end_epoch = ep == 0 ? 0u : ep + on_s;
      if (e.vpd_wait_line == idx) e.vpd_wait_line = kNoLine;
      res = {IrrVpdAnswer::kGrant, IrrReason::kNone};
    }
  }
  build_outputs(e, now_ms, out);
  return res;
}

// The climate VPD pulse on `idx` ended: release the manifold. The next free boundary
// goes to a waiting scheduled pulse, and a drip yielding for this pulse resumes.
inline void irr_vpd_release(IrrEngine& e, uint8_t idx, uint32_t now_ms, IrrOutputs& out) {
  using namespace detail;
  if (e.vpd_line == idx) {
    e.vpd_line = kNoLine;
    stamp_close(e, idx, now_ms);  // the climate path calls this once its relay is off
    e.sched_turn = true;
    if (e.run.yielding) e.run.yield_vpd_done = true;  // a drip or drip flush yielding for it resumes
  }
  build_outputs(e, now_ms, out);
}

// The outputs as they stand, with no tick: a push reject shows in irr_state at once.
inline void irr_refresh_outputs(IrrEngine& e, uint32_t now_ms, IrrOutputs& out) {
  detail::build_outputs(e, now_ms, out);
}

// D-IRP-19 (F10, #1161): the mister watchdog's faults reach the engine at once, not at its next
// tick, so a VPD request or a run-now in between already sees a new back-off or latch (or a
// cleared one). `faulted`: the misters the watchdog just forced OFF. A climate VPD grant on one
// of them is dropped, and the engine's own open there is closed, so nothing reopens the relay: a
// pulse counts as delivered and the next waits for the fault to end; a prewet or flush on a
// mister stops its clock and waits on mist_tail_hold(). With `faulted` 0 only the masks change.
inline void irr_apply_faults(IrrEngine& e, uint16_t faulted, uint16_t backoff_mask, uint16_t latch_mask,
                             uint32_t now_ms, IrrOutputs& out) {
  using namespace detail;
  e.in.backoff_mask = backoff_mask;
  e.in.latch_mask = latch_mask;
  if (e.vpd_line != kNoLine && (faulted & irr_bit(e.vpd_line))) {
    stamp_close(e, e.vpd_line, now_ms);  // the watchdog closed it inside this call
    e.vpd_line = kNoLine;
    e.sched_turn = true;
    if (e.run.yielding) e.run.yield_vpd_done = true;
  }
  IrrRun& r = e.run;
  const uint16_t held = uint16_t(run_relays(r) & faulted & kMistManifoldMask);
  if (r.kind != IrrRunKind::kNone && held != 0) {
    uint8_t line = kNoLine;
    for (uint8_t i = 0; i < kLineCount; ++i) {
      if (held & irr_bit(i)) line = i;
    }
    const IrrReason why = (latch_mask & zone_lines(line)) ? IrrReason::kFlt : IrrReason::kBof;
    if (r.pulse_open) {
      close_pulse(e, r, now_ms);
      ++r.done;
      const uint16_t min_off = e.in.mister_min_off_s;
      r.next_pulse_ms = now_ms + uint32_t(r.gap_s > min_off ? r.gap_s : min_off) * 1000u;
    } else if (r.open) {
      close_continuous(r, now_ms);
      stamp_close(e, line, now_ms);
    }
    r.block = why;
    r.last_block = why;
    rtc_mirror_run(e);
  }
  build_outputs(e, now_ms, out);
}

// ── Device wiring, the pure half (VD-45, #1160) ──────────────────────────────
// firmware/greenhouse/irrigation_engine.yaml and firmware/lib/irrigation_engine_esphome.h
// call these; the native tests cover them on the same inputs.

constexpr size_t kTextMax = 255;              // IRR_TEXT_MAX_LEN: a Home Assistant state
constexpr size_t kTextBufLen = kTextMax + 1;  // the device's static 256 B buffers
// The relays the engine grants (F5): L01-L10. Fog (L11) is climate only, outside the grant.
constexpr uint16_t kGrantableMask = 0x03FF;
static_assert(kGrantableMask == (kWetMask & ~kFogMask), "the grantable relays are L01-L10");

// irr_program_id: "h=<the first 16 hex of the sha256> src=nvs" for a stored program, and
// "h=0000000000000000 src=rom" for the ROM default, so every desired program differs from it
// and is pushed. Returns the length written, or 0 when `cap` is too small (`buf` reads "").
inline size_t irr_format_program_id(bool nvs, const uint8_t hash8[8], char* buf, size_t cap) {
  if (buf == nullptr || cap == 0) return 0;
  static constexpr char kHex[] = "0123456789abcdef";
  char hex[17];
  for (int i = 0; i < 8; ++i) {
    const uint8_t b = (nvs && hash8 != nullptr) ? hash8[i] : uint8_t(0);
    hex[2 * i] = kHex[b >> 4];
    hex[2 * i + 1] = kHex[b & 0x0F];
  }
  hex[16] = '\0';
  const int w = std::snprintf(buf, cap, "h=%s src=%s", hex, nvs ? "nvs" : "rom");
  if (w < 0 || size_t(w) >= cap) {
    buf[0] = '\0';
    return 0;
  }
  return size_t(w);
}

// An f= token: a mister line backing off (b) or latched (l) by the D-IRP-19 watchdog, and until
// when. irr_fault_tokens() fills these from the RTC block (one per faulted zone); the engine wiring
// publishes them.
struct IrrFaultToken {
  uint8_t line;          // a mister: L04, L07, L08, L09 or L10
  bool latched;          // l: latched until local midnight or clear_fault; b: backing off
  uint32_t until_epoch;  // UTC seconds
};
constexpr uint8_t kMaxFaults = 5;  // one per mister line

// What irr_state carries beside the engine's own fields (IrrStateFields).
struct IrrStateExtras {
  IrrFaultToken faults[kMaxFaults];
  uint8_t fault_count;
  char vpd_source[3];  // center, south, west: 'o' own, 'p' peer, 'h' house; vpd_source[0] 0: no v=
};

namespace detail {

inline bool vpd_code_ok(char c) { return c == 'o' || c == 'p' || c == 'h'; }

// Append `t` (t_n characters) to dst[0..n), one space apart, if the result stays within `room`.
inline bool append_token(char* dst, size_t& n, size_t room, const char* t, int t_n) {
  if (t_n <= 0) return false;
  const size_t need = (n ? 1u : 0u) + size_t(t_n);
  if (n + need > room) return false;
  if (n) dst[n++] = ' ';
  std::memcpy(dst + n, t, size_t(t_n));
  n += size_t(t_n);
  dst[n] = '\0';
  return true;
}

}  // namespace detail

// irr_state in #1151's grammar, in format_irr_state()'s order: d=, m=, each w= (earliest due
// first), each f=, v=, j=. Past 255 characters the w= tokens drop, latest due first, exactly
// as format_irr_state() drops them. Absolute times only; no masks, acks or remaining seconds.
// Tokens the parsers refuse are never written: a run on the wrong manifold, a w= naming L03 or
// L11 or with no reason, an f= naming a line that is not a mister or one line twice, an unknown
// VPD code. Returns the length written (`buf` is always terminated).
inline size_t irr_format_state(const IrrStateFields& s, const IrrStateExtras& x, char* buf, size_t cap) {
  if (buf == nullptr || cap == 0) return 0;
  buf[0] = '\0';
  const size_t limit = cap - 1 < kTextMax ? cap - 1 : kTextMax;
  char tok[48];
  // The f=, v= and j= tail first, so the w= tokens can be fitted in front of it.
  char tail[128];
  size_t tail_n = 0;
  tail[0] = '\0';
  uint16_t fault_lines = 0;
  for (uint8_t i = 0; i < x.fault_count && i < kMaxFaults; ++i) {
    const IrrFaultToken& f = x.faults[i];
    if (!detail::is_mister(f.line) || (fault_lines & irr_bit(f.line))) continue;
    fault_lines = uint16_t(fault_lines | irr_bit(f.line));
    detail::append_token(tail, tail_n, sizeof(tail) - 1, tok,
                         std::snprintf(tok, sizeof(tok), "f=%u,%c,%lu", unsigned(f.line), f.latched ? 'l' : 'b',
                                       static_cast<unsigned long>(f.until_epoch)));
  }
  if (detail::vpd_code_ok(x.vpd_source[0]) && detail::vpd_code_ok(x.vpd_source[1]) &&
      detail::vpd_code_ok(x.vpd_source[2])) {
    detail::append_token(tail, tail_n, sizeof(tail) - 1, tok,
                         std::snprintf(tok, sizeof(tok), "v=%c%c%c", x.vpd_source[0], x.vpd_source[1],
                                       x.vpd_source[2]));
  }
  if (s.reject != IrrReject::kNone && uint8_t(s.reject) < 7) {
    detail::append_token(tail, tail_n, sizeof(tail) - 1, tok,
                         std::snprintf(tok, sizeof(tok), "j=%s", irr_reject_token(s.reject)));
  }
  // The run on each manifold.
  size_t n = 0;
  auto run = [&](char key, const IrrRunToken& r, uint16_t manifold) {
    if (!r.valid || r.line >= kLineCount || !(manifold & irr_bit(r.line)) || uint8_t(r.cause) >= kCauseCount) return;
    const uint32_t end = r.end_epoch < r.start_epoch ? r.start_epoch : r.end_epoch;
    detail::append_token(buf, n, limit, tok,
                         std::snprintf(tok, sizeof(tok), "%c=%u,%s,%u,%lu,%lu", key, unsigned(r.line),
                                       kCauseNames[uint8_t(r.cause)], unsigned(r.slot_id),
                                       static_cast<unsigned long>(r.start_epoch), static_cast<unsigned long>(end)));
  };
  run('d', s.drip, kDripManifoldMask);
  run('m', s.mist, uint16_t(kMistManifoldMask | kFogMask));  // a fog run-once rides m= (#1343 follow-up)
  // The w= tokens while the tail still fits after them: the first that does not, and every later
  // one, drop (format_irr_state() pops them from the end).
  const size_t after = tail_n ? tail_n + 1 : 0;
  for (uint8_t i = 0; i < s.wait_count && i < kMaxSlots + 1; ++i) {
    const IrrWait& w = s.waits[i];
    if (w.line >= kLineCount || !(kSchedulableMask & irr_bit(w.line)) || w.reason == IrrReason::kNone ||
        uint8_t(w.reason) >= kReasonCount) {
      continue;
    }
    const int t_n = w.until_epoch != 0
                        ? std::snprintf(tok, sizeof(tok), "w=%u,%s,%lu", unsigned(w.line),
                                        kReasonCodes[uint8_t(w.reason)], static_cast<unsigned long>(w.until_epoch))
                        : std::snprintf(tok, sizeof(tok), "w=%u,%s", unsigned(w.line), kReasonCodes[uint8_t(w.reason)]);
    if (t_n <= 0 || n + (n ? 1u : 0u) + size_t(t_n) + after > limit) break;
    detail::append_token(buf, n, limit, tok, t_n);
  }
  if (tail_n) detail::append_token(buf, n, limit, tail, int(tail_n));
  return n;
}

// How the device applies one engine call (F5). The bits of the relays about to open go into
// irr_grant_mask before any turn_on; the relays the engine let go (and, for a stop, every open
// wet relay in its scope) turn off first, L03 last, so two drips never overlap; then the
// engine's relays turn on, L03 first; the bits clear only once their relays are off.
struct IrrApply {
  uint16_t grant_before;  // irr_grant_mask while relays change
  uint16_t turn_off;
  uint16_t turn_on;
  uint16_t grant_after;   // irr_grant_mask once they have
};

// `grant`: irr_grant_mask now; `held`: the engine relays the device opened at the last call;
// `on`: the relays that read ON; `stop_scope`: the wet relays a stop closes (0 otherwise).
inline IrrApply irr_apply_plan(uint16_t grant, uint16_t held, uint16_t on, const IrrOutputs& out, uint16_t stop_scope) {
  IrrApply a{};
  const uint16_t want = uint16_t(out.relay_mask & kGrantableMask);
  a.grant_before = uint16_t((grant | out.grant_mask) & kGrantableMask);
  a.turn_off = uint16_t(((held & ~want) | stop_scope) & on & kGrantableMask);
  a.turn_on = uint16_t(want & ~on);
  a.grant_after = uint16_t(out.grant_mask & kGrantableMask);
  return a;
}

// The program as NVS holds it (one preference): the 584 B record and the SHA-256 it was pushed
// with. At boot it is decoded again: src=nvs when it decodes, else the ROM default (src=rom).
struct IrrProgramRecord {
  uint8_t rec[kRecordLen];
  uint8_t sha256[32];
};
static_assert(sizeof(IrrProgramRecord) == kRecordLen + 32, "616 B NVS record");
static_assert(std::is_trivially_copyable<IrrProgramRecord>::value, "IrrProgramRecord is saved byte for byte");

// The program push (F2) in one static record: irr_program_begin(len, sha256_hex) stores the
// expected hash and clears the record, irr_program_chunk(offset, data_hex) copies at most
// 192 B into it, irr_program_commit() decodes it against the hash. The decoder settles every
// reject, a declared length other than 584 included (bad_length).
struct IrrStaging {
  IrrProgramRecord record;  // the staged bytes and the expected hash (on the device, also the NVS buffer)
  int32_t len;              // the length irr_program_begin declared
  bool open;                // a begin with no commit since
  bool hash_ok;             // sha256_hex carried 32 bytes
};

// `sha256`: the 32 bytes sha256_hex decoded to, or nullptr when it did not (the commit is then
// rejected bad_hash, or earlier for the length or magic).
inline void irr_staging_begin(IrrStaging& st, int32_t len, const uint8_t* sha256) {
  std::memset(&st.record, 0, sizeof(st.record));
  st.len = len;
  st.hash_ok = sha256 != nullptr;
  if (st.hash_ok) std::memcpy(st.record.sha256, sha256, sizeof(st.record.sha256));
  st.open = true;
}

// Copy one decoded chunk. False, and nothing copied, with no push open, an empty or undecodable
// chunk (`data` nullptr), more than 192 B, or bytes past the record's 584.
inline bool irr_staging_chunk(IrrStaging& st, int32_t offset, const uint8_t* data, size_t n) {
  if (!st.open || data == nullptr || n == 0 || n > kChunkMax || offset < 0 || size_t(offset) + n > kRecordLen) {
    return false;
  }
  std::memcpy(st.record.rec + offset, data, n);
  return true;
}

enum class IrrCommit : uint8_t { kNotOpen, kRejected, kAccepted };

// Decode the staged record against the begin's hash; either way the push is closed. kAccepted:
// `out` is the program and st.record (record and hash) is what NVS keeps. kRejected: `why`.
inline IrrCommit irr_staging_commit(IrrStaging& st, IrrProgram& out, IrrReject& why) {
  why = IrrReject::kNone;
  if (!st.open) return IrrCommit::kNotOpen;
  st.open = false;
  const size_t len = st.len < 0 ? 0u : size_t(st.len);
  if (!irr_decode(st.record.rec, len, st.hash_ok ? st.record.sha256 : nullptr, out, why)) {
    return IrrCommit::kRejected;
  }
  return IrrCommit::kAccepted;
}

// irr_command(cmd, line, on_s, count, gap_s, prewet_s, flush_s) (F4).
enum class IrrCmd : uint8_t { kUnknown, kRun, kStop, kClearFault };

inline IrrCmd irr_parse_cmd(const char* cmd) {
  if (cmd == nullptr) return IrrCmd::kUnknown;
  if (std::strcmp(cmd, "run") == 0) return IrrCmd::kRun;
  if (std::strcmp(cmd, "stop") == 0) return IrrCmd::kStop;
  if (std::strcmp(cmd, "clear_fault") == 0) return IrrCmd::kClearFault;
  return IrrCmd::kUnknown;
}

// The service's ints as run-now arguments. False (bad_args) for a value outside its field's
// width; irr_run_now() then applies the line kind's bounds (BOUNDS_BY_KIND).
inline bool irr_run_args_from(int32_t line, int32_t on_s, int32_t count, int32_t gap_s, int32_t prewet_s,
                              int32_t flush_s, IrrRunArgs& out) {
  auto u16 = [](int32_t v) { return v >= 0 && v <= 0xFFFF; };
  if (line < 0 || line >= kLineCount || !u16(on_s) || count < 0 || count > 0xFF || !u16(gap_s) || !u16(prewet_s) ||
      !u16(flush_s)) {
    return false;
  }
  out = IrrRunArgs{uint8_t(line), uint16_t(on_s), uint8_t(count), uint16_t(gap_s), uint16_t(prewet_s),
                   uint16_t(flush_s)};
  return true;
}

// The wet relays a stop closes inside the service call: one line's relay (L01-L10), or every
// wet relay (255). Fog's relay (L11) is the climate path's, so a stop never switches it: a stop
// on L11 ends a fog run-once (scope 0) and the climate path's own fog goes on. False for any
// other line, and the stop does nothing.
inline bool irr_stop_scope(int32_t line, uint16_t& scope) {
  scope = 0;
  if (line == kAllLines) {
    scope = kGrantableMask;
    return true;
  }
  if (line == kL11) return true;
  if (line >= 0 && line < kLineCount && (kGrantableMask & irr_bit(uint8_t(line)))) {
    scope = irr_bit(uint8_t(line));
    return true;
  }
  return false;
}

// The local UTC offset in minutes from one reading of the clock: its local hour, minute and
// second and its UTC epoch, taken from the same instant (ESPHome's ESPTime gives both), so the
// DST changeovers come out right by construction (irr_clock_from_epoch()).
inline int irr_utc_offset_min(int hour, int minute, int second, uint32_t epoch) {
  int32_t d = int32_t(hour) * 3600 + int32_t(minute) * 60 + int32_t(second) - int32_t(epoch % 86400u);
  if (d > 43200) d -= 86400;
  else if (d <= -43200) d += 86400;
  return int(d / 60);
}

// Flow-meter water for SAF-4 and the soft budget: `gpm` over `dt_ms` in whole centi-gallons,
// the fraction below one centi-gallon carried in `carry` to the next call, so truncation loses
// no water (irr_count_water() adds the result to the RTC block).
inline uint32_t irr_water_centigal(float gpm, uint32_t dt_ms, float& carry) {
  if (!std::isfinite(carry) || carry < 0.0f) carry = 0.0f;
  if (!std::isfinite(gpm) || gpm <= 0.0f || dt_ms == 0) return 0;
  const float total = gpm * float(dt_ms) / 600.0f + carry;  // gpm x dt_ms / 60000 gal, x 100
  const float whole = std::floor(total);
  carry = total - whole;
  return whole >= 4294967040.0f ? UINT32_MAX : uint32_t(whole);
}

// The RH ceiling and dew margin inputs (irrigation §4.6, D-IRP-09, D-IRP-12) from one measured
// indoor temperature and RH pair: the average probes, or while they are out the controller-case
// probe (the climate loop's own fallback for its temperature and RH). With no measured pair (a
// NaN, or an RH outside (0, 100]) the RH reads missing, which never blocks, and the dew margin is
// unknown, which holds degraded_only mist (dew). The dew point is the Magnus form of the
// dewpoint_avg sensor (sensors.yaml: 17.27, 237.3 C).
struct IrrAirInputs {
  float rh_pct;           // NaN: missing
  bool dew_margin_known;  // a dew margin could be computed
  bool dew_breach;        // temperature less dew point is below `min_margin_f`
};

inline IrrAirInputs irr_air_inputs(float temp_f, float rh_pct, float min_margin_f) {
  IrrAirInputs a{NAN, false, false};
  if (!std::isfinite(temp_f) || !std::isfinite(rh_pct) || rh_pct <= 0.0f || rh_pct > 100.0f) return a;
  const float tc = (temp_f - 32.0f) * 5.0f / 9.0f;
  const float g = std::log(rh_pct / 100.0f) + (17.27f * tc) / (237.3f + tc);
  const float dew_f = (237.3f * g) / (17.27f - g) * 9.0f / 5.0f + 32.0f;
  if (!std::isfinite(dew_f)) return a;
  a.rh_pct = rh_pct;
  a.dew_margin_known = true;
  a.dew_breach = std::isfinite(min_margin_f) && temp_f - dew_f < min_margin_f;
  return a;
}

// Degraded sensing as the engine counts it (D-IRP-12, #1271). A degraded_only window opens its
// mister by time, not VPD, so only an outage that lasts may start one. The averages read NaN for
// the first seconds after every boot, until the probes first answer and the 15 s template sensors
// run; a reboot inside a due window must not look degraded. An outage counts once the averages
// have been out for 300 s straight, and the first call with both back clears it at once. Only
// this engine input waits: the climate path's own trust gate (vpd_control_trusted(), fed !avg_ok)
// only holds VPD wetting back, so it stays immediate and a fabricated VPD is never chased.
constexpr uint32_t kDegradedGraceMs = 300000u;

struct IrrDegradedGrace {
  bool out;           // the averages were out at the last call
  bool counted;       // the grace ran out; a latch, so a millis() wrap cannot clear it
  uint32_t since_ms;  // when they went out
};

inline bool irr_degraded_after_grace(IrrDegradedGrace& g, bool averages_ok, uint32_t now_ms) {
  if (averages_ok) {
    g = IrrDegradedGrace{};
    return false;
  }
  if (!g.out) {
    g.out = true;
    g.since_ms = now_ms;
  }
  if (uint32_t(now_ms - g.since_ms) >= kDegradedGraceMs) g.counted = true;
  return g.counted;
}

// ── The mist path (F6, F10; VD-46, #1161) ───────────────────────────────────
// The climate VPD path of controls.yaml and the engine glue call these; the native tests cover
// them. Scheduled, run-now and fertilizer mist are the engine's own runs (above); these settle
// which zone a climate VPD pulse may use and the D-IRP-19 mister watchdog.

// An effective VPD member (F6): a clean mister (L04, L07, L09) whose program VPD-member bit and
// line enable bit are both set. With no program the ROM default makes L04 the only one.
inline bool irr_effective_vpd_member(const IrrProgram& p, uint8_t idx) {
  return idx < kLineCount && kMisterMaskBit[idx] != 255 && (p.enabled_mask & irr_bit(idx)) != 0 &&
         (p.vpd_member_mask & (1u << kMisterMaskBit[idx])) != 0;
}

// The effective VPD members as a line mask (bit = device_idx).
inline uint16_t irr_vpd_member_lines(const IrrProgram& p) {
  uint16_t m = 0;
  for (uint8_t z = 0; z < kZoneCount; ++z) {
    if (irr_effective_vpd_member(p, kZoneCleanLine[z])) m = uint16_t(m | irr_bit(kZoneCleanLine[z]));
  }
  return m;
}

// What the climate path knows each second that irr_vpd_zone_allowed() reads beside the program,
// the RTC block and the watchdog's masks.
struct IrrVpdZoneGate {
  bool demand;            // climate_wet_assist_demand: SEALED_MIST past fog, or the VENTILATE mist assist
  bool safety_ok;         // climate_wet_assist_safety_ok: a valid clock and climate_wet_assist_permitted()
  bool saf4_tripped;      // mister_volume_hard_block: the SAF-4 ceiling is reached
  uint16_t backoff_mask;  // mister lines backing off (D-IRP-19)
  uint16_t latch_mask;    // mister lines latched (D-IRP-19)
};

// F6: may a climate VPD pulse use clean mister `idx` (L04 center, L07 south, L09 west) now? One
// rule per zone: the climate's demand and its safety gate, the zone an effective VPD member, not
// backing off or latched (a fault on any line of the zone, D-IRP-19), its duty cap not reached
// (the clean line's daily_cap_min, 0 = none, against every mister second on the zone today,
// whatever opened it) and SAF-4 not tripped. Confirmed occupancy, the soft budget, a drip and the
// vent interlock hold VPD pulses in mister_blocked (controls.yaml); the engine's grant settles
// the manifold.
inline bool irr_vpd_zone_allowed(const IrrProgram& p, const IrrRtcBlock& rtc, uint8_t idx,
                                 const IrrVpdZoneGate& g) {
  if (!g.demand || !g.safety_ok || g.saf4_tripped || !irr_effective_vpd_member(p, idx)) return false;
  if ((g.backoff_mask | g.latch_mask) & detail::zone_lines(idx)) return false;
  const uint32_t cap_s = uint32_t(p.lines[idx].daily_cap_min) * 60u;
  return cap_s == 0 || rtc.zone_pulse_s[kMistZone[idx]] < cap_s;
}

// F6 zone choice among the zones a climate VPD pulse may use now (`allowed` by ZoneId: center,
// south, west; the east entry is ignored, east has no mister). With more than one allowed the rank
// arbiter picks, whatever sw_arbiter_zone_enabled says: arbitrate_wet_zone() takes the best owner
// rank among the zones whose VPD is above their band target (center rank 1 by
// zone_priority_center), and when none is (the house-level stress asked for the pulse) the
// best-ranked allowed zone gets it, ties to the lower ZoneId. The legacy stress score halves center
// (mister_center_penalty), so it never chooses between members. Returns the ZoneId, or -1 with
// fewer than two zones allowed: the caller keeps today's single-zone path.
inline int irr_rank_vpd_zone(const bool (&allowed)[ZONE_COUNT], const ZoneWetIntent (&intents)[ZONE_COUNT],
                             const int (&rank)[ZONE_COUNT]) {
  bool act[ZONE_COUNT] = {};
  int n = 0;
  for (int z = 0; z < ZONE_COUNT; ++z) {
    act[z] = allowed[z] && z != ZONE_EAST;
    n += act[z] ? 1 : 0;
  }
  if (n < 2) return -1;
  const int won = arbitrate_wet_zone(intents, rank, act);
  if (won >= 0) return won;
  int best = -1;
  for (int z = 0; z < ZONE_COUNT; ++z) {
    if (act[z] && (best < 0 || rank[z] < rank[best])) best = z;
  }
  return best;
}

// ── The D-IRP-19 mister watchdog (F10) ──
// Each call (the climate loop's 1 s tick on the device) it forces OFF every wet relay (L01-L10)
// that reads ON without its bit in irr_grant_mask, and every mister (L04, L07-L10) that has been ON
// longer than its commanded length + 30 s, or 300 s when no length is known. The commanded length
// is the climate VPD pulse's (its grant carries the weighted length, from its grant) or the engine
// open's (IrrOutputs::mist_cmd_s: a window or run-now pulse's on_s, a feed pulse's, or a mist
// prewet's or flush's seconds, from the ON edge). Each overrun faults the mister's zone for the
// local day: on an effective VPD member the first fault backs the zone off 10 min and the second
// 30 min, and the third latches it; on any other mister (schedule-only, and the fertilizer misters
// L08 and L10) the first fault latches it. A latch holds until local midnight or clear_fault. The
// faults live in the RTC block (IrrRtcFault); irr_state publishes each end time as an f= token.

constexpr uint32_t kWatchdogSlackMs = 30000u;     // a mister may stay ON its commanded length + 30 s
constexpr uint32_t kWatchdogCeilingMs = 300000u;  // the limit when no commanded length is known
constexpr uint32_t kBackoffFirstMs = 600000u;     // a VPD member's first fault of the day: 10 min
constexpr uint32_t kBackoffSecondMs = 1800000u;   // its second: 30 min; its third latches

// What one watchdog call samples.
struct IrrWatchdogInputs {
  uint32_t now_ms;
  uint32_t epoch;          // UTC now; 0: no valid clock
  uint32_t next_midnight;  // UTC epoch of the next local midnight, a latch's end; 0: not known
  uint16_t relay_on;       // wet relays reading ON (bit = device_idx; fog is not watched)
  uint16_t grant;          // irr_grant_mask
  uint16_t vpd_members;    // the effective VPD member lines (irr_vpd_member_lines())
  uint8_t engine_line;     // the mister the engine's own run holds open (IrrOutputs::mist_on), or kNoLine
  uint16_t engine_cmd_s;   // its commanded length (IrrOutputs::mist_cmd_s)
  uint8_t vpd_line;        // the mister a climate VPD grant names, or kNoLine
  uint16_t vpd_on_s;       // that pulse's commanded length
  uint32_t vpd_since_ms;   // when it was granted
};

// The watchdog's RAM: each mister's ON edge and the commanded length of its current open.
struct IrrWatchdog {
  uint16_t on_mask;               // the misters ON at the last call
  uint32_t since_ms[kLineCount];  // their ON edges (a VPD pulse: its grant)
  uint16_t cmd_s[kLineCount];     // their commanded lengths (0: not known, the 300 s ceiling)
  bool have_last;
  uint32_t last_ms;               // the last call, for the back-off countdowns
};

struct IrrWatchdogResult {
  uint16_t force_off;  // wet relays to turn OFF now: a mister past its limit, or any wet relay with no grant bit
  uint16_t faulted;    // the misters that faulted in this call
  bool changed;        // the fault state changed: masks and f= tokens
};

// The inputs from the engine's state as it stands (`out`: its last outputs).
inline IrrWatchdogInputs irr_watchdog_inputs(const IrrEngine& e, const IrrOutputs& out, uint32_t now_ms,
                                             uint16_t relay_on, uint16_t grant) {
  IrrWatchdogInputs in{};
  in.now_ms = now_ms;
  in.epoch = detail::epoch_now(e, now_ms);
  const int64_t midnight =
      e.in.clock.valid ? irr_local_midnight(e.in.clock, uint16_t(e.in.clock.local_day + 1)) : int64_t(0);
  in.next_midnight = midnight > 0 && midnight <= int64_t(UINT32_MAX) ? uint32_t(midnight) : 0u;
  in.relay_on = uint16_t(relay_on & kGrantableMask);
  in.grant = grant;
  in.vpd_members = irr_vpd_member_lines(e.prog);
  in.engine_line = out.mist_on;
  in.engine_cmd_s = out.mist_cmd_s;
  in.vpd_line = e.vpd_line;
  in.vpd_on_s = e.vpd_on_s;
  in.vpd_since_ms = e.vpd_since_ms;
  return in;
}

namespace detail {

// The mister line a zone's f= token and masks name: its last faulted line, else its clean line.
inline uint8_t fault_line(const IrrRtcFault& f, uint8_t zone) {
  return is_mister(f.line) && kMistZone[f.line] == zone ? f.line : kZoneCleanLine[zone];
}

// One overrun on mister `line`: count it on its zone, then back the zone off or latch it.
inline void count_fault(IrrRtcBlock& rtc, uint8_t line, bool vpd_member, uint32_t epoch, uint32_t next_midnight) {
  const uint8_t z = kMistZone[line];
  if (z >= kZoneCount) return;
  IrrRtcFault& f = rtc.fault[z];
  if (f.count < 255) ++f.count;
  f.line = line;
  if (vpd_member && f.count <= 2) {
    f.backing_off = 1;
    f.backoff_left_ms = f.count == 1 ? kBackoffFirstMs : kBackoffSecondMs;
    f.until_epoch = epoch != 0 ? epoch + f.backoff_left_ms / 1000u : 0u;
  } else {
    f.latched = 1;
    f.backing_off = 0;
    f.backoff_left_ms = 0;
    f.until_epoch = next_midnight;
  }
}

}  // namespace detail

// One watchdog call: count the back-offs down, track each mister's open, fault the overruns, and
// list every relay to force OFF. The caller turns those relays off, then hands `faulted` and the
// new masks to the engine (irr_apply_faults()) when `changed`.
inline IrrWatchdogResult irr_watchdog_step(IrrWatchdog& w, IrrRtcBlock& rtc, const IrrWatchdogInputs& in) {
  IrrWatchdogResult res{};
  bool touched = false;
  // The back-off countdowns. The first call after a boot counts nothing.
  const uint32_t dt = w.have_last ? in.now_ms - w.last_ms : 0u;
  w.have_last = true;
  w.last_ms = in.now_ms;
  for (IrrRtcFault& f : rtc.fault) {
    if (f.backing_off) {
      touched = true;
      if (dt >= f.backoff_left_ms) {
        f.backing_off = 0;
        f.backoff_left_ms = 0;
        if (!f.latched) f.until_epoch = 0;
        res.changed = true;
      } else {
        f.backoff_left_ms -= dt;
      }
    }
    // An end time the clock could not give when the fault came.
    if ((f.backing_off || f.latched) && f.until_epoch == 0 && in.epoch != 0) {
      f.until_epoch = f.latched ? in.next_midnight : in.epoch + (f.backoff_left_ms + 999u) / 1000u;
      if (f.until_epoch != 0) touched = res.changed = true;
    }
  }
  // Each mister's open: its ON edge (a VPD pulse: its grant, also when a new grant reopens a relay
  // the watchdog never saw OFF) and its commanded length; past the limit, a fault.
  for (uint8_t i = 0; i < kLineCount; ++i) {
    const uint16_t b = irr_bit(i);
    if (!detail::is_mister(i)) continue;
    if (!(in.relay_on & b)) {
      w.on_mask = uint16_t(w.on_mask & ~b);
      continue;
    }
    const bool vpd = in.vpd_line == i;
    if (!(w.on_mask & b) || (vpd && w.since_ms[i] != in.vpd_since_ms)) {
      w.on_mask = uint16_t(w.on_mask | b);
      w.since_ms[i] = vpd ? in.vpd_since_ms : in.now_ms;
      w.cmd_s[i] = vpd ? in.vpd_on_s : in.engine_line == i ? in.engine_cmd_s : uint16_t(0);
    }
    const uint32_t limit_ms = w.cmd_s[i] != 0 ? uint32_t(w.cmd_s[i]) * 1000u + kWatchdogSlackMs : kWatchdogCeilingMs;
    if (in.now_ms - w.since_ms[i] < limit_ms) continue;  // wrap-safe
    res.force_off = uint16_t(res.force_off | b);
    res.faulted = uint16_t(res.faulted | b);
    w.on_mask = uint16_t(w.on_mask & ~b);
    detail::count_fault(rtc, i, (in.vpd_members & b) != 0, in.epoch, in.next_midnight);
    touched = res.changed = true;
  }
  // Every wet relay ON without its grant bit goes OFF (not a fault: no length was overrun).
  res.force_off = uint16_t(res.force_off | (in.relay_on & kGrantableMask & ~in.grant));
  if (touched) irr_rtc_seal(rtc);
  return res;
}

// The back-off and latch masks the engine (IrrInputs) and irr_vpd_zone_allowed() read: one bit per
// faulted zone, its faulted line (both test every line of the zone).
inline void irr_fault_masks(const IrrRtcBlock& rtc, uint16_t& backoff, uint16_t& latch) {
  backoff = 0;
  latch = 0;
  for (uint8_t z = 0; z < kZoneCount; ++z) {
    const IrrRtcFault& f = rtc.fault[z];
    const uint16_t b = irr_bit(detail::fault_line(f, z));
    if (f.latched) latch = uint16_t(latch | b);
    else if (f.backing_off) backoff = uint16_t(backoff | b);
  }
}

// irr_state's f= tokens (F3): each zone backing off or latched, named by its last faulted line,
// with the back-off's end or the latch's local midnight.
inline void irr_fault_tokens(const IrrRtcBlock& rtc, IrrStateExtras& x) {
  x.fault_count = 0;
  for (uint8_t z = 0; z < kZoneCount && x.fault_count < kMaxFaults; ++z) {
    const IrrRtcFault& f = rtc.fault[z];
    if (!f.latched && !f.backing_off) continue;
    x.faults[x.fault_count++] = {detail::fault_line(f, z), f.latched != 0, f.until_epoch};
  }
}

// clear_fault (irr_command, F4): zero the zone's fault count and its latch, for the zone of mister
// `line`, or every zone (kAllLines). A back-off still running ends at its own time. Returns true
// when anything changed.
inline bool irr_fault_clear(IrrRtcBlock& rtc, uint8_t line) {
  bool changed = false;
  for (uint8_t z = 0; z < kZoneCount; ++z) {
    if (line != kAllLines && (line >= kLineCount || kMistZone[line] != z)) continue;
    IrrRtcFault& f = rtc.fault[z];
    if (f.count == 0 && !f.latched) continue;
    f.count = 0;
    f.latched = 0;
    if (!f.backing_off) f = IrrRtcFault{};
    changed = true;
  }
  if (changed) irr_rtc_seal(rtc);
  return changed;
}

}  // namespace verdify_irr
