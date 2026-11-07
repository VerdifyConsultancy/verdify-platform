# Cortex planner: the 60K input pack, the deterministic call, embeddings and the A/B arm

Track `cortex-planner`. Issues: #1366 (input pack), #1350 (replace Hermes/OpenAI with one Cortex call), #1346 (Cortex-written greenhouse snapshot). Related: #1358 (served-band grade), #1359/#1360 (AI A/B study), #1351 (local-only), #751 (south probe).

Research date: 2026-11-05. Code at origin/main `cf21fa421`. All data reads were read-only production SQL; all cluster reads were `kubectl get`.

## Question(s)

1. What does today's planner see and do, and what did that history achieve against the served band?
2. Which inputs actually moved plan quality, as far as the data can tell?
3. Which tunables does the planner change, and how large are their effects?
4. Which context is wasted?
5. What does Cortex offer: models, context, structured output, embeddings, latency?
6. What should the deterministic call look like: pack sections and budgets, output schema, validation, retries, fallback, and per-run storage?
7. Should embeddings retrieve past similar days and lessons, and how?
8. How does the planner plug into the A/B arms (#1359/#1360)?

## Bottom line

- **Build a 20-25K-token deterministic pack, not a 60K one.** 60K stays a hard cap for the "rich" ablation variant only. Today's Iris starts at about 65K and ends at 122-140K Qwen tokens per full plan. About a third of that is duplicated, and some of it is wrong (#1366 measurements, re-confirmed here: SUNRISE system 4,729 and user message 38,791 Qwen tokens). Long-context research says accuracy falls with input length even when retrieval is perfect, so extra tokens are not free.
- **The forecast is the input that carries information.** Over 135 plans since 2026-10-06, forecast features explain 22-40% of the variance in the intent fields that should track weather: mist budget, economizer advantages, fog escalation and solar pre-cool. Current indoor and outdoor state alone explains 1-11%. Put the forecast with the band in force per hour at the centre of the pack.
- **Remove Iris's self-score and the lessons from what the planner sees.** The self-graded `outcome_score` correlates *negatively* with the real served-band outcome (r = −0.22 with temperature in band and −0.17 with VPD in band, 113 days). Of 229 active lessons, 197 are low-confidence, 21 name band-owned or retired knobs, and none is newer than 2026-10-05. Feed a deterministic served-band scorecard and a plan-history table instead.
- **No measurable climate benefit can be attributed to today's planner.** On the 14 days the plan was more than 24 hours stale (2026-07-04..09 and 2026-08-06..10), weather-adjusted VPD time in band was not significantly different: −6.4 points in summer (SE 4.3) and −2.4 points across all days (SE 3.6). Temperature in band was 8.7 points *better* without fresh plans (SE 3.9, summer). Planned days ran more fog (+101 min/day) and more vent (+127 min/day). This is observational and confounded, so it is no verdict. The A/B study (#1360) has to answer the question.
- **The levers that move are not the levers that matter now.** 15 of the 41 pushable knobs never varied from 2026-07-04 to 2026-11-04, including `night_vpd_bias_kpa`. In the cold season the main miss is night VPD below band (78% of hours below band in November). Jason approved letting the planner write `night_vpd_bias_kpa` (decisions.md 2026-11-05 ~19:50Z). Add it to the output schema, and add a night-VPD section to the pack.
- **One call:** `llm.primary.longctx` (Qwen3.8-27B NVFP4, vLLM 0.32, 98,304 context), no tools, thinking on at `medium`, `max_tokens` 24,000 (#1480), sampling temperature 0.6 / top_p 0.95 / top_k 20 with a fixed seed. Use `response_format: json_schema` only after a gateway test passes. Pydantic plus the registry always re-validate. On failure, retry once with a minimum pack. If that also fails, write nothing, keep the plan in force and raise `planner_failed`. There is no OpenAI fallback.
- **Store everything:** add a `planner_pack` table (compressed bytes, per-section sha and token counts) and extend `planner_inference_runs` (0 rows today) with trigger, pack/request/response hashes, status, finish reason, latency and arm. The forecast table keeps only about 30 days, so a pack cannot be rebuilt later. It must be stored when the plan is made.
- **Embeddings: not in pack v1.** Cortex `embed.main` (Qwen3-Embedding-4B, 2,560-dim, served at 8,192 tokens) is live. But the existing corpus is 5,307 OpenAI 3,072-dim chunks frozen since 2026-05-24, and the lessons are poor. Analog-day retrieval on weather features did no better than "yesterday" over 94 days: MAE 0.101 vs 0.111 for VPD-below fraction, and 0.493 vs 0.461 for kPa-hours below. Re-embed with Cortex at 1,024 dims (MRL) into a new table, use it for the snapshot/agents' search, and test retrieval only as a shadow pack variant.
- **A/B plug-in:** the pack version plus the request hash are the treatment identity. The same job runs in both arms. In the `baseline` arm it runs propose-only, so its plan is stored and never dispatched. `planner_inference_runs` already has `experiment_id`, `assignment_id` and `scheduled_in_arm` columns.
- **#1346 shares the client:** the snapshot reuses the S5/S6 builders and the Cortex client, with thinking off, about 3K tokens in and about 300 out, as a separate inference identity.

## Evidence from our data

### Windows and why

- **Served-band outcome window: 2026-07-04 to 2026-11-04 local (124 days).** `crop_band_anchors` for `house` were last updated on 2026-07-03, and the S8 firmware went live on 2026-07-04. `fn_band_setpoints(ts)` therefore reproduces the served band for every day in the window. Earlier data uses a different band.
- **Forecast-linked analysis: 2026-10-06 onward (135 plans).** `weather_forecast` begins at fetch 2026-10-06 21:18Z (713 fetches). Older forecasts are purged.
- **Excluded:** CO2, PAR and south-zone sensors (being replaced, #751). The house averages are used instead.
- **Caveats:** firmware OTAs inside the window, and the season change from summer to cold (heat replaces cooling and wetting). The analyses are split into summer (07-04..09-30) and all days where that matters.

### Q1. What the planner sees and does today

Deliveries and latency, 30 days:
```sql
SELECT event_type, count(*) n, count(*) FILTER (WHERE status IN ('plan_written','action_completed','acked')) ok,
 count(*) FILTER (WHERE status ILIKE '%fail%') failed,
 percentile_cont(0.5/0.95) of (terminal_at-delivered_at) minutes
FROM plan_delivery_log WHERE delivered_at>now()-interval '30 days' GROUP BY 1;
```
```
TRANSITION 58 ok 58 fail 0  p50 0.7 p95 1.6 min | FORECAST_DEVIATION 41/41 2.3/4.2 | SUNRISE 37/30 fail 7 3.1/6.1
SUNSET 34/29 fail 5 2.4/5.2 | MIDNIGHT 31/29 fail 2 2.4/3.8 | SOLAR_MAX 30/30 1.1/2.8 | WEEKLY 4/4 3.3/4.0
```

Plan journal by month:
```sql
SELECT date_trunc('month',created_at), count(*), count(climate_intents), avg(outcome_score), avg(anchor_score),
 count(lesson_extracted), count(hypothesis_structured) FROM plan_journal GROUP BY 1;
```
```
month    n   intents self  anchor lessons structured_hyp
2026-07  63  63      5.21  2.84   47      59
2026-08  73  73      4.08  2.53   22      53
2026-09  92  92      6.38  6.62   31      64
2026-10 146 146      3.63  1.06    5      61
2026-11  20  20      3.72  1.00    0      10
```
The learning loop has gone quiet: 5 lessons in October and 0 in November. `anchor_score` collapsed to about 1.

Context size: measured with the served model's own `tokenizer.json`, SUNRISE 2026-11-05: system 4,729 tokens, user message 38,791 (13.3K preamble, 0.6K event, 24.8K gather), plus 21.8K of tool schemas. Full-plan cycles end at 122-140K, re-send 0.9-1.24M input tokens, and output about 1.3-1.9K tokens of `set_plan` (#1366 table, re-confirmed). The Qwen tokenizer gives every digit its own token, so numeric tables cost about 4x what chars/4 predicts.

Served-band outcome, daily, computed from hourly means against `fn_band_setpoints`:
```sql
WITH h AS (SELECT time_bucket('1 hour',ts) hb, avg(temp_avg) t, avg(vpd_avg) v, avg(solar_altitude_deg) alt, ...
           FROM climate WHERE ts>='2026-07-04 07:00+00' AND ts<'2026-11-05 07:00+00' GROUP BY 1),
 j AS (SELECT h.*, b.* FROM h, LATERAL fn_band_setpoints(h.hb+interval '30 min') b)
SELECT local date, avg(t in band), avg(v in band), avg(v<vpd_low), avg(v>vpd_high), night below, kPa-h below/above,
       °F-h cold/hot, outdoor min/max/RH FROM j GROUP BY 1;
```
The last days, for example: 2026-11-03 VPD in band 58%, below 42% (71% of night hours); 2026-11-04 in band 50%, below 50% (86% of night hours). Temperature is in band 96-100%. This agrees with #1358.

### Q2. Which inputs moved plan quality

The packs that past plans saw were never stored, so no direct ablation is possible from history. Three indirect tests follow.

**(a) Forecast responsiveness.** For each plan since 2026-10-06: the per-plan mean of each intent field, against the forecast for the next 24 h from the latest fetch at or before the plan, and against the last hour's outdoor temperature and indoor VPD.
```sql
-- per plan: avg(climate_intent.<field>) over waypoints; forecast = latest fetched_at <= created_at;
-- fc_tmax/fc_tmin/fc_vpdmax/avg solar/avg rh over [created_at, +24h]; now_tout/now_vpd = last hour of climate
```
Correlations, plus R² for forecast+now and for now alone (n = 135; about 0.05 is the chance level for 7 regressors):
```
field                            sd     fc_tmax fc_vpdmax fc_rh  | R2 fc+now  R2 now
fog_escalate_vpd_excess_kpa      0.057  -0.41   -0.46     +0.50  |  0.40      0.01
economizer_temp_advantage_f      2.171  -0.51   -0.48     +0.36  |  0.37      0.11
economizer_dewpoint_advantage_f  1.665  -0.48   -0.43     +0.31  |  0.34      0.07
daily_mist_budget_gal            35.8   +0.45   +0.45     -0.31  |  0.27      0.08
solar_precool_gain_f             0.369  +0.40   +0.41     -0.38  |  0.22      0.02
mist_duty_limit_pct              6.14   +0.20   +0.25     -0.26  |  0.19      0.01
dew_margin_floor_f               0.341  -0.04   -0.06     +0.04  |  0.18      0.07
relay_churn_penalty              0.077  -0.04   -0.04     +0.04  |  0.15      0.05
wet_cutoff_hour (inert)          1.08   +0.28   +0.30     -0.30  |  0.14      0.10
forecast_vpd_bias_kpa            0.094  -0.27   -0.29     +0.22  |  0.13      0.09
moisture_engage_vpd_excess_kpa   0.052  +0.01   -0.02     +0.16  |  0.13      0.02
all_zone_vpd_excess_kpa          0.040  +0.02   -0.00     +0.12  |  0.12      0.03
forecast_temp_bias_f             0.621  +0.28   +0.28     -0.26  |  0.10      0.04
thermal_lead_time_min            9.60   -0.08   -0.07     +0.10  |  0.08      0.01
resource_sensitivity             0.121  -0.05   -0.06     +0.11  |  0.05      0.01
```
Reading: the planner does use the forecast, in the right direction, for the wet-side and economizer fields. It does not use the forecast for `thermal_lead_time_min`, which is the one field that should track the overnight minimum in winter (r = −0.03 with fc_tmin). `resource_sensitivity`, `relay_churn_penalty`, `moisture_engage` and `all_zone` vary from plan to plan with no driver; that is plan-to-plan noise. The pack should make the forecast prominent and attach each field to its driver (S1 below). The output should let the model keep the incumbent value instead of re-sampling it.

**(b) Self-score vs reality.** Daily mean `plan_journal.outcome_score` and `anchor_score` against the served-band daily outcome, 113 days:
```
self   vs t_in r=-0.22 | v_in -0.17 | kpah_below -0.12 | kpah_above +0.20 | fh_hot +0.23
anchor vs t_in r=-0.15 | v_in -0.17 | kpah_above +0.25
```
The score Iris feeds back to itself (PLANNER SCORE TREND, plans' self-grades, lessons derived from them) points the wrong way. It must not enter the pack. Replace it with the deterministic served-band scorecard (#1358).

**(c) Planner-off natural experiment.** There were 14 days with plan age over 24 h (no fresh plan; the device held the last values). OLS of each daily outcome on an off indicator plus outdoor max/min temperature and mean RH:
```
                    summer (n=59, off=14)          all (n=124, off=14)
t_in   in band      off effect +0.087 (SE 0.039)   +0.043 (0.036)
v_in   in band            -0.064 (0.043)           -0.024 (0.036)
v_below                   +0.119 (0.033)           +0.006 (0.037)
fog min/day               -101  (25)               -56   (33)
vent min/day              -127  (57)               -35   (53)
mist min/day              +16   (27)               +37   (20)
```
In summer, fresh plans shifted VPD misses from below band to above band and ran more fog and vent. There is no significant net gain in VPD time in band, and temperature in band was worse. The off days cluster right after the 2026-07-04 OTA and in one August outage, so the confounding is large. Treat this as "no evidence of benefit", not as evidence of harm.

### Q3. Which tunables the planner changes, and their effect sizes

`setpoint_plan`, 2026-07-04..11-04, per parameter (distinct values, SD, range):
```
cool_stage2_over_high_f nd=299 sd 0.65 (0-2.1) | mister_engage_kpa 276 sd 0.19 (0.79-1.90) | mister_all_kpa 246 sd 0.18
mister_vpd_weight 181 sd 0.27 | min_fog_on_s 32 | min_fog_off_s 29 (30-150) | direct_wet_stress_vpd_margin_kpa 26
mister_pulse_gap_s 24 | mister_water_budget_gal 22 (100-300) | ... dwell_gate_ms 16 | vent_prefer_temp_delta_f 13
never varied (nd=1): band_track_fraction 0, night_vpd_bias_kpa 0, heat_hysteresis 1, enthalpy_open/close, mist_backoff_s,
 mist_max_closed_vent_s, mist_thermal_relief_s, outdoor_staleness_max_s, cool_stage2_exit_hysteresis_f, vent_exchange_fraction,
 sw_economiser_enabled, sw_fog_closes_vent, sw_mister_closes_vent, sw_summer_vent_enabled, sw_dwell_gate_enabled
```

Effect sizes: the daily mean planned knob against daily outcomes, controlling for outdoor max/min temperature and RH. Each cell is the effect of +1 SD of the knob (t-statistic):
```
Jul04-Sep30 (n=89)  mist_min      fog           vent          v_above       v_in
engage   sd 0.163   -10.7(-1.8)   +0.9(+0.1)    +35.2(+1.8)   +0.02(+1.5)   -0.01(-0.4)
fogesc   sd 0.055   -19.8(-2.0)   -35.7(-1.5)   -4.8(-0.1)    -0.02(-0.8)   +0.00(+0.0)
cool2    sd 0.521   -11.4(-1.3)   +32.1(+2.2)   +40.6(+2.0)   +0.03(+1.8)   -0.00(-0.1)
budget   sd 51.2    +13.5(+1.6)   +44.7(+2.2)   +70.9(+2.7)   +0.04(+2.0)   -0.01(-0.6)
fogoff   sd 15.8    -16.5(-1.8)   -33.1(-1.4)   -6.7(-0.2)    -0.04(-1.8)   +0.02(+0.8)
mweight  sd 0.200   +12.7(+1.8)   +23.8(+1.4)   +52.5(+2.3)   +0.04(+2.3)   -0.02(-1.2)
dwell_s  sd 22.7    -14.8(-1.9)   -39.1(-2.1)   -69.5(-2.8)   -0.05(-3.2)   +0.01(+0.7)
```
Reading:
- Every knob's association with **VPD time in band** is under |t| = 1.3 in both windows. The planner's knob choices have no detectable link to the graded outcome.
- The associations with actuator minutes are 10-120 min/day per SD. Their signs follow reverse causation: the planner raises `budget` and `mister_vpd_weight` on hot, dry days, and those days also run more fog and vent and sit above band more. A day-level observational fit cannot separate the knob's effect from the planner's reaction to the weather. That is what the randomized study is for.
- In November, heat1 runs 1,260-1,436 min/day, fog 2-45 and mist 0-8 gal (#1366). Most of these knobs are idle in the cold season.

### Q4. Wasted context (measured, #1366, re-confirmed)

- Duplicated in one cycle: the forecast is sent 2x, setpoints 2x (1.3K plus a 15.3K raw `get_setpoints` dump), lessons 5x, the scorecard 2x, tunable bounds 3x and the playbook 2x.
- Low value or wrong:
  - unrounded forecast-accuracy floats (2.8K);
  - band DB lineage (0.8K);
  - grow-light detail (0.7K);
  - KPI formula prose (2.3K);
  - frozen-band binary compliance (wrong, #1358);
  - raw RAG chunks from April and May plans (7.8-19.6K);
  - stale lessons;
  - 21.8K of tool schemas.
- The embedding corpus behind `knowledge_search` is stale:
  ```sql
  SELECT source_type, count(*), min(embedded_at)::date, max(embedded_at)::date FROM verdify_embeddings GROUP BY 1;
  site_doc 4274 05-11..05-23 | observation 636 05-11 | plan 159 05-11 | playbook 124 05-11..05-23 | lesson 114 05-11..05-24
  ```
  All are `vector(3072)` OpenAI `text-embedding-3-large` (`scripts/embed-corpora.py`). `planner_memory_items` has 0 rows.
- Lessons:
  ```sql
  SELECT is_active, confidence, count(*), max(created_at)::date FROM planner_lessons GROUP BY 1,2;
  active: low 197 (newest 10-05), medium 19, high 13 (newest 08-20)
  -- 21 of 229 active lessons mention temp_low/high, vpd_low/high, band_track, bias_heat/cool, gpt, hermes or slack
  ```

### Q5. Embedding retrieval test (analog days)

For each day from 2026-08-03 onward, I found the 3 nearest earlier days on standardized (outdoor min, max, mean RH). The weather used is the *realized* weather, which is an optimistic oracle. I predicted the day's outcome as the analogs' mean.
```
target       MAE analog-3   persistence (yesterday)   running mean
v_below      0.101          0.111                     0.189
kpah_below   0.493          0.461                     0.726
t_in         0.060          0.060                     0.161
heat1 min    125            116                       353
```
Even with oracle weather, analog days do not beat "the last few days", which the pack already carries in S7. History is only 4 months and crosses a season change. Revisit after a full year of served-band data.

## Cortex: what it offers (read-only, 2026-11-05 ~21:00Z)

`kubectl --context vallery -n gpustack-system get pods` plus runner args:

| route / served name | model | context | node | notes |
|---|---|---|---|---|
| `llm.primary.longctx` → `llm.qwen38u.longctx` | orcarouter/Qwen3.8-27B-Uncensored-NVFP4, TP2 | **98,304** | vm-k3s-node5 | vLLM 0.32.0, xgrammar 0.2.8, `--reasoning-parser qwen3`, prefix caching, `--max-num-seqs 8`, default `reasoning_effort: medium`, server default temperature 1.0 |
| `llm.primary.longctx.mm` → `llm.qwen38u-5080` | same weights | 65,536 | vm-k3s-node7 | vision (#1021); runner back up (132 min old) after the node7 maintenance |
| `embed.main` | Qwen/Qwen3-Embedding-4B (rev 5cf2132a), pooling runner | 8,192 served (model supports 32K) | vm-k3s-node8 | 2,560-dim, last-token pooling, L2/cosine; query prefix `Instruct: … Query:`; cortex profile marks it `live_qualified: false`, `state: proposed` |
| rerank | Qwen/Qwen3-Reranker-0.6B | 8,192 | vm-k3s-node6 | available for retrieval re-ranking |

- **The 98,304 incident.** In August, an 81.9K prompt plus a 16,384 `max_tokens` came to 98,305 tokens and failed 7 of 7 cycles (`96775eb5e`). The cap is pinned in cortex `config/eight-gpu-placement.json`. Only the planner side can fix this.
- **Latency.** Prefill about 1.8K tok/s and decode 63-65 tok/s single-stream (cortex `docs/QWEN38_EXPERIMENT_WAVES.md`). A 22K pack plus 4-8K of output takes roughly 1.5-2.5 min, against Hermes's 2.3-3.1 min p50 for plan events.
- **Structured output.** vLLM supports `json_schema` with a reasoning parser and applies the grammar to the answer after reasoning. Cortex's own contract only tests `json_object`, so strict `json_schema` through the GPUStack/Higress gateway is unverified.
- **Reasoning effort.** The chat template's `reasoning_effort` is a system-prompt sentence with no token budget, so `max_tokens` is the only bound. A `length` stop inside `<think>` returns no JSON.

## Public research

- **Lost in the Middle** (Liu et al., TACL 2024): https://arxiv.org/abs/2307.03172. Models use information at the start and end of a long input better than in the middle. Put the task and output contract last and the key data near them.
- **RULER** (Hsieh et al., 2024): https://arxiv.org/abs/2404.06654. Effective context is much shorter than the advertised window for most open models, and performance degrades as length grows. Keep the pack well below 98K.
- **Context length alone hurts** (Du et al., 2025): https://arxiv.org/abs/2510.05381. Accuracy fell 13.9-85% as input grew, even with perfect retrieval and even when the padding was whitespace. Every unused token costs quality. Their mitigation is to restate the relevant evidence before answering. For us, that means asking for a short "key facts" field in the output, before the waypoints.
- **Let Me Speak Freely?** (Tam et al., 2024): https://arxiv.org/abs/2408.02442. Strict format constraints during generation can reduce reasoning quality. Let the model think freely, and constrain only the final JSON.
- **JSONSchemaBench** (Geng et al., 2025): https://arxiv.org/abs/2501.10868. Constrained-decoding engines (XGrammar included) differ in JSON Schema feature coverage and compliance. Test our exact schema features (`minimum`/`maximum`, `enum`, nested arrays) on our build, and keep server-side validation.
- **vLLM structured outputs docs:** https://docs.vllm.ai/en/latest/features/structured_outputs.html. `response_format` works with a reasoning parser; reasoning and the constrained answer come back in separate fields; backends are xgrammar and guidance.
- **Qwen3 model card** (sampling guidance for the family): https://huggingface.co/Qwen/Qwen3-32B. For thinking mode it recommends temperature 0.6, top_p 0.95, top_k 20, min_p 0. It says not to use greedy decoding, because greedy decoding causes repetition and lower quality. Our server default of temperature 1.0 is higher than recommended.
- **Qwen3-Embedding-4B card:** https://huggingface.co/Qwen/Qwen3-Embedding-4B. 32K context, up to 2,560 dimensions with MRL (32-2,560 selectable), and instruction-aware: a task instruction on the query side adds about 1-5%.
- **LLMs as zero-shot time-series forecasters** (Gruver et al., NeurIPS 2023): https://arxiv.org/abs/2310.07820. How numbers are tokenized changes results substantially. This supports compact, fixed-precision integer columns and the real tokenizer for budgeting.
- **Pre-trained LLMs for industrial (HVAC) control** (Song et al., MSRA 2023): https://arxiv.org/abs/2308.03028. GPT-4 controlled HVAC from a short task description, a few selected demonstrations and the current observation, at a level comparable to RL with few samples. Prompt components matter; this is prior art for a compact observation-plus-examples prompt.
- **Analog ensemble** (Delle Monache et al., Monthly Weather Review 2013, DOI 10.1175/MWR-D-12-00281.1): https://opensky.library.ucar.edu/islandora/object/articles%3A12922. Retrieving past situations that match the current forecast is a sound method, but it relies on 12-15 months of training history. We have 4 months of valid served-band data, which is consistent with our null analog result.
- **pgvector:** https://github.com/pgvector/pgvector. HNSW and IVFFlat indexes support `vector` up to 2,000 dimensions and `halfvec` up to 4,000. A 2,560-dim vector cannot be HNSW-indexed as `vector`, so truncate to 1,024 with MRL or use `halfvec`.

## Options considered

| option | for | against | verdict |
|---|---|---|---|
| A. Port Iris's prompt plus gather as-is into one call | Least work | 39K user plus 4.7K system without tools; still carries wrong inputs (self-score, frozen compliance, stale lessons); no attribution | No |
| B. Deterministic pack about 22K (≤25K target, 60K cap), one schema-bound call | Fits 98K with room; reproducible; each section testable by ablation | New builder; needs a bench to prove quality | **Yes** |
| C. "Rich" 50-60K pack (7 d hourly, 20 plans, top-20 lessons) | More history | Long-context degradation (RULER, Du 2025); 2.5x prefill; our data shows no gain from older history (analog null) | Ablation and WEEKLY only |
| D. Keep the agent loop on Cortex with fewer tools | Flexibility | Unbounded tokens; not reproducible; the August failure mode | No |
| E. Embedding RAG of past plans and lessons in v1 | Jason asked about embeddings | Corpus stale and OpenAI-dimensioned; lessons low quality; analog null result | Not in v1; shadow variant later |
| F. Thinking off (non-thinking mode) | Faster, no `<think>` truncation risk | Plans are multi-constraint trade-offs; cortex W6 found `medium` better than `xhigh` and faster | Thinking `medium`; bench `low` |

## Recommended design

### Components

- **`planner/` module**, new. It replaces the planning parts of `ingestor/iris_planner.py` and `scripts/gather-plan-context.sh`. It contains:
  - `pack/`: one pure builder per section;
  - `sections.py`: order, budgets and the drop order;
  - `tokens.py`: Qwen `tokenizer.json`, pinned by sha256;
  - `client.py`: the shared Cortex client, also used by #1346;
  - `run.py`: one run per `trigger_id`.

  The ingestor keeps the trigger loop and calls `run.py` in-process or as a Job. Recommendation: a separate `verdify-planner` Deployment, reusing the name of the idle LangGraph one, so the single device writer stays free of model latency.
- **Shared validator/writer.** Lift MCP `set_plan`'s body (`mcp/server.py:3878-4363`) into `verdify_services`. MCP and the planner both call it: one bounded-write contract.
- **`planner_graph/`: delete.** It has never run (`planner_graph_runs` = 0), it is OpenAI-bound, and its only reusable idea (`context_digest`) is replaced by `planner_pack`.

### Pack v1 (fixed order, Qwen-token budgets)

Static sections come first, for prefix-cache reuse. Data is in the middle. Task and contract come last.

| # | section | content | budget |
|---|---|---|---|
| S0 | System | Role, authority boundary (firmware owns relays and safety; 40/100 °F hard limits), units, no tools | 700 |
| S1 | Decision surface | Each output field: min/max/default, **incumbent value per active waypoint**, the knobs it drives (one line) and **its driver** (e.g. `daily_mist_budget_gal ← forecast VPD peak`; `night_vpd_bias_kpa ← tonight's band shortfall and outdoor dew point`). Manual-held knobs listed read-only | 1,800 |
| S2 | Greenhouse card (versioned file) | Equipment, heat1 electric primary / heat2 gas backup, misters and fog, door window closed for winter, thermal τ ≈ 3 h, dew-margin rules, "outdoor air may dry the house when confirmed drier" (decisions 2026-11-05) | 1,100 |
| S3 | Playbook digest (versioned file) | ≤15 hard rules plus ≤10 heuristics | 1,300 |
| S4 | Trigger and clock | Event, `as_of`, sun times today and tomorrow, active plan and remaining waypoints | 400 |
| S5 | Now | House and zone T/RH/VPD (south marked untrusted), dew margin, mode, relays; the served (graded) band and the house VPD control envelope, labelled; freshness flags | 800 |
| S6 | Forecast with band | Hourly 0-36 h, 3-hourly 36-72 h: T, RH, centi-kPa VPD, dew point, solar, cloud, wind, precip prob, **band per row**; days 4-7 daily; bias-by-lead table | 3,400 |
| S7 | Observed 72 h hourly | Indoor, signed band deltas dT/dV, outdoor, minutes-on per actuator; integer-scaled, blanks for zero | 2,700 |
| S8 | Night VPD card (new) | Last 7 nights: hours below band, kPa-h below, heat1/heat2 minutes, `night_vpd_bias_kpa` in force, outdoor-drier-than-indoor hours (dew point) | 600 |
| S9 | Last 6 h | 10-min rows (5-min for TRANSITION/FORECAST_DEVIATION) | 1,600 |
| S10 | Response priors | Deterministic, 14 d: °F per heat1/heat2 hour by outdoor bin; ΔVPD per fog/mist minute; vent effect on T and dew point; night RH drift | 900 |
| S11 | Served-band scorecard | 14 days × (T/VPD in band day/night, below/above, kPa-h, stress hours, actuator minutes, water). **No self-score** | 1,400 |
| S12 | Plan history | Last 6 plans: intent deltas vs previous, predicted vs realized (structured hypothesis), deterministic window score, clamps/rejections | 2,200 |
| S13 | Alerts | Open alerts and dispatcher clamps, 24 h | 500 |
| S14 | Crops | One line per planting (today: hanging Vanda orchids; hydro rails empty) | 200 |
| S15 | Task and output contract | Event goal, waypoint anchors, "keep the incumbent unless a driver changed; give a reason ≤25 words per changed field", schema reference | 700 |
| | **Total** | | **≈ 20,300** |

- **Not in v1:** lessons, RAG chunks, KPI prose, band lineage, raw setpoint dumps, grow-light detail, irrigation detail, frozen-band compliance, tool schemas, Slack.
- **Drop order when over budget:** S14, S10 → summary, S9 → hourly, S12 → 3 plans, S6 days 4-7. Record what was dropped.
- **Encoding rules** (measured in #1366): pipe tables with one header row, fixed precision, integer scaling (VPD in centi-kPa), signed band deltas instead of repeated band columns, blanks for zero. No JSON objects in data sections (2.3x the tokens).

### Output schema `PlannerProposal` (in `verdify_schemas/planner_proposal.py`)

- `key_facts`: ≤5 strings, ≤25 words each. This is the restate-the-evidence mitigation.
- `hypothesis`: conditions, ≤3 numeric predictions (`metric`, `window`, `value`).
- `waypoints[3..6]`, each with:
  - `ts`, an enum of the S4 anchors;
  - `climate_intent`: ClimateIntent fields with registry `minimum`/`maximum`, **plus `night_vpd_bias_kpa` (0-0.25)**, **minus `wet_cutoff_hour`** (it reaches no knob, `climate_intent.py:595-599`);
  - `changed`: the list of field names;
  - `reasons`: a map from field to text.
- `expected_outcome`: VPD-below hours, cold hours, water gallons.
- `confidence`: 0-1.

Cross-field rules, such as the moisture-ladder validator (`all_zone ≥ moisture_engage`), are not expressible in JSON Schema, so Pydantic re-validates every output.

### Request

`POST /v1/chat/completions` to `llm.primary.longctx`, no tools:
- `max_tokens` 24,000 (12,000 until #1480: one SUNRISE primary spent all of 12,000 in reasoning);
- `temperature` 0.6, `top_p` 0.95, `top_k` 20, a fixed `seed`;
- `chat_template_kwargs.reasoning_effort = "medium"`;
- `response_format: json_schema` (strict) after the gateway test, otherwise `json_object` plus validation.

Guard: `pack_tokens + max_tokens ≤ 72,000`, checked before sending.

### Retries and fallback

1. An HTTP or timeout error (deadline 5 min) gets 1 retry with the same request.
2. On `finish_reason ≠ stop`, a parse error, or a Pydantic/registry rejection: one repair retry with thinking off (`enable_thinking=false`, sampling 0.7/0.8/20; #1480 measured `low` still reasoning 10,997 tokens), the minimum pack (S0-S6, S15) and the validation error appended.
3. If that still fails: write nothing, keep the active plan (the device holds the last values), insert `alert_log` `planner_failed`, and set `plan_delivery_log.status=planner_failed`. There is no OpenAI fallback.

Idempotency: there is one run per `trigger_id`. A re-delivery reuses the stored response.

### Data model (one migration, next number after 289)

- `planner_pack`:
  - `pack_sha256` PK;
  - `trigger_id`, `as_of`, `pack_version`, `builder_git_sha`;
  - `sections` jsonb (name, sha, tokens, dropped);
  - `total_tokens`, `body_zstd` bytea, `created_at`.
  - Retention: keep indefinitely. It is about 20-40 KB compressed per pack, so about 8 packs a day comes to about 100 MB a year.
- `planner_inference_runs`: add `trigger_id`, `pack_sha256`, `request_sha256`, `response_sha256`, `status`, `finish_reason`, `latency_ms`, `reasoning_tokens`, `response_body` jsonb, `dispatched` bool and `pack_variant`. It already has `experiment_id`, `assignment_id`, `scheduled_in_arm`, `input_tokens`, `output_tokens` and `energy_wh`.
- `plan_journal`: add `inference_run_id`. Set `planner_instance='cortex'`.

### Embeddings (later, not v1)

- New table `verdify_embeddings_v2` (`halfvec(1024)` or `vector(1024)` with HNSW), `model='Qwen3-Embedding-4B@5cf2132a'`, `dims`, and `content_hash`.
- Re-embed the site docs, playbook, plan reasons and hypotheses with `embed.main`, using MRL truncation to 1,024 dims and the query instruction prefix. Use the reranker for the top 20 → top 5.
- Retire the OpenAI 3,072-dim store and MCP `knowledge_search`'s OpenAI path (#1351).
- Planner use is limited to an ablation variant "S16: 3 most similar past plan days with their served-band outcome" (≤1,500 tokens), run in shadow. It is promoted only if it beats v1 on the bench metrics.

### A/B fit (#1359/#1360)

- The treatment identity is `(pack_version, request_sha256)`, where the request hash covers the model, runner image, tokenizer sha, sampling and schema sha.
- One job serves both arms:
  - `ai` arm: validate, write and dispatch.
  - `baseline` arm: validate and store with `dispatched=false`; the dispatcher skips it.
- Pack variants (lean / v1 / rich / +retrieval) run only as extra propose-only shadow calls. The route is idle (`max-num-seqs 8`), so a shadow call per trigger is affordable. Only one variant is ever live.

### #1346 snapshot

- Same client, separate identity and Secret (`verdify-brief-cortex`).
- Fact sheet built from S5, the S6 first 12 h and S13 (about 2-3K tokens).
- Thinking off (`enable_thinking=false`), temperature 0.7 / top_p 0.8 (non-thinking guidance), `max_tokens` 400.
- Skip when the fact-sheet hash is unchanged.
- It never feeds the planner.

## Implementation plan

1. **PR 1, research and fixtures (#1366).**
   - Add `docs/planner/pack-v1.md`, the versioned greenhouse card and playbook digest, and the pack builder with token counter.
   - Add a unit test for byte-identical packs.
   - Add a cluster Job that builds the 40-trigger bench corpus as of past triggers. **Start this now:** forecasts older than about 30 days are purged daily.
2. **PR 2, schema, migration and shared writer (#1350, #1366).**
   - `PlannerProposal` with `night_vpd_bias_kpa` added and `wet_cutoff_hour` removed.
   - Materializer emits `night_vpd_bias_kpa`.
   - Migration: `planner_pack` and the `planner_inference_runs` columns.
   - Lift `set_plan` into `verdify_services`.
3. **Cross-repo, in parallel.**
   - jvallery/cortex-ai-compute: planner and brief inference identities; gateway `json_schema` test (5-10 calls: strict schema plus reasoning, numeric bounds, enum); bench window on node5.
   - jvallery/agents: KSOPS Secrets `verdify-planner-cortex` and `verdify-brief-cortex`.
4. **PR 3, planner service and shadow (#1350).**
   - Client, run, retries and alert.
   - A `VERDIFY_PLANNER_BACKEND=hermes|cortex|shadow` flag.
   - 14-day shadow next to Hermes; bench, noise floor and group ablation as cluster Jobs (#1366 research items 3-5).
5. **PR 4, cutover and retirement (#1350, #1352).**
   - Flip to `cortex`.
   - Remove `verdify-hermes-iris`, the idle `verdify-planner` LangGraph code and the OpenAI keys; drop the Slack step.
   - Verify `max(input_tokens + max_tokens) < 98304`.
6. **PR 5, A/B hooks (#1360).** Arm read, `dispatched=false` path and propose-only shadow variants.
7. **PR 6, snapshot (#1346).** `greenhouse_brief` table, CronJob, API/MCP read and hero.
8. **Later.** Cortex re-embedding (`verdify_embeddings_v2`) and the retrieval shadow variant (#1351 follow-up).

## Decisions Jason must make

1. **Pack size target.** Default: about 20-25K live; 60K only as an ablation and WEEKLY variant.
2. **Drop lessons and self-scores from the planner's input**, and stop `plan_evaluate`/`lessons_manage`. Default: yes. The score is anti-correlated with outcomes and the lessons are stale.
3. **Output schema change.** Add `night_vpd_bias_kpa` (already approved) and remove the inert `wet_cutoff_hour`. Also decide whether to expose heat-side knobs (`min_heat_on_s`, `heat_hysteresis`). Default: night bias only for now; heat knobs after the first bench.
4. **Where the planner runs.** Default: its own Deployment, not inside the single-writer ingestor.
5. **Fail-safe.** On failure, keep the current plan and alert, with no cloud fallback. Default: yes. How long a stale plan is acceptable depends on the dispatcher's expiry behaviour (#1350 research item 6).
6. **Embeddings now or later.** Default: re-embed with Cortex for search and the snapshot, but keep retrieval out of the live pack until a shadow ablation shows a gain.
7. **Sampling.** Default: temperature 0.6 with a seed, overriding the server's 1.0, and `reasoning_effort` medium.

## Open risks

- **Strict JSON schema through the gateway is unverified** on this build. Fallback: `json_object` plus Pydantic plus one repair retry.
- **There is no failover for `llm.primary.longctx`.** It is one node pair on node5, and node7 has just been through maintenance. Outages mean the plan stays stale.
- **`max_tokens` is the only bound on reasoning**, so a long `<think>` returns nothing. Monitor `finish_reason=length` and reasoning tokens.
- **The embedding route is marked `live_qualified: false`** in cortex-ai-compute. Its qualification belongs to Cortex.
- **The bench corpus is time-critical** (30-day forecast retention). Without it, the ablation cannot run on past triggers.
- **No offline climate simulator exists.** Bench metrics are proxies (validity, forecast responsiveness, replay decision deltas). Only the A/B study measures outcome, and the history here suggests the effect may be small.
- **The materializer anchors mister thresholds to the house VPD control envelope, not the served crop band** (#1366 side finding, #1358). The pack must label both until that is fixed.
- **The analyses above are observational.** Short windows, a season change and OTAs confound them. They justify the pack choices, not claims of planner benefit or harm.

## Reproducibility

The query texts are inline above. The analysis scripts (numpy OLS, correlations and the analog test over SQL-aggregated daily rows, 124-135 rows each) ran on the operator Mac. They are small and are not part of the repo.
