"""tasks.irrigation — the ingestor's irrigation step, step 0: the L01 bridge (VD-64; irrigation §6.1 and §6.5).

The services (``verdify_services.irrigation``) only store ``irrigation_command`` rows; this pass is the only
code that turns them into device calls, through ``esp32_push`` under the writer fences. The task loop runs
``irrigation_sync`` every 5 s and force-launches it while ``shared.irrigation_sync_requested`` is set
(``NOTIFY irrigation_changed``). Step 0 reads pending commands on every pass, so a lost NOTIFY costs at most
5 s. #1157 adds steps 1-4 (compile, push and the engine commands) to the same pass.

1. With the irrigation engine on this connection (the ``irr_command`` and ``irr_program_begin`` services both
   offered), return: the bridge is for pre-engine firmware, including after a rollback to it.
2. Pending commands older than 2 min end ``failed`` ("too old") and are never sent. While the writer preflight
   reports a reason (no transport, no writer lease, device writes off, shadow mode), the pass marks nothing
   ``sent``: commands stay pending and expire, so a ``sent`` row always reached a live connection.
3. Stops first, oldest first, never heap-deferred: in one committed transaction mark the row ``sent`` with
   ``sent_at`` and ``args.relays_on`` (the wet relays in its scope that this connection has not read OFF: open
   ones, and in the state burst after a reconnect the ones not read yet) and mark every pending run in its scope
   ``failed`` ("stopped"), so a run requested just before a Stop is never pressed after it;
   then ``esp32_push.bridge_stop``, which records ``args.restore_irrig_enabled`` before it toggles
   ``sw_irrig_enabled``; ``args.restored`` once the switch is back.
4. Re-sends: for each Stop sent in the last 2 min, every relay in ``args.relays_on`` that has not read OFF since
   is switched off again; one that read OFF once is done, so VPD misting may pulse again. A Stop that recorded
   ``args.restore_irrig_enabled`` but not ``args.restored`` (a restart or a transport change cut its toggle
   short) gets ``sw_irrig_enabled`` set back to that value.
5. Runs, oldest first: while heap deferral holds (``dispatcher._heap_push_defer_active``: under 30 kB free or an
   18 kB largest block) they stay pending for the next pass. One on a line other than L01 cannot run without the
   engine and ends ``failed``. If ``drip_wall`` reads ON, or an earlier bridge run has not reached its planned end,
   it ends ``failed`` ("line_busy") and is never pressed: a press during a run queues a second full run.
   Otherwise it is marked ``sent`` and ``esp32_push.press_wall_clean`` presses "Wall Drips - RUN CLEAN"; a press
   that provably never reached the transport ends ``failed`` with its reason.

Relay edges in ``equipment_state``, never an ack, show whether water started or stopped. On every ESP32 connect
``record_engine_capability`` writes one ``system_state`` row, entity ``irr_engine``, ``true`` only when both
engine services exist; the services read it to choose this path.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg
import esp32_push
import shared
from pydantic import ValidationError

from verdify_schemas.irrigation import (
    BRIDGE_RUN_LINE,
    COMMAND_SEND_WINDOW_S,
    STOP_RESEND_WINDOW_S,
    IrrigationCommandRow,
)
from verdify_schemas.telemetry import SystemStateRow

from .dispatcher import _heap_push_defer_active

log = logging.getLogger("irrigation")

ENGINE_ENTITY = "irr_engine"
ENGINE_SERVICES = frozenset({"irr_command", "irr_program_begin"})
# Each wet line's relay (equipment_state name). L11's fog_rly has no switch to send, so a Stop on L11 alone
# is stored and closes nothing.
LINE_RELAYS: Mapping[str, str] = {
    "L01": "drip_wall",
    "L02": "drip_wall_fert",
    "L03": "fert_master_valve",
    "L04": "mister_center",
    "L05": "drip_center",
    "L06": "drip_center_fert",
    "L07": "mister_south",
    "L08": "mister_south_fert",
    "L09": "mister_west",
    "L10": "mister_west_fert",
}
RUN_RELAY = LINE_RELAYS[BRIDGE_RUN_LINE]
TOO_OLD = "too old"
STOPPED = "stopped"
LINE_BUSY = "line_busy"
NO_ENGINE = "arrives with the irrigation firmware"
SEND_WINDOW = timedelta(seconds=COMMAND_SEND_WINDOW_S)
RESEND_WINDOW = timedelta(seconds=STOP_RESEND_WINDOW_S)
# How far back a cut-short toggle is still restored, and how far back a sent run can still be watering
# (a bridge run lasts irrig_wall_duration_min, at most 120 min).
RESTORE_WINDOW = timedelta(days=1)
RUN_LOOKBACK = timedelta(hours=3)

_COLUMNS = (
    "command_id, greenhouse_id, kind, line_id, args::text AS args, status, fail_reason, source, actor, reason, "
    "created_at, sent_at"
)
_EXPIRED_SQL = (
    f"SELECT {_COLUMNS} FROM public.irrigation_command "
    "WHERE status = 'pending' AND created_at < now() - $1::interval ORDER BY created_at, command_id"
)
_PENDING_SQL = (
    f"SELECT {_COLUMNS} FROM public.irrigation_command "
    "WHERE status = 'pending' AND kind = $1 AND created_at >= now() - $2::interval ORDER BY created_at, command_id"
)
_FAIL_SQL = (
    "UPDATE public.irrigation_command SET status = 'failed', fail_reason = $2 "
    "WHERE command_id = ANY($1::uuid[]) AND status = $3"
)
_SEND_SQL = (
    "UPDATE public.irrigation_command SET status = 'sent', sent_at = $2, args = $3::text::jsonb "
    "WHERE command_id = $1 AND status = 'pending' AND created_at >= now() - $4::interval RETURNING command_id"
)
_RUNS_IN_SCOPE_SQL = (
    f"SELECT {_COLUMNS} FROM public.irrigation_command "
    "WHERE kind = 'run' AND status = 'pending' AND greenhouse_id = $1 AND ($2::text IS NULL OR line_id = $2) "
    "ORDER BY created_at, command_id"
)
_MERGE_ARGS_SQL = "UPDATE public.irrigation_command SET args = args || $2::text::jsonb WHERE command_id = $1"
_SENT_STOPS_SQL = (
    f"SELECT {_COLUMNS} FROM public.irrigation_command "
    "WHERE kind = 'stop' AND status = 'sent' AND sent_at > now() - $1::interval ORDER BY sent_at, command_id"
)
_SENT_RUNS_SQL = (
    f"SELECT {_COLUMNS} FROM public.irrigation_command "
    "WHERE kind = 'run' AND status = 'sent' AND line_id = $1 AND sent_at > now() - $2::interval"
)
# The dispatcher's heap guard inputs: the newest heap reading and whether a heap alert is open.
_HEAP_SQL = """
SELECT d.heap_bytes, d.heap_largest_free_block_kb,
       EXISTS (SELECT 1 FROM alert_log
                WHERE disposition = 'open'
                  AND alert_type IN ('heap_pressure_warning', 'heap_pressure_critical')) AS heap_alert_open
  FROM (SELECT 1) AS one
  LEFT JOIN LATERAL (SELECT heap_bytes, heap_largest_free_block_kb FROM diagnostics
                      WHERE heap_bytes IS NOT NULL ORDER BY ts DESC LIMIT 1) AS d ON true
