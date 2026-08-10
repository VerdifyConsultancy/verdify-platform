#!/usr/bin/env python3
"""Backfill Verdify telemetry gaps from Home Assistant recorder history.

Default mode is a dry-run. The production CronJob passes --apply.

This intentionally backfills only tables where Home Assistant is a real source
of truth or a faithful mirror of ESPHome state:
  climate, diagnostics, energy, equipment_state, system_state, setpoint_snapshot

It does not synthesize planner/action tables such as climate_action_log.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import http.client
import json
import logging
import math
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg
from pydantic import ValidationError


def local_repo_candidates() -> tuple[Path, ...]:
    script = Path(__file__).resolve()
    candidates = [Path("/app/ingestor"), Path("/app")]
    if len(script.parents) > 4:
        candidates.extend([script.parents[4] / "ingestor", script.parents[4]])
    return tuple(candidates)


for candidate in local_repo_candidates():
    if candidate.exists() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from entity_map import (  # noqa: E402
    CFG_READBACK_MAP,
    CLIMATE_MAP,
    DIAGNOSTIC_MAP,
    EQUIPMENT_BINARY_MAP,
    EQUIPMENT_SWITCH_MAP,
    STATE_MAP,
    normalize_feedback_value,
)

try:
    from backfill_write_fence import (  # noqa: E402
        BACKFILL_WRITE_FENCE_CONTRACT_VERSION,
        CLIMATE_WRITE_BUCKET_SECONDS,
        CLIMATE_WRITE_FENCE_SQL,
        climate_write_fence_key,
    )
except ImportError as exc:
    raise RuntimeError(
        "HA gap backfill requires the paired ingestor image write-fence contract; refusing to run"
    ) from exc

if BACKFILL_WRITE_FENCE_CONTRACT_VERSION != 1:  # pragma: no cover - image/source skew guard
    raise RuntimeError("unsupported ingestor/backfill write-fence contract version")

from verdify_schemas import ClimateRow, Diagnostics, EnergySample, EquipmentStateEvent, SystemStateRow  # noqa: E402
from verdify_schemas.setpoint import SetpointSnapshot  # noqa: E402

LOG = logging.getLogger("ha-gap-backfill")
GREENHOUSE_ID = os.environ.get("GREENHOUSE_ID", "vallery")
DEFAULT_HA_URL = "http://192.168.30.107:8123"
DEFAULT_HA_TOKEN_FILE = "/mnt/agents/shared/credentials/ha_token.txt"
DEFAULT_HA_RESPONSE_MAX_BYTES = 32 * 1024 * 1024
DEFAULT_WRITE_TRANSACTION_TIMEOUT_SECONDS = 30.0
DEFAULT_EVENT_RECONCILE_MINUTES = 240.0
MIN_APPLY_SETTLE_SECONDS = 120.0
ADVISORY_LOCK_NAME = "verdify-ha-gap-backfill"

SAMPLE_TABLES = ("climate", "diagnostics", "setpoint_snapshot", "energy")
WRITE_TABLES = (*SAMPLE_TABLES, "equipment_state", "system_state")
REPRESENTATIVE_HA_ENTITIES = (
    "sensor.greenhouse_avg_temp_degf",
    "sensor.greenhouse_avg_rh",
    "sensor.greenhouse_avg_vpd_kpa",
)


@dataclass(frozen=True)
class Candidate:
    entity_id: str
    converter: Callable[[float], float | int | None] | None = None


@dataclass(frozen=True)
class ScalarMapping:
    entity_id: str
    target: str
    converter: Callable[[float], float | int | None] | None = None


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime
    reasons: tuple[str, ...]

    @property
    def minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60.0


@dataclass
class HAHistory:
    points: list[tuple[datetime, str]]

    def value_at(self, ts: datetime) -> str | None:
        idx = bisect_right(self.points, (ts, chr(0x10FFFF))) - 1
        if idx < 0:
            return None
        state = self.points[idx][1]
        return None if is_bad_state(state) else state

    def events_between(self, start: datetime, end: datetime) -> Iterable[tuple[datetime, str]]:
        left = bisect_right(self.points, (start - timedelta(microseconds=1), chr(0x10FFFF)))
        for ts, state in self.points[left:]:
            if ts > end:
                break
            if not is_bad_state(state):
                yield ts, state


@dataclass
class MappingSet:
    climate: list[ScalarMapping]
    diagnostics: list[ScalarMapping]
    setpoints: list[ScalarMapping]
    energy: list[ScalarMapping]
    equipment: dict[str, str]
    system_state: dict[str, str]

    @property
    def all_entities(self) -> list[str]:
        ids = {
            *(m.entity_id for m in self.climate),
            *(m.entity_id for m in self.diagnostics),
            *(m.entity_id for m in self.setpoints),
            *(m.entity_id for m in self.energy),
            *self.equipment,
            *self.system_state,
        }
        return sorted(ids)


@dataclass
class Stats:
    windows_seen: int = 0
    windows_backfilled: int = 0
    windows_without_ha: int = 0
    windows_committed: int = 0
    windows_with_candidates: int = 0
    rows: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    skipped: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def add(self, table: str, count: int) -> None:
        self.rows[table] += count


@dataclass(frozen=True)
class HistoryFetchPolicy:
    request_timeout_seconds: float = 60.0
    transport_retries: int = 1
    retry_backoff_seconds: float = 2.0
    request_max_minutes: float = 60.0
    request_min_minutes: float = 5.0
    max_requests: int = 512
    budget_seconds: float = 1200.0
    max_split_depth: int = 12
    max_points: int = 250_000
    response_max_bytes: int = DEFAULT_HA_RESPONSE_MAX_BYTES

    def __post_init__(self) -> None:
        finite_positive = {
            "request_timeout_seconds": self.request_timeout_seconds,
            "retry_backoff_seconds": self.retry_backoff_seconds,
            "request_max_minutes": self.request_max_minutes,
            "request_min_minutes": self.request_min_minutes,
            "budget_seconds": self.budget_seconds,
        }
        invalid = [
            name
            for name, value in finite_positive.items()
            if isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or value <= 0
        ]
        integer_limits = {
            "max_requests": self.max_requests,
            "max_split_depth": self.max_split_depth,
            "max_points": self.max_points,
            "response_max_bytes": self.response_max_bytes,
        }
        invalid.extend(name for name, value in integer_limits.items() if type(value) is not int or value <= 0)
        if type(self.transport_retries) is not int or self.transport_retries < 0:
            invalid.append("transport_retries")
        if invalid:
            raise ValueError("invalid history fetch policy: " + ", ".join(invalid))
        if self.request_max_minutes < self.request_min_minutes:
            raise ValueError("history request maximum must be at least the minimum")


@dataclass(frozen=True)
class HistoryRequest:
    entity_ids: tuple[str, ...]
    start: datetime
    end: datetime
    depth: int = 0
    parent_key: str | None = None
    include_initial_state: bool = True

    @property
    def span_minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60.0

    @property
    def entity_fingerprint(self) -> str:
        joined = "\n".join(self.entity_ids).encode()
        return hashlib.sha256(joined).hexdigest()[:12]

    @property
    def key(self) -> str:
        material = (
            f"{history_iso_z(self.start)}|{history_iso_z(self.end)}|{','.join(self.entity_ids)}|"
            f"{self.depth}|{self.include_initial_state}"
        )
        return hashlib.sha256(material.encode()).hexdigest()[:12]


@dataclass
class HistoryFetchStats:
    attempts: int = 0
    retries: int = 0
    splits: int = 0
    points: int = 0


class HistoryFetchExhausted(RuntimeError):
    """A bounded history fetch could not complete safely."""


class HistoryPayloadError(RuntimeError):
    """Home Assistant returned ambiguous or malformed history."""


class HistoryResponseTooLarge(HistoryPayloadError):
    """Home Assistant response exceeded the bounded in-memory body limit."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write missing rows; default is dry-run")
    parser.add_argument("--lookback-days", type=float, default=30.0, help="scan this many days when --start is omitted")
    parser.add_argument("--start", help="UTC/ISO start time for a manual reconciliation window")
    parser.add_argument("--end", help="UTC/ISO end time; default is now minus --settle-minutes")
    parser.add_argument(
        "--since-earliest-ha",
        action="store_true",
        help="discover the earliest HA recorder row for representative greenhouse entities and start there",
    )
    parser.add_argument(
        "--earliest-search-start",
        default="2024-01-01T00:00:00Z",
        help="lower bound for --since-earliest-ha discovery",
    )
    parser.add_argument(
        "--full-scan",
        action="store_true",
        help="reconcile every chunk in the range instead of only Verdify DB gaps",
    )
    parser.add_argument("--max-gap-minutes", type=float, default=10.0, help="minimum DB gap size to repair")
    parser.add_argument(
        "--max-backfill-minutes",
        type=float,
        default=720.0,
        help="split repair work into chunks no larger than this",
    )
    parser.add_argument("--max-windows", type=int, default=12, help="maximum windows per run; 0 means unlimited")
    parser.add_argument(
        "--event-reconcile-minutes",
        type=float,
        default=DEFAULT_EVENT_RECONCILE_MINUTES,
        help="always reconcile this trailing event-only window so sparse transitions cannot fall between runs",
    )
    parser.add_argument("--sample-seconds", type=int, default=60, help="climate/diagnostic/setpoint backfill cadence")
    parser.add_argument("--energy-sample-seconds", type=int, default=300, help="energy backfill cadence")
    parser.add_argument("--settle-minutes", type=float, default=2.0, help="do not backfill the freshest N minutes")
    parser.add_argument("--ha-url", default=os.environ.get("HA_URL", DEFAULT_HA_URL))
    parser.add_argument("--ha-token-file", default=os.environ.get("HA_TOKEN_FILE", DEFAULT_HA_TOKEN_FILE))
    parser.add_argument("--batch-size", type=int, default=25, help="HA history entity batch size")
    parser.add_argument(
        "--history-carry-minutes", type=float, default=20.0, help="pre-window HA history for carry-forward"
    )
    parser.add_argument(
        "--history-request-timeout-seconds",
        type=float,
        default=60.0,
        help="timeout for one bounded HA history request",
    )
    parser.add_argument(
        "--history-transport-retries",
        type=int,
        default=1,
        help="same-shape retries before splitting or failing",
    )
    parser.add_argument(
        "--history-retry-backoff-seconds",
        type=float,
        default=2.0,
        help="initial bounded retry delay",
    )
    parser.add_argument(
        "--history-request-max-minutes",
        type=float,
        default=60.0,
        help="pre-split ordinary history requests to at most this span",
    )
    parser.add_argument(
        "--history-request-min-minutes",
        type=float,
        default=5.0,
        help="smallest adaptive time-split leaf",
    )
    parser.add_argument("--history-max-requests", type=int, default=512, help="run-wide HA history request budget")
    parser.add_argument(
        "--history-fetch-budget-seconds",
        type=float,
        default=1200.0,
        help="run-wide wall-clock budget for HA history fetching",
    )
    parser.add_argument("--history-max-split-depth", type=int, default=12, help="adaptive split depth limit")
    parser.add_argument(
        "--history-max-points",
        type=int,
        default=250_000,
        help="run-wide decoded HA history point budget",
    )
    parser.add_argument(
        "--history-response-max-bytes",
        type=int,
        default=DEFAULT_HA_RESPONSE_MAX_BYTES,
        help="maximum encoded bytes accepted from one HA response",
    )
    parser.add_argument(
        "--write-transaction-timeout-seconds",
        type=float,
        default=DEFAULT_WRITE_TRANSACTION_TIMEOUT_SECONDS,
        help="maximum time for each apply DB statement and the pre-commit climate-fence phase",
    )
    parser.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "INFO"))
    return parser.parse_args()


