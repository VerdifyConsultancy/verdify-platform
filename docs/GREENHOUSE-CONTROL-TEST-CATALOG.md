# Greenhouse Control Test-Case Catalog

*Single 367 sq ft greenhouse, Longmont CO. Band-first FSM (`sw_fsm_controller_enabled=true` in production). Audience: the owner, to understand how temp, VPD, outdoor, solar, season, and equipment couple — and where coverage is thin. All "expected" values below are the VERIFIED/corrected responses; enumerator errors are flagged.*

Key constants (production defaults, verified): `temp_high=82`, `temp_low=65`, `vpd_low=0.8`, `vpd_high=1.4`, `band_vpd_hysteresis=0.198`, `cool_exit_hysteresis=1.5`, `cool_stage2_over_high_f=1.0` (fan2 fires at temp > 83.0), `vpd_high_eff=1.25`, **fog relay trigger = vpd_high_eff+fog_escalation = 1.65**, `fog_escalation=0.4`, `safety_max=95` (default), `safety_min=45`, `vpd_watch_dwell=60s` (NOT 180s — common enumerator error), `mist_s2_delay=300s` (NOT 60s), `mister_all_kpa=1.9` (S2 rotation threshold), `mister_daily_volume_max_gal=360`.

---

## 1. The temp × VPD interaction matrix

The FSM is **band-first with temp priority**: whenever temp is out of band, the temp candidate (VENT_COOL / HEAT / SAFETY) preempts every VPD-only mode (SEALED_MIST, DEHUM_VENT). VPD then rides as a non-blocking *assist* under an open vent. The free lever is whatever the outdoor air gives you (sensible vent cooling, latent fog, or nothing).

| | **Wet (VPD < 0.8 — low)** | **Ideal (0.8 ≤ VPD ≤ 1.4)** | **Dry (VPD > 1.4 — high)** |
|---|---|---|---|
| **Hot (temp > 82)** | **Actuator:** vent + fans. **CONFLICT:** temp wants air exchange, low-VPD wants more moisture — venting hot+humid air imports heat AND raises RH. Fog forbidden (VPD<1.65, often dew_margin<8). **Lever:** none if outdoor hot+wet. **Outcome:** *sauna trap* — rides up to safety_max (HOT-05). | **Actuator:** vent + lead fan (+fan2 if >83.0). **ALIGNED** (only temp out of band; VPD neutral). **Lever:** economizer if outdoor cooler/drier; none if outdoor ≥ indoor. **Outcome:** free cooling when outdoor is a sink, else *heat-soak* import (HOT-01/02/03). | **Actuator:** vent + both fans + **fog** (VPD>1.65). **ALIGNED** — one actuator (fog) fixes both: latent cooling + raises RH toward band. **Lever:** evaporative fog into dry air = free latent sink. **Outcome:** *best hot corner* — both axes converge fast (HOT-04/10, LSE-13). |
| **In-band (65–82)** | **Actuator:** DEHUM_VENT (vent+fan) — IF outdoor not cold-guarded & not econ_blocked. **ALIGNED** (VPD-only demand). **Lever:** free vent dehum when outdoor dewpoint < indoor. **Outcome:** drier air raises VPD into band; cold/econ guard can suppress → hold (ET-05/06, IB-04). | **Actuator:** none — **IDLE**. **No conflict**, no demand. **Lever:** solar band + thermal mass hold open-loop. **Outcome:** quiet controller, zero cost (IB-01, ET-15). | **Actuator:** SEALED_MIST (center → S2 → fog), dwell-gated 60s. **ALIGNED** (VPD-only). **Lever:** none — humidification is always water cost. **Outcome:** seal + mist lifts RH down toward band; escalates only if sustained (ET-07→10, IB-05). |
| **Cold (temp < 65, or < heat target+hyst)** | **Actuator:** heat1 (+heat2 latched if <65). **ALIGNED** — heating raises temp AND lowers RH/raises VPD; one actuator serves both. Cold-vent guard disables DEHUM. **Lever:** none (outdoor too cold to vent). **Outcome:** heat dries air as a free side-effect (LSE-11, PZ-06). | **Actuator:** heat1 (+heat2 if <65). **No conflict** — temp-only demand. **Lever:** solar gain if daytime. **Outcome:** electric-before-gas staging; gas latches below 65, clears at 69.25 (ET-03/04, IB-03). | **Actuator:** heat1 by day (band heat) — but VPD-rescue **suppressed at night** (ENV-2). **CONFLICT:** low temp wants sealed heat, high VPD wants misting. Cold-vent guard + arbitration favor HEAT; misting may still run sealed. **Lever:** night VPD bias lets the mister run instead of heating. **Outcome:** band heat fires; econ-rescue heat vetoed at night (FE-11/12, LSE-07). |

**The two conflict diagonals** (genuinely hard cells, where actuators fight): **Hot+Wet** (vent imports heat, fog forbidden — no escape but safety_max) and **Cold+Dry-at-night** (heat-to-chase-humidity is policy-suppressed). These are the cells where an untested failure hurts most.

---

## 2. Interaction map (the physics)

**SVP(T) welds temp and VPD.** VPD = SVP(T)·(1 − RH). Saturation vapor pressure rises exponentially with temperature, so *the same actuator moves both axes*. Any time you heat, you raise SVP and therefore VPD (dries the air) for free; any time you cool, you drop SVP and VPD (humidifies). This is why the aligned diagonal of the matrix (hot+dry → fog, cold+wet → heat) is "one actuator fixes both," and why the controller never needs a separate dehumidifier in cold weather — the furnace *is* the dehumidifier.

**When venting helps vs. hurts is purely an outdoor-relation question.** Venting moves indoor air toward outdoor enthalpy. If outdoor is cooler+drier (the economizer win), vent is free sensible cooling AND free dehum. If outdoor is hotter (HOT-03/05, LSE-01/02), vent *imports* heat and the FSM is stuck running fans for negative benefit until safety_max. If outdoor is hotter AND wetter (HOT-05, LSE-02 monsoon), it imports heat and humidity and the only survival cooler (fog) is forbidden because the air is already near saturation (dew_margin < 8°F or RH > 90%). The controller cannot tell you it's losing — it keeps the vent open because temp demands cooling; the loss is physical, not logical. The `override_summer_vent` *label* only arms when outdoor is ≥5°F cooler AND ≥5°F drier by dewpoint AND data is fresh AND `humidify_ready` (high VPD + matured dwell) — so in-band-VPD nights vent on plain `temp_high`, never the `summer_vent` label (a recurring enumerator confusion in FE-01, FE-13, LSE-04).

