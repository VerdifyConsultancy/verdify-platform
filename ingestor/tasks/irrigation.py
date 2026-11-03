"""tasks.irrigation — the ingestor's irrigation step: the L01 bridge (VD-64) and the engine's steps (VD-42; irrigation
§6.1, §6.2 and §6.5).

The services (``verdify_services.irrigation``) only store ``irrigation_command`` rows and the program's tables; this
pass is the only code that turns them into device calls, through ``esp32_push`` under the writer fences. The task loop
runs ``irrigation_sync`` every 5 s, with no forced launch: every pass runs step 0 and the commands, so a lost NOTIFY
costs at most 5 s, and the program steps run when ``shared.irrigation_sync_requested`` is set (the pass clears it before
it reads) or 60 s after the last full pass.

Step 0 chooses by the services on the current connection. Without ``irr_command`` and ``irr_program_begin`` the pass logs
"irrigation engine not on controller" once per connection and only the bridge acts: no program, no ledger row and no
``irr_command``. ``record_engine_capability`` writes one ``system_state`` row, entity ``irr_engine``, on every ESP32
connect, ``true`` only when both services exist; the services read it to choose this path.

The bridge, for pre-engine firmware, including after a rollback to it:

1. Pending commands older than 2 min end ``failed`` ("too old") and are never sent. While the writer preflight
   reports a reason (no transport, no writer lease, device writes off, shadow mode), the pass marks nothing
   ``sent``: commands stay pending and expire, so a ``sent`` row always reached a live connection.
2. Stops first, oldest first, never heap-deferred: in one committed transaction mark the row ``sent`` with
   ``sent_at`` and ``args.relays_on`` (the wet relays in its scope that this connection has not read OFF: open
   ones, and in the state burst after a reconnect the ones not read yet) and mark every pending run in its scope
   ``failed`` ("stopped"), so a run requested just before a Stop is never pressed after it;
   then ``esp32_push.bridge_stop``, which records ``args.restore_irrig_enabled`` before it toggles
   ``sw_irrig_enabled``; ``args.restored`` once the switch is back.
3. Re-sends: for each Stop sent in the last 2 min, every relay in ``args.relays_on`` that has not read OFF since
   is switched off again; one that read OFF once is done, so VPD misting may pulse again. A Stop that recorded
   ``args.restore_irrig_enabled`` but not ``args.restored`` (a restart or a transport change cut its toggle
   short) gets ``sw_irrig_enabled`` set back to that value.
4. Runs, oldest first: while heap deferral holds (``dispatcher._heap_push_defer_active``: under 30 kB free or an
   18 kB largest block) they stay pending for the next pass. One on a line other than L01 cannot run without the
   engine and ends ``failed``. If ``drip_wall`` reads ON, or an earlier bridge run has not reached its planned end,
   it ends ``failed`` ("line_busy") and is never pressed: a press during a run queues a second full run.
   Otherwise it is marked ``sent`` and ``esp32_push.press_wall_clean`` presses "Wall Drips - RUN CLEAN"; a press
   that provably never reached the transport ends ``failed`` with its reason.

With the engine on the connection (``_engine_pass``):

1. Commands. Expiry and the preflight as above. Each Stop goes first as its own push request, never heap-deferred, with
   the same committed transaction before ``esp32_push.send_irrigation_command`` (it fails only the pending runs
   requested before it), and is re-sent each pass until its ``args.relays_on`` read OFF, for at most 2 min (or until a
   send returned, when the first one failed). Then ``run``, ``clear_fault`` and ``override``, oldest first, which wait
   out heap deferral and are each marked ``sent`` first, so each goes at most once.
2. Compile, in one read-only REPEATABLE READ snapshot (``read_program``): ``irrigation_line``, the enabled
   ``irrigation_slot`` rows and the three ``irrigation_site`` values, into VD-37's 584 B record and its SHA-256.
3. Push when the hash differs from ``irr_program_id``, no Stop is pending, heap deferral is off and this SHA-256 is not
   waiting out a reject back-off: ``irrigation_push`` records it first (a new row only when the hash differs from the
   newest row's; a re-push updates that row), then ``esp32_push.push_irrigation_program``. A reject (``j=``) goes to
   ``reject_code`` and the same program is retried 1, 5 and 15 min later, then every 15 min; a transport failure
   retries on the next pass.
4. Confirm: ``confirmed_at`` once ``irr_program_id`` reports the push's hash.

The ledger (``_sync_ledger``), inert until ``irr_program_id`` appears: one ``irrigation_ledger`` row per occurrence stage
or run-now from the ``irr_state`` run tokens (never per VPD pulse), closed from the line's relay edges when the token
leaves, and ``outcome``, ``reason`` and ``event_seq`` from ``irr_events``. Outcomes are never invented: one that aged out
of the ring stays null.

Relay edges in ``equipment_state``, never an ack, show whether water started or stopped.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg
import esp32_push
import shared
from pydantic import ValidationError

from verdify_schemas.irrigation import (
    BRIDGE_RUN_LINE,
    CLEAN_PARTNER,
    COMMAND_SEND_WINDOW_S,
    LINE_KIND,
    STOP_RESEND_WINDOW_S,
    WEEKDAYS,
    CompiledProgram,
    IrrEvent,
    IrrigationCause,
    IrrigationCommandRow,
    IrrigationLedgerRow,
    IrrigationPushRow,
    IrrRun,
    LineSpec,
    ProgramId,
    RunClose,
    RunOutcome,
    ScheduleSpec,
    compile_program,
    irr_state_reading,
    local_tz,
    occurrence_start,
    parse_irr_events,
    parse_irr_program_id,
    site_values,
)
from verdify_schemas.telemetry import SystemStateRow
from verdify_schemas.tunable_registry import IRRIGATION_SITE_PARAMS

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
    "AND ($3::timestamptz IS NULL OR created_at <= $3) ORDER BY created_at, command_id"
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

    async def mark_stop_sent(
        self, stop: IrrigationCommandRow, sent_at: datetime, relays_on: list[str], *, earlier_runs_only: bool = False
    ) -> bool:
        """One transaction: the Stop ``sent`` with ``sent_at`` and ``args.relays_on``, and every pending run in
        its scope ``failed`` ("stopped"): on the engine path only those requested before it, because a run requested
        after a Stop starts after it, as asked. False when the Stop is no longer pending."""
        sent = _transition(stop, status="sent", sent_at=sent_at, args={**stop.args, "relays_on": relays_on})
        async with self.pool.acquire() as conn, conn.transaction():
            if await conn.fetchval(_SEND_SQL, sent.command_id, sent_at, _json(sent.args), SEND_WINDOW) is None:
                return False
            before = stop.created_at if earlier_runs_only else None
            runs = _rows(await conn.fetch(_RUNS_IN_SCOPE_SQL, stop.greenhouse_id, stop.line_id, before))
            stopped = [_transition(run, status="failed", fail_reason=STOPPED) for run in runs]
            if stopped:
                await conn.execute(_FAIL_SQL, [run.command_id for run in stopped], STOPPED, "pending")
        return True

    async def mark_run_sent(self, run: IrrigationCommandRow, sent_at: datetime) -> bool:
        """The command ``sent`` with ``sent_at``, committed before the device call. False when it is no longer
        pending."""
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
            log.info("irrigation commands: writer preflight clear; commands can be sent")
        else:
            log.warning("irrigation commands: nothing sent while the writer preflight reports %s", reason)
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


async def _bridge_pass(store: CommandStore) -> None:
    """Step 0's other half: the L01 bridge, while the engine is not on the controller."""
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


