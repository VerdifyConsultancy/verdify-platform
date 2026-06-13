# Verdify Platform Epics

Last updated: 2026-06-13

Agent name: `verdify-platform`

These are the EPIC-level board cards for the `verdify-platform` lane. Board
statuses use the lane contract vocabulary: `Backlog`, `Ready`, `In Progress`,
`In Review`, and `Done`.

## Current Sprint Proposal

S0 `lane-board-normalization` and S1 `platform-inventory-and-service-map` are
complete. The next sprint should be selected from the `Ready` epic cards on the
`verdify-platform` project board.

## Epics

### Lane Board Normalization

- User/value statement: Future agents and Jason can make board-level decisions
  from one canonical `verdify-platform` Project Board instead of scattered docs.
- Scope: Exact board creation/reuse, field/status contract, epic cards,
  fallback issue tracking blocks, root planning docs, and project-access gaps.
- Non-goals: Product-code work, live cluster changes, or cross-lane board edits.
- Acceptance criteria: Board named exactly `verdify-platform`; statuses
  `Backlog`, `Ready`, `In Progress`, `In Review`, `Done`; root docs updated;
  epic cards attached or a documented blocker issue exists.
- Status: `Done`
- Priority: P1
- Milestone: Lane board normalization
- Sprint: S0 `lane-board-normalization`
- Related files/issues/PRs/commits: issue #334, `PROJECT_BOARD.md`,
  `docs/PROJECT_BOARD_WORKFLOW.md`, `AGENT_LANE.md`,
  `COORDINATION_REQUESTS.md`, issues #330-#333, commits `713fa2d`, `d9f30c2`,
  `34bca46`, `4e968ea`.
- Dependencies: None active; project write access was available.
- Risks: Project field writes can drift from issue-body metadata; broad issue
  import can blur epic versus task cards.
- Evidence: GitHub Project #5, 16 epic cards, exact status vocabulary, closed
  #331 and #333, open #332, and current root lane docs.

### Platform Architecture Inventory

- User/value statement: Agents can orient safely in the current k3s-era stack
  without trusting stale VM-era architecture claims.
- Scope: Reconcile `README.md`, `docs/SERVICE_MAP.md`,
  `docs/SYSTEM-ARCHITECTURE.md`, `docs/FOLDER-HIERARCHY.md`,
  `docs/runbooks/laptop-operator.md`, manifests, CI, and Orbit context dump.
- Non-goals: Large architecture rewrite or changing runtime behavior.
- Acceptance criteria: Current docs distinguish k3s dev/prod, retired staging,
  historical VM references, service ownership, verification paths, and
  dependency-agent boundaries.
- Status: `Ready`
- Priority: P1
- Milestone: Enablement: Data Hygiene & Observability
- Sprint: S1 `platform-inventory-and-service-map`
- Related files/issues/PRs/commits: `README.md`, `docs/SERVICE_MAP.md`,
  `docs/SYSTEM-ARCHITECTURE.md`, `docs/FOLDER-HIERARCHY.md`, issue #207.
- Dependencies: `network-infra`, `storage-infra`, and `monitoring-stack` for
  shared route/storage/telemetry truth.
- Risks: Older docs still contain valid historical details next to stale
  deployment facts.
- Evidence: `docs/SERVICE_MAP.md`, `AGENTS.md`, Orbit context dump manifest.

### API/Service Map

- User/value statement: Agents can identify service entrypoints, manifests,
  ports, data stores, and verification commands before editing runtime code.
- Scope: API, MCP, ingestor, planner, setpoint server, Hermes, MQTT, lab,
  Grafana, DB, migration, backup, restore, shadow, and residual components.
- Non-goals: Full SQL lineage parser or live cluster validation.
- Acceptance criteria: Current service map exists, links source and manifest
  paths, names secret contracts by key only, and records uncertainty as
  boundary/dependency notes.
