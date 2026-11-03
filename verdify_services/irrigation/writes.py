"""Run L01 now and stop any wet line: API-IRR-06 and API-IRR-07, MCP ``irrigation_run`` and ``irrigation_stop``.

Until the irrigation engine firmware is on the controller, both go through the L01 bridge (irrigation §6.5,
VD-64): the ingestor presses the existing ESPHome button ``btn_wall_clean`` ("Wall Drips - RUN CLEAN") over
its single native-API session, so L01 runs for the controller's ``irrig_wall_duration_min``, and a Stop turns
the open wet relays in its scope off the same way. These functions only record the request. Each is one
``activity.recorded_write`` (verdify_services/README.md): the ``irrigation_command`` row, its ``activity_log``
row and ``pg_notify('irrigation_changed', '')`` commit together, and only the ingestor's ``irrigation_sync``
(ingestor/tasks/irrigation.py) turns the row into device calls, under the writer fences.

- Engine: the newest ``system_state`` row with entity ``irr_engine``, which the ingestor writes on every
  ESP32 connect; missing or ``false`` selects the bridge. This module builds only that path: #1156 adds the
  engine path, and until then a run while the engine is on is 503 ``unavailable`` and stores nothing.
- ``run``: L01 only before the engine; any other line is 503 ``unavailable`` ("arrives with the irrigation
  firmware", not retryable) and stores nothing. ``minutes`` may only repeat ``irrig_wall_duration_min``
  (``out_of_range`` otherwise). Refused while L01 is disabled, while the controller's ``sw_irrig_enabled`` or
  ``sw_irrig_wall_enabled`` reads off (the job machine would drop the press), while it is paused, and while it
  runs or a run of it is pending or sent and not yet over (``line_busy``: a press during a run would queue a
  second full run).
- ``stop``: never refused for engine state, heap or the ingestor being down; an unknown line is
  ``unknown_target``. It stores ``{"bridge": true}`` before the engine.

Every write takes ``pg_advisory_xact_lock(hashtext('irrigation:' || greenhouse_id))`` before its checks
(irrigation §8.3), so two writers never both pass the busy check. Nothing here reads
``DASHBOARD_WRITES_ENABLED``: the BFF alone enforces it, and never on Stop.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated, Any
from uuid import UUID

import asyncpg
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic.json_schema import SkipJsonSchema

from verdify_schemas.activity import REASON_MAX_LENGTH, ActivityLogRow
from verdify_schemas.irrigation import (
    BRIDGE_RUN_LINE,
    LINE_IDS,
    RUN_BOUNDS_BY_KIND,
    Command,
    IrrigationCommandRow,
    LineId,
)
from verdify_schemas.tunable_registry import REGISTRY
from verdify_services import activity
from verdify_services.actor import Actor
from verdify_services.problems import ServiceError, problem

GREENHOUSE_ID = activity.GREENHOUSE_ID
NOTIFY_CHANNEL = "irrigation_changed"
ENGINE_ENTITY = "irr_engine"
DURATION_PARAM = "irrig_wall_duration_min"
# The controller switches the legacy job machine needs on to take the press, in the order they are checked.
BRIDGE_SWITCHES = ("sw_irrig_enabled", "sw_irrig_wall_enabled")
STOP_TOGGLED_SWITCH = "sw_irrig_enabled"  # the one a bridge Stop switches off for one job tick
NO_ENGINE_DETAIL = "arrives with the irrigation firmware"
AUDIT_MAX_LENGTH = 64  # trigger_id and planner_instance, kept only in the activity detail

_RANGES = {**RUN_BOUNDS_BY_KIND["fert_drip"], **RUN_BOUNDS_BY_KIND["fert_mister"]}
_BODY_FIELDS = ("minutes", "prewet_s", "flush_s", "on_s", "count", "gap_s")
_COMMAND_COLUMNS = (
    "command_id, greenhouse_id, kind, line_id, args::text AS args, status, fail_reason, source, actor, reason, "
    "created_at, sent_at"
)
_LOCK_SQL = "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext('irrigation:' || $1))"
_NOTIFY_SQL = f"SELECT pg_catalog.pg_notify('{NOTIFY_CHANNEL}', '')"
_ENGINE_SQL = (
    "SELECT value FROM public.system_state WHERE entity = $2 AND COALESCE(greenhouse_id, 'vallery') = $1 "
    "ORDER BY ts DESC LIMIT 1"
)
# L01's line row, its newest relay edge, and the controller readbacks the bridge depends on: the newest
# setpoint_snapshot value of each (the cfg_* readback of the duration and the two switches). stop_toggled: a bridge
# Stop switched sw_irrig_enabled off for one job tick within the last 2 min. A snapshot taken during that tick reads
# it off for a minute (the readback publishes every 30 s and the snapshot every 60 s), and that off is not a refusal.
_L01_SQL = f"""
SELECT now() AS now, l.enabled, l.paused_until, l.relay_on, l.relay_at,
       EXISTS (SELECT 1 FROM public.irrigation_command AS c
                WHERE c.greenhouse_id = l.greenhouse_id AND c.kind = 'stop' AND c.status = 'sent'
                  AND c.sent_at > now() - interval '2 minutes' AND c.args ? 'restore_irrig_enabled') AS stop_toggled,
       (SELECT s.value FROM public.setpoint_snapshot AS s
         WHERE s.parameter = '{DURATION_PARAM}' AND COALESCE(s.greenhouse_id, 'vallery') = l.greenhouse_id
         ORDER BY s.ts DESC LIMIT 1) AS duration_min,
       (SELECT s.value FROM public.setpoint_snapshot AS s
         WHERE s.parameter = '{BRIDGE_SWITCHES[0]}' AND COALESCE(s.greenhouse_id, 'vallery') = l.greenhouse_id
         ORDER BY s.ts DESC LIMIT 1) AS {BRIDGE_SWITCHES[0]},
       (SELECT s.value FROM public.setpoint_snapshot AS s
         WHERE s.parameter = '{BRIDGE_SWITCHES[1]}' AND COALESCE(s.greenhouse_id, 'vallery') = l.greenhouse_id
         ORDER BY s.ts DESC LIMIT 1) AS {BRIDGE_SWITCHES[1]}
  FROM public.v_irrigation_line_state AS l
 WHERE l.line_id = $1 AND l.greenhouse_id = $2
