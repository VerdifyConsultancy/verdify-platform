"""Pure, non-networked tests for live ESPHome grid evidence."""

from __future__ import annotations

import json
import struct
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from aioesphomeapi import NumberInfo

from verdify_schemas.component_executor import CANONICAL_FIELD_ORDER, ENTITY_GRIDS
from verdify_schemas.component_qualification import (
    ComponentGridEvidenceError,
    RuntimeEntityMetadata,
    build_live_entity_grid_evidence,
)
from verdify_schemas.tunable_registry import REGISTRY

NOW = datetime(2026, 8, 26, 1, 2, 3, 456789, tzinfo=UTC)
SOURCE_REVISION = "a" * 40
RUNTIME_INSTANCE_ID = "11111111-1111-4111-8111-111111111111"


def test_pinned_aioesphomeapi_canonicalizes_protobuf_binary32_number_metadata() -> None:
    binary32_005 = struct.unpack("!f", struct.pack("!f", 0.05))[0]
    binary32_01 = struct.unpack("!f", struct.pack("!f", 0.1))[0]
    assert binary32_005 != 0.05
    assert binary32_01 != 0.1
    entity = NumberInfo(min_value=binary32_005, max_value=binary32_01, step=binary32_005)
    assert entity.min_value == 0.05
    assert entity.max_value == 0.1
    assert entity.step == 0.05


def exact_runtime_inventory() -> tuple[RuntimeEntityMetadata, ...]:
    entities: list[RuntimeEntityMetadata] = []
    key = 100
    for field_name in CANONICAL_FIELD_ORDER:
        definition = REGISTRY[field_name]
        grid = ENTITY_GRIDS[field_name]
        key += 1
        entities.append(
            RuntimeEntityMetadata(
                object_id=definition.esp_object_id,
                entity_type=grid.entity_type,
                key=key,
                minimum=grid.minimum,
                maximum=grid.maximum,
                step=grid.step,
                assumed_state=False if grid.entity_type == "switch" else None,
            )
        )
        key += 1
        entities.append(
            RuntimeEntityMetadata(
                object_id=definition.cfg_readback_object_id,
                entity_type="sensor",
                key=key,
            )
        )
    return tuple(entities)


def build(entities: tuple[RuntimeEntityMetadata, ...] | None = None):
    return build_live_entity_grid_evidence(
        exact_runtime_inventory() if entities is None else entities,
        device_id="vallery/greenhouse-controller",
        firmware_revision="2026.7.10.1500.09ee886",
        source_revision=SOURCE_REVISION,
        runtime_instance_id=RUNTIME_INSTANCE_ID,
        connection_generation=7,
        observed_at=NOW,
    )


def test_exact_runtime_inventory_produces_stable_content_and_connection_receipt() -> None:
    evidence = build()
    assert evidence.field_count == 48
    assert evidence.grid_revision == f"live-entity-grid-v1:sha256:{evidence.grid_content_sha256}"
    assert len(evidence.grid_content_sha256) == 64
    assert len(evidence.observation_receipt_sha256) == 64

    content = json.loads(evidence.grid_content_json)
    receipt = json.loads(evidence.observation_receipt_json)
    assert [row["field_name"] for row in content["fields"]] == list(CANONICAL_FIELD_ORDER)
    assert [row["field_name"] for row in receipt["routes"]] == list(CANONICAL_FIELD_ORDER)
    assert "firmware_revision" not in content
    assert content["field_count"] == 48
    assert receipt["firmware_revision"] == "2026.7.10.1500.09ee886"
    assert receipt["source_revision"] == SOURCE_REVISION
    assert receipt["connection_generation"] == 7
    assert receipt["observed_at"] == "2026-08-26T01:02:03.456789Z"

    # Enumeration order is runtime noise. Canonical field order and keys make
    # the same authenticated inventory byte-identical when supplied reversed.
    assert build(tuple(reversed(exact_runtime_inventory()))) == evidence

    # Firmware is an independently fenced deployed revision. It belongs in
    # the observation receipt, not the semantic identity of an unchanged grid.
    other_firmware = build_live_entity_grid_evidence(
        exact_runtime_inventory(),
        device_id="vallery/greenhouse-controller",
        firmware_revision="2026.8.26.0000.abcdef0",
        source_revision=SOURCE_REVISION,
        runtime_instance_id=RUNTIME_INSTANCE_ID,
        connection_generation=7,
        observed_at=NOW,
    )
    assert other_firmware.grid_revision == evidence.grid_revision
    assert other_firmware.observation_receipt_sha256 != evidence.observation_receipt_sha256


