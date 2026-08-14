// test_policy_vector.cpp — cross-language golden test for the canonical
// policy-vector wire codec (Lane A, #582; audit §8.9).
//
// Asserts byte- and hash-identical behavior with the Python codec in
// verdify_schemas/policy_vector.py against the shared golden fixtures
// (verdify_schemas/tests/fixtures/policy_vector_goldens.json, embedded at
// generation time as policy_vector_goldens_generated.inc), plus strict
// decoder rejection of malformed vectors and §8.9 treatment octets.
//
// Build + run (same pattern as test_greenhouse_logic; from firmware/):
//   g++ -std=c++17 -I lib -o test/test_policy_vector test/test_policy_vector.cpp
//   ./test/test_policy_vector

#include <cstdint>
#include <cstdio>
#include <cstring>

#include "policy_sha256.h"
#include "policy_vector_generated.h"

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

static bool hex_equal(const uint8_t* got, const uint8_t* want, size_t n) { return std::memcmp(got, want, n) == 0; }

static void test_sha256_self_test() {
  // FIPS 180-4 vectors.
  uint8_t out[32];
  Sha256::hash(reinterpret_cast<const uint8_t*>("abc"), 3, out);
  const uint8_t abc[32] = {0xba, 0x78, 0x16, 0xbf, 0x8f, 0x01, 0xcf, 0xea, 0x41, 0x41, 0x40, 0xde, 0x5d, 0xae, 0x22,
                           0x23, 0xb0, 0x03, 0x61, 0xa3, 0x96, 0x17, 0x7a, 0x9c, 0xb4, 0x10, 0xff, 0x61, 0xf2, 0x00,
                           0x15, 0xad};
  CHECK(hex_equal(out, abc, 32), "sha256(abc)");
  Sha256::hash(nullptr, 0, out);
  const uint8_t empty[32] = {0xe3, 0xb0, 0xc4, 0x42, 0x98, 0xfc, 0x1c, 0x14, 0x9a, 0xfb, 0xf4, 0xc8, 0x99, 0x6f,
                             0xb9, 0x24, 0x27, 0xae, 0x41, 0xe4, 0x64, 0x9b, 0x93, 0x4c, 0xa4, 0x95, 0x99, 0x1b,
                             0x78, 0x52, 0xb8, 0x55};
  CHECK(hex_equal(out, empty, 32), "sha256(empty)");
}

static void test_table_sanity() {
  CHECK(kPolicyFieldCount == 48, "field count is the frozen 48 (wire schema v2)");
  size_t size = kPolicyVectorHeaderSize;
  uint16_t prev_id = 0;
  for (size_t i = 0; i < kPolicyFieldCount; ++i) {
    const PolicyFieldDef& field = kPolicyFields[i];
    CHECK(field.wire_id > prev_id, "wire_ids strictly ascending");
    prev_id = field.wire_id;
    CHECK(field.scale >= 1, "scale positive");
    CHECK(field.min_raw <= field.max_raw, "raw bounds ordered");
    CHECK(wire_kind_width(field.kind) > 0, "kind has width");
    size += 2 + wire_kind_width(field.kind);
  }
  CHECK(size == kPolicyVectorSize, "vector size matches table");
  CHECK(hex_equal(kWireManifestDigest, kGoldenManifestDigest, 32), "manifest digest matches goldens");
}

