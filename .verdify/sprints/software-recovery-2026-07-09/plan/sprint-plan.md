# Live greenhouse software truth and control recovery

## Primary outcome

Deliver the July 9 recovery all the way to the live greenhouse: one truthful non-starving writer, a self-healing bounded planner, correct irrigation topology, honest DLI availability, solar-night realized dry-out evidence, reliable replay/heap/cycling gates, provenance-bearing resource accounting, and removal plus rotation of the exposed database credential.

This is an outcome-driven recovery wave, not a one-day issue-count exercise. It contains 17 executable GitHub issues under `sprint:software-recovery-2026-07-09`. Work is complete only after reviewed source is merged, schema and services are live, stale intent is retired, one combined firmware OTA passes every deterministic gate, and the settled runtime state is re-probed.

## Delivery order

1. Finish source credential cleanup and caller inventory; hold the production release at the explicit rotation gate.
2. Land serialized contracts/evidence: solar parity, divergence migration 189, DLI availability, cycle truth, night episodes, resource provenance, and outcome integration.
3. Repair the device writer and prove non-starving terminal semantics.
4. Repair planner/MCP delivery, strict bounds, plan lifecycle, and forecast semantics; activate after acceptance.
5. Build one exclusive firmware package: measured heap protection/diagnostics, approved irrigation topology, DLI unavailable signal, real replay freshness, and regression proof that existing mister/light dwell and solar-night safety survive. Add no speculative control tunables.
6. Independently review and integrate every lane on current main; run repo, migration, firmware, lighting, irrigation, heap, cycling, and release gates.
7. With rotation authorized, rotate and verify every DB consumer; then release schema/services, retire stale 0.25 intent, clear the planner critical alert, and OTA once.
8. Prove immediate and settled production behavior; reconcile GitHub and lifecycle records.

## What good looks like

- No five-to-six-minute 69-value push storm; one deliberate drift causes one truthful write.
- Required SUNRISE/SUNSET work terminates in a valid expiring plan or explicit neutral fallback, and tool loss self-heals.
- Climate mist is center-only. Center drip and dormant south/west irrigation remain disabled. Fertilizer is wall-drip-only and cannot actuate until commissioning is complete.
- Crop DLI is `unavailable`, not a number, until Jason replaces and validates the sensor.
- Night drying follows solar night and reports realized moisture/temperature response with bounded stop reasons.
- Replay contains real outdoor freshness coverage; firmware maintains a safe heap floor; transition-derived cycling does not regress; old inflated counter premises do not trigger speculative tuning.
- Water and energy surfaces say what is measured, modeled, uncertain, or unattributed.
- Effective `band_track_fraction` is zero with no repin, the exposed DB credential is invalidated, and no deploy-blocking alert remains.

## Human gate

Implementation, production delivery, and the OTA are already authorized by the objective. One independent protected action remains: explicit authorization to rotate the exposed production application DB credential. Local implementation proceeds, but nothing is released to production until issue #438 and its gate are satisfied.

## Highest risks

The combined firmware touches the only live controller; several schema changes must be serialized; the current device has a dangerously low historical heap floor; weather can confound outcome comparisons; and credential rotation can strand a hidden consumer. The plan answers those risks with one exclusive firmware owner, strict release order, availability-bearing evidence, independent review, a rollback artifact, and immediate plus settled runtime verification.

## Readiness

Ready for lane decomposition. Product decisions are resolved; physical fertigation commissioning is intentionally fail-closed; credential rotation blocks release only, not implementation.
