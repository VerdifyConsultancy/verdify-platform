---
name: northstar-planning
description: >-
  Runs the self-improving North Star planning loop: distill registered context
  and research, answer questions from evidence when possible, propose follow-up
  research, capture validated lessons as proposed skill/artifact improvements,
  update product/architecture drafts, incorporate review feedback, and request
  final human approval only when ready to lock
  `NORTHSTAR_PRODUCT.md`, `NORTHSTAR_ARCHITECTURE.md`, and the structured loop
  record for the next milestone. Use after transcript-replan or research
  intake, when the user says to ingest context, kick off planning, continue the
  North Star loop, handle review feedback, or before project-definition,
  architecture-contracts, sprint-planning, CI/CD wave planning, or any
  protected DESIGN_COMMITTED change.
metadata:
  author: Verdify
  version: "1.0.0"
  lifecycle-order: "2b"
---

# North Star Planning

Convert messy planning input into explicit product and architecture North Star
artifacts. Preserve evidence, keep product and architecture separate but linked,
answer what the repo and research can answer, queue research where evidence is
thin, capture evidence-backed learning proposals without silently mutating
skills, and iterate until the artifacts are ready for final human lock approval.

Requires access to source evidence, current `.agent-workflow` artifacts,
repository docs, and GitHub state or snapshots when issue recommendations are
needed.

## Canonical artifacts

- `.agent-workflow/northstar/NORTHSTAR_PRODUCT.md` - product planning authority
- `.agent-workflow/northstar/NORTHSTAR_ARCHITECTURE.md` - architecture planning authority
- `.agent-workflow/northstar/northstar-artifacts.yaml` - loop state, evidence,
  open questions, cross-links, review, and signoff
- `.agent-workflow/northstar/northstar-plan.yaml` - structured synthesis/index
- `.agent-workflow/northstar/northstar-plan.md` - human-readable synthesis
- `.agent-workflow/northstar/evidence-registry.yaml` - registered research
  references consumed during synthesis
- `.agent-workflow/northstar/learning-capture/*.yaml` - proposal-only learning
  and alignment packets for evidence-backed skill, artifact, tool, and product
  shape improvements
- Proposed GitHub Issues, gates, or artifact patches when allowed by policy

Validate YAML against `../../schemas/northstar-plan.schema.yaml` and
`../../schemas/northstar-artifacts.schema.yaml`. Validate learning proposal
packets against `../../schemas/northstar-learning-proposals.schema.yaml`.

## Procedure

1. Read `../../COMMON_OPERATING_CONTRACT.md`.
2. Read routed transcript records, `.agent-workflow/northstar/evidence-registry.yaml`,
   registered collateral, notes, existing project definition, architecture, ADRs,
   state-of-union, issues, and PRs when available.
3. Inventory sources as evidence, not conclusions. Mark each claim `verified`,
   `observed`, `reported`, `inferred`, or `unknown`.
4. Create or update `NORTHSTAR_PRODUCT.md` from
   `assets/NORTHSTAR_PRODUCT.template.md`. Include PRD summary, users,
   stories, requirements, milestones, waves, surfaces/shapes, review script,
   open questions, and traceability.
5. Create or update `NORTHSTAR_ARCHITECTURE.md` from
   `assets/NORTHSTAR_ARCHITECTURE.template.md`. Include architecture stories,
   requirements, high-level design, infrastructure, interfaces, security/RBAC,
   observability, delivery/release/rollback, ADR index, open questions, and
   traceability.
6. Maintain `.agent-workflow/northstar/northstar-artifacts.yaml` from
   `assets/northstar-artifacts.template.yaml`. Record loop status, iteration,
   evidence references, product/architecture section IDs, cross-links, open
   questions, review state, approvals, and handoff.
7. Cross-link product intent to architecture inputs. Every architecture input
   must explain the user, operator, security, delivery, or cost purpose it
   serves.
