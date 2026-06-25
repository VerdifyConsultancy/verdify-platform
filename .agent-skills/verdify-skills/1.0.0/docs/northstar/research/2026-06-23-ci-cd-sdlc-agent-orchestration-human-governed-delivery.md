# CI/CD, SDLC, Agent Orchestration, and Human-Governed Autonomous Delivery

## Executive summary

The strongest current pattern for what you are aiming to build is not "fully autonomous delivery," but **human-governed, policy-constrained, Git-anchored autonomous execution**: agents can plan, code, test, and prepare deployments, but authoritative state changes still flow through versioned artifacts, explicit reviews, protected environments, and continuously reconciled controllers. That pattern is reinforced across GitHub Actions and GitLab CI features for environment protection and review gates, OpenGitOps principles for declarative and continuously reconciled delivery, and Kubernetes guidance for least-privilege RBAC, namespace isolation, quotas, and secrets handling. citeturn26view4turn26view6turn36view3turn46view3turn46view6turn46view0

Against the requirement set named in your brief--**Gravity, Agent Platform, Skills/SDLC, North Star governance, controller/wave lifecycle, repo hygiene, observability, RBAC/secrets, review inbox, browser terminal, and session ledger**--the most credible architecture is a layered one. Use a mainstream CI substrate for eventing and approvals, GitOps for environment reconciliation, a graph- or event-driven agent orchestrator for multi-agent work, and a durable audit/ledger plane that binds transcript -> plan -> issue -> branch/worktree -> PR -> deployment -> runtime evidence. In practice, that means choosing one of two dominant paths: **GitHub Actions + Argo CD/Flux + LangGraph/AutoGen + OpenTelemetry + SQLite/Postgres ledger**, or **GitLab CI/CD + Argo CD/Flux + AutoGen/LangGraph + OpenTelemetry + Postgres ledger**. citeturn26view4turn26view0turn31view0turn32view5turn48view0turn39view3turn45view4turn43view0turn44view2

The places where your envisioned platform can **lead** are the explicit governance fabric and artifact model: a durable **North Star** document for repo-scoped intent, explicit **wave lifecycle** objects, a generated **TRACEABILITY.yaml**, and a **session ledger** that records prompts, tool calls, approvals, diffs, tests, deployments, and rollback evidence in one navigable graph. Existing tools each cover slices of that stack, but few unify them. Claude Code's `CLAUDE.md` and Codex's `AGENTS.md` show that persistent repo instructions are now a first-class pattern for coding agents; LangGraph and LangSmith show durable execution, persistence, and execution trace concepts; AutoGen shows multi-agent coordination with human agents and intervention points; GitHub and GitLab provide the enforced gates those agents still need. citeturn23view0turn23view3turn48view0turn48view2turn39view0turn39view2turn49view0turn26view4

The main **risk areas** are also clear. Argo CD's PR-based preview generation is powerful, but its own docs warn about secret leakage and why only admins should create certain ApplicationSets. Argo's browser terminal is disabled by default for security reasons. Kubernetes warns that listing secrets is effectively equivalent to reading them, and that secrets are unencrypted in `etcd` by default unless you enable encryption at rest. GitHub's OIDC guidance strongly favors short-lived cloud credentials over hardcoded secrets. These are exactly the fault lines where agentic delivery platforms fail if convenience outruns governance. citeturn31view0turn31view4turn31view5turn46view0turn46view2turn26view6

The recommended near-term move is a **narrow pilot**: one repository, one non-production cluster, one protected staging environment, one preview-environment pattern, three lanes of agents, and one human review inbox. Success should be measured less by raw autonomy and more by **trace completeness, policy conformance, rollback confidence, and review efficiency**. If that pilot proves out, the 3-6 month roadmap should harden tenancy, approvals, provenance, replayability, and semantic retrieval before expanding to broader multi-repo or production mutation rights. citeturn36view3turn46view6turn35view1turn45view1

## Framing and assumptions

This report treats the named elements in your brief--**Gravity, Agent Platform, Skills/SDLC, North Star governance, controller/wave lifecycle, repo hygiene, observability, RBAC/secrets, review inbox, browser terminal, session ledger**--as the working requirement set. I did **not** assume a cloud vendor, budget, or existing CI standard. Because the tools you named are heavily Kubernetes- and GitOps-centric, the analysis assumes a **Kubernetes-centered delivery platform** even if some pipelines still execute outside the cluster. That assumption is consistent with the dominant feature surfaces in Argo CD, Flux, Tekton, Actions Runner Controller, and Crossplane. citeturn31view3turn32view0turn27view4turn26view7turn30view0

One ambiguity is the term **"Reflex."** In current agent literature and framework practice, the closest high-signal interpretation is **Reflexion**, the reasoning pattern that adds episodic reflection and verbal reinforcement between episodes. I used that interpretation when discussing agent patterns, because the major current framework docs and papers center on LangGraph, AutoGen, AutoGPT, Claude Code, Codex, ReAct, Reflexion, Voyager, and SWE-agent rather than a distinct, actively documented "Reflex" orchestration framework. citeturn24academia0turn25academia1

