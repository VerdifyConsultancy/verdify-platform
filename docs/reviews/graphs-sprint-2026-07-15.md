# graphs.verdify.ai sprint — 2026-07-15

Operator-requested sprint: "all the current grafana crapshoot on
graphs.verdify.ai — some graphs not loading, database in recovery mode,
the broader page, caching." Success bar: **every graph on every page
renders and has useful data.**

## Incident: the "database is in recovery mode" storm

`verdify-db-0` was in a cgroup OOM-kill loop: kernel signal-9 kills of
postgres backends at 02:15:04Z, 02:15:39Z, 02:21:59Z on 2026-07-15 (plus two
full-container OOMKills on 2026-07-13 ~18:50Z/19:10Z and the 2026-07-09
node5 SystemOOM). Each kill forced postmaster crash recovery, so **every**
client — all Grafana panels, verdify-api (`/health/detailed` timeouts),
verdify-mcp (readyz 503s), hermes-iris — errored together with
`FATAL: the database system is in recovery mode`.

**Killer identified from the postmaster kill log**: `fn_planner_scorecard(date)`
made 27 separate scans of `v_daily_kpi`, a view with **no date pushdown**
(the filter applies after full-history window aggregations; plan cost ~195M;
one bounded evaluation = 13.8 s). The `site-evidence-planning-quality`
dashboard fires 7 such panels concurrently per load; 57 more panels across 8
dashboards read `v_daily_kpi` directly (a single economics page load fired
20). Concurrent × 27-branch inlined plans, inside a 6Gi pod, with
`statement_timeout=0` — OOM, on every dashboard refresh.

## Fixes applied (all live in prod; repo == DB == cluster)

| # | Fix | Verification |
|---|-----|--------------|
| 1 | **Migration 203**: `mv_daily_kpi` matview + `fn_planner_scorecard` repointed at it | 12 ms vs ~6 min (27×13.8 s); rollback-proofed before apply; values byte-identical |
| 2 | **57 panel queries** repointed `v_daily_kpi` → `mv_daily_kpi` (8 dashboards) | served dashboards confirmed swapped via Grafana API |
| 3 | **Migration 201** (committed 07-13 but **never applied**) applied: `v_runtime_energy_daily` joins the runtime matview | 13.5 s panel reads → matview speed; outer-rollback proof first |
| 4 | `verdify-band-curve-refresh` CronJob also refreshes `mv_daily_kpi` | full chain ran 02:40Z: band curve → equipment → daily KPI in ~35 s |
| 5 | **Migration 204**: ungated `*_est`/`*_modeled` estimate columns appended to `v_daily_kpi`; matview rebuilt atomically | pre/post row values identical on existing columns; 07-13 = 16.89 kWh → $1.88 electric / $3.55 total |
| 6 | **40 cost/kWh/water panels** repointed from fail-closed gated columns to `*_est`, titles suffixed "(est)"; `resource_terms_available` filters dropped | includes site-home "Daily Cost by Type", economics "Daily Cost by Source" |
| 7 | **Migration 205**: `v_setpoint_velocity` rewritten parallel-safe (correlated EXISTS → value-run windows) | live error "subplan was not initialized (parallel worker)" reproduced, then fixed: 59 rows / 1.6 s; equivalence proof 0 diffs over 72 h |
| 8 | **Migration 206**: raw-meter water fallback restricted to completed days | intraday 1388-gal artifact (vs 78–346 normal) no longer pollutes 30-day water panels |
| 9 | 3 sparse-column latest-value panels get 48 h scan bounds | cheap insurance vs dead-sensor full-history walks |
| 10 | **verdify-vision CronJob** PodSecurity-restricted securityContext (repo + live) | server-side apply clean (admission would warn on a bad template); pods were admission-rejected since 07-11T21:00Z, 4,654 FailedCreate |
| 11 | Grafana datasource `maxOpenConns: 10` cap (provisioning CM applied) | **takes effect on next Grafana restart — deliberately deferred, operator-scoped** |

**Durability probes**: last backend kill 02:21:59Z. 0 kills in the
38 min after (probe 03:01Z, `kubectl logs --since=38m | grep -c 'signal 9'`
= 0); ≥60-min re-probe recorded in the sprint wrap. graphs.verdify.ai
HTTP 200 / `api/health` database ok throughout the post-fix window.

## Root cause of the "empty cost graphs" (independent of the OOM)

The ADR-0004 scoring gate is **structurally unsatisfiable in prod**:
`v_runtime_energy_daily.available_for_scoring` requires
`NOT bool_or(has_uncertainty)`, but migration 193 deliberately seeded ALL
14 `electric_watts` coefficients with provisional ±10–20 % bounds "until
circuit-isolated measurement". So `energy_ok` was false for **all 345
days**; `kwh`/`cost_*` were NULL from birth. PR #437 (07-09) additionally
put the ingestor's legacy `daily_summary.cost_electric/cost_total` writes
behind the same gate — the last legacy estimates froze at 2026-07-08. The
model itself is healthy (07-13: 16.892 kWh, 100 % runtime coverage).

