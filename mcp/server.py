#!/usr/bin/env python3
"""
Verdify MCP Server — Greenhouse control tools for Agent Iris.

Gives Iris direct access to greenhouse data, planner control,
and setpoint management through the standard MCP protocol.

Run: python mcp/server.py
Transport: streamable-http on port 8400
"""

import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import asyncpg
from mcp.server.fastmcp import FastMCP
from pydantic import ValidationError

# verdify_schemas lives one level up from this server file in every worktree.
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
from slack_config import load_slack_settings  # noqa: E402
from slack_ops.service import handle_slack_command  # noqa: E402
from verdify_schemas import (  # noqa: E402
    ALL_TUNABLES,
    AlertAckPayload,
    AlertResolvePayload,
    ClimateSnapshot,
    CropCreate,
    CropUpdate,
    EquipmentStateRow,
    EventCreate,
    ForecastSummaryRow,
    HarvestCreate,
    LessonCreate,
    LessonSummary,
    LessonSupersede,
    LessonUpdate,
    LessonValidate,
    ObservationCreate,
    OutcomeKpiActionRow,
    OutcomeKpiCoverage,
    OutcomeKpiResponse,
    Plan,
    PlanDeliveryLogRow,
    PlanEvaluation,
    PlanHypothesisStructured,
    PlanRunResponse,
    PlanStatusJournal,
    PlanStatusResponse,
    PlanStatusWaypoint,
    ScorecardResponse,
    SetpointSummary,
    SlackCommandRequest,
    TreatmentCreate,
    derive_lesson_state,
    is_legal_lesson_transition,
)
from verdify_schemas.climate_intent import (  # noqa: E402
    CLIMATE_INTENT_CONTRACT_VERSION,
    CLIMATE_INTENT_FIELDS,
    ClimateIntent,
    climate_intent_materialization_guardrails,
    materialize_climate_intent_tier1,
)
from verdify_schemas.tunable_registry import (  # noqa: E402
    BAND_OWNED_REG,
    CROP_BAND_REG,
    PLANNER_PUSHABLE_REG,
    TIER1_REG,
    registry_value_error,
)

# ── Config ──
# Read DB password from .env
_env_path = Path("/srv/verdify/.env")
_db_pass = "verdify"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        if line.startswith("POSTGRES_PASSWORD="):
            _db_pass = line.split("=", 1)[1].strip().strip('"').strip("'")
# #24: the MCP server is already DSN-native — it connects via asyncpg against
# DB_DSN, never `docker exec psql`. This IS the VERDIFY_DB_BACKEND=dsn path of
# the shared scripts/lib/psql-verdify.sh contract: setting DB_DSN to an
# in-cluster Postgres endpoint moves this service off the VM with no code change.
# Default below preserves the live VM connection (localhost:5432).
DB_DSN = os.environ.get("DB_DSN", f"postgresql://verdify:{_db_pass}@localhost:5432/verdify")
# Legacy planner.py removed — planning runs via iris_planner.py → Hermes /v1/runs
BAND_OWNED_PARAMS = BAND_OWNED_REG
# P1a (B6): the crop temp/VPD band targets (temp_low/high, vpd_low/high, per-zone
# vpd targets). These are dispatcher/curve-owned and MUST NOT be written to
# setpoint_plan (that caused clamp storms), but the planner's INTENDED band is
# worth recording in the plan_journal audit so v_plan_accuracy / v_plan_compliance
# can grade planned-vs-served band against the new (migration-145) curve. We
# capture them into hypothesis_structured.planned_band WITHOUT actuating them.
CROP_BAND_PARAMS = CROP_BAND_REG
_OPENAI_KEY_FILES = (
    Path("/etc/verdify/hermes-iris.env"),
    Path("/mnt/agents/shared/credentials/openai_api_key.txt"),
)
PLAN_REQUIRED_PARAMS = TIER1_REG
TIER1_TUNABLES = TIER1_REG

FORCED_ON_SWITCH_PARAMS = frozenset({"sw_fsm_controller_enabled"})
CLIMATE_TARGET_PARAM_ALIASES = {
    "temp_low_f": "temp_low",
    "temp_high_f": "temp_high",
    "vpd_low_kpa": "vpd_low",
    "vpd_high_kpa": "vpd_high",
}


def _climate_intent_waypoint_errors(waypoints: object) -> list[dict[str, object]]:
    if not isinstance(waypoints, list):
        return [{"transition_index": -1, "error": "transitions must be a JSON array"}]
    errors: list[dict[str, object]] = []
    for idx, wp in enumerate(waypoints):
        if not isinstance(wp, dict):
            errors.append({"transition_index": idx, "error": "transition must be an object"})
            continue
        if "climate_intent" not in wp:
            errors.append({"transition_index": idx, "error": "missing climate_intent"})
        elif isinstance(wp["climate_intent"], dict):
            provided = set(wp["climate_intent"])
            missing = sorted(set(CLIMATE_INTENT_FIELDS) - provided)
            if missing:
                errors.append(
                    {
                        "transition_index": idx,
                        "error": "climate_intent must explicitly set every field",
                        "missing_fields": missing,
                    }
                )
        if "params" in wp and wp.get("params") not in ({}, None):
            errors.append({"transition_index": idx, "error": "raw params are not accepted in set_plan"})
    return errors


async def _fetch_active_tier1_params(conn: asyncpg.Connection) -> dict[str, float]:
    rows = await conn.fetch(
        """
        SELECT parameter, value
          FROM v_active_plan
         WHERE parameter = ANY($1::text[])
        """,
        sorted(set(TIER1_REG) | set(BAND_OWNED_REG)),
    )
    params = {str(row["parameter"]): float(row["value"]) for row in rows}
    target_row = await conn.fetchrow(
        """
        WITH latest_climate AS (
          SELECT ts,
                 temp_avg,
                 vpd_avg,
                 dew_point
            FROM climate
           WHERE temp_avg IS NOT NULL
             AND vpd_avg IS NOT NULL
           ORDER BY ts DESC
           LIMIT 1
        ), target AS (
          SELECT ts,
                 temp_avg,
                 vpd_avg,
                 dew_point,
                 fn_setpoint_at('temp_low', ts) AS temp_low_f,
                 fn_setpoint_at('temp_high', ts) AS temp_high_f,
                 fn_setpoint_at('vpd_low', ts) AS vpd_low_kpa,
                 fn_setpoint_at('vpd_high', ts) AS vpd_high_kpa
            FROM latest_climate
        )
        SELECT temp_avg AS temp_actual_f,
               vpd_avg AS vpd_actual_kpa,
               temp_low_f,
               temp_high_f,
               vpd_low_kpa,
               vpd_high_kpa,
               CASE WHEN dew_point IS NULL THEN NULL ELSE temp_avg - dew_point END AS dew_margin_f,
               greatest(0.0, temp_avg - temp_high_f) AS temp_above_high_f,
               greatest(0.0, vpd_avg - vpd_high_kpa) AS vpd_above_high_kpa,
               (temp_avg - ((temp_low_f + temp_high_f) / 2.0)) AS temp_target_delta_f,
               (vpd_avg - ((vpd_low_kpa + vpd_high_kpa) / 2.0)) AS vpd_target_delta_kpa
          FROM target
        """
    )
    if target_row:
        for key, value in dict(target_row).items():
            if value is not None:
                numeric = float(value)
                params[key] = numeric
                if alias := CLIMATE_TARGET_PARAM_ALIASES.get(key):
                    params[alias] = numeric
    return params


def _materialize_climate_intent_waypoints(
    waypoints: list[object],
    active_tier1_params: dict[str, float],
) -> tuple[list[object], list[dict[str, object]]]:
    expanded: list[object] = []
    intent_records: list[dict[str, object]] = []
    for wp in waypoints:
        if not isinstance(wp, dict) or "climate_intent" not in wp:
            expanded.append(wp)
            continue
        intent = ClimateIntent.model_validate(wp["climate_intent"])
        materialized = materialize_climate_intent_tier1(intent, active_tier1_params)
        guardrails = climate_intent_materialization_guardrails(intent, active_tier1_params, materialized)
        expanded_wp = dict(wp)
        expanded_wp["params"] = materialized
        expanded.append(expanded_wp)
        record = {
            "ts": wp.get("ts"),
            "reason": wp.get("reason"),
            "climate_intent": intent.model_dump(mode="json"),
            "materialized_params": materialized,
        }
        if guardrails:
            record["guardrails"] = guardrails
        intent_records.append(record)
    return expanded, intent_records


def _json(obj):
    """JSON serialize with asyncpg/Decimal support."""
    import decimal

    def default(o):
        if isinstance(o, decimal.Decimal):
            return float(o)
        if isinstance(o, datetime | date):
            return o.isoformat()
        return str(o)

    return json.dumps(obj, default=default)


mcp = FastMCP(
    "verdify",
    instructions="""Verdify greenhouse control tools. Use these to monitor climate,
    manage setpoints, run the AI planner, and review performance.
    The greenhouse has temp/VPD bands, misters, fog, fans, heaters, and a vent.
    The planner emits bounded ClimateIntent in set_plan transitions; MCP
    materializes it into registry-approved tunables that shape how the controller responds.
    Band params (temp_low, temp_high, vpd_low, vpd_high) are dispatcher-owned
    read-only context in routine plans. Temp comes from crop policy; house VPD is
    derived from crop + zone policy. Use direct tunable pushes only for explicit overrides.""",
    # Bind explicitly so MCP_HTTP_HOST/PORT env vars actually take effect.
    # FastMCP only auto-reads FASTMCP_-prefixed env vars, so the
    # os.environ.setdefault block in __main__ was dead code. Reading the env
    # here lets a systemd drop-in (Environment=MCP_HTTP_HOST=0.0.0.0) make
    # the server reachable from the hermes-iris Docker container via the
    # docker0 / verdify-internal bridge IP. Default stays 127.0.0.1:8000.
    host=os.environ.get("MCP_HTTP_HOST", "127.0.0.1"),
    port=int(os.environ.get("MCP_HTTP_PORT", "8000")),
)


async def _db() -> asyncpg.Connection:
    return await asyncpg.connect(DB_DSN)


@mcp.tool()
async def slack_ops(
    text: str,
    slack_user_id: str = "iris",
    slack_user_name: str = "Iris",
    role_override: str = "operator",
) -> str:
    """Execute deterministic Iris Slack operations against the #greenhouse command surface.

    Use this for greenhouse status, briefs, alert runbooks/actions, forecast
    triage, guardrail summaries, public-safe ops logs, crop observations/photo
    intake, crop lifecycle/task writes, lesson extraction requests, and planner
    triggers. Direct relay commands are denied by policy.
    """

    settings = load_slack_settings()
    req = SlackCommandRequest(
        text=text,
        slack_user_id=slack_user_id,
        slack_user_name=slack_user_name,
        channel_id=settings.channel_id,
        channel_name=settings.channel_name,
        raw_event={"source": "mcp.slack_ops"},
    )
    response = await handle_slack_command(req, dsn=DB_DSN, settings=settings, role_override=role_override)
    return response.model_dump_json()


async def _insert_plan_delivery_log(conn: asyncpg.Connection, result: dict) -> str | None:
    """Persist or refresh a send_to_iris result from MCP-triggered manual planning."""
    row = {
        "event_type": result["event_type"],
        "event_label": result.get("event_label"),
        "session_key": result.get("session_key"),
        "wake_mode": result.get("wake_mode"),
        "gateway_status": result.get("gateway_status"),
        "gateway_body": result.get("gateway_body"),
        "trigger_id": result.get("trigger_id"),
        "instance": result.get("instance"),
        "hermes_run_id": result.get("hermes_run_id"),
    }
    explicit_status = result.get("status")
    if explicit_status is None and result.get("delivered") is False and result.get("gateway_status") is not None:
        explicit_status = "delivery_failed"
    if explicit_status is not None:
        row["status"] = explicit_status
    PlanDeliveryLogRow.model_validate(row)

    await conn.execute(
        """
        INSERT INTO plan_delivery_log AS pdl
          (event_type, event_label, session_key, wake_mode, gateway_status,
           gateway_body, trigger_id, instance, status, hermes_run_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7::uuid, $8, COALESCE($9, 'pending'), $10)
        ON CONFLICT (trigger_id) DO UPDATE
          SET event_type     = EXCLUDED.event_type,
              event_label    = EXCLUDED.event_label,
              session_key    = COALESCE(EXCLUDED.session_key, pdl.session_key),
              wake_mode      = COALESCE(EXCLUDED.wake_mode, pdl.wake_mode),
              gateway_status = EXCLUDED.gateway_status,
              gateway_body   = COALESCE(EXCLUDED.gateway_body, pdl.gateway_body),
              instance       = COALESCE(EXCLUDED.instance, pdl.instance),
              hermes_run_id  = COALESCE(EXCLUDED.hermes_run_id, pdl.hermes_run_id),
              status         = CASE
                                 WHEN pdl.status IN ('acked', 'plan_written') THEN pdl.status
                                 ELSE EXCLUDED.status
                               END
        """,
        row["event_type"],
        row["event_label"],
        row["session_key"],
        row["wake_mode"],
        row["gateway_status"],
        row["gateway_body"],
        row["trigger_id"],
        row["instance"],
        explicit_status,
        row["hermes_run_id"],
    )
    return explicit_status


# ═══════════════════════════════════════════════════════════════
# MONITORING TOOLS
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
async def climate() -> str:
    """Get current greenhouse climate readings (temp, VPD, RH, dew point, zone sensors, outdoor)."""
    conn = await _db()
    try:
        row = await conn.fetchrow("""
            SELECT round(temp_avg::numeric,1) as temp_f,
                   round(vpd_avg::numeric,2) as vpd_kpa,
                   round(rh_avg::numeric,0) as rh_pct,
                   round(dew_point::numeric,1) as dew_point_f,
                   round((temp_avg - dew_point)::numeric,1) as dp_margin_f,
                   round(vpd_south::numeric,2) as vpd_south,
                   round(vpd_west::numeric,2) as vpd_west,
                   round(vpd_east::numeric,2) as vpd_east,
                   round(outdoor_temp_f::numeric,1) as outdoor_temp,
                   round(outdoor_rh_pct::numeric,0) as outdoor_rh,
                   round(lux::numeric,0) as lux,
                   round(solar_irradiance_w_m2::numeric,0) as solar_w,
                   round(solar_irradiance_w_m2::numeric,0) as solar_w_m2,
                   extract(epoch FROM now() - ts)::int as age_seconds
            FROM climate ORDER BY ts DESC LIMIT 1
        """)
        mode = await conn.fetchval(
            "SELECT value FROM system_state WHERE entity = 'greenhouse_state' ORDER BY ts DESC LIMIT 1"
        )
        # Sprint 23+: validate-on-emit through ClimateSnapshot. Schema drift
        # in the SELECT (e.g. a renamed column) fails here, not silently in
        # Iris's downstream parse.
        snap = ClimateSnapshot.model_validate({**dict(row), "mode": mode})
        return snap.model_dump_json()
    finally:
        await conn.close()


@mcp.tool()
async def scorecard(target_date: str = "") -> str:
    """Get the planner scorecard — 25 KPI metrics for a given day.
    Includes: planner_score, compliance_v2_attributable_pct (the current scored
    compliance field), dev_temp_norm_median_day/night + dev_temp_norm_p95
    (+ dev_vpd_*) as target-reference diagnostics only, compliance_v2_raw_pct,
    compliance_v2_unachievable_frac, the legacy binary compliance_pct /
    temp_compliance_pct / vpd_compliance_pct, stress hours (heat/cold/vpd_high/
    vpd_low), utility usage (kwh, therms, water_gal, mister_water_gal), costs
    (electric/gas/water/total), dew point safety, and 7-day averages. Pass date
    as YYYY-MM-DD or omit for today.

    Compliance is GRADED + PER-ZONE + CONTROLLER-ATTRIBUTABLE (band-compliance
    rearchitecture, migrations 146-147), and the scored compliance number is
    compliance_v2_attributable_pct. ADR-0004 supersedes target hugging: use this
    as a corridor/outcome guard, not as permission to spend water/energy/wear
    reducing target-reference deviation while readings are already inside the
    crop corridor. GRADED severity still distinguishes small edge misses from large
    stress. PER-ZONE: each zone graded against what is actually planted there
    (center = Vanda orchid; east = lettuce/strawberry/pepper), aggregated to a house
    number (center weight 0.60, east 0.40). CONTROLLER-ATTRIBUTABLE: every miss is
    split into controller-error (a cooling/heating stage was idle with authority
    available) vs physically-unachievable (e.g. vent saturated and outdoor >= the
    served high edge — an exhaust-only box cannot beat ambient); the unachievable
    misses are credited back so weather Iris cannot change is not scored against her.
    compliance_v2_raw_pct and compliance_v2_unachievable_frac are reported context — a
    high unachievable_frac should cue WIDENING the served envelope, not working the
    actuators harder.

    The scorecard still reads compliance_v2_attributable_pct per day and falls back
    to the legacy binary compliance_pct (% of readings with BOTH temp and VPD in the
    served band) only for days before the graded column was populated. Treat binary
    compliance_pct as transitional/diagnostic context only.

    Response is validated through verdify_schemas.ScorecardResponse — partial
    days emit a subset of metrics as null. DB drift (new metric) surfaces as a
    validation error with the raw values preserved so Iris can still read the card."""
    conn = await _db()
    try:
        if target_date:
            d = datetime.strptime(target_date, "%Y-%m-%d").date()
        else:
            d = await conn.fetchval("SELECT (now() AT TIME ZONE 'America/Denver')::date")
        rows = await conn.fetch("SELECT * FROM fn_planner_scorecard($1::date)", d)
        try:
            sc = ScorecardResponse.from_metric_rows(rows)
        except ValidationError as e:
            return _json(
                {
                    "error": "ScorecardResponse validation failed — DB may have new metrics",
                    "details": json.loads(e.json()),
                    "raw": {r["metric"]: (float(r["value"]) if r["value"] is not None else None) for r in rows},
                }
            )
        return sc.model_dump_json(by_alias=True)
    finally:
        await conn.close()


