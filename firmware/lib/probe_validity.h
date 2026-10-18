#pragma once

#include <cmath>
#include <cstdint>

// Diagnostic validity only. Aggregate membership, control quorum and physical
// trial eligibility are separate contracts. Timestamp subtraction is wrap-safe.
inline bool probe_sample_fresh(uint32_t now, uint32_t last, uint32_t stale_ms) {
    return last != 0 && (now - last) < stale_ms;
}

inline bool probe_pair_finite(float temperature, float humidity) {
    return std::isfinite(temperature) && std::isfinite(humidity);
}

inline bool probe_triple_finite(float temperature, float humidity, float vpd) {
    return probe_pair_finite(temperature, humidity) && std::isfinite(vpd);
}
