#!/usr/bin/env python3
"""Requirement-level audit for irrigation/fertigation completion.

Default exit status is intentionally strict:
  0 = software checks pass and physical feedback gate is ok
  1 = at least one requirement is failed or still physically blocked
  2 = audit could not run

Use --software-only to prove the deployable software/dashboard pieces while
south probe repair and center feedback hardware are still in progress.
"""

from __future__ import annotations

import argparse
import ast
import html
import json
import re
import shutil
import subprocess
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SITE_PUBLIC_ROOT = Path("/srv/verdify/verdify-site/public")
SITE_IRRIGATION_HTML = Path("/srv/verdify/verdify-site/public/greenhouse/irrigation.html")
EXPECTED_PUBLIC_GATEWAY = "gateway.verdify.ai."
PUBLIC_SITE_BASES = (
    "https://lab.verdify.ai",
    "https://labs.verdify.ai",
)
PUBLIC_DNS_RESOLVERS = ("1.1.1.1", "8.8.8.8")
LEGACY_IRRIGATION_RETIREMENT_TS = "2026-05-21 00:00:00+00"
EXPECTED_DATA_TRUST_IRRIGATION_DETAIL = (
    "fertigation starts in equipment_state without canonical run rows in last 14 days"
)

IRRIGATION_SCHEDULE_PARAMS = (
    "irrig_wall_start_hour",
    "irrig_wall_start_min",
    "irrig_wall_duration_min",
    "irrig_wall_fert_duration_min",
    "irrig_wall_fert_every_n",
    "irrig_wall_days_mask",
    "irrig_wall_fert_days_mask",
    "irrig_wall_flush_min",
    "irrig_wall_interval_days",
    "irrig_center_start_hour",
    "irrig_center_start_min",
    "irrig_center_duration_min",
    "irrig_center_fert_duration_min",
    "irrig_center_fert_every_n",
    "irrig_center_days_mask",
    "irrig_center_fert_days_mask",
    "irrig_center_flush_min",
    "irrig_center_interval_days",
)

FEEDBACK_FIELD_REQUIREMENTS = (
    "south_soil_probe_1_repair",
    "center_root_zone_runoff_feedback",
)

FEEDBACK_REGISTRY_COLUMNS = (
    "soil_moisture_south_1",
    "soil_ec_south_1",
    "soil_temp_south_1",
    "moisture_center",
    "ph_runoff_center",
    "ec_runoff_center",
)

FEEDBACK_VALIDATION_EQUIPMENT = (
    "south_soil_probe_1",
    "center_root_zone_runoff_feedback",
)

REQUIRED_DASHBOARD_PANELS = {
    "Canonical Schedule": (
        "v_irrigation_schedule_current",
        "stale_readback_count",
        "readback_drift_count",
        "zone_path",
    ),
    "Last Run Age": ("v_irrigation_fertigation_runs", "run_end"),
    "Master Overlap": ("v_irrigation_fertigation_runs", "fert_master_overlap_min", "fert_duration_min"),
    "Fertigation Water Today": ("v_irrigation_fertigation_runs", "meter_delta_gal"),
    "Flagged Runs": ("v_irrigation_fertigation_runs", "quality_flag"),
    "Latest Fertigation Runs": ("fert_relay", "flush_relay", "quality_flag", "meter_delta_gal"),
    "Daily Irrigation Water": ("v_irrigation_program_daily", "meter_delta_gal", "flagged_events"),
    "Daily Irrigation Runtime": (
        "daily_summary",
        "runtime_irrigation_clean_h",
        "runtime_irrigation_fert_h",
        "runtime_fert_master_h",
    ),
    "Relay State": (
        "equipment_state",
        "generate_series",
        "state_seed",
        "state_timeline",
        "state_segments",
        "COALESCE(seed.state, false)",
        "drip_wall_fert",
        "drip_center_fert",
        "mister_south_fert",
        "mister_west_fert",
        "fert_master_valve",
    ),
    "Root-Zone Response": ("climate", "soil_moisture_south_1", "soil_moisture_south_2", "moisture_center"),
    "Flow And Meter": ("climate", "flow_gpm", "water_total_gal"),
    "Feedback Sensor Gaps": ("v_irrigation_sensor_feedback_status", "required_action"),
    "Feedback Field Work": ("instrumentation_requirements", "maintenance_log", "south_soil_probe_1_repair"),
    "Feedback Registry Targets": ("sensor_registry", "moisture_center", "ec_runoff_center", "soil_moisture_south_1"),
    "Feedback Acceptance Closure": (
        "v_irrigation_sensor_feedback_status",
        "alert_log",
        "instrumentation_requirements",
        "sensor_registry",
        "maintenance_log",
        "service_type = 'validation'",
    ),
}


@dataclass
class Check:
    name: str
    status: str
    detail: str

    @property
    def failed(self) -> bool:
        return self.status == "fail"

    @property
    def blocked(self) -> bool:
        return self.status == "blocked"


def _run(cmd: list[str], timeout: int = 45) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def _psql(sql: str, *, direct_db: bool = False) -> list[list[str]]:
    if direct_db and shutil.which("psql"):
        cmd = ["psql", "-t", "-A", "-F", "\t", "-c", sql]
    else:
        cmd = [
            "docker",
            "exec",
            "verdify-timescaledb",
            "psql",
            "-U",
            "verdify",
            "-d",
            "verdify",
            "-t",
            "-A",
            "-F",
            "\t",
            "-c",
            sql,
        ]
    result = _run(cmd)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip())
    return [line.split("\t") for line in result.stdout.splitlines() if line.strip()]


def _psql_exec(sql: str, *, direct_db: bool = False) -> subprocess.CompletedProcess[str]:
    if direct_db and shutil.which("psql"):
        cmd = ["psql", "-v", "ON_ERROR_STOP=1", "-q", "-c", sql]
    else:
        cmd = [
            "docker",
            "exec",
            "verdify-timescaledb",
            "psql",
            "-U",
            "verdify",
            "-d",
            "verdify",
            "-v",
            "ON_ERROR_STOP=1",
            "-q",
            "-c",
            sql,
        ]
    return _run(cmd)


def _scalar(sql: str, *, direct_db: bool = False) -> str:
    rows = _psql(sql, direct_db=direct_db)
    if not rows or not rows[0]:
        return ""
    return rows[0][0]


def _check_legacy_retired(direct_db: bool) -> Check:
    count = int(_scalar("SELECT count(*) FROM irrigation_schedule WHERE enabled IS TRUE", direct_db=direct_db) or "0")
    legacy_log_rows_since_retirement = int(
        _scalar(
            f"SELECT count(*) FROM irrigation_log WHERE ts >= TIMESTAMPTZ '{LEGACY_IRRIGATION_RETIREMENT_TS}'",
            direct_db=direct_db,
        )
        or "0"
    )
    comments = {
        name: comment
        for name, comment in _psql(
            """
            SELECT relname, COALESCE(obj_description(c.oid, 'pg_class'), '')
              FROM pg_class c
             WHERE relname IN ('irrigation_schedule', 'irrigation_log', 'v_irrigation_log')
            """,
            direct_db=direct_db,
        )
    }
    comments_ok = all(
        "Retired compatibility" in comments.get(name, "")
        for name in ("irrigation_schedule", "irrigation_log", "v_irrigation_log")
    )
    viewdef = _scalar(
        "SELECT replace(pg_get_viewdef('v_irrigation_log'::regclass, true), E'\\n', ' ')",
        direct_db=direct_db,
    )
    canonical_log_view_ok = "v_irrigation_fertigation_runs" in viewdef and not re.search(
        r"\bFROM\s+(?:public\.)?irrigation_log\b", viewdef, flags=re.IGNORECASE
    )
    retired_view_deps = _psql(
        """
        WITH retired AS (
          SELECT c.oid, n.nspname, c.relname
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname = 'public'
             AND c.relname IN ('irrigation_schedule', 'irrigation_log')
        )
        SELECT DISTINCT
               rn.nspname || '.' || rv.relname AS retired_object,
               dn.nspname || '.' || dv.relname AS dependent_object
          FROM pg_depend dep
          JOIN pg_rewrite rw ON rw.oid = dep.objid
          JOIN pg_class dv ON dv.oid = rw.ev_class
          JOIN pg_namespace dn ON dn.oid = dv.relnamespace
          JOIN retired r ON r.oid = dep.refobjid
          JOIN pg_class rv ON rv.oid = r.oid
          JOIN pg_namespace rn ON rn.oid = rv.relnamespace
         WHERE dv.oid <> r.oid
         ORDER BY retired_object, dependent_object
        """,
        direct_db=direct_db,
    )
    retired_view_deps_ok = len(retired_view_deps) == 0
    retired_view_deps_detail = ",".join(f"{row[0]}->{row[1]}" for row in retired_view_deps) or "-"
    guard_trigger_count = int(
        _scalar(
            """
            SELECT count(DISTINCT event_object_table || ':' || trigger_name)
              FROM information_schema.triggers
             WHERE event_object_schema = 'public'
               AND event_object_table IN ('irrigation_schedule', 'irrigation_log')
               AND trigger_name IN (
                    'block_retired_irrigation_schedule_write',
                    'block_retired_irrigation_log_write'
               )
               AND action_statement LIKE '%prevent_retired_irrigation_compat_write%'
            """,
            direct_db=direct_db,
        )
        or "0"
    )
    guard_function = _scalar(
        """
        SELECT replace(COALESCE(pg_get_functiondef(to_regprocedure('prevent_retired_irrigation_compat_write()')), ''), E'\n', ' ')
        """,
        direct_db=direct_db,
    )
    guard_function_ok = (
        "verdify.allow_retired_irrigation_compat_write" in guard_function
        and "retired irrigation compatibility table" in guard_function
    )
    schedule_probe = _psql_exec(
        """
        BEGIN;
        INSERT INTO irrigation_schedule (zone, start_time, duration_s, days_of_week, enabled, notes)
        VALUES ('center', '23:59', 60, ARRAY[0], false, 'write guard probe');
        ROLLBACK;
        """,
        direct_db=direct_db,
    )
    log_probe = _psql_exec(
        """
        BEGIN;
        INSERT INTO irrigation_log (ts, zone, actual_start, actual_end, source, notes)
        VALUES (now(), 'center', now(), now(), 'manual', 'write guard probe');
        ROLLBACK;
        """,
        direct_db=direct_db,
    )
    probe_output = f"{schedule_probe.stdout}{schedule_probe.stderr}\n{log_probe.stdout}{log_probe.stderr}"
    write_guard_behavior_ok = (
        schedule_probe.returncode != 0
        and log_probe.returncode != 0
        and probe_output.count("retired irrigation compatibility table") >= 2
    )
    status = (
        "pass"
        if (
            count == 0
            and legacy_log_rows_since_retirement == 0
            and comments_ok
            and canonical_log_view_ok
            and retired_view_deps_ok
            and guard_trigger_count == 2
            and guard_function_ok
            and write_guard_behavior_ok
        )
        else "fail"
    )
    return Check(
        "legacy schedule/log retired",
        status,
        (
            f"{count} enabled legacy irrigation_schedule row(s); "
            f"legacy_log_rows_since_retirement={legacy_log_rows_since_retirement} "
            f"retirement_ts={LEGACY_IRRIGATION_RETIREMENT_TS}; comments_ok={comments_ok} "
            f"canonical_log_view_ok={canonical_log_view_ok} "
            f"retired_view_deps={retired_view_deps_detail} "
            f"write_guard_triggers={guard_trigger_count}/2 guard_function_ok={guard_function_ok} "
            f"write_guard_behavior_ok={write_guard_behavior_ok}"
        ),
    )