def parse_time(value: str) -> datetime:
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def iso_z(dt: datetime) -> str:
    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def history_iso_z(dt: datetime) -> str:
    """RFC3339 with microseconds for padded HA recorder boundaries."""
    return dt.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def floor_time(dt: datetime, seconds: int) -> datetime:
    epoch = math.floor(dt.astimezone(UTC).timestamp())
    return datetime.fromtimestamp(epoch - (epoch % seconds), UTC)


def ceil_time(dt: datetime, seconds: int) -> datetime:
    floored = floor_time(dt, seconds)
    return floored if floored == dt else floored + timedelta(seconds=seconds)


def iter_times(start: datetime, end: datetime, seconds: int) -> Iterable[datetime]:
    ts = ceil_time(start, seconds)
    while ts <= end:
        yield ts
        ts += timedelta(seconds=seconds)


def is_bad_state(state: str | None) -> bool:
    return state is None or state.lower() in {"unknown", "unavailable", "none", ""}


def to_float(state: str | None) -> float | None:
    if is_bad_state(state):
        return None
    try:
        value = float(str(state).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def to_bool(state: str | None) -> bool | None:
    if is_bad_state(state):
        return None
    normalized = str(state).strip().lower()
    if normalized in {"on", "true", "1", "open", "detected", "home", "occupied"}:
        return True
    if normalized in {"off", "false", "0", "closed", "clear", "not_home", "empty"}:
        return False
    return None


def fahrenheit_from_c(value: float) -> float:
    return value * 9.0 / 5.0 + 32.0


def hpa_from_pressure(value: float) -> float:
    return value * 33.8639 if value < 200 else value


def positive_abs(value: float) -> float:
    return abs(value)


def identity(value: float) -> float:
    return value


def int_value(value: float) -> int:
    return int(value)


def object_id_to_slug(object_id: str) -> str:
    slug = object_id.lower()
    replacements = (
        ("co___ppm_", "co2_ppm"),
        ("___s_cm_", "_us_cm"),
        ("__s_cm_", "_us_cm"),
        ("___f_", "_degf"),
        ("__f_", "_degf"),
        ("____", "_pct"),
        ("___", "_"),
        ("__kpa_", "_kpa"),
        ("__ppm_", "_ppm"),
        ("__gpm_", "_gpm"),
        ("__gal_", "_gal"),
        ("__lx_", "_lx"),
        ("__s_", "_s"),
        ("__ms_", "_ms"),
        ("__min_", "_min"),
        ("__hour_", "_hour"),
    )
    for old, new in replacements:
        slug = slug.replace(old, new)
    slug = slug.replace("mol_m_day", "mol_m2_day")
    slug = slug.replace("g_m_", "g_m3")
    slug = slug.replace("p_h", "ph")
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug


def unique_candidates(candidates: Iterable[Candidate]) -> list[Candidate]:
    seen: set[str] = set()
    result: list[Candidate] = []
    for candidate in candidates:
        if candidate.entity_id in seen:
            continue
        seen.add(candidate.entity_id)
        result.append(candidate)
    return result


def choose_candidate(candidates: Iterable[Candidate], current_entities: set[str]) -> Candidate | None:
    choices = unique_candidates(candidates)
    for candidate in choices:
        if candidate.entity_id in current_entities:
            return candidate
    return choices[0] if choices else None


def greenhouse_sensor(slug: str) -> str:
    return f"sensor.greenhouse_{slug}"


def greenhouse_text_sensor(slug: str) -> str:
    return f"text_sensor.greenhouse_{slug}"


def column_slug_candidates(column: str) -> list[str]:
    slugs = [column]
    if column.startswith("temp_"):
        zone = column.removeprefix("temp_")
        slugs.extend([f"{zone}_temp_degf", f"{zone}_temp"])
    if column.startswith("rh_"):
        zone = column.removeprefix("rh_")
        slugs.extend([f"{zone}_rh", f"{zone}_rh_pct"])
    if column.startswith("vpd_"):
        zone = column.removeprefix("vpd_")
        slugs.extend([f"{zone}_vpd_kpa", f"{zone}_vpd"])
    if column.startswith("soil_temp_"):
        slugs.append(f"{column}_degf")
    if column.startswith("soil_moisture_"):
        slugs.append(column.replace("soil_moisture", "soil_moisture_pct"))
    return list(dict.fromkeys(slugs))


CLIMATE_EXACT: dict[str, list[Candidate]] = {
    "dew_point": [Candidate("sensor.greenhouse_dew_point_degf")],
    "abs_humidity": [Candidate("sensor.greenhouse_abs_humidity_g_m3"), Candidate("sensor.greenhouse_abs_humidity")],
    "enthalpy_delta": [Candidate("sensor.greenhouse_enthalpy_kj_kg"), Candidate("sensor.greenhouse_enthalpy")],
    "co2_ppm": [Candidate("sensor.greenhouse_co2_ppm")],
    "lux": [Candidate("sensor.greenhouse_light_lx"), Candidate("sensor.greenhouse_lux")],
    "dli_today": [Candidate("sensor.greenhouse_dli_mol_m2_day"), Candidate("sensor.greenhouse_dli")],
    "flow_gpm": [Candidate("sensor.greenhouse_water_flow_gpm")],
    "water_total_gal": [Candidate("sensor.greenhouse_water_used_gal"), Candidate("sensor.greenhouse_water_total_gal")],
    "pressure_hpa": [
        Candidate("sensor.panorama_air_pressure", hpa_from_pressure),
        Candidate("sensor.greenhouse_tempest_pressure", hpa_from_pressure),
    ],
    "outdoor_temp_f": [Candidate("sensor.panorama_temperature")],
    "outdoor_rh_pct": [Candidate("sensor.panorama_humidity")],
    "wind_speed_mph": [Candidate("sensor.panorama_wind_speed")],
    "wind_direction_deg": [Candidate("sensor.panorama_wind_direction")],
    "outdoor_lux": [Candidate("sensor.panorama_illuminance")],
    "solar_irradiance_w_m2": [Candidate("sensor.panorama_irradiance")],
    "precip_in": [Candidate("sensor.panorama_precipitation")],
    "uv_index": [Candidate("sensor.panorama_uv_index")],
    "wind_gust_mph": [Candidate("sensor.panorama_wind_gust")],
    "wind_lull_mph": [Candidate("sensor.panorama_wind_lull")],
    "wind_speed_avg_mph": [Candidate("sensor.panorama_wind_speed_average")],
    "wind_direction_avg_deg": [Candidate("sensor.panorama_wind_direction_average")],
    "feels_like_f": [Candidate("sensor.panorama_feels_like")],
    "wet_bulb_temp_f": [Candidate("sensor.panorama_wet_bulb_temperature")],
    "vapor_pressure_inhg": [Candidate("sensor.panorama_vapor_pressure")],
    "air_density_kg_m3": [Candidate("sensor.panorama_air_density")],
    "precip_intensity_in_h": [Candidate("sensor.panorama_precipitation_intensity")],
    "lightning_count": [Candidate("sensor.panorama_lightning_count", int_value)],
    "lightning_avg_dist_mi": [Candidate("sensor.panorama_lightning_average_distance")],
    "hydro_ec_us_cm": [Candidate("sensor.greenhouse_hydroponic_ec_corrected")],
    "hydro_orp_mv": [Candidate("sensor.greenhouse_hydroponic_orp")],
    "hydro_ph": [Candidate("sensor.greenhouse_hydroponic_ph_corrected")],
    "hydro_tds_ppm": [Candidate("sensor.greenhouse_hydroponic_tds_corrected")],
    "hydro_water_temp_f": [Candidate("sensor.greenhouse_hydroponic_water_temp", fahrenheit_from_c)],
    "hydro_battery_pct": [Candidate("sensor.greenhouse_hydroponic_yinmik_battery")],
    "moisture_center": [
        Candidate("sensor.greenhouse_center_soil_moisture"),
        Candidate("sensor.greenhouse_center_root_zone_moisture"),
        Candidate("sensor.greenhouse_center_root_zone_soil_moisture"),
        Candidate("sensor.greenhouse_center_vwc"),
        Candidate("sensor.greenhouse_center_substrate_vwc"),
    ],
    "ph_runoff_center": [
        Candidate("sensor.greenhouse_center_runoff_ph"),
        Candidate("sensor.greenhouse_center_run_off_ph"),
        Candidate("sensor.greenhouse_center_drain_ph"),
        Candidate("sensor.greenhouse_center_leachate_ph"),
    ],
    "ec_runoff_center": [
        Candidate("sensor.greenhouse_center_runoff_ec"),
        Candidate("sensor.greenhouse_center_runoff_ec_ms_cm"),
        Candidate("sensor.greenhouse_center_runoff_ec_us_cm"),
        Candidate("sensor.greenhouse_center_drain_ec"),
        Candidate("sensor.greenhouse_center_leachate_ec"),
    ],
}

DIAGNOSTIC_TEXT_COLUMNS = {"probe_health", "reset_reason", "firmware_version", "zone_wet_granted", "band_source"}
DIAGNOSTIC_INT_COLUMNS = {
    "active_probe_count",
    "relief_cycle_count",
    "vent_latch_timer_s",
    "sealed_timer_s",
    "vpd_watch_timer_s",
    "mist_backoff_timer_s",
    "vent_mist_assist_active",
    "controller_time_epoch",
    "controller_local_hour",
    "sntp_valid",
    "sntp_miss_count",
    "last_sntp_sync_age_s",
}

ENERGY_MAPPINGS = [
    ScalarMapping("sensor.shellyproem50_ac15186daafc_energy_meter_0_power", "ch0_power_w"),
    ScalarMapping("sensor.shellyproem50_ac15186daafc_energy_meter_0_total_active_energy", "ch0_energy_kwh"),
    ScalarMapping("sensor.shellyproem50_ac15186daafc_energy_meter_1_power", "ch1_power_w", positive_abs),
]

LIGHT_EQUIPMENT = {
    "switch.greenhouse_main": "grow_light_main",
    "switch.greenhouse_grow": "grow_light_grow",
}

HA_SWITCH_EQUIPMENT = {
    "switch.greenhouse_economiser_enabled": "economiser_enabled",
    "switch.greenhouse_fog_closes_vent": "fog_closes_vent",
    "switch.greenhouse_irrigation_enabled": "irrigation_enabled",
    "switch.greenhouse_irrigation_wall_enabled": "irrigation_wall_enabled",
    "switch.greenhouse_irrigation_center_enabled": "irrigation_center_enabled",
    "switch.greenhouse_irrigation_weather_skip": "irrigation_weather_skip",
}


def climate_candidates(object_id: str, column: str) -> list[Candidate]:
    candidates = list(CLIMATE_EXACT.get(column, []))
    slug = object_id_to_slug(object_id)
    candidates.append(Candidate(greenhouse_sensor(slug)))
    if slug.endswith("_pct"):
        candidates.append(Candidate(greenhouse_sensor(slug.removesuffix("_pct"))))
    for col_slug in column_slug_candidates(column):
        candidates.append(Candidate(greenhouse_sensor(col_slug)))
    return candidates


def diagnostic_candidates(object_id: str, column: str) -> list[Candidate]:
    slug = object_id_to_slug(object_id)
    candidates = [
        Candidate(greenhouse_sensor(slug)),
        Candidate(greenhouse_text_sensor(slug)),
        Candidate(greenhouse_sensor(column)),
        Candidate(greenhouse_text_sensor(column)),
    ]
    if column == "effective_heat_target_f":
        candidates.insert(0, Candidate("sensor.greenhouse_effective_heat_target_degf"))
    if column == "effective_cool_stage2_delta_f":
        candidates.insert(0, Candidate("sensor.greenhouse_effective_cool_stage_2_delta_degf"))
    return candidates


def cfg_candidates(object_id: str, parameter: str) -> list[Candidate]:
    slug = object_id_to_slug(object_id)
    candidates = [Candidate(greenhouse_sensor(slug)), Candidate(greenhouse_sensor(f"cfg_{parameter}"))]
    if "degf" in slug:
        candidates.append(Candidate(greenhouse_sensor(f"cfg_{parameter}_degf")))
        if parameter.endswith("_f"):
            candidates.append(Candidate(greenhouse_sensor(f"cfg_{parameter.removesuffix('_f')}_degf")))
    if slug.endswith("_pct") and not parameter.endswith("_pct"):
        candidates.append(Candidate(greenhouse_sensor(f"cfg_{parameter}_pct")))
    if parameter == "site_pressure_hpa":
        return [Candidate("sensor.greenhouse_cfg_site_pressure_hpa", hpa_from_pressure), *candidates]
    return candidates


def build_mappings(current_entities: set[str]) -> MappingSet:
    climate: list[ScalarMapping] = []
    for object_id, column in sorted(CLIMATE_MAP.items(), key=lambda item: item[1]):
        candidate = choose_candidate(climate_candidates(object_id, column), current_entities)
        if candidate:
            climate.append(ScalarMapping(candidate.entity_id, column, candidate.converter))
    for column, candidates in sorted(CLIMATE_EXACT.items()):
        candidate = choose_candidate(candidates, current_entities)
        if candidate:
            climate.append(ScalarMapping(candidate.entity_id, column, candidate.converter))

    diagnostics: list[ScalarMapping] = []
    for object_id, column in sorted(DIAGNOSTIC_MAP.items(), key=lambda item: item[1]):
        candidate = choose_candidate(diagnostic_candidates(object_id, column), current_entities)
        if candidate:
            diagnostics.append(ScalarMapping(candidate.entity_id, column, candidate.converter))

    setpoints: list[ScalarMapping] = []
    for object_id, parameter in sorted(CFG_READBACK_MAP.items(), key=lambda item: item[1]):
        candidate = choose_candidate(cfg_candidates(object_id, parameter), current_entities)
        if candidate:
            setpoints.append(ScalarMapping(candidate.entity_id, parameter, candidate.converter))

    equipment: dict[str, str] = {}
    for object_id, name in EQUIPMENT_BINARY_MAP.items():
        slug = object_id_to_slug(object_id)
        candidate = choose_candidate([Candidate(f"binary_sensor.greenhouse_{slug}")], current_entities)
        if candidate:
            equipment[candidate.entity_id] = name
    for object_id, name in EQUIPMENT_SWITCH_MAP.items():
        slug = object_id_to_slug(object_id)
        candidate = choose_candidate([Candidate(f"switch.greenhouse_{slug}")], current_entities)
        if candidate:
            equipment[candidate.entity_id] = name
    equipment.update(LIGHT_EQUIPMENT)
    equipment.update(HA_SWITCH_EQUIPMENT)

    system_state: dict[str, str] = {}
    for object_id, entity in STATE_MAP.items():
        slug = object_id_to_slug(object_id)
        candidate = choose_candidate(
            [Candidate(greenhouse_sensor(slug)), Candidate(greenhouse_text_sensor(slug))],
            current_entities,
        )
        if candidate:
            system_state[candidate.entity_id] = entity

    return MappingSet(
        climate=dedupe_mappings(climate),
        diagnostics=dedupe_mappings(diagnostics),
        setpoints=dedupe_mappings(setpoints),
        energy=ENERGY_MAPPINGS,
        equipment=equipment,
        system_state=system_state,
    )


def dedupe_mappings(mappings: list[ScalarMapping]) -> list[ScalarMapping]:
    by_target: dict[str, ScalarMapping] = {}
    for mapping in mappings:
        by_target.setdefault(mapping.target, mapping)
    return list(by_target.values())


def load_ha_token(path: str) -> str:
    token = Path(path).read_text(encoding="utf-8").strip()
    if not token:
        raise RuntimeError(f"HA token file is empty: {path}")
    return token


def ha_get_json(
    ha_url: str,
    token: str,
    path: str,
    timeout: float = 60,
    *,
    max_response_bytes: int = DEFAULT_HA_RESPONSE_MAX_BYTES,
) -> Any:
    req = urllib.request.Request(
        f"{ha_url.rstrip('/')}{path}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read(max_response_bytes + 1)
    if len(body) > max_response_bytes:
        # A traceback retains this frame. Drop the body before raising so
        # adaptive split recursion cannot retain one body per ancestor request.
        del body
        raise HistoryResponseTooLarge("HA response exceeded encoded byte limit")
    return json.loads(body)


def fetch_current_entities(ha_url: str, token: str) -> set[str]:
    payload = ha_get_json(ha_url, token, "/api/states", timeout=30)
    return {row.get("entity_id") for row in payload if row.get("entity_id")}


def parse_ha_time(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return parse_time(raw)
    except ValueError:
        return None


def _history_error_action(exc: Exception) -> tuple[str, str]:
    """Return bounded handling action and a non-secret error class."""
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 413:
            return "split", "http_413"
        if exc.code == 408:
            return "retry_split", "http_408"
        if exc.code == 429:
            return "retry_fail", "http_429"
        if 500 <= exc.code <= 599:
            return "retry_fail", f"http_{exc.code}"
        return "fatal", f"http_{exc.code}"
    if isinstance(exc, HistoryResponseTooLarge):
        return "split", "response_too_large"
    if isinstance(exc, json.JSONDecodeError):
        return "retry_fail", "json_decode"
    if isinstance(exc, http.client.IncompleteRead):
        return "retry_split", "incomplete_read"
    if isinstance(
        exc,
        (
            http.client.RemoteDisconnected,
            ConnectionResetError,
            ConnectionAbortedError,
            BrokenPipeError,
        ),
    ):
        return "retry_fail", type(exc).__name__
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "retry_split", type(exc).__name__
    if isinstance(exc, urllib.error.URLError):
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            return "retry_split", "url_timeout"
        if isinstance(exc.reason, http.client.IncompleteRead):
            return "retry_split", "url_incomplete_read"
        if isinstance(
            exc.reason,
            (
                http.client.RemoteDisconnected,
                ConnectionResetError,
                ConnectionAbortedError,
                BrokenPipeError,
            ),
        ):
            return "retry_fail", "url_connection_reset"
        return "retry_fail", "url_transport"
    if isinstance(exc, HistoryPayloadError):
        return "fatal", "payload_integrity"
    return "fatal", type(exc).__name__


def _merge_history_points(
    destination: dict[str, dict[datetime, str]],
    source: dict[str, list[tuple[datetime, str]]],
) -> None:
    for entity_id, points in source.items():
        target = destination.setdefault(entity_id, {})
        for timestamp, state in points:
            previous = target.get(timestamp)
            if previous is not None and previous != state:
                entity_hash = hashlib.sha256(entity_id.encode()).hexdigest()[:12]
                raise HistoryPayloadError(f"conflicting HA states for entity_hash={entity_hash} at {iso_z(timestamp)}")
            target[timestamp] = state


def _freeze_history_points(
    points: dict[str, dict[datetime, str]],
) -> dict[str, list[tuple[datetime, str]]]:
    return {entity_id: sorted(by_timestamp.items()) for entity_id, by_timestamp in points.items()}


def _parse_history_payload(
    payload: Any,
    allowed_entities: set[str],
    *,
    max_points: int | None = None,
) -> dict[str, list[tuple[datetime, str]]]:
    if not isinstance(payload, list):
        raise HistoryPayloadError("HA history payload is not a list")
    histories: dict[str, list[tuple[datetime, str]]] = defaultdict(list)
    decoded_points = 0
    for series in payload:
        if not isinstance(series, list):
            raise HistoryPayloadError("HA history series is not a list")
        if not series:
            continue
        entity_id = None
        for row in series:
            if not isinstance(row, dict):
                raise HistoryPayloadError("HA history row is not an object")
            row_entity = row.get("entity_id")
            if row_entity is not None and (not isinstance(row_entity, str) or not row_entity):
                raise HistoryPayloadError("HA history row has an invalid entity_id")
            if row_entity:
                if entity_id is not None and row_entity != entity_id:
                    raise HistoryPayloadError("HA history series changed entity_id")
                entity_id = row_entity
            if entity_id is None:
                raise HistoryPayloadError("HA history series does not establish entity_id")
            if entity_id not in allowed_entities:
                raise HistoryPayloadError("HA history returned an unrequested entity_id")
            timestamp = parse_ha_time(row.get("last_updated") or row.get("last_changed"))
            state = row.get("state")
            if timestamp is None:
                raise HistoryPayloadError("HA history row has a missing or invalid timestamp")
            if not isinstance(state, str):
                raise HistoryPayloadError("HA history row has a missing or non-string state")
            if max_points is not None and decoded_points >= max_points:
                raise HistoryFetchExhausted("HA history decoded point budget exhausted")
            histories[entity_id].append((timestamp, state))
            decoded_points += 1
    return dict(histories)


class HistoryFetcher:
    """Run-scoped, bounded Home Assistant history fetcher."""

    def __init__(
        self,
        ha_url: str,
        token: str,
        policy: HistoryFetchPolicy,
        *,
        request_json: Callable[[str, str, str, float], Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.ha_url = ha_url
        self.token = token
        self.policy = policy
        self.request_json = request_json or (
            lambda ha_url, token, path, timeout: ha_get_json(
                ha_url,
                token,
                path,
                timeout,
                max_response_bytes=policy.response_max_bytes,
            )
        )
        self.clock = clock
        self.sleep = sleep
        self.started_at = clock()
        self.deadline = self.started_at + policy.budget_seconds
        self.stats = HistoryFetchStats()

    def _remaining_seconds(self) -> float:
        return self.deadline - self.clock()

    def ensure_within_budget(self, stage: str) -> None:
        if self._remaining_seconds() <= 0:
            raise HistoryFetchExhausted(f"HA history wall-clock budget exhausted {stage}")

    def _reserve_request(self) -> float:
        self.ensure_within_budget("before request")
        remaining = self._remaining_seconds()
        if self.stats.attempts >= self.policy.max_requests:
            raise HistoryFetchExhausted("HA history request budget exhausted")
        self.stats.attempts += 1
        return min(self.policy.request_timeout_seconds, remaining)

    @staticmethod
    def _path(request: HistoryRequest) -> str:
        # HA recorder uses strict ``> start`` / ``< end`` filters. Pad both
        # bounds so an event exactly on an adjacent chunk boundary is included.
        # Only the first temporal chunk asks HA for its synthetic carry-in row.
        query_params = {
            "filter_entity_id": ",".join(request.entity_ids),
            "end_time": history_iso_z(request.end + timedelta(microseconds=1)),
            "minimal_response": "true",
            "no_attributes": "true",
            "significant_changes_only": "0",
        }
        if not request.include_initial_state:
            query_params["skip_initial_state"] = "true"
        query = urllib.parse.urlencode(query_params)
        padded_start = request.start - timedelta(microseconds=1)
        start = urllib.parse.quote(history_iso_z(padded_start), safe="")
        return f"/api/history/period/{start}?{query}"

    def _request_once(
        self,
        request: HistoryRequest,
        attempt: int,
    ) -> dict[str, list[tuple[datetime, str]]]:
        remaining_points = self.policy.max_points - self.stats.points
        if remaining_points <= 0:
            raise HistoryFetchExhausted("HA history decoded point budget exhausted")
        timeout = self._reserve_request()
        started = self.clock()
        LOG.info(
            "history_request_start key=%s parent=%s depth=%d attempt=%d entity_count=%d "
            "entity_hash=%s start=%s end=%s span_minutes=%.1f timeout_seconds=%.1f",
            request.key,
            request.parent_key or "root",
            request.depth,
            attempt,
            len(request.entity_ids),
            request.entity_fingerprint,
            iso_z(request.start),
            iso_z(request.end),
            request.span_minutes,
            timeout,
        )
        payload = self.request_json(self.ha_url, self.token, self._path(request), timeout)
        self.ensure_within_budget("after transport")
        parsed = _parse_history_payload(
            payload,
            set(request.entity_ids),
            max_points=remaining_points,
        )
        self.ensure_within_budget("after payload parsing")
        point_count = sum(len(points) for points in parsed.values())
        if self.stats.points + point_count > self.policy.max_points:
            raise HistoryFetchExhausted("HA history decoded point budget exhausted")
        self.stats.points += point_count
        LOG.info(
            "history_request_ok key=%s elapsed_ms=%d series=%d points=%d",
            request.key,
            round((self.clock() - started) * 1000),
            len(parsed),
            point_count,
        )
        return parsed

    @staticmethod
    def _retry_after_seconds(exc: Exception) -> float:
        if not isinstance(exc, urllib.error.HTTPError) or exc.code not in {429, 503}:
            return 0.0
        headers = exc.headers
        raw = headers.get("Retry-After") if headers is not None else None
        if raw is None:
            return 0.0
        try:
            requested = float(raw)
        except (TypeError, ValueError):
            return 0.0
        if not math.isfinite(requested) or requested <= 0:
            return 0.0
        return min(requested, 15.0)

    def _retry_delay(
        self,
        request: HistoryRequest,
        attempt: int,
        error_class: str,
        retry_after_seconds: float,
    ) -> None:
        delay = self.policy.retry_backoff_seconds * (2 ** (attempt - 1))
        delay = max(delay, retry_after_seconds)
        remaining = self._remaining_seconds()
        if remaining <= delay:
            raise HistoryFetchExhausted("HA history budget cannot accommodate retry delay")
        self.stats.retries += 1
        LOG.warning(
            "history_request_retry key=%s error_class=%s attempt=%d delay_seconds=%.1f",
            request.key,
            error_class,
            attempt,
            delay,
        )
        self.sleep(delay)
        self.ensure_within_budget("after retry delay")

    def _split_request(
        self,
        request: HistoryRequest,
        reason: str,
        destination: dict[str, dict[datetime, str]],
    ) -> None:
        if request.depth >= self.policy.max_split_depth:
            raise HistoryFetchExhausted("HA history maximum split depth exhausted")
        can_split_entities = len(request.entity_ids) > 1
        minimum_span = timedelta(minutes=self.policy.request_min_minutes)
        can_split_time = request.end - request.start >= minimum_span * 2
        if not can_split_entities and not can_split_time:
            raise HistoryFetchExhausted("HA history request failed at irreducible shape")

        time_pressure = request.span_minutes / self.policy.request_min_minutes
        split_entities = can_split_entities and (not can_split_time or len(request.entity_ids) >= time_pressure)
        if split_entities:
            midpoint = len(request.entity_ids) // 2
            children = (
                HistoryRequest(
                    request.entity_ids[:midpoint],
                    request.start,
                    request.end,
                    request.depth + 1,
                    request.key,
                    request.include_initial_state,
                ),
                HistoryRequest(
                    request.entity_ids[midpoint:],
                    request.start,
                    request.end,
                    request.depth + 1,
                    request.key,
                    request.include_initial_state,
                ),
            )
            axis = "entities"
        else:
            span_microseconds = (request.end - request.start) // timedelta(microseconds=1)
            midpoint = request.start + timedelta(microseconds=span_microseconds // 2)
            children = (
                HistoryRequest(
                    request.entity_ids,
                    request.start,
                    midpoint,
                    request.depth + 1,
                    request.key,
                    request.include_initial_state,
                ),
                HistoryRequest(
                    request.entity_ids,
                    midpoint,
                    request.end,
                    request.depth + 1,
                    request.key,
                    False,
                ),
            )
            axis = "time"
        self.stats.splits += 1
        LOG.warning(
            "history_request_split key=%s axis=%s reason=%s child_keys=%s,%s",
            request.key,
            axis,
            reason,
            children[0].key,
            children[1].key,
        )
        for child in children:
            self._fetch_request(child, destination)

    def _fetch_request(
        self,
        request: HistoryRequest,
        destination: dict[str, dict[datetime, str]],
    ) -> None:
        for attempt in range(1, self.policy.transport_retries + 2):
            decision: tuple[str, str, float] | None = None
            try:
                _merge_history_points(destination, self._request_once(request, attempt))
                return
            except HistoryFetchExhausted:
                raise
            except Exception as exc:
                action, error_class = _history_error_action(exc)
                retry_after_seconds = self._retry_after_seconds(exc)
                if isinstance(exc, urllib.error.HTTPError):
                    try:
                        exc.close()
                    except OSError:
                        pass
                if action == "fatal":
                    decision = ("fatal", error_class, 0.0)
                elif action == "split":
                    decision = ("split", error_class, 0.0)
                elif attempt <= self.policy.transport_retries:
                    decision = ("retry", error_class, retry_after_seconds)
                elif action == "retry_split":
                    decision = ("split", error_class, 0.0)
                else:
                    decision = ("exhausted", error_class, 0.0)

            # Retry and recursive split happen only after Python has cleared the
            # caught exception and traceback, which may retain response bytes.
            assert decision is not None
            next_action, error_class, retry_after_seconds = decision
            if next_action == "retry":
                self._retry_delay(request, attempt, error_class, retry_after_seconds)
                continue
            if next_action == "split":
                self._split_request(request, error_class, destination)
                return
            if next_action == "fatal":
                raise HistoryFetchExhausted(f"HA history request failed without retry ({error_class})") from None
            raise HistoryFetchExhausted(f"HA history request failed after bounded retry ({error_class})") from None
        raise AssertionError("unreachable history retry state")

    @staticmethod
    def _time_partitions(
        start: datetime,
        end: datetime,
        max_minutes: float,
    ) -> list[tuple[datetime, datetime, bool]]:
        partitions = []
        cursor = start
        maximum = timedelta(minutes=max_minutes)
        while cursor < end:
            child_end = min(end, cursor + maximum)
            partitions.append((cursor, child_end, not partitions))
            cursor = child_end
        return partitions or [(start, end, True)]

    def fetch(
        self,
        entity_ids: list[str] | tuple[str, ...],
        start: datetime,
        end: datetime,
        batch_size: int,
        *,
        prepartition_time: bool = True,
        window_key: str = "window",
    ) -> dict[str, HAHistory]:
        if start.tzinfo is None or start.utcoffset() is None or end.tzinfo is None or end.utcoffset() is None:
            raise ValueError("history bounds must be timezone-aware")
        if start >= end:
            raise ValueError("history start must be before end")
        requested_entities = tuple(entity_ids)
        if any(not isinstance(entity_id, str) or not entity_id for entity_id in requested_entities):
            raise ValueError("history entity IDs must be non-empty strings")
        entities = tuple(sorted(set(requested_entities)))
        if not entities:
            return {}
        if batch_size <= 0:
            raise ValueError("history batch_size must be positive")
        batches = [entities[offset : offset + batch_size] for offset in range(0, len(entities), batch_size)]
        intervals = (
            self._time_partitions(start, end, self.policy.request_max_minutes)
            if prepartition_time
            else [(start, end, True)]
        )
        LOG.info(
            "history_fetch_start window=%s start=%s end=%s entity_count=%d planned_leaves=%d "
            "remaining_requests=%d remaining_seconds=%.1f",
            window_key,
            iso_z(start),
            iso_z(end),
            len(entities),
            len(batches) * len(intervals),
            self.policy.max_requests - self.stats.attempts,
            self._remaining_seconds(),
        )
        started = self.clock()
        started_stats = HistoryFetchStats(
            attempts=self.stats.attempts,
            retries=self.stats.retries,
            splits=self.stats.splits,
            points=self.stats.points,
        )
        combined: dict[str, dict[datetime, str]] = {}
        try:
            for batch in batches:
                for interval_start, interval_end, include_initial_state in intervals:
                    request = HistoryRequest(
                        batch,
                        interval_start,
                        interval_end,
                        include_initial_state=include_initial_state,
                    )
                    self._fetch_request(request, combined)
            self.ensure_within_budget("at fetch completion")
            frozen = _freeze_history_points(combined)
            combined.clear()
            self.ensure_within_budget("after result freeze")
            histories = {entity_id: HAHistory(points) for entity_id, points in frozen.items()}
            self.ensure_within_budget("after history construction")
        except Exception as exc:
            combined.clear()
            LOG.error(
                "history_fetch_exhausted window=%s error_class=%s window_attempts=%d "
                "window_retries=%d window_splits=%d window_decoded_points=%d "
                "run_attempts=%d run_retries=%d run_splits=%d run_decoded_points=%d "
                "elapsed_seconds=%.1f",
                window_key,
                type(exc).__name__,
                self.stats.attempts - started_stats.attempts,
                self.stats.retries - started_stats.retries,
                self.stats.splits - started_stats.splits,
                self.stats.points - started_stats.points,
                self.stats.attempts,
                self.stats.retries,
                self.stats.splits,
                self.stats.points,
                self.clock() - started,
            )
            raise
        LOG.info(
            "history_fetch_complete window=%s window_attempts=%d window_retries=%d "
            "window_splits=%d entities=%d window_decoded_points=%d unique_points=%d "
            "run_attempts=%d run_retries=%d run_splits=%d run_decoded_points=%d "
            "elapsed_seconds=%.1f",
            window_key,
            self.stats.attempts - started_stats.attempts,
            self.stats.retries - started_stats.retries,
            self.stats.splits - started_stats.splits,
            len(frozen),
            self.stats.points - started_stats.points,
            sum(len(points) for points in frozen.values()),
            self.stats.attempts,
            self.stats.retries,
            self.stats.splits,
            self.stats.points,
            self.clock() - started,
        )
        return histories


def discover_earliest_ha(
    fetcher: HistoryFetcher,
    search_start: datetime,
    end: datetime,
) -> datetime | None:
    cursor = search_start
    while cursor < end:
        chunk_end = min(cursor + timedelta(days=30), end)
        histories = fetcher.fetch(
            list(REPRESENTATIVE_HA_ENTITIES),
            cursor,
            chunk_end,
            batch_size=len(REPRESENTATIVE_HA_ENTITIES),
            prepartition_time=False,
            window_key=f"earliest-{iso_z(cursor)}",
        )
        earliest = min((history.points[0][0] for history in histories.values() if history.points), default=None)
        if earliest:
            return earliest
        cursor = chunk_end
    return None


def build_db_dsn() -> str:
    direct = os.environ.get("VERDIFY_DB_DSN") or os.environ.get("DATABASE_URL")
    if direct:
        return direct
    host = os.environ.get("DB_HOST", "localhost")
    port = os.environ.get("DB_PORT", "5432")
    name = os.environ.get("DB_NAME", "verdify")
    user = os.environ.get("DB_USER", "verdify")
    password = os.environ.get("DB_PASSWORD") or os.environ.get("POSTGRES_PASSWORD") or os.environ.get("PGPASSWORD")
    if not password:
        raise RuntimeError("missing DB password env: expected DB_PASSWORD, POSTGRES_PASSWORD, or PGPASSWORD")
    return f"postgresql://{urllib.parse.quote(user)}:{urllib.parse.quote(password)}@{host}:{port}/{name}"


async def check_permissions(conn: asyncpg.Connection, apply: bool) -> None:
    required = ("SELECT", "INSERT") if apply else ("SELECT",)
    missing: list[str] = []
    for table in WRITE_TABLES:
        for privilege in required:
            ok = await conn.fetchval("SELECT has_table_privilege(current_user, $1, $2)", f"public.{table}", privilege)
            if not ok:
                missing.append(f"{privilege} public.{table}")
    if missing:
        raise RuntimeError("DB user lacks required privileges: " + ", ".join(missing))


async def table_columns(conn: asyncpg.Connection, table: str) -> set[str]:
    rows = await conn.fetch(
        """
        SELECT column_name
          FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name = $1
        """,
        table,
    )
    return {row["column_name"] for row in rows}


async def try_advisory_lock(conn: asyncpg.Connection) -> bool:
    return bool(await conn.fetchval("SELECT pg_try_advisory_lock(hashtext($1))", ADVISORY_LOCK_NAME))


async def release_advisory_lock(conn: asyncpg.Connection) -> None:
    await conn.execute("SELECT pg_advisory_unlock(hashtext($1))", ADVISORY_LOCK_NAME)


async def detect_sample_windows(
    conn: asyncpg.Connection,
    table: str,
    start: datetime,
    end: datetime,
    cadence_seconds: int,
    gap_seconds: int,
) -> list[Window]:
    first_expected = ceil_time(start, cadence_seconds)
    last_expected = floor_time(end, cadence_seconds)
    if first_expected > last_expected:
        return []
    rows = await conn.fetch(
        f"""
        SELECT DISTINCT to_timestamp(floor(extract(epoch from ts) / $3::double precision) * $3)::timestamptz AS bucket
          FROM {table}
         WHERE greenhouse_id = $4
           AND ts >= $1
           AND ts <= $2
         ORDER BY 1
        """,
        start,
        end,
        cadence_seconds,
        GREENHOUSE_ID,
    )
    buckets = [row["bucket"].astimezone(UTC) for row in rows]
    windows: list[Window] = []
    if not buckets:
        if (last_expected - first_expected).total_seconds() >= gap_seconds:
            windows.append(Window(first_expected, last_expected, (table,)))
        return windows

    first = buckets[0]
    if (first - first_expected).total_seconds() >= gap_seconds:
        windows.append(Window(first_expected, first - timedelta(seconds=cadence_seconds), (table,)))

    previous = first
    for bucket in buckets[1:]:
        if (bucket - previous).total_seconds() > gap_seconds:
            gap_start = previous + timedelta(seconds=cadence_seconds)
            gap_end = bucket - timedelta(seconds=cadence_seconds)
            if gap_start <= gap_end:
                windows.append(Window(gap_start, gap_end, (table,)))
        previous = bucket

    last = buckets[-1]
    if (last_expected - last).total_seconds() >= gap_seconds:
        windows.append(Window(last + timedelta(seconds=cadence_seconds), last_expected, (table,)))
    return windows


def merge_windows(windows: list[Window]) -> list[Window]:
    if not windows:
        return []
    ordered = sorted(windows, key=lambda w: (w.start, w.end))
    merged: list[Window] = [ordered[0]]
    for window in ordered[1:]:
        current = merged[-1]
        if window.start <= current.end + timedelta(seconds=60):
            reasons = tuple(sorted({*current.reasons, *window.reasons}))
            merged[-1] = Window(current.start, max(current.end, window.end), reasons)
        else:
            merged.append(window)
    return merged


def split_windows(windows: list[Window], max_minutes: float) -> list[Window]:
    max_delta = timedelta(minutes=max_minutes)
    result: list[Window] = []
    for window in windows:
        cursor = window.start
        while cursor <= window.end:
            boundary = min(window.end, cursor + max_delta)
            if boundary >= window.end:
                result.append(Window(cursor, window.end, window.reasons))
                break
            # HA event bounds are inclusive and timestamps have microsecond
            # precision. Partition at the exact boundary without overlap or a
            # representable gap; a +60s cursor would drop real transitions.
            result.append(Window(cursor, boundary - timedelta(microseconds=1), window.reasons))
            cursor = boundary
    return result


def full_scan_windows(start: datetime, end: datetime, max_minutes: float) -> list[Window]:
    return split_windows([Window(ceil_time(start, 60), floor_time(end, 60), ("full-scan",))], max_minutes)


def trailing_event_window(start: datetime, end: datetime, minutes: float) -> Window:
    if not math.isfinite(minutes) or minutes <= 0:
        raise ValueError("event reconcile minutes must be finite and positive")
    return Window(max(start, end - timedelta(minutes=minutes)), end, ("event-tail",))


async def detect_windows(
    conn: asyncpg.Connection, args: argparse.Namespace, start: datetime, end: datetime
) -> list[Window]:
    if args.full_scan:
        return full_scan_windows(start, end, args.max_backfill_minutes)

    gap_seconds = int(args.max_gap_minutes * 60)
    windows: list[Window] = []
    for table, cadence in (
        ("climate", args.sample_seconds),
        ("diagnostics", args.sample_seconds),
        ("setpoint_snapshot", args.sample_seconds),
        ("energy", args.energy_sample_seconds),
    ):
        threshold = max(gap_seconds, cadence * 2)
        windows.extend(await detect_sample_windows(conn, table, start, end, cadence, threshold))

    return split_windows(merge_windows(windows), args.max_backfill_minutes)


async def existing_buckets(
    conn: asyncpg.Connection,
    table: str,
    start: datetime,
    end: datetime,
    cadence_seconds: int,
) -> set[datetime]:
    rows = await conn.fetch(
        f"""
        SELECT DISTINCT to_timestamp(floor(extract(epoch from ts) / $3::double precision) * $3)::timestamptz AS bucket
          FROM {table}
         WHERE greenhouse_id = $4
           AND ts >= $1
           AND ts <= $2
        """,
        start,
        end,
        cadence_seconds,
        GREENHOUSE_ID,
    )
    return {row["bucket"].astimezone(UTC) for row in rows}


async def existing_setpoint_buckets(
    conn: asyncpg.Connection,
    start: datetime,
    end: datetime,
    cadence_seconds: int,
    parameters: list[str],
) -> set[tuple[str, datetime]]:
    if not parameters:
        return set()
    rows = await conn.fetch(
        """
        SELECT parameter,
               to_timestamp(floor(extract(epoch from ts) / $3::double precision) * $3)::timestamptz AS bucket
          FROM setpoint_snapshot
         WHERE greenhouse_id = $4
           AND ts >= $1
           AND ts <= $2
           AND parameter = ANY($5::text[])
         GROUP BY parameter, bucket
        """,
        start,
        end,
        cadence_seconds,
        GREENHOUSE_ID,
        parameters,
    )
    return {(row["parameter"], row["bucket"].astimezone(UTC)) for row in rows}


def convert_numeric(mapping: ScalarMapping, raw: str | None) -> float | int | None:
    value = to_float(raw)
    if value is None:
        return None
    if mapping.converter:
        converted = mapping.converter(value)
        if converted is None:
            return None
        return converted
    return value


async def insert_dict_rows(
    conn: asyncpg.Connection,
    table: str,
    columns: list[str],
    rows: list[dict[str, Any]],
    apply: bool,
) -> int:
    if not rows:
        return 0
    if not apply:
        return len(rows)
    values = [tuple(row.get(col) for col in columns) for row in rows]
    placeholders = ", ".join(f"${idx}" for idx in range(1, len(columns) + 1))
    cols_sql = ", ".join(columns)
    await conn.executemany(f"INSERT INTO {table} ({cols_sql}) VALUES ({placeholders})", values)
    return len(rows)


async def backfill_climate(
    conn: asyncpg.Connection,
    window: Window,
    histories: dict[str, HAHistory],
    mappings: MappingSet,
    columns: set[str],
    args: argparse.Namespace,
) -> int:
    available = [m for m in mappings.climate if m.target in columns]
    existing = await existing_buckets(conn, "climate", window.start, window.end, args.sample_seconds)
    rows: list[dict[str, Any]] = []
    for ts in iter_times(window.start, window.end, args.sample_seconds):
        bucket = floor_time(ts, args.sample_seconds)
        if bucket in existing:
            continue
        row: dict[str, Any] = {"ts": ts, "greenhouse_id": GREENHOUSE_ID}
        for mapping in available:
            history = histories.get(mapping.entity_id)
            if not history:
                continue
            value = convert_numeric(mapping, history.value_at(ts))
            if value is None:
                continue
            if mapping.target in {"moisture_center", "ph_runoff_center", "ec_runoff_center"}:
                value = normalize_feedback_value(mapping.target, value)
                if value is None:
                    continue
            row[mapping.target] = value
        if not any(row.get(core) is not None for core in ("temp_avg", "rh_avg", "vpd_avg")):
            continue
        try:
            ClimateRow.model_validate(row)
        except ValidationError as exc:
            LOG.warning("climate row skipped at %s: %s", iso_z(ts), exc)
            continue
        rows.append(row)
    if not rows:
        return 0
    insert_columns = [
        "ts",
        "greenhouse_id",
        *sorted({key for row in rows for key in row if key not in {"ts", "greenhouse_id"}}),
    ]
    return await insert_dict_rows(conn, "climate", insert_columns, rows, args.apply)


def diagnostic_value(column: str, raw: str | None, mapping: ScalarMapping) -> str | int | float | None:
    if is_bad_state(raw):
        return None
    if column in DIAGNOSTIC_TEXT_COLUMNS:
        return str(raw)
    value = convert_numeric(mapping, raw)
    if value is None:
        return None
    if column in DIAGNOSTIC_INT_COLUMNS:
        return int(value)
    return value


async def backfill_diagnostics(
    conn: asyncpg.Connection,
    window: Window,
    histories: dict[str, HAHistory],
    mappings: MappingSet,
    columns: set[str],
    args: argparse.Namespace,
) -> int:
    available = [m for m in mappings.diagnostics if m.target in columns]
    existing = await existing_buckets(conn, "diagnostics", window.start, window.end, args.sample_seconds)
    rows: list[dict[str, Any]] = []
    for ts in iter_times(window.start, window.end, args.sample_seconds):
        bucket = floor_time(ts, args.sample_seconds)
        if bucket in existing:
            continue
        row: dict[str, Any] = {"ts": ts, "greenhouse_id": GREENHOUSE_ID}
        for mapping in available:
            history = histories.get(mapping.entity_id)
            if not history:
                continue
            value = diagnostic_value(mapping.target, history.value_at(ts), mapping)
            if value is not None:
                row[mapping.target] = value
        if len(row) <= 2:
            continue
        try:
            Diagnostics.model_validate(row)
        except ValidationError as exc:
            LOG.warning("diagnostics row skipped at %s: %s", iso_z(ts), exc)
            continue
        rows.append(row)
    if not rows:
        return 0
    insert_columns = [
        "ts",
        "greenhouse_id",
        *sorted({key for row in rows for key in row if key not in {"ts", "greenhouse_id"}}),
    ]
    return await insert_dict_rows(conn, "diagnostics", insert_columns, rows, args.apply)


async def backfill_setpoints(
    conn: asyncpg.Connection,
    window: Window,
    histories: dict[str, HAHistory],
    mappings: MappingSet,
    args: argparse.Namespace,
) -> int:
    parameters = sorted({m.target for m in mappings.setpoints})
    existing = await existing_setpoint_buckets(conn, window.start, window.end, args.sample_seconds, parameters)
    rows: list[dict[str, Any]] = []
    for ts in iter_times(window.start, window.end, args.sample_seconds):
        bucket = floor_time(ts, args.sample_seconds)
        for mapping in mappings.setpoints:
            if (mapping.target, bucket) in existing:
                continue
            history = histories.get(mapping.entity_id)
            if not history:
                continue
            value = convert_numeric(mapping, history.value_at(ts))
            if value is None:
                continue
            row = {"ts": ts, "parameter": mapping.target, "value": float(value), "greenhouse_id": GREENHOUSE_ID}
            try:
                SetpointSnapshot.model_validate(row)
            except ValidationError:
                continue
            rows.append(row)
    return await insert_dict_rows(
        conn,
        "setpoint_snapshot",
        ["ts", "parameter", "value", "greenhouse_id"],
        rows,
        args.apply,
    )


async def backfill_energy(
    conn: asyncpg.Connection,
    window: Window,
    histories: dict[str, HAHistory],
    mappings: MappingSet,
    args: argparse.Namespace,
) -> int:
    existing = await existing_buckets(conn, "energy", window.start, window.end, args.energy_sample_seconds)
    rows: list[dict[str, Any]] = []
    by_target = {mapping.target: mapping for mapping in mappings.energy}
    for ts in iter_times(window.start, window.end, args.energy_sample_seconds):
        bucket = floor_time(ts, args.energy_sample_seconds)
        if bucket in existing:
            continue
        vals: dict[str, float | int] = {}
        for mapping in mappings.energy:
            history = histories.get(mapping.entity_id)
            if not history:
                continue
            value = convert_numeric(mapping, history.value_at(ts))
            if value is not None:
                vals[mapping.target] = value
        if not vals:
            continue
        ch0 = float(vals.get("ch0_power_w") or 0.0)
        ch1 = float(vals.get("ch1_power_w") or 0.0)
        row = {
            "ts": ts,
            "greenhouse_id": GREENHOUSE_ID,
            "watts_total": ch0 + ch1,
            "watts_heat": ch1,
            "watts_fans": 0.0,
            "watts_other": ch0,
            "kwh_today": vals.get("ch0_energy_kwh"),
        }
        if by_target and row["kwh_today"] is None and ch0 == 0.0 and ch1 == 0.0:
            continue
        try:
            EnergySample.model_validate(row)
        except ValidationError as exc:
            LOG.warning("energy row skipped at %s: %s", iso_z(ts), exc)
            continue
        rows.append(row)
    return await insert_dict_rows(
        conn,
        "energy",
        ["ts", "greenhouse_id", "watts_total", "watts_heat", "watts_fans", "watts_other", "kwh_today"],
        rows,
        args.apply,
    )


async def latest_equipment_states(conn: asyncpg.Connection, equipment: list[str], before: datetime) -> dict[str, bool]:
    if not equipment:
        return {}
    rows = await conn.fetch(
        """
        SELECT DISTINCT ON (equipment) equipment, state
          FROM equipment_state
         WHERE greenhouse_id = $1
           AND equipment = ANY($2::text[])
           AND ts < $3
         ORDER BY equipment, ts DESC
        """,
        GREENHOUSE_ID,
        equipment,
        before,
    )
    return {row["equipment"]: row["state"] for row in rows}


async def insert_equipment_event(
    conn: asyncpg.Connection,
    ts: datetime,
    equipment: str,
    state: bool,
    apply: bool,
) -> int:
    if not apply:
        return 1
    result = await conn.execute(
        """
        INSERT INTO equipment_state (ts, equipment, state, greenhouse_id)
        SELECT $1, $2, $3, $4
         WHERE NOT EXISTS (
             SELECT 1
               FROM equipment_state
              WHERE greenhouse_id = $4
                AND equipment = $2
                AND state = $3
                AND ts BETWEEN $1::timestamptz - interval '1 second' AND $1::timestamptz + interval '1 second'
         )
        """,
        ts,
        equipment,
        state,
        GREENHOUSE_ID,
    )
    return 1 if result.endswith("1") else 0


async def backfill_equipment(
    conn: asyncpg.Connection,
    window: Window,
    histories: dict[str, HAHistory],
    mappings: MappingSet,
    args: argparse.Namespace,
) -> int:
    latest = await latest_equipment_states(conn, sorted(set(mappings.equipment.values())), window.start)
    inserted = 0
    for entity_id, equipment in sorted(mappings.equipment.items()):
        history = histories.get(entity_id)
        if not history:
            continue
        last = latest.get(equipment)
        for ts, raw in history.events_between(window.start, window.end):
            state = to_bool(raw)
            if state is None or state == last:
                continue
            try:
                EquipmentStateEvent(ts=ts, equipment=equipment, state=state, greenhouse_id=GREENHOUSE_ID)
            except ValidationError as exc:
                LOG.warning("equipment event skipped at %s %s: %s", iso_z(ts), equipment, exc)
                continue
            inserted += await insert_equipment_event(conn, ts, equipment, state, args.apply)
            last = state
        latest[equipment] = last
    return inserted


async def latest_system_states(conn: asyncpg.Connection, entities: list[str], before: datetime) -> dict[str, str]:
    if not entities:
        return {}
    rows = await conn.fetch(
        """
        SELECT DISTINCT ON (entity) entity, value
          FROM system_state
         WHERE greenhouse_id = $1
           AND entity = ANY($2::text[])
           AND ts < $3
         ORDER BY entity, ts DESC
        """,
        GREENHOUSE_ID,
        entities,
        before,
    )
    return {row["entity"]: row["value"] for row in rows}


async def insert_system_event(
    conn: asyncpg.Connection,
    ts: datetime,
    entity: str,
    value: str,
    apply: bool,
) -> int:
    if not apply:
        return 1
    result = await conn.execute(
        """
        INSERT INTO system_state (ts, entity, value, greenhouse_id)
        SELECT $1, $2, $3, $4
         WHERE NOT EXISTS (
             SELECT 1
               FROM system_state
              WHERE greenhouse_id = $4
                AND entity = $2
                AND value = $3
                AND ts BETWEEN $1::timestamptz - interval '1 second' AND $1::timestamptz + interval '1 second'
         )
        """,
        ts,
        entity,
        value,
        GREENHOUSE_ID,
    )
    return 1 if result.endswith("1") else 0


async def backfill_system_state(
    conn: asyncpg.Connection,
    window: Window,
    histories: dict[str, HAHistory],
    mappings: MappingSet,
    args: argparse.Namespace,
) -> int:
    latest = await latest_system_states(conn, sorted(set(mappings.system_state.values())), window.start)
    inserted = 0
    for entity_id, entity in sorted(mappings.system_state.items()):
        history = histories.get(entity_id)
        if not history:
            continue
        last = latest.get(entity)
        for ts, value in history.events_between(window.start, window.end):
            if value == last:
                continue
            try:
                SystemStateRow(ts=ts, entity=entity, value=value, greenhouse_id=GREENHOUSE_ID)
            except ValidationError as exc:
                LOG.warning("system_state event skipped at %s %s: %s", iso_z(ts), entity, exc)
                continue
            inserted += await insert_system_event(conn, ts, entity, value, args.apply)
            last = value
        latest[entity] = last
    return inserted


async def backfill_window(
    conn: asyncpg.Connection,
    window: Window,
    mappings: MappingSet,
    history_fetcher: HistoryFetcher,
    args: argparse.Namespace,
    table_column_cache: dict[str, set[str]],
) -> Stats:
    stats = Stats(windows_seen=1)
    event_only = window.reasons == ("event-tail",)
    history_start = window.start - timedelta(minutes=args.history_carry_minutes)
    window_key = f"{iso_z(window.start)}--{iso_z(window.end)}"
    history_entities = sorted({*mappings.equipment, *mappings.system_state}) if event_only else mappings.all_entities
    histories = history_fetcher.fetch(
        history_entities,
        history_start,
        window.end,
        args.batch_size,
        window_key=window_key,
    )
    representative_ok = (
        bool(histories)
        if event_only
        else any(
            history.value_at(window.start) is not None
            or any(True for _ in history.events_between(window.start, window.end))
            for entity_id, history in histories.items()
            if entity_id in REPRESENTATIVE_HA_ENTITIES
        )
    )
    if not histories:
        stats.windows_without_ha += 1
        return stats

    budget_check = getattr(history_fetcher, "ensure_within_budget", None)
    if callable(budget_check):
        budget_check("before window transaction")
    apply_mode = bool(getattr(args, "apply", False))
    mode = "APPLY" if apply_mode else "DRY-RUN"
    LOG.info("window_transaction_begin window=%s mode=%s", window_key, mode)

    async def run_non_climate_writes() -> None:
        diagnostics_count = await backfill_diagnostics(
            conn,
            window,
            histories,
            mappings,
            table_column_cache["diagnostics"],
            args,
        )
        stats.add("diagnostics", diagnostics_count)
        stats.add("setpoint_snapshot", await backfill_setpoints(conn, window, histories, mappings, args))
        stats.add("energy", await backfill_energy(conn, window, histories, mappings, args))
        stats.add("equipment_state", await backfill_equipment(conn, window, histories, mappings, args))
        stats.add("system_state", await backfill_system_state(conn, window, histories, mappings, args))

    async def run_climate_write() -> None:
        climate_count = await backfill_climate(
            conn,
            window,
            histories,
            mappings,
            table_column_cache["climate"],
            args,
        )
        stats.add("climate", climate_count)

    write_timeout = float(getattr(args, "write_transaction_timeout_seconds", DEFAULT_WRITE_TRANSACTION_TIMEOUT_SECONDS))
    if not math.isfinite(write_timeout) or write_timeout <= 0:
        raise ValueError("write transaction timeout must be finite and positive")
    write_timeout_ms = max(1, math.ceil(write_timeout * 1000))
    try:
        async with conn.transaction():
            if event_only:
                stats.add("equipment_state", await backfill_equipment(conn, window, histories, mappings, args))
                stats.add("system_state", await backfill_system_state(conn, window, histories, mappings, args))
            elif apply_mode:
                # Do the potentially large diagnostics/setpoint/event batches
                # before taking the live climate writer's mutex. They remain in
                # this transaction and roll back if the fenced climate phase
                # fails.
                await run_non_climate_writes()
                # Install transaction-local server limits only after the
                # potentially large non-climate phase. They bound the lock,
                # climate statements, and COMMIT, and reclaim a client that
                # becomes idle while holding the xact lock. The asyncio timeout
                # below separately caps this pre-commit phase end to end.
                await conn.execute(f"SET LOCAL statement_timeout = {write_timeout_ms}")
                await conn.execute(f"SET LOCAL idle_in_transaction_session_timeout = {write_timeout_ms}")
                # The climate table has no unique logical-bucket constraint,
                # and the live ingestor may replay old rows from its durable
                # spool. The paired image takes this same transaction-scoped
                # advisory lock and rechecks the minute bucket on every climate
                # insert. Holding it before our existence checks serializes
                # both current and future spool replays; an older image lacks
                # the imported capability marker and the script fails closed.
                # Keep timeout inside the transaction so cancellation rolls
                # back before __aexit__ attempts COMMIT. The server-side local
                # statement timeout above independently bounds COMMIT itself.
                async with asyncio.timeout(write_timeout):
                    await conn.execute(CLIMATE_WRITE_FENCE_SQL, climate_write_fence_key(GREENHOUSE_ID))
                    LOG.info(
                        "window_write_fence_acquired window=%s contract=%d timeout_seconds=%.1f",
                        window_key,
                        BACKFILL_WRITE_FENCE_CONTRACT_VERSION,
                        write_timeout,
                    )
                    await run_climate_write()
            else:
                await run_non_climate_writes()
                await run_climate_write()
    except Exception:
        LOG.exception("window_transaction_rollback window=%s mode=%s", window_key, mode)
        raise
    stats.windows_committed = int(apply_mode)
    row_kind = "inserted_rows" if apply_mode else "candidate_rows"
    LOG.info(
        "window_transaction_complete window=%s mode=%s %s=%s",
        window_key,
        mode,
        row_kind,
        dict(sorted(stats.rows.items())),
    )

    row_count = sum(stats.rows.values())
    if row_count == 0 and not representative_ok:
        stats.windows_without_ha += 1
    elif row_count > 0:
        stats.windows_with_candidates += 1
        stats.windows_backfilled += int(apply_mode)
    return stats


def combine_stats(total: Stats, part: Stats) -> None:
    total.windows_seen += part.windows_seen
    total.windows_backfilled += part.windows_backfilled
    total.windows_without_ha += part.windows_without_ha
    total.windows_committed += part.windows_committed
    total.windows_with_candidates += part.windows_with_candidates
    for table, count in part.rows.items():
        total.rows[table] += count
    for key, count in part.skipped.items():
        total.skipped[key] += count


def compute_range(args: argparse.Namespace, history_fetcher: HistoryFetcher) -> tuple[datetime, datetime]:
    end = parse_time(args.end) if args.end else datetime.now(UTC) - timedelta(minutes=args.settle_minutes)
    if args.since_earliest_ha:
        search_start = parse_time(args.earliest_search_start)
        earliest = discover_earliest_ha(history_fetcher, search_start, end)
        if earliest is None:
            raise RuntimeError("could not find any representative Home Assistant greenhouse history")
        start = earliest
    elif args.start:
        start = parse_time(args.start)
    else:
        start = end - timedelta(days=args.lookback_days)
    if start >= end:
        raise RuntimeError(f"invalid range: start {iso_z(start)} is not before end {iso_z(end)}")
    return start, end


def require_settled_apply_end(apply: bool, end: datetime, *, now: datetime | None = None) -> None:
    if not apply:
        return
    observed_now = now or datetime.now(UTC)
    latest_safe_end = observed_now - timedelta(seconds=MIN_APPLY_SETTLE_SECONDS)
    if end > latest_safe_end:
        raise RuntimeError(
            f"apply end {iso_z(end)} is newer than the mandatory {int(MIN_APPLY_SETTLE_SECONDS)}s settle boundary"
        )


def require_apply_write_contract(apply: bool, sample_seconds: int) -> None:
    if apply and sample_seconds != CLIMATE_WRITE_BUCKET_SECONDS:
        raise RuntimeError(
            "apply sample cadence must match the shared "
            f"{CLIMATE_WRITE_BUCKET_SECONDS}s ingestor climate bucket contract"
        )


async def async_main() -> int:
    args = parse_args()
    require_apply_write_contract(args.apply, args.sample_seconds)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO), format="%(levelname)s %(message)s"
    )
    token = load_ha_token(args.ha_token_file)
    history_policy = HistoryFetchPolicy(
        request_timeout_seconds=args.history_request_timeout_seconds,
        transport_retries=args.history_transport_retries,
        retry_backoff_seconds=args.history_retry_backoff_seconds,
        request_max_minutes=args.history_request_max_minutes,
        request_min_minutes=args.history_request_min_minutes,
        max_requests=args.history_max_requests,
        budget_seconds=args.history_fetch_budget_seconds,
        max_split_depth=args.history_max_split_depth,
        max_points=args.history_max_points,
        response_max_bytes=args.history_response_max_bytes,
    )
    history_fetcher = HistoryFetcher(args.ha_url, token, history_policy)
    start, end = compute_range(args, history_fetcher)
    require_settled_apply_end(args.apply, end)
    mode = "APPLY" if args.apply else "DRY-RUN"
    LOG.info("%s scanning %s -> %s", mode, iso_z(start), iso_z(end))

    current_entities = fetch_current_entities(args.ha_url, token)
    mappings = build_mappings(current_entities)
    LOG.info(
        "mapped HA entities: climate=%d diagnostics=%d setpoints=%d equipment=%d system_state=%d energy=%d",
        len(mappings.climate),
        len(mappings.diagnostics),
        len(mappings.setpoints),
        len(mappings.equipment),
        len(mappings.system_state),
        len(mappings.energy),
    )

    conn = await asyncpg.connect(build_db_dsn(), timeout=15)
    try:
        await check_permissions(conn, args.apply)
        if not await try_advisory_lock(conn):
            LOG.warning("another %s run holds the advisory lock; exiting", ADVISORY_LOCK_NAME)
            return 0
        try:
            table_column_cache = {
                "climate": await table_columns(conn, "climate"),
                "diagnostics": await table_columns(conn, "diagnostics"),
            }
            windows = await detect_windows(conn, args, start, end)
            if args.max_windows and len(windows) > args.max_windows:
                LOG.warning("detected %d windows; processing first %d this run", len(windows), args.max_windows)
                windows = windows[: args.max_windows]
            if not windows:
                LOG.info("no sampled Verdify DB gaps found; processing the event reconciliation tail")
            windows.append(trailing_event_window(start, end, args.event_reconcile_minutes))
            LOG.info("processing %d window(s)", len(windows))
            total = Stats()
            for window in windows:
                LOG.info(
                    "window %s -> %s (%.1f min, reasons=%s)",
                    iso_z(window.start),
                    iso_z(window.end),
                    window.minutes,
                    ",".join(window.reasons),
                )
                try:
                    part = await backfill_window(
                        conn,
                        window,
                        mappings,
                        history_fetcher,
                        args,
                        table_column_cache,
                    )
                except Exception:
                    LOG.exception(
                        "run_failed committed_windows=%d failed_window=%s--%s",
                        total.windows_committed,
                        iso_z(window.start),
                        iso_z(window.end),
                    )
                    raise
                combine_stats(total, part)
            row_kind = "inserted_rows" if args.apply else "candidate_rows"
            LOG.info(
                "%s complete: windows=%d committed=%d candidates=%d backfilled=%d no_ha=%d %s=%s",
                mode,
                total.windows_seen,
                total.windows_committed,
                total.windows_with_candidates,
                total.windows_backfilled,
                total.windows_without_ha,
                row_kind,
                dict(sorted(total.rows.items())),
            )
        finally:
            await release_advisory_lock(conn)
    finally:
        await conn.close()
    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