"""
_ENGINE_ROW_SQL = "INSERT INTO v_runtime_system_state_write (ts, entity, value) VALUES ($1, $2, $3)"

_last_block_reason: str | None = None


def _json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _transition(row: IrrigationCommandRow, **changes: Any) -> IrrigationCommandRow:
    """The row after a status update, validated as an ``irrigation_command`` row before it is written."""
    return IrrigationCommandRow.model_validate({**row.model_dump(), **changes})


def _rows(records: Iterable[Any]) -> list[IrrigationCommandRow]:
    """The rows the model accepts. One it refuses is logged and dropped, like any row the ingestor cannot
    validate, so it can never keep a Stop from going out."""
    rows = []
    for record in records:
        try:
            rows.append(IrrigationCommandRow.from_row(record))
        except ValidationError as exc:
            problems = ",".join(str(error["loc"][0]) for error in exc.errors() if error["loc"])
            log.error("irrigation_command row %s dropped: invalid %s", record["command_id"], problems or "row")
    return rows


def engine_services_present(services: Iterable[str] | Mapping[str, object] | None) -> bool:
    """True when the connection offers both irrigation engine services (#1159's firmware)."""
    return ENGINE_SERVICES <= set(services or ())


def engine_on_connection() -> bool:
    """The current native-API connection's answer (the services cached on its connect)."""
    return engine_services_present(shared.esp32.get("services"))


async def record_engine_capability(pool: asyncpg.Pool, services: Iterable[str] | Mapping[str, object] | None) -> bool:
    """On every ESP32 connect: one ``system_state`` row, entity ``irr_engine``, ``true`` only when both engine
    services exist. The services read the newest row to choose the bridge, also after a firmware rollback."""
    engine = engine_services_present(services)
    row = SystemStateRow(ts=datetime.now(UTC), entity=ENGINE_ENTITY, value="true" if engine else "false")
    async with pool.acquire() as conn:
        await conn.execute(_ENGINE_ROW_SQL, row.ts, row.entity, row.value)
    log.info("irrigation engine on this connection: %s", row.value)
    return engine


def stop_scope(line_id: str | None) -> tuple[str, ...]:
    """The wet relays a Stop covers, in line order: every one for all lines, one for a line, none for L11."""
    if line_id is None:
        return tuple(shared.WET_RELAYS)
    relay = LINE_RELAYS.get(line_id)
    return () if relay is None else (relay,)


def _run_minutes(row: IrrigationCommandRow) -> int | None:
    minutes = row.args.get("minutes")
    return minutes if isinstance(minutes, int) and not isinstance(minutes, bool) else None


class CommandStore:
    """``irrigation_command`` reads and the ingestor's status updates, each on its own pooled connection.

    Every update is validated through ``IrrigationCommandRow`` first and guarded by the status it expects, so
    a row is marked ``sent`` at most once. The ingestor login holds SELECT on the table and UPDATE of
    ``status``, ``sent_at``, ``fail_reason`` and ``args`` (migration 283).
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def expire(self) -> list[IrrigationCommandRow]:
        """Pending commands older than 2 min: ``failed`` ("too old"), never sent. A row the model refuses
        expires too, so it stops being read."""
        async with self.pool.acquire() as conn:
            records = await conn.fetch(_EXPIRED_SQL, SEND_WINDOW)
            if records:
                await conn.execute(_FAIL_SQL, [record["command_id"] for record in records], TOO_OLD, "pending")
        return [_transition(row, status="failed", fail_reason=TOO_OLD) for row in _rows(records)]

    async def pending(self, kind: str) -> list[IrrigationCommandRow]:
        """Pending commands of one kind still inside the send window, oldest first."""
        async with self.pool.acquire() as conn:
            return _rows(await conn.fetch(_PENDING_SQL, kind, SEND_WINDOW))

    async def mark_stop_sent(self, stop: IrrigationCommandRow, sent_at: datetime, relays_on: list[str]) -> bool:
        """One transaction: the Stop ``sent`` with ``sent_at`` and ``args.relays_on``, and every pending run in
        its scope ``failed`` ("stopped"). False when the Stop is no longer pending."""
        sent = _transition(stop, status="sent", sent_at=sent_at, args={**stop.args, "relays_on": relays_on})
        async with self.pool.acquire() as conn, conn.transaction():
            if await conn.fetchval(_SEND_SQL, sent.command_id, sent_at, _json(sent.args), SEND_WINDOW) is None:
                return False
            runs = _rows(await conn.fetch(_RUNS_IN_SCOPE_SQL, stop.greenhouse_id, stop.line_id))
            stopped = [_transition(run, status="failed", fail_reason=STOPPED) for run in runs]
            if stopped:
                await conn.execute(_FAIL_SQL, [run.command_id for run in stopped], STOPPED, "pending")
        return True

    async def mark_run_sent(self, run: IrrigationCommandRow, sent_at: datetime) -> bool:
        """The run ``sent`` with ``sent_at``, committed before the press. False when it is no longer pending."""
        sent = _transition(run, status="sent", sent_at=sent_at)
        async with self.pool.acquire() as conn:
            return await conn.fetchval(_SEND_SQL, sent.command_id, sent_at, _json(sent.args), SEND_WINDOW) is not None

    async def mark_failed(self, row: IrrigationCommandRow, reason: str) -> None:
        """``failed`` with ``reason``, only from the status the row has now."""
        failed = _transition(row, status="failed", fail_reason=reason)
        async with self.pool.acquire() as conn:
            await conn.execute(_FAIL_SQL, [failed.command_id], reason, row.status)

    async def merge_args(self, row: IrrigationCommandRow, patch: Mapping[str, Any]) -> None:
        """Merge keys into the row's ``args`` (a Stop's ``restore_irrig_enabled`` and ``restored``)."""
        _transition(row, args={**row.args, **patch})
        async with self.pool.acquire() as conn:
            await conn.execute(_MERGE_ARGS_SQL, row.command_id, _json(patch))

    async def sent_stops(self, window: timedelta = RESTORE_WINDOW) -> list[IrrigationCommandRow]:
        async with self.pool.acquire() as conn:
            return _rows(await conn.fetch(_SENT_STOPS_SQL, window))

    async def sent_runs(self, line_id: str = BRIDGE_RUN_LINE) -> list[IrrigationCommandRow]:
        async with self.pool.acquire() as conn:
            return _rows(await conn.fetch(_SENT_RUNS_SQL, line_id, RUN_LOOKBACK))

    async def heap_deferred(self) -> bool:
        """The dispatcher's heap deferral, from the newest heap reading and the open heap alerts."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(_HEAP_SQL)
        free = None if row is None or row["heap_bytes"] is None else float(row["heap_bytes"])
        largest = (
            None
            if row is None or row["heap_largest_free_block_kb"] is None
            else float(row["heap_largest_free_block_kb"])
        )
        return _heap_push_defer_active(bool(row and row["heap_alert_open"]), free, largest)


