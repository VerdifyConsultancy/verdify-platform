#!/usr/bin/env python3
"""Render zone pages from v_zone_full + v_position_current.

Generates `/mnt/iris/verdify-vault/website/greenhouse/zones/{slug}.md` for
every active zone, pulling sensors, equipment, water systems, and current
plantings from the Sprint 22/23 topology tables + history views.

Idempotent: compares rendered content against existing file, only writes
when the content (below the frontmatter) changes, so Quartz's 10s poll
doesn't rebuild on every run.

Usage:
    python scripts/render-zone-pages.py [--dry-run] [--zone SLUG] [--out DIR]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from verdify_public.output_policy import (  # noqa: E402
    PUBLIC_CROP_EXCLUDE_SLUGS,
    PUBLIC_CROP_SQL_NAME_PATTERN,
    is_public_crop_record,
    public_crop_zone_joins,
    public_crop_zone_predicate,
)
from verdify_schemas.vault import VaultFrontmatter  # noqa: E402

DEFAULT_OUT = Path("/mnt/iris/verdify-vault/website/greenhouse/zones")


def _database_dsn() -> str:
    if dsn := os.environ.get("VERDIFY_DSN"):
        return dsn
    password = os.environ.get("POSTGRES_PASSWORD")
    if not password:
        raise RuntimeError("VERDIFY_DSN or POSTGRES_PASSWORD is required")
    return f"postgresql://verdify:{password}@127.0.0.1:5432/verdify"


AUTO_BLOCK_RE = re.compile(
    r"(?P<start>(?:\[//\]: # \(auto-render:start (?P<markdown_name>[-a-z0-9_]+)\)|"
    r"<!-- auto-render:start (?P<html_name>[-a-z0-9_]+) -->|"
    r'<span data-auto-render="start (?P<span_name>[-a-z0-9_]+)"></span>|'
    r'<div class="auto-render-marker" data-auto-render="start (?P<div_name>[-a-z0-9_]+)"></div>)\n)'
    r".*?"
    r"(?P<end>\n(?:\[//\]: # \(auto-render:end [-a-z0-9_]+\)|"
    r"<!-- auto-render:end [-a-z0-9_]+ -->|"
    r'<span data-auto-render="end [-a-z0-9_]+"></span>|'
    r'<div class="auto-render-marker" data-auto-render="end [-a-z0-9_]+"></div>))',
    re.DOTALL,
)
STALE_SEEDLING_AFTER_DAYS = 35


class ZoneFrontmatter(VaultFrontmatter):
    """Frontmatter for the rendered zone pages."""

    title: str
    date: str  # ISO date string
    type: str = "zone"
    zone: str
    zone_name: str | None = None
    sensor: str | None = None
    orientation: str | None = None
    water_systems: list[str]
    position_scheme: str | None = None
    peak_temp: str | None = None
    status: str


def _render_shelves_table(shelves: list[dict]) -> str:
    if not shelves:
        return _empty_card("No shelves", "No shelves defined for this zone.")
    lines = ['<div class="metric-grid">']
    for s in sorted(shelves, key=lambda x: (x.get("tier") or -1, x.get("slug") or "")):
        tier = s.get("tier") if s.get("tier") is not None else "—"
        lines.append(
            _metric_card(
                s.get("name") or s.get("slug"),
                f"Kind: {s['kind']}; Tier: {tier}; Position scheme: <code>{s.get('position_scheme') or '—'}</code>",
            )
        )
    lines.append("</div>")
    return "\n".join(lines)


def _render_equipment_table(equipment: list[dict]) -> str:
    if not equipment:
        return _empty_card("No equipment", "No equipment assigned to this zone.")
    lines = ['<div class="metric-grid">']
    for e in sorted(equipment, key=lambda x: (x["kind"], x["slug"])):
        watts = f"{e['watts']:.0f}W" if e.get("watts") else "—"
        cost = f"USD {e['cost_per_hour_usd']:.3f}" if e.get("cost_per_hour_usd") else "—"
        lines.append(
            _metric_card(
                f"<code>{e['slug']}</code> ({e['name']})",
                f"Kind: {e['kind']}; Model: {e.get('model') or '—'}; Watts: {watts}; Cost/hr: {cost}",
            )
        )
    lines.append("</div>")
    return "\n".join(lines)


def _render_sensors_table(sensors: list[dict]) -> str:
    if not sensors:
        return _empty_card("No sensors", "No sensors assigned to this zone.")
    lines = ['<div class="metric-grid">']
    for s in sorted(sensors, key=lambda x: x["slug"]):
        addr = "configured" if (s.get("modbus_addr") or s.get("gpio_pin")) else "—"
        model = "camera model withheld" if s.get("kind") == "camera" else s.get("model") or "—"
        protocol = s["protocol"]
        if s.get("kind") == "hydro_quality" and s.get("model") == "YINMIK":
            protocol = "WiFi via HA"
        lines.append(
            _metric_card(
                f"<code>{s['slug']}</code>",
                f"Kind: {s['kind']}; Protocol: {protocol}; Model: {model}; Addr: {addr}",
            )
        )
    lines.append("</div>")
    return "\n".join(lines)


def _render_water_systems_table(water_systems: list[dict], zone_name: str) -> str:
    if not water_systems:
        return _empty_card("No water systems", f"No water systems are assigned to {zone_name}.")
    lines = ['<div class="metric-grid">']
    for w in sorted(water_systems, key=lambda x: (x["kind"], x["slug"])):
        lines.append(
            _metric_card(
                f"<code>{w['slug']}</code> ({w['name']})",
                f"Kind: {w['kind']}; Heads: {w.get('head_count') or '—'}; Nozzles: {w.get('nozzle_count') or '—'}; Mount: {w.get('mount') or '—'}; Fert: {'yes' if w.get('is_fert_path') else 'no'}",
            )
        )
    lines.append("</div>")
    return "\n".join(lines)


def _render_current_crops_table(crops: list[dict], zone_name: str) -> str:
    if not crops:
        return _empty_card("No active crops", f"No active crops are recorded in {zone_name}.")
    lines = ['<div class="data-table">']
    for c in sorted(crops, key=lambda x: x["position_label"]):
        if not c.get("is_occupied"):
            continue
        crop_name = f"{c['crop_name']}{' (' + c['crop_variety'] + ')' if c.get('crop_variety') else ''}"
        stage_label = _planting_stage_label(c.get("crop_stage"), c.get("crop_days_in_place"))
        age_note = _planting_age_note(
            c.get("crop_planted_date"),
            c.get("crop_days_in_place"),
            c.get("crop_stage"),
        )
        lines.append(
            f'  <div class="data-row"><strong><code>{c["position_label"]}</code></strong>'
            f"<span>{crop_name} · {stage_label}</span>"
            f"<p>{age_note}</p></div>"
        )
    if len(lines) == 1:
        return _empty_card("No active crops", f"No occupied crop records are attached to {zone_name}.")
    lines.append("</div>")
    return "\n".join(lines)


def _metric_card(title: str | None, body: str) -> str:
    return f'  <div class="metric-card"><strong>{title or "—"}</strong><p>{body}</p></div>'


def _empty_card(title: str, body: str) -> str:
    return f'<div class="metric-grid">\n  <div class="metric-card"><strong>{title}</strong><p>{body}</p></div>\n</div>'


def _coerce_days(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_stale_seedling(stage: object, days: int | None) -> bool:
    if days is None:
        return False
    return str(stage or "").strip().lower() == "seedling" and days > STALE_SEEDLING_AFTER_DAYS


def _planting_stage_label(stage: object, days_value: object) -> str:
    stage_text = str(stage or "—")
    days = _coerce_days(days_value)
    if _is_stale_seedling(stage, days):
        return "stage review needed"
    return stage_text


def _planting_age_note(planted_date: object, days_value: object, stage: object) -> str:
    days = _coerce_days(days_value)
    days_text = f"{days} days in place" if days is not None else "age not recorded"
    note = f"Planted {planted_date or '—'}; {days_text}."
    if _is_stale_seedling(stage, days):
        note += " Stage review needed: the database stage value is stale for this planting age."
    return note


def _render_zone_profile_cards(
    d: dict,
    zone_slug: str,
    status: str,
    position_scheme: str | None,
    public_active_crop_count: int,
) -> str:
    cards = [
        (d["zone_name"], f"Slug <code>{zone_slug}</code>; status {status}."),
        (d.get("orientation") or "—", f"Sensor Modbus addr {d.get('sensor_modbus_addr') or '—'}."),
        ((str(d["peak_temp_f"]) + "°F") if d.get("peak_temp_f") else "—", "Recorded/known peak temperature."),
        (
            str(public_active_crop_count),
            f"Public active crop records. Position scheme: <code>{position_scheme or '—'}</code>.",
        ),
    ]
    lines = ['<div class="metric-grid">']
    for title, body in cards:
        lines.append(_metric_card(title, body))
    lines.append("</div>")
    return "\n".join(lines)


def _replace_auto_blocks(existing: str, blocks: dict[str, str]) -> tuple[str, list[str], list[str]]:
    """Replace generated blocks in an existing hybrid page."""
    replaced: list[str] = []

    def repl(match: re.Match[str]) -> str:
        name = (
            match.group("markdown_name")
            or match.group("html_name")
            or match.group("span_name")
            or match.group("div_name")
        )
        if name not in blocks:
            return match.group(0)
        replaced.append(name)
        return _auto_block(name, blocks[name])

    updated = AUTO_BLOCK_RE.sub(repl, existing)
    missing = [name for name in blocks if name not in replaced]
    return updated, replaced, missing


def _auto_block(name: str, body: str) -> str:
    return (
        f'<div class="auto-render-marker" data-auto-render="start {name}"></div>\n'
        f"{body}\n"
        f'<div class="auto-render-marker" data-auto-render="end {name}"></div>'
    )


def _yaml_dumps(fm: dict) -> str:
    """Emit YAML without importing pyyaml (keep the script dependency-light)."""
    import yaml

    return yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()


async def render_zone(conn: asyncpg.Connection, zone_slug: str) -> tuple[str, str, dict[str, str]] | None:
    """Return (filename, markdown_body) for the zone, or None if not found."""
    row = await conn.fetchrow(
        "SELECT * FROM v_zone_full WHERE greenhouse_id = 'vallery' AND zone_slug = $1",
        zone_slug,
    )
    if row is None:
        return None
    d = dict(row)
    # JSONB arrays arrive as str — parse them.
    for k in ("shelves", "sensors", "equipment", "water_systems"):
        if isinstance(d.get(k), str):
            d[k] = json.loads(d[k])

    # Current plantings. Query crops directly rather than v_position_current so
    # public active crops without a position_id are counted and rendered
    # consistently. Missing catalog identity is not publishable for an occupied
    # record; empty position rows are handled by the API and are not needed here.
    plantings = await conn.fetch(
        f"""
        SELECT c.position AS position_label,
               c.name AS crop_name,
               c.variety AS crop_variety,
               c.stage AS crop_stage,
               c.planted_date AS crop_planted_date,
               (now() AT TIME ZONE 'America/Denver')::date - c.planted_date AS crop_days_in_place,
               cc.slug AS crop_catalog_slug,
               true AS is_occupied
        FROM crops c
        LEFT JOIN crop_catalog cc ON cc.id = c.crop_catalog_id
        {public_crop_zone_joins()}
        WHERE {public_crop_zone_predicate("$1", "cc.slug", "c.name", 2, 3)}
          AND c.greenhouse_id = 'vallery'
          AND c.is_active
        ORDER BY c.position, c.name
        """,
        d["zone_id"],
        sorted(PUBLIC_CROP_EXCLUDE_SLUGS),
        PUBLIC_CROP_SQL_NAME_PATTERN,
    )
    plantings_list = [dict(r) for r in plantings]
    plantings_list = [
        p
        for p in plantings_list
        if is_public_crop_record(p.get("crop_catalog_slug"), p.get("crop_name"), occupied=True)
    ]

    status = d.get("zone_status") or "active"
    position_scheme = ", ".join(s.get("position_scheme") for s in d["shelves"] if s.get("position_scheme")) or None

    water_system_slugs = [w["slug"] for w in d["water_systems"]]

    fm = ZoneFrontmatter(
        title=f"{d['zone_name']}",
        date="2026-04-19",
        zone=zone_slug,
        zone_name=d["zone_name"],
        sensor=(f"Modbus addr {d['sensor_modbus_addr']}" if d.get("sensor_modbus_addr") else "Derived"),
        orientation=d.get("orientation"),
        water_systems=water_system_slugs,
        position_scheme=position_scheme,
        peak_temp=f"{d['peak_temp_f']}°F" if d.get("peak_temp_f") else None,
        status=status,
    )
    fm_yaml = _yaml_dumps(fm.model_dump(exclude_none=True))

    blocks = {
        "current-plantings": _render_current_crops_table(plantings_list, d["zone_name"]),
        "shelves": _render_shelves_table(d["shelves"]),
        "sensors": _render_sensors_table(d["sensors"]),
        "equipment": _render_equipment_table(d["equipment"]),
        "water-systems": _render_water_systems_table(d["water_systems"], d["zone_name"]),
        "zone-profile": _render_zone_profile_cards(d, zone_slug, status, position_scheme, len(plantings_list)),
    }

    body = f"""# {d["zone_name"]}

