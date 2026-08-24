"""Fault-complete fake-store tests for the confirmed-component executor."""

from __future__ import annotations

import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

_INGESTOR_PATH = str(Path(__file__).resolve().parents[1] / "ingestor")
if _INGESTOR_PATH not in sys.path:
    sys.path.insert(0, _INGESTOR_PATH)

import shared  # noqa: E402
import tasks.component_experiment as component_experiment  # noqa: E402
from esp32_push import (  # noqa: E402
    ComponentBundleResult,
    ComponentCommandOutcome,
    component_authority_hold,
    set_component_authority_hold,
)
from tasks.component_experiment import (  # noqa: E402
    RUNTIME_INSTANCE_ID,
    BundleReservation,
    ComponentRuntimeFault,
    ConfirmedComponentExecutor,
    DeliveryBundle,
    ObservationEpoch,
    ObservedComponent,
    ResolvedWork,
    RevisionSet,
    RuntimeAuthority,
    RuntimeExposureStatus,
    RuntimeFaultReceipt,
    RuntimeFence,
    WorkSignals,
    attest_component_safe_startup,
    component_cfg_source_epochs,
    component_experiment_worker,
    configure_component_cfg_source,
    create_component_experiment_pool,
    prime_component_startup_hold,
    record_component_cfg_readback,
    record_component_device_uptime,
)

from verdify_schemas.component_executor import CANONICAL_FIELD_ORDER, ENTITY_GRIDS

NOW = datetime(2026, 8, 23, 22, 0, tzinfo=UTC)
EXPERIMENT_ID = "11111111-1111-4111-8111-111111111111"
WORK_ID = "22222222-2222-4222-8222-222222222222"
REVISION = RevisionSet(
    bundle_sha256="c" * 64,
    firmware_revision="historical-source-parity-live-unverified",
    config_revision="config-r1",
    registry_revision="registry-r1",
    grid_revision="source-grid-r1",
)


class AsyncAcquire:
    def __init__(self, connection) -> None:
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *_args):
        return False


def minimum_state() -> dict[str, bool | float]:
    return {
        field: False if grid.entity_type == "switch" else float(grid.minimum) for field, grid in ENTITY_GRIDS.items()
    }


def maximum_state() -> dict[str, bool | float]:
    return {
        field: True if grid.entity_type == "switch" else float(grid.maximum) for field, grid in ENTITY_GRIDS.items()
    }


def work(**overrides) -> ResolvedWork:
    baseline = minimum_state()
    target = dict(baseline)
    target["mister_all_kpa"] = 1.05
    values = dict(
        experiment_id=EXPERIMENT_ID,
        work_id=WORK_ID,
        assignment_id=WORK_ID,
        operation_kind="randomized_assignment",
        execution_phase="randomized",
        admission_state="open",
        lifecycle_status="running",
        protocol_version=2,
        transport_kind="legacy_components_v1",
        target_profile="moderate",
        target_state_content_sha256="a" * 64,
        baseline_state_content_sha256="b" * 64,
        target_state=target,
        baseline_state=baseline,
        revisions=REVISION,
        expected_revision_bundle_sha256=REVISION.bundle_sha256,
        lease_generation=5,
        runtime_instance_id=RUNTIME_INSTANCE_ID,
        writer_generation=5,
        connection_generation=7,
        valid_from=NOW - timedelta(minutes=1),
        valid_until=NOW + timedelta(hours=1),
        expires_at=NOW + timedelta(hours=1),
        claim_expires_at=NOW + timedelta(minutes=2),
        resolved_at=NOW,
        device_id="greenhouse-controller",
        baseline_interposition_confirmed=True,
        signals=WorkSignals(),
    )
    values.update(overrides)
    return ResolvedWork(**values)


def preview_work(**overrides) -> ResolvedWork:
    values = dict(
        assignment_id=None,
        operation_kind="shadow_preview",
        execution_phase="shadow",
        admission_state="closed",
        lifecycle_status="draft",
        target_profile="baseline",
        target_state=minimum_state(),
        baseline_state=minimum_state(),
        target_state_content_sha256="b" * 64,
        baseline_interposition_confirmed=False,
    )
    values.update(overrides)
    return work(**values)


def recovery_work(**overrides) -> ResolvedWork:
    baseline = minimum_state()
    values = dict(
        assignment_id=None,
        operation_kind="baseline_recovery",
        execution_phase="randomized",
        admission_state="baseline_recovery",
        lifecycle_status="paused",
        target_profile="baseline",
        target_state=baseline,
        baseline_state=baseline,
        target_state_content_sha256="b" * 64,
        baseline_interposition_confirmed=False,
    )
    values.update(overrides)
    return work(**values)


def observation(
    owner: ResolvedWork,
    state,
    *,
    observed_at: datetime = NOW - timedelta(seconds=10),
    source_epoch_id: str = "33333333-3333-4333-8333-333333333333",
) -> ObservationEpoch:
    return ObservationEpoch(
        source_epoch_id=source_epoch_id,
        experiment_id=owner.experiment_id,
        work_id="44444444-4444-4444-8444-444444444444",
        bundle_id="55555555-5555-4555-8555-555555555555",
        execution_phase=owner.execution_phase,
        operation_kind=owner.operation_kind,
        identity_source="derived_cfg_readbacks_v1",
        state_content_sha256="0" * 64,
        observations={field: ObservedComponent(value, observed_at) for field, value in state.items()},
        persisted_at=observed_at,
        revisions=owner.revisions,
        runtime_instance_id=owner.runtime_instance_id,
        writer_generation=owner.writer_generation,
        connection_generation=owner.connection_generation,
    )


class SequenceClock:
    def __init__(self, *moments: datetime) -> None:
        self.moments = list(moments)
        self.last = moments[-1]

    def __call__(self) -> datetime:
        if self.moments:
            self.last = self.moments.pop(0)
        return self.last


