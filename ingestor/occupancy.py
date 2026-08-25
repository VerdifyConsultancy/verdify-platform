"""Occupancy bridge actions for Frigate/Sentinel greenhouse presence."""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta

import asyncpg
from esp32_push import push_occupancy_to_esp32
from quiet_mode import iso_utc, parse_iso_utc

from verdify_schemas import SystemStateRow

log = logging.getLogger("occupancy")

OCCUPANCY_ENTITY = "occupancy"
OCCUPANCY_LAST_DETECTED_ENTITY = "occupancy_last_detected"
OCCUPANCY_UNTIL_ENTITY = "occupancy_until"
OCCUPANCY_SOURCE_ENTITY = "occupancy_source"
OCCUPANCY_TIMEOUT_REASON_ENTITY = "occupancy_timeout_reason"
OCCUPANCY_LATCH_MIN = int(os.environ.get("OCCUPANCY_LATCH_MIN", "15"))
OCCUPANCY_STATE_ENTITIES = (
    OCCUPANCY_ENTITY,
    OCCUPANCY_LAST_DETECTED_ENTITY,
    OCCUPANCY_UNTIL_ENTITY,
    OCCUPANCY_SOURCE_ENTITY,
)


async def _fetch_system_state(conn: asyncpg.Connection, entities: tuple[str, ...]) -> dict[str, str]:
    rows = await conn.fetch(
        """
        SELECT DISTINCT ON (entity) entity, value
        FROM system_state
        WHERE entity = ANY($1::text[])
        ORDER BY entity, ts DESC
        """,
        list(entities),
    )
    return {row["entity"]: row["value"] for row in rows}


async def _insert_system_state(conn: asyncpg.Connection, entity: str, value: str) -> None:
    SystemStateRow(ts=datetime.now(UTC), entity=entity, value=value)
    await conn.execute(
        "INSERT INTO v_runtime_system_state_write (ts, entity, value) VALUES (now(), $1, $2)",
        entity,
        value,
    )


def _as_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _effective_occupancy_from_state(state: dict[str, str], now: datetime | None = None) -> bool:
    if state.get(OCCUPANCY_ENTITY) != "occupied":
        return False
    until = parse_iso_utc(state.get(OCCUPANCY_UNTIL_ENTITY))
    return until is not None and until > (now or datetime.now(UTC))


async def _record_occupancy_observation(
    conn: asyncpg.Connection,
    occupied: bool,
    source: str,
    observed_at: datetime | None = None,
) -> tuple[bool, datetime]:
    """Persist a raw occupancy observation and return the effective latched state."""
    now = datetime.now(UTC)
    await _insert_system_state(conn, OCCUPANCY_SOURCE_ENTITY, source)

    if not occupied:
        await _insert_system_state(conn, OCCUPANCY_UNTIL_ENTITY, iso_utc(now))
        await _insert_system_state(conn, OCCUPANCY_ENTITY, "empty")
        await _insert_system_state(conn, OCCUPANCY_TIMEOUT_REASON_ENTITY, f"released:{source}")
        return False, now

    detected_at = _as_utc(observed_at)
    expires_at = detected_at + timedelta(minutes=OCCUPANCY_LATCH_MIN)
    await _insert_system_state(conn, OCCUPANCY_LAST_DETECTED_ENTITY, iso_utc(detected_at))
    await _insert_system_state(conn, OCCUPANCY_UNTIL_ENTITY, iso_utc(expires_at))

    if expires_at <= now:
        await _insert_system_state(conn, OCCUPANCY_ENTITY, "empty")
        await _insert_system_state(conn, OCCUPANCY_TIMEOUT_REASON_ENTITY, f"stale_detection:{source}")
        return False, expires_at

    await _insert_system_state(conn, OCCUPANCY_ENTITY, "occupied")
    return True, expires_at


async def sync_occupancy_state(
    pool: asyncpg.Pool,
    occupied: bool,
    source: str,
    observed_at: datetime | None = None,
) -> None:
    """Record occupancy, latch fresh detections, and push the effective state to ESP32."""
    async with pool.acquire() as conn:
        effective, until = await _record_occupancy_observation(conn, occupied, source, observed_at)

    if occupied and not effective:
        log.warning("Occupancy: stale occupied observation via %s expired at %s; pushing empty", source, iso_utc(until))
    elif effective:
        log.info("Occupancy: occupied via %s; latched until %s", source, iso_utc(until))
    else:
        log.info("Occupancy: empty via %s", source)

    await push_occupancy_to_esp32(effective, source)


async def expire_occupancy_latch(pool: asyncpg.Pool, source: str = "occupancy_watchdog") -> bool:
    """Fail safe to empty if no fresh occupied detection extends the latch."""
    now = datetime.now(UTC)
    expired = False
    async with pool.acquire() as conn:
        state = await _fetch_system_state(conn, OCCUPANCY_STATE_ENTITIES)
        until = parse_iso_utc(state.get(OCCUPANCY_UNTIL_ENTITY))
        if state.get(OCCUPANCY_ENTITY) == "occupied" and (until is None or until <= now):
            await _insert_system_state(conn, OCCUPANCY_ENTITY, "empty")
            await _insert_system_state(conn, OCCUPANCY_TIMEOUT_REASON_ENTITY, f"expired:{source}")
            expired = True

    if expired:
        log.warning("Occupancy: latch expired without fresh detection; pushing empty via %s", source)
        await push_occupancy_to_esp32(False, source)
    return expired


async def refresh_latest_occupancy_state(pool: asyncpg.Pool, source: str) -> None:
    """Replay the latest effective occupancy after ESP32 API reconnect."""
    if await expire_occupancy_latch(pool, source):
        return
    async with pool.acquire() as conn:
        state = await _fetch_system_state(conn, OCCUPANCY_STATE_ENTITIES)
    effective = _effective_occupancy_from_state(state)
    if effective:
        log.info("Occupancy: replaying occupied via %s; latch until %s", source, state.get(OCCUPANCY_UNTIL_ENTITY))
    elif state.get(OCCUPANCY_ENTITY) in {"occupied", "empty"}:
        log.info("Occupancy: replaying empty via %s", source)
    else:
        return

    await push_occupancy_to_esp32(effective, source)
