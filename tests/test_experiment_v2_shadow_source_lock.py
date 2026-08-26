from __future__ import annotations

import hashlib
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from experiment_orchestrator.contracts import (  # noqa: E402
    CLIMATE_VALUE_FIELDS,
    FORECAST_VALUE_FIELDS,
    OPENAI_SELECTOR_RESPONSE_FORMAT,
    OutcomePayload,
    SelectorIdentity,
    canonical_json_bytes,
    openai_messages_template_bytes,
)
from experiment_orchestrator.outcome import OutcomeIdentity  # noqa: E402
from scripts.prepare_experiment_v2_shadow import (  # noqa: E402
    COMMISSIONING_SCHEMA,
    INPUT_SCHEMA,
    build_packet,
    stable_experiment_uuid,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCK_DIR = REPO_ROOT / "research/planner-efficacy/protocols/shadow-v2"
LOCK = json.loads((LOCK_DIR / "source-lock-v1.json").read_text())


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")))


def _repo_path(path: str) -> str:
    return f"repo:{path}"


def test_source_lock_derivations_and_artifact_hashes_are_exact() -> None:
    study = LOCK["study"]
    namespace = study["assignment_namespace"]
    assert uuid.uuid5(uuid.NAMESPACE_URL, namespace["name"]) == uuid.UUID(namespace["uuid"])
    assert stable_experiment_uuid(study["study_id"], study["greenhouse_id"]) == study["experiment_id"]["uuid"]

    selector = LOCK["selector"]
    outcome = LOCK["outcome"]
    profiles = LOCK["profiles"]
    for path_key, hash_key in (
        ("artifact_path", "artifact_sha256"),
        ("context_schema_path", "context_schema_sha256"),
        ("lesson_snapshot_path", "lesson_snapshot_sha256"),
        ("prompt_path", "prompt_sha256"),
        ("system_message_path", "system_message_sha256"),
    ):
        assert _sha256(REPO_ROOT / selector[path_key]) == selector[hash_key]
    assert _sha256(REPO_ROOT / outcome["schema_path"]) == outcome["schema_sha256"]
    assert _sha256(REPO_ROOT / outcome["evaluator_source"]) == outcome["evaluator_source_sha256"]
    assert _sha256(REPO_ROOT / profiles["artifact_path"]) == profiles["artifact_sha256"]

    system_message = (REPO_ROOT / selector["system_message_path"]).read_text()
    prompt = (REPO_ROOT / selector["prompt_path"]).read_text()
    decoding = {
        "chat_template_kwargs": {"reasoning_effort": "medium"},
        "max_tokens": selector["max_tokens"],
        "response_format": OPENAI_SELECTOR_RESPONSE_FORMAT,
        "stream": False,
        "temperature": 0,
    }
    assert hashlib.sha256(canonical_json_bytes(decoding)).hexdigest() == selector["decoding_parameters_sha256"]
    assert (
        hashlib.sha256(openai_messages_template_bytes(system_message, prompt)).hexdigest()
        == selector["messages_sha256"]
    )

    outcome_identity = {
        "evaluator_source_sha256": outcome["evaluator_source_sha256"],
        "outcome_schema_sha256": outcome["schema_sha256"],
        "schema": "verdify-experiment-v2-outcome-evaluator-identity-v1",
        "temperature_duplicate_tolerance_f": 0.0,
        "vpd_duplicate_tolerance_kpa": 0.0,
    }
    assert (
        hashlib.sha256(canonical_json_bytes(outcome_identity, reject_forbidden_fields=False)).hexdigest()
        == outcome["identity_sha256_with_zero_tolerances"]
    )

    profile_document = json.loads((REPO_ROOT / profiles["artifact_path"]).read_text())
    wire = profile_document["wire_schema"]
    assert profiles["wire_bytes"] == 178
    assert profiles["wire_field_count"] == 48
    assert profiles["wire_manifest_digest_sha256"] == wire["manifest_digest_sha256"]
    assert profiles["wire_schema_version"] == wire["version"]
    for profile, row in profile_document["profiles"].items():
        state_sha256 = hashlib.sha256(
            b"verdify-policy-state-content-v1\x00"
            + bytes([wire["version"]])
            + bytes.fromhex(wire["manifest_digest_sha256"])
            + bytes.fromhex(row["wire_hex"])
        ).hexdigest()
        assert state_sha256 == row["policy_state_content_sha256"]
        assert state_sha256 == profiles["state_content_sha256"][profile]
    assert (
        _sha256(REPO_ROOT / "firmware/greenhouse/tunables.yaml")
        == (LOCK["candidate_source_revisions"]["grid_revision"]["source_entity_grid_sha256"])
    )
    assert (
        _sha256(REPO_ROOT / "verdify_schemas/tunable_registry.py")
        == (LOCK["candidate_source_revisions"]["registry_revision"]["source_sha256"])
    )


def test_source_locked_schemas_project_the_runtime_contracts() -> None:
    context = json.loads((LOCK_DIR / "selector-context-v2.schema.json").read_text())
    assert context["additionalProperties"] is False
    assert set(context["required"]) == {
        "schema",
        "local_date",
        "context_cutoff_at",
        "boundary_at",
        "climate_observations",
        "forecast_vintage",
    }
    climate = context["properties"]["climate_observations"]["items"]
    forecast = context["properties"]["forecast_vintage"]["items"]
    assert set(climate["properties"]["values"]["required"]) == CLIMATE_VALUE_FIELDS
    assert set(climate["properties"]["values"]["properties"]) == CLIMATE_VALUE_FIELDS
    assert climate["properties"]["values"]["properties"]["temp_avg_f"]["type"] == "number"
    assert climate["properties"]["values"]["properties"]["vpd_avg_kpa"]["type"] == "number"
    assert set(forecast["properties"]["values"]["required"]) == FORECAST_VALUE_FIELDS
    assert set(forecast["properties"]["values"]["properties"]) == FORECAST_VALUE_FIELDS

    outcome = json.loads((LOCK_DIR / "assigned-day-outcome-v2.schema.json").read_text())
    assert outcome["additionalProperties"] is False
    assert set(outcome["required"]) == set(
        OutcomePayload.missing(
            source_bundle_sha256="0" * 64,
            climate_reason="source_unavailable",
            equipment_reason="source_unavailable",
        ).as_mapping()
    )
    assert outcome["properties"]["schema"]["const"] == "verdify-assigned-day-outcome-v2"
    assert outcome["allOf"][1]["oneOf"][0]["properties"]["nine_control_state_minutes"]["maximum"] == 9720


def test_prompt_carries_exact_profile_meanings_needed_by_the_model() -> None:
    prompt = (LOCK_DIR / "selector-prompt-v1.txt").read_text()
    profiles = json.loads((REPO_ROOT / LOCK["profiles"]["artifact_path"]).read_text())["profiles"]
    baseline = profiles["baseline"]["values"]
    expected_differences = {
        profile: {field: value for field, value in profiles[profile]["values"].items() if value != baseline[field]}
        for profile in ("moderate", "aggressive")
    }
    assert expected_differences == {
        "moderate": {
            "cool_stage2_over_high_f": 0.8,
            "fog_escalation_kpa": 0.3,
            "mister_all_kpa": 1.35,
            "mister_pulse_gap_s": 40,
            "mister_pulse_on_s": 60,
            "mister_water_budget_gal": 220,
        },
        "aggressive": {
            "cool_stage2_over_high_f": 0.5,
            "min_fog_off_s": 30,
            "min_fog_on_s": 90,
            "mister_all_delay_s": 60,
            "mister_all_kpa": 1.2,
            "mister_engage_kpa": 1.0,
            "mister_pulse_on_s": 75,
            "mister_water_budget_gal": 250,
        },
    }
    for profile, differences in expected_differences.items():
        assert f"- {profile}:" in prompt
        for field, value in differences.items():
            rendered = f"{value:g}" if isinstance(value, float) else str(value)
            assert f"{field}={rendered}" in prompt


def test_source_lock_composes_with_generator_without_claiming_live_authority(tmp_path: Path) -> None:
    assert LOCK["authority"] == {
        "admission_open": False,
        "device_write": False,
        "packet_ready": False,
        "physical_approval": False,
        "reason": (
            "The deployed runtime identity, running-device entity grid, current firmware/config evidence, "
            "and commissioning state remain live-evidence inputs."
        ),
    }
    profile_document = json.loads((REPO_ROOT / LOCK["profiles"]["artifact_path"]).read_text())
    baseline = profile_document["profiles"]["baseline"]
    wire = profile_document["wire_schema"]
    commissioning_path = tmp_path / "commissioning-test-fixture.json"
    _canonical_write(
        commissioning_path,
        {
            "schema": COMMISSIONING_SCHEMA,
            "wire_manifest_digest_hex": wire["manifest_digest_sha256"],
            "wire_schema_version": wire["version"],
            "wire_vector_hex": baseline["wire_hex"],
        },
    )

    source_revisions = LOCK["candidate_source_revisions"]
    selector = LOCK["selector"]
    study = LOCK["study"]
    spec = {
        "audit_ref": LOCK["audit_ref"],
        "candidate": {
            "config_revision": source_revisions["config_revision"]["revision"],
            "firmware_revision": source_revisions["firmware_revision"]["revision"],
            "grid_revision": source_revisions["grid_revision"]["revision"],
            "registry_revision": source_revisions["registry_revision"]["revision"],
        },
        "profiles": {
            "commissioning_state_path": str(commissioning_path),
            "profile_artifact_path": _repo_path(LOCK["profiles"]["artifact_path"]),
        },
        "schema": INPUT_SCHEMA,
        "selector": {
            "context_schema_path": _repo_path(selector["context_schema_path"]),
            "egress_cidr": "192.168.7.10/32",
            "endpoint": selector["endpoint"],
            "expected_system_fingerprint": selector["expected_system_fingerprint"],
            "lesson_snapshot_path": _repo_path(selector["lesson_snapshot_path"]),
            "max_attempts": selector["max_attempts"],
            "max_tokens": selector["max_tokens"],
            "model_identifier": selector["model_identifier"],
            "model_revision": selector["model_revision"],
            "prompt_path": _repo_path(selector["prompt_path"]),
            "runtime_environment_sha256": "0" * 64,
            "selector_artifact_path": _repo_path(selector["artifact_path"]),
            "system_message_path": _repo_path(selector["system_message_path"]),
            "timeout_milliseconds": selector["timeout_milliseconds"],
        },
        "shadow": {
            "context_cutoff_at": "2026-08-27T05:45:00.000000Z",
            "local_date": "2026-08-27",
            "outcome_schema_path": _repo_path(LOCK["outcome"]["schema_path"]),
            "temperature_duplicate_tolerance_f": 0.0,
            "vpd_duplicate_tolerance_kpa": 0.0,
        },
        "source_git_sha": LOCK["source_git_sha"],
        "study": {
            "assignment_namespace_uuid": study["assignment_namespace"]["uuid"],
            "experiment_id": study["experiment_id"]["uuid"],
            "greenhouse_id": study["greenhouse_id"],
            "name": study["name"],
            "study_id": study["study_id"],
            "timezone": study["timezone"],
        },
    }
    input_path = tmp_path / "input.json"
    _canonical_write(input_path, spec)
    outputs = build_packet(repo_root=REPO_ROOT, input_path=input_path)

    selector_raw = outputs["selector-identity/identity.json"]
    selector_identity = SelectorIdentity.parse(selector_raw, hashlib.sha256(selector_raw).hexdigest())
    assert selector_identity.max_attempts == 2
    assert selector_identity.timeout_milliseconds == 60_000
    assert selector_identity.decoding_parameters["max_tokens"] == 512

    outcome_raw = outputs["outcome-identity/identity.json"]
    outcome_identity = OutcomeIdentity.parse(outcome_raw, hashlib.sha256(outcome_raw).hexdigest())
    assert outcome_identity.temperature_duplicate_tolerance_f == 0.0
    assert outcome_identity.vpd_duplicate_tolerance_kpa == 0.0

    manifest = json.loads(outputs["packet-manifest.json"])
    assert manifest["no_authority_claims"] == {
        "admission_open": False,
        "arm_or_mapping_present": False,
        "device_write": False,
        "physical_approval": False,
        "randomization_secret": False,
    }