# ── Commands with the engine (irrigation §6.1, step 1) ───────────────────────

# Every command but a Stop waits out heap deferral, oldest first across the kinds.
ENGINE_COMMAND_KINDS = ("run", "clear_fault", "override")
BAD_ARGS = "bad_args"


async def _send_engine_stop(store: CommandStore, stop: IrrigationCommandRow, memory: EngineMemory) -> None:
    if esp32_push.bridge_preflight_failure() is not None:
        return  # the writer went away mid-pass: the Stop stays pending
    # sent_at first, then the relay snapshot, so any OFF read after the snapshot counts as read since sent_at.
    sent_at = datetime.now(UTC)
    live = shared.live_wet_relays()
    relays_on = [relay for relay in stop_scope(stop.line_id) if live.get(relay) is not False]
    if not await store.mark_stop_sent(stop, sent_at, relays_on, earlier_runs_only=True):
        return
    sent = _transition(stop, status="sent", sent_at=sent_at, args={**stop.args, "relays_on": relays_on})
    # A Stop goes even with no open relay: a mist window between pulses and a run waiting to open have none.
    result = await esp32_push.send_irrigation_command(sent)
    if not result.ok:
        memory.undelivered_stops.add(stop.command_id)
    log.info(
        "irrigation stop command=%s line=%s relays_on=%s ok=%s failure=%s",
        stop.command_id,
        stop.line_id or "all",
        ",".join(relays_on) or "none",
        result.ok,
        result.failure.reason if result.failure else None,
    )


async def _resend_engine_stops(store: CommandStore, skip: set[UUID | None], memory: EngineMemory) -> None:
    """Each Stop sent in the last 2 min goes again until its relays have read OFF since it was sent (one that read OFF
    once is done, so VPD misting may pulse again), and until a send has returned when the first one failed."""
    stops = await store.sent_stops(RESEND_WINDOW)
    memory.undelivered_stops &= {stop.command_id for stop in stops}
    for stop in stops:
        if stop.command_id in skip or stop.sent_at is None:
            continue
        relays = stop.args.get("relays_on") or ()
        waiting = [relay for relay in relays if not shared.wet_relay_read_off_since(relay, stop.sent_at)]
        if not waiting and stop.command_id not in memory.undelivered_stops:
            continue
        result = await esp32_push.send_irrigation_command(stop)
        if result.ok:
            memory.undelivered_stops.discard(stop.command_id)
        log.info(
            "irrigation stop re-send command=%s line=%s waiting=%s ok=%s failure=%s",
            stop.command_id,
            stop.line_id or "all",
            ",".join(waiting) or "none",
            result.ok,
            result.failure.reason if result.failure else None,
        )


async def _send_engine_command(store: CommandStore, row: IrrigationCommandRow) -> None:
    """A run, clear-fault or override: marked ``sent`` first, so it goes at most once and never late."""
    try:
        esp32_push.irrigation_command_call(row)
    except ValueError as exc:
        log.error("irrigation command=%s kind=%s failed: %s", row.command_id, row.kind, exc)
        await store.mark_failed(row, BAD_ARGS)
        return
    if esp32_push.bridge_preflight_failure() is not None:
        return  # the writer went away mid-pass: the command stays pending
    sent_at = datetime.now(UTC)
    if not await store.mark_run_sent(row, sent_at):
        return
    sent = _transition(row, status="sent", sent_at=sent_at)
    result = await esp32_push.send_irrigation_command(sent)
    failure = result.failure.reason if result.failure else None
    if esp32_push.policy_call_unsent(result):
        await store.mark_failed(sent, failure)
    log.info(
        "irrigation command=%s kind=%s line=%s ok=%s failure=%s",
        row.command_id,
        row.kind,
        row.line_id or "all",
        result.ok,
        failure,
    )


async def _send_engine_commands(store: CommandStore, memory: EngineMemory) -> None:
    sent_now: set[UUID | None] = set()
    for stop in await store.pending("stop"):
        await _send_engine_stop(store, stop, memory)
        sent_now.add(stop.command_id)
    await _resend_engine_stops(store, sent_now, memory)
    if shared.IRR_PROGRAM_ID not in shared.irrigation_readback:
        return  # the engine ignores them until its first tick has started it: they wait, or expire
    rows = [row for kind in ENGINE_COMMAND_KINDS for row in await store.pending(kind)]
    if not rows:
        return
    if await store.heap_deferred():
        log.info("irrigation: heap deferral holds %d pending command(s) for the next pass", len(rows))
        return
    for row in sorted(rows, key=lambda row: row.created_at or datetime.min.replace(tzinfo=UTC)):
        await _send_engine_command(store, row)