class FakeStore:
    def __init__(self, item: ResolvedWork | None, current: ObservationEpoch | None = None) -> None:
        self.item = item
        self.current = current
        self.claims = 0
        self.previewed = 0
        self.bundle: DeliveryBundle | None = None
        self.force_existing_bundle: DeliveryBundle | None = None
        self.events: list[tuple[str, dict]] = []
        self.outcomes: list[ComponentCommandOutcome] = []
        self.closed: list[str] = []
        self.recoveries: list[str] = []
        self.opened: list[str] = []
        self.epoch_mode = "valid"
        self.raise_claim = False
        self.raise_prepare = False
        self.raise_outcomes = False
        self.raise_monitor = False
        self.raise_runtime_snapshot = False
        self.prepares = 0
        self.monitor_calls = 0
        self.runtime_snapshots = []
        self.worker_order: list[str] = []
        self.monitor_status: RuntimeExposureStatus | None = None
        self.fault_reports = []
        self.raise_runtime_fault = False
        self.runtime_fault_hold_required = True
        self.runtime_fault_facility_yielded = False
        self.runtime_authority_required = True

    async def prepare_runtime(
        self,
        experiment_id,
        *,
        device_id,
        connection_generation,
        writer_lease_held,
        device_write_enabled,
    ):
        self.prepares += 1
        self.worker_order.append("prepare")
        if self.raise_prepare:
            raise RuntimeError("db down")
        assert experiment_id == EXPERIMENT_ID
        assert connection_generation == 7
        authority_work = self.item
        return RuntimeAuthority(
            lease_generation=authority_work.lease_generation if authority_work is not None else 5,
            writer_generation=authority_work.writer_generation if authority_work is not None else 5,
            device_id=device_id,
            component_authority_required=self.runtime_authority_required,
            observation_source_required=True,
            rescue_authorized=False,
            revisions=authority_work.revisions if authority_work is not None else REVISION,
            runtime_instance_id=(
                authority_work.runtime_instance_id if authority_work is not None else RUNTIME_INSTANCE_ID
            ),
            connection_generation=connection_generation,
        )

    async def report_runtime_fault(
        self,
        experiment_id,
        *,
        device_id,
        fault_report_id,
        expected_lease_generation,
        runtime_instance_id,
        writer_generation,
        connection_generation,
        fault_kind,
        reason,
    ):
        self.worker_order.append("runtime_fault")
        report = (
            experiment_id,
            device_id,
            fault_report_id,
            expected_lease_generation,
            runtime_instance_id,
            writer_generation,
            connection_generation,
            fault_kind,
            reason,
        )
        self.fault_reports.append(report)
        if self.raise_runtime_fault:
            raise RuntimeError("db timeout after unknown commit")
        return RuntimeFaultReceipt(
            fault_report_id=fault_report_id,
            close_reason="reconnect" if fault_kind == "connection_generation_changed" else fault_kind,
            recovery_work_id="99999999-9999-4999-8999-999999999999",
            admission_state_after="baseline_recovery",
            authority_hold_required=self.runtime_fault_hold_required,
            facility_authority_yielded=self.runtime_fault_facility_yielded,
            recorded_at=NOW,
        )

    async def record_runtime_snapshot(self, raw, *, device_id):
        self.worker_order.append("runtime_snapshot")
        if self.raise_runtime_snapshot:
            raise RuntimeError("db down")
        self.runtime_snapshots.append((raw, device_id))

    async def monitor_open_exposure(self, experiment_id, *, device_id, lease_generation):
        self.monitor_calls += 1
        self.worker_order.append("monitor")
        if self.raise_monitor:
            raise RuntimeError("db down")
        assert experiment_id == EXPERIMENT_ID
        assert lease_generation == 5
        return self.monitor_status

    async def claim_next(
        self,
        experiment_id,
        *,
        lease_generation,
        writer_generation,
        connection_generation,
    ):
        self.claims += 1
        self.worker_order.append("claim")
        if self.raise_claim:
            raise RuntimeError("db down")
        return self.item

    async def complete_preview(self, item):
        self.previewed += 1

    async def current_observation(self, item):
        return self.current

    async def reserve_bundle(self, item, *, bundle_id, purpose, expected_state_content_sha256):
        if self.force_existing_bundle is not None:
            self.bundle = self.force_existing_bundle
            return BundleReservation(self.bundle, False)
        if self.bundle is not None:
            return BundleReservation(self.bundle, False)
        self.bundle = DeliveryBundle(
            bundle_id=bundle_id,
            work_id=item.work_id,
            purpose=purpose,
            expected_state_content_sha256=expected_state_content_sha256,
            writer_generation=item.writer_generation,
            connection_generation=item.connection_generation,
            revision_bundle_sha256=item.revisions.bundle_sha256,
            reserved_at=NOW,
        )
        return BundleReservation(self.bundle, True)

    async def finish_bundle(self, item, bundle, finished_at):
        self.bundle = replace(bundle, finished_at=finished_at)
        return self.bundle

    async def record_component_outcomes(self, item, bundle, outcomes):
        if self.raise_outcomes:
            raise RuntimeError("db down")
        self.outcomes.extend(outcomes)
        requested = tuple(outcome.parameter for outcome in outcomes if outcome.status == "requested")
        if requested and self.bundle is not None:
            self.bundle = replace(self.bundle, component_fields=requested)

    async def observation_epochs(self, item, bundle):
        if self.epoch_mode == "reset_fault":
            raise ComponentRuntimeFault("device_reset_detected")
        if self.epoch_mode == "atomic_mismatch_yield":
            raise ComponentRuntimeFault(
                "post_delivery_observation_mismatch",
                authority_hold_required=False,
                facility_authority_yielded=True,
            )
        if self.epoch_mode == "pending":
            return []
        assert bundle.finished_at is not None
        state = item.baseline_state if item.operation_kind == "baseline_recovery" else item.target_state
        first_at = bundle.finished_at + timedelta(seconds=5)
        second_at = bundle.finished_at + timedelta(seconds=40)
        if self.epoch_mode == "cached":
            second_at = first_at
        if self.epoch_mode == "stale":
            first_at = bundle.finished_at + timedelta(seconds=1)
            second_at = bundle.finished_at + timedelta(seconds=31)
        first = self._confirmation_epoch(item, bundle, state, first_at, "66666666-6666-4666-8666-666666666666")
        second_state = dict(state)
        if self.epoch_mode == "mismatch":
            second_state["mister_all_kpa"] = 1.1
        second = self._confirmation_epoch(
            item,
            bundle,
            second_state,
            second_at,
            "77777777-7777-4777-8777-777777777777",
        )
        if self.epoch_mode == "wrong_kind":
            second = replace(second, operation_kind="commissioning_canary")
        if self.epoch_mode == "wrong_revision":
            second = replace(second, revisions=replace(item.revisions, config_revision="wrong"))
        return [first, second]

    def _confirmation_epoch(self, item, bundle, state, at, epoch_id):
        return ObservationEpoch(
            source_epoch_id=epoch_id,
            experiment_id=item.experiment_id,
            work_id=item.work_id,
            bundle_id=bundle.bundle_id,
            execution_phase=item.execution_phase,
            operation_kind=item.operation_kind,
            identity_source="derived_cfg_readbacks_v1",
            state_content_sha256=bundle.expected_state_content_sha256,
            observations={field: ObservedComponent(value, at) for field, value in state.items()},
            persisted_at=at + timedelta(seconds=1),
            revisions=item.revisions,
            runtime_instance_id=item.runtime_instance_id,
            writer_generation=item.writer_generation,
            connection_generation=item.connection_generation,
        )

    async def record_work_event(self, item, event_kind, detail):
        self.events.append((event_kind, dict(detail)))

    async def open_exposure(self, item, bundle):
        exposure_id = "88888888-8888-4888-8888-888888888888"
        self.opened.append(exposure_id)
        return exposure_id

    async def close_exposure(self, item, reason):
        self.closed.append(reason)

    async def request_recovery(self, item, reason):
        self.recoveries.append(reason)
        return "99999999-9999-4999-8999-999999999999"


class FakeTransport:
    def __init__(self, *, failure_index: int | None = None, failure_reason: str = "injected") -> None:
        self.calls = []
        self.failure_index = failure_index
        self.failure_reason = failure_reason

    async def deliver(
        self,
        calls,
        *,
        on_state,
        expected_writer_generation,
        expected_connection_generation,
        work_deadline,
    ):
        assert work_deadline.tzinfo is not None
        self.calls.extend(calls)
        requested = tuple(
            ComponentCommandOutcome(
                index=index,
                parameter=call.parameter,
                object_id=call.object_id,
                value=call.value,
                entity_type=call.entity_type,
                status="requested",
                reason="fake",
                writer_generation=expected_writer_generation,
                connection_generation=expected_connection_generation,
            )
            for index, call in enumerate(calls)
        )
        queued = tuple(replace(outcome, status="queued") for outcome in requested)
        await on_state(requested)
        await on_state(queued)
        terminal = []
        for index, outcome in enumerate(requested):
            if self.failure_index == index:
                terminal.append(replace(outcome, status="failed", reason=self.failure_reason))
                terminal.extend(
                    replace(later, status="cancelled", reason="prior_failure") for later in requested[index + 1 :]
                )
                break
            terminal.append(replace(outcome, status="sent"))
        await on_state(tuple(terminal))
        return ComponentBundleResult(tuple(terminal), expected_connection_generation)


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(shared, "transport_generation", 7)
    monkeypatch.setattr(component_experiment, "physical_execution_qualified", lambda _grid: True)
    monkeypatch.setattr(component_experiment, "request_component_state_replay", lambda: False)
    component_experiment._runtime_reporters.clear()
    component_experiment._pending_runtime_faults.clear()
    set_component_authority_hold(False)
    configure_component_cfg_source(
        experiment_id=None,
        lease_generation=None,
        writer_generation=None,
        connection_generation=None,
        revisions=None,
    )
    for key in (
        "VERDIFY_COMPONENT_EXPERIMENT_ENABLED",
        "VERDIFY_POLICY_VECTOR_MODE",
        "VERDIFY_ACTIVE_EXPERIMENT_ID",
    ):
        monkeypatch.delenv(key, raising=False)
    yield
    component_experiment._runtime_reporters.clear()
    component_experiment._pending_runtime_faults.clear()
    set_component_authority_hold(False)
    configure_component_cfg_source(
        experiment_id=None,
        lease_generation=None,
        writer_generation=None,
        connection_generation=None,
        revisions=None,
    )


