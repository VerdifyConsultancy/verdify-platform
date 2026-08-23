# Planner experiment fast path — multidisciplinary execution plan

- **Decision:** implement the confirmed-component fast path in ADR-0010.
- **Program owner:** GitHub epic #581.
- **Launch owner:** GitHub issue #642.
- **Physical readiness:** GitHub issue #641.
- **Factual baseline:**
  [`planner-experiment-resumption-audit-2026-08-23.md`](../research/planner-experiment-resumption-audit-2026-08-23.md).
- **Machine-readable target:**
  `research/planner-efficacy/protocols/planner-switchback-v2.template.yaml`.
- **Current production invariant:** the generalized policy-vector mode remains
  `off`; this plan does not authorize physical activation by itself.

## 1. Outcome and speed target

The shortest reliable path is not to finish the unfinished generalized
firmware platform. It is to put a truthful activation/exposure barrier around
the bounded component transport already used by the running greenhouse.

The program now targets:

- non-actuating shadow evidence as soon as the integrated release is deployed;
- supervised physical canaries after software, writer, band and field gates;
- a 48-hour A/A dress rehearsal;
- randomized day 1 approximately 5–8 days after implementation begins, if the
  evidence gates pass;
- a 30-day, 15-pair target causal run after day 1, enlarged before the draw if
  the frozen power/dilution analysis says that target is inadequate.

This is a target, not a date waiver. “Immediately” means agents begin the
non-actuating and implementation work without waiting on deferred platform
work. It does not mean claiming a physical experiment before the device state,
safety, rollback and outcome path are proven.

## 2. First-principles requirements

A valid and safe experiment needs only five load-bearing guarantees:

1. assignments are randomized and immutable before their outcomes exist;
2. every analyzed interval has known, fresh, observed physical treatment;
3. the existing deterministic firmware retains all safety/control authority;
4. ambiguous delivery closes exposure and either confirms baseline under
   bounded authority or yields to an explicit facility-owned emergency-safe
   state, while remaining honest in ITT;
5. outcomes, deviations and analysis are frozen before arm mapping is revealed.

Everything else must justify its place on the critical path.

### What changes

