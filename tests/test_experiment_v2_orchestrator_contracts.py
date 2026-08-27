from __future__ import annotations

import hashlib
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from experiment_orchestrator.contracts import (
    CLIMATE_SOURCE_SCHEMA,
    CLIMATE_VALUE_FIELDS,
    CONTEXT_SCHEMA,
    FORECAST_SOURCE_SCHEMA,
    FORECAST_VALUE_FIELDS,
    MAX_CLIMATE_OBSERVATIONS,
    MAX_SELECTOR_CONTEXT_BYTES,
    SELECTOR_IDENTITY_SCHEMA,
    ContractError,
    LifecyclePlan,
    OutcomePayload,
    ProviderResponse,
    SelectorContext,
    SelectorIdentity,
    canonical_json_bytes,
    canonical_sha256,
)

ZERO = "0" * 64
CUTOFF = "2026-08-24T23:45:00.000000Z"
BOUNDARY = "2026-08-25T00:00:00.000000Z"


def _source_hash(schema: str, timestamps: dict[str, str], values: dict[str, float | None]) -> str:
    return canonical_sha256(
        {"schema": schema, **timestamps, "values": values},
        domain="verdify-experiment-v2-selector-source-v1",
    )


def climate_row(*, observed_at: str = CUTOFF) -> dict:
    values = dict.fromkeys(CLIMATE_VALUE_FIELDS)
    values["temp_avg_f"] = 79.2
    values["vpd_avg_kpa"] = 1.37
    return {
        "schema": CLIMATE_SOURCE_SCHEMA,
        "observed_at": observed_at,
        "source_row_sha256": _source_hash(CLIMATE_SOURCE_SCHEMA, {"observed_at": observed_at}, values),
        "values": values,
    }


def forecast_row() -> dict:
    values = dict.fromkeys(FORECAST_VALUE_FIELDS)
    values["temp_f"] = 84.0
    values["vpd_kpa"] = 1.6
    valid_at = "2026-08-25T01:00:00.000000Z"
    fetched_at = "2026-08-24T23:30:00.000000Z"
    return {
        "schema": FORECAST_SOURCE_SCHEMA,
        "valid_at": valid_at,
        "fetched_at": fetched_at,
        "source_row_sha256": _source_hash(
            FORECAST_SOURCE_SCHEMA,
            {"fetched_at": fetched_at, "valid_at": valid_at},
            values,
        ),
        "values": values,
    }


def context_payload() -> dict:
    return {
        "schema": CONTEXT_SCHEMA,
        "local_date": "2026-08-25",
        "context_cutoff_at": CUTOFF,
        "boundary_at": BOUNDARY,
        "climate_observations": [climate_row()],
        "forecast_vintage": [forecast_row()],
    }


def parse_context(payload: dict | None = None) -> SelectorContext:
    raw = canonical_json_bytes(payload or context_payload())
    return SelectorContext.parse(raw, hashlib.sha256(raw).hexdigest())


def identity_payload() -> dict:
    return {
        "schema": SELECTOR_IDENTITY_SCHEMA,
        "provider": "frozen-provider",
        "model_identifier": "model-2026-08-01",
        "model_revision": "revision-1",
        "expected_system_fingerprint": "fp-1",
        "prompt_sha256": "1" * 64,
        "system_message_sha256": "2" * 64,
        "messages_sha256": "3" * 64,
        "decoding_parameters_sha256": "4" * 64,
        "tool_contract_revision": "tools-v2",
        "response_schema_revision": "response-v2",
        "context_schema_sha256": "5" * 64,
        "lesson_snapshot_sha256": "6" * 64,
        "runtime_environment_sha256": "7" * 64,
        "timeout_milliseconds": 10_000,
        "max_attempts": 2,
    }


def parse_identity(payload: dict | None = None) -> SelectorIdentity:
    raw = canonical_json_bytes(payload or identity_payload())
    return SelectorIdentity.parse(raw, hashlib.sha256(raw).hexdigest())


