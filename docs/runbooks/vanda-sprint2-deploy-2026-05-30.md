# Vanda Sprint-2 — Deploy Runbook (2026-05-30)

Operator: Jason. Timezone: America/Denver (MDT). Branch: `firmware/vanda-band-compliance-rearch`.

This runbook covers the **sprint-2 deploy** — tunable-surface curation, the learning-loop
close, clamp observability, and the Vanda center-zone moisture-guardrail exemption. It is a
**successor** to `vanda-deploy-runbook-2026-05-30.md` (the band-compliance rearch runbook),
which already landed migration **145** (live) and the **2026.5.29 OTA** (`e7781a3` band bundle,
now baking). **Do not re-run that runbook's Steps 1 or 4.** Start here.

> Convention: the live DB is reached with
> `docker exec -i verdify-timescaledb psql -U verdify -d verdify`.
> Read-only checks use `-tAc "<SQL>"`. Migrations are piped through `-v ON_ERROR_STOP=1`.
> Service restarts are `sudo systemctl restart <unit>`.

Service-name map (verified against `systemd/`):

| Logical name | systemd unit |
|---|---|
| setpoint dispatcher | `verdify-setpoint-server` |
| MCP | `verdify-mcp` |
| ingestor | `verdify-ingestor` |

---

## STATE BEFORE YOU START (verified against the live DB, 2026-05-30)

- **Migration 145 = LIVE** (`fn_band_setpoints` present; Vanda curve, per-zone bands, season fix).
- **Migration 146 = NOT applied** (`daily_summary.compliance_v2_raw_pct` absent). Written + rollback-validated.
- **Migration 147 = NOT applied.** Written + rollback-validated. Phase-2 gate; applies only after 146 dual-writes ≥1 day.
- **Migration 148 = NOT applied** (`plan_journal.guardrail_penalty` absent; `v_plan_accuracy` returns **0 rows** — the dead facade this sprint repairs). New this sprint.
- **2026.5.29 OTA (`e7781a3` band bundle) = LIVE and baking.** This sprint's OTA (Step C) **supersedes it and RESETS the 48h bake** — operator override granted by the task.
- **last-good rollback target = `2026.5.17.1849.9353df5`** (mtime 2026-05-17 18:51) — UNCHANGED, your rollback floor.
- **Open critical/legacy-high alerts = 0** (re-confirm in Step 0).
- Sprint-2 staged OTA: `firmware/artifacts/2026.5.30.1012.e7781a3-vanda-center-guardrail/firmware.ota.bin`
  (sha256 `7838568db1293e209f134428a58c7fb17fab122e6748f4bd609a615ba218ebf2`, 1,063,008 bytes).
- Sprint-2 evidence bundle: `docs/runbooks/evidence/vanda-sprint2-ota/`.

**Nothing has been committed, pushed, deployed, or applied.** The branch is dirty with the
sprint-2 worktree edits; the operator owns commit + PR + apply + deploy.

### ⚠ THREE THINGS YOU MUST KNOW BEFORE YOU START

1. **Migration 148 has its OWN `BEGIN;` (line 55) / `COMMIT;` (line 173).** Do **NOT** chain it
   after 146/147 inside one outer `BEGIN..ROLLBACK` psql session — 148's inner `COMMIT` will
   prematurely commit the whole chain to prod. Apply 148 in **its own psql invocation** (Step A2),
   exactly as written, letting its own `BEGIN/COMMIT` own the transaction. (This bit us during
   evidence-gathering; the live DB was fully restored. Routes through coordinator: `db/migrations/**`.)

2. **The Step-C OTA resets the 48h bake** on the still-baking 2026.5.29 build. This is an
   explicit operator override granted by the task — the weekly-OTA limit and no-critical-alert
   gates remain HARD; only the 48h-bake gate is overridden, and must be re-armed after Step C.

3. **The Step-C replay-diff is RED-BY-DESIGN** at default `THRESHOLD_PCT=0` (2.54% vs `main`).
   100% of that divergence is the already-reviewed 2026.5.29 bundle; **this sprint's firmware
   change is 0 rows vs `e7781a3`** (no-op at the shipped `relax=0` default). You need coordinator
   + iris `THRESHOLD_PCT=3` concurrence before Step C.

