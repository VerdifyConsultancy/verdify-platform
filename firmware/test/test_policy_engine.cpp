// test_policy_engine.cpp — host tests for the firmware atomic policy engine
// (Lane E, #586; audit §8.9): slot/staging state machine, manifest gating,
// identity rebinds, two-copy journal with power-loss/corruption fixtures,
// conservative reboot semantics, and ROM-baseline identity against the
// Python-computed goldens.
//
// Build + run (same pattern as test_policy_vector; from firmware/):
//   g++ -std=c++17 -I lib -o test/test_policy_engine test/test_policy_engine.cpp
//   ./test/test_policy_engine

#include <cstdint>
#include <cstdio>
#include <cstring>

#include "policy_sha256.h"
#include "policy_vector.h"

#include "policy_vector_goldens_generated.inc"

using namespace verdify_policy;

static int checks_run = 0;
static int checks_failed = 0;

#define CHECK(cond, label)                                     \
  do {                                                         \
    ++checks_run;                                              \
    if (!(cond)) {                                             \
      ++checks_failed;                                         \
      std::printf("FAIL: %s (line %d)\n", (label), __LINE__);  \
    }                                                          \
  } while (0)

// ── Fixtures ────────────────────────────────────────────────────────────────

// Golden indexes: 0 registry_defaults (== ROM baseline == template BASELINE),
// 2 wire_max_bounds (template AGGRESSIVE), 3 mixed_realistic (template
// MODERATE). wire_min_bounds (1) violates the cross-field rules on purpose.
static const size_t kVecBaseline = 0;
static const size_t kVecAggressive = 2;
static const size_t kVecModerate = 3;

static const uint32_t kFrom = 1755000000;  // 2026-08-12ish
static const uint32_t kTo = 1755086400;
static const uint32_t kDay1 = 20260812;
static const uint32_t kDay2 = 20260813;

static void put_u32(uint8_t* out, size_t& o, uint32_t v) {
  out[o++] = static_cast<uint8_t>(v >> 24);
  out[o++] = static_cast<uint8_t>(v >> 16);
  out[o++] = static_cast<uint8_t>(v >> 8);
  out[o++] = static_cast<uint8_t>(v);
}

static const uint8_t* template_sha(int t) {
  return kGoldenContentSha[t == 0 ? kVecBaseline : (t == 1 ? kVecModerate : kVecAggressive)];
}

struct ManifestSpec {
  uint8_t kind = kManifestKindQualification;
  uint32_t generation = 1;
  uint8_t edge_bitmap = 0x3F;
  bool spec_ref = true;
  bool qual_ref = false;
  bool aa_ref = false;
  uint32_t valid_from = kFrom;
  uint32_t valid_to = kTo + 30 * 86400;
};

static size_t build_manifest(uint8_t* out, const ManifestSpec& spec) {
  size_t o = 0;
  std::memcpy(out, kManifestMagic, 4);
  o = 4;
  out[o++] = kManifestSchemaVersion;
  out[o++] = spec.kind;
  for (int i = 0; i < 16; ++i) out[o++] = static_cast<uint8_t>(0xE0 + i);  // experiment uuid
  put_u32(out, o, spec.generation);
  out[o++] = kManifestTemplateCount;
  for (int t = 0; t < kManifestTemplateCount; ++t) {
    for (int i = 0; i < 16; ++i) out[o++] = static_cast<uint8_t>(0x10 * (t + 1) + i);  // template uuid
    std::memcpy(out + o, template_sha(t), 32);
    o += 32;
  }
  out[o++] = spec.edge_bitmap;
  const auto ref = [&](bool present, uint8_t fill) {
    if (!present) {
      out[o++] = 0x00;
      return;
    }
    out[o++] = 0x01;
    for (int i = 0; i < 32; ++i) out[o++] = fill;
  };
  ref(spec.spec_ref, 0xA1);
  ref(spec.qual_ref, 0xB2);
  ref(spec.aa_ref, 0xC3);
  put_u32(out, o, spec.valid_from);
  put_u32(out, o, spec.valid_to);
  return o;
}

// Stage a full payload in two ordered chunks (each ≤ kMaxStageChunk).
static bool stage_all(PolicyEngine& engine, const uint8_t* buf, size_t len) {
  const size_t first = len / 2;
  if (!engine.stage_chunk(0, buf, first)) return false;
  return engine.stage_chunk(first, buf + first, len - first);
}

static bool arm_manifest(PolicyEngine& engine, const ManifestSpec& spec) {
  uint8_t buf[kManifestMaxSize];
  const size_t len = build_manifest(buf, spec);
  if (!engine.manifest_begin(len)) return false;
  if (!stage_all(engine, buf, len)) return false;
  return engine.manifest_commit();
}

struct HeaderSpec {
  PolicySlot slot = PolicySlot::kBoundary;
  CommitKind kind = CommitKind::kContentChange;
  uint8_t schema = kWireSchemaVersion;
  uint32_t generation = 1;
  const uint8_t* content_sha = nullptr;  // required
  bool has_activation = false;
  const uint8_t* activation_sha = nullptr;  // 32B when has_activation
  uint32_t valid_from = kFrom;
  uint32_t valid_to = kTo;
  uint16_t expected_len = static_cast<uint16_t>(kPolicyVectorSize);
  const uint8_t* treatment = nullptr;
  uint8_t treatment_len = 0;
};

static const uint8_t kAssignmentId[16] = {0x66, 0x66, 0x66, 0x66, 0x77, 0x77, 0x48, 0x88,
                                          0x99, 0x99, 0xaa, 0xaa, 0xaa, 0xaa, 0xaa, 0xaa};
static const uint8_t kExperimentId[16] = {0xE0, 0xE1, 0xE2, 0xE3, 0xE4, 0xE5, 0xE6, 0xE7,
                                          0xE8, 0xE9, 0xEA, 0xEB, 0xEC, 0xED, 0xEE, 0xEF};

static size_t build_header(uint8_t* out, const HeaderSpec& spec) {
  size_t o = 0;
  out[o++] = spec.schema;
  out[o++] = static_cast<uint8_t>(spec.slot);
  out[o++] = static_cast<uint8_t>(spec.kind);
  put_u32(out, o, spec.generation);
  std::memcpy(out + o, spec.content_sha, 32);
  o += 32;
  out[o++] = spec.has_activation ? 1 : 0;
  if (spec.has_activation) {
    std::memcpy(out + o, spec.activation_sha, 32);
  } else {
    std::memset(out + o, 0, 32);
  }
  o += 32;
  std::memcpy(out + o, kAssignmentId, 16);
  o += 16;
  put_u32(out, o, spec.valid_from);
  put_u32(out, o, spec.valid_to);
  out[o++] = static_cast<uint8_t>(spec.expected_len >> 8);
  out[o++] = static_cast<uint8_t>(spec.expected_len);
  out[o++] = spec.treatment_len;
  if (spec.treatment_len > 0) {
    std::memcpy(out + o, spec.treatment, spec.treatment_len);
    o += spec.treatment_len;
  }
  return o;
}

