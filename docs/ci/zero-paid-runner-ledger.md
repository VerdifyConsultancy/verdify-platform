# Zero-paid-runner CI ledger — `VerdifyConsultancy/verdify-platform`

**Execution verdict: `REPO_ZERO_PAID_READY`. Publication verdict:
`BLOCKED_PLATFORM`** on the generated GitHub credential/status-provenance
contract (see §11). The two documented execution exceptions and stale-branch
risk remain in §6 and §7.

Initially probed 2026-07-28 and refreshed 2026-07-29. Section 11 supersedes
older dynamic SHA, timing, PR, authentication and live-state observations.
Every claim is backed by a literal probe recorded in §10/§11; re-run those to
re-verify.

GitHub remains the source, issue, PR, review and check authority. No change in
this ledger alters that.

---

## 1. Headline finding

**There is nothing to migrate.** GitHub Actions execution was removed from this
repo on 2026-07-11 (commit `6c7abe1`, operator directive: no external CI
dependency). `.github/workflows/` does not exist on `main` — only
`.github/CODEOWNERS`. The pre-merge gate is `scripts/ci-local.sh`, executed
in-cluster by the `verdify-platform-ci` Argo Workflow (ns `agent-fleet-ci`),
which reports the required check via the commit-status API.

Two premises in the original goal do not hold for this repo, and they change
the work materially:

| Premise | Actual | Consequence |
| --- | --- | --- |
| "active **private**-repository job" | Repo is **public** (`"private": false`) | GitHub-hosted standard runners are **free** here. Paid-runner exposure was already **$0** even before the 2026-07-11 cutover. |
| Jobs exist to move onto ARC profiles | **0** repo-owned workflow files on `main` | The `MIGRATE_ARC` disposition set is **empty**. Zero-paid was reached by *removing* Actions execution, not by re-hosting it. |

Consequently this ledger records dispositions, evidence, measurements and
rollback rather than a migration. No ARC scale sets, namespaces, runner images
or Kubernetes resources were created (all four were out of scope).

## 2. Workflow ledger — dispositions

### 2.1 Live surface (registered with GitHub today)

| Workflow | Path | Trigger | Runner | Disposition | Rationale |
| --- | --- | --- | --- | --- | --- |
| `Dependency Graph` | `dynamic/dependabot/update-graph` | `dynamic` (GitHub-managed) | GitHub-managed, not an Actions runner | **KEEP_FREE_GITHUB_NATIVE** | Synthesized by GitHub, not a repo file. Cannot be redirected to ARC and consumes no Actions minutes. Last run 2026-07-18. |

That is the **entire** registered workflow surface: `actions/workflows` returns
`total_count: 1`.

### 2.2 Retired surface (removed from `main` 2026-07-11)

All eight are gone from `main`. They are listed because they still exist on
stale branches (§7) and their replacement must be traceable.

| Workflow | Old runner | Disposition | Replaced by |
| --- | --- | --- | --- |
| `ci.yml` (**CI** — principal workflow) | `ubuntu-latest` ×8 jobs | **RETIRE** | `scripts/ci-local.sh` via `verdify-platform-ci` Argo Workflow |
| `container-publish.yml` | `ubuntu-latest` | **RETIRE** | `repo-build` Kaniko Workflows → zot origin (ADR-0021) |
| `reusable-container-build.yml` (`workflow_call`) | `ubuntu-latest` ×3 | **RETIRE** | same |
| `cnpg-image.yml` | `ubuntu-latest` | **RETIRE** | same |
| `k8s-manifests.yml` | `ubuntu-latest` | **RETIRE** | `kustomize build overlays/prod` step in `ci-local.sh` |
| `promote-diff-guard.yml` | `ubuntu-latest` ×2 | **RETIRE** | digest-pin review in `docs/runbooks/prod-promotion.md` |
| `prod-promote.yml` | `ubuntu-latest` | **RETIRE** | gated `argocd app sync verdify-prod-dark` (human gate) |
| `lab-content-pipeline.yml` | `ubuntu-latest` ×2 | **RETIRE** | in-cluster lab publisher |

**No workflow carries a `MIGRATE_ARC`, `BLOCKED_PLATFORM` or
`EXPLICIT_EXCEPTION` disposition**, because none survives to be migrated.

