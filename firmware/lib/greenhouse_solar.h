#pragma once
/*
 * greenhouse_solar.h — On-chip solar ephemeris + deterministic band curves
 * ========================================================================
 *
 * Firmware-v2 (docs/design/firmware-v2-contract-2026-06-10.md §B1/§B2/§B3).
 *
 * OFFLINE-FIRST: the ESP32 computes sunrise / solar-noon / sunset locally
 * (NOAA solar-position approximation) and interpolates the served band from
 * NVS-persisted anchors, so the full diurnal program keeps enforcing with
 * ZERO network. The dispatcher syncs anchor values when crop profiles
 * change; it is not a control-loop dependency.
 *
 * PURE C++: no ESPHome dependencies; identical math on ESP32, native unit
 * tests, and the replay harness (replay derives day-of-year / minute-of-day
 * from row timestamps). Single-precision floats by design (ESP32 FPU).
 */

#include <cstdint>
#include <cmath>
#include <algorithm>

// ── Greenhouse site constants (Longmont, CO) ───────────────────────────
static constexpr float GH_LATITUDE_DEG  = 40.167f;
static constexpr float GH_LONGITUDE_DEG = -105.102f;  // east-positive

struct SolarTimes {
    int sunrise_min;     // minutes from local midnight
    int solar_noon_min;
    int sunset_min;
};

// NOAA solar geometry (General Solar Position Calculations, NOAA SPC).
// Accuracy ~±2 min at mid-latitudes — far inside the band's tolerance.
// utc_offset_min: local-minus-UTC including DST (caller derives it from the
// time component each cycle, so DST transitions are correct by construction).
inline SolarTimes compute_solar_times(
    int day_of_year,
    float lat_deg,
    float lon_deg,
    int utc_offset_min
) noexcept {
    constexpr float PI_F = 3.14159265358979f;
    day_of_year = std::max(1, std::min(366, day_of_year));

    // Fractional year (radians), evaluated at solar noon.
    const float g = 2.0f * PI_F / 365.0f * (float(day_of_year) - 1.0f);

    // Equation of time (minutes) and solar declination (radians).
    const float eqtime = 229.18f * (0.000075f
        + 0.001868f * std::cos(g)       - 0.032077f * std::sin(g)
        - 0.014615f * std::cos(2.0f*g)  - 0.040849f * std::sin(2.0f*g));
    const float decl = 0.006918f
        - 0.399912f * std::cos(g)        + 0.070257f * std::sin(g)
        - 0.006758f * std::cos(2.0f*g)   + 0.000907f * std::sin(2.0f*g)
        - 0.002697f * std::cos(3.0f*g)   + 0.001480f * std::sin(3.0f*g);

    const float lat_rad = lat_deg * PI_F / 180.0f;
    // Sunrise/sunset zenith 90.833° (refraction + solar disc radius).
    const float zenith = 90.833f * PI_F / 180.0f;
    float cos_ha = (std::cos(zenith) - std::sin(lat_rad) * std::sin(decl))
                 / (std::cos(lat_rad) * std::cos(decl));
    cos_ha = std::max(-1.0f, std::min(1.0f, cos_ha));  // polar clamp
    const float ha_deg = std::acos(cos_ha) * 180.0f / PI_F;

    const float noon_utc    = 720.0f - 4.0f * lon_deg - eqtime;
    const float sunrise_utc = noon_utc - 4.0f * ha_deg;
    const float sunset_utc  = noon_utc + 4.0f * ha_deg;

    auto to_local = [utc_offset_min](float utc_min) -> int {
        int m = int(std::lround(utc_min)) + utc_offset_min;
        m %= 1440; if (m < 0) m += 1440;
        return m;
    };
    return {
        .sunrise_min    = to_local(sunrise_utc),
        .solar_noon_min = to_local(noon_utc),
        .sunset_min     = to_local(sunset_utc)
    };
}

inline SolarTimes compute_greenhouse_solar_times(int day_of_year, int utc_offset_min) noexcept {
    return compute_solar_times(day_of_year, GH_LATITUDE_DEG, GH_LONGITUDE_DEG, utc_offset_min);
}

