"""The operator reads: Now, plan and equipment (VD-54), settings, history and activity (VD-55), and water (VD-57).

API-02, API-07 and API-13 (VD-54); API-09, API-12 and API-17 (VD-55), whose
composition over ``verdify_services.settings`` and ``.activity`` lives here:
``settings_rows()``, ``setting_history()`` and ``activity_page()``, which the
MCP ``settings`` and ``activity`` tools call too (#1171). API-14 is
``alerts.list`` itself. API-21 is ``water_daily()``.

Architecture §7 (read model) and §8 (API surface). Each read is ``async``,
takes an asyncpg connection, needs no ``Actor`` and returns a Pydantic model
shaped like its committed OpenAPI schema
(docs/design/dashboard/openapi/dashboard-api.v1.yaml). A read runs all of its
statements in one read-only snapshot, so every age in a response is measured
from one database ``now()``. Every hypertable query is bounded by ``ts``; the
one exception is the writer age, the newest ``setpoint_changes`` row, which the
``ts`` index answers from the newest chunk. API-02 is polled every 15 s and the
dashboard login has a 10 s ``statement_timeout``.

Helpers other reads share: ``reporting()``, the one §7 test (#1155 and #1180
use it too); ``collapse()``, which merges repeated state rows into runs;
``equipment_tracks()`` and ``leak_block()``, which API-IRR-01 (#1155) reuses; and
``plan_waypoints()`` and ``plan_history()``, the plan values with their
``effective`` flag. API-02's alert count is ``alerts.open_count``, the one
count API-14 and every other surface show (Round 2b C), and API-07's lanes
take their state from ``settings.statuses``, so Plan and Settings agree.

Unknown is never off, null is never 0, and gaps stay gaps. Nothing here writes,
and the leak is an alert only (Q-05).
"""

from __future__ import annotations

import json
import math
from bisect import bisect_right
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

import asyncpg
from pydantic import AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field

from verdify_schemas.alerts import flow_stopped_at
from verdify_schemas.band_rows import HOUSE_SERIES, ZONES
from verdify_schemas.irrigation import purpose_of
from verdify_schemas.policy_vector import wire_fields
from verdify_schemas.setting_help import band_in_force_help, band_in_force_sources, setting_label
from verdify_schemas.setting_values import readback_matches, readback_tolerance, resolve_effective
from verdify_schemas.tunable_registry import BAND_OWNED_REG, LIGHTING_CIRCUIT_DEFAULT_REG, desired_write_rule
from verdify_schemas.tunable_registry import get as registry_get
from verdify_services import activity, alerts, brief, settings
from verdify_services.actor import ActorRef as ActorRef  # re-exported: the OpenAPI Actor of DesiredSetting
from verdify_services.irrigation.runs import run_causes
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
# A key with no row in the state window takes its newest row from this longer
# bound: a state that has not changed or been re-snapshotted in 31 days is still
# known, with since null.
NEWEST_ROW_WINDOW = timedelta(days=90)
DIFFERING_AFTER = timedelta(minutes=10)
OVERRIDE_COMMAND_LEAD = timedelta(minutes=2)
# run_causes() looks this far back for what is on now; a run already on keeps its true start.
RUNNING_CAUSE_LOOKBACK = timedelta(minutes=1)
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
# API-13's reasons (entity_map's controller text entities): which branch chose the mode, what holds fog (L11) off and
# what holds the misters off.
REASON_ENTITIES = ("mode_reason", "fog_block_reason", "moisture_block_reason")
# In SEALED_MIST the firmware publishes greenhouse_state as SEALED_MIST_<stage>
# (controls.yaml, MIST_NAMES in greenhouse_types.h), so the label carries the stage.
SEALED_MIST = "SEALED_MIST"
MIST_STAGES = ("WATCH", "FOG", "S1", "S2")
# fog_block_reason values that hold nothing: none, and served (the fog relay is on).
_FOG_NOT_HELD = frozenset({"", "none", "served"})

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

# API-09's Band in force rows: the dispatcher's band_<zone>_<role> setpoint_snapshot
# rows (band_anchors.emit_band_audit, every pass), house then each zone, six roles.
BAND_ZONES = ("house", *ZONES)
BAND_ROLES = HOUSE_SERIES
BAND_IN_FORCE_WINDOW = timedelta(hours=1)
BAND_IN_FORCE_REASON = "derived by the dispatcher from the crop band anchors"
NO_BAND_ROW = "No band row in the last hour"
# Setting.plan.next: the first waypoint after now. A plan row expires 78 h after it
# is written (setpoint_plan.expires_at), so none applies past this horizon.
NEXT_WAYPOINT_HORIZON = timedelta(days=4)
PLAN_SOURCES = ("iris", "preemptive")
SETTING_WRITE_ACTIONS = frozenset({"setting.write", "setting.release"})


# ── Response models (the committed OpenAPI schemas) ─────────────────────────


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# One model per OpenAPI schema: the services that own these define them.
AlertCounts = alerts.AlertCounts
PlanWaypoint = settings.PlanWaypoint
StateReason = settings.StateReason
DesiredSetting = settings.DesiredSetting
ReadbackState = activity.ReadbackState
AppliedFrom = settings.AppliedFrom
Setting = settings.Setting
SettingHistory = settings.SettingHistory
ActivityPage = activity.ActivityPage


