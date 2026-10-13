# #396 → #245: current275 contained recovery and cutover handoff

This packet updates the preserved [current274 handoff](current274-cutover-rollback-packet.md) with the accepted current275 integration recovery, whole-data/role inverses and actual three-consumer configuration reversal. It prepares the next source and read-only work. It does not execute production cutover, create another database, change credentials or grant a native fault window. Production DB_HOST and its old database/PVC remain untouched.

## Exact retained candidate and data boundary

| Item | Accepted coordinate |
|---|---|
| Integration namespace / Cluster | `verdify-db-rehearsal` / `verdify-cnpg-s2-integration` |
| Cluster UID | `b087a146-58c5-4d98-8054-09094d22ecfe` |
| Primary Pod / UID | `verdify-cnpg-s2-integration-1` / `45cbb6ef-a246-4448-9111-8d1a2ba4afad` |
| RW Service UID | `f374b4dd-9ebc-453d-a561-fab081479106` |
| Project database / OID / system identifier | `verdify_rehearsal` / `16447` / `7693399121498484768` |
| Operand | PostgreSQL16.13 / Timescale2.25.2 / vector0.8.1; digest `sha256:8b461e37d18aa049704eb6a9cde2ba9af0f955f8d3725e921070d2450bb2f137` |
| Topology | Three instances; required `topology.vallery.net/proxmox-host` anti-affinity; 20Gi data + 5Gi WAL each, R2 |
| Existing storage | 75Gi logical / 150Gi potential R2 allocation on separate Longhorn NVMe; already occupied, not a new reservation |
| Recovery input | Backup `20261006T052358`, timeline1, inclusive target `2026-10-06T05:38:19.219172+00:00` |
| Inspection policy | Timescale workers0; original job definitions retained; no archive writer or production device authority acquired |
| Accepted original / current data | 951 relations / 9,699,549 rows at274; reviewed275/admission gives 952 / 9,699,553, all77 sequences /42 study anchors |
| Catalog seal | `4355a2c83fbb260c3a6a5c483c46369dd115a24258bbdeb260d59f49557112b5` |

