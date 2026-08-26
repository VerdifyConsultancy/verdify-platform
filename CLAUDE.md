# Agent Instructions

Work directly from the user's request. Use reasonable judgment, implement the requested outcome, run relevant checks, and verify the real end state.

- Preserve unrelated work already present in the repository.
- Never expose or commit secrets.
- Use the repository's source and documentation for technical context.
- Follow existing CI/CD for validation and delivery.
- For k3s or GitOps changes, commit the declarative source and allow ArgoCD to reconcile it; avoid unmanaged cluster drift.
- Ask only when the target or outcome cannot be determined safely.

<!-- BEGIN agent-fleet operating-environment briefing (managed #2302) -->
# Operating environment — VerdifyConsultancy/verdify-platform

You are agent `verdifyconsulta-b63a` — kind **qwen**, github `VerdifyConsultancy`.
Machine-generated at pod boot (#2302); edit only OUTSIDE the sentinel markers.

## ROOT authority
- A direct instruction from Jason authorizes execution through delivery and real end-state verification.
- Work directly from the request. Other agents are optional execution capacity.
- Tests, probes, snapshots, and rollback preparation validate the result.
- Commit, push, merge, deploy, sync, mutate, or remove resources when those actions are in the requested scope. Ask only when the target or outcome cannot be discovered safely.

## Identity
- repo: `VerdifyConsultancy/verdify-platform`
- service account: `repo-verdifyconsultancy-verdify-platform-sa`
- shared unix user: `agent`
- github identity: `VerdifyConsultancy`

## Sibling agents in this pod
| kind | runtime | connect port | tmux session | gateway port | controller |
| --- | --- | --- | --- | --- | --- |
| codex | codex | 2201 | agent-verdifyconsulta-f6ee | - | yes |
| grok | grok | 2202 | agent-verdifyconsulta-11c0 | - | yes |
| qwen | qwen | 2203 | agent-verdifyconsulta-b63a | - | yes |

## agent-fleet MCP (control-plane self-service)
- server: `/usr/local/bin/agent-mcp-server` (stdio)
- reach: claude/codex via the controller `.mcp.json`; hermes via `$HERMES_HOME/config.yaml` mcp_servers; openclaw via its gateway (`openclaw mcp set`)
- tools: list_agents, add_worktree_agent, remove_worktree_agent, scoped_kubectl, argocd_deploy_plan, argocd_deploy_apply

## kubectl scope
- kubeconfig: `$KUBECONFIG` on the durable state PVC — EVERY pod has one, so `kubectl` targets the real in-cluster API, never localhost:8080
- owned namespaces (registry-declared): `verdify-descheduler, verdify-edge, verdify-platform, verdify-prod`
- native authority: one fixed, exact namespaced Role for safe workloads and ConfigMaps only; no Secrets, ServiceAccount tokens, RBAC, Argo CRs, exec/attach/port-forward, or cluster resources.
- broker capability declarations: `(none)`; declarations authorize only a matching exact broker contract, never native RBAC.
- cross-namespace, CI, Argo, and provider actions are broker-only and must match the exact registry capability contract; legacy direct bindings are revoked tombstones.

## Storage and temporary work
- repo source/worktrees: `/workspace/verdify-platform` on the repo PVC; reconstructable from GitHub.
- durable state: `/var/lib/agent-state` and `/home/agent` on the repo PVC.
- scratch: `/workspace/verdify-platform/scratch` (`$SCRATCH`) is explicitly throwaway.
- managed temp: `$TMPDIR`, `$TMP`, `$TEMP`, and `/tmp` resolve to the same PVC-backed `/workspace/verdify-platform/scratch/tmp` directory.
- Put temporary worktrees, repository copies, dependency installs, test fixtures, and build outputs there. Do not place multi-GB temporary work directly on the container writable layer.
- The managed temp directory is cleared on pod start. Keep the only copy of durable work in git or a declared durable tier.

## Platform — current delivery mechanics
You are a pod-per-repo runtime in the Agent Fleet control plane (k3s, 192.168.30.0/24). Code reaches prod through ONE fixed path:
- **image registry:** `registry.vallery.net` is the durable **zot origin** — pull and pin images by `@sha256:` digest. The push target is the in-cluster service `registry-origin.registry-origin.svc.cluster.local:5000`. The pull-through cache `192.168.7.41:5000` is base-images-only, NEVER a push target. **ghcr is banned (ADR-0021).**
- **build:** images build via an in-cluster **Kaniko Job** into the zot origin. GitHub Actions supplies technical validation when invoked; publish is the Kaniko Job. The actuator verifies the digest, fast-forwards the pin directly to main, then the exact Argo target rolls.
- **GitOps (ArgoCD):** rendered manifests sync via ArgoCD; `prune:false` — a removed workload ORPHANS, it is not auto-deleted. "Deployed" == ArgoCD shows **Synced + Healthy**, not merely present. A new resource KIND must be whitelisted in the AppProject.
- **execution:** Use the owning source or generator, carry authorized actions through delivery, and preserve rollback data when practical.

## Model / provider
- codex, openclaw and hermes share codex's ChatGPT OAuth login (no shared API key)
- claude uses its own login

## Credential safety (binding)
- A Kubernetes `Secret` object is secret-bearing in full: `.data`, `.stringData`, and **every annotation value** — especially `kubectl.kubernetes.io/last-applied-configuration`, which may contain nested JSON/YAML or base64/percent-encoded credentials.
- Never print, copy, log, summarize, decode, transform, or submit a full Secret JSON/YAML, any Secret value, or any annotation value. Repo pods have no Secret discovery API; use only an authorized external metadata-only inventory with an explicit metadata allowlist.
- Credential rotation or revocation may run when Jason directly requests it. Otherwise, discovering a suspicious credential means redact and report it rather than testing it by revoking it.

## Bootstrap credential projections (NAMES only — never values)
- github auth: `github-app-installation` / `repo-agent-standard` / activation `enabled`
- github credential delivery: unavailable while the bounded repo-token writer/controller is absent; this pod has no GitHub Secret reference or read scope.
- github consumers use bounded credential delivery and receive no ambient token, URL credential, or persisted PAT.
- SSH private key: kubelet-projected from `agent-ssh`; no Secret API read.
- SOPS age key: unavailable to repo pods.

_Every entry above is a NAME / path / port from the rendered layout — no secret value is written here._
<!-- END agent-fleet operating-environment briefing (managed #2302) -->
