# Mechanical response matrix and overnight dehumidification review - 2026-06-22

## Scope

This review answers: for each indoor temperature/VPD condition, and for the
outdoor weather that makes ventilation helpful or harmful, which mechanical
equipment should be on to improve band compliance?

The biggest focus is overnight dehumidification. In greenhouse terms:

- `high VPD` means too dry. The system needs moisture delivery or humid air import.
- `low VPD` means too wet. The system needs dehumidification, usually by raising
  air moisture capacity with heat, exchanging with drier outdoor air, or both.

Only read-only production DB queries were used. No device, ArgoCD, or DB writes
were performed.

## Evidence Window

Primary data window:

- Start: `2026-06-18 02:43:52 UTC`, first telemetry from firmware
  `2026.6.17.2042.dcc6078`.
- End: latest climate row during this review, `2026-06-22 23:08 UTC`.
- Climate rows in window: 6,819.
- `climate_action_log` rows in window: 15,868.
- Current steady `band_track_fraction`: `0.25`.
- Live moisture-exchange parameters observed in setpoint/diagnostic telemetry:
  `vent_exchange_fraction=0.30`, raw `dehum_aggressive_kpa=0.60`, effective
  dehum-aggressive diagnostic roughly `0.51-0.52 kPa` at review time.

The companion before/after architecture review is
`docs/reviews/firmware-band-performance-review-2026-06-22.md`.

> **2026-07-03 envelope note (#412, added by #413):** the door screen-window opened
> ~2026-06-19 — i.e. **inside this review's evidence window** — and stays open until
> fall. It ~3×'d passive night air exchange (the standing indoor−outdoor moisture
> surplus stepped **+5.7 → +1.9 g/m³** at flat fog/mister source duty ~22%→~20%,
> with no heat1 night-duty increase), so the night baselines observed here blend
> pre- and post-open regimes. The estimator's closed-vent assumption — that indoor
> air mixes toward outdoor only when `DEHUM_VENT` opens the vent by
> `vent_exchange_fraction` (below) — is weakened while the window is open: a large
> passive exchange path exists even with the vent closed. Re-read the projected
> vent-gain and vent-selection conclusions with that in mind. Every bake/KPI
> comparison window must record the envelope config; never change the window state
> mid-bake.

## Current Firmware Behavior

The current controller does not simply compare against the served low/high band.
It pinches the served band toward the target using `band_track_fraction`, so the
effective control corridor is:

```text
effective_low  = low  + f * (target - low)
effective_high = high - f * (high - target)
```

With `f=0.25`, many rows that look in-band against the served band are active
control rows against the pinched control corridor.

The moisture decision path is centered in
`firmware/lib/greenhouse_logic.h::estimate_moisture_exchange()`:

- For low VPD / too wet, it estimates a heat candidate by conserving vapor and
  warming the air by a 1.5 F probe step.
- It estimates a vent candidate by mixing indoor and outdoor air using
  `vent_exchange_fraction`.
- Vent dehum is selected only if outdoor data is fresh, projected VPD gain is
  above `dehum_vent_gain_margin_kpa`, and the mixed air will not overcool below
  the effective temperature low edge.
- If venting and heat both help, `DEHUM_VENT` may co-run `heat1` after
  `dehum_heat_assist_min_dwell_ms` and only when there is heating demand.
- If heat helps but venting does not, the estimator returns heat-assist, but
  normal mode selection only turns heat on when temperature demand or the VPD
  safety rail also calls for it.
- For high VPD / too dry, the same estimator can redirect sealed humidification
  into vent-based humid import when outside air is meaningfully more humid,
  dew-safe, and not too cold.

`DEHUM_VENT` equipment output is currently:

- Vent open.
- Lead fan on, with rare both-fan aggressive dehum.
- Optional `heat1` only after the heat-assist dwell and only when heat demand is
  present.
- Never `heat2` in dehum heat-assist.

## Condition Matrix Observed

