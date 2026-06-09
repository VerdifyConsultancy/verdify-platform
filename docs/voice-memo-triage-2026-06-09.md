# Voice-memo triage — 2026-06-09

Jason recorded an in-greenhouse voice memo on 2026-06-09 describing first-principles
problems with the firmware and automation story. This document maps each requirement
against verified current behavior (multi-agent code triage, 2026-06-09; all
load-bearing button/climate diagnoses independently re-derived and confirmed by
adversarial code-walks). File references are to this repo at `live/platform-main`
(385590c).

## The requirements (R1–R8)

| # | Requirement | Status |
|---|---|---|
| R1 | Fans button: momentary toggle → both fans ON + vent OPEN for configurable timeout (default 10 min), superseding all automation; second press cancels | **Partial** — mechanism exists, precedence doesn't |
| R2 | Humid button: same semantics, fogger only | **Broken** — subordinate to every fog rail; dead while vent is open |
| R3 | Vent bypass: fans ON with vent CLOSED (winter house-air pull) | **Missing** — current button does the opposite (full air lockdown) |
| R4 | Buttons supersede everything; live observation: fan press → no fans | **Broken (recurring)** — last failed press 2026-06-08 PM, happens regularly; points at the systematic min-off dwell cause (H2); `force_on` fix proceeds now (#289), discriminator is confirmation not a blocker |
| R5 | Temp+VPD on solar-aligned smooth curve; remove "weird orchid dry cutoff" | **Half-shipped** — DB curve is solar+smooth (mig 145); firmware night rails frozen at June clock-hours because the dispatcher push was never built |
| R6 | Two lighting schedules: main=orchid photoperiod, grow=hydroponics | **Partial** — per-circuit machinery shipped, but one shared max-DLI(pepper) policy feeds both |
| R7 | Soil moisture/EC feedback loop on irrigation/fertigation | **Missing by design** — read-side only; gated on probe bring-up (RM-3), now 2 generations stale vs physical reality |
| R8 | One integrated automation story (state machine + PID + forecast) | **Partial** — ClimateIntent single-path is the declared architecture; manual/button precedence was never part of it; PID was explicitly evaluated and rejected |

---

## R1–R4: Buttons (verified)

### What exists

Three physical momentary buttons on the PCF8574 input expander `pcf_in_2` @ I2C 0x24
(`hardware.yaml:221-227`): PB Fan Burst (pin 6), PB Vent Bypass (pin 7), PB Fog Burst
(pin 5). Each fires a template button also exposed in HA ('Fan Cycle', 'Vent Bypass
Toggle', 'Fog Cycle') — physical and HA presses share one toggle state. Each press
toggles a timed script (default 10 min, second press cancels — matching the memo
spec) and silently forces occupancy empty for 60 min (`greenhouse.yaml:584-592`).
Buttons never touch relays directly; the 5 s control loop is the only actuator.

### Why the fan press produced no fans (R4) — confirmed precedence chain

Net precedence today: `SENSOR_FAULT > safety modes > manual fan > vent lock > FSM`,
**but relay dwell timers and the entire fog gate stack sit above manual requests.**
The design comment at `controls.yaml:549-552` says manual overrides "are still below
the sensor-fault, safety-heat, and fog-safety rails" — i.e., current behavior is the
documented intent, the opposite of the memo spec. The digital-twin design even treats
manual-override divergence as "legitimate, by design" (`firmware-digital-twin.md:59,149`).

Ranked, code-confirmed causes for "pressed fans, nothing happened":

1. **Silent toggle-cancel (H1).** No debounce filter on the PCF8574 binary_sensors
   (`greenhouse.yaml:325-335`); on_press is a bare toggle. A prior press (physical or
   the HA 'Fan Cycle' button) or a contact bounce spanning ≥2 I2C polls leaves
   `manual_fan_active` latched, so the next press *cancels*. There is no local
   feedback (no LED/buzzer) — an armed-but-blocked window is invisible in the
   greenhouse.
2. **Fan min-off dwell (H2).** The manual branch sets `willFan1/2=true`
   (`controls.yaml:553-557`) but fans are applied with `force_on=false`
   (`controls.yaml:808-809`), so `can_on()` (`controls.yaml:99`) blocks restart for up
   to `min_fan_off_s` (production 90 s) + 5 s loop latency. The vent meanwhile *does*
   get `force_on` (`controls.yaml:807`) — producing the confusing
   vent-moves-but-no-fans signature. **Bound:** H2 only explains ≤ ~95 s of delay,
   not a dead 10-min window — if Jason waited longer, H2 is excluded.
3. **SENSOR_FAULT lock (H3).** Blocks the manual branch entirely
   (`controls.yaml:553, 564-571`). Two real plausibility edges can pin it: VPD is
   clamped to exactly 10.0 (`controls.yaml:213`) while `sensors_plausible` requires
   `vpd < 10.0f` *strictly* (`greenhouse_logic.h:35`); RH probes are averaged with no
   clamp, so a saturated probe reading 100.x% fails the `<=100.0` test.
4. **Dead PCF8574 (H4).** Press never reaches firmware; nothing logs.

**Recurring failure (operator confirmation 2026-06-09):** the failed press was the
**evening of 2026-06-08**, and it **happens regularly** — not a one-off. A recurring,
systematic failure points primarily at the min-off dwell cause (H2: fans applied with
`force_on=false` at `controls.yaml:808-809`, so a press within ~90 s of a fan cycle-off
is silently swallowed) and/or a habitual double-press toggle-cancel (H1). The `force_on`
fix (below) addresses the recurring case and **should proceed now without waiting on the
exact-timestamp discriminator — the DB pull is confirmation, not a blocker.** Tracked as
issue #289.

**Discriminator (live DB confirmation, no longer gating the fix):** the
`fan_burst_active` series (`entity_map.py:329`) plus `fan1/fan2` equipment_state and
`mode_reason` at press time. Flag armed + fans off → H2/H3 (mode_reason
`sensor_fault` separates H3); flag flicker/fall → H1; no change → H4 (and absence of
the `occupancy_quiet_override` ESP_LOGW in ESP32 logs confirms the press never
arrived). A blocked press writes **no** override_events row — manual/burst types are
absent from `OVERRIDE_EVENT_TYPES` (`telemetry.py:26-41`), so the planner/scorecard
are blind to all of this.

### R2 — the humid button is the weakest surface

Manual fog must pass the *entire* fog gate stack: SNTP validity, dew margin ≥ 10 °F,
fog window 07–17, RH ≤ 90 %, temp ≥ 55 °F (`controls.yaml:538-547, 558`), then
survive leak / occupancy / irrigation-conflict / water-budget / SAF-4 hard cap /
fert-master kills (`controls.yaml:766-768`), then `fog_closes_vent && vent_is_open`
(`controls.yaml:785-787`, default true). Since any fan activity force-opens the vent,
**the humid button does nothing whenever fans are running — R1 and R2 boosts are
mutually exclusive as built.** All rejections are silent.

The timeout is also unreliable: `fog_burst_minutes` has three writers — the operator
number, the outdoor-RH adaptive block that stomps it to 3/5/8 min on every RH-regime
change (`controls.yaml:313-337`), and boot init. The operator value effectively never
survives.

### R3 — vent bypass is the opposite of the spec

`vent_lock_active` forces fans **and** vent OFF (`controls.yaml:559-563`) — an air
lockdown, not fans-with-vent-closed. Independently, the fan→vent interlock
(`controls.yaml:580-587`) force-opens the vent whenever any fan is on/wanted outside
SAFETY_HEAT, and the no-fan-without-vent invariant whitelists only SAFETY_HEAT
(`greenhouse_logic.h:1993-2002`). The winter house-air use case is structurally
unreachable from every override surface. Also: the script caps the window at 30 min
while the number entity allows 60 (`greenhouse.yaml:617` vs `tunables.yaml:415-416`).

### Registry drift (will mislead any agent triaging from the registry)

`fan_burst_min` / `vent_bypass_min` / `fog_burst_min` are listed in
`RETIRED_TUNABLES_REG`, classed `retired`, with notes claiming firmware "does not
consume it for timing" (`tunable_registry.py:829,844,859,2719-2730`). **False** — the
burst scripts consume them as the delay (`greenhouse.yaml:601-632`), and have since
the 2026-04-09 rewrite; the notes were wrong at introduction (2026-05-16, commit
4f47892). Net effect: the only live config path for burst durations is the HA number
entities, values revert to 10 min on reboot (`restore_value: no`), and MCP rejects
the names.

### Fix shape (R1–R4) — firmware-side, small surface

The dispatch reader confirmed nothing server-side can be the culprit (fan relays are
`internal: true`, not in SETPOINT_MAP; Slack relay control hard-blocked; quiet-mode
overlays retired with "quiet behavior belongs in firmware"). The boost window already
lives in the right place; what's missing is precedence, not placement:

1. Pass `force_on=true` for fans (and vent/fogger during the manual window) — mirror
   the vent's existing escape and the fog micropulse. This addresses the recurring H2
   dwell-swallow directly and **proceeds now** (discriminator = confirmation, not a
   blocker). Tracked as issue #289.
2. Debounce the PCF8574 inputs (`delayed_on` filter) and split "start" vs "cancel"
   feedback (at minimum an ESP_LOG + a distinct HA event; ideally a panel LED).
3. New vent-bypass semantics: a modifier flag that (a) keeps manual-fan boost active,
   (b) carves the case out of `fan_requires_vent`, (c) extends the
   no-fan-without-vent invariant whitelist. Decide whether it also suppresses the
   heat/fan interlock (winter heater + fans simultaneously).
4. Manual fog tier: define which gates a manual press beats (window hours, RH
   ceiling, dew margin, fog_closes_vent) vs hard safety it must keep (leak,
   fert-master co-fire SAF-5, SAF-4 volume ceiling). Needs Jason's call — see
   Decisions.
5. Give `fog_burst_minutes` a single owner (separate the adaptive duration variable
   from the operator burst tunable).
6. Add manual/burst-block types to `OVERRIDE_EVENT_TYPES` so blocked presses are
   observable; fix the three registry notes + retired classification; reconcile the
   vent-bypass 30/60 clamp.

**Validation caveat:** the manual-override layer lives only in the `controls.yaml`
lambda — zero references in `firmware/test/`; the replay corpus does not execute
button paths (the 2026-05-30 staged OTA replayed 0/193,525 divergent precisely
because gated paths live outside `replay_emit`). A replay-diff for this fix will
trivially pass. Positive evidence must come from new unit tests + new invariants
(boost-window semantics), per the backlog-closeout precedent. Consider moving the
override logic into `greenhouse_logic.h` (e.g. a `ManualOverrides` input to
`resolve_equipment`) so it becomes replay-testable — that is the durable fix for the
"invisible layer" problem.

---

## R5: Climate curve & the "orchid dry cutoff" (verified)

### What's already right

The served temp/VPD band **is** solar-tracked and smooth: migration 145 (live
2026-05-30) computes a C1-continuous cos² diurnal interpolation between Vanda night
anchors (61–67 °F, 0.75–0.85 kPa) and day anchors (~78–88 °F, 0.95–1.20 kPa), peak =
solar noon + 2 h thermal lag, width tracking day length; dispatcher samples every
300 s. Planner triggers (SUNRISE/SOLAR_MAX/SUNSET…) are astral-ephemeris-derived and
shift with season. This half of R5 is largely done.

### The "weird orchid dry cutoff" — identified and confirmed

It is the **CYC-1/SAF-3 dusk cutoff**: `past_dusk_cutoff()`
(`greenhouse_logic.h:129-132`) is a pure integer-hour test that hard-stops ALL fog and
climate misting in [18:00, 06:00) regardless of VPD, evaluated before every
VPD/stress gate; only relief is the ≤5 s CYC-4 micro-pulse (VPD > 1.25 kPa, 10-min
lockout) and a SAFETY_COOL exemption unreachable overnight. It was built deliberately
for Vanda velamen dry-down (ACC-1) — so "remove" likely means **re-anchor to sunset +
soften the edge**, not delete (Jason to confirm, see Decisions).

The confirmed structural bug: firmware comments and the Vanda design promise "the
dispatcher pushes dusk_cutoff_hour = sunset − 2 h" (`greenhouse_logic.h:126-128`,
`globals.yaml:540`), and unified-backlog C2 is a P0 row — **but the dispatcher half
was never built.** `dusk_cutoff_hour` / `night_start_hour` / `night_end_hour` appear
in zero Python files, are absent from the tunable registry and `SETPOINT_MAP` (so the
dispatcher *cannot* address them and MCP rejects them), have **no cfg_* readbacks**
(despite the rule-6 banner claiming compliance — the CI gate is not actually
satisfied), and are `restore_value: no`, resetting to June-solstice values 18/20/6 on
every reboot. By December the "sunset−2h" cutoff fires ~1.3 h *after* dark.

Invariants #21 and #24 codify the sharp cutoff — any softening must update them.

### Adjacent confirmed defects

- **Dawn-rehydrate anchor frozen at 7.** IRR-3 anchors to `gl_sunrise_hour`
  (`controls.yaml:496-501`), but the legacy push (`dispatcher.py:727-735`) is
  *statically dead code*: `FIRMWARE_HAS_PER_CIRCUIT_LIGHTING` is always True at
  import time (all four sentinels have cfg readbacks), so only `gl_main_/gl_grow_*`
  are pushed and nothing refreshes the legacy global. The anchor resets to 7 on every
  reboot and is never updated by any automated path. Fix: anchor to
  `gl_main_sunrise_hour` (already pushed seasonally) + add a cfg readback.
- **Two disagreeing solar models.** Triggers use astral; the band curve uses
  `fn_solar_altitude`, which hardcodes solar noon at 13:00 local (no equation of
  time/longitude correction) — ~10–20 min disagreement.
- **No forecast in the curve.** Open-Meteo hourly (incl. cloud cover, radiation) is
  fetched but requests no daily block — forecast sunrise/sunset is never ingested;
  the band is pure astronomy. Forecast reaches actuation only via LLM-chosen
  ClimateIntent biases at 5–8 coarse step waypoints/day. Firmware
  `SensorInputs.solar_w_m2` is populated but never read (a unit test asserts no solar
  pre-cooling); `DESIGN.md:43-47` documents a feed-forward that does not exist.
- **PID:** explicitly evaluated and rejected twice (launch-response-pack, 2026-05-16
  control-loop audit) in favor of the band-first FSM + ClimateIntent. The memo's
  "PID" language conflicts with that standing decision — surfaced in Decisions rather
  than silently adopted either way. The smoothness Jason wants is achievable within
  the current architecture (sunset-anchored rails, taper instead of step, slew-limited
  duty) without introducing continuous control.
- **In-flight related work:** migration 160 (orchid VPD band realign + stress
  envelope widen to 1.62) is dev-tested and **gated on Jason since 2026-06-07**
  (`verdify-band-live-apply-gated.md`); the "Band Tuning — Diurnal Adjustment"
  Grafana dashboard is already deployed.

### R5 fix sequence

1. Registry rows + dispatcher push for `dusk_cutoff_hour`/`night_start_hour`/
   `night_end_hour` from `fn_solar_sunset_hour` (the C2 design, dispatcher half) +
   cfg_* readbacks. Pure ingestor/schema work — **no OTA needed** for the seasonal
   re-anchoring, since the firmware consumes the values already.
2. Same push pattern for `fog_time_window_start/end` (sunset-relative).
3. Firmware (OTA, later): minute-granularity windows; taper/ramp at the cutoff
   instead of a step; update invariants #21/#24 accordingly.
4. Optional richer layer: cloud-cover-adjusted effective dusk/dawn from the
   already-fetched Open-Meteo hourlies (Decision 4).
5. Decide migration 160 (already staged, awaiting Jason).

---

## R6: Lighting (two schedules)

Per-circuit machinery shipped 2026-05-16: independent qualified-light-minutes state
machines per circuit, separate `gl_main_*`/`gl_grow_*` tunables with cfg readbacks,
per-circuit dispatch. **But both circuits derive from ONE policy**:
`fn_lighting_policy` picks the single highest-DLI active crop (`LIMIT 1` —
currently pepper, DLI 22, window 6–22) and fans it to both circuits identically. The
orchid (lower-DLI Vanda) photoperiod is thereby overridden on the main circuit. No
repo artifact maps circuits to benches/zones (main=center orchids? grow=west
shelves? — needs Jason's confirmation), there is no photoperiod-hours concept per
crop, and the only divergence path (operator setpoint_changes) is actively
re-superseded by the confirmation sweep.

**West grow lights (shorted, unplugged) are invisible and the accounting is
self-deceiving:** zero power/current telemetry on either Lutron circuit; the
controller keeps cycling `switch.greenhouse_grow`; switch-ON time counts as qualified
light minutes and credits phantom DLI (0.4515 mol/m²/h) — the hydro shelf "meets" its
photoperiod with dark fixtures. **Immediate mitigation: set `sw_gl_grow_auto_mode=0`**
(also a safety question with a shorted fixture). Cheapest detection fix: a Shelly PM
on the circuit, or an indoor-lux-delta plausibility check.

**Coupling trap:** the greenhouse-wide biological-activity window (gating ALL
direct-wet irrigation) is derived from the MAIN circuit's start/target
(`dispatcher.py:102-147`). Splitting main to an orchid photoperiod will silently move
every irrigation window — decouple or re-map first.

**Cannabis warning:** adding a cannabis crop row would likely become the new max-DLI
crop and hijack BOTH photoperiods via the same `LIMIT 1`, and shift the activity
window. Cannabis is also photoperiod-sensitive (flowering) — supplemental-light
spillover into the south zone vs. the orchid schedule is a real conflict. Don't add
the crop row until the per-circuit policy split lands.

### R6 fix sequence

1. Jason confirms circuit→fixture mapping (and James's cannabis photoperiod intent).
2. Operator action now: `sw_gl_grow_auto_mode=0` while the circuit is dead.
3. Schema/DB: per-circuit crop policy (circuit→zone/crop join; photoperiod-hours
   field), replacing the single max-DLI collapse. Decouple the activity window from
   the main circuit first.
4. Health: out-of-service flag concept for equipment + a delivered-light
   plausibility check (lux delta or Shelly PM) before re-energizing the west circuit.

---

## R7: Irrigation / fertigation feedback

**There is no closed loop, by explicit design**: firmware irrigation is
clock/day-mask/air-VPD driven; soil data is read-side only ("no actuation, no device
write"); the planner receives no soil context. The whole acceptance pipeline
(RM-3/C-RM3/I-RM3, `irrigation-feedback-bringup.md`) is gated on the exact probes
Jason just physically moved.

**The physical record is now two generations stale.** Repo says: south probes
unpotted (Canna on patio per 2026-05-29 correction), south_1 stuck-at-zero since
2026-05-21, west probe in "Unknown pots". Memo says: south_1 (the moisture+temp+EC
SEN0601) now in the **new lime-tree pot** (south wall, under misters, wall-drip
head); west probe in a **hydroponics pot** (drip head, under west misters + grow
lights); second south probe (SEN0600) **homeless**. The stuck-zero diagnosis, the S1
"unpotted" alert work, `soil_moisture_targets` thresholds (still Canna-Lily-seeded),
`zones.yaml`, and the acceptance gate keys all need re-baselining. Note: probe-media
recontact in the lime pot may have already "fixed" stuck-zero — check the live series
first. Hydro-substrate VWC calibration for the west probe is a real question.

**Overwatering ("everything is very wet"):** no saturation/overwatering alert exists
— `saturation_pct` is seeded in `soil_moisture_targets` (mig 064) but nothing
evaluates it; the only soil alert is dryout. Plausible mechanism: daily-by-default
drip day-masks (127) + misters double-watering pots beneath (interlock-modeled but
never quantitatively credited) + no feedback. Immediate mitigations available without
firmware changes: trim `irrig_*_days_mask` / durations via existing dispatcher knobs.

### R7 fix sequence

1. Re-baseline physical truth: zones/topology/`soil_moisture_targets`/sensor_registry
   rows for lime tree, cannabis, hydro pot, homeless probe (decide its new home —
   candidate: the cannabis planting, which is the only new south-zone crop without a
   probe). Topology supports per-position sensors; the import script only does
   zone-level — extend it.
2. Saturation alert (read-side, cheap, addresses today's pain): evaluate
   `saturation_pct`, page when a zone sits above it.
3. First closed loop, dispatcher-side (no OTA): skip/shrink scheduled drip jobs when
   zone moisture > threshold — fits the existing IRRIGATION_SCHEDULE_PARAMS push
   path and avoids the controls.yaml replay-coverage problem. Firmware-side loop
   later via the IRR-3/IRR-4 stub pattern if needed.
4. EC closed-loop fertigation stays future (no doser hardware; recipe row dormant by
   design).

---

## R8: One automation story

The declared architecture (ClimateIntent single-path: firmware owns safety+relays,
dispatcher/crop policy owns bands, planner owns bounded tactics) already *is* the
"one story" — and the "it got weird" history (2026-04-21 whipsaw, sprint-10/11
two-band rule, abandoned alternate controller) is well documented and should not be
re-litigated. What the memo actually adds to R8:

1. **Manual/button precedence was never specified as part of the story** — no doc or
   schema says what a boost may supersede. That spec (this triage, section R1–R4) is
   the missing chapter, not a rewrite.
2. The behavior emerges from ~10 stacked incident-driven hard rails interacting at
   hour granularity — the verified source of perceived weirdness. The R5 fixes
   (solar re-anchoring, minute granularity, tapers) smooth this without a new
   controller.
3. The authoritative Vanda design (2026-05-29) predates the physical changes
   (shelving out, lime tree, cannabis, dead west lights, probe moves) — needs a rev.

**"Don't throw the baby out with the bathwater" maps cleanly to the standing rules:**
no alternate controllers, replay zero-divergence, single live control path.

---

## Process constraints on shipping any of this

- Freeze rules in force: no OTA with open critical alerts, ≤1 OTA/week, 48 h bake,
  replay-diff THRESHOLD_PCT=0, cfg_* readback per new tunable, full PR artifact set.
- **OTA sealing blocks only the firmware-OTA subset; tracked as #301.** Post-k3s-cutover
  the `.150` VM is off, preflight is re-homed to kube-exec, but the OTA password is **not
  yet sealed in k3s** and the rollback-floor refresh (#256) is staged only. This is a
  tracked backlog item (#301), turnkey via PR #309 — **not a hard blocker.** It only
  gates the firmware halves (button fix #289, dusk-cutoff firmware #292); the rest of the
  control-optimization program (dispatcher/registry/DB-policy work) ships without any OTA.
  Seal the secret before the R1–R4 button-fix OTA.
- Live firmware version needs confirmation (staged 2026.5.30.1314 vs live
  2026.5.30.1155) via kube-exec, plus live-DB confirmation that migrations 145/146+
  applied per the 2026-05-30 runbook.
- Much of R5–R7 ships **without OTA** (registry + dispatcher + DB policy work) — do
  those first while the button-fix OTA bakes through the process gates.

## Decisions needed from Jason

1. **R4 incident timestamp — ANSWERED (2026-06-09):** the failed press was the **evening
   of 2026-06-08** and **happens regularly** (recurring, not a one-off). The recurring
   signature points at the systematic H2 dwell cause; the `force_on` fix (#289) proceeds
   now and the telemetry discriminator is confirmation, not a blocker.
2. **R2 supersede tier:** which fog gates may a manual humid press beat (07–17
   window, RH≤90, 10 °F dew margin, fog-closes-vent) vs. keep (leak, SAF-5
   fert-master, SAF-4 volume cap)? And must fan-boost + fog-boost work
   simultaneously? (As built they are mutually exclusive.)
3. **R3 winter mode:** should vent-bypass also suppress the heat/fan interlock so the
   heater can run while fans pull house air?
4. **"Forecasted" sunrise/sunset:** is astronomical (deterministic) sunset-anchoring
   sufficient, or do you want cloud-cover-adjusted effective dawn/dusk from the
   Open-Meteo hourlies?
5. **Dry cutoff intent:** re-anchor + soften the CYC-1 dusk cutoff (keep Vanda
   velamen dry-down), or actually remove it? Related: approve/reject the staged
   migration 160 (orchid VPD band realign, gated since 2026-06-07).
6. **PID stance:** the anti-PID decision is documented twice; memo says PID. Confirm
   whether "smooth control" = better rails (recommended, no architecture change) or
   revisit continuous control.
7. **Circuit mapping:** confirm main = center orchid benches, grow = west shelves;
   and what photoperiod James needs for the cannabis (it will interact with the
   lighting policy redesign).
8. **Probe re-homing:** where should the second south probe (SEN0600) go —
   cannabis pot? And confirm the "both probes in one cucumber pot" history vs. the
   repo's "unpotted" record, for re-baselining S1.
9. **West grow circuit:** OK to set `sw_gl_grow_auto_mode=0` now?
10. **OTA secret sealing** in k3s — tracked **backlog item #301** (turnkey artifacts in
    PR #309), **not a blocker** for the non-OTA control-optimization work (req B/C/D —
    dispatcher/registry/DB-policy). It is a prerequisite only for the firmware-OTA subset
    (button fix #289, dusk-cutoff firmware #292).

## Suggested routing (per CLAUDE.md agent scopes)

- **firmware:** R1–R4 precedence fix + debounce + vent-bypass mode + new invariants +
  (stretch) move manual layer into `greenhouse_logic.h`. One PR-scoped change, full
  artifact set; replay will trivially pass — unit tests + invariants are the evidence.
- **ingestor:** C2 dispatcher push (dusk/night/fog-window solar re-anchoring) + dawn
  anchor fix + saturation alert + drip-skip feedback loop + probe re-baseline tasks.
- **coordinator (schemas/migrations):** registry corrections (burst tunables,
  dusk/night rows + readbacks), per-circuit lighting crop policy migration, topology
  per-position probe mapping, OVERRIDE_EVENT_TYPES additions (bounce service
  restarts per rule 7).
- **genai:** planner prompt/context updates once new surfaces exist (soil context,
  per-circuit lighting, boost-window observability).
- **web:** Grafana panel for boost-window/button events once telemetry lands.
