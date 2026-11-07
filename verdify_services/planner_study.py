"""The planner study's run (#1360): start, pause, resume and stop (API-28), the boundary runner, and its reads.

Jason, 2026-11-07: the AI-planner-versus-baseline study resumes with Hermes as the AI arm, run from the dashboard.
docs/design/dashboard/planner-study.md has the design; in short:

- **State is the log.** A run is its ``study.start``, ``study.pause``, ``study.resume`` and ``study.stop`` rows in
  ``activity_log`` (target the study id, ``detail.run_id``). ``place()`` turns them and the committed seed into the
  placed days, so nothing else is stored and no migration is needed. The boundary runner adds ``study.boundary``
  (one per study day: confirmed or not) and ``study.deviation`` rows.
- **Arms through desired state.** On a baseline day the runner holds each of the 48 ``PLANNER_PUSHABLE_REG`` fields
  manual at the committed baseline vector, as ``system:planner_study``; a manual value wins over any plan, so
  Hermes's plans for that day are stored and never sent. On an AI day, while paused or stopped, and after the last
  day, it releases every field whose newest desired row is still its own. It never overwrites a field someone else
  holds manual: that field is a deviation for the day. The dispatcher re-sends and re-asserts on its own.
- **Idempotent and restart-safe.** Every runner write carries a request id derived from the run, the day and the
  field, so a repeated sweep replays instead of writing twice. ``LOCK_KEY`` (a transaction advisory lock) keeps an
  API-28 write and a sweep from interleaving.

The pure parts (``place``, ``Run``, ``hold_plan``) take no connection and are what the tests pin.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

import asyncpg
from pydantic import BaseModel, ConfigDict

from verdify_schemas.activity import ActivityLogRow
from verdify_schemas.planner_study import (
    PlannerStudy,
    arm_order,
    baseline_sha256,
    cohort_of,
    load_baseline,
    load_study,
)
from verdify_schemas.setting_values import readback_values_equivalent
from verdify_schemas.tunable_registry import PLANNER_PUSHABLE_REG
from verdify_services import activity, settings
from verdify_services.actor import Actor
from verdify_services.problems import ServiceError, problem

DENVER = ZoneInfo("America/Denver")
RUNNER = Actor.system("planner_study")
# The advisory lock key API-28 and the runner share (a fixed int64 no other advisory lock uses).
LOCK_KEY = 0x2B5B3D3E6C1A7F01
NAMESPACE = UUID("6d2b0f6e-1360-4b8e-9f3a-5d1e2c7a1360")
CONTROL_ACTIONS = ("start", "pause", "resume", "stop")
# Readback states that mean the field holds what the arm asked for: confirmed, no readback route to check, or held at a
# safety fence's value (a fence is the same in both arms).
SETTLED = frozenset({"confirmed", "no_readback", "held"})
FIELDS = tuple(sorted(PLANNER_PUSHABLE_REG))

RunStatus = Literal["scheduled", "running", "paused", "finished", "stopped"]
DayStatus = Literal["scheduled", "active", "done", "void", "cancelled"]
Action = Literal["start", "pause", "resume", "stop"]


# ── Pure model ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Control:
    """One operator row: ``study.<action>`` at ``at``, its ``detail`` and who wrote it."""

    action: Action
    at: datetime
    detail: Mapping[str, Any]
    actor: str
    reason: str | None = None


@dataclass(frozen=True)
class PlacedDay:
    date: date
    pair_index: int
    position: int
    arm: str
    starts_at: datetime
    ends_at: datetime
    status: DayStatus
    reason: str | None = None


@dataclass(frozen=True)
class Run:
    run_id: UUID
    study_id: str
    status: RunStatus
    started_at: datetime
    started_by: str
    first_day: date
    pairs: int
    seed: int
    schedule_sha256: str
    baseline_sha256: str
    paused_at: datetime | None
    stopped_at: datetime | None
    stopped_by: str | None
    stop_reason: str | None
    days: tuple[PlacedDay, ...]
    pairs_done: int
    pairs_left: int

    def day_at(self, at: datetime) -> PlacedDay | None:
        """The placed, un-voided day in force at ``at`` while the run is running, else None."""
        if self.status != "running":
            return None
        return next(
            (day for day in self.days if day.status == "active" and day.starts_at <= at < day.ends_at),
            None,
        )

    def next_boundary(self, at: datetime) -> datetime | None:
        if self.status not in ("scheduled", "running"):
            return None
        return min((day.starts_at for day in self.days if day.starts_at > at), default=None)


def boundary(study: PlannerStudy, day: date) -> datetime:
    """Study day ``day`` starts at the boundary time on that local date."""
    return datetime.combine(day, study.schedule.boundary_local, tzinfo=DENVER).astimezone(UTC)


def first_day_after(study: PlannerStudy, at: datetime) -> date:
    """The first local date whose boundary is at or after ``at``."""
    local = at.astimezone(DENVER).date()
    return local if boundary(study, local) >= at else local + timedelta(days=1)


def place(study: PlannerStudy, controls: Sequence[Control], now: datetime) -> Run | None:
    """The newest run's placed days and status, from its controls (oldest first) and the committed seed.

    Pairs are placed back to back from the first day. A pause ends the running segment: a pair with a day already
    under way is void (both days, reason ``paused``) and runs again, in its own seeded order, in the next segment,
    which starts at the first boundary at or after the resume (never before the first day). A stop ends the run: a
    pair under way is void (``stopped``) and the days not yet run are dropped. After the last pair the run is
    finished. None when no run was ever started.
    """
    starts = [index for index, control in enumerate(controls) if control.action == "start"]
    if not starts:
        return None
    start = controls[starts[-1]]
    later = list(controls[starts[-1] + 1 :])
    detail = start.detail
    seed, pairs = int(detail["seed"]), int(detail["pairs"])
    order = arm_order(seed, pairs)  # the same function schedule_sha256 pins
    first_day = date.fromisoformat(str(detail["first_day"]))
    # Running segments: [begin, end) with the date the segment's first pair may start on.
    segments: list[tuple[date, datetime | None, str | None]] = []
    begin = first_day
    paused_at = stopped_at = None
    stopped_by = stop_reason = None
    status: RunStatus = "running"
    for control in later:
        if control.action == "pause" and status == "running":
            segments.append((begin, control.at, "paused"))
            status, paused_at = "paused", control.at
        elif control.action == "resume" and status == "paused":
            begin = max(first_day, first_day_after(study, control.at))
            status, paused_at = "running", None
        elif control.action == "stop" and status in ("running", "paused"):
            if status == "running":
                segments.append((begin, control.at, "stopped"))
            status, stopped_at, stopped_by, stop_reason = "stopped", control.at, control.actor, control.reason
            break
    if status == "running":
        segments.append((begin, None, None))

    days: list[PlacedDay] = []
    next_pair = 0
    for segment_begin, end, why in segments:
        day = segment_begin
        while next_pair < pairs:
            pair_start, pair_end = boundary(study, day), boundary(study, day + timedelta(days=2))
            if end is not None and pair_start >= end:
                break
            interrupted = end is not None and pair_end > end
            for position in (0, 1):
                on = day + timedelta(days=position)
                starts_at, ends_at = boundary(study, on), boundary(study, on + timedelta(days=1))
                if interrupted:
                    if end is not None and starts_at >= end:
                        continue  # the pair's second day never started
                    day_status: DayStatus = "void"
                elif ends_at <= now:
                    day_status = "done"
                elif starts_at <= now:
                    day_status = "active"
                else:
                    day_status = "scheduled"
                days.append(
                    PlacedDay(
                        date=on,
                        pair_index=next_pair,
                        position=position,
                        arm=order[next_pair][position],
                        starts_at=starts_at,
                        ends_at=ends_at,
                        status=day_status,
                        reason=why if interrupted else None,
                    )
                )
            if interrupted:
                break  # the pair runs again in the next segment
            next_pair += 1
            day += timedelta(days=2)
    pairs_done = len({day.pair_index for day in days if day.status == "done" and day.position == 1})
    if status == "running":
        if next_pair >= pairs and all(day.status in ("done", "void") for day in days):
            status = "finished"
        elif now < boundary(study, first_day):
            status = "scheduled"
    return Run(
        run_id=UUID(str(detail["run_id"])),
        study_id=study.study_id,
        status=status,
        started_at=start.at,
        started_by=start.actor,
        first_day=first_day,
        pairs=pairs,
        seed=seed,
        schedule_sha256=str(detail["schedule_sha256"]),
        baseline_sha256=str(detail["baseline_sha256"]),
        paused_at=paused_at,
        stopped_at=stopped_at,
        stopped_by=stopped_by,
        stop_reason=stop_reason,
        days=tuple(days),
        pairs_done=pairs_done,
        pairs_left=pairs - pairs_done,
    )


@dataclass(frozen=True)
class Desired:
    """A field's newest ``desired_state`` row (``v_desired_state_current``)."""

    id: int
    value: float | None
    manual: bool
    source: str
    actor: str
    created_at: datetime

    @property
    def ours(self) -> bool:
        return self.source == RUNNER.source and self.actor == RUNNER.name

    @property
    def foreign_hold(self) -> bool:
        """Someone else holds the field manual: the study never overwrites it."""
        return not self.ours and self.manual and self.value is not None