- Status: `Done`
- Priority: P1
- Milestone: Lane board normalization
- Sprint: S1 `platform-inventory-and-service-map`
- Related files/issues/PRs/commits: `docs/SERVICE_MAP.md`, issue #331, commits
  `34bca46`, `4e968ea`.
- Dependencies: None for docs; shared providers remain out of lane.
- Risks: Route/storage/monitoring details can change outside this repo.
- Evidence: Closed issue #331 and green CI/Container Publish on `4e968ea`.

### CI/CD And Promotion Hardening

- User/value statement: Every merge and promotion should prove the same
  source/image/manifest state that will run in dev and prod.
- Scope: CI gates, manifest validation, digest write-back, prod-promote race
  fixes, Actions PR creation, and k3s-era test cleanup.
- Non-goals: Direct prod ArgoCD sync or changing org settings without Jason.
- Acceptance criteria: CI is green, k3s-era tests replace destroyed VM asserts,
  prod-promote cannot race the dev-equality guard, and PR creation blockers are
  resolved or clearly Jason-gated.
- Status: `In Progress`
- Priority: P1
- Milestone: Deploy Enablement (agent access + firmware CI/OTA)
- Sprint: S2 `gitops-and-access-hardening`
- Related files/issues/PRs/commits: issue #335, `.github/workflows/`,
  `Makefile`, issues #319, #320, #322, #303, #307.
- Dependencies: Jason/org settings for #320; CI environment.
- Risks: CI can be green while test semantics still assume retired topology.
- Evidence: Latest `main` CI green at `4e968ea`; open issues #319, #320, #322.

### ArgoCD Deployment And GitOps Cleanup

- User/value statement: Kubernetes desired state stays durable, reviewable, and
  safe for the live greenhouse writer.
- Scope: Dev/prod Application manifests, retired staging removal,
  `live/platform-main` retirement, prod app rename, selective sync bugs,
  ServerSideApply behavior, and prod promotion mechanics.
- Non-goals: Direct durable `kubectl` changes, cluster-wide ArgoCD/CRD policy,
  or prod sync without Jason.
- Acceptance criteria: App names and target revisions match `main`, staging is
  removed or explicitly historical, prod rename has a safe orphan/readopt plan,
  and ArgoCD sync bugs have issue evidence.
- Status: `In Progress`
- Priority: P1
- Milestone: Enablement: Three-Env (dev/stage parity)
- Sprint: S2 `gitops-and-access-hardening`
- Related files/issues/PRs/commits: issue #336, `ARGOCD.md`,
  `deploy/k8s/argocd/apps/`,
  `deploy/k8s/overlays/`, issues #207, #220, #317, #318, #321.
- Dependencies: Jason for live prod app operations; `network-infra` for shared
  ingress where routes cross lanes.
- Risks: App deletion or bad sync scope can prune live resources.
- Evidence: `ARGOCD.md`, app manifests, open issues #317, #318, #321.

### Deploy Enablement And Agent Access

- User/value statement: Agents can validate and prepare live-safe work without
  overbroad credentials or unreviewed device impact.
- Scope: Live DB read path, firmware compile CI, OTA secret shape, agent tooling
  image, admin-token review, safe shadow loop, and branch-protection constraints.
- Non-goals: Unattended firmware OTA, credential rotation, or raw secret custody.
- Acceptance criteria: Epic #288 children #301-#307 are closed or explicitly
  gated; secret references are name/key only; deploy tooling works through
  least-privilege paths.
- Status: `In Progress`
- Priority: P1
- Milestone: Deploy Enablement (agent access + firmware CI/OTA)
- Sprint: S2 `gitops-and-access-hardening`
- Related files/issues/PRs/commits: issues #288 and #301-#307,
  `deploy/k8s/SECRETS.md`, `.github/workflows/ci.yml`.
- Dependencies: Jason, secret-delivery owner, and org settings.
- Risks: Too much access increases blast radius; too little access blocks
  verification and safe OTA prep.
- Evidence: Open deploy-enablement milestone and issues #301-#307.

### Data/Storage Durability And DB HA/PITR

