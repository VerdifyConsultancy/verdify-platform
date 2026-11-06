#!/usr/bin/env python3
"""Render crop profile pages from crop_catalog + v_crop_catalog_with_profiles.

Outputs `/mnt/iris/verdify-vault/website/greenhouse/crops/{slug}.md` for
every catalog entry. For each crop:
  - Frontmatter with slug, category, season, cycle_days
  - Stage/season band aggregates from v_crop_catalog_with_profiles
  - Active plantings of this crop type (from the active crops records, #1329)
  - The crops hub (index.md): planted now and every profile's status
  - Historical plantings (from v_crop_history)

Idempotent — writes only when content changes.

Usage:
    python scripts/render-crop-profiles.py [--dry-run] [--slug CROP] [--out DIR]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from verdify_public.output_policy import is_public_crop, is_public_crop_record  # noqa: E402

DEFAULT_OUT = Path("/mnt/iris/verdify-vault/website/greenhouse/crops")
DEFAULT_VISION_OUT = Path("/mnt/iris/verdify-vault/website/static/vision")


def _database_dsn() -> str:
    if dsn := os.environ.get("VERDIFY_DSN"):
        return dsn
    password = os.environ.get("POSTGRES_PASSWORD")
    if not password:
        raise RuntimeError("VERDIFY_DSN or POSTGRES_PASSWORD is required")
    return f"postgresql://verdify:{password}@127.0.0.1:5432/verdify"


STALE_SEEDLING_AFTER_DAYS = 35
# Plantings come from the operator read model's records: crops.is_active plus the anchor zone and position that
# verdify_services.crops keeps in step with the open crop_occupancy row, so the lab lists what /v1/crops/locations
# shows (#1329). There is no hand-kept list of "active" crops.
ACTIVE_PLANTINGS_SQL = """
SELECT c.id AS planting_id,
       c.name AS crop_name,
       c.variety AS crop_variety,
       c.count AS crop_count,
       c.position AS position_label,
       COALESCE(z.slug, lower(btrim(c.zone))) AS zone_slug,
       COALESCE(z.name, initcap(btrim(c.zone))) AS zone_name,
       c.stage AS crop_stage,
       c.planted_date AS crop_planted_date,
       (now() AT TIME ZONE 'America/Denver')::date - c.planted_date AS crop_days_in_place,
       cc.slug AS crop_catalog_slug
FROM crops c
LEFT JOIN crop_catalog cc ON cc.id = c.crop_catalog_id
LEFT JOIN zones z ON z.id = c.zone_id
WHERE c.is_active
  AND c.greenhouse_id = 'vallery'
