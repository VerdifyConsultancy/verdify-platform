"""The irrigation writes: API-IRR-03 to -08, -12 and -13, which the MCP write tools (#1174) call unchanged.

These functions only record requests. Each is one ``activity.recorded_write`` (verdify_services/README.md): the
irrigation row, its ``activity_log`` row and ``pg_notify('irrigation_changed', '')`` commit together, and only the
ingestor's ``irrigation_sync`` (ingestor/tasks/irrigation.py) compiles, pushes and sends, under the writer fences.
Nothing here touches ``setpoint_changes`` or the NOTIFY fast path, and nothing reads ``DASHBOARD_WRITES_ENABLED``:
the BFF alone enforces it, and never on Stop.

- Every write takes ``pg_advisory_xact_lock(hashtext('irrigation:' || greenhouse_id))`` first, before its replay read
  and its checks (irrigation §8.3), so two writers never both take the 24th slot or both pass a busy check, and a
  concurrent duplicate replays the first instead of being refused by what the first stored.
- Fields: a function takes the REST body's model or a mapping of the same fields, and applies only the fields it
  carries (``model_fields_set``). ``actor``, ``source`` and ``*_by`` are dropped unread.
- ``manual``: stored exactly as given and never defaulted here. ``None`` leaves an edit's stored flag alone, and a
  create needs one. REST defaults it to true (``api/operator_routes/irrigation_writes.py``) and MCP to false. Iris
  alone is refused with ``manual`` on a manual schedule or line, a pause included, and on an ``all`` pause while any
  line is manual; commands never check the flag. Answers carry ``manual_before``, and the activity row
  ``manual: {before, after}``.
- Activity detail: ``ref`` for a replay, ``line_id``, ``before`` and ``value`` (the fields before and after, as the
  Activity screen reads them), ``manual``, and the MCP-only ``trigger_id`` and ``planner_instance``. A command's
  holds its ``command_id`` and ``args``. A body with ``paused_until`` also sets ``detail.paused_until`` to the stored
  value (null on a resume), which ``reads`` turns into ``paused_by`` and ``pause_reason``.
- Engine: the newest ``system_state`` row with entity ``irr_engine``, which the ingestor writes on every ESP32
  connect; missing or ``false`` is no engine. Before the engine (irrigation §6.5, VD-64) schedule and line writes
  are stored for the first push; run on L01 and every Stop go through the bridge, which presses ``btn_wall_clean``
  over the ingestor's native-API session, so L01 runs for the controller's ``irrig_wall_duration_min``; any other
  run, the override and clear-fault are 503 ``unavailable`` ("arrives with the irrigation firmware", not retryable)
  and store nothing.
- ``run`` before the engine: ``minutes`` may only repeat ``irrig_wall_duration_min`` (``out_of_range`` otherwise).
  Refused while L01 is disabled, while the controller's ``sw_irrig_enabled`` or ``sw_irrig_wall_enabled`` reads off
  (the job machine would drop the press), while it is paused, and while it runs or a run of it is pending or sent
  and not yet over (``line_busy``: a press during a run would queue a second full run). Those three are no longer
  registry rows or routes (#1165 retired them once the engine was last-good, so a rollback flashes the engine): the
  bridge reads the last readbacks recorded before the engine flash, or the firmware's default duration.
- ``run`` with the engine: the body per line kind (``RunArgs``), then the refusals of irrigation §4.7 in their order
  from the stored line, the newest ``irr_state`` and today's minutes. Nothing is queued, and the device may still
  refuse what the platform cannot see.
- ``stop``: never refused for engine state, heap or the ingestor being down; an unknown line is
  ``unknown_target``. It stores ``{"bridge": true}`` before the engine.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID

import asyncpg
from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    ValidationError,
    model_validator,
)
from pydantic.json_schema import SkipJsonSchema
from pydantic_core import InitErrorDetails

from verdify_schemas.activity import REASON_MAX_LENGTH, ActivityLogRow
from verdify_schemas.irrigation import (
    BRIDGE_RUN_LINE,
    CIRCUIT_BY_ID,
    CIRCUIT_OF_LINE,
    CLEAN_PARTNER,
    COMMAND_SEND_WINDOW_S,
    FOG_RUN_CAP,
    IRR_CAPS_ENTITY,
    LINE_BOUNDS,
    LINE_IDS,
    LINE_KIND,
    LOCAL_TZ_NAME,
    MISTER_MASK_BIT,
    RUN_ANY_MISTER_CAP,
    RUN_ARGS_BY_KIND,
    RUN_BOUNDS_BY_KIND,
    Command,
    FertMisterRunArgs,
    IrrigationCause,
    IrrigationCommandRow,
    IrrRun,
    IrrState,
    LineId,
    LineSpec,
    MisterRunArgs,
    Refusal,
    RunArgs,
    ScheduleSpec,
    StartKind,
    Variant,
    Weekday,
    _Whole,
    _whole,
    circuit_line,
    firmware_takes,
    irr_state_reading,
    local_tz,
    run_args,
    validate_line,
    validate_run,
    validate_schedule,
)
from verdify_services import activity
from verdify_services.actor import Actor, ActorRef
from verdify_services.irrigation import reads
from verdify_services.irrigation.reads import IrrigationLine, Schedule
from verdify_services.labels import name_line_codes
from verdify_services.problems import Problem, ServiceError
from verdify_services.problems import problem as _problem

GREENHOUSE_ID = activity.GREENHOUSE_ID
NOTIFY_CHANNEL = "irrigation_changed"
ENGINE_ENTITY = "irr_engine"
IRR_STATE_ENTITY = "irr_state"
DURATION_PARAM = "irrig_wall_duration_min"
# The pre-engine firmware's own default and range for that duration. The bridge's registry rows retired once the
# engine was last-good (#1165), so the bridge keeps the values those rows held.
DURATION_DEFAULT_MIN = 10
DURATION_RANGE_MIN = (1, 120)
# The controller switches the legacy job machine needs on to take the press, in the order they are checked, with
# the names the pre-engine firmware gives them.
BRIDGE_SWITCHES = ("sw_irrig_enabled", "sw_irrig_wall_enabled")
BRIDGE_SWITCH_LABELS = {"sw_irrig_enabled": "Irrigation Enabled", "sw_irrig_wall_enabled": "Irrigation Wall Enabled"}
STOP_TOGGLED_SWITCH = "sw_irrig_enabled"  # the one a bridge Stop switches off for one job tick
NO_ENGINE_DETAIL = "arrives with the irrigation firmware"
AUDIT_MAX_LENGTH = 64  # trigger_id and planner_instance, kept only in the activity detail
CHECK_NOTE_MAX_LENGTH = 500
# A run command this recent counts as the line's run even before irr_state names it (line_busy).
RUN_SENT_BUSY_S = 30
SLOT_ID_RANGE = (1, 0xFFFF)
# A REST body with any of these is a definition edit, so it stores manual true unless it says otherwise (R3).
LINE_DEFINITION_FIELDS = frozenset({"enabled", "vpd_member", "schedule_member", "daily_cap_min", "vpd_reserve_min"})
OVERRIDE_BURSTS = frozenset({"fans", "humidify", "vent_bypass"})

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
# setpoint_snapshot value of each (the cfg_* readback of the duration and the two switches). Their routes retired with
# the bridge's rows (#1165), so these are the last values recorded before the engine flash. stop_toggled: a bridge
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
_LINE_COLUMNS = (
    "line_id, kind, device_idx, clean_partner, reserved, enabled, vpd_member, schedule_member, daily_cap_min, "
    "vpd_reserve_min, paused_until, manual, checked_by, checked_at, check_note"
)
_LINES_SQL = (
    f"SELECT now() AS now, {_LINE_COLUMNS} FROM public.irrigation_line WHERE greenhouse_id = $1 ORDER BY device_idx"
)
_PAUSE_EVERY_LINE_SQL = (
    "UPDATE public.irrigation_line SET paused_until = $2, source = $3, updated_by = $4, reason = $5, "
    "updated_at = now() WHERE greenhouse_id = $1"
)
_SLOT_COLUMNS = (
    "slot_id, line_id, enabled, days, start_kind, start_min, on_s, count, gap_s, prewet_s, flush_s, degraded_only, "
    "label, manual, source, updated_by, updated_at, reason"
)
_SLOTS_SQL = f"SELECT {_SLOT_COLUMNS} FROM public.irrigation_slot WHERE greenhouse_id = $1 ORDER BY slot_id"
_INSERT_SLOT_SQL = (
    "INSERT INTO public.irrigation_slot (line_id, enabled, days, start_kind, start_min, on_s, count, gap_s, prewet_s, "
    "flush_s, degraded_only, label, manual, source, updated_by, reason, greenhouse_id) "
    f"VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17) RETURNING {_SLOT_COLUMNS}"
)
_UPDATE_SLOT_SQL = (
    "UPDATE public.irrigation_slot SET line_id = $1, enabled = $2, days = $3, start_kind = $4, start_min = $5, "
    "on_s = $6, count = $7, gap_s = $8, prewet_s = $9, flush_s = $10, degraded_only = $11, label = $12, manual = $13, "
    "source = $14, updated_by = $15, reason = $16, updated_at = now() "
    f"WHERE slot_id = $17 RETURNING {_SLOT_COLUMNS}"
)
_DELETE_SLOT_SQL = "DELETE FROM public.irrigation_slot WHERE slot_id = $1"
# A run of the line that may start any moment: pending and not yet too old to send, or sent in the last 30 s, and
# not ended by a later Stop that covers the line.
_RECENT_RUN_SQL = f"""
SELECT EXISTS (
    SELECT 1 FROM public.irrigation_command AS c
     WHERE c.greenhouse_id = $1 AND c.kind = 'run' AND c.line_id = $2
       AND ((c.status = 'pending' AND c.created_at > now() - interval '{COMMAND_SEND_WINDOW_S} seconds')
            OR (c.status = 'sent' AND c.sent_at > now() - interval '{RUN_SENT_BUSY_S} seconds'))
       AND NOT EXISTS (SELECT 1 FROM public.irrigation_command AS s
                        WHERE s.greenhouse_id = c.greenhouse_id AND s.kind = 'stop'
                          AND s.status IN ('pending', 'sent') AND (s.line_id IS NULL OR s.line_id = c.line_id)
                          AND s.created_at > c.created_at))