@mcp.tool()
async def outcome_kpi(target_date: str = "") -> str:
    """Get ADR-0004 outcome KPIs for a day.

    This is the read-only outcome surface for floating-corridor control: served
    corridor compliance, VPD misses, transition-derived actuator cycles/runtime,
    dew margin, water
    use, DLI, moisture-estimator decisions, fog/dehum ping-pong sequences,
    heat-dehum episodes, and per-action effectiveness from the daily climate-
    action scorecard. ADR-0004 means these are outcome and resource guardrails,
    not an instruction to target-hug while the crop is already inside the
    served corridor.

    Pinched corridor compliance, DIF, and solar-phase buckets are computed on
    demand from one-minute climate samples plus the active setpoint/readback
    state. Moisture-estimator buckets are computed from
    climate_action_log.source_system_state->climate_moisture_exchange when the
    OTA/deploy path has produced rows; #327 makes that context first-class:
    per-(action, reason) buckets now include the #410 held-temp fields
    (vent_held_vpd_gain_kpa, hold_required), the selected/expected VPD gain,
    and outdoor age, and vpd_policy carries episode counters by estimator
    reason (episodes_by_mx_reason; pre-#385 rows bucket as estimator_absent).
    Migration 187's v_moisture_estimator_telemetry view is the equivalent
    typed SQL surface.  Raw equipment transitions, not firmware daily counters,
    are the cycle/runtime authority; the counters remain diagnostic context.
    Realized solar-night dry-out episodes distinguish effective, ineffective,
    blocked, and insufficient-evidence outcomes. Pass date as YYYY-MM-DD or
    omit for today."""
    greenhouse_id = "vallery"
    conn = await _db()
    try:
        if target_date:
            try:
                d = datetime.strptime(target_date, "%Y-%m-%d").date()
            except ValueError:
                return _json({"error": "target_date must be YYYY-MM-DD"})
        else:
            d = await conn.fetchval("SELECT (now() AT TIME ZONE 'America/Denver')::date")

        summary_row = await conn.fetchrow(
            """
            SELECT date, greenhouse_id,
                   compliance_v2_attributable_pct,
                   compliance_v2_raw_pct,
                   compliance_v2_unachievable_frac,
                   compliance_pct,
                   temp_compliance_pct,
                   vpd_compliance_pct,
                   graded_temp_compliance_pct,
                   graded_vpd_compliance_pct,
                   feasibility_unknown_min,
                   stress_hours_vpd_high,
                   stress_hours_vpd_low,
                   graded_stress_hours_vpd_high,
                   graded_stress_hours_vpd_low,
                   cycles_fan1,
                   cycles_fan2,
                   cycles_heat1,
                   cycles_heat2,
                   cycles_fog,
                   cycles_vent,
                   cycles_dehum,
                   cycles_safety_dehum,
                   cycles_mister_south,
                   cycles_mister_west,
                   cycles_mister_center,
                   cycles_grow_light,
                   runtime_fan1_min,
                   runtime_fan2_min,
                   runtime_heat1_min,
                   runtime_heat2_min,
                   runtime_fog_min,
                   runtime_vent_min,
                   runtime_mister_south_h,
                   runtime_mister_west_h,
                   runtime_mister_center_h,
                   runtime_grow_light_min,
                   water_used_gal,
                   mister_water_gal,
                   irrigation_water_gal,
                   fertigation_water_gal,
                   dli_final,
                   min_dp_margin_f,
                   dp_risk_hours,
                   kwh_estimated,
                   therms_estimated,
                   cost_electric,
                   cost_gas,
                   cost_water,
                   cost_total
            FROM daily_summary
            WHERE date = $1::date AND greenhouse_id = $2
            """,
            d,
            greenhouse_id,
        )
        summary = dict(summary_row) if summary_row else {}

        water_resource_row = await conn.fetchrow(
            """
            SELECT *
            FROM v_water_attribution_daily
            WHERE date = $1::date AND greenhouse_id = $2
            """,
            d,
            greenhouse_id,
        )
        water_resource = dict(water_resource_row) if water_resource_row else None

        energy_resource_row = await conn.fetchrow(
            """
            SELECT *
            FROM v_energy_estimate_reconciliation
            WHERE date = $1::date AND greenhouse_id = $2
            """,
            d,
            greenhouse_id,
        )
        energy_resource = dict(energy_resource_row) if energy_resource_row else None
        if energy_resource and isinstance(energy_resource.get("coefficient_revisions"), str):
            energy_resource["coefficient_revisions"] = json.loads(
                energy_resource["coefficient_revisions"]
            )

        cycle_rows = await conn.fetch(
            """
            SELECT equipment,
                   on_minutes::double precision AS on_minutes,
                   starts,
                   cycles_under_1m,
                   cycles_1m_to_5m,
                   short_cycles_under_5m,
                   cycles_5m_to_15m,
                   cycles_15m_plus,
                   open_pulses_at_cutoff,
                   peak_transitions_per_hour,
                   is_complete_day,
                   start_state_known,
                   open_at_end,
                   is_deploy_gate_eligible,
                   quality,
                   quality_flags,
                   raw_event_rows,
                   normalized_transition_count,
                   same_timestamp_duplicate_rows,
                   redundant_state_rows,
                   conflicting_timestamp_count
            FROM v_equipment_runtime_daily
            WHERE day = $1::date AND greenhouse_id = $2
            ORDER BY equipment
            """,
            d,
            greenhouse_id,
        )

        dryout_rows = await conn.fetch(
            """
            SELECT *
            FROM fn_realized_solar_night_dryout($1::date, $1::date, $2)
            ORDER BY episode_started_at
            """,
            d,
            greenhouse_id,
        )

        action_rows = await conn.fetch(
            """
            SELECT climate_action,
                   decisions,
                   avg_abs_temp_error_before_f,
                   avg_abs_vpd_error_before_kpa,
                   avg_temp_abs_error_delta_15m_f,
                   avg_vpd_abs_error_delta_15m_kpa,
                   avg_wet_relay_duty_pct,
                   avg_vent_fan_duty_pct,
                   mister_water_delta_gal,
                   wet_blocked_decisions,
                   fog_blocked_decisions
            FROM v_climate_action_daily_scorecard
            WHERE date = $1::date AND greenhouse_id = $2
            ORDER BY decisions DESC, climate_action
            """,
            d,
            greenhouse_id,
        )

        pinched_row = await conn.fetchrow(
            """
            WITH bounds AS (
                SELECT
                    ($1::date::timestamp AT TIME ZONE 'America/Denver') AS start_ts,
                    (($1::date + 1)::timestamp AT TIME ZONE 'America/Denver') AS end_ts
            ),
            samples AS (
                SELECT
                    time_bucket('1 minute', c.ts) AS bucket,
                    avg(c.temp_avg)::double precision AS temp_avg,
                    avg(c.vpd_avg)::double precision AS vpd_avg,
                    avg(c.house_temp_target_f)::double precision AS temp_target_f,
                    avg(c.house_vpd_target)::double precision AS vpd_target_kpa,
                    avg(c.solar_phase)::double precision AS solar_phase
                FROM climate c
                CROSS JOIN bounds b
                WHERE c.greenhouse_id = $2
                  AND c.ts >= b.start_ts
                  AND c.ts < b.end_ts
                  AND c.temp_avg IS NOT NULL
                  AND c.vpd_avg IS NOT NULL
                GROUP BY 1
            ),
            resolved AS (
                SELECT
                    s.*,
                    COALESCE(temp_low.value, band.temp_low) AS temp_low_f,
                    COALESCE(temp_high.value, band.temp_high) AS temp_high_f,
                    COALESCE(vpd_low.value, house.house_vpd_low) AS vpd_low_kpa,
                    COALESCE(vpd_high.value, house.house_vpd_high) AS vpd_high_kpa,
                    GREATEST(
                        0.0,
                        LEAST(
                            1.0,
                            COALESCE(btf_readback.value, btf_change.value, 0.0)
                        )
                    ) AS band_track_fraction,
                    btf_readback.value IS NOT NULL AS has_fraction_readback
                FROM samples s
                CROSS JOIN LATERAL fn_band_setpoints(s.bucket) AS band
                CROSS JOIN LATERAL fn_house_vpd_control_band(s.bucket) AS house
                LEFT JOIN LATERAL (
                    SELECT value FROM setpoint_snapshot
                    WHERE greenhouse_id = $2 AND parameter = 'temp_low' AND ts <= s.bucket
                    ORDER BY ts DESC LIMIT 1
                ) temp_low ON true
                LEFT JOIN LATERAL (
                    SELECT value FROM setpoint_snapshot
                    WHERE greenhouse_id = $2 AND parameter = 'temp_high' AND ts <= s.bucket
                    ORDER BY ts DESC LIMIT 1
                ) temp_high ON true
                LEFT JOIN LATERAL (
                    SELECT value FROM setpoint_snapshot
                    WHERE greenhouse_id = $2 AND parameter = 'vpd_low' AND ts <= s.bucket
                    ORDER BY ts DESC LIMIT 1
                ) vpd_low ON true
                LEFT JOIN LATERAL (
                    SELECT value FROM setpoint_snapshot
                    WHERE greenhouse_id = $2 AND parameter = 'vpd_high' AND ts <= s.bucket
                    ORDER BY ts DESC LIMIT 1
                ) vpd_high ON true
                LEFT JOIN LATERAL (
                    SELECT value FROM setpoint_snapshot
                    WHERE greenhouse_id = $2
                      AND parameter = 'band_track_fraction'
                      AND ts <= s.bucket
                    ORDER BY ts DESC LIMIT 1
                ) btf_readback ON true
                LEFT JOIN LATERAL (
                    SELECT value FROM setpoint_changes
                    WHERE greenhouse_id = $2
                      AND parameter = 'band_track_fraction'
                      AND ts <= s.bucket
                      AND (expired_at IS NULL OR expired_at > s.bucket)
                    ORDER BY ts DESC LIMIT 1
                ) btf_change ON true
            ),
            eligible AS (
                SELECT
                    *,
                    temp_low_f + band_track_fraction
                        * (LEAST(GREATEST(temp_target_f, temp_low_f), temp_high_f) - temp_low_f)
                        AS pinched_temp_low_f,
                    temp_high_f - band_track_fraction
                        * (temp_high_f - LEAST(GREATEST(temp_target_f, temp_low_f), temp_high_f))
                        AS pinched_temp_high_f,
                    vpd_low_kpa + band_track_fraction
                        * (LEAST(GREATEST(vpd_target_kpa, vpd_low_kpa), vpd_high_kpa) - vpd_low_kpa)
                        AS pinched_vpd_low_kpa,
                    vpd_high_kpa - band_track_fraction
                        * (vpd_high_kpa - LEAST(GREATEST(vpd_target_kpa, vpd_low_kpa), vpd_high_kpa))
                        AS pinched_vpd_high_kpa
                FROM resolved
                WHERE temp_target_f IS NOT NULL
                  AND vpd_target_kpa IS NOT NULL
                  AND temp_low_f IS NOT NULL
                  AND temp_high_f IS NOT NULL
                  AND vpd_low_kpa IS NOT NULL
                  AND vpd_high_kpa IS NOT NULL
            ),
            scored AS (
                SELECT
                    *,
                    GREATEST(pinched_temp_low_f - temp_avg, temp_avg - pinched_temp_high_f, 0.0)
                        AS temp_pinched_miss_f,
                    GREATEST(pinched_vpd_low_kpa - vpd_avg, vpd_avg - pinched_vpd_high_kpa, 0.0)
                        AS vpd_pinched_miss_kpa,
                    temp_avg BETWEEN pinched_temp_low_f AND pinched_temp_high_f AS temp_pinched_in_band,
                    vpd_avg BETWEEN pinched_vpd_low_kpa AND pinched_vpd_high_kpa AS vpd_pinched_in_band
                FROM eligible
            )
            SELECT
                count(*)::int AS sample_min,
                count(*) FILTER (WHERE has_fraction_readback)::int AS samples_with_fraction_readback,
                round(avg(band_track_fraction)::numeric, 3)::double precision
                    AS avg_band_track_fraction,
                round(min(band_track_fraction)::numeric, 3)::double precision
                    AS min_band_track_fraction,
                round(max(band_track_fraction)::numeric, 3)::double precision
                    AS max_band_track_fraction,
                round((100.0 * count(*) FILTER (WHERE temp_pinched_in_band)
                    / NULLIF(count(*), 0))::numeric, 2)::double precision AS temp_pct,
                round((100.0 * count(*) FILTER (WHERE vpd_pinched_in_band)
                    / NULLIF(count(*), 0))::numeric, 2)::double precision AS vpd_pct,
                round((100.0 * count(*) FILTER (WHERE temp_pinched_in_band AND vpd_pinched_in_band)
                    / NULLIF(count(*), 0))::numeric, 2)::double precision AS both_pct,
                round((100.0 * count(*) FILTER (
                        WHERE solar_phase < 2.0 AND temp_pinched_in_band AND vpd_pinched_in_band
                    ) / NULLIF(count(*) FILTER (WHERE solar_phase < 2.0), 0))::numeric, 2)::double precision
                    AS day_pct,
                round((100.0 * count(*) FILTER (
                        WHERE solar_phase >= 2.0 AND temp_pinched_in_band AND vpd_pinched_in_band
                    ) / NULLIF(count(*) FILTER (WHERE solar_phase >= 2.0), 0))::numeric, 2)::double precision
                    AS night_pct,
                round((percentile_cont(0.95) WITHIN GROUP (
                    ORDER BY temp_pinched_miss_f
                ))::numeric, 2)::double precision AS p95_temp_miss_f,
                round((percentile_cont(0.95) WITHIN GROUP (
                    ORDER BY vpd_pinched_miss_kpa
                ))::numeric, 3)::double precision AS p95_vpd_miss_kpa
            FROM scored
            """,
            d,
            greenhouse_id,
        )
        pinched = dict(pinched_row) if pinched_row else {}

        dif_row = await conn.fetchrow(
            """
            WITH bounds AS (
                SELECT
                    ($1::date::timestamp AT TIME ZONE 'America/Denver') AS start_ts,
                    (($1::date + 1)::timestamp AT TIME ZONE 'America/Denver') AS end_ts
            ),
            samples AS (
                SELECT
                    time_bucket('1 minute', c.ts) AS bucket,
                    avg(c.temp_avg)::double precision AS temp_avg,
                    avg(c.solar_phase)::double precision AS solar_phase
                FROM climate c
                CROSS JOIN bounds b
                WHERE c.greenhouse_id = $2
                  AND c.ts >= b.start_ts
                  AND c.ts < b.end_ts
                  AND c.temp_avg IS NOT NULL
                  AND c.solar_phase IS NOT NULL
                GROUP BY 1
            )
            SELECT
                count(*) FILTER (WHERE solar_phase < 2.0)::int AS day_sample_min,
                count(*) FILTER (WHERE solar_phase >= 2.0)::int AS night_sample_min,
                round((avg(temp_avg) FILTER (WHERE solar_phase < 2.0))::numeric, 2)::double precision
                    AS day_temp_avg_f,
                round((avg(temp_avg) FILTER (WHERE solar_phase >= 2.0))::numeric, 2)::double precision
                    AS night_temp_avg_f,
                round((
                    avg(temp_avg) FILTER (WHERE solar_phase < 2.0)
                    - avg(temp_avg) FILTER (WHERE solar_phase >= 2.0)
                )::numeric, 2)::double precision AS day_night_temp_delta_f
            FROM samples
            """,
            d,
            greenhouse_id,
        )
        dif = dict(dif_row) if dif_row else {}

        phase_rows = await conn.fetch(
            """
            WITH bounds AS (
                SELECT
                    ($1::date::timestamp AT TIME ZONE 'America/Denver') AS start_ts,
                    (($1::date + 1)::timestamp AT TIME ZONE 'America/Denver') AS end_ts
            ),
            samples AS (
                SELECT
                    time_bucket('1 minute', c.ts) AS bucket,
                    avg(c.temp_avg)::double precision AS temp_avg,
                    avg(c.vpd_avg)::double precision AS vpd_avg,
                    avg(c.dew_point)::double precision AS dew_point,
                    avg(c.solar_irradiance_w_m2)::double precision AS solar_w_m2,
                    avg(c.solar_phase)::double precision AS solar_phase,
                    avg(c.house_temp_target_f)::double precision AS temp_target_f,
                    avg(c.house_vpd_target)::double precision AS vpd_target_kpa
                FROM climate c
                CROSS JOIN bounds b
                WHERE c.greenhouse_id = $2
                  AND c.ts >= b.start_ts
                  AND c.ts < b.end_ts
                  AND c.temp_avg IS NOT NULL
                  AND c.vpd_avg IS NOT NULL
                  AND c.solar_phase IS NOT NULL
                GROUP BY 1
            ),
            resolved AS (
                SELECT
                    s.*,
                    CASE
                        WHEN s.solar_phase < 1.0 THEN 'sunrise_to_noon'
                        WHEN s.solar_phase < 2.0 THEN 'noon_to_sunset'
                        WHEN s.solar_phase < 3.0 THEN 'sunset_to_midnight'
                        ELSE 'midnight_to_sunrise'
                    END AS phase_bucket,
                    CASE
                        WHEN s.solar_phase < 1.0 THEN 0
                        WHEN s.solar_phase < 2.0 THEN 1
                        WHEN s.solar_phase < 3.0 THEN 2
                        ELSE 3
                    END AS phase_order,
                    COALESCE(temp_low.value, band.temp_low) AS temp_low_f,
                    COALESCE(temp_high.value, band.temp_high) AS temp_high_f,
                    COALESCE(vpd_low.value, house.house_vpd_low) AS vpd_low_kpa,
                    COALESCE(vpd_high.value, house.house_vpd_high) AS vpd_high_kpa,
                    GREATEST(
                        0.0,
                        LEAST(
                            1.0,
                            COALESCE(btf_readback.value, btf_change.value, 0.0)
                        )
                    ) AS band_track_fraction
                FROM samples s
                CROSS JOIN LATERAL fn_band_setpoints(s.bucket) AS band
                CROSS JOIN LATERAL fn_house_vpd_control_band(s.bucket) AS house
                LEFT JOIN LATERAL (
                    SELECT value FROM setpoint_snapshot
                    WHERE greenhouse_id = $2 AND parameter = 'temp_low' AND ts <= s.bucket
                    ORDER BY ts DESC LIMIT 1
                ) temp_low ON true
                LEFT JOIN LATERAL (
                    SELECT value FROM setpoint_snapshot
                    WHERE greenhouse_id = $2 AND parameter = 'temp_high' AND ts <= s.bucket
                    ORDER BY ts DESC LIMIT 1
                ) temp_high ON true
                LEFT JOIN LATERAL (
                    SELECT value FROM setpoint_snapshot
                    WHERE greenhouse_id = $2 AND parameter = 'vpd_low' AND ts <= s.bucket
                    ORDER BY ts DESC LIMIT 1
                ) vpd_low ON true
                LEFT JOIN LATERAL (
                    SELECT value FROM setpoint_snapshot
                    WHERE greenhouse_id = $2 AND parameter = 'vpd_high' AND ts <= s.bucket
                    ORDER BY ts DESC LIMIT 1
                ) vpd_high ON true
                LEFT JOIN LATERAL (
                    SELECT value FROM setpoint_snapshot
                    WHERE greenhouse_id = $2
                      AND parameter = 'band_track_fraction'
                      AND ts <= s.bucket
                    ORDER BY ts DESC LIMIT 1
                ) btf_readback ON true
                LEFT JOIN LATERAL (
                    SELECT value FROM setpoint_changes
                    WHERE greenhouse_id = $2
                      AND parameter = 'band_track_fraction'
                      AND ts <= s.bucket
                      AND (expired_at IS NULL OR expired_at > s.bucket)
                    ORDER BY ts DESC LIMIT 1
                ) btf_change ON true
            ),
            scored AS (
                SELECT
                    *,
                    temp_low_f + band_track_fraction
                        * (LEAST(GREATEST(temp_target_f, temp_low_f), temp_high_f) - temp_low_f)
                        AS pinched_temp_low_f,
                    temp_high_f - band_track_fraction
                        * (temp_high_f - LEAST(GREATEST(temp_target_f, temp_low_f), temp_high_f))
                        AS pinched_temp_high_f,
                    vpd_low_kpa + band_track_fraction
                        * (LEAST(GREATEST(vpd_target_kpa, vpd_low_kpa), vpd_high_kpa) - vpd_low_kpa)
                        AS pinched_vpd_low_kpa,
                    vpd_high_kpa - band_track_fraction
                        * (vpd_high_kpa - LEAST(GREATEST(vpd_target_kpa, vpd_low_kpa), vpd_high_kpa))
                        AS pinched_vpd_high_kpa
                FROM resolved
                WHERE temp_target_f IS NOT NULL
                  AND vpd_target_kpa IS NOT NULL
                  AND temp_low_f IS NOT NULL
                  AND temp_high_f IS NOT NULL
                  AND vpd_low_kpa IS NOT NULL
                  AND vpd_high_kpa IS NOT NULL
            )
            SELECT
                phase_bucket,
                count(*)::int AS sample_min,
                round(min(solar_phase)::numeric, 2)::double precision AS phase_min,
                round(max(solar_phase)::numeric, 2)::double precision AS phase_max,
                round(avg(temp_avg)::numeric, 2)::double precision AS temp_avg_f,
                round(avg(vpd_avg)::numeric, 3)::double precision AS vpd_avg_kpa,
                round(min(temp_avg - dew_point)::numeric, 2)::double precision AS min_dp_margin_f,
                round(avg(solar_w_m2)::numeric, 1)::double precision AS avg_solar_w_m2,
                round((100.0 * count(*) FILTER (
                        WHERE temp_avg BETWEEN temp_low_f AND temp_high_f
                          AND vpd_avg BETWEEN vpd_low_kpa AND vpd_high_kpa
                    ) / NULLIF(count(*), 0))::numeric, 2)::double precision AS served_both_pct,
                round((100.0 * count(*) FILTER (
                        WHERE temp_avg BETWEEN pinched_temp_low_f AND pinched_temp_high_f
                          AND vpd_avg BETWEEN pinched_vpd_low_kpa AND pinched_vpd_high_kpa
                    ) / NULLIF(count(*), 0))::numeric, 2)::double precision AS pinched_both_pct,
                round((100.0 * count(*) FILTER (
                        WHERE vpd_avg > vpd_high_kpa
                    ) / NULLIF(count(*), 0))::numeric, 2)::double precision AS vpd_high_miss_pct,
                round((100.0 * count(*) FILTER (
                        WHERE vpd_avg < vpd_low_kpa
                    ) / NULLIF(count(*), 0))::numeric, 2)::double precision AS vpd_low_miss_pct
            FROM scored
            GROUP BY phase_order, phase_bucket
            ORDER BY phase_order
            """,
            d,
            greenhouse_id,
        )

        moisture_rows = await conn.fetch(
            """
            WITH bounds AS (
                SELECT
                    ($1::date::timestamp AT TIME ZONE 'America/Denver') AS start_ts,
                    (($1::date + 1)::timestamp AT TIME ZONE 'America/Denver') AS end_ts
            ),
            estimator AS (
                SELECT
                    source_system_state -> 'climate_moisture_exchange' AS mx
                FROM climate_action_log
                CROSS JOIN bounds b
                WHERE greenhouse_id = $2
                  AND ts >= b.start_ts
                  AND ts < b.end_ts
                  AND jsonb_typeof(source_system_state -> 'climate_moisture_exchange') = 'object'
            ),
            parsed AS (
                SELECT
                    COALESCE(NULLIF(mx ->> 'action', ''), 'unknown') AS action,
                    COALESCE(NULLIF(mx ->> 'reason', ''), 'unknown') AS reason,
                    NULLIF(mx ->> 'vent_vpd_gain_kpa', '')::double precision
                        AS vent_vpd_gain_kpa,
                    NULLIF(mx ->> 'heat_vpd_gain_kpa', '')::double precision
                        AS heat_vpd_gain_kpa,
                    -- #327/#410 fields (settled names; NULL from pre-#410
                    -- emitters). typeof-guarded like migration 187's
                    -- v_moisture_estimator_telemetry so raw/odd payloads
                    -- degrade to NULL instead of erroring.
                    CASE WHEN jsonb_typeof(mx -> 'vent_held_vpd_gain_kpa') = 'number'
                        THEN (mx ->> 'vent_held_vpd_gain_kpa')::double precision
                    END AS vent_held_vpd_gain_kpa,
                    CASE WHEN mx ->> 'hold_required' IN ('true', 'false')
                        THEN (mx ->> 'hold_required')::boolean
                    END AS hold_required,
                    CASE WHEN jsonb_typeof(mx -> 'expected_vpd_gain_kpa') = 'number'
                        THEN (mx ->> 'expected_vpd_gain_kpa')::double precision
                    END AS expected_vpd_gain_kpa_raw,
                    COALESCE(
                        CASE WHEN jsonb_typeof(mx -> 'outdoor_age_s') = 'number'
                            THEN (mx ->> 'outdoor_age_s')::double precision
                        END,
                        CASE WHEN jsonb_typeof(mx -> 'outdoor_data_age_s') = 'number'
                            THEN (mx ->> 'outdoor_data_age_s')::double precision
                        END
                    ) AS outdoor_age_s,
                    CASE WHEN mx ->> 'outdoor_fresh' IN ('true', 'false')
                        THEN (mx ->> 'outdoor_fresh')::boolean
                    END AS outdoor_fresh,
                    CASE WHEN mx ->> 'vent_overcools' IN ('true', 'false')
                        THEN (mx ->> 'vent_overcools')::boolean
                    END AS vent_overcools,
                    CASE WHEN mx ->> 'heat_assist_corun' IN ('true', 'false')
                        THEN (mx ->> 'heat_assist_corun')::boolean
                    END AS heat_assist_corun,
                    CASE WHEN mx ->> 'heat_assist_active' IN ('true', 'false')
                        THEN (mx ->> 'heat_assist_active')::boolean
                    END AS heat_assist_active,
                    NULLIF(mx ->> 'heat_assist_timer_s', '')::double precision
                        AS heat_assist_timer_s
                FROM estimator
            ),
            enriched AS (
                -- Selected/expected gain: explicit emitter value if present,
                -- else the selected path's own projection (held-temp gain for
                -- the #410 vent_plus_heat_hold / hold_required co-run).
                SELECT parsed.*,
                    COALESCE(
                        expected_vpd_gain_kpa_raw,
                        CASE
                            WHEN reason = 'vent_plus_heat_hold'
                                 OR COALESCE(hold_required, false)
                                THEN COALESCE(vent_held_vpd_gain_kpa, vent_vpd_gain_kpa)
                            WHEN action IN ('vent_dehum', 'vent_humidify')
                                THEN vent_vpd_gain_kpa
                            WHEN action = 'heat_assist' THEN heat_vpd_gain_kpa
                        END
                    ) AS expected_vpd_gain_kpa
                FROM parsed
            )
            SELECT
                action,
                reason,
                count(*)::int AS decisions,
                round(avg(vent_vpd_gain_kpa)::numeric, 3)::double precision
                    AS avg_vent_vpd_gain_kpa,
                round(avg(heat_vpd_gain_kpa)::numeric, 3)::double precision
                    AS avg_heat_vpd_gain_kpa,
                round(avg(vent_held_vpd_gain_kpa)::numeric, 3)::double precision
                    AS avg_vent_held_vpd_gain_kpa,
                round(avg(expected_vpd_gain_kpa)::numeric, 3)::double precision
                    AS avg_expected_vpd_gain_kpa,
                count(*) FILTER (WHERE hold_required)::int AS hold_required_decisions,
                count(*) FILTER (WHERE outdoor_fresh)::int AS outdoor_fresh_decisions,
                round(avg(outdoor_age_s)::numeric, 0)::double precision
                    AS avg_outdoor_age_s,
                count(*) FILTER (WHERE vent_overcools)::int AS vent_overcool_decisions,
                count(*) FILTER (WHERE heat_assist_corun)::int AS heat_assist_corun_decisions,
                count(*) FILTER (WHERE heat_assist_active)::int AS heat_assist_active_decisions,
                round(avg(heat_assist_timer_s)::numeric, 0)::double precision
                    AS avg_heat_assist_timer_s
            FROM enriched
            GROUP BY action, reason
            ORDER BY decisions DESC, action, reason
            """,
            d,
            greenhouse_id,
        )

        vpd_policy_row = await conn.fetchrow(
            """
            WITH bounds AS (
                SELECT
                    ($1::date::timestamp AT TIME ZONE 'America/Denver') AS start_ts,
                    (($1::date + 1)::timestamp AT TIME ZONE 'America/Denver') AS end_ts
            ),
            ordered AS (
                SELECT
                    l.ts,
                    l.climate_action,
                    l.priority_axis,
                    l.candidate_summary,
                    l.source_system_state,
                    lag(l.climate_action) OVER (ORDER BY l.ts) AS prev_action
                FROM climate_action_log l
                CROSS JOIN bounds b
                WHERE l.greenhouse_id = $2
                  AND l.ts >= b.start_ts
                  AND l.ts < b.end_ts
            ),
            tagged AS (
                SELECT
                    *,
                    sum(CASE WHEN prev_action IS DISTINCT FROM climate_action THEN 1 ELSE 0 END)
                        OVER (ORDER BY ts ROWS UNBOUNDED PRECEDING) AS episode_id
                FROM ordered
            ),
            episodes AS (
                SELECT
                    episode_id,
                    min(ts) AS started_at,
                    max(ts) AS ended_at,
                    climate_action,
                    count(*)::int AS sample_count,
                    bool_or(priority_axis = 'vpd') AS vpd_priority,
                    bool_or(COALESCE(candidate_summary, '') ILIKE '%heat-assist dehum%')
                        AS heat_dehum_summary,
                    bool_or(
                        source_system_state -> 'climate_moisture_exchange' ->> 'action'
                            = 'heat_assist'
                    ) AS mx_heat_assist,
                    bool_or(
                        source_system_state -> 'climate_moisture_exchange' ->> 'heat_assist_active'
                            = 'true'
                    ) AS mx_heat_assist_active
                FROM tagged
                GROUP BY episode_id, climate_action
            ),
            sequenced AS (
                SELECT
                    *,
                    lag(climate_action) OVER (ORDER BY started_at) AS prev_episode_action,
                    lag(ended_at) OVER (ORDER BY started_at) AS prev_episode_ended_at
                FROM episodes
            ),
            classified AS (
                SELECT
                    *,
                    climate_action IN (
                        'SEALED_HUMIDIFY',
                        'SEALED_FOG',
                        'VENT_COOL_MIST_ASSIST',
                        'VENT_COOL_FOG_ASSIST'
                    ) AS is_wetting,
                    climate_action = 'DEHUM_VENT' AS is_dehum,
                    climate_action = 'HEAT'
                        AND (vpd_priority OR heat_dehum_summary OR mx_heat_assist OR mx_heat_assist_active)
                        AS is_heat_dehum,
                    started_at - prev_episode_ended_at AS prev_gap
                FROM sequenced
            )
            SELECT
                count(*)::int AS total_episodes,
                COALESCE(sum(sample_count), 0)::int AS total_samples,
                count(*) FILTER (WHERE is_wetting)::int AS wetting_episodes,
                count(*) FILTER (WHERE is_dehum)::int AS vent_dehum_episodes,
                count(*) FILTER (WHERE is_heat_dehum)::int AS heat_dehum_episodes,
                count(*) FILTER (
                    WHERE is_dehum
                      AND prev_episode_action IN (
                          'SEALED_HUMIDIFY',
                          'SEALED_FOG',
                          'VENT_COOL_MIST_ASSIST',
                          'VENT_COOL_FOG_ASSIST'
                      )
                      AND prev_gap <= interval '30 minutes'
                )::int AS wet_to_dehum_episodes_30m,
                count(*) FILTER (
                    WHERE is_wetting
                      AND prev_episode_action = 'DEHUM_VENT'
                      AND prev_gap <= interval '30 minutes'
                )::int AS dehum_to_wet_episodes_30m,
                round((
                    avg(extract(epoch FROM prev_gap) / 60.0) FILTER (
                        WHERE is_dehum
                          AND prev_episode_action IN (
                              'SEALED_HUMIDIFY',
                              'SEALED_FOG',
                              'VENT_COOL_MIST_ASSIST',
                              'VENT_COOL_FOG_ASSIST'
                          )
                          AND prev_gap <= interval '30 minutes'
                    )
                )::numeric, 1)::double precision AS avg_wet_to_dehum_gap_min,
                round((
                    avg(extract(epoch FROM prev_gap) / 60.0) FILTER (
                        WHERE is_wetting
                          AND prev_episode_action = 'DEHUM_VENT'
                          AND prev_gap <= interval '30 minutes'
                    )
                )::numeric, 1)::double precision AS avg_dehum_to_wet_gap_min
            FROM classified
            """,
            d,
            greenhouse_id,
        )

        # #327: VPD-policy episode counters by estimator reason (mx_reason).
        # One row per modal estimator reason across the day's action episodes;
        # pre-#385 rows (no parsed object) land in 'estimator_absent' so the
        # counters stay meaningful across the fw 995c9b3 -> #385 -> #410
        # rollout. This is the #371 grading surface for "did the cycle take
        # vent_plus_heat_hold or heat_assist?".
        vpd_policy_reason_rows = await conn.fetch(
            """
            WITH bounds AS (
                SELECT
                    ($1::date::timestamp AT TIME ZONE 'America/Denver') AS start_ts,
                    (($1::date + 1)::timestamp AT TIME ZONE 'America/Denver') AS end_ts
            ),
            ordered AS (
                SELECT
                    l.ts,
                    l.climate_action,
                    l.priority_axis,
                    lag(l.climate_action) OVER (ORDER BY l.ts) AS prev_action,
                    CASE WHEN jsonb_typeof(
                             l.source_system_state -> 'climate_moisture_exchange'
                         ) = 'object'
                        THEN NULLIF(
                            l.source_system_state -> 'climate_moisture_exchange' ->> 'reason',
                            ''
                        )
                    END AS mx_reason
                FROM climate_action_log l
                CROSS JOIN bounds b
                WHERE l.greenhouse_id = $2
                  AND l.ts >= b.start_ts
                  AND l.ts < b.end_ts
            ),
            tagged AS (
                SELECT
                    *,
                    sum(CASE WHEN prev_action IS DISTINCT FROM climate_action THEN 1 ELSE 0 END)
                        OVER (ORDER BY ts ROWS UNBOUNDED PRECEDING) AS episode_id
                FROM ordered
            ),
            episodes AS (
                SELECT
                    episode_id,
                    climate_action,
                    count(*)::int AS sample_count,
                    bool_or(priority_axis = 'vpd') AS vpd_priority,
                    COALESCE(
                        mode() WITHIN GROUP (ORDER BY mx_reason),
                        'estimator_absent'
                    ) AS mx_reason
                FROM tagged
                GROUP BY episode_id, climate_action
            )
            SELECT
                mx_reason,
                count(*)::int AS episodes,
                COALESCE(sum(sample_count), 0)::int AS samples,
                count(*) FILTER (WHERE climate_action = 'DEHUM_VENT')::int
                    AS vent_dehum_episodes,
                count(*) FILTER (WHERE climate_action = 'HEAT' AND vpd_priority)::int
                    AS heat_dehum_episodes,
                count(*) FILTER (
                    WHERE climate_action IN (
                        'SEALED_HUMIDIFY',
                        'SEALED_FOG',
                        'VENT_COOL_MIST_ASSIST',
                        'VENT_COOL_FOG_ASSIST'
                    )
                )::int AS wetting_episodes
            FROM episodes
            GROUP BY mx_reason
            ORDER BY episodes DESC, mx_reason
            """,
            d,
            greenhouse_id,
        )

        moisture_sample_count = sum(row["decisions"] for row in moisture_rows)
        moisture_estimator = {
            "sample_count": moisture_sample_count,
            "coverage": "available" if moisture_sample_count else "pending_live_rows",
            "by_action_reason": [dict(row) for row in moisture_rows],
        }
        vpd_policy = dict(vpd_policy_row) if vpd_policy_row else {}
        if vpd_policy:
            vpd_policy["transition_window_min"] = 30
            vpd_policy["episodes_by_mx_reason"] = [dict(row) for row in vpd_policy_reason_rows]

        cycle_by_equipment = {row["equipment"]: dict(row) for row in cycle_rows}
        actuator_cycles = {
            equipment: cycle_by_equipment.get(equipment, {}).get("starts")
            for equipment in (
                "fan1",
                "fan2",
                "heat1",
                "heat2",
                "fog",
                "vent",
                "mister_south",
                "mister_west",
                "mister_center",
                "drip_wall",
                "drip_center",
                "drip_wall_fert",
                "drip_center_fert",
                "mister_south_fert",
                "mister_west_fert",
                "fert_master_valve",
                "grow_light_main",
                "grow_light_grow",
            )
        }
        light_cycle_values = [
            actuator_cycles[name]
            for name in ("grow_light_main", "grow_light_grow")
            if actuator_cycles[name] is not None
        ]
        actuator_cycles["grow_light"] = sum(light_cycle_values) if light_cycle_values else None

        actuator_runtime = {
            f"{equipment}_min": cycle_by_equipment.get(equipment, {}).get("on_minutes")
            for equipment in actuator_cycles
            if equipment != "grow_light"
        }
        light_runtime_values = [
            actuator_runtime[f"{name}_min"]
            for name in ("grow_light_main", "grow_light_grow")
            if actuator_runtime[f"{name}_min"] is not None
        ]
        actuator_runtime["grow_light_min"] = sum(light_runtime_values) if light_runtime_values else None
        for mister in ("mister_south", "mister_west", "mister_center"):
            minutes = actuator_runtime[f"{mister}_min"]
            actuator_runtime[f"{mister}_h"] = round(minutes / 60.0, 3) if minutes is not None else None

        dryout = [dict(row) for row in dryout_rows]
        cycle_quality = [
            {
                key: row[key]
                for key in (
                    "equipment",
                    "is_complete_day",
                    "start_state_known",
                    "open_at_end",
                    "is_deploy_gate_eligible",
                    "quality",
                    "quality_flags",
                    "cycles_under_1m",
                    "cycles_1m_to_5m",
                    "short_cycles_under_5m",
                    "cycles_5m_to_15m",
                    "cycles_15m_plus",
                    "open_pulses_at_cutoff",
                    "peak_transitions_per_hour",
                    "raw_event_rows",
                    "normalized_transition_count",
                    "same_timestamp_duplicate_rows",
                    "redundant_state_rows",
                    "conflicting_timestamp_count",
                )
            }
            for row in cycle_rows
        ]
        firmware_counter_diagnostics = {
            "semantics": (
                "Legacy firmware daily counters are diagnostic only; release "
                "comparisons use v_equipment_runtime_daily raw transitions."
            ),
            "cycles": {key.removeprefix("cycles_"): summary.get(key) for key in summary if key.startswith("cycles_")},
            "runtime": {
                key.removeprefix("runtime_"): summary.get(key) for key in summary if key.startswith("runtime_")
            },
        }
        vpd_policy["cycle_source"] = {
            "authority": "v_equipment_runtime_daily",
            "semantics": "raw equipment_state transition derivation",
            "all_rows_deploy_gate_eligible": bool(cycle_rows)
            and all(row["is_deploy_gate_eligible"] for row in cycle_rows),
            "equipment": cycle_quality,
        }
        vpd_policy["firmware_counter_diagnostics"] = firmware_counter_diagnostics
        vpd_policy["realized_solar_night_dryout"] = {
            "semantics": (
                "Measured solar-night demand and relay truth; projected planner "
                "intent is never labeled as realized outcome."
            ),
            "episodes": dryout,
            "dispositions": {
                disposition: sum(row["dryout_disposition"] == disposition for row in dryout)
                for disposition in (
                    "effective",
                    "ineffective",
                    "blocked",
                    "insufficient_evidence",
                )
            },
            # The bounded SQL function repeats adjacent-day counts on each night
            # episode, so take maxima rather than multiplying by episode count.
            # General daytime VPD dehum remains visible and allowed; only an
            # admitted held-temp flavor fails the solar-night safety gate.
            "daytime_dry_action_samples": max(
                (row["daytime_dry_action_samples"] or 0 for row in dryout),
                default=0,
            ),
            "daytime_hold_admission_samples": max(
                (row["daytime_hold_admission_samples"] or 0 for row in dryout),
                default=0,
            ),
            "safety_gate_status": (
                "pending"
                if not dryout
                else ("fail" if any(row["safety_gate_status"] == "fail" for row in dryout) else "pass")
            ),
        }
        pending_metrics = []
        if not moisture_sample_count:
            pending_metrics.append("moisture_estimator: source path wired; waiting for OTA/deploy/live rows")
        if not cycle_rows:
            pending_metrics.append("actuator_cycles_runtime: no transition-derived rows for date")
        elif any(not row["is_deploy_gate_eligible"] for row in cycle_rows):
            pending_metrics.append(
                "actuator_cycles_runtime: transition evidence is partial or quarantined; not deploy-gate eligible"
            )
        if not dryout:
            pending_metrics.append("solar_night_dryout: no eligible opportunity or admitted-action episodes for date")
        elif any(row["dryout_disposition"] != "effective" for row in dryout):
            pending_metrics.append(
                "solar_night_dryout: ineffective, blocked, or insufficient-evidence "
                "episodes remain unresolved; this is not a completed control fix"
            )
        try:
            actions = [OutcomeKpiActionRow.model_validate(dict(row)) for row in action_rows]
            response = OutcomeKpiResponse(
                date=summary.get("date", d),
                greenhouse_id=summary.get("greenhouse_id", greenhouse_id),
                semantics=(
                    "ADR-0004 outcome view: float inside the crop corridor and act at edges. "
                    "Resource terms are eligible only when resource_evidence marks them "
                    "available_for_scoring; modeled and measured scopes never collapse."
                ),
                coverage=OutcomeKpiCoverage(
                    actuator_cycles_runtime=(
                        "available"
                        if cycle_rows and all(row["is_deploy_gate_eligible"] for row in cycle_rows)
                        else "pending"
                    ),
                    moisture_estimator="available" if moisture_sample_count else "pending",
                    water_use=(
                        "available"
                        if water_resource and water_resource.get("available_for_scoring")
                        else "degraded"
                        if water_resource
                        else "unavailable"
                    ),
                    resource_accounting=(
                        "available"
                        if water_resource
                        and water_resource.get("available_for_scoring")
                        and energy_resource
                        and energy_resource.get("modeled_available_for_scoring")
                        else "degraded"
                        if water_resource or energy_resource
                        else "unavailable"
                    ),
                ),
                served_corridor={
                    "attributable_pct": summary.get("compliance_v2_attributable_pct"),
                    "raw_pct": summary.get("compliance_v2_raw_pct"),
                    "unachievable_frac": summary.get("compliance_v2_unachievable_frac"),
                    "binary_both_pct": summary.get("compliance_pct"),
                    "binary_temp_pct": summary.get("temp_compliance_pct"),
                    "binary_vpd_pct": summary.get("vpd_compliance_pct"),
                    "graded_temp_pct": summary.get("graded_temp_compliance_pct"),
                    "graded_vpd_pct": summary.get("graded_vpd_compliance_pct"),
                    "feasibility_unknown_min": summary.get("feasibility_unknown_min"),
                },
                pinched_corridor={
                    "sample_min": pinched.get("sample_min"),
                    "samples_with_fraction_readback": pinched.get("samples_with_fraction_readback"),
                    "avg_band_track_fraction": pinched.get("avg_band_track_fraction"),
                    "min_band_track_fraction": pinched.get("min_band_track_fraction"),
                    "max_band_track_fraction": pinched.get("max_band_track_fraction"),
                    "both_pct": pinched.get("both_pct"),
                    "temp_pct": pinched.get("temp_pct"),
                    "vpd_pct": pinched.get("vpd_pct"),
                    "p95_temp_miss_f": pinched.get("p95_temp_miss_f"),
                    "p95_vpd_miss_kpa": pinched.get("p95_vpd_miss_kpa"),
                    "day_pct": pinched.get("day_pct"),
                    "night_pct": pinched.get("night_pct"),
                },
                vpd_misses_h={
                    "high_stress_h": summary.get("stress_hours_vpd_high"),
                    "low_stress_h": summary.get("stress_hours_vpd_low"),
                    "graded_high_stress_h": summary.get("graded_stress_hours_vpd_high"),
                    "graded_low_stress_h": summary.get("graded_stress_hours_vpd_low"),
                },
                actuator_cycles=actuator_cycles,
                actuator_runtime=actuator_runtime,
                water_use_gal={
                    "quality_filtered_total": (
                        water_resource.get("quality_filtered_meter_gal")
                        if water_resource and water_resource.get("available_for_scoring")
                        else None
                    ),
                    "meter_attributed": (
                        water_resource.get("attributed_gal")
                        if water_resource and water_resource.get("available_for_scoring")
                        else None
                    ),
                    "ambiguous": (
                        water_resource.get("ambiguous_gal")
                        if water_resource and water_resource.get("available_for_scoring")
                        else None
                    ),
                    "manual_or_unattributed": (
                        water_resource.get("manual_or_unattributed_gal")
                        if water_resource and water_resource.get("available_for_scoring")
                        else None
                    ),
                },
                dli={"sensor_mol_m2_d": summary.get("dli_final")},
                dif={
                    "day_night_temp_delta_f": dif.get("day_night_temp_delta_f"),
                    "day_temp_avg_f": dif.get("day_temp_avg_f"),
                    "night_temp_avg_f": dif.get("night_temp_avg_f"),
                    "day_sample_min": dif.get("day_sample_min"),
                    "night_sample_min": dif.get("night_sample_min"),
                },
                dew_margin={
                    "min_f": summary.get("min_dp_margin_f"),
                    "risk_h": summary.get("dp_risk_hours"),
                },
                energy_cost={
                    "runtime_modeled_kwh": (
                        energy_resource.get("kwh_estimated")
                        if energy_resource and energy_resource.get("modeled_available_for_scoring")
                        else None
                    ),
                    "partial_measured_kwh": (
                        energy_resource.get("measured_kwh")
                        if energy_resource and energy_resource.get("measured_available_for_scoring")
                        else None
                    ),
                    "electric_usd": (
                        summary.get("cost_electric")
                        if energy_resource and energy_resource.get("modeled_available_for_scoring")
                        else None
                    ),
                    "water_usd": (
                        summary.get("cost_water")
                        if water_resource and water_resource.get("available_for_scoring")
                        else None
                    ),
                    "total_usd": (
                        summary.get("cost_total")
                        if water_resource
                        and water_resource.get("available_for_scoring")
                        and energy_resource
                        and energy_resource.get("modeled_available_for_scoring")
                        else None
                    ),
                },
                resource_evidence={
                    "water": water_resource,
                    "energy": energy_resource,
                    "contract": (
                        "accepted meter gallons are conserved across attributed, ambiguous, "
                        "and manual_or_unattributed; command-only runs have null gallons; "
                        "runtime-modeled energy and partial Shelly energy are separate scopes"
                    ),
                },
                action_scorecard=actions,
                solar_phase_buckets=[dict(row) for row in phase_rows],
                moisture_estimator=moisture_estimator,
                vpd_policy=vpd_policy,
                pending_metrics=pending_metrics,
                source_tables=[
                    "daily_summary",
                    "v_equipment_runtime_daily",
                    "fn_realized_solar_night_dryout",
                    "v_climate_action_daily_scorecard",
                    "v_water_attribution_daily",
                    "v_energy_estimate_reconciliation",
                    "v_resource_accounting_health",
                    "climate",
                    "climate_action_log",
                    "setpoint_snapshot",
                    "setpoint_changes",
                ],
            )
        except ValidationError as e:
            return _json(
                {
                    "error": "OutcomeKpiResponse validation failed",
                    "details": json.loads(e.json()),
                    "raw": {
                        "summary": summary,
                        "pinched_corridor": pinched,
                        "dif": dif,
                        "solar_phase_buckets": [dict(row) for row in phase_rows],
                        "action_scorecard": [dict(row) for row in action_rows],
                    },
                }
            )
        return response.model_dump_json()
    finally:
        await conn.close()


