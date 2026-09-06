# C0 exact-boundary transaction qualification

Campaign #775 / restore qualification #783 / C0 release PR #793.
`scripts/c0-boundary-transition.py` emits one transaction for the exact seven
migrations 241–247 and both ordinary-login attestation receipts. It never
connects to a database. **No approved production contract ships with it, and
the normal migration runner/image is not yet wired to invoke it. The release
hold remains.** Do not run emitted SQL manually against production.

## Trust and scope

Migration 217 makes both ordinary applications fail startup if their catalog
boundary differs from the stored receipt. C0 legitimately changes that boundary.
Migration 247 removes the untracked private capture-helper dependency, but does
not itself rotate receipts. A fresh digest is an observation, not approval.

This tool requires independently reviewed exact predecessor and successor
fingerprints for **both** logins. It verifies the current predecessor against
both the supplied value and the existing receipt; applies the immutable source;
then compares the full resulting catalog against the independently supplied
successor. Only the approved literal successor bytes can be written. It does
not infer approval from changed object names, recapture arbitrary state, disable
startup guards, rerun 217, or introduce a new runtime permission/function.

The contract's SHA256 must be pinned in the owning reviewed delivery source.
Passing `--contract-sha256` authenticates bytes against that pin, not who approved
them. A person able to replace the tool, contract and pin already controls this
release authority. An arbitrary JSON file or synthetic test output is not an
approved production contract.

The full 217 fingerprint contains catalog identities, including numeric ACL
grantees, database names/settings and PostgreSQL-deparsed definitions. A restore
can assign different role OIDs. **Do not transplant fixture/restore digests into
production or assume matching names imply matching fingerprints.** Target-specific
catalog mapping and the reviewed before/after projection must establish exactly
which hashes the owning target may accept. The database-name/version checks do
not uniquely identify a cluster; the owning job/broker must bind the target.

## Transaction behavior

1. Verify exact source hashes for 217 and the seven fixed release files. No file
   path or executable SQL comes from the contract. Validate bounded JSON fields,
   exact PostgreSQL 16 version, database name and the two supported logins.
2. Emit `psql` input with `ON_ERROR_STOP`, one transaction, the existing migration
   advisory lock, bounded statement/lock/idle timeouts, and exclusive ledger and
   receipt locks. Require a direct database-owner session.
3. Verify installed 217 digest/attestor source, owner, language and search path;
   verify the pgcrypto C digest binding before invoking it. Recompute the exact
   pinned catalog projection with built-in SHA256 as an independent comparison.
   Fingerprint deparsing uses 217's `pg_catalog, pg_temp` context.
4. Check the full prior ledger identity across streams (source, filename,
   sequence, hash, stamp method) and exactly two existing receipts. Reject
   unexpected ledger hooks, shape, partitioning, RLS, constraints, indexes or
   non-owner table/column write privileges.
5. Require either no C0 release stamps plus the exact trusted predecessor, or
   all seven exact stamps plus the trusted successor and matching receipts.
   The latter is a no-write retry. Partial, edited or stale states are refused;
   no baseline is invented and no intermediate release is silently resumed.
6. Apply the seven exact embedded sources and insert normal `runner` ledger
   stamps, supplying every value explicitly instead of invoking an untracked
   stamp helper/default. Check both full successor boundaries before writing
   either receipt. Update the two receipts with approved literals, verify again,
   and commit all DDL, rows, stamps and receipts together.

The advisory/table locks serialize cooperating migration callers and receipt
writers. They are not a universal lock against privileged changes to every
PostgreSQL catalog. Unknown state at validation is refused; later drift cannot
be blessed because receipt values are fixed approved hashes. Ordinary startup
probes and continuous operational checks remain necessary. The database owner,
server binaries and owning release system remain trusted authorities.

## Contract and invocation

The JSON has exactly these fields (placeholders below are deliberately invalid):

```json
{
  "version": "c0-boundary-transition-241-247-v1",
  "database": "qualified_target_name",
  "server_version_num": 160013,
  "predecessor_ledger_sha256": "REVIEWED_TARGET_LEDGER_HASH",
  "before": {
    "verdify_api_runtime_login": "REVIEWED_TARGET_PREDECESSOR_HASH",
    "verdify_ingestor_runtime_login": "REVIEWED_TARGET_PREDECESSOR_HASH"
  },
  "after": {
    "verdify_api_runtime_login": "REVIEWED_TARGET_SUCCESSOR_HASH",
    "verdify_ingestor_runtime_login": "REVIEWED_TARGET_SUCCESSOR_HASH"
  }
}
```

```sh
python scripts/c0-boundary-transition.py \
  --contract /qualified-artifacts/c0-contract.json \
  --contract-sha256 REVIEWED_HASH_PIN \
  --output /qualified-artifacts/c0-transition.psql
```

The emitter refuses to overwrite an existing output, reports the emitted SQL
hash, and discloses no rejected input values. The output is mutating SQL, not a
read-only report. Its bytes, tool revision, contract/pin and execution target must
be bound in the future owning migration-job integration. Never supply production
credentials to a fixture or put credentials in a contract/artifact.

## Validation and remaining delivery

Run the new synthetic qualification with the earlier C0 tests:

```sh
SCORECARD_TEST_PG_BIN=/path/to/private-postgres16/bin python -m pytest -q \
  tests/test_c0_boundary_transition.py tests/test_inline_climate_capture.py \
  tests/test_ordinary_boundary_diff.py tests/test_c0_release_rehearsal.py \
  tests/test_apply_migrations_runner.py
```

Tests use a private socket-only cluster, source-derived C0 fixtures, actual 217
digest/attestor functions, and the actual application startup SQL. The fixture
obtains expected hashes inside a rollback rehearsal only on that synthetic
cluster. It does not replay the full 217 security normalizer or a real backup.
No production hash or qualification approval is inferred from test success.

Before releasing, still establish the real backup/restore evidence and exact
target-specific contract; review full predecessor/successor differences and
callee closure; qualify supported server/extension/role state; integrate this
transaction into the owning migrate image/job without allowing the ordinary
per-file path to commit 241–247 first; add the tests to owning generated CI;
obtain exact-SHA validation and Kaniko/Zot digest evidence; then verify Argo
Synced + Healthy and both ordinary application sessions/consumer adoption.

Any failure before commit rolls back the full C0 transaction, including receipt
updates. PostgreSQL sequence allocations are not transactional; failed attempts
may leave gaps and must not be described as byte-identical sequence state.
After commit, a rollback is a separately reviewed forward boundary/data change,
not merely an old image pin or stale receipt restoration. Preserve the backup,
previous pins, contract, SQL hash and failed qualification evidence.

This is no physical proof, scientific endpoint approval, experiment lock/draw,
launch, incident disposition or completed pilot evidence.
