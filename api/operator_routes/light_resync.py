"""Back to automatic, finished on the server (#1300): once API-10's write of ``sw_gl_main_auto_mode`` or
``sw_gl_grow_auto_mode`` = 1 confirms, match the Lutron light to the controller's intent. No routes and no SQL: it
reads through ``verdify_services`` and switches through ``setpoint_server.command``, as API-20 does.

The controller's ``apply_light`` switches a light only when its own decision changes, so a light switched by hand stays
where it is after its automatic switch comes back on, until that decision next changes. The Equipment page
(``useLightControl``) sends the one API-20 command that matches it, but only while the page stays open: on 2026-11-04
Back to automatic was pressed at 20:20:25Z with the main light ON and the intent OFF, the page moved to Graphs 5 s
later, and the light stayed ON until 21:04Z. So API-10 starts ``resync`` for such a write, and the page's own resync
stays the fast path:

1. Wait for the write to read CONFIRMED by API-09's rule (``settings.status``: it is still the switch's newest desired
   row, and its state is ``confirmed``), reading every 5 s as the page reads a REQUESTED row, for no longer than the
   page's window: the 5 min (``settings.REQUESTED_FOR``) the row reads REQUESTED before it reads NOT CONFIRMED. A
   newer write of the switch ends the wait; a failed read waits on.
2. Read the light's intent and Lutron state from API-13's read (``reads.equipment``). Nothing is sent unless both are
   known and differ (``resync_state``, the page's ``resyncState``).
3. Leave the page time to send its own command (``PAGE_FIRST_S``). Then, right before sending, stand down when a newer
   write of the switch exists, when a light command for that light was recorded since the write (a Turn on or Turn off,
   or the page's resync), or when Lutron now reads the intent; the command is the intent of that last read.
4. Send one command through ``setpoint_server.command`` under the original write's actor, with reason "Back to
   automatic": its ``activity_log`` row is API-20's. A call that fails is logged, and its row records the result, as
   API-20 records its 503.

The task lives only in this process, in ``app.state.background``, which the operator API cancels when it stops: an
operator API restart (a rollout, an eviction) while a resync waits loses it, and nothing then matches the light. The
page's row reads its "differ" then, and pressing Back to automatic again starts a new one.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Literal

import asyncpg
from fastapi import FastAPI

from operator_routes import setpoint_server
from verdify_services import activity, reads, settings
from verdify_services.activity import WriteResult
from verdify_services.actor import Actor
from verdify_services.lights import Light, LightState

log = logging.getLogger(__name__)

# The reason the page's write and its resync carry into Activity (the page's BACK_TO_AUTOMATIC).
BACK_TO_AUTOMATIC = "Back to automatic"
# Each grow light's automatic switch, and the light's API-13 item, which is also API-20's activity target (the page's
# LIGHTS).
SWITCHES: Mapping[str, Light] = MappingProxyType({"sw_gl_main_auto_mode": "main", "sw_gl_grow_auto_mode": "grow"})
EQUIPMENT: Mapping[Light, str] = MappingProxyType({"main": "grow_light_main", "grow": "grow_light_grow"})
POLL_S = 5.0  # the page reads a REQUESTED row every 5 s (READBACK_POLL_MS)
WINDOW_S = settings.REQUESTED_FOR.total_seconds()  # a write reads REQUESTED this long, then NOT CONFIRMED
# The page's resync lands within its next read of the row, its API-13 read and API-20's 12 s cap: wait that, plus a
# margin, before deciding, so the page goes first whenever it is open.
PAGE_FIRST_S = POLL_S + setpoint_server.TIMEOUT_S + 5.0

Outcome = Literal["sent", "failed", "matched", "superseded", "not_confirmed", "error"]


def resync_state(item: reads.EquipmentItem | None) -> LightState | None:
    """The one API-20 state Back to automatic sends, as the page's ``resyncState``: the controller's intent when it and
    the Lutron readback are both known and differ; else None."""
    if item is None or item.intent not in ("on", "off") or item.state not in ("on", "off"):
        return None
    return item.intent if item.intent != item.state else None


def _light_item(board: reads.Equipment, light: Light) -> reads.EquipmentItem | None:
    return next((item for item in board.items if item.equipment == EQUIPMENT[light]), None)


async def _until_confirmed(
    pool: asyncpg.Pool, write: settings.DesiredSetting
) -> tuple[Literal["confirmed", "superseded", "not_confirmed"], str | None]:
    """Step 1: the verdict, and the type of the last failed read, for the log. The window bounds the whole wait, a slow
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
    return "not_confirmed", failed


async def _superseded(conn: asyncpg.Connection, write: settings.DesiredSetting, light: Light) -> bool:
    """A newer write of the switch, or a light command for the light recorded since the write."""
    status = await settings.status(conn, write.param)
    if status.desired is None or status.desired.id != write.id:
        return True
    since = await activity.list(conn, action="light.", target=EQUIPMENT[light], from_=write.created_at, limit=1)
    return bool(since.items)


async def _resync(
    pool: asyncpg.Pool, setpoint_url: str, actor: Actor, light: Light, write: settings.DesiredSetting
) -> Outcome:
    verdict, failed = await _until_confirmed(pool, write)
    if verdict == "not_confirmed":
        last = f"; the last read failed with {failed}" if failed else ""
        log.warning(
            "Back to automatic: %s did not read confirmed within %d s, so the %s light was left as it was%s",
            write.param,
            WINDOW_S,
            light,
            last,
        )
    if verdict != "confirmed":
        return verdict
    async with pool.acquire() as conn:
        board = await reads.equipment(conn)
    if resync_state(_light_item(board, light)) is None:
        return "matched"
    await asyncio.sleep(PAGE_FIRST_S)
    async with pool.acquire() as conn:
        if await _superseded(conn, write, light):
            return "superseded"
        board = await reads.equipment(conn)
    state = resync_state(_light_item(board, light))
    if state is None:
        return "matched"
    async with pool.acquire() as conn:
        command = await setpoint_server.command(conn, actor, setpoint_url, light, state, BACK_TO_AUTOMATIC, None)
    if command.value.result != "confirmed":
        log.warning("Back to automatic: the %s light was not switched %s (%s)", light, state, command.value.result)
        return "failed"
    return "sent"


async def resync(
    pool: asyncpg.Pool, setpoint_url: str, actor: Actor, light: Light, write: settings.DesiredSetting
) -> Outcome:
    """Steps 1 to 4 for one stored write; it returns what happened and raises nothing but its cancellation."""
    try:
        return await _resync(pool, setpoint_url, actor, light, write)
    except asyncio.CancelledError:
        log.warning("Back to automatic: the %s resync was dropped because the operator API is stopping", light)
        raise
    except Exception as failure:
        log.warning("Back to automatic: the %s resync stopped on %s", light, type(failure).__name__)
        return "error"


def start(app: FastAPI, actor: Actor, written: WriteResult[settings.DesiredSetting]) -> asyncio.Task[Any] | None:
    """API-10's hook, after its write is stored: a new write (not a replay) of an automatic switch with the value 1
    starts ``resync`` in ``app.state.background``, which the operator API cancels when it stops. Any other write starts
    nothing."""
    write = written.value
    light = SWITCHES.get(write.param)
    if light is None or written.replayed or write.value != 1:
        return None
    task = asyncio.create_task(resync(app.state.pool, app.state.settings.setpoint_server_url, actor, light, write))
    background: set[asyncio.Task[Any]] = app.state.background
    background.add(task)
    task.add_done_callback(background.discard)
    return task
