"""Pure one-cycle orchestration for lifecycle, selector, and freezer workers."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import UUID

from .contracts import (
    ContractError,
    LifecyclePlan,
    OutcomePayload,
    SelectorContext,
    SelectorIdentity,
    canonical_json_bytes,
    parse_hash_bound_document,
    require_sha256,
)
from .outcome import OutcomeSourceCandidate, evaluate_outcome
from .provider import SelectorAttemptResult, SelectorProviderAdapter

SelectorCycleKind = Literal["shadow", "randomized"]


class LifecycleStore(Protocol):
    async def schedule_shadow_cycle(self, plan: LifecyclePlan) -> Mapping[str, Any]: ...

    async def boundary_cycle(self, experiment_id: str) -> Mapping[str, Any] | None: ...


async def run_lifecycle_cycle(
    store: LifecycleStore,
    *,
    experiment_id: str,
    plan: LifecyclePlan | None,
) -> Literal["idle", "shadow_scheduled", "boundary_finalized"]:
    if plan is None:
        return "idle"
    if plan.experiment_id != experiment_id:
        raise ContractError("lifecycle plan/active experiment mismatch")
    if plan.action == "shadow_schedule":
        await store.schedule_shadow_cycle(plan)
        return "shadow_scheduled"
    row = await store.boundary_cycle(experiment_id)
    return "idle" if row is None else "boundary_finalized"


def load_lifecycle_plan(
    path: Path | None,
    expected_sha256: str | None,
    experiment_id: str,
) -> LifecyclePlan | None:
    if path is None or expected_sha256 is None:
        return None
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if len(raw) > 65_536:
        return None
    try:
        return LifecyclePlan.parse(raw, expected_sha256, experiment_id)
    except ContractError:
        return None


@dataclass(frozen=True)
class SelectorCandidate:
    cycle_kind: SelectorCycleKind
    subject_id: str
    assignment_id: str | None
    work_id: str | None
    study_id: str
    local_date: str
    invocation_key: str
    context_status: Literal["frozen", "unavailable"]
    context_payload: Mapping[str, Any]
    context_canonical_bytes: bytes
    context_sha256: str
    source_bundle_sha256: str
    context_schema_sha256: str
    selector_identity_sha256: str
    selector_artifact_sha256: str
    context_cutoff_at: datetime
    boundary_at: datetime
    resolved_at: datetime
    failure_reason: str | None

    @classmethod
    def from_row(cls, raw: Mapping[str, Any]) -> SelectorCandidate:
        required = {
            "cycle_kind",
            "subject_id",
            "assignment_id",
            "work_id",
            "study_id",
            "local_date",
            "invocation_key",
            "context_status",
            "context_payload",
            "context_canonical_bytes",
            "context_sha256",
            "source_bundle_sha256",
            "context_schema_sha256",
            "selector_identity_sha256",
            "selector_artifact_sha256",
            "context_cutoff_at",
            "boundary_at",
            "resolved_at",
            "failure_reason",
        }
        if set(raw) != required:
            raise ContractError("selector cycle result shape mismatch")
        kind = raw["cycle_kind"]
        if kind not in ("shadow", "randomized"):
            raise ContractError("selector cycle kind must be shadow or randomized")

        def canonical_uuid(value: object, field: str, *, optional: bool = False) -> str | None:
            if value is None and optional:
                return None
            try:
                parsed = UUID(str(value))
            except (ValueError, TypeError) as exc:
                raise ContractError(f"selector {field} must be a UUID") from exc
            if str(parsed) != str(value):
                raise ContractError(f"selector {field} must be a canonical UUID")
            return str(parsed)

        subject_id = canonical_uuid(raw["subject_id"], "subject_id")
        assignment_id = canonical_uuid(raw["assignment_id"], "assignment_id", optional=True)
        work_id = canonical_uuid(raw["work_id"], "work_id", optional=True)
        if (kind == "shadow" and (assignment_id is not None or work_id != subject_id)) or (
            kind == "randomized" and (assignment_id != subject_id or work_id is not None)
        ):
            raise ContractError("selector cycle subject/assignment/work binding mismatch")
        study_id = raw["study_id"]
        invocation_key = raw["invocation_key"]
        if not isinstance(study_id, str) or not study_id or not isinstance(invocation_key, str):
            raise ContractError("selector study/invocation identity is missing")
        if kind == "shadow" and invocation_key != subject_id:
            raise ContractError("shadow invocation must equal its deterministic cycle UUID")
        try:
            parsed_invocation = UUID(invocation_key)
        except ValueError as exc:
            raise ContractError("selector invocation key must be a UUID") from exc
        if str(parsed_invocation) != invocation_key:
            raise ContractError("selector invocation key must be canonical")
        local_date_raw = raw["local_date"]
        if isinstance(local_date_raw, date):
            local_date = local_date_raw.isoformat()
        elif isinstance(local_date_raw, str):
            local_date = local_date_raw
        else:
            raise ContractError("selector local date is malformed")
        try:
            parsed_local_date = date.fromisoformat(local_date)
        except ValueError as exc:
            raise ContractError("selector local date must be canonical") from exc
        if parsed_local_date.isoformat() != local_date:
            raise ContractError("selector local date must be canonical")
        status = raw["context_status"]
        if status not in ("frozen", "unavailable"):
            raise ContractError("selector context status is invalid")
        payload_raw = raw["context_payload"]
        if isinstance(payload_raw, str):
            try:
                payload = json.loads(payload_raw)
            except json.JSONDecodeError as exc:
                raise ContractError("selector context payload is invalid JSON") from exc
        elif isinstance(payload_raw, Mapping):
            payload = dict(payload_raw)
        else:
            raise ContractError("selector context payload is malformed")
        if not isinstance(payload, dict):
            raise ContractError("selector context payload must be an object")
        bytes_raw = raw["context_canonical_bytes"]
        if not isinstance(bytes_raw, (bytes, bytearray, memoryview)):
            raise ContractError("selector context bytes are malformed")
        context_bytes = bytes(bytes_raw)
        hashes = {
            field: require_sha256(raw[field], field)
            for field in (
                "context_sha256",
                "source_bundle_sha256",
                "context_schema_sha256",
                "selector_identity_sha256",
                "selector_artifact_sha256",
            )
        }
        if hashlib.sha256(context_bytes).hexdigest() != hashes["context_sha256"]:
            raise ContractError("selector context byte/hash binding mismatch")
        times: dict[str, datetime] = {}
        for field in ("context_cutoff_at", "boundary_at", "resolved_at"):
            value = raw[field]
            if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
                raise ContractError(f"selector {field} must be timezone-aware")
            times[field] = value.astimezone(UTC)
        if not times["context_cutoff_at"] < times["boundary_at"] or not (
            times["context_cutoff_at"] <= times["resolved_at"] < times["boundary_at"]
        ):
            raise ContractError("selector cycle is outside its server cutoff window")
        failure_reason = raw["failure_reason"]
        if status == "frozen" and failure_reason is not None:
            raise ContractError("frozen selector context cannot have a failure reason")
        allowed_unavailable_reasons = {
            "source_relation_unavailable",
            "no_usable_precutoff_climate_source",
            "conflicting_latest_forecast_vintage",
        }
        if status == "unavailable" and (
            not isinstance(failure_reason, str) or failure_reason not in allowed_unavailable_reasons
        ):
            raise ContractError("unavailable selector context needs a locked failure code")
        if status == "unavailable":
            unavailable = parse_hash_bound_document(
                context_bytes,
                hashes["context_sha256"],
                expected_payload=payload,
            )
            if unavailable != {
                "schema": "verdify-selector-context-unavailable-v1",
                "local_date": local_date,
                "context_cutoff_at": times["context_cutoff_at"].strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                "boundary_at": times["boundary_at"].strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                "reason": failure_reason,
            }:
                raise ContractError("unavailable selector receipt/cycle binding mismatch")
        return cls(
            cycle_kind=kind,
            subject_id=subject_id,
            assignment_id=assignment_id,
            work_id=work_id,
            study_id=study_id,
            local_date=local_date,
            invocation_key=invocation_key,
            context_status=status,
            context_payload=payload,
            context_canonical_bytes=context_bytes,
            failure_reason=failure_reason,
            **hashes,
            **times,
        )


@dataclass(frozen=True)
class SelectorDecision:
    profile: str
    fallback_reason: str | None
    raw_request_sha256: str
    raw_response_sha256: str | None
    attempt_receipt_sha256: tuple[str, ...]


class SelectorStore(Protocol):
    async def selector_cycle(self, experiment_id: str) -> SelectorCandidate | None: ...

    async def record_selector_choice(
        self,
        experiment_id: str,
        candidate: SelectorCandidate,
        decision: SelectorDecision,
    ) -> Mapping[str, Any]: ...


def load_identity(path: Path | None, expected_sha256: str) -> SelectorIdentity | None:
    if path is None:
        return None
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if len(raw) > 65_536:
        return None
    try:
        return SelectorIdentity.parse(raw, expected_sha256)
    except ContractError:
        return None


def _fallback_decision(candidate: SelectorCandidate, reason: str) -> SelectorDecision:
    if candidate.context_status == "unavailable":
        # SQL explicitly binds this no-provider path to the immutable context
        # document itself. No fabricated provider request is claimed.
        request_hash = candidate.context_sha256
    else:
        request_hash = hashlib.sha256(
            canonical_json_bytes(
                {
                    "context_sha256": candidate.context_sha256,
                    "identity_sha256": candidate.selector_identity_sha256,
                    "invocation_key": candidate.invocation_key,
                    "local_date": candidate.local_date,
                    "reason": reason,
                    "schema": "verdify-selector-local-fallback-v2",
                    "study_id": candidate.study_id,
                }
            )
        ).hexdigest()
    receipt = hashlib.sha256(
        canonical_json_bytes(
            {
                "attempt": 1,
                "request_sha256": request_hash,
                "result": reason,
                "schema": "verdify-selector-attempt-receipt-v2",
            }
        )
    ).hexdigest()
    return SelectorDecision("baseline", reason, request_hash, None, (receipt,))


async def run_selector_cycle(
    store: SelectorStore,
    *,
    experiment_id: str,
    provider: SelectorProviderAdapter,
    identity_path: Path | None,
    identity_loader: Callable[[Path | None, str], SelectorIdentity | None] = load_identity,
) -> Literal["idle", "selected", "fallback"]:
    candidate = await store.selector_cycle(experiment_id)
    if candidate is None:
        return "idle"
    if candidate.context_status == "unavailable":
        decision = _fallback_decision(candidate, candidate.failure_reason or "source_unavailable")
    else:
        try:
            context = SelectorContext.parse(
                candidate.context_canonical_bytes,
                candidate.context_sha256,
                expected_payload=candidate.context_payload,
            )
            if (
                context.local_date != candidate.local_date
                or context.context_cutoff_at != candidate.context_cutoff_at
                or context.boundary_at != candidate.boundary_at
            ):
                raise ContractError("selector context/cycle timing mismatch")
            identity = identity_loader(identity_path, candidate.selector_identity_sha256)
            if identity is None or identity.context_schema_sha256 != candidate.context_schema_sha256:
                raise ContractError("selector identity/context schema binding unavailable")
            attempted: SelectorAttemptResult = await provider.select(
                study_id=candidate.study_id,
                local_date=candidate.local_date,
                invocation_key=candidate.invocation_key,
                context=context,
                identity=identity,
            )
            decision = SelectorDecision(
                attempted.profile,
                attempted.fallback_reason,
                attempted.raw_request_sha256,
                attempted.raw_response_sha256,
                attempted.attempt_receipt_sha256,
            )
        except ContractError:
            decision = _fallback_decision(candidate, "identity_or_context_invalid")
    persisted = await store.record_selector_choice(experiment_id, candidate, decision)
    persisted_fallback = persisted.get("fallback_reason")
    return "selected" if decision.fallback_reason is None and persisted_fallback is None else "fallback"


class OutcomeStore(Protocol):
    async def outcome_source_cycle(self, experiment_id: str) -> OutcomeSourceCandidate | None: ...

    async def record_outcome(
        self,
        experiment_id: str,
        candidate: OutcomeSourceCandidate,
        outcome: OutcomePayload,
    ) -> Mapping[str, Any]: ...


async def run_outcome_cycle(
    store: OutcomeStore,
    *,
    experiment_id: str,
    identity_path: Path | None,
) -> Literal["idle", "frozen", "frozen_missing"]:
    candidate = await store.outcome_source_cycle(experiment_id)
    if candidate is None:
        return "idle"
    outcome = evaluate_outcome(candidate, identity_path=identity_path)
    await store.record_outcome(experiment_id, candidate, outcome)
    endpoints = (
        outcome.temperature_corridor_distance_f,
        outcome.vpd_corridor_distance_kpa,
        outcome.nine_control_state_minutes,
    )
    return "frozen" if all(value is not None for value in endpoints) else "frozen_missing"
