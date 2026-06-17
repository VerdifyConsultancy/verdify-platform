# Control-Story Adversarial Audit — band-compliance vs the core user story

- **Date:** 2026-06-17
- **Branch audited:** `band-compliance-fw` (the WIP "what we were just working with") vs `main`
- **Method:** Ultracode fan-out adversarial audit — 7 spec dimensions, 35 findings, each verified by two independent adversarial lenses (code-accuracy + user-intent). 80 subagents.
- **Outcome:** 35/35 findings survived verification; several severities corrected down and a few framings refuted (see §6). 3 new issues + 5 augmentations to the existing BC set (#360–#371).
- **Refs:** `docs/adr/0003-band-compliance-track-the-target.md`, `docs/reviews/band-compliance-reconcile-sprint-2026-06-17.md`, `docs/firmware-fsm-spec.md`, epic #359.

> Scope: software-only control audit (firmware = software). No secrets. Code is the source of truth; where this doc and code disagree, the code wins.

---

# Closing the Gap to "Exactly That Implementation": A Band-Compliance Control Audit

## 1. TL;DR

The greenhouse controller is **architecturally close** to Jason's clean model — one on-chip diurnal curve as the setpoint, one supervisory 8-mode FSM, one single-arbitration actuator allocator, staged hysteresis — but it is **behaviorally not yet that model**, and the WIP `band-compliance-fw` branch did the safe, additive half of the work while leaving the convergent half undone. The single biggest gap: the controller does **not** "always strive toward the setpoint." It floats inside the wide band — `climate_band_error()` returns `0.0` anywhere in `[low,high]` (`firmware/lib/greenhouse_logic.h:468-472`), and the one mechanism built to fix that, BC-3's `apply_band_track_pinch()`, ships with `band_track_fraction` defaulted to 0 (`greenhouse_types.h:690`), value-initialized to 0 on the device (omitted from the `controls.yaml:510-587` setpts initializer), and **wired to nothing** (absent from `tunable_registry.py`, `tunables.yaml`, the dispatcher) — so the OTA binary's tracking behavior is byte-identical to `main`. The 3-5 things between here and "exactly that":

1. **Arm band-tracking** (BC-3): make target-pinch the default behavior, not a dormant unreachable knob.
2. **A decision from Jason on heat+vent**: BC-5 deliberately adds a heat1+open-vent state (DEHUM_VENT) that contradicts the literal "never heat and vent" rule but implements the single ADR0003 §3 exception — he must bless it or revert it.
3. **Fix the fan1→fan2 escalation asymmetry**: heat2 has a real two-sided latch, fan2 is a bare one-sided threshold with no de-escalation hysteresis (`greenhouse_logic.h:2096`).
4. **Decide whether delta-to-target is the penalty/metric**: it is graphed (a secondary panel) but the arbitration penalty and the headline reward are band-EDGE / graded-compliance, not `|actual−target|`.
5. **Execute the deletions** (BC-2/4/8/10/11): the WIP is net **+240 LOC** against an explicit net-negative success criterion (ADR0003 §5); the legacy cascade, cold-brakes, dead tunables, and dwell-gate dead path all still ship.

---

## 2. Intended design (the user story)

Reference design for this audit. Each clause is testable against firmware/DB/planner code as **aligned** or **diverges**.

- **C1 — The setpoint IS the diurnal band curve.** `temp_target`/`vpd_target` are the smooth on-chip curve from solar ephemeris + crop anchors (`band_value_at_phase()` over `BandAnchors`, keyed on `solar_phase`), identical across firmware, `ingestor/solar.py`, and `fn_crop_band_value` — not a static number, not wall-clock-driven.
- **C2 — Always strive toward the target, in every state.** Across all 8 FSM states the controller drives temp and VPD toward the curve's `target`, bounded only by a hysteresis deadband around that target. The wide `low`/`high` edges and `safety_min/max`/`vpd_*_safe` are *safety bounds*, not the control objective. "Do nothing inside the wide envelope" is a divergence.
- **C3 — Tracking is hysteresis-bounded, not chatter.** Anti-chatter is hysteresis (entry≠exit) + per-relay min-on/min-off dwell, NOT declining to act.
- **C4 — No physically incoherent actuator combinations.** Heat and active cooling (vent/fans-for-cooling) are mutually exclusive; fans never run against a shut vent — except explicitly named, bounded carve-outs (SAFETY_HEAT recirculation, VENT-BYPASS, and the one sanctioned heat-assisted-dehum state).
- **C5 — Two-phase heating escalation gated by a SECOND, tunable hysteresis.** heat1→heat2 across a second threshold distinct from phase-1, tunable, with symmetric de-escalation.
- **C6 — Two-phase cooling escalation gated by a SECOND, tunable hysteresis.** fan1→fan2 across a second threshold above the lead-fan point, tunable via `cool_stage2_over_high_f`, with symmetric de-escalation.
- **C7 — Center-mist ↔ fog is one ladder with a deliberate flow ordering.** MIST_S1 → MIST_S2 → MIST_FOG with no level-skipping, S2→FOG gated by `fog_escalation_kpa` above `vpd_high`; the ordering reflects an explicit, recorded decision about device flow (GPM).
- **C8 — The tracked, penalized, graphed metric is delta-from-target.** Objective and homepage graph are the signed deviation of actual temp OR VPD from the target curve, day and night, both axes; cost/energy/relay-churn is NOT an objective and not a tiebreak in `climate_candidate_precedes`.
- **C9 — VPD has equal standing with temperature.** VPD tracked as aggressively as temp; axis arbitration by band-normalized error (furthest-from-target leads), never by which is cheaper to correct.
- **C10 — Radical simplicity.** One band curve, one FSM, one arbiter, staged hysteresis — no PID, no dual loops, no per-axis integration, no clock/taper/cost special-cases. New behavior comes from *removing* branches/tunables; net complexity trends down.

---

## 3. Dimension-by-dimension: intent vs implementation

### Dimension 1 — Setpoint == the calculated curve (C1) — **PARTIAL**

The data/telemetry half is genuinely realized: one on-chip harmonic over the `house` `crop_band_anchors`; the device computes `temp_target`/`vpd_target` each cycle and publishes them as device-truth (`controls.yaml:585-586` set the struct, `:1695-1697` publish `gh_house_temp_target`/`gh_house_vpd_target`), and migration 171 realigns `fn_band_setpoints` to the same harmonic.

**But the control half diverges (P0, both branches).** The production FSM is forced into the band-first path — `controls.yaml:590` unconditionally sets `sw_fsm_controller_enabled = true`, so `determine_mode` always delegates to `determine_mode_band_first` (`greenhouse_logic.h:1443-1444`). That path keys demand off band EDGES: `needs_cooling = temp_f > temp_high(±hyst)` (`:1178-1180`), `needs_heating_s1 = temp_f < band_heat_target_f + heat_hysteresis` where `band_heat_target_f = temp_low + 0.25·width` (`:445-448`), VPD only at `vpd_high`/`vpd_low−HV` (`:1169,1187`). Scoring uses `climate_band_error()`, which **returns 0.0 anywhere inside `[low,high]`** (`:468-472`), and even the normalized arbitration tiebreak divides those edge-errors by half-width (`:709-716`) — so the tiebreak is *also* zero inside the band. `temp_target`/`vpd_target` are read **only** for telemetry deltas and never influence mode/relay selection. This is exactly the "do-nothing-inside-the-envelope" behavior ADR0003 §1 names as the thing to remove.

**Sub-finding (P1):** Served-vs-device target divergence is **unmonitored**. `fn_band_setpoints` returns only the 4 edges (verified: `db/migrations/171:45` `RETURNS TABLE(temp_low, temp_high, vpd_low, vpd_high)`), no target row. The mig-178 divergence view (`v_band_device_divergence`) joins only on edges (`178:23-26`), so target-level drift is invisible. The homepage graph derives the target line through a *different* path (`v_band_curve` / `band_anchors.py`) than the device target. There is also a documented phase-engine skew (DB noon = midpoint(SR,SS) via bisection vs firmware NOAA ephemeris noon; year-angle `g=2π/365·(doy−1)` at `solar.h:47` vs `gamma=2π/days·(doy−1+0.5)` at `solar.py:70`) — bounded and disclosed (±2–5 min, sub-degree, smallest at the noon peak where `dValue/dphase→0`), so this is correctness/observability debt, not an active control failure.

### Dimension 2 — Always strive toward the target (C2/C3) — **DIVERGES**

The WIP built the right mechanism: `apply_band_track_pinch()` (`greenhouse_logic.h:420-429`) narrows the control band toward `temp_target`/`vpd_target` by `band_track_fraction` and is applied once at `determine_mode` (`:1442`) and `resolve_equipment` (`:2025`) — so when armed it flows uniformly through every non-safety mode. **But it ships behavior-neutral and unreachable (P0, wip_branch):**

- Early-return `if (f <= 0.0f) return sp;` (`:422`); default `band_track_fraction = 0.0f` (`greenhouse_types.h:690`).
- **Omitted from the `controls.yaml:510-587` setpts aggregate initializer** (last field set is `.vpd_target` at line 586), so C++ value-initializes it to 0.0f on the device regardless of `default_setpoints()`.
- **Zero references** outside `firmware/lib` + tests + the firmware-twin mirror (verified: `grep -rnE band_track_fraction verdify_schemas/ ingestor/ mcp/ db/ firmware/greenhouse/` returns nothing). Not an ESPHome number, not in `tunable_registry.py`, not in the dispatcher's `PLANNER_PUSHABLE_REG` — so neither the planner nor a manual `set_tunable` can ever raise it.

At fraction 0, only the **heat axis** partially strives toward an interior point (`band_heat_target_f` = 25%-into-band, `:445-448`); cooling/dehum/humidify float fully to the edges. The intent ("always striving across all states, 24/7") is unmet on every axis except partial heat. The code comments (`greenhouse_logic.h:413-419`, `types.h:236-243`) advertise it as "Planner-pushable; ramp live," and the authoritative `docs/firmware-fsm-spec.md` was **not updated** — it has zero mention of the pinch, the float-envelope, or the default-off state, so a future session orienting from the spec cannot discover that striving is built-but-disarmed.

> **Headline tension (a): `band_track_fraction` default 0 = still the float-envelope, NOT "always striving."** The whole sprint exists to deliver target-tracking; the OTA binary delivers none of it and has no live path to arm it.

### Dimension 3 — Delta-to-target as penalty AND graphed metric (C8/C9) — **PARTIAL**

- **Penalty (arbitration):** delta-to-**EDGE**, not target. `climate_band_error` is 0 inside the band; `evaluate_climate_decision` builds every candidate's projected error from it (`:608-609`) and normalizes by half-width (`:709-716`); `climate_candidate_precedes` orders on that (`greenhouse_types.h:471-477`). A reading at `temp_low` (well off-target) and one on target both score 0. **This is the deliberately-chosen ADR0003 §2.1/§2.2 design** (track-the-target via deadband narrowing, NOT a `|actual−target|` arbitration term), so the "penalty should be `|actual−target|`" framing is a *rejected alternative*, not a defect — the legitimate gap is that the pinch that *would* realize it ships at 0.
- **Headline metric / planner reward:** the graded compliance credit `fn_grade_credit` (1.0 across the *entire* ideal band, linear shoulder, 0 beyond — `db/migrations/146:62-72`), and the planner reward is `compliance_v2_attributable_pct` (`mig 147:311,347`). This **saturates at 1.0** across the band interior, so it is structurally blind to off-target-but-in-band drift — the very tracking the regime is meant to optimize. **BC-4 correctly demoted** `resource_cost`/`relay_churn_cost` out of the tiebreak (`greenhouse_types.h:486-492`), so cost is no longer a driver.
- **Graph:** a normalized `|actual−target|/half-width` panel **DOES exist** on the homepage — "Climate Delta & State Machine (normalized)" (`grafana/dashboards/site-home.json:6338`) — and the band panels overlay `temp_target`/`vpd_target` (`:2198,3378`). But it is a *secondary* panel (gridPos y=126, last in the array), and it graphs a *different quantity* than the controller penalizes (target-error vs edge-error). BC-12 (the planned headline live tracking-score + actual-vs-device-target) is **not built** on either branch.

> **Headline tension (d): delta-to-target is graphed (secondarily) but is NOT the arbitration penalty and NOT the headline reward.** The reward is the saturating grade — an ADR-ratified choice that contradicts the literal user story. This is a *ratify-or-redefine the success metric* decision, not a silent bug.

### Dimension 4 — Never heat and vent (C4) — **PARTIAL → DECISION**

On `main`: heat and active air-exchange are fully mutually exclusive with one pre-existing aligned carve-out — SAFETY_HEAT runs a recirculation fan with the **vent CLOSED** (`greenhouse_logic.h:2063-2072`; `fan_requires_open_vent()` excludes it, `:2224-2231`). All other modes verified clean: SAFETY_COOL, THERMAL_RELIEF, VENTILATE, SEALED_MIST, IDLE never co-run heat with vent or cooling fans.

**The WIP (BC-5) deliberately ADDS a second heat+vent state (P0 decision_needed, wip_branch):** DEHUM_VENT now sets `out.vent = true` (`greenhouse_logic.h:2117`) AND `out.heat1 = true` when `needs_heating_s1` (`:2126`) AND a fan (`:2132-2136`), and `controls.yaml:712` exempts DEHUM_VENT from the heat↔air interlock. Net effect on a cold humid night: **heater + open vent + fan simultaneously.** This is correctly bounded (heat1 only, never heat2 — verified at `:2126` and invariant #27's `!eq_heat2` clause `invariants.h:484`) and is precisely the ONE exception ADR0003 §3 sanctions (raise moisture capacity so the VPD target is reachable, replacing the old "suppress dehum when cold" brake). It is *dehumidification cooperation* (DEHUM_VENT is selected on VPD below band, not cooling), so it does not violate the narrower "heat vs active *cooling*" rule — but it does violate the user's *literal* "never heat and vent, full stop."

A new offline invariant #27 (`invariants.h:472-488`, wired into the replay Runner at `:839`) codifies the boundary and is *stricter* than the on-device interlock (it fails on DEHUM_VENT+heat2). But the authoritative `docs/firmware-fsm-spec.md` was **NOT updated** — it still lists DEHUM_VENT heat = "off/off" (`:112`) and "No heat while venting" (`:294`), so the SoT doc now contradicts the shipped code (P1/P2: contradiction, but human-gated OTA means it cannot mislead silently if fixed in the same PR).

> **Headline tension (b): BC-5 heat+vent in DEHUM_VENT is a DECISION Jason must make.** It is the deliberate ADR0003 §3 exception, not an accident — but it crosses the literal rule he stated.

### Dimension 5 — Escalation = a second, tunable hysteresis (C5/C6) — **PARTIAL**

- **heat1→heat2:** a real two-sided latch — set at `temp_below_band` and cleared at `temp_f >= heat_target` in the production band-first path (`greenhouse_logic.h:1193-1197`). **But the production geometry differs from both the legacy path and the spec (P1, contradicts):** legacy latches at `temp_f < Tlow − dH2` / clears at `Tlow + heat_hysteresis` (`:1530-1533`), and `docs/firmware-fsm-spec.md:263,283` documents *only* the legacy `dH2` geometry. The band-first escalation gap is the **fixed** `BAND_HEAT_TARGET_FRACTION = 0.25` span (`:435`), NOT `dH2` and **not planner-tunable** — `d_heat_stage_2` is `planner_pushable=False`, "Retired from live band-first control" (`tunable_registry.py:148-151`). So the second hysteresis the operator believes is tunable (dH2=5°F) is **inert in production**, and a stale device log line still prints `S2_thr = Tlow − dH2` (`controls.yaml:2110`).
- **fan1→fan2:** a **bare one-sided threshold** — `needs_both = temp_f > (Thigh + cool_stage2_over_high_f)` (`greenhouse_logic.h:2096`), recomputed each tick with **no latch and no de-escalation hysteresis**. The entry gap *is* independently planner-tunable (`cool_stage2_over_high_f`, `tunable_registry.py:172-185`), satisfying the literal "can be tuned." But there is **no symmetric de-escalation gap** — fan2 drops the instant temp falls below the same threshold, bounded only by the relay min-on/min-off timer (120s/90s, `globals.yaml:1072-1079`), a time dwell not a temperature hysteresis. `cool_exit_hysteresis_f` (`greenhouse_types.h:162`) is the VENTILATE *mode* exit, not the fan1↔fan2 stage transition.
- **mist S2↔FOG:** entry at `vpd > vpd_high + fog_escalation_kpa` (`:1379`), exit at the *same* value `vpd <= vpd_high + fog_escalation_kpa` (`:1389`) — same bare-threshold shape as fan2 (and likewise documented "threshold only" at spec `:265`). The S1↔S2 edge, by contrast, is timer-dwell + VPD-hysteresis. So the mist ladder is a hybrid, *consistent with fan2* but not with the user's clean symmetric concept.

> **Headline tension (c): the escalation design is asymmetric.** heat2 has a latch + anti-chatter; fan2 and fog have bare thresholds with no de-escalation hysteresis. The user described ONE symmetric concept for both. (Note: this overlaps the project's own tracked consolidation item, ADR0003 §3 "a single consolidated anti-chatter mechanism" / BC-8.)

### Dimension 6 — Center-mist ↔ fog ordered by flow (C7) — **PARTIAL**

The ladder is unambiguously misters-first then fog (MIST_WATCH→S1→S2→FOG, `greenhouse_logic.h:1366-1402`; fog only at `mist_stage==MIST_FOG`, `:2077-2078`), and it is a fixed enum progression (`greenhouse_types.h:31`), not a data-driven flow arbiter. The order **is** documented — but on an electrical-power/intensity axis ("~800W heavy artillery," `controls.yaml:1199-1202`) and on humidification effectiveness (`iris_planner.py:595-623`), **not** on the GPM axis Jason asked about.

**Correction to the audit's own framing:** the GPM figures **do exist** (verified: `docs/planner/greenhouse-reference.md:33` AquaFog "Max 15.8 GPH (0.26 GPM)"; `:68` mister "1 GPM per zone"). So fog is the **lower water-flow** device (0.26 GPM) and the center mister is **higher** (~1 GPM) — meaning the ladder escalates **high-GPM-water-first → low-GPM-water-last**, with fog being the lower-water but higher-electrical-power device. The true gap (P1→P2, missing): the owner's literal "which is lower GPM" decision and its linkage to the escalation order is **unrecorded** in any decision doc. There is also no per-device GPM telemetry — the single DAE AS200U-75P meter is a whole-supply aggregate (`sensors.yaml:156`, "Measures ALL water downstream") and fog flow **is** accumulated into `mister_water_today` through it (`controls.yaml:1561-1565`), so a per-device split must come from spec, not live data.

> **Headline tension (e): the center↔fog ordering rationale-of-record is energy/intensity, not GPM.** The GPM data exists and contradicts a naive "low-GPM-first" assumption (fog is lower water flow), so the ordering needs an explicit recorded decision tying it to whichever axis Jason intends.

### Dimension 7 — "The simplicity of it all" (C10) — **DIVERGES**

ADR0003 §5 and the reconcile sprint §3 mandate a **net-negative-LOC** DELETE/collapse program. The WIP is **net +240 LOC** (277 ins / 37 del; verified) and executed only the additive half:

- **Dead second controller still ships (P1/P2, both):** production forces band-first, so the entire legacy cascade (`greenhouse_logic.h:~1447-1915`, ~468 lines) and **THERMAL_RELIEF** (only assignable in the legacy else-branch at `:1647,1668`; absent from the `ClimateAction` enum and `climate_action_to_mode`, `:768-787`) are **prod-unreachable**. BC-11's "collapse if replay-diff=0" is untouched. (Note: the legacy path is still the *test-default* via `sw_fsm_controller_enabled=false`, which is why BC-11 gates deletion on a clean replay rather than a blind rip-out.)
- **BC-4 only half-done (P1, both):** cost-tiebreak removal landed (`greenhouse_types.h:486-492`), but `cold_dehum_allowed`/`outdoor_cold_for_vent` brake is **retained** (`:643-651,1185-1188,1278`) and `night_econ_heat_suppressed()`+`sw_night_econ_heat_suppress_enabled` are **retained** (`:135-137,2158`; default true). ADR0003 §2.3 says delete both. The WIP comment at `:2118-2119` even claims "with the cold-dehum brake removed (BC-4), DEHUM_VENT now runs on cold nights" — but the brake is NOT removed and still gates `dehum_wanted`, so on extreme-cold nights DEHUM_VENT may never be selected, partially mooting the new heat-assist in its target scenario.
- **Dwell-gate dead path (P2, both):** `sw_dwell_gate_enabled` default false (`greenhouse_types.h:641`), implemented **twice** (band-first `:1338-1359`, legacy `:1812-1849`) and disabled in both. BC-8 ("pick ONE mechanism, delete the other") not executed; WIP only added a comment.
- **Retired-inert tunables still present (P2, both):** `bias_heat`/`bias_cool` (active only in the dead legacy cascade), `direct_wet_stress_*`/`fog_stress_*` no-op shims (`:148-170` return false/true stubs). BC-10/BC-2 purge untouched.
- **BC-11 interlock not consolidated (P2, both):** the heat↔air rule still lives in the `controls.yaml:711-724` runtime lambda (which reads physical relay/dwell state — so it cannot trivially move into pure `resolve_equipment`), not in one pure place. Only invariant #27 was added.

> **Headline tension (f): the implementation carries substantial complexity beyond the clean model** — a whole second controller, a prod-unreachable mode, two cost-brakes the sprint said to delete, a duplicated dead dwell gate, dead tunables. The WIP moved LOC the wrong way; the simplification subset (BC-2/4/8/10/11) is entirely outstanding.

---

## 4. The path to "exactly that implementation"

Ordered to land the *behavioral* objective first (with active replacements in place), then the deletions — net-negative-LOC bias per ADR0003 §5. Each firmware step gated by `make firmware-replay-band OLD=main` (band-curve changes are stock-replay-blind per CLAUDE.md verification step 4) + `make firmware-invariants` + `make test-firmware`; OTA on Jason's gate.

**Phase A — Make striving the default behavior (the headline P0)** — *maps to BC-3 #362, partially new work*
1. Decide the BC-3 destination: either (a) make a calibrated non-zero `band_track_fraction` the **default** (set it in the `controls.yaml:586` setpts initializer AND `default_setpoints()`), or (b) delete the fraction parameter and pinch to a fixed tracking half-band unconditionally. The user's "one default behavior" favors (b); ADR §2.5 "delete, don't add config" also favors (b).
2. If a ramp knob is kept: plumb it fully — `tunable_registry.py` TunableDef (`planner_pushable=True`, bounds [0,1]), an ESPHome `number` in `tunables.yaml`, and a `cfg_band_track_fraction` readback (firmware freeze rule #6). Add it to the `controls.yaml:586` setpts initializer.
3. Prove the curve-derived behavioral diff with `make firmware-replay-band`; add an invariant asserting actual converges toward target.
4. Update `docs/firmware-fsm-spec.md` to document `climate_band_error` as a do-nothing envelope, the pinch mechanism, and that the controls.yaml initializer (not `default_setpoints()`) is the authoritative device default.

**Phase B — Resolve the heat+vent decision (gate)** — *maps to BC-5 #364*
5. **Gated decision (see §5).** If approved: keep BC-5, update `docs/firmware-fsm-spec.md:112,294,119-121` to name DEHUM_VENT as the second sanctioned heat+air-exchange exception (cite ADR0003 §3, invariant #27), and add #27 to the spec's invariant inventory (`:213,391,402`). If rejected: revert `controls.yaml:712` to SAFETY_HEAT-only and remove the `out.heat1` assign at `greenhouse_logic.h:2126`.

**Phase C — Symmetric escalation hysteresis** — *NEW work (no BC issue), plus BC-8 #367*
6. Add a `fan2_latched` to ControlState mirroring the heat2 latch geometry: set at `temp_f > Thigh + cool_stage2_over_high_f`, clear at `temp_f <= Thigh + cool_stage2_over_high_f − cool_stage2_exit_hysteresis_f`. Add the new `cool_stage2_exit_hysteresis_f` tunable (default ~1.0°F) through `Setpoints`/`default_setpoints()`/`validate_setpoints()`/`tunables.yaml`/registry + cfg readback. Add a fan2 no-toggle-within-band invariant.
7. Reconcile the heat2 escalation geometry: decide one intended shape across band-first, legacy, and the spec. If heat1→heat2 should be planner-tunable, reintroduce a band-relative tunable into the band-first latch; fix the stale `controls.yaml:2110` `S2_thr` log and `docs/firmware-fsm-spec.md:263,283`.
8. Execute BC-8: pick ONE anti-chatter mechanism (hysteresis vs dwell gate), delete the other path and its duplicated block.

**Phase D — Metric reconciliation** — *maps to BC-12 #371*
9. Decide (see §5): either make the existing normalized `|actual−target|` panel THE headline and the planner reward, or ratify the graded grade as the objective. Add the sprint §4 deviation-from-target rollup (median/p95 `|actual−target|/half-width` per axis, day/night) as a first-class column from `v_band_curve`. Add `temp_target`/`vpd_target` to `fn_band_setpoints` and extend `v_band_device_divergence` (mig 178) to cover the target, so there is one served-target SoT and the graph is gated against the device truth.

**Phase E — The deletions (net-negative LOC)** — *maps to BC-2 #361, BC-4 #363, BC-10 #369, BC-11 #370*
10. Finish BC-4: replace the `cold_dehum_allowed` suppression with the now-existing BC-5 heat-assist, delete the brake + `night_econ_heat_suppressed()`/`sw_night_econ_heat_suppress_enabled`, re-derive invariant #14 against the new behavior.
11. BC-11/THERMAL_RELIEF: confirm replay-diff=0 on band-first, delete the legacy cascade (`:1447-1915`), make `sw_fsm_controller_enabled` non-optional (remove the field + both code paths), delete THERMAL_RELIEF + its timers. Move the heat↔air interlock into one pure place (threading physical relay state into it) guarded by #27; collapse the fog-assist 4-way split if replay stays clean.
12. BC-10/BC-2: delete `bias_heat`/`bias_cool` (inert once the legacy cascade is gone), the `direct_wet_stress_*`/`fog_stress_*` no-op shims + their registry TunableDefs, and the redundant HA TUNABLE CONSTRAINTS block.

**Convergence target:** one band-first controller (~one `determine_mode` body), 7 modes (THERMAL_RELIEF gone), mutual exclusion in one pure place, band-pinch-to-target as the *default* behavior, symmetric escalation hysteresis on both stages, delta-from-target as the headline metric — at strongly net-negative LOC.

---

## 5. Decisions Jason must make

1. **Heat + vent in DEHUM_VENT (foremost, blocks OTA).** BC-5 introduces ONE new state that co-runs stage-1 heat with the vent open on cold humid nights to make the VPD target reachable — the deliberate ADR0003 §3 exception, correctly bounded (heat1 only, never heat2; `greenhouse_logic.h:2126`, invariant #27). Your literal rule is "never heat and vent, full stop." **Approve the exception** (it is dehumidification cooperation, not the heat-vs-cool fight you called stupid), or **keep strict exclusivity** (revert BC-5, accept that VPD floats wet on cold nights — the orchid wet-night risk).

2. **The success metric: delta-from-target vs graded compliance.** The planner reward and headline KPI today are the saturating band-compliance grade (`fn_grade_credit` = 1.0 across the whole ideal band), an ADR0003 §2.1-ratified choice — but it cannot "see" off-target-but-in-band drift, and your story says `|actual−target|` is what we penalize and graph. **Make the normalized delta the headline + reward**, or **ratify the grade as the objective** and keep delta as the secondary diagnostic.

3. **Center↔fog escalation ordering basis.** The ladder is misters→fog, justified in code by electrical power/intensity. The GPM data shows fog is the *lower* water-flow device (0.26 GPM vs ~1 GPM/zone). **Confirm the intended ordering axis** (water GPM, electrical power, or humidification effectiveness) and we record it as an explicit decision; if it must be literally low-GPM-first by water, the current order would invert.

4. **BC-3 destination — knob vs default.** Should target-tracking be the *unconditional default* (delete the `band_track_fraction` parameter, fixed tracking half-band — most aligned with "the simplicity of it all"), or a planner-pushable ramp knob (keep the fraction, plumb it fully)? This is the difference between "always striving" being the binary's behavior vs an opt-in.

---

## 6. What the audit could NOT confirm / refuted findings

Honesty section — findings that one or both adversarial lenses refuted or materially corrected:

- **"Arbitration penalty should be `|actual−target|`" — REFUTED on intent.** The edge-error arbitration is the *intended* ADR0003 §2.1/§2.2 design (track-the-target via deadband narrowing, NOT a target-error arbitration term, which was an explicitly-rejected alternative). The headline graded metric *mirrors* the zero-inside-band penalty, so they are consistent, not divergent. The legitimate open issue is narrower: the pinch that realizes tracking ships at 0.
- **SAFETY_HEAT fan and the "all other modes clean" verification — REFUTED as divergences (correctly so).** These are confirmed *aligned* — recirculation with the vent closed exchanges no outside air; they were flagged only to document the boundary holds.
- **Invariant #27 "encodes an un-blessed exception" — REFUTED.** The DEHUM_VENT heat-assist is recorded as *Accepted* in ADR0003 §3 (2026-06-17) and BC-5; #27 tracks an approved decision and is *stricter* than the on-device interlock (it catches DEHUM_VENT+heat2). It enforces the intent rather than diverging from it.
- **"No per-device GPM instrumentation / fog flow invisible to accounting" — REFUTED on the evidence.** The single meter is whole-supply (`sensors.yaml:156`), and fog flow **is** accumulated into `mister_water_today` (`controls.yaml:1561-1565`). The valid residual is only that one shared meter cannot yield a per-device GPM split.
- **BC-11 "two-layer interlock = tech debt to delete" — partially REFUTED on intent.** The mutual-exclusion rule *exists* and is enforced every tick (`controls.yaml:721-724`); the lambda is load-bearing because it reads physical relay/dwell state a pure function cannot see. "One pure place" is the sprint's refactor goal, not a user-story requirement.
- **Severity calibration disputes (not refutations).** Several findings drew P0 from one lens and P2 from the other — notably the GPM-ordering gap (data exists → P2 doc gap, not P1), the device-vs-DB noon divergence (bounded/disclosed sub-degree → P2, not P1), the fan2 chatter (bounded by the 210s relay floor → P1/P2, not P0), and the net-positive-LOC finding (the documented "add replacements first" phase of a sprint whose deletions are the P2 tail → informational, not a P1 contradiction). The headline behavioral P0 — `band_track_fraction` shipping inert and unreachable — was confirmed P0 by **both** lenses and stands.
- **Minor citation imprecisions found during verification (not load-bearing):** the apply_band_track_pinch call sites are `:1442`/`:2025` (not the cited ranges); the served target also diverges *structurally* (graph `fn_band_timeline` uses lower-quartile `temp_low + 0.25·width`, legacy fallback uses midpoint, device uses its own anchor harmonic) — sharper than "phase-engine skew only"; and the firmware↔python skew is the year-angle term, not the hour-angle algebra (those are algebraically identical).
