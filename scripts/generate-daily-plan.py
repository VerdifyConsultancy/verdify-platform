#!/usr/bin/env /srv/greenhouse/.venv/bin/python3
"""
generate-daily-plan.py — Generate or update daily plan documents for verdify.ai

Usage:
  # Backfill all historical days from plan_journal + daily_summary
  python3 generate-daily-plan.py --backfill

  # Generate/update today's plan document for a specific cycle
  python3 generate-daily-plan.py --date 2026-03-30 --cycle morning --plan-id iris-20260330-0600

  # Generate from live DB data (called by planner cron)
  python3 generate-daily-plan.py --today --cycle morning --plan-id iris-20260330-0600

Output: /srv/verdify/verdify-site/content/plans/YYYY-MM-DD.md
"""

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path

import yaml

sys.path.insert(0, "/mnt/iris/verdify")
from verdify_schemas import DailyPlanVaultFrontmatter  # noqa: E402

CONTENT_DIR = Path("/srv/verdify/verdify-site/content/plans")
DB_CMD = "docker exec verdify-timescaledb psql -U verdify -d verdify -t -A"
DB_TIMEOUT_S = int(os.environ.get("VERDIFY_DAILY_PLAN_DB_TIMEOUT_S", "60"))


def _yaml_escape(val: str) -> str:
    """Escape a string for safe inclusion in double-quoted YAML values."""
    if not val:
        return ""
    return val.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def db_query(sql: str) -> str:
    """Run a psql query and return stripped output."""
    cmd = [
        *shlex.split(os.environ.get("VERDIFY_DAILY_PLAN_DB_CMD", DB_CMD)),
        "-v",
        "ON_ERROR_STOP=1",
        "-c",
        sql,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=DB_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"daily-plan DB query timed out after {DB_TIMEOUT_S}s") from exc

    if result.returncode != 0:
        stderr = result.stderr.strip() or "no stderr"
        raise RuntimeError(f"daily-plan DB query failed with exit {result.returncode}: {stderr}")
    return result.stdout.strip()


def db_query_rows(sql: str) -> list[list[str]]:
    """Run a psql query and return list of rows (pipe-delimited)."""
    raw = db_query(sql)
    if not raw:
        return []
    return [row.split("|") for row in raw.split("\n") if row.strip()]


def db_query_json(sql: str) -> dict | list | None:
    """Run a psql query expecting JSON output."""
    raw = db_query(sql)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _sql_literal(value: object) -> str:
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def public_text(value: object) -> str:
    """Scrub implementation names and avoid Quartz dollar-sign math parsing."""
    text = str(value or "")
    text = re.sub(r"iris-hermes-validation", "iris-validation", text, flags=re.IGNORECASE)
    text = re.sub(r"\bHermes\b", "planning gateway", text)
    text = re.sub(r"\bhermes\b", "planner-gateway", text)
    text = re.sub(r"\bOpenClaw/Iris\b", "planner", text)
    text = re.sub(r"\bIris\b(?!-)", "AI planning agent", text)
    text = re.sub(r"\bOpenClaw\b", "planner gateway", text)
    text = re.sub(r"\blocal Gemma context overflow\b", "planner context overflow", text, flags=re.IGNORECASE)
    text = re.sub(r"\blocal Gemma overflow\b", "planner context overflow", text, flags=re.IGNORECASE)
    text = re.sub(r"\blocal Gemma\b", "planner", text, flags=re.IGNORECASE)
    return re.sub(r"\$(\d)", r"USD \1", text)


def public_summary(value: object, max_chars: int = 900) -> str:
    """Render stored planner prose without raw structured payloads."""
    text = public_text(value)
    text = re.sub(r"```(?:json)?\s*.*?```", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars].rstrip()
    sentence_end = max(truncated.rfind("."), truncated.rfind("?"), truncated.rfind("!"))
    if sentence_end >= max_chars * 0.65:
        truncated = truncated[: sentence_end + 1]
    return truncated.rstrip(" ,;:.") + "..."


def normalized_public_block(value: object) -> str:
    return re.sub(r"\s+", " ", public_text(value)).strip()


def get_daily_summary(d: date) -> dict:
    """Get daily_summary row as dict."""
    return (
        db_query_json(f"""
        SELECT row_to_json(ds) FROM (
            SELECT date, temp_min, temp_max, temp_avg, vpd_min, vpd_max, vpd_avg,
                   rh_min, rh_max, co2_avg, dli_final,
                   cost_electric, cost_gas, cost_water, cost_total,
                   water_used_gal, mister_water_gal,
                   stress_hours_heat, stress_hours_cold, stress_hours_vpd_high, stress_hours_vpd_low,
                   runtime_fan1_min, runtime_fan2_min, runtime_fog_min,
                   runtime_heat1_min, runtime_heat2_min, runtime_vent_min,
                   runtime_grow_light_min,
                   runtime_mister_south_h, runtime_mister_west_h, runtime_mister_center_h,
                   runtime_drip_wall_h
            FROM daily_summary WHERE date = '{d}'
        ) ds
    """)
        or {}
    )


def record_plan_archive_audit(d: date, output: Path, plans: list[dict], summary: dict, content: str) -> None:
    """Store a DB-side self-check row for generated daily plan pages."""
    db_plan_count_raw = db_query(f"""
        SELECT count(*) FROM plan_journal
        WHERE plan_id LIKE 'iris-{d.strftime("%Y%m%d")}%'
          AND plan_id NOT LIKE 'iris-reactive%'
          AND plan_id NOT LIKE 'iris-fix%'
    """)
    try:
        db_plan_count = int(db_plan_count_raw or "0")
    except ValueError:
        db_plan_count = len(plans)

    def audit_float(value: object) -> float | None:
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    generated_cost = audit_float(summary.get("cost_total"))
    generated_water = audit_float(summary.get("water_used_gal"))
    checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()
    stale = "false" if db_plan_count == len(plans) else "true"
    db_query(
        f"""
        INSERT INTO daily_plan_archive_audit (
            date, generated_at, page_path, generated_plan_count, db_plan_count,
            generated_cost_total, db_cost_total, generated_water_gal, db_water_gal,
            content_checksum, stale, notes
        )
        VALUES (
            '{d}'::date, now(), {_sql_literal(output)}, {len(plans)}, {db_plan_count},
            {generated_cost if generated_cost is not None else "NULL"},
            {generated_cost if generated_cost is not None else "NULL"},
            {generated_water if generated_water is not None else "NULL"},
            {generated_water if generated_water is not None else "NULL"},
            {_sql_literal(checksum)}, {stale}, 'scripts/generate-daily-plan.py'
        )
        ON CONFLICT (date) DO UPDATE SET
            generated_at = EXCLUDED.generated_at,
            page_path = EXCLUDED.page_path,
            generated_plan_count = EXCLUDED.generated_plan_count,
            db_plan_count = EXCLUDED.db_plan_count,
            generated_cost_total = EXCLUDED.generated_cost_total,
            db_cost_total = EXCLUDED.db_cost_total,
            generated_water_gal = EXCLUDED.generated_water_gal,
            db_water_gal = EXCLUDED.db_water_gal,
            content_checksum = EXCLUDED.content_checksum,
            stale = EXCLUDED.stale,
            notes = EXCLUDED.notes
        """
    )