@dataclass
class HoldPlan:
    """What a sweep does to the 48 fields: hold these at their baseline values, release these, leave these alone
    (someone else holds them manual)."""

    hold: dict[str, float] = field(default_factory=dict)
    release: dict[str, int] = field(default_factory=dict)  # field -> the study's desired row id
    foreign: dict[str, str] = field(default_factory=dict)  # field -> who holds it


def hold_plan(arm: str | None, baseline: Mapping[str, float], current: Mapping[str, Desired]) -> HoldPlan:
    """The sweep's writes: ``arm`` baseline holds every field at ``baseline``; any other arm (``ai``, or None when no
    study day is in force) releases the study's own holds. A field someone else holds manual is left alone."""
    plan = HoldPlan()
    for name in FIELDS:
        row = current.get(name)
        if row is not None and row.foreign_hold:
            if arm == "baseline":
                plan.foreign[name] = f"{row.source}:{row.actor}"
            continue
        if arm == "baseline":
            target = baseline[name]
            held = (
                row is not None
                and row.ours
                and row.manual
                and row.value is not None
                and readback_values_equivalent(name, row.value, target)
            )
            if not held:
                plan.hold[name] = target
        elif row is not None and row.ours and row.value is not None:
            plan.release[name] = row.id
    return plan


def request_key(run_id: UUID, *parts: object) -> UUID:
    """A runner write's request id: the same run, day and field always give the same id, so a sweep replays."""
    return uuid.uuid5(NAMESPACE, ":".join([str(run_id), *map(str, parts)]))


