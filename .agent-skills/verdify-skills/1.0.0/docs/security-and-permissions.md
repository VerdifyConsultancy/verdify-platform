# Security and permissions

Use role-specific least privilege.

| Role | Normal access |
|---|---|
| Router/definition/architect/planner | Read repository and GitHub; write versioned planning artifacts through a PR |
| Worker | Write one branch/worktree; update its issue and PR; no production credentials |
| Critic | Read branch, PR, checks, and evidence; submit review; no worker-session reuse |
| Integrator | Merge approved PRs subject to rules; run system checks |
| Deployment verifier | Access only the authorized environment and deployment evidence |

Secrets should come from protected runtime identity or GitHub environments, not committed files or worker prompts. Production approval, database migrations, security-boundary changes, destructive operations, and policy exceptions require durable gates.

Prompt and contract files may contain sensitive project context. Apply repository access controls and retention policies accordingly. Evidence should prove outcomes without unnecessarily copying secrets or customer data.