# ── The program: compile, push, confirm (irrigation §6.1, steps 2-4) ─────────

GREENHOUSE_ID = "vallery"
FULL_PASS_INTERVAL_S = 60.0
# A commit's reject shows at once and its hash at the engine's next 5 s tick, so an old j= still showing this long after
# the commit went out is this push's. A push with neither by the later mark is pushed again by the next full pass.
REJECT_SETTLE_S = 6.0
PUSH_SETTLE_S = 30.0
# The delay before each retry of a rejected program; the last one repeats.
REJECT_RETRY_S = (60.0, 300.0, 900.0)

_LINES_SQL = (
    "SELECT line_id, kind, device_idx, clean_partner, reserved, enabled, vpd_member, schedule_member, "
    "daily_cap_min, vpd_reserve_min, paused_until, manual "
    "FROM public.irrigation_line WHERE greenhouse_id = $1 ORDER BY device_idx"
)
_SLOT_COLUMNS = (
    "slot_id, line_id, enabled, days, start_kind, start_min, on_s, count, gap_s, prewet_s, flush_s, degraded_only, "
    "label, manual"
)
_ENABLED_SLOTS_SQL = (
    f"SELECT {_SLOT_COLUMNS} FROM public.irrigation_slot WHERE greenhouse_id = $1 AND enabled ORDER BY slot_id"
)
_SITE_SQL = (
    "SELECT id, param, value, created_at FROM public.v_desired_state_current "
    "WHERE greenhouse_id = $1 AND param = ANY($2::text[])"
)
_NEWEST_PUSH_SQL = (
    "SELECT revision, sha256 FROM public.irrigation_push WHERE greenhouse_id = $1 ORDER BY revision DESC LIMIT 1"
)
_PUSH_OF_HASH_SQL = (
    "SELECT revision, confirmed_at FROM public.irrigation_push "
    "WHERE greenhouse_id = $1 AND left(sha256, 16) = $2 ORDER BY revision DESC LIMIT 1"
)
_INSERT_PUSH_SQL = (
    "INSERT INTO public.irrigation_push (greenhouse_id, sha256, bytes, site_desired_at, pushed_at) "
    "VALUES ($1, $2, $3, $4, $5) RETURNING revision"
)
# The controller is not running this hash when it is pushed again, so the row is unconfirmed until it reports it.
_REPUSH_SQL = (
    "UPDATE public.irrigation_push SET pushed_at = $2, reject_code = NULL, confirmed_at = NULL WHERE revision = $1"
)
_CONFIRM_PUSH_SQL = "UPDATE public.irrigation_push SET confirmed_at = $2 WHERE revision = $1 AND confirmed_at IS NULL"
_REJECT_PUSH_SQL = "UPDATE public.irrigation_push SET reject_code = $2 WHERE revision = $1"


@dataclass(frozen=True)
class ProgramSnapshot:
    """One compile's inputs and result, from one read-only snapshot. ``site_desired_at`` is the newest ``created_at``
    among the site rows."""

    lines: tuple[LineSpec, ...]
    slots: tuple[ScheduleSpec, ...]
    compiled: CompiledProgram
    site_desired_at: datetime | None


def _schedule_specs(rows: Iterable[Any]) -> tuple[ScheduleSpec, ...]:
    """The rows the model accepts. One it refuses is logged and left off the program, so it never blocks the others."""
    specs = []
    for row in rows:
        try:
            specs.append(ScheduleSpec.from_row(row))
        except (ValidationError, ValueError) as exc:
            log.error("irrigation_slot %s left off the program: %s", row["slot_id"], type(exc).__name__)
    return tuple(specs)


async def read_program(conn: asyncpg.Connection) -> ProgramSnapshot:
    """The lines, the enabled schedules and the site values in one read-only REPEATABLE READ transaction, compiled.
    A value with no row, or a released one, falls back to its registry default (``site_values``)."""
    async with conn.transaction(isolation="repeatable_read", readonly=True):
        line_rows = await conn.fetch(_LINES_SQL, GREENHOUSE_ID)
        slot_rows = await conn.fetch(_ENABLED_SLOTS_SQL, GREENHOUSE_ID)
        site_rows = await conn.fetch(_SITE_SQL, GREENHOUSE_ID, sorted(IRRIGATION_SITE_PARAMS))
    lines = tuple(LineSpec.from_row(row) for row in line_rows)
    slots = _schedule_specs(slot_rows)
    return ProgramSnapshot(
        lines=lines,
        slots=slots,
        compiled=compile_program(lines, slots, site_values(site_rows)),
        site_desired_at=max((row["created_at"] for row in site_rows), default=None),
    )


class ProgramStore:
    """The compile's read and ``irrigation_push``. The ingestor login holds INSERT on the table and UPDATE of
    ``pushed_at``, ``confirmed_at`` and ``reject_code`` (migration 283)."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def snapshot(self) -> ProgramSnapshot:
        async with self.pool.acquire() as conn:
            return await read_program(conn)

    async def push_of_hash(self, hash16: str) -> Mapping[str, Any] | None:
        """The newest push of the hash the controller reports, which need not be the newest push."""
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(_PUSH_OF_HASH_SQL, GREENHOUSE_ID, hash16)

    async def record_push(self, snapshot: ProgramSnapshot, pushed_at: datetime) -> int:
        """Just before the device call: a new row only when the SHA-256 differs from the newest row's; a re-push of the
        same hash (a retry, or a controller back on ``src=rom``) updates that row. Returns its revision."""
        compiled = snapshot.compiled
        async with self.pool.acquire() as conn, conn.transaction():
            newest = await conn.fetchrow(_NEWEST_PUSH_SQL, GREENHOUSE_ID)
            if newest is not None and newest["sha256"] == compiled.sha256:
                await conn.execute(_REPUSH_SQL, newest["revision"], pushed_at)
                return newest["revision"]
            row = IrrigationPushRow(
                sha256=compiled.sha256,
                bytes=compiled.record,
                site_desired_at=snapshot.site_desired_at,
                pushed_at=pushed_at,
            )
            return await conn.fetchval(
                _INSERT_PUSH_SQL, row.greenhouse_id, row.sha256, row.bytes, row.site_desired_at, row.pushed_at
            )

    async def confirm(self, revision: int, confirmed_at: datetime) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(_CONFIRM_PUSH_SQL, revision, confirmed_at)

    async def record_reject(self, revision: int, code: str) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(_REJECT_PUSH_SQL, revision, code)


def _program_id_reading() -> ProgramId | None:
    """This connection's ``irr_program_id``, with no reading or text outside the grammar treated as none."""
    text = shared.irrigation_readback.get(shared.IRR_PROGRAM_ID)
    if text is None:
        return None
    try:
        return parse_irr_program_id(text)
    except ValueError:
        return None


