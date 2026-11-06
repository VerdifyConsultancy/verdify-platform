"""Timed manual lights (#1335): a manual switch goes back to automatic when its minutes are up. No routes and no SQL.

Jason, 2026-11-05: a manual light switch from the dashboard gets a 15-minute timer, like the Fans burst, then returns
to automatic. A Fans burst is a deadline latch on the controller, but a light never reaches the ESP32 from here
(D-IRP-24): API-20 switches Lutron through verdify-setpoint-server, the one Home Assistant light writer outside the
firmware (#399). So the timer lives on this side and ends through that same server:

1. API-20 stores ``minutes`` in its ``activity_log`` row's detail (``lights.record``); the row's ``ts`` plus those
   minutes is when the manual switch ends (``lights.timer``, by the database's clock). Nothing else is stored, so a
   restart or a rollout loses no timer: the row is the timer.
2. Every ``SWEEP_S`` each operator API pod reads each light's newest switch row. When that row is timed and its time is
   up, it takes the light's lock (``Resyncs.locks`` here and ``lights.try_lock`` across the pods, which API-20 and the
   Back to automatic resync take too), reads the row again, and checks what the resync checks: the controller decided
   the light in the last ``DECISION_FRESH_S`` with a valid time (not ``time_invalid``), and API-13
   reads the intent and the Lutron state known and different (``resync_state``). Then it sends the intent and records
   the row as API-20 does, under ``Actor.system("light_timer")``, with the reason ``TIMER_ENDED`` and ``detail.via``
   ``timer`` and ``detail.timed_activity_id``. That row has no minutes, so it ends the timer for every pod.
3. When the Lutron state already matches the intent there is nothing to send and the timer is done. When the
   controller is not deciding, its decision is stale or a state is unknown, the light stays as it is and the next
   sweep tries again, each cause logged once per timer.

The setpoint server is single-threaded and every caller holds the light's lock, so the timer, a press and a resync
never switch one light at once. The controller's own switching does not go through the server: its ``apply_light``
writes Home Assistant only when its decision changes. So a confirmed timed switch also queues the controller's own hold
for the same minutes (``lights.record`` → ``manual_override light_<light>_<state>``, the per-circuit deadline latch the
bursts have, #1335's firmware part), and during the minutes a decision change no longer switches the light. Before that
firmware is flashed the controller ignores the hold, and a decision change can still bring automatic back early.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Literal

import asyncpg

from operator_routes import light_resync, setpoint_server
from operator_routes.light_resync import Resyncs
from verdify_services import lights, reads
from verdify_services.actor import Actor
from verdify_services.lights import Light, LightState

log = logging.getLogger(__name__)

SWEEP_S = 15.0  # how often each pod looks for a timer that is up; a light goes back within about this long
TIMER_ENDED = "Timer ended"  # the reason on the timer's row
VIA = "timer"  # detail.via on the timer's row
ACTOR = Actor.system("light_timer")
# The controller has no valid time, so its intent says nothing. Unlike the Back to automatic resync, auto_disabled is
# a decision here: automation off holds the light off (firmware evaluate_lighting), and the timer then sends off.
NOT_DECIDING = frozenset({"time_invalid"})

Outcome = Literal[
    "sent",  # Lutron confirmed the intent
    "failed",  # the command went out and was not confirmed; the light is left to the controller
    "matched",  # Lutron already reads the intent
    "idle",  # no timer is up
    "busy",  # a command is switching the light; the next sweep looks again
    "retry",  # the controller is not deciding, its decision is stale or a state is unknown; the next sweep tries again
    "deadline",  # the checks did not finish within DECIDE_S; the next sweep tries again
]


class Timers:
    """The sweeps in one operator API process (``app.state.light_timers``): the timers already ended here and the
    retry causes already logged, so a light that cannot go back yet logs once rather than every sweep."""

    def __init__(self, resyncs: Resyncs) -> None:
        self.resyncs = resyncs
        self.ended: dict[Light, int | None] = {}
        self.logged: dict[Light, tuple[int | None, str]] = {}

    async def run(self, pool: asyncpg.Pool, setpoint_url: str, interval_s: float = SWEEP_S) -> None:
        """The lifespan task: a sweep every ``interval_s``, the first one ``interval_s`` after the start. A failed sweep
        is logged and the next one goes on. Without a setpoint server URL nothing can switch a light, so it never sweeps.
        """
        if not setpoint_url:
            log.warning("Light timer: no setpoint server URL, so a manual light stays until its controller changes it")
            return
        while True:
            await asyncio.sleep(interval_s)
            for light in light_resync.EQUIPMENT:
                try:
                    await self.sweep(light, pool, setpoint_url)
                except asyncio.CancelledError:
                    raise
                except Exception as failure:
                    log.warning("Light timer: the %s light's sweep failed (%s)", light, type(failure).__name__)

    async def sweep(self, light: Light, pool: asyncpg.Pool, setpoint_url: str) -> Outcome:
        """One light: send its intent when its timer is up, under the light's lock; what happened."""
        async with pool.acquire() as conn:
            due = await lights.timer(conn, light)
        if not due.due or self.ended.get(light) == due.activity_id:
            return "idle"
        lock = self.resyncs.locks[light]
        if lock.locked():
            return "busy"
        deadline = asyncio.get_running_loop().time() + light_resync.DECIDE_S
        async with contextlib.AsyncExitStack() as held:
            try:
                async with asyncio.timeout_at(deadline):
                    conn = await held.enter_async_context(pool.acquire())
                    if not await light_resync._take(lock):
                        return "busy"
                    held.callback(lock.release)
                    presses = self.resyncs.presses[light]
                    if not await lights.try_lock(conn, light):
                        return "busy"
                    held.push_async_callback(light_resync._unlock, conn, light)
                    timer = await lights.timer(conn, light)
                    if not timer.due:  # a press, a resync or the other pod's timer came first
                        return "idle"
                    verdict, detail = await _verdict(conn, light)
                    if self.resyncs.presses[light] != presses:
                        return "busy"
            except TimeoutError:
                return self._retry(light, due.activity_id, "deadline", f"checks over {light_resync.DECIDE_S:g} s")
            if verdict == "matched":
                self.ended[light] = timer.activity_id
                log.info("Light timer: the %s light's manual time is up and it reads %s, its intent", light, detail)
                return "matched"
            if verdict == "retry":
                return self._retry(light, timer.activity_id, "retry", detail)
            outcome = await _send(conn, setpoint_url, light, verdict, timer.activity_id)
        self.ended[light] = timer.activity_id
        return outcome

    def _retry(self, light: Light, activity_id: int | None, outcome: Outcome, detail: str | None) -> Outcome:
        cause = f"{outcome}: {detail}"
        if self.logged.get(light) != (activity_id, cause):
            self.logged[light] = (activity_id, cause)
            log.info("Light timer: the %s light's manual time is up; it stays as it is for now (%s)", light, cause)
        return outcome


