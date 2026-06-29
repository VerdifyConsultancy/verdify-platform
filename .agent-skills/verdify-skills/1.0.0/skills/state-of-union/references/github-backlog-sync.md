# GitHub Backlog Sync

Use this reference when state-of-union or sprint orchestration needs to
reconcile GitHub backlog and delivery state with Verdify artifacts.

GitHub remains authoritative for Issues, PRs, checks, reviews, deployments,
environments, releases, dependencies, sub-issues, milestones, labels, Projects,
and discussion. Local snapshots are caches. A `GitHubBacklogSync` artifact
records what was checked, what was missing, and which control plane must carry
each proposed action.

## Inputs

- Live GitHub state when available, otherwise
  `.agent-workflow/github/snapshot.json` with its capture timestamp and known
  limitations.
- North Star, project definition, architecture contracts, state-of-union,
  sprint plan, lane contracts, leases, PRs, checks, review packets, diagnostic
  packets, deployment evidence, runtime/log health evidence, and outcome
  records.
- GitHub Issues, sub-issues, dependencies, labels, assignees, milestones,
  Projects fields, PR branches, PR body links, check/workflow run status,
  deployment status, and environment records when available.

## Procedure

1. Prefer live GitHub reads. If using a snapshot, record it as a cache with
   freshness and limitations.
2. Record each API/source surface used: issues, PRs, checks, workflow runs,
   deployments, statuses, environments, projects, sub-issues, dependencies,
   timeline events, search, local artifacts, and Git state.
3. Classify issues using `issue-reconciliation.md`; do not treat a private list
   as backlog authority.
4. Reconcile PRs against lane branches, linked issues, base branch, draft/merge
   state, check status, deployment status, and review/release evidence.
5. Reconcile lane contracts against issue IDs, branch, PR refs, duplicate issue
   assignment, and current sprint/orchestrator state.
6. Record delivery and runtime-health findings separately from PR state. A
   merge is not deployment verification, and CI success is not log or telemetry
   health proof.
7. Record actions with the control plane that must apply them: GitHub issue,
   dependency, Project, PR, durable gate, state-of-union, sprint-orchestrator,
   or release-verification.
8. Validate the artifact against
   `../../schemas/github-backlog-sync.schema.yaml`.

## Completeness Rules

The artifact is incomplete when:

- it does not say whether live GitHub or a cached snapshot was used;
- snapshot freshness and limitations are missing;
- issue findings lack classification, evidence, GitHub refs, or required
  action;
- PR findings omit linked issues, branch/base branch, checks, deployment state,
  or review/release evidence when material;
- lane findings omit contract, issue, branch, PR, duplicate assignment, or
  mismatch details;
- delivery findings infer deployment from merge state instead of deployment or
  release evidence;
- proposed changes do not name where they must be applied.

## Stop Conditions

Stop and route to `state-of-union`, `sprint-orchestrator`,
`release-verification`, a durable gate, or the repository owner when:

- live GitHub and the snapshot disagree on a material issue, PR, branch, or
  deployment;
- a lane references a missing issue or duplicate issue assignment;
- an issue is ready only in local artifacts but missing or underspecified in
  GitHub;
- a PR appears merged or closed but deployment/outcome evidence is missing;
- write authority is required but not granted.
