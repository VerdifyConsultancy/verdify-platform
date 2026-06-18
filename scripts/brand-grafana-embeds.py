#!/usr/bin/env python3
"""Apply Verdify Lab styling rules to Grafana panels embedded on the site.

The public site embeds single Grafana panels with /d-solo URLs. This script
uses the site markdown as the source of truth for "public embedded panels" and
updates matching dashboard JSON files in grafana/dashboards and
grafana/provisioning/dashboards/json.

The rules intentionally separate panel chrome from data semantics:
- panel backgrounds/stat cards are made transparent/site-dark so they sit inside
  the Quartz Grafana frame cleanly;
- legends/tooltips/axes are normalized;
- fixed colors are mapped through semantic series names so solar, heat, water,
  forecast, target, fault, etc. stay meaningful instead of being forced into
  the site chrome palette.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import urllib.request
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VAULT_ROOT = Path("/mnt/iris/verdify-vault/website")
DEFAULT_GRAFANA_CONTAINER = "verdify-grafana"
DASHBOARD_DIRS = (
    REPO_ROOT / "grafana" / "dashboards",
    REPO_ROOT / "grafana" / "provisioning" / "dashboards" / "json",
)

EMBED_RE = re.compile(r"https://graphs\.verdify\.ai/d-solo/([^/?#\s\"<>)]*)/?[^\s\"<>)]*")

BRAND = {
    "canopy": "#2E7D32",
    "leaf": "#73BF69",
    "mint": "#26A69A",
    "navy": "#112231",
    "slate": "#78909C",
    "gray": "#8C8C8C",
    "glass": "#B0BEC5",
    "gold": "#FDD835",
    "gold_soft": "#FFE66D",
    "fault": "#EF5350",
    "heat": "#FF9800",
    "gas": "#F4511E",
    "water": "#2196F3",
    "sky": "#42A5F5",
    "fog": "#4FC3F7",
    "fan": "#4DB6AC",
    "teal": "#26A69A",
    "violet": "#AB47BC",
    "rose": "#F48FB1",
}

NON_SERIES_COLOR_VALUES = {"transparent"}
OUTDOOR_LUX_FILL_OPACITY = 55
INDOOR_LUX_FILL_OPACITY = 85
DAYLIGHT_FILL_OPACITY = 85
DAYLIGHT_FORECAST_FILL_OPACITY = 85
DAYLIGHT_LUX_FILL_OPACITY = 85
DAYLIGHT_GRADIENT_MODE = "opacity"
HOMEPAGE_SOLAR_FILL_OPACITY = 100
HOMEPAGE_SOLAR_GRADIENT_MODE = "opacity"
HOMEPAGE_SOLAR_PANEL_IDS = {30, 31, 36}
COMPLIANCE_BAND_COLOR = BRAND["leaf"]
COMPLIANCE_BAND_FILL_OPACITY = 22
LIGHTING_THRESHOLD_BAND_FILL_OPACITY = 22
HOMEPAGE_LIGHTING_THRESHOLD_LINE_WIDTH = 1
HOMEPAGE_LIGHTING_GROW_THRESHOLD_LINE_STYLE = {"fill": "dash", "dash": [6, 4]}
RELAY_STATE_FILL_OPACITY = 38
HOMEPAGE_RELAY_STATE_FILL_OPACITY = 78
HOMEPAGE_LIGHTING_STATE_FILL_OPACITY = 90
HOMEPAGE_LIGHTING_STATE_LINE_WIDTH = 0
HOMEPAGE_LIGHTING_OVERHEAD_STATE_COLOR = "#007A4D"
HOMEPAGE_LIGHTING_GROW_STATE_COLOR = "#0B5CAD"
HOMEPAGE_LIGHTING_PANEL_TITLES = {
    "Lighting: Overhead vs Grow Circuit — Lux, Thresholds & Switch State",
    "Lighting: Overhead vs Grow Circuit — Lux & Switch State",
}
RELAY_STATE_LINE_WIDTH = 0
HOMEPAGE_TARGET_LINE_COLOR = "#2E7D32"
HOMEPAGE_TARGET_LINE_STYLE = {"fill": "dash", "dash": [6, 4]}
LIGHTING_THRESHOLD_BANDS = (
    ("Overhead Threshold Base", "Overhead Threshold", BRAND["leaf"]),
    ("Grow Threshold Base", "Grow Threshold", BRAND["water"]),
    ("Grow Light Threshold Base", "Grow Light Threshold", BRAND["leaf"]),
    ("Main/Grow ON Threshold", "Main/Grow OFF Threshold", BRAND["leaf"]),
    ("Main ON Threshold", "Main OFF Threshold", BRAND["leaf"]),
    ("Grow ON Threshold", "Grow OFF Threshold", "#5794F2"),
)
TEMPERATURE_RELAY_STATE_LANES = (
    ("heat2", "Heat 2 (Gas)", "Heat 2 (Gas) Base", 56.0, 57.8),
    ("heat1", "Heat 1 (Electric)", "Heat 1 (Electric) Base", 58.2, 60.0),
    ("fan1", "Fan 1", "Fan 1 Base", 87.0, 88.8),
    ("fan2", "Fan 2", "Fan 2 Base", 89.2, 91.0),
    ("fog", "Fog", "Fog Base", 91.4, 93.2),
)
VPD_RELAY_STATE_LANES = (
    ("heat2", "Heat 2 (Gas)", "Heat 2 (Gas) Base", -0.46, -0.24),
    ("heat1", "Heat 1 (Electric)", "Heat 1 (Electric) Base", -0.18, 0.04),
    ("mister_south", "Mister South", "Mister South Base", 1.72, 2.00),
    ("mister_west", "Mister West", "Mister West Base", 2.06, 2.34),
    ("mister_center", "Mister Center", "Mister Center Base", 2.40, 2.68),
    ("fog", "Fog", "Fog Base", 2.74, 3.02),
)
EXPECTED_STAT_COLORS = {
    "Runtime Electric $/Day (30-Day Avg)": "#66BB6A",
    "Runtime Daytime Watts (30-Day Avg)": "#66BB6A",
    "Runtime Night Watts (30-Day Avg)": "#66BB6A",
    "Runtime-Modeled kWh/day (30-Day Avg)": "#66BB6A",
    "VPD Stress Hours": BRAND["violet"],
    "Heat Stress Hours": BRAND["heat"],
    "Cold Stress Hours": BRAND["sky"],
    "Stress Hours Today": BRAND["fault"],
    "Indoor VPD": BRAND["violet"],
}
EXPECTED_SERIES_COLORS = {
    "Temp Compliance %": BRAND["heat"],
    "Temp compliance": BRAND["heat"],
    "VPD Compliance %": BRAND["violet"],
    "VPD compliance": BRAND["violet"],
    "Total Compliance %": BRAND["leaf"],
    "Compliance %": BRAND["leaf"],
    "Both-axis compliance": BRAND["leaf"],
    "Temp Stress Hours": BRAND["heat"],
    "VPD Stress Hours": BRAND["violet"],
    "Total Stress Hours": BRAND["fault"],
    "Total Stress-Category Hours": BRAND["fault"],
    "Temp MAE F": BRAND["heat"],
    "VPD MAE kPa": BRAND["violet"],
    "Cost ($)": BRAND["leaf"],
    "Runtime Electric": BRAND["leaf"],
    "Runtime Electric ($)": BRAND["leaf"],
    "Gas ($)": BRAND["gas"],
    "Water ($)": BRAND["water"],
    "Runtime Load (W)": BRAND["leaf"],
    "Runtime Heat 1 (W)": BRAND["heat"],
    "Runtime Fans (W)": BRAND["fan"],
    "Runtime Other (fog/lights/vent)": BRAND["water"],
    "Stress Hours": BRAND["fault"],
    "Cost / Stress Hour": BRAND["gray"],
    "Water Used (gal)": BRAND["water"],
    "Mister Water (gal)": BRAND["violet"],
    "Mister Runtime h x10": BRAND["rose"],
    "Transmission %": BRAND["gold"],
    "Grow Light Main": BRAND["leaf"],
    "Grow Light Grow": BRAND["water"],
    "Policy Window": BRAND["glass"],
    "Occupancy": BRAND["water"],
    "Sun": BRAND["gold"],
    "Main Light Hours": BRAND["leaf"],
    "Grow Light Hours": BRAND["water"],
    "South 1 (Canna)": BRAND["leaf"],
    "South 2 (Canna)": BRAND["gold"],
    "South 1": BRAND["leaf"],
    "South 2": BRAND["gold"],
    "south 1": BRAND["leaf"],
    "south 2": BRAND["gold"],
    "west": BRAND["rose"],
    "center": BRAND["violet"],
    "EC (µS/cm)": BRAND["teal"],
    "fertigation gallons": BRAND["water"],
    "events": BRAND["gold"],
    "flagged": BRAND["fault"],
    "clean h": BRAND["sky"],
    "fert h": BRAND["violet"],
    "master h": BRAND["teal"],
    "flow gpm": BRAND["water"],
    "meter total gal": BRAND["teal"],
    "Task WDT": BRAND["fault"],
    "Guru/Panic": BRAND["gas"],
    "Forecast Outdoor Temp": BRAND["gray"],
    "Actual Indoor Temp (15-sample rolling avg)": BRAND["leaf"],
    "Botrytis Risk %": BRAND["fault"],
    "Consecutive Hours": BRAND["gold"],
    "Plan Score": BRAND["leaf"],
    "Plans Logged": BRAND["gold"],
    "Writes": BRAND["leaf"],
    "Oscillations": BRAND["fault"],
}

GRAPH_PANEL_TYPES = {"timeseries", "barchart", "histogram", "piechart", "state-timeline", "bargauge", "gauge"}
NON_SERIES_ALIASES = {
    "time",
    "ts",
    "date",
    "day",
    "month",
    "created",
    "created_at",
    "total ($)",
}

PLAN_ACCURACY_SQL = """SELECT
  to_char(date, 'MM/DD') AS "Day",
  round(avg(compliance_pct)::numeric, 1) AS "Compliance %",
  round(avg(temp_mae_f)::numeric, 2) AS "Temp MAE F",
  round(avg(vpd_mae_kpa)::numeric, 3) AS "VPD MAE kPa"
FROM v_forecast_plan_outcome_mart
WHERE date >= (now() AT TIME ZONE 'America/Denver')::date - interval '13 days'
  AND compliance_pct IS NOT NULL
GROUP BY date
ORDER BY date"""

DAILY_COST_BY_SOURCE_SQL = """SELECT
  (date + time '12:00')::timestamptz AS time,
  round(cost_electric::numeric, 2) AS "Runtime Electric ($)",
  round(cost_gas::numeric, 2) AS "Gas ($)",
  round(cost_water::numeric, 2) AS "Water ($)"
FROM daily_summary
WHERE $__timeFilter((date + time '12:00')::timestamptz)
  AND cost_total > 0
ORDER BY date"""

MONTHLY_RESOURCE_COST_SQL = """WITH months AS (
  SELECT generate_series(
    (date_trunc('month', (now() AT TIME ZONE 'America/Denver')::date)::date - interval '5 months')::date,
    date_trunc('month', (now() AT TIME ZONE 'America/Denver')::date)::date,
    interval '1 month'
  )::date AS month_start
), costs AS (
  SELECT
    date_trunc('month', date)::date AS month_start,
    ROUND(SUM(COALESCE(cost_electric, 0))::numeric, 2) AS electric_cost_usd,
    ROUND(SUM(COALESCE(cost_gas, 0))::numeric, 2) AS gas_cost_usd,
    ROUND(SUM(COALESCE(cost_water, 0))::numeric, 2) AS water_cost_usd
  FROM daily_summary
  WHERE date >= (SELECT min(month_start) FROM months)
    AND date < ((SELECT max(month_start) FROM months) + interval '1 month')::date
  GROUP BY 1
)
SELECT
  to_char(m.month_start, 'Mon YYYY') AS "Month",
  COALESCE(c.electric_cost_usd, 0) AS "Runtime Electric ($)",
  COALESCE(c.gas_cost_usd, 0) AS "Gas ($)",
  COALESCE(c.water_cost_usd, 0) AS "Water ($)",
  COALESCE(c.electric_cost_usd, 0)
    + COALESCE(c.gas_cost_usd, 0)
    + COALESCE(c.water_cost_usd, 0) AS "Total ($)"
FROM months m
LEFT JOIN costs c USING (month_start)
ORDER BY m.month_start"""

RUNTIME_ELECTRIC_WATTS_SQL = """SELECT
  bucket AS time,
  round(total_watts::numeric, 0) AS "Runtime Load (W)"
FROM fn_runtime_power_30m($__timeFrom()::timestamptz, LEAST($__timeTo()::timestamptz, now()))
ORDER BY bucket"""

RUNTIME_WATTS_30D_CTE = """WITH modeled AS (
  SELECT
    bucket AS ts,
    total_watts AS watts
  FROM fn_runtime_power_30m(
    (((now() AT TIME ZONE 'America/Denver')::date - 30)::timestamp AT TIME ZONE 'America/Denver'),
    ((now() AT TIME ZONE 'America/Denver')::date::timestamp AT TIME ZONE 'America/Denver')
  )
)"""

RUNTIME_SOLAR_ALIGNED_LOAD_SQL = (
    RUNTIME_WATTS_30D_CTE
    + """
SELECT now() AS time,
       round((100.0 * SUM(watts) FILTER (WHERE extract(hour from ts AT TIME ZONE 'America/Denver') BETWEEN 8 AND 17) / NULLIF(SUM(watts), 0))::numeric, 1) AS value
FROM modeled"""
)

RUNTIME_POWERWALL_COVERAGE_SQL = (
    RUNTIME_WATTS_30D_CTE
    + """
SELECT now() AS time,
       round((13.5 / NULLIF(avg(watts)::numeric * 14 / 1000, 0) * 100)::numeric, 0) AS value
FROM modeled
WHERE extract(hour from ts AT TIME ZONE 'America/Denver') NOT BETWEEN 8 AND 17"""
)

RUNTIME_DAYTIME_WATTS_SQL = (
    RUNTIME_WATTS_30D_CTE
    + """
SELECT now() AS time,
       round(avg(watts)::numeric, 0) AS value
FROM modeled
WHERE extract(hour from ts AT TIME ZONE 'America/Denver') BETWEEN 8 AND 17"""
)

RUNTIME_NIGHT_WATTS_SQL = (
    RUNTIME_WATTS_30D_CTE
    + """
SELECT now() AS time,
       round(avg(watts)::numeric, 0) AS value
FROM modeled
WHERE extract(hour from ts AT TIME ZONE 'America/Denver') NOT BETWEEN 8 AND 17"""
)

RUNTIME_POWER_BY_GROUP_SQL = """SELECT
  bucket AS time,
  round(heat1_watts::numeric, 0) AS "Runtime Heat 1 (W)",
  round(fans_watts::numeric, 0) AS "Runtime Fans (W)",
  round(other_watts::numeric, 0) AS "Runtime Other (fog/lights/vent)"
FROM fn_runtime_power_30m($__timeFrom()::timestamptz, LEAST($__timeTo()::timestamptz, now()))
ORDER BY bucket"""

FREE_HEAP_SQL = """SELECT
  $__time(ts),
  heap_bytes AS "Heap kB"
FROM diagnostics
WHERE $__timeFilter(ts)
  AND heap_bytes IS NOT NULL
ORDER BY ts"""

HYDRO_PH_SQL = """SELECT
  $__time(ts),
  hydro_ph AS "pH"
FROM climate
WHERE $__timeFilter(ts)
  AND hydro_ph BETWEEN 0 AND 14
ORDER BY ts"""

HYDRO_ORP_SQL = """SELECT
  $__time(ts),
  hydro_orp_mv AS "ORP (mV)"
FROM climate
WHERE $__timeFilter(ts)
  AND hydro_orp_mv BETWEEN -1000 AND 1000
ORDER BY ts"""

DAILY_WATER_USAGE_SQL = """SELECT
  $__time(ts),
  water_total_gal AS "Water Used (gal)"
FROM climate
WHERE $__timeFilter(ts)
  AND water_total_gal IS NOT NULL
ORDER BY ts"""

FLOW_RATE_SQL = """SELECT
  $__time(ts),
  flow_gpm AS "Flow (gal/min)"
FROM climate
WHERE $__timeFilter(ts)
  AND flow_gpm IS NOT NULL
ORDER BY ts"""

PUBLIC_TABLE_SQL = {
    "Active Plan": """SELECT
  plan_id AS "Plan",
  to_char(created_at AT TIME ZONE 'America/Denver', 'MM/DD HH24:MI') AS "Created"
FROM setpoint_plan
WHERE is_active = true
ORDER BY created_at DESC
LIMIT 1""",
    "Health Components": """SELECT
  initcap(replace(component, '_', ' ')) AS "Component",
  round(score_pct::numeric, 1) AS "Score %",
  CASE
    WHEN length(details_clean) > 58 THEN left(details_clean, 55) || '...'
    ELSE details_clean
  END AS "Details"
FROM (
  SELECT component, score_pct, regexp_replace(details::text, '\\s+', ' ', 'g') AS details_clean
  FROM v_system_health_score
) AS health
ORDER BY component""",
    "Latest Planner Scorecard": """SELECT
  initcap(replace(metric, '_', ' ')) AS "Metric",
  value AS "Value"
FROM fn_planner_scorecard((now() AT TIME ZONE 'America/Denver')::date)
ORDER BY CASE metric
  WHEN 'planner_score' THEN 1
  WHEN 'compliance_pct' THEN 2
  WHEN 'total_stress_h' THEN 3
  WHEN 'cost_total' THEN 4
  ELSE 9
END, metric""",
    "Recent Planning Cycles and Outcomes": """SELECT
  plan_id AS "Plan",
  created_label AS "Created",
  coalesce(outcome_score::text, '-') AS "Score",
  CASE
    WHEN length(hypothesis_clean) > 58 THEN left(hypothesis_clean, 55) || '...'
    ELSE hypothesis_clean
  END AS "Hypothesis"