// begin + chunk(s) + validate for a full vector; returns validate result.
static bool push_vector(PolicyEngine& engine, const HeaderSpec& spec, const uint8_t* vector_bytes) {
  uint8_t header[kPolicyBeginHeaderMax];
  const size_t header_len = build_header(header, spec);
  if (!engine.begin_policy(header, header_len)) return false;
  const size_t first = 100;
  if (!engine.stage_chunk(0, vector_bytes, first)) return false;
  if (!engine.stage_chunk(first, vector_bytes + first, kPolicyVectorSize - first)) return false;
  return engine.validate_policy();
}

// Full path: manifest must already be armed. validate + commit.
static bool push_and_commit(PolicyEngine& engine, const HeaderSpec& spec, const uint8_t* vector_bytes,
                            uint32_t effective_at) {
  if (!push_vector(engine, spec, vector_bytes)) return false;
  return engine.commit_policy(spec.slot, effective_at);
}

// ── Fake journal storage with corruption/power-loss injection ───────────────

struct FakeStorage : PolicyJournalStorage {
  uint8_t data[2][kJournalRecordCap] = {};
  size_t len[2] = {0, 0};
  bool present[2] = {false, false};
  int truncate_next_write = -1;  // >=0: next write tears after N bytes

  bool read_copy(uint8_t copy, uint8_t* out, size_t cap, size_t* out_len) override {
    if (copy > 1 || !present[copy] || len[copy] > cap) return false;
    std::memcpy(out, data[copy], len[copy]);
    *out_len = len[copy];
    return true;
  }
  bool write_copy(uint8_t copy, const uint8_t* src, size_t n) override {
    if (copy > 1 || n > kJournalRecordCap) return false;
    if (truncate_next_write >= 0) {
      const size_t torn = static_cast<size_t>(truncate_next_write) < n ? static_cast<size_t>(truncate_next_write) : n;
      std::memcpy(data[copy], src, torn);
      len[copy] = torn;
      present[copy] = true;
      truncate_next_write = -1;
      return false;  // power lost mid-write
    }
    std::memcpy(data[copy], src, n);
    len[copy] = n;
    present[copy] = true;
    return true;
  }
  void corrupt(uint8_t copy, size_t offset) {
    if (present[copy] && offset < len[copy]) data[copy][offset] ^= 0xFF;
  }
};

// ── Tests ───────────────────────────────────────────────────────────────────

static void test_rom_baseline_matches_python_goldens() {
  CHECK(std::memcmp(kRomBaselineContentSha256, kGoldenContentSha[kVecBaseline], 32) == 0,
        "ROM baseline content hash == Python registry_defaults hash");
  CHECK(std::memcmp(kRomBaselineVectorBytes, kGoldenVectorBytes[kVecBaseline], kPolicyVectorSize) == 0,
        "ROM baseline vector bytes == Python registry_defaults bytes");
  CHECK(std::memcmp(kRomBaselinePolicy.content_sha256, kGoldenContentSha[kVecBaseline], 32) == 0,
        "constexpr ROM policy carries the golden content hash");
  CHECK(std::memcmp(kRomBaselinePolicy.vector_bytes, kGoldenVectorBytes[kVecBaseline], kPolicyVectorSize) == 0,
        "constexpr ROM policy carries the golden vector bytes");
  // Spot-check decoded engineering values (registry defaults).
  CHECK(kRomBaselinePolicy.values[kPF_mister_engage_kpa] == 1.6f, "ROM mister_engage_kpa default 1.6");
  CHECK(kRomBaselinePolicy.values[kPF_mister_all_kpa] == 1.9f, "ROM mister_all_kpa default 1.9");
  CHECK(kRomBaselinePolicy.values[kPF_mister_water_budget_gal] == 300.0f, "ROM water budget default 300");
  CHECK(kRomBaselinePolicy.values[kPF_dwell_gate_ms] == 300000.0f, "ROM dwell_gate_ms default 300000");
  CHECK(kRomBaselinePolicy.values[kPF_sw_summer_vent_enabled] == 1.0f, "ROM sw_summer_vent_enabled default true");
  CHECK(kRomBaselinePolicy.generation == 0, "ROM generation 0");
  CHECK(kRomBaselinePolicy.valid_to_s == 0, "ROM never expires");
}

static void test_engine_starts_on_rom_baseline() {
  PolicyEngine engine;
  CHECK(engine.rom_baseline_active(), "fresh engine active == ROM");
  const PolicySnapshot snap = engine.policy_snapshot();
  CHECK(!snap.experiment_active, "no manifest => experiment inactive");
  CHECK(snap.generation == 0, "snapshot generation 0");
  CHECK(snap.values[kPF_fog_escalation_kpa] == 0.4f, "snapshot carries ROM values");
  CHECK(std::memcmp(snap.content_sha8, kGoldenContentSha[kVecBaseline], 8) == 0, "snapshot content sha8");
}

static void test_manifest_kind_result_reference_matrix() {
  struct Case {
    uint8_t kind;
    bool spec, qual, aa;
    uint8_t edges;
    bool expect_ok;
    const char* label;
  };
  const Case cases[] = {
      {kManifestKindQualification, true, false, false, 0x3F, true, "qualification: spec only"},
      {kManifestKindQualification, false, false, false, 0x3F, false, "qualification: missing spec"},
      {kManifestKindQualification, true, true, false, 0x3F, false, "qualification: qual result present"},
      {kManifestKindQualification, true, false, true, 0x3F, false, "qualification: aa result present"},
      {kManifestKindAa, true, true, false, 0x00, true, "aa: qual result, no aa, no edges"},
      {kManifestKindAa, true, false, false, 0x00, false, "aa: missing qual result"},
      {kManifestKindAa, true, true, true, 0x00, false, "aa: aa result present"},
      {kManifestKindAa, true, true, false, 0x01, false, "aa: content edges forbidden"},
      {kManifestKindRandomized, true, true, true, 0x3F, true, "randomized: both results"},
      {kManifestKindRandomized, true, false, true, 0x3F, false, "randomized: missing qual result"},
      {kManifestKindRandomized, true, true, false, 0x3F, false, "randomized: missing aa result"},
  };
  for (const Case& c : cases) {
    PolicyEngine engine;
    ManifestSpec spec;
    spec.kind = c.kind;
    spec.spec_ref = c.spec;
    spec.qual_ref = c.qual;
    spec.aa_ref = c.aa;
    spec.edge_bitmap = c.edges;
    const bool got = arm_manifest(engine, spec);
    CHECK(got == c.expect_ok, c.label);
    if (!c.expect_ok) {
      CHECK(engine.last_error() == PolicyError::kManifestKindGate || engine.last_error() == PolicyError::kBadManifest,
            "kind-gate error code");
      CHECK(!engine.manifest_armed(), "gate failure leaves manifest disarmed");
    } else {
      CHECK(engine.manifest_armed(), "manifest armed");
      CHECK(engine.armed_manifest().raw_len > 0, "raw bytes retained");
    }
  }
}