The most important design boundary is governance. OpenGitOps defines the now-standard control loop as **declarative**, **versioned and immutable**, **pulled automatically**, and **continuously reconciled**. That means your "North Star governance" and "controller/wave lifecycle" should not be bolted on as secondary metadata; they should be versioned artifacts that participate in the same reconciliation and review model as code, infra, and observability assets. citeturn36view3

## Landscape survey

### Tool landscape and where each fits

The current platform landscape separates into four layers: CI runners and workflow engines, GitOps and environment control, infrastructure control planes, and agent orchestration/coding agents.

| Layer | Strong candidates | What they are best at | Limits for your use case |
|---|---|---|---|
| CI substrate | GitHub Actions, GitLab CI/CD, Tekton | Build/test automation, status checks, manual approvals, downstream pipelines, runner orchestration | Native traceability across agent sessions and delivery artifacts is still fragmented |
| GitOps and environment controllers | Argo CD, Flux, Spinnaker | Declarative delivery, reconciliation, promotion, canaries, pull-based deployment | Usually do not model transcript/session/approval semantics deeply |
| Infra control plane | Crossplane, Atlantis | Infra APIs as control plane; PR-driven Terraform planning/apply | Need policy wrappers and tenancy controls for autonomous or semi-autonomous use |
| Agent orchestration and coding agents | LangGraph/LangChain, AutoGen, AutoGPT, Claude Code, Codex | Durable agent loops, multi-agent patterns, repo instructions, coding workflows | Human-governed release controls still need to be imposed externally |

That partitioning matters because the strongest platforms today **compose** these layers rather than replacing them with one monolith. GitHub Actions has protected environments and required reviewers; GitLab CI/CD has review apps and parent-child or multi-project pipelines; Argo CD and Flux handle pull-based, reconciled delivery; LangGraph and AutoGen provide the durable or event-driven agent coordination model. citeturn26view4turn26view0turn26view2turn31view0turn31view3turn32view5turn48view0turn39view3

### Comparative view of major platforms

The table below is a synthesis of the current feature surface, with the "maturity" column representing an architectural assessment grounded in the cited docs, not a vendor benchmark.

| Platform | Primary mode | Kubernetes integration | Preview environment support | Human gate support | Agent orchestration fit | Maturity assessment | Primary evidence |
|---|---|---:|---:|---:|---:|---|---|
| GitHub Actions | Hosted/self-hosted CI workflows | Strong with ARC and runner scale sets | Moderate | Strong via protected branches and protected environments | Indirect | Very high | Environments can require reviewers; OIDC issues short-lived tokens; ARC runs on Kubernetes and supports runner scale sets. citeturn26view4turn26view6turn26view7turn26view8turn49view0 |
| GitLab CI/CD | CI/CD plus environments | Strong | Strong via Review Apps | Strong | Indirect | Very high | Review Apps create temporary per-branch or per-MR environments; downstream parent-child and multi-project pipelines support modular delivery. citeturn26view0turn26view1turn26view2turn26view3 |
| Argo CD | Pull-based GitOps CD | Native | Strong with PR generator | Moderate, usually via Git + RBAC | Indirect | Very high | Sync waves, ApplicationSets, PR-driven test environments, strict RBAC model. citeturn31view0turn31view3turn31view6 |
| Flux | Pull-based GitOps toolkit | Native | Moderate to strong | Moderate, usually via Git PRs | Indirect | Very high | Automated image updates, repo-per-env/team/app patterns, Kubernetes-native multi-tenancy and monitoring. citeturn32view0turn32view4turn32view5turn32view6 |
| Tekton | Kubernetes-native pipeline engine | Native | Moderate | Moderate, extensible | Indirect | High | CRD-based Tasks/Pipelines/PipelineRuns; Chains adds signed provenance and attestations. citeturn27view3turn27view4turn35view1 |
| Spinnaker | Continuous delivery orchestrator | Strong | Moderate | Strong, including manual judgment stages | Indirect | High but more specialized | Pipelines, stages, deployment strategies, managed delivery to logical environments. citeturn28view0turn28view3 |
| Crossplane | Platform control plane | Native | N/A | Indirect | Indirect | High | Crossplane is a control plane framework for platform engineering with composition, environment configs, and operations. citeturn30view0turn30view1turn30view2turn30view3 |
| Atlantis | Terraform PR automation | Moderate | N/A | Strong for infra review | Indirect | High | `atlantis.yaml`, plan/apply requirements, repo locking, and security warnings around arbitrary code. citeturn29view0turn29view1turn29view3 |
| Argo Workflows | Workflow engine on Kubernetes | Native | N/A | Extensible | Moderate | High | Strong for DAG-style workflow execution on Kubernetes; best when you want workflow control separate from GitOps CD. citeturn5view2 |
| LangGraph and LangChain | Agent orchestration framework | Indirect | N/A | Strong when combined with HITL middleware | Native | High | LangGraph underpins LangChain agents for durable execution, human-in-the-loop, persistence, and tracing. citeturn48view0turn48view2turn48view3 |
| AutoGen | Event-driven multi-agent framework | Indirect | N/A | Strong | Native | High | Event-driven multi-agent architecture, group-chat and intervention patterns, MCP integrations. citeturn39view3turn39view0turn39view1turn39view2 |
| AutoGPT Platform | Low-code or hosted autonomous workflows | Indirect | N/A | Variable | Native | Medium | Good workflow builder and block model, but less naturally aligned with tightly governed SDLC artifact chains. citeturn39view5turn39view6 |
| Claude Code | Coding agent surface | Indirect | N/A | Strong through permissions, hooks, repo instructions | Strong for repo agents | High | `CLAUDE.md`, skills, hooks, parallel agents, GitHub Actions / GitLab CI integrations. citeturn23view0turn23view1turn23view2 |
| Codex | Coding agent surface | Indirect | N/A | Strong when constrained by repo instructions and isolated tasks | Strong for repo agents | High | `AGENTS.md`, isolated task environments, repo-guided standards and testing behavior. citeturn23view3turn23view4 |

