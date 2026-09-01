#!/usr/bin/env python3
"""Build a canonical, non-actuating experiment-v2 shadow preparation packet.

This tool performs no network, database, cluster, credential, randomization, or
device operation. Every semantic input must already exist as a source-bound
file or an explicit immutable identifier. API output is deliberately a set of
redacted envelopes with receipt bindings, not directly executable curl.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
import uuid
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO_ROOT_DEFAULT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT_DEFAULT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT_DEFAULT))

from experiment_orchestrator.contracts import (
    LIFECYCLE_PLAN_SCHEMA,
    OPENAI_SELECTOR_IDENTITY_SCHEMA,
    OPENAI_SELECTOR_RESPONSE_FORMAT,
    OPENAI_SELECTOR_RESPONSE_SCHEMA,
    OUTCOME_IDENTITY_SCHEMA,
    LifecyclePlan,
    SelectorIdentity,
    canonical_json_bytes,
    format_utc_timestamp,
    openai_messages_template_bytes,
    parse_utc_timestamp,
)
from experiment_orchestrator.outcome import OutcomeIdentity

INPUT_SCHEMA = "verdify-experiment-v2-shadow-preparation-input-v1"
PACKET_SCHEMA = "verdify-experiment-v2-shadow-preparation-packet-v1"
ENVELOPE_SCHEMA = "verdify-redacted-api-command-envelope-v1"
COMMISSIONING_SCHEMA = "verdify-experiment-v2-commissioning-state-v1"
EXPERIMENT_UUID_NAMESPACE = uuid.uuid5(
    uuid.NAMESPACE_URL,
    "https://verdify.com/identities/confirmed-component-experiment-v2",
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
HEX_178_RE = re.compile(r"^[0-9a-f]{356}$")
AUDIT_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/#@-]{0,95}$")
CREDENTIAL_TEXT_RE = re.compile(r"(?i)(?:api[_-]?key|authorization|bearer|password|secret|token)\s*[:=]")
PROFILE_NAMES = ("baseline", "moderate", "aggressive")
OPENAI_CONTRACT = {
    "base_endpoint": "https://api.openai.com/v1",
    "chat_completions_endpoint": "https://api.openai.com/v1/chat/completions",
    "egress_cidr": "0.0.0.0/0",
    "max_model_len": 1_050_000,
    "max_output_tokens_default": 128_000,
    "model_identifier": "gpt-5.6-luna",
    "model_revision": "gpt-5.6-luna",
    "modalities": ["text"],
    "reasoning_effort_default": "medium",
    "system_fingerprint": "openai-managed",
    "provider": "openai",
}


class PreparationError(ValueError):
    """An offline packet cannot be proven complete and safe."""


def _exact(value: object, fields: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise PreparationError(f"{label} must contain exactly {sorted(fields)}")
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PreparationError(f"duplicate JSON field {key!r}")
        result[key] = value
    return result


def _read_json(path: Path, label: str, *, maximum_bytes: int = 4 * 1024 * 1024) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise PreparationError(f"{label} is unavailable: {path}") from exc
    if not raw or len(raw) > maximum_bytes:
        raise PreparationError(f"{label} is empty or exceeds its byte bound")
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PreparationError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise PreparationError(f"{label} root must be an object")
    return value


def _source_path(raw: object, *, repo_root: Path, input_dir: Path, label: str) -> Path:
    if not isinstance(raw, str) or not raw:
        raise PreparationError(f"{label} must name one source file")
    if raw.startswith("repo:"):
        root = repo_root
        relative = raw.removeprefix("repo:")
    else:
        root = input_dir
        relative = raw
    unresolved = root / relative
    candidate = unresolved.resolve()
    if unresolved.is_symlink() or not candidate.is_relative_to(root.resolve()) or not candidate.is_file():
        raise PreparationError(f"{label} must resolve to one regular file inside its declared source root")
    return candidate


def _read_text(path: Path, label: str, *, maximum_bytes: int = 32_768) -> str:
    raw = path.read_bytes()
    if not raw or len(raw) > maximum_bytes:
        raise PreparationError(f"{label} is empty or exceeds {maximum_bytes} bytes")
    try:
        value = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PreparationError(f"{label} is not UTF-8") from exc
    if unicodedata.normalize("NFC", value) != value:
        raise PreparationError(f"{label} is not Unicode NFC")
    if CREDENTIAL_TEXT_RE.search(value):
        raise PreparationError(f"{label} appears to contain credential material")
    return value


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise PreparationError(f"{label} must be lower-case SHA-256 hex")
    return value


def _bounded_text(value: object, label: str, maximum: int = 200) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum or unicodedata.normalize("NFC", value) != value:
        raise PreparationError(f"{label} must be bounded nonempty NFC text")
    return value


def _canonical_uuid(value: object, label: str) -> str:
    try:
        parsed = uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise PreparationError(f"{label} must be a UUID") from exc
    if str(parsed) != value:
        raise PreparationError(f"{label} must use canonical UUID spelling")
    return str(parsed)


def stable_experiment_uuid(study_id: str, greenhouse_id: str = "vallery") -> str:
    """Derive a stable public identifier; this is never a randomization secret."""

    study = _bounded_text(study_id, "study_id")
    greenhouse = _bounded_text(greenhouse_id, "greenhouse_id", 64)
    return str(uuid.uuid5(EXPERIMENT_UUID_NAMESPACE, f"{greenhouse}\x00{study}"))


def _state_content_sha256(version: int, manifest_hex: str, vector_hex: str) -> str:
    if type(version) is not int or not 0 <= version <= 255:
        raise PreparationError("wire schema version must be one byte")
    if not SHA256_RE.fullmatch(manifest_hex) or not HEX_178_RE.fullmatch(vector_hex):
        raise PreparationError("state identity requires manifest[32] and vector[178]")
    return _sha256_bytes(
        b"verdify-policy-state-content-v1"
        + b"\x00"
        + bytes([version])
        + bytes.fromhex(manifest_hex)
        + bytes.fromhex(vector_hex)
    )


def _profile_states(profile_path: Path, commissioning_path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    artifact = _read_json(profile_path, "profile artifact")
    profiles = artifact.get("profiles")
    wire = artifact.get("wire_schema")
    if not isinstance(profiles, Mapping) or set(profiles) != set(PROFILE_NAMES) or not isinstance(wire, Mapping):
        raise PreparationError("profile artifact does not contain the exact three v2 profiles and wire schema")
    version = wire.get("version")
    manifest = wire.get("manifest_digest_sha256")
    if type(version) is not int or not 0 <= version <= 255 or not isinstance(manifest, str):
        raise PreparationError("profile artifact wire schema is malformed")
    _sha256(manifest, "profile wire manifest")
    states: dict[str, dict[str, Any]] = {}
    for profile in PROFILE_NAMES:
        row = profiles[profile]
        if not isinstance(row, Mapping):
            raise PreparationError(f"{profile} profile is malformed")
        vector = row.get("wire_hex")
        claimed = row.get("policy_state_content_sha256")
        if row.get("field_count") != 48 or row.get("wire_bytes") != 178 or not isinstance(vector, str):
            raise PreparationError(f"{profile} profile lacks the exact 48-field/178-byte contract")
        calculated = _state_content_sha256(version, manifest, vector)
        if claimed != calculated:
            raise PreparationError(f"{profile} state-content hash differs from its exact wire bytes")
        states[profile] = {
            "policy_state_content_sha256": calculated,
            "wire_manifest_digest_hex": manifest,
            "wire_schema_version": version,
            "wire_vector_hex": vector,
        }

    commissioning = _exact(
        _read_json(commissioning_path, "commissioning state"),
        {"schema", "wire_manifest_digest_hex", "wire_schema_version", "wire_vector_hex"},
        "commissioning state",
    )
    if commissioning["schema"] != COMMISSIONING_SCHEMA:
        raise PreparationError("commissioning state schema mismatch")
    if (commissioning["wire_schema_version"], commissioning["wire_manifest_digest_hex"]) != (
        version,
        manifest,
    ):
        raise PreparationError("commissioning state differs from the profile wire contract")
    if (
        type(commissioning["wire_schema_version"]) is not int
        or not isinstance(commissioning["wire_manifest_digest_hex"], str)
        or not isinstance(commissioning["wire_vector_hex"], str)
    ):
        raise PreparationError("commissioning state wire identity is malformed")
    commissioning_hash = _state_content_sha256(
        commissioning["wire_schema_version"],
        commissioning["wire_manifest_digest_hex"],
        commissioning["wire_vector_hex"],
    )
    states["commissioning_probe"] = {
        "policy_state_content_sha256": commissioning_hash,
        "wire_manifest_digest_hex": commissioning["wire_manifest_digest_hex"],
        "wire_schema_version": commissioning["wire_schema_version"],
        "wire_vector_hex": commissioning["wire_vector_hex"],
    }
    return states, {
        "commissioning_state_sha256": _sha256_file(commissioning_path),
        "profile_artifact_sha256": _sha256_file(profile_path),
    }


def _audit_ref(base: str, suffix: str) -> str:
    value = f"{base}/{suffix}"
    if not AUDIT_REF_RE.fullmatch(value):
        raise PreparationError(f"audit reference {value!r} violates the API contract")
    return value


def _envelope(
    *,
    sequence: int,
    method: str,
    path: str,
    body_static: Mapping[str, Any] | None = None,
    body_bindings: Mapping[str, str] | None = None,
    postcondition: Mapping[str, Any] | None = None,
    auth_header: str = "X-Verdify-Experiment-Token",
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "authentication": {
            "header": auth_header,
            "value": "<inject-at-execution; never persist>",
        },
        "method": method,
        "path": path,
        "schema": ENVELOPE_SCHEMA,
        "sequence": sequence,
    }
    if body_static is not None:
        result["body_static"] = dict(body_static)
    if body_bindings:
        result["body_bindings"] = dict(body_bindings)
    if postcondition:
        result["required_postcondition"] = dict(postcondition)
    return result


def _existing_state_fields() -> dict[str, Any]:
    return {
        "expected_admission_state": "closed",
        "expected_component_enabled": False,
        "expected_execution_phase": "shadow",
        "expected_lease_generation": 0,
        "expected_lifecycle_status": "draft",
    }


def _closed_shadow_state(*, revision: str) -> dict[str, Any]:
    return {
        "admission_state": "closed",
        "component_enabled": False,
        "execution_phase": "shadow",
        "lease_generation": 0,
        "lifecycle_status": "draft",
        "revision_bundle_sha256": revision,
    }


def _api_envelopes(
    *,
    experiment_id: str,
    name: str,
    study_id: str,
    assignment_namespace_uuid: str,
    candidate: Mapping[str, Any],
    states: Mapping[str, Mapping[str, Any]],
    audit_ref: str,
) -> dict[str, Any]:
    path = f"/api/v1/experiments/{experiment_id}/component-control/commands"
    envelopes: list[dict[str, Any]] = [
        _envelope(
            sequence=1,
            method="POST",
            path="/api/v1/experiments",
            body_static={
                "experiment_id": experiment_id,
                "greenhouse_id": "vallery",
                "kind": "randomized",
                "name": name,
                "timezone": "America/Denver",
            },
            postcondition={"kind": "randomized", "protocol_version": 1, "status": "draft"},
        ),
        _envelope(
            sequence=2,
            method="POST",
            path=path,
            body_static={
                "action": "configure",
                "assignment_namespace_uuid": assignment_namespace_uuid,
                "audit_ref": _audit_ref(audit_ref, "configure"),
                "config_revision": candidate["config_revision"],
                "expected_admission_state": "closed",
                "expected_component_enabled": False,
                "expected_execution_phase": None,
                "expected_lease_generation": 0,
                "expected_lifecycle_status": "draft",
                "expected_protocol_version": 1,
                "expected_revision_bundle_sha256": None,
                "firmware_revision": candidate["firmware_revision"],
                "grid_revision": candidate["grid_revision"],
                "registry_revision": candidate["registry_revision"],
                "study_id": study_id,
            },
            postcondition={
                "action": "configure",
                "experiment_id": experiment_id,
                "previous_state": None,
                "recorded_at": "<valid-RFC3339-timestamp>",
                "result_id": experiment_id,
                "state": _closed_shadow_state(revision="<capture-from-receipt>"),
            },
        ),
    ]
    for sequence, profile in enumerate((*PROFILE_NAMES, "commissioning_probe"), start=3):
        state = states[profile]
        envelopes.append(
            _envelope(
                sequence=sequence,
                method="POST",
                path=path,
                body_static={
                    "action": "register_state",
                    "audit_ref": _audit_ref(audit_ref, f"state-{profile}"),
                    **_existing_state_fields(),
                    "profile": profile,
                    "wire_manifest_digest_hex": state["wire_manifest_digest_hex"],
                    "wire_schema_version": state["wire_schema_version"],
                    "wire_vector_hex": state["wire_vector_hex"],
                },
                body_bindings={
                    "expected_revision_bundle_sha256": (
                        "sequence[2].response.state.revision_bundle_sha256; abort on absence or drift"
                    )
                },
                postcondition={
                    "action": "register_state",
                    "experiment_id": experiment_id,
                    "previous_state": "<must-equal-sequence[2].response.state>",
                    "recorded_at": "<valid-RFC3339-timestamp>",
                    "result_id": "<capture-state-artifact-uuid-from-receipt>",
                    "state": "<must-equal-sequence[2].response.state>",
                },
            )
        )
    envelopes.append(
        _envelope(
            sequence=7,
            method="GET",
            path=f"/api/v1/experiments/{experiment_id}/component-status",
            auth_header="X-Verdify-Operator-Token",
            postcondition={
                "admission_state": "closed",
                "db_component_enabled": False,
                "current_work": None,
                "execution_phase": "shadow",
                "kind": "randomized",
                "lifecycle_status": "draft",
                "protocol_version": 2,
                "revision_bundle_sha256": "<must-equal-sequence[2].response.state.revision_bundle_sha256>",
            },
        )
    )
    return {
        "commands": envelopes,
        "execution_contract": (
            "execute strictly in sequence; bind only the named prior receipt field; "
            "abort and refresh on every mismatch; never infer or retry changed content"
        ),
        "experiment_id": experiment_id,
        "schema": "verdify-experiment-v2-shadow-api-envelopes-v1",
    }


def _schedule_contract(local_date_raw: object, cutoff_raw: object) -> dict[str, Any]:
    if not isinstance(local_date_raw, str):
        raise PreparationError("shadow.local_date must be YYYY-MM-DD")
    try:
        local_date = date.fromisoformat(local_date_raw)
    except ValueError as exc:
        raise PreparationError("shadow.local_date must be YYYY-MM-DD") from exc
    if local_date.isoformat() != local_date_raw:
        raise PreparationError("shadow.local_date must be canonical YYYY-MM-DD")
    try:
        cutoff = parse_utc_timestamp(cutoff_raw, "shadow.context_cutoff_at")
    except ValueError as exc:
        raise PreparationError(str(exc)) from exc
    timezone = ZoneInfo("America/Denver")
    boundary_local = datetime.combine(local_date, datetime.min.time(), timezone)
    outcome_end_local = datetime.combine(local_date + timedelta(days=1), datetime.min.time(), timezone)
    if boundary_local.utcoffset() != outcome_end_local.utcoffset():
        raise PreparationError("shadow cycle crosses a DST offset and is forbidden by protocol v2")
    boundary = boundary_local.astimezone(UTC)
    outcome_end = outcome_end_local.astimezone(UTC)
    if not boundary - timedelta(hours=24) <= cutoff < boundary:
        raise PreparationError("shadow cutoff must be in the 24 hours before the local-day boundary")
    latest_submission = min(cutoff, boundary - timedelta(hours=12))
    return {
        "boundary_at": format_utc_timestamp(boundary),
        "context_cutoff_at": format_utc_timestamp(cutoff),
        "latest_schedule_submission_at": format_utc_timestamp(latest_submission),
        "local_date": local_date_raw,
        "outcome_end_at": format_utc_timestamp(outcome_end),
    }


def build_packet(*, repo_root: Path, input_path: Path) -> dict[str, bytes]:
    repo_root = repo_root.resolve()
    input_path = input_path.resolve()
    spec = _exact(
        _read_json(input_path, "preparation input", maximum_bytes=256 * 1024),
        {"audit_ref", "candidate", "profiles", "schema", "selector", "shadow", "source_git_sha", "study"},
        "preparation input",
    )
    if spec["schema"] != INPUT_SCHEMA:
        raise PreparationError("preparation input schema mismatch")
    if not isinstance(spec["source_git_sha"], str) or not GIT_SHA_RE.fullmatch(spec["source_git_sha"]):
        raise PreparationError("source_git_sha must be lower-case 40-hex")
    study = _exact(
        spec["study"],
        {"assignment_namespace_uuid", "experiment_id", "greenhouse_id", "name", "study_id", "timezone"},
        "study",
    )
    if study["greenhouse_id"] != "vallery" or study["timezone"] != "America/Denver":
        raise PreparationError("shadow packet is bound to Vallery/America-Denver")
    study_id = _bounded_text(study["study_id"], "study_id")
    name = _bounded_text(study["name"], "experiment name")
    experiment_id = (
        stable_experiment_uuid(study_id)
        if study["experiment_id"] is None
        else _canonical_uuid(study["experiment_id"], "experiment_id")
    )
    assignment_namespace_uuid = _canonical_uuid(
        study["assignment_namespace_uuid"],
        "assignment_namespace_uuid",
    )
    candidate = _exact(
        spec["candidate"],
        {"config_revision", "firmware_revision", "grid_revision", "registry_revision"},
        "candidate",
    )
    candidate = {key: _bounded_text(value, f"candidate.{key}") for key, value in candidate.items()}
    profile_inputs = _exact(
        spec["profiles"],
        {"commissioning_state_path", "profile_artifact_path"},
        "profiles",
    )
    profile_path = _source_path(
        profile_inputs["profile_artifact_path"],
        repo_root=repo_root,
        input_dir=input_path.parent,
        label="profile artifact",
    )
    commissioning_path = _source_path(
        profile_inputs["commissioning_state_path"],
        repo_root=repo_root,
        input_dir=input_path.parent,
        label="commissioning state",
    )
    states, state_sources = _profile_states(profile_path, commissioning_path)

    selector = _exact(
        spec["selector"],
        {
            "context_schema_path",
            "egress_cidr",
            "endpoint",
            "expected_system_fingerprint",
            "lesson_snapshot_path",
            "max_attempts",
            "max_completion_tokens",
            "model_identifier",
            "model_revision",
            "prompt_path",
            "runtime_environment_sha256",
            "selector_artifact_path",
            "system_message_path",
            "timeout_milliseconds",
        },
        "selector",
    )
    locked_openai = {
        "egress_cidr": OPENAI_CONTRACT["egress_cidr"],
        "endpoint": OPENAI_CONTRACT["chat_completions_endpoint"],
        "expected_system_fingerprint": OPENAI_CONTRACT["system_fingerprint"],
        "model_identifier": OPENAI_CONTRACT["model_identifier"],
        "model_revision": OPENAI_CONTRACT["model_revision"],
    }
    if any(selector[field] != expected for field, expected in locked_openai.items()):
        raise PreparationError("selector endpoint/model/egress differs from the verified OpenAI contract")
    paths = {
        field: _source_path(
            selector[field],
            repo_root=repo_root,
            input_dir=input_path.parent,
            label=f"selector.{field}",
        )
        for field in (
            "context_schema_path",
            "lesson_snapshot_path",
            "prompt_path",
            "selector_artifact_path",
            "system_message_path",
        )
    }
    system_message = _read_text(paths["system_message_path"], "selector system message")
    prompt = _read_text(paths["prompt_path"], "selector prompt")
    context_schema_sha256 = _sha256_file(paths["context_schema_path"])
    lesson_snapshot_sha256 = _sha256_file(paths["lesson_snapshot_path"])
    selector_artifact_sha256 = _sha256_file(paths["selector_artifact_path"])
    runtime_environment_sha256 = _sha256(
        selector["runtime_environment_sha256"],
        "selector.runtime_environment_sha256",
    )
    decoding = {
        "max_completion_tokens": selector["max_completion_tokens"],
        "reasoning_effort": "medium",
        "response_format": OPENAI_SELECTOR_RESPONSE_FORMAT,
        "stream": False,
    }
    selector_identity = {
        "context_schema_sha256": context_schema_sha256,
        "decoding_parameters": decoding,
        "decoding_parameters_sha256": _sha256_bytes(canonical_json_bytes(decoding)),
        "expected_system_fingerprint": selector["expected_system_fingerprint"],
        "lesson_snapshot_sha256": lesson_snapshot_sha256,
        "max_attempts": selector["max_attempts"],
        "messages_sha256": _sha256_bytes(openai_messages_template_bytes(system_message, prompt)),
        "model_identifier": selector["model_identifier"],
        "model_revision": selector["model_revision"],
        "prompt": prompt,
        "prompt_sha256": _sha256_bytes(prompt.encode("utf-8")),
        "provider": "openai",
        "response_schema_revision": OPENAI_SELECTOR_RESPONSE_SCHEMA,
        "runtime_environment_sha256": runtime_environment_sha256,
        "schema": OPENAI_SELECTOR_IDENTITY_SCHEMA,
        "system_message": system_message,
        "system_message_sha256": _sha256_bytes(system_message.encode("utf-8")),
        "timeout_milliseconds": selector["timeout_milliseconds"],
        "tool_contract_revision": "none-v1",
    }
    selector_identity_bytes = canonical_json_bytes(selector_identity)
    selector_identity_sha256 = _sha256_bytes(selector_identity_bytes)
    SelectorIdentity.parse(selector_identity_bytes, selector_identity_sha256)

    outcome = _exact(
        spec["shadow"],
        {
            "context_cutoff_at",
            "local_date",
            "outcome_schema_path",
            "temperature_duplicate_tolerance_f",
            "vpd_duplicate_tolerance_kpa",
        },
        "shadow",
    )
    outcome_schema_path = _source_path(
        outcome["outcome_schema_path"],
        repo_root=repo_root,
        input_dir=input_path.parent,
        label="outcome schema",
    )
    outcome_schema_sha256 = _sha256_file(outcome_schema_path)
    evaluator_path = repo_root / "research/planner-efficacy/switchback/v2_outcomes.py"
    if not evaluator_path.is_file():
        raise PreparationError("locked outcome evaluator source is unavailable")
    outcome_identity = {
        "evaluator_source_sha256": _sha256_file(evaluator_path),
        "outcome_schema_sha256": outcome_schema_sha256,
        "schema": OUTCOME_IDENTITY_SCHEMA,
        "temperature_duplicate_tolerance_f": outcome["temperature_duplicate_tolerance_f"],
        "vpd_duplicate_tolerance_kpa": outcome["vpd_duplicate_tolerance_kpa"],
    }
    outcome_identity_bytes = canonical_json_bytes(outcome_identity, reject_forbidden_fields=False)
    endpoint_artifact_sha256 = _sha256_bytes(outcome_identity_bytes)
    OutcomeIdentity.parse(outcome_identity_bytes, endpoint_artifact_sha256)
    schedule = _schedule_contract(outcome["local_date"], outcome["context_cutoff_at"])
    lifecycle_plan = {
        "action": "shadow_schedule",
        "context_cutoff_at": schedule["context_cutoff_at"],
        "context_schema_sha256": context_schema_sha256,
        "endpoint_artifact_sha256": endpoint_artifact_sha256,
        "experiment_id": experiment_id,
        "local_date": schedule["local_date"],
        "outcome_schema_sha256": outcome_schema_sha256,
        "schema": LIFECYCLE_PLAN_SCHEMA,
        "selector_artifact_sha256": selector_artifact_sha256,
        "selector_identity_sha256": selector_identity_sha256,
    }
    lifecycle_plan_bytes = canonical_json_bytes(lifecycle_plan, reject_forbidden_fields=False)
    lifecycle_plan_sha256 = _sha256_bytes(lifecycle_plan_bytes)
    LifecyclePlan.parse(lifecycle_plan_bytes, lifecycle_plan_sha256, experiment_id)
    audit_ref = _bounded_text(spec["audit_ref"], "audit_ref", 64)
    api_envelopes = _api_envelopes(
        experiment_id=experiment_id,
        name=name,
        study_id=study_id,
        assignment_namespace_uuid=assignment_namespace_uuid,
        candidate=candidate,
        states=states,
        audit_ref=audit_ref,
    )
    api_envelopes_bytes = canonical_json_bytes(api_envelopes, reject_forbidden_fields=False)

    outputs = {
        "api-envelopes.json": api_envelopes_bytes,
        "lifecycle-plan/plan.json": lifecycle_plan_bytes,
        "outcome-identity/identity.json": outcome_identity_bytes,
        "selector-identity/identity.json": selector_identity_bytes,
    }
    manifest = {
        "api_surface": {
            "configure_establishes": "protocol-v2 draft/shadow/closed/component-disabled",
            "separate_shadow_transition_exists": False,
            "state_profiles_registered": [*PROFILE_NAMES, "commissioning_probe"],
        },
        "artifacts": {path: _sha256_bytes(raw) for path, raw in sorted(outputs.items())},
        "openai_contract": OPENAI_CONTRACT,
        "experiment_id": experiment_id,
        "experiment_id_derivation": (
            "explicit input" if study["experiment_id"] is not None else "UUIDv5 of vallery NUL study_id"
        ),
        "input_sha256": _sha256_file(input_path),
        "no_authority_claims": {
            "admission_open": False,
            "arm_or_mapping_present": False,
            "device_write": False,
            "physical_approval": False,
            "randomization_secret": False,
        },
        "required_runtime_actions": [
            "inject API authentication only at execution time",
            "execute API envelopes in order and bind the exact configure receipt revision",
            "mount the three exact artifact files under their existing optional ConfigMap volumes",
            "bind VERDIFY_EXPERIMENT_V2_LIFECYCLE_PLAN_SHA256 to the emitted plan hash",
            "schedule before latest_schedule_submission_at and remove the one-shot plan after its durable receipt",
            "observe one complete selector/state-receipt/outcome-preview cycle with zero experiment device calls",
        ],
        "schedule": schedule,
        "schema": PACKET_SCHEMA,
        "source_artifacts": {
            **state_sources,
            "context_schema_sha256": context_schema_sha256,
            "lesson_snapshot_sha256": lesson_snapshot_sha256,
            "locked_evaluator_source_sha256": outcome_identity["evaluator_source_sha256"],
            "outcome_schema_sha256": outcome_schema_sha256,
            "runtime_environment_sha256": runtime_environment_sha256,
            "selector_artifact_sha256": selector_artifact_sha256,
            "source_git_sha": spec["source_git_sha"],
        },
        "state_content_sha256": {
            profile: states[profile]["policy_state_content_sha256"]
            for profile in (*PROFILE_NAMES, "commissioning_probe")
        },
    }
    outputs["packet-manifest.json"] = canonical_json_bytes(manifest, reject_forbidden_fields=False)
    return outputs


def write_packet(outputs: Mapping[str, bytes], output_dir: Path) -> None:
    output_dir = output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise PreparationError("output directory must be absent or empty")
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    for relative, raw in sorted(outputs.items()):
        target = output_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        target.write_bytes(raw)
        os.chmod(target, 0o600)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT_DEFAULT)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        outputs = build_packet(repo_root=args.repo_root, input_path=args.input)
        write_packet(outputs, args.output_dir)
    except ValueError as exc:
        parser.exit(2, f"shadow preparation refused: {exc}\n")
    for relative, raw in sorted(outputs.items()):
        print(f"{relative} sha256={_sha256_bytes(raw)}")


if __name__ == "__main__":
    main()
