#!/usr/bin/env python3
"""Refresh generated blocks on the greenhouse equipment page."""

from __future__ import annotations

import argparse
import asyncio
import os
import re
from html import escape
from pathlib import Path

import asyncpg

DEFAULT_TARGET = Path("/mnt/iris/verdify-vault/website/greenhouse/equipment.md")


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


def _auto_block(name: str, body: str) -> str:
    return (
        f'<div class="auto-render-marker" data-auto-render="start {name}"></div>\n'
        f"{body}\n"
        f'<div class="auto-render-marker" data-auto-render="end {name}"></div>'
    )


def _replace_auto_blocks(existing: str, blocks: dict[str, str]) -> tuple[str, list[str], list[str]]:
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


def _fmt_watts(value: float | None) -> str:
    return f"{value:.0f}W" if value is not None else "-"


def _fmt_cost(value: float | None) -> str:
    return f"USD {value:.3f}" if value is not None else "-"


def _class_attr(classes: list[str]) -> str:
    return f' class="{" ".join(classes)}"' if classes else ""


def _lab_table(
    headers: list[str],
    rows: list[tuple[object, ...]],
    *,
    numeric_cols: set[int] | None = None,
    nowrap_cols: set[int] | None = None,
) -> str:
    if not rows:
        return '<div class="metric-grid">\n  <div class="metric-card"><strong>No rows</strong><p>No rows found.</p></div>\n</div>'
    numeric_cols = numeric_cols or set()
    nowrap_cols = nowrap_cols or set()
    lines = ['<div class="lab-table-wrap">', '  <table class="lab-table">', "    <thead>", "      <tr>"]
    for idx, header in enumerate(headers):
        classes: list[str] = []
        if idx in numeric_cols:
            classes.append("is-num")
        if idx in nowrap_cols:
            classes.append("is-nowrap")
        lines.append(f"        <th{_class_attr(classes)}>{escape(str(header))}</th>")
    lines.extend(["      </tr>", "    </thead>", "    <tbody>"])
    for row in rows:
        lines.append("      <tr>")
        for idx, cell in enumerate(row):
            classes = []
            if idx in numeric_cols:
                classes.append("is-num")
            if idx in nowrap_cols:
                classes.append("is-nowrap")
            lines.append(f"        <td{_class_attr(classes)}>{escape(str(cell))}</td>")
        lines.append("      </tr>")
    lines.extend(["    </tbody>", "  </table>", "</div>"])
    return "\n".join(lines)


def _equipment_catalog(rows: list[asyncpg.Record]) -> str:
    if not rows:
        return '<div class="metric-grid">\n  <div class="metric-card"><strong>No equipment</strong><p>No active equipment rows found.</p></div>\n</div>'
    rendered_rows = []
    for row in rows:
        zone = row["zone_slug"] or "-"
        model = row["model"] or "-"
        rendered_rows.append(
            (
                row["slug"],
                row["kind"],
                row["name"],
                model,
                zone,
                _fmt_watts(row["watts"]),
                _fmt_cost(row["cost_per_hour_usd"]),
            )
        )
    return _lab_table(
        ["Slug", "Kind", "Name", "Model", "Zone", "Watts", "Cost/hr"],
        rendered_rows,
        nowrap_cols={0, 1, 4, 5, 6},
    )


def _relay_map(rows: list[asyncpg.Record]) -> str:
    if not rows:
        return '<div class="metric-grid">\n  <div class="metric-card"><strong>No relay map</strong><p>No switch rows found.</p></div>\n</div>'
    rendered_rows = []
    for row in rows:
        board = row["board"] or "-"
        pin = row["pin"] if row["pin"] is not None else "-"
        equipment = row["equipment_slug"] or "unused"
        name = row["equipment_name"] or "unused"
        zone = row["zone_slug"] or "-"
        purpose = row["purpose"] or "-"
        state = "active" if row["is_active"] else "inactive"
        rendered_rows.append(
            (
                board,
                pin,
                equipment,
                name,
                state,
                zone,
                purpose,
            )
        )
    return _lab_table(
        ["Board", "Pin", "Equipment", "Name", "State", "Zone", "Purpose"],
        rendered_rows,
        numeric_cols={1},
        nowrap_cols={0, 1, 2, 4, 5},
    )


async def main_async(args: argparse.Namespace) -> int:
    target = Path(args.target)
    existing = target.read_text()
    conn = await asyncpg.connect(_database_dsn())
    try:
        equipment = await conn.fetch(
            """
            SELECT e.slug, e.kind, e.name, e.model, z.slug AS zone_slug, e.watts, e.cost_per_hour_usd
            FROM equipment e
            LEFT JOIN zones z ON z.id = e.zone_id
            WHERE e.greenhouse_id = 'vallery' AND e.is_active
            ORDER BY e.kind, e.slug
            """
        )
        relays = await conn.fetch(
            """
            SELECT board, pin, equipment_slug, equipment_name, zone_slug, purpose, is_active
            FROM v_equipment_relay_map
            WHERE greenhouse_id = 'vallery'
            ORDER BY board, pin
            """
        )
    finally:
        await conn.close()

    updated, replaced, missing = _replace_auto_blocks(
        existing,
        {
            "equipment-catalog": _equipment_catalog(equipment),
            "relay-map": _relay_map(relays),
        },
    )
    if missing:
        raise SystemExit(f"Missing equipment auto-render block(s): {', '.join(missing)}")
    if updated == existing:
        print(f"UNCHANGED {target} ({len(replaced)} block(s))")
        return 0
    if args.dry_run:
        print(f"WOULD WRITE {target} ({len(replaced)} block(s))")
        return 0
    target.write_text(updated)
    print(f"WROTE {target} ({len(replaced)} block(s))")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", default=str(DEFAULT_TARGET))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
