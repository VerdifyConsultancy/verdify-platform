"""Raw cfg-source epoch tests; no database, network, or device access."""

from __future__ import annotations

import struct
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

_INGESTOR_PATH = str(Path(__file__).resolve().parents[1] / "ingestor")
if _INGESTOR_PATH not in sys.path:
    sys.path.insert(0, _INGESTOR_PATH)

import shared  # noqa: E402
import tasks.component_experiment as component_experiment  # noqa: E402
from aioesphomeapi.api_pb2 import SubscribeStatesRequest  # noqa: E402
from tasks.component_experiment import (  # noqa: E402
    RevisionSet,
    clear_component_entity_inventory,
    component_cfg_source_epochs,
    component_entity_grid_attestation,
    configure_component_cfg_source,
    record_component_cfg_readback,
    record_component_device_uptime,
    record_component_entity_inventory,
    record_component_grid_firmware_revision,
    request_component_state_replay,
)

from verdify_schemas.component_executor import CANONICAL_FIELD_ORDER, ENTITY_GRIDS
from verdify_schemas.component_qualification import RuntimeEntityMetadata
from verdify_schemas.policy_vector import decode_policy_vector
from verdify_schemas.tunable_registry import REGISTRY

NOW = datetime(2026, 8, 23, 23, 0, tzinfo=UTC)
EXPERIMENT_ID = "11111111-1111-4111-8111-111111111111"
REVISIONS = RevisionSet("a" * 64, "firmware", "config", "registry", "grid")


def minimum_state() -> dict[str, bool | float]:
    return {
        field: False if grid.entity_type == "switch" else float(grid.minimum) for field, grid in ENTITY_GRIDS.items()
    }


@pytest.fixture(autouse=True)
def isolated_source(monkeypatch):
    monkeypatch.setattr(shared, "transport_generation", 7)
    monkeypatch.setattr(shared, "writer_lease_strictly_held", lambda minimum_remaining_s=0: True)
    shared.esp32["client"] = None
    shared.esp32["state_subscription_client"] = None
    shared.esp32["state_subscription_generation"] = None
    shared.cfg_readback.clear()
    clear_component_entity_inventory()
    configure_component_cfg_source(
        experiment_id=None,
        lease_generation=None,
        writer_generation=None,
        connection_generation=None,
        revisions=None,
    )
    yield
    shared.esp32["client"] = None
    shared.esp32["state_subscription_client"] = None
    shared.esp32["state_subscription_generation"] = None
    clear_component_entity_inventory()
    configure_component_cfg_source(
        experiment_id=None,
        lease_generation=None,
        writer_generation=None,
        connection_generation=None,
        revisions=None,
    )


def arm(connection_generation: int = 7) -> None:
    configure_component_cfg_source(
        experiment_id=EXPERIMENT_ID,
        lease_generation=3,
        writer_generation=5,
        connection_generation=connection_generation,
        revisions=REVISIONS,
    )


def emit_complete(state: dict[str, bool | float], at: datetime) -> None:
    for index, field in enumerate(CANONICAL_FIELD_ORDER):
        completed = record_component_cfg_readback(field, state[field], observed_at=at)
        assert completed is (index == len(CANONICAL_FIELD_ORDER) - 1)


class ReplayConnection:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.messages = []

    def send_message(self, message) -> None:
        if self.fail:
            raise RuntimeError("injected send failure")
        self.messages.append(message)


class ReplayClient:
    def __init__(self, connection: ReplayConnection) -> None:
        self.connection = connection
        self.subscribe_calls = 0
        self.command_calls = 0

    def _get_connection(self):
        return self.connection

    def subscribe_states(self, _callback) -> None:
        self.subscribe_calls += 1

    def number_command(self, *_args, **_kwargs) -> None:
        self.command_calls += 1

    def switch_command(self, *_args, **_kwargs) -> None:
        self.command_calls += 1


