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
import os
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol
from uuid import UUID, uuid4

import asyncpg
import shared
from esp32_push import (
    ComponentBundleCall,
    ComponentBundleResult,
    ComponentCommandOutcome,
    ComponentStateCallback,
    LifecyclePersistenceError,
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
    normalize_component_value,
    validate_routine_target,
    validate_work_phase,
)
from verdify_schemas.experiment_config import active_experiment_id, component_experiment_gate, policy_device_id
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

MAX_SNAPSHOT_AGE = timedelta(seconds=90)
MIN_EPOCH_SEPARATION = timedelta(seconds=30)
MAX_EPOCH_SKEW = timedelta(seconds=60)
COMPONENT_EXECUTOR_INTERVAL_S = 15
COMPONENT_EXECUTOR_ACTOR = "verdify-component-executor-v2"
RUNTIME_INSTANCE_ID = str(uuid4())
_SOURCE_EPOCH_BUFFER_SIZE = 8

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


@dataclass(frozen=True)
class WorkSignals:
    rebooted: bool = False
    reset_detected: bool = False
    reconnected: bool = False
    foreign_writer: bool = False
    facility_rescue_active: bool = False
    facility_recovery_authorized: bool = False
    nonbaseline_reentry_forbidden: bool = False

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
    global _cfg_source_identity
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

    grid_value: object = value
    if ENTITY_GRIDS[field_name].entity_type == "switch":
        if value in (0, 0.0, False):
            grid_value = False
        elif value in (1, 1.0, True):
            grid_value = True
        else:
            return False
    try:
        normalized = normalize_component_value(field_name, grid_value)
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
        completed_at=max(timestamps.values()),
    )
    _cfg_source_epochs.append(epoch)
    _cfg_source_last_completed_at.clear()
    _cfg_source_last_completed_at.update(timestamps)
    _cfg_source_pending.clear()
    return True


def component_cfg_source_epochs() -> tuple[RawCfgSourceEpoch, ...]:
    """Return immutable source-owned epochs for the adapter; never relabel."""
    return tuple(_cfg_source_epochs)


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


