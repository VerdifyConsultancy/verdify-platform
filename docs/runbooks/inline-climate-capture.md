# Inline climate capture — forward repair 247

Campaign #775 / qualification #783 / measurement #371. Migration
`247-inline-climate-capture-payload.sql` repairs the untracked private-callee
dependency introduced by migrations 244–245. It **does not refresh ordinary-login
startup attestations or make the C0 stack deployable**. The separate permission
transition and actual restored-schema qualification remain release gates.

## Repair

The prior trigger called private `fn_daily_climate_metric_payload(daily_summary)`.
Migration 217 does not attest that helper's body. The existing counterexample
proves that changing it can suppress capture revisions while both startup checks
remain true. Migration 247 builds the exact same v2 JSON directly in the
already-attested `fn_capture_daily_climate_metric_revision()` trigger body.
There is no new function, role, grant, runtime write interface or measurement
definition. This removes the dependency rather than expanding runtime authority.

- Migrations 217 and 241–246 remain byte-for-byte unchanged.
- The capture function is replaced in place, preserving OID, owner, ACLs,
  SECURITY DEFINER mode and search path. Existing trigger bindings survive.
- Binary/graded fields, observed-minute JSON, nulls, insert/delete and paired
  before/after updates retain their existing layout and semantics. Unrelated
  field updates and no-op writes still do not create revisions.
- Existing daily rows and capture rows are not rewritten. Capture-schema v2,
  scientific/physical eligibility and the reader contract are unchanged.
- Only the obsolete private helper is dropped, with RESTRICT rather than CASCADE.
  A successfully deployed migration removes that database function; this source
  change has not executed against production.

## Admission and preservation guards

The migration takes a SHARE ROW EXCLUSIVE lock on `daily_summary` to serialize
the capture replacement with writers. It requires the known predecessor capture
body from 244 and v2 payload body from 245, exact language/security mode/search
path, database-owner ownership, no unexpected defaults/strictness, and no
non-owner function grants. It refuses unknown or hostile state rather than
normalizing it into an apparently successful repair. Source-body SHA256 checks
use built-in PostgreSQL hashing; migration 247 itself does not require pgcrypto.

An already repaired exact body with no old helper is accepted for a safe direct
rerun; a reintroduced helper or unexpected replacement body is not silently
deleted. Other stored SQL/PLpgSQL bodies containing the helper name are refused,
since string-body callees may lack catalog dependency edges. RESTRICT separately
protects catalog-recorded dependencies such as views. This is conservative
literal-reference checking, not a proof about dynamically constructed SQL;
actual restored-schema/call-site review is still required.

No startup receipt is inserted, updated or deleted. Changing the attested
capture body leaves an old ingestor receipt stale, as it should until the
authorized boundary transition is qualified. The source hash checks protect
this bounded replacement; they are not a replacement for the complete ordinary
runtime security contract.

## Qualification

Run against private PostgreSQL 15 and 16 installations:

```sh
SCORECARD_TEST_PG_BIN=/path/to/private-postgres/bin python -m pytest -q \
  tests/test_inline_climate_capture.py
```

The fixtures start separate socket-only clusters, use synthetic identities and
source-derived C0 tables/functions, and never use production credentials. Exact
ordinary-role membership and schema-CREATE denial are explicit fixture setup,
not proof of a full restored role inventory.

The new tests exercise:

- Normal runner application through 247, exact seven migration hashes/stamps,
  unchanged historical data, function identity/ACL preservation, ledger no-op
  and exact-state direct rerun.
- Payload parity against the original helper, updates to all 16 scalar metric
  fields and observed-minute JSON, nulls, date changes, deletes and no-op logic.
- A persistent writer connection that compiled the old trigger before repair
  and correctly reloads the replacement afterward.
- Refusal of altered function bodies, missing helper, wrong owner, security
  mode, search path and runtime/PUBLIC grants, without altering the rejected state.
- Preservation of unrelated view and string-body callers; no cascade.
- Failure injected after helper removal: the outer transaction restores both
  original functions and data, creates no 247 stamp, and normal runner resume
  with the exact source succeeds. Earlier committed migrations remain committed.
- An outer writer rollback leaves both source rows and revision rows unchanged.
- Ordinary roles cannot recreate the helper or execute the private trigger
  function directly. An owner-created throwing helper at the obsolete name is
  no longer called by the repaired capture path.
- Using the actual migration-217 attestor, changing the repaired trigger body
  is detected for the ingestor. Applying 247 does not update stored receipts.

The final two actual-attestor cases require pgcrypto in the private test server;
they skip explicitly when that extension is unavailable. Those skips are not
attestation qualification. Run the full earlier C0 suite as well; its unpatched
prefix tests intentionally continue reproducing the old defects.

## Delivery and rollback

Keep the existing C0 release hold. The owning migrate image already copies the
numbered migrations; no manual production SQL or out-of-band DDL is needed.
The build now includes 247 as well as 241–246, so consumer-first ordering, the
qualified startup-boundary transition, exact CI/build digests and normal GitOps
Synced + Healthy/runtime verification must all be satisfied before deployment.

Before applying, retain a current backup and restore/call-site evidence. A failed
247 rolls back within its normal runner transaction, including the function
drop; an unexpected dependency remains intact. After a committed deployment,
do not edit/replay older applied migration files or assume an image rollback
restores a removed private function. Any reversal must be a reviewed forward
change with its own boundary qualification; retaining the inline capture is
compatible with the existing writer and v2 reader contracts.

This repair provides no device proof, incident disposition, fixed-panel climate
endpoint, commissioned resource measurement, protocol lock/draw or launch credit.
