---
name: lane-delivery
description: Implements and closes out one approved lane contract inside one leased Git worktree and one coding-agent session. Use only when assigned a specific lane, issue, contract, branch, worktree, and lease; also use for contract-scoped fixes requested by an independent critic.
compatibility: Requires Git, an active Verdify worker lease, repository build tools, and permission to push the lane branch and update its pull request.
metadata:
  author: Verdify
  version: "1.0.0"
  lifecycle-order: "7"
---

# Lane Delivery

You are a bounded worker. Implement one lane and finish its closeout in the same session.

## Start checks

1. Read `../../COMMON_OPERATING_CONTRACT.md` and the assigned lane/module contracts.
2. Inspect the active lease:

   ```bash
   ../../bin/verdify lane inspect --repo <repository> --lease-id <lease-id>
   ```

3. Confirm session ID, worktree, branch, issue, baseline, contract status, owned paths, prohibited paths, dependencies, and runtime namespaces.
4. Reconstruct relevant code and tests before editing.
5. Stop if the lease does not belong to this session or the contract is stale/unapproved.

Read `references/worker-procedure.md` before implementation.

## Implementation mode

- Work only inside the leased worktree.
- Modify only owned paths/interfaces unless a recorded coordination rule permits otherwise.
- Preserve public/module contracts.
- Use the lease's isolated database, container, cache, port, and namespace values.
- Run validation incrementally.
- Keep commits coherent and attributable.
- Create or update one PR linked to the issue and lane contract.
- Create/propose a GitHub issue for discovered work; do not smuggle it into this lane.

## Scope and decision changes

Stop and open a gate for missing upstream contracts, public API/schema changes, migrations, security-boundary changes, destructive operations, new privileged dependencies, ownership conflicts, or acceptance criteria that cannot be met as written.

Read `references/scope-change.md`. Do not patch the contract after implementation merely to match the diff.

## Closeout mode

Closeout is the final worker action, not a separate skill.

1. Run every required validation command and capture exact results.
2. Compare the diff with owned/prohibited paths and the baseline SHA.
3. Map evidence to every lane acceptance criterion.
4. Confirm commits are pushed and PR/head SHA are current.
5. Record untracked files, residual risks, discovered issues, and deployment implications.
6. Write `.agent-workflow/sprints/<sprint-id>/lanes/closeout/<lane-id>.closeout.yaml` and validate it against `../../schemas/lane-closeout.schema.yaml`.
7. Set the lane to `READY_FOR_CRITIC`; do not mark it integrated or complete.

Read `references/closeout-procedure.md`.

## Fix-forward mode

When the critic requests contract-scoped fixes, use a newly authorized coding session according to lease policy. Address only cited findings, rerun affected and required validation, update the closeout, and return to fresh criticism.

## Handoff

Provide contract, issue, PR, head SHA, closeout, evidence, known risks, session
ID, lease/worktree refs, and artifact refs to `independent-critic` and
`controller-loop` for session-ledger events. Do not reuse this session as
critic.