class SettingRows(_Model):
    """API-09 ``GET /v1/settings``: every registry row and the Band in force rows, with the read's time."""

    items: list[Setting]
    server_time: AwareDatetime


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
    # #1346: the greenhouse snapshot, the newest written one with its freshness; None when there is none or it is
    # older than 2 h (verdify_services.brief).
    snapshot: brief.Snapshot | None = None


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
    # Grow lights only (#1377's timer): when an API-20 manual switch goes back to automatic; null with no timer running.
    manual_until: datetime | None = None
    # Grow lights only (#1335): the controller's newest reason for its intent (gl_main_reason, gl_grow_reason), such as
    # plant_lux_low or outside_window; null while it is not reporting or before any reason row.
    auto_reason: str | None = None


class ReasonState(_Model):
    code: str | None
    since: datetime | None
    observed_at: datetime | None


class Equipment(_Model):
    items: list[EquipmentItem]
    mode_reason: ReasonState
    fog_block_reason: ReasonState
    moisture_block_reason: ReasonState


class WaterDay(_Model):
    date: date
    coverage: Literal["complete", "partial", "no_data"]
    meter_gal: int | None
    by_line: dict[str, int] | None
    by_cause: dict[str, int] | None
    # #1348: irrigation (drips, feeds, scheduled and run-now misting) against climate (VPD and fallback misting, fog).
    by_purpose: dict[str, int] | None = None
    no_valve_gal: int | None


class WaterUse(_Model):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: date = Field(alias="from")
    to: date
    days: list[WaterDay]


def _int_from_query(value: object) -> object:
    return int(value) if isinstance(value, str) and value.isdigit() else value


# API-07's ``hours``: 24 (default) or 72, as the query string carries it.
PlanHours = Annotated[Literal[24, 72], BeforeValidator(_int_from_query)]
# API-21's ``days``: 30 (default) or 90.
WaterDays = Annotated[Literal[30, 90], BeforeValidator(_int_from_query)]


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


def mode_code_and_stage(label: str | None, mister_state: str | None) -> tuple[str, str | None]:
    """Now's mode code and stage from the newest ``greenhouse_state`` label.

    The firmware publishes SEALED_MIST as ``SEALED_MIST_<stage>`` (WATCH, FOG,
    S1 or S2), so the code is SEALED_MIST and the label gives the stage.
    ``mister_state`` is the mister pulse machine and never reads FOG, so it
    gives the stage only for a bare SEALED_MIST label. No row reads UNKNOWN.
    """
    if label is None:
        return "UNKNOWN", None
    prefix = f"{SEALED_MIST}_"
    if label.startswith(prefix) and label.removeprefix(prefix) in MIST_STAGES:
        return SEALED_MIST, label.removeprefix(prefix)
    if label == SEALED_MIST:
        return SEALED_MIST, mister_state if mister_state in MIST_STAGES else None
    return label, None


def humidify_held_reason(fog_block_reason: str | None, fog_on: bool) -> str | None:
    """Why the controller holds a humidify burst: fog (L11) is off and ``fog_block_reason`` names a block."""
    if fog_on or fog_block_reason is None or fog_block_reason in _FOG_NOT_HELD:
        return None
    return fog_block_reason


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


def waypoint_effective(param: str, desired: Any | None, plan_row: Any, *, superseded: bool = False) -> bool:
    """Whether a plan value applies at its time (architecture §5.2), the ``effective`` of every ``PlanWaypoint``.

    ``desired`` is the setting's desired row in force then (a mapping or a ``DesiredSetting``) and ``plan_row``
    the plan row (``value``, ``created_at``). False when ``superseded`` (a newer full Iris plan was written by
    then) or when ``resolve_effective()`` picks the desired row: a manual row, or a non-manual row written after
    the plan. A desired row of a setting that is not writable now never counts: the dispatcher skips it.
    """
    if superseded:
        return False
    if desired is not None and not desired_write_rule(param)[0]:
        desired = None
    resolved = resolve_effective(desired, plan_row)
    return resolved is None or resolved.applied_from == "plan"


@dataclass(frozen=True)
class ReadbackFacts:
    """Readbacks of one value since a time: the newest, and the last that matches it."""

    newest_value: float | None
    newest_at: datetime | None
    last_match_at: datetime | None
    clamped: bool


def superseded_at(plan_row: Mapping[str, Any], full_plans: Sequence[tuple[datetime, str]]) -> bool:
    """Whether a full Iris plan's row (not ``iris-oneshot-*``) no longer applied at its own time.

    ``full_plans`` is the (``created_at``, ``plan_id``) of every full plan, ascending: a newer one written at or
    before the row's ``ts`` replaced the row's plan, since ``v_active_plan`` reads only the effective plan. The
    row's own plan never counts: before set_plan wrote both in one transaction (the last such plan is from
    2026-04), a plan's journal row can be up to 54 s newer than its waypoints. One-shots and forecast-rule rows
    are never superseded this way.
    """
    plan_id = plan_row["plan_id"]
    if plan_row["source"] != "iris" or str(plan_id).startswith("iris-oneshot-"):
        return False
    index = bisect_right(full_plans, plan_row["created_at"], key=lambda plan: plan[0])
    # plan_id is unique in plan_journal, so the first newer plan that is not the row's own is one of these two.
    return any(written <= plan_row["ts"] and other != plan_id for written, other in full_plans[index : index + 2])


