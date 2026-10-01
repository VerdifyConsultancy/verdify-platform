# Physical A/B runtime admission

This is a source-only preparation path for `verdify-cnpg-pitr-a` and
`verdify-cnpg-pitr-b`. `scripts/cnpg-physical-runtime-transition.py` never connects,
creates a Pod, acquires credentials, requests a backup, or executes SQL. It emits
rollback qualification SQL by default. Existing logical admission's public CLI
still admits only `verdify-cnpg-rehearsal`.

## Trust inputs and required actual evidence

Physical recovery preserves product database OIDs and catalog facts. Its copied
logical attesters correctly refuse the new `cluster_name`. A copied logical
receipt table is historical evidence, not new-cluster permission.

Supply SHA256-pinned native captures for the full chain:

1. A genuinely fresh v3 original source witness whose retained raw v2 catalog
   matches the frozen ec9 source artifact, logical before witness, actual rollback
   qualification and actual successful logical install record. Preserve both the
   v3 semantic catalog and full raw v2/native facts. The original source comparison
   and source-owned DDL hash checks must pass independently.
2. Physical before witness **exactly equal** to the actual logical installed
   post-witness, validated against its independently reviewed rollback result. Preserve all inherited native roles, namespaces, full catalog,
   original ledger/seals, database owner/ACL, source-pinned implementations and
   the qualified bootstrap-grantor profile. Only newly allocated receipt-object
   OID slots may differ between rollback and install; their exact native shapes
   and relation/index/constraint links are checked. Every pre-existing raw fact
   stays exact. Do not normalize additional deltas.
3. Exact copied logical receipt rows, independently calculated from the qualified
   logical post-witness and original rollback artifact's hash. These are three
   `[login_name, boundary_sha256_hex, qualification_sha256]` rows sorted by login.
4. Native source Cluster, actual completed Backup, genuine A/B/C transaction and
   target-clock custody used by the source-owned PITR pair renderer. When a
   ScheduledBackup owns the Backup, include that raw owner object in the
   physical provenance record. Validate all original capture bytes/content.
5. Physical binding: fixed namespace/name, **new** Cluster and primary Pod UIDs,
   expected Pod name and original qualified operand digest. Keep original/new
   Cluster, Pod, PVC/PV, native system identifiers, timeline history and physical
   domains in actual custody. Before execution, ROOT verifies live objects and
   default-grace/nondestructive authority; offline bindings are not live readback.
6. Independently review native recovery-target-reached logs, native reader-only
   archive/base-backup/WAL inventory through C, and actual recovered sentinel
   rows. Retain their byte hashes. Hashes bind custody; they cannot prove that
   supplied logs are genuine or that WAL is correct. Actual source-bound native
   capture and ROOT inspection remain necessary, never a receipt substitution.
7. Actual source/physical dataset witnesses use schema
   `cnpg-physical-data-parity-v1`, database `verdify_rehearsal`, `relations` and
   `timescale_owners`. Relation rows contain `relation`, native `count`, and
   `time_ranges` mapping each native timestamp column to `[min,max]` or
   `[null,null]`. The relation inventory must cover **every public relation** in
   the native inherited C0 catalog, not a sampled table. Collect in an owner
   read-only transaction. Timescale rows contain parent/parent_owner,
   chunk/chunk_owner, compressed/compressed_owner; include every actual managed
   chunk and companion. Full inherited catalog equality independently protects
   all original owners and definitions. Count/time/owner comparison is not a
   replacement for successful native backup/WAL retrieval and physical restore.

`cnpg-physical-target-provenance-v1` records the complete binding, original source
UID, actual Backup UID/ID, numeric source timeline, exact target time, native log,
archive-inventory and sentinel capture hashes, exact recovered A-only or A+B
marker IDs, and optional raw ScheduledBackup owner object. The target-reached and
archive files retain their native formats for independent ROOT review. The
structured native sentinel capture contains `{binding,database,
server_version_num,cluster_name,pg_is_in_recovery,markers}`; actual marker rows are
`{marker_id,payload_sha256}` sorted by identity. Each original A/B/C capture must
also include its actual payload hash. Target A excludes B/C; target B excludes C.

The source-owned collector emits native read-only SQL for the original admitted
logical source or either fixed new physical target:

```sh
python scripts/cnpg-physical-runtime-transition.py emit-dataset-sql \
  --profile verdify-cnpg-rehearsal --output "$CUSTODY/native-data-source.sql"
# Select verdify-cnpg-pitr-a or -b explicitly for its fresh target capture.
```

The emitted SQL connects through native postgres-only peer, checks the exact
bootstrap posture, uses guarded SET SESSION AUTHORIZATION verdify for the owner
read-only snapshot, verifies unchanged bootstrap facts and returns to postgres.
This SQL requires the exact native local primary session, explicit target
name, PostgreSQL160013 and Timescale2.25.2; captures every public relation count,
every native timestamp min/max, and all managed parent/chunk/compressed owners
under one repeatable-read read-only snapshot. It acquires no external endpoint
and refuses foreign tables. ROOT uses its current UID-bound socket operation and
retains raw stdout/stderr/status. The private PG16.15 synthetic fixture checks
collector SQL/count/range/nullable compressed joins and verifies the unmodified
native guards reject the fixture; it is not actual16.13/Timescale/PITR proof.

