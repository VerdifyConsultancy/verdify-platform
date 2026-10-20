# Full open-issue audit and next sprint — October 20, 2026

The next sprint is **S6: public exposure and safe control preparation**, October 21–27 (five working days), using the existing planning assumption of 40 engineering hours/week. Six bounded packets total **25–34 hours**, leaving **6–15 hours** for integration, supported delivery, observations and unexpected failures. These are prospective estimates, not measured remaining effort or a promise to close six whole issues.

## Complete audit coverage

The GitHub snapshot has **62 open issues** and **one open PR**, parked draft #855. Every open issue body, metadata, all comment pages and native blocked-by pages were fetched; named external blockers and HA delivery were read independently. [ISSUE-REGISTER.md](ISSUE-REGISTER.md) assesses all 62 individually, including next action and actual completion limits. [issue-audit.json](issue-audit.json) retains extracted checklists, comment hashes/links, priority, allocation and dependencies. [before-open-issues.json](before-open-issues.json) preserves the exact pre-audit bodies/metadata. Exact later decisions are in [decisions.json](decisions.json); [external-issues.json](external-issues.json) captures owner issue states. This audits open issues; closed history is consulted where it changes current readiness, not represented as a new full audit of closed issues.

**28 issues have an actionable next packet:** 12 ready, 12 scoped-ready, two requiring an attended visit, and two ready for natural observation. Another 26 are currently dependency/input/external/decision blocked at their stated acceptance boundary; seven are zero-additional-hour roll-ups and one is deferred without a selected use case. #1007 is one of the externally blocked issues but contributes a separately ready one-hour discovery packet. An empty native blocker list does not prove authentic input or physical readiness.

The previous source plan allocated 41 open issues and retained closed #245. This review adds all 22 new ownership/security issues (#990–1011), records #245's no-cutover decision, preserves the six already consolidated closures, and gives every current open issue exactly one allocation. The planning priority vocabulary now includes P3 so the source faithfully represents current GitHub priorities. Existing original checklists and historical estimates remain; new planning does not mark technical acceptance complete.

## Verified current evidence

Read-only `kubectl --context vallery` observations during this audit:

- `verdify-prod-dark`: **Synced / Healthy**, revision `01735457467fa1fd8918641dfa48cd583e092896`.
- Effective `list secrets -A` for `system:serviceaccount:verdify-prod:verdify-traefik`: **yes**. Current source also declares the broad ClusterRole/Binding. #1006 remains an immediate exposure reduction.
- `verdify-prod/db-data-verdify-db-0`: the `argocd.argoproj.io/sync-options` annotation is absent. #994 is a small protective change before the DB pin.
- Grafana Deployment: `GF_AUTH_ANONYMOUS_ENABLED=true`. The current source/site uses both `/d-solo` iframes and `/render/d-solo` PNG fallbacks. Preserve both use cases when eliminating arbitrary public SQL. This audit reused the October 19 issue's benign SQL receipt; it did not execute another public datasource query.
- `verdify-cnpg-s2-integration`: healthy 3/3; six CNPG PVCs plus two spool PVCs still exist. The no-cutover decision has not retired these data resources.
- API source still fetches camera snapshots on every request; the restore-job renderer still lacks TTL. No security exploit, load burst, data inspection, OTA, sync, restart or destructive operation was performed by this audit.

Current source was fetched into an isolated worktree from origin/main at the revision above. The original checkout is hundreds of commits behind and contains unrelated `.kube/` work; it was not edited. Snapshot facts are dated, not guarantees about future execution readiness.

## Decisions that supersede stale issue prose