def fence(**overrides) -> RuntimeFence:
    values = dict(
        lease_generation=5,
        writer_generation=5,
        connection_generation=7,
        writer_lease_held=True,
        device_write_enabled=True,
    )
    values.update(overrides)
    return RuntimeFence(**values)


def runtime_status(**overrides) -> RuntimeExposureStatus:
    values = dict(
        exposure_id="88888888-8888-4888-8888-888888888888",
        work_id=WORK_ID,
        exposure_is_open=True,
        close_reason=None,
        recovery_work_id=None,
        current_runtime_instance_id=RUNTIME_INSTANCE_ID,
        current_writer_generation=5,
        current_connection_generation=7,
        source_epoch_id=None,
        source_runtime_instance_id=None,
        source_writer_generation=None,
        source_connection_generation=None,
        common_field_drift=False,
        cfg_drift=False,
        lineage_drift=False,
        reset_detected=False,
        foreign_writer=False,
        exposure_started_at=NOW - timedelta(seconds=30),
        last_observed_at=None,
        resolved_at=NOW,
    )
    values.update(overrides)
    return RuntimeExposureStatus(**values)


def executor(store, transport=None):
    return ConfirmedComponentExecutor(
        store,
        transport=transport or FakeTransport(),
        clock=SequenceClock(NOW, NOW + timedelta(seconds=1), NOW + timedelta(seconds=90)),
    )


@pytest.mark.asyncio
async def test_environment_gate_precedes_store_and_transport_access(monkeypatch):
    class ForbiddenStore:
        def __getattr__(self, name):
            raise AssertionError(f"store accessed: {name}")

    result = await component_experiment_worker(object(), store=ForbiddenStore())
    assert result.reason == "component_capability_off"
    monkeypatch.setenv("VERDIFY_COMPONENT_EXPERIMENT_ENABLED", "enabled")
    monkeypatch.setenv("VERDIFY_POLICY_VECTOR_MODE", "live")
    monkeypatch.setenv("VERDIFY_ACTIVE_EXPERIMENT_ID", EXPERIMENT_ID)
    result = await component_experiment_worker(object(), store=ForbiddenStore())
    assert result.reason == "generalized_vector_mode_not_exactly_off"
    assert component_authority_hold()[0] is True


@pytest.mark.asyncio
async def test_misordered_flag_and_id_clear_cannot_release_existing_authority(monkeypatch):
    class ForbiddenStore:
        def __getattr__(self, name):
            raise AssertionError(f"store accessed: {name}")

    set_component_authority_hold(True, CANONICAL_FIELD_ORDER)
    monkeypatch.setenv("VERDIFY_COMPONENT_EXPERIMENT_ENABLED", "off")
    monkeypatch.delenv("VERDIFY_ACTIVE_EXPERIMENT_ID", raising=False)

    result = await component_experiment_worker(object(), store=ForbiddenStore())

    assert result.reason == "component_capability_off"
    assert component_authority_hold() == (True, frozenset(CANONICAL_FIELD_ORDER))


@pytest.mark.asyncio
async def test_misordered_in_process_off_retains_completed_source_evidence(monkeypatch):
    monkeypatch.setenv("VERDIFY_COMPONENT_EXPERIMENT_ENABLED", "enabled")
    monkeypatch.setenv("VERDIFY_POLICY_VECTOR_MODE", "off")
    monkeypatch.setenv("VERDIFY_ACTIVE_EXPERIMENT_ID", EXPERIMENT_ID)
    store = FakeStore(None)
    await component_experiment_worker(object(), store=store, fence_provider=lambda: fence())
    for field in CANONICAL_FIELD_ORDER:
        record_component_cfg_readback(field, minimum_state()[field], observed_at=NOW)
    (raw,) = component_cfg_source_epochs()

    monkeypatch.setenv("VERDIFY_COMPONENT_EXPERIMENT_ENABLED", "off")
    monkeypatch.delenv("VERDIFY_ACTIVE_EXPERIMENT_ID", raising=False)
    result = await component_experiment_worker(object(), store=store, fence_provider=lambda: fence())

    assert result.reason == "component_capability_off"
    assert component_cfg_source_epochs() == (raw,)
    assert component_authority_hold() == (True, frozenset(CANONICAL_FIELD_ORDER))


@pytest.mark.asyncio
async def test_enabled_mode_with_unbound_store_retains_all_48_writer_hold(monkeypatch):
    monkeypatch.setenv("VERDIFY_COMPONENT_EXPERIMENT_ENABLED", "enabled")
    monkeypatch.setenv("VERDIFY_POLICY_VECTOR_MODE", "off")
    monkeypatch.setenv("VERDIFY_ACTIVE_EXPERIMENT_ID", EXPERIMENT_ID)
    monkeypatch.setattr(component_experiment, "_store_factory", None)

    result = await component_experiment_worker(object())

    assert result.reason == "l3_store_adapter_unbound"
    assert component_authority_hold() == (True, frozenset(CANONICAL_FIELD_ORDER))


@pytest.mark.asyncio
async def test_enabled_mode_rejects_unattested_shared_database_pool(monkeypatch):
    monkeypatch.setenv("VERDIFY_COMPONENT_EXPERIMENT_ENABLED", "enabled")
    monkeypatch.setenv("VERDIFY_POLICY_VECTOR_MODE", "off")
    monkeypatch.setenv("VERDIFY_ACTIVE_EXPERIMENT_ID", EXPERIMENT_ID)

    result = await component_experiment_worker(object())

    assert result.reason == "component_database_role_unattested"
    assert component_authority_hold() == (True, frozenset(CANONICAL_FIELD_ORDER))


@pytest.mark.asyncio
async def test_worker_persists_each_raw_epoch_then_monitors_before_claim(monkeypatch):
    monkeypatch.setenv("VERDIFY_COMPONENT_EXPERIMENT_ENABLED", "enabled")
    monkeypatch.setenv("VERDIFY_POLICY_VECTOR_MODE", "off")
    monkeypatch.setenv("VERDIFY_ACTIVE_EXPERIMENT_ID", EXPERIMENT_ID)
    store = FakeStore(None)

    first = await component_experiment_worker(object(), store=store, fence_provider=lambda: fence())
    assert first.reason == "no_current_typed_work"
    state = minimum_state()
    for field in CANONICAL_FIELD_ORDER:
        record_component_cfg_readback(field, state[field], observed_at=NOW)
    assert len(component_cfg_source_epochs()) == 1
    store.monitor_status = runtime_status()

    store.worker_order.clear()
    second = await component_experiment_worker(object(), store=store, fence_provider=lambda: fence())

    assert second.reason == "no_current_typed_work"
    assert store.worker_order == ["prepare", "monitor", "runtime_snapshot", "monitor", "claim"]
    assert len(store.runtime_snapshots) == 1
    raw, device_id = store.runtime_snapshots[0]
    assert raw.values == state
    assert raw.reset_detected is False
    assert device_id == "esp32-vallery"
    assert component_cfg_source_epochs() == ()


