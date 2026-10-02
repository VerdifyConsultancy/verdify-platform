"""Bound complete replay batches to actual climate rows, retaining the as-of seed."""

from __future__ import annotations

import argparse
import csv
from datetime import UTC, datetime
from pathlib import Path


def climate_bounds(path: Path) -> tuple[datetime, datetime] | None:
    first = last = None
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            moment = datetime.fromisoformat(row["ts"].replace("Z", "+00:00"))
            if moment.tzinfo is None or moment.utcoffset() is None:
                raise ValueError("replay climate timestamps require an offset")
            moment = moment.astimezone(UTC)
            if last is not None and moment <= last:
                raise ValueError("replay climate timestamps must increase")
            first = first or moment
            last = moment
    return None if first is None else (first, last)


def query(bounds: tuple[datetime, datetime] | None, *, all_history: bool = False) -> str:
    prefix = ""
    where = "greenhouse_id = 'vallery'"
    if not all_history:
        if bounds is None:
            where += " AND false"
        else:
            start, end = bounds
            if any(t.tzinfo is None or t.utcoffset() is None for t in bounds) or end < start:
                raise ValueError("invalid replay timestamp bounds")
            # Canonical datetime output is the only interpolated input. A batch
            # qualifies by the same temp_high predicate as the original export.
            start, end = (t.astimezone(UTC).isoformat() for t in bounds)
            prefix = f"""WITH seed AS (
    SELECT ts FROM setpoint_snapshot
    WHERE greenhouse_id = 'vallery' AND parameter = 'temp_high'
      AND ts <= TIMESTAMPTZ '{start}'
    ORDER BY ts DESC LIMIT 1
)
"""
            where += f"""
    AND ts >= COALESCE((SELECT ts FROM seed), TIMESTAMPTZ '{start}')
    AND ts <= TIMESTAMPTZ '{end}'"""
    return f"""COPY (
    {prefix}SELECT greenhouse_id, ts,
        jsonb_object_agg(parameter, to_jsonb(value) ORDER BY parameter) AS config_payload
    FROM setpoint_snapshot
    WHERE {where}
    GROUP BY greenhouse_id, ts
    HAVING bool_or(parameter = 'temp_high')
    ORDER BY ts
) TO STDOUT WITH (FORMAT csv, DELIMITER E'\\t', HEADER, NULL '')
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--climate", type=Path, required=True)
    parser.add_argument("--all-history", action="store_true")
    args = parser.parse_args()
    bounds = None if args.all_history else climate_bounds(args.climate)
    print(query(bounds, all_history=args.all_history))


if __name__ == "__main__":
    main()
