# Security-hygiene checkpoint closeout

State: **BLOCKED**, not complete. Source remediation and rotation readiness are prepared; protected production rotation is not authorized.

## Objective and delivered outcome

The five approved standalone clients now require `VERDIFY_DSN` or `POSTGRES_PASSWORD` and fail closed without injection. Twenty behavioral/source tests pass. Redacted current/history scan summaries, a complete five-client and live shared-secret caller matrix, and a protected rotation/rollback runbook are durable.

No production Secret, role, workload, database schema, firmware, or device state was changed.

## Scope audit

- **Lane-owned code:** the five scripts and `tests/test_no_committed_db_password.py`.
- **Lane-owned records:** `.agent-workflow/hygiene/**` and this lane's status/evidence/closeout files.
- **Controller-approved shared transaction:** project, North Star, architecture, strategy, router, platform-readiness, controller, historical S8 cancellation, and current sprint/lane artifacts. These establish the recovery baseline and local fallback; they do not broaden the security code change.
- **Out of scope:** no migrations, ingestor, MCP, firmware, deployment manifests, history rewrite, or credential values changed.

## Acceptance

| Criterion | Verdict | Evidence |
|---|---|---|
| LANE-AC-01: five clients have no literal/default and fail closed | PASS | SEC-EV-001, SEC-EV-002 |
| LANE-AC-02: source regression and redacted scans | PASS | SEC-EV-001 through SEC-EV-004 |
| LANE-AC-03: complete caller/injection/restart/validation/rollback matrix | PASS | SEC-EV-005, SEC-EV-006 |
| LANE-AC-04: explicitly authorized rotation, new-valid and old-invalid proof | PENDING/BLOCKED | SEC-EV-007, Q-001 |

## Adversarial findings

- The removed `/srv/verdify/.env` read has no live caller; daily summary is ingestor-owned and the historical VM cron is retired.
- The three renderers are live through a correctly Secret-injected lab publisher; direct laptop/repo-pod paths intentionally fail closed.
- `vault-operations-writer.py` is orphaned/manual-only and has a stale default output path.
- The live `verdify` DB role is superuser and owns 4,791 relations; a replacement-role shortcut is unsafe.
- The fleet SOPS registry/encrypted skeleton still targets retired `verdify-staging`; the production secret authority must be corrected before rotation.
- Full-history findings remain until credential invalidation; no history rewrite is proposed.

## Validation

- `python3 -m py_compile` for all five clients: PASS.
- `.venv/bin/pytest -q tests/test_no_committed_db_password.py`: 20 passed.
- `make VENV=/Users/jason/repos/verdify-platform/.venv lint`: PASS.
- Targeted Ruff: PASS.
- `git diff --check`: PASS.
- 58 changed/new YAML files parse; 55 schema-backed artifacts validate; exact 17-issue assignment and dependency DAG checks pass.
- Monolithic `make test`: not a valid laptop proof; it ran 641 passing tests but failed 139 and errored 10 because it assumes the retired local Docker/systemd/Vault/API stack. Required PR CI remains pending and authoritative for the baseline.

## Records and follow-ups

- GitHub issue #438 exists and remains open.
- Commit, PR, remote-head, CI, and independent critic refs are pending the controller checkpoint publication.
- Production rotation gate: `.agent-workflow/hygiene/gates/g-prod-db-credential-rotation.yaml`.
- The stale fleet production-secret authority and other unverified historical detector matches require separate disposition; neither is silently treated as fixed.

## Rollback and disablement

Before production use, source rollback is the parent commit. After authorized rotation, use the layered rollback in `database-credential-rotation-runbook.md`. Do not restore literal fallbacks or the retired `.env` behavior.

## Git state

Pre-commit checkpoint: worktree intentionally dirty with the controller planning/security transaction. No unrelated user change was removed or hidden. The report must be updated with immutable commit, PR, CI, critic, and remote-head refs after publication.
