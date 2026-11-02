"""Now, plan and equipment reads (VD-54): API-02, API-07 and API-13.

Architecture §7 (read model) and §8 (API surface). Each read is ``async``,
takes an asyncpg connection, needs no ``Actor`` and returns a Pydantic model
shaped like the committed OpenAPI ``Now``, ``Plan`` or ``Equipment``
(docs/design/dashboard/openapi/dashboard-api.v1.yaml). A read runs all of its
statements in one read-only snapshot, so every age in a response is measured
from one database ``now()``. Every hypertable query is bounded by ``ts``; the
one exception is the writer age, the newest ``setpoint_changes`` row, which the
``ts`` index answers from the newest chunk. API-02 is polled every 15 s and the
dashboard login has a 10 s ``statement_timeout``.

Helpers other reads share: ``reporting()``, the one §7 test (#1155 and #1180
use it too); ``collapse()``, which merges repeated state rows into runs; and
``plan_waypoints()``, which API-12 (#1168) reuses.

Unknown is never off, null is never 0, and gaps stay gaps. Nothing here writes,
and the leak is an alert only (Q-05).
"""

from __future__ import annotations

import json
import math
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

import asyncpg
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from verdify_schemas.alerts import flow_stopped_at
from verdify_schemas.policy_vector import wire_fields
from verdify_schemas.setting_values import readback_matches, readback_tolerance, resolve_effective
from verdify_schemas.tunable_registry import BAND_OWNED_REG, LIGHTING_CIRCUIT_DEFAULT_REG
from verdify_schemas.tunable_registry import get as registry_get
from verdify_services.labels import EQUIPMENT_LABELS, EQUIPMENT_LINE_IDS

DENVER = ZoneInfo("America/Denver")
GREENHOUSE_ID = "vallery"

# The ingestor heartbeat (decisions Q-01): ingestor/tasks/liveness.py writes one
# system_state row with this entity every 60 s from its own task loop.
HEARTBEAT_ENTITY = "ingestor_heartbeat"
HEARTBEAT_CADENCE_S = 60
HEARTBEAT_STALE_S = 180
# The controller reports while the heartbeat is fresh and the newest climate row
# is at most this old (the GET /health threshold in api/main.py).
CLIMATE_REPORTING_S = 300
# Climate, diagnostics and the cfg_* readback snapshot arrive every 60 s; a
# reading older than twice that is stale and gets no band verdict.
SENSOR_CADENCE_S = 60
SENSOR_STALE_S = 120
# Home Assistant polls the Lutron readback every 300 s: two missed polls is unknown.
LUTRON_UNKNOWN_S = 600

STATE_WINDOW = timedelta(days=31)  # runs older than this have since null
DIFFERING_AFTER = timedelta(minutes=10)
REQUESTED_FOR = timedelta(minutes=5)
OVERRIDE_COMMAND_LEAD = timedelta(minutes=2)
LEAK_STOPPED_BELOW_GPM = 0.1
LEAK_STOPPED_READINGS = 2

REQUIRED_TRIGGERS = ("MIDNIGHT", "SUNRISE", "SUNSET")
HOUSE_BAND_ROLES = ("temp_low", "temp_high", "vpd_low", "vpd_high")

EquipmentGroup = Literal["climate", "water", "light"]
# API-13's fixed list, in order. Water is L01-L11 in line order.
EQUIPMENT_LIST: tuple[tuple[str, EquipmentGroup], ...] = (
    ("fan1", "climate"),
    ("fan2", "climate"),
    ("vent", "climate"),
    ("heat1", "climate"),
    ("heat2", "climate"),
    *((name, "water") for name in EQUIPMENT_LINE_IDS),
    ("grow_light_main", "light"),
    ("grow_light_grow", "light"),
)
# Q-09: the controller's grow-light switches, beside the Lutron readback.
GROW_LIGHT_INTENTS: Mapping[str, str] = {
    "grow_light_main": "grow_light_main_intent",
    "grow_light_grow": "grow_light_grow_intent",
}
# Running bursts, from the controller's readback (entity_map EQUIPMENT_BINARY_MAP).
BURSTS: Mapping[str, Literal["fans", "humidify", "vent_bypass"]] = {
    "fan_burst_active": "fans",
    "fog_burst_active": "humidify",
    "vent_bypass_active": "vent_bypass",
}
LEAK_FLAG = "leak_detected"

_EQUIPMENT_KEYS = tuple(name for name, _ in EQUIPMENT_LIST) + tuple(GROW_LIGHT_INTENTS.values())
_NOW_EQUIPMENT_KEYS = _EQUIPMENT_KEYS + tuple(BURSTS) + (LEAK_FLAG,)
MODE_ENTITY = "greenhouse_state"
_NOW_NEWEST_ENTITIES = ("mister_state", "mode_reason", "fog_block_reason")

# writer.differing judges the values the dispatcher delivers: the writable
# classes it pushes from desired rows and plans. A plan value it never sends is
# left out: the band, the legacy shared lighting values, the per-circuit lux
# rows (band-driven) and the values the dispatcher derives itself.
_DISPATCHED_CLASSES = frozenset({"planner_policy", "scheduled_policy", "controller_safety"})
_LIGHTING_LUX_PARAMS = frozenset(
    name for name in LIGHTING_CIRCUIT_DEFAULT_REG if name.endswith(("_lux_threshold", "_lux_hysteresis"))
)
_POLICY_WIRE_FIELDS = frozenset(spec.name for spec in wire_fields())
_IRRIGATION_COMMAND_COLUMNS = ("kind", "args", "status", "sent_at")


# ── Response models (the committed OpenAPI schemas) ─────────────────────────


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Reading(_Model):
    value: float | None
    observed_at: datetime | None
    reason: str | None = None


class Freshness(_Model):
    observed_at: datetime | None
    age_s: int | None
    cadence_s: int
    stale: bool


class BandPosition(_Model):
    low: float | None
    high: float | None
    verdict: Literal["in", "above", "below", "unavailable"]


class Indoor(_Model):
    temp_f: Reading
    rh_pct: Reading
    vpd_kpa: Reading


class Outdoor(_Model):
    temp_f: Reading
    rh_pct: Reading


class Band(_Model):
    temp_f: BandPosition
    vpd_kpa: BandPosition
    rh_pct: BandPosition | None
    observed_at: datetime | None


class Mode(_Model):
    code: str
    stage: str | None
    reason: str | None
    since: datetime | None


RequiredTrigger = Literal["ok", "late", "missed", "failed"]


class Iris(_Model):
    plan_id: str | None
    trigger: str | None
    valid_until: datetime | None
    required_trigger: RequiredTrigger


class AlertCounts(_Model):
    open: int
    critical: int
    warning: int
    info: int


IrrigationCause = Literal["vpd", "schedule", "run_now", "feed", "prewet", "flush", "fallback", "unknown"]
LINE_ID_PATTERN = r"^L(0[1-9]|1[01])$"


