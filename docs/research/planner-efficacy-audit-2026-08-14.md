# Planner efficacy audit: executed AI plans versus fixed-setpoint PID

- **Outcome cutoff:** 2026-08-14 06:00 UTC (midnight America/Denver)
- **Inventory/extraction snapshot:** 2026-08-14 16:14 UTC
- **Primary evaluation window:** Denver-local 2026-07-15 through 2026-08-13
- **Source revision audited:** `c5607d9acceffa2e32b43978670d1baafc2d5c5a`
- **Method:** read-only production telemetry, source/architecture audit,
  deterministic controller replay, weather matching, and held-out closed-loop
  system identification
- **Core PID analysis:** [`research/planner-efficacy/`](../../research/planner-efficacy/README.md)
- **Executed result manifest:** [`results-2026-08-14.json`](../../research/planner-efficacy/results-2026-08-14.json)

## Executive answer

The historical record does **not** establish that the AI planner saves runtime,
electricity, gas, water, money, or crop loss relative to a deterministic
controller. It also does not establish that PID would have been better. The
honest result is **not estimable from the current retrospective data**.

There is one strong but narrower result: on the actual 30-day indoor-climate
trace, the nominal audit-specified fixed-setpoint PID would have *requested*
**3,725.6 climate-actuator minutes/day**, versus **2,305.4 executed actuator
minutes/day** (**+61.6%**). Across 12 prespecified audit PID target/gain
combinations, requested duty was **3,618.2–5,403.8 minutes/day**, or
**56.9–134.4% above executed duty**. This is an open-loop decision replay: it
proves that these 12 audit PID policies request more aggregate duty when fed the
factual trajectory. It does **not**
prove the runtime or climate that would result after those different actions
changed the greenhouse state.

We attempted that physical counterfactual with two actuator-aware models. Both
failed the declared held-out rollout gates, and they disagreed on the sign of
the temperature result. Publishing their numerical “savings” would turn model
error into a business claim, so those estimates are rejected.

The most important attribution result is architectural: Verdify is not “AI
versus PID” today. The fast controller is already deterministic—a one-second,
band-first staged-hysteresis FSM. The DB owns the crop/solar target corridor,
firmware owns relay selection and safety, and AI changes bounded policy
tunables. A literal PID is a new controller and a different target-chasing
objective. The experiment that isolates the value of AI is therefore **AI
planner versus the same firmware with its 49 planner-owned tunables frozen**;
that comparison has never been randomized or shadow-logged.

### Decision summary

| Question | Evidence-backed answer |
|---|---|
| Did executed operation use less relay duty than the 12 audit PID policies would request on the same observed trace? | **Yes, decisively in open-loop replay:** PID demand was 56.9–134.4% above executed demand. Equivalently, executed demand was 36.3–57.3% below PID. This is command demand, not a closed-loop outcome. |
| Did AI itself cause that reduction? | **Unknown.** Most of the difference is plausibly the deterministic floating-corridor FSM versus PID target chasing. No frozen-FSM AI ablation exists. |
| Did AI improve climate versus PID? | **Not estimable.** Both closed-loop response models failed; temperature effects changed sign across models. |
| Is AI saving electricity, gas, water, or cost? | **Not proven.** Whole-runtime energy has 0 scoring-eligible days; electricity metering is partial-scope; gas is nameplate-modeled; water has no valid PID counterfactual; cost is missing. |
| Is AI compute/runtime overhead known? | **No.** Historical LLM token, inference, energy, and price telemetry is absent. Plan delivery latency is measurable, but is not greenhouse runtime. |
| What benefit is proven? | Bounded adaptive policy changes were produced and sometimes confirmed under deterministic safety rails. No causal operational benefit is yet proven. |

## 1. What the system actually controls

The current control boundary is explicit in
[`CONTROL-ARCHITECTURE.md`](../CONTROL-ARCHITECTURE.md):

```text
crop/solar band (deterministic DB math)     AI plan (bounded policy tunables)
                     \                       /
                      dispatcher + clamps
                               |
                    ESP32 band-first FSM
                   1-second relay decisions
                               |
                     climate + relay truth
```

- The target corridor is deterministic and solar-phase-aware.
- The AI cannot move the current crop-band anchors; it tunes response posture.
- The dispatcher is the sole device writer and confirms device readback.
- Firmware performs normalized temperature/VPD arbitration, dwell, interlocks,
  and safety locally, including when the planner or network is unavailable.
