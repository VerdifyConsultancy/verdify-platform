# Verdify Platform Access Matrix

Last updated: 2026-06-16

Agent name: `verdify-platform`

This matrix follows the least-privilege lane model. It lists access by scope and
does not include raw secret values.

| Resource | Current access | Required access | Scope | Owner | Status |
|---|---|---|---|---|---|
| `VerdifyConsultancy/verdify-platform` checkout | Local read/write | Read/write repo files | Single repo | Verdify repo admins | Granted locally |
| GitHub issues/PRs | Auth via local `gh` as `jvallery` | Repo-scoped issue/PR read/write | Single repo | Verdify repo admins | Available |
| GitHub Project Board | Project scope available through local `gh`; project #5 updated with #343-#352 | Maintain lane board and epic cards | Project only | Verdify org/project admins | Available |
| GitHub Actions | Workflow files local; `gh` dispatch possible with token | Dispatch only when task calls for it | Single repo | Repo admins | Available/gated |
| GHCR app packages | CI publishes; manifests reference digest pins | Package read/write through CI | Verdify packages | Repo/org admins | CI-owned |
| `verdify-prod` namespace | Repo manifests; no live write used in this docs pass | Read for diagnostics; writes Jason-gated through GitOps | Namespace | Jason + Platform/GitOps owners | Gated |
| ArgoCD apps | App YAML in repo | Read app health; sync prod only with Jason approval | `verdify-prod-dark` | Platform/GitOps owners | Gated |
| Kubernetes Secrets | Names/key contracts only | Metadata/status only; no values | Namespace-local | Secret-delivery owner | Values out of scope |
| SOPS/Age private key | No access requested | No access | None | Secret-delivery owner | Out of scope |
| StorageClass/PV/NAS/Longhorn | Manifest references only | Coordination request | Storage resources | `storage-infra` | Out of scope |
| DNS/Cloudflare/Ingress controller/device VLAN | Manifest references only | Coordination request | Routes/network | `network-infra` + Jason | Out of scope |
| Home Assistant / Frigate / Lutron | App references and issue tracking only | Contract review and named integration path | Greenhouse integrations | Jason / integration owners | Needs L7 review |
| Shared monitoring/logging | App dashboard manifests only | Coordination request | Shared platform | `monitoring-stack` | Out of scope |
| S3 lab content store | Secret names/key contracts only | Metadata/status and publisher contract | Lab notebook publishing | Secret-delivery owner / web lane | Needs L9 review |
| ESP32 firmware OTA/device write | Source/validation only | Jason-approved operation only | Live greenhouse device | Jason | Hard gated |

## GitHub Credential Notes

- Local `gh` is authenticated as `jvallery` through the macOS keyring and has
  repo/project/workflow scopes sufficient for issue, milestone, and Project
  Board maintenance.
- The 2026-06-16 replan created GitHub milestones G0-G3, issues #343-#352, and
  Project #5 metadata without printing raw token values.
- Do not replace the keyring token with alternate token files unless Jason
  explicitly asks for credential maintenance.

## Secrets Policy

Never print, paste, commit, log, or summarize raw tokens, passwords, API keys,
client secrets, private keys, or decrypted secret files. Reference Secret names,
keys, auth modes, and credential locations only.

## Deleted Environment Note

`verdify-dev` and staging are decommissioned/deleted. Any access request that
mentions them should be revalidated against `AGENTS.md` and
`docs/runbooks/laptop-operator.md` before action.