FROM (
  SELECT
    plan_id,
    to_char(created_at AT TIME ZONE 'America/Denver', 'MM/DD HH24:MI') AS created_label,
    outcome_score,
    regexp_replace(coalesce(hypothesis, ''), '\\s+', ' ', 'g') AS hypothesis_clean,
    created_at
  FROM plan_journal
) AS plans
ORDER BY created_at DESC
LIMIT 12""",
    "Recent Lessons Extracted": """SELECT
  plan_id AS "Plan",
  validated_label AS "Validated",
  coalesce(outcome_score::text, '-') AS "Score",
  CASE
    WHEN length(lesson_clean) > 58 THEN left(lesson_clean, 55) || '...'
    ELSE lesson_clean
  END AS "Lesson"
FROM (
  SELECT
    plan_id,
    to_char(validated_at AT TIME ZONE 'America/Denver', 'MM/DD HH24:MI') AS validated_label,
    outcome_score,
    regexp_replace(coalesce(lesson_extracted, ''), '\\s+', ' ', 'g') AS lesson_clean,
    validated_at
  FROM plan_journal
  WHERE lesson_extracted IS NOT NULL
) AS lessons
ORDER BY validated_at DESC NULLS LAST
LIMIT 10""",
    "Qualified Light Minutes Now": """SELECT
  light_key AS "Circuit",
  equipment AS "Equip",
  target_light_minutes AS "Target",
  qualified_light_minutes AS "Qual",
  remaining_light_minutes AS "Remain",
  CASE WHEN actual_on THEN 'ON' ELSE 'OFF' END AS "Switch",
  CASE WHEN policy_matches_cfg THEN 'MATCH' ELSE 'DRIFT' END AS "Cfg",
  CASE WHEN firmware_decision_fresh THEN 'FRESH' ELSE 'STALE' END AS "FW"
