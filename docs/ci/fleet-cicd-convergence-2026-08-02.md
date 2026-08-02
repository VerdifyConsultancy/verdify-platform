# Fleet CI/CD convergence evidence — 2026-08-02

**Verdict: `BLOCKED_PLATFORM`**  
**Lifecycle: `active-delivery`**  
**Estate lane:** `jvallery/agents#3088`  
**Repository lane:** `VerdifyConsultancy/verdify-platform#561`  
**Proof PR:** `VerdifyConsultancy/verdify-platform#559`  
**Broadcast:** `[[codex-broadcast:fleet-cicd-convergence-20260802T180658Z-88f1eab5]]`

This is a point-in-time reconciliation of the default branch, GitHub, the
centrally rendered Agent Fleet contract, the live CI WorkflowTemplates, and the
two Argo-delivered runtime surfaces. It does not authorize or perform a
production sync. Exact command output and the immutable final PR head are
recorded on issue #561 and PR #559.

## Scope and safety boundary

- Remote default branch at `2026-08-02T18:14:17Z`: `main` at
  `eb3ac9cf33001fa6c5271412284382b36c6abf9e`.
- Issue branch: `docs/fleet-cicd-e2e-2026-07-31` in dedicated worktree
  `/workspace/verdify-platform/scratch/worktrees/fleet-cicd-e2e-20260731`.
- The shared checkout was intentionally not used: it was on
  `auth/uniform-github-app-7570d1c`, ahead of `origin/main`, with an unrelated
  untracked `.mcp.json`.
- Filesystem and network access were available. Approval policy was
  non-interactive. The pod had namespace reads in `agent-fleet-ci`,
  `verdify-platform`, and `verdify-prod`, but could not create CI Workflows or
  read/patch Argo `Application` objects. No Secret object, value, or annotation
  was read.
- Production is the sole greenhouse environment and includes the only device
  writer. Its Argo application is manual-sync and `prune:false`. No firmware,
  device, database, DNS, storage, Secret, workload, or Argo mutation was made.

The lifecycle is `active-delivery`, not validate-only: this repository owns
the running greenhouse API/MCP/ingestor/planner stack and publishes multiple
Zot images, plus an isolated static Lab stage.

## Live workflow and enforcement inventory

There are no repository-authored GitHub Actions workflows on `main`.
`.github/workflows/` is absent and repository tests require it to stay empty.
GitHub registers only its synthetic `Dependency Graph` workflow at
`dynamic/dependabot/update-graph`; its trigger and runner are GitHub-managed,
it has no repository caller, and it consumes no repository Actions runner.
The eight retired workflow files and their replacements remain inventoried in
[`zero-paid-runner-ledger.md`](zero-paid-runner-ledger.md).

Actual validation and publishing run in namespace `agent-fleet-ci`:

