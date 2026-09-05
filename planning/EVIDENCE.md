# Evidence basis and limits — September 5, 2026

This is the basis for the campaign, not a new live-health receipt. Analysis collected public data around 15:07–15:17 UTC and inspected GitHub main `963ea818aad09b02259509cffa6bfdafb48d1702`. The original checkout was 172 commits behind; planning uses an isolated current-main branch and preserves the original dirty tree.

## Observed facts that change the plan

| Finding | Evidence and interpretation | Owning work |
|---|---|---|
| September 4 hot/dry interruption | Five-minute peak 100.48°F / 5.395 kPa at 15:30 Denver; fog/center mist/recorded flow zero 15:00–18:00 with fan/vent states active. Cause unknown; flat 600 and 157.38 counters have different semantics. | New incident timeline; #749 safety disposition |
| Score semantics | September 4 attributable grade 85.8 versus legacy joint in-band 6.1%; migrations 203/206 and API aliases conflate meanings. | #371 |
| Band lineage | Migration 119 labels latest setpoint_changes as fw_* without confirmed filtering and uses latest readback without bounded freshness. Reporting mismatch is not proof of actual consumed bands. | #424 |
| Sensor break | South last non-null VPD in the retained August 14 hourly archive: August 2, 16:00 Denver. Removing south from preceding four-probe hours adds 0.091 kPa mean VPD, 0.190 at midday; not a nonlinear matched-effect correction. | Fixed-panel reanalysis |
| Water eligibility | August 14–September 4: 5,047 accepted gallons = 1,361 attributed + 3,465 ambiguous + 221 manual/unattributed; 13/22 eligible water days, 3/7 latest. | Resource contract |
| Electricity and absent claims | Same 22 days: 124.384 kWh measured on two channels; modeled 342.025 kWh ineligible throughout. Gas/cost and interior DLI do not provide eligible causal endpoints; resource score weight is zero on all 56 days. | Resource contract and future economics |
| Forecast reference | Migration 101 compares outdoor forecast VPD with indoor VPD; historical 0–6h bias +1.453 kPa versus +0.539 against outdoor truth. | Outdoor forecast repair |
| Existing implementation | Current source has component delivery/receipts, v2 outcome/analyzer/randomization and migrations through 240; 216 research and 204 focused experiment tests passed. These were unit/source tests, not a fresh real restore or physical proof. | Reuse #639/#587; new vertical qualification |
| Current public/runtime observation | Planner/data health good; no current missed-required/overdue/required-failure count in its observed window. Ready core and experiment workers. Component off, active ID empty, vector off, legacy writes enabled, MCP enforce verified. Scheduled backup Job completed September 5 08:18 UTC. | Rebaseline #747/#750, do not replay old pins |
| Unavailable private evidence | Authenticated component-status returned 403; current private ledger counts and Argo Synced/Healthy were not accessible. August 31 receipts remain historical only. Backup Job completion is not restore proof. | Fresh authorized packet #749 |
| Study mismatch | Current source Luna/medium; 30-pair direct basis accepts power 0.13776 and starts November 2. Treatment is cooling/wetting, not heating. Power assumptions need exact-window/admission/completeness replay. | Scientific design and #588 |

The 56-day descriptive comparison is not randomized: July 11–August 13 attributable mean 65.94 versus August 14–September 4 mean 65.49. Temperature/VPD graded means improve, but weather, contributing sensors, targets and delivery change. Recent 29 complete hourly days have 41,549/41,760 nominal minute samples by row count (99.49%), which is not proof of unique-minute or fixed-panel completeness. House averages also hide substantially higher midday north VPD.

The favorable stale-policy historical association (0.3079 kPa extra VPD distance and 42.1% more nine-stream runtime) used only 93/384 matched bins across four sequential days and shares a delivery outage plus sensor-composition break. It remains hypothesis-generating. The PID open-loop runtime comparison cannot estimate closed-loop climate/resource effects because both fitted response models failed their validation gates.

## Reproducibility and source anchors

The review retained 125 successful hash-verified public payloads, analysis scripts and detailed report under `/home/agent/reports/verdify-review-2026-09-05/`. Compact computed results and collection manifests are preserved in [evidence](evidence/); the manifests identify the immutable hashes of the retained raw extraction. Re-fetching a public rollup later is not guaranteed to reproduce its historical materialization. A future implementation must obtain the exact raw snapshot or explicitly create a new extraction/version, never imply these summary files contain all raw data.

Repository anchors:

- [Historical efficacy audit](../docs/research/planner-efficacy-audit-2026-08-14.md) and [current-firmware study](../docs/research/planner-efficacy-current-firmware-2026-08-14.md).
- [August 23 contract audit](../docs/research/planner-experiment-resumption-audit-2026-08-23.md), historical only where later source supersedes it.
- [Direct-launch basis](../research/planner-efficacy/protocols/direct-launch-basis-v1.json), [power artifact](../research/planner-efficacy/protocols/planner-switchback-v2-power.json) and [outcome implementation](../research/planner-efficacy/switchback/v2_outcomes.py).
- [Original issue snapshot](archive/2026-09-05/issues.json) and [original native graph](archive/2026-09-05/graph.json). No comments, closed issues or prior receipts are rewritten by the planning sprint.

Source paths in every planned issue are validated to exist. Runtime state, applied migrations, source/digest attestations, current backups, physical conditions and authorization must be freshly established at execution time.
