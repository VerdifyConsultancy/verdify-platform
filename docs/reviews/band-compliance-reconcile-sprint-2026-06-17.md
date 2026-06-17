# Band-Compliance Reconcile — sprint design & plan (2026-06-17)

**North star:** the greenhouse tracks the smooth diurnal target curve — **temperature AND VPD** — aligned to an optimized sun state and the crop target points, **24/7**, so the homepage band-trace shows actual hugging the target line. **Software-only** (firmware is software; physical/hardware upgrades are out, tracked separately). **First-principles bias: delete code, collapse edge cases — fewer tight lines.** Cost/energy is explicitly NOT an objective this regime; **dehum gets equal standing** with heating and cooling.

Decision basis: **`docs/adr/0003-band-compliance-track-the-target.md`**. Built on the 2026-06-17 end-to-end control review. Tracking: the **Band-Compliance Reconcile epic** + the issues below.

---

## 1. The reframe (what changed vs. the prior review)

The control review found a sound architecture but tuned for *efficiency* — a wide float-envelope ("do nothing inside the band") plus cost-brakes that decline to spend energy correcting drift, most visibly letting **VPD drift wet at night**. This sprint flips the objective to **compliance**: drive toward `target`, keep `low/high` only as safety bounds, and remove the cost-brakes. We keep the single-arbitration FSM (no dual PID fight) and the anti-fight safety. We **reject** temperature integration and float-for-efficiency here (they trade tracking for cost).

## 2. Workstreams & issues

Each issue is short-id'd (BC-n); the epic maps short-id → live issue number. Priorities: **P0 = the compliance critical path + the success metric**; P1 = parity/objective; P2 = simplification follow-ons.

### WS-1 — The target is correct, smooth, and singular
- **BC-1 [P0] Band single-source-of-truth → zero divergence, enforced.** Drive `v_band_device_divergence` (mig 178) to zero and make device-vs-served-band drift a hard alert/invariant. The target the controller follows must equal the target the graph shows must equal the crop recipe. *(firmware sync-confirm + data; no new band math — leans on mig 171.)*
- **BC-2 [P1] Optimize the diurnal anchors to the crops.** Review/retune `crop_band_anchors` (temp + VPD) for a smooth, crop-correct curve across the solar day (Vandas et al.); confirm the harmonic shape is smooth and the night VPD floor is dry-correct. Delete the legacy `bias_heat`/`bias_cool` target-offset path (retired-inert). *(data + schema + registry; planner bounded.)*

### WS-2 — The greenhouse tracks the target (the core)
- **BC-3 [P0] Float-envelope → target-centered tracking.** Narrow the control deadband so actual is driven toward `target`, not merely kept inside `[low,high]`; retune hysteresis to maximize band-grade subject to the min-on/off relay-wear limits. Keep single-arbitration (no dual loop). *(firmware tuning + bounded tunables.)*
- **BC-4 [P0] Dehum parity — remove the cost-brakes.** Delete `sw_night_econ_heat_suppress_enabled` + `night_econ_heat_suppressed()`, the `cold_dehum_allowed`/`outdoor_cold_for_vent` defensive suppression, and **demote `resource_cost`/`relay_churn_cost` out of `climate_candidate_precedes`** so band-error dominates. VPD/dehum tracked as aggressively as temperature; net code removal. *(firmware — deletion.)*
- **BC-5 [P1] Bounded heat-assisted dehumidification state.** Replace "suppress dehum when cold" with ONE named, limited state that may co-run heat to raise moisture capacity so the VPD target is reachable on a cold humid night (the only sanctioned heat+vent exception, per ADR 0003 §3). *(firmware.)*
- **BC-6 [P1] Planner objective = band compliance.** Apply migration **147** (reward → `compliance_v2_attributable_pct` + ladder reanchor) via the migration-safety tooling, and re-baseline the anchor-stability gate. Makes the AI explicitly optimize tracking. Folds in the standing #20 / #17 / epic #13. *(migration + planner.)*

### WS-3 — Remove what breaks tracking + simplify
- **BC-7 [P0] Night dew-margin correctness.** Resolve the dead `night_stress_min_dew_margin_f` (plumbed end-to-end, referenced 0× in logic; its value is *looser* than day, contradicting "night is most dangerous"). Decide night semantics (stricter at night, wired on `is_night_phase`) or delete the parameter. Condensation protection must serve compliance, not silently fight it. *(firmware — small.)*
- **BC-8 [P1] One anti-chatter mechanism.** The purpose-built dwell gate (`sw_dwell_gate_enabled`) ships **disabled**; flap protection rests on scattered hysteresis + invariant #6. Pick ONE mechanism (arm+settle the dwell gate OR consolidate hysteresis) and delete the other path. Flapping is the opposite of tracking. *(firmware.)*
- **BC-9 [P1] Sensor input integrity (minimal).** Add tight jump/rate-of-change rejection + a stuck-in-range flatline → `SENSOR_FAULT` path (today only NaN/out-of-range trips it), so a corrupt-but-plausible reading can't corrupt tracking. Few lines, fail-safe. *(firmware + ingestor.)*
- **BC-10 [P2] Dead-code & tunable purge.** Remove: `bias_heat`/`bias_cool` (BC-2), the suppression knobs (BC-4), the fw-v2-stripped pushed params (`direct_wet_stress*`, `fog_stress*`) that fire benign `setpoint_unconfirmed` alerts, and the redundant HA "TUNABLE CONSTRAINTS" block in `gather-plan-context.sh` (registry is authoritative). Net-negative LOC. *(firmware + ingestor + registry.)*
- **BC-11 [P2] Consolidate anti-fight into the pure path.** Move the heat↔air-exchange interlock from the `controls.yaml` lambda into pure `resolve_equipment` + add an invariant (so CI/replay guards our strongest safety rule). If replay-diff = 0, collapse the extra `THERMAL_RELIEF` mode and the `SEALED_MIST`/`VENTILATE` fog-assist split → fewer modes, fewer edge cases. *(firmware.)*

