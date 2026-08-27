"""
Verdify Crop Catalog API — FastAPI backend for crop management.

Endpoints: crops CRUD, observations, events, health trends, zones.
Runs on port 8300 (internal network only).

Usage:
    uvicorn main:app --host 0.0.0.0 --port 8300
"""

import asyncio
import datetime as dt
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import smtplib
import sys
import time
import urllib.parse
import urllib.request
import uuid
from collections.abc import Mapping
from contextlib import asynccontextmanager
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path
from typing import Annotated, Literal

import asyncpg
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

log = logging.getLogger(__name__)


def _coerce_jsonb(row_dict: dict, *keys: str) -> dict:
    """asyncpg returns JSONB columns as strings unless a codec is registered.
    Parse the named keys from str → list/dict so response_model validation works.
    """
    for k in keys:
        v = row_dict.get(k)
        if isinstance(v, str):
            row_dict[k] = json.loads(v)
    return row_dict


def _round_half_up(value: float, precision: int) -> float:
    """Match PostgreSQL numeric round() for setpoint values sent to firmware."""
    quantum = Decimal("1").scaleb(-precision)
    return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP))


# verdify_schemas is mounted at /app/verdify_schemas inside the container
# (see docker-compose.yml api.volumes). Host-side dev runs should prefer the
# current worktree before the deployed /mnt/iris/verdify checkout.
_REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in reversed(("/app", str(_REPO_ROOT), "/mnt/iris/verdify")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from verdify_public.build_provenance import image_git_sha  # noqa: E402
from verdify_public.output_policy import (  # noqa: E402
    PUBLIC_CROP_EXCLUDE_SLUGS,
    PUBLIC_CROP_SQL_NAME_PATTERN,
    is_public_crop,
    is_public_crop_record,
    public_crop_sql_predicate,
    public_crop_zone_joins,
    public_crop_zone_predicate,
    redact_non_public_crop_references,
    redact_public_data,
)
from verdify_schemas import (  # noqa: E402
    AlertEnvelope,
    APIStatus,
    CropCreate,
    CropDetail,
    CropHealthSummaryItem,
    CropHistoryEntry,
    CropLifecycle,
    CropListItem,
    CropUpdate,
    EventCreate,
    HealthTrendPoint,
    ObservationCreate,
    ObservationWithCrop,
    PositionCurrentEntry,
    PublicBandTraceLatest,
    PublicBandTraceResponse,
    PublicBandTraceSummary,
    PublicDataHealthCheck,
    PublicDataHealthResponse,
    PublicGpuPowerLatest,
    PublicGpuPowerPoint,
    PublicGpuPowerResponse,
    PublicHomeMetrics,
    PublicInfraCpuLatest,
    PublicInfraCpuPoint,
    PublicPipelineHealthSource,
    PublicPlannerDelivery,
    PublicPlannerHealthResponse,
    PublicPlannerTrigger,
    ZoneDetail,
    ZoneListItem,
)
from verdify_schemas.experiment_config import (  # noqa: E402
    active_experiment_id,
    component_experiment_gate,
    component_experiment_mode,
)
from verdify_schemas.mcp_responses import ScorecardResponse  # noqa: E402
from verdify_schemas.telemetry import DliEvidence  # noqa: E402
from verdify_schemas.tunable_registry import (  # noqa: E402
    CROP_BAND_REG,
    LEGACY_SHARED_LIGHTING_REG,
    SETPOINT_MAP_REG,
    registry_value_error,
)

FIRMWARE_SETPOINT_PARAMS = frozenset(SETPOINT_MAP_REG.values())
HOUSE_BAND_COMPUTED_PARAMS = frozenset(
    name for name in CROP_BAND_REG if name.startswith("temp_") or name in {"vpd_high", "vpd_low"}
)
ZONE_VPD_TARGET_PARAMS = frozenset(name for name in CROP_BAND_REG if name.startswith("vpd_target_"))
LEGACY_LIGHTING_COMPUTED_PARAMS = LEGACY_SHARED_LIGHTING_REG & FIRMWARE_SETPOINT_PARAMS
PUBLIC_CROP_EXCLUDE_SLUGS_DB = sorted(PUBLIC_CROP_EXCLUDE_SLUGS)
PUBLIC_CROP_FIELDS = (
    "id",
    "name",
    "variety",
    "position",
    "zone",
    "planted_date",
    "expected_harvest",
    "stage",
    "count",
    "seed_lot_id",
    "supplier",
    "base_temp_f",
    "target_dli",
    "target_vpd_low",
    "target_vpd_high",
    "notes",
    "is_active",
    "created_at",
    "updated_at",
    "greenhouse_id",
)
PUBLIC_CROP_RESPONSE_FIELDS = (*PUBLIC_CROP_FIELDS, "latest_health")
PUBLIC_OBSERVATION_FIELDS = (
    "id",
    "ts",
    "obs_type",
    "zone",
    "position",
    "severity",
    "species",
    "count",
    "affected_pct",
    "crop_id",
    "source",
    "notes",
    "health_score",
    "greenhouse_id",
    "position_id",
    "zone_id",
    "plant_height_cm",
    "leaf_count",
    "canopy_cover_pct",
    "flowering_count",
    "fruit_count",
    "root_condition",
    "mortality_count",
    "stress_tags",
)
PUBLIC_EVENT_FIELDS = (
    "id",
    "ts",
    "crop_id",
    "event_type",
    "old_stage",
    "new_stage",
    "count",
    "source",
    "notes",
    "greenhouse_id",
    "position_id",
)
PUBLIC_POSITION_FIELDS = (
    "position_id",
    "greenhouse_id",
    "position_label",
    "shelf_slug",
    "shelf_kind",
    "zone_id",
    "zone_slug",
    "zone_name",
    "crop_id",
    "crop_name",
    "crop_variety",
    "crop_stage",
    "crop_planted_date",
    "crop_expected_harvest",
    "crop_catalog_slug",
    "crop_days_in_place",
    "is_occupied",
)
PUBLIC_CROP_HISTORY_FIELDS = (
    "position_id",
    "greenhouse_id",
    "position_label",
    "zone_slug",
    "crop_id",
    "crop_name",
    "crop_variety",
    "final_stage",
    "planted_date",
    "cleared_at",
    "is_active",
    "days_in_place",
    "crop_catalog_slug",
    "crop_common_name",
    "event_count",
    "observation_count",
    "harvest_count",
)
PUBLIC_CROP_LIFECYCLE_FIELDS = (
    "crop_id",
    "greenhouse_id",
    "crop_name",
    "variety",
    "current_stage",
    "is_active",
    "planted_date",
    "cleared_at",
    "days_alive",
    "current_zone_slug",
    "current_position_label",
    "crop_catalog_slug",
    "catalog_name",
    "catalog_category",
    "events",
    "total_weight_kg",
    "total_units",
    "total_revenue_usd",
    "observation_count",
    "avg_health_score",
    "latest_observation_ts",
)
PUBLIC_CROP_LIFECYCLE_EVENT_FIELDS = (
    "ts",
    "event_type",
    "old_stage",
    "new_stage",
    "position_id",
    "notes",
    "source",
)
PUBLIC_CATALOG_FIELDS = (
    "crop_catalog_id",
    "slug",
    "common_name",
    "scientific_name",
    "category",
    "season",
    "cycle_days_min",
    "cycle_days_max",
    "base_temp_f",
    "default_target_dli",
    "default_target_vpd_low",
    "default_target_vpd_high",
    "default_ph_low",
    "default_ph_high",
    "default_ec_low",
    "default_ec_high",
    "stage_season_profiles",
)
PUBLIC_CATALOG_PROFILE_FIELDS = (
    "growth_stage",
    "season",
    "hours_covered",
    "temp_ideal_min_24h",
    "temp_ideal_max_24h",
    "vpd_ideal_min_24h",
    "vpd_ideal_max_24h",
    "dli_target_mol",
)
PUBLIC_CATALOG_HOURLY_FIELDS = (
    "growth_stage",
    "hour_of_day",
    "season",
    "temp_ideal_min",
    "temp_ideal_max",
    "temp_stress_low",
    "temp_stress_high",
    "vpd_ideal_min",
    "vpd_ideal_max",
    "vpd_stress_low",
    "vpd_stress_high",
    "dli_target_mol",
    "source",
)
PUBLIC_WATER_RESOURCE_FIELDS = (
    "date",
    "greenhouse_id",
    "quality_filtered_meter_gal",
    "attributed_gal",
    "climate_wetting_gal",
    "wall_irrigation_gal",
    "wall_fertigation_gal",
    "unsupported_path_gal",
    "ambiguous_gal",
    "manual_or_unattributed_gal",
    "command_only_runs",
    "ambiguous_runs",
    "meter_attributed_runs",
    "conservation_error_gal",
    "ledger_quality",
    "resource_quality",
    "available_for_scoring",
)
PUBLIC_ENERGY_RESOURCE_FIELDS = (
    "date",
    "kwh_estimated",
    "measured_kwh",
    "estimate_delta_kwh",
    "quality_flag",
    "greenhouse_id",
    "modeled_kwh_low",
    "modeled_kwh_high",
    "coefficient_revisions",
    "modeled_scope",
    "measured_scope",
    "meter_coverage_pct",
    "runtime_coverage_pct",
    "model_quality",
    "measured_quality",
    "modeled_available_for_scoring",
    "measured_available_for_scoring",
    "runtime_evidence",
)
PUBLIC_WATER_LEDGER_HEALTH_FIELDS = (
    "greenhouse_id",
    "raw_latest_ts",
    "materialized_through_ts",
    "raw_age_seconds",
    "materializer_lag_seconds",
    "last_total_gal",
    "last_event_quality",
    "latest_gap_ts",
    "ledger_status",
    "available_for_scoring",
    "latest_discontinuity_ts",
)
PUBLIC_RESOURCE_HEALTH_FIELDS = (
    "resource",
    "greenhouse_id",
    "quality",
    "available_for_scoring",
    "observed_through",
    "detail",
)
PUBLIC_PLANNER_HEALTH_FIELDS = (
    "generated_at",
    "missed_expected_count",
    "overdue_delivered_count",
    "required_failure_count",
    "resolved_count",
    "recent_expected_count",
    "latest_required",
)
PUBLIC_PLANNER_REQUIRED_FIELDS = (
    "event_type",
    "event_label",
    "instance",
    "expected_at",
    "due_at",
    "delivered_at",
    "resolved_at",
    "status",
    "resulting_plan_id",
    "trigger_id",
)
PUBLIC_EQUIPMENT_FIELDS = (
    "id",
    "greenhouse_id",
    "slug",
    "kind",
    "name",
    "model",
    "watts",
    "cost_per_hour_usd",
    "specs",
    "is_active",
    "zone_slug",
)
PUBLIC_EQUIPMENT_SPEC_FIELDS = ("telemetry_slug",)
PUBLIC_SWITCH_FIELDS = (
    "greenhouse_id",
    "board",
    "pin",
    "switch_slug",
    "equipment_slug",
    "equipment_name",
    "equipment_kind",
    "model",
    "zone_slug",
    "zone_name",
    "purpose",
    "state_source_column",
    "is_active",
)
PUBLIC_SENSOR_FIELDS = (
    "id",
    "greenhouse_id",
    "slug",
    "position_id",
    "kind",
    "protocol",
    "model",
    "modbus_addr",
    "gpio_pin",
    "unit",
    "source_table",
    "source_column",
    "expected_interval_s",
    "accuracy",
    "installed_date",
    "is_active",
    "zone_slug",
)
PUBLIC_PRESSURE_GROUP_FIELDS = (
    "pressure_group_id",
    "greenhouse_id",
    "group_slug",
    "group_name",
    "constraint_kind",
    "max_concurrent",
    "systems",
)
PUBLIC_RELAY_TRUTH_FIELDS = frozenset(
    {
        "heat1",
        "heat2",
        "fan1",
        "fan2",
        "fog",
        "vent",
        "grow_light_main",
        "grow_light_grow",
        "mister_south",
        "mister_west",
        "mister_center",
        "mister_south_fert",
        "mister_west_fert",
        "drip_wall",
        "drip_center",
        "drip_wall_fert",
        "drip_center_fert",
        "fert_master_valve",
    }
)
PUBLIC_SENSOR_STATUS_FIELDS = frozenset(
    {
        "latest_climate_ts",
        "latest_climate_age_s",
        "temp_avg_present",
        "vpd_avg_present",
        "band_context_complete",
    }
)
PUBLIC_TOPOLOGY_ZONE_FIELDS = ("zone_id", "slug", "name", "status", "shelves")
PUBLIC_TOPOLOGY_SHELF_FIELDS = ("shelf_id", "slug", "name", "kind", "positions")
PUBLIC_TOPOLOGY_POSITION_FIELDS = ("position_id", "label", "mount_type", "is_active")
PUBLIC_ZONE_FULL_FIELDS = (
    "zone_id",
    "greenhouse_id",
    "zone_slug",
    "zone_name",
    "orientation",
    "sensor_modbus_addr",
    "peak_temp_f",
    "zone_status",
    "zone_notes",
    "shelves",
    "sensors",
    "equipment",
    "water_systems",
    "active_crops_fk_count",
)
PUBLIC_ZONE_SHELF_FIELDS = ("id", "slug", "name", "kind", "tier", "position_scheme")
PUBLIC_ZONE_SENSOR_FIELDS = (
    "id",
    "slug",
    "kind",
    "protocol",
    "model",
    "modbus_addr",
    "source_table",
    "source_column",
    "unit",
    "is_active",
)
PUBLIC_ZONE_EQUIPMENT_FIELDS = (
    "id",
    "slug",
    "kind",
    "name",
    "model",
    "watts",
    "cost_per_hour_usd",
    "is_active",
)
PUBLIC_ZONE_WATER_SYSTEM_FIELDS = (
    "id",
    "slug",
    "kind",
    "name",
    "nozzle_count",
    "head_count",
    "mount",
    "pressure_group_id",
    "is_fert_path",
)
PUBLIC_PRESSURE_SYSTEM_FIELDS = (
    "water_system_slug",
    "water_system_kind",
    "equipment_slug",
    "zone_slug",
    "is_on",
)
PUBLIC_COEFFICIENT_REVISION_FIELDS = (
    "equipment",
    "revision",
    "source",
    "low",
    "nominal",
    "high",
    "unit",
    "evidence_ref",
)
PUBLIC_RUNTIME_EVIDENCE_FIELDS = (
    "equipment",
    "quality",
    "complete_day",
    "start_state_known",
    "eligible",
)
PUBLIC_RESOURCE_HEALTH_DETAIL_FIELDS = (
    "raw_latest_ts",
    "raw_age_seconds",
    "materializer_lag_seconds",
    "latest_gap_ts",
    "latest_discontinuity_ts",
    "modeled_kwh",
    "modeled_kwh_low",
    "modeled_kwh_high",
    "runtime_coverage_pct",
    "scope",
    "coefficient_revisions",
    "runtime_evidence",
    "measured_kwh",
    "meter_coverage_pct",
    "sample_count",
    "current_meter_status",
    "sample_age_seconds",
    "recent_sample_count",
    "completed_day_quality",
)


def _sql_columns(alias: str, fields: tuple[str, ...]) -> str:
    return ", ".join(f"{alias}.{field}" for field in fields)


def _project_public_record(row: object, fields: tuple[str, ...] | frozenset[str]) -> dict:
    record = dict(row)
    return redact_public_data({field: record[field] for field in sorted(fields) if field in record})


def _json_list(value: object) -> list:
    if isinstance(value, str):
        value = json.loads(value)
    return value if isinstance(value, list) else []


def _project_json_records(value: object, fields: tuple[str, ...]) -> list[dict]:
    return [_project_public_record(item, fields) for item in _json_list(value) if isinstance(item, dict)]


def _project_topology_zones(value: object) -> list[dict]:
    zones: list[dict] = []
    for zone_value in _json_list(value):
        if not isinstance(zone_value, dict):
            continue
        zone = _project_public_record(zone_value, PUBLIC_TOPOLOGY_ZONE_FIELDS)
        shelves: list[dict] = []
        for shelf_value in _json_list(zone_value.get("shelves")):
            if not isinstance(shelf_value, dict):
                continue
            shelf = _project_public_record(shelf_value, PUBLIC_TOPOLOGY_SHELF_FIELDS)
            shelf["positions"] = _project_json_records(
                shelf_value.get("positions"),
                PUBLIC_TOPOLOGY_POSITION_FIELDS,
            )
            shelves.append(shelf)
        zone["shelves"] = shelves
        zones.append(zone)
    return zones


def _project_zone_full(row: object) -> dict:
    record = dict(row)
    result = _project_public_record(record, PUBLIC_ZONE_FULL_FIELDS)
    result["shelves"] = _project_json_records(record.get("shelves"), PUBLIC_ZONE_SHELF_FIELDS)
    result["sensors"] = _project_json_records(record.get("sensors"), PUBLIC_ZONE_SENSOR_FIELDS)
    result["equipment"] = _project_json_records(record.get("equipment"), PUBLIC_ZONE_EQUIPMENT_FIELDS)
    result["water_systems"] = _project_json_records(
        record.get("water_systems"),
        PUBLIC_ZONE_WATER_SYSTEM_FIELDS,
    )
    return result


def _project_catalog_record(row: object) -> dict:
    record = dict(row)
    result = _project_public_record(record, PUBLIC_CATALOG_FIELDS)
    result["stage_season_profiles"] = _project_json_records(
        record.get("stage_season_profiles"),
        PUBLIC_CATALOG_PROFILE_FIELDS,
    )
    return result


def _project_crop_lifecycle(row: object) -> dict:
    record = dict(row)
    result = _project_public_record(record, PUBLIC_CROP_LIFECYCLE_FIELDS)
    result["events"] = _project_json_records(
        record.get("events"),
        PUBLIC_CROP_LIFECYCLE_EVENT_FIELDS,
    )
    return result


def _project_energy_resource(row: object) -> dict:
    record = dict(row)
    result = _project_public_record(record, PUBLIC_ENERGY_RESOURCE_FIELDS)
    result["coefficient_revisions"] = _project_json_records(
        record.get("coefficient_revisions"),
        PUBLIC_COEFFICIENT_REVISION_FIELDS,
    )
    result["runtime_evidence"] = _project_json_records(
        record.get("runtime_evidence"),
        PUBLIC_RUNTIME_EVIDENCE_FIELDS,
    )
    return result


def _project_resource_health(row: object) -> dict:
    record = dict(row)
    result = _project_public_record(record, PUBLIC_RESOURCE_HEALTH_FIELDS)
    result["detail"] = _project_resource_health_detail(record.get("detail"))
    return result


def _project_planner_health(row: object) -> dict:
    record = dict(row)
    result = _project_public_record(record, PUBLIC_PLANNER_HEALTH_FIELDS)
    result["latest_required"] = _project_json_records(
        record.get("latest_required"),
        PUBLIC_PLANNER_REQUIRED_FIELDS,
    )
    return result


def _project_equipment(row: object) -> dict:
    record = dict(row)
    result = _project_public_record(record, PUBLIC_EQUIPMENT_FIELDS)
    specs = record.get("specs")
    if isinstance(specs, str):
        specs = json.loads(specs)
    result["specs"] = _project_public_record(specs, PUBLIC_EQUIPMENT_SPEC_FIELDS) if isinstance(specs, dict) else {}
    return result


def _project_pressure_group(row: object) -> dict:
    record = dict(row)
    result = _project_public_record(record, PUBLIC_PRESSURE_GROUP_FIELDS)
    result["systems"] = _project_json_records(record.get("systems"), PUBLIC_PRESSURE_SYSTEM_FIELDS)
    return result


def _project_resource_health_detail(value: object) -> dict:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        return {}
    result = _project_public_record(value, PUBLIC_RESOURCE_HEALTH_DETAIL_FIELDS)
    result["coefficient_revisions"] = _project_json_records(
        value.get("coefficient_revisions"),
        PUBLIC_COEFFICIENT_REVISION_FIELDS,
    )
    result["runtime_evidence"] = _project_json_records(
        value.get("runtime_evidence"),
        PUBLIC_RUNTIME_EVIDENCE_FIELDS,
    )
    return result


def _public_crop_sql_predicate(
    slug_expression: str,
    name_expression: str,
    slug_parameter: int,
    name_parameter: int,
) -> str:
    """SQL half of the shared fail-closed record policy, before pagination."""
    return public_crop_sql_predicate(slug_expression, name_expression, slug_parameter, name_parameter)


def _public_crop_sql_parameters() -> tuple[list[str], str]:
    return PUBLIC_CROP_EXCLUDE_SLUGS_DB, PUBLIC_CROP_SQL_NAME_PATTERN


def _public_crop_zone_sql_predicate(
    zone_expression: str,
    slug_expression: str,
    name_expression: str,
    slug_parameter: int,
    name_parameter: int,
    *,
    crop_alias: str = "c",
) -> str:
    """Use one zone identity and protected-record predicate at every zone surface."""
    return public_crop_zone_predicate(
        zone_expression,
        slug_expression,
        name_expression,
        slug_parameter,
        name_parameter,
        crop_alias=crop_alias,
    )


ACTIVITY_MIRROR_PARAMS = frozenset({"activity_start_hour", "activity_start_minute", "activity_duration_min"})
EQUIPMENT_SWITCH_SETPOINTS = {
    "sw_economiser_enabled": "economiser_enabled",
    "sw_fog_closes_vent": "fog_closes_vent",
    "sw_irrigation_enabled": "irrigation_enabled",
    "sw_irrigation_wall_enabled": "irrigation_wall_enabled",
    "sw_irrigation_center_enabled": "irrigation_center_enabled",
    "sw_irrigation_weather_skip": "irrigation_weather_skip",
    "sw_gl_auto_mode": "gl_auto_mode",
}
DIRECT_WET_DEFAULTS = {
    "direct_wet_min_temp_f": 65,
    "direct_wet_wall_start_offset_min": 60,
    "direct_wet_wall_drydown_before_off_min": 120,
    "direct_wet_south_start_offset_min": 60,
    "direct_wet_south_drydown_before_off_min": 120,
    "direct_wet_west_start_offset_min": 60,
    "direct_wet_west_drydown_before_off_min": 120,
    "direct_wet_center_start_offset_min": 120,
    "direct_wet_center_drydown_before_off_min": 180,
    "irrig_wall_days_mask": 127,
    "irrig_wall_fert_days_mask": 127,
    "irrig_center_days_mask": 127,
    "irrig_center_fert_days_mask": 127,
    "sw_direct_wet_gate_enabled": 1,
}

_POSITION_CROP_FIELDS = (
    "crop_id",
    "crop_name",
    "crop_variety",
    "crop_stage",
    "crop_planted_date",
    "crop_expected_harvest",
    "crop_catalog_slug",
    "crop_days_in_place",
)


def _sanitize_public_position(row: object) -> dict:
    """Preserve empty/public positions while hiding fail-closed crop occupancy."""
    result = _project_public_record(row, PUBLIC_POSITION_FIELDS)
    occupied = bool(result.get("is_occupied"))
    public_occupancy = occupied and is_public_crop_record(
        result.get("crop_catalog_slug"),
        result.get("crop_name"),
        occupied=occupied,
    )
    if public_occupancy:
        return redact_public_data(result)
    for key in _POSITION_CROP_FIELDS:
        result[key] = None
    result["is_occupied"] = False
    return redact_public_data(result)


def _public_crop_rows(
    rows: object,
    *,
    slug_key: str = "crop_catalog_slug",
    name_key: str = "name",
    fields: tuple[str, ...] = PUBLIC_CROP_FIELDS,
) -> list[dict]:
    public_rows: list[dict] = []
    for row in rows:
        record = dict(row)
        if is_public_crop_record(record.get(slug_key), record.get(name_key), occupied=True):
            public_rows.append(_project_public_record(record, fields))
    return public_rows


def _public_crop_history_rows(rows: object) -> list[dict]:
    return _public_crop_rows(rows, name_key="crop_name", fields=PUBLIC_CROP_HISTORY_FIELDS)


def _public_observation_rows(rows: object) -> list[dict]:
    """Keep truly crop-less observations; fail closed for crop-linked rows."""
    public_rows: list[dict] = []
    for row in rows:
        record = dict(row)
        if record.get("crop_id") is None or is_public_crop_record(
            record.get("crop_catalog_slug"),
            record.get("crop_name"),
            occupied=True,
        ):
            public_rows.append(_project_public_record(record, (*PUBLIC_OBSERVATION_FIELDS, "crop_name", "crop_zone")))
    return public_rows


async def _require_public_crop(conn, crop_id: int, greenhouse_id: str | None = None) -> None:
    """Return 404 for missing, excluded, or identity-less crop records."""
    sql = f"""
        SELECT cc.slug AS crop_catalog_slug, c.name
        FROM crops c
        JOIN crop_catalog cc ON cc.id = c.crop_catalog_id
        WHERE c.id = $1
          AND {_public_crop_sql_predicate("cc.slug", "c.name", 2, 3)}
    """
    args: list[object] = [crop_id, *_public_crop_sql_parameters()]
    if greenhouse_id is not None:
        sql += " AND c.greenhouse_id = $4"
        args.append(greenhouse_id)
    row = await conn.fetchrow(sql, *args)
    if row is None or not is_public_crop_record(row["crop_catalog_slug"], row["name"], occupied=True):
        raise HTTPException(404, "Crop not found")


def _activity_policy_values(lighting_row, lighting_circuit_rows) -> dict[str, float | int]:
    """Derive greenhouse biological activity from the main-light runtime policy."""
    main_lighting = next((row for row in lighting_circuit_rows or [] if row["light_key"] == "main"), None)
    if main_lighting:
        activity_start_hour = int(main_lighting["start_hour"])
        activity_duration_min = int(main_lighting["target_light_minutes"])
    elif lighting_row:
        activity_start_hour = int(lighting_row["sunrise_hour"])
        activity_duration_min = int(lighting_row["target_light_hours"]) * 60
    else:
        return {}

    activity_duration_min = max(0, min(1440, activity_duration_min))
    return {
        "activity_start_hour": max(0, min(23, activity_start_hour)),
        "activity_start_minute": 0,
        "activity_duration_min": activity_duration_min,
        "direct_wet_min_temp_f": 65,
        "direct_wet_wall_start_offset_min": 60,
        "direct_wet_wall_drydown_before_off_min": 120,
        "direct_wet_south_start_offset_min": 60,
        "direct_wet_south_drydown_before_off_min": 120,
        "direct_wet_west_start_offset_min": 60,
        "direct_wet_west_drydown_before_off_min": 120,
        "direct_wet_center_start_offset_min": 120,
        "direct_wet_center_drydown_before_off_min": 180,
        "irrig_wall_days_mask": 127,
        "irrig_wall_fert_days_mask": 127,
        "irrig_center_days_mask": 127,
        "irrig_center_fert_days_mask": 127,
        "sw_direct_wet_gate_enabled": 1,
    }


def _align_activity_policy_with_plan(
    activity_values: dict[str, float | int],
    plan_values: dict[str, float],
) -> dict[str, float | int]:
    """Keep pull-fallback activity defaults tied to active main-light overrides."""
    if not activity_values:
        return activity_values
    aligned = dict(activity_values)
    if "gl_main_sunrise_hour" in plan_values:
        aligned["activity_start_hour"] = max(0, min(23, int(plan_values["gl_main_sunrise_hour"])))
    if "gl_main_target_light_minutes" in plan_values:
        aligned["activity_duration_min"] = max(0, min(1440, int(plan_values["gl_main_target_light_minutes"])))
    return aligned


# ── DB Connection ──

API_RUNTIME_DB_ROLE_REQUIRED_ENV = "VERDIFY_API_RUNTIME_DB_ROLE_REQUIRED"
API_RUNTIME_DB_LOGIN = "verdify_api_runtime_login"


def _api_runtime_db_role_required() -> bool:
    return os.environ.get(API_RUNTIME_DB_ROLE_REQUIRED_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def get_db_dsn():
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        if _api_runtime_db_role_required():
            try:
                database_user = urllib.parse.unquote(urllib.parse.urlsplit(database_url).username or "")
            except ValueError as exc:
                raise RuntimeError("runtime-role cutover rejects malformed DATABASE_URL") from exc
            if not hmac.compare_digest(database_user, API_RUNTIME_DB_LOGIN):
                raise RuntimeError("runtime-role cutover rejects DATABASE_URL with a non-runtime login")
        return database_url
    host = os.environ.get("DB_HOST", "localhost")
    name = os.environ.get("DB_NAME", "verdify")
    user = os.environ.get("DB_USER", "verdify")
    if _api_runtime_db_role_required() and not hmac.compare_digest(user, API_RUNTIME_DB_LOGIN):
        raise RuntimeError("runtime-role cutover requires the exact API database login")
    pw = os.environ.get("DB_PASS", "verdify")
    if host.startswith("/cloudsql/"):
        return f"postgresql://{user}:{pw}@/{name}?host={host}"
    return f"postgresql://{user}:{pw}@{host}:5432/{name}"


pool: asyncpg.Pool = None
experiment_lifecycle_pool: "AttestedExperimentLifecyclePool | None" = None
API_DB_STATEMENT_TIMEOUT_MS = 15_000
SCORECARD_DB_STATEMENT_TIMEOUT_MS = 6_000
EXPERIMENT_STATUS_DB_STATEMENT_TIMEOUT_MS = 3_000
EXPERIMENT_LIFECYCLE_DB_USER_ENV = "VERDIFY_EXPERIMENT_LIFECYCLE_DB_USER"
EXPERIMENT_LIFECYCLE_DB_PASSWORD_ENV = "VERDIFY_EXPERIMENT_LIFECYCLE_DB_PASSWORD"
EXPERIMENT_LIFECYCLE_DB_LOGIN = "verdify_experiment_v2_lifecycle_login"

_ORDINARY_RUNTIME_ROLE_ATTESTATION_SQL = """
SELECT current_user = session_user
   AND pg_catalog.current_setting('search_path') =
       'pg_catalog, public, pg_temp'
   AND public.fn_runtime_attest_ordinary_login()
"""


class AttestedExperimentLifecyclePool:
    """Dedicated function-only pool made available only after role attestation."""

    lifecycle_role_attested = True

    def __init__(self, candidate: asyncpg.Pool) -> None:
        self._candidate = candidate

    def acquire(self):
        return self._candidate.acquire()

    async def close(self) -> None:
        await self._candidate.close()


_EXPERIMENT_LIFECYCLE_ROLE_ATTESTATION_SQL = """
WITH login AS (
    SELECT oid, rolname, rolcanlogin, rolinherit, rolsuper, rolcreatedb,
           rolcreaterole, rolreplication, rolbypassrls
      FROM pg_roles
     WHERE rolname = current_user
), duty AS (
    SELECT oid, rolcanlogin, rolinherit, rolsuper, rolcreatedb, rolcreaterole,
           rolreplication, rolbypassrls
      FROM pg_roles
     WHERE rolname = 'verdify_experiment_lifecycle'
), allowed_functions(function_signature) AS (
    SELECT unnest(ARRAY[
        'public.fn_experiment_v2_configure(uuid,text,text,text,text,text,text,uuid,text,bigint,text)',
        'public.fn_experiment_v2_lock_design(uuid,date,integer,time without time zone,text,text,text,text,text,text,text,text,text,text,text)',
        'public.fn_experiment_v2_direct_launch_lock(uuid,date,integer,time without time zone,text,text,text,text,text,text,text,text,text,text,text,text,text,text,text,text,tstzrange,text,text,text)',
        'public.fn_experiment_v2_direct_launch_approve_day1(uuid,text)',
        'public.fn_experiment_v2_direct_proof_begin(uuid,text,tstzrange,text,text,text)',
        'public.fn_experiment_v2_direct_proof_open_aggressive(uuid,uuid,text)',
        'public.fn_experiment_v2_direct_proof_begin_baseline_after(uuid,uuid,text)',
        'public.fn_experiment_v2_direct_proof_finish(uuid,text)',
        'public.fn_experiment_v2_direct_launch_commit(uuid,date,integer,time without time zone,text,text,text,text,text,text,text,text,text,text,text)',
        'public.fn_experiment_v2_register_state(uuid,text,smallint,bytea,bytea,text)',
        'public.fn_experiment_v2_record_approval(uuid,text,text,integer,text,text,tstzrange,timestamptz,text,text,text)',
        'public.fn_experiment_v2_transition(uuid,text,text,text,text)',
        'public.fn_experiment_v2_set_admission(uuid,text,text,text)',
        'public.fn_experiment_v2_record_facility_safe_closure(uuid,text,text,text)',
        'public.fn_experiment_v2_create_work(uuid,text,text,tstzrange,timestamptz,text)',
        'public.fn_experiment_v2_request_recovery(uuid,uuid,tstzrange,timestamptz,text,text)',
        'public.fn_experiment_v2_complete(uuid,text,text)',
        'public.fn_experiment_v2_api_status(uuid)'
    ]::text[])
)
SELECT current_user::text AS current_user_name,
       session_user::text AS session_user_name,
       current_user = session_user AS session_user_matches,
       pg_has_role(current_user, 'verdify_experiment_lifecycle', 'member') AS duty_member,
       coalesce((
           SELECT NOT membership.admin_option
             FROM pg_auth_members membership
             CROSS JOIN login CROSS JOIN duty
            WHERE membership.member = login.oid
              AND membership.roleid = duty.oid
       ), false) AS duty_membership_non_admin,
       coalesce((SELECT rolcanlogin AND rolinherit FROM login), false)
           AS login_role_safe,
       coalesce((SELECT rolsuper FROM login), true) AS is_superuser,
       coalesce((
           SELECT d.datdba = login.oid
             FROM pg_database d CROSS JOIN login
            WHERE d.datname = current_database()
       ), true) AS is_database_owner,
       coalesce((
           SELECT rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls
             FROM login
       ), true) AS has_elevated_role_attributes,
       coalesce((
           SELECT NOT duty.rolcanlogin AND NOT duty.rolinherit AND
                  NOT duty.rolsuper AND
                  NOT duty.rolcreatedb AND NOT duty.rolcreaterole AND
                  NOT duty.rolreplication AND NOT duty.rolbypassrls AND
                  NOT EXISTS (
                      SELECT 1
                        FROM pg_roles inherited
                       WHERE inherited.oid <> duty.oid
                         AND pg_has_role(duty.oid, inherited.oid, 'member')
                  )
             FROM duty
       ), false) AS duty_role_safe,
       EXISTS (
           SELECT 1
             FROM pg_namespace namespace CROSS JOIN login
            WHERE namespace.nspname = 'public' AND namespace.nspowner = login.oid
           UNION ALL
           SELECT 1
             FROM pg_class owned
             JOIN pg_namespace namespace ON namespace.oid = owned.relnamespace
             CROSS JOIN login
            WHERE namespace.nspname = 'public' AND owned.relowner = login.oid
           UNION ALL
           SELECT 1
             FROM pg_proc owned
             JOIN pg_namespace namespace ON namespace.oid = owned.pronamespace
             CROSS JOIN login
            WHERE namespace.nspname = 'public' AND owned.proowner = login.oid
       ) AS has_managed_object_ownership,
       has_schema_privilege(current_user, 'public', 'USAGE') AS schema_usage,
       EXISTS (
           SELECT 1
             FROM pg_roles inherited
            WHERE inherited.rolname NOT IN (current_user, 'verdify_experiment_lifecycle')
              AND pg_has_role(current_user, inherited.oid, 'member')
       ) AS has_other_role_membership,
       EXISTS (
           SELECT 1
             FROM pg_roles candidate
             CROSS JOIN login
             CROSS JOIN duty
            WHERE candidate.oid NOT IN (login.oid, duty.oid)
              AND NOT candidate.rolsuper
              AND pg_has_role(candidate.oid, duty.oid, 'member')
       ) AS has_unexpected_duty_member,
       has_schema_privilege(current_user, 'public', 'CREATE')
           AS has_public_schema_create,
       EXISTS (
           SELECT 1
             FROM pg_class protected
             JOIN pg_namespace namespace ON namespace.oid = protected.relnamespace
            WHERE namespace.nspname = 'public'
              AND protected.relkind IN ('r', 'p', 'v', 'm', 'f')
              AND (
                  has_table_privilege(
                      current_user,
                      protected.oid,
                      'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'
                  )
                  OR has_any_column_privilege(
                      current_user,
                      protected.oid,
                      'SELECT,INSERT,UPDATE,REFERENCES'
                  )
              )
       ) AS has_protected_relation_privilege,
       EXISTS (
           SELECT 1
             FROM pg_class protected
             JOIN pg_namespace namespace ON namespace.oid = protected.relnamespace
            WHERE namespace.nspname = 'public'
              AND protected.relkind = 'S'
              AND has_sequence_privilege(
                  current_user,
                  protected.oid,
                  'USAGE,SELECT,UPDATE'
              )
       ) AS has_protected_sequence_privilege,
       EXISTS (
           SELECT 1
             FROM pg_proc candidate_function
            JOIN pg_namespace namespace ON namespace.oid = candidate_function.pronamespace
            WHERE namespace.nspname = 'public'
              AND (
                  candidate_function.proname LIKE 'fn_experiment_v2_%'
                  OR candidate_function.prosecdef
              )
              AND has_function_privilege(current_user, candidate_function.oid, 'EXECUTE')
              AND NOT EXISTS (
                  SELECT 1
                    FROM allowed_functions allowed
                   WHERE to_regprocedure(allowed.function_signature) = candidate_function.oid
              )
       ) AS has_unexpected_function_execute,
       NOT EXISTS (
           SELECT 1
             FROM allowed_functions required
            WHERE to_regprocedure(required.function_signature) IS NULL
               OR NOT has_function_privilege(
                   current_user,
                   to_regprocedure(required.function_signature),
                   'EXECUTE'
               )
       ) AS has_required_function_execute
"""


def _experiment_lifecycle_role_attestation_passes(row: Mapping[str, object] | None) -> bool:
    if row is None:
        return False
    try:
        return bool(
            row["current_user_name"] == EXPERIMENT_LIFECYCLE_DB_LOGIN
            and row["session_user_name"] == EXPERIMENT_LIFECYCLE_DB_LOGIN
            and row["session_user_matches"] is True
            and row["duty_member"] is True
            and row["duty_membership_non_admin"] is True
            and row["login_role_safe"] is True
            and row["is_superuser"] is False
            and row["is_database_owner"] is False
            and row["has_elevated_role_attributes"] is False
            and row["duty_role_safe"] is True
            and row["has_other_role_membership"] is False
            and row["has_unexpected_duty_member"] is False
            and row["has_managed_object_ownership"] is False
            and row["schema_usage"] is True
            and row["has_public_schema_create"] is False
            and row["has_protected_relation_privilege"] is False
            and row["has_protected_sequence_privilege"] is False
            and row["has_unexpected_function_execute"] is False
            and row["has_required_function_execute"] is True
        )
    except (KeyError, TypeError):
        return False


async def _init_db_connection(conn: asyncpg.Connection) -> None:
    # Public proof endpoints hit planner/data-health views that are small but
    # complex enough for Postgres JIT to spend seconds compiling. Keep API
    # sessions latency-first; the DB still uses JIT for other clients.
    if _api_runtime_db_role_required():
        attested = await conn.fetchval(_ORDINARY_RUNTIME_ROLE_ATTESTATION_SQL)
        if attested is not True:
            raise RuntimeError("ordinary API database role attestation failed")
    await conn.execute("SET jit = off")


async def _init_experiment_lifecycle_db_connection(conn: asyncpg.Connection) -> None:
    """Connection init for the separately attested migration-214 identity.

    The ordinary-role cutover assertion deliberately does not apply to this
    pool; its exact lifecycle login and function allowlist are attested after
    pool creation by ``_EXPERIMENT_LIFECYCLE_ROLE_ATTESTATION_SQL``.
    """
    await conn.execute("SET jit = off")


async def _setup_db_connection(conn: asyncpg.Connection) -> None:
    # Pool reset runs RESET ALL when a connection is released. Reapply these
    # safety settings on every checkout so a disconnected HTTP caller cannot
    # leave an unbounded production query running behind it.
    await conn.execute("SET application_name = 'verdify-api'")
    await conn.execute(f"SET statement_timeout = '{API_DB_STATEMENT_TIMEOUT_MS}ms'")


async def _setup_experiment_lifecycle_connection(conn: asyncpg.Connection) -> None:
    """Reapply the dedicated status-function session budget after pool reset."""
    await conn.execute("SET application_name = 'verdify-api-experiment-lifecycle'")
    await conn.execute(f"SET statement_timeout = '{EXPERIMENT_STATUS_DB_STATEMENT_TIMEOUT_MS}ms'")


def _ordinary_api_db_user() -> str:
    database_url = os.environ.get("DATABASE_URL", "")
    if database_url:
        try:
            parsed = urllib.parse.urlsplit(database_url)
            if parsed.username:
                return urllib.parse.unquote(parsed.username)
        except ValueError:
            # The ordinary pool will reject a malformed URL independently. The
            # dedicated pool still must pass DB-role attestation before use.
            pass
    return os.environ.get("DB_USER", "verdify")


async def create_experiment_lifecycle_pool() -> AttestedExperimentLifecyclePool | None:
    """Create the separately credentialed migration-214 lifecycle pool.

    Missing, incomplete, shared, unreachable, or over-privileged credentials
    leave only the component-status endpoint unavailable. The ordinary API
    pool is never returned as a substitute, and credential values are never
    interpolated into a DSN or log message.
    """
    user = os.environ.get(EXPERIMENT_LIFECYCLE_DB_USER_ENV, "")
    password = os.environ.get(EXPERIMENT_LIFECYCLE_DB_PASSWORD_ENV, "")
    if not user and not password:
        return None
    if not user or not password or hmac.compare_digest(user, _ordinary_api_db_user()):
        log.error("experiment lifecycle database credential is incomplete or shared; refusing it")
        return None
    if not hmac.compare_digest(user, EXPERIMENT_LIFECYCLE_DB_LOGIN):
        log.error("experiment lifecycle database login identity is not the locked login")
        return None

    candidate: asyncpg.Pool | None = None
    try:
        candidate = await asyncpg.create_pool(
            host=os.environ.get("DB_HOST", "localhost"),
            port=int(os.environ.get("DB_PORT", "5432")),
            database=os.environ.get("DB_NAME", "verdify"),
            user=user,
            password=password,
            min_size=1,
            max_size=2,
            max_inactive_connection_lifetime=60,
            init=_init_experiment_lifecycle_db_connection,
            setup=_setup_experiment_lifecycle_connection,
        )
        async with candidate.acquire() as connection:
            attestation = await connection.fetchrow(_EXPERIMENT_LIFECYCLE_ROLE_ATTESTATION_SQL)
        if not _experiment_lifecycle_role_attestation_passes(attestation):
            await candidate.close()
            log.error("experiment lifecycle database login lacks the exact restricted duty; refusing it")
            return None
        return AttestedExperimentLifecyclePool(candidate)
    except Exception as exc:
        if candidate is not None:
            try:
                await candidate.close()
            except Exception:
                pass
        log.error("experiment lifecycle database pool unavailable error=%s", type(exc).__name__)
        return None


async def _fetch_planner_scorecard(
    conn: asyncpg.Connection,
    scorecard_date: date | None = None,
) -> list[asyncpg.Record]:
    """Fetch the optional scorecard within a strict server-side DB budget."""
    try:
        async with conn.transaction():
            await conn.execute(f"SET LOCAL statement_timeout = '{SCORECARD_DB_STATEMENT_TIMEOUT_MS}ms'")
            return await conn.fetch(
                """
                SELECT metric, value
                FROM fn_planner_scorecard(
                    COALESCE($1::date, (now() AT TIME ZONE 'America/Denver')::date)
                )
                ORDER BY metric
                """,
                scorecard_date,
            )
    except asyncpg.QueryCanceledError:
        return []


async def _fetchrow_optional(
    conn: asyncpg.Connection,
    statement: str,
    *args: object,
    timeout_ms: int = 3_000,
) -> asyncpg.Record | None:
    """Fetch optional public evidence without blocking the whole response."""
    try:
        async with conn.transaction():
            await conn.execute(f"SET LOCAL statement_timeout = '{timeout_ms}ms'")
            return await conn.fetchrow(statement, *args)
    except asyncpg.QueryCanceledError:
        return None


async def _fetch_optional(
    conn: asyncpg.Connection,
    statement: str,
    *args: object,
    timeout_ms: int = 3_000,
) -> list[asyncpg.Record]:
    """Fetch optional public evidence rows within a fail-soft DB budget."""
    try:
        async with conn.transaction():
            await conn.execute(f"SET LOCAL statement_timeout = '{timeout_ms}ms'")
            return await conn.fetch(statement, *args)
    except asyncpg.QueryCanceledError:
        return []


@asynccontextmanager
async def lifespan(app: FastAPI):
    global experiment_lifecycle_pool, pool
    pool = await asyncpg.create_pool(
        get_db_dsn(),
        min_size=1,
        max_size=3,
        max_inactive_connection_lifetime=60,
        init=_init_db_connection,
        setup=_setup_db_connection,
    )
    experiment_lifecycle_pool = await create_experiment_lifecycle_pool()
    try:
        yield
    finally:
        dedicated_pool = experiment_lifecycle_pool
        experiment_lifecycle_pool = None
        try:
            if dedicated_pool is not None:
                await dedicated_pool.close()
        finally:
            await pool.close()


app = FastAPI(
    title="Verdify Crop Catalog API",
    version="1.0.0",
    description="Greenhouse crop management — inventory, observations, health tracking",
    lifespan=lifespan,
    docs_url="/docs" if os.environ.get("VERDIFY_ENABLE_API_DOCS", "").lower() in {"1", "true", "yes"} else None,
    redoc_url="/redoc" if os.environ.get("VERDIFY_ENABLE_API_DOCS", "").lower() in {"1", "true", "yes"} else None,
    # Keep OpenAPI available for contract/drift tests, but noindex it at
    # Traefik/API headers and keep the interactive docs hidden by default.
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://lab.verdify.ai",
        "https://verdify.ai",
        "https://www.verdify.ai",
        "http://localhost:8080",
    ],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.middleware("http")
async def noindex_api_responses(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


# ── Models ──
#
# Sprint 21: request-body models moved to /mnt/iris/verdify/verdify_schemas/crops.py.
# Any change to fields or validation rules lives there now; every caller
# (API, MCP crops tool, vault-crop-writer, planner) shares the same shape.

DEFAULT_GREENHOUSE = "vallery"
PLANNER_GATEWAY_LABEL = os.environ.get("VERDIFY_PLANNER_GATEWAY_LABEL", "hermes-iris")
PLANNER_MODEL_LABEL = os.environ.get("VERDIFY_PLANNER_MODEL_LABEL", "hermes-iris/custom:gpt-5.6-sol/xhigh")
WRITE_API_KEY_ENV = "VERDIFY_WRITE_API_KEY"
ALLOW_UNAUTHENTICATED_WRITES_ENV = "VERDIFY_ALLOW_UNAUTHENTICATED_WRITES"
PUBLIC_HOME_METRICS_CACHE_TTL_S = 30.0
_PUBLIC_HOME_METRICS_CACHE: dict[str, tuple[float, PublicHomeMetrics]] = {}
PUBLIC_BAND_TRACE_CACHE_TTL_S = 30.0
_PUBLIC_BAND_TRACE_CACHE: dict[str, tuple[float, PublicBandTraceResponse]] = {}
PUBLIC_GPU_POWER_CACHE_TTL_S = 30.0
_PUBLIC_GPU_POWER_CACHE: dict[str, tuple[float, dict]] = {}
CONTACT_ALLOWED_TOPICS = {"build", "control", "data", "press", "collaboration", "correction", "other"}
CONTACT_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CONTACT_URL_RE = re.compile(r"(https?://|www\.)", re.IGNORECASE)
FORCED_ON_SWITCH_PARAMS = frozenset({"sw_fsm_controller_enabled"})
CONTACT_NOTIFY_SUBJECT_PREFIX = "Verdify contact"
PUBLIC_CAMERA_IDS = {"greenhouse_1", "greenhouse_2"}
FRIGATE_BASE_URL_ENV = "VERDIFY_FRIGATE_PUBLIC_BASE_URL"
GO2RTC_BASE_URL_ENV = "VERDIFY_GO2RTC_PUBLIC_BASE_URL"
CLIMATE_ACTION_PROOF_MISSING_SQL = """
WITH latest AS (
    SELECT climate_action,
           priority_axis,
           climate_intent_version,
           temp_low_f,
           temp_target_f,
           temp_high_f,
           vpd_low_kpa,
           vpd_target_kpa,
           vpd_high_kpa,
           temp_target_delta_f,
           vpd_target_delta_kpa,
           temp_band_error_f,
           vpd_band_error_kpa,
           relay_truth,
           sensor_status
    FROM climate_action_log
    ORDER BY ts DESC
    LIMIT 1
)
SELECT COALESCE(
    (
        SELECT concat_ws(',',
            CASE WHEN climate_action IS NULL OR climate_action = '' THEN 'climate_action' END,
            CASE WHEN priority_axis IS NULL OR priority_axis = '' THEN 'priority_axis' END,
            CASE WHEN climate_intent_version IS NULL OR climate_intent_version = '' THEN 'climate_intent_version' END,
            CASE WHEN temp_low_f IS NULL THEN 'temp_low_f' END,
            CASE WHEN temp_target_f IS NULL THEN 'temp_target_f' END,
            CASE WHEN temp_high_f IS NULL THEN 'temp_high_f' END,
            CASE WHEN vpd_low_kpa IS NULL THEN 'vpd_low_kpa' END,
            CASE WHEN vpd_target_kpa IS NULL THEN 'vpd_target_kpa' END,
            CASE WHEN vpd_high_kpa IS NULL THEN 'vpd_high_kpa' END,
            CASE WHEN temp_target_delta_f IS NULL THEN 'temp_target_delta_f' END,
            CASE WHEN vpd_target_delta_kpa IS NULL THEN 'vpd_target_delta_kpa' END,
            CASE WHEN temp_band_error_f IS NULL THEN 'temp_band_error_f' END,
            CASE WHEN vpd_band_error_kpa IS NULL THEN 'vpd_band_error_kpa' END,
            CASE
                WHEN relay_truth IS NULL
                  OR jsonb_typeof(relay_truth) <> 'object'
                  OR relay_truth = '{}'::jsonb
                THEN 'relay_truth'
            END,
            CASE
                WHEN sensor_status IS NULL
                  OR jsonb_typeof(sensor_status) <> 'object'
                  OR sensor_status = '{}'::jsonb
                THEN 'sensor_status'
            END,
            CASE
                WHEN sensor_status->>'latest_climate_ts' IS NULL
                  OR sensor_status->>'latest_climate_ts' = ''
                THEN 'sensor_status.latest_climate_ts'
            END,
            CASE
                WHEN CASE
                    WHEN sensor_status->>'latest_climate_age_s' ~ '^[0-9]+$'
                    THEN (sensor_status->>'latest_climate_age_s')::int < 300
                    ELSE false
                END IS NOT true
                THEN 'sensor_status.latest_climate_age_s'
            END,
            CASE
                WHEN sensor_status->>'temp_avg_present' IS DISTINCT FROM 'true'
                THEN 'sensor_status.temp_avg_present'
            END,
            CASE
                WHEN sensor_status->>'vpd_avg_present' IS DISTINCT FROM 'true'
                THEN 'sensor_status.vpd_avg_present'
            END,
            CASE
                WHEN sensor_status->>'band_context_complete' IS DISTINCT FROM 'true'
                THEN 'sensor_status.band_context_complete'
            END
        )
        FROM latest
    ),
    'missing'
)
"""


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _trim(value: str | None, max_len: int) -> str | None:
    if value is None:
        return None
    trimmed = value.strip()
    if not trimmed:
        return None
    return trimmed[:max_len]


def _clean_email_header(value: str | None, max_len: int = 160) -> str:
    return (_trim((value or "").replace("\r", " ").replace("\n", " "), max_len) or "").strip()


def _client_ip(request: Request) -> str:
    """Prefer Cloudflare's client IP header; store only a salted hash."""
    candidates = [
        request.headers.get("CF-Connecting-IP"),
        (request.headers.get("X-Forwarded-For") or "").split(",")[0],
        request.client.host if request.client else None,
    ]
    for candidate in candidates:
        if not candidate:
            continue
        value = candidate.strip()
        try:
            return str(ipaddress.ip_address(value))
        except ValueError:
            continue
    return "unknown"


def _contact_ip_hash(ip: str) -> str:
    salt = (
        os.environ.get("VERDIFY_CONTACT_HASH_SALT")
        or os.environ.get(WRITE_API_KEY_ENV)
        or os.environ.get("DB_PASS")
        or "verdify-contact-v1"
    )
    return hashlib.sha256(f"{salt}:{ip}".encode()).hexdigest()


def _turnstile_verify_sync(secret: str, token: str, remote_ip: str) -> bool:
    payload = urllib.parse.urlencode(
        {
            "secret": secret,
            "response": token,
            "remoteip": remote_ip,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        "https://challenges.cloudflare.com/turnstile/v0/siteverify",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=4) as response:
            body = json.loads(response.read().decode("utf-8"))
    except Exception:
        return False
    return bool(body.get("success"))


def _read_camera_jpeg_sync(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "verdify-public-api/1.0"})
    with urllib.request.urlopen(request, timeout=4) as response:
        data = response.read()
    if len(data) < 1024 or not data.startswith(b"\xff\xd8"):
        raise ValueError("camera snapshot did not look like a JPEG")
    return data


def _fetch_camera_snapshot_sync(camera_id: str, height: int) -> bytes:
    go2rtc_base_url = os.environ.get(GO2RTC_BASE_URL_ENV, "http://192.168.30.142:1984").rstrip("/")
    go2rtc_url = f"{go2rtc_base_url}/api/frame.jpeg?{urllib.parse.urlencode({'src': camera_id, 'h': height})}"
    try:
        return _read_camera_jpeg_sync(go2rtc_url)
    except Exception:
        # Fall back to Frigate's latest detect frame if the source stream is unavailable.
        pass

    base_url = os.environ.get(FRIGATE_BASE_URL_ENV, "http://192.168.30.142:5000").rstrip("/")
    url = f"{base_url}/api/{urllib.parse.quote(camera_id)}/latest.jpg?{urllib.parse.urlencode({'h': height, 'quality': 100})}"
    return _read_camera_jpeg_sync(url)


async def _verify_turnstile_if_configured(token: str | None, remote_ip: str) -> bool:
    secret = os.environ.get("VERDIFY_TURNSTILE_SECRET", "").strip()
    if not secret:
        return False
    if not token:
        raise HTTPException(status_code=400, detail="Missing contact verification token")
    verified = await asyncio.to_thread(_turnstile_verify_sync, secret, token, remote_ip)
    if not verified:
        raise HTTPException(status_code=400, detail="Contact verification failed")
    return True


def _contact_smtp_config() -> dict[str, str | int | bool | None]:
    host = _trim(os.environ.get("VERDIFY_CONTACT_SMTP_HOST"), 255)
    port = _int_env("VERDIFY_CONTACT_SMTP_PORT", 587)
    username = _trim(os.environ.get("VERDIFY_CONTACT_SMTP_USERNAME"), 255)
    password = os.environ.get("VERDIFY_CONTACT_SMTP_PASSWORD")
    use_ssl = _truthy_env("VERDIFY_CONTACT_SMTP_SSL")
    starttls = not use_ssl and os.environ.get("VERDIFY_CONTACT_SMTP_STARTTLS", "true").lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    timeout_s = _int_env("VERDIFY_CONTACT_SMTP_TIMEOUT_S", 6)
    return {
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "ssl": use_ssl,
        "starttls": starttls,
        "timeout_s": timeout_s,
    }


def _contact_notification_message(
    *,
    submission_id: int,
    created_at,
    notify_to: str,
    name: str,
    email: str,
    topic: str,
    affiliation: str | None,
    message: str,
    user_agent: str | None,
    referrer: str | None,
) -> EmailMessage:
    from_addr = (
        _trim(os.environ.get("VERDIFY_CONTACT_NOTIFY_FROM"), 255)
        or _trim(os.environ.get("VERDIFY_CONTACT_SMTP_USERNAME"), 255)
        or "contact@verdify.ai"
    )
    from_name = _clean_email_header(os.environ.get("VERDIFY_CONTACT_NOTIFY_FROM_NAME") or "Verdify Contact")
    subject_prefix = _clean_email_header(os.environ.get("VERDIFY_CONTACT_NOTIFY_SUBJECT_PREFIX"), 80)
    if not subject_prefix:
        subject_prefix = CONTACT_NOTIFY_SUBJECT_PREFIX

    safe_name = _clean_email_header(name, 120)
    safe_topic = _clean_email_header(topic, 40)
    msg = EmailMessage()
    msg["To"] = notify_to
    msg["From"] = formataddr((from_name, from_addr))
    msg["Reply-To"] = formataddr((safe_name, email))
    msg["Subject"] = f"[{subject_prefix}] {safe_topic}: {safe_name}"
    msg["Message-ID"] = make_msgid(domain="verdify.ai")
    msg.set_content(
        "\n".join(
            [
                f"New Verdify contact submission #{submission_id}",
                "",
                f"Submitted: {created_at}",
                f"Name: {name}",
                f"Reply email: {email}",
                f"Topic: {topic}",
                f"Affiliation: {affiliation or '-'}",
                f"Referrer: {referrer or '-'}",
                f"User agent: {user_agent or '-'}",
                "",
                "Message:",
                message,
                "",
                "Review queue:",
                "docker exec verdify-timescaledb psql -U verdify -d verdify -x -c "
                '"SELECT id, created_at, name, email, topic, affiliation, message, status '
                "FROM public_contact_submissions WHERE status = 'new' ORDER BY created_at DESC LIMIT 20;\"",
            ]
        )
    )
    return msg


def _send_contact_notification_sync(msg: EmailMessage, smtp_config: dict[str, str | int | bool | None]) -> None:
    host = smtp_config["host"]
    if not host:
        raise RuntimeError("VERDIFY_CONTACT_SMTP_HOST is not configured")

    smtp_cls = smtplib.SMTP_SSL if smtp_config["ssl"] else smtplib.SMTP
    with smtp_cls(str(host), int(smtp_config["port"]), timeout=int(smtp_config["timeout_s"])) as smtp:
        if smtp_config["starttls"]:
            smtp.starttls()
        username = smtp_config["username"]
        password = smtp_config["password"]
        if username and password:
            smtp.login(str(username), str(password))
        smtp.send_message(msg)


async def _notify_contact_submission(
    *,
    submission_id: int,
    created_at,
    notify_to: str | None,
    name: str,
    email: str,
    topic: str,
    affiliation: str | None,
    message: str,
    user_agent: str | None,
    referrer: str | None,
) -> None:
    notify_to = _trim(os.environ.get("VERDIFY_CONTACT_NOTIFY_TO"), 254) or _trim(notify_to, 254)
    smtp_config = _contact_smtp_config()
    if not notify_to or not smtp_config["host"]:
        error = (
            "VERDIFY_CONTACT_SMTP_HOST is not configured" if notify_to else "contact notify recipient is not configured"
        )
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE public_contact_submissions
                SET notification_error = $2
                WHERE id = $1
                """,
                submission_id,
                error,
            )
        return

    msg = _contact_notification_message(
        submission_id=submission_id,
        created_at=created_at,
        notify_to=notify_to,
        name=name,
        email=email,
        topic=topic,
        affiliation=affiliation,
        message=message,
        user_agent=user_agent,
        referrer=referrer,
    )
    try:
        await asyncio.to_thread(_send_contact_notification_sync, msg, smtp_config)
    except Exception as exc:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE public_contact_submissions
                SET notification_status = 'failed',
                    notification_attempted_at = now(),
                    notification_error = $2
                WHERE id = $1
                """,
                submission_id,
                str(exc)[:500],
            )
        return

    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE public_contact_submissions
            SET notification_status = 'sent',
                notification_attempted_at = now(),
                notification_error = NULL
            WHERE id = $1
            """,
            submission_id,
        )


async def require_write_access(
    request: Request,
    x_verdify_api_key: Annotated[str | None, Header(alias="X-Verdify-API-Key")] = None,
) -> None:
    """Fail closed for mutating routes unless an operator key is configured."""
    if _truthy_env(ALLOW_UNAUTHENTICATED_WRITES_ENV):
        return
    expected = os.environ.get(WRITE_API_KEY_ENV)
    if expected and x_verdify_api_key and hmac.compare_digest(expected, x_verdify_api_key):
        return
    raise HTTPException(
        status_code=403,
        detail="Write API disabled for unauthenticated request",
    )


def _to_float(value) -> float | None:
    return float(value) if value is not None else None


def _overall_data_health(rows: list[asyncpg.Record]) -> str:
    statuses = {str(r["status"]).lower() for r in rows}
    if "fail" in statuses:
        return "fail"
    if "warn" in statuses:
        return "warn"
    return "ok"


class PublicContactSubmission(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    email: str = Field(min_length=5, max_length=254)
    message: str = Field(min_length=20, max_length=4000)
    topic: str = Field(default="other", max_length=40)
    affiliation: str | None = Field(default=None, max_length=160)
    website: str | None = Field(default=None, max_length=200)
    turnstile_token: str | None = Field(default=None, max_length=2048)


async def _parse_contact_submission(request: Request) -> tuple[PublicContactSubmission, bool]:
    content_type = (request.headers.get("Content-Type") or "").lower()
    try:
        if "application/json" in content_type:
            return PublicContactSubmission.model_validate(await request.json()), False

        body = (await request.body()).decode("utf-8")
        form_data = {
            key: values[-1] if values else ""
            for key, values in urllib.parse.parse_qs(body, keep_blank_values=True).items()
        }
        return PublicContactSubmission.model_validate(form_data), True
    except (json.JSONDecodeError, UnicodeDecodeError, ValidationError) as exc:
        raise HTTPException(status_code=422, detail="Invalid contact submission") from exc


# ── Setpoints compatibility endpoint ──


@app.get("/setpoints")
@app.get("/api/v1/greenhouses/{greenhouse_id}/setpoints")
async def get_setpoints(greenhouse_id: str = DEFAULT_GREENHOUSE):
    """Return current effective setpoints in legacy key=value format.

    Production firmware receives tunables through ESPHome native API pushes and
    cfg_* readbacks. This endpoint is kept aligned for diagnostics, recovery
    tooling, and any future explicitly-enabled pull client.
    """
    # Dispatcher-owned params: temperature uses crop profile envelope directly,
    # VPD uses the DB-owned house control band, and lighting uses the highest
    # active crop DLI to set the photoperiod window firmware enforces.
    async with pool.acquire() as conn:
        # Get latest value per parameter (Tier 1 + band-driven only, no legacy params)
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (parameter) parameter, value
            FROM setpoint_changes WHERE greenhouse_id = $1
              AND parameter = ANY($2::text[])
            ORDER BY parameter, ts DESC
        """,
            greenhouse_id,
            sorted(FIRMWARE_SETPOINT_PARAMS),
        )
        # For band-driven params, compute from crop science + house VPD policy.
        band_row = await conn.fetchrow(
            "SELECT temp_low, temp_high, vpd_low, vpd_high, temp_target, vpd_target FROM fn_band_setpoints(now())"
        )
        zone_row = await conn.fetchrow(
            "SELECT vpd_target_south, vpd_target_west, vpd_target_east, vpd_target_center "
            "FROM fn_zone_vpd_targets(now())"
        )
        house_row = await conn.fetchrow("SELECT house_vpd_low, house_vpd_high FROM fn_house_vpd_control_band(now())")
        lighting_row = await conn.fetchrow(
            "SELECT target_dli, sunrise_hour, cutoff_hour, target_light_hours FROM fn_lighting_policy(now(), $1)",
            greenhouse_id,
        )
        lighting_circuit_rows = await conn.fetch(
            "SELECT light_key, equipment, target_light_minutes, start_hour, cutoff_hour, "
            "lux_on_threshold, lux_hysteresis, min_on_s, min_off_s, auto_enabled, legacy_dli_target "
            "FROM fn_lighting_minutes_policy(now(), $1) ORDER BY light_key",
            greenhouse_id,
        )
        # Tier 1 #3: fail loud if band computation returned NULL.
        # Without a computed band, recovery tooling would receive partial
        # temp/VPD policy and could mask a dispatcher problem. Better to 503
        # than return a response that looks authoritative but is incomplete.
        if band_row is None or zone_row is None or house_row is None or lighting_row is None:
            existing = await conn.fetchval(
                "SELECT id FROM alert_log WHERE alert_type = 'band_fn_null' AND disposition = 'open' LIMIT 1"
            )
            if existing is None:
                alert = AlertEnvelope.model_validate(
                    {
                        "alert_type": "band_fn_null",
                        "severity": "critical",
                        "category": "system",
                        "message": (
                            "band or lighting policy function returned NULL — compatibility setpoints unavailable"
                        ),
                        "details": {
                            "band_row_null": band_row is None,
                            "zone_row_null": zone_row is None,
                            "house_row_null": house_row is None,
                            "lighting_row_null": lighting_row is None,
                        },
                    }
                )
                await conn.execute(
                    "INSERT INTO alert_log (alert_type, severity, category, message, details, source) "
                    "VALUES ('band_fn_null', 'critical', 'system', $1, $2, 'api')",
                    alert.message,
                    json.dumps(alert.details),
                )
            raise HTTPException(
                status_code=503,
                detail="policy setpoint computation unavailable — check band and lighting policy functions",
            )
        plan_rows = await conn.fetch(
            "SELECT parameter, value FROM v_active_plan WHERE parameter = ANY($1::text[])",
            sorted(FIRMWARE_SETPOINT_PARAMS),
        )
        params = {r["parameter"]: r["value"] for r in rows}
        plan_values = {r["parameter"]: r["value"] for r in plan_rows}
        plan_params = set(plan_values)
        # Planner overrides for all params. Band params are overwritten below by
        # the same crop/house-band functions used by the dispatcher.
        for r in plan_rows:
            params[r["parameter"]] = r["value"]
        # Band-driven params: use the same source as the live dispatcher so the
        # compatibility endpoint cannot diverge from direct pushes.
        if band_row and house_row:
            for param in HOUSE_BAND_COMPUTED_PARAMS:
                if param.startswith("temp"):
                    band_val = float(band_row[param])
                    precision = 1
                else:
                    key = f"house_{param}"
                    band_val = float(house_row[key])
                    precision = 2
                params[param] = _round_half_up(band_val, precision)
        # Lighting policy params: keep the compatibility endpoint identical to the live
        # dispatcher so stale active plans cannot shorten crop photoperiod.
        if lighting_row:
            lighting_values = {
                "gl_dli_target": _round_half_up(float(lighting_row["target_dli"]), 1),
                "gl_sunrise_hour": int(lighting_row["sunrise_hour"]),
                "gl_sunset_hour": int(lighting_row["cutoff_hour"]),
                "sw_gl_auto_mode": 1,
            }
            main_lighting = next((row for row in lighting_circuit_rows if row["light_key"] == "main"), None)
            if main_lighting:
                lighting_values["gl_lux_threshold"] = _round_half_up(float(main_lighting["lux_on_threshold"]), 0)
                lighting_values["gl_lux_hysteresis"] = _round_half_up(float(main_lighting["lux_hysteresis"]), 0)
            for param in LEGACY_LIGHTING_COMPUTED_PARAMS:
                if param in lighting_values:
                    params[param] = lighting_values[param]
        for row in lighting_circuit_rows:
            key = row["light_key"]
            lighting_values = {
                f"gl_{key}_dli_target": _round_half_up(float(row["legacy_dli_target"]), 1),
                f"gl_{key}_target_light_minutes": int(row["target_light_minutes"]),
                f"gl_{key}_sunrise_hour": int(row["start_hour"]),
                f"gl_{key}_sunset_hour": int(row["cutoff_hour"]),
                f"gl_{key}_lux_threshold": _round_half_up(float(row["lux_on_threshold"]), 0),
                f"gl_{key}_lux_hysteresis": _round_half_up(float(row["lux_hysteresis"]), 0),
                f"gl_{key}_min_on_s": int(row["min_on_s"]),
                f"gl_{key}_min_off_s": int(row["min_off_s"]),
                f"sw_gl_{key}_auto_mode": 1 if row["auto_enabled"] else 0,
            }
            for param, value in lighting_values.items():
                if param not in plan_params:
                    params[param] = value
        activity_values = _align_activity_policy_with_plan(
            _activity_policy_values(lighting_row, lighting_circuit_rows),
            plan_values,
        )
        for param, value in activity_values.items():
            if param not in plan_params or param in ACTIVITY_MIRROR_PARAMS:
                params[param] = value
        # Per-zone VPD targets (from crop data per zone)
        if zone_row:
            for param in ZONE_VPD_TARGET_PARAMS:
                params[param] = _round_half_up(float(zone_row[param]), 2)
        # Mister tuning: band provides defaults, planner can override.
        # Set band-derived values first, then planner values overwrite if present.
        if house_row:
            vpd_hi = float(house_row["house_vpd_high"])
            # Band defaults — will be overwritten by planner values from setpoint_changes if present
            params.setdefault("mister_engage_kpa", _round_half_up(vpd_hi + 0.05, 2))
            params.setdefault("mister_all_kpa", _round_half_up(vpd_hi + 0.25, 2))
        outdoor = await conn.fetchrow(
            """
            SELECT outdoor_temp_f, outdoor_rh_pct FROM climate
            WHERE outdoor_temp_f IS NOT NULL AND greenhouse_id = $1
            ORDER BY ts DESC LIMIT 1
        """,
            greenhouse_id,
        )
        if outdoor:
            if outdoor["outdoor_temp_f"]:
                params["outdoor_temp"] = _round_half_up(outdoor["outdoor_temp_f"], 1)
            if outdoor["outdoor_rh_pct"]:
                params["outdoor_rh"] = _round_half_up(outdoor["outdoor_rh_pct"], 0)
        switch_rows = await conn.fetch(
            """
            WITH switch_map(parameter, equipment) AS (
                SELECT parameter, equipment
                FROM unnest($1::text[], $2::text[]) AS mapping(parameter, equipment)
            ),
            latest AS (
                SELECT DISTINCT ON (equipment) equipment, state
                  FROM equipment_state
                 WHERE equipment = ANY($2::text[])
                 ORDER BY equipment, ts DESC
            )
            SELECT switch_map.parameter, latest.state
              FROM switch_map
              JOIN latest USING (equipment)
            """,
            list(EQUIPMENT_SWITCH_SETPOINTS.keys()),
            list(EQUIPMENT_SWITCH_SETPOINTS.values()),
        )
        for row in switch_rows:
            params[row["parameter"]] = 1 if row["state"] else 0
    lines = [f"{k}={v}" for k, v in sorted(params.items())]
    from fastapi.responses import PlainTextResponse

    return PlainTextResponse(content="\n".join(lines) + "\n")


# ── Lights (ESP32 grow light control via MQTT command) ──


@app.post("/api/v1/greenhouses/{greenhouse_id}/lights/{circuit}/{action}")
async def control_lights(
    greenhouse_id: str,
    circuit: str,
    action: str,
    _write_access: None = Depends(require_write_access),
):
    """Publish light command to MQTT for the Lutron bridge to execute."""
    if circuit not in ("main", "grow") or action not in ("on", "off"):
        raise HTTPException(status_code=400, detail="Invalid circuit or action")
    # For now, record the intent in the DB. The local Lutron bridge
    # or a future MQTT subscriber handles the actual switch.
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO v_runtime_equipment_state_write (ts, equipment, state, greenhouse_id)
            VALUES (now(), $1, $2, $3)
        """,
            f"grow_light_{circuit}",
            action == "on",
            greenhouse_id,
        )
    return {"light": circuit, "action": action, "greenhouse_id": greenhouse_id, "status": "recorded"}


# ── Root + Health ──


@app.get("/")
async def root():
    return {
        "service": "verdify-api",
        "version": "1.0.0",
        "greenhouse": DEFAULT_GREENHOUSE,
        "docs": "/docs" if app.docs_url else None,
        "status": "/api/v1/status",
        "public_home_metrics": "/api/v1/public/home-metrics",
        "public_band_trace": "/api/v1/public/band-trace",
        "public_data_health": "/api/v1/public/data-health",
        "public_planner_health": "/api/v1/public/planner-health",
        "public_gpu_power": "/api/v1/public/gpu-power",
        "public_contact": "/api/v1/public/contact",
    }


@app.get("/health")
async def health():
    """Health check endpoint for external monitoring (Prometheus, uptime checks)."""
    checks = {}
    overall = "ok"

    async with pool.acquire() as conn:
        # Climate data freshness
        age = await conn.fetchval("SELECT extract(epoch FROM now() - max(ts))::int FROM climate")
        checks["climate_age_seconds"] = age
        if age is None or age > 300:
            overall = "degraded"

        action_age = await conn.fetchval("SELECT extract(epoch FROM now() - max(ts))::int FROM climate_action_log")
        checks["climate_action_log_age_seconds"] = action_age
        if action_age is None or action_age > 300:
            overall = "degraded"

        action_proof_missing = await conn.fetchval(CLIMATE_ACTION_PROOF_MISSING_SQL)
        checks["climate_action_log_proof_missing"] = action_proof_missing or ""
        if action_proof_missing:
            overall = "degraded"

        # Scorecard
        score_row = await conn.fetchrow(
            "SELECT compliance_pct, planner_score FROM v_planner_performance WHERE date = CURRENT_DATE"
        )
        if score_row:
            checks["compliance_pct"] = float(score_row["compliance_pct"]) if score_row["compliance_pct"] else 0
            checks["planner_score"] = float(score_row["planner_score"]) if score_row["planner_score"] else 0

        # Active alerts
        alert_count = await conn.fetchval("SELECT count(*) FROM alert_log WHERE ts > now() - interval '1 hour'")
        checks["active_alerts_1h"] = alert_count

        # Setpoint dispatch freshness
        last_dispatch = await conn.fetchval("SELECT extract(epoch FROM now() - max(ts))::int FROM setpoint_changes")
        checks["last_setpoint_change_seconds"] = last_dispatch

        # ESP32 mode
        mode = await conn.fetchval(
            "SELECT value FROM system_state WHERE entity = 'greenhouse_state' ORDER BY ts DESC LIMIT 1"
        )
        checks["greenhouse_mode"] = mode

    # Service health inferred from data freshness (API runs inside Docker — no systemctl/host access)
    climate_age = checks.get("climate_age_seconds", 999)
    action_age = checks.get("climate_action_log_age_seconds", 999)
    action_proof_missing = checks.get("climate_action_log_proof_missing", "missing")
    checks["service_ingestor"] = "ok" if isinstance(climate_age, (int, float)) and climate_age < 300 else "stale"
    checks["service_climate_action_log"] = (
        "ok" if isinstance(action_age, (int, float)) and action_age < 300 and not action_proof_missing else "stale"
    )
    # MCP server health is monitored by the ingestor (planning_heartbeat), not the API.
    # The API can't reach localhost:8000 from inside Docker (MCP binds to 127.0.0.1 on host).

    return redact_public_data({"status": overall, "checks": checks})


@app.get("/health/detailed")
async def health_detailed():
    """Image-provenance + readiness probe (#58).

    Surfaces the verified git SHA baked into the image from either the explicit
    Docker build argument or the managed-CI detached-HEAD receipt, so a running
    pod is traceable to a commit — the k3s/CD equivalent of "what's actually
    deployed right now".
    Also reports baked build metadata and a basic DB-reachability check so a
    readiness probe can distinguish "process up" from "ready to serve".

    Distinct from /health (which grades live greenhouse data freshness): this
    endpoint is about the SERVICE/IMAGE, not the plants. It never touches the
    device loop.
    """
    git_sha = image_git_sha()
    build_time = os.environ.get("VERDIFY_BUILD_TIME", "unknown")
    git_ref = os.environ.get("VERDIFY_GIT_REF", "unknown")

    db_ok = False
    db_error = None
    try:
        async with pool.acquire() as conn:
            db_ok = (await conn.fetchval("SELECT 1")) == 1
    except Exception as e:  # readiness must not raise — report the failure
        db_error = str(e)

    ready = db_ok
    result = {
        "status": "ready" if ready else "not_ready",
        "git_sha": git_sha,
        "git_ref": git_ref,
        "build_time": build_time,
        "checks": {"db_reachable": db_ok},
    }
    if db_error is not None:
        result["checks"]["db_error"] = db_error
    return redact_public_data(result)


# ── Greenhouse ──

PUBLIC_GREENHOUSE_FIELDS = ("id", "name", "timezone", "status")
PUBLIC_GREENHOUSE_COLUMNS = ", ".join(PUBLIC_GREENHOUSE_FIELDS)


def _public_greenhouse(row: object) -> dict:
    record = dict(row)
    return redact_public_data({field: record.get(field) for field in PUBLIC_GREENHOUSE_FIELDS})


@app.get("/api/v1/greenhouses")
async def list_greenhouses():
    async with pool.acquire() as conn:
        rows = await conn.fetch(f"SELECT {PUBLIC_GREENHOUSE_COLUMNS} FROM greenhouses ORDER BY name")
    return [_public_greenhouse(row) for row in rows]


@app.get("/api/v1/greenhouses/{greenhouse_id}")
async def get_greenhouse(greenhouse_id: str):
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {PUBLIC_GREENHOUSE_COLUMNS} FROM greenhouses WHERE id = $1",
            greenhouse_id,
        )
        if not row:
            raise HTTPException(404, "Greenhouse not found")
        crops = await conn.fetchval(
            f"""
            SELECT COUNT(*)
            FROM crops c
            JOIN crop_catalog cc ON cc.id = c.crop_catalog_id
            WHERE c.greenhouse_id = $1
              AND c.is_active
              AND {_public_crop_sql_predicate("cc.slug", "c.name", 2, 3)}
            """,
            greenhouse_id,
            *_public_crop_sql_parameters(),
        )
        result = _public_greenhouse(row)
        result["active_crops"] = crops
    return redact_public_data(result)


# ── Crops (greenhouse-scoped + legacy aliases) ──


@app.get("/api/v1/greenhouses/{greenhouse_id}/crops", response_model=list[CropListItem])
@app.get("/api/v1/crops", response_model=list[CropListItem])  # Legacy alias (defaults to vallery)
async def list_crops(
    greenhouse_id: str = DEFAULT_GREENHOUSE,
    zone: str | None = None,
    stage: str | None = None,
    active: bool = True,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    query = f"""
        SELECT {_sql_columns("c", PUBLIC_CROP_FIELDS)},
               cc.slug AS crop_catalog_slug,
               (SELECT ROUND(AVG(o.health_score)::numeric, 2)
                FROM observations o
                WHERE o.crop_id = c.id
                  AND o.health_score IS NOT NULL
                  AND o.ts > now() - interval '7 days') AS latest_health
        FROM crops c
        JOIN crop_catalog cc ON cc.id = c.crop_catalog_id
        WHERE c.is_active = $1
          AND c.greenhouse_id = $2
          AND {_public_crop_sql_predicate("cc.slug", "c.name", 3, 4)}
    """
    params = [active, greenhouse_id, *_public_crop_sql_parameters()]
    idx = 5
    if zone:
        query += f" AND c.zone = ${idx}"
        params.append(zone)
        idx += 1
    if stage:
        query += f" AND c.stage = ${idx}"
        params.append(stage)
        idx += 1
    query += f" ORDER BY c.zone, c.position LIMIT ${idx} OFFSET ${idx + 1}"
    params.extend([limit, offset])

    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *params)
    return _public_crop_rows(rows, fields=PUBLIC_CROP_RESPONSE_FIELDS)


@app.get("/api/v1/crops/{crop_id}", response_model=CropDetail)
async def get_crop(crop_id: int):
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            SELECT {_sql_columns("c", PUBLIC_CROP_FIELDS)}, cc.slug AS crop_catalog_slug
            FROM crops c
            JOIN crop_catalog cc ON cc.id = c.crop_catalog_id
            WHERE c.id = $1
              AND {_public_crop_sql_predicate("cc.slug", "c.name", 2, 3)}
            """,
            crop_id,
            *_public_crop_sql_parameters(),
        )
        public_rows = _public_crop_rows([row], fields=PUBLIC_CROP_RESPONSE_FIELDS) if row else []
        if not public_rows:
            raise HTTPException(404, "Crop not found")

        health = await conn.fetchval(
            "SELECT ROUND(AVG(health_score)::numeric, 2) FROM observations WHERE crop_id = $1 AND health_score IS NOT NULL AND ts > now() - interval '7 days'",
            crop_id,
        )

        recent_obs = await conn.fetch(
            "SELECT ts, obs_type, notes, health_score, observer FROM observations WHERE crop_id = $1 ORDER BY ts DESC LIMIT 5",
            crop_id,
        )

        result = public_rows[0]
        result["latest_health"] = float(health) if health else None
        result["recent_observations"] = redact_public_data([dict(o) for o in recent_obs])
    return redact_public_data(result)


@app.post("/api/v1/greenhouses/{greenhouse_id}/crops", status_code=201)
@app.post("/api/v1/crops", status_code=201)
async def create_crop(
    crop: CropCreate,
    greenhouse_id: str = DEFAULT_GREENHOUSE,
    _write_access: None = Depends(require_write_access),
):
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO crops (name, variety, position, zone, planted_date, expected_harvest,
                stage, count, seed_lot_id, supplier, base_temp_f, target_dli,
                target_vpd_low, target_vpd_high, notes, greenhouse_id)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
            RETURNING *
        """,
            crop.name,
            crop.variety,
            crop.position,
            crop.zone,
            crop.planted_date,
            crop.expected_harvest,
            crop.stage,
            crop.count,
            crop.seed_lot_id,
            crop.supplier,
            crop.base_temp_f,
            crop.target_dli,
            crop.target_vpd_low,
            crop.target_vpd_high,
            crop.notes,
            greenhouse_id,
        )

        # Record the planting event
        await conn.execute(
            "INSERT INTO crop_events (crop_id, event_type, new_stage, source, notes) VALUES ($1, 'planted', $2, 'api', $3)",
            row["id"],
            crop.stage,
            f"Created via API: {crop.name} at {crop.position}",
        )

    return dict(row)


@app.put("/api/v1/crops/{crop_id}")
async def update_crop(crop_id: int, crop: CropUpdate, _write_access: None = Depends(require_write_access)):
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            f"SELECT {_sql_columns('c', PUBLIC_CROP_FIELDS)} FROM crops c WHERE c.id = $1",
            crop_id,
        )
        if not existing:
            raise HTTPException(404, "Crop not found")

        ALLOWED_COLUMNS = {
            "name",
            "variety",
            "zone",
            "position",
            "stage",
            "planted_date",
            "expected_harvest",
            "notes",
            "is_active",
            "vpd_min",
            "vpd_max",
            "temp_min_f",
            "temp_max_f",
            "dli_target",
        }
        updates = {k: v for k, v in crop.model_dump().items() if v is not None and k in ALLOWED_COLUMNS}
        if not updates:
            return dict(existing)

        set_parts = []
        vals = []
        for i, (k, v) in enumerate(updates.items()):
            set_parts.append(f"{k} = ${i + 1}")
            vals.append(v)
        vals.append(crop_id)
        set_sql = ", ".join(set_parts)

        row = await conn.fetchrow(
            f"UPDATE crops SET {set_sql}, updated_at = now() WHERE id = ${len(vals)} RETURNING *", *vals
        )

        # Record stage change event if stage changed
        if "stage" in updates and updates["stage"] != existing["stage"]:
            await conn.execute(
                "INSERT INTO crop_events (crop_id, event_type, old_stage, new_stage, source) VALUES ($1, 'stage_change', $2, $3, 'api')",
                crop_id,
                existing["stage"],
                updates["stage"],
            )

    return dict(row)


@app.delete("/api/v1/crops/{crop_id}")
async def delete_crop(crop_id: int, _write_access: None = Depends(require_write_access)):
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE crops SET is_active = false, updated_at = now() WHERE id = $1 AND is_active = true", crop_id
        )
        if result == "UPDATE 0":
            raise HTTPException(404, "Crop not found or already inactive")

        await conn.execute(
            "INSERT INTO crop_events (crop_id, event_type, source, notes) VALUES ($1, 'removed', 'api', 'Deactivated via API')",
            crop_id,
        )

    return {"status": "deactivated", "id": crop_id}


# ── Observations ──


@app.get("/api/v1/crops/{crop_id}/observations")
async def list_observations(crop_id: int, limit: int = 20):
    async with pool.acquire() as conn:
        await _require_public_crop(conn, crop_id)
        rows = await conn.fetch(
            f"SELECT {_sql_columns('o', PUBLIC_OBSERVATION_FIELDS)} "
            "FROM observations o WHERE o.crop_id = $1 ORDER BY o.ts DESC LIMIT $2",
            crop_id,
            limit,
        )
    return [_project_public_record(row, PUBLIC_OBSERVATION_FIELDS) for row in rows]


@app.post("/api/v1/crops/{crop_id}/observations", status_code=201)
async def create_observation(
    crop_id: int,
    obs: ObservationCreate,
    _write_access: None = Depends(require_write_access),
):
    async with pool.acquire() as conn:
        crop = await conn.fetchrow("SELECT zone, position, zone_id, position_id FROM crops WHERE id = $1", crop_id)
        if not crop:
            raise HTTPException(404, "Crop not found")

        row = await conn.fetchrow(
            """
            INSERT INTO observations (
                crop_id, zone, position, zone_id, position_id, obs_type, notes, severity,
                observer, health_score, species, count, affected_pct, photo_path,
                plant_height_cm, leaf_count, canopy_cover_pct, flowering_count,
                fruit_count, root_condition, mortality_count, stress_tags, source
            )
            VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8,
                $9, $10, $11, $12, $13, $14,
                $15, $16, $17, $18, $19, $20, $21, $22, 'api'
            )
            RETURNING *
        """,
            crop_id,
            obs.zone or crop["zone"],
            obs.position or crop["position"],
            crop["zone_id"],
            crop["position_id"],
            obs.obs_type,
            obs.notes,
            obs.severity,
            obs.observer,
            obs.health_score,
            obs.species,
            obs.count,
            obs.affected_pct,
            obs.photo_path,
            obs.plant_height_cm,
            obs.leaf_count,
            obs.canopy_cover_pct,
            obs.flowering_count,
            obs.fruit_count,
            obs.root_condition,
            obs.mortality_count,
            obs.stress_tags,
        )
    return dict(row)


@app.get("/api/v1/observations/recent", response_model=list[ObservationWithCrop])
async def recent_observations(limit: int = 20):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT {_sql_columns("o", PUBLIC_OBSERVATION_FIELDS)},
                   c.name AS crop_name, c.zone AS crop_zone,
                   cc.slug AS crop_catalog_slug
            FROM observations o
            LEFT JOIN crops c ON o.crop_id = c.id
            LEFT JOIN crop_catalog cc ON cc.id = c.crop_catalog_id
            WHERE o.crop_id IS NULL
               OR ({_public_crop_sql_predicate("cc.slug", "c.name", 2, 3)})
            ORDER BY o.ts DESC LIMIT $1
        """,
            limit,
            *_public_crop_sql_parameters(),
        )
    return _public_observation_rows(rows)


