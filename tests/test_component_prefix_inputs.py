"""Pure source/input admission checks: no compiler, replay, database or device."""

from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

from scripts import prepare_component_prefix_replay as prep
from verdify_schemas.tunable_registry import wire_value_bounds

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("prefix_input_report", ROOT / "scripts/component_prefix_replay.py")
assert SPEC and SPEC.loader
replay = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = replay
SPEC.loader.exec_module(replay)


@pytest.fixture(scope="module")
def source_report():
    return replay.source_coverage_report()


@pytest.fixture
def explicit_fixture(source_report):
    # Deliberately synthetic and never published as historical observations.
    fragments = source_report["fog_vent_input_admission"]["fragment_sha256"]
    assert fragments, "P1's actual source extractor is required for these contract tests"
    snapshot = {name: wire_value_bounds(name)[0] for name in prep.FOG_VENT_DWELL_FIELDS}
    snapshot["sw_fog_closes_vent"] = True
    legacy = dict(snapshot, sw_fog_closes_vent=False)
    branch = dict.fromkeys(prep.FOG_VENT_BOOLEAN_INPUTS, False)
    branch.update(mode="SEALED_MIST", requested_fog=True, will_fog=True, controller_time_valid=True)
    relays = {
        name: dict(state=(name == "vent"), last_on_ms=0, last_off_ms=0, on_stamp_ms=0, runtime_ms=0)
        for name in ("fog", "vent")
    }
    stages = {
        "policy": "tick_policy_selection",
        "branch": "post_resolve_before_fog_suppression",
        "relays": "pre_consumer_relay_state_after_tick_reset",
    }
    return {
        "schema": prep.FOG_VENT_INPUT_SCHEMA,
        "kind": "synthetic_consumer_fixture",
        "fragment_sha256": fragments,
        "source_files_sha256": source_report["fog_vent_input_admission"]["source_files_sha256"],
        "policy": dict(experiment_active=True, snapshot=snapshot, legacy=legacy),
        "branch": branch,
        "relays": relays,
        "provenance": {
            group: dict(
                boot_id="synthetic-boot",
                tick_id="synthetic-tick",
                now_ms=300000,
                sampled_now_ms=300000,
                stage=stage,
                origin="explicit_synthetic_fixture",
                defaults_used=[],
            )
            for group, stage in stages.items()
        },
    }


def refused(candidate, expected):
    report = prep.fog_vent_input_admission(candidate)
    assert not report["scenario_inputs_admissible"]
    assert any(expected in blocker for blocker in report["scenario_blockers"]), report
    assert report["historical_prefix_admitted"] is False
    assert report["qualification"] == "unproven"


def test_actual_source_report_preserves_27_of_48_and_classifies_other_twenty(source_report):
    assert source_report["field_count"] == 48
    assert source_report["declared_assignment_count"] == 27
    assert source_report["compiled_safety_qualification_supported"] is False
    remaining = source_report["remaining_consumer_requirements"]
    assert len(remaining) == 20
    assert {row["name"] for row in remaining} == set(source_report["unimposed_fields"]) - {"sw_fog_closes_vent"}
    assert all(
        row["control_read_sites"] and row["required_inputs_and_state"] and row["missing_or_unknown"]
        for row in remaining
    )
    assert all(row["prefix_admitted"] is False and row["qualification"] == "unproven" for row in remaining)
    wet_gate = next(row for row in remaining if row["name"] == "sw_direct_wet_gate_enabled")
    assert wet_gate["consumer_kind"] == "diagnostic_reason_read"
    assert "actuator influence" in wet_gate["missing_or_unknown"][-1]
    assert "fog_vent_boundary_inputs_unavailable" in source_report["fog_vent_input_admission"]["scenario_blockers"]


@pytest.mark.parametrize("experiment_active", [False, True])
def test_complete_explicit_scenario_is_not_a_historical_prefix_pass(explicit_fixture, experiment_active):
    explicit_fixture["policy"]["experiment_active"] = experiment_active
    report = prep.fog_vent_input_admission(explicit_fixture)
    assert report["scenario_inputs_admissible"] is True
    assert report["scenario_blockers"] == []
    assert report["historical_prefix_admitted"] is False
    assert len(report["historical_prefix_blockers"]) == 3
    assert report["qualification"] == "unproven"
    assert "order_revision" not in report


def test_explicit_blocked_safety_scenario_is_valid_input_not_permission(explicit_fixture):
    explicit_fixture["branch"]["controller_time_valid"] = False
    explicit_fixture["branch"]["leak_block"] = True
    report = prep.fog_vent_input_admission(explicit_fixture)
    assert report["scenario_inputs_admissible"] is True  # P1 must exercise the denial.
    assert report["historical_prefix_admitted"] is False


@pytest.mark.parametrize("key", prep.FOG_VENT_BOOLEAN_INPUTS)
def test_no_safety_or_request_default_when_a_required_boolean_is_missing(explicit_fixture, key):
    explicit_fixture["branch"].pop(key)
    refused(explicit_fixture, f"missing_or_nonboolean:branch.{key}")


@pytest.mark.parametrize("value", [None, 0, 1, "false", float("nan")])
def test_safety_flags_are_exact_booleans(explicit_fixture, value):
    explicit_fixture["branch"]["leak_block"] = value
    refused(explicit_fixture, "missing_or_nonboolean:branch.leak_block")


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), -float("inf"), 10**400, True])
def test_nonfinite_or_unavailable_dwell_state_is_refused(explicit_fixture, value):
    explicit_fixture["policy"]["snapshot"]["min_fog_on_s"] = value
    refused(explicit_fixture, "missing_nonfinite_or_out_of_bounds")


