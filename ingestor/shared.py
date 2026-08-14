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
esp32 = {"client": None, "keys": {}, "services": {}}

# param -> monotonic timestamp/value of the last direct ESP32 push.
# Shared between ingestor.py callbacks and tasks.py dispatcher so echo
# suppression works even when the service is launched as __main__.
recently_pushed: dict[str, float] = {}
recently_pushed_values: dict[str, float] = {}

# param -> latest cfg_* readback from ESP32. Used by reconnect dispatch to
# reconcile desired setpoints against device state instead of force-pushing
# values the firmware has already confirmed.
cfg_readback: dict[str, float] = {}

# Lane C (#584) / contract v2 (#586): latest device-echoed policy identity,
# keyed by verdify_schemas.policy_transport.POLICY_IDENTITY_SENSOR — the ONE
# aggregated text sensor payload ("schema|generation|assignment|activation|
# apply_state"). Populated by the esp32 ingest loop; empty until the firmware
# echoes, which the delivery worker treats as "no echo yet".
policy_readback: dict[str, str] = {}

# Monotonic transport generation.  Only a successful API connect may advance
# this counter; cfg readback drift is tracked separately below.  The dispatcher
# records the last generation it reconciled so a stable socket can never be
# mistaken for a reconnect merely because a dynamic cfg value changed.
transport_generation = 0
reconciled_transport_generation = 0

# cfg parameter -> monotonic drift version.  Versioning lets a dispatcher clear
# only the drift it actually observed; a newer readback arriving mid-dispatch
# remains pending for the next targeted pass.
cfg_drift_versions: dict[str, int] = {}
_cfg_drift_version = 0

# Wake the scheduler for targeted cfg drift without advancing transport state.
setpoint_dispatch_requested = asyncio.Event()

# Compatibility reconnect event.  This event is now set ONLY by
# note_transport_connected(); generic cfg drift must never set it.
force_setpoint_push = asyncio.Event()


def note_transport_connected() -> int:
    """Advance and return the one monotonic generation for a real API connect."""
    global transport_generation
    transport_generation += 1
    force_setpoint_push.set()
    setpoint_dispatch_requested.set()
    return transport_generation


def mark_transport_reconciled(generation: int) -> None:
    """Mark one completed generation without erasing a newer reconnect."""
    global reconciled_transport_generation
    reconciled_transport_generation = max(reconciled_transport_generation, generation)
    if transport_generation <= reconciled_transport_generation:
        force_setpoint_push.clear()


def note_cfg_drift(param: str) -> int:
    """Record targeted readback drift without changing transport generation."""
    global _cfg_drift_version
    _cfg_drift_version += 1
    cfg_drift_versions[param] = _cfg_drift_version
    setpoint_dispatch_requested.set()
    return _cfg_drift_version


def clear_cfg_drift(observed_versions: dict[str, int]) -> None:
    """Clear only drift versions consumed by a completed dispatcher pass."""
    for param, version in observed_versions.items():
        if cfg_drift_versions.get(param) == version:
            cfg_drift_versions.pop(param, None)
    # A newer transport generation can arrive while the prior dispatcher owns
    # the lock.  Never erase that reconnect's scheduler wake when clearing an
    # older cfg-drift snapshot.
    if not cfg_drift_versions and transport_generation <= reconciled_transport_generation:
        setpoint_dispatch_requested.clear()


def defer_failed_dispatch(generation: int, observed_versions: dict[str, int]) -> None:
    """Consume only the failed pass's immediate wake, without claiming success.

    The unreconciled transport generation and drift versions remain intact for
    the normal 300-second cadence. A newer reconnect or drift arriving during
    the failed pass keeps the immediate scheduler wake set.
    """
    newer_drift_pending = cfg_drift_versions != observed_versions
    if transport_generation == generation and not newer_drift_pending:
        setpoint_dispatch_requested.clear()


# True while ingestor.py is running setpoint_dispatcher from any entrypoint.
# Prevents reconnect dispatch and the periodic task loop from queueing
# duplicate heap-sensitive ESPHome pushes.
setpoint_dispatch_in_progress = False
setpoint_dispatch_lock = asyncio.Lock()

# A terminal lifecycle-persistence failure means a physical outcome may no
# longer match its durable row.  The writer fail-closes and this event makes the
# owning ingestor process exit so Kubernetes can restart and reconcile unknown
# in-memory states instead of leaving a permanently false-green pod.
writer_fatal_event = asyncio.Event()
writer_fatal_reason: str | None = None


def note_writer_fatal(reason: str) -> None:
    global writer_fatal_reason
    writer_fatal_reason = reason
    writer_fatal_event.set()


# Timestamp of last ESP32 connect (used for boot-window gating)
esp32_connected_at: float = 0.0

# ── HA-3.2 single-writer fence (#240) ────────────────────────────────────────
# The active WriterLease holder, set by esp32_loop in main() once the lease is
# constructed. esp32_push.push_to_esp32 consults writer_lease_held() BEFORE every
# device command so a fenced/lease-lost pod can NEVER push — even if a stale
# shared.esp32["client"] reference lingers. None until set; when None the gate is
# OPEN (fence inert / pre-arm), preserving exact pre-fence behaviour.
writer_lease = None


def writer_lease_held() -> bool:
    """True iff it is safe to push to the device right now.

    Returns True when no lease is configured (fence disabled/pre-arm) so the
    push path is unchanged until the gated arm; otherwise defers to the
    renew-or-die WriterLease.is_held().
    """
    lease = writer_lease
    if lease is None:
        return True
    try:
        return bool(lease.is_held())
    except Exception:
        # A bug in the fence must FAIL SAFE: if we cannot determine lease state,
        # do NOT push (better a bounded zero-writer gap than a split-brain push).
        return False
