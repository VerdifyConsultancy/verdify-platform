"""Audit historical fixed-panel availability without inventing crop targets.

The input is the private JSON emitted by ``fixed_panel.py emit-sql``. This
reports database-flush field completeness, not fresh per-probe observations or
a historical physical outcome. It deliberately does not accept a target band.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fixed_panel import AXES, BIN, FIELDS, ZONES, canonical, digest, finite, keys, mean, timestamp

DENVER = ZoneInfo("America/Denver")


def coverage(bundle: dict) -> dict:
    keys(
        bundle,
        ("contract_version", "sample_basis", "greenhouse_id", "exported_at", "window_start", "window_end", "rows"),
    )
    if (
        type(bundle["contract_version"]) is not int
        or bundle["contract_version"] != 1
        or bundle["sample_basis"] != "database_flush_snapshot"
        or bundle["greenhouse_id"] != "vallery"
    ):
        raise ValueError("unsupported fixed-panel export")
    start, end = timestamp(bundle["window_start"]), timestamp(bundle["window_end"])
    if (
        start.minute % 15
        or end.minute % 15
        or start.second
        or end.second
        or start.microsecond
        or end.microsecond
        or not start < end <= start + timedelta(days=62)
        or timestamp(bundle["exported_at"]) < end
    ):
        raise ValueError("invalid completed quarter-hour window")
    if not isinstance(bundle["rows"], list):
        raise TypeError("rows must be an array")

    exact = defaultdict(list)
    excluded = Counter()
    for row in bundle["rows"]:
        keys(row, ("ts", "greenhouse_id"), FIELDS)
        ts = timestamp(row["ts"])
        if row["greenhouse_id"] != "vallery":
            excluded["other_greenhouse"] += 1
        elif not start <= ts < end:
            excluded["outside_window"] += 1
        else:
            exact[ts].append(row)

    minute_values = defaultdict(lambda: defaultdict(list))
    conflicts = defaultdict(set)
    for ts, rows in exact.items():
        minute = ts.replace(second=0, microsecond=0)
        for field in FIELDS:
            values = [row.get(field) for row in rows]
            tokens = {(type(v).__name__, repr(v)) if not finite(v) else ("finite", float(v)) for v in values}
            if len(tokens) > 1:
                conflicts[minute].add(field)
            elif finite(values[0]):
                minute_values[minute][field].append(float(values[0]))
    minutes = {
        minute: {field: mean(values) for field, values in fields.items() if field not in conflicts[minute]}
        for minute, fields in minute_values.items()
    }
    observed_minutes = {ts.replace(second=0, microsecond=0) for ts in exact}

    bins = []
    totals = Counter()
    for index in range((end - start) // BIN):
        bucket = start + index * BIN
        slots = [bucket + timedelta(minutes=offset) for offset in range(15)]
        available = {
            axis: sum(all(f"{axis}_{zone}" in minutes.get(slot, {}) for zone in ZONES) for slot in slots)
            for axis in AXES
        }
        joint_slots = [slot for slot in slots if all(field in minutes.get(slot, {}) for field in FIELDS)]
        available["joint"] = len(joint_slots)
        panel_mean = {}
        for axis in AXES:
            panel_mean[axis] = (
                mean([mean([minutes[slot][f"{axis}_{zone}"] for zone in ZONES]) for slot in joint_slots])
                if joint_slots
                else None
            )
        missing = {field: sum(field not in minutes.get(slot, {}) for slot in slots) for field in FIELDS}
        conflicting = sum(bool(conflicts[slot]) for slot in slots)
        row = {
            "bucket_start": bucket.isoformat(),
            "local_day": bucket.astimezone(DENVER).date().isoformat(),
            "complete_minutes": available,
            "panel_mean": panel_mean,
            "missing_minutes_by_field": missing,
            "conflicting_minutes": conflicting,
        }
        bins.append(row)
        totals["observed_bins"] += any(slot in observed_minutes for slot in slots)
        totals["joint_complete_minutes"] += available["joint"]
        totals["conflicting_minutes"] += conflicting
        for axis in (*AXES, "joint"):
            totals[f"{axis}_eligible_bins_12_of_15"] += available[axis] >= 12
        for field, count in missing.items():
            totals[f"missing_{field}_minutes"] += count

    return {
        "schema": "verdify-fixed-panel-coverage-v1",
        "definition": "fixed-north-east-west-database-flush-coverage",
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "sample_basis": "database_flush_snapshot_not_per_probe_observation_time",
        "physical_proof_eligible": False,
        "historical_target_verified": False,
        "historical_contributor_identity_verified": False,
        "causal_effect_estimate": False,
        "input_canonical_sha256": digest(canonical(bundle)),
        "input_rows": len(bundle["rows"]),
        "scoped_rows": sum(len(rows) for rows in exact.values()),
        "excluded_rows": dict(sorted(excluded.items())),
        "distinct_exact_timestamps": len(exact),
        "distinct_observed_minutes": len(observed_minutes),
        "summary": {"expected_bins": len(bins), **dict(sorted(totals.items()))},
        "bins": bins,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        raw = args.input.read_bytes()
        report = coverage(json.loads(raw))
        report["input_file_sha256"] = digest(raw)
        report["calculation_source_sha256"] = digest(Path(__file__).read_bytes())
        with args.output.open("xb") as output:
            output.write(canonical(report) + b"\n")
    except (ValueError, TypeError, OverflowError, OSError):
        parser.exit(2, "fixed-panel coverage input/output rejected; no scientific acceptance claimed\n")


if __name__ == "__main__":
    main()
