// policy_vector.h — firmware atomic policy engine (Lane E, #586; audit §8.9).
//
// Hand-written, host-compilable C++17 on top of the generated codec
// (policy_vector_generated.h) and the generated immutable ROM baseline
// (policy_rom_baseline_generated.h). No ESPHome dependencies, no heap in the
// hot path: every buffer is static/fixed-size, the staging arena is one shared
// scratch buffer, and the per-tick snapshot is a small by-value struct.
//
// Surfaces (all single-threaded — the ESPHome main loop):
//   - ControlPolicy slots: active / boundary_pending / tactical_pending plus
//     the constexpr ROM baseline (registry defaults; content hash pinned to
//     the Python-computed registry_defaults golden at generation time).
//   - Staging state machine: begin_policy → stage_chunk* → validate_policy →
//     commit_policy(effective_at) → the swap happens on the next control-tick
//     boundary via on_tick(). abort_policy() is idempotent.
//   - ExperimentPolicyManifest staging/arming (manifest_begin → stage_chunk*
//     → manifest_commit), gating every content-changing commit; same-content
//     identity rebinds bypass the edge gate but must match the active member
//     byte-for-byte.
//   - Two-copy sequence-numbered crash-safe journal MODEL with pluggable
//     storage (host tests inject corruption/power loss; the ESP32 build binds
//     NVS via policy_journal_esphome.h).
//   - Conservative reboot semantics: boot_init() resumes a valid persisted
//     policy under the same identity or falls back to the ROM baseline
//     atomically; an unknown same-day water high-water mark conservatively
//     marks the wetting budget consumed until the next day rollover; one
//     typed ResetEvent describes the outcome.
//
// Static memory budget (approximate, measured by sizeof in the host tests):
//   3 × ControlPolicy slots ≈ 1.5 KB, staging arena 288 B, armed manifest
//   ≈ 650 B, journal scratch 1.3 KB → ≈ 3.8 KB of .bss, zero heap.

#pragma once

#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>

#include "policy_rom_baseline_generated.h"
#include "policy_sha256.h"
#include "policy_vector_generated.h"

namespace verdify_policy {

// ── Wire/manifest constants ─────────────────────────────────────────────────

constexpr uint8_t kManifestMagic[4] = {'V', 'P', 'M', '1'};
constexpr uint8_t kManifestSchemaVersion = 1;
constexpr char kManifestDomainTag[] = "verdify-policy-manifest-v1";

// Manifest kinds reuse the §8.9 treatment-tag octets.
constexpr uint8_t kManifestKindRandomized = 0x01;
constexpr uint8_t kManifestKindQualification = 0x02;
constexpr uint8_t kManifestKindAa = 0x03;

constexpr uint8_t kManifestTemplateCount = 3;  // baseline / moderate / aggressive
// Directed content-changing edges between the three templates, bit layout:
// bit0 0→1, bit1 1→0, bit2 0→2, bit3 2→0, bit4 1→2, bit5 2→1.
constexpr uint8_t kManifestEdgeBitmapMask = 0x3F;

// Canonical ExperimentPolicyManifest wire format, version 1 (big-endian):
//   magic "VPM1" | schema u8 | kind u8 | experiment uuid (16) |
//   manifest_generation u32 | template_count u8 (=3) |
//   3 × (template uuid 16 || content sha256 32) | edge_bitmap u8 |
//   spec_ref | qualification_result_ref | aa_result_ref |
//   valid_from u32 (epoch s) | valid_to u32
// where each optional result reference is 0x00 (absent) or 0x01 || sha256.
// manifest_sha256 = SHA-256("verdify-policy-manifest-v1" || 0x00 || bytes).
constexpr size_t kManifestFixedPrefixSize = 4 + 1 + 1 + 16 + 4 + 1 + 3 * (16 + 32) + 1;
constexpr size_t kManifestMinSize = kManifestFixedPrefixSize + 3 * 1 + 8;
constexpr size_t kManifestMaxSize = kManifestFixedPrefixSize + 3 * 33 + 8;

// One shared staging arena for both transfer types (only one transfer may be
// in flight at a time). The policy vector needs 181 B; the manifest up to
// kManifestMaxSize (283 B).
constexpr size_t kStagingArenaSize = 288;
constexpr size_t kMaxStageChunk = 192;  // bounds every api chunk allocation

// policy_begin header blob, hex-encoded over the native API (big-endian):
//   schema u8 | slot u8 (1=boundary, 2=tactical) |
//   commit kind u8 (1=content_change, 2=identity_rebind) |
//   generation u32 | content sha256 (32) | has_activation u8 |
//   activation sha256 (32; zero when has_activation=0) | assignment uuid (16)
//   | valid_from u32 (epoch s) | valid_to u32 | expected_len u16 |
//   treatment_len u8 | treatment octets (0..35)
constexpr size_t kPolicyBeginHeaderMin = 1 + 1 + 1 + 4 + 32 + 1 + 32 + 16 + 4 + 4 + 2 + 1;
constexpr size_t kPolicyBeginHeaderMax = kPolicyBeginHeaderMin + 35;

enum class PolicySlot : uint8_t { kBoundary = 1, kTactical = 2 };
enum class CommitKind : uint8_t { kNone = 0, kContentChange = 1, kIdentityRebind = 2 };

// ── ControlPolicy ───────────────────────────────────────────────────────────

struct ControlPolicy {
  float values[kPolicyFieldCount] = {};             // decoded engineering values
  uint8_t vector_bytes[kPolicyVectorSize] = {};     // canonical wire bytes
  uint8_t schema_version = 0;
  uint32_t generation = 0;
  uint8_t content_sha256[32] = {};
  bool has_activation = false;
  uint8_t activation_sha256[32] = {};
  uint8_t assignment_id[16] = {};
  uint32_t valid_from_s = 0;  // epoch seconds; 0 = unbounded (ROM baseline)
  uint32_t valid_to_s = 0;    // 0 = never expires (ROM baseline)
  bool has_manifest_binding = false;
  uint8_t manifest_sha256[32] = {};
  CommitKind commit_kind = CommitKind::kNone;
};

constexpr ControlPolicy make_rom_baseline_policy() {
  ControlPolicy policy{};
  for (size_t i = 0; i < kPolicyFieldCount; ++i) {
    policy.values[i] =
        static_cast<float>(static_cast<double>(kRomBaselineRaws[i]) / static_cast<double>(kPolicyFields[i].scale));
  }
  for (size_t i = 0; i < kPolicyVectorSize; ++i) policy.vector_bytes[i] = kRomBaselineVectorBytes[i];
  policy.schema_version = kWireSchemaVersion;
  policy.generation = 0;
  for (size_t i = 0; i < 32; ++i) policy.content_sha256[i] = kRomBaselineContentSha256[i];
  return policy;
}

// The immutable, manifest-independent fallback. Lives in flash/rodata.
inline constexpr ControlPolicy kRomBaselinePolicy = make_rom_baseline_policy();

// ── Per-tick snapshot ───────────────────────────────────────────────────────

// Captured by value ONCE at the top of each control tick and threaded through
// the FSM; consumers must not read policy globals mid-tick while an
// experiment is armed (audit §8.9).
struct PolicySnapshot {
  float values[kPolicyFieldCount] = {};
  uint32_t generation = 0;
  uint8_t schema_version = 0;
  // True only while an experiment manifest is armed — the runtime gate for
  // exemplar consumers: inactive ⇒ read legacy globals (bit-identical to
  // today), active ⇒ read this snapshot.
  bool experiment_active = false;
  uint8_t content_sha8[8] = {};
};

// ── Cross-field rules ───────────────────────────────────────────────────────

// Cross-field violations rejected at validate_policy() time (§8.9). The
// deployed FSM has no compiled cross-field asserts between these tunables
// (bounds live per-field in the ESPHome entities), so these are the minimal
// physically-required relations; raw comparisons are exact because each pair
// shares one wire scale.
//   1. mister_engage_kpa ≤ mister_all_kpa (all-zones threshold escalates the
//      engage threshold; both scale 20).
//   2. enthalpy_open < enthalpy_close (hysteresis pair; both scale 2).
//   3. mister_engage_delay_s ≤ mister_all_delay_s (all-zones delay escalates
//      the engage delay; both scale 1).
inline bool policy_cross_field_ok(const int64_t raws[kPolicyFieldCount], uint8_t* bad_index) {
  const auto fail = [&](uint8_t index) {
    if (bad_index != nullptr) *bad_index = index;
    return false;
  };
  if (raws[kPF_mister_engage_kpa] > raws[kPF_mister_all_kpa]) return fail(kPF_mister_engage_kpa);
  if (raws[kPF_enthalpy_open] >= raws[kPF_enthalpy_close]) return fail(kPF_enthalpy_open);
  if (raws[kPF_mister_engage_delay_s] > raws[kPF_mister_all_delay_s]) return fail(kPF_mister_engage_delay_s);
  return true;
}

// ── Experiment manifest ─────────────────────────────────────────────────────

struct ExperimentPolicyManifest {
  uint8_t kind = 0;
  uint8_t experiment_id[16] = {};
  uint32_t manifest_generation = 0;
  uint8_t template_ids[kManifestTemplateCount][16] = {};
  uint8_t template_content_sha[kManifestTemplateCount][32] = {};
  uint8_t edge_bitmap = 0;
  bool has_spec_ref = false;
  uint8_t spec_sha256[32] = {};
  bool has_qualification_result = false;
  uint8_t qualification_result_sha256[32] = {};
  bool has_aa_result = false;
  uint8_t aa_result_sha256[32] = {};
  uint32_t valid_from_s = 0;
  uint32_t valid_to_s = 0;
  // Derived on commit:
  uint8_t manifest_sha256[32] = {};
  uint8_t raw_bytes[kManifestMaxSize] = {};  // canonical bytes, for journaling
  uint16_t raw_len = 0;
};

// ── Engine errors / states ──────────────────────────────────────────────────

enum class PolicyError : uint8_t {
  kNone = 0,
  kBusy,               // another transfer is already staging
  kSlotOccupied,       // pending slot not empty; explicit abort required
  kBadHeader,          // malformed policy_begin header
  kBadChunk,           // out-of-order/duplicate/oversized chunk
  kIncomplete,         // validate before all bytes staged
  kMalformedVector,    // strict decode failed (magic/schema/bounds/order)
  kHashMismatch,       // recomputed content hash != declared
  kStaleGeneration,    // generation not strictly greater than last committed
  kBadValidity,        // valid_from >= valid_to, or tactical crosses boundary
  kCrossField,         // policy_cross_field_ok failed
  kNoManifest,         // no armed experiment manifest
  kNotInManifest,      // content hash is not an armed template member
  kEdgeNotPermitted,   // directed content-changing edge not in the bitmap
  kRebindMismatch,     // identity_rebind bytes/content differ from active
  kActivationMismatch, // recomputed activation hash != declared
  kBadManifest,        // manifest parse/shape failure
  kManifestKindGate,   // kind-specific result-reference rules violated
  kNotValidated,       // commit without successful validate
  kNotStaging,         // chunk/validate without begin
  kClockInvalid,       // operation requires a valid clock
};

const char* policy_error_name(PolicyError error);

enum class SlotState : uint8_t { kEmpty = 0, kStaging = 1, kValidated = 2, kCommitted = 3 };

// ── Journal model ───────────────────────────────────────────────────────────

// Two-copy, sequence-numbered crash-safe record:
//   magic "VPJ1" | journal schema u8 (=1) | seq u32 | last_committed_generation
//   u32 | payload_len u16 | payload | sha256 over bytes[0..payload) (32) |
//   crc32 over bytes[0..sha) (4)
// journal_write() alternates copies (writes the copy NOT holding the current
// highest valid sequence), so a torn write can only destroy the older copy.
// Both copies corrupt/incompatible ⇒ boot_init falls back to the ROM baseline.
constexpr uint8_t kJournalMagic[4] = {'V', 'P', 'J', '1'};
constexpr uint8_t kJournalSchemaVersion = 1;
constexpr size_t kJournalRecordCap = 1280;

class PolicyJournalStorage {
 public:
  virtual ~PolicyJournalStorage() = default;
  // copy is 0 or 1. read_copy returns false when the copy is absent/unreadable.
  virtual bool read_copy(uint8_t copy, uint8_t* out, size_t cap, size_t* out_len) = 0;
  virtual bool write_copy(uint8_t copy, const uint8_t* data, size_t len) = 0;
};

inline uint32_t policy_crc32(const uint8_t* data, size_t len) {
  uint32_t crc = 0xFFFFFFFFu;
  for (size_t i = 0; i < len; ++i) {
    crc ^= data[i];
    for (int bit = 0; bit < 8; ++bit) crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u)));
  }
  return crc ^ 0xFFFFFFFFu;
}

