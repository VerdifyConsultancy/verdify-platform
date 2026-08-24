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

import httpx

from .contracts import (
    ContractError,
    ProviderResponse,
    SelectorContext,
    SelectorIdentity,
    canonical_json_bytes,
)
from .settings import ProviderSettings

Resolver = Callable[[str, int], Awaitable[frozenset[ipaddress.IPv4Address | ipaddress.IPv6Address]]]


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
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False) as client:
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
        request = build_request(
            study_id=study_id,
            local_date=local_date,
            invocation_key=invocation_key,
            context=context,
            identity=identity,
        )
        request_hash = hashlib.sha256(request).hexdigest()
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
                addresses = await self._resolver(self._settings.endpoint_host, self._settings.endpoint_port)
                expected = self._settings.egress_network.network_address
                if addresses != frozenset({expected}):
                    raise ProviderUnavailable("selector DNS does not match the exact egress endpoint")
                raw = await self._transport.post(
                    self._settings.endpoint,
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
                response = ProviderResponse.parse(raw, identity, completed_at)
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
