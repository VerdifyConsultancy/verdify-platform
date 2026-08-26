"""Offline prefix replay preparation is complete, deterministic and non-actuating."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

from verdify_schemas.component_executor import ACTIVATION_ORDER, RECOVERY_ORDER
from verdify_schemas.policy_vector import canonical_json_bytes, decode_policy_vector

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "prepare_component_prefix_replay.py"
SPEC = importlib.util.spec_from_file_location("prepare_component_prefix_replay", SCRIPT)
assert SPEC and SPEC.loader
prep = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prep)

PROFILE_PATH = ROOT / "research/planner-efficacy/baseline/planner-switchback-v2-profiles.json"
MANIFEST_PATH = ROOT / "firmware/policy_consumer_manifest.json"
COMPILED_DEFAULT_FIXTURE = (
    ROOT / "tests/fixtures/component_prefix_replay/esphome-2026.6.5-compiled-default-constructors.cpp"
)
GRID_REVISION = "live-entity-grid-v1:sha256:" + "a" * 64
SOURCE_REVISION = "b" * 40
FIRMWARE_REVISION = "2026.8.26.0000.abcdef0"


def _artifact(path: Path) -> tuple[bytes, dict]:
    raw = path.read_bytes()
    return raw, json.loads(raw)


def _compiled_main() -> bytes:
    return COMPILED_DEFAULT_FIXTURE.read_bytes()


def _current_state(values: dict[str, bool | float]) -> dict[str, object]:
    return {
        "device_id": "esp32-vallery",
        "firmware_revision": FIRMWARE_REVISION,
        "grid_revision": GRID_REVISION,
        "observed_at": "2026-08-26T01:00:00.000000Z",
        "schema": prep.CURRENT_STATE_SCHEMA,
        "values": values,
    }


def _packet(current_states=None) -> dict[str, object]:
    profile_raw, profiles = _artifact(PROFILE_PATH)
    manifest_raw, manifest = _artifact(MANIFEST_PATH)
    return prep.build_preparation_packet(
        profile_artifact=profiles,
        profile_artifact_sha256=hashlib.sha256(profile_raw).hexdigest(),
        consumer_manifest=manifest,
        consumer_manifest_sha256=hashlib.sha256(manifest_raw).hexdigest(),
        generated_main_cpp=_compiled_main(),
        firmware_binary=b"test-compiled-firmware-binary",
        source_revision=SOURCE_REVISION,
        firmware_revision=FIRMWARE_REVISION,
        grid_revision=GRID_REVISION,
        current_states={} if current_states is None else current_states,
    )


def _profile(name: str) -> dict[str, bool | float]:
    artifact = json.loads(PROFILE_PATH.read_text())
    return decode_policy_vector(bytes.fromhex(artifact["profiles"][name]["wire_hex"]))


def test_packet_enumerates_actual_difference_lists_and_every_full_recovery_prefix() -> None:
    baseline = _profile("baseline")
    packet = _packet({"observed-20260826t010000z": _current_state(baseline)})

    assert packet["schema"] == prep.PACKET_SCHEMA
    assert packet["status"] == "prepared_not_qualified"
    assert packet["qualification_blockers"] == [
        "compiled_esphome_result_missing_for_every_case",
        "hardware_in_loop_result_missing_for_every_case",
        "qualified_evidence_manifest_not_reviewed",
    ]
    assert packet["orders"]["activation"] == list(ACTIVATION_ORDER)
    assert packet["orders"]["recovery"] == list(RECOVERY_ORDER)

    # Actual writer lists skip unchanged treatment fields: 6 moderate and 8
    # aggressive commands.  Each direction includes the empty prefix.
    assert packet["summary"] == {
        "activation_cases": 16,
        "case_count": 130,
        "current_start_count": 1,
        "recovery_cases": 98,
        "rollback_cases": 16,
    }
    assert len({case["case_id"] for case in packet["cases"]}) == 130
    for case in packet["cases"]:
        assert case["applied_fields"] == case["command_fields"][: case["prefix_length"]]
        assert case["pending_fields"] == case["command_fields"][case["prefix_length"] :]
        assert case["result_slots"] == {"compiled_esphome": None, "hardware_in_loop": None}


def test_compiled_off_grid_default_is_preserved_until_its_exact_recovery_prefix() -> None:
    packet = _packet()
    assert packet["qualification_blockers"][0] == "observed_current_state_missing"
    compiled = packet["starts"]["compiled-defaults"]
    assert compiled["values"]["mister_engage_delay_s"] == 45.0
    assert compiled["identity"]["entity_grid_valid"] is False
    assert compiled["identity"]["validation_code"] == "value_off_entity_grid"

    field_prefix = RECOVERY_ORDER.index("mister_engage_delay_s") + 1
    before = next(
        case
        for case in packet["cases"]
        if case["case_id"] == f"recovery/compiled-defaults-to-baseline/prefix-{field_prefix - 1:02d}"
    )
    repaired = next(
        case
        for case in packet["cases"]
        if case["case_id"] == f"recovery/compiled-defaults-to-baseline/prefix-{field_prefix:02d}"
    )
    assert before["expected_state_identity"]["entity_grid_valid"] is False
    assert repaired["expected_state_identity"]["entity_grid_valid"] is True
    assert (
        before["expected_state_identity"]["raw_state_sha256"] != repaired["expected_state_identity"]["raw_state_sha256"]
    )
    assert repaired["applied_fields"][-1] == "mister_engage_delay_s"


def test_preparation_is_canonical_and_deterministic_but_not_an_order_revision() -> None:
    baseline = _profile("baseline")
    moderate = _profile("moderate")
    states_a = {
        "observed-b": _current_state(moderate),
        "observed-a": _current_state(baseline),
    }
    states_b = dict(reversed(tuple(states_a.items())))
    first = canonical_json_bytes(_packet(states_a))
    second = canonical_json_bytes(_packet(states_b))
    assert first == second
    assert b"prefix-replay-v1:sha256:" not in first
    assert b'"status":"prepared_not_qualified"' in first


def test_generated_cpp_must_contain_each_exact_compiled_constructor() -> None:
    profile_raw, profiles = _artifact(PROFILE_PATH)
    manifest_raw, manifest = _artifact(MANIFEST_PATH)
    truncated = _compiled_main().replace(
        b"new(mister_engage_delay_s) globals::GlobalsComponent<int>(45);\n",
        b"",
    )
    with pytest.raises(prep.PrefixPreparationError, match="lacks compiled default mister_engage_delay_s"):
        prep.build_preparation_packet(
            profile_artifact=profiles,
            profile_artifact_sha256=hashlib.sha256(profile_raw).hexdigest(),
            consumer_manifest=manifest,
            consumer_manifest_sha256=hashlib.sha256(manifest_raw).hexdigest(),
            generated_main_cpp=truncated,
            firmware_binary=b"test-binary",
            source_revision=SOURCE_REVISION,
            firmware_revision=FIRMWARE_REVISION,
            grid_revision=GRID_REVISION,
            current_states={},
        )


def test_sensitive_output_is_atomic_private_and_never_overwritten(tmp_path: Path) -> None:
    output = tmp_path / "private" / "prefix-packet.json"
    raw = canonical_json_bytes(_packet({"observed": _current_state(_profile("baseline"))}))
    prep.write_preparation_output(raw, output)
    assert output.read_bytes() == raw
    assert output.stat().st_mode & 0o777 == 0o600
    assert output.parent.stat().st_mode & 0o077 == 0
    assert list(output.parent.iterdir()) == [output]

    with pytest.raises(prep.PrefixPreparationError, match="overwrite refused"):
        prep.write_preparation_output(b"replacement", output)
    assert output.read_bytes() == raw


def test_sensitive_output_refuses_nonregular_and_symlink_targets(tmp_path: Path) -> None:
    directory_target = tmp_path / "directory"
    directory_target.mkdir()
    with pytest.raises(prep.PrefixPreparationError, match="overwrite refused"):
        prep.write_preparation_output(b"packet", directory_target)

    existing = tmp_path / "existing"
    existing.write_bytes(b"unchanged")
    symlink = tmp_path / "symlink"
    os.symlink(existing.name, symlink)
    with pytest.raises(prep.PrefixPreparationError, match="overwrite refused"):
        prep.write_preparation_output(b"packet", symlink)
    assert existing.read_bytes() == b"unchanged"
