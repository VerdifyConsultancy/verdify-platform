# Contained recovery: current resource and admission packet

Read-only evidence for #396 / #382 / #643, collected October 12, 2026 at 17:48–17:52 UTC from context `vallery`. Source is existing [PR #969](https://github.com/VerdifyConsultancy/verdify-platform/pull/969), exact head `ac89df3e24f5dd34b6d1c145a300eb908b1519a8`. The [structured receipt](contained-recovery-receipt.json) retains sanitized native resource metadata, capacity calculations, source identities and evidence hashes. No cluster, storage, database, device, provider or credential changes were made.

The current rehearsal has **zero CNPG Clusters, Backups or Services**, and no database PVC/PV or namespace-associated Longhorn volume. This is absent resource state, not CNPG hibernation. Six completed/failed Pods and 80 Jobs remain; `verdify-s2-grafana-proof` is scaled to zero. The only PVC/PV is the Bound 2Gi spool qualification volume, PVC UID `a7158960-36dc-439c-bd6f-14f1aa744bab`; its Longhorn volume is detached. This observation does not establish who removed the old database resources or whether their external archives remain available.

The reader ObjectStore `verdify-cnpg-pitr-reader` remains, UID `e6dd97f3-3323-4155-b339-c984734b3d18`. Its presence and the other ObjectStore's historical recovery-window status do not independently prove the backup and complete WAL remain readable. No Secret data or archive credentials were read for this audit.

An additional exact-name comparison against the twelve frozen A/B allocations in the original `cnpg-current274-frozen-storage.json` found zero surviving PVs and zero surviving Longhorn volume objects. The structured receipt preserves all twelve historical PVC/PV/volume names and the absent current identities. The original evidence file is unchanged.

`verdify-db-rehearsal-cluster` is Healthy / OutOfSync against main `b5bdb3c65b243c230b7864fa81b95f926b2e68ff`; its last recorded full sync was October 2 at `9e7525197109b939520819f82d33f49b9adf0b7c`. Namespace and Secret Applications report Synced / Healthy. The existing directory renders three Cluster declarations: original rehearsal (3 × 30Gi data + 10Gi WAL), original S2 (3 × 20Gi + 5Gi, declared hibernation), and integration (3 × 20Gi + 5Gi). A full sync can create all three missing objects. Its potential declared storage is 270Gi logical / 540Gi at R2; hibernation behavior must not be used to assume a smaller allocation without native proof. No sync was submitted.

The focused source correction makes the hibernation instructions conditional on the original exact Cluster and six PVC/PV identities being present. Missing resources require reconciliation of the complete desired-state delta before creation. Historical failed-target evidence and the accepted whole 952-object / 77-sequence / 42-study-table seals remain unchanged; they are not relabeled as current live proof.

## Exact next target

| Field | Value |
|---|---|
| New target | `verdify-cnpg-s2-integration`, namespace `verdify-db-rehearsal` |
| Current target UID, Pod/PVC/PV/Service UIDs | Unallocated; target does not exist |
| Historical source Cluster UID | `4f697776-df25-4e22-b930-0b76cf35496e` |
| Historical Backup UID / backupID | `8e3c2874-f16c-4ca2-b85d-09629910ac64` / `20261006T052358` |
| Recovery point | Timeline 1; inclusive `2026-10-06T05:38:19.219172+00:00` |
| Exact operand digest | `sha256:8b461e37d18aa049704eb6a9cde2ba9af0f955f8d3725e921070d2450bb2f137` (PostgreSQL 16.13 / Timescale 2.25.2) |
| Storage | 3 × 20Gi data + 3 × 5Gi WAL = 75Gi logical / 150Gi potential R2 |
| Resource requests / limits | 500m CPU and 2Gi memory per instance; total 1.5 CPU / 6Gi requested, 18Gi memory limit |
| Placement | Three distinct `topology.vallery.net/proxmox-host` domains; required anti-affinity; workers with Longhorn label |
| Authority | Reader-only recovery, no archive writer; Timescale background workers zero before first start |

These source/backup UIDs identify historical custody. They must never be used as the new target's UID. Capture the newly allocated Cluster, primary and replica Pod, six PVC/PV/handle and RW Service identities, database OID, system identifier, placement and running image before any native admission or fixture execution.

## Current capacity, before owner reservations

The scheduler's current requested-resource totals leave the following headroom. These are snapshot calculations, not an owner admission or a prediction of memory usage at the 6Gi-per-instance limit.

| Eligible node / physical domain | CPU request headroom | Memory request headroom |
|---|---:|---:|
| node4 / oro | 12.575 cores | 18.106Gi |
| node5 / opal | 18.530 cores | 27.816Gi |
| node6 / ore | 23.715 cores | 25.843Gi |
| node7 / onyx | 25.405 cores | 30.715Gi |