def _note_blocked(reason: str | None) -> None:
    """Log the preflight block when it starts, changes or clears; not every 5 s."""
    global _last_block_reason
    if reason != _last_block_reason:
        if reason is None:
            log.info("irrigation bridge: writer preflight clear; commands can be sent")
        else:
            log.warning("irrigation bridge: nothing sent while the writer preflight reports %s", reason)
    _last_block_reason = reason


async def _send_stop(store: CommandStore, stop: IrrigationCommandRow) -> None:
    if esp32_push.bridge_preflight_failure() is not None:
        return  # the writer went away mid-pass: the Stop stays pending
    # sent_at first, then the relay snapshot, so any OFF read after the snapshot counts as read since sent_at.
    sent_at = datetime.now(UTC)
    live = shared.live_wet_relays()
    # Only a relay read OFF on this connection is left out: one not read yet (the state burst after a reconnect)
    # may be open, and OFF is harmless on a closed relay.
    relays_on = [relay for relay in stop_scope(stop.line_id) if live.get(relay) is not False]
    covers_l01 = stop.line_id in (None, BRIDGE_RUN_LINE)
    if not await store.mark_stop_sent(stop, sent_at, relays_on):
        return
    sent = _transition(stop, status="sent", sent_at=sent_at, args={**stop.args, "relays_on": relays_on})

    if not relays_on and not covers_l01:
        log.info("irrigation bridge stop command=%s line=%s: no open wet relay in scope", stop.command_id, stop.line_id)
        return

    async def record_restore(value: bool) -> None:
        await store.merge_args(sent, {"restore_irrig_enabled": value})

    outcome = await esp32_push.bridge_stop(relays_on, covers_l01, record_restore=record_restore)
    if outcome.restored:
        await store.merge_args(sent, {"restored": True})
    log.info(
        "irrigation bridge stop command=%s line=%s relays_on=%s sent=%s failure=%s toggled=%s restored=%s",
        stop.command_id,
        stop.line_id or "all",
        ",".join(relays_on) or "none",
        ",".join(outcome.sent) or "none",
        outcome.failure,
        outcome.toggled,
        outcome.restored,
    )