"""
# Today's valve-open minutes on one line. M4's bounded reader: the same rows as v_irrigation_minutes_daily, its
# equipment_state scan held to today, because this runs under the irrigation lock.
_TODAY_MINUTES_SQL = f"""
SELECT coalesce(sum(m.minutes), 0)
  FROM public.fn_irrigation_minutes_daily((now() AT TIME ZONE '{LOCAL_TZ_NAME}')::date,
                                          (now() AT TIME ZONE '{LOCAL_TZ_NAME}')::date) AS m
 WHERE m.greenhouse_id = $1 AND m.line_id = $2
"""


def _no_nul(text: str) -> str:
    """Stored free text: PostgreSQL cannot hold a NUL, so it is a bad request here, not a database error there."""
    if "\x00" in text:
        raise ValueError("must not hold a NUL character")
    return text


def _bounded(name: str) -> Any:
    """An optional whole number inside its RUN_BOUNDS_BY_KIND range; absent, never null, in the schema."""
    low, high = _RANGES[name]
    return Annotated[int, Field(ge=low, le=high), BeforeValidator(_whole)] | SkipJsonSchema[None]


_Minutes = _bounded("minutes")
_PrewetS = _bounded("prewet_s")
_FlushS = _bounded("flush_s")
_OnS = _bounded("on_s")
_Count = _bounded("count")
_GapS = _bounded("gap_s")
_Reason = Annotated[str, Field(max_length=REASON_MAX_LENGTH)] | SkipJsonSchema[None]


class _Body(BaseModel):
    """A write's body. An unknown field is refused, and who is writing never comes from it: ``actor``, ``source`` and
    ``*_by`` are dropped unread (architecture §5.6)."""

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _drop_caller_identity(cls, data: object) -> object:
        if not isinstance(data, Mapping):
            return data
        return {
            field: value
            for field, value in data.items()
            if not (isinstance(field, str) and (field in ("actor", "source") or field.endswith("_by")))
        }


class RunRequest(_Body):
    """API-IRR-06's body, the OpenAPI ``RunRequest``: drips take ``minutes``; fertilizer drips add ``prewet_s``
    and ``flush_s``; misters take ``on_s``, ``count`` and ``gap_s``. Every field is optional and none is null.
    Before the engine firmware only L01 runs, for its configured ``irrig_wall_duration_min``."""

    minutes: _Minutes = None
    prewet_s: _PrewetS = None
    flush_s: _FlushS = None
    on_s: _OnS = None
    count: _Count = None
    gap_s: _GapS = None
    reason: _Reason = None


class LineCommand(_Body):
    """API-IRR-07's and -13's body, the OpenAPI ``LineCommand``: the line, or no ``line_id`` for every line."""

    line_id: LineId | SkipJsonSchema[None] = None
    reason: _Reason = None


# The edit bodies leave each field out rather than null, so ``model_fields_set`` says what an edit applies. Bounds
# per line kind are the submit checks' (out_of_range, 422), not the model's (invalid_request, 400); the types and
# the snap to whole numbers are ScheduleSpec's and LineSpec's.
class ScheduleStartBody(_Body):
    """The OpenAPI ``ScheduleStart``: ``clock`` with a local HH:MM, or a solar kind with ``offset_min``."""

    kind: StartKind
    clock: str = None
    offset_min: _Whole = None


class ScheduleInput(_Body):
    """API-IRR-04's body, the OpenAPI ``ScheduleInput``: only the fields an edit changes. A schedule names its relay
    (``line_id``) or a physical line and variant (``circuit_id`` and ``variant``, #1336), which the services map to
    the relay; an edit may give ``variant`` alone to switch clean and fertilized on the same physical line."""

    line_id: str = None
    circuit_id: str = None
    variant: Variant = None
    label: Annotated[str, Field(max_length=60), AfterValidator(_no_nul)] = None
    enabled: StrictBool = None
    days: Annotated[list[Weekday], Field(min_length=1)] = None
    start: ScheduleStartBody = None
    on_s: _Whole = None
    count: _Whole = None
    gap_s: _Whole = None
    prewet_s: _Whole = None
    flush_s: _Whole = None
    degraded_only: StrictBool = None
    manual: StrictBool = None
    reason: _Reason = None


class ScheduleCreate(ScheduleInput):
    """API-IRR-03's body, the OpenAPI ``ScheduleCreate``: a line (``line_id``, or ``circuit_id`` and ``variant``),
    days, a start and ``on_s``; the rest default."""

    days: Annotated[list[Weekday], Field(min_length=1)]
    start: ScheduleStartBody
    on_s: _Whole

    @model_validator(mode="before")
    @classmethod
    def _names_a_line(cls, data: object) -> object:
        """A create names its line: ``line_id``, or ``circuit_id`` and ``variant``; with neither, ``line_id`` is
        missing (400 on that field)."""
        if isinstance(data, Mapping) and all(data.get(key) is None for key in ("line_id", "circuit_id", "variant")):
            details = [InitErrorDetails(type="missing", loc=("line_id",), input=dict(data))]
            raise ValidationError.from_exception_data(cls.__name__, details)
        return data


class ScheduleDeleted(BaseModel):
    """What a delete answers MCP with; REST answers 204 with no body."""

    slot_id: int
    deleted: Literal[True] = True
    manual_before: bool | None = None


class LineUpdate(_Body):
    """API-IRR-08's body, the OpenAPI ``LineUpdate``. ``paused_until`` null resumes; ``checked`` and ``check_note``
    are a dashboard person's only."""

    enabled: StrictBool = None
    vpd_member: StrictBool = None
    schedule_member: StrictBool = None
    checked: StrictBool = None
    check_note: Annotated[str, Field(max_length=CHECK_NOTE_MAX_LENGTH), AfterValidator(_no_nul)] = None
    daily_cap_min: _Whole = None
    vpd_reserve_min: _Whole = None
    paused_until: AwareDatetime | None = None
    manual: StrictBool = None
    reason: _Reason = None


class LinesUpdated(BaseModel):
    """API-IRR-08's answer: the changed lines, each with ``manual_before``."""

    lines: list[IrrigationLine]


class OverrideRequest(_Body):
    """API-IRR-12's body, the OpenAPI ``OverrideRequest``: a burst with its minutes, or ``clear`` or ``restart``."""

    kind: Literal["fans", "humidify", "vent_bypass", "clear", "restart"]
    minutes: _Minutes = None
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
    """A body that breaks the OpenAPI shape is invalid_request (400), as the REST body would be."""
    first = exc.errors()[0] if exc.errors() else {}
    loc = first.get("loc") or (None,)
    field = loc[0] if isinstance(loc[0], str) else None  # the body field; a union branch name may follow it
    return ServiceError(problem("invalid_request", str(first.get("msg") or "The request is not valid"), field=field))


def _check_audit(trigger_id: str | None, planner_instance: str | None) -> None:
    for field, value in (("trigger_id", trigger_id), ("planner_instance", planner_instance)):
        if value is not None and (
            not isinstance(value, str) or not 1 <= len(value) <= AUDIT_MAX_LENGTH or "\x00" in value
        ):
            raise ServiceError(
                problem("invalid_request", f"{field} is 1-{AUDIT_MAX_LENGTH} characters when sent", field=field)
            )


def _unknown_line(line_id: object) -> ServiceError:
    return ServiceError(problem("unknown_target", f"{line_id!r} is not a line; lines are L01-L11", field="line_id"))


def problem(code: Any, detail: str, **fields: Any) -> Problem:
    """``verdify_services.problems.problem`` with the detail in the operator's words: every relay code a person would
    read ("L01 is disabled") reads as the line's name ("Wall drip is disabled", #1341). Structured fields keep the
    ``line_id`` for agents."""
    return _problem(code, name_line_codes(detail), **fields)


def _refused(refusal: Refusal) -> ServiceError:
    body = refusal.problem()
    return ServiceError(Problem.model_validate({**body, "detail": name_line_codes(body["detail"])}))


def _resolve_target(
    line_id: str | None, circuit_id: str | None, variant: str | None, *, current_line: str | None = None
) -> str | None:
    """#1336: the relay a request names, from ``line_id`` or a physical line's ``circuit_id`` and ``variant``.

    ``variant`` alone keeps the physical line of ``line_id`` or of ``current_line`` (an edit). A line or variant that
    is not one, and a variant whose valve is not wired, are ``unknown_target`` (``circuit_line``); a ``line_id`` that
    is not that variant's relay, or a ``circuit_id`` with no ``variant``, is ``invalid_request``. ``None`` when the
    request names nothing. The master valve follows the variant: L03 opens with a fertilized relay only."""
    if circuit_id is None and variant is None:
        return line_id
    if circuit_id is None:
        found = CIRCUIT_OF_LINE.get(line_id or current_line or "")
        if found is None:
            raise ServiceError(
                problem("invalid_request", "A variant needs its physical line (circuit_id)", field="circuit_id")
            )
        circuit_id = found[0]
    if variant is None:
        raise ServiceError(
            problem("invalid_request", "A physical line needs its variant: clean or fertilized", field="variant")
        )
    resolved = circuit_line(circuit_id, variant)
    if isinstance(resolved, Refusal):
        raise _refused(resolved)
    if line_id is not None and line_id != resolved:
        name = CIRCUIT_BY_ID[circuit_id].name
        raise ServiceError(
            problem("invalid_request", f"{line_id} is not {name}'s {variant} relay ({resolved})", field="line_id")
        )
    return resolved


def _no_engine(what: str) -> ServiceError:
    return ServiceError(problem("unavailable", f"{what} {NO_ENGINE_DETAIL}", retryable=False))


def _parsed[B: _Body](model: type[B], fields: B | Mapping[str, Any]) -> B:
    """The body as its model; a mapping of the same fields (MCP) is validated like a REST body."""
    if isinstance(fields, model):
        return fields
    try:
        return model.model_validate(dict(fields))
    except ValidationError as exc:
        raise _invalid(exc) from exc


def _carried(body: _Body, **dump: Any) -> dict[str, Any]:
    """The fields a body carries, with ``manual`` and ``reason``, which travel as their own arguments, apart."""
    return body.model_dump(exclude_unset=True, exclude={"manual", "reason"}, **dump)


def _hashed_request(body: _Body, manual: bool | None, reason: str | None) -> dict[str, Any]:
    """What the request hash covers: the carried fields as JSON, the flag as it will be stored and the reason."""
    request = _carried(body, mode="json")
    if manual is not None:
        request["manual"] = manual
    if reason is not None:
        request["reason"] = reason
    return request


def _check_manual_flag(manual: object) -> None:
    if manual is not None and not isinstance(manual, bool):
        raise ServiceError(problem("invalid_request", "manual must be true or false", field="manual"))


def _refuse_iris_on_manual(actor: Actor, manual: object, what: str, field: str) -> None:
    """Only Iris is refused: a person or another agent may overwrite a manual item, attributed (architecture §5.6)."""
    if actor.source == "iris" and manual:
        raise ServiceError(
            problem("manual", f"{what} is manual, so Iris leaves it alone until the flag is cleared", field=field)
        )


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
    """The controller's ``irrig_wall_duration_min`` readback, or the firmware's default when there is none."""
    number = float(value) if isinstance(value, int | float) else math.nan
    if not math.isfinite(number):
        number = DURATION_DEFAULT_MIN
    low, high = DURATION_RANGE_MIN
    return int(round(min(max(number, low), high)))


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
                f"send minutes {configured}, or leave it out",
                field="minutes",
                min=configured,
                max=configured,
                current=configured,
            )
        )
    if not state.enabled:
        raise ServiceError(problem("line_disabled", "L01 is disabled; enable it to run it", field="line_id"))
    for switch in BRIDGE_SWITCHES:
        if switch == STOP_TOGGLED_SWITCH and state.stop_toggled:
            continue
        reading = state.switches.get(switch)
        if reading is not None and reading < 0.5:
            raise ServiceError(
                problem(
                    "line_disabled",
                    f"The controller's {switch} ({BRIDGE_SWITCH_LABELS[switch]}) is off, so the wall drip job "
                    "would drop the press",
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


def _engine_run_args(target: str, body: RunRequest) -> RunArgs | None:
    """The body as the line kind's ``RunArgs``; L03 takes no runs (``unknown_target``), fog runs ``minutes``."""
    kind = LINE_KIND[target]
    args = None
    if kind in RUN_ARGS_BY_KIND:
        try:
            args = run_args(kind, body.model_dump(exclude_none=True))
        except ValidationError as exc:
            raise _invalid(exc) from exc
    refusal = validate_run(LineSpec(line_id=target), args)
    if refusal is not None:
        raise _refused(refusal)
    return args


def _clock(moment: datetime) -> str:
    """A time as the greenhouse clock reads it (America/Denver), for a refusal's words."""
    return moment.astimezone(local_tz()).strftime("%H:%M")


def _run_until(run: IrrRun, now: datetime) -> tuple[str, int | None]:
    """A run token's planned end, in words and as ``retry_after_s``; nothing once it has passed, as then it is
    not known."""
    end = datetime.fromtimestamp(run.end_epoch, UTC)
    return (f" until {_clock(end)}", _seconds_until(end, now)) if end > now else ("", None)


def _mister_zone(line_id: str) -> frozenset[str]:
    """A mister and its fertilizer line: one watchdog latch holds both (firmware ``zone_lines``)."""
    clean = CLEAN_PARTNER.get(line_id, line_id)
    return frozenset({clean, *(fert for fert, partner in CLEAN_PARTNER.items() if partner == clean)})


# #1343 (supersedes D-IRP-11): a one-time run of any enabled line, a clean mister with Schedule off included. The
# controller refused that run (``not_schedule_member``) before the firmware that drops the rule, so the API keeps the
# refusal until the controller reports a build stamped on or after this day (``fw_version`` is
# ``YYYY.M.D.HHMM.<sha>``; the Mac stamps local time and the builder UTC, so the cutoff is the day after #1343's
# firmware change). An unknown or unreadable version keeps the refusal, so the worst a stale stamp can do is the
# controller's own ``not_schedule_member`` in the ledger, never a run the firmware would not take.
RUN_ANY_MISTER_FIRMWARE_FROM = date(2026, 11, 6)
_FW_STAMP = re.compile(r"^(\d{4})\.(\d{1,2})\.(\d{1,2})\.\d{3,4}\.")
_FIRMWARE_VERSION_SQL = (
    "SELECT firmware_version FROM public.diagnostics WHERE ts > now() - interval '1 day' "
    "AND firmware_version IS NOT NULL AND COALESCE(greenhouse_id, 'vallery') = $1 ORDER BY ts DESC LIMIT 1"
)


def _firmware_built(version: str | None) -> date | None:
    """The build day in a ``fw_version`` stamp (``YYYY.M.D.HHMM.<sha>``); ``None`` when unknown or unreadable."""
    match = _FW_STAMP.match(version or "")
    if match is None:
        return None
    try:
        return date(int(match[1]), int(match[2]), int(match[3]))
    except ValueError:
        return None


def firmware_runs_any_mister(version: str | None, caps_text: str | None = None) -> bool:
    """Whether the controller takes a run-now on a mister with Schedule off: its ``irr_caps`` says so for the version
    it runs, or (a #1343 build from before ``irr_caps``) its ``fw_version`` is stamped on or after the cutoff. A build
    flashed the same day as its code change carries the capability, so it never waits for a date."""
    if firmware_takes(caps_text, version, RUN_ANY_MISTER_CAP):
        return True
    built = _firmware_built(version)
    return built is not None and built >= RUN_ANY_MISTER_FIRMWARE_FROM


def firmware_runs_fog(version: str | None, caps_text: str | None) -> bool:
    """Whether the controller takes a fog run-once (#1343 follow-up): its ``irr_caps`` names ``fog_run`` for the
    ``fw_version`` it reports now. No date gate: a build without the capability, or a rollback, fails closed."""
    return firmware_takes(caps_text, version, FOG_RUN_CAP)


def _check_run_rails(line: LineSpec, reading: IrrState | None, now: datetime, *, any_mister: bool = False) -> None:
    """``line_disabled``, ``line_paused``, ``line_faulted`` and, on a controller that still needs Schedule for a
    mister's run-now (``any_mister`` false), ``not_schedule_member``, from the stored line."""
    target = line.line_id
    if not line.enabled:
        raise ServiceError(problem("line_disabled", f"{target} is disabled; enable it to run it", field="line_id"))
    if line.paused_until is not None and line.paused_until > now:
        raise ServiceError(
            problem(
                "line_paused",
                f"{target} is paused until {_clock(line.paused_until)}; resume it to run it",
                field="line_id",
                current=line.paused_until.isoformat(),
            )
        )
    if line.kind in ("mister", "fert_mister") and reading is not None:
        zone = _mister_zone(target)
        latch = next((f for f in reading.faults if f.fault == "mister_fault" and f.line_id in zone), None)
        if latch is not None:
            raise ServiceError(
                problem(
                    "line_faulted",
                    f"The relay watchdog latched {latch.line_id}, so its mister zone stays off until midnight or "
                    "a clear-fault",
                    field="line_id",
                )
            )
    if target in MISTER_MASK_BIT and not line.schedule_member and not any_mister:
        raise ServiceError(
            problem(
                "not_schedule_member",
                f"{target} has Schedule off, and the controller's firmware runs a mister only with Schedule on until "
                "its next flash; turn Schedule on to run it now",
                field="line_id",
            )
        )


async def _check_run_busy(conn: asyncpg.Connection, target: str, reading: IrrState | None, now: datetime) -> None:
    """``line_busy`` and ``manifold_busy``. A VPD pulse blocks neither: it serves a window on its own zone as one
    pulse and alternates with any other at pulse boundaries (irrigation §4.6). The engine holds one run at a time,
    whichever manifold it is on, so another line's run refuses this one on either (firmware ``irr_run_now``)."""
    runs = [run for run in (reading.runs if reading else ()) if run.cause != IrrigationCause.VPD]
    own = next((run for run in runs if run.line_id == target), None)
    if own is not None:
        until, retry_after_s = _run_until(own, now)
        raise ServiceError(
            problem(
                "line_busy",
                f"{target} is already running{until}; try again once it ends",
                field="line_id",
                retry_after_s=retry_after_s,
            )
        )
    if await conn.fetchval(_RECENT_RUN_SQL, GREENHOUSE_ID, target):
        raise ServiceError(
            problem(
                "line_busy",
                f"A run of {target} was just requested; try again once it shows on the line",
                field="line_id",
            )
        )
    holder = next(iter(runs), None)
    if holder is not None:
        until, retry_after_s = _run_until(holder, now)
        manifold = "drip" if holder.manifold == "drip" else "mister"
        raise ServiceError(
            problem(
                "manifold_busy",
                f"{holder.line_id} holds the {manifold} manifold{until}; try again once it ends",
                field="line_id",
                retry_after_s=retry_after_s,
                current=None if retry_after_s is None else datetime.fromtimestamp(holder.end_epoch, UTC).isoformat(),
            )
        )


async def _check_run_cap(conn: asyncpg.Connection, line: LineSpec, args: RunArgs) -> None:
    """``cap_reached``: the run's minutes past the cap less today's minutes, and less the reserve on a mister."""
    budget = line.schedule_budget_min
    if budget is None:
        return
    used = float(await conn.fetchval(_TODAY_MINUTES_SQL, GREENHOUSE_ID, line.line_id))
    pulsed = isinstance(args, MisterRunArgs | FertMisterRunArgs)
    wanted = args.on_s * args.count / 60 if pulsed else float(args.minutes)
    left = budget - used
    if wanted > left:
        reserve = f" less its {line.vpd_reserve_min} min VPD reserve" if line.kind == "mister" else ""
        raise ServiceError(
            problem(
                "cap_reached",
                f"{line.line_id} has {max(left, 0):.1f} min left today of its {line.daily_cap_min} min cap{reserve}; "
                f"this run is {wanted:.1f} min",
                field="on_s" if pulsed else "minutes",
                max=round(max(left, 0), 1),
                current=round(used, 1),
            )
        )


async def _check_engine_run(conn: asyncpg.Connection, target: str, args: RunArgs) -> None:
    """The run refusals with the engine, in the order of irrigation §4.7's write rules."""
    rows = await conn.fetch(_LINES_SQL, GREENHOUSE_ID)
    row = next((row for row in rows if row["line_id"] == target), None)
    if row is None:
        raise _unknown_line(target)
    line = LineSpec.from_row(row)
    now = row["now"]
    if line.kind == "fog" and not firmware_runs_fog(
        await conn.fetchval(_FIRMWARE_VERSION_SQL, GREENHOUSE_ID),
        await conn.fetchval(_ENGINE_SQL, GREENHOUSE_ID, IRR_CAPS_ENTITY),
    ):
        raise ServiceError(
            problem(
                "unavailable",
                "Fog runs once only on firmware that reports it takes a fog run (irr_caps fog_run); this controller does not",
                field="line_id",
                retryable=False,
            )
        )
    reading = irr_state_reading(await conn.fetchval(_ENGINE_SQL, GREENHOUSE_ID, IRR_STATE_ENTITY))
    any_mister = False
    if target in MISTER_MASK_BIT and line.enabled and not line.schedule_member:
        any_mister = firmware_runs_any_mister(
            await conn.fetchval(_FIRMWARE_VERSION_SQL, GREENHOUSE_ID),
            await conn.fetchval(_ENGINE_SQL, GREENHOUSE_ID, IRR_CAPS_ENTITY),
        )
    _check_run_rails(line, reading, now, any_mister=any_mister)
    await _check_run_busy(conn, target, reading, now)
    await _check_run_cap(conn, line, args)


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
    circuit_id: str | None = None,
    variant: str | None = None,
) -> activity.WriteResult:
    """Run a line now (API-IRR-06; MCP ``irrigation_run``): one ``pending`` run command, or a refusal.

    The line is the relay ``line_id``, or a physical line's ``circuit_id`` and ``variant`` (#1336, ``_resolve_target``:
    a fertilized variant runs its fertilizer relay, which opens L03; a clean one never does).

    Before the engine firmware only L01 runs (the bridge): the command is ``{"minutes": <configured>,
    "bridge": true}`` and its activity row (``irrigation.run``, target L01) carries the command id, minutes,
    ``trigger_id`` and ``planner_instance``. The keyword-only fields are the rest of the RunRequest body: the
    REST adapter passes what it received; on the bridge L01 takes none of them (``invalid_request``).

    With the engine, the body per line kind (drips ``minutes``; L02 and L06 add ``prewet_s`` and ``flush_s``;
    misters ``on_s``, ``count`` and ``gap_s``; L08 and L10 those three plus ``prewet_s`` and ``flush_s``): a missing
    field or another kind's is ``invalid_request`` with ``field``, and L03 is ``unknown_target``. Fog (L11) runs once
    for ``minutes`` (1-30, #1343 follow-up), ``unavailable`` until the controller runs the firmware that takes it
    (``firmware_runs_fog``). Then, in this order, ``line_disabled``, ``line_paused``, ``line_faulted``, ``not_schedule_member`` (a clean mister with
    Schedule off, only until the controller runs #1343's firmware: ``firmware_runs_any_mister``), ``line_busy``,
    ``manifold_busy`` and ``cap_reached``. An accepted run is one ``pending`` command with the body as its ``args``.

    A refusal stores nothing. Returns ``WriteResult[Command]``; the REST answer is 202.
    """
    try:
        body = RunRequest(
            minutes=minutes, prewet_s=prewet_s, flush_s=flush_s, on_s=on_s, count=count, gap_s=gap_s, reason=reason
        )
    except ValidationError as exc:
        raise _invalid(exc) from exc
    _check_audit(trigger_id, planner_instance)
    resolved = _resolve_target(line_id, circuit_id, variant)
    if resolved is None:
        raise ServiceError(
            problem("invalid_request", "Name the line: line_id, or circuit_id and variant", field="line_id")
        )
    target = str(resolved)

    async def perform(conn: asyncpg.Connection) -> tuple[Command, dict[str, Any]]:
        if target not in LINE_IDS:
            raise _unknown_line(line_id)
        if await engine_on(conn):
            args = _engine_run_args(target, body)
            await _check_engine_run(conn, target, args)
            sent = args.model_dump()
            command = await _insert(conn, actor, kind="run", line_id=target, args=sent, reason=body.reason)
            detail = _command_detail(
                command, line_id=target, args=sent, trigger_id=trigger_id, planner_instance=planner_instance
            )
            return command, detail
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
        lock=_lock,
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
        lock=_lock,
        load=_load,
    )


