# Firmware band architecture performance review - 2026-06-22

## Scope

This is a data-driven before/after review of the greenhouse firmware and band/setpoint
architecture rolled out in June 2026. The review uses live production telemetry, but
only read-only DB access was used.

The core change under review is the band-compliance firmware architecture:

- Firmware version: `2026.6.17.2042.dcc6078`
- First telemetry row: `2026-06-18 02:43:52 UTC`
- Local rollout time: `2026-06-17 20:43:52 MDT`
- Artifact receipt: `firmware/artifacts/2026.6.17.2042.dcc6078/metadata.env`
  records `deployed_at=2026-06-18T02:46:01Z`

The rollout completed in stages:

| Time MDT | Evidence | Meaning |
|---|---|---|
| 2026-06-17 20:43:52 | `diagnostics.firmware_version` first saw `2026.6.17.2042.dcc6078` | Band architecture OTA live |
| 2026-06-17 21:25-21:26 | `setpoint_snapshot` and `setpoint_changes` saw `band_track_fraction=0.30` | Device default/push visible |
| 2026-06-18 00:23 | `setpoint_changes`, source `plan`, set `band_track_fraction=0.50` | Aggressive pinch trial |
| 2026-06-18 11:11 | `setpoint_changes`, source `plan`, set `band_track_fraction=0.25` | Relaxed pinch after ADR-0004 direction |
| 2026-06-20 10:28 | `diagnostics.firmware_version` first saw `2026.6.20.1026.b7a531b` | Later lighting firmware, preserving band architecture |

## Data Windows

Primary comparison uses matched elapsed windows around the OTA boundary:

| Window | Time range MDT | Sampled climate hours |
|---|---:|---:|
| Pre-matched | 2026-06-13 00:44 to 2026-06-17 20:43 | 113.7 h |
| Post-all | 2026-06-17 20:43 to 2026-06-22 16:43 | 113.2 h |

Sub-windows:

- `post_dcc6078`: first band firmware only, through 2026-06-20 10:28 MDT.
- `post_b7a531b`: later lighting firmware period, 2026-06-20 10:28 MDT onward.
- `post_f0_30`, `post_f0_50`, `post_f0_25`: live `band_track_fraction` epochs.

Sources used:

- `diagnostics`: firmware version and device timing.
- `climate`: all numeric sensor columns in the review interval, 98 numeric fields.
- `setpoint_changes`: firmware-enforced temp/VPD low/high edges and `band_track_fraction`.
- `setpoint_snapshot`: readback/published band fraction evidence.
- `equipment_state`: relay state intervals.
- `system_state`: `greenhouse_state`, `mode_reason`, action text sensors.
- `climate_action_log`: controller action, priority axis, block reasons.
- `v_daily_kpi`, `v_equipment_runtime_daily`, `v_state_durations`,
  `v_daily_oscillation`: daily rollups and cross-checks.
- `alert_log`: alert/regression context.

Pinched control corridor was calculated as:

```text
pinched_low  = low  + f * (target - low)
pinched_high = high - f * (high - target)
```

where `f = band_track_fraction`. Pre-rollout uses `f=0`; post-rollout uses the
effective live value at each sample.

## Executive Findings

The new architecture is materially better at keeping the house inside the actual
control corridor, especially at night. It also made the actuator ladder behave much
more like the intended design: fog-first humidification is visible in the state mix,
stage-2 heat stayed rare, and dew point risk improved.

The main remaining problem is VPD. VPD compliance improved, but dry-side VPD misses
are still the dominant failure mode. The correlations show those misses are coupled
to ventilation/cooling periods and wetting demand, which fits the known arbitration
tradeoff: when cooling owns the priority, the controller vents/fans and VPD can sit
above the band while wetting assists chase but do not fully recover.

The mechanical cost also rose. Vent/fan duty and cycles increased, fog cycles
increased, and center mister cycling stayed very high. This is acceptable only if we
explicitly treat it as the cost of the transitional pinch regime, or if we move toward
ADR-0004 floating-corridor operation and verify the cycle rate falls.

## Before/After Climate Results