// ── Solar phase: continuous [0,4) — 0=SR, 1=solar-noon, 2=SS, 3=solar-midnight ──
// The day half (SR→SM→SS) spans [0,2]; the night half (SS→midnight→next-SR)
// spans [2,4). Solar-midnight = midpoint(SS, SR+24h). The band curve is a
// pure function of this phase, so the diurnal program stretches/compresses
// automatically as day length drifts through the year.
inline float solar_phase(int now_minute, const SolarTimes& st) noexcept {
    now_minute = ((now_minute % 1440) + 1440) % 1440;
    int sr = st.sunrise_min, sm = st.solar_noon_min, ss = st.sunset_min;
    // Unwrap into a monotonic solar day starting at sunrise.
    if (sm < sr) sm += 1440;
    if (ss < sm) ss += 1440;
    const int next_sr = sr + 1440;
    const int smid = ss + (next_sr - ss) / 2;
    int m = now_minute;
    if (m < sr) m += 1440;

    auto seg = [](int v, int a, int b) -> float {
        if (b <= a) return 0.0f;
        return float(v - a) / float(b - a);
    };
    if (m <= sm)  return 0.0f + seg(m, sr, sm);
    if (m <= ss)  return 1.0f + seg(m, sm, ss);
    if (m <= smid) return 2.0f + seg(m, ss, smid);
    const float p = 3.0f + seg(m, smid, next_sr);
    return p >= 4.0f ? 0.0f : p;
}

// Minutes until sunset; negative when past sunset (within the same solar day).
inline int minutes_to_sunset(int now_minute, const SolarTimes& st) noexcept {
    now_minute = ((now_minute % 1440) + 1440) % 1440;
    int sr = st.sunrise_min, ss = st.sunset_min;
    if (ss < sr) ss += 1440;
    int m = now_minute;
    if (m < sr) m += 1440;
    return ss - m;
}

// Crude fallback when the caller cannot supply a real phase (no time source):
// assumes SR=6:00, SM=13:00, SS=20:00. Used ONLY when SensorInputs carries an
// unset phase, so the controller degrades gracefully rather than mis-gating.
inline float fallback_solar_phase(int local_hour) noexcept {
    const SolarTimes st = {360, 780, 1200};
    return solar_phase(std::max(0, std::min(23, local_hour)) * 60, st);
}

// ── Deterministic band curve: 4 anchors, cosine interpolation ──────────
// Anchor order matches the phase segments: value at SR, SM, SS, solar-midnight.
struct BandAnchors {
    float sr;
    float sm;
    float ss;
    float mid;
};

inline float band_value_at_phase(const BandAnchors& a, float phase) noexcept {
    constexpr float PI_F = 3.14159265358979f;
    if (!std::isfinite(phase)) phase = 1.0f;
    phase = phase - 4.0f * std::floor(phase / 4.0f);  // wrap to [0,4)
    // Smooth 4-anchor harmonic (discrete-Fourier) interpolation through
    // SR/SM/SS/MID at phase 0/1/2/3. Passes EXACTLY through every anchor but is
    // C-infinity smooth — unlike the old piecewise cosine-ease, which had zero
    // slope at every anchor and so flattened into four visible plateaus ("lumpy"
    // bands). One curve, not four stitched eases. Mirrors db fn_crop_band_value
    // (migration 170) and ingestor/solar.py exactly.
    const float theta = PI_F * phase * 0.5f;  // 0..2π
    const float c0 = (a.sr + a.sm + a.ss + a.mid) * 0.25f;
    const float c1 = (a.sr - a.ss) * 0.5f;
    const float s1 = (a.sm - a.mid) * 0.5f;
    const float c2 = (a.sr - a.sm + a.ss - a.mid) * 0.25f;
    return c0 + c1 * std::cos(theta) + s1 * std::sin(theta) + c2 * std::cos(2.0f * theta);
}

// ── Per-zone VPD bands + priority arbitration (contract §B3) ────────────
enum ZoneId { ZONE_CENTER = 0, ZONE_SOUTH = 1, ZONE_WEST = 2, ZONE_EAST = 3, ZONE_COUNT = 4 };

inline constexpr const char* ZONE_NAMES[] = {"center", "south", "west", "east"};

struct ZoneBand {
    float target;
    float low;    // target - width_below  (stress-low edge)
    float high;   // target + width_above  (stress-high edge)
};

inline ZoneBand zone_band_at_phase(
    const BandAnchors& target_curve,
    float width_below,
    float width_above,
    float phase
) noexcept {
    const float t = band_value_at_phase(target_curve, phase);
    width_below = std::max(0.05f, width_below);
    width_above = std::max(0.05f, width_above);
    return { .target = t, .low = t - width_below, .high = t + width_above };
}

struct ZoneWetIntent {
    bool  wants_wet;     // zone VPD above target (deadband-guarded)
    bool  stressed;      // zone VPD above the stress-high edge
    float urgency;       // normalized delta: (vpd - target) / (high - target); 0 below target
    const char* reason;  // "no_sensor" | "at_target" | "above_target" | "stressed"
};

