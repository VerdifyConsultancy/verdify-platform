# Deterministic forecast comparator preparation v1

Campaign #775, C8 #786. Prepared 2026-10-02 from source `f5c703ef`.
Status: **unregistered design preparation; no final go/no-go, policy lock or
actuation authority**. This document changes no current pilot, firmware,
assignments, profile, outcome or runtime. A later instantiated contract is a
new version with immutable input/code/environment hashes; preserve this one.

## Decision and scope

#780's forecast-truth correction is delivered. #785's genuine pilot readout
is unavailable. The present disposition is **prepare only**: a deterministic
forecast-aware selector is a possible next comparison, not an automatic next
trial. Final disposition may be `no_build`, `repair_measurement`,
`inconclusive_retest` or `prospective_comparison`, with its rationale linked
to the completed #785 decision. Do not infer no effect from low power.

The prospective question is whether an AI daily admission decision improves
the prespecified measured endpoint relative to a small deterministic rule
using the **same as-of information and confirmed delivery mechanism**.
A third persistence/baseline policy may be an offline diagnostic; it is not an
extra treatment arm without a new powered design. Offline command agreement,
forecast accuracy or simulated workload cannot establish physical climate or
resource effects. Whole-resource/heating economics remains separately owned
by #787; its delivered preparation is not duplicated here.

## Immutable input and matched-surface contract

The future lock must supply all entries below. Missing entries stay null with
a named reason; no synthetic climate, prospective assignment or forecast
fills absent pilot outcomes.

| Contract | Required exact identity and evidence |
|---|---|
| Prior decision | #785 report/export/analyzer hashes, one-way reveal receipt, denominator, uncertainty, safety/deviation and scope disposition; unavailable today |
| Training corpus | Named pretrial interval or completed-pilot partition; extraction SQL/source hash, original export bytes, MVCC snapshot/capture time, units, temporal support, missingness and exclusion reasons |
| Validation split | Frozen chronological/season/event holdout with explicit cutoffs; no random-row split leaking neighboring weather episodes or outcome windows |
| Information set | Identical as-of forecast/provider vintages and observed covariates available to both arms at the daily decision; hash input availability/source clocks and any bias-prior version |
| Firmware and delivery | Same exact controller binary/version/control contract, approved profile manifest/grid/full48 baseline, sole executor image/config, lease/generation fencing, source confirmations and recovery semantics |
| Crop and measurements | Same crop assignments, fixed target revision and sensor/contributor provenance/physical validity; every unsupported/absent route remains unavailable |
| Endpoint and analysis | Same fixed window/units/coverage/uncertainty/missingness/ITT denominator, localized safety, analyzer revision/environment and benefit endpoint; no credit-as-physical-compliance substitution |
| Calendar and authority | New study identity, suitable realized season/opportunity, UTC offset/DST policy, locked start, pair count and fresh draw/commitment; all unresolved today |

No current source or firmware identity is silently installed as a future
study identity. Current C1 qualification is still incomplete; future consumer
bindings must derive from the actually qualified runtime and original native
callbacks. A later safety repair is an immutable deviation handled under the
locked protocol, not a change silently mixed into an arm.

## Forecast provenance and temporal causality

For every provider datum retain provider/model/location/units, `issued_at`,
`fetched_at` (Verdify availability), valid time or window bounds, lead bucket,
source row identity/revision and extraction snapshot. At decision time `t`,
both `issued_at <= t` and `fetched_at <= t` are required. A later vintage or
later bias correction cannot retroactively improve either arm's information.
Deduplicate by the #780 contract; conflicting, stale, missing or unsupported
windows yield explicit unavailable features. Do not silently interpolate or
select a post-decision vintage.

Freeze whether each forecast quantity is instantaneous or a preceding-hour
window and how it contributes to the decision window. Verify temperature/RH
units and derive outdoor VPD using the versioned outdoor-truth formula.
Forecast verification uses observed **outdoor** truth; indoor greenhouse
response is a separate outcome. Correct outdoor forecasts with different
indoor VPD must not incur provider error.

The accepted #780 receipt notes that `fetched_at` is availability, while
historical observation arrival time was not recorded. Such a corpus can
validate issued/available forecast selection and outdoor error; it cannot
prove that an unrecorded observation was available to a historical selector.
Exclude it from as-of feature credit or label that feature unavailable.
Prospective collection must retain actual availability clocks. Neither an
original forecast nor that limitation is a 60-day measured outcome.

