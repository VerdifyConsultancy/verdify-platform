# Verdify Band Redesign — Orchid TOD, VPD/Temp Alignment, Season Resolver

**Status:** PROPOSAL — shadow-verified, **NOT applied to live `crop_target_profiles`.**
**Lane:** laptop-root LANE C firmware-optimization (epic VerdifyConsultancy/verdify-platform#249).
**Covers:** #250 (orchid time-of-day), #251 (VPD+temp alignment), #252 (dashboard), #253 (season resolver).
**Author:** laptop-root. **Date:** 2026-06-07. **No firmware flashed; no live setpoint changed.**

> **Hard gate:** every `crop_target_profiles` change here is GATED (laptop-root + Jason),
> snapshot-first, applied to **dev DB first**. See `docs/runbooks/verdify-band-live-apply-gated.md`.

---

## 0. The single most important finding (read first)

**#253 ("`fn_band_setpoints` hardcodes `season='spring'`") is ALREADY FIXED in live prod.**
The issue was filed against the **stale `schema.sql` on the `main` branch** (which still shows
the old `WHERE ... AND season='spring'` body). The DB that prod actually deploys
(`live/platform-main`, migrations 145/146 "Vanda band/compliance rearchitecture") replaced
`fn_band_setpoints` with a season-aware resolver chain. Verified live, 2026-06-07:

```
fn_band_setpoints(ts)
  → fn_center_band_setpoints(ts)            -- v_season := fn_current_season(); fallback 'spring'
  → fn_achievable_envelope('center', fn_current_season(), ts)
  → fn_active_noncenter_stress(ts)          -- v_season := fn_current_season(); COALESCE fallback
  → fn_house_vpd_control_band(ts)           -- season-independent by design (house control band)
fn_zone_band(zone, ts, gh)                  -- v_season := fn_current_season(); fallback 'spring'
fn_zone_vpd_targets(ts)                     -- v_season := fn_current_season(); fallback 'spring'
```

Live probe (2026-06-07 ~21:00 MT, season=summer):
```
SELECT fn_current_season();                 -- 'summer'
SELECT * FROM fn_band_setpoints(now());     -- resolves via fn_current_season(), NOT 'spring'
```

**Action for #253:** no live DB change is needed. The remaining work is **documentation
hygiene** — regenerate/patch `main`'s `db/schema.sql` so it stops misrepresenting the resolver
as spring-hardcoded (the source of the false alarm). A defensive idempotent **assertion
migration** (`159-*`) is provided that FAILS LOUDLY if any resolver ever regresses to a
hardcoded season — a guard, not a mutation. See §4.

> Because spring and summer `crop_target_profiles` are currently **byte-identical** (summer was
> `+summer_from_spring` copy-derived in migration 145), the resolver fix produces **zero
> setpoint divergence today** regardless. The value of the fix is that it unblocks authoring a
> genuinely different summer curve later (and #52 dormancy).

---

## 1. Where the orchid "time-of-day logic" lives (#250)

Confirmed (matches the #258 band-tuning runbook): the orchid TOD behavior is **DB rows, not
firmware**. `firmware/lib/greenhouse_logic.h` reads `local_hour` only for hard-rail gates
(`fog_hour_in_window`, `is_night_hour`, `past_dusk_cutoff`) — never for the crop band. The
band arrives as resolved `Setpoints` from the dispatcher. **No firmware diff is required to
change the orchid TOD curve.**

The curve is the 24 `orchid/vegetative/<season>` rows of `crop_target_profiles`
(`vanda_spec_v1.0`, migration 145), a diurnal sinusoid:

| | night/trough (h0–6, 22–23) | mid-afternoon peak (h14–15) |
|---|---|---|
| `temp_ideal_min/max` °F | 61.0 / 67.0 | 77.8 / 87.8 |
| `vpd_ideal_min/max` kPa | 0.75 / 0.85 | 0.95 / 1.20 |
| `temp_stress_low/high` | 55 / 100 (flat) | 55 / 100 |
| `vpd_stress_low/high` | 0.50 / 1.50 (flat) | 0.50 / 1.50 |

---

## 2. Shadow analysis — how the CURRENT bands track reality (read-only, live DB)

Method: `climate` (center zone = `temp_avg`/`vpd_avg`, per migration 146 mapping) joined
`LATERAL fn_zone_band('center', ts, 'vallery')` over the trailing 7–14 days. Pure SELECT.

### 2.1 Graded compliance, 14 days (`daily_zone_compliance`)

| zone (crop) | temp_compliance | vpd_compliance | heat-stress h/day | vpd-high-stress h/day | unachievable min/day |
|---|---|---|---|---|---|
| **center (orchid)** | 96.1% | **45.4%** | 0.82 | **8.67** | **523** |
| east (food ∩/∪) | 79.8% | 76.0% | 4.36 | 5.18 | 656 |
| north (_default) | 88.7% | 71.0% | 2.43 | 6.18 | 403 |

**Orchid temperature tracks well (96%). Orchid VPD does not (45%).** The VPD problem dominates.

### 2.2 Orchid VPD by hour vs its own ideal band (7 days)

| local hour | actual VPD | band vpd_lo–vpd_hi | in-ideal % | in-stress % (0.5–1.5) |
|---|---|---|---|---|
| 0–6 (night) | 0.67–1.13 | 0.75–0.85 | 0–26% | 57–96% |
| 7–9 (morning) | 0.94–1.18 | 0.75–0.93 | 13–24% | 100% |
| 10–17 (day) | 1.17–1.38 | 0.82–1.20 | 3–39% | 76–98% |
| **18–21 (evening)** | **1.62–1.96** | 0.76–1.06 | **0–3%** | **3–43%** |
| 22–23 | 1.23–1.39 | 0.75–0.85 | 0–43% | 61–74% |

### 2.3 VPD distribution by diurnal phase (14 days, p10/p50/p90/max)

| phase | p10 | p50 | p90 | max |
|---|---|---|---|---|
| night | 0.42 | 0.69 | 1.31 | 2.04 |
| morning | 0.61 | 0.87 | 1.30 | 1.50 |
| day (10–17) | 0.90 | 1.22 | 1.47 | 1.96 |
| **evening (18–21)** | 0.76 | **1.46** | **2.21** | **2.89** |

**Diagnosis.** Two distinct failure modes:

1. **Band-too-narrow/too-low (all day):** the orchid `vpd_ideal` band (≈0.75–1.20) sits below
   the house's natural VPD (p50 1.22 by day). The orchid is graded "non-compliant-dry" for
   conditions a Vanda (high-airflow epiphyte) tolerates fine. This is an **authoring** problem
   — widen/raise the ideal band.
2. **Evening unachievable (h18–21):** house VPD p50 1.46, p90 2.21 — post-solar drying with
   vents still venting and night humidification not yet engaged. **No band can fix this; no
   humidifier pulls 2.2 kPa to 1.0 in minutes.** This is a **feasibility/equipment** problem.
   The band's job here is to *stop penalizing an unachievable target* (raise the evening
   ceiling), and the durable fix is an evening humidification/seal change (out of band scope →
   firmware lane, gated, NOT in this proposal).

---

## 3. Proposed band redesign (PROPOSAL — migration `160-*`, NOT applied)

Design principles (from #251 safe-change rules + the shadow above):

- **Keep the temp curve** — it tracks at 96%. Do NOT flatten temp (rejects the naive "rip out
  all TOD" reading of #250: the *temp* diurnal swing is correct and agronomically right for Vanda).
- **Re-author the VPD curve** to a wider, higher, shoulder-aligned band:
  - Raise `vpd_ideal_max` so it tracks the house p75 instead of sitting below p50.
  - Keep `vpd_ideal_min` near 0.65–0.80 (Vanda do not want sub-0.5 sustained — fungal risk).
  - **Align the VPD evening shoulder to the temp shoulder** (both descend together after the
    h14–15 peak) and **hold the evening ceiling high (≈1.55) through h22** so h18–21 is not
    graded against an unachievable floor.
  - Enforce feasible widths: `vpd_width ≥ 0.30 kPa`, `temp_width ≥ 6 °F` (kept; temp already 6).

### 3.1 Shadow result of the candidate VPD curve (14 days, in-ideal %)

| candidate | in-ideal VPD % | Δ vs current |
|---|---|---|
| current ideal (0.75→1.20) | **~53%** | — |
| candA flat 0.70–1.30 | 51.0% | −2 (rejected: flat loses the day-night shape) |
| **candB shoulder-aligned, evening-ceiling 1.55** | **58.6%** | **+5.6** |

candB is the recommended direction. The residual gap to 100% is the **evening
unachievable** tail (p90 2.21) — explicitly an equipment lever, not a band lever. The honest
ceiling of band-only tuning here is ~60–65% in-ideal; chasing higher by widening to absurd
(0.4–2.0) would make the band meaningless and is rejected per the safe-change rules.

### 3.2 Proposed migration

`db/migrations/160-orchid-vpd-band-realign-PROPOSAL.sql` (in this PR, **commented header =
PROPOSAL, dev-apply only**) re-authors the 24 orchid VPD rows (both seasons) to candB,
leaving `temp_ideal_*`, `temp_stress_*`, `vpd_stress_*`, `dli_target_mol` untouched.
DELETE-then-UPDATE on the unique key (idempotent + reversible). It is NOT in any kustomize
overlay and is NOT auto-applied.

---

## 4. #253 season-resolver guard (migration `159-*`, idempotent ASSERTION)

`db/migrations/159-band-season-resolver-guard.sql` adds a `DO $$` assertion block that raises
if `fn_band_setpoints`, `fn_zone_band`, `fn_center_band_setpoints`, or `fn_zone_vpd_targets`
ever loses its `fn_current_season()` reference (i.e. regresses to a hardcoded season). It
mutates nothing. This converts the latent #253 risk into a CI/migration tripwire.

---

## 5. Dashboard (#252)

`grafana/provisioning/dashboards/json/band-tuning-diurnal.json` (from PR #258) deployed
additively as `verdify-grafana-dashboards-2` ConfigMap (bucket2) on `live/platform-main` —
no edit to the generated cm-0/cm-1, no collision. All 6 panels' SQL verified against live prod.
See `docs/runbooks/verdify-band-live-apply-gated.md` §Dashboard.

---

## 6. What is explicitly NOT done (guardrails honored)

- **No live `crop_target_profiles` write.** The redesign is a proposed dev-only migration.
- **No firmware flash / OTA.** The orchid TOD is DB-driven; firmware untouched.
- **No CNPG cutover, no Pi-hole mutation, no ingestor/device touch.**
- The evening-VPD equipment fix is named but left to the gated firmware lane.
