# #643: two remaining ordinary maintenance duties

Source proposal from main `0b03466a8f30a0494482c983853128f98cdc9e0a`. Migration276 is reserved solely for this patch after checking main's highest migration275, all six open PR file lists and the coordinator's concurrent-owner ledger. Recheck before landing. This source branch has no PR, native CI, production SQL, persistent role/credential installation or deployment. The admitted isolated projected owner-held qualification is complete at its scope below. The previous three- and nine-process isolated credential windows are complete and supply no new authority.

## Problem and smallest change

The desired `verdify-writer-watchdog` and `verdify-band-curve-refresh` CronJobs still receive the schema-owner password. The other nine selected duties already have bounded source contracts. Database server, backup, migrate and credential-bootstrap owner authority is legitimate and unchanged here.

Existing `public.fn_runtime_refresh_materialized_views()` is not an authority substitute: it refreshes relay/climate and band-curve views, omits `mv_equipment_runtime_daily` and `mv_daily_kpi`, and belongs to the broader ingestor duty. No existing narrow watchdog role covers its own two alert lifecycles. Sharing those existing LOGINs would add cross-workload authority and break exact sole-membership contracts.

Migration276 adds two private owner-held schemas, each with one nonadmin LOGIN/INHERIT role and one NOLOGIN/NOINHERIT duty. The roles own no application objects. They receive only schema USAGE and their fixed functions; no raw table/column/sequence grants, public CREATE, other-workload membership, general SQL or relation-name parameter. Every function revokes PUBLIC EXECUTE. The source guard preserves all three existing ordinary seals and never updates a receipt or changes an applied function/migration.

| Ordinary duty | Exact allowed operation | New reference (key `password`) |
|---|---|---|
| `verdify_writer_watchdog_runtime_login` |Its fixed sweep receives validated lease state/age, measures climate age itself, and creates/resolves only `writer_absent`/`telemetry_stall` |`verdify-writer-watchdog-runtime-db` |
| `verdify_matview_refresh_runtime_login` |Three zero-argument functions refresh exactly `mv_band_curve`, `mv_equipment_runtime_daily`, `mv_daily_kpi` |`verdify-matview-refresh-runtime-db` |

The watchdog keeps thresholds180s/600s, critical/system/source attribution, existing unresolved-type deduplication, missing-climate behavior, lease-read-failed skip, schedule, Kubernetes Event/lease init container and resources. Resolution is deliberately narrower: it may resolve only rows with `source='writer_watchdog'`; unrelated actors' same-type alerts stay untouched. The database sweep is one transaction, so a later error no longer leaves an earlier half-sweep committed. No external actor/time/threshold/alert-type/SQL argument is accepted. The caller rejects malformed numeric lease text before SQL and uses psql's quoted stdin variables; it never interpolates lease content into a `-c` SQL string.

Each materialized-view call retains its own transaction, initial nonconcurrent population and subsequent concurrent refresh. Targets must be materialized views owned by `verdify`; PostgreSQL's populated/unique-index requirements remain enforced. The old three statements' cadence, resources and target set are preserved. The fixed functions follow the already-applied owner's refresh pattern. All three were exercised successfully on the admitted PostgreSQL16 clone inside a rollback-only qualification; this is separate from production execution and authenticated caller acceptance.

## Initial-only credential bootstrap

The new wave4 hook reuses the existing private psql transport; it has no service-account token and receives only the two proposed keys plus the existing owner credential. Before any setter it requires exact276 ledger/hash, every qualified275 predecessor predicate/seal, exact two role memberships/flags/ownership/effective raw-table and column denials, and six exact function body hashes/owner/definer/search-path/ACL contracts.

It authenticates any non-NULL existing verifier against its matching supplied custody before installing anything; mismatch refuses and grants no rotation. For NULL roles, one native transaction locks the role catalogue, rechecks both states and all contracts, disables verifier-bearing query/utility/parameter retention and refuses overriding audit retention, then uses non-echoing `\password` with values only in private stdin/environment. It has30s statement/5s lock timeout. Failure/transport ambiguity stops without replay. Ending actual login attesters and contracts must pass. No new Secret data, verifier or value has been obtained/generated/provisioned by this source task.

The six-role bootstrap otherwise rejects max schema276. Its source therefore adds only an explicit276 profile: the qualified275 predicates and unchanged three expected digests, correlated with this exact new migration hash. Prior profile functions and the old native receipt remain intact. The original275 fixture remains byte-identical. Its historical whole expression is reconstructed from unchanged predecessor profiles; a separate genuine276 projected-native receipt now binds the entire current helper, maintenance helper, adapter and raw/projected SQL. The two changed binding tests pass. This does not turn the owner-held projection into raw production276 execution or password/TCP/pool proof.

