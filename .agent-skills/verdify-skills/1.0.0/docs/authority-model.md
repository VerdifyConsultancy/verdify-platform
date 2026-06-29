# Typed authority model

“GitHub is the source of truth” is useful only when authority is assigned by information type.

| Information | Authoritative owner |
|---|---|
| Backlog problem, discussion, desired outcome | GitHub Issue |
| Hierarchy and blocking relationships | GitHub sub-issues and dependencies |
| Approved implementation scope | Versioned lane contract linked from issue and PR |
| Proposed code | Pull-request branch |
| Accepted code | Default branch |
| Architecture intent | Architecture artifact and ADRs on the default branch |
| Quality status | Required GitHub checks and linked evidence |
| Review decision | PR review plus critic report plus review inbox packet and diagnostic packet when runtime evidence is material |
| Release identity | Git tag and GitHub release |
| Deployment state | GitHub deployment/environment or platform deployment record |
| Runtime proof | Deployment verification evidence |
| Environment GitOps reconciliation | Validated environment reconciliation artifact plus controller/runtime evidence |
| Agent Platform control request | Validated control request artifact plus policy and review verdict |
| Gravity core extraction | Validated Gravity extraction plan plus source-object/provenance evidence |
| Local worktree owner | Machine-local lane lease |
| Session and wave history | Controller session ledger |

A Project view, local dashboard, YAML status file, or GitHub snapshot is derived unless listed above. Derived state must be refreshed or regenerated rather than treated as an alternate truth.

When authoritative records disagree, stop the transition, reconstruct current state, and use a decision or scope-change gate. Do not edit intent retroactively merely to make an implementation appear compliant.
