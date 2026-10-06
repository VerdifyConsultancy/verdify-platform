# #245 CNPG cutover and rollback packet

This packet records the qualified current274 recovery boundary for a future
production handoff. #396 did not change production DB_HOST, database architecture,
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
| Physical Backup | 392s; independent reader verified base plus all WAL through C |
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
and every original job/config/scheduled flag intact. This is a deliberate
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

## Remaining acceptance outside this274 packet

ROOT/Iris own actual ordinary-client S2→frozen-B→S2 endpoint flip and reversal,
current275 successor/admission proof and its production image/deployment sequence.
Physical host failure is not the tested normal-grace primary-Pod-loss boundary.
Production DB cutover itself remains #245. This packet neither executes it nor
credits source/CI/backup creation as deployed production acceptance.