// ── Reboot semantics ────────────────────────────────────────────────────────

enum class BootOutcome : uint8_t {
  kRomNoJournal = 0,    // no/empty journal → ROM baseline
  kRomCorruptJournal,   // both copies corrupt/incompatible → ROM baseline
  kRomClockInvalid,     // no valid clock at boot → ROM baseline
  kRomExpired,          // persisted policy validity expired → ROM baseline
  kResumedActive,       // same-identity resume of the persisted active policy
};

struct ResetEvent {
  BootOutcome outcome = BootOutcome::kRomNoJournal;
  uint32_t boot_epoch_s = 0;
  // §8.9 v1 rule: with no independently verified same-day water high-water
  // mark, the assigned wetting budget and hard ceiling are marked consumed
  // until the next verified local-day rollover.
  bool water_budget_marked_consumed = true;
  uint32_t resumed_generation = 0;
  uint8_t active_content_sha8[8] = {};
  bool manifest_rearmed = false;
};

// ── Engine ──────────────────────────────────────────────────────────────────

class PolicyEngine {
 public:
  PolicyEngine() { reset_runtime(); }

  // Full re-initialization (host tests). Does not touch storage.
  void reset_runtime() {
    active_ = &kRomBaselinePolicy;
    boundary_ = SlotRec{};
    tactical_ = SlotRec{};
    transfer_ = Transfer{};
    manifest_armed_ = false;
    manifest_ = ExperimentPolicyManifest{};
    last_committed_generation_ = 0;
    last_error_ = PolicyError::kNone;
    water_budget_consumed_ = false;
    water_day_stamp_ = 0;
    water_high_centigal_ = 0;
    boundary_effective_at_ = 0;
    tactical_effective_at_ = 0;
  }

  void bind_storage(PolicyJournalStorage* storage) { storage_ = storage; }

