#!/usr/bin/env /srv/greenhouse/.venv/bin/python3
"""
alert-monitor.py — Check alert conditions, write to alert_log, post to Slack.

Runs every 5 minutes via cron. Checks conditions including:
1. sensor_offline — v_sensor_staleness stale = true
2. relay_stuck — v_relay_stuck is_stuck = true
3. vpd_stress — v_stress_hours_today vpd_stress_hours over threshold
4. temp_safety — climate temp_avg < 35 or > 100
5. leak_detected — equipment_state leak_detected = true
6. esp32_reboot — diagnostics uptime_s < 300
6b. esp32_boot_loop — 3+ same-build reboots under 120s in 10 min (M7)
6c. heap_largest_free_block_low — sustained heap fragmentation pressure (M7)

Deduplicates: won't re-alert for the same open condition.
Auto-resolves: clears alerts when the condition passes.
Posts to Slack #greenhouse via bot token API.

Usage:
    alert-monitor.py           # run once (default, cron mode)
    alert-monitor.py --dry-run # check conditions but don't post to Slack
    alert-monitor.py --digest  # post daily digest of open alerts to Slack
"""

import asyncio
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import asyncpg

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from slack_config import build_slack_payload, load_slack_settings, read_slack_token  # noqa: E402
from slack_ops.policy import should_post_alert  # noqa: E402
from slack_ops.runbooks import fetch_alert_runbook, format_runbook  # noqa: E402
from verdify_schemas.tunable_registry import PLANNER_PUSHABLE_REG  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [alert-monitor] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# --- Configuration ---
SLACK_SETTINGS = load_slack_settings()
SLACK_CHANNEL = SLACK_SETTINGS.channel_id
SLACK_TOKEN_FILE = SLACK_SETTINGS.bot_token_file
DRY_RUN = "--dry-run" in sys.argv
DIGEST_MODE = "--digest" in sys.argv
AIR_EXCHANGE_RELAY_STUCK_MODES = frozenset({"VENTILATE", "DEHUM_VENT", "THERMAL_RELIEF", "SAFETY_COOL"})

# A planner-policy param clamped more than this many times in a trailing hour means the
# AI planning agent is repeatedly pushing values the band/guardrail layer rejects, so its
# tuning intent is silently dropped. The dispatcher clamps about every 5s while a stress
# guardrail is active, so a sustained guardrail can produce ~700+ clamps/hour for a single
# param; 60/hour (one clamp/minute sustained) is a conservative floor that filters
# incidental clamps but catches a stuck planner-vs-guardrail tug-of-war.
PLANNER_CLAMP_RATE_THRESHOLD_PER_HOUR = 60

# M7 / heap + boot-loop watchdogs (commit 90bc358 heap-protection context).
#
# Boot-loop: a healthy ESP32 reboots rarely (OTA, manual). 3+ reboots of the
# SAME firmware build, each with uptime below BOOTLOOP_UPTIME_S, inside a 10-min
# window means the new build is crash-looping — page it as a deploy-quality
# critical so the operator rolls back rather than letting it thrash.
BOOTLOOP_UPTIME_S = 120
BOOTLOOP_MIN_REBOOTS = 3
BOOTLOOP_WINDOW_MIN = 10
# Sustained-low largest-free-block: the heap can have free bytes yet no single
# contiguous block large enough to allocate (fragmentation), which is what
# actually strands the controller. Healthy avg ~60kB, p05 ~38kB. A SUSTAINED dip
# below LFB_LOW_KB (most samples in the trailing window) is real heap pressure;
# a single transient dip is not. Warns; the firmware's own debounced
# heap_pressure_critical binary sensor remains the hard rail.
LFB_LOW_KB = 22.0
LFB_WINDOW_MIN = 15
LFB_MIN_SAMPLES = 8
LFB_LOW_FRACTION = 0.8

# M4 / B8: data-pipeline coverage for the three pipelines absent from
# v_data_pipeline_health (esp32_logs, irrigation_log, weather_station). These are
# chronically off / by-design idle, so we do NOT page on their being stale per se
# — that would be permanent noise. Instead we detect a NEWLY-dead pipeline: one
# that produced rows within the trailing recovery window (so it was alive) but
# whose freshest row is now older than its expected cadence. (table, cadence_s,
# recently-active window_s, severity). irrigation_log is by-design event-driven
# (62d gaps are normal) so it only flags if it was active in the last 7d then
# stalls; esp32_logs/weather_station use tighter windows.
PIPELINE_COVERAGE = (
    ("esp32_logs", 3600, 6 * 3600, "warning"),
    ("weather_station", 3600, 6 * 3600, "warning"),
    ("irrigation_log", 24 * 3600, 7 * 24 * 3600, "info"),
)

# G6 / B19 §7.4: VPD-high stress alert threshold (hours/day). The legacy 2.0h
# constant was tuned against the broken 78F band; once 145 raises the orchid band
# and 146 re-points v_stress_hours_today.vpd_stress_hours to a graded-deficit
# integral (always <= the binary count), 2.0h is unreachable -> dead alert.
# Recalibrate to max(0.5, p75 rolling-30d graded center vpd_high), falling back to
# the legacy 2.0h until the graded column exists. Mirrors tasks.py
# _vpd_stress_alert_threshold so both VPD-stress consumers stay consistent.
VPD_STRESS_FLOOR_H = 0.5
VPD_STRESS_LEGACY_THRESHOLD_H = 2.0


