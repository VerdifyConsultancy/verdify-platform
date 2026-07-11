#!/usr/bin/env python3
"""
setpoint-server.py — HTTP setpoint delivery server for ESP32 + grow light control.

Exposes simple endpoints for the ESP32 to toggle grow lights without needing
HA auth tokens in the firmware. Also tracks light state and runtime.

Endpoints:
    GET  /lights              — current state of both lights
    POST /lights/main/on      — turn on greenhouse main
    POST /lights/main/off     — turn off greenhouse main
    POST /lights/grow/on      — turn on greenhouse grow
    POST /lights/grow/off     — turn off greenhouse grow
    GET  /health              — service health check

Listens on 0.0.0.0:8200 (accessible from ESP32 at 192.168.10.111).
Writes state changes to equipment_state table.

Usage:
    setpoint-server.py           # run as foreground service (systemd)
"""

import asyncio
import json
import logging
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import asyncpg

REPO_ROOT = Path(__file__).resolve().parents[1]
INGESTOR_DIR = REPO_ROOT / "ingestor"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(INGESTOR_DIR) not in sys.path:
    sys.path.insert(0, str(INGESTOR_DIR))

from verdify_schemas.tunable_registry import BAND_OWNED_REG, CROP_BAND_REG, SETPOINT_MAP_REG  # noqa: E402

# --- Configuration ---
HA_URL = "http://192.168.30.107:8123"
HA_TOKEN_FILE = "/mnt/agents/shared/credentials/ha_token.txt"
LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 8200

LIGHTS = {
    "main": {"ha_entity": "switch.greenhouse_main", "equipment": "grow_light_main"},
    "grow": {"ha_entity": "switch.greenhouse_grow", "equipment": "grow_light_grow"},
}

FIRMWARE_SETPOINT_PARAMS = frozenset(SETPOINT_MAP_REG.values())

FORCED_ON_SWITCH_PARAMS = frozenset({"sw_fsm_controller_enabled"})
EQUIPMENT_SWITCH_SETPOINTS = {
    "sw_economiser_enabled": "economiser_enabled",
    "sw_fog_closes_vent": "fog_closes_vent",
    "sw_irrigation_enabled": "irrigation_enabled",
    "sw_irrigation_wall_enabled": "irrigation_wall_enabled",
    "sw_irrigation_center_enabled": "irrigation_center_enabled",
    "sw_irrigation_weather_skip": "irrigation_weather_skip",
    "sw_gl_auto_mode": "gl_auto_mode",
}
BAND_OWNED_PARAMS = CROP_BAND_REG
PLAN_EXCLUDED_PARAMS_SQL = ",".join("'" + param.replace("'", "''") + "'" for param in sorted(BAND_OWNED_REG))
DIRECT_WET_DEFAULTS = {
    "direct_wet_min_temp_f": "65",
    "direct_wet_wall_start_offset_min": "60",
    "direct_wet_wall_drydown_before_off_min": "120",
    "direct_wet_south_start_offset_min": "60",
    "direct_wet_south_drydown_before_off_min": "120",
    "direct_wet_west_start_offset_min": "60",
    "direct_wet_west_drydown_before_off_min": "120",
    "direct_wet_center_start_offset_min": "120",
    "direct_wet_center_drydown_before_off_min": "180",
    "irrig_wall_days_mask": "127",
    "irrig_wall_fert_days_mask": "127",
    "irrig_center_days_mask": "127",
    "irrig_center_fert_days_mask": "127",
    "sw_direct_wet_gate_enabled": "1",
}
SAFETY_DEFAULTS = {
    "safety_max": "100",
    "safety_min": "40",
}
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [setpoint-server] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# Global state
_db_pool = None
_ha_token = None
_main_loop: asyncio.AbstractEventLoop | None = None
_light_state = {"main": None, "grow": None}  # True=on, False=off, None=unknown


def _int_param(params: dict[str, str], key: str, default: int) -> int:
    try:
        return int(float(params.get(key, default)))
    except (TypeError, ValueError):
        return default