def get_plans_for_date(d: date) -> list[dict]:
    """Get all plan_journal entries whose plan_id references this date."""
    date_str = d.strftime("%Y%m%d")
    rows = (
        db_query_json(f"""
        SELECT COALESCE(json_agg(row_to_json(p)), '[]'::json)
        FROM (
            SELECT plan_id,
                   to_char(created_at AT TIME ZONE 'America/Denver', 'YYYY-MM-DD HH24:MI') as created,
                   COALESCE(conditions_summary, '') AS conditions_summary,
                   COALESCE(hypothesis, '') AS hypothesis,
                   COALESCE(experiment, '') AS experiment,
                   COALESCE(expected_outcome, '') AS expected_outcome,
                   COALESCE(actual_outcome, '') AS actual_outcome,
                   COALESCE(outcome_score::text, '') AS outcome_score,
                   COALESCE(lesson_extracted, '') AS lesson_extracted,
                   COALESCE(array_to_string(params_changed, ','), '') AS params_changed,
                   CASE WHEN validated_at IS NULL THEN 'pending' ELSE 'validated' END AS status,
                   COALESCE(hypothesis_structured::text, '') AS hypothesis_structured
            FROM plan_journal
            WHERE plan_id LIKE 'iris-{date_str}%'
              AND plan_id NOT LIKE 'iris-reactive%'
              AND plan_id NOT LIKE 'iris-fix%'
            ORDER BY created_at
        ) p
    """)
        or []
    )
    return [{k: str(v or "").strip() for k, v in row.items()} for row in rows]


def get_plan_delivery_for_date(d: date) -> list[dict]:
    """Get planner delivery events for a local date.

    These rows are distinct from full plan_journal cycles. A delivery can be
    acknowledged, left pending, or write a small one-shot setpoint correction
    without becoming a public Tier 1 planning cycle.
    """
    next_day = d + timedelta(days=1)
    rows = (
        db_query_json(f"""
        SELECT COALESCE(json_agg(row_to_json(e)), '[]'::json)
        FROM (
            SELECT event_type,
                   event_label,
                   status,
                   to_char(delivered_at AT TIME ZONE 'America/Denver', 'YYYY-MM-DD HH24:MI') AS delivered,
                   COALESCE(to_char(acked_at AT TIME ZONE 'America/Denver', 'YYYY-MM-DD HH24:MI'), '') AS acked,
                   COALESCE(resulting_plan_id, '') AS resulting_plan_id,
                   COALESCE(to_char(plan_written_at AT TIME ZONE 'America/Denver', 'YYYY-MM-DD HH24:MI'), '') AS plan_written,
                   COALESCE(gateway_body, '') AS gateway_body
            FROM plan_delivery_log
            WHERE delivered_at >= '{d} 00:00:00 America/Denver'::timestamptz
              AND delivered_at < '{next_day} 00:00:00 America/Denver'::timestamptz
              AND event_type IN (
                  'SUNRISE', 'MIDDAY', 'SUNSET', 'MIDNIGHT',
                  'SOLAR_MAX', 'TRANSITION', 'FORECAST',
                  'FORECAST_DEVIATION', 'MANUAL'
              )
            ORDER BY delivered_at
        ) e
    """)
        or []
    )
    return [{k: str(v or "").strip() for k, v in row.items()} for row in rows]


def get_waypoints_for_plan(plan_id: str) -> list[dict]:
    """Get setpoint_plan waypoints for a given plan_id."""
    rows = db_query_rows(f"""
        SELECT to_char(ts AT TIME ZONE 'America/Denver', 'YYYY-MM-DD HH24:MI') as apply_mdt,
               parameter, value, reason
        FROM setpoint_plan
        WHERE plan_id = '{plan_id}'
        ORDER BY ts, parameter
    """)
    waypoints = []
    for row in rows:
        if len(row) >= 4:
            waypoints.append(
                {
                    "time": row[0].strip(),
                    "parameter": row[1].strip(),
                    "value": row[2].strip(),
                    "reason": row[3].strip(),
                }
            )
    return waypoints


def get_zone_snapshot(d: date, hour: int = 6) -> dict:
    """Get zone conditions at a specific hour of the given date."""
    ts = f"{d} {hour:02d}:00:00-06"
    return (
        db_query_json(f"""
        SELECT json_build_object(
            'temp_south', round(temp_south::numeric,1),
            'temp_east', round(temp_east::numeric,1),
            'temp_west', round(temp_west::numeric,1),
            'temp_north', round(temp_north::numeric,1),
            'vpd_south', round(vpd_south::numeric,2),
            'vpd_east', round(vpd_east::numeric,2),
            'vpd_west', round(vpd_west::numeric,2),
            'vpd_north', round(vpd_north::numeric,2),
            'rh_south', round(rh_south::numeric,1),
            'rh_east', round(rh_east::numeric,1),
            'rh_west', round(rh_west::numeric,1),
            'rh_north', round(rh_north::numeric,1),
            'outdoor_temp_f', round(outdoor_temp_f::numeric,1),
            'outdoor_rh_pct', round(outdoor_rh_pct::numeric,1),
            'co2_ppm', round(co2_ppm::numeric,0),
            'lux', round(lux::numeric,0),
            'soil_moisture_south_1', round(COALESCE(soil_moisture_south_1,0)::numeric,1),
            'soil_moisture_south_2', round(COALESCE(soil_moisture_south_2,0)::numeric,1),
            'soil_moisture_west', round(COALESCE(soil_moisture_west,0)::numeric,1)
        )
        FROM climate
        WHERE ts >= '{ts}'::timestamptz - interval '30 minutes'
          AND ts <= '{ts}'::timestamptz + interval '30 minutes'
        ORDER BY ts
        LIMIT 1
    """)
        or {}
    )


def get_active_setpoints_at(d: date) -> dict:
    """Get the active setpoint values as of a given date."""
    ts = f"{d} 12:00:00-06"
    rows = db_query_rows(f"""
        SELECT DISTINCT ON (parameter) parameter, value
        FROM setpoint_changes
        WHERE ts <= '{ts}'::timestamptz
        ORDER BY parameter, ts DESC
    """)
    return {row[0].strip(): row[1].strip() for row in rows if len(row) >= 2}


def get_hourly_pattern(d: date) -> list[dict]:
    """Get hourly temp/vpd/rh pattern for the day."""
    rows = db_query_rows(f"""
        SELECT to_char(date_trunc('hour', ts) AT TIME ZONE 'America/Denver', 'HH24:00') as hour,
               round(avg(temp_avg)::numeric,1) as temp,
               round(avg(vpd_avg)::numeric,2) as vpd,
               round(avg(rh_avg)::numeric,1) as rh
        FROM climate
        WHERE ts >= '{d}'::date AT TIME ZONE 'America/Denver'
          AND ts < ('{d}'::date + 1) AT TIME ZONE 'America/Denver'
        GROUP BY date_trunc('hour', ts)
        ORDER BY date_trunc('hour', ts)
    """)
    return [
        {"hour": r[0].strip(), "temp": r[1].strip(), "vpd": r[2].strip(), "rh": r[3].strip()}
        for r in rows
        if len(r) >= 4
    ]