# ── API models ──────────────────────────────────────────────────────────────


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StudyBoundary(_Model):
    date: date
    arm: str
    result: Literal["confirmed", "unconfirmed"]
    at: datetime
    unconfirmed: list[str]


class StudyRun(_Model):
    """API-27's ``run`` and API-28's answer: the run's state now."""

    study_id: str
    run_id: str
    status: RunStatus
    ai_planner: str
    started_at: datetime
    started_by: str
    first_day: date
    pairs: int
    pairs_done: int
    pairs_left: int
    seed: int
    schedule_sha256: str
    baseline_sha256: str
    paused_at: datetime | None
    stopped_at: datetime | None
    stop_reason: str | None
    arm_now: str | None
    day_now: date | None
    next_boundary: datetime | None
    holds_in_force: int
    held_by_others: list[str]
    last_boundary: StudyBoundary | None
    deviations: int


# ── SQL ─────────────────────────────────────────────────────────────────────

_LOCK_SQL = "SELECT pg_advisory_xact_lock($1::bigint)"
_TRY_LOCK_SQL = "SELECT pg_try_advisory_lock($1::bigint)"
_UNLOCK_SQL = "SELECT pg_advisory_unlock($1::bigint)"

_STUDY_ROWS_SQL = """
SELECT id, ts, source, actor, action, target, detail, reason
  FROM activity_log
 WHERE greenhouse_id = 'vallery' AND target = $1 AND action LIKE 'study.%'
 ORDER BY ts, id
"""

_DESIRED_SQL = """
SELECT id, param, value, manual, source, actor, created_at
  FROM v_desired_state_current
 WHERE greenhouse_id = 'vallery' AND param = ANY($1::text[])
"""


def _detail(value: object) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    return dict(value) if isinstance(value, Mapping) else {}


@dataclass(frozen=True)
class StudyLog:
    """The study's ``study.*`` rows, split into the newest run's controls and its runner rows."""

    controls: list[Control]
    boundaries: dict[date, Mapping[str, Any]]
    deviations: list[Mapping[str, Any]]


