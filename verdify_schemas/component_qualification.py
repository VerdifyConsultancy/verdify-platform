"""Canonical evidence for the live confirmed-component ESPHome entity grid.

The ingestor already calls ``list_entities_services`` on its sole authenticated
ESPHome connection before installing its one state subscription.  This module
turns the relevant subset of that inventory into two separate identities:

* a stable grid-content revision, bound to the device ID, wire schema, setter
  object IDs/types/bounds/steps and cfg readback object IDs/types; and
* a per-connection observation receipt, which additionally binds the running
  firmware/source revisions, runtime instance, connection generation,
  observation time and the ephemeral ESPHome entity keys used on that
  connection.

It is deliberately pure: no network, database, ESPHome client, setter or
service imports.  Callers must supply metadata already returned by the existing
authenticated connection.  Unknown, missing, duplicated, mistyped or off-grid
routes fail closed instead of being rounded or inferred.
"""

from __future__ import annotations

import hashlib
import math
import re
import struct
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal
from uuid import UUID

from verdify_schemas.component_executor import CANONICAL_FIELD_ORDER, ENTITY_GRIDS
from verdify_schemas.policy_vector import canonical_json_bytes, wire_manifest_digest
from verdify_schemas.tunable_registry import REGISTRY, WIRE_SCHEMA_VERSION

RuntimeEntityType = Literal["number", "switch", "sensor"]

_SOURCE_REVISION = re.compile(r"^[0-9a-f]{40}$")


