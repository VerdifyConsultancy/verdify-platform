# Backlog Close-Out — Deploy Runbook (2026-05-30)

Operator: Jason. Timezone: America/Denver (MDT). Branch: `firmware/vanda-band-compliance-rearch`.

This runbook covers the **software backlog close-out** sprint: the graded/feasibility
compliance rearchitecture (migration 146), the observability + KPI + recipe migrations
(149, 150), the firmware OTA bundle (`...f2bad50-backlog-closeout`), the genai/web/schemas/
Slack copy + consumer changes, and the **staged-only** reward swap (migration 147).

It is a **successor** to `vanda-sprint2-deploy-2026-05-30.md`. Migrations **145** and **148**
are already **LIVE** (re-verified below). **This runbook applies 146, 149, 150 (NOT 147),
deploys one firmware OTA, and restarts the consuming services.**

> Convention: the live DB is reached with
> `docker exec -i verdify-timescaledb psql -U verdify -d verdify`.
> Read-only checks use `-tAc "<SQL>"`. Service restarts are `sudo systemctl restart <unit>`.
> **The migration files are NOT on the container filesystem** — pipe them in via stdin
> (`cat <file> | docker exec -i ... psql ...`), do not use `\i`.

Service-name map (verified against `systemd/`):

| Logical name | systemd unit |
|---|---|
| setpoint dispatcher | `verdify-setpoint-server` |
| MCP | `verdify-mcp` |
| ingestor | `verdify-ingestor` |
| public API | `verdify-api` |

---

## STATE BEFORE YOU START (verified against the live DB, 2026-05-30)

- **Migration 145 = LIVE** (`fn_band_setpoints`/`fn_zone_band`/`fn_current_season` present;
  `fn_current_season` is STABLE `provolatile='s'`). Vanda smooth band, per-zone bands, season fix.
- **Migration 148 = LIVE** (`plan_journal.guardrail_penalty` present; `v_plan_accuracy` repointed).
- **Migration 146 = NOT applied** (`daily_summary.compliance_v2_attributable_pct` absent;
  `fn_compliance_v2`/`fn_grade_credit`/`fn_house_compliance`/`fn_zone_band_grade` = 0;
  `daily_zone_compliance` table absent). Written + rollback-validated.
- **Migration 149 = NOT applied** (`v_open_alerts` absent; `v_zone_disease_risk` absent;
  `v_setpoint_compliance` still PRESENT). New this sprint.
- **Migration 150 = NOT applied** (`nutrient_recipes.salt_model` absent). New this sprint.
- **Migration 147 = NOT applied AND NOT TO BE APPLIED THIS SPRINT.** Staged only.
  Its `≥90%` ladder ordinal-stability gate is calibrated against a false premise (the live
  `fn_plan_anchor_score` reproduces only 50.9% of the frozen anchors); coordinator must
  re-baseline the acceptance criterion before it can ever apply. 147 also self-aborts at a
  146-prerequisite RAISE guard until 146 has dual-written.
- **Open `severity='critical'` or legacy-`high` alerts = 0** (only 65 `warning` rows open).
  Deploy gate rule 1 currently PASSES — **re-confirm in Step 0.**
- **last-good rollback target = `2026.5.17.1849.9353df5`** (`firmware/artifacts/last-good.ota.bin`
  mtime 2026-05-17 18:51) — UNCHANGED, your rollback floor. The 48h-bake mtime gate (rule 3)
  is satisfied by construction.
- **Live firmware = `2026.5.30.1155.f2bad50`** (`firmware/artifacts/pending-fw-version.txt`).
- Staged OTA this sprint: `firmware/artifacts/2026.5.30.1314.f2bad50-backlog-closeout/firmware.ota.bin`
  (sha256 `1b08543b3355eabbfcf22dc4061bb643380c32280efc8d176f844ca052675ce6`, 1,074,368 bytes).
  `source_dirty=1` — **commit the firmware source before/with deploy** so `source_sha` is
  reproducible (see Step D.0).
- Evidence bundle: `docs/runbooks/evidence/backlog-closeout-ota/`.

**Nothing has been committed, pushed, deployed, or applied.** The branch is dirty with the
backlog close-out worktree edits across all groups; the operator owns commit + PR + apply + deploy.