def _push_reject() -> str | None:
    state = irr_state_reading(shared.irrigation_readback.get(shared.IRR_STATE))
    return None if state is None else state.reject


async def _settle_attempt(program: ProgramStore, memory: EngineMemory, reading: ProgramId) -> None:
    """Step 4 and the reject check of the last push: every pass, from memory, with no query until there is an answer."""
    attempt = memory.attempt
    if attempt is None:
        return
    if attempt.generation != shared.transport_generation:
        memory.attempt = None  # a new connection: the next full pass compares the hash it reports
        return
    now = time.monotonic()
    if attempt.sha256.startswith(reading.hash16):
        await program.confirm(attempt.revision, datetime.now(UTC))
        memory.attempt = None
        memory.backoff = None
        log.info("irrigation program confirmed revision=%s hash=%s", attempt.revision, reading.hash16)
        return
    if now - attempt.sent_at < REJECT_SETTLE_S:
        return
    code = _push_reject()
    if code is None:
        if now - attempt.sent_at >= PUSH_SETTLE_S:
            memory.attempt = None
        return
    await program.record_reject(attempt.revision, code)
    backoff = memory.backoff
    stage = backoff.stage + 1 if backoff is not None and backoff.sha256 == attempt.sha256 else 1
    delay = REJECT_RETRY_S[min(stage, len(REJECT_RETRY_S)) - 1]
    memory.backoff = RejectBackoff(attempt.sha256, stage, now + delay)
    memory.attempt = None
    log.warning(
        "irrigation program rejected revision=%s reject=%s stage=%d retry_in_s=%.0f",
        attempt.revision,
        code,
        stage,
        delay,
    )


def _full_pass_due(memory: EngineMemory) -> bool:
    """On the wake flag, for a new connection, 60 s after the last full pass, or when a reject's retry falls due."""
    if shared.irrigation_sync_requested.is_set() or memory.full_generation != shared.transport_generation:
        return True
    now = time.monotonic()
    if memory.last_full_at is None or now - memory.last_full_at >= FULL_PASS_INTERVAL_S:
        return True
    backoff = memory.backoff
    return backoff is not None and memory.attempt is None and memory.last_full_at < backoff.next_at <= now


async def _push_program(
    store: CommandStore, program: ProgramStore, memory: EngineMemory, snapshot: ProgramSnapshot
) -> None:
    """Step 3, once the compiled hash differs from the controller's."""
    compiled = snapshot.compiled
    if memory.attempt is not None and memory.attempt.sha256 == compiled.sha256:
        return  # this push is still waiting for its readback
    backoff = memory.backoff
    if backoff is not None and backoff.sha256 == compiled.sha256 and time.monotonic() < backoff.next_at:
        return
    if esp32_push.bridge_preflight_failure() is not None:
        return
    if await store.pending("stop"):
        shared.irrigation_sync_requested.set()  # the next pass, once the Stop has gone
        return
    if await store.heap_deferred():
        log.info("irrigation: heap deferral holds the program push")
        return
    if backoff is not None and backoff.sha256 != compiled.sha256:
        memory.backoff = None  # a newly edited program goes at once
    revision = await program.record_push(snapshot, datetime.now(UTC))
    result = await esp32_push.push_irrigation_program(compiled.record, compiled.sha256)
    if result.ok:
        memory.attempt = PushAttempt(compiled.sha256, revision, shared.transport_generation, time.monotonic())
    else:
        memory.attempt = None
        shared.irrigation_sync_requested.set()  # a transport failure retries on the next pass
    log.info(
        "irrigation program push revision=%s hash=%s ok=%s failure=%s",
        revision,
        compiled.hash16,
        result.ok,
        result.failure.reason if result.failure else None,
    )


def _note_left_off(memory: EngineMemory, compiled: CompiledProgram) -> None:
    if compiled.not_on_controller != memory.not_on_controller:
        memory.not_on_controller = compiled.not_on_controller
        if compiled.not_on_controller:
            log.warning("irrigation schedules not on the controller: %s", list(compiled.not_on_controller))


async def _program_steps(store: CommandStore, program: ProgramStore, memory: EngineMemory) -> None:
    """Steps 2-4, inert until ``irr_program_id`` appears on this connection. The flag stays set until then, so the first
    reading is compared at once."""
    reading = _program_id_reading()
    if reading is None:
        return
    await _settle_attempt(program, memory, reading)
    if not _full_pass_due(memory):
        return
    shared.irrigation_sync_requested.clear()  # before the read, so a write during it wakes the next pass
    memory.last_full_at = time.monotonic()
    memory.full_generation = shared.transport_generation
    snapshot = await program.snapshot()
    _note_left_off(memory, snapshot.compiled)
    # By the reported hash: an unconfirmed push the controller runs would leave the fence alert checking nothing.
    reported = await program.push_of_hash(reading.hash16)
    if reported is not None and reported["confirmed_at"] is None:
        await program.confirm(reported["revision"], datetime.now(UTC))
    if snapshot.compiled.hash16 == reading.hash16:
        memory.attempt = None
        memory.backoff = None
        return
    await _push_program(store, program, memory, snapshot)


# ── The ledger (irrigation §6.1 and §6.2) ────────────────────────────────────

