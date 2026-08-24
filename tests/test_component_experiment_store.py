"""Concrete migration-214 adapter tests with a function-only asyncpg fake."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

_INGESTOR_PATH = str(Path(__file__).resolve().parents[1] / "ingestor")
if _INGESTOR_PATH not in sys.path:
    sys.path.insert(0, _INGESTOR_PATH)

import shared  # noqa: E402
from esp32_push import ComponentCommandOutcome  # noqa: E402
from tasks.component_experiment import (  # noqa: E402
    RUNTIME_INSTANCE_ID,
    AsyncpgComponentExperimentStore,
    ComponentRuntimeFault,
    RevisionSet,
    component_cfg_source_epochs,
    configure_component_cfg_source,
    record_component_cfg_readback,
    record_component_device_uptime,
)

from verdify_schemas.component_executor import CANONICAL_FIELD_ORDER, ENTITY_GRIDS
from verdify_schemas.policy_vector import encode_policy_vector
from verdify_schemas.tunable_registry import REGISTRY

NOW = datetime(2026, 8, 23, 23, 30, tzinfo=UTC)
EXPERIMENT_ID = "11111111-1111-4111-8111-111111111111"
WORK_ID = "22222222-2222-4222-8222-222222222222"
BUNDLE_ID = "33333333-3333-4333-8333-333333333333"
DEVICE_ID = "esp32-vallery"
REVISIONS = RevisionSet("c" * 64, "firmware", "config", "registry", "grid")


class FakeRange:
    def __init__(self, lower: datetime, upper: datetime) -> None:
        self.lower = lower
        self.upper = upper


def baseline_state() -> dict[str, bool | float]:
    return {
        field: False if grid.entity_type == "switch" else float(grid.minimum) for field, grid in ENTITY_GRIDS.items()
    }


class Acquire:
    def __init__(self, connection) -> None:
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *_args):
        return False


class FakePool:
    def __init__(self, connection) -> None:
        self.connection = connection

    def acquire(self):
        return Acquire(self.connection)


class FunctionOnlyConnection:
    def __init__(self) -> None:
        self.queries: list[str] = []
        self.bundle: dict | None = None
        self.window: list[dict] = []
        self.outcomes: list[tuple] = []
        self.events: list[tuple] = []
        self.recovery_requests: list[tuple] = []
        self.runtime_snapshots: list[dict] = []
        self.preexposure_mismatches: list[tuple] = []
        self.raise_preexposure_mismatch = False
        self.monitor_row: dict | None = None
        self.runtime_faults: list[tuple] = []
        self.registration = {
            "generation_event_id": 1,
            "runtime_instance_id": RUNTIME_INSTANCE_ID,
            "writer_generation": 9,
            "connection_generation": 7,
            "restart_detected": False,
            "reconnect_detected": False,
            "recovery_work_id": None,
            "admission_state": "open",
        }
        self.runtime = {
            "experiment_id": EXPERIMENT_ID,
            "protocol_version": 2,
            "transport_kind": "legacy_components_v1",
            "lifecycle_status": "running",
            "execution_phase": "randomized",
            "admission_state": "open",
            "component_enabled": True,
            "lease_generation": 5,
            "revision_bundle_sha256": REVISIONS.bundle_sha256,
            "firmware_revision": REVISIONS.firmware_revision,
            "config_revision": REVISIONS.config_revision,
            "registry_revision": REVISIONS.registry_revision,
            "grid_revision": REVISIONS.grid_revision,
            "device_id": DEVICE_ID,
            **{key: value for key, value in self.registration.items() if key != "generation_event_id"},
            "open_exposure_id": None,
            "authority_hold_required": True,
            "observation_source_required": False,
            "rescue_authorized": False,
        }
        vector = encode_policy_vector(baseline_state())
        self.candidate = {
            "claimed_event_id": 10,
            "claim_expires_at": NOW + timedelta(seconds=60),
            "resolved_at": NOW,
            "work_expires_at": NOW + timedelta(hours=1),
            "work_id": WORK_ID,
            "assignment_id": WORK_ID,
            "operation_kind": "randomized_assignment",
            "execution_phase": "randomized",
            "lifecycle_status": "running",
            "admission_state": "open",
            "revision_bundle_sha256": REVISIONS.bundle_sha256,
            "firmware_revision": REVISIONS.firmware_revision,
            "config_revision": REVISIONS.config_revision,
            "registry_revision": REVISIONS.registry_revision,
            "grid_revision": REVISIONS.grid_revision,
            "device_id": DEVICE_ID,
            "runtime_instance_id": RUNTIME_INSTANCE_ID,
            "writer_generation": 9,
            "connection_generation": 7,
            "restart_detected": False,
            "reconnect_detected": False,
            "recovery_work_id": None,
            "open_exposure_id": None,
            "baseline_state_content_sha256": "b" * 64,
            "baseline_wire_vector": vector,
            "target_profile": "baseline",
            "target_state_content_sha256": "b" * 64,
            "target_wire_vector": vector,
            "valid_range": FakeRange(NOW - timedelta(minutes=1), NOW + timedelta(hours=1)),
            "lease_generation": 5,
            "recovery_required": False,
            "baseline_confirmed": True,
            "rescue_authorized": False,
            "no_reentry": False,
            "executor_signals": {},
        }

    async def fetchrow(self, query, *args):
        self.queries.append(query)
        if "fn_experiment_v2_register_runtime_instance" in query:
            assert args[2] == RUNTIME_INSTANCE_ID
            return self.registration
        if "fn_experiment_v2_executor_runtime" in query:
            return self.runtime
        if "fn_experiment_v2_claim_executor_candidate" in query:
            return self.candidate
        if "fn_experiment_v2_read_delivery_bundle" in query:
            return None if self.bundle is None else dict(self.bundle)
        if "fn_experiment_v2_begin_delivery_bundle" in query:
            if self.bundle is None:
                self.bundle = {
                    "bundle_id": args[2],
                    "experiment_id": args[0],
                    "work_id": args[1],
                    "device_id": args[3],
                    "purpose": args[4],
                    "started_at": NOW + timedelta(seconds=1),
                    "bundle_finished_at": None,
                    "completion_recorded_at": None,
                }
            return dict(self.bundle)
        if "fn_experiment_v2_record_delivery_bundle" in query:
            assert self.bundle is not None
            self.bundle["bundle_finished_at"] = args[3]
            self.bundle["completion_recorded_at"] = args[3]
            return {"bundle_id": args[2], "bundle_finished_at": args[3]}
        if "fn_experiment_v2_record_observation_epoch" in query:
            observations = json.loads(args[5])
            persisted_at = max(
                datetime.fromisoformat(item["observed_at"].replace("Z", "+00:00")) for item in observations
            ) + timedelta(seconds=1)
            self.window.append(
                {
                    "window_kind": "post_delivery",
                    "sequence_index": len(self.window) + 1,
                    "source_epoch_id": args[3],
                    "receipt_id": f"44444444-4444-4444-8444-44444444444{len(self.window)}",
                    "policy_state_content_sha256": "b" * 64,
                    "wire_vector": args[4],
                    "observations": observations,
                    "first_observed_at": persisted_at - timedelta(seconds=1),
                    "last_observed_at": persisted_at - timedelta(seconds=1),
                    "persisted_at": persisted_at,
                    "bundle_finished_at": self.bundle["bundle_finished_at"],
                    "firmware_revision": args[6],
                    "config_revision": args[7],
                    "registry_revision": args[8],
                    "grid_revision": args[9],
                    "runtime_instance_id": RUNTIME_INSTANCE_ID,
                    "writer_generation": args[10],
                    "connection_generation": args[11],
                    "is_current_generation": True,
                    "is_fresh": True,
                }
            )
            return {"receipt_id": self.window[-1]["receipt_id"], "persisted_at": persisted_at}
        if "fn_experiment_v2_record_runtime_snapshot" in query:
            observations = json.loads(args[4])
            assert [item["wire_id"] for item in observations] == [
                REGISTRY[field].wire_id for field in CANONICAL_FIELD_ORDER
            ]
            row = {
                "source_epoch_id": args[2],
                "experiment_id": args[0],
                "device_id": args[1],
                "observed_wire_vector": args[3],
                "observations": observations,
                "runtime_instance_id": args[9],
                "writer_generation": args[10],
                "connection_generation": args[11],
                "reset_detected": args[12],
            }
            self.runtime_snapshots.append(row)
            return row
        if "fn_experiment_v2_record_preexposure_mismatch" in query:
            observations = json.loads(args[6])
            assert [item["wire_id"] for item in observations] == [
                REGISTRY[field].wire_id for field in CANONICAL_FIELD_ORDER
            ]
            self.preexposure_mismatches.append(args)
            if self.raise_preexposure_mismatch:
                raise RuntimeError("database unavailable")
            return {
                "fault_report_id": args[4],
                "experiment_id": args[0],
                "device_id": args[3],
                "reported_lease_generation": args[12],
                "reporter_runtime_instance_id": args[11],
                "reporter_writer_generation": args[13],
                "reporter_connection_generation": args[14],
                "reported_fault_kind": "stale_or_mismatched_work",
                "reason": "post_delivery_observation_mismatch",
                "close_reason": "stale_or_mismatched_work",
                "recovery_work_id": "66666666-6666-4666-8666-666666666666",
                "admission_state_after": "baseline_recovery",
                "authority_hold_required": True,
                "facility_authority_yielded": False,
                "recorded_at": NOW,
            }
        if "fn_experiment_v2_monitor_open_exposure" in query:
            return self.monitor_row
        if "fn_experiment_v2_report_runtime_fault" in query:
            self.runtime_faults.append(args)
            return {
                "fault_report_id": args[2],
                "experiment_id": args[0],
                "device_id": args[1],
                "reported_lease_generation": args[3],
                "reporter_runtime_instance_id": args[4],
                "reporter_writer_generation": args[5],
                "reporter_connection_generation": args[6],
                "reported_fault_kind": args[7],
                "reason": args[8],
                "close_reason": "reconnect" if args[7] == "connection_generation_changed" else args[7],
                "recovery_work_id": "66666666-6666-4666-8666-666666666666",
                "admission_state_after": "baseline_recovery",
                "authority_hold_required": True,
                "facility_authority_yielded": False,
                "recorded_at": NOW,
            }
        if "fn_experiment_v2_safe_startup_attestation" in query:
            return {
                "attested_at": NOW,
                "device_id": args[0],
                "requested_experiment_id": args[1],
                "scoped_experiment_id": args[1],
                "scope_resolved": True,
                "current_lease_generation": 5,
                "active_experiment_count": 1,
                "open_exposure_count": 0,
                "recovery_pending_count": 0,
                "experiment_authority_active": False,
                "facility_authority_yielded": False,
                "hold_required": False,
                "attestation_reason": "no_experiment_authority",
            }
        if "fn_experiment_v2_close_exposure" in query:
            return {"exposure_id": args[0], "close_reason": args[1]}
        raise AssertionError(f"unexpected fetchrow query: {query}")

    async def fetch(self, query, *args):
        self.queries.append(query)
        assert "fn_experiment_v2_read_observation_window" in query
        rows = list(self.window)
        if rows:
            rows.append(
                {
                    **rows[-1],
                    "window_kind": "current",
                    "source_epoch_id": "88888888-8888-4888-8888-888888888888",
                }
            )
        return rows

    async def fetchval(self, query, *args):
        self.queries.append(query)
        if "fn_experiment_v2_record_component_outcome" in query:
            self.outcomes.append(args)
            return len(self.outcomes)
        if "fn_experiment_v2_record_work_event" in query:
            self.events.append(args)
            return len(self.events)
        if "fn_experiment_v2_open_exposure" in query:
            return "55555555-5555-4555-8555-555555555555"
        if "fn_experiment_v2_request_recovery" in query:
            self.recovery_requests.append(args)
            return "66666666-6666-4666-8666-666666666666"
        raise AssertionError(f"unexpected fetchval query: {query}")


@pytest.fixture(autouse=True)
def isolated_component_source_epochs():
    configure_component_cfg_source(
        experiment_id=None,
        lease_generation=None,
        writer_generation=None,
        connection_generation=None,
        revisions=None,
    )
    yield
    configure_component_cfg_source(
        experiment_id=None,
        lease_generation=None,
        writer_generation=None,
        connection_generation=None,
        revisions=None,
    )


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setattr(shared, "transport_generation", 7)
    connection = FunctionOnlyConnection()
    return AsyncpgComponentExperimentStore(FakePool(connection)), connection


@pytest.mark.asyncio
async def test_runtime_registration_and_claim_map_separate_lease_writer_and_connection(adapter):
    store, connection = adapter
    authority = await store.prepare_runtime(
        EXPERIMENT_ID,
        device_id=DEVICE_ID,
        connection_generation=7,
        writer_lease_held=True,
        device_write_enabled=False,
    )
    assert authority.lease_generation == 5
    assert authority.writer_generation == 9
    assert authority.component_authority_required is True
    work = await store.claim_next(
        EXPERIMENT_ID,
        lease_generation=5,
        writer_generation=9,
        connection_generation=7,
    )
    assert work is not None
    assert work.lease_generation == 5
    assert work.writer_generation == 9
    assert work.runtime_instance_id == RUNTIME_INSTANCE_ID
    assert work.transport_kind == "legacy_components_v1"
    assert work.target_state == baseline_state()
    assert all("experiment_v2_randomization" not in query for query in connection.queries)


@pytest.mark.asyncio
async def test_claim_uses_transient_generation_fault_but_retains_historical_reentry_fence(adapter):
    store, connection = adapter
    await store.prepare_runtime(
        EXPERIMENT_ID,
        device_id=DEVICE_ID,
        connection_generation=7,
        writer_lease_held=True,
        device_write_enabled=True,
    )
    connection.candidate["restart_detected"] = True
    connection.candidate["executor_signals"] = {
        "generation_recovery_cleared": False,
        "effective_restart_detected": True,
        "effective_reconnect_detected": False,
        "same_generation_nonbaseline_reentry_forbidden": False,
    }
    before = await store.claim_next(
        EXPERIMENT_ID,
        lease_generation=5,
        writer_generation=9,
        connection_generation=7,
    )
    assert before is not None
    assert before.signals.rebooted is True
    assert before.signals.generation_recovery_cleared is False

    connection.candidate["no_reentry"] = True
    connection.candidate["executor_signals"] = {
        "generation_recovery_cleared": True,
        "effective_restart_detected": False,
        "effective_reconnect_detected": False,
        "same_generation_nonbaseline_reentry_forbidden": True,
    }
    after = await store.claim_next(
        EXPERIMENT_ID,
        lease_generation=5,
        writer_generation=9,
        connection_generation=7,
    )
    assert after is not None
    assert after.signals.rebooted is False  # immutable row remains true; effective fault cleared
    assert after.signals.generation_recovery_cleared is True
    assert after.signals.nonbaseline_reentry_forbidden is True
    assert after.signals.same_generation_nonbaseline_reentry_forbidden is True


@pytest.mark.asyncio
async def test_bundle_begin_is_restart_idempotent_and_outcomes_remain_truthful(adapter):
    store, connection = adapter
    await store.prepare_runtime(
        EXPERIMENT_ID,
        device_id=DEVICE_ID,
        connection_generation=7,
        writer_lease_held=True,
        device_write_enabled=True,
    )
    work = await store.claim_next(
        EXPERIMENT_ID,
        lease_generation=5,
        writer_generation=9,
        connection_generation=7,
    )
    first = await store.reserve_bundle(
        work,
        bundle_id=BUNDLE_ID,
        purpose="target",
        expected_state_content_sha256="b" * 64,
    )
    assert first.owned is True
    retry = await store.reserve_bundle(
        work,
        bundle_id="77777777-7777-4777-8777-777777777777",
        purpose="target",
        expected_state_content_sha256="b" * 64,
    )
    assert retry.owned is False
    assert retry.bundle.bundle_id == BUNDLE_ID

    outcome = ComponentCommandOutcome(
        index=0,
        parameter="mister_all_kpa",
        object_id="mister_all_kpa",
        value=1.0,
        entity_type="number",
        status="requested",
        reason="resolved",
        writer_generation=9,
        connection_generation=7,
    )
    await store.record_component_outcomes(work, first.bundle, [outcome])
    await store.record_component_outcomes(work, first.bundle, [replace(outcome, status="queued")])
    await store.record_component_outcomes(work, first.bundle, [replace(outcome, status="sent")])
    assert connection.bundle is not None
    connection.bundle["component_wire_ids"] = [REGISTRY["mister_all_kpa"].wire_id]
    finished = await store.finish_bundle(work, first.bundle, NOW + timedelta(seconds=2))
    await store.record_component_outcomes(work, finished, [replace(outcome, status="confirmed")])
    assert [args[4] for args in connection.outcomes] == ["requested", "queued", "sent", "confirmed"]

    restored = await store.reserve_bundle(
        work,
        bundle_id="99999999-9999-4999-8999-999999999999",
        purpose="target",
        expected_state_content_sha256="b" * 64,
    )
    assert restored.owned is False
    assert restored.bundle.component_fields == ("mister_all_kpa",)

    await store.request_recovery(work, "initial_enrollment")
    assert connection.recovery_requests[-1][1] is None


@pytest.mark.asyncio
async def test_source_epochs_are_persisted_and_read_only_through_l3_functions(adapter):
    store, connection = adapter
    authority = await store.prepare_runtime(
        EXPERIMENT_ID,
        device_id=DEVICE_ID,
        connection_generation=7,
        writer_lease_held=True,
        device_write_enabled=True,
    )
    work = await store.claim_next(
        EXPERIMENT_ID,
        lease_generation=5,
        writer_generation=9,
        connection_generation=7,
    )
    reservation = await store.reserve_bundle(
        work,
        bundle_id=BUNDLE_ID,
        purpose="target",
        expected_state_content_sha256="b" * 64,
    )
    bundle = await store.finish_bundle(work, reservation.bundle, NOW + timedelta(seconds=2))
    configure_component_cfg_source(
        experiment_id=EXPERIMENT_ID,
        lease_generation=authority.lease_generation,
        writer_generation=authority.writer_generation,
        connection_generation=7,
        revisions=REVISIONS,
    )
    state = baseline_state()
    for at in (NOW + timedelta(seconds=7), NOW + timedelta(seconds=42)):
        for field in CANONICAL_FIELD_ORDER:
            record_component_cfg_readback(field, state[field], observed_at=at)
    assert len(component_cfg_source_epochs()) == 2

    epochs = await store.observation_epochs(work, bundle)
    assert len(epochs) == 2
    assert all(epoch.runtime_instance_id == RUNTIME_INSTANCE_ID for epoch in epochs)
    assert all(epoch.values == state for epoch in epochs)
    forbidden_relations = (
        "public.experiment_v2_work ",
        "public.experiment_v2_state_artifacts ",
        "public.experiment_v2_observation_epochs ",
        "public.experiment_v2_randomization ",
    )
    assert all(not any(relation in query for relation in forbidden_relations) for query in connection.queries)


@pytest.mark.asyncio
async def test_raw_runtime_snapshot_and_open_exposure_monitor_use_only_bounded_functions(adapter):
    store, connection = adapter
    authority = await store.prepare_runtime(
        EXPERIMENT_ID,
        device_id=DEVICE_ID,
        connection_generation=7,
        writer_lease_held=True,
        device_write_enabled=True,
    )
    configure_component_cfg_source(
        experiment_id=EXPERIMENT_ID,
        lease_generation=authority.lease_generation,
        writer_generation=authority.writer_generation,
        connection_generation=7,
        revisions=REVISIONS,
    )
    state = baseline_state()
    for field in CANONICAL_FIELD_ORDER:
        record_component_cfg_readback(field, state[field], observed_at=NOW)
    (raw,) = component_cfg_source_epochs()

    assert await store.record_runtime_snapshot(raw, device_id=DEVICE_ID) is None
    snapshot = connection.runtime_snapshots[-1]
    assert snapshot["source_epoch_id"] == raw.source_epoch_id
    assert snapshot["observed_wire_vector"] == encode_policy_vector(state)
    assert len(snapshot["observations"]) == 48
    assert snapshot["reset_detected"] is False

    connection.monitor_row = {
        "exposure_id": "55555555-5555-4555-8555-555555555555",
        "exposure_started_at": NOW - timedelta(seconds=30),
        "work_id": WORK_ID,
        "current_runtime_instance_id": RUNTIME_INSTANCE_ID,
        "current_writer_generation": 9,
        "current_connection_generation": 7,
        "source_epoch_id": raw.source_epoch_id,
        "source_runtime_instance_id": RUNTIME_INSTANCE_ID,
        "source_writer_generation": 9,
        "source_connection_generation": 7,
        "last_observed_at": NOW - timedelta(seconds=1),
        "common_field_drift": False,
        "cfg_drift": False,
        "lineage_drift": False,
        "reset_detected": False,
        "foreign_writer": False,
        "exposure_is_open": True,
        "close_reason": None,
        "recovery_work_id": None,
        "resolved_at": NOW,
    }
    status = await store.monitor_open_exposure(
        EXPERIMENT_ID,
        device_id=DEVICE_ID,
        lease_generation=authority.lease_generation,
    )
    assert status is not None
    assert status.exposure_is_open is True
    assert status.source_epoch_id == raw.source_epoch_id

    function_queries = [query for query in connection.queries if "runtime_snapshot" in query or "monitor_open" in query]
    assert len(function_queries) == 2
    assert all(query.lstrip().startswith("SELECT * FROM public.fn_experiment_v2_") for query in function_queries)
    assert all(" FROM public.experiment_v2_" not in query for query in connection.queries)


@pytest.mark.asyncio
async def test_runtime_fault_and_startup_attestation_use_exact_bounded_functions(adapter):
    store, connection = adapter
    authority = await store.prepare_runtime(
        EXPERIMENT_ID,
        device_id=DEVICE_ID,
        connection_generation=7,
        writer_lease_held=True,
        device_write_enabled=True,
    )
    fault_id = "77777777-7777-4777-8777-777777777777"
    receipt = await store.report_runtime_fault(
        EXPERIMENT_ID,
        device_id=DEVICE_ID,
        fault_report_id=fault_id,
        expected_lease_generation=authority.lease_generation,
        runtime_instance_id=RUNTIME_INSTANCE_ID,
        writer_generation=authority.writer_generation,
        connection_generation=7,
        fault_kind="connection_generation_changed",
        reason="connection_generation_changed_after_monitor",
    )
    assert receipt.fault_report_id == fault_id
    assert receipt.close_reason == "reconnect"
    assert receipt.authority_hold_required is True

    attestation = await store.safe_startup_attestation(
        device_id=DEVICE_ID,
        experiment_id=EXPERIMENT_ID,
    )
    assert attestation.scope_resolved is True
    assert attestation.hold_required is False
    assert attestation.requested_experiment_id == EXPERIMENT_ID

    fault_query = next(query for query in connection.queries if "report_runtime_fault" in query)
    startup_query = next(query for query in connection.queries if "safe_startup_attestation" in query)
    assert fault_query.startswith("SELECT (public.fn_experiment_v2_report_runtime_fault")
    assert startup_query.startswith("SELECT * FROM public.fn_experiment_v2_safe_startup_attestation")
    assert all(" FROM public.experiment_v2_" not in query for query in (fault_query, startup_query))


@pytest.mark.asyncio
async def test_reset_epoch_is_durably_reported_and_never_used_as_bundle_confirmation(adapter):
    store, connection = adapter
    authority = await store.prepare_runtime(
        EXPERIMENT_ID,
        device_id=DEVICE_ID,
        connection_generation=7,
        writer_lease_held=True,
        device_write_enabled=True,
    )
    work = await store.claim_next(
        EXPERIMENT_ID,
        lease_generation=5,
        writer_generation=9,
        connection_generation=7,
    )
    reservation = await store.reserve_bundle(
        work,
        bundle_id=BUNDLE_ID,
        purpose="target",
        expected_state_content_sha256="b" * 64,
    )
    bundle = await store.finish_bundle(work, reservation.bundle, NOW + timedelta(seconds=2))
    configure_component_cfg_source(
        experiment_id=EXPERIMENT_ID,
        lease_generation=authority.lease_generation,
        writer_generation=authority.writer_generation,
        connection_generation=7,
        revisions=REVISIONS,
    )
    record_component_device_uptime(120)
    assert record_component_device_uptime(4) is True
    for field in CANONICAL_FIELD_ORDER:
        record_component_cfg_readback(field, baseline_state()[field], observed_at=NOW + timedelta(seconds=7))

    with pytest.raises(ComponentRuntimeFault, match="device_reset_detected"):
        await store.observation_epochs(work, bundle)

    assert len(connection.runtime_snapshots) == 1
    assert connection.runtime_snapshots[0]["reset_detected"] is True
    assert connection.window == []
    assert component_cfg_source_epochs() == ()


@pytest.mark.asyncio
async def test_complete_post_delivery_mismatch_is_durably_faulted_not_filtered(adapter):
    store, connection = adapter
    authority = await store.prepare_runtime(
        EXPERIMENT_ID,
        device_id=DEVICE_ID,
        connection_generation=7,
        writer_lease_held=True,
        device_write_enabled=True,
    )
    work = await store.claim_next(
        EXPERIMENT_ID,
        lease_generation=5,
        writer_generation=9,
        connection_generation=7,
    )
    reservation = await store.reserve_bundle(
        work,
        bundle_id=BUNDLE_ID,
        purpose="target",
        expected_state_content_sha256="b" * 64,
    )
    bundle = await store.finish_bundle(work, reservation.bundle, NOW + timedelta(seconds=2))
    configure_component_cfg_source(
        experiment_id=EXPERIMENT_ID,
        lease_generation=authority.lease_generation,
        writer_generation=authority.writer_generation,
        connection_generation=7,
        revisions=REVISIONS,
    )
    mismatched = baseline_state()
    mismatched["mister_all_kpa"] = float(ENTITY_GRIDS["mister_all_kpa"].maximum)
    for field in CANONICAL_FIELD_ORDER:
        record_component_cfg_readback(field, mismatched[field], observed_at=NOW + timedelta(seconds=7))
    (raw,) = component_cfg_source_epochs()

    connection.raise_preexposure_mismatch = True
    with pytest.raises(RuntimeError, match="database unavailable"):
        await store.observation_epochs(work, bundle)
    assert component_cfg_source_epochs() == (raw,)

    connection.raise_preexposure_mismatch = False
    with pytest.raises(ComponentRuntimeFault, match="post_delivery_observation_mismatch"):
        await store.observation_epochs(work, bundle)

    assert len(connection.preexposure_mismatches) == 2
    call = connection.preexposure_mismatches[-1]
    assert call[0:5] == (EXPERIMENT_ID, WORK_ID, BUNDLE_ID, DEVICE_ID, raw.source_epoch_id)
    assert call[12:15] == (5, 9, 7)
    assert connection.window == []
    assert component_cfg_source_epochs() == ()