## 3. Security floor — current state

Assessed against the repo as it stands, not against a hypothetical migration.

| Control | State | Evidence |
| --- | --- | --- |
| No production / Kubernetes / package-write secrets in validation | **PASS** | `actions/secrets` `total_count: 0`; `actions/variables` `total_count: 0` |
| Build / deployment / release identities separate | **PASS** | Build pushes with `zot-origin-verdifyconsultancy-ci-dockerconfig` (in-cluster, never readable by this cell); workloads pull with the read-only `zot-origin-cluster-pull`; prod sync is a human-gated ArgoCD action |
| No untrusted PR code on privileged runners | **PASS** | No repo-owned workflow executes on any runner |
| Minimal explicit `permissions:` | **N/A today**, enforced on reintroduction | `tests/test_no_hosted_runner_workflows.py` |
| Third-party Actions SHA-pinned | **N/A today**, enforced on reintroduction | same |
| Deployment safety / rollback | Unchanged | ArgoCD `prune:false`, manual sync behind the device-write gate |

Repo Actions policy is currently `enabled: true`, `allowed_actions: "all"`,
`sha_pinning_required: false`. That is permissive, and it is what makes §7
reachable.

## 4. Required check — still effective

`main` branch protection requires exactly one context:

```
Verdify Platform / Argo PR CI   (app_id: null, strict: true)
```

`strict: true` (branch must be current), `required_linear_history: true`,
`allow_force_pushes: false`, `allow_deletions: false`. **0** environments and
**0** rulesets exist, so there are no environment approvals to preserve.

`app_id: null` means the context is posted through the commit-status API by the
in-cluster Argo Events sensor authenticating as `jvallery`, **not** by a GitHub
App with a pinned identity. Any actor holding write on this repo can post a
green `Verdify Platform / Argo PR CI` status. This is a **pre-existing property
of the 2026-07-11 design, not introduced here**, but it is the weakest link in
the check chain and is called out in §7.

## 5. Measurements — before / after

Durations in seconds. Hosted baseline from the GitHub Actions API (600 runs
sampled, 2026-06-23 → 2026-07-18). In-cluster figures from `pending → terminal`
commit-status transitions, i.e. **inclusive of webhook and queue latency**.

### Hosted baseline (retired Actions workflows)

| Workflow | n | p50 | p95 | Conclusions |
| --- | --- | --- | --- | --- |
| **CI** (principal) | 211 | **88** | **110** | 189 success / 22 failure |
| Container Publish | 212 | 15 | 254 | 206 success / 6 cancelled |
| Promote Diff Guard | 85 | 12 | 23 | 85 success |
| K8s Manifests | 79 | 16 | 20 | 79 success |
| Prod Promote | 11 | 28 | 46 | 2 success / 9 failure |

### In-cluster replacement (`Verdify Platform / Argo PR CI`)

Unbiased sample over **60 PR head SHAs** (includes non-merged PRs, so failures
are represented):

| Metric | Value |
| --- | --- |
| Runs measured | 61 status transitions across 60 head SHAs |
| Outcomes | 58 success / 3 failure |
| **Lost or stuck-pending statuses** | **0** |
| p50 | **200** |
| p95 | **882** |
| max | 3606 (a 1-hour step budget expiry) |

A merged-commit-only sample (50 commits on `main`) gives p50 **210**, p95
**739**, 50/50 success — consistent, and biased optimistic, which is why the
PR-head sample above is the one of record.

### Reliability verdict

- **≥20 representative runs: PASS** (60).
- **No lost status: PASS** (0 stuck-pending; every run reached a terminal state).
- **<1% infrastructure failure: NOT MET, and `BLOCKED_PLATFORM`.** 3 of 61
  (4.9%) ended `failure`. All three are terminal, correctly-reported non-green
  results, so **no status was lost** and nothing false-greened. The worst is
  PR #553 at **3606 s** — a step-budget expiry, i.e. an infrastructure-class
  failure, not a test failure. The retained Argo objects corroborate the
  pattern: `verdify-platform-ci-retry-mtlvw` ran ~2 h before failing.

  The cause is the same template-owned overhead measured in §5.1 — runs that
  stall in scheduling or dependency install run out the step budget. It cannot
  be fixed by changing `ci-local.sh`, which completed in 77 s in the original
  2026-07-28 measurement (96.312 s after the 2026-07-29 gate expansion). Fixing the
  budget expiry requires the same `jvallery/agents` template work.

