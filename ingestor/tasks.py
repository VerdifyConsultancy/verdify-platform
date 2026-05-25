"""
tasks.py — Periodic background tasks absorbed from standalone cron scripts.

Each function is an async coroutine that takes an asyncpg.Pool and runs one
unit of work. Called by task_loop() in ingestor.py on defined intervals.

Replaces 10 cron jobs with a single in-process task scheduler.
"""

import asyncio
import concurrent.futures
import json
import logging
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from datetime import timedelta as _td
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import asyncpg
import shared
from entity_map import FEEDBACK_VALUE_RANGES, normalize_feedback_value
from esp32_push import push_to_esp32
from occupancy import expire_occupancy_latch, sync_occupancy_state
from pydantic import ValidationError

from verdify_schemas import (
    AlertEnvelope,
    ClimateRow,
    EnergySample,
    EquipmentStateEvent,
    HAEntityState,
    OpenMeteoForecastResponse,
    PlanDeliveryLogRow,
    SetpointChange,
)
from verdify_schemas.tunable_registry import (
    CROP_BAND_REG,
    LEGACY_SHARED_LIGHTING_REG,
    LIGHTING_CIRCUIT_DEFAULT_REG,
    PLANNER_PUSHABLE_REG,
    REGISTRY,
    registry_value_error,
)
from verdify_schemas.tunable_registry import get as get_tunable

log = logging.getLogger("tasks")

# ── Shared config (from config.py / environment) ────────────────
from config import (
    EXPECTED_FIRMWARE_VERSION,
    EXPECTED_FIRMWARE_VERSION_FILE,
    GREENHOUSE_ID,
    HA_TOKEN_FILE,
    HA_URL,
    SLACK_CHANNEL,
    SLACK_TOKEN_FILE,
    STATE_DIR,
)
from config import (
    load_token as _load_token,
)

# Crop-band and lighting-policy params are dispatcher-owned read-only context.
# Import these sets from tunable_registry so planner/MCP/dispatcher ownership
# checks cannot drift apart.
LIGHTING_POLICY_PARAMS = LEGACY_SHARED_LIGHTING_REG
LIGHTING_CIRCUIT_DEFAULT_PARAMS = LIGHTING_CIRCUIT_DEFAULT_REG
LIGHTING_CIRCUIT_SUPPORT_SENTINELS = frozenset(
    name
    for name in LIGHTING_CIRCUIT_DEFAULT_PARAMS
    if name.endswith("_lux_threshold") or name.startswith(("sw_gl_main_", "sw_gl_grow_"))
)
LIGHTING_TARGET_MINUTE_PARAMS = frozenset(
    name for name in LIGHTING_CIRCUIT_DEFAULT_PARAMS if name.endswith("_target_light_minutes")
)
IRRIGATION_SCHEDULE_PARAMS = frozenset(
    {
        "irrig_wall_start_hour",
        "irrig_wall_start_min",
        "irrig_wall_duration_min",
        "irrig_wall_fert_duration_min",
        "irrig_wall_fert_every_n",
        "irrig_wall_days_mask",
        "irrig_wall_fert_days_mask",
        "irrig_wall_flush_min",
        "irrig_wall_interval_days",
        "irrig_center_start_hour",
        "irrig_center_start_min",
        "irrig_center_duration_min",
        "irrig_center_fert_duration_min",
        "irrig_center_fert_every_n",
        "irrig_center_days_mask",
        "irrig_center_fert_days_mask",
        "irrig_center_flush_min",
        "irrig_center_interval_days",
    }
)
HEAP_RECOVERY_PRIORITY_PARAMS = (
    frozenset(
        name
        for name in LIGHTING_CIRCUIT_DEFAULT_PARAMS
        if name.endswith(
            ("_target_light_minutes", "_sunrise_hour", "_sunset_hour", "_lux_threshold", "_lux_hysteresis")
        )
        or name.startswith(("sw_gl_main_", "sw_gl_grow_"))
    )
    | frozenset(
        {
            "activity_start_hour",
            "activity_start_minute",
            "activity_duration_min",
            "direct_wet_min_temp_f",
            "direct_wet_wall_start_offset_min",
            "direct_wet_wall_drydown_before_off_min",
            "direct_wet_south_start_offset_min",
            "direct_wet_south_drydown_before_off_min",
            "direct_wet_west_start_offset_min",
            "direct_wet_west_drydown_before_off_min",
            "direct_wet_center_start_offset_min",
            "direct_wet_center_drydown_before_off_min",
            "sw_direct_wet_gate_enabled",
        }
    )
    | IRRIGATION_SCHEDULE_PARAMS
)
SECOND_READBACK_ABS_TOLERANCE_PARAMS = frozenset(
    {
        "mister_engage_delay_s",
        "mister_all_delay_s",
        "mister_pulse_on_s",
        "mister_pulse_gap_s",
        "min_fog_on_s",
        "min_fog_off_s",
        "mist_backoff_s",
        "mist_max_closed_vent_s",
        "mist_thermal_relief_s",
        "vpd_watch_dwell_s",
    }
)
HOUSE_BAND_PARAMS = frozenset(
    name for name in CROP_BAND_REG if name.startswith("temp_") or name in {"vpd_low", "vpd_high"}
)
BAND_DRIVEN_PARAMS = CROP_BAND_REG | LIGHTING_POLICY_PARAMS
SAFETY_RAIL_PARAMS = frozenset(name for name, spec in REGISTRY.items() if spec.push_owner == "safety")

HOUSE_VPD_MIN_WIDTH_KPA = 0.55
HOUSE_VPD_LOW_MARGIN_KPA = 0.20
AIR_EXCHANGE_RELAY_STUCK_MODES = frozenset({"VENTILATE", "DEHUM_VENT", "THERMAL_RELIEF", "SAFETY_COOL"})
VPD_HIGH_GUARD_MARGIN_KPA = 0.02
VPD_MOISTURE_DEW_MARGIN_F = 7.0
VPD_MOISTURE_RECOVERY_WINDOW_MIN = 15
VPD_MOISTURE_RECOVERY_FRACTION = 0.50
VPD_DRY_AIR_OUTDOOR_RH_PCT = 25.0
VPD_VENT_FOG_ESCALATION_KPA = 0.20
VPD_HOT_DRY_FOG_ESCALATION_KPA = 0.15
VPD_VENT_MIN_FOG_OFF_S = 60.0
VPD_HOT_DRY_MIN_FOG_OFF_S = 45.0
VPD_HIGH_MOISTURE_GUARDRAIL_PARAMS = frozenset(
    {
        "mister_engage_kpa",
        "mister_all_kpa",
        "mister_engage_delay_s",
        "mister_all_delay_s",
        "mister_pulse_gap_s",
        "min_fog_off_s",
        "fog_escalation_kpa",
    }
)
GPU_POWER_EXPORTER_URL = os.environ.get("GPU_POWER_EXPORTER_URL", "http://192.168.30.105:9400/metrics")
GPU_POWER_HOST = os.environ.get("GPU_POWER_HOST", "cortex")
INFRA_TELEMETRY_GREENHOUSE_ID = os.environ.get("INFRA_TELEMETRY_GREENHOUSE_ID", "vallery")
GPU_POWER_EXPORTERS = (
    {
        "host": GPU_POWER_HOST,
        "vm_name": "vm-docker-ai",
        "purpose": "Iris/Hermes inference, embeddings, retrieval, and agent workloads",
        "url": os.environ.get("CORTEX_DCGM_EXPORTER_URL", GPU_POWER_EXPORTER_URL),
    },
    {
        "host": "sentinel",
        "vm_name": "vm-docker-frigate",
        "purpose": "Camera and vision inference for Frigate, greenhouse video, and visual evidence",
        "url": os.environ.get("SENTINEL_DCGM_EXPORTER_URL", "http://192.168.30.142:9400/metrics"),
    },
    {
        "host": "immich",
        "vm_name": "vm-docker-immich",
        "purpose": "Photo/media ML, CLIP search, and archive embeddings",
        "url": os.environ.get("IMMICH_DCGM_EXPORTER_URL", "http://192.168.30.108:9400/metrics"),
    },
)
CPU_EXPORTERS = (
    {
        "host": "iris",
        "vm_name": "vm-docker-iris",
        "purpose": "Verdify greenhouse ingestor, planner support, MCP, API, and site data jobs",
        "url": os.environ.get("IRIS_NODE_EXPORTER_URL", "http://192.168.30.150:9100/metrics"),
    },
    {
        "host": "cortex",
        "vm_name": "vm-docker-ai",
        "purpose": "Hermes planner, embeddings, retrieval, and agent workloads",
        "url": os.environ.get("CORTEX_NODE_EXPORTER_URL", "http://192.168.30.105:9100/metrics"),
    },
    {
        "host": "sentinel",
        "vm_name": "vm-docker-frigate",
        "purpose": "Camera ingest, Frigate, and vision workloads",
        "url": os.environ.get("SENTINEL_NODE_EXPORTER_URL", "http://192.168.30.142:9100/metrics"),
    },
    {
        "host": "web",
        "vm_name": "vm-docker-web",
        "purpose": "Public website publishing and edge-adjacent web jobs",
        "url": os.environ.get("WEB_NODE_EXPORTER_URL", "http://192.168.30.151:9100/metrics"),
    },
    {
        "host": "opal",
        "vm_name": "pve-opal",
        "purpose": "Proxmox host for the Cortex GPU VM",
        "url": os.environ.get("OPAL_NODE_EXPORTER_URL", "http://192.168.30.212:9100/metrics"),
    },
    {
        "host": "oro",
        "vm_name": "pve-oro",
        "purpose": "Proxmox host for Sentinel, Web, and GPU/edge workloads",
        "url": os.environ.get("ORO_NODE_EXPORTER_URL", "http://192.168.30.211:9100/metrics"),
    },
    {
        "host": "onyx",
        "vm_name": "pve-onyx",
        "purpose": "Proxmox host for Iris and HA-capable services",
        "url": os.environ.get("ONYX_NODE_EXPORTER_URL", "http://192.168.30.213:9100/metrics"),
    },
    {
        "host": "olivine",
        "vm_name": "pve-olivine",
        "purpose": "Proxmox management and quorum host",
        "url": os.environ.get("OLIVINE_NODE_EXPORTER_URL", "http://192.168.30.214:9100/metrics"),
    },
    {
        "host": "ore",
        "vm_name": "pve-ore",
        "purpose": "Proxmox host for the Immich GPU VM",
        "url": os.environ.get("ORE_NODE_EXPORTER_URL", "http://192.168.30.215:9100/metrics"),
    },
)
PROM_SAMPLE_RE = re.compile(
    r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{([^}]*)\})?\s+"
    r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?|[-+]?Inf|NaN)(?:\s|$)"
)
GPU_DCGM_METRICS = {
    "DCGM_FI_DEV_POWER_USAGE": "watts",
    "DCGM_FI_DEV_GPU_UTIL": "gpu_util_pct",
    "DCGM_FI_DEV_GPU_TEMP": "temperature_c",
    "DCGM_FI_DEV_FB_USED": "memory_used_mb",
    "DCGM_FI_DEV_FB_FREE": "memory_free_mb",
}


def _expected_firmware_version() -> str | None:
    """Return the firmware version pin alert_monitor should enforce."""
    if EXPECTED_FIRMWARE_VERSION.strip():
        return EXPECTED_FIRMWARE_VERSION.strip()
    path = Path(EXPECTED_FIRMWARE_VERSION_FILE)
    try:
        if path.exists():
            value = path.read_text().strip()
            return value or None
    except OSError as exc:
        log.warning("Could not read expected firmware version pin %s: %s", path, exc)
    return None


def _ha_state(states: dict[str, dict], eid: str) -> HAEntityState | None:
    """Validate a raw HA /api/states/{eid} payload into an HAEntityState.

    Returns None if the entity isn't in the batch or the payload fails schema
    validation (so callers can short-circuit rather than crashing the sync).
    """
    raw = states.get(eid)
    if not raw:
        return None
    try:
        return HAEntityState.model_validate(raw)
    except ValidationError as e:
        log.warning("HA entity %s failed schema validation: %s", eid, e)
        return None


def _parse_float(s) -> float | None:
    if s in ("unavailable", "unknown", "None", "", None):
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _fetch_ha_entity(token: str, entity_id: str) -> dict | None:
    """Fetch a single HA entity state (blocking — run in executor for batch)."""
    url = f"{HA_URL}/api/states/{entity_id}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def _fetch_ha_batch(token: str, entity_ids: list[str]) -> dict[str, dict]:
    """Fetch multiple HA entity states (blocking)."""
    results = {}
    for eid in entity_ids:
        data = _fetch_ha_entity(token, eid)
        if data:
            results[eid] = data
    return results


def _parse_prometheus_labels(label_blob: str) -> dict[str, str]:
    labels: dict[str, str] = {}
    key = []
    value = []
    in_value = False
    in_quote = False
    escaped = False
    current_key = ""
    for ch in label_blob:
        if in_value:
            if escaped:
                value.append(ch)
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_quote = not in_quote
            elif ch == "," and not in_quote:
                labels[current_key.strip()] = "".join(value)
                current_key = ""
                value = []
                in_value = False
            else:
                value.append(ch)
        elif ch == "=":
            current_key = "".join(key)
            key = []
            in_value = True
        elif ch == ",":
            key = []
        else:
            key.append(ch)
    if in_value and current_key:
        labels[current_key.strip()] = "".join(value)
    return labels


def _fetch_url_text(url: str, timeout: int = 8) -> str:
    req = urllib.request.Request(url, headers={"Accept": "text/plain"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _iter_prometheus_samples(body: str):
    for line in body.splitlines():
        if not line or line.startswith("#"):
            continue
        match = PROM_SAMPLE_RE.match(line)
        if not match:
            continue
        metric, label_blob, raw_value = match.groups()
        try:
            value = float(raw_value)
        except ValueError:
            continue
        if not math.isfinite(value):
            continue
        yield metric, _parse_prometheus_labels(label_blob or ""), value


def _fetch_gpu_power_samples(source: dict | None = None) -> list[dict]:
    source = source or GPU_POWER_EXPORTERS[0]
    body = _fetch_url_text(source["url"])
    by_gpu: dict[str, dict] = {}
    for metric, labels, value in _iter_prometheus_samples(body):
        field = GPU_DCGM_METRICS.get(metric)
        if not field:
            continue
        gpu = labels.get("gpu", "unknown")
        sample = by_gpu.setdefault(
            gpu,
            {
                "host": source["host"],
                "vm_name": source.get("vm_name"),
                "purpose": source.get("purpose"),
                "gpu": gpu,
                "device": labels.get("device"),
                "model_name": labels.get("modelName"),
                "raw": {},
            },
        )
        sample[field] = value
        sample["device"] = sample.get("device") or labels.get("device")
        sample["model_name"] = sample.get("model_name") or labels.get("modelName")
        sample["raw"][metric] = labels

    samples: list[dict] = []
    for sample in by_gpu.values():
        if sample.get("watts") is None:
            continue
        samples.append(
            {
                **sample,
                "watts": float(sample["watts"]),
            }
        )
    return samples


def _fetch_all_gpu_power_samples() -> list[dict]:
    samples: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(3, len(GPU_POWER_EXPORTERS))) as executor:
        future_to_source = {executor.submit(_fetch_gpu_power_samples, source): source for source in GPU_POWER_EXPORTERS}
        for future in concurrent.futures.as_completed(future_to_source):
            source = future_to_source[future]
            try:
                samples.extend(future.result())
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                log.warning("GPU power sync failed from %s (%s): %s", source["host"], source["url"], exc)
    return samples


def _extract_node_snapshot(body: str) -> dict:
    total_cpu = 0.0
    idle_cpu = 0.0
    cores: set[str] = set()
    load1: float | None = None
    mem_total: float | None = None
    mem_available: float | None = None

    for metric, labels, value in _iter_prometheus_samples(body):
        if metric == "node_cpu_seconds_total":
            total_cpu += value
            if labels.get("mode") == "idle":
                idle_cpu += value
            if labels.get("cpu"):
                cores.add(labels["cpu"])
        elif metric == "node_load1":
            load1 = value
        elif metric == "node_memory_MemTotal_bytes":
            mem_total = value
        elif metric == "node_memory_MemAvailable_bytes":
            mem_available = value

    memory_used_pct = None
    if mem_total and mem_available is not None and mem_total > 0:
        memory_used_pct = max(0.0, min(100.0, 100.0 * (1.0 - mem_available / mem_total)))

    return {
        "total_cpu": total_cpu,
        "idle_cpu": idle_cpu,
        "cores": len(cores) or None,
        "load1": load1,
        "memory_total_bytes": mem_total,
        "memory_available_bytes": mem_available,
        "memory_used_pct": memory_used_pct,
    }


def _fetch_cpu_sample(source: dict) -> dict:
    first = _extract_node_snapshot(_fetch_url_text(source["url"], timeout=5))
    time.sleep(1.0)
    second = _extract_node_snapshot(_fetch_url_text(source["url"], timeout=5))

    delta_total = second["total_cpu"] - first["total_cpu"]
    delta_idle = second["idle_cpu"] - first["idle_cpu"]
    cpu_util_pct = None
    if delta_total > 0:
        cpu_util_pct = max(0.0, min(100.0, 100.0 * (1.0 - delta_idle / delta_total)))

    return {
        "host": source["host"],
        "vm_name": source.get("vm_name"),
        "purpose": source.get("purpose"),
        "cpu_util_pct": cpu_util_pct,
        "load1": second.get("load1"),
        "cores": second.get("cores"),
        "memory_used_pct": second.get("memory_used_pct"),
        "raw": {
            "memory_total_bytes": second.get("memory_total_bytes"),
            "memory_available_bytes": second.get("memory_available_bytes"),
        },
    }


def _fetch_all_cpu_samples() -> list[dict]:
    samples: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(CPU_EXPORTERS))) as executor:
        future_to_source = {executor.submit(_fetch_cpu_sample, source): source for source in CPU_EXPORTERS}
        for future in concurrent.futures.as_completed(future_to_source):
            source = future_to_source[future]
            try:
                samples.append(future.result())
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                log.warning("CPU telemetry sync failed from %s (%s): %s", source["host"], source["url"], exc)
    return samples


def _post_slack(token: str, channel: str, text: str, thread_ts: str | None = None) -> str | None:
    payload = {"channel": channel, "text": text}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            return result.get("ts") if result.get("ok") else None
    except Exception:
        return None


# ═════════════════════════════════════════════════════════════════
# 1. WATER FLOWING + LEAK DETECTION (every 60s)
# ═════════════════════════════════════════════════════════════════
# IN-5 (Sprint 19): 10-minute dwell + reset hysteresis to kill the 68-flap/46h
# storm from the 96 h review. Hysteresis stops flow-noise-driven cycling between
# trigger (0.10 GPM) and clear; once fired, flow must drop below 0.08 GPM to
# clear. Dwell 10 ticks × 60 s = 10 min of sustained "flow with no valve open"
# before the state flips true.
LEAK_TRIGGER_GPM = 0.10
LEAK_CLEAR_GPM = 0.08  # 20% below trigger — reset hysteresis
LEAK_DWELL_TICKS = 10

_leak_counter = 0
_leak_state = False  # in-memory mirror of equipment_state.leak_detected


async def water_flowing_sync(pool: asyncpg.Pool) -> None:
    global _leak_counter, _leak_state
    async with pool.acquire() as conn:
        flow = await conn.fetchval("SELECT flow_gpm FROM climate WHERE flow_gpm IS NOT NULL ORDER BY ts DESC LIMIT 1")
        flow = float(flow) if flow is not None else 0.0

        # water_flowing
        flowing = flow > 0.05
        current = await conn.fetchval(
            "SELECT state FROM equipment_state WHERE equipment = 'water_flowing' ORDER BY ts DESC LIMIT 1"
        )
        if current is None or flowing != current:
            await conn.execute(
                "INSERT INTO equipment_state (ts, equipment, state) VALUES (NOW(), 'water_flowing', $1)", flowing
            )

        # leak_detected — hysteresis: use tighter clear threshold while latched
        effective_threshold = LEAK_CLEAR_GPM if _leak_state else LEAK_TRIGGER_GPM
        leak_candidate = False
        if flow > effective_threshold:
            valve_names = [
                "mister_south",
                "mister_west",
                "mister_center",
                "mister_any",
                "drip_wall",
                "drip_center",
                "mister_south_fert",
                "mister_west_fert",
                "drip_wall_fert",
                "drip_center_fert",
                "fert_master_valve",
            ]
            ph = ", ".join(f"${i + 1}" for i in range(len(valve_names)))
            any_open = await conn.fetchval(
                f"""
                SELECT bool_or(sub.state) FROM (
                    SELECT DISTINCT ON (equipment) state
                    FROM equipment_state WHERE equipment IN ({ph})
                    ORDER BY equipment, ts DESC
                ) sub
            """,
                *valve_names,
            )
            if not any_open:
                leak_candidate = True

        _leak_counter = (_leak_counter + 1) if leak_candidate else 0
        leak = _leak_counter >= LEAK_DWELL_TICKS

        current_leak = await conn.fetchval(
            "SELECT state FROM equipment_state WHERE equipment = 'leak_detected' ORDER BY ts DESC LIMIT 1"
        )
        if current_leak is None or leak != current_leak:
            await conn.execute(
                "INSERT INTO equipment_state (ts, equipment, state) VALUES (NOW(), 'leak_detected', $1)", leak
            )
        _leak_state = leak


# ═════════════════════════════════════════════════════════════════
# 2. MATERIALIZED VIEW REFRESH (every 300s)
# ═════════════════════════════════════════════════════════════════
async def matview_refresh(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("SELECT refresh_relay_stuck(0, '{}'::jsonb)")
        await conn.execute("SELECT refresh_climate_merged(0, '{}'::jsonb)")
        await conn.execute("SELECT refresh_greenhouse_state(0, '{}'::jsonb)")
    log.debug("Materialized views refreshed")


# ═════════════════════════════════════════════════════════════════
# 3. SHELLY ENERGY SYNC (every 300s)
# ═════════════════════════════════════════════════════════════════
_SHELLY_PREFIX = "sensor.shellyproem50_ac15186daafc_energy_meter"
_SHELLY_ENTITIES = {
    f"{_SHELLY_PREFIX}_0_power": ("ch0_power_w", None),
    f"{_SHELLY_PREFIX}_0_total_active_energy": ("ch0_energy_kwh", None),
    f"{_SHELLY_PREFIX}_0_current": ("ch0_current_a", None),
    f"{_SHELLY_PREFIX}_0_apparent_power": ("ch0_apparent_va", None),
    f"{_SHELLY_PREFIX}_1_power": ("ch1_power_w", lambda v: abs(v)),
    f"{_SHELLY_PREFIX}_1_apparent_power": ("ch1_apparent_va", None),
}


async def shelly_sync(pool: asyncpg.Pool) -> None:
    token = _load_token(HA_TOKEN_FILE)
    loop = asyncio.get_event_loop()
    states = await loop.run_in_executor(None, _fetch_ha_batch, token, list(_SHELLY_ENTITIES.keys()))
    if not states:
        return

    vals = {}
    for eid, (col, conv) in _SHELLY_ENTITIES.items():
        ha = _ha_state(states, eid)
        if ha is None:
            continue
        v = ha.as_float()
        if v is not None:
            vals[col] = conv(v) if conv else v
    if not vals:
        return

    ts = datetime.now(UTC)
    watts_total = vals.get("ch0_power_w", 0) + vals.get("ch1_power_w", 0)
    kwh_total = vals.get("ch0_energy_kwh") or 0
    try:
        sample = EnergySample(
            ts=ts,
            watts_total=watts_total,
            watts_heat=vals.get("ch1_power_w", 0),
            watts_fans=0,
            watts_other=vals.get("ch0_power_w", 0),
            kwh_today=kwh_total,
        )
    except ValidationError as e:
        log.error("Shelly sample failed schema validation: %s", e)
        return
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO energy (ts, watts_total, watts_heat, watts_fans, watts_other, kwh_today) VALUES ($1,$2,$3,$4,$5,$6)",
            sample.ts,
            sample.watts_total,
            sample.watts_heat,
            sample.watts_fans,
            sample.watts_other,
            sample.kwh_today,
        )
    log.debug("Shelly: %dW (ch0=%d ch1=%d)", watts_total, vals.get("ch0_power_w", 0), vals.get("ch1_power_w", 0))


async def gpu_power_sync(pool: asyncpg.Pool) -> None:
    """Mirror inference-fleet GPU telemetry from DCGM into TimescaleDB for public charts."""
    loop = asyncio.get_event_loop()
    samples = await loop.run_in_executor(None, _fetch_all_gpu_power_samples)
    if not samples:
        log.warning("GPU power sync found no DCGM_FI_DEV_POWER_USAGE samples")
        return

    ts = datetime.now(UTC)
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO gpu_power (
                ts, host, vm_name, purpose, gpu, device, model_name, watts,
                gpu_util_pct, temperature_c, memory_used_mb, memory_free_mb, source, raw, greenhouse_id
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, 'dcgm', $13::jsonb, $14)
            ON CONFLICT (greenhouse_id, ts, host, gpu) DO UPDATE SET
                vm_name = EXCLUDED.vm_name,
                purpose = EXCLUDED.purpose,
                device = EXCLUDED.device,
                model_name = EXCLUDED.model_name,
                watts = EXCLUDED.watts,
                gpu_util_pct = EXCLUDED.gpu_util_pct,
                temperature_c = EXCLUDED.temperature_c,
                memory_used_mb = EXCLUDED.memory_used_mb,
                memory_free_mb = EXCLUDED.memory_free_mb,
                source = EXCLUDED.source,
                raw = EXCLUDED.raw
            """,
            [
                (
                    ts,
                    s["host"],
                    s.get("vm_name"),
                    s.get("purpose"),
                    s["gpu"],
                    s.get("device"),
                    s.get("model_name"),
                    s["watts"],
                    s.get("gpu_util_pct"),
                    s.get("temperature_c"),
                    s.get("memory_used_mb"),
                    s.get("memory_free_mb"),
                    json.dumps(s.get("raw") or {}),
                    INFRA_TELEMETRY_GREENHOUSE_ID,
                )
                for s in samples
            ],
        )
    log.debug(
        "GPU power: %s",
        ", ".join(f"{s['host']}/gpu{s['gpu']}={s['watts']:.1f}W" for s in samples),
    )


async def infra_cpu_sync(pool: asyncpg.Pool) -> None:
    """Mirror public-safe CPU telemetry from node exporters into TimescaleDB."""
    loop = asyncio.get_event_loop()
    samples = await loop.run_in_executor(None, _fetch_all_cpu_samples)
    if not samples:
        log.warning("CPU telemetry sync found no node-exporter samples")
        return

    ts = datetime.now(UTC)
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO infra_cpu (
                ts, host, vm_name, purpose, cpu_util_pct, load1, cores,
                memory_used_pct, source, raw, greenhouse_id
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'node_exporter', $9::jsonb, $10)
            ON CONFLICT (greenhouse_id, ts, host) DO UPDATE SET
                vm_name = EXCLUDED.vm_name,
                purpose = EXCLUDED.purpose,
                cpu_util_pct = EXCLUDED.cpu_util_pct,
                load1 = EXCLUDED.load1,
                cores = EXCLUDED.cores,
                memory_used_pct = EXCLUDED.memory_used_pct,
                source = EXCLUDED.source,
                raw = EXCLUDED.raw
            """,
            [
                (
                    ts,
                    s["host"],
                    s.get("vm_name"),
                    s.get("purpose"),
                    s.get("cpu_util_pct"),
                    s.get("load1"),
                    s.get("cores"),
                    s.get("memory_used_pct"),
                    json.dumps(s.get("raw") or {}),
                    INFRA_TELEMETRY_GREENHOUSE_ID,
                )
                for s in samples
            ],
        )
    log.debug(
        "CPU telemetry: %s",
        ", ".join(
            f"{s['host']}={s['cpu_util_pct']:.1f}%" if s.get("cpu_util_pct") is not None else f"{s['host']}=n/a"
            for s in samples
        ),
    )


# ═════════════════════════════════════════════════════════════════
# 4. TEMPEST WEATHER SYNC (every 300s)
# ═════════════════════════════════════════════════════════════════
_TEMPEST_MAP = {
    "sensor.panorama_temperature": ("outdoor_temp_f", None),
    "sensor.panorama_humidity": ("outdoor_rh_pct", None),
    "sensor.panorama_wind_speed": ("wind_speed_mph", None),
    "sensor.panorama_wind_direction": ("wind_direction_deg", None),
    "sensor.panorama_illuminance": ("outdoor_lux", None),
    "sensor.panorama_irradiance": ("solar_irradiance_w_m2", None),
    "sensor.panorama_air_pressure": ("pressure_hpa", lambda v: v * 33.8639),
    "sensor.panorama_precipitation": ("precip_in", None),
    "sensor.panorama_uv_index": ("uv_index", None),
    "sensor.panorama_wind_gust": ("wind_gust_mph", None),
    "sensor.panorama_wind_lull": ("wind_lull_mph", None),
    "sensor.panorama_wind_speed_average": ("wind_speed_avg_mph", None),
    "sensor.panorama_wind_direction_average": ("wind_direction_avg_deg", None),
    "sensor.panorama_feels_like": ("feels_like_f", None),
    "sensor.panorama_wet_bulb_temperature": ("wet_bulb_temp_f", None),
    "sensor.panorama_vapor_pressure": ("vapor_pressure_inhg", None),
    "sensor.panorama_air_density": ("air_density_kg_m3", None),
    "sensor.panorama_precipitation_intensity": ("precip_intensity_in_h", None),
    "sensor.panorama_lightning_count": ("lightning_count", lambda v: int(v)),
    "sensor.panorama_lightning_average_distance": ("lightning_avg_dist_mi", None),
}


async def tempest_sync(pool: asyncpg.Pool) -> None:
    token = _load_token(HA_TOKEN_FILE)
    loop = asyncio.get_event_loop()
    states = await loop.run_in_executor(None, _fetch_ha_batch, token, list(_TEMPEST_MAP.keys()))
    if not states:
        return

    now = datetime.now(UTC)
    outdoor_cols = {}
    for eid, (col, conv) in _TEMPEST_MAP.items():
        ha = _ha_state(states, eid)
        if ha is None:
            continue
        val = ha.as_float()
        if val is not None:
            outdoor_cols[col] = conv(val) if conv else val
    if not outdoor_cols:
        return

    # Validate ranges on the outdoor columns before any DB write. ClimateRow
    # tolerates missing/extra columns (extra="ignore") and just enforces the
    # ge/le bounds on what it knows.
    try:
        ClimateRow.model_validate({"ts": now, **outdoor_cols})
    except ValidationError as e:
        log.error("Tempest outdoor_cols failed schema validation: %s", e)
        return

    async with pool.acquire() as conn:
        # Update ALL recent climate rows missing outdoor data (not just the latest)
        parts, vals = [], []
        for i, (c, v) in enumerate(outdoor_cols.items()):
            parts.append(f"{c} = ${i + 1}")
            vals.append(v)
        count = await conn.fetchval(
            "SELECT count(*) FROM climate WHERE ts > now() - interval '6 minutes' AND temp_avg IS NOT NULL AND outdoor_temp_f IS NULL"
        )
        if count and count > 0:
            result = await conn.execute(
                f"UPDATE climate SET {', '.join(parts)} WHERE ts > now() - interval '6 minutes' AND temp_avg IS NOT NULL AND outdoor_temp_f IS NULL",
                *vals,
            )
            log.debug("Tempest: %d outdoor cols synced to %s rows", len(outdoor_cols), result.split()[-1])
        elif not count or count == 0:
            # All recent rows already have outdoor data, update the latest one with freshest values
            latest = await conn.fetchval(
                "SELECT ts FROM climate WHERE ts > now() - interval '5 minutes' AND temp_avg IS NOT NULL ORDER BY ts DESC LIMIT 1"
            )
            if latest:
                vals.append(latest)
                await conn.execute(f"UPDATE climate SET {', '.join(parts)} WHERE ts = ${len(vals)}", *vals)
                log.debug("Tempest: %d outdoor cols refreshed on latest row", len(outdoor_cols))
            else:
                log.warning("Tempest: skipped climate overlay; no recent indoor climate row")


