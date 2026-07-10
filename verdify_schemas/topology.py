"""Physical / logical topology schemas — Sprint 22 Phase 1.

First-class entities for the greenhouse structural spine:

    Greenhouse → Zone → Shelf → Position → Crop
    Zone → Sensor, Equipment, WaterSystem
    Equipment → Switch (relay pin)
    WaterSystem → PressureGroup

Prior to Sprint 22, this topology was encoded as free-text fields
(`crops.zone`, `crops.position`, `sensor_registry.zone`,
`equipment_assets.location`) and hand-typed markdown tables. This module
promotes each concept to a typed entity with a typed-ID reference so
the planner, API, website, and DB share one model.

Multi-tenant contract: every top-level entity requires `greenhouse_id`;
there is no `"vallery"` default. New code should go through these types.
"""

from __future__ import annotations

from datetime import date as DateType
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from .telemetry import EquipmentId as EquipmentSlug  # Literal of known equipment slugs

# ── Typed ID layer ─────────────────────────────────────────────────
#
# Slug-style IDs follow the PlanId / TunableParameter pattern: an
# Annotated[str, ...] that enforces a URL-safe shape at the boundary.
# The closed-Literal `EquipmentSlug` (re-exported from telemetry) stays
# the canonical valid-equipment-name set.

_SLUG_PATTERN = r"^[a-z][a-z0-9_]*$"
_POSITION_PATTERN = r"^[A-Z][A-Z0-9_\-]*[A-Z0-9]$"

GreenhouseId = Annotated[str, Field(pattern=_SLUG_PATTERN, min_length=1, max_length=64)]
"""Greenhouse slug: lowercase snake_case (e.g., "vallery")."""

ZoneId = Annotated[str, Field(pattern=_SLUG_PATTERN, min_length=1, max_length=64)]
"""Zone slug: lowercase snake_case (e.g., "south", "north", "center")."""

ShelfId = Annotated[str, Field(pattern=_SLUG_PATTERN, min_length=1, max_length=64)]
"""Shelf slug within a zone (e.g., "south_shelf_top", "south_floor")."""

PositionId = Annotated[str, Field(pattern=_POSITION_PATTERN, min_length=1, max_length=64)]
"""Position label: upper-snake with optional hyphens and digit suffix
(e.g., "SOUTH-FLOOR-1", "SOUTH-SHELF-T1", "CENTER-HANG-2")."""

SensorId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.]*$", min_length=1, max_length=128)]
"""Sensor slug. Dotted namespace permitted: "climate.south_temp", "soil.south_moisture"."""

SwitchId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*\.\d+$", min_length=3, max_length=64)]
"""Switch slug: "<board>.<pin>" (e.g., "pcf_out_1.3", "pcf_out_2.7")."""

WaterSystemId = Annotated[str, Field(pattern=_SLUG_PATTERN, min_length=1, max_length=64)]
"""Water-system slug (e.g., "south_mister_clean", "wall_drip_fert")."""

PressureGroupId = Annotated[str, Field(pattern=_SLUG_PATTERN, min_length=1, max_length=64)]
"""Pressure-manifold group slug (e.g., "mister_manifold", "drip_manifold")."""


# ── Taxonomies (Literals) ──────────────────────────────────────────

ZoneStatus = Literal["active", "offline", "decommissioned"]

ShelfKind = Literal["floor", "shelf", "hang", "rack", "nft", "hydro"]
"""Structural kind of a shelf/location within a zone."""

MountType = Literal["pot", "shelf_slot", "hanging_hook", "nft_port", "hydro_raft", "direct_ground"]

SensorKind = Literal[
    "climate_probe",  # Tzone SHT3X: temp + RH → VPD derived
    "soil_probe",  # DFRobot: moisture + temp + EC
    "co2",
    "light",  # LDR / PAR / DLI
    "flow",  # water pulse counter
    "hydro_quality",  # YINMIK: pH/EC/TDS/ORP/temp
    "weather",  # Tempest
    "energy",  # Shelly EM50
    "camera",
    "leaf",
    "pressure",
    "derived",  # computed (e.g., greenhouse temp_avg)
]

SensorProtocol = Literal[
    "modbus_rtu",
    "adc",
    "gpio_pulse",
    "ble",
    "http_api",
    "mqtt",
    "esphome_native",
    "frigate",
    "derived",
]

EquipmentKind = Literal[
    "heater",
    "fan",
    "vent",
    "fog",
    "mister",
    "drip",
    "valve",
    "light",
    "pump",
    "heater_water",
    "sensor_bridge",
    "controller",
    "camera",
]

