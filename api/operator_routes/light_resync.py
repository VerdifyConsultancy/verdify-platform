"""Back to automatic, finished on the server (#1300). No routes and no SQL: it reads through ``verdify_services`` and
switches through ``setpoint_server.switch``, as API-20 does.

The controller's ``apply_light`` switches a light only when its own decision changes, so a light switched by hand stays
where it is after its automatic switch comes back on, until that decision next changes. The Equipment page
(``useLightControl``) sends the one API-20 command that matches it, but only while the page stays open: on 2026-11-04
Back to automatic was pressed at 20:20:25Z with the main light ON and the intent OFF, the page moved to Graphs 5 s
later, and the light stayed ON until 21:04Z. So the page's Back to automatic write starts ``Resyncs.resync`` here too,
and the page's own resync stays the fast path:

1. ``start``: only a new API-10 write (not a replay) of ``sw_gl_main_auto_mode`` or ``sw_gl_grow_auto_mode`` with the
   value 1, ``manual`` true and the reason ``BACK_TO_AUTOMATIC``, which only the Equipment button sends. A Settings
   drawer Save or Revert of the switch carries its own reason and starts nothing.
2. Wait for the write to read CONFIRMED by API-09's rule (``settings.status``: it is still the switch's newest desired
   row, and its state is ``confirmed``), reading every 5 s as the page reads a REQUESTED row, for no longer than the
   page's window: the 5 min (``settings.REQUESTED_FOR``) the row reads REQUESTED before it reads NOT CONFIRMED. A newer
   write of the switch ends the wait; a failed read waits on.
3. Leave the page ``PAGE_FIRST_S`` to send its own command.
4. Then one step under the light's lock: ``Resyncs.locks`` in this process and ``lights.try_lock`` across the two pods
   of a rollout, which API-20 takes around its call as well. If either is held, a Turn on, Turn off or the page's
   resync is switching the light, and the resync stands down. Otherwise, within ``DECIDE_S`` of asking for a
   connection, it checks that the write is still the newest and reads CONFIRMED; that no light command was recorded
   since it; that the controller decided the light in the last ``DECISION_FRESH_S`` (``lights.decision``), and not as
   ``auto_disabled`` or ``time_invalid``; that no Turn on or Turn off started meanwhile; and that API-13 reads the
   intent and the Lutron state known and different (``resync_state``, the page's ``resyncState``). The heartbeat
   matters because the ingestor keeps writing climate rows from its cached values for up to 10 min after the
   controller goes quiet, so API-13 can show a minutes-old intent as current. Then it sends that newest intent at once,
   holding the connection it checked on (no transaction is open on it), and records the row: API-20's, under the
   write's actor, with the reason "Back to automatic" and ``detail.via`` ``server_resync`` and ``detail.write_id``.

A Turn on or Turn off while a resync holds the lock waits up to ``LOCK_WAIT_S`` for it and then goes ahead either way,
so it reaches the single-threaded setpoint server after the resync's command and wins. Each resync logs one line: what
it sent, or why it sent nothing.

The tasks live only in this process (``app.state.resyncs``). When the operator API stops (a rollout, an eviction), a
resync still waiting is dropped and logged, and nothing then matches that light: the page's row reads its "differ", and
pressing Back to automatic again starts a new one. A command already on its way gets ``SHUTDOWN_S`` to finish and write
its row. No resync reads ``DASHBOARD_WRITES_ENABLED``, which only the BFF holds: one that started before the flag turned
off still finishes, within about 6 min of its write.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

import asyncpg
from fastapi import FastAPI

from operator_routes import setpoint_server
from verdify_services import activity, lights, reads, settings
from verdify_services.activity import WriteResult
from verdify_services.actor import Actor
from verdify_services.lights import Light, LightState

log = logging.getLogger(__name__)

# The reason only the Equipment page's Back to automatic write and its own resync carry. It must equal BACK_TO_AUTOMATIC
# in dashboard/app/components/equipment/light-controls.tsx (tests/test_operator_lights.py reads it there): a write of the
# switch with any other reason starts no resync.
BACK_TO_AUTOMATIC = "Back to automatic"
VIA = "server_resync"  # detail.via on the resync's row, beside detail.write_id, so it reads apart from the page's row
# Each grow light's automatic switch, and the light's API-13 item, which is also API-20's activity target (the page's
# LIGHTS).
SWITCHES: Mapping[str, Light] = MappingProxyType({"sw_gl_main_auto_mode": "main", "sw_gl_grow_auto_mode": "grow"})
EQUIPMENT: Mapping[Light, str] = MappingProxyType({"main": "grow_light_main", "grow": "grow_light_grow"})
POLL_S = 5.0  # the page reads a REQUESTED row every 5 s (READBACK_POLL_MS)
WINDOW_S = settings.REQUESTED_FOR.total_seconds()  # a write reads REQUESTED this long, then NOT CONFIRMED
# The page's resync lands within its next read of the row, its API-13 read and API-20's 12 s cap: wait that, plus a
# margin, before deciding, so the page goes first whenever it is open.
PAGE_FIRST_S = POLL_S + setpoint_server.TIMEOUT_S + 5.0
# From asking for a connection to deciding to send: past it the resync stands down and sends nothing.
DECIDE_S = 2.0
# How long API-20 waits for a resync of its light that holds the lock. It is longer than DECIDE_S, so by then that
# resync has stood down or its command is at the setpoint server; with API-20's 12 s cap it stays inside the BFF's 15 s.
LOCK_WAIT_S = 2.5
LOCK_POLL_S = 0.25  # how often API-20 asks again for an advisory lock the other pod holds
# The controller decides each light at least every 60 s; an older decision is not the controller's intent now.
DECISION_FRESH_S = 120.0
# Decisions that hold the light off whatever its automation would do: the automatic switch is off, or the controller
# has no valid time (firmware evaluate_lighting, and controls.yaml's time_invalid branch).
NOT_DECIDING = frozenset({"auto_disabled", "time_invalid"})
SHUTDOWN_S = 15.0  # when the operator API stops, a command on its way gets this long to finish and write its row
RECORD_S = 5.0  # the row after the call

Outcome = Literal[
    "sent",  # Lutron confirmed the command
    "failed",  # the command went out and was not confirmed: ha_failed, not_confirmed or timeout
    "matched",  # Lutron already reads the intent
    "unknown",  # the intent or the Lutron state is unknown
    "superseded",  # a newer write of the switch
    "not_confirmed",  # the write did not read CONFIRMED in the window, or no longer does
    "commanded",  # a light command was recorded since the write: a Turn on or Turn off, or the page's resync
    "busy",  # a light command is under way, in this process or in the other pod
    "pressed",  # a Turn on or Turn off started while the resync checked
    "stale",  # no controller decision for the light in the last DECISION_FRESH_S
    "not_deciding",  # the controller's decision is auto_disabled or time_invalid
    "deadline",  # the checks did not finish within DECIDE_S
    "error",  # an unexpected failure before anything was sent
]
_STOOD_DOWN: Mapping[str, str] = MappingProxyType(
    {
        "unknown": "the controller's intent or the Lutron state is unknown",
        "superseded": "a newer write of the switch took its place",
        "not_confirmed": "the write does not read CONFIRMED",
        "commanded": "a light command was recorded since the write",
        "busy": "another command is switching the light",
        "pressed": "Turn on or Turn off was pressed while it checked",
        "stale": "the controller has not decided the light lately",
        "not_deciding": "the controller is not deciding the light",
        "deadline": "its checks did not finish in time",
        "error": "it failed",
    }
)


def resync_state(item: reads.EquipmentItem | None) -> LightState | None:
    """The one API-20 state Back to automatic sends, as the page's ``resyncState``: the controller's intent when it and
    the Lutron readback are both known and differ; else None."""
    if item is None or item.intent not in ("on", "off") or item.state not in ("on", "off"):
        return None
    return item.intent if item.intent != item.state else None


def _light_item(board: reads.Equipment, light: Light) -> reads.EquipmentItem | None:
    return next((item for item in board.items if item.equipment == EQUIPMENT[light]), None)


@dataclass
class _Run:
    """One resync: its light and write, and whether its command is on its way (a stop then lets it finish)."""

    light: Light
    write: settings.DesiredSetting
    sending: bool = False


async def _until_confirmed(
    pool: asyncpg.Pool, write: settings.DesiredSetting
) -> tuple[Literal["confirmed", "superseded", "not_confirmed"], str | None]:
    """Step 2: the verdict, and the type of the last failed read, for the log. The window bounds the whole wait, a slow
    read included."""
    failed: str | None = None
    try:
        async with asyncio.timeout(WINDOW_S):
            while True:
                await asyncio.sleep(POLL_S)
                try:
                    async with pool.acquire() as conn:
                        status = await settings.status(conn, write.param)
                except Exception as failure:  # the page's read waits on too
                    failed = type(failure).__name__
                    continue
                if status.desired is None or status.desired.id != write.id:
                    return "superseded", None
                if status.state == "confirmed":
                    return "confirmed", None
                if status.state == "not_confirmed":  # the window closed, by the database's clock
                    break
    except TimeoutError:  # the window closed, by this process's clock
        pass
    detail = f"not within {WINDOW_S:.0f} s" + (f"; the last read failed with {failed}" if failed else "")
    return "not_confirmed", detail


async def _verdict(
    conn: asyncpg.Connection, light: Light, write: settings.DesiredSetting
) -> tuple[Outcome | LightState, str | None]:
    """Step 4's checks, right before the call: the state to send, or why none is, with a detail for the log."""
    status = await settings.status(conn, write.param)
    if status.desired is None or status.desired.id != write.id:
        return "superseded", None
    if status.state != "confirmed":
        return "not_confirmed", f"it reads {status.state}"
    since = await activity.list(conn, action="light.", target=EQUIPMENT[light], from_=write.created_at, limit=1)
    if since.items:
        return "commanded", f"{since.items[0].action}, activity {since.items[0].id}"
    decision = await lights.decision(conn, light)
    if decision.age_s is None or decision.age_s > DECISION_FRESH_S:
        return "stale", "no decision in a day" if decision.age_s is None else f"its last is {decision.age_s:.0f} s old"
    if decision.reason in NOT_DECIDING:
        return "not_deciding", decision.reason
    item = _light_item(await reads.equipment(conn), light)
    state = resync_state(item)
    if state is not None:
        return state, None
    if item is not None and item.intent in ("on", "off") and item.state == item.intent:
        return "matched", item.state
    return "unknown", None if item is None else f"intent {item.intent}, Lutron {item.state}"


