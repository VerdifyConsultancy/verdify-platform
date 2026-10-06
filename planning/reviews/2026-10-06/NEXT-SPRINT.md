# Platform review and next sprint — October 6 UTC / October 5 Denver

The platform has recovered from the bounded S1 incidents. S1 milestone 32 is closed, eight of eight issues accepted. The next sprint is **S2 — Recoverability and reliable delivery (October 2026)**: six issues, **68 engineering hours**. Target 24 elapsed hours through independent work and reuse of existing implementation; this is a scheduling objective, not demonstrated execution time or permission to weaken acceptance.

The review covers all **63 currently open issues and 188 acceptance/review clauses**, the 85 campaign source nodes, and the complete 88-node native graph including 25 closed historical nodes. [The full matrix](ISSUE-TRIAGE.md) distinguishes fresh observations from retained historical assessments; [native graph receipts](NATIVE-DEPENDENCIES.md) prove both blocking and hierarchy DAGs acyclic and source-aligned. Two unrelated draft PRs (#774 and #855) remain preserved. No additional issue was closed by this review.

For direct answers about completed work, acceptance, blockers, decisions and acceleration, see [status against the original goal](STATUS.md).

## Current platform

The runtime capture is October 6 03:25–03:35 UTC, at main `a847e8f1aeb8fc1a59925d0ad77d945f875667bd`. Publication can advance main without changing these explicitly dated observations.

| Surface | Current evidence | Remaining limit |
|---|---|---|
| Production GitOps and images | Argo `verdify-prod-dark` Synced/Healthy; all 16 long-running workloads Ready; 26 rendered workload image specs equal live; 127 objects, zero Secrets | Ready services and matching digests do not prove physical qualification or recovery |
| API and MCP | Smoke 9/9; expected API SHA/digest; authenticated MCP initialize and exact 23-tool inventory | Legacy API compliance/planner scores are internal diagnostics, not qualified crop outcome |
| Controller and ingestor | Firmware `2026.10.2.0637.620a218a-wifi-bound`, fresh telemetry, uptime about 85.5 hours; sole Recreate ingestor UID unchanged, zero restarts | No OTA or last-good promotion; complete buffered-event failure matrix remains open |
| Fleet writer safety | Read-only pinned namespace census: eight hosts, 501 namespaces, zero probe errors, exactly one ESP32 established connection, expected node5 ingestor IP | Namespace device-monitor first returned UNKNOWN when a publisher pod appeared during the scan; that failure is retained. Census is bounded evidence, not a perpetual guarantee |
| Planner | Actual forecast/sunset/manual plans complete; zero current nonterminal overdue triggers; 24-hour delivery has 190 confirmed, two superseded, 387 observed and no failed rows | Four-hour Hermes log contains six missing planning-skill lookups; #953 remains open. Historical terminal timeouts are not current overdue work |
| Observability and lab | Both protected Grafana probes up; current lab publisher success, served/public generation hash agreement; no unresolved critical DB alerts at capture | 27 open/acknowledged warnings remain. Whole alert fault/delivery acceptance is broader than this healthy snapshot |
| Database and recovery | Production migration 274; latest paired backup succeeded; three CNPG clusters healthy, each 3/3, pinned TimescaleDB image | Actual rehearsal database `verdify_rehearsal` is at migration 270. Full SOURCE/A/B policy parity remains unqualified after retained 120.554741-second timeout; backups and historical PITR do not prove current restore parity |
| Runtime roles | Nine ordinary runtime group/login pairs lack superuser, bypassrls, createdb and createrole; source role implementation landed | Complete actual-current-login allow/deny and rollback matrix is missing; metadata and Ready pods are insufficient |
| Telemetry and physical truth | All 98 numeric columns reviewed over seven days; 23 have no data; north/east/west fresh | South triplet absent despite firmware four/OK claim; south soil moisture/EC zero, chemistry/interior PAR/DLI unavailable, crop/substrate seeds uncommissioned; no physical band revisions |
| Experiment and scientific program | Zero draws, assigned outcomes, freezes, exports, direct-proof receipts and open exposures; v2 warm June 1–July 30, 2027 contract exists | Pre-draw candidate is not design lock. Qualified physical targets/contributors, empirical covariance and joint power missing; no pilot or efficacy claim |
| Delivery/toolchain | Release pins separate candidates from production; generated CI contract untouched; firmware input tree unchanged from reviewed source | Full path-impact/authority/retry/exact-source delivery matrix remains open. Declared compile steps without executed secretless positive/negative receipt do not close #303/#304 |
| Ownership transitions | Native external dependencies refreshed; agents#4459 closed, #4451/#4461/#4014 open | #801 route adoption and #802 descheduler Git/live enforce mismatch retain owner boundaries; no global apply, eviction or route change |

Evidence: [health summary](health-summary.json), [telemetry coverage](telemetry-columns.json), [desired/live image comparison](image-comparison.json), [current CNPG ledgers](cnpg-qualified-current.json), [issue matrix](issue-triage.json). Raw SQL, log, authenticated probe and pinned census receipts are retained in `/Users/jason/maintenance/verdify-s2-review-20261005`; private planner context and credentials are not published.

## S2 pull set

| Issue | Hours | Accountable lane | Sprint exit |
|---|---:|---|---|
| #644 | 16 | Platform lead, coordinating agents owner | Exact-SHA path-aware delivery and device-safe promotion/runtime receipts, including authority and failure/retry cases; bounded Verdify integration, not closure of entire agents#4014 umbrella |
| #396 | 16 | Data/platform lead | Current-schema restore/parity, PITR, failover and rollback qualification alongside live DB; complete cutover option packet without executing cutover |
| #382 | 16 | Data/platform lead | Accepted buffered events survive restart and node loss; prove capacity, backlog, format recovery and measured recovery-time bounds |
| #643 | 12 | Data/platform lead | Every ordinary runtime login proves its actual allow/deny boundaries, service behavior and reversible rollout; preserve working grants and credential custody |
| #49 | 4 | Data/platform lead | Exact-ID historical suppression audit/cleanup, idempotency and reversal; no count-parity shortcut |
| #953 | 4 | Platform lead | Complete mounted Iris skill/reference inventory and real natural planning-cycle success without missing required lookups |
| **Total** | **68** | | **All literal issue acceptance clauses remain binding** |

All recorded same-repository native predecessors of the selected work are closed. Preserve their closed edges as history. No fake dependency on a containing epic is added. Work on delivery, ingest durability and inventory can begin independently. Restore/parity data and actual role proof must join before claiming integrated recovery complete. Serialize migrations, writer changes and shared production synchronization. Owner coordination for #644 is a bounded dependency, not permission to rewrite global fleet CI.

The tactical milestone replaces stage milestone membership for these six issues only; C4/C5 stage, priority, owner roles, native parents and dependencies remain in the campaign source. All other stage milestone assignments, labels, assignees and closed history remain unchanged.

## What remains outside the sprint completion claim

Physical qualification #749/#751 and empirical design #782 remain essential to the pilot, but their missing physical observations cannot be created by closing source prerequisites. #782 has closed recorded predecessors; its empirical evidence is still incomplete. #783 retains #782/#639/#587 dependencies. The critical pilot path remains physical qualification → Gate P #641, design/analysis #782/#783 → lock #588 → launch #642 → first frozen day #640 → genuine 60-day pilot #784 → readout #785. Deferred C6–C8 controls, hardware and resource claims remain deferred.

No firmware OTA, pilot activation, production database cutover, storage mutation or fault drill was executed for this review. Future NFS client/server, mount, remount, recovery or node restart changes must be coordinated with the existing Proxmox agent and made in the owning storage/host repositories, with fleet impact and recovery coordinates captured. This is a local Mac/iTerm ROOT review; no fleet broadcast or repo-pod execution is claimed.

## Delivery acceptance and custody

Use the [paste-ready local ROOT goal](GOAL.md) to begin execution. This review only carves off the sprint. Full delivery requires enforced checks, merged source, exact running digests, successful appropriate migrations/hooks, authoritative product behavior and issue-specific recovery evidence. CI, manifests, synthetic fixtures and backup success have distinct proof limits. Do not close partial acceptance or relabel unavailable physical evidence.

The planning renderer and published schema validate every campaign node, source path, dependency and generated view. Native milestone members and relevant issue comments are read back after publication; [publication receipt](publication-receipt.json) records verified IDs and hashes. Preserve the existing two draft PRs and unrelated worktrees.

Criteria provenance: 181 literal issue-body acceptance checkboxes and 7 retained reviewer-derived bug/Verify conditions for #317/#801/#802; those issues have no Acceptance checkbox section.