---

## STEP 0 — Pre-flight (no mutation)

**0a. No open critical/legacy-high alerts** (hard gate; verified `0` at authoring time).
```bash
docker exec -i verdify-timescaledb psql -U verdify -d verdify -tAc \
  "SELECT count(*) FROM alert_log WHERE disposition IN ('open','acknowledged') AND resolved_at IS NULL AND severity IN ('critical','high')"
```
- **Expected:** `0` (same query `scripts/firmware-deploy-preflight.sh` runs).
- **If non-zero:** STOP. Triage first. Step C will self-abort; the DB/web steps have no such gate, so honor it manually.

**0b. Confirm the sprint-1 state is what this runbook assumes.**
```bash
docker exec -i verdify-timescaledb psql -U verdify -d verdify -tAc \
  "SELECT
     EXISTS(SELECT 1 FROM pg_proc WHERE proname='fn_band_setpoints')                                          AS mig145_live,
     EXISTS(SELECT 1 FROM information_schema.columns WHERE table_name='daily_summary' AND column_name='compliance_v2_raw_pct') AS mig146_applied,
     EXISTS(SELECT 1 FROM information_schema.columns WHERE table_name='plan_journal'  AND column_name='guardrail_penalty')      AS mig148_applied,
     (SELECT count(*) FROM v_plan_accuracy)                                                                    AS v_plan_accuracy_rows"
```
- **Expected:** `t | f | f | 0` (145 live; 146/148 not applied; accuracy facade dead). If 146 or 148 already shows `t`, do not re-apply that migration — skip to the next un-applied step.

**0c. Take a known-good schema restore point (DB rollback reference for Steps A).**
```bash
docker exec verdify-timescaledb pg_dump -U verdify -d verdify --schema-only \
  > /tmp/verdify-schema-pre-sprint2-$(date +%Y%m%d-%H%M).sql
```
- **Expected:** a non-empty `.sql`. This is your function/view rollback reference for Step A.

**Rollback for Step 0:** none (read-only + a dump).

---

## STEP A — DB migrations (146 dual-write + 148 accuracy/guardrail-penalty) — ATTENDED

Two migrations land here. **146 first** (additive compliance dual-write), **then 148** (accuracy
view repoint + `plan_journal.guardrail_penalty` column). 147 is deliberately **deferred to Step D**.

### A1 — Migration 146 (compliance dual-write) — ADDITIVE / SAFE

146 adds `compliance_v2_*` columns to `daily_summary`, the `daily_zone_compliance` /
`compliance_zone_weights` / `mv_zone_band_grade` objects, and the
`fn_compliance_v2`/`fn_zone_band_grade`/`fn_house_compliance` engine. **Binary reward columns are
NOT mutated** (the reward swap is 147 / Step D). Low-risk.

```bash
set -o pipefail
{ printf 'BEGIN;\n'; cat db/migrations/146-compliance-rearchitecture.sql; printf '\nCOMMIT;\n'; } \
  | docker exec -i verdify-timescaledb psql -U verdify -d verdify -v ON_ERROR_STOP=1 -q
```
- **Expected:** informational `NOTICE`s only, ends `COMMIT`, exit 0, zero `ERROR/FATAL`. 146's
  prerequisite guard (lines 41-52) `RAISE EXCEPTION`s if 145 is missing/incomplete — that is
  intended; if it fires, your live DB is not in the assumed state, STOP and re-check Step 0b.
- **If any ERROR:** the txn auto-aborts (ON_ERROR_STOP). Nothing committed; fix and retry.

### A2 — Migration 148 (accuracy repoint + guardrail_penalty column) — ADDITIVE / SAFE

148 repoints the dead `v_plan_compliance` / `v_plan_accuracy` / `v_plan_accuracy_72h` /
`v_plan_accuracy_by_day` family off the broken `setpoint_plan`-vs-climate join onto
`plan_journal.outcome_score` (restoring rows while preserving the Grafana column contract), and
adds `plan_journal.guardrail_penalty NUMERIC` (nullable) for the learning-loop UPDATE in
`mcp/server.py`.

