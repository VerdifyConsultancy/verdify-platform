"""The Planner section's reads (#1361): plan history and cost (API-25), experiments (API-26) and one study (API-27).

Each read is ``async``, takes an asyncpg connection, runs in one read-only snapshot and returns a Pydantic model
shaped like its committed OpenAPI schema (docs/design/dashboard/openapi/dashboard-api.v1.yaml).

History reads ``plan_journal`` and ``planner_trigger_ledger`` (the dashboard duty's 284 grants). Cost, the
experiment records and the arm calendar come from migration 302's views; each read checks the view is there and
readable first, so before 302 a read answers with cost "not recorded" and only the committed study definitions
instead of failing. The Cortex attempt columns (#1350, migration 294: status, latency_ms, mode, dispatched) are read
from the view's ``attempt`` jsonb only when ``planner_inference_runs`` has them, which ``pg_attribute`` says without
any table privilege. The study definitions are ``verdify_schemas.planner_study``.

A defined study's run (#1360, 2026-11-07: started, paused, resumed and stopped from the dashboard) comes from
``verdify_services.planner_study``: its status, placed days (the arm calendar), the run block, live per-day values
(equipment minutes, required plans written), the per-arm means and the per-arm cost (Hermes's plans and the Cortex
shadow runs by cohort). Results and the climate outcomes come only from the frozen analyzer of #1360, which has not
run: they read "not started", and those metrics null, until it has.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

import asyncpg
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from verdify_schemas.planner_study import PlannerStudy, studies
from verdify_services import planner_study
from verdify_services.planner_study import ArmCost, ArmSummary, StudyRun
from verdify_services.problems import ServiceError, problem
from verdify_services.reads import _int_from_query, _snapshot

DENVER = ZoneInfo("America/Denver")
REQUIRED_TRIGGERS = ("MIDNIGHT", "SUNRISE", "SUNSET")
WRITTEN = ("plan_written", "action_completed")
FAILED = ("missed", "timed_out", "delivery_failed", "wrong_action", "neutral_fallback")
PLAN_LIST_LIMIT = 60
# Closing events as words; a code missing here shows raw.
CLOSING_WORDS: Mapping[str, str] = {"facility_safe_closure": "Facility safe closure"}

HistoryDays = Annotated[Literal[30, 90], BeforeValidator(_int_from_query)]
ExperimentStatus = Literal["defined", "draft", "scheduled", "running", "paused", "finished", "closed"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ── API-25 ──────────────────────────────────────────────────────────────────


class DayCost(_Model):
    calls: int
    models: list[str]
    input_tokens: int | None
    output_tokens: int | None
    energy_wh: float | None
    latency_ms_median: int | None
    dispatched: int | None
    proposed_only: int | None


class PlannerDay(_Model):
    date: date
    plans_written: int
    triggers: dict[str, int]
    params_dispatched: int
    changes_dispatched: int
    changes_confirmed: int
    required_due: int
    required_written: int
    required_failed: int
    required_late: int
    cost: DayCost | None


class PlanCost(_Model):
    model_id: str | None
    input_tokens: int | None
    output_tokens: int | None
    energy_wh: float | None
    latency_ms: int | None
    status: str | None


class PlanSummary(_Model):
    plan_id: str
    trigger: str
    written_at: datetime
    hypothesis: str | None
    outcome_score: float | None
    params_dispatched: int
    cost: PlanCost | None


class PlannerHistory(_Model):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: date = Field(alias="from")
    to: date
    cost_recorded_from: datetime | None
    cortex_attempts: bool
    days: list[PlannerDay]
    plans: list[PlanSummary]


# ── API-26 / API-27 ─────────────────────────────────────────────────────────


class ExperimentSummary(_Model):
    id: str
    experiment_id: str | None
    study_id: str | None
    name: str
    question: str | None
    status: ExperimentStatus
    raw_status: str | None
    arms: list[str]
    started_at: datetime | None
    ended_at: datetime | None
    updated_at: datetime | None
    days_planned: int | None
    days_done: int
    deviations: int
    closed_at: datetime | None
    closed_reason: str | None


class ExperimentList(_Model):
    items: list[ExperimentSummary]


class StudyArm(_Model):
    label: str
    name: str
    planner_mode: str
    description: str


class StudyMetric(_Model):
    id: str
    label: str
    unit: str
    role: str
    direction: str
    window: str
    noninferiority_margin: float | None


class StudyLook(_Model):
    pairs: int
    purpose: str


class StudyRule(_Model):
    id: str
    rule: str
    action: str


class StudySchedule(_Model):
    timezone: str
    boundary_local: str
    pairs: int
    washout_hours: float
    blinding: str
    boundary_confirm_minutes: int
    boundary_max_retries: int


class StudyDefinition(_Model):
    study_id: str
    version: int
    definition_sha256: str
    preregistered: bool
    arms: list[StudyArm]
    metrics: list[StudyMetric]
    schedule: StudySchedule
    looks: list[StudyLook]
    covariates: list[str]
    stopping_rules: list[StudyRule]
    prerequisites: list[str]


class CalendarDay(_Model):
    date: date
    pair_index: int | None
    arm: str | None
    status: str
    starts_at: datetime | None
    ends_at: datetime | None
    deviations: int
    events: list[str]


class MetricResult(_Model):
    id: str
    label: str
    unit: str
    role: str
    direction: str
    estimate: float | None
    ci_low: float | None
    ci_high: float | None
    n_pairs: int


class StudyResults(_Model):
    state: Literal["not_started", "interim", "final"]
    reason: str | None
    pairs_done: int
    pairs_planned: int | None
    sentence: str | None
    metrics: list[MetricResult]
    analyzed_at: datetime | None
    output_sha256: str | None


class DayOutcome(_Model):
    date: date
    arm: str
    deviation: bool
    values: dict[str, float | None]
    covariates: dict[str, float | None]


class ExperimentDetail(_Model):
    experiment: ExperimentSummary
    definition: StudyDefinition | None
    run: StudyRun | None = None
    calendar: list[CalendarDay]
    next_boundary: datetime | None
    results: StudyResults
    outcomes: list[DayOutcome]
    arms: list[ArmSummary] = Field(default_factory=list)
    cost: list[ArmCost] = Field(default_factory=list)


# ── Pure helpers ────────────────────────────────────────────────────────────


def display_status(raw: str, admission: str | None, closing_kind: str | None) -> ExperimentStatus:
    """The list's status from control_experiments.status. A draft that an emergency action closed reads closed."""
    if raw == "draft":
        closed = closing_kind == "emergency_action" and admission in ("closed", "emergency_hold")
        return "closed" if closed else "draft"
    return {
        "locked": "scheduled",
        "armed": "scheduled",
        "running": "running",
        "paused": "paused",
        "completed": "finished",
        "aborted": "closed",
    }.get(raw, "draft")


