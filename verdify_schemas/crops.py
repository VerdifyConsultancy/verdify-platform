"""Crops + observations + events schemas.

Shared by:
- api/main.py: Pydantic request bodies for /crops + /observations endpoints
  (was 4 local models at lines 55-102; now re-exported from here).
- MCP crops tool (server.py: @mcp.tool() crops): wraps its free-form
  `(action, data: str)` contract in CropAction / ObservationAction envelopes.
- Website renderers: Crop / Observation as read-only row shapes.
"""

from __future__ import annotations

from datetime import date as DateType
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from .catalog import CropCatalogId
from .operations import HarvestCreate, TreatmentCreate
from .topology import PositionId, ZoneId

CropStage = Literal["seed", "germination", "seedling", "vegetative", "flowering", "fruiting", "harvest", "cleared"]
CropEventType = Literal[
    "planted",
    "stage_change",
    "transplanted",
    "moved",
    "removed",
    "thinned",
    "harvested",
    "reconciled",
    "disease",
    "pest",
    "treatment",
    "note",
]
ObservationType = Literal[
    "health_check",
    "pest",
    "disease",
    "deficiency",
    "photo",
    "measurement",
    "note",
]


# ── Create/Update input bodies (api/main.py + MCP crops tool) ─────────────


class CropCreate(BaseModel):
    """POST /api/v1/crops body + MCP crops.create data payload.

    Sprint 22 adds optional FK references (position_id, zone_id, crop_catalog_id)
    alongside the legacy string fields (position, zone, name/variety).
    During Phases 1-3 both are accepted; Phase 4 switches callers; Phase 6
    drops the strings.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(..., min_length=1, max_length=200)
    variety: str | None = Field(default=None, max_length=200)
    position: str = Field(..., min_length=1, max_length=100)
    zone: str = Field(..., min_length=1, max_length=100)
    planted_date: DateType
    expected_harvest: DateType | None = None
    stage: CropStage = "seed"
    count: int | None = Field(default=None, ge=0)
    seed_lot_id: str | None = Field(default=None, max_length=100)
    supplier: str | None = Field(default=None, max_length=200)
    base_temp_f: float = 50.0
    target_dli: float | None = Field(default=None, ge=0)
    target_vpd_low: float | None = Field(default=None, ge=0, le=20)
    target_vpd_high: float | None = Field(default=None, ge=0, le=20)
    notes: str | None = None

    # Sprint 22: typed FK references (optional during migration)
    position_id: int | None = None
    zone_id: int | None = None
    crop_catalog_id: int | None = None
    position_label: PositionId | None = None
    zone_slug: ZoneId | None = None
    crop_catalog_slug: CropCatalogId | None = None


class CropUpdate(BaseModel):
    """PUT /api/v1/crops/{id} body — every field optional (patch semantics)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=200)
    variety: str | None = Field(default=None, max_length=200)
    position: str | None = Field(default=None, min_length=1, max_length=100)
    zone: str | None = Field(default=None, min_length=1, max_length=100)
    stage: CropStage | None = None
    expected_harvest: DateType | None = None
    count: int | None = Field(default=None, ge=0)
    target_dli: float | None = Field(default=None, ge=0)
    target_vpd_low: float | None = Field(default=None, ge=0, le=20)
    target_vpd_high: float | None = Field(default=None, ge=0, le=20)
    notes: str | None = None

    # Sprint 22: typed FK references (optional during migration)
    position_id: int | None = None
    zone_id: int | None = None
    crop_catalog_id: int | None = None


class ObservationCreate(BaseModel):
    """POST /api/v1/crops/{id}/observations body."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    obs_type: ObservationType = "health_check"
    notes: str | None = None
    severity: int | None = Field(default=None, ge=0, le=10)
    observer: str | None = Field(default=None, max_length=100)
    health_score: float | None = Field(default=None, ge=0, le=1)
    zone: str | None = Field(default=None, max_length=100)
    position: str | None = Field(default=None, max_length=100)
    species: str | None = Field(default=None, max_length=200)
    count: int | None = Field(default=None, ge=0)
    affected_pct: float | None = Field(default=None, ge=0, le=100)
    photo_path: str | None = None
    plant_height_cm: float | None = Field(default=None, ge=0)
    leaf_count: int | None = Field(default=None, ge=0)
    canopy_cover_pct: float | None = Field(default=None, ge=0, le=100)
    flowering_count: int | None = Field(default=None, ge=0)
    fruit_count: int | None = Field(default=None, ge=0)
    root_condition: str | None = Field(default=None, max_length=200)
    mortality_count: int | None = Field(default=None, ge=0)
    stress_tags: list[str] | None = None


class EventCreate(BaseModel):
    """POST /api/v1/crops/{id}/events body."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    event_type: CropEventType
    old_stage: CropStage | None = None
    new_stage: CropStage | None = None
    count: int | None = Field(default=None, ge=0)
    operator: str | None = Field(default=None, max_length=100)
    notes: str | None = None