# A token gone this long closes its row. It leaves just before the relay closes, and equipment_state is flushed every 5 s.
LEDGER_CLOSE_AFTER = timedelta(seconds=15)
# How long a closing row waits for an OFF edge the flush has not written yet.
LEDGER_EDGE_WAIT = timedelta(seconds=120)
# M3's bound: no occurrence lasts 24 h, and its join ignores a row older than that.
LEDGER_OPEN_WINDOW = timedelta(hours=24)
# A start reads a minute later here than on the controller at most; this absorbs that when finding an occurrence's day.
OCCURRENCE_DAY_SLACK = timedelta(minutes=5)
# A run-now opens at once; a later send on the line is not this run's command.
RUN_COMMAND_SLACK = timedelta(seconds=30)
_FERT_LINE_OF_PARTNER: Mapping[str, str] = {partner: fert for fert, partner in CLEAN_PARTNER.items()}
_RINSE_CAUSES = frozenset({IrrigationCause.PREWET.value, IrrigationCause.FLUSH.value})
# Outcomes that never opened a valve, so a run-now's has a row of its own.
_UNOPENED_OUTCOMES = frozenset({RunOutcome.SKIPPED, RunOutcome.MISSED})

_LEDGER_OPEN_ROWS_SQL = (
    "SELECT id, line_id, slot_id, cause, started_at FROM public.irrigation_ledger "
    "WHERE greenhouse_id = $1 AND ended_at IS NULL AND started_at > $2"
)
_SLOT_SQL = f"SELECT {_SLOT_COLUMNS} FROM public.irrigation_slot WHERE greenhouse_id = $1 AND slot_id = $2"
_SLOT_ROW_SQL = (
    "SELECT id FROM public.irrigation_ledger "
    "WHERE greenhouse_id = $1 AND slot_id = $2 AND local_day = $3 AND cause = $4"
)
_RUN_NOW_ROW_SQL = (
    "SELECT id FROM public.irrigation_ledger "
    "WHERE greenhouse_id = $1 AND slot_id IS NULL AND line_id = $2 AND cause = $3 AND started_at = $4"
)
_RUN_COMMAND_SQL = (
    "SELECT command_id, actor FROM public.irrigation_command "
    "WHERE greenhouse_id = $1 AND kind = 'run' AND status = 'sent' AND line_id = ANY($2::text[]) "
    "AND sent_at > $3 AND sent_at <= $4 ORDER BY sent_at DESC LIMIT 1"
)
_SLOT_OUTCOME_SQL = (
    "SELECT outcome, reason, event_seq FROM public.irrigation_ledger "
    "WHERE greenhouse_id = $1 AND slot_id = $2 AND local_day = $3 AND outcome IS NOT NULL LIMIT 1"
)
_COMMAND_OUTCOME_SQL = (
    "SELECT outcome, reason, event_seq FROM public.irrigation_ledger "
    "WHERE greenhouse_id = $1 AND command_id = $2 AND outcome IS NOT NULL LIMIT 1"
)
_INSERT_LEDGER_SQL = (
    "INSERT INTO public.irrigation_ledger (greenhouse_id, line_id, slot_id, local_day, cause, started_at, open_s, "
    "outcome, reason, event_seq, command_id, actor) "
    "VALUES ($1, $2, $3, $4, $5, $6, 0, $7, $8, $9, $10, $11) "
    "ON CONFLICT (greenhouse_id, slot_id, local_day, cause) WHERE slot_id IS NOT NULL DO NOTHING RETURNING id"
)
_REOPEN_LEDGER_SQL = "UPDATE public.irrigation_ledger SET ended_at = NULL WHERE id = $1 AND ended_at IS NOT NULL"
_CLOSE_LEDGER_SQL = "UPDATE public.irrigation_ledger SET ended_at = $2, open_s = $3, pulses = $4 WHERE id = $1"
_RELAY_SEED_SQL = (
    "SELECT bool_or(state) FROM public.equipment_state "
    "WHERE equipment = $1 AND COALESCE(greenhouse_id, 'vallery') = $2 AND ts < $3 AND ts > $3 - interval '24 hours' "
    "GROUP BY ts ORDER BY ts DESC LIMIT 1"
)
_RELAY_EDGES_SQL = (
    "SELECT ts, bool_or(state) AS state FROM public.equipment_state "
    "WHERE equipment = $1 AND COALESCE(greenhouse_id, 'vallery') = $2 AND ts >= $3 AND ts <= $4 "
    "GROUP BY ts ORDER BY ts"
)
_STORED_EVENTS_SQL = (
    "SELECT event_seq, slot_id, local_day FROM public.irrigation_ledger "
    "WHERE greenhouse_id = $1 AND event_seq = ANY($2::integer[])"
)
_OCCURRENCE_ROWS_SQL = (
    "SELECT count(*) FROM public.irrigation_ledger WHERE greenhouse_id = $1 AND slot_id = $2 AND local_day = $3"
)
_SLOT_DEGRADED_SQL = "SELECT degraded_only FROM public.irrigation_slot WHERE greenhouse_id = $1 AND slot_id = $2"
_OCCURRENCE_OUTCOME_SQL = (
    "UPDATE public.irrigation_ledger SET outcome = $4, reason = $5, event_seq = $6 "
    "WHERE greenhouse_id = $1 AND slot_id = $2 AND local_day = $3 AND outcome IS NULL"
)
# A run-now's row by line: the newest on this local day with no outcome that opened a valve. A fertilizer run-now's
# feed is on its own line and its prewet or flush on the clean partner.
_RUN_NOW_CANDIDATE_SQL = (
    "SELECT id, command_id FROM public.irrigation_ledger "
    "WHERE greenhouse_id = $1 AND slot_id IS NULL AND outcome IS NULL AND local_day = $2 AND started_at IS NOT NULL "
    "AND ((line_id = $3 AND cause = $4) OR (line_id = $5 AND cause = ANY($6::text[]))) "
    "AND NOT (id = ANY($7::bigint[])) ORDER BY started_at DESC, id DESC LIMIT 1"
)
_ROW_OUTCOME_SQL = "UPDATE public.irrigation_ledger SET outcome = $2, reason = $3, event_seq = $4 WHERE id = $1"
_COMMAND_ROWS_OUTCOME_SQL = (
    "UPDATE public.irrigation_ledger SET outcome = $3, reason = $4, event_seq = $5 "
    "WHERE greenhouse_id = $1 AND command_id = $2 AND outcome IS NULL"
)


