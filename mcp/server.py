#!/usr/bin/env /srv/greenhouse/.venv/bin/python3
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
    LessonUpdate,
    LessonValidate,
    ObservationCreate,
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
    TreatmentCreate,
)
from verdify_schemas.climate_intent import (  # noqa: E402
    CLIMATE_INTENT_CONTRACT_VERSION,
    ClimateIntent,
    materialize_climate_intent_tier1,
)
from verdify_schemas.tunable_registry import (  # noqa: E402
    BAND_OWNED_REG,
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
DB_DSN = os.environ.get("DB_DSN", f"postgresql://verdify:{_db_pass}@localhost:5432/verdify")
# Legacy planner.py removed — planning runs via iris_planner.py → Hermes /v1/runs
BAND_OWNED_PARAMS = BAND_OWNED_REG
_OPENAI_KEY_FILES = (
    Path("/etc/verdify/hermes-iris.env"),
    Path("/mnt/agents/shared/credentials/openai_api_key.txt"),
)
PLAN_REQUIRED_PARAMS = TIER1_REG
TIER1_TUNABLES = TIER1_REG

FORCED_ON_SWITCH_PARAMS = frozenset({"sw_fsm_controller_enabled"})


def _contains_climate_intent_waypoint(waypoints: object) -> bool:
    return isinstance(waypoints, list) and any(isinstance(wp, dict) and "climate_intent" in wp for wp in waypoints)


async def _fetch_active_tier1_params(conn: asyncpg.Connection) -> dict[str, float]:
    rows = await conn.fetch(
        """
        SELECT parameter, value
          FROM v_active_plan
         WHERE parameter = ANY($1::text[])
        """,
        sorted(TIER1_REG),
    )
    return {str(row["parameter"]): float(row["value"]) for row in rows}


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
        explicit_params = wp.get("params") if isinstance(wp.get("params"), dict) else {}
        materialized = materialize_climate_intent_tier1(intent, {**active_tier1_params, **explicit_params})
        expanded_wp = dict(wp)
        expanded_wp["params"] = materialized
        expanded.append(expanded_wp)
        intent_records.append(
            {
                "ts": wp.get("ts"),
                "reason": wp.get("reason"),
                "climate_intent": intent.model_dump(mode="json"),
                "materialized_params": materialized,
            }
        )
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
    Includes: planner_score, compliance_pct (both in firmware-enforced band),
    temp_compliance_pct, vpd_compliance_pct, stress hours
    (heat/cold/vpd_high/vpd_low), utility usage
    (kwh, therms, water_gal, mister_water_gal), costs (electric/gas/water/total),
    dew point safety, and 7-day averages. Pass date as YYYY-MM-DD or omit for today.

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
    import sys

    sys.path.insert(0, "/srv/verdify/ingestor")
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
    transitions: JSON array of objects: [{"ts": "ISO8601-with-TZ", "params": {"param": value, ...}, "reason": "..."}]
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

    climate_intent_records: list[dict[str, object]] = []
    if _contains_climate_intent_waypoint(waypoints_raw):
        if not isinstance(waypoints_raw, list):
            return json.dumps({"error": "transitions must be a JSON array"})
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

    params_seen = sorted({param for wp in plan.transitions for param in wp.params if param not in BAND_OWNED_PARAMS})
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
                if delivery["status"] != "pending":
                    return json.dumps(
                        {
                            "error": "trigger_id is not pending",
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
                       AND status = 'pending'
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
            "forced_on_params": forced_on_params,
            "climate_intent_segments": len(climate_intent_records),
            "climate_intent_version": CLIMATE_INTENT_CONTRACT_VERSION if climate_intent_records else None,
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

            await conn.execute(
                """UPDATE plan_journal SET
                    outcome_score = $2, actual_outcome = $3, lesson_extracted = $4,
                    anchor_score  = $5, validated_at = now()
                   WHERE plan_id = $1""",
                ev.plan_id,
                ev.outcome_score,
                ev.actual_outcome,
                ev.lesson_extracted,
                anchor_score,
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
    get: full detail for one crop including observations and events
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
            return _json(
                {
                    "crop": dict(row),
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


@mcp.tool()
async def lessons_manage(action: str, lesson_id: int = 0, data: str = "") -> str:
    """Manage planner lessons (accumulated operational knowledge).
    Actions: create, update, deactivate, validate.
    create: data = {"category", "condition", "lesson", "confidence": "low|medium|high"}
    update: data = {"lesson"?, "condition"?, "confidence"?}
    deactivate: mark lesson as inactive
    validate: increment times_validated, optionally upgrade confidence"""
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
            await conn.execute("UPDATE planner_lessons SET is_active = false WHERE id = $1", lesson_id)
            return json.dumps({"ok": True, "lesson_id": lesson_id, "action": "deactivated"})

        elif action == "validate" and lesson_id:
            try:
                val = LessonValidate.model_validate(d) if d else LessonValidate()
            except ValidationError as e:
                return json.dumps({"error": "LessonValidate validation failed", "details": json.loads(e.json())})
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
            return json.dumps({"ok": True, "lesson_id": lesson_id, "action": "validated"})

        return json.dumps({"error": f"Unknown action '{action}'. Use: create, update, deactivate, validate"})
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
