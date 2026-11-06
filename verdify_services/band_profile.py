"""The house band editor (#1334): API-22, the profile preview, and API-23, its one atomic Save.

API-22 reads the 24 house anchors as ``fn_crop_band_value`` resolves them, overlays a draft (simple profile fields,
which recompile their axis with midpoint shoulders, and Advanced raw anchors) and evaluates both with the harmonic
the controller runs at 10-min steps of the day's ``fn_solar_phase``, so the preview is the device's curve. It adds
what the controller adds on top (``night_vpd_bias_kpa``'s night bump), the guardrails, the checks of
``verdify_schemas.band_profile.findings``, an expected night VPD ribbon and the night heat estimate.

API-23 stores the anchors a Save changes through the settings writer, one ``desired_state`` row and its
``crop_band_anchors`` rows per anchor, under one ``activity_log`` row (``band.write``) in one transaction. The
``desired_state`` insert trigger wakes the dispatcher, whose anchor sync (``ingestor/tasks/band_anchors.py``) pushes
the changed anchors; nothing here talks to the controller. Iris may not write the band (``read_only``).

Expected night VPD (docs/research/w5/vpd-band.md §3): indoor dewpoint is fitted as ``a + b·T_in + c·T_out + e·Td_out``
on the trailing 14 days' deep-night hours (sun below -6°) with no vent, fan, fog or mister running; the ribbon is
``es(T_target) - es(Td_in)`` at ±0.674·RMSE of dewpoint (p25-p75), from the forecast's outdoor values, at night steps
only. Night heat is 0.19 kW/°F × (target - outdoor) over the forecast's night hours.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel, ConfigDict, Field

from verdify_schemas.activity import ActivityLogRow
from verdify_schemas.band_profile import (
    GUARDRAIL_TEMP_MAX_F,
    GUARDRAIL_TEMP_MIN_F,
    GUARDRAIL_VPD_MAX_KPA,
    GUARDRAIL_VPD_MIN_KPA,
    HOUSE_PARAMS,
    NIGHT_VPD_FLOOR_MIN_KPA,
    SAFETY_MARGIN_F,
    BandProfile,
    axis_of,
    compile_profile,
    decompose,
    findings,
    harmonic,
    is_simple,
    night_bias,
    overshoot,
    series_points,
    snap,
    unchanged,
)
from verdify_schemas.band_rows import ANCHOR_KEYS, HOUSE_CROP, HOUSE_SERIES
from verdify_schemas.experiment_regimes import saturation_vapor_pressure_kpa
from verdify_services import activity, series, settings
from verdify_services.activity import WriteResult
from verdify_services.actor import Actor
from verdify_services.problems import ServiceError, problem
from verdify_services.series import BandCurve

GREENHOUSE_ID = "vallery"
NIGHT_BIAS_PARAM = "night_vpd_bias_kpa"
ENVELOPE_DAYS = 14
ENVELOPE_MIN_HOURS = 24
DEEP_NIGHT_ALTITUDE_DEG = -6.0
# Equipment that moves indoor moisture: hours with any of it on are left out of the fit.
MOISTURE_EQUIPMENT = ("vent", "fan1", "fan2", "fog", "mister_center", "mister_south", "mister_west")
HEAT_LOSS_KW_PER_F = 0.19  # research §3, measured
P25_Z = 0.674  # the normal quantile of p25/p75
ACTION = "band.write"
TARGET = "house band"

_TEMP_FIELDS = ("temp_day", "temp_night", "temp_width_day", "temp_width_night")
_VPD_FIELDS = ("vpd_day", "vpd_night", "vpd_below_day", "vpd_above_day", "vpd_below_night", "vpd_above_night")

ENVELOPE_SQL = """
WITH s AS (
    SELECT equipment, ts, state, lead(ts) OVER (PARTITION BY equipment ORDER BY ts) AS nts
    FROM public.equipment_state
    WHERE equipment = ANY($2::text[]) AND ts >= $1::timestamptz - interval '15 days' AND ts < $1::timestamptz
), busy AS (
    SELECT DISTINCT gs AS b
    FROM s CROSS JOIN LATERAL generate_series(
        date_trunc('hour', s.ts),
        date_trunc('hour', LEAST(COALESCE(s.nts, $1::timestamptz), s.ts + interval '6 hours')),
        interval '1 hour') AS gs
    WHERE s.state
), h AS (
    SELECT time_bucket('1 hour', ts) AS b, avg(temp_avg) AS t_in, avg(dew_point) AS td_in,
           avg(outdoor_temp_f) AS t_out, avg(outdoor_rh_pct) AS rh_out, avg(solar_altitude_deg) AS alt
    FROM public.climate
    WHERE greenhouse_id = $3 AND ts >= $1::timestamptz - interval '14 days' AND ts < $1::timestamptz
    GROUP BY 1
)
SELECT t_in, td_in, t_out, rh_out FROM h
WHERE alt < $4 AND t_in IS NOT NULL AND td_in IS NOT NULL AND t_out IS NOT NULL AND rh_out > 0
  AND NOT EXISTS (SELECT 1 FROM busy WHERE busy.b = h.b)
