# End-to-End Data-Path Adversarial Review — 2026-06-16

**Scope:** the full control data path — DB ↔ graphs ↔ ingestor ↔ dispatcher ↔
firmware. Documents the flow of parameters, tunables, setpoints, and defaults;
checks the "single source of truth for defaults / no magic strings / durable
up-and-down flow" invariants; and lists inconsistencies, bugs, and coverage
gaps adversarially.

**Method:** read-only. Five layer maps (push-path, DB, firmware, planner/registry,
graphs/API) plus direct verification of the highest-severity cross-layer claims.
No files were modified. Citations are `file:line`; items I could not confirm
without a live DB/device are tagged **[unverified]**.

**Production facts pinned during this review (verified):**
- Prod ingestor runs `VERDIFY_BAND_SOURCE: "anchors"`
  (`deploy/k8s/overlays/prod/device-write-configmap.yaml:27`) — **but the code
  default is `legacy`** (`ingestor/tasks/band_anchors.py:154`). Reading the code
  alone gives the wrong mental model of prod.
- On-chip band engine is ON: `sw_onchip_band_enabled` `restore_value: yes`,
  `initial_value: 'true'` (`firmware/greenhouse/globals.yaml:879-882`). The
  device computes its own band from NVS anchors every ~5 s.
- Device write enabled only in `overlays/prod` (`VERDIFY_DEVICE_WRITE_ENABLED: "1"`,
  `device-write-configmap.yaml:16`); `prod-dark` is device-dark (`=0`).
- Writer-lease fence is **inert**: `VERDIFY_WRITER_LEASE_ENABLED: "0"`
  (`deploy/k8s/base/configmap.yaml:54`). Exactly-one-writer currently relies
  solely on `replicas: 1`.

---

## 1. Verdict on the four stated invariants

| Invariant (as stated in the goal) | Verdict | One-line reason |
|---|---|---|
| "All values originate from a **single `config.yaml`** for defaults." | **FALSE** | There is no `config.yaml`. Defaults live in **≥4 independent places** that are only partially reconciled — see §3. |
| "No **magic strings/numbers**." | **FALSE** | Many hardcoded control thresholds, calibration constants, key lists, and datasource UIDs — see §6 / Appendix A. |
| "Anything the **firmware enforces originates in global config as a default**." | **MOSTLY TRUE, with holes** | Every band edge, safety rail, hysteresis, fog gate traces to an `id(<global>)`. But several *enforced* thresholds are hardcoded C++ magic numbers (arbitration weights, DLI calibration, a cold-vent hysteresis floor that overrides config) — see §5 / F6, F7. |
| "Any deviation from default is an **AI tunable pushed by the planner in real time**." | **PARTLY FALSE** | (a) The band itself is **not** planner-pushed — it comes from DB `crop_band_anchors`; the planner only tunes *how hard* to chase it. (b) `set_tunable` is **not real-time** — it writes `setpoint_plan` and is picked up on the ≤5-min dispatcher cycle, deliberately bypassing the sub-second `NOTIFY` path — see §4 / F8. |

**Net:** the *architecture intent* (deterministic DB band + AI tactical tuning +
single-writer dispatcher + offline-first firmware under hard rails) is sound and
mostly realized. The gaps are in **default-provenance unification**, **fail-safe
fallback values**, and **device-truth observability** — concentrated where one
value is copied into 3–4 layers and the copies have drifted.

---

## 2. The data flow, documented

### 2.1 Two distinct kinds of value

1. **The target band** (temp_low/target/high, vpd_low/target/high, per house and
   per zone). The *crop* sets this. Source of truth = DB table
   `crop_band_anchors` (4 solar anchors: sunrise / solar-noon / sunset /
   solar-midnight per series). Served by `fn_crop_band_value` →
   `fn_band_setpoints` (`db/migrations/171-align-served-band-to-device-harmonic.sql:44-56`).
2. **Tunables** (~40 knobs: hysteresis, mister timing, fog gates, lighting,
   irrigation, safety rails, summer-vent deltas, dwell). The *AI planner* tunes
   *how aggressively* to chase the band. Logical source of truth = the Python
   `verdify_schemas/tunable_registry.py` (`REGISTRY`, `tunable_registry.py:99`),
   which declares each tunable's `default`, `min`/`max`, `fw_clamp_lo/hi`,
   `esp_object_id`, `cfg_readback_object_id`, and `push_owner`.