The table below classifies 15-minute buckets against the effective pinched
corridor. `top_combo` is the most common relay combination in that condition.

| Temp state | VPD state | Night | Outdoor modifier | Buckets | Top combo |
|---|---|---:|---|---:|---|
| high | high_dry | no | wet_needed | 118 | vent+fan+wet |
| high | high_dry | yes | wet_needed | 5 | vent+fan |
| high | in | no | not_vpd_edge | 33 | vent+fan |
| high | in | yes | not_vpd_edge | 4 | none |
| in | high_dry | no | wet_needed | 181 | none |
| in | high_dry | yes | wet_needed | 71 | none |
| in | in | no | not_vpd_edge | 221 | none |
| in | in | yes | not_vpd_edge | 162 | none |
| in | low_wet | no | heat_assist | 20 | none |
| in | low_wet | yes | heat_assist | 13 | none |
| low | in | no | not_vpd_edge | 4 | heat |
| low | low_wet | no | heat_assist | 2 | heat |

Interpretation:

- High-temp and high-dry daytime rows are being handled with the expected
  vent/fan/wet combination.
- In-band temp plus high-dry VPD is often idle. That is not necessarily wrong
  if dwell/hysteresis is protecting relays, but it is the largest dry-side
  opportunity: wet equipment is historically effective in this condition.
- In-band temp plus low-wet VPD is also often idle, even when the outdoor-aware
  physics classifies heat as the effective dehumidification actuator. This is the
  key overnight dehum gap.
- Low-temp rows correctly use heat.

## Historical Effectiveness

These values are row-level 30-minute deltas from the same post-rollout window.
Negative is better for high-dry surplus and high-temp surplus. Positive VPD
delta is better for low-wet dehumidification.

### Low VPD / Too Wet

| Scenario | Night | Relay combo | Rows | Avg VPD delta 30m | Avg low-wet deficit delta 30m | Avg temp delta 30m |
|---|---:|---|---:|---:|---:|---:|
| low_wet | no | heat | 44 | +0.098 | -0.065 | +1.709 F |
| low_wet | no | none | 188 | +0.045 | -0.033 | +1.077 F |
| low_wet | yes | heat | 16 | +0.013 | -0.013 | +0.810 F |
| low_wet | yes | none | 145 | -0.009 | +0.009 | +0.118 F |

Findings:

- Heat improves low-wet rows. The effect is strongest in daytime, but it remains
  directionally right overnight.
- Overnight no-action low-wet rows drift wetter on average.
- Current firmware can identify `heat_assist`, but if temperature is not already
  low enough to call for heat, ordinary low-wet rows often remain idle. That is
  a compliance gap if the objective is to maximize VPD tracking rather than save
  energy.

### High VPD / Too Dry

| Scenario | Night | Relay combo | Rows | Avg VPD delta 30m | Avg high-dry surplus delta 30m | Avg temp delta 30m |
|---|---:|---|---:|---:|---:|---:|
| high_dry | no | none | 508 | -0.084 | -0.023 | -0.032 F |
| high_dry | no | vent+fan | 287 | -0.016 | +0.013 | -0.108 F |
| high_dry | no | vent+fan+wet | 748 | -0.141 | -0.117 | -0.448 F |
| high_dry | no | wet | 42 | -0.175 | -0.112 | +0.061 F |
| high_dry | yes | none | 343 | -0.088 | -0.041 | -0.803 F |
| high_dry | yes | vent+fan | 202 | -0.120 | -0.085 | -1.007 F |
| high_dry | yes | vent+fan+wet | 10 | -0.315 | -0.271 | -1.872 F |
| high_dry | yes | wet | 10 | -0.292 | -0.249 | -0.837 F |

Findings:

- Wet equipment is the best observed response to high-dry VPD.
- Vent+fan alone does not reliably fix dry-side VPD during the day; it is mainly
  a temperature response.