ORDER BY b
"""
FORECAST_SQL = series.forecast_rows_sql(["temp_f", "dew_point_f"])


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BandProfilePatch(_Model):
    """Simple profile fields to change; a field left out keeps the band's own value. Any temperature field
    recompiles the temperature series with midpoint shoulders, any VPD field the VPD series."""

    temp_day: float | None = Field(default=None, allow_inf_nan=False)
    temp_night: float | None = Field(default=None, allow_inf_nan=False)
    temp_width_day: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    temp_width_night: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    vpd_day: float | None = Field(default=None, allow_inf_nan=False)
    vpd_night: float | None = Field(default=None, allow_inf_nan=False)
    vpd_below_day: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    vpd_above_day: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    vpd_below_night: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    vpd_above_night: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class ProfileState(_Model):
    profile: BandProfile
    anchors: dict[str, float]
    simple: bool = Field(description="True when sunrise and sunset are the midpoint of noon and midnight")


class SunTimes(_Model):
    sunrise: int | None
    solar_noon: int | None
    sunset: int | None
    solar_midnight: int | None


class AxisPreview(_Model):
    current: BandCurve
    draft: BandCurve


class VpdPreview(AxisPreview):
    device: BandCurve = Field(description="The draft as the controller runs it: night_vpd_bias_kpa added at night")
    expected_low: list[float | None] = Field(description="Expected night VPD, p25; null by day or without a forecast")
    expected_high: list[float | None] = Field(description="Expected night VPD, p75")


class Guardrails(_Model):
    temp_min_f: float = GUARDRAIL_TEMP_MIN_F
    temp_max_f: float = GUARDRAIL_TEMP_MAX_F
    margin_f: float = SAFETY_MARGIN_F
    vpd_min_kpa: float = GUARDRAIL_VPD_MIN_KPA
    vpd_max_kpa: float = GUARDRAIL_VPD_MAX_KPA
    night_vpd_floor_kpa: float = NIGHT_VPD_FLOOR_MIN_KPA


class NightHeat(_Model):
    current: float | None
    draft: float | None


class EnvelopeFit(_Model):
    hours: int
    days: int
    rmse_f: float
    dewpoint_p10_f: float


class ProfileFinding(_Model):
    code: str
    severity: Literal["error", "warning"]
    field: str
    detail: str


class BandProfilePreview(_Model):
    day: date
    step_s: Literal[600]
    t: list[int]
    sun: SunTimes
    current: ProfileState
    draft: ProfileState
    changed: list[str]
    temp: AxisPreview
    vpd: VpdPreview
    night_vpd_bias_kpa: float | None
    guardrails: Guardrails
    overshoot: dict[str, float]
    night_heat_kwh: NightHeat
    envelope: EnvelopeFit | None
    findings: list[ProfileFinding]


class BandWrite(_Model):
    """API-23's answer: the stored row of every anchor the Save changed, in anchor order."""

    changed: list[settings.DesiredSetting]