### Performance verdict

- **Principal workflow p95 no worse than hosted baseline: FAIL**, and
  **not closable from this repo** (proof below).
  Separating pure PR validation from runs that also triggered the post-merge
  build (both post the same status context):

  | Sample | n | p50 | p95 | max |
  | --- | --- | --- | --- | --- |
  | PR-only head SHAs (pure `pr-ci`) | 29 | 191 | 796 | 3606 |
  | Head SHAs also on `main` (`pr-ci` + build) | 32 | 206 | 597 | 1463 |

  Both are far above the hosted CI p95 of 110 s.

### 5.1 Where the time actually goes — and who owns it

Node-level timings from `verdify-platform-pr-ci-h47pj` (this ledger's own PR,
2026-07-28), cross-checked against the gate measured directly on a 48-core host.
These rows are the original cutover baseline, not the current gate timing:

| Segment | Time | Owner |
| --- | --- | --- |
| `report-pending` pod | 9 s (+12 s scheduling) | template |
| `trusted-precheck` pod | 7 s (+13 s scheduling) | template |
| `validate` pod | **160 s** — of which **~77 s is `ci-local.sh`** and **~83 s is clone + venv/dependency setup** | split |
| `report` pod | 10 s (+10 s scheduling) | template |
| **Total** | **212 s** | |

Measured directly on 2026-07-28, the whole gate was **~77 s**: ruff <1 s,
schema suite 8 s, device-write gate 2 s, the then-40-entry logic selector 60 s,
migration safety 1 s, Grafana CM check 3 s, solar constants <1 s, twin `g++`
2 s, and overlay render 1 s. The 2026-07-29 source/contract expansion widened
that selector to **45 entries**; the current `scripts/ci-local.sh` completed
green in **96.312 s**. The older number remains here only as the cutover
comparison baseline.

**~135 s of every run is template-owned overhead** — four sequential pods each
paying scheduling and image-pull cost, plus an uncached dependency install.
Even if `ci-local.sh` were reduced to zero, a run could not beat the 110 s
hosted baseline. **The p95 bar is structurally unreachable from this repo.**

The long tail confirms this is infrastructure variance, not gate work:

- **PR #532 changed exactly one file** (a `deploy/` digest pin) and took
  **982 s** — the cheapest possible diff, ~13× the gate's runtime.
- PR #547 (grafana JSON + tests) took 882 s; PR #548 took 1463 s.
- None of the slow runs touched firmware, so the expensive replay-diff gates
  were not even active.

Duration is therefore uncorrelated with what changed. The old hosted `ci.yml`
also ran **8 jobs in parallel**, so its 110 s p95 was the slowest of eight
concurrent jobs; `ci-local.sh` runs every step sequentially in one pod.

Measured breakdown of the 160 s `validate` pod:

| Segment | Time | Verdict |
| --- | --- | --- |
| `apt-get install g++ git curl` | 6 s | **redundant — removed**, see below |
| kustomize download | 1 s | keep |
| `git clone` | 6 s | keep |
| `venv` create | 6 s | keep |
| **`pip install`** | **42 s** | needs a pre-baked image |
| `ci-local.sh` (the gate) | 77 s | keep |

**Optimizations measured and REJECTED rather than assumed:**

- **pip wheel cache** — cold 42 s vs warm-cache 41 s. The cost is unpacking
  wheels into the venv, not downloading them. ~1 s for a shared-volume failure
  mode: not worth it.
- **Consolidating the three `pytest` invocations** — measured *slower*
  (77 s vs 70 s split) on 2026-07-28, so that invocation shape was left
  unchanged. The selected contract set was widened on 2026-07-29.
- **Parallelising independent gate steps** — saves ~15 s of 212 s (~7 %) while
  interleaving failure output on a production gate. Bad trade.

**Optimization measured and SHIPPED:** `python:3.13-bookworm` already ships
`g++`, `git`, `curl` and `tar` (probed against that exact image), so the
`apt-get` was pure waste on every run. Filed as **`jvallery/agents#3181`** —
replaced with a *fail-closed assertion* rather than a plain deletion, because
`ci-local.sh` **skips** the twin `g++` compile when the compiler is absent, so
a silent deletion could have dropped a gate instead of failing.