| WorkflowTemplate | Caller / trigger | Work performed | Runner identity | Deadline / cleanup | Artifact and cache behavior | Credential references (names only) |
| --- | --- | --- | --- | --- | --- | --- |
| `verdify-platform-pr-ci` generation 13 | The centrally rendered PR sensor filters this repository, base `main`, and actions `opened`, `reopened`, `synchronize`; exact live Sensor reads are RBAC-forbidden | Reports pending, performs trusted precheck, calls `verdify-platform-ci/validate` for the exact head, then reports the terminal required context | Kubernetes SA `argo-ci-workflow`; no node selector; observed failed pod on `vm-k3s-node4`; no ARC runner | 3,600 s; `podGC: OnPodSuccess`; TTL 24 h success / 48 h failure; no supersession/cancel synchronization | No GitHub artifact/cache and no Argo artifact repository | `agent-fleet-ci-github-app` for precheck/read; `agent-fleet-repo-github-app` for status |
| `verdify-platform-ci` generation 30 | The centrally rendered push sensor filters non-deleted pushes to `refs/heads/main`; PR CI also calls its `validate` template | Classifies push, runs `make ci`, builds eleven images, probes Lab images, pins prod images, and opens a Lab-stage pin PR | Kubernetes SA `argo-ci-workflow`; validate has no node selector; build tasks call `repo-build` | 7,200 s; `podGC: OnPodSuccess`; TTL 24 h success / 48 h failure; no top-level synchronization | Workflow-local validation `emptyDir`; build outputs are parameters; no external artifacts/cache | `agent-fleet-ci-github-app` for classify/read/validate; `agent-fleet-repo-github-app` for pin writes; `zot-origin-verdifyconsultancy-ci-dockerconfig` for publish |
| `repo-build` generation 16 | Called eleven times by `verdify-platform-ci`; intended self-service caller is `agent-ci-build` | Exact-revision checkout, optional test, Kaniko build and Zot push | Kubernetes SA `argo-ci-workflow`; build selector `agentfleet.vallery.net/runner-eligible=true` | 3,600 s; `podGC: OnPodCompletion`; TTL 24 h success / 48 h failure | Workflow-scoped 20 Gi rebuildable Longhorn RWO workdir; no Argo artifact repository; Kaniko compressed cache disabled; emits digest, canonical image ref, short SHA, source revision and release time | `agent-fleet-ci-github-app`; caller-supplied push Secret, correctly `zot-origin-verdifyconsultancy-ci-dockerconfig` here |
| `repo-validate` generation 24 | Intended self-service caller is `agent-ci-validate`; this repo has no active caller because its config is absent | Executes declared checks and emits conclusion plus per-check report | Kubernetes SA `argo-ci-workflow`; no node selector; default runtime is the pinned Agent Fleet dev runtime | 3,600 s; per-check 900 s and one retry; `podGC: OnPodCompletion`; TTL 24 h success / 48 h failure | No GitHub artifact/cache and no Argo artifact repository; result is Workflow output parameters | `agent-fleet-ci-github-app` |

The live immutable build/validation tooling includes:

- Kaniko:
  `gcr.io/kaniko-project/executor@sha256:c3109d5926a997b100c4343944e06c6b30a6804b2f9abe0994d3de6ef92b028e`.
- Generic validation runtime:
  `registry.vallery.net/jvallery/agents-agent-dev-runtime@sha256:d3c2f8c47010a964e7f43dda467f8babd90ae63366bfe206ac1fb61c964cf316`.
- Generic control steps:
  `mirror.gcr.io/library/python@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de`.
- Verdify validation:
  `mirror.gcr.io/library/python@sha256:353cf2106d143e1d28f5d7c10c5f5c0387085bba22ef0f7f7e52c2c330fb1779`.
- Workflow scripting:
  `ruby@sha256:2f763b37070564bb00b736f1d4dba6e8f8d203b5f93b94463879fd8d79966f28`.
- Lab verification:
  `mirror.gcr.io/library/node@sha256:7725a5c2c83eed1d36258c66efae14b1ceccd021db9ed1d9559d3335ed3d68ed`.

### Required check and runner profile

`main` is protected and requires one context:
`Verdify Platform / Argo PR CI`. The observable enforcement level is
`non_admins`, and the required context has `app_id: null`. Rulesets and
environments are empty. Current App scope returns 403 for the full protection,
runner, and Actions-policy endpoints, so review requirements and the July
ledger's `strict: true` and linear-history settings could not be freshly
re-proven.

The check is therefore advisory in two important ways: administrators can
bypass non-admin enforcement, and the null App binding lets another repository
writer spoof the context name. All observed status payloads also have
`target_url: null`, so the check has no durable run-details link.

No GitHub Actions job or repo ARC runner performs the gate. The rendered
`validation-standard` label is only a prospective rule for any future Actions
workflow; it does not match current runtime reality. Actual execution is the
Argo service account and Kubernetes node recorded above.

## Repository gate and image matrix

The authoritative source gate is `make ci` (`scripts/ci-local.sh`). It runs
Ruff check/format, schema tests, device-writer guards, selected pure-logic
tests, migration rollback classification, generated-config checks, twin syntax
compiles, production Kustomize rendering, and diff-sensitive firmware gates
when `CI_BASE_REF` is provided. It does not imply the live, writable, device,
container, full firmware, or full site suites.