def test_context_accepts_only_canonical_source_bound_pre_cutoff_rows() -> None:
    parsed = parse_context()
    assert parsed.local_date == "2026-08-25"
    assert parsed.climate_observations[0].values["temp_avg_f"] == 79.2
    assert parsed.forecast_vintage[0].values["vpd_kpa"] == 1.6


@pytest.mark.parametrize(
    "forbidden",
    ["physical_arm", "mapping", "apiKey", "nested_secret", "post-cutoff", "credential_token"],
)
def test_context_rejects_forbidden_fields_at_any_depth(forbidden: str) -> None:
    payload = context_payload()
    payload["climate_observations"][0]["values"][forbidden] = 1
    with pytest.raises(ContractError, match="forbidden field"):
        canonical_json_bytes(payload)


def test_context_rejects_post_cutoff_source_even_with_a_valid_hash() -> None:
    payload = context_payload()
    payload["climate_observations"] = [climate_row(observed_at="2026-08-24T23:45:00.000001Z")]
    raw = canonical_json_bytes(payload)
    with pytest.raises(ContractError, match="post-cutoff"):
        SelectorContext.parse(raw, hashlib.sha256(raw).hexdigest())


def test_context_rejects_malformed_source_hash_and_wrong_bound_hash() -> None:
    payload = context_payload()
    payload["climate_observations"][0]["source_row_sha256"] = "not-a-hash"
    raw = canonical_json_bytes(payload)
    with pytest.raises(ContractError, match="lower-case SHA-256"):
        SelectorContext.parse(raw, hashlib.sha256(raw).hexdigest())

    valid = canonical_json_bytes(context_payload())
    with pytest.raises(ContractError, match="hash mismatch"):
        SelectorContext.parse(valid, ZERO)


def test_context_accepts_exact_database_owned_jsonb_spelling_without_reserializing() -> None:
    payload = context_payload()
    compact = canonical_json_bytes(payload)
    database_bytes = compact.replace(b'":', b'": ').replace(b',"', b', "')
    parsed = SelectorContext.parse(
        database_bytes,
        hashlib.sha256(database_bytes).hexdigest(),
        expected_payload=payload,
    )
    assert parsed.canonical_bytes == database_bytes


def test_context_rejects_missing_real_climate_without_fabricating_defaults() -> None:
    payload = context_payload()
    payload["climate_observations"] = []
    raw = canonical_json_bytes(payload)
    with pytest.raises(ContractError, match="usable real climate"):
        SelectorContext.parse(raw, hashlib.sha256(raw).hexdigest())


def test_context_rejects_unbounded_or_overflowing_source_data() -> None:
    oversized = b"{" + b" " * MAX_SELECTOR_CONTEXT_BYTES + b"}"
    with pytest.raises(ContractError, match="byte bound"):
        SelectorContext.parse(oversized, hashlib.sha256(oversized).hexdigest())

    payload = context_payload()
    payload["climate_observations"] = [climate_row()] * (MAX_CLIMATE_OBSERVATIONS + 1)
    raw = canonical_json_bytes(payload)
    with pytest.raises(ContractError, match="48-row climate bound"):
        SelectorContext.parse(raw, hashlib.sha256(raw).hexdigest())

    payload = context_payload()
    payload["climate_observations"][0]["values"]["temp_avg_f"] = 10**1000
    raw = canonical_json_bytes(payload)
    with pytest.raises(ContractError, match="finite number"):
        SelectorContext.parse(raw, hashlib.sha256(raw).hexdigest())


def test_identity_is_canonical_hash_bound_and_bounded() -> None:
    identity = parse_identity()
    assert identity.timeout_milliseconds == 10_000
    payload = identity_payload()
    payload["max_attempts"] = 4
    raw = canonical_json_bytes(payload)
    with pytest.raises(ContractError, match="max_attempts"):
        SelectorIdentity.parse(raw, hashlib.sha256(raw).hexdigest())