- Vent+fan+wet is effective when heat and VPD are both high, especially during
  hot daytime periods.
- In-temp/high-dry rows with `none` are common. That suggests the dry-side dwell
  and wetting thresholds may be conservative relative to the tracking goal.

### High Temperature

| Scenario | Night | Relay combo | Rows | Avg VPD delta 30m | Avg high-temp surplus delta 30m | Avg temp delta 30m |
|---|---:|---|---:|---:|---:|---:|
| high_temp | no | vent+fan | 126 | +0.024 | +0.120 | -0.443 F |
| high_temp | no | vent+fan+wet | 407 | -0.171 | -0.356 | -0.922 F |
| high_temp | no | none | 37 | -0.376 | -1.438 | -2.907 F |
| high_temp | yes | vent+fan | 9 | -0.069 | -0.271 | -2.275 F |
| high_temp | yes | vent+fan+wet | 1 | -0.370 | -0.490 | -2.745 F |
| high_temp | yes | none | 21 | -0.099 | -0.059 | -2.799 F |

Findings:

- Vent/fan is the right high-temperature mechanical response.
- When high temperature coincides with high-dry VPD, adding wet assist improves
  both temperature and VPD compliance more than vent+fan alone in this window.
- Some `none` high-temp rows improved naturally, usually because the condition
  was transient. That should not be read as "idle is better"; it is a reminder
  that row-level observational data has weather/time-of-day confounding.

## Equipment-by-Equipment Implications

This section translates the relay-combo evidence into actuator guidance. The
confidence is highest for combinations that occur often and lower for rare
equipment states.

| Equipment | Helps most when | Hurts or needs caution when | Evidence strength |
|---|---|---|---|
| `heat1` | Low temp; low VPD / too wet, especially when venting would overcool | High VPD / too dry unless temperature requires heat | Strong directionally. Low-wet heat rows improved VPD deficit more than overnight no-action rows. |
| `heat2` | True low-temperature stage-2 recovery only | Dehumidification fine control | Low. Heat2 is intentionally rare and should not be part of ordinary dehum. |
| `vent` | High temp; low VPD when outdoor air is drier and mixed air stays above temp low; humid import when outside is wetter and dew-safe | Cool low-wet nights where it overcools; high-dry rows where it imports drier air | Strong. Overnight vent dehum raised VPD but cooled the house. |
| `fan1` / lead fan | Any vented exchange: cooling, dehum, humid import | Closed vent except sanctioned recirculation/safety paths | Strong. It is the normal mover for vent exchange. |
| `fan2` | High-temperature stage-2 cooling; severe dehum only if the low-wet margin is large enough | Routine overnight dehum near temp low | Moderate. Fan2 cooling is structurally correct; dehum both-fan rows are rare. |
| `fog` | High VPD / too dry, especially when temperature is safe and dew margin allows wetting | Overnight near target without taper, because overshoot can create low-wet dehum demand | Strong for dry correction; strong qualitative evidence for ping-pong risk. |
| `mister_center` | High-dry assist and vent-cooling assist when fog alone is insufficient | High cycling/wear; wetting near dew margin | Moderate. Combo data shows wet assist works, but per-zone attribution is limited. |
| `mister_south` / `mister_west` | Directional or staged wet assist during dry/hot periods | Same wetting and cycling cautions as center mister | Lower. Current evidence is mostly combo-level, not clean per-zone causality. |

The current data can prove "wet equipment on/off" and "vent/fan/heat" effects
better than it can prove individual mister-zone effects. A daily equipment KPI
should separate fog, center, south, and west wetting whenever relay timing allows.

## Overnight Dehumidification

Post-rollout `DEHUM_VENT` action rows:

| Relay pattern | Night | Rows |
|---|---:|---:|
| fan1 only + vent | no | 29 |
| fan2 only + vent | no | 49 |
| fan1 + heat1 + vent | no | 7 |
| fan2 + heat1 + vent | no | 2 |
| fan1 only + vent | yes | 100 |
| fan2 only + vent | yes | 81 |
| both fans + vent | yes | 6 |
| fan1 + heat1 + vent | yes | 3 |
| no fan/heat + vent | yes | 1 |