The live push workflow builds these exact image definitions:

| Image | Dockerfile | Context | Special test / build behavior | Desired-state destination |
| --- | --- | --- | --- | --- |
| `verdify-api` | `api/Dockerfile` | `.` | Standard build | prod overlay |
| `verdify-mcp` | `mcp/Dockerfile` | `.` | Standard build | prod overlay |
| `verdify-ingestor` | `ingestor/Dockerfile` | `.` | Standard build; live sole writer | prod overlay |
| `verdify-planner` | `planner_graph/Dockerfile` | `.` | Standard build | prod overlay |
| `verdify-migrate` | `db/Dockerfile.migrate` | `.` | Standard build | prod overlay / migration Job |
| `verdify-setpoint-server` | `scripts/Dockerfile.setpoint-server` | `.` | Standard build | prod overlay |
| `verdify-lab-publisher-k3s` | `scripts/Dockerfile.lab-publisher` | `.` | Standard build | prod overlay |
| `verdify-lab-astro` | `site-astro/Dockerfile` | `site-astro` | `npm ci`, snapshot hydration, pinned-kubectl verification, full site tests | Lab-stage overlay |
| `verdify-lab-release-agent` | `site-astro/Dockerfile.release-runtime` | `site-astro` | target `agent`; source SHA/time build arguments | Lab-stage overlay, dormant runtime |
| `verdify-lab-release-nginx` | `site-astro/Dockerfile.release-runtime` | `site-astro` | target `site`; source SHA/time build arguments | Lab-stage overlay, dormant runtime |
| `verdify-lab-occurrence-exporter` | `site-astro/Dockerfile.occurrence-exporter` | `site-astro` | source SHA/time build arguments | pinned in Lab-stage overlay |

Current coverage excludes the twin, custom CNPG image,
`site-astro/Dockerfile.production`, and retired Quartz build. The Quartz
runtime remains an explicit legacy GHCR digest pull.

Publish goes to in-cluster
`registry-origin.registry-origin.svc.cluster.local:5000` and yields immutable
external references under
`registry.vallery.net/verdifyconsultancy/<image>@sha256:<digest>`. The closed
helper mapping contains the correct `verdifyconsultancy` Zot scope and push
Secret name. A new exact-SHA registry digest was not produced in this proof
because source checkout fails before code runs and this pod cannot submit a
Workflow.

## Contract-to-reality reconciliation

| Contract surface | Live finding | Match? |
| --- | --- | --- |
| `.agent-fleet/ci.yaml` | Absent on `main`; both helpers fail closed | **No** |
| Standard build schema | Supports image name, Dockerfile, context, test and size profile; it cannot express `docker_target` or source metadata build arguments required by three Lab images | **No** — a partial config would misrepresent delivery |
| Registered runner profile | Runner API is 403 and no approved repo ARC profile is evidenced; actual gate is Argo on `vm-k3s-node4` | **No / advisory only** |
| Interactive repo App | Generated `gh`/Git auth works and the installation is scoped to exactly this repository | **Yes** |
| CI checkout App/broker | Read/precheck paths use `agent-fleet-ci-github-app`, which reports no VerdifyConsultancy installation; status/write paths use the newer repo App successfully | **No** |
| Zot scope | `verdifyconsultancy` maps to the correct named publisher reference; generic and bespoke templates emit immutable Zot refs | **Definition yes; new push unproved** |
| Argo applications | Repo declares prod `verdify-prod-dark`, `main`, prod overlay, manual sync, `prune:false`; metrics show Healthy/OutOfSync. Lab-stage metrics show Healthy/Synced and manual sync | **Runtime exists; direct Application read is RBAC-blocked** |
| Repo self-service | `agent-ci-build` and `agent-ci-validate` exist, but config is absent and this SA has `create workflows = no` | **No** |
| Managed branch identity | Live Agent Fleet inventory still configures agents for retired `live/platform-main`; GitHub and this repo use `main` | **No** |
| Managed CI prose | Claims GitHub Actions validates, while no repo workflow or Actions run exists; actual required status comes from Argo | **No** |
| Pin-review contract | Lab-stage opens a reviewed pin PR, but the prod pin step pushes directly to `main`; commit `ae13b911...` has no associated PR | **No** |

