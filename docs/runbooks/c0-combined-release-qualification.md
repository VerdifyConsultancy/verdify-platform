# C0 combined migration release qualification

The measurement, forecast, band-lineage and incident PRs are a stacked release.
Passing each SQL fixture separately does not prove their shared tables, roles,
triggers and readers coexist. `tests/test_c0_release_rehearsal.py` exercises the
combined migrations 241–246 through `db/apply-migrations.sh`, using one newly
created private PostgreSQL database per scenario.

## Reproduce the private rehearsal

Use the repository Python development dependencies and a matching local PostgreSQL
server/client binary directory. Run as a non-root user, with temporary storage
under the platform scratch directory. Do not supply a production connection:

```sh
SCORECARD_TEST_PG_BIN=/path/to/private-postgres/bin uv run pytest -q \
  tests/test_c0_release_rehearsal.py tests/test_apply_migrations_runner.py \
  --junitxml=/path/to/scratch/c0-release.xml
```

Run once with PostgreSQL 15 and again with 16. Without the explicit binary
directory the database tests skip; that is not a qualification pass. The fixture
starts its own Unix-socket cluster with TCP disabled, rejects host authentication,
uses a synthetic local owner, and removes only that generated cluster afterward.
The runner environment explicitly binds that socket and ignores ambient database
settings. No real credentials, production SQL execution, device calls, image
publication or Argo mutation are involved.

## What the test establishes

- Actual runner `--plan` makes no observed schema/data/ledger changes.
- All six exact source migrations apply in filename order and receive their
  exact SHA-256, sequence, duration and applying-role ledger fields.
- Scorecard binary/graded separation, outdoor forecast truth, public band
  reconstruction and the observed-minute writer/capture/typed reader coexist.
  API reads use its duty role; ingestor writes use its duty role. Unauthorized
  raw API reads and runtime capture-ledger mutations are rejected by PostgreSQL.
- The real observed-minute helper can commit and can roll back inside the
  reconciliation-style outer repeatable-read transaction without leaving
  diagnostic or capture rows. Scientific/physical eligibility remains false.
- Re-running the ledgered release makes no observed changes. Changing an already
  applied scratch migration is rejected, without silently updating its stamp.
- A populated database with an empty ledger refuses apply. It does not acquire
  a fictitious historical baseline merely because the test wants to proceed.
- An injected end-of-file failure in **each** migration rolls back its DDL/data
  and leaves no stamp, while retaining already committed predecessors. Later
  pending migrations do not run. Restoring the exact scratch source then permits
  normal resume. Catalog identities, owners, ACLs, function/view definitions,
  columns, constraints, triggers, materialized/source rows and ledger rows are
  compared around the failure. PostgreSQL sequence allocation is deliberately
  not described as transactional; rollback can leave sequence gaps.

This is per-file transaction protection, **not** an all-six-migration atomic
release or a production downgrade procedure. Previously committed files remain
installed after a later file fails. The failure injection changes only disposable
copies, never the repository migration files or already applied production SQL.

The successful-apply path exposed a shell portability defect missed by the older
stub-based plan tests: `10#241` is not POSIX shell arithmetic and fails under dash.
The runner now strips leading zeros before decimal conversion. Tests cover zero,
008, duplicate-number-shaped 070, suffixed 095a, 241 and nonnumbered filenames
under `sh` and Bash. This is not evidence that the Alpine production image had
the same failure, or an explanation of unobserved remote CI failures.

## Deliberate limits

The baseline composes the existing synthetic scorecard, forecast and band fixtures.
Legacy view/function SQL comes from the repository, but resource-view stand-ins,
`date_bin` in place of Timescale `time_bucket`, synthetic crop-anchor values and
a legacy band-function dependency sentinel remain explicit substitutions. Shared
tables and ordinary duty grants are fixture setup, not a restored production
schema or complete role inventory. An explicitly named synthetic ledger record
is hashed from that setup; the real historical baseline backfill is never run.

This rehearsal does **not** establish production data validity, Timescale extension
or compressed-chunk compatibility, realistic volume/locking/overhead, backup
restorability, login-role inheritance, the complete selector/setter/export path,
fresh device state, incident resolution, fixed-panel crop outcomes or experiment
readiness. These remain acceptance requirements, not waived checks.

The generated `.agent-fleet/ci.yaml` does not currently select this new database
test or provision its private server binaries. Local XML receipts must accompany
review; remote CI green alone is not evidence that this rehearsal ran. Change CI
selection through its owning registry contract, not by hand-editing generated YAML.

## Delivery and rollback gates

1. Obtain exact-revision CI results and published image digests through the
   authorized delivery interface; a submission receipt or pending status is not
   a successful build. Include the migration runner repair in the migrate image.
2. Qualify a current backup by restoring to an authorized isolated target; run the
   combined contracts with the real schema, roles, extensions and bounded data.
   Retain source, migration-ledger, input and result identities and rollback data.
3. Follow [scorecard consumer-first ordering](scorecard-contract-2.md). A combined
   migrate image carries 241 as well as 243: do not install the whole image early
   merely to satisfy the band reader. Compatible consumers can return explicitly
   unavailable diagnostics until the corresponding migration is present.
4. Deliver declarative source and digest pins through the normal ledgered/GitOps
   path. Require Argo Synced + Healthy and exact runtime/configuration adoption,
   then live scorecard, band, forecast and diagnostic/revision readbacks. Healthy
   old pods are not acceptance of the new release.
5. Retain pre-release image/configuration identities and data exports. Use reviewed
   forward corrections for already committed migrations; do not delete append-only
   evidence, edit applied SQL in place or assume a failed later file undid earlier
   ones. Runtime rollback must respect the changed scorecard semantics.

Physical Gate P, protocol lock/draw and randomized launch remain separately
authorized transitions. Neither this rehearsal nor deployment clears the incident
hold or supplies the missing scientific measurement and prospective design evidence.
