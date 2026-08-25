from __future__ import annotations

import hashlib
import ipaddress
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from experiment_orchestrator.contracts import (  # noqa: E402
    CLIMATE_SOURCE_SCHEMA,
    CLIMATE_VALUE_FIELDS,
    CONTEXT_SCHEMA,
    FORECAST_VALUE_FIELDS,
    SELECTOR_IDENTITY_SCHEMA,
    SelectorContext,
    SelectorIdentity,
    canonical_json_bytes,
    canonical_sha256,
)
from experiment_orchestrator.provider import SelectorProviderAdapter  # noqa: E402
from experiment_orchestrator.settings import (  # noqa: E402
    ConfigurationError,
    ProviderSettings,
    load_settings,
)


def _context() -> SelectorContext:
    observed = "2026-08-24T23:40:00.000000Z"
    climate_values = dict.fromkeys(CLIMATE_VALUE_FIELDS)
    climate_values["temp_avg_f"] = 80.0
    climate_values["vpd_avg_kpa"] = 1.4
    climate_hash = canonical_sha256(
        {"schema": CLIMATE_SOURCE_SCHEMA, "observed_at": observed, "values": climate_values},
        domain="verdify-experiment-v2-selector-source-v1",
    )
    payload = {
        "schema": CONTEXT_SCHEMA,
        "local_date": "2026-08-25",
        "context_cutoff_at": "2026-08-24T23:45:00.000000Z",
        "boundary_at": "2026-08-25T00:00:00.000000Z",
        "climate_observations": [
            {
                "schema": CLIMATE_SOURCE_SCHEMA,
                "observed_at": observed,
                "source_row_sha256": climate_hash,
                "values": climate_values,
            }
        ],
        "forecast_vintage": [],
    }
    raw = canonical_json_bytes(payload)
    return SelectorContext.parse(raw, hashlib.sha256(raw).hexdigest())


def _identity() -> SelectorIdentity:
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
        "context_schema_sha256": "5" * 64,
        "lesson_snapshot_sha256": "6" * 64,
        "runtime_environment_sha256": "7" * 64,
        "timeout_milliseconds": 1000,
        "max_attempts": 2,
    }
    raw = canonical_json_bytes(payload)
    return SelectorIdentity.parse(raw, hashlib.sha256(raw).hexdigest())


class Transport:
    def __init__(self, raw: bytes) -> None:
        self.raw = raw
        self.calls = []

    async def post(self, endpoint: str, **kwargs) -> bytes:
        self.calls.append((endpoint, kwargs))
        return self.raw


def _provider_response(**changes: str) -> bytes:
    payload = {
        "schema": "verdify-selector-response-v2",
        "profile": "moderate",
        "provider": "provider",
        "model_identifier": "model-fixed",
        "model_revision": "r1",
        "system_fingerprint": "fp1",
    }
    payload.update(changes)
    return canonical_json_bytes(payload)


def _provider_settings() -> ProviderSettings:
    return ProviderSettings(
        endpoint="https://inference.example/v2/select",
        endpoint_host="inference.example",
        endpoint_port=443,
        egress_network=ipaddress.ip_network("8.8.8.8/32"),
        api_key="not-logged",
    )


async def _resolver(_host: str, _port: int):
    return frozenset({ipaddress.ip_address("8.8.8.8")})


@pytest.mark.asyncio
async def test_provider_sends_identical_arm_free_request_and_accepts_exact_identity() -> None:
    transport = Transport(_provider_response())
    adapter = SelectorProviderAdapter(
        _provider_settings(),
        transport=transport,
        resolver=_resolver,
        clock=lambda: datetime(2026, 8, 24, 23, 50, tzinfo=UTC),
    )
    result = await adapter.select(
        study_id="study",
        local_date="2026-08-25",
        invocation_key="11111111-1111-4111-8111-111111111111",
        context=_context(),
        identity=_identity(),
    )
    assert result.profile == "moderate"
    assert result.fallback_reason is None
    body = transport.calls[0][1]["body"]
    assert b"physical_arm" not in body and b"mapping" not in body and b"secret" not in body
    assert transport.calls[0][1]["headers"]["authorization"] == "Bearer not-logged"


@pytest.mark.asyncio
async def test_provider_unconfigured_and_dns_drift_fall_back_to_baseline() -> None:
    unconfigured = await SelectorProviderAdapter(None).select(
        study_id="study",
        local_date="2026-08-25",
        invocation_key="11111111-1111-4111-8111-111111111111",
        context=_context(),
        identity=_identity(),
    )
    assert (unconfigured.profile, unconfigured.fallback_reason) == ("baseline", "provider_unconfigured")

    async def wrong_dns(_host: str, _port: int):
        return frozenset({ipaddress.ip_address("1.1.1.1")})

    transport = Transport(_provider_response())
    adapter = SelectorProviderAdapter(_provider_settings(), transport=transport, resolver=wrong_dns)
    result = await adapter.select(
        study_id="study",
        local_date="2026-08-25",
        invocation_key="11111111-1111-4111-8111-111111111111",
        context=_context(),
        identity=_identity(),
    )
    assert result.profile == "baseline"
    assert result.fallback_reason == "provider_unavailable"
    assert transport.calls == []


