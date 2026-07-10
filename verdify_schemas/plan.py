"""Plan, PlanTransition, PlanHypothesisStructured, PlanEvaluation.

Maps 1:1 onto the MCP `set_plan` / `plan_evaluate` tool contracts and the
`setpoint_plan` + `plan_journal` DB tables. Validation happens at the MCP
boundary so malformed planner output fails loud instead of partial-writing
to the database.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from .climate_intent import ClimateIntent
from .tunable_registry import registry_value_error
from .tunables import ALL_TUNABLES, NUMERIC_TUNABLES, SWITCH_TUNABLES, TunableParameter

PLAN_ID_PATTERN = re.compile(r"^iris-\d{8}-\d{4}$")
PlanSource = Literal["iris", "manual", "recovery"]


class PlanTransition(BaseModel):
    """One waypoint of a plan — a set of parameter changes at a specific ts."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    ts: AwareDatetime
    params: dict[str, float] = Field(default_factory=dict)
    climate_intent: ClimateIntent | None = None
    reason: str | None = Field(default=None, max_length=500)
    confirmed_at: AwareDatetime | None = None

    @field_validator("params", mode="after")
    @classmethod
    def _validate_param_keys(cls, v: dict[str, float]) -> dict[str, float]:
        unknown = sorted(k for k in v if k not in ALL_TUNABLES)
        if unknown:
            raise ValueError(
                f"Unknown tunable parameter(s): {unknown}. "
                f"Every key must appear in verdify_schemas.tunables.ALL_TUNABLES."
            )
        # Switches: coerce to 0.0 / 1.0 (tolerate True/False already cast to float)
        for k, val in v.items():
            if k in SWITCH_TUNABLES and val not in (0.0, 1.0):
                raise ValueError(f"Switch parameter {k!r} must be 0.0 or 1.0 (got {val!r})")
        return v

    @model_validator(mode="after")
    def _validate_physics(self) -> PlanTransition:
        p = self.params
        if not p and self.climate_intent is None:
            raise ValueError("PlanTransition requires params or climate_intent")
        if "temp_low" in p and "temp_high" in p and p["temp_low"] >= p["temp_high"]:
            raise ValueError(
                f"temp_low ({p['temp_low']}) must be < temp_high ({p['temp_high']})",
            )
        if "vpd_low" in p and "vpd_high" in p and p["vpd_low"] >= p["vpd_high"]:
            raise ValueError(
                f"vpd_low ({p['vpd_low']}) must be < vpd_high ({p['vpd_high']})",
            )
        if "safety_min" in p and "safety_max" in p and p["safety_min"] >= p["safety_max"]:
            raise ValueError(
                f"safety_min ({p['safety_min']}) must be < safety_max ({p['safety_max']})",
            )
        if "mister_engage_kpa" in p and "mister_all_kpa" in p and p["mister_engage_kpa"] > p["mister_all_kpa"]:
            raise ValueError(
                f"mister_engage_kpa ({p['mister_engage_kpa']}) must be <= mister_all_kpa ({p['mister_all_kpa']})",
            )
        registry_errors = [err for k, value in p.items() if (err := registry_value_error(k, value))]
        if registry_errors:
            raise ValueError("Registry bounds violation: " + "; ".join(registry_errors))
        return self


PlanId = Annotated[str, Field(pattern=PLAN_ID_PATTERN.pattern)]


class Plan(BaseModel):
    """Full plan envelope as emitted by Iris via `set_plan` MCP tool."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    plan_id: PlanId
    hypothesis: str = Field(..., min_length=1, max_length=20000)
    experiment: str | None = Field(default=None, max_length=20000)
    expected_outcome: str | None = Field(default=None, max_length=20000)
    transitions: list[PlanTransition] = Field(..., min_length=1)
    valid_from: AwareDatetime | None = None
    expires_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _validate_transitions_ordered(self) -> Plan:
        # Enforce strict monotonic ts so the dispatcher's "latest-applicable"
        # semantics are deterministic. Duplicates mean two waypoints at the
        # same ts disagree — ambiguous, reject.
        prev: AwareDatetime | None = None
        for t in self.transitions:
            if prev is not None and t.ts <= prev:
                raise ValueError(
                    f"Plan transitions must be strictly ts-ascending (got {t.ts} after {prev})",
                )
            prev = t.ts
        effective_from = self.valid_from or self.transitions[0].ts
        expires_at = self.expires_at or (self.transitions[-1].ts + timedelta(hours=6))
        if effective_from > self.transitions[0].ts:
            raise ValueError("Plan valid_from may not be after the first transition")
        if expires_at <= self.transitions[-1].ts:
            raise ValueError("Plan expires_at must be after the final transition")
        if expires_at <= effective_from:
            raise ValueError("Plan expires_at must be after valid_from")
        if expires_at > effective_from + timedelta(hours=78):
            raise ValueError("Plan validity may not exceed the 72-hour horizon plus a 6-hour terminal hold")
        self.valid_from = effective_from
        self.expires_at = expires_at
        return self


def plan_current_coverage_error(plan: Plan, now: datetime) -> str | None:
    """Return why a full plan cannot safely become the current effective plan."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("plan coverage reference time must be timezone-aware")
    if plan.valid_from is None or plan.expires_at is None:
        return "plan validity bounds were not resolved"
    if plan.valid_from > now:
        return "plan is not yet valid"
    if plan.expires_at <= now:
        return "plan is already expired"
    if plan.transitions[0].ts > now:
        return "plan has a gap before its first transition"
    return None


