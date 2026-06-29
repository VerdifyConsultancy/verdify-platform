# Verdify Platform Milestones

Last updated: 2026-06-23

Agent name: `verdify-platform`

## Active Controller-Replan Milestones

| Milestone | Open | Closed | Purpose |
|---|---:|---:|---|
| G0 - Controller Architecture Audit | 0 | 1 | **DONE 2026-06-17.** L1 actual-vs-intended architecture, dead/stale path inventory, CI/CD/release checklist, and failure-mode docs — delivered + drift remediated (PRs #353-#358) + prod deploy executed. |
| G1 - Firmware-First Determinism | 1 | 2 | **L2 #344 + L3 #345 DONE 2026-06-17** for the firmware/control base: firmware-internals FSM/relay/safety/bands/72h spec (`docs/firmware-fsm-spec.md`) + safety-rail + 72h-disconnected + crop-agnostic + compliance-feasibility test rails, proven offline (222/0 native, 193,525-row invariants) + live-prod confirmation. **L7 #349** lighting/occupancy remains open. June 23 follow-through now lives under G2/#359/#293/#347/#348. |
| G2 - Data Contracts and Observability | 2 | 0 | L5/L6 schema authority, source-of-truth/readback contracts, drift detection, DB solar phase parity, floating-corridor outcome KPIs, moisture-estimator telemetry, and the evidence-gated VPD/dehum policy lane. |
| G3 - Planner, Irrigation, Lab, and Research | 4 | 0 | L4/L8/L9/L10 planner boundary, irrigation/fertilization decisions, lab notebook publishing, and all-year/extreme-weather test harness. |

## Legacy/Open Milestones

These milestones remain in GitHub because their issues are useful historical or
child anchors. Do not use them as the primary current planning decomposition.

| Milestone | Open | Closed | Current relationship |
|---|---:|---:|---|
| Cutover Complete (done) | 0 | 30 | Historical done bucket; do not add new active work. |
| Enablement: Three-Env (dev/stage parity) | 7 | 2 | Legacy name. Three-env work is superseded by single-env prod; open issues are L1/legacy cleanup anchors. |
| Enablement: Compliance & Twins | 5 | 4 | Child/evidence anchors for L3/L5/L6/L10. |
| Enablement: Data Hygiene & Observability | 10 | 8 | Child/evidence anchors for L5/L6/L9. |
| Enablement: Decommission & Auth | 7 | 0 | Child/evidence anchors for L1/L9 residual cleanup. |
| Hardware / Seasonal (operator-gated) | 6 | 0 | Child/evidence anchors for L8 operator-gated physical work. |
| M7 - HA: first-principles resilience | 10 | 15 | Child/evidence anchors for L1/L5 reliability work. |
| Greenhouse Control Optimization | 17 | 0 | Legacy umbrella under L2/L3/L7/L8. |
| Deploy Enablement (agent access + firmware CI/OTA) | 12 | 0 | Child/evidence anchors for L1/L10 enabling work. |
| M1 - CI unblocked + images published | 0 | 22 | Historical completed work. |
| M2 - Data safe before migration | 0 | 8 | Historical completed work. |
| M3 - Dev/prod substrate ready (+ SHADOW_MODE) | 0 | 4 | Historical completed work; dev is now deleted. |
| M4 - Handoff safety green | 0 | 3 | Historical completed work. |
| M5 - Single-writer cutover (Jason) | 0 | 1 | Historical completed work. |
| M6 - Iris decommission ready + product plane | 0 | 3 | Historical completed work. |

## Historical Milestones

| Milestone | Evidence |
|---|---|
| M1 - CI unblocked + images published | Closed issue #69; issues #78, #81, #82, #92, #99, #126-#128. |
| M2 - Data safe before migration | Closed issue #72; parity and schema work including #129. |
| M3 - Dev/prod substrate ready | Closed issues #25, #28, #84; dev/staging language is historical after the 2026-06-16 single-env change. |
| M4 - Handoff safety green | Closed issue #71 and related route/device safety work. |
| M5 - Single-writer cutover | Record issue #216 documents execution on 2026-06-07. |
| M6 - Iris decommission ready + product plane | Record issue #217 documents VM powered off; product follow-ups remain as legacy anchors. |

## Next Milestone Recommendation

**G0 is DONE (2026-06-17).** The audit's actual-vs-intended map, stale-path
inventory, and release/failure-mode checklist exist, drift was remediated, and
CI/CD was hardened. The audit flagged specific risks to pull forward into the
next milestones (see `docs/reviews/lane1-architecture-audit-2026-06-16.md` §8):
- **G2 (L5/L6):** DB PITR / replica (P0 — single-replica StatefulSet, RPO ≤24h);
  out-of-band writer-absent alert (P0 — spec handed to monitoring-stack); repoint
  compliance dashboards to device-truth (`setpoint_snapshot`).
- **G1 (L2/L3): DONE 2026-06-17.** L2 #344 + L3 #345 closed — the firmware
  control core was already correct; this work delivered the authoritative
  `docs/firmware-fsm-spec.md` and closed the test-rail gaps (safety-heat +
  sensor-fault invariants #25/#26, the 72h-disconnected determinism tests, the
  crop-agnostic guard, the compliance-feasibility classifier), proven offline +
  confirmed live in prod. The band-curve replay gate is blocking (Phase 1).
  **G1 remainder: L7 #349** (lighting/occupancy).
- **2026-06-23 G2 pull-forward:** DB solar phase parity is now the top data/SOT
  fix because `fn_solar_altitude()` hardcodes solar noon at 13:00 local. After
  that, run the Jason-gated #377 float trial and verify the locally implemented
  outcome/moisture telemetry through the OTA/deploy path before deeper VPD
  policy changes (#383). See
  `docs/reviews/adversarial-audit-backlog-replan-2026-06-23.md`.
Proceed to L7 (G1) + G2/G3 next; pull urgent L5/L6 safety/data items forward per the audits.
