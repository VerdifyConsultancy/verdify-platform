# Verdify — Current State Report

**Audit date:** 2026-05-29 (America/Denver, MDT) · **Window:** last 14 days (~2026-05-15 → 2026-05-29)
**Site:** single 367 sq ft production greenhouse, Longmont CO, 5090 ft, arid (~15% RH), 95°F solar peaks, mixed crops
**Author:** Lead auditor synthesis over 12 component/domain audits + 5 gap-fill investigations

---

## 1. Executive Summary

**Headline verdict: The greenhouse is SAFE but increasingly losing the comfort band as summer ramps; the control software is fundamentally sound, but its sensing, monitoring, and energy-accounting layers are decayed enough that Verdify is partly flying blind — and nobody has physically looked at the plants.**

The deterministic control core is in excellent shape: all 16 firmware replay invariants pass on 193,525 corpus rows, the 8-state FSM is stable (peak 16 transitions/hr vs a 30/hr cap), the setpoint dispatcher confirms band changes at p50 37s / p95 81s, and the ingestor is live with climate freshness under 1 minute. Climate stayed thermally safe every day — dew-point margin never below 5°F, only 12 of ~19,900 samples exceeded the 85°F true-danger line — but band compliance is mediocre and falling (75.6% → 57.5% week-over-week) because the cooling stack is vent-only with a hard physics ceiling it cannot beat against solar gain. The greenhouse's real agronomic problems are spatial and sensory: a hidden wet west corner (~52h in the botrytis window, corrected from an inflated 259h), a hot-dry north corner, an over-tight 78°F band driven by a single sensitive crop, and — most seriously — broken sensing: both south soil probes died inside the window, the west probe crashed (likely a disconnection), and PPFD/leaf-wetness/leaf-temp channels are entirely dark. On top of that, three monitoring/accounting failures erode trust: the firmware log channel has been intentionally off for 12 days, the energy meter undercounts electricity ~6.6x (feeding Iris a wrong but bounded energy number), and the open-alert backlog is 92% un-resolvable orphans. No human physically verified any crop during the window. The system keeps the plants alive; it does not currently keep itself, or its operator, well-informed.

### At-a-glance health (12 areas)

| # | Area | Status | One-line |
|---|------|--------|----------|
| 1 | Firmware (ESP32 control core) | 🟡 Degraded | Invariants/enums/heap-trend solid; log channel dark 12d, deploy churn was 30 versions/14d |
| 2 | Setpoint dispatcher | 🟢 Healthy | 95% confirm, p50 37s band latency, 0 push failures |
| 3 | Ingestor (capture pipeline) | 🟢 Healthy | <1 min freshness, 0 stale sensors, 1 restart-gap blind spot |
| 4 | Notebook / planner loop | 🟡 Degraded | Loop closes (40/42 graded); accuracy views dead, deviation triggers invisible |
| 5 | Site + public API | 🟢 Healthy | Rebuilt today, 139 Grafana embeds live; 1 stale snapshot, dead apex redirect |
| 6 | Schemas / drift guards | 🟡 Degraded | Contracts match reality; 6 tests red (test rot), 1 latent fog-enum bug |
| 7 | TimescaleDB | 🟡 Degraded | Live path fresh; 3 dead pipelines unmonitored, setpoint_snapshot 58% of DB uncompressed |
| 8 | Greenhouse climate | 🟡 Degraded | Safe but compliance falling; zone non-uniformity, over-tight band |
| 9 | Mechanical / HVAC | 🟡 Degraded | No stuck relays, balanced fans; meter undercount, reboot storm (resolved) |
| 10 | Irrigation / fertigation | 🔴 Broken | Delivers water reliably but fully open-loop; all 4 feedback signals down |
| 11 | Misting / fog | 🟡 Degraded | Evaporative cooling works; daily water counter corrupted, severe zone imbalance |
| 12 | State machine (FSM) | 🟡 Degraded | Logic stable & invariant-clean; cooling actions lack authority (actuator ceiling) |

**Cross-cutting (gap-fill) issues affecting the above:** alert backlog orphans, forward heat risk (June 4–9 cluster), uncalibrated CO₂ telemetry, no human plant verification, esp32_logs heap tradeoff unmeasured, kWh undercount reaching the planner.

---

## 2. Greenhouse Climate (last 14 days)

### Quantitative dashboard