| Metric | Pre-matched | Post-all | Change |
|---|---:|---:|---:|
| Avg temp | 74.96 F | 74.94 F | flat |
| Max temp | 90.54 F | 88.61 F | better |
| Temp stddev | 7.01 F | 6.42 F | better |
| Avg VPD | 0.844 kPa | 0.924 kPa | drier |
| VPD stddev | 0.366 kPa | 0.292 kPa | more stable |
| Dew point margin min | 3.94 F | 5.28 F | better |
| Outdoor avg temp | 70.04 F | 71.39 F | slightly warmer |
| Outdoor max temp | 93.67 F | 90.34 F | easier peak weather |
| Solar avg | 336.6 W/m2 | 322.0 W/m2 | slightly easier |
| P95 temp target error | 5.57 F | 4.05 F | better |
| P95 VPD target error | 0.456 kPa | 0.509 kPa | worse |
| P95 zone temp spread | 11.88 F | 9.54 F | better but still high |
| P95 zone VPD spread | 2.284 kPa | 2.095 kPa | better but still high |

Weather was not identical. Post-rollout had slightly warmer average outdoor air but
lower peak outdoor temp and slightly lower solar load. The improved max greenhouse
temperature should therefore be credited partly to weather and partly to control.

## Corridor Compliance

| Metric | Pre-matched | Post-all | `dcc6078` only | `b7a531b` period |
|---|---:|---:|---:|---:|
| Served temp in band | 90.7% | 98.8% | 99.4% | 97.8% |
| Served VPD in band | 51.0% | 57.2% | 49.1% | 69.0% |
| Pinched temp in control band | 90.7% | 91.4% | 93.6% | 88.1% |
| Pinched VPD in control band | 51.0% | 53.7% | 53.1% | 54.6% |
| Pinched both-axis in band | 16.5% | 40.3% | 44.7% | 35.3% |
| P95 pinched temp miss | 0.31 F | 0.33 F | 0.15 F | 0.53 F |
| P95 pinched VPD miss | 0.411 kPa | 0.318 kPa | 0.331 kPa | 0.294 kPa |

Interpretation:

- Temperature compliance is substantially better against the served corridor.
- Pinched temperature compliance is only slightly better because the post corridor is
  narrower by design.
- VPD is the weak axis. The P95 miss improved, but only about half of the samples
  are inside the pinched VPD control band.
- Both-axis compliance more than doubled, from 16.5% to 40.3%, which is the clearest
  single before/after win.

## Day/Night Physiology

| Metric | Pre-matched | Post-all |
|---|---:|---:|
| Day temp avg | 80.48 F | 78.68 F |
| Night temp avg | 73.32 F | 71.02 F |
| DIF, day minus night | 7.16 F | 7.66 F |
| Day VPD avg | 1.147 kPa | 1.074 kPa |
| Night VPD avg | 0.754 kPa | 0.767 kPa |
| Day both-axis pinched compliance | 44.1% | 50.5% |
| Night both-axis pinched compliance | 8.2% | 29.6% |

This is a horticultural improvement. The system kept a useful day/night rhythm,
slightly increased DIF, reduced day VPD, and made night compliance far less bad.
Dew point risk also improved: daily `dp_risk_hours` was nonzero before the rollout
and stayed zero on the post-rollout days in `v_daily_kpi`.

The caution: night VPD compliance is improved, not solved. The best `dcc6078`
sub-window showed 40.1% night both-axis compliance; the later `b7a531b` period fell
to 17.7%, mostly because that period was hotter/drier and spent more time venting.

## Actuator Runtime and Mechanical Load

Matched period relay/runtime summary from corrected `equipment_state` intervals:

| Equipment | Pre hours | Post hours | Pre cycles/day | Post cycles/day | Interpretation |
|---|---:|---:|---:|---:|---|
| Vent | 25.16 | 33.53 | 22.1 | 34.7 | More cooling/air exchange |
| Fan1 | 15.87 | 23.70 | 30.6 | 36.8 | More cooling duty |
| Fan2 | 16.37 | 23.45 | 30.2 | 35.4 | Symmetric fan use maintained |
| Heat1 | 20.46 | 12.32 | 11.2 | 10.8 | Less heat runtime |
| Heat2 | 0.11 | 0.52 | 0.2 | 1.4 | Mostly short f=0.50 episode |
| Fog | 11.65 | 12.85 | 52.5 | 69.5 | Fog-first behavior, more starts |
| Mister center | 8.04 | 8.08 | 69.5 | 69.9 | Still very high cycling |
| Mister south | 2.45 | 3.25 | 23.4 | 26.9 | Slightly up |
| Mister west | 0.59 | 4.64 | 8.5 | 7.7 | More west runtime, fewer cycles/day |
| Water flowing | 13.77 | 14.63 | 84.8 | 66.6 | Slight runtime up, starts down |