@mcp.tool()
async def equipment_state() -> str:
    """Get current state of all greenhouse equipment (relays, misters, heaters, fans, vent)."""
    conn = await _db()
    try:
        rows = await conn.fetch("""
            SELECT equipment, state, to_char(ts AT TIME ZONE 'America/Denver', 'HH24:MI:SS') as since
            FROM (SELECT DISTINCT ON (equipment) equipment, state, ts
                  FROM equipment_state ORDER BY equipment, ts DESC) sub
            WHERE equipment IN ('fan1','fan2','vent','fog','heat1','heat2',
                'mister_south','mister_west','mister_center')
            ORDER BY equipment
        """)
        validated = [EquipmentStateRow.model_validate(dict(r)).model_dump(mode="json") for r in rows]
        return json.dumps(validated)
    finally:
        await conn.close()


@mcp.tool()
async def forecast(hours: int = 72) -> str:
    """Get weather forecast summary for the next N hours (default 72).
    Returns hourly temp, RH, VPD, cloud cover, solar radiation."""
    try:
        hours = max(1, min(int(hours), 168))
    except (TypeError, ValueError):
        return json.dumps({"error": "hours must be an integer between 1 and 168"})
    conn = await _db()
    try:
        rows = await conn.fetch(
            """
            SELECT to_char(ts AT TIME ZONE 'America/Denver', 'Dy HH24:MI') as time,
                   round(temp_f::numeric,0) as temp, round(rh_pct::numeric,0) as rh,
                   round(vpd_kpa::numeric,2) as vpd, round(cloud_cover_pct::numeric,0) as cloud,
                   round(GREATEST(COALESCE(direct_radiation_w_m2,0),0)::numeric,0) as solar
            FROM (
                SELECT DISTINCT ON (ts) ts, temp_f, rh_pct, vpd_kpa, cloud_cover_pct,
                       direct_radiation_w_m2
                FROM weather_forecast
                WHERE ts > now() AND ts < now() + ($1::int * interval '1 hour')
                ORDER BY ts, fetched_at DESC
            ) sub
            ORDER BY ts
            LIMIT $1
            """,
            hours,
        )
        validated = [ForecastSummaryRow.model_validate(dict(r)).model_dump(mode="json") for r in rows]
        return json.dumps(validated)
    finally:
        await conn.close()


