# Ownership and issue pull order

## Current execution plan

Reviewed **2026-10-19T03:57:19.989175+00:00** against source `af46d263352d6403201a4642378197e9ca00303c`. [planning/reviews/2026-10-18-vision-recovery/README.md](planning/reviews/2026-10-18-vision-recovery/README.md) records current source, GitHub, runtime and evidence limits. All **48 open issues** have one allocation and accountable role. Planning capacity: **40 engineering hours/week**; ranges are estimates, not calendar promises.

| Pull set | Kind | Allocated hours | Capacity hours | Issues |
|---|---|---|---|---|
| S3B — Unblocked source and diagnosis (October 19–23, 2026) | sprint | 22–34 | 40 | [#410](https://github.com/VerdifyConsultancy/verdify-platform/issues/410), [#428](https://github.com/VerdifyConsultancy/verdify-platform/issues/428), [#639](https://github.com/VerdifyConsultancy/verdify-platform/issues/639) |
| WAIT — Await authentic inputs or owner qualification | conditional | Unestimated | Not timeboxed | [#587](https://github.com/VerdifyConsultancy/verdify-platform/issues/587), [#749](https://github.com/VerdifyConsultancy/verdify-platform/issues/749), [#782](https://github.com/VerdifyConsultancy/verdify-platform/issues/782), [#783](https://github.com/VerdifyConsultancy/verdify-platform/issues/783), [#981](https://github.com/VerdifyConsultancy/verdify-platform/issues/981), [#988](https://github.com/VerdifyConsultancy/verdify-platform/issues/988) |
| S3 — Prior S3 qualification scope — retained context | conditional | 0 | Not timeboxed | None |
| ALTERNATE — Alternate pull when an S3 packet is blocked | conditional | Unestimated | Not timeboxed | [#368](https://github.com/VerdifyConsultancy/verdify-platform/issues/368) |
| S4 — Integrated qualification and physical proof | conditional | Unestimated | Not timeboxed | [#641](https://github.com/VerdifyConsultancy/verdify-platform/issues/641) |
| S5 — Final identity lock, launch and first assigned outcome | conditional | Unestimated | Not timeboxed | [#588](https://github.com/VerdifyConsultancy/verdify-platform/issues/588), [#640](https://github.com/VerdifyConsultancy/verdify-platform/issues/640), [#642](https://github.com/VerdifyConsultancy/verdify-platform/issues/642) |
| PILOT — Pilot operation and readout | ongoing | Unestimated | Not timeboxed | [#784](https://github.com/VerdifyConsultancy/verdify-platform/issues/784), [#785](https://github.com/VerdifyConsultancy/verdify-platform/issues/785) |
| RELEASE — Firmware and lighting release acceptance | conditional | Unestimated | Not timeboxed | [#386](https://github.com/VerdifyConsultancy/verdify-platform/issues/386), [#390](https://github.com/VerdifyConsultancy/verdify-platform/issues/390), [#963](https://github.com/VerdifyConsultancy/verdify-platform/issues/963) |
| CONTROL — Control and irrigation backlog | conditional | Unestimated | Not timeboxed | [#297](https://github.com/VerdifyConsultancy/verdify-platform/issues/297), [#299](https://github.com/VerdifyConsultancy/verdify-platform/issues/299), [#367](https://github.com/VerdifyConsultancy/verdify-platform/issues/367), [#369](https://github.com/VerdifyConsultancy/verdify-platform/issues/369), [#370](https://github.com/VerdifyConsultancy/verdify-platform/issues/370), [#434](https://github.com/VerdifyConsultancy/verdify-platform/issues/434) |
| PHYSICAL — Operator and seasonal commissioning | conditional | Unestimated | Not timeboxed | [#45](https://github.com/VerdifyConsultancy/verdify-platform/issues/45), [#51](https://github.com/VerdifyConsultancy/verdify-platform/issues/51), [#52](https://github.com/VerdifyConsultancy/verdify-platform/issues/52), [#298](https://github.com/VerdifyConsultancy/verdify-platform/issues/298), [#398](https://github.com/VerdifyConsultancy/verdify-platform/issues/398), [#412](https://github.com/VerdifyConsultancy/verdify-platform/issues/412), [#751](https://github.com/VerdifyConsultancy/verdify-platform/issues/751) |
| FUTURE — Conditional future decisions and comparisons | future | Unestimated | Not timeboxed | [#14](https://github.com/VerdifyConsultancy/verdify-platform/issues/14), [#31](https://github.com/VerdifyConsultancy/verdify-platform/issues/31), [#245](https://github.com/VerdifyConsultancy/verdify-platform/issues/245), [#378](https://github.com/VerdifyConsultancy/verdify-platform/issues/378), [#379](https://github.com/VerdifyConsultancy/verdify-platform/issues/379), [#786](https://github.com/VerdifyConsultancy/verdify-platform/issues/786), [#787](https://github.com/VerdifyConsultancy/verdify-platform/issues/787) |
| ROLLUP — Program ownership | rollup | Unestimated | Not timeboxed | [#16](https://github.com/VerdifyConsultancy/verdify-platform/issues/16), [#75](https://github.com/VerdifyConsultancy/verdify-platform/issues/75), [#218](https://github.com/VerdifyConsultancy/verdify-platform/issues/218), [#350](https://github.com/VerdifyConsultancy/verdify-platform/issues/350), [#359](https://github.com/VerdifyConsultancy/verdify-platform/issues/359), [#430](https://github.com/VerdifyConsultancy/verdify-platform/issues/430), [#581](https://github.com/VerdifyConsultancy/verdify-platform/issues/581), [#750](https://github.com/VerdifyConsultancy/verdify-platform/issues/750), [#775](https://github.com/VerdifyConsultancy/verdify-platform/issues/775) |

Sprint allocations may cover bounded preparation. Full issue acceptance still depends on its predecessors, current evidence and declared physical or seasonal conditions. C0–C8 remain stage contracts; closed historical issues remain visible in the stage views below.

## Campaign stage contracts

Generated from [planning/backlog.yaml](planning/backlog.yaml). Edit the source and run `python -m planning.render`; do not maintain a competing roadmap.

The [campaign strategy](planning/CAMPAIGN.md) defines scope, release bundles, evidence gates and deferred work. Historical June/August plans are [archived](planning/archive/2026-09-05/README.md), not execution instructions.

One accountable role per issue; preserve existing assignees and choose an execution owner when pulling work. Serialize migrations and writer/release mutations even when analysis and device-denied tests run concurrently.

## C0 — Evidence/data lead

| Issue / state | Outcome | Predecessor records / state | Effort |
|---|---|---|---|
| [#775](https://github.com/VerdifyConsultancy/verdify-platform/issues/775) (open) | Campaign: trustworthy greenhouse outcomes → qualified pilot → evidence-backed control | None; see concrete issue conditions | L |
| [#778](https://github.com/VerdifyConsultancy/verdify-platform/issues/778) (closed) | Investigate September 4 hot/dry peak and three-hour wetting interruption | None; see concrete issue conditions | L |
| [#371](https://github.com/VerdifyConsultancy/verdify-platform/issues/371) (closed) | Repair climate score semantics and publish physical outcomes separately from credit | None; see concrete issue conditions | L |
| [#424](https://github.com/VerdifyConsultancy/verdify-platform/issues/424) (closed) | Resolve served, consumed and raw-readback band lineage without fabricated device truth | None; see concrete issue conditions | L |
| [#779](https://github.com/VerdifyConsultancy/verdify-platform/issues/779) (closed) | Re-run historical planner comparisons on a fixed sensor panel and fixed target contract | [#371](https://github.com/VerdifyConsultancy/verdify-platform/issues/371) (closed) | L |
| [#780](https://github.com/VerdifyConsultancy/verdify-platform/issues/780) (closed) | Score as-of outdoor forecasts against outdoor truth and separate indoor response | None; see concrete issue conditions | M |
| [#781](https://github.com/VerdifyConsultancy/verdify-platform/issues/781) (closed) | Commission a claim-safe water and electricity endpoint contract | None; see concrete issue conditions | L |
| [#782](https://github.com/VerdifyConsultancy/verdify-platform/issues/782) (open) | Finish the empirical inputs and frozen exploratory scientific contract | [#371](https://github.com/VerdifyConsultancy/verdify-platform/issues/371) (closed), [#779](https://github.com/VerdifyConsultancy/verdify-platform/issues/779) (closed), [#780](https://github.com/VerdifyConsultancy/verdify-platform/issues/780) (closed), [#781](https://github.com/VerdifyConsultancy/verdify-platform/issues/781) (closed) | L |

## C1 — Runtime/release lead

| Issue / state | Outcome | Predecessor records / state | Effort |
|---|---|---|---|
| [#750](https://github.com/VerdifyConsultancy/verdify-platform/issues/750) (open) | Recovery/proof campaign: rebaseline current state and seal one safe physical receipt | None; see concrete issue conditions | L |
| [#747](https://github.com/VerdifyConsultancy/verdify-platform/issues/747) (closed) | Verify corrected scheduled backups and current production recovery health | None; see concrete issue conditions | S |
| [#749](https://github.com/VerdifyConsultancy/verdify-platform/issues/749) (open) | Qualify current three-probe readiness and explicit safety dependencies | [#424](https://github.com/VerdifyConsultancy/verdify-platform/issues/424) (closed), [#778](https://github.com/VerdifyConsultancy/verdify-platform/issues/778) (closed), [#949](https://github.com/VerdifyConsultancy/verdify-platform/issues/949) (closed) | M |
| [#641](https://github.com/VerdifyConsultancy/verdify-platform/issues/641) (open) | Complete one fresh attended physical baseline–target–baseline proof | [#749](https://github.com/VerdifyConsultancy/verdify-platform/issues/749) (open), [#639](https://github.com/VerdifyConsultancy/verdify-platform/issues/639) (open), [#587](https://github.com/VerdifyConsultancy/verdify-platform/issues/587) (open), [#747](https://github.com/VerdifyConsultancy/verdify-platform/issues/747) (closed) | L |
| [#639](https://github.com/VerdifyConsultancy/verdify-platform/issues/639) (open) | Qualify the component executor, exclusivity and recovery behavior in containment | None; see concrete issue conditions | L |
| [#587](https://github.com/VerdifyConsultancy/verdify-platform/issues/587) (open) | Qualify lifecycle authority and blinded monitoring in containment | None; see concrete issue conditions | M |
| [#783](https://github.com/VerdifyConsultancy/verdify-platform/issues/783) (open) | Qualify the complete current-login selector-to-frozen-analysis path | [#639](https://github.com/VerdifyConsultancy/verdify-platform/issues/639) (open), [#587](https://github.com/VerdifyConsultancy/verdify-platform/issues/587) (open) | L |

## C2 — Research/release lead

| Issue / state | Outcome | Predecessor records / state | Effort |
|---|---|---|---|
| [#581](https://github.com/VerdifyConsultancy/verdify-platform/issues/581) (open) | Complete the bounded AI-admission pilot and publish a decision | None; see concrete issue conditions | L |
| [#588](https://github.com/VerdifyConsultancy/verdify-platform/issues/588) (open) | Bind exact scientific/runtime identity and finalize one internal draw | [#641](https://github.com/VerdifyConsultancy/verdify-platform/issues/641) (open), [#782](https://github.com/VerdifyConsultancy/verdify-platform/issues/782) (open), [#783](https://github.com/VerdifyConsultancy/verdify-platform/issues/783) (open) | M |
| [#642](https://github.com/VerdifyConsultancy/verdify-platform/issues/642) (open) | Activate and verify one randomized day 1 from the locked pilot | [#588](https://github.com/VerdifyConsultancy/verdify-platform/issues/588) (open) | M |

## C3 — Research lead

| Issue / state | Outcome | Predecessor records / state | Effort |
|---|---|---|---|
| [#640](https://github.com/VerdifyConsultancy/verdify-platform/issues/640) (open) | Freeze and reconcile the first real assigned-day outcome and blinded export | [#642](https://github.com/VerdifyConsultancy/verdify-platform/issues/642) (open) | M |
| [#784](https://github.com/VerdifyConsultancy/verdify-platform/issues/784) (open) | Operate the full blinded assigned-day pilot with immutable custody | [#640](https://github.com/VerdifyConsultancy/verdify-platform/issues/640) (open) | L |
| [#785](https://github.com/VerdifyConsultancy/verdify-platform/issues/785) (open) | Reveal once, reproduce the frozen analysis and decide the program outcome | [#784](https://github.com/VerdifyConsultancy/verdify-platform/issues/784) (open) | M |

## C4 — Platform lead

| Issue / state | Outcome | Predecessor records / state | Effort |
|---|---|---|---|
| [#644](https://github.com/VerdifyConsultancy/verdify-platform/issues/644) (closed) | Integrate exact-SHA, path-aware and device-safe delivery receipts | [#964](https://github.com/VerdifyConsultancy/verdify-platform/issues/964) (closed) | L |
| [#322](https://github.com/VerdifyConsultancy/verdify-platform/issues/322) (closed) | Replace remaining VM-era tests with current k3s delivery contracts | None; see concrete issue conditions | M |
| [#304](https://github.com/VerdifyConsultancy/verdify-platform/issues/304) (closed) | Provide reproducible least-authority firmware and database diagnostic tooling | None; see concrete issue conditions | S |
| [#303](https://github.com/VerdifyConsultancy/verdify-platform/issues/303) (closed) | Run real portable ESPHome compilation for declared firmware targets | [#304](https://github.com/VerdifyConsultancy/verdify-platform/issues/304) (closed), [#322](https://github.com/VerdifyConsultancy/verdify-platform/issues/322) (closed) | L |
| [#390](https://github.com/VerdifyConsultancy/verdify-platform/issues/390) (open) | Gate firmware releases on topology, cycling, runtime safety and rollback evidence | [#303](https://github.com/VerdifyConsultancy/verdify-platform/issues/303) (closed), [#419](https://github.com/VerdifyConsultancy/verdify-platform/issues/419) (closed) | L |
| [#433](https://github.com/VerdifyConsultancy/verdify-platform/issues/433) (closed) | Finish truthful single-writer command lifecycle and no-reconcile-storm acceptance | None; see concrete issue conditions | L |
| [#427](https://github.com/VerdifyConsultancy/verdify-platform/issues/427) (closed) | Prove active Hermes/MCP recovery and truthful non-authoritative worker health | [#433](https://github.com/VerdifyConsultancy/verdify-platform/issues/433) (closed) | L |
| [#75](https://github.com/VerdifyConsultancy/verdify-platform/issues/75) (open) | Observability program: prove actionable health from source through device | None; see concrete issue conditions | L |
| [#563](https://github.com/VerdifyConsultancy/verdify-platform/issues/563) (closed) | Enforce the actual ConfigMap-based alert delivery contract | None; see concrete issue conditions | S |
| [#394](https://github.com/VerdifyConsultancy/verdify-platform/issues/394) (closed) | Deliver and fault-test live split-brain and no-writer alerts | [#563](https://github.com/VerdifyConsultancy/verdify-platform/issues/563) (closed) | M |
| [#89](https://github.com/VerdifyConsultancy/verdify-platform/issues/89) (closed) | Verify exact-source post-deploy smoke and device-route health | [#563](https://github.com/VerdifyConsultancy/verdify-platform/issues/563) (closed) | M |
| [#671](https://github.com/VerdifyConsultancy/verdify-platform/issues/671) (closed) | Make Lab publishing failures and cache contention observable | None; see concrete issue conditions | M |
| [#399](https://github.com/VerdifyConsultancy/verdify-platform/issues/399) (closed) | Prove the HA grow-light writer remains within the single-writer contract | None; see concrete issue conditions | S |
| [#419](https://github.com/VerdifyConsultancy/verdify-platform/issues/419) (closed) | Populate real outdoor freshness in replay and enforce branch coverage | None; see concrete issue conditions | M |
| [#386](https://github.com/VerdifyConsultancy/verdify-platform/issues/386) (open) | Prove grow-light minimum-on behavior at the solar-window boundary | [#303](https://github.com/VerdifyConsultancy/verdify-platform/issues/303) (closed) | M |
| [#949](https://github.com/VerdifyConsultancy/verdify-platform/issues/949) (closed) | Bound planner-policy drift holds and prove autonomous safe convergence | None; see concrete issue conditions | M |
| [#950](https://github.com/VerdifyConsultancy/verdify-platform/issues/950) (closed) | Restore the ingestor Slack credential-file contract and operator delivery | None; see concrete issue conditions | M |
| [#951](https://github.com/VerdifyConsultancy/verdify-platform/issues/951) (closed) | Restore authenticated greenhouse camera snapshots after connection refusals | None; see concrete issue conditions | M |
| [#952](https://github.com/VerdifyConsultancy/verdify-platform/issues/952) (closed) | Preserve Verdify Grafana monitoring identity across pod replacement | None; see concrete issue conditions | M |
| [#953](https://github.com/VerdifyConsultancy/verdify-platform/issues/953) (closed) | Reconcile Iris planning skill lookup and required reference inventory | None; see concrete issue conditions | S |
| [#835](https://github.com/VerdifyConsultancy/verdify-platform/issues/835) (closed) | Fix reconnect equipment snapshot provenance and stale relay alerts | None; see concrete issue conditions | M |
| [#862](https://github.com/VerdifyConsultancy/verdify-platform/issues/862) (closed) | Lab publisher: warm-cache init rescans the public tree for ~5 min, pushing runs past the 10-min schedule and the 20-min freshness window | None; see concrete issue conditions | M |
| [#964](https://github.com/VerdifyConsultancy/verdify-platform/issues/964) (closed) | Recover failed production promotion and schedulable Lab/band refresh | None; see concrete issue conditions | L |
| [#965](https://github.com/VerdifyConsultancy/verdify-platform/issues/965) (closed) | Separate API grow-light command intent from observed equipment history | None; see concrete issue conditions | L |
| [#963](https://github.com/VerdifyConsultancy/verdify-platform/issues/963) (open) | Bound moisture-exchange state and preserve structured consumer detail | None; see concrete issue conditions | S |
| [#976](https://github.com/VerdifyConsultancy/verdify-platform/issues/976) (closed) | Qualify safe recovery of Lab publication receipt metadata | None; see concrete issue conditions | S |
| [#980](https://github.com/VerdifyConsultancy/verdify-platform/issues/980) (closed) | Resolve backup-exporter readiness timeouts without obscuring backup health | None; see concrete issue conditions | S |
| [#981](https://github.com/VerdifyConsultancy/verdify-platform/issues/981) (open) | Restore truthful energy source freshness and trace stale meter input | None; see concrete issue conditions | S |
| [#802](https://github.com/VerdifyConsultancy/verdify-platform/issues/802) (closed) | Adopt the enforcing descheduler under manual Argo ownership | None; see concrete issue conditions | M |
| [#988](https://github.com/VerdifyConsultancy/verdify-platform/issues/988) (open) | Track tier-1 ingress incident a527cc46 and remaining public-path acceptance | None; see concrete issue conditions | M |

## C5 — Data/platform lead

| Issue / state | Outcome | Predecessor records / state | Effort |
|---|---|---|---|
| [#218](https://github.com/VerdifyConsultancy/verdify-platform/issues/218) (open) | Durability program: recoverable backups, measured RPO/RTO and safe HA option | None; see concrete issue conditions | L |
| [#670](https://github.com/VerdifyConsultancy/verdify-platform/issues/670) (closed) | Make backup artifacts sufficient to restore roles, ownership and memberships safely | None; see concrete issue conditions | L |
| [#672](https://github.com/VerdifyConsultancy/verdify-platform/issues/672) (closed) | Make compressed-chunk ownership restoration a supported blocking rehearsal test | [#670](https://github.com/VerdifyConsultancy/verdify-platform/issues/670) (closed) | M |
| [#382](https://github.com/VerdifyConsultancy/verdify-platform/issues/382) (closed) | Persist ingestor queue and spool across restart and node loss | None; see concrete issue conditions | L |
| [#396](https://github.com/VerdifyConsultancy/verdify-platform/issues/396) (closed) | Design and rehearse CNPG alongside the live database on current storage | [#670](https://github.com/VerdifyConsultancy/verdify-platform/issues/670) (closed), [#672](https://github.com/VerdifyConsultancy/verdify-platform/issues/672) (closed) | L |
| [#245](https://github.com/VerdifyConsultancy/verdify-platform/issues/245) (open) | Execute a separately bounded database cutover only after parity and rollback proof | [#382](https://github.com/VerdifyConsultancy/verdify-platform/issues/382) (closed), [#396](https://github.com/VerdifyConsultancy/verdify-platform/issues/396) (closed) | L |
| [#643](https://github.com/VerdifyConsultancy/verdify-platform/issues/643) (closed) | Extend least-privilege database roles beyond the already-required experiment boundary | [#670](https://github.com/VerdifyConsultancy/verdify-platform/issues/670) (closed) | L |
| [#49](https://github.com/VerdifyConsultancy/verdify-platform/issues/49) (closed) | Audit and close historical suppressed sensor alerts without false count-parity assumptions | None; see concrete issue conditions | S |
| [#977](https://github.com/VerdifyConsultancy/verdify-platform/issues/977) (closed) | Decide the database replica policy before November 12; keep optional reclaim separate | None; see concrete issue conditions | S |

## C6 — Control lead

| Issue / state | Outcome | Predecessor records / state | Effort |
|---|---|---|---|
| [#359](https://github.com/VerdifyConsultancy/verdify-platform/issues/359) (open) | Control program: truthful crop corridors before evidence-led tuning | None; see concrete issue conditions | L |
| [#430](https://github.com/VerdifyConsultancy/verdify-platform/issues/430) (open) | Simplify firmware only while preserving autonomous control and observable truth | None; see concrete issue conditions | L |
| [#428](https://github.com/VerdifyConsultancy/verdify-platform/issues/428) (open) | Diagnose heap/watchdog risk and qualify the actual last-good firmware floor | [#433](https://github.com/VerdifyConsultancy/verdify-platform/issues/433) (closed) | L |
| [#368](https://github.com/VerdifyConsultancy/verdify-platform/issues/368) (open) | Define shared sensor jump, flatline and contributor-integrity semantics | [#424](https://github.com/VerdifyConsultancy/verdify-platform/issues/424) (closed) | M |
| [#367](https://github.com/VerdifyConsultancy/verdify-platform/issues/367) (open) | Consolidate anti-chatter into one explicit dwell contract | [#390](https://github.com/VerdifyConsultancy/verdify-platform/issues/390) (open) | M |
| [#370](https://github.com/VerdifyConsultancy/verdify-platform/issues/370) (open) | Consolidate equipment conflict resolution into one pure arbitration function | [#367](https://github.com/VerdifyConsultancy/verdify-platform/issues/367) (open) | M |
| [#369](https://github.com/VerdifyConsultancy/verdify-platform/issues/369) (open) | Remove dead tunables and code from a verified consumer inventory | [#390](https://github.com/VerdifyConsultancy/verdify-platform/issues/390) (open), [#433](https://github.com/VerdifyConsultancy/verdify-platform/issues/433) (closed) | L |
| [#324](https://github.com/VerdifyConsultancy/verdify-platform/issues/324) (closed) | Extend zonal band lineage compatibly for future deterministic control | [#424](https://github.com/VerdifyConsultancy/verdify-platform/issues/424) (closed), [#371](https://github.com/VerdifyConsultancy/verdify-platform/issues/371) (closed) | M |
| [#410](https://github.com/VerdifyConsultancy/verdify-platform/issues/410) (open) | Measure realized solar-night dry-out before changing the controller | [#371](https://github.com/VerdifyConsultancy/verdify-platform/issues/371) (closed), [#419](https://github.com/VerdifyConsultancy/verdify-platform/issues/419) (closed), [#424](https://github.com/VerdifyConsultancy/verdify-platform/issues/424) (closed) | L |
| [#378](https://github.com/VerdifyConsultancy/verdify-platform/issues/378) (open) | Decide corridor widths from fixed-target crop and resource evidence | [#785](https://github.com/VerdifyConsultancy/verdify-platform/issues/785) (open), [#371](https://github.com/VerdifyConsultancy/verdify-platform/issues/371) (closed) | M |
| [#361](https://github.com/VerdifyConsultancy/verdify-platform/issues/361) (closed) | Reassess diurnal anchors only after solar parity and measured corridor evidence | [#378](https://github.com/VerdifyConsultancy/verdify-platform/issues/378) (open), [#410](https://github.com/VerdifyConsultancy/verdify-platform/issues/410) (open) | M |
| [#214](https://github.com/VerdifyConsultancy/verdify-platform/issues/214) (closed) | Prove crop-deviation trigger → valid plan on the sole operational planner path | [#427](https://github.com/VerdifyConsultancy/verdify-platform/issues/427) (closed), [#780](https://github.com/VerdifyConsultancy/verdify-platform/issues/780) (closed) | M |
| [#350](https://github.com/VerdifyConsultancy/verdify-platform/issues/350) (open) | Irrigation program: topology truth, overwatering protection and commissioned wall feed | None; see concrete issue conditions | L |
| [#434](https://github.com/VerdifyConsultancy/verdify-platform/issues/434) (open) | Enforce center-only climate mist and commissioned wall-only fertigation | [#299](https://github.com/VerdifyConsultancy/verdify-platform/issues/299) (open) | L |
| [#299](https://github.com/VerdifyConsultancy/verdify-platform/issues/299) (open) | Preserve center-mister re-fire protection through topology and reset recovery | [#390](https://github.com/VerdifyConsultancy/verdify-platform/issues/390) (open) | M |
| [#297](https://github.com/VerdifyConsultancy/verdify-platform/issues/297) (open) | Add saturation alerts and a commissioned dispatcher-side drip-skip loop | [#45](https://github.com/VerdifyConsultancy/verdify-platform/issues/45) (open) | M |
| [#296](https://github.com/VerdifyConsultancy/verdify-platform/issues/296) (closed) | Consider slow-hysteresis firmware irrigation feedback only after dispatcher evidence | [#297](https://github.com/VerdifyConsultancy/verdify-platform/issues/297) (open), [#434](https://github.com/VerdifyConsultancy/verdify-platform/issues/434) (open), [#45](https://github.com/VerdifyConsultancy/verdify-platform/issues/45) (open) | L |

## C7 — Operator with data/control lead

| Issue / state | Outcome | Predecessor records / state | Effort |
|---|---|---|---|
| [#16](https://github.com/VerdifyConsultancy/verdify-platform/issues/16) (open) | Hardware program: commission only the sensing and equipment justified by evidence | None; see concrete issue conditions | L |
| [#751](https://github.com/VerdifyConsultancy/verdify-platform/issues/751) (open) | Replace the failed south climate probe and diagnose hydro telemetry in a bounded window | None; see concrete issue conditions | M |
| [#298](https://github.com/VerdifyConsultancy/verdify-platform/issues/298) (open) | Rebaseline actual crop, pot and probe topology before feedback control | None; see concrete issue conditions | M |
| [#398](https://github.com/VerdifyConsultancy/verdify-platform/issues/398) (open) | Replace stale soil threshold seeds with commissioned crop/substrate truth | [#298](https://github.com/VerdifyConsultancy/verdify-platform/issues/298) (open) | M |
| [#45](https://github.com/VerdifyConsultancy/verdify-platform/issues/45) (open) | Commission soil and runoff feedback before enabling irrigation decisions | [#398](https://github.com/VerdifyConsultancy/verdify-platform/issues/398) (open) | L |
| [#51](https://github.com/VerdifyConsultancy/verdify-platform/issues/51) (open) | Calibrate CO2 against a real reference and publish measurement uncertainty | None; see concrete issue conditions | S |
| [#52](https://github.com/VerdifyConsultancy/verdify-platform/issues/52) (open) | Define species-appropriate seasonal dormancy care from operator observations | None; see concrete issue conditions | M |
| [#412](https://github.com/VerdifyConsultancy/verdify-platform/issues/412) (open) | Close the seasonal door screen only when measured night conditions justify it | None; see concrete issue conditions | S |

## C8 — Research/platform lead

| Issue / state | Outcome | Predecessor records / state | Effort |
|---|---|---|---|
| [#174](https://github.com/VerdifyConsultancy/verdify-platform/issues/174) (closed) | Rehome only current Verdify admin surfaces to global SSO, fail closed | None; see concrete issue conditions | M |
| [#14](https://github.com/VerdifyConsultancy/verdify-platform/issues/14) (open) | Twin program: qualify declared oracle coverage before using divergence as a gate | None; see concrete issue conditions | L |
| [#31](https://github.com/VerdifyConsultancy/verdify-platform/issues/31) (open) | Close twin setpoint and actuator coverage gaps with generated consumer truth | None; see concrete issue conditions | L |
| [#606](https://github.com/VerdifyConsultancy/verdify-platform/issues/606) (closed) | Register the twin build profile only through its owning fleet contract | None; see concrete issue conditions | M |
| [#638](https://github.com/VerdifyConsultancy/verdify-platform/issues/638) (closed) | Unify future manifest/vector wire schema and content identity offline | None; see concrete issue conditions | L |
| [#586](https://github.com/VerdifyConsultancy/verdify-platform/issues/586) (closed) | Qualify a future atomic firmware policy engine with crash-safe recovery | [#638](https://github.com/VerdifyConsultancy/verdify-platform/issues/638) (closed), [#390](https://github.com/VerdifyConsultancy/verdify-platform/issues/390) (open), [#785](https://github.com/VerdifyConsultancy/verdify-platform/issues/785) (open) | L |
| [#786](https://github.com/VerdifyConsultancy/verdify-platform/issues/786) (open) | Decide whether a deterministic forecast comparator merits a new study | [#785](https://github.com/VerdifyConsultancy/verdify-platform/issues/785) (open), [#780](https://github.com/VerdifyConsultancy/verdify-platform/issues/780) (closed) | L |
| [#379](https://github.com/VerdifyConsultancy/verdify-platform/issues/379) (open) | Gate grey-box forecasting and MPC on validated response models and simpler baselines | [#786](https://github.com/VerdifyConsultancy/verdify-platform/issues/786) (open), [#371](https://github.com/VerdifyConsultancy/verdify-platform/issues/371) (closed), [#419](https://github.com/VerdifyConsultancy/verdify-platform/issues/419) (closed) | L |
| [#787](https://github.com/VerdifyConsultancy/verdify-platform/issues/787) (open) | Decide and scope a measured heating/overnight/resource study | [#785](https://github.com/VerdifyConsultancy/verdify-platform/issues/785) (open), [#410](https://github.com/VerdifyConsultancy/verdify-platform/issues/410) (open), [#981](https://github.com/VerdifyConsultancy/verdify-platform/issues/981) (open), [#781](https://github.com/VerdifyConsultancy/verdify-platform/issues/781) (closed) | L |
