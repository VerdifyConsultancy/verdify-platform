# #245 final-pair and write-boundary worksheet

Prepared source only. This expands the accepted [current275 handoff](../../../../docs/runbooks/cnpg/current275-contained-cutover-rollback-packet.md); it starts no fence, export, restore, cutover or credential operation. Retained restore/PITR/graceful-Pod-loss proofs remain accepted and are not prerequisites to rerun.

| Input | Required concrete receipt before attended execution | Current preparation |
|---|---|---|
| Production source | Exact intended owning revision, pending drift, eight release digests, schema/extension ledger, DB Pod/PVC/service identity |Reuse Retry's release custody; fresh source identity at actual window, no copied historical UID |
| All writers |Desired replicas/schedules plus actual active Jobs, sessions/pools, leases, Timescale jobs, hooks/reconcilers and inverse |32 desired workload surfaces retained; runtime fence is not executed |
| Candidate |Explicit future empty production candidate coordinates, identity/UID/OID/system/image/storage and role ownership, owner admission/capacity |Unselected; do not repurpose the sealed integration database or invent a second rehearsal |
| Final coherent pair |Fence clock, last acknowledged and ambiguous outcomes, snapshot clock/LSN, complete export and password-free role/ownership/settings metadata, checksums and protected custody |Existing export/verify/restore tooling; no fresh data export in this source task |
| Restored final parity |Complete actual final data/catalog/ledger/sequence/compression/history and native role/admission witness |Accepted952/77/42 integration baseline is historical reference, not final production parity |
| Endpoint release |All env/explicitDSN/datasource/config/Secret-reference surfaces, closed pools, exact no-prune hook-running sync, read-only actual candidate backend before authority release |Selected three-pool route reversal reused; no separate-DB or hot-reload claim |
| New write boundary |One authority owner, first new-primary accepted commit/time/LSN and ambiguous outcomes |Not measured; no synthetic production write solely for a receipt |

Use the implemented coherent pair and complete role-complete restore path (`scripts/verify-backup-pair.sh`, `scripts/restore-backup-pair.sh`, `scripts/cnpg-paired-restore.py`, existing current275 transition/native admission). Select the correct existing path for the actual input/target; none of these paths makes an occupied target empty or grants destructive restore authority. Verify exact restore-script constraints before selecting a future target. No arbitrary row-delta COPY, compressed-Timescale replication shortcut, reverse WAL across different systems or unacknowledged request replay is qualified.

The final catch-up is a complete fresh restore under an effective writer fence. Record separate export/transfer/restore/parity/config/first-write clocks; the actual outage includes all fenced stages. Historical104s export,19s base restore,387/418s replica readiness and209s route phases occurred at different boundaries and must not be summed into a guaranteed RTO. Define acceptable downtime and fresh-data RPO only after this concrete measured path exists. If full-restore downtime is unacceptable, qualify a supported catch-up method as a separate source task before promising zero loss.

## Fence and inverse

Bind exact replicas, CronJob suspend states, each owned active Job UID, migration/bootstrap/hook activity and actual DB session/client identity. Suspend future schedules, then account for active Jobs; do not confuse CronJob suspension with termination. Stop application mutators and close actual long-lived/dedicated pools without creating two ESP32 writers. Preserve alerting/public degraded-state truthfully. Kubernetes writer watchdog/death-healer authorities and external control paths require explicit handling, not an assumed DB-only quiet state. Prevent reconciliation reopening the fence. The DB itself remains online for coherent export.

Before releasing the new writer, retain a binary choice in the attended receipt: **no new target writes** or **target writes have occurred**. A timeout or missing ACK is not proof of no write.

**Before any target write:** close/fence target clients, restore the recorded original endpoint/config/datasource/source and replica/suspend settings through the owning no-prune reconciliation, verify original DB identity and accepted last-source data boundary, then reopen one authority. Measure the actual reversal. Keep target and original custody; no prune.

**After any target write or ambiguity:** fence both sides, preserve acknowledged/ambiguous new-primary outcomes and a fresh complete role-complete pair. A blind host flip loses newer accepted writes. The supported data-preserving candidate is a complete reverse restore into an explicitly selected fresh empty rollback database, full parity/native consumer qualification, then configuration reversal. This postwrite path is not yet measured/qualified for a production outage, and its target has not been selected or allocated. Preserve the old DB/PVC; do not overwrite it or pretend a schedule/config inverse restores data. Fix forward while preserving authority if a safe reverse path is not ready. Surface only the actual outage/data tradeoff once concrete.

## Observation and retained custody

At actual cutover verify current source/running identity, single ingestor/lease/socket, fresh validated telemetry/spool continuity, application role/duty failures, public/planner/MCP/lighting and blinded study integrity. The original#245 requires60minutes initial observation and7days durability bake; neither starts in this preparation. Preserve old database/PVC/dumps, all failed attempts/ambiguous outcomes, b087 integration baseline, spool PVC and18 staging custody files. Retirement/deletion and production credential rotation require their own exact scope.
