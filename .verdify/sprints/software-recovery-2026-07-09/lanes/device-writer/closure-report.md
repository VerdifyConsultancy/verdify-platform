# Device-writer lane closeout checkpoint

State: **READY_FOR_INTEGRATION**. Implementation, independent criticism, and
GitHub CI are complete; deployment-only LANE-AC-05 remains open.

## Objective and delivered outcome

The sole ESPHome writer now separates transport generations from cfg drift,
uses all 56 exact cfg wire identities, compares desired state with normalized
observed state, and executes through one bounded round-robin queue. Durable
state advances only after its real milestone. Cancellation, timeout, partial
delivery, restart, lifecycle persistence failure, stale retries, newer logical
requests, reconnects, and Lease loss cannot manufacture a sent or confirmed
value.

No firmware, migration, planner policy, irrigation policy, production database,
ArgoCD application, device, stale 0.25 intent, or deployment manifest changed.

## Scope audit

Lane-owned files:

- `ingestor/esp32_push.py`
- `ingestor/ingestor.py`
- `ingestor/shared.py`
- `ingestor/tasks/_common.py`
- `ingestor/tasks/confirmation.py`
- `ingestor/tasks/dispatcher.py`
- `verdify_schemas/tunable_registry.py`
- `verdify_schemas/tests/test_firmware_drift.py`
- `tests/test_03_ingestor.py`
- `tests/test_05_dispatcher.py`
- `tests/test_16_writer_lifecycle.py`
- this lane's `.verdify/**` records

Controller-approved coordination exceptions:

- `tests/test_solar_band_anchors.py`: only
  `TestAnchorContract.test_registry_wire_contract` changed. The old assertion
  required `cfg_<name>` even though the actual ESPHome display names contain a
  bullet and units; it now asserts the generated exact-wire fixture. The live
  and YAML fixture both validate 56/56.
- `slack_ops/briefs.py` and `scripts/validate-irrigation-stack.py`: only lifecycle
  classification changed. Both now distinguish in-flight, confirmed, and
  terminal-unconfirmed rows. No irrigation behavior, alert age, or pass/fail
  threshold changed. Firmware-control must preserve this vocabulary when it
  integrates the irrigation validator.

No accidental or unapproved path remains in the diff.

## Acceptance

| Criterion | Verdict | Evidence |
|---|---|---|
| LANE-AC-01: exact generated cfg wire fixture | PASS | WRI-EV-001, WRI-EV-003 |
| LANE-AC-02: cfg drift cannot become reconnect/broad unchanged push | PASS in source/tests; runtime rollout pending | WRI-EV-002, WRI-EV-006 |
| LANE-AC-03: cancellation/restart/timeout/partial delivery truth | PASS | WRI-EV-002, WRI-EV-007, WRI-EV-008 |
| LANE-AC-04: no task delayed beyond twice cadence | PASS | WRI-EV-002, WRI-EV-007 |
| LANE-AC-05: exact deployed two-hour zero unchanged broad pushes | PENDING | WRI-EV-011 |

## Interfaces and behavior changed

- `FIRMWARE_V2_CFG_WIRE_IDS` is the canonical 56-entry exact readback fixture.
- Shared writer state now exposes monotonic transport/drift generations and a
  fatal lifecycle-persistence event.
- `push_to_esp32_detailed` returns per-command outcomes and accepts immutable
  producer tokens that are normalized into one local ordering sequence.
- Delivery status uses requested, queued, retrying, sent, failed, cancelled,
  superseded, and confirmed semantics guarded by legal prior-state CAS.
- Runtime logs separate transport from persisted lifecycle events and include
  reason, generation, command/anchor counts, and actual unchanged-anchor counts.
- `summarize_writer_log_lines` is the redacted two-hour acceptance classifier.

Because the schema registry changed, release rollout must restart both
`verdify-ingestor` and `verdify-mcp`. This lane did not perform those restarts.

## Validation

- `make VENV=/Users/jason/repos/verdify-platform/.venv lint`: PASS.
- Contracted targeted pytest command: 46 passed.
- Focused compatibility/drift/registry suite: 299 passed at the reviewed
  implementation snapshot and 300 passed at the current integrated head.
- Python compilation and `git diff --check`: PASS.
- Required `make test`: 702 passed, 139 inherited failures, 6 skipped, 10
  inherited environment errors. The failures are retired local
  Docker/systemd/DB/API/Vault/site assumptions and unrelated pre-existing
  firmware/PVC/UI assertions; no focused #433 regression remains.
- Read-only exact entity comparison: expected 56, matched 56, zero missing.
- Independent critic: ACCEPT, no remaining P0/P1 blocker.
- The controller coverage amendment and controller/session records were merged
  through `07ad7e756d1caa5eb2c625a88796c999d8222fe8`. Final local validation ran at
  integrated head `a260e4258821fd14d87742180541a9ba196f1525` with no post-critic
  product-source change.

## Runtime and release evidence

The live pod observed during closeout runs pre-fix digest
`sha256:175e5ecee7468651069458fff50d6036fbfa0f3b73cf366b3e7e9159d62259e8`.
It is baseline evidence only. Release control must build/promote the reviewed
revision, pass the device-write gate, record the exact running digest/commit,
and attach a complete two-hour classified window. The worker makes no AC-05 or
production-fix claim.

The existing deployment's `replicas: 1` plus `strategy: Recreate`, together
with the in-process Kubernetes Lease and the final inside-lock generation/client
fence, preserves the one-writer invariant. A lifecycle persistence fatal exits
the main gather; the Lease then expires if a hard stop prevented graceful
release.

## Git and PR checkpoint

- Contract baseline: `0a9a19a840be6bae1beba604497d880b3b74b1ef` (provenance).
- Controller execution baseline: `6b5042c6a9d525cf1429bfda5a1f6d9a95470476`.
- Current controller contract/session head:
  `07ad7e756d1caa5eb2c625a88796c999d8222fe8` (contract content introduced by
  `a2c3ee92a46abe1861b330bf63d60e0f717605bd`).
- Reviewed/rebased implementation head:
  `e4e1c5901d2ce46400df65d9ced516bed25c0eb2`.
- Final locally validated integrated head before this records-only update:
  `a260e4258821fd14d87742180541a9ba196f1525`.
- Pull request: [#442](https://github.com/VerdifyConsultancy/verdify-platform/pull/442).
- Remote implementation branch matched the reviewed head before this
  evidence-only closeout commit.
- CI at closeout head `991f9aae417a6eedce3fd252d11e254834bf3584`:
  25 passed, eight non-applicable publish/promotion checks skipped, zero failed
  or pending.
- Self-merge is not authorized and was not attempted.

## Follow-ups and residual risk

- LANE-AC-05 remains owned by release control and does not block source
  integration; it does block declaring this lane COMPLETE.
- Stale `band_track_fraction=0.25` retirement remains #377 after repaired
  consumers are deployed and accepted.
- The full lifecycle classification in
  `scripts/validate-irrigation-stack.py` is an approved shared contract;
  firmware-control must serialize its integration rather than overwrite it.
- A lifecycle DB outage can force a safe supervised restart and a brief
  zero-writer gap. It cannot permit unrecorded continued writes.

## Rollback and disablement

Before deployment, rollback is the parent of the implementation commit. After
deployment, use the existing digest-pinned prod promotion rollback and keep the
device-write gate closed until the single-writer/Lease check passes. Do not
restore the old cfg ids, pre-delivery sent caches, broad force event, or a
parallel writer.