  // ── Boot ──────────────────────────────────────────────────────────────────
  // Load the journal and either resume the persisted active policy under the
  // same identity or fall back to the immutable ROM baseline. Always emits
  // one typed ResetEvent. Callers must additionally boot all relays off and
  // enforce full minimum-off dwell from boot time (done in controls.yaml).
  // local_day_stamp is a caller-provided opaque local-calendar-day token
  // (0 = unknown); the water high-water rule compares it against the
  // journaled stamp, so the "same day" test follows the greenhouse's local
  // day, not the UTC epoch day.
  ResetEvent boot_init(uint32_t now_epoch_s, bool clock_valid, uint32_t local_day_stamp) {
    reset_runtime();
    ResetEvent event{};
    event.boot_epoch_s = now_epoch_s;
    event.water_budget_marked_consumed = true;
    water_budget_consumed_ = true;
    water_day_stamp_ = clock_valid ? local_day_stamp : 0;

    size_t payload_len = 0;
    uint32_t seq = 0;
    if (storage_ == nullptr || !journal_load(&payload_len, &seq)) {
      bool have_any = false;
      if (storage_ != nullptr) {
        size_t probe_len = 0;
        for (uint8_t copy = 0; copy < 2; ++copy) {
          if (storage_->read_copy(copy, journal_buf_a_, sizeof(journal_buf_a_), &probe_len) && probe_len > 0) {
            have_any = true;
          }
        }
      }
      event.outcome = have_any ? BootOutcome::kRomCorruptJournal : BootOutcome::kRomNoJournal;
      copy_sha8(event.active_content_sha8, kRomBaselinePolicy.content_sha256);
      return event;
    }
    journal_seq_ = seq;

    PersistedState state{};
    if (!decode_payload(journal_buf_b_ + kJournalHeaderSize, payload_len, &state)) {
      event.outcome = BootOutcome::kRomCorruptJournal;
      copy_sha8(event.active_content_sha8, kRomBaselinePolicy.content_sha256);
      return event;
    }

    last_committed_generation_ = state.last_committed_generation;

    // Water high-water: only a journaled same-local-day mark lifts the
    // conservative consumed flag; it can never roll the mark backward.
    if (clock_valid && local_day_stamp != 0 && state.water_day_stamp == local_day_stamp) {
      water_budget_consumed_ = state.water_budget_consumed;
      water_high_centigal_ = state.water_high_centigal;
      event.water_budget_marked_consumed = water_budget_consumed_;
    }

    if (state.has_manifest) {
      ExperimentPolicyManifest manifest{};
      if (parse_manifest(state.manifest_bytes, state.manifest_len, &manifest) == PolicyError::kNone) {
        manifest_ = manifest;
        manifest_armed_ = true;
        event.manifest_rearmed = true;
      } else {
        event.outcome = BootOutcome::kRomCorruptJournal;
        copy_sha8(event.active_content_sha8, kRomBaselinePolicy.content_sha256);
        return event;
      }
    }

    if (!clock_valid) {
      // Invalid clock activates the ROM baseline atomically (§8.9 fail-safe).
      event.outcome = BootOutcome::kRomClockInvalid;
      copy_sha8(event.active_content_sha8, kRomBaselinePolicy.content_sha256);
      return event;
    }

    // Pending slots resume regardless of the active block (validated/
    // committed vectors survive reboot; their validity is re-checked at the
    // tick boundary before any apply).
    if (state.has_boundary) {
      boundary_.policy = state.boundary;
      boundary_.state = state.boundary_state;
      boundary_effective_at_ = state.boundary_effective_at;
    }
    if (state.has_tactical) {
      tactical_.policy = state.tactical;
      tactical_.state = state.tactical_state;
      tactical_effective_at_ = state.tactical_effective_at;
    }

    if (state.has_active) {
      if (state.active.valid_to_s != 0 && now_epoch_s > state.active.valid_to_s) {
        event.outcome = BootOutcome::kRomExpired;
        copy_sha8(event.active_content_sha8, kRomBaselinePolicy.content_sha256);
        return event;
      }
      active_storage_ = state.active;
      active_ = &active_storage_;
      event.outcome = BootOutcome::kResumedActive;
      event.resumed_generation = state.active.generation;
      copy_sha8(event.active_content_sha8, state.active.content_sha256);
      return event;
    }

    event.outcome = BootOutcome::kRomNoJournal;
    copy_sha8(event.active_content_sha8, active_->content_sha256);
    return event;
  }

  // ── Manifest staging ──────────────────────────────────────────────────────
  bool manifest_begin(size_t expected_len) {
    if (transfer_.kind != TransferKind::kNone) return fail(PolicyError::kBusy);
    if (expected_len < kManifestMinSize || expected_len > kManifestMaxSize) return fail(PolicyError::kBadHeader);
    transfer_ = Transfer{};
    transfer_.kind = TransferKind::kManifest;
    transfer_.expected_len = expected_len;
    return ok();
  }

  bool manifest_commit() {
    if (transfer_.kind != TransferKind::kManifest) return fail(PolicyError::kNotStaging);
    if (transfer_.received != transfer_.expected_len) return fail(PolicyError::kIncomplete);
    // Arming (or re-arming) a manifest invalidates any pending vectors: the
    // caller must abort them first so nothing staged under the old manifest
    // can commit under the new one.
    if (boundary_.state != SlotState::kEmpty || tactical_.state != SlotState::kEmpty) {
      transfer_ = Transfer{};
      return fail(PolicyError::kSlotOccupied);
    }
    ExperimentPolicyManifest manifest{};
    const PolicyError parse_error = parse_manifest(arena_, transfer_.received, &manifest);
    transfer_ = Transfer{};
    if (parse_error != PolicyError::kNone) return fail(parse_error);
    if (manifest_armed_ && manifest.manifest_generation <= manifest_.manifest_generation) {
      return fail(PolicyError::kStaleGeneration);
    }
    manifest_ = manifest;
    manifest_armed_ = true;
    journal_write();
    return ok();
  }

  // Idempotent: clears any in-flight staging transfer (manifest or vector).
  void manifest_abort() { transfer_ = Transfer{}; }

  // Explicit disarm (rollback path; not exposed as a service in this PR).
  void disarm_manifest() {
    manifest_armed_ = false;
    manifest_ = ExperimentPolicyManifest{};
    journal_write();
  }

  // ── Vector staging ────────────────────────────────────────────────────────
  bool begin_policy(const uint8_t* header, size_t header_len) {
    if (transfer_.kind != TransferKind::kNone) return fail(PolicyError::kBusy);
    PendingHeader parsed{};
    if (!parse_begin_header(header, header_len, &parsed)) return fail(PolicyError::kBadHeader);
    SlotRec& slot = slot_for(parsed.slot);
    if (slot.state != SlotState::kEmpty) return fail(PolicyError::kSlotOccupied);
    transfer_ = Transfer{};
    transfer_.kind = TransferKind::kVector;
    transfer_.expected_len = kPolicyVectorSize;
    transfer_.header = parsed;
    slot.state = SlotState::kStaging;
    return ok();
  }

  bool stage_chunk(size_t offset, const uint8_t* data, size_t len) {
    if (transfer_.kind == TransferKind::kNone) return fail(PolicyError::kNotStaging);
    if (data == nullptr || len == 0 || len > kMaxStageChunk) return fail(PolicyError::kBadChunk);
    // Ordered staging: each chunk must start exactly where the last ended —
    // duplicates, gaps, and overlaps are all rejected.
    if (offset != transfer_.received || transfer_.received + len > transfer_.expected_len) {
      return fail(PolicyError::kBadChunk);
    }
    std::memcpy(arena_ + transfer_.received, data, len);
    transfer_.received += len;
    return ok();
  }

