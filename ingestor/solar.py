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
    midnight = (ss + next_sr) / 2.0

    m = float(now_minute) % 1440.0
    if m < sr:
        m += 1440.0  # pre-dawn → continuation of the night half

    if m <= sm:
        phase = (m - sr) / max(sm - sr, 1e-9)
    elif m <= ss:
        phase = 1.0 + (m - sm) / max(ss - sm, 1e-9)
    elif m <= midnight:
        phase = 2.0 + (m - ss) / max(midnight - ss, 1e-9)
    else:
        phase = 3.0 + (m - midnight) / max(next_sr - midnight, 1e-9)
    return min(max(phase, 0.0), 3.9999999)


def band_value_at_phase(anchors: BandAnchors, phase: float) -> float:
    """Cosine interpolation between consecutive anchors (§B2).

    Segment k covers phase [k, k+1) between anchor k and anchor (k+1)%4 in
    SR → SM → SS → MID → SR order. Cosine easing keeps the curve smooth and
    flat at every anchor (zero slope), matching the on-chip engine exactly.
    """
    values = (anchors.sr, anchors.sm, anchors.ss, anchors.mid)
    p = float(phase) % 4.0
    seg = int(p)
    t = p - seg
    v0 = values[seg]
    v1 = values[(seg + 1) % 4]
    weight = 0.5 - 0.5 * math.cos(math.pi * t)
    return v0 + (v1 - v0) * weight