def install_replay_client(client: ReplayClient, generation: int = 7) -> None:
    shared.esp32["client"] = client
    shared.esp32["state_subscription_client"] = client
    shared.esp32["state_subscription_generation"] = generation


def exact_runtime_inventory() -> tuple[RuntimeEntityMetadata, ...]:
    entities: list[RuntimeEntityMetadata] = []
    key = 100
    for field_name in CANONICAL_FIELD_ORDER:
        definition = REGISTRY[field_name]
        grid = ENTITY_GRIDS[field_name]
        key += 1
        entities.append(
            RuntimeEntityMetadata(
                device_id=0,
                object_id=definition.esp_object_id,
                entity_type=grid.entity_type,
                key=key,
                minimum=grid.minimum,
                maximum=grid.maximum,
                step=grid.step,
                assumed_state=False if grid.entity_type == "switch" else None,
            )
        )
        key += 1
        entities.append(
            RuntimeEntityMetadata(
                device_id=0,
                object_id=definition.cfg_readback_object_id,
                entity_type="sensor",
                key=key,
            )
        )
    return tuple(entities)


def test_epoch_uuid_and_timestamps_are_owned_by_raw_callbacks() -> None:
    arm()
    state = minimum_state()
    for field in CANONICAL_FIELD_ORDER[:-1]:
        assert record_component_cfg_readback(field, state[field], observed_at=NOW) is False
    assert component_cfg_source_epochs() == ()
    assert record_component_cfg_readback(CANONICAL_FIELD_ORDER[-1], state[CANONICAL_FIELD_ORDER[-1]], observed_at=NOW)

    (epoch,) = component_cfg_source_epochs()
    assert UUID(epoch.source_epoch_id).version == 4
    assert epoch.lease_generation == 3
    assert epoch.values == state
    assert decode_policy_vector(epoch.wire_vector) == state
    assert set(epoch.observed_at) == set(CANONICAL_FIELD_ORDER)
    assert set(epoch.observed_at.values()) == {NOW}


def test_epoch_accepts_exact_binary32_readbacks_and_persists_canonical_grid_values() -> None:
    arm()
    state = minimum_state()
    state.update(
        {
            "temp_hysteresis": 1.6,
            "cool_exit_hysteresis_f": 1.6,
            "vent_exchange_fraction": 0.3,
            "direct_wet_stress_vpd_margin_kpa": 0.05,
        }
    )
    transported = {
        field: struct.unpack("!f", struct.pack("!f", value))[0] if type(value) is float else value
        for field, value in state.items()
    }
    emit_complete(transported, NOW)

    (epoch,) = component_cfg_source_epochs()
    assert epoch.values == state
    assert decode_policy_vector(epoch.wire_vector) == state


def test_epoch_rejects_an_adjacent_binary32_value_until_a_valid_callback_arrives() -> None:
    arm()
    state = minimum_state()
    state["direct_wet_stress_vpd_margin_kpa"] = 0.05
    field = "direct_wet_stress_vpd_margin_kpa"
    bits = int.from_bytes(struct.pack("!f", state[field]), "big")
    adjacent = struct.unpack("!f", (bits + 1).to_bytes(4, "big"))[0]

    invalid = dict(state)
    invalid[field] = adjacent
    for name in CANONICAL_FIELD_ORDER:
        assert record_component_cfg_readback(name, invalid[name], observed_at=NOW) is False
    assert component_cfg_source_epochs() == ()

    transported = struct.unpack("!f", struct.pack("!f", state[field]))[0]
    assert record_component_cfg_readback(field, transported, observed_at=NOW)
    (epoch,) = component_cfg_source_epochs()
    assert epoch.values[field] == 0.05