class ComponentGridEvidenceError(ValueError):
    """The supplied runtime inventory cannot prove the exact 48-field grid."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class RuntimeEntityMetadata:
    """Non-secret metadata from one existing ESPHome entity-list response."""

    object_id: str
    entity_type: RuntimeEntityType
    key: int
    device_id: int = 0
    disabled_by_default: bool = False
    minimum: float | Decimal | None = None
    maximum: float | Decimal | None = None
    step: float | Decimal | None = None
    unit: str = ""
    assumed_state: bool | None = None


@dataclass(frozen=True)
class LiveEntityGridEvidence:
    """Stable grid identity plus its source/connection observation receipt."""

    grid_revision: str
    grid_content_sha256: str
    grid_content_json: bytes
    observation_receipt_sha256: str
    observation_receipt_json: bytes
    field_count: int
    firmware_revision: str
    source_revision: str
    runtime_instance_id: str
    connection_generation: int
    observed_at: datetime


def _nfc_text(value: object, field_name: str, *, maximum: int = 200) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ComponentGridEvidenceError("invalid_evidence_text", field_name)
    if unicodedata.normalize("NFC", value) != value:
        raise ComponentGridEvidenceError("non_nfc_evidence_text", field_name)
    return value


def _decimal_text(value: object, field_name: str) -> str:
    if isinstance(value, bool):
        raise ComponentGridEvidenceError("invalid_entity_grid_number", field_name)
    if isinstance(value, float) and not math.isfinite(value):
        raise ComponentGridEvidenceError("invalid_entity_grid_number", field_name)
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ComponentGridEvidenceError("invalid_entity_grid_number", field_name) from exc
    if not number.is_finite():
        raise ComponentGridEvidenceError("invalid_entity_grid_number", field_name)
    text = format(number.normalize(), "f")
    return "0" if text in {"-0", "-0.0"} else text


def _float32_bytes(value: object, field_name: str) -> bytes:
    """Return the exact protobuf-float representation of one grid value.

    ESPHome declares NumberInfo min/max/step as protobuf ``float`` (IEEE-754
    binary32). aioesphomeapi exposes the decoded value as a Python binary64
    float, so decimal-string equality would turn an exact wire value such as
    0.05 into a false mismatch (0.05000000074505806). Equality here is neither
    a tolerance nor candidate-value rounding: the observed value must encode
    to the exact same four bytes as the source value on that transport. After
    equality, the evidence serializes the source canonical decimal, never the
    noisy decoded representation.
    """
    if isinstance(value, bool):
        raise ComponentGridEvidenceError("invalid_entity_grid_number", field_name)
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ComponentGridEvidenceError("invalid_entity_grid_number", field_name) from exc
    if not math.isfinite(numeric):
        raise ComponentGridEvidenceError("invalid_entity_grid_number", field_name)
    try:
        return struct.pack("!f", numeric)
    except (OverflowError, struct.error) as exc:
        raise ComponentGridEvidenceError("invalid_entity_grid_number", field_name) from exc


def _observed_at_text(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ComponentGridEvidenceError("invalid_observation_time")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _index_entities(
    entities: tuple[RuntimeEntityMetadata, ...],
) -> tuple[dict[tuple[str, str], RuntimeEntityMetadata], dict[str, set[str]]]:
    by_route: dict[tuple[str, str], RuntimeEntityMetadata] = {}
    types_by_object_id: dict[str, set[str]] = {}
    # API keys are object-id hashes and are resolved inside a concrete entity
    # type (ESPHome Application::get_<type>_by_key). They are not globally
    # unique across types, so only a same-type collision is invalid here.
    keys: set[tuple[str, int, int]] = set()
    for entity in entities:
        object_id = _nfc_text(entity.object_id, "object_id")
        if entity.entity_type not in {"number", "switch", "sensor"}:
            raise ComponentGridEvidenceError("unsupported_entity_type", object_id)
        if type(entity.disabled_by_default) is not bool:  # noqa: E721 - evidence must not coerce 0/1
            raise ComponentGridEvidenceError("invalid_disabled_by_default", object_id)
        if isinstance(entity.key, bool) or not isinstance(entity.key, int) or entity.key <= 0:
            raise ComponentGridEvidenceError("invalid_entity_key", object_id)
        if isinstance(entity.device_id, bool) or not isinstance(entity.device_id, int) or entity.device_id < 0:
            raise ComponentGridEvidenceError("invalid_entity_device_id", object_id)
        typed_key = (entity.entity_type, entity.device_id, entity.key)
        if typed_key in keys:
            raise ComponentGridEvidenceError(
                "duplicate_entity_key",
                f"{entity.entity_type}:{entity.device_id}:{entity.key}",
            )
        keys.add(typed_key)
        route = (entity.entity_type, object_id)
        if route in by_route:
            raise ComponentGridEvidenceError("duplicate_entity_route", f"{entity.entity_type}:{object_id}")
        by_route[route] = entity
        types_by_object_id.setdefault(object_id, set()).add(entity.entity_type)
    return by_route, types_by_object_id


def _required_route(
    by_route: dict[tuple[str, str], RuntimeEntityMetadata],
    types_by_object_id: dict[str, set[str]],
    *,
    object_id: str,
    entity_type: RuntimeEntityType,
    field_name: str,
    route_kind: str,
) -> RuntimeEntityMetadata:
    route = by_route.get((entity_type, object_id))
    if route is not None:
        # The current executor intentionally invokes aioesphomeapi commands
        # without a device_id, whose exact default is the primary device (0).
        # Do not attest a child-device route that this writer cannot address.
        if route.device_id != 0:
            raise ComponentGridEvidenceError(
                "component_entity_device_mismatch",
                f"{field_name}:{route_kind}:{object_id}:expected=0:observed={route.device_id}",
            )
        return route
    observed_types = sorted(types_by_object_id.get(object_id, set()))
    if observed_types:
        raise ComponentGridEvidenceError(
            "component_entity_type_mismatch",
            f"{field_name}:{route_kind}:{object_id}:expected={entity_type}:observed={','.join(observed_types)}",
        )
    raise ComponentGridEvidenceError(
        "component_entity_missing",
        f"{field_name}:{route_kind}:{entity_type}:{object_id}",
    )


def build_live_entity_grid_evidence(
    entities: tuple[RuntimeEntityMetadata, ...],
    *,
    device_id: str,
    firmware_revision: str,
    source_revision: str,
    runtime_instance_id: str,
    connection_generation: int,
    observed_at: datetime,
) -> LiveEntityGridEvidence:
    """Validate and canonically identify the actual 48-field runtime grid."""
    device_id = _nfc_text(device_id, "device_id")
    firmware_revision = _nfc_text(firmware_revision, "firmware_revision")
    if not isinstance(source_revision, str) or _SOURCE_REVISION.fullmatch(source_revision) is None:
        raise ComponentGridEvidenceError("invalid_source_revision")
    try:
        UUID(runtime_instance_id)
    except (TypeError, ValueError, AttributeError) as exc:
        raise ComponentGridEvidenceError("invalid_runtime_instance_id") from exc
    if (
        isinstance(connection_generation, bool)
        or not isinstance(connection_generation, int)
        or connection_generation < 1
    ):
        raise ComponentGridEvidenceError("invalid_connection_generation")
    observed_at_text = _observed_at_text(observed_at)

    by_route, types_by_object_id = _index_entities(entities)
    content_fields: list[dict[str, object]] = []
    receipt_routes: list[dict[str, object]] = []
    for field_name in CANONICAL_FIELD_ORDER:
        definition = REGISTRY[field_name]
        expected_grid = ENTITY_GRIDS[field_name]
        if definition.esp_object_id is None or definition.cfg_readback_object_id is None:
            raise ComponentGridEvidenceError("source_component_route_missing", field_name)
        if definition.wire_id is None:
            raise ComponentGridEvidenceError("source_component_wire_id_missing", field_name)

        setter = _required_route(
            by_route,
            types_by_object_id,
            object_id=definition.esp_object_id,
            entity_type=expected_grid.entity_type,
            field_name=field_name,
            route_kind="setter",
        )
        # Three boolean tunables publish their current state on the template
        # switch itself.  For those exact same-slug switch routes the setter is
        # also the readback; inventing a parallel sensor route would make the
        # shipped firmware impossible to attest.  Every other field retains
        # the independent cfg_* sensor requirement.
        if expected_grid.entity_type == "switch" and (definition.cfg_readback_object_id == definition.esp_object_id):
            readback = setter
        else:
            readback = _required_route(
                by_route,
                types_by_object_id,
                object_id=definition.cfg_readback_object_id,
                entity_type="sensor",
                field_name=field_name,
                route_kind="readback",
            )

        setter_body: dict[str, object] = {
            "device_id": setter.device_id,
            "disabled_by_default": bool(setter.disabled_by_default),
            "entity_type": setter.entity_type,
            "object_id": setter.object_id,
            "unit": _nfc_text(setter.unit, "setter_unit", maximum=100) if setter.unit else "",
        }
        if expected_grid.entity_type == "number":
            if setter.minimum is None or setter.maximum is None or setter.step is None:
                raise ComponentGridEvidenceError("component_number_grid_missing", field_name)
            expected = (
                _decimal_text(expected_grid.minimum, f"{field_name}:expected_minimum"),
                _decimal_text(expected_grid.maximum, f"{field_name}:expected_maximum"),
                _decimal_text(expected_grid.step, f"{field_name}:expected_step"),
            )
            observed_binary32 = (
                _float32_bytes(setter.minimum, f"{field_name}:minimum"),
                _float32_bytes(setter.maximum, f"{field_name}:maximum"),
                _float32_bytes(setter.step, f"{field_name}:step"),
            )
            expected_binary32 = (
                _float32_bytes(expected_grid.minimum, f"{field_name}:expected_minimum"),
                _float32_bytes(expected_grid.maximum, f"{field_name}:expected_maximum"),
                _float32_bytes(expected_grid.step, f"{field_name}:expected_step"),
            )
            if observed_binary32 != expected_binary32:
                actual_grid = (
                    _decimal_text(setter.minimum, f"{field_name}:minimum"),
                    _decimal_text(setter.maximum, f"{field_name}:maximum"),
                    _decimal_text(setter.step, f"{field_name}:step"),
                )
                raise ComponentGridEvidenceError(
                    "component_number_grid_mismatch",
                    f"{field_name}:expected={expected}:observed={actual_grid}",
                )
            setter_body.update({"maximum": expected[1], "minimum": expected[0], "step": expected[2]})
        else:
            if any(value is not None for value in (setter.minimum, setter.maximum, setter.step)):
                raise ComponentGridEvidenceError("component_switch_has_numeric_grid", field_name)
            if type(setter.assumed_state) is not bool:  # noqa: E721 - missing metadata must fail closed
                raise ComponentGridEvidenceError("component_switch_assumed_state_missing", field_name)
            setter_body["assumed_state"] = setter.assumed_state

        readback_body = {
            "device_id": readback.device_id,
            "disabled_by_default": bool(readback.disabled_by_default),
            "entity_type": readback.entity_type,
            "object_id": readback.object_id,
            "unit": _nfc_text(readback.unit, "readback_unit", maximum=100) if readback.unit else "",
        }
        content_fields.append(
            {
                "field_name": field_name,
                "readback": readback_body,
                "setter": setter_body,
                "wire_id": definition.wire_id,
            }
        )
        receipt_routes.append(
            {
                "field_name": field_name,
                "readback_device_id": readback.device_id,
                "readback_key": readback.key,
                "setter_device_id": setter.device_id,
                "setter_key": setter.key,
                "wire_id": definition.wire_id,
            }
        )

    grid_content = {
        "device_id": device_id,
        "field_count": len(content_fields),
        "fields": content_fields,
        "schema": "verdify-live-entity-grid-v1",
        "wire_manifest_sha256": wire_manifest_digest().hex(),
        "wire_schema_version": WIRE_SCHEMA_VERSION,
    }
    grid_content_json = canonical_json_bytes(grid_content)
    grid_content_sha256 = hashlib.sha256(grid_content_json).hexdigest()
    grid_revision = f"live-entity-grid-v1:sha256:{grid_content_sha256}"
    receipt = {
        "connection_generation": connection_generation,
        "firmware_revision": firmware_revision,
        "grid_content_sha256": grid_content_sha256,
        "grid_revision": grid_revision,
        "observed_at": observed_at_text,
        "routes": receipt_routes,
        "runtime_instance_id": runtime_instance_id,
        "schema": "verdify-live-entity-grid-observation-v1",
        "source_revision": source_revision,
    }
    observation_receipt_json = canonical_json_bytes(receipt)
    observation_receipt_sha256 = hashlib.sha256(observation_receipt_json).hexdigest()
    return LiveEntityGridEvidence(
        grid_revision=grid_revision,
        grid_content_sha256=grid_content_sha256,
        grid_content_json=grid_content_json,
        observation_receipt_sha256=observation_receipt_sha256,
        observation_receipt_json=observation_receipt_json,
        field_count=len(content_fields),
        firmware_revision=firmware_revision,
        source_revision=source_revision,
        runtime_instance_id=runtime_instance_id,
        connection_generation=connection_generation,
        observed_at=observed_at.astimezone(UTC),
    )


__all__ = [
    "ComponentGridEvidenceError",
    "LiveEntityGridEvidence",
    "RuntimeEntityMetadata",
    "build_live_entity_grid_evidence",
]
