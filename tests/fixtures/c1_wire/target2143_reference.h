// Source-only reference for measured successor620a and historical2143 arithmetic.
// Successor ELF sha256 39a237cfb044e72633d52646c203d27b1217ba9f9de3d4a8308798b1b7a96b5b.
// Historical2143 ELF sha256 f8cc733dd5286c2f1edf0d0a2b4a29532c8fb5460b344c2e9a7414959d158bd6.
// Owning greenhouse_solar.h sha256 dd0557760ac17fd61eca8a0449d0060f8452d418616edda2ab95892408a2fb0e.
// Explicit fmaf operands mirror madd.s/msub.s at compute_solar_times,
// solar_phase helper and band_value_at_phase: actual operands/literal words match
// across the two ELFs; addresses move. Compile contraction off.
// Host libm differential is not a target execution or native capture receipt.
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
    const float cg=std::cos(g), sg=std::sin(g), c2g=std::cos(2.f*g), s2g=std::sin(2.f*g);
    float eq=fmaf(.001868f,cg,.000075f);
    eq=fmaf(-.032077f,sg,eq); eq=fmaf(-.014615f,c2g,eq); eq=fmaf(-.040849f,s2g,eq);
    float decl=fmaf(-.399912f,cg,.006918f);
    decl=fmaf(.070257f,sg,decl); decl=fmaf(-.006758f,c2g,decl); decl=fmaf(.000907f,s2g,decl);
    decl=fmaf(-.002697f,std::cos(3.f*g),decl); decl=fmaf(.001480f,std::sin(3.f*g),decl);
    const float lat_rad = lat_deg * PI_F / 180.0f;
    // Sunrise/sunset zenith 90.833° (refraction + solar disc radius).
    const float zenith = 90.833f * PI_F / 180.0f;
    float cos_ha = fmaf(-std::sin(lat_rad),std::sin(decl),-0.014538058079779148f)
                 / (std::cos(lat_rad) * std::cos(decl));
    cos_ha = std::max(-1.0f, std::min(1.0f, cos_ha));  // polar clamp
    const float ha_deg = std::acos(cos_ha) * 180.0f / PI_F;

    const float noon_utc    = fmaf(-eq,229.18f,fmaf(-lon_deg,4.f,720.f));
    const float sunrise_utc = fmaf(-ha_deg,4.f,noon_utc);
    const float sunset_utc  = fmaf(ha_deg,4.f,noon_utc);

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
    float m = float(now_minute);
    if (m < float(sr)) m += 1440.0f;

    // C1-smooth phase: piecewise cubic Hermite through the 4 solar anchors
    // (SR=0, SM=1, SS=2, midnight=3), each anchor's tangent = the mean of its
    // two adjacent segment rates. The old piecewise-LINEAR phase ran a
    // different rate for the day half (SR->SS over ~15 h) and the night half
    // (SS->SR over ~9 h), so the band slope JUMPED ~1.6x at sunrise & sunset —
    // a visible corner on the high-amplitude temp band (invisible on VPD).
    // Hermite makes dphase/dt continuous, so the band is smooth in time. Stays
    // monotone for every day/night ratio this site sees (tangent/secant in
    // ~[0.8,1.33], inside the [0,3] bound) and passes EXACTLY through 0/1/2/3.
    const float fsr = float(sr), fsm = float(sm), fss = float(ss), fsmid = float(smid), fnsr = float(next_sr);
    const float r0 = 1.0f / fmaxf(fsm - fsr, 1e-6f);
    const float r1 = 1.0f / fmaxf(fss - fsm, 1e-6f);
    const float r2 = 1.0f / fmaxf(fsmid - fss, 1e-6f);
    const float r3 = 1.0f / fmaxf(fnsr - fsmid, 1e-6f);
    const float d_sr = 0.5f * (r3 + r0), d_sm = 0.5f * (r0 + r1);
    const float d_ss = 0.5f * (r1 + r2), d_mid = 0.5f * (r2 + r3);
    auto herm = [](float v, float a, float b, float pa, float da, float db) -> float {
        if (b <= a) return pa;
        const float L = b - a, u = (v - a) / L, u2 = u * u, u3 = u2 * u;
        return fmaf(db*L,u3-u2,fmaf(da*L,u+fmaf(-u2,2.f,u3),pa+fmaf(-u3,2.f,3.f*u2)));
    };
    float p;
    if (m <= fsm)        p = herm(m, fsr, fsm, 0.0f, d_sr, d_sm);
    else if (m <= fss)   p = herm(m, fsm, fss, 1.0f, d_sm, d_ss);
    else if (m <= fsmid) p = herm(m, fss, fsmid, 2.0f, d_ss, d_mid);
    else                 p = herm(m, fsmid, fnsr, 3.0f, d_mid, d_sr);
    if (p < 0.0f) p = 0.0f;
    return p >= 4.0f ? 0.0f : p;
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
    phase = fmaf(-4.f,std::floor(phase / 4.0f),phase);  // wrap to [0,4)
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
    return fmaf(c2,std::cos(2.f*theta),fmaf(s1,std::sin(theta),fmaf(a.sr+a.sm+a.ss+a.mid,.25f,c1*std::cos(theta))));
}
