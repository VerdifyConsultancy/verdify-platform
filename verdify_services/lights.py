"""API-20's record: one grow-light switch in ``activity_log`` (VD-57, #1170; US-EQP-05; D-IRP-24).

The operator API calls ``verdify-setpoint-server`` itself, outside any database transaction, because this package holds
no HTTP client (README, Rules). So a switch is three steps, and the route runs them in this order:

1. ``stored`` refuses what ``recorded_write`` would refuse and answers a replayed ``Idempotency-Key`` with its stored
   result, a failure included, so a retried request calls nothing.
2. The route calls the setpoint server (``api/operator_routes/setpoint_server.py``), which gives a ``LightResult``.
3. ``record`` writes the one ``activity_log`` row through ``activity.recorded_write``: ``light.on`` or ``light.off``,
   target ``grow_light_main`` or ``grow_light_grow``, detail ``{light, state, result}``. That row is the only stored
   thing: a light has no ``desired_state`` row and never reaches the ESP32, whose one writer stays the ingestor.

The answer is a ``LightRecord``: ``command()`` is the 200 body when Lutron reached the state, and ``refusal()`` the 503
``unavailable`` Problem otherwise. A light's state is never read from here: the board shows the Lutron readback (API-13).
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Literal
from uuid import UUID

import asyncpg
from pydantic import BaseModel, ConfigDict

from verdify_schemas.activity import ActivityLogRow
from verdify_services import activity
from verdify_services.activity import WriteResult
from verdify_services.actor import Actor
from verdify_services.problems import Problem, problem

OP = "API-20"
Light = Literal["main", "grow"]
LightState = Literal["on", "off"]
# What the setpoint server's answer means: confirmed is 200, ha_failed 502, not_confirmed 504 and timeout no usable
# answer in 12 s. The failures are problems.LightResult.
LightResult = Literal["confirmed", "ha_failed", "not_confirmed", "timeout"]
FAILURE_WORDS: Mapping[str, str] = MappingProxyType(
    {
        "ha_failed": "Home Assistant did not answer",
        "not_confirmed": "Lutron did not reach the state",
        "timeout": "The light server did not answer in time; the light may still switch",
    }
)


class LightCommand(BaseModel):
    """API-20's 200 (OpenAPI ``LightCommand``): a switch that Lutron confirmed."""

    model_config = ConfigDict(frozen=True)

    light: Light
    state: LightState
    result: Literal["confirmed"]
    activity_id: int


class LightRecord(BaseModel):
    """One switch as its ``activity_log`` row holds it."""

    model_config = ConfigDict(frozen=True)

    light: Light
    state: LightState
    result: LightResult
    activity_id: int

    def command(self) -> LightCommand | None:
        """The 200 body, or None when the switch did not confirm."""
        if self.result != "confirmed":
            return None
        return LightCommand(light=self.light, state=self.state, result="confirmed", activity_id=self.activity_id)

    def refusal(self) -> Problem | None:
        """The 503 ``unavailable`` (retryable, with its ``result``), or None when the switch confirmed."""
        if self.result == "confirmed":
            return None
        return problem("unavailable", FAILURE_WORDS[self.result], result=self.result)


def _target(light: Light) -> str:
    return f"grow_light_{light}"


def _request(state: LightState, reason: str | None) -> dict[str, Any]:
    """Every input the target does not carry: the state from the path and the body."""
    return {"state": state, "reason": reason}


async def _load(_conn: asyncpg.Connection, row: ActivityLogRow) -> LightRecord:
    return LightRecord(
        light=row.detail["light"], state=row.detail["state"], result=row.detail["result"], activity_id=row.id
    )


async def stored(
    conn: asyncpg.Connection,
    actor: Actor,
    light: Light,
    state: LightState,
    reason: str | None = None,
    request_id: UUID | str | None = None,
) -> WriteResult[LightRecord] | None:
    """Step 1: the stored result of a replayed key, or None to go ahead. Refuses a viewer, a bad reason and a key
    already used for another request, before anything is called."""
    return await activity.stored_write(
        conn,
        actor,
        op=OP,
        target=_target(light),
        request=_request(state, reason),
        request_id=request_id,
        reason=reason,
        load=_load,
    )


async def record(
    conn: asyncpg.Connection,
    actor: Actor,
    light: Light,
    state: LightState,
    result: LightResult,
    reason: str | None = None,
    request_id: UUID | str | None = None,
) -> WriteResult[LightRecord]:
    """Step 3: the switch's one ``activity_log`` row. A concurrent request with the same key that got here first wins,
    and this one answers its stored result."""

    async def perform(_conn: asyncpg.Connection) -> tuple[None, dict[str, str]]:
        return None, {"light": light, "state": state, "result": result}

    written = await activity.recorded_write(
        conn,
        actor,
        op=OP,
        action=f"light.{state}",
        target=_target(light),
        request=_request(state, reason),
        request_id=request_id,
        reason=reason,
        perform=perform,
        load=_load,
    )
    if written.replayed:
        return written
    value = LightRecord(light=light, state=state, result=result, activity_id=written.activity_id)
    return written.model_copy(update={"value": value})
