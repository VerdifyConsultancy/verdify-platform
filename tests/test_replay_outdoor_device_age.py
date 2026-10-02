"""Original-event causal age, with no cached subscription or future freshness credit."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("replay_outdoor_age", ROOT / "scripts/replay_outdoor_age.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
OutdoorAge = module.OutdoorAge


def event(second=0, generation=1, value=None, kind="weather", runtime="runtime-a"):
    return {
        "ts": f"2026-10-02T05:00:{second:02d}+00:00",
        "event_kind": kind,
        "event_id": f"event-{generation}-{second}",
        "event_sha256": "a" * 64,
        "source_runtime_instance_id": runtime,
        "source_connection_generation": str(generation),
        "value": json.dumps({"outdoor_data_age_s": 44, "outdoor_fresh": True}) if value is None else value,
    }


def climate(second=0):
    return {
        "ts": f"2026-10-02T05:00:{second:02d}+00:00",
        "outdoor_data_age_s": "12",
        "outdoor_observation_ts": "2026-10-02T04:59:48+00:00",
    }


def ready():
    tracker = OutdoorAge()
    tracker.observe(event(0))
    tracker.observe(event(10))
    return tracker


def test_original_event_age_advances_and_keeps_identity_not_flush_clock():
    tracker = ready()
    actual = tracker.project(climate(20))
    assert actual["outdoor_data_age_s"] == 54
    assert actual["outdoor_device_reported_age_s"] == 44
    assert actual["outdoor_observation_ts"] == event(10)["ts"]
    assert actual["outdoor_device_event_id"] == "event-1-10"
    assert actual["outdoor_device_runtime_id"] == "runtime-a"
    assert actual["outdoor_device_generation"] == "1"
    assert actual["outdoor_device_event_sha256"] == "a" * 64
    assert actual["outdoor_freshness_basis"] == "device_reported_original_callback"


def test_first_subscription_event_per_runtime_and_generation_is_not_fresh():
    tracker = OutdoorAge()
    tracker.observe(event())
    assert tracker.project(climate())["outdoor_data_age_s"] == ""
    tracker.observe(event(10))
    assert tracker.project(climate(10))["outdoor_data_age_s"] == 44
    tracker.observe(event(20, generation=2))
    assert tracker.project(climate(20))["outdoor_data_age_s"] == ""
    tracker.observe(event(30, generation=2))
    assert tracker.project(climate(30))["outdoor_data_age_s"] == 44
    tracker.observe(event(40, generation=2, runtime="runtime-b"))
    assert tracker.project(climate(40))["outdoor_data_age_s"] == ""


@pytest.mark.parametrize(
    "value",
    [
        "",
        "{",
        "{}",
        '{"outdoor_data_age_s":null}',
        '{"outdoor_data_age_s":true,"outdoor_fresh":true}',
        '{"outdoor_data_age_s":-1,"outdoor_fresh":true}',
        '{"outdoor_data_age_s":NaN,"outdoor_fresh":true}',
        '{"outdoor_data_age_s":44}',
        '{"outdoor_data_age_s":100000,"outdoor_fresh":true}',
    ],
)
def test_missing_malformed_subsequent_event_never_falls_back(value):
    tracker = ready()
    tracker.observe(event(20, value=value))
    actual = tracker.project(climate(20))
    assert actual["outdoor_data_age_s"] == ""
    assert actual["outdoor_freshness_basis"] == "native_unavailable_stale"


@pytest.mark.parametrize(
    "value",
    [
        '{"outdoor_data_age_s":99999,"outdoor_fresh":false}',
        '{"outdoor_data_age_s":44,"outdoor_fresh":false}',
        '{"outdoor_data_age_s":99999,"outdoor_fresh":true}',
    ],
)
def test_sentinel_or_explicit_not_fresh_retains_measured_event_but_is_stale(value):
    tracker = ready()
    tracker.observe(event(20, value=value))
    actual = tracker.project(climate(25))
    assert actual["outdoor_data_age_s"] == 99999
    assert actual["outdoor_device_event_id"] == "event-1-20"
    assert actual["outdoor_device_reported_age_s"] == json.loads(value)["outdoor_data_age_s"]


@pytest.mark.parametrize("kind", ["connected", "gap"])
def test_transport_boundary_invalidates_previous_weather(kind):
    tracker = ready()
    tracker.observe(event(20, kind=kind))
    assert tracker.project(climate(20))["outdoor_data_age_s"] == ""


def test_historical_proxy_stays_distinct_before_exact_age_emitter():
    tracker = OutdoorAge()
    tracker.observe(event(kind="connected"))
    tracker.observe(event(10, value='{"action":"hold"}'))
    actual = tracker.project(climate(10))
    assert actual["outdoor_data_age_s"] == "12"
    assert actual["outdoor_freshness_basis"] == "conservative_change_observation"
    assert "outdoor_device_event_id" not in actual


def test_future_original_event_is_refused():
    tracker = ready()
    with pytest.raises(ValueError, match="future outdoor source event"):
        tracker.project(climate(9))


def test_fractional_elapsed_age_is_never_truncated_younger():
    tracker = ready()
    row = climate(10)
    row["ts"] = "2026-10-02T05:00:10.001+00:00"
    assert tracker.project(row)["outdoor_data_age_s"] == 45


def test_export_merge_uses_only_asof_original_events_and_preserves_sentinel(tmp_path):
    import csv
    import subprocess
    import sys

    def write(name, rows, fields):
        with (tmp_path / name).open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
            writer.writeheader()
            writer.writerows(rows)

    rows = [climate(second) for second in (0, 10, 20, 30)]
    write("climate.tsv", rows, list(rows[0]))
    write("setpoints.tsv", [], ["ts", "config_payload"])
    write("system.tsv", [], ["ts", "entity", "value"])
    write("equipment.tsv", [], ["ts", "equipment", "state"])
    events = [event(0), event(10), event(20, value='{"outdoor_data_age_s":99999,"outdoor_fresh":false}'), event(40)]
    write("outdoor-device.tsv", events, list(events[0]))
    script = (ROOT / "scripts/export-replay-overrides.sh").read_text().split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
    output = tmp_path / "out.tsv"
    subprocess.run(
        [sys.executable, "-", str(tmp_path), str(output), str(ROOT / "scripts")], input=script, text=True, check=True
    )
    with output.open() as handle:
        actual = list(csv.DictReader(handle, delimiter="\t"))
    assert [row["outdoor_data_age_s"] for row in actual] == ["", "44", "99999", "99999"]
    assert actual[-1]["outdoor_device_event_id"] == "event-1-20"  # 40s event never leaks backwards
    assert actual[-1]["outdoor_observation_ts"] == event(20)["ts"]
