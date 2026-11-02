"""Settings: the one validator and writer for desired state (architecture §5.2 to §5.5, VD-67).

The operator API (API-09 to API-12, #1150 and #1168) and MCP (``settings`` and ``set_setting``, #1171) wrap these
functions, so a person and an agent get the same answer for the same input.

- ``catalog(conn)``: a ``Setting`` for every ``REGISTRY`` row, with the registry's own ``label`` and ``unit``.
- ``validate(conn, actor, param, value)``: §5.3 steps 1 to 4. The first failure raises ``ServiceError`` with a
  422 ``Problem`` (``code``, ``detail``, ``field``, ``min``, ``max``, ``current``, ``retryable`` false).
- ``write(...)`` and ``release(...)``: one ``activity.recorded_write`` transaction each: the ``desired_state`` row,
  for a crop-band setting its ``crop_band_anchors`` rows, and one ``activity_log`` row. The ``desired_state``
  insert trigger is the only wake; nothing here writes ``setpoint_changes``.
- ``status(conn, param)``: whose value applies and the readback state (§5.5). Display only: it raises no alert.
  ``statuses(conn, params)`` is the same for several settings in one batched read (API-07's lanes).
- ``history(conn, param, from_, to)``: the desired rows and the readback change points over a window.
- ``desired_states(conn, ids)``: named ``desired_state`` rows with their readback state, by ``history``'s rule
  (API-17's ``setting.write`` and ``setting.release`` rows).

Each write takes the caller's ``Actor`` and nothing else says who wrote. ``manual`` has no default here: REST
defaults it to true (#1150) and MCP to false (#1171).

``verdify_services.reads`` composes these into API-09, API-12 and API-17 (VD-55) and imports this module, so this
module imports ``reads`` only inside ``_shared()``, where it calls ``reads.reporting()``.
"""

from __future__ import annotations

import math
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal

import asyncpg
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from verdify_schemas.activity import ActivityLogRow
from verdify_schemas.band_rows import BandRows, band_rows, rows_in_force
from verdify_schemas.policy_vector import wire_fields
from verdify_schemas.setting_values import (
    binary32_ulp,
    readback_matches,
    readback_tolerance,
    readback_values_equivalent,
    resolve_effective,
    snap_setting,
)
from verdify_schemas.tunable_registry import (
    REGISTRY,
    SWITCH_READBACK_EQUIPMENT,
    TunableDef,
    desired_write_rule,
    planner_effective_bounds,
)
from verdify_services import activity
from verdify_services.activity import ReadbackState, WriteResult
from verdify_services.actor import Actor, ActorRef
from verdify_services.problems import ServiceError, problem

if TYPE_CHECKING:  # reads imports this module; _shared() imports reads when it runs
    from verdify_services.reads import Reporting

GREENHOUSE_ID = "vallery"
# A stored write reads requested this long before it reads not_confirmed (§5.5).
REQUESTED_FOR = timedelta(minutes=5)
# setpoint_snapshot holds every cfg_* sensor and native Number/Switch state each 60 s, so a readback older than
# this is no readback now. equipment_state records changes only, so its window is the readback retention.
READBACK_WINDOW = timedelta(hours=24)
EQUIPMENT_WINDOW = timedelta(days=90)
# A band or default setting with no desired row and no plan is judged against the dispatcher's newest push.
PUSH_WINDOW = timedelta(days=90)
# setpoint_changes rows the controller itself reported (source esp32, delivery_status observed) are not pushes.
DEVICE_OBSERVED_SOURCE = "esp32"
DEFERRED_STATUS = "deferred_heap_pressure"
FAILED_STATUS = "failed"
# The 48 policy fields: an armed experiment serves them to the controller, so their settings read held.
POLICY_FIELDS = frozenset(spec.name for spec in wire_fields())
NO_ANCHOR_ROW = "no crop_band_anchors row holds it"
NO_READBACK_REASON = "no readback in the last 24 h"
NO_PROGRAM_READBACK = "applies with the irrigation firmware"

ReasonCode = Literal[
    "clamped",
    "experiment",
    "deferred",
    "push_failed",
    "writer_down",
    "controller_not_reporting",
    "no_engine",
    "no_match",
]
ReadbackVia = Literal["cfg", "entity", "program", "none"]
AppliedFrom = Literal["manual", "desired", "plan", "band", "default"]
ControlClass = Literal[
    "planner_policy",
    "scheduled_policy",
    "controller_safety",
    "crop_band",
    "irrigation_site",
    "readback_context",
    "retired",
]
SettingGroup = Literal[
    "planner_policy",
    "scheduled_policy",
    "controller_safety",
    "crop_band",
    "site_water_caps",
    "read_only",
    "band_in_force",
]


# ── Models: the OpenAPI schemas (docs/design/dashboard/openapi/dashboard-api.v1.yaml) ──────────────────────


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StateReason(_Model):
    """Why a value is held or not confirmed (OpenAPI ``StateReason``). ``since`` is the HH:MM the UI shows."""

    code: ReasonCode
    detail: str | None = None
    applied_value: float | None = None
    since: AwareDatetime | None = None


class DesiredSetting(_Model):
    """The newest ``desired_state`` row of a setting and its readback state (OpenAPI ``DesiredSetting``).

    ``snapped_from`` and ``manual_before`` are set on write responses only.
    """

    id: int
    param: str
    value: float | None
    snapped_from: float | None = None
    manual: bool
    manual_before: bool | None = None
    state: ReadbackState
    state_reason: StateReason | None = None
    confirmed_at: AwareDatetime | None = None
    actor: ActorRef
    reason: str | None = None
    created_at: AwareDatetime


class Reading(_Model):
    """A value with the time it was observed (OpenAPI ``Reading``); ``reason`` says why ``value`` is null."""

    value: float | None
    observed_at: AwareDatetime | None
    reason: str | None = None


class TimedValue(_Model):
    at: AwareDatetime
    value: float | None


class SettingOption(_Model):
    value: float
    label: str


class PlanWaypoint(_Model):
    """A plan value (OpenAPI ``PlanWaypoint``): ``reads.plan_waypoints`` and ``reads.plan_history`` build them."""

    at: AwareDatetime
    value: float | None
    source: Literal["iris", "preemptive"]
    effective: bool


class SettingPlan(_Model):
    """The plan value in force (OpenAPI ``Setting.plan``): ``reads.settings_rows`` adds it to the catalog rows."""

    value: float | None
    plan_id: str
    source: Literal["iris", "preemptive"]
    written_at: AwareDatetime
    next: PlanWaypoint | None = None


