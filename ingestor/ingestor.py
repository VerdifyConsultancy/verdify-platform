"""
ingestor.py — Verdify ESP32 → TimescaleDB data ingestor

Connects directly to the greenhouse ESP32 via aioesphomeapi (native encrypted
protocol). No Home Assistant dependency. Writes to 6 TimescaleDB tables.

Usage:
    python3 ingestor.py

Environment: loads from .env in same directory.
"""

import asyncio
import json
import logging
import math
import os
import sys
import urllib.request
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import asyncpg
import paho.mqtt.client as paho_mqtt
import shared
from aioesphomeapi import APIClient, APIConnectionError, LogLevel
from aioesphomeapi.model import (
    BinarySensorInfo,
    NumberInfo,
    SensorInfo,
    SwitchInfo,
    TextSensorInfo,
)
from dotenv import load_dotenv
from entity_map import (
    CFG_READBACK_MAP,
    CLIMATE_MAP,
    DAILY_ACCUM_MAP,
    DIAGNOSTIC_MAP,
    EQUIPMENT_BINARY_MAP,
    EQUIPMENT_SWITCH_MAP,
    ESPHOME_FEEDBACK_MAP,
    FEEDBACK_VALUE_RANGES,
    MQTT_FEEDBACK_MAP,
    SETPOINT_MAP,
    STATE_MAP,
    SWITCH_TO_ENTITY,
    normalize_feedback_value,
)
from esp32_push import push_to_esp32
from mqtt_fanout import (
    FanoutPublisher,
    assert_modes_consistent,
    decode_payload,
    publish_all_enabled,
    subscribe_mode_enabled,
    subscribe_topic_filter,
)
from occupancy import refresh_latest_occupancy_state, sync_occupancy_state
from pydantic import ValidationError
from tasks import (
    BAND_DRIVEN_PARAMS,
    IRRIGATION_SCHEDULE_PARAMS,
    alert_monitor,
    daily_summary_live,
    forecast_action_engine,
    forecast_deviation_check,
    forecast_sync,
    gpu_power_sync,
    grow_light_daily,
    ha_sensor_sync,
    infra_cpu_sync,
    matview_refresh,
    midnight_watch,
    planner_memory_ingest_sync,
    planning_heartbeat,
    readback_abs_tolerance,
    setpoint_confirmation_monitor,
    setpoint_dispatcher,
    shelly_sync,
    slack_operator_briefs,
    tempest_sync,
    water_flowing_sync,
)

from verdify_schemas import (
    CLIMATE_INTENT_CONTRACT_VERSION,
    ClimateActionLogRow,
    ClimateRow,
    DailySummaryRow,
    Diagnostics,
    EquipmentStateEvent,
    ESP32LogRow,
    HAEntityState,
    OverrideEvent,
    SetpointChange,
    SetpointSnapshot,
    SystemStateRow,
)
from verdify_schemas.tunable_registry import get as get_tunable

# ──────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────
load_dotenv(Path(__file__).parent / ".env")

GREENHOUSE_ID = os.environ.get("GREENHOUSE_ID", "vallery")

# ESP32 config: loaded from DB in main(), fallback to .env
ESP32_HOST = os.environ.get("ESP32_HOST", "192.168.10.111")
ESP32_PORT = int(os.environ.get("ESP32_PORT", 6053))
ESP32_API_KEY = os.environ.get("ESP32_API_KEY", "")

DB_DSN = (
    f"postgresql://{os.environ['DB_USER']}:{os.environ['DB_PASSWORD']}"
    f"@{os.environ['DB_HOST']}:{os.environ['DB_PORT']}/{os.environ['DB_NAME']}"
)

CLIMATE_FLUSH_INTERVAL = 60  # seconds between climate row writes
CLIMATE_ACTION_LOG_INTERVAL = 60  # seconds between controller decision snapshots
DIAG_FLUSH_INTERVAL = 60  # seconds between diagnostics row writes
LOG_FLUSH_INTERVAL = 10  # seconds between log batch writes

# Loki push endpoint (nexus management VM)
LOKI_URL = os.environ.get("LOKI_URL", "")  # Empty = disabled

# Map aioesphomeapi LogLevel to string
LOG_LEVEL_MAP = {
    LogLevel.LOG_LEVEL_NONE: "NONE",
    LogLevel.LOG_LEVEL_ERROR: "ERROR",
    LogLevel.LOG_LEVEL_WARN: "WARN",
    LogLevel.LOG_LEVEL_INFO: "INFO",
    LogLevel.LOG_LEVEL_DEBUG: "DEBUG",
    LogLevel.LOG_LEVEL_VERBOSE: "VERBOSE",
    LogLevel.LOG_LEVEL_VERY_VERBOSE: "VERY_VERBOSE",
}
LOG_LEVEL_BY_NAME = {
    "NONE": LogLevel.LOG_LEVEL_NONE,
    "ERROR": LogLevel.LOG_LEVEL_ERROR,
    "WARN": LogLevel.LOG_LEVEL_WARN,
    "WARNING": LogLevel.LOG_LEVEL_WARN,
    "INFO": LogLevel.LOG_LEVEL_INFO,
    "DEBUG": LogLevel.LOG_LEVEL_DEBUG,
    "VERBOSE": LogLevel.LOG_LEVEL_VERBOSE,
    "VERY_VERBOSE": LogLevel.LOG_LEVEL_VERY_VERBOSE,
}
ESP32_LOG_LEVEL = LOG_LEVEL_BY_NAME.get(os.environ.get("ESP32_LOG_LEVEL", "NONE").strip().upper())
if ESP32_LOG_LEVEL is None:
    ESP32_LOG_LEVEL = LogLevel.LOG_LEVEL_NONE
ESP32_LOG_LEVEL_NAME = LOG_LEVEL_MAP.get(ESP32_LOG_LEVEL, "NONE")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("ingestor")

CLIMATE_ACTION_LOG_ENTITIES = frozenset(
    {
        "climate_action",
        "climate_priority_axis",
        "climate_candidate_summary",
        "climate_moisture_assist_state",
        "climate_moisture_zone",
        "climate_temp_error_f",
        "climate_vpd_error_kpa",
        "climate_fog_margin_kpa",
        "climate_fog_block_reason",
        "climate_resource_cost_estimate",
        "climate_next_mist_eligible_s",
        "moisture_block_reason",
        "vent_mist_assist_status",
        "direct_wet_zone_mask",
        "fog_block_reason",
        "greenhouse_state",
        "mode_reason",
    }
)
CLIMATE_WET_ACTIONS = frozenset(
    {
        "VENT_COOL_MIST_ASSIST",
        "VENT_COOL_FOG_ASSIST",
        "SEALED_HUMIDIFY",
        "SEALED_FOG",
        "SAFETY_COOL",
    }
)
CLIMATE_FOG_ACTIONS = frozenset({"VENT_COOL_FOG_ASSIST", "SEALED_FOG", "SAFETY_COOL"})
_NO_FOG_BLOCK_REASONS = {None, "", "none"}
_FOG_ALLOWED_REASONS = _NO_FOG_BLOCK_REASONS | {"served"}
CLIMATE_RELAY_EQUIPMENT = (
    "heat1",
    "heat2",
    "fan1",
    "fan2",
    "vent",
    "fog",
    "mister_south",
    "mister_west",
    "mister_center",
)


# ──────────────────────────────────────────────────────────────
# State
# ──────────────────────────────────────────────────────────────
class State:
    """Mutable ingestor state shared across callbacks."""

    def __init__(self):
        # Fresh values received since last flush (cleared after each write)
        self.climate: dict[str, float] = {}
        # Last-known values with timestamps (never cleared, used as fallback)
        self.climate_latest: dict[str, tuple[float, datetime]] = {}
        self.equipment: dict[str, bool] = {}
        self.system: dict[str, str] = {}
        self.setpoints: dict[str, float] = {}
        self.diagnostics: dict[str, Any] = {}
        self.daily: dict[str, float] = {}

        # ESP32 configured value readback (cfg_* sensors → setpoint_snapshot)
        self.cfg_readback: dict[str, float] = {}  # param → value

        # object_id → entity key from API enumeration
        self.key_to_object_id: dict[int, str] = {}
        self.key_to_type: dict[int, str] = {}  # 'sensor','binary','text','number','switch'

        # Pending setpoint changes to write
        self.pending_setpoints: list[tuple[str, float]] = []

        # Pending equipment events to write
        self.pending_equipment: list[tuple[str, bool]] = []

        # Pending state transitions to write
        self.pending_states: list[tuple[str, str]] = []

        # OBS-1e (Sprint 16): firmware override event audit.
        # Each tuple is (override_type, mode_str) — written per start event
        # to override_events. Populated in on_state_change when the
        # active_overrides text_sensor transitions to include new flags.
        self.pending_override_events: list[tuple[str, str | None]] = []
        # Last-seen active override set (for diff on next transition)
        self.last_override_set: set[str] = set()

        # Flag: daily snapshot taken today?
        self._daily_snapshot_date: str | None = None

        # Pending ESP32 log messages
        self.pending_logs: list[tuple[str, str, str]] = []  # (level, tag, message)


state = State()

# Telemetry fan-out publisher (#113). None unless VERDIFY_MQTT_PUBLISH_ALL=1
# (prod-only). Set up in main(); used by the flush path to re-emit every flushed
# row onto the cross-env bus. Never gates the local DB write.
_fanout_publisher: FanoutPublisher | None = None


def _fanout_publish(table: str, row: dict[str, Any]) -> None:
    """Best-effort publish of one flushed row to the fan-out bus (#113).

    No-op unless the prod publish-all publisher is active. Bus errors are
    swallowed inside FanoutPublisher.publish_row — telemetry capture (the local
    DB write) is Track A and must never be blocked by a bus outage.
    """
    if _fanout_publisher is None:
        return
    _fanout_publisher.publish_row(table, GREENHOUSE_ID, row)


def _record_mqtt_feedback(topic: str, payload: str) -> bool:
    """Record a live MQTT feedback payload into the next climate flush."""
    col = MQTT_FEEDBACK_MAP.get(topic)
    if not col:
        return False
    val = normalize_feedback_value(col, payload)
    if val is None:
        log.warning("MQTT feedback rejected invalid value: %s column=%s payload=%r", topic, col, payload)
        return False
    state.climate[col] = val
    return True


def _record_climate_sensor(obj_id: str, value: Any) -> bool:
    """Record an ESPHome climate sensor, applying feedback range guards."""
    col = CLIMATE_MAP.get(obj_id) or ESPHOME_FEEDBACK_MAP.get(obj_id)
    if not col:
        return False
    if col in FEEDBACK_VALUE_RANGES:
        normalized = normalize_feedback_value(col, value)
        if normalized is None:
            log.warning("ESPHome feedback rejected invalid value: %s column=%s value=%r", obj_id, col, value)
            return True
        state.climate[col] = normalized
        return True
    state.climate[col] = value
    return True


