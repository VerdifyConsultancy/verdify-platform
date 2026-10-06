# #245 CNPG cutover and rollback packet

This packet records the immutable current274 recovery boundary and the
additive current275 recovered-candidate proof for a future production handoff. #396 did not change production DB_HOST, database architecture,
writer, PVC, credentials, device connectivity or firmware. Execute #245 against
fresh native identities and current source; the old rehearsal snapshot is not a
live replacement. No additional human approval step is introduced here.

## Native coordinates and measured boundary

At 2026-10-06T06:02:08Z production namespace `verdify-prod` used:

| Object | Authoritative identity/value |
| --- | --- |
| Database StatefulSet | `verdify-db`, UID `692af2f5-c937-4fde-aeb3-3c4fd36710ca`, one replica |
| Database image | `timescale/timescaledb:2.25.2-pg16` |
| Headless database Service | `verdify-db`, UID `556ef7b6-2d04-4301-940e-15f8b494bab5`, port5432, selector `app.kubernetes.io/component=db` |
| ConfigMap | `verdify-config`, UID `6aa54f9d-2cc0-452a-8c7e-6c4f62cbcadd` |
| Shared DB coordinates | DB_HOST=`verdify-db`, DB_NAME=`verdify`, DB_PORT=`5432` |
| Ingestor | UID `27889696-ee26-43ff-bfd8-a950ba279866`, one replica, Recreate |
| Setpoint server | UID `92485295-17f3-4a6f-9868-d283f456a4fb`, one replica, Recreate |

These are observed rollback coordinates, not permanent UID assumptions. Recapture
resourceVersion, Pod/PVC/PV UIDs, ready endpoint, all deployment replicas, active
Jobs/CronJobs, actual DB clients, release pins and actual imageIDs immediately
before the handoff. Preserve the existing database/PVC and its immutable final
pair; never prune them. The private metadata inventory is
`/Volumes/Work/verdify-s2-cnpg-20261006/cutover-current-coordinates-once/receipt.json`.
Existing credential names/keys remain governed by `deploy/k8s/SECRETS.md`; access
values only inside consuming processes, with credential-bearing SQL/logging suppressed.

| Qualified measurement | Actual result |
| --- | --- |
| Coherent logical export | Source clock03:58:39.426422Z; dump349,998,576bytes in104.01s |
| Complete protected data | 951 COPY relations/9,699,549 target baseline rows; all workload rows exact |
| Complete catalog/time inventory | 44,220 entries;427 relations/567 timestamps/614 Timescale owners exact |
| Physical Backup | 392s; independent reader GET-verified backup.info and all WAL through C, HEAD-verified the 619,166,118-byte base; actual recovery consumed the base |
| Fresh reader-only PITR | A-only / A+B-not-C; base19.849/19.070s; threeReady387/418s |
| Exact normal-grace primary-Pod loss | 454/454 ACKs retained, zero duplicates/lost ACKs;8.464s ACK gap |
| Ambiguous client outcomes | 12 attempts unreplayed; one committed without ACK |

These are measured stage durations, not a promised production outage SLO. Physical
PITR used a frozen rehearsal baseline, not a live production change stream; its
threeReady time does not include final production export/import, full native
qualification, role custody, client rollout or smoke. Source data continued after
the capture; switching to that old snapshot would lose newer production work.
Native receipts: [full parity](../../handoffs/s2-2026-10-05/evidence/cnpg-current274-physical-full-parity.json),
[timings](../../handoffs/s2-2026-10-05/evidence/cnpg-current274-recovery-timings.json),
[failover](../../handoffs/s2-2026-10-05/evidence/cnpg-current274-primary-loss.json).

## Current275 recovered candidates and preserved lineage

The original S2 clone is rejected as a current acceptance target. After its
natural primary switch to timeline2, Timescale workers16 resumed maintenance:
9,953 diagnostics rows disappeared, two existing chunk relations were absent,
and two of 77 sequence values changed. The complete 952-relation probe failed
on the missing relation. Installed admission alone did not prevent this data
loss. Preserve the failed witness `d89b00467ad112fc840b773b49a1a2228d8fd997777eea2ddfe48de9713d6df8`;
do not repair the acceptance claim by narrowing its rowset or resetting allocators.

