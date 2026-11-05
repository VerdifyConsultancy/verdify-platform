"""Output contract for the Cortex planner's single deterministic call (#1350, #1366).

The model answers one JSON object. `PlannerProposal` validates it, and `validate_proposal`
adds the checks JSON Schema cannot express: waypoint anchors, the registry min/max of every
field that names a registered tunable, and the `changed`/`reasons` bookkeeping.

`PlannerIntent` is `ClimateIntent` with two edits (Jason, 2026-11-05):
- adds `night_vpd_bias_kpa` (registry 0..0.25 kPa, planner-pushable), the night-dry lever;
- drops `wet_cutoff_hour`, which reaches no Tier-1 knob since wire schema v2 (#588).
Every other field keeps ClimateIntent's bounds and default, read from the model itself, so the
two cannot drift. Nothing here is imported by the ingestor or MCP.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model, model_validator

from .climate_intent import ClimateIntent
from .tunable_registry import REGISTRY, registry_value_error

DROPPED_INTENT_FIELDS = ("wet_cutoff_hour",)
ADDED_INTENT_FIELDS = ("night_vpd_bias_kpa",)


def _intent_field_definitions() -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for name, info in ClimateIntent.model_fields.items():
        if name in DROPPED_INTENT_FIELDS:
            continue
        bounds = {}
        for meta in info.metadata:
            for attr in ("ge", "le"):
                if getattr(meta, attr, None) is not None:
                    bounds[attr] = getattr(meta, attr)
        fields[name] = (float, Field(info.default, **bounds))
    night = REGISTRY["night_vpd_bias_kpa"]
    fields["night_vpd_bias_kpa"] = (float, Field(float(night.default), ge=float(night.min), le=float(night.max)))
    return fields


class _IntentBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _validate_moisture_ladder(self):
        if self.all_zone_vpd_excess_kpa < self.moisture_engage_vpd_excess_kpa:
            raise ValueError("all_zone_vpd_excess_kpa must be >= moisture_engage_vpd_excess_kpa")
        return self


PlannerIntent = create_model("PlannerIntent", __base__=_IntentBase, **_intent_field_definitions())
INTENT_FIELDS: tuple[str, ...] = tuple(PlannerIntent.model_fields)


class Prediction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: str = Field(..., min_length=1, max_length=80)
    window: str = Field(..., min_length=1, max_length=80)
    value: float


class Hypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conditions: str = Field(..., min_length=1, max_length=600)
    predictions: list[Prediction] = Field(default_factory=list, max_length=3)


class PlannerWaypoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ts: str = Field(..., min_length=10, max_length=40)
    climate_intent: PlannerIntent  # type: ignore[valid-type]
    changed: list[str] = Field(default_factory=list)
    reasons: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _changed_fields_have_reasons(self) -> PlannerWaypoint:
        unknown = sorted(set(self.changed) - set(INTENT_FIELDS))
        if unknown:
            raise ValueError(f"changed names unknown intent fields: {unknown}")
        missing = sorted(set(self.changed) - set(self.reasons))
        if missing:
            raise ValueError(f"changed fields without a reason: {missing}")
        for field, reason in self.reasons.items():
            if len(reason.split()) > 40:
                raise ValueError(f"reason for {field} is longer than 40 words")
        return self


class ExpectedOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vpd_below_band_hours: float = Field(..., ge=0.0, le=72.0)
    cold_hours: float = Field(..., ge=0.0, le=72.0)
    water_gal: float = Field(..., ge=0.0, le=2000.0)


class PlannerProposal(BaseModel):
    """The whole answer: restated evidence, hypothesis, 3-6 waypoints, expectations."""

    model_config = ConfigDict(extra="forbid")

    key_facts: list[str] = Field(..., min_length=1, max_length=5)
    hypothesis: Hypothesis
    waypoints: list[PlannerWaypoint] = Field(..., min_length=3, max_length=6)
    expected_outcome: ExpectedOutcome
    confidence: float = Field(..., ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _short_key_facts(self) -> PlannerProposal:
        for fact in self.key_facts:
            if not fact.strip() or len(fact.split()) > 35:
                raise ValueError("each key fact must be 1-35 words")
        return self


class ProposalRejected(ValueError):
    """The answer parsed but broke the contract; `errors` is what the repair prompt quotes."""

    def __init__(self, errors: Sequence[str]):
        super().__init__("; ".join(errors))
        self.errors = list(errors)


def _parse_ts(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"waypoint ts {value!r} has no UTC offset")
    return parsed


def validate_proposal(raw: dict[str, Any] | str, anchors: Sequence[str]) -> PlannerProposal:
    """Parse and check one answer against the contract and the anchors it was offered.

    Raises ProposalRejected with every error found; the caller never writes a rejected answer.
    """
    try:
        proposal = (
            PlannerProposal.model_validate_json(raw) if isinstance(raw, str) else PlannerProposal.model_validate(raw)
        )
    except ValidationError as exc:
        raise ProposalRejected(
            [f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()][:20]
        ) from exc

    errors: list[str] = []
    anchor_times = {}
    for anchor in anchors:
        anchor_times[_parse_ts(anchor)] = anchor
    seen: list[datetime] = []
    for index, waypoint in enumerate(proposal.waypoints):
        try:
            ts = _parse_ts(waypoint.ts)
        except ValueError as exc:
            errors.append(f"waypoints.{index}.ts: {exc}")
            continue
        if ts not in anchor_times:
            errors.append(f"waypoints.{index}.ts {waypoint.ts} is not one of the offered anchors")
        if seen and ts <= seen[-1]:
            errors.append(f"waypoints.{index}.ts is not after the previous waypoint")
        seen.append(ts)
        for field, value in waypoint.climate_intent.model_dump().items():
            if field in REGISTRY and (error := registry_value_error(field, value)):
                errors.append(f"waypoints.{index}.climate_intent.{error}")
    if errors:
        raise ProposalRejected(errors)
    return proposal


def proposal_json_schema() -> dict[str, Any]:
    """The JSON Schema sent with `response_format` once the gateway test passes (#1366 item 1)."""
    return PlannerProposal.model_json_schema()
