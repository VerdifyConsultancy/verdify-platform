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
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal, Protocol

ProfileId = Literal["baseline", "moderate", "aggressive"]
VALID_PROFILES: tuple[ProfileId, ...] = ("baseline", "moderate", "aggressive")
# A frozen selector identity cannot authorize an unbounded provider wait/retry
# loop.  The current 10-second/two-attempt identity remains inside this cap.
MAX_SELECTOR_TIMEOUT_MILLISECONDS = 60_000
MAX_SELECTOR_ATTEMPTS = 3
MAX_CLIMATE_OBSERVATIONS = 48

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CONTEXT_SCHEMA = "verdify-selector-context-v2"
_CLIMATE_SOURCE_SCHEMA = "verdify-selector-climate-source-v1"
_FORECAST_SOURCE_SCHEMA = "verdify-selector-forecast-source-v1"
_CLIMATE_VALUE_FIELDS = frozenset(
    {
        "temp_avg_f",
        "temp_north_f",
        "temp_south_f",
        "temp_east_f",
        "temp_west_f",
        "rh_avg_pct",
        "rh_north_pct",
        "rh_south_pct",
        "rh_east_pct",
        "rh_west_pct",
        "vpd_avg_kpa",
        "vpd_north_kpa",
        "vpd_south_kpa",
        "vpd_east_kpa",
        "vpd_west_kpa",
        "dew_point_f",
        "outdoor_temp_f",
        "outdoor_rh_pct",
        "solar_irradiance_w_m2",
        "leaf_temp_north_f",
        "leaf_temp_south_f",
        "leaf_wetness_north",
        "leaf_wetness_south",
        "wind_speed_mph",
        "precip_in",
        "flow_gpm",
        "mister_water_today_gal",
    }
)
_FORECAST_VALUE_FIELDS = frozenset(
    {
        "temp_f",
        "rh_pct",
        "vpd_kpa",
        "cloud_cover_pct",
        "wind_speed_mph",
        "solar_w_m2",
        "precip_prob_pct",
        "direct_radiation_w_m2",
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
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _trusted_utc_now() -> datetime:
    """Source-owned wall clock used after provider return; callers cannot backdate it."""
    return datetime.now(UTC)


def _require_sha256(value: str, field: str) -> None:
    if not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{field} must be lowercase SHA-256 hex")


def _validate_nfc_and_safe(
    value: Any,
    path: tuple[str, ...] = (),
    *,
    allow_string_values: bool = True,
) -> None:
    if isinstance(value, str):
        if unicodedata.normalize("NFC", value) != value:
            raise ValueError(f"context string at {'.'.join(path) or '<root>'} must already be Unicode NFC")
        if not allow_string_values:
            # Until each context kind has a frozen, typed positive schema,
            # accepting caller-supplied prose/enums would let an arm, mapping,
            # outcome, online lesson, or credential hide under an innocuous
            # key such as ``note``. Numeric/bool/null covariates remain usable;
            # textual context must be represented by a frozen artifact hash in
            # SelectorIdentity rather than copied into the provider request.
            raise ValueError(f"free-form selector-context string at {'.'.join(path) or '<root>'} is forbidden")
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
            if unicodedata.normalize("NFC", key) != key:
                raise ValueError(f"context key at {'.'.join(path + (key,))} must already be Unicode NFC")
            _validate_nfc_and_safe(item, path + (key,), allow_string_values=allow_string_values)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            _validate_nfc_and_safe(item, path + (str(index),), allow_string_values=allow_string_values)
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
        if (
            type(self.timeout_milliseconds) is not int
            or not 1 <= self.timeout_milliseconds <= MAX_SELECTOR_TIMEOUT_MILLISECONDS
        ):
            raise ValueError(
                f"timeout_milliseconds must be an exact integer in [1,{MAX_SELECTOR_TIMEOUT_MILLISECONDS}]"
            )
        if type(self.max_attempts) is not int or not 1 <= self.max_attempts <= MAX_SELECTOR_ATTEMPTS:
            raise ValueError(f"max_attempts must be an exact integer in [1,{MAX_SELECTOR_ATTEMPTS}]")

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
class FrozenContext:
    local_date: str
    cutoff_at: datetime
    boundary_at: datetime
    records: tuple[dict[str, Any], ...]
    canonical_bytes: bytes
    canonical_sha256: str


def _canonical_utc_text(value: datetime, field: str) -> str:
    return _aware_utc(value, field).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_canonical_utc_text(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be canonical RFC3339 UTC text")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise ValueError(f"{field} must be canonical RFC3339 UTC text") from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%S.%fZ") != value:
        raise ValueError(f"{field} must be canonical RFC3339 UTC text")
    return parsed


def _canonical_local_date(value: Any) -> str:
    if not isinstance(value, str):
        raise TypeError("selector context local_date must be canonical YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("selector context local_date must be canonical YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise ValueError("selector context local_date must be canonical YYYY-MM-DD")
    return value


def _exact_mapping(value: Any, fields: frozenset[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ValueError(f"{label} must contain exactly {sorted(fields)}")
    if not all(isinstance(key, str) for key in value):
        raise TypeError(f"{label} keys must be strings")
    return value


def _typed_values(value: Any, fields: frozenset[str], label: str) -> dict[str, int | float | None]:
    source = _exact_mapping(value, fields, label)
    result: dict[str, int | float | None] = {}
    for key in sorted(fields):
        item = source[key]
        if item is None:
            result[key] = None
        elif type(item) in (int, float) and math.isfinite(item):
            result[key] = item
        else:
            raise ValueError(f"{label}.{key} must be a finite JSON number or null")
    return result


def _validated_context_envelope(
    value: Any,
    *,
    expected_local_date: str,
    expected_cutoff: datetime,
    expected_boundary: datetime,
) -> dict[str, Any]:
    source = _exact_mapping(
        value,
        frozenset(
            {
                "schema",
                "local_date",
                "context_cutoff_at",
                "boundary_at",
                "climate_observations",
                "forecast_vintage",
            }
        ),
        "selector context envelope",
    )
    local_date = _canonical_local_date(source["local_date"])
    cutoff = _parse_canonical_utc_text(source["context_cutoff_at"], "context_cutoff_at")
    boundary = _parse_canonical_utc_text(source["boundary_at"], "boundary_at")
    if (
        source["schema"] != _CONTEXT_SCHEMA
        or local_date != expected_local_date
        or cutoff != expected_cutoff
        or boundary != expected_boundary
        or cutoff >= boundary
    ):
        raise ValueError("frozen selector context envelope mismatch; audit and abort")
    climate_source = source["climate_observations"]
    forecast_source = source["forecast_vintage"]
    if not isinstance(climate_source, list) or not isinstance(forecast_source, list):
        raise TypeError("selector context source collections must be arrays")
    if len(climate_source) > MAX_CLIMATE_OBSERVATIONS:
        raise ValueError("selector context exceeds the 48-row climate bound")

    climate: list[dict[str, Any]] = []
    climate_order: list[tuple[datetime, str]] = []
    usable_climate = False
    for index, item in enumerate(climate_source):
        row = _exact_mapping(
            item,
            frozenset({"schema", "observed_at", "source_row_sha256", "values"}),
            f"climate_observations[{index}]",
        )
        if row["schema"] != _CLIMATE_SOURCE_SCHEMA:
            raise ValueError("climate selector source schema mismatch")
        observed = _parse_canonical_utc_text(row["observed_at"], "climate.observed_at")
        if observed > cutoff:
            raise ValueError("frozen selector context contains post-cutoff climate data")
        source_hash = row["source_row_sha256"]
        if not isinstance(source_hash, str) or not _SHA256_RE.fullmatch(source_hash):
            raise ValueError("climate.source_row_sha256 must be lowercase SHA-256 hex")
        values = _typed_values(row["values"], _CLIMATE_VALUE_FIELDS, "climate values")
        usable_climate = usable_climate or (values["temp_avg_f"] is not None and values["vpd_avg_kpa"] is not None)
        climate.append(
            {
                "schema": _CLIMATE_SOURCE_SCHEMA,
                "observed_at": row["observed_at"],
                "source_row_sha256": source_hash,
                "values": values,
            }
        )
        climate_order.append((observed, source_hash))
    if not climate or not usable_climate:
        raise ValueError("selector context lacks a usable real climate observation")
    if climate_order != sorted(climate_order) or len(set(climate_order)) != len(climate_order):
        raise ValueError("climate source rows must be uniquely ordered")

    forecast: list[dict[str, Any]] = []
    forecast_order: list[tuple[datetime, datetime, str]] = []
    valid_times: set[datetime] = set()
    for index, item in enumerate(forecast_source):
        row = _exact_mapping(
            item,
            frozenset({"schema", "valid_at", "fetched_at", "source_row_sha256", "values"}),
            f"forecast_vintage[{index}]",
        )
        if row["schema"] != _FORECAST_SOURCE_SCHEMA:
            raise ValueError("forecast selector source schema mismatch")
        valid = _parse_canonical_utc_text(row["valid_at"], "forecast.valid_at")
        fetched = _parse_canonical_utc_text(row["fetched_at"], "forecast.fetched_at")
        if fetched > cutoff or valid < cutoff or valid >= boundary + timedelta(hours=24):
            raise ValueError("forecast vintage violates cutoff/horizon")
        source_hash = row["source_row_sha256"]
        if not isinstance(source_hash, str) or not _SHA256_RE.fullmatch(source_hash):
            raise ValueError("forecast.source_row_sha256 must be lowercase SHA-256 hex")
        if valid in valid_times:
            raise ValueError("forecast vintage must contain one as-of row per valid time")
        valid_times.add(valid)
        values = _typed_values(row["values"], _FORECAST_VALUE_FIELDS, "forecast values")
        forecast.append(
            {
                "schema": _FORECAST_SOURCE_SCHEMA,
                "valid_at": row["valid_at"],
                "fetched_at": row["fetched_at"],
                "source_row_sha256": source_hash,
                "values": values,
            }
        )
        forecast_order.append((valid, fetched, source_hash))
    if forecast_order != sorted(forecast_order) or len(set(forecast_order)) != len(forecast_order):
        raise ValueError("forecast source rows must be uniquely ordered")
    return {
        "schema": _CONTEXT_SCHEMA,
        "local_date": local_date,
        "context_cutoff_at": source["context_cutoff_at"],
        "boundary_at": source["boundary_at"],
        "climate_observations": climate,
        "forecast_vintage": forecast,
    }


def freeze_context(
    *,
    local_date: str,
    climate_observations: Sequence[Mapping[str, Any]],
    forecast_vintage: Sequence[Mapping[str, Any]],
    cutoff_at: datetime,
    boundary_at: datetime,
) -> FrozenContext:
    """Freeze only the exact DB-derived, typed selector-context v2 envelope."""
    cutoff = _aware_utc(cutoff_at, "cutoff_at")
    boundary = _aware_utc(boundary_at, "boundary_at")
    if cutoff >= boundary:
        raise ValueError("selector context cutoff must be strictly before the local-day boundary")
    local_date = _canonical_local_date(local_date)
    envelope = _validated_context_envelope(
        {
            "schema": _CONTEXT_SCHEMA,
            "local_date": local_date,
            "context_cutoff_at": _canonical_utc_text(cutoff, "cutoff_at"),
            "boundary_at": _canonical_utc_text(boundary, "boundary_at"),
            "climate_observations": list(climate_observations),
            "forecast_vintage": list(forecast_vintage),
        },
        expected_local_date=local_date,
        expected_cutoff=cutoff,
        expected_boundary=boundary,
    )
    canonical = canonical_request_bytes(envelope)
    digest = hashlib.sha256(canonical).hexdigest()
    records = tuple(envelope["climate_observations"] + envelope["forecast_vintage"])
    return FrozenContext(local_date, cutoff, boundary, records, canonical, digest)


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


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _validate_response(response: ProviderResponse, identity: SelectorIdentity, boundary_at: datetime) -> ProfileId:
    if (
        not isinstance(response.completed_at, datetime)
        or not isinstance(response.provider, str)
        or not isinstance(response.model_identifier, str)
        or not isinstance(response.model_revision, str)
        or not isinstance(response.system_fingerprint, str)
        or not isinstance(response.raw_response, bytes)
    ):
        raise TypeError("malformed")
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
        decoded = json.loads(response.raw_response, object_pairs_hook=_unique_json_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("malformed") from exc
    if not isinstance(decoded, dict) or set(decoded) != {"profile"} or decoded["profile"] not in VALID_PROFILES:
        raise ValueError("invalid_output")
    return decoded["profile"]


def _validated_frozen_records(context: FrozenContext) -> dict[str, Any]:
    if not isinstance(context.canonical_bytes, bytes):
        raise TypeError("frozen selector context canonical bytes must be immutable bytes")
    if (
        not isinstance(context.canonical_sha256, str)
        or not _SHA256_RE.fullmatch(context.canonical_sha256)
        or hashlib.sha256(context.canonical_bytes).hexdigest() != context.canonical_sha256
    ):
        raise ValueError("frozen selector context bytes/hash mismatch; audit and abort")
    cutoff = _aware_utc(context.cutoff_at, "context.cutoff_at")
    boundary = _aware_utc(context.boundary_at, "context.boundary_at")
    local_date = _canonical_local_date(context.local_date)
    if cutoff >= boundary:
        raise ValueError("frozen selector context cutoff must be strictly before boundary")
    try:
        envelope = json.loads(context.canonical_bytes, object_pairs_hook=_unique_json_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("frozen selector context bytes are malformed; audit and abort") from exc
    return _validated_context_envelope(
        envelope,
        expected_local_date=local_date,
        expected_cutoff=cutoff,
        expected_boundary=boundary,
    )


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
    if context.local_date != local_date:
        raise ValueError("selector local_date does not match the DB-frozen context")
    invocation_key = _invocation_key(namespace_uuid, study_id, local_date)
    existing = ledger.get(study_id, local_date)
    if existing is not None:
        expected = (invocation_key, context.canonical_sha256, identity.digest_sha256, local_date)
        actual = (existing.invocation_key, existing.context_sha256, existing.identity_sha256, existing.local_date)
        if actual != expected:
            raise ValueError("same-day idempotent retry changed frozen invocation/context/identity; audit and abort")
        return existing
    now_utc = _aware_utc(now, "now")
    boundary = _aware_utc(context.boundary_at, "context.boundary_at")
    trusted_started_at = _trusted_utc_now()
    if now_utc >= boundary or trusted_started_at >= boundary:
        raise ValueError("missed selector start: no invocation may begin at/after the boundary")
    frozen_context = _validated_frozen_records(context)
    request = canonical_request_bytes(
        {
            # Canonical immutable bytes, not the convenience ``records`` view,
            # are authoritative so a caller cannot mutate context after freeze.
            "context": frozen_context,
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
            locally_completed_at = _trusted_utc_now()
            if not isinstance(response, ProviderResponse) or not isinstance(
                response.raw_response, (bytes, bytearray, memoryview)
            ):
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
                if locally_completed_at >= boundary:
                    raise ValueError("late")
                profile = _validate_response(response, identity, boundary)
                fallback_reason = None
                receipts.append(_attempt_receipt("accepted", response_hash, attempt))
                break
            except (TypeError, ValueError) as exc:
                fallback_reason = str(exc)
                receipts.append(_attempt_receipt(fallback_reason, response_hash, attempt))
                break
        except TimeoutError:
            receipts.append(_attempt_receipt("timeout", None, attempt))
            fallback_reason = "timeout"
        except (ProviderUnavailableError, ConnectionError, OSError):
            receipts.append(_attempt_receipt("provider_unavailable", None, attempt))
            fallback_reason = "provider_unavailable"
    accepted_at = _trusted_utc_now()
    if accepted_at >= boundary:
        # Match the DB's SQLSTATE V2B01 retry contract: after a boundary race,
        # only this source-bound, no-response baseline sentinel can persist.
        profile = "baseline"
        fallback_reason = "boundary_elapsed_before_choice_persist"
        request_hash = context.canonical_sha256
        response_hash = None
        receipts.append(_attempt_receipt("boundary_elapsed_before_choice_persist", None, len(receipts) + 1))
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
        accepted_at=accepted_at,
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
        if (
            choice.profile != "baseline"
            or choice.fallback_reason != "boundary_elapsed_before_choice_persist"
            or choice.raw_response_sha256 is not None
            or choice.raw_request_sha256 != choice.context_sha256
        ):
            raise ValueError("late selector choice")
        return "baseline"
    if physical_arm == "A":
        return "baseline"
    if physical_arm == "B":
        return choice.profile
    raise ValueError("physical_arm must be A or B")