## Small deterministic rule family to instantiate before a new draw

Use a single once-per-local-day choice from the **same approved**
`baseline|moderate|aggressive` profiles available to the AI arm. Both arms use
the same daily decision time, cadence, expiry, confirmed component setters,
full48 confirmation, fallback and deterministic safety constraints. The rule
cannot create tunables, change crop corridors, schedule a second writer or
bypass a cap. Failed execution remains assigned-arm ITT with fallback, not a
missing “unfavorable” day.

The candidate family has two forecast-only weather-opportunity indices:
valid-window outdoor VPD and solar irradiance, aggregated by explicitly
locked physical duration weights. These describe weather, **not predicted
indoor crop compliance**. Exact lead window, aggregation, coverage floor,
age bound, outdoor threshold units and two ordered decision thresholds are
unresolved until the pretrial data audit. No numeric threshold is inferred
from a missing resource meter, historical favorable chart or revealed arm.

Before parameter selection, freeze a finite candidate grid and selection
objective using only the permitted training partition. Document every
candidate, tie-break (prefer less aggressive admission), support bounds and
chronological holdout. If crop/season support or reliable forecast features
are absent, choose `baseline` with a precise reason. For available supported
features, a frozen score below the lower threshold selects `baseline`,
between thresholds `moderate`, and at/above the upper threshold `aggressive`.
Exact equality conventions and any veto must be represented in the final
rule source and goldens. Safety veto/fallback always dominates admission.

Do not tune thresholds to a revealed current-pilot arm effect. A completed
pilot may inform the later design only through explicitly named partitions
and the already published #785 decision; preserve a separate held-out
validation interval. Rule selection is not a response-model/MPC experiment
and cannot claim unseen actuator policies have proven physical effects.

Final rule evidence must include deterministic replay of identical inputs,
missing/stale/conflicting/future-vintage refusal, threshold boundaries,
unsupported weather fallback, and unchanged guardrail/confirmation behavior.
These are future implementation checks, not additional gates created by this
preparation. The final source/parameters may not adapt during randomized days.

## Carryover, missingness and prospective power

Use realized pretrial/eligible completed-pilot **18-hour** climate/equipment
windows with fixed sensor/target versions, paired covariance, autocorrelation,
forecast opportunity, admission/delivery dilution and weather/season support.
Estimate response/carryover from real histories, retaining confounders and
incomplete episodes; command replay alone cannot estimate physical carryover.
The current warm pilot's 30 pairs/60 local days and modeled joint power
0.13776 are not a sample-size or power calculation for this new comparison.

Prespecify whether the claim is climate noninferiority plus a measured benefit
or climate improvement plus benefit. Freeze effect sizes, margins, covariance,
joint success/multiplicity and sample-size-selection rule before drawing.
Run the existing paired/joint-power machinery only when genuine eligible
inputs exist; publish sensitivity to carryover, missingness and dilution.
The current `v2_power.py` fixes three endpoint names and noninferiority/benefit
boundaries. It is usable only for that exact estimand; a different improvement
or measured-resource claim needs a separately versioned calculation, not
relabeled output from the existing simulator. No calculation is run here.
Sample size, source-faithful joint power and calendar remain null today.
A deterministic selector is not grounds to change the current pilot lock.

Choose washout/exclusion and pair spacing from measured carryover before lock.
Do not automatically inherit six excluded hours or assume an arbitrary
settling period removes dependence. Record previous-day assignment, rescue,
full-state delivery/exposure and equipment response. A valid new design may
retain the existing daily cadence only if its empirical carryover contract
supports that choice.

Every assigned day remains an ITT row, including refusal, outage, fallback,
rescue and null endpoints. Freeze daily completeness, maximum gap, source
freshness, eligibility denominators and null reasons. Freeze missingness
bounds and per-protocol sensitivity before reveal; no posthoc replacement,
selective deletion, denominator revision or imputation upgrades an unmeasured
resource. If the retained rule lacks required pairs, the final result may be
inconclusive rather than silently becoming a complete-case trial.

## Inference cost and claim limits

