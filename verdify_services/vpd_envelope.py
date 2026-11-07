"""The achievable band beside the served one, and both grades (#1433): API-06 with ``achievable``.

For each step of the house band timeline, at night (``fn_solar_phase`` ≥ 2, sunset to sunrise):

* the expected VPD ribbon (p25-p75) of ``verdify_schemas.vpd_envelope``, at the house temperature the step measured
  (``climate.temp_avg``), or ahead of now at the band's temperature (the served band's midpoint, else today's
  ``fn_band_setpoints`` target), and at the step's outdoor temperature and dewpoint (the measured ``climate`` averages
  up to now, the newest forecast issue after). Graded at the measured temperature, the achievable band asks only
  whether the house held the VPD that air at that temperature could reach; the temperature is graded on its own band;
* the achievable band: the served band (else the crop band, as the ribbon draws it) widened to that ribbon.

The dewpoint fit is rolling: each night uses the ``ENVELOPE_DAYS`` before its local noon (now, for a night ahead), so a
past night is graded by what the house could do then. By day the achievable band is the band.

``nights`` grades the measured house VPD (``climate.vpd_avg``, the step's average) against both bands, one row per
night (the America/Denver date its evening falls on). Grading is at the timeline's step, not the 5-minute buckets of
``daily_band_compliance`` (migration 290), so the served share here is close to, not equal to, that rollup's.

Windows longer than ``MAX_WINDOW`` carry no achievable band (``None``): the fit reads every hour of the window plus 14
days.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, time, timedelta
from typing import Any

import asyncpg

from verdify_schemas.vpd_envelope import (
    DEEP_NIGHT_ALTITUDE_DEG,
    ENVELOPE_DAYS,
    MOISTURE_EQUIPMENT,
    AchievableColumns,
    AchievableFit,
    AchievableNight,
    DewpointFit,
    achievable_band,
    expected_vpd,
    fit_dewpoint,
    grade,
    is_night,
    outdoor_dewpoint_f,
)
from verdify_services import series

GREENHOUSE_ID = "vallery"
# (house VPD, outdoor temperature, outdoor humidity, house temperature) at a step.
Measured = tuple[float | None, float | None, float | None, float | None]
NOTHING: Measured = (None, None, None, None)
MAX_WINDOW = timedelta(days=35)
NOON = time(12, 0)

# Deep-night hours in [$1, $2) with none of the moisture equipment ($3) on: the fit's training rows.
ENVELOPE_RANGE_SQL = """
WITH s AS (
    SELECT equipment, ts, state, lead(ts) OVER (PARTITION BY equipment ORDER BY ts) AS nts
    FROM public.equipment_state
    WHERE equipment = ANY($3::text[]) AND ts >= $1::timestamptz - interval '1 day' AND ts < $2::timestamptz
), busy AS (
    SELECT DISTINCT gs AS b
    FROM s CROSS JOIN LATERAL generate_series(
        date_trunc('hour', s.ts),
        date_trunc('hour', LEAST(COALESCE(s.nts, $2::timestamptz), s.ts + interval '6 hours')),
        interval '1 hour') AS gs
    WHERE s.state
), h AS (
    SELECT time_bucket('1 hour', ts) AS b, avg(temp_avg) AS t_in, avg(dew_point) AS td_in,
           avg(outdoor_temp_f) AS t_out, avg(outdoor_rh_pct) AS rh_out, avg(solar_altitude_deg) AS alt
    FROM public.climate
    WHERE greenhouse_id = $4 AND ts >= $1::timestamptz AND ts < $2::timestamptz
    GROUP BY 1
)
SELECT b, t_in, td_in, t_out, rh_out FROM h
WHERE alt < $5 AND t_in IS NOT NULL AND td_in IS NOT NULL AND t_out IS NOT NULL AND rh_out > 0
  AND NOT EXISTS (SELECT 1 FROM busy WHERE busy.b = h.b)
