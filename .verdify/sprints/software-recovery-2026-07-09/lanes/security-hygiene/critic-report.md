# Independent critic report: security-hygiene

- Sprint: `software-recovery-2026-07-09`
- Lane: `security-hygiene`
- Baseline: `0a9a19a840be6bae1beba604497d880b3b74b1ef`
- Reviewed implementation head: `1dfb05fdab864a3d000095346ee5e16bd145df5d`
- Pull request: [#439](https://github.com/VerdifyConsultancy/verdify-platform/pull/439)
- Review date: 2026-07-09
- Critic outcome: **ESCALATE**
- Non-production checkpoint decision: **APPROVE AFTER RECORD CORRECTIONS**

The implementation and tests are acceptable as a non-production source-remediation checkpoint. The lane itself cannot receive `PASS` or `PASS_WITH_FOLLOWUPS`: `LANE-AC-04` is intentionally unexecuted and requires Jason's separate `Q-001` credential-rotation decision. The lane must remain `BLOCKED`, issue #438 and the rotation gate must remain open, and no recovery release may consume this checkpoint as proof that credential invalidation occurred.

## Findings

### HIGH — replacement credential URI compatibility was unspecified at the reviewed head; correction reviewed, commit required

**Evidence.** All five remediated helpers construct a PostgreSQL URI by interpolating `POSTGRES_PASSWORD` directly. The live lab publisher also constructs `DB_DSN` directly from `PGPASSWORD`, and other runtime manifests construct DSNs from the same Secret value. A synthetic `asyncpg` parser check confirmed that otherwise valid replacement values containing URI delimiters such as `@`, `/`, `#`, or `?` can change the parsed host or fail parsing. The test at the reviewed head asserted raw interpolation but the rotation runbook placed no constraint on the replacement alphabet.

**Impact.** An authorized rotation could produce a strong password that is valid for PostgreSQL but strands one or more consumers at the URI boundary, violating the lane's no-stranded-consumer outcome.

**Remediation reviewed.** The controller's pending record diff adds a hard stop requiring exactly 64 lowercase hexadecimal characters generated from 32 cryptographically random bytes, a non-secret `replacement_uri_safe` boolean, and a prohibition on ordinary base64 until every consumer percent-encodes credentials. This provides 256 bits of entropy using only URI-unreserved characters and is sufficient for this rotation. The correction must be committed before PR #439 merges. A future change may remove the temporary format contract by percent-encoding every DSN consumer.

### MEDIUM — immutable evidence records are stale and must be reconciled before merge

**Evidence.** At `1dfb05f`, the lane evidence still names revision `controller-baseline-precommit-20260709`; `status.yaml` still names baseline head `0a9a19a...`; and the closure report still says commit, PR, remote head, CI, and critic references are pending. In reality, PR #439 points to `1dfb05f`, is mergeable, and has 17 successful checks, eight intentional skips, and zero failed or pending checks.

The open rotation gate also overstates the source finding by saying the production password was committed in all five clients. Baseline AST review shows four clients had source default fallbacks; `daily-summary-snapshot.py` instead depended on the retired `/srv/verdify/.env` path. Both are insecure implicit-auth behavior, but the durable record must preserve the distinction.

**Impact.** Merging those stale claims would violate the lane's immutable-evidence and accuracy requirements even though the source fix is sound.

**Remediation.** In the controller evidence commit, tie the evidence and closure report to `1dfb05f`, PR #439, the green CI result, and this critic report; keep the lane state `BLOCKED` with only `LANE-AC-04` pending; correct the four-default-plus-one-env-file wording; and record the eventual evidence-commit SHA and remote-head match. Run `git diff --check` and the applicable artifact/schema validators after the update.

### FOLLOW_UP — replace the temporary password-shape contract with encoded DSN boundaries

The 64-hex compatibility rule is safe for this protected rotation but is an operational coupling. Track percent-encoding or structured connection arguments for every application and shell/manifests DSN builder so future credentials are not constrained by URI syntax. This is not required for the non-production source checkpoint or the currently blocked rotation once the reviewed hard stop is committed.

## Acceptance-criterion verdicts

| Criterion | Verdict | Critic evidence |
|---|---|---|
| `LANE-AC-01` | **PASS** | The product-code diff is limited to the five contracted clients; each prefers explicit `VERDIFY_DSN`, accepts injected `POSTGRES_PASSWORD`, and raises without either. The 20 tests exercise all five clients across source/default, explicit DSN, injected password, and missing-auth paths. |
| `LANE-AC-02` | **PASS** | Targeted tests, Python compilation, Ruff, and diff-check pass. An independent redacted current-tree scan reproduced eight baseline `generic-api-key` findings across three unrelated schema/test files and none in the five clients. The redacted history summary remains warning evidence for invalidation, not a claim that rotation occurred. |
| `LANE-AC-03` | **PASS AFTER CORRECTION COMMIT** | A local prod-overlay build independently enumerated every declared `verdify-app-secrets.POSTGRES_PASSWORD` workload in the caller matrix. The matrix provides injection, restart/rerun, validation, and rollback dispositions, including the live-only vision residual recorded by the worker. The reviewed URI-safe replacement hard stop closes the identified stranding risk. |
| `LANE-AC-04` | **BLOCKED / NOT RUN** | `Q-001` and `g-prod-db-credential-rotation-20260709` remain unresolved. No new-valid, old-invalid, restart, or rollback-disposition evidence exists, correctly. |

## Deterministic verification

The critic ran or independently inspected the following at `1dfb05fdab864a3d000095346ee5e16bd145df5d`:

- `python3 -m py_compile` for all five clients: **PASS**.
- `/Users/jason/repos/verdify-platform/.venv/bin/pytest -q tests/test_no_committed_db_password.py`: **20 passed**.
- `make VENV=/Users/jason/repos/verdify-platform/.venv lint`: **PASS**.
- `git diff --check 0a9a19a...1dfb05f`: **PASS**.
- Redacted current-tree gitleaks count/classification: **8 findings, 3 unrelated paths, 1 rule; no affected client**.
- Local `deploy/k8s/overlays/prod` render and exact Secret-key reference enumeration: **all declared consumers represented in the caller matrix**.
- PR #439 head/remote match: **both `1dfb05fdab864a3d000095346ee5e16bd145df5d`** at review time.
- PR #439 CI: **17 successful, 8 intentionally skipped, 0 failed or pending** at review time.
- Product/source scope: orchestration baseline is isolated in `6ad534f`; security source/test changes are isolated in `de2dbae`; `1dfb05f` is formatter-only.

No raw or reversible credential material was read, emitted, copied, or stored during this criticism. The critic did not repeat the live boolean comparison, read a Secret value, mutate production, rotate a role, restart a workload, sync ArgoCD, or perform an OTA.

## Controller-baseline and fallback review

The controller baseline does not falsely claim native Agent Platform readiness. It records native dispatch as `not_ready`, identifies the structured 501 worktree-operation failure and stale shared clone, and narrows the approved local fallback to isolated source/test/review worktrees. The fallback gate explicitly excludes production writes, credential rotation, destructive database work, secret handling, and bypasses of CI, critic, firmware, migration, GitOps, and runtime gates. That boundary is acceptable for subsequent implementation lanes.

The broad planning transaction contains no additional product-code, schema, migration, firmware, or Kubernetes-manifest change. The security source/test changes remain attributable and separately committed. The open production rotation gate is preserved across the sprint and release contracts.

## Merge and handoff decision

PR #439 may merge as a **non-production controller/source checkpoint** after the following exact conditions are met:

1. Commit the reviewed URI-safe replacement hard stop.
2. Reconcile lane evidence, status, closure, gate wording, PR/CI references, and this critic reference in the controller evidence commit.
3. Confirm the post-record diff contains no new product/source/test/runtime semantic change, `git diff --check` and artifact validation pass, remote head matches, and CI remains green.
4. Keep lane state `BLOCKED`, issue #438 open, the rotation gate open, and production release blocked.

No repeat critic session is required for a follow-up diff limited to the already-reviewed runbook correction, this report, and mechanical immutable-reference/status reconciliation. Any product/source/test change, caller-matrix semantic change, weakened gate, credential-format change, or production action requires fresh criticism.

The critic was explicitly limited to this report and therefore did not edit `status.yaml`; the controller must apply the required `BLOCKED` state reconciliation.
