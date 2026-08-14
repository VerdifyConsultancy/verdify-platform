"""Canonical policy-vector codec tests (Lane A, #582; audit §8.9).

Four layers of protection:

1. **Frozen wire assignment** — the literal name → (wire_id, kind, scale,
   unit) table below must never drift; retired ids are never reused.
2. **Golden fixtures** — byte- and hash-exact vectors shared with the C++
   test (`firmware/test/test_policy_vector.cpp`) via
   `fixtures/policy_vector_goldens.json`.
3. **Generated-artifact drift gates** — the checked-in
   `firmware/lib/policy_vector_generated.h` and
   `firmware/test/policy_vector_goldens_generated.inc` must be byte-identical
   to regenerated output.
4. **Firmware entity drift** — every wire field maps to a real ESPHome entity
   in `firmware/greenhouse/tunables.yaml` backed by a real global in
   `firmware/greenhouse/globals.yaml` (several globals are renamed relative
   to the registry name; the entity lambda is the source of truth). Wire
   schema v2 (#588) retired the one documented zero-firmware-presence
   exemption (`direct_wet_stress_latest_hour`, wire_id 6 — permanently in
   RETIRED_WIRE_IDS), so the exemption set is empty and stays that way.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
import yaml

from verdify_schemas import policy_vector as pv
from verdify_schemas.tunable_registry import (
    PLANNER_PUSHABLE_REG,
    POLICY_WIRE_FIELD_COUNT,
    REGISTRY,
    RETIRED_WIRE_IDS,
    WIRE_SCHEMA_VERSION,
    wire_metadata_errors,
    wire_value_bounds,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "policy_vector_goldens.json"
TUNABLES_YAML = REPO_ROOT / "firmware" / "greenhouse" / "tunables.yaml"
GLOBALS_YAML = REPO_ROOT / "firmware" / "greenhouse" / "globals.yaml"

# Wire fields with zero firmware presence. Empty since wire schema v2 (#588)
# retired `direct_wet_stress_latest_hour` — the one documented v1 exemption.
FIRMWARE_ABSENT_WIRE_FIELDS: frozenset[str] = frozenset()

# ── 1. Frozen wire assignment ────────────────────────────────────────────────
# PERMANENT. A change here is a wire-schema change: it requires a
# WIRE_SCHEMA_VERSION bump, new goldens, and regenerated firmware headers —
# never a silent renumbering. Retired ids move to RETIRED_WIRE_IDS: wire_id 6
# (direct_wet_stress_latest_hour) retired in v2 and may never be reused.
FROZEN_WIRE_TABLE: dict[str, tuple[int, str, int, str | None]] = {
    "band_track_fraction": (1, "u8", 20, "fraction"),
    "cold_vent_guard_delta_f": (2, "u8", 2, "degF"),
    "cool_exit_hysteresis_f": (3, "u8", 10, "degF"),
    "cool_stage2_exit_hysteresis_f": (4, "u8", 10, "degF"),
    "cool_stage2_over_high_f": (5, "u8", 10, "degF"),
    "direct_wet_stress_min_dew_margin_f": (7, "u8", 2, "degF"),
    "direct_wet_stress_vpd_margin_kpa": (8, "u8", 20, "kPa"),
    "dwell_gate_ms": (9, "u32", 1, "ms"),
    "enthalpy_close": (10, "i16", 2, "kJ/kg"),
    "enthalpy_open": (11, "i16", 2, "kJ/kg"),
    "fog_escalation_kpa": (12, "u8", 10, "kPa"),
    "heat_hysteresis": (13, "u8", 10, "degF"),
    "min_fan_off_s": (14, "u16", 1, "s"),
    "min_fan_on_s": (15, "u16", 1, "s"),
    "min_fog_off_s": (16, "u16", 1, "s"),
    "min_fog_on_s": (17, "u16", 1, "s"),
    "min_heat_off_s": (18, "u16", 1, "s"),
    "min_heat_on_s": (19, "u16", 1, "s"),
    "min_vent_off_s": (20, "u16", 1, "s"),
    "min_vent_on_s": (21, "u16", 1, "s"),
    "mist_backoff_s": (22, "u16", 1, "s"),
    "mist_max_closed_vent_s": (23, "u16", 1, "s"),
    "mist_thermal_relief_s": (24, "u16", 1, "s"),
    "mister_all_delay_s": (25, "u16", 1, "s"),
    "mister_all_kpa": (26, "u8", 20, "kPa"),
    "mister_center_penalty": (27, "u8", 10, "fraction"),
    "mister_engage_delay_s": (28, "u16", 1, "s"),
    "mister_engage_kpa": (29, "u8", 20, "kPa"),
    "mister_min_off_s": (30, "u8", 1, "s"),
    "mister_pulse_gap_s": (31, "u8", 1, "s"),
    "mister_pulse_on_s": (32, "u8", 1, "s"),
    "mister_vpd_weight": (33, "u8", 2, "ratio"),
    "mister_water_budget_gal": (34, "u16", 10, "gal"),
    "night_vpd_bias_kpa": (35, "u8", 100, "kPa"),
    "outdoor_staleness_max_s": (36, "u16", 1, "s"),
    "sw_cool_all_fans_at_high_enabled": (37, "bool", 1, None),
    "sw_direct_wet_gate_enabled": (38, "bool", 1, None),
    "sw_direct_wet_stress_override_enabled": (39, "bool", 1, None),
    "sw_dwell_gate_enabled": (40, "bool", 1, None),
    "sw_fog_closes_vent": (41, "bool", 1, None),
    "sw_mister_closes_vent": (42, "bool", 1, None),
    "sw_summer_vent_enabled": (43, "bool", 1, None),
    "temp_hysteresis": (44, "u8", 10, "degF"),
    "vent_exchange_fraction": (45, "u8", 20, "fraction"),
    "vent_prefer_dp_delta_f": (46, "u8", 2, "degF"),
    "vent_prefer_temp_delta_f": (47, "u8", 2, "degF"),
    "vpd_hysteresis": (48, "u8", 20, "kPa"),
    "vpd_watch_dwell_s": (49, "u8", 1, "s"),
}


@pytest.fixture(scope="module")
def goldens() -> dict:
    return json.loads(FIXTURE_PATH.read_text())


def _default_values() -> dict[str, float | bool]:
    values: dict[str, float | bool] = {}
    for defn in pv.wire_fields():
        values[defn.name] = bool(defn.default) if defn.wire_kind == "bool" else float(defn.default)
    return values


def test_wire_metadata_is_complete_unique_and_representable() -> None:
    assert wire_metadata_errors() == []
    assert WIRE_SCHEMA_VERSION == 2
    assert POLICY_WIRE_FIELD_COUNT == 48
    assert len(PLANNER_PUSHABLE_REG) == 48


def test_wire_assignment_is_frozen() -> None:
    actual = {
        d.name: (d.wire_id, d.wire_kind, d.wire_scale, d.wire_unit) for d in REGISTRY.values() if d.wire_id is not None
    }
    assert actual == FROZEN_WIRE_TABLE
    assert RETIRED_WIRE_IDS == frozenset({6})
    assert not RETIRED_WIRE_IDS & {wire_id for wire_id, *_ in FROZEN_WIRE_TABLE.values()}


def test_wire_fields_ordered_by_wire_id_and_sized() -> None:
    ids = [d.wire_id for d in pv.wire_fields()]
    assert ids == sorted(ids) == [*range(1, 6), *range(7, 50)]  # wire_id 6 retired in v2
    assert pv.POLICY_VECTOR_HEADER_SIZE == 14
    assert pv.POLICY_VECTOR_SIZE == 178


def test_wire_manifest_matches_frozen_fixture(goldens: dict) -> None:
    assert pv.wire_manifest() == goldens["wire_manifest"]
    assert pv.wire_manifest_digest().hex() == goldens["wire_manifest_digest_sha256"]
    assert goldens["wire_schema_version"] == WIRE_SCHEMA_VERSION
    assert goldens["vector_size"] == pv.POLICY_VECTOR_SIZE


def test_canonical_json_is_rfc8785_style() -> None:
    assert (
        pv.canonical_json_bytes({"b": 1, "a": [1.5, 2.0, True, None, "x\n"]}) == b'{"a":[1.5,2,true,null,"x\\n"],"b":1}'
    )
    with pytest.raises(ValueError):
        pv.canonical_json_bytes(float("nan"))
    with pytest.raises(ValueError):
        pv.canonical_json_bytes({1: "non-string key"})


def test_golden_vectors_round_trip(goldens: dict) -> None:
    for vector in goldens["vectors"]:
        values = vector["values"]
        encoded = pv.encode_policy_vector(values)
        assert encoded.hex() == vector["vector_hex"], vector["name"]
        assert pv.decode_policy_vector(encoded) == values, vector["name"]
        assert pv.quantize_policy_values(values) == values, vector["name"]
        raws = [pv._raws_from_values(values)[i] for i in range(POLICY_WIRE_FIELD_COUNT)]
        assert raws == vector["raws_by_wire_id"], vector["name"]


def test_golden_content_and_activation_hashes(goldens: dict) -> None:
    revision_ids = goldens["revision_ids"]
    assert pv.canonical_revision_ids_bytes(revision_ids).decode() == goldens["revision_ids_canonical_json"]
    ctx = goldens["activation_context"]
    for vector in goldens["vectors"]:
        blob = bytes.fromhex(vector["vector_hex"])
        content = pv.content_sha256(blob, schema_version=WIRE_SCHEMA_VERSION, policy_revision_ids=revision_ids)
        assert content.hex() == vector["content_sha256"], vector["name"]
        for treatment_name, expected in vector["activation_sha256"].items():
            activation = pv.activation_sha256(
                content,
                experiment_id=ctx["experiment_id"],
                assignment_id=ctx["assignment_id"],
                treatment_bytes=bytes.fromhex(goldens["treatments"][treatment_name]),
                generation=ctx["generation"],
                valid_from_us=ctx["valid_from_us"],
                valid_to_us=ctx["valid_to_us"],
            )
            assert activation.hex() == expected, f"{vector['name']}/{treatment_name}"


def test_quantization_is_round_half_even() -> None:
    values = _default_values()
    # temp_hysteresis: scale 10 — 1.25 is exactly representable in binary, so
    # the tie is real: 12.5 → 12 (even), 1.75 → 17.5 → 18 (even).
    values["temp_hysteresis"] = 1.25
    assert pv.quantize_policy_values(values)["temp_hysteresis"] == 1.2
    values["temp_hysteresis"] = 1.75
    assert pv.quantize_policy_values(values)["temp_hysteresis"] == 1.8
    # Non-tie values snap to the nearest grid point.
    values["temp_hysteresis"] = 1.34
    assert pv.quantize_policy_values(values)["temp_hysteresis"] == 1.3


def test_encode_rejects_bad_value_sets() -> None:
    values = _default_values()

    missing = dict(values)
    del missing["temp_hysteresis"]
    with pytest.raises(ValueError, match="missing=\\['temp_hysteresis'\\]"):
        pv.encode_policy_vector(missing)

    extra = dict(values)
    extra["not_a_field"] = 1.0
    with pytest.raises(ValueError, match="extra=\\['not_a_field'\\]"):
        pv.encode_policy_vector(extra)

    for bad in (float("nan"), float("inf"), "1.5", None):
        broken = dict(values)
        broken["temp_hysteresis"] = bad  # type: ignore[assignment]
        with pytest.raises(ValueError):
            pv.encode_policy_vector(broken)

    out_of_bounds = dict(values)
    out_of_bounds["temp_hysteresis"] = 3.2
    with pytest.raises(ValueError, match="outside wire bounds"):
        pv.encode_policy_vector(out_of_bounds)

    bad_bool = dict(values)
    bad_bool["sw_summer_vent_enabled"] = 0.5
    with pytest.raises(ValueError, match="not a valid boolean"):
        pv.encode_policy_vector(bad_bool)

    numeric_given_bool = dict(values)
    numeric_given_bool["temp_hysteresis"] = True
    with pytest.raises(ValueError, match="not numeric"):
        pv.encode_policy_vector(numeric_given_bool)


def test_decode_is_strict(goldens: dict) -> None:
    blob = bytearray(bytes.fromhex(goldens["vectors"][0]["vector_hex"]))

    bad = bytearray(blob)
    bad[0] = ord("X")
    with pytest.raises(ValueError, match="magic"):
        pv.decode_policy_vector(bytes(bad))

    bad = bytearray(blob)
    bad[4] = WIRE_SCHEMA_VERSION + 1
    with pytest.raises(ValueError, match="schema version"):
        pv.decode_policy_vector(bytes(bad))

    bad = bytearray(blob)
    bad[5] ^= 0xFF
    with pytest.raises(ValueError, match="manifest digest"):
        pv.decode_policy_vector(bytes(bad))

    bad = bytearray(blob)
    bad[13] = POLICY_WIRE_FIELD_COUNT + 1
    with pytest.raises(ValueError, match="field count"):
        pv.decode_policy_vector(bytes(bad))

    with pytest.raises(ValueError, match="size|truncated"):
        pv.decode_policy_vector(bytes(blob[:-1]))
    with pytest.raises(ValueError, match="size"):
        pv.decode_policy_vector(bytes(blob) + b"\x00")

    # Record 0 is wire_id 1 (band_track_fraction, u8, raw <= 20): force raw 21.
    bad = bytearray(blob)
    bad[pv.POLICY_VECTOR_HEADER_SIZE + 2] = 21
    with pytest.raises(ValueError, match="outside wire bounds"):
        pv.decode_policy_vector(bytes(bad))

    # Swap the first two 3-byte records → out-of-order (duplicate-adjacent) ids.
    bad = bytearray(blob)
    start = pv.POLICY_VECTOR_HEADER_SIZE
    bad[start : start + 3], bad[start + 3 : start + 6] = blob[start + 3 : start + 6], blob[start : start + 3]
    with pytest.raises(ValueError, match="duplicate or out-of-order"):
        pv.decode_policy_vector(bytes(bad))

    bad = bytearray(blob)
    bad[start : start + 2] = (999).to_bytes(2, "big")
    with pytest.raises(ValueError, match="unknown wire_id"):
        pv.decode_policy_vector(bytes(bad))


def test_content_sha256_validation(goldens: dict) -> None:
    blob = bytes.fromhex(goldens["vectors"][0]["vector_hex"])
    revision_ids = goldens["revision_ids"]
    with pytest.raises(ValueError, match="header version"):
        pv.content_sha256(blob, schema_version=WIRE_SCHEMA_VERSION + 1, policy_revision_ids=revision_ids)
    with pytest.raises(ValueError, match="\\[1, 255\\]"):
        pv.content_sha256(blob, schema_version=0, policy_revision_ids=revision_ids)
    with pytest.raises(ValueError, match="non-empty"):
        pv.content_sha256(blob, schema_version=WIRE_SCHEMA_VERSION, policy_revision_ids={})
    with pytest.raises(ValueError, match="str -> str"):
        pv.content_sha256(
            blob,
            schema_version=WIRE_SCHEMA_VERSION,
            policy_revision_ids={"registry": 7},  # type: ignore[dict-item]
        )
    with pytest.raises(ValueError, match="magic"):
        pv.content_sha256(b"nope" + blob[4:], schema_version=WIRE_SCHEMA_VERSION, policy_revision_ids=revision_ids)


def test_treatment_octets_exactly_match_spec() -> None:
    src = uuid.uuid4().bytes
    dst = uuid.uuid4().bytes
    for valid in (
        bytes([0x01, 0x58]),
        bytes([0x01, 0x59]),
        bytes([0x03, 0x00]),
        bytes([0x03, 0x01]),
        *(bytes([0x02, op]) + src + dst + bytes([regime]) for op in (1, 2, 3, 4) for regime in (0, 1, 2, 3)),
    ):
        pv.validate_treatment_bytes(valid)

    for invalid in (
        b"",  # off/shadow emits no activation hash — never a null sentinel
        bytes([0x00, 0x00]),
        bytes([0x04, 0x00]),
        bytes([0x01, 0x5A]),
        bytes([0x01]),
        bytes([0x01, 0x58, 0x00]),
        bytes([0x03, 0x02]),
        bytes([0x02, 0x00]) + src + dst + bytes([0x00]),  # op 0x00
        bytes([0x02, 0x05]) + src + dst + bytes([0x00]),  # op 0x05
        bytes([0x02, 0x01]) + src + dst + bytes([0x04]),  # regime 0x04
        bytes([0x02, 0x01]) + src + dst[:-1] + bytes([0x00]),  # short uuid
    ):
        with pytest.raises(ValueError):
            pv.validate_treatment_bytes(invalid)


def test_activation_sha256_validation(goldens: dict) -> None:
    content = bytes.fromhex(goldens["vectors"][0]["content_sha256"])
    kwargs = dict(
        experiment_id=goldens["activation_context"]["experiment_id"],
        assignment_id=goldens["activation_context"]["assignment_id"],
        treatment_bytes=bytes([0x01, 0x58]),
        generation=1,
        valid_from_us=10,
        valid_to_us=20,
    )
    assert len(pv.activation_sha256(content, **kwargs)) == 32
    with pytest.raises(ValueError, match="32 bytes"):
        pv.activation_sha256(content[:-1], **kwargs)
    with pytest.raises(ValueError, match="valid UUID"):
        pv.activation_sha256(content, **{**kwargs, "experiment_id": "not-a-uuid"})
    with pytest.raises(ValueError, match="non-empty"):
        pv.activation_sha256(content, **{**kwargs, "treatment_bytes": b""})
    with pytest.raises(ValueError, match="generation"):
        pv.activation_sha256(content, **{**kwargs, "generation": -1})
    with pytest.raises(ValueError, match="must be <"):
        pv.activation_sha256(content, **{**kwargs, "valid_from_us": 20})
    with pytest.raises(ValueError, match="valid_to_us"):
        pv.activation_sha256(content, **{**kwargs, "valid_to_us": 2**64})


def test_generated_cpp_artifacts_have_not_drifted() -> None:
    """The checked-in generated header + goldens .inc must match regeneration."""
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "gen-policy-vector-header.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, f"generated C++ artifacts drifted:\n{result.stdout}{result.stderr}"


def test_goldens_fixture_has_not_drifted() -> None:
    """The committed goldens JSON must match scripts/gen-policy-vector-goldens.py."""
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "gen-policy-vector-goldens.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, f"goldens fixture drifted:\n{result.stdout}{result.stderr}"


# ── Firmware entity drift ────────────────────────────────────────────────────

for _tag in ("!secret", "!lambda", "!include"):
    yaml.SafeLoader.add_constructor(_tag, lambda loader, node: None)


def _esphome_slug(display_name: str) -> str:
    return "".join(char if char.isascii() and char.isalnum() else "_" for char in display_name.lower())


@pytest.fixture(scope="module")
def firmware_entities() -> dict[str, dict]:
    doc = yaml.safe_load(TUNABLES_YAML.read_text())
    by_object_id: dict[str, dict] = {}
    for section in ("number", "switch", "select"):
        for entity in doc.get(section) or []:
            if isinstance(entity, dict) and "name" in entity:
                by_object_id[_esphome_slug(entity["name"])] = entity
    return by_object_id


@pytest.fixture(scope="module")
def firmware_global_ids() -> frozenset[str]:
    ids = re.findall(r"^  - id:\s*(\w+)", GLOBALS_YAML.read_text(), re.M)
    assert len(ids) > 100, "globals.yaml parse regression"
    return frozenset(ids)


def test_every_wire_field_maps_to_a_firmware_entity_and_global(
    firmware_entities: dict[str, dict], firmware_global_ids: frozenset[str]
) -> None:
    """Audit §8.9 drift guard: wire schema ↔ ESPHome entity ↔ backing global.

    The registry name and the firmware global may legitimately differ
    (e.g. temp_hysteresis → hyst_temp_f, vpd_hysteresis → hyst_vpd_kpa,
    heat_hysteresis → heat_hysteresis_f, enthalpy_open/close →
    enthalpy_*_kjkg), so the entity's lambda — not the name — binds the
    global. `direct_wet_stress_latest_hour` is the one documented exemption.
    """
    problems: list[str] = []
    for defn in pv.wire_fields():
        if defn.name in FIRMWARE_ABSENT_WIRE_FIELDS:
            continue
        entity = firmware_entities.get(defn.esp_object_id or "")
        if entity is None:
            problems.append(f"{defn.name}: no ESPHome entity with object_id {defn.esp_object_id!r}")
            continue
        state_lambda = entity.get("lambda") or ""
        match = re.search(r"id\((\w+)\)", state_lambda)
        if match is None:
            problems.append(f"{defn.name}: entity {defn.esp_object_id!r} has no id(...) state lambda")
        elif match.group(1) not in firmware_global_ids:
            problems.append(f"{defn.name}: entity global {match.group(1)!r} not declared in globals.yaml")
    assert problems == []


def test_firmware_absent_exemption_is_retired_and_stays_retired(firmware_entities: dict[str, dict]) -> None:
    """Wire schema v2 (#588): the v1 zero-consumer exemption is retired, its
    wire_id permanently reserved, and firmware still has no such entity — if
    one ever appears the field needs a NEW wire id, never a resurrected 6."""
    assert FIRMWARE_ABSENT_WIRE_FIELDS == frozenset()
    defn = REGISTRY["direct_wet_stress_latest_hour"]
    assert defn.wire_id is None and defn.tier == 2 and not defn.planner_pushable
    assert defn.control_class == "retired"
    assert 6 in RETIRED_WIRE_IDS
    assert defn.esp_object_id not in firmware_entities
    with pytest.raises(ValueError, match="not part of the policy wire schema"):
        wire_value_bounds("direct_wet_stress_latest_hour")