# ── Plantings on the location tree (migration 293, #1327) ─────────────────
#
# A planting is one crops row (a batch); crop_occupancy says where it is over time: a zone, a shelf in that zone,
# or a cell on that shelf. ``positions`` is what verdify_services.crops.resolve_positions reads: labels
# (EAST-HYDRO-12), label ranges (EAST-HYDRO-1..10), hydro numbers (hydro 1-30), rails (rail A top), shelf cells
# (west top 1-3), a whole shelf (south floor, hanging) or a zone (center). The operator API (API-CROP-07 to -11) and
# the MCP planting tools send these bodies.

PlantingEndReason = Literal["harvested", "failed", "removed", "moved_out", "reconciled"]
# The reasons a person or agent ends a planting with; harvested comes from a final harvest, and moved_out from a
# replace that takes the planting's last cell.
PlantingRemoveReason = Literal["failed", "removed", "reconciled"]
PLANTING_TEXT_MAX = 500
PLANTING_REF_MAX = 40


def _positions(value: object) -> object:
    """One string or a list of strings; each is one resolver term (a list may also be split on commas)."""
    if isinstance(value, str):
        return [value]
    return value


class _PlantingBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    reason: str | None = Field(default=None, max_length=PLANTING_TEXT_MAX)


class PlantingCreate(_PlantingBody):
    """A new planting (API-CROP-07, MCP ``record_planting``). ``crop`` is a catalog slug or name (lettuce, basil)
    or a free name; ``count`` is the number of plants, unknown when omitted; ``count_per_cell`` is stored on each
    cell's occupancy. ``planted_at`` defaults to now; a date means local midnight. ``replace`` ends or trims whatever
    holds a requested cell instead of refusing with ``position_occupied``."""

    crop: str = Field(min_length=1, max_length=200)
    variety: str | None = Field(default=None, max_length=200)
    positions: list[str] = Field(min_length=1, max_length=PLANTING_REF_MAX)
    count: int | None = Field(default=None, ge=0, le=100000)
    count_per_cell: int | None = Field(default=None, ge=0, le=1000)
    planted_at: AwareDatetime | DateType | None = None
    stage: CropStage | None = None
    notes: str | None = Field(default=None, max_length=2000)
    replace: bool = False

    _split = field_validator("positions", mode="before")(_positions)


class PlantingMove(_PlantingBody):
    """Move a planting (API-CROP-08, MCP ``move_planting``): ``from_positions`` (default: everywhere it is now)
    close at ``moved_at`` and ``to_positions`` open then. Both ranges stay in history."""

    to_positions: list[str] = Field(min_length=1, max_length=PLANTING_REF_MAX)
    from_positions: list[str] | None = Field(default=None, min_length=1, max_length=PLANTING_REF_MAX)
    moved_at: AwareDatetime | DateType | None = None
    replace: bool = False

    _split_to = field_validator("to_positions", mode="before")(_positions)
    _split_from = field_validator("from_positions", mode="before")(_positions)


class PlantingHarvest(_PlantingBody):
    """Record a harvest (API-CROP-09, MCP ``harvest_planting``). Partial by default: the planting stays. Cells in
    ``positions`` are cleared; ``final`` ends the planting as harvested."""

    count: int | None = Field(default=None, ge=0, le=100000)
    weight_kg: float | None = Field(default=None, ge=0, le=10000, allow_inf_nan=False)
    positions: list[str] | None = Field(default=None, min_length=1, max_length=PLANTING_REF_MAX)
    final: bool = False
    harvested_at: AwareDatetime | DateType | None = None
    notes: str | None = Field(default=None, max_length=2000)

    _split = field_validator("positions", mode="before")(_positions)


class PlantingEnd(_PlantingBody):
    """End a planting without a harvest (API-CROP-10, MCP ``remove_planting``): every open place closes at ``at``."""

    end_reason: PlantingRemoveReason
    at: AwareDatetime | DateType | None = None
    note: str | None = Field(default=None, max_length=2000)


class PlantingPatch(_PlantingBody):
    """Edit a planting's record (API-CROP-11): only the fields sent change. ``count`` null means unknown."""

    variety: str | None = Field(default=None, max_length=200)
    count: int | None = Field(default=None, ge=0, le=100000)
    stage: CropStage | None = None
    notes: str | None = Field(default=None, max_length=2000)
    expected_harvest: DateType | None = None