def split_log(rows: Sequence[Mapping[str, Any]]) -> StudyLog:
    controls: list[Control] = []
    runner: list[tuple[str, dict[str, Any], datetime]] = []
    for row in rows:
        action = str(row["action"]).removeprefix("study.")
        detail = _detail(row["detail"])
        if action in CONTROL_ACTIONS:
            controls.append(Control(action, row["ts"], detail, str(row["actor"]), row["reason"]))  # type: ignore[arg-type]
        else:
            runner.append((action, detail, row["ts"]))
    run_id = next((str(c.detail.get("run_id")) for c in reversed(controls) if c.action == "start"), None)
    boundaries: dict[date, Mapping[str, Any]] = {}
    deviations: list[Mapping[str, Any]] = []
    for action, detail, at in runner:
        if str(detail.get("run_id")) != run_id:
            continue
        if action == "boundary":
            boundaries[date.fromisoformat(str(detail["date"]))] = {**detail, "at": at}
        elif action == "deviation":
            deviations.append({**detail, "at": at})
    return StudyLog(controls, boundaries, deviations)


async def _log(conn: asyncpg.Connection, study_id: str) -> StudyLog:
    return split_log(await conn.fetch(_STUDY_ROWS_SQL, study_id))


async def desired_now(conn: asyncpg.Connection) -> dict[str, Desired]:
    rows = await conn.fetch(_DESIRED_SQL, list(FIELDS))
    return {
        row["param"]: Desired(
            id=row["id"],
            value=None if row["value"] is None else float(row["value"]),
            manual=bool(row["manual"]),
            source=row["source"],
            actor=row["actor"],
            created_at=row["created_at"],
        )
        for row in rows
    }


def run_view(study: PlannerStudy, run: Run, log: StudyLog, current: Mapping[str, Desired], at: datetime) -> StudyRun:
    day = run.day_at(at)
    last = max(log.boundaries.items(), default=None, key=lambda item: item[0])
    return StudyRun(
        study_id=study.study_id,
        run_id=str(run.run_id),
        status=run.status,
        ai_planner=study.ai_planner,
        started_at=run.started_at,
        started_by=run.started_by,
        first_day=run.first_day,
        pairs=run.pairs,
        pairs_done=run.pairs_done,
        pairs_left=run.pairs_left,
        seed=run.seed,
        schedule_sha256=run.schedule_sha256,
        baseline_sha256=run.baseline_sha256,
        paused_at=run.paused_at,
        stopped_at=run.stopped_at,
        stop_reason=run.stop_reason,
        arm_now=day.arm if day else None,
        day_now=day.date if day else None,
        next_boundary=run.next_boundary(at),
        holds_in_force=sum(1 for row in current.values() if row.ours and row.value is not None),
        held_by_others=sorted(name for name, row in current.items() if row.foreign_hold),
        last_boundary=(
            None
            if last is None
            else StudyBoundary(
                date=last[0],
                arm=str(last[1]["arm"]),
                result=last[1]["result"],
                at=last[1]["at"],
                unconfirmed=list(last[1].get("unconfirmed") or []),
            )
        ),
        deviations=len(log.deviations),
    )


async def current_run(conn: asyncpg.Connection, study: PlannerStudy, at: datetime) -> tuple[Run | None, StudyLog]:
    log = await _log(conn, study.study_id)
    return place(study, log.controls, at), log


# ── API-28: start, pause, resume, stop ──────────────────────────────────────


def _refuse(code: str, detail: str, field_name: str | None = None) -> ServiceError:
    return ServiceError(problem(code, detail, field=field_name))  # type: ignore[arg-type]


def _study(study_id: str) -> PlannerStudy:
    try:
        return load_study(study_id)
    except KeyError as exc:
        raise _refuse("not_found", f"no study {study_id!r}", "id") from exc