FROM v_lighting_traceability_now
ORDER BY light_key""",
}

PUBLIC_TABLE_WIDTHS = {
    "Active Plan": {"Plan": 170, "Created": 100},
    "Health Components": {"Component": 150, "Score %": 80, "Details": 520},
    "Latest Planner Scorecard": {"Metric": 220, "Value": 120},
    "Recent Planning Cycles and Outcomes": {"Plan": 160, "Created": 100, "Score": 70, "Hypothesis": 430},
    "Recent Lessons Extracted": {"Plan": 160, "Validated": 100, "Score": 70, "Lesson": 430},
    "Qualified Light Minutes Now": {
        "Circuit": 70,
        "Equip": 130,
        "Target": 70,
        "Qual": 65,
        "Remain": 70,
        "Switch": 65,
        "Cfg": 65,
        "FW": 60,
    },
}

RUNTIME_HOUR_STAT_TITLES = {
    "Heat 1",
    "Heat 2",
    "Fan 1",
    "Fan 2",
    "Fog",
    "Vent",
    "Grow Light",
    "Circuit Hours",
    "Plan Age",
    "Stress Hours Today",
    "VPD Stress Hours",
    "Heat Stress Hours",
    "Cold Stress Hours",
}

COLOR_LITERAL_MAP = {
    "red": BRAND["fault"],
    "dark-red": BRAND["fault"],
    "semi-dark-red": BRAND["fault"],
    "orange": BRAND["gold"],
    "yellow": BRAND["gold"],
    "dark-yellow": BRAND["gold"],
    "green": BRAND["leaf"],
    "dark-green": BRAND["canopy"],
    "semi-dark-green": BRAND["leaf"],
    "blue": BRAND["water"],
    "dark-blue": BRAND["sky"],
    "purple": BRAND["violet"],
    "gray": BRAND["gray"],
    "grey": BRAND["gray"],
    "dark-gray": BRAND["slate"],
    "dark-grey": BRAND["slate"],
    "#ef5350": BRAND["fault"],
    "#f44336": BRAND["fault"],
    "#ff7383": BRAND["fault"],
    "#ff5c8a": BRAND["rose"],
    "#ff9800": BRAND["heat"],
    "#ffa726": BRAND["gold"],
    "#f9a825": BRAND["gold"],
    "#fdd835": BRAND["gold"],
    "#ffd600": BRAND["gold"],
    "#ffd740": BRAND["gold_soft"],
    "#fade2a": BRAND["gold"],
    "#f2cc0c": BRAND["gold"],
    "#ffca28": BRAND["gold"],
    "#c6ff00": BRAND["gold_soft"],
    "#9ccc65": BRAND["leaf"],
    "#7cb342": BRAND["leaf"],
    "#73bf69": BRAND["leaf"],
    "#66bb6a": BRAND["leaf"],
    "#56a64b": BRAND["leaf"],
    "#2e7d32": BRAND["canopy"],
    "#42a5f5": BRAND["water"],
    "#2196f3": BRAND["water"],
    "#1e88e5": BRAND["water"],
    "#5794f2": BRAND["water"],
    "#33a2ff": BRAND["water"],
    "#80d8ff": BRAND["sky"],
    "#4fc3f7": BRAND["water"],
    "#4db6ac": BRAND["teal"],
    "#26a69a": BRAND["teal"],
    "#009688": BRAND["teal"],
    "#00acc1": BRAND["water"],
    "#5c6bc0": BRAND["violet"],
    "#8b5cf6": BRAND["violet"],
    "#ab47bc": BRAND["violet"],
    "#ce93d8": BRAND["violet"],
    "#e040fb": BRAND["violet"],
    "#f48fb1": BRAND["rose"],
    "#78909c": BRAND["gray"],
    "#8c8c8c": BRAND["gray"],
    "#b0bec5": BRAND["glass"],
    "#546e7a": BRAND["slate"],
    "#0d47a1": BRAND["sky"],
    "#1565c0": BRAND["sky"],
    "#3b82f6": BRAND["water"],
    "#f59e0b": BRAND["gold"],
    "#e65100": BRAND["gas"],
    "#f4511e": BRAND["gas"],
}


def normalize_key(value: Any) -> str:
    return str(value or "").strip().lower()


def rgba_from(hex_color: str, alpha: str) -> str:
    color = hex_color.lstrip("#")
    r, g, b = int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def semantic_color(label: str, current: Any = None, context: str = "") -> str:
    """Choose a brand color from a series/panel label, falling back to current."""

    label_text = normalize_key(label)
    context_text = normalize_key(context)
    text = f"{label_text} {context_text}".strip()
    color_text = normalize_key(current)
    alpha_match = re.match(r"rgba?\(([^)]*)\)", color_text)

    def maybe_alpha(hex_color: str) -> str:
        if not alpha_match:
            return hex_color
        parts = [part.strip() for part in alpha_match.group(1).split(",")]
        if len(parts) >= 4:
            return rgba_from(hex_color, parts[3])
        return hex_color

    if "water, air & outdoor temperature" in context_text:
        if "water" in label_text:
            return maybe_alpha(BRAND["water"])
        if "air indoor" in label_text:
            return maybe_alpha(BRAND["leaf"])
        if "outdoor" in label_text:
            return maybe_alpha(BRAND["gray"])

    if "wind speed" in context_text:
        if "forecast" in label_text:
            return maybe_alpha(BRAND["gray"])
        if "gust" in label_text:
            return maybe_alpha(BRAND["gold"])
        if "lull" in label_text:
            return maybe_alpha(BRAND["glass"])
        if label_text == "speed":
            return maybe_alpha(BRAND["sky"])

    if "mister south" in label_text:
        return maybe_alpha("#CE93D8")
    if label_text == "ph":
        return maybe_alpha(BRAND["gold"])
    if "orp" in label_text:
        return maybe_alpha(BRAND["violet"])
    if "mister west" in label_text:
        return maybe_alpha("#F48FB1")
    if "mister center" in label_text:
        return maybe_alpha("#E040FB")
    if "fan 1" in label_text:
        return maybe_alpha(BRAND["fan"])
    if "fan 2" in label_text:
        return maybe_alpha("#5C6BC0")
    if label_text == "fog" or label_text.startswith("fog "):
        return maybe_alpha(BRAND["fog"])
    if "heat 2" in label_text or "gas heat" in label_text:
        return maybe_alpha("#F4511E")
    if "heat 1" in label_text:
        return maybe_alpha("#FF9800")
    if label_text in {"overhead light", "overhead light base"}:
        return maybe_alpha(BRAND["leaf"])
    if label_text in {"grow light", "grow light base"}:
        return maybe_alpha(BRAND["water"])
    if label_text == "grow light on":
        return maybe_alpha("#5794F2")
    if "grow light grow" in label_text:
        return maybe_alpha(BRAND["water"])
    if "grow light main" in label_text:
        return maybe_alpha(BRAND["leaf"])
    if "grow light" in label_text:
        return maybe_alpha("#9CCC65")
    if label_text.startswith("switch.greenhouse_main"):
        return maybe_alpha("#FF5C8A")
    if label_text.startswith("switch.greenhouse_grow"):
        return maybe_alpha("#5794F2")
    if label_text in {
        "runtime load (w)",
        "runtime electric",
        "runtime electric ($)",
    }:
        return maybe_alpha(BRAND["leaf"])
    if label_text == "runtime heat 1 (w)":
        return maybe_alpha(BRAND["heat"])
    if label_text == "runtime fans (w)":
        return maybe_alpha(BRAND["fan"])
    if label_text == "runtime other (fog/lights/vent)":
        return maybe_alpha(BRAND["water"])
    if "cortex" in label_text or "vm-docker-ai" in label_text:
        return maybe_alpha("#AB47BC")
    if "sentinel" in label_text or "vm-docker-frigate" in label_text:
        return maybe_alpha("#42A5F5")
    if "immich" in label_text or "vm-docker-immich" in label_text:
        return maybe_alpha("#F48FB1")
    if "air vpd" in label_text:
        return maybe_alpha(BRAND["violet"])
    if "east" in label_text:
        return maybe_alpha(BRAND["water"])
    if "west" in label_text:
        return maybe_alpha(BRAND["teal"])
    if "center" in label_text:
        return maybe_alpha(BRAND["mint"])
    if "south" in label_text:
        return maybe_alpha(BRAND["leaf"])
    if "estimated" in label_text and "dli" in label_text:
        return maybe_alpha("#73BF69")
    if "sensor dli" in label_text:
        return maybe_alpha(BRAND["gold"])
    if "direct" in label_text and any(k in context_text for k in ("solar", "irradiance", "light")):
        return maybe_alpha(BRAND["gold"])
    if "diffuse" in label_text and any(k in context_text for k in ("solar", "irradiance", "light")):
        return maybe_alpha("#90CAF9")
    if "main expected" in label_text:
        return maybe_alpha("#9CCC65")
    if "grow expected" in label_text:
        return maybe_alpha("#80D8FF")
    if "temp compliance" in label_text:
        return maybe_alpha(BRAND["heat"])
    if "vpd compliance" in label_text:
        return maybe_alpha(BRAND["violet"])
    if "total compliance" in label_text or "both-axis compliance" in label_text:
        return maybe_alpha(BRAND["leaf"])
    if "temp stress" in label_text:
        return maybe_alpha(BRAND["heat"])
    if "vpd stress" in label_text:
        return maybe_alpha(BRAND["violet"])
    if "cold stress" in label_text:
        return maybe_alpha(BRAND["sky"])
    if "total stress" in label_text:
        return maybe_alpha(BRAND["fault"])
    if label_text in {"electric", "electric ($)", "greenhouse (w)", "shelly meter (w)", "shelly electric"}:
        return maybe_alpha("#66BB6A")
    if any(k in label_text for k in ("electric $", "daytime watts", "night watts", "kwh/day")):
        return maybe_alpha("#66BB6A")
    if label_text in {"gas", "gas ($)"} or label_text.startswith("gas "):
        return maybe_alpha("#F44336")
    if label_text in {"water", "water ($)"} or label_text.startswith("water "):
        return maybe_alpha("#2196F3")
    if label_text == "temp":
        return maybe_alpha("#AB47BC")
    if label_text == "humidity":
        return maybe_alpha("#26A69A")
    if label_text == "transmission":
        return maybe_alpha(BRAND["gold"])
    if "compliant high" in label_text or "compliant low" in label_text:
        return maybe_alpha("#2E7D32")

    if label_text in {"target band", "target band base"}:
        return maybe_alpha(BRAND["leaf"])
    if label_text == "greenhouse" and "vpd" in context_text:
        return maybe_alpha(BRAND["violet"])
    if label_text == "greenhouse" and "temperature" in context_text:
        return maybe_alpha(BRAND["leaf"])
    if label_text in {"outdoor", "outdoor forecast"} and any(k in context_text for k in ("vpd", "temperature")):
        return maybe_alpha(BRAND["gray"])

    if "threshold" in label_text:
        if "main/grow" in label_text:
            return maybe_alpha("#73BF69")
        if "main" in label_text:
            return maybe_alpha("#73BF69")
        if "grow" in label_text:
            return maybe_alpha("#5794F2")
        if color_text in COLOR_LITERAL_MAP:
            return maybe_alpha(COLOR_LITERAL_MAP[color_text])
        return maybe_alpha(BRAND["leaf"])
    if any(k in label_text for k in ("baseline", "target")):
        return maybe_alpha(BRAND["gray"])
    if any(k in text for k in ("alert", "critical", "fault", "panic", "error", "reset", "oscillation")):
        return maybe_alpha(BRAND["fault"])
    if "forecast" in label_text:
        if any(k in text for k in ("solar", "sun", "irradiance", "lux", "dli", "light")):
            return maybe_alpha(BRAND["gold_soft"])
        return maybe_alpha(BRAND["gray"])
    if "cloud cover" in context_text:
        if label_text == "low":
            return maybe_alpha(BRAND["glass"])
        if label_text == "high":
            return maybe_alpha(BRAND["slate"])
        return maybe_alpha(BRAND["gray"])
    if "outdoor" in label_text and any(k in text for k in ("solar", "sun", "irradiance", "lux", "dli", "light")):
        return maybe_alpha(BRAND["gold_soft"])
    if any(k in text for k in ("solar", "sun", "lux", "dli", "light", "irradiance", "ppfd", "uv")):
        return maybe_alpha(BRAND["gold"])
    if any(k in text for k in ("gas", "therm", "heat 2")):
        return maybe_alpha(BRAND["gas"])
    if any(k in text for k in ("heat", "heater", "hot", "warm", "temp high", "setpoint high")):
        return maybe_alpha(BRAND["heat"])
    if any(k in text for k in ("electric", "kwh", "greenhouse (w)")):
        return maybe_alpha("#66BB6A")
    if any(k in text for k in ("kw", "watt", "powerwall", "gpu", "power")):
        return maybe_alpha(BRAND["mint"])
    if any(k in text for k in ("water", "rain", "snow", "precip", "flow", "gpm", "fog", "mist", "drip", "irrig")):
        return maybe_alpha(BRAND["water"])
    if any(k in text for k in ("fan", "cool", "vent", "wind", "gust", "lull")):
        return maybe_alpha(BRAND["sky"])
    if "outdoor" in label_text and "vpd" in label_text:
        return maybe_alpha(BRAND["gray"])
    if "vpd" in label_text:
        return maybe_alpha(BRAND["violet"])
    if any(k in text for k in ("humidity", "rh", "dew", "wet bulb")):
        return maybe_alpha(BRAND["teal"])
    if "temperature" in context_text and any(k in label_text for k in ("observed", "actual", "indoor")):
        return maybe_alpha(BRAND["leaf"])
    if "temperature" in context_text and any(k in label_text for k in ("feels", "high")):
        return maybe_alpha(BRAND["gold"])
    if any(k in label_text for k in ("outdoor", "cloud", "bias")):
        return maybe_alpha(BRAND["gray"])
    if any(k in text for k in ("compliance", "compliant", "safe", "ok", "healthy", "coverage", "score")):
        return maybe_alpha(BRAND["leaf"])
    if any(k in text for k in ("sensor", "actual", "observed", "indoor", "air")):
        return maybe_alpha(BRAND["leaf"])
    if any(k in text for k in ("orp", "planner", "plan")):
        return maybe_alpha(BRAND["violet"])
    if "ec" in text or "tds" in text:
        return maybe_alpha(BRAND["teal"])
    if "ph" in text:
        return maybe_alpha(BRAND["gold"])

    if color_text in COLOR_LITERAL_MAP:
        return maybe_alpha(COLOR_LITERAL_MAP[color_text])
    if color_text.startswith("#") and color_text.lower() in COLOR_LITERAL_MAP:
        return maybe_alpha(COLOR_LITERAL_MAP[color_text.lower()])
    return maybe_alpha(BRAND["mint"])


def is_daylight_series(label: str, context: str = "") -> bool:
    label_text = normalize_key(label)
    context_text = normalize_key(context)
    text = f"{label_text} {context_text}".strip()

    if any(
        excluded in label_text
        for excluded in (
            "altitude",
            "azimuth",
            "baseline",
            "grow light",
            "main expected",
            "target",
            "threshold",
            "switch.greenhouse",
        )
    ):
        return False

    daylight_terms = ("solar", "sun", "lux", "illuminance", "irradiance", "sunshine", "uv")
    if any(term in label_text for term in daylight_terms):
        return True

    solar_context = any(term in context_text for term in ("solar", "sun", "lux", "irradiance", "sunshine"))
    contextual_series = ("actual", "observed", "forecast", "direct", "diffuse")
    return solar_context and any(term in label_text for term in contextual_series)


def is_daylight_forecast(label: str) -> bool:
    return "forecast" in normalize_key(label)


def is_homepage_solar_backdrop(label: str, panel: dict[str, Any] | None = None, dashboard_uid: str = "") -> bool:
    if dashboard_uid != "site-home" or panel is None:
        return False
    try:
        panel_id = int(panel.get("id"))
    except (TypeError, ValueError):
        return False
    return panel_id in HOMEPAGE_SOLAR_PANEL_IDS and is_daylight_series(label, str(panel.get("title") or ""))


def daylight_fill_opacity(label: str, panel: dict[str, Any] | None = None, dashboard_uid: str = "") -> int:
    if is_homepage_solar_backdrop(label, panel, dashboard_uid):
        return HOMEPAGE_SOLAR_FILL_OPACITY
    label_text = normalize_key(label)
    if "indoor lux" in label_text:
        return INDOOR_LUX_FILL_OPACITY
    if "outdoor lux" in label_text:
        return OUTDOOR_LUX_FILL_OPACITY
    if any(term in label_text for term in ("lux", "illuminance")):
        return DAYLIGHT_LUX_FILL_OPACITY
    if is_daylight_forecast(label):
        return DAYLIGHT_FORECAST_FILL_OPACITY
    return DAYLIGHT_FILL_OPACITY


def daylight_gradient_mode(label: str, panel: dict[str, Any] | None = None, dashboard_uid: str = "") -> str:
    if is_homepage_solar_backdrop(label, panel, dashboard_uid):
        return HOMEPAGE_SOLAR_GRADIENT_MODE
    return DAYLIGHT_GRADIENT_MODE


def daylight_color(label: str, panel: dict[str, Any] | None = None, dashboard_uid: str = "") -> str:
    if is_homepage_solar_backdrop(label, panel, dashboard_uid):
        return BRAND["gold"]
    label_text = normalize_key(label)
    if any(term in label_text for term in ("lux", "illuminance", "tempest")):
        return BRAND["gold"]
    return BRAND["gold_soft"] if is_daylight_forecast(label) else BRAND["gold"]


def is_outdoor_vpd_label(label: str) -> bool:
    label_text = normalize_key(label)
    return "vpd" in label_text and "outdoor" in label_text


def is_indoor_vpd_label(label: str) -> bool:
    label_text = normalize_key(label)
    return "vpd" in label_text and "indoor" in label_text and "outdoor" not in label_text


def vpd_context_labels(panel: dict[str, Any]) -> set[str]:
    if panel.get("type") != "timeseries":
        return set()
    labels = set(target_aliases(panel))
    for override in panel.get("fieldConfig", {}).get("overrides", []) or []:
        label = override.get("matcher", {}).get("options")
        if isinstance(label, str) and label.strip():
            labels.add(label.strip())
    return {label for label in labels if is_outdoor_vpd_label(label) or is_indoor_vpd_label(label)}


def is_relay_state_label(label: str) -> bool:
    label_text = normalize_key(label)
    if not label_text or label_text.endswith(" base"):
        return False
    if any(
        term in label_text
        for term in (
            "threshold",
            "compliant",
            "setpoint",
            "baseline",
            "forecast",
            "solar",
            "sun",
            "lux",
            "dli",
            "vpd",
            "temperature",
            "humidity",
            "cost",
            "runtime",
        )
    ):
        return False
    return label_text.startswith("switch.") or any(
        term in label_text
        for term in (
            " heat",
            "heat ",
            "heater",
            "fan",
            "fog",
            "mister",
            "relay",
            "pump",
            "vent",
            "irrigation",
            "grow light",
            "overhead light",
        )
    )


def relay_state_lane_pairs(panel: dict[str, Any]) -> list[tuple[str, str]]:
    if panel.get("type") != "timeseries":
        return []
    pairs: list[tuple[str, str]] = []
    for override in panel.get("fieldConfig", {}).get("overrides", []) or []:
        label = override.get("matcher", {}).get("options")
        if not isinstance(label, str) or not is_relay_state_label(label):
            continue
        props = {prop.get("id"): prop.get("value") for prop in override.get("properties", []) or []}
        hide_from = props.get("custom.hideFrom")
        if isinstance(hide_from, dict) and hide_from.get("viz") is True:
            continue
        base_label = props.get("custom.fillBelowTo")
        if isinstance(base_label, str) and normalize_key(base_label).endswith(" base"):
            pairs.append((label, base_label))
    return sorted(set(pairs))


def relay_state_fill_opacity(dashboard_uid: str) -> int:
    if dashboard_uid == "site-home":
        return HOMEPAGE_RELAY_STATE_FILL_OPACITY
    return RELAY_STATE_FILL_OPACITY


def normalize_color_literal(value: Any, label: str, context: str = "") -> Any:
    if not isinstance(value, str):
        return value
    text = normalize_key(value)
    if text.startswith("#") or text in COLOR_LITERAL_MAP or text.startswith("rgb"):
        return semantic_color(label, value, context)
    return value


def normalize_threshold_color(value: Any) -> Any:
    """Brand threshold colors by severity literal, not by panel title."""

    if not isinstance(value, str):
        return value
    text = normalize_key(value)
    if text in NON_SERIES_COLOR_VALUES:
        return value
    alpha_match = re.match(r"rgba?\(([^)]*)\)", text)
    if text in COLOR_LITERAL_MAP:
        return COLOR_LITERAL_MAP[text]
    if text.startswith("#") and text in COLOR_LITERAL_MAP:
        return COLOR_LITERAL_MAP[text]
    if alpha_match:
        parts = [part.strip() for part in alpha_match.group(1).split(",")]
        if len(parts) >= 4:
            return rgba_from(BRAND["gray"], parts[3])
    return value


def embedded_panels(vault_root: Path) -> dict[str, set[int]]:
    embedded: dict[str, set[int]] = defaultdict(set)
    for _path, _line, uid, panel_id, _query in site_embeds(vault_root):
        embedded[uid].add(panel_id)
    return dict(embedded)


def site_embeds(vault_root: Path) -> list[tuple[Path, int, str, int, dict[str, list[str]]]]:
    embeds: list[tuple[Path, int, str, int, dict[str, list[str]]]] = []
    for path in vault_root.rglob("*.md"):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for match in EMBED_RE.finditer(text):
            query = parse_qs(urlparse(match.group(0)).query)
            panel_id = query.get("panelId", [""])[0]
            if panel_id.isdigit():
                line = text[: match.start()].count("\n") + 1
                embeds.append((path, line, match.group(1), int(panel_id), query))
    return embeds


def check_site_embed_contract(vault_root: Path) -> list[str]:
    findings: list[str] = []
    embed_count = 0
    for path, line, _uid, _panel_id, query in site_embeds(vault_root):
        embed_count += 1
        theme = query.get("theme", [""])[0]
        if theme not in {"light", "dark"}:
            findings.append(f"{path}: line {line} Grafana embed theme is {theme or '<missing>'}, expected light/dark")
    if embed_count == 0:
        findings.append(f"{vault_root}: no Grafana /d-solo embeds found")

    chrome_files = [
        REPO_ROOT / "site" / "quartz" / "components" / "GrafanaEmbeds.tsx",
        REPO_ROOT / "site" / "quartz" / "styles" / "custom.scss",
        vault_root / "static" / "grafana-controls.f0ea8065.css",
    ]
    for path in chrome_files:
        if not path.exists():
            findings.append(f"{path}: missing Grafana site-chrome source")
            continue
        text = path.read_text(encoding="utf-8")
        if path.name == "GrafanaEmbeds.tsx":
            required = [".grafana-embed {", "background: transparent;", "border: 0;", "box-shadow: none;"]
        elif path.name == "custom.scss":
            required = [
                ".pg {",
                "border: 0;",
                "background: transparent;",
                "box-shadow: none;",
                ".home-panel-card {",
                ".home-panel-card__visual {",
            ]
        else:
            required = [".pg iframe {", "border: 0;", "border-radius: 0;", "background: transparent;"]
            if "border: 1px solid" in text:
                findings.append(f"{path}: legacy iframe border would create double Grafana chrome")
        for snippet in required:
            if snippet not in text:
                findings.append(f"{path}: missing site-chrome contract snippet {snippet!r}")
        if path.name == "custom.scss":
            home_visual = re.search(
                r"\.home-panel-card__visual\s*\{(?P<body>.*?)^\s*\}", text, flags=re.MULTILINE | re.DOTALL
            )
            if not home_visual:
                findings.append(f"{path}: missing homepage Grafana visual wrapper style")
            else:
                body = home_visual.group("body")
                for snippet in ("padding: 0;", "border: 0;", "background: transparent;", "box-shadow: none;"):
                    if snippet not in body:
                        findings.append(f"{path}: homepage Grafana visual wrapper missing {snippet!r}")
                if "border: 1px solid" in body or "box-shadow: 0 " in body:
                    findings.append(f"{path}: homepage Grafana visual wrapper still adds site chrome")
    return findings


def panel_uses_time_filter(panel: dict[str, Any]) -> bool:
    for target in panel.get("targets", []) or []:
        if isinstance(target, dict) and "$__timeFilter" in str(target.get("rawSql", "")):
            return True
    return False


def check_time_filtered_embed_ranges(vault_root: Path, paths_by_uid: dict[str, list[Path]]) -> list[str]:
    findings: list[str] = []
    dashboards: dict[str, dict[int, dict[str, Any]]] = {}
    for uid, paths in paths_by_uid.items():
        if not paths:
            continue
        dashboard = load_json(paths[0])
        dashboards[uid] = {int(panel["id"]): panel for panel in iter_panels(dashboard.get("panels", []))}

    for path, line, uid, panel_id, query in site_embeds(vault_root):
        panel = dashboards.get(uid, {}).get(panel_id)
        if not panel or not panel_uses_time_filter(panel):
            continue
        if "from" not in query or "to" not in query:
            findings.append(
                f"{path}: line {line} {uid} panel {panel_id} {panel.get('title')!r} uses $__timeFilter "
                "but the embed URL has no explicit from/to range"
            )
    return findings


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def dashboard_paths(embedded: dict[str, set[int]]) -> dict[str, list[Path]]:
    paths: dict[str, list[Path]] = defaultdict(list)
    for directory in DASHBOARD_DIRS:
        for path in sorted(directory.glob("*.json")):
            try:
                dashboard = load_json(path)
            except json.JSONDecodeError:
                continue
            uid = dashboard.get("uid")
            if uid in embedded:
                paths[uid].append(path)
    return dict(paths)


def all_dashboard_paths() -> list[Path]:
    paths: list[Path] = []
    for directory in DASHBOARD_DIRS:
        paths.extend(sorted(directory.glob("*.json")))
    return paths


def iter_panels(panels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for panel in panels or []:
        if panel.get("panels"):
            found.extend(iter_panels(panel["panels"]))
        if panel.get("type") and panel.get("id") is not None:
            found.append(panel)
    return found


def ensure_legend_and_tooltip(panel: dict[str, Any]) -> None:
    panel_type = panel.get("type")
    if panel_type not in {"timeseries", "barchart", "histogram", "piechart", "state-timeline", "bargauge", "gauge"}:
        return
    options = panel.setdefault("options", {})
    legend = options.setdefault("legend", {})
    if panel_type in {"timeseries", "barchart", "histogram", "piechart"}:
        legend["showLegend"] = True
        legend["displayMode"] = "list"
        legend["placement"] = "bottom"
        legend["calcs"] = []
    tooltip = options.setdefault("tooltip", {})
    if panel_type in {"timeseries", "barchart", "histogram", "state-timeline"}:
        tooltip.setdefault("mode", "multi")
        tooltip.setdefault("sort", "none")


def ensure_panel_chrome(panel: dict[str, Any]) -> None:
    panel["transparent"] = True
    panel_type = panel.get("type")
    options = panel.setdefault("options", {})

    if panel_type == "stat":
        if options.get("colorMode") in {None, "background", "background_solid", "background_gradient"}:
            options["colorMode"] = "value"
        options["graphMode"] = "none"
        return

    if panel_type == "table":
        options["showHeader"] = True
        options["cellHeight"] = "sm"
        footer = options.setdefault("footer", {})
        footer["show"] = False
        footer.setdefault("reducer", ["sum"])
        footer.setdefault("countRows", False)
        footer.setdefault("fields", "")
        defaults = panel.setdefault("fieldConfig", {}).setdefault("defaults", {})
        custom = defaults.setdefault("custom", {})
        custom.setdefault("align", "auto")
        custom.setdefault("cellOptions", {"type": "auto"})
        custom.setdefault("inspect", False)
        return

    defaults = panel.setdefault("fieldConfig", {}).setdefault("defaults", {})
    custom = defaults.setdefault("custom", {})
    if panel_type in {"timeseries", "barchart", "histogram"}:
        custom.setdefault("axisColorMode", "text")
        custom.setdefault("axisPlacement", "auto")
        custom.setdefault("thresholdsStyle", {"mode": "off"})
    if panel_type == "timeseries":
        custom.setdefault("lineWidth", 2)
        custom.setdefault("fillOpacity", 8)
        custom.setdefault("showPoints", "never")
    if panel_type == "barchart":
        custom.setdefault("fillOpacity", 80)
        custom.setdefault("lineWidth", 0)


def replace_first_target_sql(panel: dict[str, Any], raw_sql: str) -> None:
    for target in panel.get("targets", []) or []:
        if isinstance(target, dict) and target.get("rawSql") is not None:
            target["rawSql"] = raw_sql
            return


def replace_target_sql(panel: dict[str, Any], ref_id: str, raw_sql: str) -> None:
    for target in panel.get("targets", []) or []:
        if isinstance(target, dict) and target.get("refId") == ref_id:
            target["rawSql"] = raw_sql
            return


def relay_state_lane_sql(lanes: tuple[tuple[str, str, str, float, float], ...]) -> str:
    values_sql = ",\n    ".join(
        (
            f"('{equipment}'::text, '{top_label}'::text, '{base_label}'::text, "
            f"{lane_low}::double precision, {lane_high}::double precision)"
        )
        for equipment, top_label, base_label, lane_low, lane_high in lanes
    )
    equipment_sql = ", ".join(f"'{equipment}'" for equipment, *_ in lanes)
    return f"""WITH bounds AS MATERIALIZED (
  SELECT
    $__timeFrom()::timestamptz AS from_ts,
    LEAST($__timeTo()::timestamptz, now()) AS to_ts,
    interval '5 minutes' AS step,
    date_trunc('hour', $__timeFrom()::timestamptz) AS seed_start_ts
),
buckets AS MATERIALIZED (
  SELECT generate_series(time_bucket(b.step, b.seed_start_ts), b.to_ts, b.step) AS time
  FROM bounds b
),
lane_metrics AS MATERIALIZED (
  SELECT *
  FROM (VALUES
    {values_sql}
  ) AS m(equipment, top_metric, base_metric, lane_low, lane_high)
),
state_seed AS MATERIALIZED (
  SELECT bd.seed_start_ts AS time, m.equipment, COALESCE(seed.state, false) AS state
  FROM bounds bd
  CROSS JOIN lane_metrics m
  LEFT JOIN LATERAL (
    SELECT e.state
    FROM equipment_state e
    WHERE e.greenhouse_id = 'vallery'
      AND e.equipment = m.equipment
      AND e.ts <= bd.seed_start_ts
    ORDER BY e.ts DESC
    LIMIT 1
  ) seed ON true
),
state_events AS MATERIALIZED (
  SELECT e.ts AS time, e.equipment, e.state
  FROM equipment_state e, bounds bd
  WHERE e.greenhouse_id = 'vallery'
    AND e.equipment IN ({equipment_sql})
    AND e.ts >= bd.seed_start_ts
    AND e.ts <= bd.to_ts
),
state_timeline AS MATERIALIZED (
  SELECT
    e.time,
    e.equipment,
    e.state,
    lead(e.time, 1, (SELECT to_ts FROM bounds)) OVER (PARTITION BY e.equipment ORDER BY e.time) AS next_time
  FROM (
    SELECT time, equipment, state FROM state_seed
    UNION ALL
    SELECT time, equipment, state FROM state_events
  ) e
),
state_segments AS MATERIALIZED (
  SELECT
    greatest(st.time, bd.seed_start_ts) AS start_time,
    least(st.next_time, bd.to_ts) AS end_time,
    st.equipment
  FROM state_timeline st
  CROSS JOIN bounds bd
  WHERE st.state
    AND st.time < bd.to_ts
    AND st.next_time > bd.seed_start_ts
),
lanes AS (
  SELECT
    b.time,
    m.top_metric AS metric,
    CASE WHEN EXISTS (
      SELECT 1
      FROM state_segments s
      WHERE s.equipment = m.equipment
        AND s.start_time < b.time + bd.step
        AND s.end_time > b.time
    ) THEN m.lane_high ELSE NULL::double precision END AS value
  FROM buckets b
  CROSS JOIN bounds bd
  CROSS JOIN lane_metrics m
  WHERE b.time >= bd.from_ts
    AND b.time <= bd.to_ts
  UNION ALL
  SELECT b.time, m.base_metric, m.lane_low
  FROM buckets b
  CROSS JOIN bounds bd
  CROSS JOIN lane_metrics m
  WHERE b.time >= bd.from_ts
    AND b.time <= bd.to_ts
)
SELECT time, metric, value
FROM lanes
ORDER BY time, metric"""


HOMEPAGE_LIGHTING_STATE_LANE_SQL = """WITH raw_bounds AS (
  SELECT $__timeFrom()::timestamptz AS from_ts, $__timeTo()::timestamptz AS to_ts
),
bounds AS MATERIALIZED (
  SELECT GREATEST(from_ts, to_ts - interval '7 days') AS from_ts, to_ts AS to_ts,
    LEAST(to_ts, now()) AS state_to_ts,
    date_trunc('hour', GREATEST(from_ts, to_ts - interval '7 days')) AS seed_start_ts,
    interval '10 minutes' AS step
  FROM raw_bounds
),
policy AS MATERIALIZED (
  SELECT
    greatest(100.0, least(100000.0, COALESCE(
      max(p.lux_on_threshold) FILTER (WHERE p.light_key='main'),
      40000.0))) AS main_on,
    greatest(0.0, least(25000.0, COALESCE(
      max(p.lux_hysteresis) FILTER (WHERE p.light_key='main'),
      8000.0))) AS main_hyst,
    greatest(100.0, least(100000.0, COALESCE(
      max(p.lux_on_threshold) FILTER (WHERE p.light_key='grow'),
      40000.0))) AS grow_on,
    greatest(0.0, least(25000.0, COALESCE(
      max(p.lux_hysteresis) FILTER (WHERE p.light_key='grow'),
      8000.0))) AS grow_hyst
  FROM bounds bd
  CROSS JOIN LATERAL fn_lighting_circuit_policy(bd.state_to_ts, 'vallery') p
),
policy_wide AS MATERIALIZED (
  SELECT
    main_on,
    main_on + main_hyst AS main_off,
    grow_on,
    grow_on + grow_hyst AS grow_off
  FROM policy
),
buckets AS MATERIALIZED (
  SELECT generate_series(time_bucket(b.step, b.seed_start_ts), LEAST(b.to_ts, now()), b.step) AS time FROM bounds b
),
lane_metrics AS MATERIALIZED (
  SELECT *
  FROM policy_wide p
  CROSS JOIN LATERAL (VALUES
    (
      'grow_light_main'::text,
      'Overhead Light'::text,
      p.main_on + GREATEST(250.0, (p.main_off - p.main_on) * 0.47)
    ),
    (
      'grow_light_grow'::text,
      'Grow Light'::text,
      p.grow_off
    )
  ) AS m(equipment, metric, value_when_on)
),
state_seed AS MATERIALIZED (
  SELECT bd.seed_start_ts AS time, m.equipment, COALESCE(seed.state, false) AS state
  FROM bounds bd CROSS JOIN lane_metrics m
  LEFT JOIN LATERAL (
    SELECT e.state
    FROM equipment_state e
    WHERE e.greenhouse_id='vallery' AND e.equipment=m.equipment AND e.ts <= bd.seed_start_ts
    ORDER BY e.ts DESC LIMIT 1
  ) seed ON true
),
state_events AS MATERIALIZED (
  SELECT e.ts AS time, e.equipment, e.state
  FROM equipment_state e, bounds bd
  WHERE e.greenhouse_id='vallery'
    AND e.equipment IN ('grow_light_main','grow_light_grow')
    AND e.ts >= bd.seed_start_ts
    AND e.ts <= bd.state_to_ts
),
state_timeline AS MATERIALIZED (
  SELECT e.time, e.equipment, e.state,
    lead(e.time,1,(SELECT state_to_ts FROM bounds)) OVER (PARTITION BY e.equipment ORDER BY e.time) AS next_time
  FROM (SELECT time,equipment,state FROM state_seed UNION ALL SELECT time,equipment,state FROM state_events) e
),
state_segments AS MATERIALIZED (
  SELECT greatest(st.time, bd.seed_start_ts) AS start_time, least(st.next_time, bd.state_to_ts) AS end_time, st.equipment
  FROM state_timeline st CROSS JOIN bounds bd
  WHERE st.state AND st.time < bd.state_to_ts AND st.next_time > bd.seed_start_ts
),
lanes AS (
  SELECT b.time, m.metric,
    CASE WHEN EXISTS (
      SELECT 1
      FROM state_segments s
      WHERE s.equipment=m.equipment
        AND s.start_time < b.time + bd.step
        AND s.end_time > b.time
    ) THEN m.value_when_on ELSE NULL::double precision END AS value
  FROM buckets b CROSS JOIN bounds bd CROSS JOIN lane_metrics m
  WHERE b.time >= bd.from_ts AND b.time <= bd.state_to_ts
)
SELECT time, metric, value FROM lanes ORDER BY time, metric"""


HOMEPAGE_LIGHTING_SOLAR_SQL = """WITH raw_bounds AS (
  SELECT $__timeFrom()::timestamptz AS from_ts, $__timeTo()::timestamptz AS to_ts
),
bounds AS MATERIALIZED (
  SELECT GREATEST(from_ts, to_ts - interval '7 days') AS from_ts, to_ts AS to_ts,
    LEAST(to_ts, now()) AS state_to_ts, interval '10 minutes' AS step
  FROM raw_bounds
),
solar_actual AS MATERIALIZED (
  SELECT time_bucket((SELECT step FROM bounds), c.ts) AS time, 'Solar'::text AS metric,
    avg(COALESCE(c.outdoor_lux, c.lux))::double precision AS value
  FROM climate c
  WHERE c.ts >= (SELECT from_ts FROM bounds) AND c.ts <= (SELECT state_to_ts FROM bounds)
    AND COALESCE(c.outdoor_lux, c.lux) IS NOT NULL
  GROUP BY 1
),
solar_forecast_seed AS MATERIALIZED (
  SELECT time, 'Solar Forecast'::text AS metric, value FROM solar_actual
  WHERE (SELECT to_ts FROM bounds) > (SELECT state_to_ts FROM bounds)
  ORDER BY time DESC LIMIT 1
),
solar_forecast AS MATERIALIZED (
  SELECT DISTINCT ON (time_bucket((SELECT step FROM bounds), wf.ts))
    time_bucket((SELECT step FROM bounds), wf.ts) AS time, 'Solar Forecast'::text AS metric,
    (wf.solar_w_m2 * 120.0)::double precision AS value
  FROM weather_forecast wf
  WHERE wf.ts > (SELECT state_to_ts FROM bounds) AND wf.ts >= (SELECT from_ts FROM bounds)
    AND wf.ts <= (SELECT to_ts FROM bounds) AND wf.solar_w_m2 IS NOT NULL
  ORDER BY time_bucket((SELECT step FROM bounds), wf.ts), wf.fetched_at DESC
)
SELECT time, metric, value
FROM (
  SELECT time, metric, value FROM solar_actual
  UNION ALL SELECT time, metric, value FROM solar_forecast_seed
  UNION ALL SELECT time, metric, value FROM solar_forecast
) series
ORDER BY time,
  CASE metric WHEN 'Solar' THEN 0 WHEN 'Solar Forecast' THEN 1
    ELSE 2 END, metric"""


def simplify_homepage_lighting_solar_target(panel: dict[str, Any]) -> None:
    for target in panel.get("targets", []) or []:
        if not isinstance(target, dict) or target.get("refId") != "A":
            continue
        target["rawSql"] = HOMEPAGE_LIGHTING_SOLAR_SQL
        return


def target_uses_equipment_state_lane(
    target: dict[str, Any], lanes: tuple[tuple[str, str, str, float, float], ...]
) -> bool:
    raw_sql = target.get("rawSql")
    if not isinstance(raw_sql, str) or "equipment_state" not in raw_sql:
        return False
    return any(
        f"equipment='{equipment}'" in raw_sql
        or f"equipment = '{equipment}'" in raw_sql
        or f"'{equipment}'" in raw_sql
        or f"'{top_label}'" in raw_sql
        or f'"{top_label}"' in raw_sql
        for equipment, top_label, *_ in lanes
    )


def replace_equipment_state_targets_with_lanes(
    panel: dict[str, Any],
    lanes: tuple[tuple[str, str, str, float, float], ...],
) -> None:
    targets = panel.get("targets", []) or []
    first_state_target = next(
        (target for target in targets if isinstance(target, dict) and target_uses_equipment_state_lane(target, lanes)),
        None,
    )
    if first_state_target is None:
        return

    lane_target = deepcopy(first_state_target)
    lane_target["rawSql"] = relay_state_lane_sql(lanes)
    lane_target["format"] = "time_series"
    lane_target["editorMode"] = "code"

    replaced = False
    next_targets = []
    for target in targets:
        if isinstance(target, dict) and target_uses_equipment_state_lane(target, lanes):
            if not replaced:
                next_targets.append(lane_target)
                replaced = True
            continue
        next_targets.append(target)
    panel["targets"] = next_targets


def panel_has_relay_state_lanes(
    panel: dict[str, Any],
    lanes: tuple[tuple[str, str, str, float, float], ...],
) -> bool:
    if relay_state_lane_pairs(panel):
        return True
    return any(
        isinstance(target, dict) and target_uses_equipment_state_lane(target, lanes)
        for target in panel.get("targets", []) or []
    )


def ensure_relay_state_lane_overrides(
    panel: dict[str, Any],
    lanes: tuple[tuple[str, str, str, float, float], ...],
) -> None:
    title = str(panel.get("title") or "")
    for _equipment, top_label, base_label, _lane_low, _lane_high in lanes:
        top_override = override_for_label(panel, top_label)
        current_color = override_props(panel, top_label).get("color")
        current_fixed_color = current_color.get("fixedColor") if isinstance(current_color, dict) else None
        color = semantic_color(top_label, current_fixed_color, title)
        upsert_override_property(top_override, "color", {"fixedColor": color, "mode": "fixed"})
        upsert_override_property(top_override, "custom.fillBelowTo", base_label)

        base_override = override_for_label(panel, base_label)
        upsert_override_property(base_override, "color", {"fixedColor": color, "mode": "fixed"})


def strengthen_homepage_target_lines(panel: dict[str, Any]) -> None:
    if panel.get("type") != "timeseries":
        return
    if str(panel.get("title") or "") not in {"Temperature Compliance Band", "VPD Compliance Band"}:
        return
    if "Target" not in target_aliases(panel) and not override_props(panel, "Target"):
        return

    target_override = override_for_label(panel, "Target")
    upsert_override_property(target_override, "color", {"fixedColor": HOMEPAGE_TARGET_LINE_COLOR, "mode": "fixed"})
    upsert_override_property(target_override, "custom.drawStyle", "line")
    upsert_override_property(target_override, "custom.lineWidth", 2)
    upsert_override_property(target_override, "custom.lineInterpolation", "smooth")
    upsert_override_property(target_override, "custom.lineStyle", HOMEPAGE_TARGET_LINE_STYLE)
    upsert_override_property(target_override, "custom.fillOpacity", 0)
    upsert_override_property(target_override, "custom.showPoints", "never")


def strengthen_homepage_lighting_lanes(panel: dict[str, Any]) -> None:
    if panel.get("type") != "timeseries":
        return
    if str(panel.get("title") or "") not in HOMEPAGE_LIGHTING_PANEL_TITLES:
        return

    panel["title"] = "Lighting: Overhead vs Grow Circuit — Lux & Switch State"
    remove_override_labels(
        panel,
        {
            "Overhead Threshold Base",
            "Overhead Threshold",
            "Grow Threshold Base",
            "Grow Threshold",
            "Overhead Light Base",
            "Grow Light Base",
        },
    )
    simplify_homepage_lighting_solar_target(panel)
    replace_target_sql(panel, "B", HOMEPAGE_LIGHTING_STATE_LANE_SQL)
    for label, color in {
        "Overhead Light": HOMEPAGE_LIGHTING_OVERHEAD_STATE_COLOR,
        "Grow Light": HOMEPAGE_LIGHTING_GROW_STATE_COLOR,
    }.items():
        override = override_for_label(panel, label)
        upsert_override_property(override, "color", {"fixedColor": color, "mode": "fixed"})

    for label in ("Overhead Light", "Grow Light"):
        override = override_for_label(panel, label)
        upsert_override_property(override, "custom.drawStyle", "line")
        upsert_override_property(override, "custom.lineInterpolation", "stepAfter")
        upsert_override_property(override, "custom.lineWidth", HOMEPAGE_LIGHTING_STATE_LINE_WIDTH)
        upsert_override_property(override, "custom.fillOpacity", HOMEPAGE_LIGHTING_STATE_FILL_OPACITY)
        upsert_override_property(override, "custom.gradientMode", "opacity")
        upsert_override_property(override, "custom.spanNulls", False)
        upsert_override_property(override, "custom.showPoints", "never")
        upsert_override_property(override, "custom.pointSize", 0)
        upsert_override_property(override, "custom.hideFrom", {"legend": False, "tooltip": True, "viz": False})
        remove_override_property(override, "custom.fillBelowTo")
        remove_override_property(override, "custom.lineStyle")


def normalize_runtime_electric_sources(panel: dict[str, Any]) -> None:
    """Use runtime-modeled electric load as the public cost/load source."""

    title = str(panel.get("title") or "")
    if title in {
        "Runtime Electric $/Day (30-Day Avg)",
        "Shelly Electric $/Day (30-Day Avg)",
        "Electric $/Day (30-Day Average)",
    }:
        panel["title"] = "Runtime Electric $/Day (30-Day Avg)"
        panel["description"] = (
            "Average electric cost per day from published device wattage multiplied by observed on-time "
            "across the last 30 completed daily summaries."
        )

    if title in {
        "Runtime Daytime Watts (30-Day Avg)",
        "Shelly Daytime Watts (30-Day Avg)",
        "Daytime Watts (30-Day Average)",
    }:
        panel["title"] = "Runtime Daytime Watts (30-Day Avg)"
        replace_first_target_sql(panel, RUNTIME_DAYTIME_WATTS_SQL)

    if title in {
        "Runtime Night Watts (30-Day Avg)",
        "Shelly Night Watts (30-Day Avg)",
        "Night Watts (30-Day Average)",
    }:
        panel["title"] = "Runtime Night Watts (30-Day Avg)"
        replace_first_target_sql(panel, RUNTIME_NIGHT_WATTS_SQL)

    if title in {"30-Day Solar-Aligned Runtime Load", "30-Day Solar-Aligned Shelly Load"}:
        panel["title"] = "30-Day Solar-Aligned Runtime Load"
        panel["description"] = (
            "Percent of runtime-modeled electrical load consumed during solar hours across the last 30 completed days."
        )
        replace_first_target_sql(panel, RUNTIME_SOLAR_ALIGNED_LOAD_SQL)

    if title == "30-Day Powerwall Coverage":
        panel["description"] = (
            "Percent of average runtime-modeled overnight load covered by one Powerwall (13.5 kWh), "
            "using the last 30 completed days."
        )
        replace_first_target_sql(panel, RUNTIME_POWERWALL_COVERAGE_SQL)

    if title == "Runtime-Modeled kWh/day (30-Day Avg)":
        replace_first_target_sql(
            panel,
            "SELECT now() as time, round(avg(kwh_estimated)::numeric, 1) as value FROM daily_summary WHERE date >= current_date - interval '30 days' AND date < current_date AND kwh_estimated IS NOT NULL",
        )

    if title in {
        "Runtime-Modeled Power vs Solar Irradiance",
        "Shelly-Metered Power vs Solar Irradiance",
        "Greenhouse Power vs Solar Irradiance",
    }:
        panel["title"] = "Runtime-Modeled Power vs Solar Irradiance"
        panel["description"] = (
            "Runtime-modeled electric load from published device wattage and observed on-time overlaid "
            "with available solar radiation."
        )
        replace_target_sql(panel, "A", RUNTIME_ELECTRIC_WATTS_SQL)
        rename_override_label(panel, "Shelly Meter (W)", "Runtime Load (W)")
        rename_override_label(panel, "Greenhouse (W)", "Runtime Load (W)")

    if title in {"Runtime Power by Device Group", "Shelly Power by Circuit"}:
        panel["title"] = "Runtime Power by Device Group"
        replace_first_target_sql(panel, RUNTIME_POWER_BY_GROUP_SQL)
        rename_override_label(panel, "Shelly Heater Channel", "Runtime Heat 1 (W)")
        rename_override_label(panel, "Shelly Fans Estimate", "Runtime Fans (W)")
        rename_override_label(panel, "Shelly General Channel", "Runtime Other (fog/lights/vent)")

    if title == "Daily Cost by Type" and panel.get("type") == "timeseries":
        panel["description"] = (
            "Daily cost stacked by utility type. Electric uses published device watts multiplied by observed on-time."
        )
        rename_override_label(panel, "Shelly Electric", "Runtime Electric")
        for target in panel.get("targets", []) or []:
            raw_sql = target.get("rawSql")
            if isinstance(raw_sql, str):
                target["rawSql"] = raw_sql.replace('AS "Shelly Electric"', 'AS "Runtime Electric"')

    if title == "Monthly Resource Cost by Source (6 Months)" and panel.get("type") == "barchart":
        panel["description"] = (
            "Stacked monthly greenhouse utility cost for the current local month-to-date plus the previous "
            "five months. Electric uses published device watts multiplied by observed on-time; gas and water "
            "use the public rate assumptions shown on Resource Use."
        )
        replace_first_target_sql(panel, MONTHLY_RESOURCE_COST_SQL)
        rename_override_label(panel, "Shelly Electric ($)", "Runtime Electric ($)")
        rename_override_label(panel, "Electric ($)", "Runtime Electric ($)")

    if title == "Daily Cost by Source" and panel.get("type") == "timeseries":
        replace_first_target_sql(panel, DAILY_COST_BY_SOURCE_SQL)
        rename_override_label(panel, "Shelly Electric ($)", "Runtime Electric ($)")
        rename_override_label(panel, "Electric ($)", "Runtime Electric ($)")


def normalize_public_panel_schema(panel: dict[str, Any]) -> None:
    """Keep embedded panels readable at site width, not just valid in Grafana."""

    title = str(panel.get("title") or "")
    normalize_runtime_electric_sources(panel)

    if title == "Temperature Compliance Band" and panel.get("type") == "timeseries":
        if panel_has_relay_state_lanes(panel, TEMPERATURE_RELAY_STATE_LANES):
            replace_equipment_state_targets_with_lanes(panel, TEMPERATURE_RELAY_STATE_LANES)
            ensure_relay_state_lane_overrides(panel, TEMPERATURE_RELAY_STATE_LANES)
        strengthen_homepage_target_lines(panel)

    if title == "VPD Compliance Band" and panel.get("type") == "timeseries":
        if panel_has_relay_state_lanes(panel, VPD_RELAY_STATE_LANES):
            replace_equipment_state_targets_with_lanes(panel, VPD_RELAY_STATE_LANES)
            ensure_relay_state_lane_overrides(panel, VPD_RELAY_STATE_LANES)
        strengthen_homepage_target_lines(panel)

    if title in HOMEPAGE_LIGHTING_PANEL_TITLES:
        strengthen_homepage_lighting_lanes(panel)

    if title == "Lighting: Lux, Thresholds & Switch State" and panel.get("type") == "timeseries":
        lux_label = "Solar"
        rename_override_label(panel, "Natural Lux (10m avg)", lux_label)
        rename_override_label(panel, "Solar / Tempest Exterior Lux (10m avg)", lux_label)
        for target in panel.get("targets", []) or []:
            raw_sql = target.get("rawSql")
            if not isinstance(raw_sql, str):
                continue
            for old_metric in (
                "'Natural Lux (10m avg)'::text AS metric",
                "'Solar / Tempest Exterior Lux (10m avg)'::text AS metric",
            ):
                raw_sql = raw_sql.replace(old_metric, f"'{lux_label}'::text AS metric")
            target["rawSql"] = raw_sql
        panel["targets"] = [
            target
            for target in panel.get("targets", []) or []
            if not (isinstance(target, dict) and target.get("refId") in {"J", "K"})
        ]
        remove_override_labels(
            panel,
            {
                "Natural Lux (10m avg)",
                "Solar / Tempest Exterior Lux (10m avg)",
                "Main/Grow OFF Threshold",
                "Main/Grow ON Threshold",
                "Main OFF Threshold",
                "Main ON Threshold",
                "Grow OFF Threshold",
                "Grow ON Threshold",
            },
        )

        lux_override = override_for_label(panel, lux_label)
        upsert_override_property(lux_override, "color", {"fixedColor": BRAND["gold"], "mode": "fixed"})
        upsert_override_property(lux_override, "custom.lineWidth", 0)
        upsert_override_property(lux_override, "custom.fillOpacity", HOMEPAGE_SOLAR_FILL_OPACITY)
        upsert_override_property(lux_override, "custom.gradientMode", HOMEPAGE_SOLAR_GRADIENT_MODE)
        upsert_override_property(lux_override, "custom.hideFrom", {"legend": False, "tooltip": False, "viz": False})
        upsert_override_property(lux_override, "unit", "lux")

        forecast_override = override_for_label(panel, "Solar Forecast")
        upsert_override_property(forecast_override, "color", {"fixedColor": BRAND["gold"], "mode": "fixed"})
        upsert_override_property(forecast_override, "custom.lineWidth", 0)
        upsert_override_property(forecast_override, "custom.fillOpacity", HOMEPAGE_SOLAR_FILL_OPACITY)
        upsert_override_property(forecast_override, "custom.gradientMode", HOMEPAGE_SOLAR_GRADIENT_MODE)
        upsert_override_property(forecast_override, "custom.hideFrom", {"legend": True, "tooltip": False, "viz": False})
        upsert_override_property(forecast_override, "unit", "lux")

        threshold_base_override = override_for_label(panel, "Grow Light Threshold Base")
        upsert_override_property(threshold_base_override, "color", {"fixedColor": BRAND["leaf"], "mode": "fixed"})
        upsert_override_property(threshold_base_override, "custom.lineWidth", 0)
        upsert_override_property(threshold_base_override, "custom.fillOpacity", 0)
        upsert_override_property(threshold_base_override, "custom.gradientMode", "none")
        upsert_override_property(
            threshold_base_override,
            "custom.hideFrom",
            {"legend": True, "tooltip": True, "viz": False},
        )

        threshold_override = override_for_label(panel, "Grow Light Threshold")
        upsert_override_property(threshold_override, "color", {"fixedColor": BRAND["leaf"], "mode": "fixed"})
        upsert_override_property(threshold_override, "custom.lineWidth", 0)
        upsert_override_property(threshold_override, "custom.fillBelowTo", "Grow Light Threshold Base")
        upsert_override_property(threshold_override, "custom.fillOpacity", LIGHTING_THRESHOLD_BAND_FILL_OPACITY)
        upsert_override_property(threshold_override, "custom.gradientMode", "none")
        upsert_override_property(
            threshold_override,
            "custom.hideFrom",
            {"legend": False, "tooltip": True, "viz": False},
        )

    if title == "Per-Circuit Lighting Forecast Bands" and panel.get("type") == "timeseries":
        lux_override = override_for_label(panel, "Tempest/Forecast Lux")
        upsert_override_property(lux_override, "color", {"fixedColor": BRAND["gold"], "mode": "fixed"})
        upsert_override_property(lux_override, "custom.lineWidth", 0)
        upsert_override_property(lux_override, "custom.fillOpacity", DAYLIGHT_LUX_FILL_OPACITY)
        upsert_override_property(lux_override, "custom.gradientMode", DAYLIGHT_GRADIENT_MODE)

        for label in ("Main OFF Threshold", "Main ON Threshold", "Grow OFF Threshold", "Grow ON Threshold"):
            threshold_override = override_for_label(panel, label)
            upsert_override_property(threshold_override, "custom.lineWidth", 0)
            if "OFF Threshold" in label:
                upsert_override_property(threshold_override, "custom.fillOpacity", 22)

    if (
        title in {"Lighting Circuit State", "Lighting Decision Context", "Lighting Control State"}
        and panel.get("type") == "state-timeline"
    ):
        main_override = override_for_label(panel, "Grow Light Main")
        upsert_override_property(main_override, "color", {"fixedColor": BRAND["leaf"], "mode": "fixed"})
        upsert_override_property(main_override, "mappings", on_off_mapping(BRAND["leaf"]))

        grow_override = override_for_label(panel, "Grow Light Grow")
        upsert_override_property(grow_override, "color", {"fixedColor": BRAND["water"], "mode": "fixed"})
        upsert_override_property(grow_override, "mappings", on_off_mapping(BRAND["water"]))

        occupancy_override = override_for_label(panel, "Occupancy")
        upsert_override_property(occupancy_override, "color", {"fixedColor": BRAND["water"], "mode": "fixed"})
        upsert_override_property(occupancy_override, "mappings", lighting_occupancy_mapping())

        sun_override = override_for_label(panel, "Sun")
        upsert_override_property(sun_override, "color", {"fixedColor": BRAND["gold"], "mode": "fixed"})
        upsert_override_property(sun_override, "mappings", lighting_sun_mapping())

    if title == "VPD Compliance Band" and panel.get("type") == "timeseries":
        for target in panel.get("targets", []) or []:
            raw_sql = target.get("rawSql")
            if isinstance(raw_sql, str):
                target["rawSql"] = raw_sql.replace(
                    'AS "Actual Indoor VPD (30-sample rolling avg)"',
                    'AS "Actual Indoor VPD"',
                )

    if title == "VPD & Dew Point Spread" and panel.get("type") == "timeseries":
        for label in ("VPD (kPa)", "VPD Forecast"):
            override = override_for_label(panel, label)
            upsert_override_property(override, "unit", "pressurekpa")
            upsert_override_property(override, "custom.axisPlacement", "left")
            upsert_override_property(override, "custom.axisLabel", "kPa")
        for label in ("Dew Spread Observed (°F)", "Dew Spread Forecast (°F)"):
            override = override_for_label(panel, label)
            upsert_override_property(override, "unit", "fahrenheit")
            upsert_override_property(override, "custom.axisPlacement", "right")
            upsert_override_property(override, "custom.axisLabel", "°F")

    if title == "Soil Moisture vs Air VPD" and panel.get("type") == "timeseries":
        moisture_override = override_for_label(panel, "South 1 Moisture (%)")
        upsert_override_property(moisture_override, "color", {"fixedColor": BRAND["leaf"], "mode": "fixed"})
        upsert_override_property(moisture_override, "unit", "percent")
        upsert_override_property(moisture_override, "custom.axisPlacement", "left")
        upsert_override_property(moisture_override, "custom.axisLabel", "%")

        vpd_override = override_for_label(panel, "South Air VPD (kPa)")
        upsert_override_property(vpd_override, "color", {"fixedColor": BRAND["violet"], "mode": "fixed"})
        upsert_override_property(vpd_override, "unit", "pressurekpa")
        upsert_override_property(vpd_override, "custom.axisPlacement", "right")
        upsert_override_property(vpd_override, "custom.axisLabel", "kPa")

    if title == "Water, Air & Outdoor Temperature (7-Day Trend)" and panel.get("type") == "timeseries":
        series_styles = {
            "Water (°F)": (BRAND["water"], 2, None),
            "Air Indoor (°F)": (BRAND["leaf"], 2, None),
            "Outdoor (°F)": (BRAND["gray"], 1, {"fill": "dash", "dash": [6, 4]}),
        }
        for label, (color, width, line_style) in series_styles.items():
            override = override_for_label(panel, label)
            upsert_override_property(override, "color", {"fixedColor": color, "mode": "fixed"})
            upsert_override_property(override, "custom.lineWidth", width)
            if line_style:
                upsert_override_property(override, "custom.lineStyle", line_style)

    if title == "Wind Speed — Observed & Forecast" and panel.get("type") == "timeseries":
        series_styles = {
            "Speed": (BRAND["sky"], 2, None),
            "Gusts": (BRAND["gold"], 2, None),
            "Lulls": (BRAND["glass"], 1, None),
            "Forecast": (BRAND["gray"], 1, {"fill": "dash", "dash": [10, 5]}),
            "Forecast Gusts": (BRAND["gray"], 1, {"fill": "dash", "dash": [10, 5]}),
        }
        for label, (color, width, line_style) in series_styles.items():
            override = override_for_label(panel, label)
            upsert_override_property(override, "color", {"fixedColor": color, "mode": "fixed"})
            upsert_override_property(override, "custom.lineWidth", width)
            if line_style:
                upsert_override_property(override, "custom.lineStyle", line_style)

    if title == "Cloud Cover — Total, Low, High" and panel.get("type") == "timeseries":
        series_styles = {
            "Total": (BRAND["gray"], 2, None, 18),
            "Low": (BRAND["glass"], 1, None, 0),
            "High": (BRAND["slate"], 1, {"fill": "dash", "dash": [6, 4]}, 0),
        }
        for label, (color, width, line_style, fill_opacity) in series_styles.items():
            override = override_for_label(panel, label)
            upsert_override_property(override, "color", {"fixedColor": color, "mode": "fixed"})
            upsert_override_property(override, "custom.lineWidth", width)
            upsert_override_property(override, "custom.fillOpacity", fill_opacity)
            if line_style:
                upsert_override_property(override, "custom.lineStyle", line_style)

    if title == "30-Day Equipment Runtime Mix" and panel.get("type") == "timeseries":
        series_styles = {
            "Heat 1": BRAND["heat"],
            "Heat 2": BRAND["gas"],
            "Fan 1": BRAND["fan"],
            "Fan 2": "#5C6BC0",
            "Fog": BRAND["fog"],
            "Vent": BRAND["sky"],
            "Misters": BRAND["violet"],
        }
        for label, color in series_styles.items():
            override = override_for_label(panel, label)
            upsert_override_property(override, "color", {"fixedColor": color, "mode": "fixed"})

    if title == "Plan Accuracy by Day" and panel.get("type") == "barchart":
        options = panel.setdefault("options", {})
        options["xField"] = "Day"
        options["showValue"] = "never"
        options["xTickLabelRotation"] = 0
        options["xTickLabelSpacing"] = "auto"
        replace_first_target_sql(panel, PLAN_ACCURACY_SQL)

    if title == "Free Heap" and panel.get("type") == "stat":
        defaults = panel.setdefault("fieldConfig", {}).setdefault("defaults", {})
        defaults["unit"] = "suffix:kB"
        defaults["decimals"] = 1
        replace_first_target_sql(panel, FREE_HEAP_SQL)

    if title in RUNTIME_HOUR_STAT_TITLES and panel.get("type") == "stat":
        defaults = panel.setdefault("fieldConfig", {}).setdefault("defaults", {})
        defaults["unit"] = "suffix:h"
        defaults["decimals"] = 1

    if title == "pH & ORP (7-Day Trend)" and panel.get("type") == "timeseries":
        replace_target_sql(panel, "A", HYDRO_PH_SQL)
        replace_target_sql(panel, "B", HYDRO_ORP_SQL)
        ph_override = override_for_label(panel, "pH")
        upsert_override_property(ph_override, "color", {"mode": "fixed", "fixedColor": BRAND["gold"]})
        upsert_override_property(ph_override, "custom.axisPlacement", "left")
        upsert_override_property(ph_override, "custom.axisSoftMin", 4)
        upsert_override_property(ph_override, "custom.axisSoftMax", 8)
        orp_override = override_for_label(panel, "ORP (mV)")
        upsert_override_property(orp_override, "color", {"mode": "fixed", "fixedColor": BRAND["violet"]})
        upsert_override_property(orp_override, "unit", "mV")
        upsert_override_property(orp_override, "custom.axisPlacement", "right")

    if title == "Daily Water Usage" and panel.get("type") == "timeseries":
        replace_first_target_sql(panel, DAILY_WATER_USAGE_SQL)
        defaults = panel.setdefault("fieldConfig", {}).setdefault("defaults", {})
        defaults["unit"] = "gal"
        defaults["decimals"] = 0
        override = override_for_label(panel, "Water Used (gal)")
        upsert_override_property(override, "color", {"mode": "fixed", "fixedColor": BRAND["water"]})
        upsert_override_property(override, "custom.lineWidth", 0)
        upsert_override_property(override, "custom.fillOpacity", 80)

    if title == "Flow Rate (10min avg)" and panel.get("type") == "timeseries":
        replace_first_target_sql(panel, FLOW_RATE_SQL)
        defaults = panel.setdefault("fieldConfig", {}).setdefault("defaults", {})
        defaults["unit"] = "gal/min"
        defaults["decimals"] = 1
        override = override_for_label(panel, "Flow (gal/min)")
        upsert_override_property(override, "color", {"mode": "fixed", "fixedColor": BRAND["water"]})
        upsert_override_property(override, "custom.lineWidth", 2)
        upsert_override_property(override, "custom.fillOpacity", 8)

    if title == "Water Flow & Totalizer" and panel.get("type") == "timeseries":
        flow_override = override_for_label(panel, "Flow GPM")
        upsert_override_property(flow_override, "color", {"mode": "fixed", "fixedColor": BRAND["water"]})
        upsert_override_property(flow_override, "unit", "gal/min")
        upsert_override_property(flow_override, "custom.axisPlacement", "left")
        upsert_override_property(flow_override, "custom.axisLabel", "gal/min")

        total_override = override_for_label(panel, "Total gal")
        upsert_override_property(total_override, "color", {"mode": "fixed", "fixedColor": BRAND["sky"]})
        upsert_override_property(total_override, "unit", "gal")
        upsert_override_property(total_override, "custom.axisPlacement", "right")
        upsert_override_property(total_override, "custom.axisLabel", "gal")

    if title == "Mister Zone Runtime" and panel.get("type") == "bargauge":
        for label, color in {"South": "#CE93D8", "West": "#F48FB1", "Center": "#E040FB"}.items():
            override = override_for_label(panel, label)
            upsert_override_property(override, "color", {"mode": "fixed", "fixedColor": color})

    if panel.get("type") == "table" and title in PUBLIC_TABLE_SQL:
        replace_first_target_sql(panel, PUBLIC_TABLE_SQL[title])
        apply_table_widths(panel)


def brand_color_object(value: Any, label: str, context: str = "") -> Any:
    if not isinstance(value, dict):
        return value
    result = deepcopy(value)
    if result.get("mode") == "fixed":
        result["fixedColor"] = semantic_color(label, result.get("fixedColor"), context)
    return result


def brand_thresholds(value: Any, label: str, context: str = "") -> Any:
    if not isinstance(value, dict):
        return value
    result = deepcopy(value)
    for step in result.get("steps", []) or []:
        if "color" in step:
            step["color"] = normalize_threshold_color(step["color"])
    return result


def brand_mappings(value: Any, label: str, context: str = "") -> Any:
    if not isinstance(value, list):
        return value
    result = deepcopy(value)
    for mapping in result:
        options = mapping.get("options")
        if isinstance(options, dict):
            for key, option in options.items():
                if isinstance(option, dict) and "color" in option:
                    option["color"] = normalize_color_literal(
                        option["color"], f"{label} {option.get('text', key)}", context
                    )
    return result


def on_off_mapping(on_color: str, off_color: str | None = None) -> list[dict[str, Any]]:
    return [
        {
            "type": "value",
            "options": {
                "ON": {
                    "color": on_color,
                    "text": "ON",
                    "index": 0,
                }
            },
        },
        {
            "type": "value",
            "options": {
                "OFF": {
                    "color": off_color if off_color is not None else rgba_from(on_color, "0.18"),
                    "text": "",
                    "index": 1,
                }
            },
        },
    ]


def upsert_override_property(override: dict[str, Any], prop_id: str, value: Any) -> None:
    properties = override.setdefault("properties", [])
    for prop in properties:
        if prop.get("id") == prop_id:
            prop["value"] = value
            return
    properties.append({"id": prop_id, "value": value})


def remove_override_property(override: dict[str, Any], prop_id: str) -> None:
    override["properties"] = [prop for prop in override.setdefault("properties", []) if prop.get("id") != prop_id]


def display_label_for_override(override: dict[str, Any], fallback: str) -> str:
    for prop in override.get("properties", []) or []:
        if prop.get("id") == "displayName" and prop.get("value"):
            return str(prop["value"])
    matcher = override.get("matcher", {})
    return str(matcher.get("options") or fallback)


def override_for_label(panel: dict[str, Any], label: str) -> dict[str, Any]:
    overrides = panel.setdefault("fieldConfig", {}).setdefault("overrides", [])
    for override in overrides:
        matcher = override.get("matcher", {})
        if matcher.get("id") == "byName" and matcher.get("options") == label:
            return override
    override = {"matcher": {"id": "byName", "options": label}, "properties": []}
    overrides.append(override)
    return override


def rename_override_label(panel: dict[str, Any], old_label: str, new_label: str) -> None:
    for override in panel.setdefault("fieldConfig", {}).setdefault("overrides", []):
        matcher = override.get("matcher", {})
        if matcher.get("id") == "byName" and matcher.get("options") == old_label:
            matcher["options"] = new_label


def remove_override_labels(panel: dict[str, Any], labels: set[str]) -> None:
    overrides = panel.setdefault("fieldConfig", {}).setdefault("overrides", [])
    panel["fieldConfig"]["overrides"] = [
        override for override in overrides if override.get("matcher", {}).get("options") not in labels
    ]


def apply_table_widths(panel: dict[str, Any]) -> None:
    widths = PUBLIC_TABLE_WIDTHS.get(str(panel.get("title") or ""))
    if not widths:
        return
    for label, width in widths.items():
        override = override_for_label(panel, label)
        upsert_override_property(override, "custom.width", width)


def target_aliases(panel: dict[str, Any]) -> set[str]:
    aliases: set[str] = set()
    for target in panel.get("targets", []) or []:
        for key in ("alias", "aliasBy", "legendFormat", "refId"):
            value = target.get(key)
            if isinstance(value, str) and value.strip():
                aliases.add(value.strip())
        raw_sql = target.get("rawSql")
        if isinstance(raw_sql, str):
            aliases.update(match.strip() for match in re.findall(r'(?i)\bAS\s+"([^"]+)"', raw_sql))
    return aliases


def target_series_aliases(panel: dict[str, Any]) -> set[str]:
    aliases: set[str] = set()
    for target in panel.get("targets", []) or []:
        for key in ("alias", "aliasBy", "legendFormat"):
            value = target.get(key)
            if isinstance(value, str) and value.strip() and normalize_key(value) not in NON_SERIES_ALIASES:
                aliases.add(value.strip())
        raw_sql = target.get("rawSql")
        if isinstance(raw_sql, str):
            for match in re.findall(r'(?i)\bAS\s+"([^"]+)"', raw_sql):
                alias = match.strip()
                if alias and normalize_key(alias) not in NON_SERIES_ALIASES:
                    aliases.add(alias)
    return aliases


def daylight_labels(panel: dict[str, Any]) -> set[str]:
    title = str(panel.get("title") or "")
    labels = set(target_aliases(panel))
    for override in panel.get("fieldConfig", {}).get("overrides", []) or []:
        label = override.get("matcher", {}).get("options")
        if isinstance(label, str) and label.strip():
            labels.add(label.strip())
    return {label for label in labels if is_daylight_series(label, title)}


def strengthen_daylight_backdrops(panel: dict[str, Any], dashboard_uid: str = "") -> None:
    if panel.get("type") != "timeseries":
        return
    for label in sorted(daylight_labels(panel)):
        override = override_for_label(panel, label)
        upsert_override_property(
            override,
            "color",
            {"fixedColor": daylight_color(label, panel, dashboard_uid), "mode": "fixed"},
        )
        upsert_override_property(override, "custom.lineWidth", 0)
        upsert_override_property(override, "custom.fillOpacity", daylight_fill_opacity(label, panel, dashboard_uid))
        upsert_override_property(override, "custom.gradientMode", daylight_gradient_mode(label, panel, dashboard_uid))


def strengthen_vpd_context_series(panel: dict[str, Any]) -> None:
    if panel.get("type") != "timeseries":
        return
    for label in sorted(vpd_context_labels(panel)):
        override = override_for_label(panel, label)
        if is_outdoor_vpd_label(label):
            upsert_override_property(override, "color", {"fixedColor": BRAND["gray"], "mode": "fixed"})
            upsert_override_property(override, "custom.lineWidth", 1)
            if "forecast" in normalize_key(label):
                upsert_override_property(override, "custom.lineStyle", {"fill": "dash", "dash": [10, 5]})
        elif is_indoor_vpd_label(label):
            upsert_override_property(override, "color", {"fixedColor": BRAND["violet"], "mode": "fixed"})
            upsert_override_property(override, "custom.lineWidth", 2)


def strengthen_expected_series_colors(panel: dict[str, Any]) -> None:
    if panel.get("type") not in {"timeseries", "barchart", "histogram", "piechart", "state-timeline"}:
        return
    aliases = target_aliases(panel)
    for label, color in EXPECTED_SERIES_COLORS.items():
        if label not in aliases and not override_props(panel, label):
            continue
        override = override_for_label(panel, label)
        upsert_override_property(override, "color", {"fixedColor": color, "mode": "fixed"})


def strengthen_compliance_band(panel: dict[str, Any]) -> None:
    if panel.get("type") != "timeseries":
        return
    aliases = target_aliases(panel)
    if "Compliant High" not in aliases and not override_props(panel, "Compliant High"):
        return
    if "Compliant Low" not in aliases and not override_props(panel, "Compliant Low"):
        return

    high_override = override_for_label(panel, "Compliant High")
    upsert_override_property(high_override, "color", {"fixedColor": COMPLIANCE_BAND_COLOR, "mode": "fixed"})
    upsert_override_property(high_override, "custom.lineWidth", 0)
    upsert_override_property(high_override, "custom.fillBelowTo", "Compliant Low")
    upsert_override_property(high_override, "custom.fillOpacity", COMPLIANCE_BAND_FILL_OPACITY)
    upsert_override_property(high_override, "custom.gradientMode", "none")
    upsert_override_property(high_override, "custom.hideFrom", {"legend": True, "tooltip": True, "viz": False})

    low_override = override_for_label(panel, "Compliant Low")
    upsert_override_property(low_override, "color", {"fixedColor": COMPLIANCE_BAND_COLOR, "mode": "fixed"})
    upsert_override_property(low_override, "custom.lineWidth", 0)
    upsert_override_property(low_override, "custom.fillOpacity", 0)
    upsert_override_property(low_override, "custom.gradientMode", "none")
    upsert_override_property(low_override, "custom.hideFrom", {"legend": True, "tooltip": True, "viz": False})


def strengthen_lighting_threshold_bands(panel: dict[str, Any]) -> None:
    if panel.get("type") != "timeseries":
        return
    aliases = target_aliases(panel)
    for low_label, high_label, color in LIGHTING_THRESHOLD_BANDS:
        has_low = low_label in aliases or bool(override_props(panel, low_label))
        has_high = high_label in aliases or bool(override_props(panel, high_label))
        if not (has_low and has_high):
            continue

        high_override = override_for_label(panel, high_label)
        upsert_override_property(high_override, "color", {"fixedColor": color, "mode": "fixed"})
        upsert_override_property(high_override, "custom.lineWidth", 0)
        upsert_override_property(high_override, "custom.fillBelowTo", low_label)
        upsert_override_property(high_override, "custom.fillOpacity", LIGHTING_THRESHOLD_BAND_FILL_OPACITY)
        upsert_override_property(high_override, "custom.gradientMode", "none")
        remove_override_property(high_override, "custom.lineStyle")

        low_override = override_for_label(panel, low_label)
        upsert_override_property(low_override, "color", {"fixedColor": color, "mode": "fixed"})
        upsert_override_property(low_override, "custom.lineWidth", 0)
        upsert_override_property(low_override, "custom.fillOpacity", 0)
        upsert_override_property(low_override, "custom.gradientMode", "none")
        remove_override_property(low_override, "custom.lineStyle")


def strengthen_relay_state_lanes(panel: dict[str, Any], dashboard_uid: str = "") -> None:
    if panel.get("type") != "timeseries":
        return
    fill_opacity = relay_state_fill_opacity(dashboard_uid)
    for label, base_label in relay_state_lane_pairs(panel):
        state_override = override_for_label(panel, label)
        upsert_override_property(state_override, "custom.drawStyle", "line")
        upsert_override_property(state_override, "custom.lineInterpolation", "stepAfter")
        upsert_override_property(state_override, "custom.lineWidth", RELAY_STATE_LINE_WIDTH)
        upsert_override_property(state_override, "custom.fillBelowTo", base_label)
        upsert_override_property(state_override, "custom.fillOpacity", fill_opacity)
        upsert_override_property(state_override, "custom.spanNulls", False)
        upsert_override_property(state_override, "custom.showPoints", "never")
        upsert_override_property(state_override, "custom.pointSize", 0)
        upsert_override_property(state_override, "custom.hideFrom", {"legend": False, "tooltip": True, "viz": False})
        remove_override_property(state_override, "custom.gradientMode")
        remove_override_property(state_override, "custom.lineStyle")

        base_override = override_for_label(panel, base_label)
        upsert_override_property(base_override, "custom.drawStyle", "line")
        upsert_override_property(base_override, "custom.lineWidth", 0)
        upsert_override_property(base_override, "custom.fillOpacity", 0)
        upsert_override_property(base_override, "custom.showPoints", "never")
        upsert_override_property(base_override, "custom.pointSize", 0)
        upsert_override_property(base_override, "custom.hideFrom", {"legend": True, "tooltip": True, "viz": False})
        remove_override_property(base_override, "custom.gradientMode")
        remove_override_property(base_override, "custom.lineStyle")


def strengthen_lighting_lux_altitude(panel: dict[str, Any]) -> None:
    if panel.get("type") != "timeseries":
        return
    aliases = set(target_aliases(panel))
    for override in panel.get("fieldConfig", {}).get("overrides", []) or []:
        label = override.get("matcher", {}).get("options")
        if isinstance(label, str) and label.strip():
            aliases.add(label.strip())

    for label in sorted(aliases):
        label_text = normalize_key(label)
        if "indoor lux" in label_text:
            override = override_for_label(panel, label)
            upsert_override_property(override, "color", {"fixedColor": BRAND["gold"], "mode": "fixed"})
            upsert_override_property(override, "custom.lineWidth", 0)
            upsert_override_property(override, "custom.fillOpacity", INDOOR_LUX_FILL_OPACITY)
            upsert_override_property(override, "custom.gradientMode", DAYLIGHT_GRADIENT_MODE)
        elif "outdoor lux" in label_text:
            override = override_for_label(panel, label)
            upsert_override_property(override, "color", {"fixedColor": BRAND["gold"], "mode": "fixed"})
            upsert_override_property(override, "custom.lineWidth", 0)
            upsert_override_property(override, "custom.fillOpacity", OUTDOOR_LUX_FILL_OPACITY)
            upsert_override_property(override, "custom.gradientMode", DAYLIGHT_GRADIENT_MODE)
        elif "altitude" in label_text and any(term in label_text for term in ("sun", "solar", "altitude")):
            override = override_for_label(panel, label)
            upsert_override_property(override, "color", {"fixedColor": BRAND["gold"], "mode": "fixed"})
            upsert_override_property(override, "custom.lineWidth", 2)
            upsert_override_property(override, "custom.fillOpacity", 0)
            upsert_override_property(override, "custom.gradientMode", "none")
            upsert_override_property(override, "custom.lineStyle", {"fill": "dash", "dash": [6, 4]})


def lighting_occupancy_mapping() -> list[dict[str, Any]]:
    return [
        {
            "type": "value",
            "options": {
                "occupied": {"color": BRAND["water"], "text": "Occupied", "index": 0},
                "empty": {"color": rgba_from(BRAND["gold"], "0.30"), "text": "", "index": 1},
            },
        }
    ]


def lighting_sun_mapping() -> list[dict[str, Any]]:
    return [
        {
            "type": "value",
            "options": {
                "Day": {"color": BRAND["gold"], "text": "Day", "index": 0},
                "Night": {"color": BRAND["navy"], "text": "Night", "index": 1},
            },
        }
    ]


def brand_field_config(panel: dict[str, Any], dashboard_uid: str = "") -> None:
    title = str(panel.get("title") or "")
    field_config = panel.setdefault("fieldConfig", {})
    defaults = field_config.setdefault("defaults", {})

    if panel.get("type") == "stat" and title in EXPECTED_STAT_COLORS:
        defaults["color"] = {"mode": "fixed", "fixedColor": EXPECTED_STAT_COLORS[title]}
    elif "color" in defaults:
        defaults["color"] = brand_color_object(defaults["color"], title)
    elif panel.get("type") == "stat":
        defaults["color"] = {"mode": "fixed", "fixedColor": semantic_color(title)}

    if "thresholds" in defaults:
        defaults["thresholds"] = brand_thresholds(defaults["thresholds"], title)
    if "mappings" in defaults:
        defaults["mappings"] = brand_mappings(defaults["mappings"], title)

    for override in field_config.get("overrides", []) or []:
        label = display_label_for_override(override, title)
        for prop in override.get("properties", []) or []:
            if prop.get("id") == "color":
                prop["value"] = brand_color_object(prop.get("value"), label, title)
            elif prop.get("id") == "thresholds":
                prop["value"] = brand_thresholds(prop.get("value"), label, title)
            elif prop.get("id") == "mappings":
                prop["value"] = brand_mappings(prop.get("value"), label, title)
    strengthen_daylight_backdrops(panel, dashboard_uid)
    strengthen_vpd_context_series(panel)
    strengthen_lighting_lux_altitude(panel)
    strengthen_expected_series_colors(panel)
    strengthen_compliance_band(panel)
    strengthen_lighting_threshold_bands(panel)
    strengthen_relay_state_lanes(panel, dashboard_uid)
    strengthen_homepage_lighting_lanes(panel)
    strengthen_homepage_target_lines(panel)
    if panel.get("type") == "state-timeline" and title in {
        "Lighting Circuit State",
        "Lighting Decision Context",
        "Lighting Control State",
    }:
        occupancy_override = override_for_label(panel, "Occupancy")
        upsert_override_property(occupancy_override, "color", {"fixedColor": BRAND["water"], "mode": "fixed"})
        upsert_override_property(occupancy_override, "mappings", lighting_occupancy_mapping())

        sun_override = override_for_label(panel, "Sun")
        upsert_override_property(sun_override, "color", {"fixedColor": BRAND["gold"], "mode": "fixed"})
        upsert_override_property(sun_override, "mappings", lighting_sun_mapping())


def brand_dashboard(path: Path, embedded_ids: set[int]) -> tuple[bool, int]:
    dashboard = load_json(path)
    dashboard_uid = str(dashboard.get("uid") or "")
    before = json.dumps(dashboard, sort_keys=True)
    dashboard["style"] = "light"
    changed_panels = 0
    for panel in iter_panels(dashboard.get("panels", [])):
        strengthen_daylight_backdrops(panel, dashboard_uid)
        strengthen_lighting_lux_altitude(panel)
        strengthen_compliance_band(panel)
        strengthen_lighting_threshold_bands(panel)
        strengthen_relay_state_lanes(panel, dashboard_uid)
        normalize_runtime_electric_sources(panel)
        if panel.get("id") not in embedded_ids:
            continue
        normalize_public_panel_schema(panel)
        ensure_panel_chrome(panel)
        ensure_legend_and_tooltip(panel)
        brand_field_config(panel, dashboard_uid)
        changed_panels += 1
    after = json.dumps(dashboard, sort_keys=True)
    if after != before:
        path.write_text(json.dumps(dashboard, indent=2, sort_keys=False) + "\n", encoding="utf-8")
        return True, changed_panels
    return False, changed_panels


def color_values(panel: dict[str, Any]) -> list[str]:
    values: list[str] = []
    defaults = panel.get("fieldConfig", {}).get("defaults", {})
    color = defaults.get("color")
    if isinstance(color, dict) and color.get("mode") == "fixed" and color.get("fixedColor"):
        values.append(str(color["fixedColor"]))
    for step in (defaults.get("thresholds") or {}).get("steps", []) or []:
        if step.get("color"):
            values.append(str(step["color"]))
    for override in panel.get("fieldConfig", {}).get("overrides", []) or []:
        for prop in override.get("properties", []) or []:
            value = prop.get("value")
            if prop.get("id") == "color" and isinstance(value, dict) and value.get("fixedColor"):
                values.append(str(value["fixedColor"]))
            if prop.get("id") == "thresholds" and isinstance(value, dict):
                for step in value.get("steps", []) or []:
                    if step.get("color"):
                        values.append(str(step["color"]))
    return values


def override_props(panel: dict[str, Any], label: str) -> dict[str, Any]:
    for override in panel.get("fieldConfig", {}).get("overrides", []) or []:
        matcher = override.get("matcher", {})
        if matcher.get("id") != "byName" or matcher.get("options") != label:
            continue
        return {prop.get("id"): prop.get("value") for prop in override.get("properties", []) or []}
    return {}


def mapped_value_color(panel: dict[str, Any], label: str, value: str) -> str | None:
    mappings = override_props(panel, label).get("mappings")
    if not isinstance(mappings, list):
        return None
    for mapping in mappings:
        options = mapping.get("options") if isinstance(mapping, dict) else None
        if isinstance(options, dict):
            option = options.get(value)
            if isinstance(option, dict):
                color = option.get("color")
                return str(color) if color is not None else None
    return None


def check_daylight_dashboard_data(label: str, dashboard: dict[str, Any]) -> list[str]:
    findings: list[str] = []
    dashboard_uid = str(dashboard.get("uid") or "")
    for panel in iter_panels(dashboard.get("panels", [])):
        if panel.get("type") != "timeseries":
            continue
        for series in sorted(daylight_labels(panel)):
            props = override_props(panel, series)
            color = props.get("color")
            fixed_color = color.get("fixedColor") if isinstance(color, dict) else None
            expected_color = daylight_color(series, panel, dashboard_uid)
            expected_fill = daylight_fill_opacity(series, panel, dashboard_uid)
            expected_gradient = daylight_gradient_mode(series, panel, dashboard_uid)
            if fixed_color != expected_color:
                findings.append(
                    f"{label}: panel {panel.get('id')} {panel.get('title')!r} daylight series "
                    f"{series!r} color is {fixed_color}, expected {expected_color}"
                )
            if props.get("custom.lineWidth") != 0:
                findings.append(
                    f"{label}: panel {panel.get('id')} {panel.get('title')!r} daylight series "
                    f"{series!r} lineWidth is {props.get('custom.lineWidth')}, expected 0"
                )
            fill_opacity = props.get("custom.fillOpacity")
            if not isinstance(fill_opacity, (int, float)) or fill_opacity < expected_fill:
                findings.append(
                    f"{label}: panel {panel.get('id')} {panel.get('title')!r} daylight series "
                    f"{series!r} fillOpacity is {fill_opacity}, expected >= {expected_fill}"
                )
            if props.get("custom.gradientMode") != expected_gradient:
                findings.append(
                    f"{label}: panel {panel.get('id')} {panel.get('title')!r} daylight series "
                    f"{series!r} gradientMode is {props.get('custom.gradientMode')}, expected "
                    f"{expected_gradient}"
                )
    return findings


def check_compliance_dashboard_data(label: str, dashboard: dict[str, Any]) -> list[str]:
    findings: list[str] = []
    for panel in iter_panels(dashboard.get("panels", [])):
        if panel.get("type") != "timeseries":
            continue
        aliases = target_aliases(panel)
        if "Compliant High" not in aliases and not override_props(panel, "Compliant High"):
            continue
        if "Compliant Low" not in aliases and not override_props(panel, "Compliant Low"):
            continue
        expected = {
            "Compliant High": {
                "custom.lineWidth": 0,
                "custom.fillBelowTo": "Compliant Low",
                "custom.fillOpacity": COMPLIANCE_BAND_FILL_OPACITY,
                "custom.gradientMode": "none",
            },
            "Compliant Low": {
                "custom.lineWidth": 0,
                "custom.fillOpacity": 0,
                "custom.gradientMode": "none",
            },
        }
        for series, expected_props in expected.items():
            props = override_props(panel, series)
            color = props.get("color")
            fixed_color = color.get("fixedColor") if isinstance(color, dict) else None
            if fixed_color != COMPLIANCE_BAND_COLOR:
                findings.append(
                    f"{label}: panel {panel.get('id')} {panel.get('title')!r} compliance series "
                    f"{series!r} color is {fixed_color}, expected {COMPLIANCE_BAND_COLOR}"
                )
            for prop_id, expected_value in expected_props.items():
                if props.get(prop_id) != expected_value:
                    findings.append(
                        f"{label}: panel {panel.get('id')} {panel.get('title')!r} compliance series "
                        f"{series!r} {prop_id} is {props.get(prop_id)}, expected {expected_value}"
                    )
    return findings


def check_lighting_threshold_dashboard_data(label: str, dashboard: dict[str, Any]) -> list[str]:
    findings: list[str] = []
    for panel in iter_panels(dashboard.get("panels", [])):
        if panel.get("type") != "timeseries":
            continue
        aliases = target_aliases(panel)
        for low_label, high_label, expected_color in LIGHTING_THRESHOLD_BANDS:
            has_low = low_label in aliases or bool(override_props(panel, low_label))
            has_high = high_label in aliases or bool(override_props(panel, high_label))
            if not (has_low and has_high):
                continue
            homepage_lighting_threshold = str(
                panel.get("title") or ""
            ) == "Lighting: Overhead vs Grow Circuit — Lux, Thresholds & Switch State" and {low_label, high_label} <= {
                "Overhead Threshold Base",
                "Overhead Threshold",
                "Grow Threshold Base",
                "Grow Threshold",
            }
            expected_line_width = HOMEPAGE_LIGHTING_THRESHOLD_LINE_WIDTH if homepage_lighting_threshold else 0
            expected_line_style = (
                HOMEPAGE_LIGHTING_GROW_THRESHOLD_LINE_STYLE
                if homepage_lighting_threshold and low_label == "Grow Threshold Base"
                else None
            )
            expected = {
                high_label: {
                    "custom.lineWidth": expected_line_width,
                    "custom.fillBelowTo": low_label,
                    "custom.fillOpacity": LIGHTING_THRESHOLD_BAND_FILL_OPACITY,
                    "custom.gradientMode": "none",
                },
                low_label: {
                    "custom.lineWidth": expected_line_width,
                    "custom.fillOpacity": 0,
                    "custom.gradientMode": "none",
                },
            }
            for series, expected_props in expected.items():
                props = override_props(panel, series)
                color = props.get("color")
                fixed_color = color.get("fixedColor") if isinstance(color, dict) else None
                if fixed_color != expected_color:
                    findings.append(
                        f"{label}: panel {panel.get('id')} {panel.get('title')!r} lighting threshold "
                        f"{series!r} color is {fixed_color}, expected {expected_color}"
                    )
                if props.get("custom.lineStyle") != expected_line_style:
                    findings.append(
                        f"{label}: panel {panel.get('id')} {panel.get('title')!r} lighting threshold "
                        f"{series!r} lineStyle is {props.get('custom.lineStyle')}, expected {expected_line_style}"
                    )
                for prop_id, expected_value in expected_props.items():
                    if props.get(prop_id) != expected_value:
                        findings.append(
                            f"{label}: panel {panel.get('id')} {panel.get('title')!r} lighting threshold "
                            f"{series!r} {prop_id} is {props.get(prop_id)}, expected {expected_value}"
                        )
    return findings


def check_relay_state_lane_dashboard_data(label: str, dashboard: dict[str, Any]) -> list[str]:
    findings: list[str] = []
    fill_opacity = relay_state_fill_opacity(str(dashboard.get("uid") or ""))
    default_expected_state_props = {
        "custom.drawStyle": "line",
        "custom.lineInterpolation": "stepAfter",
        "custom.lineWidth": RELAY_STATE_LINE_WIDTH,
        "custom.fillOpacity": fill_opacity,
        "custom.spanNulls": False,
        "custom.showPoints": "never",
        "custom.pointSize": 0,
        "custom.hideFrom": {"legend": False, "tooltip": True, "viz": False},
    }
    expected_base_props = {
        "custom.drawStyle": "line",
        "custom.lineWidth": 0,
        "custom.fillOpacity": 0,
        "custom.showPoints": "never",
        "custom.pointSize": 0,
        "custom.hideFrom": {"legend": True, "tooltip": True, "viz": False},
    }

    for panel in iter_panels(dashboard.get("panels", [])):
        for series, base_series in relay_state_lane_pairs(panel):
            expected_state_props = dict(default_expected_state_props)
            expected_gradient = None
            if str(
                panel.get("title") or ""
            ) == "Lighting: Overhead vs Grow Circuit — Lux, Thresholds & Switch State" and series in {
                "Overhead Light",
                "Grow Light",
            }:
                expected_state_props["custom.fillOpacity"] = HOMEPAGE_LIGHTING_STATE_FILL_OPACITY
                expected_state_props["custom.lineWidth"] = HOMEPAGE_LIGHTING_STATE_LINE_WIDTH
                expected_gradient = "opacity"
            props = override_props(panel, series)
            base_props = override_props(panel, base_series)
            if props.get("custom.fillBelowTo") != base_series:
                findings.append(
                    f"{label}: panel {panel.get('id')} {panel.get('title')!r} relay state "
                    f"{series!r} fillBelowTo is {props.get('custom.fillBelowTo')}, expected {base_series!r}"
                )
            if props.get("custom.gradientMode") != expected_gradient:
                findings.append(
                    f"{label}: panel {panel.get('id')} {panel.get('title')!r} relay state "
                    f"{series!r} gradientMode is {props.get('custom.gradientMode')}, expected {expected_gradient}"
                )
            if props.get("custom.lineStyle") is not None:
                findings.append(
                    f"{label}: panel {panel.get('id')} {panel.get('title')!r} relay state "
                    f"{series!r} still has lineStyle {props.get('custom.lineStyle')}"
                )
            for prop_id, expected_value in expected_state_props.items():
                if props.get(prop_id) != expected_value:
                    findings.append(
                        f"{label}: panel {panel.get('id')} {panel.get('title')!r} relay state "
                        f"{series!r} {prop_id} is {props.get(prop_id)}, expected {expected_value}"
                    )
            if not base_props:
                findings.append(
                    f"{label}: panel {panel.get('id')} {panel.get('title')!r} relay state base "
                    f"{base_series!r} has no override"
                )
                continue
            if base_props.get("custom.gradientMode") is not None:
                findings.append(
                    f"{label}: panel {panel.get('id')} {panel.get('title')!r} relay state base "
                    f"{base_series!r} still has gradientMode {base_props.get('custom.gradientMode')}"
                )
            if base_props.get("custom.lineStyle") is not None:
                findings.append(
                    f"{label}: panel {panel.get('id')} {panel.get('title')!r} relay state base "
                    f"{base_series!r} still has lineStyle {base_props.get('custom.lineStyle')}"
                )
            for prop_id, expected_value in expected_base_props.items():
                if base_props.get(prop_id) != expected_value:
                    findings.append(
                        f"{label}: panel {panel.get('id')} {panel.get('title')!r} relay state base "
                        f"{base_series!r} {prop_id} is {base_props.get(prop_id)}, expected {expected_value}"
                    )
    return findings


def check_vpd_context_panel_data(label: str, panel: dict[str, Any]) -> list[str]:
    findings: list[str] = []
    for series in sorted(vpd_context_labels(panel)):
        props = override_props(panel, series)
        color = props.get("color")
        fixed_color = color.get("fixedColor") if isinstance(color, dict) else None
        if is_outdoor_vpd_label(series):
            if fixed_color != BRAND["gray"]:
                findings.append(
                    f"{label}: panel {panel.get('id')} {panel.get('title')!r} outdoor VPD series "
                    f"{series!r} color is {fixed_color}, expected {BRAND['gray']}"
                )
            if props.get("custom.lineWidth") != 1:
                findings.append(
                    f"{label}: panel {panel.get('id')} {panel.get('title')!r} outdoor VPD series "
                    f"{series!r} lineWidth is {props.get('custom.lineWidth')}, expected 1"
                )
            if "forecast" in normalize_key(series) and props.get("custom.lineStyle") != {
                "fill": "dash",
                "dash": [10, 5],
            }:
                findings.append(
                    f"{label}: panel {panel.get('id')} {panel.get('title')!r} outdoor VPD forecast series "
                    f"{series!r} is not dashed gray context"
                )
        elif is_indoor_vpd_label(series):
            if fixed_color != BRAND["violet"]:
                findings.append(
                    f"{label}: panel {panel.get('id')} {panel.get('title')!r} indoor VPD series "
                    f"{series!r} color is {fixed_color}, expected {BRAND['violet']}"
                )
            if props.get("custom.lineWidth") != 2:
                findings.append(
                    f"{label}: panel {panel.get('id')} {panel.get('title')!r} indoor VPD series "
                    f"{series!r} lineWidth is {props.get('custom.lineWidth')}, expected 2"
                )
    return findings


def check_series_style(
    audit_label: str,
    panel: dict[str, Any],
    series: str,
    expected_color: str,
    *,
    expected_line_width: int | None = None,
    expected_line_style: dict[str, Any] | None = None,
) -> list[str]:
    findings: list[str] = []
    props = override_props(panel, series)
    color = props.get("color")
    fixed_color = color.get("fixedColor") if isinstance(color, dict) else None
    if fixed_color != expected_color:
        findings.append(
            f"{audit_label}: panel {panel.get('id')} {panel.get('title')!r} series {series!r} "
            f"color is {fixed_color}, expected {expected_color}"
        )
    if expected_line_width is not None and props.get("custom.lineWidth") != expected_line_width:
        findings.append(
            f"{audit_label}: panel {panel.get('id')} {panel.get('title')!r} series {series!r} "
            f"lineWidth is {props.get('custom.lineWidth')}, expected {expected_line_width}"
        )
    if expected_line_style is not None and props.get("custom.lineStyle") != expected_line_style:
        findings.append(
            f"{audit_label}: panel {panel.get('id')} {panel.get('title')!r} series {series!r} "
            "does not use the expected line style"
        )
    return findings


def check_public_series_schema(label: str, panel: dict[str, Any]) -> list[str]:
    findings: list[str] = []
    title = str(panel.get("title") or "")
    if panel.get("type") == "state-timeline" and title in {
        "Lighting Circuit State",
        "Lighting Decision Context",
        "Lighting Control State",
    }:
        for series, expected_color in {"Grow Light Main": BRAND["leaf"], "Grow Light Grow": BRAND["water"]}.items():
            if series not in target_aliases(panel) and not override_props(panel, series):
                continue
            if mapped_value_color(panel, series, "ON") != expected_color:
                findings.append(
                    f"{label}: panel {panel.get('id')} {title!r} state series {series!r} ON mapping "
                    f"is {mapped_value_color(panel, series, 'ON')}, expected {expected_color}"
                )
        if "Occupancy" in target_aliases(panel) or override_props(panel, "Occupancy"):
            if mapped_value_color(panel, "Occupancy", "occupied") != BRAND["water"]:
                findings.append(
                    f"{label}: panel {panel.get('id')} {title!r} Occupancy occupied mapping "
                    f"is {mapped_value_color(panel, 'Occupancy', 'occupied')}, expected {BRAND['water']}"
                )
        if "Sun" in target_aliases(panel) or override_props(panel, "Sun"):
            if mapped_value_color(panel, "Sun", "Day") != BRAND["gold"]:
                findings.append(
                    f"{label}: panel {panel.get('id')} {title!r} Sun Day mapping "
                    f"is {mapped_value_color(panel, 'Sun', 'Day')}, expected {BRAND['gold']}"
                )
            if mapped_value_color(panel, "Sun", "Night") != BRAND["navy"]:
                findings.append(
                    f"{label}: panel {panel.get('id')} {title!r} Sun Night mapping "
                    f"is {mapped_value_color(panel, 'Sun', 'Night')}, expected {BRAND['navy']}"
                )
    if panel.get("type") == "timeseries":
        for series in target_aliases(panel):
            series_text = normalize_key(series)
            if "indoor lux" in series_text:
                props = override_props(panel, series)
                if props.get("custom.fillOpacity") != INDOOR_LUX_FILL_OPACITY:
                    findings.append(
                        f"{label}: panel {panel.get('id')} {title!r} Indoor Lux fillOpacity is "
                        f"{props.get('custom.fillOpacity')}, expected {INDOOR_LUX_FILL_OPACITY}"
                    )
            if "outdoor lux" in series_text:
                props = override_props(panel, series)
                if props.get("custom.fillOpacity") != OUTDOOR_LUX_FILL_OPACITY:
                    findings.append(
                        f"{label}: panel {panel.get('id')} {title!r} Outdoor Lux fillOpacity is "
                        f"{props.get('custom.fillOpacity')}, expected {OUTDOOR_LUX_FILL_OPACITY}"
                    )
            if "altitude" in series_text and any(term in series_text for term in ("sun", "solar", "altitude")):
                props = override_props(panel, series)
                if props.get("custom.fillOpacity") != 0 or props.get("custom.lineStyle") != {
                    "fill": "dash",
                    "dash": [6, 4],
                }:
                    findings.append(
                        f"{label}: panel {panel.get('id')} {title!r} altitude series {series!r} "
                        "is not a dashed no-fill line"
                    )
    if panel.get("type") == "timeseries" and title == "Water, Air & Outdoor Temperature (7-Day Trend)":
        findings.extend(check_series_style(label, panel, "Water (°F)", BRAND["water"], expected_line_width=2))
        findings.extend(check_series_style(label, panel, "Air Indoor (°F)", BRAND["leaf"], expected_line_width=2))
        findings.extend(
            check_series_style(
                label,
                panel,
                "Outdoor (°F)",
                BRAND["gray"],
                expected_line_width=1,
                expected_line_style={"fill": "dash", "dash": [6, 4]},
            )
        )
    if panel.get("type") == "timeseries" and title == "Wind Speed — Observed & Forecast":
        expected = {
            "Speed": (BRAND["sky"], 2, None),
            "Gusts": (BRAND["gold"], 2, None),
            "Lulls": (BRAND["glass"], 1, None),
            "Forecast": (BRAND["gray"], 1, {"fill": "dash", "dash": [10, 5]}),
            "Forecast Gusts": (BRAND["gray"], 1, {"fill": "dash", "dash": [10, 5]}),
        }
        for series, (color, width, line_style) in expected.items():
            findings.extend(
                check_series_style(
                    label,
                    panel,
                    series,
                    color,
                    expected_line_width=width,
                    expected_line_style=line_style,
                )
            )
    if panel.get("type") == "timeseries" and title == "Cloud Cover — Total, Low, High":
        expected = {
            "Total": (BRAND["gray"], 2, None),
            "Low": (BRAND["glass"], 1, None),
            "High": (BRAND["slate"], 1, {"fill": "dash", "dash": [6, 4]}),
        }
        for series, (color, width, line_style) in expected.items():
            findings.extend(
                check_series_style(
                    label,
                    panel,
                    series,
                    color,
                    expected_line_width=width,
                    expected_line_style=line_style,
                )
            )
    if panel.get("type") == "timeseries":
        for series, color in {"Fog": BRAND["fog"], "Fan 1": BRAND["fan"]}.items():
            if series in target_aliases(panel) or override_props(panel, series):
                findings.extend(check_series_style(label, panel, series, color))
    return findings


def check_explicit_graph_series_colors(label: str, panel: dict[str, Any]) -> list[str]:
    if panel.get("type") not in GRAPH_PANEL_TYPES:
        return []
    findings: list[str] = []
    for series in sorted(target_series_aliases(panel)):
        color = override_props(panel, series).get("color")
        if not (isinstance(color, dict) and color.get("mode") == "fixed" and color.get("fixedColor")):
            findings.append(
                f"{label}: panel {panel.get('id')} {panel.get('title')!r} graph series {series!r} "
                "has no explicit fixed color override"
            )
    return findings


def check_dashboard_data(label: str, dashboard: dict[str, Any], embedded_ids: set[int]) -> list[str]:
    findings: list[str] = []
    if dashboard.get("style") not in {"light", "dark"}:
        findings.append(f"{label}: dashboard style is not light/dark")
    findings.extend(check_daylight_dashboard_data(label, dashboard))
    findings.extend(check_compliance_dashboard_data(label, dashboard))
    findings.extend(check_lighting_threshold_dashboard_data(label, dashboard))
    findings.extend(check_relay_state_lane_dashboard_data(label, dashboard))
    found_ids = set()
    for panel in iter_panels(dashboard.get("panels", [])):
        if panel.get("id") not in embedded_ids:
            continue
        found_ids.add(panel["id"])
        findings.extend(check_explicit_graph_series_colors(label, panel))
        if panel.get("transparent") is not True:
            findings.append(f"{label}: panel {panel['id']} {panel.get('title')!r} is not transparent")
        if panel.get("type") == "stat" and panel.get("options", {}).get("colorMode", "").startswith("background"):
            findings.append(f"{label}: panel {panel['id']} {panel.get('title')!r} still uses stat background color")
        if panel.get("type") == "stat" and panel.get("options", {}).get("graphMode") != "none":
            findings.append(
                f"{label}: panel {panel['id']} {panel.get('title')!r} stat graphMode is "
                f"{panel.get('options', {}).get('graphMode')}, expected none"
            )
        if panel.get("type") == "stat" and panel.get("title") in EXPECTED_STAT_COLORS:
            expected_color = EXPECTED_STAT_COLORS[str(panel.get("title"))]
            color = panel.get("fieldConfig", {}).get("defaults", {}).get("color", {})
            fixed_color = color.get("fixedColor") if isinstance(color, dict) else None
            if fixed_color != expected_color:
                findings.append(
                    f"{label}: panel {panel['id']} {panel.get('title')!r} stat color is "
                    f"{fixed_color}, expected {expected_color}"
                )
        if panel.get("type") == "table":
            options = panel.get("options", {})
            title = str(panel.get("title") or "")
            if options.get("showHeader") is not True:
                findings.append(f"{label}: panel {panel['id']} {panel.get('title')!r} table header is not enabled")
            if options.get("cellHeight") != "sm":
                findings.append(
                    f"{label}: panel {panel['id']} {panel.get('title')!r} table cellHeight is "
                    f"{options.get('cellHeight')}, expected sm"
                )
            if options.get("footer", {}).get("show") is not False:
                findings.append(f"{label}: panel {panel['id']} {panel.get('title')!r} table footer is not disabled")
            custom = panel.get("fieldConfig", {}).get("defaults", {}).get("custom", {})
            if custom.get("cellOptions", {}).get("type") != "auto":
                findings.append(f"{label}: panel {panel['id']} {panel.get('title')!r} table cellOptions are not auto")
            if title in PUBLIC_TABLE_SQL:
                raw_sql = next(
                    (
                        target.get("rawSql")
                        for target in panel.get("targets", []) or []
                        if isinstance(target, dict) and target.get("rawSql") is not None
                    ),
                    "",
                )
                if raw_sql != PUBLIC_TABLE_SQL[title]:
                    findings.append(f"{label}: panel {panel['id']} {title!r} does not use the public table schema")
            expected_widths = PUBLIC_TABLE_WIDTHS.get(title, {})
            for column, width in expected_widths.items():
                actual = override_props(panel, column).get("custom.width")
                if actual != width:
                    findings.append(
                        f"{label}: panel {panel['id']} {title!r} column {column!r} width is {actual}, expected {width}"
                    )
        if panel.get("type") in {"timeseries", "barchart", "histogram", "piechart"}:
            legend = panel.get("options", {}).get("legend", {})
            if legend.get("showLegend") is not True:
                findings.append(f"{label}: panel {panel['id']} {panel.get('title')!r} legend is not enabled")
            if legend.get("displayMode") != "list" or legend.get("placement") != "bottom":
                findings.append(
                    f"{label}: panel {panel['id']} {panel.get('title')!r} legend is "
                    f"{legend.get('displayMode')}/{legend.get('placement')}, expected list/bottom"
                )
            if legend.get("calcs") not in (None, []):
                findings.append(f"{label}: panel {panel['id']} {panel.get('title')!r} legend has embedded calcs")
            for series, expected_color in EXPECTED_SERIES_COLORS.items():
                if series not in target_aliases(panel) and not override_props(panel, series):
                    continue
                color = override_props(panel, series).get("color")
                fixed_color = color.get("fixedColor") if isinstance(color, dict) else None
                if fixed_color != expected_color:
                    findings.append(
                        f"{label}: panel {panel['id']} {panel.get('title')!r} series {series!r} color is "
                        f"{fixed_color}, expected {expected_color}"
                    )
        if panel.get("type") == "state-timeline":
            for series, expected_color in EXPECTED_SERIES_COLORS.items():
                if series not in target_aliases(panel) and not override_props(panel, series):
                    continue
                color = override_props(panel, series).get("color")
                fixed_color = color.get("fixedColor") if isinstance(color, dict) else None
                if fixed_color != expected_color:
                    findings.append(
                        f"{label}: panel {panel['id']} {panel.get('title')!r} state series {series!r} color is "
                        f"{fixed_color}, expected {expected_color}"
                    )
        if panel.get("type") in {"timeseries", "state-timeline"}:
            findings.extend(check_vpd_context_panel_data(label, panel))
            findings.extend(check_public_series_schema(label, panel))
        if panel.get("type") == "timeseries" and panel.get("title") == "VPD & Dew Point Spread":
            for series in ("VPD (kPa)", "VPD Forecast"):
                props = override_props(panel, series)
                if props.get("unit") != "pressurekpa" or props.get("custom.axisPlacement") != "left":
                    findings.append(
                        f"{label}: panel {panel['id']} 'VPD & Dew Point Spread' series {series!r} "
                        "is not pinned to the left kPa axis"
                    )
            for series in ("Dew Spread Observed (°F)", "Dew Spread Forecast (°F)"):
                props = override_props(panel, series)
                if (
                    props.get("unit") != "fahrenheit"
                    or props.get("custom.axisPlacement") != "right"
                    or props.get("custom.axisLabel") != "°F"
                ):
                    findings.append(
                        f"{label}: panel {panel['id']} 'VPD & Dew Point Spread' series {series!r} "
                        "is not pinned to the right °F axis"
                    )
        if panel.get("type") == "timeseries" and panel.get("title") == "Soil Moisture vs Air VPD":
            moisture_props = override_props(panel, "South 1 Moisture (%)")
            moisture_color = moisture_props.get("color")
            if (
                not isinstance(moisture_color, dict)
                or moisture_color.get("fixedColor") != BRAND["leaf"]
                or moisture_props.get("unit") != "percent"
                or moisture_props.get("custom.axisPlacement") != "left"
                or moisture_props.get("custom.axisLabel") != "%"
            ):
                findings.append(
                    f"{label}: panel {panel['id']} 'Soil Moisture vs Air VPD' moisture series "
                    "is not pinned to the left percent axis"
                )
            vpd_props = override_props(panel, "South Air VPD (kPa)")
            vpd_color = vpd_props.get("color")
            if (
                not isinstance(vpd_color, dict)
                or vpd_color.get("fixedColor") != BRAND["violet"]
                or vpd_props.get("unit") != "pressurekpa"
                or vpd_props.get("custom.axisPlacement") != "right"
                or vpd_props.get("custom.axisLabel") != "kPa"
            ):
                findings.append(
                    f"{label}: panel {panel['id']} 'Soil Moisture vs Air VPD' VPD series "
                    "is not pinned to the right kPa axis"
                )
        if panel.get("type") == "barchart" and panel.get("title") == "Plan Accuracy by Day":
            options = panel.get("options", {})
            if options.get("xField") != "Day" or options.get("showValue") != "never":
                findings.append(
                    f"{label}: panel {panel['id']} 'Plan Accuracy by Day' does not use the compact public bar schema"
                )
            raw_sql = next(
                (
                    target.get("rawSql")
                    for target in panel.get("targets", []) or []
                    if isinstance(target, dict) and target.get("rawSql") is not None
                ),
                "",
            )
            if raw_sql != PLAN_ACCURACY_SQL:
                findings.append(
                    f"{label}: panel {panel['id']} 'Plan Accuracy by Day' does not use compact 14-day labels"
                )
        if panel.get("type") == "timeseries" and panel.get("title") == "Daily Cost by Source":
            raw_sql = next(
                (
                    target.get("rawSql")
                    for target in panel.get("targets", []) or []
                    if isinstance(target, dict) and target.get("rawSql") is not None
                ),
                "",
            )
            if raw_sql != DAILY_COST_BY_SOURCE_SQL:
                findings.append(
                    f"{label}: panel {panel['id']} 'Daily Cost by Source' does not use daily_summary cost fields"
                )
        if panel.get("type") == "stat" and panel.get("title") == "Free Heap":
            defaults = panel.get("fieldConfig", {}).get("defaults", {})
            if defaults.get("unit") != "suffix:kB":
                findings.append(
                    f"{label}: panel {panel['id']} 'Free Heap' unit is {defaults.get('unit')}, expected suffix:kB"
                )
            raw_sql = next(
                (
                    target.get("rawSql")
                    for target in panel.get("targets", []) or []
                    if isinstance(target, dict) and target.get("rawSql") is not None
                ),
                "",
            )
            if raw_sql != FREE_HEAP_SQL:
                findings.append(f"{label}: panel {panel['id']} 'Free Heap' does not use the public kB schema")
        if panel.get("type") == "stat" and panel.get("title") in RUNTIME_HOUR_STAT_TITLES:
            defaults = panel.get("fieldConfig", {}).get("defaults", {})
            if defaults.get("unit") != "suffix:h" or defaults.get("decimals") != 1:
                findings.append(
                    f"{label}: panel {panel['id']} {panel.get('title')!r} hour stat unit/decimals are "
                    f"{defaults.get('unit')}/{defaults.get('decimals')}, expected suffix:h/1"
                )
        if panel.get("type") == "timeseries" and panel.get("title") == "pH & ORP (7-Day Trend)":
            sql_by_ref = {
                target.get("refId"): target.get("rawSql")
                for target in panel.get("targets", []) or []
                if isinstance(target, dict)
            }
            if sql_by_ref.get("A") != HYDRO_PH_SQL or sql_by_ref.get("B") != HYDRO_ORP_SQL:
                findings.append(
                    f"{label}: panel {panel['id']} 'pH & ORP (7-Day Trend)' does not filter impossible chemistry values"
                )
            ph_props = override_props(panel, "pH")
            ph_color = ph_props.get("color")
            if not isinstance(ph_color, dict) or ph_color.get("fixedColor") != BRAND["gold"]:
                findings.append(f"{label}: panel {panel['id']} pH color is not chemistry gold")
            if ph_props.get("custom.axisSoftMin") != 4 or ph_props.get("custom.axisSoftMax") != 8:
                findings.append(f"{label}: panel {panel['id']} pH axis is not constrained to the public range")
            orp_props = override_props(panel, "ORP (mV)")
            orp_color = orp_props.get("color")
            if not isinstance(orp_color, dict) or orp_color.get("fixedColor") != BRAND["violet"]:
                findings.append(f"{label}: panel {panel['id']} ORP color is not chemistry violet")
        if panel.get("type") == "timeseries" and panel.get("title") in {"Daily Water Usage", "Flow Rate (10min avg)"}:
            title = str(panel.get("title"))
            expected_sql = DAILY_WATER_USAGE_SQL if title == "Daily Water Usage" else FLOW_RATE_SQL
            expected_label = "Water Used (gal)" if title == "Daily Water Usage" else "Flow (gal/min)"
            expected_unit = "gal" if title == "Daily Water Usage" else "gal/min"
            expected_decimals = 0 if title == "Daily Water Usage" else 1
            raw_sql = next(
                (
                    target.get("rawSql")
                    for target in panel.get("targets", []) or []
                    if isinstance(target, dict) and target.get("rawSql") is not None
                ),
                "",
            )
            if raw_sql != expected_sql:
                findings.append(f"{label}: panel {panel['id']} {title!r} still uses a generic water series schema")
            defaults = panel.get("fieldConfig", {}).get("defaults", {})
            if defaults.get("unit") != expected_unit or defaults.get("decimals") != expected_decimals:
                findings.append(
                    f"{label}: panel {panel['id']} {title!r} unit/decimals are "
                    f"{defaults.get('unit')}/{defaults.get('decimals')}, expected {expected_unit}/{expected_decimals}"
                )
            props = override_props(panel, expected_label)
            color = props.get("color")
            if not isinstance(color, dict) or color.get("fixedColor") != BRAND["water"]:
                findings.append(f"{label}: panel {panel['id']} {title!r} water series color is not blue")
    missing = embedded_ids - found_ids
    for panel_id in sorted(missing):
        findings.append(f"{label}: embedded panelId={panel_id} is missing from dashboard JSON")
    return findings


def check_dashboard(path: Path, embedded_ids: set[int]) -> list[str]:
    return check_dashboard_data(str(path), load_json(path), embedded_ids)


def parse_live_dashboard_payload(uid: str, stdout: str) -> dict[str, Any]:
    payload = json.loads(stdout)
    dashboard = payload.get("dashboard") if isinstance(payload, dict) else None
    if not isinstance(dashboard, dict):
        raise RuntimeError(f"live dashboard {uid} returned no dashboard payload")
    return dashboard


def live_dashboard_from_kubectl(uid: str) -> dict[str, Any]:
    result = subprocess.run(
        [
            "kubectl",
            "-n",
            "verdify-prod",
            "exec",
            "deploy/verdify-grafana",
            "-c",
            "grafana",
            "--",
            "wget",
            "-qO-",
            f"http://127.0.0.1:3000/api/dashboards/uid/{uid}",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"kubectl exec verdify-grafana wget {uid} failed: {stderr}")
    return parse_live_dashboard_payload(uid, result.stdout)


def live_dashboard_from_docker(uid: str, container: str) -> dict[str, Any]:
    result = subprocess.run(
        [
            "docker",
            "exec",
            container,
            "sh",
            "-c",
            f"curl -sS --max-time 20 http://localhost:3000/api/dashboards/uid/{uid}",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"docker exec {container} curl {uid} failed: {stderr}")
    return parse_live_dashboard_payload(uid, result.stdout)


def live_dashboard_from_public_api(uid: str) -> dict[str, Any]:
    request = urllib.request.Request(
        f"https://graphs.verdify.ai/api/dashboards/uid/{uid}",
        headers={"User-Agent": "verdify-brand-check/1.0"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return parse_live_dashboard_payload(uid, response.read().decode("utf-8"))


def live_dashboard(uid: str, container: str) -> dict[str, Any]:
    kubectl_error: RuntimeError | None = None
    try:
        return live_dashboard_from_kubectl(uid)
    except (FileNotFoundError, subprocess.TimeoutExpired, RuntimeError) as exc:
        kubectl_error = RuntimeError(str(exc))

    try:
        return live_dashboard_from_docker(uid, container)
    except (FileNotFoundError, subprocess.TimeoutExpired, RuntimeError) as exc:
        docker_error = RuntimeError(str(exc))

    try:
        return live_dashboard_from_public_api(uid)
    except Exception as exc:
        raise RuntimeError(
            f"{kubectl_error}; legacy Docker fallback failed: {docker_error}; public API fallback failed: {exc}"
        ) from exc


def source_site_dashboard_refs() -> dict[str, set[int]]:
    refs: dict[str, set[int]] = {}
    for path in sorted((REPO_ROOT / "grafana" / "dashboards").glob("site-*.json")):
        dashboard = load_json(path)
        uid = dashboard.get("uid")
        if not isinstance(uid, str) or not uid:
            continue
        panel_ids = {
            int(panel["id"])
            for panel in iter_panels(dashboard.get("panels", []))
            if panel.get("type") != "row" and isinstance(panel.get("id"), int)
        }
        if panel_ids:
            refs[uid] = panel_ids
    return refs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault-root", type=Path, default=DEFAULT_VAULT_ROOT)
    parser.add_argument("--check", action="store_true", help="validate without rewriting files")
    parser.add_argument("--live", action="store_true", help="validate live Grafana dashboards instead of source JSON")
    parser.add_argument("--grafana-container", default=DEFAULT_GRAFANA_CONTAINER)
    args = parser.parse_args()

    embedded = embedded_panels(args.vault_root)
    if args.live and not embedded:
        embedded = source_site_dashboard_refs()
    paths_by_uid = dashboard_paths(embedded)
    missing_source_uids = sorted(set(embedded) - set(paths_by_uid))
    if missing_source_uids and not args.live:
        for uid in missing_source_uids:
            print(f"missing dashboard JSON for embedded uid={uid}")
        return 1

    if args.check:
        findings: list[str] = []
        if args.live:
            for uid, panel_ids in sorted(embedded.items()):
                try:
                    dashboard = live_dashboard(uid, args.grafana_container)
                except Exception as exc:
                    findings.append(f"live:{uid}: {exc}")
                    continue
                findings.extend(check_dashboard_data(f"live:{uid}", dashboard, panel_ids))
        else:
            for uid, paths in sorted(paths_by_uid.items()):
                for path in paths:
                    findings.extend(check_dashboard(path, embedded[uid]))
            checked_paths = {path for paths in paths_by_uid.values() for path in paths}
            for path in all_dashboard_paths():
                if path in checked_paths:
                    continue
                try:
                    dashboard = load_json(path)
                    findings.extend(check_daylight_dashboard_data(str(path), dashboard))
                    findings.extend(check_compliance_dashboard_data(str(path), dashboard))
                    findings.extend(check_lighting_threshold_dashboard_data(str(path), dashboard))
                    findings.extend(check_relay_state_lane_dashboard_data(str(path), dashboard))
                except json.JSONDecodeError:
                    continue
            findings.extend(check_site_embed_contract(args.vault_root))
            findings.extend(check_time_filtered_embed_ranges(args.vault_root, paths_by_uid))
        if findings:
            print("\n".join(findings))
            return 1
        panel_count = sum(len(ids) for ids in embedded.values())
        scope = "live" if args.live else "source"
        print(
            f"Grafana embed brand check passed for {panel_count} embedded panel refs "
            f"across {len(embedded)} {scope} dashboards"
        )
        return 0

    changed = 0
    touched_panels = 0
    source_paths = sorted(set(all_dashboard_paths()) | {path for paths in paths_by_uid.values() for path in paths})
    for path in source_paths:
        try:
            uid = load_json(path).get("uid")
        except json.JSONDecodeError:
            continue
        did_change, panel_count = brand_dashboard(path, embedded.get(uid, set()))
        touched_panels += panel_count
        if did_change:
            changed += 1
            print(f"updated {path} ({uid or '<no uid>'}, {panel_count} matching embedded panels)")
    print(f"updated {changed} dashboard files; reviewed {touched_panels} embedded-panel definitions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
