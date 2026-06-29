# Evidence: Walk Transcript - Agent Platform, Gravity, and Skills

Source ID: `SRC-NS-001`

Pinned at: 2026-06-23

Source date: Not provided

Evidence status: Reported transcript extraction supplied by Jason

Related repositories:

- `VerdifyConsultancy/verdify-skills`
- `jvallery/agents`, especially `control-plane/agent-fleet-control`
- `VerdifyConsultancy/gravity`

Lifecycle use:

- Project definition discovery input
- North Star planning evidence
- Architecture and state-of-union reconciliation input
- Not an approved architecture artifact
- Not a `DESIGN_COMMITTED` marker

## Immediate Gate

Do not begin a real Gravity implementation run until the Agent Platform, SDLC
skills, credentials, namespaces, CI/CD, observability, and human-review workflow
are implemented, tested, and signed off by Jason and James.

## What This Source Supports

The transcript converges on a human-governed, agent-operated delivery system:
spoken planning feeds a protected North Star, research and adversarial review
produce multi-lane implementation plans, and a persistent controller executes
work through gated waves. Gravity is positioned as the proving ground, but its
real build is explicitly blocked until the Agent Platform and Skills
infrastructure can bootstrap agents, isolate worktrees, deploy review
environments, expose telemetry, and present review-ready pull requests.

## Reported Decisions

- Gravity is gated by a formal readiness review across platform, skills,
  infrastructure, credentials, human review, and validation criteria.
- Each project needs a canonical North Star containing distinct but linked
  product and architecture specifications.
- After `DESIGN_COMMITTED`, protected North Star changes require a pull request
  and the configured Jason and James approval rule.
- North Star requirements must trace to architecture, lanes, waves, milestones,
  epics, GitHub Issues, tests, deployments, and ADRs.
- A persistent outer-loop controller should own project state, planning history,
  orchestration, cross-lane consistency, and wave progression.
- Human review should not be requested until CI/CD is green, the change is
  deployed into a reviewable environment, documentation is present, and exact
  human test steps are supplied.
- Repo hygiene is Wave 0: inherited or newly connected repositories must reach
  a defined compliance state before feature execution.
- Agents may write directly only to development. Staging and production are
  inspected read-only and changed through gated promotion pipelines.
- Observability is part of the product requirement, including metrics, logs,
  traces, endpoint health, storage performance, and deployment state.
- A designated network or infrastructure agent should be the only automated
  actor allowed to land protected production edge or ingress changes.
- Human feedback stops protected wave progression until feedback, fixes,
  replanning, and sign-off are resolved.
- Walk transcripts are a current top-level planning input that should become
  structured changes, project routing signals, and proposed plan updates.

## Strong Directions To Reconcile

- Treat the North Star as the root contract from which planning and execution
  artifacts descend.
- Build transcript-to-replan as a first-class skill or mode.
- Represent execution as reviewable waves with explicit states for planning,
  implementation, CI/CD, preview deployment, human review, fixes, replanning,
  and sign-off.
- Model repositories as applications with declared Kubernetes environments,
  quotas, endpoints, storage, secrets, and deployment configuration.
- Expose each application through a control plane view containing namespaces,
  environments, endpoints, deployments, agents, sessions, waves, logs, metrics,
  and review state.
- Provide authenticated browser terminal access for controller and worktree
  sessions, with RBAC, auditing, and session ownership.
- Maintain a session ledger with parent-child relationships, timings, logs,
  artifacts, outcomes, and wave linkage.
- Support executor adapters for Codex, Claude Code, and future implementations
  behind one lifecycle contract.
- Run a non-Gravity end-to-end pilot before allowing Gravity feature work.

## Proposed Lifecycle From Transcript

```text
INTAKE
  -> PROJECT_ROUTING
  -> RESEARCH_FANOUT
  -> NORTH_STAR_DRAFT
  -> ADVERSARIAL_REVIEW
  -> DESIGN_REVIEW
  -> DESIGN_COMMITTED
  -> REPO_HYGIENE
  -> WAVE_PLANNED
  -> EXECUTING
  -> CI_VALIDATING
  -> PREVIEW_DEPLOYED
  -> HUMAN_REVIEW
      -> FIX_REQUIRED -> FIX_WAVE -> CI_VALIDATING
      -> REPLAN_REQUIRED -> PLAN_UPDATE -> HUMAN_REVIEW
      -> APPROVED -> WAVE_SIGNED_OFF
  -> NEXT_WAVE
  -> NORTH_STAR_PROVEN
  -> IDLE_UNTIL_NEW_REQUIREMENTS
```

## Proposed Source-Of-Truth Artifacts

