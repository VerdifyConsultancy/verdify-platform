# Firmware Digital Twins — Design

**Status:** Design-only. Nothing here is built or deployed. No production code, schema, or infra is changed by this document.
**Scope:** firmware agent authors the twin runtime + harness extensions; everything crossing `db/migrations/`, `docker-compose.yml`, `.github/workflows/`, `grafana/provisioning/`, and ingestor's `alert_monitor` routes through coordinator / the owning agent (per `CLAUDE.md`).
**Date:** 2026-05-29.

A reviewer's shortcut: every claim below is tagged **[verified]** (read from the repo), **[buildable-now]** (constructible with what exists, minor additive glue), or **[aspirational]** (needs new infra/config that does not exist yet). The honest punchline: the **prod twin shadowing live telemetry to a divergence dashboard (MVP)** is buildable-now; the **stage pre-deploy bake gate** and **dev CI live-diff** are buildable-now-with-caveats; **full relay-interlock fidelity (Tier 2 ESPHome host build)** is aspirational.

---

## 1. Concept + why

A **twin** is the real ESP32 control logic running off-device, fed the same telemetry the device saw, computing what the controller *would* decide — and **never actuating**. It writes predicted decisions to a DB table and Grafana, nothing else.

### Why this is cheap to build: the seed already exists

The native replay harness is not *like* the device logic — it compiles the **same** `firmware/lib/greenhouse_logic.h` the ESP32 includes, and drives the identical call sequence per telemetry row **[verified]**:

- `firmware/test/replay_emit.cpp` carries one `ControlState state = initial_state()` (line 87) across rows and per row calls `determine_mode(in, sp, state, dt_ms)` → `resolve_equipment(...)` → `evaluate_overrides(...)` (lines 199–201), then prints one TSV row whose header is exactly `ts, mode, relay_fog, relay_vent, relay_fan1, relay_fan2, relay_heat1, relay_heat2, mist_stage, reason, override_bits` (line 91, printf line 210). That TSV header **is** the twin's decision schema, already implemented.
- The on-device control tick in `firmware/greenhouse/controls.yaml` (the `interval: 5s` block) calls the **identical functions in the identical order**. Both paths include `lib/greenhouse_logic.h`.
- Production forces `setpts.sw_fsm_controller_enabled = true` before each tick; the harness mirrors this via `REPLAY_EMIT_FORCE_FSM` defaulting on (`replay_emit.cpp` lines 166–170; `scripts/firmware-replay-diff.sh` passes `REPLAY_EMIT_FORCE_FSM=${REPLAY_EMIT_FORCE_FSM:-1}` at lines 65–66). So the band-first controller path is the one both exercise.

The only structural delta between the harness and a twin: `replay_emit.cpp` is a **batch** program (read whole CSV → emit trace → exit at EOF). A twin is the same loop turned into a **long-running service** fed one new telemetry row per tick, writing each decision to a DB sink. The decision math, the FSM, the stateful `dt_ms` accounting, the gap-reset, and the column schema all already exist and are already OTA-gating via `make firmware-replay` / `firmware-invariants` / `test-firmware`. **[verified]**

### The three things twins give us

