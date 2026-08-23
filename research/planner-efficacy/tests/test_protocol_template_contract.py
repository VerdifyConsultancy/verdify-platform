"""Cross-check historical and current switchback templates against decisions."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research/planner-efficacy"))
V1_TEMPLATE = yaml.safe_load(
    (ROOT / "research/planner-efficacy/protocols/planner-switchback-v1.template.yaml").read_text(encoding="utf-8")
)
V2_TEMPLATE = yaml.safe_load(
    (ROOT / "research/planner-efficacy/protocols/planner-switchback-v2.template.yaml").read_text(encoding="utf-8")
)
WIRE_FIXTURE = json.loads(
    (ROOT / "verdify_schemas/tests/fixtures/policy_vector_goldens.json").read_text(encoding="utf-8")
)


def test_protocol_field_counts_match_canonical_schema_v2() -> None:
    ai = V1_TEMPLATE["arms"]["ai_planner"]
    field_count = len(WIRE_FIXTURE["wire_manifest"])
    allowlist_count = len(ai["treatment_allowlist"])

    assert WIRE_FIXTURE["wire_schema_version"] == 2
    assert field_count == 48
    assert allowlist_count == 11
    assert "48-field" in ai["definition"]
    expected_common = field_count - allowlist_count
    assert ai["common_fields_note"] == (
        f"remaining {expected_common} policy values byte-identical to baseline in both arms"
    )


def test_protocol_records_automated_commitment_decision() -> None:
    generation = V1_TEMPLATE["randomization"]["mapping_secret"]["generation"]
    assert "automated assignment-service" in generation
    assert "before the named beacon round" in generation
    assert "witnessed" not in generation


def test_v2_uses_confirmed_component_fast_path() -> None:
    transport = V2_TEMPLATE["transport"]
    ai = V2_TEMPLATE["arms"]["ai_daily_selector"]

    assert transport["kind"] == "legacy_components_v1"
    assert transport["generalized_policy_vector_mode_required"] == "off"
    assert transport["firmware_change_required"] is False
    assert transport["treatment_component_count"] == 11
    assert transport["observed_component_count"] == 48
    assert transport["confirmation"]["consecutive_full_matches"] == 2
    assert len(ai["treatment_allowlist"]) == 11
    assert ai["common_fields_note"] == ("remaining 37 policy values observed equal to baseline during exposure")
    assert ai["invocation_cadence"] == "once_per_local_day"
    assert ai["intraday_physical_replanning"] is False


def test_v2_pins_accelerated_evidence_gates() -> None:
    washout = V2_TEMPLATE["washout"]
    gates = V2_TEMPLATE["pre_randomization_gates"]

    assert washout["hours"] == 6
    assert washout["expected_bins_per_day"] == 72
    assert washout["climate_bins_minimum"] == 66
    assert washout["per_protocol_denominator_seconds"] == 64_800
    assert washout["per_protocol_minimum_seconds"] == 61_560
    assert V2_TEMPLATE["randomization"]["public_beacon_required"] is False
    assert gates["aa"]["duration_hours"] == 48
    assert gates["shadow"]["require_zero_experiment_device_calls"] is True
    assert gates["shadow"]["complete_scheduled_boundaries_minimum"] == 1
    assert V2_TEMPLATE["study"]["dst_crossing"]["allowed"] is False


def test_v2_treatment_fields_have_current_source_component_metadata() -> None:
    """Source metadata is a drift check; #641/#642 still require deployed proof."""

    from verdify_schemas.tunable_registry import REGISTRY

    allowlist = V2_TEMPLATE["arms"]["ai_daily_selector"]["treatment_allowlist"]
    candidates = json.loads(
        (ROOT / "research/planner-efficacy/baseline/ai-template-candidates-2026-08-14.json").read_text(encoding="utf-8")
    )

    assert allowlist == candidates["diff_allowlist"]
    for name in allowlist:
        tunable = REGISTRY[name]
        assert tunable.planner_pushable is True
        assert tunable.push_owner == "planner"
        assert tunable.esp_object_id
        assert tunable.cfg_readback_object_id


def test_v2_full_baseline_recovery_has_current_source_routes() -> None:
    """A reboot recovery can need all 48 fields, not only the treatment 11."""

    from verdify_schemas.tunable_registry import REGISTRY

    wire_names = [entry["name"] for entry in WIRE_FIXTURE["wire_manifest"]]
    recovery = V2_TEMPLATE["transport"]["full_baseline_recovery"]

    assert len(wire_names) == recovery["component_count"] == 48
    for name in wire_names:
        assert REGISTRY[name].esp_object_id
        assert REGISTRY[name].cfg_readback_object_id


