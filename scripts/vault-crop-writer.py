#!/usr/bin/env python3
"""
vault-crop-writer.py — Write per-crop markdown files to Obsidian vault.

Usage:
    vault-crop-writer.py              # write all active crops
    vault-crop-writer.py --backfill   # same as default
    vault-crop-writer.py --crop-id 1  # single crop by ID
"""

import asyncio
import logging
import os
import re
import sys
from datetime import date
from pathlib import Path

import asyncpg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [vault-crop] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

VAULT_DIR = Path("/mnt/iris/verdify-vault/crops")


def get_db_url() -> str:
    pw = "verdify"
    env_file = "/srv/verdify/.env"
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                if line.strip().startswith("POSTGRES_PASSWORD="):
                    pw = line.strip().split("=", 1)[1].strip().strip('"').strip("'")
    return f"postgresql://verdify:{pw}@localhost:5432/verdify"


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\-]", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")


def fmt_date(d) -> str:
    if d is None:
        return "—"
    return str(d)


async def write_crop(conn, crop_id: int) -> bool:
    crop = await conn.fetchrow("SELECT * FROM crops WHERE id = $1", crop_id)
    if not crop:
        log.warning("Crop ID %d not found", crop_id)
        return False

    name = crop["name"] or "Unknown"
    variety = crop["variety"] or ""
    position = crop["position"] or ""
    zone = crop["zone"] or ""
    stage = crop["stage"] or "unknown"
    planted = crop["planted_date"]
    days_since = (date.today() - planted).days if planted else None

    # GDD from v_gdd if available
    try:
        gdd_row = await conn.fetchrow(
            "SELECT gdd_cumulative FROM v_gdd WHERE crop_id = $1 ORDER BY date DESC LIMIT 1", crop_id
        )
    except Exception:
        gdd_row = None

    # Events
    events = await conn.fetch(
        "SELECT ts, event_type, old_stage, new_stage, operator, notes "
        "FROM crop_events WHERE crop_id = $1 ORDER BY ts DESC",
        crop_id,
    )

    # Observations
    observations = await conn.fetch(
        "SELECT ts, obs_type, severity, species, affected_pct, photo_path, observer, notes "
        "FROM observations WHERE crop_id = $1 ORDER BY ts DESC",
        crop_id,
    )

    # Build markdown
    lines = []

    # Frontmatter
    lines.append("---")
    lines.append(f"name: {name}")
    if variety:
        lines.append(f"variety: {variety}")
    lines.append(f"position: {position}")
    lines.append(f"zone: {zone}")
    lines.append(f"stage: {stage}")
    lines.append(f"planted_date: {fmt_date(planted)}")
    tags = ["crop", zone] if zone else ["crop"]
    if "hydro" in position.lower():
        tags.append("hydro")
    lines.append(f"tags: [{', '.join(tags)}]")
    lines.append("---")
    lines.append("")

    # Title
    title = name
    if variety:
        title += f" ({variety})"
    title += f" — {position}"
    lines.append(f"# {title}")
    lines.append("")

    # Status
    lines.append("## Status")
    lines.append("")
    lines.append(f"- **Stage:** {stage}")
    lines.append(f"- **Planted:** {fmt_date(planted)}")
    if days_since is not None:
        lines.append(f"- **Days since planting:** {days_since}")
    if crop.get("expected_harvest"):
        days_to = (crop["expected_harvest"] - date.today()).days
        lines.append(f"- **Expected harvest:** {crop['expected_harvest']} ({days_to} days)")
    if crop.get("count"):
        lines.append(f"- **Count:** {crop['count']}")
    if gdd_row and gdd_row.get("gdd_cumulative") is not None:
        lines.append(f"- **GDD accumulated:** {gdd_row['gdd_cumulative']:.0f}")
    if crop.get("target_dli"):
        lines.append(f"- **Target DLI:** {crop['target_dli']:.0f} mol/m²/d")
    if crop.get("notes"):
        lines.append(f"- **Notes:** {crop['notes']}")
    lines.append("")

    # Events
    lines.append("## Events")
    lines.append("")
    if events:
        for e in events:
            ts_str = e["ts"].strftime("%Y-%m-%d %H:%M") if e["ts"] else "?"
            desc = e["event_type"]
            if e.get("old_stage") and e.get("new_stage"):
                desc += f" ({e['old_stage']} → {e['new_stage']})"
            if e.get("operator"):
                desc += f" — {e['operator']}"
            lines.append(f"- **{ts_str}:** {desc}")
            if e.get("notes"):
                lines.append(f"  - {e['notes']}")
    else:
        lines.append("*No events recorded yet.*")
    lines.append("")

    # Observations
    lines.append("## Observations")
    lines.append("")
    if observations:
        for o in observations:
            ts_str = o["ts"].strftime("%Y-%m-%d %H:%M") if o["ts"] else "?"
            desc = o["obs_type"]
            if o.get("severity"):
                desc += f" (severity {o['severity']}/5)"
            if o.get("species"):
                desc += f" — {o['species']}"
            if o.get("observer"):
                desc += f" by {o['observer']}"
            lines.append(f"- **{ts_str}:** {desc}")
            if o.get("notes"):
                lines.append(f"  - {o['notes']}")
            if o.get("photo_path"):
                lines.append(f"  - ![[{o['photo_path']}]]")
    else:
        lines.append("*No observations recorded yet.*")
    lines.append("")

    # Write file
    VAULT_DIR.mkdir(parents=True, exist_ok=True)
    filename = slugify(f"{name}-{variety}-{position}") + ".md" if variety else slugify(f"{name}-{position}") + ".md"
    filepath = VAULT_DIR / filename
    filepath.write_text("\n".join(lines))
    log.info("Wrote %s (%d bytes)", filepath.name, len("\n".join(lines)))
    return True


async def main():
    conn = await asyncpg.connect(get_db_url())

    try:
        if "--crop-id" in sys.argv:
            idx = sys.argv.index("--crop-id")
            crop_id = int(sys.argv[idx + 1])
            await write_crop(conn, crop_id)
        else:
            # Default / --backfill: write all active crops
            crops = await conn.fetch("SELECT id FROM crops WHERE is_active = true ORDER BY id")
            if not crops:
                log.info("No active crops — nothing to write")
                return
            written = 0
            for c in crops:
                if await write_crop(conn, c["id"]):
                    written += 1
            log.info("Wrote %d/%d active crops", written, len(crops))

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