**Evaporative fog cools but spends VPD headroom.** Fog evaporates → latent heat leaves the canopy (sensible cooling toward the wet-bulb floor) AND water vapor enters the air (RH up, VPD down). In dry air this is a double win: you cool *and* pull a too-high VPD back into band (HOT-04, LSE-13). But it's self-limiting — as RH climbs toward the 90% ceiling or dew_margin shrinks below 8°F, evaporation efficiency collapses and the fog gate hard-closes (LSE-02). The relay only opens fog above VPD 1.65; in SAFETY_COOL the bar drops to vpd_high_eff (1.25) for survival, but even at 101°F a wet house (VPD 0.8) gets *no fog* (HOT-07) — adding water to saturated 101°F air does nothing, and the gate refuses.

**Solar gain & thermal mass are the free batteries.** Daytime solar (up to ~1000 W/m²) heats the house for free; the band rises with solar phase so the controller *tolerates* the noon peak rather than fighting it (FE-09). The 367 sq ft mass stores that heat and bleeds it overnight (the overnight heat-soak of HOT-01). The strategic free lever is *anticipatory*: pre-cool the mass overnight into cold dry air (LSE-04, FE-01) so the summer day starts cold, and solar-hoard on a sunny winter day (LSE-06) to bank heat against the −20°F night. The controller has no forecast of its own — it phase-follows whatever band the planner serves; "forecast" is entirely the planner shaping anchors (FE-09/10).

**Seasonal sign-flips live one layer up.** The FSM is season-agnostic — it consumes whatever `temp_low/temp_high/vpd_*` the band anchors produce via `band_value_at_phase()`. The same noon phase that needs cooling in summer needs heating in winter; a single anchor curve keyed `season='all'` *cannot* serve 110°F-summer-noon and −5°F-winter-noon (LSE-08). The firmware is deterministic and safe either way, but a mis-keyed band over-cools all summer and over-heats all winter toward a moderate setpoint. This is the deepest gap: not a logic bug, a band-content bug no firmware test can catch.

---

## 3. The full catalog

Format: `id | inputs (short) | expected mode/relays | interaction | outcome | conflict-resolution | coverage`. Relays abbreviated V=vent, F1/F2=fans, FOG, H1/H2=heat, M=mister.

### Region: Hot indoor (cooling demand)

| id | inputs | mode / relays | interaction | outcome | conflict-resolution | cov |
|---|---|---|---|---|---|---|
| HOT-01 | 83.5°F, VPD 1.1, out 70/dp55, dark | VENTILATE; V+F1+**F2** (83.5>83.0)+fog OFF, reason `temp_high` | temp-only out; venting toward cooler/drier outdoor holds VPD | free cooling, exits <80.5 in min | aligned | partial |
| HOT-02 | 83°F, VPD 1.2, out 83/dp60, ramp | VENTILATE; V+F1, **F2 OFF** (83 not>83.0), fog OFF | temp over, outdoor==indoor, no sink; floored vent effect still beats IDLE | holds ~83, vent useless | none; fan power for ~0 cooling | unit |
| HOT-03 | 85°F, VPD 1.3, out 95/dp62, peak | VENTILATE; V+F1+F2 (85>83), fog OFF (1.3<1.65) | temp 3°F over, outdoor hotter → vent imports heat | heat-soak trap, escape only at safety_max | logic-vs-physics: vent imports heat | partial |
| HOT-04 | 88°F, VPD 3.0 (dry), out 98/dp50, peak | VENT_COOL_FOG_ASSIST; V+F1+F2+**FOG** | both axes out; dry_excess 1.6≥0.4 → FOG; fog cools + cuts VPD | both converge despite hotter outdoor | ALIGN — fog fixes both | partial |
| HOT-05 | 86°F, VPD 0.6 (wet), out 96/dp78, peak | VENTILATE; V+F1+F2, **fog OFF** (0.6<1.65 AND dew_margin 5<8) | temp over, VPD low; vent imports hot+humid; fog doubly gated | **sauna trap**, rides up to safety_max | temp wants cooling, only cooler (fog) forbidden | partial |
| HOT-06 | 101°F, VPD 3.5 (dry), feed_hold ON, out 99 | SAFETY_COOL; V+F1+F2+**FOG** | hard rail; SAF-6 bypasses feed_hold + wet-taper; VPD>1.25 | dry-air survival fog, drops fast | safety overrides; feed_hold bypassed | unit |
| HOT-07 | 101°F, VPD 0.8 (wet), out 99/dp79 | SAFETY_COOL; V+F1+F2, **fog OFF** (0.8<1.25) | hard rail; fog still needs VPD>vpd_high_eff | air purge only, rides ≥100°F | safety overrides; fog correctly withheld | partial |
| HOT-08 | 84°F, VPD 1.9 (dry), out 68/dp50, **dark** | VENTILATE; V+F1+F2; **fog night-OFF in prod** (stress override default FALSE), ON only if enabled | dry_excess 0.5≥0.4 would label FOG_ASSIST, but night fog needs `direct_wet_stress_override_enabled` (prod=FALSE) | cool dry vent drops temp fast regardless | temp+VPD align on fog *when permitted*; night switch decides | **GAP** |
| HOT-09 | 80.7°F, VPD 1.1, prev VENT (was_cooling) | VENTILATE (exit hyst 80.5); V+F1, F2 OFF, fog OFF | below raw ceiling, above 1.5°F exit deadband | gentle vent, exits <80.5, no oscillation | pure hysteresis | unit |
| HOT-10 | 90°F, VPD 4.5 (extreme dry), out 80/dp30, peak | VENT_COOL_FOG_ASSIST; V+F1+F2+FOG | both far out; vent(sensible)+fog(latent) both work | fastest dry-hot convergence | aligned on cool+humidify | partial |
| HOT-11 | 87°F, VPD 3.0, **occupancy=1**, out 78 | VENTILATE; **V only** (F1/F2/fog OFF by occupancy) | would be FOG_ASSIST, but occupancy zeroes fans+fog; vent stays | weak passive cooling while occupied | occupancy overrides cooling; coolers disabled | unit |
| HOT-12 | 85°F, VPD 2.8, out 70/dp45 **stale 9999s**, peak | VENT_COOL_FOG_ASSIST; V+F1+F2+FOG | temp drives vent on indoor temp; staleness only kills `summer_vent` label, not temp-vent | cooling proceeds, fog fires (day) | no conflict; staleness suppresses label only | **GAP** |

