# Replacement containment target for #587 and #639

Status: proposal requested by Jason on 2026-10-25; no target provisioned.

## Recommendation

Use a disposable, private PostgreSQL fixture in the existing admitted in-cluster
Verdify CI lane for source-level lifecycle and executor cases. Do not replace the
retired `verdify-db-rehearsal` service with another persistent clone. A separate
runtime/authentication qualification target is justified only when a remaining
clause cannot be proved in that fixture and its source, identity, data and egress
contract have been reviewed concretely.

This makes bounded progress possible without resurrecting #992's retired
namespace or eight retained volumes. It does not unblock authentic historical
controller replay, actual deployed-service LOGIN acceptance or physical recovery.

## Existing path and limits

The agents-owned `verdify-campaign-ci` helper runs PostgreSQL 16 with pgcrypto
inside the existing CI workload, sanitizes ambient database environment variables,
and uses fixture-local trust-auth UNIX sockets with TCP disabled. It records the
exact Git revision, selected tests, failures/errors/skips and qualification result.
The current selection is a fixed list of C0, migration, panel, ledger, scorecard,
forecast and band-lineage tests. It does **not** select #587/#639's residual cases.

Source reference: jvallery/agents at
`26c275536` (`control-plane/agent-fleet-control/runtime/agent-dev/verdify_campaign_ci.py`).
Product reference: `624856f3524195c09fa884f4b8bda08801a23e7d`.
The C0 fixture explicitly uses synthetic resource-view stand-ins, date_bin rather
than Timescale, and a frozen legacy migration runner. Reusing its execution
mechanism does not make that synthetic schema a complete current production DB.

## Proposed first implementation

1. In verdify-platform, build a dedicated fixture from the current relevant
   schema/migrations and actual lifecycle/executor source. Declare every stand-in
   explicitly; fail when a required relation/function/role is absent. Never stamp
   the real production baseline onto a synthetic DB.
2. Add residual cases only: feature-off and emergency nonactuation; locked/armed
   no-reopen; populated blinded reads; executor writer exclusion and contained
   lease-loss/reconnect recovery where authentic fixture preconditions exist.
   Keep accepted six denials, two admission transitions, begin-bundle invocation,
   56 native positives/eight causal negatives and delivered alert pair as prior
   evidence, not new acceptance.
3. Use fixture-only role identities and socket authentication. Instrument the
   device/message boundary with a call recorder that fails on any attempted
   transport creation. Zero calls is an assertion, not successful physical output.
   This cannot be credited as actual production LOGIN authentication.
4. In agents, extend the helper's fixed selection for these exact tests through
   the registry-owned CI source. Do not hand-edit generated `.agent-fleet/ci.yaml`,
   bypass admission, alter fleet quota, add experiment RBAC/credentials or submit
   an ad hoc privileged Job. If existing admission cannot host the fixture,
   report that blocker before any execution.
5. Run serially in the existing CI namespace/profile with its existing CPU/memory
   limits. Proposed additional test runtime cap: 10 minutes, no parallel database
   servers, 1 GiB fixture-data ceiling. Confirm actual requests/limits, current
   allocatable resources, Pod working sets, pressure and quota admission before
   accepting these bounds; reject rather than run on the Mac if insufficient.
6. Retain source and fixture hashes, exact Workflow/Pod UIDs, selected-case map,
   JUnit and before/after row/catalog/ACL/sequence receipts. Preserve failed attempts.
   Required tests may not skip. The PostgreSQL data directory is disposable private
   CI storage; no PVC, production dump, retained rehearsal volume or Secret is mounted.
   Existing CI garbage collection cleans only its own disposable workload after
   retained outputs are verified. Public receipts contain no credentials or raw
   production data.

## Acceptance boundaries

