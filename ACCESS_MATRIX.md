# Verdify Platform Access Matrix

Last updated: 2026-07-29

Agent name: `verdify-platform`

This matrix follows the least-privilege lane model. It lists access by scope and
does not include raw secret values.

| Resource | Current access | Required access | Scope | Owner | Status |
|---|---|---|---|---|---|
| `VerdifyConsultancy/verdify-platform` checkout | Public HTTPS reads work; authenticated Git still has legacy runtime plumbing | Repo-scoped contents read/write through the rotating runtime askpass | Single repo | Agent Fleet + Verdify repo admins | Declared centrally, not activated; writes blocked |
| GitHub issues/PRs/statuses/workflows | Current pod has stock `gh`, not the rotating runtime wrapper | Operations allowed by the central `repo-agent-standard` profile | Single repo | Agent Fleet + Verdify repo admins | Declared centrally, not activated; writes blocked |
| GitHub Project Board | Not included in the repository-scoped standard profile | Org Project mutation only through a separately authorized operator/control-plane path | Org project #5 | Verdify org/project admins | Outside standard profile; report as a blocker |
| In-cluster CI/publishing | Argo Events/Workflows + Kaniko; exact revisions publish to Zot | Submit/observe reviewed repo-build workflows; no GitHub Actions publishing | `agent-fleet-ci` / repo scope | Agent Fleet + repo owners | Available through the fixed pipeline |
| Zot application images | `registry.vallery.net` digest pins; in-cluster origin push | Resolve/pin immutable digests through CI | Verdify image namespace | Agent Fleet / registry owners | CI-owned |
| GHCR holdovers | Read-only legacy production references only | Retire through the gated migration; never publish new images | Legacy Quartz runtime | Repo/Jason | Retirement pending #482 |
| `verdify-prod` namespace | Repo manifests; no live write used in this docs pass | Read for diagnostics; writes Jason-gated through GitOps | Namespace | Jason + Platform/GitOps owners | Gated |
| ArgoCD apps | App YAML in repo | Read app health; sync prod only with Jason approval | `verdify-prod-dark` | Platform/GitOps owners | Gated |
| Kubernetes Secrets | Names/key contracts only | Metadata/status only; no values | Namespace-local | Secret-delivery owner | Values out of scope |
| SOPS/Age private key | No access requested | No access | None | Secret-delivery owner | Out of scope |
| StorageClass/PV/NAS/Longhorn | Manifest references only | Coordination request | Storage resources | `storage-infra` | Out of scope |
| DNS/Cloudflare/Ingress controller/device VLAN | Manifest references only | Coordination request | Routes/network | `network-infra` + Jason | Out of scope |
| Home Assistant / Frigate / Lutron | App references and issue tracking only | Contract review and named integration path | Greenhouse integrations | Jason / integration owners | Needs L7 review |
| Shared monitoring/logging | App dashboard manifests only | Coordination request | Shared platform | `monitoring-stack` | Out of scope |
| Astro stage occurrence store | Secret names/key contracts only; no values/admin path | Protected #533 bucket + reader/writer delivery and name-only verification | Lab stage only | storage-infra / Secret-delivery owner | Approved; delivery pending |
| Isolated reporting tier/feed | Repo contracts only; no live projection/credential | Execute protected #534 anonymous-disabled tier + read-only feed delivery under the recorded stage-only approval | Lab stage only | `verdify-platform` executor | Approved; executor delivery pending |
| ESP32 firmware OTA/device write | Source/validation only | Jason-approved operation only | Live greenhouse device | Jason | Hard gated |

## GitHub Credential Notes

