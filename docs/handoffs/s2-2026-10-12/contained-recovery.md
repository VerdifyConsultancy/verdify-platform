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

No integration target has been created. The remaining admission must account for its 150Gi potential physical allocation on Longhorn NVMe, 6Gi requested memory and three physical domains. The actual legacy CI caller uses root-filesystem emptyDir: its root/thin growth must be budgeted separately from the physically distinct passthrough NVMe allocation. Truly shared RAM, image/cache/log growth and any actual shared backing still need coordinated accounting. Existing occupied bytes are not charged a second time as future growth. Point-in-time headroom is not a reservation grant. Existing Storage and sole release-owner chats have received the exact packet; validation/merge and allocation are coordinated through those owners and the project coordinator.

## Exact-input emitter and physical-backing correction

Preparing the existing bridge CLI with the complete 44,929,390-byte sealed274 witness exposed a real source failure before any SQL execution: `configured()` selected the integration name as the C0 source profile, but the independently pinned C0 emitter intentionally admits only its original rehearsal/S2 profiles. The correction keeps the original S2 source profile and lets the existing physical-target remap bind its one exact guard to the integration target. The C0 allowlist, original S2/frozen behavior, migration275 bytes, predecessor witness and historical failures remain unchanged.

An unmocked regression exercises that witness-emission path, verifies the integration guard and executable digest guards, and verifies that the C0 emitter itself still rejects the integration name. The integration/current275/target-runtime modules pass **95 tests, zero failures, zero skips**, with the native fixtures running on a private local PostgreSQL Unix socket. The real sealed-input CLI now emits the rollback-only bridge successfully. Three private SQL artifacts are prepared: that existing bridge, a full read-only predecessor witness capture and a read-only native identity probe. None has been sent to a cluster or database. Their exact input/output hashes and limits are in the [preparation receipt](native275-preparation-receipt.json); private PostgreSQL16.15 fixture success does not prove the actual PG16.13/Timescale restore or ordinary LOGIN authentication.

The [current backing readback](physical-backing-summary.json) independently matches workers4–7 to their Proxmox VMs: `/`, k3s and kubelet use guest `/dev/sda1`, backed by each VM's QEMU `scsi0` thin volume. Both Longhorn mounts on every worker use distinct passthrough Samsung1TB NVMe serials; the hypervisor thin pools use separate boot-device serials. Thus the recovery's 150Gi R2 allocation is not an allocation against those root thin pools. This is physical identity evidence, not a storage reservation or authorization to create the target.

The release coordinator is integrating one final source cohort. This correction is published only on the independent `codex/contained-recovery-20261012` branch; PR969's branch stays at its already verified `38efbd834a08577b124e3aff7f122697b9f04f71` head. There is no second PR, main merge, build submission or target sync from this worker.

A separate [full-output preservation receipt](original-275-output-preservation.json) compares the complete historical S2 and frozen-B rollback SQL emitted from original `ac89df3e24f5dd34b6d1c145a300eb908b1519a8` source against correction `89e76357a8579101a17e56b1951f40c3a227aad4`, using the exact sealed274 input. Both outputs are byte-identical. This receipt adds no source behavior or execution.

The [full951 COPY preparation](full951-preparation-receipt.json) retains the existing ordered-marker/terminal-marker protocol and accepted original 9,699,549-row baseline. Its read-only SQL adds a literal integration/database/OID/native-primary guard; fresh Cluster/primary-Pod UID binding remains mandatory before execution. The original incomplete S2 capture remains failed evidence and is not used as an accepted replacement baseline. Post275/native-admission controlled ledger and receipt deltas, the whole952 inverse seal, all 77 sequences and42 study tables remain separate actual-target checks.

## Candidate-revision delivery is supported

The [candidate-operation preparation](candidate-sync-preparation.json) verifies the actual installed API, Application and AppProject. `operation.sync.revision` can select frozen PR973 `3d9f31e8274d131e1bb2178bd9edb24cf66eb347` while `spec.source.targetRevision` remains `main`; the Application remains manual and prune off. The project allows the repository, namespace and four selected resource kinds, with no sync window or revision restriction. The exact four-resource, no-prune operation passed a server dry-run; a subsequent live read verified that no operation persisted and source/policy were unchanged. This establishes a supported mechanism, not an executed restore.

Current capacity observations include all active Pod requests, no Pending Pods, four eligible physical domains and healthy Longhorn disks with sufficient headroom. The release owner explicitly coordinated its existing PR validation and later serial builder obligations with the new target's 6Gi requested / 18Gi aggregate memory limits. Those builds use separate root/emptyDir storage; the exact CNPG image is already cached on all four eligible workers. Before execution, exact frozen-head native CI must succeed and current placement, memory, reader/isolation identities and idle Application must still fit. Final-main delivery and production image publication are no longer inferred prerequisites. Source, candidate operation, actual restore/native admission and production acceptance remain separate evidence.

