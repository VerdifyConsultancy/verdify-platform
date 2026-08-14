# Planner efficacy, second pass: current-firmware epoch

- **Firmware:** `2026.7.10.1500.09ee886`
- **Firmware source:** `09ee886f1a6fbd6452064460e1a57e5dc1399a70`
- **First device readback:** 2026-07-10 21:03:12.991915 UTC
- **Outcome cutoff:** 2026-08-14 06:00 UTC (midnight America/Denver)
- **Complete-day window:** Denver-local July 11 through August 13, 34 days
- **Method:** exact-epoch extraction, open-loop controller replay, rejected
  held-out closed-loop models, as-of forecast response audit, and a matched
  analysis of an unplanned stale-policy interval, followed by prospective
  switchback screening and full-stack experiment design
- **First pass:** [planner efficacy audit](planner-efficacy-audit-2026-08-14.md)
- **Reproduction package:**
  [`research/planner-efficacy/`](../../research/planner-efficacy/README.md)
- **Core result:**
  [`results-current-firmware-core-2026-08-14.json`](../../research/planner-efficacy/results-current-firmware-core-2026-08-14.json)
- **Mechanism/interruption result:**
  [`results-current-firmware-supplement-2026-08-14.json`](../../research/planner-efficacy/results-current-firmware-supplement-2026-08-14.json)

## Executive answer

Bounding every outcome and model row to the current firmware does **not** turn
the historical record into proof that AI caused resource, runtime, climate, or
crop benefit. The physical PID counterfactual still fails its declared model
gates, whole-equipment energy remains scoring-ineligible, and no randomized
AI-versus-frozen assignment exists.

This pass does, however, identify a substantially better place to look for net
positive AI value.

During four complete days when fresh planner and dispatcher deliveries stopped,
the ESP32 kept controlling with the last confirmed policy vector. In 93
same-time-of-day, tightly weather-matched 15-minute intervals, the stale-policy
period had:

- **0.3079 kPa more VPD distance outside the corridor**; the direction was
  unfavorable on all four stale days;
- **35.9% more six-core actuator demand**, equivalent to matched fresh periods
  using 26.4% less; and
- **42.1% more nine-climate-actuator demand**, equivalent to matched fresh
  periods using 29.6% less.

This is the first same-firmware signal in the record where climate quality and
actuator demand move in a jointly favorable direction. It is **suggestive, not
causal**: only 93 of 384 stale intervals had strict common support, the four
days were sequential rather than randomized, device state carried over, and
the interruption affected both AI and deterministic delivery. It is not a
no-AI arm and it is not a savings estimate. Its proper use is to prioritize a
randomized **fresh adaptive policy versus versioned Frozen-FSM** switchback.

Two mechanism findings make that experiment plausible:

1. Across 70 plans with at least 20 archived as-of forecast hours, planned
   cooling and wetting posture changed coherently with future heat/VPD, even
   after linear adjustment for current conditions and time of day.
2. Effective readbacks combined earlier hot/dry response with a lower water
   ceiling: the daily mister budget averaged 222.4 gal versus the 300 gal
   compiled default, while mist/fog thresholds and delays were generally more
   responsive than defaults.

Neither mechanism proves outcomes. They show that the planner is doing
meaningful, bounded adaptation and identify the VPD-versus-runtime frontier as
the highest-value causal test.

### Decision table

| Question | Current-firmware answer |
|---|---|
| Does the current epoch prove net-positive AI benefit? | **No.** The strongest result is a favorable but non-randomized stale-versus-fresh signal. |
| Where is the strongest benefit hypothesis? | **Hot/dry VPD control:** fresh adaptation may reduce both VPD miss and actuator demand versus leaving a formerly valid vector stale. |
| Does the planner actually anticipate future conditions? | **Yes as a mechanism, not an outcome:** as-of forecast maxima remain associated with planned posture after adjustment for current state and local hour. |
| Does the PID result change? | **No in interpretation.** All 12 audit PIDs request 64.0–137.6% more open-loop duty, but both plant models fail, so physical climate/resource effects remain not estimable. |
| What comparator isolates AI? | The same firmware, bands, safety, hardware, and delivery with a named, versioned baseline/Frozen-FSM vector—not a new PID controller. |
| What should be fixed first? | Forecast calibration/intent semantics, atomic vector lineage, delivery, objective scoring, plan horizon, and a byte-identical firmware twin. |
| Can the controlled study start on today's stack? | **No.** Per-parameter policy selection/delivery can create hybrid vectors and cannot prove arm exposure. Section 8 defines the required atomic path and A/A gate. |
| What can 30 days answer? | A balanced two-arm, 15-pair switchback can screen for a large VPD/runtime effect; it is underpowered for ordinary 10–20% runtime savings, so a null is inconclusive and informs a separately preregistered new trial. |

## 1. Exact epoch and admissible window

### 1.1 Firmware identity

The current device-reported build is `2026.7.10.1500.09ee886`. The preceding
build's last readback was 2026-07-10 21:02:12.510610 UTC and the current build's
first was 21:03:12.991915 UTC, placing the flash transition inside a 60.48-second
interval. The release handoff records candidate binary SHA-256
`4c412460b19472c94a1dbb01fa5fb7c629aa05aa3cdde7a6ace5b1b35ecef65d`.
Production telemetry verifies the version string, not a hash read back from
flash; the hash is release-chain evidence.

Complete Denver-local days wholly after first readback map to UTC
`[2026-07-11 06:00, 2026-08-14 06:00)`. This is the primary factual window.
The core model uses its first 20 days for training and its last 14 days for
held-out evaluation; no row from another firmware build enters either set.

### 1.2 Same-build interruptions

Same firmware does not mean every surrounding component was static.

| Time | Event | Treatment in this audit |
|---|---|---|
| Jul 11 19:55–20:56 UTC | Planner, ingestor, MCP, setpoint-server, and Hermes service roll | Report the 34-day factual window; exclude Jul 11 in a matching sensitivity. |
| Jul 25 09:06 UTC | ESP32 Guru/Panic reboot | Exclude the full local day from matched controls. |
| Jul 25 09:06–09:09 UTC | Brief cold-default interval before dispatcher reassertion | Covered by the full-day exclusion. |
| Aug 5–11 | Planner/gateway and device-delivery interruption | Analyze Aug 6–9 as the stale-policy hypothesis period; exclude onset/recovery days. |
| Aug 11 01:35 UTC | Nine-minute telemetry gap | Not in the four complete stale days and not evidence of hardware replacement. |

No persistent crop-band, sensor-registry, equipment, greenhouse-sensor, or
forecast-action-rule change occurred during the epoch. Crop-band anchors were
last updated July 3. The current firmware stayed active after the cutoff.

## 2. What happened under this firmware

The bounded extraction contains 3,264/3,264 expected 15-minute climate bins,
48,630 raw climate samples, 16,507 equipment transition/seed rows, 34 daily outcomes,
and 84 plans. Core climate/weather/target fields are present in every 15-minute
bin; only two wind observations required interpolation.

| Factual endpoint | 34-day result |
|---|---:|
| Attributable compliance | 65.94% |
| Temperature graded compliance | 49.16% |
| VPD graded compliance | 40.71% |
| Graded stress | 779.88 axis-hours |
| Six-core runtime | 75,551.9 device-minutes |
| Nine climate-device runtime | 79,333.2 device-minutes |
| Six-core relay cycles | 5,010 |
| Plans | 84; all validated, anchored, trigger-linked, and ClimateIntent-bearing |
| Structured hypotheses | 77/84 |
| Metered water | 9,029 gal; 27/34 meter days eligible |
| Water-attribution eligibility | 25/34 days |
| Partial two-channel electricity | 255.32 kWh; eligible only for that named scope |
| Whole controlled-equipment model | 517.269 kWh; 0/34 scoring-eligible |

The local planner's self-score remains unusable as efficacy evidence. Mean
self-score was 4.98 versus a deterministic anchor mean of 2.69, a +2.29 bias;
only 45/84 were within two points. Guardrail penalty was exactly 3 for 82/84
plans and 0 for two, which is saturation rather than a useful learning signal.

## 3. Exact-firmware PID rerun

### 3.1 Design

The rerun uses only the bounded current-firmware files:

- training: UTC `[2026-07-11 06:00, 2026-07-31 06:00)`, 20 complete days;
- evaluation: UTC `[2026-07-31 06:00, 2026-08-14 06:00)`, 14 complete days;
- the same 12 prespecified target/gain combinations, coordinated allocator,
  anti-windup, and safety rules as the first pass; and
- no cross-era historical matching.

The model split is chronological. Every held-out day begins at its factual
initial state; future indoor state is never injected into a PID rollout.

### 3.2 Open-loop decision replay

| Policy family | Requested climate-device minutes/day | Relative to executed |
|---|---:|---:|
| Executed operation | 2,173.9 | — |
| Least-demanding audit PID | 3,564.6 | +64.0% |
| Most-demanding audit PID | 5,165.2 | +137.6% |

Equivalently, executed demand was 39.0–57.9% below these PID requests on the
same factual state trace. This remains a valid decision-demand result and an
invalid physical-outcome claim: the PID actions do not change the next factual
state in open-loop replay.

### 3.3 Closed-loop result is still rejected

| Gate | Ridge ARX | Nonlinear HGB | Required |
|---|---:|---:|---:|
| Recursive temperature MAE | 2.709°F | 1.841°F | ≤2.5°F |
| Recursive VPD MAE | 0.281 kPa | 0.262 kPa | ≤0.25 kPa |
| PID state-action support | 90.1% | 83.7% | ≥90% |
| Residual gate | Pass | Pass | Pass |
| Overall | **Fail** | **Fail** | Both models pass |

The exact-epoch restriction improves some model diagnostics but does not clear
the declared gates. `counterfactual_eligible` remains false. Rejected model
outputs are not resource or climate estimates.

The more relevant AI comparator is Frozen-FSM, but a complete Frozen-FSM
re-execution was not possible with the current replay stack. At the exact
firmware source, the replay override path wires only
`fog_escalation_kpa` and `cool_stage2_over_high_f` among the posture controls
summarized in Section 5; it does not cover the mister thresholds, pulse
timings, fog dwell values, or water budget. Production climate decisions also
depend on firmware/ESPHome sequencing and an effective vector assembled from
AI, deterministic preemptive rules, one-shots, clamps, bands, and retained
values. The Python intent comparator does not implement the deployed
firmware's exact normalized leading-axis arbitration. Therefore this pass
executed only the 12-policy PID decision replay and the rejected response-model
counterfactual—not a Frozen-FSM ablation. A byte-identical effective-vector
twin and explicit assignment lineage are prerequisites to that comparison.

## 4. Strongest positive investigation: fresh versus stale policy

### 4.1 What the interruption created

The last pre-interruption journal plan was created August 5 at 06:19 UTC and
expired August 8 at 06:00 UTC. No new journal plan landed on Denver-local
August 6–10. No non-ESP32 setpoint request confirmed on the four complete local
days August 6–9. Dispatcher/band confirmations resumed August 11 at 02:21 UTC;
fresh AI plan commands resumed at 12:16 UTC.

The stale interval is not a clean “AI off” treatment. The ESP32 continued its
deterministic loop with retained settings; the last AI vector carried over,
then expired in the journal; default/band deliveries also failed; and
deterministic forecast logic remained part of the system. The contrast is
therefore named **fresh adaptation versus stale last-confirmed policy during a
shared delivery interruption**.

### 4.2 Fixed exploratory matching specification

Each of 384 stale 15-minute bins was compared with a control bin at the exact
same Denver quarter-hour. Candidate controls were the other complete
current-firmware days, excluding July 25 and the August 5/10/11 transition
days. Matching used nearest Euclidean distance on control-standardized outdoor
temperature, outdoor RH, solar, and wind, followed by a strict 0.35-SD
per-axis caliper.

- 93 pairs survived, representing 23.25 hours or 24.2% of the stale period.
- They used 85 unique control bins; maximum reuse was two.
- Retained counts by stale day were 28, 18, 42, and 5.
- All selected weather values were raw measured values.
- Post-match standardized differences were −0.101 temperature, −0.073 RH,
  −0.038 solar, and −0.004 wind.

No minute-level p-value is reported. Four sequential days, not 93 bins, are the
largest defensible temporal unit, and even those days are not randomized.

### 4.3 Result

| Endpoint per 15-min bin | Stale | Matched fresh | Stale minus fresh | Direction across four days |
|---|---:|---:|---:|---|
| Temperature distance outside corridor | 0.4443°F | 0.3269°F | +0.1174°F | Mixed |
| VPD distance outside corridor | 0.3584 kPa | 0.0505 kPa | **+0.3079 kPa** | **Stale worse 4/4** |
| Six-core device-minutes | 31.37 | 23.08 | **+8.29 (+35.9%)** | Stale higher 3/4 |
| Nine-climate device-minutes | 34.03 | 23.94 | **+10.08 (+42.1%)** | Stale higher 3/4 |
| Wet-device minutes | 4.84 | 3.77 | +1.07 | Mixed; aggregate driven by one day |

