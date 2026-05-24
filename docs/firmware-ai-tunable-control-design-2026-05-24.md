# Firmware AI-Tunable Control Design - 2026-05-24

## Purpose

This note captures the firmware and planner changes that would make Verdify less
rigid and give Iris more useful control authority while preserving deterministic
safety rails. It is based on the current firmware implementation, the live
planner/tunable contract, and historical data through 2026-05-24.

The goal is not to let AI bypass safety. The goal is to make firmware a bounded
policy executor: deterministic code owns physical safety and actuator
interlocks, while AI owns the tactical tradeoffs that determine band compliance.

## Executive Conclusion

The largest missed opportunity on 2026-05-23 was not setpoint delivery. The
firmware/readback band matched the firmware-enforced band. The controller made
the decisions it is coded to make:

- prioritize temperature over VPD;
- ventilate while above `temp_high`;
- use one fan until derived stage-2 criteria are met;
- let VPD recovery run only through the available moisture path;
- block fog after the fixed fog window and block direct wetting during drydown.

That produced a safe but under-controlled evening recovery. From 20:00-22:00
MDT, VPD was high while all normal moisture time gates were closed. Firmware
continued to request `vent_mist_assist`, but fog was outside its time window and
all direct-wet mister zones were gated by drydown policy.

The highest-value changes are:

1. Add real AI control over cooling aggression: both-fan threshold and cooling
   exit hysteresis.
2. Add bounded VPD-over-cooling priority when temperature is no longer above the
   high edge by a meaningful margin.
3. Add stress overrides for direct-wet and fog time gates when dew margin and
   leaf wetness are safe.
4. Add feed-forward solar/load cooling before the high edge is breached.
5. Add telemetry for "why moisture/fans did not run" so Iris can tune the right
   knob instead of guessing.

## Evidence Summary

Authoritative band compliance was computed with:

```sql
fn_band_trace(start_ts, end_ts, 'vallery')
```

This matters because raw `setpoint_changes` can show dispatcher/crop rows that
do not line up with the trace views used by public scorecards and diagnostics.

Worst recent days by firmware-band compliance:

| Day | Both ok % | Temp ok % | VPD ok % | Hot h | Cold h | VPD-high h | VPD-low h | Hot and VPD-high h |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2026-05-11 | 24.5 | 49.8 | 33.2 | 10.92 | 1.13 | 11.45 | 4.58 | 9.33 |
| 2026-05-10 | 40.2 | 65.7 | 44.8 | 7.32 | 0.92 | 8.25 | 5.00 | 6.38 |
| 2026-05-14 | 43.7 | 52.5 | 54.5 | 11.37 | 0.00 | 10.90 | 0.00 | 8.78 |
| 2026-05-13 | 45.3 | 47.4 | 66.5 | 11.95 | 0.63 | 8.00 | 0.02 | 7.52 |
| 2026-05-12 | 48.3 | 63.5 | 58.1 | 8.02 | 0.73 | 7.93 | 2.12 | 5.93 |
| 2026-05-23 | 54.5 | 58.6 | 71.7 | 8.85 | 0.75 | 6.35 | 0.20 | 5.57 |

Hot-time fan utilization:

| Day | Hot h | Both-fan hot h | One-fan hot h | No-fan hot h | Vent hot h | Avg hot excess F |
|---|---:|---:|---:|---:|---:|---:|
| 2026-05-13 | 11.95 | 9.13 | 2.77 | 0.05 | 11.95 | 2.15 |
| 2026-05-14 | 11.37 | 7.07 | 4.13 | 0.17 | 11.30 | 2.25 |
| 2026-05-11 | 10.92 | 7.13 | 3.05 | 0.73 | 10.20 | 3.13 |
| 2026-05-16 | 9.12 | 5.08 | 3.10 | 0.93 | 8.20 | 1.71 |
| 2026-05-23 | 8.85 | 4.05 | 3.68 | 1.12 | 8.28 | 1.36 |

VPD-high moisture utilization, using actual zone misters plus fog:

| Day | VPD-high h | With hot h | Zone-mister h | Fog h | Any moisture h | Vent h | Avg VPD excess kPa |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2026-05-11 | 11.45 | 9.33 | 3.12 | 4.25 | 6.38 | 10.75 | 0.25 |
| 2026-05-14 | 10.90 | 8.78 | 2.95 | 6.63 | 7.85 | 10.83 | 0.15 |
| 2026-05-16 | 8.63 | 7.42 | 2.73 | 2.10 | 3.20 | 7.72 | 0.18 |
| 2026-05-10 | 8.25 | 6.38 | 0.03 | 4.02 | 4.05 | 8.08 | 0.33 |
| 2026-05-13 | 8.00 | 7.52 | 4.45 | 4.73 | 5.53 | 7.95 | 0.11 |
| 2026-05-12 | 7.93 | 5.93 | 3.85 | 2.25 | 4.47 | 7.40 | 0.11 |
| 2026-05-23 | 6.35 | 5.57 | 2.17 | 1.97 | 2.67 | 6.15 | 0.11 |

On 2026-05-23 18:00-23:00:

| Hour | VPD-high h | VPD-high with any mister gate open h | VPD-high with fog time open h | VPD-high with no moisture time gate h |
|---|---:|---:|---:|---:|
| 18:00 | 0.40 | 0.40 | 0.00 | 0.00 |
| 19:00 | 0.27 | 0.27 | 0.00 | 0.00 |
| 20:00 | 0.93 | 0.00 | 0.00 | 0.93 |
| 21:00 | 0.82 | 0.00 | 0.00 | 0.82 |
| 22:00 | 0.65 | 0.00 | 0.00 | 0.65 |

