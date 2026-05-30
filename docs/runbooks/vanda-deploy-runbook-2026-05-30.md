# Vanda Band-Compliance Rearchitecture — Deploy Runbook (2026-05-30)

Operator: Jason. Timezone: America/Denver (MDT). Branch: `firmware/vanda-band-compliance-rearch`.

This is the ordered, turnkey deploy sequence. **Do the steps in order.** Each migration is
a hard prerequisite for the next; the firmware OTA (Step 4) is independent of the DB curve
(Step 1) but should be done last so its replay-diff sign-off is fresh.

> Convention used throughout: the live DB is reached with
> `docker exec -i verdify-timescaledb psql -U verdify -d verdify`.
> Read-only checks use `-tAc "<SQL>"`. Migrations are piped through `-v ON_ERROR_STOP=1`.
> Service restarts are `sudo systemctl restart <unit>` (units live in `systemd/`).

Service-name map (verified against `systemd/`):
| Logical name in this runbook | systemd unit |
|---|---|
| setpoint dispatcher | `verdify-setpoint-server` |
| MCP | `verdify-mcp` |
| ingestor | `verdify-ingestor` |

---

## STATE BEFORE YOU START (what is staged vs. what you do)

- All three migrations (145/146/147) are written, lint-clean, and replay only inside a single
  `BEGIN..ROLLBACK` (never committed to live). **Nothing is applied to prod.**
- The firmware OTA binary is **staged but NOT deployed and NOT promoted to last-good**:
  `firmware/artifacts/2026.5.29.2232.fb17f43-vanda-band/firmware.ota.bin`
  (sha256 `9ae13495f0d4de2e16427565c217c42bf6fd402b7c695e9f8622c7e4239e6cbc`, 1,061,616 bytes).
- `last-good.ota.bin` is still `2026.5.17.1849.9353df5` (mtime May 17) — your rollback target.
- Evidence bundle: `docs/runbooks/evidence/vanda-band-compliance-ota/`.

**Two things need YOU specifically before Step 4 can run:**
1. Coordinator/iris `THRESHOLD_PCT` sign-off for the characterized 2.54% firmware replay
   divergence (recommend `THRESHOLD_PCT=3`).
2. An attended apply of the DB curve (Step 1) — the night-VPD raise changes overnight heat
   firing until the firmware guard from Step 4 lands; watch it.

---

## STEP 0 — Pre-flight (no mutation)

**0a. No open critical/legacy-high alerts.** (Hard gate; verified clear tonight, re-confirm in the morning.)

```bash
docker exec -i verdify-timescaledb psql -U verdify -d verdify -tAc \
  "SELECT count(*) FROM alert_log WHERE disposition IN ('open','acknowledged') AND resolved_at IS NULL AND severity IN ('critical','high')"
```
- **Expected:** `0`. (This is the same query `scripts/firmware-deploy-preflight.sh` runs.)
- **If non-zero:** STOP. Resolve/triage the alert first. Do not proceed to any step. Firmware
  OTA (Step 4) will self-abort on this in preflight; the DB steps have no such gate so honor it manually.

**0b. 48-hour bake + weekly-OTA window.** (Both checked by Step 4 preflight; confirm now so there are no surprises.)
- Last-good OTA is **2026-05-17** → far more than 48h old, and no firmware version first-appeared this
  calendar week → both windows clear.
```bash
ls -l --time-style=long-iso firmware/artifacts/last-good.ota.bin
docker exec -i verdify-timescaledb psql -U verdify -d verdify -tAc \
  "SELECT count(DISTINCT fw_version) FROM climate_action_log WHERE ts >= date_trunc('week', now() AT TIME ZONE 'America/Denver') AT TIME ZONE 'America/Denver'" 2>/dev/null || true
```
- **Expected:** last-good dated 2026-05-17; weekly count `0`.

**0c. Take a known-good restore point for the functions you're about to replace.**
```bash
docker exec verdify-timescaledb pg_dump -U verdify -d verdify --schema-only \
  > /tmp/verdify-schema-pre-vanda-$(date +%Y%m%d-%H%M).sql
```
- **Expected:** a non-empty `.sql` file. This is your DB-level rollback reference for Steps 1-3.

**Rollback for Step 0:** none (read-only + a dump). Nothing changed.

---

## STEP 1 — Migration 145 (DB diurnal curve + per-zone bands) — ATTENDED

