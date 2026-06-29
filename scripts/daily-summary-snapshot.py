#!/usr/bin/env python3
"""
daily-summary-snapshot.py — Compute and upsert daily climate aggregates + cost estimates.

Complements the ESP32 ingestor which writes cycle counts and runtimes via DAILY_ACCUM_MAP.
This script adds: climate min/max/avg, stress hours, cost estimates, and notes.

Runs at 00:05 UTC daily via cron (captures the completed day).

Usage:
    daily-summary-snapshot.py              # snapshot yesterday
    daily-summary-snapshot.py --date 2026-03-23   # snapshot a specific date
    daily-summary-snapshot.py --backfill 30        # backfill last N days
"""

import asyncio
import logging
import os
import sys
from datetime import date, datetime, timedelta

import asyncpg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [daily-snapshot] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# Wattage for cost estimation (from equipment_assets)
WATTAGE = {
    "heat1": 1500,
    "heat2": 0,  # heat2 is gas
    "fan1": 52,
    "fan2": 52,
    "fog": 1644,  # AquaFog XE 2000 observed draw
    "vent": 10,
    "grow_light_main": 630,
    "grow_light_grow": 816,  # main: 15x 4FT@42W=630W, grow: 34x 2FT@24W=816W, total 1446W
}
ELECTRIC_RATE = 0.111  # $/kWh (confirmed by Jason, Longmont CO residential)
GAS_RATE = 0.83  # $/therm (Xcel Energy marginal: variable + franchise fee + sales tax)
WATER_RATE = 0.00484  # $/gal ($4.84/1000 gal, lowest tier)
HEAT2_BTU = 75000  # BTU/h (Lennox LF24-75A-5 natural gas furnace)
THERM_BTU = 100000


def get_db_url() -> str:
    pw = "verdify"
    env_file = "/srv/verdify/.env"
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                if line.strip().startswith("POSTGRES_PASSWORD="):
                    pw = line.strip().split("=", 1)[1].strip().strip('"').strip("'")
    return f"postgresql://verdify:{pw}@localhost:5432/verdify"


