# Owning C0 migration delivery

Campaign #775 / qualification #783 / exact-SHA delivery #644.
The candidate migrate image now includes the atomic transition emitter,
catalog-projection helper and `scripts/c0-migration-delivery.py`. Both the image
entrypoint and `db/apply-migrations.sh` detect any 241–247 source file and hand
off before schema replay, repair, ledger bootstrap or per-file migration writes.
The existing `VERDIFY_MIGRATE_LEDGER=1` job opt-in remains required at the image
entrypoint. There is no legacy-C0 bypass flag and no fallback after a refusal.

**The source integration is not production deployment.** A real restored-target
qualification, reviewed target-specific contract/pin, exact CI/build results and
GitOps/runtime adoption remain required. No production contract is supplied and
no source test is permission to execute manual production SQL or launch a study.

## Inputs and admission

The owning release must supply both of these non-secret bindings:

- `VERDIFY_C0_BOUNDARY_CONTRACT`: readable regular JSON file containing the
  [reviewed target-specific transition](c0-boundary-transition.md).
- `VERDIFY_C0_BOUNDARY_CONTRACT_SHA256`: SHA256 of those exact bytes, pinned
  independently in the reviewed PreSync job specification.

Existing DB host/name/user/password bindings and the image's migration-directory
binding remain in use. The wrapper passes DB credentials to psql through its
existing environment, never prints them, and does not add Secret access, role
grants or credential discovery. It suppresses raw DB stdout/stderr on errors;
only locally authored diagnostics and a bounded SQLSTATE reach the job log.

An artifact/pin is mandatory for apply even when all C0 files appear ledgered.
A missing, partial or invalid binding fails before contacting the database.
Do not place the pin only in `envFrom`: the hook may run before the new ordinary
ConfigMap is reconciled. The artifact must exist before PreSync execution and
be matched by a direct job pin. A source-managed immutable, content-addressed
ConfigMap may be provisioned through the owning GitOps ordering; mount its JSON
with `subPath` as a regular read-only file. Ordinary ConfigMap symlink paths are
rejected by the contract reader. A baked regular artifact is another option
only if its COPY/build-context admission and independent job pin are reviewed.
No placeholder ConfigMap, artifact or hash is installed by this change.

The complete exact seven-file cohort must be present and match the pinned image
sources. Other migration files in the selected inventory must already have
matching ledger entries (or an explicitly recorded historical baseline entry).
Missing/outstanding non-C0 work, partial C0 state, edited source or non-exact C0
stamps are refused. This is a bounded release profile, not permission to skip
unqualified predecessors or automatically approve future migration 248+. A later
boundary-changing release needs its own coherent reviewed transition.

The wrapper preserves the entrypoint's read-only Timescale-extension and existing
`climate`, `setpoint_changes`, `equipment_state` checks. Missing prerequisites
are not repaired automatically. A fresh target must first have a separately
qualified predecessor; this image must not replay an old snapshot and invent an
attestation baseline on the way to C0.

## Execution and verified retry

After admission, the wrapper emits the exact transaction in memory and reports
its SHA256 plus the contract pin. It runs psql with `-X`, `ON_ERROR_STOP`, explicit
connection arguments and bounded timeouts. No generated SQL file or writable
application directory is needed; Python bytecode writes are disabled in the
candidate image.

The transaction applies all seven immutable migration sources, stamps them and
updates both approved literal successor receipts atomically. After psql exits,
the wrapper opens new read-only connections to verify all seven exact stamps,
the predecessor ledger identity, receipt count, installed attestation functions,
ledger shape and both full successor fingerprints/receipts. This is committed
state readback, not merely success inferred from the submission or process start.

Failure before commit rolls back DDL/data/stamps/receipts. Failure or lost
readback after commit is reported as **unverified**, not as proof that nothing
committed. There is no second per-file attempt. Retrying the same qualified job
uses the transaction's exact-successor no-write branch, then verifies committed
state again. Receipt timestamps and historical data are not refreshed on retry.
Sequence allocations may leave gaps after rollback; do not claim transactional
sequence restoration or a lossless post-commit image-only rollback.

`apply-migrations.sh --plan` uses read-only core/ledger inventory queries. Both
contract bindings may be absent for that diagnostic; malformed or partial
bindings are rejected. The plan explicitly reports that live fingerprints and
execution remain unverified. It creates no baseline, receipt or SQL artifact.

## Qualification

```sh
SCORECARD_TEST_PG_BIN=/path/to/postgres16-with-timescale/bin python -m pytest -q \
  tests/test_c0_migration_delivery.py tests/test_experiment_schema_migration.py \
  tests/test_release_dockerfile_base_pins.py
```