EquipmentAliasKind = Literal["telemetry", "legacy_catalog", "display"]
ResourceKind = Literal["electric_watts", "gas_btu_per_hour", "water_gpm"]
ResourceCoefficientSource = Literal["nameplate", "operator", "meter_fit", "measured", "unknown"]
ResourceUnit = Literal["W", "BTU/h", "gal/min"]

SwitchBoard = Literal["pcf_out_1", "pcf_out_2", "pcf_in", "gpio"]

WaterSystemKind = Literal["mister", "drip", "fog", "fertigation", "nft", "manual"]

PressureConstraint = Literal[
    "mister_max_1",  # only one mister zone can fire at a time
    "drip_max_1",
    "mister_max_2",
    "none",
]


# ── Greenhouse ─────────────────────────────────────────────────────


class Greenhouse(BaseModel):
    """greenhouses table row — multi-tenant root."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    id: GreenhouseId
    name: str = Field(..., min_length=1, max_length=200)
    owner_email: str | None = Field(default=None, max_length=200)
    timezone: str = Field(default="America/Denver", max_length=64)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    elevation_ft: float | None = None
    esp32_host: str | None = Field(default=None, max_length=200)
    esp32_port: int | None = Field(default=6053, ge=1, le=65535)
    mqtt_topic: str | None = Field(default=None, max_length=200)
    status: Literal["active", "inactive", "provisioning"] = "active"
    config: dict = Field(default_factory=dict)
    created_at: AwareDatetime | None = None
    updated_at: AwareDatetime | None = None


# ── Zone ───────────────────────────────────────────────────────────


class Zone(BaseModel):
    """zones table row — a spatial region with its own sensor + climate profile."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    id: int | None = None
    greenhouse_id: GreenhouseId
    slug: ZoneId
    name: str = Field(..., min_length=1, max_length=200)
    orientation: str | None = Field(default=None, max_length=500)
    sensor_modbus_addr: int | None = Field(default=None, ge=1, le=247)
    peak_temp_f: float | None = None
    status: ZoneStatus = "active"
    notes: str | None = None
    created_at: AwareDatetime | None = None


class ZoneCreate(BaseModel):
    """POST /api/v1/zones body + import payload."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    greenhouse_id: GreenhouseId
    slug: ZoneId
    name: str = Field(..., min_length=1, max_length=200)
    orientation: str | None = Field(default=None, max_length=500)
    sensor_modbus_addr: int | None = Field(default=None, ge=1, le=247)
    peak_temp_f: float | None = None
    status: ZoneStatus = "active"
    notes: str | None = None


class ZoneUpdate(BaseModel):
    """PATCH /api/v1/zones/{id} body — all fields optional."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=200)
    orientation: str | None = Field(default=None, max_length=500)
    sensor_modbus_addr: int | None = Field(default=None, ge=1, le=247)
    peak_temp_f: float | None = None
    status: ZoneStatus | None = None
    notes: str | None = None


# ── Shelf ──────────────────────────────────────────────────────────


class Shelf(BaseModel):
    """shelves table row — a structural sub-region within a zone.

    Examples: "south_shelf_top" (top tier of south-zone shelf rack),
    "south_floor" (floor pots area), "center_hang" (hanging hooks).
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    id: int | None = None
    greenhouse_id: GreenhouseId
    zone_id: int  # FK → zones.id
    slug: ShelfId
    name: str = Field(..., min_length=1, max_length=200)
    kind: ShelfKind
    tier: int | None = Field(default=None, ge=0, description="Vertical tier 0=bottom")
    position_scheme: str | None = Field(
        default=None,
        max_length=200,
        description='Template e.g. "SOUTH-SHELF-T{1..4}" — documentation only',
    )
    notes: str | None = None
    created_at: AwareDatetime | None = None


class ShelfCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    greenhouse_id: GreenhouseId
    zone_id: int
    slug: ShelfId
    name: str = Field(..., min_length=1, max_length=200)
    kind: ShelfKind
    tier: int | None = Field(default=None, ge=0)
    position_scheme: str | None = Field(default=None, max_length=200)
    notes: str | None = None


# ── Position ───────────────────────────────────────────────────────


class Position(BaseModel):
    """positions table row — an individual planting slot.

    A position is the canonical FK target for crops and observations —
    instead of `crops.position: text`, crops point here via position_id.
    Label is the human-facing identifier (e.g., "SOUTH-FLOOR-1").
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    id: int | None = None
    greenhouse_id: GreenhouseId
    shelf_id: int  # FK → shelves.id
    label: PositionId
    slot_number: int | None = Field(default=None, ge=1)
    mount_type: MountType
    is_active: bool = True
    notes: str | None = None
    created_at: AwareDatetime | None = None


class PositionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    greenhouse_id: GreenhouseId
    shelf_id: int
    label: PositionId
    slot_number: int | None = Field(default=None, ge=1)
    mount_type: MountType
    notes: str | None = None


# ── Sensor ─────────────────────────────────────────────────────────


class Sensor(BaseModel):
    """sensors table row — replaces sensor_registry with proper FKs.

    Supersedes `sensor_registry` (Sprint 22 Phase 6 drops the old table).
    `source_table`/`source_column` locates the live reading for
    staleness detection (e.g., "climate"/"temp_south").
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    id: int | None = None
    greenhouse_id: GreenhouseId
    slug: SensorId
    zone_id: int | None = None  # Optional — outdoor sensors have no zone
    position_id: int | None = None  # For position-scoped sensors (leaf, soil)
    kind: SensorKind
    protocol: SensorProtocol
    model: str | None = Field(default=None, max_length=200)
    modbus_addr: int | None = Field(default=None, ge=1, le=247)
    gpio_pin: int | None = Field(default=None, ge=0, le=64)
    unit: str | None = Field(default=None, max_length=32)
    source_table: str | None = Field(default=None, max_length=100)
    source_column: str | None = Field(default=None, max_length=100)
    expected_interval_s: int | None = Field(default=None, ge=1)
    accuracy: str | None = Field(default=None, max_length=200)
    installed_date: DateType | None = None
    is_active: bool = True
    notes: str | None = None
    created_at: AwareDatetime | None = None


class SensorCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    greenhouse_id: GreenhouseId
    slug: SensorId
    zone_id: int | None = None
    position_id: int | None = None
    kind: SensorKind
    protocol: SensorProtocol
    model: str | None = Field(default=None, max_length=200)
    modbus_addr: int | None = Field(default=None, ge=1, le=247)
    gpio_pin: int | None = Field(default=None, ge=0, le=64)
    unit: str | None = Field(default=None, max_length=32)
    source_table: str | None = Field(default=None, max_length=100)
    source_column: str | None = Field(default=None, max_length=100)
    expected_interval_s: int | None = Field(default=None, ge=1)
    accuracy: str | None = Field(default=None, max_length=200)
    installed_date: DateType | None = None
    notes: str | None = None


# ── Equipment ──────────────────────────────────────────────────────


class Equipment(BaseModel):
    """equipment table row — the devices controlled by the ESP32.

    `slug` is the canonical instance name (e.g., "mister_south", "fan1",
    "heat2") and must be a member of telemetry.EquipmentId. This is what
    equipment_state events reference; `id` is the relational PK used by
    switches.equipment_id.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    id: int | None = None
    greenhouse_id: GreenhouseId
    slug: EquipmentSlug  # Literal of known equipment names (telemetry.EquipmentId)
    zone_id: int | None = None  # Nullable: whole-greenhouse equipment (gas furnace)
    kind: EquipmentKind
    name: str = Field(..., min_length=1, max_length=200)
    model: str | None = Field(default=None, max_length=200)
    manufacturer: str | None = Field(default=None, max_length=200)
    watts: float | None = Field(default=None, ge=0)
    cost_per_hour_usd: float | None = Field(default=None, ge=0)
    specs: dict = Field(default_factory=dict)  # freeform: CFM, BTU, head count, etc.
    install_date: DateType | None = None
    is_active: bool = True
    notes: str | None = None
    created_at: AwareDatetime | None = None


class EquipmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    greenhouse_id: GreenhouseId
    slug: EquipmentSlug
    zone_id: int | None = None
    kind: EquipmentKind
    name: str = Field(..., min_length=1, max_length=200)
    model: str | None = Field(default=None, max_length=200)
    manufacturer: str | None = Field(default=None, max_length=200)
    watts: float | None = Field(default=None, ge=0)
    cost_per_hour_usd: float | None = Field(default=None, ge=0)
    specs: dict = Field(default_factory=dict)
    install_date: DateType | None = None
    notes: str | None = None


class EquipmentAlias(BaseModel):
    """One explicit historical/telemetry alias into canonical equipment."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    id: int | None = None
    greenhouse_id: GreenhouseId
    alias_slug: Annotated[str, Field(pattern=_SLUG_PATTERN, min_length=1, max_length=64)]
    equipment_id: int
    alias_kind: EquipmentAliasKind = "telemetry"
    source: str = Field(..., min_length=1, max_length=200)
    evidence_ref: str = Field(..., min_length=1, max_length=500)
    valid_from: AwareDatetime
    valid_to: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_interval(self) -> EquipmentAlias:
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError("valid_to must be later than valid_from")
        return self


class ResourceCoefficient(BaseModel):
    """Revisioned resource coefficient with machine-readable uncertainty."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    id: int | None = None
    equipment_id: int
    resource_kind: ResourceKind
    unit: ResourceUnit
    nominal_value: float = Field(..., ge=0)
    lower_bound: float = Field(..., ge=0)
    upper_bound: float = Field(..., ge=0)
    coefficient_source: ResourceCoefficientSource
    revision: str = Field(..., min_length=1, max_length=200)
    evidence_ref: str = Field(..., min_length=1, max_length=500)
    valid_from: AwareDatetime
    valid_to: AwareDatetime | None = None
    is_model_default: bool = False
    notes: str | None = None

    @model_validator(mode="after")
    def validate_bounds_and_interval(self) -> ResourceCoefficient:
        if not self.lower_bound <= self.nominal_value <= self.upper_bound:
            raise ValueError("coefficient bounds must contain nominal_value")
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError("valid_to must be later than valid_from")
        return self