# ═════════════════════════════════════════════════════════════════
# 5. HA SENSOR SYNC — hydro, lights, switches, occupancy (every 300s)
# ═════════════════════════════════════════════════════════════════
_HYDRO_MAP = {
    # Read from HA template sensors that apply empirical scaling corrections
    # for the YINMIK meter's non-standard Tuya DP encoding (pH × 400, TDS × ½,
    # EC × 0.565). Corrections are defined in HA's
    # /config/packages/greenhouse/hydroponic_calibration.yaml — see the
    # haos agent if values drift or scaling needs adjustment. Temp + ORP +
    # battery DPs are unaffected and still read raw.
    "sensor.greenhouse_hydroponic_ec_corrected": ("hydro_ec_us_cm", None),
    "sensor.greenhouse_hydroponic_orp": ("hydro_orp_mv", None),
    "sensor.greenhouse_hydroponic_ph_corrected": ("hydro_ph", None),
    "sensor.greenhouse_hydroponic_tds_corrected": ("hydro_tds_ppm", None),
    "sensor.greenhouse_hydroponic_water_temp": ("hydro_water_temp_f", lambda v: v * 9.0 / 5.0 + 32.0),
    "sensor.greenhouse_hydroponic_yinmik_battery": ("hydro_battery_pct", None),
}
_CENTER_FEEDBACK_MAP = {
    # Pending physical center feedback. Keep HA entity aliases broad so a
    # sensor added outside the ESP32 path can begin populating climate rows
    # without another deploy.
    "sensor.greenhouse_center_soil_moisture": ("moisture_center", None),
    "sensor.greenhouse_center_root_zone_moisture": ("moisture_center", None),
    "sensor.greenhouse_center_root_zone_soil_moisture": ("moisture_center", None),
    "sensor.greenhouse_center_rootzone_moisture": ("moisture_center", None),
    "sensor.greenhouse_center_moisture": ("moisture_center", None),
    "sensor.greenhouse_center_vwc": ("moisture_center", None),
    "sensor.greenhouse_center_substrate_vwc": ("moisture_center", None),
    "sensor.greenhouse_center_substrate_moisture": ("moisture_center", None),
    "sensor.greenhouse_center_root_zone_vwc": ("moisture_center", None),
    "sensor.greenhouse_middle_substrate_vwc": ("moisture_center", None),
    "sensor.greenhouse_middle_substrate_moisture": ("moisture_center", None),
    "sensor.greenhouse_center_runoff_ph": ("ph_runoff_center", None),
    "sensor.greenhouse_center_runoff_p_h": ("ph_runoff_center", None),
    "sensor.greenhouse_center_run_off_ph": ("ph_runoff_center", None),
    "sensor.greenhouse_center_run_off_p_h": ("ph_runoff_center", None),
    "sensor.greenhouse_center_drain_ph": ("ph_runoff_center", None),
    "sensor.greenhouse_center_drain_p_h": ("ph_runoff_center", None),
    "sensor.greenhouse_center_drainage_ph": ("ph_runoff_center", None),
    "sensor.greenhouse_center_leachate_ph": ("ph_runoff_center", None),
    "sensor.greenhouse_center_effluent_ph": ("ph_runoff_center", None),
    "sensor.greenhouse_center_tray_ph": ("ph_runoff_center", None),
    "sensor.greenhouse_center_runoff_ec": ("ec_runoff_center", None),
    "sensor.greenhouse_center_runoff_ec_ms_cm": ("ec_runoff_center", None),
    "sensor.greenhouse_center_runoff_ec_us_cm": ("ec_runoff_center", None),
    "sensor.greenhouse_center_runoff_ec_u_s_cm": ("ec_runoff_center", None),
    "sensor.greenhouse_center_run_off_ec": ("ec_runoff_center", None),
    "sensor.greenhouse_center_run_off_ec_ms_cm": ("ec_runoff_center", None),
    "sensor.greenhouse_center_run_off_ec_us_cm": ("ec_runoff_center", None),
    "sensor.greenhouse_center_runoff_conductivity": ("ec_runoff_center", None),
    "sensor.greenhouse_center_runoff_electrical_conductivity": ("ec_runoff_center", None),
    "sensor.greenhouse_center_drain_ec": ("ec_runoff_center", None),
    "sensor.greenhouse_center_drain_ec_ms_cm": ("ec_runoff_center", None),
    "sensor.greenhouse_center_drain_ec_us_cm": ("ec_runoff_center", None),
    "sensor.greenhouse_center_drain_ec_u_s_cm": ("ec_runoff_center", None),
    "sensor.greenhouse_center_drainage_ec": ("ec_runoff_center", None),
    "sensor.greenhouse_center_leachate_ec": ("ec_runoff_center", None),
    "sensor.greenhouse_center_effluent_ec": ("ec_runoff_center", None),
    "sensor.greenhouse_center_tray_ec": ("ec_runoff_center", None),
}
_LIGHT_ENTITIES = {
    "switch.greenhouse_main": "grow_light_main",
    "switch.greenhouse_grow": "grow_light_grow",
}
_HA_SWITCHES = {
    "switch.greenhouse_economiser_enabled": "economiser_enabled",
    "switch.greenhouse_fog_closes_vent": "fog_closes_vent",
    "switch.greenhouse_irrigation_enabled": "irrigation_enabled",
    "switch.greenhouse_irrigation_wall_enabled": "irrigation_wall_enabled",
    "switch.greenhouse_irrigation_center_enabled": "irrigation_center_enabled",
    "switch.greenhouse_irrigation_weather_skip": "irrigation_weather_skip",
}
_OCCUPANCY_ENTITIES = {
    "binary_sensor.greenhouse_zone_person_occupancy": "occupancy",
}
_ha_prev_state: dict = {}
_HA_STATE_FILE = STATE_DIR / "ha-sensor-sync-state.json"


async def ha_sensor_sync(pool: asyncpg.Pool) -> None:
    global _ha_prev_state
    token = _load_token(HA_TOKEN_FILE)
    climate_sensor_map = {**_HYDRO_MAP, **_CENTER_FEEDBACK_MAP}
    all_eids = list(_LIGHT_ENTITIES) + list(climate_sensor_map) + list(_HA_SWITCHES) + list(_OCCUPANCY_ENTITIES)
    loop = asyncio.get_event_loop()
    states = await loop.run_in_executor(None, _fetch_ha_batch, token, all_eids)
    if not states:
        await expire_occupancy_latch(pool, "ha_sensor_sync")
        return

    # Load previous state on first run
    if not _ha_prev_state and _HA_STATE_FILE.exists():
        _ha_prev_state = json.loads(_HA_STATE_FILE.read_text())

    now = datetime.now(UTC)
    new_state = dict(_ha_prev_state)
    occupancy_observations: list[tuple[bool, datetime | None]] = []

    async with pool.acquire() as conn:
        # HA climate overlays → climate. Hydro values and optional center
        # feedback are merged into the latest ESP32 climate row.
        climate_cols = {}
        for eid, (col, conv) in climate_sensor_map.items():
            ha = _ha_state(states, eid)
            if ha is None:
                continue
            val = ha.as_float()
            if val is not None:
                normalized = conv(val) if conv else val
                if col in FEEDBACK_VALUE_RANGES:
                    normalized = normalize_feedback_value(col, normalized)
                    if normalized is None:
                        log.warning("HA feedback rejected invalid value: %s column=%s value=%r", eid, col, val)
                        continue
                climate_cols[col] = normalized
        if climate_cols:
            try:
                ClimateRow.model_validate({"ts": now, **climate_cols})
            except ValidationError as e:
                log.error("HA climate overlay failed schema validation: %s", e)
                climate_cols = {}
        if climate_cols:
            latest = await conn.fetchval(
                "SELECT ts FROM climate WHERE ts > now() - interval '5 minutes' AND temp_avg IS NOT NULL ORDER BY ts DESC LIMIT 1"
            )
            if latest:
                parts, vals = [], []
                for i, (c, v) in enumerate(climate_cols.items()):
                    parts.append(f"{c} = ${i + 1}")
                    vals.append(v)
                vals.append(latest)
                await conn.execute(f"UPDATE climate SET {', '.join(parts)} WHERE ts = ${len(vals)}", *vals)

        # Grow lights → equipment_state. Record every HA poll so lighting
        # traceability can prove physical state after OTA even if a relay held.
        for eid, equip in _LIGHT_ENTITIES.items():
            ha = _ha_state(states, eid)
            if ha is None:
                continue
            is_on = ha.state == "on"
            changed = new_state.get(eid) != is_on
            try:
                EquipmentStateEvent(ts=now, equipment=equip, state=is_on)
            except ValidationError as e:
                log.error("Light event skipped (validation failed: %s)", e)
                continue
            await conn.execute(
                "INSERT INTO equipment_state (ts, equipment, state) VALUES ($1, $2, $3)", now, equip, is_on
            )
            if changed:
                log.info("Light: %s → %s", equip, "ON" if is_on else "OFF")
            new_state[eid] = is_on

        # Config switches → equipment_state (on-change)
        for eid, equip in _HA_SWITCHES.items():
            ha = _ha_state(states, eid)
            if ha is None:
                continue
            is_on = ha.state == "on"
            key = f"switch_{equip}"
            if new_state.get(key) != is_on:
                try:
                    EquipmentStateEvent(ts=now, equipment=equip, state=is_on)
                except ValidationError as e:
                    log.error("Switch event skipped (validation failed: %s)", e)
                    continue
                await conn.execute(
                    "INSERT INTO equipment_state (ts, equipment, state) VALUES ($1, $2, $3)", now, equip, is_on
                )
            new_state[key] = is_on

        # Occupancy → latched system_state + ESP32 occupancy switch.
        # HA's ON state is stateful; repeat ON polls are not fresh Frigate
        # detections, so only ON transitions extend the latch. OFF is a
        # definitive empty observation and clears the latch on every poll.
        for eid, entity in _OCCUPANCY_ENTITIES.items():
            ha = _ha_state(states, eid)
            if ha is None or not ha.is_available:
                continue
            val = "occupied" if ha.state == "on" else "empty"
            key = f"occupancy_{entity}"
            if new_state.get(key) != val:
                log.info("Occupancy: %s (via HA)", val)
            if val == "empty":
                occupancy_observations.append((False, now))
            elif new_state.get(key) != val:
                occupancy_observations.append((True, ha.last_changed))
            new_state[key] = val

    for occupied, observed_at in occupancy_observations:
        await sync_occupancy_state(pool, occupied, "ha_sensor_sync", observed_at=observed_at)
    await expire_occupancy_latch(pool, "ha_sensor_sync")

    _ha_prev_state = new_state
    _HA_STATE_FILE.write_text(json.dumps(new_state))


