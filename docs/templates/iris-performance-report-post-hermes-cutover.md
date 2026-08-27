# Iris planner — post-cutover performance report (TEMPLATE)

**Window:** `<post-cutover-start>` → `<post-cutover-end>` (30 days)
**Subject:** Iris on Hermes + OpenAI GPT-5.6 Sol (reasoning_effort=xhigh)
**Author:** `<author>`
**Baseline:** `/mnt/iris/vault/iris-baseline-2026-05-10.md`
**Method:** read-only DB pulls from `verdify-timescaledb`; same queries as the 2026-05-10 baseline (§7) re-run against the post-cutover window.

> Use the GPT-5.6 Sol/xhigh subject only for a window after separately validated
> live activation is verified. Until then, replace it with the actual deployed
> profile recorded for the reporting window.

This file is a template — fill in the bracketed values from the actual queries during Phase 8. The baseline-comparison table at the top of each section is the binding acceptance gate.

---

## 1. Executive summary

`<TODO: 1-paragraph summary referencing the loop-overhaul plan's stated goals — causal evaluation, structured-hypothesis enforcement, vectorized retrieval, trigger reshape, gateway+model swap — and whether each landed measurably.>`

**TL;DR**

- Loop closure within 25h: **`<X>%`** (baseline 41.5%, target ≥ 90%)
- Structured hypothesis on SUNRISE/SUNSET: **`<X>%`** (baseline ~12%, target ≥ 95%)
- `lesson_extracted` → `planner_lessons` row created: **`<X>%`** (baseline 27%, target 100%)
- Mean planner_score: **`<X>`** (baseline 57.1, floor 54.3)
- Plans per day: mean **`<X>`**, max **`<X>`** (baseline 0–41 range, target ≤ 8 mean / ≤ 12 max)
- Anchor-vs-Iris mean abs deviation: **`<X>`** (baseline 1.26, target ≤ 0.7)

---

## 2. Scope & method

Same as `/mnt/iris/vault/iris-performance-report-2026-05-10.md` §2, with the
following deltas:

- All cycles in this window routed through `hermes-iris` (OpenAI GPT-5.6 Sol,
  reasoning_effort=xhigh). Verify via `SELECT COUNT(*) FILTER (WHERE hermes_run_id IS NOT NULL)` from `plan_delivery_log`.
- Causal attribution: `v_plan_execution_intervals` is live (migration 111).
  Outcome scoring uses `fn_plan_anchor_score()` for the deterministic anchor.
- Trigger taxonomy is the closed set: SUNRISE / SUNSET / SOLAR_MAX /
  TRANSITION:peak_stress / TRANSITION:decline / FORECAST_DEVIATION / MANUAL.

---

## 3. Quantitative findings

### 3.1 Plan quality

| Metric | Baseline | Post-cutover | Δ |
|---|---:|---:|---:|
| Plans in window | 138 | `<X>` | `<Δ>` |
| Evaluated within 25h | 47 / 138 (34%) | `<X / Y>` (`<X>%`) | `<Δ>` |
| SUNRISE-only within 25h | 17 / 41 (41.5%) | `<X / Y>` (`<X>%`) | **`<Δ>`** |
| `hypothesis_structured` populated | 17 (12%) | `<X>` (`<X>%`) | **`<Δ>`** |
| Mean outcome_score | 4.73 | `<X>` | `<Δ>` |
| Mean anchor_score | 3.68 | `<X>` | `<Δ>` |
| Mean abs deviation (Iris vs anchor) | 1.26 | `<X>` | **`<Δ>`** |
| Plans scoring ≥8 | 11 (all from 04-09/04-10) | `<X>` | `<Δ>` |

### 3.2 Band compliance & stress

`<TODO: re-run the half-period query and the daily series; compare to
baseline 57.1 / 55.1 / 10.7 / 5.17 post-hardening means>`

### 3.3 Loop closure

`<TODO: same loop-closure SQL as baseline §3.3; target 90%+ on SUNRISE
within 25h>`

### 3.4 Tunable hygiene

`<TODO: plans/day distribution, most-touched tunables, AI vs other
source split. Confirm 0 plans pushed band-owned params (Phase 4
guarantee).>`

### 3.5 Cost & utility

`<TODO: total $/day, gas/electric/water breakdown, surface
GPT-5.6-Sol-xhigh-reasoning per-cycle cost from Hermes telemetry separately.
Note: cost-side gates from the plan are descriptive, not binding —
greenhouse outcomes drive acceptance.>`

