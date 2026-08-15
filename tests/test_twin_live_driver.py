"""twin/live_driver.py unit tests (#587, audit §8.9).

Covers the wire-field -> sp_* input mapping (with drift guards against both
replay_emit.cpp and the Python wire registry), the input-row precedence
rules, the §8.9 classification matrix, and the boot-reset behavior — all
without a DB or the compiled follower (a stub stands in).
"""

from __future__ import annotations

import importlib.util
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


driver = _load("twin_live_driver", REPO_ROOT / "twin" / "live_driver.py")

TICK = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


def comparable_row(**overrides) -> dict:
    """A view row that classifies as comparable (agreement-eligible)."""
    row = {
        "greenhouse_id": "vallery",
        "ts": TICK,
        "temp_avg": 78.2,
        "rh_avg": 61.0,
        "vpd_avg": 1.12,
        "indoor_dew_point": 63.9,
        "enthalpy_delta": -4.0,
        "solar_irradiance_w_m2": 610.0,
        "outdoor_temp_f": 88.0,
        "outdoor_rh_pct": 22.0,
        "outdoor_dewpoint_f": 45.1,
        "outdoor_data_age_s": 42,
        "occupied": False,
        "clock_valid": True,
        "boot_event_ts": TICK - timedelta(days=2),
        "snapshot_id": 41,
        "device_id": "esp32-gh",
        "snapshot_age_s": 300,
        "device_generation": 7,
        "apply_state": "active",
        "vector_id": "6a3ffb44-0000-4000-8000-000000000001",
        "policy_hash_match": True,
        "validity_contains_tick": True,
        "sp_payload": {"temp_high": 84.0, "temp_low": 66.0},
        "sp_asof": TICK - timedelta(minutes=9),
        "relay_readback_asof": TICK - timedelta(minutes=1),
        "relay_readback_age_s": 60,
        **{f"live_relay_{r}": False for r in driver.RELAYS},
    }
    row.update(overrides)
    return row


def decision_row(**overrides) -> dict:
    dec = {
        "ts": "2026-08-10 12:00:00",
        "mode": "IDLE",
        **{f"relay_{r}": "0" for r in driver.RELAYS},
        "mist_stage": "0",
        "reason": "in_band",
        "override_bits": "0",
        "climate_action": "hold",
    }
    dec.update(overrides)
    return dec


# ── Mapping drift guards ─────────────────────────────────────────────────────


def test_frozen_sp_columns_match_replay_emit_cpp():
    source = (REPO_ROOT / "firmware" / "test" / "replay_emit.cpp").read_text()
    parsed = driver.parse_expected_sp_columns(source)
    assert parsed, "EXPECTED_SP_COLUMNS not found in replay_emit.cpp"
    assert parsed == driver.FROZEN_EXPECTED_SP_COLUMNS, (
        "twin/live_driver.py FROZEN_EXPECTED_SP_COLUMNS drifted from "
        "replay_emit.cpp EXPECTED_SP_COLUMNS — update the frozen mirror"
    )


def test_policy_mapping_partitions_the_wire_schema_exactly():
    from verdify_schemas.policy_vector import wire_fields

    names = {d.name for d in wire_fields()}
    driver.assert_wire_mapping_partition(names)  # raises SystemExit on drift
    assert set(driver.POLICY_TO_SP) | driver.UNMAPPED_WIRE_FIELDS == names
    assert not set(driver.POLICY_TO_SP) & driver.UNMAPPED_WIRE_FIELDS


def test_mapping_partition_rejects_unclassified_and_stale_fields():
    import pytest

    with pytest.raises(SystemExit):
        driver.assert_wire_mapping_partition(set(driver.POLICY_TO_SP) | {"brand_new_field"})
    with pytest.raises(SystemExit):
        driver.assert_wire_mapping_partition(set(driver.POLICY_TO_SP))  # unmapped now stale


def test_every_mapped_target_is_a_real_harness_column():
    columns = set(driver.expected_sp_columns())
    for field, col in driver.POLICY_TO_SP.items():
        assert col in columns, f"{field} maps to unknown harness column {col}"
    for param, col in driver.SP_PARAM_TO_COLUMN.items():
        assert col in columns, f"{param} maps to unknown harness column {col}"


def test_wire_and_setpoint_mappings_target_disjoint_columns():
    """The decoded device-confirmed vector owns its columns exclusively; the
    dispatcher projection may never collide with treatment truth."""
    assert not set(driver.POLICY_TO_SP.values()) & set(driver.SP_PARAM_TO_COLUMN.values())


def test_input_header_is_base_plus_full_sp_surface():
    header = driver.input_header()
    assert header[: len(driver.BASE_INPUT_COLUMNS)] == driver.BASE_INPUT_COLUMNS
    assert set(driver.expected_sp_columns()) <= set(header)
    assert len(header) == len(set(header))


# ── Input-row construction ───────────────────────────────────────────────────


