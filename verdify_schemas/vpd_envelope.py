"""The achievable night VPD envelope and the achievable band (#1433; docs/research/w5/vpd-band.md §3).

The served band is aspirational: on cold or humid nights the glazing holds the indoor dewpoint up and no passive
control reaches the night VPD floor. The envelope says what the house can be expected to reach:

* ``fit_dewpoint``: indoor dewpoint is fitted as ``a + b·T_in + c·T_out + e·Td_out`` by least squares on the
  trailing ``ENVELOPE_DAYS`` days' deep-night hours (sun below ``DEEP_NIGHT_ALTITUDE_DEG``) with none of the
  ``MOISTURE_EQUIPMENT`` running. Backtested on 355 nights: median-night VPD MAE 0.10 kPa.
* ``expected_vpd``: the expected VPD at a temperature target and an outdoor temperature and dewpoint, as the
  ribbon ``es(T_target) - es(Td_in ± 0.674·RMSE)`` (p25-p75).
* ``achievable_band``: the served band widened to the ribbon: the low edge drops to the ribbon's p25 when that is
  below it, and the high edge rises to its p75 when that is above it. Inside the ribbon the served band stands.
  By day (and with no fit) the achievable band is the served band: the envelope is a night model.
* ``grade``: in, below or above a band.

The API's band read (API-06 with ``achievable``) and the band editor's preview (API-23) both use these functions;
grading against the served band and against the achievable band is the same ``grade`` on the two edges.

Pure: no database, clock or file reads.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

from verdify_schemas.experiment_regimes import saturation_vapor_pressure_kpa

ENVELOPE_DAYS = 14
ENVELOPE_MIN_HOURS = 24
DEEP_NIGHT_ALTITUDE_DEG = -6.0
# Equipment that moves indoor moisture: hours with any of it on are left out of the fit.
MOISTURE_EQUIPMENT = ("vent", "fan1", "fan2", "fog", "mister_center", "mister_south", "mister_west")
P25_Z = 0.674  # the normal quantile of p25/p75
NIGHT_PHASE = 2.0  # fn_solar_phase: 0 sunrise, 1 solar noon, 2 sunset, 3 solar midnight


def es_kpa(temp_f: float) -> float:
    return saturation_vapor_pressure_kpa(temp_f)


def dewpoint_f(vapour_kpa: float) -> float:
    """Inverse Tetens (the constants of ``saturation_vapor_pressure_kpa``)."""
    g = math.log(max(vapour_kpa, 1e-6) / 0.6108)
    return (237.3 * g / (17.27 - g)) * 9.0 / 5.0 + 32.0


def outdoor_dewpoint_f(temp_f: float, rh_pct: float) -> float:
    return dewpoint_f(rh_pct / 100.0 * es_kpa(temp_f))


def is_night(phase: float | None) -> bool:
    return phase is not None and phase >= NIGHT_PHASE


@dataclass(frozen=True)
class DewpointFit:
    coef: tuple[float, float, float, float]  # a, b (T_in), c (T_out), e (Td_out)
    rmse_f: float
    hours: int
    dewpoint_p10_f: float

    def predict(self, t_in: float, t_out: float, td_out: float) -> float:
        a, b, c, e = self.coef
        return a + b * t_in + c * t_out + e * td_out


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float] | None:
    """Gaussian elimination with partial pivoting; None when singular."""
    size = len(vector)
    m = [row[:] + [vector[index]] for index, row in enumerate(matrix)]
    for col in range(size):
        pivot = max(range(col, size), key=lambda row: abs(m[row][col]))
        if abs(m[pivot][col]) < 1e-9:
            return None
        m[col], m[pivot] = m[pivot], m[col]
        for row in range(size):
            if row != col:
                factor = m[row][col] / m[col][col]
                m[row] = [value - factor * m[col][index] for index, value in enumerate(m[row])]
    return [m[index][size] / m[index][index] for index in range(size)]


def fit_dewpoint(rows: Iterable[Mapping[str, Any]]) -> DewpointFit | None:
    """Least squares of indoor dewpoint (``td_in``) on indoor temperature (``t_in``), outdoor temperature (``t_out``)
    and outdoor dewpoint (from ``rh_out``); None with fewer than ``ENVELOPE_MIN_HOURS`` hours or a singular fit."""
    samples: list[tuple[list[float], float]] = []
    for row in rows:
        t_in, td_in, t_out, rh_out = (row[key] for key in ("t_in", "td_in", "t_out", "rh_out"))
        if None in (t_in, td_in, t_out, rh_out) or float(rh_out) <= 0:
            continue
        td_out = outdoor_dewpoint_f(float(t_out), float(rh_out))
        samples.append(([1.0, float(t_in), float(t_out), td_out], float(td_in)))
    if len(samples) < ENVELOPE_MIN_HOURS:
        return None
    xtx = [[sum(x[i] * x[j] for x, _ in samples) for j in range(4)] for i in range(4)]
    xty = [sum(x[i] * y for x, y in samples) for i in range(4)]
    coef = _solve(xtx, xty)
    if coef is None:
        return None
    residuals = [y - sum(c * v for c, v in zip(coef, x, strict=True)) for x, y in samples]
    rmse = math.sqrt(sum(r * r for r in residuals) / len(residuals))
    observed = sorted(y for _, y in samples)
    p10 = observed[max(0, math.ceil(0.1 * len(observed)) - 1)]
    return DewpointFit(tuple(coef), round(rmse, 3), len(samples), round(p10, 2))  # type: ignore[arg-type]


def expected_vpd(fit: DewpointFit, temp_f: float, t_out: float, td_out: float) -> tuple[float, float]:
    """The expected VPD ribbon (p25, p75) at temperature ``temp_f``: a wetter dewpoint gives the lower VPD."""
    td = fit.predict(temp_f, t_out, td_out)
    spread = P25_Z * fit.rmse_f
    es = es_kpa(temp_f)
    low = max(0.0, es - es_kpa(min(temp_f, td + spread)))
    high = max(0.0, es - es_kpa(min(temp_f, td - spread)))
    return round(low, 3), round(high, 3)


def achievable_band(
    low: float | None, high: float | None, expected: tuple[float, float] | None
) -> tuple[float | None, float | None]:
    """The served band widened to the expected ribbon; the served band where there is no ribbon (by day)."""
    if low is None or high is None or expected is None:
        return low, high
    p25, p75 = expected
    return round(min(low, p25), 3), round(max(high, p75), 3)


Grade = Literal["below", "in", "above"]


def grade(value: float | None, low: float | None, high: float | None) -> Grade | None:
    """Where a value sits against a band; None without a value or a band."""
    if value is None or low is None or high is None:
        return None
    return "below" if value < low else "above" if value > high else "in"


# ── the read model's wire shape (API-06 ``achievable``) ─────────────────────


class AchievableFit(BaseModel):
    hours: int = Field(description="Deep-night hours the fit used")
    rmse_f: float = Field(description="Root-mean-square dewpoint error, °F")


class AchievableNight(BaseModel):
    night: date = Field(description="America/Denver date of the night's evening")
    graded: int = Field(description="Night steps with a measured VPD and a band")
    served_in_pct: float | None = Field(description="Share of graded steps inside the served band")
    achievable_in_pct: float | None = Field(description="Share of graded steps inside the achievable band")
    below_served_pct: float | None = Field(description="Share below the served band's low edge")
    below_achievable_pct: float | None = Field(description="Share below the achievable band's low edge")
    fit: AchievableFit | None = Field(description="The rolling dewpoint fit the night used; null with too few hours")


class AchievableColumns(BaseModel):
    """The house VPD band the house can be expected to hold, per step of the timeline."""

    vpd_low: list[float | None] = Field(description="Achievable low edge: the band's, or the ribbon's p25 below it")
    vpd_high: list[float | None] = Field(description="Achievable high edge: the band's, or the ribbon's p75 above it")
    expected_low: list[float | None] = Field(description="Expected night VPD, p25; null by day or without a fit")
    expected_high: list[float | None] = Field(description="Expected night VPD, p75")
    nights: list[AchievableNight]
    days: int = Field(default=ENVELOPE_DAYS, description="Days of deep-night hours each night's fit reads")