async def control(
    conn: asyncpg.Connection,
    actor: Actor,
    study_id: str,
    action: Action,
    *,
    first_day: date | None = None,
    reason: str | None = None,
    request_id: UUID | str | None = None,
) -> activity.WriteResult[StudyRun]:
    """API-28: one ``study.<action>`` row, after the action's checks, under ``LOCK_KEY``.

    - ``start``: the definition is pre-registered and its baseline file hashes to the committed value; no run is
      scheduled, running or paused; ``first_day``'s boundary is still ahead; nobody holds any of the 48 fields
      manual (``study_state``, naming them). The row's detail carries the new run id, first day, pairs, seed and both
      hashes, so the run is reproducible from the row alone.
    - ``pause``: the run is scheduled or running. ``resume``: it is paused. ``stop``: it is scheduled, running or
      paused. A refusal is 422 ``study_state``. The runner applies the change on its next sweep (within a minute).
    """
    study = _study(study_id)
    if action not in CONTROL_ACTIONS:
        raise _refuse("invalid_request", f"unknown study action {action!r}", "action")
    if action != "start" and first_day is not None:
        raise _refuse("invalid_request", "first_day goes with start only", "first_day")
    request: dict[str, Any] = {"action": action, "first_day": first_day, "reason": reason}

    async def lock(tx: asyncpg.Connection) -> None:
        await tx.execute(_LOCK_SQL, LOCK_KEY)

    async def perform(tx: asyncpg.Connection) -> tuple[StudyRun, dict[str, Any]]:
        at: datetime = await tx.fetchval("SELECT now()")
        run, log = await current_run(tx, study, at)
        live = run is not None and run.status in ("scheduled", "running", "paused")
        if action == "start":
            if live:
                raise _refuse("study_state", f"run {run.run_id} is {run.status}; stop it before starting another")
            if not study.preregistered:
                raise _refuse(
                    "study_state",
                    "the study is not pre-registered: commit its baseline file, seed and schedule hash first",
                )
            try:
                load_baseline(study)
            except ValueError as exc:
                raise _refuse("study_state", str(exc)) from exc
            if first_day is None:
                raise _refuse("invalid_request", "start needs first_day", "first_day")
            if boundary(study, first_day) <= at:
                raise _refuse("study_state", f"day 1 must start after now: {first_day} 11:00 has passed", "first_day")
            held = sorted(
                f"{name} ({row.source}:{row.actor})"
                for name, row in (await desired_now(tx)).items()
                if row.foreign_hold
            )
            if held:
                raise _refuse(
                    "study_state", "release these planner settings first, they are held manual: " + ", ".join(held)
                )
            detail = {
                "run_id": str(uuid.uuid4()),
                "first_day": first_day.isoformat(),
                "pairs": study.schedule.pairs,
                "seed": study.schedule.seed,
                "schedule_sha256": study.schedule.schedule_sha256,
                "baseline_sha256": study.baseline_vector.sha256,
                "definition_sha256": study.definition_sha256(),
                "ai_planner": study.ai_planner,
            }
        else:
            allowed = {
                "pause": ("scheduled", "running"),
                "resume": ("paused",),
                "stop": ("scheduled", "running", "paused"),
            }
            if run is None or run.status not in allowed[action]:
                state = "no run" if run is None else f"run {run.run_id} is {run.status}"
                raise _refuse("study_state", f"cannot {action}: {state}")
            detail = {"run_id": str(run.run_id)}
        detail["ref"] = {"table": "activity_log", "study_id": study.study_id}
        assert actor is not None  # recorded_write refused a missing actor first
        controls = [*log.controls, Control(action, at, detail, actor.name, reason)]
        placed = place(study, controls, at)
        assert placed is not None
        return run_view(study, placed, log, await desired_now(tx), at), detail

    async def load(tx: asyncpg.Connection, logged: ActivityLogRow) -> StudyRun:
        at = await tx.fetchval("SELECT now()")
        run, log = await current_run(tx, study, at)
        if run is None:
            raise _refuse("not_found", "the run this request started is gone")
        return run_view(study, run, log, await desired_now(tx), at)

    return await activity.recorded_write(
        conn,
        actor,
        op="API-28",
        action=f"study.{action}",
        target=study.study_id,
        request=request,
        request_id=request_id,
        reason=reason,
        perform=perform,
        load=load,
        lock=lock,
    )


# ── The boundary runner (one sweep) ─────────────────────────────────────────


@dataclass
class SweepResult:
    arm: str | None = None
    held: int = 0
    released: int = 0
    foreign: list[str] = field(default_factory=list)
    boundary: str | None = None


