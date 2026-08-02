# Verdify Platform ArgoCD

Last updated: 2026-07-14

Agent name: `verdify-platform`

## Enforcement Rule

All Kubernetes desired state owned by this lane must be represented in Git and
reconciled by ArgoCD. Do not make durable changes with direct
`kubectl apply/edit/patch` except emergency rollback or read-only diagnostics.
Any exception must be documented in the owning issue.

Every workload, namespace, secret reference, ingress, PVC, RBAC, and config
change must trace to:

- repo file or PR,
- ArgoCD Application sync/health evidence,
- issue or `## Project Tracking` block,
- verification command or runbook evidence.

## Environment Model

Verdify is single-env as of 2026-06-16.

- Prod namespace: `verdify-prod`.
- Live ArgoCD app: `verdify-prod-dark` (legacy name).
- Desired state: `deploy/k8s/overlays/prod`.
- Sync model: manual, behind the device-write gate.
- Canonical branch: `main`.

`verdify-dev`, staging, and `live/platform-main` are retired/deleted. Do not add
new work under deleted dev/staging overlays or revive the old branch model.
The isolated `verdify-platform-lab-stage` web canary is not a second
greenhouse/data-plane environment: it has no device writer, database, or Track
A authority and exists only for the Lab Astro migration.

## Applications

| App | Namespace | Source path | Sync policy | Notes |
|---|---|---|---|---|
| `verdify-prod-dark` | `verdify-prod` | `deploy/k8s/overlays/prod` | Manual | Legacy live app name; currently the real production writer. Sync requires Jason. |
| `verdify-prod` | `verdify-prod` | `deploy/k8s/overlays/prod` | Manual | Intended rename target only. Apply through a gated orphan/readopt procedure if ever scheduled. |
| `verdify-platform-lab-stage` | `verdify-platform` | `deploy/k8s/overlays/lab-stage` | Manual | Fleet-owned isolated Astro canary. This repo owns the rendered overlay; `jvallery/agents` owns the Application/AppProject. `prune:false`, `selfHeal:false`; explicit exact-revision actuation plus T0/T+10 proof. |

Production Application manifests live in `deploy/k8s/argocd/apps/`. The Lab
stage Application and AppProject do **not**: their fleet GitOps sources of truth
are respectively
`jvallery/agents/platform/gitops/applications/local-staging/verdify-platform-lab-stage.yaml`
and
`jvallery/agents/platform/gitops/projects/verdify-platform-lab-stage.yaml`.
This repo owns only their rendered source path,
`deploy/k8s/overlays/lab-stage`. See `docs/runbooks/laptop-operator.md`.

## Owned Desired State

- Base app workloads and policies: `deploy/k8s/base/`.
- Optional app components: `deploy/k8s/components/`.
- Prod overlay: `deploy/k8s/overlays/prod`.
- Secret names and key contracts: `deploy/k8s/SECRETS.md` plus placeholder
  manifests marked local-config.
- CNPG/migration runbooks and experimental manifests: `deploy/k8s/cnpg/`.

## Out Of Scope

- Cluster-wide ArgoCD installation, AppProject policy, CRDs, CNI, CSI,
  StorageClasses, shared ingress controllers, Cloudflare tunnel config, and
  shared monitoring.
- Secret value sealing/decryption and age key custody.
- Prod sync execution without Jason approval.

## Promotion Model

GitHub Actions no longer publishes this repo. A fleet Argo Event is intended to
submit the exact `main` revision through `verdify-platform-ci` to
`repo-build/build` in `agent-fleet-ci`; Kaniko creates a no-push image archive
and pinned Crane publishes it to the in-cluster Zot origin. The bespoke caller
declares the correct owner-scoped Zot binding, but source checkout is currently
`BLOCKED_PLATFORM`, so a new exact-SHA digest has not been proved. The live prod
pin step also pushes directly to `main` instead of opening the required reviewed
digest-only PR; only the Lab-stage path currently opens a pin PR. See
`docs/ci/fleet-cicd-convergence-2026-08-02.md` and
`docs/runbooks/prod-promotion.md` for the exact evidence and standard fix.

Merging a pin changes Git only. `verdify-platform-lab-stage` requires an
explicit reviewed exact-revision actuator and T0/T+10 durability proof. For
each Lab pass, record one immutable Platform commit and image pin-set, its
complete rendered inventory, the previous known-good rollback commit/trigger,
and the reviewed `jvallery/agents` actuator commit. The actuator temporarily
sets the fleet-owned Application annotation/`targetRevision` to that immutable
commit; verify the live AppProject admits every rendered kind and require Argo
`Synced` + `Healthy` with no unexpected resources. After T+10, a separate
reviewed fleet PR restores `targetRevision: main`, autosync disabled,
`prune:false`, and `selfHeal:false`. This is the proven #2998/#2999 pattern;
never sync moving `main` directly for an activation pass. Production remains a
separate Jason-gated `argocd app sync verdify-prod-dark`; no image build or pin
authorizes that sync.

## Verification

- Render prod manifests before PRs that touch desired state:
  `kustomize build deploy/k8s/overlays/prod`.
- CI gate: local `make ci` plus the exact-head in-cluster PR status; the latter
  is currently blocked before checkout and must not be bypassed.
- Promotion must use exact Zot digests and the reviewed pin workflow described
  in `docs/runbooks/prod-promotion.md`; merge changes Git only. The manual prod
  sync remains Jason-gated.
