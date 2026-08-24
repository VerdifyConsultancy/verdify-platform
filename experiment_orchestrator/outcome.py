"""Strict, source-bound adapter around the locked protocol-v2 outcome evaluator."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import sys
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .contracts import (
    OUTCOME_IDENTITY_SCHEMA,
    ContractError,
    OutcomePayload,
    parse_canonical_document,
    parse_hash_bound_document,
    parse_utc_timestamp,
    require_local_date,
    require_sha256,
)

OUTCOME_SOURCE_SCHEMA = "verdify-experiment-v2-outcome-source-bundle-v1"
OUTCOME_CLIMATE_SCHEMA = "verdify-experiment-v2-outcome-climate-source-v1"
MAX_OUTCOME_SOURCE_BYTES = 32 * 1024 * 1024
EQUIPMENT_STREAMS: tuple[str, ...] = (
    "heat1",
    "heat2",
    "vent",
    "fan1",
    "fan2",
    "fog",
    "mister_south",
    "mister_west",
    "mister_center",
)
_MINUTE_NATIVE_STREAMS = frozenset({"heat1", "heat2", "vent", "fan1", "fan2", "fog"})
_HOUR_NATIVE_STREAMS = frozenset({"mister_south", "mister_west", "mister_center"})
EQUIPMENT_SOURCE_MAP_REVISION = "combined-normal-fertilized-misters-v1"
EQUIPMENT_SOURCE_MAP_SHA256 = "5c790584da6a99eed70421514fda4bf2a79aabbccd91ae1f4fe6e0c4fc3d3048"
EQUIPMENT_COMPONENTS: dict[str, tuple[str, ...]] = {
    **{stream: (stream,) for stream in EQUIPMENT_STREAMS},
    "mister_south": ("mister_south", "mister_south_fert"),
    "mister_west": ("mister_west", "mister_west_fert"),
}
SELECTOR_SOURCE_FAILURES = frozenset(
    {
        "source_relation_unavailable",
        "no_usable_precutoff_climate_source",
        "conflicting_latest_forecast_vintage",
    }
)


def _exact(value: object, fields: frozenset[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ContractError(f"{label} shape mismatch")
    return value


def _uuid(value: object, field: str) -> str:
    if not isinstance(value, str):
        value = str(value)
    try:
        parsed = UUID(value)
    except (ValueError, TypeError) as exc:
        raise ContractError(f"{field} must be a UUID") from exc
    if str(parsed) != value:
        raise ContractError(f"{field} must be a canonical UUID")
    return value


def _aware(value: object, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ContractError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _finite(value: object, field: str, *, nullable: bool = False) -> float | None:
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(f"{field} must be finite")
    try:
        normalized = float(value)
    except OverflowError as exc:
        raise ContractError(f"{field} must be finite") from exc
    if not math.isfinite(normalized):
        raise ContractError(f"{field} must be finite")
    return normalized


def _nonempty_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or unicodedata.normalize("NFC", value) != value or len(value) > 512:
        raise ContractError(f"{field} must be bounded nonempty NFC text")
    return value


def _postgres_jsonb_text(value: object) -> str:
    """Spell the restricted receipt JSON exactly like PostgreSQL ``jsonb::text``.

    Receipt events contain only objects, arrays, booleans, and strings. JSONB
    orders object keys by UTF-8 byte length and then byte value, and separates
    members with one space. Keeping this adapter deliberately narrow lets the
    freezer verify the DB-owned ``events_sha256`` without normalizing away a
    byte-level mismatch.
    """

    if value is None:
        return "null"
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) is int:
        return str(value)
    if isinstance(value, str):
        if unicodedata.normalize("NFC", value) != value:
            raise ContractError("receipt JSON string is not NFC")
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(_postgres_jsonb_text(item) for item in value) + "]"
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ContractError("receipt JSON object key is malformed")
        ordered = sorted(value, key=lambda key: (len(key.encode("utf-8")), key.encode("utf-8")))
        return (
            "{"
            + ", ".join(f"{json.dumps(key, ensure_ascii=False)}: {_postgres_jsonb_text(value[key])}" for key in ordered)
            + "}"
        )
    raise ContractError("receipt JSON contains an unsupported value")


@lru_cache(maxsize=1)
def load_locked_evaluator() -> tuple[ModuleType, str]:
    candidates = (
        Path(__file__).with_name("_frozen_v2_outcomes.py"),
        Path(__file__).resolve().parents[1] / "research" / "planner-efficacy" / "switchback" / "v2_outcomes.py",
    )
    source_path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if source_path is None:
        raise ContractError("locked outcome evaluator source is unavailable")
    raw = source_path.read_bytes()
    source_sha256 = hashlib.sha256(raw).hexdigest()
    spec = importlib.util.spec_from_file_location("_verdify_frozen_v2_outcomes", source_path)
    if spec is None or spec.loader is None:
        raise ContractError("locked outcome evaluator cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        sys.modules.pop(spec.name, None)
        raise ContractError("locked outcome evaluator cannot be loaded") from exc
    return module, source_sha256


@dataclass(frozen=True)
class OutcomeIdentity:
    outcome_schema_sha256: str
    evaluator_source_sha256: str
    temperature_duplicate_tolerance_f: float
    vpd_duplicate_tolerance_kpa: float
    canonical_sha256: str

    @classmethod
    def parse(cls, raw: bytes, endpoint_artifact_sha256: str) -> OutcomeIdentity:
        payload = parse_canonical_document(
            raw,
            endpoint_artifact_sha256,
            reject_forbidden_fields=False,
        )
        identity = _exact(
            payload,
            frozenset(
                {
                    "schema",
                    "outcome_schema_sha256",
                    "evaluator_source_sha256",
                    "temperature_duplicate_tolerance_f",
                    "vpd_duplicate_tolerance_kpa",
                }
            ),
            "outcome identity",
        )
        if identity["schema"] != OUTCOME_IDENTITY_SCHEMA:
            raise ContractError("outcome identity schema mismatch")
        outcome_schema = require_sha256(identity["outcome_schema_sha256"], "outcome_schema_sha256")
        evaluator_sha = require_sha256(identity["evaluator_source_sha256"], "evaluator_source_sha256")
        temperature_tolerance = _finite(
            identity["temperature_duplicate_tolerance_f"],
            "temperature_duplicate_tolerance_f",
        )
        vpd_tolerance = _finite(
            identity["vpd_duplicate_tolerance_kpa"],
            "vpd_duplicate_tolerance_kpa",
        )
        assert temperature_tolerance is not None and vpd_tolerance is not None
        if not 0 <= temperature_tolerance <= 1 or not 0 <= vpd_tolerance <= 0.1:
            raise ContractError("outcome duplicate tolerances exceed locked bounds")
        return cls(
            outcome_schema,
            evaluator_sha,
            temperature_tolerance,
            vpd_tolerance,
            endpoint_artifact_sha256,
        )


def load_outcome_identity(path: Path | None, endpoint_artifact_sha256: str) -> OutcomeIdentity | None:
    if path is None:
        return None
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if len(raw) > 65_536:
        return None
    try:
        identity = OutcomeIdentity.parse(raw, endpoint_artifact_sha256)
        _module, source_sha256 = load_locked_evaluator()
    except ContractError:
        return None
    if identity.evaluator_source_sha256 != source_sha256:
        return None
    return identity


@dataclass(frozen=True)
class OutcomeSourceCandidate:
    source_kind: Literal["shadow", "randomized"]
    subject_id: str
    local_date: str
    timezone: str
    window_start_at: datetime
    window_end_at: datetime
    outcome_schema_sha256: str
    endpoint_artifact_sha256: str
    source_bundle_canonical: bytes
    source_bundle_sha256: str
    delivery_failed: bool
    fallback_used: bool
    facility_rescue: bool
    resolved_at: datetime

    @classmethod
    def from_row(cls, raw: Mapping[str, Any]) -> OutcomeSourceCandidate:
        required = frozenset(
            {
                "source_kind",
                "subject_id",
                "local_date",
                "timezone",
                "window_start_at",
                "window_end_at",
                "outcome_schema_sha256",
                "endpoint_artifact_sha256",
                "source_bundle_canonical",
                "source_bundle_sha256",
                "delivery_failed",
                "fallback_used",
                "facility_rescue",
                "resolved_at",
            }
        )
        _exact(raw, required, "outcome source cycle result")
        source_kind = raw["source_kind"]
        if source_kind not in ("shadow", "randomized"):
            raise ContractError("outcome source kind is invalid")
        local_raw = raw["local_date"]
        if isinstance(local_raw, datetime):
            raise ContractError("local_date must not contain a time")
        local_date = local_raw.isoformat() if isinstance(local_raw, date) else require_local_date(local_raw)
        timezone = _nonempty_text(raw["timezone"], "timezone")
        if timezone != "America/Denver":
            raise ContractError("outcome source timezone differs from the locked protocol")
        try:
            ZoneInfo(timezone)
        except ZoneInfoNotFoundError as exc:
            raise ContractError("outcome source timezone is unknown") from exc
        window_start = _aware(raw["window_start_at"], "window_start_at")
        window_end = _aware(raw["window_end_at"], "window_end_at")
        resolved = _aware(raw["resolved_at"], "resolved_at")
        if window_start >= window_end or resolved < window_end + timedelta(minutes=5):
            raise ContractError("outcome source server window is not elapsed and settled")
        bytes_raw = raw["source_bundle_canonical"]
        if not isinstance(bytes_raw, (bytes, bytearray, memoryview)):
            raise ContractError("outcome source canonical bundle must be bytes")
        flags: dict[str, bool] = {}
        for field in ("delivery_failed", "fallback_used", "facility_rescue"):
            if type(raw[field]) is not bool:
                raise ContractError(f"{field} must be an exact boolean")
            flags[field] = raw[field]
        return cls(
            source_kind=source_kind,
            subject_id=_uuid(raw["subject_id"], "subject_id"),
            local_date=local_date,
            timezone=timezone,
            window_start_at=window_start,
            window_end_at=window_end,
            outcome_schema_sha256=require_sha256(raw["outcome_schema_sha256"], "outcome_schema_sha256"),
            endpoint_artifact_sha256=require_sha256(raw["endpoint_artifact_sha256"], "endpoint_artifact_sha256"),
            source_bundle_canonical=bytes(bytes_raw),
            source_bundle_sha256=require_sha256(raw["source_bundle_sha256"], "source_bundle_sha256"),
            resolved_at=resolved,
            **flags,
        )


def _local_window(candidate: OutcomeSourceCandidate) -> tuple[datetime, datetime]:
    parsed = date.fromisoformat(candidate.local_date)
    zone = ZoneInfo(candidate.timezone)
    start = datetime.combine(parsed, time(6, 0), tzinfo=zone)
    end = datetime.combine(parsed + timedelta(days=1), time(0, 0), tzinfo=zone)
    if start.utcoffset() != end.utcoffset() or (end - start).total_seconds() != 64_800:
        raise ContractError("outcome window crosses a UTC-offset transition")
    if start.astimezone(UTC) != candidate.window_start_at or end.astimezone(UTC) != candidate.window_end_at:
        raise ContractError("outcome source row/local window mismatch")
    return start, end


def _parse_bundle(candidate: OutcomeSourceCandidate) -> Mapping[str, Any]:
    raw = candidate.source_bundle_canonical
    if len(raw) > MAX_OUTCOME_SOURCE_BYTES:
        raise ContractError("outcome source bundle exceeds locked byte bound")
    payload = parse_hash_bound_document(
        raw,
        candidate.source_bundle_sha256,
    )
    bundle = _exact(
        payload,
        frozenset(
            {
                "schema",
                "source_kind",
                "subject_id",
                "local_date",
                "timezone",
                "window_start_at",
                "window_end_at",
                "revision_bundle_sha256",
                "outcome_schema_sha256",
                "endpoint_artifact_sha256",
                "analyzer_environment_sha256",
                "climate_observations",
                "corridors",
                "equipment_streams",
                "equipment_ingestion_receipt_chain",
                "equipment_source_map_revision",
                "equipment_source_map_sha256",
                "delivery_failed",
                "fallback_used",
                "facility_rescue",
                "selector_context_status",
                "selector_failure_reason",
            }
        ),
        "outcome source bundle",
    )
    expected = {
        "schema": OUTCOME_SOURCE_SCHEMA,
        "source_kind": candidate.source_kind,
        "subject_id": candidate.subject_id,
        "local_date": candidate.local_date,
        "timezone": candidate.timezone,
        "window_start_at": candidate.window_start_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "window_end_at": candidate.window_end_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "outcome_schema_sha256": candidate.outcome_schema_sha256,
        "endpoint_artifact_sha256": candidate.endpoint_artifact_sha256,
        "delivery_failed": candidate.delivery_failed,
        "fallback_used": candidate.fallback_used,
        "facility_rescue": candidate.facility_rescue,
    }
    for field, value in expected.items():
        if bundle[field] != value:
            raise ContractError(f"outcome source bundle {field} binding mismatch")
    require_sha256(bundle["revision_bundle_sha256"], "revision_bundle_sha256")
    if (
        bundle["equipment_source_map_revision"] != EQUIPMENT_SOURCE_MAP_REVISION
        or bundle["equipment_source_map_sha256"] != EQUIPMENT_SOURCE_MAP_SHA256
    ):
        raise ContractError("equipment source map identity mismatch")
    selector_status = bundle["selector_context_status"]
    selector_failure = bundle["selector_failure_reason"]
    if candidate.source_kind == "randomized":
        if selector_status is not None or selector_failure is not None:
            raise ContractError("randomized outcome source cannot expose selector context status")
    elif selector_status == "frozen":
        if selector_failure is not None:
            raise ContractError("frozen shadow selector context cannot have a failure reason")
    elif selector_status == "unavailable":
        if selector_failure not in SELECTOR_SOURCE_FAILURES:
            raise ContractError("unavailable shadow selector context has an invalid failure reason")
    else:
        raise ContractError("shadow outcome source selector context status is invalid")
    analyzer = bundle["analyzer_environment_sha256"]
    if candidate.source_kind == "shadow":
        if analyzer is not None:
            raise ContractError("shadow outcome source cannot claim a locked analyzer environment")
    else:
        require_sha256(analyzer, "analyzer_environment_sha256")
    _local_window(candidate)
    return bundle


def _climate_result(
    bundle: Mapping[str, Any],
    candidate: OutcomeSourceCandidate,
    identity: OutcomeIdentity,
    evaluator: ModuleType,
) -> tuple[float | None, float | None, str | None]:
    rows = bundle["climate_observations"]
    corridors_raw = bundle["corridors"]
    if not isinstance(rows, list) or not isinstance(corridors_raw, list):
        raise ContractError("outcome climate/corridor sources must be arrays")
    samples = []
    ordering: list[tuple[datetime, str]] = []
    climate_values = frozenset(
        {
            "temp_avg_f",
            "temp_east_f",
            "temp_north_f",
            "temp_south_f",
            "temp_west_f",
            "vpd_avg_kpa",
            "vpd_east_kpa",
            "vpd_north_kpa",
            "vpd_south_kpa",
            "vpd_west_kpa",
        }
    )
    for item in rows:
        row = _exact(
            item,
            frozenset({"schema", "observed_at", "source_row_sha256", "values"}),
            "outcome climate row",
        )
        if row["schema"] != OUTCOME_CLIMATE_SCHEMA:
            raise ContractError("outcome climate row schema mismatch")
        observed = parse_utc_timestamp(row["observed_at"], "climate.observed_at")
        source_hash = require_sha256(row["source_row_sha256"], "climate.source_row_sha256")
        values = _exact(row["values"], climate_values, "outcome climate values")
        normalized = {field: _finite(values[field], f"climate.values.{field}", nullable=True) for field in values}
        if normalized["temp_avg_f"] is None or normalized["vpd_avg_kpa"] is None:
            raise ContractError("outcome climate row lacks the locked aggregate values")
        if not candidate.window_start_at <= observed < candidate.window_end_at:
            raise ContractError("outcome climate row is outside the locked window")
        ordering.append((observed, source_hash))
        samples.append(evaluator.ClimateSample(observed, normalized["temp_avg_f"], normalized["vpd_avg_kpa"]))
    if ordering != sorted(ordering) or len(ordering) != len(set(ordering)):
        raise ContractError("outcome climate rows are not uniquely ordered")

    expected_bucket = candidate.window_start_at
    corridors: dict[datetime, Any] = {}
    for index, item in enumerate(corridors_raw):
        row = _exact(
            item,
            frozenset(
                {
                    "bucket_start",
                    "temperature_high_f",
                    "temperature_low_f",
                    "vpd_high_kpa",
                    "vpd_low_kpa",
                }
            ),
            "outcome corridor row",
        )
        bucket = parse_utc_timestamp(row["bucket_start"], "corridor.bucket_start")
        if bucket != expected_bucket + timedelta(minutes=15 * index):
            raise ContractError("outcome corridors are not the exact ordered 15-minute grid")
        values = [row[field] for field in ("temperature_low_f", "temperature_high_f", "vpd_low_kpa", "vpd_high_kpa")]
        if all(value is None for value in values):
            continue
        if any(value is None for value in values):
            raise ContractError("outcome corridor row is partially missing")
        temperature_low, temperature_high, vpd_low, vpd_high = (_finite(value, "corridor value") for value in values)
        assert None not in (temperature_low, temperature_high, vpd_low, vpd_high)
        if temperature_low > temperature_high or vpd_low > vpd_high:
            raise ContractError("outcome corridor low exceeds high")
        corridors[bucket] = evaluator.Corridor(temperature_low, temperature_high, vpd_low, vpd_high)
    if len(corridors_raw) != 72:
        raise ContractError("outcome source must contain exactly 72 corridor slots")
    try:
        bins = evaluator.climate_bins(
            samples,
            local_date=candidate.local_date,
            timezone=candidate.timezone,
            corridors=corridors,
            temperature_duplicate_tolerance_f=identity.temperature_duplicate_tolerance_f,
            vpd_duplicate_tolerance_kpa=identity.vpd_duplicate_tolerance_kpa,
        )
        return evaluator.daily_climate_outcome(bins)
    except (TypeError, ValueError) as exc:
        raise ContractError("locked climate evaluator rejected the source") from exc


@dataclass(frozen=True)
class _Lineage:
    runtime: str
    generation: int
    firmware: str
    uptime: float
    observed_at: datetime


def _lineage(row: Mapping[str, Any], prefix: str) -> _Lineage:
    generation = row["source_connection_generation"]
    if type(generation) is not int or generation < 1 or generation > 9_007_199_254_740_991:
        raise ContractError(f"{prefix} connection generation is malformed")
    uptime = _finite(row["device_uptime_seconds"], f"{prefix}.device_uptime_seconds")
    assert uptime is not None
    if not 0 <= uptime <= 1_000_000_000:
        raise ContractError(f"{prefix} uptime is outside the source contract")
    return _Lineage(
        _uuid(row["source_runtime_instance_id"], f"{prefix}.source_runtime_instance_id"),
        generation,
        _nonempty_text(row["firmware_revision"], f"{prefix}.firmware_revision"),
        uptime,
        parse_utc_timestamp(row["source_observed_at"], f"{prefix}.source_observed_at"),
    )


class EquipmentMissing(ContractError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class _ReceiptEvent:
    equipment: str
    observed_at: datetime
    state: bool


@dataclass(frozen=True)
class _IngestionReceipt:
    receipt_id: str
    receipt_sha256: str
    source_sequence: int
    source_observed_through: datetime
    recorded_at: datetime
    runtime: str
    generation: int
    firmware: str
    events: tuple[_ReceiptEvent, ...]


@dataclass(frozen=True)
class _ReceiptChain:
    coverage_start_at: datetime
    coverage_end_at: datetime
    receipts: tuple[_IngestionReceipt, ...]
    runtime: str
    generation: int
    firmware: str


_RECEIPT_GAP_REASONS = frozenset(
    {
        "initial_receipt",
        "collector_reported_gap",
        "source_time_gap",
        "connection_generation_change",
        "firmware_revision_change",
        "nonmonotonic_barrier",
    }
)
_RECEIPT_HASH_DOMAIN = "verdify-equipment-state-source-receipt-v2"
_RECEIPT_GREENHOUSE_ID = "vallery"
_RECEIPT_DEVICE_ID = "esp32:vallery"
_RECEIPT_EVENT_STREAMS = frozenset(
    {
        "fan1",
        "fan2",
        "vent",
        "fog",
        "heat1",
        "heat2",
        "mister_south",
        "mister_west",
        "mister_center",
        "mister_any",
        "mister_south_fert",
        "mister_west_fert",
        "drip_wall",
        "drip_center",
        "drip_wall_fert",
        "drip_center_fert",
        "fert_master_valve",
        "water_flowing",
        "leak_detected",
        "gl1",
        "gl2",
        "grow_light",
        "grow_light_main",
        "grow_light_grow",
        "dehum",
        "safety_dehum",
        "occupancy",
        "door_open",
        "fan_burst_active",
        "fog_burst_active",
        "vent_bypass_active",
        "occupancy_quiet_override_active",
        "sntp_status",
        "mister_budget_exceeded",
        "economiser_blocked",
        "heap_pressure_warning",
        "heap_pressure_critical",
        "economiser_enabled",
        "fog_closes_vent",
        "gl_auto_mode",
        "irrigation_enabled",
        "irrigation_wall_enabled",
        "irrigation_center_enabled",
        "irrigation_weather_skip",
        "occupancy_inhibit",
    }
)


def _receipt_hash_preimage(
    *,
    receipt_id: str,
    source_observed_through: str,
    source_sequence: int,
    previous_receipt_sha256: str | None,
    gap_requested: bool,
    gap_before: bool,
    gap_reason: str | None,
    firmware_revision: str,
    event_count: int,
    events_sha256: str,
    recorded_at: str,
    runtime_instance_id: str,
    connection_generation: int,
) -> bytes:
    """Reconstruct the exact PG15 ``jsonb_build_object(...)::text`` preimage."""

    return _postgres_jsonb_text(
        {
            "device_id": _RECEIPT_DEVICE_ID,
            "domain": _RECEIPT_HASH_DOMAIN,
            "event_count": event_count,
            "events_sha256": events_sha256,
            "firmware_revision": firmware_revision,
            "gap_before": gap_before,
            "gap_reason": gap_reason,
            "gap_requested": gap_requested,
            "greenhouse_id": _RECEIPT_GREENHOUSE_ID,
            "previous_receipt_sha256": previous_receipt_sha256,
            "receipt_id": receipt_id,
            "recorded_at": recorded_at,
            "source_connection_generation": connection_generation,
            "source_observed_through": source_observed_through,
            "source_runtime_instance_id": runtime_instance_id,
            "source_sequence": source_sequence,
        }
    ).encode("utf-8")


def _ingestion_receipt_chain(
    bundle: Mapping[str, Any],
    candidate: OutcomeSourceCandidate,
) -> _ReceiptChain:
    chain = _exact(
        bundle["equipment_ingestion_receipt_chain"],
        frozenset(
            {
                "schema",
                "maximum_source_barrier_gap_seconds",
                "coverage_start_at",
                "coverage_end_at",
                "receipts",
            }
        ),
        "equipment ingestion receipt chain",
    )
    if (
        chain["schema"] != "verdify-equipment-state-receipt-chain-v1"
        or type(chain["maximum_source_barrier_gap_seconds"]) is not int
        or chain["maximum_source_barrier_gap_seconds"] != 60
    ):
        raise ContractError("equipment receipt chain identity is invalid")
    coverage_start = parse_utc_timestamp(
        chain["coverage_start_at"],
        "equipment receipt chain coverage_start_at",
    )
    coverage_end = parse_utc_timestamp(
        chain["coverage_end_at"],
        "equipment receipt chain coverage_end_at",
    )
    if (
        not candidate.window_start_at - timedelta(seconds=90) <= coverage_start <= candidate.window_start_at
        or coverage_end != candidate.window_end_at
    ):
        raise ContractError("equipment receipt chain coverage binding is invalid")
    raw_receipts = chain["receipts"]
    if not isinstance(raw_receipts, list) or not 2 <= len(raw_receipts) <= 5_000:
        raise ContractError("equipment receipt chain is empty or unbounded")

    receipt_fields = frozenset(
        {
            "connection_generation",
            "event_count",
            "events",
            "events_sha256",
            "firmware_revision",
            "gap_before",
            "gap_reason",
            "gap_requested",
            "previous_receipt_sha256",
            "receipt_id",
            "receipt_sha256",
            "recorded_at",
            "runtime_instance_id",
            "source_observed_through",
            "source_sequence",
        }
    )
    event_fields = frozenset({"equipment", "source_observed_at", "state"})
    parsed: list[_IngestionReceipt] = []
    previous_hash: str | None = None
    previous_sequence: int | None = None
    previous_barrier: datetime | None = None
    previous_recorded: datetime | None = None
    expected_lineage: tuple[str, int, str] | None = None
    for index, item in enumerate(raw_receipts):
        receipt = _exact(item, receipt_fields, "equipment ingestion receipt")
        receipt_id = _uuid(receipt["receipt_id"], "equipment receipt id")
        receipt_sha = require_sha256(receipt["receipt_sha256"], "equipment receipt sha256")
        sequence = receipt["source_sequence"]
        generation = receipt["connection_generation"]
        event_count = receipt["event_count"]
        if (
            type(sequence) is not int
            or not 1 <= sequence <= 9_007_199_254_740_991
            or type(generation) is not int
            or not 1 <= generation <= 9_007_199_254_740_991
            or type(event_count) is not int
            or not 0 <= event_count <= 10_000
        ):
            raise ContractError("equipment receipt integer field is malformed")
        runtime = _uuid(receipt["runtime_instance_id"], "equipment receipt runtime")
        firmware = _nonempty_text(receipt["firmware_revision"], "equipment receipt firmware")
        lineage = (runtime, generation, firmware)
        if expected_lineage is None:
            expected_lineage = lineage
        elif lineage != expected_lineage:
            raise EquipmentMissing("counter_reset_or_wrap")

        barrier = parse_utc_timestamp(
            receipt["source_observed_through"],
            "equipment receipt source_observed_through",
        )
        recorded = parse_utc_timestamp(receipt["recorded_at"], "equipment receipt recorded_at")
        if recorded > candidate.resolved_at or recorded + timedelta(seconds=5) < barrier:
            raise ContractError("equipment receipt server/source chronology is invalid")
        if previous_recorded is not None and recorded < previous_recorded:
            raise ContractError("equipment receipt server times are not ordered")

        gap_requested = receipt["gap_requested"]
        gap_before = receipt["gap_before"]
        gap_reason = receipt["gap_reason"]
        if type(gap_requested) is not bool or type(gap_before) is not bool:
            raise ContractError("equipment receipt gap flag is malformed")
        if (gap_before and gap_reason not in _RECEIPT_GAP_REASONS) or (not gap_before and gap_reason is not None):
            raise ContractError("equipment receipt gap reason is inconsistent")
        if sequence == 1:
            if not gap_before or gap_reason != "initial_receipt":
                raise ContractError("initial equipment receipt gap is malformed")
        elif gap_requested != (gap_reason == "collector_reported_gap"):
            raise ContractError("equipment receipt collector gap binding is inconsistent")

        predecessor = receipt["previous_receipt_sha256"]
        if sequence == 1:
            if predecessor is not None:
                raise ContractError("first equipment receipt cannot name a predecessor")
        else:
            predecessor = require_sha256(predecessor, "equipment previous receipt sha256")
        if index > 0:
            assert previous_sequence is not None and previous_hash is not None
            assert previous_barrier is not None
            if sequence != previous_sequence + 1 or predecessor != previous_hash:
                raise ContractError("equipment receipt hash chain is broken")
            barrier_gap = barrier - previous_barrier
            if not timedelta(0) < barrier_gap <= timedelta(seconds=60):
                raise ContractError("equipment receipt source barrier gap is invalid")
            if gap_requested or gap_before or gap_reason is not None:
                raise ContractError("equipment receipt chain contains a source gap")

        raw_events = receipt["events"]
        if not isinstance(raw_events, list) or len(raw_events) != event_count:
            raise ContractError("equipment receipt event count differs")
        events_hash = require_sha256(receipt["events_sha256"], "equipment events sha256")
        canonical_events = _postgres_jsonb_text(raw_events).encode("utf-8")
        if hashlib.sha256(canonical_events).hexdigest() != events_hash:
            raise ContractError("equipment receipt event bytes/hash differ")
        expected_receipt_sha = hashlib.sha256(
            _receipt_hash_preimage(
                receipt_id=receipt_id,
                source_observed_through=receipt["source_observed_through"],
                source_sequence=sequence,
                previous_receipt_sha256=predecessor,
                gap_requested=gap_requested,
                gap_before=gap_before,
                gap_reason=gap_reason,
                firmware_revision=firmware,
                event_count=event_count,
                events_sha256=events_hash,
                recorded_at=receipt["recorded_at"],
                runtime_instance_id=runtime,
                connection_generation=generation,
            )
        ).hexdigest()
        if receipt_sha != expected_receipt_sha:
            raise ContractError("equipment receipt content/hash binding differs")
        events: list[_ReceiptEvent] = []
        previous_event_at: datetime | None = None
        for event_item in raw_events:
            event = _exact(event_item, event_fields, "equipment receipt event")
            equipment = _nonempty_text(event["equipment"], "equipment receipt event stream")
            if equipment not in _RECEIPT_EVENT_STREAMS:
                raise ContractError("equipment receipt event stream is outside the ledger contract")
            observed_at = parse_utc_timestamp(
                event["source_observed_at"],
                "equipment receipt event source_observed_at",
            )
            if type(event["state"]) is not bool or observed_at > barrier:
                raise ContractError("equipment receipt event value/window is invalid")
            if index > 0 and previous_barrier is not None and observed_at <= previous_barrier:
                raise ContractError("equipment receipt event precedes its open interval")
            if previous_event_at is not None and observed_at < previous_event_at:
                raise ContractError("equipment receipt events are not ordered")
            previous_event_at = observed_at
            events.append(_ReceiptEvent(equipment, observed_at, event["state"]))

        parsed.append(
            _IngestionReceipt(
                receipt_id,
                receipt_sha,
                sequence,
                barrier,
                recorded,
                runtime,
                generation,
                firmware,
                tuple(events),
            )
        )
        previous_hash = receipt_sha
        previous_sequence = sequence
        previous_barrier = barrier
        previous_recorded = recorded

    assert expected_lineage is not None
    if parsed[0].source_observed_through > coverage_start:
        raise ContractError("equipment receipt anchor does not precede source coverage")
    if parsed[-1].source_observed_through < coverage_end or any(
        receipt.source_observed_through >= coverage_end for receipt in parsed[:-1]
    ):
        raise ContractError("equipment receipt chain terminal barrier is not exact")
    return _ReceiptChain(
        coverage_start,
        coverage_end,
        tuple(parsed),
        expected_lineage[0],
        expected_lineage[1],
        expected_lineage[2],
    )


def _receipt_transition(
    receipt: _IngestionReceipt,
    event: _ReceiptEvent,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "observed_at": event.observed_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "source_receipt_id": receipt.receipt_id,
        "source_receipt_sequence": receipt.source_sequence,
        "source_receipt_sha256": receipt.receipt_sha256,
        "state": event.state,
        "stream": event.equipment,
    }
    source_hash = hashlib.sha256(
        b"verdify-experiment-v2-outcome-state-transition-v1\x00" + _postgres_jsonb_text(payload).encode("utf-8")
    ).hexdigest()
    return {**payload, "source_row_sha256": source_hash}


def _equipment_result(
    bundle: Mapping[str, Any],
    candidate: OutcomeSourceCandidate,
    evaluator: ModuleType,
) -> tuple[float | None, str | None]:
    equipment = _exact(bundle["equipment_streams"], frozenset(EQUIPMENT_STREAMS), "equipment streams")
    receipt_chain = _ingestion_receipt_chain(bundle, candidate)
    seed_fields = frozenset(
        {
            "device_uptime_seconds",
            "firmware_revision",
            "recorded_at",
            "snapshot_id",
            "source_bundle_sha256",
            "source_connection_generation",
            "source_epoch_id",
            "source_observed_at",
            "source_row_sha256",
            "source_runtime_instance_id",
            "state",
            "stream",
        }
    )
    counter_fields = frozenset(
        {
            "counter_reset_epoch_id",
            "counter_value_minutes",
            "device_uptime_seconds",
            "firmware_revision",
            "native_unit",
            "native_value",
            "recorded_at",
            "sample_id",
            "sample_sha256",
            "source_connection_generation",
            "source_observed_at",
            "source_runtime_instance_id",
            "stream",
        }
    )
    transition_fields = frozenset(
        {
            "observed_at",
            "source_receipt_id",
            "source_receipt_sequence",
            "source_receipt_sha256",
            "source_row_sha256",
            "state",
            "stream",
        }
    )
    parsed: dict[str, tuple[list[Any], Any, Any]] = {}
    seed_ids: set[tuple[str, str, str]] = set()
    seed_times: list[datetime] = []
    all_lineages: list[_Lineage] = []
    reset_epochs: set[str] = set()
    for stream in EQUIPMENT_STREAMS:
        source = _exact(
            equipment[stream],
            frozenset(
                {
                    "counter_end",
                    "counter_start",
                    "direct_state_components",
                    "transition_components",
                }
            ),
            f"equipment stream {stream}",
        )
        component_names = EQUIPMENT_COMPONENTS[stream]
        seeds = _exact(
            source["direct_state_components"],
            frozenset(component_names),
            f"{stream} direct state components",
        )
        transition_sources = _exact(
            source["transition_components"],
            frozenset(component_names),
            f"{stream} transition components",
        )
        if any(seeds[component] is None for component in component_names):
            raise EquipmentMissing("direct_state_snapshot_unavailable")
        if source["counter_start"] is None or source["counter_end"] is None:
            raise EquipmentMissing("counter_samples_unavailable")
        start = _exact(source["counter_start"], counter_fields, f"{stream} start counter")
        end = _exact(source["counter_end"], counter_fields, f"{stream} end counter")
        if start["stream"] != stream or end["stream"] != stream:
            raise ContractError("equipment source stream binding mismatch")
        start_lineage = _lineage(start, f"{stream}.counter_start")
        end_lineage = _lineage(end, f"{stream}.counter_end")
        all_lineages.extend((start_lineage, end_lineage))
        if (
            not candidate.window_start_at - timedelta(seconds=90)
            <= start_lineage.observed_at
            <= candidate.window_start_at
        ):
            raise ContractError("start counter is outside its fresh source window")
        if not candidate.window_end_at - timedelta(seconds=90) <= end_lineage.observed_at < candidate.window_end_at:
            raise ContractError("end counter is outside its fresh source window")
        for row, lineage, label in (
            (start, start_lineage, "counter_start"),
            (end, end_lineage, "counter_end"),
        ):
            recorded = parse_utc_timestamp(row["recorded_at"], f"{stream}.{label}.recorded_at")
            if recorded < lineage.observed_at or recorded > candidate.resolved_at:
                raise ContractError("equipment recorded/source time ordering is invalid")
        if start_lineage.uptime > end_lineage.uptime:
            raise EquipmentMissing("counter_reset_or_wrap")
        expected_unit = "minutes" if stream in _MINUTE_NATIVE_STREAMS else "hours"
        maximum_native_value = 1_500.0 if expected_unit == "minutes" else 25.0
        for counter, label in ((start, "start"), (end, "end")):
            _uuid(counter["sample_id"], f"{stream}.{label}.sample_id")
            require_sha256(counter["sample_sha256"], f"{stream}.{label}.sample_sha256")
            reset_epochs.add(_uuid(counter["counter_reset_epoch_id"], f"{stream}.{label}.reset_epoch"))
            if counter["native_unit"] != expected_unit:
                raise ContractError("equipment counter native unit mismatch")
            native_value = _finite(counter["native_value"], f"{stream}.{label}.native_value")
            value_minutes = _finite(counter["counter_value_minutes"], f"{stream}.{label}.counter_value_minutes")
            assert native_value is not None and value_minutes is not None
            expected_minutes = native_value if expected_unit == "minutes" else native_value * 60.0
            if (
                not 0 <= native_value <= maximum_native_value
                or not 0 <= value_minutes <= 1_500.0
                or value_minutes != expected_minutes
            ):
                raise ContractError("equipment counter minutes are malformed")

        component_states: dict[str, bool] = {}
        component_seed_times: dict[str, datetime] = {}
        component_updates: dict[datetime, dict[str, set[bool]]] = {}
        for component in component_names:
            seed = _exact(seeds[component], seed_fields, f"{component} direct seed")
            if seed["stream"] != component or type(seed["state"]) is not bool:
                raise EquipmentMissing("direct_state_snapshot_invalid")
            seed_lineage = _lineage(seed, f"{component}.seed")
            all_lineages.append(seed_lineage)
            if (
                not (
                    candidate.window_start_at - timedelta(seconds=90)
                    <= seed_lineage.observed_at
                    <= candidate.window_start_at
                )
                or seed_lineage.observed_at > start_lineage.observed_at
            ):
                raise EquipmentMissing("direct_state_snapshot_invalid")
            recorded = parse_utc_timestamp(seed["recorded_at"], f"{component}.seed.recorded_at")
            if recorded < seed_lineage.observed_at or recorded > candidate.resolved_at:
                raise ContractError("equipment recorded/source time ordering is invalid")
            if seed_lineage.uptime > start_lineage.uptime:
                raise EquipmentMissing("counter_reset_or_wrap")
            seed_ids.add(
                (
                    _uuid(seed["snapshot_id"], f"{component}.snapshot_id"),
                    _uuid(seed["source_epoch_id"], f"{component}.source_epoch_id"),
                    require_sha256(
                        seed["source_bundle_sha256"],
                        f"{component}.source_bundle_sha256",
                    ),
                )
            )
            seed_times.append(seed_lineage.observed_at)
            require_sha256(seed["source_row_sha256"], f"{component}.source_row_sha256")
            component_states[component] = seed["state"]
            component_seed_times[component] = seed_lineage.observed_at

            transitions_raw = transition_sources[component]
            if not isinstance(transitions_raw, list):
                raise ContractError("equipment transition component must be an array")
            expected_transitions = [
                _receipt_transition(receipt, event)
                for receipt in receipt_chain.receipts
                for event in receipt.events
                if event.equipment == component
                and seed_lineage.observed_at < event.observed_at < candidate.window_end_at
            ]
            expected_transitions.sort(key=lambda row: (str(row["observed_at"]), str(row["source_row_sha256"])))
            transition_order: list[tuple[datetime, str]] = []
            normalized_transitions: list[dict[str, object]] = []
            for item in transitions_raw:
                transition = _exact(item, transition_fields, f"{component} transition")
                if transition["stream"] != component or type(transition["state"]) is not bool:
                    raise ContractError("equipment transition stream/state is malformed")
                observed = parse_utc_timestamp(
                    transition["observed_at"],
                    f"{component}.transition.observed_at",
                )
                source_hash = require_sha256(
                    transition["source_row_sha256"],
                    f"{component}.transition.source_row_sha256",
                )
                if not seed_lineage.observed_at < observed < candidate.window_end_at:
                    raise ContractError("equipment transition is outside the source window")
                transition_order.append((observed, source_hash))
                normalized_transitions.append(dict(transition))
                by_component = component_updates.setdefault(observed, {})
                by_component.setdefault(component, set()).add(transition["state"])
            if transition_order != sorted(transition_order):
                raise ContractError("equipment transitions are not ordered")
            if normalized_transitions != expected_transitions:
                raise ContractError("equipment transitions differ from receipt-bound events")

        if any(len(values) != 1 for by_component in component_updates.values() for values in by_component.values()):
            raise ContractError("equipment component has a conflicting same-time state")
        anchor = max(component_seed_times.values())
        for moment in sorted(component_updates):
            if moment > anchor:
                break
            for component, values in component_updates[moment].items():
                if len(values) != 1:
                    raise ContractError("equipment component has a conflicting same-time state")
                component_states[component] = next(iter(values))
        derived_state = any(component_states.values())
        states = [evaluator.StateObservation(anchor, derived_state)]
        cursor = anchor
        for moment in sorted(component_updates):
            if moment <= anchor:
                continue
            if len(component_names) > 1 and all(component_states.values()) and moment > cursor:
                raise EquipmentMissing("counter_state_reconciliation")
            for component, values in component_updates[moment].items():
                if len(values) != 1:
                    raise ContractError("equipment component has a conflicting same-time state")
                component_states[component] = next(iter(values))
            next_state = any(component_states.values())
            if next_state != derived_state:
                states.append(evaluator.StateObservation(moment, next_state))
                derived_state = next_state
            cursor = moment
        if len(component_names) > 1 and all(component_states.values()) and candidate.window_end_at > cursor:
            raise EquipmentMissing("counter_state_reconciliation")

        start_sample = evaluator.CounterSample(
            start_lineage.observed_at,
            float(start["counter_value_minutes"]),
            str(start["counter_reset_epoch_id"]),
        )
        end_sample = evaluator.CounterSample(
            end_lineage.observed_at,
            float(end["counter_value_minutes"]),
            str(end["counter_reset_epoch_id"]),
        )
        parsed[stream] = (states, start_sample, end_sample)

    if len(seed_ids) != 1 or max(seed_times) - min(seed_times) > timedelta(seconds=60):
        raise EquipmentMissing("direct_state_snapshot_invalid")
    if receipt_chain.coverage_start_at != min(seed_times):
        raise ContractError("equipment receipt coverage does not bind the earliest seed")
    if not all(
        receipt_chain.receipts[0].source_observed_through
        <= seed_time
        <= receipt_chain.receipts[-1].source_observed_through
        for seed_time in seed_times
    ):
        raise ContractError("equipment seeds are outside continuous receipt coverage")
    if len({(row.runtime, row.generation, row.firmware) for row in all_lineages}) != 1:
        raise EquipmentMissing("counter_reset_or_wrap")
    source_lineage = (all_lineages[0].runtime, all_lineages[0].generation, all_lineages[0].firmware)
    if (
        receipt_chain.runtime,
        receipt_chain.generation,
        receipt_chain.firmware,
    ) != source_lineage:
        raise EquipmentMissing("counter_reset_or_wrap")
    ordered_lineage = sorted(all_lineages, key=lambda row: row.observed_at)
    for previous, current in zip(ordered_lineage, ordered_lineage[1:], strict=False):
        if current.uptime < previous.uptime or (
            current.observed_at == previous.observed_at and current.uptime != previous.uptime
        ):
            raise EquipmentMissing("counter_reset_or_wrap")
    if len(reset_epochs) != 1:
        raise EquipmentMissing("counter_reset_or_wrap")

    outcomes = []
    for stream in EQUIPMENT_STREAMS:
        states, start_sample, end_sample = parsed[stream]
        try:
            outcome = evaluator.equipment_stream_outcome(
                stream,
                states,
                local_date=candidate.local_date,
                timezone=candidate.timezone,
                start_counter=start_sample,
                end_counter=end_sample,
            )
        except (TypeError, ValueError) as exc:
            raise ContractError("locked equipment evaluator rejected the source") from exc
        if not outcome.valid:
            if outcome.reason == "counter_state_reconciliation":
                raise EquipmentMissing("counter_state_reconciliation")
            if outcome.reason == "counter_reset_or_wrap":
                raise EquipmentMissing("counter_reset_or_wrap")
            if outcome.reason in {"missing_fresh_direct_state_seed", "missing_state_seed"}:
                raise EquipmentMissing("direct_state_snapshot_invalid")
            raise ContractError("locked equipment evaluator rejected malformed source")
        outcomes.append(outcome)
    try:
        burden, reason = evaluator.nine_stream_burden(outcomes)
    except (TypeError, ValueError) as exc:
        raise ContractError("locked nine-stream evaluator rejected the source") from exc
    if reason is not None or burden is None:
        raise ContractError("locked nine-stream evaluator rejected complete stream set")
    return float(burden), None


def evaluate_outcome(
    candidate: OutcomeSourceCandidate,
    *,
    identity_path: Path | None,
) -> OutcomePayload:
    try:
        bundle = _parse_bundle(candidate)
    except ContractError:
        return OutcomePayload.missing(
            source_bundle_sha256=candidate.source_bundle_sha256,
            climate_reason="source_contract_invalid",
            equipment_reason="source_contract_invalid",
        )
    if candidate.source_kind == "shadow" and bundle["selector_context_status"] == "unavailable":
        return OutcomePayload.missing(
            source_bundle_sha256=candidate.source_bundle_sha256,
            climate_reason="source_unavailable",
            equipment_reason="source_unavailable",
        )
    identity = load_outcome_identity(identity_path, candidate.endpoint_artifact_sha256)
    if identity is None or identity.outcome_schema_sha256 != candidate.outcome_schema_sha256:
        return OutcomePayload.missing(
            source_bundle_sha256=candidate.source_bundle_sha256,
            climate_reason="source_contract_invalid",
            equipment_reason="source_contract_invalid",
        )
    try:
        evaluator, source_sha256 = load_locked_evaluator()
        if source_sha256 != identity.evaluator_source_sha256:
            raise ContractError("outcome evaluator source identity mismatch")
    except ContractError:
        return OutcomePayload.missing(
            source_bundle_sha256=candidate.source_bundle_sha256,
            climate_reason="source_contract_invalid",
            equipment_reason="source_contract_invalid",
        )
    try:
        temperature, vpd, climate_reason = _climate_result(bundle, candidate, identity, evaluator)
    except ContractError:
        temperature = vpd = None
        climate_reason = "source_contract_invalid"
    try:
        equipment, equipment_reason = _equipment_result(bundle, candidate, evaluator)
    except EquipmentMissing as exc:
        equipment = None
        equipment_reason = exc.reason
    except ContractError:
        equipment = None
        equipment_reason = "source_contract_invalid"
    return OutcomePayload(
        temperature,
        vpd,
        equipment,
        climate_reason,
        equipment_reason,
        candidate.source_bundle_sha256,
    )
