#!/usr/bin/env python3
"""
generate-checklist.py — Generate today's checklist items from templates.

Idempotent: skips tasks already generated for today.
Frequency logic:
  daily    — every day
  weekly   — if last completed >6 days ago (or never)
  biweekly — if last completed >13 days ago (or never)
  monthly  — if last completed >29 days ago (or never)

Usage:
    generate-checklist.py           # generate for today
    generate-checklist.py --date 2026-03-25
"""

import asyncio
import logging
import os
import sys
from datetime import date

import asyncpg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [checklist-gen] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

FREQ_DAYS = {"daily": 0, "weekly": 6, "biweekly": 13, "monthly": 29}


def get_db_url() -> str:
    pw = "verdify"
    env_file = "/srv/verdify/.env"
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                if line.strip().startswith("POSTGRES_PASSWORD="):
                    pw = line.strip().split("=", 1)[1].strip().strip('"').strip("'")
    return f"postgresql://verdify:{pw}@localhost:5432/verdify"


async def main():
    target = date.today()
    if "--date" in sys.argv:
        idx = sys.argv.index("--date")
        target = date.fromisoformat(sys.argv[idx + 1])

    conn = await asyncpg.connect(get_db_url())
    try:
        templates = await conn.fetch("SELECT id, task, frequency FROM daily_checklist_template WHERE is_active = true")

        inserted = 0
        skipped = 0
        for t in templates:
            tid = t["id"]
            freq = t["frequency"]
            min_days = FREQ_DAYS.get(freq, 0)

            # Check if due: daily always, others based on last completion
            if min_days > 0:
                last = await conn.fetchval(
                    "SELECT MAX(date) FROM daily_checklist_log WHERE template_id = $1 AND completed_at IS NOT NULL", tid
                )
                if last and (target - last).days <= min_days:
                    continue  # Not due yet

            # Insert if not already present for this date
            result = await conn.execute(
                "INSERT INTO daily_checklist_log (template_id, date) "
                "VALUES ($1, $2) ON CONFLICT (template_id, date) DO NOTHING",
                tid,
                target,
            )
            if result == "INSERT 0 1":
                inserted += 1
            else:
                skipped += 1

        log.info("%s: %d items generated, %d already existed", target, inserted, skipped)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
