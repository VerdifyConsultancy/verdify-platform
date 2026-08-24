from __future__ import annotations

import hashlib
import ipaddress
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from experiment_orchestrator.contracts import (  # noqa: E402
    CLIMATE_SOURCE_SCHEMA,
    CLIMATE_VALUE_FIELDS,
    CONTEXT_SCHEMA,
    SELECTOR_IDENTITY_SCHEMA,
    SelectorIdentity,
    canonical_json_bytes,
)
from experiment_orchestrator.provider import SelectorProviderAdapter  # noqa: E402
from experiment_orchestrator.service import (  # noqa: E402
    SelectorCandidate,
    run_selector_cycle,
)
from experiment_orchestrator.settings import ProviderSettings  # noqa: E402

EXPERIMENT = "11111111-1111-4111-8111-111111111111"
SUBJECT = "22222222-2222-4222-8222-222222222222"
CUTOFF = datetime(2026, 8, 24, 23, 45, tzinfo=UTC)
BOUNDARY = datetime(2026, 8, 25, 0, 0, tzinfo=UTC)


def context_payload() -> dict:
    values = dict.fromkeys(CLIMATE_VALUE_FIELDS)
    values["temp_avg_f"] = 80.0
    values["vpd_avg_kpa"] = 1.4
    observed = "2026-08-24T23:40:00.000000Z"
    # SQL owns row-hash verification; use a structurally valid hash sentinel.
    return {
        "schema": CONTEXT_SCHEMA,
        "local_date": "2026-08-25",
        "context_cutoff_at": "2026-08-24T23:45:00.000000Z",
        "boundary_at": "2026-08-25T00:00:00.000000Z",
        "climate_observations": [
            {
                "schema": CLIMATE_SOURCE_SCHEMA,
                "observed_at": observed,
                "source_row_sha256": "a" * 64,
                "values": values,
            }
        ],
        "forecast_vintage": [],
    }


def candidate_row(*, kind: str = "shadow", status: str = "frozen", failure_reason=None) -> dict:
    payload = context_payload()
    if status == "unavailable":
        payload = {
            "schema": "verdify-selector-context-unavailable-v1",
            "local_date": "2026-08-25",
            "context_cutoff_at": "2026-08-24T23:45:00.000000Z",
            "boundary_at": "2026-08-25T00:00:00.000000Z",
            "reason": failure_reason,
        }
    # Exercise DB-owned jsonb spelling rather than Python reserialization.
    raw = json.dumps(payload, sort_keys=True, separators=(", ", ": ")).encode()
    return {
        "cycle_kind": kind,
        "subject_id": SUBJECT,
        "assignment_id": SUBJECT if kind == "randomized" else None,
        "work_id": None if kind == "randomized" else SUBJECT,
        "study_id": "study-v2",
        "local_date": date(2026, 8, 25),
        "invocation_key": SUBJECT,
        "context_status": status,
        "context_payload": payload,
        "context_canonical_bytes": raw,
        "context_sha256": hashlib.sha256(raw).hexdigest(),
        "source_bundle_sha256": "b" * 64,
        "context_schema_sha256": "c" * 64,
        "selector_identity_sha256": "d" * 64,
        "selector_artifact_sha256": "e" * 64,
        "context_cutoff_at": CUTOFF,
        "boundary_at": BOUNDARY,
        "resolved_at": datetime(2026, 8, 24, 23, 46, tzinfo=UTC),
        "failure_reason": failure_reason,
    }


def identity(expected_hash: str, context_schema_sha256: str = "c" * 64) -> SelectorIdentity:
    payload = {
        "schema": SELECTOR_IDENTITY_SCHEMA,
        "provider": "provider",
        "model_identifier": "model-fixed",
        "model_revision": "r1",
        "expected_system_fingerprint": "fp1",
        "prompt_sha256": "1" * 64,
        "system_message_sha256": "2" * 64,
        "messages_sha256": "3" * 64,
        "decoding_parameters_sha256": "4" * 64,
        "tool_contract_revision": "tools-v2",
        "response_schema_revision": "response-v2",
        "context_schema_sha256": context_schema_sha256,
        "lesson_snapshot_sha256": "6" * 64,
        "runtime_environment_sha256": "7" * 64,
        "timeout_milliseconds": 1000,
        "max_attempts": 1,
    }
    raw = canonical_json_bytes(payload)
    # Tests inject the already-validated object; candidate's expected digest is
    # independently checked by the real file loader.
    return SelectorIdentity.parse(raw, hashlib.sha256(raw).hexdigest())