# ═══════════════════════════════════════════════════════════════
# SETPOINT TOOLS
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
async def get_setpoints() -> str:
    """Get all current active setpoints (firmware band values + planner tunables)."""
    conn = await _db()
    try:
        rows = await conn.fetch(
            """
            SELECT parameter, round(value::numeric,3) as value, source,
                   to_char(ts AT TIME ZONE 'America/Denver', 'HH24:MI') as updated
            FROM (SELECT DISTINCT ON (parameter) parameter, value, source, ts
                  FROM setpoint_changes ORDER BY parameter, ts DESC) sub
            WHERE parameter = ANY($1::text[])
            ORDER BY parameter
            """,
            sorted(ALL_TUNABLES),
        )
        validated = [SetpointSummary.model_validate(dict(r)).model_dump(mode="json") for r in rows]
        return json.dumps(validated)
    finally:
        await conn.close()


@mcp.tool()
async def set_tunable(
    parameter: str = "",
    value: float | None = None,
    reason: str = "iris-manual",
    trigger_id: str | None = None,
    planner_instance: str | None = None,
) -> str:
    """Push a single registry-approved tunable to the ESP32 immediately.
    The dispatcher will apply it within 5 minutes.
    Example: set_tunable('fog_escalation_kpa', 0.15, 'fog is 7x more effective than misters')

    trigger_id, planner_instance: required contract v1.5 audit fields for MCP writes.
    Pass through from the trigger banner shown at the bottom of every
    planning event prompt (`trigger_id=<uuid>`, `planner_instance='opus'|'local'`).
    Stamped onto the one-shot setpoint_plan reason and plan_delivery_log so
    SLA monitors can correlate by uuid."""
    normalized_trigger_id: str | None = None
    if not trigger_id:
        return json.dumps(
            {
                "error": "trigger_id is required for set_tunable MCP writes",
                "hint": "Copy trigger_id exactly from the planning prompt audit headers into set_tunable.",
            }
        )
    try:
        normalized_trigger_id = str(UUID(trigger_id))
    except (TypeError, ValueError):
        return json.dumps({"error": "trigger_id must be a valid UUID"})
    if not parameter:
        return json.dumps({"error": "parameter is required"})
    if value is None:
        return json.dumps({"error": "value is required"})

    # Schema-level gate first rejects typos; the registry then blocks
    # operator-only safety rails and readback-only diagnostics.
    if parameter not in ALL_TUNABLES:
        return json.dumps({"error": f"'{parameter}' is not a known tunable — not in verdify_schemas.ALL_TUNABLES"})
    if parameter not in PLANNER_PUSHABLE_REG:
        return json.dumps(
            {
                "error": f"'{parameter}' is not planner-pushable in the tunable registry",
                "allowed": sorted(PLANNER_PUSHABLE_REG),
            }
        )
    if bounds_error := registry_value_error(parameter, value):
        return json.dumps(
            {
                "error": "Tunable value outside registry bounds",
                "parameter": parameter,
                "value": value,
                "details": bounds_error,
            }
        )
    if parameter in FORCED_ON_SWITCH_PARAMS and value < 0.5:
        return json.dumps(
            {
                "error": "controller_locked_on",
                "parameter": parameter,
                "value": value,
                "hint": "The unified band-first controller is locked ON; rollback requires an explicit firmware/config rollback outside the planner surface.",
            }
        )

    # Phase-1b: set_tunable writes to setpoint_plan (one-shot waypoint at
    # ts=now()) so the dispatcher's plan-reading cycle doesn't overwrite
    # the iris push within 5 minutes. Observed live 2026-04-21:
    # min_heat_off_s=180 pushed at 11:36, overwritten to 300 from the
    # prior sunrise plan within 4 minutes. setpoint_plan is the dispatcher's
    # actual source of truth; writing there makes iris pushes durable until
    # the next plan supersedes.
    #
    # plan_id format `iris-oneshot-<YYYYMMDD-HHMM>` lets the next set_plan
    # call (which deactivates older plans) distinguish iris tactical pushes
    # from automatic SUNRISE/SUNSET plans and preserve them across boundaries.
    # Contract v1.5 — stamp audit metadata into dedicated setpoint_plan
    # columns for dispatcher propagation; keep the suffix in reason for
    # operator-readable compatibility with older queries.
    audit_suffix_parts = []
    if normalized_trigger_id:
        audit_suffix_parts.append(f"trigger={normalized_trigger_id}")
    if planner_instance:
        audit_suffix_parts.append(f"instance={planner_instance}")
    if audit_suffix_parts:
        reason_with_audit = f"{reason} [{' '.join(audit_suffix_parts)}]"
    else:
        reason_with_audit = reason

    conn = await _db()
    try:
        now_mdt = datetime.now(ZoneInfo("America/Denver"))
        plan_id = f"iris-oneshot-{now_mdt.strftime('%Y%m%d-%H%M')}"
        async with conn.transaction():
            if normalized_trigger_id:
                delivery = await conn.fetchrow(
                    """
                    SELECT trigger_id, status, instance
                      FROM plan_delivery_log
                     WHERE trigger_id = $1::uuid
                    """,
                    normalized_trigger_id,
                )
                if not delivery:
                    return json.dumps(
                        {
                            "error": "trigger_id not found in plan_delivery_log",
                            "trigger_id": normalized_trigger_id,
                        }
                    )
                if delivery["status"] not in {"pending", "plan_written"}:
                    return json.dumps(
                        {
                            "error": "trigger_id is not writable",
                            "trigger_id": normalized_trigger_id,
                            "status": delivery["status"],
                        }
                    )
                if planner_instance and delivery["instance"] and planner_instance != delivery["instance"]:
                    return json.dumps(
                        {
                            "error": "planner_instance does not match plan_delivery_log",
                            "trigger_id": normalized_trigger_id,
                            "planner_instance": planner_instance,
                            "delivery_instance": delivery["instance"],
                        }
                    )

            wrote_at = await conn.fetchval(
                """
                INSERT INTO setpoint_plan
                  (ts, parameter, value, plan_id, source, reason, trigger_id, planner_instance)
                VALUES (now(), $1, $2, $3, 'iris', $4, $5::uuid, $6)
                ON CONFLICT (ts, parameter, plan_id) DO UPDATE
                  SET value = EXCLUDED.value,
                      reason = EXCLUDED.reason,
                      trigger_id = EXCLUDED.trigger_id,
                      planner_instance = EXCLUDED.planner_instance
                RETURNING ts
                """,
                parameter,
                value,
                plan_id,
                reason_with_audit,
                normalized_trigger_id,
                planner_instance,
            )
            if normalized_trigger_id:
                await conn.execute(
                    """
                    UPDATE plan_delivery_log
                       SET resulting_plan_id = $2,
                           plan_written_at   = $3,
                           status            = 'plan_written'
                     WHERE trigger_id = $1::uuid
                       AND status IN ('pending', 'plan_written')
                    """,
                    normalized_trigger_id,
                    plan_id,
                    wrote_at,
                )
        return json.dumps(
            {
                "ok": True,
                "parameter": parameter,
                "value": value,
                "reason": reason_with_audit,
                "plan_id": plan_id,
                "trigger_id": normalized_trigger_id,
                "planner_instance": planner_instance,
                "delivery_status": "plan_written" if normalized_trigger_id else None,
                "note": (
                    "Written to setpoint_plan as a one-shot waypoint at now(). "
                    "Dispatcher pushes to ESP32 within 5 minutes and this value "
                    "persists until the next set_plan or set_tunable supersedes."
                ),
            }
        )
    finally:
        await conn.close()


