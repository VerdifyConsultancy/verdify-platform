// policy_journal_esphome.h — ESP32/ESPHome binding for the policy journal
// (Lane E, #586). Binds the pluggable PolicyJournalStorage model in
// policy_vector.h to ESPHome's NVS-backed global preferences, following the
// existing on-device pattern (the weekly wall-feed journal in controls.yaml
// uses global_preferences->make_preference<T>() the same way).
//
// Host builds compile this header to an empty translation unit: everything
// device-specific is guarded by ESP_PLATFORM (the same guard heap_diagnostics.h
// uses), so `g++ -I lib` host tests never see the ESPHome API. The host tests
// exercise the journal model itself through an injected fake storage.
//
// Flash-write budget: the engine journals only on state transitions (manifest
// arm, commit scheduled, slot apply, expiry fallback, explicit abort) — a few
// writes per day at the §8 replanning cadence, two NVS blobs of ≤1280 B each.
// No periodic write loop exists in this layer.
#pragma once

#include "policy_vector.h"

#ifdef ESP_PLATFORM

// Fixed-size NVS blob per copy. Trivially copyable so ESPHome's
// make_preference<T>() can store it.
struct PolicyJournalBlob {
  uint16_t len;
  uint8_t data[verdify_policy::kJournalRecordCap];
};

class EspPreferencesPolicyJournal : public verdify_policy::PolicyJournalStorage {
 public:
  bool read_copy(uint8_t copy, uint8_t* out, size_t cap, size_t* out_len) override {
    if (copy > 1) return false;
    auto pref = pref_for(copy);
    // Static blob: keep the 1.3 KB scratch out of the loop-task stack.
    static PolicyJournalBlob blob;
    if (!pref.load(&blob)) return false;
    if (blob.len == 0 || blob.len > verdify_policy::kJournalRecordCap || blob.len > cap) return false;
    std::memcpy(out, blob.data, blob.len);
    *out_len = blob.len;
    return true;
  }

  bool write_copy(uint8_t copy, const uint8_t* data, size_t len) override {
    if (copy > 1 || len == 0 || len > verdify_policy::kJournalRecordCap) return false;
    static PolicyJournalBlob blob;
    blob.len = static_cast<uint16_t>(len);
    std::memcpy(blob.data, data, len);
    std::memset(blob.data + len, 0, sizeof(blob.data) - len);
    auto pref = pref_for(copy);
    if (!pref.save(&blob)) return false;
    return esphome::global_preferences->sync();
  }

 private:
  static esphome::ESPPreferenceObject pref_for(uint8_t copy) {
    // Stable per-copy NVS keys ("VPJ0"/"VPJ1" as u32 hashes). Never reuse.
    const uint32_t kBaseKey = 0x56504A30u;  // "VPJ0"
    return esphome::global_preferences->make_preference<PolicyJournalBlob>(kBaseKey + copy);
  }
};

inline EspPreferencesPolicyJournal& esp_policy_journal() {
  static EspPreferencesPolicyJournal journal;
  return journal;
}

#endif  // ESP_PLATFORM