class Store:
    def __init__(self, candidate: SelectorCandidate | None) -> None:
        self.candidate = candidate
        self.recorded = []

    async def selector_cycle(self, experiment_id: str):
        assert experiment_id == EXPERIMENT
        return self.candidate

    async def record_selector_choice(self, experiment_id, candidate, decision):
        self.recorded.append((experiment_id, candidate, decision))
        return {"recorded": True}


class Transport:
    async def post(self, _endpoint: str, **_kwargs):
        return canonical_json_bytes(
            {
                "schema": "verdify-selector-response-v2",
                "profile": "aggressive",
                "provider": "provider",
                "model_identifier": "model-fixed",
                "model_revision": "r1",
                "system_fingerprint": "fp1",
            }
        )


async def resolver(_host: str, _port: int):
    return frozenset({ipaddress.ip_address("8.8.8.8")})


def provider() -> SelectorProviderAdapter:
    settings = ProviderSettings(
        "https://inference.example/select",
        "inference.example",
        443,
        ipaddress.ip_network("8.8.8.8/32"),
        "hidden",
    )
    return SelectorProviderAdapter(
        settings,
        transport=Transport(),
        resolver=resolver,
        clock=lambda: datetime(2026, 8, 24, 23, 50, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_selector_cycle_uses_one_frozen_context_and_persists_choice() -> None:
    candidate = SelectorCandidate.from_row(candidate_row())
    store = Store(candidate)
    disposition = await run_selector_cycle(
        store,
        experiment_id=EXPERIMENT,
        provider=provider(),
        identity_path=None,
        identity_loader=lambda _path, _expected: identity(_expected),
    )
    assert disposition == "selected"
    decision = store.recorded[0][2]
    assert decision.profile == "aggressive" and decision.fallback_reason is None


@pytest.mark.asyncio
async def test_unavailable_context_never_calls_provider_and_binds_context_hash() -> None:
    candidate = SelectorCandidate.from_row(
        candidate_row(
            status="unavailable",
            failure_reason="no_usable_precutoff_climate_source",
        )
    )

    class ExplodingProvider:
        async def select(self, **_kwargs):
            raise AssertionError("provider must not be called")

    store = Store(candidate)
    disposition = await run_selector_cycle(
        store,
        experiment_id=EXPERIMENT,
        provider=ExplodingProvider(),  # type: ignore[arg-type]
        identity_path=None,
    )
    decision = store.recorded[0][2]
    assert disposition == "fallback"
    assert decision.profile == "baseline"
    assert decision.fallback_reason == "no_usable_precutoff_climate_source"
    assert decision.raw_request_sha256 == candidate.context_sha256
    assert decision.raw_response_sha256 is None


@pytest.mark.asyncio
async def test_identity_hash_or_context_schema_failure_is_baseline_not_source_fabrication() -> None:
    candidate = SelectorCandidate.from_row(candidate_row())
    store = Store(candidate)
    disposition = await run_selector_cycle(
        store,
        experiment_id=EXPERIMENT,
        provider=provider(),
        identity_path=None,
        identity_loader=lambda _path, _expected: identity(_expected, context_schema_sha256="f" * 64),
    )
    assert disposition == "fallback"
    decision = store.recorded[0][2]
    assert (decision.profile, decision.fallback_reason) == ("baseline", "identity_or_context_invalid")
    assert decision.raw_response_sha256 is None


def test_candidate_rejects_cross_kind_ids_and_outside_server_window() -> None:
    row = candidate_row()
    row["assignment_id"] = SUBJECT
    with pytest.raises(Exception, match="binding mismatch"):
        SelectorCandidate.from_row(row)
    row = candidate_row()
    row["resolved_at"] = BOUNDARY
    with pytest.raises(Exception, match="outside"):
        SelectorCandidate.from_row(row)
