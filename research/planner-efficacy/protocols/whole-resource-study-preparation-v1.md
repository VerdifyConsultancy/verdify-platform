# Whole-resource and overnight study preparation v1

Campaign #775, C8 #787. Prepared 2026-09-30 from source `1c1e3107`.
This is an unregistered study specification. Its start date, sample size,
meter commissioning, tariff inputs and power are unresolved. It authorizes
no hardware purchase, controller change, new random draw or efficacy claim.
The current hot/dry pilot remains governed by its separate frozen contract.

## Present decision

The eligible whole-resource endpoint is **unavailable**. The existing partial
two-channel electricity and shared-supply water observations cannot establish
whole-greenhouse savings. Gas, calibration uncertainty and resource cost are
null in the frozen endpoint receipt. Keep #787 open. Preparation can proceed;
study registration requires the measured boundary and empirical design below.

| Proposed endpoint | Measurement required | Current evidence limit |
|---|---|---|
| Whole greenhouse electricity, kWh | Installed circuit inventory; individually mapped import/export channels covering every claimed load; reference calibration; per-channel physical sample clock, availability and gap boundaries | Existing scope is `partial_shelly_two_channels`; circuit map, clock validation and calibration evidence are null |
| Heating fuel, therms or measured energy equivalent | Named meter and installed heater boundary; dated unit/conversion and calibration; raw counter/reset epochs; separation from unrelated household consumption | Frozen gas endpoint is null; neither heater-on minutes nor nameplate consumption is a fuel measurement |
| Climate wetting water, gallons | Calibrated meter/flow boundary and authenticated counter epochs; raw-delta conservation; nonoverlapping equipment attribution | Shared supply contains ambiguous and manual/unattributed volume; source attribution alone does not establish commissioning |
| Crop irrigation and fertigation, gallons | Separate measured or independently validated attribution boundaries, reset epochs and manual volume dispositions | Reported historical zero attribution does not prove physical absence |
| Variable resource cost, USD | Each eligible measured quantity paired with named currency, units, effective tariff dates, tier/time rules and uncertain inputs | Missing quantity or price yields null cost; it is never treated as free |
| Incremental computing cost | Calls, tokens and dated provider prices that actually differ between policies; separately metered compute if a physical energy claim includes it | Shared virtual inference is not incremental policy energy or cost |

Vent-open duration remains an equipment-state diagnostic. Motor travel energy
requires its own measured circuit and travel interval. Do not combine unlike
electricity scopes, sum runtime-model estimates with meter totals, reallocate
ambiguous water, or use a modeled range as calibration uncertainty.

## Prospective boundary and design

1. Retain a versioned installed-boundary manifest for each resource: meter ID,
   circuit/plumbing map, reference procedure and result, validity interval,
   signed units, clock evidence, reset identity and uncertainty model. A missing
   item is an explicit unavailable reason. Source inspection cannot infer a
   physical installation. Commission only a measurement needed for the stated
   claim; the current task includes no purchase or gas adjustment.
2. Define heating/overnight observation windows by the fixed solar calculation,
   location and timezone, with explicit crossing-midnight allocation and 23/25
   hour DST cases. Select a suitable cold-season calendar using realized
   outdoor and heating-opportunity data. No date or fixed 60-day duration is
   assigned to this separate study by the hot/dry pilot.
3. Freeze the crop-target and sensor-contributor contracts, firmware, bounded
   policies, safety handling and carryover policy. Examine realized solar-night
   dry-out under #410. Predeclare safety interventions and retain affected
   assigned days; do not silently compare different control surfaces.
4. Choose whether the question is climate noninferiority plus measured resource
   benefit, or climate improvement plus benefit. The current hot/dry margins
   do not establish improvement. Predeclare a joint success rule, effect sizes,
   endpoint families and multiplicity handling. Lower costs alone do not prove
   less water, electricity and gas individually; report each measured resource.
5. Use eligible pretrial or completed-pilot data for paired variance/covariance,
   overnight carryover, seasonal opportunity, missingness and admission rates.
   Freeze the empirical extraction and power calculation before selecting
   pairs, sample size or drawing a schedule. The hot/dry modeled 0.13776 joint
   power is not power for heating or economic outcomes.
6. Every resource endpoint needs its own declared coverage denominator, gap and
   uncertainty criteria for the actual observation window. Retain every
   assigned day, including a null resource endpoint. Publish complete matched
   contrasts with their denominator and the predeclared missingness sensitivity;
   do not describe a complete-case resource contrast as a complete ITT result.
   No model imputation upgrades an unmeasured endpoint.

The winter feasibility observer measures data yield and opportunity without
treatment assignment. It is useful design input, but does not demonstrate a
heating-policy effect. Unlike seasons require separately identified estimates;
concatenation into a 300-day efficacy claim is not permitted.

## Completion evidence

#787 needs the #781 measured endpoint and #410 realized overnight evidence,
plus the completed #785 pilot decision. A prospective follow-on study has its
own immutable protocol, source and input hashes, power result, lock, draw and
authority. No revealed schedule is reused. Publish a no-build, inconclusive or
negative decision when warranted. Keep measured commissioning, prospective
design and eventual physical outcomes separate in every receipt.

## Input byte identities

All paths are relative to repository root. These are source artifacts and
historical projections, not a live measurement refresh.

| Input | SHA256 |
|---|---|
| `research/planner-efficacy/protocols/resource-endpoint-v1.json` | `d65ce69a6c909d0dfd7bae4b4c6f51bc6a875c82c7c0d12fc69d6b0d9d250bc1` |
| `research/planner-efficacy/protocols/resource-endpoint-2026-08-14_2026-09-05-v1.receipt.json` | `17a914a53d39defcc82892a0a4a8d246a46f78820e1532fea3dfe9a73972e159` |
| `research/planner-efficacy/resources-2026-08-14_2026-09-05.baseline.json` | `aa7ef5097efc3a44e6dd15e7ce851fba872af8b3792ab7faff93cf70e8f7d6a3` |
| `research/planner-efficacy/protocols/direct-launch-basis-v2.json` | `074d7ef29415eea922d59b3ab1596612c0a7966624011b1c29bf477d50964835` |

Recompute these hashes before reuse. A later endpoint or design is a new
version; preserve this preparation and all original inputs.
