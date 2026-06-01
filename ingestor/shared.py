"""shared.py — Shared mutable state between ingestor and tasks."""

import asyncio
import logging

log = logging.getLogger("shadow")


def is_shadow_mode() -> bool:
    """True when VERDIFY_SHADOW_MODE is set — suppress ALL writes.

    Read through config so the flag has a single source of truth and tests can
    monkeypatch config.SHADOW_MODE without re-importing this module.
    """
    try:
        import config

        return bool(config.SHADOW_MODE)
    except Exception:
        return False


# SQL statements that mutate the DB. Shadow mode no-ops these while letting
# reads (SELECT / WITH ... SELECT, fetch*) pass straight through, so freshness
# probes and config loads still work during a parallel telemetry run.
_WRITE_PREFIXES = (
    "insert",
    "update",
    "delete",
    "merge",
    "copy",
    "truncate",
    "drop",
    "alter",
    "create",
)


def _is_write_sql(query: str) -> bool:
    """Classify a SQL string as a mutating statement.

    Skips leading whitespace and SQL line comments so that e.g. an indented
    ``INSERT`` or a ``WITH x AS (...) INSERT`` is still recognized as a write.
    """
    if not isinstance(query, str):
        return True  # be conservative: unknown shape -> treat as a write
    stripped = query.lstrip()
    while stripped.startswith("--"):
        nl = stripped.find("\n")
        if nl == -1:
            return False
        stripped = stripped[nl + 1 :].lstrip()
    lowered = stripped.lower()
    if lowered.startswith(_WRITE_PREFIXES):
        return True
    # CTEs may wrap a writing statement: WITH ... INSERT/UPDATE/DELETE ...
    if lowered.startswith("with "):
        return any(f" {kw} " in lowered or f"\n{kw} " in lowered for kw in ("insert", "update", "delete"))
    return False


class _ShadowConnection:
    """Wraps an asyncpg connection so write statements become no-ops.

    Reads (fetch/fetchval/fetchrow/cursor) and connection lifecycle delegate to
    the real connection unchanged. Only execute/executemany/copy* are gated.
    """

    def __init__(self, conn):
        self._conn = conn

    async def execute(self, query, *args, **kwargs):
        if _is_write_sql(query):
            log.debug("SHADOW_MODE: suppressed execute: %.60s", query.lstrip() if isinstance(query, str) else query)
            return "SHADOW"
        return await self._conn.execute(query, *args, **kwargs)

    async def executemany(self, query, args, **kwargs):
        if _is_write_sql(query):
            log.debug("SHADOW_MODE: suppressed executemany: %.60s", query.lstrip() if isinstance(query, str) else query)
            return None
        return await self._conn.executemany(query, args, **kwargs)

    async def copy_records_to_table(self, *args, **kwargs):
        log.debug("SHADOW_MODE: suppressed copy_records_to_table")
        return "SHADOW"

    async def copy_to_table(self, *args, **kwargs):
        log.debug("SHADOW_MODE: suppressed copy_to_table")
        return "SHADOW"

    def __getattr__(self, name):
        # Everything else (fetch, fetchval, fetchrow, cursor, transaction,
        # add_listener, prepare, set_type_codec, ...) passes through.
        return getattr(self._conn, name)


class _ShadowAcquireContext:
    """Async-context wrapper around pool.acquire() yielding a _ShadowConnection."""

    def __init__(self, acquire_ctx):
        self._ctx = acquire_ctx

    async def __aenter__(self):
        conn = await self._ctx.__aenter__()
        return _ShadowConnection(conn)

    async def __aexit__(self, *exc):
        return await self._ctx.__aexit__(*exc)


class _ShadowPool:
    """Wraps an asyncpg pool so every acquired connection is write-suppressed.

    Single DB chokepoint for SHADOW_MODE: all ingestor writes go through
    ``async with pool.acquire() as conn: await conn.execute(...)``.
    """

    def __init__(self, pool):
        self._pool = pool

    def acquire(self, *args, **kwargs):
        return _ShadowAcquireContext(self._pool.acquire(*args, **kwargs))

    def __getattr__(self, name):
        return getattr(self._pool, name)


def wrap_pool_for_shadow(pool):
    """Return a write-suppressing proxy when SHADOW_MODE is on, else the pool."""
    if is_shadow_mode():
        log.warning("SHADOW_MODE active: ALL ingestor DB writes are suppressed (telemetry consume/parse only)")
        return _ShadowPool(pool)
    return pool


# ESP32 client reference, set by esp32_loop in ingestor.py
# Used by dispatcher in tasks.py for direct setpoint push
esp32 = {"client": None, "keys": {}}

# param -> monotonic timestamp/value of the last direct ESP32 push.
# Shared between ingestor.py callbacks and tasks.py dispatcher so echo
# suppression works even when the service is launched as __main__.
recently_pushed: dict[str, float] = {}
recently_pushed_values: dict[str, float] = {}

# param -> latest cfg_* readback from ESP32. Used by reconnect dispatch to
# reconcile desired setpoints against device state instead of force-pushing
# values the firmware has already confirmed.
cfg_readback: dict[str, float] = {}

# Set by esp32_loop on reconnect — tells dispatcher to reconcile desired
# setpoints against cfg_readback and push only drift/missing values.
force_setpoint_push = asyncio.Event()

# True while ingestor.py is running setpoint_dispatcher from any entrypoint.
# Prevents reconnect dispatch and the periodic task loop from queueing
# duplicate heap-sensitive ESPHome pushes.
setpoint_dispatch_in_progress = False

# Timestamp of last ESP32 connect (used for boot-window gating)
esp32_connected_at: float = 0.0
