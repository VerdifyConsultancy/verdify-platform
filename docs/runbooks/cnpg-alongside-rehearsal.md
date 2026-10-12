# #396: current-storage CNPG alongside rehearsal

Status: source preparation; no Verdify CNPG deployment, restore, failover or PITR
has been performed by this change. Production `DB_HOST=verdify-db`, its
StatefulSet, PVC, role credentials and writer stay unchanged. #245 retains the
actual attended cutover. #670 and #672 are closed predecessors; their real
role-complete pair and compressed ownership checks must be consumed again here.

## Current baseline and candidate

2026-10-01 read-only baseline: PostgreSQL16.11, Timescale2.25.2, vector0.8.1,
pgcrypto1.3, database4,042,273,251 bytes. Existing CNPG and Barman controllers are
ready. No Verdify CNPG cluster exists. The old `cnpg/dev` files use node-local
images, node5 pinning, one instance, local-path and no WAL store; their restore
helper suppresses `pg_restore` errors and excludes ownership. Do not execute
those as acceptance procedures.

`scripts/render-cnpg-rehearsal.py --image <qualified-origin-image>` renders an
isolated `verdify-db-rehearsal/verdify-cnpg-rehearsal` three-instance candidate. Image must
be `registry.vallery.net/verdifyconsultancy/verdify-timescaledb-cnpg:16.13-ts2.25.2@sha256:<actual digest>`.
CNPG needs the leading PostgreSQL-major tag even when pulling by digest. Build
only this operand via existing `repo-build-verdifyconsultancy`, exact committed
source and `deploy/k8s/cnpg/image/Dockerfile`; never substitute a fake digest.
Base digest and pgvector0.8.1 tarball hash are pinned; running extension versions
and Timescale community compression support remain runtime acceptance checks.

Three required distinct `topology.vallery.net/proxmox-host` domains, synchronous
quorum1 with `dataDurability: required`, `synchronous_commit=on` and failover
quorum deliberately favor committed-data durability over writes when insufficient
standbys exist. Measure both stall and recovery. Existing `longhorn-v1-rwo` has
two storage replicas and best-effort locality. This is the declared rehearsal
tradeoff: six logical30/10Gi volumes total120Gi, potentially240Gi replicated
storage, with three1Gi memory and500m CPU requests. It does not claim the
Research/Cortex-only strict-local R1 contract. No storage admission is widened,
no static host/disk map, shed, reservation or infrastructure change is introduced.

The candidate includes a Barman ObjectStore, ScheduledBackup and narrow
NetworkPolicy. Egress is same labeled rehearsal peers, cluster DNS/API and current
Garage ingress192.168.7.10:443; there is no production DB/device route. The dedicated recovery namespace is explicitly disposable database qualification,
not a second product environment: no API, MCP, ingestor, planner, device writer or
product route is declared. Its Namespace has restricted Pod security and a namespace-wide
default-deny policy; only labeled CNPG/qualification peers and shared operator ports
are allowed. The separate Agents project permits Namespace creation and only `verdify-db-rehearsal`, no Secret reconciliation or
production destination. The Storage inventory request uses a dedicated
`verdify-cnpg-rehearsal` bucket and reader/writer key names; the existing Garage
actuator establishes values without exposing them. No production backup key is
reused. CNPG bootstrap credentials are operator-managed throwaway identity;
production restored LOGIN passwords are not dumped or provisioned by this lane.

## Execution coordinates and isolation

Before reconciliation, record exact product/Agents/Storage commits, operand
manifest/config bytes and OCI source, rendered resource hashes, backup-pair stem
and each checksum, operator image identities, node domains/requests and available
Longhorn space. Render only after actual origin image qualification. Reconcile
through owning Argo sources with no prune; keep each Cluster/Pod/PVC/PV and
Garage target identity in a private receipt. Never run a restore on `verdify-db`.
The new candidate bootstrap database is `rehearsal_bootstrap`; imported product
DB is `verdify_rehearsal`. The old backup PVC remains read-only and retained.

## Role-complete Timescale import

