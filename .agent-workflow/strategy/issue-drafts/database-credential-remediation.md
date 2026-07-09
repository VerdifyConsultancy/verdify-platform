## Problem

A redacted repository-hygiene scan found committed application-database password fallbacks in five standalone scripts. A value-comparison check, performed without printing either side, confirmed that one committed literal matches the current production application credential. Git history retains earlier occurrences.

This violates the repository's no-secret policy and means source access can imply production database access. The raw value must never be copied into this issue, logs, commits, or remediation artifacts.

## Desired outcome

All database clients require injected `VERDIFY_DSN` or `POSTGRES_PASSWORD`; source and tests contain no credential fallback; the exposed production application credential is rotated through the existing secret authority; every consumer is restarted/verified; and old credentials no longer authenticate.

## Acceptance intent

- [ ] Remove literal/default password fallbacks from every current-tree database client and fail closed when neither approved environment input exists.
- [ ] Add AST/source regression tests covering every affected script and a redacted current-tree secret scan.
- [ ] Inventory actual callers and prove each injects an approved DSN/password path before merge/deploy.
- [ ] Do not print, paste, commit, log, or summarize the credential; evidence records only locations, auth mode, and boolean validation.
- [ ] Preserve Git history; no force push or history rewrite is part of this recovery.
- [ ] After explicit operator authorization, rotate the scoped production application credential in its secret authority, restart/reconcile all consumers, verify positive authentication with the new value and negative authentication with the old value, and record only redacted results.
- [ ] Production release remains blocked until rotation and consumer verification are complete.

## Non-goals

- Rotating unrelated credentials.
- Publishing the exposed value for forensic convenience.
- Rewriting repository history or force-pushing branches.
- Expanding database privileges.

## Dependencies and related gates

- Local source remediation is already authorized by repository policy and the recovery objective.
- Production credential rotation is a separately protected action and requires Jason's explicit authorization.
- Durable gate: `.agent-workflow/hygiene/gates/g-prod-db-credential-rotation.yaml`.

## Initial risk

Critical credential-exposure risk. Source cleanup is low operational risk; rotation has high coordination risk if any consumer is missed.

## Affected surfaces

Standalone renderer/snapshot/writer scripts, credential-injection callers, production application secret authority, API/MCP/ingestor/migration/site consumers as discovered, deployment restart verification, and repo hygiene evidence.

### Triage investigation

- Existing issue search: no open issue owned this current production credential exposure.
- Evidence inspected: redacted current-tree/full-history scan, AST of database clients, boolean comparison against the live secret, caller manifests and runtime auth modes.
- Reproduction: redacted/boolean checks only; no secret material was emitted.
- Likely cause: convenience fallbacks survived the k3s/injected-secret migration.
- Potential fix: fail-closed injection helper, coverage test, scoped rotation runbook, consumer-by-consumer verification.
- Adversarial audit: source cleanup alone does not invalidate history exposure; rotation alone fails if a hidden caller still relies on the old value.
- Confidence: high.
- Remaining unknowns: rotation timing awaits the protected operator gate; caller inventory is completed before that action.
