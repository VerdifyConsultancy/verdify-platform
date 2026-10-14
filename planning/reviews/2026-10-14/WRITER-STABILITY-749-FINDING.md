# #749: writer-stability source finding

## Superseding retained-log timestamp finding — October14

Offline replay of the same retained input found17 matching app events and17 silently skipped naive timestamps. The old collector therefore fell back to Pod start with a zero recurring-error counter. Earlier22:10/22:15 event-max clocks describe normalized intended-event projections, **not the actual old collector output**. No readiness packet was executed or acted on. The collector-only correction requests authoritative RFC3339 timestamps on the same existing log GET and fails closed on malformed lifecycle time. The underlying repeated-report classification and recurrence-arithmetic defects below remain concrete. Terminal halted reconciliation stays an explicit denial; no clock correction settles it.

The existing supported archival route is conditional confirmed_halt_forward v2, preserving original active files and requiring distinct fresh authority later. Authentic original fullPod7666/UID6509 and same-process cb21/session1798 preview are retained; earlier different-Pod shorthand was incorrect. Exact target raw files and native request outcomes still require a finite hash-bound read-only completion. No archival, state deletion, stale approval replay, physical recovery or readiness acceptance follows from this source finding.

## Retained initial source assessment


**Repeated `blocked_stage` reporting does not itself identify a new transport connection. The collector currently treats it as a new stability-clock event because its pattern matches the sticky dispatch reason.** An unresolved held stage still matters: this finding does not declare it healthy, reset or waive the clock, supply a current readiness result, or authorize replay/device work.

Source assessed: production revision `ed01cb8f64c9b3165924e4006a9bb3fe2a7e1920`, with byte equality checked against the image build source `5e992d1747eb77d6f596d7e880db1f30862dae06` for the five relevant files. Root's retained owner receipt is `8a13d19f63e31586a460298ff82582746f7fdf83`. It reports a22:10:42UTC `blocked_stage` line, same generation1/socket/restart0, and existing-contract earliest22:40:42 only if no later matching event. That is a valid description of the current collector algorithm, not proof that22:10:42 was the last genuine reconnect. No new log, state, cluster, SQL or provider observation occurred here.

## Exact producer and consumer chain