def closing_words(code: str | None) -> str | None:
    return None if code is None else CLOSING_WORDS.get(code, code)


def definition(study: PlannerStudy) -> StudyDefinition:
    return StudyDefinition(
        study_id=study.study_id,
        version=study.version,
        definition_sha256=study.definition_sha256(),
        preregistered=study.preregistered,
        arms=[StudyArm(**arm.model_dump()) for arm in study.arms],
        metrics=[StudyMetric(**metric.model_dump(include=set(StudyMetric.model_fields))) for metric in study.metrics],
        schedule=StudySchedule(
            timezone=study.schedule.timezone,
            boundary_local=study.schedule.boundary_local.strftime("%H:%M"),
            pairs=study.schedule.pairs,
            washout_hours=study.schedule.washout_hours,
            blinding=study.schedule.blinding,
            boundary_confirm_minutes=study.schedule.boundary_confirm_minutes,
            boundary_max_retries=study.schedule.boundary_max_retries,
        ),
        looks=[StudyLook(**look.model_dump()) for look in study.analysis.looks],
        covariates=list(study.covariates),
        stopping_rules=[StudyRule(**rule.model_dump()) for rule in study.stopping_rules],
        prerequisites=list(study.prerequisites),
    )


def defined_summary(study: PlannerStudy) -> ExperimentSummary:
    """A committed study the orchestrator has not registered yet."""
    return ExperimentSummary(
        id=study.study_id,
        experiment_id=None,
        study_id=study.study_id,
        name=study.title,
        question=study.question,
        status="defined",
        raw_status=None,
        arms=[arm.label for arm in study.arms],
        started_at=None,
        ended_at=None,
        updated_at=None,
        days_planned=study.days_planned,
        days_done=0,
        deviations=0,
        closed_at=None,
        closed_reason=None,
    )


