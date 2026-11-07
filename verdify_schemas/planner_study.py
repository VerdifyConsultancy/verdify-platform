"""The planner A/B study definition (#1360): arms, metrics, schedule, analysis and stopping rules.

One reviewed YAML file per study (``planner_ab_winter_2026.yaml`` beside this module) holds the design from
docs/research/w5/planner-ab.md. ``PlannerStudy`` checks it: exactly the two arms ``ai`` (dispatch) and
``baseline`` (propose-only), one primary metric, a guardrail with its non-inferiority margin, looks that end at the
planned pair count, and heat reported but never scored (electric heat1 is the primary heater, Jason 2026-11-05).
``definition_sha256`` is the canonical hash the orchestrator records when it registers the study, and ``load_study``
the one loader the orchestrator and the operator API (API-26/24) share.

Jason, 2026-11-07: the study resumes with Hermes as the AI arm (``ai_planner``), and the dashboard starts, pauses,
resumes and stops it (docs/design/dashboard/planner-study.md). The arm order is a pure function of the committed seed
(``arm_order``, hashed by ``schedule_sha256``), and the baseline vector is one committed JSON file per study
(``planner_study_baselines/<study_id>.json``, hashed by ``baseline_sha256``). ``shadow_cohorts`` keeps the Cortex
shadow runs before and after #1482 apart: they are different treatments and are never pooled.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, time
from functools import cache
from pathlib import Path
from typing import Literal
from uuid import UUID

import yaml
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from verdify_schemas.tunable_registry import PLANNER_PUSHABLE_REG

STUDY_DIR = Path(__file__).parent
STUDY_FILES: dict[str, str] = {"planner-ab-winter-2026": "planner_ab_winter_2026.yaml"}
BASELINE_DIR = STUDY_DIR / "planner_study_baselines"
ARMS = ("ai", "baseline")
SHA256 = r"^[0-9a-f]{64}$"
# A heat metric is a cost: reported with its CI, never scored as better or worse.
HEAT_METRICS = re.compile(r"^(heat|electric|thermal|therms)")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Superseded(_Model):
    experiment_id: UUID
    name: str
    reason: str = Field(min_length=20)


class Arm(_Model):
    label: Literal["ai", "baseline"]
    name: str
    planner_mode: Literal["dispatch", "propose_only"]
    description: str


class BaselineVector(_Model):
    fields: Literal["PLANNER_PUSHABLE_REG"]
    field_count: int
    rule: str
    sha256: str | None = Field(default=None, pattern=SHA256)

    @field_validator("field_count")
    @classmethod
    def _every_planner_field(cls, value: int) -> int:
        if value != len(PLANNER_PUSHABLE_REG):
            raise ValueError(f"the baseline restores all {len(PLANNER_PUSHABLE_REG)} planner-pushable fields")
        return value


class Schedule(_Model):
    unit: Literal["day"]
    timezone: Literal["America/Denver"]
    boundary_local: time
    pairs: int = Field(ge=2, le=90)
    order: Literal["seeded_coin_flip_within_adjacent_pair"]
    blinding: Literal["none"]
    washout_hours: float = Field(ge=0, lt=12)
    seed: int | None = None
    schedule_sha256: str | None = Field(default=None, pattern=SHA256)
    boundary_confirm_minutes: int = Field(gt=0, le=60)
    boundary_max_retries: int = Field(ge=0, le=5)
    only_at_pair_boundaries: list[str]


class ShadowCohort(_Model):
    """A Cortex shadow treatment: the runs from ``starts_at`` until the next cohort starts. Each cohort is its own
    treatment and is never pooled with another (Jason, 2026-11-07, #1482). The first cohort's ``starts_at`` may be
    null (from the first run). A later cohort's ``starts_at`` is null until its rollout time is known; meanwhile
    every run at or after its ``not_before`` reads ``unattributed`` rather than joining the cohort before it."""

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,62}$")
    label: str
    starts_at: AwareDatetime | None
    not_before: AwareDatetime | None = None
    note: str


class Metric(_Model):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    label: str
    unit: str
    role: Literal["primary", "guardrail", "secondary", "cost", "planner"]
    direction: Literal["higher", "lower", "report"]
    window: Literal["night_post_washout", "study_day_post_washout", "whole_day"]
    source: str
    noninferiority_margin: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _heat_is_reported(self) -> Metric:
        if HEAT_METRICS.match(self.id) and (self.direction != "report" or self.role in ("primary", "guardrail")):
            raise ValueError(f"{self.id}: heat is a reported cost, never scored (heat1 is the primary heater)")
        if (self.role == "guardrail") != (self.noninferiority_margin is not None):
            raise ValueError(f"{self.id}: a guardrail, and only a guardrail, has a non-inferiority margin")
        return self


class Look(_Model):
    pairs: int = Field(gt=0)
    purpose: Literal["safety_futility", "final"]


class Analysis(_Model):
    estimator: Literal["ols_arm_covariates_pair_fixed_effects"]
    confidence: Literal[0.95]
    ci_methods: list[Literal["randomization_inference", "hac_t"]]
    randomization_draws: int = Field(ge=1000)
    population: Literal["intention_to_treat"]
    sensitivity: list[Literal["per_protocol"]]
    looks: list[Look] = Field(min_length=1)
    interim_label: Literal[True]
    frozen_output: Literal["byte_reproducible_json"]
    expected_precision: dict[str, float]


class StoppingRule(_Model):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    rule: str
    action: str


class PlannerStudy(_Model):
    study_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,62}$")
    version: int = Field(ge=1)
    title: str
    question: str
    status: Literal["defined"]
    ai_planner: Literal["hermes"]
    issues: list[str]
    research: str
    supersedes: list[Superseded]
    arms: list[Arm]
    same_in_both_arms: list[str]
    baseline_vector: BaselineVector
    schedule: Schedule
    metrics: list[Metric]
    covariates: list[str] = Field(min_length=1)
    recorded_not_adjusted: list[str]
    analysis: Analysis
    stopping_rules: list[StoppingRule] = Field(min_length=1)
    prerequisites: list[str]
    shadow_cohorts: list[ShadowCohort] = Field(min_length=1)

    @model_validator(mode="after")
    def _design(self) -> PlannerStudy:
        if [(arm.label, arm.planner_mode) for arm in self.arms] != [("ai", "dispatch"), ("baseline", "propose_only")]:
            raise ValueError("two arms: ai dispatches, baseline runs the planner propose-only")
        ids = [metric.id for metric in self.metrics]
        if len(set(ids)) != len(ids):
            raise ValueError("metric ids are unique")
        roles = [metric.role for metric in self.metrics]
        if roles.count("primary") != 1 or roles[0] != "primary" or "guardrail" not in roles:
            raise ValueError("exactly one primary metric, listed first, and at least one guardrail")
        pairs = [look.pairs for look in self.analysis.looks]
        if pairs != sorted(set(pairs)) or pairs[-1] != self.schedule.pairs:
            raise ValueError("looks rise and the last is the planned pair count")
        if self.analysis.looks[-1].purpose != "final" or any(
            look.purpose == "final" for look in self.analysis.looks[:-1]
        ):
            raise ValueError("only the last look is final")
        if set(self.covariates) & set(ids):
            raise ValueError("a covariate is never an outcome")
        if self.schedule.seed is not None and self.schedule.schedule_sha256 is not None:
            if schedule_sha256(self.schedule.seed, self.schedule.pairs) != self.schedule.schedule_sha256:
                raise ValueError("schedule_sha256 is not the hash of the seed's arm order")
        starts = [cohort.starts_at for cohort in self.shadow_cohorts if cohort.starts_at is not None]
        if starts != sorted(starts) or len({cohort.id for cohort in self.shadow_cohorts}) != len(self.shadow_cohorts):
            raise ValueError("shadow cohorts have unique ids and start in order")
        if any(cohort.starts_at is None and cohort.not_before is None for cohort in self.shadow_cohorts[1:]):
            raise ValueError("a later shadow cohort without its start time needs not_before")
        return self

    @property
    def primary(self) -> Metric:
        return self.metrics[0]

    @property
    def days_planned(self) -> int:
        return 2 * self.schedule.pairs

    @property
    def preregistered(self) -> bool:
        """The seed, schedule hash and baseline hash are committed: day 1 may start."""
        return None not in (self.schedule.seed, self.schedule.schedule_sha256, self.baseline_vector.sha256)

    def arm_order(self) -> list[tuple[str, str]]:
        """The committed seed's arm order, one (first, second) per pair; ValueError before pre-registration."""
        if self.schedule.seed is None:
            raise ValueError(f"{self.study_id} has no committed seed")
        return arm_order(self.schedule.seed, self.schedule.pairs)

    def definition_sha256(self) -> str:
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode()).hexdigest()