1. **Continuous live validation.** The 16 invariants (`firmware/test/replay_invariants.cpp` + `invariants.h`, with check #13 deferred — confirmed via static_assert/Mode enum reads) run today only against the frozen corpus in CI. A live twin runs them against today's real telemetry stream continuously.
2. **Pre-deploy shadow.** A **stage** twin pinned to the baked OTA candidate shadows live telemetry for the full bake window *before* the binary is flashed — converting rule 3's file-mtime bake into a behavioral bake.
3. **Firmware-vs-reality drift detection.** A **prod** twin pinned to the currently-deployed `last-good` predicts what the deployed code *should* do given logged sensors, and is compared against what the relays *actually did* (`equipment_state`). A sustained gap means a firmware bug, a sensor/telemetry skew, or a stale/corrupted setpoint push — the exact class behind the 2026-04-21 MCP staleness incident. **This signal is novel: twin-vs-twin only catches divergence between code versions; if prod firmware has a latent bug the twin faithfully reproduces, twin-vs-twin is blind to it. Prod-twin-vs-reality is not.**

---

## 2. Twin runtime & build

### 2.1 Recommended approach: native logic twin (Tier 1)

Wrap the exact code `replay_emit.cpp` already compiles (`g++ -std=c++17 -O2 -I lib`, used verbatim in `firmware-replay-diff.sh` line 48) into a long-running daemon. Per tick it: pulls the latest telemetry row, builds `SensorInputs` + `Setpoints` exactly as `replay_emit.cpp` does, calls `determine_mode` → `resolve_equipment` → `evaluate_overrides` against its persisted `ControlState`, and writes the predicted decision to a DB table.

**The only firmware-source change is one additive flag** — a `--stream` mode on `replay_emit.cpp` that blocks on stdin instead of exiting at EOF, keeping `ControlState` resident across ticks. It is gated so the existing batch path (and therefore the rule-8 CI replay-diff) is byte-for-byte unchanged. **[buildable-now]**

Why Tier 1 is the primary tier:
- It **is** the ESP32 decision code; the freeze gates already trust this exact compile. **[verified]**
- Tiny and deterministic: pure C++17, no hot-path allocation (`last_mode_reason` is a `const char*` literal). One tick is sub-millisecond; a twin is a few MB RSS.
- Trivially pinnable per ref via `git worktree add <ref>` + `g++` — exactly what `firmware-replay-diff.sh` `build_ref()` (lines 40–52) already does. **[verified]**

### 2.2 Fidelity tiers — what each captures and misses

| Tier | Build | Captures | **Misses** |
|---|---|---|---|
| **Tier 1 — native logic twin** (recommended, primary) | `g++` over `greenhouse_logic.h` from a pinned ref | FSM mode, `resolve_equipment` relay *intent*, mist_stage, override bits, all `ControlState` timers | Everything in the YAML wrapper: per-relay min-on/min-off **dwell timers**, manual-override merges, cross-relay vent interlocks, mister pulse sub-state machine |
| **Tier 2 — ESPHome host build** (selective, stage-only/on-demand) | ESPHome `host`/`linux` platform over `greenhouse.yaml` + `greenhouse/*.yaml` | Tier 1 **plus** the YAML automations, dwell timers, interlocks, manual merges | esp-idf timing/heap; still fed the same telemetry |
| **Tier 3 — QEMU ESP32 emulation** (NOT recommended for twins) | the actual `firmware.ota.bin` | full esp-idf fidelity | too heavy/slow; its marginal fidelity (heap frag, scheduler jitter) does not change the mode/relay *decision*, which is all a twin answers |

#### Tier 1's fidelity boundary — stated precisely (folding critique fixes)

The native twin predicts **FSM intent** (mode + `resolve_equipment` bitmask + mist_stage), not the final physical relay state after the YAML layer. Specifically, Tier 1 does **not** execute:

- **Per-relay dwell / min-on-min-off timers** (`RelayMeta`, `set_relay`, `can_on`/`can_off` in `controls.yaml`). `resolve_equipment()` returns *desired* relay state; the device defers transitions by `MIN_HEAT_OFF_MS` / `MIN_FAN_OFF_MS` / etc. **This is a large effect for heat** (min-off on the order of minutes). During high-churn periods (SEALED_MIST / THERMAL_RELIEF), the twin's relay intent will lead the device's actual relays by up to the relevant min-off window. **The prod-vs-reality alert threshold must therefore be ≥ `MAX(MIN_HEAT_OFF_MS, MIN_FAN_OFF_MS)` — practically several minutes — not an arbitrary "M minutes."**
- **Manual-override merges** (`manual_fan_active`, `manual_fog_active`, `vent_lock_active`). When any of these was active on the device, actual relays diverge from `resolve_equipment()` **by design** — a legitimate, non-bug explanation for prod-vs-reality disagreement that the differ must be able to suppress (see §4).
- **Mister pulse sub-state** (`mister_south`/`mister_west`/`mister_center` relays). These live in the YAML interval machine, not in `resolve_equipment()`. **Tier 1 cannot predict mister relay state.** Twin output covers the **6 climate relays only**; a `mist_active` boolean (`mist_stage > MIST_WATCH`) is the best Tier-1 proxy. Mister-timing regressions are a Tier-2-only signal.
- **SENSOR_FAULT relay lock** (`controls.yaml`). Note: `resolve_equipment()` **already returns all-false for SENSOR_FAULT mode**, so Tier 1 *correctly* predicts the climate-relay lockout in that mode; the YAML `sensor_fault_relay_lock` only additionally force-offs the manual paths Tier 1 doesn't model anyway. **Not a net-new Tier-1 gap.** (Corrects an earlier overstatement.)

#### Tier 1 input-fidelity gaps (must be documented in `twin_decisions` metadata, not silently trusted)

- **Setpoint coverage hole — the most dangerous gap.** `replay_emit.cpp` populates only ~17 `Setpoints` fields; `greenhouse_types.h default_setpoints()` defines ~50. The export (`scripts/export-replay-overrides.sh`) likewise emits only ~17 `sp_*` columns (verified: `sp_temp_high/low`, `sp_vpd_high/low`, `sp_bias_*`, `sp_*_hysteresis`, `sp_watch_dwell`, `sp_safety_*`, `sp_vpd_*_safe`, `sp_fog_escalation`, `sp_sw_fsm`, `sp_mist_backoff`, `sp_mist_s2_delay`). Every other field — `fog_rh_ceiling`, `fog_min_temp`, `fog_window_start/end`, `sealed_max_ms`, `relief_duration_ms`, `max_relief_cycles`, `sw_summer_vent_enabled`, `vent_prefer_*`, `outdoor_staleness_max_s`, `sw_dwell_gate_enabled`/`dwell_gate_ms`, `cool_*`, `direct_wet_*`, etc. — silently falls through to its `default_setpoints()` value. **If the dispatcher has tuned any of these away from the default, the twin diverges from the device for a config reason, not a code reason — producing systematic false prod-vs-reality alarm.** This is a **blocker for trusting the divergence metric as a gate**: before going live, walk every `Setpoints` field that has an active code path in `greenhouse_logic.h` against the `setpoint_snapshot` parameter keys, and either (a) wire it into the export SQL + `replay_emit.cpp` + the CSV schema, or (b) document it as "default-only, twin agrees by construction" because the dispatcher never pushes it. Add a startup assertion in the live driver that fails loudly if a `sp_*` column the current `Setpoints` struct expects is absent.
- **Three header-only fields:** `sw_night_econ_heat_suppress_enabled`, `sw_dusk_cutoff_enabled`, `feed_hold_active` exist in `default_setpoints()` and have active code paths (`night_econ_heat_suppressed()`, `past_dusk_cutoff()`, fog-permit gates) but are **not wired in the `controls.yaml` Setpoints initializer** (grep: zero hits). So the device runs their compiled defaults and the twin agrees **by accident**. Track them: the moment `controls.yaml` wires any of them, the export + parser must add the column or the twin silently diverges.
- **`econ_block` / `outdoor_rh_mode`:** these are **stateful YAML-layer computations**, not `ControlState` fields. `replay_emit.cpp` passes `econ_block = false` always. The live device's enthalpy gate is a hysteresis deadband (open ≤ `enthalpy_open_kjkg`, close ≥ `enthalpy_close_kjkg`, 10-min staleness). A live twin must **reconstruct `econ_block` from the live enthalpy series** the same way `controls.yaml` does, holding it as a twin-maintained per-instance flag — not rely on the corpus default — or it will model `DEHUM_VENT` entries the device's economiser blocks.
- **Zone VPDs (`vpd_south/west/east`):** `replay_emit.cpp` (lines 114–115) aliases them to `in.vpd_kpa` because the climate table has no per-zone columns. **Grep confirms `greenhouse_logic.h` never reads them** — they are consumed only by the mister zone-selection logic in `controls.yaml`. So for **Tier 1 they are irrelevant** (harmless avg proxy, no effect on tracked decisions). They matter only for Tier 2. Record `vpd_zone_inputs='homogenized'` in twin metadata so Grafana divergence is never misattributed.
- **`occupancy_inhibit`:** `replay_emit.cpp` leaves it at the `default_setpoints()` `false`; the device sets `occupancy_inhibit = occupancy_inhibit_enabled && greenhouse_occupied`, and `replay_overrides.cpp` forces it true. The live driver should set `sp.occupancy_inhibit` from the `occupied` column (matching the device), or the `occupancy_blocks_equipment` override bit is always false in the twin and must be excluded from comparison.
- **Local-hour timezone bug (carries from the harness, amplified live).** `replay_emit.cpp` parses `local_hour` from `ts.substr(11,2)`, but TimescaleDB exports `ts` as **UTC** while the ESP32 runs `America/Denver` (`greenhouse.yaml` line 216). UTC-vs-MDT is a 6–7h offset, and fog-window / night-suppression / dusk-cutoff gates all key on `local_hour`. **The live driver must convert to America/Denver** (e.g. SQL `EXTRACT(HOUR FROM c.ts AT TIME ZONE 'America/Denver')` passed as a column). This is a blocker for any hour-gated decision divergence being trustworthy.

### 2.3 Time-stepping and carried state

**Telemetry-driven `dt_ms`, with the device's 5 s cap added for the live path.** The harness derives `dt_ms` from consecutive `ts` deltas and resets `state = initial_state()` on a gap > 600 s (the off-device analog of an ESP32 reboot — correct and preserved). But the harness does **not** apply the device's `dt_ms` clamp to 5000 ms; the device steps a 30 s gap as six 5 s ticks while the harness passes one 30000 ms tick — numerically different for timer-threshold crossings. **The live twin should apply `dt_ms = min(dt_ms, 5000)` to match the device.** (The corpus tool may optionally adopt the cap too; its current results have a known minor gap-divergence.)

**Carried state is exactly the `ControlState` struct** (`greenhouse_types.h` lines 182+): `sentinel`, `mode`/`mode_prev`, `mist_stage`, the `uint32_t` timers, `relief_cycle_count`, the latch booleans (`dry_override_active`, `heat2_latched`, `override_summer_vent`, `vent_mist_assist_active`), and `last_mode_reason`. The `STATE_SENTINEL = 0xBEEF0042` guard self-heals corrupt state on-device and equally in the twin (the twin starts from `initial_state()` which sets the sentinel).

**In-memory only, re-init on restart — with one correction to the original rationale.** *Climate-control* globals are all `restore_value: no`, so the device cold-starts `ControlState` from `initial_state()` every boot; the twin matches by holding state in memory and re-initing on restart. **Correction (critique):** `globals.yaml` has **22 `restore_value: yes`** entries — all *irrigation-scheduling tunables* (`irrig_wall_start_hour`, `irrig_wall_duration_min`, `irrig_enabled`, …), not climate state. `ControlState` itself is never persisted, so the climate conclusion holds, but the basis must be stated precisely: *all climate-control (`ControlState`/climate-`Setpoints`) globals are `restore_value: no`; 22 irrigation tunables persist in NVS.* Consequence: the twin's `Setpoints` must be populated from the **live DB / dispatcher state**, not hardcoded `initial_values`, to reproduce scheduling behavior the device retained across its last reboot.

**Cold-start / warm-up divergence (honest caveat).** A freshly-spawned or restarted twin begins at `initial_state()` while the device has weeks of accumulated state. Until convergence, prod-vs-reality is unreliable. Time-based accumulators converge within a few dwell cycles; **path-dependent bits do not always** — `dry_override_active` (logic comment: cannot be reconstructed post-hoc because R2-3 mutates `vpd_watch_timer_ms` in the same cycle), `heat2_latched`, `mist_backoff_timer_ms`, `vent_mist_assist_active` need an unbroken chain. Mitigations, in order of preference:
1. **Best-effort warm-start:** seed `ControlState` from the last ~10 min of `equipment_state` + `system_state` (mist_stage from fog/mist relays, heat latches from heat-relay history, `dry_override_active` hinted from `mode_reason`).
2. **Suppress alerts during warm-up:** write a `twin_warmup_until` timestamp at repin (`= now + ~30 min`, sized to `≥ MAX(sealed_max_ms + relief_duration_ms·max_relief_cycles)`, ~20–30 min worst case) and gate divergence alerts off until then.
3. Size the rolling-window warm-up window (if using the micro-batch model, §3) to **≥ 4× `sealed_max_ms`** so an in-progress SEALED_MIST episode at the window's left edge does not produce wrong mist_stage for the first ~10–15 min of each batch.

### 2.4 The Mode → ClimateAction column (correcting "one-liner")

The operator-legible label is `ClimateAction` (11 values: `SENSOR_FAULT, SAFETY_COOL, SAFETY_HEAT, HEAT, IDLE, VENT_COOL, VENT_COOL_MIST_ASSIST, VENT_COOL_FOG_ASSIST, SEALED_HUMIDIFY, SEALED_FOG, DEHUM_VENT`), which is what `climate_action_log.climate_action` stores. The twin currently emits the 8-value `Mode` enum. **These are different vocabularies and the mapping is context-dependent**, not a bijection: `SEALED_MIST` → `SEALED_HUMIDIFY`|`SEALED_FOG` (by `mist_stage`); `VENTILATE` → `VENT_COOL`|`VENT_COOL_MIST_ASSIST`|`VENT_COOL_FOG_ASSIST` (by assist state); `IDLE`+heat → `HEAT`.

**The right fix is not duplicating YAML logic — the mapping already exists in the header:** `effective_climate_action_for_mode(...)` (`greenhouse_logic.h` line 590) and `describe_effective_climate_decision(...)` (line 662). The `--stream` extension should call `describe_effective_climate_decision()` after `resolve_equipment()` and emit a `climate_action` column. Then the divergence join is a straight `twin.climate_action = climate_action_log.climate_action`, no translation table. **[buildable-now, single additive change.]** Until that column exists, drop `climate_action` comparison rather than approximate it with a broken mapping.

---

## 3. Dev / stage / prod topology + telemetry feed

### 3.1 Three twins, one feed, comparable outputs

| Twin | Ref it pins | How to resolve it (verified values) | Rebuild trigger | Represents |
|---|---|---|---|---|
| **dev** | in-development branch HEAD (or uncommitted worktree, mirroring `firmware-replay-worktree-diff.sh`) | `firmware/*` branch HEAD | every push to dev branch | what the firmware agent is actively changing |
| **stage** | baked OTA candidate | `pending-fw-version.txt` → `2026.5.23.1711.63c59c4.dirty`; build from the archived `source-snapshot/` | when a candidate is promoted to pending | pre-deploy shadow of the next OTA |
| **prod** | currently-deployed last-good | `last-good.version` → `2026.5.17.1849.9353df5`; `last-good.metadata.env` gives `source_sha=9353df58…`, `source_dirty=0` | when `last-good.*` changes after an OTA promotion | the firmware physically on the ESP32 right now |

**Per-ref build precedence (folding critique fixes):**
- **dev / prod (ref mode):** `git worktree add --force <worktree> <ref>` + `g++`, identical to `firmware-replay-diff.sh`. **Prod git-ref build is faithful only when `source_dirty=0`** — the current last-good is `source_dirty=0`, so this works. Add a guard: **if `source_dirty=1`, require the artifact `source-snapshot` build path and fail if it's absent.** Also: pin prod to the resolved **SHA** (or a `deployed/last-good` annotated tag created at each promotion) rather than the branch name (`coordinator/occupancy-quiet-bridge-2026-05-17`), so the ref survives branch rebase/delete; pre-build, assert `git cat-file -e <SHA>^{commit}`.
- **stage (artifact-snapshot mode, mandatory for dirty builds):** the current candidate is `…63c59c4.dirty` with `source_dirty=1`. **A git-SHA checkout of `63c59c4` reproduces the base commit, NOT the dirty binary that was flashed.** Only the archived `source-snapshot/` is faithful. So for stage, **`source-snapshot` is the mandatory primary, not an option**, validated against `SOURCE_SHA256SUMS`. Honest labeling: `pending-fw-version.txt` is "the most recently compiled candidate," **not necessarily a freeze-rule-qualified 48h bake** — reserve "baked OTA candidate" for a version that has passed the bake.

  **Blocker (critique):** `scripts/archive-firmware-artifacts.sh` copies `firmware/greenhouse.yaml`, `firmware/greenhouse`, and `firmware/lib` into `source-snapshot` — **but NOT `firmware/test/`**. So an artifact-snapshot build has the artifact's `lib/` but would compile **HEAD's `replay_emit.cpp`**, which can drift from the artifact's interface. Fix (coordinator-scope): **add `firmware/test/replay_emit.cpp` + `firmware/test/invariants.h` to the snapshot manifest**, and have the stage Dockerfile compile `source-snapshot/firmware/test/replay_emit.cpp` against `source-snapshot/firmware/lib/` — both from the same artifact.

  **Path note (critique):** promoted artifacts live in the **main worktree** (`/mnt/iris/verdify/firmware/artifacts/<ver>/`), not the firmware worktree. The stage build context must root at `/mnt/iris/verdify` (or bind-mount that artifacts dir read-only); the firmware worktree only holds the latest local dirty build.

### 3.2 The defining property: identical inputs → directly comparable outputs

All three twins consume **one canonical row stream** (same `ts` sequence, same setpoints, same occupancy) produced by a **single shared feed adapter**. A single adapter (not three independent DB samplers) is non-negotiable: it removes any chance the twins disagree because they sampled the DB at three different instants, and it guarantees their `dt_ms` / `ControlState` evolution differ *only* for firmware-logic reasons. The topology is literally **"run the existing dual-ref `firmware-replay-diff` three-way, continuously, against the live feed instead of the frozen corpus."** All three run `REPLAY_EMIT_FORCE_FSM=1` (the prod-aligned default) and `DWELL_ENABLED=0` (production has `sw_dwell_gate_enabled=false`) explicitly set in the container env, so neither env var silently diverges the twin from the device.

### 3.3 Live feed — reuse the export SQL, do not reimplement it

**Provenance (verified from `export-replay-overrides.sh`):** only `climate` is periodic (~60 s heartbeat — the driver). Everything else (`equipment_state`, `system_state`, `setpoint_snapshot`, `setpoint_changes`) is **event-driven**, written only on change, and pulled into each row by a `LATERAL ... ts <= c.ts ORDER BY ts DESC LIMIT 1` **forward-fill as-of join** keyed on the climate heartbeat. The live feed must replicate that *exact* as-of semantics, not naively zip streams. The cleanest way to guarantee corpus/live row-shape identity is to **factor the inner SELECT of `export-replay-overrides.sh` into a `WHERE`-parameterizable form** (`SINCE_TS` for the live tail, `FROM_TS/TO_TS` for targeted backtests) so one SQL body serves both batch export and live adapter. **[buildable-now, pure refactor.]** Read path is the established `docker exec verdify-timescaledb psql -U verdify -d verdify -tAc "…"`.

**Effective-setpoint source = `setpoint_snapshot` (the firmware `cfg_*` readback), not `setpoint_changes`.** This is already what the export does and it is load-bearing: `setpoint_snapshot` is the device's *confirmed* effective value (the `cfg_*` echo; `setpoint_confirmation_monitor` alerts if the ESP32 doesn't confirm within 5 min), whereas `setpoint_changes` is dispatcher *intent*. Using the readback means a dropped/corrupted push (exactly what freeze-rule 6 guards) is *visible* — the twin sees the stale value the device actually held and still matches it. Using intent would mask push-corruption. **No change needed; this design just pins the rationale.**

