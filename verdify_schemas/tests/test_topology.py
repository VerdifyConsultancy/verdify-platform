"""Phase 1 tests — topology + catalog schemas.

These are pure unit tests (no DB) that validate the ID patterns, required
fields, and cross-module composition of the new topology layer.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from verdify_schemas import (
    CropCatalogCreate,
    CropCatalogEntry,
    CropProfileHour,
    CropStageTarget,
    Equipment,
    EquipmentAlias,
    EquipmentCreate,
    Greenhouse,
    PositionCreate,
    PressureGroupCreate,
    ResourceCoefficient,
    Sensor,
    SensorCreate,
    ShelfCreate,
    SwitchCreate,
    WaterSystemCreate,
    Zone,
    ZoneCreate,
    ZoneUpdate,
)


class TestGreenhouse:
    def test_valid(self):
        g = Greenhouse(
            id="vallery",
            name="Vallery Greenhouse",
            timezone="America/Denver",
            latitude=40.1672,
            longitude=-105.1019,
            elevation_ft=4979,
        )
        assert g.id == "vallery"
        assert g.status == "active"

    def test_rejects_invalid_slug(self):
        with pytest.raises(ValidationError):
            Greenhouse(id="Vallery-1", name="x")  # uppercase + hyphen

    def test_rejects_bad_latitude(self):
        with pytest.raises(ValidationError):
            Greenhouse(id="vallery", name="x", latitude=91.0)


class TestZone:
    def test_valid_create(self):
        z = ZoneCreate(
            greenhouse_id="vallery",
            slug="south",
            name="South Zone",
            orientation="Front-facing",
            sensor_modbus_addr=4,
        )
        assert z.slug == "south"
        assert z.status == "active"

    def test_requires_greenhouse_id_no_default(self):
        # Multi-tenant: greenhouse_id is required, no "vallery" default
        with pytest.raises(ValidationError):
            ZoneCreate(slug="south", name="South Zone")  # type: ignore[call-arg]

    def test_rejects_bad_slug(self):
        with pytest.raises(ValidationError):
            ZoneCreate(greenhouse_id="vallery", slug="South", name="South")

    def test_modbus_addr_range(self):
        with pytest.raises(ValidationError):
            ZoneCreate(
                greenhouse_id="vallery",
                slug="south",
                name="South",
                sensor_modbus_addr=248,
            )

    def test_update_all_optional(self):
        u = ZoneUpdate()
        assert u.name is None
        assert u.status is None

    def test_row_model_allows_id(self):
        z = Zone(
            id=1,
            greenhouse_id="vallery",
            slug="south",
            name="South Zone",
        )
        assert z.id == 1


class TestShelf:
    def test_valid(self):
        s = ShelfCreate(
            greenhouse_id="vallery",
            zone_id=1,
            slug="south_floor",
            name="South Floor",
            kind="floor",
            position_scheme="SOUTH-FLOOR-{N}",
        )
        assert s.kind == "floor"

    def test_rejects_unknown_kind(self):
        with pytest.raises(ValidationError):
            ShelfCreate(
                greenhouse_id="vallery",
                zone_id=1,
                slug="south_floor",
                name="South Floor",
                kind="warp_drive",  # type: ignore[arg-type]
            )


class TestPosition:
    def test_valid_position_labels(self):
        for label in ["SOUTH-FLOOR-1", "CENTER-HANG-2", "SOUTH-SHELF-T1", "EAST-NFT-PORT-12"]:
            p = PositionCreate(
                greenhouse_id="vallery",
                shelf_id=1,
                label=label,  # type: ignore[arg-type]
                mount_type="pot",
            )
            assert p.label == label

    def test_rejects_lowercase_label(self):
        with pytest.raises(ValidationError):
            PositionCreate(
                greenhouse_id="vallery",
                shelf_id=1,
                label="south-floor-1",  # type: ignore[arg-type]
                mount_type="pot",
            )

    def test_rejects_trailing_dash(self):
        with pytest.raises(ValidationError):
            PositionCreate(
                greenhouse_id="vallery",
                shelf_id=1,
                label="SOUTH-FLOOR-",  # type: ignore[arg-type]
                mount_type="pot",
            )

    def test_mount_type_closed_set(self):
        with pytest.raises(ValidationError):
            PositionCreate(
                greenhouse_id="vallery",
                shelf_id=1,
                label="SOUTH-FLOOR-1",  # type: ignore[arg-type]
                mount_type="floating",  # type: ignore[arg-type]
            )


class TestSensor:
    def test_valid(self):
        s = SensorCreate(
            greenhouse_id="vallery",
            slug="climate.south_temp",
            zone_id=1,
            kind="climate_probe",
            protocol="modbus_rtu",
            modbus_addr=4,
            unit="fahrenheit",
            source_table="climate",
            source_column="temp_south",
            expected_interval_s=10,
        )
        assert s.slug == "climate.south_temp"

    def test_dotted_slug_allowed(self):
        s = SensorCreate(
            greenhouse_id="vallery",
            slug="soil.south_moisture",
            kind="soil_probe",
            protocol="modbus_rtu",
        )
        assert "." in s.slug

    def test_row_model(self):
        s = Sensor(
            greenhouse_id="vallery",
            slug="climate.south_temp",
            kind="climate_probe",
            protocol="modbus_rtu",
        )
        assert s.is_active is True


class TestEquipment:
    def test_valid_known_slug(self):
        e = EquipmentCreate(
            greenhouse_id="vallery",
            slug="mister_south",  # From telemetry.EquipmentId Literal
            zone_id=1,
            kind="mister",
            name="South Misters",
            model="Micro Drip 360",
            watts=None,
            cost_per_hour_usd=0.0,
        )
        assert e.slug == "mister_south"
        assert e.kind == "mister"

    def test_rejects_unknown_slug(self):
        # Only telemetry.EquipmentId members are valid
        with pytest.raises(ValidationError):
            EquipmentCreate(
                greenhouse_id="vallery",
                slug="pizza_oven",  # type: ignore[arg-type]
                kind="heater",
                name="x",
            )

    def test_row_model(self):
        e = Equipment(
            greenhouse_id="vallery",
            slug="fan1",
            kind="fan",
            name="Exhaust Fan 1",
        )
        assert e.is_active is True

    def test_alias_and_resource_coefficient_contracts(self):
        now = datetime.now(UTC)
        alias = EquipmentAlias(
            greenhouse_id="vallery",
            alias_slug="gl1",
            equipment_id=1,
            alias_kind="legacy_catalog",
            source="migration_193",
            evidence_ref="issue:#437",
            valid_from=now,
        )
        coefficient = ResourceCoefficient(
            equipment_id=1,
            resource_kind="electric_watts",
            unit="W",
            nominal_value=113,
            lower_bound=102,
            upper_bound=124,
            coefficient_source="meter_fit",
            revision="meter_fit_2026_07_09",
            evidence_ref="issue:#437",
            valid_from=now,
            is_model_default=True,
        )
        assert alias.alias_slug == "gl1"
        assert coefficient.lower_bound < coefficient.nominal_value < coefficient.upper_bound

    def test_resource_coefficient_rejects_false_precision_bounds(self):
        with pytest.raises(ValidationError):
            ResourceCoefficient(
                equipment_id=1,
                resource_kind="electric_watts",
                unit="W",
                nominal_value=113,
                lower_bound=124,
                upper_bound=130,
                coefficient_source="meter_fit",
                revision="bad",
                evidence_ref="fixture",
                valid_from=datetime.now(UTC),
            )


class TestSwitch:
    def test_valid(self):
        s = SwitchCreate(
            greenhouse_id="vallery",
            slug="pcf_out_1.3",
            equipment_id=1,
            board="pcf_out_1",
            pin=3,
            purpose="South misters (clean)",
        )
        assert s.slug == "pcf_out_1.3"
        assert s.pin == 3

    def test_rejects_slug_without_pin(self):
        with pytest.raises(ValidationError):
            SwitchCreate(
                greenhouse_id="vallery",
                slug="pcf_out_1",  # missing .pin
                board="pcf_out_1",
                pin=3,
                purpose="x",
            )

    def test_pin_range(self):
        with pytest.raises(ValidationError):
            SwitchCreate(
                greenhouse_id="vallery",
                slug="pcf_out_1.16",
                board="pcf_out_1",
                pin=16,  # PCF8574 has 8 pins (0-7); we allow 0-15 for future boards
                purpose="x",
            )

    def test_rejects_unknown_board(self):
        with pytest.raises(ValidationError):
            SwitchCreate(
                greenhouse_id="vallery",
                slug="pcf_out_9.0",
                board="pcf_out_9",  # type: ignore[arg-type]
                pin=0,
                purpose="x",
            )


class TestWaterSystemAndPressureGroup:
    def test_pressure_group_valid(self):
        p = PressureGroupCreate(
            greenhouse_id="vallery",
            slug="mister_manifold",
            name="Mister pressure manifold",
            constraint="mister_max_1",
            max_concurrent=1,
            description="Solenoid manifold: only one mister zone firing at a time",
        )
        assert p.constraint == "mister_max_1"

    def test_water_system_valid(self):
        ws = WaterSystemCreate(
            greenhouse_id="vallery",
            slug="south_mister_clean",
            zone_id=1,
            equipment_id=1,
            pressure_group_id=1,
            kind="mister",
            name="South Misters (clean)",
            nozzle_count=30,
            head_count=6,
            mount="Wall-mounted, 2 rows",
        )
        assert ws.is_fert_path is False

    def test_fert_path_flag(self):
        ws = WaterSystemCreate(
            greenhouse_id="vallery",
            slug="south_mister_fert",
            kind="fertigation",
            name="x",
            is_fert_path=True,
        )
        assert ws.is_fert_path is True


class TestCropCatalog:
    def test_valid(self):
        c = CropCatalogCreate(
            slug="tomatoes",
            common_name="Tomatoes",
            scientific_name="Solanum lycopersicum",
            category="fruit",
            season="warm",
            cycle_days_min=70,
            cycle_days_max=90,
            default_target_vpd_low=0.8,
            default_target_vpd_high=1.2,
            default_target_dli=25.0,
        )
        assert c.slug == "tomatoes"

    def test_rejects_invalid_category(self):
        with pytest.raises(ValidationError):
            CropCatalogCreate(
                slug="tomatoes",
                common_name="Tomatoes",
                category="alien_fruit",  # type: ignore[arg-type]
                season="warm",
            )

    def test_row_with_id(self):
        e = CropCatalogEntry(
            id=1,
            slug="tomatoes",
            common_name="Tomatoes",
            category="fruit",
            season="warm",
        )
        assert e.id == 1

    def test_vpd_range(self):
        with pytest.raises(ValidationError):
            CropCatalogCreate(
                slug="tomatoes",
                common_name="Tomatoes",
                category="fruit",
                season="warm",
                default_target_vpd_high=25.0,  # > 20 ceiling
            )


class TestCropProfileHour:
    def test_valid(self):
        p = CropProfileHour(
            greenhouse_id="vallery",
            crop_catalog_id=1,
            crop_type="tomatoes",
            growth_stage="vegetative",
            hour_of_day=12,
            season="warm",
            temp_ideal_min=70.0,
            temp_ideal_max=85.0,
            temp_stress_low=60.0,
            temp_stress_high=95.0,
            vpd_ideal_min=0.8,
            vpd_ideal_max=1.2,
            vpd_stress_low=0.4,
            vpd_stress_high=1.8,
        )
        assert p.hour_of_day == 12

    def test_hour_of_day_range(self):
        with pytest.raises(ValidationError):
            CropProfileHour(
                greenhouse_id="vallery",
                crop_type="tomatoes",
                hour_of_day=24,
                temp_ideal_min=70.0,
                temp_ideal_max=85.0,
                temp_stress_low=60.0,
                temp_stress_high=95.0,
                vpd_ideal_min=0.8,
                vpd_ideal_max=1.2,
                vpd_stress_low=0.4,
                vpd_stress_high=1.8,
            )

    def test_catalog_id_nullable_during_backfill(self):
        # Phase 3 imports rows before backfill fills crop_catalog_id — nullable
        p = CropProfileHour(
            greenhouse_id="vallery",
            crop_type="orphan_crop",
            hour_of_day=0,
            temp_ideal_min=60.0,
            temp_ideal_max=80.0,
            temp_stress_low=50.0,
            temp_stress_high=90.0,
            vpd_ideal_min=0.5,
            vpd_ideal_max=1.5,
            vpd_stress_low=0.2,
            vpd_stress_high=2.0,
        )
        assert p.crop_catalog_id is None


class TestCropStageTarget:
    def test_valid(self):
        t = CropStageTarget(
            crop_catalog_id=1,
            crop_slug="tomatoes",
            growth_stage="fruiting",
            season="warm",
            temp_ideal_min_24h=70.0,
            temp_ideal_max_24h=85.0,
            vpd_ideal_min_24h=0.8,
            vpd_ideal_max_24h=1.2,
            dli_target_mol=25.0,
            hours_covered=24,
        )
        assert t.hours_covered == 24

    def test_hours_capped_at_24(self):
        with pytest.raises(ValidationError):
            CropStageTarget(
                crop_catalog_id=1,
                crop_slug="tomatoes",
                growth_stage="fruiting",
                season="warm",
                temp_ideal_min_24h=70.0,
                temp_ideal_max_24h=85.0,
                vpd_ideal_min_24h=0.8,
                vpd_ideal_max_24h=1.2,
                hours_covered=25,
            )


class TestCrossModuleComposition:
    """Confirm that CropCreate / CropUpdate accept the new FK fields without
    breaking their existing string-field shape."""

    def test_crop_create_accepts_fk_fields(self):
        from verdify_schemas import CropCreate

        c = CropCreate(
            name="tomatoes",
            position="SOUTH-FLOOR-1",
            zone="south",
            planted_date="2026-04-01",  # type: ignore[arg-type]
            position_id=42,
            zone_id=1,
            crop_catalog_id=3,
            position_label="SOUTH-FLOOR-1",
            zone_slug="south",
            crop_catalog_slug="tomatoes",
        )
        assert c.position_id == 42
        assert c.zone_slug == "south"

    def test_crop_create_still_works_without_fk_fields(self):
        """During migration, legacy callers without FK fields must still succeed."""
        from verdify_schemas import CropCreate

        c = CropCreate(
            name="basil",
            position="EAST-NFT-1",
            zone="east",
            planted_date="2026-04-01",  # type: ignore[arg-type]
        )
        assert c.position_id is None
        assert c.zone_id is None