def _check_data_trust_ledger_canonical_irrigation(direct_db: bool) -> Check:
    viewdef = _scalar(
        "SELECT replace(pg_get_viewdef('v_data_trust_ledger'::regclass, true), E'\\n', ' ')",
        direct_db=direct_db,
    )
    legacy_refs = []
    for token in ("irrigation_log", "irrigation_schedule"):
        if re.search(rf"\bFROM\s+(?:public\.)?{token}\b", viewdef, flags=re.IGNORECASE):
            legacy_refs.append(token)
    uses_runs = "v_irrigation_fertigation_runs" in viewdef
    rows = _psql(
        """
        SELECT status, metric_value::text, details
          FROM v_data_trust_ledger
         WHERE check_name = 'irrigation_logging_14d'
        """,
        direct_db=direct_db,
    )
    if not rows:
        return Check(
            "data trust ledger canonical irrigation logging",
            "fail",
            "missing irrigation_logging_14d row",
        )
    row_status, metric_value, details = rows[0]
    canonical_detail = details == EXPECTED_DATA_TRUST_IRRIGATION_DETAIL
    status = (
        "pass"
        if row_status == "ok"
        and metric_value in ("0", "0.0", "0.00")
        and uses_runs
        and canonical_detail
        and not legacy_refs
        else "fail"
    )
    return Check(
        "data trust ledger canonical irrigation logging",
        status,
        (
            f"row_status={row_status} metric={metric_value} uses_runs={uses_runs} "
            f"canonical_detail={canonical_detail} legacy_refs={legacy_refs or '-'}"
        ),
    )


def _check_schedule_current(direct_db: bool) -> Check:
    rows = _psql(
        """
        SELECT schedule_id,
               zone_path,
               enabled::text,
               start_time::text,
               clean_duration_min::text,
               fert_duration_min::text,
               flush_min::text,
               schedule_source,
               stale_readback_count::text,
               readback_drift_count::text,
               array_to_string(serves_zones, ',')
          FROM v_irrigation_schedule_current
         ORDER BY schedule_id
        """,
        direct_db=direct_db,
    )
    by_id = {row[0]: row for row in rows}
    missing = sorted({"center", "wall_shared"} - set(by_id))
    wall = by_id.get("wall_shared")
    stale = sum(int(row[8]) for row in rows)
    drift = sum(int(row[9]) for row in rows)
    topology_ok = wall is not None and wall[10] == "south,west" and wall[1] == "wall_shared"
    enabled_ok = all(row[2] == "true" for row in rows)
    status = "pass" if not missing and topology_ok and enabled_ok and stale == 0 and drift == 0 else "fail"
    detail = (
        f"rows={len(rows)} missing={missing or '-'} enabled_ok={enabled_ok} "
        f"wall_serves={wall[10] if wall else '-'} stale_readbacks={stale} readback_drift={drift}"
    )
    return Check("current schedule view", status, detail)


def _check_schedule_cfg_readbacks(direct_db: bool) -> Check:
    values_sql = ",".join(f"('{param}')" for param in IRRIGATION_SCHEDULE_PARAMS)
    row = _psql(
        f"""
        WITH params(parameter) AS (VALUES {values_sql}),
        latest AS (
            SELECT DISTINCT ON (parameter) parameter, value, ts
              FROM setpoint_snapshot
             WHERE parameter IN (SELECT parameter FROM params)
             ORDER BY parameter, ts DESC
        )
        SELECT count(l.parameter)::text,
               count(l.parameter) FILTER (WHERE l.ts >= now() - interval '15 minutes')::text,
               COALESCE(min(l.ts)::text, '-'),
               COALESCE(max(l.ts)::text, '-'),
               COALESCE(string_agg(p.parameter, ',' ORDER BY p.parameter) FILTER (WHERE l.parameter IS NULL), '-')
          FROM params p
          LEFT JOIN latest l USING (parameter)
        """,
        direct_db=direct_db,
    )[0]
    present, fresh, min_ts, max_ts, missing = row
    expected = len(IRRIGATION_SCHEDULE_PARAMS)
    status = "pass" if int(present) == expected and int(fresh) == expected and missing == "-" else "fail"
    return Check(
        "irrigation cfg readbacks",
        status,
        f"present={present}/{expected} fresh_15m={fresh}/{expected} range={min_ts}..{max_ts} missing={missing}",
    )


def _check_schedule_setpoint_confirmations(direct_db: bool) -> Check:
    values_sql = ",".join(f"('{param}')" for param in IRRIGATION_SCHEDULE_PARAMS)
    row = _psql(
        f"""
        WITH params(parameter) AS (VALUES {values_sql}),
        latest AS (
            SELECT DISTINCT ON (parameter) parameter, value, ts
              FROM setpoint_snapshot
             WHERE parameter IN (SELECT parameter FROM params)
             ORDER BY parameter, ts DESC
        ),
        active AS (
            SELECT sc.parameter,
                   sc.ts,
                   sc.value,
                   COALESCE(sc.delivery_status, 'pending') AS delivery_status,
                   l.value AS cfg_value,
                   l.ts AS cfg_ts
              FROM setpoint_changes sc
              JOIN params p USING (parameter)
              LEFT JOIN latest l USING (parameter)
             WHERE sc.confirmed_at IS NULL
               AND sc.expired_at IS NULL
               AND sc.superseded_by_ts IS NULL
               AND COALESCE(sc.source, '') <> 'esp32'
               AND COALESCE(sc.delivery_status, 'pending') IN (
                   'pending', 'requested', 'queued', 'retrying', 'sent',
                   'deferred_heap_pressure'
               )
               AND sc.ts > now() - interval '1 hour'
        ),
        lifecycle AS (
            SELECT count(*) FILTER (
                       WHERE sc.confirmed_at IS NOT NULL OR sc.delivery_status = 'confirmed'
                   )::text AS confirmed,
                   count(*) FILTER (
                       WHERE sc.confirmed_at IS NULL
                         AND COALESCE(sc.delivery_status, 'pending') IN (
                             'pending', 'requested', 'queued', 'retrying', 'sent',
                             'deferred_heap_pressure'
                         )
                   )::text AS in_flight,
                   count(*) FILTER (
                       WHERE sc.delivery_status IN ('failed', 'cancelled', 'superseded')
                   )::text AS terminal_unconfirmed
              FROM setpoint_changes sc
              JOIN params p USING (parameter)
             WHERE COALESCE(sc.source, '') <> 'esp32'
               AND sc.ts > now() - interval '1 hour'
        ),
        open_alerts AS (
            SELECT count(*)::text AS open_unconfirmed_alerts,
                   COALESCE(string_agg(al.sensor_id, ',' ORDER BY al.sensor_id), '-') AS open_alert_sensors
              FROM alert_log al
              JOIN params p ON al.sensor_id = 'setpoint.' || p.parameter
             WHERE al.alert_type = 'setpoint_unconfirmed'
               AND al.disposition = 'open'
        ),
        classified AS (
            SELECT *,
                   cfg_ts IS NOT NULL
                   AND cfg_ts > ts
                   AND abs(value - cfg_value::double precision)
                       / greatest(abs(cfg_value::double precision), 1e-3) < 0.01
                       AS should_be_confirmed,
                   ts < now() - interval '5 minutes' AS alert_age
              FROM active
        )
        SELECT count(*)::text,
               count(*) FILTER (WHERE should_be_confirmed)::text,
               count(*) FILTER (WHERE alert_age)::text,
               COALESCE(min(ts)::text, '-'),
               COALESCE(max(ts)::text, '-'),
               COALESCE(
                   string_agg(parameter, ',' ORDER BY parameter)
                       FILTER (WHERE should_be_confirmed OR alert_age),
                   '-'
               ),
               (SELECT open_unconfirmed_alerts FROM open_alerts),
               (SELECT open_alert_sensors FROM open_alerts),
               (SELECT confirmed FROM lifecycle),
               (SELECT in_flight FROM lifecycle),
               (SELECT terminal_unconfirmed FROM lifecycle)
          FROM classified
        """,
        direct_db=direct_db,
    )[0]
    (
        active,
        should_confirm,
        alert_age,
        min_ts,
        max_ts,
        offenders,
        open_alerts,
        open_alert_sensors,
        confirmed,
        in_flight,
        terminal_unconfirmed,
    ) = row
    status = "pass" if int(should_confirm) == 0 and int(alert_age) == 0 and int(open_alerts) == 0 else "fail"
    return Check(
        "irrigation setpoint confirmations",
        status,
        (
            f"active_unconfirmed={active} should_be_confirmed={should_confirm} "
            f"older_than_5m={alert_age} open_unconfirmed_alerts={open_alerts} "
            f"lifecycle_confirmed={confirmed} lifecycle_in_flight={in_flight} "
            f"lifecycle_terminal_unconfirmed={terminal_unconfirmed} "
            f"range={min_ts}..{max_ts} offenders={offenders} open_alert_sensors={open_alert_sensors}"
        ),
    )


def _check_planner_context_uses_canonical_irrigation() -> Check:
    script_path = REPO_ROOT / "scripts" / "gather-plan-context.sh"
    try:
        script = script_path.read_text()
    except OSError as exc:
        return Check("planner context canonical irrigation source", "fail", str(exc))

    required = ("v_irrigation_schedule_current", "v_irrigation_fertigation_runs")
    forbidden = ("FROM irrigation_schedule", "FROM irrigation_log", "SELECT zone, start_time, duration_s")
    missing = [token for token in required if token not in script]
    legacy = [token for token in forbidden if token in script]
    status = "pass" if not missing and not legacy else "fail"
    return Check(
        "planner context canonical irrigation source",
        status,
        f"required_missing={missing or '-'} legacy_refs={legacy or '-'}",
    )


def _check_schema_contract_marks_legacy_irrigation_retired() -> Check:
    operations_path = REPO_ROOT / "verdify_schemas" / "operations.py"
    relationships_path = REPO_ROOT / "verdify_schemas" / "RELATIONSHIPS.md"
    try:
        operations = operations_path.read_text()
        relationships = relationships_path.read_text()
    except OSError as exc:
        return Check("schema contract legacy irrigation retired", "fail", str(exc))

    required = (
        "Retired compatibility row.",
        "v_irrigation_schedule_current",
        "v_irrigation_fertigation_runs",
        "retired compatibility; canonical schedule is",
    )
    combined = f"{operations}\n{relationships}"
    missing = [token for token in required if token not in combined]
    stale = []
    if "IrrigationLog / IrrigationSchedule: water events + recurring rules" in operations:
        stale.append("operations module summary")
    if "| `v_water_budget` | `irrigation_log`, `equipment_state`" in relationships:
        stale.append("v_water_budget source")
    status = "pass" if not missing and not stale else "fail"
    return Check(
        "schema contract legacy irrigation retired",
        status,
        f"required_missing={missing or '-'} stale_refs={stale or '-'}",
    )