async def clear_fault(
    conn: asyncpg.Connection,
    actor: Actor | None,
    line_id: str | None = None,
    reason: str | None = None,
    request_id: UUID | str | None = None,
) -> activity.WriteResult:
    """Clear a mister's watchdog latch (API-IRR-13; MCP ``irrigation_clear_fault``): L04, L07 or L09, or every mister
    when ``line_id`` is None (any other line is ``unknown_target``). One ``pending`` ``clear_fault`` command with its
    activity row (``irrigation.clear_fault``, target the line or ``all``). Before the engine 503 ``unavailable``,
    storing nothing. Returns ``WriteResult[Command]``; the REST answer is 202."""
    request = {key: value for key, value in (("line_id", line_id), ("reason", reason)) if value is not None}
    target = "all" if line_id is None else str(line_id)

    async def perform(conn: asyncpg.Connection) -> tuple[Command, dict[str, Any]]:
        if line_id is not None and line_id not in LINE_IDS:
            raise _unknown_line(line_id)
        if not await engine_on(conn):
            raise _no_engine("Clearing a mister fault")
        if line_id is not None and line_id not in MISTER_MASK_BIT:
            raise ServiceError(
                problem(
                    "unknown_target",
                    f"{line_id} has no watchdog latch to clear; send L04, L07 or L09, or no line for every mister",
                    field="line_id",
                )
            )
        command = await _insert(conn, actor, kind="clear_fault", line_id=line_id, args={}, reason=reason)
        return command, _command_detail(command, line_id=line_id, args={})

    return await activity.recorded_write(
        conn,
        actor,
        op="API-IRR-13",
        action="irrigation.clear_fault",
        target=target,
        request=request,
        request_id=request_id,
        reason=reason,
        perform=perform,
        lock=_lock,
        load=_load,
    )


