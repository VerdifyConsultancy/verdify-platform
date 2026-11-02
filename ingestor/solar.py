"""solar.py — NOAA solar ephemeris + deterministic band-curve engine.

Server-side mirror of the firmware-v2 on-chip solar engine
(docs/design/firmware-v2-contract-2026-06-10.md §B1/§B2). The dispatcher uses
this to (a) compute the ephemeris for audit/compliance scoring and (b)
reproduce the exact band the ESP32 computes from its NVS-persisted anchors —
same anchors + same ephemeris + same cosine interpolation.

The ephemeris itself (the site constants, `SolarTimes` and
`compute_solar_times`) lives in `verdify_schemas/solar.py` (VD-37, #1152), so
the api and MCP images compute the irrigation agenda's solar starts with the
same math; this module re-exports it unchanged, so `from solar import ...`
keeps working. The band-curve engine (`BandAnchors`, `solar_phase`,
`band_value_at_phase`) stays here.

Pure stdlib + verdify_schemas on purpose: `astral` is NOT in
ingestor/requirements.txt (it only exists in the container image closure for
the planner path), and the firmware implements the NOAA approximation directly,
so a dependency-free port keeps one replay-testable implementation on both
sides of the wire.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from verdify_schemas.solar import (
    _ZENITH_DEG,
    GREENHOUSE_LAT_DEG,
    GREENHOUSE_LON_DEG,
    SolarTimes,
    _days_in_year,
    compute_solar_times,
)

__all__ = [
    "GREENHOUSE_LAT_DEG",
    "GREENHOUSE_LON_DEG",
    "_ZENITH_DEG",
    "BandAnchors",
    "SolarTimes",
    "_days_in_year",
    "band_value_at_phase",
    "compute_solar_times",
    "solar_phase",
]


@dataclass(frozen=True)
class BandAnchors:
    """Band value at sunrise, solar noon, sunset, solar midnight (§B2)."""

    sr: float
    sm: float
    ss: float
    mid: float


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