def test_v2_randomization_uses_full_entropy_internal_secret() -> None:
    randomization = V2_TEMPLATE["randomization"]

    assert "internally generates" in randomization["draw"]
    assert "32-byte" in randomization["draw"]
    assert "callers cannot supply" in randomization["draw"]
    assert "uint32_be(j)" in randomization["derivation"]
    assert "verdify-switchback-v2/pair" in randomization["derivation"]
    assert "verdify-switchback-v2/mapping" in randomization["derivation"]
    assert "RFC 8785" in randomization["schedule_serialization"]
    assert "randomization_secret_32_bytes" in randomization["mapping_commitment"]
    assert "bare one-bit" in randomization["mapping_commitment"]


def test_v2_design_lock_freezes_start_before_randomization() -> None:
    locking = V2_TEMPLATE["locking"]

    assert "exact local start date" in locking["pre_draw_design_lock"]
    assert "only in those receipt fields" in locking["randomization_finalization"]
    assert "Missing the locked start aborts" in locking["randomization_finalization"]
    assert "start date" not in locking["randomization_finalization"].split("only in", 1)[1].split(", and", 1)[0]


def test_v2_separates_stable_state_hash_from_observation_receipt() -> None:
    from verdify_schemas import policy_vector

    transport = V2_TEMPLATE["transport"]
    state = transport["state_content_identity"]
    receipt = transport["observation_receipt_identity"]

    assert state["field"] == "policy_state_content_sha256"
    assert "encode_policy_vector(values) 178 bytes" in state["formula"]
    assert "round-half-even" in state["canonical_payload"]
    assert "deployed ESPHome entity grid" in state["deployed_grid_precondition"]
    assert "no observation timestamp or generation" in state["semantics"]
    assert receipt["field"] == "observation_receipt_sha256"
    assert "source_epoch_id" in receipt["canonical_payload"]
    assert "writer/connection generations" in receipt["canonical_payload"]
    assert "never minted or relabeled by the executor" in receipt["source_epoch_ownership"]
    assert V2_TEMPLATE["arms"]["frozen_fsm"]["baseline_state_content_sha256"] == "TO-LOCK"

    expected = state["golden_sha256"]
    actual = {}
    for vector in WIRE_FIXTURE["vectors"]:
        digest = hashlib.sha256()
        digest.update(state["domain_ascii"].encode("ascii"))
        digest.update(b"\x00")
        digest.update(bytes([WIRE_FIXTURE["wire_schema_version"]]))
        digest.update(policy_vector.wire_manifest_digest())
        digest.update(policy_vector.encode_policy_vector(vector["values"]))
        actual[vector["name"]] = digest.hexdigest()
    assert actual == expected