static void test_manifest_malformed_rejects() {
  ManifestSpec spec;
  uint8_t buf[kManifestMaxSize];
  const size_t len = build_manifest(buf, spec);

  {  // bad magic
    PolicyEngine engine;
    uint8_t bad[kManifestMaxSize];
    std::memcpy(bad, buf, len);
    bad[0] = 'X';
    CHECK(engine.manifest_begin(len), "begin ok");
    CHECK(stage_all(engine, bad, len), "chunk ok");
    CHECK(!engine.manifest_commit(), "bad magic rejected");
    CHECK(engine.last_error() == PolicyError::kBadManifest, "bad magic error");
  }
  {  // bad schema
    PolicyEngine engine;
    uint8_t bad[kManifestMaxSize];
    std::memcpy(bad, buf, len);
    bad[4] = kManifestSchemaVersion + 1;
    engine.manifest_begin(len);
    stage_all(engine, bad, len);
    CHECK(!engine.manifest_commit(), "bad manifest schema rejected");
  }
  {  // validity inverted
    PolicyEngine engine;
    ManifestSpec inverted = spec;
    inverted.valid_from = kTo;
    inverted.valid_to = kFrom;
    CHECK(!arm_manifest(engine, inverted), "inverted validity rejected");
  }
  {  // truncated staging (missing final chunk)
    PolicyEngine engine;
    engine.manifest_begin(len);
    engine.stage_chunk(0, buf, len - 10);
    CHECK(!engine.manifest_commit(), "incomplete manifest rejected");
    CHECK(engine.last_error() == PolicyError::kIncomplete, "incomplete error");
  }
  {  // stale manifest generation on re-arm
    PolicyEngine engine;
    CHECK(arm_manifest(engine, spec), "first arm ok");
    ManifestSpec stale = spec;
    stale.generation = spec.generation;  // not increased
    CHECK(!arm_manifest(engine, stale), "stale manifest generation rejected");
    CHECK(engine.last_error() == PolicyError::kStaleGeneration, "stale manifest error");
    ManifestSpec newer = spec;
    newer.generation = spec.generation + 1;
    CHECK(arm_manifest(engine, newer), "higher manifest generation re-arms");
  }
}

static void test_staging_state_machine_rejects() {
  ManifestSpec mspec;
  HeaderSpec base;
  base.content_sha = kGoldenContentSha[kVecModerate];

  {  // chunk/validate without begin
    PolicyEngine engine;
    uint8_t byte = 0;
    CHECK(!engine.stage_chunk(0, &byte, 1), "chunk without begin rejected");
    CHECK(engine.last_error() == PolicyError::kNotStaging, "not staging error");
    CHECK(!engine.validate_policy(), "validate without begin rejected");
  }
  {  // begin while busy + slot occupied + explicit abort requirement
    PolicyEngine engine;
    CHECK(arm_manifest(engine, mspec), "arm");
    uint8_t header[kPolicyBeginHeaderMax];
    const size_t hlen = build_header(header, base);
    CHECK(engine.begin_policy(header, hlen), "begin ok");
    CHECK(!engine.begin_policy(header, hlen), "second begin while staging rejected");
    CHECK(engine.last_error() == PolicyError::kBusy, "busy error");
    // Finish the transfer; slot becomes validated → a new begin into the same
    // slot must fail until an explicit abort.
    CHECK(engine.stage_chunk(0, kGoldenVectorBytes[kVecModerate], kPolicyVectorSize), "single-chunk stage");
    CHECK(engine.validate_policy(), "validate ok");
    HeaderSpec next = base;
    next.generation = 2;
    uint8_t header2[kPolicyBeginHeaderMax];
    const size_t hlen2 = build_header(header2, next);
    CHECK(!engine.begin_policy(header2, hlen2), "occupied slot requires explicit abort");
    CHECK(engine.last_error() == PolicyError::kSlotOccupied, "slot occupied error");
    CHECK(engine.abort_policy(PolicySlot::kBoundary), "abort idempotent ok");
    CHECK(engine.begin_policy(header2, hlen2), "begin after abort ok");
    CHECK(engine.abort_policy(PolicySlot::kBoundary), "abort during staging ok");
    CHECK(engine.abort_policy(PolicySlot::kBoundary), "double abort idempotent");
  }
  {  // duplicate / gap / overlap / oversized chunks
    PolicyEngine engine;
    arm_manifest(engine, mspec);
    uint8_t header[kPolicyBeginHeaderMax];
    const size_t hlen = build_header(header, base);
    engine.begin_policy(header, hlen);
    const uint8_t* vec = kGoldenVectorBytes[kVecModerate];
    CHECK(engine.stage_chunk(0, vec, 64), "first chunk");
    CHECK(!engine.stage_chunk(0, vec, 64), "duplicate chunk rejected");
    CHECK(engine.last_error() == PolicyError::kBadChunk, "duplicate error");
    CHECK(!engine.stage_chunk(128, vec + 128, 32), "gap chunk rejected");
    CHECK(!engine.stage_chunk(64, vec + 64, kPolicyVectorSize), "overrun chunk rejected");
    uint8_t big[kMaxStageChunk + 1] = {};
    CHECK(!engine.stage_chunk(64, big, sizeof(big)), "oversized chunk rejected");
    CHECK(!engine.validate_policy(), "incomplete validate rejected");
    CHECK(engine.last_error() == PolicyError::kIncomplete, "incomplete error");
    // A failed validate tears the transfer down: slot is empty again.
    CHECK(engine.boundary_state() == SlotState::kEmpty, "failed validate clears slot");
  }
  {  // bad headers
    PolicyEngine engine;
    arm_manifest(engine, mspec);
    uint8_t header[kPolicyBeginHeaderMax];
    HeaderSpec bad = base;
    bad.schema = kWireSchemaVersion + 1;
    CHECK(!engine.begin_policy(header, build_header(header, bad)), "wrong schema rejected");
    CHECK(engine.last_error() == PolicyError::kBadHeader, "schema header error");
    bad = base;
    bad.expected_len = 42;
    CHECK(!engine.begin_policy(header, build_header(header, bad)), "wrong expected_len rejected");
    bad = base;
    bad.generation = 0;
    CHECK(!engine.begin_policy(header, build_header(header, bad)), "generation 0 rejected");
    CHECK(!engine.begin_policy(header, 10), "truncated header rejected");
  }
}