async def override(
    conn: asyncpg.Connection,
    actor: Actor | None,
    kind: str,
    minutes: int | None = None,
    reason: str | None = None,
    request_id: UUID | str | None = None,
) -> activity.WriteResult:
    """A burst or a restart (API-IRR-12; no MCP tool in MVP): ``fans``, ``humidify`` or ``vent_bypass`` with
    ``minutes`` 1-120, or ``clear`` or ``restart`` with none. One ``pending`` command with ``kind`` ``override``,
    ``line_id`` null and ``args`` ``{kind, minutes}``, with its activity row (``irrigation.override``, target the
    kind). Before the engine 503 ``unavailable``, storing nothing. Returns ``WriteResult[Command]``; the REST answer
    is 202."""
    try:
        body = OverrideRequest(kind=kind, minutes=minutes, reason=reason)
    except ValidationError as exc:
        raise _invalid(exc) from exc
    if (body.kind in OVERRIDE_BURSTS) != (body.minutes is not None):
        detail = (
            f"A {body.kind} burst needs minutes" if body.kind in OVERRIDE_BURSTS else f"{body.kind} takes no minutes"
        )
        raise ServiceError(problem("invalid_request", detail, field="minutes"))
    args = body.model_dump(exclude_none=True, exclude={"reason"})

    async def perform(conn: asyncpg.Connection) -> tuple[Command, dict[str, Any]]:
        if not await engine_on(conn):
            raise _no_engine("A burst or a restart from here")
        command = await _insert(conn, actor, kind="override", line_id=None, args=args, reason=body.reason)
        return command, _command_detail(command, args=args)

    return await activity.recorded_write(
        conn,
        actor,
        op="API-IRR-12",
        action="irrigation.override",
        target=body.kind,
        request=body.model_dump(mode="json", exclude_none=True),
        request_id=request_id,
        reason=body.reason,
        perform=perform,
        lock=_lock,
        load=_load,
    )