ORDER BY zone_name NULLS LAST, c.position NULLS LAST, c.name, c.id
"""
HUB_FILENAME = "index.md"
# The status block's heading on pages rendered before #1329.
RENAMED_STATUS_HEADING = ("\n## Crop-Control Status\n", "\n## Planting Status\n")
# Hand-written hub sections that the generated blocks replace the first time the hub is rendered.
HUB_PLANTED_SECTION = "## Current Planting"
HUB_PROFILE_SECTIONS = ("## Profile Status", "## Active Control", "## Observed/Reference", "## Planned/Retired")
CROP_LABELS = {
    "basil": "Basil",
    "canna": "Canna lilies",
    "cucumbers": "Cucumbers",
    "herbs": "Herbs",
    "lettuce": "Lettuce",
    "orchid": "Vanda orchids",
    "peppers": "Peppers",
    "strawberries": "Strawberries",
    "tomatoes": "Tomatoes",
}
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


def _yaml_dumps(fm: dict) -> str:
    import yaml

    return yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()


def _render_stage_band_table(profiles: list[dict], crop_label: str) -> str:
    if not profiles:
        return (
            '<div class="metric-grid">\n'
            '  <div class="metric-card"><strong>No target profile</strong>'
            f"<p>No hourly target profiles defined for {crop_label} yet.</p></div>\n"
            "</div>"
        )
    lines = ['<div class="metric-grid">']
    for p in sorted(profiles, key=lambda x: (x["growth_stage"], x["season"])):
        t_lo = p.get("temp_ideal_min_24h")
        t_hi = p.get("temp_ideal_max_24h")
        v_lo = p.get("vpd_ideal_min_24h")
        v_hi = p.get("vpd_ideal_max_24h")
        dli = p.get("dli_target_mol")
        temp_range = f"{float(t_lo):.1f}–{float(t_hi):.1f}°F" if t_lo and t_hi else "—"
        vpd_range = f"{float(v_lo):.2f}–{float(v_hi):.2f} kPa" if v_lo and v_hi else "—"
        lines.append(
            f'  <div class="metric-card"><strong>{p["growth_stage"]} / {p["season"]}</strong>'
            f"<p>{temp_range}; {vpd_range}; DLI {f'{float(dli):.1f}' if dli else '—'}. "
            f"Hours covered: {p['hours_covered']}.</p></div>"
        )
    lines.append("</div>")
    return "\n".join(lines)


def _place(planting: dict) -> str:
    zone = planting.get("zone_name") or "Unassigned zone"
    position = planting.get("position_label")
    return f"{zone} · {position}" if position else zone


def _planting_title(planting: dict) -> str:
    variety = planting.get("crop_variety")
    return f"{planting['crop_name']} ({variety})" if variety else str(planting["crop_name"])


def _planting_detail(planting: dict) -> str:
    stage_label = _planting_stage_label(planting.get("crop_stage"), planting.get("crop_days_in_place"))
    count = planting.get("crop_count")
    counted = f"{count} plants · " if count else ""
    return f"{counted}{stage_label}"


def _render_current_plantings(current: list[dict], crop_label: str) -> str:
    if not current:
        return (
            '<div class="metric-grid">\n'
            '  <div class="metric-card"><strong>No active plantings</strong>'
            f"<p>No active {crop_label} plantings are recorded right now.</p></div>\n"
            "</div>"
        )
    lines = ['<div class="data-table">']
    for c in current:
        age_note = _planting_age_note(
            c.get("crop_planted_date"),
            c.get("crop_days_in_place"),
            c.get("crop_stage"),
        )
        lines.append(
            f'  <div class="data-row"><strong>{_place(c)}</strong>'
            f"<span>{_planting_title(c)} · {_planting_detail(c)}</span>"
            f"<p>{age_note}</p></div>"
        )
    lines.append("</div>")
    return "\n".join(lines)


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


def _render_history(history: list[dict], crop_label: str) -> str:
    if not history:
        return (
            '<div class="metric-grid">\n'
            '  <div class="metric-card"><strong>No planting history</strong>'
            f"<p>No historical {crop_label} plantings are recorded yet.</p></div>\n"
            "</div>"
        )
    lines = ['<div class="data-table">']
    for h in history:
        cleared = h.get("cleared_at")
        cleared_str = str(cleared)[:10] if cleared else "—"
        lines.append(
            f'  <div class="data-row"><strong><code>{h.get("position_label") or "—"}</code></strong>'
            f"<span>{h['planted_date']} to {cleared_str}</span>"
            f"<p>{h.get('days_in_place') or '—'} days; final stage {h.get('final_stage') or '—'}; "
            f"events {h['event_count']}.</p></div>"
        )
    lines.append("</div>")
    return "\n".join(lines)


def _render_catalog_cards(d: dict) -> str:
    cycle = f"{d['cycle_days_min']}-{d['cycle_days_max']}" if d.get("cycle_days_min") else "—"
    default_vpd = (
        f"{d['default_target_vpd_low']:.2f}-{d['default_target_vpd_high']:.2f} kPa"
        if d.get("default_target_vpd_low")
        else "—"
    )
    cards = [
        (
            d["common_name"],
            f"Slug <code>{d['slug']}</code>; category {d['category']}.",
        ),
        (
            d["season"],
            f"Cycle {cycle}; scientific name {d.get('scientific_name') or '—'}.",
        ),
        (
            str(d.get("default_target_dli") or "—"),
            f"Default DLI; default VPD {default_vpd}.",
        ),
    ]
    lines = ['<div class="metric-grid">']
    for title, body in cards:
        lines.append(f'  <div class="metric-card"><strong>{title}</strong><p>{body}</p></div>')
    lines.append("</div>")
    return "\n".join(lines)


def _render_taxonomy_status(slug: str, current: list[dict]) -> str:
    """The crop page's planting status: planted now (where) or not, from the active crop records."""
    del slug
    if current:
        places = "; ".join(f"{_planting_title(c)} at {_place(c)}" for c in current)
        noun = "planting" if len(current) == 1 else "plantings"
        status = f'  <div class="metric-card"><strong>Planted now</strong><p>{len(current)} active {noun}: {places}.</p></div>'
    else:
        status = (
            '  <div class="metric-card"><strong>Not planted now</strong>'
            "<p>No active planting of this crop; the profile is kept for planning and history.</p></div>"
        )
    source = (
        '  <div class="metric-card"><strong>Source</strong><p>The operator crop records (active plantings and their '
        "zone and position), the same records the dashboard's crop locations show.</p></div>"
    )
    return "\n".join(['<div class="metric-grid">', status, source, "</div>"])