// Per-zone wetting intent. Shared rails (taper, feed-hold, occupancy, dew
// margin, duty caps) are enforced by the caller/arbiter — this is the pure
// per-zone demand signal. A NaN reading yields no intent (the zone's wetting
// then rides the house-average machinery only).
inline ZoneWetIntent zone_wet_intent(float zone_vpd_kpa, const ZoneBand& band) noexcept {
    if (!std::isfinite(zone_vpd_kpa)) {
        return { .wants_wet = false, .stressed = false, .urgency = 0.0f, .reason = "no_sensor" };
    }
    constexpr float ENGAGE_DEADBAND_KPA = 0.05f;
    const float half_above = std::max(0.05f, band.high - band.target);
    const float delta = zone_vpd_kpa - band.target;
    if (delta <= ENGAGE_DEADBAND_KPA) {
        return { .wants_wet = false, .stressed = false, .urgency = 0.0f, .reason = "at_target" };
    }
    const float urgency = delta / half_above;
    const bool stressed = zone_vpd_kpa > band.high;
    return {
        .wants_wet = true,
        .stressed = stressed,
        .urgency = urgency,
        .reason = stressed ? "stressed" : "above_target"
    };
}

// Rank-based arbiter (owner's settable priority: 1 = highest). Among zones
// that want wetting AND have an actuator, the best (lowest) rank wins; ties
// break on urgency. Returns the winning ZoneId or -1 when no zone qualifies.
// EAST has no mister relay — the caller passes has_actuator[ZONE_EAST]=false
// and serves east through the west/center adjacency factor as before.
inline int arbitrate_wet_zone(
    const ZoneWetIntent (&intents)[ZONE_COUNT],
    const int (&priority_rank)[ZONE_COUNT],
    const bool (&has_actuator)[ZONE_COUNT]
) noexcept {
    int best = -1;
    for (int z = 0; z < ZONE_COUNT; z++) {
        if (!has_actuator[z] || !intents[z].wants_wet) continue;
        if (best < 0) { best = z; continue; }
        const int rz = priority_rank[z], rb = priority_rank[best];
        if (rz < rb || (rz == rb && intents[z].urgency > intents[best].urgency)) {
            best = z;
        }
    }
    return best;
}

// ── Host-side helpers (replay / invariants): solar truth from unix time ──
// The replay corpus rows carry ts_unix_s; these derive the same solar inputs
// the live firmware computes from its time component. Hinnant civil-from-days
// algorithm — exact for the whole unix era.
struct CivilDate { int year; int month; int day; int day_of_year; };

inline CivilDate civil_from_unix(uint64_t ts_unix_s, int utc_offset_min) noexcept {
    const int64_t local_s = int64_t(ts_unix_s) + int64_t(utc_offset_min) * 60;
    int64_t z = local_s / 86400; if (local_s < 0 && local_s % 86400 != 0) z--;
    z += 719468;
    const int64_t era = (z >= 0 ? z : z - 146096) / 146097;
    const unsigned doe = unsigned(z - era * 146097);
    const unsigned yoe = (doe - doe/1460 + doe/36524 - doe/146096) / 365;
    const int y = int(yoe) + int(era) * 400;
    const unsigned doy_mar = doe - (365*yoe + yoe/4 - yoe/100);
    const unsigned mp = (5*doy_mar + 2) / 153;
    const unsigned d = doy_mar - (153*mp + 2)/5 + 1;
    const unsigned m = mp < 10 ? mp + 3 : mp - 9;
    const int year = y + (m <= 2);
    // day-of-year from (year, m, d)
    static const int cum[12] = {0,31,59,90,120,151,181,212,243,273,304,334};
    const bool leap = (year % 4 == 0 && year % 100 != 0) || (year % 400 == 0);
    int doy = cum[m-1] + int(d) + ((leap && m > 2) ? 1 : 0);
    return { year, int(m), int(d), doy };
}

inline int local_minute_from_unix(uint64_t ts_unix_s, int utc_offset_min) noexcept {
    int64_t local_s = int64_t(ts_unix_s) + int64_t(utc_offset_min) * 60;
    int64_t mod = local_s % 86400; if (mod < 0) mod += 86400;
    return int(mod / 60);
}

// Infer the UTC offset from a row's local_hour (corpus rows carry local_hour
// but not the zone): result in whole hours, range [-12h, +11h].
inline int infer_utc_offset_min(uint64_t ts_unix_s, int local_hour) noexcept {
    const int utc_hour = int((ts_unix_s % 86400) / 3600);
    int diff = ((local_hour - utc_hour) % 24 + 36) % 24 - 12;
    return diff * 60;
}

inline SolarTimes solar_times_from_unix(uint64_t ts_unix_s, int utc_offset_min) noexcept {
    const CivilDate cd = civil_from_unix(ts_unix_s, utc_offset_min);
    return compute_greenhouse_solar_times(cd.day_of_year, utc_offset_min);
}