**Settling delay = 5 min, not 60 s (critique).** The adapter must lag the live edge so event-driven edges land before the as-of join reads them. Because a setpoint confirmation can echo into `setpoint_snapshot` up to **5 min** after the push, a 60 s lag would as-of-join a stale snapshot and produce spurious setpoint-timing divergence. Set the settling delay to **≥ 300 s** (or lag the setpoint LATERAL joins explicitly with `ts <= c.ts - interval '5 min'`).

**Delivery into the harness — two wirings:**
1. **Micro-batch (recommended launch, zero harness change):** every ~30 s the adapter writes a cumulative window CSV and re-runs the stock `replay_emit` from `initial_state()`; only the newest rows are published. Window length **≥ 4× `sealed_max_ms`** (§2.3) to bound warm-up error. **Better: checkpoint the last `ControlState` at each batch end and feed it as the next batch's initial state** — this makes micro-batch equivalent to `--follow` and eliminates warm-up uncertainty.
2. **`--follow` (steady-state optimization):** the `--stream` flag (§2.1). **If pursued, gate it behind a separate binary target (`replay_emit_follow`) or a build-time conditional** so the stock `replay_emit` used by CI replay/invariant targets is untouched — otherwise a `--follow` bug could break the rule-8 gate.

**MQTT is explicitly secondary.** Climate/equipment/setpoints **do not flow over MQTT** — the firmware has **no `mqtt:` block** [verified] and speaks the ESPHome native API; the dispatcher pushes setpoints via `aioesphomeapi` (`ingestor/esp32_push.py`), and the ingestor *reads* MQTT only for occupancy/irrigation feedback. The authoritative live feed is **TimescaleDB**. MQTT is at most a lower-latency occupancy nudge; **start DB-only** (the as-of join already pulls occupancy from `system_state`).

