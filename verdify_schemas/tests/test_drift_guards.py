"""Drift guards — assert every DB-row schema's fields are a subset of the
live table's `information_schema.columns`. When the DB grows or loses a
column, the matching schema test fires immediately instead of after an
outage downstream.

Each guard:
  - Queries information_schema.columns for the target table's column set
  - Compares against the Pydantic model's field names
  - Allows the schema to be a SUBSET (model.extra="ignore" tolerates extras)
  - Flags fields the schema declares but the DB doesn't have (renamed /
    dropped) — this is the real regression path.

Runs against live Docker Postgres. If `docker` isn't available, these skip
(for example on a CI runner that builds schemas without a DB attached).
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

from verdify_schemas.alerts import AlertLogRow
from verdify_schemas.crop_profiles import CropTargetProfile
from verdify_schemas.crops import Crop, CropEvent, Observation
from verdify_schemas.daily import DailySummaryRow
from verdify_schemas.forecast import ForecastHour
from verdify_schemas.forecast_ops import ForecastActionLog, ForecastActionRule
from verdify_schemas.lessons import PlannerLesson
from verdify_schemas.media import ImageObservation
from verdify_schemas.operations import (
    ConsumablesLog,
    Harvest,
    IrrigationLog,
    IrrigationSchedule,
    LabResult,
    MaintenanceLog,
    Treatment,
)
from verdify_schemas.plan import PlanDeliveryLogRow, PlanJournalRow
from verdify_schemas.setpoint import (
    SetpointChange,
    SetpointClamp,
    SetpointPlanRow,
    SetpointSnapshot,
)
from verdify_schemas.system_infra import (
    DataGap,
    ESP32LogRow,
    SensorRegistry,
    UtilityCost,
)
from verdify_schemas.telemetry import (
    ClimateActionLogRow,
    ClimateRow,
    Diagnostics,
    EnergySample,
    EquipmentStateEvent,
    OverrideEvent,
    SystemStateRow,
)


def _docker_available() -> bool:
    r = subprocess.run(["docker", "ps"], capture_output=True, text=True, check=False)
    return r.returncode == 0


def _ci_postgres_reachable() -> bool:
    """In GitHub Actions a Postgres service container is reachable at
    $POSTGRES_HOST:$POSTGRES_PORT (or defaults). When that env is present we
    run psql against it directly; otherwise we expect a local docker-compose
    stack and shell out to `docker exec`.
    """
    return bool(os.environ.get("POSTGRES_HOST"))


pytestmark = pytest.mark.skipif(
    not (_ci_postgres_reachable() or _docker_available()),
    reason="no DB backend available (need POSTGRES_HOST env or local docker)",
)


def _table_columns(table: str) -> set[str]:
    sql = f"SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='{table}'"
    if _ci_postgres_reachable():
        env = os.environ.copy()
        env.setdefault("PGHOST", env.get("POSTGRES_HOST", "localhost"))
        env.setdefault("PGPORT", env.get("POSTGRES_PORT", "5432"))
        env.setdefault("PGUSER", env.get("POSTGRES_USER", "verdify"))
        env.setdefault("PGPASSWORD", env.get("POSTGRES_PASSWORD", "verdify"))
        env.setdefault("PGDATABASE", env.get("POSTGRES_DB", "verdify"))
        cmd = ["psql", "-t", "-A", "-c", sql]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=True, env=env)
    else:
        r = subprocess.run(
            ["docker", "exec", "verdify-timescaledb", "psql", "-U", "verdify", "-d", "verdify", "-t", "-A", "-c", sql],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
    return {ln.strip() for ln in r.stdout.splitlines() if ln.strip()}


def _tasks_route_target_columns(repo_root: Path, *mapping_names: str) -> set[str]:
    """Read literal HA/weather route-map target columns without importing tasks.py.

    The ingestor service module imports async runtime dependencies at module
    scope. This guard only needs the first element of each route tuple, so keep
    schema CI independent from the full service dependency tree.
    """
    tree = ast.parse((repo_root / "ingestor" / "tasks.py").read_text())
    wanted = set(mapping_names)
    columns: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        target_names = {target.id for target in node.targets if isinstance(target, ast.Name)}
        matched = wanted & target_names
        if not matched:
            continue
        assert isinstance(node.value, ast.Dict), f"{', '.join(sorted(matched))} must be a literal dict"
        for value in node.value.values:
            assert isinstance(value, ast.Tuple) and value.elts, "route-map values must be tuples"
            column_node = value.elts[0]
            assert isinstance(column_node, ast.Constant) and isinstance(column_node.value, str), (
                "route-map tuple first element must be a column name string"
            )
            columns.add(column_node.value)
        wanted -= matched
    assert wanted == set(), f"missing route map(s) in ingestor/tasks.py: {sorted(wanted)}"
    return columns


# (schema class, DB table name)
DB_BACKED = [
    # Sprint 20/21 — telemetry + plan + setpoint + crops + lessons
    (ClimateRow, "climate"),
    (ClimateActionLogRow, "climate_action_log"),
    (Diagnostics, "diagnostics"),
    (EquipmentStateEvent, "equipment_state"),
    (EnergySample, "energy"),
    (SystemStateRow, "system_state"),
    (OverrideEvent, "override_events"),
    (SetpointChange, "setpoint_changes"),
    (SetpointPlanRow, "setpoint_plan"),
    (SetpointSnapshot, "setpoint_snapshot"),
    (SetpointClamp, "setpoint_clamps"),
    (PlanJournalRow, "plan_journal"),
    (PlanDeliveryLogRow, "plan_delivery_log"),
    (DailySummaryRow, "daily_summary"),
    (ForecastHour, "weather_forecast"),
    (AlertLogRow, "alert_log"),
    (Crop, "crops"),
    (CropEvent, "crop_events"),
    (Observation, "observations"),
    (PlannerLesson, "planner_lessons"),
    # Sprint 22 Phase 2 — operations + forecast ops + system infra + media
    (Treatment, "treatments"),
    (Harvest, "harvests"),
    (IrrigationLog, "irrigation_log"),
    (IrrigationSchedule, "irrigation_schedule"),
    (LabResult, "lab_results"),
    (MaintenanceLog, "maintenance_log"),
    (ConsumablesLog, "consumables_log"),
    (CropTargetProfile, "crop_target_profiles"),
    (ForecastActionLog, "forecast_action_log"),
    (ForecastActionRule, "forecast_action_rules"),
    (UtilityCost, "utility_cost"),
    (ImageObservation, "image_observations"),
    (SensorRegistry, "sensor_registry"),
    (ESP32LogRow, "esp32_logs"),
    (DataGap, "data_gaps"),
]


@pytest.mark.parametrize("model_class,table_name", DB_BACKED)
def test_schema_fields_subset_of_db_columns(model_class, table_name):
    """Every field declared in the schema must exist in the DB table."""
    db_cols = _table_columns(table_name)
    if not db_cols:
        pytest.skip(f"table {table_name!r} not found (migration pending?)")
    schema_fields = set(model_class.model_fields.keys())
    missing_in_db = sorted(schema_fields - db_cols)
    assert not missing_in_db, (
        f"{model_class.__name__} declares field(s) the {table_name!r} table doesn't have: "
        f"{missing_in_db}. Either the schema is stale (field renamed/removed in DB) "
        f"or a migration is missing."
    )


# Columns we require to propagate *into* the schema whenever the DB has them.
# Multi-tenant correctness: if a table is tenant-scoped in the DB, the model
# must know about it. Without this guard, renderers and queries silently miss
# the greenhouse_id filter and data bleeds across tenants.
TENANCY_CRITICAL_COLUMNS = ("greenhouse_id",)
LINKAGE_CRITICAL_COLUMNS = {
    "crop_events": ("crop_id", "position_id"),
    "harvests": ("crop_id", "position_id"),
    "observations": ("crop_id", "position_id", "zone_id"),
    "treatments": ("crop_id", "position_id", "observation_id"),
}


@pytest.mark.parametrize("model_class,table_name", DB_BACKED)
def test_tenancy_critical_columns_declared_by_schema(model_class, table_name):
    """If the DB table has a tenancy-critical column, the schema must declare it.

    One-way drift (schema → DB) is already guarded by
    test_schema_fields_subset_of_db_columns. This is the other direction: the
    DB has greenhouse_id; the schema must too. Prevents silent tenant bleed.
    """
    db_cols = _table_columns(table_name)
    if not db_cols:
        pytest.skip(f"table {table_name!r} not found (migration pending?)")
    schema_fields = set(model_class.model_fields.keys())
    missing_in_schema = [c for c in TENANCY_CRITICAL_COLUMNS if c in db_cols and c not in schema_fields]
    assert not missing_in_schema, (
        f"{model_class.__name__} is missing tenancy-critical field(s) that exist "
        f"on the {table_name!r} table: {missing_in_schema}. Declare them on the "
        f"model so renderers and queries can filter by tenant."
    )


@pytest.mark.parametrize("model_class,table_name", DB_BACKED)
def test_operation_linkage_columns_declared_by_schema(model_class, table_name):
    """Hands-on crop work rows must preserve crop/position lineage in schemas."""

    critical = LINKAGE_CRITICAL_COLUMNS.get(table_name)
    if not critical:
        return
    db_cols = _table_columns(table_name)
    if not db_cols:
        pytest.skip(f"table {table_name!r} not found (migration pending?)")
    schema_fields = set(model_class.model_fields.keys())
    missing_in_schema = [c for c in critical if c in db_cols and c not in schema_fields]
    assert not missing_in_schema, (
        f"{model_class.__name__} is missing crop linkage field(s) that exist "
        f"on the {table_name!r} table: {missing_in_schema}. Declare them so "
        f"harvest/treatment records stay traceable to crop positions."
    )


def test_ingestor_climate_route_targets_declared_by_schema_and_db():
    """Every climate column written by ingestor maps must be modeled and present.

    ClimateRow keeps extra='ignore' so older readers survive additive DB
    columns. That means the write-path maps need a reverse guard; otherwise a
    mapped/live column can bypass Pydantic range validation indefinitely.
    """

    repo_root = Path(__file__).resolve().parents[2]
    ingestor_path = repo_root / "ingestor"
    for path in (str(repo_root), str(ingestor_path)):
        if path not in sys.path:
            sys.path.insert(0, path)

    import entity_map  # noqa: PLC0415

    mapped_columns = set(entity_map.CLIMATE_MAP.values())
    mapped_columns.update(entity_map.ESPHOME_FEEDBACK_MAP.values())
    mapped_columns.update(entity_map.MQTT_FEEDBACK_MAP.values())
    mapped_columns.update(_tasks_route_target_columns(repo_root, "_TEMPEST_MAP", "_HYDRO_MAP", "_CENTER_FEEDBACK_MAP"))

    schema_fields = set(ClimateRow.model_fields)
    db_cols = _table_columns("climate")
    missing_schema = sorted(mapped_columns - schema_fields)
    missing_db = sorted(mapped_columns - db_cols)

    assert missing_schema == []
    assert missing_db == []
