"""Operations row schemas — audit trail for hands-on greenhouse work.

Every grower/operator action lands in one of these:
- Treatment: pesticide / fungicide / fertigation application with rate + PHI/REI
- Harvest: yield event with weight, count, revenue
- IrrigationLog / IrrigationSchedule: retired compatibility rows. Canonical
  irrigation state is v_irrigation_schedule_current plus
  v_irrigation_fertigation_runs.
- LabResult: full nutrient panel from external lab
- MaintenanceLog: equipment service, cost, next-due
- ConsumablesLog: inventory (fertilizer, growing media, pest control)
"""

from __future__ import annotations

from datetime import date as DateType
from datetime import time as TimeType
from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class Treatment(BaseModel):
    """treatments table row — one per pesticide/fungicide/nutrient application."""

    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    ts: AwareDatetime | None = None
    greenhouse_id: str = "vallery"
    product: str = Field(..., min_length=1)
    active_ingredient: str | None = None
    concentration: float | None = Field(default=None, ge=0)
    rate: float | None = Field(default=None, ge=0)
    rate_unit: str | None = None
    method: str | None = None
    zone: str | None = None
    crop_id: int | None = None
    position_id: int | None = None
    target_pest: str | None = None
    phi_days: int | None = Field(default=None, ge=0)  # pre-harvest interval
    rei_hours: int | None = Field(default=None, ge=0)  # restricted-entry interval
    applicator: str | None = None
    observation_id: int | None = None
    followup_due_at: AwareDatetime | None = None
    followup_completed_at: AwareDatetime | None = None
    outcome: str | None = None
    notes: str | None = None


class Harvest(BaseModel):
    """harvests table row — yield event."""

    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    ts: AwareDatetime | None = None
    greenhouse_id: str = "vallery"
    crop_id: int | None = None
    position_id: int | None = None
    weight_kg: float | None = Field(default=None, ge=0)
    unit_count: int | None = Field(default=None, ge=0)
    quality_grade: str | None = None
    salable_weight_kg: float | None = Field(default=None, ge=0)
    cull_weight_kg: float | None = Field(default=None, ge=0)
    cull_reason: str | None = None
    quality_reason: str | None = None
    zone: str | None = None
    destination: str | None = None
    unit_price: float | None = Field(default=None, ge=0)
    revenue: float | None = Field(default=None, ge=0)
    labor_minutes: int | None = Field(default=None, ge=0)
    operator: str | None = None
    notes: str | None = None


# ── Input envelopes for MCP tool boundary ─────────────────────────────────


class HarvestCreate(BaseModel):
    """MCP observations(record_harvest) data payload.

    Column names mirror the live `harvests` table exactly — `unit_price` (not
    `unit_price_usd`) and `operator` (not `harvested_by`). Tenant context
    (`greenhouse_id`) is supplied by the MCP caller, not by the envelope.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    weight_kg: float | None = Field(default=None, ge=0)
    unit_count: int | None = Field(default=None, ge=0)
    quality_grade: str | None = Field(default=None, max_length=50)
    salable_weight_kg: float | None = Field(default=None, ge=0)
    cull_weight_kg: float | None = Field(default=None, ge=0)
    cull_reason: str | None = Field(default=None, max_length=500)
    quality_reason: str | None = Field(default=None, max_length=500)
    zone: str | None = Field(default=None, max_length=100)
    destination: str | None = Field(default=None, max_length=200)
    unit_price: float | None = Field(default=None, ge=0)
    revenue: float | None = Field(default=None, ge=0)
    labor_minutes: int | None = Field(default=None, ge=0)
    operator: str | None = Field(default=None, max_length=100)
    notes: str | None = None


class TreatmentCreate(BaseModel):
    """MCP observations(record_treatment) data payload.

    Column names mirror the live `treatments` table — `applicator` (not
    `applied_by`). Tenant context (`greenhouse_id`) is supplied by the MCP
    caller, not by the envelope.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    product: str = Field(..., min_length=1, max_length=200)
    active_ingredient: str | None = Field(default=None, max_length=200)
    concentration: float | None = Field(default=None, ge=0)
    rate: float | None = Field(default=None, ge=0)
    rate_unit: str | None = Field(default=None, max_length=50)
    method: str | None = Field(default=None, max_length=100)
    zone: str | None = Field(default=None, max_length=100)
    target_pest: str | None = Field(default=None, max_length=200)
    phi_days: int | None = Field(default=None, ge=0)
    rei_hours: int | None = Field(default=None, ge=0)
    applicator: str | None = Field(default=None, max_length=100)
    observation_id: int | None = Field(default=None, ge=1)
    followup_due_at: AwareDatetime | None = None
    followup_completed_at: AwareDatetime | None = None
    outcome: str | None = Field(default=None, max_length=1000)
    notes: str | None = None