async def _resend_and_restore(store: CommandStore, skip: set[UUID | None]) -> None:
    now = datetime.now(UTC)
    for stop in await store.sent_stops():
        if stop.command_id in skip or stop.sent_at is None:
            continue
        if now - stop.sent_at <= RESEND_WINDOW:
            relays = [
                relay for relay in stop.args.get("relays_on") or () if relay in esp32_push.BRIDGE_RELAY_OBJECT_IDS
            ]
            waiting = [relay for relay in relays if not shared.wet_relay_read_off_since(relay, stop.sent_at)]
            if waiting:
                outcome = await esp32_push.bridge_switch_off(waiting)
                log.info(
                    "irrigation bridge stop re-send command=%s relays=%s sent=%s failure=%s",
                    stop.command_id,
                    ",".join(waiting),
                    ",".join(outcome.sent) or "none",
                    outcome.failure,
                )
        if "restore_irrig_enabled" in stop.args and not stop.args.get("restored"):
            value = bool(stop.args["restore_irrig_enabled"])
            if esp32_push.irrigation_enabled_reading() is value:
                restored = True
            else:
                outcome = await esp32_push.bridge_set_irrigation_enabled(value)
                restored = outcome.restored
                log.info(
                    "irrigation bridge stop restore command=%s sw_irrig_enabled=%s sent=%s failure=%s",
                    stop.command_id,
                    value,
                    ",".join(outcome.sent) or "none",
                    outcome.failure,
                )
            if restored:
                await store.merge_args(stop, {"restored": True})


