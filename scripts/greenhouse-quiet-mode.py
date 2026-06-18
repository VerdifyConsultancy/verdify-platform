#!/usr/bin/env python3
"""Operator recording quiet mode metadata for the greenhouse.

This command no longer writes target/band overlays. Routine quiet behavior is
handled by firmware occupancy inhibit; this helper keeps the timed recording
metadata and optional one-shot light-off request.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg

REPO_ROOT = Path(__file__).resolve().parents[1]
INGESTOR_DIR = REPO_ROOT / "ingestor"
for path in (REPO_ROOT, INGESTOR_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from quiet_mode import (  # noqa: E402
    QUIET_MODE_ENTITY,
    QUIET_REASON_ENTITY,
    QUIET_RESTORE_ENTITY,
    QUIET_UNTIL_ENTITY,
    build_restore_payload,
    iso_utc,
    parse_restore_payload,
    quiet_is_active,
)

SETPOINT_SERVER = "http://127.0.0.1:8200"
SYSTEM_STATE_ENTITIES = (
    QUIET_MODE_ENTITY,
    QUIET_UNTIL_ENTITY,
    QUIET_RESTORE_ENTITY,
    QUIET_REASON_ENTITY,
)


def get_db_url() -> str:
    if os.environ.get("DB_DSN"):
        return os.environ["DB_DSN"]
    if all(os.environ.get(key) for key in ("DB_USER", "DB_PASSWORD", "DB_HOST", "DB_PORT", "DB_NAME")):
        return (
            f"postgresql://{os.environ['DB_USER']}:{os.environ['DB_PASSWORD']}"
            f"@{os.environ['DB_HOST']}:{os.environ['DB_PORT']}/{os.environ['DB_NAME']}"
        )

    password = os.environ.get("POSTGRES_PASSWORD", "verdify")
    env_file = Path("/srv/verdify/.env")
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.strip().startswith("POSTGRES_PASSWORD="):
                password = line.strip().split("=", 1)[1].strip().strip('"').strip("'")
                break
    return f"postgresql://verdify:{password}@localhost:5432/verdify"


async def fetch_system_state(conn: asyncpg.Connection) -> dict[str, str]:
    rows = await conn.fetch(
        """
        SELECT DISTINCT ON (entity) entity, value
        FROM system_state
        WHERE entity = ANY($1::text[])
        ORDER BY entity, ts DESC
        """,
        list(SYSTEM_STATE_ENTITIES),
    )
    return {row["entity"]: row["value"] for row in rows}


async def insert_system_state(conn: asyncpg.Connection, entity: str, value: str) -> None:
    await conn.execute(
        "INSERT INTO system_state (ts, entity, value) VALUES (now(), $1, $2)",
        entity,
        value,
    )


def lights_off() -> None:
    for light in ("main", "grow"):
        url = f"{SETPOINT_SERVER}/lights/{light}/off"
        req = urllib.request.Request(url, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                if response.status != 200:
                    print(f"warning: {light} light off returned HTTP {response.status}")
        except (OSError, urllib.error.URLError) as exc:
            print(f"warning: could not turn {light} light off via setpoint server: {exc}")


async def enable(minutes: int, reason: str, lights_off_requested: bool) -> None:
    if minutes < 1:
        raise SystemExit("--minutes must be at least 1")
    if minutes > 240:
        raise SystemExit("--minutes must be 240 or less")

    until = datetime.now(UTC) + timedelta(minutes=minutes)
    async with asyncpg.create_pool(get_db_url(), min_size=1, max_size=2) as pool:
        async with pool.acquire() as conn:
            state = await fetch_system_state(conn)
            active = quiet_is_active(state.get(QUIET_MODE_ENTITY), state.get(QUIET_UNTIL_ENTITY))
            if not active:
                await insert_system_state(conn, QUIET_RESTORE_ENTITY, build_restore_payload({}))
            await insert_system_state(conn, QUIET_UNTIL_ENTITY, iso_utc(until))
            await insert_system_state(conn, QUIET_REASON_ENTITY, reason)
            await insert_system_state(conn, QUIET_MODE_ENTITY, "on")

    if lights_off_requested:
        lights_off()
    print(f"Recording quiet mode ON until {iso_utc(until)} ({minutes} min).")
    print("Wrote 0 manual quiet setpoints.")
    if lights_off_requested:
        print("Requested grow lights off.")
    else:
        print("Current light state was left unchanged.")
    print("Targets and bands were not changed.")


async def disable() -> None:
    async with asyncpg.create_pool(get_db_url(), min_size=1, max_size=2) as pool:
        async with pool.acquire() as conn:
            state = await fetch_system_state(conn)
            legacy_restore_count = len(parse_restore_payload(state.get(QUIET_RESTORE_ENTITY)))
            await insert_system_state(conn, QUIET_MODE_ENTITY, "off")
            await insert_system_state(conn, QUIET_UNTIL_ENTITY, iso_utc(datetime.now(UTC)))

    print("Recording quiet mode OFF.")
    if legacy_restore_count:
        print(f"Ignored {legacy_restore_count} legacy restore setpoints; quiet mode no longer changes setpoints.")
    else:
        print("No captured restore payload was present.")


async def status() -> None:
    async with asyncpg.create_pool(get_db_url(), min_size=1, max_size=2) as pool:
        async with pool.acquire() as conn:
            state = await fetch_system_state(conn)
            mode = state.get(QUIET_MODE_ENTITY) or "off"
            until = state.get(QUIET_UNTIL_ENTITY) or "(none)"
            reason = state.get(QUIET_REASON_ENTITY) or "(none)"
            active = quiet_is_active(state.get(QUIET_MODE_ENTITY), state.get(QUIET_UNTIL_ENTITY))
            restore_count = len(parse_restore_payload(state.get(QUIET_RESTORE_ENTITY)))

    print(f"mode: {mode}")
    print(f"active: {'yes' if active else 'no'}")
    print(f"until: {until}")
    print(f"reason: {reason}")
    print(f"restore setpoints captured: {restore_count}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Timed operator quiet mode for greenhouse recording")
    subparsers = parser.add_subparsers(dest="command", required=True)

    enable_parser = subparsers.add_parser("enable", help="enable recording quiet mode")
    enable_parser.add_argument("--minutes", type=int, default=30)
    enable_parser.add_argument("--reason", default="recording")
    enable_parser.add_argument(
        "--lights-off",
        action="store_true",
        help="also request both greenhouse grow-light circuits off",
    )

    subparsers.add_parser("disable", help="disable quiet mode and restore captured setpoints")
    subparsers.add_parser("status", help="show quiet mode state")

    args = parser.parse_args()
    if args.command == "enable":
        asyncio.run(enable(args.minutes, args.reason, args.lights_off))
    elif args.command == "disable":
        asyncio.run(disable())
    elif args.command == "status":
        asyncio.run(status())


if __name__ == "__main__":
    main()
