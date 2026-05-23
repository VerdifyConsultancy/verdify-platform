"""Phase 2 unit tests — new Tier A schemas.

Happy path + one rejection each. Drift guards already prove each model's
field set is a valid subset of the live table; these confirm runtime
rejection of bad input.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time

import pytest
from pydantic import ValidationError

from verdify_schemas import (
    ConsumablesLog,
    CropTargetProfile,
    DataGap,
    ESP32LogRow,
    ForecastActionLog,
    ForecastActionRule,
    Harvest,
    HarvestCreate,
    ImageObservation,
    IrrigationLog,
    IrrigationSchedule,
    LabResult,
    MaintenanceLog,
    ObservationAction,
    SensorRegistry,
    Treatment,
    TreatmentCreate,
    UtilityCost,
)

NOW = datetime(2026, 4, 19, 1, 0, tzinfo=UTC)


class TestTreatment:
    def test_valid(self):
        t = Treatment(
            product="Neem oil",
            rate=2.0,
            rate_unit="oz/gal",
            method="foliar spray",
            phi_days=0,
            position_id=4,
            outcome="aphids reduced on follow-up",
        )
        assert t.phi_days == 0
        assert t.position_id == 4

    def test_rejects_empty_product(self):
        with pytest.raises(ValidationError):
            Treatment(product="")

    def test_rejects_negative_phi(self):
        with pytest.raises(ValidationError):
            Treatment(product="x", phi_days=-1)


class TestHarvest:
    def test_valid(self):
        h = Harvest(
            ts=NOW,
            crop_id=3,
            position_id=4,
            weight_kg=2.4,
            unit_count=20,
            quality_grade="A",
            unit_price=3.5,
            revenue=70.0,
        )
        assert h.revenue == 70.0
        assert h.position_id == 4

    def test_rejects_negative_weight(self):
        with pytest.raises(ValidationError):
            Harvest(weight_kg=-1.0)


class TestHarvestCreate:
    def test_valid(self):
        h = HarvestCreate(weight_kg=1.2, unit_count=12, quality_grade="A", operator="jason")
        assert h.operator == "jason"

    def test_rejects_negative_weight(self):
        with pytest.raises(ValidationError):
            HarvestCreate(weight_kg=-0.1)

    def test_rejects_legacy_field_unit_price_usd(self):
        # The live DB column is unit_price; prior MCP code sent unit_price_usd
        # and silently failed at INSERT time. Envelope now rejects it upfront.
        with pytest.raises(ValidationError):
            HarvestCreate(unit_price_usd=3.50)

    def test_rejects_legacy_field_harvested_by(self):
        # DB column is `operator`; old MCP sent `harvested_by`.
        with pytest.raises(ValidationError):
            HarvestCreate(harvested_by="jason")

    def test_rejects_greenhouse_id(self):
        # harvests table has no greenhouse_id column yet.
        with pytest.raises(ValidationError):
            HarvestCreate(greenhouse_id="vallery")


class TestTreatmentCreate:
    def test_valid(self):
        t = TreatmentCreate(product="neem oil", rate=5.0, rate_unit="ml/L", applicator="jason")
        assert t.applicator == "jason"

    def test_rejects_empty_product(self):
        with pytest.raises(ValidationError):
            TreatmentCreate(product="")

    def test_rejects_legacy_field_applied_by(self):
        # DB column is `applicator`; old MCP sent `applied_by`.
        with pytest.raises(ValidationError):
            TreatmentCreate(product="neem", applied_by="jason")

    def test_rejects_greenhouse_id(self):
        with pytest.raises(ValidationError):
            TreatmentCreate(product="neem", greenhouse_id="vallery")


class TestObservationActionEnvelope:
    def test_record_harvest(self):
        a = ObservationAction(
            action="record_harvest",
            crop_id=3,
            data=HarvestCreate(weight_kg=0.8, quality_grade="A", operator="jason"),
        )
        assert isinstance(a.data, HarvestCreate)

    def test_record_treatment(self):
        a = ObservationAction(
            action="record_treatment",
            crop_id=3,
            data=TreatmentCreate(product="neem oil", rate=5.0, rate_unit="ml/L"),
        )
        assert isinstance(a.data, TreatmentCreate)


class TestIrrigationLog:
    def test_valid(self):
        i = IrrigationLog(
            ts=NOW,
            zone="center",
            actual_start=NOW,
            volume_gal=15.0,
        )
        assert i.source == "manual"


class TestIrrigationSchedule:
    def test_valid(self):
        s = IrrigationSchedule(zone="center", start_time=time(6, 0), duration_s=600, days_of_week=[1, 3, 5])
        assert s.enabled is True

    def test_rejects_empty_days(self):
        with pytest.raises(ValidationError):
            IrrigationSchedule(zone="x", start_time=time(6, 0), duration_s=60, days_of_week=[])


class TestLabResult:
    def test_valid(self):
        lab = LabResult(sample_type="tissue", ph=6.2, ec_ms_cm=1.8, n_pct=3.4)
        assert lab.n_pct == 3.4

    def test_rejects_bad_ph(self):
        with pytest.raises(ValidationError):
            LabResult(sample_type="x", ph=20.0)


class TestMaintenanceLog:
    def test_valid(self):
        m = MaintenanceLog(equipment="fan1", service_type="bearing replacement", cost=85.0)
        assert m.cost == 85.0


class TestConsumablesLog:
    def test_valid(self):
        c = ConsumablesLog(
            purchased_date=date(2026, 4, 18),
            category="fertilizer",
            item_name="10-30-20 bloom",
            quantity=50.0,
            unit="lb",
            cost_usd=125.40,
        )
        assert float(c.cost_usd) == 125.40

    def test_rejects_negative_cost(self):
        with pytest.raises(ValidationError):
            ConsumablesLog(
                purchased_date=date(2026, 4, 18),
                category="x",
                item_name="y",
                cost_usd=-5,
            )


class TestCropTargetProfile:
    def test_valid(self):
        p = CropTargetProfile(
            crop_type="tomato",
            growth_stage="fruiting",
            hour_of_day=14,
            season="summer",
            temp_ideal_min=75.0,
            temp_ideal_max=82.0,
            temp_stress_low=60.0,
            temp_stress_high=95.0,
            vpd_ideal_min=0.8,
            vpd_ideal_max=1.2,
            vpd_stress_low=0.4,
            vpd_stress_high=2.0,
        )
        assert p.hour_of_day == 14

    def test_rejects_bad_hour(self):
        with pytest.raises(ValidationError):
            CropTargetProfile(
                crop_type="x",
                hour_of_day=25,
                temp_ideal_min=60.0,
                temp_ideal_max=80.0,
                temp_stress_low=50.0,
                temp_stress_high=90.0,
                vpd_ideal_min=0.5,
                vpd_ideal_max=1.5,
                vpd_stress_low=0.2,
                vpd_stress_high=2.5,
            )


class TestForecastActionRuleAndLog:
    def test_rule(self):
        r = ForecastActionRule(
            name="pre_heat_cold_morning",
            condition="temp < 40F overnight",
            metric="temp_f",
            operator="<",
            threshold=40,
            param="bias_heat",
            adjustment_value=1,
            action_type="setpoint",
        )
        assert r.priority == 50

    def test_log(self):
        log = ForecastActionLog(
            rule_name="pre_heat_cold_morning",
            action_taken="bias_heat +1",
            param="bias_heat",
            old_value=0,
            new_value=1,
        )
        assert log.param == "bias_heat"


class TestUtilityCost:
    def test_valid(self):
        u = UtilityCost(month=date(2026, 3, 1), category="electric", amount_usd=142.30, kwh=1203)
        assert u.category == "electric"


class TestImageObservation:
    def test_valid(self):
        img = ImageObservation(
            ts=NOW,
            camera="greenhouse_1",
            zone="center",
            image_path="/mnt/iris/media/snapshots/greenhouse_1/2026-04-19_0100.jpg",
            model="gemini-2.0-flash",
            confidence=0.87,
        )
        assert img.confidence == 0.87

    def test_rejects_bad_confidence(self):
        with pytest.raises(ValidationError):
            ImageObservation(
                ts=NOW,
                camera="x",
                zone="x",
                image_path="/mnt/iris/x.jpg",
                confidence=1.5,
            )

    def test_accepts_correct_embedding_dim(self):
        from verdify_schemas import EMBEDDING_DIM

        img = ImageObservation(
            ts=NOW,
            camera="x",
            zone="x",
            image_path="/mnt/iris/x.jpg",
            embedding=[0.0] * EMBEDDING_DIM,
        )
        assert len(img.embedding) == EMBEDDING_DIM

    def test_rejects_short_embedding(self):
        from verdify_schemas import EMBEDDING_DIM

        with pytest.raises(ValidationError, match="too_long|too_short|at most|at least"):
            ImageObservation(
                ts=NOW,
                camera="x",
                zone="x",
                image_path="/mnt/iris/x.jpg",
                embedding=[0.0] * (EMBEDDING_DIM - 1),
            )

    def test_rejects_long_embedding(self):
        from verdify_schemas import EMBEDDING_DIM

        with pytest.raises(ValidationError, match="too_long|too_short|at most|at least"):
            ImageObservation(
                ts=NOW,
                camera="x",
                zone="x",
                image_path="/mnt/iris/x.jpg",
                embedding=[0.0] * (EMBEDDING_DIM + 1),
            )

    def test_embedding_optional(self):
        img = ImageObservation(ts=NOW, camera="x", zone="x", image_path="/mnt/iris/x.jpg")
        assert img.embedding is None


class TestSensorRegistry:
    def test_valid(self):
        s = SensorRegistry(
            sensor_id="hydro_ph",
            type="hydroponics",
            source_table="climate",
            source_column="hydro_ph",
            unit="pH",
            expected_interval_s=300,
        )
        assert s.active is True


class TestESP32LogRow:
    def test_valid(self):
        log = ESP32LogRow(ts=NOW, level="INFO", tag="main", message="ESP32 booted")
        assert log.level == "INFO"


class TestDataGap:
    def test_valid(self):
        g = DataGap(start_ts=NOW, end_ts=NOW, duration_s=45)
        assert g.backfill_status == "pending"

    def test_rejects_negative_duration(self):
        with pytest.raises(ValidationError):
            DataGap(start_ts=NOW, end_ts=NOW, duration_s=-5)
