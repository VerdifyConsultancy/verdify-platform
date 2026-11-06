"""API-20's record: one grow-light switch in ``activity_log`` (VD-57, #1170; US-EQP-05; D-IRP-24).

The operator API calls ``verdify-setpoint-server`` itself, outside any database transaction, because this package holds
no HTTP client (README, Rules). So a switch is three steps, and the route runs them in this order:

1. ``stored`` refuses what ``recorded_write`` would refuse and answers a replayed ``Idempotency-Key`` with its stored
   result, a failure included, so a retried request calls nothing.
2. The route calls the setpoint server (``api/operator_routes/setpoint_server.py``), which gives a ``LightResult``.
3. ``record`` writes the one ``activity_log`` row through ``activity.recorded_write``: ``light.on`` or ``light.off``,
   target ``grow_light_main`` or ``grow_light_grow``, detail ``{light, state, result}``. A light has no
   ``desired_state`` row; the switch itself never reaches the ESP32, whose one writer stays the ingestor (a timed
   switch's controller hold goes there as an ``irrigation_command`` row the ingestor sends, below).

The answer is a ``LightRecord``: ``command()`` is the 200 body when Lutron reached the state, and ``refusal()`` the 503
``unavailable`` Problem otherwise. A light's state is never read from here: the board shows the Lutron readback (API-13).

The operator API's Back to automatic resync (``api/operator_routes/light_resync.py``, #1300) switches a light through
the same steps 2 and 3, its row's detail adding ``via`` and ``write_id``. It and API-20 both take ``try_lock`` (released
by ``unlock``) around steps 2 and 3: a session advisory lock per light, so the two operator API pods of a rollout never
switch one light at once. ``decision`` is the controller's newest decision for a light, which says whether its intent
is current.

A switch from API-20 is timed (#1335, Jason 2026-11-05): like a fan burst it lasts ``minutes`` (``MANUAL_MINUTES`` by
default, at most ``MAX_MANUAL_MINUTES``), stored in the row's detail, and then the light goes back to automatic.
``timer`` reads the light's newest switch row and when its time is up (``ts + minutes``, both by the database's clock);
the operator API's ``light_timer`` sends the controller's intent once it is. A row without ``minutes`` (Back to
automatic, the timer's own row, rows from before #1335) holds no timer. A confirmed timed switch also queues the
controller's own hold for the same minutes (``irrigation.writes.light_hold``, ``manual_override light_<light>_<state>``)
in the same transaction as its row, so the controller's schedule cannot switch the light back during them; Back to
automatic releases it (``release_hold``).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from types import MappingProxyType
from typing import Any, Literal
from uuid import UUID

import asyncpg
from pydantic import AwareDatetime, BaseModel, ConfigDict

from verdify_schemas.activity import ActivityLogRow
from verdify_services import activity
from verdify_services.activity import GREENHOUSE_ID, WriteResult
from verdify_services.actor import Actor
from verdify_services.irrigation import writes as irrigation_writes
from verdify_services.problems import Problem, problem

OP = "API-20"
Light = Literal["main", "grow"]
LightState = Literal["on", "off"]
# The controller's decision for each light (firmware controls.yaml): the firmware publishes it on every change and at
# least every 60 s, and the ingestor writes a system_state row for each publish, unchanged values included (its
# force_refresh), so the newest row's age says how long ago the controller last decided the light.
DECISION_ENTITIES: Mapping[str, str] = MappingProxyType({"main": "gl_main_reason", "grow": "gl_grow_reason"})
DECISION_WINDOW = timedelta(days=1)  # older rows are no decision now; the bound keeps the read on the newest chunk
# A manual switch lasts this long by default, like the Fans burst, then the light goes back to automatic (#1335). The
# bounds are the bursts' (API-IRR-12's minutes).
MANUAL_MINUTES = 15
MAX_MANUAL_MINUTES = 120
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


class LightDecision(BaseModel):
    """``decision``'s answer: the reason in the light's newest ``gl_<light>_reason`` row (``plant_lux_low``,
    ``outside_window``, ``auto_disabled``, ``time_invalid``, ...), when the ingestor received it, and the database's now,
    so its age needs no other clock. ``reason`` and ``observed_at`` are None when no row is a day old or newer."""

    model_config = ConfigDict(frozen=True)

    light: Light
    reason: str | None
    observed_at: AwareDatetime | None
    at: AwareDatetime

    @property
    def age_s(self) -> float | None:
        """How long ago the controller last decided the light; None for no decision."""
        return None if self.observed_at is None else (self.at - self.observed_at).total_seconds()


# The newest decision row, read against the database's own now().
_DECISION_SQL = """
SELECT now() AS at, s.value, s.ts
  FROM (SELECT 1) AS one
  LEFT JOIN LATERAL (SELECT value, ts FROM system_state
                      WHERE entity = $1 AND ts > now() - $2::interval
                      ORDER BY ts DESC LIMIT 1) AS s ON true
"""
# The light's newest switch row (API-20's, a Back to automatic resync's or the timer's) in the last day, and when its
# manual time is up: null when that row has no minutes. The bound keeps the read on the newest chunk of the
# (greenhouse_id, ts DESC) index; a timer is at most MAX_MANUAL_MINUTES long.
_TIMER_SQL = """
SELECT now() AS at, a.id, a.ts + make_interval(mins => (a.detail->>'minutes')::int) AS ends_at
  FROM (SELECT 1) AS one
  LEFT JOIN LATERAL (SELECT id, ts, detail FROM public.activity_log
                      WHERE greenhouse_id = $1 AND target = $2 AND action IN ('light.on', 'light.off')
                        AND ts > now() - $3::interval
                      ORDER BY ts DESC, id DESC LIMIT 1) AS a ON true
