# Coverage-amendment independent critic report

- Sprint: `software-recovery-2026-07-09`
- Pull request: `#441`
- Reviewed head: `9ee3b714d40639478b9136e7a5aaa7c825ab3617`
- Base: `bfac89a17a8e81c7ac1f11e22604ec7174834409`
- Review date: `2026-07-09`
- Initial verdict on `9ee3b71`: **REQUEST_FIXES**
- Initial skill outcome mapping: **CHANGES_REQUESTED**

The amendment closes the four substantive coverage holes it set out to close. It preserves Hermes/MCP as the sole accepted production planner, forbids planner_graph writer authority, makes center mist climate-only in both directions, removes both legacy 10:30 jobs from eligibility, requires restart/missed-window-safe weekly solar cadence and calibrated liters, prevents a dry-out disposition from masquerading as a control fix, and keeps issue `#438` as a hard stop before every production mutation.

The head should not merge unchanged because its release worker contract is internally ambiguous at the pre-production checkpoint, and several deterministic records do not agree with the authoritative contracts they summarize. These are planning/controller defects, not product-code defects. No production or device action is warranted from this PR.

## Findings

### HIGH — the release tooling checkpoint has no executable acceptance boundary

**Evidence.** The release contract deliberately authorizes an autonomous worker to build and validate tooling while forbidding that worker from making production changes (`lanes/release-control/lane.yaml:104-111`). However:

- `LANE-AC-03` through `LANE-AC-05` require live consumer deployment, stale-row mutation, OTA, and settled runtime evidence (`lane.yaml:132-146`).
- Required commands include firmware preflight and a post-deploy climate-authority probe (`lane.yaml:164-179`).
- The worker prompt nevertheless says to meet **every** acceptance criterion and run **every** command (`worker-prompt.md:13`).
- The tooling PR may merge only after independent critic acceptance (`lane.yaml:207-211`), while the only defined full definition of done requires credential rotation, live services, OTA, and 48-hour evidence (`lane.yaml:226-233`).
- The pre-release critic is named (`lane.yaml:221-225`), but no Phase-A criterion set, allowed checkpoint verdict/state, or command subset tells that critic how to accept the tooling head without falsely accepting the production criteria.

The security dependency has the same ambiguity at its boundary. Its required output correctly says the merged source/caller/runbook checkpoint is sufficient for tooling and rotation closes later (`lane.yaml:77-98`), but the generic requirement that every implementation dependency be independently accepted and merged (`lane.yaml:95,105`; `worker-prompt.md:7`) does not explicitly say that the independently reviewed, merged PR `#439` checkpoint satisfies this prerequisite while the security lane remains `BLOCKED` on AC4.

**Impact.** A literal worker or Prompt-11 critic must either violate the no-production rule, falsely pass unexecuted AC3-AC5, or refuse the tooling PR that is supposed to create the release machinery. That recreates the circular release condition DEC-013 intended to remove.

**Required fix.** Define a deterministic two-stage contract:

1. Phase A: named tooling/manifest acceptance criteria, validation commands, evidence, status, and critic outcome; explicitly record that merged PR `#439` satisfies the security tooling prerequisite while credential AC4 remains blocked.
2. Phase B: controller-only production criteria and commands, entered only after `#438` is explicitly authorized, rotated, and redacted new-valid/old-invalid verification passes.

The contract may remain one lane, but its prompt and merge rule must state exactly which Phase-A subset permits the tooling PR to merge without claiming the lane or release complete.

### MEDIUM — topology points to a nonexistent irrigation validation target

**Evidence.** `plan/lane-topology.yaml:345` specifies `make irrigation-software-audit`. No such Make target exists. The authoritative firmware contract uses `make irrigation-stack-software-check` (`lanes/firmware-control/lane.yaml:189-193`), and the Makefile defines that target at `Makefile:298`.

**Impact.** The topology and lane contract do not agree despite the PR's parity claim. A worker or generated runbook using the topology command would fail before exercising the intended irrigation audit.

