# Confirmed-component planner experiment — rollout and rollback runbook

Scope: the fast AI-vs-Frozen-FSM experiment in ADR-0010, epic #581 and launch
issue #642.

Authoritative design:

- `docs/adr/0010-confirmed-component-experiment-fast-path.md`
- `docs/plans/planner-experiment-fast-path-2026-08-23.md`
- `research/planner-efficacy/protocols/planner-switchback-v2.template.yaml`

This runbook does not authorize a physical phase without its preceding evidence
gate. The generalized policy-vector rollout is deferred; its mode remains off.

## Mode and ownership contract

The fast-path implementation must add a separate safe-default mode, named here
as the target contract:

```text
VERDIFY_COMPONENT_EXPERIMENT_ENABLED=off|enabled # coarse kill switch; default off
VERDIFY_ACTIVE_EXPERIMENT_ID=<explicit UUID>     # default empty
VERDIFY_POLICY_VECTOR_MODE=off                   # must remain off
```

The implementation lane may choose a more precise final name only by updating
ADR-0010, the v2 protocol, tests, issue bodies and this runbook atomically. It
must preserve the coarse `off|enabled` semantics everywhere and must not reuse
`VERDIFY_POLICY_VECTOR_MODE` or accidentally enable the broken manifest/vector
workers.

`enabled` only makes the bounded executor available. Database execution phase
and admission state grant actual authority. `shadow` has closed admission;
`commissioning`, `aa_rehearsal` and `randomized` open only their exact typed
operation. Ordinary planner/MCP/forecast proposals for the experiment-owned
fields remain shadow-only. An explicit source-aware hold prevents other writers
from interleaving; do not use a global switch that also blocks the executor's
legacy component transport.

Startup/readiness and every claim hard-fail to zero experiment work if the
component capability is `enabled` while vector mode is anything other than
exact `off`.

Use one `kind=randomized`, `protocol_version=2` experiment ID for `shadow`,
`commissioning`, `aa_rehearsal` and `randomized`. Lifecycle status, execution
phase and admission state are orthogonal and audited. Every assignment, bundle,
receipt, exposure and outcome carries the phase; non-randomized evidence cannot
enter ITT. The additive v2 transition contract replaces separate migration-213
qualification/A/A result prerequisites only for v2. Do not edit ConfigMaps or
restart services for phase changes.

Pre-draw shadow/commissioning/A-A remain lifecycle `draft`. Additive v2 preview
and typed readiness-operation functions serve those phases: shadow creates no
device work; canary/A-A operations are immutable, phase-tagged, and live outside
the randomized assignment/ITT tables. Do not reuse or relax the existing
`fn_freeze_experiment_context`/`fn_create_assignment` armed-or-running gates.
The executor uses a separate least-information readiness-target resolver until
randomized phase, then the randomized-only current-assignment resolver. A third
resolver handles linked immutable baseline-recovery work in every physical
phase and can return only the locked baseline—never the triggering assignment
or a nonbaseline profile.

The executor's baseline-only recovery authority survives `paused`; paused
blocks non-baseline admission, not safe recovery. `emergency_hold` blocks all
experiment writes and yields to the facility.

An open failure moves to `baseline_recovery` or `emergency_hold`, never directly
to an apparently safe `closed`. Recovery may close only after two qualifying
baseline epochs. Completion, kill-switch disable and ordinary-writer restoration
require that proof, except when an immutable facility-owned emergency-safe-state
event explicitly transfers responsibility. Emergency-hold release always needs
facility authorization.

## Audited lifecycle control surface

Use `POST /api/v1/experiments/{experiment_id}/component-control/commands`
through the authenticated API for v2 configuration, state-artifact registration,
approval recording, lifecycle/phase transitions, admission changes,
facility-safe closure, typed readiness work, baseline-recovery requests and
completion. Use the typed `lock_design` action for the atomic pre-draw lock;
the generic transition action cannot create that lock. The route requires the
separately delivered experiment API token and the attested function-only
lifecycle database login; it never falls back to the ordinary database-owner
pool. Do not place either credential in a command, issue comment or captured
evidence. The API login attestation fails if its lifecycle duty gains shadow
scheduler, randomizer, executor, freezer or any other unlisted v2 function.

