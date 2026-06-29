# Routing rules

## Precedence

1. Safety and incident policy.
2. Open human/policy gate that blocks the requested transition.
3. Live GitHub and Git state.
4. Approved default-branch artifacts.
5. Local unmerged artifacts.
6. Cached snapshots and narrative reports.

## Direct-routing exceptions

- A worker may invoke `lane-delivery` directly only with an approved contract, active lease, and explicit assignment.
- A fresh critic may invoke `independent-critic` directly only with worker closeout and a separate session/worktree.
- A deployment verifier may invoke `release-verification` directly only with an integrated revision and authorized environment.
- An incident may bypass normal planning only under the repository incident policy; follow-up definition and contracts are still required.

## Staleness

Treat artifacts as stale when their baseline SHA, referenced issue state, contract version, or architecture decision no longer matches the intended target. Staleness routes to the owning skill, not to ad hoc repair by a downstream role.