| Source | What it establishes |
| --- | --- |
| [shared.py:236](https://github.com/VerdifyConsultancy/verdify-platform/blob/ed01cb8f64c9b3165924e4006a9bb3fe2a7e1920/ingestor/shared.py#L236) | Transport generation advances only for a successful API connection; cfg drift is tracked separately. |
| [shared.py:266](https://github.com/VerdifyConsultancy/verdify-platform/blob/ed01cb8f64c9b3165924e4006a9bb3fe2a7e1920/ingestor/shared.py#L266) | `note_transport_connected` increments generation at275 and clears the prior readback/wake state. |
| [ingestor.py:4101](https://github.com/VerdifyConsultancy/verdify-platform/blob/ed01cb8f64c9b3165924e4006a9bb3fe2a7e1920/ingestor/ingestor.py#L4101) | The successful connect calls that helper at4114 and emits `action=awaiting_readbacks` at4118–4123. This is an actual connection boundary. |
| [shared.py:286](https://github.com/VerdifyConsultancy/verdify-platform/blob/ed01cb8f64c9b3165924e4006a9bb3fe2a7e1920/ingestor/shared.py#L286) and [ingestor.py:3360](https://github.com/VerdifyConsultancy/verdify-platform/blob/ed01cb8f64c9b3165924e4006a9bb3fe2a7e1920/ingestor/ingestor.py#L3360) | Completing all expected readbacks returns true only once per generation and emits `action=readbacks_complete`; ordinary subsequent readback callbacks do not emit that completion again. |
| [dispatcher.py:769](https://github.com/VerdifyConsultancy/verdify-platform/blob/ed01cb8f64c9b3165924e4006a9bb3fe2a7e1920/ingestor/tasks/dispatcher.py#L769) | `reconnect_pending = transport_generation > reconciled_transport_generation`. As long as it stays pending, every dispatch chooses the label `transport_reconnect`; choosing that label does not establish a new connect. |
| [dispatcher.py:800](https://github.com/VerdifyConsultancy/verdify-platform/blob/ed01cb8f64c9b3165924e4006a9bb3fe2a7e1920/ingestor/tasks/dispatcher.py#L800) | The pending replay's force event may reseed current-generation comparison values on later passes and emit another same-reason `seeded=...` line. This also need not be a new connection. |
| [dispatcher.py:1285](https://github.com/VerdifyConsultancy/verdify-platform/blob/ed01cb8f64c9b3165924e4006a9bb3fe2a7e1920/ingestor/tasks/dispatcher.py#L1285) | A held bounded-stage decision is retained, its immediate wake is deferred, and the error log combines the sticky dispatch reason with `action=blocked_stage` and the actual stage reason. It returns without completing reconciliation. |
| [shared.py:366](https://github.com/VerdifyConsultancy/verdify-platform/blob/ed01cb8f64c9b3165924e4006a9bb3fe2a7e1920/ingestor/shared.py#L366) | `defer_failed_dispatch` consumes only that pass's immediate scheduler wake. Its contract explicitly preserves the unreconciled generation and drift for the normal300-second cadence; it does not mark success or increment generation. |
| [shared.py:335](https://github.com/VerdifyConsultancy/verdify-platform/blob/ed01cb8f64c9b3165924e4006a9bb3fe2a7e1920/ingestor/shared.py#L335) | Only completed reconciliation advances `reconciled_transport_generation` and clears the force event when applicable. |
| [collector.py:137](https://github.com/VerdifyConsultancy/verdify-platform/blob/ed01cb8f64c9b3165924e4006a9bb3fe2a7e1920/scripts/experiment_v2_proof_packet.py#L137) and [collector.py:405](https://github.com/VerdifyConsultancy/verdify-platform/blob/ed01cb8f64c9b3165924e4006a9bb3fe2a7e1920/scripts/experiment_v2_proof_packet.py#L405) | `DESTABILIZER` matches any `reason=transport_reconnect`; all such parsed timestamps enter the max used as `stable_since`. It does not inspect action, generation change, retained hold state or one-time completion identity. |
| [guard.py:360](https://github.com/VerdifyConsultancy/verdify-platform/blob/ed01cb8f64c9b3165924e4006a9bb3fe2a7e1920/scripts/experiment_v2_readiness_guard.py#L360) | The unchanged guard requires observed time minus stable_since≥30minutes; this is the downstream consequence of the collector's event classification. |

## Bounded finding

The producer distinguishes a connection boundary from an unreconciled-generation dispatch reason. The collector loses that distinction. Therefore a held stage reporting on the normal cadence can indefinitely move its transport-stability clock, despite an unchanged actual socket/generation/Pod. Repeated `seeded` or incomplete-readback reports can have the same issue; skipping only the word `blocked_stage` would not fully correct the classification.

This does **not** prove the actual current hold is harmless, nor that a genuine reconnect was absent before the reported line. A single blocked-stage line could follow a new real connect. Its inner stage reason and durable stage state must be classified with the same source/UID/generation. Reconciliation remains incomplete in the producer until an actual successful pass; a healthy transport clock cannot substitute for that condition.

One closely related static defect is visible in the same calculation: collector405–413 sets `stable_since` to the maximum of every parsed matching timestamp plus Pod start, then414–423 counts matching timestamps strictly greater than that maximum. With the same unchanged log string, that count is necessarily0. No test or log replay was run to establish this arithmetic. The guard separately blocks `recurring_error_count != 0` at378, so a future correction must preserve meaningful unresolved-error/hold evidence rather than simply filtering lines and letting this counter silently stay zero.

## Smallest correct next observation

Root has assigned the sole owner one bounded read-only log/state classification. Bind current PodUID/container/source/generation and exact raw event ordering; identify:

1. Last actual successful connection (`awaiting_readbacks`), one-time readback completion, lease acquisition/loss/self-fencing/renew error and new grid-attestation boundary.
2. Whether each later blocked/seeded report is the same unreconciled generation and retained stage; preserve its actual inner reason, immutable stage/approval identities and disposition. Do not print credential or opaque approval values.
3. Whether any genuine later transition or distinct unsafe condition occurred. An unchanged generation/socket/restart count is supporting scope, not a universal proof of no failure.

No readiness SQL, state rewrite, dispatch replay, approval change, manual generation/reconciled mutation, source override or physical fault follows. Preserve22:10:42 and the old conditional22:40:42 calculation as historical algorithm output. Do not replace it with a newly inferred passing clock in the owner receipt. A real unresolved hold remains a separate actionable blocker.

## Smallest future correction, after hold classification

Keep the guard's30-minute threshold, all freshness/identity/generation/backup requirements and all original fail-closed behavior. Correct **only the collector's event classification/calculation**: distinguish actual connect/replay-completion/lease/new-attestation boundaries from repeated same-generation state reports, and separately retain/classify unresolved holds/errors so they cannot disappear as a consequence of clock correction. An explicit event/action predicate over existing log fields is sufficient; no new service, framework, physical permission or log format is justified here. Do not change the producer's safety hold or mark its pending reconciliation complete to make the clock pass.

The exact expected behavior for a later focused regression is finite: repeated same-generation blocked/seeded reporting cannot masquerade as another connection; a genuine new connect/generation, lease failure or new attestation still restarts the required interval; an unresolved unsafe hold remains fail-closed and recurring failures cannot be mathematically erased. Those checks were **not executed or added** in this source-only assessment. Root will assign the bounded source fix only after the actual hold semantics are distinguished.

`source-finding.json` records exact file hashes and line coordinates. No current readiness, new stable_since, proof/randomized authority or changed scientific/physical coverage is claimed. This assessment is complete.