def _check_schema_snapshot_irrigation_contract() -> Check:
    schema_path = REPO_ROOT / "db" / "schema.sql"
    try:
        schema = schema_path.read_text()
    except OSError as exc:
        return Check("schema snapshot irrigation contract", "fail", str(exc))

    required = (
        "Retired compatibility table. Canonical irrigation/fertigation events are reconstructed from equipment_state in v_irrigation_fertigation_runs.",
        "Retired compatibility table. Canonical current schedule is v_irrigation_schedule_current",
        "Retired compatibility view reconstructed from v_irrigation_fertigation_runs.",
        "FROM public.v_irrigation_fertigation_runs",
        "CREATE FUNCTION public.prevent_retired_irrigation_compat_write() RETURNS trigger",
        "verdify.allow_retired_irrigation_compat_write",
        "CREATE TRIGGER block_retired_irrigation_log_write",
        "CREATE TRIGGER block_retired_irrigation_schedule_write",
        "runtime_drip_wall_fert_h double precision",
        "runtime_drip_center_fert_h double precision",
        "runtime_mister_south_fert_h double precision",
        "runtime_mister_west_fert_h double precision",
        "runtime_fert_master_h double precision",
        "runtime_irrigation_clean_h double precision",
        "runtime_irrigation_fert_h double precision",
        "runtime_irrigation_total_h double precision",
        "irrigation_water_gal double precision",
        "fertigation_water_gal double precision",
        "COMMENT ON COLUMN public.daily_summary.fertigation_water_gal",
        "CREATE VIEW public.v_irrigation_schedule_current AS",
        "CREATE VIEW public.v_irrigation_fertigation_runs AS",
        "CREATE VIEW public.v_irrigation_program_daily AS",
        "CREATE VIEW public.v_irrigation_accountability AS",
        "CREATE VIEW public.v_irrigation_sensor_feedback_status AS",
        "CREATE VIEW public.v_water_budget AS",
        "Daily water decomposition including equipment-derived fertigation gallons and fert/master relay runtime.",
    )
    stale = (
        "Actual irrigation events. Linked to schedule",
        "Programmed irrigation schedules per zone",
        "Irrigation log with computed duration_s",
        "Daily water decomposition: mister vs drip vs unaccounted.",
    )
    missing = [token for token in required if token not in schema]
    stale_refs = [token for token in stale if token in schema]
    status = "pass" if not missing and not stale_refs else "fail"
    return Check(
        "schema snapshot irrigation contract",
        status,
        f"required_missing={missing or '-'} stale_refs={stale_refs or '-'}",
    )


def _check_fertigation_runs(direct_db: bool) -> Check:
    row = _psql(
        """
        SELECT count(*)::text,
               count(*) FILTER (WHERE quality_flag <> 'ok')::text,
               round(sum(COALESCE(meter_delta_gal, 0))::numeric, 2)::text,
               round(avg(CASE WHEN fert_duration_min > 0
                              THEN fert_master_overlap_min / fert_duration_min * 100
                         END)::numeric, 1)::text
          FROM v_irrigation_fertigation_runs
         WHERE run_start > now() - interval '7 days'
        """,
        direct_db=direct_db,
    )[0]
    runs, flagged, gallons, master_pct = row
    status = "pass" if int(runs) > 0 and int(flagged) == 0 else "fail"
    return Check(
        "equipment-derived fertigation runs",
        status,
        f"runs_7d={runs} flagged_7d={flagged} meter_delta_gal_7d={gallons} avg_master_overlap_pct={master_pct}",
    )


def _check_fertigation_run_coherence(direct_db: bool) -> Check:
    row = _psql(
        """
        WITH recent AS (
            SELECT *
              FROM v_irrigation_fertigation_runs
             WHERE run_start > now() - interval '7 days'
        )
        SELECT count(*)::text,
               count(DISTINCT run_id)::text,
               count(*) FILTER (WHERE fert_relay IS NULL OR flush_relay IS NULL)::text,
               count(*) FILTER (
                 WHERE NOT (
                   run_start = fert_start
                   AND fert_start < fert_end
                   AND fert_end <= flush_start
                   AND flush_start < flush_end
                   AND flush_end = run_end
                 )
               )::text,
               count(*) FILTER (
                 WHERE fert_duration_min <= 0
                    OR flush_duration_min <= 0
                    OR total_duration_min < fert_duration_min + flush_duration_min - 0.1
               )::text,
               count(*) FILTER (
                 WHERE fert_master_overlap_min < greatest(0.5, fert_duration_min * 0.95)
               )::text,
               count(*) FILTER (
                 WHERE meter_samples < 2
                    OR meter_delta_gal <= 0
                    OR max_total_gal < min_total_gal
               )::text,
               COALESCE(min(meter_delta_gal)::text, '-'),
               COALESCE(max(meter_delta_gal)::text, '-')
          FROM recent
        """,
        direct_db=direct_db,
    )[0]
    (
        runs,
        distinct_runs,
        missing_pair,
        bad_sequence,
        bad_duration,
        bad_master,
        bad_meter,
        min_delta,
        max_delta,
    ) = row
    bad_counts = {
        "duplicate_run_ids": int(runs) - int(distinct_runs),
        "missing_pairs": int(missing_pair),
        "bad_sequence": int(bad_sequence),
        "bad_duration": int(bad_duration),
        "bad_master_overlap": int(bad_master),
        "bad_meter_delta": int(bad_meter),
    }
    status = "pass" if int(runs) > 0 and all(value == 0 for value in bad_counts.values()) else "fail"
    return Check(
        "fertigation run reconstruction coherence",
        status,
        (
            f"runs_7d={runs} distinct_run_ids={distinct_runs} bad_counts={bad_counts} "
            f"meter_delta_range={min_delta}..{max_delta}"
        ),
    )


def _check_daily_accounting(direct_db: bool) -> Check:
    row = _psql(
        """
        WITH recent AS (
            SELECT ds.date,
                   ds.runtime_irrigation_clean_h,
                   ds.runtime_irrigation_fert_h,
                   ds.runtime_irrigation_total_h,
                   ds.runtime_fert_master_h,
                   ds.irrigation_water_gal,
                   ds.fertigation_water_gal,
                   ipd.fert_runtime_min,
                   ipd.fert_master_overlap_min,
                   ipd.meter_delta_gal
              FROM daily_summary ds
              JOIN v_irrigation_program_daily ipd ON ipd.date = ds.date
             WHERE ds.date >= (now() AT TIME ZONE 'America/Denver')::date - 14
        )
        SELECT count(*)::text,
               min(date)::text,
               max(date)::text,
               count(*) FILTER (
                 WHERE runtime_irrigation_clean_h IS NULL
                    OR runtime_irrigation_fert_h IS NULL
                    OR runtime_irrigation_total_h IS NULL
                    OR runtime_fert_master_h IS NULL
                    OR irrigation_water_gal IS NULL
                    OR fertigation_water_gal IS NULL
               )::text AS null_daily_fields,
               count(*) FILTER (
                 WHERE abs(COALESCE(runtime_irrigation_fert_h, 0) - COALESCE(fert_runtime_min, 0) / 60.0) > 0.02
               )::text AS fert_runtime_mismatch,
               count(*) FILTER (
                 WHERE abs(COALESCE(runtime_fert_master_h, 0) - COALESCE(fert_master_overlap_min, 0) / 60.0) > 0.02
               )::text AS master_runtime_mismatch,
               count(*) FILTER (
                 WHERE abs(COALESCE(irrigation_water_gal, 0) - COALESCE(meter_delta_gal, 0)) > 0.01
               )::text AS irrigation_water_mismatch,
               count(*) FILTER (
                 WHERE abs(COALESCE(fertigation_water_gal, 0) - COALESCE(meter_delta_gal, 0)) > 0.01
               )::text AS fertigation_water_mismatch
          FROM recent
        """,
        direct_db=direct_db,
    )[0]
    (
        rows,
        min_date,
        max_date,
        null_daily_fields,
        fert_runtime_mismatch,
        master_runtime_mismatch,
        irrigation_water_mismatch,
        fertigation_water_mismatch,
    ) = row
    mismatch_counts = {
        "null_daily_fields": int(null_daily_fields),
        "fert_runtime_mismatch": int(fert_runtime_mismatch),
        "master_runtime_mismatch": int(master_runtime_mismatch),
        "irrigation_water_mismatch": int(irrigation_water_mismatch),
        "fertigation_water_mismatch": int(fertigation_water_mismatch),
    }
    status = "pass" if int(rows) > 0 and all(value == 0 for value in mismatch_counts.values()) else "fail"
    return Check(
        "daily runtime/water accounting",
        status,
        f"reconciled_rows={rows} date_range={min_date or '-'}..{max_date or '-'} mismatch_counts={mismatch_counts}",
    )


def _check_feedback_value_range_gate(direct_db: bool) -> Check:
    viewdef = _scalar(
        "SELECT replace(pg_get_viewdef('v_irrigation_sensor_feedback_status'::regclass, true), E'\n', ' ')",
        direct_db=direct_db,
    )
    viewdef = viewdef or ""
    required_patterns = {
        "south_1_moisture_upper_bound": r"soil_moisture_south_1\s*>\s*0::double precision\s+AND\s+climate\.soil_moisture_south_1\s*<=\s*100::double precision",
        "south_2_reference_upper_bound": r"soil_moisture_south_2\s*>\s*0::double precision\s+AND\s+climate\.soil_moisture_south_2\s*<=\s*100::double precision",
        "center_moisture_valid_ts": r"center_moisture_last_valid_ts",
        "center_moisture_range": r"moisture_center\s*>=\s*0::double precision\s+AND\s+climate\.moisture_center\s*<=\s*100::double precision",
        "center_ph_valid_ts": r"center_ph_last_valid_ts",
        "center_ph_range": r"ph_runoff_center\s*>=\s*0::double precision\s+AND\s+climate\.ph_runoff_center\s*<=\s*14::double precision",
        "center_ec_valid_ts": r"center_ec_last_valid_ts",
        "center_ec_nonnegative": r"ec_runoff_center\s*>=\s*0::double precision",
        "invalid_status": r"'invalid'::text",
        "raw_value_details": r"latest_raw_value",
    }
    missing = [label for label, pattern in required_patterns.items() if not re.search(pattern, viewdef)]
    return Check(
        "irrigation feedback valid-range gate",
        "pass" if not missing else "fail",
        f"missing={missing or '-'}",
    )