1. **Public graphs remain public** (#1002, Jason October 19). Prefer saved public dashboards or a properly confined public datasource; preserve the actual lab graphs. Disabling public access alone fails the task.
2. **External MCP is required** (#1007, DR-20). Keep the route; network-infra#752 supplies Cloudflare Access. Identify the client from logs and confirm it with Jason before issuing its service token. Separate client audience and Iris rotation are already approved; identity remains unknown.
3. **Campaign resumed** (DR-22). October 12 pause handoffs are historical. Qualification and physical gates still apply; no blanket approval question is reintroduced.
4. **Greenhouse visits are possible** (#298, revised DR-45). The earlier no-visit comment is superseded. One field survey can advance #298/#751 and supply #398/#45/#52/#412. #51 alone still lacks a reference CO2 sensor. Availability is not an actually scheduled visit or completed measurement.
5. **Final model identity is settled** (#588, DR-47): GPT-6 Luna/xhigh. Runtime/manifest compatibility still must be verified; the model-choice question is not reopened.
6. **Device-side signing is deferred** (#1009, DR-48). Full-SHA builder/toolchain/egress work can proceed; approved compensating controls are OTA ACL plus value-blind password rotation, coordinated with network-infra#77 and a safe firmware release. Do not treat the original signing proposal as a new requirement or silently rotate during this audit.
7. **No CNPG cutover** (#245 closed, DR-49). Keep the single production StatefulSet and backups. Rehearsal ends now. Retirement is already authorized after posted, verified dump/snapshot receipts; agents and storage-infra execute their owned cleanup. Older #587/#639 prose retaining rehearsal indefinitely is superseded. Preserve custody and settle actual target consumers before deleting; future qualification must use a supported target, not an automatic replacement namespace.

## Sprint packets and acceptance

| Order | Issue / accountable role | Hours | Sprint deliverable and evidence required |
|---|---|---:|---|
| 1 | [#994](https://github.com/VerdifyConsultancy/verdify-platform/issues/994), data/platform lead | 1–2 | Metadata-only protection for the exact DB PVC; owner and central records agree. Readback has `Prune=false,Delete=false`; DB pod UID and desired workload spec unchanged. No PVC recreation. |
| 2 | [#1006](https://github.com/VerdifyConsultancy/verdify-platform/issues/1006), platform/security lead | 6–8 | Least-privilege namespaced Traefik RBAC. Deliver Role/Binding before removing exact old cluster grants; update central ownership in its owner repo. Denied cluster-wide and local Secret access, working api/lab/labs/mcp/graphs paths with expected auth behavior, focused regression, one hour free of required-resource RBAC denials. |
| 3 | [#1002](https://github.com/VerdifyConsultancy/verdify-platform/issues/1002), platform/security lead | 8–10 | Preserve real anonymous lab graphs and both iframe/PNG use cases while eliminating unrestricted SQL. Inspect Grafana feature support and current panel dependencies first; select the approved saved-public-dashboard or curated-datasource path. Show rejected arbitrary query or confinement to curated views, statement timeout/grant readback, and browser-level anonymous rendering. No claim that Grafana edition-dependent datasource permissions exist without checking. |
| 4 | [#1008](https://github.com/VerdifyConsultancy/verdify-platform/issues/1008), API/platform lead | 5–7 | Bound public query parameters, camera cache size/lifetime/keying and concurrent upstream fetches; add trusted-client rate/in-flight limits. Negative/oversize tests and concurrent mock-NVR cache/refusal proof precede a small production readback. Preserve authentication and trusted-proxy semantics. Owner edge/Turnstile pieces remain explicitly open if not delivered. |
| 5 | [#297](https://github.com/VerdifyConsultancy/verdify-platform/issues/297), control lead | 4–6 | Alert-only sustained-saturation evaluator and persistence/recovery/missing/stale fixtures, unavailable or disabled until measured targets exist. Prove no device-write/skip/shrink path is enabled. Shared alert rules remain in monitoring-stack. Native #45 remains the actuation/full-acceptance blocker. |
| 6 | [#1007](https://github.com/VerdifyConsultancy/verdify-platform/issues/1007), platform/security lead | 1 | Inspect existing public access-log metadata and record the external client's supported identity or a precise attribution gap. No new credential or client token is issued from a guessed IP/shared token. Network-infra#752 and client confirmation remain full-cutover gates. If logs cannot attribute it, #1010 becomes a bounded replacement candidate. |

Start with #994 and #1006, then #1002 and #1008; place #297 after the public exposure fixes. The one-hour #1007 discovery can occur at the start without waiting for edge delivery. This is a serial pull order for one capacity budget, not an instruction to create parallel agent workloads.

**Finish and verify each packet before widening scope.** For #1002, inspect the real embed/feature constraints early; if the migration exceeds its timebox, retain the concrete compatibility findings and substitute a ready packet rather than disabling graphs or claiming closure. If #1006 needs an unexpected required permission, capture the denied resource and grant only evidenced namespace scope. Reserve covers rollout/readback and normal fixes, not an extra promised project.

## Replacement queue and near-term unlocks

Replace a genuinely blocked packet; do not stack these hours onto the 40-hour budget:

| Candidate | Pull condition and bounded estimate |
|---|---|
| #981, **4–8h** | First alternate: HA#878/PR#894 is closed and accepted. Product adoption is ready; authentic source-time and power must come from the same response. Stable zero, reversed CT and negative source cases remain honest. Final natural comparison may outlast coding time. |
| #996, **1–3h** | Remove exact legacy RBAC only after no current subject/consumer is established; retain inverse metadata. |
| #993, **2–4h** | Zero-diff adoption of restore NetworkPolicy; preserve access boundary and prove supported restore connectivity. |
| #1010, **3–5h** | Pull if #1007 logs cannot attribute success. Add tool/audience/result attribution and trustworthy client provenance; never log tokens or trust arbitrary forwarded headers. |
| #386 / #410, **1–3h each preparation** | Prepare/inspect existing natural observations, with real elapsed windows separate. Do not force a cycle, interpolate gaps or claim physical efficacy from existing source receipts. |
| #298 shared survey, **2–4h engineering plus Jason's visit** | Use a real booked visit; include south/hydro diagnosis (#751) and optional seasonal observations (#52/#412) within that window. No CO2-meter prerequisite, no guessed data. Downstream #398→#45→#297 actuation remains sequenced. |

Other actionable backlog is in the complete register: #218 placement/recovery planning, #367/#368/#390 release preparation, #990/#991 cleanup, #992 receipt-backed retirement, #995/#997 ownership cleanup, #999 non-DB pin preparation, #1000 coverage handoff, #1004 drift inventory, #1005 redundancy design, #1009 builder compensation and #1011 MCP isolation. Do not mistake these for additional sprint commitments.

## What remains blocked

[DEPENDENCIES.md](DEPENDENCIES.md) records the authoritative current active native edges and semantic gaps. Key chains are:

- #639 authentic pre-tick evidence and #587 authentic contained preconditions → #783 integrated actual-login proof; those plus #749 real readiness → #641 physical proof. Published source and synthetic tests do not complete these issues. Draft #855 stays parked.
- #641 + #782 authentic inputs + #783 → #588 final lock → #642 day 1 → #640 first outcome → #784 full pilot → #785 reveal → #378 tuning decision. No launch date is promised; November winter collection and the candidate June–July 2027 calendar are real external waits.
- #390 exact firmware/recovery/attended acceptance → #299/#367/#369/#963 full acceptance; #299 → #434 and #367 → #370. Missing jump/flatline semantics in #368 are distinct from merged finite-probe correction.
- network-infra#752 → #1007 cutover; network-infra#77 → #1009 final ACL/OTA compensation. monitoring-stack#1016 → #998 watchdog retirement. agents#4461/#4470/#4468 plus network D-16 retirements → #1001.
- #1003 still has an unanswered release-pin/branch-policy decision, despite zero native blockers. It is outside this sprint; do not ask it as a gate to the already ready work.

## Execution and closure rules

Use existing credentials, source-owner boundaries and supported CI/GitOps. Heavy builds/tests run only in admitted k3s paths after checking current context/namespace, identity, capacity, resource requests/limits, bounded concurrency/deadline and output custody. The audit's read-only context was explicitly `vallery`; inspect current placement before execution. Do not run firmware builds or the full `make ci` on this Mac.

Before a future production sync, inspect the complete pending diff, use an exact revision full nonpruning sync with hooks, and check the submitted selector is absent/empty. Verify Argo intended revision/health, exact running digests, one writer and issue-specific behavior; an Argo green status alone is insufficient. Preserve DB/PVCs. Individual issue status stays open when its remaining owner, physical, natural-window or final acceptance is incomplete. Sprint milestone membership commits the scoped packet above, not an assertion that its whole issue will close.

This change publishes the audit and execution scope. It performs no product/security remediation or production mutation.