@pytest.mark.asyncio
async def test_late_or_revision_mismatched_response_falls_back() -> None:
    late_adapter = SelectorProviderAdapter(
        _provider_settings(),
        transport=Transport(_provider_response()),
        resolver=_resolver,
        clock=lambda: datetime(2026, 8, 25, 0, 0, tzinfo=UTC),
    )
    late = await late_adapter.select(
        study_id="study",
        local_date="2026-08-25",
        invocation_key="11111111-1111-4111-8111-111111111111",
        context=_context(),
        identity=_identity(),
    )
    assert (late.profile, late.fallback_reason) == ("baseline", "late")

    mismatch_adapter = SelectorProviderAdapter(
        _provider_settings(),
        transport=Transport(_provider_response(model_revision="floating")),
        resolver=_resolver,
        clock=lambda: datetime(2026, 8, 24, 23, 50, tzinfo=UTC),
    )
    mismatch = await mismatch_adapter.select(
        study_id="study",
        local_date="2026-08-25",
        invocation_key="11111111-1111-4111-8111-111111111111",
        context=_context(),
        identity=_identity(),
    )
    assert (mismatch.profile, mismatch.fallback_reason) == ("baseline", "invalid_response")


def test_settings_are_off_by_default_and_require_distinct_complete_credentials() -> None:
    off = load_settings(
        {"VERDIFY_EXPERIMENT_V2_POLL_INTERVAL_SECONDS": "malformed-but-inert"},
        mode_override="lifecycle",
    )
    assert not off.runnable and off.inactive_reason == "capability_off"
    assert off.poll_interval_seconds == 15.0

    base = {
        "VERDIFY_COMPONENT_EXPERIMENT_ENABLED": "enabled",
        "VERDIFY_POLICY_VECTOR_MODE": "off",
        "VERDIFY_ACTIVE_EXPERIMENT_ID": "11111111-1111-4111-8111-111111111111",
        "DB_HOST": "verdify-db",
        "DB_NAME": "verdify",
        "DB_USER": "ordinary",
    }
    incomplete = {
        **base,
        "VERDIFY_EXPERIMENT_SHADOW_SCHEDULER_DB_USER": "scheduler-login",
    }
    with pytest.raises(ConfigurationError, match="incomplete"):
        load_settings(incomplete, mode_override="lifecycle")
    shared = {
        **base,
        "VERDIFY_EXPERIMENT_SHADOW_SCHEDULER_DB_USER": "ordinary",
        "VERDIFY_EXPERIMENT_SHADOW_SCHEDULER_DB_PASSWORD": "hidden",
    }
    with pytest.raises(ConfigurationError, match="differ"):
        load_settings(shared, mode_override="lifecycle")


def test_optional_named_database_secret_absence_is_safe_unready_configuration() -> None:
    settings = load_settings(
        {
            "VERDIFY_COMPONENT_EXPERIMENT_ENABLED": "enabled",
            "VERDIFY_POLICY_VECTOR_MODE": "off",
            "VERDIFY_ACTIVE_EXPERIMENT_ID": "11111111-1111-4111-8111-111111111111",
            "VERDIFY_EXPERIMENT_SHADOW_SCHEDULER_DB_USER": ("verdify_experiment_v2_shadow_scheduler_login"),
        },
        mode_override="lifecycle",
    )
    assert not settings.runnable
    assert settings.inactive_reason == "database_unconfigured"


def test_selector_endpoint_requires_exact_public_host_cidr_and_no_url_credentials() -> None:
    base = {
        "VERDIFY_COMPONENT_EXPERIMENT_ENABLED": "enabled",
        "VERDIFY_POLICY_VECTOR_MODE": "off",
        "VERDIFY_ACTIVE_EXPERIMENT_ID": "11111111-1111-4111-8111-111111111111",
        "DB_HOST": "verdify-db",
        "DB_NAME": "verdify",
        "VERDIFY_EXPERIMENT_RANDOMIZER_DB_USER": "randomizer-login",
        "VERDIFY_EXPERIMENT_RANDOMIZER_DB_PASSWORD": "hidden",
        "VERDIFY_EXPERIMENT_SELECTOR_API_KEY": "hidden",
    }
    with pytest.raises(ConfigurationError, match="globally routable"):
        load_settings(
            {
                **base,
                "VERDIFY_EXPERIMENT_SELECTOR_ENDPOINT": "https://10.0.0.7/select",
                "VERDIFY_EXPERIMENT_SELECTOR_EGRESS_CIDR": "10.0.0.7/32",
            },
            mode_override="selector",
        )
    with pytest.raises(ConfigurationError, match="credential-free"):
        load_settings(
            {
                **base,
                "VERDIFY_EXPERIMENT_SELECTOR_ENDPOINT": "https://user:pass@inference.example/select",
                "VERDIFY_EXPERIMENT_SELECTOR_EGRESS_CIDR": "8.8.8.8/32",
            },
            mode_override="selector",
        )


def test_selector_empty_endpoint_is_unconfigured_despite_manifest_placeholder_cidr() -> None:
    configured = load_settings(
        {
            "VERDIFY_COMPONENT_EXPERIMENT_ENABLED": "enabled",
            "VERDIFY_POLICY_VECTOR_MODE": "off",
            "VERDIFY_ACTIVE_EXPERIMENT_ID": "11111111-1111-4111-8111-111111111111",
            "DB_HOST": "verdify-db",
            "DB_NAME": "verdify",
            "VERDIFY_EXPERIMENT_RANDOMIZER_DB_USER": "randomizer-login",
            "VERDIFY_EXPERIMENT_RANDOMIZER_DB_PASSWORD": "hidden",
            "VERDIFY_EXPERIMENT_SELECTOR_ENDPOINT": "",
            "VERDIFY_EXPERIMENT_SELECTOR_EGRESS_CIDR": "192.0.2.1/32",
        },
        mode_override="selector",
    )
    assert configured.runnable
    assert configured.provider is None


def test_secret_fields_are_excluded_from_setting_repr() -> None:
    assert "not-logged" not in repr(_provider_settings())
    assert set(FORECAST_VALUE_FIELDS)