async def sweep(conn: asyncpg.Connection, study_id: str) -> SweepResult:
    """One runner pass for ``study_id``: hold or release the 48 fields for the arm in force, record each field
    someone else holds on a baseline day as a deviation, and record the day's boundary once every field it set
    reads settled (or, after ``boundary_confirm_minutes``, as unconfirmed with a deviation). Nothing to do without
    a started run. Call it outside a transaction; take ``LOCK_KEY`` around it (``locked_sweep``)."""
    study = load_study(study_id)
    at: datetime = await conn.fetchval("SELECT now()")
    run, log = await current_run(conn, study, at)
    result = SweepResult()
    if run is None:
        return result
    day = run.day_at(at)
    result.arm = day.arm if day else None
    current = await desired_now(conn)
    if day is not None and day.arm == "baseline":
        baseline = load_baseline(study)
        # Refuse to run a baseline that is not the one the run committed to.
        if baseline_sha256(baseline) != run.baseline_sha256:
            raise RuntimeError("the committed baseline file is not the run's baseline")
    else:
        baseline = {}
    plan = hold_plan(result.arm, baseline, current)
    note = {"run_id": str(run.run_id), "study_id": study.study_id}
    for name, value in plan.hold.items():
        assert day is not None
        await settings.write(
            conn,
            RUNNER,
            name,
            value,
            True,
            f"Planner study: baseline day {day.date}",
            request_key(run.run_id, "hold", day.date, name),
            extra_detail={"study": {**note, "date": day.date.isoformat(), "arm": day.arm}},
        )
        result.held += 1
    for name, desired_id in plan.release.items():
        why = f"Planner study: AI day {day.date}" if day is not None else f"Planner study: run {run.status}"
        await settings.release(
            conn,
            RUNNER,
            name,
            why,
            request_key(run.run_id, "release", desired_id),
            extra_detail={"study": {**note, "released_hold": desired_id}},
        )
        result.released += 1
    if day is None:
        return result
    for name, holder in plan.foreign.items():
        await _runner_row(
            conn,
            "deviation",
            study.study_id,
            {
                **note,
                "date": day.date.isoformat(),
                "arm": day.arm,
                "reason": "held_by_other",
                "field": name,
                "holder": holder,
            },
            f"{name} is held manual by {holder} during a baseline day",
            request_key(run.run_id, "foreign", day.date, name),
        )
    result.foreign = sorted(plan.foreign)
    if day.date in log.boundaries:
        return result
    checked = [name for name in FIELDS if name not in plan.foreign]
    states = await settings.statuses(conn, checked)
    unsettled = sorted(name for name in checked if name not in states or states[name].state not in SETTLED)
    window = timedelta(minutes=study.schedule.boundary_confirm_minutes)
    if unsettled and at - day.starts_at < window:
        return result
    outcome = "unconfirmed" if unsettled else "confirmed"
    await _runner_row(
        conn,
        "boundary",
        study.study_id,
        {**note, "date": day.date.isoformat(), "arm": day.arm, "result": outcome, "unconfirmed": unsettled},
        f"Planner study: {day.arm} day {day.date} {outcome}",
        request_key(run.run_id, "boundary", day.date),
    )
    if unsettled:
        await _runner_row(
            conn,
            "deviation",
            study.study_id,
            {
                **note,
                "date": day.date.isoformat(),
                "arm": day.arm,
                "reason": "boundary_unconfirmed",
                "fields": unsettled,
            },
            f"{len(unsettled)} planner settings did not read back within {study.schedule.boundary_confirm_minutes} min",
            request_key(run.run_id, "unconfirmed", day.date),
        )
    result.boundary = outcome
    return result


async def _runner_row(
    conn: asyncpg.Connection, action: str, target: str, detail: dict[str, Any], reason: str, key: UUID
) -> None:
    """One idempotent ``study.boundary`` or ``study.deviation`` row by the runner."""
    stored = {**detail, "ref": {"table": "activity_log", "study_id": target}}

    async def perform(_tx: asyncpg.Connection) -> tuple[dict[str, Any], dict[str, Any]]:
        return stored, stored

    async def load(_tx: asyncpg.Connection, logged: ActivityLogRow) -> dict[str, Any]:
        return logged.detail

    await activity.recorded_write(
        conn,
        RUNNER,
        op=f"study.{action}",
        action=f"study.{action}",
        target=target,
        request=detail,
        request_id=key,
        reason=reason,
        perform=perform,
        load=load,
    )


async def locked_sweep(conn: asyncpg.Connection, study_id: str) -> SweepResult | None:
    """``sweep`` under the session advisory lock; None when another sweep or an API-28 write holds it."""
    if not await conn.fetchval(_TRY_LOCK_SQL, LOCK_KEY):
        return None
    try:
        return await sweep(conn, study_id)
    finally:
        await conn.fetchval(_UNLOCK_SQL, LOCK_KEY)


# ── Reads for API-26/27 ─────────────────────────────────────────────────────

