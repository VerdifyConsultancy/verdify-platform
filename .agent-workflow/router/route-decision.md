# Route decision

- Current state: `EXECUTION_FALLBACK_READY`
- Next skill: `sprint-orchestrator`
- Next mode: `local-fallback-dispatch`

Resource accounting is merged, so DLI availability is dependency-ready. Jason
explicitly resolved the production database credential-rotation gate with
`rotate-now`; the controller may execute the approved runbook while the
source-only, production-read-only DLI lane runs independently. Dispatch remains
on the sprint-scoped isolated local-worktree fallback because native Agent
Platform worktree creation is unavailable.

## Missing artifacts

None.

## Open gates

None. Credential rotation is authorized but incomplete and remains subject to
every runbook hard stop and redacted proof requirement.