Record actual AI provider/model/call/time/token/usage and dated price/currency
only where the arms differ. The deterministic rule has no model calls; shared
operational calls belong to both arms and are not incremental treatment cost.
Absent usage or tariff yields null cost. Record latency and failed calls as
operational outcomes. Monetary inference cost does not prove whole-platform
energy consumption, and simulated/runtime-derived equipment burden is not
metered water/electricity/gas savings. Retain the #781 narrow endpoint decision
and #787 whole-resource boundary. No yield, profit or other-season efficacy
claim follows from this comparison.

## Final disposition and separate prospective authority

A completed decision record must link #785, #780 and the locked input/source
hashes, then select one of:

- **No build:** unreliable measurements, unacceptable safety/integrity or no
  justified next question; preserve adverse/no-benefit results and stop.
- **Repair measurement:** name the concrete missing provenance/endpoint;
  repair only that owning surface and do not randomize meanwhile.
- **Inconclusive retest:** state why uncertainty remains and what empirical
  design changes address it; do not reinterpret the original null result.
- **Prospective comparison:** name the supported claim, frozen comparator,
  matched surfaces, eligible inputs and source-faithful power; establish a
  distinct protocol/lock/draw/authority before any new treatment execution.

Never reuse a revealed schedule or shift/reorder/replace its days. No draw,
freeze, experiment registration, provider call, runtime enable or device write
is performed by this document. #786 remains open: preparation does not satisfy
its #785-linked final go/no-go or actual later trial acceptance. #14/#638/#586
remain explicitly separate deferred future platforms, not hidden dependencies
requiring their enablement for this simple confirmed-component comparator.

## Existing evidence and preparation input byte identities

- [#786 accepted scope](https://github.com/VerdifyConsultancy/verdify-platform/issues/786).
- [#780 outdoor-truth delivery and historical limits](https://github.com/VerdifyConsultancy/verdify-platform/issues/780#issuecomment-5857209033).
- [#785 uncompleted genuine readout contract](https://github.com/VerdifyConsultancy/verdify-platform/issues/785).
- [#781 accepted narrow endpoint/null resource limits](https://github.com/VerdifyConsultancy/verdify-platform/issues/781#issuecomment-5856986403).
- [#787 distinct delivered whole-resource preparation](https://github.com/VerdifyConsultancy/verdify-platform/issues/787#issuecomment-5917099536).
- [Current campaign follow-through](../../../planning/CAMPAIGN.md),
  [deferred platform disposition](../../../docs/audits/work-pending-2026-08-29.md).

All paths below are repository-relative. Hashes identify existing preparation
inputs, not fresh measurements or qualified future runtime. Recompute before
reuse; preserve originals and supersede with a new version on any input change.

| Source input | SHA256 |
|---|---|
| `research/planner-efficacy/protocols/direct-launch-basis-v2.json` | `074d7ef29415eea922d59b3ab1596612c0a7966624011b1c29bf477d50964835` |
| `research/planner-efficacy/protocols/resource-endpoint-v1.json` | `d65ce69a6c909d0dfd7bae4b4c6f51bc6a875c82c7c0d12fc69d6b0d9d250bc1` |
| `research/planner-efficacy/protocols/warm-season-calendar-prereg-2027-v1.json` | `f63dcca480c5413fcd66480e81eb5afb18f4f8395885b4392975cd4eca6d1301` |
| `research/planner-efficacy/protocols/whole-resource-study-preparation-v1.md` | `0bd6be3a78ee49e808e74a4aa56cac31b51dba71802144ed2d203ad8538e8a8f` |
| `research/planner-efficacy/switchback/v2_power.py` | `fa39a9157da04a15b899dadea66a4d82b7a365521cf1c48dadede7e8c0c11921` |
| `research/planner-efficacy/switchback/v2_selector.py` | `e8d194ceb3763e5549e832dd4c4fd6655716c42f7ba9e4978ea6a3901f04b431` |
| `research/planner-efficacy/forecast_replay.py` | `7878f93017053635036b1a591a50bdd451b2cacb388031c505639e6709d6c10b` |
| `db/migrations/242-outdoor-forecast-verification.sql` | `80de73d174fc69c1921a362392147e271142d6357481172c07b9b3a6dde226fc` |