def get_stress_context(d: date) -> list[dict]:
    """Get 7-day stress context ending on this date."""
    rows = db_query_rows(f"""
        SELECT date, round(stress_hours_heat::numeric,1), round(stress_hours_vpd_high::numeric,1),
               round(stress_hours_cold::numeric,1), round(stress_hours_vpd_low::numeric,1)
        FROM daily_summary
        WHERE date >= '{d}'::date - 6 AND date <= '{d}'
        ORDER BY date
    """)
    return [
        {
            "date": r[0].strip(),
            "heat": r[1].strip(),
            "vpd_high": r[2].strip(),
            "cold": r[3].strip(),
            "vpd_low": r[4].strip(),
        }
        for r in rows
        if len(r) >= 5
    ]


def classify_cycle(plan_id: str) -> str:
    """Classify a plan_id into morning/midday/evening/overnight."""
    # Extract time portion: iris-20260325-0605 → 0605
    parts = plan_id.split("-")
    if len(parts) >= 3:
        time_part = parts[-1]
        if len(time_part) == 4:
            hour = int(time_part[:2])
            if hour < 10:
                return "morning"
            elif hour < 15:
                return "midday"
            elif hour < 21:
                return "evening"
            else:
                return "overnight"
    return "unknown"


def r(v, digits=1):
    """Round a numeric value for display."""
    if v is None:
        return "—"
    try:
        return f"{float(v):.{digits}f}"
    except (ValueError, TypeError):
        return str(v) if v else "—"


CORE_PARAMS = [
    "temp_high",
    "temp_low",
    "vpd_high",
    "vpd_hysteresis",
    "cool_stage2_over_high_f",
    "cool_exit_hysteresis_f",
    "sw_cool_all_fans_at_high_enabled",
    "sw_direct_wet_stress_override_enabled",
    "direct_wet_stress_latest_hour",
    "sw_fog_stress_window_extend_enabled",
    "fog_stress_window_latest_hour",
    "mister_engage_kpa",
    "mister_all_kpa",
    "mister_pulse_on_s",
    "mister_pulse_gap_s",
    "mister_vpd_weight",
]
PRIMARY_BAND_PARAMS = [
    "temp_high",
    "temp_low",
    "vpd_high",
    "vpd_hysteresis",
]
TACTICAL_TUNABLE_PARAMS = [
    "cool_stage2_over_high_f",
    "cool_exit_hysteresis_f",
    "sw_cool_all_fans_at_high_enabled",
    "sw_direct_wet_stress_override_enabled",
    "direct_wet_stress_latest_hour",
    "sw_fog_stress_window_extend_enabled",
    "fog_stress_window_latest_hour",
    "mister_engage_kpa",
    "mister_all_kpa",
    "mister_pulse_on_s",
    "mister_pulse_gap_s",
    "mister_vpd_weight",
]
PARAM_SHORT = {
    "temp_high": "high",
    "temp_low": "low",
    "vpd_high": "vpd_h",
    "vpd_hysteresis": "hyst",
    "cool_stage2_over_high_f": "s2",
    "cool_exit_hysteresis_f": "cool_exit",
    "sw_cool_all_fans_at_high_enabled": "all_fans",
    "sw_direct_wet_stress_override_enabled": "dw_stress",
    "direct_wet_stress_latest_hour": "dw_until",
    "sw_fog_stress_window_extend_enabled": "fog_stress",
    "fog_stress_window_latest_hour": "fog_until",
    "mister_engage_kpa": "engage",
    "mister_all_kpa": "all",
    "mister_pulse_on_s": "pulse",
    "mister_pulse_gap_s": "gap",
    "mister_vpd_weight": "wt",
}


def _has_value(value: object) -> bool:
    return value is not None and str(value).strip() not in {"", "·", "-", "None", "null"}


def status_label(status: object) -> str:
    status_text = str(status or "pending").strip()
    if status_text == "pending":
        return "Daily Summary So Far"
    return status_text


def compact_time(local_timestamp: object) -> str:
    text = str(local_timestamp or "").strip()
    if len(text) >= 16:
        return text[11:16]
    return text or "unknown"


def event_display_label(event: dict) -> str:
    label = str(event.get("event_label") or "").strip()
    if label and not label.lstrip().startswith(("{", "[")):
        return public_text(label)
    event_type = str(event.get("event_type") or "").strip()
    fallback = {
        "MIDNIGHT": "End-of-day review and reset",
        "SUNRISE": "Morning planning cycle",
        "SOLAR_MAX": "Solar peak planning checkpoint",
        "TRANSITION": "Stress transition checkpoint",
        "SUNSET": "Evening planning cycle",
        "FORECAST_DEVIATION": "Forecast deviation",
        "MANUAL": "Manual planner request",
    }
    return fallback.get(event_type, event_type or "Planner event")


def delivery_public_note(event: dict, max_chars: int = 520) -> str:
    """Extract public prose from a plan_delivery_log gateway body."""
    body = str(event.get("gateway_body") or "").strip()
    if not body:
        return ""

    lines = []
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("{") and line.endswith("}"):
            continue
        lines.append(line)

    text = " ".join(lines)
    text = re.sub(r"^acknowledged by [^:]+:\s*", "Acknowledged: ", text, flags=re.IGNORECASE)
    return public_summary(text, max_chars=max_chars)


def data_table(rows: list[tuple[str, str, str]]) -> str:
    if not rows:
        return '<div class="metric-grid">\n  <div class="metric-card"><strong>No data</strong><p>No rows available.</p></div>\n</div>'
    lines = ['<div class="data-table">']
    for title, meta, body in rows:
        lines.append(
            f'  <div class="data-row"><strong>{escape(str(title))}</strong>'
            f"<span>{escape(str(meta))}</span><p>{escape(str(body))}</p></div>"
        )
    lines.append("</div>")
    return "\n".join(lines)


def _class_attr(classes: list[str]) -> str:
    return f' class="{" ".join(classes)}"' if classes else ""


def lab_table(
    headers: list[str],
    rows: list[tuple[object, ...]],
    *,
    numeric_cols: set[int] | None = None,
    nowrap_cols: set[int] | None = None,
) -> str:
    if not rows:
        return '<div class="metric-grid">\n  <div class="metric-card"><strong>No data</strong><p>No rows available.</p></div>\n</div>'
    numeric_cols = numeric_cols or set()
    nowrap_cols = nowrap_cols or set()
    lines = ['<div class="lab-table-wrap">', '  <table class="lab-table">', "    <thead>", "      <tr>"]
    for idx, header in enumerate(headers):
        classes: list[str] = []
        if idx in numeric_cols:
            classes.append("is-num")
        if idx in nowrap_cols:
            classes.append("is-nowrap")
        lines.append(f"        <th{_class_attr(classes)}>{escape(str(header))}</th>")
    lines.extend(["      </tr>", "    </thead>", "    <tbody>"])
    for row in rows:
        lines.append("      <tr>")
        for idx, cell in enumerate(row):
            classes = []
            if idx in numeric_cols:
                classes.append("is-num")
            if idx in nowrap_cols:
                classes.append("is-nowrap")
            lines.append(f"        <td{_class_attr(classes)}>{escape(str(cell))}</td>")
        lines.append("      </tr>")
    lines.extend(["    </tbody>", "  </table>", "</div>"])
    return "\n".join(lines)


