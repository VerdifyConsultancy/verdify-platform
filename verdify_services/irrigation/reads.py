"""The irrigation reads: API-IRR-01, -02, -10 and -11 (VD-40, #1155; irrigation §6.2 and §8.1).

Every function is async, takes an asyncpg connection and an ``Actor`` and returns a Pydantic model that matches the
committed OpenAPI schema of the same name (docs/design/dashboard/openapi/dashboard-api.v1.yaml). The operator API
routes (``api/operator_routes/irrigation_reads.py``) and the MCP read tools (#1173) return the same models from these
functions; the roles are the adapters' to check, so the actor changes nothing here.

- ``status``: API-IRR-01, in one read-only snapshot, with ``lines``, the reader API-IRR-08's response reuses (#1156),
  and ``write_sync``, which API-IRR-03's and -04's responses use the same way.
- ``agenda``: API-IRR-02, the week planned against actual.
- ``minutes``: API-IRR-10, from M4's bounded ``fn_irrigation_minutes_daily``.
- ``runs``: API-IRR-11, the ended runs, newest first.
- ``run_causes`` (``verdify_services.irrigation.runs``): the same join, for API-05 and API-02.

Read-only: no writes, no NOTIFY, no device path and no Home Assistant call. Unknown is null, never 0; gaps stay gaps;
gallons are whole. Everything here reads what both the dashboard and the MCP logins can read: M1 (``activity_log``,
``v_desired_state_current``), M3 (the irrigation tables, ``v_irrigation_line_state``, ``v_water_event_attribution``),
M4 and migration 259's ``alert_log``, ``climate``, ``equipment_state``, ``setpoint_snapshot`` and ``system_state``.
Never ``water_meter_events`` or ``v_open_alerts``, which the MCP login cannot read. Every hypertable query is bounded
by ``ts`` or walks an index to one row.
"""

from __future__ import annotations

import base64
import json
from collections import defaultdict
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Literal

import asyncpg
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, SerializerFunctionWrapHandler, model_serializer

from verdify_schemas import irrigation as irr
from verdify_schemas.irrigation import (
    MAX_SCHEDULES,
    REASON_WORDS,
    CompiledProgram,
    IrrigationCause,
    IrrState,
    LineSpec,
    LineState,
    RunOutcome,
    ScheduleSpec,
    SolarDay,
    bounds_by_kind,
    compile_program,
    line_state,
    site_values,
    unpack_program,
)
from verdify_schemas.tunable_registry import IRRIGATION_SITE_PARAMS, REGISTRY
from verdify_services import alerts
from verdify_services import reads as core
from verdify_services.actor import Actor, ActorRef
from verdify_services.irrigation.program import (
    ENGINE_ENTITY,
    NEWEST_STATE_WINDOW,
    ControllerProgram,
    controller_program,
    engine_flag,
)
from verdify_services.irrigation.runs import (
    GREENHOUSE_ID,
    LEDGER_COLUMNS,
    LINE_RELAY,
    MAX_DELTA_SPAN,
    MINUTES_ZONE,
    AttributedRun,
    DayGroup,
    Deltas,
    RunCause,
    RunGroup,
    attributed_runs,
    chemistry,
    day_groups,
    deltas_between,
    group_runs,
    pulsing,
    run_causes,
    run_gallons,
)
from verdify_services.labels import line_name
from verdify_services.problems import ServiceError, problem
from verdify_services.reads import Leak, ReadbackState

__all__ = [
    "IrrigationLine",
    "IrrigationStatus",
    "MinutesReport",
    "Run",
    "RunCause",
    "RunPage",
    "ScheduleAgenda",
    "agenda",
    "lines",
    "minutes",
    "run_causes",
    "runs",
    "status",
    "write_sync",
]

NOT_ON_CONTROLLER = "engine not on controller"
ARRIVES_WITH_FIRMWARE = "arrives with the irrigation firmware"
NO_OUTCOME = "no outcome received"
# A write or a push reads requested this long before it reads not_confirmed (architecture §5.5).
REQUESTED_FOR = timedelta(minutes=5)
MINUTES_MAX_DAYS = 400
RUNS_MAX_DAYS = 31
LIMIT_DEFAULT = 50
LIMIT_MAX = 200
SAF4_PARAM = "mister_daily_volume_max_gal"
SOFT_BUDGET_PARAM = "mister_water_budget_gal"
OCCUPANCY_FLAG = "occupancy"
OCCUPANCY_HOLD_FLAG = "occupancy_inhibit"
# The zones of API-IRR-01's today (L03's supply minutes are in none). Fog is climate only, so it always reports.
ZONE_LINES: Mapping[str, tuple[str, ...]] = {
    "wall": ("L01", "L02"),
    "center": ("L04", "L05", "L06"),
    "south": ("L07", "L08"),
    "west": ("L09", "L10"),
    "fog": ("L11",),
}
DRIP_MANIFOLD = ("L01", "L02", "L05", "L06")
MIST_MANIFOLD = ("L04", "L07", "L08", "L09", "L10")
# A run's own episode is read back at most this far from its newest ON row; no VPD episode comes near it.
LAST_WATER_WINDOW = timedelta(hours=24)
# Past this a ledger row whose end never arrived stops holding its run open (the bound M3's join uses).
LEDGER_OPEN_FOR = timedelta(hours=24)
_MICROSECOND = timedelta(microseconds=1)
_DAY = timedelta(days=1)

GroupBy = Literal["zone", "line", "cause"]
Chemistry = Literal["clean", "fert"]


# ── Response models (the committed OpenAPI schemas) ─────────────────────────


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UsedOfMax(_Model):
    used: float | None
    max: float | None


class NextRun(_Model):
    line_id: str
    slot_id: int
    start_at: AwareDatetime
    label: str | None = None
    minutes: float | None = None


class ProgramSync(_Model):
    """The compiled program against the controller (OpenAPI ``ProgramSync``)."""

    state: ReadbackState
    revision: int | None = None
    desired_sha256: str
    applied_revision: int | None = None
    applied_hash: str | None = None
    src: Literal["nvs", "rom"] | None
    reason: str | None = None
    pushed_at: AwareDatetime | None = None
    confirmed_at: AwareDatetime | None = None
    reject_code: str | None = None
    next_retry_at: AwareDatetime | None = None


class LineCheck(_Model):
    checked_by: str
    checked_at: AwareDatetime
    note: str | None = None


class LineToday(_Model):
    minutes: float | None = None
    pulses: int | None = None
    gal: int | None = None


class LastWater(_Model):
    at: AwareDatetime
    open_s: int
    pulses: int | None = None
    cause: IrrigationCause


class LastOutcome(_Model):
    outcome: RunOutcome
    at: AwareDatetime
    reason: str | None = None


class IrrigationLine(_Model):
    """One line as the board shows it (OpenAPI ``IrrigationLine``). ``manual_before`` is set on write responses."""

    line_id: str
    name: str
    zone: Literal["wall", "center", "south", "west", "supply", "house"]
    kind: Literal["drip", "mister", "fert_drip", "fert_mister", "master", "fog"]
    clean_partner: str | None = None
    reserved: bool
    enabled: bool
    check: LineCheck | None
    vpd_member: bool | None = None
    schedule_member: bool | None = None
    vpd_source: Literal["own", "peer", "house"] | None = None
    daily_cap_min: int
    vpd_reserve_min: int | None = None
    paused_until: AwareDatetime | None
    paused_by: ActorRef | None = None
    pause_reason: str | None = None
    fault: Literal["backing_off", "mister_fault"] | None = None
    fault_until: AwareDatetime | None = None
    manual: bool
    manual_before: bool | None = None
    state: LineState
    today: LineToday
    next_run: NextRun | None = None
    last_water: LastWater | None = None
    last_outcome: LastOutcome | None = None
    sync: ReadbackState