"""
# L01 runs that may still be watering: pending or sent, and not ended by a later Stop that covers L01.
_LIVE_RUNS_SQL = """
SELECT c.status, c.created_at, c.sent_at, c.args::text AS args
  FROM public.irrigation_command AS c
 WHERE c.greenhouse_id = $1 AND c.kind = 'run' AND c.line_id = $2 AND c.status IN ('pending', 'sent')
   AND c.created_at > now() - interval '1 day'
   AND NOT EXISTS (SELECT 1 FROM public.irrigation_command AS s
                    WHERE s.greenhouse_id = c.greenhouse_id AND s.kind = 'stop'
                      AND s.status IN ('pending', 'sent') AND (s.line_id IS NULL OR s.line_id = c.line_id)
                      AND s.created_at > c.created_at)
"""
_INSERT_SQL = (
    "INSERT INTO public.irrigation_command (greenhouse_id, kind, line_id, args, source, actor, reason) "
    f"VALUES ($1, $2, $3, $4::text::jsonb, $5, $6, $7) RETURNING {_COMMAND_COLUMNS}"
)
_LOAD_SQL = f"SELECT {_COMMAND_COLUMNS} FROM public.irrigation_command WHERE command_id = $1"


def _bounded(name: str) -> Any:
    """An optional whole number inside its RUN_BOUNDS_BY_KIND range; absent, never null, in the schema."""
    low, high = _RANGES[name]
    return Annotated[int, Field(ge=low, le=high)] | SkipJsonSchema[None]


_Minutes = _bounded("minutes")
_PrewetS = _bounded("prewet_s")
_FlushS = _bounded("flush_s")
_OnS = _bounded("on_s")
_Count = _bounded("count")
_GapS = _bounded("gap_s")
_Reason = Annotated[str, Field(max_length=REASON_MAX_LENGTH)] | SkipJsonSchema[None]


class RunRequest(BaseModel):
    """API-IRR-06's body, the OpenAPI ``RunRequest``: drips take ``minutes``; fertilizer drips add ``prewet_s``
    and ``flush_s``; misters take ``on_s``, ``count`` and ``gap_s``. Every field is optional and none is null.
    Before the engine firmware only L01 runs, for its configured ``irrig_wall_duration_min``."""

    model_config = ConfigDict(extra="forbid")

    minutes: _Minutes = None
    prewet_s: _PrewetS = None
    flush_s: _FlushS = None
    on_s: _OnS = None
    count: _Count = None
    gap_s: _GapS = None
    reason: _Reason = None


class LineCommand(BaseModel):
    """API-IRR-07's body, the OpenAPI ``LineCommand``: the line to stop, or no ``line_id`` for every line."""

    model_config = ConfigDict(extra="forbid")

    line_id: LineId | SkipJsonSchema[None] = None
    reason: _Reason = None