ORDER BY b
"""
# The step's measured house VPD and temperature and outdoor temperature and humidity.
STEP_CLIMATE_SQL = (
    "SELECT time_bucket_gapfill($3::interval, ts, $1::timestamptz, $2::timestamptz) AS bucket, "
    "avg(vpd_avg)::double precision AS vpd, avg(temp_avg)::double precision AS t_in, "
    "avg(outdoor_temp_f)::double precision AS t_out, "
    "avg(outdoor_rh_pct)::double precision AS rh_out "
    "FROM public.climate WHERE greenhouse_id = $4 AND ts >= $1::timestamptz AND ts < $2::timestamptz "
    "GROUP BY 1 ORDER BY 1"
)
FORECAST_SQL = series.forecast_rows_sql(["temp_f", "dew_point_f"])


# ── pure ────────────────────────────────────────────────────────────────────


def night_of(epoch: int) -> date:
    """The Denver date a night step's evening falls on (12 h back from the step)."""
    return datetime.fromtimestamp(epoch - 12 * 3600, series.DENVER).date()


def fit_end(night: date, now: datetime) -> datetime:
    """A night's fit reads the days before its local noon; a night ahead of now, the days before now."""
    noon = datetime.combine(night, NOON, tzinfo=series.DENVER)
    return min(noon, now)


def fits_for(
    nights: Iterable[date], rows: Sequence[Mapping[str, Any]], now: datetime
) -> dict[date, DewpointFit | None]:
    out: dict[date, DewpointFit | None] = {}
    for night in nights:
        end = fit_end(night, now)
        start = end - timedelta(days=ENVELOPE_DAYS)
        out[night] = fit_dewpoint(row for row in rows if start <= row["b"] < end)
    return out


def _outdoor(
    at: int, measured: Mapping[int, tuple[float | None, float | None]], forecast: Mapping[int, tuple[float, float]]
) -> tuple[float, float] | None:
    """(outdoor temperature, outdoor dewpoint) at a step: measured where it has both, else that hour's forecast."""
    t_out, rh_out = measured.get(at, (None, None))
    if t_out is not None and rh_out is not None and rh_out > 0:
        return t_out, outdoor_dewpoint_f(t_out, rh_out)
    return forecast.get(at - at % 3600)


def _pct(part: int, whole: int) -> float | None:
    return round(100.0 * part / whole, 1) if whole else None


def columns(
    t: Sequence[int],
    phases: Sequence[float | None],
    band: Mapping[str, Sequence[float | None]],
    crop: Mapping[str, Sequence[float | None]],
    temp_targets: Sequence[float | None],
    measured: Mapping[int, Measured],
    forecast: Mapping[int, tuple[float, float]],
    fits: Mapping[date, DewpointFit | None],
) -> AchievableColumns:
    """Assemble the achievable columns and the nightly grades (pure).

    ``band`` and ``crop`` hold ``temp_low``, ``temp_high``, ``vpd_low`` and ``vpd_high`` per step; the band in force
    wins where it has both edges. ``measured`` maps a step to (house VPD, outdoor temperature, outdoor humidity, house temperature)."""
    a_low: list[float | None] = []
    a_high: list[float | None] = []
    e_low: list[float | None] = []
    e_high: list[float | None] = []
    tallies: dict[date, list[int]] = {}
    outdoor = {at: (row[1], row[2]) for at, row in measured.items()}
    for index, at in enumerate(t):
        in_force = band["vpd_low"][index] is not None and band["vpd_high"][index] is not None
        source = band if in_force else crop
        low, high = source["vpd_low"][index], source["vpd_high"][index]
        t_low, t_high = source["temp_low"][index], source["temp_high"][index]
        row = measured.get(at, NOTHING)
        temp = row[3]
        if temp is None:
            temp = (
                (t_low + t_high) / 2 if in_force and t_low is not None and t_high is not None else temp_targets[index]
            )
        expected: tuple[float, float] | None = None
        night = night_of(at) if is_night(phases[index]) else None
        fit = fits.get(night) if night is not None else None
        if fit is not None and temp is not None:
            weather = _outdoor(at, outdoor, forecast)
            if weather is not None:
                expected = expected_vpd(fit, temp, *weather)
        edges = achievable_band(low, high, expected)
        a_low.append(edges[0])
        a_high.append(edges[1])
        e_low.append(None if expected is None else expected[0])
        e_high.append(None if expected is None else expected[1])
        if night is None:
            continue
        tally = tallies.setdefault(night, [0, 0, 0, 0, 0])
        vpd = row[0]
        served = grade(vpd, low, high)
        if served is None:
            continue
        achievable = grade(vpd, *edges)
        tally[0] += 1
        tally[1] += served == "in"
        tally[2] += achievable == "in"
        tally[3] += served == "below"
        tally[4] += achievable == "below"
    nights = []
    for night in sorted({night_of(at) for at, phase in zip(t, phases, strict=True) if is_night(phase)}):
        graded, served_in, achievable_in, below_served, below_achievable = tallies.get(night, [0] * 5)
        fit = fits.get(night)
        nights.append(
            AchievableNight(
                night=night,
                graded=graded,
                served_in_pct=_pct(served_in, graded),
                achievable_in_pct=_pct(achievable_in, graded),
                below_served_pct=_pct(below_served, graded),
                below_achievable_pct=_pct(below_achievable, graded),
                fit=None if fit is None else AchievableFit(hours=fit.hours, rmse_f=fit.rmse_f),
            )
        )
    return AchievableColumns(vpd_low=a_low, vpd_high=a_high, expected_low=e_low, expected_high=e_high, nights=nights)