| Evidence | Credit possible | Credit unavailable |
| --- | --- | --- |
| Private synthetic DB, current source, explicit stand-ins | Named contained behavioral clauses and counterexamples | Production authentication, deployed runtime, complete restore, physical recovery |
| Authentic pre-tick causal inputs with source/tick provenance | Per-field replay after propagation and recovery are demonstrated | Historical-prefix credit from defaults, post-bias outputs or synthetic stand-ins |
| Future scoped runtime target | Only named clauses its real identity/data/transport boundaries support | Automatic full-issue or pilot acceptance |

#639 remains **27/48 UNPROVEN, 21 gaps**. It independently needs a legitimate
pre-tick upstream/persistent-state producer or reconstruction, source-bound
row-wise counterfactual propagation and recovery. Copying `eq_fog`/`eq_vent`
outputs, substituting defaults, or collecting only post-bias band values will not
supply missing upstream state. Preserve the P2 refusal contract in
`planning/reviews/2026-10-18-s3b-execution/p2-prefix-inputs/README.md`.
No order/grid activation or historical accepted-risk renewal is proposed.
Physical equipment and ordinary-writer handoff remain #641's scope.

## Decision and delivery sequence

Recommended decision: implement the private-fixture phase above first, with
explicit synthetic-only credit. This proposal itself authorizes no provisioning,
credential/RBAC change, production experiment mutation, device connection or
message. Jason requested preparation of the proposal; implementation follows a
separate decision on this concrete scope. Any remaining runtime target must return
with an exact identity/data/egress and cleanup design rather than reusing the
retired clone's authority.

Deliver the fixture and agents selection changes through their owning repos'
required CI, verify terminal cluster execution and retained receipt integrity,
then update #587/#639 clause by clause. There is no production deployment in this
phase. Keep both issues open for remaining runtime/causal/physical criteria.

## Approved implementation update

Jason approved the private-fixture phase on 2026-10-25. The active Verdify
native PR gate runs `scripts/ci-local.sh` in a credential-free Python validation
container (500m CPU/768Mi requests, 2 CPU/4Gi limits, 10Gi ephemeral storage).
It predates the generic registry campaign helper and does not contain that
helper. Therefore this first implementation adds an explicit cluster-only
private-fixture step to the owning product gate and reuses the helper's signed
PostgreSQL resolver in a product harness. No fleet runtime rollout, generated
registry command change or additional admission authority is necessary. The private PostgreSQL
server drops to uid/gid65534; pytest keeps the validation container identity
to read its existing private checkout. No checkout permissions are widened.

The first fixture selects five bounded cases: actual worker feature-off before
store/transport access, locked and armed readiness reopening denials, emergency
runtime-authority readback and a populated blinded outcome/mapping denial. It
loads unmodified migrations207/214 with explicit greenhouse/climate/forecast
column stand-ins. It does not claim later physical-proof/recovery migration
coverage. Active exposure, exclusivity and lease/reconnect recovery still need
their own source-qualified fixture expansion; none is marked accepted here.

The first native attempt (`42a9baff`, Workflow `verdify-platform-pr-ci-2r9rj`)
resolved PostgreSQL16.15 successfully but failed before pytest because the
root-private checkout was unreadable after dropping the whole test process to
nobody. That attempt is failed and retained. The correction drops only the
private server, retaining the original checkout access boundary.

The corrected server-only attempt (`febfa422`) reached the real private DB,
then all five cases failed closed during setup because migration207's
`update_updated_at_column` prerequisite was missing. The fixture now loads that
exact function from `db/schema.sql`, without a stand-in implementation, and
binds the schema/source-table fixture hashes in its report. The private step
runs immediately after lint/format so missing prerequisites fail early rather
than waiting through the unrelated full test suite. The failed attempt remains
failed; no behavioral case was accepted from it.

At `35c05826`, four SQL cases passed; feature-off failed on an isolated-worker
import-path omission (`shared`), before executing the worker. The harness now
sets the explicit repository/ingestor import paths used by the existing worker
tests. This failed suite remains failed; the next run must pass every case.
