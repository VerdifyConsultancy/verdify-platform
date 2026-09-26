"""Wire rejection tests included by the configured platform schema selection."""

import pytest
from pydantic import ValidationError

from verdify_schemas.api import PublicBandTraceSummary, PublicVpdHighLineage


@pytest.mark.parametrize(
    "field", ["fw_temp_compliance_pct", "fw_vpd_compliance_pct", "readback_match_pct", "ok_trace_pct"]
)
def test_unverified_device_metrics_cannot_carry_a_numeric_rate(field):
    values = {"hours": 1, "sample_count": 4}
    values.update(
        {
            f"{basis}_{axis}_eligible_samples": 0
            for basis in ("reconstructed", "desired")
            for axis in ("temp", "vpd", "both")
        }
    )
    values[field] = 0
    with pytest.raises(ValidationError):
        PublicBandTraceSummary.model_validate(values)
    values[field] = None
    result = PublicBandTraceSummary.model_validate(values)
    assert result.reconstructed_both_compliance_pct is None
    assert result.consumed_band_eligible_samples == 0


@pytest.mark.parametrize("unit,slug", [("°F", "cfg___vpd_high__kpa_"), ("kPa", "house_vpd_target")])
def test_vpd_edge_has_one_canonical_unit_and_raw_route(unit, slug):
    with pytest.raises(ValidationError):
        PublicVpdHighLineage(
            unit=unit,
            raw_slug=slug,
            desired_value=None,
            desired_recorded_at=None,
            desired_conflict=False,
            cfg_snapshot_value=None,
            cfg_snapshot_captured_at=None,
            cfg_snapshot_conflict=False,
            numeric_comparison="unavailable",
        )