static void test_validate_reject_matrix() {
  ManifestSpec mspec;
  HeaderSpec good;
  good.content_sha = kGoldenContentSha[kVecModerate];

  {  // hash mismatch (declared content hash != recomputed)
    PolicyEngine engine;
    arm_manifest(engine, mspec);
    HeaderSpec spec = good;
    spec.content_sha = kGoldenContentSha[kVecAggressive];  // wrong declaration
    CHECK(!push_vector(engine, spec, kGoldenVectorBytes[kVecModerate]), "hash mismatch rejected");
    CHECK(engine.last_error() == PolicyError::kHashMismatch, "hash mismatch error");
  }
  {  // malformed vector (out-of-bounds raw)
    PolicyEngine engine;
    arm_manifest(engine, mspec);
    uint8_t bad[kPolicyVectorSize];
    std::memcpy(bad, kGoldenVectorBytes[kVecModerate], kPolicyVectorSize);
    bad[kPolicyVectorHeaderSize + 2] = 21;  // band_track_fraction raw > max 20
    CHECK(!push_vector(engine, good, bad), "out-of-bounds vector rejected");
    CHECK(engine.last_error() == PolicyError::kMalformedVector, "malformed error");
  }
  {  // cross-field violation: engage > all kpa, per-field in bounds
    PolicyEngine engine;
    arm_manifest(engine, mspec);
    int64_t raws[kPolicyFieldCount];
    CHECK(decode_policy_vector(kGoldenVectorBytes[kVecModerate], kPolicyVectorSize, raws), "decode base");
    raws[kPF_mister_engage_kpa] = 45;  // 2.25 kPa
    raws[kPF_mister_all_kpa] = 38;     // 1.9 kPa < engage
    uint8_t vec[kPolicyVectorSize];
    size_t vec_len = 0;
    CHECK(encode_policy_vector(raws, vec, sizeof(vec), &vec_len), "encode cross-field vector");
    uint8_t content[32];
    CHECK(content_sha256(vec, vec_len, kWireSchemaVersion, reinterpret_cast<const uint8_t*>(kRomBaselineRevisionIdsJson),
                         sizeof(kRomBaselineRevisionIdsJson) - 1, content),
          "content of cross-field vector");
    HeaderSpec spec = good;
    spec.content_sha = content;
    CHECK(!push_vector(engine, spec, vec), "cross-field violation rejected");
    CHECK(engine.last_error() == PolicyError::kCrossField, "cross-field error");
    uint8_t bad_index = 0xFF;
    CHECK(!policy_cross_field_ok(raws, &bad_index), "cross-field helper rejects");
    CHECK(bad_index == kPF_mister_engage_kpa, "cross-field helper names the field");
    // wire_min_bounds golden violates enthalpy_open < enthalpy_close.
    int64_t min_raws[kPolicyFieldCount];
    CHECK(decode_policy_vector(kGoldenVectorBytes[1], kPolicyVectorSize, min_raws), "decode wire_min");
    CHECK(!policy_cross_field_ok(min_raws, &bad_index), "wire_min violates enthalpy ordering");
    CHECK(bad_index == kPF_enthalpy_open, "enthalpy named");
  }
  {  // validity inverted + tactical crossing the staged boundary
    PolicyEngine engine;
    arm_manifest(engine, mspec);
    HeaderSpec spec = good;
    spec.valid_from = kTo;
    spec.valid_to = kFrom;
    CHECK(!push_vector(engine, spec, kGoldenVectorBytes[kVecModerate]), "inverted validity rejected");
    CHECK(engine.last_error() == PolicyError::kBadValidity, "validity error");

    // Stage + commit a boundary vector for [kTo, kTo+day], then try a
    // tactical vector whose validity crosses that boundary.
    HeaderSpec boundary = good;
    boundary.generation = 1;
    boundary.valid_from = kTo;
    boundary.valid_to = kTo + 86400;
    CHECK(push_and_commit(engine, boundary, kGoldenVectorBytes[kVecModerate], kTo), "boundary staged");
    HeaderSpec tactical = good;
    tactical.slot = PolicySlot::kTactical;
    tactical.kind = CommitKind::kContentChange;
    tactical.content_sha = kGoldenContentSha[kVecAggressive];
    tactical.generation = 2;
    tactical.valid_from = kFrom;
    tactical.valid_to = kTo + 3600;  // ends AFTER the boundary begins
    CHECK(!push_vector(engine, tactical, kGoldenVectorBytes[kVecAggressive]), "tactical crossing boundary rejected");
    CHECK(engine.last_error() == PolicyError::kBadValidity, "tactical validity error");
    tactical.valid_to = kTo;  // ends exactly at the boundary
    CHECK(push_vector(engine, tactical, kGoldenVectorBytes[kVecAggressive]), "tactical ending at boundary ok");
  }
  {  // stale generation: applied, pending, and equal generations
    PolicyEngine engine;
    arm_manifest(engine, mspec);
    HeaderSpec first = good;
    first.generation = 5;
    CHECK(push_and_commit(engine, first, kGoldenVectorBytes[kVecModerate], kFrom + 10), "gen5 commit");
    engine.on_tick(kFrom + 10, true, kDay1);
    CHECK(engine.last_committed_generation() == 5, "gen5 applied");
    HeaderSpec stale = good;
    stale.content_sha = kGoldenContentSha[kVecAggressive];
    stale.generation = 5;
    CHECK(!push_vector(engine, stale, kGoldenVectorBytes[kVecAggressive]), "equal generation rejected");
    CHECK(engine.last_error() == PolicyError::kStaleGeneration, "stale error");
    stale.generation = 4;
    CHECK(!push_vector(engine, stale, kGoldenVectorBytes[kVecAggressive]), "lower generation rejected");
    stale.generation = 6;
    CHECK(push_vector(engine, stale, kGoldenVectorBytes[kVecAggressive]), "higher generation accepted");
    CHECK(engine.abort_policy(PolicySlot::kBoundary), "cleanup");
  }
  {  // no manifest armed → every pushed vector is rejected
    PolicyEngine engine;
    CHECK(!push_vector(engine, good, kGoldenVectorBytes[kVecModerate]), "no manifest rejected");
    CHECK(engine.last_error() == PolicyError::kNoManifest, "no manifest error");
  }
  {  // content hash not a manifest member
    PolicyEngine engine;
    arm_manifest(engine, mspec);
    int64_t raws[kPolicyFieldCount];
    decode_policy_vector(kGoldenVectorBytes[kVecModerate], kPolicyVectorSize, raws);
    raws[kPF_temp_hysteresis] += 1;  // in-bounds, but content differs from all templates
    uint8_t vec[kPolicyVectorSize];
    size_t vec_len = 0;
    encode_policy_vector(raws, vec, sizeof(vec), &vec_len);
    uint8_t content[32];
    content_sha256(vec, vec_len, kWireSchemaVersion, reinterpret_cast<const uint8_t*>(kRomBaselineRevisionIdsJson),
                   sizeof(kRomBaselineRevisionIdsJson) - 1, content);
    HeaderSpec spec = good;
    spec.content_sha = content;
    CHECK(!push_vector(engine, spec, vec), "non-template content rejected");
    CHECK(engine.last_error() == PolicyError::kNotInManifest, "not-in-manifest error");
  }
}