class Running(_Model):
    equipment: str
    line_id: str | None = Field(pattern=LINE_ID_PATTERN)
    cause: IrrigationCause | None
    since: datetime | None


class Override(_Model):
    kind: Literal["fans", "humidify", "vent_bypass"]
    since: datetime
    ends_at: datetime | None
    held_reason: str | None


class Leak(_Model):
    active: bool | None
    since: datetime | None
    flow_gpm: float | None
    gal_last_10min: int | None
    alert_id: int | None
    stopped_at: datetime | None


class NowFreshness(_Model):
    climate: Freshness
    diagnostics: Freshness
    readback: Freshness
    ingestor: Freshness


class Writer(_Model):
    last_at: datetime | None
    age_s: int | None
    stuck: bool
    differing: int
    oldest_since: datetime | None


class Controller(_Model):
    firmware_version: str | None
    uptime_s: int | None
    reset_reason: str | None
    heap_free_kb: float | None
    heap_largest_block_kb: float | None
    rssi_dbm: float | None
    observed_at: datetime | None


class Now(_Model):
    server_time: datetime
    indoor: Indoor
    outdoor: Outdoor
    band: Band
    mode: Mode
    iris: Iris
    alerts: AlertCounts
    running: list[Running]
    overrides: list[Override]
    leak: Leak
    freshness: NowFreshness
    writer: Writer
    controller: Controller


TriggerOutcome = Literal[
    "expected",
    "delivered",
    "acked",
    "plan_written",
    "action_completed",
    "neutral_fallback",
    "wrong_action",
    "delivery_failed",
    "timed_out",
    "missed",
]


class PlanRecord(_Model):
    plan_id: str
    trigger: str
    written_at: datetime
    valid_from: datetime | None
    valid_until: datetime | None
    status: str | None
    hypothesis: str | None
    experiment: str | None
    expected_outcome: str | None
    conditions_summary: str | None
    outcome_score: float | None
    lesson: str | None
    detail: dict[str, Any]


class PlannerTrigger(_Model):
    trigger: str
    required: bool
    expected_at: datetime
    due_at: datetime
    outcome: TriggerOutcome
    resolved_at: datetime | None
    failure_class: str | None


class PlanWaypoint(_Model):
    at: datetime
    value: float | None
    source: Literal["iris", "preemptive"]
    effective: bool


class ActorRef(_Model):
    source: str
    name: str


ReadbackState = Literal["requested", "confirmed", "not_confirmed", "held", "no_readback", "overridden", "released"]


class StateReason(_Model):
    code: Literal[
        "clamped",
        "experiment",
        "deferred",
        "push_failed",
        "writer_down",
        "controller_not_reporting",
        "no_engine",
        "no_match",
    ]
    detail: str | None = None
    applied_value: float | None = None
    since: datetime | None = None


class DesiredSetting(_Model):
    id: int
    param: str
    value: float | None
    snapped_from: float | None = None
    manual: bool
    manual_before: bool | None = None
    state: ReadbackState
    state_reason: StateReason | None = None
    confirmed_at: datetime | None
    actor: ActorRef
    reason: str | None
    created_at: datetime


AppliedFrom = Literal["manual", "desired", "plan", "band", "default"]


class PlanLane(_Model):
    param: str
    label: str
    unit: str | None
    device: Reading
    waypoints: list[PlanWaypoint]
    desired: DesiredSetting | None
    applied_from: AppliedFrom


class Plan(_Model):
    plan: PlanRecord | None
    no_plan_since: datetime | None
    lanes: list[PlanLane]
    manual_count: int
    health: list[PlannerTrigger]


class EquipmentItem(_Model):
    equipment: str
    label: str
    group: EquipmentGroup
    line_id: str | None = Field(pattern=LINE_ID_PATTERN)
    state: Literal["on", "off", "unknown"]
    since: datetime | None
    runtime_today_s: int | None
    cycles_today: int | None
    intent: Literal["on", "off", "unknown"] | None
    observed_at: datetime | None


class Equipment(_Model):
    items: list[EquipmentItem]


def _hours_from_query(value: object) -> object:
    return int(value) if isinstance(value, str) and value.isdigit() else value


# API-07's ``hours``: 24 (default) or 72, as the query string carries it.
PlanHours = Annotated[Literal[24, 72], BeforeValidator(_hours_from_query)]


# ── Pure rules ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Reporting:
    """The one §7 test: is the ingestor alive, and is the controller reporting?"""

    heartbeat_at: datetime | None
    climate_at: datetime | None
    ingestor_fresh: bool
    controller_reporting: bool
    at: datetime


@dataclass(frozen=True)
class Span:
    """One run of equal states, oldest first; ``end`` is None for the newest run."""

    state: Any
    start: datetime
    end: datetime | None


@dataclass(frozen=True)
class Track:
    """One key's runs in the state window and the time of its newest row."""

    spans: tuple[Span, ...]
    newest_at: datetime | None


