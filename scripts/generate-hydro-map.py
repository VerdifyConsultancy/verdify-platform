#!/usr/bin/env python3
"""
generate-hydro-map.py — Generate 60-position hydroponic layout HTML map.

Usage:
    generate-hydro-map.py           # generate map
    generate-hydro-map.py --open    # generate and open in browser
"""

import asyncio
import logging
import os
from datetime import date
from pathlib import Path

import asyncpg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [hydro-map] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

OUTPUT = Path("/srv/verdify/reports/hydro-map.html")

STAGE_COLORS = {
    "seed": "#FFF9C4",
    "seedling": "#C8E6C9",
    "vegetative": "#66BB6A",
    "flowering": "#F8BBD0",
    "fruiting": "#FFCC80",
    "harvest": "#FFE0B2",
    "empty": "#E0E0E0",
}

STAGE_TEXT_COLORS = {
    "seed": "#5D4037",
    "seedling": "#1B5E20",
    "vegetative": "#FFFFFF",
    "flowering": "#880E4F",
    "fruiting": "#E65100",
    "harvest": "#BF360C",
    "empty": "#9E9E9E",
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


def render_cell(pos: int, crop: dict | None) -> str:
    if crop:
        stage = crop.get("stage", "unknown")
        bg = STAGE_COLORS.get(stage, "#E0E0E0")
        fg = STAGE_TEXT_COLORS.get(stage, "#333")
        name = crop["name"]
        variety = crop.get("variety") or ""
        days = (date.today() - crop["planted_date"]).days if crop.get("planted_date") else "?"
        return f"""<td class="cell occupied" style="background:{bg};color:{fg}">
            <div class="pos">#{pos}</div>
            <div class="name">{name}</div>
            <div class="detail">{variety}</div>
            <div class="detail">{stage} &middot; {days}d</div>
        </td>"""
    else:
        return f"""<td class="cell empty">
            <div class="pos">#{pos}</div>
            <div class="name">&nbsp;</div>
        </td>"""


def render_html(crops: dict) -> str:
    today = date.today().isoformat()
    occupied = sum(1 for c in crops.values() if c)

    top_row = "".join(render_cell(i, crops.get(i)) for i in range(1, 31))
    bottom_row = "".join(render_cell(i, crops.get(i)) for i in range(31, 61))

    legend_items = "".join(
        f'<span class="legend-item" style="background:{color};color:{STAGE_TEXT_COLORS[stage]}">{stage}</span>'
        for stage, color in STAGE_COLORS.items()
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Hydro Pod Map — {today}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; padding: 16px; background: #FAFAFA; }}
  h1 {{ font-size: 18px; margin-bottom: 4px; color: #333; }}
  .meta {{ font-size: 12px; color: #666; margin-bottom: 12px; }}
  table {{ border-collapse: collapse; width: 100%; table-layout: fixed; }}
  .row-label {{ font-size: 11px; font-weight: 600; color: #666; padding: 4px 0; }}
  .cell {{
    border: 1px solid #CCC; padding: 3px; text-align: center;
    vertical-align: top; font-size: 10px; height: 70px;
    transition: all 0.2s;
  }}
  .cell:hover {{ box-shadow: 0 0 6px rgba(0,0,0,0.3); z-index: 1; position: relative; }}
  .cell.empty {{ background: {STAGE_COLORS["empty"]}; color: {STAGE_TEXT_COLORS["empty"]}; }}
  .pos {{ font-size: 9px; font-weight: 700; opacity: 0.6; }}
  .name {{ font-size: 11px; font-weight: 600; margin: 2px 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .detail {{ font-size: 9px; opacity: 0.8; }}
  .legend {{ margin-top: 12px; display: flex; gap: 8px; flex-wrap: wrap; }}
  .legend-item {{ padding: 3px 10px; border-radius: 3px; font-size: 11px; font-weight: 500; }}
  .stats {{ font-size: 12px; color: #555; margin-top: 8px; }}
  @media print {{
    body {{ padding: 8px; }}
    .cell {{ height: 60px; font-size: 9px; }}
    .cell:hover {{ box-shadow: none; }}
    @page {{ size: landscape; margin: 0.5cm; }}
  }}
</style>
</head>
<body>
<h1>Hydroponic Pod Allocation Map</h1>
<div class="meta">Generated: {today} &middot; {occupied}/60 pods occupied</div>

<div class="row-label">Top Rail (Front) &mdash; Positions 1&ndash;30</div>
<table><tr>{top_row}</tr></table>

<div class="row-label" style="margin-top:8px">Bottom Rail (Back) &mdash; Positions 31&ndash;60</div>
<table><tr>{bottom_row}</tr></table>

<div class="legend">{legend_items}</div>
<div class="stats">{occupied} occupied &middot; {60 - occupied} empty &middot; {len(set(c["name"] for c in crops.values() if c))} crop types</div>
</body>
</html>"""


async def main():
    conn = await asyncpg.connect(get_db_url())
    try:
        rows = await conn.fetch(
            "SELECT name, variety, position, stage, planted_date "
            "FROM crops WHERE is_active = true AND position LIKE 'HYDRO-%' "
            "ORDER BY position"
        )

        crops = {}
        for r in rows:
            pos_str = r["position"].replace("HYDRO-", "")
            try:
                pos = int(pos_str)
                crops[pos] = dict(r)
            except ValueError:
                log.warning("Skipping non-numeric position: %s", r["position"])

        # Fill empties
        for i in range(1, 61):
            if i not in crops:
                crops[i] = None

        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        html = render_html(crops)
        OUTPUT.write_text(html)
        log.info("Generated %s (%d occupied, %d empty)", OUTPUT, len(rows), 60 - len(rows))

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