## Actual candidate restore started

The exact candidate four-resource operation succeeded on October12 at18:32Z without changing the Application source or manual/prune-off policy. The [apply receipt](actual-restore-apply-receipt.json) distinguishes the normalized omitted-false status field from the submitted `prune:false`; only the four reviewed resources ran, with zero hooks/prunes and no original cluster recreation. The stopped local assertion is preserved and the operation was not resubmitted.

The new primary has authoritative Cluster/Pod/Service identities, database OID 16447, system identifier7693399121498484768, PostgreSQL16.13, Timescale2.25.2, vector0.8.1 and zero Timescale background workers. Its complete predecessor witness equals the exact sealed274 SHA256. The [native predecessor receipt](actual-native-predecessor-receipt.json) records fresh capacity and identities. Complete COPY,275 bridge, native admission and ordinary authentication are still separate pending checks. After automatic approval review rejected a whole-row export to the Mac, the safer approved COPY path runs the same streaming parser inside the isolated primary and returns only counts and hashes; no raw row file leaves or persists in that path.

## Actual predecessor data and rollback qualification

The [complete951 COPY receipt](actual-full951-pre275-copy.json) verifies every ordered relation marker and the terminal marker, with9,699,549 rows and all951 per-relation row-multiset hashes exactly equal to the accepted original baseline. The2,457,973,078-byte stream was consumed entirely inside the isolated primary; only counts and hashes were returned, and no raw row file was exported or persisted. The [execution receipt](actual-full951-pre275-execution.json) binds source SQL/parser hashes and the new Cluster/Pod identity. Peak parser RSS was499.6MiB.

All77 sequence states [equal the retained frozen-A baseline](actual-pre275-sequence-receipt.json). The [42-study derivation](actual-study42-pre275-derivation.json) finds the exact complete COPY header/count/SHA256 for every retained study table and confirms each historical42-capture row count; it does not equate the older MD5 serialization to COPY SHA256. The three historical admission receipt rows remain captured separately.

The [actual275 rollback qualification](actual275-rollback-receipt.json) produced the exact expected7089e3bc post-witness. An [independent witness after rollback](actual275-after-rollback-receipt.json) still equals the sealed274 predecessor6d896a5d. This proves rollback qualification, while the committed275 bridge and separate native admission remain in progress. The [scientific-role metadata](actual-scientific-role-metadata.json) shows the two pre-existing scientific logins retain exact memberships and null passwords; no credentials/grants have been changed. The restored component remains draft/shadow/closed with component execution disabled.


## Committed275 and separate native admission, 18:49–19:03 UTC

The first committed bridge attempt reached its uncommitted result emission but
blocked the duplex SQL/result pipe. The exact owned backend1253, transaction
start18:40:17.526105Z, database/application/last-query/state and local client
were rebound before terminating only that session. A fresh read proved it gone
and275 uncommitted; the [independent full witness](actual275-after-abort-receipt.json)
again equaled sealed274. The [abort receipt](actual275-transport-abort.json) and
private failed attempt remain preserved. The correction changed transport only:
exact same reviewed SQL SHA2bd781dd was staged by hash inside the UID-bound Pod,
then `psql -f` wrote private local output and returned only metadata.

The [committed275 receipt](actual275-installed-receipt.json) and
[independent readback](actual275-installed-independent-receipt.json) both bind
post-witness7089e3bc. The separate native admission [rollback review](actual-admission-rollback-source-review.json)
preserved every pre-existing identity/catalog/grant fact; its independent
rollback witness remained exactly7089e3bc. The [actual install](actual-admission-installed-receipt.json)
then passed the existing source validator, permitting only the enumerated new
receipt relation/index/constraint OID differences from rollback qualification.
An [independent full read](actual-admission-installed-independent-receipt.json)
equals installed4355a2c83fbb260c3a6a5c483c46369dd115a24258bbdeb260d59f49557112b5.
The native admission record SHA is1fb737992a058f16007d9dd5c47d763e3231147eeeab8bd668aedd2b229c86aa.

The [complete prefixture952 receipt](actual-prefixture-full952-copy.json)
contains 9,699,553 rows, with all ordered and terminal markers verified and no raw
row export/persistence. Relative to the accepted original951,949relations are
exact; only the ledger's one275 row and the two ordinary canonical receipt
updates changed. The new native receipt relation has exactly three rows. This
is a controlled successor baseline, not replacement of the old951 seal.
[All77 sequences](actual-prefixture-sequence-receipt.json) and
[all 42 study tables](actual-prefixture-study42-derivation.json) remain exact.
Temporary target-only password provisioning, actual ordinary authentication/
rollback duty behavior, scientific READ ONLY consumers and final inverses/reseals
are the next finite checks. Production remains untouched by this lane.


