"""Standard-library runtime-health contract for the singleton ingestor.

The owning asyncio event loop publishes an allowlisted status document on the
container's writable ``/tmp`` volume. Kubelet liveness reads only this file;
it never depends on PostgreSQL, telemetry freshness, the ESP32, or the Lease.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
DEFAULT_STATUS_PATH = Path(
    os.environ.get(
        "INGESTOR_RUNTIME_STATUS_PATH",
        str(Path(tempfile.gettempdir()) / "verdify-ingestor-runtime.json"),
    )
)
DEFAULT_HEARTBEAT_INTERVAL_S = 5.0
DEFAULT_HEARTBEAT_MAX_AGE_S = 60.0
STATUS_KEYS = frozenset(
    {
        "schema_version",
        "heartbeat_monotonic",
        "mode",
        "lease_enabled",
        "lease_fencing_active",
        "lease_initialized",
        "lease_held",
        "esp32_connected",
        "writer_fatal",
    }
)


def runtime_status(
    state: dict[str, Any],
    *,
    now_monotonic: float | None = None,
) -> dict[str, Any]:
    """Build the exact non-secret status schema written for kubelet."""
    return {
        "schema_version": SCHEMA_VERSION,
        "heartbeat_monotonic": time.monotonic() if now_monotonic is None else now_monotonic,
        "mode": state.get("mode"),
        "lease_enabled": state.get("lease_enabled") is True,
        "lease_fencing_active": state.get("lease_fencing_active") is True,
        "lease_initialized": state.get("lease_initialized") is True,
        "lease_held": state.get("lease_held") is True,
        "esp32_connected": state.get("esp32_connected") is True,
        "writer_fatal": state.get("writer_fatal") is True,
    }


def write_runtime_status(path: Path, status: dict[str, Any]) -> None:
    """Atomically publish one allowlisted status document."""
    if set(status) != STATUS_KEYS:
        raise ValueError("runtime status does not match the allowlisted schema")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(status, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def read_runtime_status(path: Path = DEFAULT_STATUS_PATH) -> dict[str, Any] | None:
    """Read a complete schema-valid status document, or ``None``."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if not isinstance(payload, dict) or set(payload) != STATUS_KEYS:
        return None
    if type(payload.get("schema_version")) is not int or payload["schema_version"] != SCHEMA_VERSION:
        return None
    heartbeat = payload.get("heartbeat_monotonic")
    if not isinstance(heartbeat, (int, float)) or isinstance(heartbeat, bool) or not math.isfinite(heartbeat):
        return None
    if payload.get("mode") not in {"capture", "subscribe"}:
        return None
    boolean_keys = STATUS_KEYS - {"schema_version", "heartbeat_monotonic", "mode"}
    if any(type(payload.get(key)) is not bool for key in boolean_keys):
        return None
    return payload


def evaluate_liveness(
    status: dict[str, Any] | None,
    *,
    now_monotonic: float | None = None,
    max_age_seconds: float = DEFAULT_HEARTBEAT_MAX_AGE_S,
) -> tuple[bool, str, float | None]:
    """Evaluate only event-loop progress; return healthy, reason, and age."""
    if status is None:
        return False, "runtime_status_missing_or_invalid", None
    if (
        isinstance(max_age_seconds, bool)
        or not isinstance(max_age_seconds, (int, float))
        or not math.isfinite(float(max_age_seconds))
        or max_age_seconds <= 0
    ):
        return False, "heartbeat_threshold_invalid", None
    now = time.monotonic() if now_monotonic is None else now_monotonic
    if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(float(now)):
        return False, "heartbeat_clock_invalid", None
    age = now - float(status["heartbeat_monotonic"])
    if age < 0:
        return False, "heartbeat_from_future", age
    if age > max_age_seconds:
        return False, "heartbeat_stale", age
    return True, "event_loop_progressing", age


def evaluate_writer_readiness(status: dict[str, Any] | None) -> tuple[bool, str]:
    """Evaluate in-process singleton writer state without changing it."""
    if status is None:
        return False, "runtime_status_missing_or_invalid"
    if status["mode"] != "capture":
        return False, "not_capture_mode"
    if status["writer_fatal"]:
        return False, "writer_fatal"
    if not status["lease_initialized"]:
        return False, "writer_lease_uninitialized"
    if not status["lease_enabled"]:
        return False, "writer_lease_disabled"
    if not status["lease_fencing_active"]:
        return False, "writer_lease_fencing_degraded"
    if not status["lease_held"]:
        return False, "writer_lease_not_held"
    if not status["esp32_connected"]:
        return False, "esp32_disconnected"
    return True, "writer_connected_and_fenced"


async def runtime_status_loop(
    state_provider: Callable[[], dict[str, Any]],
    path: Path = DEFAULT_STATUS_PATH,
    interval_seconds: float = DEFAULT_HEARTBEAT_INTERVAL_S,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Publish status from the ingestor event loop until cancelled."""
    while True:
        write_runtime_status(path, runtime_status(state_provider(), now_monotonic=clock()))
        await asyncio.sleep(interval_seconds)