def band_label(zone: str, role: str) -> str:
    """A Band in force row's name in words: ``House temp low``, ``West VPD high``."""
    axis, _, side = role.partition("_")
    return f"{zone.capitalize()} {'VPD' if axis == 'vpd' else axis} {side}"


def band_in_force_row(zone: str, role: str, value: object, observed_at: datetime | None) -> settings.Setting:
    """One read-only Band in force row (API-09 ``group`` ``band_in_force``): DERIVED by the dispatcher, no edit.

    It has no registry row, so its label and unit are set here: °F for the ``temp_*`` roles, kPa for ``vpd_*``.
    Its ``help`` is generated from its source (``band_in_force_help``) and ``source_params`` names the crop-band
    settings it is computed from, so the dashboard links each row to the anchors to edit (#1332, #1333).
    """
    number = _finite(value)
    readback = settings.Reading(
        value=number,
        observed_at=observed_at if number is not None else None,
        reason=None if number is not None else NO_BAND_ROW,
    )
    return settings.Setting(
        param=f"band_{zone}_{role}",
        label=band_label(zone, role),
        control_class="crop_band",
        group="band_in_force",
        kind="numeric",
        unit="°F" if role.startswith("temp") else "kPa",
        writable=False,
        read_only_reason=BAND_IN_FORCE_REASON,
        read_only_kind="band_in_force",
        source_params=list(band_in_force_sources(zone, role)),
        help=band_in_force_help(zone, role),
        readback_via="none",
        readback=readback,
        desired=None,
        plan=None,
        applied_from="band",
        state="no_readback",
    )


def setting_plan(plan_row: Mapping[str, Any], upcoming: Sequence[PlanWaypoint]) -> settings.SettingPlan | None:
    """API-09's ``plan`` block: the value in force as stored (Iris's 48.75 stays 48.75), its plan, its source and
    when it was written, and ``next``, the first waypoint after now with its ``effective``. None for a row whose
    source is not ``iris`` or ``preemptive``."""
    source = plan_row["source"]
    if source not in PLAN_SOURCES:
        return None
    return settings.SettingPlan(
        value=_finite(plan_row["value"]),
        plan_id=plan_row["plan_id"],
        source=source,
        written_at=plan_row["created_at"],
        next=upcoming[0] if upcoming else None,
    )


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

# Each grow light's newest switch row in the last day and, when it is timed (detail.minutes, API-20), when its manual
# time is up: the same rule as verdify_services.lights.timer, which the operator API's light timer sweeps by.
_LIGHT_TIMERS_SQL = """
SELECT t.target AS key, a.ts + make_interval(mins => (a.detail->>'minutes')::int) AS ends_at
  FROM unnest($2::text[]) AS t(target)
  LEFT JOIN LATERAL (SELECT ts, detail FROM activity_log
                      WHERE greenhouse_id = $1 AND target = t.target AND action IN ('light.on', 'light.off')
                        AND ts > now() - $3::interval
                      ORDER BY ts DESC, id DESC LIMIT 1) AS a ON true
"""
LIGHT_TIMER_WINDOW = timedelta(days=1)
# Each grow light's controller reason entity (lights.DECISION_ENTITIES), served as API-13 auto_reason (#1335). The
# controller decides every 60 s, so a day bounds the read to the newest chunk.
LIGHT_REASON_ENTITIES: Mapping[str, str] = {"grow_light_main": "gl_main_reason", "grow_light_grow": "gl_grow_reason"}
_LIGHT_REASONS_SQL = """
SELECT t.key, s.value AS reason
  FROM unnest($1::text[], $2::text[]) AS t(key, entity)
  LEFT JOIN LATERAL (SELECT value FROM system_state
                      WHERE entity = t.entity AND ts > now() - interval '1 day'
                      ORDER BY ts DESC LIMIT 1) AS s ON true
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

# The newest row of each key with none in the state window (NEWEST_ROW_WINDOW).
_EQUIPMENT_NEWEST_SQL = """
SELECT k.key, s.ts, s.state
  FROM unnest($1::text[]) AS k(key)
  JOIN LATERAL (SELECT ts, state FROM equipment_state
                 WHERE equipment = k.key AND ts > now() - $2::interval
                 ORDER BY ts DESC, state DESC LIMIT 1) AS s ON true
"""

_SYSTEM_NEWEST_SQL = """
SELECT k.key, s.ts, s.value AS state
  FROM unnest($1::text[]) AS k(key)
  JOIN LATERAL (SELECT ts, value FROM system_state
                 WHERE entity = k.key AND ts > now() - $2::interval
                 ORDER BY ts DESC, value DESC LIMIT 1) AS s ON true
"""

# The newest value of controller text entities that need no since (mode_reason
# changes thousands of times a month), bounded like a track's newest row.
_NEWEST_STATES_SQL = """
SELECT e.entity, s.value, s.ts
  FROM unnest($1::text[]) AS e(entity)
  LEFT JOIN LATERAL (SELECT value, ts FROM system_state
                      WHERE entity = e.entity AND ts > now() - $2::interval
                      ORDER BY ts DESC LIMIT 1) AS s ON true