Direct interpretation: after 20:00, the system still had VPD-high demand, but
normal mist/fog eligibility was closed by time gates. This is exactly the kind
of situation AI should be able to correct with bounded tunables.

## 30-Day Opportunity Model

The May 23 pattern is not isolated. I queried `fn_band_trace` plus
`equipment_state` over 2026-04-24 through 2026-05-24 10:45 MDT. These are
opportunity measurements, not guarantees: they identify periods where a missing
knob would have had authority to change actuator posture.

Summary across 730.7 analyzed hours:

| Opportunity | Hours | Interpretation |
|---|---:|---|
| Temp above firmware high band | 126.8 | Thermal compliance is the largest repeated physical miss. |
| Hot with one fan only | 52.6 | A both-fans-near-high knob would have had many chances to act. |
| Hot with no fan | 18.8 | Some hot time is transition/dwell/occupancy/interlock visibility, not capacity. |
| VPD above firmware high band | 165.8 | Dry-side VPD is the most persistent biological stress axis. |
| VPD-high with no fog or zone mister running | 103.9 | The planner often needs moisture-path availability, not just lower VPD thresholds. |
| Evening VPD-high/no-moisture with dew margin >= 8 F and leaf wetness 0 | 30.9 | Bounded evening wetting was usually biologically plausible. |
| Solar >= 750 W/m2, temp within 2 F below high, hot breach within 2h | 22.9 | Preventive cooling/feed-forward had repeated lead time. |
| VPD-high, temp near high, venting, no moisture | 14.6 | VPD-preempts-cooling-hold would have had repeated near-edge recovery windows. |

Top daily examples from the 30-day slice:

| Day | Hot h | Hot one-fan h | Hot no-fan h | VPD-high h | VPD-high no-moisture h | Solar pre-hot opportunity h |
|---|---:|---:|---:|---:|---:|---:|
| 2026-05-13 | 11.58 | 2.37 | 0.05 | 10.36 | 5.28 | 1.34 |
| 2026-05-14 | 11.40 | 4.28 | 0.10 | 11.78 | 4.74 | 0.72 |
| 2026-05-11 | 9.97 | 2.74 | 0.21 | 10.35 | 4.19 | 1.79 |
| 2026-05-16 | 9.29 | 3.25 | 1.01 | 9.80 | 7.11 | 0.62 |
| 2026-05-23 | 8.92 | 4.17 | 0.69 | 7.56 | 5.33 | 1.14 |
| 2026-05-10 | 6.56 | 3.79 | 0.15 | 7.92 | 4.01 | 1.30 |
| 2026-05-15 | 6.25 | 3.88 | 0.13 | 5.64 | 2.87 | 1.69 |
| 2026-05-08 | 6.13 | 3.86 | 0.02 | 6.65 | 2.47 | 1.75 |

Slope analysis is directionally useful but confounded by firmware staging:
both fans are usually engaged during the hardest conditions, so raw slope is not
a causal estimate. Still, the pattern is useful:

- During hot high-solar samples (`solar >= 750 W/m2`), even both fans often only
  slowed the rise rather than reversing it. That argues for feed-forward cooling
  before the high edge, not just more cooling after breach.
- During VPD-high venting without moisture, median VPD slope was flat-to-rising;
  with misters/fog, median VPD slope was generally falling. That argues for
  making moisture availability first-class during dry ventilation.

## Deterministic Logic To Keep

These are safety or hard physical constraints and should remain deterministic:

- Sensor plausibility -> `SENSOR_FAULT`, all relays off.
- `SAFETY_COOL` and `SAFETY_HEAT` preemption.
- Heat/vent air-exchange interlock.
- Heat2 requiring heat1.
- Relay min-on/min-off protection.
- Stale outdoor-data rejection for outdoor-air decisions.
- Fog RH and temperature safety gates.
- Irrigation conflict lockout and water-budget emergency behavior.
- Maximum sealed residence with a required escape path.

AI may tune bounded thresholds around these rails, but should not override the
rails themselves.

## Deterministic Logic To Relax Into AI Tunables

### 1. Cooling Stage-2 Threshold

Current behavior:

- `d_cool_stage_2` is planner-writable, but the active FSM transforms it through
  `min(dC2, max(1.0F, 25% of temp band))`.
- In `VENTILATE`, one lead fan runs first; both fans run only above the derived
  stage-2 threshold.

Problem:

- On bad days, there are 2.7-4.1 hot hours with only one fan.
- On 2026-05-23 there were 3.68 hot hours with one fan and 1.12 hot hours with
  no fan.

Proposed tunables:

| Tunable | Range | Default | Meaning |
|---|---:|---:|---|
| `cool_stage2_over_high_f` | 0.0-3.0 F | 1.0 | Fan2 engages this far above `temp_high`. |
| `cool_all_fans_at_high_enabled` | bool | off | If on, both fans run as soon as temp is above high edge during AI-declared stress windows. |
| `cool_exit_hysteresis_f` | 0.3-3.0 F | current temp hysteresis | Cooling clears at `temp_high - cool_exit_hysteresis_f`. |
| `cold_vent_guard_delta_f` | 0-10 F | current hardcoded 10 F behavior | Extra guard when outdoor air is cold enough to slug the house. |

Expected outcome:

