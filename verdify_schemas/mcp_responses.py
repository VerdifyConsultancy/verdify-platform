"""MCP read-tool response envelopes.

The 8 read-only MCP tools (climate, scorecard, equipment_state, forecast,
history, get_setpoints, plan_status, lessons) all return JSON strings. Until
Sprint 23 these were free-form dicts — Iris received whatever the SQL
happened to project. Now each tool builds its response through the matching
model below + `.model_dump_json()`, so a future SQL refactor that drops a
column fails at the boundary instead of silently returning a smaller shape.

Skipped intentionally:
- `query` — generic SELECT escape hatch; cannot be typed.
- `set_*` and `*_evaluate` write tools — already typed via Plan / PlanEvaluation.
- `plan_run` — fire-and-forget trigger, returns {"ok", "note"}; trivial.
- `alerts list` — already structured; can add later if useful.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date as DateType
from decimal import Decimal
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from .lessons import LessonConfidence


class ClimateSnapshot(BaseModel):
    """`climate()` tool response — one current reading + greenhouse mode."""

    model_config = ConfigDict(extra="ignore")

    temp_f: Decimal | None = None
    vpd_kpa: Decimal | None = None
    rh_pct: Decimal | None = None
    dew_point_f: Decimal | None = None
    dp_margin_f: Decimal | None = None
    vpd_south: Decimal | None = None
    vpd_west: Decimal | None = None
    vpd_east: Decimal | None = None
    outdoor_temp: Decimal | None = None
    outdoor_rh: Decimal | None = None
    lux: Decimal | None = None
    solar_w: Decimal | None = None
    solar_w_m2: Decimal | None = None
    age_seconds: int | None = None
    mode: str | None = None


def _scorecard_value_to_float(value: Any) -> float | None:
    """Normalize asyncpg Decimal / str / None into float | None for scorecard metrics."""
    if value is None:
        return None
    if isinstance(value, str):
        if not value or value.lower() in {"n/a", "perfect", "none", "null"}:
            return None
        return float(value)
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


class ScorecardResponse(BaseModel):
    """`scorecard(target_date)` tool response — typed projection of
    `fn_planner_scorecard()` (25 metrics).

    All fields are Optional: partial days (today, data gaps) emit a subset
    of metrics. Unknown metric keys raise ValidationError — if the DB
    function grows a new metric, this schema fails loud instead of silently
    dropping it. Wire format preserves the historical `7d_avg_*` JSON keys
    via aliases; downstream consumers (Iris prompt, daily-plan renderer,
    Grafana panels) keep reading the same shape.

    Authoritative metric list is `db/migrations/096-scorecard-live-resync.sql`
    and the matching `db/schema.sql` dump.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    # ── Score + compliance ──────────────────────────────────────────
    planner_score: float | None = None
    compliance_pct: float | None = None
    temp_compliance_pct: float | None = None
    vpd_compliance_pct: float | None = None

    # ── Graded + feasibility-aware compliance (migration 146/147, §6-§7) ──
    # Schema-first: these are modelled BEFORE fn_planner_scorecard emits them
    # so the extra='forbid' contract does not 500 the public /api/v1/scorecard
    # endpoint when the DB function grows the graded metrics. All optional.
    compliance_v2_raw_pct: float | None = None
    compliance_v2_attributable_pct: float | None = None
    compliance_v2_unachievable_frac: float | None = None
    graded_temp_compliance_pct: float | None = None
    graded_vpd_compliance_pct: float | None = None

    # ── Stress hours (four independent categories; overlap allowed so
    #    total_stress_h can exceed 24h)
    total_stress_h: float | None = None
    heat_stress_h: float | None = None
    cold_stress_h: float | None = None
    vpd_high_stress_h: float | None = None
    vpd_low_stress_h: float | None = None

    # ── Graded stress hours (deficit integral; subsumes binary paths) ─────
    graded_heat_stress_h: float | None = None
    graded_cold_stress_h: float | None = None
    graded_vpd_high_stress_h: float | None = None
    graded_vpd_low_stress_h: float | None = None

    # ── Utility usage (raw)
    kwh: float | None = None
    therms: float | None = None
    water_gal: float | None = None
    mister_water_gal: float | None = None

    # ── Utility cost (USD)
    cost_electric: float | None = None
    cost_gas: float | None = None
    cost_water: float | None = None
    cost_total: float | None = None

    # ── Dew point
    dp_margin_min_f: float | None = None
    dp_risk_hours: float | None = None

    # ── 7-day averages (alias preserves `7d_*` wire keys) ───────────
    avg_score_7d: float | None = Field(default=None, alias="7d_avg_score")
    avg_compliance_7d: float | None = Field(default=None, alias="7d_avg_compliance")
    avg_cost_7d: float | None = Field(default=None, alias="7d_avg_cost")
    avg_kwh_7d: float | None = Field(default=None, alias="7d_avg_kwh")
    avg_therms_7d: float | None = Field(default=None, alias="7d_avg_therms")
    avg_water_gal_7d: float | None = Field(default=None, alias="7d_avg_water_gal")

    # Present in the migration-076/077-era function but removed from the
    # canonical 25-metric scorecard in migration 096. Kept as optional aliases
    # until old test databases are fully rebuilt from the resynced schema.
    avg_stress_7d: float | None = Field(default=None, alias="7d_avg_stress")
    avg_dp_risk_7d: float | None = Field(default=None, alias="7d_avg_dp_risk")

    @classmethod
    def from_metric_rows(cls, rows: Iterable[Any]) -> ScorecardResponse:
        """Build from `fn_planner_scorecard()` rows.

        Accepts asyncpg Records, dicts with (metric, value) keys, or raw
        2-tuples. Converts Decimal → float and treats sentinel strings
        ('n/a', 'perfect') as None.
        """
        data: dict[str, float | None] = {}
        for row in rows:
            if isinstance(row, tuple):
                metric, value = row[0], row[1]
            else:
                metric, value = row["metric"], row["value"]
            data[str(metric)] = _scorecard_value_to_float(value)
        return cls.model_validate(data)

    @classmethod
    def metric_names(cls) -> frozenset[str]:
        """Wire-format metric names this schema recognizes (with 7d_ aliases)."""
        names: set[str] = set()
        for field_name, field in cls.model_fields.items():
            names.add(field.alias or field_name)
        return frozenset(names)