**The arithmetic that makes this bar unreachable:** even with setup reduced to
zero, four sequential pods cost ~45 s of scheduling plus the current 96.312 s
gate ≈ **141 s** — still above the 110 s hosted p95, before any queue variance. The
only remaining lever is pre-baking dependencies into a CI image and collapsing
the pods, both of which live in `jvallery/agents`.

**Disposition: `BLOCKED_PLATFORM` for the performance and infra-reliability
bars.** The fix belongs to the Argo templates in `jvallery/agents`
(`platform/kubernetes/ci/agent-fleet-ci/workflows/`): collapse the four
sequential pods, pre-bake the CI image with dependencies installed, and cache
the venv. **Filed with this evidence as `jvallery/agents#3173`.**

Nothing in this change causes or worsens the regression; it is inherited from
the 2026-07-11 cutover.

### 5.2 Cutover validation — what was actually exercised

Executed live on this ledger's own PR, 2026-07-28. Every row is an observation,
not a design claim.

| Check | Result |
| --- | --- |
| **Representative success** | 3 runs green on real content (`d5e66b5` 213 s, `287c39a` ~220 s, plus probes below) |
| **Intentional failure** | Injected an `ubuntu-latest` workflow with an unpinned third-party action and no `permissions:` — **all 4 guards fired**; clean again once removed |
| **Exact head SHA** | Status attaches to the head SHA itself, not a synthetic merge commit |
| **Observed runner labels** | `actions/runs?head_sha=…` → `total_count: 0` on **every** commit pushed. Zero GitHub-hosted compute; all execution in ns `agent-fleet-ci` |
| **Runner pod cleanup** | `podGC: {strategy: OnPodSuccess}`, `ttlStrategy: 86400s` after completion / `172800s` after failure. No `verdify-platform-pr-ci` pods remain after any run |
| **Protected environment behavior** | N/A — **0** environments exist (§4), so there are no approvals to exercise |
| **Superseded-job cancellation** | **FAILS — see below** |
| **Timeout** | Observed in the wild: the 3606 s step-budget expiry on PR #553 terminated and reported non-green (no false green, no lost status) |

#### Superseded jobs do NOT cancel

Tested directly: two commits pushed 42 s apart (`2c175da` → `7c6e623`) while
the first run was live.

```
[ 20s] A=pending B=pending | pr-ci-rgscg=Running  pr-ci-tlgbx=Running
[160s] A=success B=pending | pr-ci-rgscg=Running  pr-ci-tlgbx=Running
[180s] A=success B=success | pr-ci-rgscg=Succeeded pr-ci-tlgbx=Succeeded
```

**Both ran to completion concurrently.** The superseded run was never
terminated. Root cause: the `verdify-platform-pr-ci` WorkflowTemplate declares
**no `synchronization` block** (no mutex/semaphore), so nothing supersedes a
prior run for the same ref.

Severity: **resource waste, not a correctness or safety defect.** Commit
statuses are keyed per-SHA, so the superseded run posted to its own old SHA and
the head SHA got its own independent result. Mergeability follows the head SHA.
No false green, no lost status, no cross-contamination. The cost is one wasted
full run per superseded push.

**Disposition: `BLOCKED_PLATFORM` — blocked on exactly one RBAC verb.**

Argo has no native `cancel-in-progress`. A `synchronization.mutex` *serializes*
(the newer run queues behind the older), which is worse for latency, not
supersede. The only correct fix is a `stop-superseded` step that terminates
older Running workflows for the same `{repository}/{head-ref}` — and the CI
identities cannot do that today:

```yaml
# platform/kubernetes/ci/agent-fleet-ci/bootstrap/sensor-rbac.yaml
kind: Role
metadata: {name: argo-ci-events-submit-workflows}
rules:
  - apiGroups: [argoproj.io]
    resources: [workflows, workflowtemplates]
    verbs: [create, get, list, watch]     # <-- no patch / update / delete
```