def _literal_assignment(path: Path, name: str) -> object:
    tree = ast.parse(path.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return ast.literal_eval(node.value)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            return ast.literal_eval(node.value)
    raise KeyError(f"{name} not found in {path}")


def _mapped_keys(mapping: dict[str, object], column: str) -> set[str]:
    keys: set[str] = set()
    for key, value in mapping.items():
        mapped_column = value[0] if isinstance(value, tuple) else value
        if mapped_column == column:
            keys.add(key)
    return keys


def _check_feedback_alias_alignment() -> Check:
    try:
        entity_map_path = REPO_ROOT / "ingestor" / "entity_map.py"
        tasks_path = REPO_ROOT / "ingestor" / "tasks.py"
        legacy_sync_path = REPO_ROOT / "scripts" / "ha-sensor-sync.py"
        validator_path = REPO_ROOT / "scripts" / "validate-irrigation-feedback.py"

        esphome_feedback_map = _literal_assignment(entity_map_path, "ESPHOME_FEEDBACK_MAP")
        mqtt_map = _literal_assignment(entity_map_path, "MQTT_FEEDBACK_MAP")
        mqtt_candidates = _literal_assignment(entity_map_path, "MQTT_FEEDBACK_CANDIDATES")
        tasks_ha_map = _literal_assignment(tasks_path, "_CENTER_FEEDBACK_MAP")
        legacy_ha_map = _literal_assignment(legacy_sync_path, "CENTER_FEEDBACK_MAP")
        validator_ha_candidates = _literal_assignment(validator_path, "HA_CANDIDATES")
        validator_esphome_candidates = _literal_assignment(validator_path, "ESPHOME_CANDIDATES")
    except (OSError, KeyError, SyntaxError, ValueError) as exc:
        return Check("irrigation feedback alias alignment", "fail", str(exc))

    key_to_column = {
        "center_root_zone_moisture": "moisture_center",
        "center_runoff_ph": "ph_runoff_center",
        "center_runoff_ec": "ec_runoff_center",
    }
    mismatches: list[str] = []
    counts: list[str] = []
    for feedback_key, column in key_to_column.items():
        ha_candidates = set(validator_ha_candidates.get(feedback_key, ()))
        task_entities = _mapped_keys(tasks_ha_map, column)
        legacy_entities = _mapped_keys(legacy_ha_map, column)
        if ha_candidates != task_entities or ha_candidates != legacy_entities:
            mismatches.append(f"{feedback_key}:ha")

        expected_mqtt = set(mqtt_candidates.get(feedback_key, ()))
        accepted_mqtt = _mapped_keys(mqtt_map, column)
        if expected_mqtt != accepted_mqtt:
            mismatches.append(f"{feedback_key}:mqtt")

        esphome_candidates = set(validator_esphome_candidates.get(feedback_key, ()))
        accepted_esphome = _mapped_keys(esphome_feedback_map, column)
        if esphome_candidates != accepted_esphome:
            mismatches.append(f"{feedback_key}:esphome")

        counts.append(
            f"{feedback_key}:ha={len(ha_candidates)} mqtt={len(expected_mqtt)} esphome={len(esphome_candidates)}"
        )

    feedback_columns = set(key_to_column.values())
    accepted_maps = {
        "esphome_feedback": esphome_feedback_map,
        "mqtt": mqtt_map,
        "tasks_ha": tasks_ha_map,
        "legacy_ha": legacy_ha_map,
    }
    tds_accepted = [
        f"{name}:{key}"
        for name, mapping in accepted_maps.items()
        for key, value in mapping.items()
        if "tds" in key.lower() and (value[0] if isinstance(value, tuple) else value) in feedback_columns
    ]
    for feedback_key, values in mqtt_candidates.items():
        if feedback_key in key_to_column:
            tds_accepted.extend(f"mqtt_candidates:{topic}" for topic in values if "tds" in topic.lower())
    for feedback_key, values in validator_ha_candidates.items():
        if feedback_key in key_to_column:
            tds_accepted.extend(f"ha_candidates:{entity_id}" for entity_id in values if "tds" in entity_id.lower())
    for feedback_key, values in validator_esphome_candidates.items():
        if feedback_key in key_to_column:
            tds_accepted.extend(f"esphome_candidates:{object_id}" for object_id in values if "tds" in object_id.lower())

    status = "pass" if not mismatches and not tds_accepted else "fail"
    return Check(
        "irrigation feedback alias alignment",
        status,
        f"mismatches={mismatches or '-'} tds_accepted={tds_accepted or '-'} counts={'; '.join(counts)}",
    )


def _check_feedback(direct_db: bool) -> Check:
    rows = _psql(
        """
        SELECT feedback_key, status, COALESCE(latest_value::text, '-'), COALESCE(details::text, '{}')
          FROM v_irrigation_sensor_feedback_status
         ORDER BY feedback_key
        """,
        direct_db=direct_db,
    )
    not_ok = [f"{key}:{status}" for key, status, _value, _details in rows if status != "ok"]
    alert_count = int(
        _scalar(
            """
            SELECT count(*)
              FROM alert_log
             WHERE alert_type = 'irrigation_feedback_gap'
               AND resolved_at IS NULL
            """,
            direct_db=direct_db,
        )
        or "0"
    )
    open_requirements = [
        f"{requirement_id}:{current_status}"
        for requirement_id, current_status in _psql(
            """
            SELECT requirement_id, current_status
              FROM instrumentation_requirements
             WHERE requirement_id IN ('south_soil_probe_1_repair', 'center_root_zone_runoff_feedback')
               AND current_status <> 'complete'
             ORDER BY requirement_id
            """,
            direct_db=direct_db,
        )
    ]
    registry_not_validated = [
        f"{source_column}:{active}:installed={installed_date}"
        for source_column, active, installed_date in _psql(
            """
            WITH expected(source_column) AS (
              VALUES
                ('soil_moisture_south_1'::text),
                ('soil_ec_south_1'::text),
                ('soil_temp_south_1'::text),
                ('moisture_center'::text),
                ('ph_runoff_center'::text),
                ('ec_runoff_center'::text)
            )
            SELECT e.source_column,
                   COALESCE(sr.active::text, 'missing') AS active,
                   COALESCE(sr.installed_date::text, '-') AS installed_date
              FROM expected e
              LEFT JOIN sensor_registry sr
                ON sr.source_table = 'climate'
               AND sr.source_column = e.source_column
             WHERE sr.sensor_id IS NULL
                OR sr.active IS DISTINCT FROM true
                OR sr.installed_date IS NULL
             ORDER BY e.source_column
            """,
            direct_db=direct_db,
        )
    ]
    missing_validation_logs = [
        equipment
        for (equipment,) in _psql(
            """
            WITH expected(equipment, description) AS (
              VALUES
                (
                  'south_soil_probe_1'::text,
                  'South soil probe 1 irrigation feedback validation passed.'::text
                ),
                (
                  'center_root_zone_runoff_feedback'::text,
                  'Center root-zone and runoff feedback validation passed.'::text
                )
            )
            SELECT e.equipment
              FROM expected e
             WHERE NOT EXISTS (
               SELECT 1
                 FROM maintenance_log ml
                WHERE ml.equipment = e.equipment
                  AND ml.service_type = 'validation'
                  AND ml.description = e.description
             )
             ORDER BY e.equipment
            """,
            direct_db=direct_db,
        )
    ]
    status = (
        "pass"
        if not not_ok
        and alert_count == 0
        and not open_requirements
        and not registry_not_validated
        and not missing_validation_logs
        else "blocked"
    )
    return Check(
        "physical irrigation feedback",
        status,
        (
            f"not_ok={not_ok or '-'} open_irrigation_feedback_gap_alerts={alert_count} "
            f"open_field_requirements={open_requirements or '-'} "
            f"registry_not_validated={registry_not_validated or '-'} "
            f"missing_validation_logs={missing_validation_logs or '-'}"
        ),
    )


def _check_dashboard_files() -> Check:
    dashboard_path = REPO_ROOT / "grafana" / "dashboards" / "site-irrigation.json"
    provisioned_path = REPO_ROOT / "grafana" / "provisioning" / "dashboards" / "json" / "site-irrigation.json"
    try:
        dashboard = json.loads(dashboard_path.read_text())
        provisioned = json.loads(provisioned_path.read_text())
        html = SITE_IRRIGATION_HTML.read_text()
    except (OSError, json.JSONDecodeError) as exc:
        return Check("irrigation dashboard/site artifacts", "fail", str(exc))

    panel_ids = {panel.get("id") for panel in dashboard.get("panels", [])}
    panels_by_title = {panel.get("title"): panel for panel in dashboard.get("panels", [])}
    missing_panels: list[str] = []
    missing_tokens: list[str] = []
    for title, tokens in REQUIRED_DASHBOARD_PANELS.items():
        panel = panels_by_title.get(title)
        if panel is None:
            missing_panels.append(title)
            continue
        query_text = "\n".join(str(target.get("rawSql", "")) for target in panel.get("targets", []))
        missing = [token for token in tokens if token not in query_text]
        if missing:
            missing_tokens.append(f"{title}:{','.join(missing)}")
    relay_panel = panels_by_title.get("Relay State") or {}
    relay_panel_json = json.dumps(relay_panel, sort_keys=True)
    relay_mappings = relay_panel.get("fieldConfig", {}).get("defaults", {}).get("mappings", [{}])[0].get("options", {})
    relay_mapping_labels_blank = all(relay_mappings.get(value, {}).get("text") == "" for value in ("0", "1"))
    relay_style_ok = (
        relay_panel.get("type") == "state-timeline"
        and relay_panel.get("options", {}).get("showValue") == "never"
        and relay_panel.get("options", {}).get("mergeValues") is True
        and relay_panel.get("fieldConfig", {}).get("defaults", {}).get("unit") == "none"
        and relay_mapping_labels_blank
        and "bool_on_off" not in relay_panel_json
        and "CASE WHEN state THEN 'ON'" not in relay_panel_json
        and "CASE WHEN state THEN 'OFF'" not in relay_panel_json
    )
    ok = (
        dashboard.get("uid") == "site-irrigation"
        and provisioned.get("uid") == "site-irrigation"
        and dashboard == provisioned
        and len(panel_ids) >= 15
        and 9 in panel_ids
        and 12 in panel_ids
        and 15 in panel_ids
        and not missing_panels
        and not missing_tokens
        and relay_style_ok
        and "panelId=9" in html
        and "panelId=12" in html
        and "panelId=13" in html
        and "panelId=14" in html
        and "panelId=15" in html
        and "site-irrigation" in html
    )
    return Check(
        "irrigation dashboard/site artifacts",
        "pass" if ok else "fail",
        (
            f"uid={dashboard.get('uid')} panels={len(panel_ids)} panel9={9 in panel_ids} "
            f"panel12={12 in panel_ids} panel15={15 in panel_ids} "
            f"required_missing={missing_panels or '-'} token_missing={missing_tokens or '-'} "
            f"relay_style_ok={relay_style_ok} provisioned_matches={dashboard == provisioned} "
            f"site_html={SITE_IRRIGATION_HTML.exists()}"
        ),
    )


def _check_site_discoverability() -> Check:
    home_path = SITE_PUBLIC_ROOT / "index.html"
    sitemap_path = SITE_PUBLIC_ROOT / "sitemap.xml"
    redirect_paths = {
        "irrigation": (SITE_PUBLIC_ROOT / "irrigation.html", "url=./greenhouse/irrigation"),
        "climate/irrigation": (SITE_PUBLIC_ROOT / "climate" / "irrigation.html", "url=../greenhouse/irrigation"),
        "water/irrigation": (SITE_PUBLIC_ROOT / "water" / "irrigation.html", "url=../greenhouse/irrigation"),
    }
    try:
        home = home_path.read_text()
        sitemap = sitemap_path.read_text()
        redirects = {name: path.read_text() for name, (path, _token) in redirect_paths.items()}
    except OSError as exc:
        return Check("irrigation page discoverability", "fail", str(exc))

    nav_link = 'href="/greenhouse/irrigation">Irrigation' in home
    sitemap_loc = "<loc>https://lab.verdify.ai/greenhouse/irrigation</loc>" in sitemap
    redirect_status = {name: expected in redirects[name] for name, (_path, expected) in redirect_paths.items()}
    ok = nav_link and sitemap_loc and all(redirect_status.values())
    return Check(
        "irrigation page discoverability",
        "pass" if ok else "fail",
        f"nav_link={nav_link} sitemap_loc={sitemap_loc} redirects={redirect_status}",
    )


def _check_acceptance_tooling() -> Check:
    makefile_path = REPO_ROOT / "Makefile"
    finalizer_path = REPO_ROOT / "scripts" / "finalize-irrigation-feedback.py"
    runbook_path = REPO_ROOT / "docs" / "runbooks" / "irrigation-feedback-bringup.md"
    try:
        makefile = makefile_path.read_text()
        finalizer = finalizer_path.read_text()
        runbook = runbook_path.read_text()
    except OSError as exc:
        return Check("irrigation acceptance tooling", "fail", str(exc))

    missing = []
    if "irrigation-feedback-finalize-dry-run" not in makefile:
        missing.append("finalize dry-run target")
    if "scripts/finalize-irrigation-feedback.py --dry-run" not in makefile:
        missing.append("make dry-run command")
    if "irrigation-feedback-finalize-dry-run-proof:" not in makefile:
        missing.append("finalize dry-run proof target")
    if (
        "IRRIGATION_FINALIZER_DRY_RUN_PROOF" not in makefile
        or 'tee "$(IRRIGATION_FINALIZER_DRY_RUN_PROOF)"' not in makefile
    ):
        missing.append("finalizer dry-run proof persisted artifact")
    if "irrigation-feedback-finalize:" not in makefile:
        missing.append("finalize target")
    if "irrigation-feedback-finalize-proof:" not in makefile:
        missing.append("finalizer proof target")
    if "IRRIGATION_FINALIZER_PROOF" not in makefile or 'tee "$(IRRIGATION_FINALIZER_PROOF)"' not in makefile:
        missing.append("finalizer persisted artifact")
    if "irrigation-feedback-proof-json:" not in makefile:
        missing.append("feedback proof-json target")
    if "IRRIGATION_FEEDBACK_PROOF" not in makefile or 'tee "$(IRRIGATION_FEEDBACK_PROOF)"' not in makefile:
        missing.append("feedback proof-json persisted artifact")
    if "irrigation-feedback-work-order-proof:" not in makefile:
        missing.append("field work-order proof target")
    if "IRRIGATION_WORK_ORDER_PROOF" not in makefile or 'tee "$(IRRIGATION_WORK_ORDER_PROOF)"' not in makefile:
        missing.append("field work-order persisted artifact")
    if "irrigation-field-sensor-health-proof:" not in makefile:
        missing.append("field sensor-health proof target")
    if (
        "IRRIGATION_FIELD_SENSOR_HEALTH_PROOF" not in makefile
        or 'tee "$(IRRIGATION_FIELD_SENSOR_HEALTH_PROOF)"' not in makefile
    ):
        missing.append("field sensor-health persisted artifact")
    if "irrigation-feedback-discovery-proof:" not in makefile:
        missing.append("field discovery proof target")
    if "IRRIGATION_DISCOVERY_PROOF" not in makefile or 'tee "$(IRRIGATION_DISCOVERY_PROOF)"' not in makefile:
        missing.append("field discovery persisted artifact")
    if "irrigation-feedback-watch-field-proof:" not in makefile:
        missing.append("field watch proof target")
    if "IRRIGATION_FIELD_WATCH_PROOF" not in makefile or 'tee "$(IRRIGATION_FIELD_WATCH_PROOF)"' not in makefile:
        missing.append("field watch persisted artifact")
    if "irrigation-sensor-health-proof:" not in makefile:
        missing.append("sensor-health proof target")
    if "IRRIGATION_SENSOR_HEALTH_PROOF" not in makefile or 'tee "$(IRRIGATION_SENSOR_HEALTH_PROOF)"' not in makefile:
        missing.append("sensor-health proof persisted artifact")
    if "irrigation-stack-proof:" not in makefile:
        missing.append("stack proof target")
    if "IRRIGATION_STACK_PROOF" not in makefile or 'tee "$(IRRIGATION_STACK_PROOF)"' not in makefile:
        missing.append("stack proof persisted artifact")
    if "irrigation-completion-audit:" not in makefile:
        missing.append("completion audit target")
    if "irrigation-completion-audit-proof:" not in makefile:
        missing.append("completion audit proof target")
    if (
        "IRRIGATION_COMPLETION_AUDIT_PROOF" not in makefile
        or 'tee "$(IRRIGATION_COMPLETION_AUDIT_PROOF)"' not in makefile
    ):
        missing.append("completion audit persisted artifact")
    if "irrigation-acceptance:" not in makefile:
        missing.append("acceptance target")
    if "irrigation-full-acceptance:" not in makefile:
        missing.append("full acceptance target")
    if "irrigation-post-deploy-acceptance-plan:" not in makefile:
        missing.append("post-deploy acceptance plan target")
    if "irrigation-post-deploy-acceptance:" not in makefile:
        missing.append("post-deploy acceptance target")
    if "irrigation-migration-proof:" not in makefile:
        missing.append("migration proof target")
    if "IRRIGATION_MIGRATION_PROOF" not in makefile or 'tee "$(IRRIGATION_MIGRATION_PROOF)"' not in makefile:
        missing.append("migration proof persisted artifact")
    if (
        '"--dry-run"' not in finalizer
        or "dry_run=true" not in finalizer
        or "expected_open_feedback_alerts_after_finalize" not in finalizer
    ):
        missing.append("finalizer dry-run mode")
    if "source IS DISTINCT FROM 'system'" not in finalizer:
        missing.append("manual alert null-safe preflight")
    if "async with conn.transaction():" not in finalizer or "FinalizerBlocked" not in finalizer:
        missing.append("transaction rollback guard")
    if (
        "first records DB, HA, MQTT, and ESPHome feedback source evidence" not in runbook
        or "then runs the dry run before the mutating finalizer" not in runbook
    ):
        missing.append("runbook finalize source-evidence and dry-run guard")
    runbook_ingestor_restart_doc = (
        "sudo systemctl restart verdify-ingestor" in runbook
        and "Alias-only irrigation feedback changes do not require `verdify-mcp`" in runbook
        and "Do not run this restart from a dirty shared worktree" in runbook
    )
    if not runbook_ingestor_restart_doc:
        missing.append("runbook ingestor restart boundary for feedback aliases")
    runbook_post_deploy_acceptance_doc = (
        "Final acceptance is a post-deploy proof, not a deploy target" in runbook
        and "Run it only after the branch is merged" in runbook
        and "the generated public site is live" in runbook
        and "Run this after merge/deploy on the production host" in runbook
        and "it proves the deployed state, it does not deploy the state" in runbook
    )
    if not runbook_post_deploy_acceptance_doc:
        missing.append("runbook post-deploy acceptance boundary")
    runbook_post_deploy_plan_doc = (
        "make irrigation-post-deploy-acceptance-plan" in runbook
        and "print-only preview" in runbook
        and "does not run checks, wait on sensors, or invoke the finalizer" in runbook
    )
    if not runbook_post_deploy_plan_doc:
        missing.append("runbook post-deploy acceptance print-only plan")
    runbook_static_snapshot_boundary_doc = (
        "Current proof artifacts are authoritative" in runbook
        and "do not treat the static snapshot below as fresher than those files" in runbook
        and "Representative point-in-time snapshot" in runbook
        and "/srv/verdify/state/irrigation-completion-audit.json" in runbook
        and "/srv/verdify/state/irrigation-work-order.txt" in runbook
    )
    if not runbook_static_snapshot_boundary_doc:
        missing.append("runbook static snapshot freshness boundary")

    order_ok = False
    acceptance_calls_finalize = False
    acceptance_persists_field_watch = False
    acceptance_persists_discovery = False
    acceptance_runs_sensor_health = False
    acceptance_emits_feedback_json = False
    acceptance_runs_stack_proof = False
    acceptance_emits_completion_audit_json = False
    acceptance_runs_completion_audit = False
    acceptance_site_before_live = False
    stack_check_site_before_live = False
    software_check_runs_direct_audit = False
    feedback_proof_persisted = False
    field_watch_proof_persisted = False
    finalizer_dry_run_proof_persisted = False
    finalizer_proof_persisted = False
    work_order_proof_persisted = False
    field_sensor_health_proof_persisted = False
    diagnostics_persists_sensor_health = False
    diagnostics_persists_work_order = False
    diagnostics_persists_completion_audit = False
    diagnostics_persists_discovery = False
    diagnostics_persists_finalizer_dry_run = False
    discovery_proof_persisted = False
    sensor_health_proof_persisted = False
    stack_proof_persisted = False
    completion_audit_proof_persisted = False
    migration_proof_persisted = False
    full_acceptance_includes_tests = False
    post_deploy_acceptance_plan_prints_only = False
    post_deploy_acceptance_aliases_full = False
    try:
        software_check_block = makefile[
            makefile.index("irrigation-stack-software-check:") : makefile.index(
                "irrigation-stack-check:", makefile.index("irrigation-stack-software-check:")
            )
        ]
        software_check_runs_direct_audit = (
            "scripts/validate-irrigation-stack.py --software-only" in software_check_block
            and "$(MAKE) site-doctor" not in software_check_block
        )
        stack_check_block = makefile[
            makefile.index("irrigation-stack-check:") : makefile.index(
                "irrigation-feedback-check:", makefile.index("irrigation-stack-check:")
            )
        ]
        stack_check_site_before_live = stack_check_block.index("$(MAKE) site-doctor") < stack_check_block.index(
            "scripts/validate-irrigation-stack.py --live-site"
        )
        finalizer_proof_block = makefile[
            makefile.index("irrigation-feedback-finalize-proof:") : makefile.index(
                "irrigation-feedback-proof-json:", makefile.index("irrigation-feedback-finalize-proof:")
            )
        ]
        finalizer_preflight_source_evidence = (
            "scripts/validate-irrigation-feedback.py --status-only --discover-ha --discover-mqtt --discover-mqtt-all --discover-esphome"
            in finalizer_proof_block
            and "--include-db-history" in finalizer_proof_block
            and "IRRIGATION_FIELD_WATCH_MQTT_TIMEOUT" in finalizer_proof_block
        )
        order_ok = (
            finalizer_proof_block.index("scripts/validate-irrigation-feedback.py --status-only")
            < finalizer_proof_block.index("scripts/finalize-irrigation-feedback.py --dry-run")
            < finalizer_proof_block.index("scripts/finalize-irrigation-feedback.py &&")
        )
        finalizer_proof_persisted = (
            "IRRIGATION_FINALIZER_PROOF" in finalizer_proof_block
            and "set -o pipefail" in finalizer_proof_block
            and finalizer_preflight_source_evidence
            and "scripts/finalize-irrigation-feedback.py --dry-run" in finalizer_proof_block
            and "scripts/finalize-irrigation-feedback.py &&" in finalizer_proof_block
            and "scripts/validate-irrigation-feedback.py" in finalizer_proof_block
            and "2>&1" in finalizer_proof_block
            and 'tee "$(IRRIGATION_FINALIZER_PROOF)"' in finalizer_proof_block
        )
        acceptance_block = makefile[
            makefile.index("irrigation-acceptance:") : makefile.index(
                "irrigation-full-acceptance:", makefile.index("irrigation-acceptance:")
            )
        ]
        acceptance_calls_finalize = "$(MAKE) irrigation-feedback-finalize" in acceptance_block
        acceptance_persists_field_watch = "$(MAKE) irrigation-feedback-watch-field-proof" in acceptance_block
        acceptance_persists_discovery = "$(MAKE) irrigation-feedback-discovery-proof" in acceptance_block
        acceptance_runs_sensor_health = "$(MAKE) irrigation-sensor-health-proof" in acceptance_block
        acceptance_emits_feedback_json = "$(MAKE) irrigation-feedback-proof-json" in acceptance_block
        acceptance_runs_stack_proof = "$(MAKE) irrigation-stack-proof" in acceptance_block
        acceptance_emits_completion_audit_json = "$(MAKE) irrigation-completion-audit-proof" in acceptance_block
        acceptance_runs_completion_audit = "\t$(MAKE) irrigation-completion-audit\n" in acceptance_block
        if acceptance_persists_field_watch and acceptance_persists_discovery and acceptance_runs_sensor_health:
            acceptance_runs_sensor_health = (
                acceptance_block.index("$(MAKE) irrigation-feedback-watch-field-proof")
                < acceptance_block.index("$(MAKE) irrigation-feedback-discovery-proof")
                < acceptance_block.index("$(MAKE) irrigation-sensor-health-proof")
                < acceptance_block.index("$(MAKE) irrigation-feedback-finalize")
            )
        if acceptance_emits_feedback_json:
            acceptance_emits_feedback_json = (
                acceptance_block.index("$(MAKE) irrigation-feedback-finalize")
                < acceptance_block.index("$(MAKE) irrigation-feedback-proof-json")
                < acceptance_block.index("$(MAKE) irrigation-stack-proof")
            )
        if acceptance_runs_stack_proof:
            acceptance_runs_stack_proof = acceptance_block.index(
                "$(MAKE) irrigation-feedback-proof-json"
            ) < acceptance_block.index("$(MAKE) irrigation-stack-proof")
        if acceptance_emits_completion_audit_json:
            acceptance_emits_completion_audit_json = acceptance_block.index(
                "$(MAKE) irrigation-stack-proof"
            ) < acceptance_block.index("$(MAKE) irrigation-completion-audit-proof")
        if acceptance_runs_completion_audit:
            acceptance_runs_completion_audit = acceptance_block.index(
                "$(MAKE) irrigation-completion-audit-proof"
            ) < acceptance_block.index("\t$(MAKE) irrigation-completion-audit\n")
        proof_block = makefile[
            makefile.index("irrigation-feedback-proof-json:") : makefile.index(
                "irrigation-acceptance:", makefile.index("irrigation-feedback-proof-json:")
            )
        ]
        feedback_proof_persisted = (
            "IRRIGATION_FEEDBACK_PROOF" in proof_block
            and "set -o pipefail" in proof_block
            and "--include-db-history" in proof_block
            and 'tee "$(IRRIGATION_FEEDBACK_PROOF)"' in proof_block
        )
        field_watch_proof_block = makefile[
            makefile.index("irrigation-feedback-watch-field-proof:") : makefile.index(
                "irrigation-feedback-finalize-dry-run:", makefile.index("irrigation-feedback-watch-field-proof:")
            )
        ]
        field_watch_proof_persisted = (
            "IRRIGATION_FIELD_WATCH_PROOF" in field_watch_proof_block
            and "set -o pipefail" in field_watch_proof_block
            and "scripts/validate-irrigation-feedback.py --watch --status-only --discover-ha --discover-mqtt --discover-mqtt-all --discover-esphome"
            in field_watch_proof_block
            and "--include-db-history" in field_watch_proof_block
            and 'tee "$(IRRIGATION_FIELD_WATCH_PROOF)"' in field_watch_proof_block
        )
        finalizer_dry_run_proof_block = makefile[
            makefile.index("irrigation-feedback-finalize-dry-run-proof:") : makefile.index(
                "irrigation-feedback-finalize:", makefile.index("irrigation-feedback-finalize-dry-run-proof:")
            )
        ]
        finalizer_dry_run_proof_persisted = (
            "IRRIGATION_FINALIZER_DRY_RUN_PROOF" in finalizer_dry_run_proof_block
            and "set -o pipefail" in finalizer_dry_run_proof_block
            and "scripts/finalize-irrigation-feedback.py --dry-run" in finalizer_dry_run_proof_block
            and "PIPESTATUS[0]" in finalizer_dry_run_proof_block
            and "Irrigation feedback still blocked: .*not_ok=" in finalizer_dry_run_proof_block
            and "2>&1" in finalizer_dry_run_proof_block
            and 'tee "$(IRRIGATION_FINALIZER_DRY_RUN_PROOF)"' in finalizer_dry_run_proof_block
        )
        sensor_health_block = makefile[
            makefile.index("irrigation-sensor-health-proof:") : makefile.index(
                "irrigation-acceptance:", makefile.index("irrigation-sensor-health-proof:")
            )
        ]
        sensor_health_proof_persisted = (
            "IRRIGATION_SENSOR_HEALTH_PROOF" in sensor_health_block
            and "set -o pipefail" in sensor_health_block
            and "$(MAKE) sensor-health SINCE='5 minutes'" in sensor_health_block
            and "2>&1" in sensor_health_block
            and 'tee "$(IRRIGATION_SENSOR_HEALTH_PROOF)"' in sensor_health_block
        )
        stack_proof_block = makefile[
            makefile.index("irrigation-stack-proof:") : makefile.index(
                "irrigation-acceptance:", makefile.index("irrigation-stack-proof:")
            )
        ]
        stack_proof_persisted = (
            "IRRIGATION_STACK_PROOF" in stack_proof_block
            and "set -o pipefail" in stack_proof_block
            and "$(MAKE) site-doctor" in stack_proof_block
            and "scripts/validate-irrigation-stack.py --live-site" in stack_proof_block
            and 'tee "$(IRRIGATION_STACK_PROOF)"' in stack_proof_block
        )
        acceptance_site_before_live = stack_proof_persisted and stack_proof_block.index(
            "$(MAKE) site-doctor"
        ) < stack_proof_block.index("scripts/validate-irrigation-stack.py --live-site")
        completion_audit_block = makefile[
            makefile.index("irrigation-completion-audit-proof:") : makefile.index(
                "irrigation-acceptance:", makefile.index("irrigation-completion-audit-proof:")
            )
        ]
        completion_audit_proof_persisted = (
            "IRRIGATION_COMPLETION_AUDIT_PROOF" in completion_audit_block
            and "set -o pipefail" in completion_audit_block
            and "scripts/irrigation-completion-audit.py --json --live-site --allow-physical-blocker --mqtt-live-timeout-s"
            in completion_audit_block
            and 'tee "$(IRRIGATION_COMPLETION_AUDIT_PROOF)"' in completion_audit_block
        )
        migration_proof_block = makefile[
            makefile.index("irrigation-migration-proof:") : makefile.index(
                "irrigation-field-diagnostics:", makefile.index("irrigation-migration-proof:")
            )
        ]
        migration_proof_persisted = (
            "IRRIGATION_MIGRATION_PROOF" in migration_proof_block
            and "set -o pipefail" in migration_proof_block
            and "db/migrations/134-irrigation-fertigation-canonical.sql" in migration_proof_block
            and "ROLLBACK" in migration_proof_block
            and 'tee "$(IRRIGATION_MIGRATION_PROOF)"' in migration_proof_block
        )
        field_diagnostics_block = makefile[
            makefile.index("irrigation-field-diagnostics:") : makefile.index(
                "irrigation-field-sensor-health-proof:", makefile.index("irrigation-field-diagnostics:")
            )
        ]
        field_sensor_health_proof_block = makefile[
            makefile.index("irrigation-field-sensor-health-proof:") : makefile.index(
                "irrigation-stack-software-check:", makefile.index("irrigation-field-sensor-health-proof:")
            )
        ]
        field_sensor_health_proof_persisted = (
            "IRRIGATION_FIELD_SENSOR_HEALTH_PROOF" in field_sensor_health_proof_block
            and "set -o pipefail" in field_sensor_health_proof_block
            and "$(MAKE) sensor-health SINCE='2 minutes'" in field_sensor_health_proof_block
            and "2>&1" in field_sensor_health_proof_block
            and 'tee "$(IRRIGATION_FIELD_SENSOR_HEALTH_PROOF)"' in field_sensor_health_proof_block
        )
        diagnostics_persists_sensor_health = (
            "$(MAKE) irrigation-field-sensor-health-proof" in field_diagnostics_block
            and "$(MAKE) irrigation-feedback-work-order-proof" in field_diagnostics_block
            and field_diagnostics_block.index("$(MAKE) irrigation-field-sensor-health-proof")
            < field_diagnostics_block.index("$(MAKE) irrigation-feedback-work-order-proof")
        )
        diagnostics_persists_work_order = (
            diagnostics_persists_sensor_health
            and "$(MAKE) irrigation-feedback-work-order-proof" in field_diagnostics_block
        )
        diagnostics_persists_completion_audit = (
            diagnostics_persists_work_order
            and "$(MAKE) irrigation-completion-audit-proof" in field_diagnostics_block
            and field_diagnostics_block.index("$(MAKE) irrigation-feedback-work-order-proof")
            < field_diagnostics_block.index("$(MAKE) irrigation-completion-audit-proof")
        )
        diagnostics_persists_discovery = (
            diagnostics_persists_completion_audit
            and "$(MAKE) irrigation-feedback-discovery-proof" in field_diagnostics_block
            and field_diagnostics_block.index("$(MAKE) irrigation-completion-audit-proof")
            < field_diagnostics_block.index("$(MAKE) irrigation-feedback-discovery-proof")
        )
        diagnostics_persists_finalizer_dry_run = (
            diagnostics_persists_discovery
            and "$(MAKE) irrigation-feedback-finalize-dry-run-proof" in field_diagnostics_block
            and field_diagnostics_block.index("$(MAKE) irrigation-feedback-discovery-proof")
            < field_diagnostics_block.index("$(MAKE) irrigation-feedback-finalize-dry-run-proof")
        )
        discovery_proof_block = makefile[
            makefile.index("irrigation-feedback-discovery-proof:") : makefile.index(
                "irrigation-feedback-work-order:", makefile.index("irrigation-feedback-discovery-proof:")
            )
        ]
        discovery_proof_persisted = (
            "IRRIGATION_DISCOVERY_PROOF" in discovery_proof_block
            and "set -o pipefail" in discovery_proof_block
            and "scripts/validate-irrigation-feedback.py --discover-ha --discover-mqtt --discover-mqtt-all --discover-esphome"
            in discovery_proof_block
            and "IRRIGATION_MQTT_LIVE_TIMEOUT" in discovery_proof_block
            and "if [ $$rc -eq 1 ]; then exit 0; fi" in discovery_proof_block
            and "2>&1" in discovery_proof_block
            and 'tee "$(IRRIGATION_DISCOVERY_PROOF)"' in discovery_proof_block
        )
        work_order_proof_block = makefile[
            makefile.index("irrigation-feedback-work-order-proof:") : makefile.index(
                "irrigation-feedback-clear-stale-retained:", makefile.index("irrigation-feedback-work-order-proof:")
            )
        ]
        work_order_proof_persisted = (
            "IRRIGATION_WORK_ORDER_PROOF" in work_order_proof_block
            and "set -o pipefail" in work_order_proof_block
            and "scripts/validate-irrigation-feedback.py --work-order" in work_order_proof_block
            and "2>&1" in work_order_proof_block
            and 'tee "$(IRRIGATION_WORK_ORDER_PROOF)"' in work_order_proof_block
        )
        full_acceptance_block = makefile[
            makefile.index("irrigation-full-acceptance:") : makefile.index(
                "firmware-deploy:", makefile.index("irrigation-full-acceptance:")
            )
        ]
        full_acceptance_order = [
            full_acceptance_block.index("$(MAKE) lint"),
            full_acceptance_block.index("$(MAKE) test"),
            full_acceptance_block.index("$(MAKE) irrigation-migration-proof"),
            full_acceptance_block.index("$(MAKE) irrigation-acceptance"),
        ]
        full_acceptance_includes_tests = full_acceptance_order == sorted(full_acceptance_order)
        post_deploy_plan_block = makefile[
            makefile.index("irrigation-post-deploy-acceptance-plan:") : makefile.index(
                "irrigation-post-deploy-acceptance:", makefile.index("irrigation-post-deploy-acceptance-plan:")
            )
        ]
        post_deploy_acceptance_plan_prints_only = (
            "Print non-mutating post-deploy acceptance sequence" in post_deploy_plan_block
            and "prints only; does not run checks" in post_deploy_plan_block
            and "make irrigation-feedback-finalize" in post_deploy_plan_block
            and "make irrigation-post-deploy-acceptance only after merge" in post_deploy_plan_block
            and "$(MAKE)" not in post_deploy_plan_block
            and "$(PYTHON)" not in post_deploy_plan_block
            and "scripts/" not in post_deploy_plan_block
        )
        post_deploy_acceptance_aliases_full = (
            "irrigation-post-deploy-acceptance: irrigation-full-acceptance" in makefile
            and "Post-deploy production proof after merge/restart/site publish" in makefile
        )
    except ValueError:
        pass
    if not order_ok:
        missing.append("finalize target dry-run before mutation")
    if not acceptance_calls_finalize:
        missing.append("acceptance target calls finalize target")
    if not acceptance_persists_field_watch:
        missing.append("acceptance target persists field watch proof before sensor-health")
    if not acceptance_persists_discovery:
        missing.append("acceptance target persists discovery proof after field watch")
    if not acceptance_runs_sensor_health:
        missing.append("acceptance target runs sensor-health after field watch and discovery proof")
    if not acceptance_emits_feedback_json:
        missing.append("acceptance target emits feedback proof JSON after finalizer")
    if not acceptance_runs_stack_proof:
        missing.append("acceptance target runs persisted stack proof after feedback proof")
    if not acceptance_emits_completion_audit_json:
        missing.append("acceptance target emits completion audit proof JSON after stack proof")
    if not acceptance_runs_completion_audit:
        missing.append("acceptance target runs strict objective completion audit after proof JSON")
    if not acceptance_site_before_live:
        missing.append("acceptance target runs site-doctor before live audit")
    if not stack_check_site_before_live:
        missing.append("stack-check target runs site-doctor before live audit")
    if not software_check_runs_direct_audit:
        missing.append("software-check target runs only the software audit")
    if not feedback_proof_persisted:
        missing.append("feedback proof target persists JSON with pipefail")
    if not field_watch_proof_persisted:
        missing.append("field watch proof target persists transcript with pipefail")
    if not finalizer_dry_run_proof_persisted:
        missing.append("finalizer dry-run proof target persists non-mutating closure preflight")
    if not finalizer_proof_persisted:
        missing.append(
            "finalizer proof target persists source evidence, dry-run, mutation, and feedback check transcript"
        )
    if not work_order_proof_persisted:
        missing.append("field work-order proof target persists transcript with pipefail")
    if not field_sensor_health_proof_persisted:
        missing.append("field sensor-health proof target persists short-window transcript with pipefail")
    if not diagnostics_persists_sensor_health:
        missing.append("field diagnostics persists sensor-health artifact before work-order")
    if not diagnostics_persists_work_order:
        missing.append("field diagnostics persists work-order artifact after field sensor-health")
    if not diagnostics_persists_completion_audit:
        missing.append("field diagnostics persists completion audit artifact after work-order")
    if not diagnostics_persists_discovery:
        missing.append("field diagnostics persists discovery artifact after completion audit")
    if not diagnostics_persists_finalizer_dry_run:
        missing.append("field diagnostics persists finalizer dry-run proof after discovery")
    if not discovery_proof_persisted:
        missing.append("field discovery proof target persists transcript with pipefail")
    if not sensor_health_proof_persisted:
        missing.append("sensor-health proof target persists transcript with pipefail")
    if not stack_proof_persisted:
        missing.append("stack proof target persists transcript with pipefail")
    if not completion_audit_proof_persisted:
        missing.append("completion audit proof target persists JSON with pipefail")
    if not migration_proof_persisted:
        missing.append("migration proof target persists rollback transcript with pipefail")
    if not full_acceptance_includes_tests:
        missing.append("full acceptance target runs lint, tests, persisted migration replay, then acceptance")
    if not post_deploy_acceptance_plan_prints_only:
        missing.append("post-deploy acceptance plan target is not print-only")
    if not post_deploy_acceptance_aliases_full:
        missing.append("post-deploy acceptance target aliases full acceptance")

    status = "pass" if not missing else "fail"
    return Check(
        "irrigation acceptance tooling",
        status,
        f"missing={missing or '-'} dry_run_before_finalize={order_ok} "
        f"acceptance_calls_finalize={acceptance_calls_finalize} "
        f"acceptance_persists_field_watch={acceptance_persists_field_watch} "
        f"acceptance_persists_discovery={acceptance_persists_discovery} "
        f"acceptance_runs_sensor_health={acceptance_runs_sensor_health} "
        f"acceptance_emits_feedback_json={acceptance_emits_feedback_json} "
        f"acceptance_runs_stack_proof={acceptance_runs_stack_proof} "
        f"acceptance_emits_completion_audit_json={acceptance_emits_completion_audit_json} "
        f"acceptance_runs_completion_audit={acceptance_runs_completion_audit} "
        f"acceptance_site_before_live={acceptance_site_before_live} "
        f"stack_check_site_before_live={stack_check_site_before_live} "
        f"software_check_runs_direct_audit={software_check_runs_direct_audit} "
        f"feedback_proof_persisted={feedback_proof_persisted} "
        f"field_watch_proof_persisted={field_watch_proof_persisted} "
        f"finalizer_dry_run_proof_persisted={finalizer_dry_run_proof_persisted} "
        f"finalizer_proof_persisted={finalizer_proof_persisted} "
        f"work_order_proof_persisted={work_order_proof_persisted} "
        f"field_sensor_health_proof_persisted={field_sensor_health_proof_persisted} "
        f"diagnostics_persists_sensor_health={diagnostics_persists_sensor_health} "
        f"diagnostics_persists_work_order={diagnostics_persists_work_order} "
        f"diagnostics_persists_completion_audit={diagnostics_persists_completion_audit} "
        f"diagnostics_persists_discovery={diagnostics_persists_discovery} "
        f"diagnostics_persists_finalizer_dry_run={diagnostics_persists_finalizer_dry_run} "
        f"discovery_proof_persisted={discovery_proof_persisted} "
        f"sensor_health_proof_persisted={sensor_health_proof_persisted} "
        f"stack_proof_persisted={stack_proof_persisted} "
        f"completion_audit_proof_persisted={completion_audit_proof_persisted} "
        f"migration_proof_persisted={migration_proof_persisted} "
        f"full_acceptance_includes_tests={full_acceptance_includes_tests} "
        f"post_deploy_acceptance_plan_prints_only={post_deploy_acceptance_plan_prints_only} "
        f"post_deploy_acceptance_aliases_full={post_deploy_acceptance_aliases_full} "
        f"runbook_ingestor_restart_doc={runbook_ingestor_restart_doc} "
        f"runbook_post_deploy_acceptance_doc={runbook_post_deploy_acceptance_doc} "
        f"runbook_post_deploy_plan_doc={runbook_post_deploy_plan_doc} "
        f"runbook_static_snapshot_boundary_doc={runbook_static_snapshot_boundary_doc}",
    )


def _curl_fetch(url: str, *, resolve_host: tuple[str, str] | None = None) -> subprocess.CompletedProcess[bytes]:
    if not shutil.which("curl"):
        raise RuntimeError("curl not found")
    cmd = [
        "curl",
        "-fsSL",
        "--retry",
        "3",
        "--retry-all-errors",
        "--retry-delay",
        "2",
        "--max-time",
        "30",
        "-A",
        "verdify-irrigation-audit/1.0",
    ]
    if resolve_host:
        host, ip = resolve_host
        cmd.extend(["--resolve", f"{host}:443:{ip}"])
    cmd.append(url)
    return subprocess.run(
        cmd,
        capture_output=True,
        timeout=120,
        check=False,
    )


def _check_live_public_page() -> Check:
    attempts = []
    for base_url in PUBLIC_SITE_BASES:
        url = f"{base_url}/greenhouse/irrigation"
        try:
            result = _curl_fetch(url)
        except RuntimeError as exc:
            attempts.append(f"{base_url}: error={exc}")
            continue
        body = result.stdout.decode(errors="replace")
        ok = (
            result.returncode == 0
            and "Irrigation and Fertigation" in body
            and "site-irrigation" in body
            and "panelId=12" in body
            and "panelId=13" in body
            and "panelId=14" in body
        )
        detail = f"base={base_url} curl_rc={result.returncode} bytes={len(result.stdout)} content_ok={ok}"
        if result.returncode != 0:
            detail += f" stderr={(result.stderr.decode(errors='replace') or '').strip()}"
        if ok:
            return Check("live public irrigation page", "pass", detail)
        attempts.append(detail)
    return Check("live public irrigation page", "fail", "; ".join(attempts))


def _check_live_public_discoverability() -> Check:
    attempts = []
    for base_url in PUBLIC_SITE_BASES:
        home_url = f"{base_url}/"
        sitemap_url = f"{base_url}/sitemap.xml"
        try:
            home_result = _curl_fetch(home_url)
            sitemap_result = _curl_fetch(sitemap_url)
            alias_results = {
                "irrigation": _curl_fetch(f"{base_url}/irrigation"),
                "climate/irrigation": _curl_fetch(f"{base_url}/climate/irrigation"),
                "water/irrigation": _curl_fetch(f"{base_url}/water/irrigation"),
            }
        except RuntimeError as exc:
            attempts.append(f"{base_url}: error={exc}")
            continue

        home = home_result.stdout.decode(errors="replace")
        sitemap = sitemap_result.stdout.decode(errors="replace")
        nav_link = home_result.returncode == 0 and 'href="/greenhouse/irrigation">Irrigation' in home
        sitemap_loc = (
            sitemap_result.returncode == 0 and "<loc>https://lab.verdify.ai/greenhouse/irrigation</loc>" in sitemap
        )
        alias_status = {
            "irrigation": (
                alias_results["irrigation"].returncode == 0
                and "url=./greenhouse/irrigation" in alias_results["irrigation"].stdout.decode(errors="replace")
            ),
            "climate/irrigation": (
                alias_results["climate/irrigation"].returncode == 0
                and "url=../greenhouse/irrigation"
                in alias_results["climate/irrigation"].stdout.decode(errors="replace")
            ),
            "water/irrigation": (
                alias_results["water/irrigation"].returncode == 0
                and "url=../greenhouse/irrigation" in alias_results["water/irrigation"].stdout.decode(errors="replace")
            ),
        }
        ok = nav_link and sitemap_loc and all(alias_status.values())
        detail = (
            f"base={base_url} home_rc={home_result.returncode} sitemap_rc={sitemap_result.returncode} "
            f"nav_link={nav_link} sitemap_loc={sitemap_loc} aliases={alias_status}"
        )
        if home_result.returncode != 0:
            detail += f" home_stderr={(home_result.stderr.decode(errors='replace') or '').strip()}"
        if sitemap_result.returncode != 0:
            detail += f" sitemap_stderr={(sitemap_result.stderr.decode(errors='replace') or '').strip()}"
        if ok:
            return Check("live public irrigation discoverability", "pass", detail)
        attempts.append(detail)
    return Check("live public irrigation discoverability", "fail", "; ".join(attempts))


def _dig_short(name: str, record_type: str, *, resolver: str | None = None) -> list[str]:
    if not shutil.which("dig"):
        raise RuntimeError("dig not found")
    cmd = ["dig", "+short"]
    if resolver:
        cmd.append(f"@{resolver}")
    cmd.extend([name, record_type])
    result = _run(cmd, timeout=15)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip())
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _check_live_graphs_dns() -> Check:
    details = []
    public_results = []
    for resolver in (None, *PUBLIC_DNS_RESOLVERS):
        label = resolver or "system"
        try:
            cname_rows = _dig_short("graphs.verdify.ai", "CNAME", resolver=resolver)
            graph_a = _dig_short("graphs.verdify.ai", "A", resolver=resolver)
            gateway_a = _dig_short(EXPECTED_PUBLIC_GATEWAY, "A", resolver=resolver)
        except RuntimeError as exc:
            details.append(f"{label}: error={exc}")
            if resolver:
                public_results.append(False)
            continue

        resolver_ok = EXPECTED_PUBLIC_GATEWAY in cname_rows or bool(set(graph_a) & set(gateway_a))
        if resolver:
            public_results.append(resolver_ok)
        details.append(
            f"{label}: cname={cname_rows or '-'} graph_a={graph_a or '-'} gateway_a={gateway_a or '-'} ok={resolver_ok}"
        )

    ok = bool(public_results) and all(public_results)
    return Check(
        "live graphs DNS routing",
        "pass" if ok else "fail",
        "; ".join(details),
    )