The last ending metadata at [e06dcf51](https://github.com/VerdifyConsultancy/verdify-platform/blob/e06dcf51b29cc9062605241512e1df73effcf09f/planning/execution/2026-10-12/runtime-endpoint-643/native-nine-phase-checkpoint/README.md) proves the same identity,3/3Ready, zero clients, three NULL LOGINs and exact catalog after nine serial native phase Jobs and21UID cleanups. Earlier full952/77/42 seals at b4eb and499131da are reused; the final nine READ ONLY consumers did not warrant another full-COPY scan. None of this makes the old October6 snapshot current production data, and the integration database is not silently repurposed as a production candidate.

## Measured recovery boundaries

| Actual retained measurement | Value and limit |
|---|---|
| Coherent source export | Source clock October6 03:58:39.426422Z; 349,998,576 bytes in104.014926s |
| Protected physical backup |392s; backup.info and required WAL72–74 independently verified; October12 full619,166,118-byte base recheck matches SHA256c3b56cd2… |
| Historical frozen A/B PITR | A-only / A+B-not-C; base restore19.849456/19.069798s; creation→threeReady387/418s |
| Historical graceful primary-Pod loss |454/454 acknowledged sentinel commits retained;0 lost ACKs/duplicates;12 ambiguous attempts unreplayed,1 committed without ACK;8.463646806s same-client ACK gap |
| Later current275 frozen-A natural switch |4.751499s between observed operator stages, with clients stopped; full952/77/42/native authority preserved. This is not a client outage measurement. |
| Current integration restore | Exact four-resource apply18:32:58→18:33:00Z October12; independent native predecessor captured18:35:45.730399Z; complete951 data proof finished18:37:50.782799Z, with52.947441s in-pod COPY processing |
| Current three-consumer route reversal | Nine warm cached phase Jobs across API/ingestor/MCP,18 READ ONLY checkouts,209s first-Job→last-completion including operator/Kubernetes gaps; all pools closed and native client counts returned to0 |

Historical timing/native events and data seals are linked in the [396 evidence map](../../../planning/execution/2026-10-13/recovery-396-handoff/README.md). Historical Cluster UIDs are preserved, not relabeled as b087. A normal-grace UID/RV Pod deletion does not prove physical host/node failure. The current integration timestamps are completed stage observations; no instrumented current production fence→first-new-write RTO, fresh production RPO or post-write reverse-restore duration exists. The source export, base restore, replica readiness and data qualification durations came from different stages/targets and must not be added into a promised outage bound.

The original unfrozen recoveries and original S2 at275 are rejected acceptance targets: restored maintenance deleted9,953 diagnostics rows and broke full COPY/chunk/sequence seals. Worker0 is the supported inspection freeze that prevented this on intact targets. It is not an indefinite production maintenance policy. Retain every failed seal, ambiguous outcome and old artifact; do not repair them by narrowing rowsets or resetting unexplained allocations.

## Concrete next source and read-only work

1. Bind the intended production source revision, release digests and complete mutator/scheduler/hook surfaces to a reversible fence plan. Main was read at `0b03466a8f30a0494482c983853128f98cdc9e0a` for this packet; that Git fact is not a fresh live production authority/readback. Retry remains the sole production writer. Reuse its current receipts, then refresh only missing live identities when the actual window is defined.
2. Prepare the final coherent export/restore/parity and both write-boundary rollback procedures. The implemented safe catch-up is a complete role-complete coherent final restore into an empty, explicitly selected production candidate. There is no qualified arbitrary COPY-of-new-rows, reverse WAL into a different cluster or compressed-Timescale logical-replication shortcut. Candidate name/namespace/database/image/schema and resource/role/native-admission guards must be concrete before execution; this packet allocates none and does not point production at the protected integration snapshot.
3. Prepare the production Timescale job policy from retained exact job owners/configs/retention and product history requirements. Preserve history before destructive retention. Define the first actual job execution and its data-impact measurement. Do not resume every restored scheduled job or leave an unexplained worker0 freeze indefinitely. Surface a human tradeoff only if existing product requirements cannot resolve it.
4. Join the six-duty107SQL proofs and three-consumer native proofs to the selected production bounded-role/ambient-authority/health surfaces. Nine new phases are three pools × three routes, not nine independent application duties. Planner, setpoint-server, HA backfill, vision, lab publisher, Grafana and dedicated experiment pools retain their original proof scopes. Do not require repeat native faults or full seals merely to restate accepted evidence.

These source/read-only steps need no new human decision. The completed three-LOGIN credential window is closed. Any later credential/native fault/target allocation must have its own concrete selected scope; a live production database cutover/retirement remains the separately bounded #245 attended request, outside this work.

## #245 execution sequence to make concrete before the attended window

**Fence every actual writer.** Record exact replicas, strategy, CronJob suspend states, current Jobs, DB sessions, endpoint/DSN/datasource surfaces and inverse. Include mutating API/lifecycle routes, ingestor and its dedicated experiment pools, planner/store/log writers, setpoint-server, experiment orchestrator/freezer/randomizer, HA backfill, vision, lab publisher, watchdog/application Jobs, Timescale jobs and migration/credential-bootstrap hooks. Suspending a CronJob does not stop its existing Job. One scaled ingestor does not make the database quiet. Verify actual session identities and no reconciliation reopening a writer; keep one serialization owner.

**Capture and qualify a final pair under the fence.** Preserve a fresh coherent source clock, password-free role/membership/settings metadata, full ledger/catalog/data/ownership/compression/sequence evidence and rollback storage identities. Use existing supported pair verification, restore and native admission source. Exact fresh production-target UID/OID/system/image guards are required; copied integration/old274 receipts cannot authorize a different target. Keep writers fenced through parity. If the full final restore downtime is unacceptable, implement and qualify a supported delta method before cutover instead of promising zero loss.

**Switch all configuration surfaces while fenced.** Bind exact owning Git revision, pending drift, source/target endpoint, DB_NAME/PORT, explicit DSNs, Secret reference contracts, Grafana datasources and workload overrides. Updating only one ConfigMap leaves existing pools and overrides unchanged. Apply only the reviewed production source through its normal full no-prune Argo path with selector absent and required hooks. The alongside four-resource selective recovery operation is not the production promotion mechanism. Verify bounded read-only clients against actual candidate backend/OID/source/role before releasing a writer.

**Release authority once, then observe.** Restore only reviewed replica/CronJob settings; ingestor stays one/Recreate. Record first new-primary committed transaction/time/LSN, retained ACK/ambiguous outcomes, telemetry/spool continuity and actual allowed/denied duties. Verify exact source/running DB authority and source/image/Argo health. The original #245 contract requires at least60minutes initial observation and a7day durability bake. Source/CI or nine isolated read-only phase Jobs do not replace these post-cutover observations.

## Rollback at the write boundary

**Before any new target write:** fence/close target clients, restore recorded endpoint/DSN/datasource/source/replica/suspend settings through no-prune reconciliation, verify preserved-source identity/ledger/seals and bounded duties, then reopen the single authority. Source remains unchanged and authoritative. Measure this reversal in its actual selected topology; the same-database DNS/IP/DNS rehearsal does not supply a production database reversal duration.

**After target writes:** fence both sides and preserve all acknowledged/ambiguous target transactions plus a fresh role-complete pair/native witness. A blind host flip to the stale old source loses new accepted writes. The data-preserving reverse path is a qualified complete reverse restore into a fresh empty rollback database compatible with the selected schema/image, followed by full parity and consumer qualification. It is not yet measured as a production outage procedure. Do not overwrite the retained old database/PVC or replay ambiguous transactions. Until reverse recovery is qualified, fix forward or explicitly state the unresolved time/data boundary; do not claim zero RPO.

Keep old database/PVC/dumps and rollback coordinates through the bake. Retiring or deleting them requires the separate verified retention/decommission scope. No prune, old snapshot promotion, second-target creation, production credential rotation, device action or OTA follows from this handoff.