  bool validate_policy() {
    if (transfer_.kind != TransferKind::kVector) return fail(PolicyError::kNotStaging);
    const PendingHeader header = transfer_.header;
    SlotRec& slot = slot_for(header.slot);
    const PolicyError error = validate_staged_vector(header, &slot.policy);
    transfer_ = Transfer{};
    if (error != PolicyError::kNone) {
      slot.state = SlotState::kEmpty;
      return fail(error);
    }
    slot.state = SlotState::kValidated;
    return ok();
  }

  bool commit_policy(PolicySlot which, uint32_t effective_at_s) {
    SlotRec& slot = slot_for(which);
    if (slot.state != SlotState::kValidated) return fail(PolicyError::kNotValidated);
    if (effective_at_s == 0) return fail(PolicyError::kClockInvalid);
    if (slot.policy.valid_to_s != 0 && effective_at_s > slot.policy.valid_to_s) return fail(PolicyError::kBadValidity);
    slot.state = SlotState::kCommitted;
    if (which == PolicySlot::kBoundary) {
      boundary_effective_at_ = effective_at_s;
    } else {
      tactical_effective_at_ = effective_at_s;
    }
    journal_write();
    return ok();
  }

  // Idempotent abort of one pending slot (and its in-flight staging).
  bool abort_policy(PolicySlot which) {
    SlotRec& slot = slot_for(which);
    if (transfer_.kind == TransferKind::kVector && transfer_.header.slot == which) transfer_ = Transfer{};
    const bool had_content = slot.state != SlotState::kEmpty;
    slot = SlotRec{};
    if (which == PolicySlot::kBoundary) {
      boundary_effective_at_ = 0;
    } else {
      tactical_effective_at_ = 0;
    }
    if (had_content) journal_write();
    return ok();
  }

  // ── Tick boundary ─────────────────────────────────────────────────────────
  // Called ONCE at the top of each one-second control tick. Applies at most
  // one due pointer swap (boundary generation wins), enforces expiry and
  // clock rules, and returns the snapshot for this tick. local_day_stamp is
  // the caller's opaque local-calendar-day token (see boot_init).
  PolicySnapshot on_tick(uint32_t now_epoch_s, bool clock_valid, uint32_t local_day_stamp) {
    if (!clock_valid) {
      // §8.9 fail-safe: invalid clock activates the immutable ROM baseline.
      if (active_ != &kRomBaselinePolicy) {
        active_ = &kRomBaselinePolicy;
        journal_write();
      }
      return make_snapshot();
    }

    roll_water_day(local_day_stamp);

    // Expiry of the active policy → ROM baseline, atomically.
    if (active_->valid_to_s != 0 && now_epoch_s > active_->valid_to_s) {
      active_ = &kRomBaselinePolicy;
      journal_write();
    }

    // Due boundary generation wins over a due tactical vector.
    if (boundary_.state == SlotState::kCommitted && boundary_effective_at_ != 0 &&
        now_epoch_s >= boundary_effective_at_) {
      apply_slot(boundary_, now_epoch_s);
      boundary_effective_at_ = 0;
      // A tactical vector must end before the boundary; anything still
      // pending at the boundary swap is stale by construction — discard it.
      tactical_ = SlotRec{};
      tactical_effective_at_ = 0;
    } else if (tactical_.state == SlotState::kCommitted && tactical_effective_at_ != 0 &&
               now_epoch_s >= tactical_effective_at_) {
      apply_slot(tactical_, now_epoch_s);
      tactical_effective_at_ = 0;
    }

    return make_snapshot();
  }

  // Snapshot without side effects (diagnostics/readback).
  PolicySnapshot policy_snapshot() const { return make_snapshot(); }

  // ── Water budget (conservative reboot semantics, §8.9 v1) ─────────────────
  void note_water_high_water(uint32_t local_day_stamp, float gallons_today) {
    roll_water_day(local_day_stamp);
    const uint32_t centigal = static_cast<uint32_t>(gallons_today * 100.0f + 0.5f);
    if (centigal > water_high_centigal_) water_high_centigal_ = centigal;  // never rolls backward
  }
  bool water_budget_marked_consumed() const { return water_budget_consumed_; }
  float water_high_water_gal() const { return static_cast<float>(water_high_centigal_) / 100.0f; }

  // Persist the current state explicitly (e.g. periodic slow journal cadence
  // decided by the caller — the engine itself journals on state transitions).
  bool journal_write() {
    if (storage_ == nullptr) return false;
    const size_t len = encode_record(journal_buf_a_);
    if (len == 0) return false;
    // Alternate: overwrite the copy NOT holding the newest valid sequence.
    const uint8_t target = static_cast<uint8_t>((journal_seq_ + 1) & 1);
    ++journal_seq_;
    return storage_->write_copy(target, journal_buf_a_, len);
  }

  // ── Readback ──────────────────────────────────────────────────────────────
  const ControlPolicy& active() const { return *active_; }
  bool rom_baseline_active() const { return active_ == &kRomBaselinePolicy; }
  bool manifest_armed() const { return manifest_armed_; }
  const ExperimentPolicyManifest& armed_manifest() const { return manifest_; }
  SlotState boundary_state() const { return boundary_.state; }
  SlotState tactical_state() const { return tactical_.state; }
  const ControlPolicy& boundary_pending() const { return boundary_.policy; }
  const ControlPolicy& tactical_pending() const { return tactical_.policy; }
  uint32_t last_committed_generation() const { return last_committed_generation_; }
  PolicyError last_error() const { return last_error_; }

  // Compact identity line for the single aggregated readback text sensor:
  // "v<schema>|g<generation>|c<content8>|a<activation8|->|s<assignment8|->|<state>"
  // out must hold >= 96 bytes.
  void identity_readback(char* out, size_t cap) const {
    char content8[17];
    char activation8[17];
    char assignment8[17];
    hex8(content8, active_->content_sha256);
    if (active_->has_activation) {
      hex8(activation8, active_->activation_sha256);
    } else {
      activation8[0] = '-';
      activation8[1] = '\0';
    }
    bool assignment_zero = true;
    for (size_t i = 0; i < 16; ++i) assignment_zero = assignment_zero && active_->assignment_id[i] == 0;
    if (assignment_zero) {
      assignment8[0] = '-';
      assignment8[1] = '\0';
    } else {
      hex8(assignment8, active_->assignment_id);
    }
    const char* state = rom_baseline_active() ? "ROM" : "ACTIVE";
    std::snprintf(out, cap, "v%u|g%lu|c%s|a%s|s%s|%s", static_cast<unsigned>(active_->schema_version),
                  static_cast<unsigned long>(active_->generation), content8, activation8, assignment8, state);
  }

 private:
  enum class TransferKind : uint8_t { kNone = 0, kVector, kManifest };

  struct PendingHeader {
    PolicySlot slot = PolicySlot::kBoundary;
    CommitKind kind = CommitKind::kNone;
    uint8_t schema_version = 0;
    uint32_t generation = 0;
    uint8_t content_sha256[32] = {};
    bool has_activation = false;
    uint8_t activation_sha256[32] = {};
    uint8_t assignment_id[16] = {};
    uint32_t valid_from_s = 0;
    uint32_t valid_to_s = 0;
    uint16_t expected_len = 0;
    uint8_t treatment_len = 0;
    uint8_t treatment[35] = {};
  };

  struct Transfer {
    TransferKind kind = TransferKind::kNone;
    size_t expected_len = 0;
    size_t received = 0;
    PendingHeader header{};
  };

  struct SlotRec {
    SlotState state = SlotState::kEmpty;
    ControlPolicy policy{};
  };

