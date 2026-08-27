"""Confirmed-component experiment executor (ADR-0010, #433, #639).

The core is intentionally dependency-injected.  PostgreSQL owns immutable
phase-typed work, admission, revisions, claims, bundle reservations, raw cfg
epochs, receipts, exposure, and recovery linkage.  This worker owns only the
bounded physical decision:

* fail the environment gate before constructing/using a store;
* validate one least-information resolved work record;
* send an exact fixed-order difference bundle through the sole ESPHome writer;
* never resend a durably reserved/delivered bundle after restart; and
* open exposure only after two independently persisted qualifying cfg epochs.

No observation epoch is created or relabelled here.  The cfg-ingestion/L3 path
owns epoch IDs, raw observations, state hashes, and receipts.  Store methods are
awaited at every truth boundary; database uncertainty never becomes success.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol
from uuid import UUID, uuid4

import asyncpg
import shared
from aioesphomeapi.api_pb2 import SubscribeStatesRequest
from esp32_push import (
    ComponentBundleCall,
    ComponentBundleResult,
    ComponentCommandOutcome,
    ComponentStateCallback,
    LifecyclePersistenceError,
    component_authority_hold,
    device_writes_enabled,
    push_component_bundle,
    set_component_authority_hold,
)

from verdify_schemas.component_executor import (
    ACTIVATION_ORDER,
    CANONICAL_FIELD_ORDER,
    COMMON_FIELDS,
    ENTITY_GRIDS,
    RECOVERY_ORDER,
    ROLLBACK_ORDER,
    WORK_KIND_ASSIGNMENT,
    WORK_KIND_PREVIEW,
    WORK_KIND_RECOVERY,
    ComponentChange,
    ComponentContractError,
    ComponentValue,
    fixed_order_complete_bundle,
    fixed_order_differences,
    normalize_complete_state,
    normalize_observed_component_value,
    physical_execution_qualified,
    validate_routine_target,
    validate_work_phase,
)
from verdify_schemas.component_qualification import (
    ComponentGridEvidenceError,
    LiveEntityGridEvidence,
    RuntimeEntityMetadata,
    build_live_entity_grid_evidence,
)
from verdify_schemas.experiment_config import (
    active_experiment_id,
    component_experiment_gate,
    component_startup_hold_required,
    policy_device_id,
)
from verdify_schemas.policy_vector import decode_policy_vector, encode_policy_vector
from verdify_schemas.tunable_registry import REGISTRY

log = logging.getLogger("component_experiment")

# Centralized L3 names.  Keeping every identifier here lets the controller
# align the adapter to migration 214 without scattering SQL through the state
# machine.  The executor never reads randomization/mapping/future-work tables.
L3_WORK_TABLE = "experiment_v2_work"
L3_RESOLVE_READINESS = "fn_experiment_v2_resolve_readiness"
L3_RESOLVE_RANDOMIZED = "fn_experiment_v2_resolve_randomized"
L3_RESOLVE_RECOVERY = "fn_experiment_v2_resolve_recovery"
L3_EXECUTOR_RUNTIME = "fn_experiment_v2_executor_runtime"
L3_CLAIM_COMPONENT_WORK = "fn_experiment_v2_claim_executor_candidate"
L3_READ_DELIVERY_BUNDLE = "fn_experiment_v2_read_delivery_bundle"
L3_READ_OBSERVATION_EPOCHS = "fn_experiment_v2_read_observation_window"
L3_RECORD_WORK_EVENT = "fn_experiment_v2_record_work_event"
L3_BEGIN_DELIVERY_BUNDLE = "fn_experiment_v2_begin_delivery_bundle"
L3_RECORD_DELIVERY_BUNDLE = "fn_experiment_v2_record_delivery_bundle"
L3_RECORD_COMPONENT_OUTCOME = "fn_experiment_v2_record_component_outcome"
L3_RECORD_RUNTIME_GENERATION = "fn_experiment_v2_register_runtime_instance"
L3_RECORD_OBSERVATION_EPOCH = "fn_experiment_v2_record_observation_epoch"
L3_OPEN_EXPOSURE = "fn_experiment_v2_open_exposure"
L3_CLOSE_EXPOSURE = "fn_experiment_v2_close_exposure"
L3_REQUEST_RECOVERY = "fn_experiment_v2_request_recovery"
L3_RECORD_RUNTIME_SNAPSHOT = "fn_experiment_v2_record_runtime_snapshot"
L3_RECORD_PREEXPOSURE_MISMATCH = "fn_experiment_v2_record_preexposure_mismatch"
L3_MONITOR_OPEN_EXPOSURE = "fn_experiment_v2_monitor_open_exposure"
L3_REPORT_RUNTIME_FAULT = "fn_experiment_v2_report_runtime_fault"
L3_SAFE_STARTUP_ATTESTATION = "fn_experiment_v2_safe_startup_attestation"

MAX_SNAPSHOT_AGE = timedelta(seconds=90)
MIN_EPOCH_SEPARATION = timedelta(seconds=30)
MAX_EPOCH_SKEW = timedelta(seconds=60)
COMPONENT_EXECUTOR_INTERVAL_S = 15
COMPONENT_EXECUTOR_ACTOR = "verdify-component-executor-v2"
RUNTIME_INSTANCE_ID = str(uuid4())
_SOURCE_EPOCH_BUFFER_SIZE = 8
_STATE_REPLAY_MIN_INTERVAL_S = 31.0

WorkDisposition = Literal[
    "idle",
    "previewed",
    "deferred",
    "delivered",
    "confirmed",
    "recovered",
    "failed",
    "yielded",
    "superseded",
]
BundlePurpose = Literal["preview", "target", "recovery"]


class ComponentStoreError(RuntimeError):
    """The executor cannot prove a durable L3 transition."""


class ComponentRuntimeFault(ComponentStoreError):
    """A source-owned runtime signal durably invalidated current work."""

    def __init__(
        self,
        reason: str,
        *,
        authority_hold_required: bool = True,
        facility_authority_yielded: bool = False,
    ) -> None:
        super().__init__(reason)
        if facility_authority_yielded and authority_hold_required:
            raise ValueError("a yielded runtime fault cannot retain experiment authority")
        self.reason = reason
        self.authority_hold_required = authority_hold_required
        self.facility_authority_yielded = facility_authority_yielded


@dataclass(frozen=True)
class RevisionSet:
    bundle_sha256: str
    firmware_revision: str
    config_revision: str
    registry_revision: str
    grid_revision: str


@dataclass(frozen=True)
class RawCfgSourceEpoch:
    """One source-owned complete cfg callback epoch.

    The ESPHome callback creates the UUID and timestamps.  Delivery code may
    bind this immutable source epoch to a completed bundle, but cannot mint,
    retime, or relabel it.  A process restart intentionally discards partial
    collection rather than claiming freshness for cached readbacks.
    """

    source_epoch_id: str
    experiment_id: str
    wire_vector: bytes
    values: Mapping[str, ComponentValue]
    observed_at: Mapping[str, datetime]
    revisions: RevisionSet
    runtime_instance_id: str
    lease_generation: int
    writer_generation: int
    connection_generation: int
    reset_detected: bool
    completed_at: datetime


@dataclass(frozen=True)
class RuntimeAuthority:
    lease_generation: int
    writer_generation: int
    device_id: str
    component_authority_required: bool
    observation_source_required: bool
    rescue_authorized: bool
    revisions: RevisionSet
    runtime_instance_id: str | None = None
    connection_generation: int | None = None


@dataclass(frozen=True)
class RuntimeExposureStatus:
    exposure_id: str
    work_id: str
    exposure_is_open: bool
    close_reason: str | None
    recovery_work_id: str | None
    current_runtime_instance_id: str | None
    current_writer_generation: int | None
    current_connection_generation: int | None
    source_epoch_id: str | None
    source_runtime_instance_id: str | None
    source_writer_generation: int | None
    source_connection_generation: int | None
    common_field_drift: bool
    cfg_drift: bool
    lineage_drift: bool
    reset_detected: bool
    foreign_writer: bool
    exposure_started_at: datetime | None = None
    last_observed_at: datetime | None = None
    resolved_at: datetime | None = None


@dataclass(frozen=True)
class RuntimeFaultReceipt:
    fault_report_id: str
    close_reason: str
    recovery_work_id: str | None
    admission_state_after: str
    authority_hold_required: bool
    facility_authority_yielded: bool
    recorded_at: datetime


@dataclass(frozen=True)
class StartupAttestation:
    device_id: str
    requested_experiment_id: str | None
    scoped_experiment_id: str | None
    scope_resolved: bool
    current_lease_generation: int | None
    active_experiment_count: int
    open_exposure_count: int
    recovery_pending_count: int
    experiment_authority_active: bool
    facility_authority_yielded: bool
    hold_required: bool
    attestation_reason: str
    attested_at: datetime


@dataclass(frozen=True)
class RuntimeReporterIdentity:
    experiment_id: str
    device_id: str
    expected_lease_generation: int
    runtime_instance_id: str
    writer_generation: int
    connection_generation: int


@dataclass(frozen=True)
class PendingRuntimeFault:
    fault_report_id: str
    fault_kind: str
    reason: str
    reporter: RuntimeReporterIdentity | None


@dataclass(frozen=True)
class WorkSignals:
    rebooted: bool = False
    reset_detected: bool = False
    reconnected: bool = False
    foreign_writer: bool = False
    facility_rescue_active: bool = False
    facility_recovery_authorized: bool = False
    nonbaseline_reentry_forbidden: bool = False
    generation_recovery_cleared: bool = False
    snapshot_recovery_cleared: bool = False
    same_generation_nonbaseline_reentry_forbidden: bool = False

    @property
    def invalidates_physical_state(self) -> bool:
        return self.rebooted or self.reset_detected or self.reconnected or self.foreign_writer


@dataclass(frozen=True)
class ResolvedWork:
    experiment_id: str
    work_id: str
    assignment_id: str | None
    operation_kind: str
    execution_phase: str
    admission_state: str
    lifecycle_status: str
    protocol_version: int
    transport_kind: str
    target_profile: str
    target_state_content_sha256: str
    baseline_state_content_sha256: str
    target_state: Mapping[str, ComponentValue]
    baseline_state: Mapping[str, ComponentValue]
    revisions: RevisionSet
    expected_revision_bundle_sha256: str
    lease_generation: int
    runtime_instance_id: str
    writer_generation: int
    connection_generation: int
    valid_from: datetime
    valid_until: datetime
    expires_at: datetime
    claim_expires_at: datetime
    resolved_at: datetime
    device_id: str
    baseline_interposition_confirmed: bool = False
    signals: WorkSignals = field(default_factory=WorkSignals)
    open_exposure_id: str | None = None


@dataclass(frozen=True)
class ObservedComponent:
    value: ComponentValue
    observed_at: datetime


@dataclass(frozen=True)
class ObservationEpoch:
    source_epoch_id: str
    experiment_id: str
    work_id: str
    bundle_id: str
    execution_phase: str
    operation_kind: str
    identity_source: str
    state_content_sha256: str
    observations: Mapping[str, ObservedComponent]
    persisted_at: datetime
    revisions: RevisionSet
    runtime_instance_id: str
    writer_generation: int
    connection_generation: int

    @property
    def values(self) -> dict[str, ComponentValue]:
        return {name: observation.value for name, observation in self.observations.items()}


# Raw cfg callbacks are synchronous and run on the same asyncio event-loop
# thread as the worker.  No executor method writes these structures.
_cfg_source_identity: tuple[str, int, int, int, RevisionSet] | None = None
_cfg_source_pending: dict[str, ObservedComponent] = {}
_cfg_source_last_completed_at: dict[str, datetime] = {}
_cfg_source_epochs: deque[RawCfgSourceEpoch] = deque(maxlen=_SOURCE_EPOCH_BUFFER_SIZE)
_cfg_source_last_uptime_s: float | None = None
_cfg_source_reset_pending = False
_cfg_source_unacked_reset_epoch: RawCfgSourceEpoch | None = None
_cfg_source_unacked_mismatch_epoch: RawCfgSourceEpoch | None = None
_runtime_reporters: dict[tuple[str, str], RuntimeReporterIdentity] = {}
_pending_runtime_faults: dict[tuple[str, str], PendingRuntimeFault] = {}
_state_replay_identity: tuple[str, int, int, int, RevisionSet] | None = None
_state_replay_last_requested_monotonic: float | None = None
_component_grid_inventory: tuple[RuntimeEntityMetadata, ...] | None = None
_component_grid_inventory_generation: int | None = None
_component_grid_inventory_observed_at: datetime | None = None
_component_grid_attestation: LiveEntityGridEvidence | None = None
_component_grid_attempted_firmware_revision: str | None = None


def record_component_entity_inventory(
    entities: Sequence[RuntimeEntityMetadata],
    *,
    connection_generation: int,
    observed_at: datetime | None = None,
) -> None:
    """Stage metadata from the ingestor's existing authenticated enumeration.

    This function performs no ESPHome call.  The caller supplies the result of
    the connection loop's one existing ``list_entities_services`` request.
    Device-reported firmware identity arrives on the existing state callback
    and completes the evidence separately below.
    """
    global _component_grid_inventory, _component_grid_inventory_generation
    global _component_grid_inventory_observed_at, _component_grid_attestation
    global _component_grid_attempted_firmware_revision
    moment = _aware(observed_at or datetime.now(UTC), "entity_inventory_observed_at")
    _component_grid_inventory = tuple(entities)
    _component_grid_inventory_generation = connection_generation
    _component_grid_inventory_observed_at = moment
    _component_grid_attestation = None
    _component_grid_attempted_firmware_revision = None


def clear_component_entity_inventory(*, connection_generation: int | None = None) -> None:
    """Discard current-route evidence without deleting historical log proof."""
    global _component_grid_inventory, _component_grid_inventory_generation
    global _component_grid_inventory_observed_at, _component_grid_attestation
    global _component_grid_attempted_firmware_revision
    if connection_generation is not None and _component_grid_inventory_generation != connection_generation:
        return
    _component_grid_inventory = None
    _component_grid_inventory_generation = None
    _component_grid_inventory_observed_at = None
    _component_grid_attestation = None
    _component_grid_attempted_firmware_revision = None


def record_component_grid_firmware_revision(
    value: object,
    *,
    observed_at: datetime | None = None,
) -> bool:
    """Complete and log one live-grid attestation from an existing callback.

    Returns ``True`` only when a new current-generation attestation passes.
    A mismatch is logged once per firmware value and remains non-fatal to the
    ordinary greenhouse telemetry path; the independent physical execution
    gate stays closed.
    """
    global _component_grid_attestation, _component_grid_attempted_firmware_revision
    if not isinstance(value, str) or not value:
        return False
    inventory = _component_grid_inventory
    generation = _component_grid_inventory_generation
    enumerated_at = _component_grid_inventory_observed_at
    if inventory is None or generation is None or enumerated_at is None:
        return False
    if generation != int(shared.transport_generation):
        clear_component_entity_inventory(connection_generation=generation)
        return False
    if _component_grid_attestation is not None and _component_grid_attestation.firmware_revision == value:
        return False
    if _component_grid_attempted_firmware_revision == value:
        return False
    _component_grid_attempted_firmware_revision = value
    moment = _aware(observed_at or enumerated_at, "entity_grid_firmware_observed_at")
    try:
        evidence = build_live_entity_grid_evidence(
            inventory,
            device_id=policy_device_id(os.environ.get("GREENHOUSE_ID", "vallery")),
            firmware_revision=value,
            source_revision=os.environ.get("VERDIFY_GIT_SHA", ""),
            runtime_instance_id=RUNTIME_INSTANCE_ID,
            connection_generation=generation,
            observed_at=moment,
        )
    except ComponentGridEvidenceError as exc:
        log.error(
            "component_entity_grid_attestation status=fail code=%s detail=%s connection_generation=%d",
            exc.code,
            exc.detail,
            generation,
        )
        return False
    _component_grid_attestation = evidence
    log.info(
        "component_entity_grid_attestation status=pass grid_revision=%s "
        "observation_receipt_sha256=%s field_count=%d firmware_revision=%s "
        "source_revision=%s connection_generation=%d",
        evidence.grid_revision,
        evidence.observation_receipt_sha256,
        evidence.field_count,
        evidence.firmware_revision,
        evidence.source_revision,
        evidence.connection_generation,
    )
    return True


def component_entity_grid_attestation() -> LiveEntityGridEvidence | None:
    """Return evidence only while its exact authenticated connection is live."""
    evidence = _component_grid_attestation
    if evidence is None or evidence.connection_generation != int(shared.transport_generation):
        return None
    client = shared.esp32.get("client")
    if (
        client is None
        or shared.esp32.get("state_subscription_client") is not client
        or shared.esp32.get("state_subscription_generation") != evidence.connection_generation
    ):
        return None
    return evidence


def configure_component_cfg_source(
    *,
    experiment_id: str | None,
    lease_generation: int | None,
    writer_generation: int | None,
    connection_generation: int | None,
    revisions: RevisionSet | None,
) -> None:
    """Arm one source identity or discard all partial/cached collection.

    A changed experiment, lease generation, transport generation, or revision
    tuple starts a new source lineage.  Completed epochs from an old lineage
    are discarded; they can never be rebound to new work after reconnect.
    """
    global _cfg_source_identity, _cfg_source_last_uptime_s, _cfg_source_reset_pending
    global _cfg_source_unacked_reset_epoch, _cfg_source_unacked_mismatch_epoch
    global _state_replay_identity, _state_replay_last_requested_monotonic
    identity = (
        (experiment_id, lease_generation, writer_generation, connection_generation, revisions)
        if experiment_id is not None
        and lease_generation is not None
        and writer_generation is not None
        and connection_generation is not None
        and revisions is not None
        else None
    )
    if identity == _cfg_source_identity:
        return
    _cfg_source_identity = identity
    _cfg_source_pending.clear()
    _cfg_source_last_completed_at.clear()
    _cfg_source_epochs.clear()
    _cfg_source_last_uptime_s = None
    _cfg_source_reset_pending = False
    _cfg_source_unacked_reset_epoch = None
    _cfg_source_unacked_mismatch_epoch = None
    _state_replay_identity = None
    _state_replay_last_requested_monotonic = None


def record_component_device_uptime(value: object) -> bool:
    """Latch an in-connection device reset from the raw uptime callback.

    Reconnects are independently fenced by the transport/runtime generation.
    This source catches the narrower case where the firmware uptime regresses
    without a native-API reconnect. The next complete all-48 raw epoch carries
    the immutable reset flag into migration 214.
    """
    global _cfg_source_last_uptime_s, _cfg_source_reset_pending
    if _cfg_source_identity is None or isinstance(value, bool):
        return False
    try:
        uptime_s = float(value)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(uptime_s) or uptime_s < 0:
        return False
    previous = _cfg_source_last_uptime_s
    _cfg_source_last_uptime_s = uptime_s
    if previous is not None and uptime_s + 1.0 < previous:
        _cfg_source_reset_pending = True
        return True
    return False


def record_component_cfg_readback(
    field_name: str,
    value: object,
    *,
    observed_at: datetime | None = None,
) -> bool:
    """Ingest one raw cfg callback and freeze an epoch only after all 48 advance.

    Returns ``True`` only when this callback completed a new source epoch.
    Unknown/noncanonical/off-grid values are ignored fail-closed.  This hook is
    deliberately separate from periodic ``setpoint_snapshot`` persistence;
    cached flushes never call it and therefore cannot satisfy a receipt.
    """
    global _cfg_source_reset_pending, _cfg_source_unacked_reset_epoch
    identity = _cfg_source_identity
    if identity is None or field_name not in CANONICAL_FIELD_ORDER:
        return False
    experiment_id, lease_generation, writer_generation, connection_generation, revisions = identity
    if connection_generation != int(shared.transport_generation):
        # The worker has not yet accepted the new socket generation.  Drop the
        # partial epoch; callbacks from two connections can never be combined.
        configure_component_cfg_source(
            experiment_id=None,
            lease_generation=None,
            writer_generation=None,
            connection_generation=None,
            revisions=None,
        )
        return False

    # A callback is the newest source statement for this wire. Remove any
    # earlier pending value before validating it so an invalid/off-grid update
    # cannot be hidden behind a stale valid sample from the same epoch.
    _cfg_source_pending.pop(field_name, None)

    grid_value: object = value
    if ENTITY_GRIDS[field_name].entity_type == "switch":
        if value in (0, 0.0, False):
            grid_value = False
        elif value in (1, 1.0, True):
            grid_value = True
        else:
            return False
    try:
        normalized = normalize_observed_component_value(field_name, grid_value)
    except ComponentContractError:
        return False
    moment = _aware(observed_at or datetime.now(UTC), "cfg_observed_at")
    prior = _cfg_source_last_completed_at.get(field_name)
    if prior is not None and moment <= prior:
        return False
    _cfg_source_pending[field_name] = ObservedComponent(normalized, moment)
    if frozenset(_cfg_source_pending) != frozenset(CANONICAL_FIELD_ORDER):
        return False

    values = {name: _cfg_source_pending[name].value for name in CANONICAL_FIELD_ORDER}
    timestamps = {name: _cfg_source_pending[name].observed_at for name in CANONICAL_FIELD_ORDER}
    try:
        normalized_state = normalize_complete_state(values)
        wire_vector = encode_policy_vector(normalized_state)
    except (ComponentContractError, ValueError):
        _cfg_source_pending.clear()
        return False
    mark_reset = _cfg_source_reset_pending and _cfg_source_unacked_reset_epoch is None
    epoch = RawCfgSourceEpoch(
        source_epoch_id=str(uuid4()),
        experiment_id=experiment_id,
        wire_vector=wire_vector,
        values=normalized_state,
        observed_at=timestamps,
        revisions=revisions,
        runtime_instance_id=RUNTIME_INSTANCE_ID,
        lease_generation=lease_generation,
        writer_generation=writer_generation,
        connection_generation=connection_generation,
        reset_detected=mark_reset,
        completed_at=max(timestamps.values()),
    )
    _cfg_source_epochs.append(epoch)
    _cfg_source_last_completed_at.clear()
    _cfg_source_last_completed_at.update(timestamps)
    _cfg_source_pending.clear()
    if mark_reset:
        # Pin the one-shot fault evidence outside the bounded ordinary deque so
        # a prolonged database outage cannot silently evict it. Subsequent
        # uptime regressions remain pending until this epoch is acknowledged.
        _cfg_source_unacked_reset_epoch = epoch
        _cfg_source_reset_pending = False
    return True


def component_cfg_source_epochs() -> tuple[RawCfgSourceEpoch, ...]:
    """Return immutable source-owned epochs for the adapter; never relabel."""
    buffered = tuple(_cfg_source_epochs)
    pinned = tuple(
        epoch
        for epoch in (_cfg_source_unacked_reset_epoch, _cfg_source_unacked_mismatch_epoch)
        if epoch is not None and not any(item.source_epoch_id == epoch.source_epoch_id for item in buffered)
    )
    return (*pinned, *buffered)


def request_component_state_replay(*, monotonic_clock: Callable[[], float] = time.monotonic) -> bool:
    """Request a read-only initial-state replay on the existing subscription.

    ``subscribe_states`` is intentionally never called here because each call
    installs another client callback.  The dependency is pinned to the audited
    API version whose authenticated connection accepts the empty protobuf
    request without reconnecting or invoking an entity command.
    """
    global _state_replay_identity, _state_replay_last_requested_monotonic
    identity = _cfg_source_identity
    if identity is None:
        return False
    _experiment_id, _lease_generation, _writer_generation, connection_generation, _revisions = identity
    if connection_generation != int(shared.transport_generation):
        raise ComponentStoreError("state replay transport generation is stale")
    if not shared.writer_lease_strictly_held():
        raise ComponentStoreError("state replay writer lease is not strictly held")
    client = shared.esp32.get("client")
    subscribed_client = shared.esp32.get("state_subscription_client")
    subscribed_generation = shared.esp32.get("state_subscription_generation")
    if client is None or subscribed_client is not client or subscribed_generation != connection_generation:
        raise ComponentStoreError("state replay has no current authenticated subscription")
    now = float(monotonic_clock())
    if not math.isfinite(now) or now < 0:
        raise ComponentStoreError("state replay monotonic clock is invalid")
    if _state_replay_identity != identity:
        _state_replay_identity = identity
        _state_replay_last_requested_monotonic = None
    if (
        _state_replay_last_requested_monotonic is not None
        and now - _state_replay_last_requested_monotonic < _STATE_REPLAY_MIN_INTERVAL_S
    ):
        return False
    try:
        client._get_connection().send_message(SubscribeStatesRequest())
    except Exception as exc:
        raise ComponentStoreError("state replay request failed") from exc
    _state_replay_last_requested_monotonic = now
    return True


def _consume_component_cfg_source_epoch(source_epoch_id: str) -> None:
    """Forget one raw epoch only after its bounded L3 call succeeded.

    A failed database call intentionally leaves the epoch available for the
    next scheduler tick.  Successful no-row calls are also consumed: they mean
    there was no open exposure, or the epoch was already used as confirmation
    evidence and predates the exposure boundary.
    """
    global _cfg_source_unacked_reset_epoch, _cfg_source_unacked_mismatch_epoch
    for index, epoch in enumerate(_cfg_source_epochs):
        if epoch.source_epoch_id == source_epoch_id:
            del _cfg_source_epochs[index]
            break
    if (
        _cfg_source_unacked_reset_epoch is not None
        and _cfg_source_unacked_reset_epoch.source_epoch_id == source_epoch_id
    ):
        _cfg_source_unacked_reset_epoch = None
    if (
        _cfg_source_unacked_mismatch_epoch is not None
        and _cfg_source_unacked_mismatch_epoch.source_epoch_id == source_epoch_id
    ):
        _cfg_source_unacked_mismatch_epoch = None


@dataclass(frozen=True)
class DeliveryBundle:
    bundle_id: str
    work_id: str
    purpose: BundlePurpose
    expected_state_content_sha256: str
    writer_generation: int
    connection_generation: int
    revision_bundle_sha256: str
    reserved_at: datetime
    finished_at: datetime | None = None
    component_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class BundleReservation:
    bundle: DeliveryBundle
    owned: bool


@dataclass(frozen=True)
class RuntimeFence:
    lease_generation: int
    writer_generation: int
    connection_generation: int
    writer_lease_held: bool
    device_write_enabled: bool


@dataclass(frozen=True)
class ConfirmationResult:
    confirmed: bool
    pending: bool
    reason: str
    confirmed_at: datetime | None = None


@dataclass(frozen=True)
class ExecutorResult:
    disposition: WorkDisposition
    reason: str
    work_id: str | None = None
    bundle_id: str | None = None
    setter_calls: int = 0


class ComponentExperimentStore(Protocol):
    """Narrow executor role boundary; implemented by migration-214 adapter."""

    async def prepare_runtime(
        self,
        experiment_id: str,
        *,
        device_id: str,
        connection_generation: int,
        writer_lease_held: bool,
        device_write_enabled: bool,
    ) -> RuntimeAuthority: ...

    async def claim_next(
        self,
        experiment_id: str,
        *,
        lease_generation: int,
        writer_generation: int,
        connection_generation: int,
    ) -> ResolvedWork | None: ...

    async def complete_preview(self, work: ResolvedWork) -> None: ...

    async def current_observation(self, work: ResolvedWork) -> ObservationEpoch | None: ...

    async def reserve_bundle(
        self,
        work: ResolvedWork,
        *,
        bundle_id: str,
        purpose: BundlePurpose,
        expected_state_content_sha256: str,
    ) -> BundleReservation: ...

    async def finish_bundle(
        self, work: ResolvedWork, bundle: DeliveryBundle, finished_at: datetime
    ) -> DeliveryBundle: ...

    async def record_component_outcomes(
        self,
        work: ResolvedWork,
        bundle: DeliveryBundle,
        outcomes: Sequence[ComponentCommandOutcome],
    ) -> None: ...

    async def observation_epochs(self, work: ResolvedWork, bundle: DeliveryBundle) -> Sequence[ObservationEpoch]: ...

    async def record_work_event(
        self,
        work: ResolvedWork,
        event_kind: Literal["claimed", "deferred", "completed", "failed", "recovered"],
        detail: Mapping[str, object],
    ) -> None: ...

    async def open_exposure(self, work: ResolvedWork, bundle: DeliveryBundle) -> str: ...

    async def close_exposure(self, work: ResolvedWork, reason: str) -> None: ...

    async def request_recovery(self, work: ResolvedWork, reason: str) -> str: ...

    async def record_runtime_snapshot(self, raw: RawCfgSourceEpoch, *, device_id: str) -> None: ...

    async def record_preexposure_mismatch(
        self,
        raw: RawCfgSourceEpoch,
        work: ResolvedWork,
        bundle: DeliveryBundle,
    ) -> RuntimeFaultReceipt: ...

    async def monitor_open_exposure(
        self,
        experiment_id: str,
        *,
        device_id: str,
        lease_generation: int,
    ) -> RuntimeExposureStatus | None: ...

    async def report_runtime_fault(
        self,
        experiment_id: str,
        *,
        device_id: str,
        fault_report_id: str,
        expected_lease_generation: int,
        runtime_instance_id: str,
        writer_generation: int,
        connection_generation: int,
        fault_kind: str,
        reason: str,
    ) -> RuntimeFaultReceipt: ...

    async def safe_startup_attestation(
        self,
        *,
        device_id: str,
        experiment_id: str | None,
    ) -> StartupAttestation: ...


class ComponentTransport(Protocol):
    async def deliver(
        self,
        calls: Sequence[ComponentBundleCall],
        *,
        on_state: ComponentStateCallback,
        expected_writer_generation: int,
        expected_connection_generation: int,
        work_deadline: datetime,
    ) -> ComponentBundleResult: ...


class Esp32ComponentTransport:
    """The only production transport: the existing sole-writer chokepoint."""

    async def deliver(
        self,
        calls: Sequence[ComponentBundleCall],
        *,
        on_state: ComponentStateCallback,
        expected_writer_generation: int,
        expected_connection_generation: int,
        work_deadline: datetime,
    ) -> ComponentBundleResult:
        return await push_component_bundle(
            calls,
            on_state=on_state,
            expected_writer_generation=expected_writer_generation,
            expected_connection_generation=expected_connection_generation,
            work_deadline=work_deadline,
        )


_RUNTIME_SQL = f"SELECT * FROM public.{L3_EXECUTOR_RUNTIME}($1::uuid, $2::text)"
_CLAIM_SQL = f"SELECT * FROM public.{L3_CLAIM_COMPONENT_WORK}($1::uuid, $2::text, $3::bigint, $4::text)"
_READ_BUNDLE_SQL = f"SELECT * FROM public.{L3_READ_DELIVERY_BUNDLE}($1::uuid, $2::uuid, $3::text, $4::text, $5::bigint)"
_BEGIN_BUNDLE_SQL = (
    f"SELECT (public.{L3_BEGIN_DELIVERY_BUNDLE}($1::uuid, $2::uuid, $3::uuid, $4::text, $5::text, $6::text)).*"
)
_FINISH_BUNDLE_SQL = (
    f"SELECT (public.{L3_RECORD_DELIVERY_BUNDLE}($1::uuid, $2::uuid, $3::uuid, $4::timestamptz, $5::text)).*"
)
_RECORD_OUTCOME_SQL = (
    f"SELECT public.{L3_RECORD_COMPONENT_OUTCOME}"
    "($1::uuid, $2::uuid, $3::uuid, $4::integer, $5::text, $6::text, "
    "$7::bigint, $8::bigint, $9::text)"
)
_RECORD_GENERATION_SQL = (
    f"SELECT * FROM public.{L3_RECORD_RUNTIME_GENERATION}($1::uuid, $2::text, $3::uuid, $4::bigint, $5::text)"
)
_RECORD_EPOCH_SQL = (
    f"SELECT (public.{L3_RECORD_OBSERVATION_EPOCH}"
    "($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5::bytea, $6::jsonb, "
    "$7::text, $8::text, $9::text, $10::text, $11::bigint, $12::bigint, $13::text)).*"
)
_READ_OBSERVATIONS_SQL = (
    f"SELECT * FROM public.{L3_READ_OBSERVATION_EPOCHS}($1::uuid, $2::uuid, $3::uuid, $4::text, $5::bigint)"
)
_RECORD_EVENT_SQL = f"SELECT public.{L3_RECORD_WORK_EVENT}($1::uuid, $2::uuid, $3::text, $4::jsonb, $5::text)"
_OPEN_EXPOSURE_SQL = f"SELECT public.{L3_OPEN_EXPOSURE}($1::uuid, $2::uuid, $3::text, $4::text)"
_CLOSE_EXPOSURE_SQL = f"SELECT (public.{L3_CLOSE_EXPOSURE}($1::uuid, $2::text, $3::text)).*"
_REQUEST_RECOVERY_SQL = (
    f"SELECT public.{L3_REQUEST_RECOVERY}"
    "($1::uuid, $2::uuid, tstzrange($3::timestamptz, $4::timestamptz, '[)'), "
    "$5::timestamptz, $6::text, $7::text)"
)
_RECORD_RUNTIME_SNAPSHOT_SQL = (
    f"SELECT * FROM public.{L3_RECORD_RUNTIME_SNAPSHOT}"
    "($1::uuid, $2::text, $3::uuid, $4::bytea, $5::jsonb, $6::text, $7::text, "
    "$8::text, $9::text, $10::uuid, $11::bigint, $12::bigint, $13::boolean, $14::text)"
)
_RECORD_PREEXPOSURE_MISMATCH_SQL = (
    f"SELECT (public.{L3_RECORD_PREEXPOSURE_MISMATCH}"
    "($1::uuid, $2::uuid, $3::uuid, $4::text, $5::uuid, $6::bytea, $7::jsonb, "
    "$8::text, $9::text, $10::text, $11::text, $12::uuid, $13::bigint, "
    "$14::bigint, $15::bigint, $16::text)).*"
)
_MONITOR_OPEN_EXPOSURE_SQL = f"SELECT * FROM public.{L3_MONITOR_OPEN_EXPOSURE}($1::uuid, $2::text, $3::bigint)"
_REPORT_RUNTIME_FAULT_SQL = (
    f"SELECT (public.{L3_REPORT_RUNTIME_FAULT}"
    "($1::uuid, $2::text, $3::uuid, $4::bigint, $5::uuid, $6::bigint, "
    "$7::bigint, $8::text, $9::text, $10::text)).*"
)
_SAFE_STARTUP_ATTESTATION_SQL = f"SELECT * FROM public.{L3_SAFE_STARTUP_ATTESTATION}($1::text, $2::uuid)"

_WIRE_NAME_BY_ID = {definition.wire_id: name for name, definition in REGISTRY.items() if definition.wire_id is not None}
_MISSING = object()


def _row_value(row: Mapping[str, object], name: str, default: object = _MISSING) -> object:
    try:
        return row[name]
    except (KeyError, TypeError):
        if default is _MISSING:
            raise ComponentStoreError(f"migration-214 row missing required column {name}") from None
        return default


def _row_text(row: Mapping[str, object], name: str, default: str | None = None) -> str | None:
    value = _row_value(row, name, default)
    return None if value is None else str(value)


def _range_bounds(value: object) -> tuple[datetime, datetime]:
    lower = getattr(value, "lower", None)
    upper = getattr(value, "upper", None)
    if not isinstance(lower, datetime) or not isinstance(upper, datetime):
        raise ComponentStoreError("migration-214 valid_range is not a bounded tstzrange")
    return _aware(lower, "valid_from"), _aware(upper, "valid_until")


def _revision_from_row(row: Mapping[str, object]) -> RevisionSet:
    return RevisionSet(
        bundle_sha256=str(_row_value(row, "revision_bundle_sha256")),
        firmware_revision=str(_row_value(row, "firmware_revision")),
        config_revision=str(_row_value(row, "config_revision")),
        registry_revision=str(_row_value(row, "registry_revision")),
        grid_revision=str(_row_value(row, "grid_revision")),
    )


def _wire_state(value: object, field_name: str) -> dict[str, ComponentValue]:
    try:
        decoded = decode_policy_vector(bytes(value))
        return normalize_complete_state(decoded)
    except (TypeError, ValueError, ComponentContractError) as exc:
        raise ComponentStoreError(f"invalid {field_name} from migration-214 resolver") from exc


def _json_value(value: object) -> object:
    if isinstance(value, str):
        return json.loads(value)
    return value


def _observation_timestamps(value: object) -> dict[str, datetime]:
    decoded = _json_value(value)
    if not isinstance(decoded, list) or len(decoded) != 48:
        raise ComponentStoreError("observation window must contain exactly 48 timestamp records")
    timestamps: dict[str, datetime] = {}
    for item in decoded:
        if not isinstance(item, Mapping) or set(item) != {"wire_id", "observed_at"}:
            raise ComponentStoreError("observation timestamp record is not canonical")
        try:
            wire_id = int(item["wire_id"])
            field_name = _WIRE_NAME_BY_ID[wire_id]
            moment_value = item["observed_at"]
            moment = (
                moment_value
                if isinstance(moment_value, datetime)
                else datetime.fromisoformat(str(moment_value).replace("Z", "+00:00"))
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ComponentStoreError("observation timestamp record is invalid") from exc
        if field_name in timestamps:
            raise ComponentStoreError("observation timestamp record duplicates a wire")
        timestamps[field_name] = _aware(moment, "observed_at")
    if tuple(sorted(timestamps, key=lambda name: REGISTRY[name].wire_id or 0)) != CANONICAL_FIELD_ORDER:
        raise ComponentStoreError("observation timestamp wire set is incomplete")
    return timestamps


def _bundle_component_fields(value: object) -> tuple[str, ...]:
    """Decode the immutable requested-wire journal for a delivery bundle."""
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ComponentStoreError("delivery bundle component_wire_ids is not an array")
    fields: list[str] = []
    try:
        for raw_wire_id in value:
            wire_id = int(raw_wire_id)
            if isinstance(raw_wire_id, bool) or wire_id != raw_wire_id:
                raise ValueError
            fields.append(_WIRE_NAME_BY_ID[wire_id])
    except (KeyError, TypeError, ValueError) as exc:
        raise ComponentStoreError("delivery bundle component wire id is invalid") from exc
    if len(fields) != len(set(fields)):
        raise ComponentStoreError("delivery bundle component wire ids contain a duplicate")
    return tuple(fields)


def _source_observation_epoch(raw: RawCfgSourceEpoch, work: ResolvedWork) -> ObservationEpoch:
    return ObservationEpoch(
        source_epoch_id=raw.source_epoch_id,
        experiment_id=raw.experiment_id,
        work_id="00000000-0000-4000-8000-000000000000",
        bundle_id="00000000-0000-4000-8000-000000000000",
        execution_phase=work.execution_phase,
        operation_kind=work.operation_kind,
        identity_source="derived_cfg_readbacks_v1",
        state_content_sha256="0" * 64,
        observations={
            field_name: ObservedComponent(raw.values[field_name], raw.observed_at[field_name])
            for field_name in CANONICAL_FIELD_ORDER
        },
        persisted_at=raw.completed_at,
        revisions=raw.revisions,
        runtime_instance_id=raw.runtime_instance_id,
        writer_generation=raw.writer_generation,
        connection_generation=raw.connection_generation,
    )


def _canonical_raw_observations(raw: RawCfgSourceEpoch) -> list[dict[str, object]]:
    return [
        {
            "wire_id": REGISTRY[field_name].wire_id,
            "observed_at": raw.observed_at[field_name].astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        }
        for field_name in CANONICAL_FIELD_ORDER
    ]


def _optional_int(row: Mapping[str, object], field_name: str) -> int | None:
    value = _row_value(row, field_name, None)
    return None if value is None else int(value)


def _required_bool(row: Mapping[str, object], field_name: str) -> bool:
    value = _row_value(row, field_name)
    if type(value) is not bool:
        raise ComponentStoreError(f"migration-214 row column {field_name} is not boolean")
    return value


def _mapping_bool(values: Mapping[str, object], field_name: str, default: bool) -> bool:
    value = _row_value(values, field_name, default)
    if type(value) is not bool:
        raise ComponentStoreError(f"migration-214 signal {field_name} is not boolean")
    return value


def _optional_datetime(row: Mapping[str, object], field_name: str) -> datetime | None:
    value = _row_value(row, field_name, None)
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise ComponentStoreError(f"migration-214 row column {field_name} is not timestamptz")
    return _aware(value, field_name)


def _runtime_exposure_status(row: Mapping[str, object]) -> RuntimeExposureStatus:
    exposure_started_at = _optional_datetime(row, "exposure_started_at")
    resolved_at = _optional_datetime(row, "resolved_at")
    if exposure_started_at is None or resolved_at is None:
        raise ComponentStoreError("migration-214 exposure monitor lacks required timestamps")
    return RuntimeExposureStatus(
        exposure_id=str(_row_value(row, "exposure_id")),
        work_id=str(_row_value(row, "work_id")),
        exposure_is_open=_required_bool(row, "exposure_is_open"),
        close_reason=_row_text(row, "close_reason"),
        recovery_work_id=_row_text(row, "recovery_work_id"),
        current_runtime_instance_id=_row_text(row, "current_runtime_instance_id"),
        current_writer_generation=_optional_int(row, "current_writer_generation"),
        current_connection_generation=_optional_int(row, "current_connection_generation"),
        source_epoch_id=_row_text(row, "source_epoch_id"),
        source_runtime_instance_id=_row_text(row, "source_runtime_instance_id"),
        source_writer_generation=_optional_int(row, "source_writer_generation"),
        source_connection_generation=_optional_int(row, "source_connection_generation"),
        common_field_drift=_required_bool(row, "common_field_drift"),
        cfg_drift=_required_bool(row, "cfg_drift"),
        lineage_drift=_required_bool(row, "lineage_drift"),
        reset_detected=_required_bool(row, "reset_detected"),
        foreign_writer=_required_bool(row, "foreign_writer"),
        exposure_started_at=exposure_started_at,
        last_observed_at=_optional_datetime(row, "last_observed_at"),
        resolved_at=resolved_at,
    )


def _runtime_fault_receipt(row: Mapping[str, object]) -> RuntimeFaultReceipt:
    recorded_at = _optional_datetime(row, "recorded_at")
    if recorded_at is None:
        raise ComponentStoreError("migration-214 runtime fault receipt lacks recorded_at")
    return RuntimeFaultReceipt(
        fault_report_id=str(_row_value(row, "fault_report_id")),
        close_reason=str(_row_value(row, "close_reason")),
        recovery_work_id=_row_text(row, "recovery_work_id"),
        admission_state_after=str(_row_value(row, "admission_state_after")),
        authority_hold_required=_required_bool(row, "authority_hold_required"),
        facility_authority_yielded=_required_bool(row, "facility_authority_yielded"),
        recorded_at=recorded_at,
    )


def _startup_attestation(row: Mapping[str, object]) -> StartupAttestation:
    attested_at = _optional_datetime(row, "attested_at")
    if attested_at is None:
        raise ComponentStoreError("migration-214 startup attestation lacks attested_at")
    return StartupAttestation(
        device_id=str(_row_value(row, "device_id")),
        requested_experiment_id=_row_text(row, "requested_experiment_id"),
        scoped_experiment_id=_row_text(row, "scoped_experiment_id"),
        scope_resolved=_required_bool(row, "scope_resolved"),
        current_lease_generation=_optional_int(row, "current_lease_generation"),
        active_experiment_count=int(_row_value(row, "active_experiment_count")),
        open_exposure_count=int(_row_value(row, "open_exposure_count")),
        recovery_pending_count=int(_row_value(row, "recovery_pending_count")),
        experiment_authority_active=_required_bool(row, "experiment_authority_active"),
        facility_authority_yielded=_required_bool(row, "facility_authority_yielded"),
        hold_required=_required_bool(row, "hold_required"),
        attestation_reason=str(_row_value(row, "attestation_reason")),
        attested_at=attested_at,
    )


_CLOSE_REASON_MAP = {
    "device_reboot": "reboot",
    "device_reset": "protocol_deviation",
    "device_reset_detected": "reboot",
    "device_reconnect": "reconnect",
    "foreign_writer": "writer_collision",
    "facility_rescue": "manual_rescue",
    "common_field_drift": "common_field_drift",
    "writer_lease_not_held": "lease_loss",
    "connection_generation_changed": "reconnect",
    "current_observation_uncertain": "db_outage",
    "observation_read_uncertain": "db_outage",
    "confirmation_persistence_uncertain": "db_outage",
    "interrupted_bundle_outcome_unknown": "unknown_delivery",
    "delivery_outcome_unknown": "unknown_delivery",
}


def _runtime_fault_key(experiment_id: str, device_id: str) -> tuple[str, str]:
    return experiment_id, device_id


def _reporter_from_authority(
    experiment_id: str,
    device_id: str,
    authority: RuntimeAuthority,
) -> RuntimeReporterIdentity | None:
    if (
        authority.runtime_instance_id != RUNTIME_INSTANCE_ID
        or authority.connection_generation is None
        or authority.writer_generation < 0
    ):
        return None
    return RuntimeReporterIdentity(
        experiment_id=experiment_id,
        device_id=device_id,
        expected_lease_generation=authority.lease_generation,
        runtime_instance_id=RUNTIME_INSTANCE_ID,
        writer_generation=authority.writer_generation,
        connection_generation=authority.connection_generation,
    )


def _remember_runtime_reporter(reporter: RuntimeReporterIdentity | None) -> None:
    if reporter is not None:
        _runtime_reporters[_runtime_fault_key(reporter.experiment_id, reporter.device_id)] = reporter


def _queue_runtime_fault(
    experiment_id: str,
    device_id: str,
    *,
    fault_kind: str,
    reason: str,
    reporter: RuntimeReporterIdentity | None = None,
) -> PendingRuntimeFault:
    key = _runtime_fault_key(experiment_id, device_id)
    existing = _pending_runtime_faults.get(key)
    if existing is not None:
        if existing.reporter is None and reporter is not None:
            existing = replace(existing, reporter=reporter)
            _pending_runtime_faults[key] = existing
        return existing
    pending = PendingRuntimeFault(
        fault_report_id=str(uuid4()),
        fault_kind=fault_kind,
        reason=reason,
        reporter=reporter or _runtime_reporters.get(key),
    )
    _pending_runtime_faults[key] = pending
    return pending


async def _persist_pending_runtime_fault(
    store: ComponentExperimentStore,
    experiment_id: str,
    device_id: str,
    *,
    reporter: RuntimeReporterIdentity | None = None,
) -> RuntimeFaultReceipt | None:
    key = _runtime_fault_key(experiment_id, device_id)
    pending = _pending_runtime_faults.get(key)
    if pending is None:
        return None
    if pending.reporter is None and reporter is not None:
        pending = replace(pending, reporter=reporter)
        _pending_runtime_faults[key] = pending
    if pending.reporter is None:
        raise ComponentStoreError("runtime fault has no registered reporter identity")
    identity = pending.reporter
    receipt = await store.report_runtime_fault(
        identity.experiment_id,
        device_id=identity.device_id,
        fault_report_id=pending.fault_report_id,
        expected_lease_generation=identity.expected_lease_generation,
        runtime_instance_id=identity.runtime_instance_id,
        writer_generation=identity.writer_generation,
        connection_generation=identity.connection_generation,
        fault_kind=pending.fault_kind,
        reason=pending.reason,
    )
    if receipt.facility_authority_yielded and receipt.authority_hold_required:
        raise ComponentStoreError("runtime fault receipt has conflicting authority outputs")
    del _pending_runtime_faults[key]
    set_component_authority_hold(receipt.authority_hold_required, CANONICAL_FIELD_ORDER)
    if receipt.facility_authority_yielded:
        configure_component_cfg_source(
            experiment_id=None,
            lease_generation=None,
            writer_generation=None,
            connection_generation=None,
            revisions=None,
        )
    return receipt


class AsyncpgComponentExperimentStore:
    """Concrete function-only migration-214 executor adapter.

    It never selects a base experiment, work, state, mapping, schedule, or
    receipt table.  Candidate discovery/claim, frozen vectors, observation
    windows, and every mutation cross a named SECURITY DEFINER function.
    """

    def __init__(self, pool: asyncpg.Pool | object) -> None:
        self.pool = pool
        self.device_id: str | None = None
        self.runtime_row: Mapping[str, object] | None = None

    async def _fetchrow(self, query: str, *args: object) -> Mapping[str, object] | None:
        async with self.pool.acquire() as connection:
            return await connection.fetchrow(query, *args)

    async def _fetch(self, query: str, *args: object) -> Sequence[Mapping[str, object]]:
        async with self.pool.acquire() as connection:
            return await connection.fetch(query, *args)

    async def _fetchval(self, query: str, *args: object) -> object:
        async with self.pool.acquire() as connection:
            return await connection.fetchval(query, *args)

    async def prepare_runtime(
        self,
        experiment_id: str,
        *,
        device_id: str,
        connection_generation: int,
        writer_lease_held: bool,
        device_write_enabled: bool,
    ) -> RuntimeAuthority:
        registration: Mapping[str, object] | None = None
        if writer_lease_held and connection_generation == int(shared.transport_generation):
            registration = await self._fetchrow(
                _RECORD_GENERATION_SQL,
                experiment_id,
                device_id,
                RUNTIME_INSTANCE_ID,
                connection_generation,
                COMPONENT_EXECUTOR_ACTOR,
            )
            if registration is None:
                raise ComponentStoreError("migration-214 runtime registration returned no row")
        row = await self._fetchrow(_RUNTIME_SQL, experiment_id, device_id)
        if row is None:
            raise ComponentStoreError("migration-214 runtime context unavailable")
        if registration is not None and (
            str(_row_value(row, "runtime_instance_id")) != RUNTIME_INSTANCE_ID
            or int(_row_value(row, "writer_generation")) != int(_row_value(registration, "writer_generation"))
            or int(_row_value(row, "connection_generation")) != connection_generation
        ):
            raise ComponentStoreError("migration-214 runtime registration readback mismatch")
        writer_value = _row_value(row, "writer_generation", None)
        authority = RuntimeAuthority(
            lease_generation=int(_row_value(row, "lease_generation")),
            writer_generation=-1 if writer_value is None else int(writer_value),
            device_id=str(_row_value(row, "device_id")),
            component_authority_required=_required_bool(row, "authority_hold_required"),
            observation_source_required=_required_bool(row, "observation_source_required"),
            rescue_authorized=_required_bool(row, "rescue_authorized"),
            revisions=_revision_from_row(row),
            runtime_instance_id=_row_text(row, "runtime_instance_id"),
            connection_generation=_optional_int(row, "connection_generation"),
        )
        if authority.device_id != device_id:
            raise ComponentStoreError("migration-214 runtime device mismatch")
        self.device_id = device_id
        self.runtime_row = row
        return authority

    async def claim_next(
        self,
        experiment_id: str,
        *,
        lease_generation: int,
        writer_generation: int,
        connection_generation: int,
    ) -> ResolvedWork | None:
        if self.device_id is None or self.runtime_row is None:
            raise ComponentStoreError("runtime context must be prepared before claim")
        row = await self._fetchrow(
            _CLAIM_SQL,
            experiment_id,
            self.device_id,
            lease_generation,
            COMPONENT_EXECUTOR_ACTOR,
        )
        if row is None:
            return None
        valid_from, valid_until = _range_bounds(_row_value(row, "valid_range"))
        resolved_at_value = _row_value(row, "resolved_at")
        work_id = str(_row_value(row, "work_id"))
        operation_kind = str(_row_value(row, "operation_kind"))
        assignment = _row_value(row, "assignment_id", None)
        returned_connection = int(_row_value(row, "connection_generation"))
        if returned_connection != connection_generation:
            raise ComponentStoreError("migration-214 candidate connection generation mismatch")
        returned_writer = int(_row_value(row, "writer_generation"))
        if returned_writer != writer_generation:
            raise ComponentStoreError("migration-214 candidate writer generation mismatch")
        revisions = _revision_from_row(row)
        signals_value = _json_value(_row_value(row, "executor_signals", {}))
        if not isinstance(signals_value, Mapping):
            raise ComponentStoreError("migration-214 executor_signals must be an object")
        historical_restart = _required_bool(row, "restart_detected")
        historical_reconnect = _required_bool(row, "reconnect_detected")
        generation_recovery_cleared = _mapping_bool(
            signals_value,
            "generation_recovery_cleared",
            not (historical_restart or historical_reconnect),
        )
        same_generation_nonbaseline_reentry = _mapping_bool(
            signals_value,
            "same_generation_nonbaseline_reentry_forbidden",
            False,
        )
        historical_reset = _mapping_bool(
            signals_value,
            "historical_reset_detected",
            _mapping_bool(signals_value, "reset_detected", False),
        )
        historical_foreign_writer = _mapping_bool(
            signals_value,
            "historical_foreign_writer",
            _mapping_bool(signals_value, "foreign_writer", False),
        )
        snapshot_recovery_cleared = _mapping_bool(
            signals_value,
            "snapshot_recovery_cleared",
            not (historical_reset or historical_foreign_writer),
        )
        no_reentry = _required_bool(row, "no_reentry")
        if same_generation_nonbaseline_reentry and not no_reentry:
            raise ComponentStoreError("migration-214 lost the durable nonbaseline re-entry fence")
        return ResolvedWork(
            experiment_id=str(_row_value(row, "experiment_id", experiment_id)),
            work_id=work_id,
            assignment_id=None if assignment is None else str(assignment),
            operation_kind=operation_kind,
            execution_phase=str(_row_value(row, "execution_phase")),
            admission_state=str(_row_value(row, "admission_state")),
            lifecycle_status=str(_row_value(row, "lifecycle_status")),
            protocol_version=int(_row_value(self.runtime_row, "protocol_version")),
            transport_kind=str(_row_value(self.runtime_row, "transport_kind")),
            target_profile=str(_row_value(row, "target_profile")),
            target_state_content_sha256=str(_row_value(row, "target_state_content_sha256")),
            baseline_state_content_sha256=str(_row_value(row, "baseline_state_content_sha256")),
            target_state=_wire_state(_row_value(row, "target_wire_vector"), "target_wire_vector"),
            baseline_state=_wire_state(_row_value(row, "baseline_wire_vector"), "baseline_wire_vector"),
            revisions=revisions,
            expected_revision_bundle_sha256=str(_row_value(row, "revision_bundle_sha256")),
            lease_generation=int(_row_value(row, "lease_generation")),
            runtime_instance_id=str(_row_value(row, "runtime_instance_id")),
            writer_generation=returned_writer,
            connection_generation=returned_connection,
            valid_from=valid_from,
            valid_until=valid_until,
            expires_at=_aware(
                _row_value(row, "work_expires_at"),
                "expires_at",
            ),
            claim_expires_at=_aware(_row_value(row, "claim_expires_at"), "claim_expires_at"),
            resolved_at=_aware(resolved_at_value, "resolved_at"),
            device_id=str(_row_value(row, "device_id")),
            baseline_interposition_confirmed=bool(_row_value(row, "baseline_confirmed")),
            signals=WorkSignals(
                rebooted=_mapping_bool(
                    signals_value,
                    "effective_restart_detected",
                    historical_restart,
                ),
                reset_detected=_mapping_bool(signals_value, "reset_detected", historical_reset),
                reconnected=_mapping_bool(
                    signals_value,
                    "effective_reconnect_detected",
                    historical_reconnect,
                ),
                foreign_writer=_mapping_bool(
                    signals_value,
                    "foreign_writer",
                    historical_foreign_writer,
                ),
                facility_rescue_active=bool(_row_value(signals_value, "facility_rescue_active", False)),
                facility_recovery_authorized=bool(_row_value(row, "rescue_authorized")),
                nonbaseline_reentry_forbidden=no_reentry,
                generation_recovery_cleared=generation_recovery_cleared,
                snapshot_recovery_cleared=snapshot_recovery_cleared,
                same_generation_nonbaseline_reentry_forbidden=same_generation_nonbaseline_reentry,
            ),
            open_exposure_id=_row_text(row, "open_exposure_id"),
        )

    async def complete_preview(self, work: ResolvedWork) -> None:
        await self.record_work_event(work, "completed", {"physical_setter_calls": 0})

    async def current_observation(self, work: ResolvedWork) -> ObservationEpoch | None:
        for raw in reversed(component_cfg_source_epochs()):
            if (
                raw.experiment_id == work.experiment_id
                and raw.revisions == work.revisions
                and raw.runtime_instance_id == work.runtime_instance_id
                and raw.writer_generation == work.writer_generation
                and raw.connection_generation == work.connection_generation
            ):
                return _source_observation_epoch(raw, work)
        return None

    async def _read_bundle(
        self,
        work: ResolvedWork,
        purpose: BundlePurpose,
    ) -> Mapping[str, object] | None:
        return await self._fetchrow(
            _READ_BUNDLE_SQL,
            work.experiment_id,
            work.work_id,
            work.device_id,
            purpose,
            work.lease_generation,
        )

    async def reserve_bundle(
        self,
        work: ResolvedWork,
        *,
        bundle_id: str,
        purpose: BundlePurpose,
        expected_state_content_sha256: str,
    ) -> BundleReservation:
        existing = await self._read_bundle(work, purpose)
        row = await self._fetchrow(
            _BEGIN_BUNDLE_SQL,
            work.experiment_id,
            work.work_id,
            bundle_id,
            work.device_id,
            purpose,
            COMPONENT_EXECUTOR_ACTOR,
        )
        if row is None:
            raise ComponentStoreError("migration-214 bundle begin returned no row")
        canonical_id = str(_row_value(row, "bundle_id"))
        current = await self._read_bundle(work, purpose)
        if current is None or str(_row_value(current, "bundle_id")) != canonical_id:
            raise ComponentStoreError("migration-214 canonical bundle readback mismatch")
        finished = _row_value(current, "bundle_finished_at", None)
        bundle = DeliveryBundle(
            bundle_id=canonical_id,
            work_id=work.work_id,
            purpose=purpose,
            expected_state_content_sha256=expected_state_content_sha256,
            writer_generation=work.writer_generation,
            connection_generation=work.connection_generation,
            revision_bundle_sha256=work.revisions.bundle_sha256,
            reserved_at=_aware(_row_value(current, "started_at"), "bundle_started_at"),
            finished_at=None if finished is None else _aware(finished, "bundle_finished_at"),
            component_fields=_bundle_component_fields(_row_value(current, "component_wire_ids", ())),
        )
        owned = existing is None and canonical_id == bundle_id
        return BundleReservation(bundle, owned)

    async def finish_bundle(
        self,
        work: ResolvedWork,
        bundle: DeliveryBundle,
        finished_at: datetime,
    ) -> DeliveryBundle:
        row = await self._fetchrow(
            _FINISH_BUNDLE_SQL,
            work.experiment_id,
            work.work_id,
            bundle.bundle_id,
            finished_at,
            COMPONENT_EXECUTOR_ACTOR,
        )
        if row is None:
            raise ComponentStoreError("migration-214 bundle completion returned no row")
        persisted_finish = _aware(_row_value(row, "bundle_finished_at"), "bundle_finished_at")
        if persisted_finish != _aware(finished_at, "finished_at"):
            raise ComponentStoreError("migration-214 changed immutable bundle finish time")
        return replace(bundle, finished_at=persisted_finish)

    async def record_component_outcomes(
        self,
        work: ResolvedWork,
        bundle: DeliveryBundle,
        outcomes: Sequence[ComponentCommandOutcome],
    ) -> None:
        for outcome in outcomes:
            definition = REGISTRY.get(outcome.parameter)
            if definition is None or definition.wire_id is None:
                raise ComponentStoreError("component outcome has no canonical wire id")
            if (
                outcome.writer_generation != work.writer_generation
                or outcome.connection_generation != work.connection_generation
            ):
                raise ComponentStoreError("component outcome generation mismatch")
            await self._fetchval(
                _RECORD_OUTCOME_SQL,
                work.experiment_id,
                work.work_id,
                bundle.bundle_id,
                definition.wire_id,
                outcome.status,
                outcome.reason or None,
                outcome.writer_generation,
                outcome.connection_generation,
                COMPONENT_EXECUTOR_ACTOR,
            )

    async def _persist_source_epochs(self, work: ResolvedWork, bundle: DeliveryBundle) -> None:
        global _cfg_source_unacked_mismatch_epoch
        if bundle.finished_at is None:
            return
        expected = work.baseline_state if work.operation_kind == WORK_KIND_RECOVERY else work.target_state
        for raw in component_cfg_source_epochs():
            if (
                raw.experiment_id != work.experiment_id
                or raw.revisions != work.revisions
                or raw.runtime_instance_id != work.runtime_instance_id
                or raw.lease_generation != work.lease_generation
                or raw.writer_generation != work.writer_generation
                or raw.connection_generation != work.connection_generation
            ):
                continue
            if raw.reset_detected:
                # A reset can arrive after the physical prefix but before an
                # exposure exists. Persist it through L3's source-keyed fault
                # path and never let that same epoch qualify as confirmation.
                await self.record_runtime_snapshot(raw, device_id=work.device_id)
                _consume_component_cfg_source_epoch(raw.source_epoch_id)
                raise ComponentRuntimeFault("device_reset_detected")
            if min(raw.observed_at.values()) <= bundle.finished_at:
                continue
            if raw.values != expected:
                # A complete current-lineage epoch after a completed physical
                # bundle is negative evidence, not an ignorable non-receipt.
                # One L3 transaction persists the raw all-48 epoch, faults the
                # source work, and creates/retains bounded baseline recovery.
                if _cfg_source_unacked_mismatch_epoch is None:
                    # Pin negative evidence outside the bounded normal deque
                    # until the atomic L3 call acknowledges it. A prolonged DB
                    # outage must not let later cfg cycles evict the fault.
                    _cfg_source_unacked_mismatch_epoch = raw
                receipt = await self.record_preexposure_mismatch(raw, work, bundle)
                _consume_component_cfg_source_epoch(raw.source_epoch_id)
                set_component_authority_hold(receipt.authority_hold_required, CANONICAL_FIELD_ORDER)
                if receipt.facility_authority_yielded:
                    configure_component_cfg_source(
                        experiment_id=None,
                        lease_generation=None,
                        writer_generation=None,
                        connection_generation=None,
                        revisions=None,
                    )
                raise ComponentRuntimeFault(
                    "post_delivery_observation_mismatch",
                    authority_hold_required=receipt.authority_hold_required,
                    facility_authority_yielded=receipt.facility_authority_yielded,
                )
            observations = [
                {
                    "wire_id": REGISTRY[field_name].wire_id,
                    "observed_at": raw.observed_at[field_name].astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                }
                for field_name in CANONICAL_FIELD_ORDER
            ]
            await self._fetchrow(
                _RECORD_EPOCH_SQL,
                work.experiment_id,
                work.work_id,
                bundle.bundle_id,
                raw.source_epoch_id,
                raw.wire_vector,
                json.dumps(observations, separators=(",", ":")),
                raw.revisions.firmware_revision,
                raw.revisions.config_revision,
                raw.revisions.registry_revision,
                raw.revisions.grid_revision,
                raw.writer_generation,
                raw.connection_generation,
                COMPONENT_EXECUTOR_ACTOR,
            )

    async def observation_epochs(
        self,
        work: ResolvedWork,
        bundle: DeliveryBundle,
    ) -> Sequence[ObservationEpoch]:
        await self._persist_source_epochs(work, bundle)
        rows = await self._fetch(
            _READ_OBSERVATIONS_SQL,
            work.experiment_id,
            work.work_id,
            bundle.bundle_id,
            work.device_id,
            work.lease_generation,
        )
        result: list[ObservationEpoch] = []
        for row in rows:
            # The least-information window also carries one separately labelled
            # current marker for pre-delivery inspection.  Exposure proof may
            # use only this bundle's immutable post-delivery rows; never let a
            # newest global/current row displace one of the two receipts.
            if str(_row_value(row, "window_kind")) != "post_delivery":
                continue
            if not bool(_row_value(row, "is_current_generation")) or not bool(_row_value(row, "is_fresh")):
                raise ComponentStoreError("migration-214 observation window is stale")
            values = _wire_state(_row_value(row, "wire_vector"), "observation_wire_vector")
            timestamps = _observation_timestamps(_row_value(row, "observations"))
            result.append(
                ObservationEpoch(
                    source_epoch_id=str(_row_value(row, "source_epoch_id")),
                    experiment_id=str(_row_value(row, "experiment_id", work.experiment_id)),
                    work_id=str(_row_value(row, "work_id", work.work_id)),
                    bundle_id=str(_row_value(row, "bundle_id", bundle.bundle_id)),
                    execution_phase=str(_row_value(row, "execution_phase", work.execution_phase)),
                    operation_kind=str(_row_value(row, "operation_kind", work.operation_kind)),
                    identity_source=str(_row_value(row, "identity_source", "derived_cfg_readbacks_v1")),
                    state_content_sha256=str(_row_value(row, "policy_state_content_sha256")),
                    observations={
                        field_name: ObservedComponent(values[field_name], timestamps[field_name])
                        for field_name in CANONICAL_FIELD_ORDER
                    },
                    persisted_at=_aware(_row_value(row, "persisted_at"), "persisted_at"),
                    revisions=RevisionSet(
                        bundle_sha256=work.revisions.bundle_sha256,
                        firmware_revision=str(_row_value(row, "firmware_revision")),
                        config_revision=str(_row_value(row, "config_revision")),
                        registry_revision=str(_row_value(row, "registry_revision")),
                        grid_revision=str(_row_value(row, "grid_revision")),
                    ),
                    runtime_instance_id=str(_row_value(row, "runtime_instance_id")),
                    writer_generation=int(_row_value(row, "writer_generation")),
                    connection_generation=int(_row_value(row, "connection_generation")),
                )
            )
        return result

    async def record_work_event(
        self,
        work: ResolvedWork,
        event_kind: Literal["claimed", "deferred", "completed", "failed", "recovered"],
        detail: Mapping[str, object],
    ) -> None:
        await self._fetchval(
            _RECORD_EVENT_SQL,
            work.experiment_id,
            work.work_id,
            event_kind,
            json.dumps(dict(detail), sort_keys=True, default=str, separators=(",", ":")),
            COMPONENT_EXECUTOR_ACTOR,
        )

    async def open_exposure(self, work: ResolvedWork, bundle: DeliveryBundle) -> str:
        exposure_id = await self._fetchval(
            _OPEN_EXPOSURE_SQL,
            work.experiment_id,
            work.work_id,
            work.device_id,
            COMPONENT_EXECUTOR_ACTOR,
        )
        if exposure_id is None:
            raise ComponentStoreError("migration-214 exposure open returned no id")
        return str(exposure_id)

    async def close_exposure(self, work: ResolvedWork, reason: str) -> None:
        if work.open_exposure_id is None:
            return
        close_reason = _CLOSE_REASON_MAP.get(reason)
        if close_reason is None:
            if "stale" in reason or "mismatch" in reason or "expired" in reason:
                close_reason = "stale_or_mismatched_work"
            elif "observation" in reason or "cfg" in reason:
                close_reason = "cfg_drift"
            else:
                close_reason = "protocol_deviation"
        await self._fetchrow(
            _CLOSE_EXPOSURE_SQL,
            work.open_exposure_id,
            close_reason,
            COMPONENT_EXECUTOR_ACTOR,
        )

    async def request_recovery(self, work: ResolvedWork, reason: str) -> str:
        # Migration 214 deliberately distinguishes linked nonbaseline recovery
        # from root/initial baseline recovery.  A baseline source row cannot be
        # its own parent, so enrollment/reset of baseline work uses NULL.
        source_work_id = (
            None if work.target_profile == "baseline" or work.operation_kind == WORK_KIND_RECOVERY else work.work_id
        )
        recovery_id = await self._fetchval(
            _REQUEST_RECOVERY_SQL,
            work.experiment_id,
            source_work_id,
            work.valid_from,
            work.valid_until,
            work.expires_at,
            reason,
            COMPONENT_EXECUTOR_ACTOR,
        )
        if recovery_id is None:
            raise ComponentStoreError("migration-214 recovery request returned no id")
        return str(recovery_id)

    async def record_runtime_snapshot(self, raw: RawCfgSourceEpoch, *, device_id: str) -> None:
        if self.device_id != device_id or raw.runtime_instance_id != RUNTIME_INSTANCE_ID:
            raise ComponentStoreError("runtime snapshot device/instance is not the prepared executor")
        await self._fetchrow(
            _RECORD_RUNTIME_SNAPSHOT_SQL,
            raw.experiment_id,
            device_id,
            raw.source_epoch_id,
            raw.wire_vector,
            json.dumps(_canonical_raw_observations(raw), separators=(",", ":")),
            raw.revisions.firmware_revision,
            raw.revisions.config_revision,
            raw.revisions.registry_revision,
            raw.revisions.grid_revision,
            raw.runtime_instance_id,
            raw.writer_generation,
            raw.connection_generation,
            raw.reset_detected,
            COMPONENT_EXECUTOR_ACTOR,
        )

    async def record_preexposure_mismatch(
        self,
        raw: RawCfgSourceEpoch,
        work: ResolvedWork,
        bundle: DeliveryBundle,
    ) -> RuntimeFaultReceipt:
        if (
            self.device_id != work.device_id
            or raw.experiment_id != work.experiment_id
            or raw.runtime_instance_id != work.runtime_instance_id
            or raw.lease_generation != work.lease_generation
            or raw.writer_generation != work.writer_generation
            or raw.connection_generation != work.connection_generation
            or bundle.work_id != work.work_id
            or bundle.finished_at is None
        ):
            raise ComponentStoreError("pre-exposure mismatch identity is not the prepared work bundle")
        row = await self._fetchrow(
            _RECORD_PREEXPOSURE_MISMATCH_SQL,
            work.experiment_id,
            work.work_id,
            bundle.bundle_id,
            work.device_id,
            raw.source_epoch_id,
            raw.wire_vector,
            json.dumps(_canonical_raw_observations(raw), separators=(",", ":")),
            raw.revisions.firmware_revision,
            raw.revisions.config_revision,
            raw.revisions.registry_revision,
            raw.revisions.grid_revision,
            raw.runtime_instance_id,
            raw.lease_generation,
            raw.writer_generation,
            raw.connection_generation,
            COMPONENT_EXECUTOR_ACTOR,
        )
        if row is None:
            raise ComponentStoreError("migration-214 pre-exposure mismatch returned no fault receipt")
        echoed = (
            str(_row_value(row, "fault_report_id")),
            str(_row_value(row, "experiment_id")),
            str(_row_value(row, "device_id")),
            int(_row_value(row, "reported_lease_generation")),
            str(_row_value(row, "reporter_runtime_instance_id")),
            int(_row_value(row, "reporter_writer_generation")),
            int(_row_value(row, "reporter_connection_generation")),
            str(_row_value(row, "reported_fault_kind")),
            str(_row_value(row, "reason")),
        )
        expected = (
            raw.source_epoch_id,
            work.experiment_id,
            work.device_id,
            raw.lease_generation,
            raw.runtime_instance_id,
            raw.writer_generation,
            raw.connection_generation,
            "stale_or_mismatched_work",
            "post_delivery_observation_mismatch",
        )
        if echoed != expected:
            raise ComponentStoreError("migration-214 pre-exposure mismatch receipt input mismatch")
        receipt = _runtime_fault_receipt(row)
        if receipt.facility_authority_yielded and receipt.authority_hold_required:
            raise ComponentStoreError("pre-exposure mismatch receipt has conflicting authority outputs")
        return receipt

    async def monitor_open_exposure(
        self,
        experiment_id: str,
        *,
        device_id: str,
        lease_generation: int,
    ) -> RuntimeExposureStatus | None:
        if self.device_id != device_id:
            raise ComponentStoreError("open exposure monitor device is not the prepared executor")
        row = await self._fetchrow(
            _MONITOR_OPEN_EXPOSURE_SQL,
            experiment_id,
            device_id,
            lease_generation,
        )
        return None if row is None else _runtime_exposure_status(row)

    async def report_runtime_fault(
        self,
        experiment_id: str,
        *,
        device_id: str,
        fault_report_id: str,
        expected_lease_generation: int,
        runtime_instance_id: str,
        writer_generation: int,
        connection_generation: int,
        fault_kind: str,
        reason: str,
    ) -> RuntimeFaultReceipt:
        if self.device_id != device_id:
            raise ComponentStoreError("runtime fault device is not the prepared executor")
        row = await self._fetchrow(
            _REPORT_RUNTIME_FAULT_SQL,
            experiment_id,
            device_id,
            fault_report_id,
            expected_lease_generation,
            runtime_instance_id,
            writer_generation,
            connection_generation,
            fault_kind,
            reason,
            COMPONENT_EXECUTOR_ACTOR,
        )
        if row is None:
            raise ComponentStoreError("migration-214 runtime fault returned no receipt")
        echoed = (
            str(_row_value(row, "experiment_id")),
            str(_row_value(row, "device_id")),
            int(_row_value(row, "reported_lease_generation")),
            str(_row_value(row, "reporter_runtime_instance_id")),
            int(_row_value(row, "reporter_writer_generation")),
            int(_row_value(row, "reporter_connection_generation")),
            str(_row_value(row, "reported_fault_kind")),
            str(_row_value(row, "reason")),
        )
        expected = (
            experiment_id,
            device_id,
            expected_lease_generation,
            runtime_instance_id,
            writer_generation,
            connection_generation,
            fault_kind,
            reason,
        )
        if echoed != expected:
            raise ComponentStoreError("migration-214 runtime fault receipt immutable input mismatch")
        receipt = _runtime_fault_receipt(row)
        if receipt.fault_report_id != fault_report_id:
            raise ComponentStoreError("migration-214 runtime fault receipt identity mismatch")
        return receipt

    async def safe_startup_attestation(
        self,
        *,
        device_id: str,
        experiment_id: str | None,
    ) -> StartupAttestation:
        row = await self._fetchrow(
            _SAFE_STARTUP_ATTESTATION_SQL,
            device_id,
            experiment_id,
        )
        if row is None:
            raise ComponentStoreError("migration-214 startup attestation returned no row")
        attestation = _startup_attestation(row)
        if attestation.device_id != device_id or attestation.requested_experiment_id != experiment_id:
            raise ComponentStoreError("migration-214 startup attestation scope mismatch")
        if (
            min(
                attestation.active_experiment_count,
                attestation.open_exposure_count,
                attestation.recovery_pending_count,
            )
            < 0
            or (attestation.scope_resolved and experiment_id is not None and attestation.scoped_experiment_id is None)
            or (attestation.facility_authority_yielded and attestation.hold_required)
        ):
            raise ComponentStoreError("migration-214 startup attestation is internally inconsistent")
        return attestation


StoreFactory = Callable[[object], ComponentExperimentStore]
RuntimeFenceProvider = Callable[[], RuntimeFence]
_store_factory: StoreFactory | None = AsyncpgComponentExperimentStore


class AttestedComponentPool:
    """Pool wrapper created only after the restricted DB-role proof passes."""

    component_executor_role_attested = True

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    def acquire(self):
        return self._pool.acquire()

    async def close(self) -> None:
        await self._pool.close()


_COMPONENT_ROLE_ATTESTATION_SQL = """
WITH login AS (
    SELECT oid, rolcanlogin, rolinherit, rolsuper, rolcreatedb, rolcreaterole,
           rolreplication, rolbypassrls
      FROM pg_roles
     WHERE rolname = current_user
), duty AS (
    SELECT oid, rolcanlogin, rolinherit, rolsuper, rolcreatedb, rolcreaterole,
           rolreplication, rolbypassrls
      FROM pg_roles
     WHERE rolname = 'verdify_experiment_component_executor'
), required_functions(function_signature) AS (
    SELECT unnest(ARRAY[
        'public.fn_experiment_v2_resolve_readiness(uuid,uuid,bigint)',
        'public.fn_experiment_v2_resolve_randomized(uuid,uuid,bigint)',
        'public.fn_experiment_v2_resolve_recovery(uuid,uuid,bigint)',
        'public.fn_experiment_v2_executor_runtime(uuid,text)',
        'public.fn_experiment_v2_claim_executor_candidate(uuid,text,bigint,text)',
        'public.fn_experiment_v2_read_observation_window(uuid,uuid,uuid,text,bigint)',
        'public.fn_experiment_v2_record_work_event(uuid,uuid,text,jsonb,text)',
        'public.fn_experiment_v2_begin_delivery_bundle(uuid,uuid,uuid,text,text,text)',
        'public.fn_experiment_v2_read_delivery_bundle(uuid,uuid,text,text,bigint)',
        'public.fn_experiment_v2_record_component_outcome(uuid,uuid,uuid,integer,text,text,bigint,bigint,text)',
        'public.fn_experiment_v2_record_delivery_bundle(uuid,uuid,uuid,timestamptz,text)',
        'public.fn_experiment_v2_register_runtime_instance(uuid,text,uuid,bigint,text)',
        'public.fn_experiment_v2_record_observation_epoch(uuid,uuid,uuid,uuid,bytea,jsonb,text,text,text,text,bigint,bigint,text)',
        'public.fn_experiment_v2_record_preexposure_mismatch(uuid,uuid,uuid,text,uuid,bytea,jsonb,text,text,text,text,uuid,bigint,bigint,bigint,text)',
        'public.fn_experiment_v2_record_runtime_snapshot(uuid,text,uuid,bytea,jsonb,text,text,text,text,uuid,bigint,bigint,boolean,text)',
        'public.fn_experiment_v2_monitor_open_exposure(uuid,text,bigint)',
        'public.fn_experiment_v2_open_exposure(uuid,uuid,text,text)',
        'public.fn_experiment_v2_close_exposure(uuid,text,text)',
        'public.fn_experiment_v2_request_recovery(uuid,uuid,tstzrange,timestamptz,text,text)',
        'public.fn_experiment_v2_report_runtime_fault(uuid,text,uuid,bigint,uuid,bigint,bigint,text,text,text)',
        'public.fn_experiment_v2_safe_startup_attestation(text,uuid)'
    ]::text[])
)
SELECT current_user::text AS current_user_name,
       session_user::text AS session_user_name,
       current_user = session_user AS session_user_matches,
       pg_has_role(current_user, 'verdify_experiment_component_executor', 'member')
           AS duty_member,
       coalesce((
           SELECT NOT membership.admin_option
             FROM pg_auth_members membership
             CROSS JOIN login CROSS JOIN duty
            WHERE membership.member = login.oid
              AND membership.roleid = duty.oid
       ), false) AS duty_membership_non_admin,
       coalesce((SELECT rolcanlogin AND rolinherit FROM login), false)
           AS login_role_safe,
       coalesce((SELECT rolsuper FROM login), true) AS is_superuser,
       coalesce((
           SELECT d.datdba = login.oid
             FROM pg_database d CROSS JOIN login
            WHERE d.datname = current_database()
       ), true) AS is_database_owner,
       coalesce((
           SELECT rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls
             FROM login
       ), true) AS has_elevated_role_attributes,
       coalesce((
           SELECT NOT duty.rolcanlogin AND NOT duty.rolinherit AND
                  NOT duty.rolsuper AND
                  NOT duty.rolcreatedb AND NOT duty.rolcreaterole AND
                  NOT duty.rolreplication AND NOT duty.rolbypassrls AND
                  NOT EXISTS (
                      SELECT 1
                        FROM pg_roles inherited
                       WHERE inherited.oid <> duty.oid
                         AND pg_has_role(duty.oid, inherited.oid, 'member')
                  )
             FROM duty
       ), false) AS duty_role_safe,
       EXISTS (
           SELECT 1
             FROM pg_roles inherited
            WHERE inherited.rolname NOT IN (
                      current_user,
                      'verdify_experiment_component_executor'
                  )
              AND pg_has_role(current_user, inherited.oid, 'member')
       ) AS has_other_role_membership,
       EXISTS (
           SELECT 1
             FROM pg_roles candidate
             CROSS JOIN login
             CROSS JOIN duty
            WHERE candidate.oid NOT IN (login.oid, duty.oid)
              AND NOT candidate.rolsuper
              AND pg_has_role(candidate.oid, duty.oid, 'member')
       ) AS has_unexpected_duty_member,
       EXISTS (
           SELECT 1
             FROM pg_namespace namespace CROSS JOIN login
            WHERE namespace.nspname = 'public'
              AND namespace.nspowner = login.oid
           UNION ALL
           SELECT 1
             FROM pg_class owned
             JOIN pg_namespace namespace ON namespace.oid = owned.relnamespace
             CROSS JOIN login
            WHERE namespace.nspname = 'public' AND owned.relowner = login.oid
           UNION ALL
           SELECT 1
             FROM pg_proc owned
             JOIN pg_namespace namespace ON namespace.oid = owned.pronamespace
             CROSS JOIN login
            WHERE namespace.nspname = 'public' AND owned.proowner = login.oid
       ) AS has_managed_object_ownership,
       has_schema_privilege(current_user, 'public', 'USAGE') AS schema_usage,
       has_schema_privilege(current_user, 'public', 'CREATE')
           AS has_public_schema_create,
       EXISTS (
           SELECT 1
             FROM pg_class protected
             JOIN pg_namespace namespace ON namespace.oid = protected.relnamespace
            WHERE namespace.nspname = 'public'
              AND protected.relkind IN ('r', 'p', 'v', 'm', 'f')
              AND (
                  has_table_privilege(
                      current_user,
                      protected.oid,
                      'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'
                  )
                  OR has_any_column_privilege(
                      current_user,
                      protected.oid,
                      'SELECT,INSERT,UPDATE,REFERENCES'
                  )
              )
       ) AS has_protected_relation_privilege,
       EXISTS (
           SELECT 1
             FROM pg_class protected
             JOIN pg_namespace namespace ON namespace.oid = protected.relnamespace
            WHERE namespace.nspname = 'public'
              AND protected.relkind = 'S'
              AND has_sequence_privilege(
                  current_user,
                  protected.oid,
                  'USAGE,SELECT,UPDATE'
              )
       ) AS has_protected_sequence_privilege,
       EXISTS (
           SELECT 1
             FROM pg_proc candidate_function
             JOIN pg_namespace namespace ON namespace.oid = candidate_function.pronamespace
            WHERE namespace.nspname = 'public'
              AND (
                  candidate_function.proname LIKE 'fn_experiment_v2_%'
                  OR candidate_function.prosecdef
              )
              AND has_function_privilege(current_user, candidate_function.oid, 'EXECUTE')
              AND NOT EXISTS (
                  SELECT 1
                    FROM required_functions required
                   WHERE to_regprocedure(required.function_signature) = candidate_function.oid
              )
       ) AS has_unexpected_function_execute,
       NOT EXISTS (
           SELECT 1
             FROM required_functions required
            WHERE to_regprocedure(required.function_signature) IS NULL
               OR NOT has_function_privilege(
                   current_user,
                   to_regprocedure(required.function_signature),
                   'EXECUTE'
               )
       ) AS has_required_function_execute
