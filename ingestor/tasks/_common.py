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
from bisect import bisect_right
from dataclasses import dataclass
from datetime import UTC, datetime
from datetime import timedelta as _td
from pathlib import Path
from zoneinfo import ZoneInfo

# Issue #46: this module moved from ingestor/tasks.py to ingestor/tasks/_common.py
# (one directory deeper), so REPO_ROOT now needs three .parent hops instead of
# two to resolve to the same repository root the monolith computed.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent


if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


import asyncpg
import shared
from entity_map import FEEDBACK_VALUE_RANGES, normalize_feedback_value
from esp32_push import push_to_esp32
from occupancy import expire_occupancy_latch, sync_occupancy_state
from pydantic import ValidationError

from slack_config import build_slack_payload
from slack_ops.briefs import build_operator_brief
from slack_ops.policy import should_post_alert
from slack_ops.runbooks import fetch_alert_runbook, format_runbook
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
    SLACK_SETTINGS,
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


# Per-circuit lux ON/OFF thresholds. These are firmware-persisted (restore_value)
# but revert to cold defaults on an ESP32 reboot/OTA. They must be FORCE-
# reconciled on reconnect (not seeded from the device's possibly-reverted cfg),
# so a reboot cannot strand the grow lights on a stale threshold and cut them at
# dawn (the 2026-06-16 incident). Including them in BAND_DRIVEN_PARAMS makes the
# reconnect reconcile re-push the dispatcher-resolved (AI-tunable) value
# immediately instead of waiting for the next drift-detection cycle.
LIGHTING_CIRCUIT_LUX_PARAMS = frozenset(
    name
    for name in LIGHTING_CIRCUIT_DEFAULT_PARAMS
    if name.endswith("_lux_threshold") or name.endswith("_lux_hysteresis")
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


BAND_DRIVEN_PARAMS = CROP_BAND_REG | LIGHTING_POLICY_PARAMS | LIGHTING_CIRCUIT_LUX_PARAMS


SAFETY_RAIL_PARAMS = frozenset(name for name, spec in REGISTRY.items() if spec.push_owner == "safety")


HOUSE_VPD_MIN_WIDTH_KPA = 0.55


HOUSE_VPD_LOW_MARGIN_KPA = 0.20


AIR_EXCHANGE_RELAY_STUCK_MODES = frozenset({"VENTILATE", "DEHUM_VENT", "THERMAL_RELIEF", "SAFETY_COOL"})


# M5 / B10: alert auto-close disposition.
#
# When a previously-OPEN alert stops being produced, the auto-resolver marks it
# disposition='resolved' (the condition recovered). But some alerts represent a
# KNOWN, ACCEPTED, software-unactionable gap — chiefly the irrigation/soil-probe
# feedback alerts for hardware that is not installed (center EC/pH/moisture
# probes, NB1-3, hardware-gated) or for an unpotted/empty zone. When the
# operator ACKNOWLEDGES such an alert, closing it later as 'resolved' is a lie
# (nothing recovered — the probe still isn't there) and re-emitting it floods
# the open backlog (the ~92% orphan population in B10). For these we reach
# disposition='suppressed' instead: it leaves the canonical open set (v_open_alerts,
# db-group) without falsely claiming recovery, and it carries the operator's prior
# acknowledgement forward. Genuine transient recoveries still resolve normally.
SUPPRESSIBLE_ALERT_TYPES = frozenset(
    {
        "irrigation_feedback_gap",  # missing/stuck center+south probe feedback (HW-gated)
        "soil_sensor_offline",  # unpotted/empty-zone probe (occupancy-aware; S1)
    }
)


# G6 / B19 §7.4: VPD-high stress alert threshold (hours/day).
#
# The legacy 2.0h threshold was calibrated against the broken 78F served band.
# Once migration 145 raises the orchid band AND 146 re-points
# v_stress_hours_today.vpd_stress_hours to a graded-DEFICIT integral
# (severity-weighted, always <= the old binary count), 2.0h is structurally
# unreachable -> a dead alert. The design recalibrates to
#   max(VPD_STRESS_FLOOR_H, p75 of rolling-30d graded_stress_hours_vpd_high[center]).
# That graded history only exists after 146 dual-writes it, so the threshold is
# computed dynamically and self-recalibrates the moment the column is populated;
# until then it falls back to the legacy binary threshold so the alert keeps
# working byte-identically during co-existence.
VPD_STRESS_FLOOR_H = 0.5


VPD_STRESS_LEGACY_THRESHOLD_H = 2.0


# ── Soil dryout (issue #40) ──────────────────────────────────────────────
#
# Audit §7-#1 / P0: a root-zone probe crashed for 11 days and NO alert fired,
# because alert_monitor only had soil_sensor_offline (no-data) and
# irrigation_feedback_gap (stuck/missing) — there was no value-below-wilt rule.
# The surviving soil_moisture_west probe is alive (~40.6%) so a below-wilt rule
# is now exercisable.
#
# This rule is READ-SIDE PAGING ONLY. It never actuates irrigation and never
# writes the device. When a LIVE probe (continuous samples, non-stuck) reads
# continuously below its zone wilt threshold for > SOIL_DRYOUT_MIN_DURATION_H
# hours it pages a critical alert. It is occupancy-aware: suppressed entirely
# for unpotted/empty zones (mirrors soil_sensor_offline). A stuck-zero or
# missing probe is NOT a dryout — that is owned by irrigation_feedback_gap /
# soil_sensor_offline — so a fired dryout requires every in-window sample to be
# strictly positive (min_pct > 0) and below wilt.
SOIL_DRYOUT_MIN_DURATION_H = 2.0


# A 5 s ESP32 loop yields ~720 soil samples/h. Require well over an hour of
# coverage so a brief reporting gap or a single spurious read can't satisfy the
# >2h-continuous test. ~60 samples ≈ 5 min of data even at a heavily decimated
# 1/min cadence; the duration_h gate (oldest in-window sample ≥ 2h old) is the
# real "continuously" guard, this just rejects too-sparse windows.
SOIL_DRYOUT_MIN_SAMPLES = 60


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


# ── Graded + feasibility-aware compliance (migration 146 dual-write) ──────
# Python mirror of the DB compliance engine (band-compliance-architecture §6).
# These accumulate the new daily_summary *_v2 columns + daily_zone_compliance
# rows ALONGSIDE the untouched binary calc during the co-existence window.
# The DB functions (fn_grade_credit / fn_zone_band_grade / fn_compliance_v2)
# are the canonical source once migration 146 lands; this loop is the writer
# the design (§6.6) specifies so no extra full-climate scan is incurred.
#
# Graded zones: center (orchid proxy via temp_avg/vpd_avg), east (food crops,
# intersection-ideal / union-stress band), north (_default house-comfort band).
# House roll-up uses priority weights (center 0.60 / east 0.40); empty zones
# (north/south/west) carry weight 0 and are excluded from the house reward
# (§6.3). Weights mirror the compliance_zone_weights seed; if the table exists
# at write-time the DB seed is authoritative and these are the fallback.
COMPLIANCE_ZONE_WEIGHTS_DEFAULT = {"center": 0.60, "east": 0.40, "north": 0.0}


# relay_truth (per-miss feasibility) only exists from this cutover; older rows
# are labelled feasibility_unknown rather than mislabelled (§6.2).
RELAY_TRUTH_START = datetime(2026, 5, 24, 22, 47, tzinfo=ZoneInfo("America/Denver"))


# _default house-comfort band for empty zones (§4.1): ideal 60–80F / stress
# 45–95F, vpd ideal 0.4–1.4 / stress 0.2–2.0. Used when crop_target_profiles
# has no _default row yet (pre-migration-146 fallback so the grader never NULLs).
DEFAULT_ZONE_BAND = {
    "temp_ideal_min": 60.0,
    "temp_ideal_max": 80.0,
    "temp_stress_low": 45.0,
    "temp_stress_high": 95.0,
    "vpd_ideal_min": 0.4,
    "vpd_ideal_max": 1.4,
    "vpd_stress_low": 0.2,
    "vpd_stress_high": 2.0,
}


# Relays whose per-timestamp state the feasibility classifier needs.
_GRADE_RELAYS = ("fan1", "fan2", "vent", "fog", "heat1", "heat2", "mister_center", "mister_south", "mister_west")


# Zone → profile crop_type resolution for the GRADING band (§4.1). center grades
# against the orchid profile (temp_avg/vpd_avg proxy); east is the food-crop
# intersection-ideal / union-stress band; north is the _default house band.
# south/west are empty (weight 0) so they are not graded into the house roll-up.
_GRADED_ZONES = ("center", "east", "north")


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
    payload = build_slack_payload(SLACK_SETTINGS, text, channel=channel, thread_ts=thread_ts)
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{SLACK_SETTINGS.api_base_url.rstrip('/')}/chat.postMessage",
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


# ═════════════════════════════════════════════════════════════════
# 2b. SITE_CONTENT RAG REFRESH (daily, every 86400s)
# ═════════════════════════════════════════════════════════════════
# The site_content table is Iris's semantic-retrieval snapshot of the public
# vault/docs corpus (chunked + embedded downstream by embed-corpora.py). Before
# this task there was no scheduled refresh, so site_content silently drifted
# stale (max(updated_at) lagged the vault by a week — issue #43). This task
# re-materializes the corpus into site_content on a daily cadence using the same
# walk helpers as scripts/populate-site-content.py (single source of truth for
# what counts as a public RAG page), then asserts max(updated_at) lands back
# inside the cadence freshness window. Read/embed side only — no device path.

# Daily cadence; the freshness window allows one full cadence plus a grace
# margin before the snapshot is considered stale. Kept as module constants so
# the registration interval and the freshness assertion can't drift apart.
SITE_CONTENT_REFRESH_INTERVAL_S = 86400  # 24h


SITE_CONTENT_FRESHNESS_GRACE_S = 6 * 3600  # 6h slack for run jitter / long walks


SITE_CONTENT_FRESHNESS_WINDOW_S = SITE_CONTENT_REFRESH_INTERVAL_S + SITE_CONTENT_FRESHNESS_GRACE_S


_POPULATE_SITE_CONTENT_SCRIPT = REPO_ROOT / "scripts" / "populate-site-content.py"


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


# Direct ESP32 pushes are heap-expensive only during current pressure. A
# since-boot low-water mark is diagnostic evidence, not an active throttle:
# partial config snapshots can strand the controller on contradictory setpoints.
HEAP_DEFER_FREE_KB = 30.0


HEAP_DEFER_LARGEST_BLOCK_KB = 18.0


HEAP_CRITICAL_RECOVERY_FREE_KB = 25.0


HEAP_CRITICAL_RECOVERY_LARGEST_BLOCK_KB = 20.0


HEAP_CRITICAL_RECOVERY_SAMPLES = 2


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
        # fog_stress_* removed (BC-11/ADR0003 §6.7): retired dead registry rows.
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


# FW-3 (Sprint 18): dispatcher guardrails are derived from the registry's
# fw_clamp_lo/fw_clamp_hi fields. This keeps the setpoint path from carrying a
# second hand-maintained bounds table that can drift from MCP, schema, and
# ESPHome number limits.
_PHYSICS_INVARIANTS: dict[str, tuple[float | None, float | None]] = {
    name: (spec.fw_clamp_lo, spec.fw_clamp_hi)
    for name, spec in REGISTRY.items()
    if spec.kind == "numeric" and (spec.fw_clamp_lo is not None or spec.fw_clamp_hi is not None)
}


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


# ═════════════════════════════════════════════════════════════════
# 11. FORECAST ACTION ENGINE (every 900s = 15 min)
# ═════════════════════════════════════════════════════════════════
import subprocess as _sp

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
}


