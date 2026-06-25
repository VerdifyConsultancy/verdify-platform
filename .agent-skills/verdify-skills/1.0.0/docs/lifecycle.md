# Lifecycle and handoffs

Verdify exposes eighteen lifecycle skills but preserves the detailed lifecycle as explicit modes. A lifecycle skill may advance through its own modes without reactivation, provided each mode's artifacts and gates are satisfied. Standalone skills such as `issue-triage` sit outside this graph.

## 1. Project router

Inspects Git, GitHub state or snapshot, and `.agent-workflow` artifacts. It writes a route decision and names exactly one next skill/mode. It does not manufacture missing project facts.

## 2. Transcript replan

Converts walk transcripts, meeting notes, and spoken planning extracts into
routed source evidence, proposed lifecycle changes, conflict flags, issue
recommendations, and gate recommendations. It does not directly edit protected
artifacts or start implementation.

## 3. North Star research ingest

Copies local research files into `.agent-workflow/northstar/collateral/`,
creates normalized evidence-item YAML, and maintains the queryable
`.agent-workflow/northstar/evidence-registry.yaml`. It does not synthesize
requirements or edit protected planning artifacts.

## 4. North Star planning

Synthesizes registered research evidence, ideation, routed transcript evidence,
requirements, user stories, PRDs, milestones, waves, product surfaces,
architecture stories, architecture requirements, high-level designs,
infrastructure, conflicts, proposed issues, planning questions, research
recommendations, feedback, and final lock approval into
`.agent-workflow/northstar/NORTHSTAR_PRODUCT.md` and
`.agent-workflow/northstar/NORTHSTAR_ARCHITECTURE.md` artifacts, with loop state
in `.agent-workflow/northstar/northstar-artifacts.yaml`. It does not mark
`DESIGN_COMMITTED`, edit protected artifacts, or start implementation. Ordinary
questions keep routing through `artifact-loop` or `research-loop`; human review
is requested only when the drafts are ready for feedback or final lock approval.

## 5. North Star interview

Reviews current North Star product and architecture drafts, registered evidence,
review packets, and open gates. It produces
`.agent-workflow/northstar/NORTHSTAR_INTERVIEW.md` with prioritized human
questions, proposed defaults, options, tradeoffs, affected IDs, evidence
references, and answer-capture rules. It does not approve the North Star or
rewrite protected artifacts from inferred answers.

## 6. North Star question resolution

Inventories large sets of human-gated or open planning questions across docs,
interview packets, issues, and `.agent-workflow` artifacts. It clusters
questions by shared decision, researches missing evidence, ingests source-backed
notes through `northstar-research-ingest`, records delegated default answers and
confidence, and reduces true human-only decisions into a short escalation pack.
It hands back to `northstar-planning` for artifact updates and keeps final lock
approval separate.

## 7. Project definition

Four ordered modes share one canonical `project-definition.yaml`:

1. **Discovery** inventories supplied sources, known decisions, assumptions, contradictions, and evidence-based questions.
2. **Requirements** defines functional and non-functional requirements, constraints, acceptance criteria, and traceability.
3. **Product** defines users, jobs, scope, non-goals, workflows, value, success metrics, and product risks.
4. **Design surface** defines UI, CLI, API, tool, agent, and human-approval surfaces, states, and error behavior.

Material ambiguity opens a decision gate rather than becoming an invented requirement.

## 8. Architecture and contracts

The architecture mode defines system boundaries, topology, data flow, storage, integrations, security, deployment, observability, and ADRs. The module-contract mode converts that architecture into black boxes with stable inputs, outputs, public interfaces, owned paths, invariants, dependencies, tests, and completion evidence.

## 9. State of union

Reviews approved project definition, lifecycle readiness, architecture, module
contracts, GitHub Issues, pull requests, gates, sprint history, planning
artifacts, and deployment/log health. It records source freshness, planning
inventory, delivery health, stale/missing/blocked work, proposed issue/Project/
milestone/gate/diagnostic actions, execution sequencing, and one next lifecycle
handoff. It does not create lane contracts or replace GitHub Issues with a
private task list.