async def _take(lock: asyncio.Lock) -> bool:
    """Take the lock only when no one holds it or waits for it: asyncio's Lock has no try-acquire."""
    if lock.locked():
        return False
    try:
        async with asyncio.timeout(0):  # a free lock is taken without suspending; any wait times out at once
            await lock.acquire()
    except TimeoutError:
        return False
    return True


async def _unlock(conn: asyncpg.Connection, light: Light) -> None:
    """Release the light's advisory lock; on a failure the pool's reset releases it with the connection."""
    try:
        await lights.unlock(conn, light)
    except Exception as failure:
        log.warning("the %s light's advisory lock was not released (%s)", light, type(failure).__name__)


async def _advisory(conn: asyncpg.Connection, light: Light, deadline: float) -> bool:
    """API-20's advisory lock, asked for every ``LOCK_POLL_S`` until ``deadline``: False to go ahead without it. It only
    keeps a resync in the other pod from switching the light at the same time, so it never refuses a press."""
    loop = asyncio.get_running_loop()
    while True:
        try:
            if await lights.try_lock(conn, light):
                return True
        except Exception as failure:
            log.warning("API-20 goes ahead without the %s light's advisory lock (%s)", light, type(failure).__name__)
            return False
        remaining = deadline - loop.time()
        if remaining <= 0:
            return False
        await asyncio.sleep(min(LOCK_POLL_S, remaining))