def metric_grid(cards: list[tuple[str, str]]) -> str:
    if not cards:
        return '<div class="metric-grid">\n  <div class="metric-card"><strong>No data</strong><p>No values available.</p></div>\n</div>'
    lines = ['<div class="metric-grid">']
    for title, body in cards:
        lines.append(
            f'  <div class="metric-card"><strong>{escape(str(title))}</strong><p>{escape(str(body))}</p></div>'
        )
    lines.append("</div>")
    return "\n".join(lines)


def _render_structured_hypothesis(s: dict) -> list[str]:
    """Render PlanHypothesisStructured JSON into a Conditions / Stress Windows /
    Rationale markdown block. Silent no-op if a section is missing."""
    lines: list[str] = []
    conds = s.get("conditions") or {}
    if conds:
        lines.append("")
        lines.append("**Conditions**")
        lines.append("")
        lines.append(
            metric_grid(
                [
                    ("Outdoor peak", f"{conds.get('outdoor_temp_peak_f', '?')}°F"),
                    ("RH minimum", f"{conds.get('outdoor_rh_min_pct', '?')}%"),
                    ("Solar peak", f"{conds.get('solar_peak_w_m2', '?')} W/m²"),
                    ("Cloud average", f"{conds.get('cloud_cover_avg_pct', '?')}%"),
                ]
            )
        )
        if conds.get("notes"):
            lines.append("")
            lines.append(f"> {public_text(conds['notes'])}")
    sw = s.get("stress_windows") or []
    if sw:
        lines.append("")
        lines.append("**Expected stress windows**")
        lines.append("")
        rows = []
        for w in sw:
            rows.append(
                (
                    w.get("kind", "?"),
                    f"{w.get('severity', '?')} · {w.get('start', '?')} to {w.get('end', '?')}",
                    public_text(w.get("mitigation", "")),
                )
            )
        lines.append(data_table(rows))
    rat = s.get("rationale") or []
    if rat:
        lines.append("")
        lines.append("**Parameter rationale**")
        lines.append("")
        rows = []
        for r_ in rat:
            old = r_.get("old_value")
            new = r_.get("new_value")
            change = f"{old} → {new}" if old is not None else f"{new}"
            rows.append(
                (
                    r_.get("parameter", "?"),
                    public_text(f"{change}; {r_.get('forecast_anchor', '')}"),
                    public_text(r_.get("expected_effect", "")),
                )
            )
        lines.append(data_table(rows))
    lines.append("")
    return lines


def _format_param_values(vals: dict, params: list[str]) -> str:
    cols = []
    for p in params:
        v = vals.get(p, "")
        if _has_value(v):
            cols.append(f"{PARAM_SHORT.get(p, p)} {v}")
    return "; ".join(cols)


def _waypoint_rows(
    times: list[str],
    by_time: dict,
    notes_by_time: dict,
    params: list[str],
    fallback_note: str,
) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for t in times:
        vals = by_time[t]
        meta = _format_param_values(vals, params)
        if not meta:
            continue
        time_str = t[11:16] if len(t) > 11 else t
        note = public_text(notes_by_time.get(t, ""))
        note = note.replace("|", "—")[:80]
        rows.append((time_str, meta, note or fallback_note))
    return rows


def format_waypoints_table(waypoints: list[dict]) -> str:
    """Format waypoints as grouped primary crop-band and tactical tunable changes."""
    if not waypoints:
        return "*No waypoints recorded.*\n"

    # Group by timestamp
    from collections import defaultdict

    by_time = defaultdict(dict)
    notes_by_time = {}
    for wp in waypoints:
        t = wp["time"]
        param = wp["parameter"]
        by_time[t][param] = wp["value"]
        if wp.get("reason") and param in CORE_PARAMS:
            notes_by_time.setdefault(t, wp["reason"][:60])

    # Group by day
    times = sorted(by_time.keys())

    lines = []
    days = []
    for t in times:
        day = t[:10]
        if day not in days:
            days.append(day)

    for day in days:
        day_times = [t for t in times if t.startswith(day)]
        try:
            d = datetime.strptime(day, "%Y-%m-%d")
            day_label = d.strftime("%A %B %d")
        except ValueError:
            day_label = day
        lines.append(f"\n#### {day_label}\n")

        primary_rows = _waypoint_rows(
            day_times,
            by_time,
            notes_by_time,
            PRIMARY_BAND_PARAMS,
            "Primary crop-band transition.",
        )
        tactical_rows = _waypoint_rows(
            day_times,
            by_time,
            notes_by_time,
            TACTICAL_TUNABLE_PARAMS,
            "Tactical tunable transition.",
        )
        if primary_rows:
            lines.extend(
                [
                    "**Primary crop-band changes:**",
                    "",
                    lab_table(["Time", "Values", "Note"], primary_rows, nowrap_cols={0}),
                    "",
                ]
            )
        if tactical_rows:
            lines.extend(
                [
                    "**Tactical tunable changes:**",
                    "",
                    lab_table(["Time", "Values", "Note"], tactical_rows, nowrap_cols={0}),
                    "",
                ]
            )

    # Secondary parameters are noisy when every waypoint repeats the full
    # controller state. Public pages keep deltas and omit unchanged raw rows.
    changed_non_core_rows = []
    last_seen: dict[str, str] = {}
    for t in times:
        vals = by_time[t]
        time_str = t[11:16] if len(t) > 11 else t
        for param in sorted(vals):
            if param in CORE_PARAMS:
                continue
            value = vals[param]
            previous = last_seen.get(param)
            if previous != value:
                change = f"{previous} → {value}" if previous is not None else f"initial {value}"
                changed_non_core_rows.append((time_str, param, change))
            last_seen[param] = value

    if changed_non_core_rows:
        lines.extend(
            [
                "",
                "**Changed secondary parameters:**",
                "",
                lab_table(["Time", "Parameter", "Change"], changed_non_core_rows, nowrap_cols={0, 1}),
            ]
        )

    return "\n".join(lines) + "\n"


def _num(val, digits: int = 1):
    """Parse a maybe-string-maybe-number value; return None if empty/unparseable."""
    if val is None or val == "":
        return None
    try:
        f = float(val)
        return round(f, digits) if digits > 0 else int(round(f))
    except (TypeError, ValueError):
        return None


def get_latest_plan_page_date() -> date | None:
    rows = db_query_rows("""
        WITH plan_dates AS (
            SELECT substring(plan_id FROM 'iris-(\\d{8})')::date AS d
            FROM plan_journal
            WHERE plan_id NOT LIKE 'iris-reactive%' AND plan_id NOT LIKE 'iris-fix%'
        ),
        summary_dates AS (
            SELECT date AS d
            FROM daily_summary
            WHERE date >= '2026-03-24' AND cost_total IS NOT NULL
        )
        SELECT max(d)::text
        FROM (
            SELECT d FROM plan_dates WHERE d IS NOT NULL
            UNION
            SELECT d FROM summary_dates WHERE d IS NOT NULL
        ) latest
    """)
    if not rows or not rows[0] or not rows[0][0].strip():
        return None
    return date.fromisoformat(rows[0][0].strip())


