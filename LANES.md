# Ownership and issue pull order

## Current execution plan

Reviewed **2026-11-06** against source `0ebab1860a6fd022e99702b7fb26400034461a5c`. [planning/reviews/2026-11-06-triage/README.md](planning/reviews/2026-11-06-triage/README.md) records current source, GitHub, runtime and evidence limits. All **86 open issues** have one allocation and accountable role. Planning capacity: **40 engineering hours/week**; ranges are estimates, not calendar promises.

| Pull set | Kind | Allocated hours | Capacity hours | Issues |
|---|---|---|---|---|
| T0 — Unblock: Cortex egress fix and the in-flight web pin | sprint | 7–21 | 20 | [#1351](https://github.com/VerdifyConsultancy/verdify-platform/issues/1351), [#1118](https://github.com/VerdifyConsultancy/verdify-platform/issues/1118), [#1346](https://github.com/VerdifyConsultancy/verdify-platform/issues/1346), [#1330](https://github.com/VerdifyConsultancy/verdify-platform/issues/1330), [#1361](https://github.com/VerdifyConsultancy/verdify-platform/issues/1361), [#1327](https://github.com/VerdifyConsultancy/verdify-platform/issues/1327), [#1328](https://github.com/VerdifyConsultancy/verdify-platform/issues/1328) |
| T1 — Live verification walk (shipped code, no new build) | sprint | 0–32 | 16 | [#1125](https://github.com/VerdifyConsultancy/verdify-platform/issues/1125), [#1130](https://github.com/VerdifyConsultancy/verdify-platform/issues/1130), [#1132](https://github.com/VerdifyConsultancy/verdify-platform/issues/1132), [#1135](https://github.com/VerdifyConsultancy/verdify-platform/issues/1135), [#1143](https://github.com/VerdifyConsultancy/verdify-platform/issues/1143), [#1181](https://github.com/VerdifyConsultancy/verdify-platform/issues/1181), [#1186](https://github.com/VerdifyConsultancy/verdify-platform/issues/1186), [#1300](https://github.com/VerdifyConsultancy/verdify-platform/issues/1300), [#1162](https://github.com/VerdifyConsultancy/verdify-platform/issues/1162), [#1164](https://github.com/VerdifyConsultancy/verdify-platform/issues/1164), [#1337](https://github.com/VerdifyConsultancy/verdify-platform/issues/1337), [#1338](https://github.com/VerdifyConsultancy/verdify-platform/issues/1338), [#1339](https://github.com/VerdifyConsultancy/verdify-platform/issues/1339), [#1340](https://github.com/VerdifyConsultancy/verdify-platform/issues/1340), [#1341](https://github.com/VerdifyConsultancy/verdify-platform/issues/1341), [#1342](https://github.com/VerdifyConsultancy/verdify-platform/issues/1342), [#1344](https://github.com/VerdifyConsultancy/verdify-platform/issues/1344), [#1347](https://github.com/VerdifyConsultancy/verdify-platform/issues/1347), [#1349](https://github.com/VerdifyConsultancy/verdify-platform/issues/1349), [#1353](https://github.com/VerdifyConsultancy/verdify-platform/issues/1353), [#1355](https://github.com/VerdifyConsultancy/verdify-platform/issues/1355), [#1356](https://github.com/VerdifyConsultancy/verdify-platform/issues/1356), [#1357](https://github.com/VerdifyConsultancy/verdify-platform/issues/1357), [#1332](https://github.com/VerdifyConsultancy/verdify-platform/issues/1332), [#1333](https://github.com/VerdifyConsultancy/verdify-platform/issues/1333), [#1334](https://github.com/VerdifyConsultancy/verdify-platform/issues/1334), [#1335](https://github.com/VerdifyConsultancy/verdify-platform/issues/1335), [#1336](https://github.com/VerdifyConsultancy/verdify-platform/issues/1336), [#1348](https://github.com/VerdifyConsultancy/verdify-platform/issues/1348), [#1370](https://github.com/VerdifyConsultancy/verdify-platform/issues/1370), [#1343](https://github.com/VerdifyConsultancy/verdify-platform/issues/1343), [#1358](https://github.com/VerdifyConsultancy/verdify-platform/issues/1358) |
| T2 — Bounded code fixes | sprint | 36–108 | 40 | [#1345](https://github.com/VerdifyConsultancy/verdify-platform/issues/1345), [#1354](https://github.com/VerdifyConsultancy/verdify-platform/issues/1354), [#1322](https://github.com/VerdifyConsultancy/verdify-platform/issues/1322), [#1329](https://github.com/VerdifyConsultancy/verdify-platform/issues/1329), [#1277](https://github.com/VerdifyConsultancy/verdify-platform/issues/1277), [#1283](https://github.com/VerdifyConsultancy/verdify-platform/issues/1283), [#1312](https://github.com/VerdifyConsultancy/verdify-platform/issues/1312), [#1313](https://github.com/VerdifyConsultancy/verdify-platform/issues/1313), [#1389](https://github.com/VerdifyConsultancy/verdify-platform/issues/1389), [#1363](https://github.com/VerdifyConsultancy/verdify-platform/issues/1363), [#1010](https://github.com/VerdifyConsultancy/verdify-platform/issues/1010), [#299](https://github.com/VerdifyConsultancy/verdify-platform/issues/299), [#367](https://github.com/VerdifyConsultancy/verdify-platform/issues/367), [#368](https://github.com/VerdifyConsultancy/verdify-platform/issues/368), [#369](https://github.com/VerdifyConsultancy/verdify-platform/issues/369), [#370](https://github.com/VerdifyConsultancy/verdify-platform/issues/370), [#390](https://github.com/VerdifyConsultancy/verdify-platform/issues/390), [#386](https://github.com/VerdifyConsultancy/verdify-platform/issues/386) |
| T3 — Planner A/B study (Hermes arm) | conditional | Unestimated | Not timeboxed | [#1360](https://github.com/VerdifyConsultancy/verdify-platform/issues/1360) |
| T4 — Time-gated soaks | ongoing | Unestimated | Not timeboxed | [#1364](https://github.com/VerdifyConsultancy/verdify-platform/issues/1364), [#1365](https://github.com/VerdifyConsultancy/verdify-platform/issues/1365), [#1352](https://github.com/VerdifyConsultancy/verdify-platform/issues/1352), [#1165](https://github.com/VerdifyConsultancy/verdify-platform/issues/1165) |
| T5 — Hardware, replant and irrigation bring-up (Jason physical) | conditional | Unestimated | Not timeboxed | [#51](https://github.com/VerdifyConsultancy/verdify-platform/issues/51), [#751](https://github.com/VerdifyConsultancy/verdify-platform/issues/751), [#1183](https://github.com/VerdifyConsultancy/verdify-platform/issues/1183), [#1184](https://github.com/VerdifyConsultancy/verdify-platform/issues/1184), [#298](https://github.com/VerdifyConsultancy/verdify-platform/issues/298), [#398](https://github.com/VerdifyConsultancy/verdify-platform/issues/398), [#45](https://github.com/VerdifyConsultancy/verdify-platform/issues/45), [#297](https://github.com/VerdifyConsultancy/verdify-platform/issues/297), [#52](https://github.com/VerdifyConsultancy/verdify-platform/issues/52), [#1166](https://github.com/VerdifyConsultancy/verdify-platform/issues/1166), [#1161](https://github.com/VerdifyConsultancy/verdify-platform/issues/1161), [#1271](https://github.com/VerdifyConsultancy/verdify-platform/issues/1271) |
| T6 — External owners, upstream and the repo transfer | conditional | Unestimated | Not timeboxed | [#1001](https://github.com/VerdifyConsultancy/verdify-platform/issues/1001), [#1009](https://github.com/VerdifyConsultancy/verdify-platform/issues/1009), [#1113](https://github.com/VerdifyConsultancy/verdify-platform/issues/1113), [#1397](https://github.com/VerdifyConsultancy/verdify-platform/issues/1397), [#31](https://github.com/VerdifyConsultancy/verdify-platform/issues/31) |
| ROLLUP — Program roll-ups | rollup | Unestimated | Not timeboxed | [#16](https://github.com/VerdifyConsultancy/verdify-platform/issues/16), [#75](https://github.com/VerdifyConsultancy/verdify-platform/issues/75), [#359](https://github.com/VerdifyConsultancy/verdify-platform/issues/359), [#430](https://github.com/VerdifyConsultancy/verdify-platform/issues/430), [#775](https://github.com/VerdifyConsultancy/verdify-platform/issues/775), [#1120](https://github.com/VerdifyConsultancy/verdify-platform/issues/1120), [#1325](https://github.com/VerdifyConsultancy/verdify-platform/issues/1325) |

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
| [#782](https://github.com/VerdifyConsultancy/verdify-platform/issues/782) (closed) | Finish the empirical inputs and frozen exploratory scientific contract | [#371](https://github.com/VerdifyConsultancy/verdify-platform/issues/371) (closed), [#779](https://github.com/VerdifyConsultancy/verdify-platform/issues/779) (closed), [#780](https://github.com/VerdifyConsultancy/verdify-platform/issues/780) (closed), [#781](https://github.com/VerdifyConsultancy/verdify-platform/issues/781) (closed) | L |

## C1 — Runtime/release lead

| Issue / state | Outcome | Predecessor records / state | Effort |
|---|---|---|---|
| [#750](https://github.com/VerdifyConsultancy/verdify-platform/issues/750) (closed) | Recovery/proof campaign: rebaseline current state and seal one safe physical receipt | None; see concrete issue conditions | L |
| [#747](https://github.com/VerdifyConsultancy/verdify-platform/issues/747) (closed) | Verify corrected scheduled backups and current production recovery health | None; see concrete issue conditions | S |
| [#749](https://github.com/VerdifyConsultancy/verdify-platform/issues/749) (closed) | Qualify current three-probe readiness and explicit safety dependencies | [#424](https://github.com/VerdifyConsultancy/verdify-platform/issues/424) (closed), [#778](https://github.com/VerdifyConsultancy/verdify-platform/issues/778) (closed), [#949](https://github.com/VerdifyConsultancy/verdify-platform/issues/949) (closed) | M |
| [#641](https://github.com/VerdifyConsultancy/verdify-platform/issues/641) (closed) | Complete one fresh attended physical baseline–target–baseline proof | [#587](https://github.com/VerdifyConsultancy/verdify-platform/issues/587) (closed), [#639](https://github.com/VerdifyConsultancy/verdify-platform/issues/639) (closed), [#747](https://github.com/VerdifyConsultancy/verdify-platform/issues/747) (closed), [#749](https://github.com/VerdifyConsultancy/verdify-platform/issues/749) (closed) | L |
| [#639](https://github.com/VerdifyConsultancy/verdify-platform/issues/639) (closed) | Qualify the component executor, exclusivity and recovery behavior in containment | None; see concrete issue conditions | L |
| [#587](https://github.com/VerdifyConsultancy/verdify-platform/issues/587) (closed) | Qualify lifecycle authority and blinded monitoring in containment | None; see concrete issue conditions | M |
| [#783](https://github.com/VerdifyConsultancy/verdify-platform/issues/783) (closed) | Qualify the complete current-login selector-to-frozen-analysis path | [#587](https://github.com/VerdifyConsultancy/verdify-platform/issues/587) (closed), [#639](https://github.com/VerdifyConsultancy/verdify-platform/issues/639) (closed) | L |

## C2 — Research/release lead

| Issue / state | Outcome | Predecessor records / state | Effort |
|---|---|---|---|
| [#581](https://github.com/VerdifyConsultancy/verdify-platform/issues/581) (closed) | Complete the bounded AI-admission pilot and publish a decision | None; see concrete issue conditions | L |
| [#588](https://github.com/VerdifyConsultancy/verdify-platform/issues/588) (closed) | Bind exact scientific/runtime identity and finalize one internal draw | [#641](https://github.com/VerdifyConsultancy/verdify-platform/issues/641) (closed), [#782](https://github.com/VerdifyConsultancy/verdify-platform/issues/782) (closed), [#783](https://github.com/VerdifyConsultancy/verdify-platform/issues/783) (closed) | M |
| [#642](https://github.com/VerdifyConsultancy/verdify-platform/issues/642) (closed) | Activate and verify one randomized day 1 from the locked pilot | [#588](https://github.com/VerdifyConsultancy/verdify-platform/issues/588) (closed) | M |

## C3 — Research lead

| Issue / state | Outcome | Predecessor records / state | Effort |
|---|---|---|---|
| [#640](https://github.com/VerdifyConsultancy/verdify-platform/issues/640) (closed) | Freeze and reconcile the first real assigned-day outcome and blinded export | [#642](https://github.com/VerdifyConsultancy/verdify-platform/issues/642) (closed) | M |
| [#784](https://github.com/VerdifyConsultancy/verdify-platform/issues/784) (closed) | Operate the full blinded assigned-day pilot with immutable custody | [#640](https://github.com/VerdifyConsultancy/verdify-platform/issues/640) (closed) | L |
| [#785](https://github.com/VerdifyConsultancy/verdify-platform/issues/785) (closed) | Reveal once, reproduce the frozen analysis and decide the program outcome | [#784](https://github.com/VerdifyConsultancy/verdify-platform/issues/784) (closed) | M |

## C4 — Platform lead

| Issue / state | Outcome | Predecessor records / state | Effort |
|---|---|---|---|
| [#644](https://github.com/VerdifyConsultancy/verdify-platform/issues/644) (closed) | Integrate exact-SHA, path-aware and device-safe delivery receipts | [#964](https://github.com/VerdifyConsultancy/verdify-platform/issues/964) (closed) | L |
| [#322](https://github.com/VerdifyConsultancy/verdify-platform/issues/322) (closed) | Replace remaining VM-era tests with current k3s delivery contracts | None; see concrete issue conditions | M |
| [#304](https://github.com/VerdifyConsultancy/verdify-platform/issues/304) (closed) | Provide reproducible least-authority firmware and database diagnostic tooling | None; see concrete issue conditions | S |
| [#303](https://github.com/VerdifyConsultancy/verdify-platform/issues/303) (closed) | Run real portable ESPHome compilation for declared firmware targets | [#304](https://github.com/VerdifyConsultancy/verdify-platform/issues/304) (closed), [#322](https://github.com/VerdifyConsultancy/verdify-platform/issues/322) (closed) | L |
| [#390](https://github.com/VerdifyConsultancy/verdify-platform/issues/390) (open) | Gate firmware releases on topology, cycling, runtime safety and rollback evidence | None; see concrete issue conditions | L |
| [#433](https://github.com/VerdifyConsultancy/verdify-platform/issues/433) (closed) | Finish truthful single-writer command lifecycle and no-reconcile-storm acceptance | None; see concrete issue conditions | L |
| [#427](https://github.com/VerdifyConsultancy/verdify-platform/issues/427) (closed) | Prove active Hermes/MCP recovery and truthful non-authoritative worker health | [#433](https://github.com/VerdifyConsultancy/verdify-platform/issues/433) (closed) | L |
| [#75](https://github.com/VerdifyConsultancy/verdify-platform/issues/75) (open) | Observability program: prove actionable health from source through device | None; see concrete issue conditions | L |
| [#563](https://github.com/VerdifyConsultancy/verdify-platform/issues/563) (closed) | Enforce the actual ConfigMap-based alert delivery contract | None; see concrete issue conditions | S |
| [#394](https://github.com/VerdifyConsultancy/verdify-platform/issues/394) (closed) | Deliver and fault-test live split-brain and no-writer alerts | [#563](https://github.com/VerdifyConsultancy/verdify-platform/issues/563) (closed) | M |
| [#89](https://github.com/VerdifyConsultancy/verdify-platform/issues/89) (closed) | Verify exact-source post-deploy smoke and device-route health | [#563](https://github.com/VerdifyConsultancy/verdify-platform/issues/563) (closed) | M |
| [#671](https://github.com/VerdifyConsultancy/verdify-platform/issues/671) (closed) | Make Lab publishing failures and cache contention observable | None; see concrete issue conditions | M |
| [#399](https://github.com/VerdifyConsultancy/verdify-platform/issues/399) (closed) | Prove the HA grow-light writer remains within the single-writer contract | None; see concrete issue conditions | S |
| [#419](https://github.com/VerdifyConsultancy/verdify-platform/issues/419) (closed) | Populate real outdoor freshness in replay and enforce branch coverage | None; see concrete issue conditions | M |
| [#386](https://github.com/VerdifyConsultancy/verdify-platform/issues/386) (open) | Prove grow-light minimum-on behavior at the solar-window boundary | None; see concrete issue conditions | M |
| [#949](https://github.com/VerdifyConsultancy/verdify-platform/issues/949) (closed) | Bound planner-policy drift holds and prove autonomous safe convergence | None; see concrete issue conditions | M |
| [#950](https://github.com/VerdifyConsultancy/verdify-platform/issues/950) (closed) | Restore the ingestor Slack credential-file contract and operator delivery | None; see concrete issue conditions | M |
| [#951](https://github.com/VerdifyConsultancy/verdify-platform/issues/951) (closed) | Restore authenticated greenhouse camera snapshots after connection refusals | None; see concrete issue conditions | M |
| [#952](https://github.com/VerdifyConsultancy/verdify-platform/issues/952) (closed) | Preserve Verdify Grafana monitoring identity across pod replacement | None; see concrete issue conditions | M |
| [#953](https://github.com/VerdifyConsultancy/verdify-platform/issues/953) (closed) | Reconcile Iris planning skill lookup and required reference inventory | None; see concrete issue conditions | S |
| [#835](https://github.com/VerdifyConsultancy/verdify-platform/issues/835) (closed) | Fix reconnect equipment snapshot provenance and stale relay alerts | None; see concrete issue conditions | M |
| [#862](https://github.com/VerdifyConsultancy/verdify-platform/issues/862) (closed) | Lab publisher: warm-cache init rescans the public tree for ~5 min, pushing runs past the 10-min schedule and the 20-min freshness window | None; see concrete issue conditions | M |
| [#964](https://github.com/VerdifyConsultancy/verdify-platform/issues/964) (closed) | Recover failed production promotion and schedulable Lab/band refresh | None; see concrete issue conditions | L |
| [#965](https://github.com/VerdifyConsultancy/verdify-platform/issues/965) (closed) | Separate API grow-light command intent from observed equipment history | None; see concrete issue conditions | L |
| [#963](https://github.com/VerdifyConsultancy/verdify-platform/issues/963) (closed) | Bound moisture-exchange state and preserve structured consumer detail | [#390](https://github.com/VerdifyConsultancy/verdify-platform/issues/390) (open) | S |
| [#976](https://github.com/VerdifyConsultancy/verdify-platform/issues/976) (closed) | Qualify safe recovery of Lab publication receipt metadata | None; see concrete issue conditions | S |
| [#980](https://github.com/VerdifyConsultancy/verdify-platform/issues/980) (closed) | Resolve backup-exporter readiness timeouts without obscuring backup health | None; see concrete issue conditions | S |
| [#981](https://github.com/VerdifyConsultancy/verdify-platform/issues/981) (closed) | Restore truthful energy source freshness and trace stale meter input | None; see concrete issue conditions | S |
| [#802](https://github.com/VerdifyConsultancy/verdify-platform/issues/802) (closed) | Adopt the enforcing descheduler under manual Argo ownership | None; see concrete issue conditions | M |
| [#988](https://github.com/VerdifyConsultancy/verdify-platform/issues/988) (closed) | Restore public camera snapshots and qualify authenticated ingress | None; see concrete issue conditions | M |
| [#990](https://github.com/VerdifyConsultancy/verdify-platform/issues/990) (closed) | Delete verdify-prod qualification and restore-drill residue; add TTL to the restore-job renderer | None; see concrete issue conditions | M |
| [#991](https://github.com/VerdifyConsultancy/verdify-platform/issues/991) (closed) | Prune verdify-db-rehearsal run residue (non-data, D-04) | None; see concrete issue conditions | M |
| [#993](https://github.com/VerdifyConsultancy/verdify-platform/issues/993) (closed) | Adopt NetworkPolicy verdify-prod/allow-db-from-restore into the db-backup component | None; see concrete issue conditions | M |
| [#995](https://github.com/VerdifyConsultancy/verdify-platform/issues/995) (closed) | Adopt Namespace verdify-descheduler in place and record Verdify cluster-scoped objects | None; see concrete issue conditions | M |
| [#997](https://github.com/VerdifyConsultancy/verdify-platform/issues/997) (closed) | Keep Verdify dashboards single-sourced: delete stale ConfigMaps in ns verdify-platform and retire the monitoring-stack staged copy | None; see concrete issue conditions | M |
| [#998](https://github.com/VerdifyConsultancy/verdify-platform/issues/998) (closed) | Retire writer/DB watchdog CronJobs after monitoring-stack alerts are verified (R13) | None; see concrete issue conditions | M |
| [#999](https://github.com/VerdifyConsultancy/verdify-platform/issues/999) (closed) | Pin the remaining tag-only verdify-prod images by digest | [#994](https://github.com/VerdifyConsultancy/verdify-platform/issues/994) (closed) | M |
| [#1000](https://github.com/VerdifyConsultancy/verdify-platform/issues/1000) (closed) | Close health coverage gaps for Verdify items and alert the greenhouse Shelly meters (D-06) | None; see concrete issue conditions | M |
| [#1001](https://github.com/VerdifyConsultancy/verdify-platform/issues/1001) (open) | Finish retired Verdify namespace and hostname cleanup after owner handoffs | None; see concrete issue conditions | M |
| [#1002](https://github.com/VerdifyConsultancy/verdify-platform/issues/1002) (closed) | Stop anonymous internet users running ad-hoc SQL via graphs.verdify.ai Grafana | None; see concrete issue conditions | M |
| [#1003](https://github.com/VerdifyConsultancy/verdify-platform/issues/1003) (closed) | Route verdify-platform main changes, including prod release pins, through validated PRs | None; see concrete issue conditions | M |
| [#1004](https://github.com/VerdifyConsultancy/verdify-platform/issues/1004) (closed) | Bring kubectl-patched verdify-prod workloads back under Git ownership | None; see concrete issue conditions | M |
| [#1005](https://github.com/VerdifyConsultancy/verdify-platform/issues/1005) (closed) | Give verdify-lab real node redundancy instead of two replicas on one RWO volume | None; see concrete issue conditions | M |
| [#1006](https://github.com/VerdifyConsultancy/verdify-platform/issues/1006) (closed) | Scope the internet-facing verdify-traefik to verdify-prod: its ClusterRole can read all 936 Secrets in 84 namespaces | None; see concrete issue conditions | M |
| [#1007](https://github.com/VerdifyConsultancy/verdify-platform/issues/1007) (closed) | Put edge pre-auth in front of mcp.verdify.ai: one static Iris bearer token lets anyone on the internet call set_plan, set_tunable and slack_ops | None; see concrete issue conditions | M |
| [#1008](https://github.com/VerdifyConsultancy/verdify-platform/issues/1008) (closed) | Add rate limits and request bounds to anonymous api.verdify.ai: the public camera route triggers a fresh home-Frigate snapshot on every request | None; see concrete issue conditions | M |
| [#1009](https://github.com/VerdifyConsultancy/verdify-platform/issues/1009) (open) | Sign greenhouse ESP32 firmware and flash only reviewed archived builds: the device accepts any OTA image that carries the one static password | None; see concrete issue conditions | M |
| [#1010](https://github.com/VerdifyConsultancy/verdify-platform/issues/1010) (open) | Finish trusted client IP attribution for successful verdify-mcp calls | None; see concrete issue conditions | M |
| [#1011](https://github.com/VerdifyConsultancy/verdify-platform/issues/1011) (closed) | Bound verdify-mcp egress and stop mounting the default ServiceAccount token into the internet-facing MCP pods | None; see concrete issue conditions | M |

## C5 — Data/platform lead

| Issue / state | Outcome | Predecessor records / state | Effort |
|---|---|---|---|
| [#218](https://github.com/VerdifyConsultancy/verdify-platform/issues/218) (closed) | Durability program: recoverable backups, measured RPO/RTO and safe HA option | None; see concrete issue conditions | L |
| [#670](https://github.com/VerdifyConsultancy/verdify-platform/issues/670) (closed) | Make backup artifacts sufficient to restore roles, ownership and memberships safely | None; see concrete issue conditions | L |
| [#672](https://github.com/VerdifyConsultancy/verdify-platform/issues/672) (closed) | Make compressed-chunk ownership restoration a supported blocking rehearsal test | [#670](https://github.com/VerdifyConsultancy/verdify-platform/issues/670) (closed) | M |
| [#382](https://github.com/VerdifyConsultancy/verdify-platform/issues/382) (closed) | Persist ingestor queue and spool across restart and node loss | None; see concrete issue conditions | L |
| [#396](https://github.com/VerdifyConsultancy/verdify-platform/issues/396) (closed) | Design and rehearse CNPG alongside the live database on current storage | [#670](https://github.com/VerdifyConsultancy/verdify-platform/issues/670) (closed), [#672](https://github.com/VerdifyConsultancy/verdify-platform/issues/672) (closed) | L |
| [#245](https://github.com/VerdifyConsultancy/verdify-platform/issues/245) (closed) | Execute a separately bounded database cutover only after parity and rollback proof | [#382](https://github.com/VerdifyConsultancy/verdify-platform/issues/382) (closed), [#396](https://github.com/VerdifyConsultancy/verdify-platform/issues/396) (closed) | L |
| [#643](https://github.com/VerdifyConsultancy/verdify-platform/issues/643) (closed) | Extend least-privilege database roles beyond the already-required experiment boundary | [#670](https://github.com/VerdifyConsultancy/verdify-platform/issues/670) (closed) | L |
| [#49](https://github.com/VerdifyConsultancy/verdify-platform/issues/49) (closed) | Audit and close historical suppressed sensor alerts without false count-parity assumptions | None; see concrete issue conditions | S |
| [#977](https://github.com/VerdifyConsultancy/verdify-platform/issues/977) (closed) | Decide the database replica policy before November 12; keep optional reclaim separate | None; see concrete issue conditions | S |
| [#992](https://github.com/VerdifyConsultancy/verdify-platform/issues/992) (closed) | Retire the verdify-db-rehearsal experiment namespace now that no CNPG cutover is planned (D-04) | None; see concrete issue conditions | M |
| [#994](https://github.com/VerdifyConsultancy/verdify-platform/issues/994) (closed) | Record the verdify-db data PVC db-data-verdify-db-0 as protected | None; see concrete issue conditions | M |
| [#996](https://github.com/VerdifyConsultancy/verdify-platform/issues/996) (closed) | Remove laptop-root-era verdify-agent RBAC | None; see concrete issue conditions | M |
| [#1021](https://github.com/VerdifyConsultancy/verdify-platform/issues/1021) (closed) | Switch verdify-vision from shared Gemini credentials to Cortex longctx.mm | None; see concrete issue conditions | M |
| [#1022](https://github.com/VerdifyConsultancy/verdify-platform/issues/1022) (closed) | Make the ESP32 writer identity and egress exact without losing safe failover | None; see concrete issue conditions | M |

## C6 — Control lead

| Issue / state | Outcome | Predecessor records / state | Effort |
|---|---|---|---|
| [#359](https://github.com/VerdifyConsultancy/verdify-platform/issues/359) (open) | Control program: truthful crop corridors before evidence-led tuning | None; see concrete issue conditions | L |
| [#430](https://github.com/VerdifyConsultancy/verdify-platform/issues/430) (open) | Simplify firmware only while preserving autonomous control and observable truth | None; see concrete issue conditions | L |
| [#428](https://github.com/VerdifyConsultancy/verdify-platform/issues/428) (closed) | Diagnose heap/watchdog risk and qualify the actual last-good firmware floor | [#433](https://github.com/VerdifyConsultancy/verdify-platform/issues/433) (closed) | L |
| [#368](https://github.com/VerdifyConsultancy/verdify-platform/issues/368) (open) | Define shared sensor jump, flatline and contributor-integrity semantics | None; see concrete issue conditions | M |
| [#367](https://github.com/VerdifyConsultancy/verdify-platform/issues/367) (open) | Consolidate anti-chatter into one explicit dwell contract | [#390](https://github.com/VerdifyConsultancy/verdify-platform/issues/390) (open) | M |
| [#370](https://github.com/VerdifyConsultancy/verdify-platform/issues/370) (open) | Consolidate equipment conflict resolution into one pure arbitration function | [#367](https://github.com/VerdifyConsultancy/verdify-platform/issues/367) (open) | M |
| [#369](https://github.com/VerdifyConsultancy/verdify-platform/issues/369) (open) | Remove dead tunables and code from a verified consumer inventory | [#390](https://github.com/VerdifyConsultancy/verdify-platform/issues/390) (open) | L |
| [#324](https://github.com/VerdifyConsultancy/verdify-platform/issues/324) (closed) | Extend zonal band lineage compatibly for future deterministic control | [#424](https://github.com/VerdifyConsultancy/verdify-platform/issues/424) (closed), [#371](https://github.com/VerdifyConsultancy/verdify-platform/issues/371) (closed) | M |
| [#410](https://github.com/VerdifyConsultancy/verdify-platform/issues/410) (closed) | Measure realized solar-night dry-out before changing the controller | [#371](https://github.com/VerdifyConsultancy/verdify-platform/issues/371) (closed), [#419](https://github.com/VerdifyConsultancy/verdify-platform/issues/419) (closed), [#424](https://github.com/VerdifyConsultancy/verdify-platform/issues/424) (closed) | L |
| [#378](https://github.com/VerdifyConsultancy/verdify-platform/issues/378) (closed) | Decide corridor widths from fixed-target crop and resource evidence | [#371](https://github.com/VerdifyConsultancy/verdify-platform/issues/371) (closed), [#785](https://github.com/VerdifyConsultancy/verdify-platform/issues/785) (closed) | M |
| [#361](https://github.com/VerdifyConsultancy/verdify-platform/issues/361) (closed) | Reassess diurnal anchors only after solar parity and measured corridor evidence | [#378](https://github.com/VerdifyConsultancy/verdify-platform/issues/378) (closed), [#410](https://github.com/VerdifyConsultancy/verdify-platform/issues/410) (closed) | M |
| [#214](https://github.com/VerdifyConsultancy/verdify-platform/issues/214) (closed) | Prove crop-deviation trigger → valid plan on the sole operational planner path | [#427](https://github.com/VerdifyConsultancy/verdify-platform/issues/427) (closed), [#780](https://github.com/VerdifyConsultancy/verdify-platform/issues/780) (closed) | M |
| [#350](https://github.com/VerdifyConsultancy/verdify-platform/issues/350) (closed) | Irrigation program: topology truth, overwatering protection and commissioned wall feed | None; see concrete issue conditions | L |
| [#434](https://github.com/VerdifyConsultancy/verdify-platform/issues/434) (closed) | Enforce center-only climate mist and commissioned wall-only fertigation | [#299](https://github.com/VerdifyConsultancy/verdify-platform/issues/299) (open) | L |
| [#299](https://github.com/VerdifyConsultancy/verdify-platform/issues/299) (open) | Preserve center-mister re-fire protection through topology and reset recovery | [#390](https://github.com/VerdifyConsultancy/verdify-platform/issues/390) (open) | M |
| [#297](https://github.com/VerdifyConsultancy/verdify-platform/issues/297) (open) | Add saturation alerts and a commissioned dispatcher-side drip-skip loop | [#45](https://github.com/VerdifyConsultancy/verdify-platform/issues/45) (open), [#398](https://github.com/VerdifyConsultancy/verdify-platform/issues/398) (open) | M |
| [#296](https://github.com/VerdifyConsultancy/verdify-platform/issues/296) (closed) | Consider slow-hysteresis firmware irrigation feedback only after dispatcher evidence | [#297](https://github.com/VerdifyConsultancy/verdify-platform/issues/297) (open), [#434](https://github.com/VerdifyConsultancy/verdify-platform/issues/434) (closed), [#45](https://github.com/VerdifyConsultancy/verdify-platform/issues/45) (open) | L |

## C7 — Operator with data/control lead

| Issue / state | Outcome | Predecessor records / state | Effort |
|---|---|---|---|
| [#16](https://github.com/VerdifyConsultancy/verdify-platform/issues/16) (open) | Hardware program: commission only the sensing and equipment justified by evidence | None; see concrete issue conditions | L |
| [#751](https://github.com/VerdifyConsultancy/verdify-platform/issues/751) (open) | Replace the failed south climate probe and diagnose hydro telemetry in a bounded window | None; see concrete issue conditions | M |
| [#298](https://github.com/VerdifyConsultancy/verdify-platform/issues/298) (open) | Rebaseline actual crop, pot and probe topology before feedback control | None; see concrete issue conditions | M |
| [#398](https://github.com/VerdifyConsultancy/verdify-platform/issues/398) (open) | Replace stale soil threshold seeds with commissioned crop/substrate truth | [#298](https://github.com/VerdifyConsultancy/verdify-platform/issues/298) (open) | M |
| [#45](https://github.com/VerdifyConsultancy/verdify-platform/issues/45) (open) | Commission soil and runoff feedback before enabling irrigation decisions | [#398](https://github.com/VerdifyConsultancy/verdify-platform/issues/398) (open), [#298](https://github.com/VerdifyConsultancy/verdify-platform/issues/298) (open) | L |
| [#51](https://github.com/VerdifyConsultancy/verdify-platform/issues/51) (open) | Calibrate CO2 against a real reference and publish measurement uncertainty | None; see concrete issue conditions | S |
| [#52](https://github.com/VerdifyConsultancy/verdify-platform/issues/52) (open) | Define species-appropriate seasonal dormancy care from operator observations | [#298](https://github.com/VerdifyConsultancy/verdify-platform/issues/298) (open) | M |
| [#412](https://github.com/VerdifyConsultancy/verdify-platform/issues/412) (closed) | Close the seasonal door screen only when measured night conditions justify it | None; see concrete issue conditions | S |

## C8 — Research/platform lead

| Issue / state | Outcome | Predecessor records / state | Effort |
|---|---|---|---|
| [#174](https://github.com/VerdifyConsultancy/verdify-platform/issues/174) (closed) | Rehome only current Verdify admin surfaces to global SSO, fail closed | None; see concrete issue conditions | M |
| [#14](https://github.com/VerdifyConsultancy/verdify-platform/issues/14) (closed) | Twin program: qualify declared oracle coverage before using divergence as a gate | None; see concrete issue conditions | L |
| [#31](https://github.com/VerdifyConsultancy/verdify-platform/issues/31) (open) | Close twin setpoint and actuator coverage gaps with generated consumer truth | None; see concrete issue conditions | L |
| [#606](https://github.com/VerdifyConsultancy/verdify-platform/issues/606) (closed) | Register the twin build profile only through its owning fleet contract | None; see concrete issue conditions | M |
| [#638](https://github.com/VerdifyConsultancy/verdify-platform/issues/638) (closed) | Unify future manifest/vector wire schema and content identity offline | None; see concrete issue conditions | L |
| [#586](https://github.com/VerdifyConsultancy/verdify-platform/issues/586) (closed) | Qualify a future atomic firmware policy engine with crash-safe recovery | [#638](https://github.com/VerdifyConsultancy/verdify-platform/issues/638) (closed), [#390](https://github.com/VerdifyConsultancy/verdify-platform/issues/390) (open), [#785](https://github.com/VerdifyConsultancy/verdify-platform/issues/785) (closed) | L |
| [#786](https://github.com/VerdifyConsultancy/verdify-platform/issues/786) (closed) | Decide whether a deterministic forecast comparator merits a new study | [#780](https://github.com/VerdifyConsultancy/verdify-platform/issues/780) (closed) | L |
| [#379](https://github.com/VerdifyConsultancy/verdify-platform/issues/379) (closed) | Gate grey-box forecasting and MPC on validated response models and simpler baselines | [#371](https://github.com/VerdifyConsultancy/verdify-platform/issues/371) (closed), [#419](https://github.com/VerdifyConsultancy/verdify-platform/issues/419) (closed) | L |
| [#787](https://github.com/VerdifyConsultancy/verdify-platform/issues/787) (closed) | Decide and scope a measured heating/overnight/resource study | [#781](https://github.com/VerdifyConsultancy/verdify-platform/issues/781) (closed) | L |

## VD — Dashboard/irrigation lead

| Issue / state | Outcome | Predecessor records / state | Effort |
|---|---|---|---|
| [#1120](https://github.com/VerdifyConsultancy/verdify-platform/issues/1120) (open) | Program: dashboard.verdify.ai and the irrigation program | None; see concrete issue conditions | M |
| [#1125](https://github.com/VerdifyConsultancy/verdify-platform/issues/1125) (open) | Build the form controls, edit drawer, water confirm dialog and write hooks | [#1143](https://github.com/VerdifyConsultancy/verdify-platform/issues/1143) (open), [#1135](https://github.com/VerdifyConsultancy/verdify-platform/issues/1135) (open), [#1164](https://github.com/VerdifyConsultancy/verdify-platform/issues/1164) (open) | M |
| [#1130](https://github.com/VerdifyConsultancy/verdify-platform/issues/1130) (open) | Build the Graphs screen (SCR-02) with the metric picker and the daily scorecard | None; see concrete issue conditions | L |
| [#1132](https://github.com/VerdifyConsultancy/verdify-platform/issues/1132) (open) | Build the Settings screen (SCR-05) with the edit drawer | None; see concrete issue conditions | L |
| [#1135](https://github.com/VerdifyConsultancy/verdify-platform/issues/1135) (open) | Build the Water screen (SCR-04): read views and Stop all water | None; see concrete issue conditions | L |
| [#1143](https://github.com/VerdifyConsultancy/verdify-platform/issues/1143) (open) | Ship R1: put the read-only dashboard live at dashboard.verdify.ai | None; see concrete issue conditions | M |
| [#1161](https://github.com/VerdifyConsultancy/verdify-platform/issues/1161) (open) | Rework the mist path: membership, scheduled pulses, occupancy, survival fog and the D-IRP-19 watchdog | None; see concrete issue conditions | L |
| [#1162](https://github.com/VerdifyConsultancy/verdify-platform/issues/1162) (open) | Remove the Home Assistant buttons, add manual_override and retire the old irrigation entities | None; see concrete issue conditions | L |
| [#1164](https://github.com/VerdifyConsultancy/verdify-platform/issues/1164) (open) | Ship R3: deploy the irrigation platform and flash the engine firmware | [#1161](https://github.com/VerdifyConsultancy/verdify-platform/issues/1161) (open), [#1162](https://github.com/VerdifyConsultancy/verdify-platform/issues/1162) (open) | M |
| [#1165](https://github.com/VerdifyConsultancy/verdify-platform/issues/1165) (open) | Bring the clean lines online: L01 first, then L04, L07 and L09 | [#1329](https://github.com/VerdifyConsultancy/verdify-platform/issues/1329) (open) | S |
| [#1166](https://github.com/VerdifyConsultancy/verdify-platform/issues/1166) (open) | Bring the fertilizer lines online: L02 with L03, then L08 and L10 | [#1165](https://github.com/VerdifyConsultancy/verdify-platform/issues/1165) (open), [#1329](https://github.com/VerdifyConsultancy/verdify-platform/issues/1329) (open) | M |
| [#1181](https://github.com/VerdifyConsultancy/verdify-platform/issues/1181) (open) | Ship R1.1: put every read screen, the water reads and the MCP irrigation reads live | None; see concrete issue conditions | M |
| [#1183](https://github.com/VerdifyConsultancy/verdify-platform/issues/1183) (open) | Read the YINMIK hydroponic probe directly, without Home Assistant | None; see concrete issue conditions | M |
| [#1184](https://github.com/VerdifyConsultancy/verdify-platform/issues/1184) (open) | Add a PAR light meter: hardware, firmware, ingest and DLI | None; see concrete issue conditions | L |
| [#1186](https://github.com/VerdifyConsultancy/verdify-platform/issues/1186) (open) | Ship R4: put the Next screens live | [#1300](https://github.com/VerdifyConsultancy/verdify-platform/issues/1300) (open) | M |
| [#1300](https://github.com/VerdifyConsultancy/verdify-platform/issues/1300) (open) | Back to automatic leaves a grow light off its intent when the Equipment page closes before the write confirms | None; see concrete issue conditions | S |
| [#1322](https://github.com/VerdifyConsultancy/verdify-platform/issues/1322) (open) | dashboard: chart corner labels overlap the band edge; stray separator on the phone Now status line | [#1354](https://github.com/VerdifyConsultancy/verdify-platform/issues/1354) (open) | M |

## W5 — Dashboard wave-2 lead

| Issue / state | Outcome | Predecessor records / state | Effort |
|---|---|---|---|
| [#1325](https://github.com/VerdifyConsultancy/verdify-platform/issues/1325) (open) | [Epic] Dashboard wave 2: Jason's acceptance feedback on the redesign (2026-11-05) | None; see concrete issue conditions | M |
| [#1327](https://github.com/VerdifyConsultancy/verdify-platform/issues/1327) (open) | Model plantings on the location tree and serve them from the operator API | [#1330](https://github.com/VerdifyConsultancy/verdify-platform/issues/1330) (open) | M |
| [#1328](https://github.com/VerdifyConsultancy/verdify-platform/issues/1328) (open) | Let agents record, move and harvest plantings through MCP | [#1330](https://github.com/VerdifyConsultancy/verdify-platform/issues/1330) (open) | M |
| [#1329](https://github.com/VerdifyConsultancy/verdify-platform/issues/1329) (open) | Reconcile greenhouse locations and current plantings with the physical greenhouse and lab.verdify.ai | None; see concrete issue conditions | M |
| [#1330](https://github.com/VerdifyConsultancy/verdify-platform/issues/1330) (open) | Add a Crops section: hydro slot grid, wall shelves and planting history | None; see concrete issue conditions | M |
| [#1332](https://github.com/VerdifyConsultancy/verdify-platform/issues/1332) (open) | Explain every setting: what it does to the controller and what raising or lowering it does | None; see concrete issue conditions | M |
| [#1333](https://github.com/VerdifyConsultancy/verdify-platform/issues/1333) (open) | Define the Settings filters and states in the UI, and explain or route around read-only rows | None; see concrete issue conditions | M |
| [#1334](https://github.com/VerdifyConsultancy/verdify-platform/issues/1334) (open) | Research the band math and design a plant-agnostic band editor with a live 24 h preview | None; see concrete issue conditions | M |
| [#1335](https://github.com/VerdifyConsultancy/verdify-platform/issues/1335) (open) | Give each light circuit a findable Auto / On / Off control that holds until released | None; see concrete issue conditions | M |
| [#1336](https://github.com/VerdifyConsultancy/verdify-platform/issues/1336) (open) | Model one physical line with clean and fertilized schedules; give each mister its own line | None; see concrete issue conditions | M |
| [#1337](https://github.com/VerdifyConsultancy/verdify-platform/issues/1337) (open) | Redraw Now's band gauges as one track with a green band box, and say in words when a zone uses its peers | None; see concrete issue conditions | M |
| [#1338](https://github.com/VerdifyConsultancy/verdify-platform/issues/1338) (open) | Keep the Now headline steady and move the out-of-band detail into a plain line below it | None; see concrete issue conditions | M |
| [#1339](https://github.com/VerdifyConsultancy/verdify-platform/issues/1339) (open) | Replace codes on Now with plain labels and add a shared glossary (SAF-4, line IDs, trigger codes) | None; see concrete issue conditions | M |
| [#1340](https://github.com/VerdifyConsultancy/verdify-platform/issues/1340) (open) | Give Running and next room for its labels and add Main and Grow light rows | None; see concrete issue conditions | M |
| [#1341](https://github.com/VerdifyConsultancy/verdify-platform/issues/1341) (open) | Show line names everywhere and remove the L01/L02 relay codes from the UI | None; see concrete issue conditions | M |
| [#1342](https://github.com/VerdifyConsultancy/verdify-platform/issues/1342) (open) | Add Main and Grow light controls to Now's quick actions | None; see concrete issue conditions | M |
| [#1343](https://github.com/VerdifyConsultancy/verdify-platform/issues/1343) (open) | Allow a one-time run of any line, misters included | None; see concrete issue conditions | M |
| [#1344](https://github.com/VerdifyConsultancy/verdify-platform/issues/1344) (open) | Explain VPD control, Manual and Fallback on Water; show VPD control only on misters | None; see concrete issue conditions | M |
| [#1345](https://github.com/VerdifyConsultancy/verdify-platform/issues/1345) (open) | Remove plant-specific copy and make soil-moisture targets configurable | None; see concrete issue conditions | M |
| [#1346](https://github.com/VerdifyConsultancy/verdify-platform/issues/1346) (open) | Write a greenhouse snapshot with the local Cortex Qwen model and show it in the Now hero | None; see concrete issue conditions | M |
| [#1347](https://github.com/VerdifyConsultancy/verdify-platform/issues/1347) (open) | Make the schedule timeline readable: daylight band on every lane, visible legend, one lane per physical line | None; see concrete issue conditions | M |
| [#1348](https://github.com/VerdifyConsultancy/verdify-platform/issues/1348) (open) | Separate irrigation runs from VPD mist and fog everywhere | None; see concrete issue conditions | M |
| [#1349](https://github.com/VerdifyConsultancy/verdify-platform/issues/1349) (open) | Redesign Water cards for clear line and group delineation and replace the SAF-4 label | None; see concrete issue conditions | M |
| [#1350](https://github.com/VerdifyConsultancy/verdify-platform/issues/1350) (closed) | Replace the Hermes/OpenAI planner with one deterministic call to the local Cortex Qwen model | None; see concrete issue conditions | M |
| [#1351](https://github.com/VerdifyConsultancy/verdify-platform/issues/1351) (open) | Audit and remove Verdify's external dependencies (local-only goal) | [#1352](https://github.com/VerdifyConsultancy/verdify-platform/issues/1352) (open) | M |
| [#1352](https://github.com/VerdifyConsultancy/verdify-platform/issues/1352) (open) | Decommission Slack from Verdify: alerts on the dashboard, paging through Alertmanager | None; see concrete issue conditions | M |
| [#1353](https://github.com/VerdifyConsultancy/verdify-platform/issues/1353) (open) | Show 72 h of history and 72 h of forecast on Graphs by default | None; see concrete issue conditions | M |
| [#1354](https://github.com/VerdifyConsultancy/verdify-platform/issues/1354) (open) | Move chart labels out of the plots and use one chart vocabulary across pages | None; see concrete issue conditions | M |
| [#1355](https://github.com/VerdifyConsultancy/verdify-platform/issues/1355) (open) | Make event markers on Graphs show what each event is and what it touched | None; see concrete issue conditions | M |
| [#1356](https://github.com/VerdifyConsultancy/verdify-platform/issues/1356) (open) | Redesign the equipment lanes: shared time axis, solid bars, grouped by system | None; see concrete issue conditions | M |
| [#1357](https://github.com/VerdifyConsultancy/verdify-platform/issues/1357) (open) | Add a Lighting tab to Graphs: light state, outdoor lux and thresholds, light minutes | None; see concrete issue conditions | M |
| [#1358](https://github.com/VerdifyConsultancy/verdify-platform/issues/1358) (open) | Research and fix band compliance: grade time in band against the band the controller served | None; see concrete issue conditions | M |
| [#1360](https://github.com/VerdifyConsultancy/verdify-platform/issues/1360) (open) | Run an AI-planner-versus-baseline study end to end by extending the experiment orchestrator | [#1365](https://github.com/VerdifyConsultancy/verdify-platform/issues/1365) (open), [#1389](https://github.com/VerdifyConsultancy/verdify-platform/issues/1389) (open) | M |
| [#1361](https://github.com/VerdifyConsultancy/verdify-platform/issues/1361) (open) | Add a Planner section: plan history, cost, experiments, arm calendar and results | [#1360](https://github.com/VerdifyConsultancy/verdify-platform/issues/1360) (open) | M |
| [#1363](https://github.com/VerdifyConsultancy/verdify-platform/issues/1363) (open) | Pull every Verdify image through the local registry (no direct Docker Hub/ghcr pulls) | None; see concrete issue conditions | M |
| [#1364](https://github.com/VerdifyConsultancy/verdify-platform/issues/1364) (open) | Use the estate's local NTP for the greenhouse controller instead of pool.ntp.org | None; see concrete issue conditions | M |
| [#1365](https://github.com/VerdifyConsultancy/verdify-platform/issues/1365) (open) | Research sprint: a VPD band the greenhouse can actually hold, and how to improve VPD control | None; see concrete issue conditions | M |
| [#1366](https://github.com/VerdifyConsultancy/verdify-platform/issues/1366) (closed) | Research the planner's 60K-token input pack: what the Cortex planner must see to plan well | [#1350](https://github.com/VerdifyConsultancy/verdify-platform/issues/1350) (closed) | M |
| [#1370](https://github.com/VerdifyConsultancy/verdify-platform/issues/1370) (open) | Turn the lights on when the cameras see someone in the greenhouse | None; see concrete issue conditions | M |

## OPS — Platform lead

| Issue / state | Outcome | Predecessor records / state | Effort |
|---|---|---|---|
| [#1113](https://github.com/VerdifyConsultancy/verdify-platform/issues/1113) (open) | security: grafana-image-renderer chromium CVE-2026-118333/118337 - bump when upstream ships chromium >= 156.0.8078.73 | None; see concrete issue conditions | M |
| [#1118](https://github.com/VerdifyConsultancy/verdify-platform/issues/1118) (open) | verdify-vision crop-health analysis step fails (exit 1) after B-POWER; its stderr is discarded | None; see concrete issue conditions | M |
| [#1271](https://github.com/VerdifyConsultancy/verdify-platform/issues/1271) (open) | firmware: give sensor_degraded a boot grace before timed L04 fallback slots return | None; see concrete issue conditions | M |
| [#1277](https://github.com/VerdifyConsultancy/verdify-platform/issues/1277) (open) | c1 wire validation: move the firmware pin to the engine firmware | None; see concrete issue conditions | M |
| [#1283](https://github.com/VerdifyConsultancy/verdify-platform/issues/1283) (open) | v_water_meter_daily may read a 25-hour day as complete with its last hour missing | None; see concrete issue conditions | M |
| [#1312](https://github.com/VerdifyConsultancy/verdify-platform/issues/1312) (open) | firmware-replay-diff: rounding lets up to 14 divergent rows pass at THRESHOLD_PCT=0 | None; see concrete issue conditions | M |
| [#1313](https://github.com/VerdifyConsultancy/verdify-platform/issues/1313) (open) | firmware: runtime and drip accounting after an aborted OTA upload | None; see concrete issue conditions | M |
| [#1389](https://github.com/VerdifyConsultancy/verdify-platform/issues/1389) (open) | experiment-v2 lifecycle/freezer/selector CPU-throttled >50% (limits 250-500m, requests 10-25m) | None; see concrete issue conditions | M |
| [#1397](https://github.com/VerdifyConsultancy/verdify-platform/issues/1397) (open) | Move CI, image builds and pin automation to GitHub Actions on self-hosted runners | None; see concrete issue conditions | M |