- The GitHub authentication authority is the Agent Fleet registry change in
  `jvallery/agents` commit
  [`7570d1c222b472c091c7378181f9974ed7955bd8`](https://github.com/jvallery/agents/commit/7570d1c222b472c091c7378181f9974ed7955bd8)
  (#3189). Its Verdify record selects the uniform GitHub App installation mode,
  the standard repo-agent permission profile, and gated activation. This repo
  must not duplicate that declaration, name its generated Secret, or add an App
  ID, installation ID, private key, PAT, or custom permission profile.
- As of 2026-07-28, the current repo pod has not received the rotating `gh`
  wrapper, runtime askpass handle, or token-file handles. Legacy token
  plumbing is present but is not authority for a write; credential values were
  not inspected. Local commits and deliberate public reads are available, but
  push, PR, issue, status, workflow, and other authenticated GitHub operations
  are blocked until the Fleet activation and rollout happen externally.
- Re-probed at `2026-07-29T05:17:21Z`: `gh` still resolves to stock
  `/usr/bin/gh` (`2.96.0`); neither `GITHUB_TOKEN_FILE` nor
  `AGENT_GITHUB_TOKEN_FILE` is present; no runtime wrapper exists at the
  declared user-local path. `GIT_ASKPASS` names the legacy helper only. No
  credential value was read. This is the exact central activation blocker in
  `jvallery/agents#3189`; legacy ambient token names are not authorization for
  push, PR, issue, review, or status writes.
- Once activation is performed externally, ordinary HTTPS Git uses the runtime
  `GIT_ASKPASS`, and `gh` resolves through the runtime-provided wrapper. Both
  obtain the current one-hour credential at operation time. File-aware clients
  may read `GITHUB_TOKEN_FILE` for each operation but must not cache its value.
- While activation remains gated, the presence of a legacy token environment or
  a stock `gh` binary is not authority to perform a GitHub write. Do not write
  `hosts.yml`, install a credential helper, or run `gh auth login`; do not fall
  back to a host keyring, SSH identity, anonymous write, or broader credential.
- Organization Project mutations and cross-repository writes are outside the
  standard repository profile. Record them as blockers; do not request a
  Verdify-specific authentication exception.

## GitHub Consumer Inventory

| Consumer | Execution context | Authentication boundary |
|---|---|---|
| Interactive repo checkout and `gh` commands | Agent Fleet repo pod | Ordinary HTTPS Git and `gh` from `PATH`; authenticated operations remain blocked until the central runtime is activated |
| Vendored Quartz `update` / `sync` CLI | Manual, not invoked by CI or deployment | Validates credential-free GitHub HTTPS remotes immediately before each operation, suppresses ambient helpers/headers, uses a scrubbed public-upstream pull, and otherwise spawns ordinary Git separately so the runtime resolves each authenticated pull/push; failures propagate nonzero |
| Firmware-builder source clone | Operator-created production CronJob | Intentional public read with all helpers, askpass, and prompts disabled; never receives the repo-pod App credential |
| Astro stage-snapshot download | One-shot build script | Intentional credential-free public release read; rejects URL credentials and verifies size, digest, and attestation |
| Pre-commit hook downloads | Developer/bootstrap process | Intentional public dependency reads; no token storage or protected-repo fallback |
| Quartz citation locale download | Build-time branch, inactive for current `en-US` configuration | Intentional public `raw.githubusercontent.com` read only |
| ArgoCD repository URLs and fleet `repo-build` source | External long-lived controllers | Controller-owned source acquisition; this repo must not inject its App credential |
| Legacy GHCR image pulls | Kubelet/container registry path | Separate registry credential contract, not Git/`gh`/API auth; retirement remains #482 |
| GitHub Actions | No workflows are present | Per-job `GITHUB_TOKEN` remains a separate unchanged contract if a reviewed workflow is ever restored |

No repo-owned service or watch process performs GitHub operations. No direct
`api.github.com` client, authenticated GitHub `curl`, local App client, or
credential-aware long-lived process remains.

## Secrets Policy

Never print, paste, commit, log, or summarize raw tokens, passwords, API keys,
client secrets, private keys, or decrypted secret files. Reference Secret names,
keys, auth modes, and credential locations only.

## Deleted Environment Note

`verdify-dev` and staging are decommissioned/deleted. Any access request that
mentions them should be revalidated against `AGENTS.md` and
`docs/runbooks/laptop-operator.md` before action.