- Faster high-edge recovery on hot/dry shoulders.
- Less reliance on indirect `d_cool_stage_2` semantics.
- Better replay explainability: "AI requested both fans near high edge" instead
  of "firmware derived a hidden stage-2 margin."

### 2. VPD Priority During Cooling Hold

Current behavior:

- Normal priority is temp first, then VPD.
- Once cooling starts, `needs_cooling` remains true until temp falls below
  `temp_high - temp_hysteresis`.
- `SEALED_MIST` cannot enter while cooling demand is active.

Problem:

- After temp has fallen near or below the high edge, this can keep the house in
  ventilation longer than VPD recovery needs.
- On 2026-05-23, VPD-high persisted into the 22:00 hour while temp was nearly
  recovered.

Proposed tunables:

| Tunable | Range | Default | Meaning |
|---|---:|---:|---|
| `vpd_preempts_cooling_hold_enabled` | bool | off | Allows VPD recovery to override cooling hysteresis once temp is safe. |
| `vpd_preempt_max_temp_over_high_f` | 0.0-3.0 F | 0.5 | Max temp above high where VPD may preempt cooling hold. |
| `vpd_preempt_min_dew_margin_f` | 3-15 F | 7 | Dew margin required before humidifying during recovery. |
| `vpd_preempt_leaf_wetness_max` | sensor units | conservative | Blocks preempt if leaves are wet. |

Expected outcome:

- Cuts the late tail where VPD stays high after temperature recovery.
- Keeps safety deterministic by refusing preempt near `safety_max` or poor dew
  margin.

### 3. Vent-Mist Assist As A First-Class Control Path

Current behavior:

- Firmware can request `vent_mist_assist_active` while in `VENTILATE`.
- Physical misters still depend on direct-wet windows, zone demand, irrigation,
  occupancy, water budget, and vent interlocks.
- The planner can see some state, but not a complete "why moisture did not run"
  explanation.

Problem:

- On 2026-05-23, `vent_mist_assist` appeared in overrides while VPD was high,
  but no moisture could run after 20:00 because time gates were closed.

Proposed diagnostics:

| Diagnostic | Purpose |
|---|---|
| `moisture_block_reason` | CSV/enum: direct_wet_window, direct_wet_temp, occupancy, irrigation, budget, vent_interlock, fog_window, fog_rh, fog_temp. |
| `vent_mist_assist_status` | AI-readable status: inactive, served, or blocked with the moisture block reason. |
| `direct_wet_zone_mask` | Bitmask of south/west/center wet eligibility. |
| `fog_permitted_reason` | Reason fog was permitted or blocked. |

Proposed tunables:

| Tunable | Range | Default | Meaning |
|---|---:|---:|---|
| `vent_mist_assist_vpd_margin_kpa` | 0.0-0.5 | 0.0 | Extra VPD margin above high before assist requests water. |
| `vent_mist_assist_min_dew_margin_f` | 3-15 | 7 | Dew margin required for water while venting. |
| `vent_mist_assist_max_temp_f` | 70-95 | safety_max - margin | Blocks water assist near dangerous heat. |

Expected outcome:

- Iris can distinguish "wrong threshold" from "right threshold, blocked path."
- Planner can tune direct-wet/fog windows rather than over-tightening VPD knobs.

### 4. Direct-Wet Stress Override

Current behavior:

- Direct wetting is tied to the global biological activity window and per-zone
  drydown holds.
- On 2026-05-23, activity started 06:00, duration was 960 min, south/west
  drydown was 120 min, and center drydown was 180 min. That closed center after
  19:00 and south/west after 20:00.

Problem:

- Evening dry recovery needed water after 20:00, but all mister zones were
  gated.

Existing planner levers:

- `direct_wet_south_drydown_before_off_min`
- `direct_wet_west_drydown_before_off_min`
- `direct_wet_center_drydown_before_off_min`
- `direct_wet_min_temp_f`
- `sw_direct_wet_gate_enabled`

These are planner-pushable Tier 2, but they are not routine Tier 1 keys and the
planner prompt does not make them prominent enough for dry recovery.

Proposed new tunables:

| Tunable | Range | Default | Meaning |
|---|---:|---:|---|
| `direct_wet_stress_override_enabled` | bool | off | Opens mister zones during VPD-high recovery if safety gates pass. |
| `direct_wet_stress_vpd_margin_kpa` | 0.0-0.5 | 0.05 | VPD excess needed to override drydown. |
| `direct_wet_stress_min_dew_margin_f` | 3-15 | 8 | Minimum dew margin. |
| `direct_wet_stress_latest_hour` | 17-24 | 22 | Latest local hour for stress override. |
| `direct_wet_stress_leaf_wetness_max` | sensor units | conservative | Blocks override if leaf wetness is elevated. |

Expected outcome:

- Prevents the exact 20:00-22:00 no-moisture failure mode.
- Preserves disease-risk controls with dew margin, local hour, and leaf wetness.

### 5. Fog Stress Window

Current behavior:

- Fog has a fixed time window and safety gates.
- On 2026-05-23, fog time ended at 17:00, before the VPD recovery shoulder.

Proposed tunables:

| Tunable | Range | Default | Meaning |
|---|---:|---:|---|
| `fog_stress_window_extend_enabled` | bool | off | Allows fog after normal window during VPD-high stress. |
| `fog_stress_window_latest_hour` | 17-22 | 19 | Latest local hour for fog stress extension. |
| `fog_stress_min_dew_margin_f` | 5-15 | 10 | Dew margin required for fog extension. |
| `fog_stress_min_leaf_wetness_clear_min` | 0-180 | 60 | Requires dry leaf sensors before fog extension. |

