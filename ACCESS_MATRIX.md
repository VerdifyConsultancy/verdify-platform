# Verdify Platform Access Matrix

Last updated: 2026-06-13

Agent name: `verdify-platform`

This matrix follows the least-privilege lane model. It lists access by scope and
does not include raw secret values.

| Resource | Current access | Required access | Scope | Owner | Status |
|---|---|---|---|---|---|
| `VerdifyConsultancy/verdify-platform` checkout | Local read/write | Read/write repo files | Single repo | Verdify repo admins | Granted locally |
| GitHub issues/PRs | Auth via local Git credential helper for `jvallery`; connector available for issue writes | Repo-scoped issue/PR read/write | Single repo | Verdify repo admins | Available |
| GitHub Project Board | Project scope token available; exact `Agent Command Center Kanban` board not visible | Owner/project number or board visibility | Project only | Board owner | Gap |
| GitHub Actions | Workflow files local; `gh` dispatch possible with token | Dispatch only when task calls for it | Single repo | Repo admins | Available/gated |
| GHCR app packages | CI publishes; manifests reference digest pins | Package read/write through CI | Verdify packages | Repo/org admins | CI-owned |
| `verdify-dev` namespace | Repo manifests; no live write used here | Read for diagnostics; GitOps for durable changes | Namespace | Platform/GitOps owners | Read not verified this pass |
| `verdify-prod` namespace | Repo manifests; no live write used here | Read for diagnostics; writes Jason-gated through GitOps | Namespace | Jason + Platform/GitOps owners | Gated |
| ArgoCD apps | App YAML in repo | Read app health; sync prod only with Jason approval | `verdify-dev`, `verdify-prod-dark` | Platform/GitOps owners | Gated |
| Kubernetes Secrets | Names/key contracts only | Metadata/status only; no values | Namespace-local | Secret-delivery owner | Values out of scope |
| SOPS/Age private key | No access requested | No access | None | Secret-delivery owner | Out of scope |
| StorageClass/PV/NAS/Longhorn | Manifest references only | Coordination request | Storage resources | `storage-infra` | Out of scope |
| DNS/Cloudflare/Ingress controller/device VLAN | Manifest references only | Coordination request | Routes/network | `network-infra` + Jason | Out of scope |
| Shared monitoring/logging | App dashboard manifests only | Coordination request | Shared platform | `monitoring-stack` | Out of scope |
| GPU/AI runtime | No direct dependency found in this pass | Coordination request only if needed | Shared AI compute | `cortex-ai-compute` | Out of scope |
| ESP32 firmware OTA/device write | Source/validation only | Jason-approved operation only | Live greenhouse device | Jason | Hard gated |

## Secrets Policy

Never print, paste, commit, log, or summarize raw tokens, passwords, API keys,
client secrets, private keys, or decrypted secret files. Reference Secret names,
keys, auth modes, and credential locations only.
