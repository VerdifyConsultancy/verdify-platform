# C0 combined migration release qualification

**Release hold: ordinary-login attestation transition is not qualified.** The
new actual-function probe shows each migration 241–246, and the complete bundle,
invalidates both stored API/ingestor startup attestations. Existing SQL/consumer
passes below are not evidence that those services can restart after migration.
Do not deploy this bundle until an explicitly validated boundary transition
preserves the fail-closed startup guard. Do not disable that guard, blindly
replace stored hashes, or rerun immutable migration 217 to bless changed state.
The old digest also omits the private climate-capture payload helper introduced
by 244. Forward migration 247 now removes that dependency by inlining the payload
in the attested trigger; see [the repair and validation](inline-climate-capture.md).
That fixes the private-callee gap in source, not the startup-receipt transition.

The measurement, forecast, band-lineage and incident PRs are a stacked release.
Passing each SQL fixture separately does not prove their shared tables, roles,
triggers and readers coexist. `tests/test_c0_release_rehearsal.py` exercises the
combined migrations 241–246 through `db/apply-migrations.sh`, using one newly
created private PostgreSQL database per scenario.
`tests/test_inline_climate_capture.py` additionally applies the complete prefix
through 247 via that same runner and qualifies the repaired capture path. The
earlier counterexamples deliberately retain the unpatched 241–246 prefix.

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

## Newly reproduced startup-attestation incompatibility

Migration 217 seals the API/ingestor security-active catalog in
`runtime_ordinary_login_attestation_receipts`. Its actual
`fn_runtime_ordinary_boundary_digest(text)` includes granted view definitions,
function definitions, relevant columns/ACLs and the protected write interfaces;
its actual `fn_runtime_attest_ordinary_login()` compares that current digest with
the stored one. A legitimate source change still changes that digest. Startup
does not infer authorization from a successful schema-migration ledger stamp.

Seven additional private-database cases install those two **unchanged source
functions**, then exercise the exact startup SQL extracted from both API and
ingestor source using both exact session identities:

1. Six cases capture a synthetic attestation at each predecessor prefix, prove
   both startup checks true, apply the next actual migration through the normal
   runner, and prove both checks false while receipt rows remain unchanged.
2. One case applies real migration 241 inside an outer transaction: the attestor
   returns false inside it; rollback restores true for both identities. Applying
   all six through the runner then leaves both false despite correct ledger
   hashes and successful SQL completion.

Direct digest calls must succeed and differ from the stored hashes. This rules
out the attestor's catch-all returning false because an object is missing or a
fixture stub raised an error. These tests intentionally **pass when the release
incompatibility is reproduced**; they are counterexample evidence, not repair
acceptance or permission to ship.

This remains a synthetic catalog test: missing protected relations are inert
placeholders; missing named function signatures have throwing, never-executed
bodies. The actual digest/attestor implementations are not rewritten or mocked.
The initial receipt is captured only in that isolated synthetic fixture. This
does not execute all migration-217 security normalization/assertions, prove
production role completeness, or authorize receipt recapture in any real DB.
The private server must supply `pgcrypto`; otherwise these seven tests skip
explicitly. PostgreSQL 16 coverage is required until the PostgreSQL 15 test
installation also supplies that extension.

The production deployment manifests were read with a strict nonsecret allowlist
on September 5: `VERDIFY_API_RUNTIME_DB_ROLE_REQUIRED=1` and
`VERDIFY_INGESTOR_RUNTIME_DB_ROLE_REQUIRED=1`. No production DB was queried and
no deployment/device state was changed. This identifies a relevant release
hazard, not a claim that the currently running services have failed or that it
caused remote CI failures.

The repair must prove a trusted predecessor boundary, validate the exact
authorized schema/privilege transition, preserve denial of unrelated drift,
and establish matching startup receipts transactionally at the appropriate
release boundary. Test hostile preexisting drift, injected migration failures,
receipt integrity, rollback/resume, exact ordinary-login startup and the actual
restored production schema. A generic "recompute the hash after migrations"
step would erase the security property and is not an acceptable repair.

