from __future__ import annotations

import sys
from pathlib import Path

MODULE_DIR = Path(__file__).parents[1]
sys.path.insert(0, str(MODULE_DIR))
from fixed_panel_coverage import coverage


def export(rows: list[dict]) -> dict:
    return {
        "contract_version": 1,
        "sample_basis": "database_flush_snapshot",
        "greenhouse_id": "vallery",
        "exported_at": "2026-08-02T00:00:00+00:00",
        "window_start": "2026-08-01T00:00:00+00:00",
        "window_end": "2026-08-01T00:15:00+00:00",
        "rows": rows,
    }


def row(minute: int, *, house: str = "vallery", west: float | None = 1.2) -> dict:
    return {
        "ts": f"2026-08-01T00:{minute:02d}:00+00:00",
        "greenhouse_id": house,
        "temp_north": 80.0,
        "temp_east": 80.0,
        "temp_west": 80.0,
        "vpd_north": 1.2,
        "vpd_east": 1.2,
        "vpd_west": west,
    }


def test_conflicting_duplicate_loses_one_joint_minute_but_not_temperature() -> None:
    rows = [row(minute) for minute in range(15)]
    for field in ("temp_north", "temp_east", "temp_west"):
        rows[0][field] = 100.0
    duplicate = row(0)
    for field in ("temp_north", "temp_east", "temp_west"):
        duplicate[field] = 100.0
    duplicate["vpd_north"] = 1.3
    rows.extend((duplicate, row(0, house="other"), row(15)))

    report = coverage(export(rows))
    bin_row = report["bins"][0]
    assert report["excluded_rows"] == {"other_greenhouse": 1, "outside_window": 1}
    assert report["scoped_rows"] == 16
    assert report["distinct_observed_minutes"] == 15
    assert bin_row["complete_minutes"] == {"temp": 15, "vpd": 14, "joint": 14}
    assert bin_row["conflicting_minutes"] == 1
    assert bin_row["panel_mean"] == {"temp": 80.0, "vpd": 1.2}
    assert report["summary"]["joint_eligible_bins_12_of_15"] == 1
    assert report["historical_target_verified"] is False


def test_missing_west_cannot_renormalize_the_panel() -> None:
    rows = [row(minute, west=None if minute < 4 else 1.2) for minute in range(15)]
    report = coverage(export(rows))

    assert report["bins"][0]["complete_minutes"] == {"temp": 15, "vpd": 11, "joint": 11}
    assert report["bins"][0]["missing_minutes_by_field"]["vpd_west"] == 4
    assert report["summary"]["temp_eligible_bins_12_of_15"] == 1
    assert report["summary"]["joint_eligible_bins_12_of_15"] == 0