def _parse_override_set(val: str) -> set[str]:
    """OBS-1e: parse "none" / "a,b,c" payload from active_overrides text_sensor."""
    if not val or val == "none":
        return set()
    return {t.strip() for t in val.split(",") if t.strip() and t.strip() != "none"}


# ──────────────────────────────────────────────────────────────
# DB helpers
# ──────────────────────────────────────────────────────────────
async def write_climate(pool: asyncpg.Pool, ts: datetime) -> None:
    """Write a climate row using two-tier buffer: fresh + last-known.

    Strategy:
    - state.climate = values received since last flush (cleared after each write)
    - state.climate_latest = last known value + timestamp per sensor (persistent)

    On each flush:
    1. Start with last-known values (within 10 min staleness window)
    2. Overlay with fresh values (takes precedence)
    3. Update last-known with fresh values
    4. Clear fresh buffer

    This prevents phantom data (zombie ingestor stale values from hours ago)
    while preserving legitimate current values from sensors that publish on-change.
    """
    STALENESS_TIMEOUT = 600  # 10 minutes — sensors not seen in 10 min are excluded

    # Step 1: Build merged row from last-known (within timeout) + fresh
    merged = {}
    for col, (val, seen_at) in state.climate_latest.items():
        age = (ts - seen_at).total_seconds()
        if age < STALENESS_TIMEOUT:
            merged[col] = val

    # Step 2: Overlay fresh values (always take precedence)
    merged.update(state.climate)

    # Step 3: Update last-known with any fresh values
    for col, val in state.climate.items():
        state.climate_latest[col] = (val, ts)

    # Step 4: Clear fresh buffer
    state.climate.clear()

    # Step 5: Validate + write merged row
    cols = list(merged.keys())
    if not cols:
        return
    # Validate ranges on every known column (rh∈[0,100], vpd∈[0,20], etc.).
    # extra="ignore" means novel column names pass through to the INSERT —
    # the DB will surface those (column doesn't exist) if the entity map is wrong.
    try:
        ClimateRow.model_validate({"ts": ts, "greenhouse_id": GREENHOUSE_ID, **merged})
    except ValidationError as e:
        log.error(f"climate row failed schema validation: {e}")
        return

    # Sprint 23 Phase 4b: Pydantic validation at the asyncpg boundary.
    # Validates numeric ranges (rh 0-100, vpd 0-20, ts tz-aware, etc.).
    # Column-name drift is guarded separately by map/schema tests because
    # ClimateRow intentionally tolerates additive DB columns for older readers.
    if ClimateRow is not None:
        try:
            ClimateRow.model_validate({"ts": ts, **merged})
        except ValidationError as e:
            log.error("climate row failed Pydantic validation: %s", e)
            # Continue — the write still attempts. Validation is observability,
            # not a hard gate (yet). A future sprint will promote to fail-closed
            # once the known-false-positive-free baseline is proven.

    cols_sql = ", ".join(["ts"] + cols)
    placeholders = ", ".join([f"${i + 1}" for i in range(len(cols) + 1)])
    values = [ts] + [merged.get(c) for c in cols]
    async with pool.acquire() as conn:
        await conn.execute(
            f"INSERT INTO climate ({cols_sql}) VALUES ({placeholders})",
            *values,
        )
    log.debug(f"climate row written ({len(cols)} columns)")
    # #113: re-emit the exact row we just persisted onto the fan-out bus so
    # dev/stage subscribers write the same climate row to their own DB.
    _fanout_publish("climate", {"ts": ts, **merged})


async def write_equipment_events(pool: asyncpg.Pool, ts: datetime) -> None:
    """Flush pending equipment state change events."""
    if not state.pending_equipment:
        return
    events = state.pending_equipment.copy()
    state.pending_equipment.clear()
    validated: list[tuple[datetime, str, bool]] = []
    for equip, s in events:
        try:
            EquipmentStateEvent(ts=ts, equipment=equip, state=s, greenhouse_id=GREENHOUSE_ID)
        except ValidationError as e:
            log.error(f"equipment_state skipped (validation failed: {e}): equip={equip} state={s}")
            continue
        validated.append((ts, equip, s))
    if not validated:
        return

    # Sprint 23 Phase 4b: Pydantic validation against the EquipmentId Literal.
    # Catches equipment slugs that aren't in the known set (typo → silent
    # misroute previously). Failed validations are logged; the write still
    # proceeds so a firmware-added slug doesn't halt the pipeline.
    if EquipmentStateEvent is not None:
        for equip, s in events:
            try:
                EquipmentStateEvent.model_validate({"ts": ts, "equipment": equip, "state": s})
            except ValidationError as e:
                log.warning("equipment_state event failed validation: equipment=%s state=%s: %s", equip, s, e)

    async with pool.acquire() as conn:
        await conn.executemany(
            "INSERT INTO equipment_state (ts, equipment, state) VALUES ($1, $2, $3)",
            validated,
        )
    log.debug(f"equipment_state: {len(validated)} events written")
    # #113: fan-out each equipment event to the bus.
    for row_ts, equip, s in validated:
        _fanout_publish("equipment_state", {"ts": row_ts, "equipment": equip, "state": s})


async def write_state_transitions(pool: asyncpg.Pool, ts: datetime) -> set[str]:
    """Flush pending state machine transitions."""
    if not state.pending_states:
        return set()
    transitions = state.pending_states.copy()
    state.pending_states.clear()
    validated: list[tuple[datetime, str, str]] = []
    for entity, val in transitions:
        try:
            SystemStateRow(ts=ts, entity=entity, value=val, greenhouse_id=GREENHOUSE_ID)
        except ValidationError as e:
            log.error(f"system_state skipped (validation failed: {e}): entity={entity} value={val!r}")
            continue
        validated.append((ts, entity, val))
    if not validated:
        return set()
    async with pool.acquire() as conn:
        await conn.executemany(
            "INSERT INTO system_state (ts, entity, value) VALUES ($1, $2, $3)",
            validated,
        )
    log.debug(f"system_state: {len(validated)} transitions written")
    # #113: fan-out each transition to the bus.
    for row_ts, entity, val in validated:
        _fanout_publish("system_state", {"ts": row_ts, "entity": entity, "value": val})
    return {entity for _, entity, _ in validated}


def _finite_state_float(value: str | None) -> float | None:
    """Parse a system_state string to a finite float, or None.

    Routes the parse through the shared ``HAEntityState.as_float()`` contract
    (handles unavailable / non-numeric values) and additionally rejects
    non-finite results (inf/nan), which must never reach DB float columns.
    """
    if value is None:
        return None
    parsed = HAEntityState(entity_id="system_state", state=value).as_float()
    if parsed is None or not math.isfinite(parsed):
        return None
    return parsed