def _graph_gateway_ip_for_render() -> str | None:
    for resolver in (*PUBLIC_DNS_RESOLVERS, None):
        try:
            rows = _dig_short(EXPECTED_PUBLIC_GATEWAY, "A", resolver=resolver)
        except RuntimeError:
            continue
        for row in rows:
            if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", row):
                return row
    return None


def _paeth_predictor(left: int, up: int, up_left: int) -> int:
    p = left + up - up_left
    pa = abs(p - left)
    pb = abs(p - up)
    pc = abs(p - up_left)
    if pa <= pb and pa <= pc:
        return left
    if pb <= pc:
        return up
    return up_left


def _decode_png_rgb_rows(png: bytes) -> list[bytes]:
    if not png.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("not a PNG")

    pos = 8
    width: int | None = None
    height: int | None = None
    channels: int | None = None
    idat_parts: list[bytes] = []

    while pos + 8 <= len(png):
        length = int.from_bytes(png[pos : pos + 4], "big")
        chunk_type = png[pos + 4 : pos + 8]
        data_start = pos + 8
        data_end = data_start + length
        if data_end + 4 > len(png):
            raise ValueError("truncated PNG chunk")
        data = png[data_start:data_end]
        pos = data_end + 4

        if chunk_type == b"IHDR":
            if length != 13:
                raise ValueError("invalid IHDR")
            width = int.from_bytes(data[0:4], "big")
            height = int.from_bytes(data[4:8], "big")
            bit_depth = data[8]
            color_type = data[9]
            compression = data[10]
            filter_method = data[11]
            interlace = data[12]
            channels = 3 if color_type == 2 else None
            if bit_depth != 8 or channels is None or compression != 0 or filter_method != 0 or interlace != 0:
                raise ValueError("unsupported PNG format")
        elif chunk_type == b"IDAT":
            idat_parts.append(data)
        elif chunk_type == b"IEND":
            break

    if width is None or height is None or channels is None or not idat_parts:
        raise ValueError("missing PNG image data")

    raw = zlib.decompress(b"".join(idat_parts))
    stride = width * channels
    expected = (stride + 1) * height
    if len(raw) < expected:
        raise ValueError("truncated PNG raster")

    rows: list[bytes] = []
    previous = bytearray(stride)
    offset = 0
    for _ in range(height):
        filter_type = raw[offset]
        offset += 1
        scanline = bytearray(raw[offset : offset + stride])
        offset += stride
        for idx, value in enumerate(scanline):
            left = scanline[idx - channels] if idx >= channels else 0
            up = previous[idx]
            up_left = previous[idx - channels] if idx >= channels else 0
            if filter_type == 0:
                recon = value
            elif filter_type == 1:
                recon = value + left
            elif filter_type == 2:
                recon = value + up
            elif filter_type == 3:
                recon = value + ((left + up) // 2)
            elif filter_type == 4:
                recon = value + _paeth_predictor(left, up, up_left)
            else:
                raise ValueError(f"unsupported PNG filter {filter_type}")
            scanline[idx] = recon & 0xFF
        rows.append(bytes(scanline))
        previous = scanline
    return rows


def _relay_state_png_visual_check(png: bytes) -> tuple[bool, str]:
    try:
        rows = _decode_png_rgb_rows(png)
    except (ValueError, zlib.error) as exc:
        return False, f"relay_visual=unreadable:{exc}"

    total = max(sum(len(row) // 3 for row in rows), 1)
    gray_pixels = 0
    white_pixels = 0
    blue_pixels = 0
    for row in rows:
        for idx in range(0, len(row), 3):
            r, g, b = row[idx], row[idx + 1], row[idx + 2]
            if r > 245 and g > 245 and b > 245:
                white_pixels += 1
            if 80 <= r <= 220 and 80 <= g <= 220 and 80 <= b <= 220 and max(r, g, b) - min(r, g, b) <= 12:
                gray_pixels += 1
            if b > 150 and g > 80 and r < 140 and b - r > 60:
                blue_pixels += 1

    gray_pct = gray_pixels / total
    white_pct = white_pixels / total
    ok = gray_pct >= 0.20 and white_pct <= 0.55
    return (
        ok,
        f"relay_visual_gray_pct={gray_pct:.3f} relay_visual_white_pct={white_pct:.3f} "
        f"relay_visual_blue_px={blue_pixels}",
    )


def _check_live_site() -> Check:
    attempts = []
    ok = True
    min_png_bytes = {9: 20_000, 12: 20_000, 13: 20_000, 14: 20_000, 15: 20_000}
    gateway_ip = _graph_gateway_ip_for_render()
    resolve_host = ("graphs.verdify.ai", gateway_ip) if gateway_ip else None
    try:
        page_html = SITE_IRRIGATION_HTML.read_text()
    except OSError:
        page_html = ""
    for panel_id, height in ((9, 360), (12, 310), (13, 320), (14, 320), (15, 340)):
        match = re.search(rf'data-image-src="([^"]*panelId={panel_id}[^"]*)"', page_html)
        if match:
            url = html.unescape(match.group(1))
        else:
            url = (
                "https://graphs.verdify.ai/render/d-solo/site-irrigation/"
                f"?orgId=1&panelId={panel_id}&theme=light&from=now-7d&to=now&width=1240&height={height}"
            )
        try:
            result = _curl_fetch(url, resolve_host=resolve_host)
        except RuntimeError as exc:
            attempts.append(f"panel{panel_id}: error={exc}")
            ok = False
            continue
        is_png = result.stdout.startswith(b"\x89PNG")
        large_enough = len(result.stdout) >= min_png_bytes[panel_id]
        visual_ok = True
        visual_detail = ""
        if panel_id == 9 and is_png:
            visual_ok, visual_detail = _relay_state_png_visual_check(result.stdout)
        panel_ok = result.returncode == 0 and is_png and large_enough and visual_ok
        ok = ok and panel_ok
        detail = (
            f"panel{panel_id}: curl_rc={result.returncode} bytes={len(result.stdout)} "
            f"png={is_png} min_bytes={min_png_bytes[panel_id]}"
        )
        if visual_detail:
            detail += f" {visual_detail}"
        if resolve_host:
            detail += f" resolve={resolve_host[0]}:443:{resolve_host[1]}"
        if result.returncode != 0:
            detail += f" stderr={(result.stderr.decode(errors='replace') or '').strip()}"
        attempts.append(detail)
    return Check("live irrigation dashboard render", "pass" if ok else "fail", "; ".join(attempts))


def run_checks(*, software_only: bool, live_site: bool, direct_db: bool) -> list[Check]:
    checks = [
        _check_legacy_retired(direct_db),
        _check_data_trust_ledger_canonical_irrigation(direct_db),
        _check_schedule_current(direct_db),
        _check_schedule_cfg_readbacks(direct_db),
        _check_schedule_setpoint_confirmations(direct_db),
        _check_planner_context_uses_canonical_irrigation(),
        _check_schema_contract_marks_legacy_irrigation_retired(),
        _check_schema_snapshot_irrigation_contract(),
        _check_fertigation_runs(direct_db),
        _check_fertigation_run_coherence(direct_db),
        _check_daily_accounting(direct_db),
        _check_feedback_value_range_gate(direct_db),
        _check_feedback_alias_alignment(),
        _check_dashboard_files(),
        _check_site_discoverability(),
        _check_acceptance_tooling(),
    ]
    if live_site:
        checks.append(_check_live_public_page())
        checks.append(_check_live_public_discoverability())
        checks.append(_check_live_graphs_dns())
        checks.append(_check_live_site())
    feedback = _check_feedback(direct_db)
    if software_only and feedback.blocked:
        feedback = Check(feedback.name, "blocked", f"{feedback.detail} (ignored by --software-only)")
    checks.append(feedback)
    return checks


def print_text(checks: list[Check]) -> None:
    for check in checks:
        print(f"{check.status.upper():7} {check.name}: {check.detail}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    parser.add_argument(
        "--software-only", action="store_true", help="Do not fail the audit for physical feedback blockers"
    )
    parser.add_argument("--live-site", action="store_true", help="Render the public Grafana feedback panel")
    parser.add_argument("--direct-db", action="store_true", help="Use local psql instead of docker exec when available")
    args = parser.parse_args()

    try:
        checks = run_checks(software_only=args.software_only, live_site=args.live_site, direct_db=args.direct_db)
    except Exception as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True))
        else:
            print(f"Irrigation stack audit could not run: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({"checks": [check.__dict__ for check in checks]}, indent=2, sort_keys=True))
    else:
        print_text(checks)

    failed = any(check.failed for check in checks)
    blocked = any(check.blocked for check in checks)
    if failed:
        return 1
    if blocked and not args.software_only:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
