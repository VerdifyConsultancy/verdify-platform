"""Shadow-mode DB write gate (#25) — safe parallel-run interlock.

The k3s migration needs a STAGING ingestor that can connect to the live data
sources (ESP32 read stream, HA, MQTT, Tempest) and exercise the full ingest
path WITHOUT mutating the production database or driving the physical device.
That parallel run validates the staged build against real traffic before any
cutover.

Two independent interlocks compose here:

  * ``VERDIFY_DEVICE_WRITE_ENABLED`` (esp32_push.py, #79) — gates the *device*
    write chokepoints. Already default-deny.
  * ``VERDIFY_SHADOW_MODE`` (this module) — gates *database* writes. When on,
    every ``execute`` / ``executemany`` that mutates state becomes a logged
    no-op while reads (``fetch`` / ``fetchval`` / ``fetchrow`` and read-class
    ``execute``) pass through unchanged.

Shadow mode is the DB analogue of the device gate: a staged ingestor leaves the
env unset (default-deny is *off* for DB writes — i.e. writes happen normally in
prod) and the staging overlay sets ``VERDIFY_SHADOW_MODE=1`` to suppress them.

Both ``VERDIFY_SHADOW_MODE`` and ``DRY_RUN`` enable shadow mode (DRY_RUN is the
generic alias some tooling sets). The gate reads the env at call time, not
import time, so tests can toggle it.

Implementation: a custom asyncpg ``Connection`` subclass intercepts the write
methods. Every ``pool.acquire()`` in the ingestor yields one of these, so the
~30 call sites need no change — the chokepoint is the connection itself, which
mirrors how the device gate sits at the single push helper.
"""

from __future__ import annotations

import logging
import os
import re

import asyncpg

log = logging.getLogger("shadow_mode")

# Statements whose leading keyword is read-only. A statement is a *write* (and
# therefore suppressed in shadow mode) unless its first keyword is in this set
# AND it is not a refresh-procedure call (see _is_write below).
_READ_LEADERS = frozenset(
    {
        "SELECT",
        "WITH",
        "SHOW",
        "EXPLAIN",
        "SET",
        "RESET",
        "LISTEN",
        "UNLISTEN",
        "BEGIN",
        "START",
        "COMMIT",
        "ROLLBACK",
        "SAVEPOINT",
        "RELEASE",
        "DECLARE",
        "FETCH",
        "CLOSE",
        "VALUES",
        "TABLE",
    }
)

# A bare SELECT that invokes one of these procedures mutates server-side state
# (TimescaleDB continuous-aggregate / view refreshes). Treat as a write so
# shadow mode does not silently refresh prod aggregates.
_REFRESH_PROC_RE = re.compile(r"\brefresh_\w+\s*\(", re.IGNORECASE)

_COMMENT_RE = re.compile(r"/\*.*?\*/|--[^\n]*", re.DOTALL)
_LEADING_WORD_RE = re.compile(r"^\s*([A-Za-z_]+)")

_SHADOW_ENABLED_LOGGED = False


def shadow_mode_enabled() -> bool:
    """True when DB writes must be suppressed.

    Enabled by ``VERDIFY_SHADOW_MODE=1`` or ``DRY_RUN=1`` (exact string '1',
    matching the device-gate convention so 'true'/'yes'/'2' do NOT enable it).
    """
    return os.environ.get("VERDIFY_SHADOW_MODE", "") == "1" or os.environ.get("DRY_RUN", "") == "1"


def _log_shadow_enabled_once() -> None:
    global _SHADOW_ENABLED_LOGGED
    if not _SHADOW_ENABLED_LOGGED:
        _SHADOW_ENABLED_LOGGED = True
        log.warning(
            "SHADOW_MODE active (VERDIFY_SHADOW_MODE/DRY_RUN == '1'); all DB "
            "write statements are no-ops, reads pass through"
        )


def _is_write(query: str) -> bool:
    """Classify a SQL statement as a write (True) or a read (False)."""
    stripped = _COMMENT_RE.sub(" ", query)
    m = _LEADING_WORD_RE.match(stripped)
    if not m:
        # Empty / unparseable — be conservative and treat as a write.
        return True
    leader = m.group(1).upper()
    if leader not in _READ_LEADERS:
        return True
    # Read-class leader, but a SELECT/WITH that calls a refresh_* proc mutates
    # state; suppress it too.
    if _REFRESH_PROC_RE.search(stripped):
        return True
    return False


class ShadowConnection(asyncpg.Connection):
    """asyncpg Connection that suppresses write statements in shadow mode.

    Reads (``fetch`` / ``fetchval`` / ``fetchrow``) are never touched.
    ``executemany`` is always a write batch and is suppressed wholesale.
    ``execute`` is classified by leading SQL keyword.

    Return values mimic asyncpg so callers that inspect the status string do not
    crash: ``execute`` returns the leading verb as a faux command tag; this is
    only reached when shadow mode is on (otherwise the real path runs).
    """

    async def execute(self, query: str, *args, **kwargs):  # type: ignore[override]
        if shadow_mode_enabled() and _is_write(query):
            _log_shadow_enabled_once()
            log.debug("SHADOW_MODE: suppressed execute: %s", _preview(query))
            return "SHADOW"
        return await super().execute(query, *args, **kwargs)

    async def executemany(self, command: str, args, **kwargs):  # type: ignore[override]
        if shadow_mode_enabled():
            _log_shadow_enabled_once()
            log.debug("SHADOW_MODE: suppressed executemany: %s", _preview(command))
            return None
        return await super().executemany(command, args, **kwargs)


def _preview(query: str, limit: int = 80) -> str:
    flat = " ".join(query.split())
    return flat[:limit] + ("…" if len(flat) > limit else "")


def connection_class() -> type[asyncpg.Connection]:
    """Connection class for ``create_pool`` / ``connect``.

    Returns ``ShadowConnection`` always — the class is a no-cost passthrough
    when shadow mode is off (the env check is per-call and cheap), so the same
    class is safe in prod. Centralizing here keeps the wiring in one place.
    """
    return ShadowConnection
