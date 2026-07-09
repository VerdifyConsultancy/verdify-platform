# Transcript replan — software-scope corrections

**Source:** Jason's 2026-07-09 follow-up to the greenhouse review

**Status:** routed and approved for implementation and delivery on 2026-07-09
**Canonical record:** `transcript-replan.yaml`

## Corrected product intent

- `TR-001`: the center drip is disabled and physically unconnected; zero runtime is expected.
- `TR-002`: center misters are clean-water climate actuators for the VPD cycle, not scheduled irrigation or fertilizer delivery.
- `TR-003`: fertilizer delivery is wall-drip-only.
- `TR-004`: the 10:30 center schedule has no confirmed purpose and may need to be disabled rather than retimed.
- `TR-005` and `TR-006`: interior DLI is unknown until Jason replaces the broken light sensor; new physical sensors or hardware are outside the current plan.
- `TR-007`: software should expose DLI as unavailable and suppress DLI-dependent conclusions instead of fabricating an interior value.
- `TR-008`: overnight dry-out remains a high-interest controls problem using existing equipment and telemetry.
- `TR-009`: the five-to-six-minute event is a mislabeled reconcile, not a transport disconnect; its cache reset, wire-ID misses, and repeated full push require a software fix.
- `TR-010`: the production AI planner requires a reliability and materialization recovery.
- `TR-011`: remaining greenhouse questions that materially change software behavior should be asked directly.
- `TR-012`: deliberate center Vanda watering is not accepted current intent.
- `TR-013` and `TR-014`: wall fertilization is automatic; begin with a researched weekly pilot using calibrated volumes, commissioning evidence, and immediate clean flushing rather than guessed minutes.
- `TR-015`: center/south/west infrastructure is future-capable but disabled while those paths have no planted-zone purpose.
- `TR-016` and `TR-017`: VPD control uses center clean mist only; south/west misters are intentional irrigation surfaces, not climate rotation.
- `TR-018`: night dry-out follows solar/diurnal phase and environmental thresholds, never a fixed clock window.
- `TR-019`: retire the June `band_track_fraction=0.25` experiment; zero is authoritative.
- `TR-020`: activate the bounded planner immediately once deployed repair checks pass.
- `TR-021`: implementation, production delivery, and any necessary OTA are approved without another operator prompt; deterministic safety gates remain.

## Conflicts requiring reconciliation

1. The July 9 report and draft definition interpreted absent center-drip runtime as failure; Jason says it is the expected state.
2. Existing Vanda design material proposes center fertilizer paths; Jason requires wall-drip-only fertilizer.
3. DLI dashboards and planner inputs imply corrected interior DLI; Jason says no defensible interior DLI exists while the sensor is broken.
4. Hardware recommendations were presented as current priorities; Jason has deferred physical additions from the current plan.
5. Current VPD mist rotates south/west/center; required behavior is center-only climate mist with south/west intentional irrigation.
6. Permanent removal of future zone infrastructure conflicts with the required disabled-until-planted policy.
7. The 90-minute absorption hold conflicts with authoritative fertigation practice favoring immediate measured line flushing.

## Proposed software backlog

1. P0: stop the false-reconnect reconcile cycle and unchanged 69-value pushes while preserving the stable ESPHome transport.
2. P0: recover Hermes-to-MCP liveness, full-plan delivery, plan expiry, registry-compatible materialization, and bounded authority.
3. P1: implement center-only VPD mist, intentional-only south/west irrigation, fail-closed unplanted-zone enablement, and automatic researched weekly wall-only fertigation.
4. P1: introduce explicit DLI unavailable/provenance semantics across firmware telemetry, database, dashboards, and planner context.
5. Update issue #410 around diurnal-phase overnight dry-out using existing sensors and bounded vent/reheat supervision.

These items are approved implementation scope. GitHub, production, firmware, database, devices, and setpoints remain unchanged at intake time.

## Handoff

Return to project-definition discovery. Correct the draft definition and review, then ask only the remaining contrastive greenhouse questions before issue or sprint planning.
