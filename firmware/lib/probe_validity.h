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

// #368: plausibility of a probe that is fresh and finite. Diagnostic only: a
// JUMP or FLAT probe still contributes to the aggregates and to the zone VPD
// (peer/house fallback, D-IRP-13), and neither raises SENSOR_FAULT, which stays
// the all-probes-silent safety path (docs/firmware-fsm-spec.md, "Probe
// plausibility"). Thresholds come from 30 days of per-probe history (2026-11-06):
// the largest real temperature step between ~60 s samples was 3.2 F (99.99th
// percentile 1.8 F), while the East probe's 2026-10-27 fault stepped 24-26 F for
// one sample; the longest real run of identical temperature and RH was 14 minutes.
constexpr float kProbeJumpF = 6.0f;               // temperature step between samples
constexpr uint32_t kProbeJumpWindowMs = 90000;    // only samples at most this far apart
constexpr uint32_t kProbeJumpHoldMs = 600000;     // JUMP is reported for 10 minutes
constexpr uint32_t kProbeFlatMs = 3600000;        // 60 minutes of identical temperature and RH

enum class ProbeTrendState : uint8_t { kOk, kJump, kFlat };

struct ProbeTrend {
    float temp = NAN;
    float rh = NAN;
    uint32_t sample_ms = 0;
    uint32_t changed_ms = 0;
    uint32_t jump_ms = 0;
    bool have = false;
    bool jumped = false;
};

// Feed one fresh, finite sample (call it once per diagnostic period). A
// non-finite sample restarts the trend, so INVALID never turns into FLAT.
inline ProbeTrendState probe_trend_update(ProbeTrend& p, float temp, float rh, uint32_t now_ms) {
    if (!probe_pair_finite(temp, rh)) {
        p = ProbeTrend{};
        return ProbeTrendState::kOk;
    }
    if (!p.have) {
        p.have = true;
        p.changed_ms = now_ms;
    } else {
        if (now_ms - p.sample_ms <= kProbeJumpWindowMs && std::fabs(temp - p.temp) > kProbeJumpF) {
            p.jumped = true;
            p.jump_ms = now_ms;
        }
        if (temp != p.temp || rh != p.rh) p.changed_ms = now_ms;
    }
    p.temp = temp;
    p.rh = rh;
    p.sample_ms = now_ms;
    if (p.jumped && now_ms - p.jump_ms >= kProbeJumpHoldMs) p.jumped = false;
    if (p.jumped) return ProbeTrendState::kJump;
    if (now_ms - p.changed_ms >= kProbeFlatMs) return ProbeTrendState::kFlat;
    return ProbeTrendState::kOk;
}