async def vpd_stress_threshold(conn) -> float:
    """Dynamic VPD-high stress threshold (hours/day), graded-history aware (G6)."""
    try:
        p75 = await conn.fetchval(
            """
            SELECT percentile_cont(0.75) WITHIN GROUP (ORDER BY graded_stress_hours_vpd_high)
              FROM daily_summary
             WHERE date >= (now() AT TIME ZONE 'America/Denver')::date - 30
               AND graded_stress_hours_vpd_high IS NOT NULL
            """
        )
    except asyncpg.exceptions.PostgresError:
        return VPD_STRESS_LEGACY_THRESHOLD_H
    if p75 is None:
        return VPD_STRESS_LEGACY_THRESHOLD_H
    return max(VPD_STRESS_FLOOR_H, float(p75))


SEVERITY_EMOJI = {
    "critical": "\U0001f534",  # 🔴
    "warn": "\U0001f7e1",  # 🟡
    "info": "\u2139\ufe0f",  # ℹ️
}


def get_db_url() -> str:
    pw = "verdify"
    env_file = "/srv/verdify/.env"
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                if line.strip().startswith("POSTGRES_PASSWORD="):
                    pw = line.strip().split("=", 1)[1].strip().strip('"').strip("'")
    return f"postgresql://verdify:{pw}@localhost:5432/verdify"


def load_slack_token() -> str:
    return read_slack_token(SLACK_TOKEN_FILE)


def post_slack(token: str, channel: str, text: str, thread_ts: str | None = None) -> str | None:
    """Post a message to Slack. Returns the message ts for threading, or None on failure."""
    if DRY_RUN:
        log.info("DRY RUN — would post to Slack: %s", text[:100])
        return None

    payload = build_slack_payload(SLACK_SETTINGS, text, channel=channel, thread_ts=thread_ts)

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{SLACK_SETTINGS.api_base_url.rstrip('/')}/chat.postMessage",
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            if result.get("ok"):
                return result.get("ts")
            else:
                log.warning("Slack API error: %s", result.get("error", "unknown"))
                return None
    except Exception as e:
        log.warning("Slack post failed: %s", e)
        return None


def format_alert(severity: str, alert_type: str, message: str) -> str:
    emoji = SEVERITY_EMOJI.get(severity, "")
    sev_label = severity.upper()
    return f"{emoji} *[{sev_label}]* `{alert_type}` — {message}"


