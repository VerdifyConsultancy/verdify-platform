## Problem

Observed in production after PRs #431/#432 and ingestor digest `175e5ec`: the ESP32 transport remains connected, but ordinary cfg readback changes still trigger a broad 69-value job mislabeled `reconnect reconcile` every roughly five to six minutes. Each job performs 56 anchor service writes and occupies the serial task loop for about 4m38s; one 300s timeout is visible. Each hour contains roughly 450–570 anchor pushes.

Verified causes:

- `_record_cfg_readback` uses the same global force event for generic cfg drift and true reconnect; dynamic outdoor-dew-point readback repeatedly trips it.
- All 56 staged registry anchor readback IDs differ from the actual ESPHome wire slugs.
- Dispatcher cache and database state advance before a paced batch physically completes, so cancellation can leave false sent/pending state.
- The sequential task loop awaits long dispatcher batches and starves heartbeat, alerts, confirmation, and planning work.
- The stale active `band_track_fraction=0.25` row adds another repeated compare/clamp against the approved/device zero.

This is the residual P0 child of #430. The Tier-1 change reduced one class of constant churn but did not satisfy its runtime outcome.

## Desired outcome

The sole live writer reconciles observed normalized state exactly once when needed, distinguishes true connection generation from cfg drift, records each command truthfully, and never starves unrelated tasks.

## Acceptance intent

- [ ] Generated registry fixture shows zero mismatch across all canonical cfg wire IDs.
- [ ] Two steady-state production hours contain zero broad anchor pushes while ESPHome remains connected.
- [ ] One deliberate readback drift causes exactly one write and one confirmation.
- [ ] One controlled reconnect increments connection generation once and restores only parameters that require reconnect restoration.
- [ ] Requested, queued, sent, failed, cancelled, confirmed, and superseded states match physical outcomes across partial delivery and restart.
- [ ] No periodic task runs later than twice its intended cadence during device writes.
- [ ] Logs distinguish reconnect, drift, desired change, retry, and confirmation.

## Non-goals

- Collapsing all cfg entities into a config hash.
- Removing firmware tunables or changing greenhouse control behavior.
- Retiring the stale 0.25 row before repaired consumers are deployed and verified.

## Dependencies and related issues

- Parent: #430
- Root heap incident: #428
- Shipped but insufficient Tier-1 work: PR #431 and promotion PR #432
- Stale band intent: #377
- Planner critical alert affected by task starvation: #427

## Initial risk

Critical. This is the sole writer to a live greenhouse device and currently records ambiguous delivery state while monopolizing the scheduler.

## Affected surfaces

`verdify_schemas/tunable_registry.py`, `ingestor/entity_map.py`, `ingestor/ingestor.py`, `ingestor/shared.py`, `ingestor/esp32_push.py`, `ingestor/tasks/_common.py`, `dispatcher.py`, `confirmation.py`, and targeted tests.

### Triage investigation

- Existing issue search: #430 covers the broader simplification program but describes Tier 1 as shipped/clean; no existing issue captures this residual acceptance boundary.
- Evidence inspected: live ingestor logs, `setpoint_push_log`, cfg telemetry, ESPHome connection logs, current main code, PRs #431/#432.
- Reproduction: passive six-hour production observation; no device mutation.
- Likely cause: conflated event semantics, invalid wire IDs, pre-delivery accounting, and synchronous scheduling.
- Potential fix options: monotonic connection generation, generated wire-ID contract, normalized confirmed-state comparison, bounded writer queue, post-success lifecycle transitions.
- Adversarial audit: preserve reboot-reverting safety behavior; cancellation and partial batches must not create stranded values.
- Confidence: high.
- Remaining unknowns: exact queue pacing can be chosen during implementation as long as task-cadence and device-load acceptance pass.