8. Turn unresolved material gaps into `NSQ-*` planning questions. Do not stop
   the loop merely because questions exist. Answer questions from registered
   evidence where possible, propose follow-up research where evidence is thin,
   and keep iterating the drafts.
9. Run a learning-capture pass when new evidence, review feedback, session
   history, tool friction, or repeated corrections reveal reusable lessons.
   Stage proposals in `.agent-workflow/northstar/learning-capture/` unless an
   explicit current request authorizes applying a low-risk repo-local artifact
   improvement. Each proposal should cite source evidence, describe the observed
   issue or opportunity, summarize verification, name a destination such as
   content, context file, slash command, skill, hook, tool/CLI fix, config,
   backlog, artifact schema, product shape, architecture, or no-op, and state
   risk class plus approval requirement.
10. For recurring loop proposals, check recurrence, verifier, durable state,
    stop condition, budget, objective done criteria, permissions, one reliable
    manual run, and handoff summary before recommending scheduling.
11. Mark protected changes as proposed. Do not edit protected North Star content
   or mark `DESIGN_COMMITTED` without the configured approval rule.
12. Request human review only when both markdown artifacts are coherent,
    cross-linked, and ready for Jason/James feedback. If feedback requests
    changes, route back to `artifact-loop` and iterate again.
13. Hand off to `project-definition`, `architecture-contracts`,
    `state-of-union`, `repo-hygiene`, `platform-readiness`, or
    `gravity-readiness` with one explicit next action only after final North
    Star lock approval for the next milestone.

## Loop modes

- `intake`: gather and classify sources.
- `synthesis`: create product, requirement, story, milestone, architecture
  input, and risk records.
- `artifact-loop`: update product and architecture North Star docs, answer or
  queue questions, propose research, cross-link evidence, and maintain review
  state.
- `research-loop`: identify missing evidence, ingest or request research, and
  rerun synthesis.
- `adversarial-review`: attach findings and required dispositions.
- `decision-pack`: produce proposed artifact changes, issues, and design
  options for the next loop.
- `learning-capture`: classify validated lessons from evidence, sessions,
  review feedback, failed loops, and tool friction into proposal-only changes
  for content, context files, commands, skills, hooks, tools/CLI, config,
  backlog, or no-op.
- `human-review`: present ready artifacts, remaining judgment calls, and review
  script.
- `review-feedback`: incorporate human feedback and return to `artifact-loop`.
- `signoff`: record configured final approval and lock the North Star for the
  next milestone.

## Stop conditions

Do not open a human gate for ordinary planning questions. Keep looping through
evidence distillation, research proposals, question answering, design drafts,
and review feedback until the artifacts are ready for final review.

Open a gate only when final North Star lock approval is required to advance to
the next milestone, or when continuing would require unsafe access, protected
production mutation, raw secrets, destructive actions, or a protected-file edit
without the configured approval rule.

CI/CD based wave deployment is core planning scope when the broader delivery
architecture is discussed. The North Star must preserve wave deployment,
preview/review environments, promotion, rollback, and CI evidence requirements
as architecture-significant planning content.

Self-improvement is also core planning scope, but it is proposal-first. A
session-mining or learning-capture loop may scan, redact, classify, and stage
improvement proposals. It must not apply skill, hook, command, tool, config,
source, or protected artifact changes without the configured approval path.
The safe default artifact is a typed learning proposal packet under
`.agent-workflow/northstar/learning-capture/`; scheduled session mining remains
off until loop-readiness evidence proves verifier, durable state, stop
condition, budget, objective-done criteria, permissions, and a reliable manual
run.

## Load references only when needed

- Read `references/planning-contract.md` for field rules, ID conventions, and
  handoff rules.
- Read `references/artifact-loop.md` when creating, reviewing, or iterating
  `NORTHSTAR_PRODUCT.md`, `NORTHSTAR_ARCHITECTURE.md`, or
  `northstar-artifacts.yaml`.
- Read `references/learning-capture.md` when staging `NLP-*` proposals,
  assessing recurring scan readiness, or handling redacted session/review/tool
  lessons.