- User/value statement: The live TimescaleDB has recoverable backups, verified
  restore paths, and a clear HA/PITR migration plan.
- Scope: DB backups, backup freshness, dev restore, CNPG/PITR, prod cutover
  runbooks, storage class/PV dependencies, and migration rollback safety.
- Non-goals: StorageClass/PV administration or destructive prod DB work without
  Jason.
- Acceptance criteria: Backup RPO is monitored, restore proof exists, CNPG gates
  are explicit, and any live DB cutover has a gated issue/runbook.
- Status: `In Progress`
- Priority: P1
- Milestone: M7 - HA: first-principles resilience
- Sprint: S3 `data-storage-and-observability-requests`
- Related files/issues/PRs/commits: `deploy/k8s/components/db-backup/`,
  `deploy/k8s/overlays/dev/db-restore-from-prod.yaml`, `deploy/k8s/cnpg/`,
  `docs/runbooks/db-copy-not-move.md`, issues #218, #243-#245.
- Dependencies: `storage-infra`, Jason for live DB cutover.
- Risks: CNPG/restore work can affect the production system of record.
- Evidence: Open #218 and #245; backup and restore manifests in repo.

### Observability, Data Hygiene, And Product Health

- User/value statement: Operators can see whether the greenhouse, planner, data
  pipeline, public lab site, and supporting telemetry are healthy.
- Scope: Health/smoke checks, stale host polls, planner dark-cycle verification,
  lab content freshness, site RAG refresh, Grafana panels, alerts, and public
  data hygiene.
- Non-goals: Owning the shared Prometheus/Loki/Grafana platform outside app-local
  dashboards.
- Acceptance criteria: Open health/data issues have owners and verification;
  shared monitoring requests are in `COORDINATION_REQUESTS.md`.
- Status: `Ready`
- Priority: P2
- Milestone: Enablement: Data Hygiene & Observability
- Sprint: S3 `data-storage-and-observability-requests`
- Related files/issues/PRs/commits: issues #43, #49, #75, #89, #210, #214,
  #215, #219, #308, #315, `grafana/`, `docs/grafana-panel-catalog.md`.
- Dependencies: `monitoring-stack`, `network-infra` for route visibility.
- Risks: Shared telemetry gaps can be mistaken for app defects.
- Evidence: Open issues in the Data Hygiene & Observability milestone.

### Greenhouse Control Optimization

- User/value statement: Climate control keeps plants safer through better
  deterministic firmware, setpoint, lighting, and irrigation behavior.
- Scope: Firmware-v2 requirements A-E, dispatcher solar anchoring, button
  override precedence, two-zone lighting, irrigation feedback, actuator-wear
  limits, schemas, and drift cleanup.
- Non-goals: Firmware OTA without Jason, hardware installation by the agent, or
  bypassing replay/invariant gates.
- Acceptance criteria: Epic #287 and children close with firmware replay diff,
  invariants, unit tests, schema drift guards, and Jason-gated OTA evidence when
  required.
- Status: `In Progress`
- Priority: P1
- Milestone: Greenhouse Control Optimization
- Sprint: S2 `gitops-and-access-hardening`
- Related files/issues/PRs/commits: issues #287, #289-#300, #323-#327,
  `firmware/`, `ingestor/`, `verdify_schemas/`, `docs/design/firmware-v2-simplification-2026-06-10.md`.
- Dependencies: Jason for OTA/hardware, `storage-infra` or `network-infra` only
  if runtime dependencies appear.
- Risks: Production firmware controls live relays; regressions can harm plants.
- Evidence: Open Greenhouse Control Optimization milestone and firmware gates in
  `AGENTS.md`.

### HA Resilience

- User/value statement: The greenhouse stack should survive common node, pod,
  network, and storage failures without unsafe writer duplication.
- Scope: Resource governance, PDBs/spread, edge HA, singleton-writer fencing,
  chaos acceptance, CNPG/PITR groundwork, and HA incident follow-ups.
- Non-goals: Cluster-wide networking/storage administration outside repo-owned
  manifests.
