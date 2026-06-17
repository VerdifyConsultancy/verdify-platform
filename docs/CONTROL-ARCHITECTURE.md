# Greenhouse Control Architecture

**How the planner, tunables, dispatcher, and firmware come together across the
controllable dimensions — and where the opportunities to improve are.**

Status: living reference · Last substantive update: 2026-06-15 (anchors-mode
completion + smooth harmonic band). Audience: anyone touching control behavior.
If this doc and the code disagree, the code wins — fix this doc.

---

## 0. TLDR

> The **DB defines a deterministic target curve**; the **AI planner only tunes
> *how hard* the controller chases it**; the **dispatcher is the single bridge**
> that writes the device; and the **firmware is a dumb-but-reliable, offline-first
> state machine** that drives the relays under **hard safety rails**.

Three things are invariant regardless of the planner, the network, or anything
else: the **deterministic band**, the **single writer**, and the **firmware
safety rails**. Everything else is tuning.

```
 crop_band_anchors (DB)              tunable_registry + planner (AI)
   = deterministic TARGET curve        = ~40 knobs: "how aggressively to
     f(crop, zone, solar-phase)          chase the target / respond"
          │                                       │
          └───────────────────┬───────────────────┘
                              ▼
            DISPATCHER (ingestor)  ── THE single writer; ~5.5 min + on-change
            • band anchors → device via set_band_anchor service (anchors-mode)
            • tunables / active plan → device via number/switch entities
            • clamps the planner to the band; heap-guards; writer-lease fence
                              ▼
            FIRMWARE (ESP32)  ── offline-first, recomputes every ~1 s, no WiFi
            • computes the band ON-CHIP (NOAA solar → smooth harmonic curve)
            • per-zone VPD FSM + house temp FSM → relays
            • SAFETY RAILS clamp every setpoint (the last word)
```

---

## 1. The control philosophy — deterministic by construction

The system was deliberately rebuilt (firmware-v2, 2026-06) around a single idea:
**separate the *target* from the *response* from the *execution*.**

- **The target is math, not AI.** The climate band (temp + VPD low/target/high)
  is a pure, repeatable function of *(crop priority, zone, solar phase)*. Same
  inputs → same curve, computed identically in the DB, the dispatcher, and the
  ESP32. Nobody "decides" the band per cycle; it is `band_value_at_phase()`.
- **The AI only shapes the response.** The planner can make the controller chase
  the band faster/slower, mist more/less, prioritize the Vandas over cannabis
  when the single shared air mass cannot satisfy every zone — but it **cannot
  move the target**. Band-owned params are rejected at the MCP boundary and
  dropped by the dispatcher. This is why the system is predictable and safe to
  let an LLM touch.
- **The execution is offline-first.** The firmware computes the band itself from
  NVS-persisted anchors and runs the full diurnal program with **zero network**.
  The dispatcher syncs anchors only when they change (rare). A dead network, a
  dead planner, or a dead dispatcher does not stop correct climate control.
- **Safety rails are absolute.** `safety_min/max` (temp), `safety_vpd_min/max`,
  anti-short-cycle min-run, leak/heap watchdogs — these override the band, the
  planner, and everything else, on-chip.

---

## 2. The four layers

### 2.1 DB — the source of truth
- **`crop_band_anchors`** — the canonical band curve. Rows are
  `(crop_type, series, anchor, value, widths, season, growth_stage)`. `series` ∈
  {`temp_low`,`temp_target`,`temp_high`,`vpd_low`,`vpd_target`,`vpd_high`};
  `anchor` ∈ {`sr`,`sm`,`ss`,`mid`} (sunrise / solar-noon / sunset /
  solar-midnight). `house` is the thermal curve; `orchid`/`cannabis`/`citrus`/
  `pepper` are per-zone VPD curves. **Editing this table changes the greenhouse**
  (it syncs to the device — §7).
- **`tunable_registry`** (`verdify_schemas/`) — the wire contract for every
  knob: `name`, range, `fw_clamp`, `esp_object_id`, `cfg_readback_object_id`,
  `push_owner` (`band`|`planner`), `planner_pushable`. This is the single source
  of truth for "what can be set, by whom, with what name on the device."