async def _verdict(conn: asyncpg.Connection, light: Light) -> tuple[LightState | Literal["matched", "retry"], str]:
    """The state to send, ``matched`` when Lutron reads the intent, or ``retry`` with why not yet."""
    decision = await lights.decision(conn, light)
    if decision.age_s is None or decision.age_s > light_resync.DECISION_FRESH_S:
        return "retry", "no decision in a day" if decision.age_s is None else f"decision {decision.age_s:.0f} s old"
    if decision.reason in NOT_DECIDING:
        return "retry", f"the controller's decision is {decision.reason}"
    item = light_resync._light_item(await reads.equipment(conn), light)
    state = light_resync.resync_state(item)
    if state is not None:
        return state, state
    if item is not None and item.intent in ("on", "off") and item.state == item.intent:
        return "matched", item.state
    return "retry", "unknown" if item is None else f"intent {item.intent}, Lutron {item.state}"


async def _send(
    conn: asyncpg.Connection, setpoint_url: str, light: Light, state: LightState, timed_id: int | None
) -> Outcome:
    """The call, then its row, as API-20 records it. A failed row is logged; the light has still switched."""
    result = await setpoint_server.switch(setpoint_url, light, state)
    try:
        async with asyncio.timeout(light_resync.RECORD_S):
            await lights.record(
                conn, ACTOR, light, state, result, TIMER_ENDED, None, {"via": VIA, "timed_activity_id": timed_id}
            )
    except Exception as failure:
        log.error(
            "Light timer: sent the %s light %s (%s), but its Activity row was not written (%s)",
            light,
            state,
            result,
            type(failure).__name__,
        )
    if result == "confirmed":
        log.info("Light timer: the %s light's manual time is up; sent %s, its intent", light, state)
        return "sent"
    log.warning("Light timer: the %s light's manual time is up; sent %s, not confirmed (%s)", light, state, result)
    return "failed"