"""
TIMER_WINDOW = timedelta(days=1)
# One advisory key per light, in the session lock space. A transaction-level lock could not cover the call, which runs
# outside any transaction; returning the connection to the pool also releases it (asyncpg's reset runs
# pg_advisory_unlock_all), so a holder that fails before unlock still lets go.
_TRY_LOCK_SQL = "SELECT pg_catalog.pg_try_advisory_lock(pg_catalog.hashtext('light:' || $1))"
_UNLOCK_SQL = "SELECT pg_catalog.pg_advisory_unlock(pg_catalog.hashtext('light:' || $1))"


def _target(light: Light) -> str:
    return f"grow_light_{light}"


def _request(state: LightState, reason: str | None, minutes: int | None = None) -> dict[str, Any]:
    """Every input the target does not carry: the state from the path and the body (``minutes`` only when timed)."""
    request: dict[str, Any] = {"state": state, "reason": reason}
    if minutes is not None:
        request["minutes"] = minutes
    return request


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
    minutes: int | None = None,
) -> WriteResult[LightRecord] | None:
    """Step 1: the stored result of a replayed key, or None to go ahead. Refuses a viewer, a bad reason and a key
    already used for another request, before anything is called."""
    return await activity.stored_write(
        conn,
        actor,
        op=OP,
        target=_target(light),
        request=_request(state, reason, minutes),
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
    extra_detail: Mapping[str, Any] | None = None,
    minutes: int | None = None,
) -> WriteResult[LightRecord]:
    """Step 3: the switch's one ``activity_log`` row. A concurrent request with the same key that got here first wins,
    and this one answers its stored result. ``minutes`` makes it a timed manual switch (detail ``minutes``, read by
    ``timer``). ``extra_detail`` adds keys to the detail, never ``light``, ``state``, ``result`` or ``minutes`` (the
    resync's ``via`` and ``write_id``)."""
    extra = dict(extra_detail or {})
    timed = {} if minutes is None else {"minutes": minutes}
    clash = sorted({"light", "state", "result", "minutes"} & set(extra))
    if clash:
        raise ValueError(f"extra_detail may not set {', '.join(clash)}")

    async def perform(conn: asyncpg.Connection) -> tuple[None, dict[str, Any]]:
        detail = {**extra, **timed, "light": light, "state": state, "result": result}
        if minutes is not None and result == "confirmed":
            # The controller holds the light for the same minutes (#1335), so a lighting decision change (a window
            # opening, occupancy, lux) cannot switch it back before the timer ends.
            command = await irrigation_writes.light_hold(conn, actor, light, state, minutes, reason)
            if command is not None:
                detail["hold_command_id"] = str(command.command_id)
        return None, detail

    written = await activity.recorded_write(
        conn,
        actor,
        op=OP,
        action=f"light.{state}",
        target=_target(light),
        request=_request(state, reason, minutes),
        request_id=request_id,
        reason=reason,
        perform=perform,
        load=_load,
    )
    if written.replayed:
        return written
    value = LightRecord(light=light, state=state, result=result, activity_id=written.activity_id)
    return written.model_copy(update={"value": value})


async def release_hold(conn: asyncpg.Connection, actor: Actor, light: Light, reason: str | None = None) -> bool:
    """Back to automatic: release the controller's hold on ``light`` (#1335). True when the release was queued."""
    return await irrigation_writes.light_hold(conn, actor, light, None, reason=reason) is not None


async def try_lock(conn: asyncpg.Connection, light: Light) -> bool:
    """Take the light's advisory lock for this session without waiting: True when it now holds it, False when another
    session does. The holder keeps the connection until ``unlock``; returning it to the pool releases the lock too."""
    return bool(await conn.fetchval(_TRY_LOCK_SQL, _target(light)))


async def unlock(conn: asyncpg.Connection, light: Light) -> bool:
    """Release the light's advisory lock this session holds; False when it held none."""
    return bool(await conn.fetchval(_UNLOCK_SQL, _target(light)))


async def decision(conn: asyncpg.Connection, light: Light) -> LightDecision:
    """The controller's newest decision for the light (``DECISION_ENTITIES``), for the caller to judge its age."""
    row = await conn.fetchrow(_DECISION_SQL, DECISION_ENTITIES[light], DECISION_WINDOW)
    return LightDecision(light=light, reason=row["value"], observed_at=row["ts"], at=row["at"])


class LightTimer(BaseModel):
    """``timer``'s answer: the light's newest switch row in the last day (``activity_id``, None for none), when its manual
    time is up (``ends_at``, None when that row holds no timer) and the database's now."""

    model_config = ConfigDict(frozen=True)

    light: Light
    activity_id: int | None
    ends_at: AwareDatetime | None
    at: AwareDatetime

    @property
    def due(self) -> bool:
        """The newest row is a timed manual switch whose time is up."""
        return self.ends_at is not None and self.ends_at <= self.at


async def timer(conn: asyncpg.Connection, light: Light) -> LightTimer:
    """The light's manual timer, from its newest switch row (``_TIMER_SQL``)."""
    row = await conn.fetchrow(_TIMER_SQL, GREENHOUSE_ID, _target(light), TIMER_WINDOW)
    return LightTimer(light=light, activity_id=row["id"], ends_at=row["ends_at"], at=row["at"])