static void test_edge_gating_and_identity_rebind() {
  {  // from ROM (content == baseline template) only permitted edges work
    ManifestSpec mspec;
    mspec.edge_bitmap = 0x01;  // only 0→1 (baseline → moderate)
    PolicyEngine engine;
    CHECK(arm_manifest(engine, mspec), "arm restricted manifest");
    // ROM content IS the baseline template content: 0→2 must be rejected.
    HeaderSpec to_aggressive;
    to_aggressive.content_sha = kGoldenContentSha[kVecAggressive];
    CHECK(!push_vector(engine, to_aggressive, kGoldenVectorBytes[kVecAggressive]), "0→2 edge not permitted");
    CHECK(engine.last_error() == PolicyError::kEdgeNotPermitted, "edge error");
    // 0→1 is permitted.
    HeaderSpec to_moderate;
    to_moderate.content_sha = kGoldenContentSha[kVecModerate];
    CHECK(push_and_commit(engine, to_moderate, kGoldenVectorBytes[kVecModerate], kFrom + 5), "0→1 permitted");
    engine.on_tick(kFrom + 5, true, kDay1);
    CHECK(!engine.rom_baseline_active(), "moderate active");
    // 1→0 (bit1) is NOT permitted in this manifest.
    HeaderSpec back;
    back.content_sha = kGoldenContentSha[kVecBaseline];
    back.generation = 2;
    CHECK(!push_vector(engine, back, kGoldenVectorBytes[kVecBaseline]), "1→0 edge not permitted");
    CHECK(engine.last_error() == PolicyError::kEdgeNotPermitted, "edge error 1→0");
  }
  {  // same-content declared as content_change is rejected; identity_rebind works
    ManifestSpec mspec;
    PolicyEngine engine;
    CHECK(arm_manifest(engine, mspec), "arm");
    // ROM active == baseline template content ⇒ content_change to baseline
    // must be declared as identity_rebind instead.
    HeaderSpec same;
    same.content_sha = kGoldenContentSha[kVecBaseline];
    CHECK(!push_vector(engine, same, kGoldenVectorBytes[kVecBaseline]), "same-content content_change rejected");
    CHECK(engine.last_error() == PolicyError::kRebindMismatch, "rebind-required error");
    // Proper identity rebind: same 49 bytes + content hash, new identity.
    HeaderSpec rebind = same;
    rebind.kind = CommitKind::kIdentityRebind;
    rebind.generation = 1;
    CHECK(push_and_commit(engine, rebind, kGoldenVectorBytes[kVecBaseline], kFrom + 2), "identity rebind validates");
    const PolicySnapshot before = engine.on_tick(kFrom + 1, true, kDay1);
    CHECK(before.generation == 0, "not yet applied before effective_at");
    const PolicySnapshot after = engine.on_tick(kFrom + 2, true, kDay1);
    CHECK(after.generation == 1, "rebind applied at boundary");
    CHECK(!engine.rom_baseline_active(), "rebound policy is not the ROM pointer");
    CHECK(std::memcmp(engine.active().content_sha256, kGoldenContentSha[kVecBaseline], 32) == 0,
          "content identity unchanged");
    CHECK(std::memcmp(engine.active().assignment_id, kAssignmentId, 16) == 0, "assignment identity bound");
    // Rebind against DIFFERENT active content must fail.
    HeaderSpec wrong;
    wrong.kind = CommitKind::kIdentityRebind;
    wrong.content_sha = kGoldenContentSha[kVecModerate];
    wrong.generation = 2;
    CHECK(!push_vector(engine, wrong, kGoldenVectorBytes[kVecModerate]), "rebind content != active rejected");
    CHECK(engine.last_error() == PolicyError::kRebindMismatch, "rebind mismatch error");
  }
  {  // aa manifest accepts baseline only
    ManifestSpec mspec;
    mspec.kind = kManifestKindAa;
    mspec.spec_ref = true;
    mspec.qual_ref = true;
    mspec.aa_ref = false;
    mspec.edge_bitmap = 0;
    PolicyEngine engine;
    CHECK(arm_manifest(engine, mspec), "arm aa");
    HeaderSpec moderate;
    moderate.content_sha = kGoldenContentSha[kVecModerate];
    CHECK(!push_vector(engine, moderate, kGoldenVectorBytes[kVecModerate]), "aa rejects non-baseline");
    CHECK(engine.last_error() == PolicyError::kEdgeNotPermitted, "aa edge error");
    HeaderSpec rebind;
    rebind.kind = CommitKind::kIdentityRebind;
    rebind.content_sha = kGoldenContentSha[kVecBaseline];
    CHECK(push_vector(engine, rebind, kGoldenVectorBytes[kVecBaseline]), "aa baseline rebind ok");
  }
}

static void test_activation_hash_gate() {
  ManifestSpec mspec;
  const uint8_t aa_lane0[2] = {0x03, 0x00};
  uint8_t activation[32];
  CHECK(activation_sha256(kGoldenContentSha[kVecModerate], kExperimentId, kAssignmentId, aa_lane0, 2, 1,
                          static_cast<uint64_t>(kFrom) * 1000000ULL, static_cast<uint64_t>(kTo) * 1000000ULL,
                          activation),
        "compute expected activation");

  PolicyEngine engine;
  CHECK(arm_manifest(engine, mspec), "arm");
  HeaderSpec spec;
  spec.content_sha = kGoldenContentSha[kVecModerate];
  spec.has_activation = true;
  spec.activation_sha = activation;
  spec.treatment = aa_lane0;
  spec.treatment_len = 2;
  CHECK(push_vector(engine, spec, kGoldenVectorBytes[kVecModerate]), "correct activation accepted");
  engine.abort_policy(PolicySlot::kBoundary);

  uint8_t wrong[32];
  std::memcpy(wrong, activation, 32);
  wrong[0] ^= 0xFF;
  spec.activation_sha = wrong;
  CHECK(!push_vector(engine, spec, kGoldenVectorBytes[kVecModerate]), "wrong activation rejected");
  CHECK(engine.last_error() == PolicyError::kActivationMismatch, "activation error");

  // Activation without treatment bytes and treatment without activation.
  spec.activation_sha = activation;
  spec.treatment_len = 0;
  CHECK(!push_vector(engine, spec, kGoldenVectorBytes[kVecModerate]), "activation without treatment rejected");
  spec.has_activation = false;
  spec.treatment = aa_lane0;
  spec.treatment_len = 2;
  CHECK(!push_vector(engine, spec, kGoldenVectorBytes[kVecModerate]), "treatment without activation rejected");
  CHECK(engine.last_error() == PolicyError::kBadHeader, "shadow emits no activation, no treatment");
}

