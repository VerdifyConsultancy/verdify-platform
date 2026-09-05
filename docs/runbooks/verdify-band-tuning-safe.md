# How to Tune a Band Safely (VPD / Temperature / Orchid Time-of-Day)

> **Obsolete under the 2026-06-19 single-environment model.** This runbook
> references the retired dev DB. Do not follow its `verdify-dev` apply/test
> commands against the current cluster.

**Status:** Runbook (lane VerdifyConsultancy/verdify-platform#221). DB-driven; **no firmware flash**.
**Author:** laptop-root. **Date:** 2026-06-07.

> **Hard gate:** applying a `crop_target_profiles` migration to the **prod** DB
> (`verdify-prod/verdify-db`) changes the live setpoints the dispatcher pushes to the sole
> device writer. **Build + test the migration in the dev DB first; the prod apply is
> laptop-root + execution safeguardd.** No secrets are printed in this runbook.

---

## 1. Where the bands actually live (the key architectural fact)

The agronomic bands — including what is loosely called the **"orchid time-of-day logic"** —
are **DB rows, not firmware code**:

```
crop_target_profiles            fn_band_setpoints(ts)          dispatcher              firmware
(24 hourly rows × crop)   ──▶   (resolve + interpolate)  ──▶  (push setpoints)  ──▶  (consumes Setpoints)
  hour_of_day 0..23              picks MAX(min)/MIN(max)        Tier-1 rows             greenhouse_logic.h
  temp_ideal_min/max             across active crops,           the planner emits        bands only GRADE;
  vpd_ideal_min/max              linear-interpolates to          per the bands           firmware never
  temp/vpd_stress_low/high       the minute                                              hardcodes them
```

- **`crop_target_profiles`** (`db/schema.sql`): one row per `(crop_type, growth_stage,
  hour_of_day, season, greenhouse_id)`. Columns:
  `temp_ideal_min/max`, `temp_stress_low/high`, `vpd_ideal_min/max`, `vpd_stress_low/high`,
  `dli_target_mol`, `source`.
- **`fn_band_setpoints(target_ts)`**: migration 171 supersedes the old hourly
  crop-profile interpolation described here historically. It reconstructs the
  CURRENT house anchors at the requested solar phase, not immutable historical
  crop targets or confirmed device-consumed state.
- **`fn_band_timeline(...)`** is a legacy reconstruction/desired/planned timeline.
  Its actual/firmware labels do not establish delivery, raw freshness or consumed
  branches. Do not tune bands to improve those reported compliance labels; use
  the [current lineage contract](../band-traceability-contract.md) and #424
  qualification before interpreting a mismatch.

### 1.1 The "orchid time-of-day" curve = the orchid rows of this table

Migration `145-vanda-band-and-join-fix.sql` (`vanda_spec_v1.0`) wrote the orchid 24-hour
curve. It is a **diurnal sinusoid**:

| | night/trough (h 0–6, 22–23) | peak (h 14–15) |
|---|---|---|
| `temp_ideal_min/max` (°F) | 61.0 / 67.0 | 77.8 / 87.8 |
| `vpd_ideal_min/max` (kPa) | 0.75 / 0.85 | 0.95 / 1.20 |
| `temp_stress_low/high` | 55 / 100 (flat all day) | 55 / 100 |
| `vpd_stress_low/high` | 0.50 / 1.50 (flat) | 0.50 / 1.50 |

The temp band climbs ~17°F from pre-dawn to mid-afternoon then falls; VPD tracks it
(warmer, drier-allowed by day). **This curve IS the orchid time-of-day behavior.** To
"rip out the orchid time-of-day logic" you do NOT touch the firmware — you re-author these
rows (e.g. to a flatter curve, or fewer distinct anchors, or align the temp & VPD shoulders).

> **Verification that it is NOT in firmware:** `greenhouse_logic.h` reads `in.local_hour`
> only for fog-window / night-suppression / dusk-cutoff *gates* (hard rails), never for the
> crop band itself. The band arrives as resolved `Setpoints` (`sp.temp_high/low`,
> `sp.vpd_high/low`) from the dispatcher. (`grep local_hour firmware/lib/greenhouse_logic.h`
> shows only the gate predicates `fog_hour_in_window`, `is_night_hour`, `past_dusk_cutoff`.)

So issues #52 (orchid dormancy/seasonal) and #37 (irrigation default hour) are **different
surfaces**: #37 *is* a firmware default (`irrig_center_start_hour` in `globals.yaml`), while
the orchid temp/VPD diurnal band is the DB curve above. Don't conflate them.

---

## 2. The safe tuning loop (DB-only, no OTA)

```
  edit band  →  migration in DEV DB  →  band-viz dashboard diff  →  replay-grade  →  GATED prod apply
```

### Step 1 — author the migration
Add `db/migrations/<next>-<slug>.sql` (next number after `150-vanda-nutrient-recipe.sql`,
i.e. **151+**). Pattern from `145`: `DELETE FROM crop_target_profiles WHERE crop_type='orchid'
AND season='spring';` then `INSERT … VALUES` the new 24 rows. Keep it **idempotent**
(DELETE-then-INSERT on the unique key `(crop_type, growth_stage, hour_of_day, season,
greenhouse_id)`) and reversible (commit the prior rows as a down-note).

### Step 2 — apply to the DEV DB only
```bash
# dev DB = verdify-dev/verdify-db (k3s). NEVER point this at verdify-prod.
ssh jason@192.168.30.32 \
  "sudo k3s kubectl -n verdify-dev exec -i statefulset/verdify-db -- \
   psql -U verdify -d verdify" < db/migrations/151-<slug>.sql
```

### Step 3 — visualize the change (the band-adjustment dashboard)
Open the **"Band Tuning — Diurnal Adjustment"** dashboard (`grafana/dashboards/
band-tuning-diurnal.json`, see §3). It plots, hour-by-hour:
- the **temp_ideal_min/max** band envelope across the day,
- the **vpd_ideal_min/max** band envelope across the day,
- the **band width** (temp_high − temp_low, vpd_high − vpd_low) — so you can see where a
  band is too tight to be physically achievable,
- the **resolved `fn_band_setpoints` curve** (what the dispatcher will actually push).

Confirm the new curve looks right *before* any prod apply. Tight bands (small width) are
the #1 cause of unachievable-compliance churn — watch the width row.

### Step 4 — grade the change against history
Run the planner/compliance replay against recent telemetry with the dev band active to see
how compliance moves. (Compliance is graded per-zone against each crop's served band —
`iris_planner.py` §80% Compliance.) A band that *raises* compliance only because it widened
the target is not necessarily a win — check temp AND vpd compliance separately.

### Step 5 — GATED prod apply (root executor)
Same `psql` apply but against `verdify-prod/verdify-db`. **Gate + snapshot first:**
```bash
# snapshot the current orchid rows BEFORE applying (rollback artifact)
ssh jason@192.168.30.32 \
  "sudo k3s kubectl -n verdify-prod exec -i statefulset/verdify-db -- \
   pg_dump -U verdify -d verdify -t crop_target_profiles --data-only" \
   > /tmp/crop_target_profiles.prod.$(date +%Y%m%d%H%M).sql
# then apply, then re-probe fn_band_setpoints(now()) matches intent
```
Record `GREEN at <T>, re-verified at <T+10min>` with the literal
`SELECT * FROM fn_band_setpoints(now());` output.

---

## 3. The VPD + temperature band-alignment refactor

**Goal (the #225/agronomy ask):** cleaner VPD+temperature band alignment — the temp and VPD
"shoulders" (the hours where each starts rising/falling) should be coherent, and the orchid
diurnal swing should be intentional rather than an artifact of the v1.0 anchors.

Concretely, the tooling for Jason to drive this:
1. **`scripts/band-curve-export.sql`** (ship): dumps the current 24-row curve for a crop as a
   tidy table (hour, temp_lo, temp_hi, vpd_lo, vpd_hi, temp_width, vpd_width) — the input to
   a re-author.
2. **The band-viz dashboard** (§3 / `band-tuning-diurnal.json`): see the curve + widths + the
   resolved setpoint, dev vs prod side by side.
3. **A re-author migration template** (DELETE+INSERT 24 rows) — copy `145`'s shape, change
   the anchors. For "rip out the time-of-day swing," set all 24 hours to the same
   `temp_ideal_min/max` + `vpd_ideal_min/max` (a flat band); for "align shoulders," shift the
   VPD-rise hours to match the temp-rise hours.

**Safe-change rules for the band re-author:**
- Never narrow a band into infeasibility — keep `temp_width ≥ ~6°F` and `vpd_width ≥ ~0.3 kPa`
  unless you have evidence the zone can hold tighter (planner notes this exact failure mode).
- Keep `temp_stress_*` / `vpd_stress_*` wider than `*_ideal_*` (the grader gives partial
  credit through the stress band; collapsing them to the ideal makes every near-miss a zero).
- Change temp and VPD **together** and re-grade — VPD is a function of temp + RH, so a temp
  shoulder shift moves the achievable VPD.

---

## 4. Pitfalls

- **`fn_band_setpoints` keys on `season='spring'` (HARDCODED) — but it is now summer.**
  Verified live 2026-06-07: prod `crop_target_profiles` has BOTH `orchid/spring` (24 rows)
  AND `orchid/summer` (24 rows), plus `_default/{spring,summer}`, basil, canna, lettuce,
  pepper. **`fn_band_setpoints` only reads `season='spring'`** (`db/schema.sql:406,412`), so
  the **summer curve is authored but NOT served** until the function selects the live season.
  This is itself a band-alignment defect to fix (a firmware-optimization issue): the resolver
  should pick the active season, not a hardcoded one. A seasonal orchid dormancy curve (#52)
  has the same dependency — it needs both the rows **and** the resolver change.
- **The DEV DB `crop_target_profiles` is EMPTY** (verified live 2026-06-07: 0 rows; the
  fresh-cutover dev DB was not seeded with profiles). So the band-tuning loop's Step 2 (apply
  the migration to dev) is *also* the dev-seed step — panels 1–4 of the band-viz dashboard are
  blank in dev until you load the rows, which is the intended author→load→visualize flow.
  Panel 5 (`fn_band_setpoints`) still resolves in dev via its defaults.
- **Timezone:** the curve is interpreted in `America/Denver`. A row authored "for hour 14"
  is 14:00 MDT/MST, not UTC.
- **Multi-crop intersection:** `fn_band_setpoints` takes `MAX(min)/MIN(max)` across *all*
  active crops for the hour — so the served band is the *intersection*. Re-authoring the
  orchid rows alone may not move the served band if another active crop is the binding
  constraint that hour. Check `v_crop_catalog_with_profiles` for what's active.