### What the market is converging on

The community is converging on three patterns.

First, **PR- and environment-centered delivery** is the dominant review-controlled mechanism. GitLab Review Apps, Argo CD's PR generator, and Flux's repo-per-env or manual-prod-bump patterns all reinforce "preview fast, promote slowly." citeturn26view0turn31view0turn32view5

Second, **repo-scoped persistent guidance for agents** is no longer an edge technique. Anthropic and OpenAI both formalize it with `CLAUDE.md` and `AGENTS.md`, respectively. That is highly relevant to your proposed **NORTH_STAR.md**: the market signal is that durable, repo-local, machine-readable operating instructions are now an expected primitive, not a workaround. citeturn23view0turn23view1turn23view3

Third, **agent orchestration is moving toward durable graphs and explicit managers**, not "one giant prompt." LangGraph emphasizes durable execution, persistence, and HITL, while AutoGen formalizes event-driven runtimes, group-chat managers, and intervention-based approvals. citeturn48view0turn48view2turn39view3turn39view0

## Best-practice design patterns

### Delivery control, promotion, and review gating

For human-governed autonomous delivery, the most defensible pipeline is:

1. Agents draft plans and code in isolated branches or worktrees.
2. CI runs deterministic checks and produces status signals.
3. Preview environments are created per PR or MR.
4. Human reviewers approve both code and deployment intent.
5. Environment or branch protections gate promotion.
6. GitOps controllers reconcile the approved desired state into the target environment.

GitHub environments can require up to six reviewers before jobs proceed, and environment secrets are only exposed after protection rules pass. Protected branches can require reviews, status checks, conversation resolution, merge queues, and successful deployments. GitLab Review Apps create temporary environments per branch or merge request, and downstream pipelines let you separate build, app delivery, and deployment concerns cleanly. citeturn26view4turn49view0turn26view0turn26view1turn26view2

For your **review inbox**, that implies a single place where a human sees at least five evidence bundles together: the plan diff, the code diff, the test report, the preview URL, and the policy/traceability summary. In the current ecosystem, that "inbox" is usually composed from GitHub or GitLab rather than bought whole. The gap--and your opportunity--is to generate a higher-order review object that aggregates those artifacts before the human decides. That is where your **Gravity** layer can add real leverage. GitHub already treats issues and pull requests as the core planning and collaboration substrates, and ties issue references, projects, checks, and review status together. citeturn40view0turn40view2turn40view3

### Namespace, quota, and tenancy strategy

Kubernetes recommends namespaces as the unit for limiting aggregate resource consumption and for separating resources with different trust or tenancy requirements. ResourceQuotas constrain aggregate consumption per namespace and can even be scoped by `PriorityClass`. Kubernetes also notes that namespace boundaries are still weak security boundaries, so they should not be mistaken for complete isolation. citeturn46view6turn46view5turn46view3

For your platform, the practical strategy is:

- **Permanent namespaces** for control-plane lanes: `gravity-system`, `agent-platform`, `observability`, `crossplane-system`, `gitops-system`.
- **Environment namespaces** or clusters for `dev`, `staging`, and `prod`.
- **Ephemeral preview namespaces** per PR or wave, with hard quotas, TTL cleanup, and default-deny ingress/egress.
- **No shared "playground" namespace** for agent execution across tenants.

NetworkPolicy is additive and pod-centric, so default-deny plus explicit allowlists is the right starting posture. That matters especially for preview environments, browser terminals, and any tool-execution sidecars. citeturn47view0turn47view1