def _parse_json_object(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {"raw": value}
    return parsed if isinstance(parsed, dict) else {"raw": parsed}


def _climate_wet_assist_status(action: str, moisture_state: str | None, fog_allowed: bool) -> tuple[bool, str | None]:
    if action not in CLIMATE_WET_ACTIONS:
        return False, None
    if moisture_state == "served":
        return True, None
    if fog_allowed:
        return True, None

    vent_status = state.system.get("vent_mist_assist_status") or ""
    blocked_reason = None
    if vent_status.startswith("blocked:"):
        blocked_reason = vent_status.split(":", 1)[1] or "blocked"

    if moisture_state in {"engage_delay", "pulse_on", "pulse_gap"}:
        if blocked_reason and blocked_reason not in {"engage_delay", "pulse_gap", "served", "none"}:
            return False, blocked_reason
        return True, None
    if blocked_reason:
        return False, blocked_reason
    if moisture_state == "blocked":
        return False, state.system.get("moisture_block_reason") or "blocked"

    block = state.system.get("moisture_block_reason")
    if block and block not in {"none", "served", "pulse_gap"}:
        return False, block
    return False, None


def _normalized_block_reason(value: object) -> str | None:
    if value is None:
        return None
    reason = str(value).strip()
    return reason or None


def _climate_fog_assist_status(
    action: str,
    climate_block_reason: object,
    final_block_reason: object,
) -> tuple[bool, str | None]:
    """Return final fog authority after candidate and relay-level gates."""

    climate_reason = _normalized_block_reason(climate_block_reason)
    final_reason = _normalized_block_reason(final_block_reason)
    reason = final_reason if final_reason not in _NO_FOG_BLOCK_REASONS else climate_reason
    fog_allowed = bool(action in CLIMATE_FOG_ACTIONS and reason in _FOG_ALLOWED_REASONS)
    return fog_allowed, reason


async def write_climate_action_log(pool: asyncpg.Pool, ts: datetime) -> bool:
    """Persist one structured ClimateIntent controller decision snapshot."""
    action = state.system.get("climate_action")
    priority_axis = state.system.get("climate_priority_axis")
    if not action or not priority_axis:
        return False

    moisture_state = state.system.get("climate_moisture_assist_state")
    moisture_zone = state.system.get("climate_moisture_zone") or "none"
    fog_allowed, fog_block_reason = _climate_fog_assist_status(
        action,
        state.system.get("climate_fog_block_reason"),
        state.system.get("fog_block_reason"),
    )
    wet_assist_allowed, wet_block_reason = _climate_wet_assist_status(action, moisture_state, fog_allowed)
    relay_truth = {equipment: bool(state.equipment.get(equipment, False)) for equipment in CLIMATE_RELAY_EQUIPMENT}
    source_system_state = {entity: state.system.get(entity) for entity in sorted(CLIMATE_ACTION_LOG_ENTITIES)}
    resource_cost = _parse_json_object(state.system.get("climate_resource_cost_estimate"))

    try:
        ClimateActionLogRow(
            ts=ts,
            greenhouse_id=GREENHOUSE_ID,
            climate_action=action,
            priority_axis=priority_axis,
            temp_band_error_f=_finite_state_float(state.system.get("climate_temp_error_f")),
            vpd_band_error_kpa=_finite_state_float(state.system.get("climate_vpd_error_kpa")),
            moisture_assist_state=moisture_state,
            moisture_zone=moisture_zone,
            wet_assist_allowed=wet_assist_allowed,
            wet_assist_block_reason=wet_block_reason,
            fog_allowed=fog_allowed,
            fog_block_reason=fog_block_reason,
            relay_truth=relay_truth,
            resource_cost_estimate=resource_cost,
            climate_intent_version=CLIMATE_INTENT_CONTRACT_VERSION,
            candidate_summary=state.system.get("climate_candidate_summary"),
            source_system_state=source_system_state,
        )
    except ValidationError as e:
        log.error("climate_action_log skipped (validation failed: %s): action=%s priority=%s", e, action, priority_axis)
        return False

    async with pool.acquire() as conn:
        await conn.execute(
            """
            WITH latest_climate AS (
                SELECT c.*
                  FROM climate c
                 WHERE COALESCE(c.greenhouse_id, $1) = $1
                   AND c.temp_avg IS NOT NULL
                   AND c.vpd_avg IS NOT NULL
                 ORDER BY c.ts DESC
                 LIMIT 1
            ),
            band AS (
                SELECT
                    fn_setpoint_at($1, 'temp_low', COALESCE((SELECT ts FROM latest_climate), $2)) AS temp_low_f,
                    fn_setpoint_at($1, 'temp_high', COALESCE((SELECT ts FROM latest_climate), $2)) AS temp_high_f,
                    fn_setpoint_at($1, 'vpd_low', COALESCE((SELECT ts FROM latest_climate), $2)) AS vpd_low_kpa,
                    fn_setpoint_at($1, 'vpd_high', COALESCE((SELECT ts FROM latest_climate), $2)) AS vpd_high_kpa
            ),
            plan_context AS (
                SELECT sp.plan_id, sp.trigger_id, sp.planner_instance
                  FROM setpoint_plan sp
                 WHERE COALESCE(sp.greenhouse_id, $1) = $1
                   AND sp.is_active = true
                   AND sp.parameter <> 'plan_metadata'
                   AND sp.ts <= COALESCE((SELECT ts FROM latest_climate), $2)
                 ORDER BY sp.ts DESC, sp.created_at DESC
                 LIMIT 1
            )
            INSERT INTO climate_action_log (
                ts,
                greenhouse_id,
                climate_action,
                priority_axis,
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
                moisture_assist_state,
                moisture_zone,
                wet_assist_allowed,
                wet_assist_block_reason,
                fog_allowed,
                fog_block_reason,
                relay_truth,
                resource_cost_estimate,
                climate_intent_version,
                plan_id,
                trigger_id,
                planner_instance,
                sensor_status,
                candidate_summary,
                source_system_state
            )
            SELECT
                $2,
                $1,
                $3,
                $4,
                band.temp_low_f,
                CASE WHEN band.temp_low_f IS NULL OR band.temp_high_f IS NULL
                     THEN NULL ELSE (band.temp_low_f + band.temp_high_f) / 2.0 END,
                band.temp_high_f,
                band.vpd_low_kpa,
                CASE WHEN band.vpd_low_kpa IS NULL OR band.vpd_high_kpa IS NULL
                     THEN NULL ELSE (band.vpd_low_kpa + band.vpd_high_kpa) / 2.0 END,
                band.vpd_high_kpa,
                CASE WHEN lc.temp_avg IS NULL OR band.temp_low_f IS NULL OR band.temp_high_f IS NULL
                     THEN NULL ELSE lc.temp_avg - ((band.temp_low_f + band.temp_high_f) / 2.0) END,
                CASE WHEN lc.vpd_avg IS NULL OR band.vpd_low_kpa IS NULL OR band.vpd_high_kpa IS NULL
                     THEN NULL ELSE lc.vpd_avg - ((band.vpd_low_kpa + band.vpd_high_kpa) / 2.0) END,
                CASE
                    WHEN lc.temp_avg IS NULL OR band.temp_low_f IS NULL OR band.temp_high_f IS NULL THEN $5
                    WHEN lc.temp_avg < band.temp_low_f THEN lc.temp_avg - band.temp_low_f
                    WHEN lc.temp_avg > band.temp_high_f THEN lc.temp_avg - band.temp_high_f
                    ELSE 0.0
                END,
                CASE
                    WHEN lc.vpd_avg IS NULL OR band.vpd_low_kpa IS NULL OR band.vpd_high_kpa IS NULL THEN $6
                    WHEN lc.vpd_avg < band.vpd_low_kpa THEN lc.vpd_avg - band.vpd_low_kpa
                    WHEN lc.vpd_avg > band.vpd_high_kpa THEN lc.vpd_avg - band.vpd_high_kpa
                    ELSE 0.0
                END,
                $7,
                $8,
                $9,
                $10,
                $11,
                $12,
                $13::jsonb,
                $14::jsonb,
                $15,
                pc.plan_id,
                pc.trigger_id,
                pc.planner_instance,
                jsonb_strip_nulls(
                    $16::jsonb || jsonb_build_object(
                        'latest_climate_ts', lc.ts,
                        'latest_climate_age_s',
                            CASE
                                WHEN lc.ts IS NULL THEN NULL
                                ELSE greatest(0, extract(epoch FROM ($2 - lc.ts))::int)
                            END,
                        'temp_avg_present', lc.temp_avg IS NOT NULL,
                        'vpd_avg_present', lc.vpd_avg IS NOT NULL,
                        'band_context_complete',
                            band.temp_low_f IS NOT NULL
                            AND band.temp_high_f IS NOT NULL
                            AND band.vpd_low_kpa IS NOT NULL
                            AND band.vpd_high_kpa IS NOT NULL
                    )
                ),
                $17,
                $18::jsonb
            FROM band
            LEFT JOIN latest_climate lc ON true
            LEFT JOIN plan_context pc ON true
            """,
            GREENHOUSE_ID,
            ts,
            action,
            priority_axis,
            _finite_state_float(state.system.get("climate_temp_error_f")),
            _finite_state_float(state.system.get("climate_vpd_error_kpa")),
            moisture_state,
            moisture_zone,
            wet_assist_allowed,
            wet_block_reason,
            fog_allowed,
            fog_block_reason,
            json.dumps(relay_truth, sort_keys=True),
            json.dumps(resource_cost, sort_keys=True),
            CLIMATE_INTENT_CONTRACT_VERSION,
            json.dumps({"latest_climate_age_s": None}, sort_keys=True),
            state.system.get("climate_candidate_summary"),
            json.dumps(source_system_state, sort_keys=True),
        )
    log.debug("climate_action_log: action=%s priority=%s wet_allowed=%s", action, priority_axis, wet_assist_allowed)
    return True


async def write_override_events(pool: asyncpg.Pool, ts: datetime) -> None:
    """OBS-1e (Sprint 16): flush pending firmware override start events.

    Writes one row per newly-started override to override_events so the
    planner can correlate compliance misses with firmware decisions she
    cannot see any other way. "End" events are not written — the
    active_overrides system_state transitions carry that info.
    """
    if not state.pending_override_events:
        return
    events = state.pending_override_events.copy()
    state.pending_override_events.clear()
    validated: list[tuple[datetime, str, str | None]] = []
    for otype, mode in events:
        try:
            OverrideEvent(ts=ts, override_type=otype, mode=mode, greenhouse_id=GREENHOUSE_ID)
        except ValidationError as e:
            log.error(f"override_events skipped (validation failed: {e}): type={otype} mode={mode}")
            continue
        validated.append((ts, otype, mode))
    if not validated:
        return
    async with pool.acquire() as conn:
        await conn.executemany(
            "INSERT INTO override_events (ts, override_type, mode) VALUES ($1, $2, $3)",
            validated,
        )
    log.info(f"override_events: {len(validated)} start events written")


async def write_setpoint_changes(pool: asyncpg.Pool, ts: datetime) -> None:
    """Flush pending setpoint change events.

    These rows originate from the ESP32 reporting a configured-value change
    (firmware local override, manual HA switch toggle, etc.) — not from the
    dispatcher's own pushes (those write directly in tasks.py::setpoint_dispatcher
    with source='plan' | 'band'). Tagged source='esp32' to preserve provenance
    per SetpointSource literal in verdify_schemas/setpoint.py.
    """
    if not state.pending_setpoints:
        return
    changes = state.pending_setpoints.copy()
    state.pending_setpoints.clear()
    validated: list[tuple[datetime, str, float, str, datetime, str]] = []
    for param, val in changes:
        if param in BAND_DRIVEN_PARAMS:
            log.debug("setpoint_changes ignored dispatcher-owned ESP32 echo: %s=%s", param, val)
            continue
        try:
            SetpointChange(ts=ts, parameter=param, value=val, source="esp32", greenhouse_id=GREENHOUSE_ID)
        except ValidationError as e:
            log.error(f"setpoint_changes skipped (validation failed: {e}): param={param} value={val}")
            continue
        validated.append((ts, param, val, "esp32", ts, "observed"))
    if not validated:
        return
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO setpoint_changes
                (ts, parameter, value, source, confirmed_at, delivery_status)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            validated,
        )
    log.debug(f"setpoint_changes: {len(validated)} changes written")


