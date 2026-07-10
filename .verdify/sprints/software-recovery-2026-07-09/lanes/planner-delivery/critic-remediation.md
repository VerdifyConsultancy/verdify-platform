# Planner-delivery critic remediation

Substantive remediation head: `8b2fdeca8184efad720b3e8ad7303dcb6012d6c2`

This is the response packet for the independent `CHANGES_REQUIRED` verdict in
`critic-report.md`. It records code disposition only; it does not supersede the
critic's original report or authorize production migration, rollout, restart,
alert mutation, plan acceptance, or device action.

## Finding disposition

1. **Hermes false-green client state:** the embedded stdlib-only probe now
   reduces timestamped Hermes client logs after PID 1 process start, parses the
   exact configured tool names, distinguishes disconnect/recovery from fatal
   reconnect exhaustion, uses `python3`, and has independent readiness and
   liveness modes. Persistent PVC history cannot fail a new process.
2. **Stale planner attempts:** `set_plan`, `set_tunable`, and
   `acknowledge_trigger` share one transaction-local lock helper. A write needs
   the exact pending `plan_delivery_log` row and, for scheduled events, its exact
   linked `delivered` ledger row. Both terminal updates use conditional
   `RETURNING` fences so a late loser rolls back all materialization.
3. **Invalid current plan coverage:** `set_plan` fetches PostgreSQL `now()`
   inside the write transaction and rejects not-yet-valid, expired, and
   first-transition-in-the-future plans. It preserves the approved 72-hour
   envelope without inventing a minimum final-transition horizon.
4. **Active-plan lifecycle drift:** `v_active_plan` now treats an effective,
   current-valid journal row plus `is_active` as eligibility for full Iris
   plans. Finite active one-shots and active non-Iris sources are explicit.
   Disposable fixtures prove a newer-created superseded full plan cannot win,
   and an inactive row from the effective plan cannot be resurrected.
5. **Permanent terminal NULL escape:** rolling-compatibility triggers derive
   deterministic terminal evidence for old writers and clear it when an old
   retry returns to pending/delivered. Strict constraints then allow NULL
   terminal evidence only for true nonterminal states.
6. **planner_graph lease fencing:** every worker has a pod/process/nonce owner,
   renews throughout graph execution, refreshes before terminal commit, and can
   write completed/failed only while the owner and unexpired lease match. Real
   PostgreSQL takeover tests reject both stale terminal paths. The Deployment
   uses `Recreate` as an additional rollout-overlap defense.
7. **WEEKLY wire gap:** `WEEKLY` is now present in schema, routing, database
   vocabulary, prompt routing, and immediate wake behavior. It remains outside
   the required daily SLA while retaining its intended `set_plan` strategy
   action.

## Verification

- Root offline remediation slice: 217 passed.
- Full `planner_graph` suite: 64 passed.
- Disposable migration apply, rerun, fixture, and rollback: PASS.
- Production kustomize/kubeconform render: 90 resources; 84 valid, 0 invalid,
  0 errors, 6 expected schema skips.
- `make lint`, changed-file planner ruff, `git diff --check`, and migration
  rollback-wrap classification: PASS.
- `make test`: live-stack target is not laptop-portable; after five stale
  issue-427 assertions were fixed and rerun green, 140 inherited environment
  failures remain. See `evidence.yaml` EVI-PLAN-013.

The next gate is exact-head GitHub CI followed by a fresh independent critic.
