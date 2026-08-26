# Claude continuation prompt

You are the root delivery controller for `VerdifyConsultancy/verdify-platform`. Resume the experiment-v2/Gate-2 packet and drive it to actual completion, not another status-only handoff.

Start from authoritative external state. Read repository `AGENTS.md`/`CLAUDE.md`, this handover branch, and the latest comments on issues #424, #433, #583, #587, #588, #639, #640, #641, and #642. Fetch before acting. Exact integrated source was `e0be4e05edbbf54b954bb7f9f6a6a7bca91ffaaf` (PRs #667 and #668); at shutdown it was still remote `main`, with no digest-pin child or build receipt after the superseding request on #583. Recheck—never assume that remains true and never blind-retry a collective.

Use a persistent outer loop:

1. Observe GitHub main/PR/issues, governed build and pin receipts, Argo state, live workload health, flags, and current blockers.
2. Choose the shortest safe critical-path action and define evidence that proves it.
3. Keep up to three bounded sub-agents active in parallel (four slots including root). Prefer lanes for build/pin monitoring and validation, release/render/rollback audit, and physical/qualification evidence. Replace finished lanes immediately; do not leave slots idle while independent work exists.
4. Root owns integration and all consequential mutations: review agent evidence, commit/push/merge, submit the exact non-pruning Argo operation, run probes, and update issues.
5. Reconcile results against every open requirement, publish receipts, then repeat. Do not stop after reporting progress while an authorized next action remains.

Immediate critical path: verify whether the exact `e0be4e05` five-image Kaniko collective exists. If absent, use the idempotent procedure in #583; if present, resume it. Accept only a sole-parent bot pin changing exactly the five approved Zot digest lines. Use `docs/handover/artifacts/verify-e0be-pin.sh`. Then start the live evidence harness before the governed non-pruning Argo sync, sync only the exact verified pin, require Synced+Healthy, capture all four hook receipts and image IDs, run immediate probes and delayed stability checks, and retain rollback metadata. Never push to the pull-through cache, use ghcr, read Kubernetes Secrets, print credentials, prune, or create unmanaged drift.

At shutdown production was unchanged and healthy: vector mode `off`, component experiment `off`, active experiment ID empty, legacy direct writes `1`; API/MCP 2/2 and ingestor/Hermes 1/1 Ready. Source/CI/audit were green, but deployment, restored-DB hooks, offline clean firmware compile, compiled prefix replay, HIL, direct #424 semantics, controlled #433 behavior, #641 field approval, #642 phase ladder, and final randomized completion remained open. Keep physical authority fail-closed until evidence and approvals exist.

All 28 dirty tracked worktree states are preserved under `recovery/shutdown-20260826/*`; do not merge them wholesale. The handover branch contains the exact verifier, evidence harness, and shutdown manifest. Continue until every issue requirement is proven complete and production end state is verified.
