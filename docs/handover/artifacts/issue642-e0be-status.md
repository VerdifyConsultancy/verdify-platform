Controller reconciliation — 2026-08-26 02:22 UTC.

The feature-off integrated source and the accelerated physical-evidence preparation are now on exact `main`:

- PR #667 → `a754e873e8c42bfbb05a4c808bdf023c6462691a`: combined runtime-boundary/Gate-2 release, restore rehearsal, migrations 214–218, ordinary and experiment role bootstraps, six duty workers, Hermes Cortex/backport deployment, and shadow packet generator.
- PR #668 → `e0be4e05edbbf54b954bb7f9f6a6a7bca91ffaaf`, sole parent a754: source-locked shadow inputs, existing-connection 48-setter/48-readback live-grid attestor, offline prefix-replay preparation, and disconnect-race fences. The squash tree is byte-identical to independently audited head `8339d422…`.

Validation on the final tree: exact-base full `make ci` returned `ALL GATES GREEN`; in-cluster Argo PR CI passed; independent audit returned GO; dynamic fault injection proved disconnect during either `connect()` or entity enumeration publishes zero generation, inventory, subscription, or dispatch. The capability remains deliberately unqualified and OFF; `GRID_REVISION`/`ORDER_REVISION` are provisional.

Planner reliability also has one real natural-schedule receipt after the Cortex cutover: SUNSET fired naturally at 01:42:35Z, dispatched one Hermes run, completed 12 MCP calls, and durably wrote plan `iris-20260825-1944` at 01:58:37Z. Ledger/delivery are both `plan_written`; public planner health is `ok`, required/missed/overdue counts are zero, and all observed API/MCP/Hermes/ingestor pods remained Ready with zero restarts. This is planner-path evidence, not experiment shadow or physical evidence.

Production has **not** rolled to e0be. Live API/MCP/ingestor remain on the prior known-good digests/revisions; no experiment-v2 worker is live. Safe ConfigMap truth remains vector `off`, component `off`, active experiment empty, legacy direct writes `1`. Zero experiment-owned device calls were made.

Two non-user Root actions are on the deployment critical path:

1. The governed fixed-source five-image collective is requested, with old/new revision race preflight, in #583 comment `5419647864` under fixed name `verdify-platform-ci-recovery-e0be4e05edbb`. No start receipt exists yet.
2. The protected Gate-2 SOPS/Secret reconciliation requested in #583 comment `5418586882` still has no metadata-only receipt.

The controller has the exact-pin verifier, full non-pruning Argo plan/apply sequence, evidence harness, hook ordering, live rollback snapshot, and singleton-ingestor rollback ready. After both Root receipts and the bot pin land, the next action is the exact-revision sync plus live source/grid capture. #641 scoped/combined approvals and #642 randomized-day-1 approval remain later physical decisions; none is being inferred from this source/deployment work.