# ═════════════════════════════════════════════════════════════════
# 6. ALERT MONITOR (every 300s)
# ═════════════════════════════════════════════════════════════════
async def alert_monitor(pool: asyncpg.Pool) -> None:
    try:
        await _expire_planner_trigger_slas(pool)
    except Exception as e:
        log.warning("planner trigger SLA lifecycle refresh failed: %s", e)

    async with pool.acquire() as conn:
        alerts = []

        # 1. sensor_offline (exclude legacy firmware entities)
        _STALE_EXCLUDE = {"state.mister_state", "state.mister_zone"}
        for r in await conn.fetch(
            "SELECT sensor_id, type, staleness_ratio FROM v_sensor_staleness WHERE is_stale = true"
        ):
            if r["sensor_id"] in _STALE_EXCLUDE:
                continue
            ratio = r["staleness_ratio"]
            alerts.append(
                {
                    "alert_type": "sensor_offline",
                    "severity": "warning",
                    "category": "sensor",
                    "sensor_id": r["sensor_id"],
                    "zone": None,
                    "message": f"Sensor `{r['sensor_id']}` offline ({ratio:.0f}x expected interval)"
                    if ratio
                    else f"Sensor `{r['sensor_id']}` offline",
                    "details": {"type": r["type"], "staleness_ratio": float(ratio) if ratio else None},
                    "metric_value": float(ratio) if ratio else None,
                    "threshold_value": None,
                }
            )

        # 2. relay_stuck
        # v_relay_stuck is derived from commanded switch state, not independent
        # relay feedback. Treat long heater runtime as normal when current
        # climate still demands heat; only alert when heat remains commanded
        # above the active band where it is physically contradictory.
        relay_context = await conn.fetchrow(
            """
            SELECT temp_avg, vpd_avg, sp_temp_high, sp_vpd_low, sp_vpd_high,
                   heat1, heat2, vent, fan1, fan2, greenhouse_mode, ts
              FROM v_greenhouse_state
             WHERE ts >= now() - interval '10 minutes'
             ORDER BY ts DESC
             LIMIT 1
            """
        )
        for r in await conn.fetch(
            "SELECT equipment, hours_on, threshold_hours FROM v_relay_stuck WHERE is_stuck = true"
        ):
            equipment = r["equipment"]
            details = {
                "hours_on": float(r["hours_on"]),
                "threshold_hours": float(r["threshold_hours"]),
                "state_source": "commanded_equipment_state",
            }
            if equipment in ("heat1", "heat2") and relay_context:
                temp_avg = relay_context["temp_avg"]
                sp_temp_high = relay_context["sp_temp_high"]
                heat_commanded = bool(relay_context[equipment])
                details.update(
                    {
                        "temp_avg": float(temp_avg) if temp_avg is not None else None,
                        "sp_temp_high": float(sp_temp_high) if sp_temp_high is not None else None,
                        "greenhouse_mode": relay_context["greenhouse_mode"],
                        "context_ts": relay_context["ts"].isoformat() if relay_context["ts"] else None,
                    }
                )
                if (
                    heat_commanded
                    and temp_avg is not None
                    and sp_temp_high is not None
                    and float(temp_avg) <= float(sp_temp_high) + 0.5
                ):
                    continue
                message = (
                    f"Heater `{equipment}` commanded ON for {r['hours_on']:.1f}h "
                    f"while temp is not below the active band"
                )
            elif equipment in ("vent", "fan1", "fan2") and relay_context:
                temp_avg = relay_context["temp_avg"]
                vpd_avg = relay_context["vpd_avg"]
                sp_temp_high = relay_context["sp_temp_high"]
                sp_vpd_low = relay_context["sp_vpd_low"]
                greenhouse_mode = (relay_context["greenhouse_mode"] or "").upper()
                relay_commanded = bool(relay_context[equipment])
                temp_demands_air_exchange = (
                    temp_avg is not None and sp_temp_high is not None and float(temp_avg) > float(sp_temp_high)
                )
                vpd_demands_dehum = (
                    vpd_avg is not None and sp_vpd_low is not None and float(vpd_avg) < float(sp_vpd_low)
                )
                details.update(
                    {
                        "temp_avg": float(temp_avg) if temp_avg is not None else None,
                        "vpd_avg": float(vpd_avg) if vpd_avg is not None else None,
                        "sp_temp_high": float(sp_temp_high) if sp_temp_high is not None else None,
                        "sp_vpd_low": float(sp_vpd_low) if sp_vpd_low is not None else None,
                        "sp_vpd_high": float(relay_context["sp_vpd_high"])
                        if relay_context["sp_vpd_high"] is not None
                        else None,
                        "greenhouse_mode": relay_context["greenhouse_mode"],
                        "context_ts": relay_context["ts"].isoformat() if relay_context["ts"] else None,
                    }
                )
                if relay_commanded and (
                    greenhouse_mode in AIR_EXCHANGE_RELAY_STUCK_MODES or temp_demands_air_exchange or vpd_demands_dehum
                ):
                    continue
                message = f"Relay `{equipment}` commanded ON for {r['hours_on']:.1f}h without current mode demand"
            else:
                message = f"Relay `{equipment}` commanded ON for {r['hours_on']:.1f}h without an OFF command"
            alerts.append(
                {
                    "alert_type": "relay_stuck",
                    "severity": "warning",
                    "category": "equipment",
                    "sensor_id": f"equipment.{equipment}",
                    "zone": None,
                    "message": message,
                    "details": details,
                    "metric_value": float(r["hours_on"]),
                    "threshold_value": float(r["threshold_hours"]),
                }
            )

        # 3. VPD stress
        # Daily cumulative stress belongs in the scorecard; an open alert
        # should represent a condition that is still active. Gate the daily
        # >2h threshold by the last 15 minutes so recovered VPD auto-resolves.
        row = await conn.fetchrow(
            """
            WITH daily AS (
                SELECT vpd_stress_hours::float AS vpd_stress_hours
                  FROM v_stress_hours_today
                 WHERE date >= date_trunc('day', now() AT TIME ZONE 'America/Denver')
                 ORDER BY date DESC
                 LIMIT 1
            ),
            recent AS (
                SELECT count(*)::int AS samples,
                       count(*) FILTER (WHERE vpd_avg > fn_setpoint_at('vpd_high', ts))::int AS high_samples,
                       avg(vpd_avg)::float AS avg_vpd,
                       avg(fn_setpoint_at('vpd_high', ts))::float AS avg_vpd_high
                  FROM climate
                 WHERE ts >= now() - interval '15 minutes'
                   AND vpd_avg IS NOT NULL
            )
            SELECT daily.vpd_stress_hours,
                   recent.samples,
                   recent.high_samples,
                   recent.avg_vpd,
                   recent.avg_vpd_high,
                   CASE WHEN recent.samples > 0
                        THEN recent.high_samples::float / recent.samples
                        ELSE 0.0
                   END AS recent_high_fraction
              FROM daily CROSS JOIN recent
            """
        )
        if (
            row
            and row["vpd_stress_hours"]
            and float(row["vpd_stress_hours"]) > 2.0
            and int(row["samples"] or 0) >= 3
            and float(row["recent_high_fraction"] or 0.0) >= 0.5
        ):
            hrs = float(row["vpd_stress_hours"])
            high_fraction = float(row["recent_high_fraction"] or 0.0)
            alerts.append(
                {
                    "alert_type": "vpd_stress",
                    "severity": "warning",
                    "category": "climate",
                    "sensor_id": "climate.vpd_avg",
                    "zone": None,
                    "message": f"VPD stress active: {hrs:.1f} hours today, {high_fraction:.0%} high in last 15m",
                    "details": {
                        "vpd_stress_hours": hrs,
                        "recent_samples": int(row["samples"] or 0),
                        "recent_high_samples": int(row["high_samples"] or 0),
                        "recent_high_fraction": high_fraction,
                        "avg_vpd_15m": float(row["avg_vpd"]) if row["avg_vpd"] is not None else None,
                        "avg_vpd_high_15m": float(row["avg_vpd_high"]) if row["avg_vpd_high"] is not None else None,
                    },
                    "metric_value": hrs,
                    "threshold_value": 2.0,
                }
            )

        # 3b. VPD-high while ventilating but moisture is not active. This is a
        # control-path alert: VPD demand exists, the firmware is in the mode that
        # should allow vent mist assist, and the relay surface is not carrying it.
        row = await conn.fetchrow(
            """
            WITH recent AS (
                SELECT *
                  FROM v_greenhouse_state
                 WHERE ts >= now() - interval '15 minutes'
            ),
            agg AS (
                SELECT count(*)::int AS samples,
                       count(*) FILTER (WHERE greenhouse_mode = 'VENTILATE')::int AS vent_samples,
                       count(*) FILTER (
                           WHERE greenhouse_mode = 'VENTILATE'
                             AND vpd_avg > sp_vpd_high
                             AND NOT (fog OR mist_south OR mist_west OR mist_center)
                       )::int AS high_no_moisture_samples,
                       avg(vpd_avg)::float AS avg_vpd,
                       avg(sp_vpd_high)::float AS avg_vpd_high,
                       avg(temp_avg)::float AS avg_temp,
                       avg(sp_temp_high)::float AS avg_temp_high,
                       avg(outdoor_temp_f)::float AS avg_outdoor_temp_f,
                       avg(outdoor_rh_pct)::float AS avg_outdoor_rh_pct,
                       count(*) FILTER (WHERE fog OR mist_south OR mist_west OR mist_center)::int
                           AS moisture_samples
                  FROM recent
            )
            SELECT *,
                   CASE WHEN samples > 0 THEN high_no_moisture_samples::float / samples ELSE 0.0 END
                       AS high_no_moisture_fraction,
                   CASE WHEN samples > 0 THEN moisture_samples::float / samples ELSE 0.0 END
                       AS moisture_fraction
              FROM agg
            """
        )
        if (
            row
            and int(row["samples"] or 0) >= 10
            and int(row["high_no_moisture_samples"] or 0) >= 10
            and float(row["high_no_moisture_fraction"] or 0.0) >= 0.60
        ):
            fraction = float(row["high_no_moisture_fraction"] or 0.0)
            alerts.append(
                {
                    "alert_type": "vent_vpd_moisture_gap",
                    "severity": "warning",
                    "category": "climate",
                    "sensor_id": "climate.vent_vpd_moisture",
                    "zone": None,
                    "message": f"VENTILATE VPD-high with no moisture assist in {fraction:.0%} of last 15m",
                    "details": {
                        "recent_minutes": 15,
                        "samples": int(row["samples"] or 0),
                        "vent_samples": int(row["vent_samples"] or 0),
                        "high_no_moisture_samples": int(row["high_no_moisture_samples"] or 0),
                        "high_no_moisture_fraction": fraction,
                        "moisture_fraction": float(row["moisture_fraction"] or 0.0),
                        "avg_vpd": float(row["avg_vpd"]) if row["avg_vpd"] is not None else None,
                        "avg_vpd_high": float(row["avg_vpd_high"]) if row["avg_vpd_high"] is not None else None,
                        "avg_temp": float(row["avg_temp"]) if row["avg_temp"] is not None else None,
                        "avg_temp_high": float(row["avg_temp_high"]) if row["avg_temp_high"] is not None else None,
                        "avg_outdoor_temp_f": float(row["avg_outdoor_temp_f"])
                        if row["avg_outdoor_temp_f"] is not None
                        else None,
                        "avg_outdoor_rh_pct": float(row["avg_outdoor_rh_pct"])
                        if row["avg_outdoor_rh_pct"] is not None
                        else None,
                    },
                    "metric_value": fraction,
                    "threshold_value": 0.60,
                }
            )

        # 3c. Moisture is active but the hot/dry air mass is still outside both
        # bands. This separates actuator timing bugs from physical capacity gaps.
        row = await conn.fetchrow(
            """
            WITH recent AS (
                SELECT *
                  FROM v_greenhouse_state
                 WHERE ts >= now() - interval '30 minutes'
            ),
            agg AS (
                SELECT count(*)::int AS samples,
                       count(*) FILTER (WHERE greenhouse_mode = 'VENTILATE')::int AS vent_samples,
                       count(*) FILTER (WHERE fog OR mist_south OR mist_west OR mist_center)::int
                           AS moisture_samples,
                       count(*) FILTER (
                           WHERE greenhouse_mode = 'VENTILATE'
                             AND (fog OR mist_south OR mist_west OR mist_center)
                             AND temp_avg > sp_temp_high
                             AND vpd_avg > sp_vpd_high
                       )::int AS capacity_limited_samples,
                       avg(temp_avg - sp_temp_high)::float AS avg_temp_excess_f,
                       max(temp_avg - sp_temp_high)::float AS max_temp_excess_f,
                       avg(vpd_avg - sp_vpd_high)::float AS avg_vpd_excess_kpa,
                       max(vpd_avg - sp_vpd_high)::float AS max_vpd_excess_kpa,
                       avg(outdoor_temp_f)::float AS avg_outdoor_temp_f,
                       avg(outdoor_rh_pct)::float AS avg_outdoor_rh_pct,
                       avg(solar_irradiance_w_m2)::float AS avg_solar_w_m2
                  FROM recent
            )
            SELECT *,
                   CASE WHEN samples > 0 THEN moisture_samples::float / samples ELSE 0.0 END
                       AS moisture_fraction,
                   CASE WHEN samples > 0 THEN capacity_limited_samples::float / samples ELSE 0.0 END
                       AS capacity_limited_fraction
              FROM agg
            """
        )
        if (
            row
            and int(row["samples"] or 0) >= 20
            and int(row["capacity_limited_samples"] or 0) >= 20
            and float(row["capacity_limited_fraction"] or 0.0) >= 0.67
        ):
            fraction = float(row["capacity_limited_fraction"] or 0.0)
            alerts.append(
                {
                    "alert_type": "vent_moisture_capacity_limit",
                    "severity": "warning",
                    "category": "climate",
                    "sensor_id": "climate.vent_moisture_capacity",
                    "zone": None,
                    "message": f"VENTILATE moisture assist active but temp+VPD remain high in {fraction:.0%} of last 30m",
                    "details": {
                        "recent_minutes": 30,
                        "samples": int(row["samples"] or 0),
                        "vent_samples": int(row["vent_samples"] or 0),
                        "moisture_samples": int(row["moisture_samples"] or 0),
                        "capacity_limited_samples": int(row["capacity_limited_samples"] or 0),
                        "capacity_limited_fraction": fraction,
                        "moisture_fraction": float(row["moisture_fraction"] or 0.0),
                        "avg_temp_excess_f": float(row["avg_temp_excess_f"])
                        if row["avg_temp_excess_f"] is not None
                        else None,
                        "max_temp_excess_f": float(row["max_temp_excess_f"])
                        if row["max_temp_excess_f"] is not None
                        else None,
                        "avg_vpd_excess_kpa": float(row["avg_vpd_excess_kpa"])
                        if row["avg_vpd_excess_kpa"] is not None
                        else None,
                        "max_vpd_excess_kpa": float(row["max_vpd_excess_kpa"])
                        if row["max_vpd_excess_kpa"] is not None
                        else None,
                        "avg_outdoor_temp_f": float(row["avg_outdoor_temp_f"])
                        if row["avg_outdoor_temp_f"] is not None
                        else None,
                        "avg_outdoor_rh_pct": float(row["avg_outdoor_rh_pct"])
                        if row["avg_outdoor_rh_pct"] is not None
                        else None,
                        "avg_solar_w_m2": float(row["avg_solar_w_m2"]) if row["avg_solar_w_m2"] is not None else None,
                    },
                    "metric_value": fraction,
                    "threshold_value": 0.67,
                }
            )

        # 4. Temp safety
        row = await conn.fetchrow(
            "SELECT ts, temp_avg FROM climate WHERE temp_avg IS NOT NULL AND ts >= now() - interval '10 minutes' ORDER BY ts DESC LIMIT 1"
        )
        if row and row["temp_avg"] is not None:
            t = row["temp_avg"]
            if t < 40:
                alerts.append(
                    {
                        "alert_type": "temp_safety",
                        "severity": "critical",
                        "category": "climate",
                        "sensor_id": "climate.temp_avg",
                        "zone": None,
                        "message": f"FREEZE WARNING — {t:.1f}°F",
                        "details": {"temp_f": t},
                        "metric_value": t,
                        "threshold_value": 40.0,
                    }
                )
            elif t > 100:
                alerts.append(
                    {
                        "alert_type": "temp_safety",
                        "severity": "critical",
                        "category": "climate",
                        "sensor_id": "climate.temp_avg",
                        "zone": None,
                        "message": f"OVERHEAT WARNING — {t:.1f}°F",
                        "details": {"temp_f": t},
                        "metric_value": t,
                        "threshold_value": 100.0,
                    }
                )

        # 4b. VPD extreme
        vpd_row = await conn.fetchrow(
            "SELECT vpd_avg FROM climate WHERE vpd_avg IS NOT NULL AND ts >= now() - interval '10 minutes' ORDER BY ts DESC LIMIT 1"
        )
        if vpd_row and vpd_row["vpd_avg"] is not None:
            v = vpd_row["vpd_avg"]
            if v < 0.3 or v > 3.0:
                alerts.append(
                    {
                        "alert_type": "vpd_extreme",
                        "severity": "warning",
                        "category": "climate",
                        "sensor_id": "climate.vpd_avg",
                        "zone": None,
                        "message": f"VPD {'low' if v < 0.3 else 'high'}: {v:.2f} kPa",
                        "details": {"vpd_kpa": v},
                        "metric_value": v,
                        "threshold_value": 0.3 if v < 0.3 else 3.0,
                    }
                )

        # 5. Leak
        row = await conn.fetchrow(
            "SELECT ts, state FROM equipment_state WHERE equipment = 'leak_detected' ORDER BY ts DESC LIMIT 1"
        )
        if row and row["state"]:
            alerts.append(
                {
                    "alert_type": "leak_detected",
                    "severity": "critical",
                    "category": "water",
                    "sensor_id": "equipment.leak_detected",
                    "zone": None,
                    "message": f"LEAK DETECTED since {row['ts'].strftime('%H:%M')} UTC",
                    "details": {"since": row["ts"].isoformat()},
                    "metric_value": None,
                    "threshold_value": None,
                }
            )

        # 6. ESP32 reboot
        # Sprint 19 followup: suppress the alert when uptime_s < 600 s because
        # an OTA is the expected reboot path and the alert auto-resolves anyway.
        # Only fires for unexpected reboots where the ESP32 is still rebooting
        # frequently (uptime < 300 s, which means a crash-loop scenario).
        row = await conn.fetchrow(
            "SELECT uptime_s, reset_reason FROM diagnostics WHERE ts >= now() - interval '10 minutes' AND uptime_s IS NOT NULL ORDER BY ts DESC LIMIT 1"
        )
        if row and row["uptime_s"] < 300:
            # Check if this is likely an OTA-induced reboot (reset_reason or recent deploy)
            reason = (row["reset_reason"] or "").lower()
            if "ota" in reason or "software" in reason:
                pass  # expected post-OTA reboot — no alert
            else:
                alerts.append(
                    {
                        "alert_type": "esp32_reboot",
                        "severity": "info",
                        "category": "system",
                        "sensor_id": "diag.uptime_s",
                        "zone": None,
                        "message": f"ESP32 rebooted — uptime {row['uptime_s']:.0f}s (reset_reason={reason or 'unknown'})",
                        "details": {"uptime_s": row["uptime_s"], "reset_reason": reason},
                        "metric_value": None,
                        "threshold_value": None,
                    }
                )

        # 7. Planner stale. Threshold 14h = SUNSET→SUNRISE gap (~12.7h) + 1.3h slack.
        # Iris emits full plans at SUNRISE and SUNSET only; interim TRANSITION /
        # FORECAST / DEVIATION events adjust tunables or trigger replans. An 8h
        # threshold (pre-sprint-2) guaranteed a daily false-positive mid-afternoon;
        # 14h fires only when a SUNRISE has genuinely missed. F14's severity
        # ladder (≥12h critical, else warning) is kept for AlertEnvelope dedup
        # structure but degenerates to always-critical at this threshold. This
        # rule will be superseded by contract v1.4's per-(type,instance) SLAs
        # in ingestor sprint-25; treat as an interim fix.
        plan_age = await conn.fetchval("SELECT EXTRACT(EPOCH FROM now() - MAX(created_at))::int FROM setpoint_plan")
        if plan_age and plan_age > 50400:
            age_h = plan_age / 3600.0
            severity = "critical" if age_h >= 12 else "warning"
            alerts.append(
                {
                    "alert_type": "planner_stale",
                    "severity": severity,
                    "category": "system",
                    "sensor_id": "system.planner",
                    "zone": None,
                    "message": f"No plan in {plan_age // 3600}h",
                    "details": {"age_s": plan_age, "age_h": round(age_h, 1)},
                    "metric_value": round(age_h, 1),
                    "threshold_value": 14.0,
                }
            )

        # 7b. planner_evaluation_missed — Phase 2 of Iris loop overhaul.
        # SUNRISE plans older than 26h with no validated_at. The SUNRISE
        # prompt declares plan_evaluate MANDATORY but the 2026-05-10 baseline
        # showed only 41.5% of SUNRISE plans get evaluated within 25h.
        # Warning at 26h, critical at 48h. This rule was originally drafted
        # in scripts/alert-monitor.py (Phase 2 PR) but the live alert engine
        # is this file — the rule lives here so it actually fires.
        eval_missed = await conn.fetch(
            """
            SELECT plan_id,
                   EXTRACT(EPOCH FROM (now() - created_at))::int AS age_seconds
              FROM plan_journal
             WHERE plan_id LIKE 'iris-%'
               AND validated_at IS NULL
               AND created_at < now() - interval '26 hours'
               AND EXTRACT(hour FROM created_at AT TIME ZONE 'America/Denver') BETWEEN 5 AND 9
             ORDER BY created_at
            """
        )
        for row in eval_missed:
            age_h = row["age_seconds"] // 3600
            severity = "critical" if row["age_seconds"] > 48 * 3600 else "warning"
            alerts.append(
                {
                    "alert_type": "planner_evaluation_missed",
                    "severity": severity,
                    "category": "system",
                    "sensor_id": "system.planner.evaluation",
                    "zone": None,
                    "message": (
                        f"SUNRISE plan {row['plan_id']} not evaluated ({age_h}h since created); "
                        f"plan_evaluate is MANDATORY per the SUNRISE prompt contract"
                    ),
                    "details": {"plan_id": row["plan_id"], "age_hours": age_h},
                    "metric_value": float(age_h),
                    "threshold_value": 26.0,
                }
            )

        # 7a. Planner gateway delivery failures. A failed Hermes POST is a
        # first-class outage, not a pending planner action. Keep the lookback
        # short so transient restarts auto-resolve once deliveries recover.
        gateway_failures = await conn.fetch(
            """
            WITH last_success AS (
                SELECT max(delivered_at) AS ts
                  FROM plan_delivery_log
                 WHERE delivered_at > now() - interval '2 hours'
                   AND gateway_status BETWEEN 200 AND 299
            )
            SELECT id, event_type, event_label, instance, gateway_status, delivered_at, gateway_body
              FROM plan_delivery_log, last_success
             WHERE delivered_at > now() - interval '2 hours'
               AND (last_success.ts IS NULL OR delivered_at > last_success.ts)
               AND (
                    status = 'delivery_failed'
                    OR gateway_status = 0
                    OR gateway_status >= 400
               )
             ORDER BY delivered_at DESC
             LIMIT 10
            """
        )
        if gateway_failures:
            failures = [
                {
                    "id": int(r["id"]),
                    "event_type": r["event_type"],
                    "event_label": r["event_label"],
                    "instance": r["instance"],
                    "gateway_status": int(r["gateway_status"]) if r["gateway_status"] is not None else None,
                    "delivered_at": r["delivered_at"].isoformat(),
                    "gateway_body": (r["gateway_body"] or "")[:300],
                }
                for r in gateway_failures
            ]
            required_failed = any(f["event_type"] in ("SUNRISE", "SUNSET", "MIDNIGHT") for f in failures)
            host_down = any(f["gateway_status"] == 0 for f in failures)
            severity = "critical" if required_failed or host_down or len(failures) >= 3 else "warning"
            first = failures[0]
            alerts.append(
                {
                    "alert_type": "planner_gateway_delivery_failed",
                    "severity": severity,
                    "category": "system",
                    "sensor_id": "system.hermes",
                    "zone": None,
                    "message": (
                        f"{len(failures)} planner gateway delivery failure(s) in 2h; "
                        f"latest {first['event_type']}/{first['event_label']} "
                        f"status={first['gateway_status']}"
                    ),
                    "details": {"failures": failures},
                    "metric_value": float(len(failures)),
                    "threshold_value": 0.0,
                }
            )

        # 7a.1. Per-trigger SLA timeouts. Gateway delivery may succeed but
        # never resolve to ack/plan_written; surface those rows with their
        # trigger id and gateway context instead of relying on flat
        # planner_stale.
        timed_out_deliveries = await conn.fetch(
            """
            SELECT id, event_type, event_label, instance, gateway_status, delivered_at,
                   gateway_body, trigger_id, hermes_run_id,
                   EXTRACT(EPOCH FROM (now() - delivered_at))::int AS elapsed_seconds
              FROM plan_delivery_log
             WHERE status = 'timed_out'
               AND delivered_at > now() - interval '6 hours'
             ORDER BY delivered_at DESC
             LIMIT 10
            """
        )
        if timed_out_deliveries:
            timeouts = [
                {
                    "id": int(r["id"]),
                    "event_type": r["event_type"],
                    "event_label": r["event_label"],
                    "instance": r["instance"],
                    "gateway_status": int(r["gateway_status"]) if r["gateway_status"] is not None else None,
                    "delivered_at": r["delivered_at"].isoformat() if r["delivered_at"] else None,
                    "gateway_body": (r["gateway_body"] or "")[:300],
                    "trigger_id": str(r["trigger_id"]) if r["trigger_id"] else None,
                    "elapsed_seconds": int(r["elapsed_seconds"] or 0),
                    "hermes_run_id": r["hermes_run_id"],
                }
                for r in timed_out_deliveries
            ]
            required_timed_out = any(t["event_type"] in ("SUNRISE", "SUNSET", "MIDNIGHT") for t in timeouts)
            severity = "critical" if required_timed_out else "warning"
            latest = timeouts[0]
            alerts.append(
                {
                    "alert_type": "planner_trigger_sla_timeout",
                    "severity": severity,
                    "category": "system",
                    "sensor_id": "system.planner_trigger_sla",
                    "zone": None,
                    "message": (
                        f"{len(timeouts)} planner trigger SLA timeout(s) in 6h; "
                        f"latest {latest['event_type']}/{latest['event_label']} "
                        f"trigger_id={latest['trigger_id']} elapsed={latest['elapsed_seconds']}s "
                        f"gateway_status={latest['gateway_status']}"
                    ),
                    "details": {"timeouts": timeouts},
                    "metric_value": float(len(timeouts)),
                    "threshold_value": 0.0,
                }
            )

        # 7b. Required SUNRISE/SUNSET/MIDNIGHT plans. planner_trigger_ledger is
        # materialized before delivery, so this catches both failure modes:
        # delivered-but-no-plan and no delivery row at all.
        required_misses = await conn.fetch(
            """
            WITH latest_required AS (
                SELECT id, event_type, event_label, instance, status, expected_at, due_at,
                       delivered_at, plan_delivery_log_id, trigger_id, resulting_plan_id, notes,
                       row_number() OVER (PARTITION BY event_type ORDER BY expected_at DESC) AS rn
                 FROM planner_trigger_ledger
                 WHERE event_type IN ('SUNRISE', 'SUNSET', 'MIDNIGHT')
                   AND expected_at > now() - interval '36 hours'
                   AND event_label NOT ILIKE 'validation%ack-only%'
            )
            SELECT id, event_type, event_label, instance, status, expected_at, due_at,
                   delivered_at, plan_delivery_log_id, trigger_id, resulting_plan_id, notes
              FROM latest_required
             WHERE rn = 1
               AND status <> 'plan_written'
               AND due_at < now()
             ORDER BY delivered_at DESC
            """
        )
        if required_misses:
            misses = [
                {
                    "id": int(r["id"]),
                    "event_type": r["event_type"],
                    "event_label": r["event_label"],
                    "instance": r["instance"],
                    "status": r["status"],
                    "gateway_status": None,
                    "expected_at": r["expected_at"].isoformat(),
                    "due_at": r["due_at"].isoformat(),
                    "delivered_at": r["delivered_at"].isoformat() if r["delivered_at"] else None,
                    "gateway_body": (r["notes"] or "")[:300],
                    "plan_delivery_log_id": int(r["plan_delivery_log_id"])
                    if r["plan_delivery_log_id"] is not None
                    else None,
                    "trigger_id": str(r["trigger_id"]) if r["trigger_id"] else None,
                    "resulting_plan_id": r["resulting_plan_id"],
                }
                for r in required_misses
            ]
            latest = misses[0]
            alerts.append(
                {
                    "alert_type": "planner_required_plan_missed",
                    "severity": "critical",
                    "category": "system",
                    "sensor_id": "system.planner_required_plan",
                    "zone": None,
                    "message": (
                        f"{latest['event_type']} did not produce a plan by SLA "
                        f"(status={latest['status']}, due={latest['due_at']})"
                    ),
                    "details": {"misses": misses},
                    "metric_value": float(len(misses)),
                    "threshold_value": 0.0,
                }
            )

        # 7c. Planner ownership drift. Crop-band and lighting-policy params
        # are dispatcher-owned read-only context; active rows in setpoint_plan
        # can outrank the DB policy functions and create repeated clamp storms.
        band_owned_rows = await conn.fetch(
            """
            SELECT parameter,
                   coalesce(plan_id, '<null>') AS plan_id,
                   coalesce(source, '<null>') AS source,
                   count(*)::int AS rows
              FROM setpoint_plan
             WHERE is_active = true
               AND parameter = ANY($1::text[])
             GROUP BY parameter, coalesce(plan_id, '<null>'), coalesce(source, '<null>')
             ORDER BY parameter, plan_id, source
            """,
            sorted(BAND_DRIVEN_PARAMS),
        )
        if band_owned_rows:
            offenders = [
                {
                    "parameter": r["parameter"],
                    "plan_id": r["plan_id"],
                    "source": r["source"],
                    "rows": int(r["rows"]),
                }
                for r in band_owned_rows
            ]
            total_rows = sum(r["rows"] for r in offenders)
            sample = ", ".join(f"{r['parameter']}:{r['plan_id']}({r['rows']})" for r in offenders[:4])
            alerts.append(
                {
                    "alert_type": "planner_band_ownership_drift",
                    "severity": "critical",
                    "category": "system",
                    "sensor_id": "system.planner_band_ownership",
                    "zone": None,
                    "message": (f"{total_rows} active planner row(s) contain dispatcher-owned policy params: {sample}"),
                    "details": {
                        "band_owned_params": [
                            "temp_low",
                            "temp_high",
                            "vpd_low",
                            "vpd_high",
                            "vpd_target_south",
                            "vpd_target_west",
                            "vpd_target_east",
                            "vpd_target_center",
                            "gl_dli_target",
                            "gl_sunrise_hour",
                            "gl_sunset_hour",
                            "sw_gl_auto_mode",
                        ],
                        "offenders": offenders,
                    },
                    "metric_value": float(total_rows),
                    "threshold_value": 0.0,
                }
            )

        # 7d. Active/future tunable range drift. MCP validates writes before
        # insertion, but this guard checks the live schedule the dispatcher will
        # actually read. It catches direct SQL/manual rows, stale pre-registry
        # plans, and forced-on switches that would otherwise revive legacy
        # controller behavior.
        candidate_rows = await conn.fetch(
            """
            SELECT ts, parameter, value, plan_id, source, reason
              FROM setpoint_plan
             WHERE is_active = true
               AND source IN ('iris', 'plan')
             ORDER BY ts, parameter
             LIMIT 10000
            """
        )
        tunable_violations = []
        for r in candidate_rows:
            parameter = r["parameter"]
            value = float(r["value"])
            error = registry_value_error(parameter, value)
            if parameter in FORCED_ON_SWITCH_PARAMS and value < 0.5:
                error = "controller_locked_on: unified controller rollback requires firmware/config rollback"
            if not error:
                continue
            tunable_violations.append(
                {
                    "parameter": parameter,
                    "value": value,
                    "plan_id": r["plan_id"],
                    "source": r["source"],
                    "ts": r["ts"].isoformat(),
                    "reason": (r["reason"] or "")[:200],
                    "error": error,
                }
            )
        if tunable_violations:
            sample = ", ".join(f"{v['parameter']}={v['value']:g} ({v['plan_id']})" for v in tunable_violations[:4])
            alerts.append(
                {
                    "alert_type": "planner_tunable_range_drift",
                    "severity": "critical",
                    "category": "system",
                    "sensor_id": "system.planner_tunable_range",
                    "zone": None,
                    "message": f"{len(tunable_violations)} active/future planner tunable violation(s): {sample}",
                    "details": {"violations": tunable_violations[:20]},
                    "metric_value": float(len(tunable_violations)),
                    "threshold_value": 0.0,
                }
            )

        # 7e. Future plan horizon guard. Validation smoke must never leave the
        # production planner with only a current-past waypoint surface; SUNRISE /
        # SUNSET/MIDNIGHT are expected to maintain future non-oneshot waypoints.
        horizon = await conn.fetchrow(
            """
            WITH future AS (
                SELECT count(*)::int AS future_waypoints
                  FROM setpoint_plan
                 WHERE is_active = true
                   AND ts > now()
                   AND source IN ('iris', 'plan')
                   AND plan_id NOT LIKE 'iris-oneshot-%'
            ),
            latest AS (
                SELECT plan_id, max(created_at) AS created_at, max(ts) AS latest_waypoint_ts
                  FROM setpoint_plan
                 WHERE is_active = true
                   AND source IN ('iris', 'plan')
                   AND plan_id NOT LIKE 'iris-oneshot-%'
                 GROUP BY plan_id
                 ORDER BY max(created_at) DESC NULLS LAST
                 LIMIT 1
            ),
            next_required AS (
                SELECT event_type, due_at
                  FROM planner_trigger_ledger
                 WHERE event_type IN ('SUNRISE', 'SUNSET', 'MIDNIGHT')
                   AND status IN ('expected', 'delivered')
                   AND due_at >= now() - interval '2 hours'
                 ORDER BY due_at
                 LIMIT 1
            )
            SELECT future.future_waypoints,
                   latest.plan_id,
                   latest.created_at,
                   latest.latest_waypoint_ts,
                   next_required.event_type AS next_required_event_type,
                   next_required.due_at AS next_required_due_at
              FROM future
              LEFT JOIN latest ON true
              LEFT JOIN next_required ON true
            """
        )
        if horizon and int(horizon["future_waypoints"] or 0) == 0:
            next_due = horizon["next_required_due_at"]
            severity = "critical" if next_due and next_due < datetime.now(UTC) else "warning"
            alerts.append(
                {
                    "alert_type": "planner_plan_horizon_missing",
                    "severity": severity,
                    "category": "system",
                    "sensor_id": "system.planner_plan_horizon",
                    "zone": None,
                    "message": "Planner has no active future non-oneshot setpoint_plan waypoints",
                    "details": {
                        "active_plan_id": horizon["plan_id"],
                        "active_plan_created_at": horizon["created_at"].isoformat() if horizon["created_at"] else None,
                        "latest_waypoint_ts": horizon["latest_waypoint_ts"].isoformat()
                        if horizon["latest_waypoint_ts"]
                        else None,
                        "future_waypoints": 0,
                        "next_required_event_type": horizon["next_required_event_type"],
                        "next_required_due_at": next_due.isoformat() if next_due else None,
                    },
                    "metric_value": 0.0,
                    "threshold_value": 1.0,
                }
            )

        # 8. Safety value sanity check — catch zeroed/invalid safety rails
        for r in await conn.fetch(
            """
            SELECT DISTINCT ON (parameter) parameter, value
            FROM setpoint_snapshot
            WHERE parameter = ANY($1::text[])
              AND ts > now() - interval '5 minutes'
            ORDER BY parameter, ts DESC
        """,
            sorted(SAFETY_RAIL_PARAMS),
        ):
            val = r["value"]
            param = r["parameter"]
            spec = REGISTRY[param]
            lo = spec.fw_clamp_lo
            hi = spec.fw_clamp_hi
            is_invalid = val is None or (lo is not None and val < lo) or (hi is not None and val > hi)
            if is_invalid:
                alerts.append(
                    {
                        "alert_type": "safety_invalid",
                        "severity": "critical",
                        # "system" per AlertCategory literal; semantic
                        # "safety" subtype is Sprint 25's discriminated union.
                        "category": "system",
                        "sensor_id": f"setpoint.{param}",
                        "zone": None,
                        "message": f"CRITICAL: {param}={val} is invalid — state machine safety compromised",
                        "details": {"parameter": param, "value": val},
                        "metric_value": float(val) if val else 0,
                        "threshold_value": None,
                    }
                )

        # 9. Heat manual override
        heat = await conn.fetchrow("SELECT AVG(watts_heat) AS w FROM energy WHERE ts > now() - interval '10 minutes'")
        if heat and heat["w"] and heat["w"] > 1000:
            h1 = await conn.fetchval(
                "SELECT state FROM equipment_state WHERE equipment = 'heat1' ORDER BY ts DESC LIMIT 1"
            )
            h2 = await conn.fetchval(
                "SELECT state FROM equipment_state WHERE equipment = 'heat2' ORDER BY ts DESC LIMIT 1"
            )
            if not h1 and not h2:
                alerts.append(
                    {
                        "alert_type": "heat_manual_override",
                        "severity": "warning",
                        "category": "equipment",
                        "sensor_id": "equipment.heat1",
                        "zone": None,
                        "message": f"Heat drawing {int(heat['w'])}W but ESP32 reports OFF",
                        "details": {"watts": int(heat["w"])},
                        "metric_value": float(heat["w"]),
                        "threshold_value": 1000.0,
                    }
                )

        # 9. Soil sensor offline (daytime only, 6AM–10PM MDT)
        local_hour = datetime.now(ZoneInfo("America/Denver")).hour
        if 6 <= local_hour < 22:
            soil_cols = [
                ("soil_moisture_south_1", "soil.south_1"),
                ("soil_temp_south_1", "soil.south_1"),
                ("soil_ec_south_1", "soil.south_1"),
                ("soil_moisture_south_2", "soil.south_2"),
                ("soil_temp_south_2", "soil.south_2"),
                ("soil_moisture_west", "soil.west"),
                ("soil_temp_west", "soil.west"),
            ]
            for col, sensor_id in soil_cols:
                non_null = await conn.fetchval(
                    f"SELECT COUNT(*) FROM climate WHERE ts >= now() - interval '30 minutes' AND {col} IS NOT NULL"
                )
                if non_null == 0:
                    alerts.append(
                        {
                            "alert_type": "soil_sensor_offline",
                            "severity": "warning",
                            "category": "sensor",
                            "sensor_id": f"{sensor_id}.{col}",
                            "zone": None,
                            "message": f"Soil sensor `{col}` has no data for 30 min",
                            "details": {"column": col, "sensor": sensor_id},
                            "metric_value": None,
                            "threshold_value": 30.0,
                        }
                    )

        # 9b. Irrigation feedback gaps: south probe stuck-zero and center
        # root-zone/runoff feedback missing/stale. The status view owns the
        # physical sensor semantics; alert lifecycle follows status != ok.
        for r in await conn.fetch(
            """
            SELECT feedback_key,
                   zone,
                   signal,
                   status,
                   latest_value,
                   last_sample_ts,
                   required_action,
                   details
              FROM v_irrigation_sensor_feedback_status
             WHERE status <> 'ok'
             ORDER BY zone, signal
            """
        ):
            latest_value = r["latest_value"]
            view_details = r["details"] or {}
            if isinstance(view_details, str):
                try:
                    view_details = json.loads(view_details)
                except json.JSONDecodeError:
                    view_details = {"raw": view_details}
            elif not isinstance(view_details, dict):
                view_details = {"raw": str(view_details)}
            alerts.append(
                {
                    "alert_type": "irrigation_feedback_gap",
                    "severity": "warning",
                    "category": "sensor",
                    "sensor_id": f"irrigation.feedback.{r['feedback_key']}",
                    "zone": r["zone"],
                    "message": (f"Irrigation feedback `{r['signal']}` is {r['status']}: {r['required_action']}"),
                    "details": {
                        "feedback_key": r["feedback_key"],
                        "signal": r["signal"],
                        "status": r["status"],
                        "latest_value": float(latest_value) if latest_value is not None else None,
                        "last_sample_ts": r["last_sample_ts"].isoformat() if r["last_sample_ts"] else None,
                        "required_action": r["required_action"],
                        "view_details": view_details,
                    },
                    "metric_value": float(latest_value) if latest_value is not None else None,
                    "threshold_value": None,
                }
            )

        # 10. Heating staging inversion (heat2 ON without heat1)
        staging_row = await conn.fetchrow("SELECT * FROM fn_heat_staging_inversion()")
        if staging_row:
            dur = staging_row["duration_s"]
            alerts.append(
                {
                    "alert_type": "heat_staging_inversion",
                    "severity": "warning",
                    "category": "equipment",
                    "sensor_id": "equipment.heat2",
                    "zone": None,
                    "message": (
                        f"STAGING INVERSION: heat2 (gas) ON for {dur:.0f}s while heat1 (electric) OFF. "
                        f"Temp={staging_row['temp_avg']:.1f}°F, "
                        f"Tlow={staging_row['temp_low']:.1f}°F"
                    ),
                    "details": {
                        "heat2_on_since": staging_row["heat2_on_since"].isoformat(),
                        "duration_s": dur,
                        "temp_avg": float(staging_row["temp_avg"]) if staging_row["temp_avg"] else None,
                        "temp_low": float(staging_row["temp_low"]) if staging_row["temp_low"] else None,
                        "d_heat_stage_2": float(staging_row["d_heat_stage_2"])
                        if staging_row["d_heat_stage_2"]
                        else None,
                    },
                    "metric_value": dur,
                    "threshold_value": 60.0,
                }
            )

        # 11. OBS-3 coverage (Sprint 25-omnibus): firmware breaker state.
        # Sprint 18 added relief_cycle_count + vent_latch_timer_s to
        # diagnostics but alert_monitor didn't read them, so the planner had
        # no warning before firmware force-latched VENTILATE. Thresholds
        # chosen against the firmware default max_relief_cycles=3 (range 1–10
        # per greenhouse_types.h:171); if the planner raises the cap, these
        # warn a touch early but don't misfire.
        obs3_row = await conn.fetchrow(
            """
            SELECT relief_cycle_count, vent_latch_timer_s, ts
              FROM diagnostics
             WHERE ts >= now() - interval '5 minutes'
               AND (relief_cycle_count IS NOT NULL OR vent_latch_timer_s IS NOT NULL)
             ORDER BY ts DESC LIMIT 1
            """
        )
        # OBS-3 cooldown (Tier 2b): firmware_relief_ceiling and
        # firmware_vent_latched flap rapidly when the metric oscillates near
        # threshold during stress windows. After a recent auto-resolve, hold
        # off re-firing for 10 min so the alert log + Slack don't accumulate
        # the same incident as 17+ separate rows/day. The auto-resolve loop
        # below still clears genuinely-cleared alerts on the same monitor pass.
        relief_recent_resolve = await conn.fetchval(
            """
            SELECT 1 FROM alert_log
             WHERE alert_type = 'firmware_relief_ceiling'
               AND disposition = 'resolved'
               AND resolved_at > now() - interval '10 minutes'
             LIMIT 1
            """
        )
        latch_recent_resolve = await conn.fetchval(
            """
            SELECT 1 FROM alert_log
             WHERE alert_type = 'firmware_vent_latched'
               AND disposition = 'resolved'
               AND resolved_at > now() - interval '10 minutes'
             LIMIT 1
            """
        )
        if obs3_row:
            relief = obs3_row["relief_cycle_count"]
            if relief is not None and relief >= 2 and not relief_recent_resolve:
                # Warning at ceiling-1 (nearing); critical at ceiling (3) or beyond.
                severity = "critical" if relief >= 3 else "warning"
                alerts.append(
                    {
                        "alert_type": "firmware_relief_ceiling",
                        "severity": severity,
                        "category": "equipment",
                        "sensor_id": "diag.relief_cycle_count",
                        "zone": None,
                        "message": (
                            f"Firmware relief_cycle_count={relief} "
                            f"({'at/past' if relief >= 3 else 'nearing'} default ceiling=3; "
                            f"VENTILATE force-latch {'active' if relief >= 3 else 'imminent'})"
                        ),
                        "details": {"relief_cycle_count": int(relief), "ceiling_default": 3},
                        "metric_value": float(relief),
                        "threshold_value": 3.0,
                    }
                )
            latch = obs3_row["vent_latch_timer_s"]
            if latch is not None and latch >= 600 and not latch_recent_resolve:
                # Warning at 10 min latched; critical at 20 min (schema max 1800s).
                severity = "critical" if latch >= 1200 else "warning"
                alerts.append(
                    {
                        "alert_type": "firmware_vent_latched",
                        "severity": severity,
                        "category": "equipment",
                        "sensor_id": "diag.vent_latch_timer_s",
                        "zone": None,
                        "message": (
                            f"Firmware vent latched for {latch}s "
                            f"({'critical' if latch >= 1200 else 'prolonged'}; "
                            f"planner hasn't resolved the stress that triggered it)"
                        ),
                        "details": {"vent_latch_timer_s": int(latch)},
                        "metric_value": float(latch),
                        "threshold_value": 600.0,
                    }
                )

        # 12. Firmware version mismatch. The deploy path writes
        # STATE_DIR/expected-firmware-version only after sensor-health accepts
        # an OTA. If diagnostics later report a different build, the operator
        # may be validating the wrong binary or an out-of-band OTA happened.
        expected_fw = _expected_firmware_version()
        if expected_fw:
            latest_fw = await conn.fetchrow(
                """
                SELECT firmware_version, ts
                  FROM diagnostics
                 WHERE ts >= now() - interval '10 minutes'
                   AND firmware_version IS NOT NULL
                 ORDER BY ts DESC
                 LIMIT 1
                """
            )
            live_fw = latest_fw["firmware_version"] if latest_fw else None
            if live_fw and live_fw != expected_fw:
                alerts.append(
                    {
                        "alert_type": "firmware_version_mismatch",
                        "severity": "warning",
                        "category": "system",
                        "sensor_id": "diag.firmware_version",
                        "zone": None,
                        "message": (f"ESP32 firmware_version={live_fw} does not match expected pin {expected_fw}"),
                        "details": {
                            "expected_firmware_version": expected_fw,
                            "live_firmware_version": live_fw,
                            "diagnostics_ts": latest_fw["ts"].isoformat() if latest_fw else None,
                            "pin_source": EXPECTED_FIRMWARE_VERSION_FILE
                            if not EXPECTED_FIRMWARE_VERSION.strip()
                            else "EXPECTED_FIRMWARE_VERSION",
                        },
                        "metric_value": None,
                        "threshold_value": None,
                    }
                )

        # 13. ESP32 heap pressure watchdogs. Firmware publishes debounced
        # binary sensors; route them into alert_log so heap exhaustion has the
        # same lifecycle and Slack path as the other system-owned alerts.
        heap_resolution_rows = await conn.fetch(
            """
            SELECT sensor_id, max(resolved_at) AS resolved_at
              FROM alert_log
             WHERE disposition = 'resolved'
               AND resolved_at IS NOT NULL
               AND alert_type IN ('heap_pressure_warning', 'heap_pressure_critical')
               AND sensor_id IN ('equipment.heap_pressure_warning', 'equipment.heap_pressure_critical')
             GROUP BY sensor_id
            """
        )
        heap_event_floor = {row["sensor_id"]: row["resolved_at"] for row in heap_resolution_rows}
        heap_critical_floor = heap_event_floor.get("equipment.heap_pressure_critical")
        heap_warning_floor = heap_event_floor.get("equipment.heap_pressure_warning")
        heap_rows = await conn.fetch(
            """
            SELECT equipment,
                   (array_agg(state ORDER BY ts DESC, state ASC))[1] AS latest_state,
                   max(ts) AS latest_ts,
                   bool_or(state) FILTER (WHERE ts > now() - interval '30 minutes') AS recent_true,
                   max(ts) FILTER (WHERE state) AS last_true_ts
              FROM equipment_state
             WHERE equipment IN ('heap_pressure_warning', 'heap_pressure_critical')
               AND (
                   (equipment = 'heap_pressure_critical' AND ts > COALESCE($1, '-infinity'::timestamptz))
                   OR (equipment = 'heap_pressure_warning' AND ts > COALESCE($2, '-infinity'::timestamptz))
               )
             GROUP BY equipment
            """,
            heap_critical_floor,
            heap_warning_floor,
        )
        heap_log = await conn.fetchrow(
            """
            SELECT count(*) FILTER (
                       WHERE message ILIKE '%Heap pressure CRITICAL%'
                   ) AS critical_logs,
                   max(ts) FILTER (
                       WHERE message ILIKE '%Heap pressure CRITICAL%'
                   ) AS last_critical_ts,
                   (array_agg(message ORDER BY ts DESC) FILTER (
                       WHERE message ILIKE '%Heap pressure CRITICAL%'
                   ))[1] AS last_critical_message,
                   count(*) FILTER (
                       WHERE message ILIKE '%Heap pressure WARNING%'
                   ) AS warning_logs,
                   max(ts) FILTER (
                       WHERE message ILIKE '%Heap pressure WARNING%'
                   ) AS last_warning_ts,
                   (array_agg(message ORDER BY ts DESC) FILTER (
                       WHERE message ILIKE '%Heap pressure WARNING%'
                   ))[1] AS last_warning_message
              FROM esp32_logs
             WHERE ts > now() - interval '30 minutes'
               AND message ILIKE '%Heap pressure%'
               AND (
                   (message ILIKE '%Heap pressure CRITICAL%' AND ts > COALESCE($1, '-infinity'::timestamptz))
                   OR (message ILIKE '%Heap pressure WARNING%' AND ts > COALESCE($2, '-infinity'::timestamptz))
               )
            """,
            heap_critical_floor,
            heap_warning_floor,
        )
        heap_diag = await conn.fetchrow(
            """
            SELECT heap_bytes,
                   heap_min_free_kb,
                   heap_largest_free_block_kb,
                   uptime_s,
                   ts
              FROM diagnostics
             WHERE heap_bytes IS NOT NULL
                OR heap_min_free_kb IS NOT NULL
                OR heap_largest_free_block_kb IS NOT NULL
             ORDER BY ts DESC
             LIMIT 1
            """
        )
        heap_state = {r["equipment"]: r for r in heap_rows}
        heap_critical = heap_state.get("heap_pressure_critical")
        heap_warning = heap_state.get("heap_pressure_warning")
        heap_bytes = float(heap_diag["heap_bytes"]) if heap_diag and heap_diag["heap_bytes"] is not None else None
        heap_min_free_kb = (
            float(heap_diag["heap_min_free_kb"]) if heap_diag and heap_diag["heap_min_free_kb"] is not None else None
        )
        heap_largest_free_block_kb = (
            float(heap_diag["heap_largest_free_block_kb"])
            if heap_diag and heap_diag["heap_largest_free_block_kb"] is not None
            else None
        )
        critical_logs = int(heap_log["critical_logs"] or 0) if heap_log else 0
        warning_logs = int(heap_log["warning_logs"] or 0) if heap_log else 0
        last_critical_event_ts = max(
            [
                ts
                for ts in (
                    heap_critical["last_true_ts"] if heap_critical else None,
                    heap_log["last_critical_ts"] if heap_log else None,
                )
                if ts is not None
            ],
            default=None,
        )
        last_warning_event_ts = max(
            [
                ts
                for ts in (
                    heap_warning["last_true_ts"] if heap_warning else None,
                    heap_log["last_warning_ts"] if heap_log else None,
                )
                if ts is not None
            ],
            default=None,
        )
        healthy_after_critical = 0
        healthy_after_warning = 0
        if last_critical_event_ts:
            healthy_after_critical = await conn.fetchval(
                """
                SELECT count(*)
                  FROM diagnostics
                 WHERE ts > $1
                   AND heap_bytes >= $2
                   AND (
                       heap_largest_free_block_kb IS NULL
                       OR heap_largest_free_block_kb >= $3
                   )
                """,
                last_critical_event_ts,
                HEAP_CRITICAL_RECOVERY_FREE_KB,
                HEAP_CRITICAL_RECOVERY_LARGEST_BLOCK_KB,
            )
        if last_warning_event_ts:
            healthy_after_warning = await conn.fetchval(
                """
                SELECT count(*)
                  FROM diagnostics
                 WHERE ts > $1
                   AND heap_bytes >= $2
                   AND (
                       heap_largest_free_block_kb IS NULL
                       OR heap_largest_free_block_kb >= $3
                   )
                """,
                last_warning_event_ts,
                HEAP_CRITICAL_RECOVERY_FREE_KB,
                HEAP_CRITICAL_RECOVERY_LARGEST_BLOCK_KB,
            )
        startup_heap_grace = False
        if last_critical_event_ts and heap_diag and heap_diag["uptime_s"] is not None:
            boot_ts = heap_diag["ts"] - _td(seconds=float(heap_diag["uptime_s"]))
            age_after_boot_s = (last_critical_event_ts - boot_ts).total_seconds()
            # ESPHome/API reconnect and reconnect setpoint reconciliation can
            # transiently dip heap during the first boot minute. Keep those
            # events out of critical alerting once the current heap sample is
            # healthy; sustained pressure still alerts after startup.
            startup_heap_grace = 0 <= age_after_boot_s <= 180
        critical_active = bool((heap_critical and heap_critical["recent_true"]) or critical_logs > 0)
        warning_active = bool((heap_warning and heap_warning["recent_true"]) or warning_logs > 0)
        low_watermark_warning = bool(heap_min_free_kb is not None and heap_min_free_kb < 10.0)
        fragmentation_warning = bool(heap_largest_free_block_kb is not None and heap_largest_free_block_kb < 20.0)
        largest_block_recovered = bool(
            heap_largest_free_block_kb is None or heap_largest_free_block_kb >= HEAP_CRITICAL_RECOVERY_LARGEST_BLOCK_KB
        )
        if heap_bytes is not None:
            # The binary sensors and diagnostics can arrive at the same second;
            # a recent true event still matters until firmware reports the
            # problem sensor false and current fragmentation has recovered.
            # Historical low-watermark risk remains a warning; it should not
            # hold the OTA critical gate open after the current heap recovers.
            if heap_bytes < 15.0:
                critical_active = True
                warning_active = False
            elif heap_bytes < 30.0 and not critical_active:
                critical_active = False
                warning_active = True
            elif (low_watermark_warning or fragmentation_warning) and not critical_active:
                warning_active = True
            elif heap_bytes >= HEAP_CRITICAL_RECOVERY_FREE_KB and largest_block_recovered and heap_diag:
                # Recovery is explicit once firmware publishes a false binary
                # event after the last true/log event and the numeric heap
                # sample is healthy after that false. This preserves real
                # transients while preventing stale log lines from holding a
                # critical alert open after observed recovery.
                if (
                    critical_active
                    and last_critical_event_ts
                    and heap_diag["ts"] > last_critical_event_ts
                    and healthy_after_critical >= HEAP_CRITICAL_RECOVERY_SAMPLES
                    and not (
                        heap_critical
                        and heap_critical["latest_state"] is True
                        and heap_critical["latest_ts"] >= last_critical_event_ts
                    )
                ):
                    critical_active = False
                if (
                    warning_active
                    and last_warning_event_ts
                    and heap_diag["ts"] > last_warning_event_ts
                    and healthy_after_warning >= HEAP_CRITICAL_RECOVERY_SAMPLES
                    and not (
                        heap_warning
                        and heap_warning["latest_state"] is True
                        and heap_warning["latest_ts"] >= last_warning_event_ts
                    )
                ):
                    warning_active = False
            if critical_active and startup_heap_grace and heap_bytes >= HEAP_CRITICAL_RECOVERY_FREE_KB:
                critical_active = False
            if not critical_active and (low_watermark_warning or fragmentation_warning):
                warning_active = True
        elif low_watermark_warning or fragmentation_warning:
            warning_active = True
        if critical_active:
            alerts.append(
                {
                    "alert_type": "heap_pressure_critical",
                    "severity": "critical",
                    "category": "system",
                    "sensor_id": "equipment.heap_pressure_critical",
                    "zone": None,
                    "message": "ESP32 heap pressure critical: free heap dropped below firmware critical threshold",
                    "details": {
                        "equipment": "heap_pressure_critical",
                        "equipment_ts": heap_critical["latest_ts"].isoformat() if heap_critical else None,
                        "last_true_ts": heap_critical["last_true_ts"].isoformat()
                        if heap_critical and heap_critical["last_true_ts"]
                        else None,
                        "heap_free_kb": round(heap_bytes, 1) if heap_bytes is not None else None,
                        "heap_min_free_kb": round(heap_min_free_kb, 1) if heap_min_free_kb is not None else None,
                        "heap_largest_free_block_kb": round(heap_largest_free_block_kb, 1)
                        if heap_largest_free_block_kb is not None
                        else None,
                        "heap_low_watermark_warning": low_watermark_warning,
                        "heap_fragmentation_warning": fragmentation_warning,
                        "heap_diag_ts": heap_diag["ts"].isoformat() if heap_diag else None,
                        "critical_logs_30m": critical_logs,
                        "healthy_heap_samples_after_event": healthy_after_critical,
                        "last_critical_log_ts": heap_log["last_critical_ts"].isoformat()
                        if heap_log and heap_log["last_critical_ts"]
                        else None,
                        "last_critical_log_message": heap_log["last_critical_message"] if heap_log else None,
                    },
                    "metric_value": heap_bytes,
                    "threshold_value": 15.0,
                }
            )
        elif warning_active:
            alerts.append(
                {
                    "alert_type": "heap_pressure_warning",
                    "severity": "warning",
                    "category": "system",
                    "sensor_id": "equipment.heap_pressure_warning",
                    "zone": None,
                    "message": "ESP32 heap pressure warning: free heap stayed below firmware warning threshold",
                    "details": {
                        "equipment": "heap_pressure_warning",
                        "equipment_ts": heap_warning["latest_ts"].isoformat() if heap_warning else None,
                        "last_true_ts": heap_warning["last_true_ts"].isoformat()
                        if heap_warning and heap_warning["last_true_ts"]
                        else None,
                        "heap_free_kb": round(heap_bytes, 1) if heap_bytes is not None else None,
                        "heap_min_free_kb": round(heap_min_free_kb, 1) if heap_min_free_kb is not None else None,
                        "heap_largest_free_block_kb": round(heap_largest_free_block_kb, 1)
                        if heap_largest_free_block_kb is not None
                        else None,
                        "heap_low_watermark_warning": low_watermark_warning,
                        "heap_fragmentation_warning": fragmentation_warning,
                        "heap_diag_ts": heap_diag["ts"].isoformat() if heap_diag else None,
                        "warning_logs_30m": warning_logs,
                        "healthy_heap_samples_after_event": healthy_after_warning,
                        "last_warning_log_ts": heap_log["last_warning_ts"].isoformat()
                        if heap_log and heap_log["last_warning_ts"]
                        else None,
                        "last_warning_log_message": heap_log["last_warning_message"] if heap_log else None,
                    },
                    "metric_value": heap_bytes,
                    "threshold_value": 30.0,
                }
            )

        # 14. Tunable zero-variance detection (Sprint 24.9, G-9).
        # Firmware sprint-13 30-day scan flagged vpd_target_west pinned at
        # 1.2 kPa across 33k samples — either fn_zone_vpd_targets has a
        # west-zone default/bug or the west zone has no active crop. Catching
        # this class of issue automatically (any dispatcher-owned tunable with
        # stddev=0 over 7 days) surfaces the condition without waiting for
        # an operator to notice.
        active_crop_zones = {
            str(r["zone"])
            for r in await conn.fetch("SELECT DISTINCT zone FROM crops WHERE is_active = true AND zone IS NOT NULL")
        }
        zone_target_params = {
            "vpd_target_south": "south",
            "vpd_target_west": "west",
            "vpd_target_east": "east",
            "vpd_target_center": "center",
        }
        zero_var_params = [
            "temp_low",
            "temp_high",
            "vpd_low",
            "vpd_high",
            *[param for param, zone in zone_target_params.items() if zone in active_crop_zones],
        ]
        for r in await conn.fetch(
            """
            SELECT parameter, count(*) AS n, stddev(value) AS sd, avg(value) AS mean
              FROM setpoint_snapshot
             WHERE parameter = ANY($1::text[])
               AND ts > now() - interval '7 days'
             GROUP BY parameter
            HAVING count(*) > 100 AND (stddev(value) IS NULL OR stddev(value) = 0)
            """,
            list(zero_var_params),
        ):
            alerts.append(
                {
                    "alert_type": "tunable_zero_variance",
                    "severity": "warning",
                    "category": "system",
                    "sensor_id": f"setpoint.{r['parameter']}",
                    "zone": None,
                    "message": (
                        f"Tunable `{r['parameter']}` has zero variance over 7 days "
                        f"(n={r['n']}, pinned at {float(r['mean']):.3f}). "
                        "Check dispatcher source (band / zone function / crop profile)."
                    ),
                    "details": {
                        "parameter": r["parameter"],
                        "sample_count": int(r["n"]),
                        "pinned_value": float(r["mean"]),
                    },
                    "metric_value": 0.0,
                    "threshold_value": None,
                }
            )

        # Reactive trigger marker removed in Sprint 5 P6 — deviation monitor handles replans

        # ── Deduplicate + insert + resolve ──
        active_keys = {(a["alert_type"], a["sensor_id"]) for a in alerts}
        # Sprint 25-omnibus (setpoint_unconfirmed lifecycle fix): only
        # consider alerts THIS monitor owns (source='system') for auto-resolve.
        # Alerts inserted by other monitors (setpoint_confirmation_monitor
        # writes source='ingestor'; iris_planner writes source='iris_planner';
        # dispatcher writes source='dispatcher') have their own lifecycle
        # — auto-resolving them here caused setpoint_unconfirmed to flap
        # open↔resolved every alert_monitor cycle.
        open_rows = await conn.fetch(
            "SELECT id, alert_type, severity, sensor_id, slack_ts FROM alert_log "
            "WHERE disposition IN ('open', 'acknowledged') AND resolved_at IS NULL AND source = 'system'"
        )
        open_keys = {(r["alert_type"], r["sensor_id"]): r for r in open_rows}

        slack_token = None
        new_count = 0
        escalated_count = 0
        for a in alerts:
            key = (a["alert_type"], a["sensor_id"])
            if key in open_keys:
                existing = open_keys[key]
                try:
                    env = AlertEnvelope.model_validate(a)
                except ValidationError as e:
                    log.error("alert refresh skipped (validation failed: %s): %r", e, a)
                    continue
                is_escalation = env.severity == "critical" and existing["severity"] != "critical"
                await conn.execute(
                    """
                    UPDATE alert_log
                       SET severity=$1,
                           message=$2,
                           details=$3,
                           metric_value=$4,
                           threshold_value=$5
                     WHERE id=$6
                    """,
                    env.severity,
                    env.message,
                    json.dumps(env.details) if env.details else None,
                    env.metric_value,
                    env.threshold_value,
                    existing["id"],
                )
                # F14 (Sprint 24.6): escalate severity in place and re-notify.
                # Same-severity updates intentionally stay quiet but keep DB
                # context fresh for dashboards and deploy preflights.
                if is_escalation:
                    if slack_token is None:
                        try:
                            slack_token = _load_token(SLACK_TOKEN_FILE)
                        except Exception:
                            slack_token = ""
                    if slack_token:
                        _post_slack(
                            slack_token,
                            SLACK_CHANNEL,
                            f"\U0001f534 *[ESCALATED→CRITICAL]* `{env.alert_type}` — {env.message}",
                            thread_ts=existing["slack_ts"],
                        )
                    escalated_count += 1
                continue
            try:
                env = AlertEnvelope.model_validate(a)
            except ValidationError as e:
                log.error("alert skipped (envelope validation failed: %s): %r", e, a)
                continue
            should_slack = env.alert_type not in ("sensor_offline", "esp32_reboot")
            slack_ts = None
            if should_slack:
                if slack_token is None:
                    try:
                        slack_token = _load_token(SLACK_TOKEN_FILE)
                    except Exception:
                        slack_token = ""
                if slack_token:
                    emoji = {
                        "critical": "\U0001f534",
                        "warning": "\U0001f7e1",
                        "warn": "\U0001f7e1",
                        "info": "\u2139\ufe0f",
                    }.get(env.severity, "")
                    slack_ts = _post_slack(
                        slack_token,
                        SLACK_CHANNEL,
                        f"{emoji} *[{env.severity.upper()}]* `{env.alert_type}` — {env.message}",
                    )

            await conn.execute(
                "INSERT INTO alert_log (alert_type, severity, category, sensor_id, zone, message, details, source, slack_ts, metric_value, threshold_value) VALUES ($1,$2,$3,$4,$5,$6,$7,'system',$8,$9,$10)",
                env.alert_type,
                env.severity,
                env.category,
                env.sensor_id,
                env.zone,
                env.message,
                json.dumps(env.details) if env.details else None,
                slack_ts,
                env.metric_value,
                env.threshold_value,
            )
            new_count += 1

        # Auto-resolve
        resolved = 0
        for key, row in open_keys.items():
            if key not in active_keys:
                await conn.execute(
                    "UPDATE alert_log SET disposition = 'resolved', resolved_at = now(), resolved_by = 'system', resolution = 'auto-resolved' WHERE id = $1",
                    row["id"],
                )
                if row["slack_ts"]:
                    if slack_token is None:
                        try:
                            slack_token = _load_token(SLACK_TOKEN_FILE)
                        except Exception:
                            slack_token = ""
                    if slack_token:
                        _post_slack(
                            slack_token,
                            SLACK_CHANNEL,
                            f"\u2705 Resolved: `{row['alert_type']}` for `{row['sensor_id']}`",
                            thread_ts=row["slack_ts"],
                        )
                resolved += 1

        if new_count or resolved or escalated_count:
            log.info("Alerts: %d new, %d resolved, %d escalated", new_count, resolved, escalated_count)