The new owning-path fixtures preload real TimescaleDB 2.25.2 on a private,
TCP-disabled PostgreSQL 16 cluster with Timescale telemetry disabled. They install
the real extension and use native `time_bucket` before constructing source-derived
synthetic views/data. Actual 217 digest/attestor functions and the exact application
startup SQL are exercised. Expected fingerprints are sampled only in a rollback
rehearsal on those disposable fixtures. This is not a real backup restore, full
217 security normalization, production role inventory or hypertable/compressed
chunk qualification.

Tests run the current shell runner and packaged Python layout, contract/pin and
source-inventory rejection, read-only plan, missing core/extension refusal,
full SQL rollback and resume, post-commit readback loss and retry, and error-value
redaction. The entrypoint fixture relocates only its absolute executable path
so the current runner can execute on the private host; it is not a container
build or deployed hook proof. Both import/package source paths are checked.

Earlier per-file C0/capture counterexamples now explicitly use a hash-pinned
historical shell runner in `tests/fixtures/`. It is not copied into the migrate
image. Their role is preserving the original regression evidence; they do not
stand in for the new owning-path tests. The audited backfill fixture test also
explicitly requires all seven C0 files to remain absent from historical baseline
stamps. No applied migration or baseline artifact is rewritten.

## Restored-catalog inspection is a hold, not qualification

The retained `experiment-v2-restore-rehearsal` component remains **inactive** in
production. Its legacy per-file replay cannot qualify the C0 atomic transition.
For an image containing any 241–247 source, the candidate init container now
checks the fixed seven-file source closure and generates the exact read-only
catalog SQL for both ordinary logins. Python runs in the candidate migrate
image; the Timescale restore image needs only its existing shell/psql utilities.

After restoring a recent dump with owners/ACLs and refreshing materialized views,
the C0 path verifies the generated SQL checksums, emits names/hashes-only catalog
observations and their SHA256s, then deliberately exits 1 with `HOLD`. It never
reaches legacy baseline recovery, per-file apply or the old advisory fixtures.
It does not run C0 migrations, rerun 217, refresh receipts, generate an approved
contract or declare a successful qualification. A held/Failed Job must not be
converted to PASS to permit later PreSync work. Raw restore and inspection errors
are retained only in private temporary files, not published as database output.

This is a diagnostic preparation path, not an activation instruction. Before a
bounded real rehearsal, the owning release must resolve its source/image digest,
fresh backup provenance, effective isolation, evidence retention and authorized
broker access. No backup or live database access is granted by these manifests.
The candidate component is not enabled and no digest is changed by this repair.
Retain failed attempts and their sanitized artifacts before a replacement hook;
`BeforeHookCreation` replaces the previous hook, and `prune:false` does not clean
up resources removed from Git. Any eventual cleanup requires exact owned targets
and retained evidence. Reverting this inactive source has no runtime rollback
effect; it does not undo a future database transition.

Restored receipt hashes may differ because database identity, role OIDs and
cluster-level role settings are not reproduced by a database dump alone. The
legacy role preseed is not a verified production role inventory. Explain those
differences against authorized target metadata; do not transplant restored or
synthetic hashes into a production contract or treat mismatch as approval.
An isolated restored-data transition, role/SQL/setter/export/analyzer proof and
independent target-specific contract review remain required before deployment.

`tests/test_c0_restore_inspection.py` runs a real custom-format `pg_dump` and
`pg_restore` on synthetic native Timescale 2.25.2/PG16 data into a second database
in the same private cluster. It verifies retained stale receipts, both source-
verified projections, explicit hold, unchanged catalog/data/ledger/receipts and
unchanged dump bytes. It also checks changed-SQL rejection, error-value redaction
and a Secret-free component render. It does not test a production backup, a new
cluster's complete role reconstruction, compressed chunks, the full container
entrypoint or deployed Job isolation. Run it with the same explicit PG binary
binding above and add it to the owning CI contract; missing dependencies/skips
are not qualification. No full Secret-bearing production render is required
for this component-only check.

## Delivery boundary

Add this owning-path module to the CI contract in the owning fleet registry;
do not edit its generated mirror. Build the changed migrate image through
Kaniko/Zot, verify its digest and artifact/tool/source closure, and bind the
qualified contract plus pin in owning GitOps. Existing consumer-first C0 ordering
still applies. Do not activate a candidate image with a missing or unqualified
contract, and do not treat a pending CI status as a live job or success.

Final acceptance requires the actual restored-target proof, exact source and
running digests, Argo Synced + Healthy, both ordinary application sessions and
current consumer/data evidence. Retain backup, prior pins, target contract,
transaction hash and failed attempts. The incident, scientific season decision,
physical Gate P, lock/draw, randomized launch, 60-day pilot and readout remain
separate campaign requirements.