class IrrigationLog(BaseModel):
    """Retired compatibility row.

    Canonical irrigation/fertigation events are reconstructed from
    equipment_state in v_irrigation_fertigation_runs.
    """

    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    ts: AwareDatetime
    greenhouse_id: str = "vallery"
    zone: str = Field(..., min_length=1)
    schedule_id: int | None = None
    scheduled_time: TimeType | None = None
    actual_start: AwareDatetime
    actual_end: AwareDatetime | None = None
    volume_gal: Decimal | None = Field(default=None, ge=0)
    source: str = "manual"
    weather_skip: bool | None = None
    fertigation: bool = False
    metering_method: str | None = None
    notes: str | None = None
    created_at: AwareDatetime | None = None


class IrrigationSchedule(BaseModel):
    """Retired compatibility row.

    Canonical current schedule state is v_irrigation_schedule_current, sourced
    from active-plan values and ESP32 observed/readback state.
    """

    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    greenhouse_id: str = "vallery"
    zone: str = Field(..., min_length=1)
    start_time: TimeType
    duration_s: int = Field(..., ge=0)
    days_of_week: list[int] = Field(..., min_length=1)  # 0-6 ISO
    enabled: bool = True
    notes: str | None = None
    created_at: AwareDatetime | None = None
    updated_at: AwareDatetime | None = None


class LabResult(BaseModel):
    """lab_results table row — full nutrient panel from an external lab."""

    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    ts: AwareDatetime | None = None
    greenhouse_id: str = "vallery"
    sample_type: str = Field(..., min_length=1)
    zone: str | None = None
    crop_id: int | None = None
    lab_name: str | None = None
    sampled_at: DateType | None = None
    ph: float | None = Field(default=None, ge=0, le=14)
    ec_ms_cm: float | None = Field(default=None, ge=0)
    n_pct: float | None = Field(default=None, ge=0)
    p_pct: float | None = Field(default=None, ge=0)
    k_pct: float | None = Field(default=None, ge=0)
    ca_pct: float | None = Field(default=None, ge=0)
    mg_pct: float | None = Field(default=None, ge=0)
    fe_ppm: float | None = Field(default=None, ge=0)
    mn_ppm: float | None = Field(default=None, ge=0)
    zn_ppm: float | None = Field(default=None, ge=0)
    b_ppm: float | None = Field(default=None, ge=0)
    cu_ppm: float | None = Field(default=None, ge=0)
    na_ppm: float | None = Field(default=None, ge=0)
    cl_ppm: float | None = Field(default=None, ge=0)
    recipe_id: int | None = None
    source_sample_id: str | None = None
    notes: str | None = None


class NutrientRecipe(BaseModel):
    """nutrient_recipes table row — a feed formula consumers dose against.

    `salt_model` / `product_name` are the migration-145/146 additions (design
    §5.3). Without them a consumer doing `stock_a_ml_per_l × factor` produces a
    NULL EC for single-salt recipes (Vanda MSU 13-3-15 carries NULL stock_a/b),
    a SAF-2 blind-dose risk. `salt_model='single_salt'` says "dose by mixing to
    target_ec, NOT by A/B ml/L math". Both fields are optional so existing
    2-part GH-Flora rows (NULL salt_model) keep validating during co-existence.
    """

    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    name: str = Field(..., min_length=1)
    crop_id: int | None = None
    stage: str | None = None
    target_ec: float | None = Field(default=None, ge=0)
    target_ph_low: float | None = Field(default=None, ge=0, le=14)
    target_ph_high: float | None = Field(default=None, ge=0, le=14)
    n_ppm: float | None = Field(default=None, ge=0)
    p_ppm: float | None = Field(default=None, ge=0)
    k_ppm: float | None = Field(default=None, ge=0)
    ca_ppm: float | None = Field(default=None, ge=0)
    mg_ppm: float | None = Field(default=None, ge=0)
    fe_ppm: float | None = Field(default=None, ge=0)
    stock_a_ml_per_l: float | None = Field(default=None, ge=0)
    stock_b_ml_per_l: float | None = Field(default=None, ge=0)
    salt_model: Literal["two_part", "single_salt"] | None = None
    product_name: str | None = None
    notes: str | None = None
    is_active: bool = True
    created_at: AwareDatetime | None = None


class MaintenanceLog(BaseModel):
    """maintenance_log table row."""

    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    ts: AwareDatetime | None = None
    greenhouse_id: str = "vallery"
    equipment: str | None = None
    service_type: str | None = None
    description: str | None = None
    cost: float | None = Field(default=None, ge=0)
    technician: str | None = None
    next_due: DateType | None = None
    notes: str | None = None


class ConsumablesLog(BaseModel):
    """consumables_log table row — inventory purchase."""

    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    greenhouse_id: str = "vallery"
    purchased_date: DateType
    category: str = Field(..., min_length=1)
    item_name: str = Field(..., min_length=1)
    quantity: Decimal | None = Field(default=None, ge=0)
    unit: str | None = None
    cost_usd: Decimal = Field(..., ge=0)
    zone: str | None = None
    notes: str | None = None
    created_at: AwareDatetime | None = None