# Reactive planner REMOVED in Sprint 5 P6 — replaced by forecast deviation monitor (P4)


# ═════════════════════════════════════════════════════════════════
# 8. SETPOINT DISPATCHER (every 300s)
# ═════════════════════════════════════════════════════════════════
from entity_map import CFG_READBACK_MAP, EQUIPMENT_SWITCH_MAP, PARAM_TO_ENTITY, SWITCH_TO_ENTITY
from quiet_mode import (
    QUIET_MODE_ENTITY,
    QUIET_REASON_ENTITY,
    QUIET_UNTIL_ENTITY,
    quiet_expired_needs_restore,
    quiet_is_active,
)

# In-memory cache of last pushed values — prevents re-pushing unchanged setpoints
_last_pushed: dict[str, float] = {}

SWITCH_CONFIRM_EQUIPMENT = {
    param: EQUIPMENT_SWITCH_MAP[entity_id]
    for param, entity_id in SWITCH_TO_ENTITY.items()
    if entity_id in EQUIPMENT_SWITCH_MAP
}
FIRMWARE_READBACK_PARAMS = frozenset(CFG_READBACK_MAP.values())
FIRMWARE_HAS_PER_CIRCUIT_LIGHTING = LIGHTING_CIRCUIT_SUPPORT_SENTINELS <= FIRMWARE_READBACK_PARAMS
FIRMWARE_HAS_LIGHTING_TARGET_MINUTES = LIGHTING_TARGET_MINUTE_PARAMS <= FIRMWARE_READBACK_PARAMS

# Direct ESP32 pushes are heap-expensive, but an open heap alert can also be
# stale after the controller has rebooted and current fragmentation is healthy.
# Gate on fresh diagnostics so post-OTA setpoint reconciliation can recover.
HEAP_DEFER_FREE_KB = 30.0
HEAP_DEFER_LARGEST_BLOCK_KB = 18.0
HEAP_CRITICAL_RECOVERY_FREE_KB = 25.0
HEAP_CRITICAL_RECOVERY_LARGEST_BLOCK_KB = 20.0
HEAP_CRITICAL_RECOVERY_SAMPLES = 2
HEAP_RECOVERY_LIMIT_FREE_KB = 35.0
HEAP_RECOVERY_LIMIT_MIN_FREE_KB = 12.0
HEAP_RECOVERY_LIMIT_LARGEST_BLOCK_KB = 24.0
HEAP_RECOVERY_MAX_CHANGES = 12

# Unified band-first controller compatibility/readback field. ESPHome control
# loop, dispatcher, MCP, and outbound-listener guardrails force it ON.
FORCED_ON_SWITCH_PARAMS = frozenset({"sw_fsm_controller_enabled"})
ACTIVITY_MIRROR_PARAMS = frozenset(
    {
        "activity_start_hour",
        "activity_start_minute",
        "activity_duration_min",
    }
)
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
DIRECT_WET_POLICY_PARAMS = ACTIVITY_MIRROR_PARAMS | frozenset(DIRECT_WET_DEFAULTS)
DIRECT_WET_REQUIRED_OBJECT_IDS = frozenset(
    {
        "activity_duration__min_",
        "direct_wet_gate_enabled",
        "direct_wet_wall_drydown_before_off__min_",
        "direct_wet_center_drydown_before_off__min_",
    }
)
AI_MOISTURE_STRESS_POLICY_PARAMS = frozenset(
    {
        "sw_direct_wet_stress_override_enabled",
        "direct_wet_stress_vpd_margin_kpa",
        "direct_wet_stress_min_dew_margin_f",
        "direct_wet_stress_latest_hour",
        "sw_fog_stress_window_extend_enabled",
        "fog_stress_window_latest_hour",
        "fog_stress_min_dew_margin_f",
    }
)
AI_MOISTURE_STRESS_REQUIRED_OBJECT_IDS = frozenset(
    REGISTRY[param].esp_object_id for param in AI_MOISTURE_STRESS_POLICY_PARAMS if REGISTRY[param].esp_object_id
)

QUIET_STATE_ENTITIES = (
    QUIET_MODE_ENTITY,
    QUIET_UNTIL_ENTITY,
    QUIET_REASON_ENTITY,
)
SAFETY_PARAMS = frozenset({"safety_max", "safety_min"})
MISTER_DEFAULTS = frozenset(
    {
        "mister_engage_kpa",
        "mister_all_kpa",
        "mister_engage_delay_s",
        "mister_all_delay_s",
        "mister_center_penalty",
    }
)


def _upsert_change(changes: list[tuple[str, float]], param: str, value: float) -> None:
    """Add or replace a pending dispatcher change."""
    clean_value = float(value)
    for idx, (existing_param, _) in enumerate(changes):
        if existing_param == param:
            changes[idx] = (param, clean_value)
            return
    changes.append((param, clean_value))


def _apply_manual_overlay(changes: list[tuple[str, float]], overlay: dict[str, float]) -> set[str]:
    """Force an operator overlay into the dispatcher batch."""
    overlay_params: set[str] = set()
    for param, value in overlay.items():
        _upsert_change(changes, param, value)
        overlay_params.add(param)
    return overlay_params


def _direct_wet_policy_supported() -> bool:
    """Return true once the connected firmware exposes the direct-wet contract."""
    keys = shared.esp32.get("keys") or {}
    if keys:
        return DIRECT_WET_REQUIRED_OBJECT_IDS.issubset(set(keys))
    return any(param in shared.cfg_readback for param in DIRECT_WET_POLICY_PARAMS)


def _ai_moisture_stress_policy_supported() -> bool:
    """Return true once firmware exposes PR3 direct-wet/fog stress controls."""
    keys = shared.esp32.get("keys") or {}
    if keys:
        return AI_MOISTURE_STRESS_REQUIRED_OBJECT_IDS.issubset(set(keys))
    return AI_MOISTURE_STRESS_POLICY_PARAMS.issubset(set(shared.cfg_readback))


def _activity_defaults_from_lighting(lighting_row, lighting_circuit_rows) -> dict[str, float]:
    """Derive biological activity from the same main-light runtime policy."""
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
        "activity_start_hour": float(max(0, min(23, activity_start_hour))),
        "activity_start_minute": 0.0,
        "activity_duration_min": float(activity_duration_min),
        "direct_wet_min_temp_f": 65.0,
        "direct_wet_wall_start_offset_min": 60.0,
        "direct_wet_wall_drydown_before_off_min": 120.0,
        "direct_wet_south_start_offset_min": 60.0,
        "direct_wet_south_drydown_before_off_min": 120.0,
        "direct_wet_west_start_offset_min": 60.0,
        "direct_wet_west_drydown_before_off_min": 120.0,
        "direct_wet_center_start_offset_min": 120.0,
        "direct_wet_center_drydown_before_off_min": 180.0,
        "irrig_wall_days_mask": 127.0,
        "irrig_wall_fert_days_mask": 127.0,
        "irrig_center_days_mask": 127.0,
        "irrig_center_fert_days_mask": 127.0,
        "sw_direct_wet_gate_enabled": 1.0,
    }


def _align_activity_defaults_with_planned_lighting(
    defaults: dict[str, float],
    planner_params: dict[str, float],
) -> dict[str, float]:
    """Keep activity defaults tied to active main-light plan overrides."""
    if not defaults:
        return defaults
    aligned = dict(defaults)
    if "gl_main_sunrise_hour" in planner_params:
        aligned["activity_start_hour"] = float(max(0, min(23, int(planner_params["gl_main_sunrise_hour"]))))
    if "gl_main_target_light_minutes" in planner_params:
        aligned["activity_duration_min"] = float(max(0, min(1440, int(planner_params["gl_main_target_light_minutes"]))))
    return aligned


def _dispatch_source(param: str, planner_params: dict[str, float], quiet_params: set[str]) -> str:
    """Return the setpoint_changes source for a dispatcher write."""
    if param in quiet_params:
        return "manual"
    if param in BAND_DRIVEN_PARAMS:
        return "band"
    if param in LIGHTING_CIRCUIT_DEFAULT_PARAMS and param not in planner_params:
        return "band"
    if param in ACTIVITY_MIRROR_PARAMS:
        return "band"
    if param in DIRECT_WET_POLICY_PARAMS and param not in planner_params:
        return "band"
    if param in SAFETY_PARAMS and param not in planner_params:
        return "band"
    if param in MISTER_DEFAULTS and param not in planner_params:
        return "band"
    if param in FORCED_ON_SWITCH_PARAMS:
        return "manual"
    return "plan"


async def _fetch_quiet_state(conn: asyncpg.Connection) -> dict[str, str]:
    rows = await conn.fetch(
        """
        SELECT DISTINCT ON (entity) entity, value
        FROM system_state
        WHERE entity = ANY($1::text[])
        ORDER BY entity, ts DESC
        """,
        list(QUIET_STATE_ENTITIES),
    )
    return {row["entity"]: row["value"] for row in rows}


async def _record_quiet_mode(conn: asyncpg.Connection, mode: str) -> None:
    await conn.execute(
        "INSERT INTO system_state (ts, entity, value) VALUES (now(), $1, $2)",
        QUIET_MODE_ENTITY,
        mode,
    )


# FW-3 (Sprint 18): dispatcher guardrails are derived from the registry's
# fw_clamp_lo/fw_clamp_hi fields. This keeps the setpoint path from carrying a
# second hand-maintained bounds table that can drift from MCP, schema, and
# ESPHome number limits.
_PHYSICS_INVARIANTS: dict[str, tuple[float | None, float | None]] = {
    name: (spec.fw_clamp_lo, spec.fw_clamp_hi)
    for name, spec in REGISTRY.items()
    if spec.kind == "numeric" and (spec.fw_clamp_lo is not None or spec.fw_clamp_hi is not None)
}


def _validate_physics(param: str, val: float) -> tuple[float, str | None]:
    """FW-3 (Sprint 18): clamp val to physical bounds if invariant violated.

    Returns (clamped_val, reason). reason is None when no clamp applied.
    Only clamps for parameters with invariants defined in _PHYSICS_INVARIANTS.
    Unknown params pass through unchanged (the band / dispatcher layers
    handle those).
    """
    bounds = _PHYSICS_INVARIANTS.get(param)
    if bounds is None:
        return val, None
    lo, hi = bounds
    if lo is not None and val < lo:
        return float(lo), f"invariant_violation:below_{lo}"
    if hi is not None and val > hi:
        return float(hi), f"invariant_violation:above_{hi}"
    return val, None


def _coerce_registry_value(param: str, val: float) -> tuple[float | None, str | None]:
    """Clamp numeric registry drift, reject other registry violations."""
    error = registry_value_error(param, val)
    if error is None:
        return float(val), None

    spec = get_tunable(param)
    try:
        numeric = float(val)
    except (TypeError, ValueError):
        log.error("Rejecting dispatcher setpoint %s=%s: %s", param, val, error)
        return None, error
    if spec and spec.kind == "numeric":
        if spec.min is not None and numeric < spec.min:
            return float(spec.min), error
        if spec.max is not None and numeric > spec.max:
            return float(spec.max), error

    log.error("Rejecting dispatcher setpoint %s=%s: %s", param, val, error)
    return None, error


def _should_skip(last: float | None, val: float, rel: float = 0.01, abs_floor: float = 1e-3) -> bool:
    """DI-1 (Sprint 18): proportional dead-band for dispatcher.

    Return True if `val` is within `rel` (default 1%) of the previously-pushed
    value, relative to the magnitude of `val`. An `abs_floor` protects against
    setpoints at or near zero where any absolute threshold would be arbitrary.

    Replaces the previous absolute 0.1/0.05/0.02/0.01 thresholds that were
    scale-inappropriate — 0.05 on a 0.8 kPa VPD setpoint was 6% (too coarse),
    while the same 0.05 on a 75°F temp setpoint was 0.07% (fine). 1% relative
    is the right choice across the whole setpoint range.
    """
    if last is None:
        return False
    return abs(last - val) / max(abs(val), abs_floor) < rel


def readback_abs_tolerance(param: str) -> float:
    """Absolute cfg-readback tolerance for quantized firmware readbacks."""
    return 1.0 if param in SECOND_READBACK_ABS_TOLERANCE_PARAMS else 0.0


def readback_values_equivalent(param: str, observed: float, desired: float) -> bool:
    """Return true when a cfg_* readback is close enough to the requested value."""
    try:
        observed_f = float(observed)
        desired_f = float(desired)
    except (TypeError, ValueError):
        return False
    if _should_skip(observed_f, desired_f):
        return True
    return abs(observed_f - desired_f) <= readback_abs_tolerance(param)


def _heap_push_defer_active(
    heap_alert_open: bool,
    heap_free_kb: float | None,
    heap_min_free_kb: float | None,
    heap_largest_free_block_kb: float | None,
) -> bool:
    """Return true when direct ESP32 setpoint pushes should be deferred."""

    if heap_free_kb is None:
        return heap_alert_open
    if heap_free_kb < HEAP_DEFER_FREE_KB:
        return True
    if heap_largest_free_block_kb is not None and heap_largest_free_block_kb < HEAP_DEFER_LARGEST_BLOCK_KB:
        return True
    return False


def _heap_push_recovery_limited(
    heap_alert_open: bool,
    heap_free_kb: float | None,
    heap_min_free_kb: float | None,
    heap_largest_free_block_kb: float | None,
) -> bool:
    """Return true when only high-priority lighting reconciliation should push."""

    if _heap_push_defer_active(heap_alert_open, heap_free_kb, heap_min_free_kb, heap_largest_free_block_kb):
        return False
    if heap_alert_open:
        return True
    if heap_free_kb is not None and heap_free_kb < HEAP_RECOVERY_LIMIT_FREE_KB:
        return True
    if heap_min_free_kb is not None and heap_min_free_kb < HEAP_RECOVERY_LIMIT_MIN_FREE_KB:
        return True
    if heap_largest_free_block_kb is not None and heap_largest_free_block_kb < HEAP_RECOVERY_LIMIT_LARGEST_BLOCK_KB:
        return True
    return False


def _readback_drift(param: str, desired: float) -> bool:
    """True when ESP32 cfg_* readback disagrees with the desired value."""
    readback = shared.cfg_readback.get(param)
    if readback is None:
        return False
    return not readback_values_equivalent(param, readback, desired)


async def _write_clamp_audit_rows(
    conn: asyncpg.Connection,
    clamp_rows: list[dict[str, object]],
    dispatched_params: set[str],
) -> int:
    """Persist dispatcher guardrail decisions, including unchanged holds.

    Historically `setpoint_clamps` only recorded rows that also produced a
    `setpoint_changes` push. That hid the important case where the planner
    requested a transition, the guardrail kept the already-applied value, and no
    ESP32 write was needed. These rows make that hold traceable to a plan
    transition without changing the relay/control path.
    """
    written = 0
    for row in clamp_rows:
        param = str(row["parameter"])
        reason = str(row["reason"])
        if param in dispatched_params:
            status = "guardrailed"
        elif reason in {"vpd_high_moisture_guardrail", "forced_on_guardrail"} or "guardrail" in reason:
            status = "held_by_guardrail"
        else:
            status = "rejected"
        await conn.execute(
            """
            INSERT INTO setpoint_clamps
              (parameter, requested, applied, band_lo, band_hi, reason,
               status, plan_id, plan_ts, trigger_id, planner_instance)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::uuid, $11)
            """,
            param,
            float(row["requested"]),
            float(row["applied"]),
            float(row["band_lo"]),
            float(row["band_hi"]),
            reason,
            status,
            row.get("plan_id"),
            row.get("plan_ts"),
            row.get("trigger_id"),
            row.get("planner_instance"),
        )
        written += 1
    return written


def _finite_positive(value: object) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if math.isfinite(numeric) and numeric > 0.0:
        return numeric
    return None


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _house_vpd_control_band(band_row, zone_row) -> dict[str, float]:
    """Derive the firmware's house VPD band from crop and zone policy.

    `fn_band_setpoints()` is crop-science policy and can be pulled toward the
    strictest crop. Firmware controls one air mass, so its global VPD band uses
    the median zone target while per-zone mister targets still protect localized
    crop stress.
    """
    base_low = float(band_row["vpd_low"])
    base_high = float(band_row["vpd_high"])
    if not zone_row:
        return {"vpd_low": base_low, "vpd_high": base_high}

    targets = [
        target
        for param in (
            "vpd_target_south",
            "vpd_target_west",
            "vpd_target_east",
            "vpd_target_center",
        )
        if (target := _finite_positive(zone_row[param])) is not None
    ]
    if not targets:
        return {"vpd_low": base_low, "vpd_high": base_high}

    min_target = min(targets)
    max_target = max(targets)
    house_high = min(max_target, max(base_high, _median(targets)))
    # Crop policy can create a very narrow tactical VPD band when one zone is
    # strict and another is dry. The firmware controls one air mass, so keep a
    # minimum deadband by relaxing the low side instead of pushing high-side
    # stress above the crop target.
    house_low = max(base_low, min_target - HOUSE_VPD_LOW_MARGIN_KPA)
    house_low = min(house_low, house_high - HOUSE_VPD_MIN_WIDTH_KPA)
    house_low = max(0.1, house_low)
    if house_high - house_low < HOUSE_VPD_MIN_WIDTH_KPA:
        house_low = max(0.1, house_high - HOUSE_VPD_MIN_WIDTH_KPA)
    return {"vpd_low": round(house_low, 3), "vpd_high": round(house_high, 3)}


def _control_band_from_house_row(house_row) -> dict[str, float] | None:
    if not house_row:
        return None
    return {
        "vpd_low": float(house_row["house_vpd_low"]),
        "vpd_high": float(house_row["house_vpd_high"]),
    }


async def _fetch_moisture_guard_context(conn) -> dict[str, float | str | None] | None:
    row = await conn.fetchrow(
        """
        WITH latest AS (
            SELECT temp_avg,
                   sp_temp_high,
                   vpd_avg,
                   dew_point,
                   greenhouse_mode,
                   outdoor_temp_f,
                   outdoor_rh_pct
              FROM v_greenhouse_state
             ORDER BY ts DESC
             LIMIT 1
        ),
        recent AS (
            SELECT count(*)::int AS recent_samples,
                   count(*) FILTER (
                       WHERE vpd_avg >= fn_setpoint_at('vpd_high', ts) - 0.05
                   )::int AS recent_near_high_samples,
                   avg(vpd_avg)::float AS recent_avg_vpd
              FROM climate
             WHERE ts >= now() - ($1::int * interval '1 minute')
               AND vpd_avg IS NOT NULL
        )
        SELECT latest.*,
               recent.recent_samples,
               recent.recent_near_high_samples,
               recent.recent_avg_vpd,
               CASE WHEN recent.recent_samples > 0
                    THEN recent.recent_near_high_samples::float / recent.recent_samples
                    ELSE 0.0
               END AS recent_near_high_fraction
          FROM latest CROSS JOIN recent
        """,
        VPD_MOISTURE_RECOVERY_WINDOW_MIN,
    )
    return dict(row) if row else None


def _vpd_high_moisture_guardrails(
    control_band: dict[str, float] | None,
    context: dict[str, float | str | None] | None,
) -> dict[str, float]:
    """Clamp conservative moisture thresholds during live dry-side stress.

    Firmware already supports VENTILATE mist assist. This guard keeps planner
    policy from setting physical mist/fog thresholds far above the active house
    VPD band when the latest state is above band and dew margin is healthy.
    """
    if not control_band or not context:
        return {}

    vpd_high = _finite_positive(control_band.get("vpd_high"))
    vpd_avg = _finite_positive(context.get("vpd_avg"))
    if vpd_high is None or vpd_avg is None:
        return {}
    mode = str(context.get("greenhouse_mode") or "")
    recent_samples = int(context.get("recent_samples") or 0)
    recent_fraction = float(context.get("recent_near_high_fraction") or 0.0)
    recent_avg_vpd = _finite_positive(context.get("recent_avg_vpd"))
    vent_near_high = mode == "VENTILATE" and vpd_avg >= (vpd_high - 0.10)
    above_high = vpd_avg > vpd_high + VPD_HIGH_GUARD_MARGIN_KPA
    recovery_not_sustained = (
        mode == "VENTILATE"
        and recent_samples >= 5
        and recent_fraction >= VPD_MOISTURE_RECOVERY_FRACTION
        and recent_avg_vpd is not None
        and recent_avg_vpd >= vpd_high - 0.05
    )
    if not above_high and not vent_near_high and not recovery_not_sustained:
        return {}

    temp_avg = _finite_positive(context.get("temp_avg"))
    temp_high = _finite_positive(context.get("sp_temp_high"))
    dew_point = _finite_positive(context.get("dew_point"))
    if temp_avg is not None and dew_point is not None:
        dew_margin = temp_avg - dew_point
        if dew_margin < VPD_MOISTURE_DEW_MARGIN_F:
            return {}

    outdoor_rh = _finite_positive(context.get("outdoor_rh_pct"))
    temp_hot = temp_avg is not None and temp_high is not None and temp_avg > temp_high + 0.5
    outdoor_dry = outdoor_rh is not None and outdoor_rh <= VPD_DRY_AIR_OUTDOOR_RH_PCT
    hot_dry_vent = mode == "VENTILATE" and temp_hot and outdoor_dry
    vent_stress = mode == "VENTILATE" and (above_high or vent_near_high or recovery_not_sustained)
    fog_escalation = VPD_HOT_DRY_FOG_ESCALATION_KPA if hot_dry_vent else VPD_VENT_FOG_ESCALATION_KPA
    min_fog_off = VPD_HOT_DRY_MIN_FOG_OFF_S if hot_dry_vent else VPD_VENT_MIN_FOG_OFF_S

    return {
        "mister_engage_kpa": round(max(0.5, vpd_high + 0.05), 2),
        "mister_all_kpa": round(max(1.0, vpd_high + 0.25), 2),
        "mister_engage_delay_s": 45.0,
        "mister_all_delay_s": 90.0,
        "mister_pulse_gap_s": 30.0,
        "min_fog_off_s": min_fog_off,
        "fog_escalation_kpa": fog_escalation if vent_stress else 0.30,
    }


