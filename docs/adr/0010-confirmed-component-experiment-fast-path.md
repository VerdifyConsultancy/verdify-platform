# ADR-0010: Confirmed-component fast path for the planner experiment

- Status: adopted
- Date: 2026-08-23
- Owners: planner-experiment outer-loop controller
- Parent: GitHub issue #581
- Supersedes for the first physical trial: the policy-vector-dependent rollout
  sequence in `docs/plans/planner-experiment-program.md`

## Context

The August experiment build created a generalized 48-field binary policy-vector
platform. The resumption audit found that its host, SQL and firmware contracts
do not compose: service arguments and content identities disagree, manifest
delivery is absent, device identity is not persisted continuously, and the
assignment and outcome paths are incomplete.

Those defects block that platform, but they do not block the scientific
question. The deployed firmware already exposes bounded ESPHome number/switch
setters and periodic configuration readbacks for all 11 proposed treatment
fields. Those setters update the same legacy globals used by the deterministic
controller. The greenhouse already operates through that transport.

Requiring a new firmware image, OTA, manifest transaction, NVS journal,
production twin, 96-transition campaign and seven-day A/A before collecting a
causal observation would couple platform modernization to the experiment. It
would also add firmware risk under the open heap incident #428.

## Decision

The first randomized study uses a **confirmed-component fast path** and leaves
the generalized policy-vector platform off.

1. `VERDIFY_POLICY_VECTOR_MODE` remains `off` for the complete fast-path epoch.
   A separate coarse component flag defaults disabled. A worker hard-fails and
   claims no work if the component flag is enabled while vector mode is not
   exactly off.
2. The existing deployed firmware and deterministic safety/control loop do not
   change. No experiment OTA is required.
3. The experiment may change only the existing 11-field allowlist. The other
   37 canonical policy fields are captured and monitored, not retransmitted at
   every boundary. Initial enrollment, reboot or common-field drift is the
   explicit exception: an exclusive recovery-only path restores every divergent
   field of the locked 48-field baseline before exposure.
4. One frozen AI invocation occurs before each local-day boundary in both arms.
   Arm A activates the approved baseline. Arm B may select the approved
   baseline, moderate or aggressive profile. All intraday proposals are
   shadow-only.
   Provider/model identity, decoding/tools/schema, cutoff/exclusions,
   retry/idempotency and request/response hashes are frozen; the executor
   consumes exactly one persisted choice through a least-information resolver
   and never receives the secret, mapping or future schedule.
5. Every boundary first converges through the approved baseline. A sole-writer,
   exclusive host barrier then sends the target differences in a fixed,
   replay-qualified order through the deployed setters.
6. Sequential transition prefixes are never exposure. Exposure opens only
   after two distinct, post-delivery source-observation epochs each contain a
   fresh, complete 48-field readback snapshot equal to the expected normalized
   configuration, with bounded intra-snapshot skew and current writer and
   connection generations. Equality uses one domain-separated hash over the
   existing canonical 178-byte wire encoding plus full manifest digest, with
   locked field IDs/order/types/scales/null rejection and cross-language
   goldens; observation provenance is hashed separately.
7. A partial batch, mismatch, stale readback, reboot, reconnect, lease loss,
   expired/mismatched current typed work (preview, readiness, randomized
   assignment or recovery) or unknown commit closes exposure and revokes
   non-baseline admission. The executor may request baseline only while it has
   a current lease/connection and no facility rescue owns the device. After
   reboot this is a full-48 baseline recovery from the observed/compiled state,
   not merely an 11-field treatment reset. A manual
   or emergency override instead closes exposure, yields authority, pauses and
   alerts; baseline recovery waits for explicit facility authorization. A
   failed B day remains a B day under intention to treat and may not
   automatically re-enter B after a reboot.
8. The default outcome window is 06:00–24:00 local. The conservative six-hour
   washout replaces the long physical settling campaign; its power and
   completeness consequences must be recomputed and locked before day 1. V2
   forbids a DST-offset crossing so this always represents six elapsed hours.
   Primary ITT endpoints use every fixed assigned-day window, including known
   fallback, failed delivery and rescue; confirmed-exposure coverage and the
   95% threshold are fidelity/per-protocol sensitivities, never primary filters.
