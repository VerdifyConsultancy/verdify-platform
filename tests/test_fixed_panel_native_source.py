"""Native callback receive evidence stays distinct from 60 s climate snapshots."""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
INGESTOR_PATH = str(ROOT / "ingestor")
if INGESTOR_PATH not in sys.path:
    sys.path.insert(0, INGESTOR_PATH)

os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_HOST", "127.0.0.1")
os.environ.setdefault("DB_PORT", "5432")
os.environ.setdefault("DB_NAME", "test")

import fixed_panel_source as source  # noqa: E402
import shared  # noqa: E402
from entity_map import CLIMATE_MAP  # noqa: E402

import ingestor  # noqa: E402


def test_declared_routes_match_native_entity_map_and_firmware_modbus_addresses():
    hardware = (ROOT / "firmware/greenhouse/hardware.yaml").read_text()
    sensors = (ROOT / "firmware/greenhouse/sensors.yaml").read_text()
    for zone, route in source.ROUTES.items():
        assert f"- id: {zone}_wall_probe\n    address: {route['modbus_address']}" in hardware
        assert CLIMATE_MAP[route["temp"]] == f"temp_{zone}"
        assert CLIMATE_MAP[route["rh"]] == f"rh_{zone}"
        assert (
            f'id: {zone}_wall_temperature\n    name: "{zone.title()} Temp (°F)"\n    modbus_controller_id: {zone}_wall_probe'
            in sensors
        )
        assert (
            f'id: {zone}_wall_humidity\n    name: "{zone.title()} RH (%)"\n    modbus_controller_id: {zone}_wall_probe'
            in sensors
        )
    assert len(source.OBJECT_FIELDS) == 6
    assert len(source.ROUTE_SOURCE_SHA256) == 64


def test_initial_replay_is_excluded_and_reports_do_not_carry_values_forward():
    tracker = source.NativeProbeTracker("runtime-a")
    tracker.mark_connected(7)
    now = datetime(2026, 9, 28, 14, 0, tzinfo=UTC)
    object_id = source.ROUTES["north"]["temp"]
    assert not tracker.observe(object_id, 77.0, now, 7)
    assert tracker.observe(object_id, 77.0, now + timedelta(seconds=10), 7)
    report = tracker.drain(now + timedelta(minutes=1))
    assert report["transport_generation"] == 7
    assert report["initial_state_callbacks_excluded"] == 1
    assert report["generation_changed_since_previous_report"]
    assert report["callbacks"] == [
        {
            "zone": "north",
            "metric": "temp",
            "object_id": object_id,
            "value": 77.0,
            "received_at": (now + timedelta(seconds=10)).isoformat(),
            "callback_count": 1,
        }
    ]
    assert report["physical_serial_verified"] is False
    assert report["modbus_poll_time_verified"] is False
    assert tracker.drain(now + timedelta(minutes=2))["callbacks"] == []


def test_generation_change_discards_pending_and_late_or_bad_callbacks():
    tracker = source.NativeProbeTracker("runtime-b")
    now = datetime(2026, 9, 28, 14, 0, tzinfo=UTC)
    object_id = source.ROUTES["west"]["rh"]
    tracker.mark_connected(3)
    assert not tracker.observe(object_id, 51.0, now, 3)
    assert tracker.observe(object_id, 52.0, now + timedelta(seconds=10), 3)
    tracker.mark_connected(4)
    assert not tracker.observe(object_id, 53.0, now + timedelta(seconds=20), 3)
    assert not tracker.observe(object_id, float("nan"), now + timedelta(seconds=21), 4)
    assert not tracker.observe(object_id, 101.0, now + timedelta(seconds=22), 4)
    assert not tracker.observe(object_id, 54.0, now + timedelta(seconds=23), 4)
    assert tracker.observe(object_id, 55.0, now + timedelta(seconds=24), 4)
    report = tracker.drain(now + timedelta(minutes=1))
    assert [row["value"] for row in report["callbacks"]] == [55.0]
    assert report["transport_generation"] == 4
    assert report["generation_changed_since_previous_report"]
    assert report["initial_state_callbacks_excluded"] == 1


def test_ingestor_records_only_explicitly_fenced_native_callbacks(monkeypatch):
    tracker = source.NativeProbeTracker("runtime-test")
    tracker.mark_connected(11)
    monkeypatch.setattr(ingestor.state, "fixed_panel_native", tracker)
    monkeypatch.setattr(shared, "transport_generation", 11)
    now = datetime(2026, 9, 28, 14, 0, tzinfo=UTC)
    monkeypatch.setattr(ingestor, "_equipment_source_now", lambda: now)
    ingestor.state.key_to_object_id[87654] = source.ROUTES["east"]["temp"]
    ingestor.state.key_to_type[87654] = "sensor"
    callback = SimpleNamespace(key=87654, state=75.0)
    try:
        ingestor.on_state_change(callback)
        assert tracker.drain(now)["callbacks"] == []
        ingestor.on_state_change(callback, native_generation=11)
        ingestor.on_state_change(callback, native_generation=11)
        assert len(tracker.drain(now)["callbacks"]) == 1
    finally:
        ingestor.state.key_to_object_id.pop(87654, None)
        ingestor.state.key_to_type.pop(87654, None)
        ingestor.state.climate.pop("temp_east", None)
