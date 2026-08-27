"""Exact-endpoint HTTPS selector provider with bounded, baseline-safe failures."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import socket
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx

from .contracts import (
    CORTEX_MAX_MODEL_LEN_TOKENS,
    MIN_SELECTOR_OUTPUT_TOKENS,
    ContractError,
    ProviderResponse,
    SelectorContext,
    SelectorIdentity,
    canonical_json_bytes,
    parse_canonical_document,
)
from .settings import ProviderSettings

Resolver = Callable[[str, int], Awaitable[frozenset[ipaddress.IPv4Address | ipaddress.IPv6Address]]]

CORTEX_HOST = "cortex.vallery.net"
CORTEX_OPENAI_BASE_PATH = "/v1"
CORTEX_CHAT_COMPLETIONS_PATH = "/v1/chat/completions"
CORTEX_LONG_CONTEXT_MODEL = "llm.primary.longctx"


class ProviderTransport(Protocol):
    async def post(
        self,
        endpoint: str,
        *,
        body: bytes,
        headers: Mapping[str, str],
        timeout_seconds: float,
        maximum_response_bytes: int,
    ) -> bytes: ...


class HttpxProviderTransport:
    """Minimal HTTP transport; callers never receive response headers/secrets."""

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    async def post(
        self,
        endpoint: str,
        *,
        body: bytes,
        headers: Mapping[str, str],
        timeout_seconds: float,
        maximum_response_bytes: int,
    ) -> bytes:
        timeout = httpx.Timeout(timeout_seconds)
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
            transport=self._transport,
        ) as client:
            async with client.stream("POST", endpoint, content=body, headers=headers) as response:
                if response.status_code != 200:
                    raise ProviderUnavailable("provider returned a non-success status")
                content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if content_type != "application/json":
                    raise ProviderUnavailable("provider returned a non-JSON response")
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > maximum_response_bytes:
                        raise ProviderUnavailable("provider response exceeded the locked size")
                    chunks.append(chunk)
                return b"".join(chunks)


class ProviderUnavailable(RuntimeError):
    """No response may be admitted; the selector must persist baseline."""


async def resolve_endpoint(host: str, port: int) -> frozenset[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    loop = asyncio.get_running_loop()
    rows = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses = frozenset(ipaddress.ip_address(row[4][0]) for row in rows)
    if not addresses:
        raise ProviderUnavailable("selector endpoint did not resolve")
    return addresses


@dataclass(frozen=True)
class SelectorAttemptResult:
    profile: str
    fallback_reason: str | None
    raw_request_sha256: str
    raw_response_sha256: str | None
    attempt_receipt_sha256: tuple[str, ...]


def _request_budget_bytes(identity: SelectorIdentity) -> int:
    """Return a tokenizer-independent prompt ceiling for the frozen route."""

    reserved_output = MIN_SELECTOR_OUTPUT_TOKENS
    if identity.decoding_parameters is not None:
        value = identity.decoding_parameters["max_tokens"]
        assert type(value) is int  # SelectorIdentity.parse already proves it.
        reserved_output = value
    return CORTEX_MAX_MODEL_LEN_TOKENS - reserved_output


def build_request(
    *,
    study_id: str,
    local_date: str,
    invocation_key: str,
    context: SelectorContext,
    identity: SelectorIdentity,
) -> bytes:
    # The DB-bound context hash is sent alongside the structurally validated
    # decoded context.  Assignment/arm/mapping are absent by construction.
    context_payload = json.loads(context.canonical_bytes)
    return canonical_json_bytes(
        {
            "context": context_payload,
            "context_sha256": context.canonical_sha256,
            "identity_sha256": identity.canonical_sha256,
            "invocation_key": invocation_key,
            "local_date": local_date,
            "schema": "verdify-daily-selector-request-v2",
            "study_id": study_id,
            "valid_profiles": ["baseline", "moderate", "aggressive"],
        }
    )


def _cortex_chat_completions_endpoint(endpoint: str) -> str:
    """Resolve only the one configured Cortex OpenAI base/completions URL."""

    parsed = urlsplit(endpoint)
    path = parsed.path.rstrip("/") or "/"
    if parsed.scheme != "https" or parsed.hostname != CORTEX_HOST or (parsed.port or 443) != 443:
        raise ProviderUnavailable("OpenAI selector endpoint is not the locked Cortex authority")
    if path == CORTEX_OPENAI_BASE_PATH:
        path = CORTEX_CHAT_COMPLETIONS_PATH
    elif path != CORTEX_CHAT_COMPLETIONS_PATH:
        raise ProviderUnavailable("OpenAI selector endpoint path is not the locked chat-completions route")
    return urlunsplit(("https", CORTEX_HOST, path, "", ""))


def build_openai_request(
    *,
    study_id: str,
    local_date: str,
    invocation_key: str,
    context: SelectorContext,
    identity: SelectorIdentity,
) -> bytes:
    """Build the exact non-streaming Cortex request from one hash-bound identity."""

    if (
        identity.transport_protocol != "openai_chat_completions"
        or identity.system_message is None
        or identity.prompt is None
        or identity.decoding_parameters is None
    ):
        raise ContractError("OpenAI selector request artifacts are unavailable")
    if identity.model_identifier != CORTEX_LONG_CONTEXT_MODEL:
        raise ContractError("OpenAI selector model differs from the locked Cortex long-context alias")
    selector_request = build_request(
        study_id=study_id,
        local_date=local_date,
        invocation_key=invocation_key,
        context=context,
        identity=identity,
    ).decode("utf-8")
    body: dict[str, object] = {
        "model": identity.model_identifier,
        "messages": [
            {"content": identity.system_message, "role": "system"},
            {"content": f"{identity.prompt}\n\n{selector_request}", "role": "user"},
        ],
        **dict(identity.decoding_parameters),
    }
    # canonical_json_bytes rejects credentials, arm/mapping fields, non-NFC
    # text, non-finite numbers, and any accidental tool-bearing structure.
    return canonical_json_bytes(body)


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ContractError(f"duplicate OpenAI response field {key!r}")
        value[key] = item
    return value


def _parse_openai_response(raw: bytes, identity: SelectorIdentity, completed_at: datetime) -> ProviderResponse:
    """Extract one canonical profile from a strict OpenAI chat envelope."""

    try:
        envelope = json.loads(raw, object_pairs_hook=_unique_json_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError("OpenAI selector response is not valid UTF-8 JSON") from exc
    if not isinstance(envelope, dict):
        raise ContractError("OpenAI selector response root must be an object")
    required = {"id", "object", "created", "model", "choices", "system_fingerprint"}
    allowed = required | {"kv_transfer_params", "prompt_logprobs", "service_tier", "usage"}
    if not required <= set(envelope) or not set(envelope) <= allowed:
        raise ContractError("OpenAI selector response envelope shape mismatch")
    if (
        not isinstance(envelope["id"], str)
        or not envelope["id"]
        or envelope["object"] != "chat.completion"
        or type(envelope["created"]) is not int
        or envelope["created"] < 0
    ):
        raise ContractError("OpenAI selector response envelope identity is malformed")
    if envelope["model"] != identity.model_revision:
        raise ContractError("OpenAI selector response model revision mismatch")
    if envelope["system_fingerprint"] != identity.expected_system_fingerprint:
        raise ContractError("OpenAI selector response system fingerprint mismatch")
    if envelope.get("kv_transfer_params") is not None or envelope.get("prompt_logprobs") is not None:
        raise ContractError("OpenAI selector response unexpectedly contains transfer or prompt-logprob data")
    choices = envelope["choices"]
    if not isinstance(choices, list) or len(choices) != 1:
        raise ContractError("OpenAI selector response must contain exactly one choice")
    choice = choices[0]
    if not isinstance(choice, dict) or set(choice) - {
        "finish_reason",
        "index",
        "logprobs",
        "message",
        "stop_reason",
    }:
        raise ContractError("OpenAI selector choice shape mismatch")
    if set(choice) < {"finish_reason", "index", "message"} or choice["index"] != 0:
        raise ContractError("OpenAI selector choice identity is malformed")
    if choice["finish_reason"] != "stop":
        raise ContractError("OpenAI selector response did not finish with stop")
    if "logprobs" in choice and choice["logprobs"] is not None:
        raise ContractError("OpenAI selector response unexpectedly contains logprobs")
    if choice.get("stop_reason") is not None:
        raise ContractError("OpenAI selector response has an unexpected stop reason")
    message = choice["message"]
    if not isinstance(message, dict):
        raise ContractError("OpenAI selector assistant message is malformed")
    allowed_message = {"content", "reasoning_content", "refusal", "role", "tool_calls"}
    if set(message) - allowed_message or not {"content", "role"} <= set(message):
        raise ContractError("OpenAI selector assistant message shape mismatch")
    if message["role"] != "assistant" or not isinstance(message["content"], str):
        raise ContractError("OpenAI selector assistant content is malformed")
    if message.get("refusal") not in (None, "") or message.get("tool_calls") not in (None, []):
        raise ContractError("OpenAI selector response refused or attempted a tool call")
    reasoning = message.get("reasoning_content")
    if reasoning is not None and not isinstance(reasoning, str):
        raise ContractError("OpenAI selector reasoning content is malformed")
    content = message["content"].encode("utf-8")
    decision = parse_canonical_document(content, hashlib.sha256(content).hexdigest())
    if set(decision) != {"profile"}:
        raise ContractError("OpenAI selector decision schema mismatch")
    profile = decision["profile"]
    if profile not in ("baseline", "moderate", "aggressive"):
        raise ContractError("OpenAI selector decision profile is invalid")
    if completed_at.tzinfo is None or completed_at.utcoffset() is None:
        raise ContractError("provider completion time must be timezone-aware")
    return ProviderResponse(
        profile=profile,
        provider=identity.provider,
        model_identifier=identity.model_identifier,
        model_revision=identity.model_revision,
        system_fingerprint=identity.expected_system_fingerprint,
        completed_at=completed_at.astimezone(UTC),
        raw_response_sha256=hashlib.sha256(raw).hexdigest(),
    )


def _attempt_receipt(attempt: int, result: str, response_sha256: str | None) -> str:
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "attempt": attempt,
                "response_sha256": response_sha256,
                "result": result,
                "schema": "verdify-selector-attempt-receipt-v2",
            }
        )
    ).hexdigest()


class SelectorProviderAdapter:
    def __init__(
        self,
        settings: ProviderSettings | None,
        *,
        transport: ProviderTransport | None = None,
        resolver: Resolver = resolve_endpoint,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._settings = settings
        self._transport = transport or HttpxProviderTransport()
        self._resolver = resolver
        self._clock = clock

    async def select(
        self,
        *,
        study_id: str,
        local_date: str,
        invocation_key: str,
        context: SelectorContext,
        identity: SelectorIdentity,
    ) -> SelectorAttemptResult:
        if identity.transport_protocol == "openai_chat_completions":
            try:
                request = build_openai_request(
                    study_id=study_id,
                    local_date=local_date,
                    invocation_key=invocation_key,
                    context=context,
                    identity=identity,
                )
            except ContractError:
                request = build_request(
                    study_id=study_id,
                    local_date=local_date,
                    invocation_key=invocation_key,
                    context=context,
                    identity=identity,
                )
                request_hash = hashlib.sha256(request).hexdigest()
                return SelectorAttemptResult(
                    "baseline",
                    "invalid_response",
                    request_hash,
                    None,
                    (_attempt_receipt(1, "invalid_response", None),),
                )
        else:
            request = build_request(
                study_id=study_id,
                local_date=local_date,
                invocation_key=invocation_key,
                context=context,
                identity=identity,
            )
        request_hash = hashlib.sha256(request).hexdigest()
        if len(request) > _request_budget_bytes(identity):
            reason = "request_exceeds_context_budget"
            return SelectorAttemptResult(
                "baseline",
                reason,
                request_hash,
                None,
                (_attempt_receipt(1, reason, None),),
            )
        if self._settings is None:
            return SelectorAttemptResult(
                "baseline",
                "provider_unconfigured",
                request_hash,
                None,
                (_attempt_receipt(1, "provider_unconfigured", None),),
            )
        receipts: list[str] = []
        response_hash: str | None = None
        for attempt in range(1, identity.max_attempts + 1):
            try:
                response_hash = None
                addresses = await self._resolver(self._settings.endpoint_host, self._settings.endpoint_port)
                expected = self._settings.egress_network.network_address
                normalized_addresses = frozenset(
                    address.ipv4_mapped
                    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None
                    else address
                    for address in addresses
                )
                if normalized_addresses != frozenset({expected}):
                    raise ProviderUnavailable("selector DNS does not match the exact egress endpoint")
                endpoint = (
                    _cortex_chat_completions_endpoint(self._settings.endpoint)
                    if identity.transport_protocol == "openai_chat_completions"
                    else self._settings.endpoint
                )
                raw = await self._transport.post(
                    endpoint,
                    body=request,
                    headers={
                        "accept": "application/json",
                        "authorization": f"Bearer {self._settings.api_key}",
                        "content-type": "application/json",
                        "idempotency-key": invocation_key,
                    },
                    timeout_seconds=identity.timeout_milliseconds / 1000,
                    maximum_response_bytes=self._settings.maximum_response_bytes,
                )
                response_hash = hashlib.sha256(raw).hexdigest()
                completed_at = self._clock()
                if completed_at >= context.boundary_at:
                    receipts.append(_attempt_receipt(attempt, "late", response_hash))
                    return SelectorAttemptResult("baseline", "late", request_hash, response_hash, tuple(receipts))
                response = (
                    _parse_openai_response(raw, identity, completed_at)
                    if identity.transport_protocol == "openai_chat_completions"
                    else ProviderResponse.parse(raw, identity, completed_at)
                )
                receipts.append(_attempt_receipt(attempt, "accepted", response_hash))
                return SelectorAttemptResult(response.profile, None, request_hash, response_hash, tuple(receipts))
            except (TimeoutError, httpx.TimeoutException):
                reason = "timeout"
            except (ProviderUnavailable, httpx.HTTPError, ConnectionError, OSError, socket.gaierror):
                reason = "provider_unavailable"
            except ContractError:
                reason = "invalid_response"
            receipts.append(_attempt_receipt(attempt, reason, response_hash))
            if reason == "invalid_response":
                break
        return SelectorAttemptResult("baseline", reason, request_hash, response_hash, tuple(receipts))
