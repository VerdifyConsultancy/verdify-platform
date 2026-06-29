# Firmware v2 — First-Principles Simplification (2026-06-10)

Status: **DESIGN / FOR OWNER SIGN-OFF.** No firmware code until signed off.
Owner: Jason. Author: laptop-root. Supersedes nothing — extends
`docs/design/vanda-zone-control-design.md` and
`docs/firmware-climate-intent-controller-final-design-2026-05-24.md`.
Backlog anchor: epic **#287** (Greenhouse Control Optimization), children
**#289–#300**, deploy-enablement **#288**.

---

## 0. TL;DR — strip, don't rewrite

The owner's instinct ("the firmware is overly complex, full of crop/scenario
rules that don't belong, make it a dumb AI-driven setpoint follower") is
correct about the *symptom*. The audit's surprise is the *cause*: **the
control core is already the setpoint-follower we want.** The 8-mode band-first
FSM (`determine_mode_band_first`) with delta-error arbitration
(`evaluate_climate_decision` scores temp-error and VPD-error against the served
band and picks the smallest-error action) IS the normalized temp-vs-VPD
controller described. It does not need re-authoring.

The complexity the owner feels is **four bolt-ons around a sound core**, and
three of the four are fixable **without a firmware OTA**:

1. **~5 Vanda-specific heuristics** bolted onto the FSM (night econ-heat
   suppression, dawn/midday mister cadence, fog-stress-window extension,
   direct-wet-stress override, the dedicated overnight micro-pulse). → strip
   from firmware; the deterministic served band carries this instead. *(OTA)*
2. **The curve doesn't follow the sun** — not a firmware bug. The dispatcher
   **never pushes the solar anchor hours**; they freeze at June-solstice
   wall-clock values on reboot, so by December the "dusk" cutoff fires ~1.3 h
   after dark. The solar math exists; it's just not wired. → dispatcher push.
   ***(NO OTA.)***
3. **The band is not emitted deterministically from crop+solar.** The owner's
   model (confirmed 2026-06-10): the target band is a **pure, deterministic
   function of (crop, zone, solar phase)** stored in a table; the dispatcher
   computes it from the solar curve and emits it. The AI does **NOT** author or
   bias the band — it only sets *controller tunables* (hysteresis/temp bias,
   escalation thresholds, min/max run-times, zone-priority rank). Today the
   dispatcher half-does this (a `fn_achievable_envelope` curve) but keyed to
   wall-clock, season-hardcoded, and not zone-complete. → make the band a
   deterministic crop+solar table emission. ***(NO OTA.)***
4. **The buttons are broken** by an inverted interlock (below). → firmware fix. *(OTA)*

So firmware-v2 is **one surgical OTA** (strip heuristics + fix buttons + add
per-zone state-machine activation) on top of **a body of dispatcher/DB work
that needs no OTA at all.** That is a far lower-risk path than a rewrite, and
most of the owner-visible wins (curve tracks the sun, deterministic crop-driven
bands, per-zone priority) ship without touching the device.

---

## 1. What stays in firmware (the reliability floor)

These are crop-agnostic and MUST work with the planner/WiFi offline. **KEEP, do
not touch:**

- **Safety rails:** `SENSOR_FAULT` + `sensor_fault_relay_lock`, `SAFETY_COOL` /
  `SAFETY_HEAT` temp rails, and the SAF-6 safety-cool fog exception that
  overrides the dusk cutoff at dangerous temperature.
- **Equipment protection:** relay min-on/min-off timers (heat 300/300 s, fan
  60/30, fog 60/60, vent 30/30), the Heat2 anti-chatter latch (gas-valve wear),
  the runtime-balanced **fan lead-lag rotation** (verified working: fan1 26.7
  vs fan2 27.9 turn-ons/day over 14 d — within ~5 %), and the
  `SEALED_MIST → IDLE` livelock backoff.
- **Moisture interlocks:** dew-margin gates on every wetting path
  (crown-condensation prevention), occupancy inhibit + its 1-hour failsafe, and
  the SAF-4 **daily-volume hard ceiling + per-zone duty cap** (water budget,
  immune to VPD emergencies).
- **The 8-mode FSM + delta-error arbitration** itself — the setpoint-follower.
- **The manual override LAYER** (it must be firmware-resident for latency and
  offline operation) — but its *semantics are broken* and get rebuilt (§6).

