#!/usr/bin/env python3
"""Pre-register the planner study (#1360): the baseline vector file, the seed and both hashes, in one commit.

Read-only on the database. Feed it the output of scripts/sql/planner-study-baseline.sql:

    bash scripts/verdify-db.sh prod -At < scripts/sql/planner-study-baseline.sql \\
      | python3 scripts/planner-study-preregister.py --study planner-ab-winter-2026

For each of the 48 planner-pushable fields the baseline is the median of the device-confirmed plan values over the
14 days before the query, else the newest readback (the setpoint snapshot, or a switch's equipment mirror), snapped
to the registry grid (``snap_setting``) so the held value is exactly what the dispatcher sends. It writes
``verdify_schemas/planner_study_baselines/<study>.json`` and sets ``baseline_vector.sha256``, ``schedule.seed`` (a fresh
random seed unless ``--seed``) and ``schedule.schedule_sha256`` in the study's YAML. It refuses a study that is already
pre-registered, and a field with no value. Commit both files together, before day 1; API-28 start refuses otherwise.
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from verdify_schemas.planner_study import (  # noqa: E402
    BASELINE_DIR,
    STUDY_DIR,
    STUDY_FILES,
    baseline_sha256,
    load_study,
    parse_study,
    schedule_sha256,
)
from verdify_schemas.setting_values import snap_setting  # noqa: E402
from verdify_schemas.tunable_registry import PLANNER_PUSHABLE_REG, SWITCH_READBACK_EQUIPMENT  # noqa: E402


def parse_rows(lines: list[str]) -> dict[str, dict[str, float]]:
    rows: dict[str, dict[str, float]] = {"median": {}, "readback": {}, "equipment": {}}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        kind, name, value = line.split("|")
        if kind not in rows:
            raise SystemExit(f"unexpected row kind {kind!r}: {line}")
        rows[kind][name] = float(value)
    return rows


def baseline(rows: dict[str, dict[str, float]]) -> tuple[dict[str, float], dict[str, str]]:
    values: dict[str, float] = {}
    sources: dict[str, str] = {}
    missing = []
    for name in sorted(PLANNER_PUSHABLE_REG):
        if name in rows["median"]:
            raw, source = rows["median"][name], "median_confirmed_plan_14d"
        elif name in rows["readback"]:
            raw, source = rows["readback"][name], "readback"
        elif SWITCH_READBACK_EQUIPMENT.get(name) in rows["equipment"]:
            raw, source = rows["equipment"][SWITCH_READBACK_EQUIPMENT[name]], "equipment_mirror"
        else:
            missing.append(name)
            continue
        values[name] = float(snap_setting(name, raw))
        sources[name] = source
    if missing:
        raise SystemExit("no confirmed plan value or readback for: " + ", ".join(missing))
    return values, sources


def set_null(text: str, block: str, key: str, value: str) -> str:
    """Replace ``  key: null`` inside the top-level ``block:`` mapping, exactly once."""
    pattern = re.compile(rf"(^{block}:\n(?:[ \t].*\n|\n)*?[ \t]+{key}: )null$", re.MULTILINE)
    updated, count = pattern.subn(rf"\g<1>{value}", text, count=1)
    if count != 1:
        raise SystemExit(f"{block}.{key} is not null in the study file")
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--study", default="planner-ab-winter-2026", choices=sorted(STUDY_FILES))
    parser.add_argument("--seed", type=int, help="a fixed seed (default: a fresh random one)")
    args = parser.parse_args()

    study = load_study(args.study)
    if study.preregistered:
        raise SystemExit(f"{args.study} is already pre-registered; a new design is a new study version")
    values, sources = baseline(parse_rows(sys.stdin.read().splitlines()))
    digest = baseline_sha256(values)
    seed = args.seed if args.seed is not None else secrets.randbelow(2**31 - 1) + 1
    schedule = schedule_sha256(seed, study.schedule.pairs)

    BASELINE_DIR.mkdir(exist_ok=True)
    document = {
        "study_id": study.study_id,
        "computed_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "rule": study.baseline_vector.rule,
        "sources": dict(sorted(sources.items())),
        "values": dict(sorted(values.items())),
        "sha256": digest,
    }
    (BASELINE_DIR / f"{study.study_id}.json").write_text(json.dumps(document, indent=2) + "\n")

    path = STUDY_DIR / STUDY_FILES[args.study]
    text = path.read_text()
    text = set_null(text, "baseline_vector", "sha256", f'"{digest}"')
    text = set_null(text, "schedule", "seed", str(seed))
    text = set_null(text, "schedule", "schedule_sha256", f'"{schedule}"')
    registered = parse_study(text)
    if not registered.preregistered:
        raise SystemExit("the edited study file does not read pre-registered")
    path.write_text(text)
    counts = {kind: sum(1 for source in sources.values() if source == kind) for kind in sorted(set(sources.values()))}
    print(
        json.dumps(
            {
                "study": study.study_id,
                "seed": seed,
                "baseline_sha256": digest,
                "schedule_sha256": schedule,
                "fields": len(values),
                "sources": counts,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