static void test_goldens() {
  const size_t revision_len = sizeof(kGoldenRevisionIdsJson) - 1;
  for (size_t v = 0; v < kGoldenVectorCount; ++v) {
    uint8_t encoded[kPolicyVectorSize];
    size_t encoded_len = 0;
    CHECK(encode_policy_vector(kGoldenRaws[v], encoded, sizeof(encoded), &encoded_len), kGoldenVectorNames[v]);
    CHECK(encoded_len == kPolicyVectorSize, "encoded length");
    CHECK(hex_equal(encoded, kGoldenVectorBytes[v], kPolicyVectorSize), "encoded bytes match Python");

    int64_t raws[kPolicyFieldCount];
    CHECK(decode_policy_vector(kGoldenVectorBytes[v], kPolicyVectorSize, raws), "decode golden");
    bool raws_equal = true;
    for (size_t i = 0; i < kPolicyFieldCount; ++i) raws_equal = raws_equal && raws[i] == kGoldenRaws[v][i];
    CHECK(raws_equal, "decoded raws match Python");

    uint8_t content[32];
    CHECK(content_sha256(kGoldenVectorBytes[v], kPolicyVectorSize, kWireSchemaVersion,
                         reinterpret_cast<const uint8_t*>(kGoldenRevisionIdsJson), revision_len, content),
          "content hash computes");
    CHECK(hex_equal(content, kGoldenContentSha[v], 32), "content hash matches Python");

    for (size_t t = 0; t < kGoldenTreatmentCount; ++t) {
      uint8_t activation[32];
      CHECK(activation_sha256(content, kGoldenExperimentId, kGoldenAssignmentId, kGoldenTreatmentBytes[t],
                              kGoldenTreatmentLens[t], kGoldenGeneration, kGoldenValidFromUs, kGoldenValidToUs,
                              activation),
            kGoldenTreatmentNames[t]);
      CHECK(hex_equal(activation, kGoldenActivationSha[v][t], 32), "activation hash matches Python");
    }
  }
}

static void test_strict_decode_rejections() {
  uint8_t vec[kPolicyVectorSize];
  int64_t raws[kPolicyFieldCount];

  std::memcpy(vec, kGoldenVectorBytes[0], kPolicyVectorSize);
  vec[0] = 'X';
  CHECK(!decode_policy_vector(vec, kPolicyVectorSize, raws), "bad magic rejected");

  std::memcpy(vec, kGoldenVectorBytes[0], kPolicyVectorSize);
  vec[4] = kWireSchemaVersion + 1;
  CHECK(!decode_policy_vector(vec, kPolicyVectorSize, raws), "bad schema version rejected");

  std::memcpy(vec, kGoldenVectorBytes[0], kPolicyVectorSize);
  vec[5] ^= 0xff;
  CHECK(!decode_policy_vector(vec, kPolicyVectorSize, raws), "manifest digest mismatch rejected");

  std::memcpy(vec, kGoldenVectorBytes[0], kPolicyVectorSize);
  vec[13] = kPolicyFieldCount - 1;
  CHECK(!decode_policy_vector(vec, kPolicyVectorSize, raws), "bad field count rejected");

  CHECK(!decode_policy_vector(kGoldenVectorBytes[0], kPolicyVectorSize - 1, raws), "truncated rejected");
  uint8_t longer[kPolicyVectorSize + 1];
  std::memcpy(longer, kGoldenVectorBytes[0], kPolicyVectorSize);
  longer[kPolicyVectorSize] = 0x00;
  CHECK(!decode_policy_vector(longer, kPolicyVectorSize + 1, raws), "trailing byte rejected");

  // Record 0 is wire_id 1 (band_track_fraction, u8, raw bounds [0, 20]):
  // force raw 21 to prove out-of-bounds raws are rejected.
  std::memcpy(vec, kGoldenVectorBytes[0], kPolicyVectorSize);
  vec[kPolicyVectorHeaderSize + 2] = 21;
  CHECK(!decode_policy_vector(vec, kPolicyVectorSize, raws), "out-of-bounds raw rejected");

  // Swap the first two records: ids arrive out of canonical order.
  std::memcpy(vec, kGoldenVectorBytes[0], kPolicyVectorSize);
  uint8_t record0[3];
  std::memcpy(record0, vec + kPolicyVectorHeaderSize, 3);
  std::memcpy(vec + kPolicyVectorHeaderSize, vec + kPolicyVectorHeaderSize + 3, 3);
  std::memcpy(vec + kPolicyVectorHeaderSize + 3, record0, 3);
  CHECK(!decode_policy_vector(vec, kPolicyVectorSize, raws), "out-of-order wire_id rejected");

  // Unknown wire_id 999 in place of record 0.
  std::memcpy(vec, kGoldenVectorBytes[0], kPolicyVectorSize);
  vec[kPolicyVectorHeaderSize] = static_cast<uint8_t>(999 >> 8);
  vec[kPolicyVectorHeaderSize + 1] = static_cast<uint8_t>(999 & 0xff);
  CHECK(!decode_policy_vector(vec, kPolicyVectorSize, raws), "unknown wire_id rejected");

  // Encoding an out-of-bounds raw must fail too.
  int64_t bad_raws[kPolicyFieldCount];
  std::memcpy(bad_raws, kGoldenRaws[0], sizeof(bad_raws));
  bad_raws[0] = kPolicyFields[0].max_raw + 1;
  uint8_t out[kPolicyVectorSize];
  size_t out_len = 0;
  CHECK(!encode_policy_vector(bad_raws, out, sizeof(out), &out_len), "encode rejects out-of-bounds raw");
}