### 3.4 Replay feed — corpus / historical backtest

Already exists: `firmware/test/data/replay_overrides.csv.gz` is the full corpus; `export-replay-overrides.sh [days]` produces historical windows; `make replay-corpus-refresh` refreshes with a <5% size-regression guard. Add `[FROM_TS, TO_TS]` windowing for incident backtests. **Determinism parity is itself a feed-correctness invariant:** a backtest over `[t0, t1]` and the live tail over the same window must produce identical twin output (after the SQL refactor makes them one body). Replay feed = regression/backtest plane; live feed = shadow plane; both drive identical harness code.

---

## 4. Divergence detection + upgrading the freeze rules

### 4.1 The comparison keys

The decision tuple per `(twin_ref, ts)` is exactly the actuator subset `firmware-replay-diff.sh` (lines 75–93) already treats as **hard-fail** — `mode FS relay×6 FS mist_stage` — with `reason FS override_bits` as **soft/diagnostic**. `RelayOutputs` has exactly the 6 climate bools, so the relay vector is closed.

Three comparison streams, each a windowed SQL join on `ts` (the awk column-split becomes `IS DISTINCT FROM` over the decision columns):

- **(A) stage-vs-prod (twin-vs-twin):** self-join `twin_decisions` on `ts` where `twin_ref='stage'` vs `'prod'`. Identical to today's `firmware-replay-diff` semantics, streamed over live telemetry. **This is the live rule-8.**
- **(B) prod-vs-reality (twin-vs-device):** join `twin_decisions(twin_ref='prod')` against the device's observed relay truth. Two representations agree on naming: the export's forward-filled `eq_fog/vent/fan1/fan2/heat1/heat2` from `equipment_state`, and `climate_action_log.relay_truth` JSONB (keys map 1:1 to `RelayOutputs` + mister zones). **Compared on the 6-relay vector + `mist_active` proxy**, with `mode_reason`/`greenhouse_state` as a soft secondary check — *the device does not log the `Mode` enum directly* (an asymmetry, not a choice). **This is the novel firmware-vs-reality signal.**
- **(C) dev-vs-prod:** as (A) for the dev branch — a live "how far has my branch drifted on today's real conditions" signal.