The intact frozen A candidate was instead qualified against its original full
baseline, bridged to275, and admitted separately. The bridge changes only the
lighting-policy function's local `jit=off`, two canonical ordinary receipts,
and one exact migration-ledger row. Admission replaces two target attesters and
adds ten table objects/three native receipt rows; its enumerated native allocation
changes are separately validated. Historical274 receipts and all workload
rowsets remain intact. The original installation is immutable; a separate
transport receipt binds its later primary rather than rewriting that history.

| Current recovered endpoint | A | B |
| --- | --- | --- |
| Cluster | `verdify-cnpg-s2-pitr-a-frozen` | `verdify-cnpg-s2-pitr-b-frozen` |
| Cluster UID | `bd01ec5b-efe9-4882-a6dc-c76c6b6fa7ec` | `3981d0f2-4c72-48ac-9e09-826fe6bd4ed6` |
| Observed primary | `verdify-cnpg-s2-pitr-a-frozen-2` | `verdify-cnpg-s2-pitr-b-frozen-3` |
| Primary UID | `ea68bad4-3f3b-4116-b120-6300d92161f6` | `11f64c0a-8053-49df-bad0-85fc020b8858` |
| Observed IP / timeline | `10.42.3.133` / 3 | `10.42.3.191` / 5 |
| RW Service UID | `93314da9-22ad-465c-b87b-34f7483b356f` | `1778c541-900f-4743-b9be-62b13f96c569` |
| Project / bootstrap DB OID | 16447 / 16385 | 16447 / 16385 |
| PITR marker lineage | A only | A+B, not C |

These are observed rehearsal identities, not production coordinates. Both
three-instance declarations pin the same PostgreSQL16.13/Timescale2.25.2 operand
(`sha256:8b461e37d18aa049704eb6a9cde2ba9af0f955f8d3725e921070d2450bb2f137`),
20Gi data+5Gi WAL per instance and strict physical anti-affinity. All six actual
instances proved `timescaledb.max_background_workers=0` and restoring off; the
worker limit is declared across every candidate instance through failover.

A's original951 baseline contains 9,699,549 rows. The one275 ledger row makes
its951 count 9,699,550; three native admission rows make the full952 count
9,699,553. B's legitimate original PITR marker lineage differs from A's.
Compare workload multiset/time/policy against each protected baseline and
explicitly account for marker and native authority differences; equal aggregate
counts do not prove whole A/B databases identical. Preserve A's genuine77
sequence values, including its five historical Timescale allocator offsets,
rather than substituting the logical source's allocation state.

A's natural operator switch started at09:23:42Z and its new primary timestamp is
09:23:46.751499Z: approximately4.751499s between those observed operator stages.
Clients were stopped, so this is not a consumer outage or ACK-gap measurement.
On the new primary, all952 rowsets, all77 sequences, all42 study tables, the
entire installed witness, helper authority, three boundary receipts and
historical274 rows match the actual pre-switch A seal. The full COPY byte SHA
also matches. This proves preservation through the current275 natural switch;
the earlier454-ACK/8.464s drill is a distinct current274 Pod-loss measurement.

B subsequently passed the same complete preservation proof on primary-3 at
timeline5. Its operator switch event is09:23:40Z; new-primary timestamp is
09:23:44.152255Z, approximately4.152255s between observed stages with clients
stopped. Its own952-rowset/77-sequence/42-study/native authority baseline is
unchanged, including the full native COPY SHA. Historical B installation5313
and its existing runtime Secret remain untouched. Neither natural switch is an
actual ordinary-client endpoint reversal or consumer downtime measurement.

Private authoritative artifacts under `/Volumes/Work/verdify-s2-cnpg-20261006`:

- `frozen-b275-primary-rebinding-once/receipt.json`, SHA
  `e786df5666993f5cff15ec507f27633339d3780a9942e184e63d0f6bcfe16c41`;
  includes20 checksum-bound evidence files and unchanged installed B authority.
- `frozen-a275-primary-rebinding-once/receipt.json`, SHA
  `7744a77e9681353cdb7a2be297c0917839a064175b9b289f4843b6c255107a22`;
  includes the18 checksum-bound evidence files and fresh A/B marker custody.
- `frozen-a275-admission-installed-once/installation.json`, SHA
  `9d7f12596ec4b015a45d9a3fc3f35df1f09f1be088d97b79ab03f0a00dae4ad9`;
  retains the original primary-1 UID rather than relabeling installation.
