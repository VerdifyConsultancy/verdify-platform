# #396: current-storage CNPG alongside rehearsal

## Current delivery checkpoint

The preparation commands and post268 sequencing below are historical procedures,
not assertions that today's restore and recovery proofs are unexecuted. The
immutable [current274 physical proof](../handoffs/s2-2026-10-05/evidence/cnpg-current274-physical-full-parity.json)
records authentic full restore/PITR and the measured primary-Pod-loss boundary.
The additive [current275 recovered-candidate proof](../handoffs/s2-2026-10-05/evidence/cnpg-current275-recovered-candidates.json)
records separate native bridge/admission installations, complete A/B preservation
through natural primary switches, and the rejected S2 scheduler-loss attempt.
The [cutover/rollback packet](cnpg/current274-cutover-rollback-packet.md) carries
actual measured risks and both write-boundary rollback strategies for #245.

The owning cluster render now includes the exact existing frozen A/B declarations,
completed S2 Backup, independent reader ObjectStore and archive-reader policy.
Reconciliation must first verify their existing UIDs/specs: adoption is not a new
restore, backup trigger, credential install or recreation of immutable Cluster
bootstrap fields. Preserve all failed and historical targets; prune stays off.
The AppProject already permits these resource kinds and denies Secrets. Existing
reader/writer Secret references remain unchanged and values never enter this source.

Source publication and rendering alone do not complete #396. Actual ordinary
A→B→A endpoint/Grafana qualification, committed spool plus exact inverse, final
independent952/77/42/native reseal and exact-revision Synced/Healthy owning Argo
reconciliation remain open. ROOT serializes delivery and Iris owns active client
qualification; this lane performs no production database endpoint cutover.

Status: source preparation; operand image and a scratch extension ABI fixture are
qualified. No Verdify CNPG deployment, role-complete restore, failover or PITR
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
Qualified operand (source `3a4bc792936fd8c104eb5884fa351d542e7e0453`, native
Workflow `verdify-cnpg-operand396-wx5xd`, UID
`a620db8d-7153-4ec8-a213-92e3ff2fa5f5`):

`registry.vallery.net/verdifyconsultancy/verdify-timescaledb-cnpg:16.13-ts2.25.2@sha256:8b461e37d18aa049704eb6a9cde2ba9af0f955f8d3725e921070d2450bb2f137`

Primary-origin manifest/config hashes and OCI source were checked. Native apt
installation initially upgraded the pinned PostgreSQL16.13 base to16.15; the
forward Dockerfile holds `postgresql-16` and asserts final16.13. Original candidate
failure-of-version evidence is retained. Base/pgvector source hashes are pinned.
The existing base OCI label is empty due to pre-FROM ARG scope; source bytes,
not that label, prove its base pin.

One actual emptyDir/socket-only Job `verdify-cnpg-operand-sql396-20261001`
(UID `dc21200f-d3fb-41a6-8a72-4d8a836e9244`) exited0 in44 seconds. PostgreSQL16.13,
Timescale2.25.2 and vector0.8.1 loaded; vector distance was2; 128 typed rows
compressed to one chunk and decompressed with identical row hashes. The tiny
fixture's compression overhead warning remains recorded. This is scratch ABI
proof, not production restore, compression-performance or recovery acceptance.

The checked-in `deploy/k8s/cnpg/rehearsal/namespace` and `cluster` sources carry
this exact operand. A namespace-only child Application allows the existing
reflector and Garage create-only actuator to establish bindings before the
cluster child is synced. Both use one dedicated project/destination, manual
sync and no prune. There is no production workload or endpoint in either render.

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
NetworkPolicy. Egress is same labeled rehearsal peers, cluster DNS/API (service/VIP and the observed .31/.32/.33 backend endpoints) and current
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
password-free role/member/owner/ACL parity. CNPG's postgres, streaming_replica, rehearsal_bootstrap and
cnpg_metrics_exporter management roles must be enumerated separately, not silently
excluded. Existing table/function/schema/source-ledger hashes and all hypertable
counts/time bounds must match the actual frozen pair. A failed import is preserved,
not normalized into success. Later migrations use exact serialized source and
existing C0 runner boundaries; never manually reseal changed predecessor roles.

Restored ordinary/C0 roles also acquire different catalog OIDs, so copied sealed
receipts cannot be treated as actual current boundaries. Native C0 new-cluster
qualification must establish the supported authority transition without manual
resealing or editing applied migrations.

