# Lane and worktree model

A **lane** is a durable execution contract. A **worktree** is a disposable checkout used by one agent role/session. They are related but not identical.

## Identity and lease

Durable lane identity consists of sprint ID, lane ID, issue, branch, baseline SHA, module contract, and lane-contract hash. The machine-local lease additionally records role, agent, session ID, worktree path, timestamps, and runtime namespaces.

Leases live below the shared Git directory at:

```text
<git-common-dir>/verdify/leases/<lease-id>.json
```

They are not committed and do not replace GitHub or lane contracts.

## Worker worktree

`bin/verdify lane create` validates the approved contract, refuses an active duplicate lease, creates the branch and linked worktree, locks it, and derives isolated runtime names. The worker may use only that worktree for the session.

## Critic worktree

`bin/verdify lane review` requires a different session ID and creates a detached review worktree. The critic reviews but does not write to the worker branch. Changes return to the worker through a changes-requested decision.

## Runtime isolation

The lease generates deterministic values for:

- Compose project name;
- test-database suffix;
- Kubernetes namespace;
- port offset;
- cache prefix.

Projects may extend the contract with cloud resource, queue, bucket, or test-tenant namespaces.

## Cleanup

Release a lease with `bin/verdify lane release --lease-id ... --session-id ...`. Worktree removal is explicit and refuses dirty worktrees unless `--force` is supplied. Use `git worktree repair` after external moves and `git worktree prune` only for stale administrative records.
