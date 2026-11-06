"""The band profile: the house band in the operator's terms, compiled to the 24 house anchors (#1334, Option A).

The house band is one four-anchor harmonic per series over ``crop_band_anchors`` (sunrise ``sr``, solar noon ``sm``,
sunset ``ss``, solar midnight ``mid``), evaluated identically on the controller (``greenhouse_solar.h``), in Python
(``ingestor/solar.py``) and in SQL (``fn_crop_band_value``). The editor does not change that curve; it changes how
the anchors are chosen:

* ``BandProfile`` holds a day value (solar noon) and a night value (solar midnight) for temperature and VPD, the
  temperature half-width (±°F, day and night) and the VPD range below and above the target (day and night).
* ``compile_profile`` turns it into the 24 ``band_<series>_<anchor>`` values. Sunrise and sunset are the midpoint of
  noon and midnight, so ``c1 = c2 = 0`` and each curve is a pure sinusoid bounded exactly by its noon and midnight
  anchors: no overshoot by construction (docs/research/w5/vpd-band.md §H).
* ``decompose`` reads a profile back from the anchors in force; ``is_simple`` says whether those anchors are what a
  profile compiles to (Advanced, raw anchors, otherwise).
* ``findings`` checks a full anchor set: ``error`` findings refuse a Save, ``warning`` findings are shown only.

Guardrails (decisions.md, 2026-11-05): the hard temperature rails are 40 °F and 100 °F, and the controller's
``validate_setpoints`` keeps them at least 5 °F outside the band, so the band itself stays within 45-95 °F. The VPD
rails are 0.3 and 3.0 kPa. The night VPD floor stays at least 0.74 kPa (the nightly dry-out; Jason, #1365).

Pure: no database, clock or file reads.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from verdify_schemas.band_rows import ANCHOR_KEYS, HOUSE_SERIES

# ── guardrails and display rules ────────────────────────────────────────────
GUARDRAIL_TEMP_MIN_F = 40.0
GUARDRAIL_TEMP_MAX_F = 100.0
# firmware/lib/greenhouse_types.h validate_setpoints: safety_min <= temp_low - 5 and safety_max >= temp_high + 5.
SAFETY_MARGIN_F = 5.0
GUARDRAIL_VPD_MIN_KPA = 0.3
GUARDRAIL_VPD_MAX_KPA = 3.0
# Jason, 2026-11-05 (#1365): the night floor stays at least 0.74 kPa so the house dries out overnight. Compared at
# the two decimals he stated, so the stored 0.738 is the 0.74 floor.
NIGHT_VPD_FLOOR_MIN_KPA = 0.74
# The dispatcher's push quantum (tunable_registry._fw2_push_step): 0.1 °F and 0.01 kPa.
STEP: Mapping[str, float] = {"temp": 0.1, "vpd": 0.01}
# Advanced anchors that leave the anchor range by more than this get a warning (research §4).
OVERSHOOT_WARN: Mapping[str, float] = {"temp": 0.5, "vpd": 0.03}
# 0.01 of a phase is about 7 minutes of day: fine enough for extremes to 0.01 °F on these curves.
_SAMPLES = 400

Axis = Literal["temp", "vpd"]
Severity = Literal["error", "warning"]


def harmonic(sr: float, sm: float, ss: float, mid: float, phase: float) -> float:
    """``fn_crop_band_value``'s four-anchor harmonic; ``ingestor/solar.py`` and the firmware run the same."""
    theta = math.pi * phase / 2.0
    c0 = (sr + sm + ss + mid) / 4.0
    c1 = (sr - ss) / 2.0
    s1 = (sm - mid) / 2.0
    c2 = (sr - sm + ss - mid) / 4.0
    return c0 + c1 * math.cos(theta) + s1 * math.sin(theta) + c2 * math.cos(2.0 * theta)


def night_bias(phase: float, bias_kpa: float) -> float:
    """What the controller adds to VPD low, target and high for ``night_vpd_bias_kpa`` (controls.yaml): a sin² bump,
    0 at sunset (phase 2) and sunrise (phase 4), the full bias at solar midnight (phase 3)."""
    if bias_kpa <= 0.0 or not 2.0 <= phase <= 4.0:
        return 0.0
    s = math.sin(math.pi * (phase - 2.0) * 0.5)
    return bias_kpa * s * s


def param(series: str, anchor: str) -> str:
    return f"band_{series}_{anchor}"


HOUSE_PARAMS: tuple[str, ...] = tuple(param(series, anchor) for series in HOUSE_SERIES for anchor in ANCHOR_KEYS)


def axis_of(series_or_param: str) -> Axis:
    return "temp" if "temp_" in series_or_param else "vpd"


def snap(value: float, axis: Axis) -> float:
    """``value`` on the push quantum, half to even (the settings service's snap)."""
    step = STEP[axis]
    return round(round(value / step) * step, 6)