- **`fn_crop_band_value` / `fn_zone_vpd_targets` / `fn_band_setpoints`** — the
  SQL mirror of the on-chip curve (used by the graphs, the audit, and the legacy
  push). Now a **smooth 4-anchor harmonic interpolation** (see §4).
- **`setpoint_plan`** (→ `v_active_plan`) — the planner's one-shot tunable
  waypoints the dispatcher reads each cycle.
- **`setpoint_snapshot`** — the per-zone band audit (the compliance record);
  **`setpoint_changes`** — the device-write log.

### 2.2 Planner — the AI (response only)
- LangGraph graph (`planner_graph/`) + `ingestor/iris_planner.py` + an MCP tool
  server. ~4 scheduled cycles/day (SUNRISE / SOLAR_MAX / SUNSET / MIDNIGHT) plus
  event triggers.
- Authority: **tunables only** (~40 of ~227 registry entries are
  `planner_pushable`). It writes them to `setpoint_plan` via `set_tunable`
  (audited: reason + trigger_id + planner_instance).
- It is **walled out of the band**: every band/zone anchor is `push_owner='band'`
  and rejected by `mcp/server.py` and dropped by the dispatcher
  (`PLANNER_PUSHABLE_REG` check). The planner can tighten *within* the band but
  not move it.

### 2.3 Dispatcher — the single writer (the bridge)
- Lives in the **ingestor** (`ingestor/tasks/dispatcher.py` +
  `ingestor/tasks/band_anchors.py` + `ingestor/esp32_push.py`). One process, one
  ESP32 connection, `replicas:1`, `strategy:Recreate` — **never two writers**.
- Each cycle (~5.5 min) and on LISTEN/NOTIFY it: reads the DB band + active plan,
  validates physics + clamps the planner to the band, then pushes to the device
  through **one chokepoint** (`push_to_esp32`) that enforces the device-write
  gate, the writer-lease fence, heap-pressure deferral, and command pacing.
- Push paths by entity kind: **band/zone anchors** → the `set_band_anchor` API
  service (`"service"` etype); **tunables / per-zone targets / legacy band** →
  number entities (`number_command`); **switches** → `switch_command`.
- Confirms every write via the device's `cfg_<name>` readback sensors; re-pushes
  on drift; emits the per-zone band audit.

### 2.4 Firmware — the deterministic controller (ESP32)
- ESPHome + pure-C++ libs (`firmware/lib/greenhouse_*.h`). Recomputes every
  control loop (~1 s; `dt_ms`-based timer accrual, so behavior is invariant to
  the tick rate — it was 5 s historically).
- Computes solar times on-chip (NOAA ephemeris, `greenhouse_solar.h`), maps clock
  → continuous **solar phase [0,4)**, evaluates the band via
  `band_value_at_phase()`, and runs the FSM: a band-first mode selector
  (`determine_mode_band_first`) + a normalized delta-error arbiter
  (`evaluate_climate_decision`) + a per-zone VPD wetting sub-FSM.
- NVS-persisted anchors + tunables (`restore_value`) → offline-first.
- Safety rails, anti-short-cycle, even-wear fan rotation, manual-burst buttons —
  all on-chip, all WiFi-independent.

---

## 3. The end-to-end data flow

1. **Author** the target in the DB: `crop_band_anchors` (band) and/or the planner
   writes tunables to `setpoint_plan`.
2. **Dispatcher cycle:** reads `crop_band_anchors` (→ `crop_band_anchor_values`)
   and `v_active_plan`; computes the desired anchor set + tunables; diffs against
   the device `cfg_*` readbacks; pushes only what changed.
3. **Device:** the `set_band_anchor` service writes the matching NVS global; the
   FSM recomputes the band next loop from the new anchors; tunables shape the
   response; relays fire; safety rails clamp.
4. **Telemetry back:** the device publishes sensors → the ingestor writes
   `climate` (the data) + `setpoint_snapshot` (the commanded band audit) +
   `cfg_*` readbacks (the confirm loop). Graphs read the DB.