static void test_commit_boundary_swap_and_precedence() {
  ManifestSpec mspec;
  PolicyEngine engine;
  CHECK(arm_manifest(engine, mspec), "arm");

  // Boundary: moderate content effective at kFrom+100.
  HeaderSpec boundary;
  boundary.content_sha = kGoldenContentSha[kVecModerate];
  boundary.generation = 1;
  boundary.valid_from = kFrom + 100;
  boundary.valid_to = kFrom + 100 + 86400;
  CHECK(push_and_commit(engine, boundary, kGoldenVectorBytes[kVecModerate], kFrom + 100), "boundary committed");
  CHECK(engine.boundary_state() == SlotState::kCommitted, "boundary state committed");

  // Tactical: aggressive content effective at kFrom+10, must end before the
  // boundary's valid_from.
  HeaderSpec tactical;
  tactical.slot = PolicySlot::kTactical;
  tactical.content_sha = kGoldenContentSha[kVecAggressive];
  tactical.generation = 2;
  tactical.valid_from = kFrom + 10;
  tactical.valid_to = kFrom + 100;
  CHECK(push_and_commit(engine, tactical, kGoldenVectorBytes[kVecAggressive], kFrom + 10), "tactical committed");

  // Before either effective time: ROM still active.
  PolicySnapshot snap = engine.on_tick(kFrom + 5, true, kDay1);
  CHECK(engine.rom_baseline_active(), "nothing applied yet");
  CHECK(snap.experiment_active, "manifest armed flag");

  // Tactical applies first.
  snap = engine.on_tick(kFrom + 10, true, kDay1);
  CHECK(snap.generation == 2, "tactical applied");
  CHECK(std::memcmp(engine.active().content_sha256, kGoldenContentSha[kVecAggressive], 32) == 0, "aggressive active");
  CHECK(engine.tactical_state() == SlotState::kEmpty, "tactical slot cleared");

  // At the boundary the due boundary generation wins.
  snap = engine.on_tick(kFrom + 100, true, kDay1);
  CHECK(std::memcmp(engine.active().content_sha256, kGoldenContentSha[kVecModerate], 32) == 0, "boundary won");
  CHECK(engine.boundary_state() == SlotState::kEmpty, "boundary slot cleared");
  CHECK(engine.last_committed_generation() == 2, "generation high-water stays monotone");

  // Expiry → ROM baseline, atomically.
  snap = engine.on_tick(kFrom + 100 + 86400 + 1, true, kDay2);
  CHECK(engine.rom_baseline_active(), "expired → ROM baseline");
  CHECK(snap.generation == 0, "ROM generation after expiry");

  // Invalid clock → ROM baseline (from any state).
  PolicyEngine engine2;
  CHECK(arm_manifest(engine2, mspec), "arm 2");
  HeaderSpec rebind;
  rebind.kind = CommitKind::kIdentityRebind;
  rebind.content_sha = kGoldenContentSha[kVecBaseline];
  CHECK(push_and_commit(engine2, rebind, kGoldenVectorBytes[kVecBaseline], kFrom + 1), "rebind committed");
  engine2.on_tick(kFrom + 1, true, kDay1);
  CHECK(!engine2.rom_baseline_active(), "rebound active");
  engine2.on_tick(kFrom + 2, false, kDay1);
  CHECK(engine2.rom_baseline_active(), "invalid clock → ROM");
}

static void test_boundary_discards_stale_tactical() {
  ManifestSpec mspec;
  PolicyEngine engine;
  CHECK(arm_manifest(engine, mspec), "arm");
  HeaderSpec tactical;
  tactical.slot = PolicySlot::kTactical;
  tactical.content_sha = kGoldenContentSha[kVecAggressive];
  tactical.generation = 1;
  tactical.valid_from = kFrom + 50;
  tactical.valid_to = kFrom + 100;
  CHECK(push_and_commit(engine, tactical, kGoldenVectorBytes[kVecAggressive], kFrom + 50), "tactical committed");
  HeaderSpec boundary;
  boundary.content_sha = kGoldenContentSha[kVecModerate];
  boundary.generation = 2;
  boundary.valid_from = kFrom + 100;
  boundary.valid_to = kFrom + 100 + 86400;
  CHECK(push_and_commit(engine, boundary, kGoldenVectorBytes[kVecModerate], kFrom + 100), "boundary committed");
  // Jump straight past both effective times in one tick: boundary wins, the
  // stale tactical is discarded without ever applying.
  engine.on_tick(kFrom + 200, true, kDay1);
  CHECK(std::memcmp(engine.active().content_sha256, kGoldenContentSha[kVecModerate], 32) == 0,
        "boundary generation wins");
  CHECK(engine.tactical_state() == SlotState::kEmpty, "stale tactical discarded");
}

