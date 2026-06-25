# ADR-0003: One coding session per leased worktree

- Status: accepted
- Date: 2026-06-22

## Decision

A worker lane acquires a machine-local lease before a worktree is created. The lease binds lane, issue, branch, baseline, role, agent, and session. A critic gets a different detached review worktree and session.

## Consequences

Parallel sessions cannot silently share index or runtime state. Lease cleanup becomes an explicit operational responsibility. Worktree paths remain non-durable runtime details.
