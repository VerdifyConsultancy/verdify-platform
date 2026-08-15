// test_policy_recovery.cpp — host proof of the RECOVERY image semantics
// (#586, audit §8.10 step 3). Compiled with -DPOLICY_ENGINE_RECOVERY against
// a journal fixture emitted by the NORMAL build
// (test_policy_engine --emit-recovery-fixture <prefix>), which holds an
// ACTIVE experiment policy (generation 1) plus an armed manifest.
//
// Proves that the recovery image:
//   * refuses to resume the journaled experiment (ROM baseline held, manifest
//     not re-armed, generation high-water carried forward);
//   * fail-closes every actuation entry point with recovery_image;
//   * keeps the consumer switchover permanently OFF (snapshot inactive,
//     policy_read/_read_b always return the legacy value);
//   * still compiles the generated schema: ROM baseline values and identity
//     readback ("...|recovery") remain available.
//
// Build + run (wired as make test-policy-recovery, part of test-firmware):
//   g++ -std=c++17 -DPOLICY_ENGINE_RECOVERY -I lib \
//       -o test/test_policy_recovery test/test_policy_recovery.cpp
//   ./test/test_policy_recovery <fixture-prefix>

#ifndef POLICY_ENGINE_RECOVERY
#error "test_policy_recovery.cpp must be compiled with -DPOLICY_ENGINE_RECOVERY"
#endif

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

static const uint32_t kFrom = 1755000000;  // matches the fixture emitter
static const uint32_t kDay1 = 20260812;

// Journal storage backed by the fixture files; writes stay in memory.
struct FileStorage : PolicyJournalStorage {
  uint8_t data[2][kJournalRecordCap] = {};
  size_t len[2] = {0, 0};
  bool present[2] = {false, false};

  bool load(const char* prefix) {
    for (int copy = 0; copy < 2; ++copy) {
      char path[512];
      std::snprintf(path, sizeof(path), "%s.copy%d", prefix, copy);
      FILE* f = std::fopen(path, "rb");
      if (f == nullptr) return false;
      len[copy] = std::fread(data[copy], 1, kJournalRecordCap, f);
      present[copy] = len[copy] > 0;
      std::fclose(f);
    }
    return present[0] || present[1];
  }
  bool read_copy(uint8_t copy, uint8_t* out, size_t cap, size_t* out_len) override {
    if (copy > 1 || !present[copy] || len[copy] > cap) return false;
    std::memcpy(out, data[copy], len[copy]);
    *out_len = len[copy];
    return true;
  }
  bool write_copy(uint8_t copy, const uint8_t* src, size_t n) override {
    if (copy > 1 || n > kJournalRecordCap) return false;
    std::memcpy(data[copy], src, n);
    len[copy] = n;
    present[copy] = true;
    return true;
  }
};