The Longhorn class remains R2, Retain, WaitForFirstConsumer. Eligible disks on nodes4–7 have about 1,440.518Gi aggregate conservative physical headroom after deducting both the 25% minimum-free threshold and each existing 10Gi disk reserve. This aggregate does not replace per-replica placement or concurrent reservation review. Node8 remains `dedicated=frigate:NoSchedule`; the new target has no toleration. Its storage remains replica-eligible, but no additional node8 owner reserve or pending estate allocation is assumed available. Control planes are cordoned and excluded.

The prior near-96% memory-request snapshot is no longer current. Present scheduling headroom is not the remaining blocker: **current storage/Proxmox owner confirmation, independent archive custody and the complete missing-resource desired-state delta remain unverified**, with Retry retaining the production/release/device lane.

## Validation and finite continuation

Fourteen supported offline/profile/restore-contract modules produced 87 passes and eight native-fixture skips. The three modules containing those eight tests then passed all 34 tests using a disposable private Unix-socket PostgreSQL 16.15 Homebrew fixture, with network listening disabled. The union is **95 distinct passed tests, zero remaining skipped**. These synthetic SQL rollback/refusal and result-equivalence checks do not certify a restored PostgreSQL 16.13 / Timescale target, complete data parity or recovery timing. The unchanged PR directory renders successfully with zero Secrets.

1. Reconcile the complete missing-resource delta with the existing storage/Proxmox owners and coordinate the execution lane with Retry. Independently revalidate the exact retained backup and covering WAL through the reader authority. Confirm current reservations and required three-domain placement.
2. Once admitted, deliver the bounded exact target desired state and collect fresh target/storage/endpoint identities. Do not recreate original initdb/archive-writer targets as an incidental full-sync effect.
3. Reuse the existing full predecessor/COPY custody, rollback-only 275 bridge, independently reviewed exact install, separate rollback-only native admission, reviewed install and independent post-readbacks. Preserve the original failed targets and seals; a mismatch is a failed qualification, not a replacement baseline.
4. Exercise current-source ordinary authenticated clients and six duty fixtures with inverses; prove endpoint reversal and whole 952/77/42 reseals. Measure actual restore, failover, RPO and RTO separately.

No new recovery framework, production DB cutover, live fault, device client or physical experiment authority is introduced. The next owner confirmations are routine coordination inside the authorized contained lane. Any changed production outage, cutover or physical scope belongs to Retry and Jason's explicit decision.

## Archive verification and source correction, 17:58–18:01 UTC

The subsequent [archive custody recheck](archive-custody-recheck.json) passed with one bounded reader-only Job, UID `40f2d3fe-708c-42df-8232-f273082e0cba`, Pod UID `96dcbabe-fd0c-46c6-8d74-656a5fba09be` on node7. It ran from 17:58:04 to 17:59:41 UTC, allocated no PVC and performed no database/device writes. The [exact Job definition](archive-recheck-job.json) uses the unchanged independent reader's pinned image and Secret references; no Secret API or Secret data was read. Compared with the original reader, the bounded change also streams the full exact base tar rather than only HEADing it.

The complete 619,166,118-byte base tar is currently readable, SHA256 `c3b56cd2ee1fa9612822228af7c0c0f1334a2b7c877836a6989bcecc024cd164`. The backup.info and all required timeline1 WAL segments ending `00000072`, `00000073` and `00000074` were retrieved, with byte sizes and SHA256 matching the original protected archive receipt exactly. This proves current archive custody through the retained target-crossing transaction; it does not prove a new restored database or new target admission.

The [integration-only source receipt](integration-only-source-receipt.json) records the follow-on correction to the earlier PR969 render described above: the rehearsal kustomization now declares one literal integration recovery Cluster and the existing reader ObjectStore, plus isolation/client policies. It no longer renders either original initdb/archive-writer Cluster, the writer ObjectStore or ScheduledBackup. All four historical source profiles and prior seals/failures remain unchanged. The reader has no retention writer authority; its complete live spec, default-deny policy and CNPG isolation policy [match the declared source](live-reader-policy-readback.json).

The actual rendered directory passed a meaningful refusal test against accidental original initdb/archive-writer recreation. Four focused manifest/profile modules pass all 44 tests with zero skips. The eventual contained delivery vector is exactly the integration Cluster, reader ObjectStore and two integration client NetworkPolicies; all four are namespace-local and have no hooks, Secret or migration component. Existing isolation policies are verified prerequisites; a broad shared-Application sync remains outside this lane.

No integration target has been created. The remaining admission must account for its 150Gi potential physical allocation, 6Gi requested memory and three physical domains together with the current five-image CI caller, worker/host thin provisioning and shared growth reservations. Point-in-time headroom is not a reservation grant. Existing Storage and sole release-owner chats have received the exact packet; validation/merge and allocation are coordinated through those owners and the project coordinator.