@pytest.mark.asyncio
async def test_pre_exposure_epochs_survive_monitor_tick_for_bundle_confirmation(monkeypatch):
    monkeypatch.setenv("VERDIFY_COMPONENT_EXPERIMENT_ENABLED", "enabled")
    monkeypatch.setenv("VERDIFY_POLICY_VECTOR_MODE", "off")
    monkeypatch.setenv("VERDIFY_ACTIVE_EXPERIMENT_ID", EXPERIMENT_ID)
    store = FakeStore(None)
    await component_experiment_worker(object(), store=store, fence_provider=lambda: fence())
    for field in CANONICAL_FIELD_ORDER:
        record_component_cfg_readback(field, minimum_state()[field], observed_at=NOW)
    (raw,) = component_cfg_source_epochs()
    store.worker_order.clear()

    result = await component_experiment_worker(object(), store=store, fence_provider=lambda: fence())

    assert result.reason == "no_current_typed_work"
    assert store.worker_order == ["prepare", "monitor", "claim"]
    assert store.runtime_snapshots == []
    assert component_cfg_source_epochs() == (raw,)


@pytest.mark.asyncio
async def test_pre_exposure_device_reset_stops_before_claim_after_durable_fault_call(monkeypatch):
    monkeypatch.setenv("VERDIFY_COMPONENT_EXPERIMENT_ENABLED", "enabled")
    monkeypatch.setenv("VERDIFY_POLICY_VECTOR_MODE", "off")
    monkeypatch.setenv("VERDIFY_ACTIVE_EXPERIMENT_ID", EXPERIMENT_ID)
    store = FakeStore(None)
    await component_experiment_worker(object(), store=store, fence_provider=lambda: fence())
    record_component_device_uptime(120)
    assert record_component_device_uptime(4) is True
    for field in CANONICAL_FIELD_ORDER:
        record_component_cfg_readback(field, minimum_state()[field], observed_at=NOW)
    assert component_cfg_source_epochs()[0].reset_detected is True

    result = await component_experiment_worker(object(), store=store, fence_provider=lambda: fence())

    assert result.reason == "device_reset_detected"
    assert store.claims == 1  # only the source-arming tick claimed
    assert store.runtime_snapshots[-1][0].reset_detected is True
    assert component_cfg_source_epochs() == ()
    assert component_authority_hold() == (True, frozenset(CANONICAL_FIELD_ORDER))


@pytest.mark.asyncio
async def test_prepare_outage_preserves_pinned_reset_evidence_for_recovery_tick(monkeypatch):
    monkeypatch.setenv("VERDIFY_COMPONENT_EXPERIMENT_ENABLED", "enabled")
    monkeypatch.setenv("VERDIFY_POLICY_VECTOR_MODE", "off")
    monkeypatch.setenv("VERDIFY_ACTIVE_EXPERIMENT_ID", EXPERIMENT_ID)
    store = FakeStore(None)
    await component_experiment_worker(object(), store=store, fence_provider=lambda: fence())
    record_component_device_uptime(120)
    record_component_device_uptime(4)
    for field in CANONICAL_FIELD_ORDER:
        record_component_cfg_readback(field, minimum_state()[field], observed_at=NOW)
    (reset_epoch,) = component_cfg_source_epochs()
    store.raise_prepare = True

    failed = await component_experiment_worker(object(), store=store, fence_provider=lambda: fence())

    assert failed.reason == "database_runtime_authority_uncertain"
    assert component_cfg_source_epochs() == (reset_epoch,)
    assert component_authority_hold() == (True, frozenset(CANONICAL_FIELD_ORDER))

    store.raise_prepare = False
    faulted = await component_experiment_worker(object(), store=store, fence_provider=lambda: fence())
    assert faulted.reason == "prepare_runtime_database_uncertain"
    assert store.fault_reports[-1][-2:] == ("db_outage", "prepare_runtime_database_uncertain")
    retried = await component_experiment_worker(object(), store=store, fence_provider=lambda: fence())
    assert retried.reason == "device_reset_detected"
    assert store.runtime_snapshots[-1][0].source_epoch_id == reset_epoch.source_epoch_id
    assert component_cfg_source_epochs() == ()


@pytest.mark.asyncio
async def test_runtime_snapshot_failure_holds_all_writers_and_retries_same_epoch(monkeypatch):
    monkeypatch.setenv("VERDIFY_COMPONENT_EXPERIMENT_ENABLED", "enabled")
    monkeypatch.setenv("VERDIFY_POLICY_VECTOR_MODE", "off")
    monkeypatch.setenv("VERDIFY_ACTIVE_EXPERIMENT_ID", EXPERIMENT_ID)
    store = FakeStore(None)
    await component_experiment_worker(object(), store=store, fence_provider=lambda: fence())
    for field in CANONICAL_FIELD_ORDER:
        record_component_cfg_readback(field, minimum_state()[field], observed_at=NOW)
    (raw,) = component_cfg_source_epochs()
    store.monitor_status = runtime_status()
    store.raise_runtime_snapshot = True

    failed = await component_experiment_worker(object(), store=store, fence_provider=lambda: fence())

    assert failed.reason == "open_exposure_monitor_uncertain"
    assert store.claims == 1
    assert component_authority_hold() == (True, frozenset(CANONICAL_FIELD_ORDER))
    assert component_cfg_source_epochs() == (raw,)

    store.raise_runtime_snapshot = False
    faulted = await component_experiment_worker(object(), store=store, fence_provider=lambda: fence())
    assert faulted.reason == "open_exposure_monitor_database_uncertain"
    assert store.fault_reports[-1][-2:] == ("db_outage", "open_exposure_monitor_database_uncertain")
    recovered = await component_experiment_worker(object(), store=store, fence_provider=lambda: fence())
    assert recovered.reason == "no_current_typed_work"
    assert store.runtime_snapshots[-1][0].source_epoch_id == raw.source_epoch_id
    assert component_cfg_source_epochs() == ()


@pytest.mark.asyncio
async def test_open_exposure_monitor_uncertainty_prevents_claim_and_device_access(monkeypatch):
    monkeypatch.setenv("VERDIFY_COMPONENT_EXPERIMENT_ENABLED", "enabled")
    monkeypatch.setenv("VERDIFY_POLICY_VECTOR_MODE", "off")
    monkeypatch.setenv("VERDIFY_ACTIVE_EXPERIMENT_ID", EXPERIMENT_ID)
    store = FakeStore(work(), observation(work(), minimum_state()))
    store.raise_monitor = True
    transport = FakeTransport()

    result = await component_experiment_worker(
        object(),
        store=store,
        transport=transport,
        fence_provider=lambda: fence(),
    )

    assert result.reason == "open_exposure_monitor_uncertain"
    assert store.claims == 0
    assert transport.calls == []
    assert component_authority_hold() == (True, frozenset(CANONICAL_FIELD_ORDER))


@pytest.mark.asyncio
async def test_sensor_gap_is_atomically_reported_before_claim(monkeypatch):
    monkeypatch.setenv("VERDIFY_COMPONENT_EXPERIMENT_ENABLED", "enabled")
    monkeypatch.setenv("VERDIFY_POLICY_VECTOR_MODE", "off")
    monkeypatch.setenv("VERDIFY_ACTIVE_EXPERIMENT_ID", EXPERIMENT_ID)
    store = FakeStore(work())
    store.monitor_status = runtime_status(
        exposure_started_at=NOW - timedelta(seconds=91),
        resolved_at=NOW,
    )

    result = await component_experiment_worker(object(), store=store, fence_provider=lambda: fence())

    assert result.reason == "open_exposure_first_snapshot_overdue"
    assert store.worker_order == ["prepare", "monitor", "runtime_fault"]
    assert store.fault_reports[-1][-2:] == ("sensor_gap", "open_exposure_first_snapshot_overdue")
    assert store.claims == 0