| Metric | Value | Notes |
|--------|-------|-------|
| Avg overall band compliance | **65.9%** | Falling: wk1 75.6% → wk2 57.5% (corr −0.48) |
| Avg temp compliance | 71.7% | Weakest axis; binding ceiling only 78°F |
| Avg VPD compliance | **79.1%** | (Not 90.6% — corrected; above-band ~18%) |
| Worst day | 49.6% | 2026-05-26 and 2026-05-28 |
| Heat-stress hours (14d) | 89.1 h | But 5,162 samples 78–85°F vs only **12** >85°F |
| Samples temp_avg >85°F (true danger) | 12 / 19,905 (0.06%) | Crop-danger heat is rare |
| VPD-high stress hours | 63.2 h | Concentrates hr14–17 (corrected: hr14 ≈ 7.0h, not 34.3h) |
| Cold-stress hours | 8.9 h | Negligible; coldest 61.7°F |
| Min dew-point margin | **5.0°F** | Zero condensation-risk samples on the average |
| DIF (day−night) | +9.4°F | Day 74.0°F / night 64.6°F — healthy |
| Avg VPD / time-in-band | 0.77 kPa / ~79% | Good arid control |
| Avg DLI achieved | 18.2 mol | Range 3.8–28.1; 6 days <16 (pepper target) |

### Zone non-uniformity (hidden by `*_avg`)

| Zone | Avg RH | Avg VPD | Hours >85°F (corrected) | Overall compliance |
|------|--------|---------|-------------------------|--------------------|
| North | 66.8% | 0.90 | 7.2 h | 51.2% |
| South | 70.7% | 0.77 | 0.4 h | 57.3% |
| East | 66.7% | 0.90 | 0.3 h | 46.9% |
| West | 78.1% | 0.53 | 0.1 h | 45.1% |
| **Greenhouse (avg)** | — | 0.77 | — | **65.9%** |

West botrytis-window exposure: **~52.5 h** (corrected down from an inflated 259h — the original used a 5-min-per-sample assumption against ~1-min cadence). West condensation (VPD<0.4): ~112h. All four per-zone compliance figures sit well below the composite.

### Agronomic verdict

A **safe-but-suboptimal** climate graded against an over-conservative band. The binding 78°F daytime ceiling comes from `fn_target_band` taking the MIN of active crops' `temp_ideal_max` — strawberry's 78°F caps the whole house even though pepper tolerates 85°F. **Important correction:** the original "4 of 5 crops held hostage" framing was partially wrong — `fn_target_band` joins crop profiles on `crop_type = crops.name`, and the two heat-tolerant crops the finding cited (Canna, Vanda Orchids) have a name/crop_type mismatch and **contribute nothing to the band**. The real binding set is just strawberry (78), lettuce (80), pepper (85). That name-mismatch is itself a likely latent bug: two active crops are silently excluded from their own target band. Light is adequate on average but volatile and lux-derived (true PAR/PPFD columns are entirely NULL).

---

## 3. Equipment & Mechanical

### Runtime / duty / cost dashboard (14d)

| Equipment | Runtime | Duty | Cycles/day | Notes |
|-----------|---------|------|------------|-------|
| heat1 (1500W electric) | 164.8 h | 49.0% | 22.9 | Dominant load; short-cycles 4.6–11.9 min on warm days |
| vent | 103.9 h | 30.9% | 9.1 | |
| fan1 / fan2 | 77.9 / 75.9 h | 23.2 / 22.6% | ~21 each | Wear balanced to **0.8%** |
| grow_light_main / grow | 121.8 / 121.1 h | ~36% | ~8.7 | |
| fog | 19.9 h | 5.9% | 43.5 | 609 cycles, median ON 80s |
| mister_center | — | — | 61.9 | Highest cycle count |
| heat2 (gas) | 0 events since 05-24 | — | — | **Seasonally idle, not a fault** |

**Energy cost (runtime-based, correct path):** 14d electric $54.15 (488 kWh × $0.111), gas $13.28, total $84.14. Cost reporting deliberately uses the runtime estimate, so cost is sound.

### Reliability verdict

Relays and actuators are **mechanically healthy**: zero stuck relays (`v_relay_stuck` clean), near-perfect fan wear balance, anti-short-cycle guards in force, correct heat staging (0 heat2-without-heat1 inversions over 20 days / 240 activations). Three reliability issues, none catastrophic:

