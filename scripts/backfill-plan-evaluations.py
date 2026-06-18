#!/usr/bin/env python3
"""PL-1 (Sprint 20): backfill plan_journal.outcome_score for historical plans
that were never evaluated via the MCP plan_evaluate tool.

Approach: for each plan_journal row with outcome_score IS NULL:
  - determine its "active window" (from created_at to the next plan's
    created_at, or — for the latest plan — exit; still in flight)
  - average v_planner_performance.compliance_pct over that window
  - map compliance_pct (0–100) to outcome_score (1–10)
  - validate through PlanEvaluation (verdify_schemas) before UPDATE

The backfill is one-shot. After it runs, the null count should drop to
≤2 (the current plan + maybe one just-rotated plan). Iris continues to
call plan_evaluate on live plans via the MCP tool — this script only
closes the historical gap.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, "/mnt/iris/verdify")
from verdify_schemas import PlanEvaluation  # noqa: E402


def _load_dsn() -> str:
    env_path = Path("/srv/verdify/ingestor/.env")
    env = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k] = v
    return (
        f"postgresql://{env.get('DB_USER', 'verdify')}:"
        f"{env.get('DB_PASSWORD', os.environ.get('POSTGRES_PASSWORD', 'verdify'))}"
        f"@{env.get('DB_HOST', 'localhost')}:{env.get('DB_PORT', '5432')}/{env.get('DB_NAME', 'verdify')}"
    )


def _compliance_to_score(compliance_pct: float | None, stress_h: float | None) -> int:
    """Map compliance_pct (0–100) + total stress hours → outcome_score 1–10.

    Roughly: ≥95% compliance, <1 h stress ⇒ 10; <50% compliance or >10 h
    stress ⇒ 1. Clamped to [1, 10].
    """
    if compliance_pct is None:
        compliance_pct = 50.0
    if stress_h is None:
        stress_h = 5.0
    # Base: compliance mapped linearly to [1, 10]
    base = 1 + (compliance_pct / 100.0) * 9.0
    # Stress penalty: up to 3 points off for 10 h total stress
    penalty = min(3.0, stress_h * 0.3)
    score = max(1.0, min(10.0, base - penalty))
    return int(round(score))


async def backfill() -> None:
    dsn = _load_dsn()
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            """
            SELECT pj.plan_id, pj.created_at,
                   lead(pj.created_at) OVER (ORDER BY pj.created_at) AS next_created_at
              FROM plan_journal pj
             WHERE pj.outcome_score IS NULL
             ORDER BY pj.created_at
            """,
        )
        if not rows:
            print("No null-scored plans to backfill.")
            return

        # Skip the most recent plan — still in flight, don't score yet
        to_backfill = rows[:-1]
        skipped_current = rows[-1]["plan_id"]
        print(f"Found {len(rows)} null-scored plans. Skipping current plan {skipped_current!r}.")
        print(f"Backfilling {len(to_backfill)} historical plans…")

        updated = 0
        for r in to_backfill:
            plan_id = r["plan_id"]
            start = r["created_at"]
            end = r["next_created_at"]
            # Aggregate planner-performance over the window (by date membership).
            #
            # Reward-loop integrity (band-compliance §7.1/§7.3): during the
            # migration-146 dual-write window this MUST keep reading the BINARY
            # compliance_pct — moving it to compliance_v2_attributable_pct now
            # would re-scale the reward before the ladder is re-anchored (147)
            # and corrupt the learning loop. The reward swap is migration 147,
            # which also freezes the 103 anchored + 228 outcome rows; this
            # one-shot backfill is NOT re-run over history then.
            perf = await conn.fetchrow(
                """
                SELECT avg(compliance_pct) AS avg_compliance,
                       sum(total_stress_h) AS total_stress
                  FROM v_planner_performance
                 WHERE date >= ($1::timestamptz)::date
                   AND date < ($2::timestamptz)::date
                """,
                start,
                end,
            )
            avg_compliance = float(perf["avg_compliance"]) if perf and perf["avg_compliance"] is not None else None
            total_stress = float(perf["total_stress"]) if perf and perf["total_stress"] is not None else None

            score = _compliance_to_score(avg_compliance, total_stress)
            actual_outcome = (
                f"[backfill] avg compliance {avg_compliance:.1f}% "
                if avg_compliance is not None
                else "[backfill] compliance unavailable "
            ) + (
                f"(stress {total_stress:.1f} h) over plan window {start:%Y-%m-%d %H:%M} \u2192 {end:%Y-%m-%d %H:%M}"
                if total_stress is not None
                else f"over plan window {start:%Y-%m-%d %H:%M} \u2192 {end:%Y-%m-%d %H:%M}"
            )

            # Validate through PlanEvaluation schema
            ev = PlanEvaluation.model_validate(
                {
                    "plan_id": plan_id,
                    "outcome_score": score,
                    "actual_outcome": actual_outcome,
                    "lesson_extracted": None,
                },
            )

            await conn.execute(
                """
                UPDATE plan_journal SET
                    outcome_score = $2,
                    actual_outcome = $3,
                    validated_at = now()
                 WHERE plan_id = $1
                """,
                ev.plan_id,
                ev.outcome_score,
                ev.actual_outcome,
            )
            updated += 1
            print(
                f"  {plan_id}: score={score} (compliance="
                f"{'n/a' if avg_compliance is None else f'{avg_compliance:.0f}%'}, "
                f"stress={'n/a' if total_stress is None else f'{total_stress:.1f}h'})"
            )

        print(f"\nUpdated {updated} plan_journal rows.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(backfill())
