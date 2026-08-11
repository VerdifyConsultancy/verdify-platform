# Redacted database credential scan summary

Snapshot: 2026-07-09T22:45:00Z

Scope: current worktree, full Git history, and issue #438 source remediation.

No raw or reversible secret material is included.

## Current tree

- Redacted `gitleaks detect --no-git`: **8 findings**, all `generic-api-key` rule matches.
- Files: `db/schema.sql`, `tests/test_12_fidelity.py`, and `verdify_schemas/tests/test_alert_envelope.py`.
- These eight are classified test/schema-shaped false positives already present at the recorded baseline.
- None of the five remediated standalone clients appears in the current-tree findings.
- Source/AST regression: all five clients read `POSTGRES_PASSWORD` without a default and accept explicit `VERDIFY_DSN`.
- Behavioral regression: **20 tests pass**, covering literal/default rejection, explicit DSN precedence, injected password DSN construction, and fail-closed missing injection for all five clients.

## Full history

- Redacted `gitleaks git`: **34 findings across 12 paths**.
- Rule counts: 30 `generic-api-key`, 2 `private-key`, and 2 `jwt`.
- Historical paths include four remediated renderer/writer clients plus retired `.env`/legacy/test/fixture paths. Values were not inspected or copied into artifacts.
- A separate boolean-only live comparison established that a committed standalone-client fallback matched the still-valid production application DB credential. That is why rotation remained a release-blocking technical prerequisite.
- History is not rewritten. Mitigation is current-source removal, protected credential rotation, consumer verification, and old-value invalidation.

## Required disposition

1. Merge the fail-closed source and tests.
2. Independently review redaction, caller completeness, and behavior.
3. Do not release the recovery wave until production rotation completes with new-valid/old-invalid boolean evidence and rollback readiness.
4. Triage other historical rule matches separately if live credential comparisons or current authority evidence make them actionable; do not infer exposure from a detector label alone.
