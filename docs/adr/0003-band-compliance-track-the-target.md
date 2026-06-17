# ADR 0003 — Band compliance: track the target diurnal curve, do not float for efficiency

- **Status:** Accepted — 2026-06-17. Sets the control objective for the band-compliance reconcile sprint.
- **Owner lane:** verdify-platform (firmware + planner + data + web). **Epic:** band-compliance reconcile (see `docs/reviews/band-compliance-reconcile-sprint-2026-06-17.md`).
- **Refs:** `firmware/lib/greenhouse_logic.h`, `firmware/lib/greenhouse_types.h`, `firmware/lib/greenhouse_solar.h`, `verdify_schemas/tunable_registry.py`, `db/migrations/{146,147,171,178}`, `docs/adr/0002-planner-hermes-vs-direct-gpt5.md`, the 2026-06-17 end-to-end control review.

> Decision record. No secrets. Firmware = software; physical/hardware upgrades are out of scope (tracked separately).

---

## 1. Context

The end-to-end control review (2026-06-17) confirmed the firmware implements a textbook supervisory architecture: a smooth solar-ephemeris **float-envelope** band, an 8-mode FSM, a single-arbitration actuator allocator with strong anti-fight interlocks, and VPD as a constraint (not a fighting loop). The band is a true *do-nothing-inside-`[low,high]`* envelope (`climate_band_error()` returns `0.0` inside the band), and several **cost/energy brakes** actively suppress action:

- `sw_night_econ_heat_suppress_enabled` (default **true**) suppresses econ-heat at night;
- the `cold_dehum_allowed` / `outdoor_cold_for_vent` guard blocks `DEHUM_VENT` on cold nights unless indoor temp has margin;
- `resource_cost` then `relay_churn_cost` are arbitration tiebreakers that prefer cheaper/less-cycling actions.

That design optimizes **energy efficiency** by letting the climate drift inside the band and declining to spend energy to correct it — most visibly, it lets **VPD drift wet at night** (the documented orchid wet-night regression) rather than run the dehumidifier/heat-assist.

The **product goal for this regime is different**: the greenhouse should *visibly track the smooth diurnal target curve* (both temperature and VPD) **24/7**, so the homepage graph shows actual hugging the target line. Float-for-efficiency and the cost-brakes work directly against that goal.

## 2. Decision

**For this control regime the objective is band COMPLIANCE — actual tracks the target curve — not energy minimization.** Specifically:

1. **Narrow the control deadband from envelope toward target-centered.** Keep the wide `low`/`high` as *safety bounds only*; drive the climate toward `target`, not merely inside the band. The optimization target is the band-compliance grade (`fn_zone_band_grade` / `compliance_v2_attributable_pct`), maximized for temperature AND VPD, day and night.
2. **Keep the single-arbitration mode selector — do NOT add dual PID loops.** Tracking tightly is achieved by *narrowing the deadband and minimizing excursions*, NOT by running independent temperature and VPD loops that fight each other. The leading-normalized-axis arbitration stays; it is what prevents the heater-vs-fog / vent-vs-fog fights even under tight tracking. There is still **no PID** — staged hysteresis around a tighter, target-centered band.
3. **Give DEHUM equal standing with heating and cooling.** Remove the cost-driven night brakes (`sw_night_econ_heat_suppress_enabled`, the `cold_dehum_allowed` defensive suppression) and **demote `resource_cost`/`relay_churn_cost` out of the arbitration ordering** so band-error dominates the decision. VPD is tracked as aggressively as temperature.
4. **Cost / energy is explicitly NOT an objective in this regime.** Energy savings (temperature integration, float-for-efficiency, cost-ranked actuator choice) are out of scope and tracked separately. **Temperature integration is explicitly rejected here** — it trades tracking for efficiency, the opposite of the goal.
5. **Bias toward DELETING code.** Realize the above by *removing* the dead/inert tunables and the cost-brake branches, not by adding configuration. Success includes a net-negative line count and fewer mode/edge-case branches.

## 3. Consequences

