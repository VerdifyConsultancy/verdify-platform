#!/usr/bin/env python3
"""
checklist-summary.py — Output today's checklist status.

Usage:
    checklist-summary.py                # human-readable text (default)
    checklist-summary.py --format text  # same
    checklist-summary.py --format json  # JSON array
    checklist-summary.py --date 2026-03-25
"""

import asyncio
import json
import logging
import os
import sys
from datetime import date

import asyncpg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [checklist] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


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
    fmt = "text"

    if "--date" in sys.argv:
        idx = sys.argv.index("--date")
        target = date.fromisoformat(sys.argv[idx + 1])
    if "--format" in sys.argv:
        idx = sys.argv.index("--format")
        fmt = sys.argv[idx + 1]

    conn = await asyncpg.connect(get_db_url())
    try:
        rows = await conn.fetch(
            """
            SELECT
                t.task, t.zone, t.priority, t.frequency,
                l.completed_at, l.completed_by, l.skipped, l.skip_reason, l.notes
            FROM daily_checklist_template t
            LEFT JOIN daily_checklist_log l ON l.template_id = t.id AND l.date = $1
            WHERE t.is_active = true
            ORDER BY (l.completed_at IS NOT NULL) ASC, t.priority ASC, t.zone NULLS LAST
        """,
            target,
        )

        if fmt == "json":
            items = []
            for r in rows:
                items.append(
                    {
                        "task": r["task"],
                        "zone": r["zone"],
                        "priority": r["priority"],
                        "frequency": r["frequency"],
                        "completed": r["completed_at"] is not None,
                        "completed_at": r["completed_at"].strftime("%H:%M") if r["completed_at"] else None,
                        "completed_by": r["completed_by"],
                        "skipped": r["skipped"] or False,
                        "notes": r["notes"],
                    }
                )
            print(json.dumps(items, indent=2))
        else:
            pending = sum(1 for r in rows if r["completed_at"] is None and not (r["skipped"] or False))
            done = sum(1 for r in rows if r["completed_at"] is not None)
            total = len(rows)

            print(f"Greenhouse Checklist — {target}")
            print(f"{done}/{total} complete, {pending} pending")
            print()

            for r in rows:
                zone_str = f" ({r['zone']})" if r["zone"] else ""
                pri_str = f" — priority {r['priority']}" if r["priority"] == 1 else ""

                if r["completed_at"]:
                    time_str = r["completed_at"].strftime("%H:%M")
                    by_str = f" by {r['completed_by']}" if r["completed_by"] else ""
                    print(f"  [x] {r['task']}{zone_str} — done {time_str}{by_str}")
                elif r["skipped"]:
                    reason = f" — {r['skip_reason']}" if r.get("skip_reason") else ""
                    print(f"  [-] {r['task']}{zone_str} — skipped{reason}")
                else:
                    print(f"  [ ] {r['task']}{zone_str}{pri_str}")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
