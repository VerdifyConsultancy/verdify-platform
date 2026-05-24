#!/usr/bin/env /srv/greenhouse/.venv/bin/python3
"""
analyze-greenhouse-snapshot.py — Analyze greenhouse camera snapshots via Gemini Vision.

Sends each snapshot to Gemini 2.0 Flash with structured prompt including zone context,
crop inventory, and current sensor readings. Inserts structured observations into
image_observations table.

Usage:
    analyze-greenhouse-snapshot.py                     # analyze today's latest snapshots
    analyze-greenhouse-snapshot.py --image /path.jpg   # analyze specific image
    analyze-greenhouse-snapshot.py --dry-run            # print prompt, don't call API
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent / "ingestor"))
import asyncpg
from ai_config import ai

logging.basicConfig(level=logging.INFO, format="%(asctime)s [vision] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DENVER = ZoneInfo("America/Denver")
VAULT_DIR = Path("/mnt/iris/verdify-vault/snapshots")
ZONES_CONFIG = Path("/srv/verdify/config/zones.yaml")


def get_db_url():
    pw = "verdify"
    if os.path.exists("/srv/verdify/.env"):
        with open("/srv/verdify/.env") as f:
            for line in f:
                if line.strip().startswith("POSTGRES_PASSWORD="):
                    pw = line.strip().split("=", 1)[1].strip().strip('"').strip("'")
    return f"postgresql://verdify:{pw}@localhost:5432/verdify"


def camera_from_filename(filename: str) -> str:
    """Extract camera name from snapshot filename like greenhouse_1_1200.jpg"""
    parts = filename.replace(".jpg", "").split("_")
    if len(parts) >= 2:
        return f"{parts[0]}_{parts[1]}"
    return "unknown"


async def get_current_conditions(conn) -> dict:
    """Get latest sensor readings for context."""
    row = await conn.fetchrow("""
        SELECT ROUND(temp_avg::numeric,1) AS temp, ROUND(rh_avg::numeric,1) AS rh,
               ROUND(vpd_avg::numeric,2) AS vpd, ROUND(lux::numeric,0) AS lux,
               ROUND(soil_moisture_south_1::numeric,1) AS soil_s1,
               ROUND(soil_moisture_west::numeric,1) AS soil_w
        FROM climate WHERE temp_avg IS NOT NULL ORDER BY ts DESC LIMIT 1
    """)
    return dict(row) if row else {}


async def get_crop_inventory(conn) -> list[dict]:
    """Get active crops for context."""
    rows = await conn.fetch("SELECT name, zone, position, stage, notes FROM crops WHERE is_active ORDER BY zone")
    return [dict(r) for r in rows]


async def get_zone_mapping(conn, camera: str) -> list[str]:
    """Get zones visible from a specific camera."""
    rows = await conn.fetch("SELECT zone FROM camera_zone_map WHERE camera = $1 ORDER BY coverage_pct DESC", camera)
    return [r["zone"] for r in rows]


def build_prompt(camera: str, zones: list[str], conditions: dict, crops: list[dict]) -> str:
    """Build the Gemini Vision analysis prompt from template."""
    return ai.render_template("vision", "prompt", camera=camera, zones=zones, conditions=conditions, crops=crops)


async def analyze_image(image_path: Path, conn, dry_run: bool = False) -> dict | None:
    """Analyze a single greenhouse snapshot via Gemini Vision."""
    camera = camera_from_filename(image_path.name)
    zones = await get_zone_mapping(conn, camera)
    if not zones:
        log.warning("No zone mapping for camera %s", camera)
        zones = ["unknown"]

    conditions = await get_current_conditions(conn)
    crops = await get_crop_inventory(conn)
    prompt = build_prompt(camera, zones, conditions, crops)

    if dry_run:
        log.info("DRY RUN — prompt for %s:\n%s", image_path.name, prompt[:500])
        return None

    # Call Gemini Vision
    start = time.time()
    try:
        from google import genai

        client = ai.get_client("vision")

        image_bytes = image_path.read_bytes()
        response = client.models.generate_content(
            model=ai.model_name("vision"),
            contents=[
                genai.types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
                prompt,
            ],
            config=genai.types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=ai.temperature("vision"),
            ),
        )

        elapsed_ms = int((time.time() - start) * 1000)
        raw_text = response.text.strip()

        # Strip markdown code fences if present
        if raw_text.startswith("```"):
            raw_text = raw_text.split("\n", 1)[1] if "\n" in raw_text else raw_text
            if raw_text.endswith("```"):
                raw_text = raw_text[:-3].strip()

        result = json.loads(raw_text)
        tokens = getattr(response, "usage_metadata", None)
        token_count = tokens.total_token_count if tokens else None

        # Insert into DB
        image_observation_id = await conn.fetchval(
            """
            INSERT INTO image_observations
                (ts, camera, zone, image_path, model, raw_response, crops_observed,
                 environment_notes, recommended_actions, processing_ms, tokens_used, confidence)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            RETURNING id
        """,
            datetime.now(UTC),
            camera,
            zones[0],
            str(image_path),
            ai.model_name("vision"),
            json.dumps(result),
            json.dumps(result.get("observations", [])),
            result.get("environment_notes"),
            result.get("recommended_actions"),
            elapsed_ms,
            token_count,
            result.get("overall_confidence"),
        )

        log.info(
            "%s: analyzed in %dms, %d observations, confidence %.2f",
            image_path.name,
            elapsed_ms,
            len(result.get("observations", [])),
            result.get("overall_confidence", 0),
        )

        # Auto-populate observations table per detected crop
        for obs in result.get("observations", []):
            crop_name = obs.get("crop", "")
            health = obs.get("health_score")
            if not crop_name or health is None:
                continue
            crop = await conn.fetchrow(
                """
                SELECT id, greenhouse_id, zone, position, zone_id, position_id
                FROM crops
                WHERE name ILIKE $1
                  AND is_active
                ORDER BY CASE WHEN lower(name) = lower($2) THEN 0 ELSE 1 END, id
                LIMIT 1
            """,
                f"%{crop_name}%",
                crop_name,
            )
            if crop:
                stress = obs.get("stress_indicators", [])
                notes = obs.get("notes", "")
                if stress:
                    notes = f"[{', '.join(stress)}] {notes}"
                await conn.execute(
                    """
                    INSERT INTO observations (
                        ts, crop_id, greenhouse_id, zone, position, zone_id, position_id,
                        obs_type, notes, source, health_score, image_observation_id
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, 'visual_health', $8, 'gemini-vision', $9, $10)
                """,
                    datetime.now(UTC),
                    crop["id"],
                    crop["greenhouse_id"],
                    obs.get("zone") or crop["zone"],
                    crop["position"],
                    crop["zone_id"],
                    crop["position_id"],
                    notes,
                    float(health) / 10.0,  # Normalize to 0.0-1.0
                    image_observation_id,
                )

        return result

    except json.JSONDecodeError as e:
        log.error("%s: Gemini returned invalid JSON: %s", image_path.name, str(e)[:100])
        return None
    except Exception as e:
        log.error("%s: Gemini API error: %s", image_path.name, e)
        return None


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", type=str, help="Specific image to analyze")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--date", type=str, help="Date folder to analyze (YYYY-MM-DD)")
    args = parser.parse_args()

    conn = await asyncpg.connect(get_db_url())

    try:
        if args.image:
            path = Path(args.image)
            if path.exists():
                await analyze_image(path, conn, args.dry_run)
            else:
                log.error("File not found: %s", args.image)
        else:
            # Analyze today's latest snapshots
            date_str = args.date or datetime.now(DENVER).strftime("%Y-%m-%d")
            date_dir = VAULT_DIR / date_str
            if not date_dir.exists():
                log.warning("No snapshots for %s", date_str)
                return

            images = sorted(date_dir.glob("*.jpg"))
            if not images:
                log.warning("No .jpg files in %s", date_dir)
                return

            # Analyze the latest snapshot per camera
            latest_per_camera = {}
            for img in images:
                cam = camera_from_filename(img.name)
                latest_per_camera[cam] = img

            log.info("Analyzing %d snapshots from %s", len(latest_per_camera), date_str)
            for _cam, img in sorted(latest_per_camera.items()):
                await analyze_image(img, conn, args.dry_run)

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