# ── Structured hypothesis (Phase 5) ────────────────────────────────


class Conditions(BaseModel):
    """Planner's snapshot of forecast / current state driving the plan."""

    model_config = ConfigDict(extra="forbid")

    outdoor_temp_peak_f: float = Field(..., ge=-40, le=140)
    outdoor_rh_min_pct: float = Field(..., ge=0, le=100)
    solar_peak_w_m2: float = Field(..., ge=0, le=1500)
    cloud_cover_avg_pct: float = Field(..., ge=0, le=100)
    notes: str | None = Field(default=None, max_length=500)


class StressWindow(BaseModel):
    """A time range the planner expects to push the greenhouse near a band edge."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["heat", "cold", "vpd_high", "vpd_low"]
    start: AwareDatetime
    end: AwareDatetime
    severity: Literal["low", "medium", "high"]
    mitigation: str = Field(..., min_length=1, max_length=500)

    @model_validator(mode="after")
    def _validate_range(self) -> StressWindow:
        if self.end <= self.start:
            raise ValueError(f"StressWindow end ({self.end}) must be after start ({self.start})")
        return self


class ParamRationale(BaseModel):
    """Why a specific parameter was tuned, anchored to forecast evidence."""

    model_config = ConfigDict(extra="forbid")

    parameter: TunableParameter
    old_value: float | None = None
    new_value: float
    forecast_anchor: str = Field(..., min_length=1, max_length=200)
    expected_effect: str = Field(..., min_length=1, max_length=500)


class PlanHypothesisStructured(BaseModel):
    """The typed, JSONB-stored companion to plan_journal.hypothesis prose."""

    model_config = ConfigDict(extra="forbid")

    conditions: Conditions
    stress_windows: list[StressWindow] = Field(default_factory=list)
    rationale: list[ParamRationale] = Field(..., min_length=1)

    @field_validator("rationale", mode="after")
    @classmethod
    def _validate_rationale_params(cls, v: list[ParamRationale]) -> list[ParamRationale]:
        # Ensure numeric-only constraint — rationales should not be written
        # for pure switch params (those are binary toggles with obvious semantics).
        for r in v:
            if r.parameter in SWITCH_TUNABLES:
                # Allow, but require 0.0 or 1.0 values
                if r.new_value not in (0.0, 1.0):
                    raise ValueError(
                        f"Switch param {r.parameter!r} rationale must have new_value 0.0 or 1.0",
                    )
            elif r.parameter not in NUMERIC_TUNABLES:
                raise ValueError(f"ParamRationale references unknown tunable: {r.parameter!r}")
        return v


class PlanClimateIntentSegment(BaseModel):
    """Semantic intent emitted for one plan transition and its materialization."""

    model_config = ConfigDict(extra="forbid")

    ts: AwareDatetime
    climate_intent: ClimateIntent
    materialized_params: dict[str, float]
    reason: str | None = None


# ── Plan evaluation (later — next-plan runs this after the outcome settles) ──


class PlanEvaluation(BaseModel):
    """Retrospective evaluation of a plan — written into plan_journal by the
    next planner cycle or by the PL-1 backfill script."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    plan_id: PlanId
    outcome_score: int = Field(..., ge=1, le=10)
    actual_outcome: str = Field(..., min_length=1, max_length=20000)
    lesson_extracted: str | None = Field(default=None, max_length=20000)


# ── Full plan_journal row shape (persisted, read-only for consumers) ──────


