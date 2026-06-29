#!/usr/bin/env python3
"""Regenerate data/plans/index.md and the short plans/index.md alias."""

from __future__ import annotations

import os
import re
import subprocess
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

CONTENT_ROOT = Path("/srv/verdify/verdify-site/content")
INDEX_ALIAS = CONTENT_ROOT / "plans" / "index.md"
DATA_INDEX = CONTENT_ROOT / "data" / "plans" / "index.md"
DB_CMD = ["docker", "exec", "verdify-timescaledb", "psql", "-U", "verdify", "-d", "verdify", "-t", "-A", "-F", "|"]
PLAN_PAGE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.md$")
LOCAL_TZ = ZoneInfo(os.environ.get("LAB_LOCAL_TIMEZONE") or os.environ.get("TZ") or "America/Denver")


def local_today() -> date:
    return datetime.now(LOCAL_TZ).date()


def public_text(value: object) -> str:
    text = str(value or "")
    text = re.sub(r"iris-hermes-validation", "iris-validation", text, flags=re.IGNORECASE)
    text = re.sub(r"\bHermes\b", "planning gateway", text)
    text = re.sub(r"\bhermes\b", "planner-gateway", text)
    text = re.sub(r"\bOpenClaw/Iris\b", "planner", text)
    text = re.sub(r"\bIris\b(?!-)", "AI planning agent", text)
    text = re.sub(r"\bOpenClaw\b", "planner gateway", text)
    text = re.sub(r"\blocal Gemma context overflow\b", "planner context overflow", text, flags=re.IGNORECASE)
    text = re.sub(r"\blocal Gemma overflow\b", "planner context overflow", text, flags=re.IGNORECASE)
    text = re.sub(r"\blocal Gemma\b", "planner", text, flags=re.IGNORECASE)
    return re.sub(r"\$(\d)", r"USD \1", text)


def db_rows(sql: str) -> list[list[str]]:
    result = subprocess.run([*DB_CMD, "-c", sql], capture_output=True, text=True, timeout=15, check=True)
    return [row.split("|") for row in result.stdout.strip().splitlines() if row.strip()]


def merge_plan_page_dates(rows: list[list[str]]) -> list[list[str]]:
    """Keep in-progress generated plan pages visible before daily_summary lands."""
    by_date = {row[0].strip(): row for row in rows if row}
    plans_dir = CONTENT_ROOT / "plans"
    if plans_dir.exists():
        for page in plans_dir.glob("*.md"):
            if not PLAN_PAGE_RE.match(page.name):
                continue
            plan_date = page.stem
            by_date.setdefault(plan_date, [plan_date, "0", "-", "0.0", "-", "In progress", "-"])
    return [by_date[plan_date] for plan_date in sorted(by_date, reverse=True)]


def default_header(today: date) -> str:
    return f"""---
title: AI Greenhouse Planning Archive
description: "Daily archive and monthly summary of Verdify's AI greenhouse planning agent: experiments, scorecards, climate stress, costs, and lessons from each planning cycle."
tags: [plans, greenhouse, ai]
date: {today}
cssclasses:
  - hide-folder-listing
---

# AI Greenhouse Planning Archive

The AI planning agent normally runs up to three planning cycles per day. Missed cycles are intentionally visible in this archive, because planner availability is part of the system being audited.

The individual daily pages are generated records, not polished articles. The important story is what the AI planned, what the greenhouse actually experienced, how much stress remained, and what the next plan learned.

To understand the exact parameters behind the plan rows, see [AI Tunables Traceability](/reference/ai-tunables/).

*Generated planning archive: daily pages are lab notebook records. Pending rows and missed cycles stay visible because planner availability is part of the evidence.*

## What the Archive Shows

<div class="metric-grid">
  <div class="metric-card"><strong>Plan frequency</strong><span>Up to 3 cycles/day</span><p>Morning, midday, and evening plans adjust temperature, VPD, misting, fog, ventilation, heat, and lighting tactics.</p></div>
  <div class="metric-card"><strong>Scorecard feedback</strong><span>Outcome-driven</span><p>Daily summaries compare stress hours, compliance, resource use, and experimental outcomes.</p></div>
  <div class="metric-card"><strong>Learning loop</strong><span>Validated lessons</span><p>Useful findings graduate into generated lessons that the planner reads before future cycles.</p></div>
</div>

---"""


