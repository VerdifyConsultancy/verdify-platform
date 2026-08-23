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
    RevisionSet,
    component_cfg_source_epochs,
    configure_component_cfg_source,
    record_component_cfg_readback,
)

from verdify_schemas.component_executor import CANONICAL_FIELD_ORDER, ENTITY_GRIDS
from verdify_schemas.policy_vector import encode_policy_vector

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
    finished = await store.finish_bundle(work, first.bundle, NOW + timedelta(seconds=2))
    await store.record_component_outcomes(work, finished, [replace(outcome, status="confirmed")])
    assert [args[4] for args in connection.outcomes] == ["requested", "queued", "sent", "confirmed"]

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
