# Worker prompt: security-hygiene

Own lane `security-hygiene` for issue `#438`. Objective: remove approved DB-password fallbacks, prove fail-closed injected authentication, inventory every production caller, and prepare the protected rotation without exposing credential material or bypassing its gate.

Before changing anything, read:

1. `/Users/jason/.codex/skills/verdify-agentic-sprint/references/common-operating-contract.md`
2. `.verdify/sprints/software-recovery-2026-07-09/lanes/security-hygiene/lane.yaml` (authoritative)
3. Repo `AGENTS.md`, handoff, relevant source/tests, issue `#438`, and current Git state.

Baseline is `0a9a19a840be6bae1beba604497d880b3b74b1ef`; use branch/worktree exactly as contracted. Scope is only the five listed standalone clients, the regression test, hygiene records, and this lane's records. Do not touch migrations, ingestor, MCP, firmware, deploy manifests, other lanes' directories, or unrelated dirty recovery changes. The `.verdify/**` glob is explicitly carved out: update only this lane's records and shared security-gate records.

Work autonomously within those bounds. Require `VERDIFY_DSN` or `POSTGRES_PASSWORD`, fail closed when absent, add/maintain regression coverage, run redacted scans, and build a complete caller/injection/restart/validation/rollback matrix. Never print, log, commit, or summarize raw secret values. Do not rewrite history. Do not rotate or mutate production until Jason separately and explicitly resolves Q-001; sprint/OTA approval is not rotation authority. Escalate immediately for an unidentified caller, any credential-bearing output, missing authorization, failed old-credential rejection, or inability to isolate the dirty worktree.

Run every validation in `lane.yaml`, record structured evidence in this lane's `evidence.yaml`, and keep `status.yaml` current. Make coherent `#438` commits, push, open/update the contracted PR, update the issue with redacted evidence and gate state, and leave Git clean without staging unrelated files. Before handoff, adversarially audit source/history scan coverage, missing-injection behavior, caller completeness, secret leakage, and scope. Request the independent security/release critic; do not self-merge or claim COMPLETE until all acceptance criteria, CI, critic, explicit authorization, and redacted new-valid/old-invalid verification pass.
