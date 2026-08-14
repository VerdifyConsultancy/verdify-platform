# Planner efficacy, second pass: current-firmware epoch

- **Firmware:** `2026.7.10.1500.09ee886`
- **Firmware source:** `09ee886f1a6fbd6452064460e1a57e5dc1399a70`
- **First device readback:** 2026-07-10 21:03:12.991915 UTC
- **Outcome cutoff:** 2026-08-14 06:00 UTC (midnight America/Denver)
- **Complete-day window:** Denver-local July 11 through August 13, 34 days
- **Method:** exact-epoch extraction, open-loop controller replay, rejected
  held-out closed-loop models, as-of forecast response audit, and a matched
  analysis of an unplanned stale-policy interval
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

## 8. Experiments that can demonstrate net-positive AI value

### 8.1 Primary three-arm switchback

Hold firmware SHA, crop bands, safety, hardware/window state, meters, and the
single delivery path fixed. Randomize versioned assignments among:

1. **Frozen-FSM:** the firmware's deterministic loop with a named, explicitly
   versioned baseline effective vector, produced through the same compiler and
   delivery path and held fixed within an assignment—not transient boot
   defaults or a retained AI vector. That baseline must be designed, safety
   reviewed, and checksummed before randomization; no neutral constructor
   exists today;
2. **Deterministic forecast:** the same firmware plus deterministic forecast
   proposals, with no AI; and
3. **AI planner:** AI proposals plus the same arbiter, with no parallel writer.

An optional fourth shadow arm can remove forecast context from AI to measure
forecast information value.

Use whole-solar-day or multi-day blocks stratified by forecast heat, solar, and
outdoor absolute humidity. Declare washout for thermal/moisture carryover.
Analyze intention-to-treat by assignment and per-protocol by confirmed vector.

Primary endpoints:

- VPD and temperature severity outside the active corridor;
- safety-tail events and physically unachievable versus controller-miss time;
- six-core and nine-device runtime, cycles, and short cycles; and
- qualified water, then measured energy only when scope is eligible.

Call AI net-positive only if climate is noninferior, no safety endpoint worsens,
and a block-level interval supports benefit on at least one eligible
runtime/resource endpoint after accounting for planner compute cost.

### 8.2 Targeted hot/dry micro-interventions

All 18 completed `SOLAR_MAX` one-shots in this firmware epoch changed moisture
response and reached readback: nine lowered fog escalation, six shortened fog
off dwell, and three shortened mister pulse gaps. Randomizing one bounded lever
at comparable solar/VPD events would provide much cleaner attribution than
changing 19 parameters per plan.

Prioritize separate tests for:

- second-stage cooling/all-fans timing;
- fog versus mist duty per qualified gallon under hot/dry ventilation;
- pulse-gap before pulse-duration escalation; and
- night dryout/dehumidification using a genuinely effectful night lever.

Do not combine those levers until individual response and carryover are known.

## 9. Bottom line

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
