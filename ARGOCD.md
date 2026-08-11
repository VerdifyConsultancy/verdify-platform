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
| `verdify-prod-dark` | `verdify-prod` | `deploy/k8s/overlays/prod` | Manual | Legacy live app name; currently the real production writer. Sync uses the release preflight and rollback checks. |
| `verdify-prod` | `verdify-prod` | `deploy/k8s/overlays/prod` | Manual | Intended rename target only. Apply through the orphan/readopt runbook if scheduled. |
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
- Prod sync execution without recorded task scope.

## Promotion Model

GitHub Actions no longer publishes this repo. A fleet Argo Event submits the
exact `main` revision to the `repo-build` WorkflowTemplate in
`agent-fleet-ci`; Kaniko pushes to the in-cluster Zot origin and the resulting
`registry.vallery.net/...@sha256:` identities are committed through validated
digest-pin changes. Stage and production overlay pins are distinct.

Merging a pin changes Git only. `verdify-platform-lab-stage` uses an explicit
exact-revision actuator and T0/T+10 durability proof. For
each Lab pass, record one immutable Platform commit and image pin-set, its
complete rendered inventory, the previous known-good rollback commit/trigger,
and the validated `jvallery/agents` actuator commit. The actuator temporarily
sets the fleet-owned Application annotation/`targetRevision` to that immutable
commit; verify the live AppProject admits every rendered kind and require Argo
`Synced` + `Healthy` with no unexpected resources. After T+10, a separate
validated fleet PR restores `targetRevision: main`, autosync disabled,
`prune:false`, and `selfHeal:false`. This is the proven #2998/#2999 pattern;
never sync moving `main` directly for an activation pass. Production remains a
separate safety-checked `argocd app sync verdify-prod-dark`; no image build or pin
authorizes that sync.

## Verification

- Render prod manifests before PRs that touch desired state:
  `kustomize build deploy/k8s/overlays/prod`.
- CI gate: `make ci` plus the in-cluster repo-build/PR-CI render and policy
  checks.
- Promotion must use exact Zot digests and the validated pin workflow described
  in `docs/runbooks/prod-promotion.md`; merge changes Git only. The manual prod
  sync remains safety-checked.