static void test_treatment_octets() {
  const uint8_t rand_x[] = {0x01, 0x58};
  const uint8_t rand_y[] = {0x01, 0x59};
  const uint8_t aa0[] = {0x03, 0x00};
  const uint8_t aa1[] = {0x03, 0x01};
  CHECK(validate_treatment_bytes(rand_x, 2), "randomized X valid");
  CHECK(validate_treatment_bytes(rand_y, 2), "randomized Y valid");
  CHECK(validate_treatment_bytes(aa0, 2), "aa lane 0 valid");
  CHECK(validate_treatment_bytes(aa1, 2), "aa lane 1 valid");
  CHECK(validate_treatment_bytes(kGoldenTreatmentBytes[1], kGoldenTreatmentLens[1]), "qualification valid");

  CHECK(!validate_treatment_bytes(nullptr, 0), "empty treatment rejected (off/shadow emits no hash)");
  const uint8_t bad_tag[] = {0x04, 0x00};
  CHECK(!validate_treatment_bytes(bad_tag, 2), "unknown tag rejected");
  const uint8_t bad_arm[] = {0x01, 0x5a};
  CHECK(!validate_treatment_bytes(bad_arm, 2), "randomized arm 'Z' rejected");
  const uint8_t bad_lane[] = {0x03, 0x02};
  CHECK(!validate_treatment_bytes(bad_lane, 2), "aa lane 2 rejected");
  const uint8_t short_rand[] = {0x01};
  CHECK(!validate_treatment_bytes(short_rand, 1), "short randomized rejected");

  uint8_t qual[35];
  std::memcpy(qual, kGoldenTreatmentBytes[1], 35);
  qual[1] = 0x05;
  CHECK(!validate_treatment_bytes(qual, 35), "qualification op 0x05 rejected");
  std::memcpy(qual, kGoldenTreatmentBytes[1], 35);
  qual[34] = 0x04;
  CHECK(!validate_treatment_bytes(qual, 35), "qualification regime 0x04 rejected");
  CHECK(!validate_treatment_bytes(kGoldenTreatmentBytes[1], 34), "short qualification rejected");

  // Invalid validity window must fail even with valid treatment octets.
  uint8_t content[32] = {0};
  uint8_t out[32];
  CHECK(!activation_sha256(content, kGoldenExperimentId, kGoldenAssignmentId, rand_x, 2, 1, 10, 10, out),
        "valid_from >= valid_to rejected");
}

int main() {
  test_sha256_self_test();
  test_table_sanity();
  test_goldens();
  test_strict_decode_rejections();
  test_treatment_octets();
  if (checks_failed == 0) {
    std::printf("test_policy_vector: all %d checks passed\n", checks_run);
    return 0;
  }
  std::printf("test_policy_vector: %d/%d checks FAILED\n", checks_failed, checks_run);
  return 1;
}