@dataclass(frozen=True)
class _L01:
    """L01's row, relay edge and controller readbacks at the database's ``now``."""

    now: datetime
    enabled: bool
    paused_until: datetime | None
    relay_on: bool | None
    relay_at: datetime | None
    duration_min: int
    switches: Mapping[str, float | None]
    stop_toggled: bool


def _invalid(exc: ValidationError) -> ServiceError:
    """A RunRequest that breaks the OpenAPI bounds is invalid_request (400), as the REST body would be."""
    first = exc.errors()[0] if exc.errors() else {}
    loc = first.get("loc") or (None,)
    field = loc[0] if isinstance(loc[0], str) else None  # the body field; a union branch name may follow it
    return ServiceError(problem("invalid_request", str(first.get("msg") or "The request is not valid"), field=field))


def _check_audit(trigger_id: str | None, planner_instance: str | None) -> None:
    for field, value in (("trigger_id", trigger_id), ("planner_instance", planner_instance)):
        if value is not None and (not isinstance(value, str) or not 1 <= len(value) <= AUDIT_MAX_LENGTH):
            raise ServiceError(
                problem("invalid_request", f"{field} is 1-{AUDIT_MAX_LENGTH} characters when sent", field=field)
            )


def _unknown_line(line_id: object) -> ServiceError:
    return ServiceError(problem("unknown_target", f"{line_id!r} is not a line; lines are L01-L11", field="line_id"))


def _command_detail(command: Command, **extra: Any) -> dict[str, Any]:
    """The activity detail: ``ref`` for a replay's ``load``, then what the Activity screen shows."""
    command_id = str(command.command_id)
    return {"ref": {"table": "irrigation_command", "id": command_id}, "command_id": command_id, **extra}


async def _load(conn: asyncpg.Connection, row: ActivityLogRow) -> Command:
    """A replay's stored object: the command its activity row names, as it reads now (pending, sent or failed)."""
    ref = row.detail.get("ref") or {}
    if ref.get("table") != "irrigation_command" or not ref.get("id"):
        raise RuntimeError(f"activity row {row.id} does not name an irrigation_command")
    stored = await conn.fetchrow(_LOAD_SQL, UUID(str(ref["id"])))
    if stored is None:
        raise RuntimeError(f"irrigation_command {ref['id']} named by activity row {row.id} is gone")
    return Command.from_row(stored)


async def _lock(conn: asyncpg.Connection) -> None:
    await conn.execute(_LOCK_SQL, GREENHOUSE_ID)


async def engine_on(conn: asyncpg.Connection) -> bool:
    """True when the newest ``irr_engine`` row reads ``true``: the live controller has the irrigation engine."""
    value = await conn.fetchval(_ENGINE_SQL, GREENHOUSE_ID, ENGINE_ENTITY)
    return isinstance(value, str) and value.strip().lower() == "true"


async def _insert(
    conn: asyncpg.Connection,
    actor: Actor,
    *,
    kind: str,
    line_id: str | None,
    args: Mapping[str, Any],
    reason: str | None,
) -> Command:
    row = IrrigationCommandRow(
        kind=kind, line_id=line_id, args=dict(args), source=actor.source, actor=actor.name, reason=reason
    )
    stored = await conn.fetchrow(
        _INSERT_SQL,
        row.greenhouse_id,
        row.kind,
        row.line_id,
        json.dumps(row.args, sort_keys=True, separators=(",", ":")),
        row.source,
        row.actor,
        row.reason,
    )
    await conn.execute(_NOTIFY_SQL)
    return Command.from_row(stored)


def _duration_min(value: object) -> int:
    """The controller's ``irrig_wall_duration_min`` readback, or the registry default when there is none."""
    spec = REGISTRY[DURATION_PARAM]
    number = float(value) if isinstance(value, int | float) else math.nan
    if not math.isfinite(number):
        number = spec.default
    return int(round(min(max(number, spec.min or 1), spec.max or number)))


