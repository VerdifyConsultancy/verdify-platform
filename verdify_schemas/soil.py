"""Soil-moisture targets (#1345, migration 303): one row per probe zone, editable through the operator API
(API-SOIL-01/02) and MCP (``soil_targets``, ``soil_target_set``).

A target is four percentages of volumetric moisture in one order, the database's CHECK
``soil_moisture_targets_order``: ``0 <= wilt < min < max < saturation <= 100``. ``wilt_pct`` is what the ingestor's
``soil_dryout`` rule pages on. No crop name takes part: the old ``crop`` column is a free-text note nothing reads.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, model_validator

PCT_MIN = 0.0
PCT_MAX = 100.0
NOTES_MAX_LENGTH = 500
THRESHOLDS = ("wilt_pct", "min_pct", "max_pct", "saturation_pct")


def order_problem(values: dict[str, float]) -> str | None:
    """Why ``values`` (all four thresholds) break the order, or None."""
    wilt, low, high, saturation = (values[name] for name in THRESHOLDS)
    if not (PCT_MIN <= wilt < low < high < saturation <= PCT_MAX):
        return "targets must keep 0 ≤ wilt < min < max < saturation ≤ 100 (percent)"
    return None


class SoilTargetPatch(BaseModel):
    """The thresholds and note to change; a field left out keeps its stored value. The service checks the order on
    the merged row, so a patch may move one edge as long as the result stays ordered."""

    model_config = ConfigDict(extra="forbid")

    wilt_pct: StrictFloat | None = Field(default=None, ge=PCT_MIN, le=PCT_MAX)
    min_pct: StrictFloat | None = Field(default=None, ge=PCT_MIN, le=PCT_MAX)
    max_pct: StrictFloat | None = Field(default=None, ge=PCT_MIN, le=PCT_MAX)
    saturation_pct: StrictFloat | None = Field(default=None, ge=PCT_MIN, le=PCT_MAX)
    notes: str | None = Field(default=None, max_length=NOTES_MAX_LENGTH)

    @model_validator(mode="after")
    def _something(self) -> SoilTargetPatch:
        if all(getattr(self, name) is None for name in (*THRESHOLDS, "notes")):
            raise ValueError("change at least one threshold or the note")
        return self


class SoilTargetRow(BaseModel):
    """A stored ``soil_moisture_targets`` row as the services read it."""

    model_config = ConfigDict(extra="ignore")

    zone: str
    wilt_pct: float
    min_pct: float
    max_pct: float
    saturation_pct: float
    notes: str | None = None
    probe_column: str | None = None
    sensor_id: str | None = None
    occupancy_zone: str | None = None

    @model_validator(mode="after")
    def _ordered(self) -> SoilTargetRow:
        problem = order_problem(self.model_dump())
        if problem:
            raise ValueError(problem)
        return self