"""

# API-21. Neither view has an inner time bound, so each statement filters by day and reads the whole ledger beneath
# it: about 2 s and 1.5 s for 90 days on production's 15 months of rows (2026-11-02), inside the 10 s limit.
_WATER_METER_SQL = """
SELECT (day AT TIME ZONE 'America/Denver')::date AS date, quality, round(used_gal::numeric)::integer AS gal
  FROM v_water_meter_daily
 WHERE greenhouse_id = $1 AND meter_id = 'main_pulse'
   AND day >= $2::date::timestamp AT TIME ZONE 'America/Denver'
   AND day < ($3::date + 1)::timestamp AT TIME ZONE 'America/Denver'
"""

_WATER_NO_VALVE_SQL = """
SELECT date, round(manual_or_unattributed_gal::numeric)::integer AS gal
  FROM v_water_attribution_daily
 WHERE greenhouse_id = $1 AND date >= $2::date AND date <= $3::date
"""

# M4's reader for v_irrigation_minutes_daily's rows, with its equipment_state scan bounded by the days.
_WATER_RUNS_SQL = """
SELECT local_day, line_id, cause, gal
  FROM fn_irrigation_minutes_daily($2::date, $3::date)
 WHERE greenhouse_id = $1
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

# The open leak rows and the stuck-delivery flag, from alert_log under v_open_alerts'
# own predicate: the rows alerts.open_count (the one count, Round 2b C) merges. The
# leak's one sensor keeps the oldest open row's id, its merged row's id in API-14.
_ALERT_FACTS_SQL = f"""
SELECT (SELECT id FROM alert_log WHERE ({alerts.OPEN_PREDICATE}) AND alert_type = 'leak_detected'
         ORDER BY ts, id LIMIT 1) AS leak_alert_id,
       (SELECT max(ts) FROM alert_log
         WHERE ({alerts.OPEN_PREDICATE}) AND alert_type = 'leak_detected') AS leak_open_since,
       EXISTS (SELECT 1 FROM alert_log
                WHERE ({alerts.OPEN_PREDICATE}) AND alert_type = 'setpoint_delivery_stuck') AS stuck
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
# readback when that matches, and is only searched for when it does not. (A
# desired row's confirmed_at is settings.status's, VD-55.)
_READBACK_FACTS_SQL = """
SELECT c.param, newest.value AS newest_value, newest.ts AS newest_at,
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

# API-12's plan values: every row of the setting in the window, past and superseded
# plans included (set_plan deactivates the older ones), one per ts (the newest write).
_PLAN_HISTORY_SQL = """
SELECT DISTINCT ON (sp.ts) sp.ts, sp.value, sp.source, sp.plan_id, sp.created_at
  FROM setpoint_plan AS sp
 WHERE sp.parameter = $1 AND sp.ts >= $2 AND sp.ts < $3 AND sp.source = ANY($4::text[])
 ORDER BY sp.ts, sp.created_at DESC
"""

# When each full Iris plan was written (plan_journal holds the full plans only).
_FULL_PLANS_SQL = """
SELECT created_at, plan_id FROM plan_journal
 WHERE plan_id NOT LIKE 'iris-oneshot-%' AND created_at > $1 AND created_at <= $2
 ORDER BY created_at
"""

# API-09's plan block: the v_active_plan row of each setting with its setpoint_plan
# source (iris, or preemptive for the forecast engine).
_SETTING_PLANS_SQL = """
SELECT a.parameter, a.value, a.ts, a.plan_id, a.created_at, s.source
  FROM v_active_plan AS a
  JOIN LATERAL (SELECT sp.source FROM setpoint_plan AS sp
                 WHERE sp.ts = a.ts AND sp.ts <= now() AND sp.parameter = a.parameter AND sp.plan_id = a.plan_id
                 LIMIT 1) AS s ON true
"""