The adapter now exists as `scripts/cnpg-paired-restore.py`, using the existing
paired restore and audit source. It accepts exact Cluster/Pod UIDs and the
qualified operand, compares the downward-API UID inside the selected pod before
staging/execution, transfers one verified pair with all script/source hashes to
on-PVC `restore-custody`, and rechecks those hashes natively. It refuses existing
import databases or custody paths, a standby, wrong server/cluster/owner, remote
connections, unqualified images, changed source witness, management-role
collisions or changed postgres posture. It never starts/stops CNPG's postmaster,
sets a production endpoint, provisions passwords, refreshes a C0 seal or retries.
Partial imports, original pair, source witness and all failure output are retained.
The total native restore budget is1800 seconds; individual statements180 seconds.

### Private writable custody after a failed import

The 2026-10-01 actual first import stopped before role replay because the CNPG
root filesystem made `/tmp/roles.before.sql` unwritable. Its original
`/var/lib/postgresql/data/restore-custody` and failure output remain evidence;
they are never overwritten or resumed. This is a failed import, not restore proof.

The source adapter supports a new exclusive `restore-custody-<8..32 lowercase
alphanumeric characters>` directory under PGDATA. Add these options to the
original UID/image/source-pair-bound invocation, using independently captured
literal hashes and a new receipt directory:

```text
--stage-name restore-custody-<unique-token>
--prior-custody-manifest-sha256 <original custody.sha256 file SHA256>
--management-before <password-free original four-management-role dump>
--management-before-sha256 <that artifact SHA256>
```

Before allocating this path the adapter checks the original manifest hash and
all original staged checksums inside the exact UID-bound pod. All scratch files
then stay in exclusive private `<new-stage>/work`, owned by UID26.
Custody mode must be exactly0700 or02700: Longhorn fsGroup may preserve SGID
inheritance; group and other access must still be zero. The native
empty-database/primary/server guard still runs before role replay. The current
password-free management dump must match the supplied original dump exactly
except PostgreSQL's random restrict/unrestrict transport tokens.
`cnpg_metrics_exporter` must retain its exact non-superuser/non-create/non-bypass
login posture and only the captured `pg_monitor` membership; collisions,
additional settings or changed membership fail closed. Its verified management
profile is separately enumerated, rather than credited as restored source data.
Standalone restores retain a fresh private `mktemp` directory under TMPDIR.
The ordinary path never retries partial role or database mutation. A separate
membership-prefix continuation below accepts only the captured R3 failure.

### Explicit bootstrap-grantor target profile and R3 continuation

The frozen source witness proves source bootstrap OID10 is `verdify`; native CNPG
proves target bootstrap OID10 is `postgres`. PostgreSQL16 rejects the original
`GRANTED BY verdify` memberships on this target even with restored SUPERUSER.
The optional `cnpg-source-bootstrap-grantor-v1` profile deliberately translates
only the 17 source membership grantors to native bootstrap `postgres`. It keeps
raw role-byte parity **false** and records each original/translated statement.
Role/member names, ADMIN/INHERIT/SET flags, ownership, ACL grantors, settings and
all other catalog facts remain exact. It adds no temporary ADMIN membership.
The default comparison still rejects any grantor change.

For ROOT's reviewed new exclusive attempt, add these to the original bound
invocation and the new-stage/management options above:

```text
--bootstrap-grantor-profile
--role-prefix-custody <hash-pinned R3 six-field descriptor JSON>
--role-prefix-custody-sha256 <descriptor file SHA256>
--role-prefix-current <captured password-free roles-after-r3.sql>
```

The descriptor binds the old stage name and original manifest, before-role,
replay, error and current-role artifact hashes. Before allocating a new stage,
the adapter rechecks the retained old native files and manifest. Before SQL, it
requires the product DB absent, the full current role prefix equal to captured
source attributes/settings plus the exact four CNPG management roles, zero
source memberships, and the exact first-GRANT failure. It verifies the old replay
was source-derived. Only membership GRANTs run in one BEGIN/COMMIT transaction;
full role verification precedes database creation. Unknown or drifted partial
states fail closed; this is not generic skip/resume. Preserve R1/R2/R3 unchanged.
No actual R4 restore or runtime admission is credited by source qualification.