> **Migration transaction shapes (validated):**
> - **146 / 147** are NON-self-transactional (only DO-block `BEGIN`s, no top-level `COMMIT`).
>   To apply, pipe through `-v ON_ERROR_STOP=1` — each runs in psql's implicit single-statement
>   autocommit, but the DO blocks are atomic. To **rollback-validate**, wrap in an outer
>   `BEGIN; … ROLLBACK;`.
> - **149 / 150** are SELF-transactional (own top-level `BEGIN;`/`COMMIT;`). Apply them as-is;
>   to rollback-validate, swap the trailing `COMMIT;` for `ROLLBACK;`.

---

## STEP 0 — Pre-flight gate (re-confirm immediately before any apply/deploy)

**Command:**
```bash
docker exec verdify-timescaledb psql -U verdify -d verdify -tAc \
  "SELECT severity, count(*) FROM alert_log WHERE resolved_at IS NULL AND severity IN ('critical','high') GROUP BY severity;"
```
**Expected:** zero rows (0 critical/high open). This is the hard deploy gate (CLAUDE.md rule 1)
for **Step D only** — it does NOT block DB migrations. If any `critical`/`high` row is open,
**stop the firmware OTA (Step D)**; migrations A/B/C may still proceed (off control path).

**Rollback:** N/A (read-only).

---

## STEP A — Apply migration 146 ALONE (graded compliance, additive dual-write)

146 is additive: it adds `*_v2` columns, the `daily_zone_compliance`/`compliance_zone_weights`
tables, `fn_grade_credit`/`fn_compliance_v2`/`fn_house_compliance`/`fn_zone_band_grade`/
`fn_compliance_pct` shim/`mv_zone_band_grade`, seeds `compliance_zone_weights`, and DROPs the
retired `v_setpoint_compliance`. It does NOT move the reward and does NOT drop `v_target_curve`
or `v_plan_accuracy`/`v_plan_compliance`.

**A.1 — Apply (146 is NOT self-transactional; pipe with ON_ERROR_STOP):**
```bash
cat db/migrations/146-compliance-rearchitecture.sql \
  | docker exec -i verdify-timescaledb psql -U verdify -d verdify -v ON_ERROR_STOP=1
```
**Expected:** completes with no `ERROR`/`FATAL`. Benign `NOTICE`s are expected
(`fn_target_band_smooth`/`fn_target_band` already dropped by 145/D8; the 145-guard passes since
orchid `stress_high=100` is live). rc=0.

**A.2 — Restart consumers (CLAUDE.md rule 7: 146 touches the compliance surface ingestor writes
and mcp reads):**
```bash
sudo systemctl restart verdify-ingestor verdify-mcp
```

**A.3 — Verify the v2 surface + that ingestor begins populating it:**
```bash
docker exec verdify-timescaledb psql -U verdify -d verdify -tAc \
  "SELECT count(*) FROM pg_proc WHERE proname IN ('fn_grade_credit','fn_compliance_v2','fn_house_compliance','fn_zone_band_grade');"   # expect 4
docker exec verdify-timescaledb psql -U verdify -d verdify -tAc \
  "SELECT count(*) FROM information_schema.columns WHERE table_name='daily_summary' AND column_name='compliance_v2_attributable_pct';"  # expect 1
docker exec verdify-timescaledb psql -U verdify -d verdify -tAc \
  "SELECT zone, weight FROM compliance_zone_weights ORDER BY zone;"   # expect center 0.60 / east 0.40 / north,south,west 0
docker exec verdify-timescaledb psql -U verdify -d verdify -tAc \
  "SELECT * FROM fn_house_compliance('6h');"                          # ctrl >= raw, unachievable_frac present
docker exec verdify-timescaledb psql -U verdify -d verdify -tAc \
  "SELECT count(*) FROM information_schema.views WHERE table_name='v_setpoint_compliance';"  # expect 0 (dropped)
```
Then confirm **`daily_zone_compliance` populates** on the next ingestor daily-summary cycle:
```bash
docker exec verdify-timescaledb psql -U verdify -d verdify -tAc \
  "SELECT date, count(*) FROM daily_zone_compliance GROUP BY date ORDER BY date DESC LIMIT 3;"
```
(Empty until the ingestor dual-write loop runs; force a manual snapshot run or wait for the
nightly cron. The dual-write is guarded so it no-ops cleanly until columns exist — which they
now do.)