| Previous prerequisite | Fast-path decision | Reason |
|---|---|---|
| Arbitrary 48-field manifest/vector transaction | Defer to platform v2 (#638) | All 11 treatment fields already have bounded deployed setters/readbacks. |
| New experiment firmware, ROM vector and NVS journal | No firmware change or OTA | Existing deterministic safety path remains authoritative; avoids #428 heap risk. |
| Device-echoed vector hash | Compare a stable canonical 48-value state-content hash and separately retain provenance-rich observation receipts | Exposure needs observed equality, not a new firmware identity protocol. |
| Intraday template changes | One frozen selection before each daily boundary | Fewer transitions, clearer treatment, simpler recovery and carryover. |
| Six directed template edges | Always interpose baseline; qualify four baseline↔template edges | Removes moderate↔aggressive transition complexity. |
| 96 physical transitions / up to 45 days | Prefix replay + HIL + two supervised physical canaries | The firmware and setters are already deployed; fixed-duration controller identification is not a v1 gate. |
| 7–14-day production-twin gate | Parallel platform work, not a trial gate | Current twin covers only 22/48 fields and six actuators; it does not prove the promised scope. |
| Seven-day A/A | 48 real hours plus accelerated schedule/fault simulation | A/A is a zero-tolerance integration test, not an efficacy estimate. |
| Public beacon + witnessed ceremony | One accepted 256-bit OS-CSPRNG secret, domain-separated schedule/mapping derivation, append-only no-redraw record and full-entropy commitment | Precommitment, hidden mapping and immutability provide the causal protection. |
| Operational double blinding | Operator-visible safety, analyst-blinded X/Y | Facility personnel must see enough to protect crops/equipment; automated endpoints reduce observer bias. |
| Full workload DB role split | Minimal function boundary now; broad split in #643 | Full tenancy hardening is valuable but not causal identification. |
| Separate GitOps deployment per phase/UUID | One initial image release; audited DB phase transitions; bounded ConfigMap rollouts only for the coarse kill switch | Avoids restarts for shadow→commissioning→A/A→randomized while retaining declarative off; governed fix-forward releases remain available. |
| Polished lifecycle UI before collection | Audited CLI/API, immutable ledger, safety/integrity board | Styling is not a safety or validity gate. |
| Any change destroys all evidence | Four-class fix-forward policy | Nonsemantic fixes can ship without pooling incompatible causal epochs. |

### What does not change

- AI cannot set arbitrary values. It chooses one approved profile ID.
- Firmware clamps, interlocks, dwell, emergency overrides and local fallback
  remain authoritative.
- Assignment is the primary estimand; fallback stays in ITT.
- No comparative efficacy is inspected during the run.
- Baseline recovery precedes disabling the experiment capability.
- No resource/cost/crop claim exceeds the measurement actually commissioned.

## 3. Treatment and physical exposure

### 3.1 Frozen scope

The canonical policy has 48 fields. The maximum treatment allowlist remains:

1. `cool_stage2_over_high_f`
2. `sw_cool_all_fans_at_high_enabled`
3. `fog_escalation_kpa`
4. `min_fog_on_s`
5. `min_fog_off_s`
6. `mister_engage_kpa`
7. `mister_all_kpa`
8. `mister_all_delay_s`
9. `mister_pulse_gap_s`
10. `mister_pulse_on_s`
11. `mister_water_budget_gal`

The other 37 values must equal the approved baseline throughout exposure. The
protocol must regenerate the candidate profiles on the **actual deployed
ESPHome entity grid**. Historical candidate values that cannot land exactly
(for example, an unsupported duration increment) are invalid; they are not
rounded silently during activation.

### 3.2 Daily strategy

- A frozen context snapshot closes before the local-day boundary.
- The same frozen model/prompt/context contract runs once in both arms.
- Its only valid output is `baseline`, `moderate` or `aggressive`.
- Malformed, late, unavailable or revision-mismatched output resolves to
  baseline and is logged.
- Arm A always physically activates baseline and retains the virtual AI choice.
- Arm B physically activates the valid choice.
- Intraday AI, deterministic forecast and ordinary plan proposals are stored
  and scored shadow-only for experiment-owned fields.

The selector lock includes the provider and immutable model identifier,
returned system fingerprint when available, prompt and system-message bytes,
decoding parameters/temperature/seed, tool and response-schema revisions,
timeout, retry and idempotency rules, and raw request/response hashes. Its
context excludes the X/Y label, private mapping, post-cutoff telemetry,
comparative outcomes and online lesson updates. Exactly one persisted virtual
choice per local day is consumed in either arm; a retry cannot create a second
choice or silently switch a model alias.

This estimates the physical value of admitting a bounded daily AI-selector
recommendation instead of baseline, including its failures and conservative
baseline choices. Because the same virtual inference runs in both arms, AI
compute is not randomized and cannot be included in the primary net-benefit
claim; report it descriptively or add a preregistered B-one-call sensitivity.

### 3.3 Confirmed-component activation barrier

At every boundary the sole executor:

1. validates the current experiment, phase-typed work ID/kind and validity,
   frozen revisions, writer lease and connection generation; randomized work
   additionally validates the immutable assignment;
2. closes the prior exposure;
3. converges the 11 treatment fields to baseline in a fixed rollback-safe order;
4. confirms baseline readbacks;
5. for a non-baseline B target, writes only differing allowlisted fields in a
   fixed activation order with no interleaving;
6. records every requested, queued, sent, failed, cancelled, superseded and
   confirmed component under one bundle ID;
7. waits for two distinct complete 48-field source-observation epochs: every
   component observation is after bundle delivery finished, intra-snapshot skew
   is at most 60 seconds, the epochs are at least 30 seconds apart, each is
   fresh, and both equal the normalized expected state. The cfg-ingestion cycle,
   not the executor/receipt assembler, generates and immutably persists each
   epoch ID. Every component timestamp in the second receipt must advance; a
   relabeled UUID over cached observations cannot qualify;
8. opens exposure at the second confirming snapshot, never at request or final
   command time.

Routine boundaries send only 11-field treatment differences. Initial
enrollment, reboot/reset or drift in any of the common 37 uses a distinct
recovery-only operation: the same sole executor compares against the locked
complete baseline and writes every divergent canonical field, up to all 48,
under an exclusive separately replay-qualified baseline-safe order. Every
canonical field must have an exact deployed setter and readback. Replay/HIL
covers compiled-default→baseline and all permitted recovery prefixes. Ordinary
writers remain held until two qualifying full-baseline observation epochs; the
executor never tries a B target in that recovery operation.

The server derives two different identities under
`derived_cfg_readbacks_v1`:

- `policy_state_content_sha256` is the stable equality identity:
  `SHA256("verdify-policy-state-content-v1" || 0x00 || schema_u8 ||
  full_manifest_digest || encode_policy_vector(values))`. It reuses the exact
  178-byte ascending-wire-ID codec, types/scales/round-half-even encoding and
  strict missing/extra/null rejection, with checked Python/SQL golden vectors.
  Expected and observed state use the same function. The baseline locks this
  hash.
- `observation_receipt_sha256` is per observation. It binds the state-content
  hash, source epoch, component timestamps, phase/kind/work/bundle lineage, deployed
  revisions, writer/connection generations and persistence provenance. Its
  exact JSON Schema bytes and canonical golden receipt are source-locked.

Neither identity is a device-echoed hash. Receipt timestamps/generations never
pollute the stable baseline content identity.

Any mismatch, freshness breach, reboot/reset, reconnect, writer conflict,
current typed-work expiry/mismatch, database uncertainty or safety event closes exposure and
revokes non-baseline admission. With a current lease/connection and no facility
rescue, the executor may make one bounded baseline-recovery attempt. Otherwise
it enters no-exposure emergency hold and alerts instead of repeatedly writing.
A manual/emergency override always makes the experiment yield: it closes
exposure, pauses, and sends no baseline command until the facility manager says
that recovery is compatible with the rescue. It never automatically reactivates
B after a reboot on that assigned day.

The admission state is orthogonal to lifecycle status. `paused` blocks new
non-baseline work but must leave a separately authorized, baseline-only recovery
path available; GitOps mode is turned `off` only after baseline is confirmed or
facility emergency control has explicitly taken ownership.

### 3.4 Evidence that this path exists — and its limits

The replan verified the current registry and the exact deployed-firmware source
identified by the resumption audit:

- all 11 treatment fields are planner-pushable, have one ESPHome setter route
  and one cfg-readback object in `verdify_schemas/tunable_registry.py`;
- all 48 canonical vector fields in the current-source registry have setter and
  cfg-readback metadata, supporting a recovery adapter; this is still not proof
  of the exact running-device grid;
- the deployed firmware source has writable number/switch entities and 30-second
  cfg readbacks for all 11 fields;
- those readbacks expose the same legacy globals consumed by the deployed
  controller; current-main firmware routes both through `policy_read` while
  generalized vector mode is off;
- `ingestor/ingestor.py` already persists full cfg state on a roughly 60-second
  cadence and has readback-confirmation hooks;
- `research/planner-efficacy/baseline/baseline.py` and the candidate artifacts
  already validate registry/firmware bounds.

This evidence supports building the adapter; it is not the release proof. The
legacy entity step must be verified against the running device, because the
historical candidate contains values such as 59-second fog duration and
38-second pulse gap that may not land exactly on deployed entity steps. All
treatment globals and the daily mister accumulator are volatile across reboot,
which is why reboot terminates exposure and forbids automatic B re-entry.

Finally, #433 has not yet proved that the shared writer is truthful and quiet,
and #424 has not established coherent physical band truth. Both remain hard
physical gates even though the route inventory exists.

### 3.5 Additive v2 lifecycle and integrity roles

The stable study row has `kind=randomized`, `protocol_version=2` and
`transport_kind=legacy_components_v1`. Three axes remain distinct:

- the existing lifecycle status protects design/run immutability with exact
  edges `draft→locked`, `locked→draft|armed|aborted`,
  `armed→running|aborted`, `running→paused|completed|aborted`, and
  `paused→running|aborted`;
- execution phase (`feature_off → shadow → commissioning → aa_rehearsal →
  randomized`, with `aa_rehearsal → commissioning` for rework) identifies the
  purpose of evidence;
- admission state (`closed|open|baseline_recovery|emergency_hold`) controls
  physical authority and preserves baseline-only recovery while paused.

Every assignment, bundle, observation receipt, exposure, outcome and readiness
artifact carries its phase. Canary/A/A rows are rejected from randomized ITT.
The additive v2 transition function binds typed immutable canary/A/A evidence
before `randomized`; it supersedes migration 213's separate qualification/A/A
result prerequisites only for protocol v2. Existing v1 kinds, rows and gates
retain their meaning.

Shadow, canary and A/A run while lifecycle is `draft` through additive v2
preview/readiness operations. Shadow creates no device operation. Canary/A/A
work is immutable and typed but never enters randomized assignment/ITT tables;
it must not reuse or weaken the existing armed/running-only
`fn_freeze_experiment_context` or `fn_create_assignment`. Separate
least-information readiness and randomized target resolvers keep those paths
distinct; a third all-physical-phase recovery resolver accepts only a linked
immutable recovery work ID and can return only the locked baseline. Only the
pre-draw design lock moves `draft→locked`, successful
randomization finalization moves `locked→armed`, and day-1 start moves
`armed→running` before randomized admission opens.

Machine guards require shadow to be closed; open admission to carry a current
typed operation, lease and phase readiness; randomized admission to have one
current immutable assignment; and paused/terminal state to forbid nonbaseline
open. An open failure enters baseline recovery or emergency hold, never a
deceptively safe closed state. Closing recovery, completion, disabling the
capability and ordinary-writer restoration require two qualifying baseline
epochs, except for an explicit facility-owned emergency-safe-state event.
Emergency-hold release requires immutable facility authorization.

P0 integrity also requires bounded roles: a restricted randomizer owns the
secret, a lifecycle controller may call only legal v2 transitions, the
component executor may append current bundle/receipt/exposure state but cannot
read the mapping, an outcome freezer may read blinded assignment ID/X-Y/phase
lineage to build immutable exports but cannot mutate assignments or read the
mapping/secret, and a separate read-only blinded analyst consumes only those
frozen exports. A
least-information SQL resolver reads `clock_timestamp()` exactly once inside
the SECURITY DEFINER function and returns only the current permitted profile/state
hash after server-side phase/validity/lease/revision checks; the executor cannot
supply time or enumerate the schedule. Readiness artifacts bind applicable
source/deploy/firmware/grid/profile/sensor/outcome revisions, and semantic drift
invalidates affected downstream gates.
Runtime use of a shared database-owner credential is forbidden. The broader
platform workload-role split remains #643.

## 4. Scientific design v2

### 4.1 Design and estimand

- One greenhouse, America/Denver local days.
- Target 30 consecutive days and 15 adjacent-day pairs; final fixed count is
  locked only after the power gate.
- Each pair contains one A and one B day in a precommitted AB or BA order.
- Primary estimand: mean paired effect of physical admission of the once-daily
  selector recommendation versus frozen baseline under identical virtual AI
  inference over all precommitted adjacent-day pairs in the locked schedule.
- Both arms run identical virtual AI inference; only admission differs.
- Planner timeout, baseline choice, delivery failure, fallback, safety rescue
  and manual override stay in ITT.
- One predeclared hot/bright/dry subgroup may improve interpretation, but it
  cannot replace or reorder the primary fixed calendar after outcomes exist.

### 4.2 Randomization and blinding

The restricted idempotent assignment routine internally generates exactly one
256-bit OS-CSPRNG secret after locking a unique study row; callers cannot submit
or replace it, and retry returns the existing receipt. Domain-separated
HMAC-SHA256 derives pair `j` from a zero-based `uint32_be(j)` input and derives
the X/Y-to-A/B mapping under a separate domain. RFC 8785 canonical JSON fixes
schedule bytes. It publishes:

- the complete locked-length blinded X/Y schedule and canonical hash;
- `SHA256("verdify-switchback-v2/commit\0" || UTF8_NFC(study_id) || 0x00 ||
  schedule_hash_bytes || 0x00 || secret_32_bytes)`;
- source, algorithm and protocol revisions.

A commitment to a bare one-bit mapping is forbidden because it can be tried
both ways. The full-entropy secret remains in the restricted randomizer store,
outside Git, logs, executor, operator and analyst surfaces. After frozen
outcome/deviation hashes, a one-time reveal verifies the commitment and
reproduces both schedule and mapping.

No future public beacon is required. The facility manager may see physical
values and override them. Analysts, comparative dashboards and the frozen
analyzer receive X/Y until the outcome/deviation exports are complete and
hashed. This is analyst blinding, not a claim that greenhouse operations are
blind.

Locking has two mechanically separated steps. First, an immutable pre-draw
design lock freezes every non-random value: profiles, selector, endpoints,
missingness, power/sample size, exact local start date, analyzer, grants and
source revisions. Then one
randomization transaction creates the secret-backed X/Y schedule, schedule
hash, mapping commitment and no-redraw receipt. The finalized protocol may
differ from the design lock only in those receipt-derived fields, verified by a
contract test, and is committed before day 1. If the locked start is missed,
abort that study ID/draw and preregister a new study; never shift the schedule.
There is no editable protocol after randomized outcomes begin.

### 4.3 Washout, completeness and carryover

The default primary window is 06:00–24:00 local:

- 6-hour boundary/washout exclusion;
- 72 expected 15-minute bins;
- provisional daily climate completeness: at least 66/72 bins and no
  continuous gap over 30 minutes;
- provisional per-protocol exposure denominator: 64,800 seconds;
- provisional 95% threshold: 61,560 seconds.

Those exposure seconds are treatment-fidelity evidence and define only a
prespecified per-protocol sensitivity. Primary ITT constructs one fixed-window
endpoint for every assigned day without filtering/truncating on confirmed
exposure. Known baseline fallback, failed delivery and rescue stay in the
assigned arm; irreducibly ambiguous/missing outcomes remain in the ledger and
invoke the frozen bounds/inconclusive rule.

The data-science lane must rerun historical variance/power and lock these
values before schedule generation. Six hours is a conservative isolation
choice, not proof of zero biological carryover. The frozen report must retain
0-hour/6-hour, previous-arm, water use and major-intervention sensitivities.
Canaries prove transport, prefix safety, immediate response and recovery only;
they do not establish carryover. If the historical/carryover artifact cannot
justify six elapsed hours, use multi-day blocks or redesign before the draw.
V2 forbids a DST-offset crossing, so the local exclusion cannot collapse to
five elapsed hours.

### 4.4 Outcomes and claims

Safety/integrity gates:

- no uncontained interlock, emergency or facility-rescue failure;
- truthful assignment, writer, readback and exposure lineage;
- frozen baseline recoverable and confirmed;
- primary/reference sensor and actuator-stream quality inside locked limits.

Climate co-primary gates:

- daily VPD corridor-distance noninferiority, +0.05 kPa engineering margin,
  subject to horticultural/climate justification before lock;
- daily temperature corridor-distance noninferiority, +0.50 °F engineering
  margin, subject to the same pre-lock justification;
- worst **measured wall-zone** corridor distances across north/east/south/west,
  air-temperature-minus-air-dewpoint screening, primary/reference disagreement
  and a facility-approved crown/leaf inspection rubric are hard safety/advance
  gates. `temp_avg`/`vpd_avg`/`rh_avg` may be called only an approved
  house-average center-screening proxy, never a center probe.

This fast path has no commissioned center, canopy-surface or leaf-wetness
sensor. It therefore makes no true center/canopy/leaf-condensation or crop-safety
claim. Adding such a claim requires an explicit #641 hardware gate, revised
timeline and recommissioning; missing required proxy/reference/manual evidence
blocks advance rather than being interpolated.

Operating-benefit gate:

- freeze exactly one primary endpoint before the draw: either variable
  operating cost in USD from commissioned electrical/fuel/flow coefficients,
  or the sum of active-state minutes across eight relay streams plus open-state
  minutes for `vent`, across exactly `heat1`, `heat2`, `vent`, `fan1`, `fan2`,
  `fog`, `mister_south`, `mister_west`, and `mister_center`, all with unit
  weight;
- for either choice, freeze formula, units, coefficients, direction
  (lower-is-better), superiority boundary (AI-minus-Frozen < 0), missingness and
  uncertainty handling. The unselected candidate and per-actuator metrics are
  secondary only.

`equipment_state.vent=true` means vent open, not a powered motor runtime. A
commissioned cost endpoint therefore uses measured/calibrated vent travel
events, never open duration. If resource commissioning is unavailable, the
nine-stream metric may be locked only as heterogeneous “control-state burden,”
not energy, water, cost, carbon, crop, yield or profitability.

All required conditions use the same predeclared one-sided confidence level.
The v2 power artifact must justify and pin it before schedule lock; because the
advance rule is intersection-union (all conditions must pass), it must not add
an unnecessary multiplicity penalty. Report effects and intervals, not only a
binary verdict. Null/incomplete evidence is `iterate/inconclusive`, not proof of
equivalence.

The exact outcome input contract is machine-readable in protocol v2:
`public.climate.temp_avg`/`vpd_avg` for `greenhouse_id='vallery'`, crop-band
limits from `fn_crop_band_value('house', ...)` at bucket start, the four named
wall-zone fields for localized summaries, and right-continuous `state=true`
intervals from the nine exact `public.equipment_state` IDs above. Source,
sensor, corridor, equipment-inventory and extractor revisions are locked.
Minute-slot aggregation gives each UTC minute one weight; extra polls do not
inflate count, and conflicting duplicate timestamps beyond locked sensor
resolution invalidate the slot. Equipment streams require a fresh direct
state/counter snapshot at or before 06:00 in [05:58:30,06:00:00] and a fresh
pre-midnight counter snapshot in the same reset epoch. Compare the counter
delta to state integration over the exact sample-to-sample interval, never the
different fixed 06:00–24:00 window, a since-midnight total or a post-reset
24:00 reading. A post-06:00 state snapshot cannot seed the endpoint.

Before randomization the frozen selector runs over versioned pretrial contexts
to quantify baseline/moderate/aggressive and fallback frequencies. The revised
06:00–24:00 power artifact must include this treatment dilution and
completeness. Fifteen pairs are allowed only if the **joint probability that all
three intersection-union conditions pass** is at least 80% under the locked
smallest relevant effects and plausible cross-endpoint correlation model.
Marginal 80% per endpoint is insufficient. Otherwise, before drawing,
precommit a larger fixed even day count. No sample-size adaptation occurs after
the draw. Starting a known-underpowered 15-pair screen is not an option.

### 4.5 Facility and biological covariates

Record and version:

- crop inventory, growth stage, canopy/topology and material plant events;
- irrigation and fertigation timing, recipe, EC/pH availability and flushes;
- fog/mister flow/pressure, water supply faults and daily water state;
- doors, vents, shade/screen position, occupancy and manual operations;
- maintenance, equipment availability, power/network interruption and resets;
- outside temperature, RH/dewpoint, solar/forecast vintage and clock quality;
- primary/reference T/RH disagreement and sensor wetting/recovery.

Do not freeze necessary plant care. Planned work is kept outside a pair where
practical; unplanned work becomes an immutable deviation and remains in ITT.

## 5. Multidisciplinary readiness

Issue #641 owns one manifest and two ordered approvals: probe authorization
before the first #424/#433 experiment-owned physical write, then combined
multidisciplinary signoff before moderate/aggressive canaries and A/A. Issue
#642 owns the later randomized-day-1 approval.

| Persona | Owns | Evidence before randomization |
|---|---|---|
| HVAC-D / climate systems engineer | Profile values, achievable bands, air-dew screening, fan/vent/heat/fog capacity and interlocks | Independent climate comparison, worst measured wall-zone review, explicit center/canopy claim limits, no heat/cool conflict, safe immediate canary response; carryover is a separate analysis gate |
| Water systems engineer | Climate-water topology, flow/pressure, leak/low-water stops and scope | Calibrated flow or runtime-only limitation, no leak/pressure conflict, daily ceiling truth |
| Fertigation specialist | Feed/flush/recipe epoch, EC/pH scope and pressure contention | Experiment cannot change fertigation; blackout calendar and deviation rules locked |
| Controls engineer | Setter grid, order, prefix safety, dwell/interlocks, washout and fallback | Mixed-prefix replay, HIL, idempotency, baseline recovery and fault matrix |
| Systems integration | Assignment fences, writer/connection generations, clocks, revision identity and rollback | Restored-DB vertical test and exact deploy/runtime identities |
| IoT / sensor technician | Sensor placement/calibration/freshness, readback map, actuator evidence and time sync | Reference comparison, wetting flags, bounded clock skew and complete readbacks |
| Data scientist | Estimand, power, randomization, missingness, outcomes, analyzer and no-peeking | Frozen schedule/formulas/environment; reproducible fixtures/export hash |
| Greenhouse / facility manager | Crop epoch, maintenance, doors/shade, manual actions, daily readiness and emergency authority | One-page rescue rehearsal, on-call coverage and signed physical go/no-go |

## 6. Autonomous lane graph

The outer-loop controller owns L0 and runs no more than three workers
concurrently.

```text
L0 contract / migration reservations / issue truth
 ├─ L1 writer + activation + exposure       (#433, #639)
 ├─ L2 daily selector + schedule + protocol (#588)
 ├─ L3 data + outcomes + analysis           (#583, #640)
 └─ L4 multidisciplinary field readiness    (#424, #641)
                 │
        L5 observability + release surface  (#587)
                 │
        L6 integration + staged launch      (#642)
```

### L0 — outer-loop contract and coordination

Owns this plan, ADR-0010, protocol invariants, dependency graph, migration
number reservation, file ownership and decision escalation. L0 alone merges,
builds, deploys, advances physical phases and closes/relabels issues.

Acceptance: all issue bodies, labels, milestones, docs and tests describe one
architecture; no lane assumes the platform-v2 vector path is enabled.

### L1 — truthful writer, activation and exposure

Issues: #433 and #639.

Owns `ingestor/esp32_push.py`, a new isolated component-bundle executor,
readback-derived receipts/exposure and focused tests. It may not edit firmware,
the scientific protocol, API/dashboard or GitOps.

Acceptance includes exclusive non-interleaved batches; baseline interposition;
entity-grid normalization; two distinct post-delivery observation epochs;
current experiment, typed work ID/kind, validity, lease and connection fences;
randomized work also fences the immutable assignment; reboot
hard stop; recovery-only full-48 convergence from compiled defaults/common
drift; manual-override yield; separately replay-qualified activation and
rollback orders; paused baseline-only recovery; and injected
partial/unknown/duplicate/
disconnect/DB failures. #433's
quiet-writer, one-drift/one-write, reconnect-generation and truthful lifecycle
runtime proof is mandatory.

### L2 — daily selector, schedule and protocol

Issue: #588, with executor interface in #639.

Owns frozen context cutoff, identical inference in both arms, the three-value
selection contract, full AI runtime/retry identity, one accepted 256-bit secret
and domain-separated derivation, commitment/no-redraw record, two-step design
lock/finalization, schedule loader, v2 template, selector-dilution power
artifact and analyzer contract. It cannot write the device.

Acceptance includes no arm/post-cutoff/outcome leakage into selector context,
exactly one persisted choice per day, no intraday admission, baseline fallback
for every invalid selection, immutable assignment IDs/ranges,
DST-crossing rejection/boundary/restart tests, revised six-hour power/
completeness and a justified fixed sample size with joint decision power, and
pre-day-1 frozen artifacts.

### L3 — minimal data kernel, outcomes and analysis

Issues: the fast-path portion of #583 and #640.

Owns new additive ledgered migrations, the orthogonal v2 lifecycle/phase/
admission state machine, minimum bounded role grants, activation component
provenance, expected/observed state-content hashes and observation receipts,
exposure reasons, daily outcome SQL/CLI, fixtures, export hashes and
completion/unblind enforcement. It reserves a migration number with L0 before
editing.

Acceptance: recent restored DB migrates forward while v1 behavior remains
unchanged; canary/A/A rows cannot enter randomized ITT; runtime services cannot
use a shared owner; real SQL functions reject bad hashes/state;
hand-calculated climate/runtime/resource/missingness fixtures match;
assignments cannot be replaced; arbitrary 64-hex strings do not satisfy result
gates; fixed-window ITT rows are never exposure-filtered; completion requires
closed exposures, confirmed baseline, frozen
exports/deviations and integrity evidence.

### L4 — field readiness

Issues: #424 and #641; #433 is coordinated with L1.

Owns the schema-validated readiness manifest, calibration/work-order templates
and operator evidence. It remains read-only until #641 probe approval; only the
exact ledgered `commissioning_probe` may then write under L0 phase admission.

Acceptance follows #641 exactly: first record the scoped probe authorization,
model the diagnostic as immutable `commissioning_probe` readiness work, run #424/#433
only inside that scope, then obtain the combined persona signoff before
moderate/aggressive canaries. Together with #642 randomized-day-1 approval,
these are the three planned human decision events.

### L5 — observability, lifecycle and release surface

Issue: #587.

Owns the new safe-default coarse component-experiment kill switch
(`off|enabled`), stable experiment ID, audited v2 lifecycle/phase/admission
surface, safety/integrity dashboard and actionable alerts. Shadow versus
physical authority is a database phase/admission decision, not a second
ConfigMap mode. Twin and UI polish are not part of its v1 acceptance.
Any worker hard-fails and claims no experiment work if the component flag is
enabled while generalized vector mode is not exactly `off`.

The board shows phase, operation kind and generic work ID for preview/readiness/
recovery; assignment ID/X-Y appears only for randomized work. It also shows
expected/observed equality, snapshot age, exposure, writer/connection
generation, safety/fallback, sensor/band truth, outcome completeness and
rollback readiness. It does not show comparative efficacy or X/Y mapping.

### L6 — release and real start

Issue: #642.

Owns integrated CI/fault testing, merge order, the initial image build/release,
in-cluster Kaniko→Zot digest pin, bounded kill-switch config rollouts, Argo
plan/sync without prune, exact-runtime verification, 12–24 h shadow, canaries,
48 h A/A and randomized day 1. It keeps generalized
`VERDIFY_POLICY_VECTOR_MODE=off`. Later image releases are allowed only through
the governed Class-1/2/3 fix-forward rules; “one initial release” is not a ban
on safety or replay-equivalent repairs.

## 7. Outer-loop control algorithm

1. Snapshot `origin/main`, unrelated files, issues/PRs, live safe flags, Argo
   revision/digests, public health, writer/readback state and rollback metadata.
2. Reconcile L0 first; reserve migration IDs and disjoint file boundaries.
3. Spawn at most three dependency-ready agents. Prefer isolated worktrees; if
   unavailable, enforce non-overlapping paths in the shared workspace.
4. Give each agent exact inputs, exclusions, tests, fault cases and definition
   of done. Agents return commit SHA, file inventory, checks and rollback impact.
5. Agents may implement and open PRs. They may not merge, deploy, advance DB
   phases, close issues, expose secrets or touch the live device.
6. L0 audits scope, rebases, runs focused tests and then
   `CI_BASE_REF=<base> make ci`.
7. Compose ready heads in an integration worktree. Run the real restored-DB →
   assignment → legacy setters → raw readbacks → exposure → outcome/analyzer
   chain and every negative case before merging.
8. Send concrete failures to the owning lane. Reallocate/fix interfaces before
   escalating routine engineering.
9. Merge only green dependency-satisfied PRs. Update issue checkboxes with exact
   commit/test evidence immediately.
10. Build the initial affected images from the final main SHA via Kaniko to Zot.
    Verify digests and accept the generated pin; any later image follows the
    explicit fix-forward classification and boundary rules.
11. Argo plan/apply the exact target without prune. Verify Synced + Healthy,
    running digests/config revision, migration ledger, one writer, greenhouse
    health and rollback snapshot.
12. Advance automatically through the v2 `shadow` phase and software fault
    rehearsal; phase rows are bound onto every downstream artifact.
13. Request #641's scoped probe approval before any experiment-owned physical
    write; ledger and run the #424/#433 diagnostic as `commissioning_probe` readiness
    work.
14. Obtain #641's combined multidisciplinary signoff; run supervised canaries
    and 48 h A/A.
15. Freeze the pre-draw design, execute the one secret-backed randomization
    finalization, verify its only allowed deltas, then request the single
    #642 randomized-day-1 go/no-go. Confirm day-1 physical exposure and monitor
    integrity/safety only.
16. After the final locked day, first close exposure/nonbaseline admission while
    retaining baseline-recovery authority, then activate and confirm baseline,
    transition admission to closed, freeze/hash exports and deviations,
    complete, reveal the full secret once, reproduce
    schedule/mapping, run the frozen analyzer and reconcile every GitHub
    artifact.

## 8. Escalation policy

Escalate only for:

- changing treatment fields/bounds, primary outcomes/margins, safety stops or
  physical controller authority;
- #641 probe authorization, #641 combined field/profile signoff and #642 first
  randomized activation;
- any unexpected OTA or hardware mutation;
- inability to confirm baseline recovery;
- safety or terminal integrity stop;
- mapping-custody/no-redraw failure;
- required access outside the declared repository/deployment authority.

Test failures, merge conflicts, ordinary implementation choices, reversible
observability changes and a first failed approach are not human escalations.

## 9. Fix-forward policy

| Class | Example | Action |
|---|---|---|
| 0 — nonsemantic | Dashboard layout, logging, additional non-authoritative telemetry | Deploy and continue; record revision. |
| 1 — replay-equivalent reliability | Retry/timeout/alert repair with unchanged treatment, controller, sensor calibration and outcomes | Before randomization, fix immediately; during run deploy only at pair boundary after equivalence proof. |
| 2 — epoch semantic | Profile, prompt/model contract, firmware behavior, band, sensor calibration, mechanical authority, endpoint/analysis formula | Confirm baseline, close epoch and start a new protocol version; never pool. |
| 3 — safety | Uncontained control, failed fallback, crop/equipment risk | Close exposure and yield immediately. Follow facility rescue authority; activate baseline only when safe/authorized, then abort, repair and re-commission. |

Missing days and overrides remain in the immutable ITT ledger. They are never
silently replaced or deleted. Necessary plant care always wins.

## 10. Evidence gates and calendar

| Gate | Evidence, not elapsed ceremony | Earliest target |
|---|---|---|
| Contract | ADR/plan/protocol/issues agree; routes/readbacks/grid pinned | Day 0 |
| Vertical software | Recent-DB happy path + fault matrix + frozen outcome fixture | Days 1–3 |
| One initial image/capability release | CI, Zot digest, main pin, Argo Synced+Healthy, rollback | Days 2–4 |
| Shadow | >=12 h and >=1 scheduled boundary: choice/state hash/receipt/outcome, zero experiment writes (target 24 h) | Days 3–5 |
| Probe authorization + physical readiness | #641 probe approval → #424/#433 evidence → #641 combined signoff | In parallel |
| Canaries | Baseline↔moderate/aggressive, exact recovery | Days 4–6 |
| A/A | Two real boundaries / 48 h baseline equality | Days 5–8 |
| Randomized day 1 | Frozen design and justified sample, finalized randomization receipt, #642 approval, confirmed exposure | Target days 5–8 |
| Causal result | All precommitted assigned days, frozen analysis/reveal | Target 30 days after start; longer only if fixed before draw |

The target slips only on failed evidence, not because a fixed soak calendar says
to wait. No lane may waive a failing gate to protect the target date.

## 11. Definitions of done

**Shadow started:** the deployed revision creates the expected non-actuating
selection, stable state-content hash, observation receipt and outcome-preview
records with zero experiment device calls. This is not the causal experiment.

**Experiment started:** the first randomized assignment is active; exact raw
readbacks confirm its expected complete state; a valid exposure is open; the
deployed revision is Synced + Healthy; routine greenhouse health is green; and
the tested confirmed baseline rollback remains available.

**Experiment completed:** all scheduled assignments remain in the ledger; the
device is confirmed at baseline; exposures are closed; outcomes, deviations and
fidelity artifacts are frozen and hashed; integrity gates pass or truthfully
classify failure; the 256-bit secret is revealed once and reproduces the
schedule/mapping; the frozen analyzer result and claim limits are published;
and all GitHub issues reflect deployed evidence.

## 12. GitHub source of execution truth

| Issue | Fast-path disposition |
|---|---|
| #581 | Program/outer-loop epic; two-track fast experiment vs deferred platform. |
| #583 | Minimal additive provenance, v2 lifecycle/phase/admission state machine and bounded integrity roles are P0. |
| #584 | Superseded by #639's executable confirmed-component lane. |
| #586 | Deferred platform-v2 firmware/OTA work; not a fast-path gate. |
| #587 | P0 coarse kill switch, lifecycle, safety/integrity observability and release surface; twin/UI polish deferred. |
| #588 | P0 daily selector identity, v2 two-step lock, power/sample size, schedule/commitment and analyzer lock. |
| #606 | Twin build profile backlog; not a fast-path gate. |
| #638 | Deferred generalized manifest/vector/content-identity platform work. |
| #639 | P0 once-daily executor, exclusive legacy-component activation and derived truth. |
| #640 | P0 frozen outcome/export and completion/reveal integrity. |
| #643 | Deferred platform-wide role/credential split; does not defer #583's minimum integrity roles. |
| #424 | Direct raw-band semantic proof before physical activation. |
| #433 | Sole-writer truth and non-starvation before physical activation. |
| #428 | Monitor existing heap/reboot risk; no new-firmware gate. |
| #641 | Multidisciplinary readiness manifest; scoped probe approval, then combined physical signoff. |
| #642 | Integrated release, shadow, canaries, A/A and separate randomized-day-1 approval. |

The ready-to-run outer-loop prompt is
[`docs/handoff/planner-experiment-codex-ultra-2026-08-23.md`](../handoff/planner-experiment-codex-ultra-2026-08-23.md).