# ── pure ────────────────────────────────────────────────────────────────────


def anchors_from_rows(rows: Iterable[Any]) -> dict[str, float]:
    """The house anchors as ``fn_crop_band_value`` resolves them (current season over ``all``), by param."""
    resolved = series.resolve_anchors(rows)
    return {
        f"band_{series_name}_{anchor}": float(value)
        for (series_name, anchor), value in resolved.items()
        if series_name in HOUSE_SERIES and anchor in ANCHOR_KEYS and value is not None
    }


def parse_anchor_overrides(items: Iterable[str]) -> dict[str, float]:
    """``band_temp_low_sr:62.5`` items as {param: value}; anything else is 400 or 422."""
    out: dict[str, float] = {}
    for item in items:
        name, sep, raw = item.partition(":")
        if not sep:
            raise ServiceError(problem("invalid_request", "anchor is param:value", field="anchor"))
        try:
            out[name] = float(raw)
        except ValueError:
            raise ServiceError(problem("invalid_request", f"{name}'s value is not a number", field="anchor")) from None
    return out


def draft_anchors(
    current: Mapping[str, float], patch: BandProfilePatch | None, overrides: Mapping[str, float] | None
) -> dict[str, float]:
    """The anchors a Save would store: the band's own, the patched axes recompiled, then the raw overrides."""
    draft = dict(current)
    fields = patch.model_dump(exclude_none=True) if patch is not None else {}
    if fields:
        profile = BandProfile.model_validate({**decompose(current).model_dump(), **fields})
        compiled = compile_profile(profile)
        for axis_fields, axis in ((_TEMP_FIELDS, "temp"), (_VPD_FIELDS, "vpd")):
            if any(name in fields for name in axis_fields):
                draft.update({name: value for name, value in compiled.items() if axis_of(name) == axis})
    for name, value in (overrides or {}).items():
        if name not in HOUSE_PARAMS:
            raise ServiceError(problem("unknown_target", f"{name!r} is not a house band anchor", field="anchor"))
        if not math.isfinite(value):
            raise ServiceError(problem("out_of_range", f"{name} must be a finite number", field=name))
        settings.check_range(name, value)
        draft[name] = snap(value, axis_of(name))
    return draft


def changed_params(current: Mapping[str, float], draft: Mapping[str, float]) -> list[str]:
    return [name for name in HOUSE_PARAMS if not unchanged(current[name], draft[name], axis_of(name))]


def state_of(anchors: Mapping[str, float]) -> ProfileState:
    return ProfileState(profile=decompose(anchors), anchors=dict(anchors), simple=is_simple(anchors))


def axis_curve(anchors: Mapping[str, float], axis: str, phases: Sequence[float], bias: float = 0.0) -> BandCurve:
    names = (f"{axis}_low", f"{axis}_target", f"{axis}_high")
    low, target, high = (
        [round(harmonic(*series_points(anchors, name), phase) + night_bias(phase, bias), 4) for phase in phases]
        for name in names
    )
    return BandCurve(low=low, target=target, high=high)


def sun_times(t: Sequence[int], phases: Sequence[float]) -> SunTimes:
    """When the day's phase crosses 1 (noon), 2 (sunset) and 3 (solar midnight), and wraps to 0 (sunrise)."""
    found: dict[str, int | None] = {"sunrise": None, "solar_noon": None, "sunset": None, "solar_midnight": None}
    marks = {1.0: "solar_noon", 2.0: "sunset", 3.0: "solar_midnight"}
    for index in range(1, len(phases)):
        before, after = phases[index - 1], phases[index]
        if after < before - 2.0:  # 3.99 -> 0.01: sunrise
            found["sunrise"] = t[index]
            continue
        for level, name in marks.items():
            if before < level <= after and after > before:
                fraction = (level - before) / (after - before)
                found[name] = round(t[index - 1] + fraction * (t[index] - t[index - 1]))
    return SunTimes(**found)


