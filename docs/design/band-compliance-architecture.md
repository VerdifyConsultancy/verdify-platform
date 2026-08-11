# Target Band + Compliance Architecture (Rearchitecture)

**Author role:** firmware / climate-control engineer (cross-cutting; shared-territory items route to coordinator).
**Date:** 2026-05-29 (America/Denver, MDT). **Status:** implementation-ready design.
**Greenhouse:** Vallery, 367 sq ft, Longmont CO (5090 ft, ~15% RH, 95F+ solar peaks). **Production** — live plants, ESP32 in-loop every 5 s.

This document is a clean, canonical replacement for how Verdify computes its **target band** and **compliance**. It builds directly **ON** `docs/design/vanda-zone-control-design.md` (the Vanda smooth-curve rewrite, migration 145) — it does not fork it. The band-source fix (migration 145) is a hard dependency; the compliance rearchitecture is **migration 146 + 147**, layered strictly after 145.

Every load-bearing claim is backed by a query or `file:line` read. Where the source recon or an earlier track draft asserted a number that did not reproduce against the live DB, the **verified** value is used and the discrepancy is called out inline. This is the rule: do not present a flawed or contradictory item as settled.

The historical companion work tracker is archived at
`/Users/jason/Orbit/context_dump/verdify-platform/docs/backlog/verdify-unified-backlog-2026-05-29.md`
(workstream "Band + Compliance Rearchitecture", items D4-D8 and G1-G9).

---

## 1. Why — the current method is broken

There are **three** separate, mutually inconsistent compliance/stress computations live today, all **binary** (in-band = 1, out = 0), none graded, none feasibility-aware, and the **only one that feeds the planner reward is house-level** (a single air-volume average vs the single served band), not per-zone.

### 1.1 The three tangled computations (verified)

1. **Authoritative — `daily_summary` (house, binary).** `ingestor/tasks.py` `_refresh_daily_summary_for_date` (~L4960–5019) loops every climate row for the local day, reads `temp_avg`/`vpd_avg` (house averages), tests them against the **served house band** reconstructed from `setpoint_changes` via the in-Python step function `_band_at` (L4960–4966; `HOUSE_BAND_PARAMS` at L130–132). The test is strictly binary: `t_ok = tl<=temp<=th`, `v_ok = vl<=vpd<=vh`; `compliance_pct = both_in_band/n*100` (L5001–5013). The **same loop, same band, same averages** also produces `stress_hours_*`: `temp>th → heat`, `temp<tl → cold`, `vpd>vh → vpd_high`, `vpd<vl → vpd_low` (L4993–5000). So compliance and stress-hours are **literally the same binary test** — one as fraction-of-readings, one as sum-of-minutes. Fully redundant.

2. **Per-zone but slow/dead — `v_setpoint_compliance` → `fn_compliance_pct(interval)`.** A 5-way `UNION ALL` (south/north/east/west/greenhouse) over the **full** `climate` table (~288k rows) with **no internal time bound**, each row `LEFT JOIN LATERAL`×4 into `setpoint_changes`. Times out **>120 s** (backlog M11). It grades each zone's *reading* against the **same single house served band** — not against what is actually planted in that zone — and `rh_in_range` is hardcoded NULL. `v_plan_compliance`/`v_plan_accuracy` are **dead (0 rows)**.

3. **Third, divergent stress path — `v_stress_hours_today` → `fn_stress_summary`.** Uses `fn_setpoint_at` (which has an `fn_band_setpoints` fallback when a setpoint row is expired), unlike the authoritative Python `_band_at` over raw `setpoint_changes` — so the view and `daily_summary.stress_hours_*` can disagree.

### 1.2 What is structurally wrong

- **Binary, not graded.** A reading 0.1F out of band scores identically to one 15F out. Severity is invisible. There is no full/partial/zero credit.
- **House-level, not per-zone.** One physical air-volume average is graded against one served band. Per-zone reality is hidden. Empirically the divergence is huge: `fn_compliance_pct('6 hours')` returns **east temp 0.0%**, west ~67%, greenhouse ~42% — all against the *same* band. East (lettuce/strawberry/pepper, cool-loving) reads 0% because the house band tracks the broken **78F strawberry ceiling** while east air sits hot.
- **Grades against the served band, not the agronomic target.** Compliance asks "did we hold the band we served?" not "is each crop in its agronomic comfort zone?" When the served band is itself broken (the unachievable 78F midday ceiling), the metric is meaningless.
- **Conflates three different causes of a miss.** A hot-miss with vent ON, both fans ON, and outdoor ≥ indoor (exhaust-only box physically cannot cool below ambient) is scored identically to a hot-miss where the 2nd exhaust fan was idle and outdoor < indoor (a real controller error). Today's metric blames the controller for the weather. Verified: of hot-miss minutes in the rich window, **73.4%** are physically unachievable (vent ON, outdoor ≥ served target, avg outdoor − target = +2.1F).

### 1.3 What is load-bearing (and what is safely off the control path)

Compliance is **off the live control path.** The dispatcher (`scripts/setpoint-server.py:309–311`) calls only `fn_band_setpoints(now())`, `fn_house_vpd_control_band(now())`, `fn_zone_vpd_targets(now())` — never any compliance function. So this rearchitecture **cannot break the 16/16 firmware invariants or the dispatcher 95% confirm.** The one hard wire is **planner reward integrity**:

```
daily_summary.compliance_pct
  → v_planner_performance.compliance_pct  (+ stress_hours_* → total_stress_h)
  → v_plan_window_scorecard.compliance_pct (day-weighted)
  → fn_plan_anchor_score()  (deterministic 9-/10-tier compliance × stress lookup ladder)
  → plan_journal.anchor_score
  → plan_evaluate deviation guard (|self_score − anchor| > 2 warns Iris)
parallel: daily_summary → v_daily_kpi.planner_score / v_planner_performance.planner_score
          = compliance_pct/100*80 + GREATEST(0,1−LEAST(cost_total/15,1))*20
```

**Verified plan_journal state (corrects the earlier "228 frozen anchors" claim):**
`SELECT count(*) FILTER (WHERE anchor_score IS NOT NULL), count(*) FILTER (WHERE outcome_score IS NOT NULL), ... FROM plan_journal` → **103 anchor_score, 228 outcome_score, 2 both-null, 125 outcome-without-anchor, 230 total.** The historical-freeze invariant governs the **103** anchored rows; **228** is the outcome_score population; **125** plans were self-scored under the binary scale but have no anchor (a distinct population eligible for one-time anchor backfill on the new scale without overwriting any frozen anchor).

---

## 2. The three locked architectural decisions

Set by operator Jason, 2026-05-29. We design exactly to these.

1. **SPATIAL = HYBRID.** Compute **per-zone** grading bands (each zone graded against what is actually planted there; the Vanda center has its own curve). The **single enforced control line follows the priority zone (Vanda center)**. House score = **aggregate of per-zone compliance**. Temperature is one physical air volume (one set of fans / one vent / no zonal cooling) → per-zone temp bands are for **grading/compliance only**; the served control temp line is **one** house curve = the Vanda/center band (consistent with Vanda design §3.2).

2. **COMPLIANCE = GRADED + FEASIBILITY-AWARE.** *Graded:* in the ideal band → full credit (1.0); within the stress band → linear partial credit; beyond stress → 0 (using `crop_target_profiles.temp_stress_low/high` + `vpd_stress_low/high`). *Feasibility:* decompose every miss into **controller-error** (corrective actuator authority was available and unused) vs **physically-unachievable** (e.g. outdoor ≥ indoor with vent saturated, both fans already ON). Emit **both** a raw compliance and a controller-attributable compliance.