Flux's documented repo-per-environment, repo-per-team, and repo-per-app patterns are particularly useful here. The most balanced setup for your requirement set is usually **repo-per-team or repo-per-app for developer-owned delivery**, with a **platform-admin config repo** controlling cluster-wide resources and onboarding. Flux explicitly documents that approach, with platform admins managing cluster add-ons and team onboarding while dev teams own app definitions and promotion. citeturn32view4turn32view5

### Least privilege, secrets, and identity

Kubernetes' RBAC guidance is uncompromising: assign only the minimum permissions required, minimize distribution of privileged service-account tokens, and treat namespace boundaries as weak. Kubernetes' secrets guidance is equally direct: secrets are stored unencrypted in `etcd` by default, `list` access effectively exposes their contents, mounted-secret access should be isolated, and only cluster admins should access `etcd`. citeturn46view3turn46view4turn46view0turn46view2

For autonomous delivery this means:

- **Agents should not hold broad cluster-admin credentials.**
- **CI should use OIDC-issued short-lived cloud credentials**, not duplicated long-lived secrets.
- **Runtime secrets should be injected at execution time** using Vault Agent Injector or External Secrets Operator.
- **Git should store only encrypted secret references or declarations**, not plaintext credentials.
- **All production mutation should be mediated by controller identities with narrow scopes**, not by direct agent identities.

GitHub's OIDC docs explicitly recommend short-lived access tokens from the cloud provider instead of long-lived duplicated secrets. Vault's injector and External Secrets Operator are both mature ways to pull runtime secrets into workloads without baking them into repos or CI variables. citeturn26view6turn18view7turn18view8

### Observability as code and supply-chain evidence

Grafana's observability-as-code guidance is strongly aligned with your requirement set: define dashboards, data sources, and other resources in code for version control, automated testing, and CI/CD deployment. Grafana also supports file provisioning of dashboards from the filesystem or Git-synced assets, while OpenTelemetry provides the cross-signal model for traces, metrics, and logs. Prometheus remains the default time-series backbone for Kubernetes-native monitoring. citeturn45view1turn45view3turn45view4turn45view5

For **observability**, the right model is to instrument not only application delivery but also **agent behavior**. LangSmith's framing of traces, tool calls, state transitions, and latency is conceptually the right target, even if you implement the substrate yourself or with OpenTelemetry. Every session and wave should emit:

- a `trace_id`,
- a `wave_id`,
- a `session_id`,
- a `plan_id`,
- a `pr_id`,
- a `deployment_id`,
- a `policy_decision_id`.

That gives you a coherent path from transcript to production state. citeturn48view2turn45view4

For supply-chain evidence, Tekton Chains is the most directly aligned OSS component in the sources gathered here. It observes `TaskRuns` and `PipelineRuns`, snapshots them on completion, signs them, and stores attestations in standard payload formats including SLSA provenance. Even if you do not adopt Tekton as your primary CI engine, its provenance strategy is a strong reference architecture for your ledger and compliance model. citeturn35view1turn35view2

## Proposed operating model against your requirement set

### A target artifact and control model

Your platform should treat the following as **first-class, versioned artifacts**:

- `NORTH_STAR.md` for canonical intent, operating principles, lane definitions, and governance boundaries.
- `TRACEABILITY.yaml` for machine-readable linkage across issue, wave, worktree, PR, deployment, and session IDs.
- ADRs for durable architectural and policy decisions.
- Lane-specific runbooks and prompts.
- Signed CI/CD evidence and policy decisions.
- Session ledger events with stable IDs and redacted payload retention.

This is not theoretical. The broader ecosystem is already institutionalizing repo-local agent instructions and decision records. `CLAUDE.md` and `AGENTS.md` are direct precedents for repo-scoped agent instructions, while ADR communities define the idea of project-level decision logs and even PR-time decision guardrails. citeturn23view0turn23view3turn41view0turn41view2turn41view3

A practical entity model looks like this:

```mermaid
erDiagram
    NORTH_STAR ||--o{ ADR : governs
    NORTH_STAR ||--o{ WAVE : authorizes
    WAVE ||--o{ PLAN : contains
    WAVE ||--o{ SESSION : spawns
    PLAN ||--o{ ISSUE : maps_to
    ISSUE ||--o{ BRANCH : implemented_by
    BRANCH ||--o{ WORKTREE : checked_out_as
    BRANCH ||--o{ PR : proposed_as
    PR ||--o{ PREVIEW_ENV : creates
    PR ||--o{ REVIEW_DECISION : receives
    PR ||--o{ DEPLOYMENT : promotes_to
    SESSION ||--o{ LEDGER_EVENT : emits
    DEPLOYMENT ||--o{ LEDGER_EVENT : emits
    REVIEW_DECISION ||--o{ LEDGER_EVENT : emits
    SESSION ||--o{ TRACEABILITY_RECORD : updates
    PR ||--o{ TRACEABILITY_RECORD : updates
    DEPLOYMENT ||--o{ TRACEABILITY_RECORD : updates
```

