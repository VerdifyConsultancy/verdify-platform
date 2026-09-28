"""Prospective winter target derives from exact per-zone crop grading rules."""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "research/planner-efficacy/freeze_winter_target.py"
sys.path.insert(0, str(SOURCE.parent))
spec = importlib.util.spec_from_file_location("freeze_winter_target", SOURCE)
assert spec and spec.loader
freezer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(freezer)


def source_snapshot() -> dict:
    profiles = []
    for hour in range(24):
        profiles.extend(
            [
                {
                    "id": hour * 2 + 1,
                    "greenhouse_id": "vallery",
                    "season": "spring",
                    "hour_of_day": hour,
                    "crop_type": "_default",
                    "crop_catalog_id": None,
                    "temp_ideal_min": 60.0,
                    "temp_ideal_max": 80.0,
                    "vpd_ideal_min": 0.4,
                    "vpd_ideal_max": 1.4,
                },
                {
                    "id": hour * 2 + 2,
                    "greenhouse_id": "vallery",
                    "season": "spring",
                    "hour_of_day": hour,
                    "crop_type": "pepper",
                    "crop_catalog_id": 6,
                    "temp_ideal_min": 63.0,
                    "temp_ideal_max": 72.0,
                    "vpd_ideal_min": 0.4,
                    "vpd_ideal_max": 0.8,
                },
            ]
        )
    assignments = [
        {"id": 1, "greenhouse_id": "vallery", "zone": "east", "crop_catalog_id": 6, "is_active": True},
        {"id": 2, "greenhouse_id": "vallery", "zone": "west", "crop_catalog_id": 56, "is_active": True},
    ]
    return {
        "schema": freezer.SNAPSHOT_SCHEMA,
        "captured_at": datetime.now(UTC).isoformat(),
        "database_snapshot": "1:2:",
        "greenhouse_id": "vallery",
        "profiles": profiles,
        "assignments": assignments,
        "source_profile_state_sha256": "a" * 64,
        "source_assignment_state_sha256": "b" * 64,
        "profile_revision_ids": list(range(1, len(profiles) + 1)),
        "assignment_revision_ids": [1, 2],
        "profile_row_count": len(profiles),
        "assignment_row_count": len(assignments),
    }


def future_start() -> date:
    return date(datetime.now(UTC).year + 1, 11, 10)


def test_4320_bins_use_east_pepper_and_north_west_defaults():
    draft = freezer.build(
        source_snapshot(), start=future_start(), target_version="winter-test-v1", source_sha256="c" * 64
    )
    assert len(draft["target_bins"]) == 4320
    assert draft["target_bins"][0]["temp_low"] == 61.0
    assert draft["target_bins"][0]["temp_high"] == pytest.approx(77.33333333333333)
    assert draft["target_bins"][0]["vpd_low"] == pytest.approx(0.4)
    assert draft["target_bins"][0]["vpd_high"] == pytest.approx(1.2)
    assert draft["target_bins_sha256"] == freezer._sha(freezer._canonical(draft["target_bins"]))
    sql = freezer.declaration_sql(draft)
    assert "INSERT INTO public.fixed_panel_target_revisions" in sql
    assert "LOCK TABLE public.fixed_panel_target_revisions IN SHARE ROW EXCLUSIVE MODE" in sql
    assert "prospective panel target interval already has a declaration" in sql


def test_missing_default_and_incomplete_lineage_fail_closed():
    snapshot = source_snapshot()
    snapshot["profile_revision_ids"].pop()
    with pytest.raises(ValueError, match="cover every source row"):
        freezer.build(snapshot, start=future_start(), target_version="test", source_sha256="c" * 64)
    snapshot = source_snapshot()
    snapshot["profiles"] = [p for p in snapshot["profiles"] if p["crop_type"] != "_default"]
    snapshot["profile_row_count"] = len(snapshot["profiles"])
    snapshot["profile_revision_ids"] = list(range(1, len(snapshot["profiles"]) + 1))
    with pytest.raises(ValueError, match="missing or ambiguous _default"):
        freezer.build(snapshot, start=future_start(), target_version="test", source_sha256="c" * 64)


def test_final_artifact_requires_exact_declaration_readback():
    draft = freezer.build(
        source_snapshot(), start=future_start(), target_version="winter-test-v1", source_sha256="c" * 64
    )
    row = {
        key: draft[key]
        for key in (
            "target_version",
            "effective_from",
            "effective_to",
            "source_profile_state_sha256",
            "source_assignment_state_sha256",
            "profile_revision_ids",
            "assignment_revision_ids",
            "target_rule",
            "target_bins",
        )
    }
    row.update({"revision_id": 17, "recorded_at": datetime.now(UTC).isoformat(), "db_target_bins_sha256": "d" * 64})
    artifact = freezer.finalize(draft, row)
    assert artifact["fixed_panel_target_revision_id"] == 17
    assert artifact["crop_assignment_revision_sha256"] == "b" * 64
    row["target_bins"] = row["target_bins"][:-1]
    with pytest.raises(ValueError, match="declared target_bins differs"):
        freezer.finalize(draft, row)


def test_declaration_sql_rejects_tampered_draft():
    draft = freezer.build(
        source_snapshot(), start=future_start(), target_version="winter-test-v1", source_sha256="c" * 64
    )
    for key, bad in (
        ("target_version", "x'; DROP TABLE public.crops;--"),
        ("effective_from", "2020-01-01T00:00:00+00:00"),
        ("source_assignment_state_sha256", "bad"),
        ("assignment_revision_ids", [2, 1]),
        ("target_bins_sha256", "d" * 64),
    ):
        changed = {**draft, key: bad}
        with pytest.raises((ValueError, TypeError)):
            freezer.declaration_sql(changed)