**⚠ 148 OWNS ITS OWN TRANSACTION** (`BEGIN;` line 55 / `COMMIT;` line 173). Apply it **directly**,
NOT wrapped in an outer `BEGIN..COMMIT` — feed the file as-is so its own transaction boundary holds.
```bash
set -o pipefail
docker exec -i verdify-timescaledb psql -U verdify -d verdify -v ON_ERROR_STOP=1 -q \
  < db/migrations/148-plan-accuracy-repoint-plan-journal.sql
```
- **Expected:** `NOTICE`s only, the file's own `COMMIT` at the end, exit 0, zero `ERROR/FATAL`.
- **DO NOT** pipe `BEGIN; cat 148...; COMMIT;` — that double-wraps the transaction and the inner
  COMMIT commits early. Run it standalone exactly as above.

### A3 — Restart consumers (CLAUDE.md rule 7)

146 activates the ingestor dual-write + MCP scorecard rollup; 148's `guardrail_penalty` column is
read+written by `mcp/server.py` `plan_evaluate()` ($6 bind). Both `verdify-ingestor` and
`verdify-mcp` must bounce. **Restart MCP only AFTER 148 has committed** (else `plan_evaluate()`
errors on the missing column).
```bash
sudo systemctl restart verdify-ingestor
sudo systemctl restart verdify-mcp
systemctl status verdify-ingestor verdify-mcp --no-pager | head -12
```
- **Expected:** both `active (running)`, no crash loop in the first 30s.

### A4 — Verify

```bash
# (a) 146 dual-write columns exist; populate after one daily-summary cadence (~35 min).
docker exec -i verdify-timescaledb psql -U verdify -d verdify -c \
  "SELECT date, compliance_v2_raw_pct, compliance_v2_attributable_pct, unachievable_frac
   FROM daily_summary WHERE date = CURRENT_DATE"
docker exec -i verdify-timescaledb psql -U verdify -d verdify -c \
  "SELECT date, zone, zone_score FROM daily_zone_compliance WHERE date = CURRENT_DATE ORDER BY zone"

# (b) 148 accuracy facade now returns rows (was 0).
docker exec -i verdify-timescaledb psql -U verdify -d verdify -tAc "SELECT count(*) FROM v_plan_accuracy"          # expect ~231
docker exec -i verdify-timescaledb psql -U verdify -d verdify -tAc "SELECT count(*) FROM v_plan_accuracy_by_day"  # expect ~61

# (c) guardrail_penalty column present, and MCP is writing it on the next plan_evaluate.
docker exec -i verdify-timescaledb psql -U verdify -d verdify -tAc \
  "SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_name='plan_journal' AND column_name='guardrail_penalty')"  # expect t
docker exec -i verdify-timescaledb psql -U verdify -d verdify -c \
  "SELECT id, outcome_score, anchor_score, guardrail_penalty, validated_at
   FROM plan_journal WHERE validated_at > now() - interval '2 hours' ORDER BY validated_at DESC LIMIT 5"
```
- **Expected:** `v_plan_accuracy` > 0 immediately; `compliance_v2_*` non-NULL after one cycle;
  `guardrail_penalty` non-NULL on plans validated **after** the MCP restart (older rows stay NULL — no backfill).
- **If `v_plan_accuracy` still 0:** 148 did not take — re-check A2 committed.
- **If MCP errors on `plan_evaluate`:** the column landed but MCP started before 148 — restart `verdify-mcp` again.

**Step A is REVERSIBLE.**

| Migration | Revert |
|---|---|
| 146 | Additive. Simplest: leave the new objects (nothing reads them for reward yet), revert the ingestor to stop the dual-write. Full revert: restore prior `fn_compliance_pct` from the 0c dump, drop the new objects, restart `verdify-ingestor` + `verdify-mcp`. Binary cols never touched. |
| 148 | `DROP VIEW`s + restore prior `v_plan_compliance`/`v_plan_accuracy*` defs from the 0c dump, `ALTER TABLE plan_journal DROP COLUMN guardrail_penalty` (no backfill to unwind), restart `verdify-mcp`. The accuracy family returns to 0 rows (its prior dead state). |