# ── Events ──


@app.get("/api/v1/crops/{crop_id}/events")
async def list_events(crop_id: int, limit: int = 20):
    async with pool.acquire() as conn:
        await _require_public_crop(conn, crop_id)
        rows = await conn.fetch(
            f"SELECT {_sql_columns('e', PUBLIC_EVENT_FIELDS)} "
            "FROM crop_events e WHERE e.crop_id = $1 ORDER BY e.ts DESC LIMIT $2",
            crop_id,
            limit,
        )
    return [_project_public_record(row, PUBLIC_EVENT_FIELDS) for row in rows]


@app.post("/api/v1/crops/{crop_id}/events", status_code=201)
async def create_event(crop_id: int, event: EventCreate, _write_access: None = Depends(require_write_access)):
    async with pool.acquire() as conn:
        crop = await conn.fetchrow("SELECT id FROM crops WHERE id = $1", crop_id)
        if not crop:
            raise HTTPException(404, "Crop not found")

        row = await conn.fetchrow(
            """
            INSERT INTO crop_events (crop_id, event_type, old_stage, new_stage, count, operator, source, notes)
            VALUES ($1, $2, $3, $4, $5, $6, 'api', $7)
            RETURNING *
        """,
            crop_id,
            event.event_type,
            event.old_stage,
            event.new_stage,
            event.count,
            event.operator,
            event.notes,
        )

        # Auto-update crop stage if new_stage provided
        if event.new_stage:
            await conn.execute(
                "UPDATE crops SET stage = $1, updated_at = now() WHERE id = $2", event.new_stage, crop_id
            )

    return dict(row)