**Required fix.** Replace the topology command with `make irrigation-stack-software-check`, then rerun YAML/schema/parity checks.

### MEDIUM — all four amended lanes omit the decision that amended them

**Evidence.** DEC-013 says it amends the planner-delivery, firmware-control, evidence-core, and release-control lane contracts (`decisions/decision-register.yaml:220-237`). None of those contracts includes `DEC-013` in `decision_ids`:

- evidence-core: `lane.yaml:35-37`
- firmware-control: `lane.yaml:39-45`
- planner-delivery: `lane.yaml:29-32`
- release-control: `lane.yaml:32-36`

**Impact.** The behavior is present, but reverse decision traceability is broken at the immutable lane boundary. A future worker or controller can validate the contract without discovering the accepted post-dispatch decision that explains the added authority and stop conditions.

**Required fix.** Add `DEC-013` to all four contracts, refresh all four SHA-256 values in the execution runbook, and rerun prompt/hash/parity validation.

### LOW — the sprint plan retains superseded issue titles

**Evidence.** The outcomes and scopes are corrected, but the embedded GitHub title fields still carry the superseded framing, including:

- `#299` as “Center-mister dwell and cycle governor” (`plan/sprint-plan.yaml:225-233`)
- `#386` as “Fix grow-light solar-window shoulder cycling” (`:234-242`)
- `#383` as “Consolidate wet-to-dry anti-ping-pong and night-dehum capability” (`:243-251`)
- `#390` and `#427` also retain pre-reconciliation titles (`:216-224` and their earlier included-work entry)

Live GitHub now names these preservation/evidence/truthful-runtime slices. The stale title strings contradict the corrected non-goals even though the surrounding scope is accurate.

**Impact.** Low runtime risk, but durable plan/GitHub reconciliation is not exact and future summaries can resurrect retired proposed behavior.

**Required fix.** Refresh the embedded titles from the live issues without changing issue allocation or scope.

### LOW — one durable session identity has conflicting roles

**Evidence.** `scope-coverage-audit-20260709` is a `validator` in `.agent-workflow/controller/controller-state.yaml:83-91` and a `critic` in `.agent-workflow/controller/session-ledger.yaml:380-400`.

**Impact.** Event ordering remains valid, but the same durable session cannot be reconstructed unambiguously from the two controller sources.

**Required fix.** Choose the actual role and make both records agree. Preserve the existing event IDs and sequence.

## Acceptance review

| Review surface | Verdict | Evidence |
| --- | --- | --- |
| Exact operator intent | **PASS after live issue correction** | Firmware contract and prompt require climate-only center mist, disabled center drip/dormant zones, wall-only fertilizer, no 10:30 eligibility, weekly solar exact-once behavior, positive calibrated flow, and fail-closed commissioning. During this review the controller updated live `#434`; the body was re-read at `2026-07-10T00:01:41Z` and now states the same rules explicitly. |
| Hermes/MCP versus planner_graph | **PASS** | ADR-0002/README retain Hermes/MCP production authority. Planner lane forbids trigger routing, plan materialization, device commands, and Hermes acceptance through planner_graph; retained health or desired-state removal is narrow. |
| Single-writer boundary | **PASS** | Planner and firmware contracts prohibit device-writer changes/second writers; release is controller-owned. |
| Dry-out honesty | **PASS** | Evidence-core must emit exactly one `effective`, `ineffective`, `blocked`, or `insufficient_evidence` disposition; firmware freeze consumes it, and any requested control delta requires scope change. Ineffective/insufficient evidence is not completion. |
| Credential/production authority | **PASS** | `#438`, Q-001, the gate artifact, release constraints, worker prompt, and definition of done all block production mutation until separately authorized rotation and redacted verification. No override path was introduced. |
| Issue allocation | **PASS** | 17 plan issues, 17 lane assignments, and 17 topology assignments; each set is unique and identical. |
| Lane/DAG topology | **PASS** | Eight lanes; 15 unique edges; contract hard dependencies exactly match topology edges; no cycle. |
| Schemas | **PASS** | Eight lane contracts, sprint plan, decision register, controller state, session ledger, execution runbook, and sprint status validate: 14 artifacts total. |
| Contract hashes and prompt budgets | **PASS** | All eight SHA-256 values match the runbook; prompt lengths are 2,372-3,706 characters, all below 4,000. |
| Ledger continuity | **PASS with record-role fix above** | 64 unique events, sequences 1-64 contiguous, every `previous_event_id` links to the immediately preceding event. |
| Patch/CI | **PASS** | `git diff --check` passed. PR head matched local `9ee3b71`; GitHub showed 13 successful, eight intentionally skipped, zero failed, and zero pending checks with merge state `CLEAN`. |