The transcript proposes these artifacts as a normalized contract, not yet as an
approved filename set:

- `NORTH_STAR.md`
- `AGENTS.md`
- `docs/adr/`
- `PLAN.md`
- `LANES.yaml`
- `WAVES.yaml`
- `TRACEABILITY.yaml`
- `REPO_PROFILE.yaml`
- `ENVIRONMENTS.yaml`
- `OBSERVABILITY.md`
- `REVIEW_PLAN.md`
- Session ledger

## Requirement Families To Preserve

The transcript supplied detailed lane requirements. Preserve these families for
future project definition and issue creation:

- `NS-*`: North Star and governance
- `RA-*`: Research and adversarial review
- `CTRL-*`: Controller and outer loop
- `TR-*`: Transcript intake and replanning
- `SK-*`: Repo hygiene and skills
- `WT-*`: Worktree and lane execution
- `PLAT-*`: Kubernetes platform, resources, and security
- `REL-*`: CI/CD, release, and network infrastructure
- `OBS-*`: Observability and diagnostics
- `UI-*`: Control plane and human review UX
- `LED-*`: Session ledger and knowledge
- `GRAV-*`: Gravity product and pilot readiness

## Skills Repository Implications

- The current lifecycle skills already encode several aligned principles:
  typed authority, evidence before claims, one issue per lane by default,
  role-pure worker and critic sessions, production separation, and deployment
  verification before outcome acceptance.
- The skills repo likely needs an explicit North Star evidence intake path,
  transcript-to-replan contract, repo hygiene automation, controller state
  machine specification, observability contract, and review-ready deployment
  gate.
- Existing project-definition discovery supports transcripts as source records,
  but the repository does not yet have a canonical project definition artifact.
- The current branch identity rule must be reconciled before wave automation is
  encoded: the repo says one issue equals one lane, branch, worktree, session,
  pull request, and preview by default; the transcript explores one branch per
  wave.

## Cross-Repo Implications

- Agent Platform becomes the control plane for projects, environments,
  worktrees, sessions, terminals, deployments, observability, and human review.
- Gravity becomes a gated pilot and customer of the platform and skills system,
  not the first autonomous build target.
- The home-lab platform contract centers on namespace-scoped Kubernetes
  execution, local builds, registry and cache access, Argo CD, PVC inventories,
  ingress, DNS, and environment promotion.
- GitHub remains acceptable as a temporary source-control dependency while
  other build and deployment dependencies move local-first where practical.

## Decision Tickets To Open

1. Jason and James approval semantics for protected North Star changes.
2. Issue, session, worktree, branch, wave, pull request, and preview identity
   model.
3. Repository, application, environment, and namespace cardinality.
4. Controller ownership versus specialist infrastructure ownership.
5. RBAC and secret-access boundaries.
6. Wave taxonomy and mandatory human checkpoints.
7. Release health and rollback criteria.

## Open Questions

- Which three repositories are canonical for the first multi-repo planning wave?
  This checkout identifies the skills repository as
  `VerdifyConsultancy/verdify-skills`, while the transcript recorded low
  confidence around the spoken repository name.
- Does Gravity still sit on top of Onyx?
- Should product and architecture live in one protected `NORTH_STAR.md` or in
  separately protected product and architecture files?
- What exactly constitutes `DESIGN_COMMITTED`?
- Are human checkpoints optional for some transitions or mandatory before every
  protected wave progression?
- What is the canonical wave taxonomy?
- How should agents receive runtime secret access without exposing values in
  prompts, logs, transcripts, skills, or source control?
- Can agents inspect production data, and under what audit and masking rules?
- Should the session ledger begin as SQLite, JSONL, or a service-backed event
  store?
- What was the unfinished final transcript thought after "There should be some
  way for..."?

## Backlog Hypotheses

These ideas are explicitly exploratory or backlog until validated:

- One wave per day as an optimization target.
- Agent Deck terminal component reuse.
- Claude Code and Codex mutual execution.
- LangGraph or LangChain for the outer-loop state machine.
- Semantic history search across transcripts, ADRs, plans, and session logs.
- Velocity and execution portal.
- Always-on cloud planning agent.
- Local GitLab migration.

## Evidence Handling Instructions

- Treat this document as `reported` source evidence.
- Do not rewrite protected planning artifacts solely from this record.
- During project definition, convert supported claims into stable IDs with
  source links back to `SRC-NS-001`.
- During architecture work, disposition the branch model, namespace model,
  RBAC/secrets model, rollback signals, and controller ownership conflicts
  before design commitment.
- Do not queue Gravity implementation from this evidence. Queue only Gravity
  inventory, requirements reconciliation, readiness checklist, and pilot design.
