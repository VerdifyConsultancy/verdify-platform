"""Qualification regime classifier for the controlled planner experiment.

Audit §8.3 (docs/research/planner-efficacy-current-firmware-2026-08-14.md)
defines four mutually exclusive weather regimes for the step-test
qualification. Every analyzed transition must be claimed while current
outdoor conditions match its cell's regime:

- ``night``:            solar < 20 W/m²
- ``hot_bright_dry``:   solar >= 400 W/m², outdoor temp >= 80°F,
                        outdoor humidity ratio <= 0.012 kg/kg
- ``hot_bright_humid``: solar >= 400 W/m², outdoor temp >= 80°F,
                        outdoor humidity ratio > 0.012 kg/kg
- ``other_daylight``:   everything else (solar >= 20 W/m² but not hot/bright,
                        i.e. solar < 400 W/m² or outdoor temp < 80°F)

The four regimes partition the input space exactly: for any finite
(solar, temp, humidity ratio) exactly one regime matches.

Also fixed here: the six-edge directed content-changing template graph and
the canonical 24-cell (edge x regime) index used by
``qualification_transition_slots.cell_index`` (0..23). The ordering below is
part of the locked qualification specification
(research/planner-efficacy/qualification/qualification-spec-v1.template.yaml)
and must never be reordered once a specification instance is hashed.

Psychrometrics: saturation vapor pressure uses the same Tetens constants as
the repo's only other implementation (``ingestor/tasks/forecast.py``
``_outdoor_vpd_kpa``: 0.6108 * exp(17.27*Tc / (Tc + 237.3))). That helper is
private to the forecast task and computes VPD, not humidity ratio, so the
shared formula lives here (verdify_schemas is the base layer — ingestor
imports it, never the reverse). The humidity ratio uses the standard
ASHRAE relation W = 0.62198 * pv / (P - pv).

Pure functions only: no I/O, no database, no clock.
"""

from __future__ import annotations

import math
from enum import StrEnum

__all__ = [
    "DEFAULT_STATION_PRESSURE_KPA",
    "EDGES",
    "HOT_BRIGHT_SOLAR_MIN_W_M2",
    "HOT_BRIGHT_TEMP_MIN_F",
    "HUMID_RATIO_SPLIT_KG_KG",
    "NIGHT_SOLAR_MAX_W_M2",
    "REGIMES",
    "Regime",
    "TemplateKind",
    "cell_index",
    "classify_regime",
    "edge_for_cell",
    "humidity_ratio_kg_per_kg",
    "maybe_classify_regime",
    "regime_for_cell",
    "saturation_vapor_pressure_kpa",
]

# --- §8.3 regime thresholds (frozen; also encoded in the qualification spec) --

NIGHT_SOLAR_MAX_W_M2 = 20.0  # night: solar < 20 (strict)
HOT_BRIGHT_SOLAR_MIN_W_M2 = 400.0  # hot/bright: solar >= 400 (inclusive)
HOT_BRIGHT_TEMP_MIN_F = 80.0  # hot/bright: outdoor temp >= 80°F (inclusive)
HUMID_RATIO_SPLIT_KG_KG = 0.012  # dry: W <= 0.012; humid: W > 0.012

# Site fallback when no station pressure sample is available. 84.0 kPa
# (840 hPa) is the platform's existing elevation-corrected site constant:
# tunable_registry `site_pressure_hpa` default 840.0, and
# db compute_enthalpy() COALESCE(pressure_hpa, 840). (Firmware's
# GH_STATION_PRESSURE_KPA is 85.0 — a known 1-kPa repo discrepancy; the
# qualification specification instance locks the exact value it uses,
# marked TO-LOCK there.) Callers should COALESCE measured station pressure
# over this constant.
DEFAULT_STATION_PRESSURE_KPA = 84.0


class Regime(StrEnum):
    """The four mutually exclusive §8.3 qualification regimes."""

    NIGHT = "night"
    HOT_BRIGHT_DRY = "hot_bright_dry"
    HOT_BRIGHT_HUMID = "hot_bright_humid"
    OTHER_DAYLIGHT = "other_daylight"


class TemplateKind(StrEnum):
    """The three locked policy template kinds (migration 207)."""

    BASELINE = "baseline"
    MODERATE = "moderate"
    AGGRESSIVE = "aggressive"


# Canonical regime ordering for cell indexing (locked; never reorder).
REGIMES: tuple[Regime, ...] = (
    Regime.NIGHT,
    Regime.HOT_BRIGHT_DRY,
    Regime.HOT_BRIGHT_HUMID,
    Regime.OTHER_DAYLIGHT,
)

# The six directed content-changing edges among the three locked vectors
# (§8.3: baseline<->moderate, baseline<->aggressive, moderate<->aggressive).
# Canonical ordering for cell indexing (locked; never reorder).
EDGES: tuple[tuple[TemplateKind, TemplateKind], ...] = (
    (TemplateKind.BASELINE, TemplateKind.MODERATE),
    (TemplateKind.MODERATE, TemplateKind.BASELINE),
    (TemplateKind.BASELINE, TemplateKind.AGGRESSIVE),
    (TemplateKind.AGGRESSIVE, TemplateKind.BASELINE),
    (TemplateKind.MODERATE, TemplateKind.AGGRESSIVE),
    (TemplateKind.AGGRESSIVE, TemplateKind.MODERATE),
)


def saturation_vapor_pressure_kpa(temp_f: float) -> float:
    """Tetens saturation vapor pressure over water, in kPa.

    Byte-identical constants to ``ingestor/tasks/forecast.py``
    ``_outdoor_vpd_kpa`` (0.6108, 17.27, 237.3), taking °F input because
    every outdoor temperature in the platform is stored in °F.
    """
    temp_c = (float(temp_f) - 32.0) * 5.0 / 9.0
    return 0.6108 * math.exp((17.27 * temp_c) / (temp_c + 237.3))


