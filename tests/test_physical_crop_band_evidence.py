"""#371: a qualified crop outcome must not inherit controller credit."""

from __future__ import annotations

import importlib.util
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from verdify_schemas.mcp_responses import ScorecardResponse
from verdify_schemas.physical_crop_band import PhysicalCropBandEvidence, unpublished_physical_crop_band_evidence

ROOT = Path(__file__).resolve().parents[1]
DAY = date(2026, 9, 25)


def _axis(in_band: int, high: int, low: int, zone: str) -> dict:
    return {
        "eligible_bins": 96,
        "in_band_bins": in_band,
        "in_band_pct": 100 * in_band / 96,
        "high_miss_bins": high,
        "low_miss_bins": low,
        "mean_high_distance": 0.2,
        "mean_low_distance": 0.1,
        "mean_outside_distance": 0.3,
        "worst_measured_zone": zone,
    }


def qualified_row() -> dict:
    start = datetime.combine(DAY, time(), ZoneInfo("America/Denver")).astimezone(UTC)
    end = datetime.combine(DAY + timedelta(days=1), time(), ZoneInfo("America/Denver")).astimezone(UTC)
    return {
        "availability": "available",
        "day": DAY,
        "greenhouse_id": "vallery",
        "served_at": end + timedelta(seconds=2),
        "revision_id": 7,
        "recorded_at": end + timedelta(seconds=1),
        "unavailable_reason": None,
        "diagnostic": {
            "definition": "fixed-panel-crop-band-v1",
            "greenhouse_id": "vallery",
            "day": DAY.isoformat(),
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "expected_bins": 96,
            "target_version": "crop-targets-2026-09-25",
            "target_manifest_sha256": "a" * 64,
            "panel_version": "n-e-w-2026-09",
            "panel_manifest_sha256": "b" * 64,
            "input_sha256": "c" * 64,
            "calculation_source_sha256": "d" * 64,
            "target_basis": "immutable_crop_targets_as_of_bin",
            "sample_basis": "fixed_panel_fresh_probe_observations",
            "duration_basis": "qualified_15_minute_bins_not_continuous_exposure",
            "panel_members": ["north", "east", "west"],
            "fixed_sensor_panel": True,
            "historical_crop_target_verified": True,
            "per_probe_freshness_verified": True,
            "center_probe_measured": False,
            "physical_proof_eligible": True,
            "experiment_endpoint_eligible": False,
            "dli_available": False,
            "gas_available": False,
            "resource_cost_available": False,
            "temp": _axis(20, 40, 36, "north"),
            "vpd": _axis(10, 50, 36, "west"),
            "joint": {"eligible_bins": 96, "in_band_bins": 2, "in_band_pct": 100 * 2 / 96},
        },
    }