_FORECAST_STALE_SENSOR_ID = "system.weather_forecast"


_FORECAST_STALE_THRESHOLD_S = 2 * 60 * 60


_FORECAST_DEVIATION_SIGMA_MULTIPLIER = 1.5


_FORECAST_DEVIATION_SIGMA_HISTORY_DAYS = 7


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
    # Weekly deep performance review (L4 #346 AC6). Fires once per week on the
    # review weekday (heartbeat._WEEKLY_REVIEW_WEEKDAY, default Monday) at a
    # quiet local hour, asking the planner for a strategy-level review of the
    # prior week's outcome scores / lessons / trends rather than a tactical
    # daily plan. It is materialized only on the review weekday (gated in
    # _compute_milestones, since the milestone cache is rebuilt per-day);
    # required_plan=False keeps it off the hard daily-cycle SLA paging while
    # still expecting a set_plan and recording it in the trigger ledger.
    "WEEKLY": PlannerTriggerSpec(
        event_type="WEEKLY",
        event_label="Weekly deep performance review and strategy adjustment",
        due_source="local.weekly_review",
        expected_action="set_plan",
        required_plan=False,
        normal_window_seconds=300,
        catchup_seconds=14400,
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
#
# #433 repaired the prior band-anchor exclusion.  The sensors did publish, but
# their ESPHome API object_ids were derived from display names with bullets and
# units (for example ``cfg___band_temp_low_sr___f_``); the registry incorrectly
# routed the C++ id form (``cfg_band_temp_low_sr``).  Exact generated wire IDs
# now make every firmware-v2 anchor individually readbackable and confirmable.
# ═════════════════════════════════════════════════════════════════
_READBACKABLE_PARAMS: list[str] = sorted(set(CFG_READBACK_MAP.values()))


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


# ═════════════════════════════════════════════════════════════════
# 18. SLACK OPERATOR BRIEFS
# ═════════════════════════════════════════════════════════════════

_slack_brief_last_fire: dict[str, str] = {}


# Issue #46: this module is the shared base layer of the tasks package. Every
# name below is imported or defined here for re-use by the domain submodules
# (and re-exported by tasks/__init__.py). Declaring __all__ documents that
# public surface and tells the linter these names are intentional re-exports.
__all__ = [
    "asyncio",
    "concurrent",
    "json",
    "logging",
    "math",
    "os",
    "re",
    "sys",
    "REPO_ROOT",
    "time",
    "urllib",
    "uuid",
    "bisect_right",
    "dataclass",
    "UTC",
    "datetime",
    "_td",
    "Path",
    "ZoneInfo",
    "REPO_ROOT",
    "asyncpg",
    "shared",
    "FEEDBACK_VALUE_RANGES",
    "normalize_feedback_value",
    "push_to_esp32",
    "expire_occupancy_latch",
    "sync_occupancy_state",
    "ValidationError",
    "build_slack_payload",
    "build_operator_brief",
    "should_post_alert",
    "fetch_alert_runbook",
    "format_runbook",
    "AlertEnvelope",
    "ClimateRow",
    "EnergySample",
    "EquipmentStateEvent",
    "HAEntityState",
    "OpenMeteoForecastResponse",
    "PlanDeliveryLogRow",
    "SetpointChange",
    "CROP_BAND_REG",
    "LEGACY_SHARED_LIGHTING_REG",
    "LIGHTING_CIRCUIT_DEFAULT_REG",
    "PLANNER_PUSHABLE_REG",
    "REGISTRY",
    "registry_value_error",
    "get_tunable",
    "log",
    "EXPECTED_FIRMWARE_VERSION",
    "EXPECTED_FIRMWARE_VERSION_FILE",
    "GREENHOUSE_ID",
    "HA_TOKEN_FILE",
    "HA_URL",
    "SLACK_CHANNEL",
    "SLACK_SETTINGS",
    "SLACK_TOKEN_FILE",
    "STATE_DIR",
    "_load_token",
    "LIGHTING_POLICY_PARAMS",
    "LIGHTING_CIRCUIT_DEFAULT_PARAMS",
    "LIGHTING_CIRCUIT_SUPPORT_SENTINELS",
    "LIGHTING_TARGET_MINUTE_PARAMS",
    "IRRIGATION_SCHEDULE_PARAMS",
    "SECOND_READBACK_ABS_TOLERANCE_PARAMS",
    "HOUSE_BAND_PARAMS",
    "BAND_DRIVEN_PARAMS",
    "SAFETY_RAIL_PARAMS",
    "HOUSE_VPD_MIN_WIDTH_KPA",
    "HOUSE_VPD_LOW_MARGIN_KPA",
    "AIR_EXCHANGE_RELAY_STUCK_MODES",
    "SUPPRESSIBLE_ALERT_TYPES",
    "VPD_STRESS_FLOOR_H",
    "VPD_STRESS_LEGACY_THRESHOLD_H",
    "SOIL_DRYOUT_MIN_DURATION_H",
    "SOIL_DRYOUT_MIN_SAMPLES",
    "VPD_HIGH_GUARD_MARGIN_KPA",
    "VPD_MOISTURE_DEW_MARGIN_F",
    "VPD_MOISTURE_RECOVERY_WINDOW_MIN",
    "VPD_MOISTURE_RECOVERY_FRACTION",
    "VPD_DRY_AIR_OUTDOOR_RH_PCT",
    "VPD_VENT_FOG_ESCALATION_KPA",
    "VPD_HOT_DRY_FOG_ESCALATION_KPA",
    "VPD_VENT_MIN_FOG_OFF_S",
    "VPD_HOT_DRY_MIN_FOG_OFF_S",
    "VPD_HIGH_MOISTURE_GUARDRAIL_PARAMS",
    "COMPLIANCE_ZONE_WEIGHTS_DEFAULT",
    "RELAY_TRUTH_START",
    "DEFAULT_ZONE_BAND",
    "_GRADE_RELAYS",
    "_GRADED_ZONES",
    "GPU_POWER_EXPORTER_URL",
    "GPU_POWER_HOST",
    "INFRA_TELEMETRY_GREENHOUSE_ID",
    "GPU_POWER_EXPORTERS",
    "CPU_EXPORTERS",
    "PROM_SAMPLE_RE",
    "GPU_DCGM_METRICS",
    "_expected_firmware_version",
    "_ha_state",
    "_fetch_ha_entity",
    "_fetch_ha_batch",
    "_parse_prometheus_labels",
    "_fetch_url_text",
    "_iter_prometheus_samples",
    "_fetch_gpu_power_samples",
    "_fetch_all_gpu_power_samples",
    "_extract_node_snapshot",
    "_fetch_cpu_sample",
    "_fetch_all_cpu_samples",
    "_post_slack",
    "LEAK_TRIGGER_GPM",
    "LEAK_CLEAR_GPM",
    "LEAK_DWELL_TICKS",
    "_leak_counter",
    "_leak_state",
    "SITE_CONTENT_REFRESH_INTERVAL_S",
    "SITE_CONTENT_FRESHNESS_GRACE_S",
    "SITE_CONTENT_FRESHNESS_WINDOW_S",
    "_POPULATE_SITE_CONTENT_SCRIPT",
    "_SHELLY_PREFIX",
    "_SHELLY_ENTITIES",
    "_TEMPEST_MAP",
    "_HYDRO_MAP",
    "_CENTER_FEEDBACK_MAP",
    "_LIGHT_ENTITIES",
    "_HA_SWITCHES",
    "_OCCUPANCY_ENTITIES",
    "_ha_prev_state",
    "_HA_STATE_FILE",
    "CFG_READBACK_MAP",
    "EQUIPMENT_SWITCH_MAP",
    "PARAM_TO_ENTITY",
    "SWITCH_TO_ENTITY",
    "QUIET_MODE_ENTITY",
    "QUIET_REASON_ENTITY",
    "QUIET_UNTIL_ENTITY",
    "quiet_expired_needs_restore",
    "quiet_is_active",
    "_last_pushed",
    "SWITCH_CONFIRM_EQUIPMENT",
    "FIRMWARE_READBACK_PARAMS",
    "FIRMWARE_HAS_PER_CIRCUIT_LIGHTING",
    "FIRMWARE_HAS_LIGHTING_TARGET_MINUTES",
    "HEAP_DEFER_FREE_KB",
    "HEAP_DEFER_LARGEST_BLOCK_KB",
    "HEAP_CRITICAL_RECOVERY_FREE_KB",
    "HEAP_CRITICAL_RECOVERY_LARGEST_BLOCK_KB",
    "HEAP_CRITICAL_RECOVERY_SAMPLES",
    "FORCED_ON_SWITCH_PARAMS",
    "ACTIVITY_MIRROR_PARAMS",
    "DIRECT_WET_DEFAULTS",
    "DIRECT_WET_POLICY_PARAMS",
    "DIRECT_WET_REQUIRED_OBJECT_IDS",
    "AI_MOISTURE_STRESS_POLICY_PARAMS",
    "AI_MOISTURE_STRESS_REQUIRED_OBJECT_IDS",
    "QUIET_STATE_ENTITIES",
    "SAFETY_PARAMS",
    "MISTER_DEFAULTS",
    "_PHYSICS_INVARIANTS",
    "_DENVER",
    "_FORECAST_URL",
    "_GL_SQL",
    "DEFAULT_ELECTRIC_WATTAGES",
    "HEAT2_BTU",
    "THERM_BTU",
    "_sp",
    "_FORECAST_DEVIATION_DEFAULTS",
    "_FORECAST_STALE_SENSOR_ID",
    "_FORECAST_STALE_THRESHOLD_S",
    "_FORECAST_DEVIATION_SIGMA_MULTIPLIER",
    "_FORECAST_DEVIATION_SIGMA_HISTORY_DAYS",
    "LocationInfo",
    "_sun",
    "CONTEXT_GATHER_FAILED_SENTINEL",
    "gather_context",
    "prepare_delivery_result",
    "send_to_iris",
    "SeverityContext",
    "classify_severity",
    "pick_instance",
    "sla_for",
    "_LOCATION",
    "_milestones_cache",
    "_milestones_fired",
    "_milestones_date",
    "_last_forecast_fetch",
    "_MILESTONE_STATE_FILE",
    "PlannerTriggerSpec",
    "PLANNER_TRIGGER_MATRIX",
    "_READBACKABLE_PARAMS",
    "_midnight_watch_last_date",
    "_slack_brief_last_fire",
]
