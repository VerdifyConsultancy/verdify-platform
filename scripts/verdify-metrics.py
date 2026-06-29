#!/usr/bin/env python3
"""
verdify-metrics.py — Generate Prometheus textfile metrics for node-exporter.

Runs every minute via cron. Queries TimescaleDB for greenhouse health metrics
and writes them to /var/lib/node-exporter/textfiles/verdify.prom.

Metrics exposed:
  verdify_climate_age_seconds    — seconds since last climate reading
  verdify_compliance_pct         — today's band compliance (0-100)
  verdify_planner_score          — today's planner score (0-100)
  verdify_stress_hours           — stress hours by type (heat/cold/vpd_high/vpd_low)
  verdify_cost_today_dollars     — today's utility cost
  verdify_setpoint_changes_today — number of setpoint changes today
  verdify_active_alerts          — alerts in the last hour
  verdify_plans_today            — number of plans written today
  verdify_esp32_uptime_hours     — ESP32 uptime
  verdify_data_freshness         — 1 if data is fresh (<5 min), 0 if stale
  verdify_containers_running     — number of running Docker containers
  verdify_up                     — 1 if this script ran successfully
"""

import subprocess
import sys
from pathlib import Path

import asyncpg

OUTPUT = Path("/var/lib/node-exporter/textfiles/verdify.prom")

# Read DB password from .env
db_pass = "verdify"
env_path = Path("/srv/verdify/.env")
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if line.startswith("POSTGRES_PASSWORD="):
            db_pass = line.split("=", 1)[1].strip().strip('"').strip("'")
DSN = f"postgresql://verdify:{db_pass}@localhost:5432/verdify"


