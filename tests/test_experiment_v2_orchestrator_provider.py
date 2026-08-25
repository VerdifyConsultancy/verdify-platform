from __future__ import annotations

import hashlib
import ipaddress
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from experiment_orchestrator.contracts import (  # noqa: E402
    CLIMATE_SOURCE_SCHEMA,
    CLIMATE_VALUE_FIELDS,
    CONTEXT_SCHEMA,
    FORECAST_VALUE_FIELDS,
    OPENAI_SELECTOR_IDENTITY_SCHEMA,
    OPENAI_SELECTOR_RESPONSE_FORMAT,
    OPENAI_SELECTOR_RESPONSE_SCHEMA,
    SELECTOR_IDENTITY_SCHEMA,
    SelectorContext,
    SelectorIdentity,
    canonical_json_bytes,
    canonical_sha256,
    openai_messages_template_bytes,
)
from experiment_orchestrator.provider import (  # noqa: E402
    HttpxProviderTransport,
    ProviderUnavailable,
    SelectorProviderAdapter,
)
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


def _openai_identity(**changes) -> SelectorIdentity:
    system_message = "Choose exactly one allowed greenhouse profile. Return only schema-conforming JSON."
    prompt = "Use only the supplied pre-cutoff context and choose the safest supported profile."
    decoding = {
        "chat_template_kwargs": {"reasoning_effort": "medium"},
        "max_tokens": 512,
        "response_format": OPENAI_SELECTOR_RESPONSE_FORMAT,
        "stream": False,
        "temperature": 0,
    }
    payload = {
        "schema": OPENAI_SELECTOR_IDENTITY_SCHEMA,
        "provider": "cortex-openai",
        "model_identifier": "llm.primary.longctx",
        "model_revision": "llm.qwen38u.longctx",
        "expected_system_fingerprint": "vllm-0.27.1-tp2-514990f7",
        "prompt": prompt,
        "system_message": system_message,
        "decoding_parameters": decoding,
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "system_message_sha256": hashlib.sha256(system_message.encode()).hexdigest(),
        "messages_sha256": hashlib.sha256(openai_messages_template_bytes(system_message, prompt)).hexdigest(),
        "decoding_parameters_sha256": hashlib.sha256(canonical_json_bytes(decoding)).hexdigest(),
        "tool_contract_revision": "none-v1",
        "response_schema_revision": OPENAI_SELECTOR_RESPONSE_SCHEMA,
        "context_schema_sha256": "5" * 64,
        "lesson_snapshot_sha256": "6" * 64,
        "runtime_environment_sha256": "7" * 64,
        "timeout_milliseconds": 60_000,
        "max_attempts": 2,
    }
    payload.update(changes)
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


def _cortex_provider_settings() -> ProviderSettings:
    return ProviderSettings(
        endpoint="https://cortex.vallery.net/v1",
        endpoint_host="cortex.vallery.net",
        endpoint_port=443,
        egress_network=ipaddress.ip_network("192.168.7.10/32"),
        api_key="not-logged",
    )


