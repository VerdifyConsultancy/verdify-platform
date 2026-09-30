# Ingestor source spool (#382)

## Source proposal and current live boundary

This source change has not been adopted in production. The September 30 audit
found `/srv/verdify/state` backed by `emptyDir`, no live
`verdify-ingestor-state` PVC, and no climate spool file. The live state filesystem
reported 126 GiB free. EmptyDir survives a container restart in the same Pod;
pod replacement or node loss discards it.

The proposal renders the existing 2 GiB `verdify-ingestor-state` claim and its
sole Recreate consumer together. The declared and live storage class is
`longhorn-v1-workspace-rwo`: two replicas, Retain, WaitForFirstConsumer. It has no
Synology iSCSI dependency. Production adoption requires root review of the exact
source/render, an attended old-pod preservation decision and normal gated Argo
convergence. No production restart, storage write, or outage drill was performed.

## Buffer inventory

| Buffer | Current boundary | Proposed treatment |
|---|---|---|
| Raw climate fresh/last-known | Memory before minute aggregate acceptance | Remains memory; accepted aggregate is durably enqueued before fresh buffer clear |
| Accepted climate aggregates | Legacy JSONL on emptyDir has no event identity/fsync and drops oldest at cap | Separate fsynced UUID event queue on retained PVC; migration265 ledger makes interrupted replay idempotent |
| Raw equipment counters | Memory; UUID-idempotent insert-only DB function | Durable original sample before accepted enqueue |
| Direct state snapshots | Memory; UUID-idempotent DB function | Durable original snapshot before accepted enqueue |
| Equipment state callbacks | Memory before sealing | Durable internal event UUID; atomic replacement by sealed receipt |
| Sealed equipment receipts | Memory; UUID-idempotent DB function | Durable original receipt before accepted enqueue |
| Setpoint changes, state transitions, overrides, logs, diagnostics | Memory; some clear before awaited DB writes | Unchanged residual; need individual replay contracts |
| cfg/readback and component observation epochs | Memory for fresh connection; completed work/bundles/receipts journaled in DB | Never replay as fresh device confirmation |
| Native route/C1 callback collectors | Separate collector-owned source evidence | Existing owner lane; unchanged by this proposal |

The new climate path assigns an immutable source UUID before durable acceptance.
Migration265 atomically records that UUID/payload hash and its climate row; exact
retries are harmless, and conflicting reuse fails. Original timestamps, runtime,
transport generation and per-column callback provenance survive replay. The old
JSONL has no such identity. A nonempty legacy file fails new-path startup before
the device loop; it is never automatically converted or deduplicated. Other
buffers in the table remain outside this slice, so #382 remains open.

## Equipment source acceptance and replay

`VERDIFY_SOURCE_SPOOL_ENABLED=1` requires the dedicated equipment collector login
before device-loop startup. Without the flag, there is no filesystem change.
The queue is `/srv/verdify/state/spool/equipment-source-v1.sqlite`, format version
1. SQLite uses rollback journaling and `synchronous=FULL`; initial directory
entries are fsynced. Unsupported versions or corrupt records fail restoration
before the device loop. Do not delete or rename a failed queue.

An accepted counter, callback, snapshot or receipt is committed locally before
it enters the memory queue. Original UUID, observed timestamps, runtime instance,
transport generation, reset epoch and firmware remain unchanged. Callback sealing
replaces its event records with the receipt in one local transaction. Capacity
failure rolls that transaction back and keeps the original events.

Replay uses the existing dedicated, insert-only UUID-idempotent PostgreSQL
functions. The DB transaction commits before local acknowledgement. Cancellation,
DB failure or local acknowledgement failure retains the batch; an unknown DB
commit replays the same UUID and exact payload. Newer callbacks append behind the
original batch. Accepted durable rows are not discarded to fit retry buffers.

Restoration marks a new sticky source gap beyond any restored receipt's gap
version. Old runtime/generation receipts cannot set the current source freshness
watermark. The spool has no setters and never supplies cfg confirmation, current
readback freshness or component physical qualification.

## Capacity, backlog and recovery

Default limits are 30,000 total records and 128 MiB of serialized payload across
all four record kinds. Existing individual memory limits still reject new work
when full. No oldest-row eviction occurs in this queue. The 2 GiB claim includes
SQLite high-water pages, rollback journals and existing state/log files; disk
exhaustion rejects new acceptance and marks a source continuity gap while retaining
already accepted rows. Keep substantial free space beyond the payload limit.

At 80% of either queue limit, the ingestor logs a backlog warning at most once
per minute. Acceptance failure logs an error and a sticky source gap. Production
alert routing and actual Longhorn outage/reschedule recovery measurements remain
unverified; log warnings are not a deployed alert rule.

Focused tests cover abrupt process exit, atomic sealing failure, unknown DB
commit/local acknowledgement failure, DB outage, cancellation, full queue,
SQLite `SQLITE_FULL`, old generation retention, and no invented fresh receipt.
The September 30 local synthetic 1,000-record check took 0.2874 s to enqueue and
0.0026 s to reopen/decode/acknowledge. This is not PostgreSQL or Longhorn recovery
performance. The integrated source suite passed 88 focused tests (equipment source, climate
events, counter source and initial-subscription gap observer).

