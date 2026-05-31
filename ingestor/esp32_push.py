"""Shared direct ESP32 push helper.

Kept outside ingestor.py so tasks.py does not import the service entrypoint.
When systemd runs ``python ingestor.py``, importing ``ingestor`` creates a
second module instance with separate state; this module avoids that split.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

import shared
from entity_map import SETPOINT_MAP

log = logging.getLogger("esp32_push")

# ── Device-write safety gate (#79) ──────────────────────────────────────────
# Default-deny: the ingestor only writes to the physical ESP32 when
# VERDIFY_DEVICE_WRITE_ENABLED is exactly '1'. Staging (and any env that
# forgets to set it) is therefore a no-op against the device — the k3s staging
# overlay deliberately leaves this unset/0 so a staged ingestor can never drive
# the live greenhouse. Prod sets it to '1'. The check is import-safe (reads env
# at call time, not import time, so a test can toggle it) and cheap.
_DEVICE_WRITE_DISABLED_LOGGED = False


def _shadow_mode_enabled() -> bool:
    """True when SHADOW_MODE/DRY_RUN forces all writes off (#25).

    Read locally (not imported at module top) so the device gate stays
    self-contained and import-light, matching the existing pattern.
    """
    return os.environ.get("VERDIFY_SHADOW_MODE", "") == "1" or os.environ.get("DRY_RUN", "") == "1"


def _device_writes_enabled() -> bool:
    """True only when VERDIFY_DEVICE_WRITE_ENABLED == '1' (default-deny).

    SHADOW_MODE/DRY_RUN is a hard override: it forces device writes off even
    if VERDIFY_DEVICE_WRITE_ENABLED == '1', so a shadow parallel-run can never
    drive the physical greenhouse.
    """
    if _shadow_mode_enabled():
        return False
    return os.environ.get("VERDIFY_DEVICE_WRITE_ENABLED", "") == "1"


def _log_device_writes_disabled_once(where: str) -> None:
    """Emit a single WARNING the first time a device write is gated off."""
    global _DEVICE_WRITE_DISABLED_LOGGED
    if not _DEVICE_WRITE_DISABLED_LOGGED:
        _DEVICE_WRITE_DISABLED_LOGGED = True
        log.warning(
            "Device writes DISABLED (VERDIFY_DEVICE_WRITE_ENABLED != '1'); "
            "%s and all subsequent ESP32 writes are no-ops",
            where,
        )


# Large reconnect reconciliations can push dozens of values immediately after
# OTA, while ESPHome is also rebuilding API/MQTT state. Pace conservatively to
# avoid transient heap-pressure alerts on the ESP32.
_BATCH_PAUSE_EVERY = 2
_BATCH_PAUSE_S = 6.0
_MIN_COMMAND_INTERVAL_S = 2.0
_PUSH_LOCK = asyncio.Lock()
_LAST_COMMAND_TS = 0.0


async def _pace_command() -> None:
    global _LAST_COMMAND_TS
    now = time.monotonic()
    wait_s = _MIN_COMMAND_INTERVAL_S - (now - _LAST_COMMAND_TS)
    if wait_s > 0:
        await asyncio.sleep(wait_s)
    _LAST_COMMAND_TS = time.monotonic()


async def push_to_esp32(changes: list[tuple[str, float, str]]) -> int:
    """Push setpoint changes through the shared aioesphomeapi connection.

    Args:
        changes: ``[(esp32_object_id, value, "number" | "switch"), ...]``.

    Returns:
        Count of successfully pushed changes. Returns 0 when disconnected.
    """
    # Device-write gate (#79): default-deny. No-op without a physical write when
    # VERDIFY_DEVICE_WRITE_ENABLED != '1'.
    if not _device_writes_enabled():
        _log_device_writes_disabled_once("push_to_esp32")
        return 0

    client = shared.esp32["client"]
    keys = shared.esp32["keys"]
    if client is None:
        return 0

    pushed = 0
    async with _PUSH_LOCK:
        for idx, (obj_id, val, etype) in enumerate(changes, start=1):
            key = keys.get(obj_id)
            if not key:
                log.warning("push_to_esp32: no key for '%s' (%d keys)", obj_id, len(keys))
                continue
            try:
                await _pace_command()
                if etype == "number":
                    result = client.number_command(key, val)
                    if asyncio.iscoroutine(result):
                        await result
                elif etype == "switch":
                    result = client.switch_command(key, val > 0.5)
                    if asyncio.iscoroutine(result):
                        await result
                else:
                    log.warning("push_to_esp32: unsupported entity type '%s' for %s", etype, obj_id)
                    continue

                pushed += 1
                db_param = SETPOINT_MAP.get(obj_id)
                if db_param:
                    shared.recently_pushed[db_param] = time.time()
                    shared.recently_pushed_values[db_param] = float(val)
                if len(changes) > 1 and idx < len(changes) and pushed % _BATCH_PAUSE_EVERY == 0:
                    await asyncio.sleep(_BATCH_PAUSE_S)
            except Exception as e:
                log.warning("ESP32 push failed for %s: %s", obj_id, e)
                break

    return pushed


async def push_occupancy_to_esp32(occupied: bool, source: str) -> int:
    """Push greenhouse occupancy state through the native ESPHome API."""
    # Device-write gate (#79): default-deny. Guarded here too (defense in depth)
    # even though push_to_esp32 below is the true chokepoint.
    if not _device_writes_enabled():
        _log_device_writes_disabled_once("push_occupancy_to_esp32")
        return 0

    label = "occupied" if occupied else "empty"
    if "greenhouse_occupied" not in shared.esp32["keys"]:
        log.debug("Occupancy ESP32 push skipped via %s: greenhouse_occupied API switch unavailable", source)
        return 0
    try:
        pushed = await push_to_esp32([("greenhouse_occupied", 1.0 if occupied else 0.0, "switch")])
        if pushed:
            log.info("Occupancy: pushed %s to ESP32 via %s", label, source)
        return pushed
    except Exception as e:
        log.debug("Occupancy ESP32 push skipped via %s: %s", source, e)
        return 0