RUN_STATUS: Mapping[str, ExperimentStatus] = {
    "scheduled": "scheduled",
    "running": "running",
    "paused": "paused",
    "finished": "finished",
    "stopped": "closed",
}


def run_summary(study: PlannerStudy, run: planner_study.Run, deviations: int) -> ExperimentSummary:
    """A defined study with a run: its status, days and deviations from the run."""
    base = defined_summary(study)
    stopped = run.status == "stopped"
    last_end = max((day.ends_at for day in run.days if day.status == "done"), default=None)
    reason = None
    if stopped:
        reason = f"Stopped by {run.stopped_by}" + (f": {run.stop_reason}" if run.stop_reason else "")
    return base.model_copy(
        update={
            "status": RUN_STATUS[run.status],
            "raw_status": run.status,
            "started_at": run.started_at,
            "ended_at": run.stopped_at if stopped else (last_end if run.status == "finished" else None),
            "updated_at": run.stopped_at or run.paused_at or run.started_at,
            "days_done": sum(1 for day in run.days if day.status == "done"),
            "deviations": deviations,
            "closed_at": run.stopped_at if stopped else None,
            "closed_reason": reason,
        }
    )


def run_calendar(run: planner_study.Run, log: planner_study.StudyLog) -> list[CalendarDay]:
    """One cell per placed day: its arm and status, and its boundary result and deviations as events."""
    deviations: dict[date, list[Mapping[str, Any]]] = {}
    for row in log.deviations:
        deviations.setdefault(date.fromisoformat(str(row["date"])), []).append(row)
    days = []
    for day in run.days:
        ran = day.status in ("active", "done")
        boundary = log.boundaries.get(day.date) if ran else None
        mine = deviations.get(day.date, []) if ran else []
        events = [f"boundary_{boundary['result']}"] if boundary else []
        events += sorted({str(row.get("reason")) for row in mine})
        if day.reason:
            events.append(day.reason)
        days.append(
            CalendarDay(
                date=day.date,
                pair_index=day.pair_index,
                arm=day.arm,
                status=day.status,
                starts_at=day.starts_at,
                ends_at=day.ends_at,
                deviations=len(mine),
                events=events,
            )
        )
    return days


def run_outcomes(
    run: planner_study.Run,
    facts: Mapping[date, planner_study.DayFacts],
    deviation_days: set[date],
    metric_ids: Sequence[str],
) -> list[DayOutcome]:
    """Each finished day's live values; a metric this slice does not compute (the climate ones) is null."""
    return [
        DayOutcome(
            date=day.date,
            arm=day.arm,
            deviation=day.date in deviation_days,
            values={metric: facts[day.date].values.get(metric) for metric in metric_ids},
            covariates={},
        )
        for day in run.days
        if day.status == "done" and day.date in facts
    ]


def record_summary(
    row: Mapping[str, Any], arms: Sequence[str], days_done: int, defined: Sequence[PlannerStudy]
) -> ExperimentSummary:
    """One control_experiments row; a committed definition that supersedes or defines it supplies its words."""
    experiment_id = str(row["experiment_id"])
    study = next((item for item in defined if item.study_id == row["study_id"]), None)
    superseded = next(
        (entry for item in defined for entry in item.supersedes if str(entry.experiment_id) == experiment_id), None
    )
    status = display_status(row["status"], row["admission_state"], row["closing_event_kind"])
    closed = status == "closed"
    reason = superseded.reason if superseded is not None else closing_words(row["closing_event_reason"])
    planned = row["randomized_pair_count"]
    return ExperimentSummary(
        id=experiment_id,
        experiment_id=experiment_id,
        study_id=row["study_id"],
        name=row["name"],
        question=study.question if study is not None else None,
        status=status,
        raw_status=row["status"],
        arms=list(arms) or ([arm.label for arm in study.arms] if study is not None else []),
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        updated_at=row["updated_at"],
        days_planned=2 * planned if planned else (study.days_planned if study is not None else None),
        days_done=days_done,
        deviations=row["deviation_events"] or 0,
        closed_at=(row["ended_at"] or row["closing_event_at"]) if closed else None,
        closed_reason=reason if closed else None,
    )