async def write_diagnostics(pool: asyncpg.Pool, ts: datetime) -> None:
    """Write a diagnostics row."""
    d = state.diagnostics
    if not d:
        return
    try:
        diag = Diagnostics.model_validate({"ts": ts, "greenhouse_id": GREENHOUSE_ID, **d})
    except ValidationError as e:
        log.error(f"diagnostics row failed schema validation: {e}")
        return
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO diagnostics (
                   ts, wifi_rssi, heap_bytes, heap_min_free_kb, heap_largest_free_block_kb,
                   uptime_s, probe_health, reset_reason,
                   firmware_version, active_probe_count, relief_cycle_count, vent_latch_timer_s,
                   sealed_timer_s, vpd_watch_timer_s, mist_backoff_timer_s, vent_mist_assist_active,
                   effective_heat_target_f, effective_cool_stage2_delta_f,
                   effective_vpd_hysteresis_kpa, effective_dehum_aggressive_kpa,
                   controller_time_epoch, controller_local_hour, sntp_valid, sntp_miss_count,
                   last_sntp_sync_age_s
               )
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16,
                       $17, $18, $19, $20, $21, $22, $23, $24, $25)""",
            ts,
            diag.wifi_rssi,
            diag.heap_bytes,
            diag.heap_min_free_kb,
            diag.heap_largest_free_block_kb,
            diag.uptime_s,
            diag.probe_health,
            diag.reset_reason,
            diag.firmware_version,
            diag.active_probe_count,
            diag.relief_cycle_count,
            diag.vent_latch_timer_s,
            diag.sealed_timer_s,
            diag.vpd_watch_timer_s,
            diag.mist_backoff_timer_s,
            diag.vent_mist_assist_active,
            diag.effective_heat_target_f,
            diag.effective_cool_stage2_delta_f,
            diag.effective_vpd_hysteresis_kpa,
            diag.effective_dehum_aggressive_kpa,
            diag.controller_time_epoch,
            diag.controller_local_hour,
            diag.sntp_valid,
            diag.sntp_miss_count,
            diag.last_sntp_sync_age_s,
        )
    log.debug("diagnostics row written")
    # #113: fan-out the diagnostics row. Use the explicit column list above so the
    # subscriber's generic INSERT writes the same columns (greenhouse_id excluded —
    # the subscriber stamps its own).
    _fanout_publish(
        "diagnostics",
        {
            "ts": ts,
            "wifi_rssi": diag.wifi_rssi,
            "heap_bytes": diag.heap_bytes,
            "heap_min_free_kb": diag.heap_min_free_kb,
            "heap_largest_free_block_kb": diag.heap_largest_free_block_kb,
            "uptime_s": diag.uptime_s,
            "probe_health": diag.probe_health,
            "reset_reason": diag.reset_reason,
            "firmware_version": diag.firmware_version,
            "active_probe_count": diag.active_probe_count,
            "relief_cycle_count": diag.relief_cycle_count,
            "vent_latch_timer_s": diag.vent_latch_timer_s,
            "sealed_timer_s": diag.sealed_timer_s,
            "vpd_watch_timer_s": diag.vpd_watch_timer_s,
            "mist_backoff_timer_s": diag.mist_backoff_timer_s,
            "vent_mist_assist_active": diag.vent_mist_assist_active,
            "effective_heat_target_f": diag.effective_heat_target_f,
            "effective_cool_stage2_delta_f": diag.effective_cool_stage2_delta_f,
            "effective_vpd_hysteresis_kpa": diag.effective_vpd_hysteresis_kpa,
            "effective_dehum_aggressive_kpa": diag.effective_dehum_aggressive_kpa,
            "controller_time_epoch": diag.controller_time_epoch,
            "controller_local_hour": diag.controller_local_hour,
            "sntp_valid": diag.sntp_valid,
            "sntp_miss_count": diag.sntp_miss_count,
            "last_sntp_sync_age_s": diag.last_sntp_sync_age_s,
        },
    )


async def write_daily_summary(pool: asyncpg.Pool) -> None:
    """Snapshot daily accumulator values. Called at 00:05 each day.

    Two-writer contract for daily_summary (see tasks.py::daily_summary_live):
      - This function owns the midnight UPSERT of raw accumulators from the
        ESP32's cycle/runtime/water counters: cycles_*, runtime_*_min,
        runtime_mister_*_h, water_used_gal, mister_water_gal, dli_final.
      - `daily_summary_live` refreshes every 30 min with the live-computed
        climate rollups + stress_hours_* + compliance_pct + cost_* + dp_risk_*
        and also rewrites runtimes/cycles from equipment_state transitions.
    Column ownership overlaps on cycles/runtimes; `daily_summary_live`'s values
    win for the current day because it runs after this snapshot.
    """
    today = datetime.now(UTC).date()
    today_str = str(today)
    if state._daily_snapshot_date == today_str:
        return  # already done today

    d = state.daily
    if not d:
        log.warning("daily_summary: no accumulator data available yet, skipping")
        return

    water_total = state.climate.get("water_total_gal")
    mister_water = state.climate.get("mister_water_today")
    dli = state.climate.get("dli_today")

    # Validate the accumulated daily row through DailySummaryRow (range +
    # non-negative stress-hour invariants). The schema has extra="ignore" so
    # unrelated keys in state.daily (climate rollups computed elsewhere) are
    # dropped; out-of-range cycles/runtimes raise.
    try:
        DailySummaryRow.model_validate(
            {
                "date": today,
                **d,
                "water_used_gal": water_total,
                "mister_water_gal": mister_water,
                "dli_final": dli,
            }
        )
    except ValidationError as e:
        log.error(f"daily_summary row failed schema validation: {e}")
        return

    fairness = d.get("mister_fairness_overrides_today")
    zone_cycles = {
        "cycles_mister_south": d.get("cycles_mister_south"),
        "cycles_mister_west": d.get("cycles_mister_west"),
        "cycles_mister_center": d.get("cycles_mister_center"),
        "cycles_drip_wall": d.get("cycles_drip_wall"),
        "cycles_drip_center": d.get("cycles_drip_center"),
    }
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO daily_summary (
                date,
                cycles_fan1, cycles_fan2, cycles_heat1, cycles_heat2,
                cycles_fog, cycles_vent, cycles_dehum, cycles_safety_dehum,
                cycles_mister_south, cycles_mister_west, cycles_mister_center,
                cycles_drip_wall, cycles_drip_center,
                runtime_fan1_min, runtime_fan2_min, runtime_heat1_min, runtime_heat2_min,
                runtime_fog_min, runtime_vent_min,
                runtime_mister_south_h, runtime_mister_west_h, runtime_mister_center_h,
                water_used_gal, mister_water_gal, dli_final,
                mister_fairness_overrides_today
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9,
                $10, $11, $12, $13, $14, $15, $16, $17, $18,
                $19, $20, $21, $22, $23, $24, $25, $26, $27
            ) ON CONFLICT (date) DO UPDATE SET
                cycles_fan1 = EXCLUDED.cycles_fan1,
                cycles_fan2 = EXCLUDED.cycles_fan2,
                cycles_heat1 = EXCLUDED.cycles_heat1,
                cycles_heat2 = EXCLUDED.cycles_heat2,
                cycles_fog = EXCLUDED.cycles_fog,
                cycles_vent = EXCLUDED.cycles_vent,
                cycles_dehum = EXCLUDED.cycles_dehum,
                cycles_safety_dehum = EXCLUDED.cycles_safety_dehum,
                cycles_mister_south = EXCLUDED.cycles_mister_south,
                cycles_mister_west = EXCLUDED.cycles_mister_west,
                cycles_mister_center = EXCLUDED.cycles_mister_center,
                cycles_drip_wall = EXCLUDED.cycles_drip_wall,
                cycles_drip_center = EXCLUDED.cycles_drip_center,
                runtime_fan1_min = EXCLUDED.runtime_fan1_min,
                runtime_fan2_min = EXCLUDED.runtime_fan2_min,
                runtime_heat1_min = EXCLUDED.runtime_heat1_min,
                runtime_heat2_min = EXCLUDED.runtime_heat2_min,
                runtime_fog_min = EXCLUDED.runtime_fog_min,
                runtime_vent_min = EXCLUDED.runtime_vent_min,
                runtime_mister_south_h = EXCLUDED.runtime_mister_south_h,
                runtime_mister_west_h = EXCLUDED.runtime_mister_west_h,
                runtime_mister_center_h = EXCLUDED.runtime_mister_center_h,
                water_used_gal = EXCLUDED.water_used_gal,
                mister_water_gal = EXCLUDED.mister_water_gal,
                dli_final = EXCLUDED.dli_final,
                mister_fairness_overrides_today = EXCLUDED.mister_fairness_overrides_today,
                captured_at = NOW()
            """,
            today,
            int(d.get("cycles_fan1") or 0),
            int(d.get("cycles_fan2") or 0),
            int(d.get("cycles_heat1") or 0),
            int(d.get("cycles_heat2") or 0),
            int(d.get("cycles_fog") or 0),
            int(d.get("cycles_vent") or 0),
            int(d.get("cycles_dehum") or 0),
            int(d.get("cycles_safety_dehum") or 0),
            int(zone_cycles["cycles_mister_south"]) if zone_cycles["cycles_mister_south"] is not None else None,
            int(zone_cycles["cycles_mister_west"]) if zone_cycles["cycles_mister_west"] is not None else None,
            int(zone_cycles["cycles_mister_center"]) if zone_cycles["cycles_mister_center"] is not None else None,
            int(zone_cycles["cycles_drip_wall"]) if zone_cycles["cycles_drip_wall"] is not None else None,
            int(zone_cycles["cycles_drip_center"]) if zone_cycles["cycles_drip_center"] is not None else None,
            d.get("runtime_fan1_min"),
            d.get("runtime_fan2_min"),
            d.get("runtime_heat1_min"),
            d.get("runtime_heat2_min"),
            d.get("runtime_fog_min"),
            d.get("runtime_vent_min"),
            d.get("runtime_mister_south_h"),
            d.get("runtime_mister_west_h"),
            d.get("runtime_mister_center_h"),
            water_total,
            mister_water,
            dli,
            int(fairness) if fairness is not None else None,
        )
    state._daily_snapshot_date = today_str
    log.info(f"daily_summary written for {today}")


async def write_esp32_logs(pool: asyncpg.Pool) -> None:
    """Flush pending ESP32 log messages to esp32_logs table + Loki."""
    if not state.pending_logs:
        return
    logs = state.pending_logs.copy()
    state.pending_logs.clear()
    ts = datetime.now(UTC)

    # Validate each row through ESP32LogRow before the INSERT. Schema enforces
    # message min_length=1 — empty-after-ANSI-strip payloads get dropped here
    # instead of landing in the DB as blank rows.
    validated: list[tuple[datetime, str, str | None, str]] = []
    for lvl, tag, msg in logs:
        try:
            ESP32LogRow(ts=ts, level=lvl, tag=tag, message=msg)
        except ValidationError:
            continue
        validated.append((ts, lvl, tag, msg))
    if not validated:
        return

    # Write to DB
    async with pool.acquire() as conn:
        await conn.executemany(
            "INSERT INTO esp32_logs (ts, level, tag, message) VALUES ($1, $2, $3, $4)",
            validated,
        )

        # M4 / B9: MEASURED A/B instrumentation for esp32_logs re-enable.
        # esp32_logs forwarding was disabled at the firmware to protect heap
        # (commit 90bc358). We do NOT auto-re-enable it — that is a firmware OTA
        # decision. But whenever logs ARE flowing (a controlled operator
        # re-enable), this records the concurrent heap state alongside the log
        # batch size so the heap impact is MEASURED rather than guessed. The
        # paired log-quiet baseline is every diagnostics row where no log batch
        # flushed; comparing largest-free-block in the two regimes is the A/B.
        heap_now = await conn.fetchrow(
            "SELECT heap_largest_free_block_kb, heap_min_free_kb, heap_bytes FROM diagnostics ORDER BY ts DESC LIMIT 1"
        )
        if heap_now is not None:
            log.info(
                "esp32_logs A/B: flushed %d msgs; concurrent heap largest_free=%.1fkB min_free=%.1fkB "
                "(log-active sample — compare vs log-quiet diagnostics baseline before re-enabling)",
                len(validated),
                float(heap_now["heap_largest_free_block_kb"] or 0.0),
                float(heap_now["heap_min_free_kb"] or 0.0),
            )

    # Push to Loki (best-effort, don't block on failure)
    try:
        loki_lines = []
        ts_ns = str(int(ts.timestamp() * 1e9))
        for _ts, lvl, tag, msg in validated:
            loki_lines.append([ts_ns, f"[{lvl}] [{tag or 'esp32'}] {msg}"])
        payload = json.dumps(
            {
                "streams": [
                    {
                        "stream": {"job": "esp32", "host": "greenhouse"},
                        "values": loki_lines,
                    }
                ]
            }
        ).encode()
        if LOKI_URL:
            req = urllib.request.Request(LOKI_URL, data=payload, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=2)
    except Exception:
        pass  # Don't fail ingestor if Loki is down

    log.debug(f"esp32_logs: {len(validated)} messages written")


def on_log_message(msg) -> None:
    """Callback for ESP32 log messages via aioesphomeapi."""
    import re

    level = LOG_LEVEL_MAP.get(msg.level, "UNKNOWN")
    tag = msg.tag if hasattr(msg, "tag") else None
    raw = msg.message if hasattr(msg, "message") else str(msg)
    # Decode bytes if needed, strip ANSI escape codes
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    if isinstance(tag, bytes):
        tag = tag.decode("utf-8", errors="replace")
    message = re.sub(r"\x1b\[[0-9;]*m", "", raw)  # Strip ANSI colors
    if msg.level <= ESP32_LOG_LEVEL:
        state.pending_logs.append((level, tag, message))