def test_cached_snapshot_flush_cannot_complete_a_second_epoch() -> None:
    arm()
    state = minimum_state()
    emit_complete(state, NOW)

    # This mirrors the periodic setpoint_snapshot input: values may be copied
    # again, but no raw ESPHome callback reached the source hook.
    shared.cfg_readback.update({field: float(value) for field, value in state.items()})
    assert len(component_cfg_source_epochs()) == 1
    for field in CANONICAL_FIELD_ORDER[:-1]:
        assert record_component_cfg_readback(field, state[field], observed_at=NOW + timedelta(seconds=31)) is False
    assert len(component_cfg_source_epochs()) == 1
    assert record_component_cfg_readback(
        CANONICAL_FIELD_ORDER[-1],
        state[CANONICAL_FIELD_ORDER[-1]],
        observed_at=NOW + timedelta(seconds=31),
    )
    first, second = component_cfg_source_epochs()
    assert all(second.observed_at[field] > first.observed_at[field] for field in CANONICAL_FIELD_ORDER)


def test_state_replay_is_read_only_single_subscription_request_and_throttled() -> None:
    arm()
    connection = ReplayConnection()
    client = ReplayClient(connection)
    install_replay_client(client)

    assert request_component_state_replay(monotonic_clock=lambda: 0.0) is True
    assert request_component_state_replay(monotonic_clock=lambda: 30.999) is False
    assert request_component_state_replay(monotonic_clock=lambda: 31.0) is True

    assert len(connection.messages) == 2
    assert all(isinstance(message, SubscribeStatesRequest) for message in connection.messages)
    assert client.subscribe_calls == 0
    assert client.command_calls == 0


def test_state_replay_requires_armed_current_subscription_and_strict_lease(monkeypatch) -> None:
    connection = ReplayConnection()
    client = ReplayClient(connection)
    install_replay_client(client)
    assert request_component_state_replay(monotonic_clock=lambda: 0.0) is False

    arm()
    shared.esp32["state_subscription_generation"] = 8
    with pytest.raises(component_experiment.ComponentStoreError, match="authenticated subscription"):
        request_component_state_replay(monotonic_clock=lambda: 0.0)
    shared.esp32["state_subscription_generation"] = 7
    monkeypatch.setattr(shared, "writer_lease_strictly_held", lambda minimum_remaining_s=0: False)
    with pytest.raises(component_experiment.ComponentStoreError, match="strictly held"):
        request_component_state_replay(monotonic_clock=lambda: 0.0)
    assert connection.messages == []


def test_state_replay_send_failure_does_not_advance_throttle_or_add_callback() -> None:
    arm()
    connection = ReplayConnection(fail=True)
    client = ReplayClient(connection)
    install_replay_client(client)
    with pytest.raises(component_experiment.ComponentStoreError, match="request failed"):
        request_component_state_replay(monotonic_clock=lambda: 10.0)
    connection.fail = False
    assert request_component_state_replay(monotonic_clock=lambda: 10.0) is True
    assert len(connection.messages) == 1
    assert client.subscribe_calls == 0


def test_state_replay_identity_change_resets_throttle_and_old_client_is_rejected() -> None:
    arm()
    first_connection = ReplayConnection()
    first_client = ReplayClient(first_connection)
    install_replay_client(first_client)
    assert request_component_state_replay(monotonic_clock=lambda: 100.0) is True

    configure_component_cfg_source(
        experiment_id=EXPERIMENT_ID,
        lease_generation=4,
        writer_generation=6,
        connection_generation=8,
        revisions=REVISIONS,
    )
    shared.transport_generation = 8
    second_connection = ReplayConnection()
    second_client = ReplayClient(second_connection)
    install_replay_client(second_client, generation=8)
    assert request_component_state_replay(monotonic_clock=lambda: 100.0) is True
    shared.esp32["client"] = first_client
    with pytest.raises(component_experiment.ComponentStoreError, match="authenticated subscription"):
        request_component_state_replay(monotonic_clock=lambda: 131.0)