async def collect():
    lines = []
    lines.append("# HELP verdify_up Whether the metrics collection succeeded")
    lines.append("# TYPE verdify_up gauge")

    try:
        conn = await asyncpg.connect(DSN)
    except Exception as e:
        lines.append("verdify_up 0")
        return lines

    try:
        # Climate data freshness
        age = await conn.fetchval("SELECT extract(epoch FROM now() - max(ts))::int FROM climate")
        lines.append("# HELP verdify_climate_age_seconds Seconds since last climate reading")
        lines.append("# TYPE verdify_climate_age_seconds gauge")
        lines.append(f"verdify_climate_age_seconds {age or 0}")

        lines.append("# HELP verdify_data_freshness 1 if climate data is fresh (under 5 min)")
        lines.append("# TYPE verdify_data_freshness gauge")
        lines.append(f"verdify_data_freshness {1 if age and age < 300 else 0}")

        # Scorecard
        rows = await conn.fetch(
            "SELECT metric, value FROM fn_planner_scorecard((now() AT TIME ZONE 'America/Denver')::date)"
        )
        scorecard = {r["metric"]: float(r["value"]) if r["value"] is not None else 0 for r in rows}

        lines.append("# HELP verdify_compliance_pct Band compliance (both temp AND VPD in band)")
        lines.append("# TYPE verdify_compliance_pct gauge")
        lines.append(f"verdify_compliance_pct {scorecard.get('compliance_pct', 0)}")

        lines.append("# HELP verdify_temp_compliance_pct Temperature band compliance")
        lines.append("# TYPE verdify_temp_compliance_pct gauge")
        lines.append(f"verdify_temp_compliance_pct {scorecard.get('temp_compliance_pct', 0)}")

        lines.append("# HELP verdify_vpd_compliance_pct VPD band compliance")
        lines.append("# TYPE verdify_vpd_compliance_pct gauge")
        lines.append(f"verdify_vpd_compliance_pct {scorecard.get('vpd_compliance_pct', 0)}")

        lines.append("# HELP verdify_planner_score Planner score today (0-100)")
        lines.append("# TYPE verdify_planner_score gauge")
        lines.append(f"verdify_planner_score {scorecard.get('planner_score', 0)}")

        lines.append("# HELP verdify_cost_today_dollars Utility cost today")
        lines.append("# TYPE verdify_cost_today_dollars gauge")
        lines.append(f"verdify_cost_today_dollars {scorecard.get('cost_total', 0)}")

        lines.append("# HELP verdify_stress_hours Stress hours by type today")
        lines.append("# TYPE verdify_stress_hours gauge")
        for stype in ["heat", "cold", "vpd_high", "vpd_low"]:
            val = scorecard.get(f"{stype}_stress_h", 0)
            lines.append(f'verdify_stress_hours{{type="{stype}"}} {val}')

        # Setpoint changes today
        changes = await conn.fetchval("SELECT count(*) FROM setpoint_changes WHERE ts::date = CURRENT_DATE")
        lines.append("# HELP verdify_setpoint_changes_today Setpoint changes dispatched today")
        lines.append("# TYPE verdify_setpoint_changes_today gauge")
        lines.append(f"verdify_setpoint_changes_today {changes or 0}")

        # Active alerts
        alerts = await conn.fetchval("SELECT count(*) FROM alert_log WHERE ts > now() - interval '1 hour'")
        lines.append("# HELP verdify_active_alerts Alerts in the last hour")
        lines.append("# TYPE verdify_active_alerts gauge")
        lines.append(f"verdify_active_alerts {alerts or 0}")

        # Plans today
        plans = await conn.fetchval("SELECT count(*) FROM plan_journal WHERE created_at::date = CURRENT_DATE")
        lines.append("# HELP verdify_plans_today Planning events today")
        lines.append("# TYPE verdify_plans_today gauge")
        lines.append(f"verdify_plans_today {plans or 0}")

        # Current mode
        mode = await conn.fetchval(
            "SELECT value FROM system_state WHERE entity = 'greenhouse_state' ORDER BY ts DESC LIMIT 1"
        )
        lines.append("# HELP verdify_esp32_mode Current greenhouse controller mode")
        lines.append("# TYPE verdify_esp32_mode gauge")
        # Encode mode as numeric for Prometheus (label for human readability)
        mode_map = {
            "SENSOR_FAULT": 0,
            "SAFETY_COOL": 1,
            "SAFETY_HEAT": 2,
            "SEALED_MIST": 3,
            "SEALED_MIST_S1": 3,
            "SEALED_MIST_S2": 3,
            "SEALED_MIST_FOG": 3,
            "THERMAL_RELIEF": 4,
            "VENTILATE": 5,
            "DEHUM_VENT": 6,
            "IDLE": 7,
        }
        mode_str = mode or "UNKNOWN"
        lines.append(f'verdify_esp32_mode{{mode="{mode_str}"}} {mode_map.get(mode_str, -1)}')

        # ESP32 diagnostics
        # heap_bytes is a legacy column name; firmware publishes this diagnostic in kB.
        diag = await conn.fetchrow(
            "SELECT round((uptime_s / 3600.0)::numeric, 1) as uptime_hours, round(heap_bytes::numeric, 1) as free_heap_kb, wifi_rssi FROM diagnostics ORDER BY ts DESC LIMIT 1"
        )
        if diag:
            lines.append("# HELP verdify_esp32_uptime_hours ESP32 uptime in hours")
            lines.append("# TYPE verdify_esp32_uptime_hours gauge")
            lines.append(f"verdify_esp32_uptime_hours {diag['uptime_hours'] or 0}")

            lines.append("# HELP verdify_esp32_heap_kb ESP32 free heap memory in KB")
            lines.append("# TYPE verdify_esp32_heap_kb gauge")
            lines.append(f"verdify_esp32_heap_kb {diag['free_heap_kb'] or 0}")

            lines.append("# HELP verdify_esp32_wifi_rssi ESP32 WiFi signal strength")
            lines.append("# TYPE verdify_esp32_wifi_rssi gauge")
            lines.append(f"verdify_esp32_wifi_rssi {diag['wifi_rssi'] or 0}")

        lines.append("verdify_up 1")

    except Exception as e:
        lines.append("verdify_up 0")
        print(f"Error collecting metrics: {e}", file=sys.stderr)
    finally:
        await conn.close()

    # Container count (outside DB)
    try:
        result = subprocess.run(
            ["docker", "compose", "-f", "/srv/verdify/docker-compose.yml", "ps", "-q"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        count = len(result.stdout.strip().split("\n")) if result.stdout.strip() else 0
    except Exception:
        count = 0
    lines.append("# HELP verdify_containers_running Number of running Docker containers")
    lines.append("# TYPE verdify_containers_running gauge")
    lines.append(f"verdify_containers_running {count}")

    return lines


def main():
    import asyncio

    lines = asyncio.run(collect())

    # Atomic write via temp file
    tmp = OUTPUT.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n")
    tmp.rename(OUTPUT)


if __name__ == "__main__":
    main()