## Controlled catalog delta

The closed internal template profile accepts only the two names above. It adds
owner-only `public.cnpg_physical_runtime_receipts` with the same strict three-row
shape as logical admission, and replaces only the ordinary and MCP attesters.
Each attester binds its **own exact new cluster name**, PostgreSQL 160013,
`verdify_rehearsal`, primary state and owner/receipt shape. The unchanged original
217/259/263 digest functions perform the native boundary comparison.

Original historical receipt rows, ledger and the complete copied logical
receipt table are retained. Qualification locks/checks copied logical rows
before and after DDL. Both semantic and retained raw v2 catalog deltas are checked. The complete
pre-existing native relation/index/constraint/trigger arrays stay exact in the
atomic transaction; only the explicitly enumerated new physical receipt table,
primary index and four constraints may allocate new OIDs, with native linkage
checks before commit. No membership, role, ACL, original seal or ledger repair
is performed by this transition. The same owning rollback-safety classifier refuses
self-committing statements. Rollback qualification persists no table or receipt.

After independently inspecting the actual new native post-DDL witness and exact
allowed object delta, pass the original rollback artifact and its externally
reviewed hash to emit the installation. It inserts only the three literal native
post-DDL digests, rechecks complete native before/post witnesses, generated
attester bodies and original history, then commits atomically. It never seals a
freshly computed current digest without the reviewed native predecessor. A/B DDL
hashes differ because their exact identity guards differ; one qualification
cannot admit both targets.

## Actual ordinary client source

`scripts/cnpg-physical-client-qualification.py` renders three isolated Jobs for
one fixed A/B target at a time. It consumes the same full SHA-pinned original
source/logical install/physical predecessor/Backup/WAL/marker/dataset inputs and
raw proof files as the owning physical admission emitter. Additionally provide
`--physical-install` and its SHA, `--native-binding` and its SHA, the actual
reviewed physical rollback artifact/SHA, and the consumer/image/source/module
hash/unique suffix. The physical installation must match that reviewed source
DDL and semantic post-witness, retaining every pre-existing raw fact and only
the typed new allocation slots. Copied logical receipts alone cannot enable it.

The native binding has exact keys `cluster`, `pod`, `pods`, `nodes`, `pvcs`,
`pvs`, `service`, `object_store`. It binds the admitted new Cluster/current-primary
UID, all three ready digest-pinned Pod UIDs/addresses, three node UID/physical
host domains, all six PVC/PV UIDs/handles and claim/owner links, and RW Service
UID/owner/selectors. Live reader ObjectStore and new Cluster bootstrap recovery,
original source serverName, successful Backup ID, actual time boundary, operand,
placement/storage and absence of an archive writer must match the owning renderer.
ROOT retains fresh before/after native bindings; offline JSON is custody rather
than live verification by this renderer.

The existing 883 consumer source is reused directly: baked revision/module
hashes, real API init/setup callbacks, ingestor startup attestation, MCP pool
helper, two asyncpg pool checkouts, readonly posture, exact hot SQL and private
exception handling. Only the two closed identity literals and the physical
result custody fields differ; no arbitrary host/profile or SQL escape exists.
The original-target adapter file and its behavior remain unchanged. Each Job
uses only its new target-specific Secret, for example
`verdify-cnpg-pitr-a-api-client-auth/password`. ROOT/Agents must provision fresh
ordinary-role password/SCRAM credentials on that exact admitted physical target;
restored hashes/old source credentials provide zero authentication credit.
No owner credential or Secret/API RBAC is present in the client. Narrow per-target
5432 and conjunctive CoreDNS NetworkPolicies, readonly transactions and device
gate zero remain mandatory. No service main/device connection starts.

Rendered Jobs and driver-double tests are not authenticated acceptance. Only
actual ROOT-run Jobs in the pinned consumer images can prove the ordinary pool
login/startup/hot-query behavior. Their success record retains native backend
identity, target-binding/qualification hashes, new Cluster UID, successful
Backup UID/ID, target-reached/archive custody hashes and actual target time;
`inherited_authentication_credit` is explicitly false.

## Execution custody

ROOT must obtain the current new Cluster/primary UID, exact digest and local
primary identity immediately before execution, then use the downward Pod UID
check and native postgres-only peer session. The emitted owner SQL performs
the guarded bootstrap-to-owner bridge and returns to postgres; it provides zero
ordinary-password authentication credit. Preserve raw SQL bytes, stdout, stderr and
exit/unknown outcome in a new exclusive private directory. Do not infer rollback
from a timeout or retry an unknown result. Recheck roles, complete raw history,
logical rows, actual data and the resulting native target witness afterward.

No native physical installation or actual client authentication has run. Tests
and source rendering do not satisfy C5 recovery acceptance.