@pytest.mark.asyncio
async def test_stale_open_exposure_snapshot_is_reported_from_db_clock(monkeypatch):
    monkeypatch.setenv("VERDIFY_COMPONENT_EXPERIMENT_ENABLED", "enabled")
    monkeypatch.setenv("VERDIFY_POLICY_VECTOR_MODE", "off")
    monkeypatch.setenv("VERDIFY_ACTIVE_EXPERIMENT_ID", EXPERIMENT_ID)
    store = FakeStore(work())
    store.monitor_status = runtime_status(
        exposure_started_at=NOW - timedelta(minutes=5),
        source_epoch_id="77777777-7777-4777-8777-777777777777",
        source_runtime_instance_id=RUNTIME_INSTANCE_ID,
        source_writer_generation=5,
        source_connection_generation=7,
        last_observed_at=NOW - timedelta(seconds=91),
        resolved_at=NOW,
    )

    result = await component_experiment_worker(object(), store=store, fence_provider=lambda: fence())

    assert result.reason == "open_exposure_snapshot_stale"
    assert store.fault_reports[-1][-2:] == ("sensor_gap", "open_exposure_snapshot_stale")
    assert store.claims == 0


@pytest.mark.asyncio
async def test_lease_loss_after_monitor_reports_before_claim_and_can_yield(monkeypatch):
    monkeypatch.setenv("VERDIFY_COMPONENT_EXPERIMENT_ENABLED", "enabled")
    monkeypatch.setenv("VERDIFY_POLICY_VECTOR_MODE", "off")
    monkeypatch.setenv("VERDIFY_ACTIVE_EXPERIMENT_ID", EXPERIMENT_ID)
    store = FakeStore(work())
    store.monitor_status = runtime_status()
    store.runtime_fault_hold_required = False
    store.runtime_fault_facility_yielded = True
    snapshots = iter((fence(), fence(writer_lease_held=False)))

    result = await component_experiment_worker(object(), store=store, fence_provider=lambda: next(snapshots))

    assert result.disposition == "yielded"
    assert store.fault_reports[-1][-2:] == ("lease_loss", "writer_lease_lost_after_monitor")
    assert store.claims == 0
    assert component_authority_hold()[0] is False


@pytest.mark.asyncio
async def test_connection_change_after_monitor_uses_registered_old_generation(monkeypatch):
    monkeypatch.setenv("VERDIFY_COMPONENT_EXPERIMENT_ENABLED", "enabled")
    monkeypatch.setenv("VERDIFY_POLICY_VECTOR_MODE", "off")
    monkeypatch.setenv("VERDIFY_ACTIVE_EXPERIMENT_ID", EXPERIMENT_ID)
    store = FakeStore(work())
    store.monitor_status = runtime_status()
    snapshots = iter((fence(), fence(connection_generation=8)))

    result = await component_experiment_worker(object(), store=store, fence_provider=lambda: next(snapshots))

    assert result.reason == "connection_generation_changed_after_monitor"
    report = store.fault_reports[-1]
    assert report[6] == 7  # immutable registered reporter generation
    assert report[-2:] == ("connection_generation_changed", "connection_generation_changed_after_monitor")
    assert store.claims == 0


@pytest.mark.asyncio
async def test_runtime_fault_timeout_retries_same_uuid_and_immutable_arguments(monkeypatch):
    monkeypatch.setenv("VERDIFY_COMPONENT_EXPERIMENT_ENABLED", "enabled")
    monkeypatch.setenv("VERDIFY_POLICY_VECTOR_MODE", "off")
    monkeypatch.setenv("VERDIFY_ACTIVE_EXPERIMENT_ID", EXPERIMENT_ID)
    store = FakeStore(work())
    store.monitor_status = runtime_status(
        exposure_started_at=NOW - timedelta(seconds=91),
        resolved_at=NOW,
    )
    store.raise_runtime_fault = True

    failed = await component_experiment_worker(object(), store=store, fence_provider=lambda: fence())
    assert failed.reason == "runtime_fault_persistence_uncertain"
    first_report = store.fault_reports[-1]

    store.raise_runtime_fault = False
    retried = await component_experiment_worker(object(), store=store, fence_provider=lambda: fence())

    assert retried.reason == "open_exposure_first_snapshot_overdue"
    assert store.fault_reports[-1] == first_report
    assert component_experiment._pending_runtime_faults == {}


@pytest.mark.asyncio
async def test_state_replay_request_runs_after_monitor_and_claim(monkeypatch):
    monkeypatch.setenv("VERDIFY_COMPONENT_EXPERIMENT_ENABLED", "enabled")
    monkeypatch.setenv("VERDIFY_POLICY_VECTOR_MODE", "off")
    monkeypatch.setenv("VERDIFY_ACTIVE_EXPERIMENT_ID", EXPERIMENT_ID)
    store = FakeStore(None)
    replay_order = []
    monkeypatch.setattr(component_experiment, "request_component_state_replay", lambda: replay_order.append("replay"))

    result = await component_experiment_worker(object(), store=store, fence_provider=lambda: fence())

    assert result.reason == "no_current_typed_work"
    assert store.worker_order == ["prepare", "monitor", "claim"]
    assert replay_order == ["replay"]


@pytest.mark.asyncio
async def test_missing_or_shared_component_database_credential_never_builds_pool(monkeypatch):
    monkeypatch.delenv("VERDIFY_EXPERIMENT_COMPONENT_DB_USER", raising=False)
    monkeypatch.delenv("VERDIFY_EXPERIMENT_COMPONENT_DB_PASSWORD", raising=False)
    assert await create_component_experiment_pool() is None

    monkeypatch.delenv("DB_USER", raising=False)
    monkeypatch.setenv("VERDIFY_EXPERIMENT_COMPONENT_DB_USER", "verdify")
    monkeypatch.setenv("VERDIFY_EXPERIMENT_COMPONENT_DB_PASSWORD", "redacted-test-value")
    assert await create_component_experiment_pool() is None

    monkeypatch.setenv("DB_USER", "ordinary-owner")
    monkeypatch.setenv("VERDIFY_EXPERIMENT_COMPONENT_DB_USER", "ordinary-owner")
    monkeypatch.setenv("VERDIFY_EXPERIMENT_COMPONENT_DB_PASSWORD", "redacted-test-value")
    assert await create_component_experiment_pool() is None

    monkeypatch.setenv(
        "VERDIFY_EXPERIMENT_COMPONENT_DB_USER",
        "verdify_experiment_v2_randomizer_login",
    )
    assert await create_component_experiment_pool() is None