### Actual authentication, source consumers and exact final reseal (2026-10-12)

The contained target passed all 107 allowed/denied cases across the six ordinary
planner, setpoint-server, HA-backfill, vision, lab-publisher and Grafana LOGINs.
Each proof used the real LOGIN as both current and session user against the bound
primary/database OID 16447; the frozen role fixture ran inside an outer rollback.
This establishes the scoped SQL role contracts, not a full application rollout.
The first planner Job had no admitted Pod because restricted PodSecurity required
an explicit nonroot identity. Its failed Job is retained as evidence; the distinct
corrected manifests passed without changing the source runner or fixtures.

The component executor and API lifecycle LOGINs also passed their real frozen
source consumers in READ ONLY transactions. Their 21 and 25 function-only contracts,
three protected-SELECT denials each, exact cached-image bytes plus minimal frozen
source overlays, Job/Pod ownership and stage-release custody are recorded. The
executor OFF path acquired no SQL connection and called no setters; the separate
startup consumer reported the existing facility-safe closure, lease 19 and 98
historical recovery debts. Lifecycle status retained component OFF, shadow/closed
and zero open exposures for the restored experiment. HTTP authentication, physical
execution and the missing 21 native harness fields are not established here.

The first lifecycle Job failed on TCP transport readiness. Its failed receipt and
UID remain preserved. A separate packet-only correction reused the existing
credential-free transport wait before reading the password. The single corrected
Job recorded two TCP refusals, then a ready connection and one authenticated
LOGIN. No product algorithm, import closure, overlay, grant or credential changed.
A final formatter-key correction changed reporting only; no second Job was run.

All eight existing LOGINs began with NULL passwords. Only isolated target-specific
temporary passwords were provisioned, privately in-process, with statement/error/
parameter/utility retention disabled. The exact PASSWORD NULL inverse committed
at 19:26:54Z; a fresh metadata record is byte-identical to the original record
(SHA256 35f6df173e20202b73b6fee44923f14953bae9a3de1020edaadd7ffc54543084).
LOGIN true, all role attributes, ownership, memberships and grants remain unchanged.
No production credential was read or changed. Two temporary Secrets, ten Jobs and
seven ConfigMaps were removed with exact UID preconditions; no Secret GET/LIST was
performed and no credential values or verifiers are present in these receipts.

Rollback fixtures allocated five sequences, including Timescale internal sequence
allocations. The enumerated exact-state inverse committed at 19:27:21Z. A fresh
read of all 77 states is byte-identical to frozenA/prefixture (SHA256 5ef84c37d80e40314e5d15233c10ffddbf86323a4fcf3ee9a3457b1937b80fa1).
The complete final952-relation stream contains 9,699,553 rows and is exactly equal
to the admitted prefixture baseline: stream SHA256 aee1846393ac5f23866950b23809db9c6f29ef48197462fa88c06edfe6b1642e.
Every relation header, row count and full row-multiset hash matches; all 42 study
relations and the complete native catalog/ACL witness match too. The latter is
SHA256 4355a2c83fbb260c3a6a5c483c46369dd115a24258bbdeb260d59f49557112b5.
Raw row bytes were hashed only in the Pod and never exported or persisted.

The final target readback at 19:33:22Z confirms the same primary UID, three ready
instances and six healthy Bound R2 volumes. All temporary runner Pods/Jobs/CMs are
absent. The Application remains manual, source targetRevision main, OutOfSync/Healthy
and idle after its exact candidate selective operation. This intentional state
does not authorize a broad sync or production cutover. The recovered target is
retained, with all eight qualification LOGIN passwords resealed NULL. Old failed
targets, original recovery seals and historical failures remain authoritative.

Receipts: [index](actual-qualification-receipts-index.json),
[authenticated Job bindings](actual-authenticated-job-bindings.json),
[final full952 execution](actual-final-full952-execution.json),
[final complete COPY hashes](actual-final-full952-copy.json),
[final native catalog](actual-final-native-catalog-receipt.json),
[final role metadata](actual-auth-final-metadata.json),
[sequence inverse](actual-sequence-inverse.json),
[final77](actual-final-sequence-receipt.json),
[study42](actual-final-study42-derivation.json),
[temporary cleanup](actual-temporary-object-cleanup.json),
[final target health](actual-final-target-health.json).