async def _send(
    conn: asyncpg.Connection,
    setpoint_url: str,
    actor: Actor,
    light: Light,
    state: LightState,
    write: settings.DesiredSetting,
) -> tuple[Outcome, str]:
    """The call, then its row, as API-20 records it. A stop that outlasts ``SHUTDOWN_S`` cancels the call; it may have
    reached the server already, so the row says timeout (only the readback can tell) and the cancellation goes on."""
    stopped: asyncio.CancelledError | None = None
    try:
        result = await setpoint_server.switch(setpoint_url, light, state)
    except asyncio.CancelledError as cancelled:
        result, stopped = "timeout", cancelled
    try:
        async with asyncio.timeout(RECORD_S):
            await lights.record(
                conn, actor, light, state, result, BACK_TO_AUTOMATIC, None, {"via": VIA, "write_id": write.id}
            )
    except Exception as failure:
        log.error(
            "Back to automatic: write %d sent the %s light %s (%s), but its Activity row was not written (%s)",
            write.id,
            light,
            state,
            result,
            type(failure).__name__,
        )
    if stopped is not None:
        raise stopped
    return ("sent" if result == "confirmed" else "failed"), f"{state}, {result}"


def _report(run: _Run, outcome: Outcome, detail: str | None) -> None:
    """The one line each resync logs."""
    write, light = run.write, run.light
    if outcome == "sent":
        log.info("Back to automatic: write %d switched the %s light (%s)", write.id, light, detail)
    elif outcome == "failed":
        log.warning("Back to automatic: write %d sent the %s light, not confirmed (%s)", write.id, light, detail)
    elif outcome == "matched":
        log.info("Back to automatic: write %d found the %s light %s, its intent; nothing sent", write.id, light, detail)
    else:
        level = logging.WARNING if outcome in ("not_confirmed", "deadline", "error") else logging.INFO
        words = _STOOD_DOWN[outcome] + (f" ({detail})" if detail else "")
        log.log(
            level,
            "Back to automatic: write %d of %s left the %s light as it was: %s",
            write.id,
            write.param,
            light,
            words,
        )


