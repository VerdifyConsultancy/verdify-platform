# North Star Artifact Loop

Use this reference when creating or updating
`.agent-workflow/northstar/NORTHSTAR_PRODUCT.md`,
`.agent-workflow/northstar/NORTHSTAR_ARCHITECTURE.md`, and
`.agent-workflow/northstar/northstar-artifacts.yaml`. Use the learning-capture
rules when writing `.agent-workflow/northstar/learning-capture/*.yaml`.

## Loop states

1. `draft`: evidence has been synthesized, but the loop has not yet reached a
   coherent product/architecture draft.
2. `iterating`: the loop is actively distilling evidence, answering questions,
   proposing or ingesting research, updating design, and reconciling feedback.
3. `questions_open`: retained for compatibility. It means `NSQ-*` planning
   questions exist, not that human review is required.
4. `review_requested`: both markdown artifacts are coherent enough for Jason,
   James, or the configured reviewer to approve or request changes.
5. `approved`: required reviewers have locked the North Star for the next
   milestone. Downstream skills may treat the artifacts as core planning
   authority.
6. `blocked`: the loop cannot continue without unsafe access, protected
   production mutation, raw secrets, unavailable source material, or final human
   approval.

## Evidence pass

1. Read `.agent-workflow/northstar/evidence-registry.yaml`.
2. Read each useful collateral item referenced by the registry.
3. Read routed transcript records, current project definition, ADRs, architecture
   records, strategy records, issues, and gates when available.
4. Treat evidence status literally. Do not upgrade `reported` to `verified`
   without independent evidence.
5. Create or update `evidence_references` with each evidence ID, reference URI,
   title, evidence status, and whether it supports product, architecture, both,
   a question, or deferred work.

## Product artifact rules

`NORTHSTAR_PRODUCT.md` owns user-facing and planning intent:

- product purpose, current state, target state, and non-goals;
- personas and human governance roles;
- PRD summary;
- user stories;
- product requirements and acceptance signals;
- milestones;
- waves;
- surfaces and shapes, including UI, CLI, API, agent, review, and operations
  surfaces;
- product review script;
- product open questions;
- traceability from evidence to product IDs and architecture IDs.

Use stable IDs:

- `PRD-*` for PRD records when needed.
- `USR-*` for personas.
- `PST-*` for product stories.
- `PRQ-*` for product requirements.
- `MS-*` for milestones.
- `WAVE-*` for waves.
- `SURF-*` for surfaces and shapes.

## Architecture artifact rules

`NORTHSTAR_ARCHITECTURE.md` owns system, infrastructure, and delivery intent:

- architecture intent and non-goals;
- architecture stories;
- architecture requirements;
- high-level design;
- infrastructure and environments;
- interfaces and integration contracts;
- security, RBAC, and secrets;
- observability and diagnostics;
- delivery, release, and rollback;
- ADR and decision index;
- architecture open questions;
- traceability from architecture IDs back to product IDs and evidence.

Use stable IDs:

- `AST-*` for architecture stories.
- `ARQ-*` for architecture requirements.
- `COMP-*` for components.
- `ENV-*` for environments.
- `IFACE-*` for interfaces.
- `SEC-*` for security/RBAC/secrets requirements.
- `OBS-*` for observability requirements.
- `REL-*` for release requirements.
- `ADR-*` for architecture decisions.

## Cross-linking rules

- Every product requirement must cite evidence.
- Every architecture requirement must cite evidence and at least one product
  requirement, story, wave, surface, operator need, security need, or cost need.
- Every wave must cite product requirements and any architecture prerequisites.
- Every surface must cite at least one story or requirement.
- Every open question must cite evidence or the contradiction that created it.
- Use `northstar://evidence/<id>` for evidence registry references.
- Use markdown anchors for section links, for example
  `NORTHSTAR_PRODUCT.md#product-004-user-stories`.

## Human question loop

When evidence is insufficient:

1. Add `NSQ-*` to `northstar-artifacts.yaml`.
2. Add the same question to the relevant markdown open-question table.
3. Mark `blocking: false` for normal planning questions. Use `blocking: true`
   only for final lock approval, unsafe access, protected production mutation,
   raw secrets, or a protected-file edit that requires approval.
4. Keep status as `iterating` when the agent can continue by researching,
   comparing evidence, drafting a proposed answer, or narrowing options.
5. Ask the human only when the artifacts are ready for final review or when the
   loop cannot safely continue without human judgment.
6. After evidence, answers, or feedback arrive, update the artifacts, mark
   questions answered or resolved when possible, increment `iteration`, and
   rerun the evidence and cross-link pass.

## Self-improving planning loop

Each iteration should:

1. Distill new evidence into claims, risks, requirements, and architecture
   implications.
2. Resolve any `NSQ-*` that the registered evidence can answer.
3. Convert unanswered questions into a research queue or design options instead
   of stopping by default.
4. Capture validated lessons as proposal-only improvements when evidence,
   review feedback, sessions, failed loops, or tool friction reveal reusable
   changes. Each proposal should record the source evidence, observed issue or
   opportunity, verification performed, proposed destination, risk class,
   approval requirement, and routing decision.
5. Route learning-capture proposals to one of these homes: content idea,
   context file, slash command, skill update, hook, tool/CLI fix, config
   change, backlog issue, artifact schema, product shape, architecture, or
   no-op.
6. For recurring loop proposals, require loop-readiness evidence before
   scheduling: recurrence, verifier, durable state, stop condition, budget,
   objective done criteria, permissions, one reliable manual run, and handoff
   summary.
7. Update product and architecture drafts, cross-links, traceability, and the
   handoff recommendation.
8. Keep the next mode as `artifact-loop`, `research-loop`, or
   `learning-capture` until the artifacts
   are ready for review.
9. Preserve CI/CD based wave deployment, preview/review environments, promotion,
   rollback, and CI evidence requirements whenever delivery architecture is in
   scope.

## Learning-capture proposal packet rules

Use `../../schemas/northstar-learning-proposals.schema.yaml` for typed proposal
packets under `.agent-workflow/northstar/learning-capture/`. Read
`learning-capture.md` when source eligibility, redaction, verifier, stop,
budget, permissions, or scheduling readiness is in scope.

Each packet must preserve:

- source evidence IDs and any local artifact paths used;
- redaction policy for secrets, personal data, raw session logs, and source
  retention;
- one or more `NLP-*` proposals with observed issue or opportunity,
  verification, destination, proposed change, expected benefit, risk class,
  approval requirement, routing decision, and affected artifacts;
- loop-readiness answers for recurrence, verifier, durable state, stop
  condition, budget, objective done criteria, permissions, manual run evidence,
  and scheduling readiness;
- review state and handoff.

Use `routing_decision: apply_now` only for low-risk, explicitly requested,
repo-local artifact-shape changes that do not approve the North Star, mutate
production, expose secrets, schedule recurring loops, or bypass protected
approval. Use `stage` or `request_review` for higher-risk or recurring changes.

## Review feedback loop

When human review returns feedback instead of approval:

1. Set `review.status: changes_requested`.
2. Preserve the feedback as evidence or an `NSQ-*` item.
3. Set the loop status back to `iterating`.
4. Route to `artifact-loop`, not downstream implementation.
5. Iterate until the artifacts are ready for another review request.

## Review and signoff

Request review only when:

- both markdown artifacts exist;
- every material requirement has evidence;
- every architecture section links to product purpose;
- remaining questions are either answered, evidence-backed proposals, or
  explicitly deferred with owner and follow-up path;
- downstream handoff is named.

When review is requested, set:

- `status: review_requested`;
- product and architecture statuses to `review_requested`;
- `review.status: requested`;
- `review.requested_at` to the current UTC time;
- reviewers to the configured human approvers when known.

Set `status: approved` only after required human signoff is recorded in
`review.approvals`. This is the only planning gate for advancing to the next
milestone after the North Star is locked. Until then, downstream skills may read
the artifacts but must treat them as draft planning input, not protected
authority.
