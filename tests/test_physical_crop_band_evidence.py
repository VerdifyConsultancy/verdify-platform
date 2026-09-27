"""#371: a qualified crop outcome must not inherit controller credit."""

from __future__ import annotations

import importlib.util
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from verdify_schemas.mcp_responses import ScorecardResponse
from verdify_schemas.physical_crop_band import PhysicalCropBandEvidence
from verdify_schemas.physical_crop_band_reader import parse_physical_crop_band_row

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
    physical = parse_physical_crop_band_row(qualified_row(), DAY)
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
    evidence = parse_physical_crop_band_row(row, DAY)
    assert evidence.availability == "unavailable"
    assert evidence.unavailable_reason == "invalid_evidence"
    assert evidence.diagnostic is None


def test_invalid_counts_and_day_are_withheld():
    row = qualified_row()
    row["diagnostic"]["joint"]["in_band_bins"] = 21
    assert parse_physical_crop_band_row(row, DAY).availability == "unavailable"
    row = qualified_row()
    assert parse_physical_crop_band_row(row, DAY + timedelta(days=1)).availability == "unavailable"
    assert "Unavailable" in _publisher().physical_crop_band_block(row, (DAY + timedelta(days=1)).isoformat())


def test_model_rejects_unscoped_physical_percent():
    row = qualified_row()
    row["availability"] = "available"
    row["diagnostic"]["target_manifest_sha256"] = "counterfactual"
    with pytest.raises(ValidationError):
        PhysicalCropBandEvidence.model_validate(row)
