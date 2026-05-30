# Vanda band-compliance rearchitecture (firmware + DB curve + compliance engine)

Branch: `firmware/vanda-band-compliance-rearch` → `main`
Implements: `docs/design/vanda-zone-control-design.md` (§3 diurnal curve, §4 irrigation/fertigation,
§5 crop/recipe/topology + migration-145 ordering) and `docs/design/band-compliance-architecture.md`
(§3 canonical model, §4 target bands, §5 achievable envelope, §6 compliance engine, §7 consolidation +
planner-reward + consumer re-point, §8 migration plan). Backlog: D0/D4-D8 (mig145), G1-G6 (mig146),
G7-G9 (mig147), plus firmware items ENV-2 / CYC-1 / SAF-3 / FRT-6 / FRT-7 / SAF-4 / SAF-5 / IRR-2.

This is a firmware change. Per CLAUDE.md rule 9 the required artifacts (replay-diff, invariant-suite,
unit-test delta), the restart docs (rule 7), and the `THRESHOLD_PCT` override justification (rule 8) are
below. Evidence files: `docs/runbooks/evidence/vanda-band-compliance-ota/`. Deploy sequence:
`docs/runbooks/vanda-deploy-runbook-2026-05-30.md`.

---

## What changed

**DB (3 migrations, applied in order; replay only via BEGIN..ROLLBACK so far — never committed to live):**
- `db/migrations/145-vanda-band-and-join-fix.sql` — D0 (`fn_current_season` IMMUTABLE→STABLE, June-1 fix),
  the orchid-anchored center diurnal band (night VPD 0.75, midday ceiling 85F after the non-center stress
  clamp), the per-zone band layer (`fn_zone_band`: center→orchid, east→intersection-ideal/union-stress,
  empty→`_default`, never NULL), achievable-envelope table + accessor, and re-points `v_target_curve` /
  `v_zone_band` off the deprecated step functions.
- `db/migrations/146-compliance-rearchitecture.sql` — ADDITIVE dual-write. Graded + feasibility-aware
  compliance (`fn_grade_credit`, `fn_zone_band_grade`, `fn_compliance_v2`, `fn_house_compliance`),
  `compliance_zone_weights` (center 0.60 / east 0.40 / others 0), `daily_zone_compliance`,
  `mv_zone_band_grade` (plain MV — pg_cron not installed; ingestor-refreshed), `daily_summary *_v2`
  columns (binary columns NOT mutated), and `fn_compliance_pct` re-pointed to a thin shim. Reward
  untouched here.
- `db/migrations/147-reward-swap-and-ladder-reanchor.sql` — reward re-point onto controller-attributable
  graded compliance (`fn_plan_anchor_score`, `v_planner_performance`, `v_daily_kpi`), `plan_anchor_ladder`
  quantile-match re-anchor with replay-safe `binary_fallback`, freeze-preserving backfill (frozen anchors
  untouched).

**Firmware (8 files, `firmware/**` only):**
- `firmware/lib/greenhouse_types.h` — 7 new `Setpoints` fields + defaults + clamps.
- `firmware/lib/greenhouse_logic.h` — ENV-2 night econ-heat suppression; CYC-1/SAF-3 authoritative dusk
  cutoff (VPD-independent, caps both stress latest-hours, SAFETY_COOL survival fog exempt); FRT-6 feed-hold
  blocks clean wetting.
- `firmware/greenhouse/{globals,controls}.yaml` — wires the above + SAF-4 daily-volume hard ceiling +
  non-bypassable center duty cap + midnight runtime-counter reset (fixes a latent never-reset lockout) +
  SAF-5 fog⊥fertilizer-master + FRT-7 post-feed flush relocated after a 90-min absorption hold (irrig
  state 11). IRR-3/IRR-4 left as a clearly-marked DEFERRED stub.
- `firmware/greenhouse/tunables.yaml` — `cfg_*`/`num_*`/`sw_*` readbacks for every new tunable (rule 6).
- `firmware/test/{invariants.h,replay_invariants.cpp,test_greenhouse_logic.cpp}` — invariants #17-#20,
  +12 unit tests.

**Python consumers (ingestor/api/schemas/scripts):** compliance dual-write mirror in `ingestor/tasks.py`
(no-ops until 146 lands), schema-first additions in `verdify_schemas/**`, scorecard fallback in
`api/main.py`, and a fidelity-test + schema-column-name reconciliation against the firmware refactor and
migration 147.