def test_numeric_metadata_is_exact_decimal_grid_not_wire_rounding() -> None:
    inventory = list(exact_runtime_inventory())
    setter_id = REGISTRY["min_fog_on_s"].esp_object_id
    index = next(
        index for index, row in enumerate(inventory) if row.object_id == setter_id and row.entity_type == "number"
    )
    inventory[index] = RuntimeEntityMetadata(
        object_id=setter_id,
        entity_type="number",
        key=inventory[index].key,
        minimum=Decimal("15"),
        maximum=Decimal("300"),
        step=Decimal("1"),
    )
    with pytest.raises(ComponentGridEvidenceError, match="component_number_grid_mismatch"):
        build(tuple(inventory))


def test_protobuf_binary32_grid_values_match_exact_bits_and_serialize_source_decimals() -> None:
    inventory = []
    for row in exact_runtime_inventory():
        if row.entity_type != "number":
            inventory.append(row)
            continue
        inventory.append(
            replace(
                row,
                minimum=struct.unpack("!f", struct.pack("!f", float(row.minimum)))[0],
                maximum=struct.unpack("!f", struct.pack("!f", float(row.maximum)))[0],
                step=struct.unpack("!f", struct.pack("!f", float(row.step)))[0],
            )
        )
    evidence = build(tuple(inventory))
    assert evidence.grid_revision == build().grid_revision
    fields = {row["field_name"]: row for row in json.loads(evidence.grid_content_json)["fields"]}
    assert fields["direct_wet_stress_vpd_margin_kpa"]["setter"]["step"] == "0.05"

    # One adjacent binary32 value is a mismatch; there is no ULP/tolerance
    # window beyond the exact representation the protobuf field can carry.
    target = next(
        index
        for index, row in enumerate(inventory)
        if row.object_id == REGISTRY["direct_wet_stress_vpd_margin_kpa"].esp_object_id and row.entity_type == "number"
    )
    row = inventory[target]
    step_bits = int.from_bytes(struct.pack("!f", float(row.step)), "big")
    inventory[target] = replace(row, step=struct.unpack("!f", (step_bits + 1).to_bytes(4, "big"))[0])
    with pytest.raises(ComponentGridEvidenceError, match="component_number_grid_mismatch"):
        build(tuple(inventory))


@pytest.mark.parametrize("mutation", ["missing", "wrong_type", "duplicate_route"])
def test_missing_mistyped_or_duplicated_runtime_routes_fail_closed(mutation: str) -> None:
    inventory = list(exact_runtime_inventory())
    target = next(
        index
        for index, row in enumerate(inventory)
        if row.object_id == REGISTRY["mister_all_kpa"].esp_object_id and row.entity_type == "number"
    )
    if mutation == "missing":
        inventory.pop(target)
    elif mutation == "wrong_type":
        row = inventory[target]
        inventory[target] = RuntimeEntityMetadata(object_id=row.object_id, entity_type="sensor", key=row.key)
    else:
        row = inventory[target]
        inventory.append(replace(row, key=999999))
    with pytest.raises(ComponentGridEvidenceError):
        build(tuple(inventory))


def test_source_and_runtime_lineage_are_required_for_receipt() -> None:
    with pytest.raises(ComponentGridEvidenceError, match="invalid_source_revision"):
        build_live_entity_grid_evidence(
            exact_runtime_inventory(),
            device_id="vallery/greenhouse-controller",
            firmware_revision="2026.7.10.1500.09ee886",
            source_revision="unknown",
            runtime_instance_id=RUNTIME_INSTANCE_ID,
            connection_generation=7,
            observed_at=NOW,
        )


def test_switch_metadata_is_not_coerced_into_evidence() -> None:
    inventory = list(exact_runtime_inventory())
    setter_id = REGISTRY["sw_dwell_gate_enabled"].esp_object_id
    index = next(
        index for index, row in enumerate(inventory) if row.object_id == setter_id and row.entity_type == "switch"
    )
    inventory[index] = replace(inventory[index], assumed_state=None)
    with pytest.raises(ComponentGridEvidenceError, match="component_switch_assumed_state_missing"):
        build(tuple(inventory))


def test_entity_keys_are_scoped_by_esphome_entity_type_not_globally() -> None:
    inventory = list(exact_runtime_inventory())
    number = next(row for row in inventory if row.entity_type == "number")
    sensor_index = next(index for index, row in enumerate(inventory) if row.entity_type == "sensor")
    inventory[sensor_index] = replace(inventory[sensor_index], key=number.key)
    evidence = build(tuple(inventory))
    assert evidence.field_count == 48


def test_required_routes_on_child_device_fail_closed() -> None:
    inventory = list(exact_runtime_inventory())
    setter_id = REGISTRY["min_fog_on_s"].esp_object_id
    index = next(
        index for index, row in enumerate(inventory) if row.object_id == setter_id and row.entity_type == "number"
    )
    inventory[index] = replace(inventory[index], device_id=1)
    with pytest.raises(ComponentGridEvidenceError, match="component_entity_device_mismatch"):
        build(tuple(inventory))
