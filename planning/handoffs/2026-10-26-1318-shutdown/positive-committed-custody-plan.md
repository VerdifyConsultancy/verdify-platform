# Positive exposure qualification: committed private-cluster custody

Source reviewed: origin/main ccc4274cb790308685110a154831480f700e95d7. Read-only assessment; no test, CI, cluster or production operation performed.

## Decision

Use actual existing lifecycle and component-executor LOGIN connections, with native autocommit/explicit committed transitions, inside one independently generated private PostgreSQL cluster per new case. Do not SET ROLE to simulate cross-session identity and do not delete immutable rows to simulate rollback. The original nineteen cases retain their original unchanged-row/rollback custody contract. Add a distinct committed_private_cluster_disposed custody mode for the three positive cases.

The existing isolated_pg generator in tests/test_scorecard_semantics.py creates /tmp/scorecard-pg-* using mkdtemp, initdb --auth-local=trust --auth-host=reject, listen_addresses='', then on teardown runs pg_ctl -m fast -w stop and removes exactly its generated directory. Its wrapper in tests/test_component_containment_postgres.py drops only initdb/pg_ctl to existing nobody UID/GID when native CI runs as root. No shared DB, Kubernetes credential or new role grant is needed. This proves actual private SQL LOGIN and granted-function paths, not password/TLS or production runtime authentication.

## Minimal implementation surface

Only tests/test_component_containment_postgres.py and scripts/qualify-component-containment.py. Add a positive-only wrapper around the existing isolated_pg generator, capturing the exact generated cluster path and postmaster identity from start/stop subprocess arguments. Keep the existing socket_pg/containment behavior for nineteen cases. Reuse/extract only source setup shared with those cases; do not edit migrations or tests/test_scorecard_semantics.py.

Create the ordinary synthetic configured experiment and exact current ops/alert facades before taking the starting custody snapshot. Register the baseline through the real lifecycle LOGIN. The private setup owner alone calls the existing ungranted deterministic *_at shadow helpers, using unavailable context, baseline fallback and the locked explicit-null preview shape already in native test214:733-834. Commit that clearly labeled private chronology. It is not physiological evidence or admitted wall-clock runtime.

Then use actual lifecycle LOGIN for shadow->commissioning and closed->baseline_recovery. Use actual executor LOGIN for runtime registration, native bounded recovery work request, claim, bundle, component status journal, two complete synthetic epochs >=30s apart in real elapsed time, confirmation, opening and exact retry, recovered terminal event, and closure. Read current_user and session_user on each actual connection and record equality to the expected login. No direct control-row update, approval fabrication, clock alteration, role/grant change, trigger disable or function replacement is needed.

Use a separate existing private-owner connection only for receipt readback and the existing capture-only alert facade/producer, as the current tests already do. Never instantiate/read the relay-authorization bridge for these cases. Finish by closing all exposures through the executor function and closing admission through the lifecycle function; record no open exposure and no selected current work before disposal. Component capability remains true after native admission closure, so do not claim a terminal/disabled lifecycle or restore-before state. Disposal, not row rollback, destroys this synthetic state.

## Three cases and scope

1. multiple_open_exposures: two different synthetic device/runtime IDs and two different native recovery works, with distinct live claim worker refs; one shared >=30s observation wait; both open simultaneously without defeating the per-device uniqueness guard.
2. open_exposure_work_expired: one native open baseline exposure, then wait until its actual approximately60s expiry. Work is already natively recovered, so ordinary nonterminal-expiry does not mask or persist after closure.
3. open_exposure_observation_stale: one native open/recovered baseline exposure, work valid >=5min, then allow latest persisted evidence to age >90s without changing timestamps.

For each: exact native ops reason/severity, real producer fire once, repeat dedupe, native close/admission-close, one captured resolution, final silence. At least one case also checks 47-field epoch refusal, single-epoch open refusal and two-epoch/unconfirmed-component refusal, with exact no-effect custody around each refusal. These are synthetic SQL confirmations only, never authentic48 current-grid or physical acceptance.

Expected coverage:22 mandatory cases,13/16 native reasons,16/16 typed envelopes. Remaining three defensive labels stay unexercised: runtime_generation_missing and open_exposure_observation_missing have the existing exact guard-refusal tests; open_exposure_state_mismatch has native close-before-mismatch coverage. Do not fabricate an inconsistent immutable open row solely for16/16.

## Expected committed effects

Starting snapshot is after installed setup/facades and before baseline registration. With D devices, expected inserts are state_artifacts1; shadow_cycles1; shadow_contexts1; shadow_choices1; shadow_outcome_previews1; work1+D; work_events1+2D; runtime_generationsD; delivery_bundlesD; delivery_bundle_completionsD; component_outcomes4D; observation_epochs2D; observation_receipts2D; exposuresD; exposure_closuresD; experiment_events3+D; alert_log1. Existing control_experiments has only native phase/admission/capability/lease/updated_at changes. Alert_log has only the real producer's expected in-place delivery/resolution fields. Every other preexisting row and unlisted rowset must remain exact, including zero approvals, assignments, facility closures, runtime faults, outbox/external sends, bundle attempts and model outcomes.

Expected sequence deltas from those calls are experiment_events_event_id_seq3+D; experiment_v2_work_events_work_event_id_seq1+2D; experiment_v2_runtime_generations_generation_event_id_seqD; experiment_v2_component_outcomes_component_outcome_id_seq4D; alert_log_id_seq1; every other sequence0. Multiple device case:5,5,2,8,1. Single device cases:4,3,1,4,1. Keep expected constants source-derived; any actual extra effect is a failure to investigate, not a manifest update learned automatically from the run.

Record all returned IDs and hashes, exact initial/final catalog/function/ACL/role/membership/trigger hashes, row counts and expected append-only row hashes, readback of closure reason/receipt identity/time/revision/generation tuple, sequence deltas, and capture-only producer messages. Catalog comparison excludes only explicitly separately checked sequence values; unchanged trigger enabled state and session_replication_role=origin are required. Append-only rows must retain their exact first committed payload on final readback. Qualifier rejects any undeclared row modification or insert.

## Disposal receipt

In the positive-only fixture finalizer, close every asyncpg connection, call the owned isolated_pg generator teardown, and record only after it returns successfully: exact pg_ctl stop command target and returncode0, original postmaster PID/start identity no longer running, socket path absent and exact generated cluster directory absent. The path must be the same mkdtemp-owned path captured at start; never infer/discover a path to delete. Emit the teardown receipt as a per-case JUnit property or a per-case JSON in the qualifier's temporary result directory, outside the removed cluster. No additional SQL deletes, TRUNCATE, sequence reset or persistent target.

The qualifier must require all three positive cases to have committed effect equality AND complete disposal evidence. Missing teardown, timeout, failed stop, failed directory removal or killed pytest fails qualification; no rollback/restoration claim may be made from an absent receipt. Native pytest fixture finalization handles ordinary assertion failures. Preserve failure diagnostics; do not remove an unverified live process directory merely to make the directory test pass.

## Bounds

Existing admitted native CI only, serial cases, one private PG cluster at a time, no network listening, max existing owner + lifecycle + executor connections (the second device may reuse the executor LOGIN). Per SQL command3s and connect/close5s class bounds; pg_ctl/initdb retain existing60s bound. Three actual waits total about213s, plus existing19 cases/setup. Keep existing600s private-phase timeout; do not lengthen CI preemptively. No local intensive run, build, deployment, firmware/device connection, node scheduling change or production mutation.