def occurrence_day(spec: ScheduleSpec | None, started_at: datetime) -> date:
    """The local day a slot's run belongs to, the day ``irr_events`` names: its weekday matched that day, although a
    solar start past midnight or a late start can open the next morning. With its schedule gone, the day it opened."""
    opened = started_at.astimezone(local_tz()).date()
    if spec is not None:
        for day in (opened, opened - timedelta(days=1)):
            if WEEKDAYS[day.weekday()] in spec.days and occurrence_start(spec.start, day) <= (
                started_at + OCCURRENCE_DAY_SLACK
            ):
                return day
    return opened


def _run_started_at(run: IrrRun, observed_at: datetime) -> datetime:
    """A token's start; with no valid clock on the controller it reads 0, and when the ingestor saw it stands in."""
    return datetime.fromtimestamp(run.start_epoch, UTC) if run.start_epoch else observed_at


def _occurrence_cause(line_id: str, degraded_only: bool) -> IrrigationCause:
    """The cause of a slot's outcome with no row: it never opened a valve, so no token named it."""
    if LINE_KIND[line_id] in ("fert_drip", "fert_mister"):
        return IrrigationCause.FEED
    return IrrigationCause.FALLBACK if degraded_only else IrrigationCause.SCHEDULE


def run_close(
    edges: Sequence[tuple[datetime, bool]],
    *,
    started_at: datetime,
    left_at: datetime,
    planned_end: datetime | None,
    seed_on: bool,
    watched: bool,
    now: datetime,
) -> RunClose | None:
    """``ended_at``, ``open_s`` and ``pulses`` from the line's relay edges since ``started_at``, or None while the OFF
    edge may still be on its way.

    ``ended_at`` is the last OFF edge, or the token's planned end (never past when the token left) if the ingestor missed
    the edge. It missed it when the token left across a reconnect (``watched`` false): the reconnect's own relay row is
    not an edge. ``seed_on`` is the relay's state before ``started_at``. An ON edge after the token left is another run's.
    """
    known_end = None if planned_end is None else min(planned_end, left_at)
    on_since = started_at if seed_on else None
    spans: list[tuple[datetime, datetime]] = []
    for ts, on in edges:
        if on:
            if on_since is None and ts <= left_at:
                on_since = ts
        elif on_since is not None:
            spans.append((on_since, ts))
            on_since = None
    if watched or known_end is None:
        if on_since is not None and now - left_at < LEDGER_EDGE_WAIT:
            return None
        ended = spans[-1][1] if on_since is None and spans else (known_end or left_at)
    else:
        ended = known_end
    ended = max(ended, started_at)
    if on_since is not None:
        spans.append((on_since, ended))
    open_seconds = [(min(off, ended) - on).total_seconds() for on, off in spans]
    return RunClose(
        ended_at=ended,
        open_s=round(sum(seconds for seconds in open_seconds if seconds > 0)),
        pulses=sum(seconds > 0 for seconds in open_seconds),
    )