# ── Health ──


@app.get("/api/v1/crops/{crop_id}/health", response_model=list[HealthTrendPoint])
async def crop_health(crop_id: int, days: int = 30):
    async with pool.acquire() as conn:
        await _require_public_crop(conn, crop_id)
        rows = await conn.fetch(
            """
            SELECT ts, health_score, obs_type, notes, source
            FROM observations
            WHERE crop_id = $1 AND health_score IS NOT NULL AND ts > now() - ($2 || ' days')::interval
            ORDER BY ts
        """,
            crop_id,
            str(days),
        )
    return redact_public_data([dict(r) for r in rows])


@app.get("/api/v1/greenhouses/{greenhouse_id}/health", response_model=list[CropHealthSummaryItem])
@app.get("/api/v1/health/summary", response_model=list[CropHealthSummaryItem])
async def health_summary(greenhouse_id: str = DEFAULT_GREENHOUSE):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT c.name, c.zone, c.position, c.stage,
                cc.slug AS crop_catalog_slug,
                ROUND(AVG(o.health_score)::numeric, 2) AS avg_health,
                COUNT(o.id) AS obs_count,
                MAX(o.ts) AS last_observed
            FROM crops c
            JOIN crop_catalog cc ON cc.id = c.crop_catalog_id
            LEFT JOIN observations o ON c.id = o.crop_id AND o.health_score IS NOT NULL AND o.ts > now() - interval '7 days'
            WHERE c.is_active = true
              AND c.greenhouse_id = $1
              AND {_public_crop_sql_predicate("cc.slug", "c.name", 2, 3)}
            GROUP BY c.id, c.name, c.zone, c.position, c.stage, cc.slug
            ORDER BY c.zone, c.position
        """,
            greenhouse_id,
            *_public_crop_sql_parameters(),
        )
    return _public_crop_rows(
        rows,
        fields=("name", "zone", "position", "stage", "avg_health", "obs_count", "last_observed"),
    )


# ── Zones ──


@app.get("/api/v1/zones", response_model=list[ZoneListItem])
async def list_zones():
    async with pool.acquire() as conn:
        zones = await conn.fetch(
            f"""
            SELECT z.slug AS zone,
                COUNT(c.id) AS active_crops,
                (SELECT ROUND(temp_avg::numeric, 1) FROM climate
                 WHERE ts > now() - interval '5 minutes' ORDER BY ts DESC LIMIT 1) AS current_temp
            FROM zones z
            LEFT JOIN LATERAL (
                SELECT c.id
                FROM crops c
                JOIN crop_catalog cc ON cc.id = c.crop_catalog_id
                {public_crop_zone_joins()}
                WHERE c.is_active
                  AND c.greenhouse_id = z.greenhouse_id
                  AND {_public_crop_zone_sql_predicate("z.id", "cc.slug", "c.name", 1, 2)}
            ) c ON TRUE
            WHERE z.greenhouse_id = $3
            GROUP BY z.id, z.slug
            ORDER BY z.slug
            """,
            PUBLIC_CROP_EXCLUDE_SLUGS_DB,
            PUBLIC_CROP_SQL_NAME_PATTERN,
            DEFAULT_GREENHOUSE,
        )
    return redact_public_data([dict(z) for z in zones])


@app.get("/api/v1/zones/{zone}", response_model=ZoneDetail)
async def get_zone(zone: str):
    async with pool.acquire() as conn:
        zone_exists = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM zones WHERE slug = $1 AND greenhouse_id = $2)",
            zone,
            DEFAULT_GREENHOUSE,
        )
        if not zone_exists:
            raise HTTPException(404, "Zone not found")
        crops = await conn.fetch(
            f"""
            SELECT {_sql_columns("c", PUBLIC_CROP_FIELDS)}, cc.slug AS crop_catalog_slug
            FROM crops c
            JOIN crop_catalog cc ON cc.id = c.crop_catalog_id
            {public_crop_zone_joins()}
            WHERE c.is_active
              AND {_public_crop_zone_sql_predicate("(SELECT id FROM zones WHERE slug = $1 AND greenhouse_id = $4)", "cc.slug", "c.name", 2, 3)}
            ORDER BY c.position
            """,
            zone,
            *_public_crop_sql_parameters(),
            DEFAULT_GREENHOUSE,
        )
        public_crops = _public_crop_rows(crops)
        observations = await conn.fetch(
            f"""
            SELECT {_sql_columns("o", PUBLIC_OBSERVATION_FIELDS)},
                   c.name AS crop_name, c.zone AS crop_zone, cc.slug AS crop_catalog_slug
            FROM observations o
            JOIN crops c ON o.crop_id = c.id
            JOIN crop_catalog cc ON cc.id = c.crop_catalog_id
            {public_crop_zone_joins()}
            WHERE {_public_crop_zone_sql_predicate("(SELECT id FROM zones WHERE slug = $1 AND greenhouse_id = $4)", "cc.slug", "c.name", 2, 3)}
              AND o.ts > now() - interval '7 days'
            ORDER BY o.ts DESC LIMIT 10
        """,
            zone,
            *_public_crop_sql_parameters(),
            DEFAULT_GREENHOUSE,
        )

    return redact_public_data(
        {
            "zone": zone,
            "crops": public_crops,
            "recent_observations": _public_observation_rows(observations),
        }
    )


# ── System ──


@app.get("/api/v1/status", response_model=APIStatus)
async def status():
    async with pool.acquire() as conn:
        crop_count = await conn.fetchval(
            f"""
            SELECT count(DISTINCT crop_catalog_slug)::int
            FROM v_position_current
            WHERE greenhouse_id = $1
              AND is_occupied
              AND crop_catalog_slug IS NOT NULL
              AND {_public_crop_sql_predicate("crop_catalog_slug", "crop_name", 2, 3)}
            """,
            DEFAULT_GREENHOUSE,
            *_public_crop_sql_parameters(),
        )
        obs_count = await conn.fetchval(
            f"""
            SELECT COUNT(*)
            FROM observations o
            LEFT JOIN crops c ON c.id = o.crop_id
            LEFT JOIN crop_catalog cc ON cc.id = c.crop_catalog_id
            WHERE o.crop_id IS NULL
               OR ({_public_crop_sql_predicate("cc.slug", "c.name", 1, 2)})
            """,
            *_public_crop_sql_parameters(),
        )
        latest = await conn.fetchval("SELECT MAX(ts) FROM climate")
    return {
        "status": "ok",
        "active_crops": crop_count or 0,
        "observations": obs_count,
        "latest_climate_ts": latest,
    }


@app.get("/api/v1/scorecard", response_model=ScorecardResponse)
async def planner_scorecard(scorecard_date: Annotated[date | None, Query(alias="date")] = None):
    """Planner scorecard metrics for a given date, defaulting to today."""
    async with pool.acquire() as conn:
        rows = await _fetch_planner_scorecard(conn, scorecard_date)
    try:
        return ScorecardResponse.from_metric_rows(rows)
    except ValidationError:
        # Belt-and-suspenders (band-compliance §7.1): ScorecardResponse uses
        # extra='forbid', so a brand-new fn_planner_scorecard metric (e.g. the
        # migration-146/147 graded keys before this schema is bumped) would
        # otherwise 500 the public endpoint. Drop only the unmodeled keys and
        # serve the metrics we DO recognize rather than failing the request.
        known = ScorecardResponse.metric_names()
        kept = [r for r in rows if str(r["metric"]) in known]
        return ScorecardResponse.from_metric_rows(kept)


@app.get("/api/v1/dli", response_model=DliEvidence)
@app.get("/api/v1/greenhouses/{greenhouse_id}/dli", response_model=DliEvidence)
async def get_dli_evidence(greenhouse_id: str = DEFAULT_GREENHOUSE):
    """Return interior crop DLI with explicit validity and provenance.

    The current sensor is broken, so this intentionally returns a null value
    and ``availability=unavailable``. Legacy proxy numbers remain forensic in
    the database and are never projected through this product endpoint.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT crop_dli_mol_m2_day AS value_mol_m2_day,
                   availability,
                   unavailable_reason,
                   provenance,
                   validity_revision,
                   valid_from,
                   valid_to
            FROM v_dli_current
            WHERE greenhouse_id = $1
            """,
            greenhouse_id,
        )
        if row is None:
            row = await conn.fetchrow(
                """
                SELECT NULL::double precision AS value_mol_m2_day,
                       COALESCE(availability, 'unavailable') AS availability,
                       COALESCE(unavailable_reason, 'validity_contract_missing') AS unavailable_reason,
                       COALESCE(provenance, 'unknown_unvalidated_source') AS provenance,
                       COALESCE(validity_revision, 'missing') AS validity_revision,
                       COALESCE(valid_from, '2024-01-01 00:00:00+00'::timestamptz) AS valid_from,
                       valid_to
                FROM (SELECT 1) anchor
                LEFT JOIN LATERAL fn_dli_validity(now(), $1) ON true
                """,
                greenhouse_id,
            )
    return DliEvidence.model_validate(redact_public_data(dict(row)))


@app.get("/api/v1/resources/daily")
async def daily_resource_accounting(
    resource_date: Annotated[date | None, Query(alias="date")] = None,
    greenhouse_id: str = DEFAULT_GREENHOUSE,
):
    """Scope-aware water and energy evidence for one local day.

    Gallons are accepted meter deltas only. Runtime-modeled energy and partial
    Shelly-measured energy retain independent scope, coverage, quality, and
    scoring-availability fields.
    """
    async with pool.acquire() as conn:
        target_date = resource_date or await conn.fetchval("SELECT (now() AT TIME ZONE 'America/Denver')::date")
        water = await _fetchrow_optional(
            conn,
            f"SELECT {_sql_columns('w', PUBLIC_WATER_RESOURCE_FIELDS)} "
            "FROM v_water_attribution_daily w WHERE w.date = $1 AND w.greenhouse_id = $2",
            target_date,
            greenhouse_id,
        )
        energy = await _fetchrow_optional(
            conn,
            f"SELECT {_sql_columns('e', PUBLIC_ENERGY_RESOURCE_FIELDS)} "
            "FROM v_energy_estimate_reconciliation e WHERE e.date = $1 AND e.greenhouse_id = $2",
            target_date,
            greenhouse_id,
        )
        health = await conn.fetch(
            f"SELECT {_sql_columns('h', PUBLIC_RESOURCE_HEALTH_FIELDS)} "
            "FROM v_resource_accounting_health h WHERE h.greenhouse_id = $1 ORDER BY h.resource",
            greenhouse_id,
        )
    energy_payload = _project_energy_resource(energy) if energy else None
    health_payload = [_project_resource_health(row) for row in health]
    return redact_public_data(
        {
            "date": target_date,
            "greenhouse_id": greenhouse_id,
            "water": _project_public_record(water, PUBLIC_WATER_RESOURCE_FIELDS) if water else None,
            "energy": energy_payload,
            "health": health_payload,
            "contract": {
                "water": "quality-filtered meter deltas; command-only runs never become gallons",
                "energy": "whole controlled-runtime model and partial two-channel measurement are separate scopes",
            },
        }
    )


async def _fetch_public_band_trace_generated_at() -> object:
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT now()")


async def _fetch_public_band_trace_latest(greenhouse_id: str) -> asyncpg.Record | None:
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            SELECT ts, greenhouse_id, temp_avg, vpd_avg,
                   temp_avg_smooth_15m, vpd_avg_smooth_30m,
                   crop_temp_low, crop_temp_high, crop_vpd_low, crop_vpd_high,
                   house_vpd_low, house_vpd_high,
                   fw_temp_low, fw_temp_high, fw_vpd_low, fw_vpd_high,
                   rb_temp_low, rb_temp_high, rb_vpd_low, rb_vpd_high,
                   crop_both_in_band, fw_both_in_band,
                   readback_matches_fw_band, trace_quality_flag
              FROM fn_band_trace(now() - interval '2 hours', now(), $1)
             ORDER BY ts DESC
             LIMIT 1
            """,
            greenhouse_id,
        )


