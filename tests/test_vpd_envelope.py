"""The achievable band beside the served one (#1433): the pure envelope, the nightly grades and API-06's read."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import jsonschema
import pytest
import yaml

from verdify_schemas import vpd_envelope as ve
from verdify_services import band_profile as bp
from verdify_services import series as s
from verdify_services import vpd_envelope as service

ROOT = Path(__file__).resolve().parents[1]
SPEC = yaml.safe_load((ROOT / "docs/design/dashboard/openapi/dashboard-api.v1.yaml").read_text())
# 2026-11-06 12:00 MST
NOW = datetime(2026, 11, 6, 19, 0, tzinfo=UTC)
COEF = (5.0, 0.6, 0.1, 0.2)


def openapi_errors(schema: str, instance) -> list[str]:
    validator = jsonschema.Draft202012Validator(
        {"$ref": f"#/components/schemas/{schema}", "components": SPEC["components"]}
    )
    return [f"{list(error.absolute_path)}: {error.message}" for error in validator.iter_errors(instance)]


def training(start: datetime, hours: int, coef=COEF) -> list[dict]:
    rows = []
    for index in range(hours):
        t_in, t_out, rh_out = 58 + index % 7, 30 + index % 11, 40 + index % 23
        td_out = ve.outdoor_dewpoint_f(t_out, rh_out)
        a, b, c, e = coef
        rows.append(
            {
                "b": start + timedelta(hours=index),
                "t_in": t_in,
                "td_in": a + b * t_in + c * t_out + e * td_out,
                "t_out": t_out,
                "rh_out": rh_out,
            }
        )
    return rows


# ── the pure envelope ───────────────────────────────────────────────────────


def test_the_fit_recovers_its_coefficients_and_needs_24_hours():
    fit = ve.fit_dewpoint(training(NOW, 60))
    assert fit is not None and fit.hours == 60 and fit.rmse_f < 1e-6
    assert fit.coef == pytest.approx(COEF, abs=1e-6)
    assert ve.fit_dewpoint(training(NOW, 23)) is None
    # The editor's names are the same functions.
    assert bp.fit_dewpoint is ve.fit_dewpoint and bp.es_kpa is ve.es_kpa


def test_the_ribbon_is_es_of_the_target_minus_es_of_the_fitted_dewpoint_and_never_negative():
    fit = ve.DewpointFit(COEF, 2.0, 100, 40.0)
    temp, t_out, td_out = 65.0, 40.0, 30.0
    td = fit.predict(temp, t_out, td_out)
    low, high = ve.expected_vpd(fit, temp, t_out, td_out)
    assert low == pytest.approx(ve.es_kpa(temp) - ve.es_kpa(td + 0.674 * 2.0), abs=1e-3)
    assert high == pytest.approx(ve.es_kpa(temp) - ve.es_kpa(td - 0.674 * 2.0), abs=1e-3)
    assert 0 < low < high
    # A dewpoint at or above the air temperature is saturated air: VPD 0, not negative.
    wet = ve.DewpointFit((80.0, 0.0, 0.0, 0.0), 0.0, 100, 40.0)
    assert ve.expected_vpd(wet, 65.0, 40.0, 30.0) == (0.0, 0.0)


@pytest.mark.parametrize(
    ("expected", "edges"),
    [
        (None, (0.74, 1.2)),  # by day: the band
        ((0.45, 0.6), (0.45, 1.2)),  # a humid night: the floor drops to p25
        ((0.8, 1.0), (0.74, 1.2)),  # inside the band: the band
        ((1.1, 1.4), (0.74, 1.4)),  # a dry winter night: the ceiling rises to p75
    ],
)
def test_the_achievable_band_is_the_band_widened_to_the_ribbon(expected, edges):
    assert ve.achievable_band(0.74, 1.2, expected) == edges
    assert ve.achievable_band(None, 1.2, expected) == (None, 1.2)


def test_grade_says_below_in_or_above_and_nothing_without_a_value_or_band():
    assert [ve.grade(v, 0.5, 1.0) for v in (0.4, 0.5, 1.0, 1.1)] == ["below", "in", "in", "above"]
    assert ve.grade(None, 0.5, 1.0) is None and ve.grade(0.7, None, 1.0) is None


# ── the nightly grades ──────────────────────────────────────────────────────


def test_a_night_step_belongs_to_the_evening_it_started_and_its_fit_ends_at_that_noon():
    evening = int(datetime(2026, 11, 5, 18, 0, tzinfo=s.DENVER).timestamp())
    morning = int(datetime(2026, 11, 6, 6, 50, tzinfo=s.DENVER).timestamp())
    assert service.night_of(evening) == service.night_of(morning) == date(2026, 11, 5)
    assert service.fit_end(date(2026, 11, 5), NOW) == datetime(2026, 11, 5, 12, 0, tzinfo=s.DENVER)
    assert service.fit_end(date(2026, 11, 6), NOW) == NOW  # tonight: the days before now
    assert service.fit_end(date(2026, 11, 7), NOW) == NOW


def test_each_night_uses_only_the_14_days_before_it():
    end = service.fit_end(date(2026, 11, 5), NOW)
    rows = training(end - timedelta(days=14), 30) + training(end, 30, coef=(0.0, 1.0, 0.0, 0.0))
    fits = service.fits_for([date(2026, 11, 5)], rows, NOW)
    assert fits[date(2026, 11, 5)].coef == pytest.approx(COEF, abs=1e-6)
    assert service.fits_for([date(2026, 10, 1)], rows, NOW) == {date(2026, 10, 1): None}


def test_columns_widen_the_night_floor_and_grade_each_night_against_both_bands():
    fit = ve.DewpointFit(COEF, 1.0, 120, 40.0)
    base = int(datetime(2026, 11, 5, 20, 0, tzinfo=s.DENVER).timestamp())
    t = [base - 6 * 3600, base, base + 3600, base + 7200]  # a day step, then three night steps
    phases = [1.0, 2.5, 2.6, 2.7]
    band = {"temp_low": [60.0] * 4, "temp_high": [70.0] * 4, "vpd_low": [0.74] * 4, "vpd_high": [1.2] * 4}
    crop = {key: [None] * 4 for key in band}
    # Past steps: (house VPD, outdoor temperature, outdoor RH, house temperature); the last has no house reading.
    measured = {
        t[0]: (0.9, 50.0, 60.0, 72.0),
        t[1]: (0.5, 40.0, 90.0, 65.0),
        t[2]: (0.8, 40.0, 90.0, 65.0),
        t[3]: (None, 40.0, 90.0, None),
    }
    out = service.columns(t, phases, band, crop, [None] * 4, measured, {}, {date(2026, 11, 5): fit})
    # At the measured 65 °F; the step without a reading takes the band's midpoint, also 65 °F here.
    expected = ve.expected_vpd(fit, 65.0, 40.0, ve.outdoor_dewpoint_f(40.0, 90.0))
    assert expected[0] < 0.74  # a humid night: the house is not expected to reach the floor
    assert out.vpd_low == [0.74, expected[0], expected[0], expected[0]] and out.vpd_high == [1.2] * 4
    assert out.expected_low[0] is None and out.expected_low[1] == expected[0]
    assert len(out.nights) == 1
    night = out.nights[0]
    assert night.night == date(2026, 11, 5) and night.graded == 2  # the step without a VPD is not graded
    assert (night.served_in_pct, night.below_served_pct) == (50.0, 50.0)
    assert night.achievable_in_pct == (100.0 if expected[0] <= 0.5 else 50.0)
    assert night.fit.hours == 120
    # The ribbon follows the temperature the house measured, not the band's.
    warm = service.columns(
        t[1:2],
        [2.5],
        {k: v[1:2] for k, v in band.items()},
        {k: [None] for k in band},
        [None],
        {t[1]: (0.5, 40.0, 90.0, 68.0)},
        {},
        {date(2026, 11, 5): fit},
    )
    assert warm.expected_low == [ve.expected_vpd(fit, 68.0, 40.0, ve.outdoor_dewpoint_f(40.0, 90.0))[0]]


def test_columns_take_the_crop_band_and_the_forecast_where_the_band_or_measurement_is_missing():
    fit = ve.DewpointFit(COEF, 1.0, 120, 40.0)
    at = int(datetime(2026, 11, 6, 23, 0, tzinfo=s.DENVER).timestamp())
    band = {key: [None] for key in ("temp_low", "temp_high", "vpd_low", "vpd_high")}
    crop = {"temp_low": [60.0], "temp_high": [70.0], "vpd_low": [0.74], "vpd_high": [1.2]}
    out = service.columns([at], [3.0], band, crop, [66.0], {}, {at - at % 3600: (35.0, 25.0)}, {date(2026, 11, 6): fit})
    assert out.expected_low == [ve.expected_vpd(fit, 66.0, 35.0, 25.0)[0]]
    assert out.nights[0].graded == 0 and out.nights[0].served_in_pct is None


# ── API-06 with achievable ──────────────────────────────────────────────────


class Conn:
    def __init__(self):
        self.fetches: list[tuple[str, tuple]] = []

    def transaction(self, readonly=False):
        return Transaction()

    async def execute(self, sql, *args):
        return None

    async def fetch(self, sql, *args):
        self.fetches.append((sql, args))
        if sql == s.CROP_ACHIEVABLE_SQL:
            out = []
            for ts in args[0]:
                local = ts.astimezone(s.DENVER)
                phase = 1.0 if 7 <= local.hour < 17 else 3.0
                out.append(
                    {"ts": ts, "temp_low": 60.0, "temp_high": 70.0, "vpd_low": 0.74, "vpd_high": 1.2}
                    | {"temp_target": 65.0, "phase": phase}
                )
            return out
        if sql == service.STEP_CLIMATE_SQL:
            start, end, step = args[0], args[1], args[2]
            rows, at = [], start
            while at < end:
                rows.append({"bucket": at, "vpd": 0.5, "t_in": 64.0, "t_out": 40.0, "rh_out": 90.0})
                at += step
            return rows
        if sql == service.ENVELOPE_RANGE_SQL:
            return training(args[0], int((args[1] - args[0]).total_seconds() // 3600))
        return []


class Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def test_api_06_adds_the_achievable_band_for_the_house_only_and_matches_its_schema():
    conn = Conn()
    from_, to = NOW - timedelta(days=2), NOW
    answer = asyncio.run(s.band(conn, from_, to, achievable=True, now=NOW))
    payload = answer.model_dump(mode="json", by_alias=True)
    assert openapi_errors("BandTimeline", payload) == []
    achievable = payload["achievable"]
    assert len(achievable["vpd_low"]) == len(payload["t"])
    assert [night["night"] for night in achievable["nights"]] == ["2026-11-04", "2026-11-05"]
    assert all(night["graded"] > 0 and night["served_in_pct"] == 0.0 for night in achievable["nights"])
    assert all(night["fit"]["hours"] > 24 for night in achievable["nights"])
    # The training read spans the first night's 14 days to the last night's noon.
    sql, args = next(call for call in conn.fetches if call[0] == service.ENVELOPE_RANGE_SQL)
    assert args[0] == service.fit_end(date(2026, 11, 4), NOW) - timedelta(days=14)
    assert args[1] == service.fit_end(date(2026, 11, 5), NOW)
    assert args[2] == list(ve.MOISTURE_EQUIPMENT)
    # Not asked for, a zone, or a window past 35 days: none, and no envelope read.
    for kwargs in ({}, {"zone": "south"}):
        plain = Conn()
        assert asyncio.run(s.band(plain, from_, to, now=NOW, **kwargs)).achievable is None
        assert not any(sql == service.ENVELOPE_RANGE_SQL for sql, _ in plain.fetches)
    long = Conn()
    assert asyncio.run(s.band(long, NOW - timedelta(days=40), NOW, achievable=True, now=NOW)).achievable is None


# ── the editor's warning ────────────────────────────────────────────────────


def test_the_editor_warns_when_the_night_floor_is_above_the_achievable_ribbon_for_half_the_night():
    warning = bp.floor_warning([None, 0.74, 0.74, 0.74], [None, 0.6, 0.62, 0.8])
    assert warning is not None and warning.code == "night_floor_unachievable" and "0.80 kPa" in warning.detail
    assert bp.floor_warning([0.74, 0.74, 0.74], [0.6, 0.8, 0.9]) is None
    assert bp.floor_warning([0.74], [None]) is None
