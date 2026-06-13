# Verdify Platform ArgoCD

Last updated: 2026-06-13

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

## Applications

| App | Namespace | Source path | Sync policy | Notes |
|---|---|---|---|---|
| `verdify-dev` | `verdify-dev` | `deploy/k8s/overlays/dev` | automated self-heal/prune | Device-dark proving environment; prod copy restored nightly. |
| `verdify-prod-dark` | `verdify-prod` | `deploy/k8s/overlays/prod` | manual | Legacy live app name; currently the real production writer. |
| `verdify-prod` | `verdify-prod` | `deploy/k8s/overlays/prod` | manual | Intended rename target; apply only through gated orphan/readopt procedure. |

Application manifests live in `deploy/k8s/argocd/apps/`. The active app CR
placement and AppProject definition also depend on the fleet GitOps source of
truth outside this repo; see `docs/runbooks/laptop-operator.md`.

## Owned Desired State

- Base app workloads and policies: `deploy/k8s/base/`.
- Optional app components: `deploy/k8s/components/`.
- Dev/prod overlays: `deploy/k8s/overlays/dev` and
  `deploy/k8s/overlays/prod`.
- Secret names and key contracts: `deploy/k8s/SECRETS.md` plus placeholder
  manifests marked local-config.
- CNPG/dev and migration runbooks: `deploy/k8s/cnpg/`.

## Out Of Scope

- Cluster-wide ArgoCD installation, AppProject policy, CRDs, CNI, CSI,
  StorageClasses, shared ingress controllers, Cloudflare tunnel config, and
  shared monitoring.
- Secret value sealing/decryption and age key custody.
- Prod sync execution without Jason approval.

## Verification

- Render app manifests before PRs that touch desired state:
  `kustomize build deploy/k8s/overlays/dev` and
  `kustomize build deploy/k8s/overlays/prod`.
- CI gate: `.github/workflows/k8s-manifests.yml` renders and validates overlays
  with kubeconform.
- Prod promotion must use `.github/workflows/prod-promote.yml` and
  `.github/workflows/promote-diff-guard.yml`; merge changes Git only. The manual
  prod sync remains Jason-gated.