# ──────────────────────────────────────────────────────────────
# Setpoint validation — reject boot-time defaults and implausible values
# ──────────────────────────────────────────────────────────────
_SETPOINT_RANGES = {
    "safety_max": (70, 120),
    "safety_min": (30, 60),
    "safety_vpd_max": (1.5, 5.0),
    "safety_vpd_min": (0.05, 1.0),
    "temp_high": (50, 110),
    "temp_low": (35, 90),
    "vpd_high": (0.3, 4.0),
    "vpd_low": (0.1, 3.0),
}

FORCED_ON_SWITCH_PARAMS = frozenset({"sw_fsm_controller_enabled"})

# Boot window: suppress ESP32 setpoint reports for 60s after connect
# to prevent firmware defaults from polluting the DB
_BOOT_WINDOW_S = 60

# ESPHome number entities often echo a direct push on their next state
# publish, which can arrive well after the command returns. Suppress those
# delayed echoes so setpoint_changes does not notify-listener push them back.
_PUSH_ECHO_SUPPRESS_S = 900


def _same_pushed_value(param: str, value: float) -> bool:
    pushed_value = shared.recently_pushed_values.get(param)
    if pushed_value is None:
        return False
    return abs(pushed_value - value) / max(abs(value), 1e-3) < 0.01


# F10 (Sprint 24-alignment): firmware emits mister_state + mister_selected_zone
# as numeric template sensors (state_class=measurement), not text. Map the int
# codes to human-readable names before routing to system_state so Grafana
# and the planner see "S1"/"south" not "1". Unknown codes fall through as
# "unknown(N)" so drift is visible.
# Source: firmware/greenhouse/controls.yaml (state machine) + greenhouse_types.h
# (MistStage enum).
_MISTER_STATE_NAMES = {
    0: "WATCH",
    1: "S1",
    2: "S2",
    3: "FOG",
}
_MISTER_ZONE_NAMES = {
    0: "none",
    1: "south",
    2: "west",
    3: "center",
}
_NUMERIC_STATE_DECODERS = {
    "mister_state": _MISTER_STATE_NAMES,
    "mister_zone": _MISTER_ZONE_NAMES,
}


def _decode_numeric_state(entity_name: str, val: float) -> str:
    """F10: translate a numeric state-machine code to a human label."""
    decoder = _NUMERIC_STATE_DECODERS.get(entity_name)
    if decoder is None:
        return str(val)
    code = int(val)
    return decoder.get(code, f"unknown({code})")


def _accept_setpoint(param: str, value: float) -> bool:
    """Return True if this setpoint value should be written to the DB."""
    import time as _time

    # Boot window: suppress ESP32-reported setpoints for first 60s
    if shared.esp32_connected_at > 0:
        elapsed = _time.time() - shared.esp32_connected_at
        if elapsed < _BOOT_WINDOW_S:
            log.debug("Boot window (%ds): suppressing %s=%.2f", int(elapsed), param, value)
            return False

    # Range validation: reject implausible values
    if param in _SETPOINT_RANGES:
        lo, hi = _SETPOINT_RANGES[param]
        if value < lo or value > hi:
            log.warning("Rejecting implausible setpoint %s=%.2f (valid range %.1f-%.1f)", param, value, lo, hi)
            return False

    return True


def _accept_outbound_setpoint(param: str, value: float) -> bool:
    """Return True if a DB-origin setpoint is inside registry bounds."""
    if param in FORCED_ON_SWITCH_PARAMS and value < 0.5:
        log.warning("Rejecting outbound OFF request %s=%.3f; unified band-first controller is locked ON", param, value)
        return False
    spec = get_tunable(param)
    if spec is None or spec.kind != "numeric":
        return True
    if spec.min is not None and value < spec.min:
        log.warning("Rejecting outbound setpoint %s=%.3f below registry min %.3f", param, value, spec.min)
        return False
    if spec.max is not None and value > spec.max:
        log.warning("Rejecting outbound setpoint %s=%.3f above registry max %.3f", param, value, spec.max)
        return False
    return True


INTEGER_DIAGNOSTIC_COLUMNS = {
    "controller_time_epoch",
    "controller_local_hour",
    "sntp_valid",
    "sntp_miss_count",
    "last_sntp_sync_age_s",
}


def _coerce_integer_diagnostic(col: str, value: Any) -> int | None:
    try:
        decimal_value = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        log.warning("diagnostic rejected non-integer: %s=%r", col, value)
        return None
    if decimal_value != decimal_value.to_integral_value():
        log.warning("diagnostic rejected fractional integer: %s=%r", col, value)
        return None
    parsed = int(decimal_value)
    if parsed < 0:
        log.warning("diagnostic rejected negative integer: %s=%r", col, value)
        return None
    return parsed


def _record_diagnostic(obj_id: str, value: Any) -> bool:
    col = DIAGNOSTIC_MAP.get(obj_id)
    if not col:
        return False
    if col in INTEGER_DIAGNOSTIC_COLUMNS:
        parsed = _coerce_integer_diagnostic(col, value)
        if parsed is None:
            return True
        state.diagnostics[col] = parsed
    else:
        state.diagnostics[col] = value
    return True


def _record_cfg_readback(obj_id: str, value: Any) -> bool:
    """Record a firmware cfg_* readback if this entity is part of that contract."""
    cfg_param = CFG_READBACK_MAP.get(obj_id)
    if not cfg_param:
        return False

    try:
        val = float(value)
    except (TypeError, ValueError):
        log.warning("cfg_readback rejected non-numeric: %s=%r", obj_id, value)
        return True
    if math.isnan(val):
        return True

    if cfg_param in _SETPOINT_RANGES:
        lo, hi = _SETPOINT_RANGES[cfg_param]
        if val < lo or val > hi:
            log.warning(
                "cfg_readback rejected out-of-range: %s=%.3f (valid %s-%s)",
                cfg_param,
                val,
                lo,
                hi,
            )
            return True

    prev = shared.cfg_readback.get(cfg_param)
    state.cfg_readback[cfg_param] = val
    shared.cfg_readback[cfg_param] = val
    if prev is not None and not math.isclose(prev, val, rel_tol=0.01, abs_tol=0.001):
        shared.force_setpoint_push.set()
    if cfg_param in FORCED_ON_SWITCH_PARAMS and val < 0.5:
        eid = SWITCH_TO_ENTITY.get(cfg_param)
        if eid:
            log.warning(
                "Controller guardrail: cfg readback has %s=%.0f; immediate repair push queued",
                cfg_param,
                val,
            )
            try:
                asyncio.get_running_loop().create_task(push_to_esp32([(eid, 1.0, "switch")]))
            except RuntimeError:
                shared.force_setpoint_push.set()
    return True


def _mirror_irrigation_number_readback(param: str, value: Any) -> None:
    """Treat ESP32 irrigation number-state reports as cfg readbacks.

    The irrigation schedule knobs are persisted firmware globals exposed both
    as writable Number entities and cfg_* diagnostic template sensors. Mirroring
    the ESP32 Number state keeps setpoint_snapshot fresh when a cfg_* template
    sensor fails to republish after a reconnect.
    """
    if param not in IRRIGATION_SCHEDULE_PARAMS:
        return
    try:
        val = float(value)
    except (TypeError, ValueError):
        log.warning("irrigation number readback rejected non-numeric: %s=%r", param, value)
        return
    if math.isnan(val):
        return
    prev = shared.cfg_readback.get(param)
    state.cfg_readback[param] = val
    shared.cfg_readback[param] = val
    if prev is not None and not math.isclose(prev, val, rel_tol=0.01, abs_tol=0.001):
        shared.force_setpoint_push.set()


# ──────────────────────────────────────────────────────────────
# ESP32 callbacks
# ──────────────────────────────────────────────────────────────
def on_state_change(entity_state) -> None:
    """Called by aioesphomeapi on any entity state change."""
    key = entity_state.key
    obj_id = state.key_to_object_id.get(key)
    etype = state.key_to_type.get(key)
    if obj_id is None:
        return

    if etype == "sensor":
        val = entity_state.state
        if val is None or (isinstance(val, float) and math.isnan(val)):
            return

        if _record_cfg_readback(obj_id, val):
            return

        if _record_climate_sensor(obj_id, val):
            return

        if _record_diagnostic(obj_id, val):
            return

        col = DAILY_ACCUM_MAP.get(obj_id)
        if col:
            state.daily[col] = val
            return

        param = SETPOINT_MAP.get(obj_id)
        if param:
            if not _accept_setpoint(param, val):
                return
            _mirror_irrigation_number_readback(param, val)
            old = state.setpoints.get(param)
            state.setpoints[param] = val
            if old != val:
                state.pending_setpoints.append((param, val))
            return

        # F10: numeric state-machine template sensors (mister_state,
        # mister_selected_zone) route to system_state as decoded strings.
        # These are diagnostic signals the planner uses to correlate VPD
        # outcomes with which zone was firing; without this route they
        # go stale in v_sensor_staleness within minutes.
        entity = STATE_MAP.get(obj_id)
        if entity:
            decoded = _decode_numeric_state(entity, val)
            old = state.system.get(entity)
            state.system[entity] = decoded
            if old != decoded:
                state.pending_states.append((entity, decoded))
                log.info(f"state: {entity} → {decoded}")
            return

    elif etype == "binary":
        val = entity_state.state
        equip = EQUIPMENT_BINARY_MAP.get(obj_id)
        if equip:
            old = state.equipment.get(equip)
            state.equipment[equip] = val
            if old != val:
                state.pending_equipment.append((equip, val))
            return

    elif etype == "switch":
        val = entity_state.state
        if _record_cfg_readback(obj_id, 1.0 if val else 0.0):
            return

        equip = EQUIPMENT_SWITCH_MAP.get(obj_id)
        if equip:
            old = state.equipment.get(equip)
            state.equipment[equip] = val
            if old != val:
                state.pending_equipment.append((equip, val))
            return

    elif etype == "text":
        val = entity_state.state
        if not val:
            return

        if _record_diagnostic(obj_id, val):
            return

        entity = STATE_MAP.get(obj_id)
        if entity:
            old = state.system.get(entity)
            state.system[entity] = val
            force_refresh = entity in {"gl_main_state", "gl_main_reason", "gl_grow_state", "gl_grow_reason"}
            if old != val or force_refresh:
                state.pending_states.append((entity, val))
                if old != val:
                    log.info(f"state: {entity} → {val}")
                # OBS-1e (Sprint 16): active_overrides is a comma-separated
                # list of firmware flags. Diff against last-seen set and
                # enqueue one override_events row per newly-started flag.
                if entity == "overrides_active":
                    current = _parse_override_set(val)
                    started = current - state.last_override_set
                    if started:
                        mode_str = state.system.get("greenhouse_state")
                        for otype in sorted(started):
                            state.pending_override_events.append((otype, mode_str))
                    state.last_override_set = current
            return

    elif etype == "number":
        val = entity_state.state
        if val is None or (isinstance(val, float) and math.isnan(val)):
            return

        param = SETPOINT_MAP.get(obj_id)
        if param:
            if not _accept_setpoint(param, val):
                return
            _mirror_irrigation_number_readback(param, val)
            old = state.setpoints.get(param)
            state.setpoints[param] = val
            if old != val:
                # Suppress same-value echoes from delayed ESPHome number-state publishes.
                import time as _time

                pushed_at = shared.recently_pushed.get(param, 0)
                if _time.time() - pushed_at < _PUSH_ECHO_SUPPRESS_S and _same_pushed_value(param, val):
                    return
                state.pending_setpoints.append((param, val))
            return


