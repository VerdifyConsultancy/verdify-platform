"""Fail-closed contracts for the issue-#641 field-readiness package."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest
import yaml

MODULE_DIR = Path(__file__).parents[1]
REPO_ROOT = MODULE_DIR.parents[1]
READINESS_DIR = MODULE_DIR / "readiness"
sys.path.insert(0, str(MODULE_DIR))
sys.path.insert(0, str(REPO_ROOT))

from readiness import contracts

MANIFEST_PATH = READINESS_DIR / "physical-readiness-v1.template.yaml"
CALIBRATION_PATH = READINESS_DIR / "calibration-work-order-v1.template.yaml"
PROBE_PATH = READINESS_DIR / "commissioning-probe-work-order-v1.template.yaml"
TEMPLATE_PATHS = (MANIFEST_PATH, CALIBRATION_PATH, PROBE_PATH)


def _load(path: Path) -> dict:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _schema_for(document: Mapping[str, object]) -> dict:
    name = {
        "verdify-experiment-v2-physical-readiness": "physical-readiness-v1.schema.json",
        "verdify-experiment-v2-calibration-work-order": "calibration-work-order-v1.schema.json",
        "verdify-experiment-v2-commissioning-probe-work-order": "commissioning-probe-work-order-v1.schema.json",
    }[document["schema"]]
    return json.loads((READINESS_DIR / name).read_text(encoding="utf-8"))


def _all_keys(value: object) -> set[str]:
    if isinstance(value, Mapping):
        result = {str(key) for key in value}
        for child in value.values():
            result.update(_all_keys(child))
        return result
    if isinstance(value, list):
        result: set[str] = set()
        for child in value:
            result.update(_all_keys(child))
        return result
    return set()


def _all_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        return [item for child in value.values() for item in _all_strings(child)]
    if isinstance(value, list):
        return [item for child in value for item in _all_strings(child)]
    return []


@pytest.mark.parametrize("path", TEMPLATE_PATHS)
def test_checked_in_templates_validate_offline(path: Path) -> None:
    document = contracts.load_and_validate(path)
    assert document["status"] == "template"
    assert document["document_grants_authority"] is False


@pytest.mark.parametrize("path", TEMPLATE_PATHS)
def test_template_pins_exact_schema_bytes(path: Path) -> None:
    document = _load(path)
    schema_path = REPO_ROOT / document["schema_contract"]["path"]
    assert schema_path.parent == READINESS_DIR
    assert hashlib.sha256(schema_path.read_bytes()).hexdigest() == document["schema_contract"]["sha256"]
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert schema["$schema"] == contracts.SCHEMA_URI
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False


def test_every_object_schema_is_closed() -> None:
    def visit(value: object) -> None:
        if isinstance(value, Mapping):
            if value.get("type") == "object":
                assert value.get("additionalProperties") is False
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for schema_path in READINESS_DIR.glob("*-v1.schema.json"):
        visit(json.loads(schema_path.read_text(encoding="utf-8")))


def test_manifest_pins_truthful_source_candidates_without_claiming_running_proof() -> None:
    from verdify_schemas import component_executor

    manifest = _load(MANIFEST_PATH)
    revisions = manifest["revision_set"]
    profile_path = REPO_ROOT / revisions["profile_artifact_path"]
    protocol_path = REPO_ROOT / revisions["protocol_template_path"]
    source_map_path = REPO_ROOT / manifest["nine_stream_endpoint"]["source_map_path"]

    assert hashlib.sha256(profile_path.read_bytes()).hexdigest() == revisions["profile_artifact_sha256"]
    assert hashlib.sha256(protocol_path.read_bytes()).hexdigest() == revisions["protocol_template_sha256"]
    source_map = json.loads(source_map_path.read_text(encoding="utf-8"))
    canonical_source_map = json.dumps(source_map, sort_keys=True, separators=(",", ":")).encode()
    assert (
        hashlib.sha256(canonical_source_map).hexdigest()
        == manifest["nine_stream_endpoint"]["source_map_canonical_sha256"]
    )
    assert revisions["source_grid_candidate_revision"] == component_executor.GRID_REVISION
    assert revisions["order_revision"] == component_executor.ORDER_REVISION
    assert component_executor.physical_execution_qualified(component_executor.GRID_REVISION) is False

    profile_artifact = json.loads(profile_path.read_text(encoding="utf-8"))
    expected_hashes = {
        profile_id: profile_artifact["profiles"][profile_id]["policy_state_content_sha256"]
        for profile_id in contracts.PROFILE_IDS
    }
    assert {row["profile_id"]: row["state_content_sha256"] for row in manifest["profiles"]} == expected_hashes
    assert all(row["field_count"] == 48 for row in manifest["profiles"])
    assert all(row["running_device_verified"] is False for row in manifest["profiles"])
    assert all(row["landing_status"] == "unverified" for row in manifest["profiles"])
    assert manifest["runtime_rounding_allowed"] is False
    assert manifest["transport"]["generalized_vector_mode_required"] == "off"
    assert manifest["transport"]["firmware_change_required"] is False


def test_manifest_covers_exact_multidisciplinary_physical_contract() -> None:
    manifest = _load(MANIFEST_PATH)

    assert tuple(row["persona_id"] for row in manifest["personas"]) == contracts.PERSONA_IDS
    assert tuple(row["profile_id"] for row in manifest["profiles"]) == contracts.PROFILE_IDS
    assert tuple(row["zone_id"] for row in manifest["localized_safety"]["measured_wall_zones"]) == (
        contracts.WALL_ZONE_IDS
    )
    assert tuple(row["equipment_id"] for row in manifest["nine_stream_endpoint"]["streams"]) == (
        contracts.EQUIPMENT_IDS
    )
    assert tuple(row["check_id"] for row in manifest["readiness"]["checks"]) == contracts.READINESS_CHECK_IDS
    assert tuple(row["inventory_id"] for row in manifest["inventory"]["actuators"]) == contracts.EQUIPMENT_IDS
    assert tuple(row["inventory_id"] for row in manifest["inventory"]["meters"]) == tuple(
        f"counter_{item}" for item in contracts.EQUIPMENT_IDS
    )

    proxy = manifest["localized_safety"]["center_screening_proxy"]
    assert proxy["source_ids"] == ["temp_avg", "vpd_avg", "rh_avg"]
    assert "never a center probe" in proxy["claim"]
    assert manifest["localized_safety"]["no_interpolation"] is True
    assert (
        "no true center, canopy, leaf-condensation, or crop-safety claim" in manifest["localized_safety"]["claim_limit"]
    )
    assert manifest["water_and_fertigation"]["experiment_changes_fertigation_authority"] is False
    assert manifest["nine_stream_endpoint"]["endpoint_id"] == "nine_control_state_minutes"
    assert "never energy" in manifest["nine_stream_endpoint"]["claim_limit"]
    vent = next(row for row in manifest["nine_stream_endpoint"]["streams"] if row["equipment_id"] == "vent")
    assert "open state" in vent["state_semantics"]
    assert "never motor" in vent["state_semantics"]


def test_manifest_is_truthfully_blocked_and_cannot_pass_ready_validation() -> None:
    manifest = _load(MANIFEST_PATH)
    assert tuple(manifest["readiness"]["blockers"]) == contracts.manifest_blockers(manifest)
    assert manifest["readiness"]["decision"] == "blocked"
    assert manifest["readiness"]["physical_actions_performed_by_template"] == 0
    assert manifest["approvals"]["scoped_probe"]["status"] == "pending"
    assert manifest["approvals"]["combined_physical"]["status"] == "pending"
    assert manifest["approvals"]["randomized_day_1"]["status"] == "outside_this_manifest"
    assert not any(row["physical_observation"] and row["status"] == "passed" for row in manifest["evidence"])
    with pytest.raises(contracts.ContractError, match="physical readiness is incomplete"):
        contracts.load_and_validate(MANIFEST_PATH, require_ready=True)


def test_manifest_rejects_false_ready_claim_and_passing_blocked_check() -> None:
    manifest = _load(MANIFEST_PATH)
    schema = _schema_for(manifest)

    false_ready = copy.deepcopy(manifest)
    false_ready["readiness"]["decision"] = "ready_for_combined_signoff"
    with pytest.raises(contracts.ContractError, match="must be blocked"):
        contracts.validate_document(false_ready, schema)

    false_pass = copy.deepcopy(manifest)
    false_pass["readiness"]["checks"][0]["status"] = "passed"
    with pytest.raises(contracts.ContractError, match="blocker cannot be marked passed"):
        contracts.validate_document(false_pass, schema)


def test_manifest_rejects_combined_signoff_before_scoped_probe_and_evidence() -> None:
    manifest = _load(MANIFEST_PATH)
    schema = _schema_for(manifest)
    combined = manifest["approvals"]["combined_physical"]
    combined.update(
        status="recorded",
        approval_ref="github.com/VerdifyConsultancy/verdify-platform/issues/641#combined",
        artifact_sha256="11" * 32,
        approved_at="2026-08-25T12:00:00.000000Z",
    )
    with pytest.raises(contracts.ContractError, match="must follow the scoped probe"):
        contracts.validate_document(manifest, schema)


def test_manifest_rejects_duplicate_persona_and_structural_drift() -> None:
    manifest = _load(MANIFEST_PATH)
    schema = _schema_for(manifest)

    duplicate = copy.deepcopy(manifest)
    duplicate["personas"][1]["persona_id"] = duplicate["personas"][0]["persona_id"]
    with pytest.raises(contracts.ContractError, match="duplicate|eight #641 personas"):
        contracts.validate_document(duplicate, schema)

    extra = copy.deepcopy(manifest)
    extra["device_command"] = "forbidden"
    with pytest.raises(contracts.ContractError, match="unexpected field"):
        contracts.validate_document(extra, schema)

    missing = copy.deepcopy(manifest)
    del missing["study"]
    with pytest.raises(contracts.ContractError, match="missing required field"):
        contracts.validate_document(missing, schema)


def test_loader_rejects_duplicate_yaml_mapping_keys(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.yaml"
    duplicate.write_text(CALIBRATION_PATH.read_text(encoding="utf-8") + "\nstatus: template\n", encoding="utf-8")
    with pytest.raises(contracts.ContractError, match="cannot read a YAML/JSON document"):
        contracts.load_and_validate(duplicate)


def test_templates_contain_no_secret_bearing_fields_or_fake_lock_placeholders() -> None:
    forbidden_keys = {
        "secret",
        "secret_value",
        "password",
        "token",
        "credential",
        "private_key",
        "authorization_header",
    }
    for path in TEMPLATE_PATHS:
        document = _load(path)
        assert _all_keys(document).isdisjoint(forbidden_keys)
        assert not any("TO-LOCK" in value or "TO_PIN" in value for value in _all_strings(document))

    manifest = _load(MANIFEST_PATH)
    manifest["personas"][0]["contact_role_ref"] = "operator@example.invalid"
    with pytest.raises(contracts.ContractError, match="required pattern"):
        contracts.validate_document(manifest, _schema_for(manifest))


def test_calibration_work_order_is_read_only_and_fail_closed() -> None:
    work_order = _load(CALIBRATION_PATH)
    assert work_order["device_write_allowed"] is False
    assert work_order["scope"]["measurement_only"] is True
    assert tuple(row["precondition_id"] for row in work_order["preconditions"]) == (
        contracts.CALIBRATION_PRECONDITION_IDS
    )
    assert tuple(row["step_id"] for row in work_order["steps"]) == contracts.CALIBRATION_STEP_IDS
    assert all(row["device_write"] is False for row in work_order["steps"])
    assert all(row["status"] == "pending" for row in work_order["preconditions"])
    assert all(row["maximum_absolute_error"] is None for row in work_order["scope"]["target_tolerances"])
    assert "Class-2 epoch change" in work_order["scope"]["adjustment_policy"]

    incomplete = copy.deepcopy(work_order)
    incomplete["status"] = "completed"
    with pytest.raises(contracts.ContractError, match="scheduled calibration requires|completed calibration requires"):
        contracts.validate_document(incomplete, _schema_for(incomplete))

    impossible_date = copy.deepcopy(work_order)
    impossible_date["window"]["starts_at"] = "2026-02-30T12:00:00.000000Z"
    with pytest.raises(contracts.ContractError, match="not a real UTC microsecond timestamp"):
        contracts.validate_document(impossible_date, _schema_for(impossible_date))


def test_probe_template_binds_exact_scoped_decision_and_no_live_authority() -> None:
    probe = _load(PROBE_PATH)
    binding = probe["binding"]
    assert binding["issue_number"] == 641
    assert binding["approval_kind"] == "scoped_probe"
    assert binding["scope_name"] == "commissioning_probe"
    assert binding["execution_phase"] == "commissioning"
    assert binding["operation_kind"] == "commissioning_probe"
    assert binding["lifecycle_status"] == "draft"
    for field in (
        "experiment_id",
        "work_id",
        "revision_bundle_sha256",
        "approval_ref",
        "artifact_sha256",
        "valid_from",
        "valid_until",
        "expires_at",
        "supervisor_role",
        "rescue_owner_role",
    ):
        assert binding[field] is None
    assert probe["target_profile"] == "commissioning_probe"
    assert probe["executable"] is False
    assert probe["scope"]["issue_refs"] == [424, 433]
    assert probe["scope"]["moderate_or_aggressive_allowed"] is False
    assert probe["scope"]["canary_aa_or_randomization_allowed"] is False
    assert tuple(row["step_id"] for row in probe["steps"]) == contracts.PROBE_STEP_IDS
    assert all(not row["experiment_write_possible"] or row["requires_runtime_authorization"] for row in probe["steps"])
    assert probe["safety"]["software_competes_with_rescue"] is False
    assert probe["safety"]["baseline_during_rescue_requires_facility_authorization"] is True
    assert probe["evidence_result"]["decision"] == "not_started"


def test_probe_template_rejects_injected_authority_and_incomplete_evidence() -> None:
    probe = _load(PROBE_PATH)
    schema = _schema_for(probe)

    executable = copy.deepcopy(probe)
    executable["executable"] = True
    with pytest.raises(contracts.ContractError, match="required literal"):
        contracts.validate_document(executable, schema)

    live_binding = copy.deepcopy(probe)
    live_binding["binding"]["work_id"] = "7ce9f6fa-aac3-4ec2-850d-366c2b951738"
    with pytest.raises(contracts.ContractError, match="cannot carry live authority"):
        contracts.validate_document(live_binding, schema)

    incomplete = copy.deepcopy(probe)
    incomplete["status"] = "evidence_complete"
    with pytest.raises(contracts.ContractError, match="planned probe requires|complete probe evidence requires"):
        contracts.validate_document(incomplete, schema)
