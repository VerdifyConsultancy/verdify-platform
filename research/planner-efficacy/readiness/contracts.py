"""Validate the versioned, non-operative experiment-v2 field artifacts.

The repository intentionally does not add a general JSON Schema dependency for
three small source contracts.  This module implements the exact JSON Schema
2020-12 vocabulary used by those contracts and rejects every other keyword.
It then applies cross-field rules that JSON Schema alone cannot express cleanly,
including the ordered #641 decisions and the fact that a document never grants
device authority.

The validator reads files only.  It has no network, database, cluster, provider,
or device client.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

SCHEMA_URI = "https://json-schema.org/draft/2020-12/schema"

PERSONA_IDS: tuple[str, ...] = (
    "hvac_climate",
    "water_systems",
    "fertigation",
    "controls",
    "systems_integration",
    "iot_sensor",
    "data_science",
    "facility_operations",
)
PROFILE_IDS: tuple[str, ...] = ("baseline", "moderate", "aggressive")
WALL_ZONE_IDS: tuple[str, ...] = ("north", "east", "south", "west")
EQUIPMENT_IDS: tuple[str, ...] = (
    "heat1",
    "heat2",
    "vent",
    "fan1",
    "fan2",
    "fog",
    "mister_south",
    "mister_west",
    "mister_center",
)
READINESS_CHECK_IDS: tuple[str, ...] = (
    "revision_bundle_pinned",
    "running_grid_profiles_land",
    "setter_readback_routes_48_48",
    "treatment_clamps_11_11",
    "activation_rollback_prefix_replay",
    "full48_recovery_prefix_replay",
    "physical_inventory_provenance",
    "calibrations_current",
    "hvac_capacity_and_interlocks",
    "water_fertigation_commissioning",
    "localized_wall_zone_safety",
    "center_proxy_and_claim_limits",
    "air_dew_reference_and_manual_inspection",
    "nine_stream_counter_reconciliation",
    "intervention_calendar_and_care_override",
    "commissioning_probe_evidence",
    "facility_rescue_rehearsal",
    "multidisciplinary_record",
)

CALIBRATION_PRECONDITION_IDS: tuple[str, ...] = (
    "facility_window_confirmed",
    "reference_traceability_current",
    "clock_quality_confirmed",
    "safe_access_confirmed",
    "experiment_exposure_closed",
)
CALIBRATION_STEP_IDS: tuple[str, ...] = (
    "capture_as_found",
    "compare_independent_reference",
    "capture_wetting_and_recovery_bias",
    "evaluate_locked_tolerance",
    "capture_as_left",
    "independent_verification",
)
PROBE_STEP_IDS: tuple[str, ...] = (
    "capture_read_only_preflight",
    "verify_exact_typed_work",
    "capture_served_control_readback",
    "prove_quiet_writer_nonstarvation",
    "prove_one_drift_one_write",
    "prove_reconnect_generation_fence",
    "rehearse_full48_baseline_recovery",
    "confirm_two_baseline_epochs",
    "close_typed_work_and_preserve_evidence",
)

_SCHEMA_FILES = {
    "verdify-experiment-v2-physical-readiness": "physical-readiness-v1.schema.json",
    "verdify-experiment-v2-calibration-work-order": "calibration-work-order-v1.schema.json",
    "verdify-experiment-v2-commissioning-probe-work-order": "commissioning-probe-work-order-v1.schema.json",
}

_ALLOWED_SCHEMA_KEYS = frozenset(
    {
        "$schema",
        "$id",
        "$defs",
        "$ref",
        "title",
        "description",
        "type",
        "const",
        "enum",
        "required",
        "properties",
        "additionalProperties",
        "items",
        "minItems",
        "maxItems",
        "uniqueItems",
        "minLength",
        "maxLength",
        "pattern",
        "format",
        "minimum",
        "maximum",
    }
)
_SENSITIVE_KEYS = frozenset(
    {
        "secret",
        "secret_value",
        "password",
        "token",
        "credential",
        "private_key",
        "authorization_header",
    }
)


class ContractError(ValueError):
    """A field artifact is structurally or semantically unsafe."""


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects ambiguous duplicate mapping keys."""


def _construct_unique_mapping(loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in result
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable mapping key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found a duplicate mapping key",
                key_node.start_mark,
            )
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _fail(path: str, detail: str) -> None:
    raise ContractError(f"{path}: {detail}")