class Setting(_Model):
    """One registry row as the Settings screen shows it (OpenAPI ``Setting``).

    ``label`` and ``unit`` are ``TunableDef.label`` and ``TunableDef.unit``, unchanged. ``plan`` stays None
    here: ``reads.settings_rows`` (API-09) adds the plan block and the Band in force rows.
    """

    param: str
    label: str
    control_class: ControlClass
    group: SettingGroup
    kind: Literal["numeric", "switch", "enum"]
    unit: str | None = None
    min: float | None = None
    max: float | None = None
    step: float | None = None
    options: list[SettingOption] = Field(default_factory=list)
    writable: bool
    read_only_reason: str | None = None
    readback_via: ReadbackVia
    readback: Reading | None
    desired: DesiredSetting | None
    plan: SettingPlan | None = None
    applied_from: AppliedFrom
    state: ReadbackState


class SettingHistory(_Model):
    """Desired rows, plan values and readback change points over a window (OpenAPI ``SettingHistory``).

    ``from`` is a Python keyword: the field is ``from_`` and serializes as ``from``. ``plan`` stays empty here:
    ``reads.setting_history`` (API-12) adds the plan values (``reads.plan_history``).
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)

    param: str
    unit: str | None = None
    min: float | None = None
    max: float | None = None
    from_: AwareDatetime = Field(alias="from")
    to: AwareDatetime
    desired: list[DesiredSetting]
    plan: list[PlanWaypoint] = Field(default_factory=list)
    readback: list[TimedValue]


class SettingStatus(_Model):
    """``status()``: whose value applies, the readback state and why. Display only."""

    param: str
    applied_from: AppliedFrom
    state: ReadbackState
    state_reason: StateReason | None = None
    confirmed_at: AwareDatetime | None = None
    readback_via: ReadbackVia
    readback: Reading | None
    desired: DesiredSetting | None


class ValidatedSetting(_Model):
    """``validate()``'s answer: the value to store and what it was checked against."""

    param: str
    value: float
    requested: float
    snapped_from: float | None = None
    current: float | None = None
    manual_before: bool


# ── Pure rules ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Readback:
    value: float | None
    at: datetime


@dataclass(frozen=True)
class _Push:
    at: datetime
    status: str | None


@dataclass(frozen=True)
class _Clamp:
    at: datetime
    applied: float | None
    reason: str | None


@dataclass
class _Facts:
    """What one setting's state is computed from (architecture §7 Settings)."""

    desired: Mapping[str, Any] | None = None  # the v_desired_state_current row
    plan: Mapping[str, Any] | None = None  # the v_active_plan row
    readback: _Readback | None = None  # the newest readback (setpoint_snapshot, or the equipment mirror)
    last_push: Mapping[str, Any] | None = None  # the newest dispatcher push, for band and default rows
    push: _Push | None = None  # the newest push at or after the request
    clamp: _Clamp | None = None  # the newest setpoint_clamps row at or after the request
    first_match_at: datetime | None = None  # the first readback at or after the request that confirms it


@dataclass(frozen=True)
class _Shared:
    reporting: Reporting
    experiment_armed: bool

    @property
    def at(self) -> datetime:
        return self.reporting.at


@dataclass(frozen=True)
class _Request:
    """The value a state is judged for: a desired row's, the plan's, or the newest push's."""

    kind: Literal["desired", "plan", "push"]
    value: float
    since: datetime
    matches: Callable[[str, float | None, float | None], bool]


@dataclass(frozen=True)
class _Evaluation:
    state: ReadbackState
    reason: StateReason | None = None
    confirmed_at: datetime | None = None


def _finite(value: object) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _equivalent(param: str, observed: float | None, value: float | None) -> bool:
    """Plan values keep the dispatcher's comparator (1 % dead-band, then the duration tolerance)."""
    if observed is None or value is None:
        return False
    return readback_values_equivalent(param, observed, value)


def readback_via(spec: TunableDef) -> ReadbackVia:
    """How a setting reads back: its ``cfg_*`` sensor, its native entity (or equipment mirror), the irrigation
    program hash (``irrigation_site``, #1155), or none."""
    if spec.control_class == "irrigation_site":
        return "program"
    if spec.cfg_readback_object_id:
        return "cfg"
    if spec.esp_object_id or spec.name in SWITCH_READBACK_EQUIPMENT:
        return "entity"
    return "none"


def bounds(param: str) -> tuple[float | None, float | None]:
    """``planner_effective_bounds``: the registry bounds intersected with the firmware clamp."""
    try:
        return planner_effective_bounds(param)
    except ValueError:
        return None, None