async def _bridge_run_in_progress(store: CommandStore, now: datetime) -> bool:
    """An L01 bridge run sent earlier that has not reached its planned end (``sent_at`` plus its minutes) and
    that no later Stop covering L01 ended."""
    runs = [run for run in await store.sent_runs() if run.sent_at is not None and _run_minutes(run)]
    if not runs:
        return False
    stops = [stop for stop in await store.sent_stops() if stop.line_id in (None, BRIDGE_RUN_LINE)]
    for run in runs:
        if run.sent_at + timedelta(minutes=_run_minutes(run)) <= now:
            continue
        if any(stop.sent_at is not None and stop.sent_at >= run.sent_at for stop in stops):
            continue
        return True
    return False


async def _send_run(store: CommandStore, run: IrrigationCommandRow) -> None:
    if run.line_id != BRIDGE_RUN_LINE:
        await store.mark_failed(run, NO_ENGINE)
        return
    drip = shared.live_wet_relays().get(RUN_RELAY)
    if drip is None:
        log.info(
            "irrigation bridge run command=%s waits: %s not read on this connection yet", run.command_id, RUN_RELAY
        )
        return
    if drip or await _bridge_run_in_progress(store, datetime.now(UTC)):
        await store.mark_failed(run, LINE_BUSY)
        log.info("irrigation bridge run command=%s failed: L01 is already running", run.command_id)
        return
    if esp32_push.bridge_preflight_failure() is not None:
        return  # the writer went away mid-pass: the run stays pending
    sent_at = datetime.now(UTC)
    if not await store.mark_run_sent(run, sent_at):
        return
    outcome = await esp32_push.press_wall_clean()
    if outcome.failure is not None and outcome.unsent:
        await store.mark_failed(_transition(run, status="sent", sent_at=sent_at), outcome.failure)
    log.info(
        "irrigation bridge run command=%s minutes=%s sent=%s failure=%s",
        run.command_id,
        _run_minutes(run),
        ",".join(outcome.sent) or "none",
        outcome.failure,
    )


async def irrigation_sync(pool: asyncpg.Pool, *, store: CommandStore | None = None) -> None:
    """One pass of the irrigation step; step 0 (the L01 bridge) only while the engine is not on the controller."""
    if engine_on_connection():
        return
    store = store or CommandStore(pool)
    for row in await store.expire():
        log.info("irrigation bridge command=%s kind=%s failed: %s", row.command_id, row.kind, TOO_OLD)
    failure = esp32_push.bridge_preflight_failure()
    _note_blocked(failure)
    if failure is not None:
        return
    sent_now: set[UUID | None] = set()
    for stop in await store.pending("stop"):
        await _send_stop(store, stop)
        sent_now.add(stop.command_id)
    await _resend_and_restore(store, sent_now)
    runs = await store.pending("run")
    if not runs:
        return
    if await store.heap_deferred():
        log.info("irrigation bridge: heap deferral holds %d pending run(s) for the next pass", len(runs))
        return
    for run in runs:
        await _send_run(store, run)