def _overlay_activity_direct_wet_defaults(params: dict[str, str], plan_params: set[str]) -> None:
    """Keep the compatibility endpoint aligned with dispatcher-owned activity policy.
    Keyed off the GROW (jalapeno, longest-day) circuit, NOT MAIN — a short orchid
    photoperiod on MAIN must not shrink the irrigation activity window (#294)."""
    activity_start_hour = max(0, min(23, _int_param(params, "gl_grow_sunrise_hour", 6)))
    activity_duration_min = max(0, min(1440, _int_param(params, "gl_grow_target_light_minutes", 960)))
    params["activity_start_hour"] = str(activity_start_hour)
    params["activity_start_minute"] = "0"
    params["activity_duration_min"] = str(activity_duration_min)

    for param, value in DIRECT_WET_DEFAULTS.items():
        if param not in plan_params:
            params.setdefault(param, value)


def _overlay_dispatcher_owned_defaults(params: dict[str, str], plan_params: set[str]) -> None:
    """Mirror dispatcher-owned defaults that should not drift from stale rows."""
    for param, value in SAFETY_DEFAULTS.items():
        if param not in plan_params:
            params[param] = value

    has_per_circuit_lighting = "gl_main_lux_hysteresis" in params and "gl_grow_lux_hysteresis" in params
    if has_per_circuit_lighting:
        if "gl_lux_threshold" not in plan_params and "gl_main_lux_threshold" in params:
            params["gl_lux_threshold"] = params["gl_main_lux_threshold"]
        if "gl_lux_hysteresis" not in plan_params and "gl_main_lux_hysteresis" in params:
            params["gl_lux_hysteresis"] = params["gl_main_lux_hysteresis"]


def load_token() -> str:
    with open(HA_TOKEN_FILE) as f:
        return f.read().strip()