async def light_hold(
    conn: asyncpg.Connection,
    actor: Actor,
    light: Literal["main", "grow"],
    state: Literal["on", "off"] | None,
    minutes: int | None = None,
    reason: str | None = None,
) -> Command | None:
    """#1335: the controller's own hold for a timed light switch, so its schedule cannot switch the light back during
    the minutes. One ``pending`` ``override`` command with ``args`` ``{kind: light_<light>_<state>, minutes}``, or
    ``{kind: light_<light>_auto, minutes: 0}`` to release the hold (``state`` None, Back to automatic); the ingestor
    sends it as ``manual_override`` under the writer fences, like a burst. No activity row of its own: the caller's
    API-20 row (or Back to automatic's write) is the record. Before the engine firmware there is no ``manual_override``,
    so nothing is stored and None comes back."""
    if state is not None and (minutes is None or not 1 <= minutes <= 120):
        raise ValueError(f"a light hold needs minutes 1-120, not {minutes!r}")
    if not await engine_on(conn):
        return None
    args = {"kind": f"light_{light}_{state or 'auto'}", "minutes": minutes if state is not None else 0}
    return await _insert(conn, actor, kind="override", line_id=None, args=args, reason=reason)


# ── Schedules (API-IRR-03 to -05) ───────────────────────────────────────────


