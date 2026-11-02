// test_zone_vpd.cpp — native tests for the peer-probe VPD fallback
// (irrigation F8, D-IRP-13, VD-43).
//
// zone_vpd_with_fallback() resolves each zone's VPD for mister zone choice:
// own probe while fresh and finite, else the mean of the fresh peer probes
// (south, west, east), else the house average; center always uses the house
// average. The same header compiles into the ESP32 image.
//
// Build + run (make test-zone-vpd, part of test-firmware and of the native
// firmware step of scripts/ci-local.sh):
//   cd firmware && g++ -std=c++17 -I lib -o test/test_zone_vpd test/test_zone_vpd.cpp
//   ./test/test_zone_vpd

#include "zone_vpd.h"

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <type_traits>
#include <utility>

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

static bool near(float a, float b) { return std::fabs(a - b) < 1e-5f; }

static_assert(noexcept(zone_vpd_with_fallback(ZONE_SOUTH, std::declval<const ZoneProbe (&)[ZONE_COUNT]>(),
                                              1.0f, 0u)),
              "zone_vpd_with_fallback must be noexcept");
static_assert(noexcept(vpd_source_name(VpdSource::OWN)), "vpd_source_name must be noexcept");
static_assert(sizeof(VpdSource) == 1, "VpdSource is one byte");
static_assert(std::is_trivially_copyable<ZoneProbe>::value && std::is_trivially_copyable<ZoneVpd>::value,
              "plain value types, no heap");
static_assert(kZoneProbeStaleMs == 300000u, "same 300 s window as average_vpd_kpa");

// A tick well past boot; fresh stamps are a few seconds old.
static constexpr uint32_t kNow = 3600000u;          // 1 h of uptime
static constexpr uint32_t kFresh = kNow - 5000u;    // 5 s old
static constexpr uint32_t kSixMinOld = kNow - 360000u;
static constexpr float kHouse = 0.9f;

static void set(ZoneProbe (&zp)[ZONE_COUNT], ZoneId z, float vpd, uint32_t last_ms) {
  zp[z] = {vpd, last_ms};
}

// Every probe fresh and distinct; center carries no probe.
static void all_fresh(ZoneProbe (&zp)[ZONE_COUNT]) {
  set(zp, ZONE_CENTER, NAN, 0u);
  set(zp, ZONE_SOUTH, 1.4f, kFresh);
  set(zp, ZONE_WEST, 1.0f, kFresh);
  set(zp, ZONE_EAST, 1.2f, kFresh);
}

static void own_fresh_wins() {
  ZoneProbe zp[ZONE_COUNT];
  all_fresh(zp);
  const ZoneVpd s = zone_vpd_with_fallback(ZONE_SOUTH, zp, kHouse, kNow);
  const ZoneVpd w = zone_vpd_with_fallback(ZONE_WEST, zp, kHouse, kNow);
  const ZoneVpd e = zone_vpd_with_fallback(ZONE_EAST, zp, kHouse, kNow);
  CHECK(s.source == VpdSource::OWN && near(s.vpd_kpa, 1.4f), "south fresh -> own 1.4");
  CHECK(w.source == VpdSource::OWN && near(w.vpd_kpa, 1.0f), "west fresh -> own 1.0");
  CHECK(e.source == VpdSource::OWN && near(e.vpd_kpa, 1.2f), "east fresh -> own 1.2");
}