**Rollback (A):** 146 has no down-migration. To revert the new objects:
```sql
BEGIN;
DROP MATERIALIZED VIEW IF EXISTS mv_zone_band_grade;
DROP TABLE IF EXISTS daily_zone_compliance;
DROP TABLE IF EXISTS compliance_zone_weights;
DROP FUNCTION IF EXISTS fn_zone_band_grade(timestamptz,timestamptz,text);
DROP FUNCTION IF EXISTS fn_compliance_v2(interval);
DROP FUNCTION IF EXISTS fn_house_compliance(interval);
DROP FUNCTION IF EXISTS fn_grade_credit(numeric,numeric,numeric,numeric,numeric);
-- The added daily_summary *_v2 columns are additive/NULL and safe to leave; drop only if required.
COMMIT;  -- review before running; swap to ROLLBACK to dry-run
```
Note: dropping `v_setpoint_compliance` is not reversible from 146 alone — restore it from 145/earlier
DDL if a consumer still needs it (none do; it is retired).

---

## STEP B — Apply migration 149 (M10/M5/M11/M12) ALONE

149 = setpoint_snapshot compression (M10) + canonical `v_open_alerts` (M5) +
`DROP VIEW IF EXISTS v_setpoint_compliance` (M11, idempotent no-op after 146) +
per-zone `v_zone_disease_risk` (M12). Self-transactional.

**B.1 — Apply (149 is self-transactional; apply as-is):**
```bash
cat db/migrations/149-compress-snapshot-open-alerts-zone-kpis.sql \
  | docker exec -i verdify-timescaledb psql -U verdify -d verdify -v ON_ERROR_STOP=1
```
**Expected:** ends `COMMIT`. Benign `NOTICE: view "v_setpoint_compliance" does not exist, skipping`
(already dropped by 146) — idempotent, harmless. rc=0.

**B.2 — Restart:** none required for 149 alone (no `verdify_schemas`/`entity_map`/`mcp` schema
change; the compression policy is a TimescaleDB background job; consumers still use inline
`disposition='open'` this sprint and are repointed to `v_open_alerts` only in Step C's follow-on).

**B.3 — Verify:**
```bash
docker exec verdify-timescaledb psql -U verdify -d verdify -tAc \
  "SELECT count(*) FROM v_open_alerts;"                                  # acknowledged-but-unresolved rows; 0 suppressed leak
docker exec verdify-timescaledb psql -U verdify -d verdify -tAc \
  "SELECT count(DISTINCT zone) FROM v_zone_disease_risk;"               # expect 5 zones
docker exec verdify-timescaledb psql -U verdify -d verdify -tAc \
  "SELECT count(*) FROM information_schema.views WHERE table_name='v_setpoint_compliance';"  # expect 0
docker exec verdify-timescaledb psql -U verdify -d verdify -tAc \
  "SELECT count(*) FROM timescaledb_information.jobs WHERE proc_name='policy_compression' AND hypertable_name='setpoint_snapshot';"  # expect >=1
```

**Rollback (B):** re-run the file with the trailing `COMMIT;` swapped to `ROLLBACK;`, or:
```sql
BEGIN;
DROP VIEW IF EXISTS v_zone_disease_risk;
DROP VIEW IF EXISTS v_open_alerts;
SELECT remove_compression_policy('setpoint_snapshot', if_exists => true);
ALTER TABLE setpoint_snapshot SET (timescaledb.compress = false);
COMMIT;
```

---

### Also apply migration 150 (N1 DB half) — nutrient recipe label

150 adds `nutrient_recipes.salt_model`/`product_name` and labels the already-live
`vanda_orchid_active` row. Self-transactional; `is_active` stays FALSE so no planner behavior
changes. Apply alongside B (independent of 146/149).

**Apply:**
```bash
cat db/migrations/150-vanda-nutrient-recipe.sql \
  | docker exec -i verdify-timescaledb psql -U verdify -d verdify -v ON_ERROR_STOP=1
```
**Expected:** ends `COMMIT`; columns added; `vanda_orchid_active` labeled
`salt_model='single_salt'`, `product_name='MSU 13-3-15'` with 145's chemistry (N=50/P=11.5/K=57.7/
ec=0.40) preserved; 7 legacy rows keep NULL `salt_model`. rc=0.

