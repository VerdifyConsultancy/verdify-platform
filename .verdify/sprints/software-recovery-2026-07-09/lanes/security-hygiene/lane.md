# Security hygiene lane

## Outcome

Remove all approved database-password fallbacks, prove fail-closed injected authentication, inventory every production caller, and prepare a safe credential rotation without exposing secret material. The protected rotation itself still requires Jason's separate explicit authorization.

## Scope and boundaries

- GitHub issue: `#438`
- Baseline: `0a9a19a840be6bae1beba604497d880b3b74b1ef`
- Branch/worktree: `codex/software-recovery-2026-07-09` at `/Users/jason/repos/verdify-worktrees/software-recovery-20260709`
- Owned code: the five standalone clients, `tests/test_no_committed_db_password.py`, and security/hygiene records listed in `lane.yaml`.
- Forbidden: migrations, ingestor, MCP, firmware, Kubernetes manifests, history rewrites, raw secret disclosure, and unapproved production rotation.
- Record overlap rule: the sprint-wide `.verdify` glob does not authorize edits to another lane's directory. Each worker owns only its own status and evidence records.

## Dependencies and gate

There is no hard implementation dependency. Release control consumes this lane's closed rotation gate. Work may proceed through source remediation, tests, caller inventory, PR, and criticism, but the lane cannot complete and production release cannot start until Q-001 is explicitly authorized.

## Acceptance

1. All five clients have no literal/default password and fail closed without approved injection.
2. AST/source regression and redacted scans pass.
3. Every caller has a documented injection, restart, validation, and rollback path.
4. After explicit authorization only, every caller succeeds with the new credential and rejects the old one, recorded as booleans without values.

Required commands, evidence, Git/PR duties, escalation conditions, critic requirements, and the exact definition of done are authoritative in `lane.yaml`.