class LedgerStore:
    """``irrigation_ledger``, written only by the ingestor: INSERT, and UPDATE of ``ended_at``, ``open_s``, ``pulses``,
    ``outcome``, ``reason`` and ``event_seq`` (migration 283)."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def open_rows(self, now: datetime) -> list[Any]:
        """Rows left open, as a restart finds them."""
        async with self.pool.acquire() as conn:
            return await conn.fetch(_LEDGER_OPEN_ROWS_SQL, GREENHOUSE_ID, now - LEDGER_OPEN_WINDOW)

    async def _insert(self, conn: Any, row: IrrigationLedgerRow) -> int | None:
        """One row, validated first. None when the occurrence stage already has one."""
        return await conn.fetchval(
            _INSERT_LEDGER_SQL,
            row.greenhouse_id,
            row.line_id,
            row.slot_id,
            row.local_day,
            row.cause.value,
            row.started_at,
            None if row.outcome is None else row.outcome.value,
            row.reason,
            row.event_seq,
            row.command_id,
            row.actor,
        )

    async def _run_command(self, conn: Any, line_ids: list[str], at: datetime) -> Any:
        return await conn.fetchrow(
            _RUN_COMMAND_SQL, GREENHOUSE_ID, line_ids, at - LEDGER_OPEN_WINDOW, at + RUN_COMMAND_SLACK
        )

    async def record_start(self, run: IrrRun, observed_at: datetime) -> int:
        """A new run token's row, or the row it already has: a token back after a block, a VPD pulse or a restart with
        its row closed is the same run, so the row opens again. A fertilizer stage's flush starts after the outcome,
        so it takes its occurrence's (a run-now's: its command's)."""
        started_at = _run_started_at(run, observed_at)
        cause = run.cause.value
        async with self.pool.acquire() as conn, conn.transaction():
            command = None
            if run.slot_id is None:
                row_id = await conn.fetchval(_RUN_NOW_ROW_SQL, GREENHOUSE_ID, run.line_id, cause, started_at)
                local_day = started_at.astimezone(local_tz()).date()
                if row_id is None:
                    # A prewet or flush names the clean partner; its command is on the fertilizer line.
                    line = (
                        _FERT_LINE_OF_PARTNER.get(run.line_id, run.line_id) if cause in _RINSE_CAUSES else run.line_id
                    )
                    command = await self._run_command(conn, [line], started_at)
            else:
                slot = await conn.fetchrow(_SLOT_SQL, GREENHOUSE_ID, run.slot_id)
                spec = None if slot is None else _schedule_specs([slot])
                local_day = occurrence_day(spec[0] if spec else None, started_at)
                row_id = await conn.fetchval(_SLOT_ROW_SQL, GREENHOUSE_ID, run.slot_id, local_day, cause)
            if row_id is not None:
                await conn.execute(_REOPEN_LEDGER_SQL, row_id)
                return row_id
            settled = None
            if run.cause is IrrigationCause.FLUSH:
                if run.slot_id is not None:
                    settled = await conn.fetchrow(_SLOT_OUTCOME_SQL, GREENHOUSE_ID, run.slot_id, local_day)
                elif command is not None:
                    settled = await conn.fetchrow(_COMMAND_OUTCOME_SQL, GREENHOUSE_ID, command["command_id"])
            return await self._insert(
                conn,
                IrrigationLedgerRow(
                    line_id=run.line_id,
                    slot_id=run.slot_id,
                    local_day=local_day,
                    cause=run.cause,
                    started_at=started_at,
                    outcome=None if settled is None else settled["outcome"],
                    reason=None if settled is None else settled["reason"],
                    event_seq=None if settled is None else settled["event_seq"],
                    command_id=None if command is None else command["command_id"],
                    actor=None if command is None else command["actor"],
                ),
            )

    async def relay_edges(self, relay: str, start: datetime, end: datetime) -> tuple[bool, list[tuple[datetime, bool]]]:
        """The relay's state before ``start`` and its edges from then to ``end``, read as v_water_relay_runs reads them:
        any row on at a timestamp."""
        async with self.pool.acquire() as conn:
            seed = await conn.fetchval(_RELAY_SEED_SQL, relay, GREENHOUSE_ID, start)
            rows = await conn.fetch(_RELAY_EDGES_SQL, relay, GREENHOUSE_ID, start, end)
        return bool(seed), [(row["ts"], bool(row["state"])) for row in rows]

    async def close(self, row_id: int, closing: RunClose) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(_CLOSE_LEDGER_SQL, row_id, closing.ended_at, closing.open_s, closing.pulses)

    async def _record_occurrence_outcome(self, conn: Any, event: IrrEvent) -> None:
        """Every stage of the occurrence takes it. A row already settled keeps its first outcome, so an old entry never
        rewrites one. An entry with no row (``skipped``, ``missed`` or ``cap`` before a valve opened) inserts one."""
        reason, seq = event.reason, event.seq
        outcome = event.outcome.value
        if await conn.fetchval(_OCCURRENCE_ROWS_SQL, GREENHOUSE_ID, event.slot_id, event.local_day):
            await conn.execute(
                _OCCURRENCE_OUTCOME_SQL, GREENHOUSE_ID, event.slot_id, event.local_day, outcome, reason, seq
            )
            return
        degraded = await conn.fetchval(_SLOT_DEGRADED_SQL, GREENHOUSE_ID, event.slot_id)
        await self._insert(
            conn,
            IrrigationLedgerRow(
                line_id=event.line_id,
                slot_id=event.slot_id,
                local_day=event.local_day,
                cause=_occurrence_cause(event.line_id, bool(degraded)),
                outcome=event.outcome,
                reason=reason,
                event_seq=seq,
            ),
        )

    async def _record_run_now_outcome(self, conn: Any, event: IrrEvent, running: Sequence[int], now: datetime) -> None:
        """A run-now has no key, so it finds its row by line: the newest with no outcome that is not still running. A
        fertilizer run-now's stages share its command. A refusal opened nothing and gets a row of its own."""
        outcome, reason, seq = event.outcome.value, event.reason, event.seq
        if event.outcome not in _UNOPENED_OUTCOMES:
            partner = CLEAN_PARTNER.get(event.line_id)
            own_cause = IrrigationCause.FEED if partner else IrrigationCause.RUN_NOW
            row = await conn.fetchrow(
                _RUN_NOW_CANDIDATE_SQL,
                GREENHOUSE_ID,
                event.local_day,
                event.line_id,
                own_cause.value,
                partner,
                sorted(_RINSE_CAUSES),
                list(running),
            )
            if row is not None:
                await conn.execute(_ROW_OUTCOME_SQL, row["id"], outcome, reason, seq)
                if row["command_id"] is not None:
                    await conn.execute(
                        _COMMAND_ROWS_OUTCOME_SQL, GREENHOUSE_ID, row["command_id"], outcome, reason, seq
                    )
                return
        command = await self._run_command(conn, [event.line_id], now)
        await self._insert(
            conn,
            IrrigationLedgerRow(
                line_id=event.line_id,
                local_day=event.local_day,
                cause=IrrigationCause.RUN_NOW,
                outcome=event.outcome,
                reason=reason,
                event_seq=seq,
                command_id=None if command is None else command["command_id"],
                actor=None if command is None else command["actor"],
            ),
        )

    async def record_outcomes(self, events: Sequence[IrrEvent], running: Sequence[int], now: datetime) -> None:
        """The ring's entries, in one transaction. An entry already stored (its sequence, slot and day) inserts nothing,
        so a replayed text does nothing and the first read after a reconnect fills what the ring still holds. Matching on
        identity rather than on the newest stored sequence also survives a power cut, which restarts the sequence. Newest
        first, so two run-nows' outcomes in one text find their own rows: each takes the newest row left."""
        if not events:
            return
        async with self.pool.acquire() as conn, conn.transaction():
            stored = {
                (row["event_seq"], row["slot_id"], row["local_day"])
                for row in await conn.fetch(_STORED_EVENTS_SQL, GREENHOUSE_ID, [event.seq for event in events])
            }
            for event in reversed(events):
                if (event.seq, event.slot_id, event.local_day) in stored:
                    continue
                if event.slot_id is None:
                    await self._record_run_now_outcome(conn, event, running, now)
                else:
                    await self._record_occurrence_outcome(conn, event)


def _run_key(run: IrrRun) -> tuple[str, str, int | None, int]:
    return (run.line_id, run.cause.value, run.slot_id, run.start_epoch)


async def _apply_state_change(ledger: LedgerStore, memory: EngineMemory, change: shared.IrrStateChange) -> None:
    """One ``irr_state`` reading: a new non-VPD run token gets its row, and a tracked one no longer named has left."""
    state = irr_state_reading(change.text)
    if state is None:
        if change.text != memory.bad_state_text:
            memory.bad_state_text = change.text
            log.error("irr_state outside the grammar ignored (%d characters)", len(change.text))
        return
    named = set()
    for run in state.runs:
        if run.cause is IrrigationCause.VPD:
            continue
        key = _run_key(run)
        named.add(key)
        tracked = memory.open_runs.get(key)
        if tracked is None:
            row_id = await ledger.record_start(run, change.observed_at)
            tracked = OpenRun(row_id, run.line_id, _run_started_at(run, change.observed_at))
            memory.open_runs[key] = tracked
        tracked.left_at = tracked.left_generation = None
        tracked.generation = change.generation
        if run.end_epoch:
            tracked.planned_end = datetime.fromtimestamp(run.end_epoch, UTC)
    for key, tracked in memory.open_runs.items():
        if key not in named and tracked.left_at is None:
            tracked.left_at = change.observed_at
            tracked.left_generation = change.generation


