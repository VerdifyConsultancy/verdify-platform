# Isolated CNPG native runtime admission

This source prepares the missing new-cluster C0 transition. It does not admit
production, move an endpoint, provision passwords, replay migrations, or grant
device authority. The original API/ingestor/MCP seals and entire migration ledger
remain historical facts. No qualified target profile ships with this code.

## Exact contract

`scripts/cnpg-target-runtime-transition.py` consumes hash-pinned v3 source and
pre-transition target witnesses. The existing qualifier must accept full catalog
and semantic boundary equality while preserving the original three seals. The
password-free role artifact must match the source witness's nonbuiltin role names;
actual execution independently exports and checks the target's full role
attributes, settings and memberships with the existing role-parity helper.

The explicit `cnpg-source-bootstrap-grantor-v1` logical target profile permits
only source OID10 `verdify` to target OID10 `postgres` membership-grantor mapping.
Generate its target witness using `cnpg-c0-restore-qualification.py --target
--bootstrap-grantor-profile`; the original frozen source witness remains intact.
The target witness retains raw grantor differences and physical parity failure
separately from typed bootstrap-privilege equivalence. Owners, object ACLs,
membership flags, all ledger rows and original seals remain exact. The only
relation ACL equivalence is PostgreSQL's native default ACL for a NULL `relacl`:
`r` for tables/views and `s` for sequences, with exact owner/grantee/grantor,
privileges and grant options. Explicit nondefault/revoked grants remain distinct.
The source-derived target217/259 attesters additionally require the actual native
OID10/name/postgres/SUPERUSER fact at runtime. Rollback qualification and literal
install bind the profiled DDL hash and native before/post membership facts.
Neither the profile nor the local PG16.15 fixtures certify an actual16.13 restore.


The v3 witness keeps `raw_portable_catalog_v2` and complete separate
`portability_native_facts` for relations, indexes, constraints and triggers.
A genuinely fresh source v3 witness must reproduce the frozen source v2 catalog
SHA256 `f79f3c2d171097426f98a3aeb1eb52e71d7c04283b6e92821308099a5c090908`,
bound to the immutable source witness SHA256
`ec9b3d5aff2bbfd5ced3d1a769551053e72dadd0cb811a5a843ec7b022110e1d`.
Do not relabel the old witness or refreeze a changed current source.

Only internal foreign-key triggers with a native built-in `pg_catalog` RI
function, exact generated-name/actual-OID proof and native parent chain use
relation/constraint/function identities. Their enablement, event/type, arguments,
qualification, deferral, transition tables, referenced relation and complete
parent semantics remain exact. User triggers and other triggers retain their
original names/definitions. Duplicate typed identities are rejected. Raw source
and target generated OID names remain in their separate evidence.

The executor reuses the paired import's namespace/Cluster/Pod UID/operand checks
and downward API UID guard inside the selected primary. SQL independently requires
`verdify_rehearsal`, PostgreSQL **160013**, `verdify-cnpg-rehearsal`, a primary,
local socket and the original `verdify` database owner. There is no fallback,
new Pod/Job, source connection, credential installation, or retry.


Execution uses the existing CNPG privileged local peer path: `psql -U postgres`
on `/controller/run`. The UID-bound executor clears inherited libpq option/service/
password aliases, verifies native bootstrap OID10/postgres/SUPERUSER, then uses
`SET SESSION AUTHORIZATION verdify` solely for owner DDL. The original owner
assertions still require `current_user=session_user=verdify`. Native bootstrap
posture is checked inside the DDL transaction before COMMIT and after resetting
session authorization. Password-free role catalogs and bootstrap metadata are
independently captured and compared before/after the one execution; role export
uses explicit `-U postgres -l postgres`.

This is privileged operator execution, **zero ordinary password-authentication
credit**. It does not change HBA, create credentials, or invoke ordinary clients.
The separate authenticated-client adapter must later prove genuine ordinary
password/pool startup under qualified native target admission.

The fixed DDL changes only:

