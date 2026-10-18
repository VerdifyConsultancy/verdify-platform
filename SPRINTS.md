# Upcoming sprints and ongoing work

## Current execution plan

Reviewed **2026-10-17T23:53:02Z** against source `183b6f2bdd58cb6cc3df1110d6f722a7b9d3c6f6`. [planning/reviews/2026-10-17](planning/reviews/2026-10-17) records current source, GitHub, runtime and evidence limits. All **51 open issues** have one allocation and accountable role. Planning capacity: **40 engineering hours/week**; ranges are estimates, not calendar promises.

| Pull set | Kind | Allocated hours | Capacity hours | Issues |
|---|---|---|---|---|
| S2 — Completion and current readiness (October 13–26, 2026) | sprint | 8–16 | 80 | [#749](https://github.com/VerdifyConsultancy/verdify-platform/issues/749) |
| S3 — Runtime truth and bounded qualification (October 27–November 9, 2026) | sprint | 28–56 | 80 | [#587](https://github.com/VerdifyConsultancy/verdify-platform/issues/587), [#639](https://github.com/VerdifyConsultancy/verdify-platform/issues/639), [#782](https://github.com/VerdifyConsultancy/verdify-platform/issues/782), [#801](https://github.com/VerdifyConsultancy/verdify-platform/issues/801), [#802](https://github.com/VerdifyConsultancy/verdify-platform/issues/802), [#977](https://github.com/VerdifyConsultancy/verdify-platform/issues/977), [#980](https://github.com/VerdifyConsultancy/verdify-platform/issues/980), [#981](https://github.com/VerdifyConsultancy/verdify-platform/issues/981) |
| S4 — Integrated qualification after current readiness | sprint | 18–30 | 80 | [#641](https://github.com/VerdifyConsultancy/verdify-platform/issues/641), [#783](https://github.com/VerdifyConsultancy/verdify-platform/issues/783) |
| S5 — Immutable lock, launch and first real outcome | conditional | 12–24 | 80 | [#588](https://github.com/VerdifyConsultancy/verdify-platform/issues/588), [#640](https://github.com/VerdifyConsultancy/verdify-platform/issues/640), [#642](https://github.com/VerdifyConsultancy/verdify-platform/issues/642) |
| PILOT — Pilot operations and reproducible readout | ongoing | 36–68 | Not timeboxed | [#784](https://github.com/VerdifyConsultancy/verdify-platform/issues/784), [#785](https://github.com/VerdifyConsultancy/verdify-platform/issues/785) |
| RELEASE — Release safety and estate adoption follow-through | conditional | 6–12 + unestimated | Not timeboxed | [#386](https://github.com/VerdifyConsultancy/verdify-platform/issues/386), [#390](https://github.com/VerdifyConsultancy/verdify-platform/issues/390), [#963](https://github.com/VerdifyConsultancy/verdify-platform/issues/963) |
| CONTROL — Evidence-led control and irrigation queue | conditional | Unestimated | Not timeboxed | [#297](https://github.com/VerdifyConsultancy/verdify-platform/issues/297), [#299](https://github.com/VerdifyConsultancy/verdify-platform/issues/299), [#367](https://github.com/VerdifyConsultancy/verdify-platform/issues/367), [#368](https://github.com/VerdifyConsultancy/verdify-platform/issues/368), [#369](https://github.com/VerdifyConsultancy/verdify-platform/issues/369), [#370](https://github.com/VerdifyConsultancy/verdify-platform/issues/370), [#378](https://github.com/VerdifyConsultancy/verdify-platform/issues/378), [#410](https://github.com/VerdifyConsultancy/verdify-platform/issues/410), [#428](https://github.com/VerdifyConsultancy/verdify-platform/issues/428), [#434](https://github.com/VerdifyConsultancy/verdify-platform/issues/434) |
| PHYSICAL — Physical and seasonal commissioning | conditional | Unestimated | Not timeboxed | [#45](https://github.com/VerdifyConsultancy/verdify-platform/issues/45), [#51](https://github.com/VerdifyConsultancy/verdify-platform/issues/51), [#52](https://github.com/VerdifyConsultancy/verdify-platform/issues/52), [#298](https://github.com/VerdifyConsultancy/verdify-platform/issues/298), [#398](https://github.com/VerdifyConsultancy/verdify-platform/issues/398), [#412](https://github.com/VerdifyConsultancy/verdify-platform/issues/412), [#751](https://github.com/VerdifyConsultancy/verdify-platform/issues/751) |
| FUTURE — Future comparator, resource economics and cutover options | future | Unestimated | Not timeboxed | [#31](https://github.com/VerdifyConsultancy/verdify-platform/issues/31), [#245](https://github.com/VerdifyConsultancy/verdify-platform/issues/245), [#379](https://github.com/VerdifyConsultancy/verdify-platform/issues/379), [#786](https://github.com/VerdifyConsultancy/verdify-platform/issues/786), [#787](https://github.com/VerdifyConsultancy/verdify-platform/issues/787) |
| ROLLUP — Campaign and program roll-ups | rollup | Unestimated | Not timeboxed | [#14](https://github.com/VerdifyConsultancy/verdify-platform/issues/14), [#16](https://github.com/VerdifyConsultancy/verdify-platform/issues/16), [#75](https://github.com/VerdifyConsultancy/verdify-platform/issues/75), [#218](https://github.com/VerdifyConsultancy/verdify-platform/issues/218), [#350](https://github.com/VerdifyConsultancy/verdify-platform/issues/350), [#359](https://github.com/VerdifyConsultancy/verdify-platform/issues/359), [#430](https://github.com/VerdifyConsultancy/verdify-platform/issues/430), [#581](https://github.com/VerdifyConsultancy/verdify-platform/issues/581), [#750](https://github.com/VerdifyConsultancy/verdify-platform/issues/750), [#775](https://github.com/VerdifyConsultancy/verdify-platform/issues/775) |

Sprint allocations may cover bounded preparation. Full issue acceptance still depends on its predecessors, current evidence and declared physical or seasonal conditions. C0–C8 remain stage contracts; closed historical issues remain visible in the stage views below.

## S2 — Completion and current readiness (October 13–26, 2026)

Kind: sprint. Capacity: 80 engineering hours. Goal: Original396/419/643/644 are complete. 749is the sole completion focus, retaining8–16h planning allocation; software snapshot-hold fix is deployed, actual strict readiness is blocked by recurring snapshot publication/coherence waits.

Exit: Finish one bounded producer-coherence correction and genuine current original-clause readiness acceptance without pass-hunting, collector weakening, used-approval replay or a new physical policy run. Source/runtime success isdistinctfromreadiness. Any carryover usesS3reserve, notextra capacity; prior estimates and CI/runtime/wakes are not hoursworked.

| Issue | Accountable role | Engineering hours | Next action |
|---|---|---|---|
| [#749](https://github.com/VerdifyConsultancy/verdify-platform/issues/749) | Runtime/release lead | 8–16 | Corrected seven-file producer-coherence source packet is root-accepted: 60 focused tests pass, old ordering and rejected SQL regressions fail as expected, exact existing confirmation SQL and immutable transaction/fanout ordering independently verified. Same Retry now composes the fresh complete27 canonical snapshot with the genuine source change, preserving actual latest main and #983 exporter pause. Normal source push/native affected-image qualification follows the finite shared-owner handback and current admission/fit. Then review exact ingestor-only rollout/inverse249 and pending owner drift before one attended full sync and finite original-clause qualification. No extra human gate, policy replay or quiet-clock reset loop. |

## S3 — Runtime truth and bounded qualification (October 27–November 9, 2026)

Kind: sprint. Capacity: 80 engineering hours. Goal: Execution released October17 by Jason; begin ready work now as early pulls from this existing sprint, keeping October27–November9 membership and the same shared80h envelope. Eight bounded packets981/980/639/587/801/802/977/782 at28–56shared engineering hours. Reserve24h includes749carryover and unexpected work; upper envelope56+24=80 at40h/week. Finish one product delivery at a time; combine639/587in one contained execution and count shared work once.

Exit: Deliver each explicitly bounded packet and preserve full original acceptance; incomplete physical/scientific clauses stayopen. 782spend4–8h is input/decision preparation, not full12–20h design completion. 977policy precedesNovember12. Respect actual NAS pause/handoff; if980held, advance other ready packets. 963is a replacement only after exact qualified release/operator inputs, never additive.

| Issue | Accountable role | Engineering hours | Next action |
|---|---|---|---|
| [#587](https://github.com/VerdifyConsultancy/verdify-platform/issues/587) | Runtime/release lead | 4–8 | S3 bounded packet: Incremental lifecycle/kill-switch/visibility cases in SAME639 run; count shared execution only once; physical portions stay separately scoped. Preserve the original full issue acceptance; partial preparation or contained proof does not close physical/scientific clauses. Use the same639 execution; estimate only distinct incremental cases, never duplicate shared runtime. See planning/reviews/2026-10-17/EXECUTION-QUEUE.md for first action, stop condition and owner. |
| [#639](https://github.com/VerdifyConsultancy/verdify-platform/issues/639) | Runtime/release lead | 8–16 | S3 bounded packet: One contained device-denied executor/recovery qualification; preserve unproved physical portions. Preserve the original full issue acceptance; partial preparation or contained proof does not close physical/scientific clauses. See planning/reviews/2026-10-17/EXECUTION-QUEUE.md for first action, stop condition and owner. |
| [#782](https://github.com/VerdifyConsultancy/verdify-platform/issues/782) | Evidence/data lead | 4–8 | S3 bounded packet: Authentic seasonal/panel/target input and a concrete scientific decision packet; full design/power/approval acceptance remains when inputs allow. Preserve the original full issue acceptance; partial preparation or contained proof does not close physical/scientific clauses. This4–8h packet is not the former12–20h full scientific-contract estimate. See planning/reviews/2026-10-17/EXECUTION-QUEUE.md for first action, stop condition and owner. |
| [#801](https://github.com/VerdifyConsultancy/verdify-platform/issues/801) | Platform lead | 1–2 | EXECUTION RELEASED October17. Assigned to existing Retry as next small product source task after the #749 coherence packet; preserve #749 priority. Prepare only two host exclusions plus focused contract/render proof; no new image profile. Normal delivery follows actual owner handback and scoped pending #983 drift disposition. |
| [#802](https://github.com/VerdifyConsultancy/verdify-platform/issues/802) | Platform lead | 4–8 | EXECUTION RELEASED October17. Existing Agents owner acknowledged the independent source/metadata packet and will pull it after its current Network/reindex/archive repair work. Prepare exact current enforce resources, minimum manual project/catalog declarations and inverse; adoption not started. No new workers or whole-program prerequisite. |
| [#977](https://github.com/VerdifyConsultancy/verdify-platform/issues/977) | Platform lead + storage owner | 2–4 | S3 bounded packet: Record replica-policy decision beforeNovember12; optional remount/reclaim stays separate and unapproved. Preserve the original full issue acceptance; partial preparation or contained proof does not close physical/scientific clauses. See planning/reviews/2026-10-17/EXECUTION-QUEUE.md for first action, stop condition and owner. |
| [#980](https://github.com/VerdifyConsultancy/verdify-platform/issues/980) | Platform/observability lead | 2–4 | S3 bounded packet: Review intentional NAS-window exporter pause and resume handoff; qualify exporter/backup separately after owner release, no NFS load during the hold. Preserve the original full issue acceptance; partial preparation or contained proof does not close physical/scientific clauses. See planning/reviews/2026-10-17/EXECUTION-QUEUE.md for first action, stop condition and owner. |
| [#981](https://github.com/VerdifyConsultancy/verdify-platform/issues/981) | Data/platform lead | 3–6 | SOURCE DIAGNOSIS STARTED October17 by root. Six current183b provider files retained; shelly_sync AST is exact despite unrelated HA-file differences. Two fixed HA power entities preserve gateway last_updated independently from row time; >=300s stale and any non-ok channel blocks total. No live source recovery or physical cause proven. Next bounded intended-source/ingest comparison belongs to the existing product lane after #749 and #801; never substitute clocks/channels or force OK. |

## S4 — Integrated qualification after current readiness

Kind: sprint. Capacity: 80 engineering hours. Goal: After S3contained639/587evidence and genuine scientific inputs, finish remaining783vertical-path and separately authorized641physical proof; preserve any unfinished639/587physical clauses.

Exit: 639 source/contained preparation was pulled intoS3; its full physical acceptance remains distinct. 587/783 need actual stateful vertical-path evidence. 641 requires a specific attended proof decision. No blanket experiment/OTA or new isolated target is authorized by planning.

| Issue | Accountable role | Engineering hours | Next action |
|---|---|---|---|
| [#641](https://github.com/VerdifyConsultancy/verdify-platform/issues/641) | Runtime/release lead | 6–10 | Complete current source/readiness and behavioral prerequisites first; prepare exactly one attended proof window and rollback using real field/device inputs. |
| [#783](https://github.com/VerdifyConsultancy/verdify-platform/issues/783) | Runtime/release lead | 12–20 | Use the finite prepared device-denied vertical-slice packet on the one admitted target after required behavior/design inputs; serialize one future writer and preserve rollback/seals. No new target or connected-clock runner. |

## S5 — Immutable lock, launch and first real outcome

Kind: conditional. Capacity: 80 engineering hours. Goal: After the genuine predecessor evidence and seasonal decision, lock once and perform one separately authorized launch.

Exit: #588 exactly one immutable lock/draw; #642 actual authorized day 1; #640 first genuine assigned-day freeze/export. No calendar launch date is promised.

| Issue | Accountable role | Engineering hours | Next action |
|---|---|---|---|
| [#588](https://github.com/VerdifyConsultancy/verdify-platform/issues/588) | Research/release lead | 4–8 | Wait for real #641/#782/#783 acceptance; review and execute the single supported lock/draw only in its authorized launch lane. |
| [#640](https://github.com/VerdifyConsultancy/verdify-platform/issues/640) | Research lead | 4–8 | After true day1 acceptance, freeze the declared outcome/custody and compare independent canonical exports; preserve every assigned-day outcome. |
| [#642](https://github.com/VerdifyConsultancy/verdify-platform/issues/642) | Research/release lead | 4–8 | After #588 and selected attended launch, bind the one allowed activation to real assigned day and verify exact launch receipts; no authority inferred from this replan. |

## PILOT — Pilot operations and reproducible readout

Kind: ongoing. Goal: Retain every assigned day and produce the prespecified bounded decision.

Exit: #784 genuine 60 local days if the retained design is chosen (or prespecified stopping boundary), then #785 one-way reveal and reproducible ITT report.

| Issue | Accountable role | Engineering hours | Next action |
|---|---|---|---|
| [#784](https://github.com/VerdifyConsultancy/verdify-platform/issues/784) | Research lead | 24–48 | After #640 first-day acceptance, operate all declared days without replacement or engineering compression, with daily bounded custody and safety checks. |
| [#785](https://github.com/VerdifyConsultancy/verdify-platform/issues/785) | Research lead | 12–20 | Wait for #784 complete custody; rehearse only already permitted blinded analysis machinery, then execute the declared reveal/readout once. |

## RELEASE — Release safety and estate adoption follow-through

Kind: conditional. Goal: Finish the remaining bounded release and ownership interfaces without delaying independent scientific work.

Exit: Actual386/390firmware/lighting release evidence and separately selected963operator/binary/HA acceptance. 317is retired to owner/workaround record;801/802bounded adoption packets are inS3. No whole-estate adoption gate, broadsharedsync or OTA implied.

| Issue | Accountable role | Engineering hours | Next action |
|---|---|---|---|
| [#386](https://github.com/VerdifyConsultancy/verdify-platform/issues/386) | Platform lead | Unestimated | Prepare the smallest deterministic boundary cases against current FSM, then select a separate attended actual-state observation when authorized; no credit from 87,878 origin-unknown historical rows. |
| [#390](https://github.com/VerdifyConsultancy/verdify-platform/issues/390) | Platform lead | Unestimated | Consume existing compiler/corpus receipts in one exact release/rollback acceptance packet; settle real binary/last-good and physical-window choices with operator. Do not rerun compiler merely to replace old closed dependencies. |
| [#963](https://github.com/VerdifyConsultancy/verdify-platform/issues/963) | Platform lead | 6–12 | Substitute only after a concrete qualified binary/last-good/HA/operator decision packet is ready, replacing held S3 scope within80h. Compatible source is delivered; no recompile/OTA or physical recovery merely for triage. |

## CONTROL — Evidence-led control and irrigation queue

Kind: conditional. Goal: Retain C6 ownership and pull bounded sensing/safety maintenance independently; treatment changes respect study locks.

Exit: Retained topology/sensor/irrigation/dwell/arbitration contracts. 378owns any later conditional anchor decision from361; current anchors and zonal authority remainunchanged. No tuning during locked study;296firmwarefeedback is explicitlynotselected.

| Issue | Accountable role | Engineering hours | Next action |
|---|---|---|---|
| [#297](https://github.com/VerdifyConsultancy/verdify-platform/issues/297) | Control lead | Unestimated | Once topology/calibration accepted, qualify alert-only/decision cases and exact current dispatcher path before enabling any real skip loop. |
| [#299](https://github.com/VerdifyConsultancy/verdify-platform/issues/299) | Control lead | Unestimated | After release/rollback contract, qualify native boundary cases and prepare one attended actual center-mister window; do not waive current #749 holds. |
| [#367](https://github.com/VerdifyConsultancy/verdify-platform/issues/367) | Control lead | Unestimated | Review the durable isolated source fix for the next firmware cohort with exact red/green/native/replay receipt; do not fold into frozen current runtime delivery or claim #367 complete/OTA accepted. |
| [#368](https://github.com/VerdifyConsultancy/verdify-platform/issues/368) | Control lead | Unestimated | Pick one demonstrated cross-consumer semantic discrepancy, map exact producer/consumer tests and propose the smallest shared contract fix; preserve original fail-closed thresholds. |
| [#369](https://github.com/VerdifyConsultancy/verdify-platform/issues/369) | Control lead | Unestimated | Prepare one exact consumer inventory and smallest provably dead set; perform removal only after release/rollback contract, no broad cleanup. |
| [#370](https://github.com/VerdifyConsultancy/verdify-platform/issues/370) | Control lead | Unestimated | After accepted dwell/release contract, isolate one exact duplicated decision with native invariants and no control-semantic change. |
| [#378](https://github.com/VerdifyConsultancy/verdify-platform/issues/378) | Control lead | Unestimated | Keep current corridors stable through the declared pilot; after readout analyze eligible evidence and select one bounded corridor decision. |
| [#410](https://github.com/VerdifyConsultancy/verdify-platform/issues/410) | Control lead | Unestimated | Define one source-stamped solar-event/actual-state episode extraction and analyze eligible nights; do not change controller settings to produce the desired curve. |
| [#428](https://github.com/VerdifyConsultancy/verdify-platform/issues/428) | Control lead | Unestimated | Retain the source-bound heap diagnosis from#749 and the historical low-watermark risk; no OTA, recompilation or speculative heap rewrite is justified by this single informational snapshot. Finish this issue's broader causal/last-good-floor acceptance when selected, keeping current condition and historical risk distinct. |
| [#434](https://github.com/VerdifyConsultancy/verdify-platform/issues/434) | Control lead | Unestimated | Prepare meaningful topology/origin native cases after release/cycling contract; physical commissioning and attended routing proof require the actual crop/plumbing map. |

## PHYSICAL — Physical and seasonal commissioning

Kind: conditional. Goal: Prepare concrete operator worksheets and keep missing sensors visibly unavailable.

Exit: Actual crop/probe/plumbing map, calibrated chemistry/CO2, south/hydro repair and supported seasonal choices. No guessed dates or purchasing authority.

| Issue | Accountable role | Engineering hours | Next action |
|---|---|---|---|
| [#45](https://github.com/VerdifyConsultancy/verdify-platform/issues/45) | Operator with data/control lead | Unestimated | Preserve all seven missing-channel warnings and unknown values. The finite passive provenance attempt stopped before metadata/log access; no hardware cause is established. Carry broader commissioning and irrigation-feedback acceptance in this issue; it is not a new prerequisite for the bounded non-actuating#749 readiness qualification. Any physical service action needs its own concrete scope. |
| [#51](https://github.com/VerdifyConsultancy/verdify-platform/issues/51) | Operator with data/control lead | Unestimated | Identify the actual available reference and bounded sampling session; no fabricated reference values or automatic hardware purchase. |
| [#52](https://github.com/VerdifyConsultancy/verdify-platform/issues/52) | Operator with data/control lead | Unestimated | Obtain operator observation and select no-change or one documented care action before implementing a schedule. |
| [#298](https://github.com/VerdifyConsultancy/verdify-platform/issues/298) | Operator with data/control lead | Unestimated | Collect one operator field worksheet with actual labels and observed wiring before editing mappings or threshold seeds. |
| [#398](https://github.com/VerdifyConsultancy/verdify-platform/issues/398) | Operator with data/control lead | Unestimated | After #298 field mapping, produce exact before/after threshold worksheet and bounded reviewed source/data delta; no invented default commissioning. |
| [#412](https://github.com/VerdifyConsultancy/verdify-platform/issues/412) | Operator with data/control lead | Unestimated | Use operator observations to choose no-change or one reversible door-screen action; retain measurements and do not silently change a locked study covariate. |
| [#751](https://github.com/VerdifyConsultancy/verdify-platform/issues/751) | Operator with data/control lead | Unestimated | Select the operator service window and actual replacement/reference resources; capture truthful three-probe degraded baseline first. Do not turn south replacement into a new blanket prerequisite when #749 permits three. |

## FUTURE — Future comparator, resource economics and cutover options

Kind: future. Goal: Keep useful comparator/economics/read-onlytwin and conditional245cutover work without making them current pilot gates. Separate unselected atomic/vector architecture586/638isretired, notanimplementationcommitment.

Exit: Issue-specific qualified proof and separate implementation decision after dependencies; no claim of whole-resource savings or AI superiority from the narrow pilot.

| Issue | Accountable role | Engineering hours | Next action |
|---|---|---|---|
| [#31](https://github.com/VerdifyConsultancy/verdify-platform/issues/31) | Research/platform lead | Unestimated | Use actual firmware/tunable contract to inject/observe missing dimensions only when selected; no labels/count prefill/testdouble claims or physical equivalence from replay. |
| [#245](https://github.com/VerdifyConsultancy/verdify-platform/issues/245) | Data/platform lead | Unestimated | Reuse b97176ad current275 cutover/rollback handoff and prepare writer-fence/final-delta/job-policy worksheets only; update source275 assumptions against actual276 before selecting a live window. No new target, production role cutover, PVC deletion or old-data promotion. |
| [#379](https://github.com/VerdifyConsultancy/verdify-platform/issues/379) | Research/platform lead | Unestimated | Wait for comparator/readout results; select one falsifiable held-out modeling question rather than adding a new active controller. |
| [#786](https://github.com/VerdifyConsultancy/verdify-platform/issues/786) | Research/platform lead | Unestimated | After frozen readout, write one deterministic comparator decision/spec and held-out qualification plan. Preserve current pilot arm identity. |
| [#787](https://github.com/VerdifyConsultancy/verdify-platform/issues/787) | Research/platform lead | Unestimated | Keep post-readout economics deferred. Diagnose immediate energy-source health under a bounded #75 child; do not hide that repair in this future economics issue or credit stale channel0 as real consumption. |

## ROLLUP — Campaign and program roll-ups

Kind: rollup. Goal: Track original C0–C8 program outcomes without double-counting epic effort.

Exit: Each child retains its own acceptance and historical receipts. Parentage is ownership, never an automatic blocking edge.

| Issue | Accountable role | Engineering hours | Next action |
|---|---|---|---|
| [#14](https://github.com/VerdifyConsultancy/verdify-platform/issues/14) | Research/platform lead | Unestimated | Keep #31/#606/#638 future source/build work separate from current pilot delivery; no twin adoption prerequisite invented for current source/physical proof. |
| [#16](https://github.com/VerdifyConsultancy/verdify-platform/issues/16) | Operator with data/control lead | Unestimated | Keep #751/#298/#51/#52/#412 finite operator decisions; no generalized hardware procurement or forced healthy state. |
| [#75](https://github.com/VerdifyConsultancy/verdify-platform/issues/75) | Platform lead | Unestimated | Keep#980/#981 as bounded source-to-live children;#749 owns the current readiness-blocking diagnosis and minimal stale-corpus producer lifecycle correction. Preserve#428's separate historical heap/firmware scope and#45's physical soil commissioning boundary. Do not reopen accepted historical alert cleanup#49 or waive current soil warnings. |
| [#218](https://github.com/VerdifyConsultancy/verdify-platform/issues/218) | Data/platform lead | Unestimated | Reuse accepted recovery evidence and keep #245 as separate decision/workstream; bounded exporter health belongs #75, backup storage/offsite ownership remains storage-infra. |
| [#350](https://github.com/VerdifyConsultancy/verdify-platform/issues/350) | Control lead | Unestimated | Commission topology/measurement before control; retain #296 conditional no-build option and no side-channel irrigation writer. |
| [#359](https://github.com/VerdifyConsultancy/verdify-platform/issues/359) | Control lead | Unestimated | Keep the child graph and bounded source pulls; do not close program on compiler or replay counts. |
| [#430](https://github.com/VerdifyConsultancy/verdify-platform/issues/430) | Control lead | Unestimated | Pull only after #428 diagnosis and #367/#369/#370 source contracts identify one demonstrable safe simplification. |
| [#581](https://github.com/VerdifyConsultancy/verdify-platform/issues/581) | Research/release lead | Unestimated | Retain end-to-end program as rollup, with finite current/next pulls; do not schedule a whole real60-day study as a short software sprint. |
| [#750](https://github.com/VerdifyConsultancy/verdify-platform/issues/750) | Runtime/release lead | Unestimated | Track current sole runtime writer delivery via owner publisher #4977, then current readiness. Do not reopen completed recovery work or call a historical restore a present proof receipt. |
| [#775](https://github.com/VerdifyConsultancy/verdify-platform/issues/775) | Evidence/data lead | Unestimated | Replace launch-first/global completion language with current #749 delivery and bounded S3 pulls. Keep closed predecessors closed, preserve all56 open original scopes and graph ownership. |

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