Steady state: the band is stable, so anchor pushes are **zero**; the device runs
the diurnal program autonomously and the dispatcher only re-syncs on a DB change.

---

## 4. The band curve — the deterministic target (the math)

- **Solar phase**: clock time → continuous `[0,4)` where `0`=sunrise,
  `1`=solar-noon, `2`=sunset, `3`=solar-midnight. Because the anchors live in
  *phase* space, the diurnal program stretches/compresses automatically as day
  length drifts through the year — no clock-window cutoffs.
- **Interpolation**: a **smooth 4-anchor harmonic (discrete-Fourier)
  interpolation** through the SR/SM/SS/MID anchors:
  `value(φ) = c0 + c1·cos(θ) + s1·sin(θ) + c2·cos(2θ)`, `θ = π·φ/2`, with
  `c0=(sr+sm+ss+mid)/4`, `c1=(sr−ss)/2`, `s1=(sm−mid)/2`,
  `c2=(sr−sm+ss−mid)/4`. It passes **exactly** through every anchor but is
  C-infinity smooth (no plateaus). This replaced a piecewise cosine-ease that had
  zero slope at every anchor → four visible "lumps." Identical in firmware
  (`greenhouse_solar.h`), dispatcher (`ingestor/solar.py`), and DB
  (`fn_crop_band_value`, migration 170).
- **House vs zone**: temperature is **one house curve** (single air mass — there
  is no per-zone heat actuator). VPD is **per-zone**: `target = curve(φ)`,
  `low = target − width_below`, `high = target + width_above`.
- **Why VPD bands look "uneven" in kPa**: VPD ≈ SVP(T)·(1−RH), and SVP roughly
  doubles from a 64°F night to an 84°F day — so a *constant-RH* policy is a
  *rising-then-falling* VPD curve. Reason about RH; the curve converts to VPD at
  the live temperature.

---

## 5. The controllable dimensions

| Dimension | Target (deterministic, DB) | Actuators (firmware FSM) | Planner tunes (the "how") | Scope |
|---|---|---|---|---|
| **Temperature** | `band_temp_low/target/high` | heat1→heat2, vent, fan1→fan2, economizer (dehum-vent) | `bias_heat`, `stage2_heat/cool`, `hyst_temp`, escalation/lead-rotate timeouts | **house-wide** (one air mass) |
| **Humidity / VPD** | `zone_vpd_target_*` + `band_vpd_*` | center fog, S/W wall misters, vent (to dry), heat (warm→drier) | `mister_pulse_on/gap`, `mister_vpd_weight`, `engage_kpa`, `mister_center_penalty`, `east_adjacency_factor`, **`zone_priority_*`** | **per-zone** (misters/fog) + house (vent/heat) |
| **Lighting** | DLI + photoperiod policy (2 circuits: `gl_main_*`, `gl_grow_*`) | `grow_light_main`, `grow_light_grow` | lux thresholds/hysteresis, sunrise/sunset, min on/off, DLI target | per-circuit |
| **Irrigation** | operator schedule (wall/center drip + fertigation) | drip valves, fert master valve | `irrig_vpd_boost_pct`, durations, day masks | per-line |

**The tie-breaker primitive:** because temperature is house-wide and the air mass
can't satisfy every crop at once, a **settable ranked crop/zone priority**
(default center/Vanda = rank 1) decides who wins. This is the single most
important control lever and is the intended (but see §9a) arbitration axis.

---

## 6. The invariants — single writer + safety

- **Single writer**: the prod ingestor is the *only* thing that writes the ESP32.
  `replicas:1` + `strategy:Recreate` (never two pods) + a
  `coordination.k8s.io` writer **Lease** with renew-or-die self-fencing (built;
  arming is gated). The device firmware sets `max_connections:20`, so it is **not**
  a natural fence — the Lease is the guarantee. Never open a second writer
  (a laptop `number_command`/`execute_service` counts).
- **Device-write gate**: `VERDIFY_DEVICE_WRITE_ENABLED=1` only in prod; dev is
  device-dark by construction (`replicas:0` + deny-egress).
