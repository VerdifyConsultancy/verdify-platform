# Agent Platform Execution

Use this reference when `sprint-orchestrator` moves an approved sprint from
planning artifacts into Agent Platform lane sessions, monitors those sessions,
and coordinates CI/CD, review deployments, and handoffs.

The controller coordinates execution. It does not implement lane code, review
its own output, or treat a local terminal as the source of truth.

## Inputs

- Approved sprint plan, plan approval gate, lane map, lane contracts, module
  contracts, and wave release plan when deployment or review evidence is in
  scope.
- `.agent-workflow/sprints/<sprint-id>/execution/sprint-execution-runbook.yaml`
  validated against `../../schemas/sprint-execution-runbook.schema.yaml`.
- Platform readiness and Agent Platform control request artifacts when the MCP
  operation, terminal access, authorization, or policy decision is not already
  recorded.
- Controller state and session ledger from `controller-loop`.
- GitHub issue, branch, PR, check, deployment, and environment state.

## Platform-First Rules

1. Use the Agent Platform MCP/API identity recorded in the runbook for session
   creation, polling, terminal attachment, and controller responses.
2. Record policy-sensitive operations as Agent Platform control requests per
   `platform-readiness/references/agent-platform-control.md`.
3. Use platform-provided tmux or browser terminal refs for operator visibility.
   Local iTerm2 or tmux windows may attach to those refs but are never the
   authoritative session identity.
4. Do not launch local Claude, Codex, or tmux worker sessions as a substitute
   for Agent Platform dispatch unless a recorded fallback gate authorizes it.
5. Store refs, IDs, paths, concise status, and evidence links. Do not copy raw
   terminal logs, secrets, private prompts, or long session transcripts into
   durable artifacts.

## Build Or Refresh The Runbook

Create the runbook immediately before dispatch:

```bash
skills/sprint-orchestrator/scripts/build_execution_runbook.rb \
  --repo <repository> \
  --sprint <sprint-id> \
  --controller-session-id <controller-session-id>
```

Rebuild or supersede it when sprint plans, lane contracts, wave release plans,
platform readiness, or controller identity change. A stale runbook blocks
dispatch.

Build per-lane Agent Platform session-create control request stubs from the
runbook before invoking MCP operations:

```bash
skills/sprint-orchestrator/scripts/build_platform_control_requests.rb \
  --repo <repository> \
  --runbook <repository>/.agent-workflow/sprints/<sprint-id>/execution/sprint-execution-runbook.yaml
```

## Dispatch Procedure

For each dependency-ready lane:

1. Confirm the lane has exactly one issue/branch/worktree/PR path unless the
   approved contract documents coupling.
2. Confirm no active worker lease or platform session already owns the lane.
3. Compile the worker prompt from the approved contract:

   ```bash
   bin/verdify prompt compile \
     --repo <repository> \
     --contract <lane-contract> \
     --role worker
   ```

4. Acquire or confirm local lease/worktree identity only as the lane ownership
   record:

   ```bash
   bin/verdify lane create \
     --repo <repository> \
     --sprint <sprint-id> \
     --lane-id <lane-id> \
     --issue <issue-number> \
     --session-id <platform-session-id-or-pending-id> \
     --agent <agent-platform-worker>
   ```

5. Invoke the configured Agent Platform MCP/API operation or write the required
   control request before invocation. The session create payload must include
   repository, lane ID, issue, branch, lease/worktree ref, prompt ref, contract
   ref, baseline SHA, allowed paths, validation commands, closeout requirements,
   and protected gate rules.
6. Record the resulting platform session ID, terminal/tmux/browser attach refs,
   prompt manifest, branch, PR, issue, lease ID, and worker status in the
   execution runbook, sprint status, and session ledger.

Never dispatch two worker sessions into the same lane or worktree.

## Controller Poll Loop

Run the configured cadence, typically every five minutes:

1. Refresh GitHub issue, PR, check, deployment, and review state.
2. Refresh lease/worktree state and compare it with lane contracts.
3. Poll every active Agent Platform session for heartbeat, status, questions,
   blockers, closeout, and coordination requests.
4. Attach to terminal refs only to observe state, answer the assigned lane
   agent, or capture concise evidence references.
5. Answer questions that are inside the approved lane contract and delegated
   policy. Open gates for protected decisions.
6. Route closeout to `independent-critic`, critic approval to
   `release-verification` review-inbox mode, CI failure to the owning lane, and
   deployment readiness gaps to `release-verification` or `platform-readiness`.
7. Append controller and session-ledger events for each material transition.

## CI/CD And Review Deployment

- Observe required GitHub checks and workflow runs from GitHub or the CI
  provider. Re-run checks only when policy allows and the reason is recorded.
- Trigger review deployment only when the wave release plan authorizes the
  target environment and the required platform control request or gate is
  satisfied.
- For k3s/GitOps environments, record desired-state refs, namespace, observed
  controller health, deployed revision, rollout status, telemetry refs, and
  rollback path. Runtime verification remains owned by `release-verification`.
- A successful merge or green check does not prove deployment. Route deployment
  proof to the deployment-verification role.

## Delegation And Gates

The controller may answer worker questions and coordinate lanes inside approved
lane contracts. Stop and open a gate for:

- scope, requirement, architecture, public API, schema, data migration, security,
  compliance, or destructive changes;
- production writes, protected environment changes, broad RBAC, or secret
  handling;
- missing or ambiguous Agent Platform MCP operation identity;
- ambiguous lane, issue, branch, PR, worktree, lease, session, or terminal
  identity;
- stale runbook, stale lane contract, missing GitHub state, missing CI/CD
  evidence, or missing deployment evidence;
- terminal/session loss when the worker state cannot be reconstructed from
  durable platform events.

## Artifact Updates

Update these records as execution proceeds:

- execution runbook lane session fields and status;
- sprint status and gate artifacts;
- controller state and session ledger;
- Agent Platform control request result refs;
- GitHub PR/check/deployment refs;
- review inbox, diagnostic, release verification, and outcome artifacts through
  the owning downstream skills.