- The deployed algorithm is explicitly **not PID**. PID needs gains, sample
  time, anti-windup, actuator allocation, saturation, dwell, and safety rules;
  no historical Verdify PID specification exists.

This matters for interpretation. A comparison between executed operation and
PID mixes at least two effects:

1. the value of floating-corridor staged-hysteresis control versus fixed target
   tracking; and
2. the marginal value of AI-selected tunables inside the existing controller.

Only the second is an AI efficacy question. Historical data does not isolate it.

## 2. Evidence inventory and study cohorts

The audit queried production read-only. Raw telemetry is not committed. The
core PID package records hashes, row counts, bounds, and aggregate model results
for its four inputs. Broader inventory and lineage facts came from read-only
snapshot queries and source inspection; the package does not claim to reproduce
every table in this report. Its four exports use sequential read-only
transactions, not one database-wide MVCC snapshot.

The outcome window closes at 06:00 UTC. Mutable journal lifecycle, validation,
and score fields reflect the 16:14 extraction snapshot; this is why the live
367-plan inventory and the 365 rows created before the outcome cutoff are
reported as distinct populations.

### 2.1 Historical inventory

| Evidence | Rows | Available range | Use |
|---|---:|---|---|
| Climate | 389,737 | 2025-08-14–2026-08-14 | Indoor state, outdoor weather, solar |
| Equipment transitions | 234,547 | 2024-11-17–2026-08-14 | Transition-derived runtime/cycles |
| Device diagnostics | 264,098 | 2026-02-12–2026-08-14 | Firmware/configuration epochs |
| Setpoint snapshots | 22,863,474 | 2026-05-14–2026-08-14 | Effective readback evidence |
| Setpoint changes | 676,819 | 2025-08-05–2026-08-14 | Requested/sent/confirmed delivery |
| Plan journal | 367 plans | 2026-03-24–2026-08-14 | Plan hypotheses/lifecycle |
| Planned waypoints | 102,887 rows | 2026-03-24 onward | Intended policy; not execution proof |
| Climate action log | 221,542 | 2026-05-25–2026-08-14 | Firmware action context |
| Daily outcomes | 375 days | 2025-08-05–2026-08-14 | Stored factual rollups |
| Forecast vintages | 645,888 targets | Vintages only from 2026-07-15 | As-of forecast evaluation |

The 367 journal plans are not 367 independent experiments. Plans overlap,
later plans supersede earlier plans, and one plan governed for almost 6.25 days.
`v_active_plan` resolves per parameter, so a live policy can combine several
plan IDs. Preemptive deterministic rules are interleaved with journal plans.

### 2.2 Structural breaks

The following cohorts are descriptive, not exchangeable experimental groups:

| Cohort | Complete days | Mean compliance | Mean outdoor daily max | Stress hours/day |
|---|---:|---:|---:|---:|
| Pre-planner, Aug 14–Mar 23 | 222 | 71.69% | 62.9°F | 4.19 |
| Legacy planner, Mar 24–May 24 | 62 | 65.74% | 69.0°F | 10.74 |
| ClimateIntent, May 25–Jun 29 | 36 | 75.18% | 84.5°F | 13.65 |
| Runtime evidence before forecasts, Jun 30–Jul 14 | 15 | 61.21% | 92.7°F | 20.48 |
| Stable full-evidence window, Jul 15–Aug 13 | 30 | 65.97% | 92.7°F | 22.72 |

Seasonal load rises sharply across those rows. Firmware changed dozens of times;
the screen/window opened around June 19; crop bands, scoring definitions,
sensors, plan lifecycle, and resource accounting also changed. Band-anchor
records are not validity-versioned, and some daily fields were backfilled under
newer algorithms. A whole-history before/after difference is therefore not a
causal AI estimate.

## 3. Factual performance in the strongest 30-day window

The primary window maps exactly to UTC `[2026-07-15 06:00,
2026-08-14 06:00)`. It has stable firmware, complete forecast vintages, served
targets, mature runtime materialization, and completed local days.

### 3.1 Climate and actuation

| Factual endpoint | Result |
|---|---:|
| Plans | 72; all scored, anchored, trigger-linked, and ClimateIntent-bearing |
| Mean / median governed interval | 10.0 h / 5.61 h |
| Climate rows | 43,017; 98.01% of expected minutes; maximum gap 9 min |
| Attributable compliance | 65.97% |
| Temperature graded compliance | 49.25% |
| VPD graded compliance | 41.24% |
| Total graded stress | 681.46 h, or 22.72 h/day across axes |
| Six core climate-actuator runtime | 65,837.1 device-minutes |
| Six core relay cycles | 3,763 |
| Transition-derived all-equipment runtime | 95,195.7 device-minutes; 536/540 equipment-days eligible |
| Short cycles under five minutes | 5,743 |

