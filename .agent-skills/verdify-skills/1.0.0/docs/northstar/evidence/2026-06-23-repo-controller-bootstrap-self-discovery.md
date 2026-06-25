# Semantic Walk Intake: Repo Controller Bootstrap And Fleet Operations

Date: 2026-06-23
Source type: spoken walk transcript, normalized by Jason's clarification "Not literal".
Evidence status: reported

## Intent

Jason wants Verdify Skills and Agent Platform to standardize how repository
controller agents onboard themselves, discover repo and runtime context, produce
durable inventory and planning artifacts, and then operate in monitored loops
across a fleet of active repositories.

## Normalized Claims

- Every active repository across Jason, Emily, James, and shared organizations
  should eventually have an assigned controller agent backed by durable storage.
- Repo controller agents should be able to manage worktree agents through
  structured schemas, tmux sessions, a web UI, an API, and Agent Platform MCP or
  control operations.
- A bootstrap or self-discovery workflow should read repository contents,
  documentation, Git history, GitHub issues, deployed Kubernetes resources,
  logs, metrics, routes, secrets references, credential validity, storage
  mounts, CI/CD shape, and package/runtime requirements.
- The bootstrap output should include a standard project inventory, updated
  `AGENTS.md`, a gap assessment, recommended backlog issues, namespace and
  environment mapping, and proposed changes as a pull request.
- Repository namespace naming should become consistent. The preferred direction
  is repository-aligned naming, with explicit handling for organization or
  GitHub-owner prefixes and separate dev, preview, staging, and production
  environment namespaces where needed.
- Least privilege remains a key design principle, but some infrastructure
  domain agents, such as networking, storage, backup, and platform agents, need
  broader scoped access to their owned systems, secrets, logs, metrics, and
  change history.
- Repo-local agents should normally stay inside their assigned namespace and
  receive only the mounts, credentials, network access, routes, and deployment
  powers needed for their project.
- NFS mounts, durable controller state, Git worktrees, repo source, and useful
  workspace folders should be visible and understandable inside the agent pod
  and VS Code or equivalent remote workspace.
- Agents should recommend package and base-image improvements from repository
  discovery. The fleet should consolidate those recommendations into
  prebuilt/versioned images with registry cache, rollback, and regular rebuild
  policy.
- Controller loops should persist state, reset or resume model contexts, manage
  inner worktree loops, update dashboards, emit metrics, and alert or recover
  when loops fail.
- Grafana and Prometheus should expose pod health, controller availability,
  loop statistics, ledger events, costs, and per-agent or per-project health.
  Alertmanager events should route back to the responsible controller when
  possible, and controller/pod recovery should rehydrate outstanding work from
  durable state.
- Planning and documentation loops should include adversarial Codex and Claude
  review, lane-owner review, and stakeholder perspectives such as product,
  management, finance, infrastructure, SRE, and security before final plan
  consensus.
- Orbit or an equivalent higher-level planner should aggregate project state,
  controller health, PRs, issues, calendars, email, and agent status into a
  "good morning" daily operating brief and a cross-repo review loop.
- The skill set should be audited for internal consistency and evidence quality,
  including Brave/API-backed research where external evidence is needed, so the
  architecture and skill boundaries remain defendable.
- A separate follow-up exists for a VAST TCO calculator/object storage
  comparison deck or model. That work should be routed to its owning repository
  or workstream when identified and should not be folded into Verdify Skills
  North Star requirements except as routed personal context.

## Planning Implications

- Treat repo bootstrap/self-discovery as an explicit workflow or mode, likely
  spanning `repo-hygiene`, `platform-readiness`, `controller-loop`,
  `state-of-union`, and `northstar-planning`.
- Preserve the seventeen-skill kernel unless an explicit later decision creates
  a new top-level skill. The safer default is to define a `repo-bootstrap` or
  `sdlc-bootstrap-repo` workflow facade over existing lifecycle skills.
- Keep raw secret values out of prompts, artifacts, logs, and committed state.
  Credential discovery should record references, auth modes, scopes, validation
  results, and owners, not secret material.
- Add non-blocking planning questions for namespace naming, infrastructure
  domain-agent boundaries, self-service storage mounts, controller model choice,
  and Orbit ownership.
