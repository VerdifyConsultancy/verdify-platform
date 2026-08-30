# Verdify Platform ArgoCD

Last updated: 2026-08-30

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

`verdify-dev`, staging, `live/platform-main`, and the former isolated Lab canary
are retired/deleted. Do not add work under deleted dev/staging overlays or
revive the old branch model.

## Applications

| App | Namespace | Source path | Sync policy | Notes |
|---|---|---|---|---|
| `verdify-prod-dark` | `verdify-prod` | `deploy/k8s/overlays/prod` | Manual | Legacy live app name; currently the real production writer. Sync uses the release preflight and rollback checks. |
| `verdify-prod` | `verdify-prod` | `deploy/k8s/overlays/prod` | Manual | Intended rename target only. Apply through the orphan/readopt runbook if scheduled. |

Production Application manifests live in `deploy/k8s/argocd/apps/`. The former
`verdify-platform-lab-stage` Application/AppProject and its source overlay were
retired with the abandoned alternate Lab generator on 2026-08-30.

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
digest-pin changes. The Lab publisher image follows this model; the Lab web
runtime uses a pinned content-free nginx image and serves only the validated
Quartz cache PVC. Merging a pin changes Git only. Production remains a separate
`argocd app sync verdify-prod-dark`; no image build or pin authorizes that sync.

## Verification

- Render prod manifests before PRs that touch desired state:
  `kustomize build deploy/k8s/overlays/prod`.
- CI gate: `make ci` plus the in-cluster repo-build/PR-CI render and policy
  checks.
- Promotion must use exact Zot digests and the validated pin workflow described
  in `docs/runbooks/prod-promotion.md`; merge changes Git only. The manual prod
  sync remains safety-checked.
