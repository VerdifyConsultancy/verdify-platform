# Verdify critic prompt

Generated: 2026-07-03T21:05:36Z
Sprint: s8-vanda-night-dehum
Lane: fw-410-vent-reheat-hold
Role: critic
Contract SHA-256: 03695cc17d083eed4b07233043d0e80e800bc72d3eabf940eeafbe9c591aab3f

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


## Authoritative lane contract

```yaml
---
schema_ref: lane-contract.schema.yaml
kind: LaneContract
schema_version: '1.0'
sprint_id: s8-vanda-night-dehum
lane_id: fw-410-vent-reheat-hold
title: '#410 vent+reheat held-temp dehum (flag-OFF) per the 2026-07-03 design review'
status: approved
issue_ids: [410]
coupling_justification: null
objective: >-
  Implement issue #410 exactly as amended by the 2026-07-03 design review
  (comment on #410): (1) held-temp gain candidate in estimate_moisture_exchange
  with the selection ladder vent_cooled -> vent_plus_heat_hold -> heat_assist;
  (2) can_hold actual-temp floor (temp_f >= band_heat_target_f + heat_hysteresis)
  replacing — not deleting — the vent_overcools veto for the hold candidate, plus
  GH_DEHUM_HOLD_REENTRY_F=1.0f entry hysteresis in the mode layer; (3)
  resolve_equipment DEHUM_VENT heat1 gate becomes (dehum_heat_assist_active &&
  (needs_heating_s1 || (hold_required && temp_f < temp_target))), with the 5-min
  corun dwell bypassed for hold-flavor entries; (4) new bool tunable
  dehum_vent_hold_enabled, default OFF, with cfg_dehum_vent_hold_enabled
  readback; estimator returns today's exact behavior when OFF.
desired_outcome: >-
  A merged, unreleased firmware change that is bit-identical to origin/main when
  the flag is OFF (replay 0 divergence at THRESHOLD_PCT=0), fully characterized
  when ON (replay divergence buckets + synthetic cold-night fixture), and ready
  for a Jason-gated flag-OFF OTA. heat2 never participates; the DEHUM heat<->air
  interlock exemption is unchanged; vpd_min_safe rescue arms hold-reheat
  immediately.
non_goals:
  - 'No OTA, no tunable push, no prod mutation of any kind.'
  - 'No closed_heat_dehum enable-flag work (#383 remainder, deferred post-OTA).'
  - 'No vpd_target/anchor value changes (db-411-night-anchors owns migration 188).'
  - 'No ingestor/MCP changes (data-327 owns the telemetry consumers).'
baseline_sha: 0efdb2f59a8600130cd9521696e395423c86e910
branch: fw-410-vent-reheat-hold
module_contracts:
  - docs/firmware-fsm-spec.md
  - docs/adr/0003-band-compliance-track-the-target.md
  - docs/adr/0004-floating-corridor-control.md
  - docs/firmware-control-contract.md
worktree_policy:
  one_coding_session_per_worktree: true
  lock_required: true
lease_policy:
  worker_ttl_hours: 24
  critic_ttl_hours: 8
runtime_namespace:
  strategy: derived_at_dispatch
ownership:
  domains: [firmware, firmware-tests, firmware-docs]
  owned_paths:
    - firmware/lib/greenhouse_logic.h
    - firmware/lib/greenhouse_types.h
    - firmware/greenhouse/globals.yaml
    - firmware/greenhouse/tunables.yaml
    - firmware/greenhouse/sensors.yaml
    - firmware/greenhouse/controls.yaml
    - firmware/test/
    - docs/firmware-fsm-spec.md
    - docs/adr/0003-band-compliance-track-the-target.md
    - docs/adr/0004-floating-corridor-control.md
    - docs/firmware-control-contract.md
  prohibited_paths:
    - db/migrations/
    - ingestor/
    - mcp/
    - docs/RELEASE-CHECKLIST.md
    - docs/handoff/
    - docs/runbooks/
  coordination_required_paths:
    - verdify_schemas/
    - docs/reviews/
  owned_interfaces:
    - 'MoistureExchangeEstimate struct (adds vent_held_vpd_gain_kpa, hold_required — field NAMES coordinated with data-327 before either PR merges)'
    - 'climate_moisture_exchange published JSON shape (additive only)'
    - 'Setpoints.dehum_vent_hold_enabled + cfg_dehum_vent_hold_enabled readback'
dependencies:
  hard: []
  soft:
    - lane_id: data-327-moisture-telemetry
      coordination: >-
        Agree the two new telemetry field names (vent_held_vpd_gain_kpa,
        hold_required) and the additive JSON shape before either PR merges, so
        migration 187 and the firmware emitter never disagree.
acceptance_criteria:
  - id: LANE-AC-01
    statement: >-
      Flag OFF is behavior-identical: make firmware-replay-worktree
      OLD=origin/main reports 0 divergent rows at THRESHOLD_PCT=0; make
      firmware-replay-band OLD=origin/main within threshold; full invariant
      suite green; make test-firmware green including new tests.
    sprint_acceptance_ids: [SPR-AC-01]
    evidence_required:
      - 'Rule-9 artifacts in the PR body: replay diff, invariant output, unit-test delta'
  - id: LANE-AC-02
    statement: >-
      Flag ON is characterized: a flag-ON replay run (documented mechanism; if
      none exists for forcing a non-default global in replay, BUILD it in
      firmware/test and document it) classifies every divergent row into
      (a) #385 heat_dehum redirects, (b) new night DEHUM_VENT+heat1 episodes,
      (c) anything else = investigate before merge.
    sprint_acceptance_ids: [SPR-AC-02]
    evidence_required:
      - 'Flag-ON replay report with bucket counts in the PR'
  - id: LANE-AC-03
    statement: >-
      Synthetic cold-night fixture (new CSV under firmware/test/data/ + native
      test): outdoor 20-40F trace proves vent_overcools routing to closed heat,
      can_hold floor exit + GH_DEHUM_HOLD_REENTRY_F re-entry spacing, no
      invariant-#14 breach, heat2 never without heat1, and the vpd_min_safe
      rescue arming hold-reheat immediately (no 5-min unheated vent).
    sprint_acceptance_ids: [SPR-AC-02]
    evidence_required:
      - 'Fixture file + test names in make test-firmware output'
  - id: LANE-AC-04
    statement: >-
      Freeze-rule-6 compliance: dehum_vent_hold_enabled defaults false,
      restore_value: no, entity in tunables.yaml, cfg_dehum_vent_hold_enabled
      readback in sensors.yaml; no-new-fire-and-forget CI green. No other new
      tunables (hold target = served temp_target; re-entry gap = named
      constexpr).
    sprint_acceptance_ids: [SPR-AC-01]
    evidence_required:
      - 'CI no-new-fire-and-forget green + globals/tunables/sensors diff'
  - id: LANE-AC-05
    statement: >-
      Behavior-coupled docs updated in the same PR: fsm-spec DEHUM relay map
      (heat1 co-run + hold candidate), fsm-spec:269/:282-284 band_track_fraction
      default corrected to 0.0, ADR-0003 s6.4 ladder addendum, ADR-0004:55
      selector note, firmware-control-contract dehum exit rule; heat1-electric
      comment fixes at greenhouse_logic.h:14,:150 and greenhouse_types.h:185.
    sprint_acceptance_ids: [SPR-AC-01, SPR-AC-04]
    evidence_required:
      - 'Doc diffs in the PR; git diff --check clean'
validation_commands:
  - id: V1
    command: make lint
    purpose: repo lint gate
    required: true
  - id: V2
    command: make test-firmware
    purpose: native C++ tests incl. new estimator/hold/fixture tests
    required: true
  - id: V3
    command: make firmware-invariants
    purpose: 16-invariant replay gate (esp. #14)
    required: true
  - id: V4
    command: 'make firmware-replay-worktree OLD=origin/main'
    purpose: flag-OFF zero-divergence proof (THRESHOLD_PCT=0)
    required: true
  - id: V5
    command: 'make firmware-replay-band OLD=origin/main'
    purpose: band-derived replay guard (no curve change expected)
    required: true
  - id: V6
    command: 'SECRETS_SRC=$HOME/.verdify/esphome-secrets.yaml make firmware-check'
    purpose: ESPHome compile proof
    required: true
required_evidence:
  - 'PR body rule-9 artifact block (replay diff + invariants + unit delta)'
  - 'Flag-ON replay bucket report'
  - 'Cold-night fixture output'
git_policy:
  pull_request_required: true
  github_checks_required: true
  self_merge_allowed: false
  clean_worktree_required: true
escalation_conditions:
  - 'Any invariant breach, or flag-OFF replay divergence != 0.'
  - 'The flag-ON replay mechanism cannot be built inside firmware/test ownership.'
  - 'Any needed edit to a prohibited path (esp. verdify_schemas/ or ingestor/).'
  - 'Any discovery that changes the estimator ladder semantics agreed in the #410 design-review comment.'
  - 'Anything that would require an OTA or live tunable push to verify.'
definition_of_done:
  - 'PR open with all validation commands green and rule-9 artifacts in the body.'
  - 'Independent critic approval on the head SHA.'
  - 'Merged to main with checks; NO OTA performed; #410 left open for the release wave.'
approval:
  status: approved
  approver: jason
  approved_at: '2026-07-03T20:14:19Z'
```
