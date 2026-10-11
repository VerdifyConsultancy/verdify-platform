"""#963: HA's short state cannot destroy native estimator observations."""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ingestor"))
for key, value in {
    "DB_USER": "test",
    "DB_PASSWORD": "test",
    "DB_HOST": "localhost",
    "DB_PORT": "5432",
    "DB_NAME": "test",
}.items():
    os.environ.setdefault(key, value)

from test_experiment_workers import FakeConn, FakePool  # noqa: E402

import ingestor  # noqa: E402
from verdify_schemas.telemetry import MX_COMPACT_WIRE_KEYS, expand_moisture_exchange_wire  # noqa: E402

PAYLOAD = {
    "action": "vent_dehum",
    "reason": "vent_plus_heat_hold",
    "vent_vpd_gain_kpa": 0.062,
    "vent_held_vpd_gain_kpa": 0.055,
    "heat_vpd_gain_kpa": 0.041,
    "outdoor_fresh": True,
    "outdoor_data_age_s": 4294967295,
    "vent_overcools": False,
    "heat_assist_corun": True,
    "hold_required": True,
    "heat_assist_active": True,
    "heat_assist_timer_s": 240,
    "future_estimator_key": 1.25,
}
RAW = json.dumps(PAYLOAD, separators=(",", ":"))
WIRE_PAYLOAD = {"_mx": 1, **{alias: PAYLOAD[key] for alias, key in MX_COMPACT_WIRE_KEYS.items()}}
WIRE_RAW = json.dumps(WIRE_PAYLOAD, separators=(",", ":"))


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch):
    monkeypatch.setattr(ingestor.shared, "transport_generation", 0)
    monkeypatch.setattr(ingestor, "_observation_spool_enabled", lambda: False)
    monkeypatch.setattr(ingestor.state, "system", {"climate_action": "IDLE", "climate_priority_axis": "temp"})
    monkeypatch.setattr(ingestor.state, "pending_states", [])
    monkeypatch.setattr(ingestor.state, "key_to_object_id", {})
    monkeypatch.setattr(ingestor.state, "key_to_type", {})
    monkeypatch.delenv("VERDIFY_POLICY_VECTOR_MODE", raising=False)


def observe(object_id, value):
    ingestor.state.key_to_object_id[1] = object_id
    ingestor.state.key_to_type[1] = "text"
    ingestor.on_state_change(SimpleNamespace(key=1, state=value))


@pytest.mark.parametrize("object_id", ["climate_moisture_exchange", "climate_moisture_exchange_details"])
def test_old_and_new_firmware_keep_full_payload_in_analytics(object_id):
    assert len(RAW) > 255
    observe(object_id, RAW)
    conn = FakeConn([])
    assert asyncio.run(ingestor.write_climate_action_log(FakePool(conn), datetime.now(UTC)))
    inserts = conn.sql_calls("INSERT INTO v_runtime_climate_action_log_write")
    assert len(inserts) == 1
    stored = json.loads(inserts[0][2][17])
    assert stored["climate_moisture_exchange"] == PAYLOAD
    assert ingestor.state.pending_states == [("climate_moisture_exchange", RAW)]


@pytest.mark.parametrize("details_first", [True, False])
def test_short_visible_action_never_replaces_structured_observation(details_first):
    if details_first:
        observe("climate_moisture_exchange_details", RAW)
    observe("climate_moisture_exchange", "vent_dehum")
    if not details_first:
        assert "climate_moisture_exchange" not in ingestor.state.system
        observe("climate_moisture_exchange_details", RAW)
    assert ingestor.state.system["climate_moisture_exchange"] == RAW
    assert ingestor.state.system["climate_moisture_exchange_action"] == "vent_dehum"


def test_spooled_observations_keep_distinct_action_and_detail_entities(monkeypatch):
    events = []
    monkeypatch.setattr(ingestor, "_observation_spool_enabled", lambda: True)

    def accept(batch, _ts):
        events.extend(batch)
        return True

    monkeypatch.setattr(ingestor, "_accept_observations", accept)
    observe("climate_moisture_exchange", "none")
    observe("climate_moisture_exchange_details", RAW)
    assert events == [
        ("system_state", {"entity": "climate_moisture_exchange_action", "value": "none"}),
        ("system_state", {"entity": "climate_moisture_exchange", "value": RAW}),
    ]
    assert not ingestor.state.pending_states


