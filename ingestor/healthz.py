"""Ingestor health primitives (#25) — write-free liveness/readiness.

The ingestor is a connect-OUT worker with no inbound port (see Dockerfile), so
k8s health is an ``exec`` probe, not an ``httpGet``. This module is the single
source of truth for:

  * ``HEARTBEAT_FILE`` — the path the running ingestor touches every flush
    cycle (5 s). A *filesystem* touch, not a DB write, so it fires in
    SHADOW_MODE too — a shadow (non-writing) pod still proves liveness.
  * ``touch_heartbeat()`` — called from the flush loop.
  * ``check_health()`` — the probe body: heartbeat freshness (liveness) plus an
    optional read-only DB ping (readiness). Performs ZERO writes, so it is safe
    to run against a shadow pod or a prod pod identically.

``ingestor-healthz.py`` is the thin CLI wrapper a k8s probe invokes.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path


def _state_dir() -> Path:
    return Path(os.environ.get("STATE_DIR", "/srv/verdify/state"))


def heartbeat_path() -> Path:
    """Resolved heartbeat file path (env STATE_DIR-relative)."""
    return _state_dir() / "ingestor-heartbeat"


# Convenience constant for callers that just want the path once.
HEARTBEAT_FILE = heartbeat_path()

# A heartbeat older than this means the flush loop is wedged/dead. The flush
# loop touches every 5 s; 90 s tolerates GC pauses, slow DB cycles, and a probe
# that races a single missed cycle without flapping.
DEFAULT_STALE_AFTER_S = 90.0


def touch_heartbeat() -> None:
    """Update the heartbeat file mtime. Creates it (and STATE_DIR) if needed.

    Write-free w.r.t. the DB — this is the readiness signal that works even
    when SHADOW_MODE suppresses every database write.
    """
    path = heartbeat_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    if not path.exists():
        path.touch()
    os.utime(path, (now, now))


@dataclass
class HealthResult:
    ok: bool
    checks: dict[str, str]

    def summary(self) -> str:
        status = "OK" if self.ok else "UNHEALTHY"
        body = " ".join(f"{k}={v}" for k, v in self.checks.items())
        return f"{status} {body}".rstrip()


def check_heartbeat(stale_after_s: float = DEFAULT_STALE_AFTER_S) -> tuple[bool, str]:
    """Liveness: heartbeat file exists and is fresh. No writes."""
    path = heartbeat_path()
    if not path.exists():
        return False, "missing"
    age = time.time() - path.stat().st_mtime
    if age > stale_after_s:
        return False, f"stale({age:.0f}s>{stale_after_s:.0f}s)"
    return True, f"fresh({age:.0f}s)"


async def check_db(dsn: str | None = None, timeout_s: float = 5.0) -> tuple[bool, str]:
    """Readiness: a read-only ``SELECT 1`` against the DB. No writes.

    Imports asyncpg/config lazily so the heartbeat-only probe path has no heavy
    dependency. Returns (False, reason) on any failure rather than raising.
    """
    try:
        import asyncpg
    except Exception as e:  # pragma: no cover - import-time only
        return False, f"asyncpg-import-error:{e}"

    if dsn is None:
        try:
            from config import DB_DSN

            dsn = DB_DSN
        except Exception as e:
            return False, f"config-error:{e}"

    conn = None
    try:
        conn = await asyncpg.connect(dsn, timeout=timeout_s)
        val = await conn.fetchval("SELECT 1")
        return (val == 1), ("reachable" if val == 1 else "unexpected-result")
    except Exception as e:
        return False, f"unreachable:{type(e).__name__}"
    finally:
        if conn is not None:
            try:
                await conn.close()
            except Exception:
                pass


async def check_health(
    *,
    check_database: bool = True,
    stale_after_s: float = DEFAULT_STALE_AFTER_S,
    dsn: str | None = None,
) -> HealthResult:
    """Full probe: heartbeat (always) + optional read-only DB ping. No writes."""
    checks: dict[str, str] = {}

    hb_ok, hb_detail = check_heartbeat(stale_after_s=stale_after_s)
    checks["heartbeat"] = hb_detail
    ok = hb_ok

    if check_database:
        db_ok, db_detail = await check_db(dsn=dsn)
        checks["db"] = db_detail
        ok = ok and db_ok

    return HealthResult(ok=ok, checks=checks)