Expected outcome:

- Gives Iris a bounded fog path for hot/dry evening shoulders without opening
  fog at unsafe night humidity.

### 6. Solar Feed-Forward Cooling

Current behavior:

- Firmware sees `solar_w_m2`, but active cooling starts from temperature band
  state, not forecast/load prediction.

Problem:

- Once the greenhouse is already above band on high solar days, catch-up is
  hard. Last-14-day data shows hot and VPD-high overlap dominates the worst
  compliance days.

Proposed tunables:

| Tunable | Range | Default | Meaning |
|---|---:|---:|---|
| `solar_preventive_cooling_enabled` | bool | off | Allows pre-cooling before high edge during high solar. |
| `solar_cooling_w_m2` | 300-1000 | 750 | Solar threshold. |
| `solar_cooling_temp_margin_f` | 0-5 | 2 | Start cooling this far below high edge under high solar. |
| `solar_cooling_vpd_guard_kpa` | 0-0.5 | 0.1 | If VPD is already near high edge, require vent-mist assist availability. |

Expected outcome:

- Reduces early heat overshoot and gives misters/fog time to humidify before the
  dry peak.

### 7. Occupancy Split

Current behavior:

- Occupancy can block routine fans/fog/mist outside safety modes.

Problem:

- This is reasonable for wetting and noise, but it can leave hot-band
  compliance under-controlled.

Proposed tunables:

| Tunable | Range | Default | Meaning |
|---|---:|---:|---|
| `occupancy_allow_cooling_fans` | bool | off | Allows cooling fans while occupied when temp is above high edge. |
| `occupancy_cooling_fan_stage_max` | 1-2 | 1 | Max fan stage allowed during occupancy. |
| `occupancy_allow_moisture` | bool | off/operator only | Should remain operator-controlled, not routine AI. |

Expected outcome:

- Keeps people protected from unexpected wetting while allowing bounded thermal
  correction.

## Second-Order Tunability Opportunities

These are lower priority than cooling, moisture availability, and solar
feed-forward, but they should be in the longer design because they convert more
hardcoded firmware policy into bounded AI policy.

| Current hardcoded policy | Code anchor | Proposed AI-facing knob | Why it matters |
|---|---|---|---|
| Heat target is fixed at 25% into the band | `BAND_HEAT_TARGET_FRACTION` in `greenhouse_logic.h` | `heat_target_fraction` | Lets Iris choose lower-quartile vs mid-band heating by crop stage, night load, and gas cost while preserving safety and heat2 rails. |
| Cold outdoor vent guard uses `temp_low - 10 F` | `outdoor_cold_for_vent` in `greenhouse_logic.h` | `cold_vent_guard_delta_f` | Lets Iris avoid cold slugs on winter nights but use outdoor exchange more freely in spring. |
| Cold dehum headroom is fixed around `max(2 F, temp_hysteresis)` | `cold_dehum_allowed` in `greenhouse_logic.h` | `cold_dehum_headroom_f` | Gives Iris a safer low-VPD correction knob when the house is near the cold edge. |
| VPD hysteresis cap is fixed at 33% of VPD band width | `band_vpd_hysteresis()` | `vpd_hysteresis_cap_fraction` | Lets Iris trade chatter vs recovery speed for very narrow or very wide crop-derived VPD bands. |
| Summer vent min runtime is exposed but not wired | `summer_vent_min_runtime_s` registry/global | Wire existing knob into a latch | Prevents rapid vent/seal churn when outdoor air is temporarily cooler and drier. |
| Mister fairness window and stress ratios are hardcoded | `select_overdue_zone()` and VPD weighting in `controls.yaml` | `mister_fairness_window_s`, `mister_high_stress_ratio`, `mister_vpd_weight_threshold` | Lets Iris tune zone equity when west/east/center stress distribution changes by crop layout. |
| Irrigation weather skip uses fixed VPD/temp cutoffs | irrigation block in `controls.yaml` | `irrig_skip_vpd_min_kpa`, `irrig_skip_outdoor_min_f` | Lets Iris adapt watering skips to crop season and night disease risk. |

These should not all be shipped together. They should be promoted only after a
specific replay question proves each one changes a real decision without
violating invariants.

## End-State Firmware Shape

The target architecture is not "AI drives relays." It is a deterministic
executor with an AI-owned policy object. Firmware should continue to own sensor
plausibility, actuator contradictions, min-on/min-off, safety modes, and
interlocks. Iris should own bounded policy posture inside those rails.

### Control Loop Contract

Each control cycle should evaluate in four layers:

1. **Hard safety layer**: validate sensors, detect stale outdoor data, enforce
   `SENSOR_FAULT`, `SAFETY_COOL`, `SAFETY_HEAT`, relay dwell, heat/vent
   exclusivity, heat2-with-heat1, irrigation conflicts, water-budget emergency
   rules, fog RH/temp gates, and occupancy hard blocks.
2. **Stress context layer**: compute temp error, VPD error, dew margin, leaf
   wetness status, solar/load pressure, outdoor cooling/drying value, occupancy
   status, recent mode churn, and whether each actuator path is actually
   available.
3. **AI policy layer**: apply the current tunable posture for cooling, moisture,
   dehumidification, heating, priority arbitration, and stress overrides.