def _openai_response(
    *,
    profile: str = "moderate",
    finish_reason: str = "stop",
    model: str = "llm.qwen38u.longctx",
    fingerprint: str | None = "vllm-0.27.1-tp2-514990f7",
    content: str | None = None,
    message_changes: dict | None = None,
) -> bytes:
    message = {
        "content": content
        if content is not None
        else json.dumps({"profile": profile}, sort_keys=True, separators=(",", ":")),
        "role": "assistant",
    }
    message.update(message_changes or {})
    return json.dumps(
        {
            "choices": [{"finish_reason": finish_reason, "index": 0, "logprobs": None, "message": message}],
            "created": 1787690000,
            "id": "chatcmpl-selector-test",
            "model": model,
            "object": "chat.completion",
            "system_fingerprint": fingerprint,
            "usage": {"completion_tokens": 154, "prompt_tokens": 26, "total_tokens": 180},
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def test_cortex_openai_identity_recomputes_embedded_hashes_and_bounds_decoding() -> None:
    with pytest.raises(ValueError, match="embedded artifact hash mismatch"):
        _openai_identity(prompt="changed without rebinding its frozen hash")
    with pytest.raises(ValueError, match="forbids tools"):
        _openai_identity(tool_contract_revision="tools-v2")

    payload = json.loads(_openai_identity().canonical_bytes)
    payload["decoding_parameters"]["max_tokens"] = 511
    payload["decoding_parameters_sha256"] = hashlib.sha256(
        canonical_json_bytes(payload["decoding_parameters"])
    ).hexdigest()
    raw = canonical_json_bytes(payload)
    with pytest.raises(ValueError, match=r"\[512,16384\]"):
        SelectorIdentity.parse(raw, hashlib.sha256(raw).hexdigest())


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
async def test_cortex_openai_request_is_exact_bounded_and_accepts_verified_runtime_identity() -> None:
    transport = Transport(_openai_response())

    async def cortex_resolver(_host: str, _port: int):
        return frozenset(
            {
                ipaddress.ip_address("192.168.7.10"),
                ipaddress.ip_address("::ffff:192.168.7.10"),
            }
        )

    identity = _openai_identity()
    adapter = SelectorProviderAdapter(
        _cortex_provider_settings(),
        transport=transport,
        resolver=cortex_resolver,
        clock=lambda: datetime(2026, 8, 24, 23, 50, tzinfo=UTC),
    )
    result = await adapter.select(
        study_id="study",
        local_date="2026-08-25",
        invocation_key="11111111-1111-4111-8111-111111111111",
        context=_context(),
        identity=identity,
    )
    assert (result.profile, result.fallback_reason) == ("moderate", None)
    assert result.raw_response_sha256 == hashlib.sha256(_openai_response()).hexdigest()
    assert len(transport.calls) == 1
    endpoint, call = transport.calls[0]
    assert endpoint == "https://cortex.vallery.net/v1/chat/completions"
    assert call["timeout_seconds"] == 60
    assert call["headers"] == {
        "accept": "application/json",
        "authorization": "Bearer not-logged",
        "content-type": "application/json",
        "idempotency-key": "11111111-1111-4111-8111-111111111111",
    }
    body = json.loads(call["body"])
    assert body["model"] == "llm.primary.longctx"
    assert body["max_tokens"] == 512
    assert body["temperature"] == 0
    assert body["stream"] is False
    assert body["chat_template_kwargs"] == {"reasoning_effort": "medium"}
    assert body["response_format"] == OPENAI_SELECTOR_RESPONSE_FORMAT
    assert "tools" not in body and "tool_choice" not in body
    assert body["messages"][0]["role"] == "system"
    assert "verdify-daily-selector-request-v2" in body["messages"][1]["content"]
    assert all(term not in call["body"] for term in (b"physical_arm", b"mapping", b"secret"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw",
    [
        _openai_response(finish_reason="length"),
        _openai_response(content='{"profile": "moderate"}'),
        _openai_response(model="llm.qwen38u.unpinned"),
        _openai_response(fingerprint=None),
        _openai_response(fingerprint="vllm-different"),
        _openai_response(message_changes={"tool_calls": [{"id": "unexpected"}]}),
        _openai_response(content='{"profile":"unknown"}'),
        b"not-json",
    ],
)
async def test_cortex_openai_rejects_truncation_noncanonical_body_and_identity_drift(raw: bytes) -> None:
    transport = Transport(raw)

    async def cortex_resolver(_host: str, _port: int):
        return frozenset({ipaddress.ip_address("192.168.7.10")})

    result = await SelectorProviderAdapter(
        _cortex_provider_settings(),
        transport=transport,
        resolver=cortex_resolver,
        clock=lambda: datetime(2026, 8, 24, 23, 50, tzinfo=UTC),
    ).select(
        study_id="study",
        local_date="2026-08-25",
        invocation_key="11111111-1111-4111-8111-111111111111",
        context=_context(),
        identity=_openai_identity(),
    )
    assert (result.profile, result.fallback_reason) == ("baseline", "invalid_response")
    # Contract-invalid replies are not retried into sampling variance.
    assert len(transport.calls) == 1


class FailingTransport:
    def __init__(self) -> None:
        self.calls = 0

    async def post(self, *_args, **_kwargs) -> bytes:
        self.calls += 1
        raise ProviderUnavailable("synthetic HTTP status/body failure")


@pytest.mark.asyncio
async def test_cortex_http_failure_retries_exactly_then_falls_back() -> None:
    transport = FailingTransport()

    async def cortex_resolver(_host: str, _port: int):
        return frozenset({ipaddress.ip_address("192.168.7.10")})

    result = await SelectorProviderAdapter(
        _cortex_provider_settings(), transport=transport, resolver=cortex_resolver
    ).select(
        study_id="study",
        local_date="2026-08-25",
        invocation_key="11111111-1111-4111-8111-111111111111",
        context=_context(),
        identity=_openai_identity(),
    )
    assert (result.profile, result.fallback_reason) == ("baseline", "provider_unavailable")
    assert transport.calls == 2
    assert len(result.attempt_receipt_sha256) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "content_type", "body"),
    [
        (429, "application/json", b"{}"),
        (200, "text/html", b"{}"),
        (200, "application/json", b"x" * 513),
    ],
)
async def test_http_transport_rejects_status_content_type_and_oversized_body(
    status: int,
    content_type: str,
    body: bytes,
) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers={"content-type": content_type}, content=body)

    transport = HttpxProviderTransport(transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderUnavailable):
        await transport.post(
            "https://cortex.vallery.net/v1/chat/completions",
            body=b"{}",
            headers={"authorization": "Bearer not-logged"},
            timeout_seconds=1,
            maximum_response_bytes=512,
        )


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


def test_selector_endpoint_requires_exact_public_or_cortex_host_cidr_and_no_url_credentials() -> None:
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
    cortex = load_settings(
        {
            **base,
            "VERDIFY_EXPERIMENT_SELECTOR_ENDPOINT": "https://cortex.vallery.net/v1",
            "VERDIFY_EXPERIMENT_SELECTOR_EGRESS_CIDR": "192.168.7.10/32",
        },
        mode_override="selector",
    )
    assert cortex.provider is not None
    assert cortex.provider.endpoint_host == "cortex.vallery.net"
    with pytest.raises(ConfigurationError, match="globally routable"):
        load_settings(
            {
                **base,
                "VERDIFY_EXPERIMENT_SELECTOR_ENDPOINT": "https://cortex.vallery.net/v1",
                "VERDIFY_EXPERIMENT_SELECTOR_EGRESS_CIDR": "192.168.7.11/32",
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