def humidity_ratio_kg_per_kg(
    temp_f: float,
    rh_pct: float,
    pressure_kpa: float,
) -> float:
    """Humidity ratio W (kg water vapor per kg dry air).

    ``W = 0.62198 * pv / (P - pv)`` with ``pv = (RH/100) * psat(T)``.
    Raises ``ValueError`` on non-finite inputs, RH outside [0, 100], or a
    pressure at or below the vapor pressure (physically impossible station
    sample — never classify from it).
    """
    temp_f = float(temp_f)
    rh_pct = float(rh_pct)
    pressure_kpa = float(pressure_kpa)
    if not (math.isfinite(temp_f) and math.isfinite(rh_pct) and math.isfinite(pressure_kpa)):
        raise ValueError("humidity ratio requires finite temp/RH/pressure")
    if not 0.0 <= rh_pct <= 100.0:
        raise ValueError(f"RH {rh_pct} outside [0, 100]")
    if pressure_kpa <= 0.0:
        raise ValueError(f"non-physical station pressure {pressure_kpa} kPa")
    vapor_kpa = saturation_vapor_pressure_kpa(temp_f) * rh_pct / 100.0
    dry_kpa = pressure_kpa - vapor_kpa
    if dry_kpa <= 0.0:
        raise ValueError(f"vapor pressure {vapor_kpa:.4f} kPa >= station pressure {pressure_kpa:.4f} kPa")
    return 0.62198 * vapor_kpa / dry_kpa


def classify_regime(
    solar_w_m2: float,
    outdoor_temp_f: float,
    outdoor_rh_pct: float,
    pressure_kpa: float,
) -> Regime:
    """Classify current outdoor conditions into exactly one §8.3 regime.

    Boundary semantics are the audit's, verbatim:
    ``solar < 20`` night (strict); ``solar >= 400`` and ``temp >= 80``
    hot/bright (inclusive); humidity ratio ``<= 0.012`` dry, ``> 0.012``
    humid. Raises ``ValueError`` on non-finite/invalid inputs — eligibility
    must treat that as "no regime", never guess.
    """
    solar = float(solar_w_m2)
    if not math.isfinite(solar) or solar < 0.0:
        raise ValueError(f"invalid solar {solar_w_m2} W/m²")
    if solar < NIGHT_SOLAR_MAX_W_M2:
        return Regime.NIGHT
    temp_f = float(outdoor_temp_f)
    if not math.isfinite(temp_f):
        raise ValueError(f"invalid outdoor temperature {outdoor_temp_f} °F")
    if solar >= HOT_BRIGHT_SOLAR_MIN_W_M2 and temp_f >= HOT_BRIGHT_TEMP_MIN_F:
        ratio = humidity_ratio_kg_per_kg(temp_f, outdoor_rh_pct, pressure_kpa)
        if ratio <= HUMID_RATIO_SPLIT_KG_KG:
            return Regime.HOT_BRIGHT_DRY
        return Regime.HOT_BRIGHT_HUMID
    return Regime.OTHER_DAYLIGHT


def maybe_classify_regime(
    solar_w_m2: float | None,
    outdoor_temp_f: float | None,
    outdoor_rh_pct: float | None,
    pressure_kpa: float | None,
) -> Regime | None:
    """``classify_regime`` tolerant of missing/invalid inputs (returns None).

    The scheduler uses this: a missing or non-finite input means the
    conditions cannot be proven to match any cell, so the eligibility
    predicate is simply false. Humidity inputs are only required when the
    solar/temperature gate would reach the dry/humid split.
    """
    if solar_w_m2 is None:
        return None
    solar = float(solar_w_m2)
    if not math.isfinite(solar) or solar < 0.0:
        return None
    if solar < NIGHT_SOLAR_MAX_W_M2:
        return Regime.NIGHT
    if outdoor_temp_f is None or not math.isfinite(float(outdoor_temp_f)):
        return None
    if solar >= HOT_BRIGHT_SOLAR_MIN_W_M2 and float(outdoor_temp_f) >= HOT_BRIGHT_TEMP_MIN_F:
        if outdoor_rh_pct is None or pressure_kpa is None:
            return None
        try:
            return classify_regime(solar, float(outdoor_temp_f), float(outdoor_rh_pct), float(pressure_kpa))
        except ValueError:
            return None
    return Regime.OTHER_DAYLIGHT


# --- 24-cell (edge x regime) canonical index --------------------------------


def cell_index(edge: tuple[TemplateKind, TemplateKind], regime: Regime) -> int:
    """Canonical ``qualification_transition_slots.cell_index`` (0..23).

    ``cell = edge_index * 4 + regime_index`` over the locked orderings above.
    """
    try:
        edge_idx = EDGES.index((TemplateKind(edge[0]), TemplateKind(edge[1])))
    except ValueError as exc:
        raise ValueError(f"unknown directed edge {edge!r}") from exc
    regime_idx = REGIMES.index(Regime(regime))
    return edge_idx * len(REGIMES) + regime_idx


def edge_for_cell(cell: int) -> tuple[TemplateKind, TemplateKind]:
    """Directed edge for a canonical cell index (0..23)."""
    if not 0 <= int(cell) <= 23:
        raise ValueError(f"cell index {cell} outside 0..23")
    return EDGES[int(cell) // len(REGIMES)]


def regime_for_cell(cell: int) -> Regime:
    """Regime for a canonical cell index (0..23)."""
    if not 0 <= int(cell) <= 23:
        raise ValueError(f"cell index {cell} outside 0..23")
    return REGIMES[int(cell) % len(REGIMES)]
