#!/usr/bin/env python3
"""Render crop profile pages from crop_catalog + v_crop_catalog_with_profiles.

Outputs `/mnt/iris/verdify-vault/website/greenhouse/crops/{slug}.md` for
every catalog entry. For each crop:
  - Frontmatter with slug, category, season, cycle_days
  - Stage/season band aggregates from v_crop_catalog_with_profiles
  - Active plantings of this crop type (from v_position_current)
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


ACTIVE_CONTROL_SLUGS = {"canna", "lettuce", "orchid", "peppers", "strawberries"}

STALE_SEEDLING_AFTER_DAYS = 35
CROP_TAXONOMY = {
    "basil": (
        "Planned/Retired",
        "No active planting in <code>v_position_current</code>; kept as a crop profile and prior planting record.",
    ),
    "canna": (
        "Active Control",
        "Occupied south-zone record in <code>v_position_current</code>; its wide VPD tolerance informs south-zone control.",
    ),
    "cucumbers": (
        "Planned/Retired",
        "No active planting in <code>v_position_current</code>; retained as a future west-zone crop candidate.",
    ),
    "herbs": (
        "Observed/Reference",
        "Mixed-herb reference profile; individual herb species are not active control records right now.",
    ),
    "lettuce": (
        "Active Control",
        "Occupied east-zone record in <code>v_position_current</code>; heat-sensitive crop context participates in active policy. Target Bands below show current DB-backed profile coverage.",
    ),
    "orchid": (
        "Active Control",
        "Occupied center hanging record in <code>v_position_current</code>; Vanda VPD tolerance participates in active crop and zone policy.",
    ),
    "peppers": (
        "Active Control",
        "Occupied east-zone record in <code>v_position_current</code>; warm-crop context participates in active policy. Target Bands below show current DB-backed profile coverage.",
    ),
    "strawberries": (
        "Active Control",
        "Occupied east-zone record in <code>v_position_current</code>; strawberry crop context participates in active policy. Target Bands below show current DB-backed profile coverage.",
    ),
    "tomatoes": (
        "Planned/Retired",
        "No active planting in <code>v_position_current</code>; retained as a south-zone future crop profile.",
    ),
}
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


def _render_current_plantings(current: list[dict], crop_label: str) -> str:
    if not current:
        return (
            '<div class="metric-grid">\n'
            '  <div class="metric-card"><strong>No active plantings</strong>'
            f"<p>No active {crop_label} plantings are recorded right now.</p></div>\n"
            "</div>"
        )
    lines = ['<div class="data-table">']
    for c in sorted(current, key=lambda x: x["position_label"] or ""):
        stage_label = _planting_stage_label(c.get("crop_stage"), c.get("crop_days_in_place"))
        age_note = _planting_age_note(
            c.get("crop_planted_date"),
            c.get("crop_days_in_place"),
            c.get("crop_stage"),
        )
        lines.append(
            f'  <div class="data-row"><strong><code>{c["position_label"]}</code></strong>'
            f"<span>{stage_label}</span>"
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
    status, note = CROP_TAXONOMY.get(
        slug,
        ("Observed/Reference", "No explicit crop-control status assigned; treated as reference until validated."),
    )
    occupied = [c for c in current if c.get("is_occupied", True)]
    positions = ", ".join(c.get("position_label") or "—" for c in occupied) or "none"
    control_source = (
        "Counts as active control."
        if slug in ACTIVE_CONTROL_SLUGS
        else "Does not count toward the active-control crop total."
    )
    lines = [
        '<div class="metric-grid">',
        (f'  <div class="metric-card"><strong>{status}</strong><p>{control_source} {note}</p></div>'),
        (
            '  <div class="metric-card"><strong>Source Check</strong>'
            f"<p>Current occupied positions from <code>v_position_current</code>: {positions}.</p></div>"
        ),
        "</div>",
    ]
    return "\n".join(lines)


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
        block = "\n\n## Crop-Control Status\n\n" + _auto_block("taxonomy-status", blocks["taxonomy-status"])
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


async def _check_crop_consistency(conn: asyncpg.Connection) -> int:
    rows = await conn.fetch(
        """
        SELECT zone_slug, crop_catalog_slug
        FROM v_position_current
        WHERE greenhouse_id = 'vallery'
          AND is_occupied
          AND crop_catalog_slug IS NOT NULL
        """
    )
    occupied_by_zone = {(r["zone_slug"], r["crop_catalog_slug"]) for r in rows}
    active_from_db = {slug for _, slug in occupied_by_zone}
    ok = True
    if active_from_db != ACTIVE_CONTROL_SLUGS:
        ok = False
        print(
            "ERROR active-control taxonomy mismatch: "
            f"taxonomy={sorted(ACTIVE_CONTROL_SLUGS)} db={sorted(active_from_db)}"
        )
    print(f"Active-control crop count: {len(ACTIVE_CONTROL_SLUGS)} ({', '.join(sorted(ACTIVE_CONTROL_SLUGS))})")
    return 0 if ok else 1


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

    current = await conn.fetch(
        """
        SELECT p.* FROM v_position_current p
        WHERE p.crop_catalog_slug = $1 AND p.is_occupied
        """,
        slug,
    )
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
        "taxonomy-status": _render_taxonomy_status(slug, [dict(r) for r in current]),
        "target-bands": _render_stage_band_table(profiles, CROP_LABELS.get(slug, d["common_name"])),
        "current-plantings": _render_current_plantings(
            [dict(r) for r in current], CROP_LABELS.get(slug, d["common_name"])
        ),
        "latest-vision": _render_latest_vision(
            vision_dicts,
            public_refs,
            CROP_LABELS.get(slug, d["common_name"]),
        ),
        "planting-history": _render_history([dict(r) for r in history_rows], CROP_LABELS.get(slug, d["common_name"])),
    }

    body = f"""# {d["common_name"]}

> Rendered from DB: `crop_catalog` + `crop_target_profiles` + `v_position_current` + `v_crop_history`.
> Do not edit by hand — run `scripts/render-crop-profiles.py` to regenerate.

## Catalog Entry

{_auto_block("catalog-entry", blocks["catalog-entry"])}

## Crop-Control Status

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
                content, replaced, missing = _replace_auto_blocks(existing, blocks)
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
    p.add_argument(
        "--check-consistency", action="store_true", help="Validate active crop-control status against DB views"
    )
    p.add_argument(
        "--replace-page", action="store_true", help="Overwrite the whole page instead of updating auto-render blocks"
    )
    args = p.parse_args()
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