> Rendered from DB: zones + shelves + positions + sensors + equipment + water_systems.
> Source of truth: `v_zone_full` (migration 087) + `v_position_current` (migration 089).
> Do not edit by hand — run `scripts/render-zone-pages.py` to regenerate.

## Current Plantings

{_auto_block("current-plantings", blocks["current-plantings"])}

## Shelves

{_auto_block("shelves", blocks["shelves"])}

## Sensors

{_auto_block("sensors", blocks["sensors"])}

## Equipment

{_auto_block("equipment", blocks["equipment"])}

## Water Systems

{_auto_block("water-systems", blocks["water-systems"])}

## Zone Profile

{_auto_block("zone-profile", blocks["zone-profile"])}
"""

    rendered = f"---\n{fm_yaml}\n---\n\n{body}"
    return f"{zone_slug}.md", rendered, blocks


async def run(args: argparse.Namespace) -> int:
    conn = await asyncpg.connect(_database_dsn())
    try:
        if args.zone:
            zones = [args.zone]
        else:
            rows = await conn.fetch(
                "SELECT slug FROM zones WHERE greenhouse_id = 'vallery' AND status = 'active' ORDER BY slug"
            )
            zones = [r["slug"] for r in rows]

        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)

        changes = 0
        for slug in zones:
            result = await render_zone(conn, slug)
            if result is None:
                print(f"  SKIP {slug}: not found in v_zone_full")
                continue
            filename, content, blocks = result
            target = out_dir / filename
            existing = target.read_text() if target.exists() else ""
            mode = "full page"
            if existing and not args.replace_page and "auto-render" in existing:
                content, replaced, missing = _replace_auto_blocks(existing, blocks)
                mode = f"{len(replaced)} block(s)"
                if missing:
                    print(f"  WARN {filename}: missing auto-render block(s): {', '.join(missing)}")
            if existing == content:
                print(f"  UNCHANGED  {filename}")
                continue
            if args.dry_run:
                line_count = content.count("\n")
                print(f"  WOULD WRITE  {filename} ({mode}; {len(content)} chars, {line_count} lines)")
            else:
                target.write_text(content)
                print(f"  WROTE  {filename} ({mode})")
            changes += 1

        print(f"\n{'Would change' if args.dry_run else 'Changed'} {changes} zone page(s)")
    finally:
        await conn.close()
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--zone", help="Render only this zone slug (default: all active zones)")
    p.add_argument("--out", default=str(DEFAULT_OUT), help="Output directory")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--replace-page", action="store_true", help="Overwrite the whole page instead of updating auto-render blocks"
    )
    args = p.parse_args()
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