async def _fetch_public_band_trace_summary(hours: int, greenhouse_id: str) -> asyncpg.Record | None:
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            WITH rows AS (
                SELECT crop_temp_in_band, crop_vpd_in_band, crop_both_in_band,
                       fw_temp_in_band, fw_vpd_in_band, fw_both_in_band,
                       readback_matches_fw_band, trace_quality_flag
                  FROM fn_band_trace(now() - ($1::int * interval '1 hour'), now(), $2)
            )
            SELECT count(*)::int AS sample_count,
                   round(avg(CASE WHEN crop_temp_in_band THEN 100.0 ELSE 0.0 END)::numeric, 1)::float
                       AS crop_temp_compliance_pct,
                   round(avg(CASE WHEN crop_vpd_in_band THEN 100.0 ELSE 0.0 END)::numeric, 1)::float
                       AS crop_vpd_compliance_pct,
                   round(avg(CASE WHEN crop_both_in_band THEN 100.0 ELSE 0.0 END)::numeric, 1)::float
                       AS crop_both_compliance_pct,
                   round(avg(CASE WHEN fw_temp_in_band THEN 100.0 ELSE 0.0 END)::numeric, 1)::float
                       AS fw_temp_compliance_pct,
                   round(avg(CASE WHEN fw_vpd_in_band THEN 100.0 ELSE 0.0 END)::numeric, 1)::float
                       AS fw_vpd_compliance_pct,
                   round(avg(CASE WHEN fw_both_in_band THEN 100.0 ELSE 0.0 END)::numeric, 1)::float
                       AS fw_both_compliance_pct,
                   round(avg(CASE WHEN readback_matches_fw_band THEN 100.0 ELSE 0.0 END)::numeric, 1)::float
                       AS readback_match_pct,
                   round(avg(CASE WHEN trace_quality_flag = 'ok' THEN 100.0 ELSE 0.0 END)::numeric, 1)::float
                       AS ok_trace_pct
              FROM rows
            """,
            hours,
            greenhouse_id,
        )


@app.get("/api/v1/public/band-trace", response_model=PublicBandTraceResponse)
async def public_band_trace(
    greenhouse_id: str = DEFAULT_GREENHOUSE,
    hours: Annotated[int, Query(ge=1, le=168)] = 24,
):
    """Public-safe crop-vs-firmware-vs-readback band trace summary."""
    cache_key = f"{greenhouse_id}:{hours}"
    now_mono = time.monotonic()
    cached = _PUBLIC_BAND_TRACE_CACHE.get(cache_key)
    if cached and now_mono - cached[0] < PUBLIC_BAND_TRACE_CACHE_TTL_S:
        return cached[1]

    generated_at, latest, summary = await asyncio.gather(
        _fetch_public_band_trace_generated_at(),
        _fetch_public_band_trace_latest(greenhouse_id),
        _fetch_public_band_trace_summary(hours, greenhouse_id),
    )

    response = PublicBandTraceResponse(
        generated_at=generated_at,
        greenhouse_id=greenhouse_id,
        latest=PublicBandTraceLatest.model_validate(dict(latest)) if latest else None,
        summary=PublicBandTraceSummary(
            hours=hours,
            sample_count=summary["sample_count"] if summary else 0,
            crop_temp_compliance_pct=_to_float(summary["crop_temp_compliance_pct"]) if summary else None,
            crop_vpd_compliance_pct=_to_float(summary["crop_vpd_compliance_pct"]) if summary else None,
            crop_both_compliance_pct=_to_float(summary["crop_both_compliance_pct"]) if summary else None,
            fw_temp_compliance_pct=_to_float(summary["fw_temp_compliance_pct"]) if summary else None,
            fw_vpd_compliance_pct=_to_float(summary["fw_vpd_compliance_pct"]) if summary else None,
            fw_both_compliance_pct=_to_float(summary["fw_both_compliance_pct"]) if summary else None,
            readback_match_pct=_to_float(summary["readback_match_pct"]) if summary else None,
            ok_trace_pct=_to_float(summary["ok_trace_pct"]) if summary else None,
        ),
    )
    response = PublicBandTraceResponse.model_validate(redact_public_data(response.model_dump()))
    _PUBLIC_BAND_TRACE_CACHE[cache_key] = (time.monotonic(), response)
    return response


@app.get("/api/v1/public/data-health", response_model=PublicDataHealthResponse)
async def public_data_health():
    """Public-safe proof freshness and trust-ledger status for launch pages."""
    async with pool.acquire() as conn:
        generated_at = await conn.fetchval("SELECT now()")
        pipeline_rows = await conn.fetch(
            """
            SELECT source, rows_1h, rows_24h, age_s, null_pct_1h
            FROM v_data_pipeline_health
            ORDER BY source
            """
        )
        open_critical_high = await conn.fetchval(
            """
            SELECT count(*)::int
            FROM alert_log
            WHERE disposition = 'open'
              AND severity IN ('critical', 'high')
            """
        )
        climate_action_log_age_s = await conn.fetchval(
            "SELECT extract(epoch FROM now() - max(ts))::int FROM climate_action_log"
        )
        climate_action_proof_missing = await conn.fetchval(CLIMATE_ACTION_PROOF_MISSING_SQL)
        try:
            async with conn.transaction():
                await conn.execute("SET LOCAL jit = off")
                await conn.execute("SET LOCAL statement_timeout = '6000ms'")
                trust_rows = await conn.fetch(
                    """
                    SELECT check_name, lower(status) AS status, metric_value, threshold_value, details
                    FROM v_data_trust_ledger
                    ORDER BY
                      CASE lower(status) WHEN 'fail' THEN 0 WHEN 'warn' THEN 1 ELSE 2 END,
                      check_name
                    """
                )
        except Exception:
            trust_rows = []

    pipeline_by_source = {r["source"]: r for r in pipeline_rows}
    climate_pipeline = pipeline_by_source.get("climate")
    forecast_pipeline = pipeline_by_source.get("forecast")
    climate_age_s = climate_pipeline["age_s"] if climate_pipeline else None
    forecast_age_s = forecast_pipeline["age_s"] if forecast_pipeline else None
    fallback_check_rows = [
        {
            "check_name": "climate_freshness",
            "status": "ok" if climate_age_s is not None and climate_age_s <= 300 else "fail",
            "metric_value": climate_age_s,
            "threshold_value": 300,
            "details": "climate age seconds",
        },
        {
            "check_name": "forecast_freshness",
            "status": "ok" if forecast_age_s is not None and forecast_age_s <= 21600 else "fail",
            "metric_value": forecast_age_s,
            "threshold_value": 21600,
            "details": "weather_forecast fetched_at age seconds",
        },
        {
            "check_name": "climate_action_log_freshness",
            "status": "ok" if climate_action_log_age_s is not None and climate_action_log_age_s <= 300 else "fail",
            "metric_value": climate_action_log_age_s,
            "threshold_value": 300,
            "details": "controller decision/action snapshot age seconds",
        },
        {
            "check_name": "climate_action_log_proof_complete",
            "status": "ok" if not climate_action_proof_missing else "fail",
            "metric_value": 0 if not climate_action_proof_missing else 1,
            "threshold_value": 0,
            "details": (
                "latest controller proof row has graphable target deltas and relay truth"
                if not climate_action_proof_missing
                else f"missing fields: {climate_action_proof_missing}"
            ),
        },
        {
            "check_name": "open_critical_or_high_alerts",
            "status": "ok" if not open_critical_high else "fail",
            "metric_value": open_critical_high or 0,
            "threshold_value": 0,
            "details": "open critical/high alerts",
        },
    ]
    check_rows = [dict(r) for r in trust_rows] if trust_rows else fallback_check_rows
    if not any(r["check_name"] == "open_critical_or_high_alerts" for r in check_rows):
        check_rows.append(fallback_check_rows[-1])
    if not any(r["check_name"] == "climate_action_log_freshness" for r in check_rows):
        check_rows.append(fallback_check_rows[2])
    if not any(r["check_name"] == "climate_action_log_proof_complete" for r in check_rows):
        check_rows.append(fallback_check_rows[3])
    checks = [
        PublicDataHealthCheck(
            name=r["check_name"],
            status=r["status"],
            metric_value=_to_float(r["metric_value"]),
            threshold_value=_to_float(r["threshold_value"]),
            details=r["details"] if isinstance(r["details"], str) else json.dumps(r["details"], sort_keys=True),
        )
        for r in check_rows
    ]
    pipeline_sources = [
        PublicPipelineHealthSource(
            source=r["source"],
            rows_1h=r["rows_1h"],
            rows_24h=r["rows_24h"],
            age_s=r["age_s"],
            null_pct_1h=_to_float(r["null_pct_1h"]),
        )
        for r in pipeline_rows
    ]
    response = PublicDataHealthResponse(
        generated_at=generated_at,
        overall_status=_overall_data_health(check_rows),
        checks=checks,
        pipeline_sources=pipeline_sources,
    )
    return PublicDataHealthResponse.model_validate(redact_public_data(response.model_dump()))


@app.get("/api/v1/public/gpu-power", response_model=PublicGpuPowerResponse)
async def public_gpu_power(
    greenhouse_id: str = DEFAULT_GREENHOUSE,
    hours: Annotated[int, Query(ge=1, le=168)] = 24,
    step_minutes: Annotated[int, Query(ge=1, le=60)] = 5,
):
    """Public-safe inference-fleet GPU and CPU telemetry mirrored from exporters."""
    if hours * 60 / step_minutes > 2000:
        raise HTTPException(
            status_code=400, detail="gpu-power request exceeds 2000 time buckets; increase step_minutes"
        )
    cache_key = f"{greenhouse_id}:{hours}:{step_minutes}"
    now_mono = time.monotonic()
    cached = _PUBLIC_GPU_POWER_CACHE.get(cache_key)
    if cached and now_mono - cached[0] < PUBLIC_GPU_POWER_CACHE_TTL_S:
        return cached[1]

    async with pool.acquire() as conn:
        generated_at = await conn.fetchval("SELECT now()")
        latest_rows = await conn.fetch(
            """
            SELECT ts, host, vm_name, purpose, gpu, device, model_name, watts,
                   gpu_util_pct, temperature_c, memory_used_mb, memory_free_mb, age_s
              FROM v_gpu_power_latest
             WHERE greenhouse_id = $1
             ORDER BY host, gpu
            """,
            greenhouse_id,
        )
        series_rows = await conn.fetch(
            """
            SELECT time_bucket($2::int * interval '1 minute', ts) AS bucket_ts,
                   host,
                   max(vm_name) AS vm_name,
                   gpu,
                   avg(watts)::double precision AS watts
              FROM gpu_power
             WHERE ts >= now() - ($1::int * interval '1 hour')
               AND greenhouse_id = $3
             GROUP BY bucket_ts, host, gpu
             ORDER BY bucket_ts, host, gpu
            """,
            hours,
            step_minutes,
            greenhouse_id,
        )
        summary = await conn.fetchrow(
            """
            WITH by_bucket AS (
                SELECT time_bucket($2::int * interval '1 minute', ts) AS bucket_ts,
                       host,
                       gpu,
                       avg(watts)::double precision AS watts
                  FROM gpu_power
                 WHERE ts >= now() - ($1::int * interval '1 hour')
                   AND greenhouse_id = $3
                 GROUP BY bucket_ts, host, gpu
            ),
            totals AS (
                SELECT bucket_ts, sum(watts)::double precision AS total_watts
                  FROM by_bucket
                 GROUP BY bucket_ts
            )
            SELECT max(total_watts)::double precision AS peak_total_watts,
                   avg(total_watts)::double precision AS avg_total_watts
              FROM totals
            """,
            hours,
            step_minutes,
            greenhouse_id,
        )
        cpu_latest_rows = await conn.fetch(
            """
            SELECT ts, host, vm_name, purpose, cpu_util_pct, load1, cores,
                   memory_used_pct, age_s
              FROM v_infra_cpu_latest
             WHERE greenhouse_id = $1
             ORDER BY host
            """,
            greenhouse_id,
        )
        cpu_series_rows = await conn.fetch(
            """
            SELECT time_bucket($2::int * interval '1 minute', ts) AS bucket_ts,
                   host,
                   max(vm_name) AS vm_name,
                   avg(cpu_util_pct)::double precision AS cpu_util_pct,
                   avg(memory_used_pct)::double precision AS memory_used_pct
              FROM infra_cpu
             WHERE ts >= now() - ($1::int * interval '1 hour')
               AND greenhouse_id = $3
             GROUP BY bucket_ts, host
             ORDER BY bucket_ts, host
            """,
            hours,
            step_minutes,
            greenhouse_id,
        )
        cpu_summary = await conn.fetchrow(
            """
            WITH by_bucket AS (
                SELECT time_bucket($2::int * interval '1 minute', ts) AS bucket_ts,
                       avg(cpu_util_pct)::double precision AS avg_cpu_util_pct
                  FROM infra_cpu
                 WHERE ts >= now() - ($1::int * interval '1 hour')
                   AND greenhouse_id = $3
                 GROUP BY bucket_ts
            )
            SELECT max(avg_cpu_util_pct)::double precision AS peak_avg_cpu_util_pct
              FROM by_bucket
            """,
            hours,
            step_minutes,
            greenhouse_id,
        )

    latest = [
        PublicGpuPowerLatest(
            ts=r["ts"],
            host=r["host"],
            vm_name=r["vm_name"],
            purpose=r["purpose"],
            gpu=r["gpu"],
            device=r["device"],
            model_name=r["model_name"],
            watts=round(float(r["watts"]), 1),
            gpu_util_pct=round(float(r["gpu_util_pct"]), 1) if r["gpu_util_pct"] is not None else None,
            temperature_c=round(float(r["temperature_c"]), 1) if r["temperature_c"] is not None else None,
            memory_used_mb=round(float(r["memory_used_mb"]), 1) if r["memory_used_mb"] is not None else None,
            memory_free_mb=round(float(r["memory_free_mb"]), 1) if r["memory_free_mb"] is not None else None,
            age_s=r["age_s"],
        )
        for r in latest_rows
    ]
    series = [
        PublicGpuPowerPoint(
            ts=r["bucket_ts"],
            host=r["host"],
            vm_name=r["vm_name"],
            gpu=r["gpu"],
            watts=round(float(r["watts"]), 1),
        )
        for r in series_rows
    ]
    cpu_latest = [
        PublicInfraCpuLatest(
            ts=r["ts"],
            host=r["host"],
            vm_name=r["vm_name"],
            purpose=r["purpose"],
            cpu_util_pct=round(float(r["cpu_util_pct"]), 1) if r["cpu_util_pct"] is not None else None,
            load1=round(float(r["load1"]), 2) if r["load1"] is not None else None,
            cores=r["cores"],
            memory_used_pct=round(float(r["memory_used_pct"]), 1) if r["memory_used_pct"] is not None else None,
            age_s=r["age_s"],
        )
        for r in cpu_latest_rows
    ]
    cpu_series = [
        PublicInfraCpuPoint(
            ts=r["bucket_ts"],
            host=r["host"],
            vm_name=r["vm_name"],
            cpu_util_pct=round(float(r["cpu_util_pct"]), 1) if r["cpu_util_pct"] is not None else None,
            memory_used_pct=round(float(r["memory_used_pct"]), 1) if r["memory_used_pct"] is not None else None,
        )
        for r in cpu_series_rows
    ]
    latest_total = round(sum(item.watts for item in latest), 1) if latest else None
    latest_gpu_utils = [item.gpu_util_pct for item in latest if item.gpu_util_pct is not None]
    latest_cpu_utils = [item.cpu_util_pct for item in cpu_latest if item.cpu_util_pct is not None]
    payload = PublicGpuPowerResponse(
        generated_at=generated_at,
        greenhouse_id=greenhouse_id,
        source="Verdify mirror of Nexus-scraped DCGM and node-exporter telemetry",
        hours=hours,
        step_minutes=step_minutes,
        latest_total_watts=latest_total,
        latest_gpu_count=len(latest),
        latest_avg_gpu_util_pct=round(sum(latest_gpu_utils) / len(latest_gpu_utils), 1) if latest_gpu_utils else None,
        peak_total_watts=round(float(summary["peak_total_watts"]), 1)
        if summary and summary["peak_total_watts"] is not None
        else None,
        avg_total_watts=round(float(summary["avg_total_watts"]), 1)
        if summary and summary["avg_total_watts"] is not None
        else None,
        latest_avg_cpu_util_pct=round(sum(latest_cpu_utils) / len(latest_cpu_utils), 1) if latest_cpu_utils else None,
        peak_avg_cpu_util_pct=round(float(cpu_summary["peak_avg_cpu_util_pct"]), 1)
        if cpu_summary and cpu_summary["peak_avg_cpu_util_pct"] is not None
        else None,
        latest=latest,
        series=series,
        cpu_latest=cpu_latest,
        cpu_series=cpu_series,
    )
    payload = PublicGpuPowerResponse.model_validate(redact_public_data(payload.model_dump()))
    _PUBLIC_GPU_POWER_CACHE[cache_key] = (time.monotonic(), payload)
    return payload


def _public_planner_trigger(row) -> PublicPlannerTrigger | None:
    if row is None:
        return None
    return PublicPlannerTrigger(
        id=int(row["id"]),
        event_type=row["event_type"],
        event_label=row["event_label"],
        instance=row["instance"],
        expected_at=row["expected_at"],
        due_at=row["due_at"],
        delivered_at=row["delivered_at"],
        resolved_at=row["resolved_at"],
        status=row["status"],
        expected_action=row["expected_action"],
        trigger_id=str(row["trigger_id"]) if row["trigger_id"] else None,
        resulting_plan_id=row["resulting_plan_id"],
    )


def _public_planner_delivery(row) -> PublicPlannerDelivery | None:
    if row is None:
        return None
    hermes_run_id = row["hermes_run_id"]
    return PublicPlannerDelivery(
        id=int(row["id"]),
        event_type=row["event_type"],
        event_label=row["event_label"],
        delivered_at=row["delivered_at"],
        status=row["status"],
        instance=row["instance"],
        session_key=row["session_key"],
        wake_mode=row["wake_mode"],
        gateway_status=row["gateway_status"],
        hermes_run_id=hermes_run_id,
        trigger_id=str(row["trigger_id"]) if row["trigger_id"] else None,
        resulting_plan_id=row["resulting_plan_id"],
        plan_written_at=row["plan_written_at"],
        planner_gateway=PLANNER_GATEWAY_LABEL if hermes_run_id else "legacy",
        planner_model_label=PLANNER_MODEL_LABEL if hermes_run_id else None,
    )


def _pending_sla_age_buckets(row) -> dict[str, int]:
    if row is None:
        return {
            "within_sla": 0,
            "overdue_lt_15m": 0,
            "overdue_15m_1h": 0,
            "overdue_gt_1h": 0,
        }
    return {
        "within_sla": int(row["within_sla"] or 0),
        "overdue_lt_15m": int(row["overdue_lt_15m"] or 0),
        "overdue_15m_1h": int(row["overdue_15m_1h"] or 0),
        "overdue_gt_1h": int(row["overdue_gt_1h"] or 0),
    }


def _active_plan_range_violation_count(rows) -> int:
    violations = 0
    for row in rows:
        parameter = row["parameter"]
        try:
            value = float(row["value"])
        except (TypeError, ValueError):
            violations += 1
            continue
        error = registry_value_error(parameter, value)
        if parameter in FORCED_ON_SWITCH_PARAMS and value < 0.5:
            error = "controller_locked_on"
        if error:
            violations += 1
    return violations


@app.get("/api/v1/public/planner-health", response_model=PublicPlannerHealthResponse)
async def public_planner_health():
    """Public-safe expected-trigger SLA surface for planner reliability."""
    async with pool.acquire() as conn:
        summary = await conn.fetchrow(
            f"SELECT {_sql_columns('h', PUBLIC_PLANNER_HEALTH_FIELDS)} FROM v_planner_trigger_health h"
        )
        trigger_projection = """
            SELECT id, event_type, event_label, instance, expected_at, due_at,
                   delivered_at, resolved_at, status, expected_action, trigger_id,
                   resulting_plan_id
              FROM planner_trigger_ledger
        """
        trigger_rows = await conn.fetch(
            trigger_projection
            + """
             WHERE expected_at >= now() - interval '36 hours'
             ORDER BY expected_at DESC
             LIMIT 40
            """
        )
        last_expected = await conn.fetchrow(
            trigger_projection
            + """
             WHERE expected_at >= now() - interval '36 hours'
             ORDER BY expected_at DESC
             LIMIT 1
            """
        )
        last_delivered = await conn.fetchrow(
            trigger_projection
            + """
             WHERE expected_at >= now() - interval '36 hours'
               AND delivered_at IS NOT NULL
             ORDER BY delivered_at DESC
             LIMIT 1
            """
        )
        last_resolved = await conn.fetchrow(
            trigger_projection
            + """
             WHERE expected_at >= now() - interval '36 hours'
               AND resolved_at IS NOT NULL
             ORDER BY resolved_at DESC
             LIMIT 1
            """
        )
        pending_by_sla_age = await conn.fetchrow(
            """
            SELECT
              count(*) FILTER (WHERE due_at >= now())::int AS within_sla,
              count(*) FILTER (
                WHERE due_at < now()
                  AND now() - due_at < interval '15 minutes'
              )::int AS overdue_lt_15m,
              count(*) FILTER (
                WHERE due_at < now()
                  AND now() - due_at >= interval '15 minutes'
                  AND now() - due_at < interval '1 hour'
              )::int AS overdue_15m_1h,
              count(*) FILTER (
                WHERE due_at < now()
                  AND now() - due_at >= interval '1 hour'
              )::int AS overdue_gt_1h
              FROM planner_trigger_ledger
             WHERE expected_at >= now() - interval '36 hours'
               AND status IN ('expected', 'delivered')
            """
        )
        current_delivery = await conn.fetchrow(
            """
            SELECT session_key, hermes_run_id
              FROM plan_delivery_log
             WHERE greenhouse_id = $1
             ORDER BY delivered_at DESC
             LIMIT 1
            """,
            DEFAULT_GREENHOUSE,
        )
        recent_deliveries = await conn.fetch(
            """
            SELECT id, event_type, event_label, delivered_at, status, instance,
                   session_key, wake_mode, gateway_status, hermes_run_id,
                   trigger_id, resulting_plan_id, plan_written_at
              FROM plan_delivery_log
             WHERE greenhouse_id = $1
               AND delivered_at >= now() - interval '36 hours'
             ORDER BY delivered_at DESC
             LIMIT 40
            """,
            DEFAULT_GREENHOUSE,
        )
        active_plan_candidates = await conn.fetch(
            """
            SELECT parameter, value
              FROM setpoint_plan
             WHERE greenhouse_id = $1
               AND is_active = true
               AND source IN ('iris', 'plan')
             ORDER BY ts, parameter
             LIMIT 10000
            """,
            DEFAULT_GREENHOUSE,
        )

    if summary is None:
        raise HTTPException(status_code=503, detail="Planner health view unavailable")
    summary = _project_planner_health(summary)

    required_failure_count = int(summary["required_failure_count"] or 0)
    missed_expected_count = int(summary["missed_expected_count"] or 0)
    overdue_delivered_count = int(summary["overdue_delivered_count"] or 0)
    if required_failure_count > 0:
        overall_status = "fail"
    elif missed_expected_count > 0 or overdue_delivered_count > 0:
        overall_status = "warn"
    else:
        overall_status = "ok"

    latest_required = summary["latest_required"] or []

    response = PublicPlannerHealthResponse(
        generated_at=summary["generated_at"],
        overall_status=overall_status,
        missed_expected_count=missed_expected_count,
        overdue_delivered_count=overdue_delivered_count,
        required_failure_count=required_failure_count,
        recent_expected_count=int(summary["recent_expected_count"] or 0),
        resolved_count=int(summary["resolved_count"] or 0),
        latest_required=latest_required,
        last_expected_trigger=_public_planner_trigger(last_expected),
        last_delivered_trigger=_public_planner_trigger(last_delivered),
        last_resolved_trigger=_public_planner_trigger(last_resolved),
        pending_by_sla_age=_pending_sla_age_buckets(pending_by_sla_age),
        current_session_key=current_delivery["session_key"] if current_delivery else None,
        current_model_label=PLANNER_MODEL_LABEL,
        current_hermes_run_id=current_delivery["hermes_run_id"] if current_delivery else None,
        active_plan_range_violation_count=_active_plan_range_violation_count(active_plan_candidates),
        recent_deliveries=[
            delivery for row in recent_deliveries if (delivery := _public_planner_delivery(row)) is not None
        ],
        recent_triggers=[trigger for r in trigger_rows if (trigger := _public_planner_trigger(r)) is not None],
    )
    return PublicPlannerHealthResponse.model_validate(redact_public_data(response.model_dump()))


@app.get("/api/v1/public/cameras/{camera_id}/latest.jpg")
async def public_camera_snapshot(camera_id: str, h: Annotated[int, Query(ge=120, le=1080)] = 1080):
    """Public-safe proxy for the two greenhouse source-stream camera snapshots."""
    if camera_id not in PUBLIC_CAMERA_IDS:
        raise HTTPException(status_code=404, detail="Unknown public camera")
    try:
        data = await asyncio.to_thread(_fetch_camera_snapshot_sync, camera_id, h)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Camera snapshot unavailable") from exc
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=20, stale-while-revalidate=60"},
    )


@app.post("/api/v1/public/contact", status_code=202)
async def public_contact_submission(request: Request):
    """Accept public project contact without publishing a personal email address."""
    payload, is_form_submission = await _parse_contact_submission(request)

    if _trim(payload.website, 200):
        if is_form_submission:
            return RedirectResponse("https://lab.verdify.ai/start/contact?sent=1", status_code=303)
        return {"ok": True, "status": "received"}

    name = _trim(payload.name, 120)
    email = _trim(payload.email, 254)
    message = _trim(payload.message, 4000)
    affiliation = _trim(payload.affiliation, 160)
    topic = (payload.topic or "other").strip().lower()

    if not name or len(name) < 2:
        raise HTTPException(status_code=422, detail="Name must be at least 2 characters")
    if not email or not CONTACT_EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="A valid reply email is required")
    if not message or len(message) < 20:
        raise HTTPException(status_code=422, detail="Message must be at least 20 characters")
    if topic not in CONTACT_ALLOWED_TOPICS:
        topic = "other"
    if len(CONTACT_URL_RE.findall(message)) > 3:
        raise HTTPException(status_code=422, detail="Message contains too many links")

    remote_ip = _client_ip(request)
    ip_hash = _contact_ip_hash(remote_ip)
    turnstile_verified = await _verify_turnstile_if_configured(payload.turnstile_token, remote_ip)
    max_per_ip_hour = _int_env("VERDIFY_CONTACT_MAX_PER_IP_HOUR", 5)
    max_per_email_day = _int_env("VERDIFY_CONTACT_MAX_PER_EMAIL_DAY", 4)

    user_agent = _trim(request.headers.get("User-Agent"), 500)
    referrer = _trim(request.headers.get("Referer"), 500)
    metadata = {
        "source": "lab.verdify.ai/start/contact",
        "cf_ray": _trim(request.headers.get("CF-Ray"), 120),
        "turnstile_configured": bool(os.environ.get("VERDIFY_TURNSTILE_SECRET", "").strip()),
    }

    async with pool.acquire() as conn:
        recent_ip = await conn.fetchval(
            """
            SELECT count(*)::int
            FROM public_contact_submissions
            WHERE ip_hash = $1
              AND created_at > now() - interval '1 hour'
              AND status <> 'spam'
            """,
            ip_hash,
        )
        if recent_ip is not None and recent_ip >= max_per_ip_hour:
            raise HTTPException(status_code=429, detail="Too many contact submissions from this network")

        recent_email = await conn.fetchval(
            """
            SELECT count(*)::int
            FROM public_contact_submissions
            WHERE lower(email) = lower($1)
              AND created_at > now() - interval '1 day'
              AND status <> 'spam'
            """,
            email,
        )
        if recent_email is not None and recent_email >= max_per_email_day:
            raise HTTPException(status_code=429, detail="Too many contact submissions from this address")

        submission = await conn.fetchrow(
            """
            INSERT INTO public_contact_submissions (
              name, email, topic, affiliation, message, ip_hash,
              user_agent, referrer, turnstile_verified, metadata
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb)
            RETURNING id, created_at
            """,
            name,
            email,
            topic,
            affiliation,
            message,
            ip_hash,
            user_agent,
            referrer,
            turnstile_verified,
            json.dumps(metadata),
        )
        notify_to = await conn.fetchval(
            "SELECT owner_email FROM greenhouses WHERE id = $1",
            DEFAULT_GREENHOUSE,
        )

    await _notify_contact_submission(
        submission_id=submission["id"],
        created_at=submission["created_at"],
        notify_to=notify_to,
        name=name,
        email=email,
        topic=topic,
        affiliation=affiliation,
        message=message,
        user_agent=user_agent,
        referrer=referrer,
    )

    if is_form_submission:
        return RedirectResponse("https://lab.verdify.ai/start/contact?sent=1", status_code=303)
    return {"ok": True, "status": "received"}


@app.post("/api/v1/admin/contact-notifications/retry")
async def retry_contact_notifications(
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    _write_access: None = Depends(require_write_access),
):
    """Retry email notifications for queued contact submissions."""
    async with pool.acquire() as conn:
        notify_to = await conn.fetchval(
            "SELECT owner_email FROM greenhouses WHERE id = $1",
            DEFAULT_GREENHOUSE,
        )
        rows = await conn.fetch(
            """
            SELECT id, created_at, name, email, topic, affiliation, message, user_agent, referrer
            FROM public_contact_submissions
            WHERE notification_status IN ('pending', 'failed')
              AND status <> 'spam'
            ORDER BY created_at
            LIMIT $1
            """,
            limit,
        )

    results = []
    for row in rows:
        await _notify_contact_submission(
            submission_id=row["id"],
            created_at=row["created_at"],
            notify_to=notify_to,
            name=row["name"],
            email=row["email"],
            topic=row["topic"],
            affiliation=row["affiliation"],
            message=row["message"],
            user_agent=row["user_agent"],
            referrer=row["referrer"],
        )
        async with pool.acquire() as conn:
            updated = await conn.fetchrow(
                """
                SELECT id, notification_status, notification_error
                FROM public_contact_submissions
                WHERE id = $1
                """,
                row["id"],
            )
        results.append(dict(updated))

    return {"ok": True, "attempted": len(results), "results": results}


@app.get("/api/v1/public/home-metrics", response_model=PublicHomeMetrics)
async def public_home_metrics(greenhouse_id: str = DEFAULT_GREENHOUSE):
    """Launch-safe live metrics for lab.verdify.ai proof cards."""
    cache_key = greenhouse_id
    now_mono = time.monotonic()
    cached = _PUBLIC_HOME_METRICS_CACHE.get(cache_key)
    if cached and now_mono - cached[0] < PUBLIC_HOME_METRICS_CACHE_TTL_S:
        return cached[1]

    async with pool.acquire() as conn:
        generated_at = await conn.fetchval("SELECT now()")
        climate_summary = await conn.fetchrow(
            """
            SELECT count(*)::int AS climate_rows,
                   COALESCE(
                     round((extract(epoch FROM max(ts) - min(ts)) / 86400.0)::numeric, 1),
                     0
                   )::float AS climate_days
            FROM climate
            WHERE greenhouse_id = $1
            """,
            greenhouse_id,
        )
        latest_climate = await conn.fetchrow(
            """
            SELECT ts,
                   extract(epoch FROM now() - ts)::int AS age_s,
                   round(temp_avg::numeric, 1)::float AS indoor_temp_f,
                   round(vpd_avg::numeric, 2)::float AS indoor_vpd_kpa,
                   round(outdoor_temp_f::numeric, 1)::float AS outdoor_temp_f,
                   round(outdoor_rh_pct::numeric, 1)::float AS outdoor_rh_pct
            FROM climate
            WHERE greenhouse_id = $1
            ORDER BY ts DESC
            LIMIT 1
            """,
            greenhouse_id,
        )
        active_crops = await conn.fetchval(
            f"""
            SELECT count(DISTINCT crop_catalog_slug)::int
            FROM v_position_current
            WHERE greenhouse_id = $1
              AND is_occupied
              AND {_public_crop_sql_predicate("crop_catalog_slug", "crop_name", 2, 3)}
            """,
            greenhouse_id,
            *_public_crop_sql_parameters(),
        )
        plan_count = await conn.fetchval(
            """
            SELECT count(*)::int
            FROM plan_journal
            WHERE greenhouse_id = $1
              AND plan_id LIKE 'iris-%'
              AND plan_id NOT LIKE 'iris-reactive%'
              AND plan_id NOT LIKE 'iris-fix%'
            """,
            greenhouse_id,
        )
        lesson_count = await conn.fetchval(
            "SELECT count(*)::int FROM planner_lessons WHERE greenhouse_id = $1 AND is_active",
            greenhouse_id,
        )
        last_plan = await conn.fetchrow(
            """
            SELECT plan_id, created_at, extract(epoch FROM now() - created_at)::int AS age_s
            FROM plan_journal
            WHERE greenhouse_id = $1
              AND plan_id LIKE 'iris-%'
              AND plan_id NOT LIKE 'iris-reactive%'
              AND plan_id NOT LIKE 'iris-fix%'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            greenhouse_id,
        )
        score_rows = await _fetch_planner_scorecard(conn)
        scorecard = {r["metric"]: _to_float(r["value"]) for r in score_rows}
        water_resource = await _fetchrow_optional(
            conn,
            f"""
            SELECT {_sql_columns("w", PUBLIC_WATER_RESOURCE_FIELDS)}
            FROM v_water_attribution_daily w
            WHERE w.date = (now() AT TIME ZONE 'America/Denver')::date
              AND w.greenhouse_id = $1
            """,
            greenhouse_id,
        )
        energy_resource = await _fetchrow_optional(
            conn,
            f"""
            SELECT {_sql_columns("e", PUBLIC_ENERGY_RESOURCE_FIELDS)}
            FROM v_energy_estimate_reconciliation e
            WHERE e.date = (now() AT TIME ZONE 'America/Denver')::date
              AND e.greenhouse_id = $1
            """,
            greenhouse_id,
        )
        water_ledger_health = await conn.fetchrow(
            f"SELECT {_sql_columns('h', PUBLIC_WATER_LEDGER_HEALTH_FIELDS)} "
            "FROM v_water_ledger_health h WHERE h.greenhouse_id = $1",
            greenhouse_id,
        )
        open_critical_high = await conn.fetchval(
            """
            SELECT count(*)::int
            FROM alert_log
            WHERE greenhouse_id = $1
              AND disposition = 'open'
              AND severity IN ('critical', 'high')
            """,
            greenhouse_id,
        )
        forecast_health = await conn.fetchrow(
            """
            SELECT age_s
            FROM v_data_pipeline_health
            WHERE source = 'forecast'
            """
        )
        latest_action = await conn.fetchrow(
            """
            SELECT extract(epoch FROM now() - ts)::int AS age_s,
                   climate_action,
                   priority_axis,
                   round(temp_target_delta_f::numeric, 2)::float AS temp_target_delta_f,
                   round(vpd_target_delta_kpa::numeric, 3)::float AS vpd_target_delta_kpa,
                   round(temp_band_error_f::numeric, 2)::float AS temp_band_error_f,
                   round(vpd_band_error_kpa::numeric, 3)::float AS vpd_band_error_kpa,
                   moisture_assist_state,
                   wet_assist_allowed,
                   wet_assist_block_reason,
                   fog_allowed,
                   fog_block_reason,
                   relay_truth,
                   sensor_status
            FROM climate_action_log
            WHERE COALESCE(greenhouse_id, 'vallery') = $1
            ORDER BY ts DESC
            LIMIT 1
            """,
            greenhouse_id,
        )
        latest_action_data = (
            _coerce_jsonb(dict(latest_action), "relay_truth", "sensor_status") if latest_action else None
        )
        if latest_action_data:
            latest_action_data["relay_truth"] = _project_public_record(
                latest_action_data.get("relay_truth") or {},
                PUBLIC_RELAY_TRUTH_FIELDS,
            )
            latest_action_data["sensor_status"] = _project_public_record(
                latest_action_data.get("sensor_status") or {},
                PUBLIC_SENSOR_STATUS_FIELDS,
            )
        climate_action_log_age_s = latest_action_data["age_s"] if latest_action_data else None
        climate_action_proof_missing = await conn.fetchval(CLIMATE_ACTION_PROOF_MISSING_SQL)

    climate_age_s = latest_climate["age_s"] if latest_climate else None
    forecast_age_s = forecast_health["age_s"] if forecast_health else None
    data_checks = [
        {
            "check_name": "climate_freshness",
            "status": "ok" if climate_age_s is not None and climate_age_s <= 300 else "fail",
            "metric_value": climate_age_s,
            "threshold_value": 300,
            "details": "climate age seconds",
        },
        {
            "check_name": "forecast_freshness",
            "status": "ok" if forecast_age_s is not None and forecast_age_s <= 21600 else "fail",
            "metric_value": forecast_age_s,
            "threshold_value": 21600,
            "details": "weather_forecast fetched_at age seconds",
        },
        {
            "check_name": "water_ledger_freshness",
            "status": ("ok" if water_ledger_health and water_ledger_health["ledger_status"] == "fresh" else "fail"),
            "metric_value": (
                _to_float(water_ledger_health["materializer_lag_seconds"]) if water_ledger_health else None
            ),
            "threshold_value": 300,
            "details": (
                f"water ledger status={water_ledger_health['ledger_status']}"
                if water_ledger_health
                else "water ledger health unavailable"
            ),
        },
        {
            "check_name": "climate_action_log_freshness",
            "status": "ok" if climate_action_log_age_s is not None and climate_action_log_age_s <= 300 else "fail",
            "metric_value": climate_action_log_age_s,
            "threshold_value": 300,
            "details": "controller decision/action snapshot age seconds",
        },
        {
            "check_name": "climate_action_log_proof_complete",
            "status": "ok" if not climate_action_proof_missing else "fail",
            "metric_value": 0 if not climate_action_proof_missing else 1,
            "threshold_value": 0,
            "details": (
                "latest controller proof row has graphable target deltas and relay truth"
                if not climate_action_proof_missing
                else f"missing fields: {climate_action_proof_missing}"
            ),
        },
        {
            "check_name": "open_critical_or_high_alerts",
            "status": "ok" if not open_critical_high else "fail",
            "metric_value": open_critical_high or 0,
            "threshold_value": 0,
            "details": "open critical/high alerts",
        },
    ]
    warning_checks = [
        PublicDataHealthCheck(
            name=r["check_name"],
            status=r["status"],
            metric_value=_to_float(r["metric_value"]),
            threshold_value=_to_float(r["threshold_value"]),
            details=r["details"],
        )
        for r in data_checks
        if r["status"] != "ok"
    ]
    metrics = PublicHomeMetrics(
        generated_at=generated_at,
        greenhouse_id=greenhouse_id,
        climate_rows=climate_summary["climate_rows"] if climate_summary else 0,
        climate_days=climate_summary["climate_days"] if climate_summary else 0,
        active_crops=active_crops or 0,
        plan_count=plan_count or 0,
        lesson_count=lesson_count or 0,
        latest_climate_ts=latest_climate["ts"] if latest_climate else None,
        latest_climate_age_s=latest_climate["age_s"] if latest_climate else None,
        indoor_temp_f=latest_climate["indoor_temp_f"] if latest_climate else None,
        indoor_vpd_kpa=latest_climate["indoor_vpd_kpa"] if latest_climate else None,
        outdoor_temp_f=latest_climate["outdoor_temp_f"] if latest_climate else None,
        outdoor_rh_pct=latest_climate["outdoor_rh_pct"] if latest_climate else None,
        last_plan_id=last_plan["plan_id"] if last_plan else None,
        last_plan_created_at=last_plan["created_at"] if last_plan else None,
        last_plan_age_s=last_plan["age_s"] if last_plan else None,
        planner_score_today=scorecard.get("planner_score"),
        planner_score_scope=(
            "climate_plus_resource"
            if scorecard.get("resource_terms_available") == 1.0
            else "climate_only_resource_excluded"
        ),
        planner_score_resource_weight_pct=scorecard.get("planner_score_resource_weight_pct") or 0,
        planner_score_resource_terms_available=scorecard.get("resource_terms_available") == 1.0,
        compliance_pct_today=scorecard.get("compliance_pct"),
        cost_today_usd=(
            scorecard.get("cost_total")
            if water_resource
            and water_resource["available_for_scoring"]
            and energy_resource
            and energy_resource["modeled_available_for_scoring"]
            else None
        ),
        cost_today_estimate_usd=scorecard.get("cost_total"),
        water_today_gal=(
            _to_float(water_resource["quality_filtered_meter_gal"])
            if water_resource and water_resource["available_for_scoring"]
            else None
        ),
        water_today_observed_gal=(_to_float(water_resource["quality_filtered_meter_gal"]) if water_resource else None),
        water_today_quality=water_resource["resource_quality"] if water_resource else "unavailable",
        water_today_available_for_scoring=bool(water_resource and water_resource["available_for_scoring"]),
        runtime_modeled_kwh=_to_float(energy_resource["kwh_estimated"]) if energy_resource else None,
        runtime_modeled_kwh_low=_to_float(energy_resource["modeled_kwh_low"]) if energy_resource else None,
        runtime_modeled_kwh_high=_to_float(energy_resource["modeled_kwh_high"]) if energy_resource else None,
        runtime_model_quality=energy_resource["model_quality"] if energy_resource else "unavailable",
        runtime_model_available_for_scoring=bool(energy_resource and energy_resource["modeled_available_for_scoring"]),
        partial_measured_kwh=_to_float(energy_resource["measured_kwh"]) if energy_resource else None,
        partial_meter_coverage_pct=(_to_float(energy_resource["meter_coverage_pct"]) if energy_resource else None),
        partial_meter_quality=energy_resource["measured_quality"] if energy_resource else "unavailable",
        partial_meter_available_for_scoring=bool(energy_resource and energy_resource["measured_available_for_scoring"]),
        open_critical_high_alerts=open_critical_high or 0,
        climate_action_log_age_s=climate_action_log_age_s,
        controller_climate_action=latest_action_data["climate_action"] if latest_action_data else None,
        controller_priority_axis=latest_action_data["priority_axis"] if latest_action_data else None,
        controller_temp_target_delta_f=latest_action_data["temp_target_delta_f"] if latest_action_data else None,
        controller_vpd_target_delta_kpa=latest_action_data["vpd_target_delta_kpa"] if latest_action_data else None,
        controller_temp_band_error_f=latest_action_data["temp_band_error_f"] if latest_action_data else None,
        controller_vpd_band_error_kpa=latest_action_data["vpd_band_error_kpa"] if latest_action_data else None,
        controller_moisture_assist_state=latest_action_data["moisture_assist_state"] if latest_action_data else None,
        controller_wet_assist_allowed=latest_action_data["wet_assist_allowed"] if latest_action_data else None,
        controller_wet_assist_block_reason=latest_action_data["wet_assist_block_reason"]
        if latest_action_data
        else None,
        controller_fog_allowed=latest_action_data["fog_allowed"] if latest_action_data else None,
        controller_fog_block_reason=latest_action_data["fog_block_reason"] if latest_action_data else None,
        controller_relay_truth=latest_action_data["relay_truth"] if latest_action_data else None,
        controller_sensor_status=latest_action_data["sensor_status"] if latest_action_data else None,
        data_health_status=_overall_data_health(data_checks),
        data_health_warnings=warning_checks[:8],
    )
    metrics = PublicHomeMetrics.model_validate(redact_public_data(metrics.model_dump()))
    _PUBLIC_HOME_METRICS_CACHE[cache_key] = (time.monotonic(), metrics)
    return metrics


