# Source-owned field handoff (#1004)

The initial SSA zero-diff preserved some unclaimed live fields; it was not complete reconstruction proof. The field-set audit found four scheduling labels and two spread policies missing from source. These patches request Argo server-side apply for exactly the 14 extant issue-owned workload objects and the eight descheduler resources. They add SSA resource metadata and declare existing live scheduling values: rebalanceable=true on API/MCP/Lab/Planner, and the existing Lab/Planner hostname spread policies. These desired-source additions reconstruct the current live Pod templates; no live image, scheduling, replica, CronJob suspend or resource protection change is intended. The retired writer-watchdog is not recreated. The descheduler Namespace retains `Prune=false,Delete=false`.

Deliver only after the unchanged `verdify-prod-dark` Application declaration has its verified Agents catalog owner (#4461). Keep manual product sync and pruning disabled. The production Application's storage ignores, database/PVC protections and fail-closed sole-writer contract remain unchanged. Use the normal attended full, hook-bearing, non-pruning sync after reviewing the complete drift; never submit a selective product sync or move/restart the writer as a verification tool.

The descheduler remains separately pinned in the Agents catalog. After this source is accepted, review and deliver its exact Application pin through that owner, preserving suspend=false and its existing enforcing schedule. Do not force a diagnostic Job or change its Namespace labels.

Read back the actual Argo SSA manager and field sets before crediting ownership transfer. Confirm source plus API-defaulted specs reconstruct the live specs, every existing resource UID and Pod template is unchanged, and the original writer socket/Lease remains intact. A dry-run, history erasure or manager name alone is insufficient.

Historical kubectl manager entries may remain after real SSA. Retire only the individually inventoried entries after checking their exact fields against the new Argo-owned/source-defaulted spec and retaining a private reversible preimage. Any uncovered functional field must be reconciled in source before its manager is retired. Preserve controller/status managers and all spec values. Use UID/resourceVersion preconditions and metadata-only operations; do not blanket-clear `managedFields` or claim historical cleanup as field ownership proof.

Finish with actual managedFields readback, an empty normalized server-side desired-spec diff and live product/descheduler health. Keep #1004 open until the separate Application-spec and all listed workload criteria are satisfied. This runbook authorizes no device connection, credential action, data deletion or shared CI change.

## Delivered 2026-10-21

Agents #5263/#5272 adopted the unchanged production Application under the central catalog. Product and descheduler completed full non-pruning syncs at `f9f8f45c9c912499acdcb5b0fd7421937ab07a91`, with actual Argo Apply ownership and unchanged functional payloads. All 23 audited targets have no kubectl manager; the 22 workload/descheduler server-side source diffs are empty. [Central receipts](https://github.com/jvallery/agents/tree/main/docs/evidence/verdify-1004-delivered-20261021) preserve the bounded ownership and live verification evidence.

A completed full operation was initially reused without applying the new annotations. The reported success was rejected by live field readback; the exact-revision atomic operation/state-clear in the laptop runbook then ran fresh hooks and actual SSA. Always verify actual fields and hook freshness after the sync. No device connection probe or writer rollout is part of this procedure.
