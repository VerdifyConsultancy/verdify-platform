#!/usr/bin/env python3
"""Fail-closed Gate R / Gate P readiness packet verifier (#749).

The verifier is deliberately non-actuating.  It consumes a metadata-only JSON
packet assembled from the existing read-only HA/DB, component-grid, writer,
authentication, provider, Argo and backup preflights.  It never reads a
credential, talks to a device, or changes database/Kubernetes state.

Proof packets form a one-use chain: gate-p -> baseline-before -> aggressive ->
baseline-after.  The optional state file records only the previous receipt hash
and rejects replay/cached packets.  Recovery has its own one-item gate-r chain
and can authorize Gate R only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

INPUT_SCHEMA = "verdify-experiment-v2-readiness-input-v1"
RESULT_SCHEMA = "verdify-experiment-v2-readiness-result-v1"
STATE_SCHEMA = "verdify-experiment-v2-readiness-chain-v1"
CORRECTED_ONE_OFF_SOURCE_PIN = "6b48dba7217438f5fdd7fb14fc8e067975cf1c35"

Mode = Literal["recovery", "proof"]
Boundary = Literal["gate-r", "gate-p", "baseline-before", "aggressive", "baseline-after"]

BOUNDARY_SEQUENCE: dict[Mode, tuple[Boundary, ...]] = {
    "recovery": ("gate-r",),
    "proof": ("gate-p", "baseline-before", "aggressive", "baseline-after"),
}
ZONES = ("north", "south", "east", "west")
METRICS = ("temp_f", "rh_pct", "vpd_kpa")
SURFACES = {
    "component_proof": "scripts/verify_component_proof_packet.py",
    "selector": "research/planner-efficacy/switchback/v2_selector.py",
    "executor_control": "ingestor/tasks/component_experiment.py",
    "locked_outcome": "research/planner-efficacy/switchback/v2_outcomes.py",
}
KNOWN_DEPENDENCIES = {
    "aggregate_rh",
    "aggregate_temperature",
    "aggregate_vpd",
    "component_grid",
    "device_cfg_readback",
    "equipment_counter",
    "equipment_state",
    "experiment_authority",
    "forecast",
    "registry_revision",
    "writer_connection_generation",
    "writer_lease",
}
GATE_P_PREREQUISITES = {
    "boundary_observation_contract",
    "climate_quorum",
    "component_grid_48_of_48",
    "controller_backup_current",
    "degradation_classified",
    "exact_source_and_images_pinned",
    "fresh_gate_p_authorization",
    "recovery_path_ready",
    "stable_writer_lease_generation",
    "zero_exposure",
}
CORE_WORKLOADS = {
    "verdify-api",
    "verdify-db",
    "verdify-hermes-iris",
    "verdify-ingestor",
    "verdify-mcp",
}
APPLICATION_IMAGES = {"verdify-api", "verdify-ingestor", "verdify-mcp"}
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA64 = re.compile(r"^[0-9a-f]{64}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
SAFE_REVISION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}$")
MAX_PACKET_AGE = timedelta(seconds=120)
MAX_SOURCE_FUTURE_SKEW = timedelta(seconds=5)
MIN_WRITER_STABILITY = timedelta(minutes=30)
MAX_COMPONENT_AGE = timedelta(seconds=120)
MAX_CURRENT_EVIDENCE_AGE = timedelta(minutes=5)
MAX_AUTH_AGE = timedelta(minutes=30)
MAX_BACKUP_AGE = timedelta(hours=26)
MEAN_TOLERANCE = {
    "temp_f": Decimal("0.10"),
    "rh_pct": Decimal("0.10"),
    "vpd_kpa": Decimal("0.010"),
}
MAX_SPREAD = {
    # Existing causal limits used by gather-plan-context.sh / Iris. RH has no
    # independent source limit; temperature/VPD already cover its physical
    # consequence, so do not invent a looser RH substitute here.
    "temp_f": Decimal("4.0"),
    "vpd_kpa": Decimal("0.50"),
}


class PacketError(ValueError):
    """The packet is malformed rather than merely not ready."""


@dataclass(frozen=True)
class ExpectedPins:
    git_pin: str
    application_source: str
    experiment_id: str


@dataclass(frozen=True)
class ChainState:
    mode: Mode
    attempt_id: str
    next_sequence: int
    last_receipt_sha256: str | None


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _exact_keys(value: object, expected: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PacketError(f"{label} must be an object")
    actual = set(value)
    if actual != expected:
        raise PacketError(
            f"{label} keys mismatch: missing={sorted(expected - actual)} extra={sorted(actual - expected)}"
        )
    return value


def _array(value: object, label: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise PacketError(f"{label} must be an array")
    return value


def _text(value: object, label: str, *, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value):
        raise PacketError(f"{label} must be a{' possibly empty' if empty else ' non-empty'} string")
    return value


def _boolean(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise PacketError(f"{label} must be a boolean")
    return value


def _integer(value: object, label: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise PacketError(f"{label} must be an integer >= {minimum}")
    return value


def _timestamp(value: object, label: str) -> datetime:
    text = _text(value, label)
    if not text.endswith("Z"):
        raise PacketError(f"{label} must be canonical UTC with Z")
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as exc:
        raise PacketError(f"{label} is not RFC-3339") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise PacketError(f"{label} must be UTC")
    return parsed.astimezone(UTC)


def _uuid(value: object, label: str) -> str:
    text = _text(value, label)
    try:
        parsed = UUID(text)
    except ValueError as exc:
        raise PacketError(f"{label} must be a UUID") from exc
    if str(parsed) != text:
        raise PacketError(f"{label} must be a canonical lowercase UUID")
    return text


def _decimal(value: object) -> Decimal | None:
    if type(value) not in (int, float, str) or type(value) is bool:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _fresh(
    observed_at: datetime,
    *,
    now: datetime,
    max_age: timedelta,
    label: str,
    blockers: list[str],
) -> None:
    if observed_at > now + MAX_SOURCE_FUTURE_SKEW:
        blockers.append(f"{label}_from_future")
    elif now - observed_at > max_age:
        blockers.append(f"{label}_cached")


def _safe_revision(value: object, label: str) -> str:
    text = _text(value, label)
    if not SAFE_REVISION.fullmatch(text):
        raise PacketError(f"{label} is not a safe revision token")
    return text


def _source_match(actual: object, expected: str, label: str, blockers: list[str]) -> None:
    if _text(actual, label) != expected:
        blockers.append(f"{label}_mismatch")


def _generation_match(actual: object, expected: int, label: str, blockers: list[str]) -> None:
    if _integer(actual, label) != expected:
        blockers.append(f"{label}_mismatch")


def _validate_provenance(
    raw: object, expected: ExpectedPins, *, now: datetime, blockers: list[str]
) -> tuple[int, int, int, str]:
    value = _exact_keys(
        raw,
        {
            "git_pin",
            "application_source_revision",
            "rendered_git_pin",
            "running_git_pin",
            "experiment_id",
            "registry_revision",
            "images",
            "writer",
        },
        "provenance",
    )
    for field in ("git_pin", "rendered_git_pin", "running_git_pin"):
        pin = _text(value[field], f"provenance.{field}")
        if not SHA40.fullmatch(pin):
            raise PacketError(f"provenance.{field} must be a 40-character Git SHA")
        if pin != expected.git_pin:
            blockers.append(f"provenance_{field}_mismatch")
    app_source = _text(value["application_source_revision"], "provenance.application_source_revision")
    if not SHA40.fullmatch(app_source):
        raise PacketError("provenance.application_source_revision must be a 40-character Git SHA")
    if app_source != expected.application_source:
        blockers.append("provenance_application_source_revision_mismatch")
    if _uuid(value["experiment_id"], "provenance.experiment_id") != expected.experiment_id:
        blockers.append("provenance_experiment_id_mismatch")
    registry = _safe_revision(value["registry_revision"], "provenance.registry_revision")

    images = _array(value["images"], "provenance.images")
    if not images:
        blockers.append("provenance_images_absent")
    names: set[str] = set()
    for index, raw_image in enumerate(images):
        image = _exact_keys(
            raw_image,
            {"workload", "rendered_digest", "running_digest", "application_source_revision"},
            f"provenance.images[{index}]",
        )
        name = _text(image["workload"], f"provenance.images[{index}].workload")
        if name in names:
            blockers.append(f"duplicate_image_workload:{name}")
        names.add(name)
        rendered = _text(image["rendered_digest"], f"provenance.images[{index}].rendered_digest")
        running = _text(image["running_digest"], f"provenance.images[{index}].running_digest")
        if not DIGEST.fullmatch(rendered) or not DIGEST.fullmatch(running):
            raise PacketError(f"provenance.images[{index}] digests must be sha256 digests")
        if rendered != running:
            blockers.append(f"image_digest_mismatch:{name}")
        _source_match(
            image["application_source_revision"],
            expected.application_source,
            f"image_source:{name}",
            blockers,
        )
    if names != APPLICATION_IMAGES:
        blockers.append(
            f"application_image_set_mismatch:missing={sorted(APPLICATION_IMAGES - names)}:extra={sorted(names - APPLICATION_IMAGES)}"
        )

    writer = _exact_keys(
        value["writer"],
        {
            "lease_holder",
            "current_writer_count",
            "lease_generation",
            "writer_generation",
            "connection_generation",
            "stable_since",
            "observed_at",
            "application_source_revision",
            "running_digest",
            "recurring_error_count",
        },
        "provenance.writer",
    )
    _text(writer["lease_holder"], "provenance.writer.lease_holder")
    if _integer(writer["current_writer_count"], "provenance.writer.current_writer_count") != 1:
        blockers.append("writer_count_not_one")
    lease = _integer(writer["lease_generation"], "provenance.writer.lease_generation", minimum=1)
    writer_generation = _integer(writer["writer_generation"], "provenance.writer.writer_generation")
    connection_generation = _integer(
        writer["connection_generation"], "provenance.writer.connection_generation", minimum=1
    )
    observed = _timestamp(writer["observed_at"], "provenance.writer.observed_at")
    stable_since = _timestamp(writer["stable_since"], "provenance.writer.stable_since")
    _fresh(observed, now=now, max_age=MAX_PACKET_AGE, label="writer_evidence", blockers=blockers)
    if observed - stable_since < MIN_WRITER_STABILITY:
        blockers.append("writer_generation_not_stable")
    _source_match(
        writer["application_source_revision"],
        expected.application_source,
        "writer_source_revision",
        blockers,
    )
    digest = _text(writer["running_digest"], "provenance.writer.running_digest")
    if not DIGEST.fullmatch(digest):
        raise PacketError("provenance.writer.running_digest must be a sha256 digest")
    if digest not in {
        _text(row["running_digest"], "image.running_digest") for row in images if isinstance(row, Mapping)
    }:
        blockers.append("writer_running_digest_not_rendered")
    if _integer(writer["recurring_error_count"], "provenance.writer.recurring_error_count") != 0:
        blockers.append("writer_recurring_errors")
    return lease, writer_generation, connection_generation, registry


def _validate_workloads(raw: object, *, now: datetime, blockers: list[str]) -> None:
    rows = _array(raw, "workloads")
    observed_names: set[str] = set()
    for index, raw_row in enumerate(rows):
        row = _exact_keys(
            raw_row,
            {"name", "kind", "ready", "desired", "healthy", "observed_at"},
            f"workloads[{index}]",
        )
        name = _text(row["name"], f"workloads[{index}].name")
        if name in observed_names:
            blockers.append(f"duplicate_core_workload:{name}")
        observed_names.add(name)
        _text(row["kind"], f"workloads[{index}].kind")
        ready = _integer(row["ready"], f"workloads[{index}].ready")
        desired = _integer(row["desired"], f"workloads[{index}].desired", minimum=1)
        if ready != desired or not _boolean(row["healthy"], f"workloads[{index}].healthy"):
            blockers.append(f"core_workload_unhealthy:{name}")
        observed = _timestamp(row["observed_at"], f"workloads[{index}].observed_at")
        _fresh(observed, now=now, max_age=MAX_PACKET_AGE, label=f"workload:{name}", blockers=blockers)
    missing = CORE_WORKLOADS - observed_names
    extra = observed_names - CORE_WORKLOADS
    if missing or extra:
        blockers.append(f"core_workload_set_mismatch:missing={sorted(missing)}:extra={sorted(extra)}")


def _validate_runtime(
    raw: object, *, mode: Mode, boundary: Boundary, expected: ExpectedPins, blockers: list[str]
) -> int:
    value = _exact_keys(
        raw,
        {
            "experiment_feature_mode",
            "active_experiment_id",
            "policy_vector_mode",
            "component_enabled",
            "admission_state",
            "open_exposure_count",
            "experiment_id",
            "lease_generation",
            "writer_generation",
            "connection_generation",
            "registry_revision",
        },
        "runtime",
    )
    if value["experiment_feature_mode"] != "off":
        blockers.append("experiment_feature_not_off")
    if value["active_experiment_id"] != "":
        blockers.append("active_experiment_id_not_empty")
    if value["policy_vector_mode"] != "off":
        blockers.append("policy_vector_mode_not_off")
    if _uuid(value["experiment_id"], "runtime.experiment_id") != expected.experiment_id:
        blockers.append("runtime_experiment_id_mismatch")
    component_enabled = _boolean(value["component_enabled"], "runtime.component_enabled")
    admission = _text(value["admission_state"], "runtime.admission_state")
    exposures = _integer(value["open_exposure_count"], "runtime.open_exposure_count")
    if boundary in ("gate-p", "baseline-before") and (component_enabled or admission != "closed"):
        blockers.append("proof_start_authority_not_closed")
    if boundary == "aggressive" and (not component_enabled or admission != "baseline_recovery"):
        blockers.append("aggressive_boundary_authority_mismatch")
    if boundary == "baseline-after" and (not component_enabled or admission != "open"):
        blockers.append("baseline_after_boundary_authority_mismatch")
    if boundary == "gate-r" and (admission not in ("emergency_hold", "baseline_recovery")):
        blockers.append("recovery_boundary_authority_mismatch")
    if boundary != "baseline-after" and exposures != 0:
        blockers.append("open_exposure_before_actuation")
    if boundary == "baseline-after" and exposures not in (0, 1):
        blockers.append("ambiguous_open_exposure_count")
    if mode == "recovery" and boundary != "gate-r":
        blockers.append("recovery_mode_cannot_authorize_proof")
    return exposures


def _validate_backup(raw: object, *, mode: Mode, expected: ExpectedPins, now: datetime, blockers: list[str]) -> None:
    value = _exact_keys(raw, {"corrected_one_off", "controller_owned", "policy_max_age_seconds"}, "backup")
    one_off = _exact_keys(
        value["corrected_one_off"],
        {
            "status",
            "completed_at",
            "artifact_bytes",
            "restorable",
            "partial_artifact",
            "source_git_pin",
            "receipt_sha256",
        },
        "backup.corrected_one_off",
    )
    one_off_status = _text(one_off["status"], "backup.corrected_one_off.status")
    if one_off_status != "succeeded":
        blockers.append("corrected_one_off_backup_failed")
    if _integer(one_off["artifact_bytes"], "backup.corrected_one_off.artifact_bytes") < 1:
        blockers.append("corrected_one_off_backup_empty")
    if not _boolean(one_off["restorable"], "backup.corrected_one_off.restorable"):
        blockers.append("corrected_one_off_backup_not_restorable")
    if _boolean(one_off["partial_artifact"], "backup.corrected_one_off.partial_artifact"):
        blockers.append("corrected_one_off_backup_partial")
    one_off_at = _timestamp(one_off["completed_at"], "backup.corrected_one_off.completed_at")
    if one_off_at > now + MAX_SOURCE_FUTURE_SKEW:
        blockers.append("corrected_one_off_backup_from_future")
    one_off_receipt = _text(one_off["receipt_sha256"], "backup.corrected_one_off.receipt_sha256")
    if not SHA64.fullmatch(one_off_receipt):
        raise PacketError("backup.corrected_one_off.receipt_sha256 must be a SHA-256")
    one_off_source = _text(one_off["source_git_pin"], "backup.corrected_one_off.source_git_pin")
    if not SHA40.fullmatch(one_off_source):
        raise PacketError("backup.corrected_one_off.source_git_pin must be a lowercase Git SHA")
    if one_off_source != CORRECTED_ONE_OFF_SOURCE_PIN:
        blockers.append("corrected_one_off_backup_source_pin_mismatch")

    controller = _exact_keys(
        value["controller_owned"],
        {
            "status",
            "completed_at",
            "artifact_bytes",
            "restorable",
            "partial_artifact",
            "source_git_pin",
            "receipt_sha256",
        },
        "backup.controller_owned",
    )
    controller_status = _text(controller["status"], "backup.controller_owned.status")
    controller_at = _timestamp(controller["completed_at"], "backup.controller_owned.completed_at")
    controller_bytes = _integer(controller["artifact_bytes"], "backup.controller_owned.artifact_bytes")
    controller_restorable = _boolean(controller["restorable"], "backup.controller_owned.restorable")
    controller_partial = _boolean(controller["partial_artifact"], "backup.controller_owned.partial_artifact")
    controller_receipt = _text(controller["receipt_sha256"], "backup.controller_owned.receipt_sha256")
    if not SHA64.fullmatch(controller_receipt):
        raise PacketError("backup.controller_owned.receipt_sha256 must be a SHA-256")
    controller_source = _text(controller["source_git_pin"], "backup.controller_owned.source_git_pin")
    if not SHA40.fullmatch(controller_source):
        raise PacketError("backup.controller_owned.source_git_pin must be a lowercase Git SHA")
    policy_age = _integer(value["policy_max_age_seconds"], "backup.policy_max_age_seconds", minimum=1)
    if mode == "proof":
        if controller_source != expected.git_pin:
            blockers.append("controller_owned_backup_source_pin_mismatch")
        if controller_status != "succeeded":
            blockers.append("controller_owned_backup_failed")
        if controller_bytes < 1:
            blockers.append("controller_owned_backup_empty")
        if not controller_restorable:
            blockers.append("controller_owned_backup_not_restorable")
        if controller_partial:
            blockers.append("controller_owned_backup_partial")
        _fresh(
            controller_at,
            now=now,
            max_age=min(MAX_BACKUP_AGE, timedelta(seconds=policy_age)),
            label="controller_owned_backup",
            blockers=blockers,
        )


def _validate_argo(raw: object, *, mode: Mode, expected: ExpectedPins, now: datetime, blockers: list[str]) -> None:
    value = _exact_keys(
        raw,
        {"revision", "sync_status", "health_status", "operation_phase", "prune", "resource_selector", "observed_at"},
        "argo",
    )
    revision = _text(value["revision"], "argo.revision")
    sync_status = _text(value["sync_status"], "argo.sync_status")
    health_status = _text(value["health_status"], "argo.health_status")
    operation_phase = _text(value["operation_phase"], "argo.operation_phase")
    if revision != expected.git_pin:
        blockers.append("argo_revision_mismatch")
    if mode == "proof" and (sync_status != "Synced" or health_status != "Healthy" or operation_phase != "Succeeded"):
        blockers.append("proof_argo_not_exact_synced_healthy")
    if _boolean(value["prune"], "argo.prune"):
        blockers.append("argo_prune_enabled")
    if value["resource_selector"] is not None:
        blockers.append("argo_resource_selector_present")
    observed = _timestamp(value["observed_at"], "argo.observed_at")
    _fresh(observed, now=now, max_age=MAX_PACKET_AGE, label="argo_evidence", blockers=blockers)


def _metric_cell(raw: object, label: str) -> tuple[Decimal | None, datetime, str, str]:
    cell = _exact_keys(raw, {"value", "observed_at", "source_event_id", "source_cycle_id"}, label)
    return (
        _decimal(cell["value"]),
        _timestamp(cell["observed_at"], f"{label}.observed_at"),
        _text(cell["source_event_id"], f"{label}.source_event_id"),
        _text(cell["source_cycle_id"], f"{label}.source_cycle_id"),
    )


def _validate_climate(
    raw: object, *, captured_at: datetime, blockers: list[str], warnings: list[str]
) -> tuple[int, tuple[str, ...], tuple[str, ...], bool]:
    value = _exact_keys(
        raw,
        {"max_source_age_seconds", "samples", "qualification_capture"},
        "climate",
    )
    max_age = timedelta(seconds=_integer(value["max_source_age_seconds"], "climate.max_source_age_seconds", minimum=1))
    qualification = _exact_keys(
        value["qualification_capture"],
        {
            "status",
            "source_kind",
            "evidence_class",
            "window_started_at",
            "window_ended_at",
            "sample_count",
            "minimum_contributors",
            "application_source_revision",
            "receipt_sha256",
        },
        "climate.qualification_capture",
    )
    if qualification["status"] not in ("pass", "degraded-pass"):
        blockers.append("climate_qualification_capture_failed")
    if qualification["source_kind"] != "ha_cycle_aligned_events":
        blockers.append("climate_qualification_not_cycle_aligned_ha")
    evidence_class = qualification["evidence_class"]
    if evidence_class not in ("source_qualification", "current_gate_capture"):
        blockers.append("climate_qualification_evidence_class_invalid")
    window_start = _timestamp(qualification["window_started_at"], "climate.qualification_capture.window_started_at")
    window_end = _timestamp(qualification["window_ended_at"], "climate.qualification_capture.window_ended_at")
    if window_end - window_start < timedelta(minutes=30):
        blockers.append("climate_qualification_window_short")
    if _integer(qualification["sample_count"], "climate.qualification_capture.sample_count", minimum=2) < 2:
        blockers.append("climate_qualification_samples_insufficient")
    if _integer(qualification["minimum_contributors"], "climate.qualification_capture.minimum_contributors") < 3:
        blockers.append("climate_qualification_below_quorum")
    if window_end > captured_at + MAX_SOURCE_FUTURE_SKEW:
        blockers.append("climate_qualification_capture_from_future")
    elif evidence_class == "current_gate_capture":
        _fresh(
            window_end,
            now=captured_at,
            max_age=MAX_CURRENT_EVIDENCE_AGE,
            label="climate_qualification_capture",
            blockers=blockers,
        )
    receipt = _text(qualification["receipt_sha256"], "climate.qualification_capture.receipt_sha256")
    if not SHA64.fullmatch(receipt):
        raise PacketError("climate.qualification_capture.receipt_sha256 must be a SHA-256")

    samples = _array(value["samples"], "climate.samples")
    if len(samples) < 2:
        blockers.append("climate_samples_insufficient_for_advancement")
        return 0, (), ZONES, False
    previous_sample_at: datetime | None = None
    previous_aggregate_times: dict[str, datetime] = {}
    contributor_sets: list[tuple[str, ...]] = []
    diagnostic_contradiction = False
    for index, raw_sample in enumerate(samples):
        sample = _exact_keys(
            raw_sample,
            {"cycle_id", "sample_at", "declared_contributors", "zones", "aggregates", "diagnostics"},
            f"climate.samples[{index}]",
        )
        cycle_id = _text(sample["cycle_id"], f"climate.samples[{index}].cycle_id")
        sample_at = _timestamp(sample["sample_at"], f"climate.samples[{index}].sample_at")
        if previous_sample_at is not None and sample_at <= previous_sample_at:
            blockers.append("climate_sample_timestamps_not_advancing")
        previous_sample_at = sample_at
        _fresh(
            sample_at,
            now=captured_at,
            max_age=max_age,
            label=f"climate_sample:{index}",
            blockers=blockers,
        )

        zones = _exact_keys(sample["zones"], set(ZONES), f"climate.samples[{index}].zones")
        contributors: list[str] = []
        metric_values: dict[str, list[Decimal]] = {metric: [] for metric in METRICS}
        source_events: set[str] = set()
        for zone in ZONES:
            triplet = _exact_keys(zones[zone], set(METRICS), f"climate.samples[{index}].zones.{zone}")
            usable: list[bool] = []
            values: dict[str, Decimal | None] = {}
            for metric in METRICS:
                parsed, observed_at, source_event_id, source_cycle_id = _metric_cell(
                    triplet[metric], f"climate.samples[{index}].zones.{zone}.{metric}"
                )
                if source_event_id in source_events:
                    blockers.append(f"duplicate_climate_source_event:{index}:{source_event_id}")
                source_events.add(source_event_id)
                cycle_aligned = source_cycle_id == cycle_id
                if not cycle_aligned:
                    blockers.append(f"asynchronous_source_membership:{index}:{zone}:{metric}")
                values[metric] = parsed
                usable.append(
                    parsed is not None
                    and cycle_aligned
                    and observed_at <= sample_at + MAX_SOURCE_FUTURE_SKEW
                    and sample_at - observed_at <= max_age
                )
            if all(usable):
                contributors.append(zone)
                for metric in METRICS:
                    assert values[metric] is not None
                    metric_values[metric].append(values[metric])
            elif any(usable):
                blockers.append(f"ambiguous_contributor_triplet:{index}:{zone}")

        declared = tuple(_array(sample["declared_contributors"], f"climate.samples[{index}].declared_contributors"))
        if any(type(zone) is not str for zone in declared) or len(set(declared)) != len(declared):
            raise PacketError(f"climate.samples[{index}].declared_contributors must contain unique strings")
        actual = tuple(contributors)
        if declared != actual:
            blockers.append(f"ambiguous_contributor_set:{index}")
        contributor_sets.append(actual)
        if len(actual) < 3:
            blockers.append(f"climate_quorum_below_three:{index}")

        aggregates = _exact_keys(sample["aggregates"], set(METRICS), f"climate.samples[{index}].aggregates")
        for metric in METRICS:
            aggregate, observed_at, source_event_id, source_cycle_id = _metric_cell(
                aggregates[metric], f"climate.samples[{index}].aggregates.{metric}"
            )
            if source_event_id in source_events:
                blockers.append(f"duplicate_climate_source_event:{index}:{source_event_id}")
            source_events.add(source_event_id)
            if source_cycle_id != cycle_id:
                blockers.append(f"asynchronous_aggregate_membership:{index}:{metric}")
            if aggregate is None:
                blockers.append(f"aggregate_non_finite:{index}:{metric}")
                continue
            if observed_at > sample_at + MAX_SOURCE_FUTURE_SKEW or sample_at - observed_at > max_age:
                blockers.append(f"aggregate_stale:{index}:{metric}")
            if metric in previous_aggregate_times and observed_at <= previous_aggregate_times[metric]:
                blockers.append(f"aggregate_not_advancing:{metric}")
            previous_aggregate_times[metric] = observed_at
            if metric_values[metric]:
                expected_mean = sum(metric_values[metric], Decimal(0)) / Decimal(len(metric_values[metric]))
                if abs(aggregate - expected_mean) > MEAN_TOLERANCE[metric]:
                    blockers.append(f"aggregate_mean_mismatch:{index}:{metric}")
                spread = max(metric_values[metric]) - min(metric_values[metric])
                if metric in MAX_SPREAD and spread > MAX_SPREAD[metric]:
                    blockers.append(f"implausible_cross_zone_spread:{index}:{metric}")

        diagnostics = _exact_keys(
            sample["diagnostics"], {"active_probe_count", "probe_health"}, f"climate.samples[{index}].diagnostics"
        )
        reported_count = _integer(
            diagnostics["active_probe_count"], f"climate.samples[{index}].diagnostics.active_probe_count"
        )
        reported_health = _text(diagnostics["probe_health"], f"climate.samples[{index}].diagnostics.probe_health")
        if reported_count != len(actual) or (reported_health == "OK" and len(actual) != 4):
            diagnostic_contradiction = True

    if len(set(contributor_sets)) != 1:
        blockers.append("contributor_set_changed_within_packet")
    contributors = contributor_sets[-1]
    excluded = tuple(zone for zone in ZONES if zone not in contributors)
    if diagnostic_contradiction:
        warnings.append("diagnostic_contradiction:false_green_probe_health")
    return len(contributors), contributors, excluded, diagnostic_contradiction


def _validate_alerts(raw: object, *, now: datetime, blockers: list[str], warnings: list[str]) -> None:
    rows = _array(raw, "alerts")
    required_scopes = {"south_wall_probe", "hydroponic_monitor"}
    observed_scopes: set[str] = set()
    for index, raw_row in enumerate(rows):
        row = _exact_keys(
            raw_row,
            {
                "alert_id",
                "alert_type",
                "scope",
                "disposition",
                "observed_at",
                "classification",
                "causal",
                "decision_issue_url",
                "maintenance_issue_url",
            },
            f"alerts[{index}]",
        )
        _text(row["alert_id"], f"alerts[{index}].alert_id")
        _text(row["alert_type"], f"alerts[{index}].alert_type")
        scope = _text(row["scope"], f"alerts[{index}].scope")
        observed_scopes.add(scope)
        _text(row["disposition"], f"alerts[{index}].disposition")
        observed = _timestamp(row["observed_at"], f"alerts[{index}].observed_at")
        _fresh(observed, now=now, max_age=MAX_CURRENT_EVIDENCE_AGE, label=f"alert:{scope}", blockers=blockers)
        classification = _text(row["classification"], f"alerts[{index}].classification")
        causal = _boolean(row["causal"], f"alerts[{index}].causal")
        if classification == "unclassified":
            blockers.append(f"unclassified_alert:{scope}")
        if causal:
            blockers.append(f"causal_alert:{scope}")
        if scope in required_scopes:
            if (
                classification != "accepted_nonblocking_degradation"
                or row["decision_issue_url"] != "https://github.com/VerdifyConsultancy/verdify-platform/issues/748"
                or row["maintenance_issue_url"] != "https://github.com/VerdifyConsultancy/verdify-platform/issues/751"
            ):
                blockers.append(f"degradation_alert_linkage_invalid:{scope}")
            else:
                warnings.append(f"accepted_nonblocking_degradation:{scope}")
        elif classification != "informational_noncausal":
            blockers.append(f"unsupported_alert_classification:{scope}")
    missing = required_scopes - observed_scopes
    if missing:
        blockers.append(f"required_degradation_alerts_missing:{sorted(missing)}")


def _validate_dependencies(
    raw: object,
    *,
    expected: ExpectedPins,
    repo_root: Path | None,
    blockers: list[str],
) -> None:
    value = _exact_keys(raw, {"application_source_revision", "surfaces", "classifications"}, "dependencies")
    _source_match(
        value["application_source_revision"], expected.application_source, "dependency_source_revision", blockers
    )
    classifications = _array(value["classifications"], "dependencies.classifications")
    classified: set[str] = set()
    for index, raw_classification in enumerate(classifications):
        classification = _exact_keys(
            raw_classification,
            {"dependency", "classification", "causal"},
            f"dependencies.classifications[{index}]",
        )
        name = _text(classification["dependency"], f"dependencies.classifications[{index}].dependency")
        if name in classified:
            blockers.append(f"duplicate_dependency_classification:{name}")
        classified.add(name)
        if classification["classification"] != "required_causal" or not _boolean(
            classification["causal"], f"dependencies.classifications[{index}].causal"
        ):
            blockers.append(f"invalid_dependency_classification:{name}")
    unknown_classified = classified - KNOWN_DEPENDENCIES
    if unknown_classified:
        blockers.append(f"unclassified_new_causal_dependencies:{sorted(unknown_classified)}")

    rows = _array(value["surfaces"], "dependencies.surfaces")
    observed: set[str] = set()
    used: set[str] = set()
    for index, raw_row in enumerate(rows):
        row = _exact_keys(
            raw_row,
            {"name", "path", "source_sha256", "application_source_revision", "causal_dependencies", "hydro_references"},
            f"dependencies.surfaces[{index}]",
        )
        name = _text(row["name"], f"dependencies.surfaces[{index}].name")
        observed.add(name)
        if name not in SURFACES:
            blockers.append(f"unknown_dependency_surface:{name}")
            continue
        if row["path"] != SURFACES[name]:
            blockers.append(f"dependency_surface_path_mismatch:{name}")
        source_hash = _text(row["source_sha256"], f"dependencies.surfaces[{index}].source_sha256")
        if not SHA64.fullmatch(source_hash):
            raise PacketError(f"dependencies.surfaces[{index}].source_sha256 must be a SHA-256")
        _source_match(
            row["application_source_revision"],
            expected.application_source,
            f"dependency_surface_source:{name}",
            blockers,
        )
        dependencies = _array(row["causal_dependencies"], f"dependencies.surfaces[{index}].causal_dependencies")
        for dependency in dependencies:
            dependency_name = _text(dependency, f"dependencies.surfaces[{index}].causal_dependency")
            used.add(dependency_name)
            if dependency_name not in classified:
                blockers.append(f"unclassified_causal_dependency:{name}:{dependency_name}")
        hydro = _array(row["hydro_references"], f"dependencies.surfaces[{index}].hydro_references")
        if hydro:
            blockers.append(f"hydro_causal_dependency_present:{name}")
        if repo_root is not None:
            path = repo_root / SURFACES[name]
            if not path.is_file():
                blockers.append(f"dependency_surface_source_missing:{name}")
            else:
                content = path.read_bytes()
                if hashlib.sha256(content).hexdigest() != source_hash:
                    blockers.append(f"dependency_surface_hash_mismatch:{name}")
                if re.search(rb"(?i)\b(hydro|hydroponic|yinmik)\b", content):
                    blockers.append(f"hydro_source_reference_present:{name}")
    if observed != set(SURFACES):
        blockers.append(f"dependency_surface_set_mismatch:missing={sorted(set(SURFACES) - observed)}")
    if used != classified:
        blockers.append(f"dependency_classification_usage_mismatch:unused={sorted(classified - used)}")


def _validate_evidence(
    raw: object,
    *,
    mode: Mode,
    expected: ExpectedPins,
    registry_revision: str,
    lease_generation: int,
    writer_generation: int,
    connection_generation: int,
    now: datetime,
    blockers: list[str],
) -> None:
    value = _exact_keys(
        raw,
        {"component_grid", "authentication_686", "provider_preflight", "served_control_observed_424", "writer_433"},
        "evidence",
    )
    common_keys = {
        "status",
        "observed_at",
        "application_source_revision",
        "experiment_id",
        "receipt_sha256",
    }

    component = _exact_keys(
        value["component_grid"],
        common_keys
        | {
            "expected_components",
            "observed_components",
            "fresh",
            "registry_revision",
            "lease_generation",
            "writer_generation",
            "connection_generation",
        },
        "evidence.component_grid",
    )
    if component["status"] != "pass" or not _boolean(component["fresh"], "evidence.component_grid.fresh"):
        blockers.append("component_grid_evidence_failed")
    if (
        _integer(component["expected_components"], "evidence.component_grid.expected_components") != 48
        or _integer(component["observed_components"], "evidence.component_grid.observed_components") != 48
    ):
        blockers.append("component_grid_not_48_of_48")
    if component["registry_revision"] != registry_revision:
        blockers.append("component_grid_registry_revision_mismatch")
    _generation_match(component["lease_generation"], lease_generation, "component_grid_lease_generation", blockers)
    _generation_match(component["writer_generation"], writer_generation, "component_grid_writer_generation", blockers)
    _generation_match(
        component["connection_generation"], connection_generation, "component_grid_connection_generation", blockers
    )

    auth = _exact_keys(
        value["authentication_686"],
        common_keys
        | {
            "replica_count",
            "replicas_checked",
            "public_unauthenticated_denied",
            "unknown_bearer_denied",
            "authenticated_iris_passed",
            "admin_query_denied",
            "session_identifier_absent",
        },
        "evidence.authentication_686",
    )
    if mode == "proof" and auth["status"] != "pass":
        blockers.append("authentication_acceptance_failed")
    replicas = _integer(auth["replica_count"], "evidence.authentication_686.replica_count", minimum=1)
    checked = _integer(auth["replicas_checked"], "evidence.authentication_686.replicas_checked", minimum=1)
    if mode == "proof" and checked != replicas:
        blockers.append("authentication_not_checked_on_current_replicas")
    for key in (
        "public_unauthenticated_denied",
        "unknown_bearer_denied",
        "authenticated_iris_passed",
        "admin_query_denied",
        "session_identifier_absent",
    ):
        passed = _boolean(auth[key], f"evidence.authentication_686.{key}")
        if mode == "proof" and not passed:
            blockers.append(f"authentication_{key}_failed")

    provider = _exact_keys(
        value["provider_preflight"],
        common_keys
        | {"non_actuating", "credential_present", "provider_reachable", "request_count", "device_call_count"},
        "evidence.provider_preflight",
    )
    if mode == "proof" and provider["status"] != "pass":
        blockers.append("provider_preflight_failed")
    non_actuating = _boolean(provider["non_actuating"], "evidence.provider_preflight.non_actuating")
    if mode == "proof" and not non_actuating:
        blockers.append("provider_preflight_actuating")
    credential_present = _boolean(provider["credential_present"], "evidence.provider_preflight.credential_present")
    if mode == "proof" and not credential_present:
        blockers.append("provider_credential_unavailable")
    provider_reachable = _boolean(provider["provider_reachable"], "evidence.provider_preflight.provider_reachable")
    if mode == "proof" and not provider_reachable:
        blockers.append("provider_unreachable")
    request_count = _integer(provider["request_count"], "evidence.provider_preflight.request_count")
    if mode == "proof" and request_count < 1:
        blockers.append("provider_preflight_request_absent")
    if (
        mode == "proof"
        and _integer(provider["device_call_count"], "evidence.provider_preflight.device_call_count") != 0
    ):
        blockers.append("provider_preflight_device_call_detected")

    passive = _exact_keys(
        value["served_control_observed_424"],
        common_keys | {"passive", "agreement", "series_checked", "device_call_count"},
        "evidence.served_control_observed_424",
    )
    passive_only = _boolean(passive["passive"], "evidence.served_control_observed_424.passive")
    if mode == "proof" and (passive["status"] != "pass" or not passive_only):
        blockers.append("served_control_observed_not_passive")
    agreement = _boolean(passive["agreement"], "evidence.served_control_observed_424.agreement")
    if mode == "proof" and not agreement:
        blockers.append("served_control_observed_disagreement")
    series_checked = _integer(passive["series_checked"], "evidence.served_control_observed_424.series_checked")
    if mode == "proof" and series_checked != 6:
        blockers.append("served_control_observed_series_not_six")
    if (
        mode == "proof"
        and _integer(passive["device_call_count"], "evidence.served_control_observed_424.device_call_count") != 0
    ):
        blockers.append("served_control_observed_device_call_detected")

    writer = _exact_keys(
        value["writer_433"],
        common_keys
        | {
            "current_writer_count",
            "lease_holder_matches",
            "generation_stable",
            "component_truth_48_of_48",
            "lease_generation",
            "writer_generation",
            "connection_generation",
        },
        "evidence.writer_433",
    )
    if writer["status"] != "pass":
        blockers.append("writer_433_evidence_failed")
    if _integer(writer["current_writer_count"], "evidence.writer_433.current_writer_count") != 1:
        blockers.append("writer_433_count_not_one")
    for key in ("lease_holder_matches", "generation_stable", "component_truth_48_of_48"):
        if not _boolean(writer[key], f"evidence.writer_433.{key}"):
            blockers.append(f"writer_433_{key}_failed")
    _generation_match(writer["lease_generation"], lease_generation, "writer_433_lease_generation", blockers)
    _generation_match(writer["writer_generation"], writer_generation, "writer_433_writer_generation", blockers)
    _generation_match(
        writer["connection_generation"], connection_generation, "writer_433_connection_generation", blockers
    )

    for label, row, max_age, required_mode in (
        ("component_grid", component, MAX_COMPONENT_AGE, "shared"),
        ("authentication_686", auth, MAX_AUTH_AGE, "proof"),
        ("provider_preflight", provider, MAX_CURRENT_EVIDENCE_AGE, "proof"),
        ("served_control_observed_424", passive, MAX_CURRENT_EVIDENCE_AGE, "proof"),
        ("writer_433", writer, MAX_COMPONENT_AGE, "shared"),
    ):
        _text(row["status"], f"evidence.{label}.status")
        observed = _timestamp(row["observed_at"], f"evidence.{label}.observed_at")
        source = _text(row["application_source_revision"], f"evidence.{label}.application_source_revision")
        experiment_id = _uuid(row["experiment_id"], f"evidence.{label}.experiment_id")
        receipt = _text(row["receipt_sha256"], f"evidence.{label}.receipt_sha256")
        if not SHA64.fullmatch(receipt):
            raise PacketError(f"evidence.{label}.receipt_sha256 must be a SHA-256")
        if required_mode == "proof" and mode != "proof":
            continue
        _fresh(observed, now=now, max_age=max_age, label=f"{label}_evidence", blockers=blockers)
        _source_match(source, expected.application_source, f"{label}_source_revision", blockers)
        if experiment_id != expected.experiment_id:
            blockers.append(f"{label}_experiment_id_mismatch")


def _validate_issue_state(raw: object, *, mode: Mode, blockers: list[str]) -> None:
    value = _exact_keys(raw, {"decision_748", "maintenance_751", "recovery_747", "gate_p_641"}, "issue_state")
    decision = _exact_keys(value["decision_748"], {"accepted", "issue_url"}, "issue_state.decision_748")
    if not _boolean(decision["accepted"], "issue_state.decision_748.accepted") or decision["issue_url"] != (
        "https://github.com/VerdifyConsultancy/verdify-platform/issues/748"
    ):
        blockers.append("decision_748_not_bound")
    maintenance = _exact_keys(value["maintenance_751"], {"deferred", "issue_url"}, "issue_state.maintenance_751")
    if not _boolean(maintenance["deferred"], "issue_state.maintenance_751.deferred") or maintenance["issue_url"] != (
        "https://github.com/VerdifyConsultancy/verdify-platform/issues/751"
    ):
        blockers.append("maintenance_751_not_bound")
    recovery = _exact_keys(
        value["recovery_747"],
        {"corrected_one_off_complete", "full_acceptance_complete", "issue_url"},
        "issue_state.recovery_747",
    )
    if not _boolean(recovery["corrected_one_off_complete"], "issue_state.recovery_747.corrected_one_off_complete"):
        blockers.append("issue_747_recovery_acceptance_incomplete")
    full_recovery_complete = _boolean(
        recovery["full_acceptance_complete"], "issue_state.recovery_747.full_acceptance_complete"
    )
    if mode == "proof" and not full_recovery_complete:
        blockers.append("issue_747_full_acceptance_incomplete")
    if recovery["issue_url"] != "https://github.com/VerdifyConsultancy/verdify-platform/issues/747":
        blockers.append("issue_747_not_bound")
    gate = _exact_keys(value["gate_p_641"], {"issue_url", "prerequisites"}, "issue_state.gate_p_641")
    gate_issue_url = _text(gate["issue_url"], "issue_state.gate_p_641.issue_url", empty=True)
    if mode == "proof" and gate_issue_url != "https://github.com/VerdifyConsultancy/verdify-platform/issues/641":
        blockers.append("issue_641_not_bound")
    prerequisites = _array(gate["prerequisites"], "issue_state.gate_p_641.prerequisites")
    observed: set[str] = set()
    for index, raw_row in enumerate(prerequisites):
        row = _exact_keys(raw_row, {"name", "complete"}, f"issue_state.gate_p_641.prerequisites[{index}]")
        name = _text(row["name"], f"issue_state.gate_p_641.prerequisites[{index}].name")
        observed.add(name)
        complete = _boolean(row["complete"], f"issue_state.gate_p_641.{name}.complete")
        if mode == "proof" and not complete:
            blockers.append(f"issue_641_prerequisite_incomplete:{name}")
    if mode == "proof" and observed != GATE_P_PREREQUISITES:
        blockers.append(
            f"issue_641_prerequisite_set_mismatch:missing={sorted(GATE_P_PREREQUISITES - observed)}:extra={sorted(observed - GATE_P_PREREQUISITES)}"
        )


def _validate_guard_chain(
    raw: object,
    *,
    mode: Mode,
    boundary: Boundary,
    prior: ChainState | None,
    blockers: list[str],
) -> tuple[str, int, str | None]:
    value = _exact_keys(raw, {"attempt_id", "sequence", "previous_receipt_sha256"}, "guard")
    attempt_id = _uuid(value["attempt_id"], "guard.attempt_id")
    sequence = _integer(value["sequence"], "guard.sequence")
    expected_sequence = BOUNDARY_SEQUENCE[mode].index(boundary)
    if sequence != expected_sequence:
        blockers.append("guard_boundary_sequence_mismatch")
    previous = value["previous_receipt_sha256"]
    if previous is not None and (not isinstance(previous, str) or not SHA64.fullmatch(previous)):
        raise PacketError("guard.previous_receipt_sha256 must be null or a SHA-256")
    if prior is None:
        if sequence != 0 or previous is not None:
            blockers.append("guard_chain_predecessor_missing")
    else:
        if prior.mode != mode or prior.attempt_id != attempt_id:
            blockers.append("guard_chain_attempt_mismatch")
        if prior.next_sequence != sequence or prior.last_receipt_sha256 != previous:
            blockers.append("guard_chain_replay_or_gap")
    return attempt_id, sequence, previous


def evaluate_packet(
    packet: object,
    *,
    expected: ExpectedPins,
    now: datetime,
    mode: Mode,
    boundary: Boundary,
    prior: ChainState | None = None,
    repo_root: Path | None = None,
) -> dict[str, object]:
    """Evaluate one exact packet and return a deterministic safe result."""

    top = _exact_keys(
        packet,
        {
            "schema",
            "mode",
            "boundary",
            "packet_id",
            "captured_at",
            "guard",
            "provenance",
            "workloads",
            "runtime",
            "backup",
            "argo",
            "climate",
            "alerts",
            "dependencies",
            "evidence",
            "issue_state",
        },
        "packet",
    )
    if top["schema"] != INPUT_SCHEMA:
        raise PacketError("packet schema mismatch")
    if top["mode"] != mode or top["boundary"] != boundary:
        raise PacketError("packet mode/boundary differs from the requested guard")
    if boundary not in BOUNDARY_SEQUENCE[mode]:
        raise PacketError("boundary is not valid for the requested mode")
    packet_id = _uuid(top["packet_id"], "packet.packet_id")
    captured_at = _timestamp(top["captured_at"], "packet.captured_at")
    blockers: list[str] = []
    warnings: list[str] = []
    _fresh(captured_at, now=now, max_age=MAX_PACKET_AGE, label="readiness_packet", blockers=blockers)
    attempt_id, sequence, previous = _validate_guard_chain(
        top["guard"], mode=mode, boundary=boundary, prior=prior, blockers=blockers
    )
    lease, writer_generation, connection_generation, registry = _validate_provenance(
        top["provenance"], expected, now=now, blockers=blockers
    )
    _validate_workloads(top["workloads"], now=now, blockers=blockers)
    open_exposures = _validate_runtime(
        top["runtime"], mode=mode, boundary=boundary, expected=expected, blockers=blockers
    )
    runtime = top["runtime"]
    assert isinstance(runtime, Mapping)
    _generation_match(runtime["lease_generation"], lease, "runtime_lease_generation", blockers)
    _generation_match(runtime["writer_generation"], writer_generation, "runtime_writer_generation", blockers)
    _generation_match(
        runtime["connection_generation"], connection_generation, "runtime_connection_generation", blockers
    )
    if runtime["registry_revision"] != registry:
        blockers.append("runtime_registry_revision_mismatch")
    _validate_backup(top["backup"], mode=mode, expected=expected, now=now, blockers=blockers)
    _validate_argo(top["argo"], mode=mode, expected=expected, now=now, blockers=blockers)
    count, contributors, excluded, contradiction = _validate_climate(
        top["climate"], captured_at=captured_at, blockers=blockers, warnings=warnings
    )
    climate = top["climate"]
    assert isinstance(climate, Mapping)
    qualification = climate["qualification_capture"]
    assert isinstance(qualification, Mapping)
    _source_match(
        qualification["application_source_revision"],
        expected.application_source,
        "climate_qualification_source_revision",
        blockers,
    )
    _validate_alerts(top["alerts"], now=now, blockers=blockers, warnings=warnings)
    _validate_dependencies(top["dependencies"], expected=expected, repo_root=repo_root, blockers=blockers)
    _validate_evidence(
        top["evidence"],
        mode=mode,
        expected=expected,
        registry_revision=registry,
        lease_generation=lease,
        writer_generation=writer_generation,
        connection_generation=connection_generation,
        now=now,
        blockers=blockers,
    )
    _validate_issue_state(top["issue_state"], mode=mode, blockers=blockers)

    blockers = sorted(set(blockers))
    warnings = sorted(set(warnings))
    status = "fail" if blockers else ("degraded-pass" if count == 3 else "pass")
    authorized_gate = None if blockers else ("R" if mode == "recovery" else "P")
    mandatory_action = (
        "close_exposure_first_revoke_nonbaseline_enter_emergency_hold_preserve_attempt"
        if blockers and open_exposures > 0
        else "block_before_actuation_preserve_attempt"
        if blockers
        else "proceed_only_to_authorized_boundary"
    )
    receipt_preimage = {
        "schema": RESULT_SCHEMA,
        "packet_sha256": _sha256(top),
        "packet_id": packet_id,
        "attempt_id": attempt_id,
        "mode": mode,
        "boundary": boundary,
        "sequence": sequence,
        "previous_receipt_sha256": previous,
        "captured_at": top["captured_at"],
        "evaluated_at": now.isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "status": status,
        "authorized_gate": authorized_gate,
        "contributors": {"count": count, "included": list(contributors), "excluded": list(excluded)},
        "diagnostic_contradiction": contradiction,
        "blockers": blockers,
        "warnings": warnings,
        "mandatory_action": mandatory_action,
        "expected": {
            "git_pin": expected.git_pin,
            "application_source_revision": expected.application_source,
            "experiment_id": expected.experiment_id,
        },
    }
    return {**receipt_preimage, "receipt_sha256": _sha256(receipt_preimage)}


def _load_json(path: Path) -> object:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=lambda value: (_ for _ in ()).throw(PacketError(f"non-finite JSON constant: {value}")),
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise PacketError(f"cannot read readiness packet: {exc}") from exc


def _load_state(path: Path) -> ChainState | None:
    if not path.exists():
        return None
    raw = _exact_keys(
        _load_json(path), {"schema", "mode", "attempt_id", "next_sequence", "last_receipt_sha256"}, "state"
    )
    if raw["schema"] != STATE_SCHEMA or raw["mode"] not in BOUNDARY_SEQUENCE:
        raise PacketError("readiness chain state schema/mode mismatch")
    receipt = raw["last_receipt_sha256"]
    if receipt is not None and (not isinstance(receipt, str) or not SHA64.fullmatch(receipt)):
        raise PacketError("readiness chain state receipt is invalid")
    return ChainState(
        mode=raw["mode"],
        attempt_id=_uuid(raw["attempt_id"], "state.attempt_id"),
        next_sequence=_integer(raw["next_sequence"], "state.next_sequence"),
        last_receipt_sha256=receipt,
    )


def _write_state(path: Path, result: Mapping[str, object]) -> None:
    payload = {
        "schema": STATE_SCHEMA,
        "mode": result["mode"],
        "attempt_id": result["attempt_id"],
        "next_sequence": int(result["sequence"]) + 1,
        "last_receipt_sha256": result["receipt_sha256"],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="metadata-only readiness packet JSON")
    parser.add_argument("--mode", choices=tuple(BOUNDARY_SEQUENCE), required=True)
    parser.add_argument(
        "--boundary", choices=tuple(item for values in BOUNDARY_SEQUENCE.values() for item in values), required=True
    )
    parser.add_argument("--expected-git-pin", required=True)
    parser.add_argument("--expected-application-source", required=True)
    parser.add_argument("--expected-experiment-id", required=True)
    parser.add_argument("--now", help="deterministic UTC evaluation time; defaults to current UTC")
    parser.add_argument("--state", type=Path, help="one-use boundary chain state file")
    parser.add_argument("--repo-root", type=Path, help="also verify dependency source files/hashes locally")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if not SHA40.fullmatch(args.expected_git_pin):
            raise PacketError("--expected-git-pin must be a 40-character lowercase Git SHA")
        if not SHA40.fullmatch(args.expected_application_source):
            raise PacketError("--expected-application-source must be a 40-character lowercase Git SHA")
        experiment_id = _uuid(args.expected_experiment_id, "--expected-experiment-id")
        now = _timestamp(args.now, "--now") if args.now else datetime.now(UTC)
        mode: Mode = args.mode
        boundary: Boundary = args.boundary
        if boundary not in BOUNDARY_SEQUENCE[mode]:
            raise PacketError("boundary does not belong to mode")
        prior = _load_state(args.state) if args.state else None
        if BOUNDARY_SEQUENCE[mode].index(boundary) > 0 and args.state is None:
            raise PacketError("proof boundaries after gate-p require --state replay protection")
        result = evaluate_packet(
            _load_json(args.input),
            expected=ExpectedPins(args.expected_git_pin, args.expected_application_source, experiment_id),
            now=now,
            mode=mode,
            boundary=boundary,
            prior=prior,
            repo_root=args.repo_root.resolve() if args.repo_root else None,
        )
    except PacketError as exc:
        failure_action = (
            "close_exposure_first_revoke_nonbaseline_enter_emergency_hold_preserve_attempt"
            if getattr(args, "boundary", None) == "baseline-after"
            else "block_before_actuation_preserve_attempt"
        )
        print(
            json.dumps(
                {
                    "schema": RESULT_SCHEMA,
                    "status": "fail",
                    "authorized_gate": None,
                    "blockers": [f"malformed_packet:{exc}"],
                    "mandatory_action": failure_action,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    if result["status"] == "fail":
        return 1
    if args.state:
        _write_state(args.state, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