@app.get("/api/v1/public/evidence-snapshot")
async def public_evidence_snapshot(greenhouse_id: str = DEFAULT_GREENHOUSE):
    """Crawler-friendly public proof snapshot for evidence subpages."""
    async with pool.acquire() as conn:
        generated_at = await conn.fetchval("SELECT now()")
        score_rows = await _fetch_planner_scorecard(conn)
        scorecard = {r["metric"]: _to_float(r["value"]) for r in score_rows}
        water_resource = await _fetchrow_optional(
            conn,
            f"""
            SELECT {_sql_columns("w", PUBLIC_WATER_RESOURCE_FIELDS)}
            FROM v_water_attribution_daily w
            WHERE w.date = (now() AT TIME ZONE 'America/Denver')::date
              AND w.greenhouse_id = $1
            """,
            greenhouse_id,
        )
        energy_resource = await _fetchrow_optional(
            conn,
            f"""
            SELECT {_sql_columns("e", PUBLIC_ENERGY_RESOURCE_FIELDS)}
            FROM v_energy_estimate_reconciliation e
            WHERE e.date = (now() AT TIME ZONE 'America/Denver')::date
              AND e.greenhouse_id = $1
            """,
            greenhouse_id,
        )
        last_plan = await conn.fetchrow(
            """
            SELECT plan_id,
                   created_at,
                   extract(epoch FROM now() - created_at)::int AS age_s,
                   outcome_score,
                   validated_at,
                   CASE
                     WHEN validated_at IS NOT NULL THEN 'validated'
                     WHEN actual_outcome IS NOT NULL OR outcome_score IS NOT NULL THEN 'evaluated'
                     ELSE 'awaiting outcome'
                   END AS status
            FROM plan_journal
            WHERE greenhouse_id = $1
              AND plan_id LIKE 'iris-%'
              AND plan_id NOT LIKE 'iris-reactive%'
              AND plan_id NOT LIKE 'iris-fix%'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            greenhouse_id,
        )
        last_validated_plan = await conn.fetchrow(
            """
            SELECT plan_id, outcome_score, validated_at
            FROM plan_journal
            WHERE greenhouse_id = $1
              AND validated_at IS NOT NULL
              AND plan_id LIKE 'iris-%'
              AND plan_id NOT LIKE 'iris-reactive%'
              AND plan_id NOT LIKE 'iris-fix%'
            ORDER BY validated_at DESC
            LIMIT 1
            """,
            greenhouse_id,
        )
        latest_lesson = await conn.fetchrow(
            """
            SELECT id, category, lesson, confidence, times_validated, last_validated
            FROM planner_lessons
            WHERE greenhouse_id = $1 AND is_active
            ORDER BY last_validated DESC NULLS LAST, created_at DESC
            LIMIT 1
            """,
            greenhouse_id,
        )
        latest_climate = await conn.fetchrow(
            """
            SELECT ts, extract(epoch FROM now() - ts)::int AS age_s
            FROM climate
            WHERE greenhouse_id = $1
            ORDER BY ts DESC
            LIMIT 1
            """,
            greenhouse_id,
        )
        climate_rows = await conn.fetchval(
            "SELECT count(*)::int FROM climate WHERE greenhouse_id = $1",
            greenhouse_id,
        )
        active_crops = await conn.fetchval(
            f"""
            SELECT count(DISTINCT crop_catalog_slug)::int
            FROM v_position_current
            WHERE greenhouse_id = $1
              AND is_occupied
              AND {_public_crop_sql_predicate("crop_catalog_slug", "crop_name", 2, 3)}
            """,
            greenhouse_id,
            *_public_crop_sql_parameters(),
        )
        plan_count = await conn.fetchval(
            """
            SELECT count(*)::int
            FROM plan_journal
            WHERE greenhouse_id = $1
              AND plan_id LIKE 'iris-%'
              AND plan_id NOT LIKE 'iris-reactive%'
              AND plan_id NOT LIKE 'iris-fix%'
            """,
            greenhouse_id,
        )
        lesson_count = await conn.fetchval(
            "SELECT count(*)::int FROM planner_lessons WHERE greenhouse_id = $1 AND is_active",
            greenhouse_id,
        )
        active_plan = await conn.fetchrow(
            """
            SELECT plan_id,
                   max(created_at) AS created_at,
                   extract(epoch FROM now() - max(created_at))::int AS age_s
            FROM setpoint_plan
            WHERE greenhouse_id = $1
              AND is_active
              AND plan_id IS NOT NULL
            GROUP BY plan_id
            ORDER BY max(created_at) DESC
            LIMIT 1
            """,
            greenhouse_id,
        )
        controller_mode = await conn.fetchval(
            "SELECT value FROM system_state WHERE entity = 'greenhouse_state' ORDER BY ts DESC LIMIT 1"
        )
        active_relays = await conn.fetch(
            """
            WITH latest AS (
              SELECT DISTINCT ON (equipment) equipment, state, ts
              FROM equipment_state
              WHERE greenhouse_id = $1
              ORDER BY equipment, ts DESC
            )
            SELECT equipment
            FROM latest
            WHERE state
              AND equipment IN (
                'heat1', 'heat2', 'fan1', 'fan2', 'fog', 'vent',
                'grow_light_main', 'grow_light_grow',
                'mister_south', 'mister_west', 'mister_center',
                'mister_south_fert', 'mister_west_fert',
                'drip_wall', 'drip_center',
                'drip_wall_fert', 'drip_center_fert',
                'fert_master_valve'
              )
            ORDER BY equipment
            """,
            greenhouse_id,
        )
        open_critical_high = await conn.fetchval(
            """
            SELECT count(*)::int
            FROM alert_log
            WHERE greenhouse_id = $1
              AND disposition = 'open'
              AND severity IN ('critical', 'high')
            """,
            greenhouse_id,
        )
        data_checks = await _fetch_optional(
            conn,
            """
            SELECT check_name, lower(status) AS status, metric_value, threshold_value, details
            FROM v_data_trust_ledger
            ORDER BY
              CASE lower(status) WHEN 'fail' THEN 0 WHEN 'warn' THEN 1 ELSE 2 END,
              check_name
            """,
        )
        planner_health = await conn.fetchrow(
            f"SELECT {_sql_columns('h', PUBLIC_PLANNER_HEALTH_FIELDS)} FROM v_planner_trigger_health h"
        )

    data_health_status = _overall_data_health(data_checks)
    active_plan_id = active_plan["plan_id"] if active_plan else (last_plan["plan_id"] if last_plan else None)
    active_plan_status = None
    if active_plan_id and last_plan and active_plan_id == last_plan["plan_id"]:
        active_plan_status = last_plan["status"]
    elif active_plan_id:
        active_plan_status = "active"
    active_relays_list = [r["equipment"] for r in active_relays]
    water_today_gal = (
        _to_float(water_resource["quality_filtered_meter_gal"])
        if water_resource and water_resource["available_for_scoring"]
        else None
    )
    planner_health_payload = _project_planner_health(planner_health) if planner_health else None
    response = {
        "generated_at": generated_at,
        "timezone": "America/Denver",
        "greenhouse_id": greenhouse_id,
        "data_health_status": data_health_status,
        "climate_age_seconds": latest_climate["age_s"] if latest_climate else None,
        "open_critical_high_alerts": open_critical_high or 0,
        "planner_score_today": scorecard.get("planner_score"),
        "planner_score_scope": (
            "climate_plus_resource"
            if scorecard.get("resource_terms_available") == 1.0
            else "climate_only_resource_excluded"
        ),
        "planner_score_resource_weight_pct": scorecard.get("planner_score_resource_weight_pct") or 0,
        "planner_score_resource_terms_available": scorecard.get("resource_terms_available") == 1.0,
        "both_axis_compliance_pct": scorecard.get("compliance_pct"),
        "graded_compliance_attributable_pct": scorecard.get("compliance_v2_attributable_pct"),
        "temp_compliance_pct": scorecard.get("temp_compliance_pct"),
        "vpd_compliance_pct": scorecard.get("vpd_compliance_pct"),
        "stress_axis_hours": scorecard.get("total_stress_h"),
        "active_plan_id": active_plan_id,
        "active_plan_status": active_plan_status,
        "last_plan_id": last_plan["plan_id"] if last_plan else None,
        "last_validated_plan_id": last_validated_plan["plan_id"] if last_validated_plan else None,
        "cost_today_usd": (
            scorecard.get("cost_total")
            if water_resource
            and water_resource["available_for_scoring"]
            and energy_resource
            and energy_resource["modeled_available_for_scoring"]
            else None
        ),
        "water_today_gal": water_today_gal,
        "resource_accounting": {
            "water": (_project_public_record(water_resource, PUBLIC_WATER_RESOURCE_FIELDS) if water_resource else None),
            "energy": _project_energy_resource(energy_resource) if energy_resource else None,
            "scalar_policy": (
                "water_today_gal is null unless ledger conservation/coverage is scoring-eligible; "
                "modeled and partial-measured energy remain separate"
            ),
        },
        "active_relays": active_relays_list,
        "active_control_crops": active_crops or 0,
        "public_plan_records": plan_count or 0,
        "climate_rows": climate_rows or 0,
        "lesson_rows_active": lesson_count or 0,
        "controller_mode": controller_mode,
        "planner_health": planner_health_payload,
        "planning_quality": {
            "planner_score_today": scorecard.get("planner_score"),
            "planner_score_scope": (
                "climate_plus_resource"
                if scorecard.get("resource_terms_available") == 1.0
                else "climate_only_resource_excluded"
            ),
            "planner_score_resource_weight_pct": scorecard.get("planner_score_resource_weight_pct") or 0,
            "resource_terms_available": scorecard.get("resource_terms_available") == 1.0,
            "both_axis_compliance_pct": scorecard.get("compliance_pct"),
            "graded_compliance_attributable_pct": scorecard.get("compliance_v2_attributable_pct"),
            "temp_compliance_pct": scorecard.get("temp_compliance_pct"),
            "vpd_compliance_pct": scorecard.get("vpd_compliance_pct"),
            "stress_axis_hours": scorecard.get("total_stress_h"),
            "stress_breakdown": {
                "heat_h": scorecard.get("heat_stress_h"),
                "cold_h": scorecard.get("cold_stress_h"),
                "vpd_high_h": scorecard.get("vpd_high_stress_h"),
                "vpd_low_h": scorecard.get("vpd_low_stress_h"),
            },
            "last_validated_plan": dict(last_validated_plan) if last_validated_plan else None,
            "last_plan": dict(last_plan) if last_plan else None,
            "latest_lesson": (
                {
                    **dict(latest_lesson),
                    "category": redact_non_public_crop_references(latest_lesson["category"]),
                    "lesson": redact_non_public_crop_references(latest_lesson["lesson"]),
                }
                if latest_lesson
                else None
            ),
        },
        "operations": {
            "data_health_status": data_health_status,
            "latest_climate_ts": latest_climate["ts"] if latest_climate else None,
            "latest_climate_age_s": latest_climate["age_s"] if latest_climate else None,
            "open_critical_high_alerts": open_critical_high or 0,
            "active_controller_mode": controller_mode,
            "active_relays": active_relays_list,
            "active_plan_id": active_plan_id,
            "active_plan_status": active_plan_status,
            "active_plan_age_s": active_plan["age_s"] if active_plan else None,
            "last_plan_age_s": last_plan["age_s"] if last_plan else None,
            "cost_today_usd": (
                scorecard.get("cost_total")
                if water_resource
                and water_resource["available_for_scoring"]
                and energy_resource
                and energy_resource["modeled_available_for_scoring"]
                else None
            ),
            "water_today_gal": water_today_gal,
            "mister_water_today_gal": (
                _to_float(water_resource["climate_wetting_gal"])
                if water_resource and water_resource["available_for_scoring"]
                else None
            ),
            "water_accounting_status": (water_resource["resource_quality"] if water_resource else "unavailable"),
            "water_accounting_incomplete": bool(not water_resource or not water_resource["available_for_scoring"]),
            "water_accounting_details": (
                "accepted meter gallons are conserved across attributed, ambiguous, "
                "and manual_or_unattributed; inspect resource_accounting.water for coverage"
            ),
        },
    }
    return redact_public_data(response)


# ═══════════════════════════════════════════════════════════════════════
# Sprint 23 — Topology + crop-history endpoints
# ═══════════════════════════════════════════════════════════════════════
#
# These endpoints consume the Sprint 22 topology tables and the Sprint 23
# history views (v_position_current, v_crop_history, v_crop_lifecycle).
# The legacy zone:str / position:str-based endpoints above remain in place
# until callers migrate; Phase 4d drops them.


# ── Topology tree (website nav, full-system debug) ────────────────────


@app.get("/api/v1/topology")
async def get_topology(greenhouse_id: str = DEFAULT_GREENHOUSE):
    """Full greenhouse → zone → shelf → position tree as JSONB."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT greenhouse_id, greenhouse_name, zones FROM v_topology_tree WHERE greenhouse_id = $1",
            greenhouse_id,
        )
        if row is None:
            raise HTTPException(404, "Greenhouse not found")
    record = dict(row)
    return redact_public_data(
        {
            "greenhouse_id": record.get("greenhouse_id"),
            "greenhouse_name": record.get("greenhouse_name"),
            "zones": _project_topology_zones(record.get("zones")),
        }
    )


# ── Zone full detail ──────────────────────────────────────────────────