- A separate owner-only `public.cnpg_qualified_runtime_receipts` table and its
  constraints/index (including the index's catalog attribute).
- The two existing native attester definitions, derived from immutable217/259
  source. Their target guards require exact context and safe receipt shape; the
  original role checks and native digest comparison remain in place.

Native digest implementations and existing function ACLs stay unchanged. Existing
consumer startup SQL continues to call the same function signatures. Production
keeps its original definitions because this operator refuses production execution.

## Rollback-only qualification, then reviewed literal install

Before wrapping DDL, the emitter runs the existing rollback-safety classifier.
The qualification transaction checks the exact current pre-transition witness,
retains and compares full original receipt/ledger row facts, executes only the
fixed DDL, emits the real post-DDL witness, and ends with **ROLLBACK**. It populates
no target receipt and establishes no admitted runtime. No temporary schema is
introduced into catalog measurement; a transaction-local result GUC is only an
output channel. Complete expected before/post witnesses enter the same guarded
session through transaction-local GUCs before the atomic PL/pgSQL block. This
keeps the real roughly 35 MB witness out of that block's compiler body. The block
still compares the full JSONB predecessor and full reviewed successor; it does
not replace them with hashes, omit raw facts, or persist an input table. Both
inputs expire with COMMIT/ROLLBACK. Never retry the old giant block after an
interrupted qualification; retain the actual failure and rebind current target
custody first.

ROOT executes this only after actual import and independent v3 target proof:

```sh
python3 scripts/cnpg-target-runtime-transition.py \
  --source "$SOURCE_V3" --source-sha256 "$SOURCE_V3_SHA" \
  --target "$TARGET_V3" --target-sha256 "$TARGET_V3_SHA" \
  --binding "$EXACT_TARGET_BINDING" --binding-sha256 "$BINDING_SHA" \
  --source-roles "$IMMUTABLE_SOURCE_ROLES" --source-roles-sha256 "$ROLES_SHA" \
  --output "$NEW_ROLLBACK_SQL" --execute --receipt-dir "$NEW_QUALIFICATION_CUSTODY"
```

The binding object has exactly `cluster_uid`, `pod`, `pod_uid`, `operand_digest`.
The native stdout contains the rollback-qualification JSON record. Preserve its
exact bytes/hash and the surrounding UID, SQL, role and terminal receipts. A
transport timeout remains **unknown**, never inferred rolled back or retried.

The actual native record contains two full roughly 35 MB witnesses. A separate
typed reader accepts only the fixed logical/physical rollback or install
envelope, with two independently bounded witnesses and fixed metadata. Every
individual witness keeps its existing 64 MiB limit; bindings, source inputs and
other single-witness files keep their existing reader. Duplicate keys, extra
fields, wrong versions/modes, oversized individual witnesses and symlinks are
refused. The full native qualification/install validators still run after
reading; a record's larger file allowance earns no admission by itself.

Review that real record and its before/after digests. The validator requires
unchanged original ledger/seals/role identities and only the enumerated source
DDL catalog delta. The install emitter verifies the qualification mode, identical
fixed DDL hash and exact predecessor witness. Supply that independently reviewed
record and hash with the same command's additional arguments:

```sh
  --reviewed-qualification "$ACTUAL_ROLLBACK_RECORD" \
  --reviewed-qualification-sha256 "$REVIEWED_RECORD_SHA"
```

Use new output/custody paths. The install writes **only those reviewed digest
literals** into the distinct target receipts, binds them to the qualification
record hash, independently checks exact generated function bodies and the complete
post-DDL witness, verifies unchanged historical row facts, then commits atomically.
The whole exact before witness remains a transaction guard. A separate native
SQL guard before COMMIT requires every pre-existing raw relation/index/constraint/
trigger fact unchanged and only the enumerated new receipt objects. Their portable
shape and all nonallocation native fields match the reviewed post witness. Newly
allocated table/type/TOAST/index/constraint OID slots are bound to actual native
objects; the new heap `relfrozenxid` is bound to the actual DDL transaction ID.
Those allocation slots cannot equal a rolled-back rehearsal's allocations. Both
raw snapshots are retained; this distinction does not exempt existing objects or
change native digest implementations.
It never seals whatever a current digest happens to return. Failed predecessor or
successor comparison aborts all target DDL/receipts; successful installation is
not blindly replayed against a changed predecessor.

A hash pin binds bytes; it does not prove reviewer authority. ROOT's existing
source/custody review supplies that authority. Qualification and install remain
distinct from genuine ordinary-role startup/hot-query verification, password/SCRAM
provisioning, client migration, failover, PITR, and production cutover.

## Local qualification limits

`CNPG_TEST_PG_BIN` enables a private socket-only fixture. It executes the selected
217/259 attesters and complete atomic emitter, with explicit synthetic catalog
witness/native-digest stand-ins. The Mac fixture uses PG16.15 via a test-only
version substitution and proves rollback, exact-before/post refusal, literal
installation, three existing startup query paths, historical retention and
security failures. This is **not** actual PG16.13/Timescale protected-closure,
backup restore, or target admission credit. ROOT must obtain those actual facts
before creating a concrete reviewed target profile.