  struct PersistedState {
    bool has_active = false;
    ControlPolicy active{};
    bool has_boundary = false;
    ControlPolicy boundary{};
    SlotState boundary_state = SlotState::kEmpty;
    uint32_t boundary_effective_at = 0;
    bool has_tactical = false;
    ControlPolicy tactical{};
    SlotState tactical_state = SlotState::kEmpty;
    uint32_t tactical_effective_at = 0;
    bool has_manifest = false;
    uint8_t manifest_bytes[kManifestMaxSize] = {};
    uint16_t manifest_len = 0;
    uint32_t last_committed_generation = 0;
    bool water_budget_consumed = true;
    uint32_t water_day_stamp = 0;
    uint32_t water_high_centigal = 0;
  };

  static constexpr size_t kJournalHeaderSize = 4 + 1 + 4 + 4 + 2;

  // ── helpers ──────────────────────────────────────────────────────────────
  static void copy_sha8(uint8_t out[8], const uint8_t sha[32]) { std::memcpy(out, sha, 8); }

  static void hex8(char out[17], const uint8_t* bytes) {
    static const char digits[] = "0123456789abcdef";
    for (size_t i = 0; i < 8; ++i) {
      out[2 * i] = digits[bytes[i] >> 4];
      out[2 * i + 1] = digits[bytes[i] & 0x0F];
    }
    out[16] = '\0';
  }

  SlotRec& slot_for(PolicySlot which) { return which == PolicySlot::kBoundary ? boundary_ : tactical_; }

  bool fail(PolicyError error) {
    last_error_ = error;
    return false;
  }
  bool ok() {
    last_error_ = PolicyError::kNone;
    return true;
  }

  PolicySnapshot make_snapshot() const {
    PolicySnapshot snap{};
    std::memcpy(snap.values, active_->values, sizeof(snap.values));
    snap.generation = active_->generation;
    snap.schema_version = active_->schema_version;
    snap.experiment_active = manifest_armed_;
    copy_sha8(snap.content_sha8, active_->content_sha256);
    return snap;
  }

  void roll_water_day(uint32_t local_day_stamp) {
    if (local_day_stamp == 0) return;  // unknown local day: never clears the flag
    if (water_day_stamp_ == 0) {
      // Clock acquisition is NOT a rollover: adopt the day, keep the
      // conservative consumed flag exactly as it is.
      water_day_stamp_ = local_day_stamp;
      return;
    }
    if (local_day_stamp != water_day_stamp_) {
      // Verified local-day rollover clears the conservative consumed flag and
      // the high-water mark for the new day.
      water_day_stamp_ = local_day_stamp;
      water_budget_consumed_ = false;
      water_high_centigal_ = 0;
    }
  }

  void apply_slot(SlotRec& slot, uint32_t now_epoch_s) {
    // Expired-before-apply vectors are discarded, never activated.
    if (slot.policy.valid_to_s != 0 && now_epoch_s > slot.policy.valid_to_s) {
      slot = SlotRec{};
      journal_write();
      return;
    }
    // The single atomic action: copy into the active storage, then swap ONE
    // pointer. Single-threaded main loop ⇒ no torn reads. The high-water
    // generation stays monotone even when a due boundary (staged earlier,
    // lower generation) wins over an intraday tactical vector.
    active_storage_ = slot.policy;
    active_ = &active_storage_;
    if (slot.policy.generation > last_committed_generation_) last_committed_generation_ = slot.policy.generation;
    slot = SlotRec{};
    journal_write();
  }

  static bool parse_begin_header(const uint8_t* data, size_t len, PendingHeader* out) {
    if (data == nullptr || len < kPolicyBeginHeaderMin || len > kPolicyBeginHeaderMax) return false;
    size_t offset = 0;
    const auto u8 = [&]() { return data[offset++]; };
    const auto u16 = [&]() {
      const uint16_t value = static_cast<uint16_t>((data[offset] << 8) | data[offset + 1]);
      offset += 2;
      return value;
    };
    const auto u32 = [&]() {
      const uint32_t value = (static_cast<uint32_t>(data[offset]) << 24) | (static_cast<uint32_t>(data[offset + 1]) << 16) |
                             (static_cast<uint32_t>(data[offset + 2]) << 8) | data[offset + 3];
      offset += 4;
      return value;
    };
    out->schema_version = u8();
    const uint8_t slot = u8();
    if (slot != 1 && slot != 2) return false;
    out->slot = static_cast<PolicySlot>(slot);
    const uint8_t kind = u8();
    if (kind != 1 && kind != 2) return false;
    out->kind = static_cast<CommitKind>(kind);
    out->generation = u32();
    std::memcpy(out->content_sha256, data + offset, 32);
    offset += 32;
    const uint8_t has_activation = u8();
    if (has_activation > 1) return false;
    out->has_activation = has_activation == 1;
    std::memcpy(out->activation_sha256, data + offset, 32);
    offset += 32;
    std::memcpy(out->assignment_id, data + offset, 16);
    offset += 16;
    out->valid_from_s = u32();
    out->valid_to_s = u32();
    out->expected_len = u16();
    out->treatment_len = u8();
    if (out->treatment_len > 35 || len != kPolicyBeginHeaderMin + out->treatment_len) return false;
    std::memcpy(out->treatment, data + offset, out->treatment_len);
    if (out->expected_len != kPolicyVectorSize) return false;
    if (out->schema_version != kWireSchemaVersion) return false;
    if (out->generation == 0) return false;
    return true;
  }

  int find_template(const uint8_t content_sha[32]) const {
    for (int i = 0; i < kManifestTemplateCount; ++i) {
      if (std::memcmp(manifest_.template_content_sha[i], content_sha, 32) == 0) return i;
    }
    return -1;
  }

  static bool edge_permitted(uint8_t bitmap, int from, int to) {
    static const uint8_t kEdgeBit[3][3] = {{0xFF, 0, 2}, {1, 0xFF, 4}, {3, 5, 0xFF}};
    if (from < 0 || from > 2 || to < 0 || to > 2 || from == to) return false;
    return (bitmap >> kEdgeBit[from][to]) & 1;
  }