def generate_frontmatter(d: date, plans: list[dict], summary: dict, setpoints: dict, *, is_latest: bool = False) -> str:
    """Sprint 22: builds frontmatter via DailyPlanVaultFrontmatter schema.
    yaml.safe_dump emits the block; a schema validation error means the
    renderer gave us a malformed value (not our concern to hide with a
    broken YAML line to Obsidian)."""
    title = d.strftime("%B %d, %Y")

    latest_plan = plans[-1] if plans else {}
    latest_cycle = classify_cycle(latest_plan.get("plan_id", "")) if latest_plan else "none"

    core_params = [
        "temp_high",
        "temp_low",
        "vpd_high",
        "vpd_hysteresis",
        "cool_stage2_over_high_f",
        "cool_exit_hysteresis_f",
        "sw_cool_all_fans_at_high_enabled",
        "sw_direct_wet_stress_override_enabled",
        "direct_wet_stress_latest_hour",
        "sw_fog_stress_window_extend_enabled",
        "fog_stress_window_latest_hour",
        "mister_engage_kpa",
        "mister_all_kpa",
        "mister_pulse_on_s",
        "mister_pulse_gap_s",
        "mister_vpd_weight",
    ]

    climate = {
        "temp_min_f": _num(summary.get("temp_min")),
        "temp_max_f": _num(summary.get("temp_max")),
        "temp_avg_f": _num(summary.get("temp_avg")),
        "vpd_min_kpa": _num(summary.get("vpd_min"), 2),
        "vpd_max_kpa": _num(summary.get("vpd_max"), 2),
        "vpd_avg_kpa": _num(summary.get("vpd_avg"), 2),
        "rh_min_pct": _num(summary.get("rh_min")),
        "rh_max_pct": _num(summary.get("rh_max")),
        "dli_sensor_mol": _num(summary.get("dli_final")),
    }
    stress = {
        "heat_hours": _num(summary.get("stress_hours_heat")),
        "vpd_high_hours": _num(summary.get("stress_hours_vpd_high")),
        "cold_hours": _num(summary.get("stress_hours_cold")),
        "vpd_low_hours": _num(summary.get("stress_hours_vpd_low")),
    }
    cost = {
        "electric": _num(summary.get("cost_electric"), 2),
        "gas": _num(summary.get("cost_gas"), 2),
        "water": _num(summary.get("cost_water"), 3),
        "total": _num(summary.get("cost_total"), 2),
    }
    water = {
        "total_gal": _num(summary.get("water_used_gal"), 0),
        "mister_gal": _num(summary.get("mister_water_gal"), 0),
    }
    equipment = {
        "fan1_min": _num(summary.get("runtime_fan1_min"), 0),
        "fan2_min": _num(summary.get("runtime_fan2_min"), 0),
        "fog_min": _num(summary.get("runtime_fog_min"), 0),
        "heat1_min": _num(summary.get("runtime_heat1_min"), 0),
        "heat2_min": _num(summary.get("runtime_heat2_min"), 0),
        "vent_min": _num(summary.get("runtime_vent_min"), 0),
        "grow_light_min": _num(summary.get("runtime_grow_light_min"), 0),
        "mister_south_h": _num(summary.get("runtime_mister_south_h"), 2),
        "mister_west_h": _num(summary.get("runtime_mister_west_h"), 2),
        "mister_center_h": _num(summary.get("runtime_mister_center_h"), 2),
    }
    setpoints_block: dict = {}
    for p in core_params:
        v = setpoints.get(p, "")
        setpoints_block[p] = _num(v, 3) if v not in ("", None) else None

    experiment = None
    if latest_plan:
        experiment = {
            "hypothesis": public_summary(latest_plan.get("hypothesis", ""), max_chars=700),
            "test": public_summary(latest_plan.get("experiment", ""), max_chars=400),
            "expected_outcome": public_summary(latest_plan.get("expected_outcome", ""), max_chars=400),
            "outcome_score": latest_plan.get("outcome_score", ""),
            "status": latest_plan.get("status", "pending"),
        }

    fm = DailyPlanVaultFrontmatter(
        title=title,
        date=d,
        tags=["daily-plan"],
        type="plan",
        latest_cycle=latest_cycle,
        latest_plan_id=latest_plan.get("plan_id", "none"),
        plan_count=len(plans),
        climate=climate,
        stress=stress,
        cost=cost,
        water=water,
        equipment=equipment,
        setpoints=setpoints_block,
        experiment=experiment,
    )
    yaml_block = yaml.safe_dump(
        fm.model_dump(mode="json", exclude_none=False),
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )
    description = (
        f"Generated AI greenhouse planning log for {title}: {len(plans)} planning cycles, "
        f"{stress['vpd_high_hours']}h high-VPD stress, {stress['heat_hours']}h heat stress, "
        f"and USD {cost['total']} total resource cost."
    )
    yaml_block += f'description: "{_yaml_escape(description)}"\n'
    yaml_block += "noindex: true\n"
    if is_latest:
        yaml_block += "aliases:\n"
        yaml_block += "  - plans/latest\n"
    lines = ["---", yaml_block.rstrip(), "---"]
    return "\n".join(lines)


def get_previous_plan(plan_created: str) -> dict | None:
    """Get the plan that was active BEFORE a given plan's creation time.

    This is used by the Reflection section to show what hypothesis was being tested
    before the current plan validated it.
    """
    row = db_query_json(f"""
        SELECT row_to_json(p)
        FROM (
            SELECT plan_id,
                   to_char(created_at AT TIME ZONE 'America/Denver', 'YYYY-MM-DD HH24:MI') as created,
                   COALESCE(conditions_summary, '') AS conditions_summary,
                   COALESCE(hypothesis, '') AS hypothesis,
                   COALESCE(experiment, '') AS experiment,
                   COALESCE(expected_outcome, '') AS expected_outcome,
                   COALESCE(actual_outcome, '') AS actual_outcome,
                   COALESCE(outcome_score::text, '') AS outcome_score,
                   COALESCE(lesson_extracted, '') AS lesson_extracted
            FROM plan_journal
            WHERE created_at < '{plan_created}'::timestamptz
              AND plan_id NOT LIKE 'iris-reactive%'
              AND plan_id NOT LIKE 'iris-fix%'
            ORDER BY created_at DESC LIMIT 1
        ) p
    """)
    if not isinstance(row, dict):
        return None
    return {k: str(v or "").strip() for k, v in row.items()}