4. **Actuator executor layer**: resolve one coherent mode/output set, clamp all
   tunables, publish requested-vs-served diagnostics, and record why any
   requested path did not run.

That shape maximizes AI control without making the ESP32 improvise. Bad AI
policy gets clipped by deterministic clamps and is visible through cfg readback
and block-reason telemetry.

### AI Policy Object

The planner-facing policy should eventually be grouped this way:

| Policy group | AI owns | Firmware still owns |
|---|---|---|
| Band intent | `temp_low`, `temp_high`, `vpd_low`, `vpd_high`, `bias_heat`, `bias_cool` | hard safety rails and relational validation |
| Cooling posture | fan2 threshold, all-fans-at-high, cooling exit hysteresis, cold-vent guard, solar feed-forward | safety cool, vent/fan relay interlocks, cold-slug invariant |
| Moisture posture | VPD dwell, mister pulse cadence, fog escalation, direct-wet stress override, fog stress extension | RH ceiling, dew/leaf-wetness hard gates, water/irrigation locks |
| Priority arbitration | when VPD may preempt cooling hold, when solar may pre-stage cooling, when vent-mist assist is worth water | safety preemption and mode coherence |
| Heating posture | heat target fraction, heat hysteresis, heat2 latch margin | gas-valve min-off, heat/vent exclusivity, freeze safety |
| Dehumidification posture | low-VPD aggressiveness, cold-dehum headroom, outdoor dewpoint preference | no cold shock, fresh outdoor-data requirement |
| Occupancy posture | whether cooling fans are allowed while occupied; max occupied fan stage | no automatic wetting/fog while occupied unless operator permits |
| Zone fairness | mister fairness window, center/east weighting, pulse weighting | zone gates, fertilizer/irrigation safety, relay watchdogs |

### First-Class Stress Context

Several proposed knobs need inputs that are present in the database but not yet
first-class in `SensorInputs` or replay:

- dew margin (`temp_avg - dew_point`) for wetting safety;
- leaf wetness north/south for disease risk;
- solar/load pressure for feed-forward cooling;
- recent VPD and temp slopes for "recovery is working" vs "still drifting";
- actuator availability and block reasons for direct wet, fog, occupancy,
  irrigation, water budget, and vent interlocks.

The current diagnostic PR starts this by exposing moisture/fog block reasons.
The next design step is to make dew margin, leaf wetness, and actuator
availability explicit in replay fixtures so stress overrides can be proven
against historical windows instead of inferred from daily summaries.

## Planner Operating Recipes

These are the lessons Iris should apply once the corresponding knobs exist.
They are deliberately operational: the planner should pick the smallest posture
that makes the next few hours controllable, then relax after recovery.

### Hot, High-Solar Day

Trigger:

- forecast or live solar >= 750 W/m2;
- temp is within 2 F below `fw_temp_high`, or prior similar days breached within
  two hours;
- VPD is near or above high edge.

Actions:

- set `cool_stage2_over_high_f` to 0.5-1.0 F;
- enable `cool_all_fans_at_high_enabled` during the stress window if hot time
  has included more than 30-60 min with one fan;
- keep `cool_exit_hysteresis_f` aggressive enough to cool below the high edge,
  but preserve the cold-outdoor guard;
- pre-position moisture: verify fog/direct-wet availability before assuming
  vent-mist assist will recover VPD;
- once the solar load falls and temp is back in band, relax both-fan posture to
  reduce energy and fan wear.

### Hot And Dry While Venting

Trigger:

- `mode_reason=temp_high` or `summer_vent`;
- VPD above `fw_vpd_high`;
- `vent_mist_assist` requested, or VPD continues rising during ventilation.

Actions:

- first inspect `vent_mist_assist_status`, `moisture_block_reason`,
  `direct_wet_zone_mask`, and `fog_block_reason`;
- if blocked by time window or drydown and dew margin/leaf wetness are safe,
  use direct-wet or fog stress extension rather than lowering VPD thresholds;
- if served but VPD is not falling, shorten `mister_engage_delay_s`, shorten
  pulse gaps, lower `fog_escalation_kpa`, or increase zone pulse weighting;
- if outdoor air is cooler but much drier, do not keep increasing ventilation
  without pairing it with moisture availability.

### Evening Dry Recovery

Trigger:

- VPD remains above high after 18:00;
- temp is near or below the high edge;
- dew margin >= 8 F and leaf wetness is clear;
- normal direct-wet/fog windows have closed.

Actions:

- do not unwind moisture solely because the clock crossed the normal drydown
  boundary;
- use `direct_wet_stress_override_enabled` or shortened direct-wet drydown
  until VPD is back inside band;
- cap stress override by latest local hour and leaf wetness;
- if temp is no longer meaningfully hot, allow VPD to preempt cooling hold.

### Low-VPD / Disease-Risk Recovery

Trigger:

- VPD below low edge, dew margin narrow, or leaf wetness elevated.

Actions:

- prefer dehumidifying with outdoor air only when outdoor data is fresh and
  outdoor dewpoint is lower;
- make `cold_dehum_headroom_f` stricter when the house is near the cold edge;
- keep fog/direct-wet stress overrides disabled until dew and leaf conditions
  recover.

### Cold Night / Heating Economy

Trigger:

- temp is near the low edge for long periods, or heating overshoots and drives
  VPD high.

Actions:

- tune `heat_target_fraction` rather than widening the crop band; lower
  fractions protect the low edge with less overshoot, higher fractions reduce
  cold-band dwell;
- use heat2 latch margin and heat hysteresis as gas-valve protection, not as
  hidden crop policy;