@pytest.mark.asyncio
async def test_component_database_login_requires_exact_function_only_duty(monkeypatch):
    monkeypatch.setenv("DB_USER", "ordinary-owner")
    monkeypatch.setenv(
        "VERDIFY_EXPERIMENT_COMPONENT_DB_USER",
        "verdify_experiment_v2_component_executor_login",
    )
    monkeypatch.setenv("VERDIFY_EXPERIMENT_COMPONENT_DB_PASSWORD", "redacted-test-value")
    attestation = {
        "session_user_matches": True,
        "duty_member": True,
        "duty_membership_non_admin": True,
        "current_user_name": "verdify_experiment_v2_component_executor_login",
        "session_user_name": "verdify_experiment_v2_component_executor_login",
        "login_role_safe": True,
        "is_superuser": False,
        "is_database_owner": False,
        "has_elevated_role_attributes": False,
        "duty_role_safe": True,
        "has_other_role_membership": False,
        "has_unexpected_duty_member": False,
        "has_managed_object_ownership": False,
        "schema_usage": True,
        "has_public_schema_create": False,
        "has_protected_relation_privilege": False,
        "has_protected_sequence_privilege": False,
        "has_unexpected_function_execute": False,
        "has_required_function_execute": True,
    }

    class ProbeConnection:
        async def fetchrow(self, query):
            assert "pg_database" in query
            assert "has_table_privilege" in query
            assert "has_any_column_privilege" in query
            assert "has_sequence_privilege" in query
            assert "has_function_privilege" in query
            assert "to_regprocedure" in query
            assert "has_schema_privilege" in query
            assert "duty.rolcanlogin" in query
            assert "duty.rolinherit" in query
            assert "membership.admin_option" in query
            assert "has_unexpected_duty_member" in query
            assert "pg_has_role(candidate.oid, duty.oid, 'member')" in query
            assert "current_user::text AS current_user_name" in query
            assert "owned.relowner" in query
            assert "candidate_function.prosecdef" in query
            assert (
                "fn_experiment_v2_report_runtime_fault(uuid,text,uuid,bigint,uuid,bigint,bigint,text,text,text)"
                in query
            )
            assert (
                "fn_experiment_v2_record_preexposure_mismatch(uuid,uuid,uuid,text,uuid,bytea,jsonb,"
                "text,text,text,text,uuid,bigint,bigint,bigint,text)" in query
            )
            assert "fn_experiment_v2_safe_startup_attestation(text,uuid)" in query
            assert "experiment_v2_%" in query
            assert "namespace.nspname = 'public'" in query
            return dict(attestation)

    class ProbePool:
        def __init__(self):
            self.closed = False

        def acquire(self):
            return AsyncAcquire(ProbeConnection())

        async def close(self):
            self.closed = True

    candidate = ProbePool()

    async def create_pool(**_kwargs):
        return candidate

    monkeypatch.setattr(component_experiment.asyncpg, "create_pool", create_pool)
    pool = await create_component_experiment_pool()
    assert isinstance(pool, component_experiment.AttestedComponentPool)
    await pool.close()
    assert candidate.closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "unsafe_value"),
    [
        ("current_user_name", "verdify_experiment_v2_randomizer_login"),
        ("session_user_name", "verdify_experiment_v2_randomizer_login"),
        ("session_user_matches", False),
        ("session_user_matches", 1),
        ("duty_member", False),
        ("duty_membership_non_admin", False),
        ("login_role_safe", False),
        ("duty_member", 1),
        ("is_superuser", True),
        ("is_superuser", 0),
        ("is_database_owner", True),
        ("has_elevated_role_attributes", True),
        ("duty_role_safe", False),
        ("has_other_role_membership", True),
        ("has_unexpected_duty_member", True),
        ("has_managed_object_ownership", True),
        ("schema_usage", False),
        ("has_public_schema_create", True),
        ("has_protected_relation_privilege", True),
        ("has_protected_sequence_privilege", True),
        ("has_unexpected_function_execute", True),
        ("has_required_function_execute", False),
    ],
)
async def test_component_database_login_rejects_every_privilege_escape(monkeypatch, field, unsafe_value):
    monkeypatch.setenv("DB_USER", "ordinary-owner")
    monkeypatch.setenv(
        "VERDIFY_EXPERIMENT_COMPONENT_DB_USER",
        "verdify_experiment_v2_component_executor_login",
    )
    monkeypatch.setenv("VERDIFY_EXPERIMENT_COMPONENT_DB_PASSWORD", "redacted-test-value")
    attestation = {
        "session_user_matches": True,
        "duty_member": True,
        "duty_membership_non_admin": True,
        "current_user_name": "verdify_experiment_v2_component_executor_login",
        "session_user_name": "verdify_experiment_v2_component_executor_login",
        "login_role_safe": True,
        "is_superuser": False,
        "is_database_owner": False,
        "has_elevated_role_attributes": False,
        "duty_role_safe": True,
        "has_other_role_membership": False,
        "has_unexpected_duty_member": False,
        "has_managed_object_ownership": False,
        "schema_usage": True,
        "has_public_schema_create": False,
        "has_protected_relation_privilege": False,
        "has_protected_sequence_privilege": False,
        "has_unexpected_function_execute": False,
        "has_required_function_execute": True,
    }
    attestation[field] = unsafe_value

    class ProbeConnection:
        async def fetchrow(self, _query):
            return dict(attestation)

    class ProbePool:
        def __init__(self):
            self.closed = False

        def acquire(self):
            return AsyncAcquire(ProbeConnection())

        async def close(self):
            self.closed = True

    candidate = ProbePool()

    async def create_pool(**_kwargs):
        return candidate

    monkeypatch.setattr(component_experiment.asyncpg, "create_pool", create_pool)
    assert await create_component_experiment_pool() is None
    assert candidate.closed is True


def test_startup_primes_all_48_writer_holds_before_scheduler(monkeypatch):
    set_component_authority_hold(False)
    monkeypatch.setenv("VERDIFY_COMPONENT_EXPERIMENT_ENABLED", "enabled")
    monkeypatch.setenv("VERDIFY_ACTIVE_EXPERIMENT_ID", EXPERIMENT_ID)
    prime_component_startup_hold()
    active, fields = component_authority_hold()
    assert active is True and fields == frozenset(CANONICAL_FIELD_ORDER)

    source = (Path(__file__).resolve().parents[1] / "ingestor/ingestor.py").read_text()
    assert source.index("await attest_component_safe_startup(component_pool)") < source.index(
        "await reconcile_interrupted_device_writes(pool)"
    )
    assert source.index("prime_component_startup_hold()") < source.index("asyncio.gather(")
    shutdown = source[source.index("if _shutdown.is_set():") :]
    assert shutdown.index("main_tasks.cancel()") < shutdown.index("await _writer_lease.release()")


@pytest.mark.asyncio
async def test_safe_startup_attestation_holds_before_off_mode_writers(monkeypatch):
    monkeypatch.setenv("VERDIFY_COMPONENT_EXPERIMENT_ENABLED", "off")
    monkeypatch.delenv("VERDIFY_ACTIVE_EXPERIMENT_ID", raising=False)
    monkeypatch.delenv("GREENHOUSE_ID", raising=False)

    class ProbeConnection:
        async def fetchrow(self, query, *args):
            assert "fn_experiment_v2_safe_startup_attestation" in query
            assert args == ("esp32-vallery", None)
            return {
                "attested_at": NOW,
                "device_id": "esp32-vallery",
                "requested_experiment_id": None,
                "scoped_experiment_id": EXPERIMENT_ID,
                "scope_resolved": True,
                "current_lease_generation": 5,
                "active_experiment_count": 1,
                "open_exposure_count": 1,
                "recovery_pending_count": 0,
                "experiment_authority_active": True,
                "facility_authority_yielded": False,
                "hold_required": True,
                "attestation_reason": "open_exposure",
            }

    pool = component_experiment.AttestedComponentPool(
        type("Pool", (), {"acquire": lambda self: AsyncAcquire(ProbeConnection())})()
    )
    attestation = await attest_component_safe_startup(pool)
    assert attestation is not None and attestation.hold_required is True
    assert component_authority_hold() == (True, frozenset(CANONICAL_FIELD_ORDER))


@pytest.mark.asyncio
async def test_failed_safe_startup_attestation_retains_all_48_hold(monkeypatch):
    monkeypatch.setenv("VERDIFY_COMPONENT_EXPERIMENT_ENABLED", "off")
    monkeypatch.delenv("VERDIFY_ACTIVE_EXPERIMENT_ID", raising=False)
    monkeypatch.delenv("GREENHOUSE_ID", raising=False)

    class ProbeConnection:
        async def fetchrow(self, _query, *_args):
            raise RuntimeError("db unavailable")

    pool = component_experiment.AttestedComponentPool(
        type("Pool", (), {"acquire": lambda self: AsyncAcquire(ProbeConnection())})()
    )
    with pytest.raises(RuntimeError, match="db unavailable"):
        await attest_component_safe_startup(pool)
    assert component_authority_hold() == (True, frozenset(CANONICAL_FIELD_ORDER))


@pytest.mark.asyncio
async def test_shadow_preview_uses_marker_and_two_epochs_but_zero_physical_calls():
    item = preview_work()
    store = FakeStore(item)
    transport = FakeTransport()
    result = await executor(store, transport).run_once(EXPERIMENT_ID, fence())
    assert result.disposition == "previewed"
    assert store.previewed == 1
    assert transport.calls == []
    assert store.bundle is not None
    assert store.bundle.purpose == "preview"
    assert store.bundle.finished_at is not None
    assert store.opened == []
    assert store.outcomes == []
    assert component_authority_hold()[0] is False


