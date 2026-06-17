# Firmware FSM + Relay-Transition Specification

**Scope:** the deterministic ESP32 controller — its state machine, the per-mode
relay map, the safety rails AI cannot override, the diurnal band, hysteresis and
dwell, the energy-waste guards, offline (72-hour) operation, and the green-band
compliance model. This is the **single authoritative spec** for what the firmware
does and why; `docs/CONTROL-ARCHITECTURE.md` is the 4-layer system overview and
`docs/firmware-control-contract.md` is the schema/tunable contract.

It is the canonical artifact for lane **L2 Firmware Core (#344)** and the firmware
half of **L3 Climate Control (#345)**. Section 11 maps every acceptance criterion
to where it is realized in code and which test pins it. Code is the source of
truth; if this doc and the code disagree, the code wins — update this doc.

Verified against `firmware/lib/greenhouse_logic.h`, `greenhouse_solar.h`,
`greenhouse_types.h`, `firmware/greenhouse/{globals,tunables,controls}.yaml`,
and `firmware/test/{invariants.h,test_greenhouse_logic.cpp}` (2026-06-17).

---

## 1. The control loop

- **Cadence: ~1 s** (`firmware/greenhouse/controls.yaml:22`, `interval: 1s` — "was 5 s").
- **`dt_ms`-based, tick-rate-invariant.** Every timer accrues *real elapsed
  milliseconds* via `sat_add(timer, dt_ms)` (saturating add, `greenhouse_types.h`),
  with `dt_ms` clamped to 5000 to absorb multi-second hiccups (`controls.yaml`).
  Behavior is therefore identical whether the loop ticks at 1 s or jitters — the
  cadence is an implementation detail, not a control constant. Older docs said
  "5-second loop"; the *behavior* never depended on the number.
- **Single relay path.** The control loop is the only actuator path. The two
  pure functions are `determine_mode()` (mutates `ControlState` — timers,
  `mode_prev`, `mist_stage`) and `resolve_equipment()` (pure; reads only). Same
  compiled C++ runs on the ESP32, the native unit tests, and the replay harness.

## 2. Layer boundary — what firmware owns (and does not)

Firmware owns exactly three control surfaces — **climate, lighting, irrigation** —
plus the safety floor. It is **crop-agnostic**: it knows sensors, relays,
thresholds, setpoints, and numeric bands; it does **not** know crops.

| Firmware OWNS | Firmware does NOT own |
|---|---|
| Real-time relay control (climate/lighting/irrigation) | The target band's *values* (DB `crop_band_anchors` → 4 NVS float anchors) |
| Safety rails + equipment protection (§5) | Crop identity / crop strategy (lives above firmware) |
| On-chip band reconstruction from anchors (§6) | Tactical *tunables* authorship (the planner/AI sets those, bounded — §5.2) |
| Offline-first 72 h operation (§9) | Plant-risk limits / emergency authoring by AI |

**Crop-agnostic boundary (L2 #344 AC5).** The only crop knowledge on-chip is a
band the device follows, expressed as `BandAnchors{sr, sm, ss, mid}` — four plain
floats (`greenhouse_solar.h:152-157`). There is **no** crop enum, struct, string,
or branch in the shipped firmware (`firmware/lib/*.h`, `firmware/greenhouse/*.yaml`).
Crop strategy is isolated above firmware: `band_defaults.yaml` (zone→crop map) →
DB `crop_band_anchors` → dispatcher anchor-sync → NVS anchors. The guard
`tests/test_firmware_crop_agnostic_guard.py` fails if any crop name appears in
non-comment firmware code/config (comments may explain the agronomy; code may not).

## 3. The 8-mode state machine

### 3.1 Modes (`greenhouse_types.h:17-26`, `MistStage` at :31)

Priority/precedence order = enum order. `IDLE == 7` is `static_assert`-pinned.

| # | Mode | One-line role | Vent | Fans | Heat | Wetting |
|---|------|---------------|------|------|------|---------|
| 0 | `SENSOR_FAULT` | implausible sensors → fail safe | — | — | — | — (ALL off) |
| 1 | `SAFETY_COOL` | `temp ≥ safety_max` | open | both | — | fog if dry |
| 2 | `SAFETY_HEAT` | `temp ≤ safety_min` | **closed** | lead only | both | — |
| 3 | `SEALED_MIST` | VPD too high → humidify | closed | off | stage if cold | misters/fog |
| 4 | `THERMAL_RELIEF` | sealed too long → purge | open | both | — | — |
| 5 | `VENTILATE` | too hot → cool | open | lead (+2nd if hot) | — | fog assist if dry |
| 6 | `DEHUM_VENT` | VPD too low → dump humidity | open | lead (+both if very wet) | — | — |
| 7 | `IDLE` | in band | closed | off | stage if cold | econ-rescue heat |

Mist sub-stages: `MIST_WATCH → MIST_S1 (targeted) → MIST_S2 (rotating) → MIST_FOG
(escalation)`. S2 is only reachable from S1 (no level-skipping; invariant #12).

### 3.2 Entry/exit — the decision ladder (`determine_mode`)

A strict preempt ladder runs before any climate arbitration
(`greenhouse_logic.h:683-691`):

1. **`!sensors_plausible(in)` → `SENSOR_FAULT`** (NaN/inf/out-of-range; all timers
   zeroed; reason `sensor_fault`).
2. **`temp_f ≥ safety_max` → `SAFETY_COOL`** (reason `safety_cool`).
3. **`temp_f ≤ safety_min` → `SAFETY_HEAT`** (reason `safety_heat`).
4. **Otherwise → climate candidate** chosen by **normalized delta-error
   arbitration**: temp error and VPD error are each divided by their band
   half-width, and the axis with the larger normalized error leads candidate
   ordering (`greenhouse_logic.h:667-691`). Candidates: heat / ventilate(+mist/fog
   assist) / sealed-humidify / sealed-fog / dehum-vent / idle.

After the climate pick, two **VPD safety rails** can still override:
`vpd_kpa > vpd_max_safe` forces `SEALED_MIST` (reason `dry_override`);
`vpd_kpa < vpd_min_safe` (in IDLE/SEALED, econ permitting) forces `DEHUM_VENT`
(reason `vpd_min_safe_rescue`). Then sealed entry/exit, the optional dwell gate
(§7.3), and mist-stage escalation resolve.

### 3.3 Per-mode relay map (`resolve_equipment`, `greenhouse_logic.h:2006-2122`)

`resolve_equipment` is pure: `(mode, inputs, setpoints, state, lead_is_fan1) →
RelayOutputs{vent, fan1, fan2, fog, heat1, heat2}`. Misters are driven from
`controls.yaml` off `mist_stage`. Exact outputs:

| Mode | vent | fan1 / fan2 | fog | heat1 / heat2 |
|------|------|-------------|-----|---------------|
| `SENSOR_FAULT` | off | off / off | off | off / off |
| `SAFETY_COOL` | **on** | on / on | on iff `fog_assist_permitted ∧ vpd>vpd_high_eff` | off / off |
| `SAFETY_HEAT` | **off** (retain heat) | lead on / other off | off | **on / on** |
| `SEALED_MIST` | off | off / off | on iff `mist_stage==MIST_FOG ∧ permitted` | s1 iff cold; s1+s2 iff `heat2_latched` |
| `THERMAL_RELIEF` | **on** | on / on | off | off / off |
| `VENTILATE` | **on** | lead on / 2nd on iff `temp>Thigh+stage2_delta` | on iff `vpd>vpd_high_eff+fog_escalation_kpa ∧ permitted` | off / off |
| `DEHUM_VENT` | **on** | lead on / both iff `vpd<vpd_low_eff−dehum_aggressive_kpa` | off | off / off |
| `IDLE` | off | off / off | off | s1 iff cold; s1+s2 iff `heat2_latched`; econ-rescue s1 (§8.1) |

**Safety/override overlays (applied after the per-mode map):**
- **`SENSOR_FAULT` → all relays off.** No actuator runs without sensor feedback;
  freeze protection is an **out-of-band hardware thermostat wired in parallel**,
  never blind software (`greenhouse_logic.h:17-18, 2007-2010`).
- **`SAFETY_HEAT` runs the lead fan with the vent CLOSED** for canopy circulation
  without dumping heat — a deliberate "fan without vent" the invariant whitelists
  (`:2019-2028`).
- **Occupancy air-block** (`air_blocked_by_occupancy`, `:2115-2119`): when occupied
  and inhibit is set, forces `fan1/fan2/fog` off — **except** `SAFETY_COOL`,
  `SAFETY_HEAT`, `SENSOR_FAULT`, which are exempt (people-comfort never overrides
  the temperature/fault rails).

### 3.4 Auditability — `mode_reason`

Every transition stamps a `mode_reason` string so any relay change is traceable
(invariant #10 fails an unattributed relay toggle). Vocabulary includes
`sensor_fault, safety_cool, safety_heat, heat_stage1/2, idle, summer_vent,
temp_high, vent_mist_assist, vent_fog_assist, humidify_enter/continue/resolved,
fog_enter/continue, dehum_continue, vpd_low/too_low, dry_override,
vpd_min_safe_rescue, mist_backoff, moisture_blocked, seal_enter/continue/exit,
relief_cycle_breaker, thermal_relief(_forced), dwell_hold` (`greenhouse_logic.h`;
the full set is enumerated in `firmware/test/invariants.h` check #10).

## 4. Manual button-override layer (FANS / HUMID / VENT-BYPASS)

Three momentary panel buttons (PCF8574 `pcf_in_2`) each arm a deadline latch
(`manual_fans_until_ms` / `manual_fog_until_ms` / `vent_bypass_until_ms`, set in
`hardware.yaml`). Each cycle, `controls.yaml` builds a `ManualOverrides` from the
effective latch state + whether a real fog safety block is up, and applies the
override through the **pure, unit-tested `apply_manual_overrides()`**
(`greenhouse_logic.h`) on top of the resolved relay table:

| Button | Effect | Forced (bypasses min-off dwell) |
|---|---|---|
| **FANS** | both fans ON, vent OPEN | fans, vent |
| **VENT-BYPASS** | both fans ON, vent **CLOSED** (winter house-air pull); *implies fans* | fans |
| **HUMID** | fogger ON, unless a genuine fog safety block (dew/RH/temp/time/leak) | fog |

**Precedence: absolute safety wins.** `apply_manual_overrides()` is a no-op in
`SENSOR_FAULT` / `SAFETY_COOL` / `SAFETY_HEAT` — a press never fights a safety rail
(a FANS press cannot throw the vent open and dump heat during a cold-rail
SAFETY_HEAT). Below the rails it supersedes all climate automation; only a
re-press, the deadline timeout, or a safety rail interrupts it.

The returned `ManualForce` flags feed `set_relay`'s `force_on=` so a press engages
within one loop tick — **the #289 fix** (the force flag was previously computed but
never wired to the fan relays, so a FANS press stuck behind the fan min-off dwell
while the vent moved — the "pressed fans, nothing happened" signature).
`fan_requires_open_vent()` carves out VENT-BYPASS (and SAFETY_HEAT) from the
fan→vent interlock so bypass can actually hold the vent shut — **the #290 fix**.

**Pinned by** 8 native tests (`test_greenhouse_logic.cpp`): per-button relay
outcomes, the safety-rail no-op, the fog-safety block, and the interlock carve-out.
The replay corpus never presses a button, so a replay-diff is **0** (automatic
control is unchanged) — these native tests are the required positive evidence.
**Live status:** OTA-deployed 2026-06-17 (firmware `2026.6.17.0134.6a3b35a`);
post-deploy sensor-health sweep 27/0/0. Closes #289, #290.

## 5. Safety rails — the AI cannot override them (L2 #344 AC4)

### 5.1 The hard rails

| Rail | Condition / default | Effect | Pinned by |
|---|---|---|---|
| Sensor-fault | `!sensors_plausible` (temp∉(−20,140)°F, rh∉[0,100], vpd∉[0,10), hour∉[0,23]) | all relays off | inv #26, native tests |
| Cold rail | `temp_f ≤ safety_min` (default 45 °F) | `SAFETY_HEAT` (both heaters) | inv #25 |
| Hot rail | `temp_f ≥ safety_max` (default 95 °F) | `SAFETY_COOL` (vent+both fans) | inv #7 |
| VPD over-dry | `vpd_kpa > vpd_max_safe` (default 2.5 kPa) | forced `SEALED_MIST` | determine_mode |
| VPD over-wet | `vpd_kpa < vpd_min_safe` (default 0.3 kPa) | forced `DEHUM_VENT` | determine_mode |
| Dew-margin veto | every wetting path gated on `temp_f − dew_point_f` | no crown condensation | inv #2/#24 |
| Relay protection | min-on/min-off (heat 300/300 s, fan 60/30, fog 60/60, vent 30/30), heat2 anti-chatter latch | wear/chatter limit | `controls.yaml` |
| Daily water ceiling | SAF-4 per-zone duty cap | immune to VPD emergencies | inv #21/#22 |

The rails preempt **before** any AI-influenced candidate is considered
(`greenhouse_logic.h:683-691`). The cold rail and the all-off fault were previously
unpinned by the always-run replay suite; they are now invariants **#25** and **#26**
(`firmware/test/invariants.h`).

### 5.2 Why AI tunables cannot move a rail or the FSM (the 5-layer defense)

The planner sets *tunables* (how hard to chase the band), never the rails or the
state machine. Five independent layers enforce this:

1. **Registry** — safety/FSM keys are `planner_pushable=False`
   (`verdify_schemas/tunable_registry.py`); the planner has no tool to author them.
2. **MCP gate** — the tool surface refuses non-pushable keys (`mcp/server.py`).
3. **Registry bounds** — every pushable tunable has a validated min/max.
4. **ESPHome number min/max** — the device clamps each accepted value
   (`firmware/greenhouse/tunables.yaml`).
5. **On-device sanity** — `validate_setpoints()` re-clamps relationally
   (`greenhouse_types.h`), and `sw_fsm_controller_enabled` is force-locked on.

The rails read fixed `safety_min/safety_max/vpd_*_safe` setpoints that are not in
the planner-pushable set. Tested by `verdify_schemas/tests/test_tunable_registry.py`
and the native suite.

### 5.3 Enforcement — the invariant suite

`make firmware-invariants` runs property invariants #1–#26 over the replay corpus
(193,525 rows); first breach fails. They are the executable form of this spec:
fog/vent and fog/heat exclusivity (#1/#11), heat-off-when-hot (#3), the hot/cold/
fault rails (#7/#25/#26), mode↔relay consistency (#15), heat2-requires-heat1 (#16),
the ≥10 °F day/night drop (#17), the fertilizer/feed interlocks (#18–#22), and the
curve-only fog gate (#24). `make test-firmware` adds 222 native unit tests of the
same code.

## 6. The diurnal band curve — the deterministic target (L3 #345 AC1)

The served band is a **pure function of solar phase**, reconstructed on-chip from
4 NVS anchors — it stretches/compresses automatically as day length drifts.

- **Solar phase** ∈ [0,4): 0=sunrise, 1=solar-noon, 2=sunset, 3=solar-midnight,
  computed by a **C1-smooth piecewise-cubic-Hermite** over the on-chip NOAA
  ephemeris (`greenhouse_solar.h:91-130`). C1 continuity kills the slope kink the
  old piecewise-linear phase put at sunrise/sunset.
- **Band value** = a **4-anchor harmonic (discrete-Fourier) interpolation** through
  `{sr, sm, ss, mid}` at phase 0/1/2/3 (`greenhouse_solar.h:159-175`): passes
  *exactly* through each anchor yet is C-∞ smooth (no lumpy plateaus). One curve,
  not four stitched eases.
- **Cross-implementation alignment.** The identical math lives in three places kept
  in lockstep: firmware `band_value_at_phase`, ingestor `solar.py`, and DB
  `fn_crop_band_value` (migration 170). Pinned by absolute goldens in
  `firmware/test/test_greenhouse_logic.cpp` (`band_value_at_phase_matches_canonical_harmonic_goldens`,
  `solar_phase_c1_smooth_and_hits_anchors`) and mirrored by
  `tests/test_solar_band_anchors.py`. `make firmware-replay-band` is the behavioral
  gate for any curve change (the stock replay is corpus-fed and would miss it).

## 7. Bands + hysteresis (L3 #345 AC2)

### 7.1 Day/night/season bands

The band's day/night shape *is* the four anchors (sunrise/noon/sunset/midnight)
interpolated by solar phase (§6). Season enters through the anchor *values* the
dispatcher syncs (DB `crop_band_anchors`, season-aware) — firmware interpolates
whatever anchors are in NVS. The ≥10 °F day/night temperature drop is a hard
property of the served band, pinned by invariant **#17** (night `temp_low` must sit
≥10 °F below the day-peak `temp_high`).

### 7.2 Hysteresis constants (whipsaw reduction)

Defaults from `default_setpoints()` (`greenhouse_types.h`) / `globals.yaml`:

| Constant | Default | Gates | Exit-widening rule |
|---|---|---|---|
| `temp_hysteresis` | 1.5 °F | VENTILATE entry/exit band | exit threshold widened when `was_cooling` (`mode_prev`) |
| `cool_exit_hysteresis_f` | 1.5 °F | VENTILATE exit | widened to ≥3.0 °F when outdoor is cold-for-vent |
| `heat_hysteresis` | 1.0 °F | IDLE/sealed heat-stage 1 entry; heat2-latch clear | clears the heat2 latch at `Tlow + heat_hysteresis` |
| `vpd_hysteresis` | 0.3 kPa | SEALED exit / DEHUM entry | structurally capped at 33 % of band width (no entry/exit inversion) |
| `dH2` | 5.0 °F | heat-stage-2 latch entry (`temp < Tlow − dH2`) | latched; clears via `heat_hysteresis` |
| `cool_stage2_over_high_f` | 1.0 °F | VENTILATE 2nd-fan escalation (band-first) | threshold only |
| `fog_escalation_kpa` | 0.4 kPa | MIST_FOG entry; VENTILATE fog assist | threshold only |
| `dehum_aggressive_kpa` | 0.3 kPa | DEHUM both-fans entry | threshold only |
| `bias_heat` / `bias_cool` | 0.0 °F | symmetric target offset (planner-tunable) | no hysteresis |
| `band_track_fraction` | **0.50** | pinches the CONTROL band toward `temp_target`/`vpd_target` (BC-3) | per-axis width floor (see §7.4) |

### 7.4 Band-tracking pinch — strive toward target, do not float (BC-3, ADR0003 §6.1)

`climate_band_error()` (`greenhouse_logic.h`) is a **do-nothing envelope**: it returns
`0.0` for any value inside `[low, high]`. By itself that means the controller *floats*
anywhere in the band. **`apply_band_track_pinch()`** closes that gap: at the controller
entry (`determine_mode`) and in `resolve_equipment` it moves the **control** band toward
the served target by `band_track_fraction` — `low += f·(target−low)`, `high −= f·(high−target)`
— so the pinched width is `served_width·(1−f)` and every non-safety axis is driven *onto*
the target curve, not merely kept inside the band. The **served band and the safety rails
(`safety_min/max`, `vpd_*_safe`) are untouched** — the wide band stays the safety bound.

- **Default 0.50, and it is the DEFAULT (float is retired).** The authoritative device
  value is set in the `controls.yaml` setpts initializer (`.band_track_fraction =
  id(band_track_fraction)`, global default `0.50`); `default_setpoints()` matches so the
  native tests and replay exercise the real behavior. It is a **bounded planner knob**
  ([0,1], registry `planner_pushable`); the planner may modulate tracking tightness but
  the float-envelope (0) is no longer the operating default.
- **Demand-overlap width floor.** Because `band_heat_target_f = low + 0.25·max(2,W)` and
  heat-stage-1 fires at `+ heat_hysteresis`, a too-narrow pinched band could make the heat
  trigger meet the cooling edge (heat+cool demanded at one temp → thrash). The pinch caps
  the per-axis effective fraction so the pinched temp width never drops below
  `heat_hysteresis/0.75` (≥2) or `0.5+heat_hysteresis` — provably keeping
  `band_heat_target_f(pinched)+heat_hysteresis ≤ pinched.temp_high` (pinned by
  `bc3_pinch_never_inverts_or_overlaps_demand`).
- **Proving a change.** The stock corpus has no per-row target column, so the stock replay
  and `make firmware-invariants` re-sim **un-pinched** (they record historical float
  operation); the pinch is exercised and proven by **`make firmware-replay-band`** (which
  derives a real target curve) plus the native `bc3_*` tests. Arming 0.50 diverges ~43 % of
  band-derive replay rows from float — the behavioral proof the stock corpus is blind to.

Hysteresis behavior is tested directly (`test_greenhouse_logic.cpp` fix3 temp/heat,
fix4 dehum-sticky) and bounded so a planner push cannot invert an entry/exit pair.

### 7.3 Dwell / timer mechanics

| Timer / gate | Default | Effect |
|---|---|---|
| `vpd_watch_dwell_ms` | 60 s | VPD must stay high this long before humidify fires |
| `sealed_max_ms` | 10 min | SEALED_MIST hard timeout → IDLE + mist-backoff (inv #4) |
| `relief_duration_ms` | 90 s | THERMAL_RELIEF purge burst (inv #8; ≤ `sealed_max_ms`) |
| `mist_backoff_ms` | 10 min | lockout suppressing new SEALED entries after a timeout (livelock breaker) |
| `mist_s2_delay_ms` | 5 min | dwell before MIST_S1→S2 (inv #12 no skip) |
| Phase-2 **dwell gate** | **default OFF** (`sw_dwell_gate_enabled=false`); 5 min | when on, holds non-safety transitions to cut whipsaw (compliance can preempt) |
| `vent_latch_timeout_ms` | 30 min | relief-cycle breaker; retries seal entry |
| heat2 anti-chatter latch | — | prevents gas-valve chatter; set `temp<Tlow−dH2`, clear `temp≥Tlow+heat_hysteresis` |

Mode-transition rate is additionally bounded by invariant #6 (≤30 transitions/hr in
stable conditions).

## 8. Mechanical transitions, energy-waste avoidance, outdoor air (L3 #345 AC3/AC4)

### 8.1 No contradictory actuator combinations by default

Modes are mutually exclusive, so most "dumb" combinations are structurally
impossible. The few cross-mode overlaps are explicitly bounded and pinned:
- **No heat while venting / no fog while venting** except an explicit, demand-justified
  assist envelope (`fog+vent` only as FW-9b high-VPD assist or SAFETY_COOL emergency
  — invariant #1; `fog+heat` only inside the SEALED cold/dry assist envelope —
  invariant #11). Outside those envelopes the combination fails the replay gate.
- **Never heat to chase humidity at night** (`night_econ_heat_suppressed`,
  `greenhouse_logic.h:119-124`): the IDLE econ-rescue heat path — the only
  heat-to-dry path — is suppressed overnight so it cannot erase the day/night drop.
- **Heat off when hot** (#3), **heat2 never without heat1** (#16).

### 8.2 Outdoor-air-aware strategy

The controller uses Tempest outdoor temperature/RH (when fresh) before spending
energy: the **summer-vent economizer gate** opens for free cooling only when outdoor
air is genuinely **cooler and drier** than indoor, with a staleness guard so stale
outdoor data can never trigger it (`greenhouse_logic.h:583-598`, invariant #9). A
`DEHUM_VENT` economizer path and a cold-outdoor vent-cooling-exit widening complete
the outdoor-aware set. Tested at `test_greenhouse_logic.cpp` (s15 gate).

### 8.3 Equipment roles

Fan **lead/lag rotation** balances runtime across fan1/fan2 (verified ~5 % balance
over 14 d). Fogger and **center** misters are the core humidity tools; **west/south**
misters are minimized for climate (zone-priority arbiter, `greenhouse_solar.h:236`).
Vent is coupled to fan behavior per mode (§3.3).

## 9. 72-hour disconnected / offline-first operation (L2 #344 AC3)

The controller is designed to run **safely and deterministically for ≥72 hours with
zero network**, because all control inputs it needs are on-chip:

- **On-chip ephemeris** — sunrise/solar-noon/sunset computed locally from the NOAA
  approximation each cycle (`greenhouse_solar.h:37-84`); the diurnal program never
  depends on the dispatcher.
- **NVS-persisted band anchors** — the served band is reconstructed from 4 floats in
  NVS (`globals.yaml` `restore_value` anchors); a dispatcher anchor-sync is an
  *update*, not a control-loop dependency.
- **Graceful clock fallback** — if there is no time source at all,
  `fallback_solar_phase(local_hour)` supplies a fixed-day phase so the controller
  degrades rather than mis-gates (`greenhouse_solar.h:145-148`,
  `effective_solar_phase` at `greenhouse_logic.h:42-48`).

**What degrades over a long disconnect:** no live anchor reconciliation (the device
holds its last NVS anchors), no telemetry capture or gap-backfill until reconnect,
and — only if the RTC/SNTP clock is *also* lost — the band rides the fixed fallback
day instead of the true ephemeris. None of these defeats safety: the rails (§5) and
the band reconstruction run entirely on-chip.

**Tested** (`firmware/test/test_greenhouse_logic.cpp`):
`disconnected_72h_run_is_deterministic_and_safe` (3 days × 96 samples, on-chip
ephemeris + NVS anchors only; two boots produce an identical mode trace and never
trip a safety/fault rail while in band), `no_time_source_fallback_phase_stays_safe`
(NaN phase → fallback across all 24 h stays finite and safe), and
`reboot_persisted_anchors_reproduce_identical_band` (a power cycle reproduces
byte-identical setpoints).

## 10. Green-band compliance — controller-miss vs physical-impossibility (L3 #345 AC5)

Compliance is **off the live control path** (the dispatcher never reads it) and is
**graded + feasibility-aware**, not binary:
- **Graded credit** — `fn_grade_credit` gives full credit inside the ideal band,
  linear partial credit out to the stress edge, zero beyond
  (`db/migrations/146-compliance-rearchitecture.sql`).
- **Feasibility decomposition** — every miss is classified **controller** (corrective
  authority was available and unused) vs **unachievable** (e.g. an exhaust-only box
  cannot cool below ambient with the vent saturated and both fans on) vs
  `feasibility_unknown` (before relay coverage). The rule is `fn_zone_band_grade`'s
  CASE (`146:259-278`); both a raw and a controller-attributable compliance are
  emitted, so the planner reward is never penalized for the weather (verified: 73.4 %
  of historical hot-misses were physically unachievable).

The decomposition rule is pinned offline by
`tests/test_compliance_feasibility_classifier.py` (a faithful Python mirror over
synthetic rows + a guard that migration 146 still carries the predicates). The live
SQL evaluation over production data is DB-gated.

### 10.1 Cross-surface seams (the deliberate couplings)

Two seams cross the otherwise-clean climate/lighting/irrigation separation and are
named here so they are not mistaken for leaks:
- **Lighting** — the firmware's on-chip `evaluate_lighting` owns the grow-light
  decision; `scripts/setpoint-server.py` is the HA-token-holding **proxy** that
  actuates the Lutron switches on the firmware's behalf (it is *not* an independent
  controller). Both key off the same `gl_main_*`/`gl_grow_*` tunables, so they cannot
  diverge in thresholds. (Hardening this with an explicit auto-vs-manual write test
  is a tracked follow-up.)
- **Irrigation↔climate** — the `feed_hold` global + shared center misters + the
  salt-to-fogger interlock couple fertigation and climate wetting deliberately
  (invariants #18–#22 enforce that fertilizer never reaches the fogger and that no
  clean wetting fires during the post-feed absorption hold).

## 11. Acceptance-criteria traceability

**L2 Firmware Core (#344)**

| AC | Where realized | Pinned by |
|---|---|---|
| Responsibilities = climate/lighting/irrigation only | §2, §3 | this spec + `test_firmware_crop_agnostic_guard.py` |
| Relay transitions + safety override explicit | §3.2, §3.3, §5.1 | inv #1/#3/#7/#11/#15/#16/#25/#26 |
| 72-hour disconnected defined AND tested | §9 | `disconnected_72h_*`, `no_time_source_*`, `reboot_persisted_*` |
| AI tunables cannot override rails or FSM | §5.2 | `test_tunable_registry.py` + 5-layer defense |
| Crop-specific assumptions removed/isolated | §2 | `test_firmware_crop_agnostic_guard.py` |

**L3 Climate Control (#345)**

| AC | Where realized | Pinned by |
|---|---|---|
| Diurnal curve math formalized + tested | §6 | harmonic/C1 goldens + `make firmware-replay-band` |
| Bands + hysteresis documented | §7 | fix3/fix4 hysteresis tests + inv #17 |
| Mechanical transitions avoid energy-waste by default | §8.1 | inv #1/#3/#11/#16 |
| Outdoor-air use explicit | §8.2 | inv #9 + s15 gate test |
| Compliance distinguishes controller-miss vs impossibility | §10 | `test_compliance_feasibility_classifier.py` (live eval DB-gated) |

## 12. Changing the firmware — the gate

Any change under `firmware/lib/**`, `firmware/greenhouse/**`, `verdify_schemas/**`,
`ingestor/entity_map.py`, or `mcp/server.py` follows the verification order in
`AGENTS.md`: `make test-firmware`, `make firmware-invariants`, the replay diff
(`make firmware-replay`, plus `make firmware-replay-band` for any band-curve change),
and `make firmware-check`. The **OTA itself is Jason-gated** (no OTA on an open
critical alert; ≤1 OTA/week; 48-hour bake). This spec's invariants + native tests +
replay are the offline proving ground that gates the OTA.