And the wave lifecycle should be explicit, not implicit:

```mermaid
stateDiagram-v2
    [*] --> Drafted
    Drafted --> Planned: transcript_to_plan
    Planned --> Scoped: wave_created
    Scoped --> InProgress: agent_execution_started
    InProgress --> AwaitingReview: PR_opened
    AwaitingReview --> ChangesRequested: review_failed
    ChangesRequested --> InProgress: agent_rework
    AwaitingReview --> Approved: code_and_policy_approved
    Approved --> Previewed: preview_env_healthy
    Previewed --> Staged: protected_env_released
    Staged --> Promoted: production_pr_merged
    Promoted --> Reconciled: gitops_sync_healthy
    Reconciled --> Closed: ledger_complete
    Reconciled --> RolledBack: rollback_triggered
    RolledBack --> Closed: audit_complete
```

### Branch, issue, and worktree model

GitHub Issues now support sub-issues, issue dependencies, and strong project integration, which is useful for your **wave** model. Git worktrees let a single repository maintain multiple active working trees tied to different branches, which is the cleanest way to support multiple concurrent waves without branch checkout thrash. Pull requests then become the formal review unit that binds the branch to tests, comments, approvals, and deployment checks. citeturn40view0turn21view0turn40view2

The most robust working convention is:

- One **issue tree** per initiative.
- One **wave** per bounded implementation slice.
- One **branch and worktree** per wave.
- One **PR** per wave into the guarded target branch.
- One **preview namespace** per PR.
- One **traceability record** updated at every state change.

Recommended naming:

- Issue: `WAVE:<id> <lane> <goal>`
- Branch: `wave/<id>-<lane>-<slug>`
- Worktree path: `.worktrees/wave-<id>`
- Preview namespace: `pr-<number>` or `wave-<id>`
- Traceability file entry key: `<wave-id>:<artifact-type>:<artifact-id>`

This is where your explicit `TRACEABILITY.yaml` pays off. Off-the-shelf platforms link *some* of these objects; almost none maintain a portable, repository-owned linkage file that an auditor--or another agent--can inspect without querying multiple APIs.

### Browser terminal and debug posture

A browser terminal is one of the highest-risk features in your requirement set. Argo CD's web terminal exists, but it is disabled by default for security reasons, and enabling it requires `exec.enabled=true`. Kubernetes also notes that ephemeral containers are a better fit for interactive troubleshooting when `kubectl exec` is insufficient, especially with distroless images. citeturn31view4turn31view5turn47view3

For that reason, the recommended posture is:

- **No browser terminal in production** at pilot stage.
- **Ephemeral containers first** for break-glass debugging.
- If a browser terminal is enabled in non-prod, gate it behind:
  - explicit approval,
  - short TTL,
  - session recording,
  - isolated namespace/network policy,
  - non-exportable credentials,
  - and a ledger entry tying terminal use to incident or review context.

This is a place where your platform should err toward governance over convenience.

### Session ledger and audit-store options

The best ledger architecture depends on scale.

| Option | Best phase | Strengths | Weaknesses | Recommendation |
|---|---|---|---|---|
| SQLite + JSONL | Pilot and single-team | Very low operational overhead; serverless, transactional; JSON functions built in; JSON Lines easy append-only event logs | Weak concurrent multi-writer story at larger scale | Best MVP |
| Postgres + pgvector + object store | Multi-team platform | Queryable relational core plus vector search; keep vectors with operational data | More ops overhead | Best default target after pilot |
| Dedicated event store + vector sidecar | Regulated or high-scale environments | Strong event semantics and replay | Most complexity | Use only if justified by compliance scale |

SQLite is serverless, transactional, single-file, and has JSON functions built in by default in modern versions; JSON Lines is a simple UTF-8, one-JSON-value-per-line append format; pgvector adds exact and approximate nearest-neighbor search directly in Postgres; Qdrant adds payload-indexed filtered semantic search if you outgrow Postgres-only retrieval. citeturn43view0turn43view1turn44view0turn44view1turn44view2turn44view4

For your use case, the right progression is:

- **Pilot:** SQLite for control-plane state, JSONL for immutable event append logs, object storage for large artifacts.
- **Expansion:** Postgres as the system of record, pgvector for semantic recall over sessions/plans/ADRs/reviews, object storage for transcripts and large evidence bundles.
- **Optional later:** Qdrant only if filtered high-scale semantic retrieval becomes a first-class product requirement.

That progression keeps the initial system understandable while preserving a path to richer search and compliance.

## Gaps, leverage points, and a recommended plan

### Where the current ecosystem meets your requirements

The ecosystem is already strong on several of your lanes.