def _schedule_spec(line_id: object, build: Callable[..., ScheduleSpec], *args: Any) -> ScheduleSpec:
    """A schedule from its fields, with ``unknown_target`` for a line that is not one and ``invalid_request`` for
    a shape the model refuses; the bounds per line kind are ``validate_schedule``'s."""
    if line_id not in LINE_IDS:
        raise _unknown_line(line_id)
    try:
        return build(*args)
    except ValidationError as exc:
        raise _invalid(exc) from exc


async def _stored_slots(conn: asyncpg.Connection) -> list[ScheduleSpec]:
    return [ScheduleSpec.from_row(row) for row in await conn.fetch(_SLOTS_SQL, GREENHOUSE_ID)]


async def _stored_lines(conn: asyncpg.Connection) -> list[LineSpec]:
    return [LineSpec.from_row(row) for row in await conn.fetch(_LINES_SQL, GREENHOUSE_ID)]


def _slot_values(spec: ScheduleSpec, actor: Actor, reason: str | None) -> tuple[Any, ...]:
    """The columns ``_INSERT_SLOT_SQL`` and ``_UPDATE_SLOT_SQL`` both start with."""
    return (
        spec.line_id,
        spec.enabled,
        spec.weekday_mask,
        spec.start.kind,
        spec.start.minute,
        spec.on_s,
        spec.count,
        spec.gap_s,
        spec.prewet_s,
        spec.flush_s,
        spec.degraded_only,
        spec.label,
        spec.manual,
        actor.source,
        actor.name,
        reason,
    )


def _slot_detail(
    slot_id: int,
    line_id: str,
    before: Mapping[str, Any] | None,
    value: Mapping[str, Any] | None,
    trigger_id: str | None,
    planner_instance: str | None,
) -> dict[str, Any]:
    """The activity detail of a schedule write: ``before`` and ``value`` whole, so a delete keeps every field and a
    replay can answer from the row it logged, whatever has happened to the schedule since."""
    return {
        "ref": {"table": "irrigation_slot", "id": slot_id},
        "line_id": line_id,
        "before": before,
        "value": value,
        "manual": {
            "before": None if before is None else before["manual"],
            "after": None if value is None else value["manual"],
        },
        "trigger_id": trigger_id,
        "planner_instance": planner_instance,
    }


async def _schedule_answer(
    conn: asyncpg.Connection,
    value: Mapping[str, Any],
    *,
    source: str,
    name: str,
    at: datetime,
    reason: str | None,
    manual_before: bool | None,
) -> Schedule:
    return Schedule(
        **value,
        manual_before=manual_before,
        sync=await reads.write_sync(conn, at),
        updated_by=ActorRef(source=source, name=name),
        updated_at=at,
        reason=reason,
    )


async def _load_schedule(conn: asyncpg.Connection, row: ActivityLogRow) -> Schedule:
    """A replay's stored object: the schedule as that write stored it (the row's ``ts`` is its ``updated_at``, both
    the transaction's ``now()``), so it answers even if the schedule has since been deleted."""
    value = row.detail.get("value")
    if not isinstance(value, Mapping):
        raise RuntimeError(f"activity row {row.id} does not hold the schedule it wrote")
    return await _schedule_answer(
        conn,
        value,
        source=row.source,
        name=row.actor,
        at=row.ts,
        reason=row.reason,
        manual_before=(row.detail.get("manual") or {}).get("before"),
    )


async def _stored_schedule_answer(
    conn: asyncpg.Connection, stored: Mapping[str, Any], value: Mapping[str, Any], manual_before: bool | None
) -> Schedule:
    return await _schedule_answer(
        conn,
        value,
        source=stored["source"],
        name=stored["updated_by"],
        at=stored["updated_at"],
        reason=stored["reason"],
        manual_before=manual_before,
    )


def _slot_key(slot_id: object) -> int:
    """A slot id the database can look up; anything else names no schedule."""
    low, high = SLOT_ID_RANGE
    if isinstance(slot_id, bool) or not isinstance(slot_id, int) or not low <= slot_id <= high:
        raise ServiceError(problem("unknown_target", f"schedule {slot_id!r} does not exist", field="slot_id"))
    return slot_id


async def schedule_create(
    conn: asyncpg.Connection,
    actor: Actor | None,
    fields: ScheduleCreate | Mapping[str, Any],
    manual: bool | None = None,
    reason: str | None = None,
    request_id: UUID | str | None = None,
    trigger_id: str | None = None,
    planner_instance: str | None = None,
) -> activity.WriteResult:
    """Create a schedule (API-IRR-03; MCP ``irrigation_schedule_set`` with no ``slot_id``).

    ``validate_schedule`` under the lock, with only its codes: ``unknown_target``, ``out_of_range``, ``too_close`` and
    ``too_many_schedules``. A schedule on a disabled line, or a mist schedule with ``schedule_member`` off, is stored;
    its runs settle at run time. ``slot_id`` comes from M3's never-reused sequence. ``manual`` is stored as given, and
    a create must give it. Returns ``WriteResult[Schedule]``; the REST answer is 201.
    """
    body = _parsed(ScheduleCreate, fields)
    _check_audit(trigger_id, planner_instance)
    _check_manual_flag(manual)
    if manual is None:
        raise ServiceError(problem("invalid_request", "A create must say whether it is manual", field="manual"))
    reason = reason if reason is not None else body.reason
    changes = _carried(body)
    target = _resolve_target(
        changes.pop("line_id", None), changes.pop("circuit_id", None), changes.pop("variant", None)
    )
    if target is None:
        raise ServiceError(
            problem("invalid_request", "Name the line: line_id, or circuit_id and variant", field="line_id")
        )
    changes["line_id"] = target

    async def perform(conn: asyncpg.Connection) -> tuple[Schedule, dict[str, Any]]:
        spec = _schedule_spec(changes["line_id"], ScheduleSpec.model_validate, {**changes, "manual": manual})
        refusal = validate_schedule(spec, await _stored_slots(conn), await _stored_lines(conn))
        if refusal is not None:
            raise _refused(refusal)
        stored = await conn.fetchrow(_INSERT_SLOT_SQL, *_slot_values(spec, actor, reason), GREENHOUSE_ID)
        await conn.execute(_NOTIFY_SQL)
        value = ScheduleSpec.from_row(stored).to_api()
        answer = await _stored_schedule_answer(conn, stored, value, None)
        return answer, _slot_detail(stored["slot_id"], spec.line_id, None, value, trigger_id, planner_instance)

    return await activity.recorded_write(
        conn,
        actor,
        op="API-IRR-03",
        action="irrigation.schedule.create",
        target="new",  # hashed; the activity row names the new slot (target_of)
        request=_hashed_request(body, manual, reason),
        request_id=request_id,
        reason=reason,
        perform=perform,
        lock=_lock,
        load=_load_schedule,
        target_of=lambda schedule: str(schedule.slot_id),
    )