def _writable_desired(param: str, desired: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    """The desired row the dispatcher acts on: none for a setting that is not writable now (§5.2)."""
    if desired is None or not desired_write_rule(param)[0]:
        return None
    return desired


def applied_from(spec: TunableDef, desired: Mapping[str, Any] | None, plan: Mapping[str, Any] | None) -> AppliedFrom:
    """Whose value the dispatcher applies (§5.2): manual, desired or plan by ``resolve_effective()``; with no
    desired row (or a released one), plan when a ``v_active_plan`` row exists, else band for a band or
    schedule setting, else default."""
    desired = _writable_desired(spec.name, desired)
    resolved = resolve_effective(desired, plan)
    if resolved is not None:
        if resolved.applied_from == "desired" and desired is not None and desired["manual"]:
            return "manual"
        return resolved.applied_from
    if plan is not None:
        return "plan"
    if spec.push_owner in ("band", "schedule"):
        return "band"
    return "default"


def _plan_took_force(plan: Mapping[str, Any]) -> datetime:
    """A plan value takes force at its waypoint time, or when it was written if that is later."""
    return max(plan["ts"], plan["created_at"])


def _request_of(spec: TunableDef, facts: _Facts) -> tuple[str, _Request | None]:
    """What the state is judged for, and how: (kind, request).

    kind is ``released`` or ``overridden`` (no request: those states need no readback), ``desired``, ``plan``,
    ``push`` or ``none`` (nothing was ever requested).
    """
    desired = _writable_desired(spec.name, facts.desired)
    if desired is not None:
        if desired["value"] is None:
            return "released", None
        resolved = resolve_effective(desired, facts.plan)
        if resolved is not None and resolved.applied_from == "plan":
            return "overridden", None
        value = _finite(desired["value"])
        if value is None:
            return "none", None
        return "desired", _Request("desired", value, desired["created_at"], readback_matches)
    if facts.plan is not None and (value := _finite(facts.plan["value"])) is not None:
        return "plan", _Request("plan", value, _plan_took_force(facts.plan), _equivalent)
    if facts.last_push is not None and (value := _finite(facts.last_push["value"])) is not None:
        return "push", _Request("push", value, facts.last_push["ts"], _equivalent)
    return "none", None


def _reason(
    clamp: _Clamp | None,
    experiment: bool,
    push: _Push | None,
    reporting: Reporting,
) -> StateReason:
    """§5.5: the first reason that holds, with its ``since``."""
    if clamp is not None:
        return StateReason(code="clamped", detail=clamp.reason, applied_value=clamp.applied, since=clamp.at)
    if experiment:
        return StateReason(code="experiment")
    if push is not None and push.status == DEFERRED_STATUS:
        return StateReason(code="deferred", since=push.at)
    if push is not None and push.status == FAILED_STATUS:
        return StateReason(code="push_failed", detail=push.status, since=push.at)
    if not reporting.ingestor_fresh:
        return StateReason(code="writer_down", since=reporting.heartbeat_at)
    if not reporting.controller_reporting:
        return StateReason(code="controller_not_reporting", since=reporting.climate_at)
    return StateReason(code="no_match", since=push.at if push is not None else None)


def evaluate(spec: TunableDef, facts: _Facts, shared: _Shared) -> _Evaluation:
    """§5.5 readback state: the first that holds of released, overridden, held, confirmed, no_readback,
    requested and not_confirmed, with ``state_reason`` for requested, not_confirmed and held.

    Only readbacks at or after the request count. A desired value confirms by ``readback_matches()``; a plan
    value or a band or default push by ``readback_values_equivalent()``. A clamp holds the value only while the
    newest readback does not confirm it, so a guardrail that let go does not hold the setting forever.
    """
    kind, request = _request_of(spec, facts)
    if kind == "released":
        return _Evaluation("released")
    if kind == "overridden":
        return _Evaluation("overridden")
    if spec.control_class == "irrigation_site":
        # Before the engine firmware a site value is stored and stays requested (§5.8); #1155 adds the program
        # hash readback and no_engine.
        if request is None:
            return _Evaluation("no_readback")
        return _Evaluation("requested", _reason(None, False, None, shared.reporting))
    route = readback_via(spec)
    if request is None:
        if route == "none":
            return _Evaluation("no_readback")
        return _Evaluation("confirmed" if facts.readback is not None else "not_confirmed")
    newest = facts.readback if facts.readback is not None and facts.readback.at >= request.since else None
    matches = newest is not None and request.matches(spec.name, newest.value, request.value)
    experiment = shared.experiment_armed and spec.name in POLICY_FIELDS
    clamp = facts.clamp if not matches else None
    if experiment or clamp is not None:
        state: ReadbackState = "held"
    elif matches:
        return _Evaluation("confirmed", confirmed_at=facts.first_match_at if kind == "desired" else None)
    elif route == "none":
        return _Evaluation("no_readback")
    elif shared.at - request.since < REQUESTED_FOR:
        state = "requested"
    else:
        state = "not_confirmed"
    return _Evaluation(state, _reason(clamp, experiment, facts.push, shared.reporting))


# ── Formatting refusals ─────────────────────────────────────────────────────


def _decimals(step: float | None) -> int | None:
    if not step:
        return None
    exponent = Decimal(str(step)).normalize().as_tuple().exponent
    return max(0, -exponent) if isinstance(exponent, int) else None


def _number(value: float, step: float | None) -> str:
    decimals = _decimals(step)
    return f"{value:.{decimals}f}" if decimals is not None else f"{value:g}"


def _with_unit(text: str, unit: str | None) -> str:
    if not unit:
        return text
    return f"{text}{unit}" if unit == "%" else f"{text} {unit}"


def _range_text(spec: TunableDef, lo: float | None, hi: float | None) -> str:
    if lo is not None and hi is not None:
        return _with_unit(f"Must be {_number(lo, spec.step)}-{_number(hi, spec.step)}", spec.unit)
    if lo is not None:
        return _with_unit(f"Must be at least {_number(lo, spec.step)}", spec.unit)
    if hi is not None:
        return _with_unit(f"Must be at most {_number(hi, spec.step)}", spec.unit)
    return "Must be a finite number"


# ── Database reads (bounded; parameters only, never text from a request) ────

_DESIRED_SQL = """
SELECT id, param, value, manual, source, actor, reason, created_at
  FROM v_desired_state_current
 WHERE greenhouse_id = $1 AND param = ANY($2::text[])
"""

_DESIRED_BY_ID_SQL = """
SELECT id, param, value, manual, source, actor, reason, created_at FROM desired_state WHERE id = $1
"""

_ACTIVE_PLAN_SQL = (
    "SELECT parameter, value, ts, plan_id, created_at FROM v_active_plan WHERE parameter = ANY($1::text[])"
)

# The newest readback of each setting: cfg_* sensors and native Number/Switch states (not the band audit rows).
_SNAPSHOT_SQL = """
SELECT p.param, s.value, s.ts
  FROM unnest($2::text[]) AS p(param)
  JOIN LATERAL (SELECT value, ts FROM setpoint_snapshot
                 WHERE greenhouse_id = $1 AND parameter = p.param AND band_role IS NULL
                   AND ts > now() - $3::interval
                 ORDER BY ts DESC LIMIT 1) AS s ON true
"""

# The newest equipment_state row of each switch's mirror.
_EQUIPMENT_SQL = """
SELECT p.param, e.state, e.ts
  FROM unnest($2::text[], $3::text[]) AS p(param, equipment)
  JOIN LATERAL (SELECT state, ts FROM equipment_state
                 WHERE greenhouse_id = $1 AND equipment = p.equipment AND ts > now() - $4::interval
                 ORDER BY ts DESC LIMIT 1) AS e ON true
"""

# The dispatcher's newest push of each setting; rows the controller reported (source esp32) are not pushes.
_LAST_PUSH_SQL = """
SELECT p.param, c.value, c.ts
  FROM unnest($2::text[]) AS p(param)
  JOIN LATERAL (SELECT value, ts FROM setpoint_changes
                 WHERE greenhouse_id = $1 AND parameter = p.param AND ts > now() - $3::interval
                   AND source IS DISTINCT FROM $4
                 ORDER BY ts DESC LIMIT 1) AS c ON true
"""

# For each (setting, request time): the newest push and the newest clamp at or after the request.
_REQUEST_FACTS_SQL = """
SELECT c.param, push.ts AS push_at, push.delivery_status, clamp.ts AS clamp_at, clamp.applied, clamp.reason
  FROM unnest($2::text[], $3::timestamptz[]) AS c(param, since)
  LEFT JOIN LATERAL (SELECT ts, delivery_status FROM setpoint_changes
                      WHERE greenhouse_id = $1 AND parameter = c.param AND ts >= c.since
                        AND source IS DISTINCT FROM $4
                      ORDER BY ts DESC LIMIT 1) AS push ON true
  LEFT JOIN LATERAL (SELECT ts, applied, reason FROM setpoint_clamps
                      WHERE greenhouse_id = $1 AND parameter = c.param AND ts >= c.since
                      ORDER BY ts DESC LIMIT 1) AS clamp ON true
"""

# For each (setting, since, until, value, tolerance, mirror): the first readback in [since, until) that confirms
# the value, from setpoint_snapshot and, for a switch with one, its equipment mirror. until NULL is open.
_FIRST_MATCH_SQL = """
SELECT c.key,
       (SELECT s.ts FROM setpoint_snapshot AS s
         WHERE s.greenhouse_id = $1 AND s.parameter = c.param AND s.band_role IS NULL
           AND s.ts >= c.since AND (c.until IS NULL OR s.ts < c.until) AND abs(s.value - c.value) <= c.tol
         ORDER BY s.ts LIMIT 1) AS snapshot_at,
       (SELECT e.ts FROM equipment_state AS e
         WHERE c.equipment IS NOT NULL AND e.greenhouse_id = $1 AND e.equipment = c.equipment
           AND e.ts >= c.since AND (c.until IS NULL OR e.ts < c.until) AND e.state = (c.value >= 0.5)
         ORDER BY e.ts LIMIT 1) AS equipment_at
  FROM unnest($2::bigint[], $3::text[], $4::timestamptz[], $5::timestamptz[], $6::float8[], $7::float8[], $8::text[])
       AS c(key, param, since, until, value, tol, equipment)
"""

_SEASON_SQL = "SELECT fn_current_season()"
_ANCHOR_ROWS_SQL = "SELECT id, crop_type, series, anchor, season FROM crop_band_anchors WHERE greenhouse_id = $1"


@asynccontextmanager
async def _snapshot(conn: asyncpg.Connection) -> AsyncIterator[None]:
    """One read-only snapshot for a whole read, so every ``now()`` in it agrees."""
    if conn.is_in_transaction():
        yield
        return
    async with conn.transaction(isolation="repeatable_read", readonly=True):
        yield


def _tolerance(param: str, value: float) -> float:
    try:
        return readback_tolerance(param, value)
    except ValueError:  # a value binary32 cannot hold never matches
        return -1.0


async def _shared(conn: asyncpg.Connection) -> _Shared:
    from verdify_services import reads  # here, not at the top: reads imports this module

    reporting = await reads.reporting(conn)
    armed = await conn.fetchval(activity.ARMED_EXPERIMENT_SQL)
    return _Shared(reporting=reporting, experiment_armed=armed is not None)


async def _readbacks(conn: asyncpg.Connection, names: Sequence[str]) -> dict[str, _Readback]:
    """The newest readback of each setting: setpoint_snapshot, or the equipment mirror when that is newer."""
    out = {
        row["param"]: _Readback(_finite(row["value"]), row["ts"])
        for row in await conn.fetch(_SNAPSHOT_SQL, GREENHOUSE_ID, list(names), READBACK_WINDOW)
    }
    switches = [name for name in names if name in SWITCH_READBACK_EQUIPMENT]
    if switches:
        rows = await conn.fetch(
            _EQUIPMENT_SQL,
            GREENHOUSE_ID,
            switches,
            [SWITCH_READBACK_EQUIPMENT[name] for name in switches],
            EQUIPMENT_WINDOW,
        )
        for row in rows:
            mirror = _Readback(1.0 if row["state"] else 0.0, row["ts"])
            current = out.get(row["param"])
            if current is None or mirror.at > current.at:
                out[row["param"]] = mirror
    return out


async def _first_matches(
    conn: asyncpg.Connection, items: Sequence[tuple[int, str, datetime, datetime | None, float]]
) -> dict[int, datetime]:
    """For each (key, param, since, until, value): the first readback in [since, until) that confirms the value
    by ``readback_matches()`` (the comparator's tolerance, computed here)."""
    if not items:
        return {}
    rows = await conn.fetch(
        _FIRST_MATCH_SQL,
        GREENHOUSE_ID,
        [key for key, *_ in items],
        [param for _, param, *_ in items],
        [since for _, _, since, _, _ in items],
        [until for _, _, _, until, _ in items],
        [value for *_, value in items],
        [_tolerance(param, value) for _, param, _, _, value in items],
        [SWITCH_READBACK_EQUIPMENT.get(param) for _, param, *_ in items],
    )
    out: dict[int, datetime] = {}
    for row in rows:
        times = [at for at in (row["snapshot_at"], row["equipment_at"]) if at is not None]
        if times:
            out[row["key"]] = min(times)
    return out


async def _gather(
    conn: asyncpg.Connection,
    specs: Sequence[TunableDef],
    *,
    desired_rows: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[dict[str, _Facts], _Shared]:
    """Every fact the states of ``specs`` are computed from, in a few batched queries.

    ``desired_rows`` replaces the ``v_desired_state_current`` read (a write's own row, or a replayed one).
    """
    names = [spec.name for spec in specs]
    shared = await _shared(conn)
    if desired_rows is None:
        desired_rows = {row["param"]: row for row in await conn.fetch(_DESIRED_SQL, GREENHOUSE_ID, names)}
    plans = {row["parameter"]: row for row in await conn.fetch(_ACTIVE_PLAN_SQL, names)}
    readbacks = await _readbacks(conn, names)
    facts = {
        name: _Facts(desired=desired_rows.get(name), plan=plans.get(name), readback=readbacks.get(name))
        for name in names
    }
    # A band or default setting with no desired row and no plan is judged against the newest push.
    unrequested = [
        name for name in names if _writable_desired(name, facts[name].desired) is None and facts[name].plan is None
    ]
    if unrequested:
        for row in await conn.fetch(_LAST_PUSH_SQL, GREENHOUSE_ID, unrequested, PUSH_WINDOW, DEVICE_OBSERVED_SOURCE):
            facts[row["param"]].last_push = row
    requests = {spec.name: _request_of(spec, facts[spec.name]) for spec in specs}
    pending = {name: request for name, (_, request) in requests.items() if request is not None}
    if pending:
        rows = await conn.fetch(
            _REQUEST_FACTS_SQL,
            GREENHOUSE_ID,
            list(pending),
            [request.since for request in pending.values()],
            DEVICE_OBSERVED_SOURCE,
        )
        for row in rows:
            item = facts[row["param"]]
            if row["push_at"] is not None:
                item.push = _Push(row["push_at"], row["delivery_status"])
            if row["clamp_at"] is not None:
                item.clamp = _Clamp(row["clamp_at"], _finite(row["applied"]), row["reason"])
    # confirmed_at: the first readback that confirms a desired value, for the desired values that read confirmed.
    confirmed = []
    for spec in specs:
        kind, request = requests[spec.name]
        if kind != "desired" or request is None:
            continue
        if evaluate(spec, facts[spec.name], shared).state == "confirmed":
            confirmed.append((len(confirmed), spec.name, request.since, None, request.value))
    for index, at in (await _first_matches(conn, confirmed)).items():
        facts[confirmed[index][1]].first_match_at = at
    return facts, shared


async def _anchor_rows(conn: asyncpg.Connection) -> tuple[list[dict[str, Any]], str | None]:
    """Every ``crop_band_anchors`` row of the greenhouse and the current season (as the dispatcher reads them)."""
    season = await conn.fetchval(_SEASON_SQL)
    rows = [dict(row) for row in await conn.fetch(_ANCHOR_ROWS_SQL, GREENHOUSE_ID)]
    return rows, (str(season).strip().lower() if season else None)


# ── Building the models ─────────────────────────────────────────────────────


def _desired_setting(
    row: Mapping[str, Any],
    evaluation: _Evaluation,
    *,
    snapped_from: float | None = None,
    manual_before: bool | None = None,
) -> DesiredSetting:
    return DesiredSetting(
        id=row["id"],
        param=row["param"],
        value=None if row["value"] is None else float(row["value"]),
        snapped_from=snapped_from,
        manual=bool(row["manual"]),
        manual_before=manual_before,
        state=evaluation.state,
        state_reason=evaluation.reason,
        confirmed_at=evaluation.confirmed_at,
        actor=ActorRef(source=row["source"], name=row["actor"]),
        reason=row["reason"],
        created_at=row["created_at"],
    )


def _reading(spec: TunableDef, facts: _Facts) -> Reading | None:
    route = readback_via(spec)
    if route == "none":
        return None
    if route == "program":
        return Reading(value=None, observed_at=None, reason=NO_PROGRAM_READBACK)
    if facts.readback is None:
        return Reading(value=None, observed_at=None, reason=NO_READBACK_REASON)
    return Reading(value=facts.readback.value, observed_at=facts.readback.at)


def _setting_status(spec: TunableDef, facts: _Facts, shared: _Shared) -> SettingStatus:
    evaluation = evaluate(spec, facts, shared)
    desired = _writable_desired(spec.name, facts.desired)
    return SettingStatus(
        param=spec.name,
        applied_from=applied_from(spec, facts.desired, facts.plan),
        state=evaluation.state,
        state_reason=evaluation.reason,
        confirmed_at=evaluation.confirmed_at,
        readback_via=readback_via(spec),
        readback=_reading(spec, facts),
        desired=_desired_setting(desired, evaluation) if desired is not None else None,
    )


def _options(spec: TunableDef) -> list[SettingOption]:
    if spec.kind != "enum" or not spec.enum_values:
        return []
    return [
        SettingOption(value=float(value), label=label)
        for label, value in sorted(spec.enum_values.items(), key=lambda item: item[1])
    ]


def _write_rule(spec: TunableDef, anchors: Mapping[str, tuple[int, ...]]) -> tuple[bool, str | None]:
    """§5.3 step 1: the registry rule, and for a crop-band setting its ``crop_band_anchors`` rows."""
    writable, reason = desired_write_rule(spec.name)
    if writable and spec.control_class == "crop_band" and not anchors.get(spec.name):
        return False, NO_ANCHOR_ROW
    return writable, reason


def _anchors_in_force(rows: Sequence[Mapping[str, Any]], season: str | None) -> dict[str, tuple[int, ...]]:
    out: dict[str, tuple[int, ...]] = {}
    for name, spec in REGISTRY.items():
        target = band_rows(name) if spec.control_class == "crop_band" else None
        if target is not None:
            out[name] = rows_in_force(target, rows, season)
    return out


def _setting(spec: TunableDef, facts: _Facts, shared: _Shared, anchors: Mapping[str, tuple[int, ...]]) -> Setting:
    writable, reason = _write_rule(spec, anchors)
    if not writable:
        group: SettingGroup = "read_only"
    elif spec.control_class == "irrigation_site":
        group = "site_water_caps"
    else:
        group = spec.control_class  # type: ignore[assignment]
    status = _setting_status(spec, facts, shared)
    lo, hi = bounds(spec.name)
    return Setting(
        param=spec.name,
        label=spec.label or spec.name,
        control_class=spec.control_class,
        group=group,
        kind=spec.kind,
        unit=spec.unit,
        min=lo,
        max=hi,
        step=spec.step,
        options=_options(spec),
        writable=writable,
        read_only_reason=reason,
        readback_via=status.readback_via,
        readback=status.readback,
        desired=status.desired,
        applied_from=status.applied_from,
        state=status.state,
    )


# ── Reads ───────────────────────────────────────────────────────────────────


def _spec_or_not_found(param: str) -> TunableDef:
    spec = REGISTRY.get(param) if isinstance(param, str) else None
    if spec is None:
        raise ServiceError(problem("not_found", f"{param!r} is not a setting", field="param"))
    return spec


async def catalog(conn: asyncpg.Connection) -> list[Setting]:
    """API-09's rows: a ``Setting`` for every ``REGISTRY`` row, in registry order.

    ``label`` and ``unit`` are ``TunableDef.label`` and ``TunableDef.unit``, unchanged. ``group`` is the
    ``control_class`` of a writable row, ``site_water_caps`` for the ``irrigation_site`` rows and ``read_only``
    (with ``read_only_reason``) for every row §5.3 step 1 refuses.
    """
    specs = list(REGISTRY.values())
    async with _snapshot(conn):
        facts, shared = await _gather(conn, specs)
        anchors = _anchors_in_force(*await _anchor_rows(conn))
    return [_setting(spec, facts[spec.name], shared, anchors) for spec in specs]


async def status(conn: asyncpg.Connection, param: str) -> SettingStatus:
    """§5.5 on every read: whose value applies, the readback state, its reason and the newest readback.

    Display only: no state raises an alert (``setpoint_unconfirmed`` keeps covering device writes).
    """
    spec = _spec_or_not_found(param)
    async with _snapshot(conn):
        facts, shared = await _gather(conn, [spec])
    return _setting_status(spec, facts[spec.name], shared)


async def statuses(conn: asyncpg.Connection, params: Sequence[str]) -> dict[str, SettingStatus]:
    """``status()`` of several settings in one batched read, by param (API-07's lanes, so Plan and Settings show the
    same state and reason). Names outside ``REGISTRY`` are left out. Display only, as ``status()``."""
    specs = [REGISTRY[name] for name in dict.fromkeys(params) if isinstance(name, str) and name in REGISTRY]
    if not specs:
        return {}
    async with _snapshot(conn):
        facts, shared = await _gather(conn, specs)
    return {spec.name: _setting_status(spec, facts[spec.name], shared) for spec in specs}


def _row_evaluation(
    row: Mapping[str, Any],
    spec: TunableDef,
    facts: _Facts,
    shared: _Shared,
    current: Mapping[str, Any] | None,
    matched: Mapping[int, datetime],
) -> _Evaluation:
    """One desired row's state, as ``history`` and ``desired_states`` give it: the setting's newest (writable) row
    reads its full ``evaluate()``; an older row reads released, or confirmed (``confirmed_at``) when a readback
    before the next row confirmed it (``matched``), else not_confirmed."""
    if current is not None and row["id"] == current["id"]:
        return evaluate(spec, facts, shared)
    if row["value"] is None:
        return _Evaluation("released")
    if row["id"] in matched:
        return _Evaluation("confirmed", confirmed_at=matched[row["id"]])
    return _Evaluation("not_confirmed")


_HISTORY_DESIRED_SQL = """
SELECT id, param, value, manual, source, actor, reason, created_at
  FROM desired_state
 WHERE greenhouse_id = $1 AND param = $2 AND created_at < $4
   AND (created_at >= $3
        OR id = (SELECT id FROM desired_state
                  WHERE greenhouse_id = $1 AND param = $2 AND created_at < $3
                  ORDER BY created_at DESC, id DESC LIMIT 1))
 ORDER BY created_at, id
"""

_HISTORY_NEXT_SQL = """
SELECT created_at FROM desired_state
 WHERE greenhouse_id = $1 AND param = $2 AND created_at >= $3
 ORDER BY created_at, id LIMIT 1
"""

_READBACK_POINTS_SQL = """
SELECT ts, value FROM (
    SELECT ts, value, lag(value) OVER (ORDER BY ts) AS previous
      FROM setpoint_snapshot
     WHERE greenhouse_id = $1 AND parameter = $2 AND band_role IS NULL AND ts >= $3 AND ts < $4
) AS points
 WHERE previous IS DISTINCT FROM value
 ORDER BY ts
"""

_READBACK_BEFORE_SQL = """
SELECT ts, value FROM setpoint_snapshot
 WHERE greenhouse_id = $1 AND parameter = $2 AND band_role IS NULL AND ts < $3 AND ts > $3 - $4::interval
 ORDER BY ts DESC LIMIT 1
"""


def _aware(name: str, value: object) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ServiceError(problem("invalid_request", f"{name} needs a time with its UTC offset", field=name))
    return value


async def history(conn: asyncpg.Connection, param: str, from_: datetime, to: datetime) -> SettingHistory:
    """API-12: the desired rows (oldest first, from the row in force at ``from_``) and the readback change
    points over [``from_``, ``to``).

    The newest row reads its full ``status``; an older row reads released, or confirmed (with
    ``confirmed_at``) when a readback before the next row confirmed it, else not_confirmed. ``readback``
    starts with the newest readback before ``from_`` (readbacks are kept 90 days). ``plan`` stays empty:
    #1168 adds the waypoints.
    """
    spec = _spec_or_not_found(param)
    start, end = _aware("from", from_), _aware("to", to)
    if start >= end:
        raise ServiceError(problem("invalid_request", "from must be before to", field="from"))
    lo, hi = bounds(param)
    async with _snapshot(conn):
        rows = await conn.fetch(_HISTORY_DESIRED_SQL, GREENHOUSE_ID, param, start, end)
        after = await conn.fetchval(_HISTORY_NEXT_SQL, GREENHOUSE_ID, param, end)
        facts, shared = await _gather(conn, [spec])
        current = _writable_desired(param, facts[param].desired)
        items: list[tuple[int, str, datetime, datetime | None, float]] = []
        for index, row in enumerate(rows):
            if current is not None and row["id"] == current["id"]:
                continue
            value = _finite(row["value"])
            if value is None:
                continue
            until = rows[index + 1]["created_at"] if index + 1 < len(rows) else after
            items.append((row["id"], param, row["created_at"], until, value))
        matched = await _first_matches(conn, items)
        before = await conn.fetchrow(_READBACK_BEFORE_SQL, GREENHOUSE_ID, param, start, EQUIPMENT_WINDOW)
        points = await conn.fetch(_READBACK_POINTS_SQL, GREENHOUSE_ID, param, start, end)
    desired = [
        _desired_setting(row, _row_evaluation(row, spec, facts[param], shared, current, matched)) for row in rows
    ]
    readback: list[TimedValue] = []
    for point in ([before] if before is not None else []) + list(points):
        value = _finite(point["value"])
        if readback and readback[-1].value == value:
            continue
        readback.append(TimedValue(at=point["ts"], value=value))
    return SettingHistory.model_validate(
        {
            "param": param,
            "unit": spec.unit,
            "min": lo,
            "max": hi,
            "from": start,
            "to": end,
            "desired": desired,
            "readback": readback,
        }
    )


# Each named row and the time its setting's next row was written (None for the newest row).
_DESIRED_ROWS_SQL = """
SELECT d.id, d.param, d.value, d.manual, d.source, d.actor, d.reason, d.created_at,
       (SELECT n.created_at FROM desired_state AS n
         WHERE n.greenhouse_id = d.greenhouse_id AND n.param = d.param
           AND (n.created_at, n.id) > (d.created_at, d.id)
         ORDER BY n.created_at, n.id LIMIT 1) AS until
  FROM desired_state AS d
 WHERE d.greenhouse_id = $1 AND d.id = ANY($2::bigint[])
"""
_BIGINT_MAX = 2**63 - 1


async def desired_states(conn: asyncpg.Connection, ids: Sequence[int]) -> dict[int, DesiredSetting]:
    """The named ``desired_state`` rows with their readback state, by id (API-17's ``setting.write`` and
    ``setting.release`` rows, through ``detail.desired_id``).

    ``history``'s rule: a setting's newest row reads its full state, as ``status()`` gives it; an older row reads
    released, or confirmed (with ``confirmed_at``) when a readback before the next row confirmed it, else
    not_confirmed. An id that names no row, or a row of a setting outside ``REGISTRY``, is left out. Display only.
    """
    keys = sorted({key for key in ids if type(key) is int and 1 <= key <= _BIGINT_MAX})
    if not keys:
        return {}
    async with _snapshot(conn):
        rows = [row for row in await conn.fetch(_DESIRED_ROWS_SQL, GREENHOUSE_ID, keys) if row["param"] in REGISTRY]
        specs = [REGISTRY[param] for param in dict.fromkeys(row["param"] for row in rows)]
        if not specs:
            return {}
        facts, shared = await _gather(conn, specs)
        current = {spec.name: _writable_desired(spec.name, facts[spec.name].desired) for spec in specs}
        older: list[tuple[int, str, datetime, datetime | None, float]] = []
        for row in rows:
            newest = current[row["param"]]
            value = _finite(row["value"])
            if (newest is None or row["id"] != newest["id"]) and value is not None:
                older.append((row["id"], row["param"], row["created_at"], row["until"], value))
        matched = await _first_matches(conn, older)
    return {
        row["id"]: _desired_setting(
            row,
            _row_evaluation(row, REGISTRY[row["param"]], facts[row["param"]], shared, current[row["param"]], matched),
        )
        for row in rows
    }


# ── Validation (architecture §5.3) ──────────────────────────────────────────


@dataclass(frozen=True)
class _Checked:
    spec: TunableDef
    value: float
    requested: float
    snapped_from: float | None
    current: float | None
    manual_before: bool
    band: BandRows | None
    anchor_ids: tuple[int, ...]


def _current(spec: TunableDef, facts: _Facts) -> float | None:
    """The value in force (``resolve_effective()`` over the desired and plan rows, else the plan), else the
    device readback."""
    resolved = resolve_effective(_writable_desired(spec.name, facts.desired), facts.plan)
    if resolved is not None:
        return resolved.value
    if facts.plan is not None and (plan_value := _finite(facts.plan["value"])) is not None:
        return plan_value
    return facts.readback.value if facts.readback is not None else None


def _refuse(code: str, detail: str, spec: TunableDef | None, current: float | None, *, field: str) -> ServiceError:
    lo, hi = bounds(spec.name) if spec is not None else (None, None)
    return ServiceError(problem(code, detail, field=field, min=lo, max=hi, current=current))  # type: ignore[arg-type]


async def _validation_facts(conn: asyncpg.Connection, spec: TunableDef) -> _Facts:
    desired = await conn.fetchrow(_DESIRED_SQL, GREENHOUSE_ID, [spec.name])
    plan = await conn.fetchrow(_ACTIVE_PLAN_SQL, [spec.name])
    readback = (await _readbacks(conn, [spec.name])).get(spec.name)
    return _Facts(desired=desired, plan=plan, readback=readback)


async def _check_writable(
    conn: asyncpg.Connection, param: str
) -> tuple[TunableDef, _Facts, BandRows | None, tuple[int, ...]]:
    """§5.3 step 1: known (``unknown_target``) and writable now (``read_only`` with the reason)."""
    spec = REGISTRY.get(param) if isinstance(param, str) else None
    if spec is None:
        raise ServiceError(problem("unknown_target", f"{param!r} is not a setting", field="param"))
    facts = await _validation_facts(conn, spec)
    writable, reason = desired_write_rule(param)
    target: BandRows | None = None
    anchor_ids: tuple[int, ...] = ()
    if writable and spec.control_class == "crop_band":
        target = band_rows(param)
        if target is not None:
            anchor_ids = rows_in_force(target, *await _anchor_rows(conn))
        if not anchor_ids:
            writable, reason = False, NO_ANCHOR_ROW
    if not writable:
        raise _refuse("read_only", f"{param} is read-only: {reason}", spec, _current(spec, facts), field="param")
    return spec, facts, target, anchor_ids


def _check_manual(actor: Actor, spec: TunableDef, facts: _Facts) -> None:
    """§5.3 step 3: Iris never overwrites or releases a value a person holds manual (Q-02)."""
    desired = facts.desired
    if actor.source == "iris" and desired is not None and desired["value"] is not None and desired["manual"]:
        raise _refuse(
            "manual",
            f"{spec.name} is held manual by {desired['actor']}; Iris's values for it are ignored until it is released",
            spec,
            _finite(desired["value"]),
            field="param",
        )


def _check_value(spec: TunableDef, value: object, current: float | None) -> float:
    """§5.3 step 2: a finite number; a switch 0 or 1; an enum option; inside ``planner_effective_bounds``."""
    number = _finite(value) if isinstance(value, int | float) else None
    lo, hi = bounds(spec.name)
    if number is None:
        raise _refuse("out_of_range", _range_text(spec, lo, hi), spec, current, field="value")
    if spec.kind == "switch" and number not in (0.0, 1.0):
        raise ServiceError(
            problem("out_of_range", "Must be 0 or 1", field="value", min=0, max=1, current=current)  # type: ignore[arg-type]
        )
    if spec.kind == "enum":
        allowed = sorted((spec.enum_values or {}).values())
        if number % 1 != 0 or int(number) not in allowed:
            text = ", ".join(f"{option} ({label})" for label, option in sorted((spec.enum_values or {}).items()))
            raise ServiceError(
                problem(
                    "out_of_range",
                    f"Must be one of {text}",
                    field="value",
                    min=min(allowed) if allowed else None,
                    max=max(allowed) if allowed else None,
                    current=current,  # type: ignore[arg-type]
                )
            )
    if (lo is not None and number < lo) or (hi is not None and number > hi):
        raise _refuse("out_of_range", _range_text(spec, lo, hi), spec, current, field="value")
    return number


def _snap(spec: TunableDef, number: float, facts: _Facts) -> float:
    """§5.3 step 4: the registry step, half to even; a value equal (within its binary32 ULP) to the value in
    force or the device value is stored as is, so taking Iris's 48.75 manual keeps 48.75."""
    in_force = resolve_effective(_writable_desired(spec.name, facts.desired), facts.plan)
    candidates = [
        in_force.value if in_force is not None else (_finite(facts.plan["value"]) if facts.plan is not None else None),
        facts.readback.value if facts.readback is not None else None,
    ]
    tolerance = binary32_ulp(number)
    if any(candidate is not None and abs(number - candidate) <= tolerance for candidate in candidates):
        return number
    return snap_setting(spec.name, number)


async def _validate(conn: asyncpg.Connection, actor: Actor, param: str, value: object) -> _Checked:
    spec, facts, target, anchor_ids = await _check_writable(conn, param)
    current = _current(spec, facts)
    number = _check_value(spec, value, current)
    _check_manual(actor, spec, facts)
    stored = _snap(spec, number, facts)
    return _Checked(
        spec=spec,
        value=stored,
        requested=number,
        snapped_from=number if stored != number else None,
        current=current,
        manual_before=bool(facts.desired is not None and facts.desired["manual"]),
        band=target,
        anchor_ids=anchor_ids,
    )


async def validate(conn: asyncpg.Connection, actor: Actor, param: str, value: float) -> ValidatedSetting:
    """Architecture §5.3, the only validator for desired state: the first failure raises ``ServiceError``.

    1. Known and writable (``unknown_target``; ``read_only`` with the reason, a crop-band setting included
       while ``crop_band_anchors`` holds none of its rows).
    2. Type and range (``out_of_range`` with ``min`` and ``max``).
    3. Manual: an ``iris`` write to a setting whose newest row is manual (``manual``).
    4. Snap to the registry step, half to even, unless the value equals the value in force or the device
       value within its binary32 ULP. ``snapped_from`` is the submitted value when snapping changed it.

    Every refusal is 422 with ``field``, ``min``, ``max`` (``planner_effective_bounds``), ``current`` and
    ``retryable`` false. Nothing is written.
    """
    checked = await _validate(conn, actor, param, value)
    return ValidatedSetting(
        param=checked.spec.name,
        value=checked.value,
        requested=checked.requested,
        snapped_from=checked.snapped_from,
        current=checked.current,
        manual_before=checked.manual_before,
    )


# ── Writes ──────────────────────────────────────────────────────────────────

_INSERT_DESIRED_SQL = """
INSERT INTO desired_state (greenhouse_id, param, value, manual, source, actor, reason)
VALUES ($1, $2, $3, $4, $5, $6, $7)
RETURNING id, param, value, manual, source, actor, reason, created_at
"""

# The only crop_band_anchors columns a write sets (the runtime logins' column grants); the column is a fixed
# name from verdify_schemas.band_rows, never request text.
_BAND_COLUMNS = frozenset({"value", "width_below", "width_above"})
_CORE_DETAIL_KEYS = frozenset({"ref", "desired_id", "before", "value", "requested", "manual", "anchor_rows"})


def _extra(extra_detail: Mapping[str, Any] | None) -> dict[str, Any]:
    """The caller's extra activity detail (#1171: ``trigger_id``, ``planner_instance``); it never replaces a
    key the write sets itself."""
    extra = dict(extra_detail or {})
    clash = sorted(_CORE_DETAIL_KEYS & set(extra))
    if clash:
        raise ValueError(f"extra_detail may not set {', '.join(clash)}")
    return extra


def _manual_flag(manual: object) -> bool:
    if not isinstance(manual, bool):
        raise ServiceError(problem("invalid_request", "manual must be true or false", field="manual"))
    return manual


def _hashable(value: object) -> object:
    """The request's value as the hash sees it: a finite number as a float; anything else as text, so a value
    validation refuses (out_of_range) still has a request to hash."""
    number = _finite(value) if isinstance(value, int | float) else None
    return number if number is not None else repr(value)


async def _update_band_rows(conn: asyncpg.Connection, checked: _Checked) -> list[int]:
    target = checked.band
    if target is None or not checked.anchor_ids:
        return []
    if target.column not in _BAND_COLUMNS:
        raise ValueError(f"{target.column} is not a crop_band_anchors column a write may set")
    rows = await conn.fetch(
        f"UPDATE crop_band_anchors SET {target.column} = $1 WHERE id = ANY($2::int[]) RETURNING id",
        checked.value,
        list(checked.anchor_ids),
    )
    return sorted(row["id"] for row in rows)


async def _stored(
    conn: asyncpg.Connection,
    spec: TunableDef,
    row: Mapping[str, Any],
    *,
    snapped_from: float | None,
    manual_before: bool | None,
) -> DesiredSetting:
    """A stored row as a write returns it, with its state as of now."""
    facts, shared = await _gather(conn, [spec], desired_rows={spec.name: row})
    return _desired_setting(
        row, evaluate(spec, facts[spec.name], shared), snapped_from=snapped_from, manual_before=manual_before
    )


def _loader(param: str):
    async def load(conn: asyncpg.Connection, logged: ActivityLogRow) -> DesiredSetting:
        """A replay: the stored row, its submitted value and flag from the activity detail, its state now."""
        row = await conn.fetchrow(_DESIRED_BY_ID_SQL, (logged.detail.get("ref") or {}).get("id"))
        spec = REGISTRY.get(param)
        if row is None or spec is None:
            raise ServiceError(problem("not_found", f"the stored write for {param} is gone", field="param"))
        value, requested = logged.detail.get("value"), logged.detail.get("requested")
        snapped_from = requested if value is not None and requested is not None and requested != value else None
        manual_before = (logged.detail.get("manual") or {}).get("before")
        return await _stored(conn, spec, row, snapped_from=snapped_from, manual_before=manual_before)

    return load


async def write(
    conn: asyncpg.Connection,
    actor: Actor,
    param: str,
    value: float,
    manual: bool,
    reason: str | None,
    request_id: Any,
    extra_detail: Mapping[str, Any] | None = None,
) -> WriteResult[DesiredSetting]:
    """API-10 and MCP ``set_setting``: validate (§5.3), then one ``recorded_write`` transaction.

    It inserts the ``desired_state`` row with ``manual`` exactly as passed (no default here) and the snapped
    value. A crop-band value write (``band_<series>_<anchor>``, ``zone_vpd_target_<zone>_<anchor>``) updates
    ``value`` on every ``crop_band_anchors`` row of its crop types, series and anchor at the best season score
    present; a width write (``zone_vpd_width_{below,above}_<zone>``) updates that column on every row of the
    zone crop's ``vpd_target`` series at that score. The ``activity_log`` row is ``setting.write`` with detail
    ``{ref, desired_id, before, value, requested, manual: {before, after}}``, ``anchor_rows`` for a band write,
    merged with ``extra_detail``. Returns ``DesiredSetting`` with ``state``, ``snapped_from`` and
    ``manual_before``. The insert trigger wakes the dispatcher; nothing here writes ``setpoint_changes``.
    """
    flag = _manual_flag(manual)
    extra = _extra(extra_detail)
    request = {"value": _hashable(value), "manual": flag, "reason": reason}

    async def perform(tx: asyncpg.Connection) -> tuple[DesiredSetting, dict[str, Any]]:
        checked = await _validate(tx, actor, param, value)
        row = await tx.fetchrow(
            _INSERT_DESIRED_SQL, GREENHOUSE_ID, param, checked.value, flag, actor.source, actor.name, reason
        )
        anchor_rows = await _update_band_rows(tx, checked)
        stored = await _stored(
            tx, checked.spec, row, snapped_from=checked.snapped_from, manual_before=checked.manual_before
        )
        detail: dict[str, Any] = {
            **extra,
            "ref": {"table": "desired_state", "id": row["id"]},
            "desired_id": row["id"],
            "before": checked.current,
            "value": checked.value,
            "requested": checked.requested,
            "manual": {"before": checked.manual_before, "after": flag},
        }
        if anchor_rows:
            detail["anchor_rows"] = anchor_rows
        return stored, detail

    return await activity.recorded_write(
        conn,
        actor,
        op="API-10",
        action="setting.write",
        target=param,
        request=request,
        request_id=request_id,
        reason=reason,
        perform=perform,
        load=_loader(param),
    )


async def release(
    conn: asyncpg.Connection,
    actor: Actor,
    param: str,
    reason: str | None,
    request_id: Any,
    extra_detail: Mapping[str, Any] | None = None,
) -> WriteResult[DesiredSetting]:
    """API-11 and MCP ``set_setting(release=True)``: back to plan, band or default.

    Runs §5.3 steps 1 and 3 (so Iris cannot release a value a person holds manual), then appends
    ``value NULL, manual false`` in one ``recorded_write`` transaction with a ``setting.release`` activity row
    (detail ``{ref, desired_id, before, value: null, manual: {before, after: false}}`` merged with
    ``extra_detail``). Returns the release row: state ``released`` and ``manual_before``.
    """
    extra = _extra(extra_detail)

    async def perform(tx: asyncpg.Connection) -> tuple[DesiredSetting, dict[str, Any]]:
        spec, facts, _target, _ids = await _check_writable(tx, param)
        _check_manual(actor, spec, facts)
        manual_before = bool(facts.desired is not None and facts.desired["manual"])
        current = _current(spec, facts)
        row = await tx.fetchrow(
            _INSERT_DESIRED_SQL, GREENHOUSE_ID, param, None, False, actor.source, actor.name, reason
        )
        stored = _desired_setting(row, _Evaluation("released"), manual_before=manual_before)
        detail = {
            **extra,
            "ref": {"table": "desired_state", "id": row["id"]},
            "desired_id": row["id"],
            "before": current,
            "value": None,
            "manual": {"before": manual_before, "after": False},
        }
        return stored, detail

    async def load(tx: asyncpg.Connection, logged: ActivityLogRow) -> DesiredSetting:
        row = await tx.fetchrow(_DESIRED_BY_ID_SQL, (logged.detail.get("ref") or {}).get("id"))
        if row is None:
            raise ServiceError(problem("not_found", f"the stored release of {param} is gone", field="param"))
        before = (logged.detail.get("manual") or {}).get("before")
        return _desired_setting(row, _Evaluation("released"), manual_before=before)

    return await activity.recorded_write(
        conn,
        actor,
        op="API-11",
        action="setting.release",
        target=param,
        request={"reason": reason},
        request_id=request_id,
        reason=reason,
        perform=perform,
        load=load,
    )
