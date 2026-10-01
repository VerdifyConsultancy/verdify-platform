# Isolated CNPG native runtime admission

This source prepares the missing new-cluster C0 transition. It does not admit
production, move an endpoint, provision passwords, replay migrations, or grant
device authority. The original API/ingestor/MCP seals and entire migration ledger
remain historical facts. No qualified target profile ships with this code.

## Exact contract

`scripts/cnpg-target-runtime-transition.py` consumes hash-pinned v2 source and
pre-transition target witnesses. The existing qualifier must accept full catalog
and semantic boundary equality while preserving the original three seals. The
password-free role artifact must match the source witness's nonbuiltin role names;
actual execution independently exports and checks the target's full role
attributes, settings and memberships with the existing role-parity helper.

The executor reuses the paired import's namespace/Cluster/Pod UID/operand checks
and downward API UID guard inside the selected primary. SQL independently requires
`verdify_rehearsal`, PostgreSQL **160013**, `verdify-cnpg-rehearsal`, a primary,
local socket and the original `verdify` database owner. There is no fallback,
new Pod/Job, source connection, credential installation, or retry.

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
output channel.

ROOT executes this only after actual import and independent v2 target proof:

```sh
python3 scripts/cnpg-target-runtime-transition.py \
  --source "$SOURCE_V2" --source-sha256 "$SOURCE_V2_SHA" \
  --target "$TARGET_V2" --target-sha256 "$TARGET_V2_SHA" \
  --binding "$EXACT_TARGET_BINDING" --binding-sha256 "$BINDING_SHA" \
  --source-roles "$IMMUTABLE_SOURCE_ROLES" --source-roles-sha256 "$ROLES_SHA" \
  --output "$NEW_ROLLBACK_SQL" --execute --receipt-dir "$NEW_QUALIFICATION_CUSTODY"
```

The binding object has exactly `cluster_uid`, `pod`, `pod_uid`, `operand_digest`.
The native stdout contains the rollback-qualification JSON record. Preserve its
exact bytes/hash and the surrounding UID, SQL, role and terminal receipts. A
transport timeout remains **unknown**, never inferred rolled back or retried.

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