This applies the Vanda curve, the D0 season fix (`fn_current_season` STABLE so June-1 flips to
summer), the orchid-anchored center band, the per-zone band layer, and re-points
`v_target_curve`/`v_zone_band`. **It does not deploy firmware.** It changes what the dispatcher serves.

**1a. Apply 145 (single transaction, abort on any error).**
```bash
set -o pipefail
{ printf 'BEGIN;\n'; cat db/migrations/145-vanda-band-and-join-fix.sql; printf '\nCOMMIT;\n'; } \
  | docker exec -i verdify-timescaledb psql -U verdify -d verdify -v ON_ERROR_STOP=1 -q
```
- **Expected:** informational `NOTICE`s only, ends with `COMMIT`, exit code 0, zero `ERROR/FATAL`.
- **If any ERROR:** the transaction auto-aborts (ON_ERROR_STOP). Nothing is committed; fix and retry.

**1b. Restart the consumers (CLAUDE.md rule 7).** 145 changes served setpoints + MCP context.
```bash
sudo systemctl restart verdify-setpoint-server
sudo systemctl restart verdify-mcp
systemctl status verdify-setpoint-server verdify-mcp --no-pager | head -12
```
- **Expected:** both `active (running)`, no crash loop in the first 30s.

**1c. Verify the served band.**
```bash
# D0: season is STABLE (provolatile 's'), and today (pre-June-1) is still spring.
docker exec -i verdify-timescaledb psql -U verdify -d verdify -tAc \
  "SELECT provolatile FROM pg_proc WHERE proname='fn_current_season'"        # expect: s
docker exec -i verdify-timescaledb psql -U verdify -d verdify -tAc \
  "SELECT fn_current_season()"                                                # expect: spring (flips to summer on 2026-06-01)

# Served noon ceiling ~85F (NOT the broken 78F) and night VPD floor ~0.75.
docker exec -i verdify-timescaledb psql -U verdify -d verdify -c \
  "SELECT * FROM fn_band_setpoints(date_trunc('day', now()) + interval '14 hour')"   # temp_high ~85
docker exec -i verdify-timescaledb psql -U verdify -d verdify -c \
  "SELECT * FROM fn_center_band_setpoints(date_trunc('day', now()) + interval '2 hour')"  # vpd_low/high 0.750/0.850
```
- **Expected:** noon `temp_high ≈ 85.0`; night (h2) `vpd_low/vpd_high = 0.750/0.850`. If noon shows
  78 or night VPD shows 0.2, the join/curve did not take — investigate before continuing.

**1d. MONITOR overnight econ-heat (EXPLICIT WARNING).**
The night-VPD floor is now ~0.75 (was effectively 0.2). Until the Step-4 firmware guard
(ENV-2 night econ-heat suppression) is live, the **firmware can fire heat overnight to chase the
higher humidity target** — econ VPD-rescue heat is still enabled on the controller. This is expected
and bounded, but watch it:
```bash
# Heat-relay firing overnight after 145 (before Step-4 firmware lands):
docker exec -i verdify-timescaledb psql -U verdify -d verdify -c \
  "SELECT date_trunc('hour', ts) h, sum((heat1)::int) heat1_on, sum((heat2)::int) heat2_on
   FROM equipment_state WHERE ts > now() - interval '12 hours' GROUP BY 1 ORDER BY 1"
```
- **If overnight heat firing is excessive/uncomfortable before Step 4 is deployed:** this is exactly
  what Step 4's ENV-2 guard fixes. Either prioritize Step 4 the same night, or roll back 145 (1e).

**Step 1 is REVERSIBLE.** 145 only replaces function/view definitions + curve data; it changes no
control hardware. To revert: re-apply the prior function definitions from your 0c dump (or restore the
specific `fn_*`/`v_*` bodies), then restart `verdify-setpoint-server` + `verdify-mcp`. The dispatcher
returns to the previous served band on restart.

```bash
# 1e. Rollback 145 (example: restore prior fn/view defs from the pre-deploy dump, then restart):
#   grep the relevant CREATE OR REPLACE blocks out of /tmp/verdify-schema-pre-vanda-*.sql,
#   apply them inside BEGIN..COMMIT, then:
sudo systemctl restart verdify-setpoint-server verdify-mcp
```

---

## STEP 2 — Migration 146 (compliance dual-write) — ADDITIVE / SAFE

