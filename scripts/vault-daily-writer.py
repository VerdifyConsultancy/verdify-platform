#!/usr/bin/env python3
"""
vault-daily-writer.py — Write daily_summary to Obsidian vault as markdown.

Usage:
    vault-daily-writer.py              # write yesterday
    vault-daily-writer.py --date 2026-03-24
    vault-daily-writer.py --backfill   # all dates missing vault files
"""

import asyncio
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import asyncpg
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
from verdify_schemas import DailyVaultFrontmatter  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [vault-writer] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

VAULT_DIR = Path("/mnt/iris/verdify-vault/daily")


def get_db_url() -> str:
    pw = "verdify"
    env_file = "/srv/verdify/.env"
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                if line.strip().startswith("POSTGRES_PASSWORD="):
                    pw = line.strip().split("=", 1)[1].strip().strip('"').strip("'")
    return f"postgresql://verdify:{pw}@localhost:5432/verdify"


def fmt(val, unit="", decimals=1):
    if val is None:
        return "—"
    if isinstance(val, float):
        return f"{val:.{decimals}f}{unit}"
    return f"{val}{unit}"


def render_markdown(row: dict) -> str:
    d = row["date"]

    # Compute derived fields
    fan_h = (float(row.get("runtime_fan1_min") or 0) + float(row.get("runtime_fan2_min") or 0)) / 60
    mister_h = (
        float(row.get("runtime_mister_south_h") or 0)
        + float(row.get("runtime_mister_west_h") or 0)
        + float(row.get("runtime_mister_center_h") or 0)
    )
    heat1_h = float(row.get("runtime_heat1_min") or 0) / 60
    heat2_h = float(row.get("runtime_heat2_min") or 0) / 60
    fog_h = float(row.get("runtime_fog_min") or 0) / 60
    vent_h = float(row.get("runtime_vent_min") or 0) / 60
    gl_h = float(row.get("runtime_grow_light_min") or 0) / 60
    total_cycles = sum(
        int(row.get(f"cycles_{e}") or 0) for e in ["fan1", "fan2", "heat1", "heat2", "fog", "vent", "grow_light"]
    )

    lines = []

    # Sprint 22: frontmatter now built through DailyVaultFrontmatter — typos
    # and out-of-range values fail loud here, not silently in Obsidian.
    def _round(val, digits=1):
        if val is None:
            return None
        try:
            return round(float(val), digits)
        except (TypeError, ValueError):
            return None

    cost_total = row.get("cost_total")
    fm = DailyVaultFrontmatter(
        date=d,
        tags=["daily", "greenhouse"],
        temp_avg=_round(row.get("temp_avg")),
        vpd_avg=_round(row.get("vpd_avg"), 2),
        # Preserve the frontmatter key for compatibility, but source it only
        # from the availability-gated product view (NULL while invalid).
        dli=_round(row.get("dli_product_value")),
        dli_availability=row.get("dli_availability") or "unavailable",
        dli_unavailable_reason=row.get("dli_unavailable_reason") or "validity_contract_missing",
        dli_provenance=row.get("dli_provenance") or "unknown_unvalidated_source",
        dli_validity_revision=row.get("dli_validity_revision") or "missing",
        dli_valid_from=str(row.get("dli_valid_from") or "2024-01-01T00:00:00Z"),
        dli_valid_to=str(row.get("dli_valid_to") or "open"),
        cost_total=f"${float(cost_total):.2f}" if cost_total is not None else None,
        water_gal=int(row["water_used_gal"]) if row.get("water_used_gal") is not None else None,
        stress_vpd_h=_round(row.get("stress_hours_vpd_high")),
        stress_heat_h=_round(row.get("stress_hours_heat")),
    )
    frontmatter = fm.model_dump(mode="json", exclude_none=True)
    # Stable Dataview key, now explicitly null rather than omitted or zero.
    frontmatter["dli"] = None
    yaml_block = yaml.safe_dump(
        frontmatter,
        sort_keys=False,
        default_flow_style=None,
    )
    lines.append("---")
    lines.append(yaml_block.rstrip())
    lines.append("---")
    lines.append("")

    # Title
    lines.append(f"# Greenhouse Daily — {d}")
    lines.append("")

    # Climate
    lines.append("## Climate")
    lines.append("")
    lines.append("| Metric | Min | Avg | Max |")
    lines.append("|--------|-----|-----|-----|")
    lines.append(
        f"| Temperature (°F) | {fmt(row.get('temp_min'))} | {fmt(row.get('temp_avg'))} | {fmt(row.get('temp_max'))} |"
    )
    lines.append(
        f"| Relative Humidity (%) | {fmt(row.get('rh_min'))} | {fmt(row.get('rh_avg'))} | {fmt(row.get('rh_max'))} |"
    )
    lines.append(
        f"| VPD (kPa) | {fmt(row.get('vpd_min'), decimals=2)} | {fmt(row.get('vpd_avg'), decimals=2)} | {fmt(row.get('vpd_max'), decimals=2)} |"
    )
    lines.append("")
    lines.append("- **Interior crop DLI:** unavailable")
    lines.append(f"  - Reason: `{row.get('dli_unavailable_reason') or 'validity_contract_missing'}`")
    lines.append(f"  - Provenance: `{row.get('dli_provenance') or 'unknown_unvalidated_source'}`")
    lines.append(
        f"  - Validity: `{row.get('dli_validity_revision') or 'missing'}`; "
        f"{row.get('dli_valid_from') or '2024-01-01T00:00:00Z'} → {row.get('dli_valid_to') or 'open'}"
    )
    lines.append(f"- **CO₂:** {fmt(row.get('co2_avg'), decimals=0)} ppm")
    lines.append("")

    # Outdoor
    if row.get("outdoor_temp_min") or row.get("outdoor_temp_max"):
        lines.append("## Outdoor Conditions")
        lines.append("")
        lines.append(f"- **Temperature:** {fmt(row.get('outdoor_temp_min'))}–{fmt(row.get('outdoor_temp_max'))}°F")
        lines.append("")

    # Stress
    lines.append("## Stress Hours")
    lines.append("")
    lines.append(f"- **Heat stress:** {fmt(row.get('stress_hours_heat'))}h (>85°F)")
    lines.append(f"- **Cold stress:** {fmt(row.get('stress_hours_cold'))}h (<50°F)")
    lines.append(f"- **VPD high:** {fmt(row.get('stress_hours_vpd_high'))}h (>2.0 kPa)")
    lines.append(f"- **VPD low:** {fmt(row.get('stress_hours_vpd_low'))}h (<0.4 kPa)")
    lines.append("")

    # Equipment
    lines.append("## Equipment Runtime")
    lines.append("")
    lines.append("| Equipment | Runtime | Cycles |")
    lines.append("|-----------|---------|--------|")
    lines.append(f"| Heater 1 (electric) | {heat1_h:.1f}h | {row.get('cycles_heat1') or 0} |")
    lines.append(f"| Heater 2 (gas) | {heat2_h:.1f}h | {row.get('cycles_heat2') or 0} |")
    lines.append(
        f"| Fans (combined) | {fan_h:.1f}h | {(row.get('cycles_fan1') or 0) + (row.get('cycles_fan2') or 0)} |"
    )
    lines.append(f"| Fog | {fog_h:.1f}h | {row.get('cycles_fog') or 0} |")
    lines.append(f"| Vent | {vent_h:.1f}h | {row.get('cycles_vent') or 0} |")
    lines.append(f"| Misters (all zones) | {mister_h:.2f}h | — |")
    lines.append(f"| Grow lights | {gl_h:.1f}h | {row.get('cycles_grow_light') or 0} |")
    lines.append(f"| **Total cycles** | — | **{total_cycles}** |")
    lines.append("")

    # Water
    lines.append("## Water")
    lines.append("")
    lines.append(f"- **Total:** {fmt(row.get('water_used_gal'), decimals=0)} gal")
    lines.append(f"- **Misters:** {fmt(row.get('mister_water_gal'), decimals=0)} gal")
    lines.append("")

    # Energy & Cost
    lines.append("## Energy & Cost")
    lines.append("")
    lines.append(
        f"- **Electricity:** {fmt(row.get('kwh_estimated'))} kWh (${fmt(row.get('cost_electric'), decimals=2)})"
    )
    lines.append(
        f"- **Gas:** {fmt(row.get('therms_estimated'), decimals=3)} therms (${fmt(row.get('cost_gas'), decimals=2)})"
    )
    lines.append(f"- **Water:** ${fmt(row.get('cost_water'), decimals=2)}")
    lines.append(f"- **Total:** **${fmt(row.get('cost_total'), decimals=2)}**")
    lines.append("")

    # Notes
    if row.get("notes"):
        lines.append("## Notes")
        lines.append("")
        lines.append(row["notes"])
        lines.append("")

    return "\n".join(lines)