// Acceptance (#751, south Modbus address 4 out): last_south_ms 6 min old while
// vpd_south still holds 2.4 kPa; fresh west 1.0 and east 1.2 -> peer 1.1.
static void south_out_held_value_uses_peer_mean() {
  ZoneProbe zp[ZONE_COUNT];
  all_fresh(zp);
  set(zp, ZONE_SOUTH, 2.4f, kSixMinOld);
  const ZoneVpd s = zone_vpd_with_fallback(ZONE_SOUTH, zp, kHouse, kNow);
  CHECK(s.source == VpdSource::PEER, "stale south with a finite held value -> peer");
  CHECK(near(s.vpd_kpa, 1.1f), "peer mean of west 1.0 and east 1.2 is 1.1");
  CHECK(std::strcmp(vpd_source_name(s.source), "peer") == 0, "source name peer");

  // The stale south reading cannot raise south's stress score: controls.yaml
  // scores (south_vpd - target) / target, and the peer mean replaces 2.4.
  const float south_target = 1.3f;
  const float stress_from_held = (2.4f - south_target) / south_target;
  const float stress_from_fallback = (s.vpd_kpa - south_target) / south_target;
  CHECK(stress_from_held > 0.0f, "held 2.4 would have scored south as stressed");
  CHECK(stress_from_fallback <= 0.0f, "peer mean keeps south below its target");

  // The other zones keep their own fresh probes; the dead one is never a peer.
  const ZoneVpd w = zone_vpd_with_fallback(ZONE_WEST, zp, kHouse, kNow);
  CHECK(w.source == VpdSource::OWN && near(w.vpd_kpa, 1.0f), "west unaffected by the south outage");
  set(zp, ZONE_WEST, 1.0f, kSixMinOld);
  const ZoneVpd w2 = zone_vpd_with_fallback(ZONE_WEST, zp, kHouse, kNow);
  CHECK(w2.source == VpdSource::PEER && near(w2.vpd_kpa, 1.2f), "stale south is never averaged in as a peer");

  // Acceptance: with west and east stale too, south gets the house value.
  set(zp, ZONE_EAST, 1.2f, kSixMinOld);
  const ZoneVpd s2 = zone_vpd_with_fallback(ZONE_SOUTH, zp, kHouse, kNow);
  CHECK(s2.source == VpdSource::HOUSE && near(s2.vpd_kpa, kHouse), "no usable peer -> house");
}

static void own_nan_or_infinite_uses_peers() {
  ZoneProbe zp[ZONE_COUNT];
  all_fresh(zp);
  set(zp, ZONE_WEST, NAN, kFresh);  // probe answers, RH sentinel -> NaN VPD
  const ZoneVpd w = zone_vpd_with_fallback(ZONE_WEST, zp, kHouse, kNow);
  CHECK(w.source == VpdSource::PEER && near(w.vpd_kpa, (1.4f + 1.2f) / 2.0f), "fresh NaN own -> peer mean");

  set(zp, ZONE_WEST, INFINITY, kFresh);
  const ZoneVpd w_inf = zone_vpd_with_fallback(ZONE_WEST, zp, kHouse, kNow);
  CHECK(w_inf.source == VpdSource::PEER, "fresh +inf own -> peer");

  // A non-finite peer is not averaged in.
  set(zp, ZONE_WEST, -INFINITY, kFresh);
  const ZoneVpd s = zone_vpd_with_fallback(ZONE_SOUTH, zp, kHouse, kNow);
  CHECK(s.source == VpdSource::OWN, "south own still fresh");
  set(zp, ZONE_SOUTH, NAN, kFresh);
  const ZoneVpd s2 = zone_vpd_with_fallback(ZONE_SOUTH, zp, kHouse, kNow);
  CHECK(s2.source == VpdSource::PEER && near(s2.vpd_kpa, 1.2f), "-inf west skipped; east alone is the peer");
}

static void one_two_and_no_usable_peers() {
  ZoneProbe zp[ZONE_COUNT];
  all_fresh(zp);
  set(zp, ZONE_EAST, NAN, 0u);  // east never seen since boot
  const ZoneVpd two = zone_vpd_with_fallback(ZONE_EAST, zp, kHouse, kNow);
  CHECK(two.source == VpdSource::PEER && near(two.vpd_kpa, (1.4f + 1.0f) / 2.0f), "two usable peers -> mean");

  set(zp, ZONE_SOUTH, 1.4f, kSixMinOld);
  const ZoneVpd one = zone_vpd_with_fallback(ZONE_EAST, zp, kHouse, kNow);
  CHECK(one.source == VpdSource::PEER && near(one.vpd_kpa, 1.0f), "one usable peer -> that peer");

  set(zp, ZONE_WEST, 1.0f, kSixMinOld);
  const ZoneVpd none = zone_vpd_with_fallback(ZONE_EAST, zp, kHouse, kNow);
  CHECK(none.source == VpdSource::HOUSE && near(none.vpd_kpa, kHouse), "no usable peer -> house");
}