### Region: In-band maintenance + edge transitions

| id | inputs | mode / relays | interaction | outcome | conflict-resolution | cov |
|---|---|---|---|---|---|---|
| IB-01 | 73.5°F, VPD 1.05, center-band | IDLE; all OFF | both errors 0 | holds indefinitely, zero cost | none | unit |
| IB-02 | 81.8°F, VPD 1.1, prev IDLE | IDLE; all OFF | under ceiling, margin 0 (outdoor warm); 0.2°F rise → VENT | holds; 1.5°F gap prevents chatter | none | partial |
| IB-03 | 70.4°F, VPD 1.0, out 45 | IDLE; all OFF | above heat-S1 entry 70.25 | holds; 0.2°F drop → HEAT | none | partial |
| IB-04 | 73°F, VPD 0.62, out dp48 | IDLE; all OFF | above dehum-enter 0.602 (hyst 0.198) | holds; drop to 0.60 → DEHUM_VENT | none | unit |
| IB-05 | 74°F, VPD 1.38, vpd_watch=0 | IDLE; all OFF | under vpd_high 1.4; dwell to arm mist = **60s** (not 180s) | holds; sustained >1.4 for 60s arms mist | none | unit |
| ET-01 | 82.0→82.2°F, VPD 1.1, out 70 warm | VENTILATE; V+F1, F2 if>83.0, fog OFF, reason `temp_high` | margin 0 (warm) → enter at raw 82; exit ≤80.5 | vent pulls toward 70; 1.5°F gap | pure temp, VPD in-band | unit |
| ET-02 | 80.6→80.4°F, prev VENT, vent 40s | **IDLE decision**; relays HELD by min_vent_on 60s / min_fan_on 120s | exit at 80.5; min-on timers hold relays | mode IDLE but vent/fan run out timers; no slam | mode-vs-dwell → dwell timer wins | partial (relay hold = harness blind spot) |
| ET-03 | 70.3→70.1°F, VPD 1.0, out 45 | CLIMATE_HEAT → **mode IDLE + H1**; V closed | <70.25 → heat1; HEAT is a climate action, mode enum IDLE | H1 raises temp; min_heat timers prevent short-cycle | temp-only | unit |
| ET-04 | 63°F deep, out 35 | mode IDLE + **H1+H2** (latched) | heat2 latches at temp<temp_low **65** (NOT a stage-2 margin) | both stages, gas latched to 69.25 | temp-only; heat2_requires_heat1 interlock | unit |
| ET-05 | 73°F, VPD 0.61→0.59, out 58/dp45 warm | DEHUM_VENT; V+F1 (both fans if very dry) | enter <0.602, !econ_block, cold_dehum_allowed; exit ≥0.8 | drier air raises VPD; 0.602/0.8 asymmetry | VPD-only; inhibited by econ/cold guard | unit |
| ET-06 | 66.5°F, VPD 0.55, out 50/dp30 | **IDLE + H1** (NOT all-off!) | cold guard suppresses DEHUM; but 66.5<70.25 → **heat1 fires** | enumerator "none" WRONG — heat runs | cold guard wins for vent; temp also below heat line | partial (enumerator bug) |
| ET-07 | 74°F, VPD 1.38→1.45 holds, out 65 | IDLE (dwell `engage_delay`) → SEALED_MIST S1 | dwell = **60s** (not 180s); >1.4 sustained arms | transient blip resets timer | VPD-only; dwell is cost gate | unit |
| ET-08 | 74°F, prev SEALED, VPD 1.25→1.19 | IDLE (`humidify_resolved`); M OFF, stage→WATCH | resolve at vpd_high−HV = 1.202 | exits, watch timer cleared, re-earns dwell | VPD satisfied | partial (high-side resolve only partly pinned) |
| ET-09 | prev SEALED S1, timer≥delay, VPD 1.55→1.19 | SEALED S1→**S2**→IDLE | S1→S2 needs **mist_s2_delay 300s** (not 60s) + VPD>1.4 | escalates to all zones, sheds, exits | VPD-only, staged; no skipping S1 | unit |
| ET-10 | prev SEALED S2, VPD→1.85→1.75 | SEALED S2→**FOG**→S2; FOG closes vent | S2→FOG at VPD>1.8 (vpd_high+0.4); retreat ≤1.8 | fogger above 1.8, misters below, debounced | VPD-only, gated by RH/dew/phase | unit |
| ET-11 | 82.5°F, VPD 1.6, out 72/dp50 | VENT_COOL_MIST_ASSIST (mode VENTILATE); V+fans+**mist assist** | both out; SEALED blocked `temp_priority_blocks_seal`; dry_excess 0.2≥0.05 → mist-assist (but <0.4 so NOT fog) | vent opens (priority), mist as open-vent assist | **temp wins, VPD as assist** | unit |
| ET-12 | 82.0→82.1°F, dwell_gate ON 300s, last txn 90s | VENTILATE (compliance override) | `compliance_preempts_dwell` when temp_error>0; 82.1 breaches → flip allowed | out-of-band flip passes; same-band whipsaw damped | out-of-band compliance always wins | partial |
| ET-13 | 72.0°F, VPD 0.90, **econ_block ON**, day vs night | IDLE both; **day H1 ON / night all OFF** | econ-rescue: VPD<vpd_low_eff 0.95 & econ_block & temp<82−5=77 & !night | day gentle heat; night stays cool/humid for orchid drop | VPD-rescue-heat vs night-drop: night wins | unit |
| ET-14 | 74°F, VPD 1.5, **occupied** | IDLE; M/FOG OFF (occupancy), running fan honors min-on | `moisture_blocked_by_occupancy` suppresses wetting | holds elevated VPD until occupant leaves | occupant wins, no wetting | partial |
| ET-15 | 73.5±0.3°F, VPD 1.05±0.05, 60 min | IDLE sustained; none for the hour | both parked mid-band; transition_cap ≤30/hr (here 0) | zero toggles all hour | none | invariant |