1. **Energy meter hard-broken (CONFIRMED, partial nuance):** the Shelly CT *meter total* (not just the heat channel) flatlined at a ~17.7W phantom baseline 2026-05-17→05-22 while heat1 ran 21h/day, then recovered ~05-23. Window `kwh_total` = 73.8 vs runtime-estimated 487.8 (6.6x). **Caveat:** the 6.6x blends the dropout with a *chronic* partial-circuit undercount — even the clean pre-dropout week ran 2.9–10x, so a healthy meter still undercounts ~3–5x. Likely a comms/power fault (reboot-storm), not a single clamp.
2. **ESP32 reboot storm (CONFIRMED, magnitude corrected):** real reboots clustered 05-15..05-25 (46 on 05-16, 43 on 05-18), with genuine 60s loops and a 6-deep Task-WDT loop on 05-15. **Correction:** "161" counts low-uptime diagnostics *samples*; distinct boots were ~35, and most are intended OTA-flash reboots. Self-resolved after 05-25 16:56 — but a fresh heap critical fired today 12:29.
3. **heat1 short-cycles on warm days** (4.6–11.9 min/cycle, 23–34 cycles/day) — contactor wear, not failure.

---

## 4. Irrigation / Misting / State Machine

### Irrigation — 🔴 BROKEN (open-loop)

**Quantitative:** Fertigation fired 11/11 days, all 4 zones every day at ~10:30 local, zero missed cycles. 14d water: 3,452 gal total (1,211 mister, 217 metered drip, 2,024 "unaccounted" — mostly real fog/cooling + a 05-15 meter-reset artifact, **not a leak**). The empty `irrigation_log` is **by design** (migration 134 retired it; events reconstructed from `equipment_state`).

**Qualitative:** Irrigation runs **fully open-loop** — all four required feedback signals are down: 3 center sensors `missing`, south_1 `stuck_zero`. **Both south soil probes died inside the window** (south_1 last positive 2026-05-16, south_2 2026-05-22); only `soil_moisture_west` survives. West soil crashed ~62%→0% in a single 60s sample on 2026-05-17 20:10 (a disconnection signature, not ET drying), pegged at a fault floor through 05-18 (1,432/1,432 samples below wilt), and recovered above its 35% min only on 05-29. **No soil-dryout/critical alert ever fired** (a `sensor_offline` warning did fire on 05-17/05-23 but never escalated). Center fertigation delivers chronically low/zero metered volume (1.64 gal/run avg) that is **unverifiable** — there is exactly one shared integer-resolution pulse meter, and center has no feedback instrumentation.

### Misting / Fog — 🟡 DEGRADED

**Quantitative:** Misting/fog cut local zone VPD by 0.36–0.37 kPa/cycle (south/west); 85.7% of 1,115 mister cycles produced a positive VPD drop; house VPD compliance 79%. Fog ran 1,008.9 min / 605 cycles; `below_threshold` (73.9%) dominates fog blocks = correct gating.

**Qualitative:** Two real problems plus **two REFUTED findings** that must not be presented as fact:
- ✅ **Severe zone imbalance (CONFIRMED):** mister_center carries ~73% of runtime while showing the *smallest* measured VPD drop (partly a measurement artifact — no center VPD sensor, falls back to diluted `vpd_avg`); mister_west ~10% despite strong local effectiveness. The house leans hardest on the zone it can least verify.
- ❌ **"Midnight reset missing in firmware" — REFUTED.** A midnight reset DOES exist, in `firmware/greenhouse.yaml:256` (`on_time` cron at 00:00 zeroes `mister_water_today`). The original finding grepped only the `greenhouse/` subdir and missed the parent file. The real (narrower) issue: the cron occasionally doesn't fire when SNTP time is invalid at midnight, causing occasional carryover.
- ❌ **"Phantom 600-gal budget block suppressed cooling for 19 min" — REFUTED.** The "600" is the budget *capacity* (`/600`), not a stale counter; the actual counter was 2–5 gal. The 10:11:59 "clear" was an **OTA reboot**, not budget logic. `house_vpd_high` was ~0.79, not 1.288. A VPD-emergency override already exists (`controls.yaml:599-614`).
- 🟡 **`daily_summary.mister_water_gal` corrupted (PARTIAL):** uses `MAX()` which both over-counts on carryover days (600.1 vs ~233 real on 05-15) AND *under-counts* on reset days; reset-aware 14d total is ~949 gal, not the reported 1,211.