The mechanical picture is mixed:

- Heat runtime fell sharply, which is good for gas use and lifecycle.
- Vent/fan runtime and starts increased materially.
- Fog runtime increased only modestly, but fog starts increased about 32%.
- Center mister cycling remains high and deserves its own wear/water review.
- Heat2 was rare overall, but the `f=0.50` trial produced seven heat2 starts on
  2026-06-18. That supports the later relaxation to `f=0.25`.

The later `b7a531b` period changed lighting behavior. Grow-light cycles/day rose
from low single digits during `dcc6078` to about 21.7/day after `b7a531b`, so do
not attribute lighting cycle changes to the band architecture.

## State Machine and Escalation Path

`greenhouse_state` duration shifted as expected:

| State | Pre duration | Post duration |
|---|---:|---:|
| IDLE | 76.9% | 66.5% |
| VENTILATE | 19.8% | 26.5% |
| SEALED_MIST_FOG | 0.2% | 4.9% |
| DEHUM_VENT | 1.1% | 2.0% |
| SEALED_MIST_S1/S2/WATCH | 2.0% combined | near zero |

This is strong evidence that the architecture is doing what it said:

- Fog-first wetting is real. `SEALED_MIST_FOG` replaced most old S1/S2 sealed
  mister time.
- The controller is less idle and more active at the corridor edges.
- DEHUM_VENT is used more, but remains a small fraction of total time.
- VENTILATE increased, especially after the 2026-06-20 lighting firmware period
  when outdoor conditions were warmer/drier.

`climate_action_log` tells the same story:

| Action | Pre rows | Post rows |
|---|---:|---:|
| `IDLE` | 8512 | 7444 |
| `VENT_COOL` | 2005 | 2208 |
| `VENT_COOL_MIST_ASSIST` | 2267 | 2051 |
| `VENT_COOL_FOG_ASSIST` | 1538 | 1766 |
| `HEAT` | 2055 | 1420 |
| `SEALED_FOG` | 68 | 606 |
| `SEALED_HUMIDIFY` | 532 | 10 |
| `DEHUM_VENT` | 239 | 278 |

That confirms:

- Fog-first inversion worked.
- Heat usage decreased.
- Dehumidification is active but not dominant.
- Humidification moved away from mister-first sealed stages.

## Cross-Correlations

Using 15-minute buckets:

| Correlation | Pre | Post | Meaning |
|---|---:|---:|---|
| Temp miss vs outdoor temp | 0.340 | 0.303 | Hot misses still weather-driven |
| Temp miss vs vent duty | 0.404 | 0.393 | Venting responds to hot misses |
| Temp miss vs fan duty | 0.406 | 0.379 | Fan response remains aligned |
| VPD miss vs fog duty | 0.621 | 0.736 | Fog is now tightly coupled to VPD misses |
| VPD miss vs mister duty | 0.506 | 0.683 | Misters still participate in hard dry periods |
| VPD miss vs vent duty | 0.819 | 0.604 | VPD misses are still coupled to cooling/venting |
| VPD high miss vs wet duty | 0.571 | 0.750 | Wet actuators chase dry-side misses |
| Zone VPD spread vs wet duty | 0.675 | 0.802 | Wetting events strongly expose or create zone gradients |

The main operational diagnosis is not "the wetting ladder is idle." It is active.
The problem is that dry VPD misses remain highly correlated with ventilation and
wetting duty. That points to a cooling-priority tradeoff: venting/fans solve heat
but often move the air mass drier than the pinched VPD corridor can tolerate.

## Alerts and Data Quality

Alert volume improved in the most important control-path category:

- `setpoint_unconfirmed` fell from 1330 warnings and 75 critical rows in the
  pre-matched window to 481 warnings and no critical rows post.
- `band_device_db_divergence` was present but small: 1 pre warning vs 10 post
  warnings, all resolved.
- `sensor_offline` warnings were higher post: 16 pre vs 32 post.
- Some open planner alerts remained at the end of the post window
  (`planner_required_plan_missed`, `planner_evaluation_missed`,
  `planner_plan_horizon_missing`, `planner_trigger_sla_timeout`). These are not
  firmware failures, but they weaken the autonomy layer.

Sensor coverage caveat:

- Core climate sensors were present enough for this analysis.
- Some derived target fields in `climate` had about 76% post-window coverage.
- Hydro/Tempest extended fields had much lower post-window coverage in the raw
  climate table. They were not used for the main control conclusions.