def get_cycle_label(plan: dict) -> tuple[str, str]:
    """Return (emoji_label, formatted_time) for a plan's cycle.

    Uses plan_id time to determine cycle type. If the plan came from a
    deviation trigger, labels it as a Deviation Replan.
    """
    plan_id = plan.get("plan_id", "")
    created = plan.get("created", "")

    # Format time for display: "06:06 AM"
    time_display = ""
    if created and len(created) >= 16:
        try:
            dt = datetime.strptime(created, "%Y-%m-%d %H:%M")
            time_display = dt.strftime("%I:%M %p").lstrip("0")
        except ValueError:
            time_display = created[11:16]

    # Check for deviation trigger
    if "deviation" in plan_id.lower() or "reactive" in plan_id.lower():
        return f"⚠️ Deviation Replan ({time_display})", time_display

    cycle_type = classify_cycle(plan_id)
    labels = {
        "morning": "🌅 Morning Cycle",
        "midday": "☀️ Midday Cycle",
        "evening": "🌆 Evening Cycle",
        "overnight": "🌙 Overnight Cycle",
        "unknown": "📋 Planning Cycle",
    }
    label = labels.get(cycle_type, "📋 Planning Cycle")
    return f"{label} ({time_display})", time_display


def generate_cycle_section(
    plan: dict,
    prev_plan: dict | None,
    waypoints: list[dict],
    seen_validation_results: set[tuple[str, str]],
    seen_previous_hypotheses: set[str],
) -> str:
    """Generate a single cycle section with Reflection + Hypothesis + Setpoints.

    Each planning cycle is a first-class entry in chronological order.
    The Reflection validates the PREVIOUS cycle's hypothesis.
    The Hypothesis is THIS cycle's forward look.
    """
    label, _ = get_cycle_label(plan)
    plan_id = plan.get("plan_id", "")
    lines = [f"## {label} — `{plan_id}`", ""]

    changed_params = [p.strip() for p in plan.get("params_changed", "").split(",") if p.strip()]
    score = plan.get("outcome_score", "")
    structured_raw = plan.get("hypothesis_structured", "")
    structured = None
    if structured_raw:
        try:
            structured = json.loads(structured_raw)
        except (json.JSONDecodeError, TypeError):
            structured = None
    structured_context = isinstance(structured, dict) and bool(
        structured.get("conditions") or structured.get("stress_windows")
    )
    lines.append(
        metric_grid(
            [
                ("Status", status_label(plan.get("status", "pending"))),
                ("Outcome score", f"{score}/10" if score else "not validated yet"),
                (
                    "Changed parameters",
                    ", ".join(changed_params[:8]) + (" ..." if len(changed_params) > 8 else "")
                    if changed_params
                    else "none recorded",
                ),
            ]
        )
    )
    lines.append("")

    # --- Reflection: validates the previous cycle ---
    lines.append("### Reflection")
    lines.append("")

    if plan.get("status") == "pending":
        # This is the latest (current) cycle — hasn't been validated yet
        lines.append("*Daily Summary So Far — this cycle will be validated at the next planning cycle.*")
        lines.append("")
    elif prev_plan:
        # Show what previous cycle hypothesized and how it turned out
        prev_id = prev_plan.get("plan_id", "unknown")
        prev_label, _ = get_cycle_label(prev_plan)
        lines.append(f"_Validating previous cycle: `{prev_id}`_")
        lines.append("")

        prev_hypothesis = prev_plan.get("hypothesis", "")
        prev_hypothesis_normalized = normalized_public_block(prev_hypothesis or "(not recorded)")
        if prev_hypothesis_normalized not in seen_previous_hypotheses:
            if prev_hypothesis:
                lines.append(f"**Previous hypothesis:** {public_summary(prev_hypothesis)}")
            else:
                lines.append("**Previous hypothesis:** *(not recorded)*")
            seen_previous_hypotheses.add(prev_hypothesis_normalized)

        # The actual_outcome and score come from THIS plan's validation of the previous one
        actual = plan.get("actual_outcome", "")
        score = plan.get("outcome_score", "")
        duplicate_validation = False
        if actual:
            validation_key = (prev_id, normalized_public_block(actual))
            duplicate_validation = validation_key in seen_validation_results
            if duplicate_validation:
                lines.append(
                    "_Duplicate validation row: this same previous cycle and result already appeared earlier on this page. The row stays visible for audit continuity._"
                )
            else:
                lines.append(f"**Result:** {public_text(actual)}")
                seen_validation_results.add(validation_key)
        if score and not duplicate_validation:
            lines.append(f"**Score:** {score}/10")
        lines.append("")

        lesson = plan.get("lesson_extracted", "")
        if lesson and not duplicate_validation:
            lines.append(f"> **New finding:** {public_text(lesson)} → Added to [Lessons Learned](/reference/lessons)")
            lines.append("")
    else:
        # First ever cycle or no previous plan found
        lines.append("_First planning cycle — no previous hypothesis to validate._")
        lines.append("")

    # --- Hypothesis: THIS cycle's forward look ---
    lines.append("### Hypothesis")
    lines.append("")

    conditions = plan.get("conditions_summary", "")
    if conditions and not structured_context:
        lines.append(f"**Conditions:** {public_text(conditions)}")

    experiment = plan.get("experiment", "")
    if experiment:
        lines.append(f"**Testing:** {public_text(experiment)}")

    expected = plan.get("expected_outcome", "")
    if expected:
        lines.append(f"**Expected outcome:** {public_text(expected)}")
    lines.append("")

    # Sprint 20 Phase 5: structured hypothesis block (typed conditions /
    # stress windows / per-parameter rationale) — rendered when the planner
    # emitted the JSON block via the set_plan MCP tool. Legacy rows where
    # hypothesis_structured IS NULL just render the prose above and skip this.
    # Only render if it's a dict with the expected shape; tolerate legacy rows
    # that may have non-object JSON (scalars, arrays).
    if isinstance(structured, dict):
        lines.extend(_render_structured_hypothesis(structured))

    # --- Setpoints: waypoints for THIS plan_id only ---
    if waypoints:
        lines.append("#### Setpoints")
        lines.append("")
        lines.append(format_waypoints_table(waypoints))

    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def generate_no_plan_section(d: date, delivery_events: list[dict]) -> str:
    """Explain a day with no full plan_journal cycles without hiding activity."""
    lines = [
        f"**Planner archive status:** no full `plan_journal` planning cycles were recorded on {d.isoformat()}.",
        "",
    ]

    if not delivery_events:
        lines.extend(
            [
                f"No planner delivery-log rows were recorded for {d.isoformat()}.",
                "",
            ]
        )
        return "\n".join(lines)

    required_events = [
        event for event in delivery_events if event.get("event_type") in {"SUNRISE", "MIDDAY", "SUNSET", "MIDNIGHT"}
    ]
    one_shots = [
        event
        for event in delivery_events
        if event.get("status") == "plan_written" and event.get("resulting_plan_id", "").startswith("iris-oneshot-")
    ]
    pending_required = [event for event in required_events if event.get("status") == "pending"]

    summary_cards = [
        ("Delivery events", str(len(delivery_events))),
        ("Required cycles pending", str(len(pending_required))),
        ("One-shot corrections", str(len(one_shots))),
    ]
    lines.append(metric_grid(summary_cards))
    lines.append("")

    if pending_required:
        names = ", ".join(
            f"{event.get('event_type')} at {event.get('delivered', '')[11:]}" for event in pending_required
        )
        lines.append(
            f"Required planning events accepted by the gateway but still pending: {public_text(names)}. That is an availability failure, not a hidden success."
        )
        lines.append("")

    if one_shots:
        ids = ", ".join(event["resulting_plan_id"] for event in one_shots[:5])
        lines.append(
            f"The planner did write one-shot setpoint corrections on {d} ({public_text(ids)}), "
            "but those are not counted as full daily planning cycles."
        )
        lines.append("")

    rows = []
    for event in delivery_events:
        plan_id = event.get("resulting_plan_id") or "-"
        note = delivery_public_note(event, max_chars=320)
        body = f"Delivered {event.get('delivered', '?')}; resulting plan {plan_id}."
        if note:
            body = f"{body} {note}"
        rows.append(
            (
                event_display_label(event),
                event.get("event_type", "?"),
                event.get("status", "?"),
                event.get("delivered", "?"),
                body,
            )
        )
    lines.append(
        lab_table(
            ["Event", "Type", "Status", "Delivered", "Note"],
            rows,
            nowrap_cols={1, 2, 3},
        )
    )
    lines.append("")
    return "\n".join(lines)