class EquipmentStateRow(BaseModel):
    """One row of the `equipment_state()` tool response."""

    model_config = ConfigDict(extra="ignore")

    equipment: str
    state: bool
    since: str  # HH:MM:SS — Denver-local time string


class ForecastSummaryRow(BaseModel):
    """One hour of the `forecast(hours)` tool response."""

    model_config = ConfigDict(extra="ignore")

    time: str  # "Sun 14:00" Denver-local
    temp: Decimal | None = None
    rh: Decimal | None = None
    vpd: Decimal | None = None
    cloud: Decimal | None = None
    solar: Decimal | None = None


class HistoryRow(BaseModel):
    """One bucket of the `history(metric, hours, resolution_min)` tool response.

    Permissive — different metrics project different columns.
    """

    model_config = ConfigDict(extra="allow")

    time: AwareDatetime | str


class SetpointSummary(BaseModel):
    """One row of the `get_setpoints()` tool response."""

    model_config = ConfigDict(extra="ignore")

    parameter: str
    value: Decimal
    source: str
    updated: str  # HH:MM Denver-local


class PlanStatusJournal(BaseModel):
    model_config = ConfigDict(extra="ignore")

    plan_id: str
    created: str
    hypothesis: str | None = None
    experiment: str | None = None
    expected_outcome: str | None = None


class PlanStatusWaypoint(BaseModel):
    model_config = ConfigDict(extra="ignore")

    time: str
    params: int = Field(..., ge=0)


class PlanStatusResponse(BaseModel):
    """`plan_status()` tool response — current plan + future waypoints."""

    model_config = ConfigDict(extra="ignore")

    plan: PlanStatusJournal | None = None
    future_waypoints: list[PlanStatusWaypoint] = Field(default_factory=list)


class LessonSummary(BaseModel):
    """One row of the `lessons()` tool response."""

    model_config = ConfigDict(extra="ignore")

    category: str
    condition: str
    lesson: str
    confidence: LessonConfidence
    times_validated: int = Field(..., ge=0)


# Generic envelopes for tool errors / fire-and-forget acks.
ToolStatus = Literal["ok", "error"]


class ToolError(BaseModel):
    model_config = ConfigDict(extra="ignore")

    error: str
    details: list | dict | None = None


class PlanRunResponse(BaseModel):
    """`plan_run(mode)` tool response — fire-and-forget trigger."""

    model_config = ConfigDict(extra="ignore")

    ok: bool
    note: str | None = None
    error: str | None = None
    trigger_id: str | None = None
    event_type: str | None = None
    planner_instance: str | None = None
    session_key: str | None = None
    status: str | None = None
    hermes_run_id: str | None = None


# ── Date stub used only to keep the import block tidy ─
_unused_date_alias: type = DateType  # noqa: F841