async def setpoint_dispatcher(pool: asyncpg.Pool) -> None:
    global _last_pushed
    # On ESP32 reconnect, rebuild the cache from firmware cfg_* readbacks.
    # Values already confirmed by the device do not need to be pushed again;
    # params without a readback still flow through as a conservative fallback.
    # Dispatcher-owned band params are excluded from this seed so an OTA reboot
    # cannot let firmware defaults suppress the authoritative crop-band push.
    if shared.force_setpoint_push.is_set():
        shared.force_setpoint_push.clear()
        _last_pushed.clear()
        seeded = 0
        forced_band = 0
        for param, val in shared.cfg_readback.items():
            if param in BAND_DRIVEN_PARAMS:
                forced_band += 1
                continue
            _last_pushed[param] = float(val)
            seeded += 1
        if SWITCH_CONFIRM_EQUIPMENT:
            async with pool.acquire() as seed_conn:
                equipment_rows = await seed_conn.fetch(
                    """
                    SELECT DISTINCT ON (equipment) equipment, state
                      FROM equipment_state
                     WHERE equipment = ANY($1::text[])
                     ORDER BY equipment, ts DESC
                    """,
                    list(SWITCH_CONFIRM_EQUIPMENT.values()),
                )
            equipment_state = {row["equipment"]: bool(row["state"]) for row in equipment_rows}
            for param, equipment in SWITCH_CONFIRM_EQUIPMENT.items():
                if param in shared.cfg_readback:
                    continue
                if equipment in equipment_state:
                    _last_pushed[param] = 1.0 if equipment_state[equipment] else 0.0
                    seeded += 1
        log.info(
            "Dispatcher: reconnect reconcile — seeded %d cfg readbacks; forcing %d band setpoint(s)",
            seeded,
            forced_band,
        )
    async with pool.acquire() as conn:
        # Compute crop-science band, per-zone VPD targets, the DB-owned house
        # VPD control band, and crop-driven lighting policy used by firmware.
        band_row = await conn.fetchrow("SELECT * FROM fn_band_setpoints(now())")
        zone_row = await conn.fetchrow("SELECT * FROM fn_zone_vpd_targets(now())")
        house_row = await conn.fetchrow("SELECT * FROM fn_house_vpd_control_band(now())")
        lighting_row = await conn.fetchrow("SELECT * FROM fn_lighting_policy(now())")
        lighting_circuit_rows = await conn.fetch("SELECT * FROM fn_lighting_minutes_policy(now()) ORDER BY light_key")
        control_band = _control_band_from_house_row(house_row)
        moisture_guardrails = _vpd_high_moisture_guardrails(
            control_band,
            await _fetch_moisture_guard_context(conn) if control_band else None,
        )

        planned = await conn.fetch(
            "SELECT parameter, value, ts, plan_id, reason, trigger_id, planner_instance FROM v_active_plan"
        )
        raw_planner_params = {r["parameter"]: r["value"] for r in (planned or [])}
        lighting_circuit_supported = FIRMWARE_HAS_PER_CIRCUIT_LIGHTING or any(
            param in shared.cfg_readback or param in _last_pushed for param in LIGHTING_CIRCUIT_SUPPORT_SENTINELS
        )
        lighting_target_minutes_supported = FIRMWARE_HAS_LIGHTING_TARGET_MINUTES or all(
            param in shared.cfg_readback for param in LIGHTING_TARGET_MINUTE_PARAMS
        )
        direct_wet_supported = _direct_wet_policy_supported()
        ai_moisture_stress_supported = _ai_moisture_stress_policy_supported()
        planner_meta = {
            r["parameter"]: {
                "trigger_id": str(r["trigger_id"]) if r["trigger_id"] else None,
                "planner_instance": r["planner_instance"],
                "plan_id": r["plan_id"],
                "plan_ts": r["ts"],
            }
            for r in (planned or [])
        }
        quiet_state = await _fetch_quiet_state(conn)
        quiet_mode = quiet_state.get(QUIET_MODE_ENTITY)
        quiet_until = quiet_state.get(QUIET_UNTIL_ENTITY)
        quiet_active = quiet_is_active(quiet_mode, quiet_until)
        quiet_restore_due = quiet_expired_needs_restore(quiet_mode, quiet_until)
        quiet_params: set[str] = set()

        # FW-3 (Sprint 18): enforce physics invariants BEFORE any downstream
        # use. Clamped values replace the planner's originals; violations
        # get logged alongside band-clamps in setpoint_clamps for audit.
        changes = []
        clamps_to_log: list[dict[str, object]] = []

        def add_clamp_audit(
            param: str,
            requested: float,
            applied: float,
            band_lo: float,
            band_hi: float,
            reason: str,
        ) -> None:
            meta = planner_meta.get(param, {})
            clamps_to_log.append(
                {
                    "parameter": param,
                    "requested": float(requested),
                    "applied": float(applied),
                    "band_lo": float(band_lo),
                    "band_hi": float(band_hi),
                    "reason": reason,
                    "plan_id": meta.get("plan_id"),
                    "plan_ts": meta.get("plan_ts"),
                    "trigger_id": meta.get("trigger_id"),
                    "planner_instance": meta.get("planner_instance"),
                }
            )

        planner_params: dict[str, float] = {}
        for param, raw_val in raw_planner_params.items():
            if param not in PLANNER_PUSHABLE_REG:
                add_clamp_audit(
                    param,
                    float(raw_val),
                    float(raw_val),
                    0.0,
                    0.0,
                    "planner_param_not_pushable",
                )
                log.warning("Dispatcher: ignoring non-planner-pushable active plan param %s", param)
                continue
            clean_val, violation = _validate_physics(param, float(raw_val))
            if violation is not None:
                add_clamp_audit(param, float(raw_val), clean_val, 0.0, 0.0, violation)
                log.warning(
                    "FW-3 invariant: %s=%s clamped to %s (%s)",
                    param,
                    raw_val,
                    clean_val,
                    violation,
                )
            if param in FORCED_ON_SWITCH_PARAMS and clean_val < 0.5:
                add_clamp_audit(param, float(raw_val), 1.0, 1.0, 1.0, "forced_on_guardrail")
                log.warning(
                    "Controller guardrail: ignoring OFF request %s=%s; unified band-first controller remains locked ON",
                    param,
                    raw_val,
                )
                clean_val = 1.0
            guardrail_max = moisture_guardrails.get(param)
            if guardrail_max is not None and clean_val > guardrail_max:
                add_clamp_audit(
                    param,
                    float(raw_val),
                    float(guardrail_max),
                    0.0,
                    float(guardrail_max),
                    "vpd_high_moisture_guardrail",
                )
                log.warning(
                    "VPD-high moisture guardrail: %s=%s clamped to %s while live VPD is above band",
                    param,
                    raw_val,
                    guardrail_max,
                )
                clean_val = guardrail_max
            planner_params[param] = clean_val

        # Band-driven params: planner can tighten within band, clamped to edges
        if band_row and control_band:
            for param in ("temp_low", "temp_high", "vpd_low", "vpd_high"):
                source_row = band_row if param.startswith("temp") else control_band
                band_lo = float(source_row["temp_low" if param.startswith("temp") else "vpd_low"])
                band_hi = float(source_row["temp_high" if param.startswith("temp") else "vpd_high"])
                planner_val = planner_params.get(param)
                if planner_val is not None:
                    planner_f = float(planner_val)
                    val = max(band_lo, min(band_hi, planner_f))
                    # Tier 1 #2: audit clamp when planner request was modified
                    if abs(val - planner_f) > 1e-6:
                        add_clamp_audit(
                            param,
                            planner_f,
                            val,
                            band_lo,
                            band_hi,
                            "band_lo" if planner_f < band_lo else "band_hi",
                        )
                else:
                    val = float(source_row[param])
                val = round(val, 1 if param.startswith("temp") else 2)
                if _should_skip(_last_pushed.get(param), val) and not _readback_drift(param, val):
                    continue
                changes.append((param, val))

        # Per-zone VPD targets (from crop data per zone)
        if zone_row:
            for param in ("vpd_target_south", "vpd_target_west", "vpd_target_east", "vpd_target_center"):
                val = round(float(zone_row[param]), 2)
                if _should_skip(_last_pushed.get(param), val) and not _readback_drift(param, val):
                    continue
                changes.append((param, val))

        # Crop-driven lighting policy: highest active crop DLI determines the
        # target photoperiod. Firmware owns enforcement once these values are
        # pushed, so this remains reliable without the planner VM online.
        if lighting_row:
            lighting_defaults = {}
            if not lighting_circuit_supported:
                lighting_defaults.update(
                    {
                        "gl_dli_target": round(float(lighting_row["target_dli"]), 1),
                        "gl_sunrise_hour": float(int(lighting_row["sunrise_hour"])),
                        "gl_sunset_hour": float(int(lighting_row["cutoff_hour"])),
                        "sw_gl_auto_mode": 1.0,
                    }
                )
            if lighting_circuit_rows and not lighting_circuit_supported:
                main_lighting = next((row for row in lighting_circuit_rows if row["light_key"] == "main"), None)
                if main_lighting:
                    lighting_defaults["gl_lux_threshold"] = round(float(main_lighting["lux_on_threshold"]), 0)
                    lighting_defaults["gl_lux_hysteresis"] = round(float(main_lighting["lux_hysteresis"]), 0)
            for param, val in lighting_defaults.items():
                if _should_skip(_last_pushed.get(param), val) and not _readback_drift(param, val):
                    continue
                changes.append((param, val))

        # Per-circuit lighting state machines: crop policy + Tempest history
        # seed both circuits, but active planner rows are allowed to diverge
        # circuit targets, thresholds, windows, dwell, and auto enable.
        for row in lighting_circuit_rows or []:
            if not lighting_circuit_supported:
                continue
            key = row["light_key"]
            circuit_defaults = {
                f"gl_{key}_dli_target": round(float(row["legacy_dli_target"]), 1),
                f"gl_{key}_target_light_minutes": float(int(row["target_light_minutes"])),
                f"gl_{key}_sunrise_hour": float(int(row["start_hour"])),
                f"gl_{key}_sunset_hour": float(int(row["cutoff_hour"])),
                f"gl_{key}_lux_threshold": round(float(row["lux_on_threshold"]), 0),
                f"gl_{key}_lux_hysteresis": round(float(row["lux_hysteresis"]), 0),
                f"gl_{key}_min_on_s": float(int(row["min_on_s"])),
                f"gl_{key}_min_off_s": float(int(row["min_off_s"])),
                f"sw_gl_{key}_auto_mode": 1.0 if row["auto_enabled"] else 0.0,
            }
            for param, val in circuit_defaults.items():
                if param in LIGHTING_TARGET_MINUTE_PARAMS and not lighting_target_minutes_supported:
                    continue
                if param in planner_params:
                    continue
                if _should_skip(_last_pushed.get(param), val) and not _readback_drift(param, val):
                    continue
                changes.append((param, val))

        # Global biological activity + per-zone direct-wet windows. The global
        # activity duration intentionally follows the same main-light runtime
        # policy that firmware uses for daily qualified light minutes; zones
        # then narrow the wettable portion with start/drydown offsets.
        if direct_wet_supported:
            activity_defaults = _align_activity_defaults_with_planned_lighting(
                _activity_defaults_from_lighting(lighting_row, lighting_circuit_rows),
                planner_params,
            )
            for param, val in activity_defaults.items():
                if param in planner_params and param not in ACTIVITY_MIRROR_PARAMS:
                    continue
                if _should_skip(_last_pushed.get(param), val) and not _readback_drift(param, val):
                    continue
                changes.append((param, val))

        # Safety limits: always dispatched from firmware/operator defaults.
        # Planner safety-rail rows are filtered above by PLANNER_PUSHABLE_REG.
        safety_defaults = {
            "safety_max": 100.0,
            "safety_min": 40.0,
        }
        for param, val in safety_defaults.items():
            planner_val = planner_params.get(param)
            if planner_val is not None:
                val = float(planner_val)
            if _should_skip(_last_pushed.get(param), val) and not _readback_drift(param, val):
                continue
            changes.append((param, val))

        # Mister tuning defaults: band-derived fallbacks, planner can override
        # engage/all_kpa default to band ceiling; planner may set different values
        if band_row and control_band:
            vpd_hi = float(control_band["vpd_high"])
            mister_defaults = {
                "mister_engage_kpa": round(vpd_hi + 0.05, 2),
                "mister_all_kpa": round(vpd_hi + 0.25, 2),
                "mister_engage_delay_s": 30,
                "mister_all_delay_s": 60,
                "mister_center_penalty": 0.5,
            }
            # Only set defaults if planner hasn't specified a value
            for param, val in mister_defaults.items():
                if param in planner_params:
                    continue  # Planner owns this — don't override
                if _should_skip(_last_pushed.get(param), val) and not _readback_drift(param, val):
                    continue
                changes.append((param, val))

        # Process planner setpoints (tactical knobs — skip band params already handled)
        for row in planned or []:
            param = row["parameter"]
            planned_val = planner_params.get(param, row["value"])
            if param in LIGHTING_CIRCUIT_DEFAULT_PARAMS and not lighting_circuit_supported:
                continue
            if param in LIGHTING_TARGET_MINUTE_PARAMS and not lighting_target_minutes_supported:
                continue
            if param in ACTIVITY_MIRROR_PARAMS:
                continue
            if param in DIRECT_WET_POLICY_PARAMS and not direct_wet_supported:
                continue
            if param in AI_MOISTURE_STRESS_POLICY_PARAMS and not ai_moisture_stress_supported:
                continue
            if param.startswith("plan_") or param in BAND_DRIVEN_PARAMS:
                continue

            if param in FORCED_ON_SWITCH_PARAMS:
                planned_val = 1.0
            readback = shared.cfg_readback.get(param)
            readback_drift = _readback_drift(param, planned_val)
            if readback_drift:
                log.warning(
                    "Dispatcher readback drift: %s active plan=%s cfg_readback=%s; re-pushing",
                    param,
                    planned_val,
                    readback,
                )
            last = _last_pushed.get(param)
            if param.startswith("sw_"):
                planned_bool = planned_val > 0.5
                if last is not None and ((last > 0.5) == planned_bool) and not readback_drift:
                    continue
                changes.append((param, 1.0 if planned_bool else 0.0))
            else:
                if _should_skip(last, planned_val) and not readback_drift:
                    continue
                changes.append((param, planned_val))

        for param in FORCED_ON_SWITCH_PARAMS:
            readback = shared.cfg_readback.get(param)
            if readback is not None and readback < 0.5:
                if not any(existing_param == param for existing_param, _ in changes):
                    log.warning(
                        "Controller guardrail: cfg readback has %s=%.0f; forcing ON",
                        param,
                        readback,
                    )
                    changes.append((param, 1.0))

        if quiet_active:
            log.info(
                "Dispatcher: recording quiet mode active until %s; no setpoint overlay applied",
                quiet_until,
            )
        elif quiet_restore_due:
            await _record_quiet_mode(conn, "expired_no_overlay")
            log.info("Dispatcher: recording quiet mode expired; no restore overlay applied")

        if not changes:
            clamp_rows_written = await _write_clamp_audit_rows(conn, clamps_to_log, set())
            if clamp_rows_written:
                log.info(
                    "Dispatcher: wrote %d guardrail hold/audit row(s) with no ESP32 push",
                    clamp_rows_written,
                )
            else:
                log.info("Dispatcher: no setpoint changes needed")
            (STATE_DIR / "setpoint-dispatcher.log").touch()
            return

        heap_guard = await conn.fetchrow(
            """
            SELECT heap_bytes, heap_min_free_kb, heap_largest_free_block_kb
              FROM diagnostics
             WHERE heap_bytes IS NOT NULL
             ORDER BY ts DESC
             LIMIT 1
            """
        )
        heap_alert_open = await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1
                 FROM alert_log
                 WHERE disposition = 'open'
                   AND alert_type IN ('heap_pressure_warning', 'heap_pressure_critical')
            )
            """
        )
        heap_free = float(heap_guard["heap_bytes"]) if heap_guard and heap_guard["heap_bytes"] is not None else None
        heap_min = (
            float(heap_guard["heap_min_free_kb"]) if heap_guard and heap_guard["heap_min_free_kb"] is not None else None
        )
        heap_largest = (
            float(heap_guard["heap_largest_free_block_kb"])
            if heap_guard and heap_guard["heap_largest_free_block_kb"] is not None
            else None
        )
        heap_defer_active = _heap_push_defer_active(bool(heap_alert_open), heap_free, heap_min, heap_largest)
        heap_recovery_limited = _heap_push_recovery_limited(bool(heap_alert_open), heap_free, heap_min, heap_largest)

        dispatchable_changes: list[tuple[str, float, str]] = []
        skipped_heap_deferred = 0
        skipped_heap_recovery = 0
        heap_recovery_push_count = 0
        for param, val in changes:
            source = _dispatch_source(param, planner_params, quiet_params)

            requested_val = float(val)
            registry_val, registry_violation = _coerce_registry_value(param, requested_val)
            if registry_val is None:
                add_clamp_audit(
                    param,
                    requested_val,
                    requested_val,
                    0.0,
                    0.0,
                    registry_violation or "registry_violation",
                )
                continue
            if registry_violation is not None:
                add_clamp_audit(param, requested_val, registry_val, 0.0, 0.0, registry_violation)
                log.warning(
                    "Dispatcher registry guard: %s=%s clamped to %s (%s)",
                    param,
                    requested_val,
                    registry_val,
                    registry_violation,
                )
            val = registry_val
            if heap_defer_active:
                skipped_heap_deferred += 1
                _last_pushed.pop(param, None)
                continue
            if heap_recovery_limited:
                if param not in HEAP_RECOVERY_PRIORITY_PARAMS or heap_recovery_push_count >= HEAP_RECOVERY_MAX_CHANGES:
                    skipped_heap_recovery += 1
                    _last_pushed.pop(param, None)
                    continue
                heap_recovery_push_count += 1

            # Sprint 24.9 (G-2): validate through SetpointChange before INSERT.
            # Defense-in-depth: MCP's PlanTransition.params already validates
            # at write time, but a regression there would silently corrupt
            # setpoint_changes. Drift-surface the mismatch here where it's
            # cheap (one row at a time) vs. downstream where it blows up a
            # grafana panel or planner scorecard.
            try:
                meta = planner_meta.get(param, {})
                change_trigger_id = meta.get("trigger_id")
                change_planner_instance = meta.get("planner_instance")
                SetpointChange(
                    ts=datetime.now(UTC),
                    parameter=param,
                    value=float(val),
                    source=source,
                    trigger_id=change_trigger_id,
                    planner_instance=change_planner_instance,
                    delivery_status="pending",
                )
            except ValidationError as e:
                log.error(
                    "dispatcher setpoint_change skipped (validation failed: %s): param=%s value=%s source=%s",
                    e,
                    param,
                    val,
                    source,
                )
                continue
            # Mark before INSERT so the LISTEN/NOTIFY real-time listener
            # suppresses this dispatcher-origin row. The dispatcher performs
            # its own retried batch push below; without this pre-mark, reconnect
            # force-pushes duplicate every command and can drive ESP32 heap
            # into critical-pressure transients.
            shared.recently_pushed[param] = time.time()
            shared.recently_pushed_values[param] = float(val)
            await conn.execute(
                "INSERT INTO setpoint_changes "
                "(ts, parameter, value, source, trigger_id, planner_instance, delivery_status) "
                "VALUES (now(), $1, $2, $3, $4::uuid, $5, 'pending')",
                param,
                val,
                source,
                change_trigger_id,
                change_planner_instance,
            )
            _last_pushed[param] = val
            dispatchable_changes.append((param, float(val), source))
        if skipped_heap_deferred:
            log.warning(
                "Dispatcher: held %d setpoint retry row(s) during active heap pressure",
                skipped_heap_deferred,
            )
        if skipped_heap_recovery:
            log.warning(
                "Dispatcher: limited heap-recovery push to %d priority lighting setpoint(s); held %d other drift row(s)",
                heap_recovery_push_count,
                skipped_heap_recovery,
            )
        # Tier 1 #2: persist guardrail audit. Rows are written even when the
        # guardrail intentionally holds an unchanged applied value and no
        # setpoint_changes row is emitted.
        dispatched_params = {param for param, _value, _source in dispatchable_changes}
        clamp_rows_written = await _write_clamp_audit_rows(conn, clamps_to_log, dispatched_params)
        if clamp_rows_written:
            log.info(
                "Dispatcher: wrote %d clamp/audit row(s) (%s)",
                clamp_rows_written,
                ", ".join(f"{row['parameter']}={row['requested']}->{row['applied']}" for row in clamps_to_log),
            )
        log.info(
            "Dispatcher: pushed %d setpoint changes (%d band, %d plan, %d manual)",
            len(dispatchable_changes),
            sum(1 for _, _, source in dispatchable_changes if source == "band"),
            sum(1 for _, _, source in dispatchable_changes if source == "plan"),
            sum(1 for _, _, source in dispatchable_changes if source == "manual"),
        )

    # Direct ESP32 push via shared ingestor connection (non-blocking optimization)
    # Tier 1 #4: retry on failure, escalate to alert_log after exhausted attempts.
    esp32_changes = []
    esp32_params = []
    for param, val, _source in dispatchable_changes:
        if param.startswith("sw_"):
            eid = SWITCH_TO_ENTITY.get(param)
            if eid:
                esp32_changes.append((eid, val, "switch"))
                esp32_params.append(param)
        else:
            eid = PARAM_TO_ENTITY.get(param)
            if eid:
                esp32_changes.append((eid, val, "number"))
                esp32_params.append(param)

    if esp32_changes:
        async with pool.acquire() as conn:
            heap_guard = await conn.fetchrow(
                """
                SELECT heap_bytes, heap_min_free_kb, heap_largest_free_block_kb
                  FROM diagnostics
                 WHERE heap_bytes IS NOT NULL
                 ORDER BY ts DESC
                 LIMIT 1
                """
            )
            heap_alert_open = await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1
                      FROM alert_log
                     WHERE disposition = 'open'
                       AND alert_type IN ('heap_pressure_warning', 'heap_pressure_critical')
                )
                """
            )
        heap_free = float(heap_guard["heap_bytes"]) if heap_guard and heap_guard["heap_bytes"] is not None else None
        heap_min = (
            float(heap_guard["heap_min_free_kb"]) if heap_guard and heap_guard["heap_min_free_kb"] is not None else None
        )
        heap_largest = (
            float(heap_guard["heap_largest_free_block_kb"])
            if heap_guard and heap_guard["heap_largest_free_block_kb"] is not None
            else None
        )
        if _heap_push_defer_active(bool(heap_alert_open), heap_free, heap_min, heap_largest):
            log.warning(
                "Dispatcher: skipped direct ESP32 push of %d change(s) due to heap pressure "
                "(heap=%.1fKB min=%sKB largest=%sKB alert_open=%s)",
                len(esp32_changes),
                heap_free if heap_free is not None else -1.0,
                f"{heap_min:.1f}" if heap_min is not None else "unknown",
                f"{heap_largest:.1f}" if heap_largest is not None else "unknown",
                bool(heap_alert_open),
            )
            # Clear only the dispatcher's retry cache. Keep shared.recently_pushed
            # so LISTEN/NOTIFY cannot bypass this heap guard with the same row.
            for param in esp32_params:
                _last_pushed.pop(param, None)
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE setpoint_changes
                       SET delivery_status = 'deferred_heap_pressure'
                     WHERE parameter = ANY($1::text[])
                       AND confirmed_at IS NULL
                       AND COALESCE(source, '') <> 'esp32'
                       AND delivery_status IS DISTINCT FROM 'confirmed'
                       AND ts > now() - interval '5 minutes'
                    """,
                    esp32_params,
                )
            esp32_changes = []

    if esp32_changes:
        last_err: Exception | None = None
        for attempt in (1, 2, 3):
            try:
                pushed = await push_to_esp32(esp32_changes)
                log.info(
                    "Dispatcher: direct-pushed %d/%d to ESP32 (attempt %d)",
                    pushed,
                    len(esp32_changes),
                    attempt,
                )
                last_err = None
                break
            except Exception as e:
                last_err = e
                log.warning("ESP32 direct push failed (attempt %d/3): %s", attempt, e)
                if attempt < 3:
                    await asyncio.sleep(0.5 * attempt)
        if last_err is not None:
            async with pool.acquire() as conn:
                existing = await conn.fetchval(
                    "SELECT id FROM alert_log WHERE alert_type = 'esp32_push_failed' AND disposition = 'open' LIMIT 1"
                )
                if existing is None:
                    alert = AlertEnvelope.model_validate(
                        {
                            "alert_type": "esp32_push_failed",
                            "severity": "warning",
                            "category": "system",
                            "message": f"ESP32 direct push failed after 3 attempts: {last_err}",
                            "details": {
                                "error": str(last_err),
                                "change_count": len(esp32_changes),
                            },
                        }
                    )
                    await conn.execute(
                        "INSERT INTO alert_log (alert_type, severity, category, message, details, source) "
                        "VALUES ('esp32_push_failed', 'warning', 'system', $1, $2, 'dispatcher')",
                        alert.message,
                        json.dumps(alert.details),
                    )

    (STATE_DIR / "setpoint-dispatcher.log").touch()


# ═════════════════════════════════════════════════════════════════
# 9. FORECAST SYNC (every 3600s)
# ═════════════════════════════════════════════════════════════════
_DENVER = ZoneInfo("America/Denver")
_FORECAST_URL = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude=40.1672&longitude=-105.1019"
    "&hourly=temperature_2m,relative_humidity_2m,dew_point_2m,"
    "apparent_temperature,precipitation_probability,precipitation,"
    "rain,snowfall,weather_code,"
    "cloud_cover,cloud_cover_low,cloud_cover_high,"
    "wind_speed_10m,wind_direction_10m,wind_gusts_10m,"
    "shortwave_radiation,direct_radiation,diffuse_radiation,"
    "uv_index,sunshine_duration,"
    "vapour_pressure_deficit,surface_pressure,"
    "et0_fao_evapotranspiration,"
    "soil_temperature_0cm,visibility"
    "&temperature_unit=fahrenheit&wind_speed_unit=mph&precipitation_unit=inch"
    "&forecast_days=16&timezone=America%2FDenver"
)


def _fetch_forecast() -> list[dict] | None:
    """Fetch 16-day hourly forecast from Open-Meteo. Validated via
    OpenMeteoForecastResponse — parallel-array length mismatch fails loud
    instead of silently index-truncating like the old hand-zipped loop."""
    req = urllib.request.Request(_FORECAST_URL, headers={"User-Agent": "verdify-ingestor/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = json.loads(resp.read())
    except Exception as e:
        log.warning("Forecast fetch failed: %s", e)
        return None

    try:
        response = OpenMeteoForecastResponse.model_validate(raw)
    except ValidationError as e:
        log.warning("Open-Meteo response failed schema validation: %s", e)
        return None

    hourly = response.hourly
    times = hourly.time
    if not times:
        return None
    n = len(times)

    def col(name: str) -> list:
        arr = getattr(hourly, name, None)
        return arr if arr is not None else [None] * n

    rows = []
    for i in range(n):
        rows.append(
            {
                "ts": times[i],
                "temp_f": col("temperature_2m")[i],
                "rh_pct": col("relative_humidity_2m")[i],
                "dew_point_f": col("dew_point_2m")[i],
                "feels_like_f": col("apparent_temperature")[i],
                "vpd_kpa": col("vapour_pressure_deficit")[i],
                "precip_prob_pct": col("precipitation_probability")[i],
                "precip_in": col("precipitation")[i],
                "rain_in": col("rain")[i],
                "snow_in": col("snowfall")[i],
                "weather_code": col("weather_code")[i],
                "cloud_cover_pct": col("cloud_cover")[i],
                "cloud_cover_low_pct": col("cloud_cover_low")[i],
                "cloud_cover_high_pct": col("cloud_cover_high")[i],
                "wind_speed_mph": col("wind_speed_10m")[i],
                "wind_dir_deg": col("wind_direction_10m")[i],
                "wind_gust_mph": col("wind_gusts_10m")[i],
                "solar_w_m2": col("shortwave_radiation")[i],
                "direct_radiation_w_m2": col("direct_radiation")[i],
                "diffuse_radiation_w_m2": col("diffuse_radiation")[i],
                "uv_index": col("uv_index")[i],
                "sunshine_duration_s": col("sunshine_duration")[i],
                "surface_pressure_hpa": col("surface_pressure")[i],
                "et0_mm": col("et0_fao_evapotranspiration")[i],
                "soil_temp_f": col("soil_temperature_0cm")[i],
                "visibility_m": col("visibility")[i],
            }
        )
    return rows


async def forecast_sync(pool: asyncpg.Pool) -> None:
    loop = asyncio.get_event_loop()
    rows = await loop.run_in_executor(None, _fetch_forecast)
    if not rows:
        return
    now = datetime.now(UTC)
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM weather_forecast WHERE fetched_at < now() - interval '30 days'")
        for row in rows:
            ts = datetime.fromisoformat(row["ts"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=_DENVER).astimezone(UTC)
            await conn.execute(
                """
                INSERT INTO weather_forecast (ts, fetched_at, temp_f, rh_pct, wind_speed_mph, wind_dir_deg,
                    cloud_cover_pct, precip_prob_pct, solar_w_m2, dew_point_f, feels_like_f, vpd_kpa,
                    precip_in, rain_in, snow_in, wind_gust_mph, uv_index, et0_mm,
                    direct_radiation_w_m2, diffuse_radiation_w_m2, sunshine_duration_s, weather_code,
                    cloud_cover_low_pct, cloud_cover_high_pct, surface_pressure_hpa, soil_temp_f, visibility_m)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24,$25,$26,$27)
            """,
                ts,
                now,
                row.get("temp_f"),
                row.get("rh_pct"),
                row.get("wind_speed_mph"),
                row.get("wind_dir_deg"),
                row.get("cloud_cover_pct"),
                row.get("precip_prob_pct"),
                row.get("solar_w_m2"),
                row.get("dew_point_f"),
                row.get("feels_like_f"),
                row.get("vpd_kpa"),
                row.get("precip_in"),
                row.get("rain_in"),
                row.get("snow_in"),
                row.get("wind_gust_mph"),
                row.get("uv_index"),
                row.get("et0_mm"),
                row.get("direct_radiation_w_m2"),
                row.get("diffuse_radiation_w_m2"),
                row.get("sunshine_duration_s"),
                row.get("weather_code"),
                row.get("cloud_cover_low_pct"),
                row.get("cloud_cover_high_pct"),
                row.get("surface_pressure_hpa"),
                row.get("soil_temp_f"),
                row.get("visibility_m"),
            )
    log.info("Forecast: %d rows inserted", len(rows))


# ═════════════════════════════════════════════════════════════════
# 10. GROW LIGHT DAILY (every 86400s — runs at ~00:10 UTC)
# ═════════════════════════════════════════════════════════════════
_GL_SQL = """
WITH light_events AS (
    SELECT ts, equipment, state,
        LEAD(ts) OVER (PARTITION BY equipment ORDER BY ts) AS next_ts,
        LEAD(state) OVER (PARTITION BY equipment ORDER BY ts) AS next_state
    FROM equipment_state
    WHERE equipment IN ('grow_light_main', 'grow_light_grow')
      AND date_trunc('day', ts)::date = $1
)
SELECT
    COALESCE(SUM(CASE WHEN state = true AND next_state = false AND next_ts IS NOT NULL
        THEN EXTRACT(EPOCH FROM (next_ts - ts)) / 60.0 ELSE 0 END), 0) AS runtime_min,
    COALESCE(SUM(CASE WHEN state = true THEN 1 ELSE 0 END), 0) AS cycles