async def check_conditions(conn) -> list[dict]:
    """Return list of active alert conditions."""
    alerts = []

    # 1. Sensor offline
    rows = await conn.fetch("SELECT sensor_id, type, staleness_ratio FROM v_sensor_staleness WHERE is_stale = true")
    for r in rows:
        ratio = r["staleness_ratio"]
        ratio_str = f"{ratio:.0f}x" if ratio else "no data"
        alerts.append(
            {
                "alert_type": "sensor_offline",
                "severity": "warning",
                "category": "sensor",
                "sensor_id": r["sensor_id"],
                "zone": None,
                "message": f"Sensor `{r['sensor_id']}` offline ({ratio_str} expected interval)",
                "details": {"type": r["type"], "staleness_ratio": float(ratio) if ratio else None},
                "metric_value": float(ratio) if ratio else None,
            }
        )

    # 2. Relay stuck
    relay_context = await conn.fetchrow("""
        WITH latest_climate AS (
            SELECT ts, temp_avg, vpd_avg
              FROM climate
             WHERE ts >= now() - interval '10 minutes'
               AND temp_avg IS NOT NULL
               AND vpd_avg IS NOT NULL
             ORDER BY ts DESC
             LIMIT 1
        )
        SELECT c.temp_avg,
               c.vpd_avg,
               fn_setpoint_at('temp_high', c.ts) AS sp_temp_high,
               fn_setpoint_at('vpd_low', c.ts) AS sp_vpd_low,
               fn_setpoint_at('vpd_high', c.ts) AS sp_vpd_high,
               fn_equip_at('heat1', c.ts) AS heat1,
               fn_equip_at('heat2', c.ts) AS heat2,
               fn_equip_at('vent', c.ts) AS vent,
               fn_equip_at('fan1', c.ts) AS fan1,
               fn_equip_at('fan2', c.ts) AS fan2,
               (
                   SELECT ss.value
                     FROM system_state ss
                    WHERE ss.entity = 'greenhouse_state'
                      AND ss.ts <= c.ts
                    ORDER BY ss.ts DESC
                    LIMIT 1
               ) AS greenhouse_mode,
               c.ts
          FROM latest_climate c
    """)
    rows = await conn.fetch("SELECT equipment, hours_on, threshold_hours FROM v_relay_stuck WHERE is_stuck = true")
    for r in rows:
        equipment = r["equipment"]
        details = {
            "hours_on": float(r["hours_on"]),
            "threshold": float(r["threshold_hours"]),
            "state_source": "commanded_equipment_state",
        }
        message = f"Relay `{equipment}` stuck ON for {r['hours_on']:.1f}h (threshold: {r['threshold_hours']}h)"
        if relay_context:
            greenhouse_mode = (relay_context["greenhouse_mode"] or "").upper()
            details.update(
                {
                    "temp_avg": float(relay_context["temp_avg"]) if relay_context["temp_avg"] is not None else None,
                    "vpd_avg": float(relay_context["vpd_avg"]) if relay_context["vpd_avg"] is not None else None,
                    "sp_temp_high": float(relay_context["sp_temp_high"])
                    if relay_context["sp_temp_high"] is not None
                    else None,
                    "sp_vpd_low": float(relay_context["sp_vpd_low"])
                    if relay_context["sp_vpd_low"] is not None
                    else None,
                    "sp_vpd_high": float(relay_context["sp_vpd_high"])
                    if relay_context["sp_vpd_high"] is not None
                    else None,
                    "greenhouse_mode": relay_context["greenhouse_mode"],
                    "context_ts": relay_context["ts"].isoformat() if relay_context["ts"] else None,
                }
            )
            if equipment in ("heat1", "heat2"):
                heat_commanded = bool(relay_context[equipment])
                temp_avg = relay_context["temp_avg"]
                sp_temp_high = relay_context["sp_temp_high"]
                if (
                    heat_commanded
                    and temp_avg is not None
                    and sp_temp_high is not None
                    and float(temp_avg) <= float(sp_temp_high) + 0.5
                ):
                    continue
                message = (
                    f"Heater `{equipment}` commanded ON for {r['hours_on']:.1f}h "
                    f"while temp is not below the active band"
                )
            elif equipment in ("vent", "fan1", "fan2"):
                temp_avg = relay_context["temp_avg"]
                vpd_avg = relay_context["vpd_avg"]
                sp_temp_high = relay_context["sp_temp_high"]
                sp_vpd_low = relay_context["sp_vpd_low"]
                relay_commanded = bool(relay_context[equipment])
                temp_demands_air_exchange = (
                    temp_avg is not None and sp_temp_high is not None and float(temp_avg) > float(sp_temp_high)
                )
                vpd_demands_dehum = (
                    vpd_avg is not None and sp_vpd_low is not None and float(vpd_avg) < float(sp_vpd_low)
                )
                if relay_commanded and (
                    greenhouse_mode in AIR_EXCHANGE_RELAY_STUCK_MODES or temp_demands_air_exchange or vpd_demands_dehum
                ):
                    continue
                message = f"Relay `{equipment}` commanded ON for {r['hours_on']:.1f}h without current mode demand"
        alerts.append(
            {
                "alert_type": "relay_stuck",
                "severity": "warning",
                "category": "equipment",
                "sensor_id": f"equipment.{equipment}",
                "zone": None,
                "message": message,
                "details": details,
                "metric_value": float(r["hours_on"]),
                "threshold_value": float(r["threshold_hours"]),
            }
        )

    # 3. VPD stress over the (recalibrated) daily threshold, still active in the
    # last 15 minutes. Threshold is graded-history-aware (G6) — see
    # vpd_stress_threshold; it self-recalibrates once 146 grades the deficit.
    vpd_threshold = await vpd_stress_threshold(conn)
    row = await conn.fetchrow("""
        WITH daily AS (
            SELECT vpd_stress_hours::float AS vpd_stress_hours
              FROM v_stress_hours_today
             WHERE date >= date_trunc('day', now() AT TIME ZONE 'America/Denver')
             ORDER BY date DESC
             LIMIT 1
        ),
        recent AS (
            SELECT count(*)::int AS samples,
                   count(*) FILTER (WHERE vpd_avg > fn_setpoint_at('vpd_high', ts))::int AS high_samples,
                   avg(vpd_avg)::float AS avg_vpd,
                   avg(fn_setpoint_at('vpd_high', ts))::float AS avg_vpd_high
              FROM climate
             WHERE ts >= now() - interval '15 minutes'
               AND vpd_avg IS NOT NULL
        )
        SELECT daily.vpd_stress_hours,
               recent.samples,
               recent.high_samples,
               recent.avg_vpd,
               recent.avg_vpd_high,
               CASE WHEN recent.samples > 0
                    THEN recent.high_samples::float / recent.samples
                    ELSE 0.0
               END AS recent_high_fraction
          FROM daily CROSS JOIN recent
    """)
    if (
        row
        and row["vpd_stress_hours"]
        and float(row["vpd_stress_hours"]) > vpd_threshold
        and int(row["samples"] or 0) >= 3
        and float(row["recent_high_fraction"] or 0.0) >= 0.5
    ):
        hrs = float(row["vpd_stress_hours"])
        high_fraction = float(row["recent_high_fraction"] or 0.0)
        alerts.append(
            {
                "alert_type": "vpd_stress",
                "severity": "warning",
                "category": "climate",
                "sensor_id": "climate.vpd_avg",
                "zone": None,
                "message": (
                    f"VPD stress active: {hrs:.1f} hours today "
                    f"(threshold {vpd_threshold:.1f}h), {high_fraction:.0%} high in last 15m"
                ),
                "details": {
                    "vpd_stress_hours": hrs,
                    "recent_samples": int(row["samples"] or 0),
                    "recent_high_samples": int(row["high_samples"] or 0),
                    "recent_high_fraction": high_fraction,
                    "avg_vpd_15m": float(row["avg_vpd"]) if row["avg_vpd"] is not None else None,
                    "avg_vpd_high_15m": float(row["avg_vpd_high"]) if row["avg_vpd_high"] is not None else None,
                },
                "metric_value": hrs,
                "threshold_value": vpd_threshold,
            }
        )

    # 4. Temperature safety (freeze/overheat)
    row = await conn.fetchrow("""
        SELECT ts, temp_avg FROM climate
        WHERE temp_avg IS NOT NULL AND ts >= now() - interval '10 minutes'
        ORDER BY ts DESC LIMIT 1
    """)
    if row and row["temp_avg"] is not None:
        t = row["temp_avg"]
        if t < 40:
            alerts.append(
                {
                    "alert_type": "temp_safety",
                    "severity": "critical",
                    "category": "climate",
                    "sensor_id": "climate.temp_avg",
                    "zone": None,
                    "message": f"FREEZE WARNING — greenhouse temp {t:.1f}°F (threshold: 40°F)",
                    "details": {"temp_f": t, "threshold": 40},
                    "metric_value": t,
                    "threshold_value": 40.0,
                }
            )
        elif t > 100:
            alerts.append(
                {
                    "alert_type": "temp_safety",
                    "severity": "critical",
                    "category": "climate",
                    "sensor_id": "climate.temp_avg",
                    "zone": None,
                    "message": f"OVERHEAT WARNING — greenhouse temp {t:.1f}°F (threshold: 100°F)",
                    "details": {"temp_f": t, "threshold": 100},
                    "metric_value": t,
                    "threshold_value": 100.0,
                }
            )

    # 4b. VPD out of range (instantaneous check)
    if row and row["temp_avg"] is not None:
        vpd_row = await conn.fetchrow("""
            SELECT ts, vpd_avg FROM climate
            WHERE vpd_avg IS NOT NULL AND ts >= now() - interval '10 minutes'
            ORDER BY ts DESC LIMIT 1
        """)
        if vpd_row and vpd_row["vpd_avg"] is not None:
            v = vpd_row["vpd_avg"]
            if v < 0.3:
                alerts.append(
                    {
                        "alert_type": "vpd_extreme",
                        "severity": "warning",
                        "category": "climate",
                        "sensor_id": "climate.vpd_avg",
                        "zone": None,
                        "message": f"VPD dangerously low: {v:.2f} kPa (min threshold: 0.3 kPa)",
                        "details": {"vpd_kpa": v, "threshold": 0.3},
                        "metric_value": v,
                        "threshold_value": 0.3,
                    }
                )
            elif v > 3.0:
                alerts.append(
                    {
                        "alert_type": "vpd_extreme",
                        "severity": "warning",
                        "category": "climate",
                        "sensor_id": "climate.vpd_avg",
                        "zone": None,
                        "message": f"VPD critically high: {v:.2f} kPa (max threshold: 3.0 kPa)",
                        "details": {"vpd_kpa": v, "threshold": 3.0},
                        "metric_value": v,
                        "threshold_value": 3.0,
                    }
                )

    # 5. Leak detected
    row = await conn.fetchrow("""
        SELECT ts, state FROM equipment_state
        WHERE equipment = 'leak_detected'
        ORDER BY ts DESC LIMIT 1
    """)
    if row and row["state"]:
        alerts.append(
            {
                "alert_type": "leak_detected",
                "severity": "critical",
                "category": "water",
                "sensor_id": "equipment.leak_detected",
                "zone": None,
                "message": f"LEAK DETECTED — sensor active since {row['ts'].strftime('%H:%M')} UTC",
                "details": {"since": row["ts"].isoformat()},
            }
        )

    # 6. ESP32 reboot (uptime < 300s)
    row = await conn.fetchrow("""
        SELECT ts, uptime_s, reset_reason FROM diagnostics
        WHERE ts >= now() - interval '10 minutes' AND uptime_s IS NOT NULL
        ORDER BY ts DESC LIMIT 1
    """)
    if row and row["uptime_s"] < 300:
        alerts.append(
            {
                "alert_type": "esp32_reboot",
                "severity": "info",
                "category": "system",
                "sensor_id": "diag.uptime_s",
                "zone": None,
                "message": f"ESP32 rebooted — uptime {row['uptime_s']:.0f}s, reason: {row.get('reset_reason', 'unknown')}",
                "details": {"uptime_s": row["uptime_s"], "reset_reason": row.get("reset_reason")},
            }
        )

    # 6b. ESP32 boot-loop — 3+ reboots of the SAME build under BOOTLOOP_UPTIME_S
    # within a 10-min window (M7). A reboot is a sample whose uptime_s dropped
    # below the prior sample's (a reset), and is itself below the short-uptime
    # floor. Counting resets (not just low-uptime rows) avoids inflating one slow
    # boot's consecutive low samples into a false loop.
    bootloop = await conn.fetchrow(
        """
        WITH samples AS (
            SELECT ts, uptime_s, firmware_version,
                   lag(uptime_s) OVER (PARTITION BY firmware_version ORDER BY ts) AS prev_uptime
              FROM diagnostics
             WHERE ts >= now() - make_interval(mins => $1)
               AND uptime_s IS NOT NULL
        ),
        reboots AS (
            SELECT firmware_version, ts, uptime_s
              FROM samples
             WHERE uptime_s < $2
               AND (prev_uptime IS NULL OR uptime_s < prev_uptime)
        )
        SELECT firmware_version,
               count(*)::int AS reboot_count,
               min(uptime_s)::int AS min_uptime_s,
               max(ts) AS last_reboot_ts
          FROM reboots
         GROUP BY firmware_version
        HAVING count(*) >= $3
         ORDER BY count(*) DESC
         LIMIT 1
        """,
        BOOTLOOP_WINDOW_MIN,
        BOOTLOOP_UPTIME_S,
        BOOTLOOP_MIN_REBOOTS,
    )
    if bootloop:
        fw = bootloop["firmware_version"] or "unknown"
        count = int(bootloop["reboot_count"])
        alerts.append(
            {
                "alert_type": "esp32_boot_loop",
                "severity": "critical",
                "category": "system",
                "sensor_id": "diag.uptime_s",
                "zone": None,
                "message": (
                    f"ESP32 BOOT-LOOP — build `{fw}` rebooted {count}x in {BOOTLOOP_WINDOW_MIN} min "
                    f"(each uptime <{BOOTLOOP_UPTIME_S}s, min {bootloop['min_uptime_s']}s); "
                    "the running build is crash-looping — roll back"
                ),
                "details": {
                    "firmware_version": fw,
                    "reboot_count": count,
                    "window_min": BOOTLOOP_WINDOW_MIN,
                    "uptime_floor_s": BOOTLOOP_UPTIME_S,
                    "min_uptime_s": bootloop["min_uptime_s"],
                    "last_reboot_ts": bootloop["last_reboot_ts"].isoformat() if bootloop["last_reboot_ts"] else None,
                },
                "metric_value": float(count),
                "threshold_value": float(BOOTLOOP_MIN_REBOOTS),
            }
        )

    # 6c. Sustained low largest-free-block — heap fragmentation pressure (M7).
    # Alert only when MOST samples in the trailing window sit below the floor
    # (a sustained dip), not a single transient, and we have enough samples to
    # judge. Distinct from the firmware's debounced heap_pressure binary sensors:
    # this catches a slow fragmentation creep before the hard rail trips.
    lfb = await conn.fetchrow(
        """
        SELECT count(*)::int AS samples,
               count(*) FILTER (WHERE heap_largest_free_block_kb < $1)::int AS low_samples,
               round(min(heap_largest_free_block_kb)::numeric, 1) AS min_lfb,
               round(avg(heap_largest_free_block_kb)::numeric, 1) AS avg_lfb
          FROM diagnostics
         WHERE ts >= now() - make_interval(mins => $2)
           AND heap_largest_free_block_kb IS NOT NULL
        """,
        LFB_LOW_KB,
        LFB_WINDOW_MIN,
    )
    if (
        lfb
        and int(lfb["samples"] or 0) >= LFB_MIN_SAMPLES
        and int(lfb["low_samples"] or 0) >= LFB_LOW_FRACTION * int(lfb["samples"])
    ):
        low_frac = int(lfb["low_samples"]) / int(lfb["samples"])
        alerts.append(
            {
                "alert_type": "heap_largest_free_block_low",
                "severity": "warning",
                "category": "system",
                "sensor_id": "diag.heap_largest_free_block_kb",
                "zone": None,
                "message": (
                    f"ESP32 heap fragmentation — largest free block sustained <{LFB_LOW_KB:g}kB "
                    f"({low_frac:.0%} of last {LFB_WINDOW_MIN} min, min {lfb['min_lfb']}kB, avg {lfb['avg_lfb']}kB); "
                    "allocations may fail before the heap_pressure rail trips"
                ),
                "details": {
                    "min_lfb_kb": float(lfb["min_lfb"]) if lfb["min_lfb"] is not None else None,
                    "avg_lfb_kb": float(lfb["avg_lfb"]) if lfb["avg_lfb"] is not None else None,
                    "low_sample_fraction": round(low_frac, 3),
                    "samples": int(lfb["samples"]),
                    "window_min": LFB_WINDOW_MIN,
                    "threshold_kb": LFB_LOW_KB,
                },
                "metric_value": float(lfb["min_lfb"]) if lfb["min_lfb"] is not None else None,
                "threshold_value": LFB_LOW_KB,
            }
        )

    # 6d. Data-pipeline coverage — newly-dead pipelines absent from
    # v_data_pipeline_health (esp32_logs / irrigation_log / weather_station, M4).
    # Flags only a pipeline that was active within its recovery window but has now
    # gone stale beyond its cadence (a transition to dead), so chronically-off
    # pipelines do not page perpetually.
    for table, cadence_s, active_window_s, severity in PIPELINE_COVERAGE:
        cov = await conn.fetchrow(
            f"""
            SELECT GREATEST(EXTRACT(epoch FROM now() - max(ts))::int, 0) AS age_s,
                   count(*) FILTER (WHERE ts > now() - make_interval(secs => $1)) AS rows_recent
              FROM {table}
            """,  # noqa: S608 - table from a fixed module-level allowlist, not user input
            active_window_s,
        )
        if cov is None or cov["age_s"] is None:
            continue
        age_s = int(cov["age_s"])
        rows_recent = int(cov["rows_recent"] or 0)
        # Was alive in the window AND is now stale beyond cadence -> newly dead.
        if rows_recent > 0 and age_s > cadence_s:
            alerts.append(
                {
                    "alert_type": "pipeline_stale",
                    "severity": severity,
                    "category": "system",
                    "sensor_id": f"pipeline.{table}",
                    "zone": None,
                    "message": (
                        f"Data pipeline `{table}` went stale — no rows for {age_s // 60} min "
                        f"(cadence {cadence_s // 60} min) after being active in the trailing window"
                    ),
                    "details": {
                        "table": table,
                        "age_s": age_s,
                        "cadence_s": cadence_s,
                        "rows_in_active_window": rows_recent,
                    },
                    "metric_value": float(age_s),
                    "threshold_value": float(cadence_s),
                }
            )

    # 7. Planner heartbeat — no plan written in 14h (SUNSET→SUNRISE ~12.7h + 1.3h slack)
    plan_age = await conn.fetchval("SELECT EXTRACT(EPOCH FROM now() - MAX(created_at))::int FROM setpoint_plan")
    if plan_age is not None and plan_age > 50400:  # 14 hours
        alerts.append(
            {
                "alert_type": "planner_stale",
                "severity": "warning",
                "category": "system",
                "sensor_id": "system.planner",
                "zone": None,
                "message": f"No setpoint plan written in {plan_age // 3600}h — planner may be offline",
                "details": {"seconds_since_plan": plan_age},
            }
        )

    # 7b. planner_evaluation_missed — SUNRISE plans older than 26h with no
    # validated_at. The SUNRISE prompt declares plan_evaluate MANDATORY but
    # baseline (2026-05-10) showed only 41.5% of SUNRISE plans get evaluated
    # within 25h. Two thresholds: warning at 26h, critical at 48h.
    eval_missed = await conn.fetch("""
        SELECT plan_id,
               EXTRACT(EPOCH FROM (now() - created_at))::int AS age_seconds
          FROM plan_journal
         WHERE plan_id LIKE 'iris-%'
           AND validated_at IS NULL
           AND created_at < now() - interval '26 hours'
           AND EXTRACT(hour FROM created_at AT TIME ZONE 'America/Denver') BETWEEN 5 AND 9
         ORDER BY created_at
    """)
    for row in eval_missed:
        age_h = row["age_seconds"] // 3600
        severity = "critical" if row["age_seconds"] > 48 * 3600 else "warning"
        alerts.append(
            {
                "alert_type": "planner_evaluation_missed",
                "severity": severity,
                "category": "system",
                "sensor_id": "system.planner.evaluation",
                "zone": None,
                "message": (
                    f"SUNRISE plan {row['plan_id']} not evaluated ({age_h}h since created); "
                    f"prompt declares plan_evaluate MANDATORY"
                ),
                "details": {"plan_id": row["plan_id"], "age_hours": age_h},
            }
        )

    # 8. Dispatcher heartbeat — log file stale >15 min
    import os

    disp_log = "/srv/verdify/state/setpoint-dispatcher.log"
    if os.path.exists(disp_log):
        disp_age = int(datetime.now(UTC).timestamp()) - int(os.path.getmtime(disp_log))
        if disp_age > 900:  # 15 min
            alerts.append(
                {
                    "alert_type": "dispatcher_stale",
                    "severity": "warning",
                    "category": "system",
                    "sensor_id": "system.dispatcher",
                    "zone": None,
                    "message": f"Dispatcher log stale ({disp_age // 60}min) — cron may have stopped",
                    "details": {"seconds_since_dispatch": disp_age},
                }
            )

    # 9. Heat1 manual override detection — Shelly shows power but ESP32 says heater is OFF
    heat_override = await conn.fetchrow("""
        SELECT AVG(watts_heat) AS avg_watts, COUNT(*) AS samples
        FROM energy WHERE ts > now() - interval '10 minutes'
    """)
    if heat_override and heat_override["avg_watts"] and heat_override["avg_watts"] > 1000:
        # Check if ESP32 thinks heaters are off
        heat1_on = await conn.fetchval("""
            SELECT state FROM equipment_state WHERE equipment = 'heat1' ORDER BY ts DESC LIMIT 1
        """)
        heat2_on = await conn.fetchval("""
            SELECT state FROM equipment_state WHERE equipment = 'heat2' ORDER BY ts DESC LIMIT 1
        """)
        if not heat1_on and not heat2_on:
            watts = int(heat_override["avg_watts"])
            alerts.append(
                {
                    "alert_type": "heat_manual_override",
                    "severity": "warning",
                    "category": "equipment",
                    "sensor_id": "equipment.heat1",
                    "zone": None,
                    "message": f"Heat circuit drawing {watts}W but ESP32 reports both heaters OFF. Check heat1 manual override switch.",
                    "details": {"watts_heat": watts, "heat1_state": heat1_on, "heat2_state": heat2_on},
                    "metric_value": float(watts),
                    "threshold_value": 1000.0,
                }
            )

    # 11. Planner-policy clamp pressure — a planner-pushable param is being clamped
    # by the dispatcher band/guardrail layer faster than threshold/hour, meaning the
    # AI planning agent's tuning intent is silently dropped. Clamps are visible to Iris
    # (gather-plan-context) but were invisible to humans; this surfaces them to Slack.
    clamp_rows = await conn.fetch(
        """
        SELECT parameter,
               count(*)::int AS clamp_events,
               round(avg(abs(requested - applied))::numeric, 3) AS avg_delta,
               (array_agg(reason ORDER BY ts DESC))[1] AS latest_reason
          FROM setpoint_clamps
         WHERE ts > now() - interval '1 hour'
           AND parameter = ANY($1::text[])
         GROUP BY parameter
        HAVING count(*) > $2
         ORDER BY count(*) DESC
        """,
        sorted(PLANNER_PUSHABLE_REG),
        PLANNER_CLAMP_RATE_THRESHOLD_PER_HOUR,
    )
    for r in clamp_rows:
        param = r["parameter"]
        events = int(r["clamp_events"])
        avg_delta = float(r["avg_delta"]) if r["avg_delta"] is not None else None
        reason = r["latest_reason"] or "unknown"
        delta_str = f", avg clamp delta {avg_delta:g}" if avg_delta is not None else ""
        alerts.append(
            {
                "alert_type": "planner_clamp_pressure",
                "severity": "warning",
                "category": "system",
                "sensor_id": f"setpoint_clamps.{param}",
                "zone": None,
                "message": (
                    f"Planner-policy `{param}` clamped {events}x in the last hour "
                    f"(reason `{reason}`{delta_str}); the AI planning agent's tuning intent is being "
                    "silently capped by the band/guardrail layer"
                ),
                "details": {
                    "parameter": param,
                    "clamp_events_1h": events,
                    "avg_clamp_delta": avg_delta,
                    "latest_reason": reason,
                    "threshold_per_hour": PLANNER_CLAMP_RATE_THRESHOLD_PER_HOUR,
                },
                "metric_value": float(events),
                "threshold_value": float(PLANNER_CLAMP_RATE_THRESHOLD_PER_HOUR),
            }
        )

    # 10. Reactive planning trigger — sustained stress with stale plan
    MARKER = "/srv/verdify/state/reactive-plan-needed.txt"
    vpd_stress_active = any(a["alert_type"] == "vpd_stress" for a in alerts)
    temp_safety_active = any(a["alert_type"] == "temp_safety" for a in alerts)

    if vpd_stress_active or temp_safety_active:
        last_plan_age = await conn.fetchval(
            "SELECT EXTRACT(EPOCH FROM now() - MAX(created_at))::int FROM setpoint_plan"
        )
        marker_exists = os.path.exists(MARKER)
        marker_fresh = False
        if marker_exists:
            marker_age = int(datetime.now(UTC).timestamp()) - int(os.path.getmtime(MARKER))
            marker_fresh = marker_age < 7200  # 2h cooldown

        if last_plan_age and last_plan_age > 7200 and not marker_fresh:
            trigger = "vpd_stress" if vpd_stress_active else "temp_safety"
            with open(MARKER, "w") as f:
                f.write(f"{datetime.now(UTC).isoformat()}|{trigger}|plan_age={last_plan_age}s\n")
            log.info("REACTIVE TRIGGER: %s (last plan %ds ago) — wrote marker", trigger, last_plan_age)

    return alerts