def generate_delivery_events_section(d: date, delivery_events: list[dict], public_plan_ids: set[str]) -> str:
    """Render all planner delivery events, including no-change acknowledgements."""
    if not delivery_events:
        return ""

    acknowledged = [event for event in delivery_events if event.get("status") == "acked"]
    plan_writes = [event for event in delivery_events if event.get("status") == "plan_written"]
    pending = [event for event in delivery_events if event.get("status") == "pending"]

    lines = [
        "## Planner Execution Ledger",
        "",
        (
            "Planner checkpoints can acknowledge that the active plan is still suitable without "
            "writing a new public plan ID. Those no-change decisions are part of the audit trail."
        ),
        "",
        metric_grid(
            [
                ("Delivery events", str(len(delivery_events))),
                ("Plan writes", str(len(plan_writes))),
                ("No-change acknowledgements", str(len(acknowledged))),
                ("Pending", str(len(pending))),
            ]
        ),
        "",
    ]

    rows = []
    for event in delivery_events:
        event_type = event.get("event_type", "?")
        status = event.get("status", "?")
        plan_id = event.get("resulting_plan_id", "")
        delivered = compact_time(event.get("delivered"))
        resolved = compact_time(event.get("acked") or event.get("plan_written"))

        if status == "plan_written" and plan_id:
            body = f"Wrote public plan {plan_id}."
            if plan_id not in public_plan_ids:
                body = f"Wrote non-archive plan {plan_id}."
        elif status == "acked":
            body = delivery_public_note(event) or "Acknowledged with no setpoint or plan change."
        elif status == "pending":
            body = "Delivery accepted by the planning gateway; no resolution recorded yet."
        else:
            body = "Delivery recorded without a public plan write."

        if resolved != "unknown":
            body = f"{body} Resolved {resolved} MDT."
        rows.append((event_display_label(event), delivered, event_type, status, body))

    lines.append(
        lab_table(
            ["Event", "Delivered", "Type", "Status", "Note"],
            rows,
            nowrap_cols={1, 2, 3},
        )
    )
    lines.append("")
    return "\n".join(lines)


def generate_daily_summary_section(
    summary: dict,
    hourly: list[dict],
    summary_date: date = None,
    pending: bool = False,
) -> str:
    """Generate end-of-day summary section."""
    if not summary:
        return ""

    heading = "Daily Summary So Far" if pending else "End-of-Day Summary"
    lines = [f"## {heading}", ""]

    # Climate cards
    lines.extend(
        [
            "### Climate",
            "",
            metric_grid(
                [
                    (
                        "Temperature",
                        f"{r(summary.get('temp_min'))}–{r(summary.get('temp_max'))}°F; avg {r(summary.get('temp_avg'))}°F",
                    ),
                    (
                        "VPD",
                        f"{r(summary.get('vpd_min'), 2)}–{r(summary.get('vpd_max'), 2)} kPa; avg {r(summary.get('vpd_avg'), 2)} kPa",
                    ),
                    ("Relative humidity", f"{r(summary.get('rh_min'))}–{r(summary.get('rh_max'))}%"),
                ]
            ),
            "",
        ]
    )

    # Stress
    lines.extend(
        [
            "### Stress Hours",
            "",
            f"- **Heat stress (>85°F):** {r(summary.get('stress_hours_heat'))}h",
            f"- **VPD stress (>2.0 kPa):** {r(summary.get('stress_hours_vpd_high'))}h",
            f"- **Cold stress (<55°F):** {r(summary.get('stress_hours_cold'))}h",
            "",
        ]
    )

    # Economics
    lines.extend(
        [
            "### Economics",
            "",
            metric_grid(
                [
                    ("Electric", f"USD {r(summary.get('cost_electric'), 2)}"),
                    ("Gas", f"USD {r(summary.get('cost_gas'), 2)}"),
                    ("Water", f"USD {r(summary.get('cost_water'), 3)}"),
                    ("Total", f"USD {r(summary.get('cost_total'), 2)}"),
                ]
            ),
            "",
        ]
    )

    # Equipment runtimes
    lines.extend(
        [
            "### Equipment Runtimes",
            "",
            lab_table(
                ["Equipment", "Runtime", "Reading"],
                [
                    ("Fan 1", f"{r(summary.get('runtime_fan1_min'), 0)} min", "Primary exhaust runtime."),
                    ("Fan 2", f"{r(summary.get('runtime_fan2_min'), 0)} min", "Secondary exhaust runtime."),
                    ("Vent", f"{r(summary.get('runtime_vent_min'), 0)} min", "Intake vent runtime."),
                    ("Fog", f"{r(summary.get('runtime_fog_min'), 0)} min", "Fogger runtime."),
                    ("Heat 1 electric", f"{r(summary.get('runtime_heat1_min'), 0)} min", "Electric heater runtime."),
                    ("Heat 2 gas", f"{r(summary.get('runtime_heat2_min'), 0)} min", "Gas heater runtime."),
                    (
                        "Grow lights",
                        f"{r(summary.get('runtime_grow_light_min'), 0)} min",
                        "Supplemental lighting runtime.",
                    ),
                    ("Mister south", f"{r(summary.get('runtime_mister_south_h'), 2)}h", "South mister runtime."),
                    ("Mister west", f"{r(summary.get('runtime_mister_west_h'), 2)}h", "West mister runtime."),
                    ("Mister center", f"{r(summary.get('runtime_mister_center_h'), 2)}h", "Center mister runtime."),
                ],
                nowrap_cols={1},
            ),
            "",
        ]
    )

    # Water
    water_total = summary.get("water_used_gal")
    mister_water = summary.get("mister_water_gal")
    if water_total is not None:
        lines.extend(
            [
                "### Water",
                "",
                f"- **Total:** {r(water_total, 0)} gal",
                f"- **Mister:** {r(mister_water, 0)} gal",
                "",
            ]
        )

    # Crop health (from Gemini Vision observations)
    crop_health = db_query_rows(f"""
        SELECT c.name, c.zone, ROUND(AVG(o.health_score)::numeric, 2) AS avg_health,
            COUNT(*) AS obs_count,
            string_agg(DISTINCT o.notes, ' || ' ORDER BY o.notes) AS notes
        FROM observations o JOIN crops c ON o.crop_id = c.id
        WHERE o.source = 'gemini-vision' AND o.ts::date = '{summary_date or date.today()}'
        GROUP BY c.name, c.zone ORDER BY c.name
    """)
    if crop_health:
        lines.extend(
            [
                "### Crop Health (Gemini Vision)",
                "",
            ]
        )
        rows = []
        detail_rows = []
        for row in crop_health:
            if len(row) >= 5:
                health_pct = f"{float(row[2].strip()) * 100:.0f}%" if row[2].strip() else "—"
                crop = row[0].strip()
                notes = row[4].strip()
                rows.append(
                    (
                        crop,
                        row[1].strip(),
                        health_pct,
                        row[3].strip(),
                        "Observation notes are collapsed below to avoid publishing partial vision snippets.",
                    )
                )
                if notes:
                    detail_rows.append((crop, "Gemini Vision notes", public_text(notes[:1000])))
        lines.append(
            lab_table(
                ["Crop", "Zone", "Health", "Observations", "Note"],
                rows,
                numeric_cols={3},
                nowrap_cols={1, 2, 3},
            )
        )
        if detail_rows:
            lines.extend(
                [
                    "",
                    "<details>",
                    "<summary>Vision observation notes</summary>",
                    "",
                    data_table(detail_rows),
                    "",
                    "</details>",
                ]
            )
        lines.append("")

    # Hourly pattern (compact)
    if hourly:
        lines.extend(["### Hourly Pattern", ""])
        rows = []
        for h in hourly:
            rows.append((h["hour"], f"{h['temp']}°F", f"{h['vpd']} kPa", f"{h['rh']}%"))
        lines.append(
            lab_table(
                ["Hour", "Temperature", "VPD", "RH"],
                rows,
                nowrap_cols={0, 1, 2, 3},
            )
        )
        lines.append("")

    return "\n".join(lines)


