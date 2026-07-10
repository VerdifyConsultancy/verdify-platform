# Route decision

- Current state: `EXECUTION_FALLBACK_READY`
- Next skill: `sprint-orchestrator`
- Next mode: `local-fallback-dispatch`

Resource accounting is merged, so DLI availability is now dependency-ready.
The open production database credential-rotation gate still blocks release
mutation, but it does not block the approved source-only, production-read-only
DLI lane. Dispatch remains on the sprint-scoped isolated local-worktree
fallback because native Agent Platform worktree creation is unavailable.

## Missing artifacts

None.

## Open gates

- `g-prod-db-credential-rotation-20260709` — blocks production release mutation;
  it does not authorize or prevent the next source-only lane.