The ingestor uses two additional optional key pairs from the existing
`verdify-app-secrets`: `VERDIFY_EXPERIMENT_COMPONENT_DB_USER` /
`VERDIFY_EXPERIMENT_COMPONENT_DB_PASSWORD` and
`VERDIFY_EXPERIMENT_EQUIPMENT_SOURCE_COLLECTOR_DB_USER` /
`VERDIFY_EXPERIMENT_EQUIPMENT_SOURCE_COLLECTOR_DB_PASSWORD`. The former must
authenticate exactly as `verdify_experiment_v2_component_executor_login`; the
latter must authenticate exactly as
`verdify_experiment_v2_equipment_source_collector_login`. Missing or mismatched
credentials leave those experiment paths unavailable and never fall back to the
ordinary ingestor database owner.

Every command on an already configured experiment supplies the caller's exact
expected lifecycle status, execution phase, admission state, component-enabled
state, lease generation and revision-bundle hash. The API checks those values,
calls only the fixed action-to-function allowlist and reads the treatment-free
receipt in one serializable transaction. Refresh the operator status and
reconcile evidence after a 409; never blindly retry stale authority. Initial
`configure` carries the fixed protocol-1/draft/closed/disabled/lease-0
precondition. It records only the candidate revisions, stable study ID and
assignment namespace; it cannot accept a start date, pair count or pre-draw
artifact. Replacing an unlocked candidate requires the exact observed v2
revision and lease and returns the candidate to shadow/disabled posture.

`lock_design` requires the exact current authority axes and atomically binds
the start date, randomized pair count, local selector-context cutoff,
source/design/schema identities, selector identity and artifact, endpoint
artifact, analyzer environment and power artifact after the revision-bound
shadow, commissioning and A/A gates. An exact retry after a lost successful
response returns the already-locked row; any changed lock tuple conflicts.
Generic v1 status, export, unblind, transition and device-policy routes reject a v2
experiment; use only the dedicated component status and lifecycle workflow.

The response contains only an opaque durable result ID, before/after authority
axes, revision identity and database timestamp. It never returns component
vectors, target profiles, approval payloads, recovery reasons, assignment
mapping, efficacy or credentials. `GET .../component-status` remains the
separately operator-token-protected safety/integrity read surface.

## Immutable preflight snapshot

Before the first implementation deployment, record safe metadata only:

- current main and desired GitOps revision;
- current and rollback image digests/config revision;
- Argo sync/health and workload readiness;
- generalized vector mode `off`, current active ID and component kill-switch state;
- exact deployed firmware, registry/entity-grid and baseline revisions;
- public planner/data health and open critical/high alerts;
- single writer/Lease and #433 evidence state;
- direct raw band/control/readback evidence for #424;
- database migration-ledger and restorable-backup metadata;
- current complete 48-field raw baseline payload, stable
  `policy_state_content_sha256`, and latest `observation_receipt_sha256`;
- facility/crop epoch and planned maintenance/irrigation/fertigation windows.

Never read or emit Kubernetes Secret contents or annotation values. A Secret is
secret-bearing in full.

## Config revision

`verdify-config` is consumed through `envFrom`; a ConfigMap edit alone does not
restart consumers. On every experiment ConfigMap edit:

1. edit the declarative base/overlay source;
2. run `scripts/gen-config-revision.sh`;
3. commit the generated pod-template annotation changes with the ConfigMap;
4. pass `tests/test_21_config_revision.py` and full CI;
5. after Argo sync, verify every consumer runs the intended revision.

The stable experiment ID does not change between phases. One initial image
release is built; governed fix-forward releases remain available. Enabling or
rehearsing the coarse kill switch still causes bounded
ConfigMap revision rollouts; shadow→commissioning→A/A→randomized does not.