class Resyncs:
    """Back to automatic in one operator API process (``app.state.resyncs``): one lock per light, which API-20 and the
    resync share, the Turn on and Turn off presses started on each light, and the running resyncs."""

    def __init__(self) -> None:
        self.locks: dict[Light, asyncio.Lock] = {light: asyncio.Lock() for light in EQUIPMENT}
        self.presses: dict[Light, int] = dict.fromkeys(EQUIPMENT, 0)
        self.runs: dict[asyncio.Task[Outcome], _Run] = {}

    def start(
        self, pool: asyncpg.Pool, setpoint_url: str, actor: Actor, written: WriteResult[settings.DesiredSetting]
    ) -> asyncio.Task[Outcome] | None:
        """Step 1, API-10's hook after its write is stored: ``resync`` for the Equipment page's Back to automatic write
        (a new write of an automatic switch with the value 1, ``manual`` and the reason ``BACK_TO_AUTOMATIC``). Any
        other write starts nothing."""
        write = written.value
        light = SWITCHES.get(write.param)
        if light is None or written.replayed or write.value != 1 or not write.manual:
            return None
        if write.reason != BACK_TO_AUTOMATIC:
            return None
        run = _Run(light, write)
        task = asyncio.create_task(self.resync(run, pool, setpoint_url, actor))
        self.runs[task] = run
        task.add_done_callback(self._forget)
        return task

    def _forget(self, task: asyncio.Task[Outcome]) -> None:
        self.runs.pop(task, None)

    async def resync(self, run: _Run, pool: asyncpg.Pool, setpoint_url: str, actor: Actor) -> Outcome:
        """Steps 2 to 4 for one stored write: what happened, in one log line. It raises nothing but its cancellation."""
        try:
            outcome, detail = await self._steps(run, pool, setpoint_url, actor)
        except asyncio.CancelledError:
            if run.sending:  # a stop that outlasted SHUTDOWN_S
                log.warning(
                    "Back to automatic: write %d's %s light command was cut off by the stop; its row says timeout",
                    run.write.id,
                    run.light,
                )
            else:
                log.warning(
                    "Back to automatic: write %d's %s resync was dropped because the operator API is stopping",
                    run.write.id,
                    run.light,
                )
            raise
        except Exception as failure:
            outcome, detail = "error", type(failure).__name__
        _report(run, outcome, detail)
        return outcome

    async def _steps(
        self, run: _Run, pool: asyncpg.Pool, setpoint_url: str, actor: Actor
    ) -> tuple[Outcome, str | None]:
        verdict, detail = await _until_confirmed(pool, run.write)
        if verdict != "confirmed":
            return verdict, detail
        # #1335: the light is automatic again, so the controller's hold from a timed switch ends now, not at its
        # deadline; the send below may find the light already matching and send nothing. Bounded like step 4's checks,
        # so a busy pool cannot hold the resync here.
        try:
            async with asyncio.timeout(DECIDE_S), pool.acquire() as conn:
                await lights.release_hold(conn, actor, run.light, BACK_TO_AUTOMATIC)
        except Exception as failure:
            log.warning(
                "Back to automatic: write %d could not release the %s light's controller hold (%s)",
                run.write.id,
                run.light,
                type(failure).__name__,
            )
        await asyncio.sleep(PAGE_FIRST_S)
        return await self._final(run, pool, setpoint_url, actor)

    async def _final(
        self, run: _Run, pool: asyncpg.Pool, setpoint_url: str, actor: Actor
    ) -> tuple[Outcome, str | None]:
        """Step 4: check, read, send and record as one step under the light's lock."""
        light, write = run.light, run.write
        lock = self.locks[light]
        if lock.locked():
            return "busy", None
        deadline = asyncio.get_running_loop().time() + DECIDE_S
        async with contextlib.AsyncExitStack() as held:
            try:
                async with asyncio.timeout_at(deadline):
                    conn = await held.enter_async_context(pool.acquire())
                    if not await _take(lock):
                        return "busy", None
                    held.callback(lock.release)
                    presses = self.presses[light]
                    if not await lights.try_lock(conn, light):
                        return "busy", "in the other operator API pod"
                    held.push_async_callback(_unlock, conn, light)
                    verdict, detail = await _verdict(conn, light, write)
                    if verdict not in ("on", "off"):
                        return verdict, detail
                    if self.presses[light] != presses:
                        return "pressed", None
            except TimeoutError:
                return "deadline", f"{DECIDE_S:g} s"
            run.sending = True
            return await _send(conn, setpoint_url, actor, light, verdict, write)

    @contextlib.asynccontextmanager
    async def commanding(self, conn: asyncpg.Connection, light: Light) -> AsyncIterator[None]:
        """API-20's side of the light's lock, around its call and its row. The press is counted first, so a resync
        still checking stands down. Then it waits up to ``LOCK_WAIT_S`` for this process's lock and for the advisory
        lock a resync in the other pod may hold, and goes ahead either way: a resync that still holds one then has its
        command at the single-threaded setpoint server already, so this press runs after it and wins."""
        self.presses[light] += 1
        deadline = asyncio.get_running_loop().time() + LOCK_WAIT_S
        lock = self.locks[light]
        held = advisory = False
        try:
            try:
                async with asyncio.timeout_at(deadline):
                    await lock.acquire()
                held = True
            except TimeoutError:
                log.warning("API-20 goes ahead while another command still holds the %s light's lock", light)
            advisory = await _advisory(conn, light, deadline)
            yield
        finally:
            if advisory:
                await _unlock(conn, light)
            if held:
                lock.release()

    async def stop(self) -> None:
        """When the operator API stops: every resync still waiting or checking is dropped, and a command on its way gets
        up to ``SHUTDOWN_S`` to finish and write its row; one still going after that is cancelled, its row saying
        timeout."""
        for task, run in list(self.runs.items()):
            if not run.sending:
                task.cancel()
        tasks = list(self.runs)
        if not tasks:
            return
        _done, late = await asyncio.wait(tasks, timeout=SHUTDOWN_S)
        for task in late:
            task.cancel()
        if late:
            await asyncio.wait(late, timeout=RECORD_S)


def start(app: FastAPI, actor: Actor, written: WriteResult[settings.DesiredSetting]) -> asyncio.Task[Outcome] | None:
    """API-10's hook: ``Resyncs.start`` with this app's pool and setpoint server."""
    resyncs: Resyncs = app.state.resyncs
    return resyncs.start(app.state.pool, app.state.settings.setpoint_server_url, actor, written)


def commanding(app: FastAPI, conn: asyncpg.Connection, light: Light) -> contextlib.AbstractAsyncContextManager[None]:
    """API-20's hook: ``Resyncs.commanding`` for this app."""
    resyncs: Resyncs = app.state.resyncs
    return resyncs.commanding(conn, light)