# API-09's Band in force rows: each zone and role's newest band row in the window.
_BAND_IN_FORCE_SQL = """
SELECT r.zone, r.role, s.value, s.ts
  FROM unnest($1::text[], $2::text[], $3::text[]) AS r(zone, role, parameter)
  LEFT JOIN LATERAL (SELECT value, ts FROM setpoint_snapshot
                      WHERE parameter = r.parameter AND zone = r.zone AND band_role = r.role
                        AND ts > now() - $4::interval
                      ORDER BY ts DESC LIMIT 1) AS s ON true
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


_NEWEST_FOR_EDGES = {_EQUIPMENT_TRACKS_SQL: _EQUIPMENT_NEWEST_SQL, _SYSTEM_TRACKS_SQL: _SYSTEM_NEWEST_SQL}


async def _tracks(conn: asyncpg.Connection, sql: str, keys: Sequence[str]) -> dict[str, Track]:
    """Each key's runs over the state window, from the lag() edges of ``sql``.

    A key with no row in the window takes its newest older row as its one run,
    whose start is not known: the state is known and ``since`` is null.
    """
    rows = await conn.fetch(sql, list(keys), STATE_WINDOW)
    edges: dict[str, list[tuple[datetime, Any]]] = {}
    newest: dict[str, datetime] = {}
    for row in rows:
        edges.setdefault(row["key"], []).append((row["ts"], row["state"]))
        newest[row["key"]] = row["newest_at"]
    missing = [key for key in keys if key not in edges]
    if missing:
        for row in await conn.fetch(_NEWEST_FOR_EDGES[sql], missing, NEWEST_ROW_WINDOW):
            edges[row["key"]] = [(row["ts"], row["state"])]
            newest[row["key"]] = row["ts"]
    return {key: Track(tuple(collapse(edges.get(key, ()))), newest.get(key)) for key in keys}


async def equipment_tracks(conn: asyncpg.Connection, keys: Sequence[str]) -> dict[str, Track]:
    """Each ``equipment_state`` key's runs over the state window, with the key's newest row."""
    return await _tracks(conn, _EQUIPMENT_TRACKS_SQL, keys)


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
    conn: asyncpg.Connection, items: Sequence[tuple[str, datetime, float]]
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
    )
    return {
        row["param"]: ReadbackFacts(
            newest_value=_finite(row["newest_value"]),
            newest_at=row["newest_at"],
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
    forecast-rule rows. ``effective`` is ``waypoint_effective()`` against the
    param's newest desired row: false under a manual row, or a non-manual row
    newer than the plan row's ``created_at``. ``desired`` (the
    ``v_desired_state_current`` rows by param) is read when not given.
    """
    rows = await conn.fetch(_PLAN_WAYPOINTS_SQL, from_, to, list(params) if params is not None else None)
    if desired is None:
        desired = await _desired_rows(conn, sorted({row["parameter"] for row in rows}))
    out: dict[str, list[PlanWaypoint]] = {}
    for row in rows:
        out.setdefault(row["parameter"], []).append(
            PlanWaypoint(
                at=row["ts"],
                value=_finite(row["value"]),
                source=row["source"],
                effective=waypoint_effective(row["parameter"], desired.get(row["parameter"]), row),
            )
        )
    return out


