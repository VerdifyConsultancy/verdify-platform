"""Phase 6 — external-API boundary schemas."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from verdify_schemas.external import (
    HAEntityState,
    OpenMeteoForecastResponse,
    OpenMeteoHourly,
    TuyaDeviceStatus,
)


class TestOpenMeteoHourly:
    def test_valid_matching_lengths(self):
        h = OpenMeteoHourly(
            time=["2026-04-19T00:00", "2026-04-19T01:00", "2026-04-19T02:00"],
            temperature_2m=[58.0, 56.0, 54.0],
            relative_humidity_2m=[60.0, 65.0, 70.0],
        )
        assert len(h.time) == 3

    def test_rejects_array_length_mismatch(self):
        with pytest.raises(ValidationError, match="length"):
            OpenMeteoHourly(
                time=["2026-04-19T00:00", "2026-04-19T01:00"],
                temperature_2m=[58.0, 56.0, 54.0],  # 3 vs 2 -> mismatch
            )

    def test_nulls_in_arrays_allowed(self):
        h = OpenMeteoHourly(
            time=["2026-04-19T00:00", "2026-04-19T01:00"],
            precipitation_probability=[10.0, None],
        )
        assert h.precipitation_probability[1] is None


class TestOpenMeteoForecastResponse:
    def test_full_envelope(self):
        envelope = OpenMeteoForecastResponse(
            latitude=40.1,
            longitude=-105.1,
            timezone="America/Denver",
            hourly={
                "time": ["2026-04-19T00:00", "2026-04-19T01:00"],
                "temperature_2m": [58.0, 56.0],
            },
        )
        assert envelope.hourly.time[0] == "2026-04-19T00:00"


class TestHAEntityState:
    def test_valid_numeric(self):
        s = HAEntityState(
            entity_id="sensor.panorama_temperature",
            state="58.3",
            attributes={"unit_of_measurement": "°F"},
        )
        assert s.as_float() == 58.3
        assert s.is_available is True

    def test_unavailable(self):
        s = HAEntityState(entity_id="x", state="unavailable")
        assert s.is_available is False
        assert s.as_float() is None

    def test_unknown_non_numeric(self):
        s = HAEntityState(entity_id="x", state="IDLE")
        assert s.as_float() is None

    def test_rejects_empty_entity_id(self):
        with pytest.raises(ValidationError):
            HAEntityState(entity_id="", state="1.0")

    def test_parses_iso_datetime_state(self):
        s = HAEntityState(entity_id="x", state="2026-04-19T00:00:00+00:00")
        dt = s.as_datetime()
        assert dt is not None
        assert dt.year == 2026


class TestTuyaDeviceStatus:
    def test_keeps_raw_values_and_drops_the_envelope(self):
        s = TuyaDeviceStatus.model_validate(
            {"devId": "fixture-device", "t": 1, "dps": {"10": 621, "11": 2484, "101": "AsiaStandard", "102": False}}
        )
        assert s.dps == {"10": 621, "11": 2484, "101": "AsiaStandard", "102": False}
        assert type(s.dps["102"]) is bool and "devId" not in s.model_dump()

    @pytest.mark.parametrize(
        "payload",
        [
            {"Error": "Network Error: Unable to Connect", "Err": "901", "Payload": None},
            {"dps": {"ph": 621}},
            {"dps": {"10": None}},
            {"dps": {"10": [621]}},
        ],
    )
    def test_rejects_errors_named_dps_and_non_scalar_values(self, payload):
        with pytest.raises(ValidationError):
            TuyaDeviceStatus.model_validate(payload)
