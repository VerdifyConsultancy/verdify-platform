from __future__ import annotations

import hashlib
import json
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from experiment_orchestrator.contracts import LifecyclePlan, SelectorIdentity  # noqa: E402
from experiment_orchestrator.outcome import OutcomeIdentity  # noqa: E402
from scripts.prepare_experiment_v2_shadow import (  # noqa: E402
    COMMISSIONING_SCHEMA,
    INPUT_SCHEMA,
    PreparationError,
    build_packet,
    stable_experiment_uuid,
    write_packet,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
ASSIGNMENT_NAMESPACE = "6ba7b810-9dad-11d1-80b4-00c04fd430c8"


def _canonical_write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")))


def _fixture(tmp_path: Path) -> tuple[Path, dict]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    profile_path = REPO_ROOT / "research/planner-efficacy/baseline/planner-switchback-v2-profiles.json"
    profiles = json.loads(profile_path.read_text())
    baseline = profiles["profiles"]["baseline"]
    wire = profiles["wire_schema"]
    commissioning_path = tmp_path / "commissioning.json"
    _canonical_write(
        commissioning_path,
        {
            "schema": COMMISSIONING_SCHEMA,
            "wire_manifest_digest_hex": wire["manifest_digest_sha256"],
            "wire_schema_version": wire["version"],
            "wire_vector_hex": baseline["wire_hex"],
        },
    )
    sources = {
        "context-schema.json": {"schema": "synthetic-context-schema-v1"},
        "lesson-snapshot.json": {"schema": "synthetic-lesson-snapshot-v1"},
        "outcome-schema.json": {"schema": "synthetic-outcome-schema-v1"},
        "selector-artifact.json": {"schema": "synthetic-selector-artifact-v1"},
    }
    for name, value in sources.items():
        _canonical_write(tmp_path / name, value)
    (tmp_path / "system.txt").write_text("Return exactly one safe profile as the required JSON object.")
    (tmp_path / "prompt.txt").write_text(
        "Select from baseline, moderate, or aggressive using only the supplied context."
    )
    spec = {
        "audit_ref": "gate2-shadow",
        "candidate": {
            "config_revision": "config-e7db51a9906e",
            "firmware_revision": "2026.7.10.1500.09ee886",
            "grid_revision": "grid-current",
            "registry_revision": "registry-current",
        },
        "profiles": {
            "commissioning_state_path": "commissioning.json",
            "profile_artifact_path": ("repo:research/planner-efficacy/baseline/planner-switchback-v2-profiles.json"),
        },
        "schema": INPUT_SCHEMA,
        "selector": {
            "context_schema_path": "context-schema.json",
            "egress_cidr": "0.0.0.0/0",
            "endpoint": "https://api.openai.com/v1/chat/completions",
            "expected_system_fingerprint": "openai-managed",
            "lesson_snapshot_path": "lesson-snapshot.json",
            "max_attempts": 2,
            "max_completion_tokens": 512,
            "model_identifier": "gpt-5.6-luna",
            "model_revision": "gpt-5.6-luna",
            "prompt_path": "prompt.txt",
            "runtime_environment_sha256": "a" * 64,
            "selector_artifact_path": "selector-artifact.json",
            "system_message_path": "system.txt",
            "timeout_milliseconds": 60_000,
        },
        "shadow": {
            "context_cutoff_at": "2026-08-27T05:45:00.000000Z",
            "local_date": "2026-08-27",
            "outcome_schema_path": "outcome-schema.json",
            "temperature_duplicate_tolerance_f": 0.0,
            "vpd_duplicate_tolerance_kpa": 0.0,
        },
        "source_git_sha": "a9ccefb213ab35d11158fd6ba02b400ac043274d",
        "study": {
            "assignment_namespace_uuid": ASSIGNMENT_NAMESPACE,
            "experiment_id": None,
            "greenhouse_id": "vallery",
            "name": "confirmed-component shadow",
            "study_id": "confirmed-component-2026-08",
            "timezone": "America/Denver",
        },
    }
    input_path = tmp_path / "input.json"
    _canonical_write(input_path, spec)
    return input_path, spec


def test_packet_is_canonical_mount_ready_redacted_and_non_actuating(tmp_path: Path) -> None:
    input_path, spec = _fixture(tmp_path)
    outputs = build_packet(repo_root=REPO_ROOT, input_path=input_path)
    assert set(outputs) == {
        "api-envelopes.json",
        "lifecycle-plan/plan.json",
        "outcome-identity/identity.json",
        "packet-manifest.json",
        "selector-identity/identity.json",
    }
    experiment_id = stable_experiment_uuid(spec["study"]["study_id"])

    identity_raw = outputs["selector-identity/identity.json"]
    identity = SelectorIdentity.parse(identity_raw, hashlib.sha256(identity_raw).hexdigest())
    assert identity.provider == "openai"
    assert identity.model_identifier == "gpt-5.6-luna"
    assert identity.model_revision == "gpt-5.6-luna"
    assert identity.expected_system_fingerprint == "openai-managed"
    assert identity.decoding_parameters == {
        "max_completion_tokens": 512,
        "reasoning_effort": "medium",
        "response_format": identity.decoding_parameters["response_format"],
        "stream": False,
    }

    outcome_raw = outputs["outcome-identity/identity.json"]
    outcome = OutcomeIdentity.parse(outcome_raw, hashlib.sha256(outcome_raw).hexdigest())
    assert outcome.temperature_duplicate_tolerance_f == 0.0
    assert outcome.vpd_duplicate_tolerance_kpa == 0.0
    plan_raw = outputs["lifecycle-plan/plan.json"]
    plan = LifecyclePlan.parse(plan_raw, hashlib.sha256(plan_raw).hexdigest(), experiment_id)
    assert plan.action == "shadow_schedule"
    assert plan.context_cutoff_at.isoformat() == "2026-08-27T05:45:00+00:00"

    envelopes = json.loads(outputs["api-envelopes.json"])
    commands = envelopes["commands"]
    assert [row["sequence"] for row in commands] == list(range(1, 8))
    assert commands[0]["body_static"]["experiment_id"] == experiment_id
    assert commands[1]["required_postcondition"]["state"] == {
        "admission_state": "closed",
        "component_enabled": False,
        "execution_phase": "shadow",
        "lease_generation": 0,
        "lifecycle_status": "draft",
        "revision_bundle_sha256": "<capture-from-receipt>",
    }
    assert [row["body_static"]["profile"] for row in commands[2:6]] == [
        "baseline",
        "moderate",
        "aggressive",
        "commissioning_probe",
    ]
    assert all("expected_revision_bundle_sha256" in row["body_bindings"] for row in commands[2:6])
    assert all(
        row["required_postcondition"]["result_id"] == "<capture-state-artifact-uuid-from-receipt>"
        for row in commands[2:6]
    )
    assert commands[6]["authentication"]["header"] == "X-Verdify-Operator-Token"
    assert commands[6]["required_postcondition"]["db_component_enabled"] is False
    serialized_commands = outputs["api-envelopes.json"].decode()
    assert "record_approval" not in serialized_commands
    assert "set_admission" not in serialized_commands
    assert "create_work" not in serialized_commands
    assert "Bearer " not in serialized_commands

    manifest = json.loads(outputs["packet-manifest.json"])
    assert manifest["api_surface"]["separate_shadow_transition_exists"] is False
    assert manifest["no_authority_claims"] == {
        "admission_open": False,
        "arm_or_mapping_present": False,
        "device_write": False,
        "physical_approval": False,
        "randomization_secret": False,
    }
    assert manifest["schedule"]["boundary_at"] == "2026-08-27T06:00:00.000000Z"
    assert manifest["schedule"]["latest_schedule_submission_at"] == "2026-08-26T18:00:00.000000Z"
    for path, digest in manifest["artifacts"].items():
        assert hashlib.sha256(outputs[path]).hexdigest() == digest

    output_dir = tmp_path / "packet"
    write_packet(outputs, output_dir)
    assert (output_dir / "lifecycle-plan/plan.json").read_bytes() == plan_raw
    assert oct((output_dir / "selector-identity/identity.json").stat().st_mode & 0o777) == "0o600"


def test_stable_experiment_uuid_is_safe_to_generate_before_deployment() -> None:
    first = stable_experiment_uuid("confirmed-component-2026-08")
    second = stable_experiment_uuid("confirmed-component-2026-08")
    assert first == second
    assert str(uuid.UUID(first)) == first
    assert first != stable_experiment_uuid("confirmed-component-2026-08-rerun")


def test_packet_refuses_unfrozen_or_unsafe_inputs(tmp_path: Path) -> None:
    input_path, spec = _fixture(tmp_path)
    spec["selector"]["max_completion_tokens"] = 511
    _canonical_write(input_path, spec)
    with pytest.raises(ValueError, match=r"\[512,16384\]"):
        build_packet(repo_root=REPO_ROOT, input_path=input_path)

    input_path, spec = _fixture(tmp_path / "second")
    spec["randomization_secret"] = "forbidden"
    _canonical_write(input_path, spec)
    with pytest.raises(PreparationError, match="exactly"):
        build_packet(repo_root=REPO_ROOT, input_path=input_path)


def test_packet_rejects_dst_crossing_shadow_cycle(tmp_path: Path) -> None:
    input_path, spec = _fixture(tmp_path)
    spec["shadow"]["local_date"] = "2026-11-01"
    spec["shadow"]["context_cutoff_at"] = "2026-11-01T05:45:00.000000Z"
    _canonical_write(input_path, spec)
    with pytest.raises(PreparationError, match="DST"):
        build_packet(repo_root=REPO_ROOT, input_path=input_path)


def test_offline_builder_has_no_network_cluster_database_or_device_client() -> None:
    source = (REPO_ROOT / "scripts/prepare_experiment_v2_shadow.py").read_text().lower()
    for forbidden in (
        "import asyncpg",
        "import httpx",
        "import requests",
        "import socket",
        "import subprocess",
        "kubectl",
        "aioesphomeapi",
        "mqtt",
    ):
        assert forbidden not in source
