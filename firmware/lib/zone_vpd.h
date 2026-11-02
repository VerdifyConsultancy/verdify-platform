#pragma once
/*
 * zone_vpd.h — per-zone VPD with the peer-probe fallback (irrigation F8)
 * =====================================================================
 *
 * D-IRP-13: a probe outage must not stop VPD misting, and nothing waits for a
 * probe repair. A Modbus sensor keeps its last value when the probe stops
 * answering, so a finite reading alone does not prove a live probe; the age
 * of the probe's last callback (last_south_ms / last_west_ms / last_east_ms,
 * stamped in each probe's on_value) decides freshness.
 *
 * Resolution for one zone:
 *   1. CENTER has no probe and always uses the house average (HOUSE).
 *   2. The zone's own probe, while fresh and finite (OWN).
 *   3. Otherwise the mean of the fresh, finite peer probes among SOUTH, WEST
 *      and EAST, the zone itself excluded (PEER).
 *   4. Otherwise the house average the caller passes (HOUSE).
 *
 * Pure C++17: no ESPHome or Arduino includes, no heap. The same code runs on
 * the ESP32 (controls.yaml zone choice) and in the native tests
 * (firmware/test/test_zone_vpd.cpp).
 */

#include <cmath>
#include <cstdint>

#include "probe_validity.h"
#include "greenhouse_solar.h"

// Which reading a zone's VPD came from. vpd_source_name() gives the OpenAPI
// `vpd_source` value; irr_state carries one letter per zone (o, p, h).
enum class VpdSource : uint8_t { OWN, PEER, HOUSE };

struct ZoneProbe { float vpd_kpa; uint32_t last_ms; };  // last_ms 0 = never seen
struct ZoneVpd   { float vpd_kpa; VpdSource source; };

// The staleness window average_vpd_kpa (sensors.yaml) and the probe watchdog use.
constexpr uint32_t kZoneProbeStaleMs = 300000;

// The peer scan walks SOUTH..EAST by value; keep it tied to greenhouse_solar.h.
static_assert(ZONE_CENTER == 0 && ZONE_SOUTH == 1 && ZONE_WEST == 2 && ZONE_EAST == 3 && ZONE_COUNT == 4,
              "zone_vpd.h assumes CENTER, SOUTH, WEST, EAST = 0..3");

// A reading is usable when its probe called back within stale_ms (wrap-safe,
// never-seen excluded) and the value is finite.
inline bool zone_probe_usable(const ZoneProbe& probe, uint32_t now_ms, uint32_t stale_ms) noexcept {
    return probe_sample_fresh(now_ms, probe.last_ms, stale_ms) && std::isfinite(probe.vpd_kpa);
}

inline ZoneVpd zone_vpd_with_fallback(ZoneId zone, const ZoneProbe (&probes)[ZONE_COUNT],
                                      float house_vpd_kpa, uint32_t now_ms,
                                      uint32_t stale_ms = kZoneProbeStaleMs) noexcept {
    // CENTER has no probe; an out-of-range zone gets the same safe answer.
    if (zone <= ZONE_CENTER || zone >= ZONE_COUNT) {
        return {house_vpd_kpa, VpdSource::HOUSE};
    }
    if (zone_probe_usable(probes[zone], now_ms, stale_ms)) {
        return {probes[zone].vpd_kpa, VpdSource::OWN};
    }
    float sum = 0.0f;
    int usable = 0;
    for (int peer = ZONE_SOUTH; peer <= ZONE_EAST; ++peer) {
        if (peer == zone || !zone_probe_usable(probes[peer], now_ms, stale_ms)) continue;
        sum += probes[peer].vpd_kpa;
        ++usable;
    }
    if (usable > 0) {
        return {sum / float(usable), VpdSource::PEER};
    }
    return {house_vpd_kpa, VpdSource::HOUSE};
}

inline const char* vpd_source_name(VpdSource s) noexcept {
    switch (s) {
        case VpdSource::OWN:   return "own";
        case VpdSource::PEER:  return "peer";
        case VpdSource::HOUSE: return "house";
    }
    return "house";
}