9. Operations are not described as blinded: the facility manager may see any
   state needed for safety. Comparative analysis remains blinded behind X/Y
   labels until outcomes and deviations are frozen.
10. The restricted idempotent randomizer internally generates one 256-bit
    operating-system CSPRNG secret per study; callers cannot submit or replace
    it. Domain-separated HMAC outputs derive pair order and the hidden
    X/Y-to-A/B mapping. A commitment binds the study ID, blinded-schedule hash
    and the full-entropy secret; the restricted secret is revealed once only
    after outcome/deviation freeze. A one-bit plaintext commitment would be
    brute-forceable and is forbidden. A public future beacon and human-witness
    ceremony are not required.
11. One initial image/capability release is used. A coarse declarative `off|enabled`
    kill switch may require bounded ConfigMap rollouts, but shadow,
    commissioning, A/A and randomized authority are audited database
    transitions on one
    `kind=randomized`, `protocol_version=2` experiment, not separate GitOps UUID
    rollouts and pod restarts. This is an additive v2 state machine; it does not
    reinterpret the v1 `qualification|aa|randomized` rows or migration-213
    bindings.
12. A non-actuating shadow of at least 12 hours **and one complete scheduled
    boundary path**, two supervised template canaries and a 48-hour A/A dress
    rehearsal replace fixed 7–14-day twin and seven-day A/A gates. Canaries
    prove transport/prefix/immediate-safety/recovery only; they do not prove
    six-hour carryover. These are evidence gates: failures must be fixed and
    re-proved.

## Scientific contract

The causal unit remains an adjacent local-day pair. The 30-day study contains
15 target AB/BA pairs and estimates physical admission of the once-daily
bounded AI-selector recommendation versus the frozen baseline under common AI
inference. Both arms run the same virtual inference; only physical admission
differs. Inference compute therefore cancels from the randomized treatment and
is descriptive unless a preregistered sensitivity assigns one call's cost to B.
Before randomization, the frozen
selector is replayed on pretrial contexts to quantify baseline/moderate/
aggressive selection frequencies, and the 06:00–24:00 variance, completeness
and dilution power analysis must either justify 15 pairs or precommit a larger
fixed count. It must provide at least 80% **joint advance-decision power** for
the three-condition intersection-union rule under a locked plausible
cross-endpoint correlation model; marginal 80% for each endpoint is
insufficient. No sample-size adaptation occurs after the draw. The study must
not start a known-underpowered fixed screen merely to retain a 30-day calendar.

The frozen selector context preserves replayable source-row provenance while
remaining within its provider budget: over the preceding 24 hours it retains
the last admitted real climate row from each Unix-epoch 30-minute bucket and
then the newest 48 buckets. It performs no floating-point aggregation. The
provider adapter independently fails closed before network I/O if the complete
request exceeds the conservative byte ceiling derived from model length minus
reserved output tokens; that fallback reason is persisted distinctly.

Safety events are stop conditions, not optimization outcomes. Temperature and
VPD corridor distance remain noninferiority gates. Exactly one operating-
benefit endpoint, including formula, units, weights, direction, boundary and
missingness, is frozen before randomization. It may be commissioned variable
operating cost, or—if commissioning is incomplete—the exact nine-stream
active/open-state-minutes endpoint with unit weights. `vent=true` means vent
open, not motor runtime; commissioned cost must use measured/calibrated travel
events rather than open duration. The unselected endpoint and all per-actuator
metrics are secondary. No post-result metric shopping is allowed, and an
uncommissioned study may not claim energy, water, cost or resource efficiency.

Irrigation and fertigation are never treatment authority. Their schedules,
recipes, EC/pH availability, pressure contention and manual interventions are
versioned covariates/deviations. Crop care and facility safety always override
the protocol.

## What remains mandatory

- #433's truthful, non-starving sole-writer proof.
- Direct raw-device resolution of #424's band/readback contradiction.
- Actual legacy entity-grid validation for every baseline/template value.
- Mixed-prefix replay and fixed-order rollback tests.
- Compiled-default/current-state → full-48 baseline recovery routes, ownership,
  prefixes and confirmation, including all 37 common fields.