async def _l01(conn: asyncpg.Connection) -> _L01:
    row = await conn.fetchrow(_L01_SQL, BRIDGE_RUN_LINE, GREENHOUSE_ID)
    if row is None:
        raise ServiceError(problem("unknown_target", "L01 is not in irrigation_line", field="line_id"))
    return _L01(
        now=row["now"],
        enabled=bool(row["enabled"]),
        paused_until=row["paused_until"],
        relay_on=row["relay_on"],
        relay_at=row["relay_at"],
        duration_min=_duration_min(row["duration_min"]),
        switches={name: row[name] for name in BRIDGE_SWITCHES},
        stop_toggled=bool(row["stop_toggled"]),
    )


def _seconds_until(end: datetime, now: datetime) -> int:
    return max(0, math.ceil((end - now).total_seconds()))


async def _busy_until(conn: asyncpg.Connection, state: _L01) -> tuple[bool, datetime | None]:
    """Whether L01 is busy, and until when when that is known.

    Busy while its relay reads ON, or while a run of it is pending or sent and its planned end (``sent_at``,
    or ``created_at`` while pending, plus its minutes) has not passed and no later Stop covering L01 ended it.
    """
    ends: list[datetime] = []
    for run in await conn.fetch(_LIVE_RUNS_SQL, GREENHOUSE_ID, BRIDGE_RUN_LINE):
        args = json.loads(run["args"]) if run["args"] else {}
        minutes = args.get("minutes")
        minutes = minutes if isinstance(minutes, int) and not isinstance(minutes, bool) else state.duration_min
        end = (run["sent_at"] or run["created_at"]) + timedelta(minutes=minutes)
        if end > state.now:
            ends.append(end)
    if ends:
        return True, max(ends)
    if state.relay_on:
        if state.relay_at is not None:
            end = state.relay_at + timedelta(minutes=state.duration_min)
            return True, end if end > state.now else None
        return True, None
    return False, None


async def _check_bridge_run(conn: asyncpg.Connection, body: RunRequest) -> int:
    """Steps 3-5 of a bridge run on L01; returns the minutes it will run."""
    state = await _l01(conn)
    configured = state.duration_min
    if body.minutes is not None and body.minutes != configured:
        raise ServiceError(
            problem(
                "out_of_range",
                f"Before the irrigation firmware L01 runs for the controller's {DURATION_PARAM} ({configured} min); "
                f"send minutes {configured}, or leave it out, or change {DURATION_PARAM} in Settings",
                field="minutes",
                min=configured,
                max=configured,
                current=configured,
            )
        )
    if not state.enabled:
        raise ServiceError(problem("line_disabled", "L01 is disabled; enable the line to run it", field="line_id"))
    for switch in BRIDGE_SWITCHES:
        if switch == STOP_TOGGLED_SWITCH and state.stop_toggled:
            continue
        reading = state.switches.get(switch)
        if reading is not None and reading < 0.5:
            label = REGISTRY[switch].label or switch
            raise ServiceError(
                problem(
                    "line_disabled",
                    f"The controller's {switch} ({label}) is off, so the wall drip job would drop the press; "
                    f"turn {switch} on in Settings to run L01",
                    field="line_id",
                )
            )
    if state.paused_until is not None and state.paused_until > state.now:
        raise ServiceError(
            problem(
                "line_paused",
                f"L01 is paused until {state.paused_until.isoformat()}; resume it to run it",
                field="line_id",
                current=state.paused_until.isoformat(),
            )
        )
    busy, until = await _busy_until(conn, state)
    if busy:
        detail = "L01 is already running"
        if until is not None:
            detail += f" until {until.isoformat()}"
        raise ServiceError(
            problem(
                "line_busy",
                detail + "; a press now would queue a second full run, so try again once it ends",
                field="line_id",
                retry_after_s=None if until is None else _seconds_until(until, state.now),
            )
        )
    return configured