@pytest.mark.asyncio
async def test_shadow_preview_cached_or_pending_epochs_never_complete():
    for mode, expected in (
        ("pending", "awaiting_two_observation_epochs"),
        ("cached", "observation_epoch_separation_too_short"),
    ):
        item = preview_work()
        store = FakeStore(item)
        store.epoch_mode = mode
        transport = FakeTransport()
        result = await executor(store, transport).run_once(EXPERIMENT_ID, fence())
        assert result.reason == expected
        assert store.previewed == 0
        assert store.opened == []
        assert transport.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ({"execution_phase": "commissioning"}, "work_phase_mismatch"),
        ({"admission_state": "closed"}, "physical_admission_not_open"),
        ({"expected_revision_bundle_sha256": "d" * 64}, "revision_bundle_mismatch"),
        ({"expires_at": NOW}, "work_expired_or_not_current"),
        ({"claim_expires_at": NOW}, "work_expired_or_not_current"),
        ({"lease_generation": 6}, "lease_generation_mismatch"),
        ({"writer_generation": 6}, "writer_generation_mismatch"),
        ({"runtime_instance_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"}, "runtime_instance_mismatch"),
        ({"connection_generation": 8}, "connection_generation_mismatch"),
        ({"assignment_id": None}, "randomized_assignment_lineage_mismatch"),
    ],
)
async def test_wrong_phase_admission_revision_expiry_lease_and_lineage_make_zero_calls(mutation, reason):
    item = work(**mutation)
    store = FakeStore(item, observation(item, item.baseline_state))
    transport = FakeTransport()
    result = await executor(store, transport).run_once(EXPERIMENT_ID, fence())
    assert result.reason == reason
    assert transport.calls == []
    assert store.opened == []


@pytest.mark.asyncio
async def test_unqualified_live_grid_or_prefix_replay_blocks_every_physical_call(monkeypatch):
    item = work()
    store = FakeStore(item, observation(item, item.baseline_state))
    transport = FakeTransport()
    monkeypatch.setattr(component_experiment, "physical_execution_qualified", lambda _grid: False)

    result = await executor(store, transport).run_once(EXPERIMENT_ID, fence())

    assert result.reason == "physical_route_grid_or_prefix_replay_unqualified"
    assert transport.calls == []
    assert store.opened == []


@pytest.mark.asyncio
async def test_nonbaseline_target_cannot_write_before_linked_baseline_confirmation():
    item = work(baseline_interposition_confirmed=False)
    store = FakeStore(item, observation(item, item.baseline_state))
    transport = FakeTransport()
    result = await executor(store, transport).run_once(EXPERIMENT_ID, fence())
    assert result.reason == "baseline_interposition_required"
    assert store.recoveries == ["baseline_interposition_required"]
    assert transport.calls == []


@pytest.mark.asyncio
async def test_exact_treatment_difference_confirms_then_opens_exposure():
    item = work()
    store = FakeStore(item, observation(item, item.baseline_state))
    transport = FakeTransport()
    result = await executor(store, transport).run_once(EXPERIMENT_ID, fence())
    assert result.disposition == "confirmed"
    assert [call.parameter for call in transport.calls] == ["mister_all_kpa"]
    assert [outcome.status for outcome in store.outcomes] == ["requested", "queued", "sent", "confirmed"]
    assert store.opened
    assert component_authority_hold() == (True, frozenset(CANONICAL_FIELD_ORDER))


@pytest.mark.asyncio
async def test_reset_during_confirmation_fails_work_and_never_opens_exposure():
    item = work()
    store = FakeStore(item, observation(item, item.baseline_state))
    store.epoch_mode = "reset_fault"
    transport = FakeTransport()

    result = await executor(store, transport).run_once(EXPERIMENT_ID, fence())

    assert result.reason == "device_reset_detected"
    assert store.opened == []
    assert store.events[-1] == ("failed", {"reason": "device_reset_detected"})


@pytest.mark.asyncio
async def test_atomic_recovery_mismatch_yields_without_repeating_terminal_mutations():
    item = recovery_work()
    store = FakeStore(item, observation(item, maximum_state()))
    store.epoch_mode = "atomic_mismatch_yield"

    result = await executor(store).run_once(EXPERIMENT_ID, fence())

    assert result.disposition == "yielded"
    assert result.reason == "facility_rescue_yield"
    assert store.opened == []
    assert store.events == []  # the L3 mismatch function already committed it
    assert component_authority_hold()[0] is False


@pytest.mark.asyncio
async def test_database_lease_and_process_writer_generations_are_independent():
    item = work(writer_generation=9)
    store = FakeStore(item, observation(item, item.baseline_state))
    transport = FakeTransport()
    result = await executor(store, transport).run_once(EXPERIMENT_ID, fence(writer_generation=9))
    assert result.disposition == "confirmed"
    assert {outcome.writer_generation for outcome in store.outcomes} == {9}


@pytest.mark.asyncio
async def test_partial_and_unknown_delivery_never_open_exposure_and_request_recovery():
    item = work()
    target = dict(item.target_state)
    target["mister_pulse_on_s"] = 35.0
    item = replace(item, target_state=target)
    for reason in ("command_error:RuntimeError", "command_timeout_outcome_unknown"):
        store = FakeStore(item, observation(item, item.baseline_state))
        transport = FakeTransport(failure_index=0, failure_reason=reason)
        result = await executor(store, transport).run_once(EXPERIMENT_ID, fence())
        assert result.reason == reason
        assert store.opened == []
        assert store.closed[-1] == reason
        assert store.recoveries == [reason]


@pytest.mark.asyncio
async def test_off_grid_and_common_field_targets_fail_before_bundle_reservation():
    item = work()
    off_grid = dict(item.target_state)
    off_grid["mister_pulse_gap_s"] = 38.0
    common = dict(item.target_state)
    common["cold_vent_guard_delta_f"] = 0.5
    for target, reason in (
        (off_grid, "value_off_entity_grid"),
        (common, "routine_target_changes_common_field"),
    ):
        changed = replace(item, target_state=target)
        store = FakeStore(changed, observation(changed, changed.baseline_state))
        transport = FakeTransport()
        result = await executor(store, transport).run_once(EXPERIMENT_ID, fence())
        assert result.reason == reason
        assert store.bundle is None
        assert transport.calls == []


@pytest.mark.asyncio
async def test_common_field_drift_invokes_separate_recovery_without_routine_setter():
    item = work()
    current = dict(item.baseline_state)
    current["cold_vent_guard_delta_f"] = 0.5
    store = FakeStore(item, observation(item, current))
    transport = FakeTransport()
    result = await executor(store, transport).run_once(EXPERIMENT_ID, fence())
    assert result.reason == "common_field_drift"
    assert store.recoveries == ["common_field_drift"]
    assert transport.calls == []


@pytest.mark.asyncio
async def test_full48_paused_recovery_is_fixed_order_and_retains_db_authority():
    item = recovery_work()
    store = FakeStore(item, observation(item, maximum_state()))
    transport = FakeTransport()
    result = await executor(store, transport).run_once(EXPERIMENT_ID, fence())
    assert result.disposition == "recovered"
    assert [call.parameter for call in transport.calls] == list(CANONICAL_FIELD_ORDER)
    assert len(transport.calls) == 48
    assert store.opened == []
    assert component_authority_hold() == (True, frozenset(CANONICAL_FIELD_ORDER))


@pytest.mark.asyncio
async def test_recovery_releases_hold_only_after_runtime_authority_explicitly_clears():
    item = recovery_work()
    store = FakeStore(item, observation(item, maximum_state()))
    store.runtime_authority_required = False

    result = await executor(store).run_once(EXPERIMENT_ID, fence())

    assert result.disposition == "recovered"
    assert store.prepares == 1
    assert component_authority_hold()[0] is False