## Attended adoption and rollback

This procedure is prepared source guidance. It has not been executed. Root owns
image promotion, the bounded single-writer pause and attended full sync; coordinate
with the firmware/native build lane before starting a new build or changing pins.

### Release and schema order

1. Integrate the linear source commits on current main. Preserve migration265
   SHA256 `f7bded636fd1d98ef37a192ecf4fdd085585ac3a0ceadb388d1330edea067ae2`.
   The exact file passed actual production predecessor/postflight in a rolled-back
   transaction; that test did not adopt the schema or add runner ledger265.
2. Build the exact integrated source through the existing native CI. Do not reuse
   the observer-only 2c image for this change. Promote the matching five release
   pins together (api, mcp, ingestor, migrate, experiment-v2 orchestrator) with
   `scripts/promote-release-pins.py`. The existing ledgered PreSync runner must
   apply265 before the new ingestor starts. No selective sync, digest refresh,
   changed migration bytes, or manually inserted ledger stamp.
3. Inspect the complete Argo diff, source-role Secret **names/key availability**
   without printing values, and current ordinary predecessor receipts. Any actual
   predecessor mismatch fails closed; qualification is not permission to overwrite
   receipts. The new deployment requires both spool gates, retained mount and
   dedicated equipment collector pool before the native device loop starts.

### Preserve old emptyDir and seed the retained claim

The sole live writer still uses emptyDir. Read-only inventory on September30
found five files (writer-stage-preview, dispatcher log, HA sync bookkeeping,
C1 capture status and milestones), no climate JSONL and no retained claim. Re-read
this inventory immediately before adoption; it is time-sensitive.

1. Save the old deployment/pod UID, image digest, declared release coordinates,
   full `/srv/verdify/state` file list/sizes/hashes, current queue counts and source
   gaps. Store a complete tar archive off the Pod before any pod deletion. Preserve
   file ownership, permissions and all SQLite rollback journals if present. Read
   the archive locally, hash it and fsync the local file and parent directory.
   Preliminary live captures are recovery checkpoints only: compare per-file hashes
   and retain both if files changed. They cannot be the final accepted-file handoff.
   Use the bounded quiescent boundary below before replacing the old Pod. The first
   transition cannot preserve memory-only/nonaccepted samples; record that cutover
   interval as a source gap, not continuity proof.
2. If legacy `spool/climate.jsonl` is nonempty, preserve its exact bytes/hashes and
   leave the **climate gate off** for adoption until its backlog has an explicit
   resolution. The old plain INSERT path cannot establish unknown-commit identity.
   Do not assign UUIDs to legacy samples, heuristically deduplicate by timestamp,
   delete/truncate the file, or automatically replay it under the new API. For a
   genuinely empty backlog, prove zero size/absence from the final archive and the
   restored claim. A quarantined unresolved archive is still an open data-integrity
   boundary, not a successfully drained queue.
3. Create only the source-declared `verdify-ingestor-state` PVC, without applying
   the Deployment patch yet. Use an existing exact-digest ingestor image in a
   one-shot **file-transfer-only Pod**: command runs Python/tar utilities, not the
   ingestor entrypoint; no device/HA/DB credentials, no app selector labels, no
   native connection. Mount only this claim at `/restore`, run as UID/GID1000,
   fsGroup1000. Its scheduling binds the WFFC claim. Verify claim/PV identity,
   capacity and two healthy Longhorn replicas before old-pod replacement.
4. Keep the bound transfer destination empty until the final quiescent handoff
   below. Restore that final archive through `kubectl exec -i` into the transfer Pod,
   refusing a nonempty destination rather than overwriting retained state. Extract
   only relative regular files/directories: reject absolute paths, `..`, symlinks
   and hardlinks. Preserve the original content and write permissions for UID1000;
   fsync files and directories. Compare every restored file's SHA256 and byte count
   with the archive manifest. Keep the original archive outside Kubernetes too.
   This bootstrap writes persistent storage and is performed only in the coordinated
   adoption window; source preparation does not authorize an unattended transfer.
5. Execute the final quiescent handoff below, then seed from that final archive
   (preliminary copies are not authoritative). End and remove only the owned
   transfer Pod, release its RWO mount, and preserve the seeded claim. Root performs the normal full `prune:false` attended sync at
   the exact integrated/pinned revision. The ingestor remains replicas1/Recreate;
   never start a second native consumer to test the volume. Record old/new Pod UIDs
   and actual connection generation boundaries. Do not claim old capture-status
   or HA bookkeeping files are fresh observations after restoration.

### Final accepted-file handoff: stop acceptance while emptyDir remains readable

Two live archives, even with an empty JSONL, do not prevent acceptance between
capture and deletion. The final archive must be made while the exact old ingestor
process is stopped and its Pod/emptyDir remain intact. Do not execute this step
until matched images/hooks and the transfer destination are ready.