- `frozen-a275-authority-readback-once/authority.json`, SHA
  `56bcb4fa9d933d1d112579bcbe68ef3f00004283486c8486160c7b896e33294d`;
  helper hashes are function-body hashes, not full function-definition hashes.
- `frozen-a275-final952-copy-once/receipt.json`, SHA
  `4c5668164d32ccdbf418b9e54bbb5e131d6fdee5586444076236a43b85ec86ad`;
  complete baseline data witness, unchanged after the switch.

A's initial NULL-only nine-role password installation subsequently completed
with target-only Secret UID `f8613e8b-c3c5-4aaf-966a-57aee09cc8aa`, preserving
native authority and source credential bytes. This is credential custody;
actual owning-client authentication is separate acceptance. No production
credential rotation or production database cutover occurred.

## Concrete handoff sequence

1. **Own one serial handoff.** In an isolated owning-repo worktree, render the
   exact fresh production CNPG declaration, selected image/schema/release pins,
   service endpoint, namespace/NetworkPolicies, retained rollback endpoint and
   worker policy. Record complete Argo pending drift. Do not repurpose the old
   rehearsal namespace or widen its denied device route. Do not use old VM
   migration instructions or alter shared StorageClasses/NFS/hosts. Shared
   storage or node needs go through their owner and the existing Proxmox agent.

2. **Enumerate and fence every writer before the final snapshot.** Preserve
   replicas and CronJob suspend values, stop accepting mutating API work, stop
   experiment-v2 selector/lifecycle/freezer and any active experiment Jobs,
   planner, setpoint-server, ingestor, HA-backfill, vision, lab-publisher and
   application/watchdog Jobs that write the DB. Inventory actual pg_stat_activity
   identities and role memberships rather than assuming the nine ordinary logins
   cover all writers. Suspend relevant CronJobs and wait for their current Jobs
   to finish or stop their exact owned jobs; include migration/bootstrap hooks.
   Fence owner/experiment identities and scheduler maintenance too. Verify no
   admitted writer sessions remain and no reconciliation restarts them. Keep
   ingestor at zero while fenced and one/Recreate after handoff; do not activate
   devices, experiment enrollment or OTA as part of database qualification.

3. **Capture a fresh role-complete final pair under the fence.** Reuse
   `verify-backup-pair.sh`, `cnpg-paired-restore.py` and the complete C0/native
   witness protocols from `cnpg-alongside-rehearsal.md`. Capture coherent exported
   snapshot, password-free full roles/memberships/comments/settings, exact ledger,
   source seals and all workload/native ownership evidence. Native stage SHA,
   source context, server/DB/Pod/PVC identities and final timestamp are mandatory
   input integrity. The supported catch-up is a complete final coherent restore
   into an empty target; arbitrary COPY-of-new-rows, reverse WAL between these
   different clusters or unqualified logical replication of compressed Timescale
   data is not an implemented shortcut. Keep writers fenced through the final
   parity/native-admission proof. If that duration is unacceptable, implement and
   qualify a supported live-delta method before the handoff.

4. **Restore and qualify the actual production candidate.** Use a new exclusive
   custody stage, exact cluster/primary/image/DB identities and checksum-bound
   pair; fail closed on partial imports. Run all original full row multiset,
   catalog/role/ACL, sequence, ownership, compression and common-clock count/time
   criteria. Enumerate Timescale install metadata explicitly. Fresh native
   target admission must bind the candidate's actual OIDs and endpoint; copied
   source receipts or S2 literal-cluster helpers cannot authorize this target.
   Install existing runtime credential verifiers through the guarded target-only
   custody procedure after password-free baseline protection. Verify all actual
   consumer login/startup/duty/deny behavior; current275 or future schema work
   needs an additive native ledger/function-config proof, never relabel274.

5. **Switch all consumer coordinate surfaces while still fenced.** Update the
   owning declarative `verdify-config` DB_HOST/DB_NAME/DB_PORT and every explicit
   DB_DSN, Secret-reference contract, Grafana datasource and CronJob/workload
   override discovered in the inventory. One ConfigMap change alone does not
   replace existing pools or explicit DSNs. Render and diff the full prod
   Application, then use its normal full no-prune sync at the exact revision;
   selective sync skips migration hooks. Verify the operation's resources selector
   is empty and its hook/ledger results match the intended schema. Start read-only
   API/MCP/Grafana clients first and verify their actual TCP server/database/OID,
   ordinary identity and guarded pool readiness. Keep all writer paths fenced
   until those observations and the reversible endpoint-flip proof pass.