**Two separation rules the differ must honor (critique):**
1. **Prediction vs invariant inputs.** The twin's predicted relay output must be derived **only from sensor inputs + setpoints**, never from the observed `eq_*` columns. The current `replay_emit.cpp` correctly does not read `eq_*` when computing mode/relay — name this explicitly so a future feed change doesn't feed observed relay state back into the prediction path and mask a regression. The invariant runner checks the **twin's own emitted output** for logical coherence (it already reads `replay_emit`'s TSV, not the raw CSV); a *separate* invariant pass over observed device state is a different signal.
2. **Override suppression.** Prod-vs-reality rows where a **manual override** (`manual_fan_active`/`manual_fog_active`/`vent_lock_active`) was active are legitimate divergence, not a bug. The differ must categorize/suppress them. **This requires the override state to be logged** (a `system_state` entity or a `climate_action_log` field). If it isn't today, that's a cross-agent schema change (coordinator) — flag it before trusting stream (B).

**Time-bucket join, not strict equality (critique):** the twin's rolling-window output and `equipment_state` never share exact timestamps. Join on a bucket / range tolerance (`DATE_TRUNC('minute', …)` or ±30 s), and threshold prod-vs-reality on **sustained** disagreement (M ≥ relay min-off window, §2.2) to absorb the ~5 s control vs ~20 s `climate_action_log` vs up-to-one-sample `eq_*` lag.

### 4.2 Conditioning + sinks

Conditioning dimensions (all present in the feed): **by mode** (8 values), **by daypart** (`local_hour`, MDT-corrected), **by outdoor band** (`<32 / 32–85 / >85` °F, tied to stress-window rule 5), **per-relay** (isolates "only fog differs" from "heat differs" — load-bearing for plant-safety triage). Drift cause-class from the pattern: disagreement only on `fog` only in `SEALED_MIST` → suspect fog-gate/YAML override (the override-bits column narrows it); all relays at one `ts` → suspect input skew / sensor fault, not a logic bug.

Sinks: a `firmware_twin_divergence` summary table + Grafana panels (rolling stage-vs-prod relay-disagreement %, prod-vs-reality drift %, per-relay heatmap, by-mode bar). Grafana already reads TimescaleDB on `verdify-internal`; anonymous Viewer is on, so the board is lab-site-visible like the rest. **Grafana unified alerting is disabled** (`GF_UNIFIED_ALERTING_ENABLED: "false"`), so alerts are computed in SQL / by ingestor's `alert_monitor` and written to `alert_log` — which also lets them feed the deploy preflight.

### 4.3 How the freeze rules become live/continuous

- **Rule 8 (`firmware-replay-diff`, THRESHOLD_PCT=0).** Today: candidate vs merge-base over the 8-month frozen corpus, PR-time only. Upgrade: **add a live agreement gate** — stage-vs-prod `relay_disagree_pct ≤ THRESHOLD_PCT` (default 0) sustained over N hours of *today's* telemetry. The corpus diff **stays** (covers conditions the recent window lacks — winter freezes in summer); the live diff **adds** "agrees under today's actual conditions." Both must pass; the existing `REPLAY_DIFF_THRESHOLD_PCT:` PR-body override extends to the live gate.
- **Rule 3 (48-h bake).** Today: `firmware-deploy-preflight.sh` checks the **mtime** of `last-good.ota.bin` — a clock check on the *previous* binary, not a behavioral check on the candidate. Upgrade: bake = **"48 continuous hours of stage-twin agreement under real conditions, measured before deploy."** The bake clock advances only while stage-vs-prod stays within threshold AND no `severity='critical'` twin alert is open; a divergence spike **resets the clock**. Preflight gains a query: `min(window_start) WHERE comparison='stage_vs_prod' AND relay_disagree_pct ≤ threshold` must span ≥48 h ending now. **Precision (critique):** this bake criterion is **stage-twin FSM output vs prod-twin FSM output** (both are FSM intent). A separate, stronger "FSM vs actual relays" definition would read `equipment_state` and account for dwell/interlock effects Tier 1 doesn't model — be explicit about which is measured. **Trigger (critique):** the stage twin must repin to the candidate SHA **when the candidate binary is produced (post-merge / `make firmware-deploy` accepted)**, not when the operator initiates deploy — otherwise the 48 h clock can't have started and the gate is circular.
- **Rule 9 (required artifacts + coordinator independent replay).** The continuously-running stage twin **is** an independent reproduction: separate container, separate process, separate checkout of the candidate SHA, on the same live data. The CI hook attaches the twin's corpus+live diff artifact automatically; the coordinator step collapses to "confirm the stage-twin container runs the right SHA (`twin_decisions.twin_ref`) and its divergence artifact is green." **Honest limit (critique):** this replaces only the manual *replay re-run*. The **Iris planner concurrence brief for interface-level changes** (new `ClimateAction` value, changed `mode_reason` string, new field) remains a human step — the twin validates control-logic consistency, not planner/MCP interpretation. The CI artifact should *flag* interface-level changes (enum-ordinal or string-literal deltas) separately to prompt the brief.
- **Rule 1 interaction (cleanest integration point).** `firmware-deploy-preflight.sh` already blocks deploy on `severity IN ('critical','high')` rows in `alert_log`. So the divergence detector plugs into the existing freeze gate with **no new gate plumbing** — it just writes the right alert rows: stage-vs-prod over-threshold during bake → `high` (deploy blocker); sustained prod-vs-reality → `critical` candidate (which then blocks the *next* OTA — a misbehaving live device should block shipping on top of it); twin staleness (twin stopped emitting) → `high`, because a dark twin silently disables the live gates.

### 4.4 CI hook

Extend the existing `.github/workflows/ci.yml` `firmware-replay-diff` job (it already builds OLD/NEW `replay_emit` via worktrees, runs the corpus diff at `THRESHOLD_PCT=0`, and parses `REPLAY_DIFF_THRESHOLD_PCT:`):
1. **Build the twin per PR** — the job *already* builds the candidate `replay_emit` from HEAD; **that binary is the dev/stage twin.** Tag and upload it by HEAD SHA so the bake pipeline promotes the *same* binary (build-once, run-as-twin).
2. **Corpus diff stays the blocking PR gate.** The **live** diff cannot run in CI — GitHub-hosted runners have **no route to the prod TimescaleDB** (`127.0.0.1:5432`, localhost-only). CI **fetches a summary** of the stage twin's recent stage-vs-prod divergence (published artifact / read-only endpoint) and **reports** it; live generation happens on the prod host. **No-data handling (critique):** if the stage twin hasn't run this SHA yet, emit a warning annotation ("live bake gate enforced at deploy time") and **do not block** — the corpus diff remains the blocking gate at PR time; the live bake is enforced at `firmware-deploy-preflight.sh`.
3. **Post the combined artifact** (corpus %, live 48 h %, per-relay + by-mode breakdown, bake-clock status) in the format reviewers already know from the `firmware-replay-diff.sh` summary — replacing manual rule-9 artifact assembly.

---

## 5. Containerization + observability + read-only safety

### 5.1 Compose sketch (overlay; coordinator-owned infra)