146 is additive: it adds `*_v2`/graded columns to `daily_summary`, the `daily_zone_compliance`,
`compliance_zone_weights`, and `mv_zone_band_grade` objects, the `fn_compliance_v2`/`fn_zone_band_grade`/
`fn_house_compliance` engine, and re-points `fn_compliance_pct` to a thin shim. **Binary columns are
NOT mutated** — the reward loop is untouched until Step 3. Low-risk.

**2a. Apply 146.**
```bash
set -o pipefail
{ printf 'BEGIN;\n'; cat db/migrations/146-compliance-rearchitecture.sql; printf '\nCOMMIT;\n'; } \
  | docker exec -i verdify-timescaledb psql -U verdify -d verdify -v ON_ERROR_STOP=1 -q
```
- **Expected:** `NOTICE`s only, ends `COMMIT`, exit 0, zero `ERROR/FATAL`. The 146.1 `RAISE EXCEPTION`
  guard will FATAL the apply if 145 didn't land correctly (orchid `temp_stress_high=100`, `fn_zone_band` +
  `achievable_envelope` present) — that is intended; if it fires, go fix Step 1, do not force past it.

**2b. Restart the dual-write consumer (CLAUDE.md rule 7).** The ingestor writes the new columns +
refreshes `mv_zone_band_grade`; MCP rolls up the scorecard.
```bash
sudo systemctl restart verdify-ingestor
sudo systemctl restart verdify-mcp
systemctl status verdify-ingestor verdify-mcp --no-pager | head -12
```
- **Expected:** both `active (running)`. The ingestor's guarded dual-write block (which no-ops pre-146)
  now activates against the new columns.

**2c. Verify `*_v2` columns populate + `daily_zone_compliance` fills.** Allow one daily-summary
cadence (the live recompute runs ~every 30 min); give it ~35 min, then:
```bash
docker exec -i verdify-timescaledb psql -U verdify -d verdify -c \
  "SELECT date, compliance_v2_raw_pct, compliance_v2_attributable_pct, unachievable_frac
   FROM daily_summary WHERE date = CURRENT_DATE"
docker exec -i verdify-timescaledb psql -U verdify -d verdify -c \
  "SELECT date, zone, zone_score FROM daily_zone_compliance WHERE date = CURRENT_DATE ORDER BY zone"
docker exec -i verdify-timescaledb psql -U verdify -d verdify -c \
  "SELECT count(*) FROM mv_zone_band_grade"
```
- **Expected:** today's `compliance_v2_*` are non-NULL after a cycle; `daily_zone_compliance` has
  center/east (and north when occupied) rows with `zone_score` in [0,1]; `mv_zone_band_grade` non-empty.