def test_provider_response_rejects_extra_or_identity_mismatched_fields() -> None:
    identity = parse_identity()
    response = {
        "schema": "verdify-selector-response-v2",
        "profile": "moderate",
        "provider": identity.provider,
        "model_identifier": identity.model_identifier,
        "model_revision": identity.model_revision,
        "system_fingerprint": identity.expected_system_fingerprint,
    }
    raw = canonical_json_bytes(response)
    parsed = ProviderResponse.parse(raw, identity, datetime(2026, 8, 24, 23, 50, tzinfo=UTC))
    assert parsed.profile == "moderate"

    response["mapping"] = "X=A"
    with pytest.raises(ContractError, match="forbidden field"):
        canonical_json_bytes(response)

    response.pop("mapping")
    response["model_revision"] = "floating"
    raw = canonical_json_bytes(response)
    with pytest.raises(ContractError, match="identity mismatch"):
        ProviderResponse.parse(raw, identity, datetime.now(UTC))


def test_missing_outcome_is_explicit_null_and_source_bound() -> None:
    outcome = OutcomePayload.missing(
        source_bundle_sha256="a" * 64,
        climate_reason="source_unavailable",
        equipment_reason="counter_samples_unavailable",
    )
    assert outcome.temperature_corridor_distance_f is None
    assert outcome.nine_control_state_minutes is None
    assert len(outcome.canonical_sha256) == 64


@pytest.mark.parametrize(
    "equipment_reason",
    ["direct_state_snapshot_unavailable", "direct_state_snapshot_invalid"],
)
def test_missing_outcome_distinguishes_direct_state_seed_failures(equipment_reason: str) -> None:
    outcome = OutcomePayload.missing(
        source_bundle_sha256="a" * 64,
        climate_reason="climate_completeness",
        equipment_reason=equipment_reason,
    )
    assert outcome.equipment_missing_reason == equipment_reason
    assert outcome.nine_control_state_minutes is None


def test_outcome_missing_codes_are_endpoint_specific() -> None:
    with pytest.raises(ContractError, match="temperature corridor"):
        OutcomePayload.missing(
            source_bundle_sha256="a" * 64,
            climate_reason="counter_samples_unavailable",
            equipment_reason="counter_samples_unavailable",
        )
    with pytest.raises(ContractError, match="nine-control-state"):
        OutcomePayload.missing(
            source_bundle_sha256="a" * 64,
            climate_reason="climate_completeness",
            equipment_reason="climate_completeness",
        )


def test_lifecycle_plan_is_canonical_experiment_bound_and_phase_exclusive() -> None:
    experiment_id = "11111111-1111-4111-8111-111111111111"
    payload = {
        "schema": "verdify-experiment-v2-lifecycle-plan-v1",
        "experiment_id": experiment_id,
        "action": "shadow_schedule",
        "local_date": "2026-08-26",
        "context_cutoff_at": "2026-08-25T23:45:00.000000Z",
        "context_schema_sha256": "1" * 64,
        "selector_identity_sha256": "2" * 64,
        "selector_artifact_sha256": "3" * 64,
        "endpoint_artifact_sha256": "4" * 64,
        "outcome_schema_sha256": "5" * 64,
    }
    raw = canonical_json_bytes(payload, reject_forbidden_fields=False)
    parsed = LifecyclePlan.parse(raw, hashlib.sha256(raw).hexdigest(), experiment_id)
    assert parsed.action == "shadow_schedule"
    assert parsed.local_date == "2026-08-26"

    payload["mapping"] = "forbidden-extra"
    raw = canonical_json_bytes(payload, reject_forbidden_fields=False)
    with pytest.raises(ContractError, match="exactly"):
        LifecyclePlan.parse(raw, hashlib.sha256(raw).hexdigest(), experiment_id)

    boundary_payload = {
        "schema": "verdify-experiment-v2-lifecycle-plan-v1",
        "experiment_id": experiment_id,
        "action": "boundary",
    }
    raw = canonical_json_bytes(boundary_payload)
    boundary = LifecyclePlan.parse(raw, hashlib.sha256(raw).hexdigest(), experiment_id)
    assert boundary.action == "boundary" and boundary.local_date is None