“Device-minutes” sum concurrently running devices; they are not elapsed wall
time. The 15-minute reconstruction used for PID replay includes the three
climate misters and averages 2,305.4 climate-actuator minutes/day.

These numbers describe what happened. They do not say what would have happened
without AI.

### 3.2 Resource evidence

| Resource | Factual evidence | Eligibility / limitation |
|---|---:|---|
| Water meter | 7,626 gal | 25/30 meter days eligible; 7 gap events and 1 reset |
| Attributed water ledger | Same total under attribution classes | 23/30 days scoring-eligible; two degraded and five discontinuous days |
| Partial Shelly electricity | 225.07 kWh | 30/30 eligible for its named **two-channel** scope |
| Whole controlled-equipment model | 475.89 kWh | 0/30 scoring-eligible because coefficients are uncertain |
| Gas | Runtime × 75,000 BTU/h nameplate | Not metered |
| Daily total cost | Missing | `cost_total` is null 30/30; estimated cost uses ineligible modeled inputs |

The 225.07 kWh and 475.89 kWh figures have different scopes and cannot be
treated as disagreement or facility totals. Production correctly assigns zero
resource weight to the planner score for these days. There is no eligible
counterfactual resource endpoint.

## 4. Was the AI policy actually executed and measurable?

Plan authorship is not execution. The binding lineage is:

```text
trigger → delivery → journal → intended waypoints → accepted write
        → confirmed device readback → firmware action → relay transition
```

### 4.1 Delivery and attribution findings

- The journal contains 367 bounded-lifecycle plans; 365 had a validation and
  subjective outcome score at the live snapshot.
- The trigger ledger has 799 rows: 185 `plan_written`, 219 timed out, and 73
  delivery failures, alongside acknowledgements and other terminal actions.
- The delivery log has 2,698 rows, including 245 full plans and 76 one-shot
  tunable completions, but also 593 timeouts and 1,224 gateway-delivery failures.
  The denominator mixes expected actions, acknowledgements, retries, and legacy
  semantics, so it is not a single plan-success rate.
- In the strong window, 30,987 plan-source setpoint-change rows had 6,510
  confirmed, 24,275 failed, and 202 superseded dispositions. These are row/retry
  dispositions, not independent plan failures; they nevertheless make intended
  waypoints unsafe as treatment evidence.
- All 45,769 strong-window action rows carried a plan ID, but only 23,866
  (52.1%) joined a journal AI plan. Deterministic/preemptive plan IDs are
  interleaved.
- Median plan-written latency was about 124 seconds in July and 141 seconds in
  August (p95 251 and 294 seconds). Firmware still controls locally every second;
  planner latency is episodic supervisory latency, not relay-loop latency.

### 4.2 The built-in planner score is not efficacy evidence

`v_plan_accuracy` is a renamed projection of the planner's own outcome score:
`accuracy_pct = outcome_score × 10`; it is not measured waypoint or forecast
accuracy. The plan-to-daily-outcome view is documented as directional, not
causal.

Calibration is poor for the current local planner:

| Cohort | Anchored plans | Mean self-score | Mean deterministic anchor | Mean bias | Within ±2 |
|---|---:|---:|---:|---:|---:|
| Local planner, all history | 112 | 4.90 | 2.81 | +2.24 | 57 |
| Strong 30-day window | 72 | 5.07 | 2.72 | +2.40 | 38 |
| Earlier Opus cohort | 95 | — | — | +0.46 | 83 |

Self-score, narrative outcome text, and the mutable composite planner score are
therefore excluded from efficacy endpoints.

## 5. Deterministic PID counterfactual

### 5.1 PID-C specification

Because no Verdify PID existed, this audit specifies a study policy rather than
retroactively assuming a favorable controller:

- 15-minute study step;
- fixed targets of **72°F** and **0.95 kPa VPD** for the nominal audit PID;
- selected training-only gains: temperature `Kp=0.20`, `Ki=0.01`, `Kd=0.02`;
  VPD `Kp=1.50`, `Ki=0.05`, `Kd=0.10`;
- clamped integrators for anti-windup;
- one coordinated allocator: staged heat, vent/fans, fog-first wetting, then
  rotated misters; outdoor-moisture-aware dehumidification;