# Equipment on-minutes over each window: the state in force at the window's start, then each change inside it.
_EQUIPMENT_MINUTES_SQL = """
WITH w AS (SELECT * FROM unnest($1::timestamptz[], $2::timestamptz[]) AS w(s, e)),
k AS (SELECT unnest($3::text[]) AS equipment),
r AS (
    SELECT w.s, w.e, k.equipment, p.ts, p.state
      FROM w CROSS JOIN k
      CROSS JOIN LATERAL (
            (SELECT w.s AS ts, x.state FROM equipment_state x
              WHERE x.equipment = k.equipment AND x.ts < w.s ORDER BY x.ts DESC LIMIT 1)
            UNION ALL
            (SELECT x.ts, x.state FROM equipment_state x
              WHERE x.equipment = k.equipment AND x.ts >= w.s AND x.ts < w.e)
      ) p
), runs AS (
    SELECT s, equipment, state, ts,
           coalesce(lead(ts) OVER (PARTITION BY s, equipment ORDER BY ts), least(e, now())) AS until
      FROM r
)
SELECT s, equipment, (sum(extract(epoch FROM until - ts)) FILTER (WHERE state) / 60.0)::float8 AS minutes
  FROM runs WHERE until > ts
 GROUP BY s, equipment
"""

_REQUIRED_PLANS_SQL = """
SELECT w.s,
       count(l.*)::int AS due,
       count(l.*) FILTER (WHERE l.status = ANY($4::text[]))::int AS written
  FROM unnest($1::timestamptz[], $2::timestamptz[]) AS w(s, e)
  LEFT JOIN planner_trigger_ledger AS l
    ON l.event_type = ANY($3::text[]) AND l.due_at >= w.s AND l.due_at < w.e AND l.due_at <= now()
 GROUP BY w.s
"""

_PLAN_ACTIVITY_SQL = """
SELECT w.s,
       (SELECT count(*) FROM plan_journal AS p WHERE p.created_at >= w.s AND p.created_at < w.e)::int AS plans,
       (SELECT count(*) FROM setpoint_changes AS c
         WHERE c.source = 'plan' AND c.ts >= w.s AND c.ts < w.e)::int AS plan_changes
  FROM unnest($1::timestamptz[], $2::timestamptz[]) AS w(s, e)
"""

_SHADOW_RUNS_SQL = """
SELECT started_at, input_tokens, output_tokens, energy_wh::float8 AS energy_wh, attempt ->> 'status' AS status
  FROM v_planner_inference_runs
 WHERE provider = 'cortex' AND started_at >= $1 AND started_at < $2
"""

EQUIPMENT_METRICS: Mapping[str, tuple[str, ...]] = {
    "heat1_minutes": ("heat1",),
    "heat2_minutes": ("heat2",),
    "vent_minutes": ("vent", "fan1", "fan2"),
    "wetting_minutes": ("fog", "mister_any"),
}
REQUIRED_TRIGGERS = ("MIDNIGHT", "SUNRISE", "SUNSET")
WRITTEN = ("plan_written", "action_completed")
# The live values this slice fills; the climate metrics wait for the frozen freezer (#1360 follow-up).
LIVE_METRICS = (
    "heat_minutes",
    "heat1_minutes",
    "heat2_minutes",
    "vent_minutes",
    "wetting_minutes",
    "plans_written_pct",
)


class CohortCost(_Model):
    cohort: str
    calls: int
    ok: int
    input_tokens: int
    output_tokens: int
    energy_wh: float | None


class ArmCost(_Model):
    arm: str
    days: int
    planner: str
    plans_written: int
    plan_changes_sent: int
    shadow: list[CohortCost]


class ArmSummary(_Model):
    arm: str
    days_done: int
    deviation_days: int
    means: dict[str, float | None]


@dataclass(frozen=True)
class DayFacts:
    values: dict[str, float | None]
    plans: int
    plan_changes: int