FROM light_events;
"""

DEFAULT_ELECTRIC_WATTAGES = {
    "heat1": 1500,
    "fan1": 52,
    "fan2": 52,
    "fog": 1644,
    "grow_light_main": 630,
    "grow_light_grow": 816,
    "vent": 10,
}
HEAT2_BTU, THERM_BTU = 75000, 100000


async def _electric_wattages(conn: asyncpg.Connection) -> dict[str, float]:
    """Return published device wattages, falling back to conservative defaults."""
    wattages = dict(DEFAULT_ELECTRIC_WATTAGES)
    rows = await conn.fetch(
        """
        SELECT equipment, wattage
        FROM equipment_assets
        WHERE wattage IS NOT NULL
        """
    )
    for row in rows:
        wattages[row["equipment"]] = float(row["wattage"])
    return wattages


def _runtime_kwh_from_minutes(minutes_by_equipment: dict[str, float], wattages: dict[str, float]) -> float:
    return sum(minutes_by_equipment.get(e, 0.0) / 60.0 * watts / 1000.0 for e, watts in wattages.items())


async def grow_light_daily(pool: asyncpg.Pool) -> None:
    """Comprehensive daily_summary backfill — runtimes, cycles, energy, costs for yesterday."""
    async with pool.acquire() as conn:
        yesterday = await conn.fetchval("SELECT CURRENT_DATE - 1")
        rows = await conn.fetch(
            "SELECT equipment, on_minutes, cycles FROM v_equipment_runtime_daily WHERE day = $1", yesterday
        )
        rt = {r["equipment"]: (float(r["on_minutes"] or 0), int(r["cycles"] or 0)) for r in rows}

        # Runtimes
        rf1 = rt.get("fan1", (0, 0))[0]
        rf2 = rt.get("fan2", (0, 0))[0]
        rh1 = rt.get("heat1", (0, 0))[0]
        rh2 = rt.get("heat2", (0, 0))[0]
        rfg = rt.get("fog", (0, 0))[0]
        rv = rt.get("vent", (0, 0))[0]
        rms = rt.get("mister_south", (0, 0))[0] / 60.0
        rmw = rt.get("mister_west", (0, 0))[0] / 60.0
        rmc = rt.get("mister_center", (0, 0))[0] / 60.0
        rdw = rt.get("drip_wall", (0, 0))[0] / 60.0
        rdc = rt.get("drip_center", (0, 0))[0] / 60.0
        rgl = rt.get("grow_light_main", (0, 0))[0] + rt.get("grow_light_grow", (0, 0))[0]

        # Energy. Electric cost uses published device watts × observed on-time;
        # Shelly metered kWh remains a diagnostic because its circuit coverage is partial.
        wattages = await _electric_wattages(conn)
        electric_minutes = {e: rt.get(e, (0, 0))[0] for e in wattages}
        kwh = _runtime_kwh_from_minutes(electric_minutes, wattages)
        therms = rh2 / 60.0 * HEAT2_BTU / THERM_BTU
        water_gal = (
            await conn.fetchval("SELECT COALESCE(water_used_gal, 0) FROM daily_summary WHERE date = $1", yesterday) or 0
        )
        ce = round(kwh * 0.111, 2)
        cg = round(therms * 0.83, 2)
        cw = round(float(water_gal) * 0.00484, 2)
        ct = round(ce + cg + cw, 2)

        await conn.execute(
            """
            UPDATE daily_summary SET
                runtime_fan1_min=$2, runtime_fan2_min=$3, runtime_heat1_min=$4, runtime_heat2_min=$5,
                runtime_fog_min=$6, runtime_vent_min=$7,
                runtime_mister_south_h=$8, runtime_mister_west_h=$9, runtime_mister_center_h=$10,
                runtime_drip_wall_h=$11, runtime_drip_center_h=$12, runtime_grow_light_min=$13,
                cycles_fan1=$14, cycles_fan2=$15, cycles_heat1=$16, cycles_heat2=$17,
                cycles_fog=$18, cycles_vent=$19,
                cycles_grow_light=$20,
                cycles_mister_south=$21, cycles_mister_west=$22, cycles_mister_center=$23,
                cycles_drip_wall=$24, cycles_drip_center=$25,
                kwh_estimated=$26, therms_estimated=$27,
                cost_electric=$28, cost_gas=$29, cost_water=$30, cost_total=$31
            WHERE date = $1
        """,
            yesterday,
            rf1,
            rf2,
            rh1,
            rh2,
            rfg,
            rv,
            rms,
            rmw,
            rmc,
            rdw,
            rdc,
            rgl,
            rt.get("fan1", (0, 0))[1],
            rt.get("fan2", (0, 0))[1],
            rt.get("heat1", (0, 0))[1],
            rt.get("heat2", (0, 0))[1],
            rt.get("fog", (0, 0))[1],
            rt.get("vent", (0, 0))[1],
            rt.get("grow_light_main", (0, 0))[1] + rt.get("grow_light_grow", (0, 0))[1],
            rt.get("mister_south", (0, 0))[1],
            rt.get("mister_west", (0, 0))[1],
            rt.get("mister_center", (0, 0))[1],
            rt.get("drip_wall", (0, 0))[1],
            rt.get("drip_center", (0, 0))[1],
            round(kwh, 2),
            round(therms, 3),
            ce,
            cg,
            cw,
            ct,
        )
        await conn.execute(
            """
            UPDATE daily_summary ds
               SET kwh_total = ed.measured_kwh::double precision,
                   peak_kw = (ed.peak_watts / 1000.0)::double precision,
                   captured_at = now()
              FROM v_energy_daily ed
             WHERE ds.date = $1
               AND ed.date = ds.date
               AND ed.measured_kwh IS NOT NULL
            """,
            yesterday,
        )

    log.info("Daily summary (%s): %.1f kWh, %.3f therms, $%.2f", yesterday, kwh, therms, ct)

    # ── utility_cost monthly roll-up (idempotent) ──
    async with pool.acquire() as conn:
        month_start = yesterday.replace(day=1)
        row = await conn.fetchrow(
            """
            SELECT ROUND(SUM(COALESCE(cost_electric,0))::numeric, 2) AS ce,
                   ROUND(SUM(COALESCE(cost_gas,0))::numeric, 2)      AS cg,
                   ROUND(SUM(COALESCE(cost_water,0))::numeric, 2)    AS cw,
                   ROUND(SUM(COALESCE(kwh_estimated,0))::numeric, 2) AS kwh,
                   ROUND(SUM(COALESCE(water_used_gal,0))::numeric, 2) AS gal
            FROM daily_summary
            WHERE date >= $1 AND date < ($1 + INTERVAL '1 month')::date
        """,
            month_start,
        )
        if row:
            await conn.execute(
                """
                INSERT INTO utility_cost (month, category, amount_usd, kwh, notes)
                VALUES ($1, 'electric', $2, $3, 'Auto from daily_summary')
                ON CONFLICT (month, category) DO UPDATE SET
                    amount_usd = EXCLUDED.amount_usd, kwh = EXCLUDED.kwh, updated_at = now()
            """,
                month_start,
                row["ce"],
                row["kwh"],
            )
            await conn.execute(
                """
                INSERT INTO utility_cost (month, category, amount_usd, notes)
                VALUES ($1, 'propane', $2, 'Auto from daily_summary')
                ON CONFLICT (month, category) DO UPDATE SET
                    amount_usd = EXCLUDED.amount_usd, updated_at = now()
            """,
                month_start,
                row["cg"],
            )
            await conn.execute(
                """
                INSERT INTO utility_cost (month, category, amount_usd, gallons, notes)
                VALUES ($1, 'water', $2, $3, 'Auto from daily_summary')
                ON CONFLICT (month, category) DO UPDATE SET
                    amount_usd = EXCLUDED.amount_usd, gallons = EXCLUDED.gallons, updated_at = now()
            """,
                month_start,
                row["cw"],
                row["gal"],
            )
        log.info("utility_cost updated for %s", month_start)


# ═════════════════════════════════════════════════════════════════
# 11. FORECAST ACTION ENGINE (every 900s = 15 min)
# ═════════════════════════════════════════════════════════════════
import subprocess as _sp


async def forecast_action_engine(pool: asyncpg.Pool) -> None:
    """Run forecast-action-engine.py as subprocess."""
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        lambda: _sp.run(
            ["/srv/greenhouse/.venv/bin/python3", "/srv/verdify/scripts/forecast-action-engine.py"],
            capture_output=True,
            text=True,
            timeout=60,
        ),
    )
    if result.returncode != 0:
        log.error("Forecast engine failed: %s", result.stderr[:200])
    elif "actions taken" in result.stderr:
        # Log the summary line
        for line in result.stderr.strip().split("\n"):
            if "actions taken" in line or "TRIGGERED" in line:
                log.info("Forecast: %s", line.split("] ")[-1] if "] " in line else line)


# ═════════════════════════════════════════════════════════════════
# 12. FORECAST DEVIATION CHECK (every 900s = 15 min)
# ═════════════════════════════════════════════════════════════════
_FORECAST_DEVIATION_DEFAULTS: dict[str, dict[str, float | int | str | bool]] = {
    "temp_f": {"threshold": 8.0, "unit": "F", "cooldown_min": 60, "enabled": True},
    "rh_pct": {"threshold": 25.0, "unit": "%", "cooldown_min": 60, "enabled": True},
    "vpd_kpa": {"threshold": 0.35, "unit": "kPa", "cooldown_min": 60, "enabled": True},
    "solar_w_m2": {"threshold": 300.0, "unit": "W/m2", "cooldown_min": 120, "enabled": True},
    "wind_speed_mph": {"threshold": 12.0, "unit": "mph", "cooldown_min": 60, "enabled": True},
    "wind_gust_mph": {"threshold": 18.0, "unit": "mph", "cooldown_min": 60, "enabled": True},
    "precip_in": {"threshold": 0.03, "unit": "in/h", "cooldown_min": 60, "enabled": True},
    "cloud_cover_pct": {"threshold": 40.0, "unit": "%", "cooldown_min": 60, "enabled": True},
    "forecast_missing_min": {"threshold": 90.0, "unit": "min", "cooldown_min": 60, "enabled": True},
}
_FORECAST_DEVIATION_SIGMA_MULTIPLIER = 1.5
_FORECAST_DEVIATION_SIGMA_HISTORY_DAYS = 7


def _forecast_deviation_threshold_map(rows) -> dict[str, dict[str, float | int | str]]:
    """Merge DB overrides with built-in coverage for every planner-critical axis."""
    thresholds = {name: dict(spec) for name, spec in _FORECAST_DEVIATION_DEFAULTS.items() if spec.get("enabled", True)}
    for row in rows or []:
        parameter = row["parameter"]
        if not row["enabled"]:
            thresholds.pop(parameter, None)
            continue
        thresholds[parameter] = {
            "threshold": float(row["threshold"]),
            "unit": row["unit"],
            "cooldown_min": int(row["cooldown_min"]),
        }
    return thresholds


def _outdoor_vpd_kpa(temp_f: float | None, rh_pct: float | None) -> float | None:
    """Compute VPD from outdoor temperature/RH using the Magnus approximation."""
    if temp_f is None or rh_pct is None:
        return None
    if rh_pct < 0 or rh_pct > 100:
        return None
    temp_c = (float(temp_f) - 32.0) * 5.0 / 9.0
    saturation_kpa = 0.6108 * math.exp((17.27 * temp_c) / (temp_c + 237.3))
    return max(0.0, saturation_kpa * (1.0 - float(rh_pct) / 100.0))


def _first_float(*values) -> float | None:
    for value in values:
        if value is None:
            continue
        try:
            result = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(result):
            return result
    return None


def _cloud_cover_proxy_pct(
    observed_solar_w_m2: float | None,
    forecast_solar_w_m2: float | None,
    forecast_cloud_pct: float | None,
) -> float | None:
    """Infer a cloud-cover regime from solar miss when no observed cloud sensor exists."""
    if observed_solar_w_m2 is None or forecast_solar_w_m2 is None or forecast_cloud_pct is None:
        return None
    if forecast_solar_w_m2 < 120:
        return None
    solar_ratio = max(0.0, min(2.0, observed_solar_w_m2 / forecast_solar_w_m2))
    return max(0.0, min(100.0, forecast_cloud_pct + (1.0 - solar_ratio) * 100.0))


def _jsonb_dict(value: object) -> dict[str, object]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError("expected JSON object")
        return parsed
    return dict(value)  # type: ignore[arg-type]


def _forecast_deviation_alert_payload(trigger: dict[str, object]) -> dict[str, object]:
    details = {
        "deviations": trigger["deviations"],
        "reason": trigger["reason"],
        "max_abs_deviation": trigger["max_abs_deviation"],
        "consecutive_cycles": trigger["consecutive_cycles"],
        "planner_event_type": "FORECAST_DEVIATION",
        "source": "forecast_deviation_check",
    }
    return {
        "alert_type": "forecast_deviation",
        "severity": "warning",
        "category": "climate",
        "sensor_id": "forecast.deviation",
        "zone": None,
        "message": trigger["reason"],
        "details": details,
        "metric_value": trigger["max_abs_deviation"],
        "threshold_value": 0.0,
    }


async def _insert_forecast_deviation_alert(conn: asyncpg.Connection, trigger: dict[str, object]) -> int:
    alert = AlertEnvelope.model_validate(_forecast_deviation_alert_payload(trigger))
    alert_id = await conn.fetchval(
        """
        INSERT INTO alert_log
          (alert_type, severity, category, sensor_id, zone, message, details,
           source, metric_value, threshold_value, greenhouse_id)
        VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,'ingestor',$8,$9,$10)
        RETURNING id
        """,
        alert.alert_type,
        alert.severity,
        alert.category,
        alert.sensor_id,
        alert.zone,
        alert.message,
        json.dumps(alert.details),
        alert.metric_value,
        alert.threshold_value,
        GREENHOUSE_ID,
    )
    return int(alert_id)


def _forecast_deviation_trigger_from_alert(row) -> tuple[int, dict[str, object]]:
    details = _jsonb_dict(row["details"])
    trigger = {
        "ts": row["created_at"].isoformat() if row["created_at"] else datetime.now(UTC).isoformat(),
        "deviations": details.get("deviations", []),
        "reason": details.get("reason") or row["message"] or "Forecast deviation",
        "max_abs_deviation": details.get("max_abs_deviation", 0.0),
        "consecutive_cycles": details.get("consecutive_cycles", 1),
    }
    return int(row["id"]), trigger


async def _pending_forecast_deviation_alert(conn: asyncpg.Connection) -> tuple[int, dict[str, object]] | None:
    row = await conn.fetchrow(
        """
        SELECT id, details, message, created_at
          FROM alert_log
         WHERE alert_type = 'forecast_deviation'
           AND disposition IN ('open', 'acknowledged')
           AND resolved_at IS NULL
           AND created_at > now() - interval '5 minutes'
         ORDER BY created_at ASC
         LIMIT 1
        """
    )
    if row is None:
        return None
    return _forecast_deviation_trigger_from_alert(row)


async def _resolve_forecast_deviation_alert(conn: asyncpg.Connection, alert_id: int) -> None:
    await conn.execute(
        """
        UPDATE alert_log
           SET disposition = 'resolved',
               resolved_at = now(),
               resolved_by = 'planning_heartbeat',
               resolution = 'delivered to planner'
         WHERE id = $1
           AND disposition IN ('open', 'acknowledged')
           AND resolved_at IS NULL
        """,
        alert_id,
    )


async def forecast_deviation_check(pool: asyncpg.Pool) -> None:
    """Compare outdoor observed conditions to outdoor forecast. Queue planner alert if deviation exceeds threshold.

    Guards against false triggers:
    - Outdoor comparisons run during daytime through sunset+2h — nighttime RH divergence is normal
    - Cooldown is per parameter, so a wind miss cannot suppress a solar/precip miss
    - Logs every threshold-exceeding deviation; `triggered=false` rows feed the sigma baseline
    """
    async with pool.acquire() as conn:
        threshold_rows = await conn.fetch("SELECT * FROM forecast_deviation_thresholds")
        thresholds = _forecast_deviation_threshold_map(threshold_rows)

        logged: list[dict[str, float | str | bool | int]] = []
        triggering: list[dict[str, float | str | bool | int]] = []

        async def consider_deviation(parameter: str, observed: float | None, forecasted: float | None) -> None:
            spec = thresholds.get(parameter)
            if spec is None or observed is None or forecasted is None:
                return
            delta = abs(float(observed) - float(forecasted))
            threshold = float(spec["threshold"])
            if delta <= threshold:
                return
            dev: dict[str, float | str | bool | int] = {
                "parameter": parameter,
                "observed": round(float(observed), 2),
                "forecasted": round(float(forecasted), 2),
                "delta": round(delta, 2),
                "threshold": threshold,
                "unit": str(spec["unit"]),
                "cooldown_min": int(spec["cooldown_min"]),
                "triggered": False,
            }
            logged.append(dev)

            stats = await conn.fetchrow(
                """
                SELECT AVG(delta) AS mean, COALESCE(STDDEV(delta), 0.0) AS stddev
                FROM forecast_deviation_log
                WHERE parameter = $1 AND ts > now() - ($2::int * interval '1 day')
                """,
                parameter,
                _FORECAST_DEVIATION_SIGMA_HISTORY_DAYS,
            )
            if stats and stats["mean"] is not None:
                sigma_gate = float(stats["mean"]) + _FORECAST_DEVIATION_SIGMA_MULTIPLIER * float(stats["stddev"])
                if delta < sigma_gate:
                    log.info(
                        "PL-5 sigma gate: %s delta=%.2f below %.2f (mean + %s sigma of %dd history) - logging only",
                        parameter,
                        delta,
                        sigma_gate,
                        _FORECAST_DEVIATION_SIGMA_MULTIPLIER,
                        _FORECAST_DEVIATION_SIGMA_HISTORY_DAYS,
                    )
                    return

            recent_same_axis = await conn.fetchval(
                """
                SELECT 1
                  FROM forecast_deviation_log
                 WHERE parameter = $1
                   AND triggered = true
                   AND ts > now() - ($2::int * interval '1 minute')
                 LIMIT 1
                """,
                parameter,
                int(spec["cooldown_min"]),
            )
            if recent_same_axis:
                log.info(
                    "Forecast deviation cooldown: %s delta=%.2f within %d min same-axis cooldown - logging only",
                    parameter,
                    delta,
                    int(spec["cooldown_min"]),
                )
                return

            recent_cycles = await conn.fetchval(
                """
                SELECT COUNT(*)::int
                  FROM forecast_deviation_log
                 WHERE parameter = $1
                   AND ts > now() - interval '3 hours'
                """,
                parameter,
            )
            dev["triggered"] = True
            dev["normalized_excess"] = round((delta - threshold) / max(threshold, 1e-6), 3)
            dev["recent_cycles"] = int(recent_cycles or 0) + 1
            triggering.append(dev)

        # Prolonged missing forecast is a planner input gap, not a daylight-only
        # outdoor deviation, so evaluate it before the daytime gate.
        latest_forecast = await conn.fetchrow(
            """
            SELECT max(fetched_at) AS latest_fetched_at,
                   count(*) FILTER (
                       WHERE ts >= date_trunc('hour', now())
                         AND ts < date_trunc('hour', now()) + interval '2 hours'
                   )::int AS current_horizon_rows
              FROM weather_forecast
            """
        )
        missing_spec = thresholds.get("forecast_missing_min")
        if missing_spec is not None:
            if latest_forecast is None or latest_forecast["latest_fetched_at"] is None:
                await consider_deviation("forecast_missing_min", float(missing_spec["threshold"]) + 1.0, 0.0)
            else:
                age_min = (datetime.now(UTC) - latest_forecast["latest_fetched_at"]).total_seconds() / 60.0
                horizon_rows = int(latest_forecast["current_horizon_rows"] or 0)
                if age_min > float(missing_spec["threshold"]) or horizon_rows == 0:
                    observed_gap = max(age_min, float(missing_spec["threshold"]) + 1.0 if horizon_rows == 0 else 0.0)
                    await consider_deviation("forecast_missing_min", observed_gap, 0.0)

        # Time-of-day gate: only check during daytime + 1h buffer after sunset
        # Nighttime RH/temp deviations are climatologically normal and not actionable
        from astral import LocationInfo
        from astral.sun import sun as _astral_sun

        now = datetime.now(ZoneInfo("America/Denver"))
        loc = LocationInfo("Longmont", "USA", "America/Denver", 40.1672, -105.1019)
        s = _astral_sun(loc.observer, date=now.date(), tzinfo=ZoneInfo("America/Denver"))
        sunrise = s["sunrise"]
        sunset_buffer = s["sunset"] + _td(hours=2)  # Extended to cover evening VPD cycling

        if sunrise <= now <= sunset_buffer:
            current = await conn.fetchrow("""
                SELECT outdoor_temp_f, outdoor_rh_pct,
                       COALESCE(solar_irradiance_w_m2, 0) AS solar_w_m2,
                       COALESCE(wind_speed_avg_mph, wind_speed_mph) AS wind_speed_mph,
                       wind_gust_mph,
                       COALESCE(precip_intensity_in_h, precip_in, 0) AS precip_in
                FROM climate
                WHERE outdoor_temp_f IS NOT NULL
                ORDER BY ts DESC
                LIMIT 1
            """)

            forecast = await conn.fetchrow("""
                SELECT temp_f, rh_pct, vpd_kpa, wind_speed_mph, wind_gust_mph,
                       cloud_cover_pct, precip_in, precip_prob_pct,
                       COALESCE(solar_w_m2, direct_radiation_w_m2 + diffuse_radiation_w_m2, 0) AS solar_w_m2
                FROM (
                    SELECT DISTINCT ON (ts) *
                    FROM weather_forecast
                    WHERE ts >= date_trunc('hour', now())
                      AND ts < date_trunc('hour', now()) + interval '1 hour'
                    ORDER BY ts, fetched_at DESC
                ) sub
            """)

            if current and forecast:
                observed_temp = _first_float(current["outdoor_temp_f"])
                observed_rh = _first_float(current["outdoor_rh_pct"])
                forecast_temp = _first_float(forecast["temp_f"])
                forecast_rh = _first_float(forecast["rh_pct"])
                observed_solar = _first_float(current["solar_w_m2"])
                forecast_solar = _first_float(forecast["solar_w_m2"])
                forecast_cloud = _first_float(forecast["cloud_cover_pct"])
                forecast_vpd = _first_float(forecast["vpd_kpa"])
                if forecast_vpd is None:
                    forecast_vpd = _outdoor_vpd_kpa(forecast_temp, forecast_rh)

                pairs = {
                    "temp_f": (observed_temp, forecast_temp),
                    "rh_pct": (observed_rh, forecast_rh),
                    "vpd_kpa": (
                        _outdoor_vpd_kpa(observed_temp, observed_rh),
                        forecast_vpd,
                    ),
                    "solar_w_m2": (observed_solar, forecast_solar),
                    "wind_speed_mph": (
                        _first_float(current["wind_speed_mph"]),
                        _first_float(forecast["wind_speed_mph"]),
                    ),
                    "wind_gust_mph": (_first_float(current["wind_gust_mph"]), _first_float(forecast["wind_gust_mph"])),
                    "precip_in": (_first_float(current["precip_in"]), _first_float(forecast["precip_in"])),
                    "cloud_cover_pct": (
                        _cloud_cover_proxy_pct(observed_solar, forecast_solar, forecast_cloud),
                        forecast_cloud,
                    ),
                }
                for parameter, (observed, forecasted) in pairs.items():
                    await consider_deviation(parameter, observed, forecasted)

        # Always persist every threshold-exceeding deviation so historical
        # stats stay representative.
        for d in logged:
            await conn.execute(
                """
                INSERT INTO forecast_deviation_log
                  (parameter, observed, forecasted, delta, threshold, triggered)
                VALUES ($1,$2,$3,$4,$5,$6)
                """,
                d["parameter"],
                d["observed"],
                d["forecasted"],
                d["delta"],
                d["threshold"],
                bool(d["triggered"]),
            )

        if not triggering:
            return

    max_abs_deviation = max(float(d.get("normalized_excess", 0.0)) for d in triggering)
    consecutive_cycles = max(int(d.get("recent_cycles", 1)) for d in triggering)

    # Write trigger file for the heartbeat to deliver.
    trigger = {
        "ts": datetime.now(UTC).isoformat(),
        "deviations": triggering,
        "reason": f"Forecast deviation: {', '.join(d['parameter'] for d in triggering)}",
        "max_abs_deviation": round(max_abs_deviation, 3),
        "consecutive_cycles": consecutive_cycles,
    }
    async with pool.acquire() as conn:
        alert_id = await _insert_forecast_deviation_alert(conn, trigger)
    log.warning("Replan alert queued: %s (alert_id=%s)", trigger["reason"], alert_id)


# ═════════════════════════════════════════════════════════════════
# 13. LIVE DAILY SUMMARY (every 1800s = 30 min)
# ═════════════════════════════════════════════════════════════════
async def _refresh_daily_summary_for_date(conn: asyncpg.Connection, target_day) -> tuple[float, float, float]:
    """Refresh daily_summary derived aggregates for a local greenhouse day."""
    # Ensure row exists
    await conn.execute("INSERT INTO daily_summary (date) VALUES ($1) ON CONFLICT (date) DO NOTHING", target_day)

    # Climate aggregates
    climate = await conn.fetchrow(
        """
        SELECT MIN(temp_avg) AS temp_min, MAX(temp_avg) AS temp_max, AVG(temp_avg) AS temp_avg,
               MIN(vpd_avg) AS vpd_min, MAX(vpd_avg) AS vpd_max, AVG(vpd_avg) AS vpd_avg,
               MIN(rh_avg) AS rh_min, MAX(rh_avg) AS rh_max, AVG(rh_avg) AS rh_avg,
               MIN(outdoor_temp_f) AS outdoor_temp_min, MAX(outdoor_temp_f) AS outdoor_temp_max,
               AVG(co2_ppm) AS co2_avg, MAX(dli_today) AS dli_final,
               MAX(mister_water_today) AS mister_water_gal
        FROM climate
        WHERE ts >= $1::date::timestamp AT TIME ZONE 'America/Denver'
          AND ts < ($1::date + 1)::timestamp AT TIME ZONE 'America/Denver'
          AND temp_avg IS NOT NULL
    """,
        target_day,
    )

    # Stress hours — computed with time-appropriate setpoints.
    band_changes = await conn.fetch(
        """
        SELECT parameter, value, ts
        FROM setpoint_changes
        WHERE parameter = ANY($2::text[])
          AND ts <= ($1::date + 1)::timestamp AT TIME ZONE 'America/Denver'
        ORDER BY parameter, ts
        """,
        target_day,
        sorted(HOUSE_BAND_PARAMS),
    )
    from bisect import bisect_right

    timelines: dict[str, list[tuple]] = {}
    timeline_ts: dict[str, list] = {}
    for r in band_changes:
        param = r["parameter"]
        val = float(r["value"])
        spec = REGISTRY[param]
        if spec.fw_clamp_lo is not None and val < spec.fw_clamp_lo:
            continue
        if spec.fw_clamp_hi is not None and val > spec.fw_clamp_hi:
            continue
        timelines.setdefault(param, []).append((r["ts"], val))
        timeline_ts.setdefault(param, []).append(r["ts"])

    def _band_at(param: str, ts):
        tl = timelines.get(param, [])
        times = timeline_ts.get(param, [])
        if not tl or not times:
            return None
        idx = bisect_right(times, ts) - 1
        return tl[idx][1] if idx >= 0 else None

    readings = await conn.fetch(
        """
        SELECT ts, temp_avg, vpd_avg FROM climate
        WHERE ts >= $1::date::timestamp AT TIME ZONE 'America/Denver'
          AND ts < ($1::date + 1)::timestamp AT TIME ZONE 'America/Denver'
          AND temp_avg IS NOT NULL
        ORDER BY ts
        """,
        target_day,
    )

    heat_s = cold_s = vpd_hi_s = vpd_lo_s = 0
    temp_in_band = vpd_in_band = both_in_band = 0
    scored_readings = 0
    interval_h = 1.0 / 60.0  # greenhouse telemetry is nominally one row/minute
    for r in readings:
        th = _band_at("temp_high", r["ts"])
        tl = _band_at("temp_low", r["ts"])
        vh = _band_at("vpd_high", r["ts"])
        vl = _band_at("vpd_low", r["ts"])
        if th is None or tl is None or vh is None or vl is None:
            continue
        scored_readings += 1
        temp = float(r["temp_avg"])
        vpd = float(r["vpd_avg"])
        if temp > th:
            heat_s += interval_h
        elif temp < tl:
            cold_s += interval_h
        if vpd > vh:
            vpd_hi_s += interval_h
        elif vpd < vl:
            vpd_lo_s += interval_h
        t_ok = tl <= temp <= th
        v_ok = vl <= vpd <= vh
        if t_ok:
            temp_in_band += 1
        if v_ok:
            vpd_in_band += 1
        if t_ok and v_ok:
            both_in_band += 1

    n = scored_readings or len(readings) or 1
    compliance_pct = round((both_in_band / n) * 100, 1)
    temp_compliance_pct = round((temp_in_band / n) * 100, 1)
    vpd_compliance_pct = round((vpd_in_band / n) * 100, 1)
    stress = {
        "heat": round(heat_s, 2),
        "vpd_high": round(vpd_hi_s, 2),
        "cold": round(cold_s, 2),
        "vpd_low": round(vpd_lo_s, 2),
    }

    # Dew point margin (condensation risk)
    dp = await conn.fetchrow(
        """
        SELECT min_margin_f, COALESCE(risk_hours, 0) AS risk_hours
        FROM v_dew_point_risk WHERE date = $1
    """,
        target_day,
    )

    _RT_EQUIP = (
        "fan1",
        "fan2",
        "fog",
        "heat1",
        "heat2",
        "vent",
        "grow_light_main",
        "grow_light_grow",
        "mister_south",
        "mister_west",
        "mister_center",
        "drip_wall",
        "drip_center",
        "drip_wall_fert",
        "drip_center_fert",
        "mister_south_fert",
        "mister_west_fert",
        "fert_master_valve",
    )
    rt_rows = await conn.fetch(
        """
        WITH day_bounds AS (
            SELECT $1::date::timestamp AT TIME ZONE 'America/Denver' AS day_start,
                   ($1::date + 1)::timestamp AT TIME ZONE 'America/Denver' AS day_end
        ),
        seeded AS (
            SELECT DISTINCT ON (e.equipment)
                   e.equipment,
                   day_bounds.day_start AS ts,
                   e.state,
                   true AS is_seed
              FROM equipment_state e
              CROSS JOIN day_bounds
             WHERE e.ts < day_bounds.day_start
               AND e.equipment = ANY($2::text[])
             ORDER BY e.equipment, e.ts DESC
        ),
        day_events AS (
            SELECT e.equipment, e.ts, e.state, false AS is_seed
              FROM equipment_state e
              CROSS JOIN day_bounds
             WHERE e.ts >= day_bounds.day_start
               AND e.ts < day_bounds.day_end
               AND e.equipment = ANY($2::text[])
        ),
        raw AS (
            SELECT * FROM seeded
            UNION ALL
            SELECT * FROM day_events
        ),
        changes AS (
            SELECT equipment, ts, state, is_seed
              FROM (
                  SELECT equipment, ts, state, is_seed,
                         lag(state) OVER (PARTITION BY equipment ORDER BY ts, is_seed DESC) AS prev_state
                    FROM raw
              ) ordered
             WHERE prev_state IS NULL OR prev_state IS DISTINCT FROM state
        ),
        transitions AS (
            SELECT equipment, ts, state, is_seed,
                   lead(ts) OVER (PARTITION BY equipment ORDER BY ts, is_seed DESC) AS next_ts
              FROM changes
        )
        SELECT equipment,
               round(sum(extract(epoch FROM
                   coalesce(next_ts, (SELECT day_end FROM day_bounds)) - ts
               ) / 60.0) FILTER (WHERE state = true), 1) AS on_minutes,
               count(*) FILTER (
                   WHERE state IS TRUE
                     AND is_seed IS FALSE
               ) AS cycles
        FROM transitions
        GROUP BY equipment
    """,
        target_day,
        list(_RT_EQUIP),
    )
    rt = {r["equipment"]: float(r["on_minutes"] or 0) for r in rt_rows}
    cycles = {r["equipment"]: int(r["cycles"] or 0) for r in rt_rows}

    wattages = await _electric_wattages(conn)
    electric_minutes = {e: rt.get(e, 0.0) for e in wattages}
    kwh = _runtime_kwh_from_minutes(electric_minutes, wattages)
    therms = rt.get("heat2", 0) / 60.0 * 75000 / 100000

    mister_water_gal = float(climate["mister_water_gal"]) if climate and climate["mister_water_gal"] else 0.0
    meter_water_gal = (
        await conn.fetchval(
            """
        SELECT COALESCE(
            (SELECT used_gal FROM v_water_daily WHERE day::date = $1 ORDER BY day DESC LIMIT 1),
            (SELECT COALESCE(MAX(water_total_gal) - MIN(water_total_gal), 0)
               FROM climate
              WHERE ts >= $1::date::timestamp AT TIME ZONE 'America/Denver'
                AND ts < ($1::date + 1)::timestamp AT TIME ZONE 'America/Denver'
                AND water_total_gal > 0)
        )
    """,
            target_day,
        )
        or 0
    )
    water_gal = max(float(meter_water_gal), mister_water_gal)

    ce = round(kwh * 0.111, 2)
    cg = round(therms * 0.83, 2)
    cw = round(float(water_gal) * 0.00484, 2)
    ct = round(ce + cg + cw, 2)

    gl_min = rt.get("grow_light_main", 0) + rt.get("grow_light_grow", 0)
    irrigation_meter = await conn.fetchrow(
        """
        SELECT COALESCE(sum(COALESCE(meter_delta_gal, 0)), 0)::double precision AS meter_delta_gal
          FROM v_irrigation_fertigation_runs
         WHERE day = $1
        """,
        target_day,
    )
    fert_runtime_h = (
        rt.get("drip_wall_fert", 0)
        + rt.get("drip_center_fert", 0)
        + rt.get("mister_south_fert", 0)
        + rt.get("mister_west_fert", 0)
    ) / 60.0
    clean_irrigation_runtime_h = (
        rt.get("drip_wall", 0)
        + rt.get("drip_center", 0)
        + rt.get("mister_south", 0)
        + rt.get("mister_west", 0)
        + rt.get("mister_center", 0)
    ) / 60.0
    irrigation_water_gal = float(irrigation_meter["meter_delta_gal"] or 0) if irrigation_meter else 0.0

    await conn.execute(
        """
        UPDATE daily_summary SET
            temp_min=$2, temp_max=$3, temp_avg=$4,
            vpd_min=$5, vpd_max=$6, vpd_avg=$7,
            rh_min=$8, rh_max=$9, rh_avg=$10,
            co2_avg=$11, dli_final=$12,
            outdoor_temp_min=$13, outdoor_temp_max=$14,
            stress_hours_heat=$15, stress_hours_vpd_high=$16,
            stress_hours_cold=$17, stress_hours_vpd_low=$18,
            runtime_fan1_min=$19, runtime_fan2_min=$20,
            runtime_heat1_min=$21, runtime_heat2_min=$22,
            runtime_fog_min=$23, runtime_vent_min=$24,
            runtime_grow_light_min=$25,
            runtime_mister_south_h=$26, runtime_mister_west_h=$27, runtime_mister_center_h=$28,
            runtime_drip_wall_h=$29, runtime_drip_center_h=$30,
            kwh_estimated=$31, therms_estimated=$32,
            cost_electric=$33, cost_gas=$34, cost_water=$35, cost_total=$36,
            water_used_gal=$37, mister_water_gal=$38,
            min_dp_margin_f=$39, dp_risk_hours=$40,
            compliance_pct=$41,
            temp_compliance_pct=$42,
            vpd_compliance_pct=$43,
            cycles_mister_south=$44,
            cycles_mister_west=$45,
            cycles_mister_center=$46,
            cycles_drip_wall=$47,
            cycles_drip_center=$48,
            captured_at=now()
        WHERE date = $1
    """,
        target_day,
        climate["temp_min"] if climate else None,
        climate["temp_max"] if climate else None,
        climate["temp_avg"] if climate else None,
        climate["vpd_min"] if climate else None,
        climate["vpd_max"] if climate else None,
        climate["vpd_avg"] if climate else None,
        climate["rh_min"] if climate else None,
        climate["rh_max"] if climate else None,
        climate["rh_avg"] if climate else None,
        climate["co2_avg"] if climate else None,
        climate["dli_final"] if climate else None,
        climate["outdoor_temp_min"] if climate else None,
        climate["outdoor_temp_max"] if climate else None,
        float(stress["heat"]) if stress else 0,
        float(stress["vpd_high"]) if stress else 0,
        float(stress["cold"]) if stress else 0,
        float(stress["vpd_low"]) if stress else 0,
        rt.get("fan1", 0),
        rt.get("fan2", 0),
        rt.get("heat1", 0),
        rt.get("heat2", 0),
        rt.get("fog", 0),
        rt.get("vent", 0),
        gl_min,
        rt.get("mister_south", 0) / 60.0,
        rt.get("mister_west", 0) / 60.0,
        rt.get("mister_center", 0) / 60.0,
        rt.get("drip_wall", 0) / 60.0,
        rt.get("drip_center", 0) / 60.0,
        round(kwh, 2),
        round(therms, 3),
        ce,
        cg,
        cw,
        ct,
        float(water_gal),
        mister_water_gal,
        float(dp["min_margin_f"]) if dp and dp["min_margin_f"] is not None else None,
        float(dp["risk_hours"]) if dp else 0,
        compliance_pct,
        temp_compliance_pct,
        vpd_compliance_pct,
        cycles.get("mister_south", 0),
        cycles.get("mister_west", 0),
        cycles.get("mister_center", 0),
        cycles.get("drip_wall", 0),
        cycles.get("drip_center", 0),
    )
    await conn.execute(
        """
        UPDATE daily_summary SET
            runtime_drip_wall_fert_h=$2,
            runtime_drip_center_fert_h=$3,
            runtime_mister_south_fert_h=$4,
            runtime_mister_west_fert_h=$5,
            runtime_fert_master_h=$6,
            runtime_irrigation_clean_h=$7,
            runtime_irrigation_fert_h=$8,
            runtime_irrigation_total_h=$9,
            cycles_drip_wall_fert=$10,
            cycles_drip_center_fert=$11,
            cycles_mister_south_fert=$12,
            cycles_mister_west_fert=$13,
            cycles_fert_master=$14,
            irrigation_water_gal=$15,
            fertigation_water_gal=$16,
            captured_at=now()
        WHERE date = $1
        """,
        target_day,
        rt.get("drip_wall_fert", 0) / 60.0,
        rt.get("drip_center_fert", 0) / 60.0,
        rt.get("mister_south_fert", 0) / 60.0,
        rt.get("mister_west_fert", 0) / 60.0,
        rt.get("fert_master_valve", 0) / 60.0,
        clean_irrigation_runtime_h,
        fert_runtime_h,
        clean_irrigation_runtime_h + fert_runtime_h,
        cycles.get("drip_wall_fert", 0),
        cycles.get("drip_center_fert", 0),
        cycles.get("mister_south_fert", 0),
        cycles.get("mister_west_fert", 0),
        cycles.get("fert_master_valve", 0),
        irrigation_water_gal,
        irrigation_water_gal,
    )
    await conn.execute(
        """
        UPDATE daily_summary ds
           SET kwh_total = ed.measured_kwh::double precision,
               peak_kw = (ed.peak_watts / 1000.0)::double precision,
               captured_at = now()
          FROM v_energy_daily ed
         WHERE ds.date = $1
           AND ed.date = ds.date
           AND ed.measured_kwh IS NOT NULL
        """,
        target_day,
    )

    temp_max = float(climate["temp_max"]) if climate and climate["temp_max"] else 0.0
    return ct, temp_max, compliance_pct


async def daily_summary_live(pool: asyncpg.Pool) -> None:
    """Update recent daily_summary rows with live running aggregates.

    Two-writer contract (paired with ingestor.py::write_daily_summary):
      - `write_daily_summary` owns the midnight UPSERT of raw ESP32 accumulators.
      - This function owns the 30-min UPDATE of derived aggregates:
        climate min/max/avg, stress_hours_*, compliance_pct (temp/vpd/both),
        min_dp_margin_f, dp_risk_hours, kwh_estimated, therms_estimated,
        cost_electric/gas/water/total. It also rewrites cycles/runtimes
        computed from equipment_state transitions — which overrides the
        midnight ESP32-accumulator values for the current day (intentional:
        equipment-state-derived is the higher-fidelity source).
    """
    async with pool.acquire() as conn:
        today = await conn.fetchval("SELECT (now() AT TIME ZONE 'America/Denver')::date")
        refreshed = []
        for offset in (0, 1):
            day = today - _td(days=offset)
            ct, temp_max, compliance_pct = await _refresh_daily_summary_for_date(conn, day)
            refreshed.append((day, ct, temp_max, compliance_pct))

    latest_day, ct, temp_max, compliance_pct = refreshed[0]
    log.info(
        "Daily summary live: %s $%.2f, %.1f°F max, compliance %.1f%% (yesterday also refreshed)",
        latest_day,
        ct,
        temp_max,
        compliance_pct,
    )


# ═════════════════════════════════════════════════════════════════
# 15. PLANNING HEARTBEAT (every 60s) — Iris event-driven planner
# ═════════════════════════════════════════════════════════════════

from astral import LocationInfo
from astral.sun import sun as _sun
from iris_planner import CONTEXT_GATHER_FAILED_SENTINEL, gather_context, prepare_delivery_result, send_to_iris
from planner_routing import (
    SeverityContext,
    classify_severity,
    pick_instance,
    sla_for,
)

_LOCATION = LocationInfo("Longmont", "USA", "America/Denver", 40.1672, -105.1019)
_DENVER = ZoneInfo("America/Denver")

# Milestone state — persisted to disk across restarts
_milestones_cache: dict[str, datetime] = {}
_milestones_fired: dict[str, bool] = {}
_milestones_date: str = ""
_last_forecast_fetch: str = ""
_MILESTONE_STATE_FILE = STATE_DIR / "milestones-fired.json"


@dataclass(frozen=True)
class PlannerTriggerSpec:
    """Canonical contract for planner trigger emission and resolution."""

    event_type: str
    event_label: str
    due_source: str
    expected_action: str
    required_plan: bool
    normal_window_seconds: int | None
    catchup_seconds: int | None
    hermes_route: str = "hermes-iris"
    severity_event_type: str | None = None
    materialize_expected: bool = False


PLANNER_TRIGGER_MATRIX: dict[str, PlannerTriggerSpec] = {
    "MIDNIGHT": PlannerTriggerSpec(
        event_type="MIDNIGHT",
        event_label="End-of-day review and reset",
        due_source="local.midnight_review",
        expected_action="set_plan",
        required_plan=True,
        normal_window_seconds=300,
        catchup_seconds=7200,
        materialize_expected=True,
    ),
    "SUNRISE": PlannerTriggerSpec(
        event_type="SUNRISE",
        event_label="Morning planning cycle",
        due_source="solar.sunrise",
        expected_action="set_plan",
        required_plan=True,
        normal_window_seconds=300,
        catchup_seconds=7200,
        materialize_expected=True,
    ),
    "SOLAR_MAX": PlannerTriggerSpec(
        event_type="SOLAR_MAX",
        event_label="Solar peak planning checkpoint",
        due_source="solar.noon",
        expected_action="any",
        required_plan=False,
        normal_window_seconds=300,
        catchup_seconds=7200,
        materialize_expected=True,
    ),
    "TRANSITION:peak_stress": PlannerTriggerSpec(
        event_type="TRANSITION",
        event_label="Peak Stress",
        due_source="solar.noon+2h",
        expected_action="any",
        required_plan=False,
        normal_window_seconds=300,
        catchup_seconds=7200,
        materialize_expected=True,
    ),
    "TRANSITION:decline": PlannerTriggerSpec(
        event_type="TRANSITION",
        event_label="Decline",
        due_source="solar.sunset-1h",
        expected_action="any",
        required_plan=False,
        normal_window_seconds=300,
        catchup_seconds=7200,
        materialize_expected=True,
    ),
    "SUNSET": PlannerTriggerSpec(
        event_type="SUNSET",
        event_label="Evening planning cycle",
        due_source="solar.sunset",
        expected_action="set_plan",
        required_plan=True,
        normal_window_seconds=300,
        catchup_seconds=7200,
        materialize_expected=True,
    ),
    "FORECAST_DEVIATION": PlannerTriggerSpec(
        event_type="FORECAST_DEVIATION",
        event_label="Observed-vs-forecast breach",
        due_source="forecast_deviation_check",
        expected_action="any",
        required_plan=False,
        normal_window_seconds=None,
        catchup_seconds=300,
        severity_event_type="DEVIATION",
    ),
    "MANUAL": PlannerTriggerSpec(
        event_type="MANUAL",
        event_label="Ad-hoc planning cycle via MCP plan_run",
        due_source="mcp.plan_run",
        expected_action="any",
        required_plan=False,
        normal_window_seconds=None,
        catchup_seconds=None,
    ),
}


def _trigger_spec_for_event(event_type: str, label: str | None = None) -> PlannerTriggerSpec | None:
    normalized_label = (label or "").lower()
    matches = [spec for spec in PLANNER_TRIGGER_MATRIX.values() if spec.event_type == event_type]
    if len(matches) <= 1 or not normalized_label:
        return matches[0] if matches else None
    for spec in matches:
        if normalized_label.startswith(spec.event_label.lower()):
            return spec
    return matches[0] if matches else None


def _milestone_due_at(key: str, solar: dict[str, datetime], today) -> datetime:
    spec = PLANNER_TRIGGER_MATRIX[key]
    if spec.due_source == "local.midnight_review":
        return datetime(today.year, today.month, today.day, 0, 15, tzinfo=_DENVER)
    if spec.due_source == "solar.sunrise":
        return solar["sunrise"]
    if spec.due_source == "solar.noon":
        return solar["noon"]
    if spec.due_source == "solar.noon+2h":
        return solar["noon"] + _td(hours=2)
    if spec.due_source == "solar.sunset-1h":
        return solar["sunset"] - _td(hours=1)
    if spec.due_source == "solar.sunset":
        return solar["sunset"]
    raise ValueError(f"planner trigger {key!r} has no scheduled due time")


def _load_milestone_state():
    """Load fired milestones from disk (survives restarts)."""
    global _milestones_fired, _milestones_date
    try:
        if _MILESTONE_STATE_FILE.exists():
            data = json.loads(_MILESTONE_STATE_FILE.read_text())
            if data.get("date") == datetime.now(_DENVER).strftime("%Y-%m-%d"):
                _milestones_fired = data.get("fired", {})
                _milestones_date = data["date"]
                log.info("Loaded milestone state: %d fired today", len(_milestones_fired))
    except Exception as e:
        log.warning("Could not load milestone state: %s", e)


def _save_milestone_state():
    """Save fired milestones to disk."""
    try:
        data = {"date": _milestones_date, "fired": _milestones_fired}
        _MILESTONE_STATE_FILE.write_text(json.dumps(data))
    except Exception as e:
        log.warning("Could not save milestone state: %s", e)


def _compute_milestones() -> dict[str, datetime]:
    """Compute today's planning milestones from solar ephemeris. Cached per day."""
    global _milestones_cache, _milestones_fired, _milestones_date

    today_str = datetime.now(_DENVER).strftime("%Y-%m-%d")
    if _milestones_date == today_str:
        return _milestones_cache

    # New day — reset state
    _milestones_date = today_str
    _milestones_fired = {}

    today = datetime.now(_DENVER).date()
    s = _sun(_LOCATION.observer, date=today, tzinfo=_DENVER)

    # The scheduled subset is derived from PLANNER_TRIGGER_MATRIX so the
    # trigger contract, ledger expectations, labels, and catch-up behavior do
    # not drift apart. Phase 4 retired PRE_DAWN/MORNING/MIDDAY/AFTERNOON/
    # EVENING fixed boundaries and the raw FORECAST poll because they produced
    # low-signal cycles. MIDNIGHT remains as a short-window required review.
    now_local = datetime.now(_DENVER)
    _milestones_cache = {}
    for key, spec in PLANNER_TRIGGER_MATRIX.items():
        if not spec.materialize_expected or key == "MIDNIGHT":
            continue
        _milestones_cache[key] = _milestone_due_at(key, s, today)

    # Include the midnight review only during its catch-up window so a midday
    # process restart does not create a stale missed trigger for 00:15.
    midnight_review = _milestone_due_at("MIDNIGHT", s, today)
    midnight_catchup_s = PLANNER_TRIGGER_MATRIX["MIDNIGHT"].catchup_seconds or 0
    if now_local <= midnight_review + _td(seconds=midnight_catchup_s):
        _milestones_cache = {"MIDNIGHT": midnight_review, **_milestones_cache}

    # Load any previously fired milestones from disk (in case of restart)
    _load_milestone_state()

    return _milestones_cache