static void test_journal_roundtrip_and_boot_resume() {
  FakeStorage storage;
  ManifestSpec mspec;
  {
    PolicyEngine engine;
    engine.bind_storage(&storage);
    CHECK(arm_manifest(engine, mspec), "arm");
    HeaderSpec rebind;
    rebind.kind = CommitKind::kIdentityRebind;
    rebind.content_sha = kGoldenContentSha[kVecBaseline];
    rebind.generation = 3;
    CHECK(push_and_commit(engine, rebind, kGoldenVectorBytes[kVecBaseline], kFrom + 1), "commit");
    engine.on_tick(kFrom + 1, true, kDay1);
    CHECK(!engine.rom_baseline_active(), "active");
    engine.note_water_high_water(kDay1, 42.5f);
    CHECK(engine.journal_write(), "explicit journal write");
  }
  {  // reboot same day, valid clock → same-identity resume + water restored
    PolicyEngine engine;
    engine.bind_storage(&storage);
    const ResetEvent event = engine.boot_init(kFrom + 500, true, kDay1);
    CHECK(event.outcome == BootOutcome::kResumedActive, "resumed active");
    CHECK(event.resumed_generation == 3, "resumed generation");
    CHECK(event.manifest_rearmed, "manifest rearmed");
    CHECK(engine.manifest_armed(), "manifest armed after boot");
    CHECK(!event.water_budget_marked_consumed, "same-day journaled high-water lifts the flag");
    CHECK(engine.water_high_water_gal() == 42.5f, "high-water restored");
    CHECK(std::memcmp(engine.active().content_sha256, kGoldenContentSha[kVecBaseline], 32) == 0, "same identity");
    CHECK(engine.last_committed_generation() == 3, "generation restored");
  }
  {  // reboot next local day → conservative flag until day-rollover... which
     // already happened: journal is from yesterday, so budget marked consumed.
    PolicyEngine engine;
    engine.bind_storage(&storage);
    const ResetEvent event = engine.boot_init(kFrom + 90000, true, kDay2);
    CHECK(event.water_budget_marked_consumed, "no same-day mark → consumed");
    CHECK(engine.water_budget_marked_consumed(), "engine flag consumed");
    // Next verified local-day rollover clears it.
    engine.on_tick(kFrom + 90001, true, kDay2);
    CHECK(engine.water_budget_marked_consumed(), "same day: flag stays");
    engine.on_tick(kFrom + 180000, true, kDay2 + 1);
    CHECK(!engine.water_budget_marked_consumed(), "rollover clears the conservative flag");
  }
  {  // reboot with invalid clock → ROM baseline even with a valid journal
    PolicyEngine engine;
    engine.bind_storage(&storage);
    const ResetEvent event = engine.boot_init(0, false, 0);
    CHECK(event.outcome == BootOutcome::kRomClockInvalid, "invalid clock → ROM");
    CHECK(engine.rom_baseline_active(), "ROM active");
    CHECK(event.water_budget_marked_consumed, "conservative water flag");
  }
  {  // reboot after expiry → ROM
    PolicyEngine engine;
    engine.bind_storage(&storage);
    const ResetEvent event = engine.boot_init(kTo + 10, true, kDay2);
    CHECK(event.outcome == BootOutcome::kRomExpired, "expired persisted policy → ROM");
    CHECK(engine.rom_baseline_active(), "ROM active after expiry");
  }
}

static void test_journal_pending_slots_survive_reboot() {
  FakeStorage storage;
  ManifestSpec mspec;
  {
    PolicyEngine engine;
    engine.bind_storage(&storage);
    CHECK(arm_manifest(engine, mspec), "arm");
    HeaderSpec boundary;
    boundary.content_sha = kGoldenContentSha[kVecModerate];
    boundary.generation = 1;
    boundary.valid_from = kFrom + 100;
    boundary.valid_to = kFrom + 100 + 86400;
    CHECK(push_and_commit(engine, boundary, kGoldenVectorBytes[kVecModerate], kFrom + 100), "boundary committed");
  }
  {  // reboot before the boundary: pending commit survives and applies on time
    PolicyEngine engine;
    engine.bind_storage(&storage);
    const ResetEvent event = engine.boot_init(kFrom + 50, true, kDay1);
    CHECK(event.outcome == BootOutcome::kRomNoJournal || event.outcome == BootOutcome::kResumedActive
              ? true
              : event.outcome == BootOutcome::kRomExpired,
          "boot outcome sane");
    CHECK(engine.boundary_state() == SlotState::kCommitted, "pending boundary restored");
    engine.on_tick(kFrom + 100, true, kDay1);
    CHECK(std::memcmp(engine.active().content_sha256, kGoldenContentSha[kVecModerate], 32) == 0,
          "restored boundary applied at its boundary");
  }
}

static void test_journal_corruption_fallback() {
  ManifestSpec mspec;
  const auto seed = [&](FakeStorage& storage) {
    PolicyEngine engine;
    engine.bind_storage(&storage);
    arm_manifest(engine, mspec);
    HeaderSpec rebind;
    rebind.kind = CommitKind::kIdentityRebind;
    rebind.content_sha = kGoldenContentSha[kVecBaseline];
    push_and_commit(engine, rebind, kGoldenVectorBytes[kVecBaseline], kFrom + 1);
    engine.on_tick(kFrom + 1, true, kDay1);
    engine.journal_write();
  };

  {  // one corrupt copy → the other still resumes
    FakeStorage storage;
    seed(storage);
    CHECK(storage.present[0] && storage.present[1], "both copies written");
    storage.corrupt(1, 40);
    PolicyEngine engine;
    engine.bind_storage(&storage);
    const ResetEvent event = engine.boot_init(kFrom + 500, true, kDay1);
    CHECK(event.outcome == BootOutcome::kResumedActive, "one good copy resumes");
  }
  {  // both copies corrupt → ROM fallback
    FakeStorage storage;
    seed(storage);
    storage.corrupt(0, 40);
    storage.corrupt(1, 40);
    PolicyEngine engine;
    engine.bind_storage(&storage);
    const ResetEvent event = engine.boot_init(kFrom + 500, true, kDay1);
    CHECK(event.outcome == BootOutcome::kRomCorruptJournal, "both corrupt → ROM");
    CHECK(engine.rom_baseline_active(), "ROM active");
    CHECK(!engine.manifest_armed(), "no manifest from corrupt journal");
    CHECK(event.water_budget_marked_consumed, "conservative water flag on corruption");
  }
  {  // incompatible journal schema → ROM fallback
    FakeStorage storage;
    seed(storage);
    storage.data[0][4] = kJournalSchemaVersion + 1;
    storage.data[1][4] = kJournalSchemaVersion + 1;
    PolicyEngine engine;
    engine.bind_storage(&storage);
    const ResetEvent event = engine.boot_init(kFrom + 500, true, kDay1);
    CHECK(event.outcome == BootOutcome::kRomCorruptJournal, "incompatible schema → ROM");
  }
  {  // truncated copies → ROM fallback
    FakeStorage storage;
    seed(storage);
    storage.len[0] = 20;
    storage.len[1] = 30;
    PolicyEngine engine;
    engine.bind_storage(&storage);
    const ResetEvent event = engine.boot_init(kFrom + 500, true, kDay1);
    CHECK(event.outcome == BootOutcome::kRomCorruptJournal, "truncated copies → ROM");
  }
  {  // empty storage → ROM (no journal)
    FakeStorage storage;
    PolicyEngine engine;
    engine.bind_storage(&storage);
    const ResetEvent event = engine.boot_init(kFrom + 500, true, kDay1);
    CHECK(event.outcome == BootOutcome::kRomNoJournal, "no journal → ROM");
  }
}