# ──────────────────────────────────────────────────────────────
# Flush loop
# ──────────────────────────────────────────────────────────────
async def flush_loop(pool: asyncpg.Pool) -> None:
    """Periodically flush buffered data to the database."""
    last_climate = 0.0
    last_climate_action_log = 0.0
    last_diag = 0.0

    while True:
        await asyncio.sleep(5)
        now = asyncio.get_event_loop().time()
        ts = datetime.now(UTC)

        # Climate row every 60s
        if now - last_climate >= CLIMATE_FLUSH_INTERVAL:
            try:
                await write_climate(pool, ts)
                if now - last_climate_action_log >= CLIMATE_ACTION_LOG_INTERVAL:
                    if await write_climate_action_log(pool, ts):
                        last_climate_action_log = now
                last_climate = now
            except Exception as e:
                log.error(f"climate write error: {e}")

        # Diagnostics every 60s
        if now - last_diag >= DIAG_FLUSH_INTERVAL:
            try:
                await write_diagnostics(pool, ts)
                last_diag = now
            except Exception as e:
                log.error(f"diagnostics write error: {e}")

            # Setpoint snapshot: write ESP32 configured values (cfg_* readback)
            # FW-4 (Sprint 20): same pass also closes the confirmation loop —
            # any setpoint_changes row whose value matches the cfg readback
            # within the 1% dispatcher dead-band gets confirmed_at = now().
            # Backfill a short historical window too: when firmware gains a
            # new cfg_* readback, otherwise-matching rows pushed before that
            # sensor existed should not stay permanently "unconfirmed".
            # Rows that never match stay NULL; the setpoint_confirmation_monitor
            # task in tasks.py (FB-1) alerts after 5 min.
            if state.cfg_readback:
                try:
                    snapshot_rows: list[tuple[datetime, str, float]] = []
                    for param, val in state.cfg_readback.items():
                        try:
                            SetpointSnapshot(ts=ts, parameter=param, value=val, greenhouse_id=GREENHOUSE_ID)
                        except ValidationError as e:
                            log.error(f"setpoint_snapshot skipped (validation failed: {e}): param={param} value={val}")
                            continue
                        snapshot_rows.append((ts, param, val))
                    async with pool.acquire() as conn:
                        await conn.executemany(
                            "INSERT INTO setpoint_snapshot (ts, parameter, value) VALUES ($1, $2, $3)",
                            snapshot_rows,
                        )
                        # #113: fan-out each setpoint snapshot to the bus.
                        for snap_ts, param, val in snapshot_rows:
                            _fanout_publish(
                                "setpoint_snapshot",
                                {"ts": snap_ts, "parameter": param, "value": val},
                            )
                        # FW-4 confirmation loop — one UPDATE per readback param
                        # (tiny batch; no worse than the INSERT above).
                        # Dead-band: abs(sc.value - cfg_val) / max(|cfg_val|, 1e-3) < 0.01
                        # — same math as ingestor.tasks._should_skip. Avoid
                        # confirming through a later differing request for the
                        # same greenhouse/parameter; those older rows were
                        # superseded, not proven by the current readback.
                        await conn.executemany(
                            """
                            UPDATE setpoint_changes sc
                               SET confirmed_at = now(),
                                   delivery_status = 'confirmed'
                             WHERE sc.parameter = $1
                               AND sc.confirmed_at IS NULL
                               AND sc.ts > now() - interval '7 days'
                               AND (
                                   abs(sc.value - $2::double precision)
                                         / greatest(abs($2::double precision), 1e-3) < 0.01
                                   OR abs(sc.value - $2::double precision) <= $3::double precision
                               )
                               AND NOT EXISTS (
                                   SELECT 1
                                     FROM setpoint_changes newer
                                   WHERE newer.parameter = sc.parameter
                                     AND COALESCE(newer.greenhouse_id, '') = COALESCE(sc.greenhouse_id, '')
                                     AND COALESCE(newer.source, '') <> 'esp32'
                                     AND newer.ts > sc.ts
                                     AND NOT (
                                         abs(newer.value - $2::double precision)
                                               / greatest(abs($2::double precision), 1e-3) < 0.01
                                         OR abs(newer.value - $2::double precision) <= $3::double precision
                                     )
                               )
                            """,
                            [(param, val, readback_abs_tolerance(param)) for param, val in state.cfg_readback.items()],
                        )
                except Exception as e:
                    log.error(f"setpoint_snapshot write error: {e}")

        # Equipment events (flush immediately)
        if state.pending_equipment:
            try:
                await write_equipment_events(pool, ts)
            except Exception as e:
                log.error(f"equipment_state write error: {e}")

        # State transitions (flush immediately)
        if state.pending_states:
            try:
                changed_entities = await write_state_transitions(pool, ts)
                if changed_entities & CLIMATE_ACTION_LOG_ENTITIES and last_climate_action_log != now:
                    if await write_climate_action_log(pool, ts):
                        last_climate_action_log = now
            except Exception as e:
                log.error(f"system_state write error: {e}")

        # OBS-1e override events (Sprint 16) — flush immediately
        if state.pending_override_events:
            try:
                await write_override_events(pool, ts)
            except Exception as e:
                log.error(f"override_events write error: {e}")

        # Setpoint changes (flush immediately)
        if state.pending_setpoints:
            try:
                await write_setpoint_changes(pool, ts)
            except Exception as e:
                log.error(f"setpoint_changes write error: {e}")

        # ESP32 logs (flush every 10s)
        if state.pending_logs:
            try:
                await write_esp32_logs(pool)
            except Exception as e:
                log.error(f"esp32_logs write error: {e}")

        # Daily summary: trigger at 00:05 local time
        now_mt = datetime.now()
        if now_mt.hour == 0 and now_mt.minute == 5:
            try:
                await write_daily_summary(pool)
            except Exception as e:
                log.error(f"daily_summary write error: {e}")


# ──────────────────────────────────────────────────────────────
# ESP32 connection loop
# ──────────────────────────────────────────────────────────────
async def esp32_loop(pool: asyncpg.Pool = None) -> None:
    """Connect to ESP32 and subscribe to all entity states.

    Uses two mechanisms to detect dead connections:
    1. on_stop callback from connect() — fires when library detects disconnect
    2. Periodic keepalive ping via device_info() every 60s — catches silent TCP death

    On disconnect, logs the gap duration and reconnects automatically.
    """
    last_disconnected_at: datetime | None = None
    # M6 / B11: in-process disconnects set last_disconnected_at, but a restart of
    # the ingestor PROCESS itself (systemd bounce, crash, deploy) loses that state
    # — the very first connect of a new process has last_disconnected_at=None, so
    # the gap between the last telemetry row written before the restart and the
    # first row after was silently NOT recorded as a data_gap (the under-report
    # bug). We detect the first connect of this process and reconstruct the gap
    # from the last persisted telemetry timestamp in the DB.
    first_connect = True

    while True:
        log.info(f"Connecting to ESP32 at {ESP32_HOST}:{ESP32_PORT}...")
        client = APIClient(
            address=ESP32_HOST,
            port=ESP32_PORT,
            password="",
            noise_psk=ESP32_API_KEY,
        )

        # Event that fires when the connection drops (set by on_stop callback or ping failure)
        connection_lost = asyncio.Event()
        disconnected_at: datetime | None = None

        async def on_stop(expected_disconnect: bool) -> None:
            """Called by aioesphomeapi when connection drops."""
            nonlocal disconnected_at
            disconnected_at = datetime.now(UTC)
            if expected_disconnect:
                log.info("ESP32 disconnected (expected)")
            else:
                log.warning("ESP32 connection lost (unexpected)")
            connection_lost.set()

        try:
            await client.connect(on_stop=on_stop, login=True)
            connected_at = datetime.now(UTC)

            # Log reconnect gap and backfill if applicable. Use the actual
            # disconnect timestamp, not the previous connect timestamp, so
            # data_gaps represents missing telemetry rather than uptime.
            if last_disconnected_at:
                gap = (connected_at - last_disconnected_at).total_seconds()
                log.info(f"Connected to ESP32 (gap: {gap:.0f}s since disconnect)")
                if gap > 120:  # >2 min gap — record and backfill
                    try:
                        await backfill_gap(pool, last_disconnected_at, connected_at)
                    except Exception as e:
                        log.error(f"Gap backfill failed: {e}")
            elif first_connect:
                # M6 / B11: first connect of this process. last_disconnected_at is
                # None not because there was no gap, but because the prior
                # disconnect happened in a previous process (restart). Reconstruct
                # the restart gap from the last persisted telemetry timestamp so it
                # is no longer under-reported.
                try:
                    last_telemetry_ts = await _last_telemetry_ts(pool)
                except Exception as e:
                    last_telemetry_ts = None
                    log.error(f"Restart-gap lookup failed: {e}")
                if last_telemetry_ts is not None:
                    gap = (connected_at - last_telemetry_ts).total_seconds()
                    if gap > 120:
                        log.info(f"Connected to ESP32 (restart gap: {gap:.0f}s since last telemetry)")
                        try:
                            await backfill_gap(pool, last_telemetry_ts, connected_at, reason="ingestor_process_restart")
                        except Exception as e:
                            log.error(f"Restart-gap backfill failed: {e}")
                    else:
                        log.info("Connected to ESP32")
                else:
                    log.info("Connected to ESP32 (no prior telemetry; first run)")
            else:
                log.info("Connected to ESP32")
            last_disconnected_at = None
            first_connect = False

            # Enumerate entities to build key→object_id map
            entities, services = await client.list_entities_services()
            for e in entities:
                obj_id = e.object_id
                key = e.key
                state.key_to_object_id[key] = obj_id
                if isinstance(e, SensorInfo):
                    state.key_to_type[key] = "sensor"
                elif isinstance(e, BinarySensorInfo):
                    state.key_to_type[key] = "binary"
                elif isinstance(e, TextSensorInfo):
                    state.key_to_type[key] = "text"
                elif isinstance(e, NumberInfo):
                    state.key_to_type[key] = "number"
                elif isinstance(e, SwitchInfo):
                    state.key_to_type[key] = "switch"

            log.info(f"Enumerated {len(entities)} entities")

            # Share client reference for dispatcher push (U2)
            shared.esp32["client"] = client
            shared.esp32["keys"] = {obj_id: key for key, obj_id in state.key_to_object_id.items()}
            log.info("ESP32 client shared: %d entity keys for direct push", len(shared.esp32["keys"]))

            # Signal dispatcher to do a full re-push (clears _last_pushed cache)
            import time as _time_mod

            shared.force_setpoint_push.set()
            shared.esp32_connected_at = _time_mod.time()
            log.info("Force-push flag set — dispatcher will re-push all setpoints")
            if pool is not None:
                try:
                    await refresh_latest_occupancy_state(pool, "esp32_reconnect")
                except Exception as e:
                    log.warning("Occupancy reconnect refresh failed: %s", e)

            tracked = sum(
                1
                for obj_id in state.key_to_object_id.values()
                if obj_id in CLIMATE_MAP
                or obj_id in ESPHOME_FEEDBACK_MAP
                or obj_id in EQUIPMENT_BINARY_MAP
                or obj_id in EQUIPMENT_SWITCH_MAP
                or obj_id in STATE_MAP
                or obj_id in SETPOINT_MAP
                or obj_id in DIAGNOSTIC_MAP
                or obj_id in DAILY_ACCUM_MAP
                or obj_id in CFG_READBACK_MAP
            )
            log.info(f"Tracking {tracked} entities across all maps (incl {len(CFG_READBACK_MAP)} cfg readback)")

            # Subscribe to state changes
            client.subscribe_states(on_state_change)

            # Keep ESP32 log streaming opt-in. Heap pressure is covered by
            # binary sensors and diagnostics; a live API log stream costs heap.
            if ESP32_LOG_LEVEL != LogLevel.LOG_LEVEL_NONE:
                client.subscribe_logs(on_log_message, log_level=ESP32_LOG_LEVEL)
                log.info("Subscribed to ESP32 logs (%s+)", ESP32_LOG_LEVEL_NAME)
            else:
                log.info("ESP32 log subscription disabled")

            # Immediate setpoint re-push after reconnect (don't wait for 300s cycle)
            try:
                from tasks import setpoint_dispatcher

                await asyncio.sleep(2)
                if shared.setpoint_dispatch_in_progress:
                    log.info("Post-reconnect setpoint dispatch skipped; dispatcher already running")
                else:
                    shared.setpoint_dispatch_in_progress = True
                    try:
                        await setpoint_dispatcher(pool)
                    finally:
                        shared.setpoint_dispatch_in_progress = False
                    log.info("Post-reconnect setpoint dispatch complete")
            except Exception as e:
                log.error(f"Post-reconnect dispatch failed: {e}")

            # Keepalive loop: ping every 60s via device_info()
            # Also watches for on_stop callback via connection_lost event
            while not connection_lost.is_set():
                try:
                    # Wait up to 60s — if connection_lost fires, we break immediately
                    await asyncio.wait_for(connection_lost.wait(), timeout=60.0)
                    # If we get here, connection_lost was set
                    break
                except TimeoutError:
                    # 60s passed without disconnect — send keepalive ping
                    try:
                        await asyncio.wait_for(client.device_info(), timeout=10.0)
                    except (TimeoutError, Exception) as ping_err:
                        log.warning(f"Keepalive ping failed: {ping_err}")
                        if disconnected_at is None:
                            disconnected_at = datetime.now(UTC)
                        connection_lost.set()
                        break

            log.warning("Connection lost — will reconnect")
            last_disconnected_at = disconnected_at or datetime.now(UTC)
            shared.esp32["client"] = None

        except APIConnectionError as e:
            log.warning(f"ESP32 connection error: {e}. Reconnecting in 30s...")
            if last_disconnected_at is None:
                last_disconnected_at = datetime.now(UTC)
            await asyncio.sleep(30)
        except Exception as e:
            log.error(f"Unexpected error: {e}. Reconnecting in 30s...")
            if last_disconnected_at is None:
                last_disconnected_at = datetime.now(UTC)
            await asyncio.sleep(30)
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass


# ──────────────────────────────────────────────────────────────
# Task loop — periodic background tasks (replaces 10 cron jobs)
# ──────────────────────────────────────────────────────────────
async def task_loop(pool: asyncpg.Pool) -> None:
    """Run periodic tasks on defined intervals."""
    task_timeouts = {
        # Reconnect reconciles can push 25+ values through heap-safe ESPHome
        # pacing, which intentionally exceeds the generic 120s watchdog.
        "setpoint_dispatch": 300,
    }
    TASKS = [
        # (name, interval_seconds, coroutine_factory)
        ("water_flowing", 60, water_flowing_sync),
        ("matview_refresh", 300, matview_refresh),
        ("shelly_sync", 300, shelly_sync),
        ("tempest_sync", 300, tempest_sync),
        ("ha_sensor_sync", 300, ha_sensor_sync),
        ("alert_monitor", 300, alert_monitor),
        ("planner_memory_ingest", 300, planner_memory_ingest_sync),
        # reactive_planner removed in Sprint 5 P6 — replaced by forecast deviation monitor
        ("setpoint_dispatch", 300, setpoint_dispatcher),
        ("setpoint_confirmation", 300, setpoint_confirmation_monitor),
        ("forecast_sync", 3600, forecast_sync),
        ("forecast_actions", 900, forecast_action_engine),
        ("deviation_check", 900, forecast_deviation_check),
        ("daily_summary_live", 1800, daily_summary_live),
        ("grow_light_daily", 86400, grow_light_daily),
        ("planning_heartbeat", 60, planning_heartbeat),
        # 60s poll; guards on time-of-day (only fires in 00:05-00:10 MDT window,
        # dedup by date). Sprint 24.7 ops stopgap — retires when Sprint 25
        # alert_monitor rule 7 rewrite ships.
        ("midnight_watch", 60, midnight_watch),
        ("slack_operator_briefs", 60, slack_operator_briefs),
        # Public inference-infra proof data. Kept after greenhouse-critical
        # tasks and sampled at 5m cadence so exporter stalls cannot starve
        # dispatch, alerts, or planner heartbeat.
        ("gpu_power_sync", 300, gpu_power_sync),
        ("infra_cpu_sync", 300, infra_cpu_sync),
    ]
    last_run: dict[str, float] = {name: 0.0 for name, _, _ in TASKS}

    # Stagger startup: wait 30s for ESP32 connection to establish first
    await asyncio.sleep(30)
    log.info("Task loop started: %d tasks registered", len(TASKS))

    while True:
        await asyncio.sleep(10)
        now = asyncio.get_event_loop().time()

        for name, interval, coro_fn in TASKS:
            if now - last_run[name] >= interval:
                last_run[name] = now
                if name == "setpoint_dispatch" and shared.setpoint_dispatch_in_progress:
                    log.info("Task setpoint_dispatch skipped; dispatcher already running")
                    continue
                if name == "setpoint_dispatch":
                    shared.setpoint_dispatch_in_progress = True
                try:
                    timeout_s = task_timeouts.get(name, 120)
                    await asyncio.wait_for(coro_fn(pool), timeout=timeout_s)
                except TimeoutError:
                    log.error("Task %s timed out (%ss)", name, timeout_s)
                except Exception as e:
                    log.error("Task %s failed: %s", name, e)
                finally:
                    if name == "setpoint_dispatch":
                        shared.setpoint_dispatch_in_progress = False


# ──────────────────────────────────────────────────────────────
# MQTT fan-out SUBSCRIBE mode (#114) — dev/stage ingest FROM prod's bus
# ──────────────────────────────────────────────────────────────
# Parse-time guard: timestamps arrive as ISO-8601 strings over MQTT.
def _coerce_fanout_value(key: str, val: Any) -> Any:
    if key.endswith("ts") or key == "ts":
        if isinstance(val, str):
            return datetime.fromisoformat(val)
    return val


async def write_fanout_row(pool: asyncpg.Pool, table: str, greenhouse_id: str, row: dict[str, Any]) -> None:
    """Write one fan-out row into THIS env's DB (#114 subscribe mode).

    The publisher already validated + persisted the row in prod; here we mirror
    it into the local per-env DB with a generic column-driven INSERT (same shape
    as write_climate). greenhouse_id comes from the envelope, not the row, so the
    subscriber stamps it consistently. NEVER touches any device — subscribe mode
    carries no ESP32 client.
    """
    cols = [c for c in row if c != "greenhouse_id"]
    if not cols:
        return
    values = [_coerce_fanout_value(c, row[c]) for c in cols]
    # All five fan-out tables carry greenhouse_id (migration 075). Stamp it.
    all_cols = [*cols, "greenhouse_id"]
    all_values = [*values, greenhouse_id]
    cols_sql = ", ".join(all_cols)
    placeholders = ", ".join(f"${i + 1}" for i in range(len(all_values)))
    async with pool.acquire() as conn:
        await conn.execute(
            f"INSERT INTO {table} ({cols_sql}) VALUES ({placeholders})",
            *all_values,
        )
    log.debug("fan-out subscribe: wrote %s row (%d cols) ghid=%s", table, len(cols), greenhouse_id)


async def mqtt_subscribe_loop(pool: asyncpg.Pool) -> None:
    """Subscribe to prod's fan-out bus and mirror rows into the local DB (#114).

    dev/stage only. No ESP32, no Home Assistant, no occupancy bridge — this is
    the entire ingest path for a subscriber env. Device writes are independently
    barred by the #79 gate (VERDIFY_DEVICE_WRITE_ENABLED), which dev/stage leave
    at 0; subscribe mode additionally carries no client capable of a write.
    """
    import paho.mqtt.client as paho_mqtt

    from config import (
        FANOUT_MQTT_HOST,
        FANOUT_MQTT_PASS,
        FANOUT_MQTT_PORT,
        FANOUT_MQTT_USER,
    )

    topic_filter = subscribe_topic_filter()
    event_loop = asyncio.get_event_loop()

    async def _persist(table: str, ghid: str, row: dict[str, Any]) -> None:
        try:
            await write_fanout_row(pool, table, ghid, row)
        except Exception as e:  # noqa: BLE001
            log.error("fan-out subscribe write failed (%s): %s", table, e)

    def on_connect(client, userdata, flags, rc):
        if rc == 0:
            client.subscribe(topic_filter, qos=0)
            log.info("fan-out subscribe: connected, subscribed to %s", topic_filter)
        else:
            log.error("fan-out subscribe: connect failed rc=%d", rc)

    def on_message(client, userdata, msg):
        try:
            table, ghid, row = decode_payload(msg.payload.decode(errors="replace"))
        except (ValueError, json.JSONDecodeError) as e:
            log.warning("fan-out subscribe: dropping malformed payload: %s", e)
            return
        asyncio.run_coroutine_threadsafe(_persist(table, ghid, row), event_loop)

    client = paho_mqtt.Client(client_id="verdify-fanout-sub")
    client.on_connect = on_connect
    client.on_message = on_message
    if FANOUT_MQTT_USER:
        client.username_pw_set(FANOUT_MQTT_USER, FANOUT_MQTT_PASS)

    while True:
        try:
            client.connect(FANOUT_MQTT_HOST, FANOUT_MQTT_PORT, 60)
            client.loop_start()
            log.info("fan-out subscribe: connected to %s:%d", FANOUT_MQTT_HOST, FANOUT_MQTT_PORT)
            while True:
                await asyncio.sleep(60)
                if not client.is_connected():
                    log.warning("fan-out subscribe: disconnected — reconnecting")
                    client.reconnect()
        except Exception as e:  # noqa: BLE001
            log.error("fan-out subscribe: %s — retry in 30s", e)
            try:
                client.loop_stop()
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(30)