def _milestone_event(key: str, *, catchup: bool = False) -> tuple[str, str]:
    """Return the planner event_type/label for a scheduled milestone key."""
    spec = PLANNER_TRIGGER_MATRIX[key]
    catchup_tag = " (catch-up)" if catchup else ""
    return spec.event_type, f"{spec.event_label}{catchup_tag}"


def _expected_action_for_event(event_type: str, label: str | None = None) -> str:
    """Planner action expected to close the trigger SLA."""
    normalized = (label or "").lower()
    if normalized.startswith("validation") and "ack-only" in normalized:
        return "acknowledge_trigger"
    spec = _trigger_spec_for_event(event_type, label)
    return spec.expected_action if spec else "any"


def _sla_seconds(event_type: str, instance: str | None) -> int | None:
    if not instance:
        return None
    try:
        sla = sla_for(event_type, instance)  # type: ignore[arg-type]
    except Exception:
        return None
    if sla is None:
        return None
    return int(sla.total_seconds())


async def _ensure_expected_planner_triggers(
    conn: asyncpg.Connection,
    milestones: dict[str, datetime],
) -> dict[str, int]:
    """Materialize today's expected trigger ledger before delivery happens.

    plan_delivery_log only exists after a Hermes POST returns. This ledger is
    written first so a missed SUNRISE/SUNSET is visible even if the POST path
    never runs.
    """
    ledger_ids: dict[str, int] = {}
    for key, expected_at in milestones.items():
        event_type, label = _milestone_event(key, catchup=False)
        severity = classify_severity(event_type, SeverityContext())
        instance = pick_instance(event_type, severity)
        sla_s = _sla_seconds(event_type, instance)
        due_at = expected_at + _td(seconds=sla_s or 7200)
        expected_action = _expected_action_for_event(event_type, label)
        ledger_id = await conn.fetchval(
            """
            INSERT INTO planner_trigger_ledger
              (greenhouse_id, event_type, event_label, instance, expected_at,
               due_at, expected_action, sla_seconds)
            VALUES ('vallery', $1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (greenhouse_id, event_type, expected_at) DO UPDATE
               SET event_label     = EXCLUDED.event_label,
                   instance        = EXCLUDED.instance,
                   due_at          = EXCLUDED.due_at,
                   expected_action = EXCLUDED.expected_action,
                   sla_seconds     = EXCLUDED.sla_seconds,
                   updated_at      = now()
             WHERE planner_trigger_ledger.status = 'expected'
            RETURNING id
            """,
            event_type,
            label,
            instance,
            expected_at,
            due_at,
            expected_action,
            sla_s,
        )
        if ledger_id is None:
            ledger_id = await conn.fetchval(
                """
                SELECT id
                  FROM planner_trigger_ledger
                 WHERE greenhouse_id = 'vallery'
                   AND event_type = $1
                   AND expected_at = $2
                """,
                event_type,
                expected_at,
            )
        if ledger_id is not None:
            await conn.execute(
                """
                WITH matched_delivery AS (
                    SELECT id, delivered_at, trigger_id, resulting_plan_id, status, gateway_body
                     FROM plan_delivery_log
                     WHERE event_type = $2
                       AND delivered_at BETWEEN $3::timestamptz - interval '5 minutes'
                                            AND $3::timestamptz + interval '2 hours'
                       AND ($4::text IS NULL OR event_label ILIKE $4::text || '%')
                     ORDER BY
                       CASE status
                         WHEN 'plan_written' THEN 0
                         WHEN 'acked' THEN 1
                         WHEN 'pending' THEN 2
                         WHEN 'delivery_failed' THEN 3
                         WHEN 'timed_out' THEN 4
                         ELSE 5
                       END,
                       delivered_at ASC
                     LIMIT 1
                )
                UPDATE planner_trigger_ledger ptl
                   SET delivered_at         = md.delivered_at,
                       plan_delivery_log_id = md.id,
                       trigger_id           = md.trigger_id,
                       resulting_plan_id    = md.resulting_plan_id,
                       status               = CASE
                                                WHEN md.status IN ('acked', 'plan_written', 'timed_out', 'delivery_failed')
                                                THEN md.status
                                                ELSE 'delivered'
                                              END,
                       resolved_at          = CASE
                                                WHEN md.status IN ('acked', 'plan_written', 'timed_out', 'delivery_failed')
                                                THEN COALESCE(ptl.resolved_at, now())
                                                ELSE ptl.resolved_at
                                              END,
                       notes                = COALESCE(ptl.notes, md.gateway_body),
                       updated_at           = now()
                  FROM matched_delivery md
                 WHERE ptl.id = $1
                   AND ptl.plan_delivery_log_id IS NULL
                """,
                int(ledger_id),
                event_type,
                expected_at,
                label,
            )
            ledger_ids[key] = int(ledger_id)
    return ledger_ids


async def _mark_expected_trigger_delivered(
    pool: asyncpg.Pool,
    *,
    expected_trigger_id: int | None,
    delivery_log_id: int | None,
    result: dict,
    catchup: bool,
) -> None:
    if expected_trigger_id is None:
        return
    status = "delivered"
    if result.get("status") == "delivery_failed" or result.get("delivered") is False:
        status = "delivery_failed"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE planner_trigger_ledger
               SET delivered_at         = COALESCE($2, now()),
                   status               = $3,
                   resolved_at          = CASE
                                            WHEN $3 = 'delivered' THEN NULL
                                            ELSE COALESCE(resolved_at, now())
                                          END,
                   catchup              = $4,
                   plan_delivery_log_id = $5,
                   trigger_id           = $6::uuid,
                   notes                = $7,
                   updated_at           = now()
             WHERE id = $1
               AND status IN ('expected', 'missed', 'delivery_failed', 'delivered')
            """,
            expected_trigger_id,
            datetime.now(UTC),
            status,
            catchup,
            delivery_log_id,
            result.get("trigger_id"),
            (result.get("gateway_body") or "")[:1000],
        )


async def _sync_planner_trigger_ledger(conn: asyncpg.Connection) -> None:
    """Copy delivery-log terminal state onto the expected-trigger ledger."""
    await conn.execute(
        """
        UPDATE planner_trigger_ledger ptl
           SET status            = pdl.status,
               resolved_at       = CASE
                                      WHEN pdl.status IN ('plan_written', 'acked', 'timed_out', 'delivery_failed')
                                      THEN COALESCE(ptl.resolved_at, now())
                                      ELSE ptl.resolved_at
                                    END,
               delivered_at      = COALESCE(ptl.delivered_at, pdl.delivered_at),
               resulting_plan_id = pdl.resulting_plan_id,
               trigger_id        = COALESCE(ptl.trigger_id, pdl.trigger_id),
               updated_at        = now()
          FROM plan_delivery_log pdl
         WHERE ptl.plan_delivery_log_id = pdl.id
           AND pdl.status IN ('acked', 'plan_written', 'timed_out', 'delivery_failed')
           AND ptl.status IS DISTINCT FROM pdl.status
        """
    )


async def _expire_planner_trigger_slas(pool: asyncpg.Pool) -> None:
    """Advance planner trigger lifecycle states based on per-trigger SLAs."""
    async with pool.acquire() as conn:
        pending = await conn.fetch(
            """
            SELECT id, event_type, instance, delivered_at
              FROM plan_delivery_log
             WHERE status = 'pending'
               AND delivered_at > now() - interval '48 hours'
            """
        )
        now_utc = datetime.now(UTC)
        for row in pending:
            sla_s = _sla_seconds(row["event_type"], row["instance"])
            if sla_s is None:
                continue
            if row["delivered_at"] + _td(seconds=sla_s) <= now_utc:
                await conn.execute(
                    """
                    UPDATE plan_delivery_log
                       SET status = 'timed_out',
                           gateway_body = concat_ws(E'\n', NULLIF(gateway_body, ''), $2::text)
                     WHERE id = $1
                       AND status = 'pending'
                    """,
                    row["id"],
                    f"SLA timed out after {sla_s}s",
                )

        await conn.execute(
            """
            UPDATE planner_trigger_ledger
               SET status      = 'missed',
                   resolved_at = now(),
                   notes       = concat_ws(E'\n', NULLIF(notes, ''), 'expected trigger was not delivered before due_at'),
                   updated_at  = now()
             WHERE status = 'expected'
               AND delivered_at IS NULL
               AND due_at < now()
            """
        )
        await conn.execute(
            """
            UPDATE planner_trigger_ledger
               SET status      = 'timed_out',
                   resolved_at = now(),
                   notes       = concat_ws(E'\n', NULLIF(notes, ''), 'delivered trigger did not resolve before due_at'),
                   updated_at  = now()
             WHERE status = 'delivered'
               AND due_at < now()
            """
        )
        await _sync_planner_trigger_ledger(conn)


async def _log_plan_delivery(pool: asyncpg.Pool, result: dict) -> int | None:
    """F14 (Sprint 24.6): persist a send_to_iris result to plan_delivery_log
    for later delivery→plan correlation. Validated through PlanDeliveryLogRow
    before INSERT/UPSERT; a ValidationError here means an unexpected event_type
    (not in the Literal) or wake_mode — safer to drop than bleed bad data.

    Sprint 24.9 (G-7): honor an explicit `status` in the result dict when
    present (e.g., 'delivery_failed' from the context-gather stub path).
    When absent, the DB default 'pending' applies — unchanged from before.
    """
    row = {
        "event_type": result["event_type"],
        "event_label": result.get("event_label"),
        "session_key": result.get("session_key"),
        "wake_mode": result.get("wake_mode"),
        "gateway_status": result.get("gateway_status"),
        "gateway_body": result.get("gateway_body"),
    }
    try:
        PlanDeliveryLogRow.model_validate(row)
    except ValidationError as e:
        log.error("plan_delivery_log skipped (validation failed: %s): %r", e, row)
        return
    explicit_status = result.get("status")
    if explicit_status is None and result.get("delivered") is False and result.get("gateway_status") is not None:
        explicit_status = "delivery_failed"
    # Contract v1.4 §2.D — both columns now populated on every INSERT so
    # correlation queries can match deliveries to plans by uuid (not
    # just the 2h time-window fallback in _resolve_delivery_log).
    trigger_id = result.get("trigger_id")
    instance = result.get("instance")
    # send_to_iris returns Hermes's /v1/runs run_id alongside the existing
    # gateway_status / gateway_body; stamped to plan_delivery_log.hermes_run_id
    # (migration 114) for downstream Hermes-telemetry joins.
    hermes_run_id = result.get("hermes_run_id")
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            INSERT INTO plan_delivery_log AS pdl
              (event_type, event_label, session_key, wake_mode, gateway_status, gateway_body,
               status, trigger_id, instance, hermes_run_id)
            VALUES ($1, $2, $3, $4, $5, $6, COALESCE($7, 'pending'), $8::uuid, $9, $10)
            ON CONFLICT (trigger_id) DO UPDATE
              SET event_type     = EXCLUDED.event_type,
                  event_label    = EXCLUDED.event_label,
                  session_key    = COALESCE(EXCLUDED.session_key, pdl.session_key),
                  wake_mode      = COALESCE(EXCLUDED.wake_mode, pdl.wake_mode),
                  gateway_status = EXCLUDED.gateway_status,
                  gateway_body   = COALESCE(EXCLUDED.gateway_body, pdl.gateway_body),
                  instance       = COALESCE(EXCLUDED.instance, pdl.instance),
                  hermes_run_id  = COALESCE(EXCLUDED.hermes_run_id, pdl.hermes_run_id),
                  status         = CASE
                                     WHEN pdl.status IN ('acked', 'plan_written') THEN pdl.status
                                     ELSE EXCLUDED.status
                                   END
            RETURNING id
            """,
            row["event_type"],
            row["event_label"],
            row["session_key"],
            row["wake_mode"],
            row["gateway_status"],
            row["gateway_body"],
            explicit_status,
            trigger_id,
            instance,
            hermes_run_id,
        )


async def _deliver_and_log(
    pool: asyncpg.Pool,
    event_type: str,
    label: str,
    context: str,
    instance: str = "opus",
    expected_trigger_id: int | None = None,
    catchup: bool = False,
) -> None:
    """Call send_to_iris in the executor and persist the outcome to
    plan_delivery_log. Called from every milestone/forecast/deviation path
    so F14 correlation is complete regardless of trigger source.

    Sprint 24.9 (G-7): if context gathering failed upstream, skip the POST
    entirely and log a plan_delivery_log row with status='delivery_failed'.
    Previously the failure string was spliced into the prompt and Iris
    received gibberish context — the 2026-04-19 incident had gather failures
    that still produced 200 OK dispatches but no useful plan.
    """
    if context == CONTEXT_GATHER_FAILED_SENTINEL:
        log.warning(
            "Skipping %s/%s dispatch: context gathering failed (see alert_log plan_context_failed)",
            event_type,
            label,
        )
        # Write a stub plan_delivery_log row so the outage is visible in
        # operational queries alongside successful deliveries. Generate a
        # trigger_id even for the sentinel path so context-gather failures
        # are countable and individually traceable.
        stub_result = {
            "delivered": False,
            "event_type": event_type,
            "event_label": label,
            "session_key": None,
            "wake_mode": None,
            "gateway_status": None,
            "gateway_body": "context_gather_failed",
            "status": "delivery_failed",
            "trigger_id": str(uuid.uuid4()),
            "instance": instance,
        }
        delivery_id = await _log_plan_delivery(pool, stub_result)
        await _mark_expected_trigger_delivered(
            pool,
            expected_trigger_id=expected_trigger_id,
            delivery_log_id=delivery_id,
            result=stub_result,
            catchup=catchup,
        )
        return

    pre_result = prepare_delivery_result(event_type, label, instance=instance)
    delivery_id = await _log_plan_delivery(pool, pre_result)
    if delivery_id is None:
        log.error("Skipping %s/%s dispatch: failed to pre-create plan_delivery_log row", event_type, label)
        return
    if pre_result.get("status") == "delivery_failed":
        await _mark_expected_trigger_delivered(
            pool,
            expected_trigger_id=expected_trigger_id,
            delivery_log_id=delivery_id,
            result=pre_result,
            catchup=catchup,
        )
        return

    loop = asyncio.get_event_loop()
    # Threaded executor + kwargs: use a lambda so `instance` and the pre-created
    # trigger_id propagate to send_to_iris cleanly (run_in_executor doesn't
    # accept kwargs directly).
    result = await loop.run_in_executor(
        None,
        lambda: send_to_iris(
            event_type,
            label,
            context,
            instance=instance,
            trigger_id=pre_result["trigger_id"],
        ),
    )
    delivery_id = await _log_plan_delivery(pool, result) or delivery_id
    await _mark_expected_trigger_delivered(
        pool,
        expected_trigger_id=expected_trigger_id,
        delivery_log_id=delivery_id,
        result=result,
        catchup=catchup,
    )


async def _resolve_delivery_log(pool: asyncpg.Pool) -> None:
    """F14 (Sprint 24.6): for each unresolved plan_delivery_log row, look for
    a plan_journal entry created after it and within a 2-hour window. Updates
    `resulting_plan_id` + `plan_written_at`. Bounded to the last 6 hours and
    events that can produce plans (SUNRISE/SUNSET/DEVIATION always do;
    TRANSITION/FORECAST only when Iris chooses to update).

    Sprint 24.9 (G-3): also set status='plan_written' so consumers querying
    `SELECT status, count(*)` see consistent state. Contract v1.4 §2.D
    defines status as the authoritative lifecycle column; resulting_plan_id
    is the join key. Keep them in sync.
    """
    async with pool.acquire() as conn:
        # Contract v1.4 primary path: exact UUID correlation. This remains the
        # only reliable join across legacy audit labels and Hermes run IDs
        # inside the old 2h fallback window.
        await conn.execute(
            """
            UPDATE plan_delivery_log pdl
               SET resulting_plan_id = pj.plan_id,
                   plan_written_at   = pj.created_at,
                   status            = 'plan_written'
              FROM plan_journal pj
             WHERE pdl.resulting_plan_id IS NULL
               AND pdl.status = 'pending'
               AND pdl.gateway_status BETWEEN 200 AND 299
               AND pdl.trigger_id IS NOT NULL
               AND pj.trigger_id = pdl.trigger_id
            """,
        )
        # Legacy fallback for pre-v1.4 rows only. If either side has a UUID,
        # exact trigger_id correlation above is authoritative; do not attach
        # a later unrelated plan by time window.
        await conn.execute(
            """
            UPDATE plan_delivery_log pdl
               SET resulting_plan_id = pj.plan_id,
                   plan_written_at   = pj.created_at,
                   status            = 'plan_written'
              FROM plan_journal pj
             WHERE pdl.resulting_plan_id IS NULL
               AND pdl.status = 'pending'
               AND pdl.gateway_status BETWEEN 200 AND 299
               AND pdl.trigger_id IS NULL
               AND pj.trigger_id IS NULL
               AND pdl.delivered_at > now() - interval '6 hours'
               AND pj.created_at BETWEEN pdl.delivered_at AND pdl.delivered_at + interval '2 hours'
               AND pj.created_at = (
                   SELECT MIN(p2.created_at) FROM plan_journal p2
                    WHERE p2.created_at BETWEEN pdl.delivered_at AND pdl.delivered_at + interval '2 hours'
               )
            """,
        )