### State machine (FSM) — 🟡 DEGRADED (logic healthy; actuators limited)

**Quantitative:** IDLE 66.9% / VENTILATE 32.3% of 14d; peak 16 transitions/hr (cap 30); 16/16 invariants pass; 0 heat2-without-heat1 over 240 activations; 1 of 2,246 IDLE decisions with band error >1°F. ClimateAction mix: VENT_COOL 56.2%, MIST_ASSIST 17.9%, IDLE 16.3%, HEAT 7.1%, FOG_ASSIST 2.4%, DEHUM 0.2%.

**Qualitative:** The FSM is **not hunting** and makes correct choices. The degraded rating reflects an **actuator-authority ceiling, not a logic fault** (see Broken §7): VENT_COOL restores band only **22.8%** of the time with ~0°F error reduction; escalation tiers help directionally (MIST −0.17°F, FOG −0.48°F) but recover <15% because they engage only once already losing. Overrides are infrequent and all expected (`relief_cycle_breaker` fired once). The heap-recovery dispatch commits (8ff8d87, fb17f43) are **ingestor traffic-shaping, not firmware fixes** — a sound simplification to defer-only pushing.

---

## 5. Software Components

**Firmware (ESP32):** 🟡 Control core is excellent — 16/16 invariants over 193,525 rows, intact enums with compile-time `static_assert`s, 279 `cfg_*` readbacks, flat heap trend (+0.08 kB/day) on the stable build (running 95.7h). Degraded by: log channel dark 12 days (intentional, commit 90bc358), transient heap fragmentation (largest free block dipped to 3.25 kB once today), and prior deploy churn (30 versions/14d, peak 9 OTAs/day — predates 05-25 stabilization).

**Dispatcher:** 🟢 The dependable core. 47,893 setpoint rows/14d, 95.0% confirm rate, band latency p50 37s / p95 81s / p99 130s, **0** `esp32_push_failed`, **0** heap-deferred. Issues are second-order: silent rejection of operator irrig_* params (dormant/historical, 05-23 only), redundant guardrail re-pushes, and two fragile analytical views.

**Ingestor:** 🟢 Live and current — service up 2+ days, climate lag 0.8 min, 0 stale sensors, all 8 pipeline sources flowing, 19 periodic tasks fresh. Only one telemetry gap >15min in 14d (a 23.1-min process-restart gap on 05-24). **CONFIRMED bug:** `data_gaps` under-reports because restart-induced gaps reset `last_disconnected_at` to None (ingestor.py:1539), so the trust ledger is blind to them.

**Notebook (planner loop):** 🟡 Loop closes well — 40/42 plans graded with deterministic anchor scores, calibration good (mean self-grade gap +0.46), lessons validated/pruned. Two CONFIRMED breaks: (1) plan-accuracy views (`v_plan_accuracy`/`_72h`/`_by_day`/`v_plan_compliance`) are **structurally dead** (0 rows) because they key on band params the planner no longer emits (band params dropped at mcp/server.py:1131-1135); (2) `FORECAST_DEVIATION` — the most frequent planner event (48/14d) — is **invisible** to `planner_trigger_ledger`. Current planner score 64.9/100.

**Site / API:** 🟢 lab.verdify.ai rebuilt today (284 pages), api.verdify.ai serving 151 setpoints + 9 crop catalog entries, all 139 Grafana embeds live. CONFIRMED minor issues: `site-doctor` exits 1 on a 252h-stale `soil.md` snapshot served live; `site_content` RAG table 6 days stale (no scheduled refresh); dead apex `verdify.ai`→lab redirect in compose.

**Schemas / drift guards:** 🟡 Substantive contracts match reality (firmware enums mirrored, DB-vs-schema clean, 0 unknown enum/alert values in production). But the test suite is **RED: 6/562 fail** — all test rot (alert-envelope fixture lag, over-broad regex, stale string-match after a refactor), not real divergence. One genuine **latent** bug: `FOG_BLOCK_REASONS` omits `served`/`irrigation`/`vent_interlock`/`time_invalid`, which firmware emits and the DB stores 3,104 times; no live path validates the column, so it's latent today.