class BandProfile(BaseModel):
    """The simple band: day (solar noon) and night (solar midnight) values, °F and kPa."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    temp_day: float = Field(description="Temperature target at solar noon, °F", allow_inf_nan=False)
    temp_night: float = Field(description="Temperature target at solar midnight, °F", allow_inf_nan=False)
    temp_width_day: float = Field(ge=0, description="± °F around the day target", allow_inf_nan=False)
    temp_width_night: float = Field(ge=0, description="± °F around the night target", allow_inf_nan=False)
    vpd_day: float = Field(description="VPD target at solar noon, kPa", allow_inf_nan=False)
    vpd_night: float = Field(description="VPD target at solar midnight, kPa", allow_inf_nan=False)
    vpd_below_day: float = Field(ge=0, description="kPa below the day target (the day floor)", allow_inf_nan=False)
    vpd_above_day: float = Field(ge=0, description="kPa above the day target (the day ceiling)", allow_inf_nan=False)
    vpd_below_night: float = Field(
        ge=0, description="kPa below the night target (the night floor)", allow_inf_nan=False
    )
    vpd_above_night: float = Field(ge=0, description="kPa above the night target", allow_inf_nan=False)


PROFILE_FIELDS: tuple[str, ...] = tuple(BandProfile.model_fields)


def _noon_and_midnight(profile: BandProfile) -> dict[str, tuple[float, float]]:
    """{series: (solar noon, solar midnight)}."""
    p = profile
    return {
        "temp_low": (p.temp_day - p.temp_width_day, p.temp_night - p.temp_width_night),
        "temp_target": (p.temp_day, p.temp_night),
        "temp_high": (p.temp_day + p.temp_width_day, p.temp_night + p.temp_width_night),
        "vpd_low": (p.vpd_day - p.vpd_below_day, p.vpd_night - p.vpd_below_night),
        "vpd_target": (p.vpd_day, p.vpd_night),
        "vpd_high": (p.vpd_day + p.vpd_above_day, p.vpd_night + p.vpd_above_night),
    }


def compile_profile(profile: BandProfile) -> dict[str, float]:
    """The 24 house anchors: noon and midnight from the profile, sunrise and sunset their midpoint, each on the push
    quantum. Shoulders are computed from the snapped noon and midnight, so they stay their midpoint after snapping
    (within half a step)."""
    out: dict[str, float] = {}
    for series, (noon, midnight) in _noon_and_midnight(profile).items():
        axis = axis_of(series)
        sm, mid = snap(noon, axis), snap(midnight, axis)
        shoulder = snap((sm + mid) / 2.0, axis)
        out.update({param(series, "sr"): shoulder, param(series, "sm"): sm, param(series, "ss"): shoulder})
        out[param(series, "mid")] = mid
    return {name: out[name] for name in HOUSE_PARAMS}


def decompose(anchors: Mapping[str, float]) -> BandProfile:
    """The profile the anchors' noon and midnight values describe (their shoulders are ignored). An asymmetric
    temperature band reads as its target and its half-width."""
    a = {name: float(anchors[name]) for name in HOUSE_PARAMS}

    def at(series: str, anchor: str) -> float:
        return a[param(series, anchor)]

    def half(anchor: str) -> float:
        return snap(max(0.0, (at("temp_high", anchor) - at("temp_low", anchor)) / 2.0), "temp")

    def gap(lower: str, upper: str, anchor: str) -> float:
        return round(max(0.0, at(upper, anchor) - at(lower, anchor)), 6)

    return BandProfile(
        temp_day=at("temp_target", "sm"),
        temp_night=at("temp_target", "mid"),
        temp_width_day=half("sm"),
        temp_width_night=half("mid"),
        vpd_day=at("vpd_target", "sm"),
        vpd_night=at("vpd_target", "mid"),
        vpd_below_day=gap("vpd_low", "vpd_target", "sm"),
        vpd_above_day=gap("vpd_target", "vpd_high", "sm"),
        vpd_below_night=gap("vpd_low", "vpd_target", "mid"),
        vpd_above_night=gap("vpd_target", "vpd_high", "mid"),
    )


def unchanged(old: float, new: float, axis: Axis) -> bool:
    """Within half a push quantum: the same value on the device."""
    return abs(old - new) <= STEP[axis] / 2.0 + 1e-9


def is_simple(anchors: Mapping[str, float]) -> bool:
    """Do the anchors equal what their own profile compiles to (midpoint shoulders, a symmetric temperature band)?"""
    compiled = compile_profile(decompose(anchors))
    return all(unchanged(float(anchors[name]), compiled[name], axis_of(name)) for name in HOUSE_PARAMS)


def series_points(anchors: Mapping[str, float], series: str) -> tuple[float, float, float, float]:
    sr, sm, ss, mid = (float(anchors[param(series, anchor)]) for anchor in ANCHOR_KEYS)
    return sr, sm, ss, mid


def curve_extremes(points: tuple[float, float, float, float]) -> tuple[float, float]:
    """The curve's min and max over a whole solar day."""
    values = [harmonic(*points, 4.0 * index / _SAMPLES) for index in range(_SAMPLES)]
    return min(values), max(values)


