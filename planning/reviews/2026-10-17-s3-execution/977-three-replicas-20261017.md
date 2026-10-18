# #977: three-replica database policy delivered

Verified 2026-10-18T02:29:34.727673+00:00. Jason selected **restore three replicas**. Storage owner commit [`55973ba5c17cfe325bb344dc3448a29a9e7648d1`](https://github.com/jvallery/storage-infra/commit/55973ba5c17cfe325bb344dc3448a29a9e7648d1) records decision A6 and removes only Verdify's exact `distinct-nodes` waiver from both protected-tier policy representations. The protected member and every unrelated policy entry remain.

Volume `pvc-59fe138d-1cc4-4aac-bfcb-3d332f847411`, UID `ce8bee30-faf5-41a6-a086-d9237fc78615`, is **attached / healthy**, desired replicas 3, with no pending rebuild. The original two replica UIDs were preserved.

| Worker | Replica suffix | Replica UID | State |
|---|---|---|---|
| vm-k3s-node5 | `a0948cb2` | `672ad232-aa51-4261-bd71-b28372b456d7` | RW / running |
| vm-k3s-node8 | `c5470818` | `afa40551-d43c-4387-9ff5-f5ab7d226be3` | RW / running |
| vm-k3s-node7 | `eb4a73da` | `39d0aa79-8a8f-4120-9539-a5b39da99fc1` | RW / running |

The new node8 copy uses eligible disk `nvme-s7u5nj0xa01268w`; post-change free capacity was 806.05 GiB / 88.01%, above its 35% policy floor. Source and target workers remained Ready with no Memory/Disk/PIDPressure. Capacity, current workload requests/working sets, existing instance managers, empty maintenance reservations and zero concurrent rebuilds were checked before the one serial controller rebuild.

The database pod UID remains `b1fa2c0e-a995-481f-a29f-4cfaad4e697b`, Ready with **zero restarts**. `pg_isready` reports accepting connections. No restart, remount, trim/reclaim or backup-route change occurred.

Argo's exact-resource operation at the owner commit succeeded for only `longhorn-system/longhorn-protected-tier`, UID `41e650b3-d852-404f-88f3-64222ec107f8`. Both live data keys equal committed source. The Longhorn application stayed Healthy; its preexisting unrelated `longhorn-default-setting` OutOfSync condition was preserved. No whole-app sync, pruning or hooks were requested.

Existing B2 backup `backup-95cccd4a675e430e` (UID `19e97208-7271-47a3-94f7-eb3537df720b`) remained **Completed 100%**, created 2026-10-17T12:53:57Z; the B2 target remained Available. This is post-change backup metadata verification, not a new backup, restore, failover or broader durability rehearsal.

Validation: survivability, chart-version and trim policy gates passed; 39 focused survivability tests passed; diff check and exact policy preservation passed. No PR or status check was enforced; source was fast-forwarded to main with linear history preserved. The automatic full `storage-infra-gates` run reported cancelled, so full CI success is not claimed.

All three #977 acceptance clauses now have evidence: owner decision/source policy, exact post-decision placement/health/backup metadata, and separation of optional reclaim. This record grants no authority for that optional operation. Detailed sanitized metadata is in `977-three-replicas-20261017.json`; the private 80-file custody manifest remains separate.

ROOT independently repeated the exact live readback and [accepted all three scoped clauses](https://github.com/VerdifyConsultancy/verdify-platform/issues/977#issuecomment-6244758504), closing #977 as completed. This is the only new accepted closure represented in this draft.
