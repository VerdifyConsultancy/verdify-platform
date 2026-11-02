"""solar.py — NOAA sunrise, solar noon and sunset for the greenhouse (VD-37, #1152).

The single editable source of the Longmont site constants and of the NOAA
ephemeris that the platform shares with the firmware's on-chip solar code
(``firmware/lib/greenhouse_solar.h``; docs/design/firmware-v2-contract-2026-06-10.md
§B1). It moved here verbatim from ``ingestor/solar.py``, which re-exports it and
keeps the band-curve engine (``BandAnchors``, ``solar_phase``,
``band_value_at_phase``), so the api and MCP images, which carry
``verdify_schemas`` but not ``ingestor/``, compute the irrigation agenda's solar
starts with the same math. ``scripts/check-solar-constants.py`` holds every
mirrored surface to the constants below.

Pure stdlib on purpose: the firmware implements the NOAA approximation directly,
so a dependency-free port keeps one replay-testable implementation on both sides
of the wire.

Equations: NOAA solar-position approximation (General Solar Position
Calculations, NOAA Global Monitoring Division). Accuracy is well within the
±5 min contract tolerance at mid-latitudes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = [
    "GREENHOUSE_LAT_DEG",
    "GREENHOUSE_LON_DEG",
    "SolarTimes",
    "compute_solar_times",
]

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