# ═══════════════════════════════════════════════════════════════
# PLANNER TOOLS
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
async def plan_run(mode: str = "normal") -> str:
    """Trigger an ad-hoc MANUAL planning cycle through the same audited path as scheduled triggers."""
    sys.path.insert(0, str(REPO_ROOT / "ingestor"))
    try:
        from iris_planner import CONTEXT_GATHER_FAILED_SENTINEL, gather_context, prepare_delivery_result, send_to_iris

        mode_clean = (mode or "normal").strip().lower()
        context = gather_context()
        label = f"Ad-hoc planning cycle via MCP plan_run(mode={mode_clean})"
        if context == CONTEXT_GATHER_FAILED_SENTINEL:
            result = {
                "delivered": False,
                "event_type": "MANUAL",
                "event_label": label,
                "session_key": None,
                "wake_mode": None,
                "gateway_status": None,
                "gateway_body": "context_gather_failed",
                "status": "delivery_failed",
                "trigger_id": str(uuid4()),
                "instance": "local",
            }
        else:
            if mode_clean in {"ack", "ack_only", "ack-only", "smoke", "validation"}:
                label = f"validation ack-only: {label}"
                context = (
                    "VALIDATION MODE: acknowledge-only smoke. Do not call set_plan or set_tunable. "
                    "Call acknowledge_trigger with the audit trigger_id and planner_instance, "
                    "then stop.\n\n"
                ) + context

            pre_result = prepare_delivery_result("MANUAL", label, instance="local")
            conn = await _db()
            try:
                await _insert_plan_delivery_log(conn, pre_result)
            finally:
                await conn.close()
            result = send_to_iris(
                "MANUAL",
                label,
                context=context,
                instance="local",
                trigger_id=pre_result["trigger_id"],
            )

        conn = await _db()
        try:
            explicit_status = await _insert_plan_delivery_log(conn, result)
        finally:
            await conn.close()

        status = explicit_status or "pending"
        resp = PlanRunResponse(
            ok=bool(result.get("delivered")),
            note="MANUAL event sent to Hermes. Check plan_delivery_log for ack/plan correlation.",
            error=None if result.get("delivered") else result.get("gateway_body"),
            trigger_id=result.get("trigger_id"),
            event_type=result.get("event_type"),
            planner_instance=result.get("instance"),
            session_key=result.get("session_key"),
            status=status,
            hermes_run_id=result.get("hermes_run_id"),
        )
        return resp.model_dump_json(exclude_none=True)
    except Exception as e:
        return PlanRunResponse(ok=False, error=str(e)).model_dump_json(exclude_none=True)


@mcp.tool()
async def plan_status() -> str:
    """Get the current active plan — waypoints, plan_id, hypothesis, compliance."""
    conn = await _db()
    try:
        journal = await conn.fetchrow("""
            SELECT plan_id, to_char(created_at AT TIME ZONE 'America/Denver', 'MM-DD HH24:MI') as created,
                   hypothesis, experiment, expected_outcome
            FROM plan_journal WHERE plan_id NOT LIKE 'iris-reactive%'
            ORDER BY created_at DESC LIMIT 1
        """)
        waypoints = await conn.fetch("""
            SELECT to_char(ts AT TIME ZONE 'America/Denver', 'Dy HH24:MI') as time, count(*) as params
            FROM setpoint_plan WHERE is_active = true AND ts > now()
            GROUP BY ts ORDER BY ts LIMIT 15
        """)
        resp = PlanStatusResponse(
            plan=PlanStatusJournal.model_validate(dict(journal)) if journal else None,
            future_waypoints=[PlanStatusWaypoint.model_validate(dict(w)) for w in waypoints],
        )
        return resp.model_dump_json(exclude_none=True)
    finally:
        await conn.close()


@mcp.tool()
async def lessons() -> str:
    """Get active planner lessons (accumulated operational knowledge)."""
    conn = await _db()
    try:
        rows = await conn.fetch("""
            SELECT category, condition, lesson, confidence, times_validated
            FROM planner_lessons WHERE is_active = true AND superseded_by IS NULL
            ORDER BY CASE confidence WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                     times_validated DESC
            LIMIT 10
        """)
        validated = [LessonSummary.model_validate(dict(r)).model_dump(mode="json") for r in rows]
        return json.dumps(validated)
    finally:
        await conn.close()


# ═══════════════════════════════════════════════════════════════
# DATA QUERY TOOL
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
async def query(sql: str) -> str:
    """Run a read-only SQL query against the Verdify database.
    Returns up to 100 rows as JSON. Only SELECT queries allowed."""
    sql_stripped = sql.strip()
    sql_upper = sql_stripped.upper()
    if not (sql_upper.startswith("SELECT") or sql_upper.startswith("WITH")):
        return json.dumps({"error": "Only SELECT/WITH queries are allowed"})
    # Keep this as a simple one-statement diagnostic path. The DB transaction is
    # read-only below, but rejecting multi-statement text avoids surprising
    # behavior and keeps tool output bounded.
    if ";" in sql_stripped.rstrip(";"):
        return json.dumps({"error": "Only a single read-only statement is allowed"})

    conn = await _db()
    try:
        async with conn.transaction(readonly=True):
            await conn.execute("SET LOCAL statement_timeout = '5s'")
            rows = await conn.fetch(sql_stripped.rstrip(";"))
        return _json([dict(r) for r in rows[:100]])
    except asyncpg.ReadOnlySQLTransactionError:
        return json.dumps({"error": "Query attempted a write and was rejected by the read-only transaction"})
    finally:
        await conn.close()


# ═══════════════════════════════════════════════════════════════
# PLANNING TOOLS
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
async def set_plan(
    plan_id: str = "",
    hypothesis: str = "",
    transitions: str = "",
    experiment: str = "",
    expected_outcome: str = "",
    trigger_id: str | None = None,
    planner_instance: str | None = None,
) -> str:
    """Write a 72-hour setpoint plan with multiple time-based waypoints.
    Deactivates all existing future waypoints, writes new ones, and logs a plan journal entry.
    The dispatcher executes these on schedule — the greenhouse follows the plan even if the planner goes offline.

    plan_id: unique ID like 'iris-YYYYMMDD-HHMM'
    hypothesis: what you expect this plan to achieve — may optionally include a
        fenced ```json block matching PlanHypothesisStructured (conditions +
        stress_windows + rationale). If present, it's validated and stored in
        plan_journal.hypothesis_structured for structured downstream rendering.
    transitions: JSON array of objects: [{"ts": "ISO8601-with-TZ", "climate_intent": {...}, "reason": "..."}]
    experiment: optional one-line experiment description
    expected_outcome: optional measurable prediction
    trigger_id, planner_instance: required contract v1.5 audit fields. Pass
        through from the audit-headers banner shown at the bottom of every
        planning event prompt (`trigger_id=<uuid>`, `planner_instance='opus'|'local'`).
        Stamped onto plan_journal so SLA monitors and audit queries can
        correlate plans to deliveries by uuid (not 2h time-window fallback)."""
    # Sprint 20: validate the whole envelope through Plan schema before any DB writes.
    # This rejects unknown tunables, inverted temp/VPD bands, non-monotonic transitions,
    # bad plan_id format, timezone-naive timestamps, etc. — at the MCP boundary, so
    # partial plans never land in setpoint_plan.
    normalized_trigger_id: str | None = None
    if not trigger_id:
        return json.dumps(
            {
                "error": "trigger_id is required for set_plan MCP writes",
                "hint": "Copy trigger_id exactly from the planning prompt audit headers into set_plan.",
            }
        )
    try:
        normalized_trigger_id = str(UUID(trigger_id))
    except (TypeError, ValueError):
        return json.dumps({"error": "trigger_id must be a valid UUID"})
    if not plan_id:
        return json.dumps({"error": "plan_id is required"})
    if not transitions:
        return json.dumps({"error": "transitions is required"})

    try:
        waypoints_raw = json.loads(transitions)
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"Invalid JSON in transitions: {e}"})

    climate_intent_errors = _climate_intent_waypoint_errors(waypoints_raw)
    if climate_intent_errors:
        return json.dumps(
            {
                "error": "set_plan requires climate_intent on every transition",
                "details": climate_intent_errors[:10],
            }
        )
    conn_for_intent = await _db()
    try:
        active_tier1_params = await _fetch_active_tier1_params(conn_for_intent)
    finally:
        await conn_for_intent.close()
    try:
        waypoints_raw, climate_intent_records = _materialize_climate_intent_waypoints(
            waypoints_raw,
            active_tier1_params,
        )
    except ValidationError as e:
        return json.dumps(
            {
                "error": "ClimateIntent validation failed",
                "details": json.loads(e.json(include_input=False))[:10],
            }
        )
    except ValueError as e:
        return json.dumps({"error": "ClimateIntent materialization failed", "detail": str(e)})

    try:
        plan = Plan.model_validate(
            {
                "plan_id": plan_id,
                "hypothesis": hypothesis,
                "experiment": experiment or None,
                "expected_outcome": expected_outcome or None,
                "transitions": waypoints_raw,
            }
        )
    except ValidationError as e:
        return json.dumps({"error": "Plan validation failed", "details": json.loads(e.json(include_input=False))[:10]})

    writable_params = [param for wp in plan.transitions for param in wp.params if param not in BAND_OWNED_PARAMS]
    if not writable_params:
        return json.dumps(
            {
                "error": "Plan contains only dispatcher-owned policy params; these are read-only context",
                "band_owned_params": sorted(BAND_OWNED_PARAMS),
            }
        )
    missing_required = []
    for idx, wp in enumerate(plan.transitions):
        missing = sorted(PLAN_REQUIRED_PARAMS - set(wp.params))
        if missing:
            missing_required.append({"transition_index": idx, "ts": wp.ts.isoformat(), "missing": missing})
    if missing_required:
        return json.dumps(
            {
                "error": f"Plan transitions must include all {len(PLAN_REQUIRED_PARAMS)} tactical Tier 1 params",
                "missing_required_params": missing_required,
                "required_params": sorted(PLAN_REQUIRED_PARAMS),
                "band_owned_params": sorted(BAND_OWNED_PARAMS),
            }
        )
    non_policy_params = sorted(
        {
            param
            for wp in plan.transitions
            for param in wp.params
            if param not in BAND_OWNED_PARAMS and param not in PLANNER_PUSHABLE_REG
        }
    )
    if non_policy_params:
        return json.dumps(
            {
                "error": "Plan contains non-policy tunables; MCP only persists planner-policy params",
                "non_policy_params": non_policy_params,
                "allowed_params": sorted(PLANNER_PUSHABLE_REG),
                "band_owned_params": sorted(BAND_OWNED_PARAMS),
            }
        )

    # Phase 2b (Iris loop overhaul): extract structured hypothesis and enforce
    # presence for SUNRISE/SUNSET. Two parser paths:
    #   1. Fenced ```json …``` block anywhere in the hypothesis (original)
    #   2. Bare top-level JSON when the hypothesis field is entirely JSON
    #      (common GPT-5.5 output mode — flagged by Codex audit 2026-05-10)
    structured_payload: str | None = None
    structured_warning: str | None = None
    import re as _re

    def _try_parse_structured(blob: str) -> tuple[str | None, str | None]:
        try:
            ps = PlanHypothesisStructured.model_validate_json(blob)
            return ps.model_dump_json(), None
        except ValidationError as ee:
            return None, f"structured hypothesis present but invalid: {ee.errors()[:3]}"

    # Path 1: fenced ```json block
    m = _re.search(r"```json\s*(\{.*?\})\s*```", hypothesis, _re.DOTALL)
    if m:
        structured_payload, structured_warning = _try_parse_structured(m.group(1))

    # Path 2: bare top-level JSON (GPT-5.5 often omits the fence)
    if structured_payload is None:
        stripped = (hypothesis or "").strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            sp, sw = _try_parse_structured(stripped)
            if sp is not None:
                structured_payload = sp
            elif structured_warning is None:
                structured_warning = sw

    # P1a (B6): the planner's crop-band targets (temp_low/high, vpd_low/high,
    # per-zone vpd targets) are dropped from setpoint_plan below (clamp-safe — the
    # dispatcher/curve owns the served band), but they were ALSO silently excluded
    # from the plan_journal audit, so plan-accuracy had no record of what band the
    # planner intended. Restore band-param *recording* (NOT actuation): include the
    # crop-band param NAMES the planner touched in params_changed so v_plan_accuracy
    # / v_plan_compliance can grade planned-vs-served band against the new curve.
    # Lighting band-owned params (gl_*) stay excluded — they are not the crop band.
    planned_band: list[dict] = []
    for wp in plan.transitions:
        band_targets = {p: float(v) for p, v in wp.params.items() if p in CROP_BAND_PARAMS}
        if band_targets:
            planned_band.append(
                {"ts": wp.ts.isoformat() if hasattr(wp.ts, "isoformat") else str(wp.ts), **band_targets}
            )

    params_seen = sorted(
        {
            param
            for wp in plan.transitions
            for param in wp.params
            if param not in BAND_OWNED_PARAMS or param in CROP_BAND_PARAMS
        }
    )

    conditions_summary: str | None = None
    if structured_payload:
        try:
            structured = json.loads(structured_payload)
            conditions = structured.get("conditions") or {}
            stress_windows = structured.get("stress_windows") or []
            parts: list[str] = []
            if conditions.get("notes"):
                parts.append(str(conditions["notes"]))
            weather_bits = []
            for key, label in (
                ("outdoor_temp_peak_f", "outdoor peak"),
                ("outdoor_rh_min_pct", "RH min"),
                ("solar_peak_w_m2", "solar peak"),
                ("cloud_cover_avg_pct", "cloud cover"),
            ):
                if conditions.get(key) is not None:
                    weather_bits.append(f"{label}: {conditions[key]}")
            if weather_bits:
                parts.append(", ".join(weather_bits))
            if stress_windows:
                labels = []
                for window in stress_windows[:4]:
                    labels.append(
                        "{kind} {start}-{end} {severity}".format(
                            kind=window.get("kind", "stress"),
                            start=window.get("start", "?"),
                            end=window.get("end", "?"),
                            severity=window.get("severity", "?"),
                        )
                    )
                parts.append("stress windows: " + "; ".join(labels))
            conditions_summary = " | ".join(parts)[:2000] if parts else None
        except (TypeError, ValueError):
            conditions_summary = None

    conn = await _db()
    try:
        async with conn.transaction():
            existing = await conn.fetchval("SELECT 1 FROM plan_journal WHERE plan_id = $1", plan.plan_id)
            if existing:
                return json.dumps({"error": f"plan_id {plan.plan_id!r} already exists; generate a new plan_id"})

            # Phase 2b: SUNRISE/SUNSET MUST carry a valid hypothesis_structured.
            # Look up the trigger's event_type from planner_trigger_ledger and
            # reject if the structured block is missing or invalid.
            event_type_row = await conn.fetchrow(
                "SELECT event_type FROM planner_trigger_ledger WHERE trigger_id = $1::uuid",
                normalized_trigger_id,
            )
            event_type = event_type_row["event_type"] if event_type_row else None
            if event_type in ("SUNRISE", "SUNSET") and structured_payload is None:
                return json.dumps(
                    {
                        "error": f"{event_type} plans require a valid PlanHypothesisStructured block",
                        "detail": structured_warning or "no JSON block found in hypothesis",
                        "required_top_level_keys": ["conditions", "stress_windows", "rationale"],
                        "accepted_formats": [
                            "fenced ```json {...} ``` block in the hypothesis prose",
                            "bare top-level JSON (the entire hypothesis field is one JSON object)",
                        ],
                        "example_template": {
                            "conditions": {
                                "outdoor_temp_peak_f": 75.0,
                                "outdoor_rh_min_pct": 25.0,
                                "solar_peak_w_m2": 900,
                                "cloud_cover_avg_pct": 30,
                                "notes": "describe the dominant weather drivers and any unusual conditions",
                            },
                            "stress_windows": [
                                {
                                    "kind": "vpd_high",
                                    "start": "2026-05-10T11:00:00-06:00",
                                    "end": "2026-05-10T17:00:00-06:00",
                                    "severity": "medium",
                                    "mitigation": "engage 1.3, gap 25s, fog_escalation 0.30",
                                }
                            ],
                            "rationale": [
                                {
                                    "parameter": "mister_engage_kpa",
                                    "old_value": 1.6,
                                    "new_value": 1.3,
                                    "forecast_anchor": "RH < 15% from 11:00-17:00",
                                    "expected_effect": "drop VPD-high stress hours from 4.5 to under 2.0",
                                }
                            ],
                        },
                    }
                )

            if normalized_trigger_id:
                delivery = await conn.fetchrow(
                    """
                    SELECT trigger_id, status, instance
                      FROM plan_delivery_log
                     WHERE trigger_id = $1::uuid
                    """,
                    normalized_trigger_id,
                )
                if not delivery:
                    return json.dumps(
                        {
                            "error": "trigger_id not found in plan_delivery_log",
                            "trigger_id": normalized_trigger_id,
                        }
                    )
                if delivery["status"] not in {"pending", "timed_out"}:
                    return json.dumps(
                        {
                            "error": "trigger_id is not pending or timed_out",
                            "trigger_id": normalized_trigger_id,
                            "status": delivery["status"],
                        }
                    )
                if planner_instance and delivery["instance"] and planner_instance != delivery["instance"]:
                    return json.dumps(
                        {
                            "error": "planner_instance does not match plan_delivery_log",
                            "trigger_id": normalized_trigger_id,
                            "planner_instance": planner_instance,
                            "delivery_instance": delivery["instance"],
                        }
                    )

            # Deactivate existing future waypoints EXCEPT iris-oneshot tactical pushes.
            # Phase 1b: set_tunable writes to setpoint_plan with plan_id
            # `iris-oneshot-<YYYYMMDD-HHMM>`. Those are live tactical adjustments
            # that should survive across regular sunrise/sunset plans until
            # superseded by a later waypoint. Plan-level supersession is by
            # `created_at DESC` so the newer multi-waypoint plan still wins on
            # any parameter it re-specifies.
            await conn.execute(
                """UPDATE setpoint_plan SET is_active = false
                   WHERE ts > now() AND is_active = true
                     AND plan_id NOT LIKE 'iris-oneshot-%'"""
            )

            # Write new waypoints. Crop-band and lighting-policy params are
            # read-only planner context, owned by DB policy functions +
            # dispatcher; dropping them here prevents future clamp storms from
            # semantically valid but owner-misaligned plans.
            rows_written = 0
            band_params_dropped = 0
            forced_on_params = 0
            for wp in plan.transitions:
                for param, value in wp.params.items():
                    if param in BAND_OWNED_PARAMS:
                        band_params_dropped += 1
                        continue
                    if param in FORCED_ON_SWITCH_PARAMS and float(value) < 0.5:
                        value = 1.0
                        forced_on_params += 1
                    await conn.execute(
                        """INSERT INTO setpoint_plan
                             (ts, parameter, value, plan_id, source, reason, created_at,
                              is_active, greenhouse_id, trigger_id, planner_instance)
                           VALUES ($1, $2, $3, $4, 'iris', $5, now(), true, 'vallery', $6::uuid, $7)""",
                        wp.ts,
                        param,
                        float(value),
                        plan.plan_id,
                        wp.reason or "",
                        normalized_trigger_id,
                        planner_instance,
                    )
                    rows_written += 1

            # Write journal entry — structured JSONB column populated only if
            # the PlanHypothesisStructured block was present AND valid.
            # Contract v1.4 §2.C — stamp planner_instance + trigger_id when the
            # caller passed them through from the prompt's audit-headers banner.
            # Both columns nullable; NULL means "pre-v1.4 path or operator
            # injection that didn't carry headers."
            journal_created_at = await conn.fetchval(
                """INSERT INTO plan_journal
                     (plan_id, created_at, hypothesis, experiment, expected_outcome,
                      hypothesis_structured, greenhouse_id, planner_instance, trigger_id,
                      conditions_summary, params_changed, climate_intents,
                      climate_intent_version)
                   VALUES ($1, now(), $2, $3, $4, $5::jsonb, 'vallery', $6, $7::uuid,
                           $8, $9::text[], $10::jsonb, $11)
                   RETURNING created_at""",
                plan.plan_id,
                plan.hypothesis,
                plan.experiment,
                plan.expected_outcome,
                structured_payload,
                planner_instance,
                normalized_trigger_id,
                conditions_summary,
                params_seen,
                json.dumps(climate_intent_records) if climate_intent_records else None,
                CLIMATE_INTENT_CONTRACT_VERSION if climate_intent_records else None,
            )
            if normalized_trigger_id:
                await conn.execute(
                    """
                    UPDATE plan_delivery_log
                       SET resulting_plan_id = $2,
                           plan_written_at   = $3,
                           status            = 'plan_written'
                     WHERE trigger_id = $1::uuid
                       AND status IN ('pending', 'timed_out')
                    """,
                    normalized_trigger_id,
                    plan.plan_id,
                    journal_created_at,
                )

        # Sprint 20 Phase 6: drop a trigger file so verdify-plan-publish.path
        # fires and regenerates the daily plan page. Local-SSD location so
        # inotify actually works (NFS path units don't fire reliably).
        try:
            from datetime import UTC
            from datetime import datetime as _dt

            trigger_path = Path("/var/local/verdify/state/plan-publish-trigger")
            trigger_path.parent.mkdir(parents=True, exist_ok=True)
            trigger_path.write_text(f"{plan.plan_id}\n{_dt.now(UTC).isoformat()}\n")
        except Exception as e:  # never block plan persistence on trigger failures
            log_msg = f"plan-publish trigger write failed (non-fatal): {e}"
            print(log_msg)

        result = {
            "ok": True,
            "plan_id": plan.plan_id,
            "transitions": len(plan.transitions),
            "rows_written": rows_written,
            "band_params_dropped": band_params_dropped,
            "planned_band_recorded": len(planned_band),
            "forced_on_params": forced_on_params,
            "climate_intent_segments": len(climate_intent_records),
            "climate_intent_version": CLIMATE_INTENT_CONTRACT_VERSION if climate_intent_records else None,
            "climate_intent_guardrails": sum(len(record.get("guardrails", ())) for record in climate_intent_records),
            "structured_hypothesis": structured_payload is not None,
            "trigger_id": normalized_trigger_id,
            "planner_instance": planner_instance,
            "delivery_status": "plan_written" if normalized_trigger_id else None,
            "note": "Dispatcher will execute waypoints on schedule. Old future waypoints deactivated.",
        }
        if structured_warning:
            result["structured_warning"] = structured_warning
        return json.dumps(result)
    finally:
        await conn.close()