---

## REQUIRED ARTIFACT 1 — Replay-diff (CLAUDE.md rule 9 + rule 8)

`make firmware-replay-worktree OLD=main`
→ **4,919 / 193,525 divergent rows = 2.54%** (exits non-zero at default `THRESHOLD_PCT=0` — intended).

Full output: `docs/runbooks/evidence/vanda-band-compliance-ota/replay-diff-worktree-vs-main.txt`
Characterization: `docs/runbooks/evidence/vanda-band-compliance-ota/replay-diff-characterization.txt`
Paired row dump (9,838 lines): `docs/runbooks/evidence/vanda-band-compliance-ota/replay-diff-rows.tsv`

Divergence is **100% confined to the intended ENV-2 econ-heat-night + CYC-1/SAF-3 dusk-cutoff behavior**
plus its second-order mist-stage FSM carryover at the window boundaries:
- Hour distribution: dusk window (18-21) 2,859 rows + night window (00-06) 1,827 rows + stage-carryover
  tail (07-12) 233 rows. Hours 07-12 have ZERO core-relay diffs (pure mist-stage resync).
- Net actuator effect: fog **2,355 OFF** / 7 ON (the 7 ON = 6 SAFETY_COOL survival-cooling rows, which
  are dusk-exempt by design, + 1 hour-06 mist re-engage at the window end); heat1 **1 ON** / 0 OFF (the
  single ON at 2026-04-22 04:34 is legitimate low-temp BAND heating, temp 64.1F in band 62.4-65.6, NOT
  econ-rescue heat — `vpd 0.836 ≥ vpd_low 0.3` and the econ path is suppressed at night); heat2 0 changes;
  no vent/fan/cooling logic changed except where the dusk/night gates apply.
- Mode transitions: SEALED_MIST→IDLE 2,857; VENTILATE→VENTILATE 1,896 (only the concurrent fog flag drops,
  fans/vent unchanged); SAFETY_COOL→SAFETY_COOL 6 (survival fog preserved).
- ENV-2 (night econ-heat suppression) contributes 0 replay divergence because `econ_block` is false
  throughout the corpus; it is validated by unit tests instead (see Artifact 3).

### THRESHOLD_PCT override justification (rule 8)
The 2.54% is intentional, dry-down behavior — fewer fog-minutes overnight/at-dusk and no overnight
heat-to-chase-humidity, with **no daytime control change** and survival cooling preserved. Request
**`THRESHOLD_PCT=3`** to cover 2.54%, with coordinator + iris independent replay reproduction and
concurrence per rule 9. Deploy command in the runbook carries the override + reason.

`make firmware-replay-worktree OLD=main THRESHOLD_PCT=3` → passes.

---

## REQUIRED ARTIFACT 2 — Invariant-suite (rule 9)

`make firmware-invariants` → **16/16 invariants pass over 193,525 corpus rows.**
Output: `docs/runbooks/evidence/vanda-band-compliance-ota/firmware-invariants.txt`

New invariants #17 (night-drop, gated on the new-band config — intentionally skipped on the current
climate-only corpus which served the OLD <10F-drop band), #18 (fog⊥fertilizer_master, SAF-5), #19/#20
(feed-hold blocks clean center wetting, FRT-6) are wired into the Runner; #18/#19/#20 are vacuously true
until the corpus exports `eq_fertilizer_master`/`feed_hold_active`. No existing invariant regressed.

---

## REQUIRED ARTIFACT 3 — Unit-test delta (rule 9)

`make test-firmware` → **190 passed / 0 failed** (main baseline 178 → **+12**); replay-overrides golden
self-test all green. Output: `docs/runbooks/evidence/vanda-band-compliance-ota/test-firmware.txt`.

New tests cover: night-econ suppression, dusk cutoff gating fog+mist regardless of VPD, stress-hour
capping at the cutoff, SAFETY_COOL/feed-hold bypass, feed-hold blocking clean wetting, and invariants
#17-#20 firing. 3 pre-existing stress-window tests were updated to disable the dusk cutoff so they isolate
the legacy latest-hour mechanic.

Python side: `make test-fast` → 438 passed / 0 failed (after the cross-group fidelity-test reconciliation);
`make lint` (ruff) → all checks passed.