Adding `patch` alone is sufficient (Argo terminates by patching
`spec.shutdown: Terminate`; no `delete`, no objects destroyed). But
`agent-fleet-ci` is outside this cell's `kubernetes_access` scope and RBAC goes
through the registry, never a hand-authored Role — and these identities are
shared by every repo's CI. Widening them to fix a **pure resource-waste issue
with no correctness impact** is an operator decision. Filed with the exact
proposed diff in `jvallery/agents#3173`.

#### Rollback path — executed, not just documented

§8.2 step 2 was run for real: a `Workflow` submitted directly against
`workflowTemplateRef: verdify-platform-pr-ci`, bypassing the webhook
(`verdify-platform-rollback-proof-trg8g`, ns `agent-fleet-ci`). It reproduces
the gate on the exact head revision with **no GitHub-hosted compute**,
confirming the rollback lands on in-cluster self-hosted execution and **never
on `ubuntu-latest`**.

## 6. Residual free GitHub-native jobs and exceptions

1. **`Dependency Graph` (`dynamic/dependabot/update-graph`)** —
   `KEEP_FREE_GITHUB_NATIVE`. GitHub-synthesized, no repo file, no Actions
   minutes, cannot target a self-hosted runner. Last observed 2026-07-18.
2. **`ghcr.io/verdifyconsultancy/verdify-lab`** — the single remaining GHCR
   pull in `overlays/prod/kustomization.yaml`. A registry exception under
   ADR-0021, *not* a runner exception; it consumes no CI compute. Its source
   repo is archived and it cannot be rebuilt in place.

## 7. Open residual risk — workflow reintroduction via stale branches

**77 of 95 remote branches still contain the retired `ubuntu-latest` workflow
files; 42 of those are outside `archive/`.**

GitHub evaluates `pull_request` workflows from the PR's **merge ref**, not from
the base branch. Because `main` no longer contains `.github/workflows/`, but
those branches do, **opening a PR from any of the 42 would re-activate
`ci.yml`, `container-publish.yml` and friends on `ubuntu-latest`** — restoring
hosted execution, GHCR pushes that ADR-0021 bans, and hosted-runner exposure to
whatever secrets those workflows referenced.

Today this costs nothing (public repo ⇒ free minutes), so it is a **correctness
and supply-chain** risk rather than a billing one. **It becomes a paid-runner
regression the moment this repo is made private.**

Mitigations, in order of strength — the first two are **outside this repo's
autonomy** and need Jason's gate (repo settings / ref deletion):

1. **Repo setting (strongest, gated):** set Actions permissions to
   `disabled`, or `allowed_actions: selected` with an empty allowlist. Kills
   reactivation at the platform, independent of branch contents.
   Caveat: confirm Dependabot's dependency-graph update survives the change.
2. **Branch hygiene (gated):** delete or re-`archive/`-prefix the 42 stale
   non-archive branches carrying workflow files. Ref deletion is destructive
   and is explicitly reserved for Jason under `CLAUDE.md`.
3. **Repo-side guard (landed with this ledger, autonomous):**
   `tests/test_no_hosted_runner_workflows.py`, wired into
   `scripts/ci-local.sh`. It fails the required gate if `.github/workflows/`
   reappears, if any job targets a hosted label, if a workflow omits explicit
   `permissions:`, or if a third-party Action is not SHA-pinned. This does not
   *prevent* a stale-branch PR from spending hosted compute, but it makes the
   reintroduction **non-mergeable**.

## 8. Rollback

### 8.1 Rolling back *this* change

This change adds one guard test, one `ci-local.sh` line, and this document. It
touches no runtime, deployment or firmware path.

```bash
git revert <merge-sha>          # restores ci-local.sh; guard test stops running
```

Pre-change revision: **`417bfe0bf86b04046d3237a7bfd313918b57d96b`** (`main` at
2026-07-28). Tag before cutover if a named anchor is wanted:

```bash
git tag -a ci/pre-zero-paid-ledger 417bfe0 -m "main before zero-paid-runner ledger"
git push origin ci/pre-zero-paid-ledger
```

### 8.2 Zero-paid rollback procedure (if CI must be restored)

The rule is: **rollback must never land on `ubuntu-latest`.** The retired
workflows in §2.2 are *not* a valid rollback target — they are hosted-runner
definitions and restoring them re-introduces exactly what was removed.

Approved rollback order:

1. **Re-run the gate out-of-band (no GitHub compute).** `scripts/ci-local.sh`
   is host-portable by design — any kubectl host or agent pod:
   ```bash
   make ci                       # full gate
   CI_BASE_REF=origin/main make ci   # adds replay-diff + fire-and-forget gates
   ```
   Post the resulting status manually if the required check must be satisfied.
2. **Re-submit the in-cluster workflow directly**, bypassing the webhook.
   **Proven executable — see §5.2** (`verdify-platform-rollback-proof-trg8g`):
   ```bash
   kubectl create -n agent-fleet-ci -f - <<'EOF'
   apiVersion: argoproj.io/v1alpha1
   kind: Workflow
   metadata:
     generateName: verdify-platform-rollback-proof-
     namespace: agent-fleet-ci
   spec:
     workflowTemplateRef: {name: verdify-platform-pr-ci}
     arguments:
       parameters:
         - {name: event-action,    value: synchronize}
         - {name: repository,      value: VerdifyConsultancy/verdify-platform}
         - {name: base-repository, value: VerdifyConsultancy/verdify-platform}
         - {name: head-repository, value: VerdifyConsultancy/verdify-platform}
         - {name: base-ref,        value: main}
         - {name: head-ref,        value: <branch>}
         - {name: base-revision,   value: <base-sha>}
         - {name: head-revision,   value: <head-sha>}
   EOF
   ```
   (Template is owned by `jvallery/agents`; see
   `platform/kubernetes/ci/agent-fleet-ci/workflows/`.)

**Rollback target: MET.** Steps 1 and 2 both land on in-cluster self-hosted
execution and consume zero GitHub-hosted compute — verified, not asserted.
`ubuntu-latest` is never a rollback destination.

Explicitly **not** an approved path: restoring any workflow from §2.2. Those
are hosted-runner definitions; reinstating one re-introduces exactly what was
removed, and `tests/test_no_hosted_runner_workflows.py` will fail the gate if
one lands. If the cluster is ever unavailable *and* a merge cannot wait, step 1
is the fallback — it needs nothing but a shell. A hosted workflow bearing a
self-hosted/ARC label would require Jason's sign-off, a matching ledger update,
and an ARC profile that **does not exist for this repo today** (§9), so that
route is **BLOCKED_PLATFORM** and is not needed: step 1 covers the case.

## 9. What could not be verified from this cell

Stated plainly rather than assumed:

- **ARC runner profiles.** ARC *is* installed cluster-wide (CRDs
  `autoscalingrunnersets`, `ephemeralrunners`, `autoscalinglisteners` in
  `actions.github.com/v1alpha1`), but this cell's RBAC denies `list` on
  `autoscalingrunnersets` in every reachable namespace, and
  `orgs/.../actions/runners` returns 403 (needs `admin:org`). **No approved ARC
  profile is registered to this repo** — `repos/.../actions/runners` returns
  `total_count: 0` — so profile selection ("unprivileged validation",
  "browser/E2E", "trusted container build", …) could not be exercised and is
  moot while the migration set is empty.
- **Actions billing endpoints** return 404 for this token scope. The zero-paid
  claim rests on the stronger structural facts instead: repo is public, and
  zero repo-owned workflows execute.
- **Argo Events sensor/eventsource definitions** — `list` denied in
  `agent-fleet-ci`; concurrency-cancellation behaviour for superseded SHAs
  therefore could not be inspected at the source. Observationally, 0 of 60
  runs were left stuck pending. Those templates are owned by `jvallery/agents`.

## 10. Probe log (re-runnable)

```bash
# Run from an activated Agent Fleet repo pod. Invoke the runtime `gh` from
# PATH directly; it resolves a rotating credential for each operation.
R=VerdifyConsultancy/verdify-platform

gh api repos/$R -q '{private,visibility,default_branch}'
gh api repos/$R/actions/workflows -q '.total_count, (.workflows[]|{name,path,state})'
gh api repos/$R/actions/runners   -q '.total_count'        # 0
gh api repos/$R/actions/permissions                        # enabled/all/no-sha-pinning
gh api repos/$R/environments      -q '.total_count'        # 0
gh api repos/$R/actions/secrets   -q '.total_count'        # 0
gh api repos/$R/actions/variables -q '.total_count'        # 0
gh api repos/$R/rulesets                                   # []
gh api repos/$R/branches/main/protection
gh api repos/$R/hooks -q '.[]|{url:.config.url,events}'
git ls-tree -r --name-only origin/main -- .github/         # CODEOWNERS only

# stale-branch reactivation surface (§7)
git branch -r --format='%(refname:short)' | grep -v HEAD | while read b; do
  n=$(git ls-tree -r --name-only "$b" -- .github/workflows/ | wc -l)
  [ "$n" -gt 0 ] && echo "$b ($n)"
done

# in-cluster CI evidence
kubectl get workflows -n agent-fleet-ci | grep verdify-platform
kubectl api-resources | grep actions.github            # ARC CRDs present
```

