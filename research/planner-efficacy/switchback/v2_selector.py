"""Protocol-v2 once-daily virtual selector contract.

This module is deliberately transport-only: it cannot actuate a device and it
does not know an assignment arm.  Both physical arms therefore pass through
the same frozen request builder and exactly-once choice ledger.  A separate
boundary-only resolver converts the persisted virtual choice into the physical
profile (A always receives baseline; B receives the persisted choice).

Production persistence/provider adapters live outside this research package.
The interfaces here are executable integration contracts, while
``TestingChoiceLedger`` is explicitly test-only and is not a durability claim.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Literal, Protocol

ProfileId = Literal["baseline", "moderate", "aggressive"]
VALID_PROFILES: tuple[ProfileId, ...] = ("baseline", "moderate", "aggressive")

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_CONTEXT_KINDS = frozenset(
    {
        "climate_history",
        "outside_weather",
        "forecast_vintage",
        "crop_state",
        "facility_state",
        "equipment_availability",
        "irrigation_fertigation_covariate",
        "frozen_lesson_snapshot",
    }
)
_FORBIDDEN_KEY_PARTS = frozenset(
    {
        "arm",
        "assignment",
        "blinded_label",
        "mapping",
        "schedule",
        "comparative",
        "outcome",
        "efficacy",
        "post_cutoff",
        "online_lesson",
        "password",
        "secret",
        "token",
        "credential",
        "authorization",
        "cookie",
        "api_key",
    }
)


def _aware_utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _require_sha256(value: str, field: str) -> None:
    if not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{field} must be lowercase SHA-256 hex")


def _validate_nfc_and_safe(value: Any, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, str):
        if unicodedata.normalize("NFC", value) != value:
            raise ValueError(f"context string at {'.'.join(path) or '<root>'} must already be Unicode NFC")
        return
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"context number at {'.'.join(path)} must be finite")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("context object keys must be strings")
            normalized = key.lower().replace("-", "_")
            if any(part in normalized for part in _FORBIDDEN_KEY_PARTS):
                raise ValueError(f"forbidden selector-context key at {'.'.join(path + (key,))}")
            _validate_nfc_and_safe(key, path + (key,))
            _validate_nfc_and_safe(item, path + (key,))
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            _validate_nfc_and_safe(item, path + (str(index),))
        return
    raise TypeError(f"unsupported selector-context value at {'.'.join(path)}: {type(value).__name__}")


def canonical_request_bytes(value: Mapping[str, Any]) -> bytes:
    """Stable UTF-8 request bytes; these exact bytes are sent and hashed."""
    _validate_nfc_and_safe(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


@dataclass(frozen=True)
class SelectorIdentity:
    provider: str
    model_identifier: str
    model_revision: str
    expected_system_fingerprint: str
    prompt_sha256: str
    system_message_sha256: str
    messages_sha256: str
    decoding_parameters_sha256: str
    tool_contract_revision: str
    response_schema_revision: str
    context_schema_revision: str
    lesson_snapshot_sha256: str
    runtime_environment_digest: str
    timeout_milliseconds: int
    max_attempts: int

    def __post_init__(self) -> None:
        for field in (
            "provider",
            "model_identifier",
            "model_revision",
            "expected_system_fingerprint",
            "tool_contract_revision",
            "response_schema_revision",
            "context_schema_revision",
            "runtime_environment_digest",
        ):
            value = getattr(self, field)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{field} must be a nonempty frozen string")
            if unicodedata.normalize("NFC", value) != value:
                raise ValueError(f"{field} must already be Unicode NFC")
        for field in (
            "prompt_sha256",
            "system_message_sha256",
            "messages_sha256",
            "decoding_parameters_sha256",
            "lesson_snapshot_sha256",
        ):
            _require_sha256(getattr(self, field), field)
        if self.timeout_milliseconds <= 0 or self.max_attempts < 1:
            raise ValueError("timeout_milliseconds and max_attempts must be positive")

    def as_request_identity(self) -> dict[str, str | int]:
        return {
            "context_schema_revision": self.context_schema_revision,
            "decoding_parameters_sha256": self.decoding_parameters_sha256,
            "expected_system_fingerprint": self.expected_system_fingerprint,
            "lesson_snapshot_sha256": self.lesson_snapshot_sha256,
            "max_attempts": self.max_attempts,
            "messages_sha256": self.messages_sha256,
            "model_identifier": self.model_identifier,
            "model_revision": self.model_revision,
            "prompt_sha256": self.prompt_sha256,
            "provider": self.provider,
            "response_schema_revision": self.response_schema_revision,
            "runtime_environment_digest": self.runtime_environment_digest,
            "system_message_sha256": self.system_message_sha256,
            "timeout_milliseconds": self.timeout_milliseconds,
            "tool_contract_revision": self.tool_contract_revision,
        }

    @property
    def digest_sha256(self) -> str:
        return hashlib.sha256(canonical_request_bytes(self.as_request_identity())).hexdigest()


@dataclass(frozen=True)
class ContextRecord:
    observed_at: datetime
    kind: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class FrozenContext:
    cutoff_at: datetime
    boundary_at: datetime
    records: tuple[dict[str, Any], ...]
    canonical_sha256: str


def freeze_context(
    records: Sequence[ContextRecord],
    *,
    cutoff_at: datetime,
    boundary_at: datetime,
) -> FrozenContext:
    """Create the arm-free, pre-cutoff context used identically on both arms.

    Records after the cutoff and kinds outside the positive allowlist are
    excluded.  Forbidden nested keys in an otherwise admitted record fail
    closed, so arm/outcome/credential leakage cannot be hidden in a payload.
    """
    cutoff = _aware_utc(cutoff_at, "cutoff_at")
    boundary = _aware_utc(boundary_at, "boundary_at")
    if cutoff >= boundary:
        raise ValueError("selector context cutoff must be strictly before the local-day boundary")
    admitted: list[dict[str, Any]] = []
    for record in records:
        observed = _aware_utc(record.observed_at, "record.observed_at")
        if observed > cutoff or record.kind not in _SAFE_CONTEXT_KINDS:
            continue
        if not isinstance(record.payload, Mapping):
            raise TypeError("context payload must be a mapping")
        _validate_nfc_and_safe(record.payload, (record.kind,))
        admitted.append(
            {
                "kind": record.kind,
                "observed_at": observed.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                "payload": dict(record.payload),
            }
        )
    admitted.sort(key=lambda item: (item["observed_at"], item["kind"], canonical_request_bytes(item)))
    envelope = {
        "boundary_at": boundary.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "cutoff_at": cutoff.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "records": admitted,
        "schema": "verdify-selector-context-v2",
    }
    digest = hashlib.sha256(canonical_request_bytes(envelope)).hexdigest()
    return FrozenContext(cutoff, boundary, tuple(admitted), digest)


@dataclass(frozen=True)
class ProviderResponse:
    raw_response: bytes
    provider: str
    model_identifier: str
    model_revision: str
    system_fingerprint: str
    completed_at: datetime


class SelectorProvider(Protocol):
    def infer(self, request: bytes, *, idempotency_key: str, timeout_milliseconds: int) -> ProviderResponse: ...


class ProviderUnavailableError(Exception):
    """Routine provider/network unavailability that must fail to baseline."""


@dataclass(frozen=True)
class SelectorChoice:
    choice_id: str
    study_id: str
    local_date: str
    profile: ProfileId
    fallback_reason: str | None
    invocation_key: str
    context_sha256: str
    identity_sha256: str
    raw_request_sha256: str
    raw_response_sha256: str | None
    attempt_receipt_sha256: tuple[str, ...]
    accepted_at: datetime


class ChoiceLedger(Protocol):
    def get(self, study_id: str, local_date: str) -> SelectorChoice | None: ...

    def insert_once(self, choice: SelectorChoice) -> SelectorChoice: ...


class TestingChoiceLedger:
    """Process-memory test double; production must provide durable insert-once."""

    __test__ = False

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], SelectorChoice] = {}

    def get(self, study_id: str, local_date: str) -> SelectorChoice | None:
        return self._rows.get((study_id, local_date))

    def insert_once(self, choice: SelectorChoice) -> SelectorChoice:
        return self._rows.setdefault((choice.study_id, choice.local_date), choice)


def _invocation_key(namespace: uuid.UUID, study_id: str, local_date: str) -> str:
    parsed = date.fromisoformat(local_date)
    if parsed.isoformat() != local_date:
        raise ValueError("local_date must be canonical YYYY-MM-DD")
    normalized = unicodedata.normalize("NFC", study_id)
    return str(uuid.uuid5(namespace, f"verdify-selector-v2\x00{normalized}\x00{local_date}"))


def _attempt_receipt(kind: str, response_hash: str | None, attempt: int) -> str:
    payload = {"attempt": attempt, "kind": kind, "response_sha256": response_hash or "none"}
    return hashlib.sha256(canonical_request_bytes(payload)).hexdigest()


def _validate_response(response: ProviderResponse, identity: SelectorIdentity, boundary_at: datetime) -> ProfileId:
    completed = _aware_utc(response.completed_at, "response.completed_at")
    if completed >= boundary_at:
        raise ValueError("late")
    expected = (
        identity.provider,
        identity.model_identifier,
        identity.model_revision,
        identity.expected_system_fingerprint,
    )
    actual = (response.provider, response.model_identifier, response.model_revision, response.system_fingerprint)
    if actual != expected:
        raise ValueError("revision_mismatch")
    try:
        decoded = json.loads(response.raw_response)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("malformed") from exc
    if not isinstance(decoded, dict) or set(decoded) != {"profile"} or decoded["profile"] not in VALID_PROFILES:
        raise ValueError("invalid_output")
    return decoded["profile"]


def select_once(
    *,
    ledger: ChoiceLedger,
    provider: SelectorProvider,
    namespace_uuid: uuid.UUID,
    study_id: str,
    local_date: str,
    context: FrozenContext,
    identity: SelectorIdentity,
    now: datetime,
) -> SelectorChoice:
    """Persist one immutable virtual choice; retries return the existing row."""
    invocation_key = _invocation_key(namespace_uuid, study_id, local_date)
    existing = ledger.get(study_id, local_date)
    if existing is not None:
        expected = (invocation_key, context.canonical_sha256, identity.digest_sha256, local_date)
        actual = (existing.invocation_key, existing.context_sha256, existing.identity_sha256, existing.local_date)
        if actual != expected:
            raise ValueError("same-day idempotent retry changed frozen invocation/context/identity; audit and abort")
        return existing
    now_utc = _aware_utc(now, "now")
    if now_utc >= context.boundary_at:
        raise ValueError("missed selector start: no invocation may begin at/after the boundary")
    request = canonical_request_bytes(
        {
            "context": list(context.records),
            "context_sha256": context.canonical_sha256,
            "identity": identity.as_request_identity(),
            "invocation_key": invocation_key,
            "local_date": local_date,
            "schema": "verdify-daily-selector-request-v2",
            "study_id": study_id,
            "valid_outputs": list(VALID_PROFILES),
        }
    )
    request_hash = hashlib.sha256(request).hexdigest()
    profile: ProfileId = "baseline"
    fallback_reason: str | None = "timeout"
    response_hash: str | None = None
    receipts: list[str] = []
    for attempt in range(1, identity.max_attempts + 1):
        try:
            response = provider.infer(
                request,
                idempotency_key=invocation_key,
                timeout_milliseconds=identity.timeout_milliseconds,
            )
            if not isinstance(response.raw_response, (bytes, bytearray, memoryview)):
                fallback_reason = "malformed"
                receipts.append(_attempt_receipt("malformed", None, attempt))
                break
            response = ProviderResponse(
                raw_response=bytes(response.raw_response),
                provider=response.provider,
                model_identifier=response.model_identifier,
                model_revision=response.model_revision,
                system_fingerprint=response.system_fingerprint,
                completed_at=response.completed_at,
            )
            response_hash = hashlib.sha256(response.raw_response).hexdigest()
            try:
                profile = _validate_response(response, identity, context.boundary_at)
                fallback_reason = None
                receipts.append(_attempt_receipt("accepted", response_hash, attempt))
                break
            except ValueError as exc:
                fallback_reason = str(exc)
                receipts.append(_attempt_receipt(fallback_reason, response_hash, attempt))
                break
        except TimeoutError:
            receipts.append(_attempt_receipt("timeout", None, attempt))
            fallback_reason = "timeout"
        except (ProviderUnavailableError, ConnectionError, OSError):
            receipts.append(_attempt_receipt("provider_unavailable", None, attempt))
            fallback_reason = "provider_unavailable"
    choice = SelectorChoice(
        choice_id=invocation_key,
        study_id=study_id,
        local_date=local_date,
        profile=profile,
        fallback_reason=fallback_reason,
        invocation_key=invocation_key,
        context_sha256=context.canonical_sha256,
        identity_sha256=identity.digest_sha256,
        raw_request_sha256=request_hash,
        raw_response_sha256=response_hash,
        attempt_receipt_sha256=tuple(receipts),
        accepted_at=now_utc,
    )
    accepted = ledger.insert_once(choice)
    expected = (invocation_key, context.canonical_sha256, identity.digest_sha256, local_date)
    actual = (accepted.invocation_key, accepted.context_sha256, accepted.identity_sha256, accepted.local_date)
    if actual != expected:
        raise ValueError("concurrent same-day insert changed frozen invocation/context/identity; audit and abort")
    return accepted


def resolve_boundary_profile(
    choice: SelectorChoice,
    *,
    physical_arm: Literal["A", "B"],
    assignment_local_date: str,
    boundary_at: datetime,
    resolved_at: datetime,
) -> ProfileId:
    """Restricted assignment-service resolver; return only one profile id.

    Mapping/schedule/blinded-label inputs are deliberately absent.  The
    component executor consumes only this least-information result and never
    receives ``physical_arm`` itself.
    """
    if choice.local_date != assignment_local_date:
        raise ValueError("choice/assignment local-date mismatch")
    boundary = _aware_utc(boundary_at, "boundary_at")
    if _aware_utc(resolved_at, "resolved_at") != boundary:
        raise ValueError("profile admission is boundary-only; intraday admission is forbidden")
    if choice.accepted_at >= boundary:
        raise ValueError("late selector choice")
    if physical_arm == "A":
        return "baseline"
    if physical_arm == "B":
        return choice.profile
    raise ValueError("physical_arm must be A or B")