The VPD and aggregate-runtime directions survive two stricter control pools:

| Sensitivity | Pairs | Stale-minus-fresh VPD | Six-core device-min | Nine-device min |
|---|---:|---:|---:|---:|
| Exclude Jul 11 service-roll day | 81 | +0.3604 kPa | +9.57 | +11.59 |
| Use only Jul 15 onward, when forecast archive exists | 73 | +0.3654 kPa | +7.00 | +8.46 |

### 4.4 Interpretation

This is stronger than a whole-history before/after comparison because firmware,
crop bands, hardware, local time, and measured weather support are held much
closer. It is still not a causal AI effect because:

- interruption assignment followed an operational failure;
- only 24.2% of stale bins have strict common support;
- retained settings and greenhouse moisture/thermal state carry over;
- fresh controls come from different sequential days;
- AI, band/default delivery, and gateway health failed together; and
- temperature and wet-device directions are not stable by day.

The correct conclusion is a prioritized hypothesis: **fresh policy adaptation
may move the VPD-runtime frontier favorably versus letting a policy go stale**.
The next experiment should deliberately reproduce the policy contrast while
holding delivery, defaults, and assignment observable.

## 5. Mechanism evidence: forecast-responsive posture

The forecast-response export selects, for each plan, only forecast vintages
fetched at or before plan creation. Seventy plans have at least 20 target hours
inside the next 24 hours. Planned values are unweighted means across waypoints
scheduled in that horizon, not duration-weighted policy means. The table
reports the ordinary correlation and a partial correlation after linear
adjustment for current indoor temperature, indoor VPD, solar, outdoor
temperature, and local hour.

| Mean across waypoints scheduled in next 24 h | Future max input | Pearson | Partial correlation | Interpreted posture |
|---|---|---:|---:|---|
| Stage-2 offset above high | VPD | −0.540 | −0.480 | Earlier second-stage cooling |
| Mister pulse gap | VPD | −0.596 | −0.446 | Shorter recovery gaps |
| Mister pulse-on time | VPD | +0.604 | +0.493 | Longer wet pulses |
| Mister water budget | VPD | +0.683 | +0.573 | More water headroom on severe forecasts |
| Resource sensitivity | VPD | −0.607 | −0.581 | Relaxes conservation under severe load |
| Mister water budget | Solar | +0.562 | +0.589 | More headroom for bright forecasts |

This establishes coherent forecast response in proposed policy. It does not
establish that the forecast was accurate, that the proposal was fully executed,
or that the response improved outcomes.

Effective device readbacks show the resulting operating posture over all
47,956 sampled minutes:

| Parameter | Compiled default | Epoch mean | Time on more-responsive / resource-conserving side |
|---|---:|---:|---:|
| `cool_stage2_over_high_f` | 1.0°F | 0.786°F | 57.7% below default |
| `fog_escalation_kpa` | 0.4 kPa | 0.261 kPa | 75.4% below default |
| `mister_all_delay_s` | 300 s | 87.3 s | >99.9% below default |
| `mister_engage_kpa` | 1.6 kPa | 1.185 kPa | 99.96% below default |
| `mister_all_kpa` | 1.9 kPa | 1.360 kPa | 99.99% below default |
| `mister_pulse_gap_s` | 45 s | 37.6 s | 69.9% below default |
| `sw_cool_all_fans_at_high_enabled` | Off | 58.3% on | 58.3% enabled |
| `mister_water_budget_gal` | 300 gal | 222.4 gal | 57.5% below; never above |

This combination—earlier/stronger response under hot/dry load but a lower
total water ceiling—is a plausible efficiency strategy. The stale-period
signal makes it worth testing, but the current history cannot attribute its
outcomes to individual parameters.

## 6. Why benefit is diluted and difficult to identify

### 6.1 Delivery and mixed policy ownership

The epoch contains 288 independent expected trigger cycles. Of these, 217
closed acceptably (84 plans written, 27 one-shot tunables completed, 106
acknowledged) and 71 failed or missed: **75.3% acceptable, 24.7% failed/missed**.
For required full plans alone, 84/106 were written, or 79.2%. Attempt-log rows
greatly amplify failures through retries and must not be used as the denominator.

The firmware-interval action log contains 53,777 rows. Only 28,418 (52.8%) join
the 84 AI journal plans; 22,703 are labeled preemptive and 2,641 one-shot. The
deterministic forecast engine also wrote 554 preemptive setpoint rows across
230 IDs. `v_active_plan` resolves each parameter independently, while the action
logger labels the whole control tick with one recent row. A tick can therefore
execute a hybrid vector that cannot be reconstructed from its single `plan_id`.

This is not merely an analytics inconvenience. It means the production
treatment is not atomic.

### 6.2 Most future waypoints do not survive

The 84 plans contain 703 waypoints, each materializing all 40 parameters.
Six were scheduled after the outcome cutoff. Of 697 due by cutoff:

- 84 were first points already due when their plan was created;
- 156 future transitions arrived while their plan still governed; and
- 457 of 613 genuinely future transitions were superseded first: **74.6%**.

Within a plan, a mean 19.3 parameters changed (median 21; range 4–25). Long
multi-waypoint plans therefore spend model and delivery effort on changes that
usually never become current, while changing too many levers for attribution.

### 6.3 Future waypoints are compiled with stale context

ClimateIntent materialization fetches the current band/state once at plan
creation and uses it for later waypoints. Across the 613 genuinely future,
due waypoints, comparing materialized `mister_engage_kpa` with the band that
applies at waypoint time gives mean absolute mismatch 0.0619 kPa, p90 0.0857,
maximum 0.5508; 247/613 exceed 0.05 kPa. Semantic intent should be compiled
against the effective band and state at execution time, not frozen against
creation-time context.

### 6.4 Intent fields are saturated, one-sided, or inert

Of 703 waypoint intents:

- 701 set positive `thermal_lead_time_min`, but the field is audit context and
  has no timing path;
- 525 set negative `forecast_vpd_bias_kpa`, including 446 exactly at −0.4;
- the materializer turns negative forecast pressure into zero, so these values
  cannot express anticipatory opposite-direction control;
- all 703 materialized `night_vpd_bias_kpa=0`, despite it being the documented
  overnight dryout lever; and
- 215 set negative temperature bias, which is also one-sided in materialization.

Every AI-visible semantic field should either alter a named effective-vector
component with a two-sided, testable mapping or be removed from the planner's
decision surface.

## 7. Architecture and planner changes most likely to improve net value

### P0 — repair forecast calibration and the semantic contract

The production `v_forecast_accuracy` family compares Open-Meteo **outdoor** VPD
with house **indoor** VPD, then the planner context subtracts that quantity as
forecast bias. On the archived current-epoch vintages:

| Lead | Current indoor-reference bias / MAE | Correct outdoor-reference bias / MAE |
|---|---:|---:|
| 0–6 h | +1.453 / 1.573 kPa | +0.539 / 0.676 kPa |
| 6–24 h | +1.628 / 1.705 kPa | +0.656 / 0.766 kPa |
| 24–48 h | +1.690 / 1.743 kPa | +0.666 / 0.816 kPa |

Fix the comparison to outdoor forecast versus outdoor truth, select one
as-of vintage per target/lead, and report residual distributions and coverage
by lead/regime. Then model outdoor-to-indoor response separately. Corrected
forecast should be a planner input, not an AI-authored “bias” output. Add a
contract test requiring every emitted intent field to change an effective
component or be explicitly audit-only.

Expected impact: less saturated negative VPD intent, less false conservation,
and usable forecast uncertainty for hot/dry pre-staging.

### P0 — make one atomic effective policy vector

Create one arbiter that accepts AI, deterministic forecast, operator one-shot,
band, and guardrail proposals and emits one immutable versioned vector. Persist:

- assignment ID and arm;
- full vector hash and validity interval;
- per-component producer, plan, and proposal;
- firmware/compiler/prompt/forecast-vintage/anchor revisions; and
- requested, admitted, sent, confirmed, and device-readback times.

Make the forecast engine an upstream proposer, not a parallel writer. Use a
durable idempotent outbox and confirm the vector only after device readback.
Every action tick should join exactly one reconstructable vector version.

Expected impact: eliminates hybrid-policy ambiguity, makes outages safe and
interpretable, and unlocks intention-to-treat and per-protocol measurement.

### P0 — replace self-grade and saturated clamp counting

Do not use AI self-score as reward. Score one plan-attributable transition once,
using deterministic climate severity and only eligible resource/churn evidence.
The current score joins clamp rows by governed time rather than exact plan and
counts repeated holds, saturating the penalty. Persist pre-action predicted
outcomes and uncertainty, then calibrate predictions against blinded outcomes.

Expected impact: directs learning toward measured climate/resource tradeoffs
instead of repeated polling volume or optimistic narrative grades.

### P1 — shorten horizon and emit sparse deltas

Use a versioned neutral/fallback vector plus short tactical deltas through the
next expected replanning boundary. Sunrise may establish a daily baseline;
sunset, midnight, and checkpoint triggers should acknowledge/no-op unless a
forecast/state delta crosses a declared threshold. Compile the delta at
execution time against current band/state.

Validate offline that the rule removes at least half of model/delivery calls
without worsening replayed climate/safety endpoints. Measure future-waypoint
utilization and vector churn directly.

Expected impact: fewer wasted calls, fewer failed writes, less stale context,
and cleaner one-lever attribution.

### P1 — establish a byte-identical twin and uncertainty-aware response model

Use compiled production C++ as the sole decision oracle and require shadow
action agreement before trusting counterfactuals. Learn conservative
action-response coefficients conditional on current errors, outdoor
temperature/absolute humidity, solar, wind, effective vector, and regime.
Publish out-of-distribution and stale-input fallbacks. Select resource-efficient
candidates only among actions climate-noninferior within uncertainty.

Expected impact: turns Frozen-FSM and targeted lever comparisons into safe,
closed-loop estimates rather than Python approximations.

### Inputs worth adding or qualifying

- corrected as-of forecast distributions, not only point maxima;
- outdoor absolute humidity/dewpoint and a learned outdoor-to-indoor transfer;
- effective vector/hash and device-side readback at action cadence;
- validity-versioned crop bands and mechanical/configuration epochs;
- canopy/leaf temperature, PAR/DLI, and root/substrate wetness if they can be
  made reliable;
- whole-scope electricity and gas measurement, or explicit device-level scopes;
- inference model, tokens, latency, retries, energy, and cost; and
- lesson validity by firmware/vector/regime, support count, effect interval,
  and expiry. High-confidence lessons should require replicated prospective
  evidence rather than narrative promotion.

## 8. Proposed next phase: a controlled 30-day planner experiment

### 8.1 Question, unit of randomization, and readiness verdict

The prospective pilot decision question is:

> Does assignment to forecast-aware AI tunables reduce aggregate command duty
> across all nine climate actuators while preserving VPD and temperature
> control, relative to a strong fixed tunable vector, when firmware, crop bands,
> safety logic, hardware, metering, and delivery are otherwise identical?

