# Security-hygiene checkpoint closeout

State: **BLOCKED**, not complete. Source remediation and rotation readiness are prepared; protected production rotation is not authorized.

## Objective and delivered outcome

The five approved standalone clients now require `VERDIFY_DSN` or `POSTGRES_PASSWORD` and fail closed without injection. Twenty behavioral/source tests pass. Redacted current/history scan summaries, a complete five-client and live shared-secret caller matrix, and a protected rotation/rollback runbook are durable. The runbook now hard-stops unless the replacement is a 256-bit, URI-safe 64-character lowercase hexadecimal value, preventing delimiter-driven DSN parsing failures across current consumers.

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
- Four clients carried committed password fallbacks; the fifth snapshot client implicitly loaded the retired `/srv/verdify/.env` path. The durable gate preserves that distinction.

## Validation

- `python3 -m py_compile` for all five clients: PASS.
- `.venv/bin/pytest -q tests/test_no_committed_db_password.py`: 20 passed.
- `make VENV=/Users/jason/repos/verdify-platform/.venv lint`: PASS.
- CI-equivalent Ruff format check and targeted Ruff: PASS.
- `git diff --check`: PASS.
- 58 changed/new YAML files parse; 55 schema-backed artifacts validate; exact 17-issue assignment and dependency DAG checks pass.
- Monolithic `make test`: not a valid laptop proof; it ran 641 passing tests but failed 139 and errored 10 because it assumes the retired local Docker/systemd/Vault/API stack. PR CI is the authoritative baseline and passed at the reviewed head.

## Immutable checkpoint

- Orchestration baseline commit: `6ad534f504b09d270f879e0a2c3d01c219ab0248`.
- Security source/test commit: `de2dbaeeeb3e3ff429b1bc1e7feb180951d1ff2d`.
- Reviewed implementation/format head: `1dfb05fdab864a3d000095346ee5e16bd145df5d`.
- Pull request: [#439](https://github.com/VerdifyConsultancy/verdify-platform/pull/439), mergeable at the reviewed head.
- Remote-head proof: local HEAD and `origin/codex/software-recovery-2026-07-09` both resolved to `1dfb05fdab864a3d000095346ee5e16bd145df5d` before the evidence-only reconciliation commit.
- GitHub CI at the reviewed head: 17 successful, eight intentional skips, zero failed or pending.
- Independent critic: `critic-report.md`; outcome `ESCALATE` for the overall lane because AC4 is protected and unexecuted, with explicit approval to merge this non-production checkpoint after the reviewed record corrections.
- The follow-up controller commit is limited to the critic-reviewed runbook/gate corrections, critic report, and mechanical immutable-reference/status reconciliation; it introduces no product/source/test/runtime semantic change.

## Records and follow-ups

- GitHub issue #438 exists and remains open.
- PR, reviewed implementation head, CI, remote-head, and independent critic refs are recorded above; the controller evidence commit and its CI rerun are the final publication step.
- Production rotation gate: `.agent-workflow/hygiene/gates/g-prod-db-credential-rotation.yaml`.
- The stale fleet production-secret authority and other unverified historical detector matches require separate disposition; neither is silently treated as fixed.

## Rollback and disablement

Before production use, source rollback is the parent commit. After authorized rotation, use the layered rollback in `database-credential-rotation-runbook.md`. Do not restore literal fallbacks or the retired `.env` behavior.

## Git state

The reviewed implementation head was clean and matched the remote. The controller evidence-only reconciliation is intentionally the sole pending diff at this report revision; no unrelated user change was removed or hidden. After it is committed and pushed, the worktree must be clean and the replacement CI run must remain green before merge.
