"""solar.py — NOAA solar ephemeris + deterministic band-curve engine.

Server-side mirror of the firmware-v2 on-chip solar engine
(docs/design/firmware-v2-contract-2026-06-10.md §B1/§B2). The dispatcher uses
this to (a) compute the ephemeris for audit/compliance scoring and (b)
reproduce the exact band the ESP32 computes from its NVS-persisted anchors —
same anchors + same ephemeris + same cosine interpolation.

Pure stdlib on purpose: `astral` is NOT in ingestor/requirements.txt (it only
exists in the container image closure for the planner path), and the firmware
implements the NOAA approximation directly, so a dependency-free port keeps
one replay-testable implementation on both sides of the wire.

Equations: NOAA solar-position approximation (General Solar Position
Calculations, NOAA Global Monitoring Division). Accuracy is well within the
±5 min contract tolerance at mid-latitudes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Greenhouse constants (contract §B1): Longmont, CO.
GREENHOUSE_LAT_DEG = 40.167
GREENHOUSE_LON_DEG = -105.102

# Official sunrise/sunset zenith (solar elevation -0.833° → refraction + disc).
_ZENITH_DEG = 90.833


@dataclass(frozen=True)
class SolarTimes:
    """Sunrise / solar-noon / sunset as minutes after local midnight."""

    sunrise_min: int
    solar_noon_min: int
    sunset_min: int


@dataclass(frozen=True)
class BandAnchors:
    """Band value at sunrise, solar noon, sunset, solar midnight (§B2)."""

    sr: float
    sm: float
    ss: float
    mid: float


def _days_in_year(year: int) -> int:
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    return 366 if leap else 365


def compute_solar_times(
    day_of_year: int,
    year: int,
    lat_deg: float = GREENHOUSE_LAT_DEG,
    lon_deg: float = GREENHOUSE_LON_DEG,
    utc_offset_min: int = -360,
) -> SolarTimes:
    """NOAA sunrise / solar-noon / sunset, minutes after local midnight.

    `lon_deg` is positive east (Longmont is negative). `utc_offset_min` is the
    local UTC offset in minutes (MDT=-360, MST=-420) — pass the *current*
    offset so the result is DST-correct by construction (§B1).
    """
    # Fractional year (radians), evaluated at local midday like the firmware.
    gamma = 2.0 * math.pi / _days_in_year(year) * (day_of_year - 1 + 0.5)

    # Equation of time (minutes) and solar declination (radians).
    eqtime = 229.18 * (
        0.000075
        + 0.001868 * math.cos(gamma)
        - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2.0 * gamma)
        - 0.040849 * math.sin(2.0 * gamma)
    )
    decl = (
        0.006918
        - 0.399912 * math.cos(gamma)
        + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2.0 * gamma)
        + 0.000907 * math.sin(2.0 * gamma)
        - 0.002697 * math.cos(3.0 * gamma)
        + 0.00148 * math.sin(3.0 * gamma)
    )

    lat = math.radians(lat_deg)
    cos_ha = math.cos(math.radians(_ZENITH_DEG)) / (math.cos(lat) * math.cos(decl)) - math.tan(lat) * math.tan(decl)
    # Clamp for polar day/night robustness (never triggered at 40°N).
    cos_ha = max(-1.0, min(1.0, cos_ha))
    ha_deg = math.degrees(math.acos(cos_ha))

    sunrise_utc = 720.0 - 4.0 * (lon_deg + ha_deg) - eqtime
    sunset_utc = 720.0 - 4.0 * (lon_deg - ha_deg) - eqtime
    noon_utc = 720.0 - 4.0 * lon_deg - eqtime

    return SolarTimes(
        sunrise_min=round(sunrise_utc + utc_offset_min),
        solar_noon_min=round(noon_utc + utc_offset_min),
        sunset_min=round(sunset_utc + utc_offset_min),
    )


def solar_phase(now_minute: float, st: SolarTimes) -> float:
    """Map a local-minute-of-day onto the [0,4) solar phase (§B1).

    Day half SR→SM→SS spans [0,2]; night half SS→midnight→nextSR spans [2,4),
    where solar-midnight ≈ midpoint(SS, SR+24h). The next-day sunrise is
    approximated with today's (drift is ~1–2 min/day — irrelevant at band
    granularity, and identical to the on-chip approximation).
    """
    sr = float(st.sunrise_min)
    sm = float(st.solar_noon_min)
    ss = float(st.sunset_min)
    next_sr = sr + 1440.0
    smid = ss + (next_sr - ss) / 2.0  # solar midnight = midpoint(SS, next SR)

    m = float(now_minute) % 1440.0
    if m < sr:
        m += 1440.0  # pre-dawn → continuation of the night half

    # C1-smooth phase: piecewise cubic Hermite through the 4 solar anchors
    # (SR=0, SM=1, SS=2, midnight=3), each anchor's tangent = the mean of its
    # two adjacent segment rates. The old piecewise-LINEAR phase ran a different
    # rate for the day half (SR→SS ~15 h) vs night half (SS→SR ~9 h), so the band
    # slope jumped ~1.6× at sunrise & sunset (a visible corner on the high-
    # amplitude temp band). Hermite makes dphase/dt continuous → smooth band.
    # MUST stay identical to firmware greenhouse_solar.h::solar_phase and DB
    # fn_solar_phase (the end-to-end band alignment guard).
    r0 = 1.0 / max(sm - sr, 1e-6)
    r1 = 1.0 / max(ss - sm, 1e-6)
    r2 = 1.0 / max(smid - ss, 1e-6)
    r3 = 1.0 / max(next_sr - smid, 1e-6)
    d_sr = 0.5 * (r3 + r0)
    d_sm = 0.5 * (r0 + r1)
    d_ss = 0.5 * (r1 + r2)
    d_mid = 0.5 * (r2 + r3)

    def _herm(v: float, a: float, b: float, pa: float, da: float, db: float) -> float:
        if b <= a:
            return pa
        length = b - a
        u = (v - a) / length
        u2 = u * u
        u3 = u2 * u
        return pa + (3.0 * u2 - 2.0 * u3) + da * length * (u3 - 2.0 * u2 + u) + db * length * (u3 - u2)

    if m <= sm:
        phase = _herm(m, sr, sm, 0.0, d_sr, d_sm)
    elif m <= ss:
        phase = _herm(m, sm, ss, 1.0, d_sm, d_ss)
    elif m <= smid:
        phase = _herm(m, ss, smid, 2.0, d_ss, d_mid)
    else:
        phase = _herm(m, smid, next_sr, 3.0, d_mid, d_sr)
    return min(max(phase, 0.0), 3.9999999)


def band_value_at_phase(anchors: BandAnchors, phase: float) -> float:
    """Smooth 4-anchor harmonic (Fourier) interpolation through SR/SM/SS/MID.

    Anchors sit at solar phase 0/1/2/3 (SR → SM → SS → MID). The curve passes
    EXACTLY through every anchor but is C-infinity smooth — unlike the old
    piecewise cosine-ease, which had zero slope at every anchor and flattened
    into four visible plateaus ("lumpy" bands). One curve, not four stitched
    eases. Mirrors the on-chip greenhouse_solar.h band_value_at_phase and db
    fn_crop_band_value (migration 170) exactly.
    """
    p = float(phase) % 4.0
    theta = math.pi * p / 2.0  # 0..2π
    c0 = (anchors.sr + anchors.sm + anchors.ss + anchors.mid) / 4.0
    c1 = (anchors.sr - anchors.ss) / 2.0
    s1 = (anchors.sm - anchors.mid) / 2.0
    c2 = (anchors.sr - anchors.sm + anchors.ss - anchors.mid) / 4.0
    return c0 + c1 * math.cos(theta) + s1 * math.sin(theta) + c2 * math.cos(2.0 * theta)