def arm_order(seed: int, pairs: int) -> list[tuple[str, str]]:
    """``seeded_coin_flip_within_adjacent_pair``: pair i is (ai, baseline) when the low bit of
    sha256("<seed>:<i>")'s first byte is 0, else (baseline, ai). Pure, so the order never depends on when a
    pair runs: a pause or resume moves its dates, never its arms."""
    order = []
    for index in range(pairs):
        flip = hashlib.sha256(f"{seed}:{index}".encode()).digest()[0] & 1
        order.append(ARMS if flip == 0 else (ARMS[1], ARMS[0]))
    return order


def schedule_sha256(seed: int, pairs: int) -> str:
    """The hash committed with the seed: canonical JSON of the seed, the pair count and the arm order."""
    body = {"seed": seed, "pairs": pairs, "order": [list(pair) for pair in arm_order(seed, pairs)]}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def baseline_sha256(values: dict[str, float]) -> str:
    """The hash committed as ``baseline_vector.sha256``: canonical JSON of {field: value}, sorted by field."""
    if set(values) != set(PLANNER_PUSHABLE_REG):
        missing, extra = (
            sorted(set(PLANNER_PUSHABLE_REG) - set(values)),
            sorted(set(values) - set(PLANNER_PUSHABLE_REG)),
        )
        raise ValueError(f"the baseline names every planner-pushable field once (missing {missing}, extra {extra})")
    for name, value in values.items():
        if not isinstance(value, int | float) or isinstance(value, bool) or not math.isfinite(value):
            raise ValueError(f"{name}: the baseline value must be a finite number")
    canonical = json.dumps({name: float(values[name]) for name in sorted(values)}, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def load_baseline(study: PlannerStudy) -> dict[str, float]:
    """The committed baseline vector of a pre-registered study; ValueError when it is missing or does not hash to
    ``baseline_vector.sha256``."""
    if study.baseline_vector.sha256 is None:
        raise ValueError(f"{study.study_id} has no committed baseline hash")
    path = BASELINE_DIR / f"{study.study_id}.json"
    try:
        document = json.loads(path.read_text())
    except (OSError, ValueError) as failure:
        raise ValueError(f"{study.study_id}: the baseline file is missing or unreadable") from failure
    values = document.get("values") if isinstance(document, dict) else None
    if not isinstance(values, dict):
        raise ValueError(f"{study.study_id}: the baseline file has no values object")
    if baseline_sha256(values) != study.baseline_vector.sha256:
        raise ValueError(f"{study.study_id}: the baseline file does not hash to baseline_vector.sha256")
    return {name: float(value) for name, value in values.items()}


def cohort_of(study: PlannerStudy, started_at: datetime) -> str:
    """The shadow cohort a Cortex run started at ``started_at`` belongs to; ``unattributed`` when a later cohort's
    start is not set yet and the run is at or after its ``not_before``, or before the first cohort."""
    first = study.shadow_cohorts[0]
    if first.starts_at is not None and started_at < first.starts_at:
        return "unattributed"
    current = first.id
    for cohort in study.shadow_cohorts[1:]:
        if cohort.starts_at is not None:
            if started_at >= cohort.starts_at:
                current = cohort.id
        elif cohort.not_before is not None and started_at >= cohort.not_before:
            return "unattributed"
    return current


def parse_study(text: str) -> PlannerStudy:
    return PlannerStudy.model_validate(yaml.safe_load(text))


@cache
def load_study(study_id: str) -> PlannerStudy:
    """The committed definition of ``study_id``; KeyError for a study with no definition here."""
    study = parse_study((STUDY_DIR / STUDY_FILES[study_id]).read_text())
    if study.study_id != study_id:
        raise ValueError(f"{STUDY_FILES[study_id]} defines {study.study_id}, not {study_id}")
    return study


def studies() -> list[PlannerStudy]:
    return [load_study(study_id) for study_id in STUDY_FILES]


if __name__ == "__main__":  # pragma: no cover - prints each definition's hash
    for item in studies():
        print(item.study_id, item.version, item.definition_sha256())
