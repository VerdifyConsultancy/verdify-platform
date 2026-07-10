# Planner-delivery independent critic report

- Verdict: `CHANGES_REQUIRED`
- Reviewed substantive head: `402251ad8e6ce8d704d3d063022ae8f31c3e34df`
- Reviewed records head: `b42a28f5a202946da8e18ad3b3ca19e02f90f7db`
- Reviewed at: `2026-07-10T15:45:21Z`
- Review role: fresh read-only distributed-state/planner contract critic

## Blocking findings

1. **P0 — Hermes readiness cannot execute.**
   `deploy/k8s/components/hermes-iris/hermes-iris.yaml` invokes `python`, but a
   read-only probe of the same pinned upstream image found only
   `/usr/bin/python3`. The proposed rollout would remain permanently Unready.

2. **P0 — Hermes readiness observes the MCP server, not Hermes's dead MCP
   client.** The embedded probe checks config text plus Verdify MCP `/readyz`;
   Hermes liveness remains TCP. After Hermes logs `failed after 5 reconnection
   attempts, giving up`, the MCP server can be healthy while Hermes remains
   disconnected. Readiness must fail on the current post-start disconnect and
   liveness must restart a post-start fatal give-up. Persistent pre-start log
   history must not poison a fresh pod. Exact allowlist parsing must not let
   `lessons` match `lessons_search`.

3. **P1 — stale planner attempts are unfenced.** `set_plan` accepts a pending or
   timed-out delivery without locking and proving that its trigger remains the
   current expected-ledger attempt. A late attempt can supersede a newer valid
   plan, overwrite ledger state, and still return success. The fix needs a
   current-attempt/row lock or generation fence, checked conditional terminal
   writes, and two-connection out-of-order tests.

4. **P1 — invalid plans can satisfy a required cycle.** Already-expired and
   future-gap full plans pass relative-order validation, deactivate the prior
   plan, and receive terminal `set_plan` success. Required plans need DB-time
   current validity, non-expiry, present/current waypoints, and required
   forward coverage before any supersession or terminal completion.

5. **P1 — the dispatcher view ignores journal singularity.** The unique
   partial index covers `plan_journal.lifecycle_status`, but `v_active_plan`
   reads `setpoint_plan` only. A disposable PostgreSQL fixture reproduced a
   newer superseded plan winning over the effective plan. The device-facing
   view/state must exclude superseded full plans while explicitly preserving
   the intended one-shot model.

6. **P1 — retained planner_graph has no lease fencing.** Every pod uses owner
   `planner-worker`, the 30-second lease is never renewed, terminal writes do
   not require the current owner/status/lease, and RollingUpdate permits an
   overlapping worker. Use a unique worker identity, lease renewal or token,
   conditional terminal writes, and stale-owner/reclaim tests; Recreate may be
   added only as defense in depth.

7. **P2 — terminal-pair constraints remain optional forever.** Migration 196
   allows terminal statuses with both `terminal_action` and `terminal_at` null.
   Backfill/derive legacy terminal evidence and tighten the constraint in a
   safe schema-first sequence, or add an explicit post-rollout tightening
   migration. The current PR must not claim durable pair enforcement while the
   compatibility escape remains permanent.

## Accepted evidence

- Exact substantive and records heads completed with 28 successful GitHub
  checks, eight intentional skips, and no failures or pending checks.
- Scope stayed inside owned paths plus the two controller-approved shared-path
  grants; firmware, dispatcher/device-write, and Grafana paths were untouched.
- DLI-unavailable context and generated ConfigMap source parity were preserved.
- Migration classification/blank restore, bounded planner_graph retry, and the
  required service-restart note were credible.

Green CI did not exercise the seven failures above. No merge, migration,
production rollout, alert mutation, stale-intent retirement, device write, or
OTA is permitted until a fresh critic accepts the remediated immutable head.