**TimescaleDB:** 🟡 2.25.2/PG16.11, live control path fresh (0–2 min), matviews refreshing, all jobs Success. Total 7.84M rows (README's "2.5M+" is 3.1x understated). Degraded by: 3 dead/stale pipelines (`esp32_logs` 12d, `irrigation_log` 62d, `weather_station` 7d intermittent) that `v_data_pipeline_health` **does not monitor**; and `setpoint_snapshot` = 58% of the 2.3GB DB, **uncompressed**, writing 134 unchanged rows/min.

---

## 6. What Is Working

- **Control determinism** — 16/16 firmware invariants pass over 193,525 corpus rows; FSM peak 16 transitions/hr vs 30 cap; 0 heat2-without-heat1 inversions across 240 activations.
- **Setpoint delivery** — 95.0% confirm rate, band latency p50 37s / p95 81s; **0** push failures and **0** heap-deferred rows in 14d.
- **Telemetry capture** — climate freshness 0.8 min, **0** stale sensors, ingestor up 2+ days, only one >15min gap in 14d.
- **Thermal safety** — min dew-point margin 5.0°F (zero condensation samples), only 12/19,905 samples >85°F, DIF +9.4°F, wide stress band held 97.8%.
- **Mechanical reliability** — zero stuck relays, fan wear balanced to 0.8%, correct seasonal heat staging.
- **Planner learning loop** — 40/42 plans closed-loop graded; self-grade calibration mean gap +0.46; deterministic anchor scoring uncontaminated by the kWh bug.
- **Planner reward integrity** — `planner_score`/`anchor_score`/cost all derive from the *correct* runtime kWh estimate, not the broken meter; **0** energy lessons polluted.
- **Public surface** — site rebuilt today (284 pages), 139 Grafana embeds live, API serving current data.
- **Evaporative cooling effectiveness** — 85.7% of mister cycles produced a positive VPD drop; south/west cut local VPD ~0.37 kPa/cycle.

---

## 7. What Is Broken

Ordered by severity. Verification status is explicit; refuted items are flagged and excluded from the problem list.

| # | Problem | Severity | Evidence anchor | Impact | Verification |
|---|---------|----------|-----------------|--------|--------------|
| 1 | **Irrigation fully open-loop; all 4 root-zone feedback signals down, both south probes dead** | 🔴 High | `v_irrigation_sensor_feedback_status`: south_1 stuck_zero, 3 center missing; south_1 last positive 05-16, south_2 05-22; soil never referenced in controls.yaml dispatch | Controller cannot detect under/over-watering at root zone; south Canna has zero live moisture telemetry | **CONFIRMED** |
| 2 | **No human ever physically verified any crop** | 🔴 High | 340/340 observations from gemini-vision (0 human); 0/7 crop_tasks completed; 0/640 checklist rows completed | Audit infers crop health from instruments it proved broken; no ground-truth fallback | **CONFIRMED (gap-fill)** |
| 3 | **West zone fully blind** (no working sensor, no camera, no active crop record) | 🔴 High | West crash 62%→2% in 1h; 0 west observations ever; crops zone 5 = "House Plants, contents unknown", is_active=false | Cannot know what is in west pots or whether they dried out | **CONFIRMED (gap-fill)** |
| 4 | **Cooling stack has a hard physics ceiling and is saturated** | 🔴 High | `greenhouse_logic.h:364-367` vent model collapses to −0.2°F when outdoor≥indoor; both fans ON in 95.7% of hot samples; 05-25 indoor 90.8°F vs outdoor 86°F | Cannot hold 78°F band against solar gain; compliance falling | **CONFIRMED** |
| 5 | **Energy meter undercounts electricity ~6.6x** (whole-meter dropout 05-17→22 + chronic partial coverage) | 🟡 Medium | `kwh_total` 73.8 vs estimate 487.8/14d; 05-18 0/274 samples >50W during 1,311 min heat1 | `kwh_total` headline misleading; reaches Iris's situational reasoning | **PARTIAL** (heat-channel→whole-meter; 6.6x blends dropout + chronic) |
| 6 | **Plan-accuracy views structurally dead (0 rows)** | 🟡 High* | `v_plan_compliance`/`v_plan_accuracy`/`_72h`/`_by_day` all 0; last band param in setpoint_plan 2026-05-12 | The "plan accuracy vs band" KPI surface is a silent-empty trap | **CONFIRMED** (citation corrected to mcp/server.py:1131-1135) |
| 7 | **FORECAST_DEVIATION triggers invisible to trigger-health surface** | 🟡 Medium | 48 deliveries/14d → 0 ledger rows; ledger only materialized from scheduled milestones (tasks.py:5575) | Highest-volume planner event undetectable in health monitoring | **CONFIRMED** |
| 8 | **3 dead pipelines unmonitored by `v_data_pipeline_health`** | 🟡 Medium | View covers 8 sources, omits esp32_logs (12d)/irrigation_log (62d)/weather_station (7d) | A health view blind to the tables most likely to silently die | **CONFIRMED** |
| 9 | **Firmware log channel (esp32_logs) dark 12 days** | 🟡 High* | Last row 2026-05-17 14:35; device alive (142 heap events after) | Primary RCA channel for firmware faults blind | **PARTIAL** — root cause is a *deliberate* heap-protection design (commit 90bc358), NOT lost env var |
| 10 | **Alert backlog 92% un-resolvable orphans** | 🟡 Medium | 61/66 unresolved = `sensor_offline` suppressed since 2026-03-23 on live sensors; auto-resolve can't reach disposition='suppressed' (tasks.py:2838-2842) | Real future warnings could be buried; backlog count means 1, 0, or 66 depending on query | **CONFIRMED (gap-fill)** |
| 11 | **`data_gaps` under-reports restart gaps** | 🟡 Medium | `if last_disconnected_at:` guard (ingestor.py:1539); 1 row recorded vs 2+ real gaps | Trust ledger blind to restart-induced telemetry loss | **CONFIRMED** |
| 12 | **`daily_summary.mister_water_gal` corrupted by MAX() over a non-monotonic counter** | 🟡 Medium | 600.1 stored vs ~233 real on 05-15; reset-aware 14d ~949 vs reported 1,211 | Misleads water dashboards both over- and under-count | **PARTIAL** (over- AND under-counts) |
| 13 | **CO₂ is uncalibrated ADC voltage with biologically-inverted curve, no plausibility gating** | 🟡 Medium | ADC GPIO36 raw transform (sensors.yaml:66-83); peaks midday, corr(temp)=0.346; 34 implausible samples pass schema | Feeds planner an agronomically misleading number | **CONFIRMED (gap-fill)** |
| 14 | **Schema test suite RED (6/562)** | 🟡 Medium | KeyError on `climate_action_proof_stale`, over-broad regex matching `ingestor`, stale string-match after refactor | Drift guards not protecting the contracts they claim to | **CONFIRMED** — all test rot, no real divergence |
| 15 | **South Canna AI observations contaminated by dead probe; pre-crash "dying/dead" read never followed up** | 🟡 Medium | gemini-vision parrots "soil 0.0%" as drought; 2026-05-15 "appear to be dying or dead" never actioned | Surviving automated channel inherits the failure it should back-stop | **CONFIRMED (gap-fill)** |
| 16 | **FOG_BLOCK_REASONS enum incomplete vs firmware (latent)** | 🟡 Medium | `served`/`irrigation`/`vent_interlock` stored 3,104×; strict `ClimateActionDecision` rejects all | Any future tool validating the column crashes on 3,104 rows | **CONFIRMED** (latent today) |
| 17 | **Pre-emptive heat pre-cool is dead-on-arrival** | 🟡 High* | heat_wave/extreme_heat target temp_high (band-owned) → always `skipped_band_owned` (forecast-action-engine.py:214-234) | Forecast-driven temperature pre-cool produces zero actuation | **CONFIRMED (gap-fill)** |
| 18 | **`site-doctor` exits 1 on live vault** (251h-stale soil.md served live) + `site_content` 6d stale | 🟢 Low | site-doctor.py:38 threshold 168h; soil.md:32 "2026-05-19 04:52" | Stale snapshot visible to site visitors; RAG snapshot lags vault | **CONFIRMED** |
| 19 | **`v_setpoint_compliance` times out (>120s)** | 🟢 Low | 5-way UNION ALL over full climate; ERROR at 12s and 120s | Unusable for live dashboards | **CONFIRMED** (companion `v_setpoint_velocity` parallel error **NOT reproduced** — drop/caveat) |

\* "High*" = the underlying issue is high-impact but the original framing needed correction (see verification column).

**Explicitly REFUTED — do NOT treat as problems:**
- ❌ "Misting water counter has no midnight reset in firmware" — **REFUTED.** Reset exists at `greenhouse.yaml:256`.
- ❌ "Phantom 600-gal budget block suppressed all cooling for 19 min" — **REFUTED.** "600" is the capacity ceiling; the clear was an OTA reboot; VPD override already exists.
- ❌ "v_setpoint_velocity intermittently errors under parallel query" — **NOT REPRODUCED** across 19 runs; treat as unverified/theoretical.
- ⚠️ "Reboot storm = 161 reboots" — magnitude **corrected** to ~35 distinct boots (sample-vs-event overcount); cited 05-25 burst was a misread.

---

## 8. What Can Be Improved

- **Re-baseline the crop band** (zone strawberries to the cool corner, relax global ceiling to ~82–84°F, add per-zone bands) → recover compliance against an *achievable* target instead of burning water/energy at 0–7% in-band success on hot days. Also **fix the crop name/crop_type mismatch** so Canna/Vanda actually participate in their band.
- **Add per-zone botrytis/condensation/heat KPIs** to `daily_summary` and the dashboards (stop relying on `*_avg`) → surface the hidden west wet corner and north hot corner (expected benefit: catch ~52h botrytis exposure that is currently invisible).
- **Enable compression on `setpoint_snapshot`** (compress_after 7d, segmentby parameter) and consider a change-only write model → likely reclaim ~0.8–1.0 GB (58% of the DB) and cut write amplification 5–38x.
- **Widen heat-rule forecast windows to 48–72h** and route pre-cool through the band engine → give 6 days' warning for the June 4–9 heat cluster instead of nothing until ~June 3.
- **Surface clamp/rejection events back to the planner** and gate band params at the MCP write layer → stop the planner re-requesting clamped values and silently dropping operator irrig_* params.
- **Add boot-loop and sustained-low-largest-free-block alerts** (e.g. ≥3 same-build reboots <120s in 10min; largest block <5 kB for N samples) → catch the next fragmentation/reboot episode early.
- **Standardize a single canonical `v_open_alerts` predicate** (`resolved_at IS NULL`) and reconcile the 61 orphan rows → backlog number stops meaning 1/0/66 depending on the query.
- **Demote noisy/expected log lines** (DisconnectResponse timeout traceback → single WARN) and exclude event-driven channels from staleness dashboards → genuine failures stand out.
- **Backfill provenance for the running 2026.5.25 build** and harden the override escape hatches in the deploy preflight → make replay-diff meaningful again.

---

## 9. Prioritized Remediation Roadmap

| Pri | Action | Owner | Evidence link |
|-----|--------|-------|---------------|
| **P0** | Operator/Emily physically walk the greenhouse: inspect & photograph south Canna + west floor pots, hand-test soil, log as `observer='human'` observations | coordinator (operator) | §7-#2, #3, #15 (gap-fill: plant verification) |
| **P0** | Restore irrigation feedback: re-seat/replace south SEN0601/SEN0600 probes; field-check west probe; bring up ≥1 root-zone probe/zone | firmware | §7-#1 (`v_irrigation_sensor_feedback_status`) |
| **P0** | Add soil-dryout critical alert (any live probe < wilt > 2h) — west crashed 11 days with no alert | ingestor | §4 Irrigation, §7-#1 |
| **P0** | Stage physical mitigation for the **June 4–9 heat cluster** (94–98°F): shade cloth, manual cooling, brief Iris to widen daytime band proactively | coordinator | §7-#4, #17 (gap-fill: forward heat risk) |
| **P1** | Stop `v_daily_kpi.kwh` preferring corrupted `kwh_total`; gate behind sanity check or fix Shelly scaling; tell Iris meter kWh is unreliable | ingestor / genai | §7-#5 (gap-fill: kWh propagation) |
| **P1** | Re-point/retire dead plan-accuracy views; INSERT ledger rows for FORECAST_DEVIATION/MANUAL deliveries | genai | §7-#6, #7 |
| **P1** | Extend `v_data_pipeline_health` + alert_monitor to cover esp32_logs/irrigation_log/weather_station | coordinator | §7-#8 |
| **P1** | Fix alert lifecycle: reconcile 61 orphan rows + include 'suppressed' in auto-resolve/dedup; standardize `v_open_alerts` | coordinator | §7-#10 (gap-fill) |
| **P1** | Decide crop band strategy (zone strawberries / per-zone bands) + fix crop name↔crop_type mismatch | genai / coordinator | §2, §8 |
| **P1** | Run measured A/B before re-enabling esp32_logs (set ESP32_LOG_LEVEL=WARN, watch largest-free-block 48h) — do NOT assert affordability | ingestor / firmware | §7-#9 (gap-fill: heap tradeoff unmeasured) |
| **P2** | Write a startup `data_gaps` row when DB-latest-ts vs now exceeds cadence (close restart blind spot) | ingestor | §7-#11 |
| **P2** | Change `mister_water_gal` rollup to reset-aware delta sum (not MAX); add midnight-reset SNTP fallback | firmware / ingestor | §7-#12 |
| **P2** | Enable compression on `setpoint_snapshot`; reconsider write cadence | coordinator | §5 TSDB, §8 |
| **P2** | Repair 6 red schema tests (alert-envelope fixture, write-path regex, mister-gate needles); extend FOG_BLOCK_REASONS + add DB-subset guard | coordinator | §7-#14, #16 |
| **P2** | Calibrate or relabel CO₂ as "uncalibrated index"; add plausibility gating; drop/annotate in planner context | firmware / genai | §7-#13 (gap-fill: CO₂) |
| **P2** | Refresh soil.md snapshot (clear site-doctor exit 1); add live-vault site-doctor timer; schedule `site_content` refresh | web | §7-#18 |
| **P2** | Time-bound/materialize `v_setpoint_compliance`; backfill firmware provenance; harden deploy override gates | coordinator / firmware | §7-#19, §1 |
| **P3** | Demote DisconnectResponse traceback to WARN; add boot-loop detector; remove dead apex redirect | ingestor / web / saas | §5 Ingestor, Site |

---

## 10. Methodology & Caveats

**Data window:** 14 days, ~2026-05-15 → 2026-05-29, America/Denver (MDT). All timestamps stored UTC; local-day analysis used `AT TIME ZONE 'America/Denver'`.

**What was verified:** Every quantitative claim in this report traces to a SQL query actually run against `verdify-timescaledb` or a `file:line` actually read, per the source audits. Findings carry explicit verification verdicts (confirmed / partial / refuted / not-checked). I have **respected those verdicts**: refuted findings (firmware midnight reset, phantom 600-gal block) are excluded from the problem list and flagged as refuted; partial findings are presented with their corrections inline (esp32_logs root cause, energy meter scope, reboot count, VPD compliance, zone-hour magnitudes, crop band participation).

**Verified vs not:** The 5 hard-broken findings carrying the most weight (irrigation open-loop, plan-accuracy views dead, FORECAST_DEVIATION invisible, alert orphans, data_gaps blind spot, schema test rot) were independently reproduced. The "not-checked" working/info findings (dispatcher core, ingestor liveness, several climate-safety metrics) were taken from the source audits without independent re-run but are internally consistent with the reproduced data.

**Known data-quality caveats:**
- **`climate` cadence is ~60s, not 5 min.** Several original "stress hours" figures were inflated ~5x by a count×5min assumption; corrected values are used here (VPD-high hr14 ≈7h not 34h; west botrytis ≈52h not 259h; VPD compliance ~79% not 90.6%).
- **Reboot/heap counts:** raw low-uptime diagnostics rows count *samples*, not *events* (~5 samples per boot). "161 reboots" → ~35 distinct boots; the dominant signal is intended OTA flashing.
- **Energy:** `kwh_total` is the broken Shelly meter; **cost** uses the correct runtime estimate. Treat any absolute `kwh_total`/`measured_kwh` as unreliable.
- **`climate_action_log` only populated since ~2026-05-25**, so effectiveness/decision conclusions cover ~4.5 days, not the full 14.
- **CO₂** is uncalibrated ADC voltage with an inverted diurnal curve — not true ppm.
- **Crop health is AI-vision-only and partly sensor-contaminated** — no human ground truth exists for the window; west has no instrumentation at all. Any "plants are fine/stressed" statement is an inference, not an observation.
- **The "94.5°F next 24h" premise in one gap-fill question was incorrect** — tomorrow (May 30) peaks at 78.3°F; the real heat risk is the forecastable June 4–9 cluster.
