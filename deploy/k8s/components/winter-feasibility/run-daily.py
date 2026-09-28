"""Run bounded catch-up for the pinned observational winter extractor."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

SOURCE = Path("/source/research/planner-efficacy/winter_feasibility.py")
ARCHIVE = Path("/archive")


def due_days(instance: dict, now: datetime, days_dir: Path) -> list[date]:
    start = date.fromisoformat(instance["start_local_date"])
    yesterday = now.astimezone(ZoneInfo("America/Denver")).date() - timedelta(days=1)
    last = min(yesterday, start + timedelta(days=59))
    if last < start:
        return []
    missing = [
        start + timedelta(days=offset)
        for offset in range((last - start).days + 1)
        if not (days_dir / f"{start + timedelta(days=offset)}.json").exists()
    ]
    if not missing:
        return []
    # At most two DB snapshots per run: oldest missed day plus yesterday when
    # it is due. Late catch-up is retained and labeled late by the extractor.
    selected = missing[:2]
    if last in missing and last not in selected:
        selected = [missing[0], last]
    return selected


def main() -> None:
    pin = os.environ["SOURCE_SHA"]
    if (Path("/source") / "source-revision").read_text().strip() != pin:
        raise ValueError("retained source revision differs from CronJob pin")
    instance_path = ARCHIVE / "instance.json"
    instance = json.loads(instance_path.read_bytes())
    selected = due_days(instance, datetime.now(UTC), ARCHIVE / "days")
    if not selected:
        print("[winter] no missing completed day in registered interval")
        return
    command = ["python", str(SOURCE)]
    common = ["--instance", str(instance_path), "--output-dir", str(ARCHIVE)]
    for day in selected:
        subprocess.run([*command, "collect", *common, "--day", day.isoformat()], check=True)
    as_of = datetime.now(UTC).isoformat(timespec="microseconds")
    subprocess.run([*command, "manifest", *common, "--as-of", as_of], check=True)


if __name__ == "__main__":
    main()