- **Safety rails (on-chip, absolute)**: `safety_min/max` temp, `safety_vpd_min/max`,
  anti-short-cycle min-run, leak/heap watchdogs. They clamp the final setpoints
  regardless of band/planner — so even an over/under-shooting band edit is bounded.

---

## 7. Operating the system — how to change each thing

| To change… | Do this | OTA? | Reaches device via |
|---|---|---|---|
| **The band / a setpoint curve** | `UPDATE crop_band_anchors …` (+ `REFRESH MATERIALIZED VIEW mv_band_curve` for the graph) | **No** | anchors-mode: dispatcher `set_band_anchor` service sync (allow ~1–2 cycles + the cfg sensor's publish interval to reflect) |
| **A planner-pushable tunable** | `set_tunable` (MCP) or insert `setpoint_plan` (source/plan_id/reason) | **No** | dispatcher `number_command` (param must be `planner_pushable`) |
| **A non-pushable tunable's default** | edit the registry `planner_pushable`/default, or firmware globals | sometimes | registry change → ingestor roll; or OTA initial_value |
| **Firmware logic / curve math / a new entity** | edit `firmware/**`, run replay-diff + invariants + `firmware-check` | **Yes** | `make firmware-deploy` (gated: alerts, 48h bake, ≤1/wk, sensor-health auto-rollback) |
| **Make the device obey the DB band** | `VERDIFY_BAND_SOURCE=anchors` on the prod ingestor + roll | No (firmware must already expose the anchor entities/service) | — |

Pipeline: push to `main` → CI builds/validates → `bump-dev-digests` → dev
auto-syncs → `prod-promote` (digest-pinned, operator-gated sync) for prod. The
ingestor's state PVC is being migrated to Synology — coordinate before touching it.

**Gotchas worth knowing** (learned the hard way): incremental DB migrations are
applied **by hand** (`kubectl exec -i … psql < file`) — the migrate job is
fresh-DB-only. The `firmware-deploy` post-flash health sweep needs
`VERDIFY_DB_BACKEND=kube`. A 2nd OTA/week needs `FIRMWARE_OTA_FREEZE_OVERRIDE_LOG`
set to a writable path on macOS.

**The band has ONE source of truth: the DB table `crop_band_anchors`** (unified
2026-06-15, item 2). `restore_value:yes` band globals survive an OTA, but you do
**not** force a new value with a firmware one-shot — the dispatcher reconciles
`crop_band_anchors` into NVS on every reconnect and corrects readback drift, so
the device converges to the DB while online. The band globals' `initial_value`
in `globals.yaml` is the conservative DRY **cold-start fallback** only (factory-
fresh boot before the first DB sync); NVS is the offline cache. The former
`band_curve_rev` one-shot on_boot migration — a third, competing source — was
retired. **To change the band, edit `crop_band_anchors`, not firmware globals.**

---

## 8. Opportunities forward to improve

Ranked roughly by leverage. Items (a)–(c) are the highest-value control gaps.

**(a) Wire the per-zone arbiter to actuation (currently dead code).** The owner's
`arbitrate_wet_zone` / `zone_wet_intent` / `zone_priority_*` design is computed
**telemetry-only**; the relays actually fire off legacy flat scalars
(`vpd_target_south=1.3`/`west=1.2`) via `select_most_stressed_zone`. So the
deterministic per-zone band + center=rank-1 priority don't yet control misting.
Wiring them is the single biggest correctness win (firmware OTA + replay-diff).

**(b) Per-zone runtime caps for south/west.** Only `center` has a duty cap; S/W
have none, so they can over-water the pots (west soil saturated at ~85%). Add a
daily runtime ceiling gated on rising soil moisture (firmware OTA). The daytime
duty cuts (`mister_pulse_on_s`, `vpd_weight`) help but don't bound the total.

**(c) Stop the dispatcher tracking fw-v2-stripped params.** `direct_wet_stress`/
`fog_stress_*` no longer exist on the device but are still pushed on every
reconnect → recurring benign `setpoint_unconfirmed` critical alerts. Prune them
from the tracked/push set (and/or suppress the alert for stripped params).

**(d) Make `cfg_*` readback confirmation faster.** Anchor confirmation lags because
the `cfg_*` sensors publish on a 30–60 s interval. (The naming is NOT broken —
verified 2026-06-15: `cfg___mister_center_penalty` maps correctly and confirms;
the `___` comes from the firmware name "Cfg • Mister Center Penalty".) Publishing
on-change would make live tuning observable within seconds instead of cycles.

**(e) Turn the forecast engine into a real overnight dry-air lever.** It's revived
but the economizer/dewpoint-vent path is under-used. Outdoor air overnight is
often ~half the indoor absolute humidity — exhausting it against a dewpoint/
enthalpy gradient is the cheapest dryer we have. Add forecast-gated overnight
vent rules (the `climate_intent.economizer_dewpoint_advantage_f` →
`vent_prefer_dp_delta_f` materialization already exists).

**(f) Give the planner an overnight-drying objective/lever.** Today the planner is
**structurally blind** to the night VPD floor and to per-zone cuts — none of its
~40 pushable tunables can move them, and a SUNSET prompt even biased toward
humidity *retention*. Either add a bounded, planner-pushable "night-dry bias" or
let it author the night-floor anchor within a clamped range, so the AI can
actually help the thing that matters most.

**(g) Reparameterize the band as {night_floor, day_peak} (+ widths).** Four free
anchors per series invite "lumpy"/inconsistent values. Deriving the anchors from
two meaningful knobs makes a wet-night dip unrepresentable and the curve easy to
reason about — fewer, more meaningful controls. (The harmonic interp already
removed the *interpolation* lumpiness; this removes the *authoring* lumpiness.)

**(h) RH-native band authoring.** Operators think in RH; the band is in VPD; the
two diverge with temperature. A thin authoring layer that lets you specify "night
RH = 55%" and stores the implied VPD anchors would prevent the
"why-is-it-soaking-at-VPD-0.5" class of mistakes.

**(i) Close the observability loop. [DONE 2026-06-15, item 1]** The `gh_*` on-chip
telemetry is now ingested: the numeric climate `gh_*` (solar phase, house/zone
targets) already landed; the two **text** decisions (`band_source`,
`zone_wet_granted` = the arbiter's grant) were silently dropped by a hardcoded
`write_diagnostics()` column list and are now written. A `house_band_drift`
wet-night alert now fires when actual house VPD sits out of the commanded band —
the detector that was missing when the house ran RH ~84% for four nights.
Remaining: surface the band audit + mister-duty metric as first-class dashboard
panels.

**(j) Arm the single-writer Lease fence.** The renew-or-die Lease is built but
inert. Arming it removes the last theoretical split-brain window (two ingestors
both writing the ESP32 during a bad roll). Gated — do it deliberately.

**(k) Acknowledge the hard limit: temperature is house-wide.** There is one air
mass and no per-zone thermal actuator, so per-zone *temperature* targets must be
mutually compatible and the priority rank resolves the residual spread. If true
per-zone thermal control is ever wanted, that's a hardware change (zone heaters/
baffles), not software.

---

## 9. Pointers

- **Firmware FSM + relay-transition + safety-rail + bands/hysteresis + 72h-offline
  + compliance spec: `docs/firmware-fsm-spec.md`** (the authoritative firmware-internals
  doc; L2 #344 / L3 #345 acceptance traceability in its §11).
- Firmware: `firmware/lib/greenhouse_{solar,logic,types}.h`, `firmware/greenhouse/`.
- Dispatcher/anchors-mode: `ingestor/tasks/{dispatcher,band_anchors}.py`,
  `ingestor/{esp32_push,solar}.py`.
- DB band: `db/migrations/16x-*.sql`, `db/migrations/170-band-harmonic-smooth-curve.sql`,
  `fn_crop_band_value`.
- Planner: `planner_graph/`, `ingestor/iris_planner.py`, `mcp/server.py`,
  `verdify_schemas/tunable_registry.py`.
- Design notes: `docs/design/firmware-v2-*.md`,
  `docs/design/firmware-v2-settable-anchors-2026-06-15.md`.
- Operating: `docs/runbooks/laptop-operator.md`.