## What is checked and what remains

Offline tests exercise missing/unknown contracts, malformed/missing state, initial-only/mixed/mismatched custody, duplicate/unsafe inputs, transport stop without replay, private value transport, fixed grant/function closure and real shell rejection of hostile lease content. JSON/YAML/render and Ruff/source checks are separate. The isolated owner-held native SQL/role/function qualification has run and rolled back; no password/TCP/pool or production authentication test has run for these new roles.

The [source and native case map](../../planning/execution/2026-10-13/maintenance-role-643/qualification-case-map.json) and [genuine native receipt](../../tests/fixtures/six-runtime-bootstrap-276-projected-receipt.json) retain exact source/native checks and limits. Use an explicitly admitted source qualification surface with identity/starting-catalog/sequence custody and exact inverse. Do not start a Job, invent a second production-like target, create a credential or reuse an expired credential approval because that file exists. The older107-duty and nine-route receipts stay valid at their original scope; they do not authenticate these two new duties.

## Reviewable delivery and inverse

1. Join Retry's current bounded production role/reference/session/RBAC receipt. Reuse existing nine-role adoption and current health/history; no redundant nine-role restart or provisioning.
2. Join the completed projected engine/function/source-guard qualification and offline initial-only-bootstrap checks; qualify the final source through normal enforced CI. Preserve old receipts, failures and old source. Production raw276/bootstrap authentication remains attended delivery evidence. Recheck main/open migrations and exact schema source before landing. Only then request any specific missing central-KSOPS credential action for these **two new production identities**, not an existing rotation or a borrowed temporary clone window.
3. Coordinate the release owner and actual CI capacity. Existing registered classifier/build/pin/full no-prune Argo machinery owns eventual delivery; no extra workflow or source gate is invented. The new bootstrap is migrate-backed, so its reviewed image must include the exact migration and scripts. Do not assume which other COPY-dependent images the final cohort selects. No build/PR/CI/main event is submitted here.
4. Record and suspend the two CronJobs, account for their owned active Jobs, then use the attended hook-running full sync. New keys must already be in canonical KSOPS custody; migration installs NULL LOGINs, old six-role wave3 accepts exact276, then wave4 installs only missing verifiers before the ordinary templates adopt them. Missing/mismatched custody must fail before caller authority is released. Existing ingestor source/spec remains unchanged by this patch.
5. Release only the recorded two schedules after actual identity/allow-deny/owned-alert/three-refresh results, zero owner references and current public/planner/ingestor/study-integrity readback. Verify actual sessions/functions and selected resource identities. Bootstrap success alone does not prove every caller duty or rollback behavior.
6. If failure precedes template adoption, preserve exact hook/status and credential state; do not blindly resubmit or reset a potentially committed verifier. If a caller fails, suspend it and finish its exact owned Job before restoring source/schedule. Applied276 remains additive; do not edit it or alter the old nine roles. Prefer a narrow forward fix. Restoring the old owner-password template is an explicit temporary authority regression, not accepted least-privilege rollback, and needs the owning attended scope. New key deletion/NULL inversion is safe only after all matching callers are fenced and exact initial state/custody is rebound; it is not an automatic source-revert action.

The [two-credential/release handoff](../../planning/execution/2026-10-13/maintenance-role-643/release-handoff.md) defines the remaining initial-only production decision and rollout. #643 remains OPEN pending actual production adoption. Production cutover, physical experiment, device/firmware operation, Timescale policy change and old database/PVC/custody deletion remain outside this patch.

## Actual projected-native result and physical observation

Three attempts are retained: the original production275 guard correctly refused
the separately admitted CNPG fork; the next projected attempt stopped at a
Timescale compressed-column fault fixture; the corrected third transaction passed
all9 fault families,27 permission/invalid-input refusals, both column-grant duty
cases, watchdog owned lifecycle/foreign preservation and three fixed refreshes.
It completed ROLLBACK. Only the alert-ID allocator advanced3; its observed-state
conditioned inverse ran once, then all77 anchors and NULL-role metadata matched.
No credentials, new target/workloads or production actions were used.

The original catalog4355a2c8… and actual post catalog92cb24ed… are both retained.
They differ in exactly4 physical maintenance fields on mv_band_curve and its
index; actual autovacuum occurred18seconds after rollback. All other canonical
bytes/definitions/ACLs/roles/ledger/seals/identities match. The reviewed delta is
explicit in the receipt: physical byte-identical reseal is false, no pg_class
reset/blanket ignore/seal rewrite occurred, and no before/after MV row hash exists
(the retained952 table seal excludes materialized views). These field semantics
are documented in [PostgreSQL16 pg_class](https://www.postgresql.org/docs/16/catalog-pg-class.html).