def calendar_days(assignments: Sequence[Mapping[str, Any]], events: Sequence[Mapping[str, Any]]) -> list[CalendarDay]:
    """One cell per assigned study day (the local date its range starts on), with its events."""
    by_assignment: dict[Any, list[Mapping[str, Any]]] = {}
    for event in events:
        by_assignment.setdefault(event["assignment_id"], []).append(event)
    days = []
    for row in assignments:
        starts = row["starts_at"]
        mine = by_assignment.get(row["assignment_id"], [])
        days.append(
            CalendarDay(
                date=(
                    starts.astimezone(DENVER).date()
                    if starts is not None
                    else row["created_at"].astimezone(DENVER).date()
                ),
                pair_index=row["pair_index"],
                arm=row["arm_label"],
                status=row["status"],
                starts_at=starts,
                ends_at=row["ends_at"],
                deviations=sum(1 for event in mine if event["severity"] in ("warning", "critical")),
                events=sorted({event["event_kind"] for event in mine}),
            )
        )
    return sorted(days, key=lambda day: (day.date, day.pair_index or 0))


def next_boundary(study: PlannerStudy | None, status: ExperimentStatus, at: datetime) -> datetime | None:
    """The next local boundary while the study runs or is scheduled."""
    if study is None or status not in ("running", "scheduled"):
        return None
    local = at.astimezone(DENVER)
    boundary = datetime.combine(local.date(), study.schedule.boundary_local, tzinfo=DENVER)
    return (
        boundary
        if boundary > local
        else datetime.combine(local.date() + timedelta(days=1), study.schedule.boundary_local, tzinfo=DENVER)
    )


def not_started(study: PlannerStudy | None, days_done: int) -> StudyResults:
    reason = (
        "No study day has run."
        if days_done == 0
        else "The frozen analyzer (#1360) has not produced output for this study yet. The daily outcomes are live "
        "run-time values, not the frozen analysis; the climate metrics wait for it."
    )
    return StudyResults(
        state="not_started",
        reason=reason,
        pairs_done=days_done // 2,
        pairs_planned=study.schedule.pairs if study is not None else None,
        sentence=None,
        metrics=[],
        analyzed_at=None,
        output_sha256=None,
    )


def _json(value: object) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    return value if isinstance(value, dict) else {}


def _int(value: object) -> int | None:
    return None if value is None else int(value)


def _float(value: object) -> float | None:
    return None if value is None else float(value)


# ── SQL ─────────────────────────────────────────────────────────────────────

_CAPABILITY_SQL = """
SELECT CASE WHEN to_regclass('public.v_planner_inference_runs') IS NULL THEN false
            ELSE has_table_privilege(to_regclass('public.v_planner_inference_runs'), 'SELECT') END AS runs,
       CASE WHEN to_regclass('public.v_planner_experiments') IS NULL THEN false
            ELSE has_table_privilege(to_regclass('public.v_planner_experiments'), 'SELECT') END AS experiments,
       EXISTS (SELECT 1 FROM pg_attribute
                WHERE attrelid = to_regclass('public.planner_inference_runs')
                  AND attname = 'latency_ms' AND NOT attisdropped) AS attempts,
       now() AS at
"""

_PLAN_TRIGGER = """
LEFT JOIN LATERAL (SELECT l.event_type FROM planner_trigger_ledger AS l
                    WHERE l.trigger_id = pj.trigger_id
                    ORDER BY l.expected_at DESC LIMIT 1) AS t ON true
"""