def test_build_input_row_policy_overrides_setpoint_batch():
    row = comparable_row(sp_payload={"temp_high": 84.0, "temp_hysteresis": 1.0, "sw_dwell_gate_enabled": 1.0})
    policy = {"temp_hysteresis": 2.5, "sw_dwell_gate_enabled": True, "dwell_gate_ms": 90000.0}
    out = driver.build_input_row(row, policy)
    assert out["sp_temp_high"] == "84"  # non-wire posture from the batch
    assert out["sp_temp_hysteresis"] == "2.5"  # wire value wins over batch
    assert out["sp_sw_dwell_gate_enabled"] == "1"
    assert out["sp_dwell_gate_ms"] == "90000"


def test_build_input_row_without_policy_leaves_wire_columns_blank():
    out = driver.build_input_row(comparable_row(sp_payload={}), None)
    assert out["sp_temp_hysteresis"] == ""  # harness falls back to defaults
    assert out["sp_fog_escalation_kpa"] == ""
    assert out["temp_avg"] == "78.2"
    assert out["occupied"] == "f"
    assert out["ts"] == "2026-08-10 12:00:00"


def test_build_input_row_formats_booleans_and_occupancy():
    out = driver.build_input_row(comparable_row(occupied=True), {**dict.fromkeys(driver.POLICY_TO_SP, 1.0)})
    assert out["occupied"] == "t"
    assert out["sp_sw_summer_vent_enabled"] == "1"
    empty = driver.build_input_row(comparable_row(outdoor_temp_f=None), None)
    assert empty["outdoor_temp_f"] == ""


# ── §8.9 classification matrix ───────────────────────────────────────────────


def classify(row, *, warmed_up=True, vector_decoded=True, snap_max=21600, relay_max=0):
    return driver.classify_tick(
        row,
        warmed_up=warmed_up,
        vector_decoded=vector_decoded,
        snapshot_max_age_s=snap_max,
        relay_max_age_s=relay_max,
    )


def test_classification_matrix_gaps():
    assert classify(comparable_row(temp_avg=None)) == (driver.CLASS_GAP, "sensor_missing")
    assert classify(comparable_row(clock_valid=False)) == (driver.CLASS_GAP, "clock_invalid")
    assert classify(comparable_row(clock_valid=None)) == (driver.CLASS_GAP, "clock_invalid")
    assert classify(comparable_row(snapshot_id=None)) == (driver.CLASS_GAP, "no_device_snapshot")
    assert classify(comparable_row(snapshot_age_s=99999)) == (driver.CLASS_GAP, "stale_device_snapshot")
    assert classify(comparable_row(live_relay_vent=None)) == (driver.CLASS_GAP, "relay_readback_missing")
    assert classify(comparable_row(relay_readback_age_s=4000), relay_max=1800) == (
        driver.CLASS_GAP,
        "relay_readback_stale",
    )


def test_classification_matrix_unmatched_state():
    assert classify(comparable_row(apply_state="rom_baseline")) == (driver.CLASS_UNMATCHED, "apply_state:rom_baseline")
    assert classify(comparable_row(apply_state=None)) == (driver.CLASS_UNMATCHED, "apply_state:unknown")
    assert classify(comparable_row(vector_id=None)) == (driver.CLASS_UNMATCHED, "vector_unknown")
    assert classify(comparable_row(policy_hash_match=False)) == (driver.CLASS_UNMATCHED, "content_hash_mismatch")
    assert classify(comparable_row(validity_contains_tick=False)) == (driver.CLASS_UNMATCHED, "outside_validity")
    assert classify(comparable_row(), vector_decoded=False) == (driver.CLASS_UNMATCHED, "vector_decode_failed")


def test_classification_warm_up_and_agreement_eligibility():
    assert classify(comparable_row(), warmed_up=False) == (driver.CLASS_WARM_UP, "warm_up_window")
    assert classify(comparable_row()) == (driver.CLASS_AGREEMENT, None)


def test_classification_ordering_feed_gap_beats_identity_and_warmup():
    row = comparable_row(temp_avg=None, policy_hash_match=False)
    assert classify(row, warmed_up=False) == (driver.CLASS_GAP, "sensor_missing")
    row = comparable_row(policy_hash_match=False)
    assert classify(row, warmed_up=False) == (driver.CLASS_UNMATCHED, "content_hash_mismatch")


def test_classification_vocabulary_matches_migration_check():
    sql = (REPO_ROOT / "db" / "migrations" / "211-twin-asof-input.sql").read_text()
    match = re.search(r"classification\s+text NOT NULL CHECK \(classification IN \(([^)]+)\)", sql)
    assert match
    assert tuple(re.findall(r"'([a-z_]+)'", match.group(1))) == driver.CLASSIFICATIONS


# ── Action comparison + boot detection ───────────────────────────────────────


def test_compare_actions_agree_and_diverge():
    agree, twin, live = driver.compare_actions(decision_row(), comparable_row())
    assert agree and twin == live
    agree, twin, _ = driver.compare_actions(decision_row(relay_fog="1"), comparable_row())
    assert not agree
    assert twin["fog"] is True