async def run(
    conn: asyncpg.Connection,
    actor: Actor | None,
    line_id: str,
    minutes: int | None = None,
    reason: str | None = None,
    request_id: UUID | str | None = None,
    trigger_id: str | None = None,
    planner_instance: str | None = None,
    *,
    prewet_s: int | None = None,
    flush_s: int | None = None,
    on_s: int | None = None,
    count: int | None = None,
    gap_s: int | None = None,
) -> activity.WriteResult:
    """Run a line now (API-IRR-06; MCP ``irrigation_run``): one ``pending`` run command, or a refusal.

    Before the engine firmware only L01 runs (the bridge): the command is ``{"minutes": <configured>,
    "bridge": true}`` and its activity row (``irrigation.run``, target L01) carries the command id, minutes,
    ``trigger_id`` and ``planner_instance``. The keyword-only fields are the rest of the RunRequest body: the
    REST adapter passes what it received; L01 takes none of them (``invalid_request``). A refusal stores
    nothing. Returns ``WriteResult[Command]``; the REST answer is 202.
    """
    try:
        body = RunRequest(
            minutes=minutes, prewet_s=prewet_s, flush_s=flush_s, on_s=on_s, count=count, gap_s=gap_s, reason=reason
        )
    except ValidationError as exc:
        raise _invalid(exc) from exc
    _check_audit(trigger_id, planner_instance)
    target = str(line_id)

    async def perform(conn: asyncpg.Connection) -> tuple[Command, dict[str, Any]]:
        await _lock(conn)
        if target not in LINE_IDS:
            raise _unknown_line(line_id)
        if await engine_on(conn):
            raise ServiceError(
                problem(
                    "unavailable",
                    "Run-now through the irrigation engine is not available yet; Stop works",
                    retryable=False,
                )
            )
        if target != BRIDGE_RUN_LINE:
            raise ServiceError(
                problem(
                    "unavailable",
                    f"Run-now on {target} {NO_ENGINE_DETAIL}; before it only L01 runs",
                    field="line_id",
                    retryable=False,
                )
            )
        extra = [name for name in _BODY_FIELDS if name != "minutes" and getattr(body, name) is not None]
        if extra:
            raise ServiceError(
                problem(
                    "invalid_request", f"L01 is a drip line: a run takes minutes only, not {extra[0]}", field=extra[0]
                )
            )
        configured = await _check_bridge_run(conn, body)
        command = await _insert(
            conn, actor, kind="run", line_id=target, args={"minutes": configured, "bridge": True}, reason=body.reason
        )
        detail = _command_detail(
            command,
            line_id=target,
            minutes=configured,
            bridge=True,
            trigger_id=trigger_id,
            planner_instance=planner_instance,
        )
        return command, detail

    return await activity.recorded_write(
        conn,
        actor,
        op="API-IRR-06",
        action="irrigation.run",
        target=target,
        request=body.model_dump(mode="json", exclude_none=True),
        request_id=request_id,
        reason=body.reason,
        perform=perform,
        load=_load,
    )


async def stop(
    conn: asyncpg.Connection,
    actor: Actor | None,
    line_id: str | None = None,
    reason: str | None = None,
    request_id: UUID | str | None = None,
) -> activity.WriteResult:
    """Stop one line, or every line when ``line_id`` is None (API-IRR-07; MCP ``irrigation_stop``).

    One ``pending`` stop command (``line_id`` NULL for all; ``{"bridge": true}`` before the engine) with its
    activity row (``irrigation.stop``, target the line or ``all``). Never refused for engine state, heap or the
    ingestor being down: the ingestor sends it ahead of any run, and before the engine it ends the legacy wall
    drip job and turns the open wet relays in scope off; VPD misting may pulse again afterwards. Returns
    ``WriteResult[Command]``; the REST answer is 202.
    """
    request = {key: value for key, value in (("line_id", line_id), ("reason", reason)) if value is not None}
    target = "all" if line_id is None else str(line_id)

    async def perform(conn: asyncpg.Connection) -> tuple[Command, dict[str, Any]]:
        await _lock(conn)
        if line_id is not None and line_id not in LINE_IDS:
            raise _unknown_line(line_id)
        bridge = not await engine_on(conn)
        command = await _insert(
            conn, actor, kind="stop", line_id=line_id, args={"bridge": True} if bridge else {}, reason=reason
        )
        return command, _command_detail(command, line_id=line_id, bridge=bridge)

    return await activity.recorded_write(
        conn,
        actor,
        op="API-IRR-07",
        action="irrigation.stop",
        target=target,
        request=request,
        request_id=request_id,
        reason=reason,
        perform=perform,
        load=_load,
    )