def existing_header(today: date) -> str:
    if not DATA_INDEX.exists():
        return default_header(today)
    text = DATA_INDEX.read_text(encoding="utf-8")
    header = re.split(r"^## (?:Recent Plans|All Plans)$", text, maxsplit=1, flags=re.MULTILINE)[0].strip()
    if not header:
        return default_header(today)
    header = re.sub(r"^date: .*$", f"date: {today}", header, flags=re.MULTILINE)
    header = re.sub(
        r"Daily archive and monthly summary of [Ii]ris, Verdify's AI greenhouse planner",
        "Daily archive and monthly summary of Verdify's AI greenhouse planning agent",
        header,
    )
    header = re.sub(
        r"[Ii]ris normally runs up to three planning cycles per day\.",
        "The AI planning agent normally runs up to three planning cycles per day.",
        header,
    )
    header = header.replace("/intelligence/planning/#ai-writable-tunables", "/reference/ai-tunables/")
    header = header.replace("/reference/ai-tunables/", "/reference/ai-tunables/")
    header = header.replace("AI-Writable Tunables", "AI Tunables Traceability")
    header = header.replace("AI Tunables](/reference/ai-tunables/", "AI Tunables Traceability](/reference/ai-tunables/")
    if "cssclasses:" not in header:
        header = re.sub(
            r"^(date: .*)$",
            "\\1\ncssclasses:\n  - hide-folder-listing",
            header,
            count=1,
            flags=re.MULTILINE,
        )
    elif "hide-folder-listing" not in header:
        header = re.sub(
            r"^(cssclasses:\s*)$",
            "\\1\n  - hide-folder-listing",
            header,
            count=1,
            flags=re.MULTILINE,
        )
    if "Generated planning archive" not in header:
        header = header.replace(
            "## What the Archive Shows",
            "*Generated planning archive: daily pages are lab notebook records. Pending rows and missed cycles stay visible because planner availability is part of the evidence.*\n\n## What the Archive Shows",
        )
    return header.rstrip()


def render(rows: list[list[str]], today: date) -> str:
    lines = [
        existing_header(today),
        "",
        "## All Plans",
        "",
        "| Date | Plans | Temp Range | VPD Stress | Cost | Experiment | Score |",
        "|------|-------|------------|------------|------|------------|-------|",
    ]
    for row in rows:
        if len(row) < 7:
            continue
        d, plans, temp_range, vpd, cost, experiment, score = [item.strip() for item in row[:7]]
        experiment_public = public_text(experiment)
        temp_display = f"{temp_range}°F" if temp_range and temp_range != "-" else "-"
        cost_display = f"${cost}" if cost and cost != "-" else "-"
        lines.append(
            f"| [{d}](/plans/{d}) | {plans} | {temp_display} | {vpd}h | {cost_display} | "
            f"{experiment_public[:40]} | {score} |"
        )
    lines.extend(
        [
            "",
            "---",
            "",
            "*Auto-generated from daily_summary + plan_journal data. Archive rows are generated lab notebook entries; null or pending fields mean the day is still in progress or the source row was not recorded.*",
        ]
    )
    return "\n".join(lines) + "\n"


def render_alias(today: date) -> str:
    return f"""---
title: AI Greenhouse Planning Archive
description: "Short route alias for the generated planning archive."
tags: [plans, greenhouse, ai, auto-generated]
date: {today}
noindex: true
cssclasses:
  - hide-folder-listing
---

[//]: # (auto-generated by scripts/generate-plans-index.py; canonical index: /data/plans/)

# AI Greenhouse Planning Archive

The generated planning archive index lives at [Data / Plans](/data/plans/).

Daily plan records still use `/plans/YYYY-MM-DD` URLs. I keep this index route as a short compatibility page so `/plans/` does not fall through to a folder listing.
"""


def main() -> None:
    rows = db_rows(
        """
        SELECT ds.date,
          (SELECT COUNT(*) FROM plan_journal pj
           WHERE pj.plan_id LIKE 'iris-' || to_char(ds.date, 'YYYYMMDD') || '%') AS plans,
          ROUND(COALESCE(ds.temp_min,0)::numeric,0) || '-' || ROUND(COALESCE(ds.temp_max,0)::numeric,0) AS temp_range,
          ROUND(COALESCE(ds.stress_hours_vpd_high,0)::numeric,1) AS vpd_stress,
          ROUND(COALESCE(ds.cost_total,0)::numeric,2) AS cost,
          COALESCE((SELECT LEFT(pj.experiment, 50) FROM plan_journal pj
            WHERE pj.plan_id LIKE 'iris-' || to_char(ds.date, 'YYYYMMDD') || '%'
            ORDER BY pj.created_at DESC LIMIT 1), '-') AS experiment,
          COALESCE((SELECT pj.outcome_score::text FROM plan_journal pj
            WHERE pj.plan_id LIKE 'iris-' || to_char(ds.date, 'YYYYMMDD') || '%'
            AND pj.outcome_score IS NOT NULL
            ORDER BY pj.created_at DESC LIMIT 1), '-') AS score
        FROM daily_summary ds
        WHERE ds.date >= '2026-03-24'
        ORDER BY ds.date DESC
        """
    )
    rows = merge_plan_page_dates(rows)
    today = local_today()
    output = render(rows, today)
    DATA_INDEX.parent.mkdir(parents=True, exist_ok=True)
    DATA_INDEX.write_text(output, encoding="utf-8")
    INDEX_ALIAS.parent.mkdir(parents=True, exist_ok=True)
    INDEX_ALIAS.write_text(render_alias(today), encoding="utf-8")
    print(f"Plans index: {len(rows)} days -> data/plans/index.md; plans/index.md is a short alias")


if __name__ == "__main__":
    main()
