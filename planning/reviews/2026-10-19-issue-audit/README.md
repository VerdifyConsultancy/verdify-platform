# Issue audit and resequencing, 2026-10-19

Repository: VerdifyConsultancy/verdify-platform. Main at `aa93a52193036cbe86e2a7a72f3764bbcd58a394` (PR #989, 2026-10-19T17:23:41Z). Evidence observed 2026-10-19 between 22:45Z and 23:30Z. This review is read-only. It changes no issue states, acceptance clauses or native edges by itself. Any edge change listed below is applied through `planning/backlog.yaml` and `python -m planning.render`, then `make planning-validate`, so that local and native graphs still match.

This review follows the dated-review convention in `planning/README.md` and sits beside `planning/reviews/2026-10-18-status/`. It does not rewrite that baseline.

## Scope and method

- **Scope:** all 69 open issues. That is 47 campaign issues (C0 to C8) from the 2026-10-18 status baseline, plus 22 estate issues filed 2026-10-19: #990 to #1001 labelled `estate-ownership` and #1002 to #1011 labelled `estate-audit`. #218 carries both campaign and `estate-audit` labels. #988 (standalone in the 2026-10-18 intake) is now closed.
- **Method:** each issue's body and comments were read. The audit then checked the issue against origin/main source, merged and open PRs, branch heads, native `blocked_by`, `blocking`, parent and sub-issue links, Argo CD Application status and live verdify objects (context `vallery-api-vip`, read-only, no Secret data). It also cross-checked jvallery/agents, jvallery/homeassistant and jvallery/monitoring-stack issues. Assessment vocabulary: not-started, partial, in-progress, merged-not-deployed, deployed-unverified, blocked.
- **Fact types:** each ledger evidence item is marked either `verified` (read directly from a source) or `inferred`. Missing evidence and how to obtain it is recorded for each issue. The per-issue status block in the ledger is meant to be posted as the issue's status comment.
- **Coverage check:** `gh issue list --state open` returned 69 issues, the planning snapshot holds 69, and the merged chunk ledgers hold 69 rows with no duplicates. No issue changed after its ledger `updated_at_seen`. No supplementary (`extra`) ledger rows were needed.

## Coverage ledger summary

| Assessment | Count |
|---|---|
| not-started | 25 |
| blocked | 23 |
| partial | 10 |
| in-progress (roll-ups and #218) | 8 |
| merged-not-deployed (#367, #368) | 2 |
| deployed-unverified (#386) | 1 |
| **Total assessed** | **69** (0 pending) |

Priority: P0 3, P1 22, P2 36, P3 8. The P3s and some P1/P2s on #990 to #1011 are proposed label additions. They are not yet applied.

**Closures:** none proposed. No open issue has shown full acceptance. #386 is the nearest candidate: it needs one passive boundary observation.

| Issue | Assessment | Priority | Standards / rules | Next action |
|---|---|---|---|---|
| #749 [C1][P0] Qualify current three-probe readiness and explicit safety ... | blocked | P0 | - | Keep FAIL sealed; obtain real wetting-criterion measurements and investigate spread lineage (sensor disagreement vs gradient); then run one new readiness packet. Verify: packet passes unchanged 4 F spread and... |
| #750 [C1][P0] Recovery/proof campaign: rebaseline current state and seal... | in-progress | P0 | - | Roll-up only: refresh after #749 produces a passing readiness packet and #641 a physical proof receipt; close only with both receipts and final safe-state receipt. |
| #775 [C0][P0] Campaign: trustworthy greenhouse outcomes → qualified pilo... | in-progress | P0 | - | Continue Oct 19-23 sprint packets (P1-P4). Decide whether estate issues #990-#1011 enter planning/backlog.yaml or stay estate-lane only; re-run native graph validation. Verify: planning validator MATCH and acyclic. |
| #75 [C4][P1] Observability program: prove actionable health from source... | in-progress | P1 | STD-OBS-01, STD-OBS-02, STD-OBS-05, R13 | Mark AC1 satisfied via #980; drive #981 (HA#878 source clock) and #963; fold #1000 metric/probe-key delivery into the coverage matrix (rules ship in monitoring-stack per R13). Verify with fresh readback + alert... |
| #218 [C5][P1] Durability program: recoverable backups, measured RPO/RTO ... | in-progress | P1 | STD-HA-02, STD-DR-03, STD-DR-04, R10, R11, R15 | Fold EST-AUD-109 ACs into the body; replace hostname pins with node-class/longhorn-attach labels (labels from proxmox-infrastructure per R15), keep replicas=1 until #245; run node7-cordon drill and record RTO;... |
| #297 [C6][P1] Add saturation alerts and a commissioned dispatcher-side d... | not-started | P1 | - | Implement alert-only sustained-saturation evaluator with missing/stale/persistence/recovery fixtures, disabled until measured targets exist; verify via native tests. Drip skip/shrink only after #45. Note: the... |
| #299 [C6][P1] Preserve center-mister re-fire protection through topology... | partial | P1 | - | Add the missing wrapper min-off/exact-boundary/restart/manual-origin native cases (source work is not gated by #390); carry physical re-fire validation in the next #390 packet. |
| #350 [C6][P1] Irrigation program: topology truth, overwatering protectio... | in-progress | P1 | - | Roll-up: progress #298→#398→#45→#297 and #390→#299→#434; close only when children accepted. |
| #359 [C6][P1] Control program: truthful crop corridors before evidence-l... | in-progress | P1 | - | Roll-up: ship #985 content via #390 release packet; finish #410 daytime/consumer evidence before #378 retuning. |
| #367 [C6][P1] Consolidate anti-chatter into one explicit dwell contract | merged-not-deployed | P1 | - | Document the single dwell authority and safety exceptions; include #985 in the next #390 candidate; after OTA measure starts/runtime for chatter/starvation. Verify via release receipt + relay-cycle readback. |
| #368 [C6][P1] Define shared sensor jump, flatline and contributor-integr... | merged-not-deployed | P1 | - | Carry #985 into the next #390 release; implement jump/stuck semantics with causal native tests in admitted k3s CI. Verify live diagnostics report true probe count after OTA. |
| #386 [C4][P1] Prove grow-light minimum-on behavior at the solar-window b... | deployed-unverified | P1 | - | Prepare passive per-circuit observation query; record one natural boundary event; close if source and relay agree with expected stop reasons. |
| #390 [C4][P1] Gate firmware releases on topology, cycling, runtime safet... | partial | P1 | STD-CI-04 | Assemble one candidate-bound release packet (#985 content + #367/#368), add archived-artifact provenance (coordinate with #1009 signing), request an attended OTA window, verify relay/cfg readback + rollback. |
| #410 [C6][P1] Measure realized solar-night dry-out before changing the c... | partial | P1 | - | Define observation/freshness contract; collect one prospective complete daytime window; observe a natural planner cycle consuming outcomes. Verify AC1-3 against those receipts. |
| #428 [C6][P1] Diagnose heap/watchdog risk and qualify the actual last-go... | partial | P1 | - | AC1 partly met (uncertainty bounded for passive load). Select profiling only for a concrete causal question; carry candidate runtime/last-good criteria in #390. |
| #434 [C6][P1] Enforce center-only climate mist and commissioned wall-onl... | partial | P1 | - | Add origin/conflict/calibration-negative cases in shared firmware cohort; physical feed only after #298 mapping and calibrated dose/flush. |
| #581 [C2][P1] Complete the bounded AI-admission pilot and publish a deci... | in-progress | P1 | - | Zero-hour roll-up; close only on #785 decision or explicit program-stop. |
| #587 [C1][P1] Qualify lifecycle authority and blinded monitoring in cont... | partial | P1 | R13, R23 | Continue per-clause map; join #783 identities; prepare only missing source cases. Note rehearsal-namespace retirement (#992) must not precede remaining contained qualification. |
| #639 [C1][P1] Qualify the component executor, exclusivity and recovery b... | partial | P1 | R23 | Obtain a causal pre-tick state reconstruction and prove row-wise propagation for the 21 gaps in admitted k3s CI; do not repeat the synthetic matrix. |
| #641 [C1][P1] Complete one fresh attended physical baseline–target–basel... | blocked | P1 | - | Keep #855 parked; prepare current dormant successor diff + rollback manifest; then request one attended window from Jason. |
| #782 [C0][P1] Finish the empirical inputs and frozen exploratory scienti... | blocked | P1 | - | Wait for authentic inputs: verify the Nov 2-3 2026 winter collection run (archived bytes, observer/day manifest, backup custody); obtain facility-owner contributor/crop identity. Verify: availability.json shows... |
| #783 [C1][P1] Qualify the complete current-login selector-to-frozen-anal... | blocked | P1 | - | After #639/#587 progress, establish own-LOGIN routes and an analyst LOGIN, then run the whole path twice. Verify: byte-identical export/analysis hashes. |
| #981 [C4][P1] Restore truthful energy source freshness and trace stale m... | not-started | P1 | R13, R22 | Implement the bounded source-clock consumer adoption using the HA SHELLY_SOURCE_CLOCK contract with stale/missing/future/nonfinite denials; deploy; then one natural source vs DB comparison. Verify: channel 0... |
| #1002 [AUD] Stop anonymous internet users running ad-hoc SQL via graphs.v... | not-started | P1 | STD-EXP-01, R3 | Implement option 1: convert lab.verdify.ai panels to Grafana public dashboards and disable anonymous Viewer; else option 2 curated-view role. Verify: anonymous POST /api/ds/query 401/403 via Cloudflare path; lab... |
| #1006 [AUD] Scope the internet-facing verdify-traefik to verdify-prod: it... | not-started | P1 | STD-K8S-02, R18, R21, R5 | Replace with namespaced Role/RoleBinding without secrets via PR; sync; delete cluster objects; add repo RBAC check. Verify: can-i = no; endpoint status codes unchanged; no forbidden in logs for 1h. |
| #14 [C8][P2] Twin program: qualify declared oracle coverage before usin... | partial | P2 | R14 | Keep as zero-hour roll-up; only if a concrete validation gap selects an oracle question, generate the #31 consumer-to-export-to-action matrix and register verdify-twin through the agents registry (R14). Verify with... |
| #16 [C7][P2] Hardware program: commission only the sensing and equipmen... | blocked | P2 | R22 | Roll-up only: drive #298 field worksheet first (unblocks #398→#45→#297), record enabled/disabled/uncommissioned state per item; per D-06/R22 record greenhouse devices in verdify-platform and link HA device-authority... |
| #31 [C8][P2] Close twin setpoint and actuator coverage gaps with genera... | partial | P2 | - | Deferred under #14. When selected, generate the 48-field consumer matrix (reuse #639 P1/P2 consumer classification) and qualify one missing active input; verify with failing-on-missing-input and self-diff PASS receipts. |
| #45 [C7][P2] Commission soil and runoff feedback before enabling irriga... | blocked | P2 | - | Wait on #398. Then bind channels, collect fresh readings, run existing feedback check/finalizer with before/after evidence. Optional graph hygiene: remove #296's blocked_by #45 edge (296 closed not_planned). |
| #51 [C7][P2] Calibrate CO2 against a real reference and publish measure... | blocked | P2 | - | Operator confirms an existing reference meter; record paired samples with predeclared tolerance; any coefficient change ships through #390. Verify by independent holdout samples within tolerance. |
| #52 [C7][P2] Define species-appropriate seasonal dormancy care from ope... | blocked | P2 | - | Use the shared #298 field survey to record a no-change or one reversible seasonal-care decision with study-intervention record. |
| #245 [C5][P2] Execute a separately bounded database cutover only after p... | not-started | P2 | STD-HA-02, STD-DR-03, STD-DR-04, R10, R18, R23 | Prepare current writer-fence/endpoint/final-parity/rollback packet from accepted evidence (read-only); request Jason's architecture+window decision; coordinate #992 so the rehearsal namespace is not retired before... |
| #298 [C7][P2] Rebaseline actual crop, pot and probe topology before feed... | blocked | P2 | R22 | Schedule one attended field survey (shared with #52/#412); reconcile config and DB from it without enabling irrigation. Verify repo/DB agreement readback. |
| #369 [C6][P2] Remove dead tunables and code from a verified consumer inv... | not-started | P2 | - | Inventory only named #300/#326 properties; record removal set or no-removal. Firmware removals ride #390. |
| #370 [C6][P2] Consolidate equipment conflict resolution into one pure ar... | not-started | P2 | - | After #367 single-authority semantics are documented, capture current conflict/origin behavior fixtures before moving any decision into the resolver. |
| #378 [C6][P2] Decide corridor widths from fixed-target crop and resource... | blocked | P2 | - | Hold bands stable; after #785, record no-change/insufficient-evidence/one bounded proposal with rollback values. |
| #379 [C8][P2] Gate grey-box forecasting and MPC on validated response mo... | blocked | P2 | - | Leave unallocated; reuse #786 disposition to decide go/no-go; close as not_planned only with a recorded no-go decision. |
| #398 [C7][P2] Replace stale soil threshold seeds with commissioned crop/... | blocked | P2 | - | After #298, prepare ledgered before/after target rows with inverse; verify config/DB agreement readback. |
| #412 [C7][P2] Close the seasonal door screen only when measured night co... | blocked | P2 | - | At the shared survey record position and decision; if closed, bounded safety readback + intervention record. |
| #430 [C6][P2] Simplify firmware only while preserving autonomous control... | in-progress | P2 | - | Zero-hour roll-up; choose any simplification tier only from measured load and complete consumer inventory. |
| #588 [C2][P2] Bind exact scientific/runtime identity and finalize one in... | blocked | P2 | - | Prepare manifest/compatibility checklist; explicitly decide v2 model identity (gpt-5.6-luna/medium vs live GPT-6 Luna/xhigh) and provider availability; no lock/draw until prerequisites close. |
| #640 [C3][P2] Freeze and reconcile the first real assigned-day outcome a... | blocked | P2 | - | Prepare reconciliation/export commands; execute after first real assigned window. |
| #642 [C2][P2] Activate and verify one randomized day 1 from the locked p... | blocked | P2 | - | Prepare locked-identity/rollback packet; activate only at the selected future boundary after #588. |
| #751 [C7][P2] Replace the failed south climate probe and diagnose hydro ... | blocked | P2 | R22 | Schedule one attended field-service window: diagnose south probe and hydro separately, replace only the failed component, record calibrated before/after readings. Verify: fresh finite south triplet in DB for 24h and... |
| #784 [C3][P2] Operate the full blinded assigned-day pilot with immutable... | blocked | P2 | - | Prepare daily blinded custody/exception handoff; operate the locked window only after #640. Verify: every locked assignment accounted for in custody manifest. |
| #785 [C3][P2] Reveal once, reproduce the frozen analysis and decide the ... | blocked | P2 | - | Prepare report skeleton and independent reproduction command; consume #784 custody once. Verify: independent rerun identical tables. Also: native link #785 blocks closed #586 is stale (#586 closed); remove that... |
| #786 [C8][P2] Decide whether a deterministic forecast comparator merits ... | blocked | P2 | - | Keep prepared/unallocated until #785 readout; then record go/no-go. Verify: decision comment linked to #785. |
| #787 [C8][P2] Decide and scope a measured heating/overnight/resource study | blocked | P2 | - | Leave conditional until #785 readout; then choose narrowest measurable follow-on or no-go. Verify: decision comment with resource coverage table. |
| #963 [C4][P2] Bound moisture-exchange state and preserve structured cons... | partial | P2 | R13 | Bind delivery into the selected #390 firmware release: confirm ingestor digest includes cb21fd3d, OTA the candidate in an attended window, then read back HA state length-error count. Verify: zero HA length errors for... |
| #992 [OWN] Run verdify-db-rehearsal as a disposable experiment namespace... | not-started | P2 | R23, R10, STD-DR-03, STD-OBS-01 | Add expiry label and sync verdify-db-rehearsal-cluster; snapshot the two spool PVCs now. Verify: App Synced, snapshot IDs recorded. Deletions wait for S2 close and Jason's go. |
| #993 [OWN] Adopt NetworkPolicy verdify-prod/allow-db-from-restore into t... | not-started | P2 | STD-IAC-01, R1, R20 | Add the policy to deploy/k8s/components/db-backup matching live spec; sync. Verify: tracking-id present, zero diff, restore dry-run connects. |
| #994 [OWN] Record the verdify-db data PVC db-data-verdify-db-0 as protected | not-started | P2 | R10, STD-DR-03 | Annotate the PVC (metadata-only, no restart) through the supported path and record in the central map. Verify: jsonpath readback shows Prune=false,Delete=false; verdify-db pod UID unchanged. |
| #996 [OWN] Remove laptop-root-era verdify-agent RBAC | not-started | P2 | STD-K8S-02, R21 | Read subjects of both bindings, confirm no live SA/token; delete by exact name; record kind/name/uid/resourceVersion. Verify: readback NotFound. |
| #998 [OWN] Retire writer/DB watchdog CronJobs after monitoring-stack ale... | blocked | P2 | R13, STD-OBS-01 | Ask monitoring-stack to add a verdify-db down rule and record a firing test for writer+DB; then remove both CronJobs. Verify: alert test receipt, CronJobs NotFound. |
| #1000 [OWN] Close health coverage gaps for Verdify items and alert the gr... | not-started | P2 | R13, R22, STD-OBS-01, STD-OBS-04 | Write the metric/probe key table in this repo, file/link the monitoring-stack rules issue, link HA device-authority rows. Verify: test firing receipt for Shelly meter alert. |
| #1001 [OWN] Track Verdify transitions owned elsewhere (agents#4461, #4470... | blocked | P2 | R6, R8, R2, STD-IAC-03, STD-ARCH-01 | Track agents #4461/#4470/#4468; after #4468 closes delete ns verdify-edge here; drop D-16 hosts from HostRegexp after network-infra retires them. Verify per item readbacks. |
| #1003 [AUD] Route verdify-platform main changes, including prod release p... | not-started | P2 | STD-CI-01, R7, R14, R17 | Add main-push validation status, route release-pin promotion via PR, then ask agents to require the check. Verify: gh api commits/<sha>/status shows context on new main SHAs. |
| #1004 [AUD] Bring kubectl-patched verdify-prod workloads back under Git o... | not-started | P2 | STD-IAC-01, R6, R7, R10 | Diff live vs overlay at aa93a521, commit wanted values (decide longhorn-recovery toleration), declare suspend in Git, sync with SSA force-conflicts. Verify: managedFields readback shows argocd-controller only. |
| #1007 [AUD] Put edge pre-auth in front of mcp.verdify.ai: one static Iris... | not-started | P2 | STD-EXP-01, R2, R3 | Identify public authenticated callers; decide route removal vs Cloudflare Access with network-infra; rotate iris token. Verify: unauthenticated curl rejected at edge; Hermes 23-tool list OK. |
| #1008 [AUD] Add rate limits and request bounds to anonymous api.verdify.a... | not-started | P2 | STD-EXP-02, R2, R3 | Ship app bounds + camera cache first (PR), then Traefik RateLimit/InFlightReq; ask network-infra for edge rule. Verify: openapi.json maxima; 50-request burst gets 429. |
| #1009 [AUD] Sign greenhouse ESP32 firmware and flash only reviewed archiv... | not-started | P2 | STD-CI-04, R14 | Get Jason's decision on device-side signing; implement SHA-only builder + signed artifacts + narrowed egress first. Verify: tampered-fixture refusal; NetworkPolicy readback. |
| #990 [OWN] Delete verdify-prod qualification and restore-drill residue; ... | not-started | P3 | STD-ORG-03, STD-IAC-02, R1, R23 | Add TTL to the renderer via PR; preserve cited logs; delete listed objects by exact name (excluding allow-db-from-restore, #993). Verify: readback absent, verdify-prod-dark Synced/Healthy. |
| #991 [OWN] Prune verdify-db-rehearsal run residue (non-data, D-04) | not-started | P3 | STD-ORG-03, R23, R10 | Prune the listed non-data residue by exact name after confirming no consumer of the 8 client-auth Secrets. Verify: readback absent; PVC count stays 8; Applications health unchanged. |
| #995 [OWN] Adopt Namespace verdify-descheduler in place and record Verdi... | not-started | P3 | R1, R21, R18, STD-IAC-03 | Adopt the Namespace in place (prune off), add central-map entries; record R18 result for verdify-traefik as failing and point to #1006. Verify: tracking-id present, App Synced. |
| #997 [OWN] Keep Verdify dashboards single-sourced: delete stale ConfigMa... | not-started | P3 | R13, STD-ARCH-01 | Delete the four ConfigMaps by exact name; link jvallery/monitoring-stack#832 for the staged copy. Verify: verdify-prod Grafana dashboards load (28 via /api/search). |
| #999 [OWN] Pin the remaining tag-only verdify-prod images by digest | not-started | P3 | STD-K8S-05, R14 | Resolve running imageIDs, pin non-DB images in one PR; stage verdify-db separately after backup freshness check. Verify: no tag-only images in verdify-prod pod specs. |
| #1005 [AUD] Give verdify-lab real node redundancy instead of two replicas... | not-started | P3 | STD-HA-02, R10 | Make the site cache per-pod or RWX, drop required podAffinity, spread DoNotSchedule. Verify: pods on two nodes; delete one, lab.verdify.ai stays 200. |
| #1010 [AUD] Log the audience and real client IP of every successful verdi... | not-started | P3 | STD-OBS-06, R13 | Log mcp_tool_call on allow in authorize_tool_call; run uvicorn with proxy headers trusted from verdify-traefik only. Verify: Loki query shows events; public call shows external IP. |
| #1011 [AUD] Bound verdify-mcp egress and stop mounting the default Servic... | not-started | P3 | STD-K8S-04, STD-K8S-02, R20 | Add dedicated SA (automount false) and Egress policy in deploy/k8s/base; sync. Verify: pod spec readback; Hermes tool call succeeds. |

## Decisions recorded in this review

1. **No closures.** Merged PRs #985, #986 and #987 delivered scoped parts of #367, #368, #410, #428, #587 and #639, but no issue has its whole acceptance met. #367 and #368 are merged but not deployed: firmware OTA rides #390.
2. **#751 stays off the #749 dependency graph.** The #751 body says readiness uses a three-of-four degraded quorum. #749 ledger text also mentions the south probe repair, so its next step is attended wetting-criterion measurement plus a spread-lineage investigation, not #751.
3. **#297 keeps its native `blocked_by #45`.** That edge gates the actuation clauses (drip skip/shrink). The alert-only evaluator can be built now, so #297 is listed as ready for that portion only.
4. **#299 source work is not gated by #390.** The native #390 edge covers the physical re-fire proof only. The wrapper native cases are ready now.
5. **Estate issues (#990 to #1011) are standalone.** They have no parent. #775 must decide whether they enter `planning/backlog.yaml`. Until then they are sequenced here by risk.
6. **Proposed priority labels for estate issues** (existing labels only): P1 for #1002 and #1006; P2 for #992, #993, #994, #996, #998, #1000, #1001, #1003, #1004, #1007, #1008 and #1009; P3 for #990, #991, #995, #997, #999, #1005, #1010 and #1011. `gate:jason` is proposed for #1003, #1007 and #1009, and severity labels as given in the ledger.

## Graph analysis

- **Edges:** 29 active intra-repo and cross-repo `blocked_by` edges, counting native edges plus the proposed #1001 edges. **No cycles.**
- **Critical path (C1/C2 pilot):** #639 + #587 → #783; #639 + #587 + #749 → #641; #641 + #782 + #783 → #588 → #642 → #640 → #784 → #785 → #786 → #379. #785 also blocks #378 and #787, and #787 additionally waits on #410 and #981.
- **Firmware lane:** #390 → #367 → #370; #390 → #369; #390 → #299 → #434.
- **Irrigation and hardware lane:** #298 → #398 → #45 → #297 (actuation clauses).
- **Cross-repo inbound:** #981 was blocked by homeassistant#878, which is now closed, and homeassistant#894 merged 2026-10-19T04:27Z, so #981 is unblocked. #1001 tracks jvallery/agents#4461, #4470 and #4468, all OPEN, but the edges are missing natively.
- **Cross-repo outbound:** #31 and #75 block monitoring-stack#627; #75 blocks monitoring-stack#629; #218 blocks monitoring-stack#628.

### Edge fixes (to apply through backlog.yaml/render)

| Issue | Remove | Add | Reason |
|---|---|---|---|
| #390 | parent #644 (closed COMPLETED 2026-10-13) | parent #775 | stale parent; other C4 programs roll up under #775 |
| #1001 | — | blocked_by jvallery/agents#4461, #4470, #4468 | hard blockers named in the body; all open |
| #296 (closed) | blocked_by #45, #297, #434 | — | optional hygiene; #296 closed NOT_PLANNED |
| #586 (closed) | blocked_by #785, #390 | — | optional hygiene; #586 closed NOT_PLANNED |
| #963 | — | blocked_by #390 (proposed) | remaining AC needs the #390 OTA; if planning treats release delivery as sequencing, record it as related instead |

## Execution sequence

**Ready, in priority order:**

1. #1002: stop anonymous ad-hoc SQL on graphs.verdify.ai. P1, severity high, public exposure.
2. #1006: replace the verdify-traefik ClusterRole with a namespaced Role. P1, severity high. The ClusterRole was live on 2026-10-19.
3. #994: protect the db-data-verdify-db-0 PVC. Metadata only, and it must happen before any verdify-prod GitOps sync work.
4. #981: truthful energy freshness. Now unblocked, and it unblocks #787.
5. #390: assemble the firmware release candidate packet. It has the highest downstream fan-out. The OTA needs Jason's attended window.
6. #639 and #587: C1 qualification. Together they form the critical path to #641, #783 and the pilot chain.
7. #368 and #299: firmware source semantics and native cases that feed the #390 packet.
8. #410: observation contract and daytime window (blocks #787). Then #297 (alert-only evaluator) and #386 (one passive boundary observation, a closure candidate).
9. #1004 (after #994, and not concurrently with #1006 or #993 syncs), #993 (before #990), #1008, #996, #1000, and #992 for the expiry label and snapshots only. verdify-db-rehearsal-cluster was OutOfSync/Healthy at aa93a521 on 2026-10-19.
10. P3 hygiene: #997, #990 (after #993), #991 (after contained qualification no longer needs the rehearsal namespace), #995, #1011, #1010, #999 and #1005.

**Next, once dependencies clear:** #963, #367, #369 and #428 after the #390 release. #370 after #367. #434 after #299. #783 after #639 and #587. #641 after #587, #639, #749 and an attended window. Then #588 → #642 → #640 → #784 → #785 → #786/#378 → #379, and #787 after #785, #410 and #981. #1003 can add main-push validation now: main aa93a521 shows 0 commit statuses and 0 check runs. Requiring that check needs jvallery/agents and Jason.

**Safe parallel sets:**

- {#1002, #1006, #994, #996}: different objects.
- {#639, #587}: experiment lane.
- {#390, #368, #299}: firmware lane, using separate worktrees.
- {#981, #1000, #410, #386}: observability and passive evidence.
- {#297, #1008}
- {#997, #995, #1010, #1011, #999}

Run #1004 on its own: it force-syncs verdify-prod with server-side apply.

**Blocked or deferred, with the required next action:**

- **#749 (P0):** blocked on physical wetting-criterion measurements. Schedule an attended measurement, then run one new read-only readiness packet.
- **#782:** authentic inputs arrive with the Nov 2–3 2026 collection. After Nov 3, verify the archived bytes, manifest and custody.
- **Hardware gate:** #298, #52, #412 and #751 share one attended field survey. #51 needs a reference CO2 meter. #398 and #45 follow #298.
- **#245:** needs Jason's architecture and window decision. Prepare the read-only packet first. #992 retirement must not come before it.
- **#998:** monitoring-stack has no verdify-db death alert. First check whether monitoring-stack#999 (baseline workload/PVC alerts) covers it; if not, file the ledger follow-up there and link it.
- **#1001:** waits on jvallery/agents#4461, #4470 and #4468.
- **#1007 and #1009:** need a decision from Jason (gate:jason). The read-only inventory and design can be prepared now.
- **#14 and #31:** deferred at zero hours until an oracle question is selected.
- **#16:** roll-up of hardware-gated children.

**In progress (roll-ups):** #775 (P0 root), #750, #581, #359, #350, #75, #218 and #430. Each closes only when its children are accepted. #218 has active work: node-class/longhorn-attach labels from proxmox-infrastructure (R15), a node7-cordon drill with measured RTO, and replicas=1 until #245.

## Active branches and open PRs (observed 2026-10-19)

| Ref | Head SHA | Relation |
|---|---|---|
| PR #855 (draft) `agent/c1-gatep-activation-20260927` | `f8bd0cba5823` | Gate P activation for #641; parked until #641 prerequisites and the attended window |
| `codex/firmware-validity-dwell-20261018` | `db948490bfc1` | #367/#368 (content merged via #985) |
| `codex/native-replay-coverage-20261012` | `3ebba10e2890` | #367 |
| `codex/getter-timeout-evidence-20261018` | `0de800dd3d82` | #410 evidence |
| `codex/daytime-consumer-evidence-20261018` | `84f5013d322b` | #410 evidence |
| `codex/s3b-evidence-20261018` | `c8cbc74266bd` | #428/#639 evidence |
| `codex/sprint-plan-20261018` | `541c11ad0786` | #775 sprint plan |
| `in-progress/paused-20261012` | not found | cited by the #749 ledger; no such branch exists (possibly a label or a deleted branch) |

Merged PRs cited as partial delivery:

| PR | Merge SHA | Date | Covers |
|---|---|---|---|
| #905 | e857c1beda | 2026-10-01 | #386 |
| #932 | efaa4b6770 | 2026-10-02 | #786 prep |
| #973 | cb21fd3d32 | 2026-10-12 | #963 |
| #982 | 83e148b609 | 2026-10-18 | #218/#245/#992 |
| #985 | db896d0fb2 | 2026-10-18 | #367/#368/#587/#639/#749 |
| #986 | d298f1ecca | 2026-10-18 | #390/#410/#428/#639 |
| #987 | 7b48a210e8 | 2026-10-18 | #410 MCP release |
| #989 | aa93a52193 | 2026-10-19 | OWNERSHIP.md |

PR #971 was closed unmerged. The repository has 1,279 branches in total; only the ones cited by open issues are listed above.

## Verification results (runtime, 2026-10-19)

**Argo CD:**

| Application | Sync / health | Revision |
|---|---|---|
| verdify-prod-dark | Synced/Healthy | aa93a52193 |
| verdify-db-rehearsal-namespace | Synced/Healthy | aa93a52193 |
| verdify-db-rehearsal-cluster | **OutOfSync**/Healthy | aa93a52193 |
| verdify-descheduler | Synced/Healthy | b523a9b1ff |
| verdify-prod-secrets-local-prod | Synced/Healthy | 987cdbd75d |

**Live objects and CI:**

- ClusterRoles `verdify-traefik`, `verdify-agent-cluster-readonly` and `verdify-descheduler` are present. This supports #1006, #996 and #995.
- verdify-prod CronJobs: both `verdify-db-watchdog` (every 5 minutes) and `verdify-writer-watchdog` are present, so neither has been retired yet (#998). `verdify-firmware-builder` is suspended, which bears on #1004 (suspend should be declared in Git).
- Main `aa93a521` has 0 commit statuses and 0 check runs. This supports #1003.

**External links:**

- homeassistant#894 MERGED 2026-10-19T04:27:02Z; homeassistant#878 closed.
- jvallery/agents #4461, #4468 and #4470 are all OPEN.
- monitoring-stack #627, #628, #629 and #832 are OPEN.

## Cross-repo dependencies

| Repository | Dependency |
|---|---|
| jvallery/homeassistant | Shelly source clock (#894 merged) for #981; device-authority rows (R22, D-06) for #751, #1000 and #16 |
| jvallery/monitoring-stack | Rules for #998 (DB death) and #1000 (Shelly meter); #832 dashboard copy for #997; outbound #627, #628 and #629 |
| jvallery/agents | #4461, #4470 and #4468 for #1001; branch-rule requirement for #1003; image registry registration (R14) for #14 and #999 |
| jvallery/network-infra | Edge rate limit for #1008; Cloudflare Access/route decision for #1007; D-16 host retirement for #1001 |
| jvallery/proxmox-infrastructure | Node-class labels for #218 (R15) |

## Unresolved questions

1. Do estate issues #990 to #1011 enter `planning/backlog.yaml`, keeping the planning validator MATCH, or stay estate-lane only? Owner: #775 and Jason.
2. **#588:** the v2 model identity (gpt-5.6-luna/medium or live GPT-6 Luna/xhigh) and provider availability. Owner: Jason.
3. **#245:** the database HA architecture and maintenance window. Owner: Jason.
4. **#1007:** remove the public mcp route, or put Cloudflare Access in front of it? **#1009:** device-side firmware signing? Owner: Jason.
5. **#998:** does monitoring-stack#999 cover verdify-db StatefulSet unavailability? If not, file the follow-up.
6. **#963:** is the #973 ingestor mapping live? Compare the running verdify-ingestor digest with the release-pins.yaml revision.

## Exact next actions for the coordinator or next agent

1. Apply the ledger status blocks and proposed labels. Labels are existing ones only: `P1`, `P2`, `P3`, `severity:*` and `gate:jason`.
2. Apply the edge fixes above through backlog.yaml/render and validate.
3. Start ready items #1002, #1006 and #994 in parallel. Verify with an anonymous `POST /api/ds/query` returning 401/403, `kubectl auth can-i` for the verdify-traefik SA returning `no` on secrets, and the PVC annotation readback with an unchanged pod UID.
4. Start #981 (verify that channel 0 freshness advances while power is a stable 0 W, and that a stale injection is denied). Start #639 and #587 in admitted k3s CI. Start the #390 candidate packet.
5. Schedule one attended field window with Jason covering #298, #52, #412, #751 and the #749 wetting measurements. Schedule a separate attended OTA window for the #390 release.
6. Re-run this audit after the Nov 2–3 collection (#782) and after the next #390 release.

---
_Applied to GitHub by the coordinator on 2026-10-19: 170 issue operations succeeded (status blocks, labels, native blocked-by and sub-issue links, evidence-based closures, follow-ups). Each issue's `<!-- planning-status -->` block is the live per-issue record._