Episode-level summary, grouping `DEHUM_VENT` rows separated by more than 90 s:

| Relay combo | Night | Episodes | Avg duration | Avg VPD delta 30m | Avg temp delta 30m | Avg start VPD | Avg start temp |
|---|---:|---:|---:|---:|---:|---:|---:|
| vent+fan | no | 19 | 1.01 min | +0.126 | +0.767 F | 0.816 | 76.46 F |
| vent+fan | yes | 44 | 1.52 min | +0.074 | -0.736 F | 0.789 | 68.96 F |
| vent+heat+fan | no | 1 | 1.02 min | +0.160 | +1.485 F | 0.832 | 74.03 F |

Findings:

- Overnight vent+fan dehum works in the VPD direction: average VPD rose
  `+0.074 kPa` after 30 minutes.
- It cools the greenhouse: average overnight temperature delta was `-0.736 F`.
  That is bad when the house is already near the low temperature edge.
- Heat-assist is rare. Overnight there were only three heat1 rows inside
  `DEHUM_VENT`, and no heat-assist-dominant overnight episode in the 90 s episode
  grouping. The 5 minute heat-assist dwell is longer than the typical observed
  overnight dehum episode.
- A concrete overnight sequence on `2026-06-17 22:49-23:17 MDT` shows the main
  pattern: `SEALED_FOG` for high-dry VPD, then VPD overshoots low, then short
  `DEHUM_VENT` pulses, then fog again. This is not pure outside-weather humidity
  pressure; part of the dehum load is self-induced by fog overshoot.

## Recommended Equipment Matrix

This is the desired mechanical policy if the objective is maximum band
compliance, subject to existing safety rails and relay dwell limits.

| Indoor condition | Outdoor modifier | Preferred equipment | Avoid | Rationale |
|---|---|---|---|---|
| Low temp, VPD in band | Any | `heat1`; `heat2` only at stage-2 threshold | vent/fan | Heat directly fixes temperature; vent fights it. |
| Low temp, low VPD / too wet | Heat helps, vent overcools or weak | `heat1` closed-vent dehum; `heat2` only for temp rail | vent-only dehum | Heat raises VPD and temperature together. Vent-only dries some but cools the house. |
| Low temp, low VPD / too wet | Vent and heat both help | `vent+lead fan+heat1`, bounded and dwell-protected | `heat2`, wet equipment | This is the sanctioned heat-assisted dehum case. Current data has too few long episodes to prove the best dwell. |
| In temp, low VPD / too wet | Heat helps, vent overcools or weak | Add a bounded heat-assist dehum path, likely `heat1` closed-vent first | idle, vent-only | This is the biggest gap: observed rows are mostly idle and drift wetter overnight. |
| In temp, low VPD / too wet | Vent helps and does not overcool | `vent+lead fan`; add heat only if both help and temp is near low edge | wet equipment | Vent dehum is useful when outdoor air is truly drier and not too cold. |
| High temp, low VPD / too wet | Outdoor not wetter | `vent+fan`; dehum is secondary to cooling | heat | Cooling owns the priority; heat would worsen high temp. |
| High temp, VPD in band | Outdoor cooler enough | `vent+fan1`, `fan2` by latch | heat, wet unless VPD turns high-dry | Current cooling ladder is aligned. |
| High temp, high VPD / too dry | Outdoor/cooling requires vent | `vent+fan+wet`; fog or mist based on existing dew/occupancy gates | sealed-only wetting | Observed best high-temp/high-dry combo is vent+fan+wet. |
| In temp, high VPD / too dry | Outdoor not humid enough | sealed fog or mister ladder after dwell | idle | Wet equipment materially reduces high-dry surplus. |
| In temp, high VPD / too dry | Outdoor more humid, dew-safe, not cold | `vent+lead fan` humid import | heat | Firmware has this path; use it only when the estimator proves it lowers VPD. |
| Low temp, high VPD / too dry | Any | Heat for temp plus cautious wetting only if dew-safe | pure heat-only for long periods | Heat fixes temp but raises VPD further. Wetting must be dew-safe at night. |
| Both axes in band | Any | idle | all active equipment | Relay conservation is correct when actual is inside the effective corridor. |