```yaml
# docker-compose.twins.yml  — overlay; file a PR, do not edit autonomously
networks:
  verdify-twin:
    name: verdify-twin
    internal: true          # SAFETY L3: no route off the Docker host → cannot reach ESP32 LAN/OTA
  verdify-internal:
    external: true          # reuse existing net for DB-only access

x-twin-common: &twin-common
  restart: unless-stopped
  read_only: true                       # SAFETY L5
  cap_drop: [ALL]
  security_opt: [no-new-privileges:true]
  user: nonroot
  tmpfs: [/tmp]
  networks: [verdify-twin, verdify-internal]   # never verdify-proxy
  environment:
    DB_HOST: timescaledb
    DB_USER: twin_ro                    # SAFETY L2: SELECT telemetry + INSERT twin_decisions only
    DB_PASS: ${TWIN_RO_PASSWORD}
    REPLAY_EMIT_FORCE_FSM: "1"          # prod-aligned (intentional)
    DWELL_ENABLED: "0"                  # production sw_dwell_gate_enabled=false
    TZ_LOCAL: America/Denver            # §2.2 local-hour correction
    # NO api_encryption_key, NO ota_password, NO mqtt creds  ← SAFETY L1/L4
  deploy: { resources: { limits: { memory: 96M, cpus: "0.10" } } }

services:
  twin-prod:
    <<: *twin-common
    container_name: verdify-twin-prod
    build: { context: ., dockerfile: twin/Dockerfile,
             args: { TWIN_ENV: prod, TWIN_REF: ${TWIN_PROD_SHA}, TWIN_SOURCE: ref } }
    depends_on: { timescaledb: { condition: service_healthy } }
  twin-stage:
    <<: *twin-common
    container_name: verdify-twin-stage
    build: { context: /mnt/iris/verdify, dockerfile: twin/Dockerfile,   # main worktree: artifacts live here
             args: { TWIN_ENV: stage, TWIN_REF: ${TWIN_STAGE_VERSION}, TWIN_SOURCE: artifact-snapshot } }
    depends_on: { timescaledb: { condition: service_healthy } }
  twin-dev:
    <<: *twin-common
    container_name: verdify-twin-dev
    build: { context: ., dockerfile: twin/Dockerfile,
             args: { TWIN_ENV: dev, TWIN_REF: ${TWIN_DEV_SHA}, TWIN_SOURCE: ref } }
    depends_on: { timescaledb: { condition: service_healthy } }
```

**Image (Tier 1):** multi-stage. Build stage = `gcc:13-bookworm`, compiles `replay_emit.cpp` against the pinned `firmware/lib` (ref mode = `git checkout`; artifact-snapshot mode = `cp` from `firmware/artifacts/<ver>/source-snapshot/firmware/{lib,test}` + `sha256sum -c SOURCE_SHA256SUMS` **run from inside the artifact dir** so the relative paths resolve). Runtime stage **must include a Python interpreter if the driver is Python** — `gcr.io/distroless/cc-debian12` has no Python, so use `python:3.12-slim-bookworm` hardened with the same `read_only`/`cap_drop`/`no-new-privileges` flags, **or** write the driver as a compiled binary and keep distroless, **or** a `python:3.12-slim` sidecar talking to the distroless `/twin` over a shared-tmpfs FIFO. The driver (`twin/run_twin.py` or equivalent) is **new twin glue, not firmware**: poll newest unseen `climate` row → assemble one input line via the shared SQL body → feed the resident `--stream` process → parse the decision TSV → `INSERT` into `twin_decisions`.

**Lifecycle / repinning.** Twins are a projection of three already-well-defined state changes: PR merge / `make firmware-deploy` accepted (writes `pending-fw-version.txt` + archives `<ver>/`) / `make firmware-promote-last-good` (updates `last-good.*` after bake). Auto-rollback collapses stage onto prod. A `twin-pinwatch` mechanism repins the affected twin on change. **Critique:** do **not** give a pinwatch container an open `/var/run/docker.sock` mount (host-root equivalent). Prefer a **host systemd-path unit** that runs `docker compose up -d --build twin-<env>` on pin-file change, or a docker-socket-proxy restricted to `restart`/`build`. Each `twin_decisions` row carries `twin_ref` for full provenance.

### 5.2 Observability schema

```sql
-- coordinator-reviewed migration (db/migrations/ is serialized shared territory)
CREATE TABLE twin_decisions (
    ts            timestamptz NOT NULL,
    twin_env      text NOT NULL,        -- 'dev' | 'stage' | 'prod'
    twin_ref      text NOT NULL,        -- git sha / fw_version the image was pinned to
    input_ts      timestamptz NOT NULL, -- the climate row that drove this decision
    mode          text NOT NULL,
    climate_action text,                -- via describe_effective_climate_decision (§2.4)
    mist_stage    int  NOT NULL,
    relay_fog boolean NOT NULL, relay_vent boolean NOT NULL,
    relay_fan1 boolean NOT NULL, relay_fan2 boolean NOT NULL,
    relay_heat1 boolean NOT NULL, relay_heat2 boolean NOT NULL,
    mode_reason   text, override_bits int NOT NULL,
    twin_metadata jsonb,                -- vpd_zone_inputs='homogenized', warmup flags, etc.
    CONSTRAINT twin_env_chk CHECK (twin_env IN ('dev','stage','prod'))
);
SELECT create_hypertable('twin_decisions', 'ts');
CREATE INDEX ON twin_decisions (twin_env, ts DESC);
```

Column set is a 1:1 map of `replay_emit`'s TSV (+ `climate_action`, `twin_metadata`), so the driver does no semantic translation. `firmware_twin_divergence` (summary) holds windowed disagreement counts/pcts, per-relay breakdown, and the `by_mode`/`by_daypart`/`by_outdoor_band` jsonb conditioning + `worst_examples`.

### 5.3 The read-only / no-actuation safety model

The twin is read-only **by construction**, in independent layers, each individually sufficient. The **only two real actuation paths** [verified]: (1) **aioesphomeapi** (encrypted; `client.switch_command`/`number_command` in `ingestor/esp32_push.py`, key `!secret api_encryption_key`); (2) **OTA** (`esphome upload`, `!secret ota_password`). **MQTT is not a relay-command path** — the firmware has no `mqtt:` block and the ingestor only *subscribes*.