### WS-4 — Prove it (measurement == success)
- **BC-12 [P0] Band-compliance metric + homepage graph.** Define the acceptance bar (below), surface a live 24/7 temp-and-VPD tracking score from `mv_zone_band_grade`, and ensure the homepage band-trace plots actual-vs-**device-truth** target (close data-path finding F2) plus the live compliance score. This is how we *see* tracking. *(data + web.)*

## 3. The explicit DELETE list (first-principles)

Sprint succeeds only if it removes more than it adds. Targets for deletion/collapse:
- `bias_heat`, `bias_cool` (retired-inert) — Setpoints + registry + refs. *(BC-2)*
- `sw_night_econ_heat_suppress_enabled` + `night_econ_heat_suppressed()`. *(BC-4)*
- `cold_dehum_allowed` / `outdoor_cold_for_vent` defensive suppression branch → replaced by BC-5's active state. *(BC-4/BC-5)*
- `resource_cost` / `relay_churn_cost` as arbitration terms in `climate_candidate_precedes`. *(BC-4)*
- fw-v2-stripped pushed params (`direct_wet_stress*`, `fog_stress*`) + their benign-alert tail. *(BC-10)*
- HA "TUNABLE CONSTRAINTS" block in `gather-plan-context.sh` (redundant with registry). *(BC-10)*
- One of {dwell-gate, scattered hysteresis} anti-chatter paths. *(BC-8)*
- `THERMAL_RELIEF` mode + `SEALED_MIST`/`VENTILATE` fog-assist split — collapse if replay-diff = 0. *(BC-11)*
- The duplicated heat↔air interlock in `controls.yaml` (move to one pure place). *(BC-11)*
- `night_stress_min_dew_margin_f` if BC-7 decides delete-not-wire.

## 4. Success metric & acceptance bar

Measured from `mv_zone_band_grade` / `fn_compliance_v2`, over rolling 24h, **temperature AND VPD, day and night**:
- **Time-in-tracking** (actual within the tight tracking band around target): proposed **≥ 90%** each axis, **no worse at night than day** (the night-wet-drift specifically eliminated — night VPD time-in-tracking ≥ day).
- **Deviation-from-target**: median |actual − target| within the tracking half-band; p95 within the safety band.
- **No flap regression**: mode transitions/hr stays ≤ invariant #6 bound while tracking tighter.
- **Visual**: homepage band-trace shows actual hugging the device-truth target line across a full diurnal cycle.

(The exact tracking-band widths are the sprint's calibration output — BC-3 tunes them to hit the bar without tripping relay-wear/flap limits.)

## 5. Phasing

- **P0 critical path (this sprint):** BC-1, BC-3, BC-4, BC-7, BC-12 — make the target singular, track it, give dehum parity, fix night condensation, and *measure* it.
- **P1 (this sprint if capacity):** BC-2, BC-5, BC-6, BC-8, BC-9.
- **P2 (reconcile tail):** BC-10, BC-11.

## 6. Guardrails (unchanged, non-negotiable)

Single-writer device gate; firmware OTA stays Jason-gated (land + prove offline via replay-diff/invariants/unit tests, OTA on the gate); band-CURVE changes require `make firmware-replay-band`; no self-committing migration wrapped in an outer txn; safety rails / FSM lockout / dewpoint veto remain planner-unreachable (ADR 0002 §5). Net behavioral changes to automatic control must ship with replay evidence.

## 7. Tracking

Broad reconcile **epic #359**; milestone **Greenhouse Control Optimization** (#15); label `epic:climate-band` + per-area labels. Decision in `docs/adr/0003`. Supersedes the narrow epic #13 (mig-147, now closed) by absorbing it as BC-6.

| Item | Issue | P | Item | Issue | P |
|---|---|---|---|---|---|
| BC-1 SSOT→0 divergence | #360 | P0 | BC-7 night dew-margin | #366 | P0 |
| BC-2 optimize anchors + del bias | #361 | P1 | BC-8 one anti-chatter | #367 | P1 |
| BC-3 target-centered tracking | #362 | P0 | BC-9 sensor jump/flatline | #368 | P1 |
| BC-4 dehum parity / del cost-brakes | #363 | P0 | BC-10 dead-code purge | #369 | P2 |
| BC-5 heat-assisted dehum state | #364 | P1 | BC-11 consolidate anti-fight | #370 | P2 |
| BC-6 planner=compliance (mig 147) | #365 | P1 | BC-12 metric + homepage graph | #371 | P0 |

**P0 critical path:** #360, #362, #363, #366, #371. Absorbed: #13 (closed), #20/#17 (→ BC-6 #365).