def _publisher():
    spec = importlib.util.spec_from_file_location("physical_publisher", ROOT / "scripts/update-evidence-snapshots.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_physical_crop_compliance_stays_separate_from_controller_credit_and_house_average():
    physical = PhysicalCropBandEvidence.model_validate(qualified_row())
    assert physical.availability == "available"
    card = ScorecardResponse.from_metric_rows(
        [
            ("scorecard_contract_version", 2),
            ("compliance_pct", 6.1),
            ("compliance_v2_attributable_pct", 85.8),
        ]
    )
    card.physical_crop_band_evidence = physical
    evidence = card.climate_evidence()
    assert evidence["both_axis_compliance_pct"] == 6.1
    assert evidence["graded_compliance_attributable_pct"] == 85.8
    assert evidence["physical_crop_band_evidence"]["diagnostic"]["joint"]["in_band_pct"] == pytest.approx(100 * 2 / 96)
    rendered = _publisher().physical_crop_band_block(evidence["physical_crop_band_evidence"], DAY.isoformat())
    assert "2.1% joint" in rendered
    assert "2/96" in rendered
    assert "85.8%" not in rendered
    assert "continuous exposure" in rendered
    assert "temperature 20/96 and VPD 10/96" in rendered
    assert "Temperature: 40 high and 36 low miss bins" in rendered
    assert "VPD: 50 high and 36 low miss bins" in rendered
    assert "worst measured zone north" in rendered
    assert "worst measured zone west" in rendered
    assert "crop-targets-2026-09-25" in rendered
    assert "definition fixed-panel-crop-band-v1" in rendered


def test_joint_pass_can_exceed_separate_axis_pass_after_shared_slot_recalculation():
    # In fixed_panel.py, the two axis means use their own eligible slots, while
    # the joint means are recalculated on the six-field intersection. An axis
    # can miss separately and pass jointly without violating the measurement.
    row = qualified_row()
    row["diagnostic"]["temp"] = _axis(0, 50, 46, "north")
    physical = PhysicalCropBandEvidence.model_validate(row)
    assert physical.diagnostic.temp.in_band_bins == 0
    assert physical.diagnostic.joint.in_band_bins == 2
    rendered = _publisher().physical_crop_band_block(physical.model_dump(mode="json"), DAY.isoformat())
    assert "2/96 joint in band" in rendered
    assert "temperature 0/96" in rendered


def test_worst_zone_can_miss_when_panel_mean_is_in_band():
    row = qualified_row()
    row["diagnostic"]["temp"] = {
        **_axis(96, 0, 0, "north"),
        "in_band_pct": 100,
        "mean_high_distance": 0,
        "mean_low_distance": 0,
        "mean_outside_distance": 0,
    }
    physical = PhysicalCropBandEvidence.model_validate(row)
    assert physical.diagnostic.temp.in_band_pct == 100
    assert physical.diagnostic.temp.worst_measured_zone == "north"


@pytest.mark.parametrize(
    ("high", "low", "mean_high"),
    [(41, 36, 0.2), (40, 35, 0.2), (40, 36, 0)],
)
def test_axis_rejects_inconsistent_panel_miss_partition_and_distance(high, low, mean_high):
    row = qualified_row()
    row["diagnostic"]["temp"].update(high_miss_bins=high, low_miss_bins=low, mean_high_distance=mean_high)
    with pytest.raises(ValidationError):
        PhysicalCropBandEvidence.model_validate(row)


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("historical_crop_target_verified", False),
        ("per_probe_freshness_verified", False),
        ("physical_proof_eligible", False),
        ("experiment_endpoint_eligible", True),
        ("center_probe_measured", True),
        ("panel_members", ["north", "east", "center"]),
        ("expected_bins", 95),
    ],
)
def test_unqualified_claim_is_withheld(field, invalid):
    row = qualified_row()
    row["diagnostic"][field] = invalid
    with pytest.raises(ValidationError):
        PhysicalCropBandEvidence.model_validate(row)
    assert "Unavailable" in _publisher().physical_crop_band_block(row, DAY.isoformat())


def test_invalid_counts_and_day_are_withheld():
    row = qualified_row()
    row["diagnostic"]["joint"]["in_band_bins"] = 21
    with pytest.raises(ValidationError):
        PhysicalCropBandEvidence.model_validate(row)
    row = qualified_row()
    assert "Unavailable" in _publisher().physical_crop_band_block(row, (DAY + timedelta(days=1)).isoformat())


def test_model_rejects_unscoped_physical_percent():
    row = qualified_row()
    row["diagnostic"]["target_manifest_sha256"] = "counterfactual"
    with pytest.raises(ValidationError):
        PhysicalCropBandEvidence.model_validate(row)


def test_production_publication_is_explicitly_unqualified_with_no_database_reader():
    held = unpublished_physical_crop_band_evidence(DAY)
    assert held.availability == "unavailable"
    assert held.unavailable_reason == "publication_not_qualified"
    assert held.diagnostic is None
    for path in ("api/main.py", "mcp/server.py"):
        source = (ROOT / path).read_text()
        assert "read_physical_crop_band_evidence" not in source
        assert "unpublished_physical_crop_band_evidence" in source
