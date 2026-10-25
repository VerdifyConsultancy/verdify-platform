# October 25 placement and contained qualification

Product #1078 merged at `bf86663e` and is deployed Synced/Healthy with all
144 normal non-pruning sync tasks completed. Monitoring #1171 merged at
`382f656a`; both collectors load the same 14-rule Verdify file, including
healthy Pending/failed-rollout warnings. This packet retains scoped live
receipts; it does not certify unplanned recovery or the full experiment pilot.

Normal lab-origin template replacement succeeded. A separate node5 window
performed UID-bound Pod replacement, including one retained unscheduled-Pod
failure and retry, then reached Ready on node7 in 186.7 seconds. Node5 was
uncordoned at 11:33:09 UTC. The natural 11:35 publisher succeeded at 11:43:39 UTC;
both independent serving caches subsequently adopted the exact origin
manifest. All 238 public HTTP samples returned 200. The corrected project-env
smoke passed 9/9; the earlier system-Python import failure remains a failure.

The strict cordoned **Deployment template rollout** criterion is still open:
Pod replacement exercises a different controller path. The reviewable
`verdify.ai/placement-verification` origin-only annotation prepares that
qualification. No further cordon is permitted without Jason's approval.
Do not reinterpret the earlier 186.7 second operator-assisted replacement as
that missing test, or as an unplanned-host-loss RTO.

The read-only structural placement/storage analysis covers 4 workloads × 5 host
losses with 0 class-C cells. DB/Grafana/Hermes can use node4/node5; the lab cohort
can use untainted workers 4–7. Lost active RWO hosts remain class B, requiring
power-off/fencing proof and storage-infra#714. Current request headroom was
read back. The model does not certify peak demand, image-cache completeness
or whole-product availability. Missing Node.status.images entries are unknown.

The earlier ingestor baseline changed before our 11:25 sync: a new Pod was
created at 11:17:44 UTC, with the same ReplicaSet. Its actor is unproven;
descheduler logged zero evictions. The current Pod and existing socket stayed
unchanged across the relocation window. No task-issued ingestor restart or
ESP32 connection occurred. Do not claim continuity from the obsolete baseline.

The private five-case proof at `2c77dc20` passed within its declared synthetic
core-schema boundary. Final CI adds complete row/catalog/ACL/role/sequence
custody and exported before/after hashes with an exact case manifest. Earlier
failed attempts remain documented in the proposal and Git history. Active
exposure/exclusivity/recovery and authentic 21-field causal gaps remain open;
#639 stays 27/48 UNPROVEN. No physical acceptance or production LOGIN credit.