def overshoot(anchors: Mapping[str, float]) -> dict[str, float]:
    """{series: how far its curve leaves its own anchor range}, 0 for a profile-compiled band."""
    out: dict[str, float] = {}
    for series in HOUSE_SERIES:
        points = series_points(anchors, series)
        low, high = curve_extremes(points)
        out[series] = round(max(0.0, min(points) - low, high - max(points)), 4)
    return out


@dataclass(frozen=True)
class Finding:
    code: str
    severity: Severity
    field: str
    detail: str


def _fmt(value: float, axis: Axis) -> str:
    return f"{value:.1f} °F" if axis == "temp" else f"{value:.2f} kPa"


def findings(anchors: Mapping[str, float]) -> list[Finding]:
    """Errors refuse a Save; warnings are shown. Order: ordering, guardrails, the night floor, overshoot."""
    out: list[Finding] = []
    for lower, upper in (("temp_low", "temp_target"), ("temp_target", "temp_high")) + (
        ("vpd_low", "vpd_target"),
        ("vpd_target", "vpd_high"),
    ):
        lo_points, hi_points = series_points(anchors, lower), series_points(anchors, upper)
        gap = tuple(h - lo for h, lo in zip(hi_points, lo_points, strict=True))
        if curve_extremes(gap)[0] < -1e-9:  # type: ignore[arg-type]
            out.append(
                Finding("band_order", "error", param(upper, "sm"), f"{upper} falls below {lower} during the day")
            )
    temp_floor = GUARDRAIL_TEMP_MIN_F + SAFETY_MARGIN_F
    temp_ceiling = GUARDRAIL_TEMP_MAX_F - SAFETY_MARGIN_F
    low_min = curve_extremes(series_points(anchors, "temp_low"))[0]
    high_max = curve_extremes(series_points(anchors, "temp_high"))[1]
    if low_min < temp_floor - 1e-9:
        out.append(
            Finding(
                "guardrail",
                "error",
                "temp_width_night",
                f"The band floor reaches {_fmt(low_min, 'temp')}; it must stay at or above {_fmt(temp_floor, 'temp')}, "
                f"{SAFETY_MARGIN_F:.0f} °F inside the {GUARDRAIL_TEMP_MIN_F:.0f} °F guardrail",
            )
        )
    if high_max > temp_ceiling + 1e-9:
        out.append(
            Finding(
                "guardrail",
                "error",
                "temp_width_day",
                f"The band ceiling reaches {_fmt(high_max, 'temp')}; it must stay at or below "
                f"{_fmt(temp_ceiling, 'temp')}, {SAFETY_MARGIN_F:.0f} °F inside the {GUARDRAIL_TEMP_MAX_F:.0f} °F guardrail",
            )
        )
    vpd_min = curve_extremes(series_points(anchors, "vpd_low"))[0]
    vpd_max = curve_extremes(series_points(anchors, "vpd_high"))[1]
    if vpd_min < GUARDRAIL_VPD_MIN_KPA - 1e-9 or vpd_max > GUARDRAIL_VPD_MAX_KPA + 1e-9:
        out.append(
            Finding(
                "guardrail",
                "error",
                "vpd_below_day" if vpd_min < GUARDRAIL_VPD_MIN_KPA else "vpd_above_day",
                f"The VPD band runs {_fmt(vpd_min, 'vpd')}-{_fmt(vpd_max, 'vpd')}; it must stay within "
                f"{_fmt(GUARDRAIL_VPD_MIN_KPA, 'vpd')}-{_fmt(GUARDRAIL_VPD_MAX_KPA, 'vpd')}",
            )
        )
    night_floor = float(anchors[param("vpd_low", "mid")])
    if round(night_floor, 2) < NIGHT_VPD_FLOOR_MIN_KPA:
        out.append(
            Finding(
                "night_floor",
                "error",
                "vpd_below_night",
                f"The night VPD floor is {_fmt(night_floor, 'vpd')}; it stays at least "
                f"{_fmt(NIGHT_VPD_FLOOR_MIN_KPA, 'vpd')} so the house dries out overnight",
            )
        )
    for series, amount in overshoot(anchors).items():
        axis = axis_of(series)
        if amount > OVERSHOOT_WARN[axis]:
            out.append(
                Finding(
                    "overshoot",
                    "warning",
                    param(series, "sr"),
                    f"{series} leaves its anchor range by {_fmt(amount, axis)}: sunrise and sunset are not the "
                    "midpoint of noon and midnight",
                )
            )
    return out
