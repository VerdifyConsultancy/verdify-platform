"""SetpointChange + DailySummaryRow + ForecastHour schema tests."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from verdify_schemas.daily import DailySummaryRow
from verdify_schemas.forecast import ForecastHour
from verdify_schemas.setpoint import SetpointChange


class TestSetpointChange:
    def test_valid(self):
        s = SetpointChange(
            ts=datetime(2026, 4, 18, tzinfo=UTC),
            parameter="temp_low",
            value=55.0,
        )
        assert s.source == "plan"  # default
        assert s.confirmed_at is None
        assert s.greenhouse_id == "vallery"

    def test_accepts_confirmed_at(self):
        s = SetpointChange(
            ts=datetime(2026, 4, 18, tzinfo=UTC),
            parameter="vpd_high",
            value=1.8,
            confirmed_at=datetime(2026, 4, 18, 0, 1, tzinfo=UTC),
        )
        assert s.confirmed_at is not None

    def test_rejects_unknown_parameter(self):
        with pytest.raises(ValidationError):
            SetpointChange(
                ts=datetime(2026, 4, 18, tzinfo=UTC),
                parameter="bogus_param",
                value=1.0,
            )

    def test_rejects_bad_source(self):
        with pytest.raises(ValidationError):
            SetpointChange(
                ts=datetime(2026, 4, 18, tzinfo=UTC),
                parameter="temp_low",
                value=55.0,
                source="totally_fake",  # not in SetpointSource literal
            )


class TestDailySummaryRow:
    def test_minimal(self):
        row = DailySummaryRow(date=date(2026, 4, 18))
        assert row.stress_hours_heat is None
        assert row.cost_total is None

    @pytest.mark.parametrize(
        "field", ("stress_hours_heat", "stress_hours_cold", "stress_hours_vpd_high", "stress_hours_vpd_low")
    )
    def test_unavailable_stress_stays_null_and_measured_zero_stays_zero(self, field):
        unavailable = DailySummaryRow.model_validate({"date": date(2026, 10, 5), field: None})
        measured = DailySummaryRow.model_validate({"date": date(2026, 10, 5), field: 0.0})
        assert unavailable.model_dump()[field] is None
        assert measured.model_dump()[field] == 0.0
        with pytest.raises(ValidationError):
            DailySummaryRow.model_validate({"date": date(2026, 10, 5), field: float("nan")})

    def test_full_row(self):
        row = DailySummaryRow(
            date=date(2026, 4, 18),
            temp_min=48.0,
            temp_avg=64.0,
            temp_max=82.0,
            rh_min=22.0,
            rh_avg=55.0,
            rh_max=90.0,
            vpd_avg=0.8,
            stress_hours_heat=0.5,
            cost_total=4.23,
        )
        assert row.temp_max == 82.0
        assert row.cost_total == 4.23

    def test_rejects_stress_hours_over_24(self):
        with pytest.raises(ValidationError):
            DailySummaryRow(date=date(2026, 4, 18), stress_hours_heat=25.0)

    def test_tolerates_extra_columns(self):
        row = DailySummaryRow.model_validate(
            {"date": date(2026, 4, 18), "new_future_column": 123},
        )
        assert row.date == date(2026, 4, 18)


class TestForecastHour:
    def test_valid(self):
        hour = ForecastHour(
            ts=datetime(2026, 4, 18, 15, tzinfo=UTC),
            fetched_at=datetime(2026, 4, 18, 12, tzinfo=UTC),
            temp_f=72.0,
            rh_pct=15.0,
            vpd_kpa=2.3,
            solar_w_m2=870.0,
        )
        assert hour.temp_f == 72.0

    def test_rejects_rh_over_100(self):
        with pytest.raises(ValidationError):
            ForecastHour(
                ts=datetime(2026, 4, 18, tzinfo=UTC),
                fetched_at=datetime(2026, 4, 18, tzinfo=UTC),
                rh_pct=110.0,
            )

    def test_rejects_negative_precip(self):
        with pytest.raises(ValidationError):
            ForecastHour(
                ts=datetime(2026, 4, 18, tzinfo=UTC),
                fetched_at=datetime(2026, 4, 18, tzinfo=UTC),
                precip_in=-0.5,
            )

    def test_tolerates_extra(self):
        hour = ForecastHour.model_validate(
            {
                "ts": datetime(2026, 4, 18, tzinfo=UTC),
                "fetched_at": datetime(2026, 4, 18, tzinfo=UTC),
                "some_new_field": "value",
            },
        )
        assert hour.ts.hour == 0
