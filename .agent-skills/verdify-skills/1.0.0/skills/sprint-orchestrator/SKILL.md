---
name: sprint-orchestrator
description: Coordinates an approved Verdify sprint or wave by reconciling GitHub, preparing an Agent Platform MCP execution runbook, dispatching dependency-ready lane worktrees and worker sessions, supervising lane agents through platform session and terminal interfaces, handling gates and coordination requests, monitoring CI/CD and deployment readiness, and routing completed lanes to criticism, review inbox, and release verification. Use only after sprint and lane contracts are approved, including when a user asks for a controller loop that owns lane agents, polling, MCP dispatch, tmux/browser terminal visibility, CI/CD, and review deployment coordination.
compatibility: Requires Git, the Verdify CLI, approved sprint artifacts, GitHub CLI for live reconciliation, and Agent Platform API/MCP details when platform sessions are launched. It coordinates but does not implement lane code or review its own work.
metadata:
  author: Verdify
  version: "1.0.0"
  lifecycle-order: "6"
---

# Sprint Orchestrator

Act as controller, not worker, critic, or deployment verifier. Coordinate lane
agents, CI/CD, review deployment readiness, and handoffs from durable evidence.

## Canonical artifacts

- `.agent-workflow/sprints/<sprint-id>/execution/sprint-execution-runbook.yaml`
  - sprint execution controller runbook for Agent Platform MCP dispatch,
  terminal visibility, polling cadence, lane session identities, CI/CD,
  deployment, review, and ledger requirements.
- `.agent-workflow/sprints/<sprint-id>/status.yaml` - sprint execution status.
- `.agent-workflow/controller/controller-state.yaml` and
  `.agent-workflow/controller/session-ledger.yaml` - durable loop and session
  history owned with `controller-loop`.
- Agent Platform control request artifacts when MCP/API operations need policy,
  authorization, target, result, and review records.

Validate the runbook against
`../../schemas/sprint-execution-runbook.schema.yaml`.

## Start

1. Read the approved sprint plan, wave release plan when present, plan gate,
   lane contracts, module contracts, execution runbook when present, and
   dependency order.
2. Refresh GitHub state:

   ```bash
   ../../bin/verdify github snapshot --repo OWNER/REPO --target <repository>
   ../../bin/verdify github reconcile --repo-path <repository> --sprint <sprint-id>
   ```

3. Confirm the baseline, issue state, branch naming, required checks, Agent
   Platform readiness, MCP operation identity, terminal access mode, and
   authorized agents.
4. Create or update the execution runbook before dispatch:

   ```bash
   scripts/build_execution_runbook.rb \
     --repo <repository> \
     --sprint <sprint-id> \
     --controller-session-id <controller-session-id>
   ```

5. Create or update per-lane Agent Platform control request stubs when session
   creation is not already authorized:

   ```bash
   scripts/build_platform_control_requests.rb \
     --repo <repository> \
     --runbook <repository>/.agent-workflow/sprints/<sprint-id>/execution/sprint-execution-runbook.yaml
   ```

6. Mark only dependency-ready lanes as dispatchable.

Read `references/platform-execution.md` before creating platform sessions or
running the controller poll loop.

## Dispatch

For each ready lane, create exactly one worker worktree/session. Prefer the
configured Agent Platform MCP/API operation recorded in the runbook and control
request. Use `bin/verdify lane create` only for local lease/worktree identity
or documented fallback; do not replace platform dispatch with ad hoc local tmux
or Claude CLI sessions.

```bash
../../bin/verdify lane create \
  --repo <repository> \
  --sprint <sprint-id> \
  --lane-id <lane-id> \
  --issue <issue-number> \
  --session-id <unique-session-id> \
  --agent <agent-name>
```

Compile the worker prompt from the contract. Record lease ID, Agent Platform
session ID, terminal/tmux/browser refs, branch, PR, issue, and agent session in
the execution runbook, sprint status, and session ledger. Never dispatch two
workers into the same lane/worktree.

## Monitor events

Run the configured controller loop, typically every five minutes unless the
runbook says otherwise. Poll durable platform/session state and terminal panes
only to observe or answer the assigned lane agent. Handle durable events rather
than trusting free-form chat narratives:

- heartbeat/status update;
- blocker;
- decision required;
- scope-change request;
- coordination request across lanes;
- worker closeout;
- critic changes requested or approval;
- CI failure;
- review deployment readiness;
- terminal/session loss;
- dependency completion;
- lease expiry or abandoned worktree.

Coordinate with `controller-loop` so dispatch, lease, closeout, critic,
review, deployment, gate, and handoff transitions have session-ledger events or
explicit exceptions.

Read `references/state-machine.md`, `references/platform-execution.md`, and
`references/gate-management.md`.

## Reconcile

At every material transition, compare issue, PR, checks, contract, lease, and artifact state. GitHub is authoritative for backlog and delivery; the lane contract is authoritative for scope; the lease is authoritative only for local worktree ownership.

Read `references/github-reconciliation.md`.

## Role boundaries

- Do not modify lane implementation files.
- Do not use the worker's session or worktree as the critic.
- Do not approve your own material plan exception.
- Do not bypass required checks, reviews, or deployment approvals.
- Do not close a sprint before release verification and outcome review.
- Do not launch local tmux/Claude/Codex sessions when the approved execution
  path is Agent Platform MCP unless a recorded fallback gate authorizes it.
- Do not copy raw session logs, secrets, or private terminal payloads into
  durable artifacts.

## Handoffs

- Ready worker -> `lane-delivery`
- Worker closeout -> fresh `independent-critic`
- Critic changes requested -> original or newly leased `lane-delivery` session per policy
- All required lanes approved -> `release-verification` review-inbox packet mode
- CI/CD or deployment readiness gap -> `release-verification`,
  `platform-readiness`, or a gate owner according to the runbook
- Material replan -> `sprint-planning`