# ──────────────────────────────────────────────────────────────
# MQTT loop — occupancy from Frigate/Sentinel + optional feedback sensors
# ──────────────────────────────────────────────────────────────
async def mqtt_loop(pool: asyncpg.Pool) -> None:
    """Subscribe to MQTT for occupancy and optional irrigation feedback."""
    from config import MQTT_HOST, MQTT_PASS, MQTT_PORT, MQTT_USER

    OCCUPANCY_TOPIC = "sentinel/occupancy/greenhouse_zone"

    event_loop = asyncio.get_event_loop()

    async def _write_occupancy(val: str) -> None:
        await sync_occupancy_state(pool, val == "occupied", "mqtt")

    def on_connect(client, userdata, flags, rc):
        if rc == 0:
            topics = [(OCCUPANCY_TOPIC, 0), *((topic, 0) for topic in MQTT_FEEDBACK_MAP)]
            client.subscribe(topics)
            log.info("MQTT: subscribed to %s + %d feedback topic(s)", OCCUPANCY_TOPIC, len(MQTT_FEEDBACK_MAP))
        else:
            log.error("MQTT: connect failed rc=%d", rc)

    def on_message(client, userdata, msg):
        topic = msg.topic
        if topic in MQTT_FEEDBACK_MAP:
            if msg.retain:
                log.debug("MQTT feedback retained message ignored: %s", topic)
                return
            payload = msg.payload.decode(errors="replace").strip()
            event_loop.call_soon_threadsafe(_record_mqtt_feedback, topic, payload)
            return

        payload = msg.payload.decode().strip().upper()
        occupied = payload == "ON"
        val = "occupied" if occupied else "empty"
        log.info("Occupancy: %s (via MQTT)", val)
        asyncio.run_coroutine_threadsafe(_write_occupancy(val), event_loop)

    client = paho_mqtt.Client(client_id="verdify-ingestor-occupancy")
    client.on_connect = on_connect
    client.on_message = on_message
    client.username_pw_set(MQTT_USER, MQTT_PASS)

    while True:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, 60)
            client.loop_start()
            log.info("MQTT: connected to %s:%d", MQTT_HOST, MQTT_PORT)
            while True:
                await asyncio.sleep(60)
                if not client.is_connected():
                    log.warning("MQTT: disconnected — reconnecting")
                    client.reconnect()
        except Exception as e:
            log.error("MQTT: %s — retry in 30s", e)
            try:
                client.loop_stop()
            except Exception:
                pass
            await asyncio.sleep(30)


# ──────────────────────────────────────────────────────────────
# Real-time setpoint listener (LISTEN/NOTIFY → ESP32 push)
# ──────────────────────────────────────────────────────────────
async def setpoint_listener(pool: asyncpg.Pool) -> None:
    """Listen for DB setpoint changes and push to ESP32 in real-time."""
    import json
    import time as _time

    from entity_map import PARAM_TO_ENTITY, SWITCH_TO_ENTITY

    _ALIASES = {
        "set_vpd_high_kpa": "vpd_high",
        "set_vpd_low_kpa": "vpd_low",
        "set_temp_low__f": "temp_low",
        "set_temp_high__f": "temp_high",
        "vpd_mister_engage_kpa": "mister_engage_kpa",
        "vpd_mister_all_kpa": "mister_all_kpa",
    }

    async def _on_notify(conn, pid, channel, payload):
        source = None
        if payload.startswith("{"):
            try:
                event = json.loads(payload)
                param = str(event.get("parameter") or "")
                val_str = str(event.get("value") or "")
                source = str(event.get("source") or "")
            except (TypeError, ValueError):
                return
        else:
            if "=" not in payload:
                return
            param, val_str = payload.split("=", 1)
        try:
            val = float(val_str)
        except ValueError:
            return

        # Normalize param name
        param = _ALIASES.get(param, param)
        if source == "esp32":
            log.debug("RT push suppressed for ESP32 echo %s", param)
            return
        if not _accept_outbound_setpoint(param, val):
            return
        pushed_at = shared.recently_pushed.get(param, 0)
        if _time.time() - pushed_at < _PUSH_ECHO_SUPPRESS_S and _same_pushed_value(param, val):
            log.debug("RT push suppressed for recently pushed %s", param)
            return

        # Look up ESP32 entity
        if param.startswith("sw_"):
            eid = SWITCH_TO_ENTITY.get(param)
            etype = "switch"
        else:
            eid = PARAM_TO_ENTITY.get(param)
            etype = "number"

        if eid:
            pushed = await push_to_esp32([(eid, val, etype)])
            if pushed:
                log.info("RT push: %s=%s → ESP32 (<1s)", param, val_str)

    # Acquire a dedicated connection for LISTEN (can't share with pool)
    conn = await asyncpg.connect(DB_DSN)
    await conn.add_listener("setpoint_changed", _on_notify)
    log.info("Setpoint listener: LISTEN on setpoint_changed channel")

    try:
        while True:
            await asyncio.sleep(60)
    finally:
        await conn.remove_listener("setpoint_changed", _on_notify)
        await conn.close()


# ──────────────────────────────────────────────────────────────
# Gap detection and backfill on reconnect
# ──────────────────────────────────────────────────────────────
async def _last_telemetry_ts(pool: asyncpg.Pool) -> datetime | None:
    """Most recent persisted telemetry timestamp (for restart-gap detection, M6).

    Uses the climate hypertable — the canonical 1/min telemetry stream. Returns
    None when the table is empty (genuine first-ever run, no gap to record).
    """
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT max(ts) FROM climate")


async def backfill_gap(
    pool: asyncpg.Pool, gap_start: datetime, gap_end: datetime, reason: str = "ingestor_restart"
) -> None:
    """Record data gap and snapshot current equipment state after reconnect."""
    duration = (gap_end - gap_start).total_seconds()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO data_gaps (start_ts, end_ts, duration_s, reason, backfill_status) "
            "VALUES ($1, $2, $3, $4, 'snapshot_taken')",
            gap_start,
            gap_end,
            duration,
            reason,
        )

        # Snapshot current equipment state (we know NOW, not what happened during gap)
        for obj_id in list(state.key_to_object_id.values()):
            from entity_map import EQUIPMENT_BINARY_MAP, EQUIPMENT_SWITCH_MAP

            equip = EQUIPMENT_BINARY_MAP.get(obj_id) or EQUIPMENT_SWITCH_MAP.get(obj_id)
            if equip and obj_id in state.equipment:
                await conn.execute(
                    "INSERT INTO equipment_state (ts, equipment, state) VALUES ($1, $2, $3)",
                    gap_end,
                    equip,
                    state.equipment[obj_id],
                )

    log.info("Gap backfill: %.0fs gap recorded, equipment state snapshot taken", duration)


# ──────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────
async def main() -> None:
    global ESP32_HOST, ESP32_PORT, ESP32_API_KEY, GREENHOUSE_ID, _fanout_publisher

    # Fail loudly if both fan-out modes are set (publisher + subscriber in one
    # process would re-emit what it consumes — a self-feeding topic storm).
    assert_modes_consistent()

    pool = await asyncpg.create_pool(DB_DSN, min_size=2, max_size=10)
    log.info("DB connection pool ready")

    # ── #114: subscribe mode (dev/stage) — ingest FROM prod's fan-out bus ─────
    # NO ESP32 loop, NO Home Assistant, NO occupancy bridge, NO setpoint listener.
    # The only ingest path is the fan-out subscriber writing into THIS env's DB.
    if subscribe_mode_enabled():
        log.info(
            "Verdify ingestor starting in MQTT-SUBSCRIBE mode (greenhouse: %s) — "
            "read-only telemetry mirror, no device/HA loops",
            GREENHOUSE_ID,
        )
        await asyncio.gather(
            mqtt_subscribe_loop(pool),
        )
        return

    # ── Capture mode (prod, and the legacy VM single-writer) ──────────────────
    log.info("Verdify ingestor starting (greenhouse: %s)...", GREENHOUSE_ID)

    # #113: prod-only publish-all. Set up the bus publisher; the flush path
    # re-emits every flushed row. Best-effort connect — a bus outage must never
    # block telemetry capture, so a failed connect leaves _fanout_publisher None
    # and the loop proceeds DB-only.
    if publish_all_enabled():
        from config import (
            FANOUT_MQTT_HOST,
            FANOUT_MQTT_PASS,
            FANOUT_MQTT_PORT,
            FANOUT_MQTT_USER,
        )

        pub = FanoutPublisher(
            FANOUT_MQTT_HOST,
            FANOUT_MQTT_PORT,
            user=FANOUT_MQTT_USER,
            password=FANOUT_MQTT_PASS,
        )
        try:
            pub.connect()
            _fanout_publisher = pub
            log.info("Fan-out publish-all ENABLED (#113) — re-emitting flushed rows to the bus")
        except Exception as e:  # noqa: BLE001
            log.error("Fan-out publisher connect failed: %s — continuing DB-only", e)
            _fanout_publisher = None

    # Load ESP32 config from greenhouses table (overrides .env)
    try:
        async with pool.acquire() as conn:
            gh = await conn.fetchrow(
                "SELECT esp32_host, esp32_port, esp32_api_key FROM greenhouses WHERE id = $1", GREENHOUSE_ID
            )
            if gh and gh["esp32_host"]:
                ESP32_HOST = gh["esp32_host"]
                ESP32_PORT = gh["esp32_port"] or 6053
                if gh["esp32_api_key"]:
                    ESP32_API_KEY = gh["esp32_api_key"]
                log.info("ESP32 config loaded from DB: %s:%d", ESP32_HOST, ESP32_PORT)
            else:
                log.info("ESP32 config from .env fallback: %s:%d", ESP32_HOST, ESP32_PORT)
    except Exception as e:
        log.warning("Could not load greenhouse config from DB: %s (using .env)", e)

    await asyncio.gather(
        esp32_loop(pool),
        flush_loop(pool),
        task_loop(pool),
        mqtt_loop(pool),
        setpoint_listener(pool),
    )


if __name__ == "__main__":
    asyncio.run(main())