---

## STEP B — Web / page / Grafana / Slack (clamp observability) — SAFE, NO CONTROL PATH

Makes clamps visible to **operators** (the planner already gets them each cycle via
`gather-plan-context.sh`). No firmware, no DB mutation. Most of this is publish-time + cron-time.

### B1 — Publish the AI-tunables page (clamp section + FORECAST_DEVIATION relabel)

`scripts/generate-ai-tunables-page.py` now renders a "Clamp Activity (last 30 days)" section, a
"Clamps 30d" column on the Parameter Index, and a data-driven FORECAST_DEVIATION row. Republish:
```bash
# Dry render first (read-only against live DB):
python scripts/generate-ai-tunables-page.py --stdout | head -60
# Then publish via the normal site-content path (regenerates reference/ai-tunables.md):
bash scripts/publish-site-content.sh   # or your standard publish entrypoint
```
- **Expected:** the dry render shows real clamp rows (e.g. `mister_engage_kpa ~681x req→applied,
  reason vpd_high_moisture_guardrail`; `fog_escalation_kpa ~472x`); FORECAST_DEVIATION row reads
  "configured; not currently firing (0 ledger fires in the last 14 days)". After publish,
  `reference/ai-tunables.md` carries today's frontmatter date.

### B2 — Confirm site-doctor freshness gate passes on the republished page

`scripts/site-doctor.py` now has `check_ai_tunables_freshness` (page exists, marker present,
frontmatter date ≤ 2 days stale).
```bash
python scripts/site-doctor.py 2>&1 | grep -iE 'ai-tunables|forecast'
```
- **Expected:** no `ai-tunables-page-stale`/`-missing`/`-marker-missing` findings (page just republished).

### B3 — Load the Grafana clamp panel

The Setpoint-Clamps row + "Clamped Setpoints (24h)" table (reading `v_clamp_activity_24h`) is in
`grafana/provisioning/dashboards/json/canonical-controller-reliability.json`. Grafana picks up
provisioned dashboards on its reload interval (`updateIntervalSeconds=300`); to apply immediately:
```bash
sudo systemctl restart grafana-server   # or: docker restart verdify-grafana — match your deploy
```
- **Expected:** within ~5 min the "Setpoint Clamps" row appears on the canonical-controller-reliability
  dashboard, table populated from `v_clamp_activity_24h`.
- **Verify the panel SQL read-only:**
```bash
docker exec -i verdify-timescaledb psql -U verdify -d verdify -c \
  "SELECT parameter, clamp_events FROM v_clamp_activity_24h ORDER BY clamp_events DESC LIMIT 10"
```

### B4 — Confirm the new Slack clamp-pressure alert

`scripts/alert-monitor.py` adds a `planner_clamp_pressure` check (warning if a PLANNER_PUSHABLE_REG
param is clamped >60×/hour). It takes effect on the next 5-min `alert-monitor` cron cycle — no action
needed. It only fires during an active stress/guardrail tug-of-war (0 when the last clamp burst is hours old).
```bash
# Confirm the trailing-hour clamp query (what the alert reads) is valid + see current pressure:
docker exec -i verdify-timescaledb psql -U verdify -d verdify -c \
  "SELECT parameter, count(*) FROM setpoint_clamps WHERE clamped_at > now() - interval '1 hour' GROUP BY 1 ORDER BY 2 DESC"
```
- **Expected:** valid output (often 0 rows off-stress; that is correct).

**Step B is REVERSIBLE.** Page: re-publish the prior `reference/ai-tunables.md` (or just let the
next publish regenerate). Grafana: revert the JSON + reload (no uid, not site-embedded, so no
iframe/staleness impact). Slack: revert `alert-monitor.py`; the alert auto-resolves via existing machinery.

---

## STEP C — Firmware OTA (Vanda center-zone moisture-guardrail) — LAST, GATED

