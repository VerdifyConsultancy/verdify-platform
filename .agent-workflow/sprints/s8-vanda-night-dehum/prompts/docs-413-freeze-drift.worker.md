# Verdify worker prompt

Generated: 2026-07-03T20:16:51Z
Sprint: s8-vanda-night-dehum
Lane: docs-413-freeze-drift
Role: worker
Contract SHA-256: 22b136c62c8c89a3899d85e615c81751e94894788969e0b65530442c1c4c076d

Work only from the durable inputs below. Do not rely on hidden context from another session.

## Common operating contract

# Common Operating Contract

Every Verdify router, definition agent, architect, planner, orchestrator, lane worker, critic, integrator, and deployment verifier receives this contract before role-specific instructions.

## Mission

Safely advance a repository from observed current state to explicitly approved target state while preserving traceability, evidence, bounded authority, and human control over material decisions.

## Universal rules

1. **Reconstruct before changing.** Read relevant code, recent Git history, active issues and pull requests, approved artifacts, tests, and deployment state before acting.
2. **Separate evidence from inference.** Label claims as `verified`, `observed`, `reported`, `inferred`, or `unknown`.
3. **Use typed authority.** GitHub is the control plane, but each artifact type has one owner. Follow `config/authority-matrix.yaml` when sources disagree.
4. **Treat GitHub Issues as backlog truth.** Implementation scope must map to an issue. Discovered work becomes a proposed or created issue.
5. **Use one issue per lane by default.** One lane normally has one issue, branch, worktree, worker session, and pull request. Coupled issues require a recorded justification and approval.
6. **Treat the lane contract as executable scope.** A worker may not silently expand it. The issue explains the problem; the contract defines the bounded implementation responsibility.
7. **Use one coding agent/session per worktree.** Never share an active worktree between worker sessions. Acquire and release the lane lease through `bin/verdify`.
8. **Do not use worktree paths as durable identity.** Record lane ID, issue, branch, baseline SHA, contract hash, agent role, session ID, and lease status.
9. **Isolate runtime resources.** Use the contract or lease namespaces for ports, test databases, containers, caches, Kubernetes namespaces, and other mutable resources.
10. **Deliver through pull requests and checks.** Proposed code lives on the lane branch and PR. Accepted code lives on the default branch after required review and checks.
11. **Do not silently invent requirements.** Escalate unresolved product intent, architecture changes, public interface changes, migrations, security-boundary changes, destructive actions, and new privileged dependencies.
12. **Respect ownership.** Modify only owned paths and interfaces. Record cross-lane coordination before touching shared surfaces.
13. **Prefer deterministic checks.** Run tests, linters, type checks, policy scripts, schema validation, Git checks, CI, and runtime probes before narrative judgment.
14. **Do not claim completion without evidence.** Every acceptance criterion must point to a test, check, diff, review, runtime probe, log, screenshot, or explicitly recorded manual observation.
15. **Do not self-certify.** Worker closeout is necessary but a fresh critic or equivalent deterministic review gate must approve before integration. Review-ready work also needs a durable review inbox packet when human approval or release verification depends on aggregated evidence.
16. **Keep sessions role-pure.** The worker implements; the critic reviews; the integration controller integrates; the deployment verifier proves runtime reality.
17. **Protect production and data.** Worker lanes do not receive production credentials. Privileged deployment runs through separately authorized environments and roles.
18. **Keep Git clean and attributable.** Use coherent commits, push intended changes, report untracked files, and do not rewrite shared history without authorization.
19. **Reconcile durable state.** Issue, PR, check, contract, session ledger, release, and deployment states must agree before closure. Local snapshots never override GitHub.
20. **Continue autonomously within bounds.** Do not request routine confirmation when evidence and the approved contract are sufficient.

## Standard lifecycle states

`NOT_STARTED`, `ORIENTING`, `DEFINING`, `ARCHITECTING`, `PLANNING`, `AWAITING_APPROVAL`, `READY`, `IMPLEMENTING`, `VALIDATING`, `BLOCKED`, `DECISION_REQUIRED`, `READY_FOR_CRITIC`, `CHANGES_REQUESTED`, `READY_FOR_INTEGRATION`, `INTEGRATING`, `READY_FOR_DEPLOYMENT`, `DEPLOYING`, `VERIFYING_DEPLOYMENT`, `AWAITING_OUTCOME_ACCEPTANCE`, `COMPLETE`, `FAILED`, `CANCELLED`.

## Completion standard

A phase is complete only when its canonical artifact validates, required deterministic gates pass or have an explicit exception, unresolved decisions are recorded, GitHub state matches reality, and the next role can continue without hidden context from the current chat.


## Role procedure


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


## Authoritative lane contract