@pytest.mark.asyncio
async def test_new_runtime_restart_signal_allows_only_linked_full48_recovery():
    item = recovery_work(writer_generation=9, signals=WorkSignals(rebooted=True))
    store = FakeStore(item)
    transport = FakeTransport()
    result = await executor(store, transport).run_once(EXPERIMENT_ID, fence(writer_generation=9))
    assert result.disposition == "recovered"
    assert [call.parameter for call in transport.calls] == list(CANONICAL_FIELD_ORDER)
    assert store.opened == []


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["rebooted", "reset_detected", "reconnected", "foreign_writer"])
async def test_reboot_reconnect_reset_and_foreign_writer_close_and_forbid_reentry(fault):
    item = work(signals=WorkSignals(**{fault: True}))
    store = FakeStore(item, observation(item, item.baseline_state))
    transport = FakeTransport()
    result = await executor(store, transport).run_once(EXPERIMENT_ID, fence())
    assert result.disposition == "failed"
    assert transport.calls == []
    assert store.closed
    assert store.recoveries
    assert store.events[-1][1]["nonbaseline_reentry_forbidden"] is True


@pytest.mark.asyncio
async def test_nonbaseline_never_reenters_after_reboot_day_marker():
    item = work(
        signals=WorkSignals(
            nonbaseline_reentry_forbidden=True,
            generation_recovery_cleared=True,
            same_generation_nonbaseline_reentry_forbidden=True,
        )
    )
    store = FakeStore(item, observation(item, item.baseline_state))
    transport = FakeTransport()
    result = await executor(store, transport).run_once(EXPERIMENT_ID, fence())
    assert result.reason == "nonbaseline_reentry_forbidden"
    assert transport.calls == []
    assert store.recoveries == []
    assert store.events[-1] == (
        "failed",
        {
            "reason": "nonbaseline_reentry_forbidden",
            "generation_recovery_cleared": True,
            "same_generation_window": True,
        },
    )


@pytest.mark.asyncio
async def test_generation_fault_stays_effective_before_confirmed_recovery_clearance():
    item = work(signals=WorkSignals(rebooted=True, generation_recovery_cleared=False))
    store = FakeStore(item, observation(item, item.baseline_state))
    transport = FakeTransport()

    result = await executor(store, transport).run_once(EXPERIMENT_ID, fence())

    assert result.reason == "device_reboot"
    assert transport.calls == []
    assert store.recoveries


@pytest.mark.asyncio
@pytest.mark.parametrize("subsequent_kind", ["baseline", "shadow", "next_day_nonbaseline"])
async def test_confirmed_generation_clearance_allows_safe_subsequent_work(subsequent_kind):
    cleared = WorkSignals(generation_recovery_cleared=True)
    if subsequent_kind == "shadow":
        item = preview_work(signals=cleared)
    elif subsequent_kind == "baseline":
        item = work(
            target_profile="baseline",
            target_state=minimum_state(),
            target_state_content_sha256="b" * 64,
            signals=cleared,
        )
    else:
        item = work(
            valid_from=NOW + timedelta(days=1),
            valid_until=NOW + timedelta(days=2),
            expires_at=NOW + timedelta(days=2),
            claim_expires_at=NOW + timedelta(days=1, minutes=2),
            resolved_at=NOW + timedelta(days=1),
            signals=cleared,
        )
    store = FakeStore(item, observation(item, item.baseline_state))
    transport = FakeTransport()

    result = await executor(store, transport).run_once(EXPERIMENT_ID, fence())

    assert result.disposition in {"confirmed", "previewed"}
    assert store.closed == []
    if subsequent_kind == "next_day_nonbaseline":
        assert [call.parameter for call in transport.calls] == ["mister_all_kpa"]


@pytest.mark.asyncio
async def test_facility_rescue_yields_releases_writer_and_never_auto_recovers():
    item = work(signals=WorkSignals(facility_rescue_active=True))
    store = FakeStore(item, observation(item, item.baseline_state))
    transport = FakeTransport()
    set_component_authority_hold(True, CANONICAL_FIELD_ORDER)
    result = await executor(store, transport).run_once(EXPERIMENT_ID, fence())
    assert result.disposition == "yielded"
    assert transport.calls == []
    assert store.recoveries == []
    assert component_authority_hold()[0] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "reason"),
    [
        ("cached", "observation_epoch_separation_too_short"),
        ("mismatch", "observed_state_value_mismatch"),
        ("wrong_kind", "observation_lineage_mismatch"),
        ("wrong_revision", "observation_revision_mismatch"),
    ],
)
async def test_cached_mismatched_kind_and_revision_receipts_never_expose(mode, reason):
    item = work()
    store = FakeStore(item, observation(item, item.baseline_state))
    store.epoch_mode = mode
    result = await executor(store).run_once(EXPERIMENT_ID, fence())
    assert result.reason == reason
    assert store.opened == []
    assert store.recoveries == [reason]


@pytest.mark.asyncio
async def test_completed_target_bundle_is_restart_idempotent_and_only_rechecks_epochs():
    item = work(target_profile="baseline", target_state=minimum_state(), target_state_content_sha256="b" * 64)
    current = dict(item.baseline_state)
    current["mister_all_kpa"] = 1.05
    store = FakeStore(item, observation(item, current))
    store.epoch_mode = "pending"
    transport = FakeTransport()
    first = await executor(store, transport).run_once(EXPERIMENT_ID, fence())
    assert first.disposition == "delivered"
    assert len(transport.calls) == 1
    store.current = observation(item, item.baseline_state, observed_at=NOW)
    store.epoch_mode = "valid"
    second_executor = ConfirmedComponentExecutor(
        store,
        transport=transport,
        clock=SequenceClock(NOW + timedelta(seconds=90), NOW + timedelta(seconds=90)),
    )
    second = await second_executor.run_once(EXPERIMENT_ID, fence())
    assert second.disposition == "confirmed"
    assert len(transport.calls) == 1
    assert [outcome.status for outcome in store.outcomes] == ["requested", "queued", "sent", "confirmed"]
    assert store.outcomes[-1].parameter == "mister_all_kpa"


@pytest.mark.asyncio
async def test_interrupted_reserved_target_and_recovery_are_never_replayed():
    for item in (work(), recovery_work()):
        store = FakeStore(item, observation(item, item.baseline_state))
        store.force_existing_bundle = DeliveryBundle(
            bundle_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            work_id=item.work_id,
            purpose="recovery" if item.operation_kind == "baseline_recovery" else "target",
            expected_state_content_sha256=(
                item.baseline_state_content_sha256
                if item.operation_kind == "baseline_recovery"
                else item.target_state_content_sha256
            ),
            writer_generation=item.writer_generation,
            connection_generation=item.connection_generation,
            revision_bundle_sha256=item.revisions.bundle_sha256,
            reserved_at=NOW,
        )
        transport = FakeTransport()
        result = await executor(store, transport).run_once(EXPERIMENT_ID, fence())
        assert result.reason == "interrupted_bundle_outcome_unknown"
        assert transport.calls == []


@pytest.mark.asyncio
async def test_claim_and_lifecycle_db_outages_never_become_device_success():
    item = work()
    store = FakeStore(item, observation(item, item.baseline_state))
    store.raise_claim = True
    transport = FakeTransport()
    result = await executor(store, transport).run_once(EXPERIMENT_ID, fence())
    assert result.reason == "database_claim_uncertain"
    assert transport.calls == []

    store.raise_claim = False
    store.raise_outcomes = True
    result = await executor(store, transport).run_once(EXPERIMENT_ID, fence())
    assert result.reason == "delivery_transport_uncertain"
    assert store.opened == []


def test_scheduler_registration_is_bounded_and_non_daily() -> None:
    source = (Path(__file__).parents[1] / "ingestor" / "ingestor.py").read_text()
    assert '("component_experiment", 15, restricted_component_experiment_worker)' in source
    assert "await component_experiment_worker(component_pool)" in source
    assert '"component_experiment": 150' in source
    assert "asyncio.create_task" in source  # periodic tasks are concurrent; no scheduler starvation


def test_all_fixture_ids_are_canonical_uuid_strings() -> None:
    for value in (EXPERIMENT_ID, WORK_ID):
        assert str(UUID(value)) == value