def test_v2_receipt_schema_and_golden_are_exact() -> None:
    from switchback.randomization import rfc8785_canonicalize_nfc_ijson

    receipt_contract = V2_TEMPLATE["transport"]["observation_receipt_identity"]
    schema_path = ROOT / receipt_contract["schema"]
    golden_path = ROOT / receipt_contract["golden_fixture"]
    schema_bytes = schema_path.read_bytes()
    schema = json.loads(schema_bytes)
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    payload = golden["payload"]

    assert hashlib.sha256(schema_bytes).hexdigest() == receipt_contract["schema_exact_bytes_sha256"]
    assert set(payload) == set(schema["required"])
    assert schema["additionalProperties"] is False
    wire_ids = [entry["wire_id"] for entry in payload["observations"]]
    assert wire_ids == [entry["wire_id"] for entry in WIRE_FIXTURE["wire_manifest"]]
    schema_wire_ids = [
        entry["properties"]["wire_id"]["const"] for entry in schema["properties"]["observations"]["prefixItems"]
    ]
    assert schema_wire_ids == wire_ids
    assert schema["properties"]["observations"]["items"] is False
    timestamp_pattern = re.compile(schema["$defs"]["timestamp"]["pattern"])
    assert all(timestamp_pattern.fullmatch(entry["observed_at"]) for entry in payload["observations"])
    assert timestamp_pattern.fullmatch(payload["persisted_at"])
    assert payload["execution_phase"] == "randomized"
    assert payload["operation_kind"] == "randomized_assignment"
    assert "assignment_id" not in payload
    assert all(entry["observed_at"].endswith(".000000Z") for entry in payload["observations"])
    for field in ("firmware_revision", "config_revision", "registry_revision", "grid_revision"):
        assert unicodedata.normalize("NFC", payload[field]) == payload[field]
    canonical = rfc8785_canonicalize_nfc_ijson(payload).encode("utf-8")
    assert hashlib.sha256(canonical).hexdigest() == golden["canonical_payload_sha256"]
    digest = hashlib.sha256()
    digest.update(receipt_contract["domain_ascii"].encode("ascii"))
    digest.update(b"\x00")
    digest.update(canonical)
    assert digest.hexdigest() == golden["receipt_sha256"]
    assert "every input string" in receipt_contract["canonicalization_profile"]
    assert "2^53-1" in receipt_contract["canonicalization_profile"]
    assert "not the" in receipt_contract["canonicalization_profile"]

    duplicate_wire = copy.deepcopy(payload)
    duplicate_wire["observations"][1]["wire_id"] = duplicate_wire["observations"][0]["wire_id"]
    assert [entry["wire_id"] for entry in duplicate_wire["observations"]] != schema_wire_ids

    unsorted_wire = copy.deepcopy(payload)
    unsorted_wire["observations"][0], unsorted_wire["observations"][1] = (
        unsorted_wire["observations"][1],
        unsorted_wire["observations"][0],
    )
    assert [entry["wire_id"] for entry in unsorted_wire["observations"]] != schema_wire_ids

    unsafe_integer = copy.deepcopy(payload)
    unsafe_integer["writer_generation"] = 2**53
    assert unsafe_integer["writer_generation"] > schema["$defs"]["generation"]["maximum"]

    contaminated_phase = copy.deepcopy(payload)
    contaminated_phase["execution_phase"] = "commissioning"
    phase_kinds = {
        branch["if"]["properties"]["execution_phase"]["const"]: set(
            branch["then"]["properties"]["operation_kind"].get(
                "enum", [branch["then"]["properties"]["operation_kind"].get("const")]
            )
        )
        for branch in schema["allOf"]
    }
    assert payload["operation_kind"] in phase_kinds[payload["execution_phase"]]
    assert "commissioning_probe" in phase_kinds["commissioning"]
    assert contaminated_phase["operation_kind"] not in phase_kinds[contaminated_phase["execution_phase"]]

    decomposed = copy.deepcopy(payload)
    decomposed["firmware_revision"] = "firmware-e\u0301"
    assert unicodedata.normalize("NFC", decomposed["firmware_revision"]) != decomposed["firmware_revision"]
    with pytest.raises(ValueError, match="Unicode NFC"):
        rfc8785_canonicalize_nfc_ijson(decomposed)
    invalid_scalar = copy.deepcopy(payload)
    invalid_scalar["firmware_revision"] = "\ud800"
    with pytest.raises(ValueError, match="Unicode scalar"):
        rfc8785_canonicalize_nfc_ijson(invalid_scalar)


def test_v2_confirmation_requires_distinct_post_delivery_epochs() -> None:
    transport = V2_TEMPLATE["transport"]
    confirmation = transport["confirmation"]

    assert confirmation["consecutive_full_matches"] == 2
    assert confirmation["all_component_observations_after_bundle_finished"] is True
    assert confirmation["distinct_source_observation_epochs"] is True
    assert confirmation["source_epoch_separation_seconds_min"] == 30
    assert confirmation["intra_snapshot_component_skew_seconds_max"] == 60
    assert "later cfg-ingestion source cycle" in confirmation["anti_cache_rule"]
    assert "strictly greater" in confirmation["anti_cache_rule"]
    assert "changing only an epoch UUID" in confirmation["anti_cache_rule"]
    assert transport["activation"]["baseline_confirmation_epochs_before_nonbaseline"] == 2
    assert "before any nonbaseline component write" in transport["activation"]["baseline_confirmation_rule"]
    assert any("expired or mismatched current typed work" in reason for reason in transport["close_exposure_on"])


