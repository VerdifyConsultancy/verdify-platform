#!/usr/bin/env python3
"""Audit lighting automation traceability from planner policy to public graphs.

This script is intentionally stricter than a unit test. Static checks prove the
source tree still contains the expected per-circuit wiring. Live checks prove the
running system exposes the same policy, dashboard, and setpoint surfaces. Until
the ESP32 OTA lands, the live audit should report BLOCKED rather than COMPLETE.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
VAULT_LIGHTING = Path("/mnt/iris/verdify-vault/website/greenhouse/lighting.md")
PUBLIC_LIGHTING = REPO_ROOT / "verdify-site" / "public" / "greenhouse" / "lighting.html"
PUBLIC_HOME = REPO_ROOT / "verdify-site" / "public" / "index.html"
GRAFANA_RENDER_BASE = "https://graphs.verdify.ai"
PUBLIC_SITE_BASE = "https://lab.verdify.ai"
HOMEPAGE_LIGHTING_THRESHOLD_SERIES = (
    "Main Light Threshold",
    "Main Light Threshold Hold",
    "Grow Light Threshold",
    "Grow Light Threshold Hold",
)
HOMEPAGE_LIGHTING_THRESHOLD_OUTPUT_TOKENS = tuple(
    f"'{label}'::text" for label in HOMEPAGE_LIGHTING_THRESHOLD_SERIES
) + ("SELECT time, metric, value FROM thresholds",)

PER_CIRCUIT_PARAMS = (
    "gl_main_target_light_minutes",
    "gl_main_lux_threshold",
    "gl_main_lux_hysteresis",
    "gl_main_sunrise_hour",
    "gl_main_sunset_hour",
    "gl_main_min_on_s",
    "gl_main_min_off_s",
    "sw_gl_main_auto_mode",
    "gl_grow_target_light_minutes",
    "gl_grow_lux_threshold",
    "gl_grow_lux_hysteresis",
    "gl_grow_sunrise_hour",
    "gl_grow_sunset_hour",
    "gl_grow_min_on_s",
    "gl_grow_min_off_s",
    "sw_gl_grow_auto_mode",
)
CFG_READBACK_PARAMS = (
    "gl_main_target_light_minutes",
    "gl_main_lux_threshold",
    "gl_main_lux_hysteresis",
    "gl_main_sunrise_hour",
    "gl_main_sunset_hour",
    "gl_main_min_on_s",
    "gl_main_min_off_s",
    "sw_gl_main_auto_mode",
    "gl_grow_target_light_minutes",
    "gl_grow_lux_threshold",
    "gl_grow_lux_hysteresis",
    "gl_grow_sunrise_hour",
    "gl_grow_sunset_hour",
    "gl_grow_min_on_s",
    "gl_grow_min_off_s",
    "sw_gl_grow_auto_mode",
)


@dataclass
class Check:
    name: str
    status: str
    detail: str


class Audit:
    def __init__(self) -> None:
        self.checks: list[Check] = []

    def add(self, name: str, status: str, detail: str) -> None:
        self.checks.append(Check(name, status, detail))

    def check(self, condition: bool, name: str, ok: str, fail: str, status: str = "FAIL") -> None:
        self.add(name, "PASS" if condition else status, ok if condition else fail)

    def exit_code(self, allow_blocked: bool) -> int:
        if any(c.status == "FAIL" for c in self.checks):
            return 1
        if not allow_blocked and any(c.status == "BLOCKED" for c in self.checks):
            return 2
        return 0

    def print_text(self) -> None:
        for check in self.checks:
            print(f"{check.status:7} {check.name}: {check.detail}")
        counts = {
            status: sum(c.status == status for c in self.checks) for status in ("PASS", "WARN", "BLOCKED", "FAIL")
        }
        print("summary: " + ", ".join(f"{status.lower()}={count}" for status, count in counts.items() if count))

    def print_json(self) -> None:
        print(json.dumps([check.__dict__ for check in self.checks], indent=2))


def read(path: Path) -> str:
    # Issue #46 split ingestor/tasks.py into the ingestor/tasks/ package. Reading
    # the legacy single-file path returns the concatenated package source so the
    # source-string audits below keep matching wherever the code now lives.
    if path.name == "tasks.py" and path.parent.name == "ingestor" and not path.exists():
        pkg = path.parent / "tasks"
        if pkg.is_dir():
            return "\n".join(p.read_text(encoding="utf-8") for p in sorted(pkg.glob("*.py")))
    return path.read_text(encoding="utf-8")


def run(cmd: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def psql_json(sql: str, timeout: int = 30) -> list[dict[str, Any]]:
    wrapped = f"SELECT COALESCE(json_agg(row_to_json(q)), '[]'::json) FROM ({sql}) q"
    proc = run(
        [
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
            "-c",
            wrapped,
        ],
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())
    return json.loads(proc.stdout.strip() or "[]")


def parse_setpoint_text(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def fetch_text(url: str, timeout: int = 10) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "verdify-lighting-audit/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8")


def grafana_dashboard(uid: str) -> dict[str, Any]:
    proc = run(
        [
            "docker",
            "exec",
            "verdify-grafana",
            "curl",
            "-fsS",
            f"http://localhost:3000/api/dashboards/uid/{uid}",
        ],
        timeout=15,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())
    return json.loads(proc.stdout)["dashboard"]


def mcp_set_tunable_dry_run() -> tuple[bool, str]:
    """Prove the MCP allowlist without writing a setpoint.

    A random trigger_id that is not in plan_delivery_log makes allowed params
    reach the trigger ledger check and return "trigger_id not found" before any
    INSERT. Disallowed params return at the registry gate before opening DB.
    """
    spec = importlib.util.spec_from_file_location("verdify_mcp_server_audit", REPO_ROOT / "mcp" / "server.py")
    if spec is None or spec.loader is None:
        return False, "could not load mcp/server.py"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fake_trigger = str(uuid4())

    async def _calls() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
        minutes = json.loads(
            await module.set_tunable(
                "gl_main_target_light_minutes",
                960,
                "lighting audit dry-run",
                trigger_id=fake_trigger,
                planner_instance="local",
            )
        )
        main = json.loads(
            await module.set_tunable(
                "gl_main_lux_threshold",
                41000,
                "lighting audit dry-run",
                trigger_id=fake_trigger,
                planner_instance="local",
            )
        )
        auto = json.loads(
            await module.set_tunable(
                "sw_gl_main_auto_mode",
                1,
                "lighting audit dry-run",
                trigger_id=fake_trigger,
                planner_instance="local",
            )
        )
        legacy = json.loads(
            await module.set_tunable(
                "gl_lux_threshold",
                41000,
                "lighting audit dry-run",
                trigger_id=fake_trigger,
                planner_instance="local",
            )
        )
        return minutes, main, auto, legacy

    minutes, main, auto, legacy = asyncio.run(_calls())
    allowed = set(legacy.get("allowed", []))
    ok = (
        minutes.get("error") == "trigger_id not found in plan_delivery_log"
        and main.get("error") == "trigger_id not found in plan_delivery_log"
        and auto.get("error") == "trigger_id not found in plan_delivery_log"
        and "not planner-pushable" in legacy.get("error", "")
        and "gl_main_target_light_minutes" in allowed
        and "gl_main_lux_threshold" in allowed
        and "sw_gl_main_auto_mode" in allowed
        and "gl_lux_threshold" not in allowed
    )
    detail = (
        f"gl_main_target_light_minutes={minutes.get('error')}; "
        f"gl_main_lux_threshold={main.get('error')}; "
        f"sw_gl_main_auto_mode={auto.get('error')}; "
        f"gl_lux_threshold={legacy.get('error')}"
    )
    return ok, detail


def render_panel(uid: str, panel_id: int, width: int, height: int, timeout: int = 45) -> tuple[bool, str]:
    url = (
        f"{GRAFANA_RENDER_BASE}/render/d-solo/{quote(uid)}/"
        f"?orgId=1&panelId={panel_id}&from=now-24h&to=now%2B48h"
        f"&width={width}&height={height}&theme=light"
    )
    start = time.monotonic()
    request = urllib.request.Request(url, headers={"User-Agent": "verdify-lighting-audit/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            elapsed_ms = int((time.monotonic() - start) * 1000)
            content_type = response.headers.get("content-type", "")
            if response.status == 200 and body.startswith(b"\x89PNG") and len(body) >= 5000:
                return True, f"HTTP 200 PNG {len(body)} bytes in {elapsed_ms}ms"
            return False, f"HTTP {response.status} {content_type} {len(body)} bytes in {elapsed_ms}ms"
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return False, f"{type(exc).__name__}: {exc} after {elapsed_ms}ms"


def panel_by_id(dashboard: dict[str, Any], panel_id: int) -> dict[str, Any] | None:
    for panel in dashboard.get("panels", []):
        if panel.get("id") == panel_id:
            return panel
    return None


def panel_sql(panel: dict[str, Any] | None) -> str:
    if not panel:
        return ""
    return "\n".join(str(target.get("rawSql", "")) for target in panel.get("targets", []))


def static_checks(audit: Audit) -> None:
    from verdify_schemas.tunable_registry import PLANNER_PUSHABLE_REG, REGISTRY

    logic = read(REPO_ROOT / "firmware" / "lib" / "greenhouse_logic.h")
    types = read(REPO_ROOT / "firmware" / "lib" / "greenhouse_types.h")
    controls = read(REPO_ROOT / "firmware" / "greenhouse" / "controls.yaml")
    tunables = read(REPO_ROOT / "firmware" / "greenhouse" / "tunables.yaml")
    sensors = read(REPO_ROOT / "firmware" / "greenhouse" / "sensors.yaml")
    hardware = read(REPO_ROOT / "firmware" / "greenhouse" / "hardware.yaml")

    audit.check(
        all(token in types for token in ("LightingInputs", "LightingSetpoints", "LightingState"))
        and "evaluate_lighting" in logic,
        "firmware lighting state-machine types",
        "LightingInputs/Setpoints/State and evaluate_lighting are present",
        "missing lighting state-machine types or evaluator",
    )
    audit.check(
        all(token in controls for token in ("gl_main_state", "gl_grow_state", "grow_light_main", "grow_light_grow")),
        "firmware per-circuit state instances",
        "main and grow circuits are evaluated separately",
        "controls.yaml does not clearly evaluate both light circuits separately",
    )
    audit.check(
        all(
            token in (logic + types)
            for token in (
                "exterior_lux_fresh",
                "exterior_lux_available",
                "occupancy_task_light_demand",
                "plant_supplement_demand",
                "occupancy_lux_low",
                "occupancy_hysteresis_hold",
                "occupancy_lux_unavailable",
                "plant_lux_low",
                "plant_hysteresis_hold",
            )
        )
        and "&& in.occupied" in logic
        and "&& exterior_lux_available" in logic
        and "&& in_window" in logic,
        "firmware occupancy task-light contract",
        "occupancy is a fresh-exterior-lux-gated demand split from plant supplementation",
        "firmware still lacks explicit lux-gated occupancy and plant demand separation",
    )
    audit.check(
        "evaluate 5s; publish 60s/change" in controls
        and "lighting_dt_s" in controls
        and ">= 60000" in controls
        and "main_changed" in controls
        and "grow_changed" in controls
        and "tempest_age_s <= (uint32_t)id(outdoor_staleness_max_s)" in controls,
        "firmware lighting latency contract",
        "lighting evaluates every 5s while telemetry publishes on change or 60s heartbeat",
        "controls.yaml still appears to gate lighting decisions on a 60s-only loop or lacks exterior lux freshness",
    )
    audit.check(
        all(
            param.replace("sw_", "").replace("_mode", "") in tunables or param in tunables
            for param in PER_CIRCUIT_PARAMS
        ),
        "firmware per-circuit tunables",
        "all per-circuit lighting params have ESPHome tunable surfaces",
        "one or more per-circuit lighting tunables are missing from firmware/greenhouse/tunables.yaml",
    )
    audit.check(
        all(
            f"cfg_{param}" in sensors or param.startswith("sw_") and f"cfg_{param[3:]}" in sensors
            for param in CFG_READBACK_PARAMS
        ),
        "firmware cfg readbacks",
        "all per-circuit lighting params have cfg_* readback sensors",
        "one or more per-circuit lighting cfg_* readbacks are missing",
    )
    # Per-circuit lighting is schedule-layer owned (the band/schedule writer drives
    # these from fn_lighting_minutes_policy; Iris pushes them 0x in the live DB).
    # They were reclassified out of the planner-pushable surface into
    # scheduled_policy (push_owner='schedule', planner_pushable=False) but keep
    # their Tier 2 standing and cfg_* readbacks for traceability.
    registry_missing = sorted(param for param in PER_CIRCUIT_PARAMS if param not in REGISTRY)
    registry_bad = sorted(
        param
        for param in PER_CIRCUIT_PARAMS
        if param in REGISTRY
        and (
            REGISTRY[param].push_owner != "schedule"
            or REGISTRY[param].planner_pushable
            or param in PLANNER_PUSHABLE_REG
            or REGISTRY[param].control_class != "scheduled_policy"
            or REGISTRY[param].tier != 2
            or not REGISTRY[param].cfg_readback_object_id
        )
    )
    audit.check(
        not registry_missing and not registry_bad,
        "tunable registry per-circuit lighting contract",
        "per-circuit lighting params are schedule-owned scheduled_policy Tier 2 tunables with cfg readbacks (not planner-pushable)",
        f"registry missing={registry_missing} bad={registry_bad}",
    )
    legacy_lighting_params = ("gl_lux_threshold", "gl_lux_hysteresis")
    legacy_bad = sorted(
        param
        for param in legacy_lighting_params
        if param not in REGISTRY
        or REGISTRY[param].planner_pushable
        or param in PLANNER_PUSHABLE_REG
        or REGISTRY[param].push_owner != "dispatcher_default"
    )
    audit.check(
        not legacy_bad,
        "legacy shared lighting params are read-only",
        "legacy shared gl_lux_* params are dispatcher/default context; planner writes use per-circuit gl_main_* and gl_grow_*",
        f"legacy shared lighting params are still planner-writable or mis-owned: {legacy_bad}",
    )
    legacy_dli_params = ("gl_main_dli_target", "gl_grow_dli_target")
    legacy_dli_bad = sorted(
        param
        for param in legacy_dli_params
        if param not in REGISTRY
        or REGISTRY[param].planner_pushable
        or param in PLANNER_PUSHABLE_REG
        or REGISTRY[param].push_owner != "dispatcher_default"
    )
    audit.check(
        not legacy_dli_bad,
        "legacy per-circuit DLI params are telemetry-only",
        "legacy gl_main/gl_grow DLI targets are dispatcher compatibility values; target minutes are the planner control goal",
        f"legacy per-circuit DLI params are still planner-writable or mis-owned: {legacy_dli_bad}",
    )
    audit.check(
        all(
            token in hardware
            for token in ("gl_main_state_text", "gl_main_reason", "gl_grow_state_text", "gl_grow_reason")
        ),
        "firmware lighting diagnostics",
        "state/reason text sensors exist for both circuits",
        "missing per-circuit lighting state/reason text sensors",
    )
    audit.check(
        "Full Unix epochs exceed ESPHome sensor float precision" in sensors
        and "return (float)now.timestamp" not in sensors
        and "publish_lighting_epoch(id(gl_main_decision_epoch), time.timestamp)" in controls
        and "publish_lighting_epoch(id(gl_grow_decision_epoch), time.timestamp)" in controls,
        "firmware exact epoch diagnostics",
        "full Unix epochs are published as text_sensor strings, not float sensors",
        "one or more full epoch diagnostics still use ESPHome float sensor publishing",
    )

    ingestor_path = str(REPO_ROOT / "ingestor")
    if ingestor_path not in sys.path:
        sys.path.insert(0, ingestor_path)
    from entity_map import CFG_READBACK_MAP, SETPOINT_MAP, STATE_MAP

    audit.check(
        all(param in set(SETPOINT_MAP.values()) for param in PER_CIRCUIT_PARAMS)
        and all(param in set(CFG_READBACK_MAP.values()) for param in CFG_READBACK_PARAMS)
        and {"gl_main_state", "gl_main_reason", "gl_grow_state", "gl_grow_reason"} <= set(STATE_MAP),
        "ingestor entity map",
        "setpoint, readback, and state routes are mapped",
        "entity_map runtime maps are missing per-circuit lighting routes",
    )
    setpoint_server = read(REPO_ROOT / "scripts" / "setpoint-server.py")
    ha_sync = read(REPO_ROOT / "scripts" / "ha-sensor-sync.py")
    tasks_src = read(REPO_ROOT / "ingestor" / "tasks.py")
    lutron_sources = (setpoint_server, ha_sync, tasks_src)
    audit.check(
        all('"switch.greenhouse_main": "grow_light_main"' in src for src in (ha_sync, tasks_src))
        and all('"switch.greenhouse_grow": "grow_light_grow"' in src for src in (ha_sync, tasks_src))
        and '"main": {"ha_entity": "switch.greenhouse_main", "equipment": "grow_light_main"}' in setpoint_server
        and '"grow": {"ha_entity": "switch.greenhouse_grow", "equipment": "grow_light_grow"}' in setpoint_server
        and all(
            '"light.greenhouse_main"' not in src and '"light.greenhouse_grow"' not in src for src in lutron_sources
        ),
        "Lutron switch enforcement path",
        "setpoint server and sync jobs use real switch.greenhouse_* Lutron entities and avoid stale light.* wrappers",
        "Lutron control/sync path is missing switch.greenhouse_* mappings or still references stale light.* wrappers",
    )

    for relpath in ("ingestor/tasks.py", "api/main.py", "scripts/setpoint-server.py", "scripts/gather-plan-context.sh"):
        src = read(REPO_ROOT / relpath)
        audit.check(
            "fn_lighting_minutes_policy" in src,
            f"{relpath} policy source",
            "uses fn_lighting_minutes_policy",
            "does not use fn_lighting_minutes_policy",
        )

    migration = read(REPO_ROOT / "db" / "migrations" / "123-lighting-per-circuit-state-machines.sql")
    minutes_migration = read(REPO_ROOT / "db" / "migrations" / "126-lighting-qualified-minutes.sql")
    traceability_migration = read(REPO_ROOT / "db" / "migrations" / "128-lighting-traceability-confirmed-state.sql")
    live_fixes_migration = read(REPO_ROOT / "db" / "migrations" / "125-lighting-live-fixes.sql")
    timeline_migration = read(REPO_ROOT / "db" / "migrations" / "127-lighting-timeline-qualified-minutes.sql")
    occupancy_migration = read(REPO_ROOT / "db" / "migrations" / "135-lighting-occupancy-task-demand.sql")
    audit.check(
        all(
            token in (minutes_migration + traceability_migration + occupancy_migration)
            for token in (
                "fn_lighting_minutes_policy",
                "v_lighting_minutes_status_now",
                "v_lighting_traceability_now",
                "v_lighting_qualified_minutes_daily",
            )
        ),
        "database lighting traceability surfaces",
        "qualified-minutes policy, status, and daily DB surfaces are defined",
        "migration 126 is missing one or more qualified-minutes lighting surfaces",
    )
    recommendation_migration = read(REPO_ROOT / "db" / "migrations" / "122-lighting-lux-threshold-recommendation.sql")
    audit.check(
        "AND COALESCE(source, '') <> 'esp32'" in migration
        and "AND COALESCE(source, '') <> 'esp32'" in recommendation_migration
        and "per-circuit gl_main_*/gl_grow_* lux tunables" in recommendation_migration,
        "lighting policy source-of-truth guard",
        "planner/default rows feed policy; ESP32 readbacks are excluded from policy and recommendation current values",
        "lighting policy/recommendation can still treat ESP32 cfg readbacks as authoritative setpoints",
    )
    audit.check(
        "qualified minute = exterior/natural lux" in minutes_migration
        and "COALESCE(t.qualified_light_minutes, 0) < p.target_light_minutes" in occupancy_migration
        and "me.natural_qualified OR me.switch_on" in occupancy_migration
        and "plant_supplement_demand" in occupancy_migration
        and "occupancy_lux_demand" in occupancy_migration
        and "exterior_lux_fresh" in occupancy_migration
        and "expected_on" in occupancy_migration
        and "j.in_light_window" in occupancy_migration
        and "j.occupancy_active" in occupancy_migration
        and "CREATE OR REPLACE FUNCTION fn_lighting_timeline" in timeline_migration
        and "fn_lighting_minutes_policy((SELECT now_ts FROM bounds), p_greenhouse_id)" in timeline_migration
        and "main_pre_minutes < r.row_main_target_light_minutes" in timeline_migration
        and "grow_pre_minutes < r.row_grow_target_light_minutes" in timeline_migration
        and "main_natural_qualified OR main_on" in timeline_migration
        and "grow_natural_qualified OR grow_on" in timeline_migration
        and "legacy DLI target columns remain compatibility-only" in timeline_migration
        and "dli_today <" not in timeline_migration,
        "lighting graph hysteresis contract",
        "status values expose plant demand, lux-gated occupancy demand, qualified minutes, and ON/OFF hysteresis gates",
        "status/timeline expected-on values do not match firmware hysteresis semantics",
    )
    audit.check(
        "firmware_telemetry_fresh" in live_fixes_migration
        and "current_firmware_start" in live_fixes_migration
        and "state_row.ts >= COALESCE((SELECT ts FROM current_firmware_start)" in live_fixes_migration
        and "CASE WHEN j.firmware_telemetry_fresh THEN j.firmware_state_raw END AS firmware_state"
        in live_fixes_migration
        and "cycles count TRUE rising edges only" in live_fixes_migration,
        "lighting rollback freshness guard",
        "status views suppress stale firmware telemetry after rollback and runtime cycles count rising edges",
        "live lighting views can still present stale firmware text or duplicate TRUE rows as fresh evidence",
    )
    audit.check(
        "fn_lighting_minutes_policy((SELECT now_ts FROM bounds), p_greenhouse_id)" in timeline_migration
        and "fn_lighting_minutes_policy(t.ts" not in timeline_migration
        and "fn_lighting_circuit_policy(t.ts" not in timeline_migration,
        "lighting timeline performance guard",
        "timeline resolves lighting minutes policy once instead of once per graph bucket",
        "timeline performance migration may call policy once per bucket",
    )

    site_home = json.loads(read(REPO_ROOT / "grafana" / "dashboards" / "site-home.json"))
    site_climate_lighting = json.loads(read(REPO_ROOT / "grafana" / "dashboards" / "site-climate-lighting.json"))
    home_panel = panel_by_id(site_home, 36)
    policy_panel = panel_by_id(site_climate_lighting, 16)
    forecast_panel = panel_by_id(site_climate_lighting, 17)
    home_panel_sql = panel_sql(home_panel) if home_panel else ""
    audit.check(
        home_panel
        and home_panel.get("title") == "Lighting: Lux & Switch State"
        and "setpoint_snapshot" in home_panel_sql
        and "setpoint_changes" in home_panel_sql
        and "'Solar'::text AS metric" in home_panel_sql
        and "'Solar Forecast'::text AS metric" in home_panel_sql
        and "weather_forecast" in home_panel_sql
        and "equipment_state" in home_panel_sql
        and "fn_lighting_timeline" not in home_panel_sql
        and not any(token in home_panel_sql for token in HOMEPAGE_LIGHTING_THRESHOLD_OUTPUT_TOKENS),
        "home lighting state graph",
        "site-home panel 36 renders observed/forecast solar lux and readback-positioned switch ON fades without threshold output",
        "site-home panel 36 is missing, stale, renders threshold bands, or still uses the heavy lighting timeline",
    )
    audit.check(
        policy_panel
        and "v_lighting_traceability_now" in panel_sql(policy_panel)
        and "occupancy_lux_demand" in panel_sql(policy_panel)
        and "plant_supplement_demand" in panel_sql(policy_panel),
        "lighting policy table graph",
        "site-climate-lighting panel 16 queries split plant/occupancy demand from v_lighting_traceability_now",
        "site-climate-lighting panel 16 is missing the per-circuit status view",
    )
    audit.check(
        forecast_panel and "fn_lighting_timeline" in panel_sql(forecast_panel),
        "lighting forecast-band graph",
        "site-climate-lighting panel 17 queries fn_lighting_timeline",
        "site-climate-lighting panel 17 is missing fn_lighting_timeline",
    )
    home_panel_contract = json.dumps(home_panel)
    forecast_panel_contract = json.dumps(forecast_panel)
    home_state_tokens = (
        "Solar",
        "Solar Forecast",
        "Main Light On",
        "Grow Light On",
        "setpoint_snapshot",
        "setpoint_changes",
        "equipment_state",
        "weather_forecast",
        "axisPlacement",
        "custom.gradientMode",
        "opacity",
    )
    forecast_label_tokens = (
        "Tempest/Forecast Lux",
        "Main ON Threshold",
        "Main OFF Threshold",
        "Grow ON Threshold",
        "Grow OFF Threshold",
        "Main Expected On",
        "Grow Expected On",
        "custom.fillBelowTo",
    )
    audit.check(
        home_panel
        and forecast_panel
        and all(token in home_panel_contract for token in home_state_tokens)
        and not any(label in home_panel_contract for label in HOMEPAGE_LIGHTING_THRESHOLD_SERIES)
        and all(token in forecast_panel_contract for token in forecast_label_tokens),
        "lighting state graph labels and fills",
        "home graph shows only observed/forecast solar and faded actual ON windows; detailed forecast graph retains hysteresis bands",
        "home state-only or detailed forecast-band graph contracts are missing or mixed together",
    )

    greenhouse_lighting = json.loads(
        read(REPO_ROOT / "grafana" / "provisioning" / "dashboards" / "json" / "lighting.json")
    )
    titles = {panel.get("title") for panel in greenhouse_lighting.get("panels", [])}
    audit.check(
        {"Main Light Circuit", "Lutron Circuit State", "Lighting Decision Context", "Daily Lutron Circuit Runtime"}
        <= titles,
        "legacy lighting dashboard labels",
        "greenhouse-lighting dashboard labels are circuit-aware",
        "greenhouse-lighting still has stale single-light labels",
    )

    audit.check(
        PUBLIC_HOME.exists() and "panelId=36" in read(PUBLIC_HOME),
        "built home page embed",
        "public home HTML embeds lighting state panel 36",
        "public home HTML is missing lighting state panel 36",
        status="WARN",
    )
    audit.check(
        PUBLIC_LIGHTING.exists()
        and "Circuit Policy And Forecast Bands" in read(PUBLIC_LIGHTING)
        and "panelId=17" in read(PUBLIC_LIGHTING),
        "built greenhouse lighting page",
        "public lighting HTML embeds per-circuit policy and forecast panels",
        "public lighting HTML is missing per-circuit lighting evidence",
        status="WARN",
    )
    audit.check(
        VAULT_LIGHTING.exists()
        and "Circuit Policy And Forecast Bands" in read(VAULT_LIGHTING)
        and "panelId=17" in read(VAULT_LIGHTING),
        "vault greenhouse lighting page",
        "vault source explains per-circuit policy and forecast bands",
        "vault source is missing per-circuit lighting page copy",
        status="WARN",
    )


def live_checks(audit: Audit, require_ota: bool) -> None:
    from verdify_schemas.tunable_registry import PLANNER_PUSHABLE_REG

    services = run(
        ["systemctl", "is-active", "verdify-ingestor.service", "verdify-mcp.service", "verdify-setpoint-server.service"]
    )
    audit.check(
        services.returncode == 0,
        "systemd lighting services",
        "ingestor, MCP, and setpoint server are active",
        services.stdout.strip() or services.stderr.strip(),
    )
    docker_state = run(["docker", "inspect", "-f", "{{.State.Status}}", "verdify-api", "verdify-grafana"])
    audit.check(
        docker_state.returncode == 0 and set(docker_state.stdout.split()) == {"running"},
        "api and grafana containers",
        "verdify-api and verdify-grafana are running",
        docker_state.stdout.strip() or docker_state.stderr.strip(),
    )

    bad_alerts = psql_json(
        """
        SELECT severity, count(*)::int AS count
          FROM alert_log
         WHERE disposition IN ('open','acknowledged')
           AND resolved_at IS NULL
           AND severity IN ('critical','high')
         GROUP BY severity
        """
    )
    audit.check(
        not bad_alerts,
        "critical/legacy-high alert gate",
        "no open critical/legacy-high alerts",
        json.dumps(bad_alerts),
    )

    policy = psql_json(
        """
        SELECT light_key, equipment, target_light_minutes, start_hour, cutoff_hour,
               lux_on_threshold, lux_off_threshold, min_on_s, min_off_s, auto_enabled
          FROM fn_lighting_minutes_policy(now(), 'vallery')
         ORDER BY light_key
        """
    )
    keys = {row["light_key"]: row for row in policy}
    audit.check(
        set(keys) == {"grow", "main"} and all(row["lux_off_threshold"] >= row["lux_on_threshold"] for row in policy),
        "live per-circuit policy",
        f"{[(r['light_key'], r['equipment'], r['lux_on_threshold'], r['lux_off_threshold']) for r in policy]}",
        f"unexpected policy rows: {policy}",
    )

    recommendation = psql_json(
        """
        SELECT current_gl_lux_threshold,
               current_gl_lux_hysteresis,
               source_chain
          FROM fn_lighting_lux_threshold_recommendation(now(), 'vallery')
        """
    )[0]
    audit.check(
        recommendation["current_gl_lux_threshold"] == keys["main"]["lux_on_threshold"]
        and recommendation["current_gl_lux_hysteresis"]
        == keys["main"]["lux_off_threshold"] - keys["main"]["lux_on_threshold"]
        and "per-circuit gl_main_*/gl_grow_* lux tunables" in recommendation["source_chain"],
        "live lighting recommendation current values",
        json.dumps(recommendation, sort_keys=True),
        f"recommendation current values do not match active per-circuit policy: {recommendation} policy={policy}",
    )

    status = psql_json(
        """
        SELECT light_key, expected_on, actual_on, natural_lux,
               occupancy_active, exterior_lux, exterior_lux_fresh,
               occupancy_lux_demand, plant_supplement_demand,
               qualified_light_minutes, remaining_light_minutes,
               lux_on_threshold, lux_off_threshold,
               COALESCE(firmware_state, '') AS firmware_state,
               COALESCE(firmware_reason, '') AS firmware_reason,
               COALESCE(firmware_decision_fresh, firmware_telemetry_fresh, false) AS firmware_telemetry_fresh,
               policy_matches_cfg
          FROM v_lighting_traceability_now
         ORDER BY light_key
        """
    )
    audit.check(
        {row["light_key"] for row in status} == {"grow", "main"},
        "live per-circuit status view",
        "v_lighting_traceability_now returns main and grow rows",
        f"unexpected status rows: {status}",
    )
    audit.check(
        all(
            row.get("occupancy_active") is not None
            and "exterior_lux" in row
            and row.get("exterior_lux_fresh") is not None
            and row.get("occupancy_lux_demand") is not None
            and row.get("plant_supplement_demand") is not None
            for row in status
        ),
        "live occupancy lighting demand fields",
        "traceability exposes occupancy, exterior lux freshness, and split demand booleans",
        f"missing split demand fields in status rows: {status}",
    )
    telemetry_live = all(
        row.get("firmware_telemetry_fresh") and row.get("firmware_state") and row.get("firmware_reason")
        for row in status
    )
    audit.add(
        "firmware per-circuit telemetry",
        "PASS" if telemetry_live else ("FAIL" if require_ota else "BLOCKED"),
        "fresh decision telemetry plus state/reason populated for both circuits"
        if telemetry_live
        else "firmware state/reason or decision timestamp blank until OTA or stale after rollback",
    )

    try:
        planner_context = run(["bash", "scripts/gather-plan-context.sh"], timeout=90)
        context_text = planner_context.stdout
        required_context_tokens = (
            "QUALIFIED LIGHT MINUTES + GROW LIGHTS",
            "grow|grow_light_grow",
            "main|grow_light_main",
            "target_light_minutes",
            "QUALIFIED LIGHT MINUTES TODAY",
            "TEMPEST LUX THRESHOLD RECOMMENDATION",
            "confirmed ESP32 cfg readbacks remain the controller-state source of truth",
            "Use Tempest outdoor illuminance as the lighting trigger",
            "Set gl_main_target_light_minutes/gl_grow_target_light_minutes",
            "Per-circuit gl_main_target_light_minutes/gl_grow_target_light_minutes",
        )
        audit.check(
            planner_context.returncode == 0 and all(token in context_text for token in required_context_tokens),
            "live planner lighting context",
            "planner context includes per-circuit policy rows, Tempest threshold evidence, and per-circuit tunable guidance",
            planner_context.stderr.strip() or "planner context missing lighting policy/evidence tokens",
        )
    except subprocess.TimeoutExpired as exc:
        audit.add("live planner lighting context", "FAIL", f"timed out after {exc.timeout}s")

    try:
        ok, detail = mcp_set_tunable_dry_run()
        audit.add(
            "MCP lighting set_tunable gate",
            "PASS" if ok else "FAIL",
            detail,
        )
    except Exception as exc:
        audit.add("MCP lighting set_tunable gate", "FAIL", str(exc))

    timeline = psql_json(
        """
        WITH rows AS (
          SELECT *
            FROM fn_lighting_timeline(
              now() - interval '12 hours',
              now() + interval '24 hours',
              interval '30 minutes',
              'vallery'
            )
        )
        SELECT count(*)::int AS rows,
               count(natural_lux)::int AS lux_rows,
               min(main_lux_on_threshold) AS main_on_min,
               max(main_lux_off_threshold) AS main_off_max,
               min(grow_lux_on_threshold) AS grow_on_min,
               max(grow_lux_off_threshold) AS grow_off_max,
               sum(main_expected_on)::int AS main_expected_slots,
               sum(grow_expected_on)::int AS grow_expected_slots,
               max(main_target_light_minutes)::int AS main_target_light_minutes,
               max(grow_target_light_minutes)::int AS grow_target_light_minutes,
               max(main_qualified_light_minutes) AS main_qualified_light_minutes,
               max(grow_qualified_light_minutes) AS grow_qualified_light_minutes
          FROM rows
        """,
        timeout=90,
    )[0]
    audit.check(
        timeline["rows"] > 0
        and timeline["lux_rows"] > 0
        and timeline["main_on_min"] is not None
        and timeline["grow_on_min"] is not None
        and timeline["main_target_light_minutes"] is not None
        and timeline["grow_target_light_minutes"] is not None
        and timeline["main_qualified_light_minutes"] is not None
        and timeline["grow_qualified_light_minutes"] is not None,
        "live lighting timeline",
        json.dumps(timeline, sort_keys=True),
        f"timeline incomplete: {timeline}",
    )

    support_rows = psql_json(
        f"""
        SELECT DISTINCT parameter
          FROM setpoint_snapshot
         WHERE parameter = ANY(ARRAY{list(CFG_READBACK_PARAMS)!r}::text[])
           AND ts > now() - interval '15 minutes'
        """
    )
    supported_params = {row["parameter"] for row in support_rows}
    missing_support = sorted(set(CFG_READBACK_PARAMS) - supported_params)
    if not missing_support:
        audit.add(
            "pre-OTA unsupported-push guard",
            "PASS",
            "per-circuit cfg readbacks are live; firmware supports per-circuit lighting pushes",
        )
    else:
        changes = psql_json(
            f"""
            SELECT count(*)::int AS per_circuit_changes
              FROM setpoint_changes
             WHERE ts > now() - interval '2 hours'
               AND parameter = ANY(ARRAY{missing_support!r}::text[])
               AND COALESCE(delivery_status, '') NOT LIKE 'deferred_%'
            """
        )[0]
        audit.check(
            changes["per_circuit_changes"] == 0,
            "pre-OTA unsupported-push guard",
            "old firmware has not received non-deferred pushes for unsupported per-circuit params",
            f"non-deferred pushes exist before firmware readback support: missing={missing_support} changes={changes}",
        )

    try:
        home = grafana_dashboard("site-home")
        lighting = grafana_dashboard("site-climate-lighting")
        greenhouse = grafana_dashboard("greenhouse-lighting")
        home_live_panel = panel_by_id(home, 36)
        home_live_sql = panel_sql(home_live_panel) if home_live_panel else ""
        audit.check(
            home_live_panel
            and home_live_panel.get("title") == "Lighting: Lux & Switch State"
            and "setpoint_snapshot" in home_live_sql
            and "setpoint_changes" in home_live_sql
            and "'Solar'::text AS metric" in home_live_sql
            and "'Solar Forecast'::text AS metric" in home_live_sql
            and "weather_forecast" in home_live_sql
            and "equipment_state" in home_live_sql
            and "fn_lighting_timeline" not in home_live_sql
            and not any(token in home_live_sql for token in HOMEPAGE_LIGHTING_THRESHOLD_OUTPUT_TOKENS),
            "live home Grafana panel",
            "site-home panel 36 is live with observed/forecast solar lux and faded actual switch ON windows only",
            "site-home panel 36 is missing, stale, or still renders threshold output in live Grafana",
        )
        audit.check(
            panel_by_id(lighting, 16)
            and panel_by_id(lighting, 17)
            and "v_lighting_traceability_now" in panel_sql(panel_by_id(lighting, 16))
            and "fn_lighting_timeline" in panel_sql(panel_by_id(lighting, 17)),
            "live lighting Grafana panels",
            "site-climate-lighting panels 16/17 are live and bound to traceability/timeline views",
            "site-climate-lighting panels 16/17 missing or stale in live Grafana",
        )
        live_titles = {panel.get("title") for panel in greenhouse.get("panels", [])}
        audit.check(
            {"Main Light Circuit", "Lutron Circuit State", "Daily Lutron Circuit Runtime"} <= live_titles,
            "live greenhouse lighting dashboard",
            "legacy greenhouse-lighting dashboard is circuit-aware",
            "greenhouse-lighting live dashboard still has stale labels",
        )
    except (RuntimeError, json.JSONDecodeError) as exc:
        audit.add("live Grafana dashboards", "FAIL", str(exc))

    render_specs = (
        ("site-home", 36, 1680, 420),
        ("site-climate-lighting", 16, 1360, 340),
        ("site-climate-lighting", 17, 1680, 420),
    )
    for uid, panel_id, width, height in render_specs:
        ok, detail = render_panel(uid, panel_id, width, height)
        audit.add(
            f"rendered Grafana panel {uid}/{panel_id}",
            "PASS" if ok else "FAIL",
            detail,
        )

    try:
        home_html = fetch_text(f"{PUBLIC_SITE_BASE}/", timeout=15)
        lighting_html = fetch_text(f"{PUBLIC_SITE_BASE}/greenhouse/lighting/", timeout=15)
        tunables_html = fetch_text(f"{PUBLIC_SITE_BASE}/reference/ai-tunables/", timeout=15)
        audit.check(
            "panelId=36" in home_html and "site-home" in home_html and "graphs.verdify.ai" in home_html,
            "live public home page",
            "lab.verdify.ai homepage serves lighting state panel 36",
            "lab.verdify.ai homepage is missing the lighting state embed",
        )
        audit.check(
            "Circuit Policy And Forecast Bands" in lighting_html
            and "panelId=16" in lighting_html
            and "panelId=17" in lighting_html
            and "Tempest outdoor illuminance" in lighting_html
            and (
                "Firmware state and reason fields show the database, dashboard, dispatcher, ESP32, and Lutron state path."
                in lighting_html
                or "Firmware state and reason fields appear after the next ESP32 OTA" in lighting_html
            ),
            "live public lighting page",
            "lab.verdify.ai/greenhouse/lighting serves the per-circuit policy story and panels 16/17",
            "lab.verdify.ai/greenhouse/lighting is missing the per-circuit lighting story or embeds",
        )
        audit.check(
            "Planner-policy knobs" in tunables_html
            and f"<strong>{len(PLANNER_PUSHABLE_REG)}</strong>" in tunables_html
            and "gl_main_lux_threshold" in tunables_html
            and "set_tunable allowed" in tunables_html
            and "gl_lux_threshold" in tunables_html
            and "MCP rejects planner writes" in tunables_html,
            "live public tunables page",
            "lab.verdify.ai/reference/ai-tunables reflects per-circuit lighting as planner-writable and legacy gl_lux_* as read-only",
            "lab.verdify.ai/reference/ai-tunables is stale for lighting tunable writeability",
        )
    except (TimeoutError, UnicodeDecodeError, urllib.error.URLError) as exc:
        audit.add("live public website pages", "FAIL", str(exc))

    readbacks = psql_json(
        f"""
        SELECT parameter, max(ts) AS latest_ts
          FROM setpoint_snapshot
         WHERE parameter = ANY(ARRAY{list(CFG_READBACK_PARAMS)!r}::text[])
           AND ts > now() - interval '15 minutes'
         GROUP BY parameter
        """
    )
    readback_params = {row["parameter"] for row in readbacks}
    missing = sorted(set(CFG_READBACK_PARAMS) - readback_params)
    firmware_support_live = not missing
    audit.add(
        "post-OTA cfg readbacks",
        "PASS" if not missing else ("FAIL" if require_ota else "BLOCKED"),
        "all per-circuit cfg readbacks are flowing" if not missing else f"missing until OTA: {missing}",
    )

    confirmed_changes = psql_json(
        f"""
        WITH latest_fw AS (
            WITH firmware_ordered AS (
                SELECT
                    ts,
                    firmware_version,
                    lag(firmware_version) OVER (ORDER BY ts) AS previous_firmware_version
                  FROM diagnostics
                 WHERE firmware_version IS NOT NULL
                   AND firmware_version <> ''
                   AND ts > now() - interval '30 days'
            ),
            current_firmware AS (
                SELECT firmware_version
                  FROM diagnostics
                 WHERE firmware_version IS NOT NULL
                   AND firmware_version <> ''
                 ORDER BY ts DESC
                 LIMIT 1
            )
            SELECT max(fo.ts) AS first_ts
              FROM firmware_ordered fo
              CROSS JOIN current_firmware cf
             WHERE fo.firmware_version = cf.firmware_version
               AND fo.previous_firmware_version IS DISTINCT FROM fo.firmware_version
        ),
        evidence AS (
            SELECT parameter, max(confirmed_at) AS latest_ts, 'confirmed_change' AS kind
              FROM setpoint_changes, latest_fw
             WHERE parameter = ANY(ARRAY{list(PER_CIRCUIT_PARAMS)!r}::text[])
               AND confirmed_at IS NOT NULL
               AND confirmed_at >= COALESCE(latest_fw.first_ts, now() - interval '24 hours')
             GROUP BY parameter
            UNION ALL
            SELECT parameter, max(ts) AS latest_ts, 'cfg_readback' AS kind
              FROM setpoint_snapshot, latest_fw
             WHERE parameter = ANY(ARRAY{list(PER_CIRCUIT_PARAMS)!r}::text[])
               AND ts >= COALESCE(latest_fw.first_ts, now() - interval '24 hours')
             GROUP BY parameter
         )
        SELECT parameter, max(latest_ts) AS latest_ts, string_agg(DISTINCT kind, ',') AS evidence
          FROM evidence
         GROUP BY parameter
        """
    )
    confirmed_params = {row["parameter"] for row in confirmed_changes}
    missing_confirmed = sorted(set(PER_CIRCUIT_PARAMS) - confirmed_params)
    if not firmware_support_live:
        confirm_status = "FAIL" if require_ota else "BLOCKED"
        confirm_detail = "requires post-OTA cfg readbacks before confirmation can be proven"
    elif missing_confirmed:
        confirm_status = "FAIL"
        confirm_detail = f"missing confirmed per-circuit setpoint pushes: {missing_confirmed}"
    else:
        confirm_status = "PASS"
        confirm_detail = "all per-circuit setpoint pushes confirmed after latest firmware start"
    audit.add("post-OTA setpoint confirmations", confirm_status, confirm_detail)

    circuit_state_rows = psql_json(
        """
        WITH latest_fw AS (
            WITH firmware_ordered AS (
                SELECT
                    ts,
                    firmware_version,
                    lag(firmware_version) OVER (ORDER BY ts) AS previous_firmware_version
                  FROM diagnostics
                 WHERE firmware_version IS NOT NULL
                   AND firmware_version <> ''
                   AND ts > now() - interval '30 days'
            ),
            current_firmware AS (
                SELECT firmware_version
                  FROM diagnostics
                 WHERE firmware_version IS NOT NULL
                   AND firmware_version <> ''
                 ORDER BY ts DESC
                 LIMIT 1
            )
            SELECT max(fo.ts) AS first_ts
              FROM firmware_ordered fo
              CROSS JOIN current_firmware cf
             WHERE fo.firmware_version = cf.firmware_version
               AND fo.previous_firmware_version IS DISTINCT FROM fo.firmware_version
        )
        SELECT s.equipment,
               s.actual_on,
               s.firmware_state,
               s.firmware_reason,
               COALESCE(s.firmware_decision_fresh, s.firmware_telemetry_fresh, false) AS firmware_telemetry_fresh,
               s.equipment_ts,
               COALESCE(latest_fw.first_ts, now() - interval '24 hours') AS first_ts
          FROM v_lighting_traceability_now s
          CROSS JOIN latest_fw
         WHERE s.equipment IN ('grow_light_main', 'grow_light_grow')
        """
    )
    state_by_equipment = {row["equipment"]: row for row in circuit_state_rows}
    missing_state_evidence = [
        equipment
        for equipment in ("grow_light_main", "grow_light_grow")
        if equipment not in state_by_equipment
        or not state_by_equipment[equipment].get("equipment_ts")
        or state_by_equipment[equipment]["equipment_ts"] < state_by_equipment[equipment]["first_ts"]
        or not state_by_equipment[equipment].get("firmware_telemetry_fresh")
        or (
            str(state_by_equipment[equipment].get("firmware_state", "")).upper() == "ON"
            and not state_by_equipment[equipment].get("actual_on")
        )
        or (
            str(state_by_equipment[equipment].get("firmware_state", "")).upper() == "OFF"
            and state_by_equipment[equipment].get("actual_on")
        )
    ]
    if not telemetry_live:
        state_status = "FAIL" if require_ota else "BLOCKED"
        state_detail = "requires post-OTA firmware state/reason telemetry before circuit state proof is complete"
    elif missing_state_evidence:
        state_status = "FAIL"
        state_detail = f"missing fresh switch-state evidence matching firmware state after latest firmware start: {missing_state_evidence}"
    else:
        state_status = "PASS"
        state_detail = "both Lutron circuits have fresh post-OTA switch-state evidence matching firmware telemetry"
    audit.add("post-OTA Lutron state evidence", state_status, state_detail)

    setpoint_text = parse_setpoint_text(fetch_text("http://127.0.0.1:8200/setpoints"))
    audit.check(
        all(param in setpoint_text for param in PER_CIRCUIT_PARAMS),
        "setpoint server per-circuit values",
        "local setpoint server exposes all per-circuit lighting values",
        "setpoint server is missing per-circuit values",
    )
    legacy_expected = {
        "gl_lux_threshold": float(keys["main"]["lux_on_threshold"]),
        "gl_lux_hysteresis": float(keys["main"]["lux_off_threshold"]) - float(keys["main"]["lux_on_threshold"]),
    }

    def legacy_values_match(values: dict[str, str]) -> bool:
        try:
            return all(
                abs(float(values.get(param, "nan")) - expected) < 0.5 for param, expected in legacy_expected.items()
            )
        except ValueError:
            return False

    legacy_shared_params = set(legacy_expected)
    audit.check(
        legacy_values_match(setpoint_text),
        "setpoint server legacy shared lighting values",
        "local setpoint server exposes compatibility gl_lux_* values derived from the main circuit policy",
        f"setpoint server legacy gl_lux_* mismatch: expected={legacy_expected} got={setpoint_text}",
    )
    api_proc = run(
        [
            "docker",
            "exec",
            "verdify-api",
            "python",
            "-c",
            "from urllib.request import urlopen; print(urlopen('http://127.0.0.1:8080/setpoints', timeout=5).read().decode())",
        ],
        timeout=15,
    )
    api_values = parse_setpoint_text(api_proc.stdout if api_proc.returncode == 0 else "")
    audit.check(
        api_proc.returncode == 0 and all(param in api_values for param in PER_CIRCUIT_PARAMS),
        "api per-circuit values",
        "API /setpoints exposes all per-circuit lighting values",
        api_proc.stderr.strip() or "API /setpoints missing per-circuit values",
    )
    audit.check(
        api_proc.returncode == 0 and legacy_values_match(api_values),
        "api legacy shared lighting values",
        "API /setpoints exposes legacy gl_lux_* values derived from the main circuit policy",
        api_proc.stderr.strip() or f"API legacy gl_lux_* mismatch: expected={legacy_expected} got={api_values}",
    )

    deploy_preflight = run(["bash", "scripts/firmware-deploy-preflight.sh"], timeout=30)
    if deploy_preflight.returncode == 0:
        audit.add("firmware OTA preflight", "PASS", "deploy guard is clear")
    else:
        preflight_text = (deploy_preflight.stdout + deploy_preflight.stderr).strip()
        last_line = preflight_text.splitlines()[-1:]
        bake_only_block = (
            "48-hour bake not satisfied" in preflight_text
            and "No unresolved critical/legacy-high alerts" in preflight_text
        )
        audit.add(
            "firmware OTA preflight",
            "WARN" if bake_only_block else ("FAIL" if require_ota else "BLOCKED"),
            last_line[0] if last_line else "preflight failed",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--static-only", action="store_true", help="Run source/build checks only.")
    parser.add_argument("--live", action="store_true", help="Run live DB/service/Grafana checks.")
    parser.add_argument("--require-ota", action="store_true", help="Treat missing post-OTA proof as failure.")
    parser.add_argument("--allow-blocked", action="store_true", help="Return 0 when checks are blocked but not failed.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    args = parser.parse_args()

    audit = Audit()
    static_checks(audit)
    if args.live or not args.static_only:
        try:
            live_checks(audit, require_ota=args.require_ota)
        except (RuntimeError, urllib.error.URLError, subprocess.TimeoutExpired) as exc:
            audit.add("live audit execution", "FAIL", str(exc))

    if args.json:
        audit.print_json()
    else:
        audit.print_text()
    return audit.exit_code(allow_blocked=args.allow_blocked)


if __name__ == "__main__":
    raise SystemExit(main())