@mcp.tool()
async def acknowledge_trigger(trigger_id: str, reason: str, planner_instance: str | None = None) -> str:
    """Record that Iris read a planning trigger and intentionally wrote no plan.

    Use this only when a FORECAST/TRANSITION/HEARTBEAT cycle needs no setpoint
    change. It turns the matching plan_delivery_log row from pending -> acked,
    so SLA monitors can distinguish "read/no action" from "silent drop"."""
    try:
        tid = UUID(trigger_id)
    except (TypeError, ValueError):
        return json.dumps({"error": "trigger_id must be a valid UUID"})

    reason = (reason or "").strip()
    if not reason:
        return json.dumps({"error": "reason is required"})
    if len(reason) > 1000:
        return json.dumps({"error": "reason must be <= 1000 characters"})

    conn = await _db()
    try:
        existing = await conn.fetchrow(
            """
            SELECT id, event_type, event_label, instance, status
              FROM plan_delivery_log
             WHERE trigger_id = $1::uuid
            """,
            str(tid),
        )
        if existing is None:
            return json.dumps({"error": f"trigger_id {tid} not found in plan_delivery_log"})
        if existing["status"] != "pending":
            return _json(
                {
                    "ok": False,
                    "trigger_id": str(tid),
                    "note": "trigger was already resolved",
                    "status": existing["status"],
                    "event_type": existing["event_type"],
                    "instance": existing["instance"],
                }
            )
        event_label = (existing["event_label"] or "").lower()
        is_validation_ack = event_label.startswith("validation") and "ack-only" in event_label
        if existing["event_type"] in {"SUNRISE", "SUNSET", "MIDNIGHT"} and not is_validation_ack:
            return _json(
                {
                    "error": "SUNRISE/SUNSET/MIDNIGHT triggers require set_plan; acknowledge_trigger is allowed only for validation ack-only rows",
                    "trigger_id": str(tid),
                    "event_type": existing["event_type"],
                    "event_label": existing["event_label"],
                }
            )
        row = await conn.fetchrow(
            """
            UPDATE plan_delivery_log
               SET status = 'acked',
                   acked_at = now(),
                   gateway_body = concat_ws(E'\n', NULLIF(gateway_body, ''), $2::text)
             WHERE trigger_id = $1::uuid
               AND status = 'pending'
             RETURNING id, event_type, instance, delivered_at, status
            """,
            str(tid),
            f"acknowledged by {planner_instance or 'iris'}: {reason}",
        )
        if row is None:
            return _json(
                {
                    "ok": False,
                    "trigger_id": str(tid),
                    "note": "trigger was already resolved",
                    "status": existing["status"],
                    "event_type": existing["event_type"],
                    "instance": existing["instance"],
                }
            )
        return _json(
            {
                "ok": True,
                "trigger_id": str(tid),
                "event_type": row["event_type"],
                "instance": row["instance"],
                "planner_instance": planner_instance,
                "status": row["status"],
            }
        )
    finally:
        await conn.close()


@mcp.tool()
async def plan_evaluate(plan_id: str, outcome_score: int, actual_outcome: str, lesson_extracted: str = "") -> str:
    """Write the evaluation results for a completed plan back to plan_journal.
    This CLOSES the learning loop: Plan → Execute → Measure → Evaluate → Learn.

    plan_id: the plan to evaluate (e.g. 'iris-20260411-1346')
    outcome_score: 1-10 score for how well the plan achieved its hypothesis
    actual_outcome: what actually happened (stress hours, compliance, key observations)
    lesson_extracted: new lesson learned, or empty if none

    Side effects (loop-closure repair, see migration 111):
      - Computes fn_plan_anchor_score(plan_id), stores it in plan_journal.anchor_score.
      - If |outcome_score - anchor_score| > 2, returns a deviation warning so Iris
        can explain the gap on her next cycle.
      - If lesson_extracted is non-empty, INSERTs a low-confidence planner_lessons
        row in the same transaction (proposed; Iris validates later via lessons_manage).
    """
    try:
        ev = PlanEvaluation.model_validate(
            {
                "plan_id": plan_id,
                "outcome_score": outcome_score,
                "actual_outcome": actual_outcome,
                "lesson_extracted": lesson_extracted or None,
            }
        )
    except ValidationError as e:
        return json.dumps({"error": "PlanEvaluation validation failed", "details": json.loads(e.json())})

    conn = await _db()
    try:
        existing = await conn.fetchrow(
            "SELECT plan_id, hypothesis_structured FROM plan_journal WHERE plan_id = $1",
            ev.plan_id,
        )
        if not existing:
            return json.dumps({"error": f"Plan '{ev.plan_id}' not found in plan_journal"})

        async with conn.transaction():
            anchor_row = await conn.fetchrow("SELECT fn_plan_anchor_score($1) AS anchor", ev.plan_id)
            anchor_score = anchor_row["anchor"] if anchor_row else None
            guardrail_row = await conn.fetchrow(
                """
                SELECT guardrail_events, held_guardrail_events,
                       dispatched_guardrail_events, vpd_high_guardrail_events,
                       guardrail_penalty
                  FROM v_plan_guardrail_scorecard
                 WHERE plan_id = $1
                """,
                ev.plan_id,
            )
            # Persist the guardrail penalty alongside the outcome/anchor scores.
            # It was fetched (and surfaced in the return payload) but never
            # written back, so v_plan_guardrail_scorecard's signal could not
            # feed the reward swap (migration 147) or any downstream learning
            # query reading plan_journal directly. Default to 0 (no clamps in
            # the governed interval) when the scorecard has no row yet.
            guardrail_penalty = guardrail_row["guardrail_penalty"] if guardrail_row else 0

            await conn.execute(
                """UPDATE plan_journal SET
                    outcome_score = $2, actual_outcome = $3, lesson_extracted = $4,
                    anchor_score  = $5, guardrail_penalty = $6, validated_at = now()
                   WHERE plan_id = $1""",
                ev.plan_id,
                ev.outcome_score,
                ev.actual_outcome,
                ev.lesson_extracted,
                anchor_score,
                guardrail_penalty,
            )

            lesson_row_id = None
            if ev.lesson_extracted:
                # Lessonization (Phase 2a): convert lesson_extracted text into a
                # queryable planner_lessons row. Category derived from the plan's
                # dominant stress type during its governed interval; condition
                # derived from hypothesis_structured.conditions when present,
                # else a templated description.
                cat_row = await conn.fetchrow(
                    """
                    SELECT CASE
                             WHEN heat_stress_h     >= GREATEST(cold_stress_h, vpd_high_stress_h, vpd_low_stress_h)
                               THEN 'cooling'
                             WHEN cold_stress_h     >= GREATEST(heat_stress_h, vpd_high_stress_h, vpd_low_stress_h)
                               THEN 'heating'
                             WHEN vpd_high_stress_h >= GREATEST(heat_stress_h, cold_stress_h, vpd_low_stress_h)
                               THEN 'misting'
                             WHEN vpd_low_stress_h  >= GREATEST(heat_stress_h, cold_stress_h, vpd_high_stress_h)
                               THEN 'humidity'
                             ELSE 'planning'
                           END AS category
                      FROM v_plan_window_scorecard WHERE plan_id = $1
                    """,
                    ev.plan_id,
                )
                category = (cat_row["category"] if cat_row else None) or "planning"

                hs = existing["hypothesis_structured"]
                if hs and isinstance(hs, dict) and hs.get("conditions"):
                    c = hs["conditions"]
                    condition = (
                        f"outdoor_high={c.get('outdoor_temp_peak_f', '?')}F, "
                        f"outdoor_rh_min={c.get('outdoor_rh_min_pct', '?')}%, "
                        f"solar_peak={c.get('solar_peak_w_m2', '?')} W/m^2"
                    )
                else:
                    condition = f"auto-extracted from {ev.plan_id}"

                lesson_row = await conn.fetchrow(
                    """
                    INSERT INTO planner_lessons
                      (category, condition, lesson, confidence, times_validated,
                       source_plan_ids, is_active, greenhouse_id)
                    VALUES ($1, $2, $3, 'low', 1, ARRAY[$4]::text[], true, 'vallery')
                    RETURNING id
                    """,
                    category,
                    condition,
                    ev.lesson_extracted,
                    ev.plan_id,
                )
                lesson_row_id = lesson_row["id"] if lesson_row else None

        deviation = abs(ev.outcome_score - anchor_score) if anchor_score is not None else None
        warning = None
        if deviation is not None and deviation > 2:
            direction = "high" if ev.outcome_score > anchor_score else "low"
            warning = (
                f"Self-score {ev.outcome_score} deviates from deterministic anchor "
                f"{anchor_score} by {deviation} ({direction}). Explain the gap on "
                f"the next cycle, or revise your grade."
            )

        site_publish_triggered = False
        try:
            trigger_path = Path("/var/local/verdify/state/plan-publish-trigger")
            trigger_path.parent.mkdir(parents=True, exist_ok=True)
            trigger_path.write_text(f"evaluation:{ev.plan_id}\n{datetime.now(ZoneInfo('UTC')).isoformat()}\n")
            site_publish_triggered = True
        except Exception as e:  # never block evaluation persistence on publish trigger failures
            print(f"plan-evaluate publish trigger write failed (non-fatal): {e}")

        return json.dumps(
            {
                "ok": True,
                "plan_id": ev.plan_id,
                "outcome_score": ev.outcome_score,
                "anchor_score": anchor_score,
                "guardrail_scorecard": dict(guardrail_row) if guardrail_row else None,
                "deviation_warning": warning,
                "lesson_row_id": lesson_row_id,
                "site_publish_triggered": site_publish_triggered,
            }
        )
    finally:
        await conn.close()