## Limitations

- This was a contract/controller review. Commit `9ee3b71` changes no product code, migration, manifest, live workload, database row, secret, or firmware image, so it cannot prove the eventual software fixes or runtime outcome.
- Production was not mutated or probed by this critic. No credential or secret value was read. The existing redacted runtime findings were checked as claims against durable records and current issue authority, not independently reproduced from raw production logs.
- GitHub issue `#434` changed during the review under the controller's authority. Its final live body was re-read and passes; that mutation is not part of commit `9ee3b71`.
- Local `.venv` is absent in this controller worktree. Schema validation used `/opt/homebrew/bin/python3` with installed `yaml` and `jsonschema`; workflow artifacts were also validated with the vendored Verdify CLI.
- Green CI does not exercise the semantic release-phase, stale-title, decision-link, or cross-record-role findings above.

## Deterministic rerun commands

```bash
git rev-parse HEAD
git diff --check bfac89a17a8e81c7ac1f11e22604ec7174834409..HEAD
gh pr view 441 --repo VerdifyConsultancy/verdify-platform \
  --json headRefOid,baseRefOid,state,mergeStateStatus,statusCheckRollup

for file in \
  .agent-workflow/controller/controller-state.yaml \
  .agent-workflow/controller/session-ledger.yaml \
  .agent-workflow/sprints/software-recovery-2026-07-09/execution/sprint-execution-runbook.yaml \
  .agent-workflow/sprints/software-recovery-2026-07-09/status.yaml; do
  .agent-skills/verdify-skills/1.0.0/bin/verdify artifact validate --file "$file"
done

grep -nE 'irrigation-(software-audit|stack-software-check)' \
  Makefile \
  .verdify/sprints/software-recovery-2026-07-09/plan/lane-topology.yaml \
  .verdify/sprints/software-recovery-2026-07-09/lanes/firmware-control/lane.yaml

for issue in 299 377 383 386 390 410 427 434; do
  gh issue view "$issue" --repo VerdifyConsultancy/verdify-platform \
    --json number,title,state,body,updatedAt,url
done
```

Rerun the same schema, issue-allocation, edge-parity/acyclicity, hash, prompt-budget, and ledger-link scripts recorded in the PR validation after applying the fixes. Any change to product behavior, production authority, issue allocation, dependency edges, or release gates requires a fresh semantic review rather than a mechanical recheck.

## Re-review boundary

A focused follow-up may be approved mechanically if it is limited to:

1. explicit Phase-A/Phase-B release acceptance and critic wording without weakening `#438` or any production gate;
2. the corrected irrigation target;
3. DEC-013 reverse links plus refreshed hashes;
4. live issue-title refresh; and
5. controller/ledger role reconciliation.

Any broader scope or authority change requires a fresh independent critic.

## Focused re-review — 2026-07-10T00:24:52Z

- Re-reviewed head: `d617ae6593f8ea9474b952d6075923af0b226dd9`
- Pull request: `#441`
- Re-review scope: the five mechanical corrections authorized above
- Final verdict: **APPROVE**
- Skill outcome mapping: **PASS**

This focused follow-up resolves every requested finding without changing issue allocation, product behavior, planner or device authority, production state, or protected-gate policy. The initial `REQUEST_FIXES` verdict remains the historical finding record for `9ee3b71`; this `APPROVE` verdict supersedes it for `d617ae6`.