## What Is Working

1. The OTA boundary and live behavior are clear in telemetry. The device really did
   move to the band-compliance architecture on 2026-06-17 at 20:43 MDT.
2. Both-axis control-band compliance improved from 16.5% to 40.3%.
3. Night behavior improved substantially: both-axis compliance at night rose from
   8.2% to 29.6%, with the `dcc6078` period reaching 40.1%.
4. Dew point margin improved, and daily dew risk stayed at zero after rollout.
5. Temperature stayed controlled: lower max, lower standard deviation, better
   target tracking, and much better served-corridor compliance.
6. Fog-first wetting is visible and working as an actuator-order change.
7. Heat runtime fell sharply, and heat2 remained rare outside the short f=0.50 trial.
8. Setpoint delivery/unconfirmed alert noise fell materially.

## What Is Worse

1. VPD remains the weak axis. Pinched VPD compliance only moved from 51.0% to 53.7%.
2. Average VPD rose from 0.844 to 0.924 kPa. The house is drier overall.
3. P95 VPD target error worsened from 0.456 to 0.509 kPa, even though P95 corridor
   miss improved.
4. Vent/fan runtime and cycles rose materially.
5. Fog starts rose from 52.5/day to 69.5/day.
6. Zone gradients remain large even after improvement: p95 post temp spread is
   9.54 F and p95 post VPD spread is 2.095 kPa.
7. The `b7a531b` period has more VENTILATE time and worse both-axis compliance than
   the `dcc6078` sub-window, likely from hotter/drier operating conditions and the
   later lighting behavior.

## What Looks Broken or Under-Specified

1. The controller still cannot fully reconcile hot/dry periods. VPD misses are
   correlated with vent duty and wet duty, which means the system is detecting the
   condition and acting, but the actuator mix does not close the gap reliably.
2. `band_track_fraction=0.50` appears too aggressive for the current physical plant.
   It caused a concentrated heating/heat2 episode on 2026-06-18. The move to 0.25
   was justified by the data.
3. Center mister cycling is too high to ignore. The architecture improved ordering,
   but it did not reduce center mister starts.
4. Wetting is associated with high zone VPD spread. That may be sensor placement,
   inadequate air mixing, localized mister effects, or expected transient physics,
   but it needs a targeted review.
5. The daily score surfaces do not yet make the pinched-control-corridor metric
   explicit enough. Existing daily compliance can look better or worse depending on
   whether the served corridor or pinched control corridor is used.

## Recommendations

1. Keep `band_track_fraction=0.25` as the current ceiling unless explicitly running
   a short, observed experiment. Do not return to 0.50 without a cycle/heat2 guard.
2. Run the ADR-0004 `band_track_fraction -> 0` float trial as a no-OTA experiment,
   but gate it with the same metrics here: both-axis compliance, VPD high stress,
   vent/fan/fog cycles, dew margin, and zone spread.
3. Add a daily pinched-corridor KPI beside served-band compliance:
   temp in pinched band, VPD in pinched band, both-axis pinched compliance,
   P95 pinched misses, and day/night splits.
4. Investigate the hot/dry ventilation tradeoff. The candidate hypothesis is that
   cooling priority is correct for temperature but creates dry-side VPD debt faster
   than fog/misters can repay during vented periods.
5. Add mechanical lifecycle budgets to the scorecard: vent/fan/fog/mister starts
   per day and peak transitions per hour. The current cycle rates are manageable
   only if they are tracked.
6. Treat zone-gradient reduction as a separate control/hardware issue. The p95
   spreads are still large enough to matter horticulturally.
7. Separate future analysis of the 2026-06-20 `b7a531b` lighting firmware from
   this climate-band analysis. It changes light runtime/cycling and can confound
   DLI and energy conclusions.
8. Clear or explain the remaining open planner alerts so autonomy-health issues do
   not get mistaken for firmware behavior.

## Bottom Line

The rollout was real, measurable, and directionally successful. It improved
temperature control, night behavior, dew risk, fog-first escalation, and overall
two-axis corridor compliance. It did not solve VPD. The remaining failure mode is
not a lack of action; it is an actuator-physics and arbitration problem during
hot/dry ventilated periods, with a real mechanical-cycle cost. The next safest
move is to keep `f=0.25` stable or deliberately test `f=0` under ADR-0004, while
making pinched-corridor compliance and actuator cycle budgets first-class daily
metrics.