def test_grid_attestation_reuses_current_subscription_metadata_and_is_generation_fenced(monkeypatch) -> None:
    monkeypatch.setenv("VERDIFY_GIT_SHA", "a" * 40)
    monkeypatch.setenv("GREENHOUSE_ID", "vallery")
    connection = ReplayConnection()
    client = ReplayClient(connection)
    install_replay_client(client)
    record_component_entity_inventory(exact_runtime_inventory(), connection_generation=7, observed_at=NOW)

    assert record_component_grid_firmware_revision("2026.7.10.1500.09ee886", observed_at=NOW) is True
    evidence = component_entity_grid_attestation()
    assert evidence is not None
    assert evidence.field_count == 48
    assert evidence.connection_generation == 7
    assert client.subscribe_calls == 0
    assert client.command_calls == 0
    assert connection.messages == []

    # Repeated diagnostic callbacks do not forge a second receipt.
    assert record_component_grid_firmware_revision("2026.7.10.1500.09ee886", observed_at=NOW) is False
    shared.transport_generation = 8
    assert component_entity_grid_attestation() is None


def test_deployable_ingestor_pins_the_audited_state_replay_client_version() -> None:
    root = Path(__file__).resolve().parents[1]
    assert "aioesphomeapi==44.24.2" in (root / "ingestor" / "requirements-image.txt").read_text()
    assert "aioesphomeapi==44.24.2" in (root / "ingestor" / "requirements.txt").read_text()
    assert '"aioesphomeapi==44.24.2"' in (root / "pyproject.toml").read_text()


def test_invalid_later_callback_removes_an_older_pending_value_for_that_wire() -> None:
    arm()
    state = minimum_state()
    field = "band_track_fraction"
    assert record_component_cfg_readback(field, state[field], observed_at=NOW) is False
    assert record_component_cfg_readback(field, 0.123456789, observed_at=NOW + timedelta(seconds=1)) is False

    for other in CANONICAL_FIELD_ORDER:
        if other != field:
            assert record_component_cfg_readback(other, state[other], observed_at=NOW + timedelta(seconds=1)) is False
    assert component_cfg_source_epochs() == ()
    assert record_component_cfg_readback(field, state[field], observed_at=NOW + timedelta(seconds=2)) is True


def test_reconnect_discards_partial_epoch_instead_of_mixing_generations(monkeypatch) -> None:
    arm()
    state = minimum_state()
    for field in CANONICAL_FIELD_ORDER[:24]:
        record_component_cfg_readback(field, state[field], observed_at=NOW)
    monkeypatch.setattr(shared, "transport_generation", 8)
    assert (
        record_component_cfg_readback(CANONICAL_FIELD_ORDER[24], state[CANONICAL_FIELD_ORDER[24]], observed_at=NOW)
        is False
    )
    assert component_cfg_source_epochs() == ()

    arm(connection_generation=8)
    emit_complete(state, NOW + timedelta(seconds=1))
    (epoch,) = component_cfg_source_epochs()
    assert epoch.connection_generation == 8


def test_lease_change_discards_partial_epoch_instead_of_relabelling_it() -> None:
    arm()
    state = minimum_state()
    for field in CANONICAL_FIELD_ORDER[:24]:
        record_component_cfg_readback(field, state[field], observed_at=NOW)
    configure_component_cfg_source(
        experiment_id=EXPERIMENT_ID,
        lease_generation=4,
        writer_generation=5,
        connection_generation=7,
        revisions=REVISIONS,
    )
    for field in CANONICAL_FIELD_ORDER[24:]:
        record_component_cfg_readback(field, state[field], observed_at=NOW + timedelta(seconds=1))
    assert component_cfg_source_epochs() == ()
    for index, field in enumerate(CANONICAL_FIELD_ORDER[:24]):
        completed = record_component_cfg_readback(field, state[field], observed_at=NOW + timedelta(seconds=2))
        assert completed is (index == 23)
    (epoch,) = component_cfg_source_epochs()
    assert epoch.lease_generation == 4
    assert all(epoch.observed_at[field] == NOW + timedelta(seconds=2) for field in CANONICAL_FIELD_ORDER[:24])