def test_ha_entity_is_short_and_backend_detail_is_default_disabled():
    hardware = (ROOT / "firmware/greenhouse/hardware.yaml").read_text()
    detail = hardware.split("id: gh_climate_moisture_exchange_details\n", 1)[1].split("  - platform:", 1)[0]
    assert 'name: "Climate Moisture Exchange Details"' in detail
    assert "disabled_by_default: true" in detail
    assert "internal: true" not in detail  # must still enumerate through native API
    controls = (ROOT / "firmware/greenhouse/controls.yaml").read_text()
    assert "id(gh_climate_moisture_exchange).publish_state(climate_decision.moisture_exchange_action);" in controls
    assert "id(gh_climate_moisture_exchange_details).publish_state(moisture_exchange);" in controls
    action_function = (
        (ROOT / "firmware/lib/greenhouse_logic.h")
        .read_text()
        .split("inline const char* moisture_exchange_action_name", 1)[1]
        .split("inline MoistureExchangeEstimate", 1)[0]
    )
    actions = re.findall(r'return "([^"]+)";', action_function)
    assert actions and max(map(len, actions)) < 255


def test_compact_wire_restores_canonical_analytics_keys():
    assert len(WIRE_RAW) < 255
    observe("climate_moisture_exchange_details", WIRE_RAW)
    assert json.loads(ingestor.state.system["climate_moisture_exchange"]) == {
        key: value for key, value in PAYLOAD.items() if key != "future_estimator_key"
    }
    conn = FakeConn([])
    assert asyncio.run(ingestor.write_climate_action_log(FakePool(conn), datetime.now(UTC)))
    stored = json.loads(conn.sql_calls("INSERT INTO v_runtime_climate_action_log_write")[0][2][17])
    assert stored["climate_moisture_exchange"]["vent_held_vpd_gain_kpa"] == 0.055
    assert stored["climate_moisture_exchange"]["outdoor_data_age_s"] == 4294967295
    assert "_mx" not in stored["climate_moisture_exchange"]


@pytest.mark.parametrize("version", [True, "1", 2, None])
def test_unsupported_wire_marks_missing_detail_instead_of_retaining_stale_payload(version):
    observe("climate_moisture_exchange_details", RAW)
    observe("climate_moisture_exchange_details", json.dumps({**WIRE_PAYLOAD, "_mx": version}))
    assert json.loads(ingestor.state.system["climate_moisture_exchange"]) == {
        "raw": "unsupported_moisture_exchange_detail"
    }


def test_incomplete_or_ambiguous_wire_does_not_guess_field_meaning():
    for payload in ({"_mx": 1}, {**WIRE_PAYLOAD, "action": "conflicting"}):
        with pytest.raises(ValueError):
            expand_moisture_exchange_wire(payload)
    assert expand_moisture_exchange_wire(PAYLOAD) == PAYLOAD  # legacy extras remain tolerated


def test_actual_native_formatter_bounds_normal_and_extreme_states(tmp_path):
    controls = (ROOT / "firmware/greenhouse/controls.yaml").read_text()
    block = controls.split("const int moisture_exchange_len =", 1)[1].split("const uint32_t climate_diag_now_ms", 1)[0]
    source = (
        r"""
    #include <cstdio>
    #include <cstdint>
    #include <cstring>
    #include <limits>
    #define ESP_LOGE(...) ((void)0)
    struct Decision {
      const char* moisture_exchange_action = "vent_humidify";
      const char* moisture_exchange_reason = "vent_plus_heat_hold";
      float moisture_vent_vpd_gain_kpa = -999.999f;
      float moisture_vent_held_vpd_gain_kpa = -999.999f;
      float moisture_heat_vpd_gain_kpa = -999.999f;
      bool moisture_outdoor_fresh = false, moisture_vent_overcools = false;
      bool moisture_heat_assist_corun = false, moisture_hold_required = false;
      bool moisture_heat_assist_active = false;
      uint32_t moisture_heat_assist_timer_ms = UINT32_MAX;
    };
    int main(int argc, char**) {
      Decision climate_decision;
      if (argc > 1) {
        climate_decision.moisture_vent_vpd_gain_kpa = std::numeric_limits<float>::max();
        climate_decision.moisture_vent_held_vpd_gain_kpa = std::numeric_limits<float>::max();
        climate_decision.moisture_heat_vpd_gain_kpa = std::numeric_limits<float>::max();
      }
      struct { uint32_t outdoor_data_age_s = UINT32_MAX; } sensor_in;
      char moisture_exchange[256];
    """
        + "const int moisture_exchange_len ="
        + block
        + r"""
      if (std::strlen(moisture_exchange) > 255) return 1;
      puts(moisture_exchange);
    }
    """
    )
    cpp, binary = tmp_path / "format.cpp", tmp_path / "format"
    cpp.write_text(source)
    subprocess.run(["g++", "-std=c++17", str(cpp), "-o", str(binary)], check=True, capture_output=True)
    normal = json.loads(subprocess.check_output([str(binary)], text=True))
    assert expand_moisture_exchange_wire(normal)["outdoor_data_age_s"] == 4294967295
    extreme = json.loads(subprocess.check_output([str(binary), "extreme"], text=True))
    assert extreme == {"raw": "moisture_exchange_payload_unavailable"}
