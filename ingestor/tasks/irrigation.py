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

Iris's context pack, section 11 (VD-60; irrigation §6.4), is ``python3 -m tasks.irrigation context --hours 24``, run
from the ingestor's working directory: ``load_context`` reads it with SQL and ``verdify_schemas``, because this image
does not carry ``verdify_services``, and ``render_context`` prints it. It only reads. Importing this module starts
nothing: ``python3 -m`` runs the ``tasks`` package first, which imports it once before it runs again as ``__main__``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import asyncpg
import esp32_push
import shared
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from verdify_schemas.irrigation import (
    BRIDGE_RUN_LINE,
    COMMAND_SEND_WINDOW_S,
    LOCAL_TZ_NAME,
    REASON_WORDS,
    STOP_RESEND_WINDOW_S,
    Agenda,
    FaultKind,
    IrrigationCommandRow,
    LineId,
    LineSpec,
    LineState,
    RunOutcome,
    ScheduleSpec,
    agenda,
    line_state,
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


# ── Section 11 of Iris's context pack (VD-60) ───────────────────────────────

GREENHOUSE_ID = "vallery"
# The architecture §7 test, as verdify_services.reads holds it (this image does not carry verdify_services).
HEARTBEAT_STALE_S = 180
CLIMATE_REPORTING_S = 300
CONTEXT_DEFAULT_HOURS = 24
CONTEXT_MAX_HOURS = 144  # always inside agenda()'s 7 local days
CONTEXT_MAX_LINES = 60
# With the 11 lines these leave the section under its budget whatever is stored.
CONTEXT_MAX_OCCURRENCES = 24
CONTEXT_MAX_MANUAL = 14
CONTEXT_FOOTER = "The irrigation tools change watering only; they do not count as your plan for a trigger."
CONTEXT_DB_TIMEOUT_S = 15

_CONTEXT_CLOCK_SQL = """
SELECT now() AS at,
       (SELECT max(ts) FROM system_state
         WHERE entity = 'ingestor_heartbeat' AND ts > now() - interval '1 day') AS heartbeat_at,
       (SELECT max(ts) FROM climate WHERE ts > now() - interval '1 day') AS climate_at,
       (SELECT value FROM system_state
         WHERE entity = $1 AND coalesce(greenhouse_id, 'vallery') = $2 AND ts > now() - interval '400 days'
         ORDER BY ts DESC LIMIT 1) AS engine
"""
_CONTEXT_LINES_SQL = """
SELECT line_id, name, zone, kind, device_idx, clean_partner, reserved, enabled, vpd_member, schedule_member,
       daily_cap_min, vpd_reserve_min, paused_until, manual, source, updated_by, updated_at, equipment,
       relay_on, relay_at, irr_state, irr_state_at
  FROM v_irrigation_line_state
 WHERE greenhouse_id = $1
 ORDER BY device_idx
"""
_CONTEXT_SLOTS_SQL = """
SELECT slot_id, line_id, enabled, days, start_kind, start_min, on_s, count, gap_s, prewet_s, flush_s, degraded_only,
       label, manual, source, updated_by, updated_at
  FROM irrigation_slot
 WHERE greenhouse_id = $1
 ORDER BY slot_id
"""
# Where each relay's run still on began, as M3 and irrigation_status start it: the newest conflicting timestamp since
# the relay was last off, else the first timestamp after that. The view's relay_at is the newest row, which a
# reconnect snapshot moves, so it cannot say whether a run started within 30 s of its command.
_CONTEXT_ON_EDGES_SQL = """
SELECT w.equipment, coalesce(last_conflict.ts, first_on.ts) AS on_edge
  FROM unnest($1::text[]) AS w(equipment)
  LEFT JOIN LATERAL (SELECT es.ts FROM equipment_state AS es
                      WHERE es.equipment = w.equipment AND coalesce(es.greenhouse_id, 'vallery') = $2
                        AND es.ts < now()
                      GROUP BY es.ts
                     HAVING NOT bool_or(es.state)
                      ORDER BY es.ts DESC
                      LIMIT 1) AS last_off ON true
  LEFT JOIN LATERAL (SELECT es.ts FROM equipment_state AS es
                      WHERE es.equipment = w.equipment AND coalesce(es.greenhouse_id, 'vallery') = $2
                        AND es.ts < now() AND (last_off.ts IS NULL OR es.ts > last_off.ts)
                      GROUP BY es.ts
                     HAVING count(DISTINCT es.state) > 1
                      ORDER BY es.ts DESC
                      LIMIT 1) AS last_conflict ON true
  LEFT JOIN LATERAL (SELECT es.ts FROM equipment_state AS es
                      WHERE last_conflict.ts IS NULL
                        AND es.equipment = w.equipment AND coalesce(es.greenhouse_id, 'vallery') = $2
                        AND es.ts < now() AND (last_off.ts IS NULL OR es.ts > last_off.ts)
                      ORDER BY es.ts
                      LIMIT 1) AS first_on ON true
"""
# The newest sent run command at or before the run's ON edge, unless a ledger row holds that run: M3's rule asks the
# ledger first, so such a run is no bridge run here, as in irrigation_status. It takes a rollback to the bridge with an
# engine row whose end never arrived.
_CONTEXT_BRIDGE_SQL = """
SELECT max(c.sent_at) FROM irrigation_command AS c
 WHERE c.greenhouse_id = $1 AND c.line_id = $2 AND c.kind = 'run' AND c.status = 'sent'
   AND c.sent_at <= $3::timestamptz
   AND NOT EXISTS (SELECT 1 FROM irrigation_ledger AS l
                    WHERE l.greenhouse_id = $1 AND l.line_id = $2
                      AND l.started_at <= $3::timestamptz AND l.started_at > $3::timestamptz - interval '24 hours'
                      AND coalesce(l.ended_at, now()) >= $3::timestamptz)
"""
# M4's day-bounded reader: the same rows as v_irrigation_minutes_daily, without its scan of every edge since 2024.
_CONTEXT_MINUTES_SQL = """
SELECT line_id, minutes FROM fn_irrigation_minutes_daily($1::date, $1::date) WHERE greenhouse_id = $2
"""
_CONTEXT_OUTCOMES_SQL = """
SELECT DISTINCT ON (line_id) line_id, outcome, reason, coalesce(ended_at, started_at, recorded_at) AS at
  FROM irrigation_ledger
 WHERE greenhouse_id = $1 AND (ended_at IS NOT NULL OR outcome IS NOT NULL)
 ORDER BY line_id, coalesce(ended_at, started_at, recorded_at) DESC, id DESC
"""
_CONTEXT_ARMED_SQL = "SELECT experiment_id FROM fn_mcp_armed_assignment_context()"


class _ContextModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ContextOutcome(_ContextModel):
    outcome: RunOutcome
    at: AwareDatetime
    reason: str | None = None


class ContextLine(_ContextModel):
    """One line's row: the fields ``irrigation_status`` gives it, with its state from the same ``line_state()``."""

    line_id: LineId
    enabled: bool
    vpd_member: bool | None = None
    schedule_member: bool | None = None
    manual: bool
    paused_until: AwareDatetime | None = None
    fault: FaultKind | None = None
    fault_until: AwareDatetime | None = None
    minutes_today: float | None = None  # null for a day with no data, never 0
    daily_cap_min: int
    last_outcome: ContextOutcome | None = None
    state: LineState


class ContextOccurrence(_ContextModel):
    slot_id: int
    line_id: LineId
    planned_start: AwareDatetime
    minutes: float
    pulses: int | None = None
    status: str
    reason: str | None = None


class ContextManualItem(_ContextModel):
    """A schedule or a line a person holds: who and since."""

    item: str
    updated_by: str
    source: str
    updated_at: AwareDatetime


class IrrigationContext(_ContextModel):
    at: AwareDatetime
    hours: int = Field(ge=1, le=CONTEXT_MAX_HOURS)
    engine: bool
    reporting: bool
    armed_experiment_id: str | None = None
    lines: tuple[ContextLine, ...]
    occurrences: tuple[ContextOccurrence, ...] = ()
    manual_items: tuple[ContextManualItem, ...] = ()


def context_occurrences(plan: Agenda, *, at: datetime, hours: int, engine: bool) -> list[ContextOccurrence]:
    """The agenda's occurrences in [``at``, ``at`` + ``hours``). With no engine nothing runs them, so each one says
    it arrives with the irrigation firmware, and a planned one reads blocked, as API-IRR-02 reads it."""
    until = at + timedelta(hours=hours)
    out = []
    for item in plan.occurrences:
        if not at <= item.planned_start < until:
            continue
        status, reason = item.status, item.reason
        if not engine:
            status = "blocked" if item.status == "planned" else item.status
            reason = NO_ENGINE if reason is None else f"{reason}; {NO_ENGINE}"
        out.append(
            ContextOccurrence(
                slot_id=item.slot_id,
                line_id=item.line_id,
                planned_start=item.planned_start,
                minutes=item.minutes,
                pulses=item.pulses,
                status=status,
                reason=reason,
            )
        )
    return out


def _context_minutes(rows: Iterable[Mapping[str, Any]], line_id: str, *, no_data: bool) -> float | None:
    """A line's minutes today: null for a day with no data; a line that did not run read 0."""
    if no_data:
        return None
    return float(sum((Decimal(str(row["minutes"])) for row in rows if row["line_id"] == line_id), Decimal(0)))


def context_lines(
    line_rows: Sequence[Mapping[str, Any]],
    *,
    engine: bool,
    reporting: bool,
    now: datetime,
    on_edges: Mapping[str, datetime],
    bridge_run_at: datetime | None,
    minutes_rows: Sequence[Mapping[str, Any]],
    outcome_rows: Sequence[Mapping[str, Any]],
    no_data_today: bool,
) -> list[ContextLine]:
    """Each ``v_irrigation_line_state`` row through ``line_state()``, with the keyword arguments
    ``verdify_services.irrigation.reads.status`` gives it, and the rest of the line's row."""
    outcomes = {row["line_id"]: row for row in outcome_rows}
    lines = []
    for row in line_rows:
        line_id = row["line_id"]
        edge = on_edges.get(row["equipment"])
        source = {**dict(row), "relay_at": edge} if edge is not None else row
        fields = line_state(
            source,
            engine=engine,
            reporting=reporting,
            now=now,
            bridge_run_at=bridge_run_at if line_id == BRIDGE_RUN_LINE else None,
        )
        outcome = outcomes.get(line_id)
        lines.append(
            ContextLine(
                line_id=line_id,
                enabled=row["enabled"],
                vpd_member=row["vpd_member"],
                schedule_member=row["schedule_member"],
                manual=row["manual"],
                paused_until=row["paused_until"],
                fault=fields.fault,
                fault_until=fields.fault_until,
                minutes_today=_context_minutes(minutes_rows, line_id, no_data=no_data_today),
                daily_cap_min=row["daily_cap_min"],
                last_outcome=(
                    None
                    if outcome is None
                    else ContextOutcome(
                        outcome=outcome["outcome"] or RunOutcome.UNKNOWN,
                        at=outcome["at"],
                        reason=None
                        if outcome["reason"] is None
                        else REASON_WORDS.get(outcome["reason"], outcome["reason"]),
                    )
                ),
                state=fields.state,
            )
        )
    return lines


def _manual_items(
    line_rows: Iterable[Mapping[str, Any]], slot_rows: Iterable[Mapping[str, Any]]
) -> list[ContextManualItem]:
    items = [
        ContextManualItem(
            item=f"line {row['line_id']}",
            updated_by=row["updated_by"],
            source=row["source"],
            updated_at=row["updated_at"],
        )
        for row in line_rows
        if row["manual"]
    ]
    for row in slot_rows:
        if row["manual"]:
            name = f"schedule {row['slot_id']} on {row['line_id']}"
            items.append(
                ContextManualItem(
                    item=f"{name} ({row['label']})" if row["label"] else name,
                    updated_by=row["updated_by"],
                    source=row["source"],
                    updated_at=row["updated_at"],
                )
            )
    return items


async def load_context(conn: asyncpg.Connection, greenhouse_id: str, hours: int) -> IrrigationContext:
    """Section 11's facts in one read-only ``REPEATABLE READ`` snapshot, so every ``now()`` in them agrees."""
    if isinstance(hours, bool) or not isinstance(hours, int) or not 1 <= hours <= CONTEXT_MAX_HOURS:
        raise ValueError(f"hours must be 1-{CONTEXT_MAX_HOURS}, not {hours!r}")
    zone = ZoneInfo(LOCAL_TZ_NAME)
    async with conn.transaction(isolation="repeatable_read", readonly=True):
        clock = await conn.fetchrow(_CONTEXT_CLOCK_SQL, ENGINE_ENTITY, greenhouse_id)
        at: datetime = clock["at"]
        local_today: date = at.astimezone(zone).date()
        midnight = datetime.combine(local_today, time(0), tzinfo=zone)
        last_local_day = (at + timedelta(hours=hours)).astimezone(zone).date()
        engine = isinstance(clock["engine"], str) and clock["engine"].strip().lower() == "true"
        heartbeat_at, climate_at = clock["heartbeat_at"], clock["climate_at"]
        reporting = (
            heartbeat_at is not None
            and (at - heartbeat_at).total_seconds() <= HEARTBEAT_STALE_S
            and climate_at is not None
            and (at - climate_at).total_seconds() <= CLIMATE_REPORTING_S
        )
        line_rows = await conn.fetch(_CONTEXT_LINES_SQL, greenhouse_id)
        slot_rows = await conn.fetch(_CONTEXT_SLOTS_SQL, greenhouse_id)
        on_edges: dict[str, datetime] = {}
        bridge_run_at: datetime | None = None
        if reporting:
            running = [row["equipment"] for row in line_rows if row["relay_on"]]
            if running:
                rows = await conn.fetch(_CONTEXT_ON_EDGES_SQL, running, greenhouse_id)
                on_edges = {row["equipment"]: row["on_edge"] for row in rows if row["on_edge"] is not None}
            bridge_edge = next(
                (on_edges.get(row["equipment"]) for row in line_rows if row["line_id"] == BRIDGE_RUN_LINE), None
            )
            if bridge_edge is not None:
                bridge_run_at = await conn.fetchval(_CONTEXT_BRIDGE_SQL, greenhouse_id, BRIDGE_RUN_LINE, bridge_edge)
        minutes_rows = await conn.fetch(_CONTEXT_MINUTES_SQL, local_today, greenhouse_id)
        outcome_rows = await conn.fetch(_CONTEXT_OUTCOMES_SQL, greenhouse_id)
        armed = await conn.fetchval(_CONTEXT_ARMED_SQL)

    plan = agenda(
        tuple(LineSpec.from_row(row) for row in line_rows),
        tuple(ScheduleSpec.from_row(row) for row in slot_rows),
        today=local_today,
        days=min(7, (last_local_day - local_today).days + 1),
    )
    return IrrigationContext(
        at=at,
        hours=hours,
        engine=engine,
        reporting=reporting,
        armed_experiment_id=None if armed is None else str(armed),
        lines=context_lines(
            line_rows,
            engine=engine,
            reporting=reporting,
            now=at,
            on_edges=on_edges,
            bridge_run_at=bridge_run_at,
            minutes_rows=minutes_rows,
            outcome_rows=outcome_rows,
            # No climate row today: API-IRR-10 reads the day no_data, so it is a gap here too.
            no_data_today=climate_at is None or climate_at < midnight,
        ),
        occurrences=context_occurrences(plan, at=at, hours=hours, engine=engine),
        manual_items=_manual_items(line_rows, slot_rows),
    )


def _cell(value: object) -> str:
    """One table cell on one line. Unknown prints as null, so a gap never reads as 0."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    return " ".join(str(value).split()).replace("|", "/")


def _local_time(moment: datetime | None) -> str:
    return "null" if moment is None else moment.astimezone(ZoneInfo(LOCAL_TZ_NAME)).strftime("%m-%d %H:%M")


def _state_cell(state: LineState) -> str:
    parts = [state.code]
    if state.cause is not None:
        parts.append(f"cause={state.cause.value}")
    if state.slot_id is not None:
        parts.append(f"schedule={state.slot_id}")
    if state.reason is not None:
        parts.append(f"reason={state.reason}")
    if state.ends_at is not None:
        parts.append(f"until={_local_time(state.ends_at)}")
    return _cell(" ".join(parts))


def _line_row(line: ContextLine) -> str:
    fault = _cell(line.fault)
    if line.fault_until is not None:
        fault += f" until {_local_time(line.fault_until)}"
    outcome = "null"
    if line.last_outcome is not None:
        outcome = f"{line.last_outcome.outcome.value} {_local_time(line.last_outcome.at)}"
        if line.last_outcome.reason is not None:
            outcome += f" ({_cell(line.last_outcome.reason)})"
    return "|".join(
        (
            line.line_id,
            _cell(line.enabled),
            _cell(line.vpd_member),
            _cell(line.schedule_member),
            _cell(line.manual),
            _local_time(line.paused_until),
            fault,
            f"{_cell(line.minutes_today)}/{line.daily_cap_min}",
            outcome,
            _state_cell(line.state),
        )
    )


def _capped(rows: list[str], limit: int, more: str) -> list[str]:
    """Rows within their part of the budget; what is left out is counted and says where it is."""
    if not rows:
        return ["(none)"]
    if len(rows) <= limit:
        return rows
    return [*rows[:limit], f"... and {len(rows) - limit} more: {more}"]


def render_context(ctx: IrrigationContext) -> str:
    """Section 11 as Iris reads it: one row per line, the next hours of occurrences and the manual items, within
    ``CONTEXT_MAX_LINES``. Pure. Times are local; unknown prints as null, never 0."""
    occurrences = [
        "|".join(
            (
                _local_time(item.planned_start),
                item.line_id,
                str(item.slot_id),
                f"{item.minutes:g}",
                _cell(item.pulses),
                item.status,
                _cell(item.reason),
            )
        )
        for item in ctx.occurrences
    ]
    manual = [
        "|".join((_cell(item.item), _cell(item.updated_by), _cell(item.source), _local_time(item.updated_at)))
        for item in ctx.manual_items
    ]
    return "\n".join(
        (
            f"--- IRRIGATION (lines, next {ctx.hours} h, manual items) ---",
            f"now={_local_time(ctx.at)} {LOCAL_TZ_NAME} engine={_cell(ctx.engine)} reporting={_cell(ctx.reporting)}",
            f"experiment_armed={ctx.armed_experiment_id or 'none'}",
            "line|enabled|vpd_member|schedule_member|manual|paused_until|fault|minutes_today/daily_cap_min"
            "|last_outcome|state",
            *(_line_row(line) for line in ctx.lines),
            f"next {ctx.hours} h: start|line|schedule|minutes|pulses|status|reason",
            *_capped(occurrences, CONTEXT_MAX_OCCURRENCES, "irrigation_schedule has them all"),
            "manual schedules and lines: item|who|source|since",
            *_capped(manual, CONTEXT_MAX_MANUAL, "irrigation_status and irrigation_schedule have them all"),
            CONTEXT_FOOTER,
        )
    )


def _context_hours(text: str) -> int:
    hours = int(text)
    if not 1 <= hours <= CONTEXT_MAX_HOURS:
        raise argparse.ArgumentTypeError(f"--hours must be 1-{CONTEXT_MAX_HOURS}")
    return hours


async def _print_context(hours: int) -> None:
    import config  # the ingestor's own settings; only this entry point needs them

    conn = await asyncpg.connect(
        config.get_db_dsn(),
        timeout=CONTEXT_DB_TIMEOUT_S,
        command_timeout=CONTEXT_DB_TIMEOUT_S,
        server_settings={
            "application_name": "verdify-iris-context",
            "statement_timeout": f"{CONTEXT_DB_TIMEOUT_S * 1000}",
        },
    )
    try:
        context = await load_context(conn, GREENHOUSE_ID, hours)
    finally:
        await conn.close()
    print(render_context(context))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m tasks.irrigation", description="The ingestor's irrigation step.")
    commands = parser.add_subparsers(dest="command", required=True)
    context = commands.add_parser("context", help="Print section 11 of Iris's context pack. Only reads.")
    context.add_argument(
        "--hours",
        type=_context_hours,
        default=CONTEXT_DEFAULT_HOURS,
        help=f"Hours of occurrences ahead, 1-{CONTEXT_MAX_HOURS} (default {CONTEXT_DEFAULT_HOURS}).",
    )
    args = parser.parse_args(argv)
    asyncio.run(_print_context(args.hours))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
