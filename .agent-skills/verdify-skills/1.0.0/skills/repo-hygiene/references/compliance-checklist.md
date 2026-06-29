# Repo Hygiene Compliance Checklist

## Required areas

1. Repository identity and default branch are clear.
2. `AGENTS.md` and host instructions are present and current.
3. `.agent-workflow` exists and has no contradictory approved artifacts.
4. Project definition, architecture, module contracts, and state-of-union status
   are either approved, intentionally pending, or explicitly blocked.
5. GitHub Issues remain the backlog source of truth.
6. Open PRs, branches, and worktrees are attributable to issues or cleanup.
7. Tests, lint, schema validation, and CI commands are discoverable.
8. Secrets are not present in prompts, logs, source files, generated artifacts,
   or committed config.
9. Environment, deployment, rollback, and observability expectations are
   declared or recorded as gaps.
10. Lane ownership, protected files, and cross-lane interface change rules are
    documented.
11. Assigned repo controllers or long-lived repo agents have a validated
    `.agent-workflow/hygiene/repo-agent-scope.yaml` contract or an explicit gap.

## Scoring

- `pass`: evidence proves the area meets policy.
- `warn`: usable but incomplete; does not block safe planning.
- `fail`: must be fixed before sprint planning or dispatch.
- `blocked`: requires human decision, credentials, external access, or security
  handling before proceeding.

## Completion

Declare `REPO_HYGIENE_COMPLETE` only when every required area is `pass` or has
an approved exception with owner, rationale, and follow-up.
