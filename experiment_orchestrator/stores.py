"""Concrete function-only stores for the production orchestrator duties."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

import asyncpg

from .contracts import (
    VALID_PROFILES,
    LifecyclePlan,
    OutcomePayload,
    canonical_json_bytes,
    require_sha256,
)
from .database import AttestedPool, record_to_mapping
from .outcome import OutcomeSourceCandidate
from .service import SelectorCandidate, SelectorDecision

ACTOR = "experiment-v2-orchestrator"
DIRECT_LAUNCH_EXPERIMENT_ID = "45039c86-c1d9-52f6-a0a9-d94a17bc4b14"

_SELECTOR_FALLBACK_REASONS = frozenset(
    {
        "source_relation_unavailable",
        "no_usable_precutoff_climate_source",
        "conflicting_latest_forecast_vintage",
        "provider_unconfigured",
        "provider_unavailable",
        "request_exceeds_context_budget",
        "timeout",
        "invalid_response",
        "late",
        "identity_or_context_invalid",
        "boundary_elapsed_before_choice_persist",
    }
)


class StoreContractError(RuntimeError):
    """An attested DB function returned no durable result for a mutation."""


class SelectorFunctionStore:
    def __init__(self, pool: AttestedPool) -> None:
        self._pool = pool

    async def selector_cycle(self, experiment_id: str) -> SelectorCandidate | None:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT * FROM public.fn_experiment_v2_selector_cycle($1::uuid)",
                experiment_id,
            )
        detached = record_to_mapping(row)
        return None if detached is None else SelectorCandidate.from_row(detached)

    async def record_selector_choice(
        self,
        experiment_id: str,
        candidate: SelectorCandidate,
        decision: SelectorDecision,
    ) -> Mapping[str, Any]:
        if decision.profile not in VALID_PROFILES:
            raise StoreContractError("selector decision profile is outside the locked set")
        if decision.fallback_reason is not None and decision.fallback_reason not in _SELECTOR_FALLBACK_REASONS:
            raise StoreContractError("selector fallback reason is outside the locked set")
        if decision.fallback_reason is not None and decision.profile != "baseline":
            raise StoreContractError("selector fallback must persist baseline")
        require_sha256(decision.raw_request_sha256, "raw_request_sha256")
        if decision.raw_response_sha256 is not None:
            require_sha256(decision.raw_response_sha256, "raw_response_sha256")
        if not 1 <= len(decision.attempt_receipt_sha256) <= 3:
            raise StoreContractError("selector attempt ledger must contain one to three receipts")
        for receipt in decision.attempt_receipt_sha256:
            require_sha256(receipt, "attempt_receipt_sha256")
        try:
            return await self._persist_selector_choice(experiment_id, candidate, decision)
        except asyncpg.PostgresError as exc:
            if exc.sqlstate != "V2B01":
                raise
            # The function raises V2B01 only for the DB-clock race where a
            # context was frozen before the boundary but the first persistence
            # round trip crossed it. Retry the exact SQL-locked baseline
            # closure; no response or provider request is claimed.
            result = "boundary_elapsed_before_choice_persist"
            request_hash = candidate.context_sha256
            receipt = hashlib.sha256(
                canonical_json_bytes(
                    {
                        "attempt": 1,
                        "request_sha256": request_hash,
                        "result": result,
                        "schema": "verdify-selector-attempt-receipt-v2",
                    }
                )
            ).hexdigest()
            closure = SelectorDecision("baseline", result, request_hash, None, (receipt,))
            return await self._persist_selector_choice(experiment_id, candidate, closure)

    async def _persist_selector_choice(
        self,
        experiment_id: str,
        candidate: SelectorCandidate,
        decision: SelectorDecision,
    ) -> Mapping[str, Any]:
        function = (
            "fn_experiment_v2_record_shadow_choice"
            if candidate.cycle_kind == "shadow"
            else "fn_experiment_v2_record_selector_choice"
        )
        query = f"""SELECT * FROM public.{function}(
            $1::uuid,$2::uuid,$3::text,$4::text,$5::text,$6::text,
            $7::text,$8::text,$9::text[],$10::text,$11::text)"""
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                query,
                experiment_id,
                candidate.subject_id,
                candidate.invocation_key,
                candidate.invocation_key,
                decision.profile,
                decision.fallback_reason,
                decision.raw_request_sha256,
                decision.raw_response_sha256,
                list(decision.attempt_receipt_sha256),
                candidate.selector_artifact_sha256,
                ACTOR,
            )
        detached = record_to_mapping(row)
        if detached is None:
            raise StoreContractError("selector choice function returned no durable row")
        return detached


class LifecycleFunctionStore:
    def __init__(self, pool: AttestedPool) -> None:
        self._pool = pool

    async def schedule_shadow_cycle(self, plan: LifecyclePlan) -> Mapping[str, Any]:
        if plan.action != "shadow_schedule":
            raise StoreContractError("shadow schedule store received a boundary plan")
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """SELECT * FROM public.fn_experiment_v2_schedule_shadow_cycle(
                    $1::uuid,$2::date,$3::timestamptz,$4::text,$5::text,
                    $6::text,$7::text,$8::text,$9::text)""",
                plan.experiment_id,
                plan.local_date,
                plan.context_cutoff_at,
                plan.context_schema_sha256,
                plan.selector_identity_sha256,
                plan.selector_artifact_sha256,
                plan.endpoint_artifact_sha256,
                plan.outcome_schema_sha256,
                ACTOR,
            )
        detached = record_to_mapping(row)
        if detached is None:
            raise StoreContractError("shadow scheduling function returned no durable row")
        return detached

    async def boundary_cycle(self, experiment_id: str) -> Mapping[str, Any] | None:
        function = (
            "fn_experiment_v2_direct_launch_cycle"
            if experiment_id == DIRECT_LAUNCH_EXPERIMENT_ID
            else "fn_experiment_v2_boundary_cycle"
        )
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                f"SELECT * FROM public.{function}($1::uuid,$2::text)",
                experiment_id,
                ACTOR,
            )
        return record_to_mapping(row)


class OutcomeFunctionStore:
    """One-row source resolution and one immutable outcome write."""

    def __init__(self, pool: AttestedPool) -> None:
        self._pool = pool

    async def outcome_source_cycle(self, experiment_id: str) -> OutcomeSourceCandidate | None:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT * FROM public.fn_experiment_v2_outcome_source_cycle($1::uuid)",
                experiment_id,
            )
        detached = record_to_mapping(row)
        return None if detached is None else OutcomeSourceCandidate.from_row(detached)

    async def record_outcome(
        self,
        experiment_id: str,
        candidate: OutcomeSourceCandidate,
        outcome: OutcomePayload,
    ) -> Mapping[str, Any]:
        if outcome.source_bundle_sha256 != candidate.source_bundle_sha256:
            raise StoreContractError("outcome/source bundle binding differs")
        # asyncpg's built-in jsonb codec accepts JSON text, not arbitrary
        # Python mappings. Send one deterministic finite representation; the
        # database owns the final jsonb hash and immutable retry check.
        payload_json = canonical_json_bytes(outcome.as_mapping()).decode("utf-8")
        if candidate.source_kind == "shadow":
            query = """SELECT * FROM public.fn_experiment_v2_record_shadow_outcome_preview(
                $1::uuid,$2::uuid,$3::jsonb,$4::text)"""
            arguments: tuple[object, ...] = (
                experiment_id,
                candidate.subject_id,
                payload_json,
                ACTOR,
            )
        else:
            endpoints = (
                outcome.temperature_corridor_distance_f,
                outcome.vpd_corridor_distance_kpa,
                outcome.nine_control_state_minutes,
            )
            zero_value_retained = any(value == 0 for value in endpoints if value is not None)
            null_value_retained = any(value is None for value in endpoints)
            query = """SELECT * FROM public.fn_experiment_v2_freeze_outcome(
                $1::uuid,$2::uuid,$3::jsonb,$4::boolean,$5::boolean,$6::boolean,
                $7::boolean,$8::boolean,$9::text)"""
            arguments = (
                experiment_id,
                candidate.subject_id,
                payload_json,
                candidate.delivery_failed,
                candidate.fallback_used,
                candidate.facility_rescue,
                zero_value_retained,
                null_value_retained,
                ACTOR,
            )
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(query, *arguments)
        detached = record_to_mapping(row)
        if detached is None:
            raise StoreContractError("outcome freeze function returned no durable row")
        return detached