def generate_day(d: date) -> str:
    """Generate the complete daily plan document for a given date.

    Structure: Title → [Cycle sections chronologically] → End-of-Day Summary → 7-Day Stress

    Each cycle gets its own ## section with Reflection + Hypothesis + Setpoints.
    The day reads like a lab notebook — chronological, no collapsing.
    """
    title = d.strftime("%B %d, %Y")

    summary = get_daily_summary(d)
    plans = get_plans_for_date(d)
    delivery_events = get_plan_delivery_for_date(d)
    setpoints = get_active_setpoints_at(d)
    hourly = get_hourly_pattern(d)
    stress_ctx = get_stress_context(d)

    # Frontmatter
    frontmatter = generate_frontmatter(d, plans, summary, setpoints, is_latest=(d == get_latest_plan_page_date()))

    # Title
    body = [
        "[//]: # (auto-generated by scripts/generate-daily-plan.py; source: daily_summary + plan_journal + plan_delivery_log)",
        "",
        f"# {title}",
        "",
        "*Generated lab notebook from `daily_summary`, `plan_journal`, `plan_delivery_log`, and setpoint audit data. It is intentionally chronological and may include in-progress cycles before validation.*",
        "",
    ]

    if not plans:
        body.append(generate_no_plan_section(d, delivery_events))
    else:
        delivery_section = generate_delivery_events_section(
            d,
            delivery_events,
            {plan.get("plan_id", "") for plan in plans},
        )
        if delivery_section:
            body.append(delivery_section)

        # Each plan gets its own cycle section, chronologically
        seen_validation_results: set[tuple[str, str]] = set()
        seen_previous_hypotheses: set[str] = set()
        for plan in plans:
            prev_plan = get_previous_plan(plan["created"])
            waypoints = get_waypoints_for_plan(plan["plan_id"])
            body.append(
                generate_cycle_section(
                    plan,
                    prev_plan,
                    waypoints,
                    seen_validation_results,
                    seen_previous_hypotheses,
                )
            )

    # End-of-day summary (unchanged)
    if summary:
        body.append(
            generate_daily_summary_section(
                summary,
                hourly,
                d,
                pending=any(plan.get("status") == "pending" for plan in plans),
            )
        )

    # 7-day stress context (unchanged)
    if stress_ctx:
        body.extend(
            [
                "## 7-Day Stress Context",
                "",
            ]
        )
        rows = []
        for s in stress_ctx:
            rows.append((s["date"], f"{s['heat']}h", f"{s['vpd_high']}h", f"{s['cold']}h"))
        body.append(
            lab_table(
                ["Date", "Heat", "VPD high", "Cold"],
                rows,
                nowrap_cols={0, 1, 2, 3},
            )
        )
        body.append("")

    return frontmatter + "\n\n" + "\n".join(body)


def backfill():
    """Backfill all days that have plan_journal entries or daily_summary data."""
    # Find all dates with plans
    plan_dates = db_query_rows("""
        SELECT DISTINCT substring(plan_id from 'iris-(\\d{8})')::date as plan_date
        FROM plan_journal
        WHERE plan_id NOT LIKE 'iris-reactive%' AND plan_id NOT LIKE 'iris-fix%'
        ORDER BY plan_date
    """)

    # Find all dates with daily_summary
    summary_dates = db_query_rows("""
        SELECT date FROM daily_summary
        WHERE date >= '2026-03-24' AND cost_total IS NOT NULL
        ORDER BY date
    """)

    all_dates = set()
    for row in plan_dates:
        if row and row[0].strip():
            try:
                all_dates.add(date.fromisoformat(row[0].strip()))
            except ValueError:
                pass
    for row in summary_dates:
        if row and row[0].strip():
            try:
                all_dates.add(date.fromisoformat(row[0].strip()))
            except ValueError:
                pass

    print(f"Backfilling {len(all_dates)} days: {sorted(all_dates)}")

    for d in sorted(all_dates):
        print(f"  Generating {d}...", end=" ")
        try:
            content = generate_day(d)
            output = CONTENT_DIR / f"{d}.md"
            output.write_text(content)
            plans = get_plans_for_date(d)
            summary = get_daily_summary(d)
            record_plan_archive_audit(d, output, plans, summary, content)
            print(f"OK ({len(plans)} plans, {len(content)} chars)")
        except Exception as e:
            print(f"ERROR: {e}")


def main():
    parser = argparse.ArgumentParser(description="Generate daily plan documents")
    parser.add_argument("--backfill", action="store_true", help="Backfill all historical days")
    parser.add_argument("--date", type=str, help="Date to generate (YYYY-MM-DD)")
    parser.add_argument("--today", action="store_true", help="Generate today's plan")
    parser.add_argument("--cycle", type=str, choices=["morning", "midday", "evening"], help="Current planning cycle")
    parser.add_argument("--plan-id", type=str, help="Current plan ID")
    args = parser.parse_args()

    CONTENT_DIR.mkdir(parents=True, exist_ok=True)

    if args.backfill:
        backfill()
    elif args.today or args.date:
        d = date.today() if args.today else date.fromisoformat(args.date)
        content = generate_day(d)
        output = CONTENT_DIR / f"{d}.md"
        output.write_text(content)
        plans = get_plans_for_date(d)
        summary = get_daily_summary(d)
        record_plan_archive_audit(d, output, plans, summary, content)
        print(f"Generated {output} ({len(content)} chars)")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