async def write_day(conn, target_date: date) -> bool:
    row = await conn.fetchrow(
        """
        SELECT ds.*,
               d.crop_dli_mol_m2_day AS dli_product_value,
               d.availability AS dli_availability,
               d.unavailable_reason AS dli_unavailable_reason,
               d.provenance AS dli_provenance,
               d.validity_revision AS dli_validity_revision,
               d.valid_from AS dli_valid_from,
               d.valid_to AS dli_valid_to
        FROM daily_summary ds
        LEFT JOIN v_dli_daily d
          ON d.date = ds.date
         AND d.greenhouse_id = COALESCE(ds.greenhouse_id, 'vallery')
        WHERE ds.date = $1
        """,
        target_date,
    )
    if not row or row.get("temp_avg") is None:
        log.warning("No data for %s — skipping", target_date)
        return False

    VAULT_DIR.mkdir(parents=True, exist_ok=True)
    filepath = VAULT_DIR / f"{target_date}.md"
    content = render_markdown(dict(row))
    filepath.write_text(content)
    log.info("Wrote %s (%d bytes)", filepath, len(content))
    return True


async def main():
    conn = await asyncpg.connect(get_db_url())

    try:
        if "--backfill" in sys.argv:
            # Find all dates with data but no vault file
            rows = await conn.fetch("SELECT date FROM daily_summary WHERE temp_avg IS NOT NULL ORDER BY date")
            written = 0
            for r in rows:
                filepath = VAULT_DIR / f"{r['date']}.md"
                if not filepath.exists():
                    if await write_day(conn, r["date"]):
                        written += 1
            log.info("Backfill: %d/%d files written", written, len(rows))

        elif "--date" in sys.argv:
            idx = sys.argv.index("--date")
            target = date.fromisoformat(sys.argv[idx + 1])
            await write_day(conn, target)

        else:
            yesterday = date.today() - timedelta(days=1)
            await write_day(conn, yesterday)

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