6. **Release one writer authority and validate.** Once the target is authoritative,
   release serialized runtime writers with their recorded replica/CronJob settings;
   ingestor stays exactly one/Recreate. Reverify single ESP32 connection through
   the supported device monitor only in the production handoff scope. Observe
   ACK persistence, telemetry continuity, spool replay without duplicates,
   current-client allowed/denied duties and application freshness. Record first
   target committed transaction/time/LSN, all ambiguous outcomes and endpoint
   rollout duration. Check Argo Synced/Healthy at exact revision and live imageIDs,
   run `k3s-smoke.sh smoke` with expected API SHA+digest, and watch ingestor schema
   errors for five minutes. Preserve old DB/PVC unchanged as rollback custody.

## Retention and scheduler policy is part of the handoff

The first actual A/B recoveries resumed old Timescale jobs after promotion:
retention deleted9,953 diagnostics rows, dropped a compressed chunk, changed
owner inventory and broke complete parity. The evidence is preserved. Both
fresh frozen targets instead start with supported worker count0, restoringoff,
and every original job/config/scheduled flag intact. The later current275 S2
loss independently reproduced this hazard when its replacement primary had
workers16; the frozen A current275 switch preserved full data with workers0
on every instance. Checking only the initial primary would miss the hazard. This is a deliberate
inspection freeze. It leaves compression, retention, refresh and other automatic
work paused and cannot be inherited indefinitely as a production performance
or maintenance policy.

Before enabling production workers, enumerate every restored job, procedure,
owner, config, next_start, retention/compression threshold and affected data at
that exact fresh clock. Choose explicit workload retention and refresh behavior
in source, apply it through supported Timescale APIs/new serialized migrations,
and measure the first actual execution with before/after data custody. Preserve
required history before destructive retention. Do not silently resume all old
scheduled jobs, change global host settings, rewrite applied migrations or call
the frozen-job proof evidence of safe automatic production policy. A job's
scheduled=true does not mean it ran safely.

## Rollback by the actual write boundary

**Before any new target write:** fence clients, stop target pools and writers,
restore all recorded source endpoint/datasource/DSN/release/replica/suspend values,
reconcile exact no-prune desired state, then reopen the preserved source. Verify
native server/DB identity, exact source ledger/seals and consumer duties before
releasing the single writer. The unchanged final source remains authoritative.

**After target writes:** first fence both sides and record every target ACK,
ambiguous attempt and final committed cutoff. A blind endpoint reversal to the
old source loses new acknowledged rows. Preserve the target's fresh role-complete
pair and native witness; qualify a complete reverse restore into a fresh empty
rollback database compatible with the selected schema/image, including all new
accepted events, sequence state, compressed chunks and receipt authority. Run
full parity and actual consumer validation before reversing all endpoints and
releasing writers. Do not overwrite the retained old DB or replay ambiguous
transactions. If there is no verified reverse restore, fix forward on the target
or explicitly report the unresolved data-loss boundary; no false zero-RPO claim.

## Remaining acceptance and execution boundaries

The current275 native A bridge/admission and natural failover preservation are
passed. A and B target credential custody is complete. Actual nine ordinary
owning-client A→B→A authentication/read-only endpoint reversal, actual Grafana
phases, and the committed-spool qualification with exact inverse remain pending.
After every client and inverse stops, independently reseal full952 rowsets,
all77 sequences, all42 study tables and installed native authority; do not award
post-fixture preservation from a pre-fixture receipt. Keep job/credential/client
mutations serialized with those custody windows.

Production source/build/deployment acceptance belongs to ROOT's delivery lane;
a production promotion does not credit a DB endpoint cutover. Physical host
failure is not the tested normal-grace primary-Pod-loss boundary. Production DB
cutover itself remains #245 and requires the fresh fenced source/target and
actual write-boundary rollback sequence above. This packet neither executes it
nor credits source/CI/backup creation as deployed production acceptance.