- **Repo hygiene and review gating:** GitHub protected branches and PRs, GitLab review apps and child pipelines, Atlantis plan/apply requirements, and Flux promotion patterns are mature. citeturn49view0turn40view2turn26view1turn26view2turn29view0turn32view5
- **Controller or wave lifecycle primitives:** Argo CD sync waves, GitLab downstream pipelines, Tekton PipelineRuns, and AutoGen group-chat or event-driven managers provide building blocks. citeturn31view3turn26view2turn27view3turn39view0turn39view3
- **Observability and supply-chain evidence:** OpenTelemetry, Prometheus, Grafana-as-code, and Tekton Chains are mature enough to anchor a serious audit story. citeturn45view4turn45view5turn45view1turn35view1
- **Persistent agent instructions:** Claude Code and Codex make repo-scoped instruction files explicit. citeturn23view0turn23view3

### Where your requirement set is still ahead of the market

You are ahead of most off-the-shelf platforms in four places.

The first is **North Star governance** as a durable control artifact that binds product intent, architecture intent, delivery policy, and agent instructions. Existing tools have pieces of this, but usually as separate concepts. citeturn23view0turn23view3turn41view0

The second is the explicit **wave** abstraction. Existing platforms have branches, pipelines, stages, ApplicationSets, or group chats, but they do not usually expose a first-class object that says "this bounded implementation wave carries the following plan, review, preview, deployment, and audit lineage." That is a genuine product differentiation point.

The third is the **session ledger** as the audit spine for delivery. LangSmith-like trace concepts exist, and Tekton captures execution evidence, but the market still lacks a broadly adopted OSS pattern that unifies transcript provenance, agent state transitions, repo instructions, approvals, and deployment evidence in one user-owned artifact chain. citeturn48view2turn35view1

The fourth is the **review inbox** as a curated bundle for human governors rather than a raw PR page. Dev platforms give people approval surfaces; they rarely synthesize the best reviewable context across planning, policy, implementation, and environment evidence.

### Main risks and how to contain them

The sharpest risks are not generic AI risks; they are very specific implementation risks.

- **Secret leakage through dynamic environment generators.** Argo CD explicitly warns about PR generator implications and admin-only creation in sensitive cases. citeturn31view0
- **Privilege sprawl through service accounts and cluster roles.** Kubernetes RBAC guidance warns to minimize privileged token distribution and assign minimal rights only. citeturn46view3turn46view4
- **Unsafe browser terminal rollout.** Argo's terminal is disabled by default; treat that as a signal, not merely a setup detail. citeturn31view4turn31view5
- **Secret exposure through broad read access.** Kubernetes warns that `list` or `watch` on secrets is highly privileged and that secrets are unencrypted in `etcd` by default. citeturn46view0turn46view2
- **Arbitrary code execution in infra automation.** Atlantis warns that PR-driven custom workflows can amount to arbitrary code execution on the Atlantis server. citeturn29view0

The response is structural: PR-only mutation, short-lived credentials, non-prod previews, no production shell access, signed evidence, and mandatory human approval on protected environments.

### OSS components to adopt or adapt

The following is the most leverage-rich stack for your requirements.