# ═══════════════════════════════════════════════════════════════
# HISTORY TOOL
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
async def history(metric: str = "climate", hours: int = 24, resolution_min: int = 15) -> str:
    """Get historical time-bucketed data for any sensor domain.
    metric: 'climate' (temp, vpd, rh, dew_point), 'equipment' (relay state durations),
            'energy' (power watts), 'outdoor' (weather station), 'diagnostics' (ESP32 health)
    hours: lookback window (default 24)
    resolution_min: bucket size in minutes (default 15)
    Returns JSON array of time-bucketed records."""
    queries = {
        "climate": """
            SELECT time_bucket($1::interval, ts) AS time,
                   round(avg(temp_avg)::numeric, 1) AS temp_f,
                   round(avg(vpd_avg)::numeric, 2) AS vpd_kpa,
                   round(avg(rh_avg)::numeric, 0) AS rh_pct,
                   round(avg(dew_point)::numeric, 1) AS dew_point_f,
                   round(avg(outdoor_temp_f)::numeric, 1) AS outdoor_temp,
                   round(avg(outdoor_rh_pct)::numeric, 0) AS outdoor_rh
            FROM climate WHERE ts > now() - $2::interval AND temp_avg IS NOT NULL
            GROUP BY 1 ORDER BY 1""",
        "energy": """
            SELECT time_bucket($1::interval, ts) AS time,
                   round(avg(watts_total)::numeric, 0) AS watts
            FROM energy WHERE ts > now() - $2::interval
            GROUP BY 1 ORDER BY 1""",
        "outdoor": """
            SELECT time_bucket($1::interval, ts) AS time,
                   round(avg(outdoor_temp_f)::numeric, 1) AS temp_f,
                   round(avg(outdoor_rh_pct)::numeric, 0) AS rh_pct,
                   round(avg(solar_irradiance_w_m2)::numeric, 0) AS solar_w
            FROM climate WHERE ts > now() - $2::interval AND outdoor_temp_f IS NOT NULL
            GROUP BY 1 ORDER BY 1""",
        "equipment": """
            SELECT time_bucket($1::interval, e.ts) AS time,
                   e.equipment,
                   round(sum(CASE WHEN e.state THEN 1.0 ELSE 0.0 END) / count(*)::numeric * 100, 0) AS on_pct
            FROM equipment_state e
            WHERE e.ts > now() - $2::interval
              AND e.equipment IN ('fan1','fan2','vent','fog','heat1','heat2','mister_south','mister_west','mister_center')
            GROUP BY 1, 2 ORDER BY 1, 2""",
        "diagnostics": """
            SELECT time_bucket($1::interval, ts) AS time,
                   round(avg(wifi_rssi)::numeric, 0) AS wifi_rssi,
                   round(avg(heap_bytes)::numeric, 0) AS heap_bytes,
                   round(max(uptime_s)::numeric, 0) AS uptime_s
            FROM diagnostics WHERE ts > now() - $2::interval
            GROUP BY 1 ORDER BY 1""",
    }

    template = queries.get(metric)
    if not template:
        return json.dumps({"error": f"Unknown metric '{metric}'. Use: {', '.join(queries.keys())}"})

    # Inline interval values (asyncpg needs timedelta for interval params)
    sql = template.replace("$1::interval", f"'{resolution_min} minutes'::interval").replace(
        "$2::interval", f"'{hours} hours'::interval"
    )

    conn = await _db()
    try:
        rows = await conn.fetch(sql)
        return _json([dict(r) for r in rows[:500]])
    finally:
        await conn.close()


# ═══════════════════════════════════════════════════════════════
# CROP MANAGEMENT TOOLS
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
async def crops(action: str, crop_id: int = 0, data: str = "") -> str:
    """Manage greenhouse crops. Actions: list, get, create, update, deactivate.
    list: all active crops with zone, stage, recent health
    get: full detail for one crop including its nutrient recipe(s), observations,
         and events. For the Vanda (crop_id 5) this surfaces `vanda_orchid_active`
         (MSU 13-3-15, 50 ppm N, ec 0.40 ABSOLUTE on RO; is_active=FALSE until the
         operator confirms it). Dose to target_ec — NOT 2-part A/B ml/L math (SAF-2).
    create: data = {"name", "variety", "zone", "position", "planted_date", "stage", ...}
    update: data = {"name"?, "stage"?, "zone"?, "expected_harvest"?, "notes"?, ...}
    deactivate: soft-delete by crop_id"""
    d = json.loads(data) if data else {}
    conn = await _db()
    try:
        if action == "list":
            rows = await conn.fetch("""
                SELECT c.id, c.name, c.variety, c.zone, c.position, c.stage, c.planted_date,
                       c.expected_harvest, c.is_active,
                       (SELECT round(avg(health_score)::numeric, 2) FROM observations o
                        WHERE o.crop_id = c.id AND o.health_score IS NOT NULL
                        AND o.ts > now() - interval '7 days') AS health_7d
                FROM crops c WHERE c.is_active = true AND c.greenhouse_id = 'vallery'
                ORDER BY c.zone, c.position""")
            return _json([dict(r) for r in rows])

        elif action == "get" and crop_id:
            row = await conn.fetchrow("SELECT * FROM crops WHERE id = $1", crop_id)
            if not row:
                return json.dumps({"error": f"Crop {crop_id} not found"})
            obs = await conn.fetch(
                "SELECT ts, obs_type, notes, health_score FROM observations WHERE crop_id = $1 ORDER BY ts DESC LIMIT 10",
                crop_id,
            )
            events = await conn.fetch(
                "SELECT ts, event_type, old_stage, new_stage, notes FROM crop_events WHERE crop_id = $1 ORDER BY ts DESC LIMIT 10",
                crop_id,
            )
            # N1 (FRT-1): surface this crop's nutrient recipe(s) so the planner can
            # reason about feed without blind A/B dose math. For the Vanda (crop_id 5)
            # this exposes `vanda_orchid_active` (MSU 13-3-15, 50 ppm N, ec 0.40
            # ABSOLUTE on RO). The recipe ships is_active=FALSE until the operator
            # confirms it physically — `is_active` tells the planner whether the feed
            # is live or provisional; the notes carry the SAF-2 dosing guardrails
            # (single-salt, dose-to-EC not A/B ml/L, AM-only, absorption hold).
            recipes = await conn.fetch(
                """SELECT name, stage, is_active, target_ec, target_ph_low, target_ph_high,
                          n_ppm, p_ppm, k_ppm, ca_ppm, mg_ppm, fe_ppm,
                          stock_a_ml_per_l, stock_b_ml_per_l, notes
                     FROM nutrient_recipes WHERE crop_id = $1 ORDER BY is_active DESC, name""",
                crop_id,
            )
            return _json(
                {
                    "crop": dict(row),
                    "nutrient_recipes": [dict(r) for r in recipes],
                    "recent_observations": [dict(o) for o in obs],
                    "recent_events": [dict(e) for e in events],
                }
            )

        elif action == "create":
            try:
                payload = CropCreate.model_validate(d)
            except ValidationError as e:
                return json.dumps({"error": "CropCreate validation failed", "details": json.loads(e.json())})
            row = await conn.fetchrow(
                """
                INSERT INTO crops (name, variety, zone, position, planted_date, expected_harvest, stage,
                                   count, notes, seed_lot_id, supplier, base_temp_f,
                                   target_dli, target_vpd_low, target_vpd_high, greenhouse_id)
                VALUES ($1, $2, $3, $4, $5::date, $6::date, $7, $8, $9, $10, $11, $12,
                        $13, $14, $15, 'vallery') RETURNING *""",
                payload.name,
                payload.variety,
                payload.zone,
                payload.position,
                payload.planted_date,
                payload.expected_harvest,
                payload.stage,
                payload.count,
                payload.notes,
                payload.seed_lot_id,
                payload.supplier,
                payload.base_temp_f,
                payload.target_dli,
                payload.target_vpd_low,
                payload.target_vpd_high,
            )
            return _json(dict(row))

        elif action == "update" and crop_id:
            try:
                patch = CropUpdate.model_validate(d)
            except ValidationError as e:
                return json.dumps({"error": "CropUpdate validation failed", "details": json.loads(e.json())})
            set_fields = patch.model_dump(exclude_unset=True)
            if not set_fields:
                return json.dumps({"error": "No fields to update"})
            sets = [f"{k} = ${i}" for i, k in enumerate(set_fields, start=2)]
            sets.append("updated_at = now()")
            vals = [crop_id, *set_fields.values()]
            row = await conn.fetchrow(f"UPDATE crops SET {', '.join(sets)} WHERE id = $1 RETURNING *", *vals)
            return _json(dict(row)) if row else json.dumps({"error": "Crop not found"})

        elif action == "deactivate" and crop_id:
            await conn.execute("UPDATE crops SET is_active = false, updated_at = now() WHERE id = $1", crop_id)
            return json.dumps({"ok": True, "crop_id": crop_id, "action": "deactivated"})

        return json.dumps({"error": f"Unknown action '{action}'. Use: list, get, create, update, deactivate"})
    finally:
        await conn.close()


@mcp.tool()
async def observations(action: str, crop_id: int = 0, data: str = "") -> str:
    """Record and query crop observations, events, harvests, and treatments.
    Actions: list_observations, record_observation, list_events, record_event,
             record_harvest, list_harvests, record_treatment, list_treatments.
    data: JSON with fields appropriate to the action. Envelopes:
      record_observation -> ObservationCreate (obs_type, notes, severity, ...)
      record_event       -> EventCreate (event_type, old_stage, new_stage, ...)
      record_harvest     -> HarvestCreate (weight_kg, unit_count, quality_grade,
                            zone, destination, unit_price, revenue, operator, notes)
      record_treatment   -> TreatmentCreate (product, active_ingredient,
                            concentration, rate, rate_unit, method, zone,
                            target_pest, phi_days, rei_hours, applicator,
                            observation_id, followup_due_at,
                            followup_completed_at, outcome, notes)"""
    d = json.loads(data) if data else {}
    conn = await _db()
    try:
        if action == "list_observations":
            rows = await conn.fetch(
                """
                SELECT o.id, o.ts, o.greenhouse_id, o.crop_id, c.name,
                       o.position_id, o.zone_id, o.zone, o.position, o.obs_type,
                       o.notes, o.health_score, o.severity, o.observer
                FROM observations o
                JOIN crops c ON o.crop_id = c.id AND c.greenhouse_id = 'vallery'
                WHERE o.greenhouse_id = 'vallery'
                  AND ($1::int = 0 OR o.crop_id = $1)
                ORDER BY o.ts DESC LIMIT 50""",
                crop_id,
            )
            return _json([dict(r) for r in rows])

        elif action == "record_observation" and crop_id:
            crop = await conn.fetchrow(
                "SELECT zone, position, zone_id, position_id FROM crops WHERE id = $1 AND greenhouse_id = 'vallery'",
                crop_id,
            )
            if not crop:
                return json.dumps({"error": f"Crop {crop_id} not found"})
            try:
                obs = ObservationCreate.model_validate({**d, "observer": d.get("observer") or "Iris"})
            except ValidationError as e:
                return json.dumps({"error": "ObservationCreate validation failed", "details": json.loads(e.json())})
            row = await conn.fetchrow(
                """
                INSERT INTO observations (
                    crop_id, greenhouse_id, zone, position, zone_id, position_id, obs_type, notes, severity,
                    observer, health_score, species, count, affected_pct, photo_path,
                    plant_height_cm, leaf_count, canopy_cover_pct, flowering_count,
                    fruit_count, root_condition, mortality_count, stress_tags, source
                )
                VALUES (
                    $1, 'vallery', $2, $3, $4, $5, $6, $7, $8,
                    $9, $10, $11, $12, $13, $14,
                    $15, $16, $17, $18, $19, $20, $21, $22, 'iris'
                ) RETURNING *""",
                crop_id,
                obs.zone or crop["zone"],
                obs.position or crop["position"],
                crop["zone_id"],
                crop["position_id"],
                obs.obs_type,
                obs.notes,
                obs.severity,
                obs.observer,
                obs.health_score,
                obs.species,
                obs.count,
                obs.affected_pct,
                obs.photo_path,
                obs.plant_height_cm,
                obs.leaf_count,
                obs.canopy_cover_pct,
                obs.flowering_count,
                obs.fruit_count,
                obs.root_condition,
                obs.mortality_count,
                obs.stress_tags,
            )
            return _json(dict(row))

        elif action == "list_events":
            rows = await conn.fetch(
                """
                SELECT e.id, e.ts, e.greenhouse_id, e.crop_id, c.name,
                       e.position_id, e.event_type, e.old_stage, e.new_stage,
                       e.count, e.operator, e.source, e.notes
                FROM crop_events e
                JOIN crops c ON e.crop_id = c.id AND c.greenhouse_id = 'vallery'
                WHERE e.greenhouse_id = 'vallery'
                  AND ($1::int = 0 OR e.crop_id = $1)
                ORDER BY e.ts DESC LIMIT 50""",
                crop_id,
            )
            return _json([dict(r) for r in rows])

        elif action == "record_event" and crop_id:
            crop = await conn.fetchrow(
                "SELECT position_id FROM crops WHERE id = $1 AND greenhouse_id = 'vallery'",
                crop_id,
            )
            if not crop:
                return json.dumps({"error": f"Crop {crop_id} not found"})
            try:
                ev = EventCreate.model_validate({**d, "operator": d.get("operator") or "Iris"})
            except ValidationError as e:
                return json.dumps({"error": "EventCreate validation failed", "details": json.loads(e.json())})
            row = await conn.fetchrow(
                """
                INSERT INTO crop_events (
                    crop_id, greenhouse_id, position_id, event_type, old_stage,
                    new_stage, count, operator, source, notes
                )
                VALUES ($1, 'vallery', $2, $3, $4, $5, $6, $7, 'iris', $8) RETURNING *""",
                crop_id,
                crop["position_id"],
                ev.event_type,
                ev.old_stage,
                ev.new_stage,
                ev.count,
                ev.operator,
                ev.notes,
            )
            return _json(dict(row))

        elif action == "record_harvest" and crop_id:
            crop = await conn.fetchrow(
                "SELECT zone, position_id FROM crops WHERE id = $1 AND greenhouse_id = 'vallery'",
                crop_id,
            )
            if not crop:
                return json.dumps({"error": f"Crop {crop_id} not found"})
            try:
                hv = HarvestCreate.model_validate({**d, "operator": d.get("operator") or "Iris"})
            except ValidationError as e:
                return json.dumps({"error": "HarvestCreate validation failed", "details": json.loads(e.json())})
            row = await conn.fetchrow(
                """
                INSERT INTO harvests (
                    crop_id, weight_kg, unit_count, quality_grade,
                    salable_weight_kg, cull_weight_kg, cull_reason, quality_reason,
                    zone, destination, unit_price, revenue, labor_minutes, operator,
                    position_id, greenhouse_id, notes
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, 'vallery', $16)
                RETURNING *""",
                crop_id,
                hv.weight_kg,
                hv.unit_count,
                hv.quality_grade,
                hv.salable_weight_kg,
                hv.cull_weight_kg,
                hv.cull_reason,
                hv.quality_reason,
                hv.zone or crop["zone"],
                hv.destination,
                hv.unit_price,
                hv.revenue,
                hv.labor_minutes,
                hv.operator,
                crop["position_id"],
                hv.notes,
            )
            return _json(dict(row))

        elif action == "list_harvests":
            rows = await conn.fetch(
                """
                SELECT h.id, h.ts, h.greenhouse_id, h.crop_id, c.name, h.position_id,
                       h.weight_kg, h.unit_count, h.quality_grade,
                       h.salable_weight_kg, h.cull_weight_kg, h.cull_reason,
                       h.quality_reason, h.zone, h.destination, h.unit_price,
                       h.revenue, h.labor_minutes, h.operator, h.notes
                FROM harvests h JOIN crops c ON h.crop_id = c.id
                WHERE h.greenhouse_id = 'vallery'
                  AND c.greenhouse_id = 'vallery'
                  AND ($1::int = 0 OR h.crop_id = $1)
                ORDER BY h.ts DESC LIMIT 50""",
                crop_id,
            )
            return _json([dict(r) for r in rows])

        elif action == "record_treatment" and crop_id:
            crop = await conn.fetchrow(
                "SELECT zone, position_id FROM crops WHERE id = $1 AND greenhouse_id = 'vallery'",
                crop_id,
            )
            if not crop:
                return json.dumps({"error": f"Crop {crop_id} not found"})
            try:
                tr = TreatmentCreate.model_validate({**d, "applicator": d.get("applicator") or "Iris"})
            except ValidationError as e:
                return json.dumps({"error": "TreatmentCreate validation failed", "details": json.loads(e.json())})
            row = await conn.fetchrow(
                """
                INSERT INTO treatments (
                    crop_id, product, active_ingredient, concentration, rate, rate_unit,
                    method, zone, target_pest, phi_days, rei_hours, applicator,
                    observation_id, position_id, greenhouse_id, followup_due_at,
                    followup_completed_at, outcome, notes
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, 'vallery', $15, $16, $17, $18)
                RETURNING *""",
                crop_id,
                tr.product,
                tr.active_ingredient,
                tr.concentration,
                tr.rate,
                tr.rate_unit,
                tr.method,
                tr.zone or crop["zone"],
                tr.target_pest,
                tr.phi_days,
                tr.rei_hours,
                tr.applicator,
                tr.observation_id,
                crop["position_id"],
                tr.followup_due_at,
                tr.followup_completed_at,
                tr.outcome,
                tr.notes,
            )
            return _json(dict(row))

        elif action == "list_treatments":
            rows = await conn.fetch(
                """
                SELECT t.id, t.ts, t.greenhouse_id, t.crop_id, c.name, t.position_id,
                       t.product, t.active_ingredient, t.concentration, t.rate,
                       t.rate_unit, t.method, t.zone, t.target_pest, t.phi_days,
                       t.rei_hours, t.applicator, t.observation_id,
                       t.followup_due_at, t.followup_completed_at, t.outcome,
                       t.notes
                FROM treatments t JOIN crops c ON t.crop_id = c.id
                WHERE t.greenhouse_id = 'vallery'
                  AND c.greenhouse_id = 'vallery'
                  AND ($1::int = 0 OR t.crop_id = $1)
                ORDER BY t.ts DESC LIMIT 50""",
                crop_id,
            )
            return _json([dict(r) for r in rows])

        return json.dumps({"error": f"Unknown action '{action}'"})
    finally:
        await conn.close()


# ═══════════════════════════════════════════════════════════════
# ALERT MANAGEMENT
# ═══════════════════════════════════════════════════════════════