_CLOSE_REASON_MAP = {
    "device_reboot": "reboot",
    "device_reset": "protocol_deviation",
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
        writer_value = _row_value(row, "writer_generation", 0)
        authority = RuntimeAuthority(
            lease_generation=int(_row_value(row, "lease_generation")),
            writer_generation=0 if writer_value is None else int(writer_value),
            device_id=str(_row_value(row, "device_id")),
            component_authority_required=bool(_row_value(row, "authority_hold_required")),
            observation_source_required=bool(_row_value(row, "observation_source_required")),
            rescue_authorized=bool(_row_value(row, "rescue_authorized")),
            revisions=_revision_from_row(row),
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
                rebooted=bool(_row_value(row, "restart_detected")),
                reset_detected=bool(_row_value(signals_value, "reset_detected", False)),
                reconnected=bool(_row_value(row, "reconnect_detected")),
                foreign_writer=bool(_row_value(signals_value, "foreign_writer", False)),
                facility_rescue_active=bool(_row_value(signals_value, "facility_rescue_active", False)),
                facility_recovery_authorized=bool(_row_value(row, "rescue_authorized")),
                nonbaseline_reentry_forbidden=bool(_row_value(row, "no_reentry")),
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
                or raw.values != expected
                or min(raw.observed_at.values()) <= bundle.finished_at
            ):
                continue
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


StoreFactory = Callable[[object], ComponentExperimentStore]
RuntimeFenceProvider = Callable[[], RuntimeFence]
_store_factory: StoreFactory | None = AsyncpgComponentExperimentStore


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
        writer_lease_held=shared.writer_lease_held(),
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
            return ExecutorResult("failed", "nonbaseline_reentry_forbidden", work.work_id)

        if not fence.writer_lease_held or not fence.device_write_enabled:
            reason = "writer_lease_not_held" if not fence.writer_lease_held else "device_writes_disabled"
            await _best_effort_close(self.store, work, reason)
            return ExecutorResult("deferred", reason, work.work_id)
        if fence.connection_generation != int(shared.transport_generation):
            await _best_effort_close(self.store, work, "connection_generation_changed")
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
                await _best_effort_close(self.store, work, "current_observation_uncertain")
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
                    await _best_effort_close(self.store, work, "delivery_outcome_unknown")
                    raise
                except LifecyclePersistenceError:
                    await _best_effort_close(self.store, work, "delivery_outcome_unknown")
                    return ExecutorResult(
                        "failed",
                        "delivery_outcome_unknown",
                        work.work_id,
                        bundle.bundle_id,
                    )
                except Exception:
                    await _best_effort_close(self.store, work, "delivery_transport_uncertain")
                    return ExecutorResult("failed", "delivery_transport_uncertain", work.work_id, bundle.bundle_id)
                if not delivery.ok:
                    reason = delivery.failure.reason if delivery.failure else "partial_or_cancelled_delivery"
                    await _best_effort_close(self.store, work, reason)
                    try:
                        await self.store.record_work_event(work, "failed", {"reason": reason})
                        await _request_recovery_if_safe(self.store, work, fence, reason)
                    except Exception:
                        return ExecutorResult(
                            "failed", "delivery_failure_persistence_uncertain", work.work_id, bundle.bundle_id
                        )
                    return ExecutorResult("failed", reason, work.work_id, bundle.bundle_id, len(changes))

            finished_at = self.clock()
            try:
                bundle = await self.store.finish_bundle(work, bundle, finished_at)
            except Exception:
                await _best_effort_close(self.store, work, "bundle_completion_uncertain")
                return ExecutorResult(
                    "failed",
                    "bundle_completion_uncertain",
                    work.work_id,
                    bundle.bundle_id,
                    len(changes),
                )

        try:
            epochs = await self.store.observation_epochs(work, bundle)
        except Exception:
            await _best_effort_close(self.store, work, "observation_read_uncertain")
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

        confirmed_outcomes = tuple(
            ComponentCommandOutcome(
                index=index,
                parameter=change.field_name,
                object_id=change.object_id,
                value=change.value,
                entity_type=change.entity_type,
                status="confirmed",
                reason="two_complete_cfg_epochs",
                writer_generation=work.writer_generation,
                connection_generation=work.connection_generation,
            )
            for index, change in enumerate(changes)
        )
        try:
            await self.store.record_component_outcomes(work, bundle, confirmed_outcomes)
            if work.operation_kind == WORK_KIND_RECOVERY:
                await self.store.record_work_event(
                    work,
                    "recovered",
                    {"confirmed_at": confirmation.confirmed_at.isoformat() if confirmation.confirmed_at else None},
                )
                set_component_authority_hold(False)
                return ExecutorResult("recovered", "baseline_confirmed", work.work_id, bundle.bundle_id, len(changes))
            await self.store.open_exposure(work, bundle)
            await self.store.record_work_event(
                work,
                "completed",
                {"confirmed_at": confirmation.confirmed_at.isoformat() if confirmation.confirmed_at else None},
            )
        except Exception:
            await _best_effort_close(self.store, work, "confirmation_persistence_uncertain")
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
        set_component_authority_hold(False)
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
            set_component_authority_hold(False)
            log.error(
                "component_executor L3 store adapter is not bound; claiming zero work (required functions: %s/%s/%s)",
                L3_RESOLVE_READINESS,
                L3_RESOLVE_RANDOMIZED,
                L3_RESOLVE_RECOVERY,
            )
            return ExecutorResult("idle", "l3_store_adapter_unbound")
        store = _store_factory(pool)

    base_fence = fence_provider()
    device_id = policy_device_id(os.environ.get("GREENHOUSE_ID", "vallery"))
    try:
        authority = await store.prepare_runtime(
            experiment_id,
            device_id=device_id,
            connection_generation=base_fence.connection_generation,
            writer_lease_held=base_fence.writer_lease_held,
            device_write_enabled=base_fence.device_write_enabled,
        )
    except Exception as exc:
        # Enabled component mode plus unknown DB authority must deny ordinary
        # writers.  Facility rescue is external and retains precedence; no
        # component setter or source receipt is attempted on uncertainty.
        set_component_authority_hold(True, CANONICAL_FIELD_ORDER)
        configure_component_cfg_source(
            experiment_id=None,
            lease_generation=None,
            writer_generation=None,
            connection_generation=None,
            revisions=None,
        )
        log.error("component_executor runtime_context_failed error=%s", type(exc).__name__)
        return ExecutorResult("failed", "database_runtime_authority_uncertain")

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
    if not base_fence.writer_lease_held:
        return ExecutorResult("deferred", "writer_lease_not_held")
    if base_fence.connection_generation != int(shared.transport_generation):
        return ExecutorResult("failed", "connection_generation_changed")
    fence = replace(
        base_fence,
        lease_generation=authority.lease_generation,
        writer_generation=authority.writer_generation,
    )
    executor = ConfirmedComponentExecutor(store, transport=transport)
    return await executor.run_once(experiment_id, fence)


__all__ = [
    "AsyncpgComponentExperimentStore",
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
    "WorkSignals",
    "component_cfg_source_epochs",
    "component_experiment_worker",
    "configure_component_cfg_source",
    "install_component_store_factory",
    "record_component_cfg_readback",
    "validate_current_observation",
    "validate_confirmation_epochs",
]