## 10. Repo hygiene

Runs Wave 0 compliance before feature work. It assesses source-of-truth
discipline, docs, tests, CI, GitHub state, stale branches, secrets exposure,
environment declarations, observability expectations, and ownership boundaries.
It applies only safe cleanup and opens gates for ambiguous or protected changes.

## 11. Sprint planning transaction

Planning begins from GitHub Issues, not a private task list. It selects a
bounded outcome, records exclusions and risk, then atomically creates the lane
topology, lane contracts, review plan, and wave release plan when CI/CD,
preview, deployment, rollback, or review evidence is in scope. The sprint plan
records what is included, what is deferred, lane owners/reviewers, QA
milestones, human review milestones, and user stories for review. The default
mapping is one issue to one lane. Human or policy approval applies to the
complete transaction; worktrees are not created before approval.

## 12. Sprint orchestration

The orchestrator checks prerequisites, snapshots/reconciles GitHub, builds or
refreshes `.agent-workflow/sprints/<sprint-id>/execution/sprint-execution-runbook.yaml`,
dispatches dependency-ready lanes through the configured Agent Platform MCP/API
operation, monitors platform session and terminal events, coordinates CI/CD and
review deployment readiness, resolves gates, and routes results. Local
tmux/terminal views are operator interfaces for platform sessions, not the
authoritative execution record. It does not implement lane code or review its
own output.

## 13. Controller loop

Persists outer-loop lifecycle state, wave state, child sessions, events, gates,
handoffs, and the append-oriented session ledger independently of model
conversation history. It supervises loops through durable events and pauses at
human gates.

## 14. Platform readiness

Inventories Agent Platform, Kubernetes namespaces, RBAC, secrets, CI/CD, GitOps,
ingress, DNS, observability, browser terminals, review inbox, and Agent Platform
API or MCP contracts before autonomous platform or Gravity execution. Proposed
Agent Platform operations are recorded as validated control requests before
execution is trusted. Environment-sensitive readiness and release decisions use
validated GitOps reconciliation records for desired state, observed controller
state, namespace controls, health, drift, rollback, and cleanup evidence.

## 15. Gravity readiness

Inventory-only Gravity gate. It confirms product, architecture, source, tests,
issues, dependencies, Onyx status, environments, credentials, observability, and
pilot criteria. When Sunshine-to-Gravity reuse is in scope, it records a
validated Gravity core extraction plan with source objects, reuse matrix,
core/pack boundaries, migration risks, and local filesystem ingestion pilot
criteria. It does not implement Gravity features.

## 16. Lane delivery

A worker acquires one worktree lease, implements only the contract, runs validation, pushes a branch, opens or updates the linked PR, and performs closeout in the same bounded session. Discoveries become issues. Material contract problems stop the lane.

## 17. Independent criticism

A fresh critic uses a separate detached worktree or clean clone. It compares requirements, issue, module contract, lane contract, diff, tests, CI, and evidence. It approves, approves with risks, requests fixes, blocks, or escalates. It does not silently repair the worker branch.

## 18. Release verification

A fresh release-verification role first assembles a review inbox packet when
work claims review-ready status, and a diagnostic packet when strategy, review,
release, readiness, incident, or feedback decisions depend on runtime evidence.
The review packet binds PR/MR identity, exact head SHA, checks, preview or
review deployment, telemetry, security disposition, rollback, risks, questions,
recommendation, and feedback route. The diagnostic packet binds correlation
IDs, hypotheses, telemetry links, signal assessments, runtime checks,
deployment markers, findings, missing instrumentation, and routing. After the
review packet is complete and approving, integration combines approved lanes in
dependency order, runs whole-system validation, and uses required checks or a
merge queue. A separately authorized deployment role proves the expected
commit/image/configuration in the target environment. Outcome review records
human acceptance, remaining risk, follow-up issues, and lessons learned.

The cycle returns to `project-router`.

## Gates

A gate is durable, typed, and includes owner, evidence required, allowed decisions, and resume state. Chat approval alone is insufficient for production, security, destructive operations, or policy exceptions.