### Region: Longmont saturated extremes + seasonal

| id | inputs | mode / relays | interaction | outcome | conflict-resolution | cov |
|---|---|---|---|---|---|---|
| LSE-01 | 110°F, RH25/VPD4.2, out 108, summer noon | SAFETY_COOL; V+F1+F2+**FOG** | rail; vent moves toward 108 (no relief); fog only enthalpy sink in 25% RH | fog→wet-bulb floor; rides until <safety_max; **360-gal ceiling risk** | both demand cool+humidify; fog/vent legal (VPD>0.5·vpd_max_safe) | unit (110°F itself corpus-unexercised) |
| LSE-02 | 110°F, RH70/VPD2.0, out 100/dp72 monsoon | SAFETY_COOL; V+F1+F2, **fog ON at RH70** (locks out only >90% RH or dew<8) | enumerator said fog off at RH70 — **WRONG**, fog still permitted | evap collapses as RH→sat; once RH>90 fog gates off, rides ≥100 | physical conflict (wet air defeats evap) | **GAP** |
| LSE-03 | 96°F, RH30/VPD3.5, out 92 | SAFETY_COOL (default safety_max=95!); V+F1+F2+FOG | enumerator "depends 95 vs 100" **WRONG** — default IS 95 | dry air → fog cools, exits in min | no conflict, both want cool+wet | unit |
| LSE-04 | 78°F, VPD1.0, out 58/dp48, summer night | **IDLE** (78==temp_high, strict > fails); summer_vent does NOT arm (in-band VPD → !humidify_ready) | enumerator "summer_vent drives venting" **WRONG** | if temp 0.5°F over → plain temp_high vent purge | no conflict | partial |
| LSE-05 | 44°F, VPD0.4, out −20, winter night | SAFETY_HEAT (44≤safety_min 45); H1+H2+lead fan, V closed | hard low rail; glazing loss max | both stages claw off floor; heavy gas | temp-only; heat/vent interlock | unit |
| LSE-06 | 70°F, VPD1.1, out −5, **winter full sun** | IDLE; all OFF | passive solar holds band; cold_dehum_allowed FALSE | solar-hoard banks mass for −20 night | no conflict; cold guard blocks DEHUM | partial |
| LSE-07 | 64°F, RH18/VPD2.5, out −10, winter night | SEALED_MIST (dwell-gated) + **band H1 may run** (64<67.5) | enumerator "THERMAL_RELIEF backstop" + "zero heat" **WRONG**: band-first → IDLE+mist_backoff, and band heat can fire | misting after dwell; econ-rescue heat suppressed (ENV-2), band heat not | VPD leads; band heat as side-effect | **GAP** |
| LSE-08 | (A) 88°F summer / (B) 58°F winter, **same `season=all` anchors** | (A) VENTILATE (B) HEAT | **sign-flip at band/anchor layer, NOT FSM** — one curve can't serve both | summer over-cools, winter over-heats | upstream conflict; FSM faithfully serves anchors | **GAP** (no firmware test catches it) |
| LSE-09 | 06:00 60°F/out35 → 13:00 90°F/out75, VPD 0.6→2.2 | HEAT→IDLE→VENTILATE (±SEALED) | axis lead flips via normalized arbitration; hysteresis+dwell limit churn | smooth handoff; mis-anchor (LSE-08) shifts crossings | temp vs VPD via normalized error | partial (corpus IS shoulder-season) |
| LSE-10 | 104°F, VPD3.8, **360-gal ceiling HIT**, out 105 | SAFETY_COOL; V+F1+F2, **fog/M OFF at controls.yaml ceiling** | suppression is in controls.yaml drive layer, downstream of resolve_equipment | only cooler lost; rides hot until sunset | demand-vs-resource-cap: ceiling wins | **GAP** |
| LSE-11 | 58°F, RH88/VPD0.22, out 5, winter night | mode IDLE + H1+H2 (latched); DEHUM inhibited | enumerator "temp-mode-over-VPD precedence" **WRONG** — it's the cold-dehum guard + arbitration | heating dries air (VPD off 0.22); self-resolves | cold guard removes DEHUM, then HEAT serves temp | partial |
| LSE-12 | indoor NaN/−40°F, out −20, winter | SENSOR_FAULT; **all OFF** | fault preempts (index 0); mode_prev preserved | software safe-off; hardware thermostat holds freeze | safety-vs-availability: safe-off | unit |
| LSE-13 | 86°F, RH22/VPD3.6, out 79/dp35, summer | VENT_COOL_FOG_ASSIST; V+F1+F2+FOG | both want cool+humidify; dry air → cheap evap | textbook good summer extreme; exits to IDLE | no conflict; temp-driven + fog assist | unit |

### Region: Per-zone divergence + arbiter

*The FSM consumes **house-average** temp/VPD only. Per-zone divergence never reaches mode selection — the entire zone arbiter/legacy selector is gated behind `humidity_demand` (mode==SEALED_MIST||vent_mist_assist). Replay sets all zone VPDs = avg, so almost all per-zone machinery is corpus-blind.*