## Gate 1 — integrated feature-off release

Required before changing the new mode from `off`:

- recent restored-Postgres migration and vertical happy-path test;
- assignment → daily selection → exclusive setter call list → two distinct
  post-delivery raw-observation epochs → stable state hash plus observation
  receipts → exposure → daily outcome/export → analyzer fixture;
- failure injection for partial/unknown delivery, stale/expired/mismatched
  phase-typed preview/readiness/assignment/recovery work, duplicate
  worker, writer collision, reconnect/reboot, DB outage, sensor gap, cfg drift,
  pod restart and interrupted rollback;
- actual deployed entity-grid goldens for all baseline/template values;
- Python/SQL state-hash goldens use the exact domain + schema byte + full
  manifest digest + canonical 178-byte wire encoding; receipt-schema hash and
  goldens match exact RFC-8785/UUID/timestamp spelling;
- compiled replay of every permitted treatment/rollback prefix and
  compiled-default/current-state → full-48 baseline recovery prefix;
- additive v2 lifecycle/phase/admission migrations that leave v1 semantics
  unchanged and prevent canary/A/A rows from entering randomized ITT;
- seven bounded experiment duties: shadow scheduler, restricted randomizer,
  lifecycle controller, component executor, outcome freezer, separate
  read-only blinded analyst and append-only equipment-source collector;
- migration 217's exact ordinary API and ingestor login/duty pairs are live;
  neither ordinary pod carries the database-owner credential, and both
  startup attestations pass against the actual login;
- separate least-information readiness, randomized-assignment and
  baseline-recovery resolvers each read `clock_timestamp()` once inside their
  SECURITY DEFINER function; the recovery branch accepts only linked immutable
  recovery work and can return only the locked baseline after phase, lease,
  validity, authorization and revision checks;
- readiness artifacts bind exact source/deploy/firmware/grid/profile/sensor/
  outcome revisions and semantic drift invalidates affected downstream gates;
- focused tests and `CI_BASE_REF=<base> make ci` green;
- exact source built in-cluster by Kaniko to Zot and digest-pinned on main;
- previous state and declarative rollback diff retained.

Sync the feature-off release without prune. Verify exact revision Synced +
Healthy, running digests/config revision, migration ledger, one writer, normal
planner/data health and unchanged device state.

### Gate 1 ordinary-role credential cutover

Migration 217 deliberately creates no password and switches no workload. Keep
the experiment component `off` while performing this separate rollout:

1. apply/replay migration 217 through the pinned migrate image and retain the
   hostile actual-login fixture result;
2. after the one-release restore rehearsal passes, retain its Job/pod identity
   and log digest, then remove its production component reference in this
   activation change. Because production syncs with `prune:false`, explicitly
   delete only the now-absent rehearsal Job, ConfigMap and deny-all
   NetworkPolicy after the activation sync; this releases the completed pod's
   restore `emptyDir` without discarding the recorded evidence;
3. provision only the four documented
   `VERDIFY_{API,INGESTOR}_RUNTIME_DB_{USER,PASSWORD}` keys out of band; never
   place a value in Git, a command transcript or an issue comment;
4. render `deploy/k8s/overlays/prod` and its compatibility review alias at
   `deploy/k8s/overlays/prod-runtime-role-boundary`; prove they are identical,
   emit no Secret, remove `POSTGRES_PASSWORD` from both ordinary containers,
   point the gather subprocess at the ingestor runtime password, and leave both
   experiment switches `off`;
5. verify the production overlay contains exactly one component reference, then
   merge the reviewed adoption only after the protected four-key reconciliation
   receipt exists and sync without prune;
6. verify both processes pass their exact current-user/membership/ownership/
   ACL attestations, the API and planner paths still work, the singleton
   ingestor is the sole writer, normal actuation is unchanged, and no owner
   credential is ambient in either ordinary pod.

If the cutover fails, remove the component reference and restore the captured
pre-cutover pod templates through GitOps. Do not enable experiment shadow while
either ordinary service still uses the database owner; repair and re-prove the
cutover first.