  PolicyError validate_staged_vector(const PendingHeader& header, ControlPolicy* out) {
    if (transfer_.received != transfer_.expected_len) return PolicyError::kIncomplete;

    int64_t raws[kPolicyFieldCount];
    if (!decode_policy_vector(arena_, kPolicyVectorSize, raws)) return PolicyError::kMalformedVector;

    uint8_t content[32];
    if (!content_sha256(arena_, kPolicyVectorSize, header.schema_version,
                        reinterpret_cast<const uint8_t*>(kRomBaselineRevisionIdsJson),
                        sizeof(kRomBaselineRevisionIdsJson) - 1, content)) {
      return PolicyError::kMalformedVector;
    }
    if (std::memcmp(content, header.content_sha256, 32) != 0) return PolicyError::kHashMismatch;

    // Generation strictly increases across every commit (stale rejection) —
    // including against anything already validated/committed in either
    // pending slot.
    if (header.generation <= last_committed_generation_) return PolicyError::kStaleGeneration;
    if (boundary_.state == SlotState::kValidated || boundary_.state == SlotState::kCommitted) {
      if (header.generation <= boundary_.policy.generation) return PolicyError::kStaleGeneration;
    }
    if (tactical_.state == SlotState::kValidated || tactical_.state == SlotState::kCommitted) {
      if (header.generation <= tactical_.policy.generation) return PolicyError::kStaleGeneration;
    }

    if (header.valid_from_s >= header.valid_to_s) return PolicyError::kBadValidity;
    // The tactical slot may only hold a vector whose validity ends before the
    // staged/committed boundary assignment begins.
    if (header.slot == PolicySlot::kTactical && boundary_.state != SlotState::kEmpty &&
        boundary_.policy.valid_from_s != 0 && header.valid_to_s > boundary_.policy.valid_from_s) {
      return PolicyError::kBadValidity;
    }

    uint8_t bad_index = 0;
    if (!policy_cross_field_ok(raws, &bad_index)) return PolicyError::kCrossField;

    // Manifest gate: the ROM baseline is the ONLY manifest-independent
    // policy; every pushed vector requires an armed experiment manifest.
    if (!manifest_armed_) return PolicyError::kNoManifest;

    const int target = find_template(header.content_sha256);
    if (header.kind == CommitKind::kIdentityRebind) {
      // Same content, new assignment/generation/validity identity only: all
      // 49 encoded bytes and the content hash must match the ACTIVE manifest
      // member.
      if (target < 0) return PolicyError::kNotInManifest;
      if (std::memcmp(active_->content_sha256, header.content_sha256, 32) != 0) return PolicyError::kRebindMismatch;
      if (std::memcmp(active_->vector_bytes, arena_, kPolicyVectorSize) != 0) return PolicyError::kRebindMismatch;
    } else {
      // Content-changing commit: target must be a manifest template and the
      // directed edge must be permitted for this manifest kind.
      if (target < 0) return PolicyError::kNotInManifest;
      if (manifest_.kind == kManifestKindAa && target != 0) return PolicyError::kEdgeNotPermitted;
      if (std::memcmp(active_->content_sha256, header.content_sha256, 32) == 0) {
        // Same content must be declared as an identity rebind.
        return PolicyError::kRebindMismatch;
      }
      const int from = find_template(active_->content_sha256);
      if (from < 0) {
        // Active content outside the template set (e.g. ROM baseline before
        // first entry): only the baseline template may be entered.
        if (target != 0) return PolicyError::kEdgeNotPermitted;
      } else if (!edge_permitted(manifest_.edge_bitmap, from, target)) {
        return PolicyError::kEdgeNotPermitted;
      }
    }

    if (header.has_activation) {
      if (header.treatment_len == 0) return PolicyError::kActivationMismatch;
      uint8_t activation[32];
      if (!activation_sha256(header.content_sha256, manifest_.experiment_id, header.assignment_id, header.treatment,
                             header.treatment_len, header.generation,
                             static_cast<uint64_t>(header.valid_from_s) * 1000000ULL,
                             static_cast<uint64_t>(header.valid_to_s) * 1000000ULL, activation)) {
        return PolicyError::kActivationMismatch;
      }
      if (std::memcmp(activation, header.activation_sha256, 32) != 0) return PolicyError::kActivationMismatch;
    } else if (header.treatment_len != 0) {
      // Off/shadow emits no activation hash; treatment bytes without one are
      // malformed rather than silently ignored.
      return PolicyError::kBadHeader;
    }

    // Fill the slot policy.
    ControlPolicy policy{};
    for (size_t i = 0; i < kPolicyFieldCount; ++i) {
      policy.values[i] =
          static_cast<float>(static_cast<double>(raws[i]) / static_cast<double>(kPolicyFields[i].scale));
    }
    std::memcpy(policy.vector_bytes, arena_, kPolicyVectorSize);
    policy.schema_version = header.schema_version;
    policy.generation = header.generation;
    std::memcpy(policy.content_sha256, header.content_sha256, 32);
    policy.has_activation = header.has_activation;
    std::memcpy(policy.activation_sha256, header.activation_sha256, 32);
    std::memcpy(policy.assignment_id, header.assignment_id, 16);
    policy.valid_from_s = header.valid_from_s;
    policy.valid_to_s = header.valid_to_s;
    policy.has_manifest_binding = true;
    std::memcpy(policy.manifest_sha256, manifest_.manifest_sha256, 32);
    policy.commit_kind = header.kind;
    *out = policy;
    return PolicyError::kNone;
  }

  static PolicyError parse_manifest(const uint8_t* data, size_t len, ExperimentPolicyManifest* out) {
    if (data == nullptr || len < kManifestMinSize || len > kManifestMaxSize) return PolicyError::kBadManifest;
    if (std::memcmp(data, kManifestMagic, 4) != 0) return PolicyError::kBadManifest;
    size_t offset = 4;
    if (data[offset++] != kManifestSchemaVersion) return PolicyError::kBadManifest;
    ExperimentPolicyManifest manifest{};
    manifest.kind = data[offset++];
    if (manifest.kind != kManifestKindRandomized && manifest.kind != kManifestKindQualification &&
        manifest.kind != kManifestKindAa) {
      return PolicyError::kBadManifest;
    }
    std::memcpy(manifest.experiment_id, data + offset, 16);
    offset += 16;
    manifest.manifest_generation = (static_cast<uint32_t>(data[offset]) << 24) |
                                   (static_cast<uint32_t>(data[offset + 1]) << 16) |
                                   (static_cast<uint32_t>(data[offset + 2]) << 8) | data[offset + 3];
    offset += 4;
    if (data[offset++] != kManifestTemplateCount) return PolicyError::kBadManifest;
    for (int i = 0; i < kManifestTemplateCount; ++i) {
      std::memcpy(manifest.template_ids[i], data + offset, 16);
      offset += 16;
      std::memcpy(manifest.template_content_sha[i], data + offset, 32);
      offset += 32;
    }
    manifest.edge_bitmap = data[offset++];
    if (manifest.edge_bitmap & ~kManifestEdgeBitmapMask) return PolicyError::kBadManifest;

    // Optional result references: 0x00 absent, 0x01 || sha256 present.
    const auto read_ref = [&](bool* present, uint8_t sha[32]) -> bool {
      if (offset >= len) return false;
      const uint8_t tag = data[offset++];
      if (tag == 0x00) {
        *present = false;
        return true;
      }
      if (tag != 0x01 || offset + 32 > len) return false;
      *present = true;
      std::memcpy(sha, data + offset, 32);
      offset += 32;
      return true;
    };
    if (!read_ref(&manifest.has_spec_ref, manifest.spec_sha256)) return PolicyError::kBadManifest;
    if (!read_ref(&manifest.has_qualification_result, manifest.qualification_result_sha256)) {
      return PolicyError::kBadManifest;
    }
    if (!read_ref(&manifest.has_aa_result, manifest.aa_result_sha256)) return PolicyError::kBadManifest;

    if (offset + 8 != len) return PolicyError::kBadManifest;
    manifest.valid_from_s = (static_cast<uint32_t>(data[offset]) << 24) | (static_cast<uint32_t>(data[offset + 1]) << 16) |
                            (static_cast<uint32_t>(data[offset + 2]) << 8) | data[offset + 3];
    offset += 4;
    manifest.valid_to_s = (static_cast<uint32_t>(data[offset]) << 24) | (static_cast<uint32_t>(data[offset + 1]) << 16) |
                          (static_cast<uint32_t>(data[offset + 2]) << 8) | data[offset + 3];
    offset += 4;
    if (manifest.valid_from_s >= manifest.valid_to_s) return PolicyError::kBadManifest;

    // §8.9 kind-specific result-reference gates:
    //   qualification: spec present, qualification+A/A results absent;
    //   aa:            completed qualification result present, A/A absent,
    //                  content-changing edges forbidden (baseline only);
    //   randomized:    both completed results present.
    switch (manifest.kind) {
      case kManifestKindQualification:
        if (!manifest.has_spec_ref || manifest.has_qualification_result || manifest.has_aa_result) {
          return PolicyError::kManifestKindGate;
        }
        break;
      case kManifestKindAa:
        if (!manifest.has_qualification_result || manifest.has_aa_result) return PolicyError::kManifestKindGate;
        if (manifest.edge_bitmap != 0) return PolicyError::kManifestKindGate;
        break;
      case kManifestKindRandomized:
        if (!manifest.has_qualification_result || !manifest.has_aa_result) return PolicyError::kManifestKindGate;
        break;
      default:
        return PolicyError::kBadManifest;
    }

    Sha256 ctx;
    ctx.update(kManifestDomainTag, sizeof(kManifestDomainTag) - 1);
    const uint8_t zero = 0x00;
    ctx.update(&zero, 1);
    ctx.update(data, len);
    ctx.finish(manifest.manifest_sha256);
    std::memcpy(manifest.raw_bytes, data, len);
    manifest.raw_len = static_cast<uint16_t>(len);
    *out = manifest;
    return PolicyError::kNone;
  }