static void never_seen_and_stale_boundary() {
  ZoneProbe zp[ZONE_COUNT];
  all_fresh(zp);
  // Early boot: millis() below the window, a probe that never called back
  // (last_ms 0) holding a finite initial value is still not usable.
  const uint32_t boot_now = 20000u;
  set(zp, ZONE_SOUTH, 1.4f, 0u);
  set(zp, ZONE_WEST, 1.0f, 15000u);
  set(zp, ZONE_EAST, 1.2f, 0u);
  const ZoneVpd s = zone_vpd_with_fallback(ZONE_SOUTH, zp, kHouse, boot_now);
  CHECK(s.source == VpdSource::PEER && near(s.vpd_kpa, 1.0f), "never-seen own and peer are skipped");

  // Exactly stale_ms old is stale (probe_sample_fresh uses a strict <).
  all_fresh(zp);
  set(zp, ZONE_SOUTH, 2.0f, kNow - kZoneProbeStaleMs);
  CHECK(zone_vpd_with_fallback(ZONE_SOUTH, zp, kHouse, kNow).source == VpdSource::PEER, "age == window is stale");
  set(zp, ZONE_SOUTH, 2.0f, kNow - kZoneProbeStaleMs + 1u);
  CHECK(zone_vpd_with_fallback(ZONE_SOUTH, zp, kHouse, kNow).source == VpdSource::OWN, "age < window is fresh");

  // The window is a parameter; the default is the 300 s one.
  set(zp, ZONE_SOUTH, 2.0f, kNow - 120000u);
  CHECK(zone_vpd_with_fallback(ZONE_SOUTH, zp, kHouse, kNow).source == VpdSource::OWN, "2 min old, default window");
  CHECK(zone_vpd_with_fallback(ZONE_SOUTH, zp, kHouse, kNow, 60000u).source == VpdSource::PEER,
        "2 min old, 60 s window");
}

static void millis_wrap() {
  ZoneProbe zp[ZONE_COUNT];
  all_fresh(zp);
  // millis() wrapped 15 ms ago; the south probe answered 15 ms before the wrap.
  const uint32_t now = 15u;
  set(zp, ZONE_SOUTH, 1.4f, UINT32_MAX - 14u);
  set(zp, ZONE_WEST, 1.0f, UINT32_MAX - 400000u);  // 400 s old across the wrap
  set(zp, ZONE_EAST, 1.2f, 10u);
  const ZoneVpd s = zone_vpd_with_fallback(ZONE_SOUTH, zp, kHouse, now);
  CHECK(s.source == VpdSource::OWN && near(s.vpd_kpa, 1.4f), "fresh across the wrap");
  const ZoneVpd w = zone_vpd_with_fallback(ZONE_WEST, zp, kHouse, now);
  CHECK(w.source == VpdSource::PEER && near(w.vpd_kpa, (1.4f + 1.2f) / 2.0f), "stale across the wrap");
}