The actual baseline command is `python ingestor.py`, PID1 in its container. Its
exec liveness runs `python ingestor-healthz.py` and accepts exit0 (climate age at
most300s) **or exit2 (DB query error)**; period30s, timeout10s, failureThreshold5,
no readiness/startup probes, termination grace15s. Do not rely on a container-local
`kill -STOP 1`: namespace-init signal restrictions can ignore it. SIGTERM alone
can trigger a container restart before capture. Do not freeze the container task
or whole cgroup, because that can freeze the capture/probe process as well.

Use the existing node operator surface, or an attended Kubernetes node-debug Pod
with hostPID and the standard `sysadmin` profile; neither requires a new SSH
credential. This helper has no device client or ingestor entrypoint. Resolve the
old Pod's exact node, UID, runtime containerID and host PID using the node's existing
CRI tooling (`crictl inspect`). Bind a watchdog to **all four identities** plus
`/proc/<hostPID>/stat` starttime and the original cgroup. Verify the host process
command and its namespace PID1 before sending any signal. Never resume a reused
PID: recheck starttime, containerID/cgroup and PodUID every time. Remove only the
owned helper afterwards. If this supported host operator surface is unavailable,
leave the old Pod running and the preliminary archive retained; finish image/claim
preparation and report that the final accepted-file transfer is not executable.
Do not substitute racing archives, add a second device consumer or invent a gate.

Before pause, run the existing health probe with `--max-age 240` and require exit0;
record the actual climate age. Verify archive-size/read throughput allows final
capture, closed-file hash verification and local fsync within **45 seconds**.
This bound leaves climate freshness below the actual300s liveness threshold;
DB errors remain accepted by the existing liveness command. A preexisting stale
climate result is not a verified pause budget. Preserve an external preliminary
archive before attempting the bounded handoff.

1. Arm an independent45s watchdog on the existing host operator/helper. On timeout,
   operator disconnect, capture/hash error or cancellation, it sends SIGCONT only
   to the same still-existing process with the bound starttime/container/cgroup/UID.
   The failure path runs before pod deletion. Confirm the watchdog is active before
   the pause; never leave a suspended writer without that recovery path.
2. From the ancestor host PID namespace, send SIGSTOP to that exact hostPID. Read
   `/proc/<hostPID>/status` and require stopped state `T`/`t`, unchanged identity,
   and no container restart. Kubelet probes and separate `kubectl exec` utilities
   can still execute: only the writer process is stopped. Record pause UTC.
3. Capture the **final** full state archive and per-file manifest through a separate
   exec utility while stopped. The original emptyDir is still mounted/readable.
   Include SQLite databases and all journals as bytes; do not open/replay/repair
   them. Require unchanged stopped process/Pod identity before and after capture.
   Close the archive, fsync it and its parent, hash it and validate member hashes.
   The complete final archive replaces the preliminary capture for PVC seeding.
   Inspect legacy JSONL in this final stopped capture; if nonempty retain the
   climate-gate-off path. Zero bytes in an earlier live snapshot is insufficient.
4. Only after the final archive is durable and verified within the budget, root
   scales the old Deployment to0/deletes the old Pod **while it remains stopped**.
   The writer never resumes to accept another file between final capture and
   deletion. Record deletion and terminated old container identities. The watchdog
   disarms only after confirming that exact process/container is gone; it must not
   send SIGCONT to a reused host PID. Finish the source-declared transfer-only Pod
   restoration/hash proof, release its RWO mount, then perform the matched full sync
   with the new sole writer at replicas1. Source gap covers this interval.

If any step cannot finish before the verified45s bound, resume the same old writer,
retain the archive as a checkpoint, make no continuity claim, and do not delete the
old Pod. Prepare a faster bounded copy or compatible source-owned quiescence path
before retrying. Accepted old files are never discarded merely to fit the window.

### Verify adoption and preserve rollback

Check ledger265 exact hash and ordinary role attestation, matching five running
release digests, Synced/Healthy exact revision, retained claim mounted at
`/srv/verdify/state`, UID1000 write permission, both queue gates, dedicated source
collector pool and exactly one ingestor native writer. Inspect original UUID/time/
generation queue rows and database receipts without synthetic climate injection.
Verify logs for schema validation, capacity errors and replay conflicts. PVC mount
plus healthy rollout proves adoption, not database-outage or node-loss recovery.
Perform any campaign-authorized interrupted-replay/restart drill only after this
readback and retained-state preservation, with measured accepted/replayed UUIDs,
original timestamps and no fresh confirmation fabricated from replay.

Keep the old baseline digest and source/pin coordinates, archives, seeded claim,
both v1 SQLite files/journals and the database UUID ledger. A baseline image does
not understand the new queue. Do not disable a gate while its accepted rows remain
without retaining a compatible reader and an explicit parked-backlog record.
Rolling back the mount to emptyDir loses the durability boundary. Never prune the
PVC, drop265/ledger, clear queues, or treat replay as new device observation. Prefer
a compatible fix forward; restoring baseline physical service may retain the PVC
and park incompatible queues, but does not complete #382.