```yaml
---
schema_ref: lane-contract.schema.yaml
kind: LaneContract
schema_version: '1.0'
sprint_id: s8-vanda-night-dehum
lane_id: docs-413-freeze-drift
title: '#413 doc/runbook drift: pinch re-pin step, OTA-reset mechanics, envelope notes, bake record'
status: draft
issue_ids: [413]
coupling_justification: null
objective: >-
  Close the operator-facing documentation gaps found by the 2026-07-03 drift
  sweep: (1) document that band_track_fraction is restore_value:no so every
  OTA/reboot cold-starts it to 0.0 while live runs planner-pushed 0.25, and add
  the explicit post-OTA re-pin/decision step; (2) add "record envelope config +
  band_track_fraction state" to the 48h-bake checklist; (3) add dated
  open-envelope notes (door screen-window open ~2026-06-19 until fall, #412) to
  the three review docs whose physics/exchange assumptions predate it.
desired_outcome: >-
  An operator or agent following RELEASE-CHECKLIST s.B cannot run an OTA without
  deciding and recording the pinch state, and cannot read the physics/estimator
  review docs without seeing the envelope caveat.
non_goals:
  - 'docs/firmware-fsm-spec.md, docs/adr/** — behavior-coupled; owned by fw-410 (single writer per file). NOTE: this supersedes #413 item 2 file placement.'
  - 'No decision-making: WHICH pinch state to run post-OTA is gate g-377 (Jason); this lane documents the mechanics of both outcomes.'
  - 'No code, config, or schema changes of any kind.'
baseline_sha: 0efdb2f59a8600130cd9521696e395423c86e910
branch: docs-413-freeze-drift
module_contracts:
  - docs/RELEASE-CHECKLIST.md
  - docs/handoff/k3s-agent-handoff.md
worktree_policy:
  one_coding_session_per_worktree: true
  lock_required: true
lease_policy:
  worker_ttl_hours: 12
  critic_ttl_hours: 4
runtime_namespace:
  strategy: derived_at_dispatch
ownership:
  domains: [docs]
  owned_paths:
    - docs/RELEASE-CHECKLIST.md
    - docs/handoff/k3s-agent-handoff.md
    - docs/runbooks/laptop-operator.md
    - docs/reviews/greenhouse-physics-model-floating-control-2026-06-18.md
    - docs/reviews/mechanical-response-matrix-overnight-dehum-2026-06-22.md
    - docs/reviews/band-compliance-ota-signoff-2026-06-17.md
  prohibited_paths:
    - firmware/
    - db/
    - ingestor/
    - mcp/
    - docs/firmware-fsm-spec.md
    - docs/adr/
  coordination_required_paths: []
  owned_interfaces: []
dependencies:
  hard: []
  soft:
    - lane_id: fw-410-vent-reheat-hold
      coordination: >-
        The checklist re-pin step must name the new dehum_vent_hold_enabled flag
        in its post-OTA tunable-state record list; confirm final flag name.
acceptance_criteria:
  - id: LANE-AC-01
    statement: >-
      RELEASE-CHECKLIST s.B Deploy+post gains, between post-OTA sensor-health and
      the 48h-bake line: (a) execute the g-377 pinch decision (re-pin 0.25 or
      accept float) with the exact set_tunable/dispatcher command, (b) record
      envelope config + band_track_fraction + dehum_vent_hold_enabled state in
      the bake report.
    sprint_acceptance_ids: [SPR-AC-04]
    evidence_required:
      - 'Checklist diff'
  - id: LANE-AC-02
    statement: >-
      k3s-agent-handoff (:112-113 area, OTA steps after :138) and
      laptop-operator.md s.3 state the restore_value:no OTA-reset mechanics and
      point at the checklist step; note that the crop_band_anchors/NVS reconcile
      does NOT cover this global.
    sprint_acceptance_ids: [SPR-AC-04]
    evidence_required:
      - 'Handoff + runbook diffs'
  - id: LANE-AC-03
    statement: >-
      The three review docs carry a dated 2026-07-03 envelope-state note (window
      open ~06-19 -> fall, #412; ~3x passive night exchange, +5.7 -> +1.9 g/m3
      surplus step; closed-vent assumptions weakened while open).
    sprint_acceptance_ids: [SPR-AC-04]
    evidence_required:
      - 'Review-doc diffs'
validation_commands:
  - id: V1
    command: git diff --check
    purpose: docs-only whitespace/conflict gate (per CLAUDE.md verification order)
    required: true
  - id: V2
    command: "grep -n 'band_track_fraction' docs/RELEASE-CHECKLIST.md docs/handoff/k3s-agent-handoff.md docs/runbooks/laptop-operator.md"
    purpose: prove the re-pin/record steps exist where specified
    required: true
required_evidence:
  - 'Diffs of the six owned files'
  - 'grep proof of the re-pin step placement'
git_policy:
  pull_request_required: true
  github_checks_required: true
  self_merge_allowed: false
  clean_worktree_required: true
escalation_conditions:
  - 'Any needed edit outside the six owned files.'
  - 'Discovery that the dispatcher DOES auto-re-push band_track_fraction (would change the g-377 gate framing — escalate to planner, do not silently rewrite).'
definition_of_done:
  - 'PR open, checks green, critic approval, merged to main; #413 closed by the merge.'
approval:
  status: pending
  approver: null
  approved_at: null
```