_HISTORY_DAYS_SQL = f"""
WITH p AS (
    SELECT (pj.created_at AT TIME ZONE 'America/Denver')::date AS date,
           coalesce(t.event_type, 'UNKNOWN') AS trigger
      FROM plan_journal AS pj {_PLAN_TRIGGER}
     WHERE pj.created_at >= $1::date::timestamp AT TIME ZONE 'America/Denver'
       AND pj.created_at < ($2::date + 1)::timestamp AT TIME ZONE 'America/Denver'
), s AS (
    SELECT (ts AT TIME ZONE 'America/Denver')::date AS date, parameter, confirmed_at
      FROM setpoint_changes
     WHERE source = 'plan'
       AND ts >= $1::date::timestamp AT TIME ZONE 'America/Denver'
       AND ts < ($2::date + 1)::timestamp AT TIME ZONE 'America/Denver'
), l AS (
    SELECT (expected_at AT TIME ZONE 'America/Denver')::date AS date, status, due_at, resolved_at
      FROM planner_trigger_ledger
     WHERE expected_at >= $1::date::timestamp AT TIME ZONE 'America/Denver'
       AND expected_at < ($2::date + 1)::timestamp AT TIME ZONE 'America/Denver'
       AND expected_at <= now() AND event_type = ANY($3::text[])
)
SELECT d.date,
       (SELECT count(*) FROM p WHERE p.date = d.date)::int AS plans_written,
       (SELECT jsonb_object_agg(x.trigger, x.n)
          FROM (SELECT trigger, count(*) AS n FROM p WHERE p.date = d.date GROUP BY 1) AS x) AS triggers,
       (SELECT count(DISTINCT s.parameter) FROM s WHERE s.date = d.date)::int AS params_dispatched,
       (SELECT count(*) FROM s WHERE s.date = d.date)::int AS changes_dispatched,
       (SELECT count(s.confirmed_at) FROM s WHERE s.date = d.date)::int AS changes_confirmed,
       (SELECT count(*) FROM l WHERE l.date = d.date)::int AS required_due,
       (SELECT count(*) FROM l WHERE l.date = d.date AND l.status = ANY($4::text[]))::int AS required_written,
       (SELECT count(*) FROM l WHERE l.date = d.date AND l.status = ANY($5::text[]))::int AS required_failed,
       (SELECT count(*) FROM l WHERE l.date = d.date AND l.status = ANY($4::text[])
           AND l.resolved_at > l.due_at)::int AS required_late
  FROM generate_series($1::date, $2::date, interval '1 day') AS g(day)
 CROSS JOIN LATERAL (SELECT g.day::date AS date) AS d
 ORDER BY d.date
"""

_HISTORY_COST_SQL = """
SELECT (r.started_at AT TIME ZONE 'America/Denver')::date AS date,
       count(*)::int AS calls,
       array_remove(array_agg(DISTINCT r.model_id), NULL) AS models,
       sum(r.input_tokens)::bigint AS input_tokens,
       sum(r.output_tokens)::bigint AS output_tokens,
       sum(r.energy_wh)::float8 AS energy_wh,
       percentile_disc(0.5) WITHIN GROUP (ORDER BY coalesce((r.attempt ->> 'latency_ms')::int,
           (extract(epoch FROM r.ended_at - r.started_at) * 1000)::int)) AS latency_ms_median,
       count(*) FILTER (WHERE (r.attempt ->> 'dispatched')::boolean)::int AS dispatched,
       count(*) FILTER (WHERE NOT (r.attempt ->> 'dispatched')::boolean)::int AS proposed_only
  FROM v_planner_inference_runs AS r
 WHERE r.started_at >= $1::date::timestamp AT TIME ZONE 'America/Denver'
   AND r.started_at < ($2::date + 1)::timestamp AT TIME ZONE 'America/Denver'
 GROUP BY 1
"""

_COST_FROM_SQL = "SELECT min(started_at) FROM v_planner_inference_runs"

_PLANS_SQL = f"""
SELECT pj.plan_id, pj.created_at, pj.hypothesis, pj.outcome_score, pj.trigger_id,
       coalesce(t.event_type, 'UNKNOWN') AS trigger,
       (SELECT count(DISTINCT c.parameter) FROM setpoint_changes AS c
         WHERE c.source = 'plan' AND c.trigger_id = pj.trigger_id
           AND c.ts >= pj.created_at - interval '1 hour' AND c.ts < pj.created_at + interval '4 days')::int
           AS params_dispatched
  FROM plan_journal AS pj {_PLAN_TRIGGER}
 WHERE pj.created_at >= $1::date::timestamp AT TIME ZONE 'America/Denver'
 ORDER BY pj.created_at DESC
 LIMIT $2
"""

_PLAN_COST_SQL = """
SELECT DISTINCT ON (r.attempt ->> 'trigger_id') r.attempt ->> 'trigger_id' AS trigger_id, r.model_id,
       r.input_tokens, r.output_tokens, r.energy_wh::float8 AS energy_wh,
       coalesce((r.attempt ->> 'latency_ms')::int, (extract(epoch FROM r.ended_at - r.started_at) * 1000)::int)
           AS latency_ms,
       r.attempt ->> 'status' AS status
  FROM v_planner_inference_runs AS r
 WHERE r.attempt ->> 'trigger_id' = ANY($1::text[])
 ORDER BY r.attempt ->> 'trigger_id', (r.attempt ->> 'status') = 'ok' DESC, r.started_at DESC
"""