- **L1 — no credentials, no client code.** The Tier-1 image has no `secrets.yaml`, no API encryption key, no OTA password, no aioesphomeapi/ESPHome at all. `replay_emit.cpp`/`replay_overrides.cpp` have **zero networking includes**; `resolve_equipment()` returns a `RelayOutputs` value struct that is only ever `printf`'d. The two actuation paths are uninvokable because the code and credentials don't exist. **This is the primary, structural guarantee.**
- **L2 — read-only DB role.** `twin_ro`: `SELECT` on `climate, equipment_state, system_state, setpoint_snapshot, setpoint_changes, climate_action_log`; `INSERT` on **only** `twin_decisions`/`firmware_twin_divergence`; **no** UPDATE/DELETE anywhere, **no** write on any control-plane table. Container entrypoint asserts at startup that the role cannot write a control table (`BEGIN; INSERT INTO equipment_state …; ROLLBACK` must raise a permission error).
- **L3 — network isolation.** `verdify-twin` is `internal: true` → no route off the Docker host, so even a smuggled key + client cannot reach the ESP32 at `192.168.10.111` (API `:6053` / OTA). Twins attach to `verdify-internal` only for TimescaleDB/Grafana; **never `verdify-proxy`** (no public surface).
- **L4 — MQTT.** Default: twins get **no broker credential** (DB-only); with `allow_anonymous false`, a credential-less connect is refused. If live occupancy is ever wanted, add a **subscribe-only ACL user** (`mqtt/acl`, new, coordinator-owned) with `topic read` only and no `write` on any topic — defense-in-depth so a future command topic can't be hit even by a misconfigured credential.
- **L5 — container hardening.** `read_only: true`, `cap_drop: [ALL]`, `no-new-privileges`, `user: nonroot`, `tmpfs: [/tmp]`, no shell/package manager in the image.

**Restated structurally:** the twin cannot actuate because it holds neither actuation credential, sits on an `internal` network with no route to the device, can only `INSERT` into observability tables, and has no MQTT write ACL — four independently sufficient guarantees, all simultaneously true.

---

## 6. Phased rollout

**Phase 0 — harness extensions (firmware-owned, additive, no behavior change).**
Add the gated `--stream`/`replay_emit_follow` mode; add the `climate_action` column via `describe_effective_climate_decision()`; refactor the export SQL inner SELECT to be `SINCE_TS`/`FROM_TS/TO_TS`-parameterizable; add the local-hour MDT correction + `dt_ms` 5 s cap in the live driver. Verify the existing `firmware-replay-diff` / `firmware-invariants` / `test-firmware` outputs are byte-identical (rule 8 stays green). **[buildable-now]**

**Phase 1 — MVP: prod twin shadowing live telemetry + divergence dashboard.**
One `twin-prod` container pinned to `last-good` (`source_dirty=0`, ref build), the shared feed adapter (5-min settling, micro-batch with `ControlState` checkpointing), `twin_decisions` table, and the Grafana **prod-vs-reality** panel. Before trusting any alarm: close the **setpoint-coverage gap** (§2.2) for every dispatcher-pushed field, reconstruct `econ_block` live, log/handle manual-override suppression, and set the prod-vs-reality threshold to the relay min-off window. Deliverable: a continuous "does the deployed firmware behave as its own logic predicts" dashboard. **[buildable-now, with the §2.2 gap-closure as gating work]**

**Phase 2 — stage pre-deploy bake gate.**
Add `twin-stage` (artifact-snapshot build, after the archive script ships `firmware/test/` in the snapshot) + the **stage-vs-prod** live diff. Repin stage at candidate-build time. Wire the live 48 h bake query into `firmware-deploy-preflight.sh` (start as non-blocking operator context, then promote to a hard gate once the false-positive rate from §2.2 gaps is demonstrably zero). **[buildable-now-with-caveats]**

**Phase 3 — dev CI live-diff.**
Add `twin-dev` + the CI summary-fetch step (corpus diff blocking; live summary informational with no-data tolerance) + the combined PR artifact + the interface-change flagger feeding the rule-9 planner-concurrence prompt. **[buildable-now-with-caveats]**

**Deferred / aspirational:** Tier 2 ESPHome host build (needs a new `greenhouse-host.yaml` platform overlay stubbing gpio/wifi/api/sntp and adapting timing-sensitive YAML — *not* a re-use of `firmware-esphome-worktree.sh`, which builds for `esp32dev`); QEMU forensic tool (reserve only); zone-VPD fidelity (needs new sensor columns in `climate`).

---

## 7. Proposed backlog items

*Listed here only — the shared `docs/BACKLOG.md` / `docs/backlog/*.md` are NOT edited by this design (coordinator territory).*

| ID | Priority | Owner | Action |
|---|---|---|---|
| TWIN-1 | P1 | firmware | Add gated `--stream`/`replay_emit_follow` mode to `replay_emit.cpp` (resident `ControlState`, stdin loop); prove stock batch path / rule-8 CI output byte-identical. |
| TWIN-2 | P1 | firmware | Emit `climate_action` column via `describe_effective_climate_decision()` (`greenhouse_logic.h:662`); no translation table. |
| TWIN-3 | P0 | firmware | **Close the setpoint-coverage gap:** walk every `Setpoints` field with an active `greenhouse_logic.h` path vs `setpoint_snapshot` keys; wire dispatcher-pushed fields into export SQL + `replay_emit.cpp` + CSV schema; document default-only fields; add startup assertion on missing `sp_*` columns. **Gates trusting the divergence metric.** |
| TWIN-4 | P1 | firmware | Live driver: America/Denver `local_hour` correction; `dt_ms = min(dt_ms, 5000)`; reconstruct `econ_block` from live enthalpy series; set `occupancy_inhibit` from `occupied`. |
| TWIN-5 | P1 | firmware | Refactor `export-replay-overrides.sh` inner SELECT to `SINCE_TS` / `FROM_TS,TO_TS` parameterization (one SQL body for batch + live); add ±tolerance/bucket join + 5-min settling lag. |
| TWIN-6 | P1 | coordinator | Migration: `twin_decisions` + `firmware_twin_divergence` hypertables; `twin_ro` role (SELECT telemetry + INSERT observability only). Serialized. |
| TWIN-7 | P1 | coordinator | Add `firmware/test/replay_emit.cpp` + `invariants.h` to `archive-firmware-artifacts.sh` `source-snapshot` manifest, so stage artifact-snapshot builds compile a self-consistent harness. |
| TWIN-8 | P1 | coordinator | `docker-compose.twins.yml` overlay (3 twins + `verdify-twin internal` net + repin mechanism); twin image Dockerfile review; pick runtime base (Python-slim hardened vs distroless+sidecar); **no open docker.sock** (systemd-path or socket-proxy). |
| TWIN-9 | P1 | firmware→coordinator | Extend `firmware-deploy-preflight.sh`: live stage-vs-prod 48 h bake query (rule 3) + live stage-vs-prod agreement query (rule 8); start non-blocking, promote to hard gate after false-positive rate is zero. |
| TWIN-10 | P2 | firmware→ingestor | `requested-by: firmware` PR adding `alert_monitor` rules: stage-vs-prod over-threshold → `high`; sustained prod-vs-reality → `critical`; twin staleness → `high`. |
| TWIN-11 | P2 | firmware→ingestor/coordinator | Log manual-override state (`manual_fan/fog_active`, `vent_lock_active`) to `system_state`/`climate_action_log` so the differ can suppress override-caused prod-vs-reality divergence. Schema-touch → coordinator. |
| TWIN-12 | P2 | coordinator | CI: tag+upload candidate `replay_emit` as the twin image artifact; add live-summary-fetch step (no-data tolerant, informational); add interface-change flagger for rule-9 planner-concurrence prompt. |
| TWIN-13 | P2 | firmware/web | Grafana divergence dashboard JSON (prod-vs-reality, stage-vs-prod, per-relay heatmap, by-mode/daypart/outdoor-band); provisioning lands via web/coordinator. |
| TWIN-14 | P3 | firmware | Warm-start: seed `ControlState` from last ~10 min `equipment_state`/`system_state`; write `twin_warmup_until`; suppress alerts during warm-up. |
| TWIN-15 | P3 | firmware | Stage/prod ref hygiene: `deployed/last-good` annotated tag at promotion; `source_dirty=1` → force artifact-snapshot build + fail if snapshot absent; `git cat-file -e` pre-build assert. |
| TWIN-16 | P4 | firmware | Tier 2 ESPHome host build (`greenhouse-host.yaml` overlay) for stage-only relay-interlock/dwell/mister fidelity. Aspirational. |
| TWIN-17 | P4 | coordinator | Optional `mqtt/acl` subscribe-only twin user (defense-in-depth) if live MQTT occupancy is ever required. |