- Acceptance criteria: M7 open issues close or move to dependency-agent
  requests; live writer fencing is dev-proven and Jason-gated before prod arm.
- Status: `In Progress`
- Priority: P1
- Milestone: M7 - HA: first-principles resilience
- Sprint: S3 `data-storage-and-observability-requests`
- Related files/issues/PRs/commits: issues #225, #232, #235, #237-#242, #245,
  #316, prod overlay HA manifests.
- Dependencies: `network-infra`, `storage-infra`, Jason.
- Risks: HA changes can accidentally increase device-writer or DB risk.
- Evidence: M7 milestone issue set and existing HA manifests under
  `deploy/k8s/overlays/prod/`.

### Band And Compliance Rearchitecture

- User/value statement: Planner scoring should reward controller-attributable
  compliance instead of stale binary band compliance.
- Scope: Migration 147 reward swap, ladder re-anchor, compliance-v2 backfill
  evidence, planner/MCP prompt language, and restart documentation.
- Non-goals: Firmware twin divergence alarms or live DB apply without the
  migration safety gates.
- Acceptance criteria: #17 and #20 close with replayed anchor evidence,
  migration 147 applies under the correct transaction safety class, and required
  service restarts are documented.
- Status: `Ready`
- Priority: P0
- Milestone: Enablement: Compliance & Twins
- Sprint: S3 `data-storage-and-observability-requests`
- Related files/issues/PRs/commits: issues #13, #17, #20, #31, #14,
  `db/migrations/147-reward-swap-and-ladder-reanchor.sql`,
  `docs/design/band-compliance-architecture.md`.
- Dependencies: Jason for live prod DB/schema/runtime gates; restart
  documentation for `verdify-mcp` and `verdify-ingestor`.
- Risks: A bad reward swap can make planner scores look better or worse than
  the controller-attributable reality.
- Evidence: Open issue #13 with Project Tracking metadata and Compliance &
  Twins milestone evidence.

### Firmware Digital Twins

- User/value statement: Twin divergence metrics are trustworthy before they
  influence live decisions or deployment gates.
- Scope: Firmware twin setpoint coverage, twin shadow behavior, schema
  extensions, and divergence dashboarding.
- Non-goals: Treating current twin divergence as a control gate before coverage
  closes, or applying live DB/user changes without Jason.
- Acceptance criteria: #31 and twin schema work have proof, and any live-prod
  twin enablement has Jason-gated DB/user changes.
- Status: `Ready`
- Priority: P1
- Milestone: Enablement: Compliance & Twins
- Sprint: S3 `data-storage-and-observability-requests`
- Related files/issues/PRs/commits: issues #14, #31, #13, #17, #20, #324,
  `deploy/k8s/components/firmware-twin/`, `twin/`, `firmware/`,
  `docs/design/firmware-digital-twin.md`.
- Dependencies: Jason for live prod DB/schema/user gates.
- Risks: Incomplete setpoint coverage creates false divergence signals.
- Evidence: Open issue #14 with Project Tracking metadata and firmware-twin
  component notes.

### Decommission, Auth, And Residual Product Plane

- User/value statement: Retired VM/auth/edge leftovers are either removed safely
  or represented as explicit future product-plane work.
- Scope: Iris VM decommission follow-through, vault commit/data-loss gates,
  admin auth rehome, dead botauth backend, setpoint-server cutover, and
  internet-friendly future device channel.
- Non-goals: DNS/Auth/Cloudflare changes by this lane without coordination.
- Acceptance criteria: Residual issues are closed, reassigned, or converted to
  coordination requests with owner and evidence.
- Status: `Ready`
- Priority: P2
- Milestone: Enablement: Decommission & Auth
- Sprint: S3 `data-storage-and-observability-requests`
- Related files/issues/PRs/commits: issue #337, issues #91, #104, #118, #174,
  #175, #177.
- Dependencies: Jason, `network-infra`, secret/auth owners.
- Risks: Retired-system cleanup can delete still-referenced state if evidence is
  incomplete.
