"""Narrow route-only 18-hour receipt; raw native callbacks stay private."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator

from verdify_schemas.physical_crop_band import PhysicalBandAxis, PhysicalBandJoint

ROUTE_DAY_READER_SQL = "SELECT * FROM public.fn_fixed_panel_native_route_day_receipt($1::date, $2::text)"
MEASUREMENT_READER_SQL = "SELECT public.fn_fixed_panel_native_route_measurement($1::date,$2::text) AS evidence"
DENVER = ZoneInfo("America/Denver")


class NativeRouteDiagnostic(BaseModel):
    """Authenticated source-route samples; physical identity remains unverified."""

    model_config = ConfigDict(extra="forbid")
    definition: Literal["fixed-panel-native-route-measurement-v1"]
    window_start: AwareDatetime
    window_end: AwareDatetime
    expected_bins: Literal[72]
    target_version: str
    target_basis: Literal["prospective_frozen_panel_mean_crop_reference"]
    sample_basis: Literal["host_receive_time_after_initial_native_subscription_state"]
    measurement_scope: Literal["source_routes_without_physical_hardware_or_crop_placement_authentication"]
    target_effective_from: AwareDatetime
    target_effective_to: AwareDatetime
    source_profile_state_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_assignment_state_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    temp: PhysicalBandAxis
    vpd: PhysicalBandAxis
    joint: PhysicalBandJoint
    temp_unavailable_reasons: dict[str, int]
    vpd_unavailable_reasons: dict[str, int]
    partial_window: bool
    completed_bin_count: int = Field(ge=0, le=72)
    physical_hardware_identity_verified: Literal[False]
    crop_placement_verified: Literal[False]
    per_probe_poll_time_verified: Literal[False]
    physical_proof_eligible: Literal[False]
    experiment_endpoint_eligible: Literal[False]
    causal_effect_estimate: Literal[False]

    @model_validator(mode="after")
    def validate_coverage(self):
        if (self.window_end - self.window_start).total_seconds() != 18 * 3600:
            raise ValueError("native route window must retain 72 expected 15-minute bins")
        if not self.window_start <= self.target_effective_from < self.target_effective_to <= self.window_end:
            raise ValueError("target scope must remain inside the diagnostic day")
        for axis, reasons in ((self.temp, self.temp_unavailable_reasons), (self.vpd, self.vpd_unavailable_reasons)):
            if axis.eligible_bins > 72 or axis.eligible_bins > self.completed_bin_count:
                raise ValueError("eligible route bins cannot exceed elapsed/expected bins")
            if (
                any(not key or type(n) is not int or n < 0 for key, n in reasons.items())
                or sum(reasons.values()) != 72 - axis.eligible_bins
            ):
                raise ValueError("native route missingness must partition 72 expected bins")
        if self.joint.eligible_bins > min(self.temp.eligible_bins, self.vpd.eligible_bins):
            raise ValueError("joint route coverage exceeds either axis")
        return self


class NativeFixedPanelRouteEvidence(BaseModel):
    """A frozen source-route observation, never physical or causal proof."""

    model_config = ConfigDict(extra="forbid")

    reader_contract_version: Literal[1] = 1
    availability: Literal["route_observation", "route_measurement", "unavailable"] = "unavailable"
    unavailable_reason: str | None = "not_frozen"
    day: date | None = None
    greenhouse_id: str = "vallery"
    served_at: AwareDatetime | None = None
    receipt_id: int | None = Field(default=None, gt=0)
    frozen_at: AwareDatetime | None = None
    projection_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    target_revision_id: int | None = Field(default=None, gt=0)
    contributor_revision_ids: tuple[int, int, int] | None = None
    source_binding_id: int | None = Field(default=None, gt=0)
    panel_source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    firmware_revision: str | None = None
    collector_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    declared_route_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    source_callback_count: int | None = Field(default=None, ge=0)
    source_continuity_verified: bool | None = None
    eligible_bins: int | None = Field(default=None, ge=0, le=72)
    joint_in_band_bins: int | None = Field(default=None, ge=0, le=72)
    diagnostic: NativeRouteDiagnostic | None = None
    route_18h_available: bool = False
    physical_serial_verified: Literal[False] = False
    modbus_poll_time_verified: Literal[False] = False
    physical_proof_eligible: Literal[False] = False
    experiment_endpoint_eligible: Literal[False] = False
    causal_effect_estimate: Literal[False] = False

    @model_validator(mode="after")
    def check_scope(self) -> NativeFixedPanelRouteEvidence:
        if self.joint_in_band_bins is not None and self.eligible_bins is not None:
            if self.joint_in_band_bins > self.eligible_bins:
                raise ValueError("joint count exceeds fresh route coverage")
        if self.availability == "route_measurement":
            if (
                self.unavailable_reason is not None
                or self.diagnostic is None
                or self.day is None
                or self.served_at is None
                or self.source_binding_id is None
                or self.target_revision_id is None
                or self.contributor_revision_ids is None
                or self.firmware_revision is None
                or self.collector_revision is None
                or self.declared_route_sha256 is None
                or self.panel_source_sha256 is None
                or self.route_18h_available
            ):
                raise ValueError("route measurement needs exact prospective lineage and no frozen 18-hour claim")
            if max(self.diagnostic.temp.eligible_bins, self.diagnostic.vpd.eligible_bins) == 0:
                raise ValueError("route measurement requires an eligible source axis")
            start = datetime.combine(self.day, time(6), DENVER).astimezone(UTC)
            end = datetime.combine(self.day + timedelta(days=1), time(), DENVER).astimezone(UTC)
            if self.diagnostic.window_start != start or self.diagnostic.window_end != end:
                raise ValueError("route diagnostic day/window mismatch")
            if (
                self.eligible_bins != self.diagnostic.joint.eligible_bins
                or self.joint_in_band_bins != self.diagnostic.joint.in_band_bins
            ):
                raise ValueError("route joint projection conflicts with diagnostic")
            return self
        if self.availability == "unavailable":
            if self.unavailable_reason is None or self.route_18h_available:
                raise ValueError("unavailable route evidence needs a reason and no outcome")
            if self.diagnostic is not None and max(
                self.diagnostic.temp.eligible_bins, self.diagnostic.vpd.eligible_bins
            ):
                raise ValueError("unavailable route measurement cannot contain eligible outcomes")
            return self
        if (
            self.unavailable_reason is not None
            or not self.route_18h_available
            or self.source_continuity_verified is not True
            or self.eligible_bins != 72
            or self.day is None
            or self.receipt_id is None
            or self.frozen_at is None
            or self.served_at is None
            or self.projection_sha256 is None
            or self.target_revision_id is None
            or self.contributor_revision_ids is None
            or self.source_binding_id is None
            or self.panel_source_sha256 is None
            or self.firmware_revision is None
            or self.collector_revision is None
            or self.declared_route_sha256 is None
            or self.source_callback_count is None
            or self.joint_in_band_bins is None
        ):
            raise ValueError("available route evidence lacks a complete frozen source binding")
        window_end = datetime.combine(self.day + timedelta(days=1), time(), DENVER).astimezone(UTC)
        if self.frozen_at < window_end or self.served_at < self.frozen_at:
            raise ValueError("route receipt predates the completed local window")
        return self


def parse_native_fixed_panel_route_row(row, day: date, greenhouse_id: str = "vallery") -> NativeFixedPanelRouteEvidence:
    """Reject malformed latest receipts without returning private DB payloads."""
    if row is None:
        return NativeFixedPanelRouteEvidence(day=day, greenhouse_id=greenhouse_id)
    try:
        data = dict(row)
        if data["day"] != day or data["greenhouse_id"] != greenhouse_id:
            raise ValueError("native route receipt scope mismatch")
        if data["unavailable_reason"] in ("not_frozen", "invalid_receipt"):
            return NativeFixedPanelRouteEvidence(
                day=day,
                greenhouse_id=greenhouse_id,
                served_at=data["served_at"],
                unavailable_reason=data["unavailable_reason"],
            )
        data["availability"] = "route_observation" if data["unavailable_reason"] is None else "unavailable"
        return NativeFixedPanelRouteEvidence.model_validate(data)
    except (ValidationError, ValueError, TypeError, KeyError, OverflowError):
        return NativeFixedPanelRouteEvidence(day=day, greenhouse_id=greenhouse_id, unavailable_reason="invalid_receipt")


async def read_native_fixed_panel_route_evidence(
    conn, day: date, greenhouse_id: str = "vallery"
) -> NativeFixedPanelRouteEvidence:
    import asyncpg

    if greenhouse_id != "vallery":
        return NativeFixedPanelRouteEvidence(
            day=day, greenhouse_id=greenhouse_id, unavailable_reason="unsupported_greenhouse"
        )
    try:
        async with conn.transaction():
            await conn.execute("SET LOCAL statement_timeout = '3000ms'")
            try:
                async with conn.transaction():
                    row = await conn.fetchrow(MEASUREMENT_READER_SQL, day, greenhouse_id, timeout=3.5)
            except asyncpg.UndefinedFunctionError:
                row = await conn.fetchrow(ROUTE_DAY_READER_SQL, day, greenhouse_id, timeout=3.5)
    except (asyncpg.QueryCanceledError, TimeoutError):
        return NativeFixedPanelRouteEvidence(
            day=day, greenhouse_id=greenhouse_id, unavailable_reason="db_statement_timeout"
        )
    except (
        asyncpg.UndefinedFunctionError,
        asyncpg.UndefinedTableError,
        asyncpg.UndefinedColumnError,
        asyncpg.InsufficientPrivilegeError,
    ):
        return NativeFixedPanelRouteEvidence(
            day=day, greenhouse_id=greenhouse_id, unavailable_reason="reader_unavailable"
        )
    if row is not None and "evidence" in row:
        try:
            data = row["evidence"]
            if isinstance(data, str):
                data = json.loads(data)
            if not isinstance(data, dict):
                raise ValueError("measurement must be a JSON object")
            if data.get("day") != day.isoformat() or data.get("greenhouse_id") != greenhouse_id:
                raise ValueError("measurement scope mismatch")
            return NativeFixedPanelRouteEvidence.model_validate(data)
        except (ValidationError, ValueError, TypeError, KeyError):
            return NativeFixedPanelRouteEvidence(
                day=day, greenhouse_id=greenhouse_id, unavailable_reason="invalid_measurement"
            )
    return parse_native_fixed_panel_route_row(row, day, greenhouse_id)
