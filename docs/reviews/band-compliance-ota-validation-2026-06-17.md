# Band-Compliance Build — OTA Validation Evidence (2026-06-17)

**Milestone:** the band-compliance firmware (WS-A–D) is **implemented, proven offline, and
STAGED — ready for OTA when the preflight passes.** Nothing is deployed; the OTA flash and
explicit `argocd` data/web sync retain the runtime safeguards in the freeze rules.

- **Decision basis:** `docs/adr/0003-band-compliance-track-the-target.md` §6 (7 locked decisions)
- **Audit basis:** `docs/reviews/band-compliance-reconcile-sprint-2026-06-17.md`
- **Sprint plan:** `docs/reviews/band-compliance-build-sprint-2026-06-17.md`
- **Epic:** #359. **Branch:** `main` (collaborative). **Last OTA'd firmware:** `6a3b35a`.

---

## 1. What changed (the device-visible behavior)

The greenhouse controller now implements Jason's control story end-to-end:

| # | Workstream | Change (commit) |
|---|---|---|
| WS-A | **Pinch armed** | The controller now *strives toward the target* — `band_track_fraction` defaults to 0.50 (was 0 = float-envelope); the control band is pinched toward `temp_target`/`vpd_target`, with a per-axis width-floor that provably prevents heat/cool demand overlap. Fully plumbed planner knob. (`cb32cf8`) |
| WS-B | **Symmetric escalation** | `fan1→fan2` now latches with a de-escalation hysteresis exactly like `heat1→heat2` — one shared `stage2_escalation_latch`; heat2 refactored onto it byte-identical. New `cool_stage2_exit_hysteresis_f` tunable. (`23fdbd8`) |
| WS-C | **Bidirectional dehum selector** | Deterministic outdoor-aware moisture-exchange estimator (Magnus 237.3): frigid wet night → heat-to-dry (no vent thrash; the orchid fix), cool-dry → vent-dehum, humid-outside/dry-inside → import moisture by venting. `vpd_min_safe` rail always fires. New `vent_exchange_fraction` AI-tunable. #14 cold-vent cap raised 12→24. (`361eca8`, `6e0f317`, `d2fa33c`) |
| WS-D | **Fog-first wetting** | Wetting inverted to fog (0.26 GPM) → misters (~1 GPM) by water GPM, **layered** (fog persists), symmetric de-escalation hysteresis. Mister zone-targeting untouched. (`08839f2`) |
| WS-F | **Served target = device curve** (data) | `fn_band_setpoints` serves the device-truth target; divergence audit extended; ingestor logs target-from-curve. (`64e8050`) |
| WS-G | **Metric reshape** (data) | `fn_grade_credit` is a tent peaking AT target — killed the flat-1.0-in-band saturation; `|actual−target|` headline metric (`fn_band_deviation`). (`0ed898c`) |
| PR-9 | **CI gate fixes** | Band-derive gate widened; the no-op `no-new-fire-and-forget` guard fixed. (`cf9a1e6`) |