# ── Switch (relay pin assignment) ──────────────────────────────────


class Switch(BaseModel):
    """switches table row — a PCF8574 pin driving one equipment instance.

    Replaces the hand-typed relay map in website/greenhouse/equipment.md.
    `(board, pin)` is unique per greenhouse.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    id: int | None = None
    greenhouse_id: GreenhouseId
    slug: SwitchId  # "pcf_out_1.3" etc. — computed = f"{board}.{pin}"
    equipment_id: int | None = None  # FK → equipment.id; None = unused pin
    board: SwitchBoard
    pin: int = Field(..., ge=0, le=15)
    purpose: str = Field(..., min_length=1, max_length=200)
    state_source_column: str | None = Field(
        default=None,
        max_length=100,
        description="equipment_state column to read on/off from (if applicable)",
    )
    is_active: bool = True
    notes: str | None = None
    created_at: AwareDatetime | None = None


class SwitchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    greenhouse_id: GreenhouseId
    slug: SwitchId
    equipment_id: int | None = None
    board: SwitchBoard
    pin: int = Field(..., ge=0, le=15)
    purpose: str = Field(..., min_length=1, max_length=200)
    state_source_column: str | None = Field(default=None, max_length=100)
    notes: str | None = None


# ── WaterSystem + PressureGroup ───────────────────────────────────


class PressureGroup(BaseModel):
    """pressure_groups table row — constraint cluster for the water manifold.

    The solenoid manifold can drive only ONE mister zone at a time (and
    one drip zone). The pressure group encodes that firmware-enforced
    constraint as relational data so the planner and API can reason
    about it.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    id: int | None = None
    greenhouse_id: GreenhouseId
    slug: PressureGroupId
    name: str = Field(..., min_length=1, max_length=200)
    constraint: PressureConstraint
    max_concurrent: int = Field(default=1, ge=1, le=8)
    description: str | None = Field(default=None, max_length=1000)
    created_at: AwareDatetime | None = None


class PressureGroupCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    greenhouse_id: GreenhouseId
    slug: PressureGroupId
    name: str = Field(..., min_length=1, max_length=200)
    constraint: PressureConstraint
    max_concurrent: int = Field(default=1, ge=1, le=8)
    description: str | None = Field(default=None, max_length=1000)


class WaterSystem(BaseModel):
    """water_systems table row — a mister/drip/fog delivery system.

    Groups the physical delivery (head count, nozzle count, mount) with
    the relational pointers (zone, pressure group, equipment). One
    equipment row (mister_south) can supply one water_system
    ("south_mister_clean") — or two (clean + fert paths).
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    id: int | None = None
    greenhouse_id: GreenhouseId
    slug: WaterSystemId
    zone_id: int | None = None  # Nullable: whole-greenhouse systems (wall drip spans zones)
    equipment_id: int | None = None  # FK → equipment.id
    pressure_group_id: int | None = None  # FK → pressure_groups.id
    kind: WaterSystemKind
    name: str = Field(..., min_length=1, max_length=200)
    nozzle_count: int | None = Field(default=None, ge=0)
    head_count: int | None = Field(default=None, ge=0)
    mount: str | None = Field(default=None, max_length=200)
    is_fert_path: bool = False
    is_active: bool = True
    effectiveness_note: str | None = Field(default=None, max_length=500)
    created_at: AwareDatetime | None = None


class WaterSystemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    greenhouse_id: GreenhouseId
    slug: WaterSystemId
    zone_id: int | None = None
    equipment_id: int | None = None
    pressure_group_id: int | None = None
    kind: WaterSystemKind
    name: str = Field(..., min_length=1, max_length=200)
    nozzle_count: int | None = Field(default=None, ge=0)
    head_count: int | None = Field(default=None, ge=0)
    mount: str | None = Field(default=None, max_length=200)
    is_fert_path: bool = False
    effectiveness_note: str | None = Field(default=None, max_length=500)