The generic schema and Workflow-create authorization are central interfaces.
This repository must not invent target/build-argument extensions, credentials,
RBAC, or a Verdify-specific broker to compensate.

## Credential reference inventory (names only)

CI and publishing references:

- Agent runtime profile: `github-app-installation`, `repo-agent-standard`,
  activation `enabled`.
- Source checkout/trusted precheck: `agent-fleet-ci-github-app`.
- Status and repository pin writes: `agent-fleet-repo-github-app`.
- Zot publish: `zot-origin-verdifyconsultancy-ci-dockerconfig`.
- Zot workload pull: `zot-origin-cluster-pull`.
- Legacy GHCR workload pull: `ghcr-jvallery-readonly`.

Runtime manifests also reference, by name only:
`verdify-app-secrets`, `verdify-ha-token`, `verdify-hermes`,
`verdify-hermes-slack`, `verdify-lab-publisher-s3`,
`verdify-grafana-secrets`, `verdify-firmware-ota`,
`verdify-firmware-wifi`, `verdify-vision-key`, and the legacy
`verdify-github-token`. The last name is still referenced by the firmware
builder despite the rendered no-ambient-token statement; its removal rollout
is incomplete. No value or Secret annotation was inspected.

## Exact-SHA proof and failure classification

The latest observed central run failed before checkout:

- Workflow: `verdify-platform-pr-ci-5brpl`.
- Head: `e61c514...` (PR #562).
- Started: `2026-08-02T10:22:54Z`; terminal failure at
  `2026-08-02T10:23:55Z`.
- Failing node/container:
  `verdify-platform-pr-ci-5brpl-validate-242330318` /
  `fetch-validate-context`.
- Literal error: `no CI App installation for owner VerdifyConsultancy`.

The same error occurred in recent workflows
`verdify-platform-pr-ci-ndj9h` and `verdify-platform-pr-ci-4827s`. Their
terminal status reporter succeeded through `agent-fleet-repo-github-app`,
which isolates the fault to the old read/checkout identity rather than repo
code. PRs #560 and #562 have terminal required-context failures with null
target URLs; PRs #558 and #559 initially had no status at all.

Self-service was probed at `2026-08-02T18:16:15Z` against exact default SHA
`eb3ac9cf33001fa6c5271412284382b36c6abf9e`:

```text
agent-ci-validate ... --print --output json
  exit 2: no CI spec at <worktree>/.agent-fleet/ci.yaml
agent-ci-build ... --output json
  exit 2: no build spec at <worktree>/.agent-fleet/ci.yaml
kubectl auth can-i create workflows.argoproj.io -n agent-fleet-ci
  no
kubectl auth can-i get applications.argoproj.io -n argocd
  no
kubectl auth can-i patch applications.argoproj.io -n argocd
  no
```

The final PR-head local gate and one post-push platform attempt are recorded on
#561/#559. A platform failure is not retried without a platform diff.

## Desired state, runtime, user path, and rollback

At `2026-08-02T18:23:06Z`, Argo metrics reported
`verdify-prod-dark` Healthy but **OutOfSync**, with autosync disabled and zero
orphans. Running prod is a mixed selectively synced digest set rather than one
Git revision:

| Workload | Running immutable digest | `main` desired digest | Ready |
| --- | --- | --- | --- |
| API | `sha256:984ba4936864851f375fdd53041e65c606c6aeb5dbedb7223c5fdafe0736bb6c` | `sha256:d28c45ce14c425cc06ef2a64430fa390602b93995d51b42fba2ac2719705b5e2` | 2/2 |
| MCP | `sha256:8cc8d7472f63b6dc8ecea47ab67d488f6c8b950dd8fd4388b7a42670d164354a` | `sha256:e3f37fc9ced69bfde0290c6aa8f6b662a983a77d42742f82e06c4df9b4ad5e3c` | 2/2 |
| Ingestor | `sha256:fcd13ad7aa91650db371b92f72bcf60caae61b6b1b9f1f79f69e6718541025e3` | `sha256:083386240ff684ac81d53eec58f89e57f095e4f390052bf1a7901f71cec14090` | 1/1 |
| Planner | `sha256:ff7de32d7de6b6eee73ad2dfc11f4bd140180b35a0fef6325803bbee0b1aa1b6` | `sha256:735cb005d436e067b013d606e037aa1a1b8cbee6a6af96187bc316e29cd68d73` | 1/1 |
| Setpoint server | `sha256:f5ac817f42f9a0f306692e1fb0434eb1b600c2ba5fa35da0dccdc618bbb3690a` | `sha256:7278d148e39818ec6098d76a88fff6289670a8811e6cd61cf44372eb9d5b8e06` | 1/1 |
| Lab publisher | `sha256:c6085836d0cb3a5bd083add258989502c84f5a07a6182f0e501aee0ffac4cee0` | `sha256:57c3ae05aaa1671f024ccf30af984e0d7a127e77a0a45ae02d8bf3fe51c3642d` | scheduled job path |
| Quartz Lab | `sha256:98ac23b6affa3b8af3ebb5c7f1c7abe31d00e2379c070f4d61b409130dae9845` | same | 2/2 |

Blindly syncing current `main` would roll multiple greenhouse workloads,
including the sole writer and setpoint server, so it is not a safe proof or
rollback. The safe rollback handle is the immutable per-workload running
digest set above and its historical pin commits. Recovery requires a reviewed
digest-only commit that first records one coherent known-good set, followed by
the explicit non-pruning, device-gated Argo sync.

Lab stage is coherent: at `2026-08-02T18:24:19Z`, metrics reported
`verdify-platform-lab-stage` Synced and Healthy, autosync disabled, with two
ready pods whose image ID exactly matches the `main` pin
`registry.vallery.net/verdifyconsultancy/verdify-lab-astro@sha256:1852cf2523041ef840b3eb1092050e4b3b19d2027494fc6b6c9809b930de93b7`.

Read-only public probes at `2026-08-02T18:23:56Z` returned:

```text
https://api.verdify.ai/health                 200
https://lab.verdify.ai/                       200
https://graphs.verdify.ai/api/health          200
https://mcp.verdify.ai/mcp                    406 (expected unauthenticated GET; route/TLS proven)
https://lab-stage.verdify.ai/                 200
```

No `/setpoints` or device-path probe was attempted because it can emit an
operational alert. No runtime was changed, so there is no new-live-delivery
`GREEN at <UTC>, re-verified at <UTC+10>` claim. The stage baseline was
observed, not promoted.

## Required standard fixes

1. Move every common read, trusted-precheck, validation, and build checkout
   consumer from the stale CI App path to the repo-scoped broker/current App
   contract. Audit all uses of `agent-fleet-ci-github-app`; do not add a
   Verdify-specific Secret. Re-render/reconcile centrally, then retrigger each
   exact PR head once.
2. Extend the standard `.agent-fleet/ci.yaml` build schema and helper for
   multi-stage targets and controlled build arguments, then add a complete
   repo-owned Verdify spec. Restore the standard repo-agent Workflow-create
   capability through the fleet registry/broker contract, not a hand-applied
   Role.
3. Bind `Verdify Platform / Argo PR CI` to the approved App, supply a durable
   non-null run URL, and make branch protection fully inspectable to the
   repo-scoped agent. Add correct superseded-run cancellation centrally.
4. Make production promotion create a reviewed digest-only PR instead of
   pushing a pin commit directly to `main`; align the promotable image set and
   repair the current mixed prod pin state before any sync.
5. Correct the centrally rendered `live/platform-main`, GitHub Actions, and
   unavailable-auth claims. Remove the obsolete runtime GitHub-token and GHCR
   references through their owner-approved standard migrations.

Until the checkout identity, standard config schema, submission capability,
and protected-check provenance are repaired, this repository cannot prove the
required source-to-Zot-to-reviewed-pin-to-Argo chain. The unambiguous current
verdict is **`BLOCKED_PLATFORM`**.