  // ── journal encode/decode ────────────────────────────────────────────────
  static void put_u16(uint8_t* out, size_t& offset, uint16_t value) {
    out[offset++] = static_cast<uint8_t>(value >> 8);
    out[offset++] = static_cast<uint8_t>(value);
  }
  static void put_u32(uint8_t* out, size_t& offset, uint32_t value) {
    out[offset++] = static_cast<uint8_t>(value >> 24);
    out[offset++] = static_cast<uint8_t>(value >> 16);
    out[offset++] = static_cast<uint8_t>(value >> 8);
    out[offset++] = static_cast<uint8_t>(value);
  }
  static uint16_t get_u16(const uint8_t* data, size_t& offset) {
    const uint16_t value = static_cast<uint16_t>((data[offset] << 8) | data[offset + 1]);
    offset += 2;
    return value;
  }
  static uint32_t get_u32(const uint8_t* data, size_t& offset) {
    const uint32_t value = (static_cast<uint32_t>(data[offset]) << 24) | (static_cast<uint32_t>(data[offset + 1]) << 16) |
                           (static_cast<uint32_t>(data[offset + 2]) << 8) | data[offset + 3];
    offset += 4;
    return value;
  }

  static constexpr size_t kPolicyBlockSize = 1 + 4 + 1 + 32 + 16 + 4 + 4 + 1 + 32 + kPolicyVectorSize;

  static void encode_policy_block(uint8_t* out, size_t& offset, const ControlPolicy& policy) {
    out[offset++] = static_cast<uint8_t>(policy.commit_kind);
    put_u32(out, offset, policy.generation);
    out[offset++] = policy.has_activation ? 1 : 0;
    std::memcpy(out + offset, policy.activation_sha256, 32);
    offset += 32;
    std::memcpy(out + offset, policy.assignment_id, 16);
    offset += 16;
    put_u32(out, offset, policy.valid_from_s);
    put_u32(out, offset, policy.valid_to_s);
    out[offset++] = policy.has_manifest_binding ? 1 : 0;
    std::memcpy(out + offset, policy.manifest_sha256, 32);
    offset += 32;
    std::memcpy(out + offset, policy.vector_bytes, kPolicyVectorSize);
    offset += kPolicyVectorSize;
  }

  static bool decode_policy_block(const uint8_t* data, size_t len, size_t& offset, ControlPolicy* out) {
    if (offset + kPolicyBlockSize > len) return false;
    ControlPolicy policy{};
    const uint8_t kind = data[offset++];
    if (kind > 2) return false;
    policy.commit_kind = static_cast<CommitKind>(kind);
    policy.generation = get_u32(data, offset);
    const uint8_t has_activation = data[offset++];
    if (has_activation > 1) return false;
    policy.has_activation = has_activation == 1;
    std::memcpy(policy.activation_sha256, data + offset, 32);
    offset += 32;
    std::memcpy(policy.assignment_id, data + offset, 16);
    offset += 16;
    policy.valid_from_s = get_u32(data, offset);
    policy.valid_to_s = get_u32(data, offset);
    const uint8_t has_manifest = data[offset++];
    if (has_manifest > 1) return false;
    policy.has_manifest_binding = has_manifest == 1;
    std::memcpy(policy.manifest_sha256, data + offset, 32);
    offset += 32;
    std::memcpy(policy.vector_bytes, data + offset, kPolicyVectorSize);
    offset += kPolicyVectorSize;

    int64_t raws[kPolicyFieldCount];
    if (!decode_policy_vector(policy.vector_bytes, kPolicyVectorSize, raws)) return false;
    for (size_t i = 0; i < kPolicyFieldCount; ++i) {
      policy.values[i] =
          static_cast<float>(static_cast<double>(raws[i]) / static_cast<double>(kPolicyFields[i].scale));
    }
    policy.schema_version = policy.vector_bytes[4];
    if (!content_sha256(policy.vector_bytes, kPolicyVectorSize, policy.schema_version,
                        reinterpret_cast<const uint8_t*>(kRomBaselineRevisionIdsJson),
                        sizeof(kRomBaselineRevisionIdsJson) - 1, policy.content_sha256)) {
      return false;
    }
    *out = policy;
    return true;
  }

  size_t encode_record(uint8_t* out) {
    // Header written after the payload (needs payload_len), but reserve room.
    size_t offset = kJournalHeaderSize;
    uint8_t flags = 0;
    if (active_ != &kRomBaselinePolicy) flags |= 0x01;
    if (manifest_armed_) flags |= 0x02;
    if (water_budget_consumed_) flags |= 0x04;
    if (boundary_.state == SlotState::kValidated || boundary_.state == SlotState::kCommitted) flags |= 0x08;
    if (tactical_.state == SlotState::kValidated || tactical_.state == SlotState::kCommitted) flags |= 0x10;
    out[offset++] = flags;
    put_u32(out, offset, last_committed_generation_);
    put_u32(out, offset, water_day_stamp_);
    put_u32(out, offset, water_high_centigal_);
    if (flags & 0x01) encode_policy_block(out, offset, *active_);
    if (flags & 0x08) {
      out[offset++] = static_cast<uint8_t>(boundary_.state);
      put_u32(out, offset, boundary_effective_at_);
      encode_policy_block(out, offset, boundary_.policy);
    }
    if (flags & 0x10) {
      out[offset++] = static_cast<uint8_t>(tactical_.state);
      put_u32(out, offset, tactical_effective_at_);
      encode_policy_block(out, offset, tactical_.policy);
    }
    if (flags & 0x02) {
      put_u16(out, offset, manifest_.raw_len);
      std::memcpy(out + offset, manifest_.raw_bytes, manifest_.raw_len);
      offset += manifest_.raw_len;
    }
    const size_t payload_len = offset - kJournalHeaderSize;
    if (offset + 32 + 4 > kJournalRecordCap) return 0;

    size_t head = 0;
    std::memcpy(out + head, kJournalMagic, 4);
    head += 4;
    out[head++] = kJournalSchemaVersion;
    put_u32(out, head, journal_seq_ + 1);
    put_u32(out, head, last_committed_generation_);
    put_u16(out, head, static_cast<uint16_t>(payload_len));

    uint8_t sha[32];
    Sha256::hash(out, offset, sha);
    std::memcpy(out + offset, sha, 32);
    offset += 32;
    const uint32_t crc = policy_crc32(out, offset);
    put_u32(out, offset, crc);
    return offset;
  }

