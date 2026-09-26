"""Offline grid capture is exact, deterministic and fails closed.

Every fixture here is synthetic.  Nothing in this module opens a socket, reads
a database, imports an ESPHome client or touches a device: the capture core is
pure logic over one JSON artifact, and that is exactly what is asserted.
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import importlib.util
import json
import stat
import struct
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from verdify_schemas.component_executor import CANONICAL_FIELD_ORDER, ENTITY_GRIDS
from verdify_schemas.component_qualification import build_live_entity_grid_evidence
from verdify_schemas.policy_vector import encode_policy_vector, wire_manifest_digest
from verdify_schemas.tunable_registry import REGISTRY, WIRE_SCHEMA_VERSION

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "component_grid_capture.py"
SPEC = importlib.util.spec_from_file_location("component_grid_capture", SCRIPT)
assert SPEC and SPEC.loader
cap = importlib.util.module_from_spec(SPEC)
# Register before exec: @dataclass resolves annotations through
# sys.modules[cls.__module__] (scripts/experiment-verify.py does the same).
sys.modules[SPEC.name] = cap
SPEC.loader.exec_module(cap)

DEVICE_ID = "vallery/greenhouse-controller"
SOURCE_REVISION = "a" * 40
FIRMWARE_REVISION = "2026.7.10.1500.09ee886"
RUNTIME_INSTANCE_ID = "11111111-1111-4111-8111-111111111111"
OBSERVED_AT = datetime(2026, 8, 26, 1, 2, 3, 456789, tzinfo=UTC)
LAYER_AS_OF = cap._timestamp_text(OBSERVED_AT - timedelta(seconds=60))

# #424's own historical table: served/control/observed for the six band series.
# temp/target rows are written coherent so a single mutation isolates one cause.
BAND_VALUES: dict[str, tuple[str, str]] = {
    "temp_low": ("75.19", "°F"),
    "temp_high": ("85.19", "°F"),
    "temp_target": ("80.19", "°F"),
    "vpd_low": ("0.875", "kPa"),
    "vpd_high": ("1.305", "kPa"),
    "vpd_target": ("1.065", "kPa"),
}
BAND_SLUGS: dict[str, str] = {
    "temp_low": "cfg___temp_low___f_",
    "temp_high": "cfg___temp_high___f_",
    "temp_target": "house_temp_target_f",
    "vpd_low": "cfg___vpd_low__kpa_",
    "vpd_high": "cfg___vpd_high__kpa_",
    "vpd_target": "house_vpd_target_kpa",
}


# ──────────────────────────────────────────────────────────────────────────────
# Synthetic fixtures
# ──────────────────────────────────────────────────────────────────────────────


def entity_rows() -> list[dict[str, Any]]:
    """One exact-parity setter + cfg readback pair for each canonical field."""
    rows: list[dict[str, Any]] = []
    key = 100
    for field_name in CANONICAL_FIELD_ORDER:
        definition = REGISTRY[field_name]
        grid = ENTITY_GRIDS[field_name]
        key += 1
        setter: dict[str, Any] = {
            "object_id": definition.esp_object_id,
            "entity_type": grid.entity_type,
            "key": key,
            "device_id": 0,
            "disabled_by_default": False,
            "unit": "",
        }
        if grid.entity_type == "number":
            setter["minimum"] = str(grid.minimum)
            setter["maximum"] = str(grid.maximum)
            setter["step"] = str(grid.step)
        else:
            setter["assumed_state"] = False
        rows.append(setter)
        if grid.entity_type == "switch" and (definition.cfg_readback_object_id == definition.esp_object_id):
            continue
        key += 1
        rows.append(
            {
                "object_id": definition.cfg_readback_object_id,
                "entity_type": "sensor",
                "key": key,
                "device_id": 0,
                "disabled_by_default": False,
                "unit": "",
            }
        )
    return rows


def on_grid_value(field_name: str) -> bool | float | str:
    """A value that is exactly on the deployed grid (its declared minimum)."""
    grid = ENTITY_GRIDS[field_name]
    if grid.entity_type == "switch":
        return False
    assert grid.minimum is not None
    return str(grid.minimum)


def observed_component_rows() -> dict[str, dict[str, Any]]:
    return {
        field_name: {
            "slug": REGISTRY[field_name].cfg_readback_object_id,
            "unit": "",
            "observed_at": LAYER_AS_OF,
            "value": on_grid_value(field_name),
        }
        for field_name in CANONICAL_FIELD_ORDER
    }


def band_layer_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for series, (value, unit) in BAND_VALUES.items():
        rows.append(
            {
                "series": series,
                "served": {
                    "value": value,
                    "unit": unit,
                    "as_of": LAYER_AS_OF,
                    "source": "fn_band_setpoints(now())",
                },
                "control": {
                    "value": value,
                    "unit": unit,
                    "as_of": LAYER_AS_OF,
                    "source": f"Setpoints.{series} in confirmed dispatcher_legacy branch",
                },
                "observed": {
                    "value": value,
                    "unit": unit,
                    "as_of": LAYER_AS_OF,
                    "source": "setpoint_snapshot",
                    "slug": BAND_SLUGS[series],
                },
            }
        )
    return rows


def artifact() -> dict[str, Any]:
    return {
        "schema": cap.INPUT_SCHEMA,
        "device_id": DEVICE_ID,
        "observed_at": cap._timestamp_text(OBSERVED_AT),
        "runtime": {"runtime_instance_id": RUNTIME_INSTANCE_ID, "connection_generation": 7},
        "band_source": {
            "slug": "band_source",
            "value": "dispatcher_legacy",
            "as_of": LAYER_AS_OF,
            "runtime_instance_id": RUNTIME_INSTANCE_ID,
            "connection_generation": 7,
        },
        "revisions": {
            "source_revision": SOURCE_REVISION,
            "firmware_revision": FIRMWARE_REVISION,
            "config_revision": "cfg-2026-08-26-01",
            "registry_revision": "registry-v2",
            "crop_band_resolver_revision": "fn_band_setpoints-mig-181",
            "sensor_registry_revision": "sensors-2026-07",
        },
        "entities": entity_rows(),
        "observed_components": observed_component_rows(),
        "band_layers": band_layer_rows(),
    }


def entity_index(rows: list[dict[str, Any]], object_id: str) -> int:
    return next(index for index, row in enumerate(rows) if row["object_id"] == object_id)


def run(document: dict[str, Any] | None = None) -> Any:
    return cap.capture_from_artifact(artifact() if document is None else document)


def exact_live_grid() -> list[Any]:
    return list(cap.project_live_entity_grid(cap.parse_input_artifact(artifact()).entities))


# ──────────────────────────────────────────────────────────────────────────────
# Exact parity
# ──────────────────────────────────────────────────────────────────────────────


def test_exact_parity_capture_emits_a_qualified_grid_revision() -> None:
    result = run()
    assert result.failures == ()
    assert result.grid_parity_ok is True
    assert result.band_coherence_ok is True
    assert result.observed_state_ok is True
    assert result.qualified is True
    assert cap._QUALIFIED_GRID_REVISION.fullmatch(result.grid_revision) is not None
    assert set(result.observed_start_state) == set(CANONICAL_FIELD_ORDER)
    assert len(result.live_grid) == len(CANONICAL_FIELD_ORDER)
    assert {triple.classification for triple in result.layer_triples} == {"resolved"}
    assert all(triple.coherent for triple in result.layer_triples)


def test_grid_revision_is_the_shipped_runtime_attestation_string_not_a_second_preimage() -> None:
    """The offline revision must be reproducible by the live ingestor attestation.

    ``physical_execution_qualified`` compares the source constant with the
    ingestor's observed revision, so a locally-invented preimage would wedge the
    gate permanently.  This binds the tool to the shipped evidence module.
    """
    parsed = cap.parse_input_artifact(artifact())
    evidence = build_live_entity_grid_evidence(
        parsed.entities,
        device_id=DEVICE_ID,
        firmware_revision=FIRMWARE_REVISION,
        source_revision=SOURCE_REVISION,
        runtime_instance_id=RUNTIME_INSTANCE_ID,
        connection_generation=7,
        observed_at=OBSERVED_AT,
    )
    result = run()
    assert result.grid_revision == evidence.grid_revision
    assert result.grid_content_sha256 == evidence.grid_content_sha256
    assert result.observation_receipt_sha256 == evidence.observation_receipt_sha256


def test_compare_live_grid_accepts_the_exact_source_projection() -> None:
    assert cap.compare_live_grid(exact_live_grid()) == []


def test_binary32_transport_numbers_are_accepted_and_one_ulp_is_not() -> None:
    """JSON numbers decoded off the protobuf float are exact, not tolerated."""
    document = artifact()
    for row in document["entities"]:
        if row["entity_type"] != "number":
            continue
        for label in ("minimum", "maximum", "step"):
            row[label] = struct.unpack("!f", struct.pack("!f", float(row[label])))[0]
    assert run(document).grid_revision == run().grid_revision

    index = entity_index(document["entities"], REGISTRY["direct_wet_stress_vpd_margin_kpa"].esp_object_id)
    bits = int.from_bytes(struct.pack("!f", float(document["entities"][index]["step"])), "big")
    document["entities"][index]["step"] = struct.unpack("!f", (bits + 1).to_bytes(4, "big"))[0]
    result = run(document)
    assert result.grid_revision is None
    assert any(
        failure.startswith("number_grid_mismatch:direct_wet_stress_vpd_margin_kpa:step") for failure in result.failures
    )


# ──────────────────────────────────────────────────────────────────────────────
# Grid parity failure modes
# ──────────────────────────────────────────────────────────────────────────────


def test_missing_field_fails_closed() -> None:
    rows = exact_live_grid()
    del rows[3]
    failures = cap.compare_live_grid(rows)
    assert any(failure.startswith("missing_entity:") for failure in failures)


def test_extra_field_fails_closed() -> None:
    rows = exact_live_grid()
    rows.append(
        cap.LiveEntityGrid(
            field_name="not_a_component",
            esp_object_id="set_not_a_component",
            cfg_readback_object_id="cfg_not_a_component",
            entity_type="number",
            minimum=Decimal("0"),
            maximum=Decimal("1"),
            step=Decimal("1"),
        )
    )
    assert "extra_entity:not_a_component" in cap.compare_live_grid(rows)


def test_duplicated_field_fails_closed() -> None:
    rows = exact_live_grid()
    rows.append(rows[0])
    assert any(failure.startswith("duplicate_entity:") for failure in cap.compare_live_grid(rows))


def test_wrong_entity_type_fails_closed() -> None:
    rows = exact_live_grid()
    index = next(i for i, row in enumerate(rows) if row.field_name == "sw_dwell_gate_enabled")
    rows[index] = cap.LiveEntityGrid(
        field_name="sw_dwell_gate_enabled",
        esp_object_id=rows[index].esp_object_id,
        cfg_readback_object_id=rows[index].cfg_readback_object_id,
        entity_type="number",
        minimum=Decimal("0"),
        maximum=Decimal("1"),
        step=Decimal("1"),
    )
    failures = cap.compare_live_grid(rows)
    assert any(failure.startswith("entity_type_mismatch:sw_dwell_gate_enabled") for failure in failures)


def test_switch_with_a_numeric_grid_fails_closed() -> None:
    rows = exact_live_grid()
    index = next(i for i, row in enumerate(rows) if row.field_name == "sw_summer_vent_enabled")
    rows[index] = cap.LiveEntityGrid(
        field_name="sw_summer_vent_enabled",
        esp_object_id=rows[index].esp_object_id,
        cfg_readback_object_id=rows[index].cfg_readback_object_id,
        entity_type="switch",
        minimum=Decimal("0"),
        maximum=Decimal("1"),
        step=Decimal("1"),
    )
    assert "switch_carries_numeric_grid:sw_summer_vent_enabled" in cap.compare_live_grid(rows)


@pytest.mark.parametrize("route", ["setter", "readback"])
def test_route_slug_mismatch_fails_closed(route: str) -> None:
    rows = exact_live_grid()
    index = next(i for i, row in enumerate(rows) if row.field_name == "mister_all_kpa")
    row = rows[index]
    if route == "setter":
        rows[index] = cap.LiveEntityGrid(
            field_name=row.field_name,
            esp_object_id="set_mister_all_kpa_v2",
            cfg_readback_object_id=row.cfg_readback_object_id,
            entity_type=row.entity_type,
            minimum=row.minimum,
            maximum=row.maximum,
            step=row.step,
        )
        expected = "setter_route_mismatch:mister_all_kpa"
    else:
        rows[index] = cap.LiveEntityGrid(
            field_name=row.field_name,
            esp_object_id=row.esp_object_id,
            cfg_readback_object_id="cfg_mister_all_kpa_v2",
            entity_type=row.entity_type,
            minimum=row.minimum,
            maximum=row.maximum,
            step=row.step,
        )
        expected = "readback_route_mismatch:mister_all_kpa"
    assert any(failure.startswith(expected) for failure in cap.compare_live_grid(rows))


@pytest.mark.parametrize(
    ("field_name", "label", "mutation"),
    [
        # exactly one grid step of drift on each bound — the smallest real
        # deployed-grid change a firmware/YAML edit can produce.
        ("min_fog_on_s", "minimum", "30"),
        ("min_fog_on_s", "maximum", "285"),
        ("min_fog_on_s", "step", "30"),
        ("night_vpd_bias_kpa", "maximum", "0.26"),
        ("direct_wet_stress_vpd_margin_kpa", "step", "0.1"),
    ],
)
def test_one_step_grid_drift_blocks_the_capture(field_name: str, label: str, mutation: str) -> None:
    document = artifact()
    index = entity_index(document["entities"], REGISTRY[field_name].esp_object_id)
    document["entities"][index][label] = mutation
    result = run(document)
    assert result.grid_parity_ok is False
    assert result.grid_revision is None
    assert any(failure.startswith(f"number_grid_mismatch:{field_name}:{label}") for failure in result.failures)


def test_absent_runtime_route_blocks_the_capture() -> None:
    document = artifact()
    del document["entities"][entity_index(document["entities"], REGISTRY["min_fog_on_s"].cfg_readback_object_id)]
    result = run(document)
    assert result.grid_parity_ok is False
    assert result.grid_revision is None
    assert any(failure.startswith("readback_route_absent:min_fog_on_s") for failure in result.failures)


def test_child_device_route_blocks_the_capture() -> None:
    """The executor addresses the primary device; a child route is not it."""
    document = artifact()
    document["entities"][entity_index(document["entities"], REGISTRY["min_fog_on_s"].esp_object_id)]["device_id"] = 1
    result = run(document)
    assert result.grid_revision is None
    assert any("component_entity_device_mismatch" in failure for failure in result.failures)


# ──────────────────────────────────────────────────────────────────────────────
# #424 three-layer coherence
# ──────────────────────────────────────────────────────────────────────────────


def test_historical_424_divergence_is_classified_present_and_blocks() -> None:
    """The recorded device vpd_low 0.56 vs served 0.875 must never converge."""
    document = artifact()
    row = next(entry for entry in document["band_layers"] if entry["series"] == "vpd_low")
    row["control"]["value"] = "0.56"
    row["observed"]["value"] = "0.56"
    result = run(document)
    triple = next(t for t in result.layer_triples if t.field_name == "vpd_low")
    assert triple.classification == "present"
    assert triple.coherent is False
    assert triple.served == "0.875" and triple.control == "0.56" and triple.observed == "0.56"
    assert result.band_coherence_ok is False
    assert result.grid_revision is None


def test_band_layers_compare_exact_binary32_transport_values() -> None:
    document = artifact()
    row = next(entry for entry in document["band_layers"] if entry["series"] == "vpd_low")
    # The served/control value is the engineering decimal; the raw ESPHome
    # readback is the same protobuf float decoded into Python binary64.
    transported = struct.unpack("!f", struct.pack("!f", 0.875))[0]
    row["served"]["value"] = "0.875"
    row["control"]["value"] = 0.875
    row["observed"]["value"] = transported
    result = run(document)
    assert next(t for t in result.layer_triples if t.field_name == "vpd_low").coherent is True

    bits = int.from_bytes(struct.pack("!f", transported), "big")
    row["observed"]["value"] = struct.unpack("!f", (bits + 1).to_bytes(4, "big"))[0]
    result = run(document)
    assert next(t for t in result.layer_triples if t.field_name == "vpd_low").coherent is False
    assert result.grid_revision is None


def test_band_series_is_bound_to_its_expected_observed_slug() -> None:
    document = artifact()
    row = next(entry for entry in document["band_layers"] if entry["series"] == "vpd_low")
    row["observed"]["slug"] = BAND_SLUGS["temp_target"]
    result = run(document)
    triple = next(t for t in result.layer_triples if t.field_name == "vpd_low")
    assert triple.classification == "unobservable"
    assert "observed_slug_mismatch" in triple.detail
    assert result.grid_revision is None


@pytest.mark.parametrize(
    ("layer", "key", "value"),
    [
        ("observed", "value", None),
        ("control", "value", None),
        ("served", "value", None),
        ("observed", "slug", None),
        ("observed", "unit", None),
        ("control", "unit", ""),
        ("served", "source", None),
        ("observed", "as_of", None),
    ],
)
def test_a_layer_that_cannot_expose_a_truthful_value_is_unobservable_and_blocks(
    layer: str, key: str, value: Any
) -> None:
    document = artifact()
    row = next(entry for entry in document["band_layers"] if entry["series"] == "vpd_low")
    row[layer][key] = value
    result = run(document)
    triple = next(t for t in result.layer_triples if t.field_name == "vpd_low")
    assert triple.classification == "unobservable"
    assert triple.coherent is False
    assert result.band_coherence_ok is False
    assert result.grid_revision is None
    assert any(failure.startswith("band_layer_unobservable:vpd_low") for failure in result.failures)


def test_stale_and_future_readbacks_are_unobservable() -> None:
    for offset, marker in ((timedelta(hours=6), "stale"), (timedelta(seconds=-30), "future")):
        document = artifact()
        row = next(entry for entry in document["band_layers"] if entry["series"] == "temp_low")
        row["observed"]["as_of"] = cap._timestamp_text(OBSERVED_AT - offset)
        result = run(document)
        triple = next(t for t in result.layer_triples if t.field_name == "temp_low")
        assert triple.classification == "unobservable"
        assert marker in triple.detail
        assert result.grid_revision is None


def test_unit_incoherence_between_layers_blocks() -> None:
    document = artifact()
    row = next(entry for entry in document["band_layers"] if entry["series"] == "vpd_high")
    row["control"]["unit"] = "hPa"
    result = run(document)
    triple = next(t for t in result.layer_triples if t.field_name == "vpd_high")
    assert triple.classification == "unobservable"
    assert "unit_incoherent" in triple.detail
    assert result.grid_revision is None


@pytest.mark.parametrize("series", cap.REQUIRED_BAND_SERIES)
def test_agreement_in_wrong_units_is_not_truthful_band_evidence(series: str) -> None:
    document = artifact()
    row = next(entry for entry in document["band_layers"] if entry["series"] == series)
    for name in ("served", "control", "observed"):
        row[name]["unit"] = "degC" if series.startswith("temp_") else "hPa"
    result = run(document)
    triple = next(t for t in result.layer_triples if t.field_name == series)
    assert triple.classification == "unobservable"
    assert "unit_not_canonical_for_series" in triple.detail
    assert result.grid_revision is None


def test_boolean_agreement_is_not_a_numeric_band() -> None:
    document = artifact()
    for name in ("served", "control", "observed"):
        document["band_layers"][0][name]["value"] = True
    result = run(document)
    assert result.grid_revision is None
    assert "value_not_numeric" in result.layer_triples[0].detail


def test_onchip_curve_cannot_be_observed_by_legacy_scalar_readbacks() -> None:
    document = artifact()
    document["band_source"]["value"] = "onchip_curve"
    result = run(document)
    assert result.grid_parity_ok is True
    assert result.observed_state_ok is True
    assert result.band_coherence_ok is False
    assert result.grid_revision is None
    for triple in result.layer_triples:
        if triple.field_name in cap.SCALAR_EDGE_SERIES:
            assert triple.classification == "unobservable"
            assert "does_not_observe_consumed_onchip_curve" in triple.detail
        else:
            # Targets really publish the consumed Setpoints target, unlike cfg edges.
            assert triple.classification == "resolved"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("value", "unknown"),
        ("slug", "setpoint_snapshot"),
        ("connection_generation", 6),
        ("connection_generation", True),
        ("runtime_instance_id", "22222222-2222-4222-8222-222222222222"),
        ("as_of", "2026-08-25T01:00:00Z"),
        ("as_of", "2026-08-27T01:00:00Z"),
        ("as_of", None),
    ],
)
def test_unknown_stale_or_different_generation_branch_blocks(key: str, value: Any) -> None:
    document = artifact()
    document["band_source"][key] = value
    result = run(document)
    assert result.grid_revision is None
    assert all(t.classification == "unobservable" for t in result.layer_triples)
    assert any(f.startswith("band_source:") for f in result.failures)


def test_version_one_cannot_be_reused_as_a_version_two_qualification() -> None:
    document = artifact()
    document["schema"] = "verdify-component-grid-capture-input-v1"
    with pytest.raises(cap.GridCaptureError, match="schema"):
        run(document)
    document["schema"] = cap.INPUT_SCHEMA
    del document["band_source"]
    with pytest.raises(cap.GridCaptureError, match="missing"):
        run(document)


def test_low_level_capture_cannot_remove_required_series_to_bypass_branch_proof() -> None:
    parsed = cap.parse_input_artifact(artifact())
    arguments = vars(parsed).copy()
    arguments["band_layers"] = []
    result = cap.capture(**arguments, required_band_series=())
    assert result.grid_revision is None
    assert "band_layer_required_series_cannot_be_reduced" in result.failures


def test_db_target_alias_is_not_the_raw_firmware_slug() -> None:
    document = artifact()
    row = next(entry for entry in document["band_layers"] if entry["series"] == "vpd_target")
    row["observed"]["slug"] = "house_vpd_target"
    result = run(document)
    assert result.grid_revision is None
    assert "observed_slug_mismatch" in next(t.detail for t in result.layer_triples if t.field_name == "vpd_target")
    hardware = (ROOT / "firmware/greenhouse/hardware.yaml").read_text()
    routes = (ROOT / "ingestor/entity_map.py").read_text()
    controls = (ROOT / "firmware/greenhouse/controls.yaml").read_text()
    assert 'name: "House VPD Target kPa"' in hardware
    assert '"house_vpd_target_kpa": "house_vpd_target"' in routes
    assert "id(gh_house_vpd_target).publish_state(setpts.vpd_target)" in controls
    assert ".vpd_low = id(sw_onchip_band_enabled) ? sv2_vpd_low : VPDlo" in controls


@pytest.mark.parametrize("series", list(cap.REQUIRED_BAND_SERIES))
def test_every_required_band_series_must_be_present(series: str) -> None:
    document = artifact()
    document["band_layers"] = [row for row in document["band_layers"] if row["series"] != series]
    result = run(document)
    assert f"missing_band_series:{series}" in result.failures
    assert result.band_coherence_ok is False
    assert result.grid_revision is None


def test_duplicate_band_series_blocks() -> None:
    document = artifact()
    document["band_layers"].append(copy.deepcopy(document["band_layers"][0]))
    result = run(document)
    assert any(failure.startswith("duplicate_band_series:") for failure in result.failures)
    assert result.grid_revision is None


# ──────────────────────────────────────────────────────────────────────────────
# Observed 48-field current state
# ──────────────────────────────────────────────────────────────────────────────


def test_off_grid_observed_value_blocks_the_capture() -> None:
    document = artifact()
    grid = ENTITY_GRIDS["min_fog_on_s"]
    off_grid = grid.minimum + grid.step / 2
    document["observed_components"]["min_fog_on_s"]["value"] = str(off_grid)
    result = run(document)
    assert result.observed_state_ok is False
    assert result.observed_start_state is None
    assert result.observed_state_content_sha256 is None
    assert result.grid_revision is None
    assert any("value_off_entity_grid" in failure for failure in result.failures)


def test_observed_components_accept_exact_binary32_grid_values_only() -> None:
    document = artifact()
    field_name = "direct_wet_stress_vpd_margin_kpa"
    transported = struct.unpack("!f", struct.pack("!f", 0.05))[0]
    document["observed_components"][field_name]["value"] = transported
    result = run(document)
    assert result.observed_state_ok is True
    assert result.observed_start_state is not None
    assert result.observed_start_state[field_name] == 0.05

    bits = int.from_bytes(struct.pack("!f", transported), "big")
    document["observed_components"][field_name]["value"] = struct.unpack("!f", (bits + 1).to_bytes(4, "big"))[0]
    result = run(document)
    assert result.observed_state_ok is False
    assert result.grid_revision is None
    assert any(f"observed_component_value_invalid:{field_name}" in failure for failure in result.failures)


def test_out_of_range_observed_value_blocks_the_capture() -> None:
    document = artifact()
    document["observed_components"]["mister_water_budget_gal"]["value"] = "310"
    result = run(document)
    assert any("value_outside_entity_grid" in failure for failure in result.failures)
    assert result.grid_revision is None


def test_switch_readback_must_be_an_exact_boolean() -> None:
    document = artifact()
    document["observed_components"]["sw_dwell_gate_enabled"]["value"] = 1
    result = run(document)
    assert any("wrong_value_type" in failure for failure in result.failures)
    assert result.grid_revision is None


@pytest.mark.parametrize("mutation", ["missing", "extra", "slug", "stale"])
def test_observed_component_layer_failures_fail_closed(mutation: str) -> None:
    document = artifact()
    if mutation == "missing":
        del document["observed_components"]["mister_all_kpa"]
        expected = "observed_component_missing:mister_all_kpa"
    elif mutation == "extra":
        document["observed_components"]["not_a_component"] = {
            "slug": "cfg_not_a_component",
            "unit": "",
            "observed_at": LAYER_AS_OF,
            "value": "1",
        }
        expected = "observed_component_unknown:not_a_component"
    elif mutation == "slug":
        document["observed_components"]["mister_all_kpa"]["slug"] = "cfg_mister_all_kpa_v2"
        expected = "observed_component_slug_mismatch:mister_all_kpa"
    else:
        document["observed_components"]["mister_all_kpa"]["observed_at"] = cap._timestamp_text(
            OBSERVED_AT - timedelta(hours=9)
        )
        expected = "observed_component:mister_all_kpa_timestamp_stale"
    result = run(document)
    assert any(failure.startswith(expected) for failure in result.failures)
    assert result.observed_state_ok is False
    assert result.grid_revision is None


# ──────────────────────────────────────────────────────────────────────────────
# Cross-language state identity (migration 214)
# ──────────────────────────────────────────────────────────────────────────────


def test_state_content_hash_reproduces_the_sql_golden_formula() -> None:
    """Mirror ``fn_experiment_v2_state_content_sha256`` byte for byte.

    db/migrations/214-confirmed-component-experiment-v2.sql:1266-1288 —
    sha256('verdify-policy-state-content-v1' || 0x00 || schema_u8 ||
    manifest[32] || vector[178]); the SQL raises unless the vector is exactly
    178 bytes and the manifest 32.
    """
    result = run()
    normalized = result.observed_start_state
    assert normalized is not None

    vector = encode_policy_vector(normalized)
    manifest = wire_manifest_digest()
    assert len(vector) == 178
    assert len(manifest) == 32
    assert 0 <= WIRE_SCHEMA_VERSION <= 255
    golden = hashlib.sha256(
        b"verdify-policy-state-content-v1" + bytes([0x00]) + bytes([WIRE_SCHEMA_VERSION]) + manifest + vector
    ).hexdigest()

    assert result.observed_state_content_sha256 == golden
    assert result.observed_wire_vector_hex == vector.hex()
    assert cap.observed_state_content_sha256(normalized) == (golden, vector.hex())


def test_state_content_hash_agrees_with_the_shipped_prefix_replay_implementation() -> None:
    """Bind to the other in-repo Python implementation of the same golden.

    ``scripts/prepare_component_prefix_replay.py::_state_identity`` already
    reproduces ``fn_experiment_v2_state_content_sha256``; two independent
    implementations that disagree would mean one of them is wrong.
    """
    spec = importlib.util.spec_from_file_location(
        "prepare_component_prefix_replay", ROOT / "scripts" / "prepare_component_prefix_replay.py"
    )
    assert spec and spec.loader
    prep = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = prep
    spec.loader.exec_module(prep)

    result = run()
    assert result.observed_start_state is not None
    identity = prep._state_identity(result.observed_start_state)
    assert identity["entity_grid_valid"] is True
    assert identity["policy_state_content_sha256"] == result.observed_state_content_sha256


def test_state_content_hash_changes_with_one_component_value() -> None:
    document = artifact()
    grid = ENTITY_GRIDS["min_fog_on_s"]
    document["observed_components"]["min_fog_on_s"]["value"] = str(grid.minimum + grid.step)
    assert run(document).observed_state_content_sha256 != run().observed_state_content_sha256


# ──────────────────────────────────────────────────────────────────────────────
# Revision determinism and stability
# ──────────────────────────────────────────────────────────────────────────────


def test_same_input_yields_the_same_revision_and_result_hash() -> None:
    first, second = run(), run()
    assert first.grid_revision == second.grid_revision
    assert first.observed_state_content_sha256 == second.observed_state_content_sha256
    payload_a = cap.build_payload(first, [], max_observation_age_s=900, computed_at=OBSERVED_AT)
    payload_b = cap.build_payload(second, [], max_observation_age_s=900, computed_at=OBSERVED_AT + timedelta(days=3))
    assert payload_a["result_sha256"] == payload_b["result_sha256"]


def test_runtime_enumeration_order_is_not_part_of_the_identity() -> None:
    document = artifact()
    document["entities"] = list(reversed(document["entities"]))
    assert run(document).grid_revision == run().grid_revision


def test_firmware_revision_moves_the_receipt_not_the_grid_identity() -> None:
    document = artifact()
    document["revisions"]["firmware_revision"] = "2026.8.26.0000.abcdef0"
    other = run(document)
    baseline = run()
    assert other.grid_revision == baseline.grid_revision
    assert other.observation_receipt_sha256 != baseline.observation_receipt_sha256


@pytest.mark.parametrize("mutation", ["device_id", "readback_unit", "disabled_by_default"])
def test_any_grid_content_change_moves_the_revision(mutation: str) -> None:
    document = artifact()
    if mutation == "device_id":
        document["device_id"] = "vallery/greenhouse-controller-b"
    elif mutation == "readback_unit":
        index = entity_index(document["entities"], REGISTRY["min_fog_on_s"].cfg_readback_object_id)
        document["entities"][index]["unit"] = "s"
    else:
        index = entity_index(document["entities"], REGISTRY["min_fog_on_s"].esp_object_id)
        document["entities"][index]["disabled_by_default"] = True
    changed = run(document)
    assert changed.grid_revision is not None
    assert changed.grid_revision != run().grid_revision


def test_derive_grid_revision_requires_every_gate() -> None:
    result = run()
    assert cap.derive_grid_revision(result) == result.grid_revision
    for gate in ("grid_parity_ok", "band_coherence_ok", "observed_state_ok"):
        assert cap.derive_grid_revision(dataclasses.replace(result, **{gate: False})) is None
    assert cap.derive_grid_revision(dataclasses.replace(result, grid_content_sha256=None)) is None
    assert cap.derive_grid_revision(dataclasses.replace(result, grid_content_sha256="not-a-digest")) is None


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda d: d["entities"].pop(0), id="missing_route"),
        pytest.param(lambda d: d["band_layers"][0]["observed"].__setitem__("value", None), id="unobservable_layer"),
        pytest.param(
            lambda d: d["observed_components"]["min_fog_on_s"].__setitem__("value", "16"), id="off_grid_state"
        ),
    ],
)
def test_no_revision_is_emitted_on_any_failure(mutate: Any) -> None:
    document = artifact()
    mutate(document)
    result = run(document)
    assert result.grid_revision is None
    assert result.qualified is False
    assert result.failures
    payload = cap.build_payload(
        result,
        cap.build_checks(result, expected_grid_revision=None),
        max_observation_age_s=900,
        computed_at=OBSERVED_AT,
    )
    assert payload["grid_revision"] is None
    assert payload["qualified"] is False
    assert "current_state_artifact" not in payload


# ──────────────────────────────────────────────────────────────────────────────
# Artifact parsing
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        pytest.param(lambda d: d.__setitem__("schema", "other-v1"), "schema", id="bad_schema"),
        pytest.param(lambda d: d.__setitem__("surprise", 1), "unknown keys", id="unknown_key"),
        pytest.param(lambda d: d.pop("band_layers"), "missing", id="missing_section"),
        pytest.param(
            lambda d: d["revisions"].__setitem__("source_revision", "unknown"), "Git SHA", id="bad_source_revision"
        ),
        pytest.param(
            lambda d: d["runtime"].__setitem__("connection_generation", 0), "connection_generation", id="bad_generation"
        ),
        pytest.param(lambda d: d["runtime"].__setitem__("runtime_instance_id", "nope"), "UUID", id="bad_instance_id"),
        pytest.param(lambda d: d["entities"][0].__setitem__("key", 0), "positive integer", id="bad_key"),
        pytest.param(lambda d: d["entities"][0].__setitem__("bonus", 1), "unknown keys", id="entity_unknown_key"),
        pytest.param(lambda d: d.__setitem__("observed_at", "not-a-time"), "RFC-3339", id="bad_observed_at"),
    ],
)
def test_malformed_artifacts_are_refused(mutate: Any, message: str) -> None:
    document = artifact()
    mutate(document)
    with pytest.raises(cap.GridCaptureError, match=message):
        cap.parse_input_artifact(document)


def test_duplicate_json_keys_are_refused() -> None:
    with pytest.raises(cap.GridCaptureError, match="duplicate JSON field"):
        json.loads('{"schema": "a", "schema": "b"}', object_pairs_hook=cap._unique_object)


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────


def test_cli_passes_and_publishes_a_private_result(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    input_path = tmp_path / "capture-input.json"
    input_path.write_text(json.dumps(artifact()))
    output_path = tmp_path / "capture-result.json"

    code = cap.main(["--input", str(input_path), "--json", str(output_path)])
    out = capsys.readouterr().out
    assert code == 0
    assert "PASS — grid_parity" in out
    assert "PASS — band_coherence_424" in out
    # This synthetic fixture has different entity keys than the physical live
    # receipt, so the CLI must warn instead of pretending it reproduces the
    # adopted source revision.
    assert "WARN — source_grid_revision_adoption" in out
    assert "grid_revision=live-entity-grid-v1:sha256:" in out
    assert "0 fail" in out

    assert stat.S_IMODE(output_path.stat().st_mode) == 0o600
    payload = json.loads(output_path.read_text())
    assert payload["schema"] == cap.RESULT_SCHEMA
    assert payload["qualified"] is True
    assert payload["grid_revision"] == run().grid_revision
    assert payload["current_state_artifact"]["schema"] == cap.CURRENT_STATE_SCHEMA
    assert set(payload["current_state_artifact"]["values"]) == set(CANONICAL_FIELD_ORDER)
    assert payload["result_sha256"] == cap.result_sha256(payload)
    assert payload["source_grid_revision_qualified"] is True

    with pytest.raises(SystemExit) as exit_info:
        cap.main(["--input", str(input_path), "--json", str(output_path)])
    assert exit_info.value.code == 2


def test_cli_fails_closed_and_emits_no_revision(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    document = artifact()
    document["band_layers"][0]["observed"]["value"] = None
    input_path = tmp_path / "blocked-input.json"
    input_path.write_text(json.dumps(document))

    code = cap.main(["--input", str(input_path)])
    out = capsys.readouterr().out
    assert code == 1
    assert "FAIL — band_coherence_424" in out
    assert "grid_revision=NONE" in out


def test_cli_expected_revision_mismatch_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    input_path = tmp_path / "capture-input.json"
    input_path.write_text(json.dumps(artifact()))
    code = cap.main(["--input", str(input_path), "--expect-grid-revision", "live-entity-grid-v1:sha256:" + "0" * 64])
    assert code == 1
    assert "FAIL — grid_revision" in capsys.readouterr().out


def test_cli_refuses_a_missing_or_malformed_artifact(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cap.main(["--input", str(tmp_path / "absent.json")])
    assert exit_info.value.code == 2

    malformed = tmp_path / "malformed.json"
    malformed.write_text("{not json")
    with pytest.raises(SystemExit) as exit_info:
        cap.main(["--input", str(malformed)])
    assert exit_info.value.code == 2