@app.get("/api/v1/zones/{zone_slug}/full")
async def get_zone_full(zone_slug: str, greenhouse_id: str = DEFAULT_GREENHOUSE):
    """Zone detail: shelves[], sensors[], equipment[], water_systems[] (from v_zone_full)."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            SELECT {_sql_columns("z", PUBLIC_ZONE_FULL_FIELDS)}
            FROM v_zone_full z
            WHERE z.greenhouse_id = $1 AND z.zone_slug = $2
            """,
            greenhouse_id,
            zone_slug,
        )
        if row is None:
            raise HTTPException(404, "Zone not found")
        public_active_crops = await conn.fetchval(
            f"""
            SELECT count(*)::int
            FROM crops c
            JOIN crop_catalog cc ON cc.id = c.crop_catalog_id
            {public_crop_zone_joins()}
            WHERE c.greenhouse_id = $2
              AND c.is_active
              AND {_public_crop_zone_sql_predicate("$1", "cc.slug", "c.name", 3, 4)}
            """,
            row["zone_id"],
            greenhouse_id,
            *_public_crop_sql_parameters(),
        )
    result = _project_zone_full(row)
    result["active_crops_fk_count"] = public_active_crops or 0
    return redact_public_data(result)


# ── Positions (current state + history) ───────────────────────────────


@app.get("/api/v1/positions", response_model=list[PositionCurrentEntry])
async def list_positions(
    zone_slug: str | None = None,
    occupied_only: bool = False,
    greenhouse_id: str = DEFAULT_GREENHOUSE,
):
    """Every active position + current crop (if any). Empty slots included unless occupied_only=true."""
    sql = f"SELECT {_sql_columns('p', PUBLIC_POSITION_FIELDS)} FROM v_position_current p WHERE p.greenhouse_id = $1"
    params: list = [greenhouse_id]
    if zone_slug is not None:
        sql += " AND zone_slug = $2"
        params.append(zone_slug)
    if occupied_only:
        slug_parameter = len(params) + 1
        name_parameter = slug_parameter + 1
        sql += " AND is_occupied AND " + _public_crop_sql_predicate(
            "crop_catalog_slug",
            "crop_name",
            slug_parameter,
            name_parameter,
        )
        params.extend(_public_crop_sql_parameters())
    sql += " ORDER BY zone_slug, shelf_slug, position_label"
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)
    public_rows = [_sanitize_public_position(row) for row in rows]
    if occupied_only:
        public_rows = [row for row in public_rows if row["is_occupied"]]
    return [PositionCurrentEntry.model_validate(redact_public_data(row)) for row in public_rows]


@app.get("/api/v1/positions/{position_id}")
async def get_position(position_id: int, greenhouse_id: str = DEFAULT_GREENHOUSE):
    """Position detail: current occupancy + full crop history at this slot."""
    async with pool.acquire() as conn:
        current = await conn.fetchrow(
            f"SELECT {_sql_columns('p', PUBLIC_POSITION_FIELDS)} "
            "FROM v_position_current p WHERE p.position_id = $1 AND p.greenhouse_id = $2",
            position_id,
            greenhouse_id,
        )
        if current is None:
            raise HTTPException(404, "Position not found")
        history_rows = await conn.fetch(
            f"""
            SELECT {_sql_columns("h", PUBLIC_CROP_HISTORY_FIELDS)}
            FROM v_crop_history h
            WHERE h.position_id = $1
              AND h.greenhouse_id = $2
              AND {_public_crop_sql_predicate("crop_catalog_slug", "crop_name", 3, 4)}
            ORDER BY planted_date DESC
            """,
            position_id,
            greenhouse_id,
            *_public_crop_sql_parameters(),
        )
    public_current = _sanitize_public_position(current)
    public_history = _public_crop_history_rows(history_rows)
    return redact_public_data(
        {
            "current": public_current,
            "history": [CropHistoryEntry.model_validate(row).model_dump() for row in public_history],
        }
    )


@app.post("/api/v1/positions/{position_id}/plant", status_code=201)
async def plant_at_position(
    position_id: int,
    body: CropCreate,
    greenhouse_id: str = DEFAULT_GREENHOUSE,
    _write_access: None = Depends(require_write_access),
):
    """Create a new crop at a specific position. Validates slot is unoccupied.

    The unique-active-per-position partial index (migration 088) prevents
    double-booking; a collision raises a 409.
    """
    async with pool.acquire() as conn:
        pos = await conn.fetchrow(
            """
            SELECT p.id AS position_id, p.label, sh.zone_id, z.slug AS zone_slug
            FROM positions p JOIN shelves sh ON sh.id = p.shelf_id JOIN zones z ON z.id = sh.zone_id
            WHERE p.id = $1 AND p.greenhouse_id = $2
            """,
            position_id,
            greenhouse_id,
        )
        if pos is None:
            raise HTTPException(404, "Position not found")
        # Resolve crop_catalog_id via slug / name
        catalog_id = None
        if body.crop_catalog_slug:
            catalog_id = await conn.fetchval("SELECT id FROM crop_catalog WHERE slug = $1", body.crop_catalog_slug)
        if catalog_id is None:
            catalog_id = await conn.fetchval(
                "SELECT id FROM crop_catalog WHERE lower(common_name) = lower($1) OR slug = lower($1)",
                body.name,
            )
        try:
            row = await conn.fetchrow(
                """
                INSERT INTO crops (
                    name, variety, position, zone, planted_date, expected_harvest, stage,
                    count, seed_lot_id, supplier, base_temp_f, target_dli, target_vpd_low,
                    target_vpd_high, notes, greenhouse_id,
                    position_id, zone_id, crop_catalog_id
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16,
                        $17, $18, $19)
                RETURNING *
                """,
                body.name,
                body.variety,
                pos["label"],
                pos["zone_slug"],
                body.planted_date,
                body.expected_harvest,
                body.stage,
                body.count,
                body.seed_lot_id,
                body.supplier,
                body.base_temp_f,
                body.target_dli,
                body.target_vpd_low,
                body.target_vpd_high,
                body.notes,
                greenhouse_id,
                position_id,
                pos["zone_id"],
                catalog_id,
            )
        except asyncpg.exceptions.UniqueViolationError:
            raise HTTPException(409, "Position is already occupied by an active crop")
    return dict(row)


# ── Crop lifecycle (clear, transplant, harvest) + full timeline ───────


@app.get("/api/v1/crops/{crop_id}/lifecycle", response_model=CropLifecycle)
async def get_crop_lifecycle(crop_id: int, greenhouse_id: str = DEFAULT_GREENHOUSE):
    """Full crop timeline: events array, harvest totals, observations summary."""
    async with pool.acquire() as conn:
        await _require_public_crop(conn, crop_id, greenhouse_id)
        row = await conn.fetchrow(
            f"SELECT {_sql_columns('l', PUBLIC_CROP_LIFECYCLE_FIELDS)} "
            "FROM v_crop_lifecycle l WHERE l.crop_id = $1 AND l.greenhouse_id = $2",
            crop_id,
            greenhouse_id,
        )
        if row is None:
            raise HTTPException(404, "Crop not found")
    return CropLifecycle.model_validate(_project_crop_lifecycle(row))


@app.post("/api/v1/crops/{crop_id}/clear")
async def clear_crop(
    crop_id: int,
    operator: str | None = None,
    _write_access: None = Depends(require_write_access),
):
    """Mark a crop as inactive (cleared/removed). Trigger auto-sets cleared_at
    and logs a 'removed' crop_events row."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE crops SET is_active = FALSE WHERE id = $1 AND is_active RETURNING id, cleared_at",
            crop_id,
        )
        if row is None:
            raise HTTPException(404, "Crop not found or already cleared")
        if operator:
            await conn.execute(
                "UPDATE crop_events SET operator = $1 WHERE crop_id = $2 AND event_type = 'removed' AND operator IS NULL",
                operator,
                crop_id,
            )
    return {"crop_id": crop_id, "is_active": False, "cleared_at": row["cleared_at"]}


class TransplantBody(BaseModel):
    new_position_id: int
    operator: str | None = None
    notes: str | None = None


@app.post("/api/v1/crops/{crop_id}/transplant")
async def transplant_crop(
    crop_id: int,
    body: TransplantBody,
    greenhouse_id: str = DEFAULT_GREENHOUSE,
    _write_access: None = Depends(require_write_access),
):
    """Move a crop to a new position. Logs a 'transplanted' event with old/new position_ids."""
    async with pool.acquire() as conn:
        crop = await conn.fetchrow(
            "SELECT id, position_id, stage, greenhouse_id FROM crops WHERE id = $1 AND is_active",
            crop_id,
        )
        if crop is None:
            raise HTTPException(404, "Active crop not found")
        target = await conn.fetchrow(
            "SELECT p.id, p.label, sh.zone_id FROM positions p JOIN shelves sh ON sh.id = p.shelf_id WHERE p.id = $1 AND p.greenhouse_id = $2",
            body.new_position_id,
            greenhouse_id,
        )
        if target is None:
            raise HTTPException(404, "Target position not found")
        try:
            await conn.execute(
                "UPDATE crops SET position_id = $1, zone_id = $2, position = $3 WHERE id = $4",
                body.new_position_id,
                target["zone_id"],
                target["label"],
                crop_id,
            )
        except asyncpg.exceptions.UniqueViolationError:
            raise HTTPException(409, "Target position is occupied")
        await conn.execute(
            """
            INSERT INTO crop_events (ts, crop_id, event_type, source, operator, notes, greenhouse_id, position_id)
            VALUES (now(), $1, 'transplanted', 'api', $2, $3, $4, $5)
            """,
            crop_id,
            body.operator,
            body.notes or f"Transplanted to position {body.new_position_id}",
            crop["greenhouse_id"],
            body.new_position_id,
        )
    return {"crop_id": crop_id, "new_position_id": body.new_position_id, "new_position_label": target["label"]}


class HarvestBody(BaseModel):
    weight_kg: float | None = None
    unit_count: int | None = None
    quality_grade: str | None = None
    salable_weight_kg: float | None = None
    cull_weight_kg: float | None = None
    cull_reason: str | None = None
    quality_reason: str | None = None
    unit_price: float | None = None
    revenue: float | None = None
    destination: str | None = None
    labor_minutes: int | None = None
    advance_stage: str | None = None  # optional: also update crops.stage
    operator: str | None = None
    notes: str | None = None


@app.post("/api/v1/crops/{crop_id}/harvest", status_code=201)
async def harvest_crop(
    crop_id: int,
    body: HarvestBody,
    greenhouse_id: str = DEFAULT_GREENHOUSE,
    _write_access: None = Depends(require_write_access),
):
    """Record a harvest against this crop. Optionally advance stage."""
    async with pool.acquire() as conn:
        crop = await conn.fetchrow(
            "SELECT id, position_id, zone, stage FROM crops WHERE id = $1 AND greenhouse_id = $2",
            crop_id,
            greenhouse_id,
        )
        if crop is None:
            raise HTTPException(404, "Crop not found")
        row = await conn.fetchrow(
            """
            INSERT INTO harvests (
                ts, crop_id, weight_kg, unit_count, quality_grade,
                salable_weight_kg, cull_weight_kg, cull_reason, quality_reason,
                unit_price, revenue, destination, labor_minutes,
                zone, operator, notes, greenhouse_id, position_id
            )
            VALUES (now(), $1, $2, $3, $4, $5, $6, $7, $8, $9,
                    $10, $11, $12, $13, $14, $15, $16, $17)
            RETURNING *
            """,
            crop_id,
            body.weight_kg,
            body.unit_count,
            body.quality_grade,
            body.salable_weight_kg,
            body.cull_weight_kg,
            body.cull_reason,
            body.quality_reason,
            body.unit_price,
            body.revenue,
            body.destination,
            body.labor_minutes,
            crop["zone"],
            body.operator,
            body.notes,
            greenhouse_id,
            crop["position_id"],
        )
        if body.advance_stage and body.advance_stage != crop["stage"]:
            await conn.execute("UPDATE crops SET stage = $1 WHERE id = $2", body.advance_stage, crop_id)
    return dict(row)


# ── Crop catalog ──────────────────────────────────────────────────────


@app.get("/api/v1/crop-catalog")
async def list_crop_catalog():
    """All crop types in the catalog (with aggregated stage/season profiles)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT {_sql_columns("c", PUBLIC_CATALOG_FIELDS)}
            FROM v_crop_catalog_with_profiles c
            WHERE {_public_crop_sql_predicate("slug", "common_name", 1, 2)}
            ORDER BY slug
            """,
            *_public_crop_sql_parameters(),
        )
    public_rows = _public_crop_rows(
        rows,
        slug_key="slug",
        name_key="common_name",
        fields=PUBLIC_CATALOG_FIELDS,
    )
    return [_project_catalog_record(row) for row in public_rows]


@app.get("/api/v1/crop-catalog/{slug}")
async def get_crop_catalog_entry(slug: str):
    """Single catalog entry + hourly profile detail."""
    if not is_public_crop(slug):
        raise HTTPException(404, "Crop catalog entry not found")
    async with pool.acquire() as conn:
        entry = await conn.fetchrow(
            f"""
            SELECT {_sql_columns("c", PUBLIC_CATALOG_FIELDS)}
            FROM v_crop_catalog_with_profiles c
            WHERE slug = $1
              AND {_public_crop_sql_predicate("slug", "common_name", 2, 3)}
            """,
            slug,
            *_public_crop_sql_parameters(),
        )
        public_entries = (
            _public_crop_rows(
                [entry],
                slug_key="slug",
                name_key="common_name",
                fields=PUBLIC_CATALOG_FIELDS,
            )
            if entry is not None
            else []
        )
        if not public_entries:
            raise HTTPException(404, "Crop catalog entry not found")
        hours = await conn.fetch(
            f"""
            SELECT {_sql_columns("p", PUBLIC_CATALOG_HOURLY_FIELDS)}
            FROM crop_target_profiles p
            WHERE p.crop_catalog_id = (SELECT id FROM crop_catalog WHERE slug = $1)
            ORDER BY p.growth_stage, p.season, p.hour_of_day
            """,
            slug,
        )
    return redact_public_data(
        {
            "entry": _project_catalog_record(public_entries[0]),
            "hourly_profiles": [_project_public_record(h, PUBLIC_CATALOG_HOURLY_FIELDS) for h in hours],
        }
    )


# ── Equipment, switches, sensors (read-only for now) ──────────────────


@app.get("/api/v1/equipment")
async def list_equipment(zone_slug: str | None = None, greenhouse_id: str = DEFAULT_GREENHOUSE):
    sql = """
        SELECT e.id, e.greenhouse_id, e.slug, e.kind, e.name, e.model,
               e.watts, e.cost_per_hour_usd, e.is_active, z.slug AS zone_slug,
               CASE
                   WHEN jsonb_typeof(e.specs) = 'object' AND e.specs ? 'telemetry_slug'
                   THEN jsonb_build_object('telemetry_slug', e.specs -> 'telemetry_slug')
                   ELSE '{}'::jsonb
               END AS specs
        FROM equipment e LEFT JOIN zones z ON z.id = e.zone_id
        WHERE e.greenhouse_id = $1 AND e.is_active
    """
    params: list = [greenhouse_id]
    if zone_slug is not None:
        sql += " AND z.slug = $2"
        params.append(zone_slug)
    sql += " ORDER BY e.kind, e.slug"
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)
    return [_project_equipment(row) for row in rows]


@app.get("/api/v1/switches")
async def list_switches(greenhouse_id: str = DEFAULT_GREENHOUSE):
    """Full relay map — v_equipment_relay_map."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT {_sql_columns('s', PUBLIC_SWITCH_FIELDS)} "
            "FROM v_equipment_relay_map s WHERE s.greenhouse_id = $1 ORDER BY s.board, s.pin",
            greenhouse_id,
        )
    return [_project_public_record(row, PUBLIC_SWITCH_FIELDS) for row in rows]


@app.get("/api/v1/sensors")
async def list_sensors(zone_slug: str | None = None, greenhouse_id: str = DEFAULT_GREENHOUSE):
    sql = f"""
        SELECT {_sql_columns("s", tuple(field for field in PUBLIC_SENSOR_FIELDS if field != "zone_slug"))},
               z.slug AS zone_slug
        FROM sensors s LEFT JOIN zones z ON z.id = s.zone_id
        WHERE s.greenhouse_id = $1 AND s.is_active
    """
    params: list = [greenhouse_id]
    if zone_slug is not None:
        sql += " AND z.slug = $2"
        params.append(zone_slug)
    sql += " ORDER BY s.kind, s.slug"
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)
    return [_project_public_record(row, PUBLIC_SENSOR_FIELDS) for row in rows]


