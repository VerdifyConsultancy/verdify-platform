---
name: controller-loop
description: Specifies or operates the long-lived Verdify outer loop that persists lifecycle state, wave state, child sessions, gates, status events, and handoffs independently of model conversation history. Use when a project needs resumable orchestration across planning, research, hygiene, sprint execution, review, fixes, replanning, deployment, and human sign-off.
compatibility: Requires approved lifecycle prerequisites, repository artifacts, access to session/runtime records, and Agent Platform API or MCP details when child sessions are actually launched.
metadata:
  author: Verdify
  version: "1.0.0"
  lifecycle-order: "6a"
---

# Controller Loop

Own durable orchestration state. Do not implement lane code, review your own
work, or bypass human gates.

## Canonical artifacts

- `.agent-workflow/controller/controller-state.yaml` - durable controller state
- `.agent-workflow/controller/session-ledger.yaml` - append-oriented session map
- `.agent-workflow/controller/waves/<wave-id>.yaml` - wave state when used

Validate controller state against `../../schemas/controller-state.schema.yaml`
and the session ledger against `../../schemas/session-ledger.schema.yaml`.

## Procedure

1. Read the approved project definition, architecture, module contracts,
   state-of-union, sprint artifacts, gates, and current GitHub state.
2. Reconstruct the current lifecycle state and pending child sessions.
3. Validate the transition against `references/state-machine.md`.
4. For each child loop, record session ID, executor, repository, branch,
   worktree, issue, lane, wave, owner, start time, heartbeat expectations, and
   stop condition.
5. Launch child sessions only through the configured Agent Platform API, MCP
   tool, or documented manual handoff.
6. Monitor durable events: status, blockers, closeout, critic outcome, CI,
   deployment, review feedback, gate decisions, and session loss.
7. Pause at human gates and protected transitions.
8. Append session-ledger events for lifecycle-significant transitions and
   record explicit exceptions for missing coverage.
9. Write updated controller state and session ledger.

Read `references/session-ledger.md` before creating or reconciling
`.agent-workflow/controller/session-ledger.yaml`.

## Stop conditions

Stop when lifecycle state cannot be reconstructed, an open gate lacks an owner,
session identity is ambiguous, production access is requested directly by a
worker, or the controller would need to make a protected design decision.

## Load references only when needed

- Read `references/state-machine.md` before defining transitions, waves, or
  failure recovery.