async def schedule_update(
    conn: asyncpg.Connection,
    actor: Actor | None,
    slot_id: int,
    fields: ScheduleInput | Mapping[str, Any],
    manual: bool | None = None,
    reason: str | None = None,
    request_id: UUID | str | None = None,
    trigger_id: str | None = None,
    planner_instance: str | None = None,
) -> activity.WriteResult:
    """Edit, enable or disable a schedule (API-IRR-04; MCP ``irrigation_schedule_set``).

    Only the fields the body carries change, and the result is checked like a new schedule. ``manual`` None leaves the
    stored flag; Iris on a manual schedule is ``manual``. The answer carries ``manual_before``. Returns
    ``WriteResult[Schedule]``; the REST answer is 200.
    """
    body = _parsed(ScheduleInput, fields)
    _check_audit(trigger_id, planner_instance)
    _check_manual_flag(manual)
    reason = reason if reason is not None else body.reason
    changes = _carried(body)
    if manual is not None:
        changes["manual"] = manual

    async def perform(conn: asyncpg.Connection) -> tuple[Schedule, dict[str, Any]]:
        key = _slot_key(slot_id)
        slots = await _stored_slots(conn)
        before = next((slot for slot in slots if slot.slot_id == key), None)
        if before is None:
            raise ServiceError(problem("unknown_target", f"schedule {key} does not exist", field="slot_id"))
        _refuse_iris_on_manual(actor, before.manual, f"Schedule {key}", "slot_id")
        edit = dict(changes)
        target = _resolve_target(
            edit.pop("line_id", None),
            edit.pop("circuit_id", None),
            edit.pop("variant", None),
            current_line=before.line_id,
        )
        if target is not None:
            edit["line_id"] = target
        spec = _schedule_spec(edit.get("line_id", before.line_id), before.with_changes, edit)
        refusal = validate_schedule(spec, slots, await _stored_lines(conn))
        if refusal is not None:
            raise _refused(refusal)
        stored = await conn.fetchrow(_UPDATE_SLOT_SQL, *_slot_values(spec, actor, reason), key)
        await conn.execute(_NOTIFY_SQL)
        value = ScheduleSpec.from_row(stored).to_api()
        answer = await _stored_schedule_answer(conn, stored, value, before.manual)
        return answer, _slot_detail(key, spec.line_id, before.to_api(), value, trigger_id, planner_instance)

    return await activity.recorded_write(
        conn,
        actor,
        op="API-IRR-04",
        action="irrigation.schedule.update",
        target=str(slot_id),
        request=_hashed_request(body, manual, reason),
        request_id=request_id,
        reason=reason,
        perform=perform,
        lock=_lock,
        load=_load_schedule,
    )


async def schedule_delete(
    conn: asyncpg.Connection,
    actor: Actor | None,
    slot_id: int,
    reason: str | None = None,
    request_id: UUID | str | None = None,
    trigger_id: str | None = None,
    planner_instance: str | None = None,
) -> activity.WriteResult:
    """Delete a schedule (API-IRR-05; MCP ``irrigation_schedule_set`` with ``delete``): the row goes, its fields stay
    in the activity row's ``detail.before``, the ledger keeps its past runs and its ``slot_id`` is never reused. Iris
    on a manual schedule is ``manual``. Returns ``WriteResult[ScheduleDeleted]``; the REST answer is 204."""
    _check_audit(trigger_id, planner_instance)
    request = {} if reason is None else {"reason": reason}

    async def perform(conn: asyncpg.Connection) -> tuple[ScheduleDeleted, dict[str, Any]]:
        key = _slot_key(slot_id)
        before = next((slot for slot in await _stored_slots(conn) if slot.slot_id == key), None)
        if before is None:
            raise ServiceError(problem("unknown_target", f"schedule {key} does not exist", field="slot_id"))
        _refuse_iris_on_manual(actor, before.manual, f"Schedule {key}", "slot_id")
        await conn.execute(_DELETE_SLOT_SQL, key)
        await conn.execute(_NOTIFY_SQL)
        detail = _slot_detail(key, before.line_id, before.to_api(), None, trigger_id, planner_instance)
        return ScheduleDeleted(slot_id=key, manual_before=before.manual), detail

    async def load(conn: asyncpg.Connection, row: ActivityLogRow) -> ScheduleDeleted:
        manual_before = (row.detail.get("manual") or {}).get("before")
        return ScheduleDeleted(slot_id=int(row.target), manual_before=manual_before)

    return await activity.recorded_write(
        conn,
        actor,
        op="API-IRR-05",
        action="irrigation.schedule.delete",
        target=str(slot_id),
        request=request,
        request_id=request_id,
        reason=reason,
        perform=perform,
        lock=_lock,
        load=load,
    )


# ── Lines (API-IRR-08) ──────────────────────────────────────────────────────


def _stored_pause(paused_until: datetime | None, now: datetime) -> datetime | None:
    """What a pause stores: null, or a time at or before now, resumes. The last hours of the calendar, in a zone behind
    UTC, have no UTC form, so the database cannot take them."""
    if paused_until is None or paused_until <= now:
        return None
    try:
        return paused_until.astimezone(UTC)
    except OverflowError as exc:
        raise ServiceError(
            problem("invalid_request", "paused_until is past the last time that can be stored", field="paused_until")
        ) from exc


async def _update_every_line(
    conn: asyncpg.Connection,
    actor: Actor,
    changes: Mapping[str, Any],
    manual: bool | None,
    reason: str | None,
    audit: Mapping[str, Any],
) -> tuple[dict[str, bool], dict[str, Any]]:
    """``all``: pause or resume every line, and nothing else. The flag never changes, so ``manual`` is the same on
    both sides: whether any line holds it."""
    other = next((field for field in changes if field != "paused_until"), "manual" if manual is not None else None)
    if other is not None:
        raise ServiceError(problem("invalid_request", f"line_id all takes only paused_until, not {other}", field=other))
    if "paused_until" not in changes:
        raise ServiceError(problem("invalid_request", "line_id all needs paused_until", field="paused_until"))
    rows = await conn.fetch(_LINES_SQL, GREENHOUSE_ID)
    held = [row["line_id"] for row in rows if row["manual"]]
    if held and actor.source == "iris":
        raise ServiceError(
            problem(
                "manual",
                f"{', '.join(held)} held manual, so Iris pauses or resumes every line only once none is",
                field="line_id",
            )
        )
    stored = _stored_pause(changes["paused_until"], rows[0]["now"])
    await conn.execute(_PAUSE_EVERY_LINE_SQL, GREENHOUSE_ID, stored, actor.source, actor.name, reason)
    await conn.execute(_NOTIFY_SQL)
    any_manual = bool(held)
    return {row["line_id"]: row["manual"] for row in rows}, {
        "ref": {"table": "irrigation_line", "id": "all"},
        "line_id": "all",
        "lines_before": {
            row["line_id"]: {"paused_until": row["paused_until"], "manual": row["manual"]} for row in rows
        },
        "value": {"paused_until": stored},
        "paused_until": stored,
        "manual": {"before": any_manual, "after": any_manual},
        **audit,
    }


