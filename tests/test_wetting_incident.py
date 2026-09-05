"""#778: bounded offset-aware projection, honest missingness and permanent hold."""

import csv
import importlib.util
import io
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "wetting_incident", ROOT / "research/planner-efficacy/wetting_incident.py"
)
incident = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(incident)


def csv_bytes(rows):
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode()


def inputs():
    hourly = []
    for hour in (15, 16, 17):
        row = {field: 0 for field in incident.HOURLY_FIELDS}
        row.update(
            hour_start_local=f"2026-09-04 {hour}:00:00",
            hour_start_utc=f"2026-09-04 {hour + 6}:00:00+00",
            timezone="America/Denver",
            climate_sample_count=60,
            temp_avg_f=98,
            vpd_avg_kpa=5,
        )
        hourly.append(row)
    climate = [
        {
            "bucket_local": "2026-09-04 15:30",
            "temp_avg_f": 100.48,
            "vpd_avg_kpa": 5.395,
            "outdoor_temp_f": 94.17,
            "source_samples": 5,
            "water_total_gal": 600,
            "mister_water_today_gal": 157.38,
        }
    ]
    projection = incident.project_logs(
        [b"2026-09-04T15:30:00-06:00 2026-09-04T15:30:00 INFO Occupancy: pushed empty to ESP32 via ha_sensor_sync\n"]
    )
    return hourly, climate, projection


def test_utc_offsets_and_nanosecond_timestamps_not_lexical_local_comparison():
    records = [
        b"2026-09-04T14:30:28.120995164-06:00 2026-09-04T14:30:28 INFO Occupancy: pushed empty to ESP32\n",
        b"2026-09-04T21:00:00Z 2026-09-04T15:00:00 INFO Connected to ESP32 (gap: 8s since disconnect)\n",
        b"2026-09-04T18:45:00-06:00 2026-09-04T18:45:00 WARNING ESP32 connection lost (unexpected)\n",
        b"2026-09-04T20:30:00-06:00 2026-09-04T20:30:00 INFO Occupancy: pushed occupied to ESP32\n",
    ]
    report = incident.project_logs(records)
    assert report["window_timestamped_records"] == 3
    assert report["events"][0]["ts"] == "2026-09-04T20:30:28.120995+00:00"
    assert report["events"][1]["reported_gap_seconds"] == 8
    assert report["window_raw_stream_sha256"] == incident.digest(b"".join(records[:3]))


def test_free_form_log_text_is_never_retained_or_echoed():
    raw = b"2026-09-04T21:00:00Z 2026-09-04T15:00:00 INFO Occupancy: pushed empty to ESP32 arbitrary_private_text\n"
    report = incident.project_logs([raw, b"not a timestamp arbitrary_private_text\n"])
    assert "arbitrary_private_text" not in json.dumps(report)
    assert report["raw_messages_retained"] is False
    assert report["unparseable_records_in_supplied_stream"] == 1
    assert report["events"][0]["source_record_sha256"] == incident.digest(raw)


def test_quoted_narrative_is_not_an_operational_event():
    raw = b"2026-09-04T21:00:00Z 2026-09-04T15:00:00 INFO Plan says Occupancy: pushed occupied to ESP32\n"
    report = incident.project_logs([raw])
    assert report["events"] == []
    assert report["window_timestamped_records"] == 1


def test_interruption_and_counter_plateau_never_authorize_wetting_or_name_a_cause():
    hourly, climate, projection = inputs()
    report = incident.analyze(csv_bytes(hourly), csv_bytes(climate), json.dumps(projection).encode())
    assert report["three_hour_recorded_zero_wetting"] is True
    assert report["equipment_observation_coverage_verified"] is False
    assert "fills absent equipment intervals with zero" in " ".join(report["limitations"])
    assert report["five_minute_peak_vpd_bin"]["vpd_avg_kpa"] == 5.395
    assert report["counter_plateau_15_to_18_local"]["water_total_gal"]["is_budget_limit"] is False
    assert report["counter_plateau_15_to_18_local"]["mister_water_today_gal"]["max"] == 157.38
    assert report["log_event_counts"] == {"occupancy_push:empty": 1}
    assert report["disposition"] == "unresolved_hold"
    assert report["physical_wetting_proof_allowed"] is False


@pytest.mark.parametrize("change", ["missing_runtime", "missing_hour", "missing_climate", "not_zero"])
def test_partial_evidence_does_not_become_complete_three_hour_zero_wetting(change):
    hourly, climate, projection = inputs()
    if change == "missing_runtime":
        hourly[1]["runtime_fog_min"] = ""
    elif change == "missing_hour":
        hourly.pop()
    elif change == "missing_climate":
        hourly[0]["climate_sample_count"] = 0
    elif change == "not_zero":
        hourly[0]["runtime_fog_min"] = 1
    report = incident.analyze(csv_bytes(hourly), csv_bytes(climate), json.dumps(projection).encode())
    assert report["three_hour_recorded_zero_wetting"] is (False if change == "not_zero" else None)
    assert report["physical_wetting_proof_allowed"] is False


def test_duplicates_and_utc_local_mismatches_refuse_analysis():
    hourly, climate, projection = inputs()
    raw = json.dumps(projection).encode()
    with pytest.raises(ValueError, match="duplicate"):
        incident.analyze(csv_bytes(hourly + [hourly[0]]), csv_bytes(climate), raw)
    hourly[0]["hour_start_utc"] = "2026-09-04T20:00:00Z"
    with pytest.raises(ValueError, match="lineage"):
        incident.analyze(csv_bytes(hourly), csv_bytes(climate), raw)


@pytest.mark.parametrize("local", ["2026-11-01 01:30", "2026-03-08 02:30"])
def test_ambiguous_or_nonexistent_local_times_need_original_utc_lineage(local):
    with pytest.raises(ValueError):
        incident.local_utc(local)


def test_cli_refuses_to_overwrite_evidence(tmp_path):
    hourly, climate, projection = inputs()
    files = []
    for name, data in (
        ("hourly", csv_bytes(hourly)),
        ("climate", csv_bytes(climate)),
        ("events", json.dumps(projection).encode()),
    ):
        path = tmp_path / name
        path.write_bytes(data)
        files.extend(["--" + name, str(path)])
    output = tmp_path / "result.json"
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr("sys.argv", ["wetting_incident", "analyze", *files, "--output", str(output)])
        incident.main()
        original = output.read_bytes()
        with pytest.raises(SystemExit) as failure:
            incident.main()
        assert failure.value.code == 2
        assert output.read_bytes() == original
