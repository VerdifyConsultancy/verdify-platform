"""Typed captured diagnostics, not scientific outcome or live-proof evidence.

Validation checks internal consistency, not raw-input recomputation, provenance
authentication or freshness. Never echo an invalid payload or validation inputs.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

Count = Annotated[int, Field(strict=True, ge=0, le=1560)]
Nonnegative = Annotated[float, Field(strict=True, ge=0, allow_inf_nan=False)]
Fraction = Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]
Percent = Annotated[float, Field(strict=True, ge=0, le=100, allow_inf_nan=False)]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
LOCAL_TZ = ZoneInfo("America/Denver")


class DiagnosticModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RejectedMinutes(DiagnosticModel):
    conflicting_timestamp: Count
    missing_value: Count
    missing_or_invalid_bounds: Count


class JointMinutes(DiagnosticModel):
    eligible_minutes: Count
    in_band_minutes: Count
    in_band_pct: Percent | None
    coverage_fraction: Fraction | None
    longest_ineligible_run_minutes: Count

    @model_validator(mode="after")
    def validate_fraction(self):
        n, k = self.eligible_minutes, self.in_band_minutes
        if k > n or not _equal(self.in_band_pct, 100 * k / n if n else None):
            raise ValueError("inconsistent eligible/in-band counts or fraction")
        return self


class AxisMinutes(JointMinutes):
    unit: Literal["degF", "kPa"]
    low_miss_observed_minutes: Count
    high_miss_observed_minutes: Count
    mean_low_distance: Nonnegative | None
    mean_high_distance: Nonnegative | None
    mean_outside_distance: Nonnegative | None
    ineligible_minutes: RejectedMinutes

    @model_validator(mode="after")
    def validate_distance(self):
        low, high = self.low_miss_observed_minutes, self.high_miss_observed_minutes
        if self.in_band_minutes + low + high != self.eligible_minutes:
            raise ValueError("inconsistent miss counts")
        for count, mean in ((low, self.mean_low_distance), (high, self.mean_high_distance)):
            if not self.eligible_minutes:
                if mean is not None:
                    raise ValueError("empty denominator must remain null")
            elif mean is None or (mean > 0) != (count > 0):
                raise ValueError("inconsistent distance and miss count")
        expected = self.mean_low_distance + self.mean_high_distance if self.eligible_minutes else None
        if not _equal(self.mean_outside_distance, expected):
            raise ValueError("inconsistent outside distance")
        return self


class MinuteAxes(DiagnosticModel):
    temp: AxisMinutes
    vpd: AxisMinutes


def _equal(actual, expected):
    if actual is None or expected is None:
        return actual is expected
    return math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-12)


class ObservedMinuteDiagnostic(DiagnosticModel):
    definition: Literal["house-average-observed-minute-v1"]
    calculation_source_sha256: Sha256
    input_sha256: Sha256
    greenhouse_id: Literal["vallery"]
    window_start: AwareDatetime
    window_end: AwareDatetime
    input_rows: Annotated[int, Field(strict=True, ge=0)]
    ignored_rows: Annotated[int, Field(strict=True, ge=0)]
    expected_minutes: Count
    observed_minutes: Count
    axes: MinuteAxes
    joint: JointMinutes
    target_basis: Literal["latest_setpoint_log_event_as_of_minute_start_provenance_unqualified_not_frozen_crop_targets"]
    sample_basis: Literal["mean_of_finite_unique_timestamp_house_averages_per_UTC_minute"]
    duration_basis: Literal["observed_minute_slots_not_continuous_physical_exposure"]
    fixed_sensor_panel: Literal[False]
    duration_weighted: Literal[False]
    physical_proof_eligible: Literal[False]
    crop_outcome_eligible: Literal[False]
    experiment_endpoint_eligible: Literal[False]
    worst_measured_zone: None

    @field_validator(
        "fixed_sensor_panel",
        "duration_weighted",
        "physical_proof_eligible",
        "crop_outcome_eligible",
        "experiment_endpoint_eligible",
        mode="before",
    )
    @classmethod
    def only_false(cls, value):
        if value is not False:
            raise ValueError("diagnostic eligibility must be the boolean false")
        return value

    @model_validator(mode="after")
    def validate_window_counts(self):
        start, end = self.window_start.astimezone(UTC), self.window_end.astimezone(UTC)
        if any(t.second or t.microsecond for t in (start, end)):
            raise ValueError("window must align to UTC minutes")
        if (end - start).total_seconds() != self.expected_minutes * 60:
            raise ValueError("window/count mismatch")
        n = self.expected_minutes
        if not self.observed_minutes <= min(n, self.input_rows):
            raise ValueError("observed slots exceed window or input rows")
        temp, vpd = self.axes.temp, self.axes.vpd
        if temp.unit != "degF" or vpd.unit != "kPa":
            raise ValueError("axis/unit mismatch")
        for axis in (temp, vpd, self.joint):
            k, gap = axis.eligible_minutes, axis.longest_ineligible_run_minutes
            if k > self.observed_minutes or not _equal(axis.coverage_fraction, k / n if n else None):
                raise ValueError("coverage/count mismatch")
            missing = n - k
            if not math.ceil(missing / (k + 1)) <= gap <= missing:
                raise ValueError("impossible longest gap")
        for axis in (temp, vpd):
            if sum(axis.ineligible_minutes.model_dump().values()) + axis.eligible_minutes != n:
                raise ValueError("missingness/count mismatch")
        joint = self.joint
        if (
            not max(0, temp.eligible_minutes + vpd.eligible_minutes - self.observed_minutes)
            <= joint.eligible_minutes
            <= min(temp.eligible_minutes, vpd.eligible_minutes)
        ):
            raise ValueError("impossible joint eligibility")
        if (
            not max(0, temp.in_band_minutes + vpd.in_band_minutes - self.observed_minutes)
            <= joint.in_band_minutes
            <= min(temp.in_band_minutes, vpd.in_band_minutes)
        ):
            raise ValueError("impossible joint in-band count")
        return self


UnavailableReason = Literal[
    "not_requested",
    "reader_unavailable",
    "db_statement_timeout",
    "unsupported_greenhouse",
    "daily_row_missing",
    "not_computed",
    "revision_mismatch",
    "invalid_diagnostic",
]


class ObservedMinuteEvidence(DiagnosticModel):
    reader_contract_version: Literal[1] = 1
    availability: Literal["available", "unavailable"] = "unavailable"
    unavailable_reason: UnavailableReason | None = "not_requested"
    currentness: Literal["captured_snapshot_not_live_freshness_not_assessed"] = (
        "captured_snapshot_not_live_freshness_not_assessed"
    )
    day: date | None = None
    greenhouse_id: str = "vallery"
    served_at: AwareDatetime | None = None
    revision_id: Annotated[int, Field(strict=True, gt=0)] | None = None
    recorded_at: AwareDatetime | None = None
    capture_schema: Literal["daily-summary-capture-v1", "daily-summary-capture-v2"] | None = None
    diagnostic: ObservedMinuteDiagnostic | None = None

    @model_validator(mode="after")
    def validate_snapshot(self):
        if self.availability == "unavailable":
            if self.unavailable_reason is None or self.diagnostic is not None:
                raise ValueError("unavailable evidence cannot expose a diagnostic")
            return self
        if (
            self.unavailable_reason is not None
            or any(
                v is None
                for v in (
                    self.day,
                    self.served_at,
                    self.revision_id,
                    self.recorded_at,
                    self.diagnostic,
                )
            )
            or self.capture_schema != "daily-summary-capture-v2"
        ):
            raise ValueError("available evidence requires its captured revision")
        d = self.diagnostic
        start = datetime.combine(self.day, time(), LOCAL_TZ).astimezone(UTC)
        try:
            day_end = datetime.combine(self.day + timedelta(days=1), time(), LOCAL_TZ).astimezone(UTC)
        except OverflowError as exc:
            raise ValueError("snapshot day has no representable end") from exc
        if self.greenhouse_id != d.greenhouse_id or d.window_start != start:
            raise ValueError("snapshot day/greenhouse mismatch")
        if not d.window_end <= min(day_end, self.served_at, self.recorded_at) or self.recorded_at > self.served_at:
            raise ValueError("snapshot window/revision is in the future")
        return self