@pytest.mark.parametrize("key", ["last_on_ms", "last_off_ms", "on_stamp_ms", "runtime_ms"])
def test_each_required_relay_state_is_explicit(explicit_fixture, key):
    explicit_fixture["relays"]["fog"].pop(key)
    refused(explicit_fixture, f"missing_or_invalid_uint32:relays.fog.{key}")


@pytest.mark.parametrize("origin", ["eq_vent", "eq_fog", "same_row_eq_vent", "previous_row_eq_fog", "default_false"])
def test_outputs_and_defaults_cannot_be_relabelled_pre_tick_state(explicit_fixture, origin):
    explicit_fixture["provenance"]["relays"]["origin"] = origin
    refused(explicit_fixture, "unsupported_or_output_derived_origin:relays")


@pytest.mark.parametrize(
    "change,expected",
    [
        ({"sampled_now_ms": 299999}, "stale_or_future"),
        ({"sampled_now_ms": 300001}, "stale_or_future"),
        ({"stage": "post_relay_output"}, "wrong_causal_stage"),
        ({"defaults_used": ["leak_block=false"]}, "permissive_defaults"),
        ({"boot_id": "different-boot"}, "incoherent_tick:boot_id"),
        ({"tick_id": "previous-tick"}, "incoherent_tick:tick_id"),
    ],
)
def test_temporal_identity_and_causal_stage_are_required(explicit_fixture, change, expected):
    explicit_fixture["provenance"]["relays"].update(change)
    refused(explicit_fixture, expected)


def test_matching_hashes_and_labels_do_not_authenticate_a_historical_capture(explicit_fixture):
    explicit_fixture["kind"] = "authenticated_historical_capture"
    refused(explicit_fixture, "historical_capture_or_reconstruction_producer_unsupported")


def test_source_fragment_mutation_does_not_admit_old_input_binding(explicit_fixture):
    explicit_fixture["fragment_sha256"] = dict(explicit_fixture["fragment_sha256"])
    key = next(iter(explicit_fixture["fragment_sha256"]))
    explicit_fixture["fragment_sha256"][key] = "0" * 64
    refused(explicit_fixture, "actual_consumer_fragment_binding_mismatch")


def test_source_header_dependency_change_refuses_old_binding(explicit_fixture):
    explicit_fixture["source_files_sha256"] = dict(explicit_fixture["source_files_sha256"])
    explicit_fixture["source_files_sha256"]["firmware/lib/greenhouse_types.h"] = "0" * 64
    refused(explicit_fixture, "actual_consumer_source_closure_binding_mismatch")


def test_off_wire_quantum_dwell_is_not_silently_truncated(explicit_fixture):
    explicit_fixture["policy"]["legacy"]["min_fog_on_s"] = 60.5
    refused(explicit_fixture, "off_wire_quantum")


@pytest.mark.parametrize("candidate", [[], True, "not-an-object"])
def test_direct_api_malformed_root_refuses_without_exception(candidate):
    refused(candidate, "missing_or_invalid:inputs")


def test_missing_extractor_refuses_even_a_complete_fixture(explicit_fixture, tmp_path):
    result = prep.fog_vent_input_admission(explicit_fixture, root=tmp_path)
    assert "actual_fog_vent_fragment_extractor_unavailable" in result["scenario_blockers"]
    assert result["scenario_inputs_admissible"] is False


def test_missing_actual_consumer_propagates_extraction_refusal(explicit_fixture, tmp_path):
    for relative in explicit_fixture["source_files_sha256"]:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    controls = tmp_path / "firmware/greenhouse/controls.yaml"
    text = controls.read_text()
    assert text.count("const bool vent_is_open = id(vent_rly)->state;") == 1
    controls.write_text(text.replace("const bool vent_is_open = id(vent_rly)->state;", "/* unavailable readback */"))
    report = prep.fog_vent_input_admission(explicit_fixture, root=tmp_path)
    assert report["scenario_inputs_admissible"] is False
    assert "actual_fog_vent_fragment_extraction_refused:ValueError" in report["scenario_blockers"]


def test_malformed_mode_and_inconsistent_derived_lock_are_refused(explicit_fixture):
    explicit_fixture["branch"]["mode"] = []
    refused(explicit_fixture, "missing_or_unknown_mode")
    explicit_fixture["branch"]["mode"] = "SENSOR_FAULT"
    refused(explicit_fixture, "sensor_fault_lock_not_derived_from_mode")


def test_no_candidate_values_are_copied_into_report(explicit_fixture):
    original = copy.deepcopy(explicit_fixture)
    report = prep.fog_vent_input_admission(explicit_fixture)
    assert explicit_fixture == original
    assert "synthetic-boot" not in json.dumps(report)
    assert "300000" not in json.dumps(report)


def test_cli_strictly_refuses_duplicate_input_keys(tmp_path):
    path = tmp_path / "ambiguous.json"
    path.write_text('{"kind":"synthetic_consumer_fixture","kind":"historical"}')
    with pytest.raises(SystemExit) as exc:
        replay.main(["coverage-source", "--fog-vent-inputs", str(path)])
    assert exc.value.code == 2