def _check_line_fields(
    actor: Actor, line_id: str, row: Mapping[str, Any], changes: Mapping[str, Any], *, records_check: bool
) -> None:
    """The refusals of a line update that need no number: which fields this line and this actor may carry, Iris and
    ``manual``, and the one-time check."""
    for field in ("vpd_member", "schedule_member"):
        if field in changes and line_id not in MISTER_MASK_BIT:
            raise ServiceError(problem("invalid_request", f"{field} is on L04, L07 and L09 only", field=field))
    # Effective VPD membership needs the line enabled too (firmware ``irr_vpd_zone_allowed``), so VPD control on a
    # disabled mister would read ON and change nothing (#1344): enable it in the same write, or first.
    if changes.get("vpd_member") is True and not changes.get("enabled", row["enabled"]):
        raise ServiceError(
            problem(
                "line_disabled",
                f"{line_id} is disabled, so VPD control would do nothing; enable the line first or in the same change",
                field="vpd_member",
            )
        )
    for field in ("checked", "check_note"):
        if field in changes and actor.source != "dashboard":
            raise ServiceError(problem("forbidden", "Only a person on the dashboard records a check", field=field))
    if "check_note" in changes and not records_check:
        raise ServiceError(problem("invalid_request", "A check note goes with checked: true", field="check_note"))
    _refuse_iris_on_manual(actor, row["manual"], line_id, "line_id")
    if row["checked_at"] is None and not records_check:
        for field in ("enabled", "schedule_member"):
            if changes.get(field) is True:
                raise ServiceError(
                    problem(
                        "check_required",
                        f"{line_id} has no one-time physical check, and turning {field} on needs one; a person "
                        "records it on the dashboard, then agents change the line freely",
                        field=field,
                    )
                )


async def _update_one_line(
    conn: asyncpg.Connection,
    actor: Actor,
    line_id: str,
    changes: Mapping[str, Any],
    manual: bool | None,
    reason: str | None,
    audit: Mapping[str, Any],
) -> tuple[dict[str, bool], dict[str, Any]]:
    rows = await conn.fetch(_LINES_SQL, GREENHOUSE_ID)
    row = next((row for row in rows if row["line_id"] == line_id), None)
    if row is None:
        raise _unknown_line(line_id)
    records_check = changes.get("checked") is True
    _check_line_fields(actor, line_id, row, changes, records_check=records_check)
    line = LineSpec.from_row(row)
    refusal = validate_line(
        line.with_changes({field: changes[field] for field in LINE_BOUNDS if field in changes}), before=line
    )
    if refusal is not None:
        raise _refused(refusal)

    columns = {field: changes[field] for field in sorted(LINE_DEFINITION_FIELDS) if field in changes}
    if "paused_until" in changes:
        columns["paused_until"] = _stored_pause(changes["paused_until"], row["now"])
    before, value = {field: row[field] for field in columns}, dict(columns)
    detail: dict[str, Any] = {}
    if "paused_until" in columns:
        detail["paused_until"] = columns["paused_until"]
    if manual is not None:
        columns["manual"] = manual
    if records_check:
        before["checked"], value["checked"] = row["checked_at"] is not None, True
        detail["check_note"] = changes.get("check_note")
        columns |= {"checked_by": actor.name, "checked_at": row["now"], "check_note": changes.get("check_note")}

    names = list(columns)
    assignments = [f"{name} = ${index}" for index, name in enumerate(names, start=5)]
    await conn.execute(
        "UPDATE public.irrigation_line SET "
        + ", ".join(["source = $2", "updated_by = $3", "reason = $4", "updated_at = now()", *assignments])
        + " WHERE line_id = $1",
        line_id,
        actor.source,
        actor.name,
        reason,
        *columns.values(),
    )
    await conn.execute(_NOTIFY_SQL)
    return {line_id: row["manual"]}, {
        "ref": {"table": "irrigation_line", "id": line_id},
        "line_id": line_id,
        "before": before,
        "value": value,
        "manual": {"before": row["manual"], "after": row["manual"] if manual is None else manual},
        **detail,
        **audit,
    }


def _manual_before_of(row: ActivityLogRow) -> dict[str, bool | None]:
    """Each changed line's flag before the write that logged ``row``: a replay's ``manual_before``."""
    every_line = row.detail.get("lines_before")
    if isinstance(every_line, Mapping):
        return {line_id: before.get("manual") for line_id, before in every_line.items()}
    return {row.target: (row.detail.get("manual") or {}).get("before")}


async def line_update(
    conn: asyncpg.Connection,
    actor: Actor | None,
    line_id: str,
    fields: LineUpdate | Mapping[str, Any],
    manual: bool | None = None,
    reason: str | None = None,
    request_id: UUID | str | None = None,
    trigger_id: str | None = None,
    planner_instance: str | None = None,
) -> activity.WriteResult:
    """Change a line (API-IRR-08; MCP ``irrigation_line_set``): its enable, mister flags, caps, pause and check.

    - ``enabled: true`` on a line with no check, or ``schedule_member: true`` on one, needs ``checked: true`` in the
      same body: ``check_required``. ``checked`` and ``check_note`` are a dashboard person's only (``forbidden`` from
      anyone else) and set ``checked_by``, ``checked_at`` and ``check_note``; a body with only the check records it.
    - ``vpd_member`` and ``schedule_member`` exist on L04, L07 and L09 only (``invalid_request``); the caps are
      ``validate_line``'s (``out_of_range``). ``vpd_member: true`` on a line that is, and stays, disabled is
      ``line_disabled``: the controller acts on VPD membership only while the line is enabled. ``paused_until`` null, or a time at or before now, resumes.
    - ``all`` takes only ``paused_until`` and ``reason``, for every line.
    - ``manual`` None leaves the stored flag; Iris on a manual line (for ``all``, while any line is manual) is
      ``manual``, a pause included.

    The changed lines are read after the activity row, in this transaction, so ``paused_by`` names this write.
    Returns ``WriteResult[LinesUpdated]``; the REST answer is 200.
    """
    body = _parsed(LineUpdate, fields)
    _check_audit(trigger_id, planner_instance)
    _check_manual_flag(manual)
    reason = reason if reason is not None else body.reason
    changes = _carried(body)
    audit = {"trigger_id": trigger_id, "planner_instance": planner_instance}
    target = str(line_id)

    async def perform(conn: asyncpg.Connection) -> tuple[dict[str, bool], dict[str, Any]]:
        if target == "all":
            return await _update_every_line(conn, actor, changes, manual, reason, audit)
        if target not in LINE_IDS:
            raise _unknown_line(line_id)
        return await _update_one_line(conn, actor, target, changes, manual, reason, audit)

    async def answer(conn: asyncpg.Connection, manual_before: Mapping[str, bool | None]) -> LinesUpdated:
        lines: Sequence[IrrigationLine] = await reads.lines(conn, actor, list(manual_before))
        return LinesUpdated(
            lines=[line.model_copy(update={"manual_before": manual_before[line.line_id]}) for line in lines]
        )

    async def load(conn: asyncpg.Connection, row: ActivityLogRow) -> LinesUpdated:
        return await answer(conn, _manual_before_of(row))

    return await activity.recorded_write(
        conn,
        actor,
        op="API-IRR-08",
        action="irrigation.line.update",
        target=target,
        request=_hashed_request(body, manual, reason),
        request_id=request_id,
        reason=reason,
        perform=perform,
        lock=_lock,
        load=load,
        finish=answer,
    )