- keep heat/vent exclusivity deterministic.

## Initial AI Posture Profiles

These are not hardcoded rules. They are suggested starting postures for Iris to
select, explain, and back out of based on live response.

| Profile | When Iris should use it | Cooling posture | Moisture posture | Exit condition |
|---|---|---|---|---|
| `hot_solar_preload` | Solar forecast/live >= 750 W/m2 and temp within 2 F below high edge | `cool_stage2_over_high_f=0.5-1.0`, all-fans-at-high only if prior one-fan hot time >30-60 min | Pre-check direct-wet/fog availability; keep moisture thresholds band-coupled | Temp stays in band through solar peak or clouds reduce load |
| `hot_dry_venting` | `VENTILATE` plus VPD above high edge | aggressive fan2, normal cold guard | vent-mist assist must be served; if blocked, alter direct-wet/fog windows rather than VPD threshold | VPD below high edge for 30-60 min and temp not rising |
| `evening_dry_recovery` | 18:00-22:00 VPD-high, dew margin >=8 F, leaf wetness clear | keep cooling active only if temp is above high or rising | direct-wet/fog stress extension with latest-hour cap | VPD recovered or dew margin/leaf wetness becomes unsafe |
| `cold_dehum_guarded` | VPD-low while temp near low edge or outdoor air is cold | no cooling; dehum only with fresh drier outdoor data | disable wetting overrides | VPD clears low edge without temp approaching safety_min |
| `cold_night_economy` | Overnight cold load without VPD-high stress | tune heat target fraction and heat hysteresis; never fight with vent | conservative moisture, avoid fog | Temp recovers above lower target and heat cycles are stable |

The planner should record which profile it chose in `conditions_summary` and
the plan hypothesis. If two profiles conflict, priority is safety, then temp
band, then VPD band, then resource cost.

## Runtime Prompt Handoff

The runtime planner surfaces are not yet fully aligned with this design. This
is genai/coordinator territory, so the firmware-side handoff is explicit:

- `ingestor/iris_planner.py` and `docs/planner/greenhouse-playbook.md` still
  describe `d_cool_stage_2` as "fan2 engages at Thigh + this." In current
  band-first firmware, the active path derives or now prototypes fan2 staging
  through `cool_stage2_over_high_f`; `d_cool_stage_2` is a legacy/compatibility
  field and should not be taught as the primary AI cooling knob.
- The prompt should add the diagnostics `vent_mist_assist_status`,
  `moisture_block_reason`, `direct_wet_zone_mask`, and `fog_block_reason` to
  the VPD-high diagnostic flow once they are deployed and visible through MCP.
- The prompt should treat `summer_vent_min_runtime_s`,
  `mist_vent_close_lead_s`, `mist_vent_reopen_delay_s`, `mister_on_s`,
  `mister_off_s`, `mister_all_on_s`, `mister_all_off_s`, and
  `mister_max_runtime_min` as unavailable for routine AI tuning until a
  firmware PR wires or retires them.
- When future registry promotion lands, the Tier 1 cooling dictionary should
  prefer `cool_stage2_over_high_f`, `cool_exit_hysteresis_f`, and
  `cool_all_fans_at_high_enabled` over indirect `temp_hysteresis` or
  `d_cool_stage_2` changes.

## Historical Model Implications

The 30-day and daily-summary data point to timing and actuator-availability
problems more than simple setpoint mismatch.

Materialized daily-summary cross-check for 2026-05-01 through 2026-05-24:

| Metric | Value |
|---|---:|
| Days analyzed | 24 |
| Average both-axis compliance | 62.3% |
| Hot stress | 114.1 h |
| Cold stress | 36.4 h |
| VPD-high stress | 121.0 h |
| VPD-low stress | 34.7 h |
| Dew-point risk | 10.4 h |
| Fan runtime | 98.5 h fan1 / 98.4 h fan2 |
| Vent runtime | 139.6 h |
| Fog runtime | 42.1 h |
| Zone mister runtime | 53.0 h |
| Mister water | 5366.6 gal |
| Fan cycles | 569 fan1 / 615 fan2 |
| Vent cycles | 314 |
| Fog cycles | 933 |
| Mister cycles | 3032 |
| Dehum cycles | 0 |
| Daily minimum dew-margin range | 3.7 F minimum; 4.9 F average daily minimum |

Simple daily correlations are not causal, but they are useful sanity checks:

| Relationship | Correlation | Interpretation |
|---|---:|---|
| Fan runtime vs hot stress | 0.962 | Fans run most on bad hot days; compliance misses are not just missing fan commands. They point to staging, lead time, and physical capacity. |
| Fog runtime vs VPD-high stress | 0.852 | Fog is already being used on dry-stress days; timing/gating and escalation posture matter more than blanket "more fog." |
| Mister water vs VPD-high stress | 0.652 | Water use rises on dry days but does not guarantee VPD compliance. The path must be available in the right window. |

- The worst May days had both high thermal stress and high VPD stress. For
  example, 2026-05-11 had 10.92 hot hours and 11.45 VPD-high hours, with
  13.11 vent runtime hours and more than 10 fan runtime hours per fan. That is
  not "equipment never ran"; it is "control was late or capacity-limited under
  combined heat/dry load."
- 2026-05-23 had lower absolute heat than 2026-05-11, but still recorded
  8.85 hot hours and 6.35 VPD-high hours. Its distinctive failure was the
  evening moisture gap after normal direct-wet/fog windows closed.