Deploys the sprint-2 firmware: the **zone-3 center moisture-guardrail exemption**
(`center_engage_threshold_kpa`, `center_stressed`) + two new tunables
(`center_moisture_relax_kpa` / `center_moisture_min_excess_kpa`) with `cfg_*` readbacks +
invariant #21. **This supersedes the still-baking 2026.5.29 build and RESETS the 48h bake
(operator override granted).**

### C1 — GATE CHECKLIST (all must hold before deploy)

| Gate | How to confirm | Required state |
|---|---|---|
| No open critical/legacy-high alerts | Step 0a query (also run by preflight) | `0` (HARD) |
| ≤ 1 OTA / calendar week | weekly counter resets Monday 00:00 MDT | HARD |
| 48h bake on last-good | **OVERRIDDEN** — supersedes the 2026.5.29 bake | operator override (granted) |
| Invariants green | `docs/runbooks/evidence/vanda-sprint2-ota/firmware-invariants.txt` | 0 violations / 193,525 rows, incl. #21 |
| Unit tests green | `.../test-firmware.txt` | 192 / 0 (was 178 on main; +14) |
| Replay-diff sign-off | characterized below | `THRESHOLD_PCT=3` + coordinator/iris concurrence |

**Replay-diff is RED-BY-DESIGN at `THRESHOLD_PCT=0`.** Characterization:
`worktree vs main = 2.54%` (all of it the already-reviewed 2026.5.29 bundle) vs
`worktree vs e7781a3 = 0.00%` (this sprint's center change is a replay no-op at the shipped
`relax=0` default). Re-confirm with the override:
```bash
make firmware-replay-worktree OLD=main THRESHOLD_PCT=3
```
- **Expected:** 2.54% < 3% → gate passes. Basis:
  `docs/runbooks/evidence/vanda-sprint2-ota/replay-diff-characterization.txt`.

> Stress-window note (CLAUDE.md rule 5): if forecast max next 24h > 85°F, preflight prints it as
> context and does NOT block. The no-critical-alert and weekly-OTA gates remain HARD.

### C2 — Deploy

The deploy re-compiles from the dirty worktree, stamps the real `fw_version`, OTAs the ESP32,
waits ~60s, runs the post-deploy sensor-health sweep, and **auto-rolls-back to `last-good.ota.bin`
(2026.5.17.1849.9353df5) on failure.** The dirty-deploy + 48h-bake override needs explicit flags +
a documented reason.
```bash
make firmware-deploy \
  ALLOW_DIRTY_FIRMWARE_DEPLOY=1 \
  FIRMWARE_DEPLOY_OPERATOR_SIGNOFF=1 \
  FIRMWARE_DEPLOY_OVERRIDE_REASON="Vanda sprint-2 center-zone moisture-guardrail exemption; supersedes 2026.5.29 e7781a3 build (48h-bake reset, operator override granted); coordinator+iris THRESHOLD_PCT=3 sign-off; orchestrator-staged uncommitted branch"
```
- **Expected (success):** preflight `✓ No unresolved critical/legacy-high alerts`,
  `✓ Weekly OTA limit clear`; compile `[SUCCESS]`; OTA upload completes;
  `wait-for-firmware-version.sh` confirms the new `fw_version` reporting in;
  `sensor-health SINCE='5 minutes'` passes → `✓ Deploy accepted.`
  Sanity-check the compiled flash size ≈ **1,063,008 bytes / Flash 57.9%** against the staged binary
  (sha256 `7838568d…ebf2`).
- **Expected (failure):** sensor-health fails → `▓▓▓ SENSOR-HEALTH FAILED POST-OTA — initiating
  auto-rollback ▓▓▓`, re-flashes `last-good`, re-checks, exits non-zero. **No operator action needed
  for the auto-rollback;** triage the sensor-health output before re-attempting.

> If the 48h-bake gate still blocks despite the override flags, it is enforced separately in
> `scripts/firmware-deploy-preflight.sh` against `last-good.ota.bin` mtime — confirm the override
> env var the preflight honors and re-run; the override is granted by the task for this build.

### C3 — Post-deploy confirmation

