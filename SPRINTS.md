# Upcoming sprints and ongoing work

## Current execution plan

Reviewed **2026-10-20T15:31:29.571834+00:00** against source `01735457467fa1fd8918641dfa48cd583e092896`. [planning/reviews/2026-10-20-sprint-audit/README.md](planning/reviews/2026-10-20-sprint-audit/README.md) records current source, GitHub, runtime and evidence limits. All **62 open issues** have one allocation and accountable role. Planning capacity: **40 engineering hours/week**; ranges are estimates, not calendar promises.

| Pull set | Kind | Allocated hours | Capacity hours | Issues |
|---|---|---|---|---|
| NEXT — S6 — Public exposure and safe control preparation (October 21–27, 2026) | sprint | 25–34 | 40 | [#297](https://github.com/VerdifyConsultancy/verdify-platform/issues/297), [#994](https://github.com/VerdifyConsultancy/verdify-platform/issues/994), [#1002](https://github.com/VerdifyConsultancy/verdify-platform/issues/1002), [#1006](https://github.com/VerdifyConsultancy/verdify-platform/issues/1006), [#1007](https://github.com/VerdifyConsultancy/verdify-platform/issues/1007), [#1008](https://github.com/VerdifyConsultancy/verdify-platform/issues/1008) |
| ALTERNATE — Ready replacements and natural observations | conditional | Unestimated | Not timeboxed | [#298](https://github.com/VerdifyConsultancy/verdify-platform/issues/298), [#386](https://github.com/VerdifyConsultancy/verdify-platform/issues/386), [#410](https://github.com/VerdifyConsultancy/verdify-platform/issues/410), [#981](https://github.com/VerdifyConsultancy/verdify-platform/issues/981), [#993](https://github.com/VerdifyConsultancy/verdify-platform/issues/993), [#996](https://github.com/VerdifyConsultancy/verdify-platform/issues/996), [#1010](https://github.com/VerdifyConsultancy/verdify-platform/issues/1010) |
| BACKLOG — Sequenced follow-through and eligible backlog | conditional | Unestimated | Not timeboxed | [#31](https://github.com/VerdifyConsultancy/verdify-platform/issues/31), [#45](https://github.com/VerdifyConsultancy/verdify-platform/issues/45), [#51](https://github.com/VerdifyConsultancy/verdify-platform/issues/51), [#52](https://github.com/VerdifyConsultancy/verdify-platform/issues/52), [#218](https://github.com/VerdifyConsultancy/verdify-platform/issues/218), [#299](https://github.com/VerdifyConsultancy/verdify-platform/issues/299), [#367](https://github.com/VerdifyConsultancy/verdify-platform/issues/367), [#368](https://github.com/VerdifyConsultancy/verdify-platform/issues/368), [#369](https://github.com/VerdifyConsultancy/verdify-platform/issues/369), [#370](https://github.com/VerdifyConsultancy/verdify-platform/issues/370), [#378](https://github.com/VerdifyConsultancy/verdify-platform/issues/378), [#390](https://github.com/VerdifyConsultancy/verdify-platform/issues/390), [#398](https://github.com/VerdifyConsultancy/verdify-platform/issues/398), [#412](https://github.com/VerdifyConsultancy/verdify-platform/issues/412), [#434](https://github.com/VerdifyConsultancy/verdify-platform/issues/434), [#587](https://github.com/VerdifyConsultancy/verdify-platform/issues/587), [#588](https://github.com/VerdifyConsultancy/verdify-platform/issues/588), [#639](https://github.com/VerdifyConsultancy/verdify-platform/issues/639), [#640](https://github.com/VerdifyConsultancy/verdify-platform/issues/640), [#641](https://github.com/VerdifyConsultancy/verdify-platform/issues/641), [#642](https://github.com/VerdifyConsultancy/verdify-platform/issues/642), [#749](https://github.com/VerdifyConsultancy/verdify-platform/issues/749), [#751](https://github.com/VerdifyConsultancy/verdify-platform/issues/751), [#782](https://github.com/VerdifyConsultancy/verdify-platform/issues/782), [#783](https://github.com/VerdifyConsultancy/verdify-platform/issues/783), [#784](https://github.com/VerdifyConsultancy/verdify-platform/issues/784), [#785](https://github.com/VerdifyConsultancy/verdify-platform/issues/785), [#963](https://github.com/VerdifyConsultancy/verdify-platform/issues/963), [#990](https://github.com/VerdifyConsultancy/verdify-platform/issues/990), [#991](https://github.com/VerdifyConsultancy/verdify-platform/issues/991), [#992](https://github.com/VerdifyConsultancy/verdify-platform/issues/992), [#995](https://github.com/VerdifyConsultancy/verdify-platform/issues/995), [#997](https://github.com/VerdifyConsultancy/verdify-platform/issues/997), [#998](https://github.com/VerdifyConsultancy/verdify-platform/issues/998), [#999](https://github.com/VerdifyConsultancy/verdify-platform/issues/999), [#1000](https://github.com/VerdifyConsultancy/verdify-platform/issues/1000), [#1001](https://github.com/VerdifyConsultancy/verdify-platform/issues/1001), [#1003](https://github.com/VerdifyConsultancy/verdify-platform/issues/1003), [#1004](https://github.com/VerdifyConsultancy/verdify-platform/issues/1004), [#1005](https://github.com/VerdifyConsultancy/verdify-platform/issues/1005), [#1009](https://github.com/VerdifyConsultancy/verdify-platform/issues/1009), [#1011](https://github.com/VerdifyConsultancy/verdify-platform/issues/1011) |
| ROLLUP — Program ownership | rollup | Unestimated | Not timeboxed | [#16](https://github.com/VerdifyConsultancy/verdify-platform/issues/16), [#75](https://github.com/VerdifyConsultancy/verdify-platform/issues/75), [#350](https://github.com/VerdifyConsultancy/verdify-platform/issues/350), [#359](https://github.com/VerdifyConsultancy/verdify-platform/issues/359), [#430](https://github.com/VerdifyConsultancy/verdify-platform/issues/430), [#581](https://github.com/VerdifyConsultancy/verdify-platform/issues/581), [#775](https://github.com/VerdifyConsultancy/verdify-platform/issues/775) |

Sprint allocations may cover bounded preparation. Full issue acceptance still depends on its predecessors, current evidence and declared physical or seasonal conditions. C0–C8 remain stage contracts; closed historical issues remain visible in the stage views below.

## NEXT — S6 — Public exposure and safe control preparation (October 21–27, 2026)

Kind: sprint. Capacity: 40 engineering hours. Goal: Reduce verified public attack surface and protect production data; deliver an alert-only saturation evaluator. Six bounded packets total 25–34 engineering hours with at least 6 hours reserve. #1007 contributes discovery only, not its blocked cutover.

Exit: Issue-specific live acceptance for delivered security/data changes, alert-only evaluator with disabled uncommissioned inputs, and an attributed MCP client or explicit evidence gap. Whole-issue closure only where all current criteria pass.

| Issue | Accountable role | Engineering hours | Next action |
|---|---|---|---|
| [#297](https://github.com/VerdifyConsultancy/verdify-platform/issues/297) | Control lead | 4–6 | Build only the sustained-saturation alert evaluator and missing/stale/persistence/recovery negative fixtures. Keep configuration unavailable/disabled without measured targets. #45 still blocks drip skip/shrink and full acceptance; no irrigation actuation in this packet. |
| [#994](https://github.com/VerdifyConsultancy/verdify-platform/issues/994) | Platform/security lead | 1–2 | Add/read back Prune=false,Delete=false protection on the exact production DB PVC; update owner/central records in the same change set, verify unchanged DB pod UID and no workload diff. Complete before #999 DB pin. |
| [#1002](https://github.com/VerdifyConsultancy/verdify-platform/issues/1002) | Platform/security lead | 8–10 | Implement the approved public-dashboard/curated-public-data solution while preserving anonymous lab graphs and PNG fallbacks. Reject arbitrary anonymous SQL, constrain DB grants/statement timeout, and verify actual anonymous embeds over the public path. Do not simply disable the public graphs. |
| [#1006](https://github.com/VerdifyConsultancy/verdify-platform/issues/1006) | Platform/security lead | 6–8 | Replace broad Traefik ClusterRole/Binding with least-privilege namespaced RBAC; deliver the Role first, then remove exact old grants and central ownership rows. Prove denied Secret access, working routes and one hour without required-resource RBAC denials. |
| [#1007](https://github.com/VerdifyConsultancy/verdify-platform/issues/1007) | Platform/security lead | 1 | One-hour sprint packet: inspect existing access logs for the external client and record a supported identity or an explicit attribution gap. Full cutover waits on network-infra#752 and Jason confirming the identified client before service-token issuance. Keep the route and ClusterIP; use separate client audience, approved Iris rotation and updated probe expectations after the edge is ready. |
| [#1008](https://github.com/VerdifyConsultancy/verdify-platform/issues/1008) | Platform/security lead | 5–7 | Add query maxima, bounded camera cache and fetch concurrency, then trusted-client rate/in-flight limits. Test limits with mocked NVR/DB fixtures first; verify deployed bounds with bounded probes. Edge policy and Turnstile custody remain owner follow-through, not blockers to app work. |

## ALTERNATE — Ready replacements and natural observations

Kind: conditional. Goal: Substitute a ready bounded packet when committed scope is genuinely blocked; do not add it on top of 40 hours. Meter adoption leads the replacement queue. Field work requires a real scheduled visit.

Exit: A replacement has a bounded estimate and actual input/owner path; no repeated accepted proof or invented physical evidence.

| Issue | Accountable role | Engineering hours | Next action |
|---|---|---|---|
| [#298](https://github.com/VerdifyConsultancy/verdify-platform/issues/298) | Operator with data/control lead | Unestimated | Schedule one shared greenhouse field worksheet with Jason: actual crop/pot/substrate/probe/relay/plumbing mapping, including explicitly unassigned positions. DR-45 revision makes visits possible; it supersedes the earlier no-visit comment. No reference CO2 sensor is needed for this survey; no irrigation enablement. |
| [#386](https://github.com/VerdifyConsultancy/verdify-platform/issues/386) | Platform lead | Unestimated | Prepare a passive per-circuit solar-boundary query and observe an eligible natural min-on/cutoff event with timestamps and stop reasons. Source and causal regressions are delivered; do not force relay changes merely to close the issue. |
| [#410](https://github.com/VerdifyConsultancy/verdify-platform/issues/410) | Control lead | Unestimated | Record one prospective complete daytime window and natural ordinary planner consumption/cost with the existing installed getter and scheduled paths. Preserve missing-minute NULLs, ineffective/insufficient outcomes and original gaps; no historical interpolation, forced provider call or controller change. |
| [#981](https://github.com/VerdifyConsultancy/verdify-platform/issues/981) | Data/platform lead | Unestimated | Consume accepted HA#878/PR#894 in an explicit new same-response Modbus source-time+signed-power revision. Preserve old-row meaning, source/ingest clocks, stable-zero advancement, reversed CT and stale/missing/future/nonfinite/disconnected denial. Verify deployed consumer and one later natural source/DB comparison. |
| [#993](https://github.com/VerdifyConsultancy/verdify-platform/issues/993) | Platform/security lead | Unestimated | Adopt allow-db-from-restore with identical live spec into db-backup; verify Argo tracking, no widened access and supported restore connectivity. Preserve the policy during #990 cleanup. |
| [#996](https://github.com/VerdifyConsultancy/verdify-platform/issues/996) | Platform/security lead | Unestimated | Inspect binding subjects and live consumers before removing exactly the four obsolete RBAC objects; preserve a metadata-only inverse/readback. Stop for any active consumer; token revocation is not implied. |
| [#1010](https://github.com/VerdifyConsultancy/verdify-platform/issues/1010) | Platform/security lead | Unestimated | Add secret-free structured allow/result events with audience/tool/write-class and trusted-proxy-only real-client attribution; verify read/write success and forged-header negative cases. Use for #1007 discovery if existing access logs cannot establish identity; do not infer a client from a shared token or pod IP. |

## BACKLOG — Sequenced follow-through and eligible backlog

Kind: conditional. Goal: Retain every nonselected issue with its explicit native, physical, input, owner or decision boundary; source-ready does not mean fully closable.

Exit: Pull when named prerequisites and capacity exist. No OTA, retirement, launch or owner-lane mutation is performed by this audit.

| Issue | Accountable role | Engineering hours | Next action |
|---|---|---|---|
| [#31](https://github.com/VerdifyConsultancy/verdify-platform/issues/31) | Research/platform lead | Unestimated | No concrete twin oracle question is selected. Retain absorbed #14/#606 immutable image/runtime/registry and consumer-matrix obligations. Pull only a selected useful oracle with self-diff PASS and injected-divergence FAIL; not a current pilot prerequisite. |
| [#45](https://github.com/VerdifyConsultancy/verdify-platform/issues/45) | Operator with data/control lead | Unestimated | After #398, commission every required soil/runoff channel against actual identity/calibration, then run supported feedback checks with before/after warning evidence. Unknown inputs cannot enable drip feedback or fertilizer. |
| [#51](https://github.com/VerdifyConsultancy/verdify-platform/issues/51) | Operator with data/control lead | Unestimated | Obtain a real reference NDIR meter, then paired and held-out samples under a predeclared tolerance. Visits are possible; only the missing reference sensor blocks calibration. Retain uncalibrated status and do not fabricate coefficients. |
| [#52](https://github.com/VerdifyConsultancy/verdify-platform/issues/52) | Operator with data/control lead | Unestimated | Use authentic species/maturity observations from the #298 visit to record no change or one reversible justified dormancy-care decision. DR-45 supersedes the old no-visit hold; actual observations remain missing. |
| [#218](https://github.com/VerdifyConsultancy/verdify-platform/issues/218) | Data/platform lead | Unestimated | Refresh the single-StatefulSet failure-domain and restore/RTO plan after the no-CNPG decision. Prepare placement removal and a bounded reattach drill; coordinate node labels/capacity with proxmox-infrastructure. A cordon/pod deletion is a later attended operation, not an audit action. |
| [#299](https://github.com/VerdifyConsultancy/verdify-platform/issues/299) | Control lead | Unestimated | Reuse existing pulse logic and add only missing exact-boundary/manual-origin/restart min-off proof. Full re-fire/topology validation waits for #390 candidate/recovery evidence; retain requested pulse duration. |
| [#367](https://github.com/VerdifyConsultancy/verdify-platform/issues/367) | Control lead | Unestimated | Document remaining single dwell authority/safety exceptions and include the merged eight-limit per-tick correction in #390. Reuse 84 actual-wrapper tests; whole-issue chatter/starvation/start-runtime acceptance waits for the actual release and observations. |
| [#368](https://github.com/VerdifyConsultancy/verdify-platform/issues/368) | Control lead | Unestimated | Separate merged finite-probe correction from still missing jump/flatline/shared-health semantics. Define the latter from real sensor evidence and causal cases in admitted k3s CI. Live diagnostic correction and recovery proof ride #390; do not repeat accepted 70-case/replay receipts as new physical proof. |
| [#369](https://github.com/VerdifyConsultancy/verdify-platform/issues/369) | Control lead | Unestimated | Inventory named #300/#326 end-to-end consumers only; removal waits for correctness delivery through #390. Record no-removal when evidence cannot prove a property dead. |
| [#370](https://github.com/VerdifyConsultancy/verdify-platform/issues/370) | Control lead | Unestimated | After #367 authority semantics, capture current origin/reverse-conflict/restart/partial-delivery behavior before moving one duplicated decision into the pure resolver. No speculative policy refactor. |
| [#378](https://github.com/VerdifyConsultancy/verdify-platform/issues/378) | Control lead | Unestimated | Hold corridor widths stable until #785 fixed-target readout; later record no change, insufficient evidence or one bounded proposal with exact inverse. #410 night-anchor evidence alone does not authorize retuning. |
| [#390](https://github.com/VerdifyConsultancy/verdify-platform/issues/390) | Platform lead | Unestimated | Assemble one exact candidate/topology/config/rollback packet containing delivered #985 and #973 source. Reuse native receipts; bind archived provenance, real recovery and predeclared heap/fragmentation/timing/reset/soak criteria absorbed from #428. Actual OTA and bake wait for a bounded attended safe window; compile/replay alone never qualifies last-good. |
| [#398](https://github.com/VerdifyConsultancy/verdify-platform/issues/398) | Operator with data/control lead | Unestimated | After #298, bind actual crop/substrate targets and source revision; append a supported ledgered correction with inverse, preserve valid operator values and verify config/DB agreement. Never edit applied migration 064 or enable irrigation from seed labels. |
| [#412](https://github.com/VerdifyConsultancy/verdify-platform/issues/412) | Operator with data/control lead | Unestimated | At the #298 visit record actual screen position and measured night context; decide no change or bounded reversible closure with a safety readback and intervention record. DR-45 supersedes the old no-visit hold. |
| [#434](https://github.com/VerdifyConsultancy/verdify-platform/issues/434) | Control lead | Unestimated | After #299, qualify missing origin/reverse-conflict/calibration-negative cases. Actual wall feed also needs #298 field mapping and calibrated dose/flush/chemistry; automatic feed stays disabled. |
| [#587](https://github.com/VerdifyConsultancy/verdify-platform/issues/587) | Runtime/release lead | Unestimated | Map only residual feature-off nonactuation, locked/armed no-reopen, blinding and named-alert clauses to actual supported LOGIN routes and populated preconditions. Reuse accepted lifecycle/executor denials and one alert receipt. With #992 ending rehearsal, resolve a supported retained target before new stateful qualification; do not fake authority or send messages from this audit. |
| [#588](https://github.com/VerdifyConsultancy/verdify-platform/issues/588) | Research/release lead | Unestimated | Wait on #641/#782/#783; use Jason's settled DR-47 GPT-6 Luna/xhigh identity in final compatibility/manifest preparation. Do not ask the model question again, silently inherit stale v2 defaults, freeze or draw before qualification. |
| [#639](https://github.com/VerdifyConsultancy/verdify-platform/issues/639) | Runtime/release lead | Unestimated | Source/synthetic packets #985/#986 are delivered; historical prefix remains 27/48 UNPROVEN. Obtain an authentic causal pre-tick state producer and row-wise propagation/counterfactual recovery proof for 21 gaps via admitted k3s execution. Do not rerun synthetic matrices or recreate a retired rehearsal namespace by default. |
| [#640](https://github.com/VerdifyConsultancy/verdify-platform/issues/640) | Research lead | Unestimated | After #642, reconcile the first genuine assigned day, retain failed/null/reset/no-exposure outcomes and freeze export/backup custody with exact runtime identities. |
| [#641](https://github.com/VerdifyConsultancy/verdify-platform/issues/641) | Runtime/release lead | Unestimated | Keep draft #855 parked and nonmergeable. After #749/#587/#639, prepare current dormant successor diff/rollback and bind a fresh attended baseline-target-baseline proof, ending baseline-confirmed/component-off. No draw or launch follows from the proof. |
| [#642](https://github.com/VerdifyConsultancy/verdify-platform/issues/642) | Research/release lead | Unestimated | After #588 and the separately selected launch boundary, bind exact lock identity and rollback packet for randomized day 1. No activation, lock or schedule is authorized by this audit. |
| [#749](https://github.com/VerdifyConsultancy/verdify-platform/issues/749) | Runtime/release lead | Unestimated | Preserve the sealed BLOCKED packet (4.86/5.04 F spreads over unchanged 4 F; six unmatched soil identities). Resolve real spread/identity lineage and obtain missing wetting measurements before one fresh source-bound readiness packet. The old selective-operation provenance issue was resolved; do not repeat that repair or waive physical criteria. |
| [#751](https://github.com/VerdifyConsultancy/verdify-platform/issues/751) | Operator with data/control lead | Unestimated | Diagnose south climate probe and hydro separately in the same bounded visit. Repair only the evidenced component, preserve panel history and record calibrated before/after contributor truth; do not silently replace the analysis panel or make replacement a new pilot gate. |
| [#782](https://github.com/VerdifyConsultancy/verdify-platform/issues/782) | Evidence/data lead | Unestimated | Reuse published input-decision/availability packet. Authentic selected inputs and facility-owner crop/contributor identity are missing; verify the existing November 2–3, 2026 winter collection and custody rather than inventing a corpus. June–July 2027 remains a candidate calendar, not a launch promise. |
| [#783](https://github.com/VerdifyConsultancy/verdify-platform/issues/783) | Runtime/release lead | Unestimated | After #587/#639, bind actual five-role LOGIN identities and authentic reachable stages; run the complete selector/freezer/analyst path twice and compare canonical hashes. Do not use owner scaffolding, SET ROLE or synthetic fixture output as authentic vertical acceptance. |
| [#784](https://github.com/VerdifyConsultancy/verdify-platform/issues/784) | Research lead | Unestimated | After #640, operate all locked assigned days with blinded exception/backup custody and final stopped admission. The 60-day seasonal pilot is not a five-day sprint deliverable. |
| [#785](https://github.com/VerdifyConsultancy/verdify-platform/issues/785) | Research lead | Unestimated | After #784, reveal once, independently reproduce and publish a bounded result. Preserve consolidated #786/#787/#379 comparator/resource/MPC decisions and genuine held-out evidence requirements, including no-benefit/inconclusive outcomes. |
| [#963](https://github.com/VerdifyConsultancy/verdify-platform/issues/963) | Platform lead | Unestimated | The formatter/detail fix is merged in #973. Bind actual firmware/ingestor/HA-consumer compatibility, length-error recovery observation, exact deployed binary and qualified rollback into #390. Do not close from native string-length tests. |
| [#990](https://github.com/VerdifyConsultancy/verdify-platform/issues/990) | Platform/security lead | Unestimated | Add restore-renderer TTL; retain cited logs, recount exact inactive Jobs/ConfigMaps/Policies and recheck zero matching pods before exact-name cleanup. Exclude allow-db-from-restore and all data. Reconcile the conflicting historical counts in the execution receipt. |
| [#991](https://github.com/VerdifyConsultancy/verdify-platform/issues/991) | Platform/security lead | Unestimated | Preserve cited evidence and prune exact non-data rehearsal residue only after checking current references. Keep all data intact. If approved #992 retirement subsumes the objects, close with that actual receipt instead of doing duplicate cleanup. |
| [#992](https://github.com/VerdifyConsultancy/verdify-platform/issues/992) | Platform/security lead | Unestimated | The experiment ends now; no CNPG cutover is planned (#245 closed). Prepare and verify dump/snapshot custody for all six CNPG and two spool PVCs, settle actual consumers, then perform the already approved retirement with storage-infra/agents in their lanes. DR-49 deletion go is conditional on posted, verified dump/snapshot receipts; do not ask again after that condition is met. Bind the exact receipt scope before deletion. |
| [#995](https://github.com/VerdifyConsultancy/verdify-platform/issues/995) | Platform/security lead | Unestimated | Adopt the descheduler Namespace in place with prune off and unchanged UID/spec. Update central ownership with surviving objects; #1006 removes obsolete Traefik ClusterRole entries. Preserve the separately pinned descheduler Application and operator pause semantics. |
| [#997](https://github.com/VerdifyConsultancy/verdify-platform/issues/997) | Platform/security lead | Unestimated | Verify consumers, delete only the four stale dashboard ConfigMaps in verdify-platform, and prove production dashboards still load. Link monitoring-stack#832 for its staged-copy work; do not edit its namespace/source here. |
| [#998](https://github.com/VerdifyConsultancy/verdify-platform/issues/998) | Platform/security lead | Unestimated | Wait for monitoring-stack#1016 writer/DB-death firing-and-clear receipts. Existing writer rules alone are insufficient. Then retire exactly the two watchdog CronJobs and verify absence; keep today's alert path until replacement proof exists. |
| [#999](https://github.com/VerdifyConsultancy/verdify-platform/issues/999) | Platform/security lead | Unestimated | Resolve running immutable imageIDs and stage third-party digest pins. Whole-issue DB pin waits on #994 and an attended fresh-backup/restart window; pinning pod specs can still roll workloads even when image bytes match. Preserve actuator-owned first-party candidate entries. |
| [#1000](https://github.com/VerdifyConsultancy/verdify-platform/issues/1000) | Platform/security lead | Unestimated | Create the exact per-item metric/probe coverage table and owner handoff, with probe plus tested alert for Shelly meters and a single referenced rehearsal obligation. Alert rules and firing proof belong to monitoring-stack; full closure waits on those receipts. |
| [#1001](https://github.com/VerdifyConsultancy/verdify-platform/issues/1001) | Platform/security lead | Unestimated | Track agents#4461/#4470/#4468 and the network owner's D-16 retirement. Perform only the corresponding local Namespace/HostRegexp cleanup after owner receipts. Do not treat missing native D-16 edge as readiness or modify shared wildcard TLS/DNS here. |
| [#1003](https://github.com/VerdifyConsultancy/verdify-platform/issues/1003) | Platform/security lead | Unestimated | Prepare PR-only release-pin delivery and main validation proposal. Jason's choice of bot-authored release-pin PRs/required-check policy remains unanswered; no enforced branch-policy or generated fleet-CI change is implied. Seven-day zero-unvalidated-commit acceptance starts after actual adoption. |
| [#1004](https://github.com/VerdifyConsultancy/verdify-platform/issues/1004) | Platform/security lead | Unestimated | Inventory desired-vs-live workload drift and decide each longhorn-recovery toleration from its owner rationale. Prepare the smallest wanted-field source changes. Application-spec ownership follows agents#4461; do not blanket force ownership or erase every kubectl manager as a substitute for behavior/field ownership proof. |
| [#1005](https://github.com/VerdifyConsultancy/verdify-platform/issues/1005) | Platform/security lead | Unestimated | Design genuine lab node redundancy while retaining validated content publication, or present the allowed single-replica exception for decision. Existing two replicas share RWO and required co-location; never claim node HA from replica count. Storage choice/capacity precedes rollout and failure testing. |
| [#1009](https://github.com/VerdifyConsultancy/verdify-platform/issues/1009) | Platform/security lead | Unestimated | Implement full-SHA builder admission, pinned toolchain inputs and bounded egress now. Device-side signing is explicitly deferred by DR-48. Complete the approved OTA credential compensation only with network-infra#77 passive ACL readback, value-blind custody and a safe verified flash/recovery packet through #390; no fresh signing or rotation decision is needed. |
| [#1011](https://github.com/VerdifyConsultancy/verdify-platform/issues/1011) | Platform/security lead | Unestimated | Inventory actual MCP destinations, use a dedicated non-automounting ServiceAccount and least-privilege egress, then prove required tools still work. A generic public-443 rule cannot claim OpenAI-only hostname restriction under ordinary Kubernetes NetworkPolicy; name that limitation or use an approved existing enforcement path. |

## ROLLUP — Program ownership

Kind: rollup. Goal: Track child acceptance without double counting engineering work.

Exit: Every required child accepted or disposed by an explicit linked scope decision.

| Issue | Accountable role | Engineering hours | Next action |
|---|---|---|---|
| [#16](https://github.com/VerdifyConsultancy/verdify-platform/issues/16) | Operator with data/control lead | Unestimated | Track child acceptance and dated scope decisions; allocate no duplicate engineering hours to the umbrella. |
| [#75](https://github.com/VerdifyConsultancy/verdify-platform/issues/75) | Platform lead | Unestimated | Track child acceptance and dated scope decisions; allocate no duplicate engineering hours to the umbrella. |
| [#350](https://github.com/VerdifyConsultancy/verdify-platform/issues/350) | Control lead | Unestimated | Track child acceptance and dated scope decisions; allocate no duplicate engineering hours to the umbrella. |
| [#359](https://github.com/VerdifyConsultancy/verdify-platform/issues/359) | Control lead | Unestimated | Track child acceptance and dated scope decisions; allocate no duplicate engineering hours to the umbrella. |
| [#430](https://github.com/VerdifyConsultancy/verdify-platform/issues/430) | Control lead | Unestimated | Track child acceptance and dated scope decisions; allocate no duplicate engineering hours to the umbrella. |
| [#581](https://github.com/VerdifyConsultancy/verdify-platform/issues/581) | Research/release lead | Unestimated | Track child acceptance and dated scope decisions; allocate no duplicate engineering hours to the umbrella. |
| [#775](https://github.com/VerdifyConsultancy/verdify-platform/issues/775) | Evidence/data lead | Unestimated | Track child acceptance and dated scope decisions; allocate no duplicate engineering hours to the umbrella. |

## Campaign stage contracts

Generated from [planning/backlog.yaml](planning/backlog.yaml). Edit the source and run `python -m planning.render`; do not maintain a competing roadmap.

The [campaign strategy](planning/CAMPAIGN.md) defines scope, release bundles, evidence gates and deferred work. Historical June/August plans are [archived](planning/archive/2026-09-05/README.md), not execution instructions.

Engineering-day estimates are planning ranges, not calendar promises or mandatory soak gates. S = up to one engineering day; M = roughly 1–3; L = 3–5 or a roll-up to split into coherent PRs. Stage ranges are bundle estimates, not sums of duplicated epic effort.

## C0 — Measurement and incident truth

3–5 engineering days; raw-ledger access is the main uncertainty.

Exit: A trustworthy fixed-panel climate/resource contract and a safely classified September 4 wetting interruption.

## C1 — Recovery and integrated physical qualification

3–5 engineering days, overlapping C0; physical window is not a promised date.

Exit: Fresh restore-backed vertical evidence and one separately authorized, sealed physical proof; finish baseline-confirmed and off.

## C2 — Seasonal design lock and randomized start

2–3 engineering days after C0/C1 evidence, plus the eligible seasonal start window.

Exit: A declared exploratory pilot or justified revised study, one immutable draw, and separately authorized day 1.

## C3 — Complete pilot and decision report

60 local days if the accepted 30-pair pilot is retained; 2–3 days for final analysis.

Exit: All assigned days retained, blinded exports frozen, one-way reveal and a reproducible decision with bounded claims.

## C4 — Delivery and runtime reliability

5–8 engineering days in coherent bundles; may run alongside C0–C3 without changing locked trial identity.

Exit: Exact-source delivery, truthful planner/writer/alert health, real compile and device-safe release checks.

## C5 — Durability and least privilege

5–8 engineering days; any production DB cutover is a separately bounded release.

Exit: Role-complete recoverable backups, durable ingest state, measured RPO/RTO and a rehearsed HA cutover option.

## C6 — Evidence-led control and irrigation

2–3 implementation sprints after evidence; no treatment-affecting changes during a locked study.

Exit: Safe behavior-preserving firmware simplification, grounded control decisions and commissioned irrigation feedback.

## C7 — Physical and seasonal commissioning

Condition-triggered; no assumed hardware purchase or install date.

Exit: Only explicitly commissioned sensors/equipment are used for control or scientific claims.

## C8 — Comparator and full resource claim

Separate future campaigns, estimated after C3 readout; not launch prerequisites.

Exit: A justified next comparison, measured heating/overnight economics, or independently qualified future platform capability.

C0 and independent C1 qualification can overlap. C4/C5 changes may run alongside the study only when they preserve its frozen identities and outcomes; otherwise defer or follow the locked safety-abort/deviation contract. No opportunistic tuning, meter reinterpretation or sensor-panel substitution during assigned days.