Use existing `verify-backup-pair.sh`, real `.roles.sql`, pair metadata/dump and
ownership/audit scripts. Import into the empty isolated CNPG primary, through its
local PostgreSQL socket, after checking Cluster/Pod UID and database identity.
Do not run `restore-backup-pair.sh` unchanged: it starts its own socket-only
postmaster, while CNPG owns this postmaster. The CNPG adapter must restore the
artifact roles, create `verdify_rehearsal` with the artifact owner, call
`timescaledb_pre_restore`, restore with `--exit-on-error --role <artifact owner>`
and exact ownership (never `--no-owner`), call post_restore, refresh the supported
matviews, then run supported parent ownership repair/adversarial tests and
password-free role/member/owner/ACL parity. CNPG's postgres and
rehearsal_bootstrap management roles must be enumerated separately, not silently
excluded. Existing table/function/schema/source-ledger hashes and all hypertable
counts/time bounds must match the actual frozen pair. A failed import is preserved,
not normalized into success. Later migrations use exact serialized source and
existing C0 runner boundaries; never manually reseal changed predecessor roles.

Restored ordinary/C0 roles also acquire different catalog OIDs, so copied sealed
receipts cannot be treated as actual current boundaries. Native C0 new-cluster
qualification must establish the supported authority transition without manual
resealing or editing applied migrations.

The adapter is a remaining concrete source requirement: no role-complete CNPG
import claim is earned by a successful standalone Timescale restore.

## Measured acceptance sequence

1. Restore parity: actual extension versions, every hypertable/compressed-chunk
   ownership invariant, counts/time bounds, role/ACL/function/ledger parity and
   read-only application hot queries under intended roles. Preserve actual
   source dump coordinates; ongoing production data is a known snapshot delta.
2. Isolated replay: use finite native application fixtures with device writing
   denied and isolated endpoints; inspect immutable IDs, retries and least
   privilege. Do not connect an ingestor to ESP32 or replay production queues.
3. Replication: show three physical domains, healthy instances, actual sync
   standby, replication/replay LSN and lag. Commit a rehearsal-only sentinel
   through the `-rw` service. Observe commit completion and standby presence.
4. Primary loss: reuse the existing Agents CNPG ordinary primary-Pod-loss
   qualification mechanics with exact Pod UID, client through `-rw`, and no
   host fence/reboot. Preserve failed probes, fault/first-success monotonic
   timestamps, promotion identity, all committed sentinel IDs and RPO/RTO.
   A primary Pod deletion does not establish abrupt physical host-loss fencing.
5. Backup: trigger native Barman plugin Backup; prove actual base backup completed
   and continuous WAL archived to the dedicated Garage target. Preserve backup
   ID, start/end LSN/timeline/time, plugin/controller/pod and object identities.
6. PITR: commit sentinelA, bind a target LSN/time before sentinelB, commit B and
   confirm WAL archive. Restore into a separately named isolated CNPG cluster
   with the same operand digest, explicit source backup/timeline/target and
   reader identity. Prove A present/B absent and all pair-era data/role/ACL
   invariants; measure restore RTO and actual recovery point. A Ready pod alone
   is insufficient. Preserve new PVC/Cluster UIDs and any failed attempt.
7. Retain all original and recovered evidence until exact owned-resource cleanup
   is reviewed; never delete the production database or original dump PVC.

## Exact cutover/rollback packet for #245

Deliver actual measured RPO/RTO, source/import identities, extension/storage and
failure-domain tradeoffs, continuous archive/base backup/PITR receipts, current
snapshot delta and a measured supported final-delta strategy. Do not use generic
`COPY newer rows` for immutable ledgers/chunks or assume logical replication
supports compressed hypertables. Rehearse application endpoint flip and reversal
using isolated clients only; measure downtime and post-flip write loss if reverting
to the preserved old snapshot. Record old/new endpoint, source ConfigMap/env and
release revisions, role credential provisioning owner, exact fenced writer order,
revert resource/digest coordinates and retained PVC/backup identities. Production
endpoint, quiesce, final delta, actual flip and decommission belong to ROOT/#245;
this alongside lane performs none of them.

## Remaining blockers

No qualified custom operand origin digest, Garage binding/credentials, owning
Argo candidate adoption or CNPG role-complete import adapter exists yet. Thus
restore/failover/PITR/cutover qualification remains unearned. This source removes
obsolete assumptions and gives explicit resources and proof boundaries; it does
not turn those missing runtime facts into synthetic success.