static void test_journal_power_loss_at_every_boundary() {
  // Tear the NEXT journal write at each engine transition; the previous
  // consistent state must survive because the two copies alternate.
  ManifestSpec mspec;

  {  // power loss during the manifest-arm write
    FakeStorage storage;
    PolicyEngine engine;
    engine.bind_storage(&storage);
    storage.truncate_next_write = 30;
    CHECK(arm_manifest(engine, mspec), "arm succeeds in RAM even if journal write tears");
    PolicyEngine rebooted;
    rebooted.bind_storage(&storage);
    const ResetEvent event = rebooted.boot_init(kFrom + 5, true, kDay1);
    CHECK(event.outcome == BootOutcome::kRomCorruptJournal || event.outcome == BootOutcome::kRomNoJournal,
          "torn first write → clean ROM boot");
    CHECK(rebooted.rom_baseline_active(), "ROM after torn arm");
  }
  {  // power loss during the commit write: the armed-manifest record survives
    FakeStorage storage;
    PolicyEngine engine;
    engine.bind_storage(&storage);
    CHECK(arm_manifest(engine, mspec), "arm journaled");
    HeaderSpec rebind;
    rebind.kind = CommitKind::kIdentityRebind;
    rebind.content_sha = kGoldenContentSha[kVecBaseline];
    CHECK(push_vector(engine, rebind, kGoldenVectorBytes[kVecBaseline]), "validated");
    storage.truncate_next_write = 50;
    CHECK(engine.commit_policy(PolicySlot::kBoundary, kFrom + 10), "commit succeeds in RAM");
    PolicyEngine rebooted;
    rebooted.bind_storage(&storage);
    const ResetEvent event = rebooted.boot_init(kFrom + 5, true, kDay1);
    CHECK(rebooted.manifest_armed(), "pre-commit record survives torn commit write");
    CHECK(rebooted.boundary_state() == SlotState::kEmpty, "torn commit not resurrected");
    CHECK(event.outcome == BootOutcome::kRomNoJournal, "ROM baseline with manifest armed");
  }
  {  // power loss during the boundary-apply write: previous commit record survives
    FakeStorage storage;
    PolicyEngine engine;
    engine.bind_storage(&storage);
    CHECK(arm_manifest(engine, mspec), "arm journaled");
    HeaderSpec rebind;
    rebind.kind = CommitKind::kIdentityRebind;
    rebind.content_sha = kGoldenContentSha[kVecBaseline];
    rebind.generation = 2;
    CHECK(push_and_commit(engine, rebind, kGoldenVectorBytes[kVecBaseline], kFrom + 10), "committed + journaled");
    storage.truncate_next_write = 60;
    engine.on_tick(kFrom + 10, true, kDay1);  // apply tears its journal write
    CHECK(!engine.rom_baseline_active(), "apply happened in RAM");
    PolicyEngine rebooted;
    rebooted.bind_storage(&storage);
    rebooted.boot_init(kFrom + 11, true, kDay1);
    // The surviving record is the pre-apply one: pending commit still staged.
    CHECK(rebooted.boundary_state() == SlotState::kCommitted, "pre-apply record survives torn apply write");
    rebooted.on_tick(kFrom + 12, true, kDay1);
    CHECK(!rebooted.rom_baseline_active(), "replayed apply reaches the same active policy");
    CHECK(std::memcmp(rebooted.active().content_sha256, kGoldenContentSha[kVecBaseline], 32) == 0,
          "same content identity after replay");
  }
}

static void test_water_high_water_never_rolls_backward() {
  PolicyEngine engine;
  engine.note_water_high_water(kDay1, 10.0f);
  engine.note_water_high_water(kDay1, 5.0f);
  CHECK(engine.water_high_water_gal() == 10.0f, "high-water keeps the max");
  engine.note_water_high_water(kDay1, 12.25f);
  CHECK(engine.water_high_water_gal() == 12.25f, "high-water advances");
  engine.note_water_high_water(kDay2, 1.0f);
  CHECK(engine.water_high_water_gal() == 1.0f, "day rollover resets the mark");
}

static void test_identity_readback_format() {
  PolicyEngine engine;
  char buf[96];
  engine.identity_readback(buf, sizeof(buf));
  char expected[96];
  std::snprintf(expected, sizeof(expected), "v1|g0|c%02x%02x%02x%02x%02x%02x%02x%02x|a-|s-|ROM",
                kGoldenContentSha[kVecBaseline][0], kGoldenContentSha[kVecBaseline][1],
                kGoldenContentSha[kVecBaseline][2], kGoldenContentSha[kVecBaseline][3],
                kGoldenContentSha[kVecBaseline][4], kGoldenContentSha[kVecBaseline][5],
                kGoldenContentSha[kVecBaseline][6], kGoldenContentSha[kVecBaseline][7]);
  CHECK(std::strcmp(buf, expected) == 0, "ROM identity readback format");
}

static void test_hex_decode() {
  uint8_t out[8];
  size_t out_len = 0;
  CHECK(policy_hex_decode("00ff10Ab", 8, out, sizeof(out), &out_len), "hex decode ok");
  CHECK(out_len == 4 && out[0] == 0x00 && out[1] == 0xFF && out[2] == 0x10 && out[3] == 0xAB, "hex bytes");
  CHECK(!policy_hex_decode("0f0", 3, out, sizeof(out), &out_len), "odd length rejected");
  CHECK(!policy_hex_decode("zz", 2, out, sizeof(out), &out_len), "non-hex rejected");
  CHECK(!policy_hex_decode("0011223344556677aa", 18, out, sizeof(out), &out_len), "overflow rejected");
}

static void test_static_footprint() {
  // Heap-budget guard: the engine (all slots + arena + journal scratch) must
  // stay a small fixed .bss block, and the per-tick snapshot cheap to copy.
  CHECK(sizeof(PolicyEngine) < 8192, "engine < 8 KB static");
  CHECK(sizeof(PolicySnapshot) <= 256, "snapshot <= 256 B by-value");
  CHECK(sizeof(ControlPolicy) <= 640, "ControlPolicy <= 640 B");
}

int main() {
  test_rom_baseline_matches_python_goldens();
  test_engine_starts_on_rom_baseline();
  test_manifest_kind_result_reference_matrix();
  test_manifest_malformed_rejects();
  test_staging_state_machine_rejects();
  test_validate_reject_matrix();
  test_edge_gating_and_identity_rebind();
  test_activation_hash_gate();
  test_commit_boundary_swap_and_precedence();
  test_boundary_discards_stale_tactical();
  test_journal_roundtrip_and_boot_resume();
  test_journal_pending_slots_survive_reboot();
  test_journal_corruption_fallback();
  test_journal_power_loss_at_every_boundary();
  test_water_high_water_never_rolls_backward();
  test_identity_readback_format();
  test_hex_decode();
  test_static_footprint();
  if (checks_failed == 0) {
    std::printf("test_policy_engine: all %d checks passed\n", checks_run);
    return 0;
  }
  std::printf("test_policy_engine: %d/%d checks FAILED\n", checks_failed, checks_run);
  return 1;
}