### Finding closure

1. **Release phases — CLOSED.** `release-control/lane.yaml` now defines `PHASE-A-TOOLING-CHECKPOINT` and `PHASE-B-CONTROLLER-PRODUCTION` with explicit owners, entry conditions, acceptance subsets, command IDs, evidence, and exit states. Phase A can reach `READY_FOR_CRITIC` on only the source/fixture portions of AC01/02 and returns the overall lane to `IMPLEMENTING` after merge. Phase B alone owns all live clauses, AC03-05, production commands, and final `COMPLETE`. The lane, summary, topology, and worker prompt all require the independently approved merged PR `#439` checkpoint for tooling while security-hygiene remains `BLOCKED`. Issue `#438` must be separately authorized, rotated, and verified with redacted new-valid/old-invalid proof before the first production mutation. Neither worker nor Phase-A critic can claim live acceptance or lane completion.
2. **Irrigation command — CLOSED.** The topology and authoritative firmware contract both use `make irrigation-stack-software-check`; `Makefile:298` defines that exact target. No `make irrigation-software-audit` reference remains in the reviewed command surfaces.
3. **DEC-013 and hashes — CLOSED.** Evidence-core, planner-delivery, firmware-control, and release-control all include `DEC-013`. All eight lane SHA-256 values exactly match the execution runbook after the four amended contracts were re-hashed.
4. **GitHub titles — CLOSED.** All 17 parsed `sprint-plan.yaml` issue numbers and titles exactly equal the 17 live open issues carrying `sprint:software-recovery-2026-07-09`; there are zero missing, extra, duplicate, or title-mismatched entries.
5. **Session role — CLOSED.** `scope-coverage-audit-20260709` is `critic` in both controller state and session ledger.
6. **Durable validation — CLOSED.** Fourteen schema-backed artifacts validate. The plan, lane contracts, and topology contain the same 17 unique issues across eight lanes. The topology has 15 unique edges, exactly equals contract hard dependencies, and is acyclic. All eight prompts remain below 4,000 characters. The ledger contains 65 unique contiguous events with correct predecessor links. Both the focused diff and full PR diff pass `git diff --check`.

### YAML-reference proof

The re-review parsed the YAML rather than relying on source grep. Literal values survive YAML comment semantics:

- release dependency, expected input, Phase-A entry, and coordination strings retain `#439`;
- the Phase-B entry condition retains `#438`;
- controller summaries and next actions retain `#439` and `#441`;
- session ledger PR references retain `#439` and `#441`, and external identifiers retain repository-qualified `#438/#439/#441` values;
- sprint state/status next actions retain `#441`.

The relevant scalars are quoted or use a no-whitespace repository-qualified `#`, so none is truncated into a YAML comment.

### Final PR evidence

- Local HEAD and PR head both resolve to `d617ae6593f8ea9474b952d6075923af0b226dd9`.
- PR base remains `bfac89a17a8e81c7ac1f11e22604ec7174834409`.
- GitHub reports `CLEAN`, 13 successful checks, eight intentional skips, zero failed, and zero pending.
- The PR body now accurately records 65 contiguous linked ledger events.
- The focused diff is limited to the critic report, release-phase clarification, irrigation command, decision links/hashes, issue-title refresh, and controller/ledger/status reconciliation. It introduces no product code, migration, deploy manifest, firmware, credential, database, or production mutation.

### Remaining gates and limitations

Approval means the amended planning/controller contract may merge. It does not mean any implementation lane, deployment, credential rotation, stale-state transaction, OTA, or runtime outcome is complete. Security-hygiene remains `BLOCKED`; `#438` still blocks every production mutation until Jason separately authorizes the scoped rotation and redacted verification passes. All implementation, critic, CI, migration, firmware, cycling, alert, weekly, bake, rollback, immediate, and settled-runtime gates remain in force.

No GitHub, production, credential, database, ArgoCD, or device state was mutated by this critic. Only this report was updated during the focused re-review.