def _finite(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _seconds(earlier: datetime, at: datetime) -> float:
    return (at - earlier).total_seconds()


def _age_s(observed_at: datetime | None, at: datetime) -> int | None:
    return None if observed_at is None else max(0, int(_seconds(observed_at, at)))


def local_midnight(at: datetime) -> datetime:
    """America/Denver midnight of ``at``'s local day."""
    return datetime.combine(at.astimezone(DENVER).date(), time(0), tzinfo=DENVER)


def reporting_from(heartbeat_at: datetime | None, climate_at: datetime | None, at: datetime) -> Reporting:
    """Heartbeat at most 180 s old; the controller reports when, also, climate is at most 300 s old."""
    ingestor_fresh = heartbeat_at is not None and _seconds(heartbeat_at, at) <= HEARTBEAT_STALE_S
    controller_reporting = ingestor_fresh and climate_at is not None and _seconds(climate_at, at) <= CLIMATE_REPORTING_S
    return Reporting(heartbeat_at, climate_at, ingestor_fresh, controller_reporting, at)


def collapse(rows: Iterable[tuple[datetime, Any]]) -> list[Span]:
    """Merge consecutive equal states into runs (reconnect snapshots, Home Assistant polls).

    ``rows`` are (ts, state) pairs in any order; equal timestamps keep their
    input order. The result is oldest first, each run ending where the next
    begins and the newest run open.
    """
    spans: list[Span] = []
    for ts, state in sorted(rows, key=lambda row: row[0]):
        if spans and spans[-1].state == state:
            continue
        if spans:
            spans[-1] = replace(spans[-1], end=ts)
        spans.append(Span(state=state, start=ts, end=None))
    return spans


def run_since(spans: Sequence[Span]) -> datetime | None:
    """The start of the newest run, or None when it is the window's first run (it may be older)."""
    return spans[-1].start if len(spans) > 1 else None


def summarize(spans: Sequence[Span], at: datetime) -> tuple[datetime | None, int, int]:
    """``since``, runtime today (s) and cycles today of on/off runs, as of ``at``.

    Runtime sums the ON time clipped to [America/Denver midnight, at). Cycles
    count the OFF->ON edges at or after midnight; the window's first run has no
    observed edge.
    """
    midnight = local_midnight(at)
    runtime = 0.0
    cycles = 0
    for index, span in enumerate(spans):
        if not span.state:
            continue
        start = max(span.start, midnight)
        end = min(span.end if span.end is not None else at, at)
        if end > start:
            runtime += (end - start).total_seconds()
        if index and not spans[index - 1].state and span.start >= midnight:
            cycles += 1
    return run_since(spans), int(round(runtime)), cycles


def freshness(observed_at: datetime | None, at: datetime, *, cadence_s: int, stale_after_s: int) -> Freshness:
    """A source's age; stale past ``stale_after_s`` or never seen."""
    if observed_at is None:
        return Freshness(observed_at=None, age_s=None, cadence_s=cadence_s, stale=True)
    return Freshness(
        observed_at=observed_at,
        age_s=_age_s(observed_at, at),
        cadence_s=cadence_s,
        stale=_seconds(observed_at, at) > stale_after_s,
    )


def band_position(
    reading: Reading, low: float | None, high: float | None, at: datetime, *, reporting: bool
) -> BandPosition:
    """in, above, below; unavailable without a band, a fresh reading or a reporting controller."""
    value, observed_at = reading.value, reading.observed_at
    if (
        not reporting
        or low is None
        or high is None
        or value is None
        or observed_at is None
        or _seconds(observed_at, at) > SENSOR_STALE_S
    ):
        verdict: Literal["in", "above", "below", "unavailable"] = "unavailable"
    elif value > high:
        verdict = "above"
    elif value < low:
        verdict = "below"
    else:
        verdict = "in"
    return BandPosition(low=low, high=high, verdict=verdict)


def required_trigger_state(status: str | None, due_at: datetime | None, at: datetime) -> RequiredTrigger:
    """Map the newest required trigger's ledger status (migration 218: only plan_written recovers it).

    ``status`` None means no required trigger fired in the last 36 h, which
    reads ``missed``: the planner has no required plan to show.
    """
    if status == "plan_written":
        return "ok"
    if status in ("expected", "delivered"):
        return "ok" if due_at is None or at < due_at else "late"
    if status == "missed" or status is None:
        return "missed"
    # timed_out, delivery_failed, wrong_action, neutral_fallback, and acked or
    # action_completed: Iris answered a required trigger without a plan.
    return "failed"


def applied_from(param: str, desired: Mapping[str, Any] | None, plan_row: Mapping[str, Any] | None) -> AppliedFrom:
    """Whose value the dispatcher applies now (architecture §5.2; #1180's settings.status uses the same order)."""
    if desired is not None and desired["value"] is not None and desired["manual"]:
        return "manual"
    resolved = resolve_effective(desired, plan_row)
    if resolved is not None:
        return resolved.applied_from
    if plan_row is not None:
        return "plan"
    spec = registry_get(param)
    if spec is not None and spec.push_owner in ("band", "schedule"):
        return "band"
    return "default"


@dataclass(frozen=True)
class ReadbackFacts:
    """Readbacks of one value since a time: the newest, and the first and last that match it."""

    newest_value: float | None
    newest_at: datetime | None
    first_match_at: datetime | None
    last_match_at: datetime | None
    clamped: bool


def desired_state(
    desired: Mapping[str, Any],
    plan_row: Mapping[str, Any] | None,
    facts: ReadbackFacts | None,
    at: datetime,
) -> tuple[ReadbackState, datetime | None]:
    """The interim readback state of a desired row and when it was confirmed.

    released, overridden (not manual and a newer plan wins), confirmed (the
    newest readback since the write matches, by ``readback_matches``),
    requested (under 5 min, nothing matched yet) or not_confirmed. #1168
    switches this to ``settings.status``.
    """
    if desired["value"] is None:
        return "released", None
    resolved = resolve_effective(desired, plan_row)
    if resolved is not None and resolved.applied_from == "plan":
        return "overridden", None
    if facts is not None and readback_matches(desired["param"], facts.newest_value, desired["value"]):
        return "confirmed", facts.first_match_at
    matched_before = facts is not None and facts.first_match_at is not None
    if not matched_before and at - desired["created_at"] < REQUESTED_FOR:
        return "requested", None
    return "not_confirmed", None


@dataclass(frozen=True)
class InForce:
    """A setting's value in force and when it took force."""

    param: str
    value: float
    since: datetime


def _plan_took_force(plan_row: Mapping[str, Any]) -> datetime:
    return max(plan_row["ts"], plan_row["created_at"])


def _dispatched(param: str, source: str) -> bool:
    spec = registry_get(param)
    if spec is None or spec.control_class not in _DISPATCHED_CLASSES:
        return False
    if source == "plan" and (
        param in BAND_OWNED_REG or param in _LIGHTING_LUX_PARAMS or spec.push_owner == "dispatcher_default"
    ):
        return False
    return True


def values_in_force(
    desired: Mapping[str, Mapping[str, Any]],
    active_plan: Mapping[str, Mapping[str, Any]],
    *,
    experiment_armed: bool,
) -> list[InForce]:
    """The values writer.differing judges: ``resolve_effective()``, else the ``v_active_plan`` row.

    A value comes from the desired row it resolves to, or from the plan row
    (when it took force: its waypoint time, or its write if later). The 48
    policy fields are left out while an experiment is armed: the firmware
    serves the armed vector on purpose.
    """
    out: list[InForce] = []
    for param in sorted(set(desired) | set(active_plan)):
        if experiment_armed and param in _POLICY_WIRE_FIELDS:
            continue
        desired_row, plan_row = desired.get(param), active_plan.get(param)
        resolved = resolve_effective(desired_row, plan_row)
        if resolved is not None and resolved.applied_from == "desired" and desired_row is not None:
            value, since, source = resolved.value, desired_row["created_at"], "desired"
        elif plan_row is not None:
            value, since, source = _finite(plan_row["value"]), _plan_took_force(plan_row), "plan"
        else:
            continue
        if value is None or not _dispatched(param, source):
            continue
        out.append(InForce(param=param, value=value, since=since))
    return out


def count_differing(
    values: Iterable[InForce], facts: Mapping[str, ReadbackFacts], at: datetime
) -> tuple[int, datetime | None]:
    """How many values have differed from the controller for more than 10 min, and since when.

    A value differs while its newest readback since it took force does not
    match (``readback_matches``; none at all counts as not matching). It has
    differed since its last matching readback, or since it took force when
    none matched. A value the dispatcher clamped after it took force (a
    ``setpoint_clamps`` row) is held on purpose and left out.
    """
    count = 0
    oldest: datetime | None = None
    for item in values:
        fact = facts.get(item.param)
        if fact is not None and fact.clamped:
            continue
        if fact is not None and readback_matches(item.param, fact.newest_value, item.value):
            continue
        since = item.since
        if fact is not None and fact.last_match_at is not None:
            since = max(since, fact.last_match_at)
        if at - since > DIFFERING_AFTER:
            count += 1
            oldest = since if oldest is None else min(oldest, since)
    return count, oldest


def _tolerance(param: str, value: float) -> float:
    try:
        return readback_tolerance(param, value)
    except ValueError:  # a value binary32 cannot hold never matches
        return -1.0


def _json_object(value: object) -> Any:
    if isinstance(value, str | bytes):
        return json.loads(value)
    return value


# ── SQL (bounded; parameters only, never text from a request) ───────────────

_REPORTING_SQL = """
SELECT now() AS at,
       (SELECT max(ts) FROM system_state WHERE entity = $1 AND ts > now() - interval '1 day') AS heartbeat_at,
       (SELECT max(ts) FROM climate WHERE ts > now() - interval '1 day') AS climate_at
"""

_CLOCK_SQL = """
SELECT now() AS at,
       (SELECT max(ts) FROM system_state WHERE entity = $1 AND ts > now() - interval '1 day') AS heartbeat_at,
       (SELECT max(ts) FROM climate WHERE ts > now() - interval '1 day') AS climate_at,
       (SELECT max(ts) FROM diagnostics WHERE ts > now() - interval '1 day') AS diagnostics_at,
       (SELECT max(ts) FROM setpoint_snapshot
         WHERE ts > now() - interval '1 day' AND band_role IS NULL) AS readback_at,
       (SELECT ts FROM setpoint_changes ORDER BY ts DESC LIMIT 1) AS writer_at,
       (SELECT round(sum(delta_gal))::int FROM water_meter_events
         WHERE ts > now() - interval '10 minutes' AND event_type = 'delta' AND quality_flag = 'ok') AS gal_10min,
       EXISTS (SELECT 1 FROM fn_mcp_armed_assignment_context()) AS experiment_armed
"""

_CLIMATE_SQL = """
SELECT c.ts, c.temp_avg, c.rh_avg, c.vpd_avg,
       t.ts AS outdoor_temp_at, t.outdoor_temp_f, h.ts AS outdoor_rh_at, h.outdoor_rh_pct, f.flow_gpm
  FROM (SELECT 1) AS anchor
  LEFT JOIN LATERAL (SELECT ts, temp_avg, rh_avg, vpd_avg FROM climate
                      WHERE ts > now() - interval '24 hours' ORDER BY ts DESC LIMIT 1) AS c ON true
  LEFT JOIN LATERAL (SELECT ts, outdoor_temp_f FROM climate
                      WHERE ts > now() - interval '1 hour' AND outdoor_temp_f IS NOT NULL
                      ORDER BY ts DESC LIMIT 1) AS t ON true
  LEFT JOIN LATERAL (SELECT ts, outdoor_rh_pct FROM climate
                      WHERE ts > now() - interval '1 hour' AND outdoor_rh_pct IS NOT NULL
                      ORDER BY ts DESC LIMIT 1) AS h ON true
  LEFT JOIN LATERAL (SELECT flow_gpm FROM climate
                      WHERE ts > now() - interval '10 minutes' AND flow_gpm IS NOT NULL
                      ORDER BY ts DESC LIMIT 1) AS f ON true
"""

_DIAGNOSTICS_SQL = """
SELECT ts, firmware_version, uptime_s, reset_reason, heap_bytes, heap_largest_free_block_kb, wifi_rssi
  FROM diagnostics
 WHERE ts > now() - interval '1 day'
 ORDER BY ts DESC
 LIMIT 1
"""

# The dispatcher's band rows for the house (band_anchors.emit_band_audit): one
# row per role per pass, parameter band_house_<role>. The plain temp_low,
# temp_high, vpd_low and vpd_high rows are legacy wide limits, never the band.
_BAND_SQL = """
SELECT r.role, s.value, s.ts
  FROM unnest($1::text[], $2::text[]) AS r(role, parameter)
  LEFT JOIN LATERAL (SELECT value, ts FROM setpoint_snapshot
                      WHERE parameter = r.parameter AND zone = 'house' AND band_role = r.role
                        AND ts > now() - interval '1 day'
                      ORDER BY ts DESC LIMIT 1) AS s ON true
"""

_EQUIPMENT_TRACKS_SQL = """
SELECT equipment AS key, ts, state, newest_at
  FROM (SELECT equipment, ts, state,
               lag(state) OVER (PARTITION BY equipment ORDER BY ts, state) AS previous,
               max(ts) OVER (PARTITION BY equipment) AS newest_at
          FROM equipment_state
         WHERE equipment = ANY($1::text[]) AND ts > now() - $2::interval) AS r
 WHERE previous IS DISTINCT FROM state
 ORDER BY equipment, ts, state
"""

_SYSTEM_TRACKS_SQL = """
SELECT entity AS key, ts, value AS state, newest_at
  FROM (SELECT entity, ts, value,
               lag(value) OVER (PARTITION BY entity ORDER BY ts, value) AS previous,
               max(ts) OVER (PARTITION BY entity) AS newest_at
          FROM system_state
         WHERE entity = ANY($1::text[]) AND ts > now() - $2::interval) AS r
 WHERE previous IS DISTINCT FROM value
 ORDER BY entity, ts, value
"""

# The newest value of controller text entities that need no since (mode_reason
# changes thousands of times a month).
_NEWEST_STATES_SQL = """
SELECT e.entity, s.value, s.ts
  FROM unnest($1::text[]) AS e(entity)
  LEFT JOIN LATERAL (SELECT value, ts FROM system_state
                      WHERE entity = e.entity AND ts > now() - $2::interval
                      ORDER BY ts DESC LIMIT 1) AS s ON true
"""

_PLAN_IN_FORCE_SQL = """
SELECT pj.plan_id, pj.created_at, pj.valid_from, pj.expires_at, pj.lifecycle_status, pj.hypothesis,
       pj.experiment, pj.expected_outcome, pj.conditions_summary, pj.outcome_score, pj.lesson_extracted,
       pj.hypothesis_structured, pj.climate_intents, t.event_type AS trigger
  FROM plan_journal AS pj
  LEFT JOIN LATERAL (SELECT l.event_type FROM planner_trigger_ledger AS l
                      WHERE l.trigger_id = pj.trigger_id
                      ORDER BY l.expected_at DESC LIMIT 1) AS t ON true
 WHERE pj.lifecycle_status = 'effective' AND pj.valid_from <= now() AND pj.expires_at > now()
 ORDER BY pj.created_at DESC
 LIMIT 1
"""

_NO_PLAN_SINCE_SQL = """
SELECT max(expires_at) FROM plan_journal
 WHERE lifecycle_status IN ('effective', 'expired') AND expires_at <= now()
"""

# The newest required trigger that has fired. The ledger also holds the day's
# expected rows ahead, which say nothing yet about Iris.
_REQUIRED_TRIGGER_SQL = """
SELECT status, due_at FROM planner_trigger_ledger
 WHERE event_type = ANY($1::text[]) AND expected_at >= now() - interval '36 hours' AND expected_at <= now()
 ORDER BY expected_at DESC
 LIMIT 1
"""

_HEALTH_SQL = """
SELECT event_type, expected_at, due_at, status, resolved_at, failure_class
  FROM planner_trigger_ledger
 WHERE expected_at >= now() - interval '36 hours'
 ORDER BY expected_at, id
"""

# One open-alert count (Round 2b C): repeats of one alert_type and zone count
# once, under their newest row's severity, while any of them is unacknowledged.
# #1168 replaces it with verdify_services.alerts.open_count (#1180).
_ALERTS_SQL = """
WITH grouped AS (
    SELECT alert_type, zone,
           bool_or(disposition = 'open') AS unacknowledged,
           (array_agg(severity ORDER BY ts DESC, id DESC))[1] AS severity
      FROM v_open_alerts
     GROUP BY alert_type, zone
)
SELECT count(*) FILTER (WHERE unacknowledged)::int AS open,
       count(*) FILTER (WHERE unacknowledged AND severity = 'critical')::int AS critical,
       count(*) FILTER (WHERE unacknowledged AND severity = 'warning')::int AS warning,
       count(*) FILTER (WHERE unacknowledged AND severity = 'info')::int AS info,
       (SELECT id FROM v_open_alerts WHERE alert_type = 'leak_detected' ORDER BY ts, id LIMIT 1) AS leak_alert_id,
       (SELECT max(ts) FROM v_open_alerts WHERE alert_type = 'leak_detected') AS leak_open_since,
       EXISTS (SELECT 1 FROM v_open_alerts WHERE alert_type = 'setpoint_delivery_stuck') AS stuck
  FROM grouped
"""

# The trailing quiet run of the site meter since the open leak row (as the
# alert monitor reads it): only the first readings after the last flowing one.
_LEAK_READINGS_SQL = """
SELECT ts, flow_gpm FROM climate
 WHERE ts >= $1 AND flow_gpm IS NOT NULL
   AND ts > coalesce((SELECT max(ts) FROM climate WHERE ts >= $1 AND flow_gpm >= $2), '-infinity')
 ORDER BY ts
 LIMIT $3
"""

_DESIRED_SQL = """
SELECT id, param, value, manual, source, actor, reason, created_at
  FROM v_desired_state_current
 WHERE greenhouse_id = $1 AND ($2::text[] IS NULL OR param = ANY($2::text[]))
"""

_ACTIVE_PLAN_SQL = "SELECT parameter, value, ts, plan_id, created_at FROM v_active_plan"

# Readbacks of each value since it took force. The last match is the newest
# readback when that matches, and is only searched for when it does not; the
# first match (a desired row's confirmed_at) only when $5 asks for it.
_READBACK_FACTS_SQL = """
SELECT c.param, newest.value AS newest_value, newest.ts AS newest_at,
       CASE WHEN $5::boolean THEN
           (SELECT s.ts FROM setpoint_snapshot AS s
             WHERE s.parameter = c.param AND s.band_role IS NULL AND s.ts >= c.since
               AND abs(s.value - c.value) <= c.tol
             ORDER BY s.ts LIMIT 1)
       END AS first_match_at,
       CASE WHEN abs(newest.value - c.value) <= c.tol THEN newest.ts ELSE
           (SELECT s.ts FROM setpoint_snapshot AS s
             WHERE s.parameter = c.param AND s.band_role IS NULL AND s.ts >= c.since
               AND abs(s.value - c.value) <= c.tol
             ORDER BY s.ts DESC LIMIT 1)
       END AS last_match_at,
       EXISTS (SELECT 1 FROM setpoint_clamps AS k WHERE k.parameter = c.param AND k.ts >= c.since) AS clamped
  FROM unnest($1::text[], $2::timestamptz[], $3::float8[], $4::float8[]) AS c(param, since, value, tol)
  LEFT JOIN LATERAL (SELECT s.value, s.ts FROM setpoint_snapshot AS s
                      WHERE s.parameter = c.param AND s.band_role IS NULL AND s.ts >= c.since
                      ORDER BY s.ts DESC LIMIT 1) AS newest ON true
"""

_DEVICE_READBACKS_SQL = """
SELECT p.param, s.value, s.ts
  FROM unnest($1::text[]) AS p(param)
  LEFT JOIN LATERAL (SELECT value, ts FROM setpoint_snapshot
                      WHERE parameter = p.param AND band_role IS NULL AND ts > now() - interval '24 hours'
                      ORDER BY ts DESC LIMIT 1) AS s ON true
"""

# The waypoints that can apply: the plan in force's rows, active Iris one-shots
# and active forecast-rule rows (source preemptive).
_PLAN_WAYPOINTS_SQL = """
SELECT sp.parameter, sp.ts, sp.value, sp.source, sp.created_at
  FROM setpoint_plan AS sp
 WHERE sp.ts >= $1 AND sp.ts < $2
   AND ($3::text[] IS NULL OR sp.parameter = ANY($3::text[]))
   AND sp.is_active AND sp.expires_at > now()
   AND ((sp.source = 'iris' AND (sp.plan_id LIKE 'iris-oneshot-%' OR sp.plan_id = (
            SELECT pj.plan_id FROM plan_journal AS pj
             WHERE pj.lifecycle_status = 'effective' AND pj.valid_from <= now() AND pj.expires_at > now()
             ORDER BY pj.created_at DESC LIMIT 1)))
        OR sp.source = 'preemptive')
 ORDER BY sp.parameter, sp.ts, sp.created_at
"""

# irrigation_command arrives with M3 (#1153); until then a burst has no end time.
_IRRIGATION_COMMAND_READY_SQL = """
SELECT coalesce(has_table_privilege(c.oid, 'SELECT'), false)
       AND (SELECT count(*) FROM pg_attribute AS a
             WHERE a.attrelid = c.oid AND a.attname = ANY($1::text[]) AND NOT a.attisdropped) = $2
  FROM (SELECT to_regclass('irrigation_command')::oid AS oid) AS c
"""

_OVERRIDE_MINUTES_SQL = """
SELECT CASE WHEN jsonb_typeof(args -> 'minutes') = 'number' THEN (args ->> 'minutes')::float8 END
  FROM irrigation_command
 WHERE kind = 'override' AND status = 'sent' AND args ->> 'kind' = $1
   AND sent_at >= $2::timestamptz - $3::interval AND sent_at <= $2::timestamptz
 ORDER BY sent_at DESC
 LIMIT 1
"""


# ── Database helpers ────────────────────────────────────────────────────────


@asynccontextmanager
async def _snapshot(conn: asyncpg.Connection) -> AsyncIterator[None]:
    """One read-only snapshot for a whole read, so every ``now()`` in it agrees."""
    if conn.is_in_transaction():
        yield
        return
    async with conn.transaction(isolation="repeatable_read", readonly=True):
        yield


async def _tracks(conn: asyncpg.Connection, sql: str, keys: Sequence[str]) -> dict[str, Track]:
    """Each key's runs over the state window, from the lag() edges of ``sql``."""
    rows = await conn.fetch(sql, list(keys), STATE_WINDOW)
    edges: dict[str, list[tuple[datetime, Any]]] = {}
    newest: dict[str, datetime] = {}
    for row in rows:
        edges.setdefault(row["key"], []).append((row["ts"], row["state"]))
        newest[row["key"]] = row["newest_at"]
    return {key: Track(tuple(collapse(edges.get(key, ()))), newest.get(key)) for key in keys}


async def reporting(conn: asyncpg.Connection) -> Reporting:
    """The one §7 test: heartbeat at most 180 s old, and climate at most 300 s old for the controller."""
    row = await conn.fetchrow(_REPORTING_SQL, HEARTBEAT_ENTITY)
    return reporting_from(row["heartbeat_at"], row["climate_at"], row["at"])


async def _desired_rows(conn: asyncpg.Connection, params: Sequence[str] | None = None) -> dict[str, asyncpg.Record]:
    rows = await conn.fetch(_DESIRED_SQL, GREENHOUSE_ID, list(params) if params is not None else None)
    return {row["param"]: row for row in rows}


async def _active_plan_rows(conn: asyncpg.Connection) -> dict[str, asyncpg.Record]:
    return {row["parameter"]: row for row in await conn.fetch(_ACTIVE_PLAN_SQL)}


async def _readback_facts(
    conn: asyncpg.Connection, items: Sequence[tuple[str, datetime, float]], *, first_match: bool = False
) -> dict[str, ReadbackFacts]:
    """Readback facts of each (param, since, value), matched with the readback comparator's tolerance."""
    if not items:
        return {}
    rows = await conn.fetch(
        _READBACK_FACTS_SQL,
        [param for param, _, _ in items],
        [since for _, since, _ in items],
        [value for _, _, value in items],
        [_tolerance(param, value) for param, _, value in items],
        first_match,
    )
    return {
        row["param"]: ReadbackFacts(
            newest_value=_finite(row["newest_value"]),
            newest_at=row["newest_at"],
            first_match_at=row["first_match_at"],
            last_match_at=row["last_match_at"],
            clamped=row["clamped"],
        )
        for row in rows
    }


async def plan_waypoints(
    conn: asyncpg.Connection,
    params: Sequence[str] | None,
    from_: datetime,
    to: datetime,
    *,
    desired: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, list[PlanWaypoint]]:
    """Plan values with ``from_ <= ts < to``, by param (every param when ``params`` is None).

    The rows of the plan in force, active Iris one-shots and active
    forecast-rule rows. ``effective`` is true when ``resolve_effective()``
    picks the plan row over the param's newest desired row: false under a
    manual row, or a non-manual row newer than the plan row's ``created_at``.
    ``desired`` (the ``v_desired_state_current`` rows by param) is read when
    not given.
    """
    rows = await conn.fetch(_PLAN_WAYPOINTS_SQL, from_, to, list(params) if params is not None else None)
    if desired is None:
        desired = await _desired_rows(conn, sorted({row["parameter"] for row in rows}))
    out: dict[str, list[PlanWaypoint]] = {}
    for row in rows:
        resolved = resolve_effective(desired.get(row["parameter"]), row)
        out.setdefault(row["parameter"], []).append(
            PlanWaypoint(
                at=row["ts"],
                value=_finite(row["value"]),
                source=row["source"],
                effective=resolved is None or resolved.applied_from == "plan",
            )
        )
    return out


def _reading(value: object, observed_at: datetime | None, missing: str) -> Reading:
    number = _finite(value)
    return Reading(value=number, observed_at=observed_at, reason=None if number is not None else missing)


def _plan_record(row: Mapping[str, Any]) -> PlanRecord:
    return PlanRecord(
        plan_id=row["plan_id"],
        trigger=row["trigger"] or "UNKNOWN",
        written_at=row["created_at"],
        valid_from=row["valid_from"],
        valid_until=row["expires_at"],
        status=row["lifecycle_status"],
        hypothesis=row["hypothesis"],
        experiment=row["experiment"],
        expected_outcome=row["expected_outcome"],
        conditions_summary=row["conditions_summary"],
        outcome_score=_finite(row["outcome_score"]),
        lesson=row["lesson_extracted"],
        detail={
            "hypothesis_structured": _json_object(row["hypothesis_structured"]),
            "climate_intents": _json_object(row["climate_intents"]),
        },
    )


def _equipment_items(tracks: Mapping[str, Track], rep: Reporting) -> list[EquipmentItem]:
    """API-13's fixed list. A state is the newest row while the controller reports, else unknown.

    The grow lights are the Lutron readback instead, unknown once its newest
    row is older than two Home Assistant polls, whatever the controller does.
    Their ``intent`` is the controller's switch: unknown while it is not
    reporting, and null before any intent row exists. An unknown state has no
    since, runtime or cycles: the open run's length is not known.
    """
    items: list[EquipmentItem] = []
    for name, group in EQUIPMENT_LIST:
        track = tracks.get(name) or Track((), None)
        if group == "light":
            known = track.newest_at is not None and _seconds(track.newest_at, rep.at) <= LUTRON_UNKNOWN_S
        else:
            known = rep.controller_reporting
        state: Literal["on", "off", "unknown"] = "unknown"
        since: datetime | None = None
        runtime: int | None = None
        cycles: int | None = None
        if known and track.spans:
            state = "on" if track.spans[-1].state else "off"
            since, runtime, cycles = summarize(track.spans, rep.at)
        intent: Literal["on", "off", "unknown"] | None = None
        intent_track = tracks.get(GROW_LIGHT_INTENTS[name]) if name in GROW_LIGHT_INTENTS else None
        if intent_track is not None and intent_track.spans:
            if not rep.controller_reporting:
                intent = "unknown"
            else:
                intent = "on" if intent_track.spans[-1].state else "off"
        items.append(
            EquipmentItem(
                equipment=name,
                label=EQUIPMENT_LABELS[name],
                group=group,
                line_id=EQUIPMENT_LINE_IDS.get(name),
                state=state,
                since=since,
                runtime_today_s=runtime,
                cycles_today=cycles,
                intent=intent,
                observed_at=track.newest_at,
            )
        )
    return items


def _newest_text(track: Track | None) -> str | None:
    if track is None or not track.spans:
        return None
    value = track.spans[-1].state
    return str(value) if value is not None else None


async def _overrides(
    conn: asyncpg.Connection, tracks: Mapping[str, Track], fog_block_reason: str | None, rep: Reporting
) -> list[Override]:
    """Running bursts from the controller's readback, while it reports, never from a command alone."""
    if not rep.controller_reporting:
        return []
    out: list[Override] = []
    commands_ready: bool | None = None
    for flag, kind in BURSTS.items():
        spans = tracks[flag].spans
        if not spans or not spans[-1].state:
            continue
        since = spans[-1].start
        ends_at: datetime | None = None
        if commands_ready is None:
            commands_ready = bool(
                await conn.fetchval(
                    _IRRIGATION_COMMAND_READY_SQL, list(_IRRIGATION_COMMAND_COLUMNS), len(_IRRIGATION_COMMAND_COLUMNS)
                )
            )
        if commands_ready:
            minutes = _finite(await conn.fetchval(_OVERRIDE_MINUTES_SQL, kind, since, OVERRIDE_COMMAND_LEAD))
            if minutes is not None and minutes > 0:
                ends_at = since + timedelta(minutes=minutes)
        held_reason = None
        if kind == "humidify":
            fog = tracks.get("fog")
            fog_on = bool(fog is not None and fog.spans and fog.spans[-1].state)
            if fog_block_reason not in (None, "", "none") and not fog_on:
                held_reason = fog_block_reason
        out.append(Override(kind=kind, since=since, ends_at=ends_at, held_reason=held_reason))
    return out


async def _leak(
    conn: asyncpg.Connection,
    track: Track,
    alerts_row: Mapping[str, Any],
    flow_gpm: float | None,
    gal_10min: int | None,
    rep: Reporting,
) -> Leak:
    """The leak block: an alert only (Q-05), never an all clear while the alert is open.

    ``stopped_at`` is ``flow_stopped_at()`` over the meter since the newest
    open leak row, read once the flag is off, as the alert monitor reads it.
    """
    flag_on = bool(track.spans and track.spans[-1].state)
    active = flag_on if rep.controller_reporting and track.spans else None
    alert_id = alerts_row["leak_alert_id"]
    open_since = alerts_row["leak_open_since"]
    stopped_at = None
    if open_since is not None and not flag_on:
        rows = await conn.fetch(_LEAK_READINGS_SQL, open_since, LEAK_STOPPED_BELOW_GPM, LEAK_STOPPED_READINGS)
        stopped_at = flow_stopped_at(
            [(row["ts"], row["flow_gpm"]) for row in rows],
            below_gpm=LEAK_STOPPED_BELOW_GPM,
            needed=LEAK_STOPPED_READINGS,
        )
    since = None
    if active or alert_id is not None:
        on_runs = [index for index, span in enumerate(track.spans) if span.state]
        if on_runs and on_runs[-1] > 0:
            since = track.spans[on_runs[-1]].start
    gal = (gal_10min or 0) if rep.controller_reporting else None
    return Leak(
        active=active,
        since=since,
        flow_gpm=flow_gpm,
        gal_last_10min=gal,
        alert_id=alert_id,
        stopped_at=stopped_at,
    )


# ── API-02 ──────────────────────────────────────────────────────────────────


async def now(conn: asyncpg.Connection) -> Now:
    """API-02 ``GET /v1/now``: is everything OK, in one bounded read."""
    async with _snapshot(conn):
        clock = await conn.fetchrow(_CLOCK_SQL, HEARTBEAT_ENTITY)
        at: datetime = clock["at"]
        rep = reporting_from(clock["heartbeat_at"], clock["climate_at"], at)
        climate = await conn.fetchrow(_CLIMATE_SQL)
        diagnostics = await conn.fetchrow(_DIAGNOSTICS_SQL)
        band_rows = await conn.fetch(
            _BAND_SQL, list(HOUSE_BAND_ROLES), [f"band_house_{role}" for role in HOUSE_BAND_ROLES]
        )
        mode_track = (await _tracks(conn, _SYSTEM_TRACKS_SQL, (MODE_ENTITY,)))[MODE_ENTITY]
        texts = {
            row["entity"]: row["value"]
            for row in await conn.fetch(_NEWEST_STATES_SQL, list(_NOW_NEWEST_ENTITIES), STATE_WINDOW)
        }
        tracks = await _tracks(conn, _EQUIPMENT_TRACKS_SQL, _NOW_EQUIPMENT_KEYS)
        plan_row = await conn.fetchrow(_PLAN_IN_FORCE_SQL)
        required = await conn.fetchrow(_REQUIRED_TRIGGER_SQL, list(REQUIRED_TRIGGERS))
        alerts_row = await conn.fetchrow(_ALERTS_SQL)
        flow_gpm = _finite(climate["flow_gpm"])
        leak = await _leak(conn, tracks[LEAK_FLAG], alerts_row, flow_gpm, clock["gal_10min"], rep)
        overrides = await _overrides(conn, tracks, texts.get("fog_block_reason"), rep)
        values = values_in_force(
            await _desired_rows(conn), await _active_plan_rows(conn), experiment_armed=clock["experiment_armed"]
        )
        due = [item for item in values if at - item.since > DIFFERING_AFTER]
        facts = await _readback_facts(conn, [(item.param, item.since, item.value) for item in due])

    indoor_at = climate["ts"]
    no_row = "No climate row in the last 24 h"
    indoor = Indoor(
        temp_f=_reading(climate["temp_avg"], indoor_at, no_row if indoor_at is None else "No indoor temperature"),
        rh_pct=_reading(climate["rh_avg"], indoor_at, no_row if indoor_at is None else "No indoor RH"),
        vpd_kpa=_reading(climate["vpd_avg"], indoor_at, no_row if indoor_at is None else "No indoor VPD"),
    )
    outdoor = Outdoor(
        temp_f=_reading(
            climate["outdoor_temp_f"], climate["outdoor_temp_at"], "No outdoor temperature in the last hour"
        ),
        rh_pct=_reading(climate["outdoor_rh_pct"], climate["outdoor_rh_at"], "No outdoor RH in the last hour"),
    )

    bands = {row["role"]: row for row in band_rows}
    band_times = [row["ts"] for row in band_rows if row["ts"] is not None]

    def role(name: str) -> float | None:
        row = bands.get(name)
        return _finite(row["value"]) if row is not None else None

    band = Band(
        temp_f=band_position(
            indoor.temp_f, role("temp_low"), role("temp_high"), at, reporting=rep.controller_reporting
        ),
        vpd_kpa=band_position(
            indoor.vpd_kpa, role("vpd_low"), role("vpd_high"), at, reporting=rep.controller_reporting
        ),
        rh_pct=None,
        observed_at=min(band_times) if band_times else None,
    )

    code = _newest_text(mode_track) or "UNKNOWN"
    mode = Mode(
        code=code,
        stage=texts.get("mister_state") if code == "SEALED_MIST" else None,
        reason=texts.get("mode_reason"),
        since=run_since(mode_track.spans),
    )

    iris = Iris(
        plan_id=plan_row["plan_id"] if plan_row else None,
        trigger=plan_row["trigger"] if plan_row else None,
        valid_until=plan_row["expires_at"] if plan_row else None,
        required_trigger=required_trigger_state(
            required["status"] if required else None, required["due_at"] if required else None, at
        ),
    )

    items = _equipment_items(tracks, rep)
    running = [
        Running(equipment=item.equipment, line_id=item.line_id, cause=None, since=item.since)
        for item in items
        if rep.controller_reporting and item.state == "on"
    ]

    differing, oldest_since = count_differing(due, facts, at)
    writer = Writer(
        last_at=clock["writer_at"],
        age_s=_age_s(clock["writer_at"], at),
        stuck=alerts_row["stuck"],
        differing=differing,
        oldest_since=oldest_since,
    )

    controller = Controller(
        firmware_version=diagnostics["firmware_version"] if diagnostics else None,
        uptime_s=int(uptime) if diagnostics and (uptime := _finite(diagnostics["uptime_s"])) is not None else None,
        reset_reason=diagnostics["reset_reason"] if diagnostics else None,
        heap_free_kb=_finite(diagnostics["heap_bytes"]) if diagnostics else None,
        heap_largest_block_kb=_finite(diagnostics["heap_largest_free_block_kb"]) if diagnostics else None,
        rssi_dbm=_finite(diagnostics["wifi_rssi"]) if diagnostics else None,
        observed_at=diagnostics["ts"] if diagnostics else None,
    )

    return Now(
        server_time=at,
        indoor=indoor,
        outdoor=outdoor,
        band=band,
        mode=mode,
        iris=iris,
        alerts=AlertCounts(
            open=alerts_row["open"],
            critical=alerts_row["critical"],
            warning=alerts_row["warning"],
            info=alerts_row["info"],
        ),
        running=running,
        overrides=overrides,
        leak=leak,
        freshness=NowFreshness(
            climate=freshness(clock["climate_at"], at, cadence_s=SENSOR_CADENCE_S, stale_after_s=SENSOR_STALE_S),
            diagnostics=freshness(
                clock["diagnostics_at"], at, cadence_s=SENSOR_CADENCE_S, stale_after_s=SENSOR_STALE_S
            ),
            readback=freshness(clock["readback_at"], at, cadence_s=SENSOR_CADENCE_S, stale_after_s=SENSOR_STALE_S),
            ingestor=freshness(
                clock["heartbeat_at"], at, cadence_s=HEARTBEAT_CADENCE_S, stale_after_s=HEARTBEAT_STALE_S
            ),
        ),
        writer=writer,
        controller=controller,
    )


# ── API-07 ──────────────────────────────────────────────────────────────────


def _desired_setting(
    row: Mapping[str, Any], plan_row: Mapping[str, Any] | None, facts: ReadbackFacts | None, at: datetime
) -> DesiredSetting:
    state, confirmed_at = desired_state(row, plan_row, facts, at)
    return DesiredSetting(
        id=row["id"],
        param=row["param"],
        value=_finite(row["value"]),
        manual=row["manual"],
        state=state,
        confirmed_at=confirmed_at,
        actor=ActorRef(source=row["source"], name=row["actor"]),
        reason=row["reason"],
        created_at=row["created_at"],
    )


async def plan(conn: asyncpg.Connection, hours: int = 24) -> Plan:
    """API-07 ``GET /v1/plan``: the plan in force, one lane per planned setting, and planner health."""
    if hours not in (24, 72):
        raise ValueError("hours must be 24 or 72")
    async with _snapshot(conn):
        at: datetime = await conn.fetchval("SELECT now()")
        plan_row = await conn.fetchrow(_PLAN_IN_FORCE_SQL)
        no_plan_since = None if plan_row is not None else await conn.fetchval(_NO_PLAN_SINCE_SQL)
        active = await _active_plan_rows(conn)
        desired = await _desired_rows(conn)
        waypoints = await plan_waypoints(conn, None, at, at + timedelta(hours=hours), desired=desired)
        manual = {param for param, row in desired.items() if row["manual"] and row["value"] is not None}
        params = sorted(set(active) | set(waypoints) | manual)
        device = {row["param"]: row for row in await conn.fetch(_DEVICE_READBACKS_SQL, params)}
        lane_desired = {param: desired[param] for param in params if param in desired}
        facts = await _readback_facts(
            conn,
            [
                (param, row["created_at"], row["value"])
                for param, row in lane_desired.items()
                if row["value"] is not None
            ],
            first_match=True,
        )
        health_rows = await conn.fetch(_HEALTH_SQL)

    lanes: list[PlanLane] = []
    for param in params:
        spec = registry_get(param)
        desired_row = lane_desired.get(param)
        plan_value = active.get(param)
        source = applied_from(param, desired_row, plan_value)
        readback = device.get(param)
        lanes.append(
            PlanLane(
                param=param,
                label=(spec.label if spec is not None and spec.label else param),
                unit=spec.unit if spec is not None else None,
                device=_reading(
                    readback["value"] if readback is not None else None,
                    readback["ts"] if readback is not None else None,
                    "No readback in the last 24 h",
                ),
                waypoints=[] if source == "manual" else waypoints.get(param, []),
                desired=(
                    _desired_setting(desired_row, plan_value, facts.get(param), at) if desired_row is not None else None
                ),
                applied_from=source,
            )
        )
    return Plan(
        plan=_plan_record(plan_row) if plan_row is not None else None,
        no_plan_since=no_plan_since,
        lanes=lanes,
        manual_count=sum(1 for lane in lanes if lane.applied_from == "manual"),
        health=[
            PlannerTrigger(
                trigger=row["event_type"],
                required=row["event_type"] in REQUIRED_TRIGGERS,
                expected_at=row["expected_at"],
                due_at=row["due_at"],
                outcome=row["status"],
                resolved_at=row["resolved_at"],
                failure_class=row["failure_class"],
            )
            for row in health_rows
        ],
    )


# ── API-13 ──────────────────────────────────────────────────────────────────


async def equipment(conn: asyncpg.Connection) -> Equipment:
    """API-13 ``GET /v1/equipment``: what runs and since when; unknown is never off."""
    async with _snapshot(conn):
        rep = await reporting(conn)
        tracks = await _tracks(conn, _EQUIPMENT_TRACKS_SQL, _EQUIPMENT_KEYS)
    return Equipment(items=_equipment_items(tracks, rep))