### 2.2 Down-path (DB/planner → device)

```
crop_band_anchors (DB) ──fn_crop_band_value──┐
                                             ▼
planner set_tunable/set_plan → setpoint_plan │   DISPATCHER  (ingestor/tasks/dispatcher.py)
operator/MCP → setpoint_changes ─────────────┤   setpoint_dispatcher() every 300 s
                                             ▼   (ingestor/ingestor.py:1991)
                         ┌─────────────────────────────────────────────┐
                         │ 1. read DB policy fns (band/zone/lighting)   │
                         │ 2. dead-band vs _last_pushed (1%)            │
                         │ 3. INSERT setpoint_changes (echo-suppress)   │
                         │ 4. push_to_esp32() — the ONE device writer   │
                         └─────────────────────────────────────────────┘
                                             ▼
   anchors-mode: 56 anchors → `set_band_anchor` API service (NVS-persisted)
   tunables/plan: number/switch entities (ESPHome native API)
                                             ▼
   FIRMWARE ESP32: sw_onchip_band_enabled=true → recomputes band on-chip from
   NVS anchors via band_value_at_phase() (greenhouse_solar.h) every cycle;
   determine_mode_band_first() (greenhouse_logic.h:1079) drives relays under
   safety_min/max rails.
```

- **Cadence:** `setpoint_dispatcher` every 300 s (`ingestor/ingestor.py:1991`),
  plus a sub-second `LISTEN setpoint_changed` listener (`ingestor.py:2208`) for
  non-`esp32`-source `setpoint_changes` rows, plus a reconnect force-push on
  every ESP32 (re)connect (`ingestor.py:1853, 1888-1903`).
- **Real-time path caveat:** the `NOTIFY` trigger fires on `setpoint_changes`
  only (`db/migrations/100-setpoint-notify-source.sql:6-21`). `set_tunable`
  writes `setpoint_plan` (`mcp/server.py:650-668`), so it does **not** trigger
  the real-time push — it waits for the 300 s cycle (by design, `mcp/server.py:587-597`).

### 2.3 Up-path (device → DB → graphs)

```
ESP32 telemetry (aioesphomeapi) → ingestor → TimescaleDB (climate, equipment_state)
ESP32 cfg_* readback sensors    → ingestor → shared.cfg_readback
                                            → confirms setpoint_changes rows
                                              (delivery_status='confirmed',
                                               ingestor.py:1639-1667)
                                            → setpoint_snapshot table
TimescaleDB → Grafana (fn_band_timeline / fn_band_setpoints / setpoint_snapshot)
            → FastAPI /setpoints (fn_band_setpoints — legacy compat, no live consumer)
            → Quartz site (embedded Grafana panels)
```

### 2.4 The reliability machinery (what is genuinely good)

- **Single writer:** every `push_to_esp32` call passes through one chokepoint
  (`ingestor/esp32_push.py:71`) gated by SHADOW_MODE, the device-write interlock
  (`VERDIFY_DEVICE_WRITE_ENABLED`, default-deny), and `writer_lease_held()`.
- **Idempotent / dead-banded:** 1% proportional dead-band vs `_last_pushed`
  (`dispatcher.py:235-249`) — steady state pushes nothing.
- **Readback confirmation loop:** cfg_* sensors mirror enforced globals; the
  confirmation monitor alerts on rows unconfirmed >5 min / >15 min
  (`ingestor/tasks/confirmation.py:20-60`).
- **Reconnect force-push** excludes `BAND_DRIVEN_PARAMS` from the readback
  re-seed so a post-OTA firmware-default revert cannot suppress re-asserting the
  authoritative band/lux (`dispatcher.py:532-565`) — this is the fix for the
  2026-06-16 lux incident and it is sound.
- **Offline-first firmware:** band + safety rails are NVS-persisted and
  recomputed on-chip; dispatcher silence leaves the device running its last
  good band, not a mid-range guess.

---

## 3. The "single source of truth for defaults" reality

There is **no `config.yaml`.** A single band/tunable default value can exist in
up to **four** places:

