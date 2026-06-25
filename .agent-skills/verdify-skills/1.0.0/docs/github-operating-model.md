# GitHub operating model

## Issues as backlog problems

Open an issue for a problem, desired outcome, defect, decision, or discovered work. The issue should explain why the work matters and what observable outcome would resolve it. Implementation detail belongs in approved contracts and pull requests.

Use native sub-issues for decomposition and native dependencies for blocked-by relationships. Use milestones or Projects for sprint grouping and fields such as Sprint, Lane, Workflow state, Risk, Target environment, and Evidence.

## One issue per lane by default

The normal cardinality is:

```text
one issue -> one lane contract -> one branch -> one worktree -> one worker session -> one PR
```

A multi-issue lane is permitted only when the issues are inseparable at the acceptance and merge boundary. The contract must list a coupling justification and the plan approval must explicitly accept it.

## Pull requests

Every implementation PR links its issue with a supported closing keyword, names the lane contract, states the outcome, proves scope, provides evidence, and describes deployment risk. The linked issue is not considered verified merely because GitHub auto-closes it after merge; outcome reconciliation may reopen or use a policy that delays closure.

## Required controls

Configure a repository ruleset or protected branch to require:

- validation and policy checks;
- at least one approving review;
- code-owner review after CODEOWNERS is configured;
- resolved conversations;
- no force pushes or branch deletion;
- a merge queue when concurrent lanes make stale integration likely.

## Deployments

Use GitHub environments or an equivalent deployment control with separate credentials, environment-specific approvals, deployment history, and a concurrency policy. Worker lanes do not receive production secrets.

## Local snapshots

`bin/verdify github snapshot` writes `.agent-workflow/github/snapshot.json` as an ignored cache. Refresh it before planning or reconciliation. It never overrides live GitHub state.

When a state-of-union decision depends on GitHub backlog or delivery reality,
write `.agent-workflow/strategy/github-backlog-sync.yaml` and validate it
against `schemas/github-backlog-sync.schema.yaml`. The artifact records
snapshot freshness, source limitations, issue findings, PR findings, lane
findings, delivery findings, and which control plane must carry each action.
