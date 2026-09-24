# Verdify Platform Access Matrix

> Technical capability inventory only. This file records observed access and
> tool surfaces; it does not grant execution authority, require approval, or
> define workflow prerequisites. Current credentials and live probes determine
> available mechanics; root `AGENTS.md` and the user's request govern work.

Last updated: 2026-09-23

Agent name: `verdify-platform`

This matrix follows the least-privilege lane model. It lists access by scope and
does not include raw secret values.

| Resource | Current access | Required access | Scope | Owner | Status |
|---|---|---|---|---|---|
| `VerdifyConsultancy/verdify-platform` checkout | Local read/write | Read/write repo files | Single repo | Verdify repo admins | Granted locally |
| GitHub issues/PRs | Repo pods: GitHub App installation token; operator Mac: `gh` keyring login | Repo-scoped issue/PR read/write | Single repo | Verdify repo admins | Available |
| In-cluster CI/publishing | Argo Events/Workflows + Kaniko; exact revisions publish to Zot | Submit/observe validated repo-build workflows; no GitHub Actions publishing | `agent-fleet-ci` / repo scope | Agent Fleet + repo owners | Available through the fixed pipeline |
| Zot application images | `registry.vallery.net` digest pins; in-cluster origin push | Resolve/pin immutable digests through CI | Verdify image namespace | Agent Fleet / registry owners | CI-owned |
| GHCR holdovers | Read-only legacy references only | Never publish new images; the Lab serving runtime is content-free and the publisher image is pinned from Zot | Legacy images | Repo/Jason | Retirement in progress |
| `verdify-prod` namespace | Repo manifests; no live write used in this docs pass | Read for diagnostics; writes use GitOps preflight and rollback | Namespace | Platform/GitOps owners | Available with safeguards |
| ArgoCD apps | App YAML in repo | Read app health; sync prod with exact-target preflight and rollback | `verdify-prod-dark` | Platform/GitOps owners | Available |
| Kubernetes Secrets | Names/key contracts only | Metadata/status only; no values | Namespace-local | Secret-delivery owner | Values out of scope |
| SOPS/Age private key | No access requested | No access | None | Secret-delivery owner | Out of scope |
| StorageClass/PV/NAS/Longhorn | Manifest references only | Coordination request | Storage resources | `storage-infra` | Out of scope |
| DNS/Cloudflare/Ingress controller/device VLAN | Manifest references only | Coordination request | Routes/network | `network-infra` + Jason | Out of scope |
| Home Assistant / Frigate / Lutron | App references and issue tracking only | Contract validation and named integration path | Greenhouse integrations | Integration owners | Requires live integration evidence |
| Shared monitoring/logging | App dashboard manifests only | Coordination request | Shared platform | `monitoring-stack` | Out of scope |
| ESP32 firmware OTA/device write | Source/validation plus scoped runtime access | Run preflight, retain last-good rollback, flash once, and verify | Live greenhouse device | Verdify executor | Available with safeguards |

## GitHub Credential Notes

- Repo pods use the GitHub App installation token that the pod runtime
  provides; the operator Mac uses its `gh` keyring login.
- Planning lives in `planning/backlog.yaml` (rendered into `PROJECT_BOARD.md`)
  and GitHub milestones; there is no GitHub Project board.
- Do not replace either credential with alternate token files; use the existing
  scoped credential mechanism.

## Secrets Policy

Never print, paste, commit, log, or summarize raw tokens, passwords, API keys,
client secrets, private keys, or decrypted secret files. Reference Secret names,
keys, auth modes, and credential locations only.

## Deleted Environment Note

`verdify-dev` and staging are decommissioned/deleted. Any access request that
mentions them should be revalidated against `AGENTS.md` and
`docs/runbooks/laptop-operator.md` before action.