This is one greenhouse and one coupled air mass. Zones, sensors, actions, and
15-minute rows are repeated measurements—not independent experimental units.
The unit of randomization must therefore be a Denver-local day. A paired
switchback is appropriate for a single unit observed over time, but its block
length and analysis must account for treatment carryover; that is a central
result of the formal switchback literature ([Bojinov, Simchi-Levi, and Zhao,
2023](https://doi.org/10.1287/mnsc.2022.4583)).

The current platform is **not ready to arm this experiment**. It can schedule
one, but it cannot prove treatment exposure:

- [`v_active_plan`](../../db/migrations/196-planner-terminal-lifecycle.sql)
  chooses a latest row independently for each parameter, so a control tick can
  combine AI, one-shot, and deterministic-forecast policy;
- the [dispatcher](../../ingestor/tasks/dispatcher.py) persists and delivers
  individual `setpoint_changes`, while firmware setters mutate live globals as
  each value arrives; and
- [`climate_action_log`](../../db/migrations/142-climate-action-log.sql) is
  populated by an [ingestor heuristic](../../ingestor/ingestor.py) that labels
  the whole tick with one inferred recent plan rather than a device-confirmed
  complete policy generation.

That gap is material, not theoretical. In this epoch only 28,418 of 53,777
action rows (52.8%) joined a full AI plan, 22,703 were labeled preemptive, and
84 of 106 required full-plan cycles wrote a plan. The platform and firmware
changes in Sections 8.6–8.9 are therefore prerequisites, not optional cleanup.

### 8.2 Two physical arms; other policies remain shadow-only

Thirty days provides only 15 adjacent-day contrasts. A physical three-arm
trial would dilute that to about ten days per arm and add a multiplicity
problem. Use two physical arms and run the deterministic forecast comparator
in shadow:

| Arm | Physical behavior | Locked contract |
|---|---|---|
| **A — Frozen-FSM** | The current deterministic ESP32 FSM with one reviewed, immutable effective policy vector. | Derive the baseline from stable same-firmware readbacks, quantize it with the new canonical wire schema, safety-review it, replay it through compiled firmware, and pin its canonical hash. It is not boot defaults, `ClimateIntent()`, or the last AI vector. |
| **B — AI planner** | The same FSM with admitted planner-derived tunables and normal within-day event-triggered replanning. | Every proposal uses the same registry, compiler, arbiter, delivery, bounds, and firmware safety path as A. A planner or delivery failure falls back to A but remains a B assignment for intention-to-treat. |
| **Shadow — deterministic forecast** | Produce, compile, and score a deterministic proposal on every eligible trigger without actuation. | Join it to the physical proposal by assignment, trigger, as-of forecast vintage, compiler, and vector schema. |

The canonical transmitted vector should contain all **49 live values in
`PLANNER_PUSHABLE_REG`** in
[`tunable_registry.py`](../../verdify_schemas/tunable_registry.py), in a fixed
wire schema. The current MCP path materializes and persists all 40 required
Tier-1 components; the other nine—`min_fan_on_s`, `min_fan_off_s`,
`min_heat_on_s`,
`min_heat_off_s`, `min_vent_on_s`, `min_vent_off_s`,
`mister_center_penalty`, `mister_min_off_s`, and
`sw_direct_wet_gate_enabled`—must be filled from the baseline and remain
byte-identical in both arms. For the first trial, narrow the fields that may
differ between A and B to the hot/dry mechanism supported by this study:

```text
cool_stage2_over_high_f
sw_cool_all_fans_at_high_enabled
fog_escalation_kpa
min_fog_on_s
min_fog_off_s
mister_engage_kpa
mister_all_kpa
mister_all_delay_s
mister_pulse_gap_s
mister_pulse_on_s
mister_water_budget_gal
```

The first trial must also freeze exactly two reviewed 49-field AI templates—a
moderate and an aggressive hot/dry response—whose differences from baseline are
confined to those 11 fields. The planner may select and switch between those
two complete templates from current as-of context; it may not synthesize an
untested intermediate vector. The arbiter admits only an exact template content
hash and only the transition edges qualified in Section 8.3. The remaining 38
policy values, crop-band schedule, safety rails, lighting, irrigation, and
hardware state are common. This estimates the value of a named **AI hot/dry
template-selection policy** rather than an unspecified 40-dimensional
treatment. The current planner changes 19.3 parameters per plan on average;
retaining that degree of freedom would make a 30-day result hard to interpret
and easier to break. A later protocol may widen the library only after its new
vectors and transitions pass the same qualification.

Construct Frozen-FSM without inspecting trial outcomes. For this report's
candidate baseline, use time-weighted medians of device-confirmed readbacks over
Denver days July 12–August 4, excluding the July 25 reboot day; use the modal
value for Booleans, then quantize with the new wire schema. A field without
qualified readback blocks baseline approval rather than silently taking a boot
default. Record the extraction query/input hashes and have horticultural,
firmware, and safety owners approve the complete vector after compiled replay,
HIL, and A/A—not after seeing A/B outcomes.

The planner should run on the same schedule in both arms. On A days its output
is retained as a blinded shadow proposal but cannot reach the outbox. This
separates planner availability and compute cost from physical admission and
prevents an operator from inferring the arm from whether the planner ran. Freeze
the lesson/retrieval corpus, policy/model weights, and response-model snapshot
at protocol lock. Current as-of sensor and forecast context remains an allowed
input under that frozen contract; trial outcomes, new lessons, model updates,
and retrieval-corpus promotions go to a quarantined candidate store and cannot
alter policy generation during the experiment.

### 8.3 Locked 15-pair assignment and carryover protocol

Arrange 30 consecutive local days as 15 adjacent two-day pairs. Independently
assign each pair to blinded order `XY` or `YX` with probability 0.5, then use
one committed secret mapping to resolve `X/Y` to physical `A/B`. This guarantees
15 days per physical arm and permits no run longer than two days. Before day 1,
commit:

- the protocol, future beacon round, byte decoder, and generator source SHA;
- all 30 half-open UTC assignment ranges and pair IDs;
- the blinded schedule hash and domain-separated mapping-secret commitment; and
- firmware binary, baseline vector, registry, compiler, planner/prompt/model,
  lesson/context and response-model snapshots, crop-band, schedule, safety,
  meter, and mechanical revisions.

Do not rerandomize or replace a day because of weather, staffing, delivery,
safety preemption, or an unfavorable outcome. At protocol lock, name a future
public-randomness-beacon round, then generate a 32-byte mapping secret with a
witnessed operating-system CSPRNG and record its commitment **before** that
beacon round is published. Use the beacon only to choose the order of blinded
labels `X` and `Y`. Only the assignment service may read that secret before
analysis lock; analysts and dashboards see only `X/Y`, while the LLM and
ordinary application roles receive only an opaque assignment receipt. None
receives the physical arm. Safety operators cannot be behaviorally blinded,
so emergency actions remain visible and audited, but they may not change the
schedule. The assignment service—not the LLM or heartbeat—owns activation.

Make both derivations byte-specific in the protocol. Encode `study_id` as
Unicode NFC UTF-8 and number pairs (j=0,\ldots,14). For each pair compute
`HMAC-SHA256(key=beacon_bytes,
ASCII("verdify-switchback-order-v1") || 0x00 || UTF8_NFC(study_id) || 0x00 ||
uint32_be(j))`; `(digest[31] & 0x01) == 0` means `XY`, and `1` means `YX`.
Resolve the physical mapping with
`HMAC-SHA256(key=mapping_secret,
ASCII("verdify-switchback-arm-map-v1") || 0x00 || UTF8_NFC(study_id))`;
`mapping_bit = mapping_digest[31] & 0x01`; bit `0` means `X=A/Frozen` and
`Y=B/AI`, while bit `1` reverses it.
Before day 1, publish
`SHA256(ASCII("verdify-switchback-map-commit-v1") || 0x00 ||
UTF8_NFC(study_id) || 0x00 || mapping_secret)`, the beacon identity and raw-byte
hash, generator/version SHA, and
`SHA256(UTF8(RFC8785(blinded_assignment_json)))`.
Derive each assignment UUIDv5 using the protocol's fixed namespace UUID and
name bytes `UTF8_NFC(study_id) || 0x00 || ASCII(YYYY-MM-DD)`. The restricted
assignment table resolves `X/Y` to `A/B`; analysis views expose only `X/Y` until
the frozen analysis output is hashed and signed. Then reveal `mapping_secret`
and regenerate the published commitment, mapping, every UUID, UTC boundary,
pair, and blinded label byte-for-byte.

Pre-stage the assigned vector, then activate it atomically at `00:00:00
America/Denver`. The proposed `00:00–02:00` exclusion leaves 88 expected
15-minute bins, but two hours is admitted only by a locked pretrial step test.
Before schedule generation, freeze the baseline plus the two HIL-approved AI
templates from Section 8.2, with the other 38 fields byte-identical to baseline.
Record their hashes and exercise every **content-changing** directed transition
among the three vectors—baseline↔moderate, baseline↔aggressive, and
moderate↔aggressive—with exactly four analyzed transitions per edge in each of four
mutually exclusive regimes: night (`solar < 20 W/m²`), hot/bright-dry
(`solar >= 400 W/m²`, outdoor temperature `>= 80°F`, outdoor humidity ratio
`<= 0.012 kg/kg`), hot/bright-humid (the same solar/temperature thresholds and
humidity ratio `> 0.012 kg/kg`), and all other daylight. Each transition
requires fresh inputs, no manual/safety override, byte-identical source-policy
content/template and manifest throughout a gap-free 60-minute pre-step trace,
and six post-step hours. That content continuity may span permitted
`identity_hold` assignments, but every activation/generation/readback must still
match its own assignment exactly; a gap, wrong hash, or manifest change restarts
the pretrace.

In a separately hashed pretrial qualification specification, freeze the 24
edge/regime cell queues, four ordered target slots per cell, the deterministic
FIFO scheduler, all pre-step eligibility predicates, and a 45-local-day
qualification window, plus the firmware, baseline/templates, compiler,
crop-band, safety, sensor, and mechanical revisions that the trial would use.
The specification deliberately does **not** invent 96 exact UTC ranges before
future weather and telemetry exist. When a locked cell is next in its queue and
its eligibility predicate is true, one advisory-locked SQL transition records
the pre-step evidence, allocates that slot, creates and locks its exact half-open
UTC assignment, and commits it before any outbox row or physical activation.
The assignment can never be replaced. Any such revision change voids the
qualification and requires a new specification and complete rerun.

Every physical content-changing move used to position a source vector or return
to baseline is also authorized by the same six-edge graph. Before actuation,
the same advisory-locked function creates an immutable, exact-range
non-analysis assignment and transition-ledger row with `slot_id = NULL`, the
locked scheduler state, operation `positioning|baseline_recovery`, reason,
source/target hashes, and validity. The initial positioning assignment is the
specification's fixed three-hour interval: two hours of settling followed by
the required 60-minute source-stability pretrace. At its boundary, either
atomically claim an eligible analyzed slot or create the next locked 15-minute
same-content `identity_hold` assignment; repeat the deterministic hold cadence
until a cell qualifies or the calendar cutoff is reached. The eligibility
function evaluates source continuity across that chain by content/template and
manifest, not by the deliberately changing assignment-bound activation hash.
Thus every vector is covered by one non-overlapping assignment and no
indefinite or retroactively closed validity interval exists.

A positioning move may fill the next analyzed slot instead only if the locked
cell, regime, source-stability trace, and every other pre-step rule already pass;
that decision is made before the move. Otherwise it never enters the 96 or
becomes eligible post hoc. A safety event, delivery/readback failure,
unauthorized byte change, or missing mandatory hold/readback evidence during an
analyzed, positioning, recovery, or identity-hold assignment fails
qualification; those rows are never invisible operational traffic.

The first four eligible, atomically claimed starts in each
direction/vector/regime cell are the fixed analysis set. Once an analyzed step
starts, its slow response, safety event, delivery failure, or missing post-step
data counts as a failed cell result and is never replaced. Stop starting
analyzed transitions after a cell reaches four. If all 96 required transitions
(four in each of 24 edge/regime cells) are not complete by the calendar cutoff,
or any required trace is not analyzable, daily switching is not qualified; do
not extend or selectively resample the step study. The final qualification
result commits the ordered 96-assignment manifest and its hash.

That qualification specification must freeze the disturbance-adjusted first-order response model,
fit code, input columns, and diagnostics before those tests. For indoor VPD,
indoor temperature, and nine-device duty, define each transition's settling
time as the first 15-minute boundary after which the fitted transient remains
within `0.025 kPa`, `0.25°F`, and `6.75 device-minutes per bin`, respectively,
of its post-step asymptote through hour six. The gate statistic is the maximum
settling time over all three endpoints and all 96 fixed transitions—not a mean,
selected quantile, or confidence bound on unobserved conditions. Two hours is
valid only if that maximum is `<= 2 h` and policy identity itself is confirmed
within 120 seconds in every transition. Any maximum over two hours fails this
version: author a new protocol and parameterize its analyzer, endpoint window,
expected-bin/completeness rules, PP denominator, assignment boundaries, and
power artifact before naming another beacon round. A value over six hours also
requires multi-day rather than daily blocks. An unqualified cell invalidates
daily switching. The qualification specification must also name every
response-model diagnostic statistic and numeric pass/fail threshold before
collecting the step tests; failing any locked check has the same result. This is
a fixed-N engineering coverage gate, not a population settling guarantee.
Replace version 1 with a separately sized multi-day protocol rather than
waiving a failed gate.

Version 1 must also use 30 local days that do not cross a Denver UTC-offset/DST
transition; otherwise 88 bins and a two-hour wall-clock washout no longer mean
the same elapsed exposure on every day. If scheduling must cross a transition,
define elapsed-time windows and day-specific expected-bin counts in a new
protocol version and rerun sizing before randomization.

Report zero-, locked-, and six-hour washout sensitivities plus the previous
day's assignment interaction. With only 15 pairs, the lag-one correlation and
previous-arm coefficient are prespecified diagnostics, not post-hoc gates or a
license to change the model; publish them and their intervals regardless of
direction. Device evidence of the prior vector after the locked washout is an
integrity failure. Crop yield, quality, disease, and biomass are exploratory:
their response extends beyond a daily switchback. Published
autonomous-greenhouse demonstrations that measured
net profit, yield, and resource efficiency used separate compartments and
multi-month crop cycles, not one 30-day air mass ([Hemming et al.,
2020](https://pmc.ncbi.nlm.nih.gov/articles/PMC7698269/)).

### 8.4 Outcomes, estimands, and decision rule

Aggregate raw climate samples into Denver-independent UTC half-open buckets
`[bucket,bucket+15 minutes)`. For each variable, (x_i) is the arithmetic mean
of its finite raw samples; require at least 12 samples and never interpolate an
indoor value. Resolve the common validity-versioned evaluation corridor
([L_i,U_i]) at the bucket-start timestamp, matching the checked extractor. Every
eligible bin has fixed weight (w_i=0.25) hour; sample count does not change its
weight. Define distance outside the corridor as:

\[
e_i = \max(L_i-x_i,\ 0,\ x_i-U_i), \qquad
Y_d = \frac{\sum_i w_i e_i}{\sum_i w_i}.
\]

Apply that separately to VPD and temperature. Integrate observed relay state
for six-core and nine-device runtime. These are **device-minutes**, not kWh,
dollars, carbon, delivered airflow, heat, or water. The nine-device endpoint is
the more complete command-duty proxy because the proposed policy can trade fog
and fan duty against the three misters.

| Role | Daily lower-is-better endpoint | Locked decision |
|---|---|---|
| Co-primary climate gate | Mean VPD distance outside the common corridor | Upper one-sided 97.5% bound for AI minus Frozen `< +0.05 kPa` |
| Co-primary climate gate | Mean temperature distance outside the common corridor | Upper one-sided 97.5% bound `< +0.50°F` |
| Co-primary engineering benefit | Nine-device minutes: heat1, heat2, vent, fan1, fan2, fog, and south/west/center misters | Upper one-sided 97.5% bound `< 0` |
| Secondary | Six-core and wet-device minutes, cycles, short cycles, controller-miss and physically-unachievable minutes | Effect and interval; no multiplicity-free claim |
| Secondary, availability-gated | Qualified climate water, scoped measured electricity, gas | No imputation; retain scope and quality flags |
| Safety | `SENSOR_FAULT`, `SAFETY_HEAT`, `SAFETY_COOL`, hard interlocks, emergency override | Operational stop gate and descriptive arm counts |
| Exploratory | Planner latency, retries, tokens, compute energy/cost, crop observations | Required before an economic or crop-benefit claim |

The two noninferiority margins are engineering proposals—11.6% of the current
0.43-kPa VPD corridor width and 5% of the 10°F temperature corridor—but they
are also approximately 50% and 98%, respectively, of the exact-firmware
historical mean distance-outside-corridor endpoints. They are therefore
permissive relative to the achieved average miss, not crop-specific biological
thresholds. A horticultural and safety owner must approve them before protocol
lock. Tomato experiments do support VPD as a physiologically meaningful
variable, including effects on plant water status, transpiration, canopy
temperature, biomass, and fruit production, but they do not validate these
exact margins or this crop mix ([Zhang et al.,
2017](https://www.nature.com/articles/srep43461)).

The primary estimand is intention-to-treat (ITT): the average effect over these
30 realized days of assignment to the complete operational AI strategy rather
than Frozen-FSM. Planner timeouts, delivery failures, automatic fallback,
safety preemption, and emergency rescue remain in ITT because they are part of
system efficacy. The supportive per-protocol set is fixed as follows. For each
day, the denominator is exactly 79,200 post-washout seconds in `[02:00,24:00)`.
For A, the numerator is the union duration whose device echo resolves to the
assignment-bound Frozen baseline content. For B, it is the union duration whose
echo exactly matches the then-current arbiter-admitted vector for that
assignment, one of the two template hashes, schema/revisions, validity, and a
lineage that entered through either a passing-qualified content-changing edge
or a manifest-permitted assignment-bound `identity_rebind`; normal admitted
intraday generations therefore qualify. Gaps, baseline fallback on B, rejected
or shadow proposals, and override intervals contribute zero. A day requires at
least 75,240 seconds (95%), no parallel writer, and no manual override; a pair
enters the supportive estimate only when both days qualify. Reuse the same
physical-arm contrast on the `m_PP` qualifying pairs; if `m_PP >= 2`, report its
paired t interval with `m_PP - 1` degrees of freedom; otherwise list any
qualifying pair without an interval. It can never change the ITT advancement
decision. No pair is replaced and
all exclusions are published. This set is post-randomization and is not
causally protected like ITT.

After the frozen analysis is unblinded, for physical pair (j) let (s_j=-1) for
`AB` and (+1) for `BA`, and let (y_{j1},y_{j2}) be its day outcomes:

\[
D_j=s_j(y_{j1}-y_{j2})=Y_{AI,j}-Y_{Frozen,j}, \qquad
\hat\tau=\frac{1}{m}\sum_jD_j.
\]

The model-based primary analysis requires all 15 outcome-complete pairs and
reports `mean(D) ± t(0.975,14) × sd(D)/sqrt(15)`, where
`t(0.975,14)=2.144786688`. It targets the mean ITT day effect and assumes the
pair contrasts are independent after the locked washout, have finite variance,
and are not dominated by one outlier. Publish the pair values, Q-Q plot,
lag-one contrast correlation, previous-arm interaction, and every
leave-one-pair-out bound. For deletion (j), recompute the mean and sample SD on
the remaining 14 contrasts and use
`mean(D_-j) + t(0.975,13) * sd(D_-j) / sqrt(14)`, with
`t(0.975,13)=2.160368656`, as that endpoint's upper bound. Apply the same three
locked boundaries. If removing any one pair changes any co-primary pass/fail
classification, the bundle cannot advance; report the run as
influence-sensitive and inconclusive. The serial and previous-arm diagnostics
remain visible model-assumption limitations and cannot be used to select a
post-hoc estimator or overturn the locked rule.

As a design-based sensitivity, center the contrasts separately at each locked
boundary (`+0.05 kPa`, `+0.50°F`, and `0` device-minutes), enumerate all
(2^{15}=32,768) legal pair-sign assignments, and invert the tests over a fixed
effect grid. This is exact for a sharp constant-effect hypothesis; it is not a
distribution-free confidence interval for a heterogeneous finite-sample
average effect. Do not compute significance from 2,640 post-washout bins. A
single prospectively frozen weather-load score may be used as a secondary
precision adjustment; fitting a large weather model after seeing 15 contrasts
is not credible.

The AI template-selection bundle qualifies for **climate-preserving nine-device command-duty
reduction** only if all three co-primary conditions pass, no safety stop fires,
and protocol integrity passes. This is not proof of net energy, water, cost,
carbon, or crop value; that later claim requires eligible total resource/cost
measurement across substitute actuators. Because every condition is required,
the primary rule is an intersection-union decision and each condition uses
one-sided alpha 0.025.

Predeclare three outcomes. Passing all gates advances the bundle. A lower
one-sided 97.5% bound above either climate harm margin is evidence against the
bundle on climate; a lower bound above zero on nine-device duty is evidence of
increased command duty. Every other failure to pass—including a bound crossing
a boundary—is inconclusive, not equivalence or safety proof.

### 8.5 What 30 days can detect

The checked-in [supplement result](../../research/planner-efficacy/results-current-firmware-supplement-2026-08-14.json)
now calculates a screening bound from the 34 exact-firmware days. It applies
the proposed 02:00–24:00 window, evaluates both nonoverlapping pair origins
(17 pairs from epoch day 1 and 16 after a one-day shift), and uses the
endpoint-wise larger required distance. This avoids making precision depend on
an arbitrary historical start parity.

Let (\Delta_j=Y_{d+1}-Y_d) and
(q=\sqrt{\sum_j\Delta_j^2/(n-1)}) be an uncentered adjacent-day planning
scale. It is at least as large as the centered sample standard deviation; it is
not estimated from randomized treatment contrasts. For a one-sided 2.5% paired
t test with 14 degrees of freedom, the noncentral-t parameter giving 80% power is
(\lambda_{0.80}=3.013271700), so the favorable distance from a decision
boundary needed for 80% marginal power is:

\[
d_{0.80} = \lambda_{0.80}\frac{q}{\sqrt{15}}.
\]

This is a marginal design approximation from an all-AI observational series,
not a causal estimate, a joint probability of passing all three gates, or a
substitute for the randomized analysis.

| Endpoint | Decision boundary for AI−Frozen | Historical 22-hour mean | 80%-power distance, all operational days | Optimistic stable-pair distance |
|---|---:|---:|---:|---:|
| Mean VPD corridor distance | `+0.05 kPa` | 0.0999 kPa | 0.0949 kPa (95.0% of mean) | 0.0401 kPa (40.2%) |
| Mean temperature corridor distance | `+0.50°F` | 0.5100°F | 0.3387°F (66.4%) | 0.2567°F (50.3%) |
| Six-core runtime, secondary | `0` | 1,920 device-min/day | 725 (37.7%) | 426 (22.2%) |
| Nine-device runtime | `0` | 2,023 device-min/day | 827 (40.9%) | 456 (22.5%) |

The optimistic sensitivity removes historical pairs touching July 11, July 25,
or August 5–11. It estimates precision after reliability repair; it is not
permission to exclude future failures. Gate-specific calculations matter:

- if true AI−Frozen climate harm is zero, marginal VPD noninferiority power is
  only 31.5% with the all-day scale and 93.7% with the optimistic stable scale;
  80% all-day power would require true VPD distance to improve by at least
  0.0449 kPa, while the stable scale permits up to 0.0099 kPa true harm;
- the corresponding zero-harm temperature powers are 98.5% and 100.0%, with
  80%-power true-effect boundaries of `+0.1613°F` and `+0.2433°F`; and
- applying the exploratory 29.6% stale/fresh nine-device reduction to the
  historical daily mean gives only 52.9% marginal all-day power versus 95.7%
  under the optimistic stable scale. A 20% reduction gives just 27.9% and
  70.1%.

These scenarios assume the stated true effects; they are not effect estimates.
Because all three gates must pass, their joint advance power is no greater than
the weakest marginal gate—31.5% under the all-day illustrative scenario and
93.7% under the optimistic stable scenario—and their dependence is not
estimable from this short observational history.

Therefore 30 days can screen for a large effect, but it cannot reliably
establish or rule out an ordinary 10–20% command-duty improvement under current
variability. A null result is inconclusive. This protocol is fixed at 30 days:
outcome-driven continuation and reuse of ordinary t/randomization bounds would
inflate type-I error. If inconclusive, use its blinded variance to design a
separately preregistered, newly randomized fixed-horizon confirmatory study and
do not pool the pilot outcomes. Any group-sequential extension would instead
need maximum sample size and alpha-spending boundaries locked before day 1;
that is deliberately outside version 1.

Water and energy cannot rescue an underpowered command-duty result. Only 25 of
34 days had attribution-eligible climate water, 27 had a valid shared water
meter, and zero had scoring-eligible whole-equipment energy. If those historical
rates persisted independently of treatment, 15 days per arm would yield about
11 attribution-eligible and 12 shared-meter-valid days. That is only an
availability expectation—not a water-effect power calculation—and water stays
secondary until interval-qualified branch metering is reliable.

### 8.6 Preregistration, data eligibility, and operational stops

Commit a machine-readable protocol such as
`research/planner-efficacy/protocols/planner-switchback-v1.yaml` before the
mapping-secret draw and named future beacon round. It should contain the
study/greenhouse IDs, 30 ranges and pair IDs,
arm definitions, full baseline and two AI template vectors, 11-field treatment
allowlist, six-edge transition graph, revisions and hashes, randomization
algorithm and commitment, endpoint formulas,
margins, washout, adherence, missingness, stopping rules, shadow versions,
analysis environment digest, and tested rollback vector.

A climate bin is primary-eligible only with at least 12 of 15 expected raw
samples, finite measured indoor temperature and VPD, a finite versioned
evaluation corridor, and no indoor-value interpolation. A day requires at
least 80 of 88 bins and no continuous gap over 30 minutes. Runtime additionally
requires seeded relay state for all nine climate actuators and transition
integration that agrees with device counters within the greater of one
device-minute or 1%. Keep every randomized day in the ITT ledger even if its
outcome is missing; do not carry values forward or delete incomplete pairs.

All 15 pairs must be outcome-complete for the primary efficacy classification.
If either day of any pair lacks a co-primary endpoint, the fixed 30-day run
becomes an integrity/feasibility result. Publish the observed pairs plus
best/worst-case imputations from endpoint bounds locked in the protocol (zero
and the accepted sensor-domain/physical maximum), but do not issue an advance
decision from complete-pair deletion. This deliberately chooses a stricter
integrity rule over an undefined 13- or 14-pair analysis.

Run a seven-day A/A qualification before randomization. Required gates:

1. both branches compile the baseline to identical canonical bytes and hash;
2. every boundary activation is confirmed within 120 seconds and the correct
   assignment/vector hash covers at least 99% of minutes;
3. no unauthorized writer changes an experiment-owned field;
4. at least 98% of climate bins and all nine climate-actuator transition streams
   are valid;
5. every eligible action row joins exactly one device-confirmed vector; and
6. compiled-firmware replay and hardware-in-loop fault tests add no safety
   event relative to the factual baseline.

There is no efficacy or futility peek during the 30 days. Monitoring is for
safety and integrity only. The 30 calendar assignments are never shifted,
replaced, or rerandomized. If physical admission pauses, baseline is pinned;
the precommitted arm labels and intervals continue, and any AI-assigned day
without AI exposure remains AI in ITT. Lock the following actions:

- `SENSOR_FAULT` over five continuous minutes, actuator interlock failure, or
  an emergency override: atomically activate baseline and pause physical AI
  admission pending review without pausing the assignment calendar;
- `SAFETY_HEAT` or `SAFETY_COOL` for 15 continuous minutes or three times in
  60 minutes: baseline for the rest of the assigned day, retain the ITT day,
  and review before the next assignment;
- unknown/mismatched vector hash over five minutes or a parallel writer:
  baseline for the day; a second affected randomized day is a terminal
  integrity stop;
  and
- any change to firmware, baseline, band, allowlist, actuator inventory,
  sensor registry, or meter calibration: end the protocol epoch rather than
  pooling incompatible days.

A terminal integrity/epoch stop disables further physical AI admission. Keep
the remaining precommitted calendar rows and collect baseline observation when
safe, but label them post-stop/non-efficacy; do not replace them, restart the
clock, or issue an efficacy classification. Any future experiment receives a
new protocol, mapping secret, beacon round, assignments, and analysis.

### 8.7 Platform and database implementation

The target evidence chain is:

```text
immutable assignment
  -> append-only proposals
  -> assignment-aware arbiter
  -> atomic 49-field effective vector
  -> durable outbox
  -> staged device commit
  -> exact generation/hash readback
  -> continuous exposure interval
  -> blinded block outcome
```

Add, rather than rewrite history, migration
`db/migrations/207-controlled-policy-experiment.sql` with these core records:

| Record | Required content and invariant |
|---|---|
| `control_experiments`, `control_experiment_arms` | Explicit `qualification|aa|randomized` kind, protocol/status, timezone, revisions, beacon identity/hash where applicable, mapping commitment, schedule hash, baseline and two AI template hashes, qualified transition graph, permitted producers and mutable fields; at most one armed/running experiment per greenhouse. Keep the restricted randomized `X/Y`→`A/B` resolution out of analyst grants and expose it only through the assignment-service transition function until unblinding. |
| `qualification_transition_slots`, assignments, and ledger | The locked qualification owns 24 FIFO cell queues and four ordered slots per cell. Slots contain edge/regime/eligibility/version requirements but no fabricated future timestamp. An advisory-locked claim function persists the eligibility snapshot and materializes exactly one immutable analyzed `control_assignments` row with a half-open UTC range before actuation. The append-only transition ledger records every analyzed, positioning, baseline-recovery, identity-hold, failed, and skipped move; only a pre-eligible non-null slot claim can enter the fixed 96. |
| `control_assignments` | Immutable UUID, pair/block, exact UTC range, algorithm/version, operation kind, and frozen strata; randomized studies store only blinded X/Y, analyzed qualification assignments are materialized from locked slots immediately before activation, non-analysis positioning/recovery/identity-hold assignments use `slot_id = NULL` and an audited scheduler/reason link, and A/A records store the fixed baseline lane. Non-overlapping and immutable after lock. All 30 randomized ranges and seven A/A ranges are precommitted; only qualification uses the eligibility-gated or deterministic-hold just-in-time paths above. |
| `policy_templates` + 49 components + `policy_template_edges` | Immutable template ID/kind, schema/manifest/compiler/registry revisions, exact ordered canonical bytes and content SHA-256, 49 unique encoded components, approval and qualification-spec/result hashes, plus every permitted directed content-changing edge. The baseline/moderate/aggressive rows—not a bare hash—are the sole resolution source once locked. Record assignment-bound `identity_rebind` as a distinct operation, not as a qualified physical edge. |
| `experiment_context_snapshots`, virtual planner state | Immutable prompt/model/tool, lesson/retrieval-corpus, crop/topology and response-model manifests plus hashes; append-only per-trigger virtual prior/selected template used identically in both arms and never overwritten by physical admission state. |
| `policy_proposals` + components | Append-only AI, forecast, baseline, guardrail, and operator proposals with assignment, trigger, prompt/model/compiler/forecast lineage, base hash, validity, digest, and shadow/reject state. |
| `effective_policy_vectors` + components | Exactly 49 unique normalized values, monotonically increasing device generation, assignment-contained validity, canonical bytes, content hash, assignment-bound activation hash, and per-component producer/clamp lineage. |
| `policy_delivery_outbox` + command/chunk rows | Unique `(device_id, vector_id)` idempotency key, lease, ordered chunks, attempt lifecycle, stage/activation times, and bounded error class. |
| `policy_device_snapshots` | Device-echoed schema, generation, assignment and hashes, validity, apply state and firmware revision. |
| `policy_exposures`, experiment/override events | Confirmed continuous intervals, expected/observed identity, coverage and close/fallback reason; append-only protocol deviations and emergency actions. |
| `planner_inference_runs` | Proposal/assignment, provider/model/prompt/tool revisions, start/end/retries, input/output/cache tokens, price snapshot and billed cost, plus energy when measurable; record the same scheduled work in both arms. |

This migration needs a real populated-database delivery path. Today
[`db/Dockerfile.migrate`](../../db/Dockerfile.migrate) copies only the schema
snapshot and migration 000, while [`db/migrate.sh`](../../db/migrate.sh) exits
successfully without applying numbered migrations when core tables already
exist. Promote the existing
[`schema_migrations` ledger design](../../db/ledger/schema_migrations.sql) into
production (including an audited baseline for already-applied files), copy the
numbered migration and its SHA into the migrate image, and run it from the
digest-pinned [PreSync Job](../../deploy/k8s/base/migration-job.yaml).

The populated-DB runner must take one migration advisory lock, verify filename
and SHA against the ledger, use `psql -X -v ON_ERROR_STOP=1` with bounded lock,
statement, and idle-transaction timeouts, apply one idempotent transaction, and
stamp success only after post-schema assertions. Before the first production
run, verify a restorable database snapshot and dry-run against a recent copy;
afterward assert every new table/function/index, constraint validation state,
owner/grant, and ledger hash before application pods roll. A rebuilt migrate
image that takes today's populated-DB no-op path is a failed deployment.

Extend `planner_trigger_ledger`, `plan_delivery_log`, `climate_action_log`,
setpoint audit, and twin rows with experiment, assignment, vector, generation,
hash, and exposure fields. Keep existing `setpoint_plan`, `setpoint_changes`, and
`setpoint_snapshot` as compatibility/event projections, not treatment truth.

Make ownership explicit in new modules rather than growing the existing
dispatcher monolith:

| Proposed owner | Sole responsibility |
|---|---|
| `ingestor/tasks/experiment_assignments.py` | Validate the locked schedule, open/close the current immutable assignment, reserve the next boundary activation, and emit protocol deviations. |
| `ingestor/tasks/policy_arbiter.py` | Select an eligible proposal, enforce arm/allowlist/revision/validity rules, and atomically persist one effective vector plus outbox intent. |
| `ingestor/tasks/policy_delivery.py` | Lease and serialize vector transactions, reconcile unknown commits by device hash, record readback, and open/close exposure intervals. |
| `ingestor/ingestor.py` action logger | Consume the device-confirmed vector identity for each tick; never infer whole-vector lineage from the newest component row. |

The existing dispatcher may project confirmed components into legacy tables,
but it cannot admit, compose, or identify an experiment vector. Register the
three workers explicitly in `ingestor/tasks/__init__.py` and their lifecycle in
`ingestor/ingestor.py`; feature-off startup must not create leases, timers, or
outbox work.

Add one audited protocol loader that, while an experiment is still draft,
decodes the checked protocol artifact into `policy_templates` and components,
re-encodes all 49 fields through the canonical codec, and proves that stored
bytes, component rows, schema manifest, and content SHA-256 agree. Locking the
experiment makes the three template rows and edge graph immutable. Only
`resolve_policy_template(experiment_id, assignment_id, template_id,
from_content_hash)` may return bytes to the arbiter. For `qualification`, a
content-changing edge must be authorized either by an already claimed analyzed
slot or by a pre-actuation immutable non-analysis positioning/recovery
assignment under the same locked six-edge graph; both outcomes are recorded,
but only the former enters the 96. For `aa` and `randomized`, a
content-changing edge fails unless the template is approved and that exact edge
has a passing qualification result. Every kind also requires an armed
experiment and matching frozen revisions. Template IDs or hashes without those
canonical bytes/components are not executable treatment definitions.

Define one explicit exception that does not weaken that gate: an
`identity_rebind` has byte-identical 49-field canonical content and the same
content hash before and after, while only the assignment, activation hash,
monotonic generation, and assignment-contained validity change. Permit it only
for a manifest member allowed by the new assignment's kind and physical arm:
the current qualification member for an `identity_hold`, baseline for A/A and
Frozen days, and baseline, moderate, or aggressive as allowed for randomized
days. It needs no settling qualification because it causes no control-policy
change. At an assignment boundary it is a real audited commit;
inside one assignment the arbiter deduplicates an unchanged selection whenever
validity already covers the requested interval, and may renew it only under the
protocol's explicit TTL rule. A single changed canonical byte makes the
operation a content-changing edge and restores the exact six-edge qualification
requirement.

Guard experiment transitions through SQL functions. The qualification claim
function must hold the per-greenhouse advisory transaction lock, prove either
the next FIFO slot and pre-step evidence or the locked non-analysis
position/recovery/hold rule, check assignment overlap, insert the immutable UTC
assignment and ledger row, and commit before the arbiter can enqueue it.
A vector cannot become
ready without 49 unique registered components; shadow proposals cannot create
outbox rows; AI may change only the protocol allowlist; validity cannot cross
an assignment; generations cannot be reused; and a failed AI delivery or
baseline fallback can never relabel the ITT arm. Exposure opens only after the
device echoes the exact assignment, schema, generation, and activation hash.
The current database setup does not install `btree_gist`, so enforce assignment
non-overlap without assuming an exclusion constraint: transition functions take
a per-greenhouse advisory transaction lock, query for overlap, insert, and
commit atomically. Revoke direct table DML from application roles and add
immutability triggers for locked assignments/protocol rows.

That revocation is impossible with today's shared `DB_USER=verdify`, which is
also the schema owner. Split credentials before shadow mode: a migration owner
available only to the PreSync job; an API role with EXECUTE on audited experiment
state transitions; a read-only Iris context role limited to the experiment
context/frozen-snapshot views; an MCP runtime role with its individually audited
non-study tool functions plus proposal-function EXECUTE but no direct table DML;
a distinct `planner_graph` role with only its required claim/lease/run/memory DML
and proposal-function EXECUTE; an ingestor/arbiter role with telemetry writes
and policy transition/outbox EXECUTE; a blinded outcome-analysis role with
`SELECT` only on X/Y outcomes, opaque assignment-scoped receipts, equality and
coverage fields; and a twin role described in Section 8.9. Explicitly deny the
analysis role direct access to proposal/effective-vector components, reusable
hashes, setpoint plan/change/snapshot tables, the map secret, and physical-arm
resolution until the completed-state unblind transition.
Use per-workload Secret references and usernames in the GitOps pod specs.
Within the ingestor pod, experiment-mode context gathering must use a dedicated
`IRIS_CONTEXT_DATABASE_URL`/Secret and reject the generic owner/ingestor DSN;
the host worker keeps its separate ingestor role. MCP and `planner_graph` each
receive their own role rather than a shared “planner” credential.
`SECURITY DEFINER` functions pin `search_path`, validate caller/experiment state,
and are owned by a non-login migration role. Acceptance tests connect as each
live runtime role, prove forbidden direct `INSERT/UPDATE/DELETE`, and prove only
the intended functions/read views succeed. Triggers are defense in depth, not a
boundary against an owner or superuser.

Keep rollout nonblocking. New lineage columns on large history/hypertables such
as `climate_action_log` start nullable; add foreign keys `NOT VALID`, deploy
dual-write readers, validate constraints after coverage is proven, and backfill
in bounded time chunks. New experiment truth is append-only and does not depend
on completing a historical backfill before shadow mode.

Add authenticated, idempotent, optimistic-concurrency endpoints in
[`api/main.py`](../../api/main.py) to validate/randomize, shadow, arm, pause,
resume, abort, complete, rollback, and export an experiment. The kind-specific
state machine must reject `aa` before a completed
qualification and reject `randomized` before both passing result hashes are
bound; each kind requires its own GitOps ID and manifest echo. During execution,
analyst status/export routes return blinded arms and suppress proposal source,
component values, reusable content hashes, and other treatment-revealing
lineage. A separately authorized and audited operational `/setpoints` route
should expose the device-confirmed effective policy apart from contextual
band/schedule values rather than reconstructing another mixed policy; safety
operators are explicitly not considered blinded.

Add a frozen `v_control_experiment_daily_outcomes` view. It should retain ITT
arm, pair, confirmed exposure coverage, both corridor-severity endpoints,
controller-miss/unachievable time, runtime/cycles, resource scope and quality,
planner compute/availability, overrides, gaps, and only prospectively frozen
as-of weather covariates. Never coalesce missing resource evidence to zero.

Add a blinded operations board to
[`site-intelligence-planning.json`](../../grafana/dashboards/site-intelligence-planning.json)
showing opaque assignment ID, a unique assignment-scoped activation receipt and
expected-versus-device-confirmed equality, exposure coverage, delivery lag,
missing data, safety/fallback state, and the tested rollback action. Do not show
the reusable content hash, component values, or proposal source. During the run
it must not show arm labels or comparative
efficacy endpoints; unblinding is a one-way completed-state API transition after
the frozen export hashes are recorded.

### 8.8 Planner, arbiter, and forecast implementation

The production planner is **not** `planner_graph`. The production
[`ha-stateless-spread` patch](../../deploy/k8s/overlays/prod/ha-stateless-spread.patch.yaml)
records zero graph runs; the live path is
[`ingestor/iris_planner.py`](../../ingestor/iris_planner.py) →
[`scripts/gather-plan-context.sh`](../../scripts/gather-plan-context.sh) → the
[Iris Hermes profile](../../deploy/k8s/components/hermes-iris/hermes-config.yaml)
→ [`mcp/server.py`](../../mcp/server.py). Implement and prove the treatment
firewall on that path first.

Add a security-barrier `v_iris_experiment_context` and immutable context/lesson
snapshot keyed by experiment and context revision. In experiment mode,
`iris_planner.py` must pass the explicit experiment ID and opaque assignment
receipt to a fail-closed gather mode. That mode exposes current sensors, as-of
forecast, fixed crop/topology context, the frozen knowledge snapshot, and the
planner's virtual prior template only. It must omit physical active
plan/setpoints, admission/fallback state, recent arm outcomes and scorecards,
evaluation backlog, and mutable lessons. Make the checked-in source and its
verbatim generated
[`gather-script-configmap.yaml`](../../deploy/k8s/components/ingestor-gather-script/gather-script-configmap.yaml)
one generated artifact with a byte-sync CI test; a stale mounted copy fails
startup rather than silently using the general context packet.

Give the Hermes experiment profile an audience-scoped MCP credential and a
minimal tool set enforced in **both**
[`hermes-config.yaml`](../../deploy/k8s/components/hermes-iris/hermes-config.yaml)
and the MCP authorization layer. Permit qualified climate/forecast/topology
reads, frozen experiment-context retrieval, `policy_template_propose`, and
trigger acknowledgement. Deny treatment-revealing `get_setpoints`,
`plan_status`, `history`, score/outcome/lesson reads, and the ordinary
`set_plan`, `set_tunable`, `plan_evaluate`, and `lessons_manage` writes for that
audience. If retrieval is required, expose a distinct read-only search over the
frozen experiment snapshot; do not reuse the mutable lesson/knowledge tools.
`iris_planner.py` sends the same template-selection prompt and virtual state on
both arms, and evaluation/lesson writes remain quarantined until unblinding.

Change [`mcp/server.py`](../../mcp/server.py) `set_plan` and `set_tunable` from
actuation-eligible database writers into compatibility **proposal** writers. A
central arbiter is the sole admission path. Convert
[`forecast-action-engine.py`](../../scripts/forecast-action-engine.py) from a
direct `setpoint_plan`/`setpoint_changes` writer into a deterministic proposal
producer, and reject legacy direct writes to experiment-owned parameters while
an assignment is armed.

For future graph-path parity, generate its contract from the canonical registry;
this does not substitute for the Iris/Hermes controls above. The copied
[`planner_graph/verdify_contract.py`](../../planner_graph/verdify_contract.py)
is already stale: it has 39 copied defaults versus 40 canonical Tier-1 fields,
omits `band_track_fraction`, `cool_stage2_exit_hysteresis_f`,
`night_vpd_bias_kpa`, and `vent_exchange_fraction`, and retains obsolete
`fog_stress_min_dew_margin_f`, `fog_stress_window_latest_hour`, and
`sw_fog_stress_window_extend_enabled`. Replace the copy with generated output
and add an opaque experiment/assignment receipt, proposal and schema/compiler
lineage, and a single `proposal` run mode to `planner_graph/contracts.py`,
`nodes/materialize_proposal.py`, and `nodes/execution_verify.py`. The planner/LLM
contract is byte-identical in both arms and never receives X/Y→A/B mapping or
physical-admission eligibility. After persistence, the restricted arbiter—not
the planner—records whether the proposal is shadowed or admitted.

The planner context may contain the opaque assignment receipt, raw current
sensor/forecast state, and its own prior virtual template selection. It must not
contain the physical effective-vector components/content hash, fallback state,
or admission result. Maintain that virtual proposal state on both arms so an A
day does not announce itself through “no previous AI vector”; the arbiter alone
reconciles a selection with actual device state and the qualified transition
graph. Physical climate can of course carry treatment information, so this is a
data firewall—not a claim that an LLM cannot statistically guess the arm.

Keep one canonical semantic compiler. Today
[`mcp/server.py`](../../mcp/server.py) calls
[`materialize_climate_intent_tier1`](../../verdify_schemas/climate_intent.py);
refactor that function into a pure, revisioned `ClimateIntent` → complete
40-component Tier-1 **proposal** with no persistence or actuation side effect,
and extend
[`test_climate_intent.py`](../../verdify_schemas/tests/test_climate_intent.py)
for every field/direction. The arbiter then fills the nine baseline-only live
values and is the only owner that can compile/admit the complete 49-field
effective vector.

For experiment version 1, keep that general semantic materializer shadow-only.
The actuation-eligible planner output is instead one opaque `policy_template_id`
chosen from the two locked AI templates, plus prediction/rationale telemetry;
the planner never supplies component values. The arbiter resolves the ID to its
exact 49-field content hash and rejects an unknown template or unqualified
transition edge. This preserves forecast/state-dependent AI selection and
intraday replanning without pretending two corner tests certify a continuous
11-dimensional hybrid/dwell surface.

The deterministic compiler should operate in one database transaction:

```text
assignment + frozen baseline + eligible proposal + fixed revisions
  -> registry normalization + cross-field checks + guardrails
  -> one 49-field vector + components + outbox row
```

Require an absolute complete Tier-1 proposal or an explicit immutable base
vector hash. Compile against the band/state at activation time, clip validity
to the assignment, and stop materializing 72-hour waypoint chains. Retain one
sunrise baseline opportunity, then replan only on predicted corridor-exit
probability, material as-of forecast revision, residual/out-of-distribution
state, readback mismatch, TTL, health, or assignment change. Sunset, midnight,
and noon checks should normally acknowledge/no-op. Offline acceptance is at
least 50% fewer model/delivery calls with no worse replayed safety or climate
outcome and lower vector churn.

Repair forecast truth before using it in either treatment or shadow scoring:
compare one as-of outdoor forecast with outdoor truth at the same target/lead,
retain residual distributions by lead/regime, and learn outdoor-to-indoor
response separately. `thermal_lead_time_min` must either drive actual timing or
leave the contract; negative one-sided forecast-bias outputs and retired writes
must be removed. Each semantic input needs low/default/high contract tests that
name and change an effective component in the expected direction.

Do not use AI self-grade as reward. Persist a pre-action prediction and
interval, score one exact vector-attributable transition once, enforce climate
and safety first, and optimize only eligible measured runtime/resource terms.

### 8.9 Atomic transport and firmware implementation

Generate matching Python and C++ codecs—for example
`verdify_schemas/policy_vector.py` and
`firmware/lib/policy_vector_generated.h`—from the registry. First extend
`TunableDef`: the current registry has bounds and kinds but no canonical
quantum/wire scale. Give every live field a permanently assigned `wire_id`,
encoded kind, unit, quantum, integer width/scale, and reserved-ID policy. Freeze
endianness and Boolean encoding; never derive IDs from insertion or sorted
order. Add drift tests against the ESPHome entities in
`firmware/greenhouse/tunables.yaml`, and snapshot the exact ordered wire-schema
manifest in the protocol so an old NVS vector remains decodable after a rename.

Use a versioned header, fixed-width scaled integers and bit fields. Define
`content_sha256` over a domain/version tag, schema ID and wire-manifest digest,
the exact canonical ordered `(wire_id, encoded fixed-width value)` byte stream,
and fixed policy revision IDs. Define `activation_sha256` over that content
identity plus experiment, assignment, exact `assignment_treatment_bytes`,
generation, and validity. Do not serialize a nullable generic "blinded arm."
Version 1 uses these exact octets:

- randomized: `0x01 || 0x58` for X or `0x01 || 0x59` for Y;
- qualification: `0x02 || operation_code || source_template_uuid_bytes ||
  target_template_uuid_bytes || regime_code`, where operation codes are `0x01`
  analyzed, `0x02` positioning, `0x03` baseline recovery, and `0x04` identity
  hold; UUIDs are 16-byte RFC 4122/network-order values and regimes are the
  locked `0x00..0x03` order from Section 8.3; and
- A/A: `0x03 || 0x00` for lane 0 or `0x03 || 0x01` for lane 1.

A required missing field is invalid; off/shadow mode emits no activation hash
rather than a null sentinel. Store these bytes with the assignment. Golden
fixtures for every tag, operation, and lane must hash identically in Python,
compiled firmware, and the twin.

Add a separate canonical `ExperimentPolicyManifest` containing experiment ID
and kind, schema/registry/compiler revisions, baseline/moderate/aggressive
template IDs and content hashes, permitted content-changing-edge bitmap,
kind/arm-scoped identity-rebind rules, qualification specification/result
references, and validity. Encode each optional result reference as `0x00` when
absent or `0x01 || 32-byte_sha256` when present: qualification manifests require
the specification hash but must have absent qualification and A/A results; A/A
requires the completed qualification result and an absent A/A result; randomized
requires both completed results. Stage and atomically activate this
manifest through audited `begin_experiment`/`commit_experiment` services before
any non-baseline vector. In `qualification`, firmware accepts only edges named
by the locked qualification specification and assignment operation
(`analyzed|positioning|baseline_recovery`), plus same-content `identity_hold`;
`aa` accepts baseline only; a `randomized` manifest accepts only edges with the
bound passing result hash.
Every content-changing policy commit must match an active manifest template and
permitted qualified edge, not merely pass bounds and self-hash checks. A
same-content identity rebind is accepted only when all 49 encoded bytes and the
content hash match the active manifest member, the new assignment permits that
member, generation increases, and only assignment/activation/validity identity
changes. Persist and echo the operation kind and manifest hash with the policy
identity, and use the same content-edge-versus-identity-rebind check in Python,
the twin, HIL, and firmware. The ROM baseline remains the only
manifest-independent fallback.

Extend [`esp32_push.py`](../../ingestor/esp32_push.py) to serialize a whole
manifest or vector transaction; no vector may stage until the device echoes the
expected active manifest. Its current fair queue must not interleave two staged
vectors. Preserve the singleton writer, device-write default deny, database
lease, heap and reconnect fences. Resolve an unknown commit outcome by reading
the device generation/hash before retrying. Serialize generations in the
outbox and never let an intraday proposal overwrite a pre-staged assignment
boundary.

Add `firmware/lib/policy_vector.h` and generated schema with immutable
`active`, `boundary_pending`, and `tactical_pending` `ControlPolicy` slots plus
an immutable Frozen-FSM baseline compiled into the firmware image. The boundary
slot is reserved for the next assignment; the tactical slot may contain only a
vector whose validity ends before that boundary. Only one transfer stages at a
time, due boundary generation wins, and neither pending slot can be overwritten
without an explicit idempotent abort. Expose
`begin_policy`, ordered chunk/component staging, `validate_policy`,
`commit_policy(effective_at)`, and `abort_policy`. Firmware must reject missing
or duplicate components, bad hashes/schema, stale generation, nonfinite or
out-of-range values, and cross-field violations. A successful commit swaps one
active pointer on one one-second control-loop boundary.

Wire the generated headers into
[`firmware/greenhouse.yaml`](../../firmware/greenhouse.yaml) `esphome.includes`,
register the experiment-manifest and policy begin/chunk/validate/commit/abort
native API services there, and publish manifest/vector identity and apply-state
readbacks. Migrate policy use in
[`controls.yaml`](../../firmware/greenhouse/controls.yaml),
[`sensors.yaml`](../../firmware/greenhouse/sensors.yaml), and auxiliary lambdas;
add native compiled tests and power-loss fixtures under
[`firmware/test`](../../firmware/test/).

That pointer swap is atomic only after **every one of the 49 policy consumers**
is migrated from direct `id(...)` globals to one policy snapshot captured at
the start of the tick and passed through the core FSM, auxiliary binary
sensors, zone/mister paths, budgets, dwell logic, and cfg readbacks. While an
experiment is armed, legacy globals are diagnostics only. Generate a consumer
manifest and fail CI if a planner-pushable ESPHome ID is read outside the
policy accessor or lacks a generated consumer/telemetry mapping.

Persist the active experiment manifest plus active and pending policy state in
a two-copy, sequence-numbered crash-safe NVS journal with magic, schema,
generation, SHA-256, and CRC. The Frozen-FSM
baseline and its schema/hash live independently in immutable firmware/ROM, so
corrupt or incompatible NVS cannot corrupt the fallback. Publish assignment,
generation, hashes, schema, component count, state, validity, apply result, and
firmware revision. Per-parameter tolerance remains diagnostic and can never
establish exposure. Test power loss at every journal/stage/commit boundary,
both-copy corruption, schema migration, and the flash-write budget under the
maximum replanning cadence.

Policy identity is not the whole crash state. Today volatile globals include
daily mister-water use and per-zone runtime/last-off state in
[`globals.yaml`](../../firmware/greenhouse/globals.yaml). For version 1, a
reboot with no independently verified same-day water high-water mark must
conservatively mark the assigned wetting budget and hard ceiling as consumed
until the next verified local-day rollover; it may not restart from zero. Boot
all relays off, initialize every actuator's last-off time to boot time, enforce
its full minimum-off dwell, reset the FSM and fairness state deterministically,
and emit one typed reset event. Close exposure at the last pre-reboot tick and
reopen it only after clock, sensors, active assignment/vector identity, and
dynamic-state policy are requalified. A later wear-levelled accumulator may
reduce that conservative downtime, but it must journal monotonic water/runtime
high-water marks with a proven flash budget and may never roll them backward.

Fail safe as follows:

- relay boot state remains off and all existing safety/manual/occupancy/leak
  interlocks outrank the experiment;
- a valid persisted policy may resume after reboot with the same identity only
  after the conservative dynamic-state rules above are applied and logged;
- corrupt/incompatible NVS, invalid clock, incomplete staging, or expired
  validity activates the immutable ROM baseline atomically;
- connectivity loss may retain the active policy only through `valid_until`;
  and
- individual legacy setters reject experiment-owned mutations while armed.

The current
[firmware-twin manifest](../../deploy/k8s/components/firmware-twin/twin-shadow-deployment.yaml)
is an offline fixed-corpus loop, is absent from both production overlays, and
explicitly falls back for missing setpoint inputs; it cannot satisfy the live
shadow gate. First extract every experiment-relevant decision now split across
`controls.yaml`, `sensors.yaml`, auxiliary ESPHome lambdas, and the core FSM into
shared native C++ called by both firmware and twin, or generate and compile an
equivalent complete harness. Both paths must capture the same one-tick policy
snapshot and pass a generated input/consumer/output coverage manifest; agreeing
only on vector hashes or today's partial replay API is insufficient. Build the
missing live adapter around that shared oracle: consume a read-only, as-of
telemetry and device-vector feed; decode all 49 values and hashes; copy-sync the
generated headers rather than hand-copying policy fields; and publish paired
live action/hash divergence. Replace the proof manifest's mutable runtime GCC/Python
build and startup `pip install` with the durable
[`twin/Dockerfile`](../../twin/Dockerfile) image, built in-cluster, pushed to
zot, and digest-pinned; remove TCP/443 egress and runtime compilation. Add the
component and DB-only NetworkPolicy to the production kustomization. Add a
security-barrier `v_policy_twin_asof_input` view that pairs each tick with the
latest device-confirmed vector/components at or before that tick. Its fixed
manifest must include exact tick timestamp and clock validity; sensor value,
validity, and freshness; relay/readback state; firmware boot/reset events; and
water, budget, dwell, fairness, and resident-FSM state or its deterministic
initialization event. Grant the twin runtime only `SELECT` on that view and
`INSERT` on twin result tables—no policy/control DML—and test privilege denial,
as-of pairing, stale-vector handling, and feed freshness. Verify the twin image,
source, firmware, policy-schema, and corpus/live-adapter hashes. Reset twin state
on the same boot event, classify warm-up or unmatched-state intervals as gaps,
and never count them as agreement. Require byte-identical policy and action
agreement in 7–14 days of live shadow before counterfactual use, while retaining
its no-actuation/no-device-egress properties.

### 8.10 GitOps rollout, acceptance, and rollback

Add declarative flags to
[`deploy/k8s/base/configmap.yaml`](../../deploy/k8s/base/configmap.yaml) with
safe defaults `mode=off`, empty experiment ID, and legacy writes enabled. Set
an explicit shadow/live mode and experiment ID only in
[`overlays/prod/device-write-configmap.yaml`](../../deploy/k8s/overlays/prod/device-write-configmap.yaml);
keep the corresponding
[`prod-dark` patch](../../deploy/k8s/overlays/prod-dark/device-write-configmap.yaml)
non-actuating:

```text
VERDIFY_POLICY_VECTOR_MODE=off|shadow|live
VERDIFY_ACTIVE_EXPERIMENT_ID=<explicit UUID>
VERDIFY_LEGACY_DIRECT_POLICY_WRITES_ENABLED=0|1
```

Live admission requires the existing device-write flag and lease, the exact
GitOps experiment ID, an armed database assignment, compatible firmware, and
an already confirmed baseline. Deliver the mapping secret through the existing
secret-management path to the assignment service alone; commit only its
domain-separated hash, never the secret itself. Preserve the singleton
`Recreate` ingestor.
The production Argo application is historically named
[`verdify-prod-dark`](../../deploy/k8s/argocd/apps/verdify-prod-dark.yaml), but its source
is `deploy/k8s/overlays/prod`; that is the only study target. Never sync the
actual `overlays/prod-dark` device-dark shape as if it were production.

Build changed API, MCP, ingestor, migrate, `planner_graph`, and completed live-twin
images in-cluster, push to the zot origin, and pin their digests in
[`overlays/prod/kustomization.yaml`](../../deploy/k8s/overlays/prod/kustomization.yaml).
Retain an immutable digest for the Hermes runtime image and record it with the
Hermes profile, MCP audience/tool manifest, Iris prompt, frozen context, and
mounted gather-script hashes.
The fixed-name `verdify-config` is consumed through `envFrom`, so a ConfigMap
change alone does not restart a pod. Add a GitOps-owned policy-config revision
hash to the API, MCP, planner, and ingestor pod templates, a Hermes profile/tool
hash to the Hermes pod, and the source/generated gather-script hash to the
ingestor pod (or equivalent deterministic rollout mechanisms). Verify the
restarted Iris/Hermes/MCP path reports the intended context mode, experiment ID,
tool audience, profile/image digest, and config hashes before arming the
database. Argo remains the
deployment authority; production means the exact revision and intended
resources are `Synced + Healthy`, the expected image IDs/config revisions are
running, and the baseline/vector hash is confirmed on-device.

Roll out in this order:

1. verify the restorable database snapshot, run the ledgered migration-207
   PreSync path, pass post-schema assertions, then roll contracts, APIs, outcome
   view, dashboards, digest-pinned images, and flags off; verify every config
   consumer;
2. proposal/arbiter/outbox shadow mode; no device actuation;
3. build the staged-vector OTA and a separate recovery image through
   [`firmware-builder.yaml`](../../deploy/k8s/components/firmware-builder/firmware-builder.yaml).
   The recovery image must use the prior proven control logic **plus** the exact
   immutable baseline/vector schema compiled in; the old binary alone can boot
   defaults because many legacy globals are not restored. Pin source, toolchain,
   binary, baseline, and schema hashes and test both images before OTA;
4. deploy the completed live twin and collect 7–14 days of shadow action/hash
   comparison;
5. create and lock a non-efficacy `qualification` UUID with the hashed pretrial
   specification, three canonical templates, six content-changing edges, 24
   FIFO cell queues, and four target slots per cell—not 96 unknowable future UTC
   assignments. Commit `mode=live`, that exact ID, legacy writes `0`, and the
   experiment Hermes/context profile through GitOps; sync, restart, verify every
   config/image/audience hash, stage and echo the qualification manifest, then
   arm the DB record. Run Section 8.3: immediately before each eligible step,
   atomically claim its next slot and lock the exact UTC assignment; ledger and
   authorize every analyzed or positioning/baseline-recovery transition under
   the rules above. Do not silently reset baseline after each edge. Position it
   only through its own immutable exact-range non-analysis assignment and ledger
   row, fail on its safety/delivery error, and require the locked three-hour
   settle/pretrace assignment (plus deterministic 15-minute identity holds when
   needed) before a later analyzed step. Require every gate, all 96 immutable
   analyzed assignments, the complete non-analysis assignment ledger, and
   the fixed two-hour/88-bin contract to pass; otherwise stop and author the new
   protocol/artifact required there. On completion, confirm baseline through the
   same ledgered path, close exposure, complete the UUID, declaratively return
   to `mode=shadow` with an empty ID, and verify the reset rollout;
6. create a separate non-efficacy `aa` UUID with seven fixed local-day
   assignments whose two audited lanes both resolve to the exact baseline
   content while exercising proposal, arbiter, boundary, transport, readback,
   and exposure paths. Commit that ID in `mode=live`, sync/restart/verify, stage
   its baseline-only manifest, arm and run the seven-day A/A gate. Then confirm
   baseline, complete it, return declaratively to shadow/empty ID, and verify
   reset before continuing;
7. freeze the randomized protocol and new UUID, bind the passing qualification
   and A/A result hashes, name the future beacon round, draw and commit the
   witnessed mapping secret before that beacon is published, and generate the
   schedule. Commit `mode=live`, the randomized ID, legacy writes `0`, and its
   frozen Hermes/context hashes through GitOps; sync/restart/verify, stage and
   echo the randomized manifest, then arm the DB record and pre-stage day 1;
8. run blinded for 30 days without efficacy peeking; then freeze exports and
   endpoint/fidelity/deviation tables before revealing the arm mapping; and
9. confirm baseline on-device, complete the experiment, then declaratively set
   mode off/empty ID, restore legacy writes only after the confirmed baseline,
   sync, restart, and verify the final state.

Acceptance tests must cover kind-specific database state transitions/overlap,
qualification→A/A→randomized result binding, exact 15/15 schedule regeneration,
canonical template load/immutability/resolution, 49-component completeness,
exact two-template admission, firmware manifest membership, six qualified
content-changing directed edges, eligibility-gated FIFO slot claims, immutable
just-in-time qualification ranges, analyzed-versus-positioning ledger rules,
non-analysis assignment overlap/authorization/validity and deterministic hold
chaining, 60-minute source-content continuity across valid hold chains, and
gap/wrong-content/manifest rejection,
all three same-content identity rebinds, A/A baseline rebind, randomized
same-arm boundary rebind, assignment-bound activation identity, every exact
kind/operation/lane treatment tag and missing-field rejection, optional
qualification/A/A result-reference encoding, and 11-field mutation limits,
cross-language golden hashes, shadow non-actuation, atomic database/outbox
commit, duplicate workers and idempotent retries, disconnected staging, stale
generation, bad hash/schema, NVS corruption, reboot/expiry fallback, unknown
water-high-water handling, full boot min-off dwell, heap stability, action-log
one-vector joins, missing-telemetry exposure closure, source/generated gather
script equality, experiment context omission/frozen retrieval, Hermes/MCP
audience and write denials, per-runtime DB role denials, blinded-role denial and
non-inferable exports/receipts, qualification/A/A/randomized GitOps ID rollovers
and pod restarts, complete
shared-code twin consumer coverage, twin boot/state parity and gap classification,
live-twin no-actuation/network isolation, exact image/config revision reporting,
recovery-image boot, and feature-off/A-A parity with current behavior.

Immediate rollback order matters: pause admission; enqueue the frozen baseline
through the same outbox; confirm its exact device generation/hash; close the
exposure as fallback; only then set GitOps mode off, restore legacy direct
writes to `1`, sync, and confirm every env-consuming pod restarted onto the
rollback config revision. With no network, firmware expiry to the immutable ROM
baseline is the independent rollback path; if the new firmware itself is bad,
flash the verified recovery image rather than the unmodified legacy binary.

## 9. Control, tunable, sensing, and learning roadmap

### 9.1 Improve the current FSM before replacing it

The deployed controller is already a useful safety-railed hybrid FSM. Its
weakest layer is not the state machine—it is the uncalibrated candidate model.
Current action projections use fixed one-cycle temperature/VPD effects, a
constant 0.65 confidence, and heuristic water/electric/gas costs; selection
then ignores resource/churn estimates and orders by normalized climate error
and prior-action hold.

First learn a versioned local response model in physical coupled coordinates
`[indoor temperature, humidity ratio/absolute humidity, slow thermal mass]`.
Condition it on outdoor temperature/humidity ratio, solar, wind, current
policy, relay state, and regime. Persist every candidate's predicted 5/15/30
minute response and uncertainty beside observed relay truth and outcome. Run
this calibrated projection in shadow while the incumbent eligibility and
safety ordering remain unchanged. Primary greenhouse studies have demonstrated
real-time recursive temperature/humidity parameter estimation and adaptive
models driven by indoor/outdoor climate, solar, wind, and actuator state
([Cunha, Couto, and Ruano,
1997](https://doi.org/10.1016/S1474-6670(17)41244-4);
[Cunha, 2006](https://doi.org/10.13031/2013.21858)). Verdify still
has to validate range, residuals, uncertainty coverage, and recursive closed-
loop behavior on its own hardware before those models influence selection.

### 9.2 Add bounded feed-forward and identify one lever at a time

Wire measured solar level/slope and short-horizon heat/solar forecast into a
bounded cooling-readiness offset or certified hot/bright template. Add Tempest
wind speed, direction, gust, and freshness to `SensorInputs`; replace the
single planner-authored `vent_exchange_fraction` with a commissioned estimator
using wind, inside/outside temperature and humidity-ratio differences, vent
state/position, and uncertainty. Full-scale experiments show greenhouse
ventilation response changes with opening, wind, and solar load
([Teitel and Tanny, 1999](https://doi.org/10.1016/S0168-1923(99)00041-6)).

The first switchback must not silently acquire new treatment dimensions. Add
the following fields behind shadow-only gates, then promote one mechanism only
after commissioning or a separate micro-intervention. Bounds below are initial
HIL envelopes to validate, not claims of agronomic safety:

| Proposed canonical field(s) | Type and initial bound | Owner and firmware/model consumer | Prerequisite; first switchback |
|---|---|---|---|
| `solar_cooling_ff_gain_f_per_kw_m2`, `solar_cooling_ff_cap_f` | fixed-point numeric: `0–2°F/(kW/m²)` and `0–2°F` | Commissioned deterministic template; adjusts non-safety cooling-candidate readiness, never the hard high-temperature rail | Qualified solar level/slope and as-of forecast; **shadow-only** |
| `night_dehum_entry_vpd_excess_kpa`, `night_dehum_exit_hysteresis_kpa`, `night_dehum_max_duty_pct` | fixed-point numeric: `0.05–0.30 kPa`, `0.05–0.20 kPa`, `0–25%` | Certified planner template consumed by `DEHUM_VENT` eligibility, exit hysteresis, and duty limiter | Outdoor absolute humidity, dew/leaf margin, occupancy and condensation gates; **shadow-only** |
| `vent_exchange_base_fraction`, `vent_exchange_wind_gain_per_mph`, `vent_exchange_stack_gain_per_f`, `vent_exchange_uncertainty_fraction` | commissioned model parameters: `0.10–0.60`, `0–0.10/mph`, `0–0.03/°F`, `0–0.30` | Calibration-owned, explicitly **not AI-writable**; consumed only by the candidate response/uncertainty model | Fresh wind plus vent position/limits and matched pulse identification; **shadow-only** |
| `planner_max_changed_components`, `planner_min_activation_interval_min` | arbiter integers: `1–4` fields and `15–120 min` | Protocol/arbiter-owned churn budget; limits proposal admission, not firmware safety | Atomic vector lineage and replay; fixed equally across arms |
| `fan_speed_pct`, `vent_position_pct`, `fog_pressure_pct` | future bounded commands `0–100%` | Low-level MPC/actuator commands, **not ordinary planner tunables** | VFD/position/pressure hardware and calibrated readback; unavailable in the first study |

Each added registry field needs the stable wire metadata, one named consumer,
low/default/high direction tests, telemetry/readback, owner, and fail-safe
default defined in Section 8.9. Model coefficients and uncertainty gates remain
commissioning-owned even after the AI policy surface grows.

Use safe, assignment-logged micro-interventions outside the randomized
switchback: one actuator or tunable, randomized sign/order about baseline,
bounded duration/amplitude, explicit abort margin, cooldown, and no saturation
or manual activity. Highest-value tests are:

1. short vent pulses stratified by wind/orientation, temperature and absolute
   humidity gradient to identify exchange;
2. fan1 versus fan1+fan2 entry at matched hot/solar events;
3. fog versus one-zone mist at matched hot/dry events with branch water;
4. pulse-gap before pulse-duration changes; and
5. an effectful bounded night VPD/dehumidification lever.

This is cleaner than moving 19 knobs. All 18 completed `SOLAR_MAX` one-shots in
this epoch already targeted moisture response and reached readback—nine fog
threshold, six fog-off dwell, and three pulse-gap changes—so those levers have
an observed operational mechanism worth isolating.

### 9.3 Measurement and actuation priorities

| Priority | Addition | Why it changes what can be controlled or proven |
|---|---|---|
| P0 | Branch flow meters for fog, climate mist, and irrigation; per-actuator power/current and gas flow | Converts runtime proxies into delivered resource evidence and prevents shared water use from being attributed to climate policy. |
| P0 | Vent limit/position, fan current/airflow, fog pressure/flow | Distinguishes a command from physical authority and supports calibrated action-response models. |
| P1 | Calibrated center-canopy and vertical T/RH/VPD | The current center outcome is a house-average proxy; spatial response and east-without-mister limitations are otherwise hidden. |
| P1 | Canopy quantum PAR/PPFD and qualified DLI | Broadband/lux proxies cannot prove crop light dose or quantify shade/lighting tradeoffs. |
| P1 | Canopy IR temperature plus leaf-wetness/dew proxy | Air dew margin can miss cold leaves; controlled wetness sensing has been used to trigger greenhouse dehumidification and increase condensation safety margin ([Seginer and Zlochin, 1997](https://doi.org/10.1016/S0168-1923(96)02387-8)). |
| P1 | Root moisture/temperature/EC in every served zone, plus drain/runoff flow and EC | Enables root-zone state, water balance, salt accumulation, and crop-response eligibility instead of south/west-only proxies. |
| P2 | Reference NDIR CO2 | Makes the existing analog CO2 signal calibratable and allows later carbon-assimilation/economic control; do not optimize from the present unqualified signal. |
| Hardware option | VFD fans, position-controlled vent, variable-pressure fog | Creates continuous authority for efficient control. A commercial pepper-greenhouse experiment reported VFD fan energy averaging 0.64 of on/off operation over one month with nearly equal daytime temperature and humidity ratio ([Teitel et al., 2004](https://doi.org/10.1016/S0196-8904(03)00147-X)). |

Sensor placement, calibration, quality, and mechanical coefficients are
commissioned/versioned inputs, never ordinary AI tunables.

### 9.4 Later controller: cascaded robust MPC, not direct AI relays

After the twin, response model, atomic lineage, and meters pass their gates,
the strongest next controller is a host-side robust/scenario economic MPC every
5–15 minutes with a two-to-six-hour horizon. Its state would contain indoor
temperature, humidity ratio, thermal mass, and optional canopy/root state;
disturbances would include as-of forecast ensembles for outdoor temperature,
humidity ratio, solar, wind, and solar phase. It should emit a short-lived
climate envelope, certified template, or duty bounds. The ESP32 FSM remains the
low-level safety controller and relay sequencer.

This matches recent cascaded greenhouse research in which an economic MPC sets
bounds for a legacy rule controller, reporting negligible **simulated** loss
versus direct actuator EMPC ([Panagopoulos et al.,
2025](https://doi.org/10.1016/j.ifacol.2025.11.828)). Robustness is essential:
sample-based greenhouse MPC research found that 20% parameter uncertainty
materially changed predicted yield, ventilation, and heating demand
([Boersma et al., 2022](https://doi.org/10.1016/j.ifacol.2022.11.135)). Other
greenhouse MPC studies jointly optimize energy, water, CO2, and climate, but
their simulation savings are design evidence—not a transferable Verdify
savings claim ([Lin, Zhang, and Xia,
2021](https://doi.org/10.1016/j.apenergy.2021.117163)).

The objective remains lexicographic: hard firmware and condensation/water
constraints; robust climate noninferiority; only then measured water,
electricity/gas, cycles, and planner compute cost. Solver timeout,
infeasibility, stale/OOD inputs, excessive uncertainty, or missing readback
returns atomically to Frozen-FSM.

Do not deploy end-to-end reinforcement learning or Bayesian optimization over
the 40-dimensional Tier-1 proposal surface now. The physical model is rejected, treatment
lineage is non-atomic, delivery is unreliable, and resource outcomes are
incomplete. Safe optimization methods are useful later for a two-to-four
dimensional family of pre-certified templates with a known safe seed and
separate safety constraints ([Sui et al.,
2015](https://proceedings.mlr.press/v37/sui15.html); [Berkenkamp, Krause, and
Schoellig, 2021](https://doi.org/10.1007/s10994-021-06019-1)). Firmware rails
and physical aborts remain the guarantee; a model calling itself “safe” does
not replace them.

## 10. Bottom line

The exact-firmware second pass preserves the first report's core caution: no
causal runtime, resource, cost, climate, or crop benefit is established, and
the PID physical counterfactual remains rejected.

It also advances the question materially:

> In the best supported same-firmware comparison available, fresh adaptive
> operation is associated with both lower VPD deviation and lower aggregate
> actuator demand than a stale retained policy under closely matched weather.

That is not yet a claim of AI savings. It is a concrete, reproducible signal
that identifies the VPD-runtime frontier as the right place to run a causal
AI-versus-Frozen-FSM test. Repairing forecast truth, atomic vector lineage,
scoring, sparse planning, and the firmware twin will increase both the likely
operational benefit of AI and the ability to prove it.

The proposed next phase is therefore an implementation program followed by a
locked experiment: one assignment-aware arbiter, an atomic device-confirmed
49-field policy vector, exposure lineage, calibrated measurements and twin;
then seven days of A/A qualification and 15 blinded `XY|YX` day pairs resolved
to physical `AB|BA` only by the restricted assignment service. The
30-day result is a large-effect screen, not a universal verdict: it advances
the named AI template selector only if climate noninferiority, nine-device command-duty
superiority, safety, and integrity all pass. An inconclusive result closes this
fixed study and informs a separately preregistered new trial; it is not an
invitation to keep sampling against the same ordinary boundary.

## Appendix A — reproduction

Keep raw operational exports outside Git:

```bash
VERDIFY_DB_BACKEND=kube \
  research/planner-efficacy/extract-current-firmware.sh \
  /tmp/planner-current-fw-inputs
```

Run the bounded core analysis:

```bash
uv run --project research/planner-efficacy \
  research/planner-efficacy/audit.py \
  --climate /tmp/planner-current-fw-inputs/climate_15m.csv \
  --equipment /tmp/planner-current-fw-inputs/equipment_transitions.csv \
  --daily /tmp/planner-current-fw-inputs/daily_outcomes.csv \
  --plans /tmp/planner-current-fw-inputs/plans.csv \
  --output /tmp/planner-current-fw-core.json \
  --train-start 2026-07-11T06:00:00+00:00 \
  --train-end 2026-07-31T06:00:00+00:00 \
  --eval-start 2026-07-31T06:00:00+00:00 \
  --eval-end 2026-08-14T06:00:00+00:00 \
  --factual-start 2026-07-11 \
  --factual-end 2026-08-14 \
  --plan-start 2026-07-10T21:03:12.991915+00:00 \
  --plan-end 2026-08-14T06:00:00+00:00 \
  --firmware-version 2026.7.10.1500.09ee886 \
  --firmware-epoch-start 2026-07-10T21:03:12.991915+00:00 \
  --era-label 'exact live firmware 2026.7.10.1500.09ee886; complete local days only' \
  --skip-historical-match
```

Run the mechanism/interruption supplement:

```bash
uv run --project research/planner-efficacy \
  research/planner-efficacy/epoch_analysis.py \
  --climate /tmp/planner-current-fw-inputs/climate_15m.csv \
  --equipment /tmp/planner-current-fw-inputs/equipment_transitions.csv \
  --daily /tmp/planner-current-fw-inputs/daily_outcomes.csv \
  --forecast-response /tmp/planner-current-fw-inputs/forecast_response.csv \
  --waypoints /tmp/planner-current-fw-inputs/waypoints.csv \
  --forecast-vpd-accuracy /tmp/planner-current-fw-inputs/forecast_vpd_accuracy.csv \
  --effective-tunables /tmp/planner-current-fw-inputs/effective_tunables.csv \
  --trigger-outcomes /tmp/planner-current-fw-inputs/trigger_outcomes.csv \
  --output /tmp/planner-current-fw-supplement.json
```

Validation:

```bash
uv run --project research/planner-efficacy \
  pytest research/planner-efficacy/tests
```

The checked-in JSON manifests record exact hashes, row counts, bounds, model
gates, matching diagnostics, control reuse, sensitivity pools, forecast
response, and waypoint accounting. Exports use sequential read-only
transactions rather than one database-wide MVCC snapshot. The outcome cutoff
is fixed; mutable workflow fields reflect extraction time.

## Appendix B — interpretation guardrails

- “Stale” means retained formerly valid policy during a shared delivery
  interruption, not a clean no-AI or neutral-FSM treatment.
- A populated action `plan_id` is server inference, not device provenance.
- Matching balances observed weather, not latent greenhouse state or carryover.
- Correlation with as-of forecast proves response, not forecast value or outcome
  benefit.
- Readback proves configuration landed, not delivered heat, airflow, or water.
- Open-loop replay proves different requested actions, not alternative physical
  trajectories.
- Backcast crop-band functions are a common evaluation corridor, not an
  immutable historical target record.
- Partial electricity, whole-equipment modeled energy, gas nameplate estimates,
  and facility energy are distinct scopes.
- Current history has no reliable crop-yield, disease, root-wetness, leaf-VPD,
  or planner-compute-cost endpoint.