| id | inputs | mode / relays | interaction | outcome | conflict-resolution | cov |
|---|---|---|---|---|---|---|
| PZ-01 | avg 74/1.0, center 0.5, south 1.9 | IDLE | avg in-band → seal never entered → south stress invisible at mode layer | nothing fires; south under-humidified | none | partial |
| PZ-02 | avg 1.55, arbiter ON, center 1.5/south 1.6 | SEALED_MIST (dwell 60s) → **center** (rank 1) | dwell 60s not 180s; S2 needs delay 300s **AND avg>1.9** (1.55<1.9 → S2 never) | center served, south deferred | arbiter center>south by rank | partial |
| PZ-03 | avg 1.6, **arbiter OFF (prod default)**, legacy scorer | SEALED_MIST, legacy `select_most_stressed_zone` | east-adjacency +0.16 to south+center; west (highest own stress) starved to 10-min watchdog | documented west-starvation pathology | east-adjacency bias unfair until watchdog | **GAP** (no test, replay zone-flat) |
| PZ-04 | avg 1.5, east 2.2 (no relay), arbiter ON | SEALED_MIST | east has no actuator → excluded; neighbors served | east relay never fires | arbiter skips actuator-less zone | partial (east-skip is unit-tested) |
| PZ-05 | avg 84/1.6, south 1.9, out 75 | VENTILATE family (not SEALED); V+fans+mist assist | SEALED blocked `temp_priority_blocks_seal`; fog NOT (dry_excess 0.2<0.4) | vent + open-vent mist assist; cools to outdoor | **temp wins mode, VPD as assist** | partial |
| PZ-06 | avg 60/0.55, out 40, night | CLIMATE_HEAT (mode IDLE + H1+H2); DEHUM inhibited | temp-low + VPD-low ALIGN; cold guard blocks DEHUM | heat dries house passively | aligned; cold guard resolves would-be dehum | replay |
| PZ-07 | avg 1.5, west age 11min>10min | SEALED_MIST → **west** (fairness watchdog) | `select_overdue_zone` runs FIRST, bypasses arbiter+legacy | west fires despite rank 3 | fairness preempts rank, bounded 10 min | **GAP** |
| PZ-08 | avg 1.0, all zones below target | IDLE; arbiter returns −1 | both in-band; mister machinery never runs | quiescent | no demand | unit |
| PZ-09 | avg 1.6, "vpd_center=NaN" | SEALED_MIST → center can still WIN | **premise WRONG**: center has NO sensor, always uses avg proxy; no "center NaN" inversion | enumerated priority-inversion does not occur | re-described: no center sensor to lose | **GAP** |
| PZ-10 | sensor_degraded, per-zone reads | temp-only control; VPD disabled | vpd_control_trusted FALSE → no SEALED/DEHUM/fog/mist; only timed center burst | south/west get nothing | suppress all VPD wetting → timed bursts | unit |
| PZ-11 | avg 1.7, **occupied** | wetting blocked; vent also blocked (non-safety) | occupancy gates actuation not mode label; **no vpd_max_safe emergency-seal override exists** | all misters/fog OFF | occupancy unconditionally blocks wetting | **GAP** |
| PZ-12 | **feed_hold ON**, south 1.9 | routine AND stress wetting blocked | enumerator "stress override still serves south" **WRONG** — feed_hold checked before stress, only SAFETY_COOL fog bypasses (SAF-6) | south NOT relieved during hold | absorption hold wins over routine+stress | partial |
| PZ-13 | avg 67 (in-band), south 86/center 64 | IDLE | house temp on AVERAGE; pockets cancel; zone_temp_stress moot (no SEALED) | south hot+dry, center cool+wet, neither corrected | opposite pockets cancel at average | partial |
| PZ-14 | avg 1.7 sustained | SEALED_MIST **S1 only** (S2 needs avg>1.9) | enumerator: S2 at 1.7 **WRONG**; fog needs avg>1.8 (not +0.2); S2 = per-pulse most-stressed re-select, not round-robin | S1 single-zone; duty caps + 360-gal ceiling bound | one solenoid, per-pulse most-stressed | partial |
| PZ-15 | south 3.2 → avg 1.8, soft budget exhausted | SEALED_MIST (because **avg 1.8>1.4**, NOT emergency) | `climate_vpd_emergency` bypasses only SOFT budget, **not a seal-mode override**; hard 360-gal absolute | south wetted despite soft-budget; hard ceiling never yields | budget tiered; emergency yields no seal MODE | **GAP** |

### Region: Sensor faults, occupancy, safety rails