Panels were switched onto this fail-closed surface on 06-29 (site-home) —
that is when "daily cost by source" actually went blank.

**This sprint deliberately did NOT touch the gate** (planner_score /
deploy-gate semantics). Sprint fix = labeled estimates (migrations 204/206).

## Decisions Jason owns (queued)

1. **ADR-0004 gate relaxation** — drop the zero-uncertainty AND-term from
   `available_for_scoring` (uncertainty is already published via
   modeled_kwh_low/high; `model_quality='uncertain_coefficients'` can stay
   as a label), or land measured (lower=upper) coefficient revisions.
   Either lights up the *evidence-tier* kwh/cost columns; the first
   retroactively adds the 20 % cost term to planner_score on
   water-eligible days. Mirror the same relaxation in
   `ingestor/tasks/daily.py` (`_apply_resource_cost_gate`, and
   `_gas_btu_per_hour`'s lower≠upper → None). History needs the
   derived-history backfill; pre-2026-01-01 days have no coefficients at all.
2. **Delete wedged Job `verdify-vision-29730060`** (snapshot-protected; the
   CronJob spec fix is already live — deletion is the last unblock;
   `concurrencyPolicy: Forbid` keeps vision dead until then). Commands:
   scratchpad `vision-fix-operator-commands.sh`.
3. **Grafana restart window** for the datasource `maxOpenConns` cap
   (~40 s of 503 on graphs.verdify.ai).
4. **site-inference-infra**: `gpu_power` last row 2026-06-09 — the
   cortex/vm-docker-ai telemetry is dead (hosts likely retired). Retire the
   dashboard or revive a collector; 10 panels are permanently empty today.
5. **Irrigation program ledger**: `v_irrigation_program_daily` last row
   2026-05-30. Seasonal/expected, or a broken fertigation pipeline?
6. **Planner cadence**: `v_plan_compliance` has only ~12 gradeable rows in
   14 days (post-outage recovery + #427 planner-miss). The Plan Compliance
   panels are starving on real data, not broken SQL.
7. **Grafana 11.6→12.4.5 upgrade staged in git** (commit 37561dbb) needs an
   out-of-band renderer secret before anyone runs the gated prod sync —
   syncing without it breaks graphs.
8. **Anonymous `/api/search`** exposes the full dashboard inventory
   (including ops/tuning dashboards never embedded on the lab site). Accept
   as transparency posture or scope the anonymous org.

## Follow-ups (agent-landable, queued)

- `mv_zone_band_grade`: 32 days stale, NO refresh owner, schema COMMENT
  falsely claims the ingestor refreshes it (feeds zero deployed panels
  today, but it is the ADR-0003 success metric). Add to a refresh owner or
  drop it deliberately.
- Matview staleness telemetry: a silent CronJob failure reproduces the
  2026-07-12 "fans not running" class. Add matview-age checks to
  db-watchdog / alert-monitor.
- `fn_lighting_minutes_policy` (7 s) / `v_lighting_traceability_now`
  (9.9 s): unbounded latest-per-parameter walk over all setpoint_changes
  chunks. Time-bound the CTEs — control-adjacent (services call it), so do
  it as its own change with bounded-query tests and runtime evidence.
- Materialize `v_water_attribution_daily` (~1 s × 5 concurrent panels,
  grows O(history)) — same migration-200/203 pattern.
- lab.verdify.ai plans index: 8 broken links (Jun 08–12 gap + tomorrow's
  date linked early; 2 crop hrefs). Fix in the publisher's index generator.
- `db/schema.sql` is stale (predates migrations 200/203–206) — regenerate.
- Optional: long `max-age` + `immutable` on lab's content-hashed static
  assets (currently no-store on everything; correct but zero-cache).
- Single-node DB failure domain (#218) made this incident cluster-visible;
  memory limits vs `statement_timeout=0` remains a standing risk. Consider
  a conservative default `statement_timeout` for the `verdify` role with
  explicit opt-outs for migrations/rollups.

## Panel sweep verdict (all 610 queries, executed bounded against prod)

Baseline mid-sprint: 514 ok / 94 empty / 2 errors / 0 timeouts (>3 s: 7).
Post-fix final sweep results in the sprint wrap; empties decompose into
(a) gated-cost panels — fixed via `*_est`, (b) equipment stripes that are
correctly empty when equipment is off (heaters in July), (c) fail-closed
evidence-tier panels on `site-evidence-dashboards` (by design until the
ADR-0004 decision), (d) dead data sources listed in the decisions above
(GPU, irrigation, plan compliance), (e) legit no-events windows (SLA
breaches, alerts).