def es_kpa(temp_f: float) -> float:
    return saturation_vapor_pressure_kpa(temp_f)


def dewpoint_f(vapour_kpa: float) -> float:
    """Inverse Tetens (the constants of ``saturation_vapor_pressure_kpa``)."""
    g = math.log(max(vapour_kpa, 1e-6) / 0.6108)
    return (237.3 * g / (17.27 - g)) * 9.0 / 5.0 + 32.0


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
    """Least squares of indoor dewpoint on indoor temperature, outdoor temperature and outdoor dewpoint."""
    samples: list[tuple[list[float], float]] = []
    for row in rows:
        t_in, td_in, t_out, rh_out = (row[key] for key in ("t_in", "td_in", "t_out", "rh_out"))
        if None in (t_in, td_in, t_out, rh_out):
            continue
        td_out = dewpoint_f(float(rh_out) / 100.0 * es_kpa(float(t_out)))
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


def _hour_key(epoch: int) -> int:
    return epoch - epoch % 3600


def expected_vpd(
    fit: DewpointFit | None,
    t: Sequence[int],
    phases: Sequence[float],
    target: Sequence[float | None],
    forecast: Mapping[int, tuple[float, float]],
) -> tuple[list[float | None], list[float | None]]:
    """The expected night VPD ribbon (p25, p75) at the draft's temperature target; null by day or without data."""
    low: list[float | None] = []
    high: list[float | None] = []
    for at, phase, temp in zip(t, phases, target, strict=True):
        outdoor = forecast.get(_hour_key(at))
        if fit is None or temp is None or outdoor is None or phase < 2.0:
            low.append(None)
            high.append(None)
            continue
        td = fit.predict(temp, *outdoor)
        spread = P25_Z * fit.rmse_f
        es = es_kpa(temp)
        # A wetter dewpoint gives the lower VPD.
        low.append(round(max(0.0, es - es_kpa(min(temp, td + spread))), 3))
        high.append(round(max(0.0, es - es_kpa(min(temp, td - spread))), 3))
    return low, high


def night_heat(
    t: Sequence[int],
    phases: Sequence[float],
    target: Sequence[float | None],
    forecast: Mapping[int, tuple[float, float]],
) -> float | None:
    """kWh over the day's night steps (sunset to sunrise) at 0.19 kW/°F; null without a forecast for them."""
    total = 0.0
    seen = 0
    night = 0
    for at, phase, temp in zip(t, phases, target, strict=True):
        if phase < 2.0 or temp is None:
            continue
        night += 1
        outdoor = forecast.get(_hour_key(at))
        if outdoor is None:
            continue
        seen += 1
        total += HEAT_LOSS_KW_PER_F * max(0.0, temp - outdoor[0]) * series.PREVIEW_STEP_S / 3600.0
    if night == 0 or seen < night / 2:
        return None
    return round(total * night / seen, 1)


def dewpoint_warning(anchors: Mapping[str, float], fit: DewpointFit | None) -> ProfileFinding | None:
    """``vpd_low`` at ``temp_low`` at solar midnight needs a dewpoint the house has not reached in 14 days."""
    if fit is None:
        return None
    temp = anchors["band_temp_low_mid"]
    vpd = anchors["band_vpd_low_mid"]
    vapour = es_kpa(temp) - vpd
    if vapour <= 0:
        return None
    needed = dewpoint_f(vapour)
    if needed >= fit.dewpoint_p10_f:
        return None
    return ProfileFinding(
        code="dewpoint_unreached",
        severity="warning",
        field="vpd_below_night",
        detail=(
            f"A {vpd:.2f} kPa night floor at {temp:.1f} °F needs an indoor dewpoint of {needed:.1f} °F; the driest "
            f"tenth of the last {ENVELOPE_DAYS} nights was {fit.dewpoint_p10_f:.1f} °F"
        ),
    )