```bash
# new fw_version reporting in:
docker exec -i verdify-timescaledb psql -U verdify -d verdify -tAc \
  "SELECT DISTINCT fw_version FROM climate_action_log WHERE ts > now() - interval '10 min'"

# cfg_* readbacks resolve (NAN until cfg_first_pull_ok, then the shipped defaults):
#   cfg_center_moisture_relax_kpa  → 0.00 (no-op default)
#   cfg_center_moisture_min_excess_kpa → 0.05 (operator floor)
# Confirm via HA/ESPHome sensor state or the diagnostic dashboard.

# Watch the center-zone misting + that the moisture guardrail no longer over-vetoes Iris:
docker exec -i verdify-timescaledb psql -U verdify -d verdify -c \
  "SELECT parameter, count(*) FROM setpoint_clamps
   WHERE clamped_at > now() - interval '6 hours' AND reason='vpd_high_moisture_guardrail'
   GROUP BY 1 ORDER BY 2 DESC"
```
- **Expected:** the new `fw_version` reporting in; `cfg_*` readbacks resolve to defaults. The
  center-exemption is a **no-op at relax=0** — center misting behavior is unchanged until the
  dispatcher ramps `center_moisture_relax_kpa > 0` (gated by operator setpoint review + the
  coordinator registry-registration blocker, below). So at this step you are confirming the binary
  is live and bounded, not a behavior change yet.

**Step C is REVERSIBLE.** Auto-rollback on a failed sweep. Manual rollback any time:
```bash
make firmware-rollback   # flashes last-good (2026.5.17.1849.9353df5) back onto the ESP32
```
Do NOT promote this build to last-good until it bakes 48h clean
(`make firmware-promote-last-good` later, explicitly).

> **BLOCKER for the relax behavior to actually engage (coordinator + firmware + genai):** the two
> new tunables are NOT yet in `verdify_schemas/tunable_registry.py`, so (1) the planner cannot push
> `center_moisture_relax_kpa`, and (2) the drift guard
> `test_cfg_readback_sensors_are_routed_to_entity_map` FAILS until the registry declares the
> `cfg_readback_object_id` routes. This is a coordinator-routed schema PR (bounds 0/0.6 + 0/0.3
> matching `firmware/greenhouse/tunables.yaml`, name `center_moisture_relax_kpa` in the Iris prompt
> to keep the prompt-coverage gate green). Also pending: the dispatcher relaxation in
> `ingestor/tasks.py` (1B) so the planner's aggressive values reach the firmware. **The OTA is safe
> to deploy without these** (it ships no-op), but the Vanda benefit does not land until they do.

---

## STEP D — Migration 147 (reward swap + ladder re-anchor) — ONLY AFTER 146 DUAL-WRITES ≥1 DAY

**Do NOT apply 147 until Step A4(a) shows `compliance_v2_*` populated for at least one full day of
history.** 147 re-points the planner reward (`fn_plan_anchor_score`, `v_planner_performance`,
`v_daily_kpi`) onto controller-attributable graded compliance and re-anchors the plan ladder by
quantile match. Without 146's dual-written graded history the re-anchor falls back to binary
(`binary_fallback`) and the ≥90% ordinal-stability acceptance cannot be met (the dry-run replay
showed 51.7% fallback precisely because no graded history existed yet).

**D1 — Confirm ≥1 day of graded history first.**
```bash
docker exec -i verdify-timescaledb psql -U verdify -d verdify -tAc \
  "SELECT count(*) FROM daily_summary WHERE compliance_v2_attributable_pct IS NOT NULL AND date < CURRENT_DATE"
```
- **Expected:** ≥ 1. If 0, wait — 146 needs to accumulate a full day. Do not proceed.

**D2 — Apply 147.**
```bash
set -o pipefail
{ printf 'BEGIN;\n'; cat db/migrations/147-reward-swap-and-ladder-reanchor.sql; printf '\nCOMMIT;\n'; } \
  | docker exec -i verdify-timescaledb psql -U verdify -d verdify -v ON_ERROR_STOP=1 -q
```
- **Expected:** `NOTICE`s (ladder re-anchor, ordinal stability), ends `COMMIT`, exit 0. 147's 147.0
  guard FATALs if 146 didn't land — intended.
