"""One-day frozen target cannot inherit the winter calendar or backdate."""

from __future__ import annotations

import importlib.util
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "research/planner-efficacy/freeze_route_day_target.py"
SPEC = importlib.util.spec_from_file_location("freeze_route_day_target", SOURCE)
assert SPEC and SPEC.loader
target = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(target)


def snapshot():
    profiles = [
        {
            "id": hour - 5,
            "greenhouse_id": "vallery",
            "season": "spring",  # global resolver fallback when no fall catalog profile exists
            "hour_of_day": hour,
            "crop_catalog_id": None,
            "crop_type": "_default",
            "temp_ideal_min": 60,
            "temp_ideal_max": 80,
            "vpd_ideal_min": 0.4,
            "vpd_ideal_max": 1.4,
        }
        for hour in range(6, 24)
    ]
    return {
        "schema": "verdify-route-day-target-source-snapshot-v1",
        "greenhouse_id": "vallery",
        "captured_at": "2026-09-28T14:00:00+00:00",
        "profiles": profiles,
        "assignments": [{"id": 1, "greenhouse_id": "vallery", "zone": "north", "is_active": False}],
        "profile_revision_ids": list(range(1, 19)),
        "assignment_revision_ids": [1],
        "source_profile_state_sha256": "a" * 64,
        "source_assignment_state_sha256": "b" * 64,
    }


def test_exactly_one_future_local_day_and_frozen_source_sql():
    draft = target.build(snapshot(), date(2026, 9, 29), datetime(2026, 9, 28, 15, tzinfo=UTC))
    assert len(draft["target_bins"]) == 72
    assert draft["effective_from"] == "2026-09-29T12:00:00+00:00"
    assert draft["effective_to"] == "2026-09-30T06:00:00+00:00"
    assert draft["target_bins"][0]["temp_low"] == 60
    assert draft["target_bins"][-1]["vpd_high"] == pytest.approx(1.4)
    sql = target.declaration_sql(draft)
    assert "native-route-2026-09-29" in sql
    assert "route day target interval already declared" in sql
    assert "60-day" not in sql and "winter" not in sql


def test_started_window_refuses_prospective_target():
    with pytest.raises(ValueError, match="future window"):
        target.build(snapshot(), date(2026, 9, 29), datetime(2026, 9, 29, 12, tzinfo=UTC))


def test_remaining_today_is_strictly_future_and_preserves_missing_past():
    start = datetime(2026, 9, 29, 20, 15, tzinfo=UTC)
    draft = target.build(snapshot(), date(2026, 9, 29), datetime(2026, 9, 29, 20, tzinfo=UTC), effective_from=start)
    assert len(draft["target_bins"]) == 39
    assert draft["target_bins"][0]["bucket_start"] == start.isoformat()
    assert draft["effective_to"] == "2026-09-30T06:00:00+00:00"
    assert "2015Z" in draft["target_version"]
    assert "route day target interval already declared" in target.declaration_sql(draft)
    with pytest.raises(ValueError, match="future window"):
        target.build(snapshot(), date(2026, 9, 29), start, effective_from=start)


@pytest.mark.parametrize(
    "start",
    [datetime(2026, 9, 29, 20, 16, tzinfo=UTC), datetime(2026, 9, 30, 6, tzinfo=UTC), datetime(2026, 9, 29, 20)],
)
def test_partial_target_rejects_unaligned_outside_or_naive_start(start):
    with pytest.raises(ValueError):
        target.build(snapshot(), date(2026, 9, 29), datetime(2026, 9, 29, 20, tzinfo=UTC), effective_from=start)