- **If the columns exist but stay NULL:** confirm the ingestor restarted and check
  `journalctl -u verdify-ingestor --since '40 min ago'` for the dual-write block raising
  `UndefinedColumn`/`UndefinedTable` (it shouldn't, post-146).
- **Post-migration test housekeeping:** once the columns exist, remove the `daily_summary` entries from
  `PENDING_MIGRATION_COLUMNS` in `verdify_schemas/tests/test_drift_guards.py` (the test self-asserts this).
  (Code change, not a deploy step — note it for the next consumer PR.)

**Step 2 is REVERSIBLE.** It is additive; the simplest revert is to leave the new columns/objects in
place (harmless, no consumer reads them for reward yet) and stop the dual-write by reverting the ingestor.
A full revert: re-apply the prior `fn_compliance_pct` def from the 0c dump, drop the new objects, restart
`verdify-ingestor` + `verdify-mcp`. Because binary columns were never touched, nothing downstream breaks.

---

## STEP 3 — Migration 147 (reward swap + ladder re-anchor) — ONLY AFTER 146 DUAL-WRITE VALIDATES

**Do NOT apply 147 until Step 2c shows `compliance_v2_*` populating for at least one full day of history.**
147 re-points the planner reward (`fn_plan_anchor_score`, `v_planner_performance`, `v_daily_kpi`) onto the
controller-attributable graded compliance and re-anchors the plan ladder by quantile-match. Without 146's
dual-written graded history, the re-anchor falls back to binary (`binary_fallback`) and the >=90% ordinal-
stability acceptance cannot be met.

**3a. Apply 147.**
```bash
set -o pipefail
{ printf 'BEGIN;\n'; cat db/migrations/147-reward-swap-and-ladder-reanchor.sql; printf '\nCOMMIT;\n'; } \
  | docker exec -i verdify-timescaledb psql -U verdify -d verdify -v ON_ERROR_STOP=1 -q
```
- **Expected:** `NOTICE`s (ladder re-anchor, ordinal stability), ends `COMMIT`, exit 0. The 147.0 guard
  (`compliance_v2_attributable_pct` + `fn_house_compliance` present) FATALs if 146 didn't land — intended.

**3b. Restart consumers (CLAUDE.md rule 7).**
```bash
sudo systemctl restart verdify-mcp
sudo systemctl restart verdify-ingestor
```
- **Expected:** both `active (running)`. Keep the binary compliance columns one more cycle for rollback.

**3c. Run the re-anchor / ordinal-stability verification (>=90% of anchored plans reproduce).**
Use the in-file reviewer verification query (it lives at the bottom of `147-...sql`); it measures ordinal
stability against the **dual-written graded history** populated since Step 2, not against an empty replay.
```bash
# Pull the reviewer query out of the migration file and run it read-only:
docker exec -i verdify-timescaledb psql -U verdify -d verdify -c \
  "SELECT * FROM plan_anchor_ladder ORDER BY cut_label"   # sanity: graded cuts populated, monotone
# Then run the documented acceptance query (see the 'reviewer query' block in 147-...sql) and confirm
# the ordinal-stability fraction is >= 0.90 over days that have *_v2 history.
```
- **Expected:** `plan_anchor_ladder` shows non-NULL `comp_cut_graded`; the acceptance fraction is
  **>= 0.90**. (Note: in the dry-run BEGIN..ROLLBACK replay it reported 51.7% `binary_fallback` because no
  graded history existed yet — that is the dry run, not this live measurement. Only accept Step 3 if the
  live measurement on dual-written days clears 90%.)
- **If < 90%:** do not let the new reward drive planning. Roll back 147 (3d) and let 146 dual-write
  accumulate more graded days, then retry.

**Step 3 is REVERSIBLE.** Re-apply the prior `fn_plan_anchor_score`, `v_planner_performance`, `v_daily_kpi`
definitions from the 0c dump (the binary columns are still present this cycle), drop `plan_anchor_ladder`,
restart `verdify-mcp` + `verdify-ingestor`. The reward returns to the binary compliance column.

---

## STEP 4 — Firmware OTA — LAST, GATED

Deploys the staged Vanda firmware (ENV-2 night econ-heat suppression, CYC-1/SAF-3 dusk cutoff,
FRT-6/7 feed-hold, SAF-4/SAF-5). This is what removes the Step-1 overnight econ-heat exposure.

### 4a. GATE CHECKLIST (all must be true before you run the deploy)

| Gate | How to confirm | Required state |
|---|---|---|
| No open critical/legacy-high alerts | Step 0a query (also run by preflight) | `0` |
| >= 48h bake on last-good | last-good dated 2026-05-17 | clear |
| <= 1 OTA / calendar week | Step 0b weekly count | `0` |
| Invariants green | `docs/runbooks/evidence/.../firmware-invariants.txt` | 16/16 over 193,525 rows |
| Unit tests green | `docs/runbooks/evidence/.../test-firmware.txt` | 190 / 0 (was 178; +12) |
| firmware-replay-diff sign-off | characterized 2.54% (intended dusk-cutoff/econ-heat) | `THRESHOLD_PCT` override + coordinator/iris concurrence |

**The replay-diff is RED-BY-DESIGN at the default `THRESHOLD_PCT=0`** (2.54% intended divergence). You
must obtain coordinator + iris concurrence and set the override before deploying. Re-confirm the diff:
```bash
make firmware-replay-worktree OLD=main THRESHOLD_PCT=3
```
- **Expected with the override:** 2.54% < 3% → gate passes. Characterization basis is in
  `docs/runbooks/evidence/vanda-band-compliance-ota/replay-diff-characterization.txt`
  (fog OFF overnight/at-dusk; the single heat1 ON is legitimate band heat, not econ-rescue;
  SAFETY_COOL survival fog preserved; daytime control unchanged).

> Stress-window note (CLAUDE.md rule 5): if the forecast max next 24h is >85F, preflight prints it as
> context and does NOT block. Severe-alert, 48h-bake, and weekly-OTA gates remain hard.

### 4b. Deploy.

The deploy compiles from the current worktree, stamps the real `fw_version`, OTAs the ESP32, waits ~60s,
runs the post-deploy sensor-health sweep, and **auto-rolls-back to `last-good.ota.bin` on failure**.

The worktree is intentionally dirty (uncommitted branch edits, per orchestrator convention), so the
deploy needs the explicit dirty-deploy override with a reason:
```bash
make firmware-deploy \
  ALLOW_DIRTY_FIRMWARE_DEPLOY=1 \
  FIRMWARE_DEPLOY_OPERATOR_SIGNOFF=1 \
  FIRMWARE_DEPLOY_OVERRIDE_REASON="Vanda band-compliance rearch; coordinator+iris THRESHOLD_PCT=3 sign-off; orchestrator-staged uncommitted branch"
```
- **Expected (success path):**
  - preflight prints `✓ No unresolved critical/legacy-high alerts`, `✓ 48-hour bake check passed`,
    `✓ Weekly OTA limit clear`;
  - compile `[SUCCESS]`, OTA upload completes;
  - `wait-for-firmware-version.sh` confirms the new `fw_version` reporting in;
  - `sensor-health SINCE='5 minutes'` passes → `✓ Deploy accepted. Archived build outputs + promoted
    expected firmware pin. Rollback target unchanged while this build bakes.`
- **Expected (failure path):** sensor-health fails → the Makefile prints
  `▓▓▓ SENSOR-HEALTH FAILED POST-OTA — initiating auto-rollback ▓▓▓`, flashes `last-good.ota.bin`,
  waits 60s, re-runs sensor-health, and exits non-zero. **No operator action needed for the auto-rollback;**
  triage the sensor-health output and the root cause before re-attempting.

> Note: `firmware-deploy` re-compiles from the worktree; the pre-staged
> `firmware/artifacts/2026.5.29.2232.fb17f43-vanda-band/firmware.ota.bin`
> (sha256 `9ae13495...e6cbc`) is the evidence/reference binary built from the same source — confirm the
> deploy's compiled flash size matches (≈1,061,616 bytes / Flash 57.8%) as a sanity check that the same
> source was built.

### 4c. Post-deploy confirmation (overnight econ-heat now guarded).
```bash
# fw_version now matches the deployed build:
docker exec -i verdify-timescaledb psql -U verdify -d verdify -tAc \
  "SELECT DISTINCT fw_version FROM climate_action_log WHERE ts > now() - interval '10 min'"
# Overnight heat firing should drop relative to the Step-1d window once night econ-heat is suppressed.
```
- **Expected:** the deployed `fw_version` reporting in; overnight heat-to-chase-humidity gone.

**Step 4 is REVERSIBLE.** The deploy auto-rolls-back on a failed health sweep. For a manual rollback at
any time (e.g. you observe a regression after acceptance):
```bash
make firmware-rollback        # flashes firmware/artifacts/last-good.ota.bin (2026.5.17.1849.9353df5) back onto the ESP32
```
- Do NOT promote the new build to last-good until the 48-hour bake completes clean. `firmware-deploy`
  deliberately leaves `last-good` on 2026-05-17 during the bake; promotion is a later explicit
  `make firmware-promote-last-good` after 48h with no critical sensor-health alert.

---

## REVERSIBILITY SUMMARY

| Step | Reversible? | How |
|---|---|---|
| 0 | n/a | read-only + schema dump |
| 1 (mig 145) | Yes | re-apply prior `fn_*`/`v_*` defs from the 0c dump, restart `verdify-setpoint-server` + `verdify-mcp` |
| 2 (mig 146) | Yes (additive) | revert ingestor to stop dual-write, or restore prior `fn_compliance_pct` + drop new objects; binary cols untouched |
| 3 (mig 147) | Yes | restore prior `fn_plan_anchor_score`/`v_planner_performance`/`v_daily_kpi`, drop `plan_anchor_ladder`; binary reward cols still present this cycle |
| 4 (firmware OTA) | Yes | auto-rollback on failed sweep; manual `make firmware-rollback` to `last-good` (2026.5.17.1849.9353df5) |

## DO-NOT list
- Do not apply 146 before 145, or 147 before 146 has dual-written real graded history (146's/147's guards
  will FATAL, by design).
- Do not promote `last-good` until the 48-hour firmware bake completes clean.
- Do not force past a `RAISE EXCEPTION` guard or a preflight `✗` gate — they encode the ordering above.
