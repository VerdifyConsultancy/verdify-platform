"""Raw cfg-source epoch tests; no database, network, or device access."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

_INGESTOR_PATH = str(Path(__file__).resolve().parents[1] / "ingestor")
if _INGESTOR_PATH not in sys.path:
    sys.path.insert(0, _INGESTOR_PATH)

import shared  # noqa: E402
from tasks.component_experiment import (  # noqa: E402
    RevisionSet,
    component_cfg_source_epochs,
    configure_component_cfg_source,
    record_component_cfg_readback,
)

from verdify_schemas.component_executor import CANONICAL_FIELD_ORDER, ENTITY_GRIDS
from verdify_schemas.policy_vector import decode_policy_vector

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
    shared.cfg_readback.clear()
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


def test_ingestor_callback_hook_is_separate_from_periodic_snapshot_flush() -> None:
    source = (Path(__file__).resolve().parents[1] / "ingestor" / "ingestor.py").read_text()
    callback = source.index("record_component_cfg_readback(cfg_param, val)")
    snapshot = source.index("Setpoint snapshot: write ESP32 configured values")
    assert callback < snapshot
    assert source.count("record_component_cfg_readback(cfg_param, val)") == 1
