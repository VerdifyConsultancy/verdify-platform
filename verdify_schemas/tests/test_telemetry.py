"""Telemetry row schema tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from verdify_schemas.telemetry import (
    OVERRIDE_EVENT_TYPES,
    ClimateRow,
    Diagnostics,
    EnergySample,
    EquipmentStateEvent,
    OverrideEvent,
    SystemStateRow,
)

NOW = datetime(2026, 4, 19, 1, 0, tzinfo=UTC)


class TestClimateRow:
    def test_minimal(self):
        row = ClimateRow(ts=NOW)
        assert row.greenhouse_id == "vallery"
        assert row.temp_avg is None

    def test_full_sensible_row(self):
        row = ClimateRow(
            ts=NOW,
            temp_avg=72.5,
            rh_avg=55.0,
            vpd_avg=0.8,
            dew_point=55.2,
            lux=820.0,
            solar_irradiance_w_m2=510.0,
            dli_today=7.2,
            air_density_kg_m3=1.02,
            co2_ppm=515.0,
            hydro_ph=6.22,
            hydro_ec_us_cm=2480.0,
            soil_moisture_south_1=38.0,
            soil_temp_south_1=69.5,
            soil_ec_south_1=0.7,
            soil_moisture_south_2=41.0,
            soil_temp_south_2=70.0,
        )
        assert row.hydro_ph == 6.22
        assert row.soil_moisture_south_1 == 38.0

    def test_rejects_out_of_range_rh(self):
        with pytest.raises(ValidationError):
            ClimateRow(ts=NOW, rh_avg=120.0)

    def test_rejects_out_of_range_vpd(self):
        with pytest.raises(ValidationError):
            ClimateRow(ts=NOW, vpd_avg=25.0)

    def test_rejects_negative_flow(self):
        with pytest.raises(ValidationError):
            ClimateRow(ts=NOW, precip_in=-1.0)

    def test_hydroponics_ph_out_of_range(self):
        with pytest.raises(ValidationError):
            ClimateRow(ts=NOW, hydro_ph=15.0)

    def test_rejects_out_of_range_soil_and_air_quality(self):
        with pytest.raises(ValidationError):
            ClimateRow(ts=NOW, soil_moisture_south_1=120.0)
        with pytest.raises(ValidationError):
            ClimateRow(ts=NOW, soil_ec_south_1=-0.1)
        with pytest.raises(ValidationError):
            ClimateRow(ts=NOW, co2_ppm=-1.0)
        with pytest.raises(ValidationError):
            ClimateRow(ts=NOW, air_density_kg_m3=-0.1)

    def test_tolerates_unknown_columns(self):
        row = ClimateRow.model_validate({"ts": NOW, "some_new_column_added_next_month": 123})
        assert row.ts == NOW


class TestDiagnostics:
    def test_valid(self):
        d = Diagnostics(
            ts=NOW,
            wifi_rssi=-47.0,
            heap_bytes=180000.0,
            heap_min_free_kb=17.6,
            heap_largest_free_block_kb=13.0,
            uptime_s=3600.0,
            probe_health="4/4 ok",
            reset_reason="Software reset",
            firmware_version="2026.4.18",
            active_probe_count=4,
            relief_cycle_count=0,
            vent_latch_timer_s=0,
            sealed_timer_s=0,
            vpd_watch_timer_s=60,
            mist_backoff_timer_s=0,
            vent_mist_assist_active=0,
            effective_heat_target_f=68.1,
            effective_cool_stage2_delta_f=3.0,
            effective_vpd_hysteresis_kpa=0.3,
            effective_dehum_aggressive_kpa=0.5,
        )
        assert d.active_probe_count == 4

    def test_rejects_rssi_above_zero(self):
        with pytest.raises(ValidationError):
            Diagnostics(ts=NOW, wifi_rssi=10.0)

    def test_rejects_negative_heap_fragmentation_metric(self):
        with pytest.raises(ValidationError):
            Diagnostics(ts=NOW, heap_largest_free_block_kb=-1.0)

    def test_rejects_probe_count_over_4(self):
        with pytest.raises(ValidationError):
            Diagnostics(ts=NOW, active_probe_count=5)

    def test_rejects_vent_latch_above_1800(self):
        with pytest.raises(ValidationError):
            Diagnostics(ts=NOW, vent_latch_timer_s=5000)

    def test_rejects_vent_mist_assist_above_one(self):
        with pytest.raises(ValidationError):
            Diagnostics(ts=NOW, vent_mist_assist_active=2)

    def test_rejects_negative_effective_control_value(self):
        with pytest.raises(ValidationError):
            Diagnostics(ts=NOW, effective_heat_target_f=-1.0)


class TestEquipmentStateEvent:
    def test_valid_fan1_on(self):
        ev = EquipmentStateEvent(ts=NOW, equipment="fan1", state=True)
        assert ev.state is True

    def test_valid_misting_off(self):
        ev = EquipmentStateEvent(ts=NOW, equipment="mister_south", state=False)
        assert ev.equipment == "mister_south"


class TestEnergySample:
    def test_valid(self):
        e = EnergySample(ts=NOW, watts_total=450.0, watts_fans=120.0, kwh_today=3.2)
        assert e.kwh_today == 3.2

    def test_negative_kwh_rejected(self):
        with pytest.raises(ValidationError):
            EnergySample(ts=NOW, kwh_today=-5.0)


class TestSystemStateRow:
    def test_valid(self):
        s = SystemStateRow(ts=NOW, entity="greenhouse_state", value="SEALED_MIST")
        assert s.value == "SEALED_MIST"


class TestOverrideEvent:
    def test_valid(self):
        ov = OverrideEvent(
            ts=NOW,
            override_type="occupancy_blocks_equipment,fog_gate_rh",
            mode="IDLE",
            details={"rh": 82.0, "occupancy": True},
        )
        assert ov.details["rh"] == 82.0

    def test_accepts_every_known_override_type(self):
        for override_type in OVERRIDE_EVENT_TYPES:
            ov = OverrideEvent(ts=NOW, override_type=override_type)
            assert ov.override_type == override_type

    def test_accepts_comma_separated_known_override_types(self):
        ov = OverrideEvent(ts=NOW, override_type="summer_vent,vent_mist_assist,fog_heat_assist")
        assert ov.override_type == "summer_vent,vent_mist_assist,fog_heat_assist"

    def test_rejects_unknown_override_type(self):
        with pytest.raises(ValidationError, match="Unknown override_type"):
            OverrideEvent(ts=NOW, override_type="summer_vent,renamed_flag")

    def test_rejects_empty_or_none_override_type(self):
        for override_type in ("", "none", " , "):
            with pytest.raises(ValidationError, match="at least one active override flag"):
                OverrideEvent(ts=NOW, override_type=override_type)
