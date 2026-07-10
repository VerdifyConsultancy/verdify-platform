# Device-writer async/state-machine critic report

Verdict: **ACCEPT** at reviewed implementation head
`e4e1c5901d2ce46400df65d9ced516bed25c0eb2`. No P0/P1 blocker remains.

## Review method

The independent critic first rejected the implementation and adversarially
tested cancellation, queued-state interruption, command timeout, callback
timeout, restart, partial delivery, cross-generation work, stale-client and
Lease fencing, old-retry ordering, logical supersession, atomic NOTIFY claim,
CAS regressions, bounded queue/fairness, failed-dispatch cadence, and runtime
log truth. The worker fixed every blocking finding and requested a fresh pass
over the resulting snapshot.

## Accepted properties

- Exact cfg identity derives from the real ESPHome wire slug, not the C++ id.
- Dynamic readback-only values cannot masquerade as reconnects or wake delivery.
- Only a real API connect advances transport generation.
- Every physical send rechecks generation, client identity, and Lease inside the
  sole writer lock after pacing.
- Queue and batch size, API await, lifecycle callback await, callback attempts,
  and immediate retry attempts are bounded.
- An exhausted lifecycle callback fail-closes and raises the fatal main-task
  monitor so Kubernetes restarts; it cannot leave a false-green writer.
- Immutable producer tokens are mapped into one local ordering domain; an old
  retry cannot overwrite a newer request, including mixed DB/default callers.
- Atomic pending-row claim and CAS transitions prevent duplicate notification
  delivery and state regression. An in-flight physical result may preserve the
  logically superseded terminal state without lying or fail-closing.
- Failed reconciliation does not mark its transport/drift generation complete,
  and its immediate wake is consumed without creating a one-second retry storm.
- Classified runtime evidence counts one canonical persisted terminal event and
  computes unchanged-anchor count from observed state rather than a constant.
- The two coordination-approved lifecycle consumers say
  `terminal_unconfirmed`, not `terminal_unsent`.

## Verification

- Focused writer/dispatcher/listener suite: **46 passed**.
- Ruff on every changed Python surface: **green**.
- Final critic response: `ACCEPT - no remaining P0/P1 blocker found in the
  current snapshot.`

The critic did not review or approve a production rollout and does not claim
LANE-AC-05. Release control still owns the exact-digest two-hour steady-state
window.