async def _close_left_runs(ledger: LedgerStore, memory: EngineMemory, now: datetime) -> None:
    for key, tracked in list(memory.open_runs.items()):
        if tracked.left_at is None or now - tracked.left_at < LEDGER_CLOSE_AFTER:
            continue
        relay = LINE_RELAYS.get(tracked.line_id)
        until = min(now, tracked.left_at + LEDGER_EDGE_WAIT)
        seed_on, edges = (False, []) if relay is None else await ledger.relay_edges(relay, tracked.started_at, until)
        closing = run_close(
            edges,
            started_at=tracked.started_at,
            left_at=tracked.left_at,
            planned_end=tracked.planned_end,
            seed_on=seed_on,
            watched=tracked.generation == tracked.left_generation,
            now=now,
        )
        if closing is None:
            continue
        await ledger.close(tracked.row_id, closing)
        del memory.open_runs[key]


async def _sync_ledger(ledger: LedgerStore, memory: EngineMemory) -> None:
    now = datetime.now(UTC)
    # Read first: the controller publishes irr_state before irr_events, so every reading this text follows is queued
    # below. A text read after them could name a run whose token has not left yet, and take a row of its own.
    text = shared.irrigation_readback.get(shared.IRR_EVENTS)
    if not memory.open_runs_loaded:
        for row in await ledger.open_rows(now):
            key = (row["line_id"], row["cause"], row["slot_id"], int(row["started_at"].timestamp()))
            memory.open_runs.setdefault(key, OpenRun(row["id"], row["line_id"], row["started_at"]))
        memory.open_runs_loaded = True
    changes = shared.irr_state_changes
    while changes:
        try:
            await _apply_state_change(ledger, memory, changes[0])
        except ValidationError as exc:
            log.error("irrigation_ledger row dropped: %d validation error(s)", exc.error_count())
        changes.popleft()  # only now: a database error keeps the reading for the next pass
    await _close_left_runs(ledger, memory, now)
    if text is None or text == memory.events_text:
        return
    running = [tracked.row_id for tracked in memory.open_runs.values() if tracked.left_at is None]
    try:
        await ledger.record_outcomes(parse_irr_events(text), running, now)
    except ValueError as exc:  # text outside the grammar, or a row the model refuses (ValidationError is a ValueError)
        log.error("irr_events ignored (%d characters): %s", len(text), type(exc).__name__)
    memory.events_text = text


# ── One pass ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PushAttempt:
    """The last push that went out, waiting for ``irr_program_id`` or a reject."""

    sha256: str
    revision: int
    generation: int
    sent_at: float  # monotonic


@dataclass(frozen=True)
class RejectBackoff:
    """A rejected program's retry stage, kept in memory: a newly edited program goes at once."""

    sha256: str
    stage: int
    next_at: float  # monotonic


@dataclass
class OpenRun:
    """A ledger row whose run token is out, or has left and is waiting to close."""

    row_id: int
    line_id: str
    started_at: datetime
    planned_end: datetime | None = None
    generation: int | None = None  # the connection that last named the token
    left_at: datetime | None = None
    left_generation: int | None = None


@dataclass
class EngineMemory:
    """What the pass keeps between ticks. All of it can be lost: a restart compares the hash again, reloads the open
    rows and rereads the ring."""

    last_full_at: float | None = None
    full_generation: int | None = None
    attempt: PushAttempt | None = None
    backoff: RejectBackoff | None = None
    undelivered_stops: set[UUID | None] = field(default_factory=set)
    open_runs: dict[tuple[str, str, int | None, int], OpenRun] = field(default_factory=dict)
    open_runs_loaded: bool = False
    events_text: str | None = None
    not_on_controller: tuple[int, ...] = ()
    bad_state_text: str | None = None
    no_engine_generation: int | None = None


_memory = EngineMemory()


def _note_no_engine(memory: EngineMemory) -> None:
    """Once per connection. VD-64's ``irr_engine`` row tells the API, which reports ``program.src`` null."""
    if shared.esp32.get("client") is None or memory.no_engine_generation == shared.transport_generation:
        return
    memory.no_engine_generation = shared.transport_generation
    log.info("irrigation engine not on controller: only the L01 bridge acts on this connection")


async def _engine_pass(store: CommandStore, program: ProgramStore, ledger: LedgerStore, memory: EngineMemory) -> None:
    """Commands on every pass; the ledger and the program steps once ``irr_program_id`` has appeared. Neither of those
    can keep a command from going out, or each other from running."""
    for row in await store.expire():
        log.info("irrigation command=%s kind=%s failed: %s", row.command_id, row.kind, TOO_OLD)
    failure = esp32_push.bridge_preflight_failure()
    _note_blocked(failure)
    if failure is None:
        await _send_engine_commands(store, memory)
    if shared.IRR_PROGRAM_ID not in shared.irrigation_readback:
        return
    try:
        await _sync_ledger(ledger, memory)
    except Exception as exc:
        log.error("irrigation ledger step failed: %s", type(exc).__name__)
    await _program_steps(store, program, memory)


async def irrigation_sync(
    pool: asyncpg.Pool,
    *,
    store: CommandStore | None = None,
    program: ProgramStore | None = None,
    ledger: LedgerStore | None = None,
) -> None:
    """One pass of the irrigation step: the engine's when both its services are on this connection, else the bridge."""
    store = store or CommandStore(pool)
    if engine_on_connection():
        await _engine_pass(store, program or ProgramStore(pool), ledger or LedgerStore(pool), _memory)
        return
    _note_no_engine(_memory)
    await _bridge_pass(store)
