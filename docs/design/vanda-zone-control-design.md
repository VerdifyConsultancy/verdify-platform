# Vanda Zone Control Design

**Author role:** firmware / climate-control engineer (cross-cutting design, routes to coordinator for shared territory).
**Date:** 2026-05-29 (America/Denver, MDT). **Status:** implementation-ready design. **Greenhouse:** Vallery, 367 sq ft, Longmont CO (5090 ft, ~15% RH, 95F+ solar peaks).

This design is the single authoritative target for making Verdify serve the bare-root Vanda zone correctly. Every capability claim below has been verified against the live DB and source as of 2026-05-29; verification corrections from the recon (refuted/partial claims) are folded in. The companion backlog derived from this design is archived at `/Users/jason/Orbit/context_dump/verdify-platform/docs/backlog/verdify-unified-backlog-2026-05-29.md`.

---

## 1. Objective + governing constraint + priority

**Objective.** Produce ONE unified, physics-honest, Vanda-prioritized control plan for the greenhouse: a smooth diurnal temperature/VPD/light curve, a bare-root-Vanda irrigation+fertigation regimen, and the topology/recipe/sensor changes that make both real — without breaking the working firmware (16/16 invariants, stable FSM, dispatcher 95% confirm).

**Governing constraint (the spec's central physical truth).** In the Vanda zone, *humidity control and irrigation are the same actuator*. The overhead misters/foggers raise humidity AND wet the bare roots below. Therefore **every humidity action is also an irrigation event**. Two delivery rules follow from plumbing reality:
- **Pressurized misters** can carry fertilizer AND truly wet the velamen.
- **The AquaFog XE 2000 fogger (`fog_rly`) is clean-water-only** by plumbing — there is no `fog_fert` relay (verified: grep finds no `fog_fert`). It is humidity-only.

**Priority crop.** **Vanda Orchids** — zone 1, center, hanging bare-root under the center misters + fogger (`crops` id 5, `crop_catalog_id=9` → `orchid` profile, `is_active=t`). All trade-offs resolve in favor of the Vanda center zone. Operator corrections applied throughout: center-mister carrying ~73% of mister runtime is CORRECT (it serves the priority zone) — the real center gap is no dedicated center VPD sensor + no center fertigation path; south/west soil probes are UNPOTTED (Canna moved to patio), not broken.

**The problem we are fixing.** The dispatched midday temperature ceiling is an unachievable **78F** (`MIN(temp_ideal_max)` across active crops = strawberry). The box physically cannot hold 78F midday — no shade hardware, exhaust-only cooling into 15% RH / 95F+ outdoor air — so the controller pins cooling ON for hours and never satisfies it, producing a permanent band-exceedance that shows up as *falling compliance* (75.6 → 57.5% wk/wk). The 7-day observed indoor temp already lives at median 82-83F / max 90.8F every afternoon. We make the served band *contain reality* and *favor the Vanda*, which both helps the priority crop (its spec wants 80-95F midday) and makes compliance measurable.

---

## 2. Spec → Capability Gap Matrix

Status legend: **SUPPORTED** = exists today, cited; **PARTIAL** = exists but insufficient (what's missing stated); **NEW_BUILD** = must be built. Owners: firmware / ingestor / genai / web / saas / coordinator / operator.

### ENV — Environmental setpoints

| Req | Requirement | Status | Mechanism / What to build | Owner |
|---|---|---|---|---|
| ENV-PHASE | Vanda-prioritized diurnal temp+VPD+light phase table | **PARTIAL** | Dispatcher serves `fn_band_setpoints(now())` (house MAX(min)/MIN(max), no `is_active`, season hardcoded `spring`). Center VPD already Vanda-correct via `fn_zone_vpd_targets`. Build: rewrite temp band to Vanda/center anchor + smooth curve (§3). | coordinator |
| ENV-1 | Hold VPD at phase target ±0.15 kPa in wet window | **PARTIAL** | Firmware controls a VPD *band* (`climate_band_error`, greenhouse_logic.h:360,877,894), not a tight target. `vpd_target_center` is the crop *ceiling*, one-sided. Build: phase target + ±0.15 tolerance during wet window; needs center sensor (HW-1). | firmware |
| ENV-2 | Preserve ≥10F day/night drop; no heat to chase night humidity | **PARTIAL / at-risk** | Drop is emergent profile data only — NO runtime guard, NO invariant. **The econ VPD-rescue heat path (greenhouse_logic.h:1770-1772) fires `heat1` when `vpd_kpa < vpd_low_eff` with NO time-of-day gate** (verified) — exactly heat-to-chase-humidity. Raising night VPD floor (§3/§5) makes it fire MORE. Build: night-drop invariant + suppress econ-rescue heat overnight (firmware OTA). | firmware + coordinator |
| ENV-3 | Allow 100-105F ONLY with shade+humidity+airflow, else ≤95F | **NEW_BUILD** | No conditional ceiling; no shade hardware to gate on. Cools at raw `temp_high`. Build conditional two-tier ceiling — blocked on shade HW (ENV-4) + HAF (ENV-6). | firmware (blocked on operator HW) |
| ENV-4 | Hold 4000-6000 fc; 25-35% shade engages above band | **NEW_BUILD** | No shade actuator anywhere (only a grow-light lux "shade-aware" toggle, tunables.yaml:1258). Lighting is ADD-only. Install motorized shade + relay + control loop + lux→fc calibration. | operator (HW) + firmware |
| ENV-5 | Preserve ≥6h dark/day | **PARTIAL** | Lighting cutoff (`fn_lighting_policy` + firmware window) gives de-facto ~8-11h dark. BUT occupancy task-light branch (greenhouse_logic.h ~770-782) omits `in_window` and can turn grow lights on during dark (empirical: grow lights on 23:49 local). Window set by highest-DLI crop (pepper 22), not Vanda. Build: min-dark invariant + gate occupancy with `in_window`. | firmware + genai |
| ENV-6 | Continuous gentle circulation whenever wetting | **NEW_BUILD** | Only air movers are exhaust fans coupled to vent ("no fan without vent"); sealed wetting modes run fans OFF. Physically impossible today. Install dedicated low-speed HAF circulation fan + relay exempt from vent interlock. | operator (HW) + firmware |

### IRR — Irrigation / wetting

| Req | Requirement | Status | Mechanism / What to build | Owner |
|---|---|---|---|---|
| IRR-1 | Drive misters to hold VPD at phase target in wet window | **PARTIAL** | SEALED_MIST stage machine (greenhouse_logic.h:907-1073) + per-zone targets exist. **Time-varying `vpd_target_center` IS already supported** via `fn_zone_vpd_targets` (maps Vanda→orchid, respects `is_active`). Gap = no center VPD *sensor* (control uses `vpd_avg × mister_center_penalty 0.5` proxy). Build: center VPD sensor (HW-1) + rewire center stress. | firmware (sensor: operator) |
| IRR-2 | Mister hysteresis ≥0.1 kPa | **PARTIAL** | `MISTER_HYST` floor is **0.05** (controls.yaml:968); current live 0.1675 satisfies ≥0.1 only by planner choice. Editing greenhouse_logic.h:191 would NOT affect misters. Build: raise the controls.yaml:968 fmaxf 0.05f floor to 0.10 or add `mister_hysteresis_kpa` tunable + `cfg_*` readback. | firmware |
| IRR-3 | Short rehydrate drench at wet-window start (dawn) | **NEW_BUILD** | No dawn-specific drench. Center misting just begins reactively ~120 min after activity start. Build: time-windowed dawn rehydrate burst (longer ON/shorter GAP) anchored to sunrise; new `cfg_*` tunables. | firmware + ingestor |
| IRR-4 | ≥1 heavier drench at midday peak | **NEW_BUILD** | Only demand-reactive `irrig_vpd_boost`/`mister_vpd_weight`; no time-anchored midday drench. Build: midday heavy-drench window anchored to solar peak. | firmware + ingestor |
| IRR-5 | Roots cycle green↔silvering; never continuously saturated | **PARTIAL** | Pulse/gap (60s/45s) + `sealed_max_ms` backoff give intermittency, but **`mister_max_runtime_min` (120) is dead code (0 refs in controls.yaml, verified)** and the 300-gal budget is bypassable by `climate_vpd_emergency`. Build: per-zone (center) cumulative-runtime/wet-fraction cap, non-bypassable, + `cfg_*` readback. | firmware |
| IRR-6 | All non-feed wetting uses clean RO (= salt flush) | **PARTIAL** | Center mister is provably fertilizer-FREE (no `center_mister_fertilized`; `turn_on_zone` uses clean relays). But "RO" is NOT established in firmware (no RO reference) and there is no salt-leaching flush (the only flush is a post-fert line rinse). Confirm RO source (operator); flush is FRT/SAF work. | operator + firmware |
| IRR-7 | Tepid (>50F), low-TDS (<175 ppm) misting feed line | **NEW_BUILD** | No feed-line water-temp or TDS sensing (`climate.ec_input` 0 rows; `hydro_*` is reservoir). Install inline water-temp + TDS probe + SAF guard. | operator (HW) + firmware |

### FRT — Fertigation / feed

| Req | Requirement | Status | Mechanism / What to build | Owner |
|---|---|---|---|---|
| FRT-1 | Nitrate-dominant low-P Ca+Mg RO salt (MSU 13-3-15 / Jacks 15-5-15) | **NEW_BUILD** | `nutrient_recipes` has 7 GH-Flora 2-part veg rows; no orchid/Vanda recipe; `crop_id` NULL on all. Add `vanda_orchid_active` (+ provisional dormant) single-salt rows (§5). | coordinator (data) |
| FRT-2 | NO organics/particulate/colloidal through nozzles | **NEW_BUILD (process)** | Schema convention is passive (unenforced). Needs operator standing rule (mineral salts only) + physical inline filtration. Not a code control today. | operator |
| FRT-3 | Feed once/day MORNING ~40-60 ppm N (~0.3-0.5 mS/cm over RO); reduce dormant | **PARTIAL** | Daily AM cadence configurable (timing only). Live start hour 10:30 (too late vs 06-09). Concentration/ppm/EC NOT controlled (binary fert valve); no active/dormant gating. Build: move feed to ~06:30, recipe-driven dose, active/dormant set. | firmware + ingestor |
| FRT-4 | Close loop on EC; else calibrated timed VOLUME | **PARTIAL** | Timed valve-open fallback is the ONLY control (controls.yaml:2074-2087); NO EC sensing (`ec_input` 0 rows, never written), NO closed-loop controller anywhere. Build: calibrate timed-volume now; inline EC + EC controller later. | firmware (later: operator HW) |
| FRT-5 | Fertilizer through MISTERS ONLY; fogger clean always | **PARTIAL / refuted-as-written** | "Misters only" is FALSE today: fertilizer flows through fertilized MISTERS (south/west wall) AND fertilized DRIPS (wall, center). **Center Vanda has NO fert mister** — fed only via `center_drips_fertilized`. Fogger clean-only is satisfied by plumbing (no `fog_fert`) but no positive SAF-5 interlock asserts it. Build: center fert mister (§6) + decide drip-vs-mister Vanda feed + SAF-5 invariant. | operator + firmware + coordinator |
| FRT-6 | After feed, 60-90 min ABSORPTION HOLD, no clean wetting | **NEW_BUILD** | CONTRADICTED today: post-fert clean flush fires *immediately* (controls.yaml:2154-2171). No `feed_hold_until`/absorption concept anywhere (0 grep). Build: shared `feed_hold_until` global gating BOTH irrigation AND climate/fog machines. | firmware |
| FRT-7 | After hold, normal clean wetting resumes (rinses salts) | **PARTIAL** | Clean-rinse capability exists (flush states + VPD clean misting). Gap is ordering: relocate flush to AFTER the FRT-6 hold. Coupled to FRT-6. | firmware |
| FRT-8 | NO feed in afternoon/dusk/overnight | **PARTIAL** | Only single morning auto-start discourages PM feed; manual fert buttons queue at any hour with no time check. The activity-window gate is inert in prod. Build: dedicated `feed_window` (e.g. 06-09) rejecting fert states 2/4/7/8 + manual buttons regardless of VPD; `cfg_*` readback. | firmware + ingestor |

### CYC — Diurnal cycle & overnight dry-down

| Req | Requirement | Status | Mechanism / What to build | Owner |
|---|---|---|---|---|
| CYC-1 | Wetting ceases at dusk cutoff (sunset−2h, configurable) | **PARTIAL / refuted-as-sunset** | Cutoff is a STATIC clock hour (`fog_time_window_end`=17), NOT sunset-relative, and never dispatcher-pushed. **Fog stress extension (`fog_stress_window_latest_hour`, live=22) ORs in and fires past dark** when VPD high. Build: dispatcher push sunset−2h to `fog_time_window_end`; cap stress window at cutoff. | ingestor (push) + firmware (gate) |
| CYC-2 | Overnight surfaces dry (no crown condensation) | **PARTIAL / refuted** | Independent direct-wet path + overnight fog fire deep overnight on some nights (climate_action_log shows fog_allowed=false at those times → non-climate path). Build: bound irrigation/fog schedule away from dusk-dawn AND add `alert_monitor` "dry-by" check (last-wet ts vs cutoff). | firmware + ingestor |
| CYC-3 | Overnight VPD ≤ ceiling (1.25) WITHOUT wetting plants | **NEW_BUILD** | All humidity actuators are overhead; DEHUM_VENT only lowers humidity. Cannot raise humidity without wetting. Install non-overhead night humidity source (OPN-2, highest-value upgrade). | operator (HW) + firmware |
| CYC-4 | If overnight VPD > ceiling, MAY micro-pulse ≤5s last resort | **NEW_BUILD** | `min_fog_on_s`=60 (12× too long); fog gated off after window. Stress extension is dry-stress driven, opposite condition. Build: dedicated ≤5s overnight micro-pulse path bypassing duty table, clean/dew-gated, auto-disabled when CYC-3 HW present. | firmware |

### SEA — Seasonal adaptation

| Req | Requirement | Status | Mechanism / What to build | Owner |
|---|---|---|---|---|
| SEA-1 | Distinct active vs dormant param sets | **NEW_BUILD** | Only `spring` rows exist (verified: 0 summer/dormant). Live path HARDCODES `season='spring'` (never switches). Author active + dormant rows; make functions season-aware. | coordinator |
| SEA-2 | Sunrise/sunset-tracked windows expand/contract | **PARTIAL** | LIGHTING is sun-tracked (`fn_lighting_policy` + `gl_*_sunset_hour` pushed). Wet-window END/dusk-cutoff and the smooth-curve solar peak are NOT sun-tracked (fixed clock hours / hardcoded 14.5h). Build: sunset-anchor wet cutoff + tie curve peak to `fn_solar_altitude`. | coordinator + firmware |
| SEA-3 | Dormant: observed dry-down + 11-13h photoperiod | **NEW_BUILD** | No dormancy state; "drydown" tokens are within-day timing gates. No 11-13h dormant clamp. Build: dormancy phase + dormant lighting clamp + dry-down governance (Vanda has no soil probe → velamen/VPD-recovery proxy). | coordinator + firmware + ingestor |

### HW — Hardware & sensing

| Req | Requirement | Status | Mechanism / What to build | Owner |
|---|---|---|---|---|
| HW-1 | Temp+RH (VPD) sensing in controlled zone | **PARTIAL** | 4 wall probes + case + exterior populated. **NO center probe** → Vanda VPD = `vpd_avg` proxy. Add 6th Modbus RH/temp probe (addr 10) in center canopy + `vpd_center` template + column + entity_map. | operator (HW) + firmware + coordinator |
| HW-2 | Inline EC on feed line (enables FRT-4) | **NEW_BUILD** | `ec_input` 0 rows ever; no inline probe. Install inline EC (+pH) downstream of injector. | operator (HW) |
| HW-3 | Light (PAR/lux) calibrated to fc | **PARTIAL** | Lux ADC exists (sensors.yaml:85-108), offset-corrected, lux-only; DLI lux-derived; `ppfd`/`dli_par_today` empty. Add fc conversion + `greenhouse_light_fc` readback + reference-meter calibration; optional PAR sensor. | firmware (+ operator for PAR) |
| HW-4 | Misters, fert injector/dosing pump, shade; fogger clean | **PARTIAL** | Misters + clean fogger exist. NO dosing pump (pre-mixed tank + valves). NO center fert mister. NO shade. `EquipmentKind` enum already has `pump`/`fertigation` (no schema change for a pump; shade needs new kind). Add dosing pump or center fert valve; install shade. | operator + firmware + coordinator |
| HW-5 | RO/rainwater, tepid, low-TDS | **NEW_BUILD** | No source TDS/feed-water-temp sensing; flow-only meter. Confirm/route RO; install source TDS + feed-water-temp probe. | operator (HW) |

### SAF — Safety & fail-safe

| Req | Requirement | Status | Mechanism / What to build | Owner |
|---|---|---|---|---|
| SAF-1 | Sensor-loss → conservative TIMED fallback (not open-loop) | **PARTIAL** | controls.yaml:159-245 fabricates temp/RH/VPD BEFORE `sensors_plausible()`, so single-probe loss keeps full VPD-driven wetting on a guess; SENSOR_FAULT (inf/garbage) goes all-off (no wetting). Build: `sensor_degraded` state (trigger `avg_ok==false`, not just all-probes-NaN) → conservative timed wetting. | firmware |
| SAF-2 | EC-loss → skip closed-loop dose, never blind | **NEW_BUILD** | Moot until FRT-4 built (nothing doses on EC today). Build with HW-2/FRT-4: on EC NaN → timed-volume or skip feed, never inject. | firmware (later) |
| SAF-3 | Dusk cutoff enforced REGARDLESS of VPD | **PARTIAL / refuted** | VPD-independent only for crop-irrigation path. Fog AND climate-mister stress extensions are VPD-DEPENDENT and uncapped vs sunset (live latest_hour=22). Build: one authoritative VPD-independent sunset-relative cutoff gating fog + all mister/drip before any stress logic. | firmware + ingestor |
| SAF-4 | Cap mister duty to prevent continuous saturation | **PARTIAL** | Pulse/gap + 300-gal budget exist, but budget bypassable on `climate_vpd_emergency`; `mister_max_runtime_min` dead. Build: non-bypassable per-zone duty cap + absolute daily volume hard-ceiling above the VPD-emergency budget. | firmware |
| SAF-5 | Salts never reach fogger; flush feed lines periodically | **SUPPORTED (+ harden)** | Fogger clean by plumbing; post-fert clean flush already runs with master closed (controls.yaml:2156-2171). Add explicit invariant: `fog_rly` never co-fires with `fertilizer_master_valve`. | firmware (test) |
| SAF-6 | Prefer shade+airflow before exceeding ceiling | **PARTIAL** | Airflow half exists (SAFETY_COOL vent+fans+fog). Shade half impossible (no shade HW). Hold ≤95F hard until shade installed. | operator (HW) + firmware |

---

## 3. The unified smooth diurnal curve (centerpiece)

### 3.1 Live control path (verified, not assumed)

The controller is served by `setpoint_changes`/`setpoint_plan` rows the dispatcher writes every ~5 min. The dispatcher reads three DB functions:
- `fn_band_setpoints(now())` → `temp_low`, `temp_high`, `vpd_low`, `vpd_high` (hourly linear interp of `crop_target_profiles`, **house MAX(temp_min)/MIN(temp_max), no `is_active` join, `season='spring'` hardcoded**).
- `fn_house_vpd_control_band(now())` → house `vpd_low`/`vpd_high`.
- `fn_zone_vpd_targets(now())` → `vpd_target_{south,west,east,center}` (**already maps Vanda→orchid via CASE, respects `is_active`** — center is Vanda-correct; also hardcodes `season='spring'`).

`fn_target_band` (step) and `fn_target_band_smooth` (cosine) are **NOT in any live path** (only dashboards/context). The smooth cosine engine exists but is orphaned.

**Verified live band:** noon `72|78|0.8|1.2`, night 02:00 `62|65|0.3|0.6`. The 78F ceiling = strawberry `MIN(temp_ideal_max)`. The night VPD low of 0.3 comes from the too-humid existing orchid rows (night `0.2-0.6`).

### 3.2 Chosen approach

**ONE house temperature curve, anchored on the Vanda center profile, with a per-zone center band, plus VPD already-correct per zone.** Rationale:
- VPD is already per-zone and Vanda-correct (`fn_zone_vpd_targets`) — keep it.
- Temperature is physically house-wide (one air volume, one set of fans, one vent — no partition, no zonal cooling). A *per-zone temperature curve is un-actuatable*, so the served temp band must be a single house curve.
- Anchor that curve on Vanda (priority + most heat-tolerant active crop, orchid max 82 vs strawberry 78). This is what *lets* the midday ceiling rise to the achievable range.
- Protect strawberry by deep cooler nights (its real need is cool nights for fruit quality) — the redesign gives strawberry *cooler* nights than today (60-67 vs current 62-65) and *daytime no worse than the 82-86F it already experiences* under the broken 78F target. Canna is leaving (operator correction) → non-constraint.

**Critical reconciliation (resolves the design contradiction).** Two candidate designs disagreed on the midday ceiling (86-88F house-anchored vs 95F orchid-profile). The orchid profile's 95F only reaches the controller IF the temperature band is sourced from the center/orchid profile. A profile-only edit that keeps house-wide `MIN(temp_ideal_max)` leaves strawberry's 78F binding → orchid 95F is dead-on-arrival. **Therefore the implementation MUST serve the center zone's own temperature band (a `fn_center_band_setpoints`), not just edit the orchid profile.** We adopt the smooth-curve engine anchored on the orchid profile, with a midday house ceiling of **86-88F** (achievable, contains observed reality) — NOT the spec's 95F, which the box cannot reach without shade. The orchid *profile* may carry 95F as its ideal, but the *served* curve caps at the physically-achievable 86-88F until shade hardware exists; the gap between 88F served and 95F ideal is logged as "tolerated, not ideal" for the planner.

### 3.3 Hour-by-hour anchor table (served house curve, Vanda-anchored)

Endpoints: night band **61-67F**, day band **78-88F**; night VPD **0.75-0.85** (corrected from the current too-humid 0.2 so velamen silvers overnight, ACC-1), day VPD **0.95-1.20**. Peak placed at **14.5h local** (2h thermal lag: solar noon ~13:00, indoor temp peaks 14-15h — verified). Day peak 88F is ≥21F above the 67F night ceiling (far exceeds the ≥10F ENV-2 invariant). Dusk wet cutoff ~18:18 (sunset−2h; sunset ~20:18 today).

| local h | temp_min | temp_max | vpd_min | vpd_max | light / shade state | phase |
|--:|--:|--:|--:|--:|:--|:--|
| 0 | 61.0 | 67.0 | 0.75 | 0.85 | dark; grow-lights OFF | night |
| 1-5 | 61.0 | 67.0 | 0.75 | 0.85 | dark | night |
| 6 | 61.0 | 67.0 | 0.75 | 0.85 | sunrise ~05:42; nat. light, no shade | dawn/feed |
| 7 | 61.2 | 67.2 | 0.75 | 0.85 | feed window; supp-light if lux<thr | dawn/feed |
| 8 | 62.4 | 68.8 | 0.76 | 0.88 | feed window | dawn/feed |
| 9 | 64.8 | 71.7 | 0.79 | 0.93 | light rising | midday-ramp |
| 10 | 67.8 | 75.5 | 0.82 | 0.99 | hold 4000-6000 fc; shade if fc>band | midday |
| 11 | 71.2 | 79.5 | 0.86 | 1.06 | shade if hot | midday |
| 12 | 74.2 | 83.3 | 0.89 | 1.12 | shade if hot | midday |
| 13 | 76.6 | 86.2 | 0.92 | 1.17 | shade if hot | midday-peak |
| 14 | 77.8 | 87.8 | 0.95 | 1.20 | thermal peak; shade+airflow max | midday-peak |
| 15 | 77.8 | 87.8 | 0.95 | 1.20 | thermal peak | midday-peak |
| 16 | 76.6 | 86.2 | 0.92 | 1.17 | easing | taper |
| 17 | 74.2 | 83.3 | 0.89 | 1.12 | easing | taper |
| 18 | 71.2 | 79.5 | 0.86 | 1.06 | **wet cutoff ~18:18 (sunset−2h)** | taper |
| 19 | 67.8 | 75.5 | 0.82 | 0.99 | drying down | taper |
| 20 | 64.8 | 71.7 | 0.79 | 0.93 | sunset ~20:18 | taper→night |
| 21 | 62.4 | 68.8 | 0.76 | 0.88 | dark approaching | night |
| 22 | 61.2 | 67.2 | 0.75 | 0.85 | dark | night |
| 23 | 61.0 | 67.0 | 0.75 | 0.85 | dark | night |

This band *contains* observed reality (h13 band 76.6-86.2 vs observed median 82.3, max 85.6) rather than sitting below it. Note the night VPD floor is set to **0.75** (spec minimum), not 0.70, to match ENV exactly.

### 3.4 Interpolation / implementation form

Build the smooth-curve engine into `fn_band_setpoints` (drop-in: keep the exact name/signature the dispatcher already calls). Cosine-squared with thermal lag:

```
sun_factor(h) = cos²( (h - peak) · π / (2·W) )   for |h-peak| < W,  else 0
   peak = solar_noon_local + 2.0       -- thermal lag; today peak ≈ 14.5
   W    = (sunset_local - sunrise_local)/2 + 1.0   -- half-day + 1h tail
target_temp_min = night_tmin + (day_tmin - night_tmin)·sun_factor   -- 61 → 78
target_temp_max = night_tmax + (day_tmax - night_tmax)·sun_factor   -- 67 → 88 (served cap)
target_vpd_min  = night_vmin + (day_vmin - night_vmin)·sun_factor   -- 0.75 → 0.95
target_vpd_max  = night_vmax + (day_vmax - night_vmax)·sun_factor   -- 0.85 → 1.20
```

`sun_factor` is C¹-continuous (no slope breaks unlike the current hourly-linear interp). `peak`, `sunrise`, `sunset`, `W` derive from `fn_solar_altitude()` (zero-crossings + argmax), so the curve **expands/contracts seasonally** (SEA-2). Endpoints are read from the **active Vanda/orchid `crop_target_profiles` rows** (night = mean hours 0-5, day = mean hours 13-15) so re-authoring the profile re-shapes the curve with no code change.

**Join + season fix (must accompany the rewrite):**
- JOIN `crops c ON c.crop_catalog_id = p.crop_catalog_id AND c.is_active AND c.greenhouse_id = p.greenhouse_id` (fixes the no-`is_active` leak; Vanda catalog 9 ↔ orchid profile catalog 9, verified).
- Backfill `crop_target_profiles.crop_catalog_id` for `pepper`/`strawberry` (NULL today; catalog slugs plural `peppers`/`strawberries` vs singular profile crop_type).
- Replace `season='spring'` with `fn_current_season()` + a documented nearest-season fallback so June (3 days away) never returns NULL. **Author summer rows as a prerequisite** (only spring exists; verified).

**Per-zone center temperature band.** Add `fn_center_band_setpoints(ts)` selecting the orchid (catalog 9) rows directly via the smooth engine; dispatcher serves the center temp band from it. This is the load-bearing piece — without it the Vanda midday ceiling never reaches the controller.

### 3.5 Why this is DB-only and how it fixes compliance

The curve ships as a coordinator **DB migration (functions + data) + dispatcher/MCP restart**. The firmware already enforces whatever band the dispatcher pushes, so **no OTA, no firmware-replay-diff block, no 48h bake for the curve itself**. The behavioral change (cooling onset at ~86-88F instead of 78F) is governed by setpoint/planner review, with `make firmware-replay` evidence attached + coordinator THRESHOLD_PCT sign-off (CLAUDE.md rule 8). Effect: cooling-relay duty *drops* (fewer futile calls into 95F air), nights become *deeper and drier*, and compliance is measured against a band the box can occupy.

**ENV-2 companion (firmware OTA, separate, sequenced AFTER the DB curve):** raising the night VPD floor from 0.2→0.75 makes the econ VPD-rescue heat path (greenhouse_logic.h:1770-1772) fire MORE overnight (it triggers when `vpd_kpa < vpd_low_eff`). This must be guarded: suppress the econ-rescue heat path during night hours, plus add a night-drop invariant (`temp_low` at night ≥10F below day-peak `temp_high`). Ship the DB curve first, monitor overnight heating, then ship the firmware guard with the full artifact set.

---

## 4. Irrigation + fertigation schedule

### 4.1 Hardware reality this is built on (verified)

| Element | State |
|---|---|
| `center_mister` (clean) | exists, 5 heads/25 nozzles, clean only (hardware.yaml:488) |
| **center fertilized MISTER** | **DOES NOT EXIST** (no `center_mister_fertilized`) |
| `center_drips_fertilized` | exists — only working Vanda feed path today |
| `fog_rly` (AquaFog XE 2000) | clean only, no `fog_fert` |
| `fertilizer_master_valve` gating 4 `_fertilized` relays | exists (south/west wall mister, wall drip, center drip) |
| inline feed-line EC | none (`climate.ec_input` 0 rows ever) |
| center VPD probe | none (proxy = `vpd_avg × mister_center_penalty 0.5`) |
| `mister_max_runtime_min`=120 | dead code (0 refs in controls.yaml, verified) |
| post-fert flush | fires IMMEDIATELY (no absorption hold) |

### 4.2 Daily wet-window timeline (active season, sunset-anchored)

```
Local   Phase            VPD target   Temp band    Wetting action
──────────────────────────────────────────────────────────────────────────────
00-05  OVERNIGHT/DRY     hold ≤1.25   61-67F       DRY. No wetting. Micro-pulse ≤5s last-resort only.
06:00  DAWN REHYDRATE    →0.85        61-67F       IRR-3: rehydrate drench burst (~6-8 min wet, 90s ON/20s GAP)
06:30  MORNING FEED       n/a         61-68F       FRT-3: fertigate (center_drips_fert interim; center fert mister later)
       └─ ABSORPTION HOLD 06:36→08:00 (90 min) — ALL clean wetting blocked (FRT-6)
08:00  VPD-HOLD am       0.76-0.88   62-69F       IRR-1: VPD-hold misting, hysteresis ≥0.10
12:30  MIDDAY DRENCH     1.0-1.2 cap 83-88F        IRR-4: heavier drench (~12 min, deeper soak at solar peak)
13-16  VPD-HOLD peak     1.0-1.2 cap ≤88F (≤95 ideal) IRR-1 with non-bypassable duty cap (IRR-5/SAF-4)
16-18  TAPER             ~0.95       79-86F        IRR-1 easing
18:18  DUSK CUTOFF       —           —             CYC-1: ALL wetting CEASES (hard, VPD-independent rail)
       └─ velamen silvers/dries ~1-2h (ACC-1)
20:18  SUNSET
22-00  DARK              ≤1.25 ceil  61-67F        Dry; micro-pulse ≤5s only if overnight VPD>ceiling
──────────────────────────────────────────────────────────────────────────────
* 100-105F (ENV-3) NOT satisfiable today (no shade); hard-hold ≤95F (served cap 88F).
```

**Dusk cutoff arithmetic note:** `fog_time_window_end` is an integer hour; pushing `sunset_hour − dusk_offset_min//60` truncates the :18 minutes. Compute `cutoff_minutes = sunset_minute_of_day − dusk_offset_min` and floor to the hour, documenting the enforced cutoff is the *start* of that hour. Cap `fog_stress_window_latest_hour` AND the climate-mister `direct_wet_stress_latest_hour` at the cutoff so no stress path extends wetting past dark (today both push to 22 — ~2h past sunset, violating CYC-1/CYC-2/ACC-1).

### 4.3 Feed regimen + dose/EC

- **Nutrient target (FRT-1):** MSU 13-3-15 (8Ca-2Mg) RO formula, nitrate-dominant, low-P, Ca+Mg (alt Jacks 15-5-15 CalMag LX). **~50 ppm N** (40-60), **target_ec ≈ 0.40 mS/cm absolute on RO base** (= +0.3-0.5 over near-zero RO). Derived: P≈11.5, K≈57.7, Ca≈30.8, Mg≈7.7, Fe≈1.5 ppm (assuming elemental, not oxide, label analysis — document this in recipe notes). pH 5.6-6.2.
- **Timing (FRT-3/FRT-8):** once/day at **~06:30** (in the 06-09 dawn window; move from live 10:30). Hard `feed_window` (06-09) rejects fert states 2/4/7/8 AND manual fert buttons regardless of VPD. `irrig_last_center_doy` already prevents same-day re-trigger (no new latch needed).
- **EC handling (FRT-4 / SAF-2):** no inline EC today → **calibrated timed VOLUME** from a pre-mixed concentrate tank. Concrete target: mature bare-root Vanda ~50-150 mL feed solution/day; measure ml/min through the feed relay at line pressure; set `irrig_center_fert_duration_min = target_mL / ml_per_min`. Flag the live 6-min default as UNVALIDATED until measured. Later (HW-2): inline EC + EC-target controller; on EC NaN → timed-volume or skip-feed, never inject blind.

### 4.4 Absorption hold (FRT-6) then resume (FRT-7)

Today's behavior is backwards (immediate flush). Replace with:
1. Feed completes (~06:30-06:36). 
2. Set shared global **`feed_hold_until = now + post_feed_hold_min`** (default 90).
3. **Absorption hold 06:36→08:00:** block ALL clean wetting from ALL actuators (`center_mister`, `fog_rly`, all clean drips) by gating both the irrigation direct-wet path AND the climate `willMist`/`center_wet_allowed` on `now_ms < feed_hold_until_ms`.
4. At `feed_hold_until`: relocate the post-fert clean flush to fire here (FRT-7), then normal VPD-hold clean misting resumes (the rinse purges surface salts = intended).

**Implementation anchor (was missing):** declare `feed_hold_until` in `firmware/greenhouse/globals.yaml` as a shared timestamp; gate BOTH state machines on it; add invariant: fert_master ON ⇒ no `center_mister`/`fog_rly` activation for `post_feed_hold_min`.

### 4.5 Center fertigation path — 3-tier (FRT-5)

- **Tier 1 (deploy now, no HW):** feed Vanda via existing `center_drips_fertilized` at 06:30, preceded by a 06:00 clean center-mister rehydrate burst (pre-wet receptive velamen). Open-loop timed volume. Not spec-ideal (drip not mister) but the only code-deployable feed for the priority crop.
- **Tier 2 (bridge):** log a manual morning hand-mist feed via the Slack `crop.feed` intent (EC/volume/time) so the planner can reason about absorption-hold timing even when the actuator is manual.
- **Tier 3 (real build):** plumb `center_mister_fertilized` off `fertilizer_master_valve` (mirror `south_wall_mister_fertilized`); add the center fert job + interlock + `sync_fert_master`; add `entity_map` + equipment row. Decide FRT-5 "misters only": either retire the fertilized drips or document them as legacy — resolve before this lands.

### 4.6 SAF mapping (concrete)

| SAF | Design |
|---|---|
| SAF-1 | Add `sensor_degraded` (trigger `avg_ok==false`, broader than all-probes-NaN) → conservative timed wetting (dawn rehydrate + midday drench at calibrated volume, no VPD-chasing). Keep SENSOR_FAULT all-off for inf/garbage. |
| SAF-2 | Built with FRT-4: EC NaN → timed-volume or skip-feed, never inject. |
| SAF-3 | Single authoritative VPD-independent sunset-relative cutoff gating fog + all mister/drip before stress logic; cap both stress windows at cutoff. |
| SAF-4 | Non-bypassable per-zone (center) cumulative-runtime / wet-fraction cap (wire dead `mister_max_runtime_min`) + absolute daily volume hard-ceiling above the VPD-emergency budget. |
| SAF-5 | Fogger clean by plumbing (preserve); add invariant `fog_rly` ⊥ `fertilizer_master_valve`; keep periodic clean flush (relocate after FRT-6 hold). |
| SAF-6 | Airflow half exists; shade half blocked on HW. Hold ≤95F hard until shade. |

---

## 5. Crop profile + nutrient recipe + topology changes

All of §5 is **coordinator-owned** (touches `crop_target_profiles`, `crops`, `nutrient_recipes`, `db/migrations/`, a view, and an ingestor alert). Bundle the DB-side items into **migration `145-vanda-band-and-join-fix.sql`** with strict internal ordering.

### 5.1 Migration 145 internal ordering (load-bearing)

```
BEGIN;
(a) UPDATE crops SET is_active=false, stage='cleared', cleared_at=now() WHERE id=2;  -- Canna → patio
    INSERT INTO crop_events (crop_id,event_type,notes,created_at) VALUES (2,'removed','Canna to patio summer 2026',now());
(b) UPDATE daily_checklist_template SET is_active=false WHERE id=5;  -- stale 'Water canna lilies if dry'
(c) UPDATE crop_target_profiles ctp SET crop_catalog_id = cc.id FROM crop_catalog cc
      WHERE ctp.crop_catalog_id IS NULL
        AND cc.slug = CASE ctp.crop_type WHEN 'pepper' THEN 'peppers'
                                         WHEN 'strawberry' THEN 'strawberries' ELSE ctp.crop_type END;
(d) DELETE FROM crop_target_profiles WHERE crop_type='orchid';
(e) INSERT new orchid spring rows (§5.2, anchors matching §3.3);
(f) INSERT summer rows: SELECT ... 'summer' ... FROM crop_target_profiles WHERE crop_type='orchid' AND season='spring';
    -- also author summer rows for the OTHER active crops or rely on the nearest-season fallback in (h)
(g) INSERT vanda_orchid_active nutrient recipe (is_active=FALSE until operator confirms salt+dosing path);
(h) CREATE OR REPLACE FUNCTION fn_band_setpoints  (smooth engine + catalog/is_active join + fn_current_season + fallback, full h0 AND h1 fallback blocks);
    CREATE OR REPLACE FUNCTION fn_center_band_setpoints (orchid-only center band);
    CREATE OR REPLACE FUNCTION fn_zone_vpd_targets (season-aware; COALESCE center default 0.85 not 0.80; west default 1.5);
    CREATE OR REPLACE FUNCTION fn_target_band (catalog join, not name join);
COMMIT;
```

Ordering rationale: backfill (c) must precede the catalog-join rewrites (h) or pepper/strawberry drop out; Canna clear (a) must precede the `is_active` join so Canna's wide VPD band does not leak; DELETE (d) precedes INSERT (e) to avoid the unique-key collision on `(crop_type,growth_stage,hour_of_day,season,greenhouse_id)`.

**Inversion guard (critical).** Raising orchid night `vpd_ideal_min` to 0.75 while `fn_band_setpoints` still takes house `MAX(vpd_min)` would invert the house VPD band at night (orchid 0.75 > lettuce `vpd_ideal_max` 0.55-0.60 for ~13 night/evening hours). **This is exactly why `fn_center_band_setpoints` is required**: serve center VPD/temp from the orchid-only band, and keep the house band from the non-orchid crops so the Vanda night floor never inflates the house `vpd_low`. Add a defensive clamp in `fn_house_vpd_control_band` (if `low > high`, clamp) as belt-and-suspenders.

### 5.2 Orchid `crop_target_profiles` rewrite

Replace the existing too-humid orchid rows (night VPD 0.2) with the §3.3 anchors. Per-hour: temp_ideal_min/max and vpd_ideal_min/max as in §3.3; `temp_stress_low/high = 55/100`; `vpd_stress_low/high = 0.50/1.50`; `dli_target_mol = 12`; `source='vanda_spec_v1.0'`; `greenhouse_id='vallery'`. Author identical spring + summer (Longmont Vanda active season). Dormant rows are **PROVISIONAL** (OPN-4 needs empirical confirmation): store under a distinct non-joined key (e.g. `crop_type='orchid_dormant_provisional'`) so they are never served until renamed — do NOT insert dormant values into a season any function reads.

### 5.3 Nutrient recipe (FRT-1)

```sql
INSERT INTO nutrient_recipes (name, crop_id, stage, target_ec, target_ph_low, target_ph_high,
  n_ppm, p_ppm, k_ppm, ca_ppm, mg_ppm, fe_ppm, stock_a_ml_per_l, stock_b_ml_per_l, notes, is_active)
VALUES ('vanda_orchid_active', 5, 'vegetative', 0.40, 5.6, 6.2,
  50, 11.5, 57.7, 30.8, 7.7, 1.5, NULL, NULL,
  'Bare-root Vanda RO feed v1.0. SINGLE-SALT MSU 13-3-15 (8Ca-2Mg) RO formula / alt Jacks 15-5-15 CalMag LX. '
  'target_ec ABSOLUTE on RO base (~0), = spec +0.3-0.5 over RO. P/K computed as ELEMENTAL (not oxide). '
  'stock_a/stock_b NULL: NOT 2-part GH Flora — do NOT use A/B ml/L dose math; dose by mixing to target_ec. '
  'AM feed only; 60-90min absorption hold after; NO organics/particulate (FRT-2).',
  FALSE);  -- is_active FALSE until operator confirms salt on-hand + dosing path (avoid blind dose, SAF-2)
```

Flag for coordinator: add a `salt_model` (`'two_part'`|`'single_salt'`) or `product_name` column so consumers branch cleanly instead of inferring from NULL stocks (any code doing `stock_a × factor` would produce NULL EC for Vanda — a SAF-2 blind-dose risk). Bounce `verdify-mcp` so the recipe enters plan context.

### 5.4 Topology refresh + alerting

- **Clear Canna (id 2)** via Slack `clear Canna Lilies` (preferred, writes provenance) or migration (a). No patio position exists → clear, not transplant. Optionally add a `patio` pseudo-position so seasonal moves preserve the record.
- **House Plants (id 4)** already correct (`west`, `is_active=f`) — no change. **Vanda (id 5)** confirmed correct.
- **Unpotted ≠ broken (Correction #1):** rewrite `v_irrigation_sensor_feedback_status` to LEFT JOIN probe→position→crop occupancy and emit status **`unpotted`** with `required_action` "Position unpotted (Canna on patio); no probe action needed" instead of "Repair or replace SEN0601". In `ingestor/tasks.py` (~2251-2280), before raising `soil_sensor_offline` for south/west soil columns, check whether that zone has an active crop; if none, skip/downgrade to info. **Do NOT recommend replacing probes.**

### 5.5 Center VPD sensor (HW-1)

Install 6th Modbus RH/temp probe (addr 10) in the center canopy; add `vpd_center` template in `sensors.yaml` (mirror `vpd_north`); add `climate.vpd_center` column + `entity_map.py` alias; rewire center-stress comparisons (controls.yaml ~870/1009/1080/1216) from `avg_vpd` to `vpd_center`. **Touches `entity_map.py` → CLAUDE.md rule 7: PR body must name `verdify-ingestor` (and `verdify-mcp`) to bounce.** Firmware side is a full-artifact OTA (replay-diff, invariants, 48h bake, ≤1 OTA/week).

### 5.6 Restart notes (CLAUDE.md rule 7)

- Migration 145 (functions + data): bounce **setpoint-server (dispatcher)** + **`verdify-mcp`**. No firmware OTA. Migration does not touch `verdify_schemas/`/`entity_map.py`/`mcp/server.py` so the strict drift-guard restart rule is not triggered, but the dispatcher must re-read to serve the new band.
- Unpotted-alert ingestor change: bounce **`verdify-ingestor`**.
- `vpd_center` schema PR (touches `entity_map.py`): bounce **`verdify-ingestor`** + **`verdify-mcp`**.

---

## 6. New capabilities to build (consolidated)

| Capability | Owner(s) | Rough effort | Unlocks |
|---|---|---|---|
| **Non-overhead night humidity source** (evaporative pad / wet-floor / humidifier+fan) — OPN-2, HIGHEST VALUE | operator (HW) → firmware | HW install + 1 relay + small control loop | Decouples overnight humidity from root wetting → resolves CYC-3 / CYC-4 compromise; lets nights stay drier (ACC-1/ACC-2) |
| **Center fertigation path** (`center_mister_fertilized`) | operator (plumb) → firmware (relay+state) → coordinator (entity_map/equipment row) | Plumbing + 1 relay + state-machine job | FRT-3/FRT-5 for priority Vanda via velamen-wetting mister |
| **Center VPD sensor** (HW-1) | operator (probe) → firmware (sensor+rewire) → coordinator (column+map) | Modbus probe + template + column + rewire | ENV-1 / IRR-1 true closed-loop center control (ends `vpd_avg` proxy) |
| **Motorized 25-35% shade + relay** (ENV-4) | operator (HW) → firmware (relay+loop) | Motor/curtain + relay + control loop | ENV-3 (100-105F ceiling), ENV-4, SEA-1 shade thresholds, SAF-6 "shade before ceiling" — the real hot-day fix (lessons: "shade cloth, not software") |
| **HAF circulation fan** (ENV-6) | operator (HW) → firmware | Low-speed fan + relay (vent-interlock exempt) | "Continuous gentle circulation whenever wetting" |
| **Inline feed-line EC (+pH) probe** (HW-2) | operator (HW) → firmware/coordinator | Probe + ESPHome sensor + `ec_input` capture | FRT-4 closed-loop dosing + SAF-2 |
| **PAR/fc light calibration** (HW-3, OPN-3) | firmware (+ operator for PAR) | fc conversion + `greenhouse_light_fc` readback + reference-meter calibration | ENV-4 fc band evaluation; optional PAR sensor for true DLI |
| **Feed-water TDS + temp sensing** (HW-5, IRR-7) | operator (HW) → firmware | Source TDS/temp probe | IRR-7 tepid/low-TDS verification + SAF guard |

---

## 7. Acceptance criteria mapping

| ACC / OPN | Requirement | How measured in telemetry/observations |
|---|---|---|
| ACC-1 | Velamen cycles green→silvering daily, silver/dry ~1-2h after dusk cutoff | `alert_monitor` "dry-by" check: last `center_mister`/`fog_rly` ON timestamp vs dusk cutoff (must be ≥1-2h before dark). Slack `crop.observe` velamen-color log (green AM / silver PM). |
| ACC-2 | No standing water/condensation on crowns overnight | `equipment_state`: zero `center_mister`/`fog_rly` ON between dusk cutoff and sunrise (except ≤5s micro-pulses). Slack `crop.observe` morning crown check. |
| ACC-3 | No salt crust over weeks; no leaf-tip burn | Weekly Slack `crop.observe`; once HW-2 lands, `ec_input` trend within recipe target ±. |
| ACC-4 | Leaf medium grass-green (not dark=under-light, not red=over-light) | Slack `crop.photo_observation` (file refs); DLI trend vs `dli_target_mol`=12; once fc-calibrated, fc within 4000-6000 band. |
| ACC-5 | VPD within phase band during wet window | Once HW-1 lands: `climate.vpd_center` within served `vpd_target_center` ±0.15 during wet window. Interim: `vpd_avg` proxy vs band. |
| ACC-6 | No fertilizer residue/scale on fogger or leaf film | SAF-5 invariant (`fog_rly` ⊥ `fertilizer_master_valve`) green in replay/invariant suite; periodic visual via Slack. |
| OPN-1 | Verify misters SATURATE velamen (green), not just dampen | Slack `crop.observe` post-rehydrate (roots green within minutes); if not, escalate heavier drench path. |
| OPN-2 | Add non-overhead night humidity source | Tracked as HIGHEST-value new build; once installed, CYC-3/CYC-4 retire and overnight VPD held without wetting. |
| OPN-3 | Calibrate lux→fc; tune dusk offset to measured dry-down | `greenhouse_light_fc` vs reference meter; `dusk_offset_min` tuned so observed silver/dry lands 1-2h post-cutoff. |
| OPN-4 | Confirm dormant setpoints empirically | Dormant profile rows stay `provisional`/non-served until Slack-logged fall/winter dry-down observations exist. |

**Working invariants to preserve (do not break):** 16/16 firmware invariants over 193,525 rows; FSM stable (16 vs 30 transitions/hr cap); dispatcher 95% confirm p50 37s, 0 push failures; ingestor <1min freshness, 0 stale sensors; thermal safety (dp margin ≥5F); planner reward integrity (correct runtime kWh).