static void center_always_house() {
  ZoneProbe zp[ZONE_COUNT];
  all_fresh(zp);
  // Even a fresh, finite value in the center slot is ignored: center has no
  // probe, and it is never a peer of the wall zones either.
  set(zp, ZONE_CENTER, 5.0f, kFresh);
  const ZoneVpd c = zone_vpd_with_fallback(ZONE_CENTER, zp, kHouse, kNow);
  CHECK(c.source == VpdSource::HOUSE && near(c.vpd_kpa, kHouse), "center -> house with probes fresh");
  set(zp, ZONE_SOUTH, 1.4f, kSixMinOld);
  const ZoneVpd s = zone_vpd_with_fallback(ZONE_SOUTH, zp, kHouse, kNow);
  CHECK(s.source == VpdSource::PEER && near(s.vpd_kpa, 1.1f), "center slot is never a peer");

  for (ZoneProbe& p : zp) p = {NAN, 0u};
  const ZoneVpd c2 = zone_vpd_with_fallback(ZONE_CENTER, zp, 1.7f, kNow);
  CHECK(c2.source == VpdSource::HOUSE && near(c2.vpd_kpa, 1.7f), "center -> house with every probe out");
  const ZoneVpd bad = zone_vpd_with_fallback(ZONE_COUNT, zp, 1.7f, kNow);
  CHECK(bad.source == VpdSource::HOUSE && near(bad.vpd_kpa, 1.7f), "out-of-range zone -> house");
}

// Every wall zone against every usability mix of the three probes.
static void exhaustive_wall_zone_matrix() {
  const ZoneId walls[] = {ZONE_SOUTH, ZONE_WEST, ZONE_EAST};
  const float value[ZONE_COUNT] = {NAN, 1.6f, 0.8f, 1.1f};
  for (unsigned mask = 0; mask < 8u; ++mask) {
    ZoneProbe zp[ZONE_COUNT];
    zp[ZONE_CENTER] = {NAN, 0u};
    for (int i = 0; i < 3; ++i) {
      const ZoneId z = walls[i];
      const bool usable = (mask >> i) & 1u;
      // Unusable alternates between a stale held value and a fresh NaN.
      zp[z] = usable ? ZoneProbe{value[z], kFresh}
                     : ((i % 2) ? ZoneProbe{NAN, kFresh} : ZoneProbe{value[z] + 1.0f, kSixMinOld});
    }
    for (int i = 0; i < 3; ++i) {
      const ZoneId z = walls[i];
      const ZoneVpd got = zone_vpd_with_fallback(z, zp, kHouse, kNow);
      float sum = 0.0f;
      int n = 0;
      for (int j = 0; j < 3; ++j) {
        if (j != i && ((mask >> j) & 1u)) { sum += value[walls[j]]; ++n; }
      }
      char label[96];
      std::snprintf(label, sizeof(label), "matrix mask=%u zone=%s", mask, ZONE_NAMES[z]);
      if ((mask >> i) & 1u) {
        CHECK(got.source == VpdSource::OWN && near(got.vpd_kpa, value[z]), label);
      } else if (n > 0) {
        CHECK(got.source == VpdSource::PEER && near(got.vpd_kpa, sum / float(n)), label);
      } else {
        CHECK(got.source == VpdSource::HOUSE && near(got.vpd_kpa, kHouse), label);
      }
      CHECK(std::isfinite(got.vpd_kpa), "resolved VPD is finite whenever the house value is");
    }
  }
}

static void source_names() {
  CHECK(std::strcmp(vpd_source_name(VpdSource::OWN), "own") == 0, "own");
  CHECK(std::strcmp(vpd_source_name(VpdSource::PEER), "peer") == 0, "peer");
  CHECK(std::strcmp(vpd_source_name(VpdSource::HOUSE), "house") == 0, "house");
  CHECK(std::strcmp(vpd_source_name(static_cast<VpdSource>(7)), "house") == 0, "unknown -> house");
}

int main() {
  own_fresh_wins();
  south_out_held_value_uses_peer_mean();
  own_nan_or_infinite_uses_peers();
  one_two_and_no_usable_peers();
  never_seen_and_stale_boundary();
  millis_wrap();
  center_always_house();
  exhaustive_wall_zone_matrix();
  source_names();
  std::printf("test_zone_vpd: %d checks, %d failed\n", checks_run, checks_failed);
  return checks_failed == 0 ? 0 : 1;
}