def _replace_auto_blocks(existing: str, blocks: dict[str, str]) -> tuple[str, list[str], list[str]]:
    """Replace generated blocks in an existing hybrid page.

    Returns updated text, replaced block names, and generated block names that
    were not present in the target page.
    """
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


def _insert_missing_blocks(existing: str, missing: list[str], blocks: dict[str, str]) -> str:
    updated = existing
    if "taxonomy-status" in missing:
        block = "\n\n## Planting Status\n\n" + _auto_block("taxonomy-status", blocks["taxonomy-status"])
        anchors = [
            "[//]: # (auto-render:end catalog-entry)",
            "<!-- auto-render:end catalog-entry -->",
            '<span data-auto-render="end catalog-entry"></span>',
            '<div class="auto-render-marker" data-auto-render="end catalog-entry"></div>',
        ]
        for anchor in anchors:
            if anchor in updated:
                updated = updated.replace(anchor, anchor + block, 1)
                break
        else:
            updated = updated.rstrip() + block + "\n"
    if "latest-vision" not in missing:
        return updated
    block = "\n\n## Latest Vision\n\n" + _auto_block("latest-vision", blocks["latest-vision"])
    anchors = [
        "[//]: # (auto-render:end current-plantings)",
        "<!-- auto-render:end current-plantings -->",
        '<span data-auto-render="end current-plantings"></span>',
        '<div class="auto-render-marker" data-auto-render="end current-plantings"></div>',
    ]
    for anchor in anchors:
        if anchor in updated:
            return updated.replace(anchor, anchor + block, 1)
    return updated.rstrip() + block + "\n"


async def _active_plantings(conn: asyncpg.Connection) -> list[dict]:
    rows = await conn.fetch(ACTIVE_PLANTINGS_SQL)
    return [dict(r) for r in rows if is_public_crop_record(r["crop_catalog_slug"], r["crop_name"], occupied=True)]


async def _check_crop_consistency(conn: asyncpg.Connection) -> int:
    """Every active public planting needs a catalog crop, so its profile page and the hub can list it."""
    plantings = await _active_plantings(conn)
    catalog = {r["slug"] for r in await conn.fetch("SELECT slug FROM crop_catalog")}
    orphans = [p for p in plantings if p["crop_catalog_slug"] not in catalog]
    for p in orphans:
        print(f"ERROR active planting {p['planting_id']} has no crop_catalog entry")
    print(f"Active plantings: {len(plantings)} ({', '.join(_planting_title(p) for p in plantings) or 'none'})")
    return 1 if orphans else 0


def _profile_href(slug: str) -> str:
    return f"/greenhouse/crops/{slug.replace('_', '-')}/"


def _render_hub_planted_now(plantings: list[dict], catalog: dict[str, dict]) -> str:
    if not plantings:
        return (
            '<div class="metric-grid">\n'
            '  <div class="metric-card"><strong>Nothing planted</strong>'
            "<p>No active plantings are recorded right now.</p></div>\n"
            "</div>"
        )
    noun = "planting" if len(plantings) == 1 else "plantings"
    lines = [
        f"<p><strong>{len(plantings)}</strong> active {noun}, from the operator crop records "
        "(the same records the dashboard's crop locations show).</p>",
        '<div class="data-table">',
    ]
    for p in plantings:
        slug = p.get("crop_catalog_slug")
        title = _planting_title(p)
        name = f'<a href="{_profile_href(slug)}">{title}</a>' if slug in catalog else title
        age_note = _planting_age_note(p.get("crop_planted_date"), p.get("crop_days_in_place"), p.get("crop_stage"))
        lines.append(
            f'  <div class="data-row"><strong>{name}</strong><span>{_place(p)}</span>'
            f"<p>{_planting_detail(p)}. {age_note}</p></div>"
        )
    lines.append("</div>")
    return "\n".join(lines)