def ha_call(service: str, entity_id: str) -> bool:
    """Call HA REST API service. Returns True on success."""
    global _ha_token
    if _ha_token is None:
        _ha_token = load_token()
    domain = entity_id.split(".", 1)[0]
    url = f"{HA_URL}/api/services/{domain}/{service}"
    body = json.dumps({"entity_id": entity_id}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {_ha_token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception as e:
        log.error("HA call failed: %s %s → %s", service, entity_id, e)
        return False


def ha_confirm_state(entity_id: str, expected: str, timeout_s: float = 8.0) -> bool:
    """Poll HA state after a service call; HTTP 200 alone does not prove Lutron moved."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if ha_get_state(entity_id) == expected:
            return True
        time.sleep(0.25)
    return False


def ha_get_state(entity_id: str) -> str | None:
    """Get current state of an HA entity. Returns 'on', 'off', or None."""
    global _ha_token
    if _ha_token is None:
        _ha_token = load_token()
    url = f"{HA_URL}/api/states/{entity_id}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {_ha_token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            return data.get("state")
    except Exception as e:
        log.error("HA state fetch failed: %s → %s", entity_id, e)
        return None


async def record_state_change(equipment: str, is_on: bool) -> None:
    """Write state change to equipment_state table."""
    global _db_pool
    if _db_pool is None:
        return
    try:
        async with _db_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO equipment_state (ts, equipment, state) VALUES ($1, $2, $3)",
                datetime.now(UTC),
                equipment,
                is_on,
            )
        log.info("State recorded: %s → %s", equipment, "ON" if is_on else "OFF")
    except Exception as e:
        log.error("DB write failed: %s", e)


def record_state_change_sync(equipment: str, is_on: bool) -> bool:
    """Record state changes from the HTTP server thread on the main asyncio loop."""
    if _main_loop is None:
        log.error("DB write skipped: main loop is not ready")
        return False
    try:
        future = asyncio.run_coroutine_threadsafe(record_state_change(equipment, is_on), _main_loop)
        future.result(timeout=5)
        return True
    except Exception as e:
        log.error("DB write failed: %s", e)
        return False


def get_db_url() -> str:
    # Env-first DSN (#118 §A1 code gate — the k3s prerequisite). Order:
    #   1. VERDIFY_DB_DSN / DATABASE_URL (a full DSN; ignored if it still
    #      carries unexpanded $(VAR) k8s references — envFrom vars are not
    #      available to $() expansion, so a composed spec value arrives raw)
    #   2. DB_HOST + friends (the verdify-config ConfigMap via envFrom +
    #      POSTGRES_PASSWORD via secretKeyRef — the in-cluster shape)
    #   3. legacy VM fallback: /srv/verdify/.env + localhost
    for var in ("VERDIFY_DB_DSN", "DATABASE_URL"):
        dsn = os.environ.get(var, "")
        if dsn and "$(" not in dsn:
            return dsn
    host = os.environ.get("DB_HOST")
    if host:
        user = os.environ.get("DB_USER", "verdify")
        pw = os.environ.get("POSTGRES_PASSWORD") or os.environ.get("DB_PASSWORD", "verdify")
        port = os.environ.get("DB_PORT", "5432")
        name = os.environ.get("DB_NAME", "verdify")
        return f"postgresql://{user}:{pw}@{host}:{port}/{name}"
    pw = "verdify"
    env_file = "/srv/verdify/.env"
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                if line.strip().startswith("POSTGRES_PASSWORD="):
                    pw = line.strip().split("=", 1)[1].strip().strip('"').strip("'")
    return f"postgresql://verdify:{pw}@localhost:5432/verdify"


def _db_text_safe(sql: str) -> str:
    """Run one read-only SQL through the asyncpg pool from the HTTP thread.

    Returns rows as psql-style '-t -A -F=' text (columns joined by '='), or ""
    on failure — matching the prior subprocess semantics per query. Replaces
    the VM-era psql/docker shell-out (#447): the k3s image ships neither
    binary, so /setpoints returned HTTP 500 from the cutover until 2026-07-11.
    """
    if _main_loop is None or _db_pool is None:
        log.error("Setpoint query skipped: DB pool not ready")
        return ""

    async def _q() -> str:
        async with _db_pool.acquire() as conn:
            rows = await conn.fetch(sql)
        lines = []
        for r in rows:
            vals = ["" if v is None else str(v) for v in r.values()]
            lines.append("=".join(vals))
        return "\n".join(lines)

    try:
        return asyncio.run_coroutine_threadsafe(_q(), _main_loop).result(timeout=5)
    except Exception as e:
        log.error("Setpoint query failed: %s", e)
        return ""


def get_setpoint_text_sync() -> str:
    """Query setpoint_plan for current active values. Synchronous for HTTP thread."""

    # Planner/dispatcher param names → firmware-compatible param names.
    # The current ESP32 firmware receives values through ESPHome native API
    # pushes; this key=value endpoint remains aligned for diagnostics and
    # recovery tooling.
    # DB trigger (migration 058) normalizes all param names at INSERT — no aliases needed
    # Step 1: ALL current setpoints as baseline
    params = {}
    result_text = _db_text_safe(
        "SELECT parameter, value FROM (SELECT DISTINCT ON (parameter) parameter, value "
        "FROM setpoint_changes ORDER BY parameter, ts DESC) sub"
    )
    for line in result_text.strip().split("\n"):
        if "=" in line:
            k, v = line.split("=", 1)
            params[k.strip()] = v.strip()

    # Step 2: Overlay active plan values (v_active_plan resolves supersession
    # by created_at DESC). Dispatcher-owned band/lighting params stay on the
    # latest dispatcher push or DB policy overlay; active plan rows are not
    # authoritative for crop/house bands or photoperiod.
    plan_params: set[str] = set()
    result_text = _db_text_safe(
        f"SELECT parameter, value FROM v_active_plan WHERE parameter NOT IN ({PLAN_EXCLUDED_PARAMS_SQL})"
    )
    for line in result_text.strip().split("\n"):
        if "=" in line:
            k, v = line.split("=", 1)
            plan_params.add(k.strip())
            params[k.strip()] = v.strip()

    # Step 2a: Overlay crop/house band policy. This mirrors the ingestor
    # dispatcher so the compatibility endpoint cannot serve stale temp/VPD or
    # zone VPD targets after policy functions change.
    result_text = _db_text_safe(
        "WITH crop AS (SELECT * FROM fn_band_setpoints(now())), "
        "house AS (SELECT * FROM fn_house_vpd_control_band(now())), "
        "zone AS (SELECT * FROM fn_zone_vpd_targets(now())) "
        "SELECT parameter, value FROM (VALUES "
        "('temp_low', (SELECT round(temp_low::numeric, 1)::text FROM crop)), "
        "('temp_high', (SELECT round(temp_high::numeric, 1)::text FROM crop)), "
        "('vpd_low', (SELECT round(house_vpd_low::numeric, 2)::text FROM house)), "
        "('vpd_high', (SELECT round(house_vpd_high::numeric, 2)::text FROM house)), "
        "('vpd_target_south', (SELECT round(vpd_target_south::numeric, 2)::text FROM zone)), "
        "('vpd_target_west', (SELECT round(vpd_target_west::numeric, 2)::text FROM zone)), "
        "('vpd_target_east', (SELECT round(vpd_target_east::numeric, 2)::text FROM zone)), "
        "('vpd_target_center', (SELECT round(vpd_target_center::numeric, 2)::text FROM zone))"
        ") AS v(parameter, value) WHERE value IS NOT NULL"
    )
    for line in result_text.strip().split("\n"):
        if "=" in line:
            k, v = line.split("=", 1)
            if k.strip() in BAND_OWNED_PARAMS:
                params[k.strip()] = v.strip()

    # Step 2b: Seed the per-circuit lighting state-machine params from
    # crop policy + Tempest threshold evidence, but let active planner rows
    # override them. This keeps the compatibility endpoint aligned with the
    # dispatcher without taking per-circuit control away from Iris.
    result_text = _db_text_safe(
        "SELECT parameter, value FROM fn_lighting_minutes_policy(now(), 'vallery') p "
        "CROSS JOIN LATERAL (VALUES "
        "('gl_' || p.light_key || '_dli_target', round(p.legacy_dli_target::numeric, 1)::text), "
        "('gl_' || p.light_key || '_target_light_minutes', p.target_light_minutes::text), "
        "('gl_' || p.light_key || '_sunrise_hour', p.start_hour::text), "
        "('gl_' || p.light_key || '_sunset_hour', p.cutoff_hour::text), "
        "('gl_' || p.light_key || '_lux_threshold', round(p.lux_on_threshold::numeric, 0)::text), "
        "('gl_' || p.light_key || '_lux_hysteresis', round(p.lux_hysteresis::numeric, 0)::text), "
        "('gl_' || p.light_key || '_min_on_s', p.min_on_s::text), "
        "('gl_' || p.light_key || '_min_off_s', p.min_off_s::text), "
        "('sw_gl_' || p.light_key || '_auto_mode', CASE WHEN p.auto_enabled THEN '1' ELSE '0' END)"
        ") AS v(parameter, value)"
    )
    for line in result_text.strip().split("\n"):
        if "=" in line:
            k, v = line.split("=", 1)
            if k.strip() not in plan_params:
                params[k.strip()] = v.strip()

    _overlay_activity_direct_wet_defaults(params, plan_params)
    _overlay_dispatcher_owned_defaults(params, plan_params)

    # Step 3: Keep this ESP32 endpoint small and numeric. Metadata such as
    # source/next_* used to bloat the response and triggered malformed parses.
    params = {k: v for k, v in params.items() if k in FIRMWARE_SETPOINT_PARAMS}
    for param in FORCED_ON_SWITCH_PARAMS:
        params[param] = "1"

    # Occupancy state (real-time from system_state, written by occupancy-bridge.py)
    result_text = _db_text_safe("SELECT value FROM system_state WHERE entity = 'occupancy' ORDER BY ts DESC LIMIT 1")
    occ = result_text.strip()
    params["occupancy"] = "1" if occ == "occupied" else "0"

    # Outdoor conditions (from Tempest via climate table — for ESP32 enthalpy computation)
    result_text = _db_text_safe(
        "SELECT round(outdoor_temp_f::numeric,1) || '|' || round(outdoor_rh_pct::numeric,1) "
        "FROM climate WHERE outdoor_temp_f IS NOT NULL ORDER BY ts DESC LIMIT 1"
    )
    outdoor = result_text.strip()
    if "|" in outdoor:
        parts = outdoor.split("|")
        params["outdoor_temp"] = parts[0]
        params["outdoor_rh"] = parts[1]

    switch_values_sql = ", ".join(
        f"('{param}', '{equipment}')" for param, equipment in sorted(EQUIPMENT_SWITCH_SETPOINTS.items())
    )
    result_text = _db_text_safe(f"""
            WITH switch_map(parameter, equipment) AS (VALUES {switch_values_sql}),
            latest AS (
                SELECT DISTINCT ON (equipment) equipment, state
                  FROM equipment_state
                 WHERE equipment IN (SELECT equipment FROM switch_map)
                 ORDER BY equipment, ts DESC
            )
            SELECT sm.parameter, CASE WHEN latest.state THEN 1 ELSE 0 END
              FROM switch_map sm
              JOIN latest USING (equipment)
            """)
    for line in result_text.strip().split("\n"):
        if "=" in line:
            k, v = line.split("=", 1)
            params[k.strip()] = v.strip()

    lines = [f"{k}={v}" for k, v in sorted(params.items())]
    return "\n".join(lines) + "\n"


class LutronHandler(BaseHTTPRequestHandler):
    """HTTP request handler for grow light and setpoint control."""

    def log_message(self, format, *args):
        log.info(format, *args)

    def _respond(self, code: int, body: dict):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def do_GET(self):
        if self.path == "/lights":
            # Fetch current state from HA
            result = {}
            for name, cfg in LIGHTS.items():
                state = ha_get_state(cfg["ha_entity"])
                result[name] = {"state": state, "entity": cfg["ha_entity"]}
                _light_state[name] = state == "on" if state else None
            self._respond(200, result)

        elif self.path == "/health":
            self._respond(200, {"status": "ok", "lights": dict(_light_state)})

        elif self.path == "/setpoints":
            # Serve current+next planned setpoints as key=value text for
            # diagnostics and recovery tooling.
            try:
                text = get_setpoint_text_sync()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(text.encode())
            except Exception as e:
                log.error("Setpoint query failed: %s", e)
                self._respond(500, {"error": str(e)})

        else:
            self._respond(404, {"error": "not found"})

    def do_POST(self):
        # Parse: /lights/{name}/{action}
        parts = self.path.strip("/").split("/")
        if len(parts) != 3 or parts[0] != "lights":
            self._respond(400, {"error": "use /lights/{main|grow}/{on|off}"})
            return

        name = parts[1]
        action = parts[2]

        if name not in LIGHTS:
            self._respond(400, {"error": f"unknown light: {name}. Use main or grow"})
            return
        if action not in ("on", "off"):
            self._respond(400, {"error": f"unknown action: {action}. Use on or off"})
            return

        cfg = LIGHTS[name]
        service = f"turn_{action}"
        success = ha_call(service, cfg["ha_entity"])

        if not success:
            self._respond(502, {"light": name, "action": action, "success": False, "error": "HA call failed"})
            return

        expected = "on" if action == "on" else "off"
        confirmed = ha_confirm_state(cfg["ha_entity"], expected)
        if not confirmed:
            actual = ha_get_state(cfg["ha_entity"])
            self._respond(
                504,
                {
                    "light": name,
                    "action": action,
                    "success": False,
                    "entity": cfg["ha_entity"],
                    "expected": expected,
                    "actual": actual,
                    "error": "HA state did not confirm requested Lutron state",
                },
            )
            log.error("Light %s %s not confirmed: expected=%s actual=%s", name, action.upper(), expected, actual)
            return

        is_on = action == "on"
        _light_state[name] = is_on
        db_recorded = record_state_change_sync(cfg["equipment"], is_on)
        self._respond(
            200,
            {
                "light": name,
                "action": action,
                "success": True,
                "entity": cfg["ha_entity"],
                "confirmed_state": expected,
                "db_recorded": db_recorded,
            },
        )
        log.info("Light %s → %s confirmed via %s", name, action.upper(), cfg["ha_entity"])


async def main():
    global _db_pool, _main_loop
    _main_loop = asyncio.get_running_loop()

    log.info("Starting Lutron proxy on %s:%d", LISTEN_HOST, LISTEN_PORT)

    # Connect to DB
    _db_pool = await asyncpg.create_pool(get_db_url(), min_size=1, max_size=3)
    log.info("DB pool ready")

    # Fetch initial light state
    for name, cfg in LIGHTS.items():
        state = ha_get_state(cfg["ha_entity"])
        _light_state[name] = state == "on" if state else None
        log.info("Initial state: %s = %s", name, state)

    # Run HTTP server in a thread
    server = HTTPServer((LISTEN_HOST, LISTEN_PORT), LutronHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log.info("HTTP server listening on %s:%d", LISTEN_HOST, LISTEN_PORT)

    # Keep alive
    try:
        while True:
            await asyncio.sleep(60)
            # Periodically refresh HA token in case it was rotated
            global _ha_token
            try:
                _ha_token = load_token()
            except Exception:
                pass
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