`cnpg-c0-restore-qualification.py` emits read-only source/target witnesses. Source
PG16.11 and targetPG16.13 are fixed; Timescale2.25.2/vector0.8.1 and the exact268
runner predecessor are required. It independently verifies the source-pinned
217 ordinary and263 MCP projections against their installed native digests and
function body/owner/security/search_path posture. It compares every original
ledger identity and original seal, API/ingestor/MCP catalog preimages with typed
role-OID/database-name translation, and a name-based full user object/ACL/body
catalog covering the six workload scopes. No arbitrary digit replacement occurs
inside SQL bodies/defaults. Role attributes/settings/memberships are separately
compared to the exact password-free artifact; CNPG management roles are separately
enumerated. The source witness also supplies typed source database privileges,
restored only on the isolated DB through supported owner GRANT/REVOKE.

The actual post268 **read-only source** witness passed on2026-10-01:303API,
302ingestor,356MCP entries and44,152 user catalog entries. This proves that the
source projection works; **no new-cluster logical restore or runtime transition
has been qualified**. The comparison receipt explicitly says
`runtime_transition_installed:false`. Original copied seals intentionally leave
ordinary attestation fail-closed on changed OIDs. A runtime-admitting transition
must be delivered by a separately serialized owning native C0 source contract,
with actual restore fingerprints and archived original seals. Existing240–248
profiles are not reused or modified. Do not manufacture successor literals or
reseal a predecessor manually.

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

## Historical source and operator start sequence

The product source owns the two renders; Agents owns project
`app-verdify-cnpg-rehearsal` and Applications
`verdify-db-rehearsal-namespace` / `verdify-db-rehearsal-cluster`; Storage owns
`plans/garage/verdify-cnpg-rehearsal.plan`. No source was merged or enacted by
this preparation. Deliver through each owner's existing checks after the active
Verdify pin actuator completes. Preserve the three original preparation trees.

The shared roots were read as `agent-fleet-appprojects` at `89575b6655c24a39fec9156fc7fa9db1f5007d7e`
(OutOfSync) and `agent-fleet-local-prod-apps` at `1a1cad6c5ab164bae47a47f1b6ed9937c0059e99`
(Synced). They do not automatically adopt current main. Their owner must adopt the
qualified Agents catalog/project source after reviewing every pending root diff;
do not repoint or whole-sync those shared roots as a side effect of this rehearsal.
The existing direct exception ledger is source bookkeeping for manual child
actuation, not a new human approval or soak requirement.

After exact source delivery and ROOT's six-role production adoption, the actual
remaining operator commands are below. Variables name the delivered immutable
source and owning checkouts, not guessed hashes. These are commands to run later;
none was executed by this preparation.

```sh
: "${VERDIFY_CNPG_SOURCE_SHA:?exact delivered product rehearsal revision}"
: "${AGENTS_CNPG_SOURCE_SHA:?exact delivered Agents revision}"
: "${STORAGE_CNPG_SOURCE_SHA:?exact delivered Storage revision}"
: "${AGENTS_CNPG_CHECKOUT:?clean exact Agents checkout}"
: "${STORAGE_CNPG_CHECKOUT:?clean exact Storage checkout}"
argocd app diff agent-fleet-appprojects --revision "$AGENTS_CNPG_SOURCE_SHA"
argocd app diff agent-fleet-local-prod-apps --revision "$AGENTS_CNPG_SOURCE_SHA"
# Owning root source adoption follows its existing release path; retain unrelated drift.
argocd app sync verdify-db-rehearsal-namespace --revision "$VERDIFY_CNPG_SOURCE_SHA"
kubectl --context vallery -n verdify-db-rehearsal get secret zot-origin-cluster-pull -o name
python3 "$STORAGE_CNPG_CHECKOUT/scripts/garage-bucket-plan.py" --check
CG_MAINTENANCE_KUBE_CONTEXT=vallery \
  bash "$AGENTS_CNPG_CHECKOUT/control-plane/change-gate/change-gate.sh" \
  --target garage --change-id verdify-cnpg396-rehearsal \
  --plan "$STORAGE_CNPG_CHECKOUT/plans/garage/verdify-cnpg-rehearsal.plan"
kubectl --context vallery -n verdify-db-rehearsal get secret \
  verdify-cnpg-rehearsal-s3-reader verdify-cnpg-rehearsal-s3-writer -o name
argocd app diff verdify-db-rehearsal-cluster --revision "$VERDIFY_CNPG_SOURCE_SHA"
argocd app sync verdify-db-rehearsal-cluster --revision "$VERDIFY_CNPG_SOURCE_SHA"
kubectl --context vallery -n verdify-db-rehearsal get cluster,pod,pvc
```