async def plan_history(
    conn: asyncpg.Connection,
    param: str,
    from_: datetime,
    to: datetime,
    desired: Sequence[DesiredSetting],
) -> list[PlanWaypoint]:
    """API-12's plan values: ``plan_waypoints()`` extended to every ``setpoint_plan`` row of ``param`` with
    ``from_ <= ts < to``, past and superseded plans and future waypoints included, one per ``ts`` (the newest
    ``created_at``), oldest first.

    ``desired`` is the setting's desired rows from the one in force at ``from_`` (``settings.history`` gives
    them). ``effective`` is ``waypoint_effective()`` against the desired row in force at the waypoint, and false
    for a full Iris plan's row once a newer full plan was written at or before it (``superseded_at()``), so the
    Graphs steps mark SUPERSEDED. Only ``iris`` and ``preemptive`` (forecast rule) rows are plan values.
    """
    rows = await conn.fetch(_PLAN_HISTORY_SQL, param, from_, to, list(PLAN_SOURCES))
    if not rows:
        return []
    written_after = min(row["created_at"] for row in rows)
    newest_at = max(row["ts"] for row in rows)
    full_plans = [
        (row["created_at"], row["plan_id"]) for row in await conn.fetch(_FULL_PLANS_SQL, written_after, newest_at)
    ]
    ordered = sorted(desired, key=lambda row: (row.created_at, row.id))
    written = [row.created_at for row in ordered]
    out: list[PlanWaypoint] = []
    for row in rows:
        index = bisect_right(written, row["ts"])
        in_force = ordered[index - 1] if index else None
        out.append(
            PlanWaypoint(
                at=row["ts"],
                value=_finite(row["value"]),
                source=row["source"],
                effective=waypoint_effective(param, in_force, row, superseded=superseded_at(row, full_plans)),
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


def _equipment_items(
    tracks: Mapping[str, Track],
    rep: Reporting,
    timers: Mapping[str, datetime | None] | None = None,
    light_reasons: Mapping[str, str | None] | None = None,
) -> list[EquipmentItem]:
    """API-13's fixed list. A state is the newest row while the controller reports, else unknown.

    The grow lights are the Lutron readback instead, unknown once its newest
    row is older than two Home Assistant polls, whatever the controller does.
    Their ``intent`` is the controller's switch: unknown while it is not
    reporting, and null before any intent row exists. An unknown state has no
    since, runtime or cycles: the open run's length is not known. A light's
    ``manual_until`` is its timed switch's end (``timers``) while it is still
    ahead, else null; its ``auto_reason`` is the controller's newest reason
    (``light_reasons``) while the controller reports.
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
                manual_until=_manual_until((timers or {}).get(name), rep.at),
                auto_reason=(light_reasons or {}).get(name) if rep.controller_reporting else None,
            )
        )
    return items


def _manual_until(ends_at: datetime | None, at: datetime) -> datetime | None:
    """A timed switch's end while it is still ahead; once it is up the light timer sends automatic back."""
    return ends_at if ends_at is not None and ends_at > at else None


async def _light_reasons(conn: asyncpg.Connection) -> dict[str, str | None]:
    """Each grow light's newest controller reason (``_LIGHT_REASONS_SQL``), keyed by its equipment name."""
    rows = await conn.fetch(_LIGHT_REASONS_SQL, list(LIGHT_REASON_ENTITIES), list(LIGHT_REASON_ENTITIES.values()))
    return {row["key"]: row["reason"] for row in rows}


async def _light_timers(conn: asyncpg.Connection) -> dict[str, datetime | None]:
    """Each grow light's timed-switch end (``_LIGHT_TIMERS_SQL``), keyed by its equipment name."""
    rows = await conn.fetch(_LIGHT_TIMERS_SQL, GREENHOUSE_ID, list(GROW_LIGHT_INTENTS), LIGHT_TIMER_WINDOW)
    return {row["key"]: row["ends_at"] for row in rows}


def _newest_text(track: Track | None) -> str | None:
    if track is None or not track.spans:
        return None
    value = track.spans[-1].state
    return str(value) if value is not None else None


def reason_state(track: Track | None, rep: Reporting) -> ReasonState:
    """The newest row of one controller reason entity. ``code`` and ``since`` are null while the controller is not
    reporting or there is no row; ``observed_at`` keeps the newest row's time. ``since`` is null too when the value has
    not changed in the state window, because the run's start is not known."""
    code = _newest_text(track) if rep.controller_reporting else None
    return ReasonState(
        code=code,
        since=run_since(track.spans) if track is not None and code is not None else None,
        observed_at=track.newest_at if track is not None else None,
    )


def water_coverage(quality: str | None) -> Literal["complete", "partial", "no_data"]:
    """API-21's coverage from ``v_water_meter_daily.quality``: ``unavailable``, or no row, is no_data."""
    if quality is None or quality == "unavailable":
        return "no_data"
    return "complete" if quality == "ok" else "partial"


def water_days(
    first: date,
    last: date,
    meter: Mapping[date, Mapping[str, Any]],
    runs: Iterable[Mapping[str, Any]],
    no_valve: Mapping[date, int | None],
) -> list[WaterDay]:
    """API-21's days, oldest first, in whole gallons (pure). ``meter`` is keyed by day (``quality``, ``gal``), ``runs``
    are the minutes rows (``local_day``, ``line_id``, ``cause``, ``gal``) and ``no_valve`` the attribution view's
    gallons by day. A no_data day is null throughout, never 0, though the views coalesce to 0. A day with data and no
    run has empty splits. A run with no gallons (L03, or no meter data) adds nothing. Nothing is rescaled, so the parts
    can sit a gallon off the total."""
    by_line: dict[date, dict[str, int]] = {}
    by_cause: dict[date, dict[str, int]] = {}
    by_purpose: dict[date, dict[str, int]] = {}
    for run in runs:
        if run["gal"] is None:
            continue
        lines = by_line.setdefault(run["local_day"], {})
        lines[run["line_id"]] = lines.get(run["line_id"], 0) + run["gal"]
        causes = by_cause.setdefault(run["local_day"], {})
        causes[run["cause"]] = causes.get(run["cause"], 0) + run["gal"]
        purposes = by_purpose.setdefault(run["local_day"], {})
        purpose = purpose_of(run["line_id"], run["cause"]).value
        purposes[purpose] = purposes.get(purpose, 0) + run["gal"]
    days: list[WaterDay] = []
    for offset in range((last - first).days + 1):
        day = first + timedelta(days=offset)
        row = meter.get(day)
        coverage = water_coverage(row["quality"] if row is not None else None)
        if coverage == "no_data":
            days.append(
                WaterDay(
                    date=day,
                    coverage=coverage,
                    meter_gal=None,
                    by_line=None,
                    by_cause=None,
                    by_purpose=None,
                    no_valve_gal=None,
                )
            )
            continue
        days.append(
            WaterDay(
                date=day,
                coverage=coverage,
                meter_gal=row["gal"],
                by_line=by_line.get(day, {}),
                by_cause=by_cause.get(day, {}),
                by_purpose=by_purpose.get(day, {}),
                no_valve_gal=no_valve.get(day),
            )
        )
    return days


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
            held_reason = humidify_held_reason(
                fog_block_reason, bool(fog is not None and fog.spans and fog.spans[-1].state)
            )
        out.append(Override(kind=kind, since=since, ends_at=ends_at, held_reason=held_reason))
    return out


async def leak_block(
    conn: asyncpg.Connection,
    track: Track,
    alert_facts: Mapping[str, Any],
    flow_gpm: float | None,
    gal_10min: int | None,
    rep: Reporting,
) -> Leak:
    """The leak block: an alert only (Q-05), never an all clear while the alert is open.

    ``stopped_at`` is ``flow_stopped_at()`` over the meter since the newest
    open leak row, read once the flag is off, as the alert monitor reads it.
    The flag, the flow and the gallons are null while the controller is not
    reporting: an old 0 would read as no water.
    """
    flag_on = bool(track.spans and track.spans[-1].state)
    active = flag_on if rep.controller_reporting and track.spans else None
    alert_id = alert_facts["leak_alert_id"]
    open_since = alert_facts["leak_open_since"]
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
        flow_gpm=flow_gpm if rep.controller_reporting else None,
        gal_last_10min=gal,
        alert_id=alert_id,
        stopped_at=stopped_at,
    )


async def _running_causes(
    conn: asyncpg.Connection, tracks: Mapping[str, Track], rep: Reporting
) -> dict[str | None, IrrigationCause]:
    """Why each wet line on now is running: ``run_causes()``'s newest run per line (#1155)."""
    wet_on = any(tracks[name].spans and tracks[name].spans[-1].state for name in EQUIPMENT_LINE_IDS)
    if not rep.controller_reporting or not wet_on:
        return {}
    causes: dict[str | None, IrrigationCause] = {}
    for run in sorted(await run_causes(conn, rep.at - RUNNING_CAUSE_LOOKBACK, rep.at), key=lambda run: run.start):
        causes[run.line_id] = run.cause
    return causes


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
            for row in await conn.fetch(_NEWEST_STATES_SQL, list(_NOW_NEWEST_ENTITIES), NEWEST_ROW_WINDOW)
        }
        tracks = await _tracks(conn, _EQUIPMENT_TRACKS_SQL, _NOW_EQUIPMENT_KEYS)
        plan_row = await conn.fetchrow(_PLAN_IN_FORCE_SQL)
        required = await conn.fetchrow(_REQUIRED_TRIGGER_SQL, list(REQUIRED_TRIGGERS))
        alert_counts = await alerts.open_count(conn)
        alert_facts = await conn.fetchrow(_ALERT_FACTS_SQL)
        flow_gpm = _finite(climate["flow_gpm"])
        leak = await leak_block(conn, tracks[LEAK_FLAG], alert_facts, flow_gpm, clock["gal_10min"], rep)
        overrides = await _overrides(conn, tracks, texts.get("fog_block_reason"), rep)
        values = values_in_force(
            await _desired_rows(conn), await _active_plan_rows(conn), experiment_armed=clock["experiment_armed"]
        )
        due = [item for item in values if at - item.since > DIFFERING_AFTER]
        facts = await _readback_facts(conn, [(item.param, item.since, item.value) for item in due])
        causes = await _running_causes(conn, tracks, rep)
        newest_brief = await brief.newest(conn)

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

    code, stage = mode_code_and_stage(_newest_text(mode_track), texts.get("mister_state"))
    mode = Mode(code=code, stage=stage, reason=texts.get("mode_reason"), since=run_since(mode_track.spans))

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
        Running(equipment=item.equipment, line_id=item.line_id, cause=causes.get(item.line_id), since=item.since)
        for item in items
        if rep.controller_reporting and item.state == "on"
    ]

    differing, oldest_since = count_differing(due, facts, at)
    writer = Writer(
        last_at=clock["writer_at"],
        age_s=_age_s(clock["writer_at"], at),
        stuck=alert_facts["stuck"],
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
        alerts=alert_counts,
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
        snapshot=brief.snapshot(
            newest_brief,
            at,
            brief.event_key(band.temp_f.verdict, band.vpd_kpa.verdict, alert_counts.open, alert_counts.critical),
        ),
    )