def _measured_map(rows: Iterable[Mapping[str, Any]]) -> dict[int, Measured]:
    return {
        series._epoch(row["bucket"]): (
            series._number(row["vpd"]),
            series._number(row["t_out"]),
            series._number(row["rh_out"]),
            series._number(row["t_in"]),
        )
        for row in rows
    }


def _forecast_map(rows: Iterable[Mapping[str, Any]]) -> dict[int, tuple[float, float]]:
    out: dict[int, tuple[float, float]] = {}
    for row in rows:
        temp, dew = series._number(row["temp_f"]), series._number(row["dew_point_f"])
        if temp is not None and dew is not None:
            at = series._epoch(row["ts"])
            out[at - at % 3600] = (temp, dew)
    return out


# ── the read ────────────────────────────────────────────────────────────────


async def read(
    conn: asyncpg.Connection,
    axis: Sequence[datetime],
    step_s: int,
    band: series.BandColumns,
    crop_rows: Sequence[Mapping[str, Any]],
    now: datetime,
) -> AchievableColumns | None:
    """The achievable columns for a house timeline; run inside the band read's transaction. ``crop_rows`` carry each
    step's ``temp_target`` and ``phase`` beside the crop band."""
    if not axis or axis[-1] - axis[0] > MAX_WINDOW:
        return None
    t = [series._epoch(start) for start in axis]
    by_ts = {series._epoch(row["ts"]): row for row in crop_rows}
    phases = [series._number(by_ts[at].get("phase")) if at in by_ts else None for at in t]
    targets = [series._number(by_ts[at].get("temp_target")) if at in by_ts else None for at in t]
    crop = series.crop_columns(axis, crop_rows)
    night_dates = sorted({night_of(at) for at, phase in zip(t, phases, strict=True) if is_night(phase)})
    end = axis[-1] + timedelta(seconds=step_s)
    measured_rows: list[Any] = []
    forecast_rows: list[Any] = []
    training: list[Any] = []
    if axis[0] < now:
        measured_rows = await conn.fetch(
            STEP_CLIMATE_SQL, axis[0], min(end, now), timedelta(seconds=step_s), GREENHOUSE_ID
        )
    if end > now:
        forecast_rows = await conn.fetch(FORECAST_SQL, max(axis[0], now) - timedelta(hours=1), end)
    if night_dates:
        first = fit_end(night_dates[0], now) - timedelta(days=ENVELOPE_DAYS)
        last = fit_end(night_dates[-1], now)
        training = [
            dict(row)
            for row in await conn.fetch(
                ENVELOPE_RANGE_SQL, first, last, list(MOISTURE_EQUIPMENT), GREENHOUSE_ID, DEEP_NIGHT_ALTITUDE_DEG
            )
        ]
    return columns(
        t,
        phases,
        band.model_dump(),
        crop.model_dump(),
        targets,
        _measured_map(measured_rows),
        _forecast_map(forecast_rows),
        fits_for(night_dates, training, now),
    )