### Gate 1 no-prune rollback

Before the first sync, retain the complete pre-release production `images:` map
(including API, MCP, ingestor, migrate and every other pin), every pre-release
`verdify-config` source, and every running `verdify.io/config-revision` value.
This is evidence, not a whole-map rollback after migrations are stamped. The
old migrate image carries older bytes for numbered migrations and will refuse
the forward ledger with a SHA mismatch; a rollback must retain the accepted
candidate migrate image and migrations.
The orchestrator is net-new and has no previous image digest, so removing its
Component from Git is not a rollback: with prune disabled, the three live
Deployments would remain extraneous and the Application would remain OutOfSync.

The bounded rollback artifact is
`deploy/k8s/components/experiment-v2-orchestrator-rollback`. In a rollback
commit, keep all of the orchestrator objects desired by consuming that directory
as a production `resource` in place of the normal orchestrator Component. Its
only patch sets the lifecycle, selector and freezer Deployments to zero replicas.
In the same commit:

1. restore only the captured API, MCP and ingestor digests needed for the prior
   application runtime; retain the accepted candidate migrate and orchestrator
   digests, and keep the frozen planner, setpoint and lab pins byte-identical;
2. restore the captured experiment ConfigMap source and generated config
   revisions as one reviewed set (never hand-edit only an annotation);
3. keep `VERDIFY_COMPONENT_EXPERIMENT_ENABLED=off`, the active experiment ID
   empty, generalized vector mode `off`, and the legacy path enabled;
4. keep the additive database migrations and the PreSync ledger verifier in
   place; rollback never attempts a schema downgrade;
5. render the complete production overlay and prove all three orchestrator
   Deployments are desired at zero, every first-party image is digest-pinned,
   and no Secret document is emitted;
6. sync without prune, then verify the prior service digests/config revisions,
   zero orchestrator pods, exactly one ordinary writer, unchanged device state,
   and Argo Synced + Healthy.

Retain the before/after render, exact commit IDs, image/config snapshots and
sync result. Deleting the dormant objects is a later exact-target reviewed
cleanup, never part of this rollback.

## Gate 2 — explicit-ID non-actuating shadow

Before enabling the capability, sync the reviewed
`experiment-v2-credential-bootstrap` component while Gate 1 remains `off` and
the active ID remains empty. Require its retained, non-secret receipt
`six database logins installed and attested; API token shapes validated`, and
capture the Job/pod UID plus exact migrate image/imageID before its 600-second
TTL expires. Any missing Secret key, verifier transaction failure, or one of
the six exact TCP duty attestations is a fail-closed Gate-2 blocker. The
blinded analyst intentionally remains `NOLOGIN`; the broader #643 credential
split is not part of this launch gate.

Provider setup is a separate input gate. The checked-in adapter binds official
OpenAI `https://api.openai.com/v1`, the frozen `gpt-5.6-sol` identity artifact,
and `verdify-hermes` / `OPENAI_API_KEY`. It normalizes the base URL to the exact
`/v1/chat/completions` path and rejects any other authority/path, non-global DNS
answer, model revision, finish reason, or response shape. Kubernetes
NetworkPolicy cannot select an FQDN, so the selector's TCP/443 egress is paired
with that independent application authority lock, redirects disabled, and
proxy environment ignored. The frozen request uses no tools or streaming,
medium reasoning, a bounded completion cap, and canonical profile-only JSON.
Never retrieve or relay the provider key through a repository pod. The provider
key is not part of the six database-password/two-token shape and uniqueness
checks. Missing key or invalid provider behavior remains explicit baseline-only
fallback and cannot qualify as a real provider shadow cycle.

1. Create one explicit experiment UUID with frozen candidate revisions and an
   assignment/selection preview schedule.
2. Set its v2 execution phase to `shadow` with admission `closed`.
3. Commit `VERDIFY_COMPONENT_EXPERIMENT_ENABLED=enabled` and that UUID through the
   production overlay, including the generated config revision.
