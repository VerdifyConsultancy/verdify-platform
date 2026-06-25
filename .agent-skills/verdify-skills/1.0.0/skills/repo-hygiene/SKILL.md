---
name: repo-hygiene
description: Performs Wave 0 repository hygiene before feature execution by assessing documentation, source-of-truth artifacts, GitHub state, stale branches, tests, CI, secrets exposure, infrastructure declarations, ownership boundaries, and Verdify compliance. Use for inherited repositories, newly connected projects, or any repo that must meet the operating standard before sprint planning or lane dispatch.
compatibility: Requires repository read access, Git, Verdify CLI, and GitHub CLI or a current snapshot when GitHub state is material.
metadata:
  author: Verdify
  version: "1.0.0"
  lifecycle-order: "4a"
---

# Repo Hygiene

Make the repository safe for agents before feature work. Prefer assessment and
small safe cleanup; escalate ambiguous deletion, ownership, or policy changes.

## Canonical artifacts

- `.agent-workflow/hygiene/repo-hygiene.yaml` - compliance report
- `.agent-workflow/hygiene/repo-hygiene.md` - human-readable report
- `.agent-workflow/hygiene/repo-agent-scope.yaml` - repo-associated agent scope,
  ownership, responsibility, authority, and escalation contract when a
  controller or long-lived repo agent is assigned
- Proposed cleanup PRs or GitHub Issues for non-automatic work

Validate YAML against `../../schemas/repo-hygiene.schema.yaml` and, when
present, `../../schemas/repo-agent-scope.schema.yaml`.

## Procedure

1. Read `../../COMMON_OPERATING_CONTRACT.md`, `../../config/authority-matrix.yaml`,
   and repository `AGENTS.md`.
2. Inspect docs, `.agent-workflow`, ADRs, schemas, tests, CI, package metadata,
   issue templates, branch/worktree state, open PRs, and ignored/generated files.
3. Check for source-of-truth drift:
   - missing project definition, architecture, strategy, or sprint artifacts;
   - stale plans or docs contradicted by code;
   - duplicated backlog outside GitHub Issues;
   - branch identity conflicts.
4. Check operational readiness:
   - test commands and CI;
   - environment declarations;
   - deployment and rollback docs;
   - observability expectations;
   - secrets handling and policy files.
5. When a controller or long-lived repo agent is assigned, create or update
   `repo-agent-scope.yaml` from `assets/repo-agent-scope.template.yaml` before
   the agent claims operational ownership.
6. Score each area as `pass`, `warn`, `fail`, or `blocked`.
7. Apply only safe cleanup that is mechanical, reversible, and within policy.
8. Write the report and exact `REPO_HYGIENE_COMPLETE` criteria.
9. Hand off to `state-of-union`, `sprint-planning`, or a gate owner.

## Stop conditions

Open a gate for ambiguous deletes, protected docs, secrets exposure, missing
approval semantics, cross-lane ownership conflicts, production policy changes,
or any cleanup that could hide evidence.

## Load references only when needed

- Read `references/compliance-checklist.md` for required hygiene areas and
  pass/fail rules.
- Read `references/repo-agent-scope.md` when a repo-associated controller or
  long-lived agent needs a scope, ownership, responsibility, authority, and
  escalation contract.