---

## 8. Open questions / decisions for the operator (Jason)

1. **Hard gate vs operator context for the live bake (rule 3).** Should the 48 h *behavioral* bake be a hard `firmware-deploy` blocker, or non-blocking operator context (like the rule-5 stress-window warning) until we trust the false-positive rate? Recommendation: non-blocking through Phase 2, hard gate after the §2.2 setpoint-coverage gap is closed and stage-vs-prod false positives are demonstrably zero.
2. **prod-vs-reality `critical` auto-blocking the next OTA.** Sustained prod-vs-reality drift writing a `critical` alert will, via rule 1, freeze the next OTA. Is that the desired coupling, or should prod-vs-reality cap at `high`/manual-triage to avoid a misbehaving-device signal also blocking the fix for it?
3. **Setpoint-coverage closure scope (TWIN-3).** Wire *all* dispatcher-pushed `Setpoints` fields now, or only the climate-critical subset (`fog_window_*`, `fog_rh_ceiling`, `fog_min_temp`, `sealed_max_ms`, `relief_duration_ms`, `max_relief_cycles`, the `sw_*`/`cool_*` booleans) first? This determines how soon the divergence metric is trustworthy.
4. **Repin mechanism.** Host systemd-path unit (no container privilege) vs docker-socket-proxy sidecar? The systemd path is simplest given the existing host footprint and avoids the docker.sock attack surface.
5. **Stage labeling / dirty builds.** Current pending candidate is `…63c59c4.dirty` (`source_dirty=1`). Confirm stage always builds from `source-snapshot` for dirty candidates and that "baked OTA candidate" is reserved for bake-qualified versions, not just the latest `pending-fw-version.txt`.
6. **Manual-override logging (TWIN-11).** Is logging override state to `system_state`/`climate_action_log` acceptable as a schema add, given it's required for prod-vs-reality stream (B) to distinguish legitimate override divergence from firmware bugs?
7. **Runtime base image.** Python-slim hardened, compiled-driver + distroless, or distroless `/twin` + Python sidecar over a shared-tmpfs FIFO?

---

## Key files referenced (all absolute)

- `/mnt/iris/verdify-worktrees/firmware/firmware/test/replay_emit.cpp` — twin core; TSV header = `twin_decisions` schema; stateful per-row loop; UTC `local_hour` bug (substr 11,2); zone-VPD alias; `REPLAY_EMIT_FORCE_FSM` default
- `/mnt/iris/verdify-worktrees/firmware/firmware/test/replay_invariants.cpp` + `invariants.h` — 16-invariant suite (check #13 deferred)
- `/mnt/iris/verdify-worktrees/firmware/firmware/test/replay_overrides.cpp` — override forward-sim; `occupancy_inhibit=true` reference
- `/mnt/iris/verdify-worktrees/firmware/firmware/lib/greenhouse_logic.h` — `determine_mode`/`resolve_equipment`/`evaluate_overrides`; `effective_climate_action_for_mode` (590), `describe_effective_climate_decision` (662); SENSOR_FAULT all-false
- `/mnt/iris/verdify-worktrees/firmware/firmware/lib/greenhouse_types.h` — `Mode` (17), `MODE_NAMES` (33), `STATE_SENTINEL` (175), `ControlState` (182), `RelayOutputs` (231), `ClimateAction` (243), `CLIMATE_ACTION_NAMES` (259)
- `/mnt/iris/verdify-worktrees/firmware/firmware/greenhouse/controls.yaml` — production 5 s tick; FSM calls; relay dwell/interlock/manual-merge/mister layer Tier 1 misses
- `/mnt/iris/verdify-worktrees/firmware/firmware/greenhouse/globals.yaml` — 22 `restore_value: yes` (irrigation), all climate globals `restore_value: no`
- `/mnt/iris/verdify-worktrees/firmware/firmware/greenhouse.yaml` — no `mqtt:` block; `esp32dev`/`esp-idf`; `timezone: America/Denver` (216); native API/OTA
- `/mnt/iris/verdify-worktrees/firmware/scripts/export-replay-overrides.sh` — telemetry+`eq_*`+`setpoint_snapshot` as-of-join contract; ~17 `sp_*` columns (the coverage gap)
- `/mnt/iris/verdify-worktrees/firmware/scripts/firmware-replay-diff.sh` — `build_ref` worktree+g++ (40–52); `REPLAY_EMIT_FORCE_FSM=1` (65–66); column-aware diff (75–93); `THRESHOLD_PCT` (117)
- `/mnt/iris/verdify-worktrees/firmware/scripts/firmware-replay-worktree-diff.sh` — ref-vs-uncommitted (dev-twin analog)
- `/mnt/iris/verdify-worktrees/firmware/scripts/firmware-deploy-preflight.sh` — freeze gates: `alert_log` blocker (58), 48 h bake mtime (129–136)
- `/mnt/iris/verdify-worktrees/firmware/scripts/archive-firmware-artifacts.sh` — `source-snapshot` manifest copies `lib`+`greenhouse` but NOT `test/` (TWIN-7); `SOURCE_SHA256SUMS`
- `/mnt/iris/verdify-worktrees/firmware/ingestor/esp32_push.py` — real actuation path (`switch_command`/`number_command` over aioesphomeapi) the twin must NOT have
- `/mnt/iris/verdify-worktrees/firmware/mqtt/mosquitto.conf` — `allow_anonymous false`, `password_file`, no ACL (read-only-by-credential-absence)
- `/mnt/iris/verdify-worktrees/firmware/docker-compose.yml` — `verdify-internal`/`verdify-proxy` nets; DB localhost-bind (34); Grafana anon Viewer (63–65) + unified alerting off (78)
- `/mnt/iris/verdify-worktrees/firmware/.github/workflows/ci.yml` — `firmware-replay-diff` job to extend
- `/mnt/iris/verdify-worktrees/firmware/firmware/artifacts/{last-good.version,last-good.metadata.env,pending-fw-version.txt,<version>/source-snapshot}` — prod/stage ref pinning (prod `source_dirty=0`; pending `…dirty`, `source_dirty=1`)