async def planner_memory_ingest_sync(pool: asyncpg.Pool) -> None:
    """Best-effort planner memory ingestion outside the planner run loop.

    First rollout intentionally keeps scope narrow:
    - observed_outcome from validated plan_journal rows
    - optional prior_plan summaries from the same rows
    - optional support_doc seeding from a local JSON file

    Failures are logged and skipped; this path must never block greenhouse ops.
    """

    from planner_memory_ingest import (
        PlannerMemoryIngestError,
        PlannerMemoryItem,
        build_outcome_memory_item,
        build_prior_plan_memory_item,
        ingest_planner_memory_batch,
        load_memory_ingest_state,
        load_support_doc_items,
        planner_memory_ingest_enabled,
        planner_memory_ingest_max_batch_items,
        planner_memory_ingest_outcomes_enabled,
        planner_memory_ingest_prior_plans_enabled,
        planner_memory_ingest_support_docs_enabled,
        planner_memory_support_docs_file,
        save_memory_ingest_state,
    )

    if not planner_memory_ingest_enabled():
        return

    state = load_memory_ingest_state()
    seeded_support_doc_ids = {
        str(item_id)
        for item_id in state.get("seeded_support_doc_ids", [])
        if isinstance(item_id, str) and item_id.strip()
    }
    max_items = planner_memory_ingest_max_batch_items()
    support_items: list[PlannerMemoryItem] = []
    if planner_memory_ingest_support_docs_enabled():
        support_file = planner_memory_support_docs_file()
        if support_file and support_file.exists():
            try:
                all_support_items = load_support_doc_items(support_file, greenhouse_id=GREENHOUSE_ID)
                support_items = [item for item in all_support_items if item.source_id not in seeded_support_doc_ids][
                    :max_items
                ]
            except (OSError, ValueError, TypeError, PlannerMemoryIngestError) as exc:
                log.warning("planner memory support-doc load failed: %s", exc)

    journal_items: list[PlannerMemoryItem] = []
    max_journal_rows = max(1, max_items - len(support_items))
    latest_validated_at_raw = state.get("last_validated_at")
    latest_validated_at: datetime | None = None
    if isinstance(latest_validated_at_raw, str) and latest_validated_at_raw.strip():
        try:
            latest_validated_at = datetime.fromisoformat(latest_validated_at_raw)
        except ValueError:
            log.warning("planner memory ingest ignored invalid watermark: %r", latest_validated_at_raw)
    if max_journal_rows > 0 and (
        planner_memory_ingest_outcomes_enabled() or planner_memory_ingest_prior_plans_enabled()
    ):
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT pj.plan_id,
                       pj.trigger_id,
                       pj.created_at,
                       pj.validated_at,
                       pj.expected_outcome,
                       pj.actual_outcome,
                       pj.outcome_score,
                       pj.anchor_score,
                       pj.lesson_extracted,
                       pdl.event_type,
                       pdl.instance AS planner_instance,
                       pj.hypothesis
                  FROM plan_journal pj
             LEFT JOIN plan_delivery_log pdl
                    ON pdl.trigger_id = pj.trigger_id
                 WHERE pj.validated_at IS NOT NULL
                   AND ($1::timestamptz IS NULL OR pj.validated_at > $1::timestamptz)
                   AND pj.plan_id NOT LIKE 'iris-reactive%'
              ORDER BY pj.validated_at ASC
                 LIMIT $2
                """,
                latest_validated_at,
                max_journal_rows,
            )
        for row in rows:
            row_dict = dict(row)
            if planner_memory_ingest_outcomes_enabled():
                journal_items.append(build_outcome_memory_item(row_dict))
            if planner_memory_ingest_prior_plans_enabled() and len(journal_items) < max_items:
                journal_items.append(build_prior_plan_memory_item(row_dict))

    items = [*support_items, *journal_items][:max_items]
    if not items:
        return

    batch_id = f"verdify-memory-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    result = ingest_planner_memory_batch(items=items, batch_id=batch_id)
    if result.status >= 400:
        raise PlannerMemoryIngestError(f"memory ingest failed: HTTP {result.status}: {result.body}")
    body = result.body if isinstance(result.body, dict) else {}
    log.info(
        "planner memory ingest batch=%s items=%d accepted=%s duplicates=%s rejected=%s",
        batch_id,
        len(items),
        body.get("accepted_count"),
        body.get("duplicate_count"),
        body.get("rejected_count"),
    )

    new_support_ids = [item.source_id for item in support_items]
    seeded_support_doc_ids.update(new_support_ids)
    validated_rows = [
        item
        for item in journal_items
        if item.memory_type == "observed_outcome" and item.payload and item.payload.get("validated_at")
    ]
    if validated_rows:
        state["last_validated_at"] = max(str(item.payload["validated_at"]) for item in validated_rows)
    state["seeded_support_doc_ids"] = sorted(seeded_support_doc_ids)
    save_memory_ingest_state(state)


async def planning_heartbeat(pool: asyncpg.Pool) -> None:
    """Check if a planning event should fire. Triggers Iris planner session."""
    now = datetime.now(_DENVER)

    # ── 1. Compute/cache today's milestones ──
    all_milestones = _compute_milestones()
    if not _milestones_fired:
        log.info("Planning milestones: %s", ", ".join(f"{k}={v.strftime('%H:%M')}" for k, v in all_milestones.items()))
    expected_trigger_ids: dict[str, int] = {}
    try:
        async with pool.acquire() as conn:
            expected_trigger_ids = await _ensure_expected_planner_triggers(conn, all_milestones)
        await _expire_planner_trigger_slas(pool)
    except Exception as e:
        log.warning("planner expected-trigger ledger refresh failed: %s", e)

    # ── 2. Check each milestone ──
    for key, milestone_time in all_milestones.items():
        if key in _milestones_fired:
            continue
        spec = PLANNER_TRIGGER_MATRIX[key]

        # Fire if we're within the window after the milestone
        # Normal and catch-up windows are defined by PLANNER_TRIGGER_MATRIX.
        delta = (now - milestone_time).total_seconds()
        normal_window_s = spec.normal_window_seconds or 0
        catchup_window_s = spec.catchup_seconds or 0
        is_catchup = normal_window_s <= delta < catchup_window_s
        if 0 <= delta < normal_window_s or is_catchup:
            event_type, label = _milestone_event(key, catchup=is_catchup)

            # Phase 4 cadence ceiling: if a SUNRISE plan was already written
            # in the last 4 hours, drop another SUNRISE trigger. The 2026-04-10
            # hot-cadence regression (41 plans in 24h, ~one every 36 min)
            # showed catch-up firing produced cascading duplicates of the
            # same posture decision. Skipping here keeps the catch-up safety
            # net for SUNSET/SOLAR_MAX/TRANSITION without letting SUNRISE
            # rewrite itself mid-morning.
            if event_type == "SUNRISE":
                try:
                    async with pool.acquire() as conn:
                        recent_sunrise = await conn.fetchval(
                            """
                            SELECT 1 FROM plan_journal
                             WHERE plan_id LIKE 'iris-%'
                               AND created_at > now() - interval '4 hours'
                               AND EXTRACT(hour FROM created_at AT TIME ZONE 'America/Denver')
                                     BETWEEN 5 AND 9
                             LIMIT 1
                            """
                        )
                except Exception as e:
                    log.warning("cadence-ceiling check failed (proceeding): %s", e)
                    recent_sunrise = None
                if recent_sunrise:
                    _milestones_fired[key] = True
                    _save_milestone_state()
                    log.info(
                        "Skipping SUNRISE %s — another SUNRISE plan exists within last 4h (Phase 4 cadence ceiling)",
                        key,
                    )
                    continue

            _milestones_fired[key] = True
            _save_milestone_state()

            log.info("Planning milestone fired: %s (%s)%s", key, label, " [CATCH-UP]" if is_catchup else "")

            # Gather context and send to Iris (blocking — runs in executor).
            # Hermes routes normal solar/fixed-boundary transitions through
            # the single audited planner gateway; legacy local/opus labels are
            # audit metadata only.
            loop = asyncio.get_event_loop()
            context = await loop.run_in_executor(None, gather_context)
            severity_event_type = spec.severity_event_type or event_type
            severity = classify_severity(severity_event_type, SeverityContext())
            instance = pick_instance(severity_event_type, severity)
            await _deliver_and_log(
                pool,
                event_type,
                label,
                context,
                instance=instance,
                expected_trigger_id=expected_trigger_ids.get(key),
                catchup=is_catchup,
            )

    # ── 3. FORECAST poll RETIRED (Phase 4, 2026-05-10) ──
    # The "new forecast fetched" event was the highest-volume / lowest-signal
    # planner trigger — it fired every time the Open-Meteo fetcher landed a
    # row, regardless of whether anything in the forecast had actually
    # changed materially. Baseline showed 238 FORECAST timeouts in the
    # ledger and most plans produced a bare acknowledge_trigger. Forecast
    # awareness now ships through the FORECAST_DEVIATION σ-gated path below
    # (Iris is told material forecast changes through the deviation watcher)
    # and through the FORECAST CALIBRATION section of gather-plan-context.sh.
    # _last_forecast_fetch is preserved as a no-op slot for backward-compat
    # of any external readers that import it; do not re-enable the emission
    # without first writing a σ-gated threshold so we don't regress.
    global _last_forecast_fetch
    async with pool.acquire() as conn:
        latest_fetch = await conn.fetchval(
            "SELECT MAX(fetched_at)::text FROM weather_forecast WHERE fetched_at > now() - interval '2 hours'"
        )
        if latest_fetch:
            _last_forecast_fetch = latest_fetch

    # ── 4. Check for FORECAST_DEVIATION (route to Iris through alert_log) ──
    # Was 'DEVIATION'; renamed in Phase 4 so the event_type vocabulary matches
    # the new closed set {SUNRISE, SUNSET, SOLAR_MAX, TRANSITION, FORECAST_DEVIATION, MANUAL}.
    forecast_alert_id: int | None = None
    forecast_trigger_data: dict[str, object] | None = None
    legacy_trigger_file = False
    async with pool.acquire() as conn:
        pending_forecast_alert = await _pending_forecast_deviation_alert(conn)
    if pending_forecast_alert is not None:
        forecast_alert_id, forecast_trigger_data = pending_forecast_alert
    else:
        trigger_file = STATE_DIR / "replan-needed.json"
        import time as _t

        if trigger_file.exists():
            age_s = _t.time() - trigger_file.stat().st_mtime
            if age_s < 300:
                forecast_trigger_data = json.loads(trigger_file.read_text())
                legacy_trigger_file = True

    if forecast_trigger_data is not None:
        try:
            deviations_str = json.dumps(forecast_trigger_data.get("deviations", []), indent=2)
            reason = forecast_trigger_data.get("reason", "Unknown deviation")

            log.info("FORECAST_DEVIATION trigger found, routing to Iris: %s", reason)
            loop = asyncio.get_event_loop()
            context = await loop.run_in_executor(None, gather_context)
            # Pull severity hints from the trigger payload if present.
            # max_abs_deviation is stamped by forecast_deviation_check's
            # AlertEnvelope row when a material miss exceeds its threshold.
            # Both severities route local unless explicitly escalated.
            severity_ctx = SeverityContext(
                max_abs_deviation=forecast_trigger_data.get("max_abs_deviation"),
                consecutive_deviation_cycles=forecast_trigger_data.get("consecutive_cycles"),
            )
            # planner_routing still uses "DEVIATION" internally for the
            # severity/SLA table key; the event_type emitted to Iris
            # and stored in plan_delivery_log / planner_trigger_ledger
            # is "FORECAST_DEVIATION".
            spec = PLANNER_TRIGGER_MATRIX["FORECAST_DEVIATION"]
            severity_event_type = spec.severity_event_type or spec.event_type
            severity = classify_severity(severity_event_type, severity_ctx)
            instance = pick_instance(severity_event_type, severity)
            await _deliver_and_log(
                pool,
                spec.event_type,
                deviations_str,
                context,
                instance=instance,
            )
            if forecast_alert_id is not None:
                async with pool.acquire() as conn:
                    await _resolve_forecast_deviation_alert(conn, forecast_alert_id)
            if legacy_trigger_file:
                trigger_file = STATE_DIR / "replan-needed.json"
                trigger_file.unlink(missing_ok=True)
        except Exception as e:
            log.error("Failed to process FORECAST_DEVIATION trigger: %s", e)

    # ── 4b. Resolve plan_delivery_log entries (F14): update any unresolved
    # rows where a plan landed within 2h of delivery. Runs every heartbeat
    # so the correlation updates in near-real-time, not just at the 30-min
    # verify check below.
    try:
        await _resolve_delivery_log(pool)
        await _expire_planner_trigger_slas(pool)
    except Exception as e:
        log.warning("plan_delivery_log/planner_trigger_ledger resolve failed: %s", e)

    # ── 5. Verify plan delivery (30 min after SUNRISE/SUNSET) ──
    for key in ("SUNRISE", "SUNSET"):
        if key not in _milestones_fired:
            continue
        milestone_time = all_milestones.get(key)
        if not milestone_time:
            continue
        mins_since = (now - milestone_time).total_seconds() / 60
        verify_key = f"_verified_{key}"
        if 30 <= mins_since < 35 and verify_key not in _milestones_fired:
            _milestones_fired[verify_key] = True
            _save_milestone_state()
            # Check if a plan was written after the milestone
            async with pool.acquire() as conn:
                plan_count = await conn.fetchval(
                    "SELECT count(*) FROM plan_journal WHERE created_at > $1",
                    milestone_time,
                )
            if plan_count == 0:
                log.warning(
                    "PLAN DELIVERY FAILED: No plan_journal entry after %s (fired %s ago)", key, f"{mins_since:.0f}m"
                )
                # Post alert to Slack
                try:
                    token = _load_token(SLACK_TOKEN_FILE)
                    _post_slack(
                        token,
                        SLACK_CHANNEL,
                        f":warning: *Planning alert:* No plan written after {key} event "
                        f"({mins_since:.0f} min ago). Iris may have failed to process the event. "
                        f"<@U0A9KJHFJSU> please check `tmux attach -t agent-iris-planner`.",
                    )
                except Exception:
                    pass
            else:
                log.info("Plan delivery verified: %d plan(s) written after %s", plan_count, key)

    # ── 6. MCP server health check (every heartbeat = 60s) ──
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: urllib.request.urlopen("http://127.0.0.1:8000/mcp", timeout=5),
        )
        # Any response (even 405 Method Not Allowed) means the server is alive
    except urllib.error.HTTPError:
        pass  # Server is alive, just doesn't accept GET on /mcp — that's fine
    except Exception as e:
        log.error("MCP server unreachable: %s", e)
        # Try to restart it
        try:
            import subprocess as _sp_mcp

            _sp_mcp.Popen(
                ["/srv/greenhouse/.venv/bin/python", "mcp/server.py"],
                cwd="/srv/verdify",
                stdout=open("/srv/verdify/state/mcp-server.log", "a"),
                stderr=open("/srv/verdify/state/mcp-server.log", "a"),
            )
            log.warning("MCP server restarted")
            try:
                token = _load_token(SLACK_TOKEN_FILE)
                _post_slack(token, SLACK_CHANNEL, ":warning: MCP server was unreachable — auto-restarted.")
            except Exception:
                pass
        except Exception as restart_err:
            log.error("MCP server restart failed: %s", restart_err)


# ═════════════════════════════════════════════════════════════════
# 16. SETPOINT CONFIRMATION MONITOR (FB-1, Sprint 20 — every 300s)
#
# Closes Milestone A4 feedback loop: every push the dispatcher makes must
# be confirmed by the ESP32's cfg_* readback within 5 minutes, or we open
# a `setpoint_unconfirmed` alert. Confirmation itself happens in
# ingestor.py's setpoint_snapshot pass — this task just fires alerts for
# the rows that stayed NULL.
#
# Only checks params that have a cfg_* readback route (CFG_READBACK_MAP
# values). Params without readback are not monitored — their confirmation
# remains perpetually NULL by design. The 52-param readback gap is
# tracked as a Sprint 21 firmware follow-up.
# ═════════════════════════════════════════════════════════════════
_READBACKABLE_PARAMS: list[str] = sorted(set(CFG_READBACK_MAP.values()))


async def setpoint_confirmation_monitor(pool: asyncpg.Pool) -> None:
    """FB-1: alert on setpoint_changes rows that never confirmed.

    Severity:
      - warning: 5 min < age < 15 min
      - critical: age >= 15 min (escalation)

    Sprint 25-omnibus (setpoint_unconfirmed lifecycle fix): this monitor
    now owns the full lifecycle of setpoint_unconfirmed alerts — both
    creation (below) AND auto-resolution (first pass, next). alert_monitor
    no longer touches source='ingestor' alerts; without this self-resolve
    pass, confirmed setpoints would leave zombie open alerts forever.
    """
    async with pool.acquire() as conn:
        # Pass 1: auto-resolve unresolved alerts whose underlying
        # setpoint_changes row is now confirmed_at NOT NULL. Acknowledged
        # alerts still block deploy preflight until resolved_at is set.
        # Matches on sensor_id's `setpoint.*` suffix back to the parameter.
        resolved = await conn.fetch(
            """
            UPDATE alert_log al
               SET disposition = 'resolved',
                   resolved_at = now(),
                   resolved_by = 'system',
                   resolution  = 'auto-resolved: confirmation landed'
              FROM (
                  SELECT DISTINCT ON (sc.parameter)
                         sc.parameter, sc.ts, sc.confirmed_at
                    FROM setpoint_changes sc
                   WHERE sc.confirmed_at IS NOT NULL
                   ORDER BY sc.parameter, sc.ts DESC
             ) confirmed
             WHERE al.alert_type = 'setpoint_unconfirmed'
               AND al.resolved_at IS NULL
               AND al.disposition IN ('open', 'acknowledged')
               AND al.source = 'ingestor'
               AND al.sensor_id = 'setpoint.' || confirmed.parameter
               AND confirmed.ts >= COALESCE(NULLIF(al.details->>'pushed_at', '')::timestamptz, al.ts)
            RETURNING al.id
            """,
        )
        if resolved:
            log.info("setpoint_unconfirmed: auto-resolved %d alert(s) after confirmation", len(resolved))

        superseded = await conn.fetch(
            """
            UPDATE alert_log al
               SET disposition = 'resolved',
                   resolved_at = now(),
                   resolved_by = 'system',
                   resolution  = 'auto-resolved: superseded by newer setpoint'
             WHERE al.alert_type = 'setpoint_unconfirmed'
               AND al.resolved_at IS NULL
               AND al.disposition IN ('open', 'acknowledged')
               AND al.source = 'ingestor'
               AND EXISTS (
                   SELECT 1
                     FROM setpoint_changes newer
                   WHERE newer.parameter = replace(al.sensor_id, 'setpoint.', '')
                      AND COALESCE(newer.source, '') <> 'esp32'
                      AND newer.ts > COALESCE(NULLIF(al.details->>'pushed_at', '')::timestamptz, al.ts)
               )
            RETURNING al.id
            """,
        )
        if superseded:
            log.info("setpoint_unconfirmed: auto-resolved %d superseded alert(s)", len(superseded))

        superseded_rows = await conn.fetch(
            """
            UPDATE setpoint_changes sc
               SET delivery_status = 'superseded',
                   superseded_by_ts = (
                       SELECT min(newer.ts)
                        FROM setpoint_changes newer
                        WHERE newer.parameter = sc.parameter
                          AND COALESCE(newer.greenhouse_id, '') = COALESCE(sc.greenhouse_id, '')
                          AND COALESCE(newer.source, '') <> 'esp32'
                          AND newer.ts > sc.ts
                   ),
                   expired_at = COALESCE(
                       sc.expired_at,
                       (
                           SELECT min(newer.ts)
                            FROM setpoint_changes newer
                            WHERE newer.parameter = sc.parameter
                              AND COALESCE(newer.greenhouse_id, '') = COALESCE(sc.greenhouse_id, '')
                              AND COALESCE(newer.source, '') <> 'esp32'
                              AND newer.ts > sc.ts
                       )
                   )
             WHERE sc.confirmed_at IS NULL
               AND COALESCE(sc.source, '') <> 'esp32'
               AND COALESCE(sc.delivery_status, 'pending') IN ('pending', 'deferred_heap_pressure')
               AND EXISTS (
                   SELECT 1
                    FROM setpoint_changes newer
                    WHERE newer.parameter = sc.parameter
                      AND COALESCE(newer.greenhouse_id, '') = COALESCE(sc.greenhouse_id, '')
                      AND COALESCE(newer.source, '') <> 'esp32'
                      AND newer.ts > sc.ts
               )
            RETURNING sc.parameter
            """
        )
        if superseded_rows:
            log.info("setpoint_unconfirmed: marked %d stale pending row(s) superseded", len(superseded_rows))

        if FIRMWARE_HAS_PER_CIRCUIT_LIGHTING:
            stale_lighting_rows = await conn.fetch(
                """
                WITH policy AS MATERIALIZED (
                    SELECT * FROM fn_lighting_minutes_policy(now(), 'vallery')
                ),
                current_policy(parameter, value) AS (
                    SELECT 'gl_' || light_key || '_dli_target', legacy_dli_target::double precision
                      FROM policy
                    UNION ALL
                    SELECT 'gl_' || light_key || '_target_light_minutes', target_light_minutes::double precision
                      FROM policy
                    UNION ALL
                    SELECT 'gl_' || light_key || '_sunrise_hour', start_hour::double precision
                      FROM policy
                    UNION ALL
                    SELECT 'gl_' || light_key || '_sunset_hour', cutoff_hour::double precision
                      FROM policy
                    UNION ALL
                    SELECT 'gl_' || light_key || '_lux_threshold', lux_on_threshold::double precision
                      FROM policy
                    UNION ALL
                    SELECT 'gl_' || light_key || '_lux_hysteresis', lux_hysteresis::double precision
                      FROM policy
                    UNION ALL
                    SELECT 'gl_' || light_key || '_min_on_s', min_on_s::double precision
                      FROM policy
                    UNION ALL
                    SELECT 'gl_' || light_key || '_min_off_s', min_off_s::double precision
                      FROM policy
                    UNION ALL
                    SELECT 'sw_gl_' || light_key || '_auto_mode',
                           CASE WHEN auto_enabled THEN 1.0 ELSE 0.0 END
                      FROM policy
                ),
                latest_snapshot AS (
                    SELECT DISTINCT ON (parameter) parameter, value, ts
                      FROM setpoint_snapshot
                     WHERE parameter IN (SELECT parameter FROM current_policy)
                     ORDER BY parameter, ts DESC
                )
                UPDATE setpoint_changes sc
                   SET delivery_status = 'superseded',
                       superseded_by_ts = COALESCE(sc.superseded_by_ts, now()),
                       expired_at = COALESCE(sc.expired_at, now())
                  FROM current_policy cp
                  JOIN latest_snapshot ls ON ls.parameter = cp.parameter
                 WHERE sc.parameter = cp.parameter
                   AND sc.confirmed_at IS NULL
                   AND COALESCE(sc.source, '') <> 'esp32'
                   AND COALESCE(sc.delivery_status, 'pending') IN ('pending', 'deferred_heap_pressure')
                   AND sc.ts > now() - interval '1 day'
                   AND abs(sc.value - cp.value) > 0.001
                   AND abs(ls.value - cp.value) <= 0.001
                RETURNING sc.parameter
                """
            )
            if stale_lighting_rows:
                log.info(
                    "setpoint_unconfirmed: marked %d stale lighting row(s) superseded by current cfg policy",
                    len(stale_lighting_rows),
                )

            legacy_lighting_rows = await conn.fetch(
                """
                UPDATE setpoint_changes sc
                   SET delivery_status = 'superseded',
                       superseded_by_ts = COALESCE(sc.superseded_by_ts, now()),
                       expired_at = COALESCE(sc.expired_at, now())
                 WHERE sc.parameter = ANY($1::text[])
                   AND sc.confirmed_at IS NULL
                   AND COALESCE(sc.source, '') <> 'esp32'
                   AND COALESCE(sc.delivery_status, 'pending') IN ('pending', 'deferred_heap_pressure')
                   AND sc.ts > now() - interval '1 day'
                RETURNING sc.parameter
                """,
                list(LIGHTING_POLICY_PARAMS),
            )
            if legacy_lighting_rows:
                log.info(
                    "setpoint_unconfirmed: marked %d legacy shared lighting row(s) superseded",
                    len(legacy_lighting_rows),
                )

        if SWITCH_CONFIRM_EQUIPMENT:
            switch_values_sql = ", ".join(
                f"('{param}', '{equipment}')"
                for param, equipment in sorted(SWITCH_CONFIRM_EQUIPMENT.items())
                if param not in _READBACKABLE_PARAMS
            )
            if switch_values_sql:
                switch_confirmed = await conn.fetch(
                    f"""
                    WITH switch_map(parameter, equipment) AS (
                        VALUES {switch_values_sql}
                    ),
                    latest_equipment AS (
                        SELECT DISTINCT ON (equipment) equipment, state, ts
                          FROM equipment_state
                         WHERE equipment IN (SELECT equipment FROM switch_map)
                         ORDER BY equipment, ts DESC
                    )
                    UPDATE setpoint_changes sc
                       SET confirmed_at = COALESCE(sc.confirmed_at, now()),
                           delivery_status = 'confirmed'
                      FROM switch_map sm
                      JOIN latest_equipment le ON le.equipment = sm.equipment
                     WHERE sc.parameter = sm.parameter
                       AND sc.confirmed_at IS NULL
                       AND COALESCE(sc.source, '') <> 'esp32'
                       AND COALESCE(sc.delivery_status, 'pending') IN ('pending', 'deferred_heap_pressure')
                       AND sc.ts > now() - interval '1 hour'
                       AND (sc.value >= 0.5) = le.state
                    RETURNING sc.parameter
                    """
                )
                if switch_confirmed:
                    log.info(
                        "setpoint_unconfirmed: confirmed %d switch-only row(s) from equipment_state",
                        len(switch_confirmed),
                    )

        terminal = await conn.fetch(
            """
            UPDATE alert_log al
               SET disposition = 'resolved',
                   resolved_at = now(),
                   resolved_by = 'system',
                   resolution = 'auto-resolved: setpoint row is terminal'
             WHERE al.alert_type = 'setpoint_unconfirmed'
               AND al.resolved_at IS NULL
               AND al.disposition IN ('open', 'acknowledged')
               AND al.source = 'ingestor'
               AND EXISTS (
                   SELECT 1
                     FROM setpoint_changes sc
                    WHERE sc.parameter = replace(al.sensor_id, 'setpoint.', '')
                      AND sc.ts = COALESCE(NULLIF(al.details->>'pushed_at', '')::timestamptz, sc.ts)
                      AND (
                          sc.confirmed_at IS NOT NULL
                          OR sc.superseded_by_ts IS NOT NULL
                          OR sc.expired_at IS NOT NULL
                          OR COALESCE(sc.delivery_status, '') IN ('confirmed', 'superseded')
                      )
               )
            RETURNING al.id
            """
        )
        if terminal:
            log.info("setpoint_unconfirmed: auto-resolved %d terminal alert(s)", len(terminal))

        # Pass 2: scan for still-unconfirmed rows that need alerting.
        rows = await conn.fetch(
            """
            SELECT sc.parameter,
                   sc.value,
                   sc.ts,
                   EXTRACT(EPOCH FROM (now() - sc.ts))::int AS age_s
             FROM setpoint_changes sc
             WHERE sc.confirmed_at IS NULL
               AND COALESCE(sc.source, '') <> 'esp32'
               AND COALESCE(sc.delivery_status, 'pending') = 'pending'
               AND sc.ts < now() - interval '5 minutes'
               AND sc.ts > now() - interval '1 hour'
               AND sc.parameter = ANY($1::text[])
               AND NOT EXISTS (
                   SELECT 1
                     FROM setpoint_changes newer
                    WHERE newer.parameter = sc.parameter
                      AND COALESCE(newer.greenhouse_id, '') = COALESCE(sc.greenhouse_id, '')
                      AND COALESCE(newer.source, '') <> 'esp32'
                      AND newer.ts > sc.ts
               )
             ORDER BY sc.ts DESC
            """,
            _READBACKABLE_PARAMS,
        )
        if not rows:
            return

        for r in rows:
            age_s = int(r["age_s"])
            severity = "critical" if age_s >= 900 else "warning"

            # last cfg readback for that param (best-effort context)
            snap = await conn.fetchrow(
                "SELECT value, ts FROM setpoint_snapshot WHERE parameter=$1 ORDER BY ts DESC LIMIT 1",
                r["parameter"],
            )
            last_cfg = float(snap["value"]) if snap and snap["value"] is not None else None

            # Skip duplicate alerts: one open alert per (parameter, ts) pair.
            existing = await conn.fetchval(
                "SELECT id FROM alert_log "
                "WHERE alert_type='setpoint_unconfirmed' "
                "  AND resolved_at IS NULL "
                "  AND sensor_id=$1",
                f"setpoint.{r['parameter']}",
            )
            if existing is not None:
                # Already alerted — escalate severity only if crossed the 15-min threshold
                if severity == "critical":
                    alert = AlertEnvelope.model_validate(
                        {
                            "alert_type": "setpoint_unconfirmed",
                            "severity": severity,
                            "category": "system",
                            "sensor_id": f"setpoint.{r['parameter']}",
                            "message": (
                                f"Setpoint unconfirmed >15 min: {r['parameter']}={float(r['value']):.3f} "
                                f"pushed at {r['ts']:%H:%M:%S} UTC, last cfg readback "
                                f"{last_cfg if last_cfg is not None else '(none)'}"
                            ),
                            "details": {
                                "parameter": r["parameter"],
                                "requested_value": float(r["value"]),
                                "last_cfg_readback": last_cfg,
                                "age_s": age_s,
                                "pushed_at": r["ts"].isoformat(),
                            },
                        }
                    )
                    await conn.execute(
                        "UPDATE alert_log SET severity='critical', message=$2, details=$3 WHERE id=$1",
                        existing,
                        alert.message,
                        json.dumps(alert.details),
                    )
                continue

            alert = AlertEnvelope.model_validate(
                {
                    "alert_type": "setpoint_unconfirmed",
                    "severity": severity,
                    "category": "system",
                    "sensor_id": f"setpoint.{r['parameter']}",
                    "message": (
                        f"Setpoint unconfirmed >5 min: {r['parameter']}={float(r['value']):.3f} "
                        f"pushed at {r['ts']:%H:%M:%S} UTC, last cfg readback "
                        f"{last_cfg if last_cfg is not None else '(none)'}"
                    ),
                    "details": {
                        "parameter": r["parameter"],
                        "requested_value": float(r["value"]),
                        "last_cfg_readback": last_cfg,
                        "age_s": age_s,
                        "pushed_at": r["ts"].isoformat(),
                    },
                }
            )
            await conn.execute(
                "INSERT INTO alert_log "
                "(alert_type, severity, category, sensor_id, message, details, source) "
                "VALUES ('setpoint_unconfirmed', $1, 'system', $2, $3, $4::jsonb, 'ingestor')",
                alert.severity,
                alert.sensor_id,
                alert.message,
                json.dumps(alert.details),
            )

        log.info("Setpoint confirmation monitor: %d unconfirmed row(s)", len(rows))


# ═════════════════════════════════════════════════════════════════
# 17. MIDNIGHT WATCH (Sprint 24.7 — ops stopgap until contract v1.4 SLA rule)
#
# At 00:05 MDT each day, check whether tonight's MIDNIGHT opus trigger
# fired and produced a plan. Posts ONE Slack message per night with the
# outcome so operators know without scraping journalctl. Retires once
# Sprint 25's alert_monitor rule 7 rewrite (per-pair SLA over
# plan_delivery_log) lands — that's the structural fix; this is the
# visibility gap-closer until then.
#
# Matches both future-canonical (event_type='MIDNIGHT' after Sprint 25
# splits it out) and today's form (event_type='TRANSITION' with
# event_label containing "Midnight").
# ═════════════════════════════════════════════════════════════════

_midnight_watch_last_date: str = ""


async def midnight_watch(pool: asyncpg.Pool) -> None:
    """Daily 00:05 MDT check that the midnight opus trigger ran.

    Three Slack outcomes (per iris-dev's ops-stopgap spec):
      - resulting_plan_id populated  → ✅ "Iris wrote plan X"
      - row exists, plan NULL        → ⚠️ "delivered but no plan yet" (+ 2h-cover note)
      - no row in the 30-min window  → 🔴 "trigger was not delivered" (escalation)
    """
    global _midnight_watch_last_date
    now_mt = datetime.now(ZoneInfo("America/Denver"))

    # Fire only in the 00:05-00:10 MDT window; dedupe by date so a ~60s
    # task_loop that sees the window 5 times only posts once.
    if now_mt.hour != 0 or not (5 <= now_mt.minute < 10):
        return
    today_str = str(now_mt.date())
    if _midnight_watch_last_date == today_str:
        return

    async with pool.acquire() as conn:
        # Match both v1.4 MIDNIGHT event_type and today's TRANSITION:midnight_posture label.
        row = await conn.fetchrow(
            """
            SELECT event_type, event_label, delivered_at, resulting_plan_id
              FROM plan_delivery_log
             WHERE (event_type = 'MIDNIGHT'
                    OR (event_type = 'TRANSITION' AND event_label ILIKE '%midnight%'))
               AND delivered_at > now() - interval '30 minutes'
             ORDER BY delivered_at DESC
             LIMIT 1
            """,
        )

        if row is None:
            msg = "\U0001f534 *Midnight watch:* trigger was not delivered in the last 30 min (escalation)"
        elif row["resulting_plan_id"]:
            msg = (
                f"\u2705 *Midnight watch:* Iris wrote plan `{row['resulting_plan_id']}` "
                f"(trigger `{row['event_type']}/{row['event_label'] or ''}` at {row['delivered_at']:%H:%M UTC})"
            )
        else:
            # Delivered but no plan — note if an earlier plan within 2h covers the window.
            recent_plan = await conn.fetchval(
                "SELECT plan_id FROM plan_journal WHERE created_at > now() - interval '2 hours' "
                "ORDER BY created_at DESC LIMIT 1"
            )
            covers = f" (prior plan `{recent_plan}` within 2h may cover)" if recent_plan else ""
            msg = (
                f"\U0001f7e1 *Midnight watch:* trigger delivered at {row['delivered_at']:%H:%M UTC} "
                f"but no plan yet{covers}"
            )

    _midnight_watch_last_date = today_str
    try:
        token = _load_token(SLACK_TOKEN_FILE)
        _post_slack(token, SLACK_CHANNEL, msg)
        log.info("midnight_watch: %s", msg)
    except Exception as e:
        log.error("midnight_watch Slack post failed: %s", e)