4. Argo plan/apply the exact app without prune.
5. Verify all expected pods restarted onto the exact revision.
6. Run at least 12 hours **and through at least one complete scheduled context
   cutoff/boundary/choice/receipt/outcome-preview path**; target 24 hours.

Shadow acceptance:

- valid once-daily virtual selections in both arms;
- complete stable cfg-readback state hashes, provenance receipts and outcome
  previews;
- zero experiment component writes/device calls;
- no experiment outbox work that the live executor could lease;
- no writer demotion or interference with ordinary production automation;
- generalized vector mode still `off`;
- no new safety/integrity alert and normal planner/data health green.

Exercise declarative rollback once: component capability `off`, empty ID, revision bump,
sync and restart verification. Re-enable with the same shadow DB phase only
after the rollback proof is retained. These are bounded kill-switch config
rollouts, not per-phase deployment mechanics.

Shadow is evidence collection, not the causal experiment start.

## Gate 3 — field readiness and physical admission capability

Before the first experiment-owned physical write, obtain #641's scoped probe
approval with supervisor, time window and facility rescue owner. Then advance
to `commissioning`, create an immutable `operation_kind=commissioning_probe`
readiness operation,
and run only the approved #424/#433 raw served/control/readback, writer and
baseline-recovery probe. The approval does not authorize a moderate/aggressive
canary.

After that diagnostic:

- #433 passes the deployed quiet-writer, deliberate drift, reconnect, truthful
  lifecycle and scheduler-nonstarvation acceptance;
- #424 has direct raw proof that served, controller and observed band semantics
  are coherent and versioned;
- #641 contains the probe evidence and its second, combined multidisciplinary
  physical signoff before any moderate/aggressive canary or A/A;
- baseline and both templates land exactly on deployed setter steps;
- facility rescue and automatic baseline recovery have named on-call ownership;
- no planned maintenance/feed/flush/irrigation action conflicts with the
  supervised canary window;
- current baseline is complete, fresh and confirmed.

Verify the coarse capability is still enabled at the exact deployed revision.
Keep admission `closed` until a specific supervised action begins; no ConfigMap
or pod restart is needed for this phase transition.

## Gate 4 — supervised template canaries

Run only with the facility manager able to override immediately.

For each sequence:

1. verify current complete baseline and writer/connection generation;
2. keep DB phase `commissioning` and open only the exact typed canary admission;
3. execute the same component-bundle path used by randomized assignments;
4. retain every command lifecycle, raw readback, intermediate prefix, sensor,
   actuator, safety and connection event;
5. open exposure only after two distinct full-state source epochs: all
   component observations follow bundle completion, epoch separation is at
   least 30 seconds, intra-snapshot skew is at most 60 seconds, and both stable
   state hashes match; cfg ingestion owns the immutable epoch IDs and every
   wire's second `observed_at` advances, so a new UUID/receipt over cached
   observations cannot qualify;
6. observe through the conservative six-hour response window or return earlier
   for a safety/operational reason;
7. close treatment exposure/nonbaseline admission, enter bounded
   `baseline_recovery`, then activate baseline through the same executor;
8. confirm two distinct fresh complete baseline epochs, transition admission to
   `closed`, and close the canary.

Required sequences:

- baseline → moderate → baseline;
- baseline → aggressive → baseline.

A mixed/unconfirmed state is transition evidence only. Ambiguity, reboot,
reconnect, foreign writer or sensor invalidity closes exposure and revokes
non-baseline admission. Initial/reboot/common-field recovery restores every
divergent field of the locked 48-field baseline through the exclusive recovery
operation; an 11-field reset is insufficient. A manual/emergency action instead
makes the experiment yield, enter `emergency_hold` and alert. It never fights
the rescue or writes baseline until the facility manager authorizes recovery.
Failure is fixed forward and re-proved; it is not waived because values are
inside clamps.

