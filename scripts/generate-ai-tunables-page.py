#!/usr/bin/env python3
"""Generate the public AI tunables traceability page and planner brief.

The output deliberately combines three evidence classes:
- static route evidence from the registry, schema, MCP, ingestor maps, and
  firmware source text;
- live landing evidence from setpoint_plan, setpoint_changes, setpoint_snapshot,
  and setpoint_clamps;
- outcome evidence from structured plan rationales and plan scorecards.

This is not a causal simulator. A confirmed readback proves that a tunable
landed on the ESP32; greenhouse effectiveness still has to be read through the
plan scorecard, equipment state, and domain-specific evidence.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import json
import math
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTENT_ROOT = Path("/srv/verdify/verdify-site/content")
OUTPUT_PATH = CONTENT_ROOT / "reference" / "ai-tunables.md"
RAW_OUTPUT_PATH = Path("/srv/verdify/state/site-generated/raw-ai-tunables.md")

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "ingestor"))

from config import DB_DSN  # noqa: E402
from verdify_schemas.climate_intent import CLIMATE_INTENT_FIELD_DOCS  # noqa: E402
from verdify_schemas.tunable_registry import (  # noqa: E402
    PLANNER_PUSHABLE_REG,
    REGISTRY,
    TIER1_REG,
    TUNABLE_CONTRACT_CLASSES_REG,
    TunableDef,
)
from verdify_schemas.tunables import ALL_TUNABLES  # noqa: E402

RESERVED_NO_EFFECT = {
    "bias_cool",
    "bias_heat",
    "d_cool_stage_2",
    "d_heat_stage_2",
    "fan_burst_min",
    "fog_burst_min",
    "mist_vent_close_lead_s",
    "mist_vent_reopen_delay_s",
    "mister_all_off_s",
    "mister_all_on_s",
    "mister_max_runtime_min",
    "mister_off_s",
    "mister_on_s",
    "sw_fsm_controller_enabled",
    "summer_vent_min_runtime_s",
    "vent_bypass_min",
}

FIRMWARE_FILES = [
    REPO_ROOT / "firmware" / "greenhouse" / "controls.yaml",
    REPO_ROOT / "firmware" / "greenhouse" / "tunables.yaml",
    REPO_ROOT / "firmware" / "greenhouse" / "globals.yaml",
    REPO_ROOT / "firmware" / "greenhouse" / "sensors.yaml",
    REPO_ROOT / "firmware" / "lib" / "greenhouse_logic.h",
    REPO_ROOT / "firmware" / "lib" / "greenhouse_types.h",
]


@dataclass
class Evidence:
    active_value: float | None = None
    active_plan_id: str | None = None
    active_ts: datetime | None = None
    future_rows: int = 0
    future_values: int = 0
    last_plan_ts: datetime | None = None
    last_plan_id: str | None = None
    last_plan_value: float | None = None
    plan_writes_7d: int = 0
    plan_writes_30d: int = 0
    last_dispatch_ts: datetime | None = None
    last_dispatch_value: float | None = None
    last_dispatch_source: str | None = None
    last_dispatch_trigger_id: str | None = None
    dispatch_7d: int = 0
    dispatch_30d: int = 0
    confirmed_7d: int = 0
    unconfirmed_7d: int = 0
    distinct_values_7d: int = 0
    last_readback_ts: datetime | None = None
    last_readback_value: float | None = None
    readbacks_7d: int = 0
    clamps_30d: int = 0
    clamps_24h: int = 0
    clamp_avg_requested: float | None = None
    clamp_avg_applied: float | None = None
    latest_clamp_ts: datetime | None = None
    latest_clamp_reason: str | None = None
    latest_rationale_plan: str | None = None
    latest_rationale_ts: datetime | None = None
    latest_rationale_expected: str | None = None
    latest_rationale_value: float | None = None
    latest_rationale_validated_at: datetime | None = None
    latest_rationale_score: float | None = None


def _assigned_set(path: Path, name: str) -> set[str]:
    tree = ast.parse(path.read_text())
    known_names = {
        "TIER1_REG": TIER1_REG,
        "PLANNER_PUSHABLE_REG": PLANNER_PUSHABLE_REG,
    }
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            continue
        value = node.value
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "frozenset":
            value = value.args[0]
        if isinstance(value, ast.Name) and value.id in known_names:
            return set(known_names[value.id])
        return set(ast.literal_eval(value))
    raise RuntimeError(f"{name} assignment not found in {path}")


def _fmt_value(value: float | None) -> str:
    if value is None:
        return "-"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(f):
        return "live"
    if abs(f - round(f)) < 1e-9:
        return str(int(round(f)))
    return f"{f:.3f}".rstrip("0").rstrip(".")


def _summary_object(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _fmt_dt(ts: datetime | None) -> str:
    if ts is None:
        return "-"
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC).strftime("%Y-%m-%d %H:%MZ")


def _age(ts: datetime | None) -> str:
    if ts is None:
        return "-"
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    seconds = max(0, int((datetime.now(UTC) - ts.astimezone(UTC)).total_seconds()))
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def _md(text: str | None) -> str:
    text = (text or "").strip()
    if not text:
        return "-"
    text = text.replace("|", "\\|")
    text = re.sub(r"\s+", " ", text)
    return text


def _frontmatter() -> str:
    data = {
        "title": "Planner Contract and AI Tunables",
        "description": (
            "Production contract for Verdify planner triggers, accepted writes, publishing behavior, "
            "and the registry-backed tunable surface the AI planning agent may use."
        ),
        "tags": ["intelligence", "planning", "tunables", "control"],
        "date": datetime.now().date().isoformat(),
        "type": "reference",
        "cssclasses": ["hide-folder-listing"],
        "aliases": [
            "reference/planner-trigger-contract",
            "reference/planner-publishing-contract",
        ],
    }
    return "---\n" + yaml.safe_dump(data, sort_keys=False).strip() + "\n---\n"


def _category(name: str, spec: TunableDef) -> str:
    if name in {"fallback_window_s", "outdoor_temp_f", "outdoor_dewpoint_f"}:
        return "Readback-only firmware inputs"
    if (
        name.startswith("temp_")
        or name.startswith("cool_")
        or name
        in {
            "d_heat_stage_2",
            "d_cool_stage_2",
            "heat_hysteresis",
            "cold_vent_guard_delta_f",
            "sw_cool_all_fans_at_high_enabled",
        }
    ):
        return "Temperature band and staging"
    if name.startswith("vpd_") and "target" not in name:
        return "VPD band"
    if name.startswith("safety_"):
        return "Safety rails"
    if name.startswith("mister_") or name.startswith("mist_") or name == "sw_mister_closes_vent":
        return "Misting and sealed-humidification"
    if name.startswith("activity_") or name.startswith("direct_wet_") or name == "sw_direct_wet_gate_enabled":
        return "Activity and direct-wet gates"
    if name in {
        "min_heat_on_s",
        "min_heat_off_s",
        "min_fan_on_s",
        "min_fan_off_s",
        "min_vent_on_s",
        "min_vent_off_s",
        "lead_rotate_s",
    }:
        return "Relay dwell and rotation"
    if name.startswith("fog_") or name.startswith("min_fog_") or name == "sw_fog_closes_vent":
        return "Fog gates"
    if name.startswith("irrig_") or name.startswith("sw_irrigation"):
        return "Irrigation"
    if name.startswith("gl_") or name == "sw_gl_auto_mode":
        return "Grow lights"
    if "enthalpy" in name or name.startswith("econ_") or name == "sw_economiser_enabled" or name == "site_pressure_hpa":
        return "Economiser"
    if "summer_vent" in name or name.startswith("vent_prefer_") or name.startswith("outdoor_staleness"):
        return "Summer vent gate"
    if name.startswith("sw_") or name in {"dwell_gate_ms", "mist_backoff_s"}:
        return "Controller gates"
    if "target" in name or name in {"east_adjacency_factor", "mister_center_penalty"}:
        return "Zone scoring"
    return spec.push_owner.replace("_", " ").title()


def _why_it_matters(name: str, category: str) -> str:
    if name in RESERVED_NO_EFFECT:
        return "It matters mostly as a drift risk: the value can be exposed or read back, but current firmware does not use it for control."
    if category == "Temperature band and staging":
        return "Temperature setpoints drive heater staging, ventilation, fan staging, and heat-stress avoidance."
    if category == "VPD band":
        return "VPD control protects transpiration balance; this is usually the limiting axis on hot dry days."
    if category == "Safety rails":
        return "These are hard guardrails. They protect the greenhouse when normal planning or sensors are wrong."
    if category == "Misting and sealed-humidification":
        return "Misting trades water and heat retention against VPD stress. Bad settings either waste water or leave plants dry."
    if category == "Activity and direct-wet gates":
        return "The activity window aligns lights, wetting, fert paths, and drydown timing so direct plant wetting happens only during the biological day."
    if category == "Relay dwell and rotation":
        return "Dwell limits protect relays and reduce churn while still letting safety paths preempt."
    if category == "Fog gates":
        return "Fog is high-power humidity assist. These gates decide when that heavy tool is allowed."
    if category == "Irrigation":
        return "Irrigation settings feed crop water availability and fertigation timing, not the firmware climate mode."
    if category == "Grow lights":
        return "Grow-light settings trade supplemental DLI against electrical cost and light-cycle stability."
    if category == "Economiser":
        return "Economiser settings decide when outside air is useful enough to ventilate without worsening heat or humidity."
    if category == "Summer vent gate":
        return "This gate prevents firmware from sealing the greenhouse when outside air is cooler and drier enough to cool safely."
    if category == "Controller gates":
        return "These switches and dwell values choose the controller posture and mode-transition stability."
    if category == "Readback-only firmware inputs":
        return (
            "The planner cannot set this value, but it explains why the firmware accepted or rejected a control path."
        )
    return "It is part of the bounded greenhouse control surface and must stay traceable across schema, DB, dispatcher, firmware, and site docs."


def _planner_guidance(name: str, spec: TunableDef, plan_required: set[str]) -> str:
    control_class = spec.control_class
    if name == "sw_fsm_controller_enabled":
        return "Do not plan with this value. It is a compatibility/readback field for the unified band-first controller, and ESPHome keeps the live controller path ON."
    if name == "cool_stage2_over_high_f":
        return "Planner-policy tunable. On hot or high-solar days, use 0.5-1.0 F so fan2 helps near the high edge; relax after recovery to reduce fan wear."
    if name == "cool_exit_hysteresis_f":
        return "Planner-policy tunable. Lower values clear VENTILATE sooner; higher values hold cooling longer after hot stress. Keep dew/VPD effects in the hypothesis."
    if name == "cold_vent_guard_delta_f":
        return "Planner-policy tunable. Raise for cold-slug risk, lower when outdoor exchange is useful and temperature headroom is healthy."
    if name == "sw_cool_all_fans_at_high_enabled":
        return "Planner-policy switch. Enable only for forecast-backed heat stress windows where one-fan ventilation has been insufficient; disable after the window."
    if name == "sw_direct_wet_stress_override_enabled":
        return "Planner-policy switch. Enable only for VPD-high evening recovery when normal direct-wet drydown is blocking water and dew margin is safely above the configured floor."
    if name == "direct_wet_stress_vpd_margin_kpa":
        return "Planner-policy tunable. Keep low, around 0.05-0.15 kPa, when late dry recovery needs direct wetting; raise it to reserve override for stronger dry stress."
    if name == "direct_wet_stress_min_dew_margin_f":
        return "Planner-policy safety gate. Use 8 F or higher unless disease-risk evidence justifies a stricter margin; never lower to chase compliance blindly."
    if name == "direct_wet_stress_latest_hour":
        return "Planner-policy latest-hour cap. Use the earliest hour that covers the dry recovery window, then back out after VPD recovers."
    if name == "sw_fog_stress_window_extend_enabled":
        return "Planner-policy switch. Enable only when VPD-high persists after the normal fog window and dew/RH/temp gates are still safe."
    if name == "fog_stress_window_latest_hour":
        return "Planner-policy latest-hour cap for fog stress extension. Keep conservative; fog is high-power and disease-risk sensitive near nightfall."
    if name == "fog_stress_min_dew_margin_f":
        return "Planner-policy safety gate. Keep at least 10 F for evening fog extension unless stronger disease-risk instrumentation supports a stricter posture."
    if name == "mister_engage_kpa":
        return "Planner-policy tunable. During VPD-high or near-edge `VENTILATE` stress with healthy dew margin, keep near active `vpd_high + 0.05`; values near 2.5 suppress wet assist and should be reserved for dew/occupancy/irrigation/water-cap constraints."
    if name == "mister_all_kpa":
        return "Planner-policy tunable. During VPD-high or near-edge `VENTILATE` stress with healthy dew margin, keep near `max(1.0, vpd_high + 0.25)` so all-zone mist assist can engage; high absolute values disable escalation."
    if name == "fog_escalation_kpa":
        return "Planner-policy tunable. During VPD-high or near-edge `VENTILATE` stress with healthy dew margin, use `0.15-0.20` in hot/dry venting, `0.25-0.30` for mild dry stress, and reserve `0.35-0.50` for VPD-low overshoot, dew risk, or resource limits."
    if name in {"mister_engage_delay_s", "mister_all_delay_s", "mister_pulse_gap_s", "min_fog_off_s"}:
        return "Planner-policy tunable. During VPD-high or near-edge `VENTILATE` stress with healthy dew margin, use shorter delays/gaps so ventilation can cool while misting protects VPD."
    if name in RESERVED_NO_EFFECT:
        return "Do not plan with this value until firmware consumes it. Treat existing readbacks as exposure evidence only."
    if name.startswith("activity_"):
        return "Dispatcher-owned mirror of the main lighting runtime policy. Do not set directly; adjust the main light schedule when the biological day should move."
    if name.startswith("direct_wet_") or name == "sw_direct_wet_gate_enabled":
        return "Planner-policy gate for direct plant wetting. Tune per zone to move morning wet start or pre-off drydown without adding crop-specific firmware logic."
    if name in plan_required:
        return "Materialized Tier 1 field. Routine `set_plan` writes must use bounded `climate_intent`; MCP derives this low-level row."
    if control_class == "planner_policy":
        return "Planner-policy tunable. Use only when forecast or previous-plan evidence points directly to this control path."
    if control_class == "crop_band":
        return "Do not push from the AI planning agent. Crop profiles and dispatcher own this value; use planner-policy bias/staging knobs instead."
    if control_class == "readback_context":
        return "Read-only context. Use it to explain firmware decisions; never emit it in set_plan or set_tunable."
    if control_class == "retired":
        return "Retired/no-op surface. Keep it out of plans and remove only through a compatibility migration."
    return "Controller/operator configuration. Keep stable from the planner; promote to planner_policy only after replay evidence proves value."


def _implementation_summary(name: str, spec: TunableDef, plan_required: set[str]) -> str:
    if spec.esp_object_id is None:
        route = "readback-only; no SETPOINT_MAP route"
    else:
        route = f"ESPHome `{spec.esp_object_id}` via SETPOINT_MAP"
    readback = spec.cfg_readback_object_id or "no cfg readback"
    mcp = (
        "materialized from climate_intent"
        if name in plan_required
        else ("set_tunable allowed" if name in PLANNER_PUSHABLE_REG else "MCP rejects planner writes")
    )
    return f"{mcp}; {route}; readback `{readback}`."


def _static_firmware_hits() -> dict[str, dict[str, int]]:
    texts = {path.name: path.read_text(errors="ignore") for path in FIRMWARE_FILES if path.exists()}
    hits: dict[str, dict[str, int]] = {}
    for name, spec in REGISTRY.items():
        tokens = {name}
        if spec.esp_object_id:
            tokens.add(spec.esp_object_id)
        if spec.cfg_readback_object_id:
            tokens.add(spec.cfg_readback_object_id)
        if name.startswith("sw_"):
            tokens.add(name[3:])
        tokens.add(name.replace("_s", ""))
        file_hits: dict[str, int] = {}
        for fname, text in texts.items():
            count = 0
            for token in tokens:
                if token:
                    count += len(re.findall(re.escape(token), text))
            if count:
                file_hits[fname] = count
        hits[name] = file_hits
    return hits


async def _load_evidence(params: list[str]) -> dict[str, Evidence]:
    conn = await asyncpg.connect(DB_DSN)
    try:
        evidence = {param: Evidence() for param in params}

        for row in await conn.fetch(
            """
            SELECT parameter, value, plan_id, ts
            FROM v_active_plan
            WHERE parameter = ANY($1::text[])
            """,
            params,
        ):
            ev = evidence[row["parameter"]]
            ev.active_value = row["value"]
            ev.active_plan_id = row["plan_id"]
            ev.active_ts = row["ts"]

        for row in await conn.fetch(
            """
            SELECT parameter,
                   COUNT(*) FILTER (WHERE ts > now() AND is_active)::int AS future_rows,
                   COUNT(DISTINCT round(value::numeric, 4)) FILTER (WHERE ts > now() AND is_active)::int AS future_values,
                   COUNT(*) FILTER (WHERE created_at > now() - interval '7 days')::int AS plan_writes_7d,
                   COUNT(*) FILTER (WHERE created_at > now() - interval '30 days')::int AS plan_writes_30d
              FROM setpoint_plan
             WHERE parameter = ANY($1::text[])
             GROUP BY parameter
            """,
            params,
        ):
            ev = evidence[row["parameter"]]
            ev.future_rows = row["future_rows"] or 0
            ev.future_values = row["future_values"] or 0
            ev.plan_writes_7d = row["plan_writes_7d"] or 0
            ev.plan_writes_30d = row["plan_writes_30d"] or 0

        for row in await conn.fetch(
            """
            SELECT DISTINCT ON (parameter) parameter, ts, value, plan_id
              FROM setpoint_plan
             WHERE parameter = ANY($1::text[])
             ORDER BY parameter, created_at DESC, ts DESC
            """,
            params,
        ):
            ev = evidence[row["parameter"]]
            ev.last_plan_ts = row["ts"]
            ev.last_plan_id = row["plan_id"]
            ev.last_plan_value = row["value"]

        for row in await conn.fetch(
            """
            SELECT parameter,
                   COUNT(*) FILTER (WHERE ts > now() - interval '7 days')::int AS dispatch_7d,
                   COUNT(*) FILTER (WHERE ts > now() - interval '30 days')::int AS dispatch_30d,
                   COUNT(*) FILTER (WHERE ts > now() - interval '7 days' AND confirmed_at IS NOT NULL)::int AS confirmed_7d,
                   COUNT(*) FILTER (WHERE ts > now() - interval '7 days' AND confirmed_at IS NULL)::int AS unconfirmed_7d,
                   COUNT(DISTINCT round(value::numeric, 4)) FILTER (WHERE ts > now() - interval '7 days')::int AS distinct_values_7d
              FROM setpoint_changes
             WHERE parameter = ANY($1::text[])
             GROUP BY parameter
            """,
            params,
        ):
            ev = evidence[row["parameter"]]
            ev.dispatch_7d = row["dispatch_7d"] or 0
            ev.dispatch_30d = row["dispatch_30d"] or 0
            ev.confirmed_7d = row["confirmed_7d"] or 0
            ev.unconfirmed_7d = row["unconfirmed_7d"] or 0
            ev.distinct_values_7d = row["distinct_values_7d"] or 0

        for row in await conn.fetch(
            """
            SELECT DISTINCT ON (parameter)
                   parameter, ts, value, source, trigger_id::text AS trigger_id, planner_instance
              FROM setpoint_changes
             WHERE parameter = ANY($1::text[])
             ORDER BY parameter, ts DESC
            """,
            params,
        ):
            ev = evidence[row["parameter"]]
            ev.last_dispatch_ts = row["ts"]
            ev.last_dispatch_value = row["value"]
            ev.last_dispatch_source = row["source"]
            ev.last_dispatch_trigger_id = row["trigger_id"]

        for row in await conn.fetch(
            """
            SELECT parameter,
                   COUNT(*) FILTER (WHERE ts > now() - interval '7 days')::int AS readbacks_7d
              FROM setpoint_snapshot
             WHERE parameter = ANY($1::text[])
             GROUP BY parameter
            """,
            params,
        ):
            evidence[row["parameter"]].readbacks_7d = row["readbacks_7d"] or 0

        for row in await conn.fetch(
            """
            SELECT DISTINCT ON (parameter) parameter, ts, value
              FROM setpoint_snapshot
             WHERE parameter = ANY($1::text[])
             ORDER BY parameter, ts DESC
            """,
            params,
        ):
            ev = evidence[row["parameter"]]
            ev.last_readback_ts = row["ts"]
            ev.last_readback_value = row["value"]

        for row in await conn.fetch(
            """
            SELECT parameter,
                   COUNT(*) FILTER (WHERE ts > now() - interval '30 days')::int AS clamps_30d,
                   COUNT(*) FILTER (WHERE ts > now() - interval '24 hours')::int AS clamps_24h,
                   AVG(requested) FILTER (WHERE ts > now() - interval '30 days') AS avg_requested,
                   AVG(applied) FILTER (WHERE ts > now() - interval '30 days') AS avg_applied,
                   MAX(ts) AS latest_clamp_ts
              FROM setpoint_clamps
             WHERE parameter = ANY($1::text[])
             GROUP BY parameter
            """,
            params,
        ):
            ev = evidence[row["parameter"]]
            ev.clamps_30d = row["clamps_30d"] or 0
            ev.clamps_24h = row["clamps_24h"] or 0
            ev.clamp_avg_requested = float(row["avg_requested"]) if row["avg_requested"] is not None else None
            ev.clamp_avg_applied = float(row["avg_applied"]) if row["avg_applied"] is not None else None
            ev.latest_clamp_ts = row["latest_clamp_ts"]

        for row in await conn.fetch(
            """
            SELECT DISTINCT ON (parameter) parameter, reason
              FROM setpoint_clamps
             WHERE parameter = ANY($1::text[])
             ORDER BY parameter, ts DESC
            """,
            params,
        ):
            evidence[row["parameter"]].latest_clamp_reason = row["reason"]

        for row in await conn.fetch(
            """
            SELECT DISTINCT ON (param)
                   param AS parameter,
                   plan_id,
                   created_at,
                   validated_at,
                   COALESCE(anchor_score::numeric, outcome_score::numeric) AS score,
                   (r->>'new_value')::double precision AS new_value,
                   r->>'expected_effect' AS expected_effect
              FROM plan_journal pj
              CROSS JOIN LATERAL jsonb_array_elements(COALESCE(pj.hypothesis_structured->'rationale', '[]'::jsonb)) r
              CROSS JOIN LATERAL (SELECT r->>'parameter' AS param) p
             WHERE param = ANY($1::text[])
             ORDER BY param, created_at DESC
            """,
            params,
        ):
            ev = evidence[row["parameter"]]
            ev.latest_rationale_plan = row["plan_id"]
            ev.latest_rationale_ts = row["created_at"]
            ev.latest_rationale_expected = row["expected_effect"]
            ev.latest_rationale_value = row["new_value"]
            ev.latest_rationale_validated_at = row["validated_at"]
            ev.latest_rationale_score = float(row["score"]) if row["score"] is not None else None

        return evidence
    finally:
        await conn.close()


async def _load_summary() -> dict[str, Any]:
    conn = await asyncpg.connect(DB_DSN)
    try:
        row = await conn.fetchrow(
            """
            SELECT
              (SELECT COUNT(*) FROM alert_log WHERE disposition = 'open')::int AS open_alerts,
              (SELECT COUNT(*) FROM setpoint_plan WHERE is_active AND ts > now())::int AS future_rows,
              (SELECT COUNT(DISTINCT parameter) FROM setpoint_plan WHERE is_active AND ts > now())::int AS future_params,
              (SELECT COUNT(*) FROM setpoint_plan
                WHERE is_active AND parameter = ANY($1::text[]))::int AS reserved_active_rows,
              (SELECT COUNT(*) FROM setpoint_changes
                WHERE ts > now() - interval '30 minutes' AND source='plan')::int AS plan_dispatch_30m,
              (SELECT COUNT(*) FROM setpoint_changes
                WHERE ts > now() - interval '30 minutes' AND source='plan' AND trigger_id IS NOT NULL)::int
                AS plan_dispatch_triggered_30m,
              (SELECT COUNT(*) FROM planner_trigger_ledger
                WHERE event_type = 'FORECAST_DEVIATION' AND created_at > now() - interval '14 days')::int
                AS forecast_deviation_fires_14d,
              (SELECT jsonb_object_agg(source_type, count)
                 FROM (SELECT source_type, COUNT(*)::int AS count FROM verdify_embeddings GROUP BY source_type) e)
                AS embedding_counts,
              (SELECT round(AVG(vpd_delta)::numeric, 3)
                 FROM v_mister_effectiveness WHERE on_ts > now() - interval '14 days') AS mister_avg_vpd_delta_14d,
              (SELECT COUNT(*)::int
                 FROM v_mister_effectiveness WHERE on_ts > now() - interval '14 days') AS mister_cycles_14d,
              (SELECT jsonb_object_agg(override_type, count)
                 FROM (
                   SELECT override_type, COUNT(*)::int AS count
                     FROM override_events
                    WHERE ts > now() - interval '7 days'
                    GROUP BY override_type
                 ) o) AS overrides_7d,
              (WITH latest_climate AS (
                   SELECT ts, temp_avg, vpd_avg
                     FROM climate
                    WHERE temp_avg IS NOT NULL AND vpd_avg IS NOT NULL
                    ORDER BY ts DESC
                    LIMIT 1
                 ), latest AS (
                   SELECT c.ts,
                          c.temp_avg,
                          c.vpd_avg,
                          fn_setpoint_at('temp_low', c.ts) AS sp_temp_low,
                          fn_setpoint_at('temp_high', c.ts) AS sp_temp_high,
                          fn_setpoint_at('vpd_low', c.ts) AS sp_vpd_low,
                          fn_setpoint_at('vpd_high', c.ts) AS sp_vpd_high,
                          (
                            SELECT ss.value
                              FROM system_state ss
                             WHERE ss.entity = 'greenhouse_state'
                               AND ss.ts <= c.ts
                             ORDER BY ss.ts DESC
                             LIMIT 1
                          ) AS greenhouse_mode
                     FROM latest_climate c
                 )
                 SELECT jsonb_build_object(
                          'reading_ts', ts,
                          'temp_actual_f', round(temp_avg::numeric, 2),
                          'temp_low_f', round(sp_temp_low::numeric, 2),
                          'temp_target_f', round(((sp_temp_low + sp_temp_high) / 2.0)::numeric, 2),
                          'temp_high_f', round(sp_temp_high::numeric, 2),
                          'temp_target_delta_f',
                            round((temp_avg - ((sp_temp_low + sp_temp_high) / 2.0))::numeric, 2),
                          'vpd_actual_kpa', round(vpd_avg::numeric, 3),
                          'vpd_low_kpa', round(sp_vpd_low::numeric, 3),
                          'vpd_target_kpa', round(((sp_vpd_low + sp_vpd_high) / 2.0)::numeric, 3),
                          'vpd_high_kpa', round(sp_vpd_high::numeric, 3),
                          'vpd_target_delta_kpa',
                            round((vpd_avg - ((sp_vpd_low + sp_vpd_high) / 2.0))::numeric, 3),
                          'greenhouse_mode', greenhouse_mode)
                   FROM latest) AS climate_target_context
            """,
            sorted(RESERVED_NO_EFFECT),
        )
        return dict(row or {})
    finally:
        await conn.close()


def _effectiveness_status(name: str, spec: TunableDef, ev: Evidence) -> str:
    if name in RESERVED_NO_EFFECT:
        return "Not effectful in current firmware; removed from planner writes."
    if spec.esp_object_id is None:
        return "Readback/control input only; effectiveness is freshness and firmware consumption, not planner landing."
    if ev.dispatch_7d and ev.confirmed_7d == ev.dispatch_7d:
        return "Route confirmed: every 7d dispatcher write has a matching firmware readback."
    if ev.dispatch_7d and ev.confirmed_7d:
        return f"Mostly confirmed: {ev.confirmed_7d}/{ev.dispatch_7d} 7d writes have readbacks."
    if ev.dispatch_30d and ev.last_readback_ts:
        return "Route exists but not recently exercised by the dispatcher."
    if ev.last_readback_ts:
        return "Firmware publishes the value; no recent planner/dispatcher use."
    return "No recent live landing evidence; static route must be reviewed before relying on this."


def _planner_status(name: str, spec: TunableDef, plan_required: set[str]) -> str:
    if name in RESERVED_NO_EFFECT:
        return "MCP rejects planner writes; reserved/no-op"
    if name in plan_required:
        return "materialized from climate_intent for routine set_plan"
    if name in PLANNER_PUSHABLE_REG:
        return "set_tunable allowed; planner may write with a hypothesis"
    if spec.control_class == "crop_band":
        return "MCP rejects planner writes; dispatcher/crop-band owned"
    if spec.control_class == "readback_context":
        return "MCP rejects planner writes; readback context only"
    if spec.control_class == "controller_safety":
        return "MCP rejects planner writes; controller safety context"
    if spec.control_class == "retired":
        return "MCP rejects planner writes; retired"
    return f"MCP rejects planner writes; {spec.control_class.replace('_', ' ')}"


def _route_summary(name: str, spec: TunableDef, ev: Evidence) -> str:
    if name in RESERVED_NO_EFFECT:
        return "reserved"
    if ev.dispatch_7d and ev.confirmed_7d == ev.dispatch_7d:
        return "confirmed"
    if ev.dispatch_7d and ev.confirmed_7d:
        return f"{ev.confirmed_7d}/{ev.dispatch_7d} confirmed"
    if ev.last_readback_ts:
        return "readback only"
    if spec.esp_object_id is None:
        return "context only"
    return "static route"


def _render_summary(summary: dict[str, Any], plan_required: set[str]) -> list[str]:
    embedding_counts = summary.get("embedding_counts") or {}
    class_counts = {
        control_class: sum(1 for value in TUNABLE_CONTRACT_CLASSES_REG.values() if value == control_class)
        for control_class in ("planner_policy", "crop_band", "controller_safety", "readback_context", "retired")
    }
    overrides = summary.get("overrides_7d") or {}
    plan_dispatch = summary.get("plan_dispatch_30m") or 0
    plan_dispatch_triggered = summary.get("plan_dispatch_triggered_30m") or 0
    mister_cycles = summary.get("mister_cycles_14d") or 0
    mister_delta = summary.get("mister_avg_vpd_delta_14d")
    mister_delta_text = "-" if mister_delta is None else f"{float(mister_delta):.3f} kPa"
    return [
        "## Current Audit Snapshot",
        "",
        '<div class="metric-grid">',
        f'  <div class="metric-card"><strong>{len(ALL_TUNABLES)}</strong><span>Schema tunables</span><p>Every name accepted by PlanTransition, SetpointChange, or setpoint_snapshot.</p></div>',
        f'  <div class="metric-card"><strong>{len(REGISTRY)}</strong><span>Registry rows</span><p>Includes dispatcher-routed and readback-only firmware inputs.</p></div>',
        f'  <div class="metric-card"><strong>{len(plan_required)}</strong><span>Materialized Tier 1 knobs</span><p>MCP derives these from bounded ClimateIntent for routine set_plan writes.</p></div>',
        f'  <div class="metric-card"><strong>{len(PLANNER_PUSHABLE_REG)}</strong><span>Planner-policy knobs</span><p>The only tunables the planner may write. Operator, crop-band, readback, and retired rows are context only.</p></div>',
        f'  <div class="metric-card"><strong>{summary.get("open_alerts", "-")}</strong><span>Open alerts</span><p>Live safety state at generation time.</p></div>',
        f'  <div class="metric-card"><strong>{summary.get("future_params", "-")}</strong><span>Future active params</span><p>{summary.get("future_rows", "-")} future active plan rows.</p></div>',
        f'  <div class="metric-card"><strong>{summary.get("reserved_active_rows", "-")}</strong><span>Reserved active rows</span><p>Should remain zero for no-op/deprecated params.</p></div>',
        f'  <div class="metric-card"><strong>{plan_dispatch_triggered}/{plan_dispatch}</strong><span>30m trigger audit</span><p>Plan dispatcher writes carrying trigger IDs.</p></div>',
        f'  <div class="metric-card"><strong>{mister_delta_text}</strong><span>Mister VPD delta</span><p>{mister_cycles} measured mister cycles in the last 14 days.</p></div>',
        "</div>",
        "",
        f"Contract class counts: `{class_counts}`.",
        "",
        f"Embedding corpus counts: `{embedding_counts}`.",
        "",
        f"Firmware override events in the last 7 days: `{overrides}`.",
        "",
        "Effectiveness labels below mean three different things:",
        "",
        "- **Route confirmed** means the planner/dispatcher write landed and firmware read it back.",
        "- **Operational effect** means firmware has a code path that consumes the value.",
        "- **Greenhouse outcome** means a later scorecard or structured rationale supports or falsifies the plan. This page reports the latest available evidence but does not pretend a single tunable has isolated causal proof unless the system measured that directly.",
        "",
        "Current controller invariants:",
        "",
        "- `DEHUM_VENT` exits immediately if dehumidifying with vent/fans pushes VPD above `vpd_high`; cooling then uses VENTILATE with vent-mist assist, otherwise sealed mist recovery is allowed.",
        "- Non-safety heat is suppressed while vent/fan air exchange is physically active.",
        "- `heat2` is never valid without `heat1`; any observed heat2-without-heat1 interval is a fault to investigate, not a planner tactic.",
        "- The dispatcher preserves a minimum 0.55 kPa house VPD deadband so mixed-zone crop targets do not create controller chatter.",
        "- During live, near-edge, or recently unrecovered `VENTILATE` VPD-high stress with healthy dew margin, the dispatcher clamps conservative moisture thresholds near the active `vpd_high` band: `mister_engage_kpa <= vpd_high + 0.05`, `mister_all_kpa <= max(1.0, vpd_high + 0.25)`, `fog_escalation_kpa <= 0.20` or `0.15` in hot/dry venting, shorter mist delays/gaps, and shorter `min_fog_off_s`.",
        "- Planner moisture tuning ladder: open the band-coupled mister surface first, use `all_zone_vpd_excess_kpa` for distributed mister escalation, shorten `mister_pulse_gap_s` before increasing `mister_pulse_on_s`, use fog as the heavy 7x escalation path, and remember `mister_vpd_weight` changes zone selection rather than total duty.",
        "",
    ]


def _render_climate_intent_surface(summary: dict[str, Any]) -> list[str]:
    target = _summary_object(summary.get("climate_target_context"))
    rows = [
        "## ClimateIntent AI Surface",
        "",
        "Routine `set_plan` transitions must include a bounded `climate_intent` object with every field below explicitly set. These fields tune tactical posture around the dispatcher-owned targets; they do not own temp/VPD low, target, or high points.",
        "",
        "| Field | Bounds | Planner meaning | Firmware impact | Materialized knobs | Planner guidance |",
        "|---|---|---|---|---|---|",
    ]
    for doc in CLIMATE_INTENT_FIELD_DOCS:
        knobs = ", ".join(f"`{name}`" for name in doc.materialized_knobs) or "audit context only"
        rows.append(
            f"| `{doc.name}` | {doc.bounds} | {_md(doc.meaning)} | {_md(doc.firmware_impact)} | "
            f"{knobs} | {_md(doc.planner_guidance)} |"
        )

    rows.extend(
        [
            "",
            "## Dispatcher-Owned Climate Targets",
            "",
            "The planner receives these target values as read-only prompt context. They are derived from crop profiles and dispatcher policy, and the target delta columns are signed `actual - target` metrics for graphing and mode diagnosis.",
            "",
            "Controller priority is safety rails first, then temperature compliance, then VPD compliance, then resource use. When VPD is above the dispatcher-owned band and dew/occupancy rails are safe, resource minimization must not close wet/fog assist; ClimateIntent should make moisture assist available near the active VPD high edge until target deltas recover.",
            "",
        ]
    )
    if target:
        rows.extend(
            [
                "| Axis | Low | Target | High | Actual | Actual - target |",
                "|---|---:|---:|---:|---:|---:|",
                "| Temp F | "
                f"{_fmt_value(target.get('temp_low_f'))} | {_fmt_value(target.get('temp_target_f'))} | "
                f"{_fmt_value(target.get('temp_high_f'))} | {_fmt_value(target.get('temp_actual_f'))} | "
                f"{_fmt_value(target.get('temp_target_delta_f'))} |",
                "| VPD kPa | "
                f"{_fmt_value(target.get('vpd_low_kpa'))} | {_fmt_value(target.get('vpd_target_kpa'))} | "
                f"{_fmt_value(target.get('vpd_high_kpa'))} | {_fmt_value(target.get('vpd_actual_kpa'))} | "
                f"{_fmt_value(target.get('vpd_target_delta_kpa'))} |",
                "",
                f"Latest sample: `{target.get('reading_ts', '-')}`; mode `{target.get('greenhouse_mode', '-')}`.",
                "",
            ]
        )
    else:
        rows.extend(["Live target snapshot unavailable at generation time.", ""])
    return rows


def _render_contract_table(evidence: dict[str, Evidence], plan_required: set[str]) -> list[str]:
    rows = [
        "## ClimateIntent Materialization Contract",
        "",
        "Routine `set_plan` calls emit bounded `climate_intent` transitions. MCP validates that semantic surface, materializes it into the low-level Tier 1 rows below, and stores the original intent in `plan_journal.climate_intents`.",
        "",
        "| Parameter | Active | Future rows | Last dispatch | 7d confirmed | Planner instruction |",
        "|---|---:|---:|---|---:|---|",
    ]
    for name in sorted(plan_required):
        spec = REGISTRY[name]
        ev = evidence[name]
        confirmed = f"{ev.confirmed_7d}/{ev.dispatch_7d}" if ev.dispatch_7d else "-"
        rows.append(
            "| "
            f"`{name}` | {_fmt_value(ev.active_value)} | {ev.future_rows} | {_fmt_dt(ev.last_dispatch_ts)} | "
            f"{confirmed} | {_md(_planner_guidance(name, spec, plan_required))} |"
        )
    rows.append("")
    return rows


def _render_parameter_index(
    evidence: dict[str, Evidence],
    plan_required: set[str],
    firmware_hits: dict[str, dict[str, int]],
) -> list[str]:
    rows = [
        "## Parameter Index",
        "",
        "This is the public contract table for every registered tunable. The row-level implementation dump is still generated for operations, but the public page keeps the reading path compact.",
        "",
    ]
    grouped: dict[str, list[str]] = defaultdict(list)
    for name, spec in REGISTRY.items():
        grouped[_category(name, spec)].append(name)

    for category in sorted(grouped):
        rows.extend(
            [
                f"### {category}",
                "",
                "| Parameter | Class | Owner | Default / bounds | Active | Readback | Route | Clamps 30d | Planner status |",
                "|---|---|---|---|---:|---:|---|---:|---|",
            ]
        )
        for name in sorted(grouped[category]):
            spec = REGISTRY[name]
            ev = evidence[name]
            bounds = "switch 0/1" if spec.kind == "switch" else f"{_fmt_value(spec.min)} to {_fmt_value(spec.max)}"
            owner = spec.push_owner.replace("_", " ")
            hit_count = sum(firmware_hits.get(name, {}).values())
            route = _route_summary(name, spec, ev)
            if hit_count:
                route = f"{route}; firmware refs {hit_count}"
            clamps_cell = f"{ev.clamps_30d} ({_age(ev.latest_clamp_ts)})" if ev.clamps_30d else "-"
            rows.append(
                "| "
                f"`{name}` | `{spec.control_class}` | {owner} | default `{_fmt_value(spec.default)}`; {bounds} | "
                f"{_fmt_value(ev.active_value)} | {_fmt_value(ev.last_readback_value)} ({_age(ev.last_readback_ts)}) | "
                f"{route} | {clamps_cell} | {_planner_status(name, spec, plan_required)} |"
            )
        rows.append("")
    return rows


def _render_clamp_activity(evidence: dict[str, Evidence]) -> list[str]:
    """Surface dispatcher setpoint clamps so humans see what Iris asked for vs what landed.

    The dispatcher clamps planner-pushed setpoints against the band/guardrail surface
    each cycle (setpoint_clamps). Those events are fed to Iris through gather-plan-context
    but were previously invisible on the public contract page. This section makes the
    last-30-day clamp pressure legible: how often each parameter was clamped, the average
    requested vs applied value, the most recent reason, and recency.
    """
    clamped = sorted(
        (name for name, ev in evidence.items() if ev.clamps_30d),
        key=lambda name: evidence[name].clamps_30d,
        reverse=True,
    )
    rows = [
        "## Clamp Activity (last 30 days)",
        "",
        "When the AI planning agent pushes a setpoint outside the active band or a guardrail floor/ceiling, "
        "the dispatcher clamps it before it reaches the ESP32 and records the event in `setpoint_clamps`. "
        "A high clamp count means the planner is repeatedly asking for a value the band/guardrail layer will "
        "not honor; for moisture knobs that usually means aggressive VPD-hold misting is being capped. These "
        "rows are sourced from `setpoint_clamps` grouped by parameter; the dispatcher also feeds them to the "
        "planner each cycle through `scripts/gather-plan-context.sh`.",
        "",
    ]
    if not clamped:
        rows.extend(["No setpoint clamps recorded in the last 30 days.", ""])
        return rows
    rows.extend(
        [
            "| Parameter | Clamps 30d | Clamps 24h | Avg requested | Avg applied | Latest reason | Last clamp |",
            "|---|---:|---:|---:|---:|---|---|",
        ]
    )
    for name in clamped:
        ev = evidence[name]
        rows.append(
            "| "
            f"`{name}` | {ev.clamps_30d} | {ev.clamps_24h} | {_fmt_value(ev.clamp_avg_requested)} | "
            f"{_fmt_value(ev.clamp_avg_applied)} | {_md(ev.latest_clamp_reason)} | "
            f"{_fmt_dt(ev.latest_clamp_ts)} ({_age(ev.latest_clamp_ts)}) |"
        )
    rows.append("")
    return rows


def _render_tunable_detail(
    name: str,
    spec: TunableDef,
    ev: Evidence,
    plan_required: set[str],
    firmware_hits: dict[str, dict[str, int]],
) -> list[str]:
    category = _category(name, spec)
    owner = spec.push_owner.replace("_", " ")
    control_class = spec.control_class
    bounds = "switch 0/1" if spec.kind == "switch" else f"{_fmt_value(spec.min)} to {_fmt_value(spec.max)}"
    hits = firmware_hits.get(name, {})
    hit_text = ", ".join(f"{fname}:{count}" for fname, count in sorted(hits.items())) or "no token hit"
    readback_age = _age(ev.last_readback_ts)
    route_status = _effectiveness_status(name, spec, ev)
    score_text = "-"
    if ev.latest_rationale_plan:
        score = "-" if ev.latest_rationale_score is None else f"{ev.latest_rationale_score:g}"
        valid = (
            "pending"
            if ev.latest_rationale_validated_at is None
            else f"validated {_fmt_dt(ev.latest_rationale_validated_at)}"
        )
        score_text = f"`{ev.latest_rationale_plan}` ({valid}, score {score})"

    summary_status = "routine" if name in plan_required else control_class.replace("_", " ")
    if name in RESERVED_NO_EFFECT:
        summary_status = "reserved/no-op"

    lines = [
        f"<details{' open' if name in plan_required else ''}>",
        f"<summary><code>{name}</code> - {summary_status}</summary>",
        "",
        f"- **Purpose:** {_md(spec.notes or _why_it_matters(name, category))}",
        f"- **Why it matters:** {_md(_why_it_matters(name, category))}",
        f"- **Implementation:** {_md(_implementation_summary(name, spec, plan_required))}",
        f"- **Registry:** class `{control_class}`, kind `{spec.kind}`, owner `{owner}`, tier `{spec.tier}`, default `{_fmt_value(spec.default)}`, bounds `{bounds}`.",
        f"- **Firmware evidence:** `{hit_text}`.",
        f"- **Last used:** active `{_fmt_value(ev.active_value)}` from `{ev.active_plan_id or '-'}`; latest plan `{ev.last_plan_id or '-'}` at `{_fmt_dt(ev.last_plan_ts)}`; latest dispatch `{_fmt_value(ev.last_dispatch_value)}` at `{_fmt_dt(ev.last_dispatch_ts)}` from `{ev.last_dispatch_source or '-'}`.",
        f"- **Readback:** latest `{_fmt_value(ev.last_readback_value)}` at `{_fmt_dt(ev.last_readback_ts)}` ({readback_age} old); 7d readbacks `{ev.readbacks_7d}`.",
        f"- **Landing evidence:** 7d dispatcher writes `{ev.dispatch_7d}`, confirmed `{ev.confirmed_7d}`, unconfirmed `{ev.unconfirmed_7d}`, distinct 7d values `{ev.distinct_values_7d}`, 30d clamps `{ev.clamps_30d}`.",
        f"- **Effectiveness:** {_md(route_status)}",
        f"- **Latest plan rationale:** {score_text}; expected effect: {_md(ev.latest_rationale_expected)}.",
        f"- **Planner use:** {_md(_planner_guidance(name, spec, plan_required))}",
        "",
        "</details>",
        "",
    ]
    return lines


def _render_full_page(
    evidence: dict[str, Evidence],
    summary: dict[str, Any],
    plan_required: set[str],
    firmware_hits: dict[str, dict[str, int]],
) -> str:
    forecast_deviation_fires = summary.get("forecast_deviation_fires_14d")
    if forecast_deviation_fires is None:
        forecast_deviation_status = "fire status unavailable at generation time"
    elif forecast_deviation_fires:
        forecast_deviation_status = f"fired {forecast_deviation_fires}x in the last 14 days"
    else:
        forecast_deviation_status = "configured; not currently firing (0 ledger fires in the last 14 days)"
    lines: list[str] = [
        _frontmatter().rstrip(),
        "",
        "[//]: # (auto-generated by scripts/generate-ai-tunables-page.py; sources: tunable_registry, MCP sets, entity_map, firmware source, setpoint_plan, setpoint_changes, setpoint_snapshot, plan_journal)",
        "",
        "# Planner Contract and AI Tunables",
        "",
        "This is the canonical planner-control contract for Verdify. It explains what triggers a plan, what values the AI planning agent may write, how those writes publish to the public site, and what live readback evidence says about the bounded tunable surface.",
        "",
        "End-to-end path for planner-owned values:",
        "",
        "`AI planning agent -> MCP set_plan or set_tunable -> setpoint_plan -> v_active_plan -> ingestor dispatcher -> ESPHome number/switch -> firmware global/Setpoints -> cfg_* readback -> setpoint_snapshot and setpoint_changes confirmation`.",
        "",
        "The ESP32 still owns relay control. The AI planning agent owns bounded setpoint hypotheses, not direct actuator commands.",
        "",
        '<div class="data-table">',
        '  <div class="data-row"><strong>Related evidence</strong><span><a href="/reference/planning-loop/">Planning Loop</a> · <a href="/reference/safety/">Safety Architecture</a> · <a href="/data/operations/">Operations</a></span><p>This generated page owns triggers, accepted writes, publishing behavior, and per-parameter contract evidence. The planning page owns prompt flow, the safety page owns relay-boundary behavior, and Operations owns current live state.</p></div>',
        "</div>",
        "",
        "## Trigger Schedule",
        "",
        "Every expected trigger is materialized in `planner_trigger_ledger` before planner delivery. Required full-plan triggers must close with `set_plan`; tactical checkpoints may close with `set_tunable` or `acknowledge_trigger` when no change is warranted.",
        "",
        '<div class="data-table">',
        '  <div class="data-row"><strong><code>MIDNIGHT</code></strong><span>00:15 America/Denver</span><p>Required end-of-day review and reset. The AI planning agent evaluates prior-day plans, extracts supported lessons, and starts the new local day with <code>set_plan</code>.</p></div>',
        '  <div class="data-row"><strong><code>SUNRISE</code></strong><span>Astral sunrise</span><p>Required morning full plan for daylight, peak stress, decline, and evening handoff.</p></div>',
        '  <div class="data-row"><strong><code>SOLAR_MAX</code></strong><span>Astral solar noon</span><p>Solar checkpoint for a small tactical correction or honest no-change acknowledgement.</p></div>',
        '  <div class="data-row"><strong><code>TRANSITION</code></strong><span>Peak stress and decline</span><p>Bounded tactical checkpoint for the two highest-signal day transitions.</p></div>',
        '  <div class="data-row"><strong><code>SUNSET</code></strong><span>Astral sunset</span><p>Required evening full plan for overnight cold, humidity, dew point, and pre-dawn posture.</p></div>',
        '  <div class="data-row"><strong><code>FORECAST_DEVIATION</code></strong>'
        f"<span>Sigma-gated observed miss; {forecast_deviation_status}</span>"
        "<p>Triggered only when actual outdoor conditions diverge materially from forecast after cooldown "
        "and threshold checks. The ledger fire count is read live from <code>planner_trigger_ledger</code> "
        "so this label reflects whether the trigger is actually firing, not just configured.</p></div>",
        '  <div class="data-row"><strong><code>MANUAL</code></strong><span>Operator initiated</span><p>Ad-hoc audited planner run with the same MCP bounds and audit metadata as scheduled triggers.</p></div>',
        "</div>",
        "",
        "## Payload And Runtime Contract",
        "",
        "The planner receives one trigger-scoped payload through Hermes `/v1/runs`: standing directives, the event prompt, assembled greenhouse context, and audit metadata. The session id keeps the historical `hermes:iris:main:trigger:<trigger_id>` shape because it is a database/service key, not public planner branding.",
        "",
        '<div class="data-table">',
        '  <div class="data-row"><strong>Planner scheduler</strong><span><code>ingestor/tasks.py::planning_heartbeat</code></span><p>Computes expected trigger times, records them before delivery, dispatches the AI planning agent, and resolves SLA state.</p></div>',
        '  <div class="data-row"><strong>Prompt builder</strong><span><code>ingestor/iris_planner.py</code></span><p>Builds the event prompt, appends live and static context, stamps audit metadata, and posts to Hermes.</p></div>',
        '  <div class="data-row"><strong>Dynamic greenhouse packet</strong><span><code>scripts/gather-plan-context.sh</code></span><p>Live sensors, equipment, forecast, active plan, scorecards, plan-review backlog, lessons, alerts, tunable constraints, guardrail audits, and context completeness.</p></div>',
        '  <div class="data-row"><strong>Public site packet</strong><span><code>/srv/verdify/state/planner-static-context.md</code></span><p>Generated from the same Markdown source tree Quartz renders for <code>lab.verdify.ai</code>, with a SHA-256 digest embedded into planner context.</p></div>',
        "</div>",
        "",
        "## Accepted Writes And Publishing",
        "",
        '<div class="data-table">',
        '  <div class="data-row"><strong><code>set_plan</code></strong><span>Required for full-plan triggers</span><p>Validates the plan envelope, bounded <code>climate_intent</code>, bounds, trigger ID, planner instance, and structured hypothesis; materializes to <code>setpoint_plan</code> and audits semantic intent in <code>plan_journal</code>.</p></div>',
        '  <div class="data-row"><strong><code>set_tunable</code></strong><span>Narrow tactical correction</span><p>Validates one planner-pushable parameter against this registry and writes an audited one-shot setpoint row.</p></div>',
        '  <div class="data-row"><strong><code>acknowledge_trigger</code></strong><span>No-op closeout</span><p>Allowed for no-op transition, forecast-deviation, heartbeat, and validation-smoke events; rejected for normal required full-plan cycles.</p></div>',
        '  <div class="data-row"><strong><code>plan_evaluate</code></strong><span>Learning-loop closure</span><p>Writes outcome, score, anchor score, optional lesson extraction, and validation time back to <code>plan_journal</code>.</p></div>',
        '  <div class="data-row"><strong>Publishing</strong><span><code>publish-site-content.sh</code></span><p>MCP writes trigger generated plan pages, archive, forecast, lessons, tunables, baseline, evidence snapshots, public sample data, static planner context, and a Quartz rebuild.</p></div>',
        "</div>",
        "",
    ]
    lines.extend(_render_summary(summary, plan_required))
    lines.extend(_render_climate_intent_surface(summary))
    lines.extend(_render_contract_table(evidence, plan_required))
    lines.extend(
        [
            "## Findings That Matter",
            "",
            "- `mister_engage_kpa` is effectful, but it is not the state-machine entry trigger. Firmware enters humidification from `vpd_high` plus `vpd_watch_dwell_s`; `mister_engage_kpa` gates physical S1 mister pulses once `SEALED_MIST` or explicit `VENTILATE` assist creates humidity demand. Zone stress can choose the pulse target or satisfy the S1 stress check, but it cannot create a standalone mister mode.",
            "- `mister_all_kpa` controls physical all-zone mister rotation. The header mist-stage delay also uses `mister_all_delay_s`; fog escalation uses `fog_escalation_kpa` independently.",
            "- The planner tunes moisture intensity, not the crop band. In `VENTILATE`, dry outside air can keep temperature in band while pushing VPD high, so moisture thresholds must stay coupled to the active `vpd_high` unless dew-risk evidence justifies suppression.",
            "- High `mister_engage_kpa`/`mister_all_kpa` values are not resource-neutral during VPD-high stress; they can close the wet-assist path. Save them for dew, occupancy, irrigation, or water-cap constraints.",
            "- Reserved/no-op values are intentionally not planner-pushable: "
            + ", ".join(f"`{p}`" for p in sorted(RESERVED_NO_EFFECT))
            + ".",
            "- Readback-only values are now registry-covered but not planner-pushable: `fallback_window_s`, `outdoor_temp_f`, `outdoor_dewpoint_f`.",
            "",
        ]
    )
    lines.extend(_render_clamp_activity(evidence))
    lines.extend(_render_parameter_index(evidence, plan_required, firmware_hits))
    lines.extend(
        [
            "---",
            "",
            "*Regenerate with `scripts/generate-ai-tunables-page.py`; publish through `scripts/publish-site-content.sh` so the static context and public site stay aligned.*",
            "",
        ]
    )
    return "\n".join(lines)


def _render_detail_artifact(
    evidence: dict[str, Evidence],
    plan_required: set[str],
    firmware_hits: dict[str, dict[str, int]],
) -> str:
    lines = [
        "# Raw AI Tunables Detail",
        "",
        "Generated by `scripts/generate-ai-tunables-page.py` for operations. The public page uses the compact parameter index.",
        "",
        "## Per-Tunable Detail",
        "",
    ]

    grouped: dict[str, list[str]] = defaultdict(list)
    for name, spec in REGISTRY.items():
        grouped[_category(name, spec)].append(name)

    for category in sorted(grouped):
        lines.extend([f"### {category}", ""])
        for name in sorted(grouped[category]):
            lines.extend(_render_tunable_detail(name, REGISTRY[name], evidence[name], plan_required, firmware_hits))

    lines.extend(
        [
            "---",
            "",
            "*Regenerate with `scripts/generate-ai-tunables-page.py`; publish through `scripts/publish-site-content.sh` so the static context and public site stay aligned.*",
            "",
        ]
    )
    return "\n".join(lines)


def _render_planner_context(evidence: dict[str, Evidence], plan_required: set[str], summary: dict[str, Any]) -> str:
    target = _summary_object(summary.get("climate_target_context"))
    lines = [
        "--- TUNABLE TRACEABILITY BRIEF (generated; use before set_plan/set_tunable) ---",
        f"schema_tunables={len(ALL_TUNABLES)} registry_rows={len(REGISTRY)} routine_plan_required={len(plan_required)} planner_policy={len(PLANNER_PUSHABLE_REG)}",
        "contract_classes="
        + ", ".join(
            f"{control_class}:{sum(1 for value in TUNABLE_CONTRACT_CLASSES_REG.values() if value == control_class)}"
            for control_class in ("planner_policy", "crop_band", "controller_safety", "readback_context", "retired")
        ),
        f"future_active_rows={summary.get('future_rows', '-')} future_active_params={summary.get('future_params', '-')} reserved_active_rows={summary.get('reserved_active_rows', '-')}",
        "Full-plan surface: emit bounded `climate_intent` per transition. MCP materializes it once into the 39 Tier 1 rows below and audits the semantic intent.",
        "ClimateIntent must explicitly set every tactical field; dispatcher-owned temp/VPD low,target,high values are read-only prompt context, not AI-owned fields.",
        "Do not use reserved/no-op params: " + ", ".join(sorted(RESERVED_NO_EFFECT)),
        "mister_engage_kpa note: SEALED_MIST entry is vpd_high + vpd_watch_dwell_s; mister_engage_kpa gates physical S1 pulses once SEALED_MIST or explicit VENTILATE assist creates humidity demand.",
        "VPD-high guardrail: during live, near-edge, or recently unrecovered VENTILATE stress with healthy dew margin, keep moisture thresholds band-coupled (engage ~= vpd_high+0.05, all-zone ~= max(1.0,vpd_high+0.25), fog_escalation ~= 0.20 or 0.15 in hot/dry venting, shorter min_fog_off_s); dispatcher clamps conservative overrides until observed recovery.",
        "Planner moisture tuning ladder: open the band-coupled mister surface first; use all_zone_vpd_excess_kpa for distributed mister escalation; shorten mister_pulse_gap_s before increasing mister_pulse_on_s; use fog as the heavy 7x escalation path; treat mister_vpd_weight as zone selection, not total duty.",
        "",
        "climate_intent_field|bounds|meaning|firmware_impact|materialized_knobs|planner_guidance",
    ]
    for doc in CLIMATE_INTENT_FIELD_DOCS:
        lines.append(
            "|".join(
                [
                    doc.name,
                    doc.bounds,
                    doc.meaning,
                    doc.firmware_impact,
                    ",".join(doc.materialized_knobs) or "audit_context_only",
                    doc.planner_guidance,
                ]
            )
        )
    if target:
        lines.extend(
            [
                "",
                "dispatcher_owned_target_context|low|target|high|actual|actual_minus_target",
                "|".join(
                    [
                        "temp_f",
                        _fmt_value(target.get("temp_low_f")),
                        _fmt_value(target.get("temp_target_f")),
                        _fmt_value(target.get("temp_high_f")),
                        _fmt_value(target.get("temp_actual_f")),
                        _fmt_value(target.get("temp_target_delta_f")),
                    ]
                ),
                "|".join(
                    [
                        "vpd_kpa",
                        _fmt_value(target.get("vpd_low_kpa")),
                        _fmt_value(target.get("vpd_target_kpa")),
                        _fmt_value(target.get("vpd_high_kpa")),
                        _fmt_value(target.get("vpd_actual_kpa")),
                        _fmt_value(target.get("vpd_target_delta_kpa")),
                    ]
                ),
            ]
        )
    lines.extend(
        [
            "",
            "param|active|future_rows|last_dispatch|7d_confirmed|last_rationale|planner_use",
        ]
    )
    for name in sorted(plan_required):
        ev = evidence[name]
        spec = REGISTRY[name]
        confirmed = f"{ev.confirmed_7d}/{ev.dispatch_7d}" if ev.dispatch_7d else "-"
        rationale = ev.latest_rationale_plan or "-"
        lines.append(
            "|".join(
                [
                    name,
                    _fmt_value(ev.active_value),
                    str(ev.future_rows),
                    _fmt_dt(ev.last_dispatch_ts),
                    confirmed,
                    rationale,
                    _planner_guidance(name, spec, plan_required),
                ]
            )
        )
    lines.append("")
    return "\n".join(lines)


async def _build() -> tuple[str, str, str]:
    plan_required = _assigned_set(REPO_ROOT / "mcp" / "server.py", "PLAN_REQUIRED_PARAMS")
    missing_registry = sorted(set(ALL_TUNABLES) - set(REGISTRY))
    if missing_registry:
        raise RuntimeError(f"ALL_TUNABLES missing from REGISTRY: {missing_registry}")
    params = sorted(REGISTRY)
    evidence = await _load_evidence(params)
    summary = await _load_summary()
    firmware_hits = _static_firmware_hits()
    page = _render_full_page(evidence, summary, plan_required, firmware_hits)
    detail_artifact = _render_detail_artifact(evidence, plan_required, firmware_hits)
    planner_context = _render_planner_context(evidence, plan_required, summary)
    return page, planner_context, detail_artifact


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--planner-context", action="store_true", help="print compact planner-context brief only")
    parser.add_argument("--stdout", action="store_true", help="print generated page instead of writing it")
    parser.add_argument(
        "--check", action="store_true", help="validate that the generated page exists and has core sections"
    )
    args = parser.parse_args()

    page, planner_context, detail_artifact = asyncio.run(_build())
    if args.planner_context:
        print(planner_context)
        return 0
    if args.stdout:
        print(page)
        return 0

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RAW_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    if args.check:
        existing = OUTPUT_PATH.read_text(encoding="utf-8") if OUTPUT_PATH.exists() else ""
        required = [
            "auto-generated by scripts/generate-ai-tunables-page.py",
            "## Current Audit Snapshot",
            "## ClimateIntent AI Surface",
            "## Dispatcher-Owned Climate Targets",
            "## ClimateIntent Materialization Contract",
            "## Clamp Activity (last 30 days)",
            "## Parameter Index",
            "`mister_engage_kpa` is effectful",
            "`fallback_window_s`",
        ]
        missing = [marker for marker in required if marker not in existing]
        if missing:
            print(f"{OUTPUT_PATH} is missing generated markers/sections: {missing}", file=sys.stderr)
            return 1
        return 0

    OUTPUT_PATH.write_text(page, encoding="utf-8")
    RAW_OUTPUT_PATH.write_text(detail_artifact, encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH}")
    print(f"Wrote {RAW_OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