| id | inputs | mode / relays | interaction | outcome | conflict-resolution | cov |
|---|---|---|---|---|---|---|
| SF-01 | temp/rh/vpd NaN | SENSOR_FAULT; all OFF | controls.yaml A6 NaN-fallback substitutes finite Tin → in practice fault is via non-finite VPD/RH/hour or implausible value, rarely Tin=NaN | relays held off; hardware thermostat backstop | safety tier 1 | unit (YAML fallback = GAP) |
| SF-02 | temp 999°F | SENSOR_FAULT (NOT SAFETY_COOL); all OFF | plausibility guard (<140) runs BEFORE safety_cool | no spurious cooling | implausible (tier 0) beats safety rail | unit |
| SF-03 | temp −50°F | SENSOR_FAULT (NOT SAFETY_HEAT); all OFF | guard (>−20) before safety_heat | hardware thermostat is freeze protector | implausible beats SAFETY_HEAT | unit |
| SF-04 | hour=24, temp/VPD in-band | SENSOR_FAULT; all OFF | plausibility spans time axis [0,23] | all off until clock recovers | plausibility preempts IDLE | **GAP** (no test sets hour=24) |
| SF-05 | sensor_degraded, 74°F, VPD 1.9 | IDLE (NOT SEALED_MIST) | vpd_control_trusted FALSE → VPD path shut; temp LIVE | humidity floats; timed center bursts allowed | VPD suppressed, temp wins | unit |
| SF-06 | sensor_degraded, 72°F, VPD 0.15 | IDLE (NOT DEHUM_VENT) | dehum_wanted needs vpd_trusted | vent held closed; no blind dehum | VPD-dehum suppressed | unit |
| SF-07 | sensor_degraded, 97°F | SAFETY_COOL; V+F1+F2 (fog per gate, **not trust-gated**) | safety on trusted temp probe; resolve_equipment fog reads in.vpd directly | emergency vent+fans | temp safety wins absolutely | unit (fog nuance partial) |
| SF-08 | sensor_degraded, 43°F | SAFETY_HEAT; H1+H2+lead fan, V closed | safety on temp probe; VPD muted | aggressive recovery | temp safety wins | unit |
| SF-09 | degraded AND temp=inf | SENSOR_FAULT; all OFF | plausibility (no-trust) layers above degradation (partial-trust) | total all-off | plausibility>degradation>normal | unit |
| OCC-01 | occupied, 78°F, VPD 1.9 | IDLE (seal blocked); M/FOG OFF | moisture_blocked_by_occupancy hard-blocks seal | humidity stays high during visit | occupancy beats sub-safety humidify | partial |
| OCC-02 | occupied, 84°F, VPD 1.0 | VENTILATE; **V OPEN**, F1/F2/FOG OFF | enumerator "vent not opened" **WRONG** — vent opens, only fans+fog suppressed | vent opens, no forced airflow, drifts up | occupancy blocks fans/fog, vent still opens | unit |
| OCC-03 | occupied, 96°F | SAFETY_COOL; V+F1+F2 RUN despite occupancy | air_blocked excludes SAFETY_* | full emergency cooling with occupant | safety tier 1 beats occupancy | unit |
| OCC-04 | occupied, running fan in min-on, dawn burst | IDLE/burst-blocked | center burst blocked by occupancy; relay min-on is separate layer | fan completes min runtime then idles | occupancy blocks burst; dwell governs in-flight fan | partial (relay-dwell = GAP) |
| SAF-01 | temp ==95.0 (==safety_max) | SAFETY_COOL; V+F1+F2+FOG | `>=` rail trips at boundary; SAF-6 fog bypass | aggressive cooling, fog latent | temp safety preempts; VPD gates fog only | unit+invariant |
| SAF-02 | temp ==45.0 (==safety_min) | SAFETY_HEAT; H1+H2+lead fan, V closed | `<=` rail trips at boundary | both burners + circulation | temp safety preempts; gas immediate | unit (boundary-exact partial) |
| SAF-03 | temp 94.9 (<safety_max) | VENTILATE (NOT SAFETY_COOL); V+F2+fog if high | one-tenth below rail → normal occupancy/econ-aware path | vigorous cooling, promotes to SAFETY_COOL at 95 | temp leads; occupancy/econ CAN gate (unlike safety) | partial |
| SAF-04 | dispatcher pushes safety_max=40/min=90 (inverted) | IDLE (firmware resets bounds to defaults) | controls.yaml SAFETY SANITY resets to 95/45, VPD to 0.3/3.0 | operates on defaults, no false emergency | firmware sanity beats dispatcher input | **GAP** (YAML, no test) |
| REL-01 | heat re-demand within min_heat_off 180s | HEAT-intent but relay LOCKED OFF | controls.yaml set_relay can_on refuses restart; even SAFETY_HEAT respects min-off | temp dips during lockout, fires after | relay min-off beats non-safety heat demand | **GAP** (YAML) |
| REL-02 | fan+vent in min-on, sensors implausible | SENSOR_FAULT; force-off bypasses min-on | sensor_fault_relay_lock force_off — the one dwell-bypass | all relays drop instantly | SENSOR_FAULT force_off beats min-on | **GAP** (intent unit-covered, bypass = YAML) |
| ECON-01 | stale enthalpy >10min, 80°F, VPD 0.5 | IDLE (dehum suppressed by econ_block) | enthalpy_age>600s → dH NaN → econ_block → DEHUM inhibited | vent stays closed, fails safe | stale-data econ_block beats VPD dehum | partial (stale→econ_block = GAP) |
| ECON-02 | stale outdoor 9999s, 86°F, out 72 cooler | VENTILATE (override_summer_vent FALSE) | outdoor_data_fresh FALSE → no summer-vent credit | cooling via band path, no stale free-cool credit | staleness suppresses summer-vent lever | invariant (#9, not replayed) |
| ECON-03 | 84°F, out 40 (cold guard), VPD 1.2 | VENTILATE with raised entry bar + exit≥3.0°F | outdoor_cold_for_vent → entry margin up, exit hyst forced 3.0 | venting deferred/latched longer, fewer cold dumps | cold-vent guard raises entry bar | partial |
| SAF-05 | SEALED, sealed_timer≥600s, 84°F | **IDLE + mist_backoff** (band-first), NOT THERMAL_RELIEF | enumerator THERMAL_RELIEF + vent purge = **LEGACY only**; production goes IDLE+mist_backoff, no vent purge | seal opens to IDLE, re-suppresses re-entry | band-first de-seals without vent purge mode | unit (production path = GAP) |
| SAF-06 | occupied, 88°F, VPD 1.8 | VENTILATE; **V OPEN**, fans/fog/M OFF | enumerator "everything quiet/no vent" **WRONG** — vent opens, fans+fog suppressed | vent opens, climbs slowly, → SAFETY_COOL at 95 | occupancy blocks fans+fog, vent opens; safety re-asserts at 95 | unit |

### Region: Free-energy / anticipatory windows

| id | inputs | mode / relays | interaction | outcome | conflict-resolution | cov |
|---|---|---|---|---|---|---|
| FE-01 | 72°F, VPD 1.0 in-band, out 38/dp28, night | VENTILATE reason **`temp_high`** (not summer_vent) | in-band VPD → dry_excess 0 → !humidify_ready → summer_vent never arms; cold guard → exit 3°F | free cooling + thermal-mass charge | no conflict; heat suppressed above band | partial |
| FE-02 | 69°F, out 18/dp8, night | VENTILATE briefly → IDLE; **V+both fans** (69>67) | cold guard widens entry to 2.5, exit to 3.0 | short burst trims overshoot, parks IDLE | cold-vent guard asymmetric | partial |
| FE-03 | 60°F, VPD 0.7, out 34, phase 0.1 (day) | CLIMATE_HEAT → mode IDLE + H1 (`heat_stage1`) | phase<2 → not night → heat fires; no "HEAT" mode enum | brief electric S1, solar takes over, gas never | electric H1 before gas | unit |
| FE-04 | 70°F, VPD 1.0, solar 550, phase 0.7 | IDLE; all OFF | both in-band; 70 just clears heat_target+hyst 70 | coasts on solar, zero cost | no demand | unit |
| FE-05 | 86°F, VPD 1.6, out 78/dp48, solar 900 | VENTILATE reason **`vent_mist_assist`** (not summer_vent); **fog OFF** | summer_vent gate TRUE but label only for plain VENT_COOL; dry_excess 0.2≥0.05 → mist-assist; fog needs 0.4 | cools 86→80, mist assist; **fog does NOT fire** | temp leads, VPD as assist | **GAP** |
| FE-06 | 86°F, VPD 1.7, out 84/dp70 muggy | VENTILATE reason vent_mist_assist/temp_high | enumerator "dew_margin negative blocks wetting" **WRONG** — dew_margin = indoor 86−dew 58 = **+28**; fog OFF (0.3<0.4); DEHUM econ-blocked | weak ~2°F cooling, VPD collapses physically | temp forces vent; DEHUM econ-blocked | **GAP** |
| FE-07 | 83°F, VPD 1.5, out 78.1 (indoor−4.9) | VENTILATE; override_summer_vent FALSE (78.1 not<78, fails by 0.1) | strict `<` AND-gate; reason likely vent_mist_assist | vents toward 78; edge near 82 cycle-prone | 5°F prefer-delta is the hysteresis | **GAP** |
| FE-08 | 85°F, VPD 1.6, out 70 attractive but **stale 900s** | VENTILATE; override_summer_vent FORCED off | outdoor_data_fresh FALSE → override FALSE (invariant #9) | vents on actual outdoor, no stale free-energy reliance | staleness failsafe overrides optimization | unit |
| FE-09 | 84°F, VPD 1.2 in-band, **SM anchor temp_high~88** | IDLE (84<88) | firmware phase-follows served band; raised anchor tolerates noon | rides up to elevated target, no wasted cooling | planner pre-resolved by shaping band | **GAP** (anchor-shape needs replay-band) |
| FE-10 | dusk 66°F, **lowered MID anchor** for frost trough | VENTILATE→IDLE (contingent on lowered temp_high<66) | phase 1.9<2 barely not night; lowered band may shed; cold-exit 3°F | pre-cools into lowered band, banks cold mass | planner pre-resolves via lowered anchors | **GAP** |
| FE-11 | 62°F, VPD 0.55, out 30, **phase 3.0 deep night** | IDLE (no heat) | night_econ_heat_suppressed vetoes econ-rescue H1; band heat not needed (62>59.5) | stays IDLE+dry — Vanda night-dry cycle, no fuel | VPD-low would pull econ-heat; night suppression vetoes | unit (cleanest in region) |
| FE-12 | 63°F, VPD 0.5, out 35, phase 0.6 (day) | CLIMATE_HEAT → IDLE + **H1+H2** (S2, `heat_stage2`) | enumerator "HEAT_S1" **WRONG** — 63<temp_low 65 → heat2 latches → gas fires; day → econ-rescue allowed | warms toward band; electric+gas | temp-low + VPD-low align; electric-before-gas | unit (S2 staging = GAP) |
| FE-13 | 82→84°F, VPD 1.2 in-band, out 70/dp45, econ_block=FALSE | VENTILATE reason **`temp_high`** (not summer_vent); V+both fans, **no fog/mist** | enumerator "summer_vent" **WRONG** — in-band VPD → !humidify_ready; VENT_COOL projection still gets +0.08 dewpoint bonus toward vent | pulls 84→78, sheds banked solar for free | no conflict; temp wants vent, VPD neutral | partial |

---

## 4. Coverage GAPS ranked by importance

Ordered by how badly an untested failure would hurt the crop or the controller.

1. **Orchid wet-night fog gating (HOT-08) — `direct_wet_stress_override_enabled` default FALSE.** *Highest stakes for the Vandas.* At night with dry air (VPD 1.9), production silently does NOT fog (the master stress-override switch gates off), while the unit-test world DOES. This is exactly the class of prod/test divergence that produced the firmware-v2 orchid regression (nights RH 39%→84%). No test pins the night fog-suppression in prod defaults. **An untested flip here directly mis-humidifies the orchids overnight.**

2. **Summer cooling saturation at the true extreme (LSE-01/02/10).** The corpus tops out at 100°F (88 SAFETY_COOL rows, 0 rows ≥105°F). The 110°F fog-only cell, the RH-70 monsoon fog-lockout (LSE-02, where the enumerator was *wrong* about fog being off), and the 360-gal daily-volume ceiling cutoff (LSE-10, suppression lives in controls.yaml, outside the C++ harness) are all unexercised. **If fog gating or the volume ceiling misbehaves at 110°F, the house cooks with no test catching it.**

3. **The conflict diagonals — hot+wet sauna and cold+dry night (HOT-05, LSE-07, LSE-11).** Hot+wet (vent imports heat, fog forbidden) is "partial" coverage; the cold+dry winter-night composite (LSE-07) is a full GAP and the enumerator got both the THERMAL_RELIEF path and the "zero heat" claim wrong. These are the cells where the controller is *physically stuck* — the most important to verify it at least fails safe.

4. **Sensor-fault fail-safe edges (SF-04, SF-01 YAML, REL-02 bypass).** `hour=24 → SENSOR_FAULT` (SF-04) has **no test at all**. The controls.yaml NaN-fallback / assume-cold-hot Tin injection (SF-01) and the force-off-bypasses-min-on dwell (REL-02) are YAML-layer and unexercised by `test_greenhouse_logic.cpp`. **A clock/sensor corruption that doesn't trip the fault could actuate on garbage.**

5. **Season sign-flip (LSE-08) and forecast-anchor shaping (FE-09/10).** Architectural — no firmware test can catch a `season=all` mis-anchor or verify a raised SM anchor suppresses venting. The firmware is correct; the *band content* is the risk. Needs `make firmware-replay-band`, not the corpus-fed stock replay (which shows 0 divergence by construction).

6. **Per-zone arbiter & fairness (PZ-03, PZ-07, PZ-09, PZ-11, PZ-15).** Replay sets all zone VPDs = avg (`replay_emit.cpp:214-216`), so the legacy stress scorer, the fairness watchdog, the center-proxy substitution, occupancy-during-divergence, and the budget-emergency bypass are **never exercised**. Lower crop-stakes (single-zone misting) but a real correctness blind spot, with several enumerator errors (no emergency-seal mode, no center sensor to lose).

7. **YAML relay-dwell layer (ET-02, REL-01, OCC-04, SAF-04, ECON-01 stale-derivation).** min_vent_on/min_fan_on holds, min_heat_off short-cycle lockout, inverted-bound sanity reset, stale→econ_block derivation — all controls.yaml, none in the C++ harness. Hardware-protective; failure = short-cycling or chattering, not crop death.

---

## 5. Recommended new tests

Concrete additions to `test_greenhouse_logic.cpp`, `invariants.h`, and the replay corpus, each with the assertion that closes the gap. Ordered to match the gap ranking.

**Gap 1 — orchid wet-night fog (HOT-08).** Add two unit tests:
- `night_dry_fog_blocked_in_prod_defaults`: inputs temp 84, VPD 1.9, phase 3 (night), `direct_wet_stress_override_enabled=false` (prod default). **Assert** mode==VENTILATE, `out.fog==false`, reason=="temp_high" (NOT vent_fog_assist).
- `night_dry_fog_allowed_when_stress_override_enabled`: same inputs, switch ON. **Assert** `out.fog==true`. Together they pin the prod/test divergence that the orchid regression exploited.

**Gap 2 — summer saturation.** Add unit tests + corpus rows:
- `safety_cool_fog_locks_out_at_rh_ceiling`: temp 110, RH 92, dew_margin 4. **Assert** SAFETY_COOL, `out.fog==false` (RH>90 OR dew<8). And `safety_cool_fog_runs_at_rh70`: temp 110, RH 70, dew_margin 15, VPD 2.0. **Assert** `out.fog==true` (corrects the LSE-02 enumerator error).
- Add a ≥105°F corpus row (e.g. 108°F) so `make firmware-invariants` exercises invariant #7 above the current 100°F cap.
- Volume-ceiling test must live at the YAML level (see Gap 7) — add a controls.yaml harness asserting `mister_water_today ≥ 360 → willFog==false` even in SAFETY_COOL.

**Gap 3 — conflict diagonals.** Add:
- `hot_wet_no_fog_rides_to_safety`: temp 86, VPD 0.6, dew_margin 5, outdoor 96/dp78. **Assert** VENTILATE, `out.fog==false`, `out.fan1 && out.fan2` (verifies it doesn't falsely fog the sauna).
- `cold_dry_winter_night_seal_timeout_goes_idle_mist_backoff` (band-first): SEALED with `sealed_timer≥600s`, temp 64. **Assert** mode==IDLE, reason=="mist_backoff", `relief_cycle_count` incremented, and `out.vent==false` (NOT THERMAL_RELIEF, NOT a vent purge — corrects SAF-05/LSE-07 enumerator).
- `winter_night_band_heat_fires_under_env2_suppression`: temp 64 (<67.5), VPD 2.5, night, econ_block. **Assert** `out.heat1==true` (band heat) while econ-rescue path is vetoed — proves "zero heat" claim is wrong.

**Gap 4 — sensor-fault edges.** Add:
- `local_hour_24_triggers_sensor_fault`: temp/VPD in-band, `local_hour=24`. **Assert** mode==SENSOR_FAULT, all relays OFF. (Closes the explicit SF-04 gap — no current test touches the time axis.)
- A YAML-harness test for the NaN-fallback chain (SF-01): all outdoor sources NaN → **assert** Tin is set finite (safety_min−10 or safety_max+10) and trips SAFETY_HEAT/COOL, NOT SENSOR_FAULT.

**Gap 5 — season/forecast anchors.** Add a **`make firmware-replay-band`** row pair (not stock corpus):
- Assert that with SM `temp_high` raised to 88, indoor 84 → IDLE, whereas the same 84 under a default band → VENTILATE (proves the raised anchor suppresses venting, FE-09).
- Add an invariant or band-content check that a `season='all'` anchor set produces setpoints inconsistent with both summer and winter noon (LSE-08) — at minimum a lint/assert that flags `season=all` when seasonal anchor rows exist.

**Gap 6 — per-zone arbiter.** Make replay zone-aware (lift the `replay_emit.cpp:214-216` flatten) for a handful of divergent rows, then:
- `fairness_watchdog_overrides_arbiter`: west age 11min>10min, west VPD>target, center/south fresh. **Assert** `select_zone_for_pulse` returns west (PZ-07).
- `center_uses_house_avg_proxy_no_nan_bench`: feed "vpd_center=NaN" — **assert** center still routes off avg and can win rank-1 (corrects PZ-09 premise).
- `vpd_emergency_bypasses_soft_budget_not_seal_mode`: south 3.2, avg in-band, soft budget exhausted. **Assert** mode stays IDLE (no emergency-seal) but `wet_allowed==true` on the stressed zone (corrects PZ-15).

**Gap 7 — YAML relay-dwell.** Stand up a controls.yaml lambda test harness (currently none) covering: `min_heat_off` short-cycle lockout (REL-01), `force_off` bypassing min-on under SENSOR_FAULT (REL-02), the SAFETY SANITY inverted-bound reset (SAF-04 — assert pushed safety_max=40 resets to 95), and the 360-gal ceiling force-off (LSE-10). Each **asserts** the YAML relay/sanity layer, which the C++ harness structurally cannot reach.

---

**Cases the verifiers flagged as enumerator-wrong (use the corrected value):** HOT-01 (fan2 ON at 83.5), HOT-08 (night fog OFF in prod), HOT-12 (fog fires, staleness only kills label), ET-06 (heat1 fires, not "none"), LSE-02 (fog ON at RH70), LSE-03 (default safety_max=95), LSE-04 (IDLE/temp_high not summer_vent), LSE-07 (IDLE+mist_backoff not THERMAL_RELIEF; band heat can fire), LSE-11 (cold-guard+arbitration not precedence), PZ-02/14 (dwell 60s, S2 delay 300s + avg>1.9), PZ-09 (no center sensor), PZ-11/15 (no emergency-seal mode), SF-01 (fallback yields finite Tin), OCC-02/SAF-06 (vent OPENS under occupancy), SAF-05 (production = IDLE+mist_backoff), FE-05/06/13 (reason is vent_mist_assist/temp_high not summer_vent; fog needs dry_excess≥0.4; dew_margin is indoor-based +28), FE-12 (heat2/gas fires at 63<65, S2 not S1).