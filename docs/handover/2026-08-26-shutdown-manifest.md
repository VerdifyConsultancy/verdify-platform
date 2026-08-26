# Shutdown preservation manifest — 2026-08-26

## Canonical release state at capture

- Remote `main`: `e0be4e05edbbf54b954bb7f9f6a6a7bca91ffaaf`.
- PR #667 merged as parent `a754e873e8c42bfbb05a4c808bdf023c6462691a`.
- PR #668 merged as `e0be4e05`; Argo PR CI succeeded and the independently audited source tree was green.
- Issue #583 contains the superseding exact-source build request. No later build-start, collective, or pin receipt was present at capture.
- No production sync of this release had occurred.
- Live non-secret flags: vector `off`, component `off`, active ID empty, legacy direct writes `1`.
- Live readiness: API 2/2, MCP 2/2, ingestor 1/1, Hermes 1/1.

## Durable local-work preservation

Every worktree with a tracked diff was captured without altering its index or working files. Twenty-eight exact working-tree snapshots were pushed atomically beneath:

`recovery/shutdown-20260826/*`

For each snapshot, the recovery commit tree was compared to the corresponding worktree and returned `PASS`. Important current-packet snapshots include:

- `recovery/shutdown-20260826/main-8f9e011b8e18`
- `recovery/shutdown-20260826/fix-experiment-v2-feature-off-adoption-0dfcbe27b5b1`
- `recovery/shutdown-20260826/lane-c-arbiter-delivery-ac73e1374d8f`
- `recovery/shutdown-20260826/lane-e-policy-engine-7d795992cfc1`
- `recovery/shutdown-20260826/qualification-machinery-2dd97208e281`
- `recovery/shutdown-20260826/fix-hermes-cortex-route-b2ad7bff79f5`

Most other snapshots contain stale/managed `.agent-workflow` deletion churn from old worktrees. They are preservation refs, not merge candidates. Inspect and cherry-pick deliberately.

The untracked root `.kube/` and `.mcp.json` were intentionally excluded: they are runtime/credential surfaces, not source artifacts. The interrupted ESPHome compile used synthetic substitutions; it produced only partial objects and no final firmware binary, so no binary is represented as qualified evidence.

## Handover artifacts

- `2026-08-26-claude-continuation.md`: under-4,000-character execution prompt.
- `artifacts/verify-e0be-pin.sh`: strict sole-parent/five-digest pin verifier.
- `artifacts/live-combined-evidence/`: read-only hook/workload/public-health evidence harness to start before Argo sync.
- `artifacts/issue583-e0be-collective.md`: exact superseding build request already posted to #583.
- `artifacts/issue641-e0be-status.md` and `artifacts/issue642-e0be-status.md`: last pre-crash status packets.

Validation at capture:

- Claude prompt length: 3,138 characters including its heading.
- Both shell artifacts pass `bash -n`.
- The strict pin verifier accepts the known-good five-digest fixture `e257fa06..96d9b9dc` when its base constant is substituted, exercising its full grammar.
- The archived live harness dry-run against `e0be4e05` passed: four of four exact hooks absent, feature-off state passed, public health passed, and it reported no mutation, Secret read, full ConfigMap read, or Argo action.

## Issue authority

The active packet is #424, #433, #583, #587, #588, #639, #640, #641, and #642. Each issue must point to this branch/commit and the central shutdown receipt on #583. Keep all open until its own acceptance criteria are evidenced; a merged source tree or feature-off deployment does not close physical qualification or study execution.