- **More actuator runtime and cycling** — accepted. Bounded by per-relay min-on/min-off dwell, a single consolidated anti-chatter mechanism, and the non-bypassable water/relay-wear caps (which stay). Relay wear is managed by dwell limits, NOT by biasing the climate decision toward inaction.
- **Higher energy use** — explicitly accepted for this regime.
- **Anti-fight safety preserved.** Heat and active cooling remain mutually exclusive, with exactly ONE sanctioned exception: a named, bounded **heat-assisted dehumidification** state that may co-run heat to raise moisture capacity so the VPD target is reachable on a cold humid night (replacing today's "suppress dehum when cold" defensive branch with an active, limited path).
- **Reversible.** The deadband width and (removed) cost weighting were bounded tunables; a future efficiency regime can re-widen the band. The decision changes the *objective*, not the architecture (supervisory FSM + single-arbitration allocator + staged hysteresis stays).

## 4. Non-goals / explicitly out of scope

- Hardware/physical upgrades (HAF circulation fan, shade, PAR/PPFD or IR-leaf sensors, dehumidifier) — tracked separately.
- Energy/cost optimization, temperature integration, demand-response.
- Reintroducing per-axis PID loops.

## 5. Success criterion

The homepage band-trace shows **actual temperature and VPD tracking the device-truth target curve 24/7**, quantified by the band-compliance grade meeting the bar defined in the sprint design doc (e.g., ≥ target % time-in-tracking for temp and VPD, day and night), with the night VPD wet-drift eliminated. The target the controller follows == the target the graph shows == the crop recipe (single source of truth).

## 6. Amendment — sprint-2 build decisions (2026-06-17, Jason)

The 2026-06-17 Ultracode control-story adversarial audit (`docs/reviews/control-story-adversarial-audit-2026-06-17.md`) found the architecture sound but the band-compliance regime **shipped disarmed** (the WIP added the easy half, the convergent half is undone). Jason resolved every open fork; these decisions are binding for the build sprint and **amend §2–§3** where noted.

1. **Pinch is the default; float is retired.** Convert to the target-tracking pinch (`apply_band_track_pinch`) as the **operating default** — `band_track_fraction` defaults to a **calibrated non-zero** value, fully plumbed (registry `planner_pushable=True` with bounds, ESPHome `number`, `cfg_*` readback, and set in the `controls.yaml` setpts initializer so it is the live device default). The float-envelope (fraction 0) is no longer reachable as a default. Because the control band is narrowed to the target, the **arbitration penalty now sees in-band drift** (`climate_band_error` is non-zero off-target inside the safety band). It stays a **bounded planner knob** (AI may modulate tracking tightness; it may not float to 0).

2. **Kill the saturate-at-1.0 metric.** Replace the flat-inside-band grade (`fn_grade_credit` = 1.0 across the whole ideal band) with a credit that **peaks at the target and declines toward the edges** so in-band drift is visible and penalized; surface the per-axis, half-width-normalized **|actual − target|** deviation as the **headline tracked metric** (median/p95, day+night, both axes); the planner reward tracks it. (Does **not** change the arbitration *numerator* — tracking is realized by the pinch per §2.2, not by a `|actual−target|` arbitration term.)

3. **Serve the target as one source, and cascade it to the homepage.** `fn_band_setpoints` gains `temp_target`/`vpd_target` rows (from `fn_crop_band_value`); the lower-quartile/midpoint derivations on the graph surface are retired; the homepage plots the **device-published** target line; the mig-178 `v_band_device_divergence` view is extended to audit the **target**, not just the edges. One served target, provably equal to the device's.

4. **Dehumidification is a deterministic, outdoor-aware, bidirectional moisture-exchange selector** — **supersedes §3 "exactly ONE fixed heat-assisted-dehum state."** Each cycle, from indoor and outdoor temp+RH (→ absolute humidity / dewpoint), the controller deterministically estimates the projected VPD-toward-target gain of (a) **venting** (exchange toward outdoor absolute humidity) and (b) **heating** (raise saturation capacity → lower RH → raise VPD), and selects the more effective action. **Heat and vent MAY co-run** when the estimate proves both move VPD toward target (bounded: heat1 only with the vent, never heat2; min-dwell; dew-margin veto). It is **bidirectional**: it also handles the **humid-outside / dry-inside** case — when VPD is too high (too dry) and outdoor absolute humidity exceeds indoor, **import moisture by venting** instead of running the foggers. This replaces both the `cold_dehum_allowed` brake and BC-5's unconditional co-run with one model-driven path; it is the sanctioned heat+air-exchange exception, now estimator-gated rather than a fixed state.

5. **Escalation is symmetric across actuators — physics- and cycle-time-sized.** Every two-stage actuator (heat1→heat2, fan1→fan2, and the wetting ladder) uses **one shared two-stage mechanism**: a stage-2 entry margin **plus a matching de-escalation hysteresis and latch**, so stage 2 cannot chatter. The gaps are sized for the actuator's physical response and its min-on/min-off cycle floor (no whipsaw). Removes today's asymmetry (heat2 latches, fan2 was a bare threshold) and the inert legacy `dH2` geometry.

6. **Wetting escalates fog → misters (lowest-GPM-first).** Humidification leads with **fog** (AquaFog ≈ 0.26 GPM — the gentlest, lowest-water stage) and escalates **up to the misters** (≈ 1 GPM/zone) when fog can't hold the VPD target — inverting today's misters→fog ladder, ordered by **water GPM**. **Misters retain their zone-targeting and fertigation duties** (the zone arbiter stays); only the *climate humidification* escalation order changes.

7. **One controller, net-negative LOC.** Make `sw_fsm_controller_enabled` non-optional (band-first only); delete the legacy cascade, the prod-unreachable `THERMAL_RELIEF` mode, the disabled dwell-gate path, the superseded `night_econ_heat_suppressed`/`cold_dehum_allowed` brakes, the inert `bias_heat`/`bias_cool` and the `direct_wet_stress_*`/`fog_stress_*` no-op shims; move the heat↔air interlock into one pure place. Each firmware deletion gated on `make firmware-replay-band` = 0 against the band-first baseline. The sprint must end **net-negative LOC**.

**Delivery target:** all non-device (DB/web/registry/planner) changes landed on `main` through CI/CD and deployed; firmware changes implemented and proven offline (`make test-firmware`, `firmware-invariants`, `firmware-replay-band`, `firmware-check`), **staged and ready for OTA on Jason's command** (OTA stays the human gate per the freeze rules).
