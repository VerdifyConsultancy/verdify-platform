#!/usr/bin/env python3
"""§8.9 live-shadow agreement gate report (#587).

Computes, over a date range of ``twin_live_results`` rows, the audit §8.9
gate: **byte-identical policy AND action agreement across 7-14 days of live
shadow**, with per-day coverage and gap accounting. Gaps, warm-up, and
unmatched-state ticks NEVER count as agreement; they only reduce coverage.

Gate definition (per UTC day, then across the window):
  * a day is COVERED when comparable ticks (agreement + divergence) make up
    at least ``--min-coverage`` of the day's ticks;
  * a day PASSES when it is covered, has zero divergence, zero
    unmatched_state, and every comparable tick carried byte-identical policy
    identity (policy_hash_match on all of them — enforced per-row by the
    twin_live_results agreement CHECK, re-verified here);
  * the WINDOW passes when it spans 7-14 calendar days, every day has ticks,
    and every day passes.

Output: one machine-readable JSON document on stdout with a
``report_sha256`` over its RFC-8785-style canonical serialization (the same
canonicalizer the policy wire codec uses), so a passing report is
hash-pinnable in the experiment record.

Usage:
  report_agreement.py --start 2026-08-01 --end 2026-08-08 \
      [--env prod] [--greenhouse vallery] [--min-coverage 0.9]

DSN comes from REPORT_DSN or TWIN_REPORT_DSN (any role with SELECT on
twin_live_results — the twin runtime role itself is INSERT-only by design and
cannot read results back; reporting is an operator/analyst action).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

GATE_MIN_DAYS = 7
GATE_MAX_DAYS = 14

DAY_QUERY = """
SELECT (tick_ts AT TIME ZONE 'UTC')::date AS day,
       count(*)                                            AS ticks,
       count(*) FILTER (WHERE classification = 'agreement')       AS agreement,
       count(*) FILTER (WHERE classification = 'divergence')      AS divergence,
       count(*) FILTER (WHERE classification = 'warm_up')         AS warm_up,
       count(*) FILTER (WHERE classification = 'unmatched_state') AS unmatched_state,
       count(*) FILTER (WHERE classification = 'gap')             AS gap,
       count(*) FILTER (WHERE classification IN ('agreement', 'divergence')
                        AND NOT COALESCE(policy_hash_match, false))
                                                            AS comparable_hash_mismatch,
       count(DISTINCT gap_reason) FILTER (WHERE gap_reason IS NOT NULL) AS distinct_reasons,
       count(DISTINCT vector_content_sha256)
           FILTER (WHERE vector_content_sha256 IS NOT NULL) AS distinct_policies
  FROM public.twin_live_results
 WHERE twin_env = %(env)s
   AND twin_mode = 'live'
   AND greenhouse_id = %(greenhouse)s
   AND (tick_ts AT TIME ZONE 'UTC')::date BETWEEN %(start)s AND %(end)s
 GROUP BY 1
 ORDER BY 1
"""


def day_summary(row: dict, min_coverage: float) -> dict:
    """Per-day §8.9 accounting from one aggregate row."""
    ticks = row["ticks"]
    comparable = row["agreement"] + row["divergence"]
    coverage = (comparable / ticks) if ticks else 0.0
    hash_ok = row["comparable_hash_mismatch"] == 0
    passes = (
        ticks > 0 and coverage >= min_coverage and row["divergence"] == 0 and row["unmatched_state"] == 0 and hash_ok
    )
    return {
        "day": row["day"].isoformat() if isinstance(row["day"], date) else str(row["day"]),
        "ticks": ticks,
        "agreement": row["agreement"],
        "divergence": row["divergence"],
        "warm_up": row["warm_up"],
        "unmatched_state": row["unmatched_state"],
        "gap": row["gap"],
        "comparable": comparable,
        "coverage": round(coverage, 6),
        "policy_hash_identical": hash_ok,
        "distinct_policies": row["distinct_policies"],
        "passes": passes,
    }


def compute_report(
    day_rows: list[dict],
    *,
    start: date,
    end: date,
    env: str,
    greenhouse: str,
    min_coverage: float,
) -> dict:
    """Pure gate math over per-day aggregates (unit-tested without a DB)."""
    window_days = (end - start).days + 1
    expected = {start + timedelta(days=i) for i in range(window_days)}
    days = [day_summary(r, min_coverage) for r in day_rows]
    seen = {date.fromisoformat(d["day"]) for d in days}
    missing_days = sorted(d.isoformat() for d in expected - seen)

    failures: list[str] = []
    if not GATE_MIN_DAYS <= window_days <= GATE_MAX_DAYS:
        failures.append(f"window_length:{window_days}d_not_in_{GATE_MIN_DAYS}..{GATE_MAX_DAYS}")
    if missing_days:
        failures.append(f"days_without_ticks:{len(missing_days)}")
    failures.extend(f"day_failed:{d['day']}" for d in days if not d["passes"])

    totals = {
        key: sum(d[key] for d in days)
        for key in ("ticks", "agreement", "divergence", "warm_up", "unmatched_state", "gap", "comparable")
    }
    totals["coverage"] = round(totals["comparable"] / totals["ticks"], 6) if totals["ticks"] else 0.0

    return {
        "gate": "audit-8.9-live-shadow-agreement",
        "window": {"start": start.isoformat(), "end": end.isoformat(), "days": window_days},
        "twin_env": env,
        "greenhouse_id": greenhouse,
        "min_coverage": min_coverage,
        "days": days,
        "missing_days": missing_days,
        "totals": totals,
        "gate_passes": not failures,
        "failures": failures,
    }


def finalize(report: dict) -> dict:
    """Attach the canonical-JSON hash (same canonicalizer as the wire codec)."""
    from verdify_schemas.policy_vector import canonical_json_bytes

    payload = canonical_json_bytes(report)
    return {"report": report, "report_sha256": hashlib.sha256(payload).hexdigest()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--env", default="prod", choices=("dev", "stage", "prod"))
    parser.add_argument("--greenhouse", default="vallery")
    parser.add_argument("--min-coverage", type=float, default=0.9)
    args = parser.parse_args(argv)
    if args.end < args.start:
        parser.error("--end must be >= --start")

    dsn = (os.environ.get("REPORT_DSN") or os.environ.get("TWIN_REPORT_DSN") or "").strip()
    if not dsn:
        print("FATAL: set REPORT_DSN (a role with SELECT on twin_live_results)", file=sys.stderr)
        return 2

    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as conn:
        rows = conn.execute(
            DAY_QUERY,
            {"env": args.env, "greenhouse": args.greenhouse, "start": args.start, "end": args.end},
        ).fetchall()

    report = compute_report(
        rows,
        start=args.start,
        end=args.end,
        env=args.env,
        greenhouse=args.greenhouse,
        min_coverage=args.min_coverage,
    )
    document = finalize(report)
    json.dump(document, sys.stdout, indent=2, sort_keys=True)
    print()
    return 0 if report["gate_passes"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