_EXPERIMENTS_SQL = """
SELECT e.*, (SELECT count(*) FROM v_planner_assignments AS a
              WHERE a.experiment_id = e.experiment_id AND a.starts_at <= now())::int AS days_done,
       (SELECT array_agg(DISTINCT a.arm_label ORDER BY a.arm_label) FROM v_planner_assignments AS a
         WHERE a.experiment_id = e.experiment_id) AS assigned_arms
  FROM v_planner_experiments AS e
 ORDER BY coalesce(e.started_at, e.created_at) DESC
"""

_ASSIGNMENTS_SQL = """
SELECT assignment_id, pair_index, block_index, arm_label, status, starts_at, ends_at, created_at
  FROM v_planner_assignments WHERE experiment_id = $1 ORDER BY starts_at, pair_index
"""

_EVENTS_SQL = """
SELECT assignment_id, event_kind, severity FROM v_planner_experiment_events
 WHERE experiment_id = $1 AND assignment_id IS NOT NULL
"""


# ── Reads ───────────────────────────────────────────────────────────────────


async def history(conn: asyncpg.Connection, days: int = 30) -> PlannerHistory:
    """API-25 ``GET /v1/planner/history``: the 30 or 90 local days ending today, and the newest plans."""
    if days not in (30, 90):
        raise ValueError("days must be 30 or 90")
    async with _snapshot(conn):
        cap = await conn.fetchrow(_CAPABILITY_SQL)
        last: date = cap["at"].astimezone(DENVER).date()
        first = last - timedelta(days=days - 1)
        rows = await conn.fetch(_HISTORY_DAYS_SQL, first, last, list(REQUIRED_TRIGGERS), list(WRITTEN), list(FAILED))
        plans = await conn.fetch(_PLANS_SQL, first, PLAN_LIST_LIMIT)
        costs: dict[date, Mapping[str, Any]] = {}
        plan_costs: dict[str, Mapping[str, Any]] = {}
        cost_from = None
        if cap["runs"]:
            cost_from = await conn.fetchval(_COST_FROM_SQL)
            costs = {row["date"]: row for row in await conn.fetch(_HISTORY_COST_SQL, first, last)}
            triggers = [str(row["trigger_id"]) for row in plans if row["trigger_id"] is not None]
            if triggers:
                plan_costs = {row["trigger_id"]: row for row in await conn.fetch(_PLAN_COST_SQL, triggers)}
    attempts = bool(cap["attempts"])
    return PlannerHistory(
        from_=first,
        to=last,
        cost_recorded_from=cost_from,
        cortex_attempts=attempts,
        days=[
            PlannerDay(
                date=row["date"],
                plans_written=row["plans_written"],
                triggers={key: int(value) for key, value in _json(row["triggers"]).items()},
                params_dispatched=row["params_dispatched"],
                changes_dispatched=row["changes_dispatched"],
                changes_confirmed=row["changes_confirmed"],
                required_due=row["required_due"],
                required_written=row["required_written"],
                required_failed=row["required_failed"],
                required_late=row["required_late"],
                cost=_day_cost(costs.get(row["date"]), attempts),
            )
            for row in rows
        ],
        plans=[
            PlanSummary(
                plan_id=row["plan_id"],
                trigger=row["trigger"],
                written_at=row["created_at"],
                hypothesis=row["hypothesis"],
                outcome_score=_float(row["outcome_score"]),
                params_dispatched=row["params_dispatched"],
                cost=_plan_cost(plan_costs.get(str(row["trigger_id"]))),
            )
            for row in plans
        ],
    )


def _day_cost(row: Mapping[str, Any] | None, attempts: bool) -> DayCost | None:
    if row is None:
        return None
    return DayCost(
        calls=row["calls"],
        models=list(row["models"] or []),
        input_tokens=_int(row["input_tokens"]),
        output_tokens=_int(row["output_tokens"]),
        energy_wh=_float(row["energy_wh"]),
        latency_ms_median=_int(row["latency_ms_median"]),
        dispatched=row["dispatched"] if attempts else None,
        proposed_only=row["proposed_only"] if attempts else None,
    )