def _render_hub_profiles(plantings: list[dict], catalog: dict[str, dict]) -> str:
    by_slug: dict[str, list[dict]] = {}
    for p in plantings:
        by_slug.setdefault(p.get("crop_catalog_slug"), []).append(p)
    rows = sorted(catalog.values(), key=lambda c: (c["slug"] not in by_slug, str(c["common_name"]).lower()))
    planted = sum(1 for c in rows if c["slug"] in by_slug)
    lines = [
        f"<p>{len(rows)} crop profiles; <strong>{planted}</strong> planted now. A profile with no active planting is "
        "kept for planning and history.</p>",
        '<div class="data-table">',
    ]
    for c in rows:
        here = by_slug.get(c["slug"], [])
        status = "Planted now: " + "; ".join(_place(p) for p in here) if here else "Not planted now"
        kind = " · ".join(str(v) for v in (c.get("category"), c.get("season")) if v) or "—"
        lines.append(
            f'  <div class="data-row"><strong><a href="{_profile_href(c["slug"])}">{c["common_name"]}</a></strong>'
            f"<span>{status}</span><p>{kind}</p></div>"
        )
    lines.append("</div>")
    return "\n".join(lines)


def _sections(text: str) -> list[tuple[str, str]]:
    """Split markdown into (heading, text) pieces at each "## " heading; the first piece has heading ""."""
    pieces: list[tuple[str, str]] = []
    heading, buf = "", []
    for line in text.splitlines(keepends=True):
        if line.startswith("## "):
            pieces.append((heading, "".join(buf)))
            heading, buf = line.rstrip("\n"), [line]
        else:
            buf.append(line)
    pieces.append((heading, "".join(buf)))
    return pieces


def _seed_hub(existing: str, blocks: dict[str, str]) -> str:
    """Convert the hand-written hub once: the planting bullets and the status tables become generated blocks."""
    out: list[str] = []
    profiles_done = False
    for heading, text in _sections(existing):
        if heading == HUB_PLANTED_SECTION and "planted-now" in blocks:
            out.append(f"{heading}\n\n{_auto_block('planted-now', blocks['planted-now'])}\n\n")
        elif heading in HUB_PROFILE_SECTIONS:
            if not profiles_done:
                out.append(f"## Crop Profiles\n\n{_auto_block('crop-profiles', blocks['crop-profiles'])}\n\n")
                profiles_done = True
        else:
            out.append(text)
    seeded = "".join(out).replace(
        "Target Bands and Crop-Control Status blocks", "Target Bands and Planting Status blocks"
    )
    for name, heading in (("planted-now", HUB_PLANTED_SECTION), ("crop-profiles", "## Crop Profiles")):
        if f'data-auto-render="start {name}"' not in seeded:
            seeded = seeded.rstrip() + f"\n\n{heading}\n\n{_auto_block(name, blocks[name])}\n"
    return seeded


def render_hub(existing: str, plantings: list[dict], catalog: dict[str, dict]) -> str:
    blocks = {
        "planted-now": _render_hub_planted_now(plantings, catalog),
        "crop-profiles": _render_hub_profiles(plantings, catalog),
    }
    if not existing:
        existing = (
            '---\ntitle: "Crops and Growing System"\ntags: [crops, reference, greenhouse]\n---\n\n'
            "# Crops and Growing\n\n"
        )
    content, _replaced, missing = _replace_auto_blocks(existing, blocks)
    if missing:
        content = _seed_hub(content, blocks)
    return content


def _render_latest_vision(rows: list[dict], public_refs: dict[int, str], crop_label: str) -> str:
    if not rows:
        return (
            '<div class="metric-grid">\n'
            '  <div class="metric-card"><strong>No vision observations</strong>'
            f"<p>No camera observations have been linked to {crop_label} yet.</p></div>\n"
            "</div>"
        )
    lines = ['<div class="data-table vision-gallery">']
    for row in rows:
        image_ref = public_refs.get(row["id"])
        score = row.get("health_score") or "—"
        notes = row.get("notes") or "No notes recorded."
        image = (
            f'<img src="{image_ref}" alt="Latest {row["crop_name"]} camera observation from {row["camera"]}"/>'
            if image_ref
            else '<span class="media-unavailable" role="img">Historical image unavailable. '
            "Observation metadata retained; no substitute image was generated.</span>"
        )
        lines.append(
            f'  <div class="data-row">{image}'
            f"<strong>{str(row['ts'])[:16]}</strong><span>{row['camera']} · {row['zone']} · health {score}/10</span>"
            f"<p><strong>{crop_label}:</strong> {notes}</p></div>"
        )
    lines.append("</div>")
    return "\n".join(lines)