@app.get("/api/v1/pressure-groups/status")
async def pressure_group_status(greenhouse_id: str = DEFAULT_GREENHOUSE):
    """Current mister/drip activity per pressure group (v_pressure_group_status)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT {_sql_columns('p', PUBLIC_PRESSURE_GROUP_FIELDS)} "
            "FROM v_pressure_group_status p WHERE p.greenhouse_id = $1 ORDER BY p.group_slug",
            greenhouse_id,
        )
    return [_project_pressure_group(row) for row in rows]


# ── Controlled planner experiments (#587, audit §8.7) ─────────────────
#
# Lifecycle + blinded status/export API over the migration-207 SQL state
# machine (fn_experiment_transition et al., db/migrations/207-*.sql).
#
# AUTH (fail closed): two SEPARATE tokens, mirroring require_write_access
# (constant-time compare, 403 when unset — feature-off parity means every
# route below returns 403 until a token is deployed):
#   - VERDIFY_EXPERIMENT_API_TOKEN   header X-Verdify-Experiment-Token
#       lifecycle transitions + the BLINDED analyst status/export/unblind
#       surface. This surface NEVER returns proposal source, component
#       values, reusable content hashes, template ids, or the X/Y->A/B arm
#       resolution (until the one-way completed-state unblind transition).
#   - VERDIFY_EXPERIMENT_OPERATOR_TOKEN  header X-Verdify-Operator-Token
#       the separately-authorized OPERATOR/SAFETY surface (§8.7: "safety
#       operators are explicitly not considered blinded"): device-confirmed
#       effective-policy identity readback. Content/activation hashes are
#       intentionally visible here and only here.
#   VERDIFY_ALLOW_UNAUTHENTICATED_WRITES deliberately does NOT apply.
#
# CONCURRENCY / IDEMPOTENCY: the protocol-v1 runtime wrappers own row locks,
# optimistic expected-status checks, and typed event writes atomically. The
# API runtime has no direct DML on shared experiment relations. A transition
# to the status the row already has is an idempotent 200 no-op (no event row).
# All state rules (transition matrix, lock/arm gates, the LANE-C
# aa/randomized qualification-hash gates as they land) live in SQL and are
# surfaced as HTTP 404/409/422 with the SQL error detail — never
# re-implemented in Python.

EXPERIMENT_API_TOKEN_ENV = "VERDIFY_EXPERIMENT_API_TOKEN"
EXPERIMENT_OPERATOR_TOKEN_ENV = "VERDIFY_EXPERIMENT_OPERATOR_TOKEN"
_EXPERIMENT_KINDS = ("qualification", "aa", "randomized")
_EXPERIMENT_STATUSES = ("draft", "locked", "armed", "running", "paused", "completed", "aborted")
_EXPERIMENT_PRODUCERS = ("ai", "forecast", "baseline", "guardrail", "operator")
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
# action -> fn_experiment_transition target status. "validate" (savepoint
# dry-run of the lock gates) and "unblind" are handled separately.
# "rollback" is the SQL matrix's only backward edge: locked -> draft
# ("unlock is allowed ONLY before arming"); "resume" targets running and is
# legal from both armed (start) and paused (resume) per the SQL matrix.
EXPERIMENT_TRANSITION_TARGETS = {
    "lock": "locked",
    "arm": "armed",
    "resume": "running",
    "pause": "paused",
    "abort": "aborted",
    "complete": "completed",
    "rollback": "draft",
}


async def require_experiment_access(
    x_verdify_experiment_token: Annotated[str | None, Header(alias="X-Verdify-Experiment-Token")] = None,
) -> None:
    """Fail closed: blinded experiment lifecycle/analyst surface (#587)."""
    expected = os.environ.get(EXPERIMENT_API_TOKEN_ENV)
    if expected and x_verdify_experiment_token and hmac.compare_digest(expected, x_verdify_experiment_token):
        return
    raise HTTPException(status_code=403, detail="Experiment API disabled for unauthenticated request")


async def require_experiment_operator_access(
    x_verdify_operator_token: Annotated[str | None, Header(alias="X-Verdify-Operator-Token")] = None,
) -> None:
    """Fail closed: separately-authorized operator/safety surface (#587)."""
    expected = os.environ.get(EXPERIMENT_OPERATOR_TOKEN_ENV)
    if expected and x_verdify_operator_token and hmac.compare_digest(expected, x_verdify_operator_token):
        return
    raise HTTPException(status_code=403, detail="Experiment operator API disabled for unauthenticated request")


def _experiment_sql_http_error(exc: asyncpg.exceptions.PostgresError) -> HTTPException:
    """Map RAISE EXCEPTION detail from the migration-207 functions to HTTP.

    unknown id -> 404; illegal state-matrix edge or state conflict -> 409;
    every gate failure (lock/arm gates, LANE-C kind gates) -> 422. The SQL
    message is the response detail — the rules themselves stay in SQL.
    """
    message = exc.message or str(exc)
    if "unknown experiment" in message:
        return HTTPException(status_code=404, detail=message)
    if "ordinary runtime rejects protocol" in message:
        return HTTPException(
            status_code=409,
            detail="Protocol-v2 experiments are not available on the legacy v1 experiment surface",
        )
    if (
        exc.sqlstate == "40001"
        or "illegal experiment transition" in message
        or "unblind requires completed" in message
        or "unblind export does not match" in message
        or "already unblinded with a different export hash" in message
    ):
        return HTTPException(status_code=409, detail=message)
    return HTTPException(status_code=422, detail=message)


class ExperimentCreate(BaseModel):
    """Draft-creation payload with kind-specific validation (#587).

    experiment_id is an optional client-supplied idempotency key: replaying
    the same create returns the existing row (200) instead of a duplicate.
    """

    greenhouse_id: str = Field(min_length=1, max_length=64)
    kind: str
    name: str = Field(min_length=1, max_length=200)
    experiment_id: str | None = None
    timezone: str = Field(default="America/Denver", max_length=64)
    protocol_ref: str | None = Field(default=None, max_length=200)
    protocol_sha256: str | None = None
    permitted_producers: list[str] | None = None
    # Randomized-only commitment material (never the mapping secret itself).
    mutable_fields: list[str] | None = None
    beacon_identity: str | None = Field(default=None, max_length=200)
    beacon_hash: str | None = None
    mapping_commitment_sha256: str | None = None
    schedule_sha256: str | None = None

    @field_validator("kind")
    @classmethod
    def _kind_known(cls, v: str) -> str:
        if v not in _EXPERIMENT_KINDS:
            raise ValueError(f"kind must be one of {_EXPERIMENT_KINDS}")
        return v

    @field_validator("protocol_sha256", "beacon_hash", "mapping_commitment_sha256", "schedule_sha256")
    @classmethod
    def _sha256_hex(cls, v: str | None) -> str | None:
        if v is not None and not _SHA256_HEX_RE.match(v):
            raise ValueError("must be 64 lowercase hex chars (sha256)")
        return v

    @field_validator("permitted_producers")
    @classmethod
    def _producers_known(cls, v: list[str] | None) -> list[str] | None:
        if v is not None:
            unknown = sorted(set(v) - set(_EXPERIMENT_PRODUCERS))
            if unknown:
                raise ValueError(f"unknown producers {unknown}; allowed: {_EXPERIMENT_PRODUCERS}")
        return v

    @model_validator(mode="after")
    def _kind_specific_payload(self) -> "ExperimentCreate":
        randomized_only = {
            "beacon_identity": self.beacon_identity,
            "beacon_hash": self.beacon_hash,
            "mapping_commitment_sha256": self.mapping_commitment_sha256,
            "schedule_sha256": self.schedule_sha256,
            "mutable_fields": self.mutable_fields,
        }
        if self.kind != "randomized":
            offending = sorted(k for k, val in randomized_only.items() if val is not None)
            if offending:
                raise ValueError(f"{offending} are randomized-only fields (kind={self.kind})")
        elif self.mutable_fields is not None and len(self.mutable_fields) > 11:
            raise ValueError("mutable_fields is the 11-field AI allowlist — at most 11 entries")
        return self


class ExperimentSummary(BaseModel):
    """Explicit non-treatment-revealing experiment summary (never a raw row)."""

    model_config = ConfigDict(extra="forbid")

    experiment_id: str
    greenhouse_id: str
    kind: str
    status: str
    name: str
    timezone: str
    created_at: dt.datetime


class ExperimentTransitionRequest(BaseModel):
    expected_status: str | None = None
    # Retained for wire compatibility only. Audit identity is derived from the
    # authenticated route/action and never trusted from request JSON.
    actor: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("expected_status")
    @classmethod
    def _status_known(cls, v: str | None) -> str | None:
        if v is not None and v not in _EXPERIMENT_STATUSES:
            raise ValueError(f"expected_status must be one of {_EXPERIMENT_STATUSES}")
        return v


class ExperimentTransitionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_id: str
    action: str
    previous_status: str
    status: str
    idempotent: bool = False
    # validate-only: gates passed under a rolled-back savepoint.
    validated: bool | None = None


class ExperimentAssignmentBlinded(BaseModel):
    """Opaque current-assignment view: id + blinded label ONLY (#587)."""

    model_config = ConfigDict(extra="forbid")

    assignment_id: str
    # X/Y for randomized; None otherwise. NEVER a template kind or A/B arm.
    blinded_label: str | None = None
    operation_kind: str
    valid_from: dt.datetime
    valid_to: dt.datetime
    status: str

    @field_validator("blinded_label")
    @classmethod
    def _blinded_only(cls, v: str | None) -> str | None:
        if v is not None and v not in ("X", "Y"):
            raise ValueError("blinded_label may only surface the opaque X/Y labels")
        return v


class ExperimentMissingDataCounters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignments_without_exposure: int = 0
    unconfirmed_exposures: int = 0
    exposures_missing_coverage: int = 0


class ExperimentSafetyState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paused: bool = False
    fallback_closures: int = 0
    protocol_deviations: int = 0
    critical_events: int = 0
    failed_deliveries: int = 0


class ExperimentStatusBlinded(BaseModel):
    """BLINDED execution status (#587, audit §8.7).

    MUST NOT carry: proposal source/producer, component values, reusable
    content/activation hashes, template ids, or the X/Y->A/B resolution.
    extra="forbid" + explicit field-by-field construction (never **row).
    """

    model_config = ConfigDict(extra="forbid")

    experiment_id: str
    kind: str
    status: str
    current_assignment: ExperimentAssignmentBlinded | None = None
    exposure_coverage_pct: float | None = None
    open_exposures: int = 0
    confirmed_exposures: int = 0
    delivery_lag_seconds: float | None = None
    pending_deliveries: int = 0
    missing_data: ExperimentMissingDataCounters
    safety: ExperimentSafetyState


class ExperimentExportRow(BaseModel):
    """One blinded per-assignment outcome row (#587).

    Documented SELECT (no Lane B outcomes view yet): control_assignments
    LEFT JOIN policy_exposures aggregates only — no policy_proposals, no
    *_components, no content/activation hashes, no template ids, no
    control_arm_resolutions.
    """

    model_config = ConfigDict(extra="forbid")

    assignment_id: str
    arm_label: str
    operation_kind: str
    pair_index: int | None = None
    block_index: int | None = None
    valid_from: dt.datetime
    valid_to: dt.datetime
    assignment_status: str
    exposure_count: int = 0
    confirmed_exposure_count: int = 0
    exposure_coverage_pct: float | None = None
    fallback_closures: int = 0


class ExperimentArmResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    blinded_label: str
    physical_arm: str
    resolved_at: dt.datetime
    resolution_source: str


class ExperimentExport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_id: str
    kind: str
    status: str
    rows: list[ExperimentExportRow]
    # sha256 over the canonical JSON of rows — the frozen hash the unblind
    # transition must echo back.
    export_sha256: str
    unblinded: bool = False
    # Present ONLY after status=completed AND the recorded unblind transition.
    arm_resolutions: list[ExperimentArmResolution] | None = None


class ExperimentUnblindRequest(BaseModel):
    export_sha256: str
    # Retained for wire compatibility only; the SQL evidence actor is fixed.
    actor: str | None = Field(default=None, max_length=120)

    @field_validator("export_sha256")
    @classmethod
    def _sha256_hex(cls, v: str) -> str:
        if not _SHA256_HEX_RE.match(v):
            raise ValueError("must be 64 lowercase hex chars (sha256)")
        return v


class ExperimentDevicePolicyIdentity(BaseModel):
    """OPERATOR/SAFETY surface (§8.7): device-confirmed effective-policy
    identity from the latest policy_device_snapshots row. Deliberately NOT
    blinded-analyst safe (content/activation hashes are the point here);
    gated by the separate operator token, never the experiment token."""

    model_config = ConfigDict(extra="forbid")

    snapshot_id: int
    device_id: str
    greenhouse_id: str | None = None
    reported_at: dt.datetime
    schema_revision: str | None = None
    device_generation: int | None = None
    assignment_id: str | None = None
    content_sha256: str | None = None
    activation_sha256: str | None = None
    valid_from: dt.datetime | None = None
    valid_to: dt.datetime | None = None
    apply_state: str | None = None
    firmware_revision: str | None = None


class ComponentExperimentWorkStatus(BaseModel):
    """Generic, phase-typed current work without treatment disclosure."""

    model_config = ConfigDict(extra="forbid")

    # The SECURITY DEFINER status function masks a future randomized work UUID
    # until its valid range begins. Generic phase/kind/range remains visible.
    work_id: str | None
    execution_phase: str
    operation_kind: str
    valid_from: dt.datetime
    valid_to: dt.datetime
    expires_at: dt.datetime
    temporal_state: Literal["pending", "active", "expired"]
    expired: bool
    # Only randomized assignment work may expose assignment identity/X-Y.
    assignment_id: str | None = None
    blinded_label: str | None = None

    @model_validator(mode="after")
    def _assignment_identity_is_randomized_only(self) -> "ComponentExperimentWorkStatus":
        has_assignment_identity = self.assignment_id is not None or self.blinded_label is not None
        if has_assignment_identity and (
            self.operation_kind != "randomized_assignment" or self.temporal_state != "active"
        ):
            raise ValueError("assignment identity is randomized-work-only")
        if self.blinded_label is not None and self.blinded_label not in ("X", "Y"):
            raise ValueError("blinded_label may only be X or Y")
        if self.expired != (self.temporal_state == "expired"):
            raise ValueError("expired must agree with temporal_state")
        if self.work_id is None and not (
            self.operation_kind == "randomized_assignment"
            and self.temporal_state == "pending"
            and not has_assignment_identity
        ):
            raise ValueError("work_id may only be masked for pending randomized work")
        return self


class ComponentExperimentApprovals(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scoped_probe: bool = False
    combined_physical: bool = False
    randomized_day_1: bool = False


class ComponentExperimentStateIdentity(BaseModel):
    """Two separate server-derived identities; neither is a device echo."""

    model_config = ConfigDict(extra="forbid")

    work_id: str | None = None
    policy_state_content_sha256: str | None = None
    observation_receipt_sha256: str | None = None
    receipt_persisted_at: dt.datetime | None = None
    identity_source: str = "server_derived"
    device_echoed: bool = False


class ComponentExperimentStatus(BaseModel):
    """Operator safety/integrity surface for confirmed-component v2."""

    model_config = ConfigDict(extra="forbid")

    experiment_id: str
    kind: str
    protocol_version: int
    transport_kind: str
    lifecycle_status: str
    execution_phase: str
    admission_state: str
    component_capability_mode: str
    environment_admissible: bool
    environment_gate_reason: str
    db_component_enabled: bool
    current_work: ComponentExperimentWorkStatus | None = None
    approvals: ComponentExperimentApprovals
    state_identity: ComponentExperimentStateIdentity
    open_exposures: int = 0
    lease_generation: int
    revision_bundle_sha256: str
    firmware_revision: str
    config_revision: str
    registry_revision: str
    grid_revision: str


_COMPONENT_EXPERIMENT_LIFECYCLE_STATUSES = (
    "draft",
    "locked",
    "armed",
    "running",
    "paused",
    "completed",
    "aborted",
)
_COMPONENT_EXPERIMENT_PHASES = ("shadow", "commissioning", "aa_rehearsal", "randomized")
_COMPONENT_EXPERIMENT_ADMISSIONS = ("closed", "open", "baseline_recovery", "emergency_hold")
_COMPONENT_EXPERIMENT_SCHEDULE_SCHEMA_SHA256 = "fc73d212f58db91bd55bb70e3faa1431172b4339ae3b22a11d404ba95147b794"
_COMPONENT_EXPERIMENT_DIRECT_PAIR_COUNT = 30
_COMPONENT_EXPERIMENT_DIRECT_POWER_SHA256 = "4d751a76465d03dc2e75034dcb398d25dc39b375d9976671bd8fffb018d237a2"
_COMPONENT_EXPERIMENT_DIRECT_PROFILE_SHA256 = "c185909cfd2a097c7dc3c7b820f4ebc4609b1261a555b7af8ed6294669ee1ea1"
_COMPONENT_EXPERIMENT_AUDIT_REF_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/#@-]{0,95}$"


class _ComponentExperimentControlBase(BaseModel):
    """Authenticated lifecycle command with an immutable external audit reference."""

    model_config = ConfigDict(extra="forbid")

    audit_ref: str = Field(pattern=_COMPONENT_EXPERIMENT_AUDIT_REF_PATTERN)

    @property
    def database_actor(self) -> str:
        # The fixed prefix makes it impossible to mistake a caller-supplied
        # trace reference for a database login or human identity.
        return f"verdify-api:{self.audit_ref}"


class _ComponentExperimentExistingControl(_ComponentExperimentControlBase):
    """Required optimistic precondition for every already-configured v2 command."""

    expected_lifecycle_status: Literal["draft", "locked", "armed", "running", "paused", "completed", "aborted"]
    expected_execution_phase: Literal["shadow", "commissioning", "aa_rehearsal", "randomized"]
    expected_admission_state: Literal["closed", "open", "baseline_recovery", "emergency_hold"]
    expected_component_enabled: bool = Field(strict=True)
    expected_lease_generation: int = Field(ge=0, strict=True)
    expected_revision_bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ComponentExperimentConfigureControl(_ComponentExperimentControlBase):
    action: Literal["configure"]
    # Candidate configuration converts a protocol-1 draft or replaces an
    # unlocked protocol-2 candidate. It deliberately carries no pre-draw
    # design, schedule, selector, endpoint, outcome, or power artifact.
    expected_protocol_version: Literal[1, 2]
    expected_lifecycle_status: Literal["draft"]
    expected_execution_phase: Literal[None, "shadow", "commissioning", "aa_rehearsal"]
    expected_admission_state: Literal["closed"]
    expected_component_enabled: bool = Field(strict=True)
    expected_lease_generation: int = Field(ge=0, strict=True)
    expected_revision_bundle_sha256: str | None = Field(pattern=r"^[0-9a-f]{64}$")
    firmware_revision: str = Field(min_length=1, max_length=200)
    config_revision: str = Field(min_length=1, max_length=200)
    registry_revision: str = Field(min_length=1, max_length=200)
    grid_revision: str = Field(min_length=1, max_length=200)
    study_id: str = Field(min_length=1, max_length=200)
    assignment_namespace_uuid: uuid.UUID

    @model_validator(mode="after")
    def _source_or_candidate_precondition_is_exact(self) -> "ComponentExperimentConfigureControl":
        if self.expected_protocol_version == 1:
            if (
                self.expected_execution_phase is not None
                or self.expected_component_enabled
                or self.expected_lease_generation != 0
                or self.expected_revision_bundle_sha256 is not None
            ):
                raise ValueError("initial configure requires the protocol-1 source precondition")
        elif self.expected_execution_phase is None or self.expected_revision_bundle_sha256 is None:
            raise ValueError("replacement configure requires the observed protocol-2 candidate revision")
        return self


class ComponentExperimentLockDesignControl(_ComponentExperimentExistingControl):
    """Atomic pre-draw lock after revision-bound shadow/canary/A-A evidence."""

    action: Literal["lock_design"]
    study_start_local_date: date
    randomized_pair_count: int = Field(ge=2, le=10_000, strict=True)
    selector_context_cutoff_local: dt.time
    design_lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_git_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    schedule_schema_sha256: Literal["fc73d212f58db91bd55bb70e3faa1431172b4339ae3b22a11d404ba95147b794"]
    selector_identity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    selector_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    context_schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    endpoint_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    outcome_schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    analyzer_environment_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    power_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("selector_context_cutoff_local")
    @classmethod
    def _selector_cutoff_is_wall_clock_time(cls, value: dt.time) -> dt.time:
        if value.tzinfo is not None:
            raise ValueError("selector_context_cutoff_local must not include a UTC offset")
        return value


class ComponentExperimentDirectLaunchLockControl(ComponentExperimentLockDesignControl):
    """Atomic Jason-authorized direct lock after one supervised physical proof."""

    action: Literal["direct_launch_lock"]
    authorization_ref: str = Field(min_length=1, max_length=500)
    qualification_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    profile_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    baseline_before_evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    aggressive_evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    baseline_after_evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    proof_valid_from: dt.datetime
    proof_valid_to: dt.datetime
    supervisor_role: str = Field(min_length=1, max_length=200)
    rescue_owner_role: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def _direct_launch_proof_window_is_bounded(self) -> "ComponentExperimentDirectLaunchLockControl":
        if (
            self.randomized_pair_count != _COMPONENT_EXPERIMENT_DIRECT_PAIR_COUNT
            or self.power_artifact_sha256 != _COMPONENT_EXPERIMENT_DIRECT_POWER_SHA256
            or self.profile_artifact_sha256 != _COMPONENT_EXPERIMENT_DIRECT_PROFILE_SHA256
        ):
            raise ValueError("direct launch requires the exact accepted-risk 30-pair power/profile lock")
        if any(
            value.tzinfo is None or value.utcoffset() is None for value in (self.proof_valid_from, self.proof_valid_to)
        ):
            raise ValueError("direct-launch proof timestamps must include a UTC offset")
        if self.proof_valid_from >= self.proof_valid_to:
            raise ValueError("direct-launch proof window must have positive duration")
        return self


class ComponentExperimentDirectLaunchApproveDay1Control(_ComponentExperimentExistingControl):
    """Derive day-1 authorization from the immutable direct-launch waiver."""

    action: Literal["direct_launch_approve_day1"]


class ComponentExperimentDirectProofBeginControl(_ComponentExperimentExistingControl):
    """Open the exact attended baseline/aggressive/baseline proof authorization."""

    action: Literal["direct_proof_begin"]
    authorization_ref: str = Field(min_length=1, max_length=500)
    proof_valid_from: dt.datetime
    proof_valid_to: dt.datetime
    supervisor_role: Literal["Jason Vallery"]
    rescue_owner_role: Literal["Jason Vallery"]

    @model_validator(mode="after")
    def _attended_window_is_exact(self) -> "ComponentExperimentDirectProofBeginControl":
        if any(
            value.tzinfo is None or value.utcoffset() is None for value in (self.proof_valid_from, self.proof_valid_to)
        ):
            raise ValueError("direct-proof timestamps must include a UTC offset")
        duration = self.proof_valid_to - self.proof_valid_from
        if duration < dt.timedelta(minutes=3) or duration > dt.timedelta(hours=12):
            raise ValueError("direct-proof window must be between 3 minutes and 12 hours")
        return self


class ComponentExperimentDirectProofWorkControl(_ComponentExperimentExistingControl):
    aggressive_work_id: uuid.UUID


class ComponentExperimentDirectProofOpenAggressiveControl(ComponentExperimentDirectProofWorkControl):
    action: Literal["direct_proof_open_aggressive"]


class ComponentExperimentDirectProofBeginBaselineAfterControl(ComponentExperimentDirectProofWorkControl):
    action: Literal["direct_proof_begin_baseline_after"]


class ComponentExperimentDirectProofFinishControl(_ComponentExperimentExistingControl):
    action: Literal["direct_proof_finish"]


class ComponentExperimentDirectLaunchCommitControl(ComponentExperimentLockDesignControl):
    """Consume only the database-sealed attended proof to lock the design."""

    action: Literal["direct_launch_commit"]

    @model_validator(mode="after")
    def _direct_launch_design_is_exact(self) -> "ComponentExperimentDirectLaunchCommitControl":
        if (
            self.randomized_pair_count != _COMPONENT_EXPERIMENT_DIRECT_PAIR_COUNT
            or self.power_artifact_sha256 != _COMPONENT_EXPERIMENT_DIRECT_POWER_SHA256
        ):
            raise ValueError("direct launch requires the exact accepted-risk 30-pair power lock")
        return self


class ComponentExperimentRegisterStateControl(_ComponentExperimentExistingControl):
    action: Literal["register_state"]
    profile: Literal["baseline", "moderate", "aggressive", "commissioning_probe"]
    wire_schema_version: int = Field(ge=0, le=255)
    wire_manifest_digest_hex: str = Field(pattern=r"^[0-9a-f]{64}$")
    wire_vector_hex: str = Field(pattern=r"^[0-9a-f]{356}$")


class ComponentExperimentRecordApprovalControl(_ComponentExperimentExistingControl):
    action: Literal["record_approval"]
    approval_kind: Literal["scoped_probe", "combined_physical", "randomized_day_1"]
    scope_name: Literal["commissioning_probe", "combined", "day1"]
    issue_number: Literal[641, 642]
    approval_ref: str = Field(min_length=1, max_length=500)
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    valid_from: dt.datetime | None = None
    valid_to: dt.datetime | None = None
    expires_at: dt.datetime | None = None
    supervisor_role: str | None = Field(default=None, min_length=1, max_length=200)
    rescue_owner_role: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def _approval_scope_is_exact(self) -> "ComponentExperimentRecordApprovalControl":
        if self.approval_kind == "scoped_probe":
            if self.issue_number != 641 or self.scope_name != "commissioning_probe":
                raise ValueError("scoped_probe must bind issue 641 commissioning_probe")
            if None in (
                self.valid_from,
                self.valid_to,
                self.expires_at,
                self.supervisor_role,
                self.rescue_owner_role,
            ):
                raise ValueError("scoped_probe requires its bounded window and facility roles")
            _validate_component_control_interval(self.valid_from, self.valid_to, self.expires_at)
        else:
            expected = (641, "combined") if self.approval_kind == "combined_physical" else (642, "day1")
            if (self.issue_number, self.scope_name) != expected:
                raise ValueError(f"{self.approval_kind} has the wrong issue/scope")
            if any(
                value is not None
                for value in (
                    self.valid_from,
                    self.valid_to,
                    self.expires_at,
                    self.supervisor_role,
                    self.rescue_owner_role,
                )
            ):
                raise ValueError(f"{self.approval_kind} cannot carry a probe window or facility roles")
        return self


class ComponentExperimentTransitionControl(_ComponentExperimentExistingControl):
    action: Literal["transition"]
    target_lifecycle_status: Literal["running", "paused", "aborted"] | None = None
    target_execution_phase: Literal["commissioning", "aa_rehearsal", "randomized"] | None = None
    note: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _one_axis_only(self) -> "ComponentExperimentTransitionControl":
        if (self.target_lifecycle_status is None) == (self.target_execution_phase is None):
            raise ValueError("transition must change exactly one lifecycle/phase axis")
        return self


class ComponentExperimentSetAdmissionControl(_ComponentExperimentExistingControl):
    action: Literal["set_admission"]
    target_admission_state: Literal["closed", "open", "baseline_recovery", "emergency_hold"]
    reason: str = Field(min_length=1, max_length=1000)


class ComponentExperimentFacilitySafeClosureControl(_ComponentExperimentExistingControl):
    action: Literal["record_facility_safe_closure"]
    authorization_ref: str = Field(min_length=1, max_length=500)
    safe_state_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class _ComponentExperimentBoundedRangeControl(_ComponentExperimentExistingControl):
    valid_from: dt.datetime
    valid_to: dt.datetime
    expires_at: dt.datetime

    @model_validator(mode="after")
    def _bounded_aware_range(self) -> "_ComponentExperimentBoundedRangeControl":
        _validate_component_control_interval(self.valid_from, self.valid_to, self.expires_at)
        return self


class ComponentExperimentCreateWorkControl(_ComponentExperimentBoundedRangeControl):
    action: Literal["create_work"]
    operation_kind: Literal["shadow_preview", "commissioning_probe", "commissioning_canary", "aa_baseline_rehearsal"]
    target_profile: Literal["baseline", "moderate", "aggressive", "commissioning_probe"]

    @model_validator(mode="after")
    def _work_kind_matches_profile(self) -> "ComponentExperimentCreateWorkControl":
        allowed = {
            "shadow_preview": {"baseline"},
            "commissioning_probe": {"commissioning_probe"},
            "commissioning_canary": {"moderate", "aggressive"},
            "aa_baseline_rehearsal": {"baseline"},
        }
        if self.target_profile not in allowed[self.operation_kind]:
            raise ValueError("operation_kind cannot use that target_profile")
        return self


class ComponentExperimentRequestRecoveryControl(_ComponentExperimentBoundedRangeControl):
    action: Literal["request_recovery"]
    source_work_id: uuid.UUID | None = None
    reason: str = Field(min_length=1, max_length=1000)


class ComponentExperimentCompleteControl(_ComponentExperimentExistingControl):
    action: Literal["complete"]
    note: str | None = Field(default=None, max_length=1000)


ComponentExperimentControlRequest = Annotated[
    ComponentExperimentConfigureControl
    | ComponentExperimentLockDesignControl
    | ComponentExperimentDirectLaunchLockControl
    | ComponentExperimentDirectLaunchApproveDay1Control
    | ComponentExperimentDirectProofBeginControl
    | ComponentExperimentDirectProofOpenAggressiveControl
    | ComponentExperimentDirectProofBeginBaselineAfterControl
    | ComponentExperimentDirectProofFinishControl
    | ComponentExperimentDirectLaunchCommitControl
    | ComponentExperimentRegisterStateControl
    | ComponentExperimentRecordApprovalControl
    | ComponentExperimentTransitionControl
    | ComponentExperimentSetAdmissionControl
    | ComponentExperimentFacilitySafeClosureControl
    | ComponentExperimentCreateWorkControl
    | ComponentExperimentRequestRecoveryControl
    | ComponentExperimentCompleteControl,
    Field(discriminator="action"),
]


class ComponentExperimentControlState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lifecycle_status: str
    execution_phase: str
    admission_state: str
    component_enabled: bool
    lease_generation: int
    revision_bundle_sha256: str


class ComponentExperimentControlReceipt(BaseModel):
    """Treatment-free receipt for one function-bounded lifecycle command."""

    model_config = ConfigDict(extra="forbid")

    experiment_id: str
    action: Literal[
        "configure",
        "lock_design",
        "direct_launch_lock",
        "direct_launch_approve_day1",
        "direct_proof_begin",
        "direct_proof_open_aggressive",
        "direct_proof_begin_baseline_after",
        "direct_proof_finish",
        "direct_launch_commit",
        "register_state",
        "record_approval",
        "transition",
        "set_admission",
        "record_facility_safe_closure",
        "create_work",
        "request_recovery",
        "complete",
    ]
    result_id: str
    previous_state: ComponentExperimentControlState | None
    state: ComponentExperimentControlState
    recorded_at: dt.datetime


def _validate_component_control_interval(
    valid_from: dt.datetime | None,
    valid_to: dt.datetime | None,
    expires_at: dt.datetime | None,
) -> None:
    if valid_from is None or valid_to is None or expires_at is None:
        raise ValueError("bounded interval is incomplete")
    if any(value.tzinfo is None or value.utcoffset() is None for value in (valid_from, valid_to, expires_at)):
        raise ValueError("bounded interval timestamps must include a UTC offset")
    if valid_from >= valid_to or not (valid_from < expires_at <= valid_to):
        raise ValueError("bounded interval must satisfy valid_from < expires_at <= valid_to")


_EXPERIMENT_V2_API_STATUS_SQL = """
SELECT experiment_id, protocol_version, experiment_kind, transport_kind,
       lifecycle_status, execution_phase, admission_state, component_enabled,
       lease_generation, revision_bundle_sha256, firmware_revision,
       config_revision, registry_revision, grid_revision, design_lock_sha256,
       schedule_sha256, mapping_commitment_sha256, scoped_probe_approved,
       combined_physical_approved, randomized_day_1_approved, work_id,
       assignment_id, work_operation_kind, work_execution_phase,
       work_valid_range, work_expires_at, future_randomized_identity_masked,
       current_work_receipt_ids, current_work_policy_state_content_sha256,
       current_work_receipt_sha256, current_work_receipt_persisted_at,
       open_exposure_count, resolved_at
  FROM public.fn_experiment_v2_api_status($1::uuid)
"""

_EXPERIMENT_V2_CONTROL_SQL: dict[str, str] = {
    "configure": """
SELECT (public.fn_experiment_v2_configure(
    $1::uuid, 'legacy_components_v1'::text,
    $2::text, $3::text, $4::text, $5::text, $6::text, $7::uuid,
    $8::text, $9::bigint, $10::text
)).experiment_id::text
""",
    "lock_design": """
SELECT (public.fn_experiment_v2_lock_design(
    $1::uuid, $2::date, $3::integer, $4::time without time zone,
    $5::text, $6::text, $7::text, $8::text, $9::text, $10::text,
    $11::text, $12::text, $13::text, $14::text, $15::text
)).experiment_id::text
""",
    "direct_launch_lock": """
SELECT (public.fn_experiment_v2_direct_launch_lock(
    $1::uuid, $2::date, $3::integer, $4::time without time zone,
    $5::text, $6::text, $7::text, $8::text, $9::text, $10::text,
    $11::text, $12::text, $13::text, $14::text, $15::text, $16::text,
    $17::text, $18::text, $19::text, $20::text, $21::tstzrange,
    $22::text, $23::text, $24::text
)).experiment_id::text
""",
    "direct_launch_approve_day1": """
SELECT (public.fn_experiment_v2_direct_launch_approve_day1(
    $1::uuid, $2::text
)).approval_id::text
""",
    "direct_proof_begin": """
SELECT public.fn_experiment_v2_direct_proof_begin(
    $1::uuid, $2::text, $3::tstzrange, $4::text, $5::text, $6::text
)::text
""",
    "direct_proof_open_aggressive": """
SELECT (public.fn_experiment_v2_direct_proof_open_aggressive(
    $1::uuid, $2::uuid, $3::text
)).experiment_id::text
""",
    "direct_proof_begin_baseline_after": """
SELECT public.fn_experiment_v2_direct_proof_begin_baseline_after(
    $1::uuid, $2::uuid, $3::text
)::text
""",
    "direct_proof_finish": """
SELECT (public.fn_experiment_v2_direct_proof_finish(
    $1::uuid, $2::text
)).proof_receipt_id::text
""",
    "direct_launch_commit": """
SELECT (public.fn_experiment_v2_direct_launch_commit(
    $1::uuid, $2::date, $3::integer, $4::time without time zone,
    $5::text, $6::text, $7::text, $8::text, $9::text, $10::text,
    $11::text, $12::text, $13::text, $14::text, $15::text
)).experiment_id::text
""",
    "register_state": """
SELECT (public.fn_experiment_v2_register_state(
    $1::uuid, $2::text, $3::smallint, $4::bytea, $5::bytea, $6::text
)).state_artifact_id::text
""",
    "record_approval": """
SELECT (public.fn_experiment_v2_record_approval(
    $1::uuid, $2::text, $3::text, $4::integer, $5::text, $6::text,
    $7::tstzrange, $8::timestamptz, $9::text, $10::text, $11::text
)).approval_id::text
""",
    "transition": """
SELECT (public.fn_experiment_v2_transition(
    $1::uuid, $2::text, $3::text, $4::text, $5::text
)).experiment_id::text
""",
    "set_admission": """
SELECT (public.fn_experiment_v2_set_admission(
    $1::uuid, $2::text, $3::text, $4::text
)).experiment_id::text
""",
    "record_facility_safe_closure": """
SELECT (public.fn_experiment_v2_record_facility_safe_closure(
    $1::uuid, $2::text, $3::text, $4::text
)).experiment_id::text
""",
    "create_work": """
SELECT public.fn_experiment_v2_create_work(
    $1::uuid, $2::text, $3::text, $4::tstzrange, $5::timestamptz, $6::text
)::text
""",
    "request_recovery": """
SELECT public.fn_experiment_v2_request_recovery(
    $1::uuid, $2::uuid, $3::tstzrange, $4::timestamptz, $5::text, $6::text
)::text
""",
    "complete": """
SELECT (public.fn_experiment_v2_complete(
    $1::uuid, $2::text, $3::text
)).experiment_id::text
""",
}


def _component_work_from_api_status(row: Mapping[str, object]) -> ComponentExperimentWorkStatus | None:
    work_fields = (
        row["work_id"],
        row["assignment_id"],
        row["work_operation_kind"],
        row["work_execution_phase"],
        row["work_valid_range"],
        row["work_expires_at"],
    )
    future_masked = row["future_randomized_identity_masked"]
    if future_masked is not True and future_masked is not False:
        raise ValueError("status function returned a malformed future-identity mask")
    if all(value is None for value in work_fields):
        if future_masked:
            raise ValueError("status function returned a mask without generic work metadata")
        return None
    if any(value is None for value in work_fields[2:]):
        raise ValueError("status function returned incomplete generic work metadata")

    valid_range = row["work_valid_range"]
    valid_from = getattr(valid_range, "lower", None)
    range_end = getattr(valid_range, "upper", None)
    expires_at = row["work_expires_at"]
    resolved_at = row["resolved_at"]
    if valid_from is None or range_end is None or expires_at is None or resolved_at is None:
        raise ValueError("status function returned an unbounded work interval")
    valid_to = min(range_end, expires_at)
    if resolved_at < valid_from:
        temporal_state: Literal["pending", "active", "expired"] = "pending"
    elif resolved_at < valid_to:
        temporal_state = "active"
    else:
        raise ValueError("status function returned expired work")

    operation_kind = row["work_operation_kind"]
    if temporal_state == "pending" and operation_kind == "randomized_assignment" and not future_masked:
        raise ValueError("status function returned an unmasked future randomized identity")
    if future_masked and not (
        temporal_state == "pending"
        and operation_kind == "randomized_assignment"
        and row["work_id"] is None
        and row["assignment_id"] is None
    ):
        raise ValueError("status function returned an inconsistent future randomized mask")
    if not future_masked and row["work_id"] is None:
        raise ValueError("status function returned unmasked work without its identifier")

    return ComponentExperimentWorkStatus(
        work_id=str(row["work_id"]) if row["work_id"] is not None else None,
        execution_phase=row["work_execution_phase"],
        operation_kind=operation_kind,
        valid_from=valid_from,
        valid_to=valid_to,
        expires_at=expires_at,
        temporal_state=temporal_state,
        expired=False,
        assignment_id=str(row["assignment_id"]) if row["assignment_id"] is not None else None,
        # Migration 214 deliberately does not return the X/Y label from this
        # least-information function. Never recover it through a base-table
        # query on the ordinary API pool.
        blinded_label=None,
    )


def _component_state_identity_from_api_status(
    row: Mapping[str, object],
    current_work: ComponentExperimentWorkStatus | None,
) -> ComponentExperimentStateIdentity:
    arrays = (
        row["current_work_receipt_ids"],
        row["current_work_policy_state_content_sha256"],
        row["current_work_receipt_sha256"],
        row["current_work_receipt_persisted_at"],
    )
    if any(not isinstance(values, (list, tuple)) for values in arrays):
        raise ValueError("status function returned malformed receipt arrays")
    lengths = {len(values) for values in arrays}
    if len(lengths) != 1:
        raise ValueError("status function returned misaligned receipt arrays")
    if not arrays[0]:
        return ComponentExperimentStateIdentity()
    if current_work is None or current_work.work_id is None:
        raise ValueError("status function returned receipts without visible current work")
    policy_hash = arrays[1][-1]
    receipt_hash = arrays[2][-1]
    persisted_at = arrays[3][-1]
    if policy_hash is None or receipt_hash is None or persisted_at is None:
        raise ValueError("status function returned an incomplete receipt identity")
    return ComponentExperimentStateIdentity(
        work_id=current_work.work_id,
        policy_state_content_sha256=policy_hash,
        observation_receipt_sha256=receipt_hash,
        receipt_persisted_at=persisted_at,
    )


def _validate_component_status_row(
    status: Mapping[str, object],
    normalized_experiment_id: str,
) -> tuple[ComponentExperimentWorkStatus | None, ComponentExperimentStateIdentity]:
    if str(status["experiment_id"]) != normalized_experiment_id:
        raise ValueError("status function returned the wrong experiment identity")
    if (
        status["protocol_version"] != 2
        or status["experiment_kind"] != "randomized"
        or status["transport_kind"] != "legacy_components_v1"
    ):
        raise ValueError("status function returned a non-component experiment")
    if status["lifecycle_status"] not in _COMPONENT_EXPERIMENT_LIFECYCLE_STATUSES:
        raise ValueError("status function returned an unknown lifecycle status")
    if status["execution_phase"] not in _COMPONENT_EXPERIMENT_PHASES:
        raise ValueError("status function returned an unknown execution phase")
    if status["admission_state"] not in _COMPONENT_EXPERIMENT_ADMISSIONS:
        raise ValueError("status function returned an unknown admission state")
    if status["component_enabled"] is not True and status["component_enabled"] is not False:
        raise ValueError("status function returned malformed component_enabled")
    if not isinstance(status["revision_bundle_sha256"], str) or not _SHA256_HEX_RE.fullmatch(
        status["revision_bundle_sha256"]
    ):
        raise ValueError("status function returned malformed revision_bundle_sha256")
    for approval_field in (
        "scoped_probe_approved",
        "combined_physical_approved",
        "randomized_day_1_approved",
    ):
        if status[approval_field] is not True and status[approval_field] is not False:
            raise ValueError("status function returned a malformed approval")
    if type(status["open_exposure_count"]) is not int or status["open_exposure_count"] < 0:
        raise ValueError("status function returned a malformed exposure count")
    if type(status["lease_generation"]) is not int or status["lease_generation"] < 0:
        raise ValueError("status function returned a malformed lease generation")
    current_work = _component_work_from_api_status(status)
    state_identity = _component_state_identity_from_api_status(status, current_work)
    return current_work, state_identity


def _component_control_state(status: Mapping[str, object]) -> ComponentExperimentControlState:
    return ComponentExperimentControlState(
        lifecycle_status=status["lifecycle_status"],
        execution_phase=status["execution_phase"],
        admission_state=status["admission_state"],
        component_enabled=status["component_enabled"],
        lease_generation=status["lease_generation"],
        revision_bundle_sha256=status["revision_bundle_sha256"],
    )


def _assert_component_control_precondition(
    status: Mapping[str, object],
    command: _ComponentExperimentExistingControl | ComponentExperimentConfigureControl,
) -> None:
    checks = {
        "lifecycle_status": (status["lifecycle_status"], command.expected_lifecycle_status),
        "execution_phase": (status["execution_phase"], command.expected_execution_phase),
        "admission_state": (status["admission_state"], command.expected_admission_state),
        "component_enabled": (status["component_enabled"], command.expected_component_enabled),
        "lease_generation": (status["lease_generation"], command.expected_lease_generation),
        "revision_bundle_sha256": (
            status["revision_bundle_sha256"],
            command.expected_revision_bundle_sha256,
        ),
    }
    mismatches = sorted(name for name, (actual, expected) in checks.items() if actual != expected)
    if mismatches:
        raise HTTPException(
            409,
            "Confirmed-component v2 precondition failed for " + ", ".join(mismatches),
        )


async def _execute_component_control(
    conn,
    experiment_id: str,
    command: ComponentExperimentControlRequest,
) -> str:
    actor = command.database_actor
    if isinstance(command, ComponentExperimentConfigureControl):
        args = (
            experiment_id,
            command.firmware_revision,
            command.config_revision,
            command.registry_revision,
            command.grid_revision,
            command.study_id,
            command.assignment_namespace_uuid,
            command.expected_revision_bundle_sha256,
            command.expected_lease_generation,
            actor,
        )
    elif isinstance(command, ComponentExperimentLockDesignControl):
        if isinstance(command, ComponentExperimentDirectLaunchLockControl):
            args = (
                experiment_id,
                command.study_start_local_date,
                command.randomized_pair_count,
                command.selector_context_cutoff_local,
                command.design_lock_sha256,
                command.source_git_sha,
                command.schedule_schema_sha256,
                command.selector_identity_sha256,
                command.selector_artifact_sha256,
                command.context_schema_sha256,
                command.endpoint_artifact_sha256,
                command.outcome_schema_sha256,
                command.analyzer_environment_sha256,
                command.power_artifact_sha256,
                command.authorization_ref,
                command.qualification_artifact_sha256,
                command.profile_artifact_sha256,
                command.baseline_before_evidence_sha256,
                command.aggressive_evidence_sha256,
                command.baseline_after_evidence_sha256,
                asyncpg.Range(
                    command.proof_valid_from,
                    command.proof_valid_to,
                    lower_inc=True,
                    upper_inc=False,
                ),
                command.supervisor_role,
                command.rescue_owner_role,
                actor,
            )
        elif isinstance(command, ComponentExperimentDirectLaunchCommitControl):
            args = (
                experiment_id,
                command.study_start_local_date,
                command.randomized_pair_count,
                command.selector_context_cutoff_local,
                command.design_lock_sha256,
                command.source_git_sha,
                command.schedule_schema_sha256,
                command.selector_identity_sha256,
                command.selector_artifact_sha256,
                command.context_schema_sha256,
                command.endpoint_artifact_sha256,
                command.outcome_schema_sha256,
                command.analyzer_environment_sha256,
                command.power_artifact_sha256,
                actor,
            )
        else:
            args = (
                experiment_id,
                command.study_start_local_date,
                command.randomized_pair_count,
                command.selector_context_cutoff_local,
                command.design_lock_sha256,
                command.source_git_sha,
                command.schedule_schema_sha256,
                command.selector_identity_sha256,
                command.selector_artifact_sha256,
                command.context_schema_sha256,
                command.endpoint_artifact_sha256,
                command.outcome_schema_sha256,
                command.analyzer_environment_sha256,
                command.power_artifact_sha256,
                actor,
            )
    elif isinstance(command, ComponentExperimentDirectLaunchApproveDay1Control):
        args = (experiment_id, actor)
    elif isinstance(command, ComponentExperimentDirectProofBeginControl):
        args = (
            experiment_id,
            command.authorization_ref,
            asyncpg.Range(
                command.proof_valid_from,
                command.proof_valid_to,
                lower_inc=True,
                upper_inc=False,
            ),
            command.supervisor_role,
            command.rescue_owner_role,
            actor,
        )
    elif isinstance(
        command,
        (
            ComponentExperimentDirectProofOpenAggressiveControl,
            ComponentExperimentDirectProofBeginBaselineAfterControl,
        ),
    ):
        args = (experiment_id, command.aggressive_work_id, actor)
    elif isinstance(command, ComponentExperimentDirectProofFinishControl):
        args = (experiment_id, actor)
    elif isinstance(command, ComponentExperimentRegisterStateControl):
        args = (
            experiment_id,
            command.profile,
            command.wire_schema_version,
            bytes.fromhex(command.wire_manifest_digest_hex),
            bytes.fromhex(command.wire_vector_hex),
            actor,
        )
    elif isinstance(command, ComponentExperimentRecordApprovalControl):
        valid_range = (
            asyncpg.Range(command.valid_from, command.valid_to, lower_inc=True, upper_inc=False)
            if command.valid_from is not None and command.valid_to is not None
            else None
        )
        args = (
            experiment_id,
            command.approval_kind,
            command.scope_name,
            command.issue_number,
            command.approval_ref,
            command.artifact_sha256,
            valid_range,
            command.expires_at,
            command.supervisor_role,
            command.rescue_owner_role,
            actor,
        )
    elif isinstance(command, ComponentExperimentTransitionControl):
        args = (
            experiment_id,
            command.target_lifecycle_status,
            command.target_execution_phase,
            actor,
            command.note,
        )
    elif isinstance(command, ComponentExperimentSetAdmissionControl):
        args = (experiment_id, command.target_admission_state, actor, command.reason)
    elif isinstance(command, ComponentExperimentFacilitySafeClosureControl):
        args = (
            experiment_id,
            command.authorization_ref,
            command.safe_state_artifact_sha256,
            actor,
        )
    elif isinstance(command, ComponentExperimentCreateWorkControl):
        args = (
            experiment_id,
            command.operation_kind,
            command.target_profile,
            asyncpg.Range(command.valid_from, command.valid_to, lower_inc=True, upper_inc=False),
            command.expires_at,
            actor,
        )
    elif isinstance(command, ComponentExperimentRequestRecoveryControl):
        args = (
            experiment_id,
            command.source_work_id,
            asyncpg.Range(command.valid_from, command.valid_to, lower_inc=True, upper_inc=False),
            command.expires_at,
            command.reason,
            actor,
        )
    elif isinstance(command, ComponentExperimentCompleteControl):
        args = (experiment_id, actor, command.note)
    else:  # pragma: no cover - the discriminated union is exhaustive.
        raise ValueError("unsupported confirmed-component v2 control action")

    result = await conn.fetchval(_EXPERIMENT_V2_CONTROL_SQL[command.action], *args)
    if result is None:
        raise ValueError("lifecycle function returned no durable identifier")
    result_id = str(result)
    uuid.UUID(result_id)
    if (
        command.action
        in {
            "configure",
            "lock_design",
            "direct_launch_lock",
            "direct_launch_commit",
            "direct_proof_open_aggressive",
            "transition",
            "set_admission",
            "record_facility_safe_closure",
            "complete",
        }
        and result_id != experiment_id
    ):
        raise ValueError("lifecycle function returned the wrong experiment identity")
    return result_id


def _component_control_sql_http_error(exc: asyncpg.exceptions.RaiseError) -> HTTPException:
    message = (exc.message or str(exc) or "Confirmed-component v2 control function rejected the command")[:1000]
    if "unknown protocol-v2 experiment" in message or "existing draft randomized experiment" in message:
        return HTTPException(404, message)
    if (
        "illegal" in message
        or "already configured" in message
        or "immutable" in message
        or "expected binding is stale" in message
        or "superseded candidate revision" in message
    ):
        return HTTPException(409, message)
    return HTTPException(422, message)


@app.post(
    "/experiments/{experiment_id}/component-control/commands",
    response_model=ComponentExperimentControlReceipt,
)
@app.post(
    "/api/v1/experiments/{experiment_id}/component-control/commands",
    response_model=ComponentExperimentControlReceipt,
)
async def control_component_experiment(
    experiment_id: str,
    command: ComponentExperimentControlRequest,
    _access: None = Depends(require_experiment_access),
):
    """Function-bounded, treatment-free v2 lifecycle command surface.

    Every ordinary configured-v2 command requires all six caller-observed state axes.
    The status read, comparison, exact allowlisted SECURITY DEFINER call, and
    treatment-free receipt read share one serializable transaction. A racing
    lifecycle change therefore aborts instead of applying against stale state.
    Initial configuration is different because its protocol-1 source row is not
    visible to the v2 status function. Candidate replacement instead carries
    the observed revision and lease into the database function, which can
    recognize an exact lost-response replay before rejecting stale changed
    content.
    """
    try:
        normalized_experiment_id = str(uuid.UUID(experiment_id))
    except (TypeError, ValueError):
        raise HTTPException(404, "Experiment not found")

    dedicated_pool = experiment_lifecycle_pool
    if dedicated_pool is None or getattr(dedicated_pool, "lifecycle_role_attested", False) is not True:
        raise HTTPException(503, "Confirmed-component v2 lifecycle database duty unavailable")

    try:
        async with dedicated_pool.acquire() as conn:
            async with conn.transaction(isolation="serializable"):
                before = await conn.fetchrow(_EXPERIMENT_V2_API_STATUS_SQL, normalized_experiment_id)
                previous_state: ComponentExperimentControlState | None = None
                if isinstance(command, ComponentExperimentConfigureControl):
                    if before is None:
                        if command.expected_protocol_version != 1:
                            raise HTTPException(409, "Confirmed-component v2 candidate is not configured")
                    else:
                        _validate_component_status_row(before, normalized_experiment_id)
                        if command.expected_protocol_version != 2:
                            raise HTTPException(409, "Confirmed-component v2 experiment is already configured")
                        # Configure's SQL function performs the exact
                        # candidate-content replay check before stale expected
                        # revision/lease checks, preserving lost-response
                        # idempotency without allowing a stale changed write.
                        if (
                            command.expected_revision_bundle_sha256 == before["revision_bundle_sha256"]
                            and command.expected_lease_generation == before["lease_generation"]
                        ):
                            _assert_component_control_precondition(before, command)
                        previous_state = _component_control_state(before)
                elif isinstance(command, ComponentExperimentLockDesignControl):
                    if before is None:
                        raise HTTPException(404, "Confirmed-component v2 experiment not found")
                    _validate_component_status_row(before, normalized_experiment_id)
                    # An exact post-lock retry must reach the SQL function's
                    # full artifact-tuple replay check. Any changed tuple is a
                    # conflict there and cannot mutate the already-locked row.
                    if before["lifecycle_status"] != "locked":
                        _assert_component_control_precondition(before, command)
                    previous_state = _component_control_state(before)
                else:
                    if before is None:
                        raise HTTPException(404, "Confirmed-component v2 experiment not found")
                    _validate_component_status_row(before, normalized_experiment_id)
                    _assert_component_control_precondition(before, command)
                    previous_state = _component_control_state(before)

                result_id = await _execute_component_control(conn, normalized_experiment_id, command)
                after = await conn.fetchrow(_EXPERIMENT_V2_API_STATUS_SQL, normalized_experiment_id)
                if after is None:
                    raise ValueError("lifecycle command left no protocol-v2 status row")
                _validate_component_status_row(after, normalized_experiment_id)
                receipt = ComponentExperimentControlReceipt(
                    experiment_id=normalized_experiment_id,
                    action=command.action,
                    result_id=result_id,
                    previous_state=previous_state,
                    state=_component_control_state(after),
                    recorded_at=after["resolved_at"],
                )
        return receipt
    except HTTPException:
        raise
    except asyncpg.exceptions.SerializationError as exc:
        raise HTTPException(
            409,
            "Confirmed-component v2 state changed concurrently; refresh status and retry",
        ) from exc
    except asyncpg.exceptions.RaiseError as exc:
        raise _component_control_sql_http_error(exc) from exc
    except (asyncpg.exceptions.UndefinedFunctionError, asyncpg.exceptions.InsufficientPrivilegeError) as exc:
        log.error("experiment lifecycle control function unavailable error=%s", type(exc).__name__)
        raise HTTPException(503, "Confirmed-component v2 lifecycle database duty unavailable") from exc
    except asyncpg.PostgresError as exc:
        log.error("experiment lifecycle control database conflict error=%s", type(exc).__name__)
        raise HTTPException(409, "Confirmed-component v2 command conflicted with durable state") from exc
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        log.error("experiment lifecycle control returned an invalid contract error=%s", type(exc).__name__)
        raise HTTPException(503, "Confirmed-component v2 lifecycle database duty unavailable") from exc


async def _fetch_experiment_row(conn, experiment_id: str, *, for_update: bool = False) -> asyncpg.Record:
    try:
        uuid.UUID(experiment_id)
    except (ValueError, TypeError):
        raise HTTPException(404, "Experiment not found")
    row = await conn.fetchrow(
        "SELECT experiment_id, greenhouse_id, kind, status, name, timezone, created_at, protocol_version "
        "FROM control_experiments WHERE experiment_id = $1" + (" FOR UPDATE" if for_update else ""),
        experiment_id,
    )
    if row is None:
        raise HTTPException(404, "Experiment not found")
    return row


def _require_legacy_v1_experiment_surface(exp: Mapping[str, object]) -> None:
    """Keep the generic experiment API from bypassing protocol-v2 duties.

    Protocol v2 has separately attested status and lifecycle functions, and
    its export/reveal flow is frozen by a different evidence contract. A
    generic handler must therefore stop immediately after identifying the
    row, before it reads legacy evidence or invokes a legacy mutator.
    """
    if exp["protocol_version"] != 1:
        raise HTTPException(
            409,
            "Protocol-v2 experiments are not available on the legacy v1 experiment surface",
        )


def _experiment_export_canonical_json(rows: list[ExperimentExportRow]) -> str:
    return json.dumps(
        [r.model_dump(mode="json") for r in rows],
        sort_keys=True,
        separators=(",", ":"),
    )


def _experiment_export_hash(rows: list[ExperimentExportRow]) -> str:
    canonical = _experiment_export_canonical_json(rows)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _experiment_create_replay_matches(conn, body: ExperimentCreate, experiment_id: str) -> bool:
    existing = await conn.fetchrow(
        """
        SELECT greenhouse_id, kind, name, timezone, protocol_ref,
               protocol_sha256, beacon_identity, beacon_hash,
               mapping_commitment_sha256, schedule_sha256, mutable_fields,
               permitted_producers, protocol_version
        FROM control_experiments
        WHERE experiment_id = $1
        """,
        experiment_id,
    )
    if existing is None or existing["protocol_version"] != 1:
        return False
    expected_producers = (
        body.permitted_producers
        if body.permitted_producers is not None
        else ["ai", "forecast", "baseline", "guardrail", "operator"]
    )
    return (
        existing["greenhouse_id"],
        existing["kind"],
        existing["name"],
        existing["timezone"],
        existing["protocol_ref"],
        existing["protocol_sha256"],
        existing["beacon_identity"],
        existing["beacon_hash"],
        existing["mapping_commitment_sha256"],
        existing["schedule_sha256"],
        list(existing["mutable_fields"]) if existing["mutable_fields"] is not None else None,
        list(existing["permitted_producers"]),
    ) == (
        body.greenhouse_id,
        body.kind,
        body.name,
        body.timezone,
        body.protocol_ref,
        body.protocol_sha256,
        body.beacon_identity,
        body.beacon_hash,
        body.mapping_commitment_sha256,
        body.schedule_sha256,
        body.mutable_fields,
        expected_producers,
    )


async def _fetch_experiment_export_rows(conn, experiment_id: str, kind: str) -> list[ExperimentExportRow]:
    # BLINDED outcome export (documented SELECT until the frozen
    # v_control_experiment_daily_outcomes view lands): assignment identity
    # (opaque uuid + blinded label), exposure coverage/confirmation
    # aggregates only.
    records = await conn.fetch(
        """
        SELECT a.assignment_id::text AS assignment_id,
               a.arm_label,
               a.operation_kind,
               a.pair_index,
               a.block_index,
               lower(a.valid_range) AS valid_from,
               upper(a.valid_range) AS valid_to,
               a.status AS assignment_status,
               count(e.exposure_id)::int AS exposure_count,
               (count(e.exposure_id) FILTER (WHERE e.identity_confirmed))::int AS confirmed_exposure_count,
               avg(e.coverage_fraction) AS exposure_coverage,
               (count(e.exposure_id) FILTER (
                   WHERE e.close_reason IN ('fallback', 'protocol_deviation')))::int AS fallback_closures
        FROM control_assignments a
        LEFT JOIN policy_exposures e ON e.assignment_id = a.assignment_id
        WHERE a.experiment_id = $1
        GROUP BY a.assignment_id, a.arm_label, a.operation_kind, a.pair_index,
                 a.block_index, a.valid_range, a.status
        ORDER BY lower(a.valid_range), a.assignment_id::text
        """,
        experiment_id,
    )
    rows: list[ExperimentExportRow] = []
    for r in records:
        # Defense in depth: a randomized study may only ever export the
        # opaque X/Y labels (also table-enforced for randomized_day rows).
        if kind == "randomized" and r["arm_label"] not in ("X", "Y"):
            continue
        coverage = r["exposure_coverage"]
        rows.append(
            ExperimentExportRow(
                assignment_id=r["assignment_id"],
                arm_label=r["arm_label"],
                operation_kind=r["operation_kind"],
                pair_index=r["pair_index"],
                block_index=r["block_index"],
                valid_from=r["valid_from"],
                valid_to=r["valid_to"],
                assignment_status=r["assignment_status"],
                exposure_count=r["exposure_count"],
                confirmed_exposure_count=r["confirmed_exposure_count"],
                exposure_coverage_pct=round(float(coverage) * 100.0, 3) if coverage is not None else None,
                fallback_closures=r["fallback_closures"],
            )
        )
    return rows


async def _experiment_unblind_recorded(conn, experiment_id: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM experiment_events "
            "WHERE experiment_id = $1 AND event_kind = 'state_transition' "
            "AND detail->>'to' = 'unblinded')",
            experiment_id,
        )
    )


@app.post("/experiments", status_code=201, response_model=ExperimentSummary)
@app.post("/api/v1/experiments", status_code=201, response_model=ExperimentSummary)
async def create_experiment(
    body: ExperimentCreate,
    response: Response,
    _access: None = Depends(require_experiment_access),
):
    """Create a draft experiment (idempotent via client experiment_id)."""
    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                """
                SELECT experiment_id::text, greenhouse_id, kind, status, name,
                       timezone, created_at, inserted
                FROM fn_runtime_v1_create_experiment(
                    $1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
                    $12::text[], $13::text[])
                """,
                body.experiment_id,
                body.greenhouse_id,
                body.kind,
                body.name,
                body.timezone,
                body.protocol_ref,
                body.protocol_sha256,
                body.beacon_identity,
                body.beacon_hash,
                body.mapping_commitment_sha256,
                body.schedule_sha256,
                body.mutable_fields,
                body.permitted_producers,
            )
        except asyncpg.exceptions.ForeignKeyViolationError:
            raise HTTPException(422, f"Unknown greenhouse {body.greenhouse_id!r}")
        except asyncpg.exceptions.DataError:
            raise HTTPException(422, "experiment_id must be a UUID")
        except asyncpg.exceptions.PostgresError as exc:
            raise _experiment_sql_http_error(exc)
        if row is None:
            raise HTTPException(422, "Experiment creation returned no row")
        if not row["inserted"]:
            # Idempotency-key replay: every caller-controlled field must match.
            if not await _experiment_create_replay_matches(conn, body, str(row["experiment_id"])):
                raise HTTPException(409, "experiment_id already exists with different content")
            response.status_code = 200
    return ExperimentSummary(
        experiment_id=str(row["experiment_id"]),
        greenhouse_id=row["greenhouse_id"],
        kind=row["kind"],
        status=row["status"],
        name=row["name"],
        timezone=row["timezone"],
        created_at=row["created_at"],
    )