### Build
`make firmware-check` → `[SUCCESS]`, Flash 57.8% (1,061,219 / 1,835,008), RAM 14.4%.
Staged OTA: `firmware/artifacts/2026.5.29.2232.fb17f43-vanda-band/firmware.ota.bin`
(sha256 `9ae13495f0d4de2e16427565c217c42bf6fd402b7c695e9f8622c7e4239e6cbc`, 1,061,616 bytes; NOT promoted to
last-good).

### Migration replay (rule 9 evidence)
`BEGIN; \i 145 \i 146 \i 147; ROLLBACK;` (ON_ERROR_STOP=1) → clean apply, zero ERROR/FATAL, explicit
ROLLBACK, never committed. Output:
`docs/runbooks/evidence/vanda-band-compliance-ota/migration-rollback-replay.txt`.

---

## REQUIRED RESTART DOCS (CLAUDE.md rule 7)

This PR touches `verdify_schemas/**` and the MCP/ingestor consumer layer, and ships DB migrations that
change served setpoints and the dual-write/reward path. Post-merge service bounces, **per migration step**:

| After applying | Bounce | Why |
|---|---|---|
| Migration 145 (curve + per-zone bands, served-setpoint change) | **`verdify-setpoint-server`** (dispatcher) + **`verdify-mcp`** | dispatcher serves the new band; MCP context/scorecard reads the curve. No firmware OTA from 145 itself. |
| Migration 146 (dual-write engine) | **`verdify-ingestor`** (writes `*_v2` cols, refreshes `mv_zone_band_grade`, runs `refresh_achievable_envelope`) + **`verdify-mcp`** (scorecard/context rollups) | activates the guarded dual-write block + graded rollups. No dispatcher bounce. |
| Migration 147 (reward swap) | **`verdify-mcp`** + **`verdify-ingestor`** | reward + ladder consumers; 147 also touches `mcp/server.py` prompt copy at the consumer layer. Keep binary columns one cycle for rollback. |

Schema-first note: after 146 lands and `daily_summary` has the new columns, drop the `daily_summary`
entries from `PENDING_MIGRATION_COLUMNS` in `verdify_schemas/tests/test_drift_guards.py` (the test
self-asserts this).

---

## Rule-6 compliance (tunable readbacks)
All 7 new firmware tunables carry `cfg_*`/`num_*`/`sw_*` readback entities in
`firmware/greenhouse/tunables.yaml` (dusk_cutoff_hour, night_start/end_hour, post_feed_hold_min,
mister_hysteresis_kpa, mister_daily_volume_max_gal, and the two econ/dusk switches). The
`no-new-fire-and-forget` CI job is satisfied.

---

## Deferrals / known caveats (called out for the reviewer; NOT silently shipped)
1. **IRR-3 dawn rehydrate / IRR-4 midday drench** — DEFERRED as a marked stub in `controls.yaml`. They
   need dispatcher-pushed sunrise/solar-peak anchor globals + a controls-level acceptance test, not cleanly
   validatable in the C++ replay harness. Not shipped unvalidated.
2. **`v_plan_compliance` / `v_plan_accuracy(/_by_day/_72h)` drops** (design §8.2 step 146.10) — DEFERRED
   (left as comments in 146) because they're still in `tests/test_02_database.py` REQUIRED_VIEWS, which is
   outside this file-group. They're dead (0 rows); their rebuild + the REQUIRED_VIEWS edit are sequenced
   together under P1a (coordinator/web). `v_setpoint_compliance` (not in the required list) IS dropped.
3. **147 ladder ordinal stability** — the dry-run replay reports 51.7% `binary_fallback` because no graded
   history exists in a single rolled-back tx; the >=90% acceptance is measured live in Phase 2 against
   146's dual-written graded history (in-file reviewer query). Apply 147 only after 146 dual-writes a day
   of real graded history.
4. **`achievable_envelope`** is seeded conservatively (`authority_seed`, summer h14 cap 82); the ingestor
   `refresh_achievable_envelope` job must overwrite it with the live Term A/B/C derivation. Until then the
   served summer cap is conservatively low (fail open-to-achievable, never NULL).

## Reviewer ask
Coordinator (iris-dev) independent replay reproduction of the 2.54% diff + `THRESHOLD_PCT=3` concurrence,
and iris planner concurrence on the reward re-point (interface-level change). Merge only on three-reviewer
agreement (firmware agent + coordinator + iris); then the 48-hour bake before OTA per the freeze rules.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