**Verify:**
```bash
docker exec verdify-timescaledb psql -U verdify -d verdify -tAc \
  "SELECT name, salt_model, product_name, target_ec, is_active FROM nutrient_recipes WHERE name='vanda_orchid_active';"
```
**Restart:** none for the table label itself (`is_active=FALSE`). The `NutrientRecipe` schema
field PR (schemas group) lands separately; bounce `verdify-mcp` when the genai consumer SELECT
adds `salt_model`/`product_name` (Step C).

**Rollback:** re-run with `COMMIT;`→`ROLLBACK;`, or
`ALTER TABLE nutrient_recipes DROP COLUMN IF EXISTS salt_model, DROP COLUMN IF EXISTS product_name;`.

---

## STEP C — genai / web / schemas / Slack code changes + restarts

These are repo code changes (already in the dirty worktree), landed by the operator's
commit/merge, then services bounced. None are on the firmware control path.

**C.1 — Restart MCP** (mcp/server.py: scorecard docstring, crops-get recipe enrichment, set_plan
band-param capture; verdify_schemas/** tunable_registry + climate_intent changes — rule 7):
```bash
sudo systemctl restart verdify-mcp
```
**C.2 — Restart ingestor** (iris_planner.py prompt copy; tasks.py dual-write + M1/M3/M5; alert-monitor
runs on its own cron; entity_map/tunable enum reload — rule 7):
```bash
sudo systemctl restart verdify-ingestor
```
**C.3 — Restart API** (api/main.py evidence-snapshot now emits `graded_compliance_attributable_pct`):
```bash
sudo systemctl restart verdify-api
```
**C.4 — Publish pages (operator, writes the vault):**
```bash
python3 scripts/generate-baseline-vs-iris-page.py    # graded section auto-surfaces now 146 is live
python3 scripts/update-evidence-snapshots.py
bash   scripts/export-public-sample-dataset.sh <out-dir>
python3 scripts/populate-site-content.py             # refreshes soil.md RAG row (W1)
# then the site publish step (publish-site-content.sh) per the web group's handoff
```
**C.5 — Grafana reload (COORDINATOR / shared territory):** the M12 `ipm.json` per-zone panels
(ids 70/71) must be coordinator-reviewed before provisioning to live Grafana, then reload
provisioning. Optionally repoint them to the `v_zone_disease_risk` graded surface post-149.

**C.6 — Repoint open-alert consumers to `v_open_alerts` (follow-on, optional this sprint):**
api/main.py, ingestor/iris_planner.py, scripts/alert-monitor.py, ingestor/tasks.py still use inline
`disposition='open'`. When repointed, bounce the touched service. Safe to defer.

**Expected:** services restart clean (`systemctl status` active); `make planner-dry` parity holds;
public pages render graded columns; scorecard endpoint does not 500 on graded keys.

**Rollback (C):** revert the code commit and re-restart the three services; pages regenerate from
the prior code on next publish.

---

## STEP D — Firmware OTA of the backlog-closeout bundle

Bundle: `firmware/artifacts/2026.5.30.1314.f2bad50-backlog-closeout/firmware.ota.bin`
(sha256 `1b08543b3355eabbfcf22dc4061bb643380c32280efc8d176f844ca052675ce6`).
Changes: F1/F3 feed-window, SF1 sensor_degraded VPD-suppression, M2 CO2 transform, M14 min-dark,
NB7 overnight micro-pulse. New tunables all have `cfg_*` readbacks (rule 6, verified).

**D.0 — Commit the firmware source first** (bundle is `source_dirty=1`):
the deployed binary's `source_sha` must be reproducible. Operator commits the firmware-lane
files (greenhouse_logic.h, greenhouse_types.h, the four greenhouse/*.yaml, the test files) on the
branch before/with deploy. **This runbook does not commit.**

**D.1 — Deploy gate checklist (all must hold):**
- [ ] **rule 1:** Step 0 shows 0 `critical`/`high` open alerts. (Currently PASS — re-confirm.)
- [ ] **rule 3 (48h bake):** `firmware/artifacts/last-good.ota.bin` mtime is 2026-05-17 — far
      beyond 48h. PASS.
- [ ] **rule 2 (≤1 OTA/week):** the sprint-2 `e7781a3-vanda-center-guardrail` bundle may have been
      deployed this calendar week. **If so, this OTA needs the documented operator override**
      (one OTA/week limit). Record the override + reason in the PR body. The task grants the
      operator override for the weekly limit.
- [ ] **rule 8 (replay-diff + THRESHOLD_PCT):** `make firmware-replay-worktree OLD=f2bad50`
      reported **0 / 193,525 divergent rows** (THRESHOLD_PCT=0 satisfied). New paths are gated by
      conditions inert on the historical corpus — coordinator sign-off that 0% is expected
      (replay-diff-characterization.txt explains the corpus blind-spots for the new gated paths).
- [ ] **invariants green:** `make firmware-invariants` PASS over 193,525 rows incl. new #23
      (min-dark) and #24 (overnight micro-pulse).

**D.2 — Deploy:**
```bash
make firmware-deploy   # runs the preflight (alerts / bake-mtime / weekly) then pushes the OTA
```
If the preflight aborts on the weekly limit, re-run with the documented operator override per
`make firmware-deploy` usage. Promote last-good only AFTER the 48h bake passes — **do not
PROMOTE_LAST_GOOD at deploy.**

**D.3 — Sensor-health sweep** (the bake definition, CLAUDE.md rule 3):
run/observe the sensor-health sweep; the 48h bake starts now and must complete with no
`severity='critical'` alert before this binary can be promoted to last-good.

**Expected:** ESP32 reboots into `2026.5.30.1314.f2bad50-backlog-closeout`, telemetry resumes,
no boot-loop (the new M7 alert would fire), `sensor_degraded_active`/`co2_plausible_flag`/
`micropulse_count_today` diagnostics appear.

**Rollback (D):** re-flash `firmware/artifacts/last-good.ota.bin`
(`2026.5.17.1849.9353df5`) via `make firmware-deploy` pointed at last-good, or the OTA
rollback path. last-good is intentionally NOT advanced by this deploy.

---

## STEP E — Migration 147 (reward swap) — STAGED, DO NOT APPLY THIS SPRINT

147 repoints the planner reward from binary `compliance_pct` to
`compliance_v2_attributable_pct` and re-anchors the plan ladder to `comp_cut_graded`.

**147 is NOT deployed this sprint.** Apply only after BOTH:
1. **146 has dual-written graded history for ≥1 full day** (so `compliance_v2_*` columns are
   populated, not the `binary_fallback` path), AND
2. **The 90% anchor-stability gate is re-baselined by coordinator.** As written, 147's verify
   reports ~52% ordinal stability — a **non-failing RAISE NOTICE** — because the *live*
   `fn_plan_anchor_score` already reproduces only 50.9% of the frozen anchors. The `≥90%`
   absolute-reproduction premise is false; coordinator must re-baseline to a graded-vs-live-binary
   reproduction **delta** before applying. 147 also self-aborts at a 146-prerequisite RAISE guard
   until 146 is live (so it is sequencing-safe but must not be forced).

When (later sprint) both conditions hold:
```bash
cat db/migrations/147-reward-swap-and-ladder-reanchor.sql \
  | docker exec -i verdify-timescaledb psql -U verdify -d verdify -v ON_ERROR_STOP=1
sudo systemctl restart verdify-ingestor verdify-mcp   # reward source moved; rule 7
```
**Rollback:** repoint `v_daily_kpi`/`v_planner_performance`/`fn_plan_anchor_score` back to the
binary source and restore the prior `plan_anchor_ladder` derivation (keep the pre-147 DDL handy).

---

## Order summary

1. **Step 0** gate (re-confirm 0 critical/high).
2. **Step A** — apply 146 alone → restart ingestor+mcp → verify v2 + daily_zone_compliance.
3. **Step B** — apply 149 alone (+ 150) → verify.
4. **Step C** — land genai/web/schemas/Slack code → restart mcp/ingestor/api → publish pages →
   coordinator Grafana reload.
5. **Step D** — firmware OTA (gate checklist; operator override for weekly limit) → sensor-health
   sweep / 48h bake.
6. **Step E** — 147 STAGED; NOT applied this sprint.

**Nothing in this runbook has been executed.** It documents the operator-driven sequence;
the operator owns commit, PR, merge, apply, restart, deploy.