- Evidence: Open Decommission & Auth milestone.

### Hardware And Seasonal Operations

- User/value statement: Operator-gated hardware, seasonal, and crop-safety work
  stays visible without being confused with autonomous software tasks.
- Scope: Sensor repairs, irrigation feedback bring-up, OTA bake promotion,
  calibration, seasonal orchid/lighting policy, and hardware-gated control
  changes.
- Non-goals: Physical work, field calibration, or OTA execution by the agent
  without Jason.
- Acceptance criteria: Each item has a Jason gate, required evidence, and no
  autonomous live-device action.
- Status: `Backlog`
- Priority: P2
- Milestone: Hardware / Seasonal (operator-gated)
- Sprint: none
- Related files/issues/PRs/commits: issues #16, #35, #37, #45, #51, #52, #298.
- Dependencies: Jason and physical greenhouse work.
- Risks: Hardware assumptions can invalidate software acceptance.
- Evidence: Open Hardware / Seasonal milestone.

### Fable Workstream Clarification

- User/value statement: Fable-related work is either explicitly owned by
  `verdify-platform` or routed to the correct repo/agent.
- Scope: Search repo/docs/issues for Fable evidence, record ownership decision,
  and create a concrete epic only if in-repo work exists.
- Non-goals: Inventing Fable scope without code, docs, or issue evidence.
- Acceptance criteria: Issue #332 either closes with no in-repo ownership or
  points to exact Fable files/issues and follow-up epics.
- Status: `Backlog`
- Priority: P3
- Milestone: none
- Sprint: S0 `lane-board-normalization`
- Related files/issues/PRs/commits: issue #332.
- Dependencies: Jason or Orbit if Fable ownership is external.
- Risks: Phantom work can pollute the lane board.
- Evidence: No in-repo Fable surface found in the current lane pass.

### Repo Cleanup And Branch Review

- User/value statement: Archived and stale branches do not hide active lane work
  or confuse future agents about the canonical source.
- Scope: Review remote branches, archived worktree branches, open PR branches,
  historical context moved to Orbit, and any remaining repo cleanup issues.
- Non-goals: Destructive branch deletion without owner approval.
- Acceptance criteria: Issue #330 records branch decisions; live/platform-main
  and archived branches are classified; active PR branches are tied to epics.
- Status: `Ready`
- Priority: P2
- Milestone: Lane board normalization
- Sprint: S0 `lane-board-normalization`
- Related files/issues/PRs/commits: issue #330, remote branch inventory,
  `HISTORY.md`, Orbit context dump.
- Dependencies: Jason or repo admins for branch deletion.
- Risks: Deleting a branch can discard evidence for an open PR or staged plan.
- Evidence: `git branch -a` inventory and issue #330.

### Historical Completed Milestones

- User/value statement: Completed work remains discoverable with evidence but
  does not clutter active planning.
- Scope: M1-M6, cutover, VM retirement, HA incident response work already done,
  and closed issue/PR evidence.
- Non-goals: Reopening completed work without a new active issue.
- Acceptance criteria: Historical milestones and major completed work are in
  `HISTORY.md` and closed issue #333.
- Status: `Done`
- Priority: P2
- Milestone: historical
- Sprint: S0 `lane-board-normalization`
- Related files/issues/PRs/commits: `HISTORY.md`, `MILESTONES.md`, issue #333,
  issues #69-#73, #216, #217, PRs #270, #325, #328, #329.
- Dependencies: None.
- Risks: Historical docs can imply obsolete branch or staging models.
- Evidence: Closed issue #333 and `HISTORY.md`.

## Rules

- One issue has one primary owning agent.
- Board cards are EPICS. Child issues/tasks may exist, but planning happens at
  the epic level.
- If work depends on another lane, record the dependency in
  `COORDINATION_REQUESTS.md` and the issue `## Project Tracking` block.
- Historical work must be `Done` only when linked to closed issues, merged PRs,
  commits, or durable runbook evidence.