# ── API-07 ──────────────────────────────────────────────────────────────────


async def plan(conn: asyncpg.Connection, hours: int = 24) -> Plan:
    """API-07 ``GET /v1/plan``: the plan in force, one lane per planned setting, and planner health.

    Each lane's ``applied_from`` and ``desired`` (with its state and reason) are ``settings.statuses``, so Plan
    and Settings show the same state. A lane for a setting outside ``REGISTRY`` has no desired row and reads
    ``plan`` while a plan value is in force, else ``default``.
    """
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
        statuses = await settings.statuses(conn, params)
        health_rows = await conn.fetch(_HEALTH_SQL)

    lanes: list[PlanLane] = []
    for param in params:
        spec = registry_get(param)
        status = statuses.get(param)
        if status is not None:
            source = status.applied_from
        else:
            source = "plan" if param in active else "default"
        readback = device.get(param)
        lanes.append(
            PlanLane(
                param=param,
                label=setting_label(param),
                unit=spec.unit if spec is not None else None,
                device=_reading(
                    readback["value"] if readback is not None else None,
                    readback["ts"] if readback is not None else None,
                    "No readback in the last 24 h",
                ),
                waypoints=[] if source == "manual" else waypoints.get(param, []),
                desired=status.desired if status is not None else None,
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
        reasons = await _tracks(conn, _SYSTEM_TRACKS_SQL, REASON_ENTITIES)
        timers = await _light_timers(conn)
        light_reasons = await _light_reasons(conn)
    return Equipment(
        items=_equipment_items(tracks, rep, timers, light_reasons),
        mode_reason=reason_state(reasons.get("mode_reason"), rep),
        fog_block_reason=reason_state(reasons.get("fog_block_reason"), rep),
        moisture_block_reason=reason_state(reasons.get("moisture_block_reason"), rep),
    )


# ── API-21 ──────────────────────────────────────────────────────────────────


async def water_daily(conn: asyncpg.Connection, days: int = 30) -> WaterUse:
    """API-21 ``GET /v1/water/daily``: the 30 or 90 America/Denver days ending today, today a partial day."""
    if days not in (30, 90):
        raise ValueError("days must be 30 or 90")
    async with _snapshot(conn):
        last: date = (await conn.fetchval("SELECT now()")).astimezone(DENVER).date()
        first = last - timedelta(days=days - 1)
        meter = await conn.fetch(_WATER_METER_SQL, GREENHOUSE_ID, first, last)
        no_valve = await conn.fetch(_WATER_NO_VALVE_SQL, GREENHOUSE_ID, first, last)
        runs = await conn.fetch(_WATER_RUNS_SQL, GREENHOUSE_ID, first, last)
    return WaterUse(
        from_=first,
        to=last,
        days=water_days(
            first,
            last,
            {row["date"]: row for row in meter},
            runs,
            {row["date"]: row["gal"] for row in no_valve},
        ),
    )


# ── API-09 ──────────────────────────────────────────────────────────────────


async def settings_rows(conn: asyncpg.Connection) -> SettingRows:
    """API-09 ``GET /v1/settings`` and the MCP ``settings`` tool: every setting, then the Band in force rows.

    The registry rows are ``settings.catalog`` (``REGISTRY`` order, each with ``TunableDef``'s label and unit,
    its readback, desired row, ``applied_from`` and state), each given its ``plan`` block while a
    ``v_active_plan`` value is in force (``setting_plan()``: the value as stored, unsnapped, its plan, source,
    when it was written and ``next``, the first ``plan_waypoints()`` waypoint after now with its ``effective``).
    Then one read-only row per zone (house, center, south, west, east) and band role from the dispatcher's newest
    band row in the last hour (``band_in_force_row()``). One snapshot; ``server_time`` is its ``now()``.
    """
    async with _snapshot(conn):
        at: datetime = await conn.fetchval("SELECT now()")
        rows = await settings.catalog(conn)
        plans = {row["parameter"]: row for row in await conn.fetch(_SETTING_PLANS_SQL)}
        upcoming = await plan_waypoints(conn, sorted(plans), at, at + NEXT_WAYPOINT_HORIZON) if plans else {}
        keys = [(zone, role) for zone in BAND_ZONES for role in BAND_ROLES]
        band = await conn.fetch(
            _BAND_IN_FORCE_SQL,
            [zone for zone, _ in keys],
            [role for _, role in keys],
            [f"band_{zone}_{role}" for zone, role in keys],
            BAND_IN_FORCE_WINDOW,
        )
    items: list[settings.Setting] = []
    for setting in rows:
        plan_row = plans.get(setting.param)
        if plan_row is not None:
            later = [waypoint for waypoint in upcoming.get(setting.param, []) if waypoint.at > at]
            setting = setting.model_copy(update={"plan": setting_plan(plan_row, later)})
        items.append(setting)
    newest = {(row["zone"], row["role"]): row for row in band}
    for zone, role in keys:
        found = newest.get((zone, role))
        value, observed_at = (found["value"], found["ts"]) if found is not None else (None, None)
        items.append(band_in_force_row(zone, role, value, observed_at))
    return SettingRows(items=items, server_time=at)


# ── API-12 ──────────────────────────────────────────────────────────────────


async def setting_history(conn: asyncpg.Connection, param: str, from_: datetime, to: datetime) -> SettingHistory:
    """API-12 ``GET /v1/settings/{param}/history`` and the MCP ``settings(param, since, until)`` tool.

    ``settings.history`` gives ``unit``, ``min`` and ``max`` (the registry's, as API-09), ``desired`` (oldest
    first, from the row in force at ``from_``) and ``readback`` (the change points; ``setpoint_snapshot`` keeps 90
    days), and refuses an unknown setting (404 ``not_found``) or a window that is not two times with an offset,
    ``from_`` first (400 ``invalid_request``). ``plan`` is ``plan_history()`` over the same window, so the Graphs
    device, requested and planned steps come from this one read. Every list is bounded by the window.
    """
    async with _snapshot(conn):
        history = await settings.history(conn, param, from_, to)
        plan_values = await plan_history(conn, param, history.from_, history.to, history.desired)
    return history.model_copy(update={"plan": plan_values})


# ── API-17 ──────────────────────────────────────────────────────────────────


def _desired_id(detail: Mapping[str, Any]) -> int | None:
    value = detail.get("desired_id")
    return value if type(value) is int else None


async def activity_page(
    conn: asyncpg.Connection,
    *,
    source: str | None = None,
    actor: str | None = None,
    action: str | None = None,
    target: str | None = None,
    from_: datetime | None = None,
    to: datetime | None = None,
    limit: int = activity.LIMIT_DEFAULT,
    cursor: str | None = None,
) -> ActivityPage:
    """API-17 ``GET /v1/activity`` and the MCP ``activity`` tool: ``activity.list`` with readback outcomes.

    Newest first by (``ts``, ``id``), keyset-paged: ``source`` is ``dashboard``, ``iris``, ``system``, ``agent``
    (every agent) or ``agent:<name>``; ``action`` is a prefix, ``actor`` and ``target`` are exact; ``from_`` is
    inclusive and ``to`` exclusive; ``limit`` is 1 to 200; ``cursor`` is the previous page's ``next_cursor``.
    A bad filter is 400 ``invalid_request``. Each ``setting.write`` and ``setting.release`` row gets ``state``
    and ``confirmed_at`` from ``settings.desired_states`` for the row its ``detail.desired_id`` names.
    """
    async with _snapshot(conn):
        page = await activity.list(
            conn,
            source=source,
            actor=actor,
            action=action,
            target=target,
            from_=from_,
            to=to,
            limit=limit,
            cursor=cursor,
        )
        named = {
            entry.id: desired_id
            for entry in page.items
            if entry.action in SETTING_WRITE_ACTIONS and (desired_id := _desired_id(entry.detail)) is not None
        }
        states = await settings.desired_states(conn, sorted(set(named.values()))) if named else {}
    items = []
    for entry in page.items:
        written = states.get(named[entry.id]) if entry.id in named else None
        if written is not None:
            entry = entry.model_copy(update={"state": written.state, "confirmed_at": written.confirmed_at})
        items.append(entry)
    return page.model_copy(update={"items": items})