- 45°F/95°F safety preemption and an 8°F dew-margin wetting block;
- observed grow-light duty retained as an external input.

The allocator emits fractional 15-minute duty requests. It does not resolve
sub-step relay dwell, starts, or firmware interlocks and is not a deployable
controller. Its open-loop output is therefore actuator-equivalent command
demand, not proof that hardware could execute the exact request sequence.

Sensitivity covers P-only, conservative, balanced, and aggressive gains at
three fixed target pairs: current specification (72/0.95), legacy-bound
midpoint (67.5/1.15), and warm/dry (76/1.10). The nominal specification was
ranked only on the calibration window, not the evaluation outcomes, but the
ranking used the same model classes that later failed validation. It is not a
validated optimum.

This coordination represents temperature/moisture coupling more faithfully
than two independent study loops. Its simplified safety preemption is not a
substitute for the firmware's full invariants, and it still pursues a different
objective from Verdify's solar-phase corridor.

### 5.2 Open-loop decision replay: valid but narrow

PID consumes each factual indoor state, but its actions do not alter the next
state. This can prove decision divergence and command demand.

| Policy | Requested/executed actuator-min/day | Difference from executed |
|---|---:|---:|
| Executed policy | 2,305.4 | — |
| Nominal audit fixed PID | 3,725.6 | +1,420.2 (+61.6%) |
| Least-demanding PID sensitivity | 3,618.2 | +1,312.8 (+56.9%) |
| Most-demanding PID sensitivity | 5,403.8 | +3,098.4 (+134.4%) |

This is evidence that these 12 audit policies *ask for* substantially more
command duty on the observed trace. It is consistent with the intended benefit
of floating inside a tolerance corridor. It is not proof that the AI planner
caused the difference, nor that the extra PID duty would or would not improve
the greenhouse.

### 5.3 Closed-loop model attempt

The state was `[indoor temperature, absolute humidity]`; VPD was derived from
those states. Inputs were heater, vent, fan, fog, mister, and grow-light duty.
Disturbances were outdoor temperature/absolute humidity, solar, wind, and solar
phase. Models were trained on the open-window calibration period June 20–July
10, then recursively rolled through 30 unseen days. Every day began from its
observed initial state; PID consumed only its own simulated indoor state. Future
observed indoor measurements were never injected.

Two model classes were required to pass:

1. regularized actuator-aware ARX/ridge; and
2. nonlinear histogram gradient boosting with the same state/action context.

Declared acceptance required recursive mean MAE ≤2.5°F and ≤0.25 kPa, both axes
better than persistence, residual lag-1 magnitude <0.5, at least 90% PID
state-action support, and stable effect direction across accepted models.

| Validation | Ridge ARX | Nonlinear HGB | Persistence |
|---|---:|---:|---:|
| Recursive temperature MAE | 5.10°F | 2.45°F | 5.51°F |
| Recursive VPD MAE | 0.314 kPa | 0.331 kPa | 0.240 kPa |
| 24 h endpoint temperature MAE | 4.18°F | 5.47°F | 1.90°F |
| 24 h endpoint VPD MAE | 0.429 kPa | 0.653 kPa | 0.182 kPa |
| PID state-action support | 98.1% | 94.7% | — |
| Residual gate | Fail (lag-1 0.736 temp, 0.516 moisture) | Pass | — |
| Overall model gate | **Fail** | **Fail** | — |

Support was adequate; plant-model fidelity was not. More importantly, rejected
models contradicted one another:

| Rejected PID-minus-executed estimate | Ridge | Nonlinear HGB |
|---|---:|---:|
| Climate actuator minutes/day | +1,707.7 | +620.3 |
| Temperature degree-hours outside corridor/day | **−40.11** | **+2.97** |
| VPD kPa-hours outside corridor/day | −0.65 | −0.36 |
| Modeled climate electricity/day | +1.93 kWh | +6.46 kWh |

One model says PID materially improves temperature; the other says it worsens
temperature. The factual executed trace is 14.49 temperature degree-hours/day
outside the backcast current corridor, while the models replay it as 98.39 and
4.49 respectively—an immediate calibration warning. None of the right-hand
numbers is an eligible outcome estimate.