---

## 4. Qualitative findings

### 4.1 Did the migration help measurably?

`<TODO: the only honest answer comes from the anchor-vs-Iris deviation
delta + the absolute compliance numbers. Compare with the baseline's
key finding: "Iris over-grades by mean +1.0 point". Did GPT-5.6 Sol close
that gap?>`

### 4.2 Lesson quality

`<TODO: post-cutover lesson taxonomy. Did the structured-hypothesis
enforcement (Phase 2b) drive cleaner lessons? What's the active /
inactive / superseded ratio now?>`

### 4.3 Vector retrieval utilization

`<TODO: how often did Iris call lessons_search vs falling back to the
static top-10? knowledge_search call rate against playbook vs site_doc?
Did GPT-5.6 Sol use the FORECAST CALIBRATION block to discount the +47 W/m²
solar bias?>`

### 4.4 Incident response

`<TODO: did any incident occur during the post-cutover window? If yes,
how did Iris respond compared to the 2026-04-21 baseline incident?>`

---

## 5. Self-improvement loop — wired and effective?

`<TODO: reproduce baseline §5's table updating every "Effective?" column
from ⚠/✗ to ✓ (or document why not). The plan's stated goal was every
row in the green column post-Phase-8.>`

---

## 6. Recommendations

`<TODO: any P0/P1 follow-ups the post-cutover read identifies. If the
loop is now fully closed and the anchor-vs-Iris deviation is within
target, the planner may be ready for the next class of improvement
(crop-specific tuning, multi-greenhouse generalization, etc.).>`

---

## 7. Appendix — queries

All queries reproducible from the 2026-05-10 baseline §7. Re-run
against the post-cutover window:

```sql
-- Loop closure (SUNRISE-only)
WITH sunrise_plans AS (
  SELECT plan_id, created_at, validated_at, outcome_score
    FROM plan_journal
   WHERE created_at >= '<post-cutover-start>'
     AND created_at < '<post-cutover-end>'
     AND EXTRACT(hour FROM created_at AT TIME ZONE 'America/Denver') BETWEEN 5 AND 9
)
SELECT COUNT(*) AS sunrise_plans,
       COUNT(*) FILTER (WHERE outcome_score IS NOT NULL) AS evaluated,
       COUNT(*) FILTER (WHERE validated_at - created_at <= interval '25 hours') AS within_25h,
       ROUND(100.0 * COUNT(*) FILTER (WHERE validated_at - created_at <= interval '25 hours')
             / NULLIF(COUNT(*),0), 1) AS pct
  FROM sunrise_plans;

-- Anchor vs Iris deviation
SELECT 'iris_mean' AS metric, ROUND(AVG(outcome_score)::numeric, 2) FROM plan_journal
 WHERE created_at >= '<post-cutover-start>' AND outcome_score IS NOT NULL AND anchor_score IS NOT NULL
UNION ALL SELECT 'anchor_mean', ROUND(AVG(anchor_score)::numeric, 2) FROM plan_journal
 WHERE created_at >= '<post-cutover-start>' AND outcome_score IS NOT NULL AND anchor_score IS NOT NULL
UNION ALL SELECT 'mean_abs_dev', ROUND(AVG(ABS(outcome_score - anchor_score))::numeric, 2) FROM plan_journal
 WHERE created_at >= '<post-cutover-start>' AND outcome_score IS NOT NULL AND anchor_score IS NOT NULL;

-- Lessonization coverage
SELECT COUNT(*) FILTER (WHERE lesson_extracted IS NOT NULL) AS has_lesson_text,
       COUNT(*) FILTER (WHERE lesson_extracted IS NOT NULL AND NOT EXISTS (
         SELECT 1 FROM planner_lessons WHERE plan_journal.plan_id = ANY(source_plan_ids)
       )) AS broken_lessonization
  FROM plan_journal
 WHERE created_at >= '<post-cutover-start>';

-- Gateway routing breakdown
SELECT event_type,
       CASE WHEN hermes_run_id IS NOT NULL THEN 'hermes' ELSE 'openclaw' END AS gateway,
       COUNT(*) AS n
  FROM plan_delivery_log
 WHERE delivered_at >= '<post-cutover-start>'
 GROUP BY event_type, gateway
 ORDER BY event_type, gateway;
```

---

*Filling this template completes Phase 8 and discharges the Iris loop overhaul.*
