# Verdify platform readiness — software recovery

Assessed: 2026-07-09T22:31:05Z

Verdict: **NOT READY for native Agent Platform lane dispatch**

The fleet dashboard, repo pod, authenticated API, tmux, and SSH surfaces are healthy. Browser-terminal WSS was not independently exercised. More importantly, these surfaces do not provide the missing execution primitive: Codex has no Agent Platform session tools, and the in-pod `add_worktree_agent` / `remove_worktree_agent` operations are explicitly disabled post-MVP and return structured HTTP 501 responses.

The four fixed claude, codex, openclaw, and hermes sessions all share `/workspace/verdify-platform/repo`. That repository is on retired `live/platform-main` at `e96f60d`, not the approved `origin/main` baseline `0a9a19a840be6bae1beba604497d880b3b74b1ef`. They cannot safely substitute for isolated lane worktrees.

## Readiness matrix

| ID | Domain | Status | Evidence / gap |
| --- | --- | --- | --- |
| PLAT-001 | Agent Platform API/MCP | FAIL | No create/poll/send/attach tools; the only worktree-agent mutations return 501. |
| PLAT-002 | Session/worktree lifecycle | FAIL | Four fixed sessions share one stale repository; no isolated lane lifecycle. |
| PLAT-003 | Namespace/placement/PVC | PASS | 38/38 repo pods and all nodes Ready; target pod has zero restarts, 64 GiB Longhorn PVC at 6%, quota 38/150. |
| PLAT-004 | Secrets/credentials | WARN | Injection paths exist, but protected application DB rotation remains open and release-blocking. |
| PLAT-005 | Environment boundaries | FAIL | Repo SA is admin in `verdify-prod`, including Secrets CRUD and pod exec; policy prose does not enforce isolation. |
| PLAT-006 | CI/CD/GitOps | WARN | Main CI and agent-sessions are healthy; prod is Healthy/OutOfSync by five, orphan reaper degraded, discovery-token gap open, pod clone stale. |
| PLAT-007 | DNS/ingress/dashboard | PASS | Dashboard API and edge are healthy, with 136/136 fleet sessions ready. |
| PLAT-008 | Observability | WARN | Readiness and 501 failures are observable; dynamic lane/lease events do not exist. |
| PLAT-009 | Browser terminal | WARN | API/tmux/SSH identities are proven, but WSS is untested and terminals point at the shared stale clone. |
| PLAT-010 | Review inbox | FAIL | Review requirements are contracted; no current packet or deployed revision exists yet. |
| PLAT-011 | Ledger/history | PASS | S8 is superseded; active controller `software-recovery-root-019f4826` and the current project ledger now record this recovery. |
| PLAT-012 | Minimum pilot | BLOCKED | Native create/remove cannot run; #2497 remains open/unbuilt although former blocker #2641 is closed. |

## Minimum native-platform pilot

The restoration proof must create one isolated non-production lane from current `origin/main`, prove unique branch/worktree/lease/session/terminal identities, exercise poll/send/heartbeat/closeout, record ledger events, and remove or recover the session without affecting the fixed sessions or `verdify-prod`. It must then carry one revision through CI, fresh criticism, review evidence, sign-off, and cleanup.

## Current execution decision

The native runbook and initial session-create requests are blocked. A separate sprint-scoped HumanGate records local Codex subagent dispatch as the fallback needed to honor Jason's explicit directive to deliver all approved software fixes and the authorized OTA. That exception does not authorize production DB credential rotation, direct worker production mutation, or bypass any CI, migration, critic, firmware, release, or runtime gate.

The stale session branch is owned by the `jvallery/agents` registry repository/session records, not by this product repository. Reassess this report after that source is corrected, a least-privilege production boundary is enforced, and a supported Agent Platform create/poll/send/attach/closeout/remove contract is live.