Canaries validate transport, prefix safety, immediate climate/safety response
and authorized recovery; they do not prove six-hour carryover. The separate
frozen historical/carryover analysis must justify six elapsed hours. If it
cannot, redesign with multi-day blocks before randomization. V2 forbids a
DST-offset crossing.

## Gate 5 — 48-hour A/A dress rehearsal

Advance the same experiment ID to `aa_rehearsal`. Preload two immutable typed
local-day readiness operations that exercise both executor branches while
resolving to the exact baseline. Bind that phase onto every artifact; these
rows never enter the randomized assignment table or ITT and do not use the
historical `kind=aa` result gate.

Require:

- two boundary activations and 48 real hours;
- 100% barrier convergence within the locked time;
- baseline exact in every eligible exposure snapshot;
- zero foreign mutation of an experiment-owned field;
- no exposure across partial/mixed state, reconnect or reboot ambiguity;
- complete climate bins and all-nine-actuator streams under the v2 rules;
- action/outcome rows join one and only one confirmed exposure;
- safety/integrity dashboard and alerts proven with controlled injected faults;
- final baseline recovery re-proved.

A/A estimates no efficacy. Zero-tolerance integrity failure closes admission,
preserves phase-tagged evidence, and returns execution phase to `commissioning`
if rework is needed. The pre-draw lifecycle remains `draft`; `paused` is
reachable only from an already `running` randomized lifecycle and is never a
phase.

## Gate 6 — protocol and randomization lock

Before randomization finalization, create and freeze the **pre-draw design
lock**:

1. freeze the approved baseline, moderate and aggressive artifacts;
2. freeze selector provider/immutable model/system fingerprint contract,
   prompt/messages, decoding controls, tool/schema versions, cutoff/exclusions,
   timeout/retry/idempotency rules and raw request/response hashing;
3. replay frozen pretrial contexts and freeze baseline/moderate/aggressive plus
   fallback frequencies;
4. freeze six-hour completeness/power including selector dilution and plausible
   cross-endpoint correlation; allow 15 pairs only if the joint probability all
   three IUT conditions pass is at least 80%, otherwise freeze a larger fixed
   even count. No sample-size adaptation occurs after the draw;
5. freeze exactly one operating-benefit endpoint with units, weights,
   direction, boundary and missingness; lock the exact climate fields/corridor
   functions and nine stream/integration semantics. Treat `vent=true` as open
   state, never motor runtime; lock minute-slot duplicate rules and fresh
   at-or-before-06:00/pre-midnight same-reset-epoch counter samples and compare
   each delta to state integration over the exact sample-to-sample interval;
   a post-06:00 state snapshot cannot seed the endpoint;
6. freeze the primary ITT export as one fixed [06:00,24:00) row per assigned
   day, including known fallback, failed delivery and rescue without filtering
   on confirmed exposure. Exposure union and 61,560/64,800 seconds are fidelity/
   per-protocol sensitivity only; ambiguous/missing endpoints stay assigned and
   use the frozen bounds/inconclusive rule;
7. freeze analysis formula, confidence level, finite-sample sensitivity and
   environment;
8. freeze the exact local start date, all revisions and minimum role grants;
9. resolve every non-random `TO-LOCK` in the v2 template and commit/hash the
   design lock, then transition lifecycle `draft→locked`;
10. verify no comparative efficacy result has been inspected.

The restricted idempotent randomization routine then locks the unique study
row and internally generates exactly one 32-byte OS-CSPRNG secret; the caller
cannot supply or replace it. The same transaction persists the restricted
secret/no-redraw receipt and creates the RFC-8785 X/Y schedule, schedule hash
and full-entropy commitment using the protocol's exact HMAC/domain/index rules.
Retry returns the existing receipt. Never print, log or commit the secret.

Finalize and commit `planner-switchback-v2.yaml` before day 1. A contract test
must prove that it differs from the pre-draw design lock only in the generated
schedule/receipt fields. The start date cannot change after the draw. If it is
missed, abort that study ID/draw and preregister a new study; never shift the
schedule. Successful idempotent finalization and all readiness bindings
transition lifecycle `locked→armed`; they do not start exposure.