class PlantingNote(_PlantingBody):
    """A note on a planting's history (MCP ``annotate_planting``)."""

    note: str = Field(min_length=1, max_length=2000)


# ── Full persisted row shapes (DB mirrors) ────────────────────────────────


class Crop(BaseModel):
    """crops table row — full persisted shape.

    Sprint 22 (migrations 085/086) added: position_id, zone_id, crop_catalog_id FKs.
    Sprint 23 (migration 088) added: cleared_at timestamp + auto-event triggers.
    The legacy string columns (position, zone) stay until Phase 4d deprecation.
    """

    model_config = ConfigDict(extra="ignore")

    id: int
    name: str
    variety: str | None = None
    position: str
    zone: str
    planted_date: DateType
    expected_harvest: DateType | None = None
    stage: str = "seed"
    count: int | None = None
    seed_lot_id: str | None = None
    supplier: str | None = None
    base_temp_f: float = 50.0
    target_dli: float | None = None
    target_vpd_low: float | None = None
    target_vpd_high: float | None = None
    notes: str | None = None
    is_active: bool = True
    cleared_at: AwareDatetime | None = None
    created_at: AwareDatetime | None = None
    updated_at: AwareDatetime | None = None
    greenhouse_id: str = "vallery"
    # Sprint 22 topology FKs
    position_id: int | None = None
    zone_id: int | None = None
    crop_catalog_id: int | None = None


class CropEvent(BaseModel):
    """crop_events table row — lifecycle audit.

    Sprint 23 (migration 088): auto-populated by triggers on INSERT/UPDATE of
    the parent crops row. source='trigger' for auto-logged events,
    'manual'/'api'/'iris' for caller-driven events, 'backfill' for one-time
    migration rows.
    """

    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    ts: AwareDatetime | None = None
    crop_id: int | None = None
    event_type: str
    old_stage: str | None = None
    new_stage: str | None = None
    count: int | None = None
    operator: str | None = None
    source: str = "manual"
    notes: str | None = None
    greenhouse_id: str = "vallery"
    position_id: int | None = None  # Sprint 22: FK to positions.id


class Observation(BaseModel):
    """observations table row.

    Sprint 22 (migration 086) added: position_id, zone_id FKs.
    """

    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    ts: AwareDatetime | None = None
    obs_type: str
    zone: str | None = None
    position: str | None = None
    severity: int | None = None
    species: str | None = None
    count: int | None = None
    affected_pct: float | None = None
    crop_id: int | None = None
    photo_path: str | None = None
    observer: str | None = None
    source: str = "manual"
    notes: str | None = None
    image_observation_id: int | None = None
    health_score: float | None = None
    greenhouse_id: str = "vallery"
    position_id: int | None = None
    zone_id: int | None = None
    plant_height_cm: float | None = None
    leaf_count: int | None = None
    canopy_cover_pct: float | None = None
    flowering_count: int | None = None
    fruit_count: int | None = None
    root_condition: str | None = None
    mortality_count: int | None = None
    stress_tags: list[str] | None = None


# ── MCP action envelopes ──────────────────────────────────────────────────


CropActionKind = Literal["list", "get", "create", "update", "deactivate"]


class CropAction(BaseModel):
    """MCP `crops` tool input — replaces (action: str, data: str JSON)."""

    model_config = ConfigDict(extra="forbid")

    action: CropActionKind
    crop_id: int | None = None
    data: CropCreate | CropUpdate | None = None


ObservationActionKind = Literal[
    "list_observations",
    "record_observation",
    "list_events",
    "record_event",
    "record_harvest",
    "list_harvests",
    "record_treatment",
    "list_treatments",
    "list",
]


class ObservationAction(BaseModel):
    """MCP `observations` tool input — accepts any of the record_* payloads."""

    model_config = ConfigDict(extra="forbid")

    action: ObservationActionKind
    crop_id: int | None = None
    data: ObservationCreate | EventCreate | HarvestCreate | TreatmentCreate | None = None


class CropTaskRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    created_at: AwareDatetime | None = None
    updated_at: AwareDatetime | None = None
    greenhouse_id: str = "vallery"
    task_type: str
    priority: str = "normal"
    status: str = "open"
    crop_id: int | None = None
    position_id: int | None = None
    zone_id: int | None = None
    due_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None
    completed_by: str | None = None
    source: str = "system"
    related_observation_id: int | None = None
    related_treatment_id: int | None = None
    related_harvest_id: int | None = None
    notes: str | None = None