def test_detect_boot_only_on_new_boot_between_ticks():
    prev = TICK - timedelta(minutes=1)
    assert driver.detect_boot(comparable_row(boot_event_ts=TICK - timedelta(seconds=30)), prev)
    assert not driver.detect_boot(comparable_row(boot_event_ts=TICK - timedelta(days=2)), prev)
    assert not driver.detect_boot(comparable_row(boot_event_ts=None), prev)
    assert not driver.detect_boot(comparable_row(), None)


# ── process_tick with a stubbed follower ─────────────────────────────────────


class StubFollower:
    def __init__(self, decision=None, warmed=True):
        self._decision = decision if decision is not None else decision_row()
        self.ticks_since_start = 1000 if warmed else 0
        self.restarts = 0

    def start(self):
        self.restarts += 1
        self.ticks_since_start = 0

    def step(self, row):
        self.ticks_since_start += 1
        return self._decision


def run_tick(row, follower, prev_tick_ts=TICK - timedelta(minutes=1), warmup_ticks=30):
    return driver.process_tick(
        row,
        follower,
        warmup_ticks=warmup_ticks,
        snapshot_max_age_s=21600,
        relay_max_age_s=0,
        prev_tick_ts=prev_tick_ts,
        reset_gap_s=600,
    )


def test_process_tick_agreement(monkeypatch):
    monkeypatch.setattr(driver, "decode_vector", lambda _: dict.fromkeys(driver.POLICY_TO_SP, 1.0))
    params, reset = run_tick(comparable_row(), StubFollower())
    assert not reset
    assert params["classification"] == driver.CLASS_AGREEMENT
    assert params["action_agree"] is True
    assert params["policy_hash_match"] is True
    assert params["twin_relay_fog"] is False


def test_process_tick_divergence(monkeypatch):
    monkeypatch.setattr(driver, "decode_vector", lambda _: dict.fromkeys(driver.POLICY_TO_SP, 1.0))
    params, _ = run_tick(comparable_row(), StubFollower(decision=decision_row(relay_fan1="1")))
    assert params["classification"] == driver.CLASS_DIVERGENCE
    assert params["action_agree"] is False


def test_process_tick_boot_resets_and_classifies_warm_up(monkeypatch):
    monkeypatch.setattr(driver, "decode_vector", lambda _: dict.fromkeys(driver.POLICY_TO_SP, 1.0))
    follower = StubFollower()
    row = comparable_row(boot_event_ts=TICK - timedelta(seconds=30))
    params, reset = run_tick(row, follower)
    assert reset and follower.restarts == 1
    assert params["classification"] == driver.CLASS_WARM_UP
    assert params["gap_reason"] == "boot_reset"


def test_process_tick_feed_gap_resets_follower(monkeypatch):
    monkeypatch.setattr(driver, "decode_vector", lambda _: dict.fromkeys(driver.POLICY_TO_SP, 1.0))
    follower = StubFollower()
    params, reset = run_tick(comparable_row(), follower, prev_tick_ts=TICK - timedelta(hours=2))
    assert reset and follower.restarts == 1
    assert params["classification"] == driver.CLASS_WARM_UP
    assert params["gap_reason"] == "feed_gap_reset"


def test_process_tick_malformed_decision_is_gap(monkeypatch):
    monkeypatch.setattr(driver, "decode_vector", lambda _: dict.fromkeys(driver.POLICY_TO_SP, 1.0))

    class BadFollower(StubFollower):
        def step(self, row):
            self.ticks_since_start += 1
            return None

    params, _ = run_tick(comparable_row(), BadFollower())
    assert params["classification"] == driver.CLASS_GAP
    assert params["gap_reason"] == "twin_decision_malformed"
    assert params["action_agree"] is None


def test_process_tick_unmatched_never_reports_agreement(monkeypatch):
    monkeypatch.setattr(driver, "decode_vector", lambda _: dict.fromkeys(driver.POLICY_TO_SP, 1.0))
    params, _ = run_tick(comparable_row(policy_hash_match=False), StubFollower())
    assert params["classification"] == driver.CLASS_UNMATCHED
    # action still recorded for triage, but never as agreement
    assert params["action_agree"] is True
    assert params["gap_reason"] == "content_hash_mismatch"


def test_decode_vector_round_trip_against_real_codec():
    from verdify_schemas.policy_vector import encode_policy_vector, wire_fields

    values = {}
    for defn in wire_fields():
        if defn.wire_kind == "bool":
            values[defn.name] = True
        else:
            from verdify_schemas.tunable_registry import wire_value_bounds

            lo, hi = wire_value_bounds(defn.name)
            values[defn.name] = lo
    decoded = driver.decode_vector(encode_policy_vector(values))
    assert decoded is not None
    assert set(decoded) == set(values)
    assert driver.decode_vector(b"garbage") is None
    assert driver.decode_vector(None) is None