The future public beacon in the historical v1 tooling is not used.

## Gate 7 — randomized day 1

Obtain #642's separate randomized-day-1 go/no-go after both #641 approvals,
canaries, A/A and finalization. Advance
the stable experiment ID's phase `aa_rehearsal→randomized` without a new GitOps
rollout. At the exact day-1 start transition lifecycle `armed→running`, then
open admission only for the current immutable typed assignment; randomized
`open` is illegal before lifecycle is `running`.

At the first boundary verify:

- immutable assignment and frozen revisions;
- expected once-daily AI selection/fallback;
- baseline interposition and exclusive target batch;
- two distinct post-delivery complete expected raw observation epochs;
- a valid exposure open at the second confirmation time;
- exact deployed revision remains Synced + Healthy;
- public planner/data/safety health remains green;
- confirmed baseline rollback remains immediately available.

Only after all of those are true may the program report “experiment started.”

## During the randomized run

- Monitor safety, integrity, exposure, data completeness and rollback only.
- Do not compute or display comparative efficacy or X/Y mapping.
- Keep every assigned day, fallback, override and deviation in ITT.
- Never redraw, reorder, shift, replace or delete an assignment.
- Class 0 observability-only changes may deploy with a revision record.
- Class 1 replay-equivalent reliability changes require equivalence proof and a
  pair boundary.
- Class 2 treatment/controller/band/sensor-calibration/mechanical/outcome changes
  close the epoch; do not pool before/after.
- Class 3 safety defects close exposure, revoke experiment authority and yield
  to the facility. Baseline is sent only when facility-authorized and
  reachable; then abort.

## Immediate rollback — physical truth first

Use this order for software/integrity rollback:

1. **Close exposure and non-baseline admission.** Set lifecycle `paused`, but
   preserve the explicit `baseline_recovery` authority; record the reason and
   assigned ITT day immediately.
2. **Yield to rescue when present.** For a manual/emergency override enter
   `emergency_hold`, send nothing and wait for explicit facility authorization.
3. **Recover baseline when authorized.** Use the durable component executor and
   separately replay-qualified baseline order. After reboot/common drift,
   restore every divergent field of the full locked 48-field baseline; never
   hand-edit an apparent value to bypass the ledger.
4. **Confirm baseline.** Require two distinct fresh complete raw observation
   epochs at the current connection generation and matching stable baseline
   state-content hash.
5. **Persist lifecycle evidence.** Pause or abort with exact receipts/deviation.
6. **Then GitOps off.** Set the component capability to `off` and clear the active ID;
   keep generalized vector mode `off`.
7. **Restore ordinary ownership.** Only after confirmed baseline may normal
   planner/forecast/MCP component admission resume.
8. **Bump, build if needed and sync.** Regenerate the config revision, commit,
   Argo sync without prune and verify restarts/digests/health.

If software cannot restore the baseline, use the facility emergency procedure
and existing firmware/local controls. Do not enable the unqualified
manifest/vector path as a fallback.

## Completion and reveal

After the final scheduled day:

1. close every exposure and stop nonbaseline admission while retaining bounded
   baseline-recovery authority;
2. activate and confirm baseline, then transition admission to `closed`;
3. freeze and hash outcomes, deviations, fidelity, facility epochs and analysis
   environment;
4. pass integrity/completion gates;
5. reveal the 256-bit secret exactly once, verify its commitment and reproduce
   both schedule and X/Y mapping;
6. run the frozen analyzer and publish effects, uncertainty, decision and claim
   limits;
7. declaratively set component capability `off`/empty ID and verify final
   Synced + Healthy;
8. reconcile all GitHub issues against exact source/deploy/runtime evidence.

## Never prune

Never run `argocd app sync --prune` or enable automated pruning for this
rollout. Resource removal is a separate reviewed action with explicit target
and rollback; it is not an experiment phase transition.