> **2026-07-03 envelope note (#412, added by #413):** WS-C's moisture-exchange
> estimator (and its `vent_exchange_fraction` tunable) models air exchange as
> vent-driven mixing against an otherwise closed envelope — true when this was
> validated. Since ~2026-06-19 the door screen-window is OPEN (and stays open
> until fall), ~3×-ing passive night air exchange (indoor−outdoor moisture surplus
> stepped +5.7 → +1.9 g/m³ at flat fog/mister source duty), so the closed-vent
> baseline used for that validation is weakened while it is open. Record the envelope
> config in every bake/KPI comparison window; never change the window state
> mid-bake.

**Net device behavior:** the homepage band-trace will show actual *tracking* the diurnal
target curve 24/7 instead of floating, with the night VPD wet-drift eliminated.

---

## 2. Receipts (re-run 2026-06-17, on `main` HEAD `0ed898c`)

| Gate | Command | Result |
|---|---|---|
| Native firmware tests | `make test-firmware` | **248 passed, 0 failed** |
| Invariant suite | `make firmware-invariants` | **✓ all pass** (193,525 corpus rows) |
| Device-visible diff vs last OTA | `make firmware-replay-band OLD=6a3b35a` | **47.75%** mode/relay decisions changed — the band-compliance behavior (intentional) |
| Stock mode diff vs last OTA | `make firmware-replay OLD=6a3b35a NEW=HEAD` | 5.81% — intentional; firmware PR carries `REPLAY_DIFF_THRESHOLD_PCT` override + this band-derive evidence (freeze rule 8) |
| Lint | `make lint` (ruff) | **✓ clean** |
| Migrations 181/182/183 | `make migration-rollback-safety` | **✓ all safe-to-wrap** (non-self-transactional) |
| Grade-credit tent | `pytest tests/test_grade_credit_tent.py` | **✓ 3 pass** (peak=1.0, edge=0.5, saturation killed, shoulder continuous) |
| Registry/drift + firmware-twin | `pytest test_tunable_registry test_firmware_drift test_19_*` | **✓ all pass** |

Per-workstream band-derive proofs (captured at each landing): WS-A **43.3%**, WS-B **0%**
(heat2 byte-identical), WS-C estimator + native dehum tests, WS-D **11.6%**.

`make firmware-check` (ESP32 compile) runs in CI — local ESPHome is unavailable; the C++
compiles via the native test harness and the YAML mirrors existing entries.

---

## 3. OTA-staging checklist (ready on the preflight passing)

The firmware is **staged, not flashed** — there is no firmware promotion in the pipeline.
Before `make firmware-deploy`, the preflight (the freeze rules) verifies:

- [x] Offline gates green: test-firmware, invariants, replay-band, lint, twin-sync.
- [x] Firmware-twin shadow in sync (`test_19` green).
- [x] Net behavioral change carries replay evidence (this doc).
- [ ] **No open `severity='critical'` alert** (preflight queries the alerts table — Jason-time).
- [ ] **48-hour bake** since the last OTA + **≤1 OTA/week** (preflight enforces).
- [ ] Outdoor-temp stress-window check (operator context, non-blocking).
- [ ] Single-writer device gate intact (`sum(verdify_esp32_writer_estab)==1`).

**On the preflight passing only:** `make firmware-deploy` (preflight → compile → OTA → soak →
wait-for-version → sensor-health sweep; auto-rollback on failure), then 48h bake, then
`make firmware-promote-last-good`. Bundle WS-A–D as ONE device-visible change.

**Data/web (WS-F, WS-G) deploy separately** (not in the OTA): push → GHCR → `prod-promote`
→ **gated `argocd app sync verdify-prod-dark`** (applies migrations 181→182→183 schema-first).
POST-MERGE: bounce `verdify-mcp` + `verdify-ingestor` (mig 181/183 change fns they read/write).

---

## 4. Decisions Jason made (locked in ADR0003 §6)

1. Pinch is the default (bounded knob, float retired). 2. Kill saturate-1.0 (peak-at-target).
3. Serve target as one source + homepage cascade. 4. Dehum = deterministic bidirectional
selector, **co-run allowed when proven**, **import moisture when humid-outside/dry-inside**.
5. Symmetric escalation. 6. Fog-first, **layered**, fog-dwell **AI-tunable**. 7. One controller,
net-negative LOC. Plus: **#14 cold-vent cap raised 12→24** (ADR §3 accepts more cycling).

---

## 5. Non-OTA-blocking tail (cleanup + DB-gated)

These do **not** change the OTA binary's behavior and are scoped for follow-up:

- **WS-E (cascade deletion)** — the ~478-line legacy controller is **dead-path** (`firmware-replay
  OLD=main NEW=HEAD = 0` confirms it's unreachable in prod). Deleting it + `THERMAL_RELIEF` (enum
  reindex confirmed safe — mode is string-on-wire) is the net-negative-LOC simplicity win but
  requires migrating ~30 legacy-path tests to band-first; doing that carefully (not dropping
  coverage) is the remaining effort. Started: dead `fog_stress_*` tunables purged (`1994293`).
  Behaviorally a no-op on the OTA, so it can land after the OTA.
- **WS-G DB-gated steps** — the daily.py deviation accumulator (persist `dev_*` columns) and the
  mig-147 ladder re-quantile-match + BC-6 ≥90% ordinal-stability gate need a live DB to run/verify
  (mig-147 `binary_fallback` keeps the reward safe meanwhile). The metric is already available via
  `fn_band_deviation` on-demand.
- **`IDEAL_EDGE_CREDIT=0.5`** is the recommended grade-shape default — confirm/record in ADR §6.2.