  static bool record_valid(const uint8_t* record, size_t len, size_t* payload_len_out, uint32_t* seq_out) {
    if (len < kJournalHeaderSize + 32 + 4 || len > kJournalRecordCap) return false;
    if (std::memcmp(record, kJournalMagic, 4) != 0) return false;
    if (record[4] != kJournalSchemaVersion) return false;
    size_t offset = 5;
    const uint32_t seq = get_u32(record, offset);
    offset += 4;  // last_committed_generation (informational at record level)
    const uint16_t payload_len = get_u16(record, offset);
    if (kJournalHeaderSize + payload_len + 32 + 4 != len) return false;
    const size_t hashed = kJournalHeaderSize + payload_len;
    uint8_t sha[32];
    Sha256::hash(record, hashed, sha);
    if (std::memcmp(sha, record + hashed, 32) != 0) return false;
    size_t crc_offset = hashed + 32;
    const uint32_t crc = policy_crc32(record, crc_offset);
    const uint32_t stored = (static_cast<uint32_t>(record[crc_offset]) << 24) |
                            (static_cast<uint32_t>(record[crc_offset + 1]) << 16) |
                            (static_cast<uint32_t>(record[crc_offset + 2]) << 8) | record[crc_offset + 3];
    if (crc != stored) return false;
    *payload_len_out = payload_len;
    *seq_out = seq;
    return true;
  }

  // On success the winning record sits in journal_buf_b_.
  bool journal_load(size_t* payload_len_out, uint32_t* seq_out) {
    bool found = false;
    uint32_t best_seq = 0;
    for (uint8_t copy = 0; copy < 2; ++copy) {
      size_t len = 0;
      if (!storage_->read_copy(copy, journal_buf_a_, sizeof(journal_buf_a_), &len)) continue;
      size_t payload_len = 0;
      uint32_t seq = 0;
      if (!record_valid(journal_buf_a_, len, &payload_len, &seq)) continue;
      if (!found || seq > best_seq) {
        std::memcpy(journal_buf_b_, journal_buf_a_, len);
        *payload_len_out = payload_len;
        best_seq = seq;
        found = true;
      }
    }
    if (found) *seq_out = best_seq;
    return found;
  }

  bool decode_payload(const uint8_t* payload, size_t len, PersistedState* out) {
    size_t offset = 0;
    if (len < 13) return false;
    const uint8_t flags = payload[offset++];
    if (flags & ~0x1F) return false;
    out->last_committed_generation = get_u32(payload, offset);
    out->water_day_stamp = get_u32(payload, offset);
    out->water_high_centigal = get_u32(payload, offset);
    out->water_budget_consumed = (flags & 0x04) != 0;
    if (flags & 0x01) {
      if (!decode_policy_block(payload, len, offset, &out->active)) return false;
      out->has_active = true;
    }
    if (flags & 0x08) {
      if (offset + 5 > len) return false;
      const uint8_t state = payload[offset++];
      if (state != 2 && state != 3) return false;
      out->boundary_state = static_cast<SlotState>(state);
      out->boundary_effective_at = get_u32(payload, offset);
      if (!decode_policy_block(payload, len, offset, &out->boundary)) return false;
      out->has_boundary = true;
    }
    if (flags & 0x10) {
      if (offset + 5 > len) return false;
      const uint8_t state = payload[offset++];
      if (state != 2 && state != 3) return false;
      out->tactical_state = static_cast<SlotState>(state);
      out->tactical_effective_at = get_u32(payload, offset);
      if (!decode_policy_block(payload, len, offset, &out->tactical)) return false;
      out->has_tactical = true;
    }
    if (flags & 0x02) {
      if (offset + 2 > len) return false;
      const uint16_t manifest_len = get_u16(payload, offset);
      if (manifest_len < kManifestMinSize || manifest_len > kManifestMaxSize || offset + manifest_len > len) return false;
      std::memcpy(out->manifest_bytes, payload + offset, manifest_len);
      out->manifest_len = manifest_len;
      offset += manifest_len;
      out->has_manifest = true;
    }
    return offset == len;
  }

  // ── state ────────────────────────────────────────────────────────────────
  const ControlPolicy* active_ = &kRomBaselinePolicy;
  ControlPolicy active_storage_{};
  SlotRec boundary_{};
  SlotRec tactical_{};
  uint32_t boundary_effective_at_ = 0;
  uint32_t tactical_effective_at_ = 0;
  Transfer transfer_{};
  uint8_t arena_[kStagingArenaSize] = {};
  bool manifest_armed_ = false;
  ExperimentPolicyManifest manifest_{};
  uint32_t last_committed_generation_ = 0;
  PolicyError last_error_ = PolicyError::kNone;
  PolicyJournalStorage* storage_ = nullptr;
  uint32_t journal_seq_ = 0;
  // Journal scratch lives in .bss, not on the (small) loop-task stack.
  uint8_t journal_buf_a_[kJournalRecordCap] = {};
  uint8_t journal_buf_b_[kJournalRecordCap] = {};
  bool water_budget_consumed_ = false;
  uint32_t water_day_stamp_ = 0;
  uint32_t water_high_centigal_ = 0;
};

inline const char* policy_error_name(PolicyError error) {
  switch (error) {
    case PolicyError::kNone: return "ok";
    case PolicyError::kBusy: return "busy";
    case PolicyError::kSlotOccupied: return "slot_occupied";
    case PolicyError::kBadHeader: return "bad_header";
    case PolicyError::kBadChunk: return "bad_chunk";
    case PolicyError::kIncomplete: return "incomplete";
    case PolicyError::kMalformedVector: return "malformed_vector";
    case PolicyError::kHashMismatch: return "hash_mismatch";
    case PolicyError::kStaleGeneration: return "stale_generation";
    case PolicyError::kBadValidity: return "bad_validity";
    case PolicyError::kCrossField: return "cross_field";
    case PolicyError::kNoManifest: return "no_manifest";
    case PolicyError::kNotInManifest: return "not_in_manifest";
    case PolicyError::kEdgeNotPermitted: return "edge_not_permitted";
    case PolicyError::kRebindMismatch: return "rebind_mismatch";
    case PolicyError::kActivationMismatch: return "activation_mismatch";
    case PolicyError::kBadManifest: return "bad_manifest";
    case PolicyError::kManifestKindGate: return "manifest_kind_gate";
    case PolicyError::kNotValidated: return "not_validated";
    case PolicyError::kNotStaging: return "not_staging";
    case PolicyError::kClockInvalid: return "clock_invalid";
  }
  return "unknown";
}

// Singleton for ESPHome lambdas (function-local static: no global ctor order
// issues, zero heap).
inline PolicyEngine& policy_engine() {
  static PolicyEngine engine;
  return engine;
}

// Hex decode helper for the native API string transport (cheapest heap-safe
// encoding: chunks arrive as hex strings ≤ 2 × kMaxStageChunk chars and are
// decoded straight into the caller's fixed buffer).
inline bool policy_hex_decode(const char* hex, size_t hex_len, uint8_t* out, size_t cap, size_t* out_len) {
  if (hex == nullptr || (hex_len % 2) != 0 || hex_len / 2 > cap) return false;
  const auto nibble = [](char c) -> int {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
  };
  for (size_t i = 0; i < hex_len / 2; ++i) {
    const int hi = nibble(hex[2 * i]);
    const int lo = nibble(hex[2 * i + 1]);
    if (hi < 0 || lo < 0) return false;
    out[i] = static_cast<uint8_t>((hi << 4) | lo);
  }
  *out_len = hex_len / 2;
  return true;
}

}  // namespace verdify_policy
