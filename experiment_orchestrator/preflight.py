"""Non-actuating strict OpenAI selector-profile preflight.

The preflight has provider-network authority only.  It has no database,
Kubernetes, or equipment endpoint, and its persisted receipt intentionally omits the
selected profile and complete provider response.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import ipaddress
import json
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .contracts import (
    CLIMATE_SOURCE_SCHEMA,
    CLIMATE_VALUE_FIELDS,
    CONTEXT_SCHEMA,
    ContractError,
    SelectorContext,
    SelectorIdentity,
    canonical_json_bytes,
    canonical_sha256,
    format_utc_timestamp,
)
from .provider import SelectorProviderAdapter
from .settings import ProviderSettings

PREFLIGHT_SCHEMA = "verdify-experiment-v2-openai-preflight-receipt-v1"


@dataclass(frozen=True)
class PreflightReceipt:
    schema: str
    status: str
    purpose: str
    provider: str
    model_identifier: str
    model_revision: str
    selector_identity_sha256: str
    context_sha256: str
    raw_request_sha256: str
    raw_response_sha256: str
    attempt_receipt_sha256: tuple[str, ...]
    response_schema_revision: str
    strict_profile_only: bool
    tools_allowed: bool
    database_authority: bool
    device_authority: bool
    kubernetes_authority: bool
    recorded_at: str

    def canonical_bytes(self) -> bytes:
        payload = asdict(self)
        payload["attempt_receipt_sha256"] = list(self.attempt_receipt_sha256)
        return canonical_json_bytes(payload, reject_forbidden_fields=False)


def _preflight_context(now: datetime) -> SelectorContext:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ContractError("preflight clock must be timezone-aware")
    now = now.astimezone(UTC)
    observed = now - timedelta(seconds=30)
    cutoff = now - timedelta(seconds=1)
    boundary = now + timedelta(minutes=5)
    values = dict.fromkeys(CLIMATE_VALUE_FIELDS)
    values["temp_avg_f"] = 75.0
    values["vpd_avg_kpa"] = 1.2
    source = {
        "schema": CLIMATE_SOURCE_SCHEMA,
        "observed_at": format_utc_timestamp(observed),
        "values": values,
    }
    source["source_row_sha256"] = canonical_sha256(
        source,
        domain="verdify-experiment-v2-selector-source-v1",
    )
    payload = {
        "boundary_at": format_utc_timestamp(boundary),
        "climate_observations": [source],
        "context_cutoff_at": format_utc_timestamp(cutoff),
        "forecast_vintage": [],
        "local_date": (now + timedelta(days=1)).date().isoformat(),
        "schema": CONTEXT_SCHEMA,
    }
    raw = canonical_json_bytes(payload)
    return SelectorContext.parse(raw, hashlib.sha256(raw).hexdigest())


async def run_preflight(
    *,
    identity: SelectorIdentity,
    provider: SelectorProviderAdapter,
    clock=lambda: datetime.now(UTC),
) -> PreflightReceipt:
    started = clock().astimezone(UTC)
    if (
        identity.provider != "openai"
        or identity.model_identifier != "gpt-5.6-luna"
        or identity.model_revision != "gpt-5.6-luna"
        or identity.tool_contract_revision != "none-v1"
        or identity.transport_protocol != "openai_chat_completions"
    ):
        raise ContractError("preflight identity is not the strict tool-free GPT-5.6 Luna contract")
    context = _preflight_context(started)
    invocation = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"verdify-openai-preflight:{identity.canonical_sha256}:{started.date().isoformat()}",
        )
    )
    result = await provider.select(
        study_id="verdify-experiment-v2-non-actuating-preflight",
        local_date=context.local_date,
        invocation_key=invocation,
        context=context,
        identity=identity,
    )
    if result.fallback_reason is not None or result.raw_response_sha256 is None:
        raise ContractError("OpenAI preflight did not return one contract-valid strict profile response")
    return PreflightReceipt(
        schema=PREFLIGHT_SCHEMA,
        status="pass",
        purpose="non-actuating-profile-contract-preflight",
        provider=identity.provider,
        model_identifier=identity.model_identifier,
        model_revision=identity.model_revision,
        selector_identity_sha256=identity.canonical_sha256,
        context_sha256=context.canonical_sha256,
        raw_request_sha256=result.raw_request_sha256,
        raw_response_sha256=result.raw_response_sha256,
        attempt_receipt_sha256=result.attempt_receipt_sha256,
        response_schema_revision=identity.response_schema_revision,
        strict_profile_only=True,
        tools_allowed=False,
        database_authority=False,
        device_authority=False,
        kubernetes_authority=False,
        recorded_at=format_utc_timestamp(started),
    )


def _load_identity(path: Path) -> SelectorIdentity:
    raw = path.read_bytes()
    return SelectorIdentity.parse(raw, hashlib.sha256(raw).hexdigest())


async def _main(args: argparse.Namespace) -> int:
    identity = _load_identity(args.identity)
    key = os.environ.get("VERDIFY_EXPERIMENT_SELECTOR_API_KEY", "")
    if not key:
        raise ContractError("OpenAI preflight credential is unavailable")
    provider = SelectorProviderAdapter(
        ProviderSettings(
            endpoint="https://api.openai.com/v1",
            endpoint_host="api.openai.com",
            endpoint_port=443,
            egress_network=ipaddress.ip_network("0.0.0.0/0"),
            api_key=key,
            maximum_response_bytes=16_384,
        )
    )
    receipt = await run_preflight(identity=identity, provider=provider)
    raw = receipt.canonical_bytes()
    args.receipt.write_bytes(raw + b"\n")
    print(json.dumps({"receipt_sha256": hashlib.sha256(raw).hexdigest(), "status": "pass"}, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the non-actuating strict OpenAI profile preflight")
    parser.add_argument("--identity", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, default=Path("/dev/termination-log"))
    args = parser.parse_args()
    return asyncio.run(_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
