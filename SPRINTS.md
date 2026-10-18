# Upcoming sprints and ongoing work

## Current execution plan

Reviewed **2026-10-18T07:18:12.927696+00:00** against source `83e148b60948d0c2328f8a22d819d481ff2c4ca6`. [planning/reviews/2026-10-18-status/README.md](planning/reviews/2026-10-18-status/README.md) records current source, GitHub, runtime and evidence limits. All **47 open issues** have one allocation and accountable role. Planning capacity: **40 engineering hours/week**; ranges are estimates, not calendar promises.

| Pull set | Kind | Allocated hours | Capacity hours | Issues |
|---|---|---|---|---|
| S3 — Runtime truth and contained qualification (October 19–30, 2026) | sprint | 21–42 | 80 | [#587](https://github.com/VerdifyConsultancy/verdify-platform/issues/587), [#639](https://github.com/VerdifyConsultancy/verdify-platform/issues/639), [#749](https://github.com/VerdifyConsultancy/verdify-platform/issues/749), [#782](https://github.com/VerdifyConsultancy/verdify-platform/issues/782), [#981](https://github.com/VerdifyConsultancy/verdify-platform/issues/981) |
| ALTERNATE — Alternate pull when an S3 packet is blocked | conditional | Unestimated | Not timeboxed | [#368](https://github.com/VerdifyConsultancy/verdify-platform/issues/368) |
| S4 — Integrated qualification and physical proof | conditional | Unestimated | Not timeboxed | [#641](https://github.com/VerdifyConsultancy/verdify-platform/issues/641), [#783](https://github.com/VerdifyConsultancy/verdify-platform/issues/783) |
| S5 — Final identity lock, launch and first assigned outcome | conditional | Unestimated | Not timeboxed | [#588](https://github.com/VerdifyConsultancy/verdify-platform/issues/588), [#640](https://github.com/VerdifyConsultancy/verdify-platform/issues/640), [#642](https://github.com/VerdifyConsultancy/verdify-platform/issues/642) |
| PILOT — Pilot operation and readout | ongoing | Unestimated | Not timeboxed | [#784](https://github.com/VerdifyConsultancy/verdify-platform/issues/784), [#785](https://github.com/VerdifyConsultancy/verdify-platform/issues/785) |
| RELEASE — Firmware and lighting release acceptance | conditional | Unestimated | Not timeboxed | [#386](https://github.com/VerdifyConsultancy/verdify-platform/issues/386), [#390](https://github.com/VerdifyConsultancy/verdify-platform/issues/390), [#963](https://github.com/VerdifyConsultancy/verdify-platform/issues/963) |
| CONTROL — Control and irrigation backlog | conditional | Unestimated | Not timeboxed | [#297](https://github.com/VerdifyConsultancy/verdify-platform/issues/297), [#299](https://github.com/VerdifyConsultancy/verdify-platform/issues/299), [#367](https://github.com/VerdifyConsultancy/verdify-platform/issues/367), [#369](https://github.com/VerdifyConsultancy/verdify-platform/issues/369), [#370](https://github.com/VerdifyConsultancy/verdify-platform/issues/370), [#410](https://github.com/VerdifyConsultancy/verdify-platform/issues/410), [#428](https://github.com/VerdifyConsultancy/verdify-platform/issues/428), [#434](https://github.com/VerdifyConsultancy/verdify-platform/issues/434) |
| PHYSICAL — Operator and seasonal commissioning | conditional | Unestimated | Not timeboxed | [#45](https://github.com/VerdifyConsultancy/verdify-platform/issues/45), [#51](https://github.com/VerdifyConsultancy/verdify-platform/issues/51), [#52](https://github.com/VerdifyConsultancy/verdify-platform/issues/52), [#298](https://github.com/VerdifyConsultancy/verdify-platform/issues/298), [#398](https://github.com/VerdifyConsultancy/verdify-platform/issues/398), [#412](https://github.com/VerdifyConsultancy/verdify-platform/issues/412), [#751](https://github.com/VerdifyConsultancy/verdify-platform/issues/751) |
| FUTURE — Conditional future decisions and comparisons | future | Unestimated | Not timeboxed | [#14](https://github.com/VerdifyConsultancy/verdify-platform/issues/14), [#31](https://github.com/VerdifyConsultancy/verdify-platform/issues/31), [#245](https://github.com/VerdifyConsultancy/verdify-platform/issues/245), [#378](https://github.com/VerdifyConsultancy/verdify-platform/issues/378), [#379](https://github.com/VerdifyConsultancy/verdify-platform/issues/379), [#786](https://github.com/VerdifyConsultancy/verdify-platform/issues/786), [#787](https://github.com/VerdifyConsultancy/verdify-platform/issues/787) |
| ROLLUP — Program ownership | rollup | Unestimated | Not timeboxed | [#16](https://github.com/VerdifyConsultancy/verdify-platform/issues/16), [#75](https://github.com/VerdifyConsultancy/verdify-platform/issues/75), [#218](https://github.com/VerdifyConsultancy/verdify-platform/issues/218), [#350](https://github.com/VerdifyConsultancy/verdify-platform/issues/350), [#359](https://github.com/VerdifyConsultancy/verdify-platform/issues/359), [#430](https://github.com/VerdifyConsultancy/verdify-platform/issues/430), [#581](https://github.com/VerdifyConsultancy/verdify-platform/issues/581), [#750](https://github.com/VerdifyConsultancy/verdify-platform/issues/750), [#775](https://github.com/VerdifyConsultancy/verdify-platform/issues/775) |

Sprint allocations may cover bounded preparation. Full issue acceptance still depends on its predecessors, current evidence and declared physical or seasonal conditions. C0–C8 remain stage contracts; closed historical issues remain visible in the stage views below.

## S3 — Runtime truth and contained qualification (October 19–30, 2026)

Kind: sprint. Capacity: 80 engineering hours. Goal: October 19–30 execution envelope: 80 shared planning hours, with original estimates retained as history rather than measured remaining effort. Three original packets (#977/#980/#802) are accepted and five issues remain open. Run one product source lane, one independent owner lane and one source/science preparation lane. Prioritize the current-prefix/full48 qualification source gap and lifecycle/blinding clause map; use the retained #368 patch when native qualification is blocked. Do not repeat the exhausted unchanged-source subset or the delivered Slack pair.

Exit: Every scoped packet has a truthful terminal outcome and preserved evidence. #749 needs a real readiness/incident disposition; #639/#587 need the remaining unchanged acceptance clauses; #981 needs upstream clock qualification; #782 needs authentic scientific inputs. A blocked packet is a recorded outcome, not issue completion. Firmware/physical proof, lock/draw and production candidate promotion remain separate downstream deliverables.

| Issue | Accountable role | Engineering hours | Next action |
|---|---|---|---|
| [#587](https://github.com/VerdifyConsultancy/verdify-platform/issues/587) | Runtime/release lead | 4–8 | Publish the accepted named-alert scope and map every residual lifecycle/nonactuation/blinding criterion to its source guard and authentic precondition. Reuse the exhausted current-role subset. Prepare only a genuinely missing source case or legitimate current-identity qualification path; no duplicate alert pair, password installation or weak native test. |
| [#639](https://github.com/VerdifyConsultancy/verdify-platform/issues/639) | Runtime/release lead | 8–16 | Make the next packet the source-bound prefix-coverage gap and supported qualification plan: map all 48 inputs to real decision consumers, cover missing actual paths or select a truthful external full-coverage/HIL runner, then run admitted prefix qualification before changing the source order/grid. Reuse the exhausted contained subset; prepare a new native case only when it adds missing acceptance with authentic reachable preconditions. |
| [#749](https://github.com/VerdifyConsultancy/verdify-platform/issues/749) | Runtime/release lead | 2–4 | Join retained climate/probe/alert-producer evidence to identify a real repair or honest hold; prepare exact typed classification plumbing with negative tests. Maintain wetting hold until real qualifying evidence exists, then collect one separately scoped packet with unchanged thresholds. |
| [#782](https://github.com/VerdifyConsultancy/verdify-platform/issues/782) | Evidence/data lead | 4–8 | Prepare current authentic contributor/crop declarations for a prospective valid interval through supported source guards; collect original as-of selector inputs without fabricated clocks/assignments. Verify the existing winter collector at its naturally due first day. Compute only after usable inputs and native admission. |
| [#981](https://github.com/VerdifyConsultancy/verdify-platform/issues/981) | Data/platform lead | 3–6 | Consume the actual installed-source result from Home Assistant #878, make only an evidenced owner-lane repair if needed, then verify one bounded natural source/DB advancement and stale/missing denial. Correct the issue dependency prose to name the existing external blocker; selected nine-state #782 remains independent while #787 retains its energy dependency. |

## ALTERNATE — Alternate pull when an S3 packet is blocked

Kind: conditional. Goal: Reuse and reconcile the retained #368 source patch/tests in place of blocked S3 work, at most six planning hours; do not recreate it or add another capacity allocation. Integrate bounded #367 duration evidence into the next #390 release packet after contributor truth.

Exit: Reviewable current-source patch, meaningful negative cases and supported native CI admission plan. No firmware flash or new controller policy is implied.

| Issue | Accountable role | Engineering hours | Next action |
|---|---|---|---|
| [#368](https://github.com/VerdifyConsultancy/verdify-platform/issues/368) | Control lead | Unestimated | Reuse /private/tmp/verdify-s3-368-source/probe-validity.patch and the retained verdify-probe-validity-368 worktree; review against current main, run the prepared causal native tests and required firmware checks in admitted k3s, then deliver the bounded diagnostic correction without claiming full #368. |

## S4 — Integrated qualification and physical proof

Kind: conditional. Goal: Run #783 after contained #639/#587, independently of final science inputs. Plan #641 after #749 and contained qualification.

Exit: Generic actual-login vertical path accepted and separately attended physical proof accepted; preserve source identities and all failed evidence.

| Issue | Accountable role | Engineering hours | Next action |
|---|---|---|---|
| [#641](https://github.com/VerdifyConsultancy/verdify-platform/issues/641) | Runtime/release lead | Unestimated | Keep PR #855 parked. First close the source/evidence gaps and prepare a current dormant successor diff/rollback manifest; only then bind one fresh remotely attended physical attempt and its cleanup. Do not retry Gate R already resolved. |
| [#783](https://github.com/VerdifyConsultancy/verdify-platform/issues/783) | Runtime/release lead | Unestimated | Compose a clause-by-clause current-role command/admission plan now using retained proofs; identify which stages are truly runnable unchanged and which require legitimate source qualification or a separately admitted disposable fixture. Do not alter clocks/guards to reach an active case. |

## S5 — Final identity lock, launch and first assigned outcome

Kind: conditional. Goal: Join #641/#782/#783 at #588; bind final design and recheck compatibility before one draw.

Exit: One immutable lock/draw, separately authorized day 1 and a genuine first assigned-day frozen export. No launch date promised.

| Issue | Accountable role | Engineering hours | Next action |
|---|---|---|---|
| [#588](https://github.com/VerdifyConsultancy/verdify-platform/issues/588) | Research/release lead | Unestimated | Prepare the exact manifest and final compatibility checklist now; perform no lock/draw until the real selected design and prerequisite receipts exist. |
| [#640](https://github.com/VerdifyConsultancy/verdify-platform/issues/640) | Research lead | Unestimated | Prepare exact reconciliation/export/backup commands and failure-day checks; execute only after the real assigned window and current source/UID binding. |
| [#642](https://github.com/VerdifyConsultancy/verdify-platform/issues/642) | Research/release lead | Unestimated | Prepare exact locked-identity/rollback packet now; activate only at the selected future boundary after #588, with no launch date promised by this replan. |

## PILOT — Pilot operation and readout

Kind: ongoing. Goal: Run the selected real calendar and preserve all assigned days; then reveal and reproduce the declared analysis.

Exit: Complete frozen outcome custody and bounded decision, including a valid null or inconclusive result.

| Issue | Accountable role | Engineering hours | Next action |
|---|---|---|---|
| [#784](https://github.com/VerdifyConsultancy/verdify-platform/issues/784) | Research lead | Unestimated | Prepare daily blinded ownership/exception/backup handoff now; after #640 operate the entire locked window and preserve failures until final safe closure. |
| [#785](https://github.com/VerdifyConsultancy/verdify-platform/issues/785) | Research lead | Unestimated | Prepare immutable report skeleton and independent reproduction command now; consume completed #784 custody once and publish the bounded result, including inconclusive/no-benefit if warranted. |

## RELEASE — Firmware and lighting release acceptance

Kind: conditional. Goal: Reuse delivered source/toolchain evidence and finish binary, rollback, actual-state and HA behavior acceptance in a selected release.

Exit: Each release meets its physical/runtime contract; no OTA is implied by this plan.

| Issue | Accountable role | Engineering hours | Next action |
|---|---|---|---|
| [#386](https://github.com/VerdifyConsultancy/verdify-platform/issues/386) | Platform lead | Unestimated | Reuse the existing source policy/tests, prepare a passive per-circuit boundary observation with exact timestamps and expected stop reasons, then record an eligible natural event. Reopen code work only if current evidence contradicts the implemented contract. |
| [#390](https://github.com/VerdifyConsultancy/verdify-platform/issues/390) | Platform lead | Unestimated | Prepare one candidate-bound release packet for the selected #368/#367 work, reusing accepted tooling and replay coverage while refreshing changed-source tests and exact binary/rollback custody. Execute no OTA from routine CI. |
| [#963](https://github.com/VerdifyConsultancy/verdify-platform/issues/963) | Platform lead | Unestimated | Prepare the shared #390 candidate/binary/rollback/HA observation packet now from existing source and tooling evidence. Complete the missing exact release and actual-state checks in the selected firmware window; retain the issue until HA recovery and deployed binary/rollback acceptance are proven. |

## CONTROL — Control and irrigation backlog

Kind: conditional. Goal: Pull only demonstrated behavior-preserving fixes with meaningful negative cases; preserve current control during locked study periods.

Exit: Evidence-led issue-specific acceptance, not broad cleanup.

| Issue | Accountable role | Engineering hours | Next action |
|---|---|---|---|
| [#297](https://github.com/VerdifyConsultancy/verdify-platform/issues/297) | Control lead | Unestimated | Reuse the existing null-input contract to prepare the smallest alert-only evaluator and missing/stale/persistence/recovery fixtures. Configuration must remain unavailable/disabled until measured values exist; actual eligible drip skip/shrink follows #45. |
| [#299](https://github.com/VerdifyConsultancy/verdify-platform/issues/299) | Control lead | Unestimated | Reuse current pulse logic and add only missing actual-wrapper min-off, exact-boundary, restart and manual-origin cases. Preserve requested pulse duration; carry actual re-fire validation in the next shared #390 packet. |
| [#367](https://github.com/VerdifyConsultancy/verdify-platform/issues/367) | Control lead | Unestimated | Reuse bc351370 from 3ebba10e, rebase only the duration-refresh correction onto current main, preserve the84 wrapper cases and submit the smallest compatible firmware cohort through existing admitted native CI. Keep full single-authority/physical acceptance open. |
| [#369](https://github.com/VerdifyConsultancy/verdify-platform/issues/369) | Control lead | Unestimated | Inventory only the named surviving #300/#326 properties end to end; select a minimal proven-dead set or record no removal. Defer a removal cohort until correctness fixes are delivered. |
| [#370](https://github.com/VerdifyConsultancy/verdify-platform/issues/370) | Control lead | Unestimated | After #367 source semantics are settled, choose one duplicated decision and capture current origin/reverse-conflict/restart/partial-delivery behavior before moving it into the pure resolver. |
| [#410](https://github.com/VerdifyConsultancy/verdify-platform/issues/410) | Control lead | Unestimated | Run the existing fn_realized_solar_night_dryout on a small admitted date/source window, retain solar semantics, eligible/confounded/incomplete outcomes and current disposition. Verify native function/source identity before trusting results; expand historical work only in k3s. |
| [#428](https://github.com/VerdifyConsultancy/verdify-platform/issues/428) | Control lead | Unestimated | Reuse the source-bound collector for a bounded passive current heap/reset/load snapshot and inspect exact rollback artifact custody. Explain cause or bound uncertainty; select code work only from a measured causal defect. |
| [#434](https://github.com/VerdifyConsultancy/verdify-platform/issues/434) | Control lead | Unestimated | Prepare the missing origin/conflict/calibration-negative cases in the shared firmware cohort, using current approved topology. After actual field mapping and calibrated dose/flush evidence, qualify the exact physical wall-feed sequence; do not enable automatic feed from a topology import. |

## PHYSICAL — Operator and seasonal commissioning

Kind: conditional. Goal: Collect authentic crop/probe/reference inputs and plan finite service windows.

Exit: Measured commissioning receipts or an explicit no-change decision; missing inputs remain visible.

| Issue | Accountable role | Engineering hours | Next action |
|---|---|---|---|
| [#45](https://github.com/VerdifyConsultancy/verdify-platform/issues/45) | Operator with data/control lead | Unestimated | After #398, bind every required channel to its real source/location and calibration, collect valid fresh readings, then execute the existing feedback check/finalizer with before/after requirements and warning evidence. |
| [#51](https://github.com/VerdifyConsultancy/verdify-platform/issues/51) | Operator with data/control lead | Unestimated | Prepare paired observations and a predeclared independent tolerance; obtain reference samples before calculating coefficients. Publish the measurement uncertainty and keep uncalibrated status until accepted. |
| [#52](https://github.com/VerdifyConsultancy/verdify-platform/issues/52) | Operator with data/control lead | Unestimated | Use the shared field observations to record a finite no-change decision or one specific reversible seasonal-care action with its basis and study-intervention handling. |
| [#298](https://github.com/VerdifyConsultancy/verdify-platform/issues/298) | Operator with data/control lead | Unestimated | Complete one field worksheet with assigned and explicitly unassigned positions, then reconcile config and DB from that evidence without enabling irrigation or overwriting valid operator settings. |
| [#398](https://github.com/VerdifyConsultancy/verdify-platform/issues/398) | Operator with data/control lead | Unestimated | From #298 observations, prepare exact before/after target rows, validity/source revision and inverse preserving valid operator values. Apply only a new supported ledgered correction when actual values are known, then verify config/DB agreement. |
| [#412](https://github.com/VerdifyConsultancy/verdify-platform/issues/412) | Operator with data/control lead | Unestimated | At the shared field survey, record current position and the justified no-change or reversible closure decision, then a bounded safety readback if changed and a visible study-intervention record. |
| [#751](https://github.com/VerdifyConsultancy/verdify-platform/issues/751) | Operator with data/control lead | Unestimated | Use one field service worksheet to diagnose south and hydro separately, repair only the identified component, then retain calibrated before/after readings, truthful contributor diagnostics and any panel/intervention history. |

## FUTURE — Conditional future decisions and comparisons

Kind: future. Goal: Retain useful twin, comparator, economics and optional cutover contracts without treating them as current pilot prerequisites.

Exit: Select work only when its stated evidence and decision triggers exist; no speculative implementation commitment.

| Issue | Accountable role | Engineering hours | Next action |
|---|---|---|---|
| [#14](https://github.com/VerdifyConsultancy/verdify-platform/issues/14) | Research/platform lead | Unestimated | Keep zero-hours roll-up and select a concrete oracle question only if it resolves an observed validation gap; then consume #31 and consolidated #606 scope. |
| [#31](https://github.com/VerdifyConsultancy/verdify-platform/issues/31) | Research/platform lead | Unestimated | For a selected oracle question, generate the source consumer-to-export-to-compiled-action matrix and qualify one real missing active input first. |
| [#245](https://github.com/VerdifyConsultancy/verdify-platform/issues/245) | Data/platform lead | Unestimated | Prepare the current writer-fence, endpoint/pool, final-parity and rollback packet from existing accepted evidence now. Keep runtime cutover conditional on the actual architecture/downtime/window choice; measure its 60-minute observation and seven-day bake only after a real handoff. Do not promote old rehearsal data or repeat accepted recovery drills for planning alone. |
| [#378](https://github.com/VerdifyConsultancy/verdify-platform/issues/378) | Control lead | Unestimated | After #785, record no change, insufficient evidence or one bounded width/anchor proposal with exact rollback values; consume #410 only for the relevant night-anchor question. |
| [#379](https://github.com/VerdifyConsultancy/verdify-platform/issues/379) | Research/platform lead | Unestimated | Leave unallocated; reuse #786/#785 dispositions to decide whether response modeling is warranted before implementing another model. |
| [#786](https://github.com/VerdifyConsultancy/verdify-platform/issues/786) | Research/platform lead | Unestimated | Keep prepared/unallocated. Let #785 select no-build, repair, retest or an explicitly scoped prospective comparator; do not build an extra arm now. |
| [#787](https://github.com/VerdifyConsultancy/verdify-platform/issues/787) | Research/platform lead | Unestimated | Leave conditional. Use the existing specification to choose the narrowest measurable follow-on or explicit no-go after readout; do not automatically buy hardware, tune gas or fix every meter before deciding. |

## ROLLUP — Program ownership

Kind: rollup. Goal: Track child outcomes without duplicating engineering estimates or blocking children behind their umbrella.

Exit: Every required child has accepted evidence or an explicit recorded scope decision.

| Issue | Accountable role | Engineering hours | Next action |
|---|---|---|---|
| [#16](https://github.com/VerdifyConsultancy/verdify-platform/issues/16) | Operator with data/control lead | Unestimated | Maintain child results for #751/#298/#51/#52/#412 and #298→#398→#45. Reuse one field inventory and record enabled/disabled/uncommissioned states for each selected item. |
| [#75](https://github.com/VerdifyConsultancy/verdify-platform/issues/75) | Platform lead | Unestimated | Reconcile the health matrix to the 07:07 runtime readback and accepted #980 receipt, then consume the concrete #981/#963/#749 next actions. Keep shared alert implementation in monitoring-stack; do not repeat accepted exporter delivery or infer missing sensor recovery from green smoke. |
| [#218](https://github.com/VerdifyConsultancy/verdify-platform/issues/218) | Data/platform lead | Unestimated | Correct the roll-up to accepted #977 three-replica policy and completed bounded restore/spool/role evidence; refresh the residual failure-domain summary. Preserve PR #982 as merged endpoint source pending owner delivery verification, and keep #245 conditional without reopening accepted rehearsals or adding a new blanket gate. |
| [#350](https://github.com/VerdifyConsultancy/verdify-platform/issues/350) | Control lead | Unestimated | Maintain the physical chain #298→#398→#45→#297 and release/re-fire/topology work #390→#299→#434. Run source preparation independently and do not infer feeding authority. |
| [#359](https://github.com/VerdifyConsultancy/verdify-platform/issues/359) | Control lead | Unestimated | Prioritize bounded #368/#367 corrections and #410 outcome evidence before dead-code/arbitration refactoring or #378 retuning. Preserve fixed scientific endpoints and independent physical gates. |
| [#430](https://github.com/VerdifyConsultancy/verdify-platform/issues/430) | Control lead | Unestimated | Keep zero-hour roll-up ownership of those four children. Choose a later simplification only from current measured load and complete consumer inventory; label projected savings explicitly. |
| [#581](https://github.com/VerdifyConsultancy/verdify-platform/issues/581) | Research/release lead | Unestimated | Keep a zero-effort roll-up that links the current child evidence and completed packet versus unfinished issue distinction. |
| [#750](https://github.com/VerdifyConsultancy/verdify-platform/issues/750) | Runtime/release lead | Unestimated | Refresh roll-up status using accepted contained receipts and concrete remaining readiness/prefix/physical gaps; remove next-action prose about already-corrected checkboxes. |
| [#775](https://github.com/VerdifyConsultancy/verdify-platform/issues/775) | Evidence/data lead | Unestimated | Publish the complete current summary and revised bounded execution queue; re-estimate only explicitly chosen unfinished packets instead of treating original estimates as a remaining-hours ledger. |

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
