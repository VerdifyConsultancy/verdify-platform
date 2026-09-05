# Ownership and issue pull order

Generated from [planning/backlog.yaml](planning/backlog.yaml). Edit the source and run `python -m planning.render`; do not maintain a competing roadmap.

The [campaign strategy](planning/CAMPAIGN.md) defines scope, release bundles, evidence gates and deferred work. Historical June/August plans are [archived](planning/archive/2026-09-05/README.md), not execution instructions.

One accountable role per issue; preserve existing assignees and choose an execution owner when pulling work. Serialize migrations and writer/release mutations even when analysis and device-denied tests run concurrently.

## C0 — Evidence/data lead

| Issue | Outcome | Blocking predecessors | Effort |
|---|---|---|---|
| [#775](https://github.com/VerdifyConsultancy/verdify-platform/issues/775) | Campaign: trustworthy greenhouse outcomes → qualified pilot → evidence-backed control | None; see concrete issue conditions | L |
| [#778](https://github.com/VerdifyConsultancy/verdify-platform/issues/778) | Investigate September 4 hot/dry peak and three-hour wetting interruption | None; see concrete issue conditions | L |
| [#371](https://github.com/VerdifyConsultancy/verdify-platform/issues/371) | Repair climate score semantics and publish physical outcomes separately from credit | None; see concrete issue conditions | L |
| [#424](https://github.com/VerdifyConsultancy/verdify-platform/issues/424) | Resolve served, consumed and raw-readback band lineage without fabricated device truth | None; see concrete issue conditions | L |
| [#779](https://github.com/VerdifyConsultancy/verdify-platform/issues/779) | Re-run historical planner comparisons on a fixed sensor panel and fixed target contract | [#371](https://github.com/VerdifyConsultancy/verdify-platform/issues/371) | L |
| [#780](https://github.com/VerdifyConsultancy/verdify-platform/issues/780) | Score as-of outdoor forecasts against outdoor truth and separate indoor response | None; see concrete issue conditions | M |
| [#781](https://github.com/VerdifyConsultancy/verdify-platform/issues/781) | Commission a claim-safe water and electricity endpoint contract | None; see concrete issue conditions | L |
| [#782](https://github.com/VerdifyConsultancy/verdify-platform/issues/782) | Choose a season-appropriate exploratory pilot and freeze the scientific contract | [#371](https://github.com/VerdifyConsultancy/verdify-platform/issues/371), [#779](https://github.com/VerdifyConsultancy/verdify-platform/issues/779), [#780](https://github.com/VerdifyConsultancy/verdify-platform/issues/780), [#781](https://github.com/VerdifyConsultancy/verdify-platform/issues/781) | L |

## C1 — Runtime/release lead

| Issue | Outcome | Blocking predecessors | Effort |
|---|---|---|---|
| [#750](https://github.com/VerdifyConsultancy/verdify-platform/issues/750) | Recovery/proof campaign: rebaseline current state and seal one safe physical receipt | None; see concrete issue conditions | L |
| [#747](https://github.com/VerdifyConsultancy/verdify-platform/issues/747) | Verify corrected scheduled backups and current production recovery health | None; see concrete issue conditions | S |
| [#749](https://github.com/VerdifyConsultancy/verdify-platform/issues/749) | Qualify current three-probe readiness and explicit safety dependencies | [#424](https://github.com/VerdifyConsultancy/verdify-platform/issues/424), [#778](https://github.com/VerdifyConsultancy/verdify-platform/issues/778) | M |
| [#641](https://github.com/VerdifyConsultancy/verdify-platform/issues/641) | Execute authorized orphan recovery and one separately attended physical proof | [#747](https://github.com/VerdifyConsultancy/verdify-platform/issues/747), [#749](https://github.com/VerdifyConsultancy/verdify-platform/issues/749), [#639](https://github.com/VerdifyConsultancy/verdify-platform/issues/639), [#587](https://github.com/VerdifyConsultancy/verdify-platform/issues/587) | L |
| [#639](https://github.com/VerdifyConsultancy/verdify-platform/issues/639) | Qualify the existing component executor and full-state recovery before launch | None; see concrete issue conditions | L |
| [#587](https://github.com/VerdifyConsultancy/verdify-platform/issues/587) | Qualify fail-closed lifecycle, kill switch and blinded operational visibility | None; see concrete issue conditions | M |
| [#783](https://github.com/VerdifyConsultancy/verdify-platform/issues/783) | Prove restore → selector → setter schema → receipt → frozen outcome end to end | [#639](https://github.com/VerdifyConsultancy/verdify-platform/issues/639), [#587](https://github.com/VerdifyConsultancy/verdify-platform/issues/587), [#782](https://github.com/VerdifyConsultancy/verdify-platform/issues/782) | L |

## C2 — Research/release lead

| Issue | Outcome | Blocking predecessors | Effort |
|---|---|---|---|
| [#581](https://github.com/VerdifyConsultancy/verdify-platform/issues/581) | Experiment program: qualify, run and read out the bounded AI-admission pilot | None; see concrete issue conditions | L |
| [#588](https://github.com/VerdifyConsultancy/verdify-platform/issues/588) | Lock the qualified pilot identity and finalize exactly one internal random draw | [#641](https://github.com/VerdifyConsultancy/verdify-platform/issues/641), [#782](https://github.com/VerdifyConsultancy/verdify-platform/issues/782), [#783](https://github.com/VerdifyConsultancy/verdify-platform/issues/783) | M |
| [#642](https://github.com/VerdifyConsultancy/verdify-platform/issues/642) | Authorize and verify one randomized day-1 activation from the locked pilot | [#588](https://github.com/VerdifyConsultancy/verdify-platform/issues/588) | M |

## C3 — Research lead

| Issue | Outcome | Blocking predecessors | Effort |
|---|---|---|---|
| [#640](https://github.com/VerdifyConsultancy/verdify-platform/issues/640) | Freeze the first assigned-day ITT outcome and blinded reproducible export | [#642](https://github.com/VerdifyConsultancy/verdify-platform/issues/642) | M |
| [#784](https://github.com/VerdifyConsultancy/verdify-platform/issues/784) | Operate the complete blinded pilot and preserve every assigned day | [#640](https://github.com/VerdifyConsultancy/verdify-platform/issues/640) | L |
| [#785](https://github.com/VerdifyConsultancy/verdify-platform/issues/785) | Reveal once, reproduce the frozen analysis and publish the campaign decision | [#784](https://github.com/VerdifyConsultancy/verdify-platform/issues/784) | M |

## C4 — Platform lead

| Issue | Outcome | Blocking predecessors | Effort |
|---|---|---|---|
| [#644](https://github.com/VerdifyConsultancy/verdify-platform/issues/644) | Integrate exact-SHA, path-aware and device-safe delivery receipts | None; see concrete issue conditions | L |
| [#322](https://github.com/VerdifyConsultancy/verdify-platform/issues/322) | Replace remaining VM-era tests with current k3s delivery contracts | None; see concrete issue conditions | M |
| [#304](https://github.com/VerdifyConsultancy/verdify-platform/issues/304) | Provide reproducible least-authority firmware and database diagnostic tooling | None; see concrete issue conditions | S |
| [#303](https://github.com/VerdifyConsultancy/verdify-platform/issues/303) | Run real portable ESPHome compilation for declared firmware targets | [#304](https://github.com/VerdifyConsultancy/verdify-platform/issues/304), [#322](https://github.com/VerdifyConsultancy/verdify-platform/issues/322) | L |
| [#390](https://github.com/VerdifyConsultancy/verdify-platform/issues/390) | Gate firmware releases on topology, cycling, runtime safety and rollback evidence | [#303](https://github.com/VerdifyConsultancy/verdify-platform/issues/303), [#419](https://github.com/VerdifyConsultancy/verdify-platform/issues/419) | L |
| [#433](https://github.com/VerdifyConsultancy/verdify-platform/issues/433) | Finish truthful single-writer command lifecycle and no-reconcile-storm acceptance | None; see concrete issue conditions | L |
| [#427](https://github.com/VerdifyConsultancy/verdify-platform/issues/427) | Prove active Hermes/MCP recovery and truthful non-authoritative worker health | [#433](https://github.com/VerdifyConsultancy/verdify-platform/issues/433) | L |
| [#75](https://github.com/VerdifyConsultancy/verdify-platform/issues/75) | Observability program: prove actionable health from source through device | None; see concrete issue conditions | L |
| [#563](https://github.com/VerdifyConsultancy/verdify-platform/issues/563) | Enforce the actual ConfigMap-based alert delivery contract | None; see concrete issue conditions | S |
| [#394](https://github.com/VerdifyConsultancy/verdify-platform/issues/394) | Deliver and fault-test live split-brain and no-writer alerts | [#563](https://github.com/VerdifyConsultancy/verdify-platform/issues/563) | M |
| [#89](https://github.com/VerdifyConsultancy/verdify-platform/issues/89) | Verify exact-source post-deploy smoke and device-route health | [#563](https://github.com/VerdifyConsultancy/verdify-platform/issues/563) | M |
| [#671](https://github.com/VerdifyConsultancy/verdify-platform/issues/671) | Make Lab publishing failures and cache contention observable | None; see concrete issue conditions | M |
| [#399](https://github.com/VerdifyConsultancy/verdify-platform/issues/399) | Prove the HA grow-light writer remains within the single-writer contract | None; see concrete issue conditions | S |
| [#419](https://github.com/VerdifyConsultancy/verdify-platform/issues/419) | Populate real outdoor freshness in replay and enforce branch coverage | None; see concrete issue conditions | M |
| [#386](https://github.com/VerdifyConsultancy/verdify-platform/issues/386) | Prove grow-light minimum-on behavior at the solar-window boundary | [#303](https://github.com/VerdifyConsultancy/verdify-platform/issues/303) | M |

## C5 — Data/platform lead

| Issue | Outcome | Blocking predecessors | Effort |
|---|---|---|---|
| [#218](https://github.com/VerdifyConsultancy/verdify-platform/issues/218) | Durability program: recoverable backups, measured RPO/RTO and safe HA option | None; see concrete issue conditions | L |
| [#670](https://github.com/VerdifyConsultancy/verdify-platform/issues/670) | Make backup artifacts sufficient to restore roles, ownership and memberships safely | None; see concrete issue conditions | L |
| [#672](https://github.com/VerdifyConsultancy/verdify-platform/issues/672) | Make compressed-chunk ownership restoration a supported blocking rehearsal test | [#670](https://github.com/VerdifyConsultancy/verdify-platform/issues/670) | M |
| [#382](https://github.com/VerdifyConsultancy/verdify-platform/issues/382) | Persist ingestor queue and spool across restart and node loss | None; see concrete issue conditions | L |
| [#396](https://github.com/VerdifyConsultancy/verdify-platform/issues/396) | Design and rehearse CNPG alongside the live database on current storage | [#670](https://github.com/VerdifyConsultancy/verdify-platform/issues/670), [#672](https://github.com/VerdifyConsultancy/verdify-platform/issues/672) | L |
| [#245](https://github.com/VerdifyConsultancy/verdify-platform/issues/245) | Execute a separately bounded database cutover only after parity and rollback proof | [#396](https://github.com/VerdifyConsultancy/verdify-platform/issues/396), [#382](https://github.com/VerdifyConsultancy/verdify-platform/issues/382) | L |
| [#643](https://github.com/VerdifyConsultancy/verdify-platform/issues/643) | Extend least-privilege database roles beyond the already-required experiment boundary | [#670](https://github.com/VerdifyConsultancy/verdify-platform/issues/670) | L |
| [#49](https://github.com/VerdifyConsultancy/verdify-platform/issues/49) | Audit and close historical suppressed sensor alerts without false count-parity assumptions | None; see concrete issue conditions | S |

## C6 — Control lead

| Issue | Outcome | Blocking predecessors | Effort |
|---|---|---|---|
| [#359](https://github.com/VerdifyConsultancy/verdify-platform/issues/359) | Control program: truthful crop corridors before evidence-led tuning | None; see concrete issue conditions | L |
| [#430](https://github.com/VerdifyConsultancy/verdify-platform/issues/430) | Simplify firmware only while preserving autonomous control and observable truth | None; see concrete issue conditions | L |
| [#428](https://github.com/VerdifyConsultancy/verdify-platform/issues/428) | Diagnose heap/watchdog risk and qualify the actual last-good firmware floor | [#433](https://github.com/VerdifyConsultancy/verdify-platform/issues/433) | L |
| [#368](https://github.com/VerdifyConsultancy/verdify-platform/issues/368) | Define shared sensor jump, flatline and contributor-integrity semantics | [#424](https://github.com/VerdifyConsultancy/verdify-platform/issues/424) | M |
| [#367](https://github.com/VerdifyConsultancy/verdify-platform/issues/367) | Consolidate anti-chatter into one explicit dwell contract | [#390](https://github.com/VerdifyConsultancy/verdify-platform/issues/390) | M |
| [#370](https://github.com/VerdifyConsultancy/verdify-platform/issues/370) | Consolidate equipment conflict resolution into one pure arbitration function | [#390](https://github.com/VerdifyConsultancy/verdify-platform/issues/390), [#367](https://github.com/VerdifyConsultancy/verdify-platform/issues/367) | M |
| [#369](https://github.com/VerdifyConsultancy/verdify-platform/issues/369) | Remove dead tunables and code from a verified consumer inventory | [#433](https://github.com/VerdifyConsultancy/verdify-platform/issues/433), [#390](https://github.com/VerdifyConsultancy/verdify-platform/issues/390) | L |
| [#324](https://github.com/VerdifyConsultancy/verdify-platform/issues/324) | Extend zonal band lineage compatibly for future deterministic control | [#424](https://github.com/VerdifyConsultancy/verdify-platform/issues/424), [#371](https://github.com/VerdifyConsultancy/verdify-platform/issues/371) | M |
| [#410](https://github.com/VerdifyConsultancy/verdify-platform/issues/410) | Measure realized solar-night dry-out before changing the controller | [#424](https://github.com/VerdifyConsultancy/verdify-platform/issues/424), [#371](https://github.com/VerdifyConsultancy/verdify-platform/issues/371), [#419](https://github.com/VerdifyConsultancy/verdify-platform/issues/419) | L |
| [#378](https://github.com/VerdifyConsultancy/verdify-platform/issues/378) | Decide corridor widths from fixed-target crop and resource evidence | [#371](https://github.com/VerdifyConsultancy/verdify-platform/issues/371), [#785](https://github.com/VerdifyConsultancy/verdify-platform/issues/785) | M |
| [#361](https://github.com/VerdifyConsultancy/verdify-platform/issues/361) | Reassess diurnal anchors only after solar parity and measured corridor evidence | [#378](https://github.com/VerdifyConsultancy/verdify-platform/issues/378), [#410](https://github.com/VerdifyConsultancy/verdify-platform/issues/410) | M |
| [#214](https://github.com/VerdifyConsultancy/verdify-platform/issues/214) | Prove crop-deviation trigger → valid plan on the sole operational planner path | [#427](https://github.com/VerdifyConsultancy/verdify-platform/issues/427), [#780](https://github.com/VerdifyConsultancy/verdify-platform/issues/780) | M |
| [#350](https://github.com/VerdifyConsultancy/verdify-platform/issues/350) | Irrigation program: topology truth, overwatering protection and commissioned wall feed | None; see concrete issue conditions | L |
| [#434](https://github.com/VerdifyConsultancy/verdify-platform/issues/434) | Enforce center-only climate mist and commissioned wall-only fertigation | [#390](https://github.com/VerdifyConsultancy/verdify-platform/issues/390), [#299](https://github.com/VerdifyConsultancy/verdify-platform/issues/299) | L |
| [#299](https://github.com/VerdifyConsultancy/verdify-platform/issues/299) | Preserve center-mister re-fire protection through topology and reset recovery | [#390](https://github.com/VerdifyConsultancy/verdify-platform/issues/390) | M |
| [#297](https://github.com/VerdifyConsultancy/verdify-platform/issues/297) | Add saturation alerts and a commissioned dispatcher-side drip-skip loop | [#398](https://github.com/VerdifyConsultancy/verdify-platform/issues/398), [#45](https://github.com/VerdifyConsultancy/verdify-platform/issues/45) | M |
| [#296](https://github.com/VerdifyConsultancy/verdify-platform/issues/296) | Consider slow-hysteresis firmware irrigation feedback only after dispatcher evidence | [#297](https://github.com/VerdifyConsultancy/verdify-platform/issues/297), [#434](https://github.com/VerdifyConsultancy/verdify-platform/issues/434), [#45](https://github.com/VerdifyConsultancy/verdify-platform/issues/45) | L |

## C7 — Operator with data/control lead

| Issue | Outcome | Blocking predecessors | Effort |
|---|---|---|---|
| [#16](https://github.com/VerdifyConsultancy/verdify-platform/issues/16) | Hardware program: commission only the sensing and equipment justified by evidence | None; see concrete issue conditions | L |
| [#751](https://github.com/VerdifyConsultancy/verdify-platform/issues/751) | Replace the failed south climate probe and diagnose hydro telemetry in a bounded window | None; see concrete issue conditions | M |
| [#298](https://github.com/VerdifyConsultancy/verdify-platform/issues/298) | Rebaseline actual crop, pot and probe topology before feedback control | None; see concrete issue conditions | M |
| [#398](https://github.com/VerdifyConsultancy/verdify-platform/issues/398) | Replace stale soil threshold seeds with commissioned crop/substrate truth | [#298](https://github.com/VerdifyConsultancy/verdify-platform/issues/298) | M |
| [#45](https://github.com/VerdifyConsultancy/verdify-platform/issues/45) | Commission soil and runoff feedback before enabling irrigation decisions | [#398](https://github.com/VerdifyConsultancy/verdify-platform/issues/398) | L |
| [#51](https://github.com/VerdifyConsultancy/verdify-platform/issues/51) | Calibrate CO2 against a real reference and publish measurement uncertainty | None; see concrete issue conditions | S |
| [#52](https://github.com/VerdifyConsultancy/verdify-platform/issues/52) | Define species-appropriate seasonal dormancy care from operator observations | None; see concrete issue conditions | M |
| [#412](https://github.com/VerdifyConsultancy/verdify-platform/issues/412) | Close the seasonal door screen only when measured night conditions justify it | None; see concrete issue conditions | S |

## C8 — Research/platform lead

| Issue | Outcome | Blocking predecessors | Effort |
|---|---|---|---|
| [#174](https://github.com/VerdifyConsultancy/verdify-platform/issues/174) | Rehome only current Verdify admin surfaces to global SSO, fail closed | None; see concrete issue conditions | M |
| [#14](https://github.com/VerdifyConsultancy/verdify-platform/issues/14) | Twin program: qualify declared oracle coverage before using divergence as a gate | None; see concrete issue conditions | L |
| [#31](https://github.com/VerdifyConsultancy/verdify-platform/issues/31) | Close twin setpoint and actuator coverage gaps with generated consumer truth | None; see concrete issue conditions | L |
| [#606](https://github.com/VerdifyConsultancy/verdify-platform/issues/606) | Register the twin build profile only through its owning fleet contract | None; see concrete issue conditions | M |
| [#638](https://github.com/VerdifyConsultancy/verdify-platform/issues/638) | Unify future manifest/vector wire schema and content identity offline | None; see concrete issue conditions | L |
| [#586](https://github.com/VerdifyConsultancy/verdify-platform/issues/586) | Qualify a future atomic firmware policy engine with crash-safe recovery | [#638](https://github.com/VerdifyConsultancy/verdify-platform/issues/638), [#390](https://github.com/VerdifyConsultancy/verdify-platform/issues/390), [#785](https://github.com/VerdifyConsultancy/verdify-platform/issues/785) | L |
| [#786](https://github.com/VerdifyConsultancy/verdify-platform/issues/786) | Design the next comparison against a deterministic forecast-aware selector | [#785](https://github.com/VerdifyConsultancy/verdify-platform/issues/785), [#780](https://github.com/VerdifyConsultancy/verdify-platform/issues/780) | L |
| [#379](https://github.com/VerdifyConsultancy/verdify-platform/issues/379) | Gate grey-box forecasting and MPC on validated response models and simpler baselines | [#786](https://github.com/VerdifyConsultancy/verdify-platform/issues/786), [#419](https://github.com/VerdifyConsultancy/verdify-platform/issues/419), [#371](https://github.com/VerdifyConsultancy/verdify-platform/issues/371) | L |
| [#787](https://github.com/VerdifyConsultancy/verdify-platform/issues/787) | Plan measured heating, overnight and whole-resource economics as a separate claim | [#785](https://github.com/VerdifyConsultancy/verdify-platform/issues/785), [#781](https://github.com/VerdifyConsultancy/verdify-platform/issues/781), [#410](https://github.com/VerdifyConsultancy/verdify-platform/issues/410) | L |