async def post_digest(conn, slack_token: str) -> None:
    """Post a daily digest of open alerts and 24h summary to Slack."""
    open_alerts = await conn.fetch(
        "SELECT alert_type, severity, sensor_id, message, ts FROM alert_log WHERE disposition = 'open' ORDER BY severity DESC, ts"
    )
    stats_24h = await conn.fetchrow("""
        SELECT
            count(*) FILTER (WHERE disposition = 'resolved' AND resolved_at > now() - interval '24 hours') AS resolved_24h,
            count(*) FILTER (WHERE created_at > now() - interval '24 hours') AS new_24h,
            count(*) FILTER (WHERE disposition = 'open') AS currently_open
        FROM alert_log
    """)

    lines = ["*Daily Alert Digest*\n"]
    lines.append(
        f"Last 24h: {stats_24h['new_24h']} new, {stats_24h['resolved_24h']} resolved, {stats_24h['currently_open']} open\n"
    )

    if open_alerts:
        lines.append("*Open alerts:*")
        for a in open_alerts:
            emoji = {"critical": "\U0001f534", "warning": "\U0001f7e1", "info": "\u2139\ufe0f"}.get(
                a["severity"], "\u2753"
            )
            age_h = (datetime.now(UTC) - a["ts"]).total_seconds() / 3600
            lines.append(f"  {emoji} `{a['alert_type']}` — {a['sensor_id']} ({age_h:.0f}h ago)")
    else:
        lines.append("\u2705 No open alerts.")

    text = "\n".join(lines)
    if not DRY_RUN:
        post_slack(slack_token, SLACK_CHANNEL, text)
    log.info("Digest posted: %d open alerts", len(open_alerts))