def _plan_cost(row: Mapping[str, Any] | None) -> PlanCost | None:
    if row is None:
        return None
    return PlanCost(
        model_id=row["model_id"],
        input_tokens=_int(row["input_tokens"]),
        output_tokens=_int(row["output_tokens"]),
        energy_wh=_float(row["energy_wh"]),
        latency_ms=_int(row["latency_ms"]),
        status=row["status"],
    )


async def _records(conn: asyncpg.Connection) -> tuple[list[asyncpg.Record], datetime]:
    cap = await conn.fetchrow(_CAPABILITY_SQL)
    rows = await conn.fetch(_EXPERIMENTS_SQL) if cap["experiments"] else []
    return rows, cap["at"]


Runs = Mapping[str, tuple[planner_study.Run | None, planner_study.StudyLog]]


async def _runs(conn: asyncpg.Connection, defined: Sequence[PlannerStudy], at: datetime) -> Runs:
    return {study.study_id: await planner_study.current_run(conn, study, at) for study in defined}


def _summaries(
    rows: Sequence[Mapping[str, Any]], defined: Sequence[PlannerStudy], runs: Runs | None = None
) -> list[ExperimentSummary]:
    registered = {row["study_id"] for row in rows}
    items = []
    for study in defined:
        if study.study_id in registered:
            continue
        run, log = (runs or {}).get(study.study_id, (None, None))
        items.append(defined_summary(study) if run is None else run_summary(study, run, len(log.deviations)))
    items += [record_summary(row, row["assigned_arms"] or [], row["days_done"], defined) for row in rows]
    return items


async def experiments(conn: asyncpg.Connection) -> ExperimentList:
    """API-26 ``GET /v1/planner/experiments``: committed studies not yet registered (with their run, once started),
    then every experiment record."""
    defined = studies()
    async with _snapshot(conn):
        rows, at = await _records(conn)
        runs = await _runs(conn, defined, at)
    return ExperimentList(items=_summaries(rows, defined, runs))


async def experiment(conn: asyncpg.Connection, key: str) -> ExperimentDetail:
    """API-27 ``GET /v1/planner/experiments/{id}``: a study by experiment id or study id, with its calendar."""
    defined = studies()
    async with _snapshot(conn):
        rows, at = await _records(conn)
        runs = await _runs(conn, defined, at)
        items = _summaries(rows, defined, runs)
        summary = next((item for item in items if key in (item.id, item.study_id)), None)
        if summary is None:
            raise ServiceError(problem("not_found", f"no experiment or study {key!r}", field="id"))
        assignments, events = [], []
        if summary.experiment_id is not None:
            uuid = UUID(summary.experiment_id)
            assignments = await conn.fetch(_ASSIGNMENTS_SQL, uuid)
            events = await conn.fetch(_EVENTS_SQL, uuid)
        study = next((item for item in defined if item.study_id == summary.study_id), None)
        run, log = (None, None)
        if study is not None and summary.experiment_id is None:
            run, log = runs.get(study.study_id, (None, None))
        if study is not None and run is not None:
            facts = await planner_study.day_facts(conn, run.days)
            current = await planner_study.desired_now(conn)
            cost = await planner_study.arm_costs(conn, study, run.days, facts)
    if study is None or run is None or log is None:
        return ExperimentDetail(
            experiment=summary,
            definition=definition(study) if study is not None else None,
            calendar=calendar_days(assignments, events),
            next_boundary=next_boundary(study, summary.status, at),
            results=not_started(study, summary.days_done),
            outcomes=[],
        )
    deviation_days = {date.fromisoformat(str(row["date"])) for row in log.deviations}
    return ExperimentDetail(
        experiment=summary,
        definition=definition(study),
        run=planner_study.run_view(study, run, log, current, at),
        calendar=run_calendar(run, log),
        next_boundary=run.next_boundary(at),
        results=not_started(study, summary.days_done),
        outcomes=run_outcomes(run, facts, deviation_days, [metric.id for metric in study.metrics]),
        arms=planner_study.arm_summaries(run.days, facts, deviation_days),
        cost=cost,
    )


__all__ = [
    "ExperimentDetail",
    "ExperimentList",
    "HistoryDays",
    "PlannerHistory",
    "experiment",
    "experiments",
    "history",
]
