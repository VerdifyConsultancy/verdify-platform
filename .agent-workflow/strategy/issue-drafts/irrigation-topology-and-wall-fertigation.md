## Problem

The current firmware does not match the actual greenhouse topology or approved crop-care intent:

- Climate wet-assist rotates center, south, and west; live today recorded center 16, south 8, and west 2 climate cycles.
- South/west time-window availability is treated as climate demand, so simply changing one eligibility flag is insufficient.
- Wall scheduled/manual fertilizer queues wall plus south and west fertilizer misters.
- Center drip and center/south/west fertilizer controls remain exposed; live center irrigation enable is on even though center drip is unconnected.
- Wall and center schedules are daily at 10:30 while fertilizer admission ends at 09:00, so jobs are dropped accidentally rather than implementing policy.
- A global 90-minute post-feed hold blocks center climate mist despite fertilizer being wall-only.
- Climate and irrigation code can write the same relays without one ownership resolver.

Jason's approved intent is: center-only clean VPD mist; south/west mist only for explicit intentional irrigation; automatic fertilizer on wall drips only; dormant center/south/west infrastructure retained but disabled until planted/commissioned; weekly wall feed as a researched pilot; liters-based prewet/feed/immediate flush; missing commissioning fails closed.

## Desired outcome

One deterministic firmware resolver makes the approved topology unambiguous, and the wall-only automatic feed path is safe, measurable, exact-once, and impossible to enable with guessed chemistry or duration.

## Acceptance intent

- [ ] Every climate/VPD wet request resolves to center only; live attribution shows zero south/west climate cycles.
- [ ] South/west clean irrigation requires explicit enabled intent and cannot race climate relay ownership.
- [ ] Center drip and all non-wall fertilizer remain represented but disabled by default with cfg readbacks.
- [ ] Wall fertilizer can actuate only wall plumbing; no manual, schedule, planner, repair, or retry path can energize center/south/west fertilizer while disabled.
- [ ] Weekly automatic admission uses solar cadence, exact-once persisted eligibility, and current commissioning revision.
- [ ] `prewet_l`, `fert_l`, and `postflush_l` derive bounded durations from calibrated aggregate wall flow.
- [ ] Sequence is clean prewet → feed → fertilizer off → immediate clean flush; the fixed 90-minute hold is removed.
- [ ] Missing/stale water chemistry, product analysis, injector ratio, flow, distribution, or flush endpoint prevents automatic fertilizer actuation.
- [ ] Unit tests, invariants, replay, band replay, firmware compile/check, and irrigation software audit pass.

## Non-goals

- Adding sensors, plumbing, HAF, shade, or dehumidification equipment.
- Deleting dormant zone infrastructure permanently.
- Inventing one full-strength recipe for lime/citrus and cannabis.
- Enabling physical fertilizer before commissioning measurements exist.

## Dependencies and related issues

- Parent: #350
- Supersedes the center-start premise in #37 and the climate fairness router in #323.
- Incorporates fail-closed mask/readback intent from #397.
- Must land before any wall schedule is moved into an active feed window.
- One combined OTA with the DLI/control-policy recovery due weekly/bake constraints.

## Initial risk

Critical if sequenced incorrectly: moving today's wall schedule earlier would also fertilize south and west misters.

## Affected surfaces

Greenhouse firmware YAML/lib/tests, cfg readbacks and registry integration, irrigation job/feedback evidence, firmware FSM/design docs, and irrigation software audit.

### Triage investigation

- Existing issue search: #350 is the correct epic, but its current desired state depends on new soil/EC hardware and leaves actual zone semantics undecided; no child carries this approved software-only outcome.
- Evidence inspected: current main firmware, live Home Assistant schedule/readbacks, 30-day relay attribution, irrigation state machine and manual buttons, operator brief, primary fertigation research.
- Reproduction: read-only code/live audit.
- Likely cause: legacy shared-purpose zone router and scheduler persisted after physical occupancy changed.
- Potential fix options: one pure resolver, explicit zone policy/readback, commissioning-gated liters state machine, wall-local flush/ownership.
- Adversarial audit: preserve future infrastructure; do not enable a schedule before wall-only routing; do not require deferred physical feedback for software truth.
- Confidence: high.
- Remaining unknowns: actual recipe/volume/product remain operator commissioning inputs and intentionally do not block fail-closed software.
