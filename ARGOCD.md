# Verdify Platform ArgoCD

Last updated: 2026-06-16

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

## Applications

| App | Namespace | Source path | Sync policy | Notes |
|---|---|---|---|---|
| `verdify-prod-dark` | `verdify-prod` | `deploy/k8s/overlays/prod` | Manual | Legacy live app name; currently the real production writer. Sync requires Jason. |
| `verdify-prod` | `verdify-prod` | `deploy/k8s/overlays/prod` | Manual | Intended rename target only. Apply through a gated orphan/readopt procedure if ever scheduled. |

Application manifests live in `deploy/k8s/argocd/apps/`. The active app CR
placement and AppProject definition also depend on the fleet GitOps source of
truth outside this repo; see `docs/runbooks/laptop-operator.md`.

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

Every push to `main` publishes impacted images to GHCR with immutable
`sha-<sha>` and mutable `branch-main` tags. Prod is advanced by
`.github/workflows/prod-promote.yml`, which resolves the `branch-main` digests,
bumps `deploy/k8s/overlays/prod`, and opens a `prod-promote` PR.

`.github/workflows/promote-diff-guard.yml` enforces the digest-only change
surface. Merge changes Git only. The live sync remains a separate Jason-gated
operator action.

## Verification

- Render prod manifests before PRs that touch desired state:
  `kustomize build deploy/k8s/overlays/prod`.
- CI gate: `.github/workflows/k8s-manifests.yml` renders and validates overlays
  with kubeconform.
- Prod promotion must use `.github/workflows/prod-promote.yml` and
  `.github/workflows/promote-diff-guard.yml`; merge changes Git only. The manual
  prod sync remains Jason-gated.