@mcp.tool()
async def alerts(action: str = "list", alert_id: int = 0, data: str = "") -> str:
    """Manage greenhouse alerts. Actions: list, acknowledge, resolve.
    list: active/recent alerts (last 24h by default)
    acknowledge: mark as seen by Iris
    resolve: close with resolution notes (data can be JSON or plain text)"""
    if data:
        try:
            d = json.loads(data)
        except (json.JSONDecodeError, ValueError):
            d = {"resolution": data}
    else:
        d = {}
    conn = await _db()
    try:
        if action == "list":
            hours = d.get("hours", 24)
            rows = await conn.fetch(
                """
                SELECT id, to_char(ts AT TIME ZONE 'America/Denver', 'MM-DD HH24:MI') AS time,
                       alert_type, severity, message, disposition,
                       acknowledged_at IS NOT NULL AS acknowledged,
                       resolved_at IS NOT NULL AS resolved
                FROM alert_log WHERE ts > now() - ($1::int || ' hours')::interval
                ORDER BY ts DESC LIMIT 50""",
                hours,
            )
            return _json([dict(r) for r in rows])

        elif action == "acknowledge" and alert_id:
            try:
                ack = AlertAckPayload.model_validate({"acknowledged_by": d.get("acknowledged_by") or "iris"})
            except ValidationError as e:
                return json.dumps({"error": "AlertAckPayload validation failed", "details": json.loads(e.json())})
            row = await conn.fetchrow(
                "UPDATE alert_log SET acknowledged_at = now(), acknowledged_by = $2, "
                "disposition = 'acknowledged' WHERE id = $1 AND resolved_at IS NULL "
                "RETURNING id, disposition",
                alert_id,
                ack.acknowledged_by,
            )
            if row is None:
                existing = await conn.fetchrow(
                    "SELECT id, disposition, resolved_at IS NOT NULL AS resolved FROM alert_log WHERE id = $1", alert_id
                )
                if existing and existing["resolved"]:
                    return json.dumps({"ok": True, "alert_id": alert_id, "action": "already_resolved"})
                if existing is None:
                    return json.dumps({"error": f"alert_id {alert_id} not found"})
            return json.dumps({"ok": True, "alert_id": alert_id, "action": "acknowledged"})

        elif action == "resolve" and alert_id:
            try:
                res = AlertResolvePayload.model_validate(
                    {
                        "resolved_by": d.get("resolved_by") or "iris",
                        "resolution": d.get("resolution") or "Resolved by Iris",
                    }
                )
            except ValidationError as e:
                return json.dumps({"error": "AlertResolvePayload validation failed", "details": json.loads(e.json())})
            await conn.execute(
                "UPDATE alert_log SET resolved_at = now(), resolved_by = $2, "
                "resolution = $3, disposition = 'resolved' WHERE id = $1",
                alert_id,
                res.resolved_by,
                res.resolution,
            )
            return json.dumps({"ok": True, "alert_id": alert_id, "action": "resolved"})

        return json.dumps({"error": f"Unknown action '{action}'. Use: list, acknowledge, resolve"})
    finally:
        await conn.close()


# ═══════════════════════════════════════════════════════════════
# LESSON MANAGEMENT
# ═══════════════════════════════════════════════════════════════


async def _lesson_state_row(conn, lesson_id: int) -> dict | None:
    """Fetch the columns needed to derive a lesson's lifecycle state.

    `last_validated > created_at` is the authoritative "validated beyond
    creation" signal; `times_validated` is the fallback the derive helper uses
    when that flag is unavailable. Returns None if the lesson doesn't exist.
    """
    return await conn.fetchrow(
        """
        SELECT id, is_active, superseded_by, times_validated,
               (last_validated IS NOT NULL AND created_at IS NOT NULL
                AND last_validated > created_at) AS has_independent_validation
          FROM planner_lessons WHERE id = $1
        """,
        lesson_id,
    )


@mcp.tool()
async def lessons_manage(action: str, lesson_id: int = 0, data: str = "") -> str:
    """Manage planner lessons (accumulated operational knowledge).

    Actions: create, update, deactivate, validate, supersede.
    Lifecycle state machine (G8): proposed -> validated -> superseded/retired.
    State is derived from is_active/superseded_by/validation history; illegal
    transitions (e.g. validating a superseded or retired lesson) are rejected.

    create: data = {"category", "condition", "lesson", "confidence": "low|medium|high"}
    update: data = {"lesson"?, "condition"?, "confidence"?}
    validate: proposed/validated -> validated; increment times_validated, optionally upgrade confidence
    deactivate: proposed/validated -> retired (terminal)
    supersede: proposed/validated -> superseded by a newer lesson;
        data = {"new_id": <existing lesson id>} (terminal)"""
    d = json.loads(data) if data else {}
    conn = await _db()
    try:
        if action == "create":
            try:
                payload = LessonCreate.model_validate(d)
            except ValidationError as e:
                return json.dumps({"error": "LessonCreate validation failed", "details": json.loads(e.json())})
            row = await conn.fetchrow(
                """
                INSERT INTO planner_lessons (category, condition, lesson, confidence, times_validated, is_active, greenhouse_id)
                VALUES ($1, $2, $3, $4, 1, true, 'vallery') RETURNING *""",
                payload.category,
                payload.condition,
                payload.lesson,
                payload.confidence,
            )
            return _json(dict(row))

        elif action == "update" and lesson_id:
            try:
                patch = LessonUpdate.model_validate(d)
            except ValidationError as e:
                return json.dumps({"error": "LessonUpdate validation failed", "details": json.loads(e.json())})
            set_fields = patch.model_dump(exclude_unset=True)
            if not set_fields:
                return json.dumps({"error": "No fields to update"})
            sets = [f"{k} = ${i}" for i, k in enumerate(set_fields, start=2)]
            vals = [lesson_id, *set_fields.values()]
            row = await conn.fetchrow(f"UPDATE planner_lessons SET {', '.join(sets)} WHERE id = $1 RETURNING *", *vals)
            return _json(dict(row)) if row else json.dumps({"error": "Lesson not found"})

        elif action == "deactivate" and lesson_id:
            cur = await _lesson_state_row(conn, lesson_id)
            if cur is None:
                return json.dumps({"error": "Lesson not found"})
            state = derive_lesson_state(
                is_active=cur["is_active"],
                superseded_by=cur["superseded_by"],
                times_validated=cur["times_validated"] or 1,
                has_independent_validation=bool(cur["has_independent_validation"]),
            )
            if not is_legal_lesson_transition(state, "retired"):
                return json.dumps(
                    {"error": f"Illegal transition {state} -> retired", "lesson_id": lesson_id, "state": state}
                )
            if state == "retired":  # idempotent
                return json.dumps({"ok": True, "lesson_id": lesson_id, "action": "deactivated", "state": "retired"})
            await conn.execute("UPDATE planner_lessons SET is_active = false WHERE id = $1", lesson_id)
            return json.dumps({"ok": True, "lesson_id": lesson_id, "action": "deactivated", "state": "retired"})

        elif action == "validate" and lesson_id:
            try:
                val = LessonValidate.model_validate(d) if d else LessonValidate()
            except ValidationError as e:
                return json.dumps({"error": "LessonValidate validation failed", "details": json.loads(e.json())})
            cur = await _lesson_state_row(conn, lesson_id)
            if cur is None:
                return json.dumps({"error": "Lesson not found"})
            state = derive_lesson_state(
                is_active=cur["is_active"],
                superseded_by=cur["superseded_by"],
                times_validated=cur["times_validated"] or 1,
                has_independent_validation=bool(cur["has_independent_validation"]),
            )
            if not is_legal_lesson_transition(state, "validated"):
                return json.dumps(
                    {"error": f"Illegal transition {state} -> validated", "lesson_id": lesson_id, "state": state}
                )
            if val.confidence:
                await conn.execute(
                    "UPDATE planner_lessons SET times_validated = times_validated + 1, "
                    "last_validated = now(), confidence = $2 WHERE id = $1",
                    lesson_id,
                    val.confidence,
                )
            else:
                await conn.execute(
                    "UPDATE planner_lessons SET times_validated = times_validated + 1, last_validated = now() WHERE id = $1",
                    lesson_id,
                )
            return json.dumps({"ok": True, "lesson_id": lesson_id, "action": "validated", "state": "validated"})

        elif action == "supersede" and lesson_id:
            try:
                sup = LessonSupersede.model_validate(d)
            except ValidationError as e:
                return json.dumps({"error": "LessonSupersede validation failed", "details": json.loads(e.json())})
            if sup.new_id == lesson_id:
                return json.dumps({"error": "A lesson cannot supersede itself", "lesson_id": lesson_id})
            cur = await _lesson_state_row(conn, lesson_id)
            if cur is None:
                return json.dumps({"error": "Lesson not found", "lesson_id": lesson_id})
            new_exists = await conn.fetchval("SELECT 1 FROM planner_lessons WHERE id = $1", sup.new_id)
            if new_exists is None:
                return json.dumps({"error": f"Superseding lesson {sup.new_id} not found", "new_id": sup.new_id})
            state = derive_lesson_state(
                is_active=cur["is_active"],
                superseded_by=cur["superseded_by"],
                times_validated=cur["times_validated"] or 1,
                has_independent_validation=bool(cur["has_independent_validation"]),
            )
            if not is_legal_lesson_transition(state, "superseded"):
                return json.dumps(
                    {"error": f"Illegal transition {state} -> superseded", "lesson_id": lesson_id, "state": state}
                )
            # superseded lessons drop out of the live set (is_active=true AND
            # superseded_by IS NULL); also flip is_active=false for clarity.
            await conn.execute(
                "UPDATE planner_lessons SET superseded_by = $2, is_active = false WHERE id = $1",
                lesson_id,
                sup.new_id,
            )
            return json.dumps(
                {
                    "ok": True,
                    "lesson_id": lesson_id,
                    "action": "superseded",
                    "superseded_by": sup.new_id,
                    "state": "superseded",
                }
            )

        return json.dumps({"error": f"Unknown action '{action}'. Use: create, update, deactivate, validate, supersede"})
    finally:
        await conn.close()


# ═══════════════════════════════════════════════════════════════
# Sprint 23 — Topology + crop-history tools for Iris
# ═══════════════════════════════════════════════════════════════
#
# These expose the topology tables (zones/shelves/positions/equipment)
# and the crop-history views to Iris, so planning decisions can reference
# "what is currently at SOUTH-FLOOR-1" instead of opaque zone strings.


@mcp.tool()
async def topology() -> str:
    """Return the full greenhouse → zones → shelves → positions tree.

    Use this when you need to know the physical layout: what zones exist,
    what shelves each zone contains, and what position slots are
    defined. The planner uses this to validate setpoint scope (e.g.,
    per-zone VPD targets) and the website uses it for navigation.
    """
    conn = await _db()
    try:
        row = await conn.fetchrow(
            "SELECT greenhouse_id, greenhouse_name, zones FROM v_topology_tree WHERE greenhouse_id = 'vallery'"
        )
        if row is None:
            return _json({"error": "topology not available"})
        # asyncpg returns JSONB as str unless a codec is registered
        z = row["zones"]
        zones = json.loads(z) if isinstance(z, str) else z
        return _json(
            {
                "greenhouse_id": row["greenhouse_id"],
                "greenhouse_name": row["greenhouse_name"],
                "zones": zones,
            }
        )
    finally:
        await conn.close()


@mcp.tool()
async def position_current(zone_slug: str = "") -> str:
    """Return the current occupancy of every position (and which crop, if any).

    Args:
        zone_slug: optional — narrow to one zone (south, north, east, west, center).

    Each row: position_label, crop_name, crop_stage, crop_days_in_place, is_occupied.
    Use this to see "what is planted where right now."
    """
    conn = await _db()
    try:
        if zone_slug:
            rows = await conn.fetch(
                "SELECT * FROM v_position_current WHERE greenhouse_id = 'vallery' AND zone_slug = $1",
                zone_slug,
            )
        else:
            rows = await conn.fetch("SELECT * FROM v_position_current WHERE greenhouse_id = 'vallery'")
        return _json([dict(r) for r in rows])
    finally:
        await conn.close()


@mcp.tool()
async def crop_history(position_id: int = 0) -> str:
    """Return the chronological crop history at a given position.

    Args:
        position_id: the integer position_id. Use `position_current()` to find it.

    Returns every crop that has ever been at this position, newest first, with
    planted_date, cleared_at, final_stage, days_in_place, observation_count,
    and harvest_count. Includes both active and historical rows.
    """
    conn = await _db()
    try:
        if not position_id:
            return _json({"error": "position_id required"})
        rows = await conn.fetch(
            """
            SELECT * FROM v_crop_history
            WHERE position_id = $1 AND greenhouse_id = 'vallery'
            ORDER BY planted_date DESC
            """,
            position_id,
        )
        return _json([dict(r) for r in rows])
    finally:
        await conn.close()


@mcp.tool()
async def crop_lifecycle(crop_id: int) -> str:
    """Return a single crop's full lifecycle timeline.

    Args:
        crop_id: integer crop id.

    Returns: planted_date, cleared_at, current_stage, days_alive, event timeline
    (planted/stage_change/transplanted/removed/harvested), harvest totals
    (weight_kg, units, revenue), observation count + avg health score.
    The authoritative per-crop summary for planning decisions and
    retrospective evaluation.
    """
    conn = await _db()
    try:
        row = await conn.fetchrow(
            "SELECT * FROM v_crop_lifecycle WHERE crop_id = $1 AND greenhouse_id = 'vallery'",
            crop_id,
        )
        if row is None:
            return _json({"error": f"crop {crop_id} not found"})
        d = dict(row)
        # Unpack the JSONB events array
        ev = d.get("events")
        if isinstance(ev, str):
            d["events"] = json.loads(ev)
        return _json(d)
    finally:
        await conn.close()


# ═══════════════════════════════════════════════════════════════
# VECTORIZED RETRIEVAL (Phase 3, migration 112)
# ═══════════════════════════════════════════════════════════════
#
# lessons_search and knowledge_search embed the query via OpenAI
# text-embedding-3-large (3072-dim) and call fn_search_embeddings()
# against the verdify_embeddings table. Both tools fail gracefully if
# OPENAI_API_KEY is unset, returning a clear error instead of crashing.


_OPENAI_EMBED_MODEL = "text-embedding-3-large"
_OPENAI_EMBED_DIM = 3072


def _openai_api_key() -> str | None:
    key = os.environ.get("OPENAI_API_KEY")
    if key:
        return key

    for path in _OPENAI_KEY_FILES:
        try:
            if not path.exists():
                continue
            text = path.read_text().strip()
        except OSError:
            continue

        if path.name.endswith(".env"):
            for raw_line in text.splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line.removeprefix("export ").strip()
                if not line.startswith("OPENAI_API_KEY="):
                    continue
                candidate = line.split("=", 1)[1].strip().strip('"').strip("'")
                if candidate:
                    return candidate
        elif text:
            return text

    return None


async def _embed_query(text: str) -> list[float] | None:
    """Embed a query string for vector retrieval. None on failure."""
    api_key = _openai_api_key()
    if not api_key:
        return None
    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        # Sync call wrapped in a worker thread; the OpenAI Python SDK has an
        # async client too but we keep the import surface minimal here.
        import asyncio as _asyncio

        resp = await _asyncio.to_thread(
            client.embeddings.create,
            model=_OPENAI_EMBED_MODEL,
            input=text,
            dimensions=_OPENAI_EMBED_DIM,
        )
        return list(resp.data[0].embedding)
    except Exception as exc:  # pragma: no cover — surface failure to caller
        print(f"[mcp.embed_query] failed: {exc}", file=sys.stderr)
        return None


def _vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"


@mcp.tool()
async def lessons_search(query: str, top_k: int = 10, min_confidence: str = "low") -> str:
    """Semantic search across planner_lessons.

    Use this to pull the lessons most relevant to a *forward-looking* condition
    (e.g. "hot dry day with 1100 W/m² solar peak") rather than relying on the
    static top-10-by-confidence list the prompt context surfaces by default.

    Args:
        query: free-text description of the conditions or topic you care about
        top_k: max results (default 10, cap 25)
        min_confidence: 'low' | 'medium' | 'high' — filter by minimum confidence
            of the underlying planner_lessons row. Most lessons are 'low' so
            the default is permissive.

    Returns: JSON array of {id, category, condition, lesson, confidence,
    times_validated, distance} sorted by ascending cosine distance.
    """
    top_k = max(1, min(int(top_k), 25))
    embedding = await _embed_query(query)
    if embedding is None:
        return json.dumps({"error": "lessons_search requires OPENAI_API_KEY for query embedding"})

    rank_floor = {"low": 1, "medium": 2, "high": 3}.get(min_confidence, 1)
    conn = await _db()
    try:
        rows = await conn.fetch(
            """
            WITH hits AS (
              SELECT source_id, content, metadata, distance
                FROM fn_search_embeddings($1::vector, $2, ARRAY['lesson']::text[])
            )
            SELECT pl.id, pl.category, pl.condition, pl.lesson, pl.confidence,
                   pl.times_validated, pl.is_active, h.distance
              FROM hits h
              JOIN planner_lessons pl ON pl.id::text = h.source_id
             WHERE pl.is_active = true AND pl.superseded_by IS NULL
               AND CASE pl.confidence WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END >= $3
             ORDER BY h.distance
            """,
            _vector_literal(embedding),
            top_k,
            rank_floor,
        )
        return _json([dict(r) for r in rows])
    finally:
        await conn.close()


@mcp.tool()
async def knowledge_search(
    query: str,
    top_k: int = 8,
    source_types: str = "lesson,plan,site_doc,playbook,observation",
) -> str:
    """Semantic search across docs, playbook, historical plans, lessons, and observations.

    Use this when you need reference-level knowledge: "what does the playbook
    say about vent oscillation?", "summarize the controller mode hierarchy",
    "have I seen a 1100 W/m² solar day before, and what did I try?". The
    source_types argument lets you scope the search:

      site_doc — public website Markdown plus operator-facing docs in docs/**/*.md
      playbook — the planner playbook + skills mirror (chunked by heading)
      plan     — past plan_journal hypotheses + actual_outcome rows
      lesson   — planner_lessons rows (same corpus as lessons_search)
      observation — historical crop observations, health notes, and stress tags

    Args:
        query: free-text query
        top_k: max results (default 8, cap 25)
        source_types: comma-separated subset of the five sources

    Returns: JSON array of {source_type, source_id, content, metadata, distance}.
    """
    top_k = max(1, min(int(top_k), 25))
    types = [s.strip() for s in source_types.split(",") if s.strip()]
    valid = {"lesson", "plan", "site_doc", "playbook", "observation"}
    types = [t for t in types if t in valid]
    if not types:
        return json.dumps(
            {"error": "source_types must include at least one of: lesson, plan, site_doc, playbook, observation"}
        )

    embedding = await _embed_query(query)
    if embedding is None:
        return json.dumps({"error": "knowledge_search requires OPENAI_API_KEY for query embedding"})

    conn = await _db()
    try:
        rows = await conn.fetch(
            """
            SELECT source_type, source_id, chunk_idx, content, metadata, distance
              FROM fn_search_embeddings($1::vector, $2, $3::text[]) h
             WHERE h.source_type <> 'lesson'
                OR EXISTS (
                     SELECT 1
                       FROM planner_lessons pl
                      WHERE pl.id::text = h.source_id
                        AND pl.is_active = true
                        AND pl.superseded_by IS NULL
                   )
            """,
            _vector_literal(embedding),
            top_k,
            types,
        )
        return _json([dict(r) for r in rows])
    finally:
        await conn.close()


# ═══════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    os.environ.setdefault("MCP_HTTP_HOST", "127.0.0.1")
    os.environ.setdefault("MCP_HTTP_PORT", "8000")
    mcp.run(transport="streamable-http")
