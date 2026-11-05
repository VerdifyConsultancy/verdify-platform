# Measuring the AI planner's advantage: experiment system state, study design and power

Track `planner-ab`. Issues: #1359 (research), #1360 (study build), #1361 (Planner section). Research date 2026-11-05. Read-only throughout.

## Question(s)

1. What does the experiment-v2 orchestrator (`experiment_orchestrator/`) and its DB do today, and what experiments have run, with what results?
2. Which outcome metrics should judge the AI planner: served-band compliance (#1358, PR #1372), energy (sign-corrected, #1362, PR #1369), water and safety events?
3. How much do those metrics vary day to day, and how many days or arm switches does a study need to detect a given effect once weather is accounted for?
4. Which arms, schedule, covariate adjustment and guardrails should the study use?
5. What must the dashboard's Planner section (#1361, approved as the seventh nav item) show?

## Bottom line

- **The experiment system has never run an assigned day.** It is built, deployed and switched off (`VERDIFY_COMPONENT_EXPERIMENT_ENABLED` off; the three pods log `orchestrator_inactive / capability_off`). Its one record is a draft bound to firmware from July, its selector is hard-wired to `api.openai.com`, and its one physical proof failed three times on 2026-08-28 before a facility safe closure ended it on 2026-09-30. It has produced no results. Keep its tables, lifecycle boundary loop, baseline restore path and freezer. Drop the OpenAI selector, the three hot/dry profiles, the beacon/HMAC blinding and the #641 proof gate.
- **The AI arm barely touches the house in winter today.** In the last 30 days the planner changed the device on about 10 of 31 days. Most of its writes were mister and fog thresholds, and wetting ran only about 28 min/day. It wrote `night_vpd_bias_kpa` and `band_track_fraction` zero times. The winter problem is VPD **below** band at night: 2-8% of 00:00-06:00 readings are in band. Unless the Cortex planner (#1350, #1366) uses its winter levers (`night_vpd_bias_kpa`, `vent_prefer_dp_delta_f`, `vent_exchange_fraction`, the enthalpy and heat-dwell tunables), the honest expected result is "no difference".
- **Design: a randomized day-level switchback**, Cortex AI planner versus the same firmware with the planner-owned tunables frozen at a locked baseline. Days come in adjacent pairs with a coin-flip order inside each pair. The study day runs 11:00 to 11:00 local, so each arm owns one whole night. The first 3 h after each boundary is a washout dropped from the climate metrics. On baseline days the planner runs propose-only, which records what the AI would have done.
- **Baseline vector: freeze the planner's own recent median**, not the registry defaults. The defaults are a different regime: `mister_engage_kpa` defaults to 1.6 against a 0.99 planner median, and `mister_all_delay_s` to 300 against 93. Against the median, the study measures what Jason asked about: the value of adapting to forecast and history, as opposed to a good fixed setting.
- **Primary outcome: VPD time in band at night against the served band**, using the #1358 definition (`fn_band_compliance_5m`), weather-adjusted. Temperature time in band is a non-inferiority guardrail with a 2-point margin. Heat minutes and sign-corrected electric kWh are reported as cost, not a gate, because Jason accepts more kWh. Water and wetting are summer outcomes. Safety events (40/100 °F) are too rare to power, so they are stop rules.
- **Power, from 31-60 days of history.** Weather explains 95% of heat-minute variance and 52-55% of night VPD variance. A 60-day study (30 pairs) detects about a **7-8 point change in night VPD time in band**, about a **10% change in heat minutes** (65-95 min/day) and a 2-point temperature non-inferiority margin. Detecting a 5-point VPD change or a 5% heat change needs 110-215 days, which is out of reach this winter. Plan for **60 days**, with one pre-registered look at 30 pairs.
- **Arms.** Run two arms: Cortex AI against the frozen baseline. "No planner" is not a separate regime: with no planner, the last values stay in force, which is a frozen vector with an undefined value. OpenAI is retired, so it cannot be an arm. A later study can put Cortex against the deterministic rule planner (the `forecast_action_log` engine), and model or prompt variants can run propose-only on every day at no physical cost.
- **Prerequisites before day 1:** #1350 cutover with `planner_inference_runs` recording, #1358/PR #1372 merged, #1362/PR #1369 merged, the #1365 band revision landed and then frozen, and the old record `45039c86-…` closed with a reason.

## Evidence from our data

All queries are read-only against `verdify-db` (`default_transaction_read_only=on`) and were run 2026-11-05 20:40-21:20Z. Days are America/Denver local.

### Windows used and why

| window | days | why |
|---|---|---|
| **winter** 2026-10-03..2026-11-02 | 31 | One firmware build (`2026.10.2.0637.620a218a`, live 10-02 07:51 to 11-03 05:35 local) in the heating regime. No `crop_band_anchors` edits since 2026-07-03. Manual writes only 1-4 a day at the end. Closest to the study season. |
| **heating season** 2026-09-06..2026-11-04 | 60 | A longer window for more stable variance estimates. It spans the 09-30/10-01 OTA burst and includes some warm September days. |
| **summer** 2026-07-11..2026-09-29 | 81 | One firmware build (`2026.7.10.1500.09ee886`). Used for summer (wetting and water) variance only. |

Excluded data: the CO2, PAR and south probes (the house averages `temp_avg`/`vpd_avg` are used; no zone metrics); `daily_summary.temp_compliance_pct`/`vpd_compliance_pct` (wrong, #1358); `daily_summary.kwh_total` (negative, #1362; recomputed below); kWh days with less than 22 h of meter coverage (10-08, 10-16, 10-22, 10-23).

```sql
-- firmware epochs
SELECT firmware_version, min(ts AT TIME ZONE 'America/Denver'), max(ts AT TIME ZONE 'America/Denver')
FROM diagnostics WHERE ts > now()-interval '120 days' GROUP BY 1 ORDER BY 2;
-- 2026.7.10.1500.09ee886     2026-07-10 15:03 → 2026-09-30 11:57
-- (9 builds on 2026-09-30..10-02)
-- 2026.10.2.0637.620a218a    2026-10-02 07:51 → 2026-11-03 05:35
-- 2026.11.3.0435.5e24d07c    2026-11-03 05:36 → 2026-11-05 10:08
-- 2026.11.5.0131.57fe2a2e    2026-11-05 10:10 →
SELECT date_trunc('day',updated_at AT TIME ZONE 'America/Denver')::date, count(*) FROM crop_band_anchors GROUP BY 1;
-- last edits 2026-07-03 (4 rows); none since
```

### 1. Experiment system state (re-run)

```sql
SELECT status, execution_phase, admission_state, component_enabled, updated_at FROM control_experiments;
-- draft | shadow | closed | f | 2026-09-30 17:50:12Z
SELECT (SELECT count(*) FROM control_assignments), (SELECT count(*) FROM experiment_v2_randomization),
       (SELECT count(*) FROM experiment_v2_outcomes), (SELECT count(*) FROM planner_inference_runs);
-- 0 | 0 | 0 | 0
```
```
kubectl -n verdify-prod get deploy: experiment-v2-freezer/lifecycle/selector 1/1 (70d); verdify-planner 1/1 (idle, #1350)
cronjobs: verdify-winter-feasibility, verdify-fixed-panel-native-freezer (00:30 daily, read-only observational)
```
#1359 has the full history: 6,263 component-outcome rows and 94 runtime faults on 2026-08-28/29, all from the failed #641 proof (78 `device_lost / state_replay_request_failed`), then the emergency hold on 09-28 and the safe closure on 09-30. **No experiment has produced an efficacy result.** The August audit (`docs/research/planner-efficacy-audit-2026-08-14.md`) concluded "not estimable from the current retrospective data". The only firm result it reports is open-loop: the FSM requests far less duty than a fixed-setpoint PID would, which says nothing about the AI.

Code facts that matter for reuse:
- `experiment_orchestrator/provider.py:31` and `settings.py:19` pin the provider to `api.openai.com`. `settings.py:154` refuses any non-global address that is not the official OpenAI host, so a local Cortex URL is rejected today.
- `research/planner-efficacy/switchback/v2_analysis.py` is a paired one-sided t bound with no covariate adjustment (`paired_upper_bound`, line 74).
- `v2_power.py` models selector dilution across three profiles (its 0.138 joint power and 150-pair figure come from assumed frequencies). It does not fit a two-arm study, so the power below is computed directly.
- `v2_randomization.py` makes HMAC-keyed XY/YX pair labels (`pair_labels`, line 93). The pair structure is right; the hidden secret is not needed.

### 2. What the planner actually changes (treatment intensity)

```sql
SELECT parameter, count(*), count(DISTINCT round(value::numeric,3)), min(value), max(value)
FROM setpoint_changes WHERE source='plan' AND ts > now()-interval '30 days' GROUP BY 1 ORDER BY 2 DESC;
-- mister_pulse_gap_s 181 | mister_engage_kpa 158 | min_fog_off_s 150 | mister_all_kpa 124 | mister_all_delay_s 91
-- mister_engage_delay_s 87 | fog_escalation_kpa 67 | mister_vpd_weight 57 | cool_stage2_over_high_f 53 | min_fog_on_s 50
-- vpd_watch_dwell_s 38 | cool_exit_hysteresis_f 38 | temp_hysteresis 38 | vpd_hysteresis 38 | dwell_gate_ms 37
-- mister_pulse_on_s 28 | vent_prefer_temp_delta_f 23 | cold_vent_guard_delta_f 21 | vent_prefer_dp_delta_f 21 | ... (24 params)
-- night_vpd_bias_kpa, band_track_fraction, heat_hysteresis, min_heat_on_s, vent_exchange_fraction: 0 writes
```
The planner may push 48 tunables (`PLANNER_PUSHABLE_REG`). Days with dispatched plan changes, from per-day `setpoint_changes` sources:
```
2026-10: plan rows on 10-01, 10-04, 10-05, 10-12, 10-17, 10-18 only; none 10-19..10-31
2026-09-06..09-25: plans written 2-4/day in plan_journal, 0 plan rows dispatched (values unchanged)
2026-11-01..11-04: plan rows every day (139-213)
```
`plan_journal` has 3-13 plans a day (mean 4.8 in the winter window), but the dispatcher writes a row only when a value changes. For long stretches the AI's vector did not move. That dilutes any effect, and it is why the baseline must be the planner's own median: the comparison is "adapting" against "not adapting".

Planner values against registry defaults (60 days of `source='plan'`):

| parameter | registry default | plan median | plan min-max |
|---|---|---|---|
| mister_engage_kpa | 1.6 | 0.993 | 0.79-1.28 |
| mister_all_kpa | 1.9 | 1.181 | 1.00-1.47 |
| mister_all_delay_s | 300 | 93 | 75-106.5 |
| mister_water_budget_gal | 300 | 120 | 100-180 |
| mister_pulse_on_s | 60 | 33.75 | 30-48.75 |
| temp_hysteresis | 1.5 | 1.96 | 1.6-2.23 |
| cool_stage2_over_high_f | 1.0 | 1.885 | 1.51-2.04 |
| vent_prefer_dp_delta_f | 5 | 4 | 3-12 |
| dwell_gate_ms | 300000 | 270000 | 210000-315000 |

### 3. Where the winter problem is (hourly profile, winter window)

```sql
-- 5-min house means graded against fn_band_setpoints(bucket) (the served band; #1358 shows audit vs reconstruction agree within 0.03 °F)
... GROUP BY local hour
```
| local hour | temp in band % | VPD in band % | VPD below band % | band temp_low °F | band vpd_low kPa |
|---|---|---|---|---|---|
| 00-06 | 99-100 | 2-8 | 92-98 | 59-61 | 0.72-0.74 |
| 07 | 99 | 23 | 77 | 63.1 | 0.76 |
| 09 | 99 | 82 | 18 | 67.3 | 0.80 |
| 11 | 97 | 93 | 6 | 71.7 | 0.84 |
| 12-16 | 98-99 | 84-90 | 3-6 | 73-75 | 0.86-0.88 |
| 18 | 99 | 79 | 20 | 71.6 | 0.84 |
| 21 | 100 | 40 | 60 | 65.9 | 0.79 |
| 23 | 100 | 14 | 86 | 62.5 | 0.75 |

Temperature is solved. Night VPD is the outcome with room to improve. Midday (11:00-16:00) is the calmest, most in-band stretch with the heaters off, so it is the right place for an arm boundary and its washout.

### 4. Daily metrics and their variance

Daily rows (138 days) come from three queries:
```sql
-- climate + served band + weather (one row per local day)
WITH r AS (SELECT time_bucket('5 min',ts) b, avg(temp_avg) t, avg(vpd_avg) v, avg(outdoor_temp_f) ot,
           avg(outdoor_rh_pct) orh, avg(solar_irradiance_w_m2) sol, avg(wind_speed_avg_mph) wind
           FROM climate WHERE greenhouse_id='vallery' AND ts >= '2026-06-20 06:00Z' AND ts < '2026-11-05 07:00Z' GROUP BY 1),
g AS (SELECT (b AT TIME ZONE 'America/Denver')::date d, r.*, f.*, fn_solar_phase(b+'150 s') < 2 is_day
      FROM r LEFT JOIN LATERAL fn_band_setpoints(r.b) f ON true)
SELECT d, 100*avg((t BETWEEN temp_low AND temp_high)::int) temp_in, 100*avg((v BETWEEN vpd_low AND vpd_high)::int) vpd_in,
       ... FILTER (WHERE NOT is_day) vpd_in_night, sum(greatest(vpd_low-v,0,v-vpd_high))/12 vpd_err_kpah,
       min/max/avg(ot), avg(outdoor VPD from ot,orh), avg(sol)*24/1000 sol_kwh_m2, minutes <45 °F / >95 °F
FROM g GROUP BY d;
-- equipment: daily_summary runtime_heat1_min, runtime_heat2_min, fog, vent, misters, water_used_gal, therms_estimated
-- electric kWh, sign-corrected per #1362/PR #1369 (|ch0|+|ch1|, 300 s hold):
WITH e AS (SELECT ts, abs(ch0_power_w)+abs(ch1_power_w) w, least(extract(epoch FROM lead(ts) OVER (ORDER BY ts)-ts),300) dt
           FROM energy WHERE ts >= '2026-09-27 06:00Z' AND measurement_revision IS NOT NULL AND ch0_power_w IS NOT NULL)
SELECT (ts AT TIME ZONE 'America/Denver')::date, sum(w*dt)/3.6e6 kwh, sum(dt)/3600 cov_h FROM e GROUP BY 1;
-- 10-03 29.1 | 10-11 3.1 | 10-15 30.3 | 10-24..11-04 28.2-32.0 kWh/day (heat1 at 1500 W ≈ 21 h/day)
```
Heat is reported three ways: heater minutes (heat1+heat2); electric kWh (measured); and thermal kWh, computed as heat1 at 1.5 kW plus heat2 at 75,000 BTU/h × 0.8 (`equipment_assets`). One gas minute delivers about 12 times the heat of one electric minute.

Variance and analytic MDE. Covariates are outdoor min temperature, outdoor mean temperature, daily solar and outdoor VPD (wind is missing since 11-03). The adjacent-pair difference SD is taken on weather residuals; MDE = (t.975 + t.80) · SD_pair / √(days/2), two-sided α = 0.05, 80% power.

**Winter window (31 days):**

| metric | mean | SD | weather R² | resid SD (LOO) | lag-1 r | SD pair diff | MDE 30 d | MDE 60 d | MDE 90 d |
|---|---|---|---|---|---|---|---|---|---|
| VPD in band at night, pts | 23.9 | 16.5 | 0.52 | 13.7 | 0.11 | 15.4 | 12.4 | 8.2 | 6.6 |
| VPD in band all day, pts | 49.5 | 14.7 | 0.36 | 14.0 | 0.36 | 16.2 | 13.0 | 8.6 | 6.9 |
| VPD error, kPa·h/day | 1.82 | 1.30 | 0.39 | 1.20 | 0.41 | 1.46 | 1.17 | 0.78 | 0.62 |
| temp in band, pts | 99.1 | 1.7 | 0.36 | 1.7 | -0.05 | 1.95 | 1.56 | 1.04 | 0.83 |
| heat minutes/day | 966 | 529 | 0.95 | 152 | 0.35 | 177 | 142 | 94 (10%) | 76 |
| electric kWh/day (26 d) | 23.2 | 9.6 | 0.71 | 6.4 | 0.67 | 4.4 | 3.5 | 2.3 (10%) | 1.9 |
| thermal kWh/day | 50.3 | 42.8 | 0.82 | 21.5 | 0.07 | 28.6 | 22.9 | 15.2 (30%) | 12.2 |
| wetting min/day | 27.9 | 34.7 | 0.62 | 25.0 | 0.57 | 19.5 | 15.6 | 10.4 | 8.3 |
| water gal/day (25 d) | 54.8 | 99.1 | 0.06 | — | 0.16 | 113 | 91 | 60 (110%) | 49 |

**Heating season (60 days):** night VPD MDE at 60 days 7.0 pts, all-day VPD 4.9, VPD error 0.35 kPa·h, heat minutes 69 (10%), electric kWh 2.3. **Summer (81 days):** wetting minutes 291 ± 204 (MDE at 60 days 112, 38%); water 216 ± 133 gal with weather R² 0.91 (MDE at 60 days 28 gal, 13%). So water is a usable outcome only in summer.

**Placebo randomization check.** I took the real 60-day series, applied 4,000 random pair assignments with no true effect, and measured the spread of the estimates. This is the design-based standard error (Bojinov et al.):
```
heating season 60 d  vpd_in_night  SE raw 2.96 | ANCOVA 2.45 | ANCOVA+pair FE 2.62  → MDE ≈ 6.9 pts
heating season 60 d  vpd_in        SE raw 1.93 | ANCOVA 1.67 | ANCOVA+pair FE 1.64  → MDE ≈ 4.6 pts
heating season 60 d  heat_min      SE raw 39.4 | ANCOVA 24.7 | ANCOVA+pair FE 22.2  → MDE ≈ 62 min
heating season 60 d  heat_kwh_th   SE raw 3.97 | ANCOVA 3.40 | ANCOVA+pair FE 3.37  → MDE ≈ 9.4 kWh
winter 30 d          vpd_in_night  SE raw 4.15 | ANCOVA 3.90                        → MDE ≈ 10.9 pts
winter 30 d          heat_min      SE raw 104  | ANCOVA 48.4                        → MDE ≈ 136 min
```
Weather adjustment cuts the heat SE by 40-55%. It barely helps VPD (about 10-17%), because night VPD depends on what the controller does more than on the weather.

**Days needed** for a given true effect (paired switchback, weather-adjusted, 80% power; the range runs from the heating-season to the winter variance):

| outcome | effect | days needed |
|---|---|---|
| night VPD in band | +15 pts | 12-17 |
| night VPD in band | +10 pts | 27-38 |
| night VPD in band | +5 pts | 108-149 |
| all-day VPD in band | +10 pts | 14-42 |
| all-day VPD in band | +5 pts | 53-165 |
| heat minutes | -15% (≈145 min) | 13-24 |
| heat minutes | -10% (≈97 min) | 29-53 |
| heat minutes | -5% | 116-215 |
| electric kWh | -10% (2.3 kWh) | 57-58 |
| temp in band, non-inferiority | 2-pt margin | ≈15 (winter SD) |

### 5. Safety and incidents

```sql
-- minutes with house temp <45 °F or >95 °F: 0 on every day of all three windows
SELECT severity, alert_type, count(*), count(DISTINCT local day) FROM alert_log WHERE ts >= '2026-09-06' GROUP BY 1,2;
-- esp32_reboot 27 on 10 days | planner_required_plan_missed 9 on 7 days | planner_gateway_delivery_failed 7 on 6
-- setpoint_unconfirmed 5 on 2 | writer_absent 4 on 2 | heap_pressure_critical 18 on 8 | vpd_extreme 9 on 2
```
Hard safety events (40/100 °F) do not occur, so they cannot be a powered outcome and become stop rules. Reboots (10 of 60 days) and missed plans (7 of 60 days) are common. They are the main protocol-deviation sources the boundary loop must handle.

## Public research

- **Switchback design and inference.** Bojinov, Simchi-Levi and Zhao, "Design and Analysis of Switchback Experiments", *Management Science* 69(7), 2023 ([arXiv 2009.00148](https://arxiv.org/abs/2009.00148)). They derive minimax-optimal switch schedules when outcomes carry over, and give exact randomization p-values and conservative CIs. Supports: assign whole periods at random, choose period length from the carryover order, and use a randomization-based CI (the placebo check above).
- **Carryover and burn-in.** Hu and Wager, "Switchback Experiments under Geometric Mixing" ([arXiv 2209.00197](https://arxiv.org/abs/2209.00197)). In systems that forget their past geometrically, dropping a short burn-in after each switch removes most carryover bias at little cost in precision. Supports: the 3 h washout (thermal τ ≈ 3 h, `docs/reviews/greenhouse-physics-model-floating-control-2026-06-18.md`) excluded from climate metrics.
- **Covariate adjustment (CUPED).** Deng, Xu, Kohavi and Walker, WSDM 2013 ([exp-platform.com/cuped](https://exp-platform.com/cuped/)). Regressing out a covariate that the treatment cannot affect cuts variance in proportion to its correlation with the outcome, without bias. Supports: adjust for observed outdoor weather, which the planner cannot change. Never adjust for indoor variables or the previous day's outcome, which carry treatment.
- **Peeking.** Johari, Koomen, Pekelis and Walsh, "Always Valid Inference: Continuous Monitoring of A/B Tests", *Operations Research* 70(3), 2022 ([arXiv 1512.04922](https://arxiv.org/abs/1512.04922)). Repeatedly checking an ordinary p-value inflates false positives. Supports: label the dashboard's running result "interim" and decide only at pre-registered looks (or use an always-valid interval).
- **Weather-normalised energy.** ASHRAE Guideline 14 (measurement of energy savings; summary of CV(RMSE)/NMBE practice at [simularis.github.io/understand_nmec](https://simularis.github.io/understand_nmec/uncertainty_excerpts.html)). Energy savings are judged against a weather-regression baseline, with stated fit and uncertainty limits. Supports: report heat and kWh effects only through a degree-day-style weather model, with its CI. Our heat model has R² 0.94-0.95 and a CV(RMSE) of about 15% on heat minutes.
- **AI greenhouse control trials.** Hemming et al., "Remote Control of Greenhouse Vegetable Production with Artificial Intelligence", *Sensors* 2019 ([DOAJ](https://doaj.org/article/5e1d7ad97488443a83f41c3f950465f7)). The Autonomous Greenhouse Challenge compared AI teams with a grower reference across parallel compartments, and one AI team beat the reference. Supports: the AI must be compared against a strong fixed reference under the same conditions. With one house we cannot run parallel compartments, so time-sliced switchback is the substitute.
- **MPC versus rule control.** Bersani et al., "Model Predictive Control versus Traditional Relay Control in a High Energy Efficiency Greenhouse", *Energies* 14(11):3353, 2021 ([DOAJ](https://doaj.org/article/accaa4a8eb31453987eadb3e4578b09b)), and their 2020 review ([MDPI Energies 13(14):3647](https://www.mdpi.com/1996-1073/13/14/3647)). They report about 30% electric savings when MPC replaces relay control, and the review notes how few controlled field comparisons exist. Caution: those gains come from replacing the controller. Our AI only tunes the parameters of an existing staged FSM (the August audit's framing), so expect effects of a few percent to about 10%, not 30%. That expectation is what sets the 60-day duration.

## Options considered

| option | for | against | verdict |
|---|---|---|---|
| Randomized day switchback, adjacent pairs, 11:00 boundary | Balances weather and season drift; pairs use the positive lag-1 correlation; one night per arm | Needs about 60 days for 10% effects; boundary restore must be reliable | **Primary** |
| 2-day blocks | Less boundary churn | Halves the units; carryover (τ ≈ 3 h) is short, so blocks buy nothing | No |
| Half-day (day/night) units | Twice the units | The planner's levers and plans span the evening→night handover; carryover lands in the outcome | No |
| Week on / week off | Simple | Weather trends alias with arm; 4-8 units in a winter | No |
| Three arms (AI / frozen / registry defaults) | Separates "good level" from "adaptation" | One-third more days for the same power; defaults are a stale regime | Later, if wanted |
| Cortex vs deterministic rule planner | Asks "does the LLM beat rules?" | A second study; rule engine idle since 10-05 | Second study |
| Counterfactual decision replay (`firmware/test/replay_emit.cpp`, `twin/`) | Every day, no house risk | Open loop, not a physical outcome; heavy, so it runs in the cluster | **Secondary**, labelled "decision replay" |
| Matched-weather observational (planner-outage days 08-06..08-10 etc.) | Free | Confounded by season, firmware and plan carryover | Sanity check only |
| Keep experiment-v2 as built (3 profiles, blinding, #641 gate) | Already coded | OpenAI-only, hot/dry only, never ran, 0.138 power | Retire the treatment; keep the plumbing |

## Recommended design

**Question.** Over a 24 h study day, does the Cortex planner adjusting the planner-owned tunables from forecast and history improve served-band VPD (and, separately, the cost in heat) compared with the same firmware, band and safety rules with those tunables frozen?

**Arms.**
- `ai`: the #1350 planner runs on its triggers and dispatches as normal.
- `baseline`: at the boundary the dispatcher restores the locked baseline vector for all 48 `PLANNER_PUSHABLE_REG` fields, reusing the ADR-0010 whole-baseline restore path. The planner still runs **propose-only**: its plan is validated and stored, tagged `arm='baseline', dispatched=false`, and never sent.
- Band (`crop_band_anchors` / on-chip curve), safety, manual writes and irrigation behave the same in both arms. Manual writes are logged as deviations with the study id (D-IRP-26 as decided) and the day stays in the intention-to-treat analysis.

**Baseline vector.** For each of the 48 fields, take the median of the device-confirmed values over the 14 days before the study, including propose-only Cortex output during a 7-day burn-in. Where the planner never wrote a field, use the current readback. Freeze it with its hash in Git. It is one winter vector; a summer study gets its own.

**Schedule.** 30 adjacent day pairs (60 days), each pair's order drawn by a seeded RNG. The seed and the schedule hash go in Git before day 1. The schedule is open, not blinded: the operator is the analyst, and the outcomes are machine-measured. The boundary is at 11:00 local, before SOLAR_MAX, so each arm gets SOLAR_MAX, SUNSET, MIDNIGHT and SUNRISE. Climate washout is 11:00-14:00 (dropped from climate metrics, kept for resource totals). OTAs and band edits happen only at a pair boundary; otherwise the pair is flagged.

**Outcomes per study day** (one append-only `study_day_outcomes` row; a metric that cannot be computed is NULL with a reason):
- Primary: night VPD time in band, graded on `fn_band_compliance_5m` (PR #1372) over the post-washout window.
- Guardrail (non-inferiority, 2 pts): temperature time in band.
- Secondary: all-day VPD time in band; VPD and temperature error integrals (kPa·h, °F·h); stress and dew-risk hours; heat1/heat2 minutes; measured electric kWh (PR #1369 `load_watts`); thermal kWh; estimated therms; vent, fan, fog and mister minutes; water gallons.
- Planner: plans written vs required, delivery failures, share of dispatched values confirmed by readback, `planner_inference_runs` tokens, latency and Wh.
- Covariates (observed, never forecast): outdoor min, mean and max temperature, solar kWh/m², outdoor VPD, wind, and heating degree-hours below 60 °F. Also recorded but not used as covariates: forecast error for the day and door-window state.

**Analysis** (frozen analyzer, extending `v2_analysis.py`): OLS with an arm indicator, weather covariates and pair fixed effects, with a randomization-inference CI (4,000 re-draws of the pair orders) and a HAC/t CI shown side by side. Intention-to-treat on all assigned days, plus a per-protocol sensitivity run without deviation days. One pre-registered look at 15 pairs (safety and futility only) and a final look at 30. Optionally extend to 45 pairs, but only if that is pre-registered. Output is byte-reproducible JSON consumed by the API.

**Guardrails (stop rules).**
1. Any house temperature below 40 °F or above 100 °F: firmware safety acts, the study pauses, and the day is a deviation.
2. A baseline day with temperature in band below 90% (winter mean 99.1, SD 1.7): pause and review.
3. The boundary restore is not confirmed by readback within 15 min: record a deviation, retry at most 3 times, never loop (August had 78 retries).
4. An ESP32 reboot during a study day: re-assert the arm's vector after reconnect (restore_value semantics) and flag the day.
5. A planner failure on an `ai` day leaves the last plan in force. That is ITT and stays in the analysis, flagged; it is part of what "the AI planner" means in practice.
6. Capability stays off until Jason starts the study, and Jason can pause from the dashboard (operator only).

**Data model (one migration, #1360).** Reuse `control_experiments` with a new `kind='planner_switchback'` and close the old row. Add `control_assignments` rows per day (arm, pair, boundary times). Add `plan_journal.arm`, `plan_journal.dispatched`, `planner_inference_runs.experiment_id` and `planner_inference_runs.arm`. Add `study_day_outcomes` (append-only, grants to the freezer role) and a `study_deviations` table, or reuse `experiment_events`. Add no new `experiment_v2_*` tables.

**Dashboard Planner section (#1361).**
- **Plan**: moved from Control as it is.
- **History**: plans per day, written vs required (failed, late); trigger mix; parameters changed and share confirmed; plans dispatched vs propose-only; cost per plan (tokens, latency, Wh, model), with "not recorded" before the #1350 cutover; the cutover marked on every chart; forecast vs actual for each plan's window.
- **Experiments**: a list with status, arms and days done of days planned. The 2026-08-28 draft shows as closed with its reason.
- **Arm calendar**: one cell per study day, coloured by arm, marking deviations, reboots, OTAs, excluded days and the next boundary.
- **Results**: one dot-and-whisker row per metric, AI minus baseline with a 95% CI. Primary first and the temperature guardrail second, then heat minutes, kWh and therms, then the rest. Show pairs done of 30 and an "interim" label before the final look. Lead with a plain sentence such as "After 18 of 30 pairs, AI nights were 9 points more in band (95% CI 2 to 16); heat +4% (CI −3 to +11)".
- **Daily outcomes table**: CSV export.
- **Decision replay**: shown last and labelled as not a physical outcome.

All numbers come from the analyzer's frozen output, not browser math.

## Implementation plan

1. **Prerequisites (other tracks).** Merge PR #1372 (#1358) and PR #1369 (#1362). Complete the #1350 cutover with `planner_inference_runs` populated and a propose-only flag. In #1366, make sure the input pack exposes the winter levers and the night VPD history. Land #1365's band revision, then freeze it.
2. **PR A: study plumbing, #1360.** In one migration: the study kind, arm and dispatched columns, `study_day_outcomes`, deviations and grants. The ingestor dispatcher gate (no `source='plan'` sends on baseline days). The lifecycle duty runs an 11:00 boundary with whole-vector restore and readback confirmation, and re-asserts after a reboot. Delete the selector's OpenAI adapter and its egress, so the selector deployment can be removed or scaled to 0. Close record `45039c86-…` with a reason.
3. **PR B: freezer and analyzer, #1360.** A nightly outcome freeze into `study_day_outcomes` with weather covariates. Extend `v2_analysis.py` to ANCOVA with pair fixed effects plus randomization inference. Add a power and schedule generator for the two-arm design to replace the three-profile `v2_power.py` model. Add read endpoints in `api/operator_routes/` and an MCP read tool.
4. **PR C: Planner section, #1361.** Nav item 7, Plan moved over, History, Experiments, calendar and results views, the CI row component, and the OpenAPI plus generated client.
5. **Dry run (acceptance for #1360).** 4 days (2 AI, 2 baseline) with capability on. Confirm the readback confirmations, zero plan dispatches on baseline days and four outcome rows.
6. **Burn-in and lock.** 7 days of Cortex propose-only. Compute and commit the baseline vector, seed and schedule hash. Start 30 pairs, roughly mid-December to mid-February.
7. **Secondary decision replay.** A cluster Job (not the Mac) runs the replay harness nightly on each day's climate with both vectors.

## Decisions Jason must make

1. **Baseline vector.** Recommended: the frozen median of the planner's own recent confirmed values, which measures the value of adapting. The alternative, registry defaults, measures "AI against stale defaults" and inflates the AI's apparent value.
2. **Primary outcome.** Recommended: night VPD time in band against the served band, with temperature time in band as a 2-point non-inferiority guardrail and heat/kWh as reported cost, not a gate. The alternative is heat minutes as primary.
3. **Duration.** Recommended: 60 days (30 pairs), starting after the prerequisites, with one look at 15 pairs (safety and futility) and a final at 30. It can detect about 7-8 points of night VPD and about 10% heat. Effects of 5% are not detectable this winter.
4. **Unit and boundary.** Recommended: whole days in adjacent pairs, boundary 11:00 local, 3 h climate washout.
5. **Blinding.** Recommended: none. An open schedule, pre-committed by hash in Git. Drop the beacon/HMAC ceremony and the #641 attended physical proof; the dry run replaces it.
6. **Arms.** Recommended: two (Cortex AI vs frozen). "No planner" is the frozen arm. Cortex vs the rule planner is a later study, and model or prompt variants run propose-only.
7. **Campaign #775/#581.** Recommended: close the hot/dry three-profile study as superseded, keep its tables and evidence, and revisit a summer study in June 2027 with the same two-arm machinery (water and wetting as outcomes).
8. **Planner levers in winter.** Recommended: the Cortex planner may write the winter levers (`night_vpd_bias_kpa` is already approved; also `vent_prefer_dp_delta_f`, `vent_exchange_fraction`, enthalpy and heat-dwell). Without them the study measures a planner that cannot touch the winter problem.

## Open risks

- **Low treatment intensity.** If the Cortex planner behaves like Iris (few winter-relevant changes), the result is a well-measured null. That is a valid answer, but plan for it.
- **The band moves.** #1365 may redefine the VPD band. Any band change mid-study breaks pairs, so freeze it first.
- **Variance estimates are short-window.** The winter window is 31 days, and colder months will have higher heat variance and heat1 saturation (already about 91% duty on 11-04, 1,309 min). As heat1 saturates, extra demand shifts to gas heat2, so kWh hits a ceiling and gas becomes the energy signal. Gas is estimated, not metered.
- **Boundary reliability.** August's retry storm shows the restore path can fail. The dry run must prove it on current firmware, which reboots on about 1 day in 6.
- **Electric metering** is a lower bound when loads net on ch1 (24 of about 11,000 samples). Kept as a secondary outcome.
- **Interim peeking from the dashboard.** Mitigated by the "interim" label and the pre-registered looks.
- **Seasonal generalisation.** A winter result says nothing about summer, where wetting and water dominate.

## Live findings for the supervisor

- `verdify-hermes-iris` was 0/1 available at about 21:19Z. The container exited (Error/1) and restarted; it showed ready at 21:20Z. The last `plan_journal` row was at 14:09Z, about 7 h earlier, while `plan_delivery_log` shows `acked TRANSITION` deliveries at 20:46Z with no plan. Worth a look on the #1350 path; no write was made.
- `equipment_assets` notes are inverted against current reality: heat1 is labelled "Backup heater … ~460W avg" and heat2 "Primary heating". Electric heat1 is the primary. Anything that reads these notes (planner context, docs) gets it backwards.
- `plan_journal` produces 3-13 plans a day, but dispatched changes happened on only about 10 of 31 October days, and none from 10-19 to 10-31. The planner's winter influence is close to nil today.
- `grow_light` runtime is 1,121-1,190 min/day in November (19-20 h), against 625-784 in June. This may be intentional DLI extension; flagged, not investigated.