# NOTE: registered BEFORE the generic /{action} transition route so the
# literal /unblind path wins starlette's in-order route matching.
@app.post("/experiments/{experiment_id}/unblind")
@app.post("/api/v1/experiments/{experiment_id}/unblind")
async def unblind_experiment(
    experiment_id: str,
    body: ExperimentUnblindRequest,
    _access: None = Depends(require_experiment_access),
):
    """One-way completed-state unblind (§8.7).

    Requires status=completed and the frozen export hash (the export_sha256
    the caller froze from GET .../export). Records the transition to the
    append-only experiment_events ledger; only then does the X/Y -> A/B arm
    resolution become readable through GET .../export.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            exp = await _fetch_experiment_row(conn, experiment_id)
            _require_legacy_v1_experiment_surface(exp)
            if exp["status"] != "completed":
                raise HTTPException(
                    409,
                    f"Unblind requires status 'completed' (experiment is {exp['status']!r})",
                )
            rows = await _fetch_experiment_export_rows(conn, experiment_id, exp["kind"])
            canonical_json = _experiment_export_canonical_json(rows)
            frozen = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
            if not hmac.compare_digest(frozen, body.export_sha256):
                raise HTTPException(
                    409,
                    "export_sha256 does not match the frozen blinded export — re-freeze via GET .../export",
                )
            try:
                inserted = await conn.fetchval(
                    "SELECT fn_runtime_v1_record_unblind($1::uuid, $2, $3, $4)",
                    experiment_id,
                    "api:experiment-unblind",
                    frozen,
                    canonical_json,
                )
            except asyncpg.exceptions.PostgresError as exc:
                raise _experiment_sql_http_error(exc)
    return {
        "experiment_id": experiment_id,
        "unblinded": True,
        "export_sha256": frozen,
        "idempotent": not inserted,
    }


@app.post("/experiments/{experiment_id}/{action}", response_model=ExperimentTransitionResponse)
@app.post("/api/v1/experiments/{experiment_id}/{action}", response_model=ExperimentTransitionResponse)
async def transition_experiment(
    experiment_id: str,
    action: str,
    body: ExperimentTransitionRequest | None = None,
    _access: None = Depends(require_experiment_access),
):
    """Thin wrapper over fn_runtime_v1_experiment_transition.

    validate: savepoint dry-run of the SQL lock gates (rolled back).
    lock/arm/resume/pause/abort/complete: forward edges of the SQL matrix.
    rollback: the matrix's only backward edge, locked -> draft (unlock).
    """
    body = body or ExperimentTransitionRequest()
    if action != "validate" and action not in EXPERIMENT_TRANSITION_TARGETS:
        raise HTTPException(404, f"Unknown experiment action {action!r}")
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await _fetch_experiment_row(conn, experiment_id)
            _require_legacy_v1_experiment_surface(row)

            if action == "validate":
                try:
                    async with conn.transaction():  # savepoint — always rolled back
                        validated = await conn.fetchrow(
                            """
                            SELECT previous_status, status, changed
                            FROM fn_runtime_v1_experiment_transition(
                                $1::uuid, 'locked', $2, $3, $4)
                            """,
                            experiment_id,
                            body.expected_status,
                            # This is a rolled-back rehearsal of the lock edge;
                            # use the same immutable actor as a real lock.
                            "api:experiment-lock",
                            body.note,
                        )
                        raise _ExperimentValidateRollback()
                except _ExperimentValidateRollback:
                    pass
                except asyncpg.exceptions.PostgresError as exc:
                    raise _experiment_sql_http_error(exc)
                return ExperimentTransitionResponse(
                    experiment_id=experiment_id,
                    action=action,
                    previous_status=validated["previous_status"],
                    status=validated["previous_status"],
                    idempotent=not validated["changed"],
                    validated=True,
                )

            target = EXPERIMENT_TRANSITION_TARGETS[action]
            try:
                updated = await conn.fetchrow(
                    """
                    SELECT previous_status, status, changed
                    FROM fn_runtime_v1_experiment_transition(
                        $1::uuid, $2, $3, $4, $5)
                    """,
                    experiment_id,
                    target,
                    body.expected_status,
                    f"api:experiment-{action}",
                    body.note,
                )
            except asyncpg.exceptions.PostgresError as exc:
                raise _experiment_sql_http_error(exc)
    return ExperimentTransitionResponse(
        experiment_id=experiment_id,
        action=action,
        previous_status=updated["previous_status"],
        status=updated["status"],
        idempotent=not updated["changed"],
    )


class _ExperimentValidateRollback(Exception):
    """Sentinel: roll the validate dry-run savepoint back on success."""


@app.get("/experiments/{experiment_id}/status", response_model=ExperimentStatusBlinded)
@app.get("/api/v1/experiments/{experiment_id}/status", response_model=ExperimentStatusBlinded)
async def get_experiment_status(
    experiment_id: str,
    _access: None = Depends(require_experiment_access),
):
    """BLINDED execution status — see ExperimentStatusBlinded docstring."""
    async with pool.acquire() as conn:
        exp = await _fetch_experiment_row(conn, experiment_id)
        _require_legacy_v1_experiment_surface(exp)
        assignment = await conn.fetchrow(
            """
            SELECT assignment_id::text AS assignment_id, arm_label, operation_kind,
                   lower(valid_range) AS valid_from, upper(valid_range) AS valid_to, status
            FROM control_assignments
            WHERE experiment_id = $1 AND status = 'active'
            ORDER BY (valid_range @> now()) DESC, lower(valid_range) DESC
            LIMIT 1
            """,
            experiment_id,
        )
        exposure = await conn.fetchrow(
            """
            SELECT count(*)::int AS exposure_count,
                   (count(*) FILTER (WHERE identity_confirmed))::int AS confirmed,
                   (count(*) FILTER (WHERE ended_at IS NULL))::int AS open_count,
                   (count(*) FILTER (WHERE NOT identity_confirmed))::int AS unconfirmed,
                   (count(*) FILTER (WHERE ended_at IS NOT NULL AND coverage_fraction IS NULL))::int
                       AS missing_coverage,
                   (count(*) FILTER (WHERE close_reason IN ('fallback', 'protocol_deviation')))::int
                       AS fallback_closures,
                   avg(coverage_fraction) AS coverage
            FROM policy_exposures
            WHERE experiment_id = $1
            """,
            experiment_id,
        )
        outbox = await conn.fetchrow(
            """
            SELECT (count(*) FILTER (WHERE o.state NOT IN ('activated', 'abandoned')))::int AS pending,
                   (count(*) FILTER (WHERE o.state = 'failed'))::int AS failed,
                   max(extract(epoch FROM now() - o.created_at))
                       FILTER (WHERE o.state NOT IN ('activated', 'abandoned')) AS lag_seconds
            FROM policy_delivery_outbox o
            JOIN effective_policy_vectors v ON v.vector_id = o.vector_id
            WHERE v.experiment_id = $1
            """,
            experiment_id,
        )
        no_exposure = await conn.fetchval(
            """
            SELECT count(*)::int
            FROM control_assignments a
            WHERE a.experiment_id = $1
              AND NOT EXISTS (SELECT 1 FROM policy_exposures e WHERE e.assignment_id = a.assignment_id)
            """,
            experiment_id,
        )
        events = await conn.fetchrow(
            """
            SELECT (count(*) FILTER (WHERE event_kind = 'protocol_deviation'))::int AS deviations,
                   (count(*) FILTER (WHERE severity = 'critical'))::int AS critical
            FROM experiment_events
            WHERE experiment_id = $1
            """,
            experiment_id,
        )

    current_assignment = None
    if assignment is not None:
        label = assignment["arm_label"]
        current_assignment = ExperimentAssignmentBlinded(
            assignment_id=assignment["assignment_id"],
            # Only randomized X/Y labels are opaque; every other label
            # (qualification template kinds, aa lanes) is suppressed.
            blinded_label=label if (exp["kind"] == "randomized" and label in ("X", "Y")) else None,
            operation_kind=assignment["operation_kind"],
            valid_from=assignment["valid_from"],
            valid_to=assignment["valid_to"],
            status=assignment["status"],
        )
    coverage = exposure["coverage"] if exposure else None
    lag = outbox["lag_seconds"] if outbox else None
    return ExperimentStatusBlinded(
        experiment_id=str(exp["experiment_id"]),
        kind=exp["kind"],
        status=exp["status"],
        current_assignment=current_assignment,
        exposure_coverage_pct=round(float(coverage) * 100.0, 3) if coverage is not None else None,
        open_exposures=exposure["open_count"] if exposure else 0,
        confirmed_exposures=exposure["confirmed"] if exposure else 0,
        delivery_lag_seconds=float(lag) if lag is not None else None,
        pending_deliveries=outbox["pending"] if outbox else 0,
        missing_data=ExperimentMissingDataCounters(
            assignments_without_exposure=no_exposure or 0,
            unconfirmed_exposures=exposure["unconfirmed"] if exposure else 0,
            exposures_missing_coverage=exposure["missing_coverage"] if exposure else 0,
        ),
        safety=ExperimentSafetyState(
            paused=exp["status"] == "paused",
            fallback_closures=exposure["fallback_closures"] if exposure else 0,
            protocol_deviations=events["deviations"] if events else 0,
            critical_events=events["critical"] if events else 0,
            failed_deliveries=outbox["failed"] if outbox else 0,
        ),
    )


@app.get("/experiments/{experiment_id}/export", response_model=ExperimentExport)
@app.get("/api/v1/experiments/{experiment_id}/export", response_model=ExperimentExport)
async def export_experiment(
    experiment_id: str,
    _access: None = Depends(require_experiment_access),
):
    """Blinded outcome export; arm resolution ONLY after completed+unblind."""
    async with pool.acquire() as conn:
        exp = await _fetch_experiment_row(conn, experiment_id)
        _require_legacy_v1_experiment_surface(exp)
        rows = await _fetch_experiment_export_rows(conn, experiment_id, exp["kind"])
        unblinded = exp["status"] == "completed" and await _experiment_unblind_recorded(conn, experiment_id)
        resolutions = None
        if unblinded:
            res_rows = await conn.fetch(
                "SELECT blinded_label, physical_arm, resolved_at, resolution_source "
                "FROM fn_runtime_v1_arm_resolutions($1::uuid)",
                experiment_id,
            )
            resolutions = [
                ExperimentArmResolution(
                    blinded_label=r["blinded_label"],
                    physical_arm=r["physical_arm"],
                    resolved_at=r["resolved_at"],
                    resolution_source=r["resolution_source"],
                )
                for r in res_rows
            ]
    return ExperimentExport(
        experiment_id=str(exp["experiment_id"]),
        kind=exp["kind"],
        status=exp["status"],
        rows=rows,
        export_sha256=_experiment_export_hash(rows),
        unblinded=unblinded,
        arm_resolutions=resolutions,
    )


@app.get("/experiments/{experiment_id}/device-policy", response_model=ExperimentDevicePolicyIdentity)
@app.get("/api/v1/experiments/{experiment_id}/device-policy", response_model=ExperimentDevicePolicyIdentity)
async def get_experiment_device_policy(
    experiment_id: str,
    _operator: None = Depends(require_experiment_operator_access),
):
    """OPERATOR/SAFETY surface (§8.7): latest device-confirmed effective-policy
    identity row for the experiment's greenhouse. Separately token-gated
    (operator token) — NOT part of the blinded-analyst surface. The existing
    /setpoints compatibility route is intentionally untouched."""
    async with pool.acquire() as conn:
        exp = await _fetch_experiment_row(conn, experiment_id)
        _require_legacy_v1_experiment_surface(exp)
        snap = await conn.fetchrow(
            """
            SELECT snapshot_id, device_id, greenhouse_id, reported_at, schema_revision,
                   device_generation, assignment_id::text AS assignment_id,
                   content_sha256, activation_sha256,
                   lower(validity) AS valid_from, upper(validity) AS valid_to,
                   apply_state, firmware_revision
            FROM policy_device_snapshots
            WHERE greenhouse_id = $1
            ORDER BY reported_at DESC
            LIMIT 1
            """,
            exp["greenhouse_id"],
        )
        if snap is None:
            raise HTTPException(404, "No device policy snapshot recorded for this greenhouse")
    return ExperimentDevicePolicyIdentity(
        snapshot_id=snap["snapshot_id"],
        device_id=snap["device_id"],
        greenhouse_id=snap["greenhouse_id"],
        reported_at=snap["reported_at"],
        schema_revision=snap["schema_revision"],
        device_generation=snap["device_generation"],
        assignment_id=snap["assignment_id"],
        content_sha256=snap["content_sha256"],
        activation_sha256=snap["activation_sha256"],
        valid_from=snap["valid_from"],
        valid_to=snap["valid_to"],
        apply_state=snap["apply_state"],
        firmware_revision=snap["firmware_revision"],
    )


@app.get(
    "/experiments/{experiment_id}/component-status",
    response_model=ComponentExperimentStatus,
)
@app.get(
    "/api/v1/experiments/{experiment_id}/component-status",
    response_model=ComponentExperimentStatus,
)
async def get_component_experiment_status(
    experiment_id: str,
    _operator: None = Depends(require_experiment_operator_access),
):
    """Read-only v2 safety/integrity state for facility operators.

    Treatment profile, component values, mapping, secret, comparative outcome,
    and efficacy data are deliberately absent. The function-provided opaque
    assignment ID is exposed only for active randomized work; this endpoint
    does not recover X/Y through another relation. The two SHA-256 fields are
    labeled as distinct server-derived identities and never as device-echoed
    state.
    """
    try:
        normalized_experiment_id = str(uuid.UUID(experiment_id))
    except (TypeError, ValueError):
        raise HTTPException(404, "Experiment not found")

    dedicated_pool = experiment_lifecycle_pool
    if dedicated_pool is None or getattr(dedicated_pool, "lifecycle_role_attested", False) is not True:
        raise HTTPException(503, "Confirmed-component v2 status database duty unavailable")
    try:
        async with dedicated_pool.acquire() as conn:
            status = await conn.fetchrow(_EXPERIMENT_V2_API_STATUS_SQL, normalized_experiment_id)
    except Exception as exc:
        log.error("experiment lifecycle status function unavailable error=%s", type(exc).__name__)
        raise HTTPException(503, "Confirmed-component v2 status database duty unavailable") from exc

    if status is None:
        raise HTTPException(404, "Confirmed-component v2 experiment not found")

    gate_allowed, gate_reason = component_experiment_gate()
    if gate_allowed and active_experiment_id() != normalized_experiment_id:
        gate_allowed = False
        gate_reason = "active_experiment_id_mismatch"

    try:
        current_work, state_identity = _validate_component_status_row(status, normalized_experiment_id)
        return ComponentExperimentStatus(
            experiment_id=str(status["experiment_id"]),
            kind=status["experiment_kind"],
            protocol_version=status["protocol_version"],
            transport_kind=status["transport_kind"],
            lifecycle_status=status["lifecycle_status"],
            execution_phase=status["execution_phase"],
            admission_state=status["admission_state"],
            component_capability_mode=component_experiment_mode(),
            environment_admissible=gate_allowed,
            environment_gate_reason=gate_reason,
            db_component_enabled=status["component_enabled"],
            current_work=current_work,
            approvals=ComponentExperimentApprovals(
                scoped_probe=status["scoped_probe_approved"],
                combined_physical=status["combined_physical_approved"],
                randomized_day_1=status["randomized_day_1_approved"],
            ),
            state_identity=state_identity,
            open_exposures=status["open_exposure_count"],
            lease_generation=status["lease_generation"],
            revision_bundle_sha256=status["revision_bundle_sha256"],
            firmware_revision=status["firmware_revision"],
            config_revision=status["config_revision"],
            registry_revision=status["registry_revision"],
            grid_revision=status["grid_revision"],
        )
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        log.error("experiment lifecycle status function returned an invalid contract error=%s", type(exc).__name__)
        raise HTTPException(503, "Confirmed-component v2 status database duty unavailable") from exc