def all_findings(anchors: Mapping[str, float], fit: DewpointFit | None) -> list[ProfileFinding]:
    out = [ProfileFinding(**vars(item)) for item in findings(anchors)]
    warning = dewpoint_warning(anchors, fit)
    if warning is not None:
        out.append(warning)
    return out


def _forecast_map(rows: Iterable[Mapping[str, Any]]) -> dict[int, tuple[float, float]]:
    out: dict[int, tuple[float, float]] = {}
    for row in rows:
        temp, dew = row["temp_f"], row["dew_point_f"]
        if temp is not None and dew is not None:
            out[_hour_key(int(row["ts"].timestamp()))] = (float(temp), float(dew))
    return out


# ── reads ───────────────────────────────────────────────────────────────────


async def _current_anchors(conn: asyncpg.Connection) -> dict[str, float]:
    rows = await conn.fetch(series.ANCHOR_ROWS_SQL, GREENHOUSE_ID, HOUSE_CROP, list(HOUSE_SERIES))
    anchors = anchors_from_rows(rows)
    missing = [name for name in HOUSE_PARAMS if name not in anchors]
    if missing:
        raise ServiceError(
            problem("read_only", f"the house band has no complete anchor set (missing {', '.join(missing)})")
        )
    return anchors


async def _night_bias(conn: asyncpg.Connection) -> float | None:
    """The bias the controller reports running, else none."""
    status = (await settings.statuses(conn, [NIGHT_BIAS_PARAM])).get(NIGHT_BIAS_PARAM)
    if status is None or status.readback is None or status.readback.value is None:
        return None
    return float(status.readback.value)


async def preview(
    conn: asyncpg.Connection,
    patch: BandProfilePatch | None = None,
    overrides: Mapping[str, float] | None = None,
    day: date | None = None,
    *,
    now: datetime | None = None,
) -> BandProfilePreview:
    """API-22: the house band as stored and as drafted over one America/Denver day. Read-only."""
    now = series._now(now)
    day = series._preview_day(day, now)
    start = series.denver_midnight(day)
    end = series.denver_midnight(day + timedelta(days=1))
    last = end - timedelta(seconds=series.PREVIEW_STEP_S)
    bias = await _night_bias(conn)
    async with series._read_transaction(conn):
        current = await _current_anchors(conn)
        phase_rows = await conn.fetch(series.PREVIEW_PHASES_SQL, start, last)
        envelope_rows = await conn.fetch(
            ENVELOPE_SQL, now, list(MOISTURE_EQUIPMENT), GREENHOUSE_ID, DEEP_NIGHT_ALTITUDE_DEG
        )
        forecast_rows = await conn.fetch(FORECAST_SQL, start, end)
    draft = draft_anchors(current, patch, overrides)
    t = [series._epoch(row["ts"]) for row in phase_rows]
    phases = [float(row["phase"]) for row in phase_rows]
    fit = fit_dewpoint(envelope_rows)
    forecast = _forecast_map(forecast_rows)
    temp_current, temp_draft = axis_curve(current, "temp", phases), axis_curve(draft, "temp", phases)
    expected_low, expected_high = expected_vpd(fit, t, phases, temp_draft.target, forecast)
    return BandProfilePreview(
        day=day,
        step_s=series.PREVIEW_STEP_S,
        t=t,
        sun=sun_times(t, phases),
        current=state_of(current),
        draft=state_of(draft),
        changed=changed_params(current, draft),
        temp=AxisPreview(current=temp_current, draft=temp_draft),
        vpd=VpdPreview(
            current=axis_curve(current, "vpd", phases),
            draft=axis_curve(draft, "vpd", phases),
            device=axis_curve(draft, "vpd", phases, bias or 0.0),
            expected_low=expected_low,
            expected_high=expected_high,
        ),
        night_vpd_bias_kpa=bias,
        guardrails=Guardrails(),
        overshoot=overshoot(draft),
        night_heat_kwh=NightHeat(
            current=night_heat(t, phases, temp_current.target, forecast),
            draft=night_heat(t, phases, temp_draft.target, forecast),
        ),
        envelope=None
        if fit is None
        else EnvelopeFit(hours=fit.hours, days=ENVELOPE_DAYS, rmse_f=fit.rmse_f, dewpoint_p10_f=fit.dewpoint_p10_f),
        findings=all_findings(draft, fit),
    )


