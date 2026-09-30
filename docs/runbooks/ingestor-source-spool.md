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
| Climate fresh/last-known | Memory; fresh cleared before awaited DB write | Unchanged; documented residual |
| Climate JSONL outage spool | EmptyDir; no fsync; corrupt rows discarded, oldest rows dropped at cap | PVC preserves its existing file, but replay idempotency still unresolved |
| Raw equipment counters | Memory; UUID-idempotent insert-only DB function | Durable original sample before accepted enqueue |
| Direct state snapshots | Memory; UUID-idempotent DB function | Durable original snapshot before accepted enqueue |
| Equipment state callbacks | Memory before sealing | Durable internal event UUID; atomic replacement by sealed receipt |
| Sealed equipment receipts | Memory; UUID-idempotent DB function | Durable original receipt before accepted enqueue |
| Setpoint changes, state transitions, overrides, logs, diagnostics | Memory; some clear before awaited DB writes | Unchanged residual; need individual replay contracts |
| cfg/readback and component observation epochs | Memory for fresh connection; completed work/bundles/receipts journaled in DB | Never replay as fresh device confirmation |
| Native route/C1 callback collectors | Separate collector-owned source evidence | Existing owner lane; unchanged by this proposal |

Climate has no proven unique row/event replay key. Its current plain INSERT can
duplicate after an unknown DB commit and interrupted JSONL checkpoint. This
proposal adds no speculative timestamp deduplication and does not close #382.

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
performance. The source suite passed 67 focused tests in 1.37 s.

## Attended adoption and rollback

Before adoption, preserve the old Pod's state files and exact digest/source,
record the current queue count/bytes and source gap/watermarks, and inspect the
full Argo diff. The first transition cannot preserve memory-only work without a
separate explicit old-pod boundary decision. Verify UID/GID 1000 can write the
mounted claim, two healthy replicas, expected capacity, exact running digest,
and only one device writer before any bounded drill.

Do not disable the feature while queued records remain. A source rollback must
retain the PVC and v1 SQLite file and drain it with a compatible reader; older
images do not understand this queue. Returning the mount to emptyDir loses the
durable boundary. PVC deletion/prune is not a rollback procedure.