def test_v2_state_axes_and_phase_lineage_are_orthogonal() -> None:
    contract = V2_TEMPLATE["database_contract"]

    assert contract["control_experiment_kind"] == "randomized"
    assert contract["protocol_version"] == 2
    assert "paused" in contract["lifecycle_status"]["existing_axis"]
    assert "paused" not in contract["execution_phase"]["additive_axis"]
    assert contract["execution_phase"]["additive_axis"] == [
        "feature_off",
        "shadow",
        "commissioning",
        "aa_rehearsal",
        "randomized",
    ]
    assert "baseline_recovery" in contract["admission_state"]["values"]
    assert "while lifecycle status is paused" in contract["admission_state"]["invariant"]
    assert "closed permits no experiment device claim or write" in contract["admission_state"]["invariant"]
    assert "emergency_hold permits no experiment write" in contract["admission_state"]["invariant"]
    guards = contract["admission_state"]["cross_axis_guards"]
    assert any("feature_off and shadow require admission closed" in guard for guard in guards)
    assert any("commissioning open requires lifecycle draft" in guard for guard in guards)
    assert any("aa_rehearsal open requires lifecycle draft" in guard for guard in guards)
    assert any("locked or armed lifecycle requires admission closed" in guard for guard in guards)
    assert any("closed rejects every experiment device claim" in guard for guard in guards)
    assert any("emergency_hold rejects every experiment write" in guard for guard in guards)
    assert any("baseline_recovery to closed requires two" in guard for guard in guards)
    assert any("facility authorization event" in guard for guard in guards)
    assert "phase tags are rejected from randomized ITT" in contract["readiness_binding"]
    assert "semantic revision invalidates" in contract["readiness_revision_guard"]
    assert "lifecycle is draft" in contract["prerun_readiness_operations"]
    assert "cannot enter the randomized assignment table" in contract["prerun_readiness_operations"]
    assert "fn_freeze_experiment_context/fn_create_assignment" in contract["prerun_readiness_operations"]
    readiness_resolver = contract["readiness_target_resolver"]
    assert "clock_timestamp() once" in readiness_resolver
    assert "unavailable in randomized phase" in readiness_resolver
    recovery_resolver = contract["baseline_recovery_target_resolver"]
    assert "admission=baseline_recovery" in recovery_resolver
    assert "exact locked baseline state hash" in recovery_resolver
    assert "rejects assignment/readiness ids" in recovery_resolver
    assert "can never return moderate" in recovery_resolver
    resolver = contract["current_target_resolver"]
    assert "randomized-only" in resolver
    assert "clock_timestamp()" in resolver
    assert "never transaction_timestamp" in resolver
    assert "cannot supply time" in resolver
    assert V2_TEMPLATE["integrity_roles"]["shared_database_owner_allowed_for_runtime"] is False
    freezer = V2_TEMPLATE["integrity_roles"]["outcome_freezer"]
    assert "may read blinded assignment" in freezer
    assert "cannot mutate assignment" in freezer
    assert "cannot" in V2_TEMPLATE["integrity_roles"]["blinded_analyst"]


def test_v2_feature_flags_are_safe_and_mutually_exclusive() -> None:
    flags = V2_TEMPLATE["feature_flags"]

    assert flags["component"]["environment"] == "VERDIFY_COMPONENT_EXPERIMENT_ENABLED"
    assert flags["component"]["default"] is False
    assert flags["generalized_vector"]["required"] == "off"
    assert "hard-fail" in flags["startup_and_worker_guard"]
    assert "mutually exclusive" in flags["startup_and_worker_guard"]


def test_v2_manual_override_yields_and_recovery_is_bounded() -> None:
    transport = V2_TEMPLATE["transport"]
    manual = transport["manual_or_emergency_override"]
    automatic = transport["automatic_recovery"]
    rollback = V2_TEMPLATE["rollback"]

    assert "yield to the facility" in manual
    assert "explicitly authorizes" in manual
    assert "one bounded baseline" in automatic
    assert "never repeat writes" in automatic
    assert "explicit facility authorization" in rollback["manual_override_exception"]
    assert "reverse order" not in transport["activation"]["fixed_rollback_order"]
    assert "yield to facility emergency" in V2_TEMPLATE["stopping_and_fix_forward"]["class_3_safety"]