This failure is expected in a heavily controlled, coupled greenhouse. Published
greenhouse system-identification work models temperature and humidity jointly
and emphasizes actuator inputs and dynamic validation; multivariable PI work
also needs decoupling and anti-windup rather than naïve independent loops
([Cunha et al.](https://doi.org/10.1016/S1474-6670(17)41244-4),
[García-Mañas et al.](https://doi.org/10.1016/j.ifacol.2023.10.668)).

## 6. Historical matching sensitivity

As a separate observational check, each AI day was matched with replacement to
the nearest pre-AI day on outdoor mean/range, solar, outdoor absolute humidity,
and wind. This used 175 pre-AI and 139 AI days. Pre-AI solar remained partly
clear-sky-imputed.

Matching did not achieve credible balance: post-match standardized differences
were 0.26 for outdoor temperature and 0.57 for solar, far above a strict 0.10
balance target. The 139 AI days reused only 39 control days, with one control
used 45 times. Serial dependence and heavy reuse invalidate an IID paired-day
bootstrap, so no inferential interval is reported. The descriptive differences
were:

| AI minus matched pre-AI | Descriptive mean |
|---|---:|
| Climate actuator minutes/day | +462.9 |
| Temperature compliance | +11.31 points |
| VPD compliance | +0.82 points |
| Temperature degree-hours outside/day | −12.66 |
| VPD kPa-hours outside/day | −6.09 |

The simultaneous increase in runtime and apparent reduction in fixed-corridor
loss may represent spending more resources under more severe weather, firmware
improvement, sensor/scoring changes, AI, or all of them. Because balance and
configuration exchangeability fail, these differences are not attributed to
AI.

Longitudinal observational data with adaptive treatment and prior-treatment-
affected state need explicit identification assumptions; ordinary before/after
or regression adjustment is insufficient ([Hernán and Robins](https://www.hsph.harvard.edu/miguel-hernan/wp-content/uploads/sites/1268/2024/04/hernanrobins_WhatIf_26apr24.pdf)).
Likewise, historical policy evaluation becomes unreliable when the evaluation
policy leaves logged support ([Thomas and Brunskill](https://proceedings.mlr.press/v48/thomasa16),
[Sachdeva et al.](https://arxiv.org/abs/2006.09438)).

## 7. What is actually proven about AI

### Proven properties—not yet proven outcome benefits

- AI produced a substantial auditable plan history and, in the strongest
  window, 72 structured ClimateIntent plans with deterministic anchor scores.
- The current planner is bounded to 49 policy tunables; deterministic bands,
  single-writer delivery, device clamps, firmware arbitration, dwell, and hard
  safety remain outside model authority.
- Some plan writes reached confirmed device readback and AI-linked action rows
  exist, so the planner is not merely a paper design.
- The architecture degrades safely to deterministic/offline control when AI or
  delivery fails. This containment is an engineering benefit of the system
  design, not evidence that AI improves greenhouse outcomes.

### Suggestive but not causal

- Fixed-setpoint PID is consistently more aggressive in observed-state replay.
- Weather-matched history suggests better fixed-corridor temperature outcomes
  alongside more runtime, but matching is imbalanced and configuration changes
  are uncontrolled.
- AI has access to forecasts and historical context that a fixed PID lacks, but
  no randomized ablation demonstrates incremental forecast value.

### Not established

- AI-specific relay-runtime savings versus frozen deterministic firmware;
- electricity, gas, water, or monetary savings;
- better temperature/VPD outcomes versus PID;
- crop quality, growth, flowering, yield, disease reduction, or root dry-down;
- planner compute energy, inference cost, or net financial return;
- generalization beyond this greenhouse and its open-window summer regime.

The historical OpenClaw usage table contains only 1,178 successful MCP status
calls from May 1–11, with zero token fields. `planner_graph_runs` is empty. No
durable LLM token, model latency, GPU/CPU energy, or price ledger exists, so AI
overhead cannot be subtracted from any claimed savings.

## 8. Recommended experiment that can answer the question

The primary comparator should be **Frozen-FSM**, not PID:

1. pin the same firmware SHA, crop-band revision, schedule, safety rails,
   hardware/window state, and dispatcher;
2. freeze all 49 planner-pushable values to versioned defaults, retaining
   `band_track_fraction=0` and the current band-first FSM;
3. compare against AI using the exact same infrastructure;
4. keep the PID-C policy in shadow first; consider a separately safety-reviewed
   live PID arm only after it passes firmware replay and command-capacity gates.

### 8.1 Instrumentation prerequisite

Before randomization:

- persist the full effective tunable-vector hash, assignment ID, plan ID,
  confirmation time, firmware SHA, target-anchor revision, and mechanical epoch
  with every action/outcome interval;
- make target anchors validity-versioned;
- retain forecast vintages and use only forecasts fetched before each decision;
- record LLM model, tokens, wall time, retries, and attributable cost;
- repair row-level plan delivery reliability and distinguish request, admission,
  send, readback confirmation, action, and physical feedback;
- qualify whole-equipment electricity/gas evidence or keep those endpoints
  explicitly modeled;
- preserve raw water quality, meter-gap, reset, and attribution eligibility.

### 8.2 Switchback design

- Shadow both policies for at least two weeks and require zero safety-invariant
  violations plus acceptable action support.
- Randomize AI versus Frozen-FSM in whole-solar-day blocks, stratified by
  forecast temperature/solar load. Use multi-day blocks or prespecified washout
  because greenhouse thermal/moisture carryover violates independent-day
  assumptions.
- Run for at least eight weeks, then extend based on a blinded variance/power
  update; do not stop opportunistically on favorable results.
- Use exact randomization inference over blocks. Switchback design must account
  for the order and duration of carryover, as developed by
  [Bojinov, Simchi-Levi, and Zhao](https://doi.org/10.1287/mnsc.2022.4583).
- Automatically revert to the frozen vector on delivery failure; safety rails
  remain identical in both arms.

### 8.3 Preregistered endpoints and decision rule

Primary climate endpoints:

- attributable temperature and VPD corridor compliance;
- controller-miss minutes, separately from physically unachievable minutes;
- degree-hours / kPa-hours outside the corridor and safety-tail excursions.

Primary efficiency endpoints:

- transition-derived runtime by device, starts, short cycles, and saturation;
- quality-qualified water;
- electricity/gas only for a declared, eligible measurement scope.

Decision rule: call AI operationally beneficial only if climate is noninferior
under a prespecified margin, no safety endpoint worsens, and the paired block
interval for at least one eligible runtime/resource endpoint excludes no
benefit. Keep endpoints separate; do not hide missing resources in a composite
score. A null or underpowered result remains inconclusive.

## 9. Bottom line

The strongest conclusion is not “AI wins” or “PID wins.” It is:

> Verdify's executed floating-corridor control asks substantially less of the
> actuators than these 12 audit-specified fixed-setpoint PID policies ask on
> the same observed state trace,
> but current history cannot identify the resulting PID climate or isolate how
> much of that difference comes from AI rather than deterministic firmware.

No resource-savings or crop-benefit claim is currently provable. The historical
data has nevertheless done something valuable: it identifies exactly why the
claim is not yet supportable, supplies a reproducible rejection rather than a
speculative estimate, and defines the bounded Frozen-FSM switchback that can
turn the next operating period into causal evidence.

## Appendix A — validity gates and falsification checks

A publishable AI-attribution or physical-counterfactual conclusion requires
the broader evidence checks below. The automated `counterfactual_eligible` flag
in the result manifest implements the model rollout, residual, support, and
selected-policy direction gates only; it does not automate lineage, epoch, or
input-completeness review. A future `true` flag would therefore still require
those human-audited evidence checks.

The broader evidence standard requires:

- confirmed readback rather than intended plan rows;
- exact firmware/mechanical/crop/scoring epochs;
- complete initial actuator state and weather coverage;
- recursive 1 h, 6 h, and 24 h validation, not one-step fit alone;
- residual autocorrelation checks;
- historical state-action support for PID;
- stable effect direction across accepted model classes for the selected PID
  specification;
- whole-day bootstrap units, not minute-level pseudo-replication.

Future work should also run future-plan placebos, outdoor weather/solar negative
controls, sham trigger times, failed/unconfirmed plan controls, alternate
resolutions, exclusion of transition/manual-operation periods, and explicit
anti-windup/saturation/cycle invariants. A failed gate means “not estimable,”
not “no effect.”

## Appendix B — evidence caveats

- `climate_action_log.plan_id` is inferred from the latest active plan, not
  supplied by the device.
- Action-effectiveness windows overlap; they are not isolated interventions.
- Historical replay against recorded sensors is open-loop unless alternative
  actions change the simulated next state.
- Current canonical bands are backcast only as a common evaluation corridor;
  they are not claimed to be the immutable historical target.
- House-average air VPD is not leaf VPD. There is no validated root-wetness,
  canopy PAR, leaf-temperature, crop-yield, or disease endpoint.
- Metered partial electricity, modeled whole-equipment energy, and facility
  energy are distinct scopes.
- Runtime proves relay command, not delivered airflow, heat, or water.