| # | Store | Role | Authoritative for |
|---|---|---|---|
| 1 | `firmware/greenhouse/globals.yaml` (`initial_value`) | Firmware cold-start / NVS seed | The device until a push lands |
| 2 | `firmware/greenhouse/tunables.yaml` (`min_value`/`max_value`) + `greenhouse.yaml` boot guards | Firmware clamp bounds (3rd clamp surface) | What the device will accept |
| 3 | `verdify_schemas/tunable_registry.py` (`REGISTRY`, `_FW2_*`) | Python control-plane default + bounds; **dispatcher fallback** when DB unreadable | Planner validation + dispatcher fallback |
| 4 | DB `crop_band_anchors` (+ `fn_*` SQL fallbacks, `crops.target_vpd_*`) | **Live source of truth for the band** | What the dispatcher actually syncs to the device |

Within the **Python control plane**, the registry genuinely *is* single-sourced
(`SETPOINT_MAP`, `CFG_READBACK_MAP`, `TIER1`, planner-pushable set are all
computed views, `tunable_registry.py:3025-3037`; consumers import them). The
problem is the **three physical stores (1, 2, 4) that hold copies of the same
numbers**, reconciled only by:
- a CI text-diff guard for registry ↔ `tunables.yaml` **clamps** (good,
  `verdify_schemas/tests/test_tunable_registry.py:142-172`), but
- **no guard** for registry `default` ↔ firmware `initial_value`, and
- **no guard** for registry band defaults ↔ DB `crop_band_anchors`, and the
  firmware-v2 band number entities are explicitly **excluded** from the firmware
  drift guard (`KNOWN_PRE_EXISTING_DRIFT`, `test_firmware_drift.py:271-294`).

That missing reconciliation is the root cause of the top finding (F1).

---

## 4. Findings — ranked

Severity: **P0** plant-risk / silent-wrong-control / false-safety; **P1**
correctness or durability gap; **P2** hygiene / magic-number / doc drift.

### F1 — [P0] Registry band defaults are stale vs BOTH firmware globals AND the DB, and were a live fail-OPEN fallback — **fail-closed fix landed 2026-06-16**

**Verified by direct read + live prod DB query.** `_FW2_HOUSE_SERIES` /
`_FW2_ZONE_VPD_TARGETS` (`tunable_registry.py:2747-2760`) still hold the original
migration-161 values. Those values disagree with the current firmware cold-start
defaults *and* the live DB anchors — a **three-way divergence on the
highest-priority crop**, now confirmed against prod `crop_band_anchors`:

| Series·anchor | Registry `tunable_registry.py` | Firmware `globals.yaml` | **DB `crop_band_anchors` (live, verified 2026-06-16)** |
|---|---|---|---|
| house `vpd_target` sr/sm/ss/mid | `0.60/1.05/0.60/0.50` (:2751) | `1.05/1.10/1.05/1.05` (:717-732) | `0.92/1.10/0.95/0.95` |
| `zone_vpd_target_center` (orchid) | `0.60/1.05/0.60/0.50` (:2756) | `1.05/1.1/1.05/1.05` (:753-768) | `0.90/1.10/0.90/0.70` |
| house `vpd_low` sr/sm/ss/mid | `0.40/0.60/0.45/0.42` (:2750) | `0.90/0.95/0.90/0.88` (:700-715) | `0.74/0.92/0.77/0.77` |

All three stores disagree. The registry's overnight target (`mid=0.50`) is the
**wettest** of the three — the fail-open-to-wet direction is confirmed.

The dispatcher builds the anchor push by overlaying `crop_band_anchors` rows on
`registry_default_anchor_values()` and previously **fell back to the bare
registry values on any read-error path** (`ingestor/tasks/band_anchors.py`),
which in prod `anchors` mode is **pushed to NVS via `set_band_anchor`** —
re-commanding the wet-night orchid band that drove RH 39%→84% (the documented
2026-06-15 regression). It failed OPEN, silently, with no drift guard.

- **Why P0:** the failure value is the exact prior incident; highest-priority
  crop; failed open; invisible.
- **FIX LANDED (this session):** the dispatcher anchor push is now **fail-closed**
  on a DB read error. `band_anchors.anchor_push_allowed(anchors_mode, supported,
  origin)` returns `False` when `origin == ANCHOR_ORIGIN_TABLE_ERROR`, so the
  dispatcher **holds the device's NVS band** (offline-first already has the
  correct values) instead of pushing the stale registry envelope, and opens a
  `band_anchor_db_read_failed` warning alert so the hold is observable. The three
  origin string literals were lifted into named constants. Unit tests added
  (`tests/test_anchor_service_sync.py`); lint + targeted tests green, zero
  regressions.
