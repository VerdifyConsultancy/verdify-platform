---
name: issue-triage
description: Investigates user-reported codebase or product problems, searches GitHub for existing issues, audits relevant implementation and evidence, and creates or updates issue-template GitHub Issues. Use when users ask to triage bugs or product problems, turn a list of findings into issues, find duplicate or related issues, research likely causes and fixes, or populate repository issue templates after code/log investigation.
metadata:
  author: Verdify
  version: "1.0.0"
  category: standalone
---

# Issue Triage

Convert reported problems into GitHub-native backlog records after real investigation. This skill creates or updates issues; it does not implement fixes, plan sprints, or replace GitHub Issues with private chat state.

## Start

1. Read `../../COMMON_OPERATING_CONTRACT.md` when available.
2. Identify the repository root, default branch, current dirty state, GitHub repository, issue templates, active labels, recent commits, and open issues.
3. Split the user's input into one candidate problem per user-visible outcome or failure. Keep separate problems separate unless evidence shows they are the same issue.
4. Refresh/search GitHub Issues before creating anything. Search by user wording, error text, stack traces, affected paths, symbols, labels, and related closed issues.

Read `references/issue-record-contract.md` before writing or updating GitHub issues.

## Investigation

For each candidate problem:

1. Preserve evidence labels: `reported`, `observed`, `verified`, `inferred`, and `unknown`.
2. Inspect relevant code, tests, docs, recent commits, configuration, issue history, and logs or runtime evidence when the user provides access.
3. Reproduce with a deterministic command only when practical and non-destructive. Record exact commands and results; otherwise record why reproduction was skipped.
4. Summarize likely cause, affected users/surfaces, severity, confidence, and fix options. Keep fix options as triage guidance, not hidden implementation scope.
5. Run an adversarial audit:
   - look for duplicate or broader existing issues;
   - look for counterevidence that weakens the suspected cause;
   - check adjacent failure modes, edge cases, migrations, permissions, security, data integrity, performance, accessibility, observability, and rollback implications;
   - identify missing logs, tests, telemetry, owner decisions, or product intent.

## Issue Action

Choose exactly one primary action per candidate:

- **Update/comment existing issue** when an open issue covers the same problem or desired outcome. Add new evidence, audit notes, likely cause, and issue-template gaps instead of creating a duplicate.
- **Link or reopen** when a closed/stale issue appears to cover the same problem but the failure is still present or has regressed.
- **Create a new issue** when no existing issue covers the problem/outcome. Use the repository issue form or template fields.
- **Stop for private handling** when the report contains credentials, private keys, sensitive production data, or an unpatched vulnerability that belongs in the repository's security process.

## Required Output

Report:

- created issue URLs, updated issue URLs, duplicates found, and related issues;
- investigation commands and evidence sources used;
- likely cause and fix options with confidence;
- adversarial audit findings and residual unknowns;
- any skipped reports and the reason they could not be triaged safely.

Do not close issues, assign milestones, create sprint plans, open branches, or edit implementation code unless the user separately assigns that lifecycle work.