class ActiveRun(_Model):
    manifold: Literal["drip", "mister"]
    line_id: str
    cause: IrrigationCause
    slot_id: int | None = None
    started_at: AwareDatetime
    ends_at: AwareDatetime | None = None
    pulses_done: int | None = None
    pulses_total: int | None = None


class TodayMinutes(_Model):
    date: date
    zones: dict[str, float | None]


class Limits(_Model):
    saf4_gal: UsedOfMax
    soft_budget_gal: UsedOfMax


class Occupancy(_Model):
    occupied: bool | None
    seen_at: AwareDatetime | None
    hold_enabled: bool | None = None


class StateFreshness(_Model):
    """``Freshness`` of a change-only source: it has no cadence, so ``cadence_s`` is left out."""

    observed_at: AwareDatetime | None
    age_s: int | None
    stale: bool


class IrrigationStatus(_Model):
    server_time: AwareDatetime
    engine: bool
    program: ProgramSync
    lines: list[IrrigationLine]
    active_runs: list[ActiveRun]
    today: TodayMinutes
    limits: Limits
    leak: Leak
    occupancy: Occupancy
    fallback_active: bool
    freshness: StateFreshness


class ScheduleStart(_Model):
    kind: Literal["clock", "sunrise", "solar_noon", "sunset"]
    clock: str | None = None
    offset_min: int | None = None

    @model_serializer(mode="wrap")
    def _only_set(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        return {key: value for key, value in handler(self).items() if value is not None}


class Schedule(_Model):
    """One ``irrigation_slot`` row with its sync (OpenAPI ``Schedule``). ``ScheduleInput`` types ``label`` and
    ``reason`` as strings, so a row with neither leaves them out rather than sending null."""

    slot_id: int
    line_id: str
    label: str | None = None
    enabled: bool
    days: list[Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]]
    start: ScheduleStart
    on_s: int
    count: int
    gap_s: int
    prewet_s: int
    flush_s: int
    degraded_only: bool
    manual: bool
    manual_before: bool | None = None
    sync: ReadbackState
    updated_by: ActorRef
    updated_at: AwareDatetime
    reason: str | None = None

    @model_serializer(mode="wrap")
    def _no_null_strings(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        return {
            key: value for key, value in handler(self).items() if value is not None or key not in ("label", "reason")
        }


class Occurrence(_Model):
    slot_id: int
    line_id: str
    planned_start: AwareDatetime
    status: Literal["planned", "skipped", "blocked", "running", "done"]
    outcome: RunOutcome | None = None
    reason: str | None = None
    run_id: str | None = None


class AgendaCounts(_Model):
    schedules: int
    max_schedules: Literal[24]


class BoundRange(_Model):
    min: float
    max: float


class ScheduleAgenda(_Model):
    schedules: list[Schedule]
    occurrences: list[Occurrence]
    counts: AgendaCounts
    bounds: dict[str, dict[str, BoundRange]]
    solar: list[SolarDay]


class MinutesPeriod(_Model):
    start: date
    coverage: Literal["complete", "partial", "no_data"]


class MinutesRow(_Model):
    period: date
    group: str
    cause: IrrigationCause
    chemistry: Chemistry
    minutes: float
    pulses: int | None = None
    gal: int | None = None


class MinutesReport(BaseModel):
    """``from`` is a Python keyword: the field is ``from_`` and serializes as ``from``."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)

    from_: date = Field(alias="from")
    to: date
    group_by: GroupBy
    periods: list[MinutesPeriod]
    rows: list[MinutesRow]


class Run(_Model):
    run_id: str
    line_id: str
    zone: str
    chemistry: Chemistry
    cause: IrrigationCause
    slot_id: int | None = None
    schedule_label: str | None = None
    command_id: str | None = None
    actor: ActorRef | None = None
    started_at: AwareDatetime
    ended_at: AwareDatetime | None = None
    open_s: int
    pulses: int | None = None
    gal: int | None = None
    outcome: RunOutcome
    reason: str | None = None


class RunPage(_Model):
    items: list[Run]
    next_cursor: str | None = None


# ── Pure rules ──────────────────────────────────────────────────────────────


def program_state(
    *,
    engine: bool,
    desired_sha256: str,
    applied_hash: str | None,
    desired_push: Mapping[str, Any] | None,
    at: datetime,
) -> ReadbackState:
    """``ProgramSync.state``: ``confirmed`` while the desired and applied hashes match; else ``requested`` until a
    push of the desired hash exists, then ``not_confirmed`` once it is rejected or 5 min after its ``pushed_at``.
    With no engine it stays ``requested``."""
    if not engine:
        return "requested"
    if applied_hash is not None and desired_sha256.startswith(applied_hash):
        return "confirmed"
    if desired_push is None or desired_push["pushed_at"] is None:
        return "requested"
    if desired_push["reject_code"] is not None or at - desired_push["pushed_at"] >= REQUESTED_FOR:
        return "not_confirmed"
    return "requested"


def item_sync(
    updated_at: datetime,
    *,
    engine: bool,
    desired_sha256: str,
    applied_hash: str | None,
    applied_pushed_at: datetime | None,
    at: datetime,
) -> ReadbackState:
    """A line's or schedule's ``sync``: ``confirmed`` while the desired hash equals the applied hash, so an edit that
    does not change the record confirms at once, or when the item's write precedes the applied push; otherwise
    ``requested``, then ``not_confirmed`` 5 min after the write. Before the engine always ``requested``."""
    if not engine:
        return "requested"
    if applied_hash is not None and desired_sha256.startswith(applied_hash):
        return "confirmed"
    if applied_pushed_at is not None and updated_at < applied_pushed_at:
        return "confirmed"
    return "not_confirmed" if at - updated_at >= REQUESTED_FOR else "requested"


def reason_words(code: str | None) -> str | None:
    """A ledger ``reason`` (a ``REASON_WORDS`` code from ``irr_events``) in words; other text stands as it is."""
    return None if code is None else REASON_WORDS.get(code, code)


def _when(text: object) -> datetime | None:
    """An aware time from an ``activity_log`` detail, or None."""
    if not isinstance(text, str):
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.utcoffset() is not None else None


def pause_row(rows: Iterable[Mapping[str, Any]], line_id: str, moment: datetime) -> Mapping[str, Any] | None:
    """The newest ``irrigation.line.update`` row with target the line or ``all`` that sets ``paused_until``, as of
    ``moment``; ``rows`` are newest first. A resume is such a row too, and it ends a pause."""
    for row in rows:
        if row["ts"] <= moment and row["target"] in (line_id, "all"):
            return row
    return None


def pause_in_force(rows: Iterable[Mapping[str, Any]], line_id: str, moment: datetime) -> Mapping[str, Any] | None:
    """The pause row in force at ``moment``: ``pause_row()`` when it holds the line past ``moment``, else None."""
    row = pause_row(rows, line_id, moment)
    if row is None:
        return None
    until = _when(row["paused_until"])
    return row if until is not None and until > moment else None


def pause_words(row: Mapping[str, Any]) -> str:
    """ "line paused by <name>: <reason>", the reason left out when none was given."""
    return f"line paused by {row['actor']}: {row['reason']}" if row["reason"] else f"line paused by {row['actor']}"


def engine_at(rows: Sequence[tuple[datetime, bool]], moment: datetime) -> bool:
    """Whether the ``irr_engine`` row in effect at ``moment`` says the engine; none in effect is no engine. ``rows``
    are oldest first."""
    state = False
    for ts, value in rows:
        if ts > moment:
            break
        state = value
    return state


def _manifold_of(line_id: str) -> Literal["drip", "mister"] | None:
    if line_id in DRIP_MANIFOLD:
        return "drip"
    return "mister" if line_id in MIST_MANIFOLD else None


def _epoch_time(epoch: int) -> datetime:
    return datetime.fromtimestamp(epoch, UTC)


def _next_midnight(day: date) -> datetime:
    return datetime.combine(day + _DAY, time(0), tzinfo=core.DENVER)


def _edge_run_id(line_id: str, start: datetime) -> str:
    return f"{line_id}-{(start - datetime(1970, 1, 1, tzinfo=UTC)) // _MICROSECOND}"


def _ledger_run_id(row: Mapping[str, Any]) -> str:
    return f"ledger-{row['id']}"


def encode_run_cursor(item: Run) -> str:
    raw = json.dumps([item.started_at.isoformat(), item.run_id], separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("ascii")).rstrip(b"=").decode("ascii")


def decode_run_cursor(cursor: str) -> tuple[datetime, str]:
    """The (``started_at``, ``run_id``) the previous page ended on."""
    try:
        started_text, run_id = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        started = datetime.fromisoformat(started_text)
        if started.utcoffset() is None or not isinstance(run_id, str):
            raise ValueError("not a cursor")
    except (TypeError, ValueError) as exc:  # binascii.Error, JSONDecodeError and UnicodeDecodeError included
        raise ServiceError(
            problem("invalid_request", "cursor is not a next_cursor of this list", field="cursor")
        ) from exc
    return started, run_id


# ── Database (parameters only, never text from a request) ───────────────────

# Every irrigation_line column of v_irrigation_line_state that a line, its LineSpec or its state needs.
_LINE_STATE_SQL = """
SELECT line_id, name, zone, kind, device_idx, clean_partner, reserved, enabled, vpd_member, schedule_member,
       daily_cap_min, vpd_reserve_min, paused_until, manual, checked_by, checked_at, check_note, updated_at,
       relay_on, relay_at, irr_state, irr_state_at
  FROM v_irrigation_line_state
 WHERE greenhouse_id = $1
 ORDER BY device_idx
"""

_SLOTS_SQL = """
SELECT slot_id, line_id, enabled, days, start_kind, start_min, on_s, count, gap_s, prewet_s, flush_s, degraded_only,
       label, manual, source, updated_by, updated_at, reason, created_at
  FROM irrigation_slot
 WHERE greenhouse_id = $1
 ORDER BY slot_id
"""

_SITE_SQL = """
SELECT id, param, value, created_at
  FROM v_desired_state_current
 WHERE greenhouse_id = $1 AND param = ANY($2::text[])
"""

_PUSH_COLUMNS = "revision, sha256, site_desired_at, pushed_at, confirmed_at, reject_code"
_NEWEST_PUSH_SQL = f"""
SELECT {_PUSH_COLUMNS} FROM irrigation_push WHERE greenhouse_id = $1 ORDER BY revision DESC LIMIT 1
"""
_DESIRED_PUSH_SQL = f"""
SELECT {_PUSH_COLUMNS} FROM irrigation_push WHERE greenhouse_id = $1 AND sha256 = $2 ORDER BY revision DESC LIMIT 1
"""

_NEWEST_ON_SQL = """
SELECT w.equipment, n.ts
  FROM unnest($1::text[]) AS w(equipment)
 CROSS JOIN LATERAL (SELECT es.ts FROM equipment_state AS es
                      WHERE es.equipment = w.equipment AND es.state
                        AND coalesce(es.greenhouse_id, 'vallery') = $2
                      ORDER BY es.ts DESC
                      LIMIT 1) AS n
"""

_LAST_OUTCOMES_SQL = """
SELECT DISTINCT ON (line_id) line_id, outcome, reason, coalesce(ended_at, started_at, recorded_at) AS at
  FROM irrigation_ledger
 WHERE greenhouse_id = $1 AND (ended_at IS NOT NULL OR outcome IS NOT NULL)
 ORDER BY line_id, coalesce(ended_at, started_at, recorded_at) DESC, id DESC
"""

_PAUSE_ROWS_SQL = """
SELECT ts, source, actor, target, reason, detail ->> 'paused_until' AS paused_until
  FROM activity_log
 WHERE greenhouse_id = $1 AND action = 'irrigation.line.update' AND detail ? 'paused_until'
   AND ts <= now()
 ORDER BY ts DESC, id DESC
"""

# The newest climate readings and meter deltas while the controller reports, and the soft budget's readback.
_WATER_NOW_SQL = """
SELECT (SELECT mister_water_today FROM climate
         WHERE ts > now() - interval '10 minutes' AND mister_water_today IS NOT NULL
         ORDER BY ts DESC LIMIT 1) AS water_today,
       (SELECT flow_gpm FROM climate
         WHERE ts > now() - interval '10 minutes' AND flow_gpm IS NOT NULL
         ORDER BY ts DESC LIMIT 1) AS flow_gpm,
       (SELECT round(sum(delta_gal))::int FROM v_water_event_attribution
         WHERE greenhouse_id = $1 AND ts > now() - interval '10 minutes') AS gal_10min,
       (SELECT value FROM setpoint_snapshot
         WHERE parameter = $2 AND band_role IS NULL AND ts > now() - interval '24 hours'
         ORDER BY ts DESC LIMIT 1) AS soft_budget_max
"""

# API-14's merged id and the newest repeat, read from alert_log under v_open_alerts' predicate (alerts.list).
_LEAK_ALERT_SQL = f"""
SELECT (SELECT id FROM alert_log WHERE alert_type = $1 AND {alerts.OPEN_PREDICATE} ORDER BY ts, id LIMIT 1)
           AS leak_alert_id,
       (SELECT max(ts) FROM alert_log WHERE alert_type = $1 AND {alerts.OPEN_PREDICATE}) AS leak_open_since
"""

# Each irr_engine row of a window and the newest before it, oldest first.
_ENGINE_ROWS_SQL = """
SELECT ts, value FROM (
    (SELECT ts, value FROM system_state
      WHERE entity = $1 AND coalesce(greenhouse_id, 'vallery') = $2 AND ts < $3
        AND ts > $3::timestamptz - $4::interval
      ORDER BY ts DESC LIMIT 1)
    UNION ALL
    (SELECT ts, value FROM system_state
      WHERE entity = $1 AND coalesce(greenhouse_id, 'vallery') = $2 AND ts >= $3)
) AS rows
ORDER BY ts
"""

_OCCURRENCE_LEDGER_SQL = """
SELECT id, line_id, slot_id, local_day, cause, outcome, reason
  FROM irrigation_ledger
 WHERE greenhouse_id = $1 AND slot_id IS NOT NULL AND local_day BETWEEN $2 AND $3
 ORDER BY id
"""

_MINUTES_SQL = """
SELECT local_day, zone, line_id, cause, chemistry, minutes, pulses, gal
  FROM fn_irrigation_minutes_daily($1, $2)
 WHERE greenhouse_id = $3
"""

_COVERAGE_SQL = """
SELECT local_day, coverage
  FROM v_irrigation_day_coverage
 WHERE greenhouse_id = $1 AND local_day BETWEEN $2 AND $3
"""

# Ledger rows that opened no valve carry no started_at; they sit on their local_day, and a solar start past midnight
# falls on the morning after it.
_NO_START_LEDGER_SQL = f"""
SELECT {LEDGER_COLUMNS}
  FROM irrigation_ledger
 WHERE greenhouse_id = $1 AND started_at IS NULL AND local_day BETWEEN $2 AND $3
 ORDER BY id
"""

_WINDOW_LEDGER_SQL = f"""
SELECT {LEDGER_COLUMNS}
  FROM irrigation_ledger
 WHERE greenhouse_id = $1 AND started_at >= $2::timestamptz AND started_at < $3::timestamptz
 ORDER BY id
"""

_COMMANDS_SQL = "SELECT command_id, source, actor FROM irrigation_command WHERE command_id = ANY($1::uuid[])"
_SLOT_RULES_SQL = """
SELECT slot_id, line_id, label, start_kind, start_min FROM irrigation_slot WHERE slot_id = ANY($1::int[])
"""

# Did the meter report over each run? A climate row with a meter reading from its start to one delta span past its
# end; each probe is a one-row index walk bounded by ts.
_METER_REPORTED_SQL = """
SELECT r.i, EXISTS (SELECT 1 FROM climate AS c
                     WHERE c.ts >= r.low AND c.ts <= r.high AND c.water_total_gal IS NOT NULL) AS reported
  FROM unnest($1::int[], $2::timestamptz[], $3::timestamptz[]) AS r(i, low, high)
"""


@asynccontextmanager
async def _snapshot(conn: asyncpg.Connection) -> AsyncIterator[None]:
    """One read-only snapshot for a whole read, so every ``now()`` in it agrees."""
    if conn.is_in_transaction():
        yield
        return
    async with conn.transaction(isolation="repeatable_read", readonly=True):
        yield


@dataclass(frozen=True)
class _Base:
    """What the status, the lines and the agenda all read."""

    reporting: core.Reporting
    line_rows: Sequence[Mapping[str, Any]]
    slot_rows: Sequence[Mapping[str, Any]]
    line_specs: tuple[LineSpec, ...]
    slot_specs: tuple[ScheduleSpec, ...]
    compiled: CompiledProgram
    controller: ControllerProgram
    newest_push: Mapping[str, Any] | None
    desired_push: Mapping[str, Any] | None

    @property
    def at(self) -> datetime:
        return self.reporting.at

    @property
    def engine(self) -> bool:
        return self.controller.engine

    @property
    def applied_hash(self) -> str | None:
        return None if self.controller.program_id is None else self.controller.program_id.hash16

    def sync_of(self, updated_at: datetime) -> ReadbackState:
        confirmed = self.controller.confirmed
        return item_sync(
            updated_at,
            engine=self.engine,
            desired_sha256=self.compiled.sha256,
            applied_hash=self.applied_hash,
            applied_pushed_at=None if confirmed is None else confirmed["pushed_at"],
            at=self.at,
        )


async def _base(conn: asyncpg.Connection) -> _Base:
    reporting = await core.reporting(conn)
    line_rows = await conn.fetch(_LINE_STATE_SQL, GREENHOUSE_ID)
    slot_rows = await conn.fetch(_SLOTS_SQL, GREENHOUSE_ID)
    site_rows = await conn.fetch(_SITE_SQL, GREENHOUSE_ID, sorted(IRRIGATION_SITE_PARAMS))
    line_specs = tuple(LineSpec.from_row(row) for row in line_rows)
    slot_specs = tuple(ScheduleSpec.from_row(row) for row in slot_rows)
    compiled = compile_program(line_specs, slot_specs, site_values(site_rows))
    controller = await controller_program(conn)
    newest_push = await conn.fetchrow(_NEWEST_PUSH_SQL, GREENHOUSE_ID)
    desired_push = await conn.fetchrow(_DESIRED_PUSH_SQL, GREENHOUSE_ID, compiled.sha256)
    return _Base(
        reporting, line_rows, slot_rows, line_specs, slot_specs, compiled, controller, newest_push, desired_push
    )


# ── API-IRR-01: lines ───────────────────────────────────────────────────────


def _line_today(groups: Iterable[DayGroup], line_id: str, *, no_data: bool) -> LineToday:
    """A line's minutes, pulses and whole gallons, from the rows API-IRR-10 gives for today. A day with no data is a
    gap, null; a line that did not run moved no water, so it reads 0 (L03's water counts on the line it feeds)."""
    if no_data:
        return LineToday()
    own = [group for group in groups if group.line_id == line_id]
    gallons = [group.gal for group in own if group.gal is not None]
    if own:
        gal = sum(gallons) if gallons else None
    else:
        gal = None if line_id == "L03" else 0
    return LineToday(
        minutes=float(sum((group.minutes for group in own), Decimal(0))),
        pulses=sum(group.pulses for group in own),
        gal=gal,
    )


def _zones(groups: Iterable[DayGroup], enabled: Mapping[str, bool], *, no_data: bool) -> dict[str, float | None]:
    """Today's minutes by zone: null for a day with no data, and for a watering zone with no enabled line."""
    zones: dict[str, float | None] = {}
    groups = list(groups)
    for zone, line_ids in ZONE_LINES.items():
        if no_data or (zone != "fog" and not any(enabled.get(line_id) for line_id in line_ids)):
            zones[zone] = None
        else:
            zones[zone] = float(sum((group.minutes for group in groups if group.line_id in line_ids), Decimal(0)))
    return zones


async def _last_water(conn: asyncpg.Connection, line_ids: Sequence[str], at: datetime) -> dict[str, LastWater]:
    """Each line's newest run that opened a valve, the one in progress and VPD episodes included, found back from the
    newest ON row of its relay. A line that never ran has none."""
    rows = await conn.fetch(_NEWEST_ON_SQL, [LINE_RELAY[line_id] for line_id in line_ids], GREENHOUSE_ID)
    newest = {row["equipment"]: row["ts"] for row in rows}
    windows = {
        line_id: (newest[LINE_RELAY[line_id]] - LAST_WATER_WINDOW, newest[LINE_RELAY[line_id]] + _MICROSECOND)
        for line_id in line_ids
        if LINE_RELAY[line_id] in newest
    }
    newest_runs: dict[str, RunGroup] = {}
    for group in group_runs(await attributed_runs(conn, windows, at)):
        if group.line_id not in newest_runs or group.end > newest_runs[group.line_id].end:
            newest_runs[group.line_id] = group
    return {
        line_id: LastWater(at=group.start, open_s=group.open_s, pulses=group.pulses, cause=IrrigationCause(group.cause))
        for line_id, group in newest_runs.items()
    }


def _next_runs(base: _Base, today: date) -> dict[str, NextRun]:
    """Each line's next planned occurrence, 8 days ahead so a weekly schedule past its time today still has one.
    There is none before the engine."""
    if not base.engine:
        return {}
    labels = {row["slot_id"]: row["label"] for row in base.slot_rows}
    out: dict[str, NextRun] = {}
    for first, days in ((today, 7), (today + timedelta(days=7), 1)):
        plan = irr.agenda(base.line_specs, base.slot_specs, today=first, days=days)
        for item in plan.occurrences:
            if item.status == "planned" and item.planned_start > base.at and item.line_id not in out:
                out[item.line_id] = NextRun(
                    line_id=item.line_id,
                    slot_id=item.slot_id,
                    start_at=item.planned_start,
                    label=labels.get(item.slot_id),
                    minutes=item.minutes,
                )
    return out


@dataclass(frozen=True)
class _LineRead:
    lines: list[IrrigationLine]
    groups: list[DayGroup]
    no_data_today: bool
    today: date
    open_runs: dict[str, AttributedRun]


async def _read_lines(conn: asyncpg.Connection, base: _Base, line_ids: Sequence[str] | None) -> _LineRead:
    """The ``IrrigationLine`` of each wanted line (every line by default), and today's rows they were counted from."""
    at, rep = base.at, base.reporting
    wanted = [row["line_id"] for row in base.line_rows if line_ids is None or row["line_id"] in line_ids]
    local_midnight = core.local_midnight(at)
    today = local_midnight.date()
    midnight = local_midnight.astimezone(UTC)  # in UTC, so a difference of two day bounds keeps a 23- or 25-hour day
    items = await attributed_runs(conn, {line_id: (midnight, at) for line_id in wanted}, at)
    deltas = await deltas_between(conn, midnight - _MICROSECOND, at)
    groups = day_groups(items, midnight, _next_midnight(today).astimezone(UTC), deltas)
    # No climate row today: API-IRR-10 reads the day no_data, so it is a gap here too.
    no_data_today = rep.climate_at is None or rep.climate_at < midnight
    open_runs = {item.run.line_id: item for item in items if item.run.open}
    last_water = await _last_water(conn, wanted, at)
    last_outcomes = {row["line_id"]: row for row in await conn.fetch(_LAST_OUTCOMES_SQL, GREENHOUSE_ID)}
    next_runs = _next_runs(base, today)
    paused = [
        row["line_id"]
        for row in base.line_rows
        if row["line_id"] in wanted and row["paused_until"] is not None and row["paused_until"] > at
    ]
    pause_rows = await conn.fetch(_PAUSE_ROWS_SQL, GREENHOUSE_ID) if paused else []

    lines_out: list[IrrigationLine] = []
    for row in base.line_rows:
        line_id = row["line_id"]
        if line_id not in wanted:
            continue
        open_run = open_runs.get(line_id)
        source: Mapping[str, Any] = row
        if open_run is not None and rep.controller_reporting:
            # The view's relay_at is the newest row, which a reconnect snapshot moves; the run's start is the ON edge.
            source = {**dict(row), "relay_at": open_run.run.start}
        bridge = None if open_run is None or open_run.bridge is None else open_run.bridge["sent_at"]
        fields = line_state(
            source, engine=base.engine, reporting=rep.controller_reporting, now=at, bridge_run_at=bridge
        )
        pause = pause_row(pause_rows, line_id, at) if line_id in paused else None
        outcome = last_outcomes.get(line_id)
        lines_out.append(
            IrrigationLine(
                line_id=line_id,
                # The operator's name for the line (#1341), not the relay register's "Drip • Wall".
                name=line_name(line_id),
                zone=row["zone"],
                kind=row["kind"],
                clean_partner=row["clean_partner"],
                reserved=row["reserved"],
                enabled=row["enabled"],
                check=(
                    None
                    if row["checked_at"] is None
                    else LineCheck(checked_by=row["checked_by"], checked_at=row["checked_at"], note=row["check_note"])
                ),
                vpd_member=row["vpd_member"],
                schedule_member=row["schedule_member"],
                vpd_source=fields.vpd_source,
                daily_cap_min=row["daily_cap_min"],
                vpd_reserve_min=row["vpd_reserve_min"],
                paused_until=row["paused_until"],
                paused_by=None if pause is None else ActorRef(source=pause["source"], name=pause["actor"]),
                pause_reason=None if pause is None else pause["reason"],
                fault=fields.fault,
                fault_until=fields.fault_until,
                manual=row["manual"],
                state=fields.state,
                today=_line_today(groups, line_id, no_data=no_data_today),
                next_run=next_runs.get(line_id),
                last_water=last_water.get(line_id),
                last_outcome=(
                    None
                    if outcome is None
                    else LastOutcome(
                        outcome=outcome["outcome"] or RunOutcome.UNKNOWN,
                        at=outcome["at"],
                        reason=reason_words(outcome["reason"]),
                    )
                ),
                sync=base.sync_of(row["updated_at"]),
            )
        )
    return _LineRead(lines_out, groups, no_data_today, today, open_runs)


async def lines(
    conn: asyncpg.Connection, actor: Actor | None, line_ids: Sequence[str] | None = None
) -> list[IrrigationLine]:
    """The line reader: each ``v_irrigation_line_state`` row through ``line_state()`` with its check, flags, caps,
    pause, today, next run, last water, last outcome and sync. API-IRR-08's response (#1156) reuses it, inside its
    write transaction, so ``paused_by`` names that write."""
    async with _snapshot(conn):
        base = await _base(conn)
        return (await _read_lines(conn, base, line_ids)).lines


async def write_sync(conn: asyncpg.Connection, updated_at: datetime) -> ReadbackState:
    """The ``sync`` of a schedule written at ``updated_at``, as of now: API-IRR-03 and -04's response (#1156) reads it
    inside its write transaction, so the program it compiles holds that write."""
    async with _snapshot(conn):
        return (await _base(conn)).sync_of(updated_at)


# ── API-IRR-01 ──────────────────────────────────────────────────────────────


def _program_sync(base: _Base) -> ProgramSync:
    newest, controller = base.newest_push, base.controller
    program_id = controller.program_id
    return ProgramSync(
        state=program_state(
            engine=base.engine,
            desired_sha256=base.compiled.sha256,
            applied_hash=base.applied_hash,
            desired_push=base.desired_push,
            at=base.at,
        ),
        revision=None if newest is None else newest["revision"],
        desired_sha256=base.compiled.sha256,
        applied_revision=None if controller.applied is None else controller.applied["revision"],
        applied_hash=base.applied_hash,
        src=None if program_id is None else program_id.src,
        reason=None if base.engine else NOT_ON_CONTROLLER,
        pushed_at=None if newest is None else newest["pushed_at"],
        confirmed_at=None if newest is None else newest["confirmed_at"],
        reject_code=None if newest is None else newest["reject_code"],
        next_retry_at=None,  # the 1, 5 and 15 min retry stage lives in the ingestor; the screen states that cadence
    )


def pulses_total(slot_rows: Iterable[Mapping[str, Any]], run: irr.IrrRun) -> int | None:
    """A mist run's planned pulses, from its schedule: a run-now or VPD pulse has no slot, and a prewet or flush on
    the clean partner is one opening, not the slot's pulses."""
    if run.slot_id is None or not pulsing(run.line_id) or run.cause in (IrrigationCause.PREWET, IrrigationCause.FLUSH):
        return None
    return next((row["count"] for row in slot_rows if row["slot_id"] == run.slot_id), None)


def _active_runs(base: _Base, read: _LineRead, reading: IrrState | None) -> list[ActiveRun]:
    """The run on each manifold from ``irr_state``; with no engine reading, each wet relay on, from its ON edge, with
    the cause its line's state gives. Nothing is known to run while the controller is not reporting."""
    if not base.reporting.controller_reporting:
        return []
    if base.engine and reading is not None:
        return [
            ActiveRun(
                manifold="drip" if run.manifold == "drip" else "mister",
                line_id=run.line_id,
                cause=run.cause,
                slot_id=run.slot_id,
                started_at=_epoch_time(run.start_epoch),
                ends_at=_epoch_time(run.end_epoch),
                pulses_total=pulses_total(base.slot_rows, run),
            )
            for run in reading.runs
        ]
    out: list[ActiveRun] = []
    for line in read.lines:
        manifold = _manifold_of(line.line_id)
        open_run = read.open_runs.get(line.line_id)
        if manifold is None or open_run is None or line.state.code != "running" or line.state.cause is None:
            continue
        out.append(
            ActiveRun(manifold=manifold, line_id=line.line_id, cause=line.state.cause, started_at=open_run.run.start)
        )
    return out


def _saf4_max(controller: ControllerProgram) -> float:
    """The applied program's SAF-4, else the registry default."""
    default = float(REGISTRY[SAF4_PARAM].default)
    if controller.applied is None:
        return default
    try:
        return float(unpack_program(controller.applied["bytes"]).saf4_gal)
    except (irr.ProgramRecordError, TypeError):
        return default


def _occupancy(tracks: Mapping[str, core.Track], rep: core.Reporting) -> Occupancy:
    """The newest ``occupancy`` row while the controller reports (null means unknown, and unknown never holds mist),
    its newest ON edge, and whether the hold is on."""
    spans = tracks[OCCUPANCY_FLAG].spans
    hold = tracks[OCCUPANCY_HOLD_FLAG].spans
    known = rep.controller_reporting
    return Occupancy(
        occupied=bool(spans[-1].state) if known and spans else None,
        seen_at=next((span.start for span in reversed(spans) if span.state), None),
        hold_enabled=bool(hold[-1].state) if known and hold else None,
    )


async def status(conn: asyncpg.Connection, actor: Actor | None) -> IrrigationStatus:
    """API-IRR-01 ``GET /v1/irrigation``: lines, active runs, program sync and the engine flag, today's minutes by
    zone, cap use, the live leak state and occupancy, in one read-only ``REPEATABLE READ`` transaction. It runs as
    the dashboard login and, for MCP's ``irrigation_status``, as the MCP login."""
    async with _snapshot(conn):
        base = await _base(conn)
        rep = base.reporting
        read = await _read_lines(conn, base, None)
        water = await conn.fetchrow(_WATER_NOW_SQL, GREENHOUSE_ID, SOFT_BUDGET_PARAM)
        tracks = await core.equipment_tracks(conn, (core.LEAK_FLAG, OCCUPANCY_FLAG, OCCUPANCY_HOLD_FLAG))
        leak_alert = await conn.fetchrow(_LEAK_ALERT_SQL, core.LEAK_FLAG)
        leak = await core.leak_block(
            conn, tracks[core.LEAK_FLAG], leak_alert, core._finite(water["flow_gpm"]), water["gal_10min"], rep
        )

    state_row = next(iter(base.line_rows), None)
    irr_state_at = None if state_row is None else state_row["irr_state_at"]
    reading = irr.irr_state_reading(None if state_row is None else state_row["irr_state"])
    used = core._finite(water["water_today"]) if rep.controller_reporting else None
    enabled = {row["line_id"]: row["enabled"] for row in base.line_rows}
    return IrrigationStatus(
        server_time=base.at,
        engine=base.engine,
        program=_program_sync(base),
        lines=read.lines,
        active_runs=_active_runs(base, read, reading),
        today=TodayMinutes(
            date=read.today,
            zones=_zones(read.groups, enabled, no_data=read.no_data_today),
        ),
        limits=Limits(
            saf4_gal=UsedOfMax(used=used, max=_saf4_max(base.controller)),
            soft_budget_gal=UsedOfMax(used=used, max=core._finite(water["soft_budget_max"])),
        ),
        leak=leak,
        occupancy=_occupancy(tracks, rep),
        fallback_active=(
            base.engine
            and rep.controller_reporting
            and reading is not None
            and any(run.cause == IrrigationCause.FALLBACK for run in reading.runs)
        ),
        freshness=StateFreshness(
            observed_at=irr_state_at,
            age_s=None if irr_state_at is None else max(0, int((base.at - irr_state_at).total_seconds())),
            stale=irr_state_at is None or not rep.controller_reporting,
        ),
    )


# ── API-IRR-02 ──────────────────────────────────────────────────────────────


def _schedule(row: Mapping[str, Any], sync: ReadbackState) -> Schedule:
    spec = ScheduleSpec.from_row(row).to_api()
    return Schedule(
        **spec,
        sync=sync,
        updated_by=ActorRef(source=row["source"], name=row["updated_by"]),
        updated_at=row["updated_at"],
        reason=row["reason"],
    )


def _ledger_of_occurrence(rows: Sequence[Mapping[str, Any]], line_id: str) -> Mapping[str, Any]:
    """An occurrence's row on its own line (a fertilizer run also has its stages on the clean partner)."""
    return next((row for row in rows if row["line_id"] == line_id), rows[0])


def _done_reason(row: Mapping[str, Any], pause: Mapping[str, Any] | None) -> str | None:
    if row["outcome"] is None:
        return NO_OUTCOME
    if row["reason"] == "pau" and pause is not None:
        return pause_words(pause)
    return reason_words(row["reason"])


def occurrences_read(
    plan: irr.Agenda,
    slot_created: Mapping[int, datetime],
    *,
    ledger: Mapping[tuple[int, date], Sequence[Mapping[str, Any]]],
    pause_rows: Sequence[Mapping[str, Any]],
    engine_rows: Sequence[tuple[datetime, bool]],
    running_slots: Iterable[int],
    at: datetime,
) -> list[Occurrence]:
    """The agenda's occurrences planned against actual (API-IRR-02).

    One due before its schedule existed is left out, so a new schedule shows no invented history. A past one that has
    a ledger row reads ``done`` with its outcome (``unknown`` when the row has none); the newest due of a slot the
    controller is running reads ``running``. One the agenda skips stays ``skipped``, a pause naming who paused it,
    from the pause row in force at its planned start. Today's flags say nothing about a past start, though: with no
    such pause row, or with the engine in effect at a start skipped for the line's enable or membership, it reads as
    planned. A planned one with no engine in effect at its start reads ``blocked``; a past one with the engine and no
    row reads ``done`` with ``unknown``, a gap, never missed.
    """
    running = set(running_slots)
    in_progress: dict[int, irr.AgendaOccurrence] = {}
    for item in plan.occurrences:
        if item.slot_id in running and item.planned_start <= at:
            in_progress[item.slot_id] = item  # occurrences are by planned start, so the newest due stays
    out: list[Occurrence] = []
    for item in plan.occurrences:
        past = item.planned_start <= at
        if past and item.planned_start <= slot_created[item.slot_id]:
            continue
        pause = pause_in_force(pause_rows, item.line_id, item.planned_start) if pause_rows else None
        rows = ledger.get((item.slot_id, item.local_day), ()) if past else ()
        status_: str = "planned" if item.status == "planned" else "skipped"
        reason = item.reason
        outcome: RunOutcome | None = None
        run_id: str | None = None
        if item.reason_code == "pau":
            if pause is not None:
                reason = pause_words(pause)
            elif past:
                status_, reason = "planned", None  # the pause came after this start
        elif past and item.reason_code is not None and engine_at(engine_rows, item.planned_start):
            status_, reason = "planned", None  # the ledger answers for it; today's flags do not
        if past and in_progress.get(item.slot_id) is item:
            status_, reason = "running", None
        elif rows:
            row = _ledger_of_occurrence(rows, item.line_id)
            status_, outcome = "done", RunOutcome(row["outcome"] or RunOutcome.UNKNOWN)
            reason, run_id = _done_reason(row, pause), _ledger_run_id(row)
        elif status_ == "planned":
            if not engine_at(engine_rows, item.planned_start):
                status_, reason = "blocked", ARRIVES_WITH_FIRMWARE
            elif past:
                status_, outcome, reason = "done", RunOutcome.UNKNOWN, NO_OUTCOME
        out.append(
            Occurrence(
                slot_id=item.slot_id,
                line_id=item.line_id,
                planned_start=item.planned_start,
                status=status_,
                outcome=outcome,
                reason=reason,
                run_id=run_id,
            )
        )
    return out


async def agenda(conn: asyncpg.Connection, actor: Actor | None, days: int = 7, past_days: int = 0) -> ScheduleAgenda:
    """API-IRR-02 ``GET /v1/irrigation/schedules``: every schedule with its sync, the occurrences of ``days`` 1-7
    ahead and ``past_days`` 0-7 back planned against actual, solar times per local day, and the bounds per line kind."""
    for name, value, low, high in (("days", days, 1, 7), ("past_days", past_days, 0, 7)):
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ServiceError(
                problem(
                    "invalid_request",
                    f"{name} must be {low}-{high}",
                    field=name,
                    min=low,
                    max=high,
                    current=value if isinstance(value, int) else None,
                )
            )
    async with _snapshot(conn):
        base = await _base(conn)
        at = base.at
        today = core.local_midnight(at).date()
        plan = irr.agenda(base.line_specs, base.slot_specs, today=today, days=days, past_days=past_days)
        window_start = datetime.combine(plan.first_day, time(0), tzinfo=core.DENVER)
        engine_rows = [
            (row["ts"], engine_flag(row["value"]))
            for row in await conn.fetch(
                _ENGINE_ROWS_SQL, ENGINE_ENTITY, GREENHOUSE_ID, window_start, NEWEST_STATE_WINDOW
            )
        ]
        ledger_rows = await conn.fetch(_OCCURRENCE_LEDGER_SQL, GREENHOUSE_ID, plan.first_day, plan.last_day)
        pausing = any(item.reason_code == "pau" for item in plan.occurrences) or any(
            row["reason"] == "pau" for row in ledger_rows
        )
        pause_rows = await conn.fetch(_PAUSE_ROWS_SQL, GREENHOUSE_ID) if pausing else []

    ledger: dict[tuple[int, date], list[Mapping[str, Any]]] = defaultdict(list)
    for row in ledger_rows:
        ledger[(row["slot_id"], row["local_day"])].append(row)
    state_row = next(iter(base.line_rows), None)
    reading = irr.irr_state_reading(None if state_row is None else state_row["irr_state"])
    running_slots = (
        [run.slot_id for run in reading.runs if run.slot_id is not None]
        if base.engine and base.reporting.controller_reporting and reading is not None
        else []
    )
    return ScheduleAgenda(
        schedules=[_schedule(row, base.sync_of(row["updated_at"])) for row in base.slot_rows],
        occurrences=occurrences_read(
            plan,
            {row["slot_id"]: row["created_at"] for row in base.slot_rows},
            ledger=ledger,
            pause_rows=pause_rows,
            engine_rows=engine_rows,
            running_slots=running_slots,
            at=at,
        ),
        counts=AgendaCounts(schedules=len(base.slot_rows), max_schedules=MAX_SCHEDULES),
        bounds=bounds_by_kind(),
        solar=list(plan.solar),
    )


# ── API-IRR-10 ──────────────────────────────────────────────────────────────


def minutes_rows(rows: Iterable[Mapping[str, Any]], group_by: GroupBy) -> list[MinutesRow]:
    """The reader's rows per period, group, cause and chemistry; grouping by zone leaves out L03's supply rows."""
    totals: dict[tuple[date, str, str, str], dict[str, Any]] = {}
    for row in rows:
        if group_by == "zone" and row["zone"] == "supply":
            continue
        group = {"zone": row["zone"], "line": row["line_id"], "cause": row["cause"]}[group_by]
        key = (row["local_day"], group, row["cause"], row["chemistry"])
        total = totals.setdefault(key, {"minutes": Decimal(0), "pulses": None, "gal": None})
        total["minutes"] += Decimal(str(row["minutes"]))
        if row["pulses"] is not None:
            total["pulses"] = (total["pulses"] or 0) + row["pulses"]
        if row["gal"] is not None:
            total["gal"] = (total["gal"] or 0) + row["gal"]
    return [
        MinutesRow(
            period=period,
            group=group,
            cause=cause,
            chemistry=chem,
            minutes=float(total["minutes"]),
            pulses=total["pulses"],
            gal=total["gal"],
        )
        for (period, group, cause, chem), total in sorted(totals.items())
    ]


async def minutes(
    conn: asyncpg.Connection, actor: Actor | None, from_: date, to: date, group_by: GroupBy = "zone"
) -> MinutesReport:
    """API-IRR-10 ``GET /v1/irrigation/minutes``: minutes, pulses and whole gallons per local day ``from_``..``to``
    (inclusive, at most 400 days) by zone, line or cause, always split by cause and chemistry. A day with coverage
    ``no_data`` has no rows, so it draws as a gap, never 0."""
    if group_by not in ("zone", "line", "cause"):
        raise ServiceError(problem("invalid_request", "group_by is zone, line or cause", field="group_by"))
    if to < from_:
        raise ServiceError(problem("invalid_request", "to must not be before from", field="from"))
    span = (to - from_).days + 1
    if span > MINUTES_MAX_DAYS:
        raise ServiceError(
            problem(
                "window_too_large",
                f"The window is {span} days; the most is {MINUTES_MAX_DAYS}.",
                field="from",
                max=MINUTES_MAX_DAYS,
                current=span,
            )
        )
    async with _snapshot(conn):
        rows = await conn.fetch(_MINUTES_SQL, from_, to, GREENHOUSE_ID)
        coverage = {
            row["local_day"]: row["coverage"] for row in await conn.fetch(_COVERAGE_SQL, GREENHOUSE_ID, from_, to)
        }
    return MinutesReport.model_validate(
        {
            "from": from_,
            "to": to,
            "group_by": group_by,
            "periods": [
                MinutesPeriod(
                    start=from_ + timedelta(days=offset),
                    coverage=coverage.get(from_ + timedelta(days=offset), "no_data"),
                )
                for offset in range(span)
            ],
            "rows": minutes_rows(rows, group_by),
        }
    )


# ── API-IRR-11 ──────────────────────────────────────────────────────────────


def _ledger_ended(row: Mapping[str, Any], at: datetime) -> bool:
    """A ledger row is over once it ended or has an outcome; one whose end never arrived stops holding its run open
    at the bound M3's join uses."""
    if row["ended_at"] is not None or row["outcome"] is not None:
        return True
    return row["started_at"] is None or at - row["started_at"] >= LEDGER_OPEN_FOR


def _no_start_time(row: Mapping[str, Any], slot_rules: Mapping[int, Mapping[str, Any]]) -> datetime:
    """When a ledger row with no ``started_at`` belongs: its occurrence's planned start while its schedule exists,
    else when it was recorded (a refused run-now has no slot)."""
    rule = slot_rules.get(row["slot_id"]) if row["slot_id"] is not None else None
    if rule is None:
        return row["recorded_at"]
    start = irr.Start(kind=rule["start_kind"], minute=rule["start_min"])
    return irr.occurrence_start(start, row["local_day"])


@dataclass(frozen=True)
class _Listed:
    """A run before its gallons: a group of relay runs, or a ledger row with no relay run."""

    run_id: str
    line_id: str
    cause: str
    started_at: datetime
    ended_at: datetime | None
    open_s: int
    pulses: int | None
    ledger: Mapping[str, Any] | None
    bridge: Mapping[str, Any] | None
    group: RunGroup | None
    outcome: str


def _listed_group(group: RunGroup) -> _Listed:
    ledger = group.ledger
    return _Listed(
        run_id=_edge_run_id(group.line_id, group.start) if ledger is None else _ledger_run_id(ledger),
        line_id=group.line_id,
        cause=group.cause,
        started_at=group.start,
        ended_at=group.end,
        open_s=group.open_s,
        pulses=group.pulses,
        ledger=ledger,
        bridge=group.bridge,
        group=group,
        outcome=RunOutcome.OK.value if ledger is None else (ledger["outcome"] or RunOutcome.UNKNOWN.value),
    )


def _listed_ledger(row: Mapping[str, Any], started_at: datetime) -> _Listed:
    return _Listed(
        run_id=_ledger_run_id(row),
        line_id=row["line_id"],
        cause=row["cause"],
        started_at=started_at,
        ended_at=row["ended_at"],
        open_s=row["open_s"],
        pulses=row["pulses"],
        ledger=row,
        bridge=None,
        group=None,
        outcome=row["outcome"] or RunOutcome.UNKNOWN.value,
    )


def _list_runs(
    groups: Iterable[RunGroup],
    lone_rows: Iterable[tuple[Mapping[str, Any], datetime]],
    *,
    from_: datetime,
    to: datetime,
    cause: str | None,
    outcome: str | None,
    at: datetime,
) -> list[_Listed]:
    """The ended runs that started in [``from_``, ``to``), newest first by (``started_at``, ``run_id``), filtered."""
    items: list[_Listed] = []
    for group in groups:
        if group.line_id == "L03" or not from_ <= group.start < to or group.open:
            continue  # L03 opens only inside a feed; a run from before the window is that window's
        if group.ledger is not None and not _ledger_ended(group.ledger, at):
            continue
        items.append(_listed_group(group))
    for row, started_at in lone_rows:
        if row["line_id"] != "L03" and from_ <= started_at < to and _ledger_ended(row, at):
            items.append(_listed_ledger(row, started_at))
    return sorted(
        (
            item
            for item in items
            if (cause is None or item.cause == cause) and (outcome is None or item.outcome == outcome)
        ),
        key=lambda item: (item.started_at, item.run_id),
        reverse=True,
    )


def _gallons_of(item: _Listed, deltas: Deltas, reported: bool) -> int | None:
    if item.group is not None:
        return run_gallons(item.group, deltas, reported)
    if item.open_s == 0:
        return 0  # never opened a valve
    return None  # a ledger row whose relay edges are missing: which water was its is not known


async def _page_gallons(conn: asyncpg.Connection, page: Sequence[_Listed]) -> dict[str, int | None]:
    """A page's gallons: its deltas, and for each run none overlaps, whether the meter reported over it."""
    relay_groups = [item for item in page if item.group is not None]
    deltas = Deltas(())
    reported: dict[int, bool] = {}
    if relay_groups:
        low = min(item.started_at for item in relay_groups)
        high = max(item.group.end for item in relay_groups)
        deltas = await deltas_between(conn, low, high + MAX_DELTA_SPAN)
        silent = [
            index
            for index, item in enumerate(relay_groups)
            if not any(deltas.overlapping(part.run.start, part.run.end) for part in item.group.parts)
        ]
        if silent:
            rows = await conn.fetch(
                _METER_REPORTED_SQL,
                silent,
                [relay_groups[index].started_at for index in silent],
                [relay_groups[index].group.end + MAX_DELTA_SPAN for index in silent],
            )
            reported = {row["i"]: row["reported"] for row in rows}
    index_of = {item.run_id: index for index, item in enumerate(relay_groups)}
    return {item.run_id: _gallons_of(item, deltas, reported.get(index_of.get(item.run_id, -1), True)) for item in page}


def _command_actor(item: _Listed, commands: Mapping[Any, Mapping[str, Any]]) -> tuple[str | None, ActorRef | None]:
    command_id = None if item.ledger is None else item.ledger["command_id"]
    command = commands.get(command_id) if command_id is not None else None
    if item.bridge is not None:
        command, command_id = item.bridge, item.bridge["command_id"]
    if command is not None:
        return str(command["command_id"]), ActorRef(source=command["source"], name=command["actor"])
    if item.ledger is not None and item.ledger["actor"]:
        name = item.ledger["actor"]
        return (None if command_id is None else str(command_id)), ActorRef(source="system", name=name)
    return (None if command_id is None else str(command_id)), None


async def runs(
    conn: asyncpg.Connection,
    actor: Actor | None,
    from_: datetime,
    to: datetime,
    line: str | None = None,
    cause: str | None = None,
    outcome: str | None = None,
    limit: int = LIMIT_DEFAULT,
    cursor: str | None = None,
) -> RunPage:
    """API-IRR-11 ``GET /v1/irrigation/runs``: ended runs that started in [``from_``, ``to``), at most 31 days, newest
    first in keyset pages on (``started_at``, ``run_id``), with filters on line, cause and outcome. The run in progress
    is in API-IRR-01, and L03 is never a run of its own. Ledger rows that opened no valve list with ``open_s`` 0."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= LIMIT_MAX:
        raise ServiceError(
            problem(
                "invalid_request",
                f"limit must be 1-{LIMIT_MAX}",
                field="limit",
                min=1,
                max=LIMIT_MAX,
                current=limit if isinstance(limit, int) else None,
            )
        )
    for name, value in (("from", from_), ("to", to)):
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise ServiceError(problem("invalid_request", f"{name} needs a time with its UTC offset", field=name))
    if to <= from_:
        raise ServiceError(problem("invalid_request", "from must be before to", field="from"))
    if to - from_ > timedelta(days=RUNS_MAX_DAYS):
        raise ServiceError(
            problem(
                "window_too_large",
                f"The window is over {RUNS_MAX_DAYS} days.",
                field="from",
                max=RUNS_MAX_DAYS,
                current=(to - from_).total_seconds() / 86400,
            )
        )
    if line is not None and line not in LINE_RELAY:
        raise ServiceError(problem("invalid_request", f"{line!r} is not a line", field="line"))
    for name, value, known in (("cause", cause, IrrigationCause), ("outcome", outcome, RunOutcome)):
        if value is not None and value not in tuple(known):
            raise ServiceError(problem("invalid_request", f"{value!r} is not a {name}", field=name))
    after = decode_run_cursor(cursor) if cursor else None
    wanted = [line_id for line_id in LINE_RELAY if line_id != "L03" and (line is None or line_id == line)]

    async with _snapshot(conn):
        at: datetime = await conn.fetchval("SELECT now()")
        items = await attributed_runs(conn, {line_id: (from_, to) for line_id in wanted}, at)
        groups = group_runs(items)
        joined = {group.ledger["id"] for group in groups if group.ledger is not None}
        first_day = from_.astimezone(core.DENVER).date() - _DAY
        last_day = to.astimezone(core.DENVER).date() + _DAY
        window_rows = await conn.fetch(_WINDOW_LEDGER_SQL, GREENHOUSE_ID, from_, to)
        no_start_rows = await conn.fetch(_NO_START_LEDGER_SQL, GREENHOUSE_ID, first_day, last_day)
        slot_ids = sorted({row["slot_id"] for row in no_start_rows if row["slot_id"] is not None})
        slot_rules = {row["slot_id"]: row for row in await conn.fetch(_SLOT_RULES_SQL, slot_ids)} if slot_ids else {}
        lone: list[tuple[Mapping[str, Any], datetime]] = [
            (row, row["started_at"]) for row in window_rows if row["id"] not in joined
        ]
        lone += [(row, _no_start_time(row, slot_rules)) for row in no_start_rows]
        lone = [(row, when) for row, when in lone if row["line_id"] in wanted]
        listed = _list_runs(groups, lone, from_=from_, to=to, cause=cause, outcome=outcome, at=at)
        if after is not None:
            listed = [item for item in listed if (item.started_at, item.run_id) < after]
        page, more = listed[:limit], len(listed) > limit

        gallons = await _page_gallons(conn, page)
        command_ids = [
            item.ledger["command_id"]
            for item in page
            if item.ledger is not None and item.ledger["command_id"] is not None
        ]
        commands = (
            {row["command_id"]: row for row in await conn.fetch(_COMMANDS_SQL, command_ids)} if command_ids else {}
        )
        page_slots = sorted({item.ledger["slot_id"] for item in page if item.ledger and item.ledger["slot_id"]})
        labels = (
            {row["slot_id"]: row["label"] for row in await conn.fetch(_SLOT_RULES_SQL, page_slots)}
            if page_slots
            else {}
        )

    out: list[Run] = []
    for item in page:
        command_id, who = _command_actor(item, commands)
        slot_id = None if item.ledger is None else item.ledger["slot_id"]
        out.append(
            Run(
                run_id=item.run_id,
                line_id=item.line_id,
                zone=MINUTES_ZONE[item.line_id],
                chemistry=chemistry(item.line_id),
                cause=item.cause,
                slot_id=slot_id,
                schedule_label=labels.get(slot_id),
                command_id=command_id,
                actor=who,
                started_at=item.started_at,
                ended_at=item.ended_at,
                open_s=item.open_s,
                pulses=item.pulses,
                gal=gallons.get(item.run_id),
                outcome=item.outcome,
                reason=None if item.ledger is None else reason_words(item.ledger["reason"]),
            )
        )
    return RunPage(items=out, next_cursor=encode_run_cursor(out[-1]) if more else None)