- **Remaining (follow-up, not landed):** re-sync the registry `_FW2_*` defaults
  to the live researched envelope and add a CI guard that *generates* / validates
  them against the canonical `crop_band_anchors` seed (the §3 single-source gap),
  so the table-absent cold-start path and planner-validation defaults are also
  current. Better done as part of the §6 unification (broader change — confirm
  scope first).

### F2 — [P1] Compliance/band graphs query the DB-derived band, not the available device readback (corrected from P0)

**Corrected by live DB query (2026-06-16).** The initial finding asserted "no
cfg_* readback of the resolved band edge exists." **That is false** — prod
`setpoint_snapshot` carries fresh resolved-band readbacks: `temp_low`,
`temp_high`, `vpd_low`, `vpd_high` (≈117k rows each, latest = current minute)
**and** per-zone resolved values `band_house_*`, `band_center_*`, `band_east_*`,
etc. So **device truth of the resolved band IS observable.** The real (narrower)
finding is that the band/compliance panels don't plot it:
- "Temperature/VPD Compliance Band" panels (control-loop, canonical-climate-control,
  site-climate-controller, site-home) draw `fn_band_timeline.projected_*` — a DB
  **re-derivation** of the band, not the `setpoint_snapshot` device readback.
- "Temp/VPD vs Setpoints" panels draw `setpoint_changes` `source='band'` rows,
  which are **frozen** since the anchors flip (control-loop.json:848,
  canonical-climate-control.json:1347).
- **Net:** the device's actual resolved band exists in the DB but is not the
  source the headline compliance panels read. A firmware-vs-DB skew or a failed
  NVS write *could* be surfaced (the readback is right there) but currently
  **isn't** plotted, so compliance shows GREEN against the DB curve rather than
  the measured device band.
- **Fix direction (cheap, since the data exists):** repoint the compliance/band
  panels to the `setpoint_snapshot` resolved-band readback (or overlay it), and
  drive a real device-vs-DB divergence alarm off the two series.

### F3 — [RETRACTED] `readback_match_pct` is NOT structurally unverifiable

The initial claim — that `fn_band_trace.rb_*` reads band-edge rows the firmware
never emits — is **false**: `setpoint_snapshot` does contain `temp_low/high` and
`vpd_low/high` readbacks (verified above). The readback signal exists, so the
match metric *can* be meaningful. What remains worth a check is whether
`fn_band_trace`'s specific query joins those rows (vs the stale
`setpoint_changes`-derived `fw_*`) — a query-wiring question, not missing data.
Severity withdrawn; the structural-blind-spot framing was wrong.

### F4 — [P1] `fn_lighting_minutes_policy` still ranks device snapshot above planner for every NON-lux lighting param

Migration 176 fixed the lux feedback loop (device cfg readback was outranking the
planner setpoint, locking the wrong threshold after a reboot). **But it fixed only
the lux columns** — the `latest_values` ordering (`source_rank`: switch 0,
snapshot 1, confirmed 2; `db/migrations/176-...sql:114-139`) still makes the
device snapshot **outrank** a confirmed planner change for `gl_*_dli_target`,
`*_target_light_minutes`, `start/cutoff hour`, `min_on/off`. So the identical
feedback-loop class — reboot reverts a global → snapshot poisons the re-push — is
**still live for the other lighting tunables**.
- **Fix direction:** apply the 176 pattern (planner/AI ranked above device
  readback) to all lighting params, not just lux.

### F5 — [P1] Lighting photoperiod/DLI has three disagreeing sources of truth

`band_anchors.GL_CIRCUIT_TARGETS = {"main": (13.0, 780.0), "grow": (21.0, 900.0)}`
(`ingestor/tasks/band_anchors.py:404-407`) is **hardcoded** and, in anchors mode,
**overrides** the DB lighting policy for DLI + light-minutes (`dispatcher.py:796,
813-814`). Meanwhile the registry default is `960` minutes and the DB fallback is
`target_light_hours*60`. Three values for "minutes of light per circuit." The
2026-06-16 unification fixed *lux* but left *minutes/DLI* bypassing the
AI-tunable DB path — editing `fn_lighting_circuit_policy` photoperiod is silently
overridden once `VERDIFY_BAND_SOURCE=anchors`.

### F6 — [P1] A config-backed hysteresis is silently overridden by a hardcoded floor

