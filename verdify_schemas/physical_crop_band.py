"""Qualified crop-band sample bins, separate from controller credit.

Stored manifest hashes identify evidence inputs; this validator checks internal
consistency, not the historical authenticity of a claimed target or probe.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

LOCAL_TZ = ZoneInfo("America/Denver")
Count = Annotated[int, Field(strict=True, ge=0, le=100)]
Percent = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
Distance = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Zone = Literal["north", "east", "west"]


def _close(actual: float | None, expected: float | None) -> bool:
    if actual is None or expected is None:
        return actual is expected
    return math.isclose(actual, expected, rel_tol=0, abs_tol=0.001)


class PhysicalBandAxis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eligible_bins: Count
    in_band_bins: Count
    in_band_pct: Percent | None
    high_miss_bins: Count
    low_miss_bins: Count
    mean_high_distance: Distance | None
    mean_low_distance: Distance | None
    mean_outside_distance: Distance | None
    worst_measured_zone: Zone | None

    @model_validator(mode="after")
    def check_counts_and_distances(self):
        n, inside = self.eligible_bins, self.in_band_bins
        outside = n - inside
        high, low = self.high_miss_bins, self.low_miss_bins
        if inside > n or max(high, low) > outside or high + low < outside:
            raise ValueError("physical axis bin counts conflict")
        if not _close(self.in_band_pct, 100 * inside / n if n else None):
            raise ValueError("physical axis percentage conflicts with denominator")
        if n == 0:
            if any(
                x is not None for x in (self.mean_high_distance, self.mean_low_distance, self.mean_outside_distance)
            ):
                raise ValueError("empty physical axis distances must be unavailable")
        elif any(x is None for x in (self.mean_high_distance, self.mean_low_distance, self.mean_outside_distance)):
            raise ValueError("eligible physical axis requires distance values")
        else:
            if not _close(self.mean_outside_distance, self.mean_high_distance + self.mean_low_distance):
                raise ValueError("physical high/low distance does not sum to outside distance")
            if (high == 0 and self.mean_high_distance != 0) or (low == 0 and self.mean_low_distance != 0):
                raise ValueError("nonzero distance without a measured miss")
        if (outside > 0) != (self.worst_measured_zone is not None):
            raise ValueError("worst measured zone must track observed misses")
        return self


class PhysicalBandJoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eligible_bins: Count
    in_band_bins: Count
    in_band_pct: Percent | None

    @model_validator(mode="after")
    def check_fraction(self):
        if self.in_band_bins > self.eligible_bins or not _close(
            self.in_band_pct,
            100 * self.in_band_bins / self.eligible_bins if self.eligible_bins else None,
        ):
            raise ValueError("physical joint bin counts conflict")
        return self


class PhysicalCropBandDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid")

    definition: Literal["fixed-panel-crop-band-v1"]
    greenhouse_id: Literal["vallery"]
    day: date
    window_start: AwareDatetime
    window_end: AwareDatetime
    expected_bins: Count
    target_version: str = Field(min_length=1)
    target_manifest_sha256: Sha256
    panel_version: str = Field(min_length=1)
    panel_manifest_sha256: Sha256
    input_sha256: Sha256
    calculation_source_sha256: Sha256
    target_basis: Literal["immutable_crop_targets_as_of_bin"]
    sample_basis: Literal["fixed_panel_fresh_probe_observations"]
    duration_basis: Literal["qualified_15_minute_bins_not_continuous_exposure"]
    panel_members: tuple[Literal["north"], Literal["east"], Literal["west"]]
    fixed_sensor_panel: Literal[True]
    historical_crop_target_verified: Literal[True]
    per_probe_freshness_verified: Literal[True]
    center_probe_measured: Literal[False]
    physical_proof_eligible: Literal[True]
    experiment_endpoint_eligible: Literal[False]
    dli_available: Literal[False]
    gas_available: Literal[False]
    resource_cost_available: Literal[False]
    temp: PhysicalBandAxis
    vpd: PhysicalBandAxis
    joint: PhysicalBandJoint

    @model_validator(mode="after")
    def check_window_and_coverage(self):
        start = datetime.combine(self.day, time(), LOCAL_TZ).astimezone(UTC)
        end = datetime.combine(self.day + timedelta(days=1), time(), LOCAL_TZ).astimezone(UTC)
        if (
            self.window_start != start
            or self.window_end != end
            or self.expected_bins != int((end - start).total_seconds() / 900)
        ):
            raise ValueError("physical window must be the complete Denver local day")
        if (
            self.temp.eligible_bins > self.expected_bins
            or self.vpd.eligible_bins > self.expected_bins
            or self.joint.eligible_bins > min(self.temp.eligible_bins, self.vpd.eligible_bins)
            or self.joint.in_band_bins > min(self.temp.in_band_bins, self.vpd.in_band_bins)
        ):
            raise ValueError("physical axis/joint coverage conflicts")
        return self


class PhysicalCropBandEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reader_contract_version: Literal[1] = 1
    availability: Literal["available", "unavailable"] = "unavailable"
    unavailable_reason: str | None = "not_requested"
    day: date | None = None
    greenhouse_id: str = "vallery"
    served_at: AwareDatetime | None = None
    revision_id: Annotated[int, Field(strict=True, gt=0)] | None = None
    recorded_at: AwareDatetime | None = None
    diagnostic: PhysicalCropBandDiagnostic | None = None

    @model_validator(mode="after")
    def check_evidence(self):
        if self.availability == "unavailable":
            if self.unavailable_reason is None or self.diagnostic is not None:
                raise ValueError("unavailable physical evidence must not publish a diagnostic")
            return self
        if (
            self.unavailable_reason is not None
            or any(x is None for x in (self.day, self.served_at, self.revision_id, self.recorded_at, self.diagnostic))
            or self.greenhouse_id != self.diagnostic.greenhouse_id
            or self.day != self.diagnostic.day
            or self.diagnostic.window_end > self.recorded_at
            or self.recorded_at > self.served_at
        ):
            raise ValueError("available physical evidence needs a scoped completed revision")
        return self