New equipment-protection to ADD (per #299): bring the **misters into the
dwell-protected relay array** and add a **per-relay max-cycles/hour governor**
(fog and center-mister currently cycle ~36×/day ≈ every 40 min — not a
short-cycle crisis, but ungoverned).

---

## 2. What leaves firmware (crop/scenario intelligence → deterministic band)

All of this is bare-root-Vanda velamen logic masquerading as control law. It
moves out of firmware into the **deterministic crop+solar band** the dispatcher
emits (§5) — NOT into the AI. The band shape itself encodes what these
heuristics used to hardcode:

| Firmware thing today | Where it goes | OTA? |
|---|---|---|
| ENV-2 night econ-heat suppression (Vanda day/night drop) | planner pushes a time-dependent night temp/VPD band | yes (#292) |
| IRR-3/4 dawn-rehydrate + midday-drench mister cadence (hardcoded 14:00 etc.) | dispatcher pushes "denser pulse on/gap" anchored to **solar** sunrise/noon | yes (#292) |
| Direct-wet-stress override + fog-stress-window extension | one consolidated "high-VPD wetting extension" band param | yes (#292) |
| CYC-4 dedicated overnight ≤5 s micro-pulse path | **deleted outright** — the smooth 24 h VPD band + normal fog paths handle it under existing safety interlocks | yes (#292) |
| Hard dusk **clock** dry-cutoff (CYC-1/SAF-3 as an absolute kill) | replace with VPD-band governance + minute-granularity taper anchored to real sunset | yes (#292) |
| Outdoor-RH adaptive fog-burst sizing; zone stress-scoring weights | deterministic per-zone `vpd_target_*` from the crop+solar table; adjacency a fixed constant | dispatcher |

**Principle:** the ESP32's only crop knowledge becomes (a) the deterministic
served band (one target + stress edges per zone, computed from crop+solar), (b)
a handful of solar-anchored window/cadence params, (c) controller tunables
(biases/escalation/min-max/priority). No growth-stage branching, no leaf-temp
modeling, no clock literals, and no AI in the band loop.

---

## 3. The crop priority model (owner decision, 2026-06-10)

A **settable ranked priority** is the new core primitive. When the single house
air mass can't satisfy every zone, the highest-ranked crop wins the bias; lower
crops' stress edges act as clamps so nobody gets cooked.

**Rank: 1) Vanda  2) Cannabis  3) Lime  4) Pepper.** Lettuce + strawberry
**dropped** (soft-removed from `crops` 2026-06-10, reversible). All four
survivors are warm-leaning, so the old cool-crop ceiling conflict is gone.

| Rank | Crop | Zone | Sensing |
|---|---|---|---|
| 1 | Vanda orchid | CENTER | center misters + fog; main lights; needs a center VPD sensor (HW gap) |
| 2 | Cannabis (veg/early-flower) | SOUTH | wall misters + fertigation drip; **south soil sensor → cannabis pot** |
| 3 | Lime (potted citrus) | WEST *(tentative move from south)* | wall misters |
| 4 | Pepper (jalapeño-type) | EAST | grow lights; **a soil sensor → pepper pot** |
| — | (empty) | NORTH | equipment-tracked only → maps to house average |

### 3.1 Temperature is ONE house curve (physics), Vanda-anchored

There is one air volume, one set of fans, one vent — **per-zone temperature is
un-actuatable.** So temp is a single house curve, anchored on the priority crop
(Vanda), capped by physics (no shade hardware → achievable midday ceiling
~84–86 °F, not Vanda's 87–88 °F ideal) and clamped by the warm-edge of
cannabis/pepper (~84–85 °F). This is exactly what `fn_band_setpoints →
fn_achievable_envelope('center', …)` already does — **verified live the band is
72.6 / 81.3 °F right now, not the old broken 78 °F.** The remaining fix is to
make this curve **solar-relative** (§4), and to drop the season hardcode.

House temperature curve (solar-anchored, °F — these are anchor points; the
cosine engine interpolates between them):

| Solar anchor | Target | Min | Max | Note |
|---|---|---|---|---|
| Sunrise (SR) | 66 | 60 | 72 | Vanda night→day ramp begins |
| SR + 2DH | 74 | 68 | 80 | |
| **Solar noon (SM)** | **84** | 76 | 86 | Vanda wants 87; capped to achievable + cannabis/pepper warm-edge |
| SM + 2DH | 83 | 75 | 86 | |
| Sunset (SS) | 73 | 66 | 80 | drydown begins |
| SS + 3NH | 66 | 60 | 73 | |
| **Solar midnight** | **64** | 60 | 70 | Vanda 65 + cannabis ~60 °F mold floor → 64 |
| next SR − 2NH | 64 | 60 | 70 | pre-dawn coolest |

### 3.2 Per-zone state-machine activation (owner ask, 2026-06-10)

Today the firmware runs **one** mode decision on greenhouse-average sensors; the
per-zone behavior lives only in the mister router in `controls.yaml`. The owner
wants the **state machine itself activated per zone.** Reconciled with the
one-air-mass physics:

- **Thermal axis stays house-wide** (heat / vent / fans act on the single air
  volume — un-actuatable per zone). One thermal FSM, Vanda-anchored.
- **The VPD/wetting axis becomes a per-zone FSM:** each zone (CENTER, SOUTH,
  WEST, EAST) evaluates its own VPD state against **its own deterministic zone
  band**, producing a per-zone wetting intent. The **mister router** then
  arbitrates those intents by the explicit **zone-priority rank** (§5) under the
  shared safety/duty interlocks. So "per-zone activation" = N parallel
  VPD sub-state-machines + 1 house thermal state machine + a priority-ranked
  actuator arbiter. Each zone's band, target, and stress edges are
  independently followed and independently logged/compliance-scored.

This keeps the FSM core (the delta-error follower) but instantiates it per zone
on the only axis that's physically zone-controllable, which is exactly what
makes per-zone compliance trustworthy.

### 3.3 Per-zone VPD targets (the zone-controllable axis), via the misters

Mister authority is modest (~0.1 kPa per burst, measured), so big zone spreads
need sustained differential misting + decent zone isolation. Per-zone VPD
targets (kPa, anchor points from the researched envelopes):

| Solar anchor | CENTER (Vanda) | SOUTH (cannabis) | WEST (lime) | EAST (pepper) |
|---|---|---|---|---|
| Sunrise | 0.60 | 0.85 | 0.60 | 0.80 |
| **Solar noon** | **1.05** | **1.18** | **1.10** | **1.22** |
| Sunset | 0.60 | 0.95 | 0.70 | 0.90 |
| **Solar midnight** | **0.50** | **0.75** | **0.57** | **0.74** |

CENTER is the owner-provided Vanda table (middle-path: midday ~1.0–1.15, raised
night floor ~0.5, **no wet night misting**, root-color gates water). SOUTH runs
dry to defend cannabis bud-mold. All targets land at 48–75 % RH — reachable
with intermittent overhead fog in dry Colorado. The dry-CO failure mode is
**VPD-too-HIGH** (air too dry), so the controller should bias toward *adding*
humidity by day; never chase any zone below ~0.95 kPa at these temps (>70 % RH →
mildew/Botrytis risk). Full 25-step per-crop tables: appendix.

### 3.4 Owner decisions still open (don't block the build)

- **Lime zone:** south (with cannabis) or moved to west. Design assumes WEST;
  trivially re-pointed.
- **Cannabis flower flip:** once it flowers it needs 12 h *uninterrupted* dark —
  guarantee zero light-leak from the other zones' 16–18 h grow lights
  (partition/blackout, independent of climate). Until then it's veg-banded.

---

## 4. Solar-relative curve (dispatcher fix — NO OTA, ships first)

**This is the single highest-value, lowest-risk change** and it directly fixes
"the curve doesn't adjust as the days get longer."

Today: `crop_target_profiles` is keyed on **`hour_of_day` 0–23 wall-clock**; the
orchid curve peaks pinned at 14:00–15:00 regardless of true solar noon; and the
dispatcher pushes **zero** solar-anchor updates (verified 0 Python hits for
`dusk_cutoff_hour`/`night_start`/`night_end`), so those firmware globals stay at
their June-solstice reboot defaults.

Fix (all dispatcher/DB, behind the existing `setpoint_plan → cfg_*` readback
loop, no firmware change):

1. Dispatcher computes **sunrise / solar-noon / sunset** every cycle (lat/lon +
   date via an ephemeris; today there's only raw `solar_w_m2`, no astronomical
   source) and **pushes** `dusk_cutoff_hour`, `night_start_hour`,
   `night_end_hour`, the dawn-rehydrate anchor, and the fog-window — never
   reboot-frozen.
2. Re-key the band lookup so the served curve is a **function of solar phase**
   (SR/SM/SS interpolation), not wall-clock hour — the day stretches/compresses
   the curve as SR/SS drift.
3. Drop the `season='spring'` hardcode (use `fn_current_season`, already exists).
4. Re-point `fn_zone_vpd_targets` so SOUTH→cannabis, WEST→lime, EAST→pepper once
   those crops are active with profiles (CENTER→Vanda already correct).

Threads **#291** (req:B, W0, no OTA) and **#300** (schema/registry rows + cfg
readbacks land FIRST — schema-lands-first discipline). **#292** then removes the
now-redundant firmware clock-cutoff + micro-pulse (OTA, after #291 soaks).

---

## 5. The band is DETERMINISTIC (owner decision, 2026-06-10) — NO AI in the band loop

**The target band is a pure function of (crop, zone, solar phase), stored in a
table, emitted by the dispatcher from the solar curve. The AI never authors or
biases it.** This is the owner's explicit call and it is the *more* first-
principles, more reliable design: the band is deterministic, reproducible, and
debuggable; the controller is never chasing an LLM-moved goalpost.

```
band(zone, t) = emit_solar( crop_target_profiles[crop_of(zone)], zone_priority, solar_phase(t) )
   where solar_phase(t) is derived from real sunrise/solar-noon/sunset
   and the temp axis is the single house curve (Vanda-anchored, physics-capped)
   while the VPD axis is per-zone.
```

What this means concretely:

- **`crop_target_profiles` is the source of truth**, re-keyed from `hour_of_day`
  to **solar phase** (fraction of the SR→SM→SS→SR cycle). One deterministic
  row-set per crop × growth-stage × season; the dispatcher interpolates by solar
  phase and emits. No planner write touches these values.
- **The dispatcher is a pure emitter:** read profile → place on today's solar
  clock → apply the house-temp-curve reconciliation (Vanda-anchored intersection
  for temp, per-zone for VPD) → push. Deterministic given (date, lat/lon, active
  crops, zone-priority).
- **Remove the planner→band path entirely.** The current `FORCED_BAND` clamp in
  `dispatcher.py` (~537–541, 689–696) stays *as a clamp on the planner*, but the
  cleaner end-state is that the planner simply has no band-authoring tool — the
  band comes only from the table+solar emitter.

### 5.1 What the AI *does* control (tunables only — pushed well in advance)

The planner's job is to tune **how** the controller chases the deterministic
band, plus the zone priority — never the band itself. All are existing or
net-new Tier-1 tunables with `cfg_*` readback:

- **Hysteresis bias / temperature bias** (`bias_heat`, `bias_cool`,
  `*_hysteresis`) — already wired.
- **Escalation thresholds** (heat1→heat2 `dH2`, fan1→fan2
  `cool_stage2_over_high_f`) — `dH2` needs to become tunable.
- **Per-equipment min/max run-times** — MIN exists; **MAX is missing**, add it.
- **Zone-priority rank** (`zone_priority_<zone>`) — net-new (§3.2, the router
  arbiter).

This is the clean separation the owner wants: **deterministic targets, tunable
pursuit.** It also *removes* a net-new issue from the plan (no "planner
band-authority" work) — a simplification, not an addition.

---

## 6. Buttons — the inverted interlock (firmware fix, OTA)

Root cause of "I push the fans button and they won't come on," verified in
`controls.yaml`:

- **`vent_lock` is inverted (line ~559):**
  `if(vent_lock_active && !manual_fan_active && mode!=SAFETY_COOL){ fan1=fan2=vent=false; }`
  — it kills **both fans** instead of suppressing only the vent. The owner's
  vent-bypass case (fans ON + vent CLOSED, winter house-air pull) is the exact
  inverse of what the code does.
- **FANS opens the vent only `if(mode!=SAFETY_HEAT)`** — spec wants it
  unconditional for the timeout.
- **HUMID is blocked by `manual_fog_safety_block` even when latched** — spec:
  the button supersedes climate gates; only true safety rails interrupt.
- Buttons sit **below** the dwell timers, fog-safety stack, and
  `sensor_fault_relay_lock`, and are applied with `force_on=false`. The fix
  pattern already exists in-tree: the fog micro-pulse uses `force_on=true`.

Rebuild (per #289 + a #290 owner decision on winter vent-bypass semantics):

- Latched **FANS / HUMID / VENT-BYPASS** sit immediately below safety rails and
  **above all** dwell/fog-safety/automation, applied with `force_on=true`.
- **Momentary toggle** with a **deadline-based latch** (`*_until_ms`, like
  `feed_hold_until_ms` — not a bare bool that orphans on reboot) and a
  **configurable timeout, default 10 min.**
- FANS = both fans ON + vent OPEN; HUMID = fogger ONLY; VENT-BYPASS = fans ON +
  vent **CLOSED** (fix the inversion). Re-press toggles back to automation; only
  safety rails (and the toggle) interrupt.
- **Net-new unit tests + invariants** — the replay corpus does not exercise
  button paths today, so this is the one place the redesign genuinely adds test
  surface rather than reusing replay.

---

## 7. Two lighting schedules (mostly already there)

The two circuits already exist as separate tunable sets — `gl_main_*` (main
lights, orchids/center) and `gl_grow_*` (grow lights, hydro shelves) — each with
its own DLI target, photoperiod minutes, lux threshold/hysteresis, sunrise hour.
They're just **undifferentiated** (both default DLI 14 mol, 960 min) and
**fixed-hour, not solar.** Fix (#294/#295, dispatcher + tunable values):

- **Main (Vanda):** DLI ~12–14 mol, photoperiod ~12–14 h, lux-threshold tuned to
  orchid "high light," sunrise/sunset anchored to **solar**.
- **Grow (hydro pepper):** DLI ~20–22 mol, photoperiod ~14–16 h, solar-anchored.
- Both windows track real sunrise/sunset (same ephemeris as §4), not a frozen
  clock hour. West grow-lights are currently unplugged — telemetry should flag
  "expected on, no lux response" (dead-fixture detection, #295).

---

## 8. Sequencing, gates, and backlog threading

**No new epic.** Firmware-v2 is execution of **#287**. Order (schema-lands-first):

1. **#300** (P1, W0, no OTA) — registry/doc rows + cfg readbacks for the
   solar-anchor + cadence tunables; fold the minor tunable gaps (dH2 tunable,
   MAX run-times). *Blocker for everything.*
2. **#291** (req:B, W0, **no OTA**) — **the deterministic solar band emitter:**
   dispatcher computes + pushes solar anchors; re-key `crop_target_profiles` to
   solar phase; emit the band as a pure function of crop+zone+solar; season
   un-hardcode; re-point zone VPD (SOUTH→cannabis, WEST→lime, EAST→pepper). The
   planner band-authoring path is removed, not added. **Biggest owner-visible
   win, zero device risk.**
3. **Net-new: zone-priority ranking** — `zone_priority_<zone>` tunables + cfg
   readback + mister-router consumes an explicit rank (reuses
   `vanda-zone-control-design` §3 framing). The owner's settable-priority ask.
4. **Net-new: `setpoint_snapshot`/`setpoint_changes` schema extension** —
   `zone`, `band_role`, `target_value` columns so per-zone deterministic bands +
   a single target value are representable/auditable.
5. **#292** (req:B, W0, **OTA**) — strip the firmware heuristics (ENV-2,
   dawn/midday cadence, direct-wet, fog-stress, CYC-4 micro-pulse, clock-cutoff);
   **add per-zone VPD state-machine activation + the mister-router priority
   arbiter** (§3.2); deterministic VPD band governs overnight; update invariants
   #21/#24.
6. **#289 / #290** (req:A, **OTA**) — button override FSM rebuild + winter
   vent-bypass decision + new unit tests/invariants.
7. **#294 / #295** (lighting), **#299** (mister dwell/governor): fold in.
8. **Crops + profiles data change** (one coherent migration): add cannabis +
   lime as active crops WITH new solar-keyed deterministic profiles; the
   lettuce/strawberry soft-remove is already done.

*(Net change vs. the first draft: the "planner band-authority" net-new issue is
DELETED — the band is deterministic, so there is no AI-authoring work to do.)*

**Gates (CLAUDE.md freeze rules):** OTA blocked until **#288** clears (k3s OTA
secret sealed — partially done 2026-06-10; agent-pod DB access; firmware CI
lane). Per OTA: no open critical alert (currently 2 open — planner alerts, must
clear first), ≤1 OTA/week, 48 h bake, **replay-diff THRESHOLD_PCT=0** with
coordinator override for the intentional divergence (cutoff removal), cfg_*
readback per new tunable, service-restart-drift doc for schema touches. The
no-OTA dispatcher work (#291 deterministic solar band emitter + zone-priority +
schema extension) can ship and prove value while the OTA queue waits on #288 +
alert clearance.

---

## Appendix — full 25-step per-crop envelopes

Researched solar-relative tables (SR / SM / SS anchored, DH/NH steps) for
cannabis-veg, lime, and pepper are generated in the crop-band-research workflow
output and become the `crop_target_profiles` rows (re-keyed to solar). Vanda is
the owner-provided table. Midday / night anchors summarized in §3.2; the
controller interpolates between solar anchors via the existing cosine engine.