`cool_exit_hysteresis_f` (a tunable, clamped 0.3–3.0) is forced to
`max(global, 3.0f)` in the cold-vent regime (`greenhouse_logic.h:1124, 583`).
Since the clamp ceiling *is* 3.0, this pins it to its maximum regardless of what
the dispatcher pushed — an operator who lowers it gets 3°F anyway. The enforced
value is no longer traceable to the pushed config in that regime. (Sibling:
`cold_dehum` margin floored at hardcoded `2.0f`, `:1135`.)

### F7 — [P1] VPD safety rails are inert in the production controller

`vpd_max_safe` / `vpd_min_safe` (globals, with cfg readbacks, validated/ordered)
are referenced only by the **dead legacy cascade** (`greenhouse_logic.h:1655,
1686`). The production `determine_mode_band_first` / `evaluate_climate_decision`
path **never consults them** — only the band edges. The VPD safety clamp is
effectively a no-op in prod. Temp rails (`safety_min/max`) *are* live and
preempt arbitration. **Confirm intentional**; if the band edges are deemed
sufficient, document it, else wire the VPD rails into the live path.

### F8 — [P1] `set_tunable` is silently ≤5-min, not real-time

`set_tunable` writes `setpoint_plan` (`mcp/server.py:650`), which the `NOTIFY`
trigger does not watch, so an "urgent" planner correction (e.g. a VPD-high stress
push) has up to ~5 min latency despite a sub-second `LISTEN` path existing. This
is a deliberate durability choice, but it should be explicit in the incident
runbook and the planner prompt, because the planner is told it controls "real
time."

### F9 — [P1] `sw_night_econ_heat_suppress_enabled` is fire-and-forget (rule-6 violation)

Pushed into `Setpoints` (`controls.yaml:562`) and **enforced** (suppresses the
only overnight VPD-driven heat path, `greenhouse_logic.h:123-124, 2055`) but has
**no `cfg_*` readback** anywhere in `sensors.yaml` (its siblings
`sw_wet_taper_enabled`/`sw_night_stress_wet_enabled` both do). The ingestor cannot
verify the push landed — a silent-push-corruption risk that CLAUDE.md firmware
rule 6 exists to prevent.

### F10 — [P1] Partial / zero device push reports success-by-count with no alert or retry

`push_to_esp32` `break`s the whole batch on the first command exception
(`esp32_push.py:162-164`) and returns a partial count; the dispatcher logs the
short count but only the **raised-exception** path triggers retry/alert
(`dispatcher.py:1168-1209`). A device that ACKs the first few commands then
silently drops the rest — or returns 0 because `client is None`
(`esp32_push.py:109-110`) — produces **no alert** and no same-cycle retry;
recovery waits up to 300 s or the confirmation monitor's 5-min alarm. Real silent
degradation mode.

### F11 — [P1] Writer-lease fence inert; exactly-one-writer relies on `replicas:1` alone

`VERDIFY_WRITER_LEASE_ENABLED: "0"` (base configmap:54), so `writer_lease_held()`
returns open. The firmware sets `max_connections:20`, so it is **not** a natural
fence — any accidental ingestor scale-up could split-brain-write the device. This
is a known gated item (HA `#240`), not a new defect, but worth re-stating: the
guarantee is currently operational (Deployment shape), not enforced.

### F12 — [P1] Code default (`legacy`) ≠ prod deployment (`anchors`)

`band_source()` defaults to `legacy` (`band_anchors.py:154`) but prod overlay
pins `anchors` (`overlays/prod/device-write-configmap.yaml:27`). Anyone reasoning
from the code (tests, new contributors, the stale `schema.sql`) builds the wrong
model of prod control. Combined with the drift-guard exclusion (§3), the live
band path is the least-guarded, least-code-visible part of the system.

### P2 — hygiene / magic-number / doc drift (condensed)

- **F13** `db/schema.sql` is a **stale pg_dump** (pre-161/171/176): no
  `crop_band_anchors`, no `fn_lighting_minutes_policy`, old `fn_band_setpoints`
  body. Misleads every reviewer. Regenerate from a post-176 dump.
- **F14** `crops.target_vpd_low/high` (`db/migrations/162:83,107`) is a **third,
  stale copy** of the per-zone band with no live consumer — an attractive
  nuisance.