def _vision_publication_assets(
    slug: str,
    rows: list[dict],
    vision_out: Path | None = None,
) -> tuple[dict[int, str], list[tuple[Path, Path]]]:
    """Select only original or retained same-ID public observation images."""

    output = vision_out or DEFAULT_VISION_OUT
    public_refs: dict[int, str] = {}
    assets: list[tuple[Path, Path]] = []
    for row in rows:
        src = Path(row["image_path"])
        dest = output / f"{slug}-{row['id']}{src.suffix or '.jpg'}"
        if not src.is_file() and not dest.is_file():
            continue
        public_refs[row["id"]] = f"/static/vision/{dest.name}"
        assets.append((src, dest))
    return public_refs, assets


async def render_crop(
    conn: asyncpg.Connection, slug: str
) -> tuple[str, str, dict[str, str], list[tuple[Path, Path]]] | None:
    entry = await conn.fetchrow(
        "SELECT * FROM v_crop_catalog_with_profiles WHERE slug = $1",
        slug,
    )
    if entry is None:
        return None
    d = dict(entry)
    if not is_public_crop_record(slug, d.get("common_name"), occupied=True):
        return None
    # JSONB
    sp = d.get("stage_season_profiles")
    profiles = json.loads(sp) if isinstance(sp, str) else (sp or [])

    current = [p for p in await _active_plantings(conn) if p["crop_catalog_slug"] == slug]
    history_rows = await conn.fetch(
        """
        SELECT h.* FROM v_crop_history h
        WHERE h.crop_catalog_slug = $1
        ORDER BY h.planted_date DESC
        LIMIT 50
        """,
        slug,
    )
    crop_names = {slug.lower(), slug.lower().rstrip("s"), str(d["common_name"]).lower()}
    crop_names.add(str(d["common_name"]).lower().rstrip("s"))
    vision_rows = await conn.fetch(
        """
        SELECT io.id, io.ts, io.camera, io.zone, io.image_path,
               crop->>'crop' AS crop_name,
               crop->>'notes' AS notes,
               crop->>'health_score' AS health_score
        FROM image_observations io
        CROSS JOIN LATERAL jsonb_array_elements(io.crops_observed) AS crop
        WHERE lower(crop->>'crop') = ANY($1::text[])
        ORDER BY io.ts DESC
        LIMIT 3
        """,
        sorted(crop_names),
    )
    vision_dicts = [dict(row) for row in vision_rows]
    public_refs, vision_assets = _vision_publication_assets(slug, vision_dicts)

    fm = {
        "title": d["common_name"],
        "date": "2026-04-19",
        "type": "crop-profile",
        "crop": slug,
        "category": d["category"],
        "season": d["season"],
    }
    if d.get("scientific_name"):
        fm["scientific_name"] = d["scientific_name"]
    if d.get("cycle_days_min") and d.get("cycle_days_max"):
        fm["cycle_days"] = f"{d['cycle_days_min']}-{d['cycle_days_max']}"

    fm_yaml = _yaml_dumps(fm)

    blocks = {
        "catalog-entry": _render_catalog_cards(d),
        "taxonomy-status": _render_taxonomy_status(slug, current),
        "target-bands": _render_stage_band_table(profiles, CROP_LABELS.get(slug, d["common_name"])),
        "current-plantings": _render_current_plantings(current, CROP_LABELS.get(slug, d["common_name"])),
        "latest-vision": _render_latest_vision(
            vision_dicts,
            public_refs,
            CROP_LABELS.get(slug, d["common_name"]),
        ),
        "planting-history": _render_history([dict(r) for r in history_rows], CROP_LABELS.get(slug, d["common_name"])),
    }

    body = f"""# {d["common_name"]}

> Rendered from DB: `crop_catalog` + `crop_target_profiles` + the active `crops` records + `v_crop_history`.
> Do not edit by hand — run `scripts/render-crop-profiles.py` to regenerate.

## Catalog Entry

{_auto_block("catalog-entry", blocks["catalog-entry"])}

## Planting Status

{_auto_block("taxonomy-status", blocks["taxonomy-status"])}

## Stage × Season Target Bands (24h averages)

{_auto_block("target-bands", blocks["target-bands"])}

## Current Plantings

{_auto_block("current-plantings", blocks["current-plantings"])}

## Latest Vision

{_auto_block("latest-vision", blocks["latest-vision"])}

## Planting History

{_auto_block("planting-history", blocks["planting-history"])}
"""

    return f"{slug.replace('_', '-')}.md", f"---\n{fm_yaml}\n---\n\n{body}", blocks, vision_assets