- (147 uses a normal outer-`BEGIN` wrap; only **148** owns its own transaction.)

**D3 — Restart consumers (rule 7).**
```bash
sudo systemctl restart verdify-mcp
sudo systemctl restart verdify-ingestor
```

**D4 — Verify ordinal stability ≥ 0.90 on dual-written days.**
```bash
docker exec -i verdify-timescaledb psql -U verdify -d verdify -c \
  "SELECT * FROM plan_anchor_ladder ORDER BY cut_label"   # graded cuts populated, monotone, non-NULL comp_cut_graded
# Then run the documented acceptance query (the 'reviewer query' block at the bottom of 147-...sql)
# and confirm the ordinal-stability fraction is >= 0.90 over days with *_v2 history.
```
- **Expected:** acceptance fraction **≥ 0.90**. The 51.7% from the dry run was the empty-history
  replay, not this live measurement.
- **If < 90%:** do NOT let the new reward drive planning. Roll back 147 (below), let 146 dual-write
  more graded days, retry.

**Step D is REVERSIBLE.** Restore prior `fn_plan_anchor_score`/`v_planner_performance`/`v_daily_kpi`
from the 0c dump, `DROP TABLE plan_anchor_ladder`, restart `verdify-mcp` + `verdify-ingestor`. The
binary reward columns are still present this cycle, so the reward returns to binary cleanly.

---

## REVERSIBILITY SUMMARY

| Step | Reversible? | How |
|---|---|---|
| 0 | n/a | read-only + schema dump |
| A1 (mig 146) | Yes (additive) | revert ingestor to stop dual-write, or restore `fn_compliance_pct` + drop new objects; binary cols untouched |
| A2 (mig 148) | Yes (additive) | restore prior accuracy view defs from 0c dump, `DROP COLUMN plan_journal.guardrail_penalty`, restart `verdify-mcp` |
| B (web/grafana/slack) | Yes | re-publish prior page / revert JSON + reload / revert alert-monitor (auto-resolves) |
| C (firmware OTA) | Yes | auto-rollback on failed sweep; `make firmware-rollback` to last-good (2026.5.17.1849.9353df5) |
| D (mig 147) | Yes | restore prior reward fn/views, drop `plan_anchor_ladder`; binary reward cols still present this cycle |

## DO-NOT list

- **Do NOT** chain migration 148 inside an outer `BEGIN..COMMIT`/`ROLLBACK` — it owns its own
  transaction (inner `COMMIT` at line 173). Apply it standalone (Step A2).
- **Do NOT** apply 147 (Step D) before 146 has dual-written ≥1 day of graded history (D1) — its guard FATALs and the re-anchor falls to binary.
- **Do NOT** restart `verdify-mcp` after 146 but before 148 commits — `plan_evaluate()` will error on the missing `guardrail_penalty` column (A3 ordering).
- **Do NOT** promote the Step-C build to last-good until its 48h bake completes clean.
- **Do NOT** force past a `RAISE EXCEPTION` guard or a preflight `✗` gate — they encode the ordering above.

## CLAUDE.md / shared-territory routing for the PR body

- Touches shared territory → **route through coordinator (Jason)**: `verdify_schemas/**`,
  `db/migrations/**` (146/147/148, serialized — one migration PR sequence), `grafana/**`,
  `.github/**` (the new CI gate), `docs/backlog/**`.
- **Rule 7 (restart docs):** `mcp/server.py` + schema touches → PR body must list **verdify-mcp**
  and **verdify-ingestor** restarts post-merge, with the **148-before-MCP-restart** ordering called out.
- **Rule 6 (cfg_* readback):** the two new tunables carry firmware `cfg_*` readbacks (done); the
  **registry-side** registration is the open coordinator blocker (Step C note).
- **Rule 8 (replay-diff):** Step C carries the characterized 2.54% (vs main) / 0.00% (vs e7781a3)
  replay-diff; `THRESHOLD_PCT=3` override requires coordinator + iris concurrence.