int main(int argc, char** argv) {
  if (argc != 2) {
    std::printf("usage: test_policy_recovery <fixture-prefix>\n");
    return 2;
  }

  CHECK(kRecoveryImage, "compiled as recovery image");

  // Generated schema still compiled in: ROM baseline decodes to the goldens.
  CHECK(std::memcmp(kRomBaselinePolicy.content_sha256, kGoldenContentSha[0], 32) == 0,
        "ROM baseline content sha matches golden");
  CHECK(kRomBaselinePolicy.values[kPF_mister_engage_kpa] == 1.6f, "ROM values decodable");

  FileStorage storage;
  CHECK(storage.load(argv[1]), "fixture journal loaded");

  // Boot against a journal holding an ACTIVE experiment policy + armed
  // manifest: the recovery image must hold the ROM baseline and stay unarmed.
  PolicyEngine engine;
  engine.bind_storage(&storage);
  const ResetEvent ev = engine.boot_init(kFrom + 30, true, kDay1);
  CHECK(ev.outcome != BootOutcome::kResumedActive, "journaled experiment NOT resumed");
  CHECK(!ev.manifest_rearmed, "journaled manifest NOT re-armed");
  CHECK(engine.rom_baseline_active(), "ROM baseline held");
  CHECK(!engine.manifest_armed(), "manifest unarmed");
  CHECK(engine.active().generation == 0, "active generation 0");
  CHECK(engine.last_committed_generation() == 1, "generation high-water carried forward");

  // Consumer switchover permanently off.
  const PolicySnapshot snap = engine.on_tick(kFrom + 40, true, kDay1);
  CHECK(!snap.experiment_active, "snapshot never experiment_active");
  CHECK(snap.generation == 0, "snapshot carries ROM generation");
  CHECK(snap.values[kPF_mister_engage_kpa] == kRomBaselinePolicy.values[kPF_mister_engage_kpa],
        "snapshot carries ROM values");
  CHECK(!policy_experiment_active(), "policy_experiment_active() constant false");
  CHECK(policy_read(kPF_mister_engage_kpa, 42.5f) == 42.5f, "policy_read always legacy");
  CHECK(policy_read_b(kPF_sw_summer_vent_enabled, false) == false, "policy_read_b always legacy");

  // Identity readback still decodes and labels the image type.
  char identity[kPolicyIdentityMax];
  engine.identity_readback(identity, sizeof(identity));
  CHECK(std::strstr(identity, "|recovery") != nullptr, "identity apply_state is recovery");
  char expected_prefix[16];
  std::snprintf(expected_prefix, sizeof(expected_prefix), "%u|0|-|-|", static_cast<unsigned>(kWireSchemaVersion));
  CHECK(std::strncmp(identity, expected_prefix, std::strlen(expected_prefix)) == 0,
        "identity echoes schema|generation 0|unbound ids");

  // Every actuation entry point fail-closes with recovery_image.
  uint8_t buf[8] = {0};
  CHECK(!engine.manifest_begin(kManifestMinSize) && engine.last_error() == PolicyError::kRecoveryImage,
        "manifest_begin rejected");
  CHECK(!engine.begin_policy(buf, sizeof(buf)) && engine.last_error() == PolicyError::kRecoveryImage,
        "begin_policy rejected");
  CHECK(!engine.stage_chunk(0, buf, sizeof(buf)) && engine.last_error() == PolicyError::kRecoveryImage,
        "stage_chunk rejected");
  CHECK(!engine.validate_policy() && engine.last_error() == PolicyError::kRecoveryImage, "validate rejected");
  CHECK(!engine.commit_policy(PolicySlot::kBoundary, kFrom + 50) && engine.last_error() == PolicyError::kRecoveryImage,
        "commit rejected");
  CHECK(!engine.manifest_commit() && engine.last_error() == PolicyError::kRecoveryImage, "manifest_commit rejected");
  CHECK(std::strcmp(policy_error_name(PolicyError::kRecoveryImage), "recovery_image") == 0, "error name");

  // Journal writes continue in recovery, and what they persist is
  // ROM-baseline state: a fresh boot from the rewritten journal must also
  // hold ROM (a later full-image flash then cannot silently resume either —
  // both copies converge on ROM-baseline records as recovery journals).
  CHECK(engine.journal_write(), "journal_write works in recovery");
  CHECK(engine.journal_write(), "second journal_write covers the other copy");
  PolicyEngine engine2;
  engine2.bind_storage(&storage);
  const ResetEvent ev2 = engine2.boot_init(kFrom + 60, true, kDay1);
  CHECK(ev2.outcome != BootOutcome::kResumedActive, "rewritten journal resumes nothing");
  CHECK(engine2.rom_baseline_active(), "rewritten journal boots to ROM");
  CHECK(engine2.last_committed_generation() == 1, "generation high-water persisted");

  if (checks_failed == 0) {
    std::printf("test_policy_recovery: all %d checks passed\n", checks_run);
    return 0;
  }
  std::printf("test_policy_recovery: %d/%d checks FAILED\n", checks_failed, checks_run);
  return 1;
}
