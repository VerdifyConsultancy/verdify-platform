# Backlog close-out OTA — staged firmware bundle + evidence

**Status: STAGED ONLY. NOT deployed, NOT promoted to last-good.** Operator deploys after review + the CLAUDE.md firmware gates (48h bake, ≤1 OTA/week, no open critical alert).

## Staged bundle

- **FW version:** `2026.5.30.1314.f2bad50-backlog-closeout`
- **.ota.bin:** `firmware/artifacts/2026.5.30.1314.f2bad50-backlog-closeout/firmware.ota.bin`
  (sha256 `1b08543b3355eabbfcf22dc4061bb643380c32280efc8d176f844ca052675ce6`, 1,074,368 bytes)
- **Build:** `make firmware-check` equivalent (`firmware-esphome-worktree.sh -s fw_version <v> compile`) — SUCCESS, flash 58.5%.
- **Archive:** `make firmware-archive-artifacts FW_VERSION=<v>` — **no `PROMOTE_LAST_GOOD`** (last-good stays `2026.5.17.1849.9353df5`, untouched).
- **Provenance:** `metadata.env` records `source_sha=f2bad50`, `source_dirty=1` (this sprint's F1/F3/SF1/M2/M14/NB7 work is uncommitted on top of the committed base f2bad50). Full `git diff` patch + source snapshot captured under `provenance/` and `source-snapshot/`.

## What's in the bundle (firmware group's sprint items)

| Item | What | Layer |
|---|---|---|
| F1 / F3 / FRT-8 | AM-only feed window `[feed_start_hour, feed_end_hour)` — VPD-independent rail gating scheduled fert states 2/4/7/8 **and** the manual fert buttons | `feed_window_open()` (logic) + controls.yaml dequeue gate + tunables.yaml button lambdas |
| SF1 / SAF-1 | `sensor_degraded` VPD-trust gate — when avg RH/VPD probes are down, suppress all VPD-chasing (humidify/dehum/fog/dry-override/vpd_min_safe/econ-heat), keep temp-only control + timed center bursts | `vpd_control_trusted()` threaded through evaluate_climate_decision / determine_mode_band_first / determine_mode / resolve_equipment + controls.yaml wires `sensor_degraded = !avg_ok` |
| M2 / B13 | CO2 inverted-linear transform fix (voltage falls as ppm rises) + plausibility gate + `co2_plausible` flag | sensors.yaml + globals.yaml co2_cal_* |
| M14 / ENV-5 | Dark guarantee — occupancy task-light gated on `in_window`; `outside_window` reported before occupancy fallbacks; ≥6h dark | evaluate_lighting() + invariant #23 |
| NB7 / CYC-4 | Overnight ≤5s fog micro-pulse — dedicated short-pulse path bypassing the fog duty table, hard-gated (dark window, VPD>ceiling, dew/RH/temp, feed-hold, occupancy, NB5-absent), ≤5s clamp | `overnight_micropulse_permitted()` (logic) + controls.yaml timer/lockout/willFog fold + invariant #24 |

Rule 6 (every new tunable needs a `cfg_*` readback): satisfied — all 8 new tunable globals (`feed_start_hour`, `feed_end_hour`, `sw_overnight_micropulse_enabled`, `sw_night_humidity_source_present`, `micropulse_vpd_ceiling`, `micropulse_max_on_s`, `micropulse_min_gap_s`, `micropulse_min_dew_margin_f`) have `cfg_*` readback sensors in sensors.yaml.

## Evidence files

| File | What |
|---|---|
| `firmware-check-build.log` | full ESPHome compile log (SUCCESS) |
| `test-firmware.txt` | `make test-firmware` — 215 passed / 0 failed + 8-month replay-overrides + synthetic self-test (all flags ✓) |
| `test-firmware-delta.txt` | unit-test delta: base f2bad50 = 199, NEW = 215 (+16, 0 regressions); the 16 new TEST() names |
| `firmware-invariants.txt` | `make firmware-invariants` — invariants #1–#24 (incl. new #23 min-dark + #24 overnight-fog) PASS over 193,525 rows |
| `replay-diff-worktree-vs-f2bad50.txt` | `make firmware-replay-worktree OLD=f2bad50` — **0 divergent rows** |
| `replay-diff-characterization.txt` | precise account of WHY 0% is correct/expected per item + corpus blind-spots |
| `migration-146-rollback-replay.txt` | 146 alone in begin..rollback (ON_ERROR_STOP=1) — clean (rc=0) |
| `migration-147-rollback-replay.txt` | 147 alone — (correctly) aborts at its 146-prerequisite RAISE guard |
| `migration-146-147-chained-rollback-replay.txt` | 146→147 in one outer begin..rollback — clean (rc=0); validates the real apply sequence with nothing committed |

## Replay-diff one-liner

`make firmware-replay-worktree OLD=f2bad50` → **0 / 193,525 rows divergent**. Mode + relay behavior is 100% preserved on the historical corpus. This is correct and expected: every new behavior is gated on a flag/path the climate replay corpus does not drive (sensor_degraded is not a corpus column; the micro-pulse timer, feed-window dequeue, and CO2 transform live in controls.yaml/sensors.yaml which `replay_emit` does not execute). The new paths are positively verified by invariants #23/#24 and the +16 unit tests, not by the replay diff. See `replay-diff-characterization.txt`.

## Migrations 146/147 (not part of the OTA; DB-only, NOT applied)

- Neither migration has its OWN top-level `BEGIN;`/`COMMIT;` (all `BEGIN` are PL/pgSQL block-level inside `DO`/function bodies; `ON COMMIT DROP` is a TEMP TABLE clause). Each is therefore safe to wrap in an external `begin..rollback` with `ON_ERROR_STOP=1` (nothing commits to prod).
- 146 replays cleanly standalone. 147 has a hard prerequisite guard (`RAISE EXCEPTION` unless 146's `daily_summary.compliance_v2_attributable_pct` exists), so standalone 147 aborts by design; the chained 146→147 replay (one rolled-back txn) is clean.
- **Operator note:** the chained replay's ladder ordinal-stability NOTICE reports 77/147 (52.4%) under `derivation=binary_fallback` because 146's dual-write graded history is not yet populated (0 plans with graded comp). 147's own header + the migration's >=90% acceptance gate are explicitly defined "against dual-written history" — i.e. the reward-swap acceptance is only meaningful AFTER 146 lands and its dual-write co-existence window accumulates graded data. This is sequencing, not a migration defect. These migrations are coordinator/shared-territory (`db/migrations/`), serialized, and were not modified by this task.