def test_v2_outcome_sources_and_formulas_are_executable() -> None:
    outcomes = V2_TEMPLATE["outcomes"]
    climate = outcomes["climate"]
    actuators = outcomes["actuator_state_contract"]

    assert "never row existence" in outcomes["primary_itt_window_contract"]
    assert "confirmed-exposure membership never selects" in outcomes["primary_itt_window_contract"]
    assert "known baseline fallback" in outcomes["primary_itt_window_contract"]
    assert "per-protocol sensitivity only" in outcomes["exposure_use"]
    assert "never" in outcomes["exposure_use"]
    assert V2_TEMPLATE["washout"]["per_protocol_threshold_filters_primary_itt"] is False
    assert V2_TEMPLATE["analysis"]["primary_never_conditions_on_exposure"] is True
    assert climate["source_contract"]["relation"] == "public.climate"
    assert "e_i = max(L_i - x_i, 0, x_i - U_i)" in climate["bin_formula"]
    assert "Y_d = sum(0.25*e_i)/sum(0.25)" in climate["daily_formula"]
    assert actuators["equipment_ids"] == [
        "heat1",
        "heat2",
        "vent",
        "fan1",
        "fan2",
        "fog",
        "mister_south",
        "mister_west",
        "mister_center",
    ]
    assert len(actuators["stream_semantics"]) == 9
    assert "vent_open active/open state" in actuators["stream_semantics"]["vent"]
    assert "not motor energized runtime" in actuators["stream_semantics"]["vent"]
    assert "conflicting true/false rows invalidate" in actuators["same_timestamp"]
    assert "counter delta" in actuators["device_counter_reconciliation"]
    assert "[start_sample_at,end_sample_at)" in actuators["device_counter_reconciliation"]
    assert "different fixed window" in actuators["device_counter_reconciliation"]
    assert "[05:58:30,06:00:00]" in actuators["initial_state"]
    assert "post-06:00 snapshot" in actuators["initial_state"]
    receipt = V2_TEMPLATE["transport"]["observation_receipt_identity"]
    assert "embeds exactly firmware, config, registry" in receipt["revision_scope"]
    assert "bound relationally" in receipt["revision_scope"]


def test_v2_localized_safety_is_feasible_and_claim_limited() -> None:
    safety = V2_TEMPLATE["outcomes"]["climate"]["localized_safety"]

    assert list(safety["measured_wall_zone_fields"]) == ["north", "east", "south", "west"]
    assert safety["center_proxy"]["fields"] == {
        "temperature": "temp_avg",
        "vpd": "vpd_avg",
        "rh": "rh_avg",
    }
    assert "never label them a center probe" in safety["center_proxy"]["status"]
    assert "zone_air_temperature_c - zone_air_dewpoint_c" == safety["air_dew_margin_proxy"]["margin_formula"]
    assert "0 < RH <= 100" in safety["air_dew_margin_proxy"]["dewpoint_formula"]
    assert "same measured zone and minute slot" in safety["air_dew_margin_proxy"]["dewpoint_formula"]
    assert "fail the safety gate" in safety["air_dew_margin_proxy"]["dewpoint_formula"]
    assert "cannot establish true center/canopy/leaf" in safety["air_dew_margin_proxy"]["claim_limit"]
    assert "crown/leaf wetness" in safety["manual_crop_inspection"]["protocol"]
    assert "no true center" in safety["claim_limit"]
    assert "surface_sensor_ids" not in str(safety)
    assert safety["reference_disagreement"]["mode"] == "TO-LOCK: commissioned_pair or no_continuous_pair"
    assert "never an implicit fast-path prerequisite" in safety["reference_disagreement"]["hardware_rule"]


def test_v2_pair_count_and_estimator_follow_locked_power_design() -> None:
    study = V2_TEMPLATE["study"]
    analysis = V2_TEMPLATE["analysis"]
    power = V2_TEMPLATE["design_power"]

    assert study["pairs_target"] == 15
    assert isinstance(study["pairs"], str) and study["pairs"].startswith("TO-LOCK")
    assert "all precommitted adjacent-day pairs" in analysis["primary_estimand"]
    assert "15" not in analysis["primary_estimand"]
    assert "df=m-1" in analysis["upper_confidence_bound"]
    assert "all locked pairs" in analysis["complete_pair_primary_rule"]
    assert power["minimum_joint_advance_power"] == 0.80
    assert "choose one fixed m" in power["fixed_design_rule"]
    assert "larger fixed m" in power["fixed_design_rule"]
    assert "No internal sample-size adaptation" in power["fixed_design_rule"]


def test_v2_selector_identity_and_context_exclusions_are_locked() -> None:
    selection = V2_TEMPLATE["selection"]

    assert "floating alias" in selection["immutable_model_identifier"]
    assert selection["raw_request_sha256"] == "required_per_invocation"
    assert selection["raw_response_sha256"] == "required_per_invocation"
    assert selection["accepted_choices_per_local_day"] == "exactly_one"
    assert "X/Y label or private A/B mapping" in selection["forbidden_context"]
    assert "post-cutoff telemetry" in selection["forbidden_context"]
