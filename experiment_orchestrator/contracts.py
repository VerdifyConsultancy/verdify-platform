"""Typed, pure contracts for the non-device experiment-v2 workers.

Every externally sourced document is accepted only in one canonical byte form
and is bound to a lower-case SHA-256 digest.  The context contract is a narrow
positive schema over real climate and forecast rows; arbitrary strings and
generic JSON records are intentionally not accepted.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

type JsonScalar = None | bool | int | float | str
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
Profile = Literal["baseline", "moderate", "aggressive"]

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
UTC_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")
VALID_PROFILES: tuple[Profile, ...] = ("baseline", "moderate", "aggressive")
CONTEXT_SCHEMA = "verdify-selector-context-v2"
CLIMATE_SOURCE_SCHEMA = "verdify-selector-climate-source-v1"
FORECAST_SOURCE_SCHEMA = "verdify-selector-forecast-source-v1"
SELECTOR_IDENTITY_SCHEMA = "verdify-selector-identity-v2"
OPENAI_SELECTOR_IDENTITY_SCHEMA = "verdify-selector-identity-openai-v1"
SELECTOR_RESPONSE_SCHEMA = "verdify-selector-response-v2"
OPENAI_SELECTOR_RESPONSE_SCHEMA = "verdify-selector-decision-openai-v1"
OUTCOME_PAYLOAD_SCHEMA = "verdify-assigned-day-outcome-v2"
OUTCOME_IDENTITY_SCHEMA = "verdify-experiment-v2-outcome-evaluator-identity-v1"
LIFECYCLE_PLAN_SCHEMA = "verdify-experiment-v2-lifecycle-plan-v1"
MAX_SELECTOR_CONTEXT_BYTES = 8 * 1024 * 1024

CLIMATE_VALUE_FIELDS = frozenset(
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

FORECAST_VALUE_FIELDS = frozenset(
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

_FORBIDDEN_FIELD_NAMES = frozenset(
    {
        "arm",
        "physical_arm",
        "assignment",
        "assignment_arm",
        "blinded_label",
        "mapping",
        "schedule",
        "secret",
        "password",
        "credential",
        "authorization",
        "cookie",
        "token",
        "api_key",
        "comparative",
        "comparative_outcome",
        "outcome",
        "efficacy",
        "post_cutoff",
        "online_lesson",
    }
)


class ContractError(ValueError):
    """An input cannot be proven to satisfy the frozen production contract."""


class OrchestratorMode(StrEnum):
    LIFECYCLE = "lifecycle"
    SELECTOR = "selector"
    FREEZER = "freezer"


def require_sha256(value: object, field: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise ContractError(f"{field} must be lower-case SHA-256 hex")
    return value


def require_uuid(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ContractError(f"{field} must be a canonical UUID")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise ContractError(f"{field} must be a canonical UUID") from exc
    if str(parsed) != value:
        raise ContractError(f"{field} must be a canonical UUID")
    return value


def require_local_date(value: object, field: str = "local_date") -> str:
    if not isinstance(value, str):
        raise ContractError(f"{field} must be canonical YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ContractError(f"{field} must be canonical YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise ContractError(f"{field} must be canonical YYYY-MM-DD")
    return value


def parse_utc_timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not UTC_TIMESTAMP_RE.fullmatch(value):
        raise ContractError(f"{field} must be canonical UTC with six fractional digits")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise ContractError(f"{field} must be a valid UTC timestamp") from exc
    if format_utc_timestamp(parsed) != value:
        raise ContractError(f"{field} must be canonical UTC")
    return parsed


def format_utc_timestamp(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ContractError("timestamp must be timezone-aware")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _normalized_field_name(key: str) -> str:
    camel_split = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key)
    return re.sub(r"[^a-z0-9]+", "_", camel_split.lower()).strip("_")


def _forbidden_field(key: str) -> bool:
    normalized = _normalized_field_name(key)
    if normalized in _FORBIDDEN_FIELD_NAMES:
        return True
    tokens = normalized.split("_")
    return (
        any(
            token in {"arm", "mapping", "secret", "password", "credential", "authorization", "cookie", "token"}
            for token in tokens
        )
        or "api_key" in normalized
        or "post_cutoff" in normalized
        or "online_lesson" in normalized
    )


def _validate_json(value: object, path: tuple[str, ...] = (), *, reject_forbidden_fields: bool = True) -> None:
    if value is None or type(value) in (bool, int):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ContractError(f"non-finite number at {'.'.join(path) or '<root>'}")
        return
    if isinstance(value, str):
        if unicodedata.normalize("NFC", value) != value:
            raise ContractError(f"non-NFC string at {'.'.join(path) or '<root>'}")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ContractError(f"non-string object key at {'.'.join(path) or '<root>'}")
            if unicodedata.normalize("NFC", key) != key:
                raise ContractError(f"non-NFC key at {'.'.join(path + (key,))}")
            if reject_forbidden_fields and _forbidden_field(key):
                raise ContractError(f"forbidden field at {'.'.join(path + (key,))}")
            _validate_json(item, path + (key,), reject_forbidden_fields=reject_forbidden_fields)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray, memoryview)):
        for index, item in enumerate(value):
            _validate_json(item, path + (str(index),), reject_forbidden_fields=reject_forbidden_fields)
        return
    raise ContractError(f"unsupported JSON value at {'.'.join(path) or '<root>'}")


def canonical_json_bytes(value: Mapping[str, Any], *, reject_forbidden_fields: bool = True) -> bytes:
    _validate_json(value, reject_forbidden_fields=reject_forbidden_fields)
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_sha256(value: Mapping[str, Any], *, domain: str | None = None) -> str:
    payload = canonical_json_bytes(value)
    if domain is not None:
        if not domain.isascii() or not domain:
            raise ContractError("hash domain must be nonempty ASCII")
        payload = domain.encode("ascii") + b"\x00" + payload
    return hashlib.sha256(payload).hexdigest()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"duplicate JSON field {key!r}")
        result[key] = value
    return result


def parse_canonical_document(
    raw: bytes,
    expected_sha256: str,
    *,
    reject_forbidden_fields: bool = True,
) -> dict[str, Any]:
    if type(raw) is not bytes:
        raise ContractError("canonical document must be immutable bytes")
    require_sha256(expected_sha256, "document_sha256")
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ContractError("canonical document hash mismatch")
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError("canonical document is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ContractError("canonical document root must be an object")
    if canonical_json_bytes(value, reject_forbidden_fields=reject_forbidden_fields) != raw:
        raise ContractError("document is not in canonical byte form")
    return value


def parse_hash_bound_document(
    raw: bytes,
    expected_sha256: str,
    *,
    expected_payload: Mapping[str, Any] | None = None,
    reject_forbidden_fields: bool = True,
) -> dict[str, Any]:
    """Parse bytes whose canonical spelling is owned by an attested DB function.

    PostgreSQL's deliberately stored ``jsonb::text`` bytes are authoritative
    for selector source evidence; Python must not re-spell floating-point
    numbers and call that equivalent.  We bind the exact bytes, reject duplicate
    or forbidden structure, and optionally require equality with the separately
    returned jsonb value without reserialization.
    """

    if type(raw) is not bytes:
        raise ContractError("hash-bound document must be immutable bytes")
    require_sha256(expected_sha256, "document_sha256")
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ContractError("hash-bound document hash mismatch")
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError("hash-bound document is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ContractError("hash-bound document root must be an object")
    _validate_json(value, reject_forbidden_fields=reject_forbidden_fields)
    if expected_payload is not None and value != dict(expected_payload):
        raise ContractError("hash-bound bytes differ from returned structured payload")
    return value


def _exact_object(value: object, fields: frozenset[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ContractError(f"{label} must contain exactly {sorted(fields)}")
    return value


def _finite_or_none(value: object, field: str) -> float | None:
    if value is None:
        return None
    if type(value) not in (int, float):
        raise ContractError(f"{field} must be a finite number or null")
    try:
        normalized = float(value)
    except OverflowError as exc:
        raise ContractError(f"{field} must be a finite number or null") from exc
    if not math.isfinite(normalized):
        raise ContractError(f"{field} must be a finite number or null")
    return normalized


OPENAI_SELECTOR_REQUEST_PLACEHOLDER = "{{VERDIFY_DAILY_SELECTOR_REQUEST_V2}}"
OPENAI_SELECTOR_RESPONSE_FORMAT: dict[str, JsonValue] = {
    "type": "json_schema",
    "json_schema": {
        "name": "verdify_selector_decision",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "profile": {"type": "string", "enum": list(VALID_PROFILES)},
            },
            "required": ["profile"],
        },
    },
}


def _bounded_nfc_text(value: object, field: str, maximum_bytes: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > maximum_bytes
        or unicodedata.normalize("NFC", value) != value
    ):
        raise ContractError(f"{field} must be bounded nonempty NFC text")
    return value


def openai_messages_template_bytes(system_message: str, prompt: str) -> bytes:
    """Canonical frozen message template, before the DB-owned context is inserted."""

    _bounded_nfc_text(system_message, "selector system_message", 32_768)
    _bounded_nfc_text(prompt, "selector prompt", 32_768)
    value = [
        {"content": system_message, "role": "system"},
        {
            "content": f"{prompt}\n\n{OPENAI_SELECTOR_REQUEST_PLACEHOLDER}",
            "role": "user",
        },
    ]
    _validate_json(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _validate_openai_decoding_parameters(value: object) -> dict[str, JsonValue]:
    decoding = _exact_object(
        value,
        frozenset(
            {
                "chat_template_kwargs",
                "max_tokens",
                "response_format",
                "stream",
                "temperature",
            }
        ),
        "OpenAI decoding parameters",
    )
    if decoding["stream"] is not False:
        raise ContractError("OpenAI selector streaming must be disabled")
    if type(decoding["max_tokens"]) is not int or not 512 <= decoding["max_tokens"] <= 16_384:
        raise ContractError("OpenAI selector max_tokens must be an integer in [512,16384]")
    if type(decoding["temperature"]) not in (int, float) or decoding["temperature"] != 0:
        raise ContractError("OpenAI selector temperature must be exactly 0")
    if decoding["chat_template_kwargs"] != {"reasoning_effort": "medium"}:
        raise ContractError("OpenAI selector reasoning_effort must be exactly medium")
    if decoding["response_format"] != OPENAI_SELECTOR_RESPONSE_FORMAT:
        raise ContractError("OpenAI selector response_format differs from the locked schema")
    normalized = dict(decoding)
    _validate_json(normalized)
    return normalized


@dataclass(frozen=True)
class ClimateSourceRecord:
    observed_at: datetime
    source_row_sha256: str
    values: Mapping[str, float | None]

    @classmethod
    def from_mapping(cls, value: object) -> ClimateSourceRecord:
        row = _exact_object(
            value,
            frozenset({"schema", "observed_at", "source_row_sha256", "values"}),
            "climate source row",
        )
        if row["schema"] != CLIMATE_SOURCE_SCHEMA:
            raise ContractError("climate source schema mismatch")
        observed_text = row["observed_at"]
        observed_at = parse_utc_timestamp(observed_text, "climate.observed_at")
        values = _exact_object(row["values"], CLIMATE_VALUE_FIELDS, "climate values")
        normalized = {field: _finite_or_none(values[field], f"climate.values.{field}") for field in values}
        # The enclosing context was created/stored by the attested SQL source
        # function. Its exact bytes and hash are authoritative because jsonb
        # number spelling is not safely reproducible by Python.
        supplied_hash = require_sha256(row["source_row_sha256"], "climate.source_row_sha256")
        return cls(observed_at, supplied_hash, normalized)


@dataclass(frozen=True)
class ForecastSourceRecord:
    valid_at: datetime
    fetched_at: datetime
    source_row_sha256: str
    values: Mapping[str, float | None]

    @classmethod
    def from_mapping(cls, value: object) -> ForecastSourceRecord:
        row = _exact_object(
            value,
            frozenset({"schema", "valid_at", "fetched_at", "source_row_sha256", "values"}),
            "forecast source row",
        )
        if row["schema"] != FORECAST_SOURCE_SCHEMA:
            raise ContractError("forecast source schema mismatch")
        valid_text = row["valid_at"]
        fetched_text = row["fetched_at"]
        valid_at = parse_utc_timestamp(valid_text, "forecast.valid_at")
        fetched_at = parse_utc_timestamp(fetched_text, "forecast.fetched_at")
        values = _exact_object(row["values"], FORECAST_VALUE_FIELDS, "forecast values")
        normalized = {field: _finite_or_none(values[field], f"forecast.values.{field}") for field in values}
        supplied_hash = require_sha256(row["source_row_sha256"], "forecast.source_row_sha256")
        return cls(valid_at, fetched_at, supplied_hash, normalized)


@dataclass(frozen=True)
class SelectorContext:
    local_date: str
    context_cutoff_at: datetime
    boundary_at: datetime
    climate_observations: tuple[ClimateSourceRecord, ...]
    forecast_vintage: tuple[ForecastSourceRecord, ...]
    canonical_bytes: bytes
    canonical_sha256: str

    @classmethod
    def parse(
        cls,
        raw: bytes,
        expected_sha256: str,
        *,
        expected_payload: Mapping[str, Any] | None = None,
    ) -> SelectorContext:
        if type(raw) is not bytes or len(raw) > MAX_SELECTOR_CONTEXT_BYTES:
            raise ContractError("selector context exceeds the locked byte bound")
        payload = parse_hash_bound_document(raw, expected_sha256, expected_payload=expected_payload)
        context = _exact_object(
            payload,
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
            "selector context",
        )
        if context["schema"] != CONTEXT_SCHEMA:
            raise ContractError("selector context schema mismatch")
        local_date = require_local_date(context["local_date"])
        cutoff = parse_utc_timestamp(context["context_cutoff_at"], "context_cutoff_at")
        boundary = parse_utc_timestamp(context["boundary_at"], "boundary_at")
        if cutoff >= boundary:
            raise ContractError("context cutoff must be strictly before boundary")
        climate_payload = context["climate_observations"]
        forecast_payload = context["forecast_vintage"]
        if not isinstance(climate_payload, list) or not isinstance(forecast_payload, list):
            raise ContractError("selector context source collections must be arrays")
        climate = tuple(ClimateSourceRecord.from_mapping(item) for item in climate_payload)
        forecast = tuple(ForecastSourceRecord.from_mapping(item) for item in forecast_payload)
        if not climate or not any(
            row.values["temp_avg_f"] is not None and row.values["vpd_avg_kpa"] is not None for row in climate
        ):
            raise ContractError("selector context lacks a usable real climate observation")
        climate_order = [(row.observed_at, row.source_row_sha256) for row in climate]
        if climate_order != sorted(climate_order) or len(set(climate_order)) != len(climate_order):
            raise ContractError("climate source rows must be uniquely ordered")
        if any(row.observed_at > cutoff for row in climate):
            raise ContractError("selector context contains post-cutoff climate data")
        forecast_order = [(row.valid_at, row.fetched_at, row.source_row_sha256) for row in forecast]
        if forecast_order != sorted(forecast_order) or len(set(forecast_order)) != len(forecast_order):
            raise ContractError("forecast source rows must be uniquely ordered")
        if any(
            row.fetched_at > cutoff or row.valid_at < cutoff or row.valid_at >= boundary + timedelta(hours=24)
            for row in forecast
        ):
            raise ContractError("forecast vintage violates cutoff/horizon")
        by_valid_at: dict[datetime, int] = {}
        for row in forecast:
            by_valid_at[row.valid_at] = by_valid_at.get(row.valid_at, 0) + 1
        if any(count != 1 for count in by_valid_at.values()):
            raise ContractError("forecast vintage must contain one as-of row per valid time")
        return cls(local_date, cutoff, boundary, climate, forecast, raw, expected_sha256)


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
    context_schema_sha256: str
    lesson_snapshot_sha256: str
    runtime_environment_sha256: str
    timeout_milliseconds: int
    max_attempts: int
    transport_protocol: Literal["verdify_selector_v2", "openai_chat_completions"]
    system_message: str | None
    prompt: str | None
    decoding_parameters: Mapping[str, JsonValue] | None
    canonical_bytes: bytes
    canonical_sha256: str

    @classmethod
    def parse(cls, raw: bytes, expected_sha256: str) -> SelectorIdentity:
        payload = parse_canonical_document(raw, expected_sha256)
        common_fields = frozenset(
            {
                "schema",
                "provider",
                "model_identifier",
                "model_revision",
                "expected_system_fingerprint",
                "prompt_sha256",
                "system_message_sha256",
                "messages_sha256",
                "decoding_parameters_sha256",
                "tool_contract_revision",
                "response_schema_revision",
                "context_schema_sha256",
                "lesson_snapshot_sha256",
                "runtime_environment_sha256",
                "timeout_milliseconds",
                "max_attempts",
            }
        )
        schema = payload.get("schema")
        if schema == SELECTOR_IDENTITY_SCHEMA:
            identity = _exact_object(payload, common_fields, "selector identity")
            transport_protocol: Literal["verdify_selector_v2", "openai_chat_completions"] = "verdify_selector_v2"
            system_message = None
            prompt = None
            decoding_parameters = None
        elif schema == OPENAI_SELECTOR_IDENTITY_SCHEMA:
            identity = _exact_object(
                payload,
                common_fields | frozenset({"system_message", "prompt", "decoding_parameters"}),
                "OpenAI selector identity",
            )
            transport_protocol = "openai_chat_completions"
            system_message = _bounded_nfc_text(identity["system_message"], "selector system_message", 32_768)
            prompt = _bounded_nfc_text(identity["prompt"], "selector prompt", 32_768)
            decoding_parameters = _validate_openai_decoding_parameters(identity["decoding_parameters"])
        else:
            raise ContractError("selector identity schema mismatch")
        text_fields = (
            "provider",
            "model_identifier",
            "model_revision",
            "expected_system_fingerprint",
            "tool_contract_revision",
            "response_schema_revision",
        )
        normalized_text: dict[str, str] = {}
        for field in text_fields:
            value = identity[field]
            if not isinstance(value, str) or not value or unicodedata.normalize("NFC", value) != value:
                raise ContractError(f"selector identity {field} must be nonempty NFC text")
            normalized_text[field] = value
        hash_fields = (
            "prompt_sha256",
            "system_message_sha256",
            "messages_sha256",
            "decoding_parameters_sha256",
            "context_schema_sha256",
            "lesson_snapshot_sha256",
            "runtime_environment_sha256",
        )
        hashes = {field: require_sha256(identity[field], field) for field in hash_fields}
        timeout = identity["timeout_milliseconds"]
        attempts = identity["max_attempts"]
        if type(timeout) is not int or not 1 <= timeout <= 60_000:
            raise ContractError("selector timeout must be an integer in [1,60000]")
        if type(attempts) is not int or not 1 <= attempts <= 3:
            raise ContractError("selector max_attempts must be an integer in [1,3]")
        if transport_protocol == "openai_chat_completions":
            assert system_message is not None and prompt is not None and decoding_parameters is not None
            if normalized_text["provider"] != "cortex-openai":
                raise ContractError("OpenAI selector provider must be cortex-openai")
            if normalized_text["tool_contract_revision"] != "none-v1":
                raise ContractError("OpenAI selector forbids tools")
            if normalized_text["response_schema_revision"] != OPENAI_SELECTOR_RESPONSE_SCHEMA:
                raise ContractError("OpenAI selector response schema revision mismatch")
            actual_prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
            actual_system_hash = hashlib.sha256(system_message.encode("utf-8")).hexdigest()
            actual_messages_hash = hashlib.sha256(openai_messages_template_bytes(system_message, prompt)).hexdigest()
            actual_decoding_hash = hashlib.sha256(canonical_json_bytes(decoding_parameters)).hexdigest()
            expected_hashes = {
                "prompt_sha256": actual_prompt_hash,
                "system_message_sha256": actual_system_hash,
                "messages_sha256": actual_messages_hash,
                "decoding_parameters_sha256": actual_decoding_hash,
            }
            mismatched = sorted(field for field, actual in expected_hashes.items() if hashes[field] != actual)
            if mismatched:
                raise ContractError("OpenAI selector embedded artifact hash mismatch: " + ", ".join(mismatched))
        return cls(
            **normalized_text,
            **hashes,
            timeout_milliseconds=timeout,
            max_attempts=attempts,
            transport_protocol=transport_protocol,
            system_message=system_message,
            prompt=prompt,
            decoding_parameters=decoding_parameters,
            canonical_bytes=raw,
            canonical_sha256=expected_sha256,
        )


@dataclass(frozen=True)
class ProviderResponse:
    profile: Profile
    provider: str
    model_identifier: str
    model_revision: str
    system_fingerprint: str
    completed_at: datetime
    raw_response_sha256: str

    @classmethod
    def parse(cls, raw: bytes, identity: SelectorIdentity, completed_at: datetime) -> ProviderResponse:
        response_hash = hashlib.sha256(raw).hexdigest()
        payload = parse_canonical_document(raw, response_hash)
        response = _exact_object(
            payload,
            frozenset(
                {
                    "schema",
                    "profile",
                    "provider",
                    "model_identifier",
                    "model_revision",
                    "system_fingerprint",
                }
            ),
            "selector response",
        )
        if response["schema"] != SELECTOR_RESPONSE_SCHEMA or response["profile"] not in VALID_PROFILES:
            raise ContractError("selector response schema/profile mismatch")
        expected_identity = (
            identity.provider,
            identity.model_identifier,
            identity.model_revision,
            identity.expected_system_fingerprint,
        )
        actual_identity = (
            response["provider"],
            response["model_identifier"],
            response["model_revision"],
            response["system_fingerprint"],
        )
        if actual_identity != expected_identity:
            raise ContractError("selector response identity mismatch")
        if completed_at.tzinfo is None or completed_at.utcoffset() is None:
            raise ContractError("provider completion time must be timezone-aware")
        return cls(
            profile=response["profile"],
            provider=response["provider"],
            model_identifier=response["model_identifier"],
            model_revision=response["model_revision"],
            system_fingerprint=response["system_fingerprint"],
            completed_at=completed_at.astimezone(UTC),
            raw_response_sha256=response_hash,
        )


@dataclass(frozen=True)
class OutcomePayload:
    temperature_corridor_distance_f: float | None
    vpd_corridor_distance_kpa: float | None
    nine_control_state_minutes: float | None
    climate_missing_reason: str | None
    equipment_missing_reason: str | None
    source_bundle_sha256: str

    def __post_init__(self) -> None:
        require_sha256(self.source_bundle_sha256, "source_bundle_sha256")
        climate_reasons = {
            "source_unavailable",
            "source_contract_invalid",
            "climate_completeness",
        }
        equipment_reasons = {
            "source_unavailable",
            "source_contract_invalid",
            "counter_samples_unavailable",
            "counter_reset_or_wrap",
            "counter_state_reconciliation",
            "direct_state_snapshot_unavailable",
            "direct_state_snapshot_invalid",
        }
        for value, reason, allowed_reasons, label, maximum in (
            (
                self.temperature_corridor_distance_f,
                self.climate_missing_reason,
                climate_reasons,
                "temperature corridor distance",
                None,
            ),
            (
                self.vpd_corridor_distance_kpa,
                self.climate_missing_reason,
                climate_reasons,
                "VPD corridor distance",
                None,
            ),
            (
                self.nine_control_state_minutes,
                self.equipment_missing_reason,
                equipment_reasons,
                "nine-control-state minutes",
                9 * 1_080,
            ),
        ):
            if value is None:
                if reason not in allowed_reasons:
                    raise ContractError(f"{label} null requires a locked missing reason")
                continue
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
                or (maximum is not None and value > maximum)
                or reason is not None
            ):
                raise ContractError(f"{label} is malformed or conflicts with its missing reason")

    def as_mapping(self) -> dict[str, JsonValue]:
        return {
            "climate_missing_reason": self.climate_missing_reason,
            "equipment_missing_reason": self.equipment_missing_reason,
            "nine_control_state_minutes": self.nine_control_state_minutes,
            "schema": OUTCOME_PAYLOAD_SCHEMA,
            "source_bundle_sha256": self.source_bundle_sha256,
            "temperature_corridor_distance_f": self.temperature_corridor_distance_f,
            "vpd_corridor_distance_kpa": self.vpd_corridor_distance_kpa,
        }

    @property
    def canonical_sha256(self) -> str:
        return canonical_sha256(self.as_mapping(), domain="verdify-experiment-v2-outcome-adapter-v1")

    @classmethod
    def missing(cls, *, source_bundle_sha256: str, climate_reason: str, equipment_reason: str) -> OutcomePayload:
        return cls(None, None, None, climate_reason, equipment_reason, source_bundle_sha256)


@dataclass(frozen=True)
class LifecyclePlan:
    """One immutable, phase-explicit scheduler action.

    An operator selects either one idempotent shadow scheduling call or one
    server-clock boundary cycle.  The worker never tries both in a poll and
    never enumerates future assignments from client time.
    """

    experiment_id: str
    action: Literal["shadow_schedule", "boundary"]
    local_date: str | None
    context_cutoff_at: datetime | None
    context_schema_sha256: str | None
    selector_identity_sha256: str | None
    selector_artifact_sha256: str | None
    endpoint_artifact_sha256: str | None
    outcome_schema_sha256: str | None
    canonical_bytes: bytes
    canonical_sha256: str

    @classmethod
    def parse(cls, raw: bytes, expected_sha256: str, experiment_id: str) -> LifecyclePlan:
        payload = parse_canonical_document(
            raw,
            expected_sha256,
            reject_forbidden_fields=False,
        )
        base = frozenset({"schema", "experiment_id", "action"})
        action = payload.get("action")
        if action == "boundary":
            _exact_object(payload, base, "boundary lifecycle plan")
            if payload["schema"] != LIFECYCLE_PLAN_SCHEMA:
                raise ContractError("lifecycle plan schema mismatch")
            bound_experiment = require_uuid(payload["experiment_id"], "plan.experiment_id")
            if bound_experiment != experiment_id:
                raise ContractError("lifecycle plan experiment binding mismatch")
            return cls(
                bound_experiment,
                action,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                raw,
                expected_sha256,
            )
        if action != "shadow_schedule":
            raise ContractError("lifecycle plan action must be shadow_schedule or boundary")
        fields = base | frozenset(
            {
                "local_date",
                "context_cutoff_at",
                "context_schema_sha256",
                "selector_identity_sha256",
                "selector_artifact_sha256",
                "endpoint_artifact_sha256",
                "outcome_schema_sha256",
            }
        )
        plan = _exact_object(payload, fields, "shadow lifecycle plan")
        if plan["schema"] != LIFECYCLE_PLAN_SCHEMA:
            raise ContractError("lifecycle plan schema mismatch")
        bound_experiment = require_uuid(plan["experiment_id"], "plan.experiment_id")
        if bound_experiment != experiment_id:
            raise ContractError("lifecycle plan experiment binding mismatch")
        local_date = require_local_date(plan["local_date"], "plan.local_date")
        cutoff = parse_utc_timestamp(plan["context_cutoff_at"], "plan.context_cutoff_at")
        hashes = {
            field: require_sha256(plan[field], f"plan.{field}")
            for field in (
                "context_schema_sha256",
                "selector_identity_sha256",
                "selector_artifact_sha256",
                "endpoint_artifact_sha256",
                "outcome_schema_sha256",
            )
        }
        return cls(
            bound_experiment,
            action,
            local_date,
            cutoff,
            **hashes,
            canonical_bytes=raw,
            canonical_sha256=expected_sha256,
        )