async def day_facts(conn: asyncpg.Connection, days: Sequence[PlacedDay]) -> dict[date, DayFacts]:
    """Live operational values for each day that has started (equipment minutes, required plans written, plans and
    plan changes sent), by the day's date. A day still running counts up to now."""
    started = [day for day in days if day.status in ("done", "active")]
    if not started:
        return {}
    starts = [day.starts_at for day in started]
    ends = [day.ends_at for day in started]
    keys = sorted({key for group in EQUIPMENT_METRICS.values() for key in group})
    minutes: dict[tuple[datetime, str], float] = {}
    for row in await conn.fetch(_EQUIPMENT_MINUTES_SQL, starts, ends, keys):
        minutes[(row["s"], row["equipment"])] = float(row["minutes"] or 0.0)
    required = {
        row["s"]: row
        for row in await conn.fetch(_REQUIRED_PLANS_SQL, starts, ends, list(REQUIRED_TRIGGERS), list(WRITTEN))
    }
    plans = {row["s"]: row for row in await conn.fetch(_PLAN_ACTIVITY_SQL, starts, ends)}
    facts: dict[date, DayFacts] = {}
    for day in started:
        values: dict[str, float | None] = {
            metric: round(sum(minutes.get((day.starts_at, key), 0.0) for key in group), 1)
            for metric, group in EQUIPMENT_METRICS.items()
        }
        values["heat_minutes"] = round((values["heat1_minutes"] or 0.0) + (values["heat2_minutes"] or 0.0), 1)
        due = required.get(day.starts_at)
        values["plans_written_pct"] = (
            round(100.0 * due["written"] / due["due"], 1) if due is not None and due["due"] else None
        )
        activity_row = plans.get(day.starts_at)
        facts[day.date] = DayFacts(
            values=values,
            plans=int(activity_row["plans"]) if activity_row else 0,
            plan_changes=int(activity_row["plan_changes"]) if activity_row else 0,
        )
    return facts


def arm_summaries(
    days: Sequence[PlacedDay], facts: Mapping[date, DayFacts], deviation_days: set[date]
) -> list[ArmSummary]:
    """Each arm's finished days and the mean of each live value over them (not the frozen analysis)."""
    summaries = []
    for arm in ("ai", "baseline"):
        done = [day for day in days if day.arm == arm and day.status == "done" and day.date in facts]
        means: dict[str, float | None] = {}
        for metric in LIVE_METRICS:
            values = [facts[day.date].values.get(metric) for day in done]
            numbers = [value for value in values if value is not None]
            means[metric] = round(sum(numbers) / len(numbers), 1) if numbers else None
        summaries.append(
            ArmSummary(
                arm=arm,
                days_done=len(done),
                deviation_days=sum(1 for day in done if day.date in deviation_days),
                means=means,
            )
        )
    return summaries


async def arm_costs(
    conn: asyncpg.Connection, study: PlannerStudy, days: Sequence[PlacedDay], facts: Mapping[date, DayFacts]
) -> list[ArmCost]:
    """Per arm, over its started days: Hermes's plans and the plan changes sent, and the Cortex shadow runs by cohort
    (never pooled across cohorts)."""
    started = [day for day in days if day.status in ("done", "active")]
    runs: list[asyncpg.Record] = []
    if started:
        runs = await conn.fetch(_SHADOW_RUNS_SQL, min(d.starts_at for d in started), max(d.ends_at for d in started))
    costs = []
    for arm in ("ai", "baseline"):
        mine = [day for day in started if day.arm == arm]
        cohorts: dict[str, dict[str, Any]] = {}
        for run in runs:
            if not any(day.starts_at <= run["started_at"] < day.ends_at for day in mine):
                continue
            entry = cohorts.setdefault(
                cohort_of(study, run["started_at"]),
                {"calls": 0, "ok": 0, "input_tokens": 0, "output_tokens": 0, "energy_wh": None},
            )
            entry["calls"] += 1
            entry["ok"] += 1 if run["status"] == "ok" else 0
            entry["input_tokens"] += int(run["input_tokens"] or 0)
            entry["output_tokens"] += int(run["output_tokens"] or 0)
            if run["energy_wh"] is not None:
                entry["energy_wh"] = (entry["energy_wh"] or 0.0) + float(run["energy_wh"])
        costs.append(
            ArmCost(
                arm=arm,
                days=len(mine),
                planner=study.ai_planner,
                plans_written=sum(facts[day.date].plans for day in mine if day.date in facts),
                plan_changes_sent=sum(facts[day.date].plan_changes for day in mine if day.date in facts),
                shadow=[CohortCost(cohort=name, **values) for name, values in sorted(cohorts.items())],
            )
        )
    return costs


def utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "ArmCost",
    "ArmSummary",
    "LOCK_KEY",
    "RUNNER",
    "Run",
    "StudyRun",
    "control",
    "locked_sweep",
    "place",
    "sweep",
]