- Several low-compliance days used substantial fog and mister water. More water
  alone is not the answer; the planner needs to know whether water was applied
  in the right window, through the right path, and while ventilation was helping
  or defeating humidification.
- Daily minimum dew-margin can be narrow on poor days, so stress wetting cannot
  be a blanket override. For example, daily summaries show VPD-low stress on
  2026-05-10 (5.00 h), 2026-05-11 (4.58 h), and 2026-05-09 (3.87 h), with
  several days showing dew margins near 4-5 F. Stress wetting needs dew-margin
  and leaf-wetness gates, latest-hour limits, and an exit condition that backs
  out after VPD recovers.
- May daily summaries reported zero dehumidification cycles while still showing
  10.4 h of dew-point risk. That does not prove dehum should have run, but it
  does show the low-VPD/disease-risk side of the controller needs better
  observability before any broad moisture override.

This supports a two-part strategy:

1. Use cooling aggression and solar feed-forward for low-biological-risk
   improvements to temp-band compliance.
2. Add moisture stress overrides only with explicit disease-risk inputs and
   replay coverage, because the data shows many hours where extra water is
   useful and some hours where it would be unsafe.

## Tunable Hygiene

Before claiming the system is maximally AI-tunable, the registry should separate
effective knobs from dead or reserved knobs. The independent firmware audit
found these exposed ids are not currently meaningful AI controls:

- `summer_vent_min_runtime_s` is declared/read back but reserved and not wired
  into a latch.
- `mist_vent_close_lead_s` and `mist_vent_reopen_delay_s` are exposed but not
  active in the band-first controller.
- `mister_on_s`, `mister_off_s`, `mister_all_on_s`, `mister_all_off_s`, and
  `mister_max_runtime_min` are legacy/readback-only relative to the current
  pulse/water-budget implementation.

Planner prompts and `/reference/ai-tunables/` should not teach Iris to rely on
these until a firmware PR either wires them or marks them explicitly retired.

## Immediate Planner Lessons And Prompt Changes

Production lesson changes made on 2026-05-24:

1. Lesson `105` was updated to encode the 2026-05-23 severe dry-day evening
   recovery failure mode: do not unwind by clock while VPD is still high and dew
   margin is healthy; verify fog window, direct-wet drydown, direct-wet minimum
   temp, occupancy, and vent-mist assist.
2. Lesson `95` was updated because it is high-confidence and appears in the
   runtime top-10 `lessons()` response. It now tells Iris that VENTILATE misting
   only works if the moisture path is not blocked.
3. Lesson `104` was updated with the 30-day solar feed-forward result: when
   solar is high and temperature is within 2 F of the high edge, pre-stage
   cooling and moisture before the breach rather than waiting for `temp_high`.
4. A new low-confidence cooling lesson was inserted for the 2026-05-23
   one-fan/no-fan hot-time pattern.

Recommended prompt/playbook edits:

- In the VPD-high diagnostic flow, add: "If `vent_mist_assist` is active but
  VPD remains high, check whether water actually flowed. If not, inspect
  direct-wet/fog/occupancy/irrigation gates before changing VPD thresholds."
- In the high-solar flow, add: "If solar exceeds 750 W/m2 and temp is within
  2 F of the high edge, pre-stage cooling and band-coupled moisture. Do not wait
  for `temp_high` if prior hot breaches are common under the same forecast."
- In the evening recovery guidance, add: "On hot/dry days, direct-wet drydown
  is a control knob, not just a disease-risk knob. If dew margin is healthy and
  leaf wetness is zero, shorten drydown holds or use stress override until VPD
  recovers."
- In the cooling diagnostic flow, add: "If hot time includes more than 30-60 min
  with one fan, use the most aggressive stage-2 posture and consider both-fans
  at high edge."
- In the SOLAR_MAX/deviation prompt, add: "If solar exceeds forecast or crosses
  the feed-forward threshold, pre-position cooling and moisture before heat/VPD
  breaches."

## Proposed Firmware Implementation Sequence

### PR 1 - Diagnostics Only

Add readbacks/text sensors:

- `moisture_block_reason`
- `vent_mist_assist_status`
- `direct_wet_zone_mask`
- `fog_block_reason`

No behavior change. Replay diff should be zero.

Current implementation status on 2026-05-24:

- `firmware/greenhouse/hardware.yaml` adds the four diagnostic text sensors.
- `firmware/greenhouse/controls.yaml` publishes moisture block reason, direct
  wet eligibility, fog block reason, and vent-mist assist status.
- `ingestor/entity_map.py` maps the four diagnostics into generic state capture.
- Validation run: `make firmware-check`, `make test-firmware`, and
  `make firmware-invariants` all pass locally.

### PR 2 - Cooling Aggression Tunables

Add:

- `cool_stage2_over_high_f`
- `cool_exit_hysteresis_f`
- optional `cool_all_fans_at_high_enabled`
- `cold_vent_guard_delta_f`

Replay diff expected and should be reviewed against hot-day corpus. This is the
highest-value behavior OTA candidate because it is simple and low biological
risk.

Current firmware-side prototype status on 2026-05-24:

- `firmware/lib/greenhouse_types.h` adds explicit cooling policy fields:
  `cool_stage2_over_high_f`, `cool_exit_hysteresis_f`,
  `cold_vent_guard_delta_f`, and `cool_all_fans_at_high_enabled`.