def _same_json_scalar(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    return left == right


def _timestamp(value: str, path: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ContractError(f"{path}: is not a real UTC microsecond timestamp") from exc


def _matches_type(value: Any, expected: str) -> bool:
    return {
        "null": value is None,
        "boolean": type(value) is bool,
        "integer": type(value) is int,
        "number": type(value) in (int, float) and not isinstance(value, bool) and math.isfinite(value),
        "string": isinstance(value, str),
        "array": isinstance(value, list),
        "object": isinstance(value, dict),
    }.get(expected, False)


def _resolve_local_ref(root: Mapping[str, Any], ref: str, path: str) -> Mapping[str, Any]:
    if not ref.startswith("#/"):
        _fail(path, "only local JSON Schema references are allowed")
    node: Any = root
    for token in ref[2:].split("/"):
        key = token.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, Mapping) or key not in node:
            _fail(path, "schema reference does not resolve")
        node = node[key]
    if not isinstance(node, Mapping):
        _fail(path, "schema reference must resolve to an object")
    return node


def _check_schema_vocabulary(schema: Any, path: str = "schema") -> None:
    if isinstance(schema, Mapping):
        unknown = set(schema) - _ALLOWED_SCHEMA_KEYS
        if unknown:
            _fail(path, f"unsupported JSON Schema keyword(s): {', '.join(sorted(unknown))}")
        for key, value in schema.items():
            if key in {"properties", "$defs"}:
                if not isinstance(value, Mapping):
                    _fail(f"{path}.{key}", "must be an object")
                for child_name, child in value.items():
                    _check_schema_vocabulary(child, f"{path}.{key}.{child_name}")
            elif key == "items" and isinstance(value, Mapping):
                _check_schema_vocabulary(value, f"{path}.items")
    elif isinstance(schema, list):
        for index, item in enumerate(schema):
            _check_schema_vocabulary(item, f"{path}[{index}]")


def _validate_schema_value(value: Any, schema: Mapping[str, Any], root: Mapping[str, Any], path: str) -> None:
    if "$ref" in schema:
        _validate_schema_value(value, _resolve_local_ref(root, schema["$ref"], path), root, path)
        return

    expected_type = schema.get("type")
    if expected_type is not None:
        expected_types = [expected_type] if isinstance(expected_type, str) else expected_type
        if not isinstance(expected_types, list) or not all(isinstance(item, str) for item in expected_types):
            _fail(path, "schema type must be a string or string array")
        if not any(_matches_type(value, item) for item in expected_types):
            _fail(path, f"expected JSON type {'|'.join(expected_types)}")

    if "const" in schema and not _same_json_scalar(value, schema["const"]):
        _fail(path, "does not match the required literal")
    if "enum" in schema:
        enum = schema["enum"]
        if not isinstance(enum, list) or not any(_same_json_scalar(value, item) for item in enum):
            _fail(path, "is not an allowed value")

    if isinstance(value, dict):
        required = schema.get("required", [])
        if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
            _fail(path, "schema required must be a string array")
        missing = [key for key in required if key not in value]
        if missing:
            _fail(path, f"missing required field(s): {', '.join(missing)}")
        properties = schema.get("properties", {})
        if not isinstance(properties, Mapping):
            _fail(path, "schema properties must be an object")
        extras = set(value) - set(properties)
        if schema.get("additionalProperties") is False and extras:
            _fail(path, f"unexpected field(s): {', '.join(sorted(extras))}")
        for key, child in value.items():
            child_schema = properties.get(key)
            if isinstance(child_schema, Mapping):
                _validate_schema_value(child, child_schema, root, f"{path}.{key}")

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            _fail(path, "has fewer items than allowed")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            _fail(path, "has more items than allowed")
        if schema.get("uniqueItems") is True:
            canonical = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
            if len(canonical) != len(set(canonical)):
                _fail(path, "contains duplicate items")
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for index, item in enumerate(value):
                _validate_schema_value(item, item_schema, root, f"{path}[{index}]")

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            _fail(path, "is shorter than allowed")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            _fail(path, "is longer than allowed")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            _fail(path, "does not match the required pattern")
        if schema.get("format") == "uuid":
            try:
                parsed = uuid.UUID(value)
            except (ValueError, AttributeError) as exc:
                raise ContractError(f"{path}: is not a UUID") from exc
            if str(parsed) != value.lower():
                _fail(path, "UUID must use canonical spelling")
        elif schema.get("format") == "date-time":
            _timestamp(value, path)
        elif "format" in schema:
            _fail(path, "schema uses an unsupported string format")

    if type(value) in (int, float) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            _fail(path, "is below the allowed minimum")
        if "maximum" in schema and value > schema["maximum"]:
            _fail(path, "is above the allowed maximum")


def _ordered_ids(rows: Any, field: str, path: str) -> tuple[str, ...]:
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        _fail(path, "must be an array")
    values = tuple(row.get(field) for row in rows if isinstance(row, Mapping))
    if len(values) != len(rows) or not all(isinstance(item, str) for item in values):
        _fail(path, f"every row must contain string {field}")
    return values  # type: ignore[return-value]


def _reject_sensitive_keys(value: Any, path: str = "document") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                _fail(path, "all object keys must be strings")
            if key.lower() in _SENSITIVE_KEYS:
                _fail(f"{path}.{key}", "secret-bearing fields are forbidden")
            _reject_sensitive_keys(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_sensitive_keys(child, f"{path}[{index}]")


def manifest_blockers(document: Mapping[str, Any]) -> tuple[str, ...]:
    """Return deterministic unresolved gate IDs without inspecting evidence values."""

    blockers: list[str] = []
    study = document["study"]
    revisions = document["revision_set"]
    inventory = document["inventory"]
    profiles = document["profiles"]
    safety = document["localized_safety"]
    approvals = document["approvals"]

    if (
        any(
            revisions[field] is None
            for field in (
                "source_git_sha",
                "revision_bundle_sha256",
                "image_set_sha256",
                "config_revision",
                "firmware_revision",
                "registry_revision",
                "deployed_grid_revision",
                "sensor_calibration_revision",
            )
        )
        or study["experiment_id"] is None
        or study["study_id"] is None
    ):
        blockers.append("revision_bundle_pinned")
    if not all(row["running_device_verified"] and row["landing_status"] == "passed" for row in profiles):
        blockers.append("running_grid_profiles_land")
    routes = inventory["canonical_routes"]
    if routes["verified_count"] != routes["expected_count"]:
        blockers.append("setter_readback_routes_48_48")
    if routes["treatment_verified_count"] != routes["treatment_expected_count"]:
        blockers.append("treatment_clamps_11_11")
    orders = document["prefix_qualification"]
    if orders["activation_rollback_status"] != "passed":
        blockers.append("activation_rollback_prefix_replay")
    if orders["full48_recovery_status"] != "passed":
        blockers.append("full48_recovery_prefix_replay")
    if not inventory["inventory_complete"]:
        blockers.append("physical_inventory_provenance")
    if not document["calibration"]["all_current"]:
        blockers.append("calibrations_current")
    if document["hvac_and_controls"]["status"] != "passed":
        blockers.append("hvac_capacity_and_interlocks")
    water = document["water_and_fertigation"]
    if any(
        water[field] is None
        for field in (
            "fog_mister_branch_map_ref",
            "pressure_flow_evidence_ref",
            "leak_low_water_stop_ref",
            "daily_ceiling_ref",
            "fertigation_recipe_epoch",
            "feed_flush_window_ref",
            "pressure_contention_ref",
        )
    ):
        blockers.append("water_fertigation_commissioning")
    if not all(zone["status"] == "passed" for zone in safety["measured_wall_zones"]):
        blockers.append("localized_wall_zone_safety")
    if safety["center_screening_proxy"]["status"] != "approved":
        blockers.append("center_proxy_and_claim_limits")
    if (
        safety["air_dew_screen"]["status"] != "passed"
        or safety["reference_evidence"]["status"] != "passed"
        or safety["manual_crop_inspection"]["status"] != "passed"
    ):
        blockers.append("air_dew_reference_and_manual_inspection")
    if document["nine_stream_endpoint"]["field_evidence_status"] != "passed":
        blockers.append("nine_stream_counter_reconciliation")
    if document["interventions"]["status"] != "locked":
        blockers.append("intervention_calendar_and_care_override")
    if approvals["scoped_probe"]["status"] != "recorded" or approvals["probe_evidence"]["status"] != "passed":
        blockers.append("commissioning_probe_evidence")
    if document["facility_rescue"]["rehearsal_status"] != "passed":
        blockers.append("facility_rescue_rehearsal")
    if not all(
        row["evidence_status"] == "passed" and row["signoff_status"] == "signed" for row in document["personas"]
    ):
        blockers.append("multidisciplinary_record")
    return tuple(blockers)


def _validate_manifest(document: Mapping[str, Any], *, require_ready: bool) -> None:
    if _ordered_ids(document["profiles"], "profile_id", "document.profiles") != PROFILE_IDS:
        _fail("document.profiles", "must contain baseline, moderate, aggressive exactly once in that order")
    if _ordered_ids(document["personas"], "persona_id", "document.personas") != PERSONA_IDS:
        _fail("document.personas", "must contain the eight #641 personas exactly once in contract order")
    zones = document["localized_safety"]["measured_wall_zones"]
    if _ordered_ids(zones, "zone_id", "document.localized_safety.measured_wall_zones") != WALL_ZONE_IDS:
        _fail("document.localized_safety.measured_wall_zones", "must contain north/east/south/west exactly once")
    streams = document["nine_stream_endpoint"]["streams"]
    if _ordered_ids(streams, "equipment_id", "document.nine_stream_endpoint.streams") != EQUIPMENT_IDS:
        _fail("document.nine_stream_endpoint.streams", "must contain the exact nine protocol streams")
    checks = document["readiness"]["checks"]
    if _ordered_ids(checks, "check_id", "document.readiness.checks") != READINESS_CHECK_IDS:
        _fail("document.readiness.checks", "must cover every #641 readiness check exactly once")
    if any(row["field_count"] != 48 for row in document["profiles"]):
        _fail("document.profiles", "every profile must bind all 48 canonical fields")
    if document["runtime_rounding_allowed"] is not False:
        _fail("document.runtime_rounding_allowed", "runtime rounding is forbidden")
    if document["transport"]["generalized_vector_mode_required"] != "off":
        _fail("document.transport.generalized_vector_mode_required", "must remain exactly off")
    if document["document_grants_authority"] is not False:
        _fail("document.document_grants_authority", "field evidence never grants device authority")

    routes = document["inventory"]["canonical_routes"]
    if routes["verified_count"] == routes["expected_count"] and routes["evidence_ref"] is None:
        _fail("document.inventory.canonical_routes.evidence_ref", "complete running routes require direct evidence")
    if routes["treatment_verified_count"] == routes["treatment_expected_count"] and routes["evidence_ref"] is None:
        _fail("document.inventory.canonical_routes.evidence_ref", "complete treatment routes require direct evidence")
    if document["inventory"]["inventory_complete"]:
        inventory_rows = (
            document["inventory"]["sensors"] + document["inventory"]["actuators"] + document["inventory"]["meters"]
        )
        if any(row["status"] != "verified" or row["provenance_ref"] is None for row in inventory_rows):
            _fail("document.inventory", "complete inventory requires verified provenance for every item")
        calibrated_rows = document["inventory"]["sensors"] + document["inventory"]["meters"]
        if any(row["calibration_record_ref"] is None for row in calibrated_rows):
            _fail("document.inventory", "complete sensor/meter inventory requires calibration records")

    if document["calibration"]["all_current"]:
        records = document["calibration"]["records"]
        if not records or any(row["decision"] != "passed" for row in records):
            _fail("document.calibration", "current calibration requires at least one passing record")
    prefix = document["prefix_qualification"]
    if (prefix["activation_rollback_status"] == "passed" or prefix["full48_recovery_status"] == "passed") and prefix[
        "evidence_ref"
    ] is None:
        _fail("document.prefix_qualification.evidence_ref", "passing prefix qualification requires evidence")

    safety = document["localized_safety"]
    for index, zone in enumerate(safety["measured_wall_zones"]):
        if zone["status"] == "passed" and any(
            zone[field] is None
            for field in ("absolute_limits_ref", "freshness_seconds_max", "maximum_breach_seconds", "evidence_ref")
        ):
            _fail(
                f"document.localized_safety.measured_wall_zones[{index}]",
                "passing zone evidence requires limits, freshness, duration, and evidence",
            )
    center = safety["center_screening_proxy"]
    if center["status"] == "approved" and center["evidence_ref"] is None:
        _fail("document.localized_safety.center_screening_proxy", "approved proxy requires evidence")
    dew = safety["air_dew_screen"]
    if dew["status"] == "passed" and any(
        dew[field] is None for field in ("minimum_margin_c", "maximum_breach_seconds", "evidence_ref")
    ):
        _fail(
            "document.localized_safety.air_dew_screen",
            "passing air-dew screen requires threshold, duration, and evidence",
        )
    reference = safety["reference_evidence"]
    if reference["status"] == "passed" and (reference["mode"] == "unresolved" or reference["evidence_ref"] is None):
        _fail(
            "document.localized_safety.reference_evidence",
            "passing reference evidence requires a locked mode and evidence",
        )
    if reference["mode"] == "commissioned_pair" and not reference["continuous_redundancy_claimed"]:
        _fail(
            "document.localized_safety.reference_evidence",
            "commissioned_pair must truthfully identify continuous redundancy",
        )
    if reference["mode"] == "no_continuous_pair" and reference["continuous_redundancy_claimed"]:
        _fail("document.localized_safety.reference_evidence", "no_continuous_pair cannot claim continuous redundancy")
    manual = safety["manual_crop_inspection"]
    if manual["status"] == "passed" and any(
        manual[field] is None
        for field in ("rubric_ref", "timing_ref", "stop_rule_ref", "record_schema_ref", "evidence_ref")
    ):
        _fail(
            "document.localized_safety.manual_crop_inspection",
            "passing inspection requires the complete frozen rubric and evidence",
        )

    if document["hvac_and_controls"]["status"] == "passed" and any(
        document["hvac_and_controls"][field] is None
        for field in ("capacity_envelope_ref", "interlocks_ref", "dwell_hysteresis_ref", "evidence_ref")
    ):
        _fail(
            "document.hvac_and_controls",
            "passing HVAC/control evidence requires capacity, interlocks, dwell, and evidence",
        )
    if (
        document["nine_stream_endpoint"]["field_evidence_status"] == "passed"
        and document["nine_stream_endpoint"]["evidence_ref"] is None
    ):
        _fail("document.nine_stream_endpoint.evidence_ref", "passing counter reconciliation requires evidence")
    if document["interventions"]["status"] == "locked" and document["interventions"]["calendar_evidence_ref"] is None:
        _fail("document.interventions.calendar_evidence_ref", "locked intervention calendar requires evidence")
    rescue = document["facility_rescue"]
    if rescue["rehearsal_status"] == "passed" and (
        rescue["operator_roster_ref"] is None or rescue["evidence_ref"] is None
    ):
        _fail("document.facility_rescue", "passing rescue rehearsal requires roster and evidence")

    approvals = document["approvals"]
    scoped = approvals["scoped_probe"]
    combined = approvals["combined_physical"]
    if combined["status"] == "recorded" and (
        scoped["status"] != "recorded" or approvals["probe_evidence"]["status"] != "passed"
    ):
        _fail("document.approvals.combined_physical", "combined signoff must follow the scoped probe and its evidence")
    if scoped["status"] == "recorded" and any(
        scoped[field] is None
        for field in (
            "approval_ref",
            "artifact_sha256",
            "valid_from",
            "valid_until",
            "expires_at",
            "supervisor_role",
            "rescue_owner_role",
        )
    ):
        _fail(
            "document.approvals.scoped_probe",
            "recorded approval requires exact window, expiry, supervisor, rescue owner, and artifact",
        )
    if scoped["status"] == "recorded":
        valid_from = _timestamp(scoped["valid_from"], "document.approvals.scoped_probe.valid_from")
        valid_until = _timestamp(scoped["valid_until"], "document.approvals.scoped_probe.valid_until")
        expires_at = _timestamp(scoped["expires_at"], "document.approvals.scoped_probe.expires_at")
        if not valid_from < expires_at <= valid_until:
            _fail("document.approvals.scoped_probe", "must satisfy valid_from < expires_at <= valid_until")
    if combined["status"] == "recorded" and any(
        combined[field] is None for field in ("approval_ref", "artifact_sha256", "approved_at")
    ):
        _fail(
            "document.approvals.combined_physical",
            "recorded combined signoff requires its immutable reference and artifact",
        )

    blockers = manifest_blockers(document)
    declared = tuple(document["readiness"]["blockers"])
    if declared != blockers:
        _fail("document.readiness.blockers", "must exactly equal the deterministic unresolved gate list")
    decision = document["readiness"]["decision"]
    if blockers and decision != "blocked":
        _fail("document.readiness.decision", "must be blocked while any gate is unresolved")
    if not blockers and decision != "ready_for_combined_signoff":
        _fail("document.readiness.decision", "must be ready_for_combined_signoff when every evidence gate passes")
    check_statuses = {row["check_id"]: row["status"] for row in checks}
    if any(check_statuses[blocker] == "passed" for blocker in blockers):
        _fail("document.readiness.checks", "a deterministic blocker cannot be marked passed")
    if any(check_statuses[check] != "passed" for check in READINESS_CHECK_IDS if check not in blockers):
        _fail("document.readiness.checks", "a cleared deterministic gate must be marked passed")
    status = document["status"]
    if blockers and status not in {"template", "blocked"}:
        _fail("document.status", "cannot claim readiness while deterministic blockers remain")
    if not blockers and combined["status"] == "recorded" and status != "combined_signed":
        _fail("document.status", "recorded combined signoff requires combined_signed status")
    if not blockers and combined["status"] == "pending" and status != "ready_for_combined_signoff":
        _fail("document.status", "complete evidence awaiting #641 must be ready_for_combined_signoff")
    if not blockers and combined["status"] == "rejected" and status != "blocked":
        _fail("document.status", "rejected combined signoff requires blocked status")
    if combined["status"] == "recorded" and blockers:
        _fail("document.approvals.combined_physical", "combined signoff cannot bind an incomplete readiness record")
    if require_ready and blockers:
        _fail("document.readiness", "physical readiness is incomplete")
    if require_ready and combined["status"] == "rejected":
        _fail("document.approvals.combined_physical", "combined physical signoff was rejected")


def _validate_calibration(document: Mapping[str, Any]) -> None:
    if document["document_grants_authority"] is not False or document["device_write_allowed"] is not False:
        _fail("document", "calibration evidence work orders are non-operative and cannot authorize a device write")
    if (
        _ordered_ids(document["preconditions"], "precondition_id", "document.preconditions")
        != CALIBRATION_PRECONDITION_IDS
    ):
        _fail("document.preconditions", "must contain the complete calibration preflight")
    if _ordered_ids(document["steps"], "step_id", "document.steps") != CALIBRATION_STEP_IDS:
        _fail("document.steps", "must contain the fixed read-only calibration sequence")
    if any(step["device_write"] for step in document["steps"]):
        _fail("document.steps", "calibration template cannot write the greenhouse controller")
    result = document["result"]
    if document["status"] in {"scheduled", "completed"}:
        required = (
            document["work_order_id"],
            document["study"]["experiment_id"],
            document["study"]["study_id"],
            document["study"]["revision_bundle_sha256"],
            document["roles"]["executor_role"],
            document["roles"]["independent_verifier_role"],
            document["window"]["starts_at"],
            document["window"]["ends_at"],
        )
        if any(item is None for item in required):
            _fail("document", "scheduled calibration requires exact study, roles, and time window")
        if _timestamp(document["window"]["starts_at"], "document.window.starts_at") >= _timestamp(
            document["window"]["ends_at"], "document.window.ends_at"
        ):
            _fail("document.window", "starts_at must precede ends_at")
    if document["status"] == "completed" and (
        result["decision"] not in {"passed", "failed"}
        or result["calibrated_at"] is None
        or result["calibration_revision"] is None
        or not result["evidence_refs"]
    ):
        _fail("document.result", "completed calibration requires decision, timestamp, revision, and evidence")
    if document["status"] == "completed" and any(row["status"] != "passed" for row in document["preconditions"]):
        _fail("document.preconditions", "completed calibration requires every precondition to have passed")


def _validate_probe(document: Mapping[str, Any]) -> None:
    binding = document["binding"]
    if document["document_grants_authority"] is not False or document["executable"] is not False:
        _fail("document", "a work-order artifact never grants or executes probe authority")
    if (
        binding["issue_number"] != 641
        or binding["approval_kind"] != "scoped_probe"
        or binding["scope_name"] != "commissioning_probe"
        or binding["execution_phase"] != "commissioning"
        or binding["operation_kind"] != "commissioning_probe"
        or binding["lifecycle_status"] != "draft"
    ):
        _fail("document.binding", "must bind the exact #641 scoped commissioning_probe contract")
    if document["target_profile"] != "commissioning_probe":
        _fail("document.target_profile", "probe work cannot target moderate or aggressive")
    if _ordered_ids(document["steps"], "step_id", "document.steps") != PROBE_STEP_IDS:
        _fail("document.steps", "must contain the fixed commissioning probe sequence")
    for index, step in enumerate(document["steps"]):
        if step["experiment_write_possible"] and not step["requires_runtime_authorization"]:
            _fail(f"document.steps[{index}]", "every possible write requires live runtime authorization")
    if document["status"] == "template" and any(
        binding[field] is not None
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
        )
    ):
        _fail("document.binding", "checked-in template cannot carry live authority or an approval window")
    if document["status"] in {"planned", "evidence_complete"}:
        required_fields = (
            "experiment_id",
            "work_id",
            "revision_bundle_sha256",
            "target_state_content_sha256",
            "approval_ref",
            "artifact_sha256",
            "valid_from",
            "valid_until",
            "expires_at",
            "supervisor_role",
            "rescue_owner_role",
        )
        if any(binding[field] is None for field in required_fields):
            _fail("document.binding", "planned probe requires exact immutable authority bindings")
        valid_from = _timestamp(binding["valid_from"], "document.binding.valid_from")
        valid_until = _timestamp(binding["valid_until"], "document.binding.valid_until")
        expires_at = _timestamp(binding["expires_at"], "document.binding.expires_at")
        if not valid_from < expires_at <= valid_until:
            _fail("document.binding", "must satisfy valid_from < expires_at <= valid_until")
    evidence = document["evidence_result"]
    if document["status"] == "evidence_complete" and (
        evidence["decision"] not in {"passed", "failed"}
        or evidence["completed_at"] is None
        or not evidence["evidence_refs"]
        or evidence["baseline_confirmation_epoch_count"] < 2
    ):
        _fail(
            "document.evidence_result", "complete probe evidence requires a decision, evidence, and two baseline epochs"
        )


def validate_document(
    document: Any,
    schema: Mapping[str, Any],
    *,
    require_ready: bool = False,
) -> None:
    """Validate one parsed artifact against its schema and semantic contract."""

    if not isinstance(document, Mapping):
        _fail("document", "must be an object")
    if schema.get("$schema") != SCHEMA_URI:
        _fail("schema.$schema", "must declare JSON Schema 2020-12")
    _check_schema_vocabulary(schema)
    _validate_schema_value(document, schema, schema, "document")
    _reject_sensitive_keys(document)
    kind = document.get("schema")
    if kind == "verdify-experiment-v2-physical-readiness":
        _validate_manifest(document, require_ready=require_ready)
    elif kind == "verdify-experiment-v2-calibration-work-order":
        if require_ready:
            _fail("document", "require_ready applies only to the physical-readiness manifest")
        _validate_calibration(document)
    elif kind == "verdify-experiment-v2-commissioning-probe-work-order":
        if require_ready:
            _fail("document", "require_ready applies only to the physical-readiness manifest")
        _validate_probe(document)
    else:
        _fail("document.schema", "unknown field-readiness artifact")


def load_and_validate(path: Path, *, require_ready: bool = False) -> Mapping[str, Any]:
    """Load YAML/JSON, select the local schema by literal, and validate it."""

    try:
        loader = _UniqueKeyLoader(path.read_text(encoding="utf-8"))
        try:
            document = loader.get_single_data()
        finally:
            loader.dispose()
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ContractError(f"{path}: cannot read a YAML/JSON document") from exc
    if not isinstance(document, Mapping) or not isinstance(document.get("schema"), str):
        _fail(str(path), "document must contain a schema literal")
    schema_name = _SCHEMA_FILES.get(document["schema"])
    if schema_name is None:
        _fail(str(path), "unknown field-readiness schema literal")
    schema_path = Path(__file__).resolve().parent / schema_name
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"{schema_path}: cannot read schema") from exc
    validate_document(document, schema, require_ready=require_ready)
    return document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate non-operative experiment-v2 field artifacts")
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--require-ready", action="store_true", help="require a complete physical-readiness manifest")
    args = parser.parse_args(argv)
    try:
        for path in args.paths:
            load_and_validate(path, require_ready=args.require_ready)
    except ContractError as exc:
        parser.exit(1, f"{exc}\n")
    for path in args.paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