async def run(args: argparse.Namespace) -> int:
    conn = await asyncpg.connect(_database_dsn())
    try:
        if args.check_consistency:
            return await _check_crop_consistency(conn)
        if args.slug:
            slugs = [args.slug]
        else:
            rows = await conn.fetch("SELECT slug FROM crop_catalog ORDER BY slug")
            slugs = [r["slug"] for r in rows]
        # Never publish redacted crops (#308). DB rows are kept for control; the
        # public site simply omits the page (and thus the URL + search index entry).
        excluded = [s for s in slugs if not is_public_crop(s)]
        if excluded:
            print(f"  REDACT: {len(excluded)} non-public crop profile(s) omitted")
        slugs = [s for s in slugs if is_public_crop(s)]

        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)

        # Actively DELETE any stale page for a redacted slug (#308) — skipping the
        # write alone would leave a previously-published page on the site, which
        # Quartz then keeps in the folder index, search index, and sitemap.
        if not args.dry_run:
            for slug in excluded:
                stale = out_dir / f"{slug.replace('_', '-')}.md"
                if stale.exists():
                    stale.unlink()
                    print("  DELETED one stale non-public crop profile")

        changes = 0
        expected_vision_assets: set[Path] = set()
        for slug in slugs:
            result = await render_crop(conn, slug)
            if result is None:
                print(f"  SKIP {slug}: not in crop_catalog")
                continue
            filename, content, blocks, vision_assets = result
            expected_vision_assets.update(dest for _src, dest in vision_assets)
            if not args.dry_run:
                for src, dest in vision_assets:
                    if src.is_file():
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        if not dest.exists() or src.stat().st_mtime_ns != dest.stat().st_mtime_ns:
                            shutil.copy2(src, dest)
                    elif not dest.is_file():
                        raise RuntimeError(f"vision source and retained public asset disappeared: {dest.name}")
            target = out_dir / filename
            existing = target.read_text() if target.exists() else ""
            mode = "full page"
            if existing and not args.replace_page and "auto-render" in existing:
                content, replaced, missing = _replace_auto_blocks(existing.replace(*RENAMED_STATUS_HEADING), blocks)
                content = _insert_missing_blocks(content, missing, blocks)
                mode = f"{len(replaced)} block(s)"
                if missing:
                    print(f"  WARN {filename}: missing auto-render block(s): {', '.join(missing)}")
            if existing == content:
                print(f"  UNCHANGED  {filename}")
                continue
            if args.dry_run:
                print(f"  WOULD WRITE  {filename} ({mode})")
            else:
                target.write_text(content)
                print(f"  WROTE  {filename} ({mode})")
            changes += 1
        if not args.slug:
            catalog = {
                r["slug"]: dict(r)
                for r in await conn.fetch("SELECT slug, common_name, category, season FROM crop_catalog")
                if is_public_crop(r["slug"])
            }
            hub = out_dir / HUB_FILENAME
            existing = hub.read_text() if hub.exists() else ""
            content = render_hub(existing, await _active_plantings(conn), catalog)
            if content == existing:
                print(f"  UNCHANGED  {HUB_FILENAME}")
            elif args.dry_run:
                print(f"  WOULD WRITE  {HUB_FILENAME}")
                changes += 1
            else:
                hub.write_text(content)
                print(f"  WROTE  {HUB_FILENAME}")
                changes += 1
        if not args.slug and not args.dry_run:
            removed = 0
            for asset in DEFAULT_VISION_OUT.glob("*.jpg"):
                if asset not in expected_vision_assets:
                    asset.unlink()
                    removed += 1
            if removed:
                print(f"Pruned {removed} stale vision asset(s)")
        print(f"\n{'Would change' if args.dry_run else 'Changed'} {changes} crop page(s)")
    finally:
        await conn.close()
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--slug", help="Render only this crop slug")
    p.add_argument("--out", default=str(DEFAULT_OUT), help="Output directory")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--check-consistency", action="store_true", help="Check every active planting has a catalog crop")
    p.add_argument(
        "--replace-page", action="store_true", help="Overwrite the whole page instead of updating auto-render blocks"
    )
    args = p.parse_args()
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
