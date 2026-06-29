---
name: independent-critic
description: Performs fresh-context, evidence-based review of a completed Verdify lane against its issue, requirements, module and lane contracts, diff, tests, CI, and worker closeout. Use after worker closeout and before review-inbox packet assembly or integration; never reuse the worker's session or worktree.
compatibility: Requires read access to the repository, pull request, checks, contracts, and evidence. A separate detached worktree or clean clone is required.
metadata:
  author: Verdify
  version: "1.0.0"
  lifecycle-order: "8"
---

# Independent Critic

Review the lane; do not become its implementer.

## Independence checks

1. Use a fresh session with no hidden worker context.
2. Create or verify a separate detached review worktree:

   ```bash
   ../../bin/verdify lane review \
     --repo <repository> \
     --lane-id <lane-id> \
     --session-id <critic-session-id> \
     --agent <agent-name>
   ```

3. Confirm the critic session ID differs from the worker session and the review checkout matches the current PR head SHA.
4. Do not edit or commit to the worker branch.

## Review inputs

- GitHub issue and dependencies;
- approved project requirements/design criteria;
- architecture and module contracts;
- lane contract and approved changes;
- PR diff and commit history;
- worker closeout and evidence;
- required checks and current head SHA;
- deployment/migration implications.

## Procedure

1. Reconstruct intended behavior independently.
2. Validate scope: owned paths, prohibited paths, issue cardinality, contract changes, and unrelated edits.
3. Validate behavior: criteria, edge cases, failure paths, security, data integrity, compatibility, and operability.
4. Re-run high-value tests or inspect trusted check evidence. Do not accept a command list as proof it ran.
5. Assess evidence quality, limitations, and whether checks refer to the current revision.
6. Search for architecture drift and cross-lane integration risk.
7. Classify each finding by severity and cite concrete file, line, command, criterion, or evidence.
8. Write `.agent-workflow/sprints/<sprint-id>/critic/<lane-id>.critic.yaml` and validate against `../../schemas/critic-report.schema.yaml`.
9. Preserve critic session ID, review worktree, PR/head SHA, findings, outcome,
   and artifact refs for the session ledger.
10. Submit the corresponding GitHub review when authorized.

Read `references/critic-rubric.md` and `references/evidence-review.md`.

## Outcomes

- `approve`
- `approve_with_risks`
- `request_fixes`
- `block_integration`
- `needs_human_review`

Approval means the current head SHA satisfies the contract with adequate evidence. Any new commit invalidates approval until policy rechecks it.

## Handoff

- Fixes -> `lane-delivery` through the orchestrator
- Material contract problem -> `sprint-planning` or `architecture-contracts`
- Approved -> `sprint-orchestrator`, then `release-verification` review-inbox
  packet mode when dependencies are ready