async def snapshot_day(conn, target_date: date) -> bool:
    """Compute and upsert daily summary for a single date. Returns True if data found."""

    # Date boundaries in Denver local time converted to UTC-aware datetimes
    from zoneinfo import ZoneInfo

    denver = ZoneInfo("America/Denver")
    day_start = datetime(target_date.year, target_date.month, target_date.day, tzinfo=denver)
    day_end = day_start + timedelta(days=1)

    # ── Climate aggregates ──
    climate = await conn.fetchrow(
        """
        SELECT
            MIN(temp_avg) AS temp_min,
            MAX(temp_avg) AS temp_max,
            ROUND(AVG(temp_avg)::numeric, 1) AS temp_avg,
            MIN(rh_avg) AS rh_min,
            MAX(rh_avg) AS rh_max,
            ROUND(AVG(rh_avg)::numeric, 1) AS rh_avg,
            MIN(vpd_avg) AS vpd_min,
            MAX(vpd_avg) AS vpd_max,
            ROUND(AVG(vpd_avg)::numeric, 2) AS vpd_avg,
            ROUND(AVG(co2_ppm)::numeric, 0) AS co2_avg,
            MIN(outdoor_temp_f) AS outdoor_temp_min,
            MAX(outdoor_temp_f) AS outdoor_temp_max,
            MAX(dli_today) AS dli_final,
            -- Water: use today's max minus yesterday's max (consecutive-day delta)
            -- NOT max-min within the day (which catches midnight counter reload artifacts)
            MAX(water_total_gal) AS water_max_today,
            MAX(mister_water_today) AS mister_water
        FROM climate
        WHERE ts >= $1 AND ts < $2
        AND temp_avg IS NOT NULL
    """,
        day_start,
        day_end,
    )

    if not climate or climate["temp_avg"] is None:
        log.warning("No climate data for %s", target_date)
        return False

    # ── Stress hours (count rows where out of band, multiply by sample interval) ──
    stress = await conn.fetchrow(
        """
        SELECT
            ROUND(COUNT(*) FILTER (WHERE temp_avg > 85) * 2.0 / 60, 2) AS stress_heat,
            ROUND(COUNT(*) FILTER (WHERE temp_avg < 50) * 2.0 / 60, 2) AS stress_cold,
            ROUND(COUNT(*) FILTER (WHERE vpd_avg > 2.0) * 2.0 / 60, 2) AS stress_vpd_high,
            ROUND(COUNT(*) FILTER (WHERE vpd_avg < 0.4) * 2.0 / 60, 2) AS stress_vpd_low
        FROM climate
        WHERE ts >= $1 AND ts < $2
        AND temp_avg IS NOT NULL
    """,
        day_start,
        day_end,
    )

    # ── Equipment runtimes (from existing daily_summary row if ingestor wrote it) ──
    existing = await conn.fetchrow("SELECT * FROM daily_summary WHERE date = $1", target_date)

    # Get runtimes — prefer ingestor values if they exist
    rt_heat1 = float(existing["runtime_heat1_min"] or 0) if existing else 0
    rt_heat2 = float(existing["runtime_heat2_min"] or 0) if existing else 0
    rt_fog = float(existing["runtime_fog_min"] or 0) if existing else 0
    rt_fan1 = float(existing["runtime_fan1_min"] or 0) if existing else 0
    rt_fan2 = float(existing["runtime_fan2_min"] or 0) if existing else 0
    rt_vent = float(existing["runtime_vent_min"] or 0) if existing else 0
    rt_gl = float(existing["runtime_grow_light_min"] or 0) if existing else 0

    # ── Cost estimation ──
    kwh_heat = (rt_heat1 / 60) * (WATTAGE["heat1"] / 1000)
    kwh_fans = ((rt_fan1 + rt_fan2) / 60) * (WATTAGE["fan1"] / 1000)
    kwh_fog = (rt_fog / 60) * (WATTAGE["fog"] / 1000)
    kwh_vent = (rt_vent / 60) * (WATTAGE["vent"] / 1000)
    kwh_gl = (rt_gl / 60) * (
        (WATTAGE["grow_light_main"] + WATTAGE["grow_light_grow"]) / 1000
    )  # both circuits on same schedule
    kwh_estimated = round(kwh_heat + kwh_fans + kwh_fog + kwh_vent + kwh_gl, 2)

    therms_estimated = round((rt_heat2 / 60) * HEAT2_BTU / THERM_BTU, 3)

    # Water: consecutive-day delta (today's max minus yesterday's max)
    # Use v_water_daily which already handles counter resets correctly
    water_row = await conn.fetchval(
        """
        SELECT used_gal FROM v_water_daily WHERE day::date = $1
    """,
        target_date,
    )
    water_gal = float(water_row) if water_row and water_row > 0 else 0.0
    cost_electric = round(kwh_estimated * ELECTRIC_RATE, 2)
    cost_gas = round(therms_estimated * GAS_RATE, 2)
    cost_water = round(water_gal * WATER_RATE, 2)
    cost_total = round(cost_electric + cost_gas + cost_water, 2)

    # ── Peak demand from Shelly EM ──
    peak_row = await conn.fetchval(
        """
        SELECT ROUND((MAX(watts_total) / 1000.0)::numeric, 2)
        FROM energy WHERE ts >= $1 AND ts < $2
    """,
        day_start,
        day_end,
    )
    peak_kw = float(peak_row) if peak_row else None

    # ── Upsert ──
    await conn.execute(
        """
        INSERT INTO daily_summary (
            date, temp_min, temp_max, temp_avg, rh_min, rh_max, rh_avg,
            vpd_min, vpd_max, vpd_avg, co2_avg,
            outdoor_temp_min, outdoor_temp_max,
            dli_final, water_used_gal, mister_water_gal,
            stress_hours_heat, stress_hours_cold, stress_hours_vpd_high, stress_hours_vpd_low,
            kwh_estimated, therms_estimated,
            cost_electric, cost_gas, cost_water, cost_total,
            peak_kw, captured_at
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13,
            $14, $15, $16, $17, $18, $19, $20, $21, $22, $23, $24, $25, $26, $27, now()
        )
        ON CONFLICT (date) DO UPDATE SET
            temp_min = COALESCE(EXCLUDED.temp_min, daily_summary.temp_min),
            temp_max = COALESCE(EXCLUDED.temp_max, daily_summary.temp_max),
            temp_avg = COALESCE(EXCLUDED.temp_avg, daily_summary.temp_avg),
            rh_min = COALESCE(EXCLUDED.rh_min, daily_summary.rh_min),
            rh_max = COALESCE(EXCLUDED.rh_max, daily_summary.rh_max),
            rh_avg = COALESCE(EXCLUDED.rh_avg, daily_summary.rh_avg),
            vpd_min = COALESCE(EXCLUDED.vpd_min, daily_summary.vpd_min),
            vpd_max = COALESCE(EXCLUDED.vpd_max, daily_summary.vpd_max),
            vpd_avg = COALESCE(EXCLUDED.vpd_avg, daily_summary.vpd_avg),
            co2_avg = COALESCE(EXCLUDED.co2_avg, daily_summary.co2_avg),
            outdoor_temp_min = COALESCE(EXCLUDED.outdoor_temp_min, daily_summary.outdoor_temp_min),
            outdoor_temp_max = COALESCE(EXCLUDED.outdoor_temp_max, daily_summary.outdoor_temp_max),
            dli_final = COALESCE(EXCLUDED.dli_final, daily_summary.dli_final),
            water_used_gal = COALESCE(EXCLUDED.water_used_gal, daily_summary.water_used_gal),
            mister_water_gal = COALESCE(EXCLUDED.mister_water_gal, daily_summary.mister_water_gal),
            stress_hours_heat = EXCLUDED.stress_hours_heat,
            stress_hours_cold = EXCLUDED.stress_hours_cold,
            stress_hours_vpd_high = EXCLUDED.stress_hours_vpd_high,
            stress_hours_vpd_low = EXCLUDED.stress_hours_vpd_low,
            kwh_estimated = COALESCE(EXCLUDED.kwh_estimated, daily_summary.kwh_estimated),
            therms_estimated = COALESCE(EXCLUDED.therms_estimated, daily_summary.therms_estimated),
            cost_electric = COALESCE(EXCLUDED.cost_electric, daily_summary.cost_electric),
            cost_gas = COALESCE(EXCLUDED.cost_gas, daily_summary.cost_gas),
            cost_water = COALESCE(EXCLUDED.cost_water, daily_summary.cost_water),
            cost_total = COALESCE(EXCLUDED.cost_total, daily_summary.cost_total),
            peak_kw = COALESCE(EXCLUDED.peak_kw, daily_summary.peak_kw),
            captured_at = now()
    """,
        target_date,
        climate["temp_min"],
        climate["temp_max"],
        float(climate["temp_avg"]),
        climate["rh_min"],
        climate["rh_max"],
        float(climate["rh_avg"]),
        climate["vpd_min"],
        climate["vpd_max"],
        float(climate["vpd_avg"]),
        float(climate["co2_avg"]) if climate["co2_avg"] else None,
        climate["outdoor_temp_min"],
        climate["outdoor_temp_max"],
        climate["dli_final"],
        water_gal,
        float(climate["mister_water"] or 0),
        float(stress["stress_heat"]),
        float(stress["stress_cold"]),
        float(stress["stress_vpd_high"]),
        float(stress["stress_vpd_low"]),
        kwh_estimated,
        therms_estimated,
        cost_electric,
        cost_gas,
        cost_water,
        cost_total,
        peak_kw,
    )

    log.info(
        "%s: temp %.0f–%.0f°F, VPD %.1f–%.1f, DLI %.1f, $%.2f, stress: heat=%.1fh vpd_hi=%.1fh",
        target_date,
        climate["temp_min"] or 0,
        climate["temp_max"] or 0,
        climate["vpd_min"] or 0,
        climate["vpd_max"] or 0,
        climate["dli_final"] or 0,
        cost_total,
        float(stress["stress_heat"]),
        float(stress["stress_vpd_high"]),
    )
    return True


async def main():
    conn = await asyncpg.connect(get_db_url())

    try:
        if "--backfill" in sys.argv:
            idx = sys.argv.index("--backfill")
            days = int(sys.argv[idx + 1]) if idx + 1 < len(sys.argv) else 30
            today = date.today()
            count = 0
            for i in range(days, 0, -1):
                d = today - timedelta(days=i)
                if await snapshot_day(conn, d):
                    count += 1
            log.info("Backfill complete: %d/%d days", count, days)

        elif "--date" in sys.argv:
            idx = sys.argv.index("--date")
            target = date.fromisoformat(sys.argv[idx + 1])
            await snapshot_day(conn, target)

        else:
            # Default: snapshot yesterday
            yesterday = date.today() - timedelta(days=1)
            await snapshot_day(conn, yesterday)

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
