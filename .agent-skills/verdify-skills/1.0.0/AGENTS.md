# Agent instructions

Use the Verdify lifecycle skills for transcript intake, North Star planning,
project definition, architecture, repo hygiene, platform readiness, sprint
planning, controller coordination, lane delivery, review, integration, and
deployment verification.

Start or resume through `$project-router` unless the user explicitly names another lifecycle skill and its prerequisites are present.

Mandatory repository rules:

- GitHub Issues are the backlog source of truth.
- GitHub is the delivery control plane; do not replace issues or pull requests with private chat state.
- One issue, lane, branch, worktree, worker session, and pull request is the default unit of implementation.
- Acquire a lane lease before coding and never share an active worktree between coding sessions.
- Follow `COMMON_OPERATING_CONTRACT.md` and `config/authority-matrix.yaml`.
- Use durable `.agent-workflow` artifacts for approved definitions, contracts, status, and evidence.
- Route new transcript or walk evidence through `$transcript-replan` before
  rewriting protected planning artifacts.
- Use `$northstar-research-ingest` to copy research into North Star collateral
  and register it in the queryable evidence registry.
- Use `$northstar-planning` to synthesize registered evidence, ideation,
  requirements, PRDs, user stories, milestones, waves, product surfaces,
  architecture stories, architecture requirements, high-level designs,
  infrastructure, conflicts, issues, planning questions, research proposals,
  review feedback, and final lock approval into `NORTHSTAR_PRODUCT.md` and
  `NORTHSTAR_ARCHITECTURE.md` before project definition or architecture consumes
  that material. Ordinary North Star questions restart the planning loop; final
  approval is required only to lock the North Star for the next milestone.
- Use `$northstar-interview` when review-ready North Star drafts or new evidence
  need prioritized human questions, proposed defaults, tradeoffs, and answer
  capture before final lock approval.
- Use `$northstar-question-resolution` when a repo has many human-gated or open
  planning questions that should be inventoried, clustered, researched with
  registered evidence, answered under delegated authority, and reduced to a
  short human escalation pack.
- Use `$state-of-union` for comprehensive project triage or replanning across
  GitHub backlog, planning artifacts, sprint artifacts, deployment/log health,
  discovered health issues, execution sequence, and next sprint candidates.
- Run `$repo-hygiene` before feature execution when a repo has not passed Wave 0
  compliance.
- Keep Gravity implementation blocked until `$platform-readiness` and
  `$gravity-readiness` are approved.
- A fresh critic must review lane output before integration.
- Runtime deployment must be verified separately from merge success.

Run `bin/verdify route --write` when lifecycle position is unclear and `ruby scripts/validate-repo.rb` before changing this skills repository.