def test_raw_uptime_regression_marks_exactly_the_next_complete_epoch_as_reset() -> None:
    arm()
    state = minimum_state()
    assert record_component_device_uptime(120.0) is False
    assert record_component_device_uptime(4.0) is True

    emit_complete(state, NOW)
    (reset_epoch,) = component_cfg_source_epochs()
    assert reset_epoch.reset_detected is True

    emit_complete(state, NOW + timedelta(seconds=31))
    _, ordinary_epoch = component_cfg_source_epochs()
    assert ordinary_epoch.reset_detected is False


def test_unacknowledged_reset_epoch_survives_bounded_ordinary_buffer_eviction() -> None:
    arm()
    state = minimum_state()
    record_component_device_uptime(120.0)
    assert record_component_device_uptime(4.0) is True
    emit_complete(state, NOW)
    reset_epoch = component_cfg_source_epochs()[0]

    for sequence in range(1, 10):
        emit_complete(state, NOW + timedelta(seconds=31 * sequence))

    buffered = component_cfg_source_epochs()
    assert len(buffered) == 9  # one pinned fault plus the bounded eight ordinary epochs
    assert buffered[0].source_epoch_id == reset_epoch.source_epoch_id
    assert [epoch.reset_detected for epoch in buffered] == [True, *([False] * 8)]


@pytest.mark.parametrize("value", [True, -1, float("nan"), float("inf"), "not-a-number"])
def test_invalid_uptime_never_forges_a_reset(value: object) -> None:
    arm()
    assert record_component_device_uptime(120.0) is False
    assert record_component_device_uptime(value) is False
    emit_complete(minimum_state(), NOW)
    (epoch,) = component_cfg_source_epochs()
    assert epoch.reset_detected is False


def test_ingestor_callback_hook_is_separate_from_periodic_snapshot_flush() -> None:
    source = (Path(__file__).resolve().parents[1] / "ingestor" / "ingestor.py").read_text()
    callback = source.index("record_component_cfg_readback(cfg_param, val)")
    snapshot = source.index("Setpoint snapshot: write ESP32 configured values")
    assert callback < snapshot
    assert source.count("record_component_cfg_readback(cfg_param, val)") == 1
    assert source.count("record_component_device_uptime(value)") == 1
    assert source.count("client.subscribe_states(on_generation_state)") == 1
    assert source.count("record_component_entity_inventory(") == 1
    assert source.count("record_component_grid_firmware_revision(value, observed_at=observed_at)") == 1
    enumeration = source.index("entities, services = await client.list_entities_services()")
    inventory = source.index("record_component_entity_inventory(")
    subscription = source.index("client.subscribe_states(on_generation_state)")
    assert enumeration < inventory < subscription
    assert "shared.transport_generation != connection_generation" in source
    assert source.index('shared.esp32["state_subscription_generation"] = connection_generation') < source.index(
        "client.subscribe_states(on_generation_state)"
    )


def test_every_immediate_disconnect_signal_revokes_live_grid_evidence_first() -> None:
    source = (Path(__file__).resolve().parents[1] / "ingestor" / "ingestor.py").read_text()

    on_stop = source.index("async def on_stop(expected_disconnect: bool)")
    on_stop_signal = source.index("connection_lost.set()", on_stop)
    assert on_stop < source.index("if connection_generation is None:", on_stop) < on_stop_signal
    assert on_stop < source.index("clear_component_entity_inventory()", on_stop) < on_stop_signal
    assert (
        on_stop
        < source.index("clear_component_entity_inventory(connection_generation=connection_generation)", on_stop)
        < on_stop_signal
    )

    lease_loss = source.index("Writer lease LOST — SELF-FENCING")
    lease_signal = source.index("connection_lost.set()", lease_loss)
    assert (
        lease_loss
        < source.index("clear_component_entity_inventory(connection_generation=connection_generation)", lease_loss)
        < lease_signal
    )

    ping_failure = source.index("Keepalive ping failed")
    ping_signal = source.index("connection_lost.set()", ping_failure)
    assert (
        ping_failure
        < source.index("clear_component_entity_inventory(connection_generation=connection_generation)", ping_failure)
        < ping_signal
    )