## 11. 2026-07-29 closeout refresh

Evidence timestamp: **2026-07-29T06:45:02Z**. This refresh was read-only with
respect to GitHub, Argo, Kubernetes workloads, the device, and production data.

### Exact git state

- Refreshed public default branch:
  `eb3ac9cf33001fa6c5271412284382b36c6abf9e` (PR #556 merge).
- Clean local branch: `auth/uniform-github-app-7570d1c`.
- Repository-owned validation fix:
  `60740ba2dbaa24600f49c0f35c394b360ee0a602`.
- The requested auth work remains preserved as ancestor
  `eb9bfbb7735d3e418672aa7b2b6957657a1d3728`; PR #555's merged lane was not
  modified or reopened.
- The branch is seven commits ahead of refreshed `origin/main` (the auth work,
  three deliberate main reconciliations, its contract-doc follow-up, the
  validation fix, and this evidence follow-up). `git status --porcelain=v1` is
  empty; the machine-managed runtime briefing remains local behind the existing `CLAUDE.md`/
  `AGENTS.md` skip-worktree contract and was not committed.

### Load-bearing execution classification

| Class | Current Verdify surface | Closeout disposition |
| --- | --- | --- |
| Generated ARC validation | None registered for this repo; repository runner inventory is zero and runner-profile RBAC is not granted to this cell | **NONE TO MIGRATE**; do not invent a profile or runner label |
| Argo validation | `verdify-platform-pr-ci` posts stable context `Verdify Platform / Argo PR CI` | **KEEP / REQUIRED**, but publication provenance is centrally blocked below |
| Argo build/publish | `verdify-platform-ci` post-merge validation and `repo-build` Kaniko jobs publish exact revisions to Zot; digest-only pin PR follows | **KEEP ZERO-PAID** |
| Dispatch-only | Generated GitHub event/sensor dispatch into the Argo templates | **KEEP PLATFORM-OWNED**; definitions are RBAC-hidden from this cell |
| Retired | Eight historical `ubuntu-latest` workflows in §2.2 | **REMAIN RETIRED**; repo guard prevents reintroduction |
| GitHub-native | Dynamic Dependency Graph | **KEEP FREE GITHUB-NATIVE**, non-load-bearing |

### Exact local and live validation

- `make lint`: green.
- `make test`: **1170 passed, 154 deselected, 1 strict xfail** (#382 gated
  ingestor PVC recovery), 85.19 s pytest / 88 s wall.
- `make ci`: **ALL GATES GREEN**, 96.312 s measured directly; the pure-contract
  selector has 45 entries. This run did not request diff-mode gates.
- Read-only schema live lane after the source fix: **164 passed, 560
  deselected**, 52.46 s. It proved that the production-persisted historical
  `wet_taper` fog-block reason now decodes through the strict schema.
- Broad `make test-live` before that one-line schema correction: **280 passed,
  3 explicitly owned-elsewhere skips, 2 failed, 1764 deselected** in 365.91 s.
  The schema failure is closed by the recheck above. The sole remaining live
  failure is **148 active planner lessons** (limit 25), which is current-prod
  rollout drift covered by existing migration 156, not an unmerged-branch
  source failure.
- `make test-container` was explicitly probed without Docker and failed closed
  rather than reporting an unexecuted green. Writable and device-live lanes
  were not run: no disposable writable DSN was supplied, and the device path
  remains operator-gated.

### Publication and status-provenance blocker

The generated Agent Fleet GitHub credential contract is unavailable in this
pod: no generated token-file environment/wrapper is present. Legacy ambient
`GH_TOKEN`/`GITHUB_TOKEN`, PATs, `GIT_ASKPASS`, handwritten Secrets, and
repo-local credential fallbacks are intentionally rejected. Central issue
[`jvallery/agents#3189`](https://github.com/jvallery/agents/issues/3189) owns
that contract failure. Consequently this cell cannot push `60740ba`, create or
update its PR/issue, request the required independent GitHub review, or publish
an exact-head status.

The last public inventory found one open PR, #553 (`ci/pin-base-image-digests`,
head `aaf8687…`), with a stale infrastructure timeout tracked centrally by
[`jvallery/agents#3049`](https://github.com/jvallery/agents/issues/3049). PR
#556 is merged at `eb3ac9c…`; its exact-head Argo status succeeded, but the
commit-status `target_url` is null. The required context is stable by name but
still has `app_id: null`; durable run/job/log target URLs and publisher
identity must come from the common generated platform contract, never a
Verdify-local status publisher. No verified central issue currently owns that
specific provenance defect. Filing or updating its exact `jvallery/agents`
issue is itself blocked by #3189; the first authorized GitHub write must attach
the SHA/status evidence above to #3189 or create the dedicated platform issue.
The retained #553 Workflow is `verdify-platform-pr-ci-lp8z4`, exact head
`aaf8687be75cb2e4223356be7994157e42ddc763`; its `validate` Pod failed after
3606 seconds with `Pod was active on the node longer than the specified
deadline`. The Pod has since been garbage-collected, while the Workflow and
terminal failure status remain. Because the publisher supplied no target URL,
the Workflow name plus retained object is the only durable run reference.

No direct workflow submission, production sync/restart, image push, digest pin,
database mutation, or device request was performed. After publication and
merge, the authorized delivery proof remains: Argo validation → post-merge
Kaniko builds → Zot digests → reviewed digest-pin PR → Jason-gated
`verdify-prod-dark` reconciliation → live digest/health proof. This change
requires reviewed image pins for `verdify-api`, `verdify-ingestor`, and
`verdify-lab-publisher-k3s`, the generated Grafana ConfigMap, and the explicitly
documented `verdify-mcp` plus Jason-gated single-writer `verdify-ingestor`
restart. The production lesson cleanup is part of that gated reconciliation;
it must not be applied directly from this cell.

### Shared T+65 read-only durability proof

Re-probed at **2026-07-29T07:01:32Z–07:03:00Z**, more than seven hours after
PR #555 merged and more than six hours after its generated digest-pin PR #556
merged. The public default branch remained
`eb3ac9cf33001fa6c5271412284382b36c6abf9e`; #555 and #556 remained merged,
and their exact-head `Verdify Platform / Argo PR CI` statuses remained
successful. Their validation/post-merge workflows
`verdify-platform-pr-ci-kk8tk`, `verdify-platform-ci-ft8l9`,
`verdify-platform-pr-ci-twxtq`, and `verdify-platform-ci-trmfj` all remained
`Succeeded`, with no active Verdify workflow.

The production core remained available: both API and MCP replicas, the single
ingestor, planner, setpoint server, database, Grafana, and both lab replicas
were `Running` and ready; the ingestor still had zero restarts. This is a
durability/health observation, **not** a deployment claim. The reviewed prod
overlay contains the newer #555 Zot digests (`verdify-api` `d28c45ce…`,
`verdify-mcp` `e3f37fc9…`, `verdify-ingestor` `08338624…`, and planner
`735cb005…`), while the running Deployments still use the older gated digests
(`984ba493…`, `8cc8d747…`, `fcd13ad7…`, and `ff7de32d…`). The service account
cannot read the `verdify-prod-dark` Argo Application, so `Synced + Healthy`
cannot be asserted; the digest mismatch independently proves that reviewed
desired state has not been reconciled. No sync or workload mutation was
attempted.

At the same probe, #553 remained the only open PR with its unchanged central
Argo timeout failure, status target URLs remained null, the generated GitHub
token-file/wrapper contract remained absent, all 15 shared Git worktrees were
clean, and no task-prefixed `/tmp/verdify-*` artifacts remained.

---

Related: `docs/runbooks/prod-promotion.md` (digest-pin + gated sync),
`docs/runbooks/laptop-operator.md` (host-portable dev loop),
`scripts/ci-local.sh` (the gate itself).
