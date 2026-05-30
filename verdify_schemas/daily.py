"""DailySummaryRow — 1:1 onto the daily_summary DB row.

61-field rollup written by daily-summary-snapshot.py at 00:05 UTC. Mirrors
the schema so vault-daily-writer, generate-daily-plan, and the website
renderer all read the same typed shape.
"""

from __future__ import annotations

from datetime import date as DateType

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class DailySummaryRow(BaseModel):
    model_config = ConfigDict(extra="ignore")  # Tolerate new columns without breaking old readers

    date: DateType
    greenhouse_id: str = "vallery"
    captured_at: AwareDatetime | None = None
    notes: str | None = None

    # Climate aggregates
    temp_min: float | None = None
    temp_avg: float | None = None
    temp_max: float | None = None
    rh_min: float | None = None
    rh_avg: float | None = None
    rh_max: float | None = None
    vpd_min: float | None = None
    vpd_avg: float | None = None
    vpd_max: float | None = None
    co2_avg: float | None = None
    outdoor_temp_min: float | None = None
    outdoor_temp_max: float | None = None
    dli_final: float | None = None

    # Stress hours (binary; NOT mutated by the migration-146 dual-write)
    stress_hours_heat: float = Field(default=0.0, ge=0, le=24)
    stress_hours_cold: float = Field(default=0.0, ge=0, le=24)
    stress_hours_vpd_high: float = Field(default=0.0, ge=0, le=24)
    stress_hours_vpd_low: float = Field(default=0.0, ge=0, le=24)

    # ── Binary compliance (existing; byte-stable through the dual-write window)
    compliance_pct: float | None = Field(default=None, ge=0, le=100)
    temp_compliance_pct: float | None = Field(default=None, ge=0, le=100)
    vpd_compliance_pct: float | None = Field(default=None, ge=0, le=100)

    # ── Graded + feasibility-aware compliance (migration 146, §6.7) ─────────
    # Priority-weighted house compliance (center 0.60 / east 0.40), additive
    # columns written alongside the binary calc during the co-existence window.
    compliance_v2_raw_pct: float | None = Field(default=None, ge=0, le=100)
    compliance_v2_attributable_pct: float | None = Field(default=None, ge=0, le=100)
    compliance_v2_unachievable_frac: float | None = Field(default=None, ge=0, le=1)
    graded_temp_compliance_pct: float | None = Field(default=None, ge=0, le=100)
    graded_vpd_compliance_pct: float | None = Field(default=None, ge=0, le=100)
    # Graded stress-hours = severity-weighted deficit integral (subsumes the
    # binary stress paths). Bounded by the binary value at each threshold.
    graded_stress_hours_heat: float | None = Field(default=None, ge=0, le=24)
    graded_stress_hours_cold: float | None = Field(default=None, ge=0, le=24)
    graded_stress_hours_vpd_high: float | None = Field(default=None, ge=0, le=24)
    graded_stress_hours_vpd_low: float | None = Field(default=None, ge=0, le=24)
    # Minutes whose feasibility could not be attributed (pre-relay_truth rows).
    feasibility_unknown_min: float | None = Field(default=None, ge=0)

    # Equipment cycles
    cycles_fan1: int = 0
    cycles_fan2: int = 0
    cycles_heat1: int = 0
    cycles_heat2: int = 0
    cycles_fog: int = 0
    cycles_vent: int = 0
    cycles_dehum: int = 0
    cycles_safety_dehum: int = 0
    cycles_grow_light: int | None = None
    cycles_mister_south: int | None = Field(default=None, ge=0)
    cycles_mister_west: int | None = Field(default=None, ge=0)
    cycles_mister_center: int | None = Field(default=None, ge=0)
    cycles_drip_wall: int | None = Field(default=None, ge=0)
    cycles_drip_center: int | None = Field(default=None, ge=0)

    # Mister fairness watchdog (sprint-24-alignment — firmware sprint-2 feature)
    mister_fairness_overrides_today: int | None = Field(default=None, ge=0)

    # Equipment runtime (minutes)
    runtime_fan1_min: float = 0.0
    runtime_fan2_min: float = 0.0
    runtime_heat1_min: float = 0.0
    runtime_heat2_min: float = 0.0
    runtime_fog_min: float = 0.0
    runtime_vent_min: float = 0.0
    runtime_grow_light_min: float = 0.0

    # Mister + drip runtime (hours)
    runtime_mister_south_h: float | None = None
    runtime_mister_west_h: float | None = None
    runtime_mister_center_h: float | None = None
    runtime_drip_wall_h: float | None = None
    runtime_drip_center_h: float | None = None

    # Water
    water_used_gal: float | None = None
    mister_water_gal: float | None = None

    # Costs + energy
    kwh_estimated: float | None = None
    therms_estimated: float | None = None
    peak_kw: float | None = None
    cost_electric: float | None = None
    cost_gas: float | None = None
    cost_water: float | None = None
    cost_total: float | None = None


class DailyZoneComplianceRow(BaseModel):
    """daily_zone_compliance row — per (date, zone) graded compliance.

    Child table written by the migration-146 dual-write in tasks.py alongside
    the house-level *_v2 columns on daily_summary. One row per graded zone
    (center, east, north). `proxy_flag` is true for center (no vpd_center
    probe yet → vpd_avg proxy, HW-1/NB1). See band-compliance §6.7.
    """

    model_config = ConfigDict(extra="ignore")

    date: DateType
    zone: str
    crop_catalog_id: int | None = None
    raw_compliance_pct: float | None = Field(default=None, ge=0, le=100)
    ctrl_compliance_pct: float | None = Field(default=None, ge=0, le=100)
    graded_temp_compliance_pct: float | None = Field(default=None, ge=0, le=100)
    graded_vpd_compliance_pct: float | None = Field(default=None, ge=0, le=100)
    graded_stress_hours_heat: float | None = Field(default=None, ge=0, le=24)
    graded_stress_hours_cold: float | None = Field(default=None, ge=0, le=24)
    graded_stress_hours_vpd_high: float | None = Field(default=None, ge=0, le=24)
    graded_stress_hours_vpd_low: float | None = Field(default=None, ge=0, le=24)
    unachievable_min: float | None = Field(default=None, ge=0)
    controller_miss_min: float | None = Field(default=None, ge=0)
    proxy_flag: bool = False
    captured_at: AwareDatetime | None = None


class ComplianceZoneWeightRow(BaseModel):
    """compliance_zone_weights row — priority weight per zone in the house roll-up.

    Seeded center 0.60 / east 0.40 / north,south,west 0 (empty zones excluded
    from the house reward). Stored so weights re-tune without a code change and
    the planner prompt can cite them. See band-compliance §6.3.
    """

    model_config = ConfigDict(extra="ignore")

    greenhouse_id: str = "vallery"
    zone: str
    weight: float = Field(..., ge=0)
