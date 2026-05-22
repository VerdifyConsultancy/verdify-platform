#!/usr/bin/env /srv/greenhouse/.venv/bin/python3
"""
tempest-sync.py — Sync Tempest/Panorama weather station data into Verdify.

Fetches current outdoor conditions from the Tempest weather station via HA REST API.
Writes to:
  - climate table (merged into ESP32 row when a recent indoor row exists)
  - weather_station table (dedicated outdoor weather history)

Forecast is handled separately by forecast-sync.py (hourly cron, 16-day, Open-Meteo).

Run every 5 minutes via cron: */5 * * * *
"""

import asyncio
import json
import logging
import os
import urllib.error
import urllib.request
from datetime import UTC, datetime

import asyncpg

# --- Configuration ---
HA_URL = "http://192.168.30.107:8123"
HA_TOKEN_FILE = "/mnt/agents/shared/credentials/ha_token.txt"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [tempest] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# --- Tempest entity → climate column mapping ---
TEMPEST_MAP = {
    "sensor.panorama_temperature": ("outdoor_temp_f", None),
    "sensor.panorama_humidity": ("outdoor_rh_pct", None),
    "sensor.panorama_wind_speed": ("wind_speed_mph", None),
    "sensor.panorama_wind_direction": ("wind_direction_deg", None),
    "sensor.panorama_illuminance": ("outdoor_lux", None),
    "sensor.panorama_irradiance": ("solar_irradiance_w_m2", None),
    "sensor.panorama_air_pressure": ("pressure_hpa", lambda v: v * 33.8639),
    "sensor.panorama_precipitation": ("precip_in", None),
    "sensor.panorama_uv_index": ("uv_index", None),
    "sensor.panorama_wind_gust": ("wind_gust_mph", None),
    "sensor.panorama_wind_lull": ("wind_lull_mph", None),
    "sensor.panorama_wind_speed_average": ("wind_speed_avg_mph", None),
    "sensor.panorama_wind_direction_average": ("wind_direction_avg_deg", None),
    "sensor.panorama_feels_like": ("feels_like_f", None),
    "sensor.panorama_wet_bulb_temperature": ("wet_bulb_temp_f", None),
    "sensor.panorama_vapor_pressure": ("vapor_pressure_inhg", None),
    "sensor.panorama_air_density": ("air_density_kg_m3", None),
    "sensor.panorama_precipitation_intensity": ("precip_intensity_in_h", None),
    "sensor.panorama_lightning_count": ("lightning_count", lambda v: int(v)),
    "sensor.panorama_lightning_average_distance": ("lightning_avg_dist_mi", None),
}


def load_token() -> str:
    with open(HA_TOKEN_FILE) as f:
        return f.read().strip()


def parse_float(s: str) -> float | None:
    if s in ("unavailable", "unknown", "None", ""):
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def fetch_ha_states(token: str, entity_ids: list[str]) -> dict[str, dict]:
    """Fetch current states for multiple entities from HA API."""
    results = {}
    for eid in entity_ids:
        url = f"{HA_URL}/api/states/{eid}"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                results[eid] = json.loads(resp.read())
        except Exception as e:
            log.warning("Failed to fetch %s: %s", eid, e)
    return results


def get_db_url() -> str:
    pw = "verdify"
    env_file = "/srv/verdify/.env"
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                if line.strip().startswith("POSTGRES_PASSWORD="):
                    pw = line.strip().split("=", 1)[1].strip().strip('"').strip("'")
    return f"postgresql://verdify:{pw}@localhost:5432/verdify"


async def sync_tempest(conn) -> None:
    """Fetch Tempest data from HA and insert into climate table."""
    token = load_token()
    entity_ids = list(TEMPEST_MAP.keys())
    states = fetch_ha_states(token, entity_ids)

    if not states:
        log.warning("No Tempest states returned from HA")
        return

    now = datetime.now(UTC)
    climate_cols = {"ts": now}

    for eid, (col, converter) in TEMPEST_MAP.items():
        if col is None:
            continue
        if eid in states:
            val = parse_float(states[eid].get("state", ""))
            if val is not None:
                climate_cols[col] = converter(val) if converter else val

    if len(climate_cols) > 1:
        # Merge outdoor data into the most recent ESP32 row (within 5 min)
        # instead of creating a separate orphan row
        latest_esp32 = await conn.fetchval(
            "SELECT ts FROM climate WHERE ts > now() - interval '5 minutes' "
            "AND temp_avg IS NOT NULL ORDER BY ts DESC LIMIT 1"
        )
        if latest_esp32:
            # UPDATE the existing ESP32 row with outdoor columns
            outdoor_cols = {c: v for c, v in climate_cols.items() if c != "ts"}
            if outdoor_cols:
                set_parts = []
                set_vals = []
                for i, (c, v) in enumerate(outdoor_cols.items()):
                    set_parts.append(f"{c} = ${i + 1}")
                    set_vals.append(v)
                set_vals.append(latest_esp32)
                await conn.execute(f"UPDATE climate SET {', '.join(set_parts)} WHERE ts = ${len(set_vals)}", *set_vals)
                log.info(
                    "Tempest: merged %d outdoor cols into ESP32 row at %s",
                    len(outdoor_cols),
                    latest_esp32.strftime("%H:%M:%S"),
                )
        else:
            log.warning("Tempest: skipped climate overlay; no recent indoor climate row")

        # Also write to dedicated weather_station table
        WS_COL_MAP = {
            "outdoor_temp_f": "temp_f",
            "outdoor_rh_pct": "rh_pct",
            "wind_speed_mph": "wind_speed_mph",
            "wind_gust_mph": "wind_gust_mph",
            "wind_lull_mph": "wind_lull_mph",
            "wind_direction_deg": "wind_dir_deg",
            "wind_speed_avg_mph": "wind_speed_avg_mph",
            "wind_direction_avg_deg": "wind_dir_avg_deg",
            "outdoor_lux": "outdoor_lux",
            "solar_irradiance_w_m2": "solar_irradiance_w_m2",
            "pressure_hpa": "pressure_hpa",
            "uv_index": "uv_index",
            "precip_in": "precip_in",
            "precip_intensity_in_h": "precip_intensity_in_h",
            "lightning_count": "lightning_count",
            "lightning_avg_dist_mi": "lightning_avg_dist_mi",
            "feels_like_f": "feels_like_f",
            "wet_bulb_temp_f": "wet_bulb_temp_f",
            "vapor_pressure_inhg": "vapor_pressure_inhg",
            "air_density_kg_m3": "air_density_kg_m3",
        }
        ws_cols = {"ts": now, "source": "tempest"}
        for climate_col, ws_col in WS_COL_MAP.items():
            if climate_col in climate_cols:
                ws_cols[ws_col] = climate_cols[climate_col]
        if len(ws_cols) > 2:
            ws_c = list(ws_cols.keys())
            ws_v = [ws_cols[c] for c in ws_c]
            ws_ph = ", ".join(f"${i + 1}" for i in range(len(ws_v)))
            ws_cn = ", ".join(ws_c)
            await conn.execute(f"INSERT INTO weather_station ({ws_cn}) VALUES ({ws_ph})", *ws_v)

        log.info("Tempest: %d columns → weather_station", len(climate_cols) - 1)
    else:
        log.warning("Tempest: no valid data to insert")


async def main():
    db_url = get_db_url()
    conn = await asyncpg.connect(db_url)

    try:
        await sync_tempest(conn)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