class PlanJournalRow(BaseModel):
    """plan_journal table row — full persisted shape.

    Used by website renderers (generate-daily-plan.py) and the planner's
    reflection step to read prior plans. Created via set_plan, updated via
    plan_evaluate. `hypothesis_structured` is the PlanHypothesisStructured
    JSONB companion added in Sprint 20 migration 084.
    """

    model_config = ConfigDict(extra="ignore")

    plan_id: PlanId
    created_at: AwareDatetime | None = None
    conditions_summary: str | None = None
    hypothesis: str | None = None
    experiment: str | None = None
    expected_outcome: str | None = None
    params_changed: list[str] | None = None
    actual_outcome: str | None = None
    outcome_score: int | None = Field(default=None, ge=1, le=10)
    lesson_extracted: str | None = None
    validated_at: AwareDatetime | None = None
    hypothesis_structured: PlanHypothesisStructured | None = None
    climate_intents: list[PlanClimateIntentSegment] | None = None
    climate_intent_version: str | None = None
    greenhouse_id: str = "vallery"
    # v1.4 audit columns (migration 093). Populated by MCP server from
    # X-Planner-Instance + X-Trigger-Id headers; NULL on pre-v1.4 rows.
    planner_instance: str | None = None
    trigger_id: UUID | None = None


# ── Planner delivery audit (Sprint 24.6 — F14, extended v1.4 in mig 093) ──


PlanDeliveryEventType = Literal[
    "SUNRISE",
    "SUNSET",
    "SOLAR_MAX",
    "MIDNIGHT",
    "TRANSITION",
    "FORECAST",
    "DEVIATION",
    "FORECAST_DEVIATION",
    "HEARTBEAT",
    "WEEKLY",
    "MANUAL",
]
# v1.4 (contract §2.G): opus | local are the post-rollout instance values.
# "iris-planner" is the backfill label for pre-v1.4 rows.
PlannerInstance = Literal["opus", "local", "iris-planner"]
# v1.4 (contract §2.F): lifecycle state on plan_delivery_log.
PlanDeliveryStatus = Literal[
    "pending",
    "acked",
    "plan_written",
    "action_completed",
    "neutral_fallback",
    "wrong_action",
    "timed_out",
    "delivery_failed",
]
PlanTerminalAction = Literal[
    "set_plan",
    "set_tunable",
    "acknowledge_trigger",
    "neutral_fallback",
    "wrong_action",
    "timeout",
    "delivery_failed",
]


@dataclass(frozen=True)
class PlannerTerminalResult:
    status: PlanDeliveryStatus
    terminal_action: PlanTerminalAction
    failure_class: str | None
    satisfies_required_plan: bool


def classify_planner_terminal_action(
    *,
    expected_action: str,
    actual_action: Literal["set_plan", "set_tunable", "acknowledge_trigger"],
    valid_full_plan: bool = False,
    explicit_neutral: bool = False,
) -> PlannerTerminalResult:
    """Classify one planner action without conflating acceptance and activity."""
    if actual_action == "set_plan" and valid_full_plan:
        return PlannerTerminalResult("plan_written", "set_plan", None, expected_action == "set_plan")
    if expected_action == "set_plan":
        if explicit_neutral and actual_action == "acknowledge_trigger":
            return PlannerTerminalResult(
                "neutral_fallback",
                "neutral_fallback",
                "explicit_neutral_fallback",
                False,
            )
        return PlannerTerminalResult(
            "wrong_action",
            "wrong_action",
            f"required_set_plan_received_{actual_action}",
            False,
        )
    if actual_action == "set_tunable":
        return PlannerTerminalResult("action_completed", "set_tunable", None, False)
    if actual_action == "acknowledge_trigger":
        return PlannerTerminalResult("acked", "acknowledge_trigger", None, False)
    return PlannerTerminalResult("wrong_action", "wrong_action", "invalid_set_plan", False)


class PlanDeliveryLogRow(BaseModel):
    """plan_delivery_log table row — one entry per send_to_iris call.

    Ingestor writes this from planning_heartbeat before posting to Hermes, then
    refreshes it after /v1/runs accepts. The verification pass updates resulting_plan_id +
    plan_written_at when a plan materializes. Makes delivery→plan
    correlation query-able instead of requiring journal log scavenging.
    See migration 092.
    """

    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    delivered_at: AwareDatetime | None = None
    event_type: PlanDeliveryEventType
    event_label: str | None = None
    session_key: str | None = None
    wake_mode: Literal["now", "next-heartbeat"] | None = None
    gateway_status: int | None = None
    gateway_body: str | None = None
    resulting_plan_id: str | None = None
    plan_written_at: AwareDatetime | None = None
    greenhouse_id: str = "vallery"
    # v1.4 audit columns (migration 093). Populated by ingestor on INSERT
    # and by MCP acknowledge_trigger; backfill sets instance='iris-planner'
    # and derives status from resulting_plan_id / gateway_status / delivered_at.
    trigger_id: UUID | None = None
    instance: PlannerInstance | None = None
    acked_at: AwareDatetime | None = None
    status: PlanDeliveryStatus | None = None
    hermes_run_id: str | None = None
    terminal_action: PlanTerminalAction | None = None
    terminal_at: AwareDatetime | None = None
    failure_class: str | None = None
    result_payload: dict[str, object] | None = None