async def main():
    conn = await asyncpg.connect(get_db_url())
    slack_token = load_slack_token()

    try:
        if DIGEST_MODE:
            await post_digest(conn, slack_token)
            return

        # --- Detect active conditions ---
        active_alerts = await check_conditions(conn)
        active_keys = {(a["alert_type"], a["sensor_id"]) for a in active_alerts}

        log.info("Detected %d active alert conditions", len(active_alerts))

        # --- Get currently open alerts ---
        open_alerts = await conn.fetch(
            "SELECT id, alert_type, sensor_id, slack_ts FROM alert_log WHERE disposition = 'open'"
        )
        open_keys = {(r["alert_type"], r["sensor_id"]): r for r in open_alerts}

        # --- Create new alerts (deduplication) ---
        new_count = 0
        for alert in active_alerts:
            key = (alert["alert_type"], alert["sensor_id"])
            if key in open_keys:
                continue  # Already alerted

            # Escalation: sensor_offline only posts to Slack after 2h
            # Critical alerts (temp_safety, leak_detected, vpd_extreme) always post immediately
            should_slack = should_post_alert(alert["alert_type"], alert["severity"], settings=SLACK_SETTINGS)

            slack_ts = None
            if should_slack and not DRY_RUN:
                runbook = await fetch_alert_runbook(conn, alert["alert_type"], alert["severity"])
                slack_text = (
                    format_alert(alert["severity"], alert["alert_type"], alert["message"])
                    + "\n"
                    + format_runbook(runbook, compact=True)
                )
                slack_ts = post_slack(slack_token, SLACK_CHANNEL, slack_text)

            # Insert into alert_log
            await conn.execute(
                """
                INSERT INTO alert_log (alert_type, severity, category, sensor_id, zone, message, details, source, slack_ts, metric_value, threshold_value)
                VALUES ($1, $2, $3, $4, $5, $6, $7, 'system', $8, $9, $10)
            """,
                alert["alert_type"],
                alert["severity"],
                alert.get("category", "system"),
                alert["sensor_id"],
                alert["zone"],
                alert["message"],
                json.dumps(alert["details"]) if alert["details"] else None,
                slack_ts,
                alert.get("metric_value"),
                alert.get("threshold_value"),
            )
            new_count += 1
            log.info("NEW ALERT: [%s] %s — %s", alert["severity"], alert["alert_type"], alert["message"][:80])

        # --- Escalate old sensor_offline alerts to Slack (2h+ open, no slack_ts) ---
        if not DRY_RUN:
            stale_alerts = await conn.fetch("""
                SELECT id, alert_type, sensor_id, message FROM alert_log
                WHERE disposition = 'open' AND alert_type = 'sensor_offline'
                AND slack_ts IS NULL AND ts < now() - interval '2 hours'
            """)
            for sa in stale_alerts:
                runbook = await fetch_alert_runbook(conn, sa["alert_type"], "warning")
                escalation_text = (
                    format_alert("warning", sa["alert_type"], f"[ESCALATED 2h+] {sa['message']}")
                    + "\n"
                    + format_runbook(runbook, compact=True)
                )
                esc_ts = post_slack(slack_token, SLACK_CHANNEL, escalation_text)
                if esc_ts:
                    await conn.execute("UPDATE alert_log SET slack_ts = $1 WHERE id = $2", esc_ts, sa["id"])
                    log.info("ESCALATED: sensor_offline for %s (2h+ open)", sa["sensor_id"])

        # --- Auto-resolve cleared alerts ---
        resolved_count = 0
        for key, row in open_keys.items():
            if key not in active_keys:
                await conn.execute(
                    """
                    UPDATE alert_log
                    SET disposition = 'resolved', resolved_at = now(), resolved_by = 'system',
                        resolution = 'auto-resolved — condition cleared'
                    WHERE id = $1
                """,
                    row["id"],
                )

                # Post resolution to Slack thread
                if row["slack_ts"]:
                    resolve_text = f"\u2705 *Resolved* — `{row['alert_type']}` for `{row['sensor_id']}` cleared."
                    post_slack(slack_token, SLACK_CHANNEL, resolve_text, thread_ts=row["slack_ts"])

                resolved_count += 1
                log.info("RESOLVED: [%s] %s", row["alert_type"], row["sensor_id"])

        # --- Summary ---
        total_open = await conn.fetchval("SELECT count(*) FROM alert_log WHERE disposition = 'open'")
        log.info("Summary: %d new, %d resolved, %d open", new_count, resolved_count, total_open)

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
