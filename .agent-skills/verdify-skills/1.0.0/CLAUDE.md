# Claude Code instructions

Use `/project-router` as the default entrypoint for Verdify lifecycle work. Invoke a later skill directly only when its prerequisite artifacts and approval gates exist.

Mandatory repository rules:

- GitHub Issues are the backlog source of truth.
- GitHub issues, pull requests, checks, releases, and deployments form the operational control plane.
- One issue, lane, branch, worktree, coding session, and pull request is the normal execution unit.
- Never reuse a worker worktree for a second coding session or for independent criticism.
- Obey `COMMON_OPERATING_CONTRACT.md`, the lane contract, and `config/authority-matrix.yaml`.
- Record all material decisions and evidence in durable artifacts.
- Do not merge, deploy, or close issues based only on narrative claims.

Use `bin/verdify route --write` to reconstruct the next lifecycle step.