3. **TARGET SOURCE = BLEND agronomic + achievable envelope.** Anchor on declarative `crop_target_profiles` (NOT empirical p25–p75 self-reference). Smooth physics-aware interpolation. Then **clamp/widen to a per-season physically-achievable envelope** (this systematizes the Vanda design's hand-set 86–88F served cap).

---

## 3. Canonical model overview (one source of truth)

The diagram-in-prose. **`crop_target_profiles` is the only place a band number is authored.** Every band, envelope, and compliance grade derives from it through the catalog/`is_active`/season join. No function self-references empirical history as its target (that was `fn_target_band_smooth`'s flaw — deprecated).

```
            crop_target_profiles  (declarative agronomic truth: ideal + stress, per crop_type / hour / season)
                    │  catalog_id + is_active + greenhouse + season join  (the migration-145 fix; B0a/B0b/T1)
                    ▼
   ┌──────────────────────────────────────────────────────────────────────────┐
   │  fn_diurnal_interp(ts, night, day)   — ONE shared cos² thermal-lag engine  │   shared shape engine
   │     (extracted from migration-145 §3.4; sunrise/sunset from fn_solar_*)     │   so grading band == served line shape
   └──────────────────────────────────────────────────────────────────────────┘
        │                                                         │
        ▼ per-zone endpoints                                      ▼ orchid endpoints
   ┌───────────────────────────────┐               ┌──────────────────────────────────────┐
   │ fn_zone_band(zone, ts)  [NEW]  │               │ fn_center_band_setpoints(ts)  [mig145] │
   │  PER-ZONE GRADING band         │               │  orchid-only smooth band               │
   │  (ideal + stress, multi-crop)  │               └──────────────────────────────────────┘
   │  center→orchid · east→∩/∪      │                              │
   │  empty zones → _default        │             ┌────────────────┴───────────────────────┐
   └───────────────────────────────┘             ▼                                          ▼
        │                             ┌───────────────────────────────┐   ┌────────────────────────────────────┐
        │ ideal+stress per zone        │ fn_achievable_envelope(zone,    │   │ fn_active_noncenter_stress(ts) [NEW] │
        │ (grading inputs)             │   season, ts)  [NEW table+accr] │   │  non-priority safety floor/ceiling   │
        │                              │  agronomic-relative cap (§5)    │   └────────────────────────────────────┘
        │                              └───────────────────────────────┘                    │
        │                                            │  clamp to achievable + concession      │
        │                                            ▼                                        ▼
        │                              ┌──────────────────────────────────────────────────────────┐
        │                              │ fn_band_setpoints(ts)  — THE SINGLE SERVED CONTROL LINE    │
        │                              │  = center band, clamped to envelope + safety-floored.      │
        │                              │  Drop-in name+signature. → dispatcher L309 → ESP32         │
        │                              └──────────────────────────────────────────────────────────┘
        ▼
   ┌──────────────────────────────────────────────────────────────────────────────────────────────┐
   │ COMPLIANCE ENGINE (migration 146):                                                              │
   │  fn_grade_credit(x, stress_lo, ideal_lo, ideal_hi, stress_hi) → [0,1]  (graded piecewise)       │
   │  fn_zone_band_grade(start,end) — per-minute × per-zone graded credit + feasibility label         │
   │     (extends the EXISTING fn_band_trace; time-bounded; NOT the v_setpoint_compliance 5-UNION)    │
   │  fn_compliance_v2(interval), fn_house_compliance — rollups (raw + controller-attributable)        │
   │  daily_summary.*_v2 columns + daily_zone_compliance + compliance_zone_weights                    │
   │  graded stress-hours = deficit integral of the same grade (SUBSUMES the 3 binary stress paths)   │
   └──────────────────────────────────────────────────────────────────────────────────────────────┘
        │
        ▼  (migration 147, after dual-write co-existence window validated)
   v_planner_performance / fn_plan_anchor_score re-pointed to ctrl_compliance_pct + re-anchored ladder
```

Three concepts that today are conflated into one `fn_band_setpoints` are named separately:
- **Grading** — per-zone, against per-zone agronomic targets (`fn_zone_band`). Temperature grading bands are compliance-only, never actuated (decision #1).
- **Control** — one served line following the priority/center zone, clamped to the achievable envelope (decision #3), safety-floored/ceilinged so non-priority crops are not actively harmed (decision #1).
- **Achievability** — a per-season physical envelope (decision #3), the target-side dual of the compliance-side feasibility rule.

---

## 4. Target bands

### 4.1 `fn_zone_band(zone, ts)` — the per-zone GRADING band (NEW)

The genuinely new object decision #1 requires: a real per-zone temperature **grading** band, which does not exist anywhere today.

```sql
CREATE OR REPLACE FUNCTION fn_zone_band(
    p_zone text,                              -- 'center'|'east'|'north'|'south'|'west'
    p_ts   timestamptz,
    p_greenhouse_id text DEFAULT 'vallery'
) RETURNS TABLE(
    zone text,
    temp_low double precision, temp_high double precision,         -- ideal band (full-credit window)
    temp_stress_low double precision, temp_stress_high double precision,  -- graded partial-credit edges (0 beyond)
    vpd_low double precision, vpd_high double precision,
    vpd_stress_low double precision, vpd_stress_high double precision,
    crop_basis text,                          -- e.g. 'orchid' or 'lettuce∩pepper∩strawberry'
    is_proxy boolean                          -- TRUE for center (no vpd_center probe; HW-1/NB1)
) LANGUAGE plpgsql STABLE;
```

**Zone → crop resolution (the FIXED catalog/is_active/season join).** This is the single most important correctness fix and it is shared with migration 145's join rewrite. Today `fn_band_setpoints` does **no `is_active` join** and `fn_zone_vpd_targets` does a **name-based** join. The canonical join is catalog-id based + `is_active` + greenhouse + season-aware, exactly as migration 145 §3.4 / §5.1(c)+(h) specifies. It requires **B0a** (backfill `crop_target_profiles.crop_catalog_id` for pepper/strawberry — verified NULL today) to land first, in the same migration.

Verified zone occupancy:

| zone | active crops | profile crop_type | grading rule |
|---|---|---|---|
| center (1) | Vanda Orchids (cat 9) | orchid | single crop → orchid band (priority) |
| east (2) | lettuce, strawberry, pepper (cat 5,7,6) | lettuce/strawberry/pepper | **intersection (ideal) / union (stress)** — §4.2 |
| south (4) | Canna (cleared by T1) | — | post-T1: empty → `_default` band |
| west (5) | House Plants (`is_active=f`) | — | empty → `_default` band |
| north (3) | none | — | empty → `_default` band |

**East multi-crop rule (the completeness gap that no earlier track fully specified).** East has **three** active crops with conflicting bands. The grading band must be agronomically honest about *all* crops sharing that air:

- **ideal band = intersection** — `temp_low = MAX(temp_ideal_min)`, `temp_high = MIN(temp_ideal_max)`. Full credit only where *every* east crop is happy. Verified h14: `temp_high = MIN(lettuce 80, strawberry 78, pepper 85) = 78`.
- **stress band = union** — `temp_stress_low = MIN(temp_stress_low)`, `temp_stress_high = MAX(temp_stress_high)`. Partial credit anywhere *some* crop still tolerates; zero only when the most-tolerant crop is stressed. Verified h14: `temp_stress_high = MAX(lettuce 85, strawberry 90, pepper 95) = 95`; `vpd_stress_high = MAX(2.0, 2.2, 2.5) = 2.5`.

This yields graded partial credit between the tight ideal window (78F) and the widest stress edge (95F) — strictly more informative than binary, and it correctly down-weights east, which the broken house band currently scores at a misleading 0%.

**Empty zones (north / west / south-after-T1).** Emit a fixed `_default` house-comfort band (ideal 60–80F / stress 45–95F, vpd 0.4–1.4 / 0.2–2.0) stored as a non-joined `crop_target_profiles` row with `crop_type='_default'` so the grader never returns NULL and the house roll-up has a defined contribution. (Whether empty zones contribute to the house score at all is resolved by zone weights — see §6.3.)

**Multi-crop & historical note.** `fn_zone_band` is `STABLE` and reads the *current* `is_active` state, so it is a **live grading band**, not a historical audit band. `fn_zone_band('south', ts)` for any `ts` after T1 returns `_default` (Canna cleared), reflecting current occupancy. Historical re-scoring of pre-T1 days is out of scope for the band layer; the compliance backfill (§8) handles history with its own band reconstruction.

### 4.2 The single served CONTROL temp line — `fn_band_setpoints(ts)` (drop-in)

Migration 145 already specifies `fn_center_band_setpoints(ts)` (orchid-only smooth band) and the smooth `fn_band_setpoints` rewrite. This rearchitecture **reuses them verbatim** and wraps `fn_band_setpoints` with decision #3's two-stage source (agronomic anchor → achievable-envelope clamp) and decision #1's non-priority safety floors:

```sql
CREATE OR REPLACE FUNCTION fn_band_setpoints(target_ts timestamptz)   -- DROP-IN: same name/signature
RETURNS TABLE(temp_low double precision, temp_high double precision,
              vpd_low double precision, vpd_high double precision)
LANGUAGE plpgsql STABLE ROWS 1 AS $$
DECLARE c record; env record; floorT float; ceil_ float;
BEGIN
    SELECT * INTO c   FROM fn_center_band_setpoints(target_ts);          -- orchid smooth band (mig 145)
    SELECT * INTO env FROM fn_achievable_envelope('center', fn_current_season(), target_ts);

    -- decision #1 safety: never drive BELOW any active non-center crop's temp_stress_low,
    -- never serve a ceiling ABOVE any active non-center crop's temp_stress_high.
    SELECT MAX(temp_stress_low), MIN(temp_stress_high) INTO floorT, ceil_
      FROM fn_active_noncenter_stress(target_ts);                        -- post-T1: lettuce/strawberry/pepper

    temp_low  := GREATEST(c.temp_low, floorT, env.env_temp_low_floor);
    temp_high := LEAST(c.temp_high, env.env_temp_hi_cap, ceil_);
    -- VPD served line = house control band (kept), with mig-145 inversion clamp
    SELECT vpd_low, vpd_high INTO vpd_low, vpd_high FROM fn_house_vpd_control_band(target_ts);
    IF vpd_low > vpd_high THEN vpd_low := vpd_high; END IF;
    RETURN NEXT;
END; $$;
```

**Worked numbers (today, spring, h14 peak, post-145).** Center band peak ≈ `77.8–87.8F`; envelope cap (§5) ≈ `86–90F`; non-center stress ceiling `MIN(lettuce 85, strawberry 90, pepper 95) = 85` (post-T1; **Canna `stress_high=100` is cleared by T1 step (a) and does not participate** — the earlier draft's worked example omitted this note but the MIN is numerically unchanged). Served → `temp_high = min(87.8, env, 85) = 85.0`. The served midday ceiling rises **78 → 85F**.

**Documented tension (do not paper over).** The 85F served ceiling sits **exactly at lettuce's `temp_stress_high`**. Under the graded rule (§6.1, zero beyond stress), any east reading at 85F scores **0** for east-lettuce heat compliance. This is honest, not a bug: the box cannot be cooler than outdoor air, and the served ceiling is the minimum physiologically tolerable for the most-sensitive active crop. East lettuce earns **partial credit from 80F (ideal_max) to 85F (stress_high)** and 0 at 85F. The served ceiling is a physical safety constraint, not a comfort target.

### 4.3 Shared interpolation + solar helpers (NEW, thin)

`fn_zone_band` and the served line share **one** cos² thermal-lag helper `fn_diurnal_interp(ts, night, day)` so grading band and served line have identical shape (no slope mismatch). It is an extraction of migration-145 §3.4's inline math; endpoints come from the per-zone-resolved profile rows (`night = mean(hours 0–5)`, `day = mean(hours 13–15)`). Two thin zero-finders `fn_solar_sunrise_hour(ts)` / `fn_solar_sunset_hour(ts)` over `fn_solar_altitude` define the seasonal window (SEA-2). **All three must be `IMMUTABLE`** — they call only `fn_solar_altitude` (already `IMMUTABLE`), and the binary-search must use no mutable state or `now()`.

### 4.4 Deprecations (verified)

| Object | Status today | Disposition |
|---|---|---|
| `fn_target_band` (step) | dashboards/context only; off live path | Mark deprecated; repoint dashboards to `v_zone_band`; drop after one cycle. (CI grep guard targets Python/SQL migration files only — exclude `db/schema.sql`, `docs/`, and the 145/146 migration files themselves.) |
| `fn_target_band_smooth` (cosine, empirical p25–p75) | off live path BUT **NOT orphaned** | **`v_target_curve` depends on it** (verified via pg_depend; 289 live rows, dashboard surface). **Repoint `v_target_curve` → `v_zone_band` FIRST**, then drop the function. A bare `DROP FUNCTION` fails; `CASCADE` would silently drop `v_target_curve`. (The earlier "deprecate immediately, no live consumer" claim was wrong.) |
| `v_setpoint_compliance` | per-zone binary, full-table 5-UNION, >120s | Drop and replace with `fn_zone_band_grade` (time-bounded). Closes M11. |
| `v_plan_compliance` / `v_plan_accuracy` | dead (0 rows) | Drop; rebuild against the new curve under P1a. Not a 146 dependency. |
| `v_stress_hours_today` / `fn_stress_summary` | 3rd divergent stress path | Re-point to graded-deficit hours (preserve column for `alert-monitor`); see §6.5. |

---

## 5. Achievable-envelope blend (decision #3)

The Vanda design (§3.2) hand-sets the served midday cap at **86–88F** "because the box cannot reach 95F without shade." That is a magic number — eyeballed from the observed median. Decision #3 says: derive it from physics + a non-circular historical clamp, store it auditably, refresh it seasonally, and record the agronomic↔served gap as "tolerated, not ideal."

**The circular trap we avoid.** The deprecated `fn_target_band_smooth` makes empirical p25–p75 the *target* — self-referential ("the target is whatever we historically did"), which can never reveal a control deficiency. The envelope uses historical percentiles **only as a one-sided clamp on an independently-derived agronomic target — never as the target itself**, with provenance stored.

### 5.1 Derivation (corrected from the earlier Track-2 draft against live data)

Per `(zone, season, hour)` the envelope cap is the **max** of two independently-derived terms (max = take the more-achievable cap), with a critical third correction for hot days.

**Term A — actuator-authority (physics, the anchor).** Exhaust-only cooling cannot pull indoor below outdoor ambient; under solar load indoor sits above outdoor by a heat-gain offset.

```
env_cap_A(h) = outdoor_p50_season(h) + k · solar_p50_season(h) + cooling_margin
```

Verified coefficients (regression on saturated-stack samples, `fan1 ∧ fan2 ∧ vent` ON):
- `k ≈ 0.0035 F/(W/m²)` (corrected from the draft's 0.0038; R²≈0.10 — see §9 risk on precision), `cooling_margin ≈ 2.0F`.
- `outdoor_p50_season` and `solar_p50_season` from `weather_forecast` rolled to per-local-hour p50 over the season window (the *driving* exogenous variable — this is what keeps it non-circular and forecast-aware).

**Term B — historical-attainment clamp (guardrail, saturated-only).** The 90th percentile of indoor temp at hour `h`, **restricted to cooling-saturated samples** (all fans + vent ON) — "what the box could not beat even at full authority." Restricting to saturated samples is what prevents circularity (unsaturated samples reflect controller *choice*). **Verified live: `indoor_p90_saturated(h14) = 83.4F`** (n=934) — **NOT the ~88F asserted in the earlier draft (a 4.6F error).** Term A dominates; the claimed "88.9 vs 88.0 corroboration" does not exist and is removed.

```
env_cap_B(h) = indoor_p90_saturated(h)        -- 83.4F at h14 today (spring-regime; see §9)
env_temp_hi_cap_authority(h) = max(env_cap_A(h), env_cap_B(h))
```

**Term C — the agronomic-relative achievable cap (the critical fix the earlier draft was MISSING).** On the hottest days, `env_cap_A` *exceeds* the agronomic ideal, making the `LEAST(agro, env)` clamp an **identity** (no clamp) precisely when heat stress is worst. Verified June forecast (h13–15): June 4 p50 93.8F, solar ~1019 → `env_cap_A = 93.8 + 0.0035·1019 + 2 = 99.4F > 95F orchid ideal`, so `LEAST(95, 99.4) = 95` — the envelope does nothing on a 94F day. (The completeness recon corrected the prior critique's "10 of 13 days ≥95F" to a verified **2 of 13 ≥95F, 7–8 ≥90F**; the structural inertness is nonetheless real on all ≥90F days.)

The fix: the envelope must store, alongside the cap, an **expected-achievable-median** so the system can say "served 95F but expected achievable median 99F → misses are unachievable" rather than pretending 95F is reachable. We bound the served cap to stay meaningfully below the agronomic ideal when outdoor approaches it:

```
env_temp_hi_cap(h) = min( env_temp_hi_cap_authority(h),  agro_ideal_hi(h) − overheat_slack )
env_temp_achievable_p50(h) = outdoor_p50(h) + k · solar_p50(h)   -- stored for the planner/feasibility
```

with `overheat_slack ≈ 2.0F` (operator-tunable, stored). This guarantees the served cap is always at least `overheat_slack` below the agronomic ideal — so the clamp is never a pure identity — and the planner sees `env_temp_achievable_p50` to know that on a 99F-achievable day the served 93F band's hot-misses are physically unachievable. **The envelope must fail open-to-achievable, never open-to-ideal** — on a missing-season lookup, fall back to the nearest season tagged `cap_source='authority', note='season_fallback'`, never return NULL (which would re-introduce the unclamped 95F regression).

### 5.2 Storage + refresh (precomputed table, not a live view)

A live envelope view that scans `climate` at query time would inherit the `v_setpoint_compliance` >120s blowup. The envelope changes on a *seasonal* timescale, so it is **precomputed and stored** (coordinator-owned shared territory):

```sql
CREATE TABLE achievable_envelope (
  greenhouse_id text NOT NULL DEFAULT 'vallery',
  zone text NOT NULL, season text NOT NULL, hour_of_day int NOT NULL,
  env_temp_lo_floor double precision NOT NULL, env_temp_hi_cap double precision NOT NULL,
  env_temp_achievable_p50 double precision,                  -- expected achievable median (§5.1 Term C)
  env_vpd_lo_floor double precision, env_vpd_hi_cap double precision,
  cap_source text NOT NULL,                                  -- 'authority'|'historical'|'agronomic_relative'|'tie'
  authority_inputs jsonb,   -- {outdoor_p50, solar_p50, k, cooling_margin, residual_stdev}
  historical_inputs jsonb,  -- {indoor_p90_saturated, n_saturated_samples, n_hot_samples, window}
  derived_at timestamptz NOT NULL DEFAULT now(),
  is_active boolean NOT NULL DEFAULT true,
  PRIMARY KEY (greenhouse_id, zone, season, hour_of_day)
);
```

A `fn_achievable_envelope(zone, season, ts)` accessor does a cheap indexed point-lookup with hourly interpolation (dispatcher-safe, <5ms). The `refresh_achievable_envelope` job (ingestor-owned, coordinator-validated because it writes a shared table) recomputes:
- **Cadence:** on each `fn_current_season()` transition (the June-1 switch is the first) + a weekly within-season re-derivation.
- **Term B gating (corrected from the draft):** require `n_saturated_samples ≥ 30` **AND** `n_hot_samples ≥ 20` where `outdoor_temp ≥ 80F`. **Every current saturated sample is from spring** (max outdoor h14 in the relay_truth window = 84.6F), so the initial summer Term B is spring-biased; the hot-sample gate forces fallback to Term A (`note='insufficient_hot_samples'`) rather than presenting a spring number as a summer guardrail. Also a plausibility gate: if indoor exceeds outdoor by more than `k·solar_max + 5F`, exclude the sample (a vent-stuck-shut failure would otherwise inflate Term B and hide a mechanical fault as "achievable").
- **Non-circularity, by construction:** the served target is never fed back into the envelope. The envelope reads outdoor weather + saturated-stack indoor; the served line reads the envelope; nothing reads the served line back. A `hardware_epoch` marker prevents pooling pre/post-shade samples when shade (H1) lands.

### 5.3 Concession — "tolerated, not ideal"

The clamp discards information the planner needs. Make it first-class: per served hour emit `agronomic_temp_hi`, `served_temp_hi`, `concession_temp_f = agronomic − served (≥0)`, `concession_reason = cap_source`. This rides into setpoint emission (`agronomic_*` + `concession_*` shadow columns, coordinator migration). It is the **target-side dual of the compliance feasibility "physically-unachievable" bucket** (§6.2): when the planner sees a 6F concession at midday it knows the gap is hardware (no shade), not a control failure, and must not reward/penalize itself for it. It is also the quantitative shade-ROI metric (H1): "center conceded N degree-hours for 14 days = the case for shade cloth."

### 5.4 Worked center-zone example (CORRECTED, summer h14)

Using verified live inputs (replaces the draft's non-reproducible 88.9F):

```
outdoor_p50(h14 summer)  ≈ 85.1F      (June+ forecast p50, conservative)
solar_p50(h14 summer)    ≈ 950 W/m²
k = 0.0035, cooling_margin = 2.0
agro_ideal_hi(h14)       = 95F (orchid true ideal), overheat_slack = 2.0F

Term A  = 85.1 + 0.0035·950 + 2.0           = 90.4 F
Term B  = indoor_p90_saturated(h14) = 83.4F  → GATED OUT (spring-biased, no hot samples) → authority-only
authority cap = max(90.4, [B gated]) = 90.4 F
agronomic-relative cap = 95 − 2.0 = 93.0 F
env_temp_hi_cap(h14) = min(90.4, 93.0) = 90.4 F     cap_source = 'authority'
env_temp_achievable_p50(h14) = 85.1 + 0.0035·950   = 88.4 F

served_temp_hi(h14) = LEAST(agro 95, env_cap 90.4) = 90.4 F   (further LEAST with non-center stress ceiling)
concession_temp_f   = 95 − 90.4 = 4.6 F   reason='authority'  (physics, no shade)
```

This **systematizes** the Vanda hand-set 86–88F (it lands just above it, from first principles, with provenance) and sits comfortably above the saturated-stack p50 (~80.9F at h14) so the box reaches it — cooling un-pins, futile-cooling duty drops. When shade (H1) lands, `k` re-fits lower (~0.0025 at 35% shade) → `env_cap_A` drops → the served ceiling moves toward the agronomic ideal and `concession_temp_f` shrinks automatically — no hand-re-tune.

---

## 6. Compliance engine (migration 146)

### 6.1 The graded piecewise formula (decision #2, graded)

For a reading `x` against `(stress_lo, ideal_lo, ideal_hi, stress_hi)`, graded credit `g ∈ [0,1]`:

```
g(x) =
  1.0                                          if  ideal_lo ≤ x ≤ ideal_hi          (full credit, in ideal)
  (x − stress_lo)/(ideal_lo − stress_lo)       if  stress_lo ≤ x < ideal_lo          (linear partial, cold/dry side)
  (stress_hi − x)/(stress_hi − ideal_hi)       if  ideal_hi < x ≤ stress_hi          (linear partial, hot/wet side)
  0.0                                          if  x < stress_lo  or  x > stress_hi  (beyond stress)
```

Continuous: `g=1` at both ideal edges, ramps linearly to `g=0` at the stress edges. Guard denominators (`NULLIF`, `GREATEST(0,...)`). Implemented as a pure scalar:

```sql
CREATE OR REPLACE FUNCTION fn_grade_credit(
  x numeric, stress_lo numeric, ideal_lo numeric, ideal_hi numeric, stress_hi numeric
) RETURNS numeric LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT CASE
    WHEN x IS NULL THEN NULL
    WHEN x BETWEEN ideal_lo AND ideal_hi THEN 1.0
    WHEN x < stress_lo OR x > stress_hi THEN 0.0
    WHEN x < ideal_lo  THEN GREATEST(0, (x - stress_lo)/NULLIF(ideal_lo - stress_lo, 0))
    ELSE                    GREATEST(0, (stress_hi - x)/NULLIF(stress_hi - ideal_hi, 0))
  END;
$$;
```

Apply independently to temp and VPD. **Zone sample score = geometric mean** `zone_score = sqrt(g_temp · g_vpd)` (a zone is only fully compliant if both axes are good; punishes one-axis collapse harder than arithmetic mean, matching the old "both_in_band" intent without being binary). Center grades against `temp_avg`/`vpd_avg` proxy until HW-1/NB1 (`is_proxy=true`).

### 6.2 Feasibility decomposition (decision #2, feasibility-aware)

Verified miss inventory (rich window, relay_truth since 2026-05-24 22:47 MDT): HOT 9,359 misses / 73.4% unachievable (vent ON & outdoor ≥ served target); COLD 23 misses / 23 both-heaters-ON (100% maxed); VPD-HIGH 6,525 misses / 99.3% co-occur with vent ON (cooling-conflict — venting dry 15%-RH air, fog blocked by vent-interlock; budget never binding, 0 ON-events in 14d).

Per miss-minute classifier:

```
HOT  (g_temp hot-side < 1):
   UNACHIEVABLE if  vent=ON AND outdoor_temp_f ≥ served_temp_high          (primary rail: can't beat ambient)
                 OR (vent=ON AND fan1 AND fan2 AND outdoor_temp_f ≥ indoor) (full stack, 2nd fan futile)
   CONTROLLER   otherwise   (a cooling stage was idle while outdoor < indoor)

COLD (g_temp cold-side < 1):
   UNACHIEVABLE if  heat1 AND heat2 ;  CONTROLLER otherwise

VPD-HIGH (too dry, g_vpd dry-side < 1):
   UNACHIEVABLE if  ( vent=ON AND (temp_band_error_f > 0 OR outdoor_temp_f ≥ served_temp_high) )   ← TIGHTENED
                 OR fog=ON OR any mister=ON
                 OR mister_budget_exceeded
   CONTROLLER   otherwise
```

**The TIGHTENED VPD-high rule (correcting the earlier "UNACHIEVABLE if vent=ON" as too broad / gameable).** Verified: **519 of 6,611** VPD-high+vent-ON misses (7.9%) occur with `temp_band_error_f ≤ 0` (temp already in band). Crediting those as unachievable is gameable — a controller could run vent whenever temp *approaches* to harvest "unachievable" credit. The rule now requires venting be genuinely forced by heat (`temp_band_error_f > 0` or outdoor ≥ target); vent-while-temp-OK is reclassified CONTROLLER.

```
VPD-LOW (too humid): UNACHIEVABLE if vent=ON AND outdoor_rh_pct ≥ indoor_rh (rare in 15% RH); else CONTROLLER.
```

**Emit BOTH compliances:**
```
raw_compliance(z)  = 100 · mean_t zone_score(z,t)
ctrl_compliance(z) = 100 · mean_t [ zone_score   if feas=CONTROLLER ;  1.0 if feas=UNACHIEVABLE ;  1.0 if no miss ]
```
i.e. controller-attributable = raw with unachievable misses scored as full credit. Primary unachievable rail = `outdoor ≥ served_temp_high`; a stricter secondary rail (`outdoor ≥ indoor`) is also emitted. Rows **before 2026-05-24 22:47** (no `relay_truth`) reconstruct relay state from `equipment_state` last-event-before-ts (reuse the existing forward-fill, `tasks.py:5050–5108`) and emit a **`feasibility_unknown`** bucket rather than mislabel (block-reason granularity is forward-only).

### 6.3 Zone → house aggregate (decision #1)

House score is a priority-weighted aggregate, with weights in a `compliance_zone_weights` table (coordinator-owned, **does not exist yet** — verified; created in migration 146):

```
house_graded_compliance = 100 · Σ_z (w_z · raw_z/100) / Σ_z w_z      (same for ctrl_compliance with ctrl_z)
```

**Resolving the empty-zone weighting gap (completeness item).** Active crops occupy only center + east post-T1 (north/south/west have no active crop). A `north=0.20` weight against a NULL-crop profile would NULL or miscalibrate the house score. Decision: **drop empty/inactive zones from the aggregate** (consistent with how south/west are treated) and place the weight on the crops that exist:

| zone | weight `w_z` | rationale |
|---|---|---|
| center (Vanda) | **0.60** | priority zone, the served control line follows it |
| east (food crops) | **0.40** | active lettuce/strawberry/pepper |
| north / south / west | **0** | no active crop; `_default` band graded for dashboards but excluded from the house reward |

Weights live in the table so they re-tune (e.g. when a crop is placed in north) without code change and so the planner prompt can cite them. The single house number plugs **drop-in** into the unchanged `planner_score` formula.

### 6.4 Performant canonical objects (decision #2 throughput)

Cardinal lesson: **never scan full `climate` with correlated LATERALs at query time** (that is what makes `v_setpoint_compliance` time out). The canonical engine **extends the already-existing `fn_band_trace`** (verified: `fn_band_trace` is time-bounded by argument `WHERE c.ts >= p_start AND c.ts <= p_end`, CROSS JOINs `fn_band_setpoints` and LEFT JOIN LATERALs `setpoint_changes`, emitting `crop_*_in_band`/`fw_*_in_band` — `api/main.py:1409–1437` is the precedent), graduating its binary `BETWEEN` flags into graded credit and adding zones + feasibility. It inherits the **good** shape (time-bounded), not the bad one.

```sql
-- fn_zone_band_grade(start, end): one row per (climate minute × graded zone {center,east,north})
--   * time-bounded climate window (NEVER full-table; resolves M11)
--   * per-zone reading: center=temp_avg/vpd_avg proxy; east=temp_east/vpd_east; north=temp_north/vpd_north
--   * agronomic band per zone from fn_zone_band (center=orchid; east=∩/∪; north=_default)
--   * served band from setpoint_changes (ONE house line)
--   * g_temp, g_vpd via fn_grade_credit; zone_score = sqrt(g_temp·g_vpd)
--   * feasibility from relay_truth (≥05-24) or equipment_state forward-fill (<05-24 → unknown)
CREATE OR REPLACE FUNCTION fn_zone_band_grade(p_start timestamptz, p_end timestamptz)
RETURNS TABLE(ts timestamptz, zone text, g_temp numeric, g_vpd numeric, zone_score numeric,
              feasibility text, proxy_center boolean) STABLE ROWS 100000;
```

**Materialization (corrected — CAgg-on-view is invalid in TimescaleDB 2.25.2).** The earlier draft proposed a continuous aggregate over a regular VIEW with a LATERAL-to-function call. Verified TS version = **2.25.2**; CAggs must be defined directly on a hypertable (or another CAgg), and LATERAL-to-function is unsupported in a CAgg context. **Two valid options:**
- **(a)** A continuous aggregate defined **directly on the `climate` hypertable** with a regular-table `JOIN crop_target_profiles ctp ON ctp.crop_type = <zone_crop> AND ctp.hour_of_day = extract(hour from time_bucket AT TIME ZONE 'America/Denver') AND ctp.season = fn_current_season()` (regular-table joins are supported in TS ≥2.16), using `fn_grade_credit` (IMMUTABLE) inline. Feasibility (which needs relay state) is computed in a thin downstream rollup, not in the CAgg.
- **(b)** A plain materialized view refreshed by `pg_cron` (loses incremental refresh but supports the LATERAL-to-function shape).

Option (a) is preferred for incremental refresh. Either way, store per (bucket, zone): `sum(g_temp), sum(g_vpd), sum(zone_score), count(*)`, plus feasibility-split sums. This **replaces `v_setpoint_compliance` + `fn_compliance_pct` entirely.**

**Rollups (cheap, sub-second over the matview):**
```sql
fn_compliance_v2(lookback interval) RETURNS TABLE(
  zone text, temp_pct numeric, rh_pct numeric, vpd_pct numeric, overall_pct numeric,   -- legacy 5-col shape preserved
  temp_pct_graded numeric, vpd_pct_graded numeric, overall_graded numeric,
  overall_controller_attributable numeric, unachievable_frac numeric);
fn_house_compliance(lookback interval) -- applies §6.3 priority weights
```

`fn_compliance_v2` **must keep the legacy 5-column prefix including `rh_pct` (always NULL today)** so the `fn_compliance_pct` shim — which positional callers like `gather-plan-context.sh:221` (`SELECT *`) depend on — does not column-shift. The shim returns the legacy 5 columns from `fn_compliance_v2`.

### 6.5 Stress-hours unification (subsumes the 3 binary paths)

Stress-hours become a **deficit integral of the graded score** (decision #2 "graded subsumes stress hours"):
```
graded_stress_hours_heat(z)     = Σ_t (1 − g_temp) · (1/60)   over hot-side deficits  (temp > ideal_hi)
graded_stress_hours_cold(z)     = Σ_t (1 − g_temp) · (1/60)   over cold-side deficits
graded_stress_hours_vpd_high(z) = Σ_t (1 − g_vpd)  · (1/60)   over dry-side deficits
graded_stress_hours_vpd_low(z)  = Σ_t (1 − g_vpd)  · (1/60)   over wet-side deficits
```
(climate cadence ~60s, verified.) A minute fully beyond stress (`g=0`) contributes a full minute; at the ideal edge, 0; half-stressed, half. **graded_stress_hours ≤ binary_stress_hours at every threshold**, equal in the all-or-nothing limit — interpretable as severity-weighted minutes out of band. `v_stress_hours_today`/`fn_stress_summary` are re-pointed to graded-deficit hours.

### 6.6 The `ingestor/tasks.py` change (cheap, in the existing loop)

Graded+feasibility compute lives in the **same per-reading loop that already exists** (`tasks.py:4983–5008`) and reuses the relay forward-fill the function already builds (`tasks.py:5050–5108`):
1. Extend the readings SELECT (`L4968–4977`) to per-zone + feasibility inputs: `temp_east, vpd_east, temp_north, vpd_north, outdoor_temp_f, outdoor_rh_pct`.
2. Build a per-zone profile-band timeline once per day (24h × 3 graded zones, `season=fn_current_season()`) — O(72) rows, negligible.
3. In the loop, compute `g_temp`/`g_vpd`/`zone_score` (Python mirror of `fn_grade_credit`), classify feasibility from the forward-filled relay state + `outdoor_temp_f`, accumulate raw/controller/deficit sums.
4. **Keep the OLD binary accumulation untouched** (co-existence).
5. Extend the `UPDATE daily_summary` (`L5165–5195`) with the new params; `INSERT … ON CONFLICT` per-zone rows into `daily_zone_compliance`.

Cost: ~6 float ops/zone/row + one band lookup; sub-second per day, no extra `climate` scan. The matview serves intraday/dashboard/`gather-plan-context` needs so the heavy view path is eliminated everywhere.

### 6.7 Schema delta

```sql
ALTER TABLE daily_summary
  ADD COLUMN compliance_v2_raw_pct double precision,            -- priority-weighted house, raw graded
  ADD COLUMN compliance_v2_attributable_pct double precision,   -- priority-weighted house, controller-attributable
  ADD COLUMN compliance_v2_unachievable_frac double precision,
  ADD COLUMN graded_temp_compliance_pct double precision,
  ADD COLUMN graded_vpd_compliance_pct double precision,
  ADD COLUMN graded_stress_hours_heat double precision,
  ADD COLUMN graded_stress_hours_cold double precision,
  ADD COLUMN graded_stress_hours_vpd_high double precision,
  ADD COLUMN graded_stress_hours_vpd_low double precision,
  ADD COLUMN feasibility_unknown_min double precision;
-- existing binary columns (compliance_pct, temp/vpd_compliance_pct, stress_hours_*) are NOT mutated.

CREATE TABLE daily_zone_compliance (
  date date NOT NULL, zone text NOT NULL, crop_catalog_id int,
  raw_compliance_pct double precision, ctrl_compliance_pct double precision,
  graded_temp_compliance_pct double precision, graded_vpd_compliance_pct double precision,
  graded_stress_hours_heat double precision, graded_stress_hours_cold double precision,
  graded_stress_hours_vpd_high double precision, graded_stress_hours_vpd_low double precision,
  unachievable_min double precision, controller_miss_min double precision,
  proxy_flag boolean DEFAULT false,    -- true for center (vpd_avg proxy until HW-1)
  captured_at timestamptz DEFAULT now(), PRIMARY KEY (date, zone));

CREATE TABLE compliance_zone_weights (
  greenhouse_id text DEFAULT 'vallery', zone text, weight double precision, PRIMARY KEY (greenhouse_id, zone));
-- seed: center 0.60, east 0.40, north/south/west 0.
```

---

## 7. Consolidation + consumer re-point + planner-reward migration

### 7.1 Consumer re-point table

**Family 1 — reward / learning loop (must not corrupt; co-existence then deliberate swap in 147).**

| Consumer | Today reads | Re-point to | When |
|---|---|---|---|
| `v_daily_kpi.planner_score` | `compliance_pct` | `compliance_v2_attributable_pct` | 147 |
| `v_planner_performance` | `daily_summary.compliance_pct` + `stress_hours_*` | `compliance_v2_attributable_pct` + graded-deficit; keep binary exposed during co-existence | 147 |
| `v_plan_window_scorecard` | `v_planner_performance.compliance_pct`, `total_stress_h` | follows automatically (LEFT JOINs the view) | 147 |
| `fn_plan_anchor_score` | scorecard `compliance_pct`, `total_stress_h`, 9/10-tier ladder | new graded column **AND re-anchored ladder** (§7.3) | 147, same migration |
| `scripts/backfill-plan-evaluations.py` | `avg(v_planner_performance.compliance_pct)` → `_compliance_to_score` | new graded column; **freeze 103 anchored + 228 outcome rows; backfill only the 2 null/in-flight (and optionally the 125 outcome-no-anchor)** | 147 |
| `mcp/server.py scorecard()` + `fn_planner_scorecard` | `v_daily_kpi` | follows `v_daily_kpi` | 147 |

**Family 2 — display / evidence / metrics (semantics-tolerant; labels must change).**

| Consumer | file:line | Action | Owner |
|---|---|---|---|
| `scripts/verdify-metrics.py:69` | "both temp AND VPD in band" HELP | Add `verdify_compliance_graded_pct` + `_attributable_pct`; keep legacy gauge; fix HELP | web/ingestor |
| `scripts/generate-baseline-vs-iris-page.py:436` | hardcodes "both inside the firmware-enforced band" | **Rewrite public label** — false once graded+per-zone. Evidence-integrity → **web** | web |
| `scripts/gather-plan-context.sh:221` | `fn_compliance_pct('24 hours')` | → `fn_compliance_v2('24 hours')` (shim preserves 5-col shape) | genai |
| `ingestor/iris_planner.py:189/198/217` | "% both… firmware-enforced band. This drives the score." | **Rewrite prompt copy** to graded + per-zone + controller-attributable semantics | genai |
| `mcp/server.py:373` scorecard docstring | "both in firmware-enforced band" | Rewrite alongside prompt | genai |
| `scripts/alert-monitor.py:248` | `v_stress_hours_today.vpd_stress_hours > 2.0` | Keep working via re-pointed view (graded deficit); recalibrate threshold (§7.4) | ingestor |
| `ingestor/tasks.py:1271` | 2nd VPD-stress check on `v_stress_hours_today` | Re-point to graded center vpd_high | ingestor |
| `api/main.py:1388–1396` `/api/v1/scorecard`, `:2282`, `:2436` | `fn_planner_scorecard` → `ScorecardResponse` (`extra='forbid'`, **no try/except** at 1396) | **Schema-first:** `ScorecardResponse` must accept new metric keys BEFORE `fn_planner_scorecard` emits them; add belt-and-suspenders try/except at 1396 (mirror MCP 391–398) | coordinator (schema) + web |
| Grafana: `operations.json:5119` (`fn_stress_summary`), `greenhouse-owner-overview.json:1090` (`v_stress_hours_today`), `site-evidence-planning-quality.json`, `site-home.json` | various | Re-point/relabel; **re-run the live Grafana audit (`audit-grafana.py`) at Phase 4 — the "~13 dashboards / plant-health.json" inventory is stale** (`plant-health.json` does not exist) | web |
| `tests/test_02_database.py` (`required_db_objects` 34/83/89; row-count asserts 164–180; `test_compliance_returns` 561) | asserts `fn_compliance_pct`, `fn_stress_summary`, `v_stress_hours_today` | Add `fn_grade_credit`, `fn_zone_band_grade`, `fn_compliance_v2` to `required_db_objects`; add `test_compliance_v2_returns` (g∈[0,1], ctrl ≥ raw ≥ 0); update `fn_stress_summary` assertion to graded replacement | coordinator |

### 7.2 Reward recommendation: optimize controller-attributable

**Reward = `compliance_v2_attributable_pct`; raw graded + `unachievable_frac` are reported context, not scored.** Rationale: the dominant miss is the unachievable hot-rail (73.4%); rewarding raw punishes Iris for outdoor weather she cannot change. Controller-attributable rewards exactly the lever she owns (the 938 genuine-headroom minutes where the 2nd fan was idle while outdoor < indoor). But attributable-alone can hide a structurally-broken served band (everything labeled unachievable → looks perfect while plants cook), so `unachievable_frac` is surfaced as the planner's cue to **widen the served envelope** (decision #3 / migration 145), not work the actuators harder. The `planner_score` keeps its exact `comp/100*80 + cost*20` form — `comp` just becomes the attributable column. No formula edit; cost half stays byte-stable.

### 7.3 Migrating reward without corrupting the learning loop

Two corruption vectors, both fixed by **dual-write + freeze + re-anchor**:
1. **In-place mutation would re-scale every anchor.** Graded partial-credit is structurally higher than binary; with the unchanged `≥90→10, ≥85→9, …` ladder every anchor inflates and the `|self − anchor| > 2` guard miscalibrates. **Fix:** never mutate `compliance_pct`; add `*_v2` columns; dual-write through a co-existence window (binary keeps feeding the reward chain byte-stable).
2. **Re-running the backfill would re-grade history.** **Fix: freeze the 103 anchored + 228 outcome rows.** Backfill only the 2 null/in-flight (and, if coordinator chooses, the 125 outcome-no-anchor population) on the new scale once the ladder is re-anchored.
3. **Re-anchor the ladder in the SAME migration as the column swap** (147). Because graded-attributable shifts the distribution upward, fit new ladder cut-points by **quantile-match against the backfilled graded history**: find graded thresholds where the same ~10/20/…% of the 103 anchored plans fall in each anchor bucket as under binary, preserving rank-order and median anchor. Acceptance: re-deriving `fn_plan_anchor_score` on graded with the new ladder reproduces the same anchor for **≥90% of the 103 anchored plans** (ordinal stability), and the deviation-guard trip-count does not spike.

### 7.4 Stress alert recalibration

After migration 145 raises the orchid band to ~85–90F midday, graded center stress metrics drop structurally — the existing `2.0h` VPD-high threshold (calibrated against the broken 78F served band) could become permanently unreachable (dead alert). Recalibrate: set the threshold to `max(0.5, p75 of rolling-30d graded_stress_hours_vpd_high for center)` and document the value in the PR body. Same PR as the `alert-monitor` / `tasks.py:1271` re-point.

---

## 8. Migration plan (146 + 147) + phased rollout + historical validation

### 8.1 Prerequisite — `fn_current_season()` volatility fix (sequence into 145)

**Verified latent bug:** `fn_current_season()` is declared **`IMMUTABLE`** (`pg_proc.provolatile='i'`) but reads `now()` in its body. IMMUTABLE means "same inputs → same output," which is false when it reads the clock — PostgreSQL may constant-fold/cache it, returning a **stale season across the June-1 spring→summer flip** (the P0 deadline). Every season-dependent function (the 145 band rewrite, `fn_achievable_envelope`, the graded-compliance season JOIN) builds on it. **Fix:** `ALTER FUNCTION fn_current_season() STABLE` — one line, sequenced into migration 145 before any season-dependent function. This is a hard prerequisite for the whole rearchitecture, not optional.

### 8.2 Migration 146 — `146-compliance-rearchitecture.sql` (strictly AFTER 145)

146 reads 145's served band (`fn_band_setpoints` + `fn_center_band_setpoints`) and 145's re-authored orchid stress bounds (D2: 55/100 + 0.50/1.50; today's 45/95 + 0.1/1.8 are placeholders). Migrations are serialized — 145 merged + dispatcher/MCP bounced before 146 opens. **Shared territory → single coordinator PR.**

```
BEGIN;
146.1  GUARD: RAISE EXCEPTION if orchid temp_stress_high still = 95 (145 D2 not applied) — graded credit needs the real bounds.
146.2  fn_grade_credit (IMMUTABLE scalar, §6.1).
146.3  fn_zone_band(zone,ts) + fn_diurnal_interp + fn_solar_sunrise/sunset_hour (§4.1/§4.3) + crop_target_profiles _default rows.
146.4  achievable_envelope table + fn_achievable_envelope accessor (§5.2). [refresh job is ingestor-owned, separate]
146.5  fn_band_setpoints wrap (envelope clamp + fn_active_noncenter_stress safety) (§4.2)  -- replaces 145's fn_band_setpoints body.
146.6  fn_zone_band_grade(start,end) (§6.4, extends fn_band_trace; time-bounded) + matview (CAgg-on-hypertable or pg_cron MV).
146.7  fn_compliance_v2 + fn_house_compliance + compliance_zone_weights (seed 0.60/0.40/0) (§6.3/§6.4).
146.8  daily_summary *_v2 columns + daily_zone_compliance (§6.7). v_zone_band surface view.
146.9  fn_compliance_pct SHIM (5-col legacy shape from fn_compliance_v2); re-point v_stress_hours_today to graded deficit.
146.10 DROP VIEW v_target_curve (repointed to v_zone_band in 146.8); THEN COMMENT-deprecate fn_target_band_smooth / fn_target_band;
       DROP v_setpoint_compliance, v_plan_compliance, v_plan_accuracy (dead). (CI grep guard excludes schema.sql/docs/migrations.)
COMMIT;
```
**The reward swap is deliberately NOT in 146.** 146 only adds graded columns and dual-writes them; no reward number moves. That isolates the learning-loop risk.

### 8.3 Migration 147 — the reward swap (after Phase 2 gate)

Re-point `v_daily_kpi`/`v_planner_performance`/`fn_plan_anchor_score` to `compliance_v2_attributable_pct` + the re-anchored ladder; re-point `gather-plan-context.sh`; ship the genai prompt-copy rewrite; fill the 2 null outcome_scores; freeze the rest. **verdify_schemas first** (add graded/attributable/per-zone fields + drift-guard tests; M9's 6 rotted tests repaired in the same window) one cycle ahead of consumers.

### 8.4 Schema / restart / replay impact

- **DB-only, no OTA.** Compliance is off the control path (`setpoint-server.py:309–311`). 146/147 touch no served-band decision the firmware enforces → `firmware-replay-diff` THRESHOLD_PCT=0 stays green, **no OTA, no 48h bake, no weekly-OTA budget** (CLAUDE.md rules 1–3, 8 not engaged). Attach `make firmware-replay OLD=145-base NEW=146` showing 0% divergence as the artifact.
- **Restarts (CLAUDE.md rule 7).** 146: bounce **verdify-ingestor** (writes new columns) + **verdify-mcp** (scorecard/context read rollups); no dispatcher bounce (it doesn't read compliance). The `refresh_achievable_envelope` job: bounce **verdify-ingestor**. 147 + prompt copy (touches `mcp/server.py`): bounce **verdify-mcp** + **verdify-ingestor**. The PR body MUST name these (the 2026-04-21 staleness lesson).

### 8.5 Phased rollout

- **Phase 0 — prerequisite.** 145 merged, `fn_current_season()` STABLE, dispatcher+MCP bounced, orchid stress = 55/100 + 0.50/1.50, summer rows authored, `fn_band_setpoints` non-NULL all summer hours. The 146.1 guard enforces it.
- **Phase 1 — engine + offline backfill (no consumer/reward change).** Land 146. Backfill `daily_summary.*_v2` for all historical days (reuse `_band_at` + `equipment_state` forward-fill; feasibility windowed — full attribution only ≥2026-05-24 22:47, older → `feasibility_unknown`). Live loop dual-writes. **Reward untouched** — the safe co-existence window.
- **Phase 2 — validation against history (gate to Phase 3).** (1) Continuity: `compliance_pct` vs `compliance_v2_raw_pct` vs `_attributable_pct` per day — expect raw ≥ binary, attributable ≥ raw; on the falling days (verified 5/25 binary 50.0, 5/28 49.6, heat-stress 11.0h) attributable is markedly higher because 73.4% of hot-misses are unachievable. (2) Per-zone sanity: `fn_compliance_v2('6 hours')` reproduces the spread but graded vs per-zone profiles. (3) Ladder re-anchor fit: ≥90% of the 103 anchored plans reproduce their anchor (ordinal stability). (4) Deviation-guard regression: trip-count does not blow up.
- **Phase 3 — flip reward (147).** Gated on Phase 2. Bounce MCP + ingestor. Keep binary columns one more cycle for rollback.
- **Phase 4 — deprecate + relabel.** Web PR: Grafana panels (after live audit refresh), public baseline page, Prometheus HELP, evidence snapshots, dataset export — relabel "firmware-enforced band" to graded+per-zone+attributable. Confirm zero readers, then the 146.10 drops take effect. Rebuild `v_plan_compliance`/`v_plan_accuracy` under P1a.
- **Rollback:** binary columns remain populated through Phase 3; reverting 147 restores the exact prior reward. 146 is additive and safe to leave on rollback.

---

## 9. Risks + working-invariants preserved

**Working invariants preserved:**
- **16/16 firmware invariants + dispatcher 95% confirm:** untouched. Compliance is off the control path; 146/147 are DB-only; replay-diff = 0%. Validate migration 145's served-band change with `make firmware-replay` plus recorded `THRESHOLD_PCT` evidence (no relay/mode divergence expected — cooling un-pins, duty *drops*).
- **Planner reward integrity:** preserved by dual-write co-existence + frozen 103 anchored / 228 outcome rows + ordinal-stable ladder re-anchor. The reward number does not move until 147, gated on the ≥90% ordinal-stability test.
- **Consistency with Vanda design / migration 145:** 146 consumes 145's served band + re-authored stress bounds; the 146.1 guard hard-blocks shipping before 145 D2. Per-zone temp grading (decision #1) is compliance-only; the single served control line stays the center/Vanda house curve.

**Risks (with mitigations folded in from the critiques):**
- **Envelope precision is overstated.** The solar-gain `k` has R²≈0.10 (residual σ ≈ 3.5F → ±7F 2σ). The "88.9 vs 88.0" corroboration in the prior draft is spurious. **Mitigation:** store `residual_stdev` in `authority_inputs`; treat the cap as a median offset, not 0.1F precision; consider added regressors (prior-hour outdoor, wind) in a later refresh.
- **Envelope inert on the hottest days.** Without the Term-C agronomic-relative cap, `LEAST(agro, env)` is an identity when outdoor ≥ agro_ideal (verified 2 of 13 June days ≥95F, 7–8 ≥90F). **Mitigation (now in §5.1):** bound the cap to `agro_ideal − overheat_slack` and store `env_temp_achievable_p50` so the planner sees those misses as unachievable; the feasibility classifier (§6.2) absorbs them.
- **Term B spring-biased.** Every saturated sample is from spring (max outdoor h14 = 84.6F). **Mitigation (now in §5.2):** `n_hot_samples ≥ 20` at outdoor ≥ 80F gate forces Term-A-only for summer until hot data exists; plausibility gate excludes vent-stuck-shut inflation.
- **Center VPD proxy.** No `vpd_center` column; center VPD grading/feasibility is approximate. **Mitigation:** `is_proxy`/`proxy_flag=true`; gate true center VPD feasibility on HW-1/NB1.
- **`v_target_curve` CASCADE.** Dropping `fn_target_band_smooth` without repointing `v_target_curve` first aborts the migration or silently deletes the view. **Mitigation (now in §4.4/§8.2):** repoint `v_target_curve → v_zone_band` in 146.8, drop the view explicitly in 146.10 before the function.
- **`ScorecardResponse` extra='forbid' break.** The public `/api/v1/scorecard` has no try/except; a new metric key 500s the endpoint. **Mitigation (now in §7.1):** schema-first ordering + belt-and-suspenders try/except at `api/main.py:1396`.
- **`fn_compliance_pct` shim column-shift.** **Mitigation:** the shim pins the legacy 5-column shape (incl. NULL `rh_pct`).
- **Gameable VPD-high feasibility.** **Mitigation (now in §6.2):** tightened rule excludes vent-while-temp-OK (7.9% of VPD misses).

---

## 10. Files / lines that ground this design (absolute)

- `/mnt/iris/verdify-worktrees/firmware/docs/design/vanda-zone-control-design.md` §3.2–3.4, §5.1 — the served-line engine, join fix, migration-145 ordering (reused).
- `/Users/jason/Orbit/context_dump/verdify-platform/docs/backlog/verdify-unified-backlog-2026-05-29.md` - T1, B0a/B0b, D1/D2/D3, N1, P1a, M9, M11 (prerequisites/related).
- `/mnt/iris/verdify-worktrees/firmware/scripts/setpoint-server.py:309-311` — the only live band consumers; proof the rewrite is DB-only.
- `/mnt/iris/verdify-worktrees/firmware/ingestor/tasks.py` — binary writer (L4960 `_band_at`, L4983–5019 loop, L5050–5108 relay forward-fill, L5165–5195 UPDATE, L130–132 HOUSE_BAND_PARAMS); 2nd stress check L1271.
- `/mnt/iris/verdify-worktrees/firmware/api/main.py:1409-1437` — `fn_band_trace` crop/fw split (the precedent `fn_zone_band_grade` extends); `:1388-1396`/`:2282`/`:2436` scorecard endpoints.
- `/mnt/iris/verdify-worktrees/firmware/ingestor/iris_planner.py:189,198,217` and `/mnt/iris/verdify-worktrees/firmware/mcp/server.py:373` — prompt copy to rewrite.
- `/mnt/iris/verdify-worktrees/firmware/scripts/gather-plan-context.sh:221`, `/mnt/iris/verdify-worktrees/firmware/scripts/alert-monitor.py:248`, `/mnt/iris/verdify-worktrees/firmware/scripts/backfill-plan-evaluations.py:48-63`.
- DB objects: `fn_plan_anchor_score` (ladder), `v_plan_window_scorecard`, `v_daily_kpi`/`v_planner_performance` (planner_score), `v_setpoint_compliance`/`fn_compliance_pct` (replace), `fn_band_trace` (extend), `v_target_curve` (CASCADE victim), `fn_current_season` (STABLE fix).

**Verified numbers used:** plan_journal 103 anchor / 228 outcome / 2 null / 125 outcome-no-anchor; `fn_current_season` provolatile='i' (IMMUTABLE bug); `v_target_curve` depends on `fn_target_band_smooth`; TimescaleDB 2.25.2; saturated-stack indoor p90 h14 = 83.4F (n=934, NOT 88); k≈0.0035; June h13–15 highs 79–97F (2 days ≥95, 7–8 ≥90); east h14 stress union temp 95F / vpd 2.5; lettuce stress_high 85 = served ceiling; hot-miss 73.4% unachievable; VPD-high 519/6611 (7.9%) gameable-if-untightened; cold-miss 23/23 heaters-maxed.