- A recent-Postgres vertical test from assignment through frozen outcome.
- A stable canonical 48-value state-content hash for equality, kept separate
  from a per-observation receipt hash that binds source epoch, component
  timestamps, revisions, writer/connection generations and provenance. Neither
  server-derived hash may be called a device-echoed hash.
- Each source epoch is generated and persisted by the cfg-ingestion cycle, not
  relabeled by the executor; every one of the 48 observation timestamps must
  advance before a second confirmation can pass.
- Frozen assignments, endpoints, analyzer, missingness and no-peeking rules.
- Independent safety, emergency override and confirmed baseline recovery.
- Minimal P0 role separation for restricted randomization custody, bounded
  lifecycle transitions, component execution, outcome freezing and read-only
  blinded analysis; the
  shared database-owner credential is not an experiment integrity boundary.
- Machine-enforced legal lifecycle/phase/admission edges and cross-axis guards,
  including shadow-closed, typed current-operation admission, paused
  baseline-only recovery and facility-authorized emergency-hold release.
- #641's explicit probe approval before #424/#433 writes, followed by its
  combined multidisciplinary physical signoff.
- #642's separate randomized-day-1 approval and staged real launch evidence.

## Deferred, not deleted

The following remain useful platform-v2 work but do not block the fast trial:

- arbitrary manifest/vector transport and firmware content identity (#638);
- atomic policy slots, ROM catalog/journal, experiment OTA and recovery image
  (#586);
- full per-workload database role separation (#643);
- production twin build/profile and long live-twin soak (#606);
- polished lifecycle UI and generalized signed-result UX.

The fast path is not allowed to masquerade as completion of those capabilities.

## Alternatives considered

**Finish the generalized vector platform first.** Rejected for the first trial
because it adds a new firmware surface and calendar gates that are not required
for randomization, confirmed exposure, safety, rollback or frozen outcomes.

**Embed two or three certified profiles in a new firmware image.** This is a
sound future safety boundary, but it still requires an OTA, heap evidence and a
recovery image. It is slower and introduces firmware risk when the deployed
setter surface already supports the bounded experiment.

**Treat sequential writes as immediate exposure.** Rejected. The fast path is
valid only because mixed prefixes are excluded, safety-replayed and followed by
complete observed confirmation.

**Remove all wetting fields.** Rejected for this study because climate fog and
mister behavior is central to the hot/dry hypothesis. Instead, water/fertigation
specialists own flow, pressure, dew/condensation and plant-care gates, and the
study makes no root-zone or fertigation-benefit claim.

The fast path reports worst measured north/east/south/west wall-zone state and
may use house-average fields only as an explicitly approved center-screening
proxy. Its air-dew calculation plus manual crown/leaf inspection cannot prove
true center, canopy-surface, leaf-condensation or crop safety. Such a claim
requires commissioned sensors and an explicit #641 hardware/timeline gate.

## Consequences

The experiment can begin non-actuating rehearsal after one initial integrated
software release and can target the first randomized day in roughly 5–8 days if #424,
#433, field readiness, canaries, A/A and the power/design lock pass. The target
causal window is 30 days; a larger sample is precommitted before the draw if the
frozen power analysis says 15 pairs are inadequate.

Later image releases remain possible only through the governed fix-forward
classes; the initial integration milestone never prevents a safety repair or a
replay-equivalent reliability fix.

The tradeoff is that transitions are not atomic on the device. Intervals are
classified as exposure only after complete confirmation; partial prefixes are
excluded, and their possible carryover remains handled by the locked washout
and sensitivities rather than being called atomic. Results apply only to this
deployed firmware, greenhouse, profiles, season and once-daily selector.

## Validation and revisit trigger

Revisit this decision if a prefix cannot be shown safe, raw readbacks cannot
identify complete state, the writer cannot meet #433, baseline recovery cannot
be confirmed, six-hour carryover is inadequate, or the treatment requires a
field without a deployed setter/readback. Any such finding blocks physical
activation; it does not authorize silently falling back to the broken vector
path.