"""


def _component_role_attestation_passes(row: Mapping[str, object] | None) -> bool:
    return bool(
        row is not None
        and row["current_user_name"] == _COMPONENT_EXECUTOR_LOGIN
        and row["session_user_name"] == _COMPONENT_EXECUTOR_LOGIN
        and row["session_user_matches"] is True
        and row["duty_member"] is True
        and row["duty_membership_non_admin"] is True
        and row["login_role_safe"] is True
        and row["is_superuser"] is False
        and row["is_database_owner"] is False
        and row["has_elevated_role_attributes"] is False
        and row["duty_role_safe"] is True
        and row["has_other_role_membership"] is False
        and row["has_unexpected_duty_member"] is False
        and row["has_managed_object_ownership"] is False
        and row["schema_usage"] is True
        and row["has_public_schema_create"] is False
        and row["has_protected_relation_privilege"] is False
        and row["has_protected_sequence_privilege"] is False
        and row["has_unexpected_function_execute"] is False
        and row["has_required_function_execute"] is True
    )


_COMPONENT_EXECUTOR_LOGIN = "verdify_experiment_v2_component_executor_login"


async def create_component_experiment_pool() -> AttestedComponentPool | None:
    """Create a dedicated function-only executor pool, or fail closed.

    The ordinary ingestor database-owner credential is never accepted for the
    experiment path. Passwords remain in environment/driver memory and are
    never interpolated into a DSN or log message.
    """
    user = os.environ.get("VERDIFY_EXPERIMENT_COMPONENT_DB_USER", "")
    password = os.environ.get("VERDIFY_EXPERIMENT_COMPONENT_DB_PASSWORD", "")
    ordinary_user = os.environ.get("DB_USER", "verdify")
    if not user and not password:
        return None
    if not user or not password or user == ordinary_user:
        log.error("component_executor dedicated database credential is incomplete or shared; refusing it")
        return None
    if user != _COMPONENT_EXECUTOR_LOGIN:
        log.error("component_executor database login identity is not the locked login")
        return None

    candidate: asyncpg.Pool | None = None
    try:
        candidate = await asyncpg.create_pool(
            host=os.environ.get("DB_HOST", "localhost"),
            port=int(os.environ.get("DB_PORT", "5432")),
            database=os.environ.get("DB_NAME", "verdify"),
            user=user,
            password=password,
            min_size=1,
            max_size=2,
            max_inactive_connection_lifetime=60,
        )
        async with candidate.acquire() as connection:
            role = await connection.fetchrow(_COMPONENT_ROLE_ATTESTATION_SQL)
        if not _component_role_attestation_passes(role):
            await candidate.close()
            log.error("component_executor database login lacks the exact restricted duty; refusing it")
            return None
        return AttestedComponentPool(candidate)
    except Exception as exc:
        if candidate is not None:
            try:
                await candidate.close()
            except Exception:
                pass
        log.error("component_executor dedicated database pool unavailable error=%s", type(exc).__name__)
        return None


async def attest_component_safe_startup(pool: object | None) -> StartupAttestation | None:
    """Retain a startup writer hold when durable v2 authority may exist.

    This is called before the ESPHome loop or ordinary dispatch can start.  A
    successful no-authority result never releases an existing hold: the SQL
    surface is intentionally attestation-only, not a release capability.
    """
    prime_component_startup_hold()
    if pool is None:
        return None
    if not bool(getattr(pool, "component_executor_role_attested", False)):
        set_component_authority_hold(True, CANONICAL_FIELD_ORDER)
        raise ComponentStoreError("component startup database role is not attested")
    store = AsyncpgComponentExperimentStore(pool)
    device_id = policy_device_id(os.environ.get("GREENHOUSE_ID", "vallery"))
    requested_experiment_id = active_experiment_id()
    try:
        attestation = await store.safe_startup_attestation(
            device_id=device_id,
            experiment_id=requested_experiment_id,
        )
    except Exception:
        set_component_authority_hold(True, CANONICAL_FIELD_ORDER)
        raise
    if not attestation.scope_resolved or attestation.hold_required:
        set_component_authority_hold(True, CANONICAL_FIELD_ORDER)
    log.info(
        "component_executor startup_attestation resolved=%s hold_required=%s reason=%s",
        attestation.scope_resolved,
        attestation.hold_required,
        attestation.attestation_reason,
    )
    return attestation


def install_component_store_factory(factory: StoreFactory | None) -> None:
    """Override/reset the function-only migration-214 adapter (tests/composition)."""
    global _store_factory
    _store_factory = factory


def runtime_fence() -> RuntimeFence:
    """Snapshot both physical fences immediately before resolver/claim."""
    # The adapter replaces zero with the database-owned lease generation only
    # after the environment gate and runtime-authority function both pass.
    return RuntimeFence(
        lease_generation=0,
        writer_generation=0,
        connection_generation=int(shared.transport_generation),
        writer_lease_held=shared.writer_lease_strictly_held(),
        device_write_enabled=device_writes_enabled(),
    )


def _aware(moment: datetime, field_name: str) -> datetime:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ComponentContractError("naive_timestamp", field_name)
    return moment.astimezone(UTC)


def _uuid(value: str, field_name: str) -> UUID:
    try:
        parsed = UUID(value)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ComponentContractError("invalid_uuid", field_name) from exc
    if str(parsed) != value.lower():
        raise ComponentContractError("noncanonical_uuid", field_name)
    return parsed


def _validate_hash(value: str, field_name: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ComponentContractError("invalid_sha256", field_name)


def _validate_work(work: ResolvedWork, expected_experiment_id: str, fence: RuntimeFence) -> None:
    _uuid(work.experiment_id, "experiment_id")
    _uuid(work.work_id, "work_id")
    if work.assignment_id is not None:
        _uuid(work.assignment_id, "assignment_id")
    if work.experiment_id != expected_experiment_id:
        raise ComponentContractError("experiment_mismatch")
    validate_work_phase(work.operation_kind, work.execution_phase)
    if work.protocol_version != 2 or work.transport_kind != "legacy_components_v1":
        raise ComponentContractError("protocol_or_transport_mismatch")
    _validate_hash(work.target_state_content_sha256, "target_state_content_sha256")
    _validate_hash(work.baseline_state_content_sha256, "baseline_state_content_sha256")
    _validate_hash(work.revisions.bundle_sha256, "revision_bundle_sha256")
    _uuid(work.runtime_instance_id, "runtime_instance_id")
    if work.runtime_instance_id != RUNTIME_INSTANCE_ID:
        raise ComponentContractError("runtime_instance_mismatch")
    if work.revisions.bundle_sha256 != work.expected_revision_bundle_sha256:
        raise ComponentContractError("revision_bundle_mismatch")
    if work.lease_generation != fence.lease_generation:
        raise ComponentContractError("lease_generation_mismatch")
    if work.writer_generation != fence.writer_generation:
        raise ComponentContractError("writer_generation_mismatch")
    if work.connection_generation != fence.connection_generation:
        raise ComponentContractError("connection_generation_mismatch")

    now = _aware(work.resolved_at, "resolved_at")
    valid_from = _aware(work.valid_from, "valid_from")
    valid_until = _aware(work.valid_until, "valid_until")
    expires_at = _aware(work.expires_at, "expires_at")
    claim_expires_at = _aware(work.claim_expires_at, "claim_expires_at")
    if not (valid_from <= now < valid_until and now < expires_at and now < claim_expires_at):
        raise ComponentContractError("work_expired_or_not_current")

    if work.admission_state == "emergency_hold":
        raise ComponentContractError("emergency_hold")
    if work.operation_kind == WORK_KIND_PREVIEW:
        if work.admission_state != "closed" or work.lifecycle_status != "draft":
            raise ComponentContractError("preview_admission_mismatch")
        return
    grid_attestation = component_entity_grid_attestation()
    if not physical_execution_qualified(
        work.revisions.grid_revision,
        grid_attestation.grid_revision if grid_attestation is not None else None,
    ):
        raise ComponentContractError("physical_route_grid_or_prefix_replay_unqualified")
    if work.operation_kind == WORK_KIND_RECOVERY:
        if work.admission_state != "baseline_recovery":
            raise ComponentContractError("recovery_admission_mismatch")
        if work.lifecycle_status not in {"draft", "armed", "running", "paused"}:
            raise ComponentContractError("recovery_lifecycle_mismatch")
    else:
        if work.admission_state != "open":
            raise ComponentContractError("physical_admission_not_open")
        permitted_lifecycle = {"running"} if work.operation_kind == WORK_KIND_ASSIGNMENT else {"draft"}
        if work.lifecycle_status not in permitted_lifecycle:
            raise ComponentContractError("physical_lifecycle_mismatch")
    if work.lifecycle_status == "paused" and work.operation_kind != WORK_KIND_RECOVERY:
        raise ComponentContractError("paused_nonrecovery_forbidden")
    if work.operation_kind == WORK_KIND_ASSIGNMENT:
        if work.assignment_id is None or work.assignment_id != work.work_id:
            raise ComponentContractError("randomized_assignment_lineage_mismatch")
    elif work.assignment_id is not None:
        raise ComponentContractError("nonrandomized_assignment_forbidden")


def _epoch_timestamp_bounds(epoch: ObservationEpoch) -> tuple[datetime, datetime]:
    timestamps = [_aware(observation.observed_at, "observed_at") for observation in epoch.observations.values()]
    if not timestamps:
        raise ComponentContractError("empty_observation_epoch")
    return min(timestamps), max(timestamps)


def validate_current_observation(
    work: ResolvedWork,
    epoch: ObservationEpoch,
    *,
    now: datetime,
) -> dict[str, ComponentValue]:
    """Validate the pre-delivery source state used to compute differences."""
    if epoch.experiment_id != work.experiment_id:
        raise ComponentContractError("current_observation_experiment_mismatch")
    if epoch.identity_source != "derived_cfg_readbacks_v1":
        raise ComponentContractError("current_observation_identity_source_mismatch")
    if epoch.revisions != work.revisions:
        raise ComponentContractError("current_observation_revision_mismatch")
    if epoch.runtime_instance_id != work.runtime_instance_id:
        raise ComponentContractError("current_observation_runtime_instance_mismatch")
    if epoch.writer_generation != work.writer_generation:
        raise ComponentContractError("current_observation_writer_generation_mismatch")
    if epoch.connection_generation != work.connection_generation:
        raise ComponentContractError("current_observation_connection_generation_mismatch")
    normalized = normalize_complete_state(epoch.values)
    earliest, latest = _epoch_timestamp_bounds(epoch)
    now = _aware(now, "now")
    if latest - earliest > MAX_EPOCH_SKEW:
        raise ComponentContractError("current_observation_skew_exceeded")
    if now - latest > MAX_SNAPSHOT_AGE or latest > now:
        raise ComponentContractError("current_observation_stale_or_future")
    return normalized


def validate_confirmation_epochs(
    work: ResolvedWork,
    bundle: DeliveryBundle,
    epochs: Sequence[ObservationEpoch],
    expected_state: Mapping[str, ComponentValue],
    expected_state_content_sha256: str,
    *,
    now: datetime,
) -> ConfirmationResult:
    """Validate exactly the two-source-epoch exposure barrier in Python.

    L3 independently validates the same facts when opening exposure; retaining
    the executor check makes mismatches fail before that call and keeps fakes /
    vertical tests honest.
    """
    if len(epochs) < 2:
        return ConfirmationResult(False, True, "awaiting_two_observation_epochs")
    first, second = sorted(epochs, key=lambda item: item.persisted_at)[-2:]
    normalized_expected = normalize_complete_state(expected_state)
    now = _aware(now, "now")

    for epoch in (first, second):
        if (
            epoch.experiment_id != work.experiment_id
            or epoch.work_id != work.work_id
            or epoch.bundle_id != bundle.bundle_id
            or epoch.execution_phase != work.execution_phase
            or epoch.operation_kind != work.operation_kind
        ):
            return ConfirmationResult(False, False, "observation_lineage_mismatch")
        if epoch.identity_source != "derived_cfg_readbacks_v1":
            return ConfirmationResult(False, False, "observation_identity_source_mismatch")
        if epoch.state_content_sha256 != expected_state_content_sha256:
            return ConfirmationResult(False, False, "observed_state_hash_mismatch")
        if epoch.revisions != work.revisions:
            return ConfirmationResult(False, False, "observation_revision_mismatch")
        if epoch.runtime_instance_id != work.runtime_instance_id:
            return ConfirmationResult(False, False, "observation_runtime_instance_mismatch")
        if epoch.writer_generation != work.writer_generation:
            return ConfirmationResult(False, False, "observation_writer_generation_mismatch")
        if epoch.connection_generation != work.connection_generation:
            return ConfirmationResult(False, False, "observation_connection_generation_mismatch")
        try:
            normalized_observed = normalize_complete_state(epoch.values)
        except ComponentContractError as exc:
            return ConfirmationResult(False, False, exc.code)
        if normalized_observed != normalized_expected:
            return ConfirmationResult(False, False, "observed_state_value_mismatch")
        earliest, latest = _epoch_timestamp_bounds(epoch)
        if latest - earliest > MAX_EPOCH_SKEW:
            return ConfirmationResult(False, False, "observation_epoch_skew_exceeded")
        if earliest <= _aware(bundle.finished_at or bundle.reserved_at, "bundle_finished_at"):
            return ConfirmationResult(False, False, "observation_not_post_delivery")
        if now - latest > MAX_SNAPSHOT_AGE or latest > now:
            return ConfirmationResult(False, False, "observation_epoch_stale_or_future")

    if first.source_epoch_id == second.source_epoch_id:
        return ConfirmationResult(False, False, "duplicate_source_epoch")
    _first_min, first_max = _epoch_timestamp_bounds(first)
    _second_min, second_max = _epoch_timestamp_bounds(second)
    if second_max - first_max < MIN_EPOCH_SEPARATION:
        return ConfirmationResult(False, False, "observation_epoch_separation_too_short")
    for field_name in normalized_expected:
        if _aware(second.observations[field_name].observed_at, "observed_at") <= _aware(
            first.observations[field_name].observed_at, "observed_at"
        ):
            return ConfirmationResult(False, False, "cached_or_relabelled_observation_epoch")
    return ConfirmationResult(True, False, "confirmed", second_max)


def _calls(changes: Sequence[ComponentChange]) -> tuple[ComponentBundleCall, ...]:
    return tuple(
        ComponentBundleCall(
            parameter=change.field_name,
            object_id=change.object_id,
            value=change.value,
            entity_type=change.entity_type,
        )
        for change in changes
    )


async def _best_effort_close(store: ComponentExperimentStore, work: ResolvedWork, reason: str) -> None:
    try:
        await store.close_exposure(work, reason)
    except Exception as exc:
        log.critical(
            "component_executor exposure_close_unproven work_id=%s reason=%s error=%s",
            work.work_id,
            reason,
            type(exc).__name__,
        )


async def _request_recovery_if_safe(
    store: ComponentExperimentStore,
    work: ResolvedWork,
    fence: RuntimeFence,
    reason: str,
) -> None:
    if (
        work.operation_kind == WORK_KIND_RECOVERY
        or work.signals.facility_rescue_active
        or not fence.writer_lease_held
        or fence.connection_generation != int(shared.transport_generation)
    ):
        return
    await store.request_recovery(work, reason)


async def _persist_and_monitor_open_exposure(
    store: ComponentExperimentStore,
    *,
    experiment_id: str,
    device_id: str,
    authority: RuntimeAuthority,
    fence: RuntimeFence,
) -> RuntimeExposureStatus | None:
    """Persist open-exposure evidence before resolving any new physical work.

    Ordinary epochs remain available to the delivery confirmation barrier until
    L3 reports an exposure is actually open. Reset epochs are always reported:
    they invalidate pre-exposure work too and can create bounded recovery.
    """
    status = await store.monitor_open_exposure(
        experiment_id,
        device_id=device_id,
        lease_generation=authority.lease_generation,
    )
    exposure_open = status is not None and status.exposure_is_open
    persisted_any = False
    for raw in component_cfg_source_epochs():
        if (
            raw.experiment_id != experiment_id
            or raw.revisions != authority.revisions
            or raw.runtime_instance_id != RUNTIME_INSTANCE_ID
            or raw.lease_generation != authority.lease_generation
            or raw.writer_generation != authority.writer_generation
            or raw.connection_generation != fence.connection_generation
        ):
            raise ComponentStoreError("raw runtime snapshot identity is stale")
        if not exposure_open and not raw.reset_detected:
            continue
        await store.record_runtime_snapshot(raw, device_id=device_id)
        _consume_component_cfg_source_epoch(raw.source_epoch_id)
        if raw.reset_detected:
            # Migration 214 durably records the source-keyed fault and creates
            # or retains baseline recovery even when no exposure exists. Stop
            # this tick so no ordinary/nonbaseline claim can follow the reset.
            try:
                refreshed = await store.prepare_runtime(
                    experiment_id,
                    device_id=device_id,
                    connection_generation=fence.connection_generation,
                    writer_lease_held=fence.writer_lease_held,
                    device_write_enabled=fence.device_write_enabled,
                )
            except Exception:
                raise ComponentRuntimeFault("device_reset_detected") from None
            raise ComponentRuntimeFault(
                "device_reset_detected",
                authority_hold_required=refreshed.component_authority_required,
            )
        persisted_any = True
    if persisted_any:
        status = await store.monitor_open_exposure(
            experiment_id,
            device_id=device_id,
            lease_generation=authority.lease_generation,
        )
    if status is None:
        return status
    return status


def _open_exposure_runtime_fault(
    status: RuntimeExposureStatus | None,
    authority: RuntimeAuthority,
    fence: RuntimeFence,
) -> tuple[str, str] | None:
    if status is None or not status.exposure_is_open:
        return None
    if (
        status.current_runtime_instance_id != RUNTIME_INSTANCE_ID
        or status.current_writer_generation != authority.writer_generation
        or status.current_connection_generation != fence.connection_generation
    ):
        return "writer_collision", "open_exposure_runtime_ownership_mismatch"
    if status.source_epoch_id is not None and (
        status.source_runtime_instance_id != RUNTIME_INSTANCE_ID
        or status.source_writer_generation != authority.writer_generation
        or status.source_connection_generation != fence.connection_generation
    ):
        return "writer_collision", "open_exposure_source_generation_mismatch"
    if status.foreign_writer:
        return "writer_collision", "open_exposure_foreign_writer"
    if status.reset_detected:
        return "reboot", "open_exposure_device_reset"
    if status.common_field_drift:
        return "common_field_drift", "open_exposure_common_field_drift"
    if status.cfg_drift or status.lineage_drift:
        return "cfg_drift", "open_exposure_cfg_or_lineage_drift"
    if status.close_reason is not None:
        return "protocol_deviation", "open_exposure_has_close_reason"
    if status.exposure_started_at is None or status.resolved_at is None:
        raise ComponentStoreError("open exposure monitor lacks authoritative timestamps")
    if status.resolved_at < status.exposure_started_at:
        return "sensor_gap", "open_exposure_clock_order_invalid"
    if status.source_epoch_id is None:
        if status.resolved_at - status.exposure_started_at > MAX_SNAPSHOT_AGE:
            return "sensor_gap", "open_exposure_first_snapshot_overdue"
        return None
    if status.last_observed_at is None:
        return "sensor_gap", "open_exposure_snapshot_timestamp_missing"
    if status.last_observed_at <= status.exposure_started_at or status.last_observed_at > status.resolved_at:
        return "sensor_gap", "open_exposure_snapshot_time_invalid"
    if status.resolved_at - status.last_observed_at > MAX_SNAPSHOT_AGE:
        return "sensor_gap", "open_exposure_snapshot_stale"
    return None


async def _stop_for_runtime_fault(
    store: ComponentExperimentStore,
    *,
    experiment_id: str,
    device_id: str,
    fault_kind: str,
    reason: str,
    reporter: RuntimeReporterIdentity | None,
    work_id: str | None = None,
) -> ExecutorResult:
    _queue_runtime_fault(
        experiment_id,
        device_id,
        fault_kind=fault_kind,
        reason=reason,
        reporter=reporter,
    )
    try:
        receipt = await _persist_pending_runtime_fault(
            store,
            experiment_id,
            device_id,
            reporter=reporter,
        )
    except Exception as exc:
        set_component_authority_hold(True, CANONICAL_FIELD_ORDER)
        log.error("component_executor runtime_fault_persist_failed error=%s", type(exc).__name__)
        return ExecutorResult("failed", "runtime_fault_persistence_uncertain", work_id)
    if receipt is None:
        set_component_authority_hold(True, CANONICAL_FIELD_ORDER)
        return ExecutorResult("failed", "runtime_fault_persistence_uncertain", work_id)
    if receipt.facility_authority_yielded:
        return ExecutorResult("yielded", "facility_rescue_yield", work_id)
    return ExecutorResult("failed", reason, work_id)


def _executor_fault_kind(reason: str) -> str | None:
    if "writer_lease_not_held" in reason:
        return "lease_loss"
    if "connection_generation" in reason:
        return "connection_generation_changed"
    if reason == "device_writes_disabled":
        return "protocol_deviation"
    if reason in {
        "delivery_outcome_unknown",
        "delivery_transport_uncertain",
        "bundle_completion_uncertain",
        "interrupted_bundle_persistence_uncertain",
    }:
        return "unknown_delivery"
    if "uncertain" in reason or "persistence" in reason:
        return "db_outage"
    return None


class ConfirmedComponentExecutor:
    def __init__(
        self,
        store: ComponentExperimentStore,
        *,
        transport: ComponentTransport | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.transport = transport or Esp32ComponentTransport()
        self.clock = clock or (lambda: datetime.now(UTC))

    async def _run_preview(self, work: ResolvedWork) -> ExecutorResult:
        """Durably prove the actual baseline without acquiring write authority."""
        set_component_authority_hold(False)
        self.clock()  # one stable processing tick before the durable marker
        try:
            baseline, target = validate_routine_target(work.baseline_state, work.target_state)
            if (
                work.target_profile != "baseline"
                or target != baseline
                or work.target_state_content_sha256 != work.baseline_state_content_sha256
            ):
                raise ComponentContractError("preview_target_not_exact_baseline")
        except ComponentContractError as exc:
            try:
                await self.store.record_work_event(work, "failed", {"reason": exc.code})
            except Exception:
                return ExecutorResult("failed", "preview_failure_persistence_uncertain", work.work_id)
            return ExecutorResult("failed", exc.code, work.work_id)

        attempted_bundle_id = str(uuid4())
        try:
            reservation = await self.store.reserve_bundle(
                work,
                bundle_id=attempted_bundle_id,
                purpose="preview",
                expected_state_content_sha256=work.baseline_state_content_sha256,
            )
        except Exception:
            return ExecutorResult("failed", "preview_marker_reservation_uncertain", work.work_id)
        bundle = reservation.bundle
        if bundle.finished_at is None:
            try:
                bundle = await self.store.finish_bundle(work, bundle, self.clock())
            except Exception:
                return ExecutorResult(
                    "failed",
                    "preview_marker_completion_uncertain",
                    work.work_id,
                    bundle.bundle_id,
                )
        try:
            epochs = await self.store.observation_epochs(work, bundle)
        except Exception:
            return ExecutorResult("failed", "preview_observation_read_uncertain", work.work_id, bundle.bundle_id)
        confirmation = validate_confirmation_epochs(
            work,
            bundle,
            epochs,
            baseline,
            work.baseline_state_content_sha256,
            now=self.clock(),
        )
        if confirmation.pending:
            try:
                await self.store.record_work_event(work, "deferred", {"reason": confirmation.reason})
            except Exception:
                return ExecutorResult("failed", "preview_defer_uncertain", work.work_id, bundle.bundle_id)
            return ExecutorResult("previewed", confirmation.reason, work.work_id, bundle.bundle_id)
        if not confirmation.confirmed:
            try:
                await self.store.record_work_event(work, "failed", {"reason": confirmation.reason})
            except Exception:
                return ExecutorResult("failed", "preview_failure_persistence_uncertain", work.work_id, bundle.bundle_id)
            return ExecutorResult("failed", confirmation.reason, work.work_id, bundle.bundle_id)
        try:
            await self.store.complete_preview(work)
        except Exception:
            return ExecutorResult("failed", "preview_persistence_uncertain", work.work_id, bundle.bundle_id)
        return ExecutorResult("previewed", "shadow_baseline_confirmed", work.work_id, bundle.bundle_id)

    async def run_once(self, expected_experiment_id: str, fence: RuntimeFence) -> ExecutorResult:
        try:
            work = await self.store.claim_next(
                expected_experiment_id,
                lease_generation=fence.lease_generation,
                writer_generation=fence.writer_generation,
                connection_generation=fence.connection_generation,
            )
        except Exception as exc:
            log.error("component_executor claim_failed error=%s", type(exc).__name__)
            return ExecutorResult("failed", "database_claim_uncertain")
        if work is None:
            return ExecutorResult("idle", "no_current_typed_work")

        try:
            _validate_work(work, expected_experiment_id, fence)
        except ComponentContractError as exc:
            if exc.code == "emergency_hold":
                set_component_authority_hold(False)
            await _best_effort_close(self.store, work, exc.code)
            try:
                await self.store.record_work_event(work, "failed", {"reason": exc.code})
            except Exception:
                pass
            return ExecutorResult("failed", exc.code, work.work_id)

        if work.operation_kind == WORK_KIND_PREVIEW:
            return await self._run_preview(work)

        if work.signals.facility_rescue_active:
            set_component_authority_hold(False)
            await _best_effort_close(self.store, work, "facility_rescue")
            try:
                await self.store.record_work_event(
                    work,
                    "failed",
                    {"reason": "facility_rescue_yield", "automatic_baseline": False},
                )
            except Exception:
                return ExecutorResult("failed", "facility_yield_persistence_uncertain", work.work_id)
            return ExecutorResult("yielded", "facility_rescue_yield", work.work_id)

        # From the first physical claim through transition, observation,
        # exposure, or baseline recovery, ordinary writers are denied all 48
        # canonical fields.  The exclusive component transport bypasses this
        # source-aware hold but still owns the shared physical lock.
        set_component_authority_hold(True, CANONICAL_FIELD_ORDER)
        physical_now = self.clock()

        if work.operation_kind == WORK_KIND_RECOVERY and not work.signals.facility_recovery_authorized:
            # Ordinary recovery does not need a facility event.  The signal is
            # required only after a rescue epoch; L3 expresses that by leaving
            # facility_rescue_active true until the immutable authorization.
            pass

        if work.signals.invalidates_physical_state and work.operation_kind != WORK_KIND_RECOVERY:
            reason = (
                "device_reboot"
                if work.signals.rebooted
                else "device_reset"
                if work.signals.reset_detected
                else "device_reconnect"
                if work.signals.reconnected
                else "foreign_writer"
            )
            await _best_effort_close(self.store, work, reason)
            try:
                await self.store.record_work_event(
                    work,
                    "failed",
                    {"reason": reason, "nonbaseline_reentry_forbidden": True},
                )
                await _request_recovery_if_safe(self.store, work, fence, reason)
            except Exception:
                return ExecutorResult("failed", "fault_persistence_uncertain", work.work_id)
            return ExecutorResult("failed", reason, work.work_id)

        if work.signals.nonbaseline_reentry_forbidden and work.target_profile != "baseline":
            await _best_effort_close(self.store, work, "nonbaseline_reentry_forbidden")
            try:
                await self.store.record_work_event(
                    work,
                    "failed",
                    {
                        "reason": "nonbaseline_reentry_forbidden",
                        "generation_recovery_cleared": work.signals.generation_recovery_cleared,
                        "same_generation_window": (work.signals.same_generation_nonbaseline_reentry_forbidden),
                    },
                )
            except Exception:
                return ExecutorResult("failed", "nonbaseline_reentry_persistence_uncertain", work.work_id)
            return ExecutorResult("failed", "nonbaseline_reentry_forbidden", work.work_id)

        if not fence.writer_lease_held or not fence.device_write_enabled:
            reason = "writer_lease_not_held" if not fence.writer_lease_held else "device_writes_disabled"
            return ExecutorResult("deferred", reason, work.work_id)
        if fence.connection_generation != int(shared.transport_generation):
            return ExecutorResult("failed", "connection_generation_changed", work.work_id)

        try:
            baseline, target = validate_routine_target(work.baseline_state, work.target_state)
            if work.operation_kind == WORK_KIND_RECOVERY and target != baseline:
                raise ComponentContractError("recovery_target_not_baseline")
        except ComponentContractError as exc:
            await _best_effort_close(self.store, work, exc.code)
            return ExecutorResult("failed", exc.code, work.work_id)

        if (
            work.operation_kind != WORK_KIND_RECOVERY
            and target != baseline
            and not work.baseline_interposition_confirmed
        ):
            try:
                await self.store.request_recovery(work, "baseline_interposition_required")
                await self.store.record_work_event(
                    work,
                    "deferred",
                    {"reason": "baseline_interposition_required"},
                )
            except Exception:
                return ExecutorResult("failed", "baseline_recovery_request_uncertain", work.work_id)
            return ExecutorResult("deferred", "baseline_interposition_required", work.work_id)

        if work.operation_kind == WORK_KIND_RECOVERY:
            # Recovery never trusts an apparent current value.  Initial
            # enrollment, reboot, reset, common-field drift, or an interrupted
            # prefix all make the physical state unknown, so all 48 baseline
            # setters are replayed in canonical fixed order.
            desired = baseline
            purpose: BundlePurpose = "recovery"
            expected_hash = work.baseline_state_content_sha256
            try:
                changes = fixed_order_complete_bundle(baseline, order=RECOVERY_ORDER)
            except ComponentContractError as exc:
                await _best_effort_close(self.store, work, exc.code)
                return ExecutorResult("failed", exc.code, work.work_id)
        else:
            try:
                current_epoch = await self.store.current_observation(work)
            except Exception:
                return ExecutorResult("failed", "current_observation_uncertain", work.work_id)
            if current_epoch is None:
                try:
                    await _request_recovery_if_safe(self.store, work, fence, "initial_enrollment")
                except Exception:
                    return ExecutorResult("failed", "initial_recovery_request_uncertain", work.work_id)
                return ExecutorResult("deferred", "initial_enrollment_requires_recovery", work.work_id)

            try:
                current = validate_current_observation(work, current_epoch, now=physical_now)
            except ComponentContractError as exc:
                await _best_effort_close(self.store, work, exc.code)
                try:
                    await _request_recovery_if_safe(self.store, work, fence, exc.code)
                except Exception:
                    return ExecutorResult("failed", "recovery_request_uncertain", work.work_id)
                return ExecutorResult("failed", exc.code, work.work_id)

            common_drift = [field for field in COMMON_FIELDS if current[field] != baseline[field]]
            if common_drift:
                await _best_effort_close(self.store, work, "common_field_drift")
                try:
                    await self.store.request_recovery(work, "common_field_drift")
                except Exception:
                    return ExecutorResult("failed", "common_drift_recovery_uncertain", work.work_id)
                return ExecutorResult("failed", "common_field_drift", work.work_id)

            desired = target
            purpose = "target"
            order = ROLLBACK_ORDER if work.target_profile == "baseline" else ACTIVATION_ORDER
            expected_hash = work.target_state_content_sha256
            try:
                changes = fixed_order_differences(current, desired, order=order)
            except ComponentContractError as exc:
                await _best_effort_close(self.store, work, exc.code)
                return ExecutorResult("failed", exc.code, work.work_id)

        attempted_bundle_id = str(uuid4())
        try:
            reservation = await self.store.reserve_bundle(
                work,
                bundle_id=attempted_bundle_id,
                purpose=purpose,
                expected_state_content_sha256=expected_hash,
            )
        except Exception:
            return ExecutorResult("failed", "bundle_reservation_uncertain", work.work_id)
        bundle = reservation.bundle
        if not reservation.owned:
            # The winner's durable bundle is authoritative.  Never emit a
            # second prefix; the next cycle observes/continues that bundle.
            try:
                await self.store.record_work_event(
                    work,
                    "deferred",
                    {
                        "reason": "durable_bundle_already_reserved",
                        "attempted_bundle_id": attempted_bundle_id,
                        "winning_bundle_id": bundle.bundle_id,
                    },
                )
            except Exception:
                return ExecutorResult("failed", "supersession_persistence_uncertain", work.work_id, bundle.bundle_id)
            if bundle.finished_at is None:
                # An unfinished durable bundle may contain an arbitrary prefix
                # from a dead worker.  Never resend it: physical state is
                # unknown and only linked baseline recovery may proceed.
                await _best_effort_close(self.store, work, "interrupted_bundle_outcome_unknown")
                try:
                    await self.store.record_work_event(
                        work,
                        "failed",
                        {"reason": "interrupted_bundle_outcome_unknown"},
                    )
                    await _request_recovery_if_safe(
                        self.store,
                        work,
                        fence,
                        "interrupted_bundle_outcome_unknown",
                    )
                except Exception:
                    return ExecutorResult(
                        "failed",
                        "interrupted_bundle_persistence_uncertain",
                        work.work_id,
                        bundle.bundle_id,
                    )
                return ExecutorResult(
                    "failed",
                    "interrupted_bundle_outcome_unknown",
                    work.work_id,
                    bundle.bundle_id,
                )
            # A completed prior bundle is restart-idempotent: skip every setter
            # and continue only its observation/confirmation barrier.

        if bundle.finished_at is None:
            if changes:

                async def persist(outcomes: tuple[ComponentCommandOutcome, ...]) -> None:
                    await self.store.record_component_outcomes(work, bundle, outcomes)

                try:
                    delivery = await self.transport.deliver(
                        _calls(changes),
                        on_state=persist,
                        expected_writer_generation=work.writer_generation,
                        expected_connection_generation=work.connection_generation,
                        work_deadline=min(work.valid_until, work.expires_at, work.claim_expires_at),
                    )
                except asyncio.CancelledError:
                    raise
                except LifecyclePersistenceError:
                    return ExecutorResult(
                        "failed",
                        "delivery_outcome_unknown",
                        work.work_id,
                        bundle.bundle_id,
                    )
                except Exception:
                    return ExecutorResult("failed", "delivery_transport_uncertain", work.work_id, bundle.bundle_id)
                if not delivery.ok:
                    reason = delivery.failure.reason if delivery.failure else "partial_or_cancelled_delivery"
                    if _executor_fault_kind(reason) is not None:
                        return ExecutorResult("failed", reason, work.work_id, bundle.bundle_id, len(changes))
                    await _best_effort_close(self.store, work, reason)
                    try:
                        await self.store.record_work_event(work, "failed", {"reason": reason})
                        await _request_recovery_if_safe(self.store, work, fence, reason)
                    except Exception:
                        return ExecutorResult(
                            "failed", "delivery_failure_persistence_uncertain", work.work_id, bundle.bundle_id
                        )
                    return ExecutorResult("failed", reason, work.work_id, bundle.bundle_id, len(changes))

                # Preserve the exact successfully delivered command set on the
                # in-memory bundle for a same-tick confirmation. On later
                # ticks the function-only bundle reader reconstructs this tuple
                # from the immutable requested-wire journal.
                bundle = replace(bundle, component_fields=tuple(change.field_name for change in changes))

            finished_at = self.clock()
            try:
                bundle = await self.store.finish_bundle(work, bundle, finished_at)
            except Exception:
                return ExecutorResult(
                    "failed",
                    "bundle_completion_uncertain",
                    work.work_id,
                    bundle.bundle_id,
                    len(changes),
                )

        try:
            epochs = await self.store.observation_epochs(work, bundle)
        except ComponentRuntimeFault as exc:
            set_component_authority_hold(exc.authority_hold_required, CANONICAL_FIELD_ORDER)
            if not exc.authority_hold_required:
                configure_component_cfg_source(
                    experiment_id=None,
                    lease_generation=None,
                    writer_generation=None,
                    connection_generation=None,
                    revisions=None,
                )
            if exc.reason == "post_delivery_observation_mismatch":
                # The mismatch L3 function has already atomically persisted
                # the raw epoch, terminal work event, fault and recovery/yield.
                disposition: WorkDisposition = "yielded" if exc.facility_authority_yielded else "failed"
                return ExecutorResult(
                    disposition,
                    "facility_rescue_yield" if exc.facility_authority_yielded else exc.reason,
                    work.work_id,
                    bundle.bundle_id,
                    len(changes),
                )
            await _best_effort_close(self.store, work, exc.reason)
            try:
                await self.store.record_work_event(work, "failed", {"reason": exc.reason})
            except Exception:
                return ExecutorResult(
                    "failed",
                    "runtime_fault_persistence_uncertain",
                    work.work_id,
                    bundle.bundle_id,
                    len(changes),
                )
            return ExecutorResult("failed", exc.reason, work.work_id, bundle.bundle_id, len(changes))
        except Exception:
            return ExecutorResult("failed", "observation_read_uncertain", work.work_id, bundle.bundle_id, len(changes))
        confirmation = validate_confirmation_epochs(
            work,
            bundle,
            epochs,
            desired,
            expected_hash,
            now=self.clock(),
        )
        if confirmation.pending:
            try:
                await self.store.record_work_event(work, "deferred", {"reason": confirmation.reason})
            except Exception:
                return ExecutorResult(
                    "failed", "confirmation_defer_uncertain", work.work_id, bundle.bundle_id, len(changes)
                )
            return ExecutorResult("delivered", confirmation.reason, work.work_id, bundle.bundle_id, len(changes))
        if not confirmation.confirmed:
            await _best_effort_close(self.store, work, confirmation.reason)
            try:
                await self.store.record_work_event(work, "failed", {"reason": confirmation.reason})
                await _request_recovery_if_safe(self.store, work, fence, confirmation.reason)
            except Exception:
                return ExecutorResult(
                    "failed", "confirmation_failure_persistence_uncertain", work.work_id, bundle.bundle_id, len(changes)
                )
            return ExecutorResult("failed", confirmation.reason, work.work_id, bundle.bundle_id, len(changes))

        confirmation_fields = bundle.component_fields
        expected_order = (
            RECOVERY_ORDER
            if work.operation_kind == WORK_KIND_RECOVERY
            else (ROLLBACK_ORDER if work.target_profile == "baseline" else ACTIVATION_ORDER)
        )
        if tuple(field for field in expected_order if field in confirmation_fields) != confirmation_fields:
            return ExecutorResult(
                "failed",
                "bundle_component_journal_order_mismatch",
                work.work_id,
                bundle.bundle_id,
                len(changes),
            )
        if work.operation_kind == WORK_KIND_RECOVERY and confirmation_fields != RECOVERY_ORDER:
            return ExecutorResult(
                "failed",
                "recovery_component_journal_incomplete",
                work.work_id,
                bundle.bundle_id,
                len(changes),
            )
        confirmed_outcomes = tuple(
            ComponentCommandOutcome(
                index=index,
                parameter=field_name,
                object_id=REGISTRY[field_name].esp_object_id or "",
                value=desired[field_name],
                entity_type=ENTITY_GRIDS[field_name].entity_type,
                status="confirmed",
                reason="two_complete_cfg_epochs",
                writer_generation=work.writer_generation,
                connection_generation=work.connection_generation,
            )
            for index, field_name in enumerate(confirmation_fields)
        )
        try:
            await self.store.record_component_outcomes(work, bundle, confirmed_outcomes)
            if work.operation_kind == WORK_KIND_RECOVERY:
                await self.store.record_work_event(
                    work,
                    "recovered",
                    {"confirmed_at": confirmation.confirmed_at.isoformat() if confirmation.confirmed_at else None},
                )
                # Recovery proof alone does not transfer writer ownership. A
                # linked baseline interposition is normally followed by the
                # parent target, while final rollback requires an explicit DB
                # closure/disable handoff. Re-read the function-only runtime
                # authority and release ordinary writers only when that
                # authoritative surface says the component no longer owns it.
                try:
                    refreshed_authority = await self.store.prepare_runtime(
                        work.experiment_id,
                        device_id=work.device_id,
                        connection_generation=fence.connection_generation,
                        writer_lease_held=fence.writer_lease_held,
                        device_write_enabled=fence.device_write_enabled,
                    )
                    if (
                        refreshed_authority.device_id != work.device_id
                        or refreshed_authority.lease_generation != work.lease_generation
                        or refreshed_authority.runtime_instance_id != work.runtime_instance_id
                        or refreshed_authority.writer_generation != work.writer_generation
                        or refreshed_authority.connection_generation != work.connection_generation
                    ):
                        raise ComponentStoreError("post-recovery runtime authority changed generation")
                except Exception:
                    set_component_authority_hold(True, CANONICAL_FIELD_ORDER)
                    return ExecutorResult(
                        "failed",
                        "recovery_authority_refresh_uncertain",
                        work.work_id,
                        bundle.bundle_id,
                        len(changes),
                    )
                set_component_authority_hold(
                    refreshed_authority.component_authority_required,
                    CANONICAL_FIELD_ORDER,
                )
                return ExecutorResult("recovered", "baseline_confirmed", work.work_id, bundle.bundle_id, len(changes))
            await self.store.open_exposure(work, bundle)
            await self.store.record_work_event(
                work,
                "completed",
                {"confirmed_at": confirmation.confirmed_at.isoformat() if confirmation.confirmed_at else None},
            )
        except Exception:
            return ExecutorResult(
                "failed", "confirmation_persistence_uncertain", work.work_id, bundle.bundle_id, len(changes)
            )
        return ExecutorResult("confirmed", "exposure_opened", work.work_id, bundle.bundle_id, len(changes))


async def component_experiment_worker(
    pool: object,
    *,
    store: ComponentExperimentStore | None = None,
    transport: ComponentTransport | None = None,
    fence_provider: RuntimeFenceProvider = runtime_fence,
) -> ExecutorResult:
    """One scheduler tick; environment guard precedes all DB/store access."""
    enabled, reason = component_experiment_gate()
    if not enabled:
        # A running process that has acquired experiment authority cannot
        # release it merely because both declarative identifiers disappeared
        # in the wrong order. Correct rollback proves baseline/recovery first,
        # which explicitly releases this hold; otherwise a clean OFF restart is
        # required and starts from the safe-default false state.
        retain_existing_authority = component_authority_hold()[0]
        set_component_authority_hold(
            component_startup_hold_required() or retain_existing_authority,
            CANONICAL_FIELD_ORDER,
        )
        if not retain_existing_authority:
            configure_component_cfg_source(
                experiment_id=None,
                lease_generation=None,
                writer_generation=None,
                connection_generation=None,
                revisions=None,
            )
        return ExecutorResult("idle", reason)
    experiment_id = active_experiment_id()
    if experiment_id is None:  # defensive; component_experiment_gate checked it
        return ExecutorResult("idle", "active_experiment_id_missing_or_invalid")

    if store is None:
        if _store_factory is None:
            # Enabled component mode plus an unbound data adapter is unknown
            # authority, not permission for legacy writers.  Retain the
            # startup deny until bounded L3 authority can be resolved.
            set_component_authority_hold(True, CANONICAL_FIELD_ORDER)
            log.error(
                "component_executor L3 store adapter is not bound; claiming zero work (required functions: %s/%s/%s)",
                L3_RESOLVE_READINESS,
                L3_RESOLVE_RANDOMIZED,
                L3_RESOLVE_RECOVERY,
            )
            return ExecutorResult("idle", "l3_store_adapter_unbound")
        if _store_factory is AsyncpgComponentExperimentStore and not bool(
            getattr(pool, "component_executor_role_attested", False)
        ):
            set_component_authority_hold(True, CANONICAL_FIELD_ORDER)
            log.error("component_executor restricted database role is not attested; claiming zero work")
            return ExecutorResult("idle", "component_database_role_unattested")
        store = _store_factory(pool)

    base_fence = fence_provider()
    device_id = policy_device_id(os.environ.get("GREENHOUSE_ID", "vallery"))
    prior_reporter = _runtime_reporters.get(_runtime_fault_key(experiment_id, device_id))
    try:
        authority = await store.prepare_runtime(
            experiment_id,
            device_id=device_id,
            connection_generation=base_fence.connection_generation,
            writer_lease_held=base_fence.writer_lease_held,
            device_write_enabled=base_fence.device_write_enabled,
        )
    except ComponentRuntimeFault as exc:
        set_component_authority_hold(exc.authority_hold_required, CANONICAL_FIELD_ORDER)
        if not exc.authority_hold_required:
            configure_component_cfg_source(
                experiment_id=None,
                lease_generation=None,
                writer_generation=None,
                connection_generation=None,
                revisions=None,
            )
        log.warning("component_executor runtime_fault reason=%s", exc.reason)
        return ExecutorResult("failed", exc.reason)
    except Exception as exc:
        # Enabled component mode plus unknown DB authority must deny ordinary
        # writers.  Facility rescue is external and retains precedence; no
        # component setter is attempted on uncertainty. Preserve completed raw
        # evidence for retry; a later successful prepare clears it if the
        # experiment/runtime lineage changed.
        set_component_authority_hold(True, CANONICAL_FIELD_ORDER)
        _queue_runtime_fault(
            experiment_id,
            device_id,
            fault_kind="db_outage",
            reason="prepare_runtime_database_uncertain",
            reporter=prior_reporter,
        )
        log.error("component_executor runtime_context_failed error=%s", type(exc).__name__)
        return ExecutorResult("failed", "database_runtime_authority_uncertain")

    reporter = _reporter_from_authority(experiment_id, device_id, authority)
    _remember_runtime_reporter(reporter)
    pending = _pending_runtime_faults.get(_runtime_fault_key(experiment_id, device_id))
    if pending is not None:
        return await _stop_for_runtime_fault(
            store,
            experiment_id=experiment_id,
            device_id=device_id,
            fault_kind=pending.fault_kind,
            reason=pending.reason,
            reporter=reporter,
        )
    if not base_fence.writer_lease_held:
        return await _stop_for_runtime_fault(
            store,
            experiment_id=experiment_id,
            device_id=device_id,
            fault_kind="lease_loss",
            reason="writer_lease_not_held",
            reporter=reporter,
        )
    if base_fence.connection_generation != int(shared.transport_generation):
        return await _stop_for_runtime_fault(
            store,
            experiment_id=experiment_id,
            device_id=device_id,
            fault_kind="connection_generation_changed",
            reason="connection_generation_changed_before_monitor",
            reporter=reporter,
        )

    set_component_authority_hold(authority.component_authority_required, CANONICAL_FIELD_ORDER)
    source_active = (
        (authority.component_authority_required or authority.observation_source_required)
        and base_fence.writer_lease_held
        and base_fence.connection_generation == int(shared.transport_generation)
    )
    configure_component_cfg_source(
        experiment_id=experiment_id if source_active else None,
        lease_generation=authority.lease_generation if source_active else None,
        writer_generation=authority.writer_generation if source_active else None,
        connection_generation=base_fence.connection_generation if source_active else None,
        revisions=authority.revisions if source_active else None,
    )
    try:
        monitor_status = await _persist_and_monitor_open_exposure(
            store,
            experiment_id=experiment_id,
            device_id=device_id,
            authority=authority,
            fence=base_fence,
        )
    except ComponentRuntimeFault as exc:
        set_component_authority_hold(exc.authority_hold_required, CANONICAL_FIELD_ORDER)
        if not exc.authority_hold_required:
            configure_component_cfg_source(
                experiment_id=None,
                lease_generation=None,
                writer_generation=None,
                connection_generation=None,
                revisions=None,
            )
        log.warning("component_executor runtime_fault reason=%s", exc.reason)
        return ExecutorResult("failed", exc.reason)
    except Exception as exc:
        set_component_authority_hold(True, CANONICAL_FIELD_ORDER)
        _queue_runtime_fault(
            experiment_id,
            device_id,
            fault_kind="db_outage",
            reason="open_exposure_monitor_database_uncertain",
            reporter=reporter,
        )
        # The runtime authority was already resolved for this exact lineage.
        # Preserve its completed raw epochs for an idempotent retry; the next
        # successful prepare clears them automatically if any identity changed.
        log.error("component_executor open_exposure_monitor_failed error=%s", type(exc).__name__)
        return ExecutorResult("failed", "open_exposure_monitor_uncertain")

    latest_fence = fence_provider()
    if not latest_fence.writer_lease_held:
        return await _stop_for_runtime_fault(
            store,
            experiment_id=experiment_id,
            device_id=device_id,
            fault_kind="lease_loss",
            reason="writer_lease_lost_after_monitor",
            reporter=reporter,
        )
    if (
        latest_fence.connection_generation != base_fence.connection_generation
        or latest_fence.connection_generation != int(shared.transport_generation)
    ):
        return await _stop_for_runtime_fault(
            store,
            experiment_id=experiment_id,
            device_id=device_id,
            fault_kind="connection_generation_changed",
            reason="connection_generation_changed_after_monitor",
            reporter=reporter,
        )
    monitor_fault = _open_exposure_runtime_fault(monitor_status, authority, latest_fence)
    if monitor_fault is not None:
        fault_kind, fault_reason = monitor_fault
        return await _stop_for_runtime_fault(
            store,
            experiment_id=experiment_id,
            device_id=device_id,
            fault_kind=fault_kind,
            reason=fault_reason,
            reporter=reporter,
        )
    if not latest_fence.device_write_enabled and monitor_status is not None and monitor_status.exposure_is_open:
        return await _stop_for_runtime_fault(
            store,
            experiment_id=experiment_id,
            device_id=device_id,
            fault_kind="protocol_deviation",
            reason="device_writes_disabled_with_open_exposure",
            reporter=reporter,
        )
    fence = replace(
        latest_fence,
        lease_generation=authority.lease_generation,
        writer_generation=authority.writer_generation,
    )
    executor = ConfirmedComponentExecutor(store, transport=transport)
    result = await executor.run_once(experiment_id, fence)
    fault_kind = _executor_fault_kind(result.reason)
    if fault_kind is not None:
        return await _stop_for_runtime_fault(
            store,
            experiment_id=experiment_id,
            device_id=device_id,
            fault_kind=fault_kind,
            reason=result.reason,
            reporter=reporter,
            work_id=result.work_id,
        )
    if result.disposition == "yielded":
        configure_component_cfg_source(
            experiment_id=None,
            lease_generation=None,
            writer_generation=None,
            connection_generation=None,
            revisions=None,
        )
        return result
    try:
        request_component_state_replay()
    except Exception:
        return await _stop_for_runtime_fault(
            store,
            experiment_id=experiment_id,
            device_id=device_id,
            fault_kind="device_lost",
            reason="state_replay_request_failed",
            reporter=reporter,
            work_id=result.work_id,
        )
    return result


def prime_component_startup_hold() -> None:
    """Synchronously deny ordinary writes before any startup task can race L3."""
    retain_existing_authority = component_authority_hold()[0]
    set_component_authority_hold(
        component_startup_hold_required() or retain_existing_authority,
        CANONICAL_FIELD_ORDER,
    )


__all__ = [
    "AsyncpgComponentExperimentStore",
    "AttestedComponentPool",
    "BundleReservation",
    "ComponentExperimentStore",
    "ConfirmedComponentExecutor",
    "DeliveryBundle",
    "ExecutorResult",
    "ObservationEpoch",
    "ObservedComponent",
    "ResolvedWork",
    "RevisionSet",
    "RuntimeFence",
    "RuntimeAuthority",
    "RuntimeExposureStatus",
    "WorkSignals",
    "clear_component_entity_inventory",
    "component_cfg_source_epochs",
    "component_entity_grid_attestation",
    "component_experiment_worker",
    "configure_component_cfg_source",
    "create_component_experiment_pool",
    "attest_component_safe_startup",
    "install_component_store_factory",
    "prime_component_startup_hold",
    "record_component_cfg_readback",
    "record_component_device_uptime",
    "record_component_entity_inventory",
    "record_component_grid_firmware_revision",
    "request_component_state_replay",
    "validate_current_observation",
    "validate_confirmation_epochs",
]
