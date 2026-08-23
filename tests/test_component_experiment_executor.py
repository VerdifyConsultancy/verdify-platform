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
from esp32_push import (  # noqa: E402
    ComponentBundleResult,
    ComponentCommandOutcome,
    component_authority_hold,
    set_component_authority_hold,
)
from tasks.component_experiment import (  # noqa: E402
    RUNTIME_INSTANCE_ID,
    BundleReservation,
    ConfirmedComponentExecutor,
    DeliveryBundle,
    ObservationEpoch,
    ObservedComponent,
    ResolvedWork,
    RevisionSet,
    RuntimeFence,
    WorkSignals,
    component_experiment_worker,
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
        self.raise_outcomes = False

    async def claim_next(
        self,
        experiment_id,
        *,
        lease_generation,
        writer_generation,
        connection_generation,
    ):
        self.claims += 1
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

    async def observation_epochs(self, item, bundle):
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
    set_component_authority_hold(False)
    for key in (
        "VERDIFY_COMPONENT_EXPERIMENT_ENABLED",
        "VERDIFY_POLICY_VECTOR_MODE",
        "VERDIFY_ACTIVE_EXPERIMENT_ID",
    ):
        monkeypatch.delenv(key, raising=False)
    yield
    set_component_authority_hold(False)


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
    assert component_authority_hold()[0] is False


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
async def test_full48_paused_recovery_is_fixed_order_and_releases_hold_after_proof():
    item = recovery_work()
    store = FakeStore(item, observation(item, maximum_state()))
    transport = FakeTransport()
    result = await executor(store, transport).run_once(EXPERIMENT_ID, fence())
    assert result.disposition == "recovered"
    assert [call.parameter for call in transport.calls] == list(CANONICAL_FIELD_ORDER)
    assert len(transport.calls) == 48
    assert store.opened == []
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
    item = work(signals=WorkSignals(nonbaseline_reentry_forbidden=True))
    store = FakeStore(item, observation(item, item.baseline_state))
    transport = FakeTransport()
    result = await executor(store, transport).run_once(EXPERIMENT_ID, fence())
    assert result.reason == "nonbaseline_reentry_forbidden"
    assert transport.calls == []


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
    assert '("component_experiment", 15, component_experiment_worker)' in source
    assert '"component_experiment": 150' in source
    assert "asyncio.create_task" in source  # periodic tasks are concurrent; no scheduler starvation


def test_all_fixture_ids_are_canonical_uuid_strings() -> None:
    for value in (EXPERIMENT_ID, WORK_ID):
        assert str(UUID(value)) == value
