"""Setpoint DB row schemas — every shape the setpoint tables take.

- SetpointChange     → setpoint_changes (events, + confirmed_at from mig 084)
- SetpointPlanRow    → setpoint_plan    (per-waypoint row; the active plan lives here)
- SetpointSnapshot   → setpoint_snapshot (cfg_* readback from ESP32)
- SetpointClamp      → setpoint_clamps  (Tier 1 #2 audit when planner values got clamped)
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from .tunable_registry import registry_value_error
from .tunables import TunableParameter

SetpointSource = Literal["plan", "band", "manual", "esp32", "iris"]


class SetpointChange(BaseModel):
    """setpoint_changes hypertable row — every push the dispatcher makes."""

    model_config = ConfigDict(extra="ignore")

    ts: AwareDatetime
    parameter: TunableParameter
    value: float
    source: SetpointSource = "plan"
    confirmed_at: AwareDatetime | None = None
    greenhouse_id: str = "vallery"
    # v1.4 audit columns (migration 093). Populated by MCP set_tunable
    # from X-Planner-Instance + X-Trigger-Id headers; NULL on pre-v1.4 rows.
    planner_instance: str | None = None
    trigger_id: UUID | None = None
    delivery_status: str | None = None
    expired_at: AwareDatetime | None = None
    superseded_by_ts: AwareDatetime | None = None

    @model_validator(mode="after")
    def _validate_registry_bounds(self) -> SetpointChange:
        if error := registry_value_error(self.parameter, self.value):
            raise ValueError(f"Registry bounds violation: {error}")
        return self


class SetpointPlanRow(BaseModel):
    """setpoint_plan hypertable row — one waypoint × parameter × plan_id.

    The planner emits 10-20 of these per plan via set_plan; the dispatcher
    resolves which is active via v_active_plan (DISTINCT ON parameter, newest
    created_at wins). is_active flips to false when a newer plan supersedes.
    """

    model_config = ConfigDict(extra="ignore")

    ts: AwareDatetime
    parameter: TunableParameter
    value: float
    plan_id: str = Field(..., min_length=1)
    source: str = "iris"
    reason: str | None = None
    created_at: AwareDatetime | None = None
    is_active: bool = True
    greenhouse_id: str = "vallery"
    planner_instance: str | None = None
    trigger_id: UUID | None = None

    @model_validator(mode="after")
    def _validate_registry_bounds(self) -> SetpointPlanRow:
        if error := registry_value_error(self.parameter, self.value):
            raise ValueError(f"Registry bounds violation: {error}")
        return self


class SetpointSnapshot(BaseModel):
    """setpoint_snapshot hypertable row — ESP32's cfg_* readback of its
    configured values. Written every 60 s by the ingestor. Used by FW-4 to
    close the confirmation loop (value-match check vs setpoint_changes)."""

    model_config = ConfigDict(extra="ignore")

    ts: AwareDatetime
    parameter: TunableParameter
    value: float
    greenhouse_id: str = "vallery"


class SetpointClamp(BaseModel):
    """setpoint_clamps hypertable row — Tier 1 #2 audit of planner values
    that were clamped down (FW-3 physics invariants, band edges, etc.).

    `requested` is what Iris emitted; `applied` is what the dispatcher pushed.
    `band_lo` / `band_hi` are the clamp bounds when they came from crop-band
    computation; null for pure physics-invariant rejections. `reason` is a
    short tag — e.g., 'band_lo', 'band_hi', 'invariant_violation'.
    """

    model_config = ConfigDict(extra="ignore")

    ts: AwareDatetime | None = None
    parameter: TunableParameter
    requested: float
    applied: float
    band_lo: float | None = None
    band_hi: float | None = None
    reason: str = Field(..., min_length=1)
    greenhouse_id: str = "vallery"