# ── the write ───────────────────────────────────────────────────────────────


async def write(
    conn: asyncpg.Connection,
    actor: Actor,
    patch: BandProfilePatch | None,
    overrides: Mapping[str, float] | None,
    manual: bool,
    reason: str | None,
    request_id: Any,
) -> WriteResult[BandWrite]:
    """API-23: every anchor the draft changes, in one ``recorded_write`` transaction with one ``band.write`` row.

    Refusals: Iris (``read_only``: the band is operator-owned), an ``error`` finding (``out_of_range`` with its field),
    a draft that changes nothing (``invalid_request``), and each anchor's own settings checks.
    """
    if actor.source == "iris":
        raise ServiceError(problem("read_only", "The band is operator-owned; Iris may not write it", field="anchor"))
    flag = settings._manual_flag(manual)
    request = {
        "profile": patch.model_dump(exclude_none=True) if patch is not None else {},
        "anchors": {name: float(value) for name, value in sorted((overrides or {}).items())},
        "manual": flag,
        "reason": reason,
    }

    async def perform(tx: asyncpg.Connection) -> tuple[BandWrite, dict[str, Any]]:
        current = await _current_anchors(tx)
        draft = draft_anchors(current, patch, overrides)
        for finding in findings(draft):
            if finding.severity == "error":
                raise ServiceError(problem("out_of_range", finding.detail, field=finding.field))
        names = changed_params(current, draft)
        if not names:
            raise ServiceError(problem("invalid_request", "The draft changes no anchor", field="anchor"))
        stored: list[settings.DesiredSetting] = []
        changes: list[dict[str, Any]] = []
        anchor_rows: list[int] = []
        for name in names:
            checked, row, rows, setting = await settings.write_in_transaction(
                tx, actor, name, draft[name], flag, reason
            )
            stored.append(setting)
            anchor_rows.extend(rows)
            changes.append({"param": name, "desired_id": row["id"], "before": current[name], "value": checked.value})
        detail: dict[str, Any] = {
            "ref": {"table": "desired_state", "id": stored[0].id},
            "desired_ids": [setting.id for setting in stored],
            "changes": changes,
            "anchor_rows": sorted(anchor_rows),
            "manual": {"after": flag},
            "profile": decompose(draft).model_dump(),
            "simple": is_simple(draft),
        }
        return BandWrite(changed=stored), detail

    async def load(tx: asyncpg.Connection, logged: ActivityLogRow) -> BandWrite:
        ids = [int(value) for value in logged.detail.get("desired_ids") or []]
        rows = await settings.desired_states(tx, ids)
        if any(value not in rows for value in ids):
            raise ServiceError(problem("not_found", "the stored band write is gone", field="anchor"))
        return BandWrite(changed=[rows[value] for value in ids])

    return await activity.recorded_write(
        conn,
        actor,
        op="API-23",
        action=ACTION,
        target=TARGET,
        request=request,
        request_id=request_id,
        reason=reason,
        perform=perform,
        load=load,
    )
