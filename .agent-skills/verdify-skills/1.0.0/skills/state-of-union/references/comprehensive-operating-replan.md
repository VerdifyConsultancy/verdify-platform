# Comprehensive Operating Replan

Use this reference when the user asks for full project triage, backlog
replanning, live deployment/log review, health issue tracking, lane carving, or
a proposed sprint with ownership and review milestones.

## Evidence order

1. Reconstruct authority before recommendations: `AGENTS.md`, common operating
   contract, authority matrix, router decision, North Star, project definition,
   architecture, module contracts, ADRs, state-of-union, sprint artifacts,
   review packets, release/outcome records, Git state, and GitHub state.
2. Prefer live GitHub for issues, PRs, checks, deployments, environments,
   Projects, milestones, dependencies, sub-issues, labels, and assignees. Use a
   snapshot only as a cache and record freshness.
3. When deployment or log health is requested, gather runtime identity and
   behavior evidence separately: expected revision, observed revision, desired
   GitOps state, deployment status, probes/events, logs, metrics, traces,
   alerts, runtime checks, and limitations.
4. Treat missing or stale evidence as a finding. Do not replace it with a
   confident summary.

## Strategy record requirements

Populate these `state-of-union.yaml` sections:

- `source_freshness`: every material source checked, including unavailable
  surfaces.
- `planning_inventory`: North Star, project, architecture, sprint, lane, gate,
  review, diagnostic, release, outcome, and relevant docs.
- `delivery_health`: current runtime health verdict, diagnostic refs,
  deployment refs, log/telemetry refs, and discovered health issues.
- `issue_inventory`: all relevant GitHub issues and any required missing issue
  placeholders.
- `gaps`: blockers that cannot be safely converted into normal issue updates.
- `actions`: proposed or applied GitHub issue, Project, milestone, dependency,
  diagnostic, review packet, gate, route, or no-op actions.
- `recommended_sequence` and `next_sprint_candidates`: only after higher-order
  foundations, gates, and health blockers are clear.

## Health issue handling

Discovered runtime, log, deployment, or telemetry problems are backlog work.
Create or propose GitHub Issues unless policy allows immediate issue creation.
Each health issue needs:

- observed symptom and severity;
- source refs such as deployment record, diagnostic packet, log URL, dashboard,
  command, or manual observation;
- expected owner or routing skill;
- acceptance intent or evidence required to close it;
- whether it blocks the next sprint, review readiness, deployment, or outcome
  acceptance.

## Report shape

A comprehensive replan report should answer, from durable artifacts:

- what was checked, and what was stale/missing/unavailable;
- what is in the proposed next sprint and why;
- what is deferred and why;
- what issues need creation, update, dependency, milestone, or Project action;
- what deployment/log/telemetry health issues were found;
- what sequence should run before, during, and after the next sprint;
- what remains blocked or human-gated;
- the single next handoff.

Do not invent lanes in this skill. State-of-union proposes candidate issues and
sequence. `sprint-planning` creates lanes, owners, QA milestones, user stories
for review, wave release plan, and the plan approval gate.
