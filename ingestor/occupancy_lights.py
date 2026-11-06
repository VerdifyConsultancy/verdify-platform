"""Activity rows for the controller's occupancy lights (#1370).

The controller is the one writer of the grow lights (#399): its lighting policy
turns a circuit on with reason ``occupancy_lux_low`` when someone is in the
greenhouse and it is dark enough outside: the main lights at any hour, night
included, and the grow lights only inside their photoperiod window, so the
Vandas keep their dark. It returns the circuit to its automatic decision when the
occupancy latch clears.
This module writes one ``activity_log`` row for each of those two changes, cause
``occupancy``, so Activity shows why a light came on and when it went back.

It watches the controller's published decision (``gl_<light>_state`` ON/OFF and
``gl_<light>_reason``) as the ingestor receives it:

- the reason becomes ``occupancy_lux_low`` while the decision is ON: ``light.on``;
- a circuit turned on that way goes OFF: ``light.off`` (back to automatic);
- a plant-supplement reason takes over while it is still ON: the circuit is the
  plant schedule's again and no row is written.

Rows are queued here and written by ``drain``, which ``activity_loop`` calls every few seconds,
because the ESPHome state callback cannot await the database. The ingestor
login has INSERT on ``activity_log`` (migration 280); rows carry
``source='system'``, actor ``controller``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from datetime import UTC, datetime
from typing import Literal

import asyncpg

from verdify_schemas.activity import ActivityLogRow

log = logging.getLogger("occupancy_lights")

LIGHTS = ("main", "grow")
ON_REASON = "occupancy_lux_low"
CAUSE = "occupancy"
ACTOR = "controller"
REASONS = {
    "on": "Someone is in the greenhouse: the controller turned the light on",
    "off": "No one seen for 15 min: the light is back on automatic",
}
INSERT_SQL = (
    "INSERT INTO public.activity_log (greenhouse_id, ts, source, actor, action, target, detail, reason) "
    "VALUES ($1, $2, $3, $4, $5, $6, $7::text::jsonb, $8)"
)
_QUEUE_MAX = 64

Change = Literal["on", "off"]


def light_for_entity(entity: str) -> str | None:
    """``main`` for ``gl_main_state``/``gl_main_reason``, and so on; None for any other entity."""
    for light in LIGHTS:
        if entity in (f"gl_{light}_state", f"gl_{light}_reason"):
            return light
    return None


class OccupancyLightLog:
    """Which circuits the controller turned on for occupancy, and the rows still to write."""

    def __init__(self) -> None:
        self.owned: dict[str, bool] = {light: False for light in LIGHTS}
        self.pending: deque[ActivityLogRow] = deque(maxlen=_QUEUE_MAX)

    def observe(self, light: str, decision: str | None, reason: str | None, now: datetime) -> Change | None:
        """Feed the controller's current decision and reason for one light; return the change it records."""
        if light not in self.owned:
            return None
        change: Change | None = None
        if not self.owned[light]:
            if decision == "ON" and reason == ON_REASON:
                self.owned[light] = True
                change = "on"
        elif decision == "OFF":
            self.owned[light] = False
            change = "off"
        elif reason is not None and reason.startswith("plant_"):
            self.owned[light] = False
        if change is not None:
            self.pending.append(
                ActivityLogRow(
                    ts=now,
                    source="system",
                    actor=ACTOR,
                    action=f"light.{change}",
                    target=f"grow_light_{light}",
                    detail={"light": light, "state": change, "cause": CAUSE, "controller_reason": reason},
                    reason=REASONS[change],
                )
            )
            log.info("Occupancy light: %s %s (controller reason %s)", light, change.upper(), reason)
        return change


LIGHT_LOG = OccupancyLightLog()


def observe_state(entity: str, system: dict[str, str]) -> None:
    """Hook for the ingestor's text-sensor path: ``system`` is its current controller state."""
    light = light_for_entity(entity)
    if light is not None:
        LIGHT_LOG.observe(light, system.get(f"gl_{light}_state"), system.get(f"gl_{light}_reason"), datetime.now(UTC))


async def drain(pool: asyncpg.Pool, light_log: OccupancyLightLog = LIGHT_LOG) -> int:
    """Write the queued rows; a row that fails is logged and dropped (the decision itself is in system_state)."""
    written = 0
    while light_log.pending:
        row = light_log.pending.popleft()
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    INSERT_SQL,
                    row.greenhouse_id,
                    row.ts,
                    row.source,
                    row.actor,
                    row.action,
                    row.target,
                    json.dumps(row.detail),
                    row.reason,
                )
            written += 1
        except Exception as exc:  # noqa: BLE001
            log.error("Occupancy light: activity row for %s %s failed: %s", row.target, row.action, exc)
    return written


async def activity_loop(pool: asyncpg.Pool) -> None:
    """Write the queued rows every few seconds. Never raises."""
    while True:
        await asyncio.sleep(5)
        try:
            await drain(pool)
        except Exception as exc:  # noqa: BLE001
            log.error("Occupancy light: drain failed: %s", exc)