### Read-only explanation of the exact digest changes

`scripts/ordinary-boundary-diff.py` supplies the catalog comparison needed to
review that transition. It **does not implement the receipt transition** and
does not remove the release hold. It has no DB client or permission-changing
operation. `emit-sql` emits one bounded repeatable-read, read-only transaction
for either exact ordinary login; only an already-authorized operator should run
it against an isolated restored target. No extra access is implied.

```sh
python scripts/ordinary-boundary-diff.py emit-sql \
  --login verdify_api_runtime_login
python scripts/ordinary-boundary-diff.py compare \
  --before /authorized/evidence/before.json \
  --after /authorized/evidence/after.json \
  --output /authorized/evidence/new-comparison.json
SCORECARD_TEST_PG_BIN=/path/to/private-postgres16/bin python -m pytest -q \
  tests/test_ordinary_boundary_diff.py tests/test_c0_release_rehearsal.py \
  tests/test_apply_migrations_runner.py
```

The emitter pins the complete immutable migration-217 source SHA256 and projects
its actual `security_entries` expression, not a newly invented subset. It checks
the installed digest function's body hash, owner, language, definer mode and
search path, and requires its independently recomputed full digest to equal the
installed function's result. PostgreSQL deparses object names according to the
search path; the emitted query deliberately uses the digest function's exact
`pg_catalog, pg_temp` path, not the application's startup search path.

Outputs contain catalog categories, object identities and per-entry hashes.
They do not expose function/view definitions, role-setting values or raw catalog
preimages. All 13 source categories, including internal/trigger functions, are
retained. Multiple entries for one object are compared as hash multisets, so
duplicate/removed entries cannot disappear through dictionary overwrites.
Unknown categories, unverified installed source, projection disagreement,
incompatible login/database-name/server-version pairs and malformed hashes are
refused. The comparison uses exclusive output creation and binds both input
file hashes and tool hash. These are byte bindings, not signatures or proof of
the same physical database; authorization flags are always false.

Actual PostgreSQL tests compare both logins before and after each source
migration. A separate counterexample grants SELECT WITH GRANT OPTION on
`v_planner_performance` after its legitimate source change: the set of changed
object names is **identical**, but the per-entry hashes reveal the additional
privilege expansion. Therefore merely allowing the named objects touched by a
migration would silently accept unauthorized ACL changes. The eventual repair
must qualify the specific definition/ownership/ACL/column/constraint/trigger
transition, not just that object list. Another case replaces the installed
digest function and proves the comparator refuses it rather than treating its
replacement result as evidence.

A further actual-function test applies 241–244, captures synthetic matching
receipts, then changes only the private
`fn_daily_climate_metric_payload(public.daily_summary)` body. Both full digest
values and both startup checks remain unchanged. An ordinary ingestor update
then changes a climate metric without creating the required capture revisions:
the altered helper makes OLD and NEW payloads equal. This is not a defect in
the comparator—it exactly reproduces the installed digest's **incomplete new
callee coverage**. Migration 217's fixed internal-function list predates this
helper. The repair must extend the attested reachable-function contract as well
as validate the authorized predecessor/successor transition; an unchanged old
digest is not adequate acceptance for the new capture path. The mutation and
suppressed capture occurred only in the private synthetic fixture.

Optional `C0_BOUNDARY_REPORT_DIR` in the tests writes exclusive synthetic
before/after projections and deltas to an existing local evidence directory.
Those artifacts are explicitly marked synthetic and cannot be used as approved
production fingerprints or permission to refresh a real receipt.

Physical Gate P, protocol lock/draw and randomized launch remain separately
authorized transitions. Neither this rehearsal nor deployment clears the incident
hold or supplies the missing scientific measurement and prospective design evidence.