- `firmware/lib/greenhouse_logic.h` uses `cool_stage2_over_high_f` for fan2
  staging and `cool_exit_hysteresis_f` for cooling hold exit. Cold-outdoor
  cooling entry preserves the prior band-scaled margin so aggressive fan
  staging does not create cold-slug vent cycling.
- `firmware/greenhouse/globals.yaml`, `tunables.yaml`, and `sensors.yaml`
  expose local HA numbers/switch plus cfg readbacks for the four prototype
  knobs.
- `firmware/test/test_greenhouse_logic.cpp` now covers clamps, explicit stage-2
  delta, all-fans-at-high, and cooling-exit hysteresis.
- Validation run: `make test-firmware`, `make firmware-invariants`, and
  `make firmware-check` pass locally.
- Replay measurement against `HEAD`: `THRESHOLD_PCT=100 make
  firmware-replay-worktree OLD=HEAD` reports 3440 divergent rows out of 193525
  rows (1.78%). Most direct relay-only divergence is expected fan2 engagement:
  2131 rows where old firmware had one fan and new firmware runs both fans in
  `VENTILATE`.

Planner contract handoff before this can be a real AI knob:

- Add schema names and registry rows in `verdify_schemas/tunable_registry.py`.
- Add dispatcher object-id routes and cfg readback mappings through the
  coordinator-owned registry path.
- Add MCP/planner Tier classification and generated `/reference/ai-tunables/`
  output.
- Document required service bounces for `verdify-ingestor` and `verdify-mcp`.
- Re-run replay diff as a PR artifact with an intentional divergence threshold
  and coordinator approval.

### PR 3 - Direct-Wet And Fog Stress Override

Add bounded stress override tunables with dew margin and leaf wetness gates:

- `direct_wet_stress_override_enabled`
- `direct_wet_stress_vpd_margin_kpa`
- `direct_wet_stress_min_dew_margin_f`
- `direct_wet_stress_latest_hour`
- `fog_stress_window_extend_enabled`
- `fog_stress_window_latest_hour`

Replay must prove this only opens during VPD-high stress and never when dew
margin or leaf wetness is unsafe.

### PR 4 - VPD Preempts Cooling Hold

Add:

- `vpd_preempts_cooling_hold_enabled`
- `vpd_preempt_max_temp_over_high_f`
- `vpd_preempt_min_dew_margin_f`

Replay must focus on hot-dry evenings and avoid preempting while temp is still
meaningfully above high edge.

### PR 5 - Solar Feed-Forward

Add:

- `solar_preventive_cooling_enabled`
- `solar_cooling_w_m2`
- `solar_cooling_temp_margin_f`
- `solar_cooling_vpd_guard_kpa`

Replay must compare high-solar days against cold-slug and VPD-low risks.

### PR 6 - Tunable Hygiene

Wire or retire exposed knobs that are currently misleading:

- wire `summer_vent_min_runtime_s` into a real VENTILATE latch, or mark it
  retired;
- remove planner-facing emphasis from legacy mister timing knobs that the
  current pulse controller does not use;
- ensure every public AI tunable changes live control behavior or is explicitly
  readback-only.

This PR has high planner value even if behavior change is small: it prevents
Iris from "tuning" fields that cannot affect compliance.

### PR 7 - Heat And Dehumidification Policy Relaxations

Promote only after the first six PRs produce clean replay and post-OTA data:

- `heat_target_fraction`
- `heat2_latch_margin_f`
- `cold_dehum_headroom_f`
- `vpd_hysteresis_cap_fraction`

These let Iris tune cold-edge protection, gas overshoot, and low-VPD recovery
without widening the crop band.

### PR 8 - Zone And Irrigation Secondary Controls

Promote after moisture-path diagnostics have enough live history:

- `mister_fairness_window_s`
- `mister_high_stress_ratio`
- `mister_vpd_weight_threshold`
- irrigation weather-skip thresholds

These are useful for a maximally fungible controller, but they are second-order
relative to cooling capacity, moisture-path availability, and solar
feed-forward.

## Validation Requirements

For every behavior PR:

- `make firmware-check`
- `make firmware-invariants`
- `make test-firmware`
- `make firmware-replay OLD=<base> NEW=HEAD`
- replay corpus slices for 2026-05-10 through 2026-05-16 and 2026-05-23
- explicit replay diff table for mode, fans, fog, misters, heat, and vent
- 48-hour bake and OTA freeze checks before deploy

For planner/docs changes:

- regenerate and inspect the planner context bundle;
- ensure the top-10 `lessons()` output includes the gate-check lesson;
- ensure generated public lessons/AI-tunables pages are refreshed if site output
  is part of the PR;
- if schemas/registry change, document required `verdify-mcp` and
  `verdify-ingestor` restarts.

## What This Would Have Changed On 2026-05-23

The proposed design would have given Iris three extra ways to act:

1. Cooling: both fans could have been requested closer to the high edge during
   the 18:00-22:00 hot recovery shoulder.
2. Moisture: direct-wet stress override could have kept at least one mister zone
   eligible from 20:00-22:00 while VPD was high and dew margin was about 9-15 F.
3. Priority: once temp was near the high edge, VPD recovery could have preempted
   cooling hold instead of waiting for full cooling hysteresis clearance.

This would not guarantee perfect band compliance. The bad-day evidence still
shows physical capacity limits: hot and VPD-high overlap dominates the worst
days, and misters/fog only partially recover dry outdoor air under solar load.
But it would remove avoidable "AI wanted recovery, firmware/gates had no
available actuator path" failures and make future plans much more actionable.