## Proposed Improvements

1. Add explicit estimator telemetry to `climate_action_log`.

   Store `mx_action`, `mx_reason`, `vent_vpd_gain_kpa`, `heat_vpd_gain_kpa`,
   `vent_overcools`, `outdoor_fresh`, and `heat_assist_corun`. Today the
   analysis has to infer these from physics and relays. This should be the first
   change because it makes every later tuning decision easier to verify.

2. Treat "dehum after recent fog" as a first-class self-induced condition.

   The overnight sequence shows fog can push VPD from high-dry to low-wet within
   minutes, causing short dehum pulses. Add a night fog/dehum anti-ping-pong rule:
   after `SEALED_FOG`, require sustained low-wet VPD or a larger low-side margin
   before `DEHUM_VENT`, or taper fog pulses as VPD approaches target.

3. Add a bounded heat-first low-wet path for overnight in-band temperature.

   When `mx.action == MX_HEAT_ASSIST`, temperature is in band, and it is night,
   current behavior often idles because heat is not temperature-required. For
   compliance, consider allowing `heat1` as a VPD dehumidification actuator before
   the low-temp edge is crossed. This should be bounded by dew/temperature rails,
   min-on/off dwell, and replay evidence.

4. Revisit `dehum_heat_assist_min_dwell_ms`.

   The current 5 minute dwell is longer than the typical observed overnight
   `DEHUM_VENT` episode. If heat+vent is still the desired response when both
   help, either shorten the dwell under night low-wet conditions or separate
   "closed-vent heat-assist" from "vent+heat co-run" so short wet events can be
   corrected without opening the vent longer than needed.

5. Keep vent-only dehum, but only for the rows where the estimator proves it.

   Overnight vent+fan raises VPD, but cools the house. It is a good response when
   the outdoor air is drier and mixed air stays above the effective temperature
   low edge. It is a weak response for cool, wet nights where heat is the better
   physics.

6. Tighten high-dry wetting response where temperature is already in band.

   In-temp/high-dry buckets are common and often idle. Wet equipment has the best
   observed 30-minute VPD improvement. Review dry-side dwell, fog escalation, and
   dew-margin gates for cases where nothing is on but VPD remains above the
   pinched high edge.

7. Add daily equipment-effectiveness KPIs.

   Generate daily 15/30/60 minute response summaries grouped by:
   temp state, VPD state, day/night, outdoor dehum class, and relay combo. This
   should become the standard way to tell whether a tuning change actually drove
   compliance higher.

## Bottom Line

The current firmware has the right physics shape: it distinguishes vent dehum,
heat assist, vent humid import, wetting, heating, and cooling. The remaining
problem is policy coverage and observability.

For overnight dehumidification, the data says:

- Vent-only dehum is real and effective on VPD, but it cools the house.
- Heat is the better actuator for cool, low-wet rows, and it improves low-wet
  deficit more than doing nothing.
- Current ordinary low-wet/in-band overnight rows are frequently idle because
  heat-assist is not a standalone VPD action.
- Some overnight dehum demand is created by fog overshoot, so reducing fog/dehum
  ping-pong may improve compliance before more aggressive dehum is needed.

The highest-leverage next change is not "run more vent overnight." It is:

1. log the moisture estimator decision,
2. damp the fog-to-dehum oscillation,
3. allow bounded heat-first dehumidification when night VPD is low and venting
   would overcool or underperform.