| Lane | Recommended OSS / foundation | Why it fits | Repo / project link |
|---|---|---|---|
| Gravity control plane | Crossplane + Argo CD or Flux | Declarative platform control plus reconciled delivery | [Crossplane](https://github.com/crossplane/crossplane), [Argo CD](https://github.com/argoproj/argo-cd), [Flux](https://github.com/fluxcd/flux2) |
| Agent platform | LangGraph or AutoGen | Durable graphs or event-driven multi-agent coordination with HITL patterns | [LangGraph](https://github.com/langchain-ai/langgraph), [AutoGen](https://github.com/microsoft/autogen) |
| Skills / SDLC | Repo-scoped instruction files + ADRs | Persistent guidance for coding agents and durable decision log | [Architecture Decision Records](https://github.com/architecture-decision-record/architecture-decision-record) |
| Controller / wave lifecycle | Argo Workflows or Tekton | Native workflow state objects and rich execution model | [Argo Workflows](https://github.com/argoproj/argo-workflows), [Tekton](https://github.com/tektoncd/pipeline) |
| Repo hygiene / review inbox | GitHub Actions or GitLab CI/CD | Protected branches, status checks, environments, review apps | [Actions Runner Controller](https://github.com/actions/actions-runner-controller) |
| Observability | OpenTelemetry + Prometheus + Grafana | Cross-signal telemetry and versioned dashboards/alerts | [OpenTelemetry](https://github.com/open-telemetry/opentelemetry-collector), [Prometheus](https://github.com/prometheus/prometheus), [Grafana](https://github.com/grafana/grafana) |
| RBAC / secrets | External Secrets Operator or Vault Agent Injector | Runtime secret retrieval and rotation | [External Secrets Operator](https://github.com/external-secrets/external-secrets), [Vault](https://github.com/hashicorp/vault) |
| Session ledger | SQLite initially, then Postgres + pgvector | Fast MVP, then integrated semantic retrieval | [SQLite](https://www.sqlite.org/), [pgvector](https://github.com/pgvector/pgvector) |
| Infra PR lane | Atlantis | Human review and lock-aware infra changes | [Atlantis](https://github.com/runatlantis/atlantis) |
| Supply-chain evidence | Tekton Chains | Signed provenance and attestations | [Tekton Chains](https://github.com/tektoncd/chains) |

### Recommended pilot and roadmap

A sensible short-term pilot is **six to eight weeks** with one repo and one staging cluster.

| Milestone | Owner | Deliverable | Success metric |
|---|---|---|---|
| Governance baseline | Platform architect | `NORTH_STAR.md`, ADR skeleton, `TRACEABILITY.yaml` schema | 100% of pilot changes link issue ↔ wave ↔ PR ↔ deploy |
| Agent execution lane | Agent platform lead | Planner, implementer, reviewer lanes using LangGraph or AutoGen | ≥80% of sessions produce machine-readable plan + patch + test summary |
| Review inbox | Developer experience lead | Single approval view aggregating plan diff, code diff, tests, preview URL, policy verdict | Median human review decision under 15 minutes |
| Preview environments | Platform engineering | Ephemeral namespace per PR with quota and TTL | Preview creation under 10 minutes; automatic cleanup >95% |
| Staging protection | DevOps lead | Protected environment with required reviewers and OIDC auth | 0 direct non-PR staging mutations |
| Ledger and observability | Reliability lead | SQLite/JSONL ledger + OTel traces + Grafana dashboards | 100% of pilot waves have trace, session, and deployment IDs |
| Security hardening | Security lead | Least-privilege SAs, secret injection, no prod terminal | 0 plaintext secrets in repo/CI; 0 privileged default SAs |

The 3-6 month roadmap should then move in this order:

| Window | Focus | Outcomes |
|---|---|---|
| First 3 months | Operationalize the kernel | Postgres ledger, richer review inbox, policy engine, multi-repo onboarding, signed provenance, production-readiness checklist |
| Next 3 months | Harden and scale | Team/tenant boundaries, repo-per-team promotion flow, incident rollback integration, semantic search over sessions and ADRs, audited break-glass workflows |

In practical terms, do **not** scale the number of autonomous lanes until the following are already true: every session is linkable to a wave, every wave is linkable to a PR, every deployment is linkable to a protected approval, and every secret path is short-lived or runtime-injected.

### Suggested policies

A minimal policy pack for the pilot should state:

- Agents may **open issues, create branches/worktrees, commit, and open PRs**, but may not write directly to protected branches.
- Agents may deploy only to **preview** and **staging** environments; production requires human approval plus protected environment gate.
- Browser terminal is **disabled by default**; production shell access is prohibited; break-glass uses ephemeral containers with recording and explicit approval.
- Secrets must be **runtime-injected**; CI uses **OIDC short-lived** credentials; no long-lived cloud credentials in GitHub/GitLab secrets unless no OIDC path exists.
- Every wave must update `TRACEABILITY.yaml` and emit a ledger event on plan creation, PR open, approval, deploy, and rollback.
- Every repo change that materially alters architecture, policy, or control flow must include an ADR update or ADR reference.

## Prompts, templates, and resources

### Lane-oriented prompt templates

These are designed to align with the patterns documented by Claude Code and Codex for repo-scoped instructions, and with graph or multi-agent orchestration frameworks that expect explicit roles and deterministic outputs. citeturn23view0turn23view3turn48view0turn39view0

#### Planner lane

```text
You are the Planner lane for this repository.

Follow NORTH_STAR.md, relevant ADRs, and TRACEABILITY.yaml conventions.
Read the issue, current repo state, and any existing wave artifacts.
Produce:
1. problem statement
2. constraints and non-goals
3. implementation wave proposal
4. acceptance criteria
5. rollback considerations
6. files and systems likely to change
7. tests and validations to run
8. required human review points

Output JSON plus a concise markdown summary.
Do not modify code.
```

#### Implementer lane

```text
You are the Implementer lane for wave {{wave_id}}.

Obey NORTH_STAR.md, applicable ADRs, and repo instruction files.
Work only in branch wave/{{wave_id}}-{{lane}}-{{slug}}.
Update TRACEABILITY.yaml with touched files, issue refs, and validations.
Run all required checks from repo instructions.
Leave the worktree clean and committed.
Output:
- patch summary
- tests executed
- risks introduced
- review notes for the human inbox
```

#### Reviewer lane

```text
You are the Reviewer lane.

Review the wave against:
- acceptance criteria
- NORTH_STAR.md
- ADR compliance
- security and secret handling rules
- observability requirements
- rollback readiness
- repo hygiene and traceability completeness

Return:
- APPROVE, CHANGES_REQUESTED, or ESCALATE
- findings by severity
- exact missing evidence
- suggested follow-up tasks
```

#### Release controller lane

```text
You are the Release Controller for human-governed delivery.

Verify that:
- PR approvals satisfy protection rules
- required checks passed
- preview environment is healthy
- traceability record is complete
- deployment target is permitted for this wave
- rollback path exists

If any condition is missing, block promotion and explain why.
If all conditions pass, prepare the deployment mutation but require human release approval for protected environments.
```

#### Security lane

```text
You are the Security lane.

Inspect the wave for:
- privilege escalation risk
- broad Secret access
- long-lived credentials
- unsafe terminal or exec enablement
- missing NetworkPolicies
- missing quotas or namespace isolation
- policy or ADR drift

Return a machine-readable policy verdict and a short human summary.
```

### Suggested file templates

#### `NORTH_STAR.md`

```markdown
# North Star

## Purpose
## Product and platform boundaries
## Invariants
## Lane definitions
## Review and approval policy
## Deployment policy
## Secret and identity policy
## Observability requirements
## Traceability contract
## Break-glass policy
## Change classification
```

#### `TRACEABILITY.yaml`

```yaml
version: 1
initiative: INIT-001
waves:
  - id: WAVE-004
    issue: 142
    branch: wave/WAVE-004-agent-platform-review-inbox
    worktree: .worktrees/wave-WAVE-004
    pr: 318
    session_ids:
      - sess_2026_06_23_001
    plan_artifact: artifacts/plans/WAVE-004.json
    preview_namespace: pr-318
    deployments:
      - env: staging
        deployment_id: dep_stg_318_01
    adrs:
      - ADR-012
    tests:
      - ci/unit
      - ci/integration
      - preview/smoke
    approvals:
      - human_release_approval
    status: awaiting_review
```

### Prioritized reading and viewing list

The most useful "read next" set is a combination of official docs, official recording hubs, and foundational papers.

| Priority | Resource | Why it matters |
|---|---|---|
| Highest | OpenGitOps principles and event hubs | Best vendor-neutral anchor for your governance and reconciliation model. citeturn36view3turn36view2 |
| Highest | GitHub protected branches, environments, and OIDC docs | Best concise package for review gates, deployment gates, and secretless CI auth. citeturn49view0turn26view4turn26view6 |
| Highest | Flux repository structure guidance | Best current OSS discussion of repo-per-env/team/app tradeoffs. citeturn32view3turn32view4turn32view5 |
| Highest | Argo CD sync waves, PR generator, RBAC, web terminal docs | Directly relevant to controller/wave lifecycle, previews, RBAC, and browser terminal risk. citeturn31view0turn31view3turn31view6turn31view4 |
| High | Tekton Chains and SLSA levels | Strongest open reference for attested pipeline evidence. citeturn35view1turn35view2 |
| High | LangGraph / LangChain and AutoGen docs | Most relevant current agent orchestration references for durable execution, HITL, state transitions, and multi-agent coordination. citeturn48view0turn48view2turn39view3turn39view0 |
| High | Claude Code and Codex repo-instructions docs | Best evidence for repo-scoped persistent instructions as an emerging standard. citeturn23view0turn23view3 |
| High | Kubernetes RBAC, Secrets, ResourceQuota, NetworkPolicy docs | Primary-source guidance for the infrastructure governance layer. citeturn46view3turn46view0turn46view6turn47view0 |
| Medium | ReAct, Reflexion, SWE-agent papers | Useful for agent-behavior patterns and failure modes in software tasks. citeturn24academia0turn25academia1 |
| Medium | OpenGitOps event recordings for ArgoCon, GitOpsCon, FluxCon | Best official talk stream that maps to your target stack, though this report did not resolve individual talk titles exhaustively. citeturn36view3 |

## Open questions and limitations

A few items remain more open than the rest.

The first is **precise transcript fidelity**. This report was framed against the requirement names included in your brief rather than a line-by-line re-derivation of the full transcript content, so if your transcript encoded stronger priorities among those lanes, the roadmap should be reweighted accordingly.

The second is **conference-talk specificity**. I prioritized official project documentation and official event hubs. The report therefore references the official OpenGitOps event and recording ecosystem with confidence, but it does not claim an exhaustive shortlist of specific KubeCon, GitOpsCon, FluxCon, or ArgoCon session titles. citeturn36view3

The third is **vendor surface evolution for coding agents**. Claude Code and Codex are moving quickly. The structural patterns they validate--repo-local instructions, session continuity, CI integration, controlled permissions, isolated execution--are strong enough to use now, but their exact UX details will continue changing. citeturn23view0turn23view3

The central conclusion holds despite those limitations: the best path is to build a **Git-governed, wave-oriented, policy-enforced delivery platform** that treats agent autonomy as a capability inside a controlled system of record, not as a replacement for one.