- **F15** `mv_band_curve` (migration 167) has **no live/provisioned consumer**
  (panels read `fn_band_timeline`/`setpoint_snapshot` live), **no anchor-change
  refresh trigger**, and **no staleness alarm** — its stated purpose ("the cache
  the compliance panels read", 167:3-5) is no longer true. Either wire panels to
  it (with a freshness tile) or retire it.
- **F16** **Dual greenhouse coordinates:** `ingestor/solar.py:25-26`
  (`40.167/-105.102`) vs `ingestor/config.py:96-97` (`40.1672/-105.1019`) vs
  `config/zones.yaml:8-9` (`40.1672/-105.1019`, elevation `5003` vs README's
  `5,090`). Immaterial physically, single-source violation.
- **F17** **Dashboards have no env banner** and there are **two divergent
  `site-home.json` copies** (provisioned reads `setpoint_snapshot`+`fn_band_timeline`;
  `grafana/dashboards/site-home.json` reads `mv_band_curve`). The known
  graphs-dev-vs-prod confusion is unmitigated in the dashboard layer. Add an
  `${ENV}`/datasource-derived title banner.
- **F18** **Magic strings:** datasource UID `verdify-tsdb` duplicated across ~80
  dashboard files; `'vallery'` greenhouse_id hardcoded in dozens of `rawSql`
  blocks; `set_band_anchor` service string duplicated as two constants
  (`esp32_push.py:23` vs `band_anchors.py:165`); lux fallbacks `40000/8000` and
  clamp bounds re-spelled as SQL literals in dashboards and migration 176.
- **F19** **Hardcoded firmware control constants** (config-as-code holes — full
  list in Appendix A): arbitration cost weights (`greenhouse_logic.h:627-650`),
  lighting DLI calibration coefficients (`:913-917`), outdoor-RH adaptive
  fog-burst bands/durations (`controls.yaml:325-336`), degraded-sensor fabricated
  fallbacks (`controls.yaml:472-475`), `BAND_HEAT_TARGET_FRACTION=0.25f` (`:402`).
- **F20** **Missing CI guards:** registry-`default` ↔ firmware-`initial_value`;
  registry band defaults ↔ DB `crop_band_anchors`; coverage gap for live firmware
  `number:` entities with no registry row (`vent_prefer_*`, `min_heat_off_s`,
  economiser set, `bias_heat/cool`, grow-light circuits) — all outside every
  drift check.

---

## 5. Firmware enforcement vs config — summary

Every **primary** climate decision (SAFETY_COOL/HEAT, VENT entry/exit, HEAT
stages, SEALED_MIST, MIST S1→S2→FOG, DEHUM, econ-heat, summer-vent, dwell,
dawn/midday boosts, lighting on/off windows) compares against an `id(<global>)`
sourced value — confirmed in `greenhouse_logic.h`. The band curve is a clean
closed-form 4-anchor harmonic (`greenhouse_solar.h:159-175`), reconstructed into
`Setpoints` every cycle when `sw_onchip_band_enabled` (gated correctly), and is
**byte-identical to `ingestor/solar.py:174-178`** (DB parity asserted by migration
170 header, **[unverified]** against the live function). Safety **temp** rails are
the final, un-bypassable clamp.

The exceptions are F6 (a config value pinned by a hardcoded floor), F7 (VPD rails
inert in prod), F9 (a fire-and-forget switch), and the F19 hardcoded constants —
i.e. the firmware enforces a handful of behaviors not traceable to a config
default. None breaches the temp safety rails.

---

## 6. Recommended path to a single source of truth

1. **Pick one seed for band defaults.** `crop_band_anchors` is already the live
   authority — make it *the* seed. Generate the firmware `globals.yaml` band
   `initial_value`s and the registry `_FW2_*` defaults from it (build step), so
   the three copies cannot diverge. Add the missing CI guards (F20).
2. **Make the dispatcher fallback fail closed** (F1): on `crop_band_anchors` read
   error, hold the last confirmed NVS anchors and alert — never push a
   researched-envelope default that may be a known-bad band.
3. **Add a resolved-band cfg readback** (F2/F3): publish the device's actual
   band edge at current phase; plot it as device-truth; drive a real divergence
   alarm so graphs and the public metric stop asserting unverifiable GREEN.
4. **Generalize the 176 lux fix** to all lighting params (F4) and route
   photoperiod/DLI through the DB policy, retiring `GL_CIRCUIT_TARGETS` (F5).
5. **Close the fire-and-forget gap** (F9) and **alert on partial/zero pushes**
   (F10).
6. **Regenerate `schema.sql`** (F13) and **add an env banner** to dashboards
   (F17), so reviewers and viewers stop reading stale/ambiguous truth.

---

## Appendix A — Magic-number inventory (control-relevant, hardcoded, not config-backed)

`greenhouse_logic.h`: `BAND_HEAT_TARGET_FRACTION 0.25f` (:402); VPD-hyst cap
factor `0.33f` / floor `0.2f,0.05f` (:391-394); cold-vent exit floor `3.0f`
(:1124, overrides config — F6); cold-dehum margin floor `2.0f` (:1135);
arbitration projected-effect + cost weights `2.5/8.0/4.0/6.0/2.0/5.0/7.0…` and
vpd caps `0.18/0.16/0.3` and churn weights `0.05…0.7` (:627-650); candidate
confidence `0.65f` (:491); resource-cost `0.04/0.02/0.006/0.002` (:444-462);
`FAN_LEAD_RUNTIME_DEADBAND_MS 600000U` (:855,875, no global/readback); DLI
calibration `LUX_TO_PPFD 0.0185f`, `INDOOR_LDR_CORRECTION 3.5f`,
`TEMPEST_TO_PLANT_LUX 0.16f`, `MAIN_LIGHT_DLI_PER_HOUR 0.3485f`,
`GROW_LIGHT_DLI_PER_HOUR 0.4515f` (:913-917); lux noise floor `10.0f` (:919,922);
lighting clamp bounds `1080/100/100000/0/25000/3600000` (:897-903).

`greenhouse_solar.h`: site lat/lon `40.167/-105.102` (:24-25); fallback ephemeris
`{360,780,1200}` (:145-147); zone width floor / engage deadband `0.05f`
(:195-197,216). (NOAA Fourier coefficients :50-60 and harmonic weights
`0.25/0.5/0.5/0.25` :159-174 are correct physical/closed-form constants, not
magic thresholds.)

`controls.yaml`: outdoor-RH adaptive fog-burst bands `15/45/20/40` % + burst
`3/5/8` min (:325-336); degraded-sensor fabricated fallbacks `Tin-10` dewpoint,
RH `30`, enthalpy `-5`, `65/50` (:472-475).

`ingestor/tasks/dispatcher.py`: activity defaults (`direct_wet_min_temp_f 65.0`,
offsets `60/120/180`, `irrig_*_days_mask 127`, :120-133); hardcoded device
`safety_max 100.0`/`safety_min 40.0` (:852-855); mister offsets `+0.05/+0.25` kPa,
delays `30/60`, center penalty `0.5` (:866-881); moisture-guardrail literals
(:515-521). `ingestor/tasks/band_anchors.py`: `GL_CIRCUIT_TARGETS` (:404-407 — F5).

## Appendix B — Provenance of representative values (verified)

| Value | Default origin | Bounds/clamp | AI-tunable? | Pushed how |
|---|---|---|---|---|
| `temp_high` (band edge) | DB `crop_band_anchors` (house) → `fn_crop_band_value` | firmware `validate_setpoints` + safety rails | No (band, not a tunable) | on-chip from NVS anchors; anchors synced via `set_band_anchor` |
| `vpd_target_center` (orchid) | DB `crop_band_anchors` (orchid); registry default **stale** (F1) | reg 0.10–3.0 = fw clamp | No (band) | `set_band_anchor` (anchors mode) |
| `mister_engage_kpa` | `globals.yaml:1140` = `1.6`; registry default `1.6` (match) | reg/fw clamp `0.5–2.5` (match) | Yes (`planner_pushable`) | `setpoint_plan` → dispatcher → number entity |
| `safety_max` | `globals.yaml:100` = `95`; dispatcher also hardcodes `100.0` (F-Appendix) | fw clamp `80–110` | safety-owner | number entity |
| `gl_main_lux_threshold` | DB `fn_lighting_circuit_policy` → `40000` (mig 176); `globals.yaml:364` cold default `40000` | `100–100000` | Yes (AI recommendation from Tempest lux) | number entity |
| `gl_main_target_light_minutes` | **3 sources disagree** (F5): registry `960`, `GL_CIRCUIT_TARGETS` `780`, DB fallback `hours*60` | — | partially | number entity |

---

*Generated by an adversarial read-only review on 2026-06-16. Items tagged
**[unverified]** require a live DB query or device readback to confirm; all
other claims carry a `file:line` citation. If this doc and the code disagree,
the code wins — update this doc.*