Do not prune either child. The create-only Garage actuator rechecks authoritative
absence immediately before execution and owns its exact rollback/dead-man; no
manual keys, Secret copies or fabricated binding metadata. The existing
`zot-origin-cluster-pull` reflector source already permits every namespace except
`frigate-dev`; no additional pull identity or Secret declarer is needed here.

## Historical post-268 recovery source procedure

Use the actual post-268 runner ledger/source checksum, all six runtime workload
role boundaries and the three preserved API/ingestor/MCP sealed receipts from ROOT's production
readback. Preserve a before/after production identity readback around the fresh
backup capture, not an old pre-268 dump. Trigger the existing paired backup only
after that adoption, if its next scheduled pair is not yet available:

```sh
kubectl --context vallery -n verdify-prod create job \
  --from=cronjob/verdify-db-backup verdify-db-backup-cnpg-post268-20261001
kubectl --context vallery -n verdify-prod get job verdify-db-backup-cnpg-post268-20261001
kubectl --context vallery -n verdify-prod logs job/verdify-db-backup-cnpg-post268-20261001
```

Retain its exact Job/Pod UID, source/image, artifact stem, password-free roles,
commit marker and both hashes. This one-off job is a fresh recovery source; it
is not a new claim of scheduled-backup acceptance. The existing paired restore
procedure remains the artifact verifier; production dump PVC remains read-only
for recovery consumption. It is namespace-scoped and cannot be mounted directly
by CNPG in the dedicated namespace: the CNPG import adapter must use a bounded
verified stream into scratch storage on the exact rehearsal primary, never a
second declaration or writable mount of the production dump PVC.

## Historical initial import requirements

The source-owned CNPG role-complete import adapter is implemented in
`scripts/cnpg-paired-restore.py`: it consumes an exact verified pair, preserves
owners/ACLs and accounts separately for CNPG management roles. Its execution and
actual restored-cluster qualification remain unearned. The v2 catalog witness
retains full text identities and rejects duplicates; v1 truncated witnesses remain
historical evidence and cannot qualify the import. The
current C0 release profiles depend on original database/role OIDs and target
PostgreSQL16.11; they do not authorize a logical new-cluster16.13 receipt reset.
A source-owned new-cluster C0 qualification/transition is needed before restored
ordinary logins can be credited. Copied seals stay unchanged as evidence; no
manual reseal, source-hash edit or replay of applied migrations is permitted.
Garage resources/bindings, dedicated owning root adoption, actual CNPG placement,
role-complete restore, WAL backup/PITR, measured failover and #245 cutover/rollback
are still unearned. Current storage/CPU observations are capacity evidence, not a
reservation or future schedulability guarantee.


## Historical initial pair import command

After the reviewed owner source is adopted, namespace bindings and the CNPG
cluster are healthy, ROOT's six-role production release has completed, and one
fresh post268 (or later exact serialized successor) backup has succeeded, preserve
its actual UID/stem/hash marker and copy that immutable pair to an operator custody
directory. The production dump PVC never moves or becomes writable to CNPG.
Capture a fresh source witness around that backup and retain any observed catalog
changes; a different catalog is a refused qualification, not permission to reseal.

```sh
python3 scripts/cnpg-c0-restore-qualification.py --output source-witness.sql
bash scripts/verdify-db.sh prod -X -qAt -v ON_ERROR_STOP=1 < source-witness.sql > source-witness.json
# Record actual source-witness SHA, pair stem and primary Cluster/Pod UIDs.
python3 scripts/cnpg-paired-restore.py \
  --cluster-uid "$REHEARSAL_CLUSTER_UID" \
  --pod "$REHEARSAL_PRIMARY_POD" --pod-uid "$REHEARSAL_PRIMARY_UID" \
  --pair-dir "$VERIFIED_PAIR_CUSTODY" --stem "$ACTUAL_BACKUP_STEM" \
  --source-witness source-witness.json --source-witness-sha256 "$SOURCE_WITNESS_SHA256" \
  --receipt-dir "$NEW_IMPORT_RECEIPT_DIRECTORY"
```

This command is prepared, not executed. Then emit `--target` SQL, run it only
through the exact primary's local socket under the same UID guard, preserve its
JSON, and compare with `--source source-witness.json --restored target-witness.json
--output new-cluster-qualification.json`. No client may be reported admitted
until the separately qualified native C0 transition and actual role hot queries
pass. Qualification does not establish failover/PITR/cutover acceptance.
