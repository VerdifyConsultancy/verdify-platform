# Verdify k3s secret contract (current prod)

The repository's single owning agent maintains the key-name contract and
manifests. Secret delivery remains an out-of-band, operator-controlled Fleet
SOPS+age responsibility. Historical Iris/Root ownership language is retired.

> **Current scope (2026-08-02): prod only.** `verdify-dev` and
> `verdify-staging` were deleted on 2026-06-16. Any dev/staging column or event
> below is historical evidence, not a live target or permission grant. The only
> current runtime namespace is `verdify-prod`; `prod-dark` is a legacy Argo app
> name/overlay variant for that same namespace. Current CI identity and checkout
> blockers are recorded in
> [`docs/ci/fleet-cicd-convergence-2026-08-02.md`](../../docs/ci/fleet-cicd-convergence-2026-08-02.md).

This file is the current source of truth for **which Secret carries which key in
prod, and how the manifests reference it**. It contains **NO real secret
material** — keys/refs only. The deploy manifests reference every Secret BY NAME;
the real value arrives out-of-band from the fleet SOPS+age backend
(`jvallery/agent-fleet-control`) BEFORE the ArgoCD app reconciles. `kustomize`
cannot decrypt SOPS, so the in-repo `*.placeholder.yaml` files exist ONLY so
`kustomize build` / `kubeconform` render a complete, lint-able manifest in CI.

## Design basis

k3s/ArgoCD migration design **§2.4** re-scopes secrets from GCP Secret Manager to
**in-cluster secrets** (SOPS+age source-of-truth, a SOPS→reconciler / sealed-secrets
controller in-cluster). Do NOT invent a second secrets system — reuse the fleet
stack. Until that controller is confirmed live, the interim is an authorized
out-of-band apply per the seal list; the GitOps
secret-delivery step is the fast-follow.

## The two invariants (CONTRACT, enforced by review + `kustomize build`)

1. **LOCAL-CONFIG-IS-AN-ANNOTATION.** Every in-repo `*.placeholder.yaml` Secret MUST
   carry `config.kubernetes.io/local-config: "true"` as an **annotation**, never a
   label. `kustomize build` excludes objects with that **annotation** from its
   output. As a **label** the object renders into the manifest, and an ArgoCD
   Secret-blacklisted AppProject rejects the whole sync ("synchronization tasks are
   not valid"). Verification: `kustomize build` of EVERY overlay must emit **zero**
   `kind: Secret`. (This caught a real prod leak — see History.)

2. **NAMESPACE-MATCH.** For each sealed Secret, the registry secret-meta
   `target.namespace` MUST be byte-identical to:
   - the overlay's `namespace:` directive,
   - the overlay's `Namespace` object, and
   - the ArgoCD `Application.spec.destination.namespace`.

   A mismatch does NOT error — the sealed Secret silently never lands in the
   workload's namespace and the pod starts without its credentials (or CrashLoops on
   a missing key). The only current pinned namespace is `verdify-prod`.

## Never

- **Never** commit a real secret value, key, token, or password to this repo.
- **Never** bake credentials into a container image (the manifests inject every
  credential via `secretKeyRef` / `secretRef` / a mounted Secret volume at runtime).
- **Never** recreate a dev/staging device writer or ESP32-egress path. Those
  environments are deleted. The device-write path + `ESP32_API_KEY` are live
  only in `verdify-prod`.
- **Never** rotate/seal `ESP32_API_KEY` without explicit Jason confirmation of the
  canonical value; it is the ESP32 Noise PSK and must never trigger a re-flash
  (handoff §6). The live `.env` and the esphome `secrets.yaml` have DRIFTED (two
  different shas); reconcile at source and seal from the canonical one.

## Secret inventory (env-injected references — no values)

Service → Secret → key wiring as authored in `deploy/k8s/{base,components}`:

| Secret | Key | Consumed by (ref type) | dev (deleted) | staging (deleted) | prod |
|---|---|---|---|---|---|
| `verdify-app-secrets` | `POSTGRES_PASSWORD` | db / api / mcp / ingestor / migrate / planner / setpoint-server (`secretKeyRef`) | ✓ | ✓ | ✓ |
| `verdify-app-secrets` | `VERDIFY_WRITE_API_KEY` | api (`secretKeyRef`; write guard `api/main.py`) | ✓ | ✓ | ✓ |
| `verdify-app-secrets` | `MQTT_USER` | ingestor (`secretKeyRef`) | ✓ | ✓ | ✓ |
| `verdify-app-secrets` | `MQTT_PASS` | ingestor (`secretKeyRef`) | ✓ | ✓ | ✓ |
| `verdify-app-secrets` | `ESP32_API_KEY` | ingestor (`secretKeyRef`); **device-affecting** | ref-only¹ | ref-only¹ | ✓ |
| `verdify-app-secrets` | `OPENAI_API_KEY` | planner (`secretKeyRef`, `optional: true`) | ✓ | — | ✓ |
| `verdify-ha-token` | `ha_token.txt` | setpoint-server (volume mount); **device-affecting** | — | — | ✓ |
| `verdify-hermes` | `OPENAI_API_KEY` | hermes-iris LLM provider (`envFrom.secretRef`) | — | — | ✓ |
| `verdify-hermes` | `VERDIFY_MCP_TOKEN` | hermes-iris MCP bearer header (`envFrom.secretRef`) | — | — | ✓ |
| `verdify-hermes` | `API_SERVER_KEY` | hermes-iris gateway API auth (`envFrom.secretRef`) | — | — | ✓ |
| `verdify-hermes` | `HERMES_IRIS_API_KEY` | ingestor caller auth (`secretKeyRef`); must be coordinated out-of-band with the gateway's `API_SERVER_KEY` | — | — | ✓ |
| `verdify-hermes-slack` | slack channel config | hermes-iris (optional Secret-backed volume mount; never inspect/print) | — | — | opt |
| `verdify-lab-publisher-s3` | `LAB_S3_BUCKET`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION`, optional `LAB_S3_ENDPOINT_URL` | lab-publisher (`envFrom.secretRef`) | ✓ | — | ✓ |
| `verdify-grafana-secrets` | `GRAFANA_ADMIN_PASSWORD` | grafana (`secretKeyRef`, required; pod fails closed when absent) | — | — | ✓ |
| `verdify-grafana-secrets` | `GRAFANA_RENDERER_TOKEN` | grafana + image-renderer (`secretKeyRef`, required shared token; pod fails closed when absent) | — | — | ✓ |
| `ghcr-jvallery-readonly` | `.dockerconfigjson` | all workloads (`imagePullSecrets`) | ✓ | ✓ | ✓ |
| `verdify-agent-secrets` | `AGENT_RO_DSN` | dev/coding agent (read-only `agent_ro`/`pg_read_all_data`, migration 184; **read-only, no device path**) | — | — | ✓ |
| `verdify-firmware-ota` | `ota_password` | `make firmware-deploy` ESPHome OTA upload + `firmware-rollback.sh`; **device-affecting** (flash gate) | — | — | ✓ |

¹ Historical only: dev/staging were device-dark before deletion. They no longer
have an ingestor, Secret delivery, or device route.

The migration-doc R7 MCP endpoint is now repo-owned at
`mcp_servers.verdify_greenhouse.url` in the Hermes ConfigMap profile. A legacy
`HERMES_MCP_URL` key in an already-delivered Secret is unused by the runtime and
is not part of the current sealed-key contract. The gateway/caller pair uses
the differently named `API_SERVER_KEY` and `HERMES_IRIS_API_KEY`; their values
must be coordinated by the authorized secret-delivery workflow without being
placed in Git or inspected during ordinary validation.

`DB_PASS` / `DB_PASSWORD` / `DB_DSN` / `VERDIFY_DB_DSN` are derived in-manifest from
`POSTGRES_PASSWORD` + the non-secret connection fields in the `verdify-config`
ConfigMap (`$(VAR)` interpolation); they are NOT separate secret keys.

`verdify-lab-publisher-s3` is non-device but required before enabling the
`verdify-lab-publisher` CronJob. The durable prod prefixes are
`s3://verdify-platform/lab/content`, `.../public`, and `.../state`. The old
`lab-dev/*` prefix is unused because dev was deleted. For the current
S3-compatible endpoint, set
`LAB_S3_BUCKET=verdify-platform`, `AWS_DEFAULT_REGION=garage`, and
`LAB_S3_ENDPOINT_URL=https://s3-hdd.vallery.net` with the Verdify-scoped key.

`verdify-grafana-secrets` is a prod-only, out-of-band prerequisite for the
Grafana Deployment. The authorized secret-delivery workflow must supply the two
named keys before an ArgoCD sync; there is deliberately no in-repo placeholder, default password, or
default renderer token. Before any sync, verify only the Secret name and both
required key names (never their values), then obtain Jason's explicit approval
for the manual `verdify-prod-dark` sync. A missing Secret or key leaves new pods
in `CreateContainerConfigError`; Kubernetes still accepts the Deployment object,
but its rollout cannot complete.

## Sealed-artifact shape

Each Secret above maps to a fleet sealed artifact:

```
agent-fleet-control/
  registry/secrets/<id>.yaml            # meta: target.namespace + name + key list
  secrets/encrypted/<id>.enc.yaml       # SOPS+age ciphertext (NEVER decrypted by kustomize)
```

The canonical sealed source for `verdify-app-secrets` is
`agent-fleet-control/secrets/encrypted/verdify-app-secrets.enc.yaml`, sealed to the
fleet age key and applied by the authorized secret-delivery step BEFORE the app
reconciles. The 2026-05-31 staging delivery record is historical; staging and dev
are deleted. Never infer current prod contents from that record or inspect values
to compare them.

For a REAL apply, drop the `- *.placeholder.yaml` lines from the overlay's
`kustomization.yaml`; the sealed Secret is already in-cluster from the delivery step.

## In-repo placeholder files (local build only)

| File | Secret | Envs |
|---|---|---|
| `overlays/prod/secrets.placeholder.yaml` | `verdify-app-secrets` | prod |
| `overlays/prod/ha-token.placeholder.yaml` | `verdify-ha-token` | prod |
| `overlays/prod/hermes-secret.placeholder.yaml` | `verdify-hermes` | prod |
| `overlays/prod-dark/secrets.placeholder.yaml` | `verdify-app-secrets` | prod (legacy app variant) |
| `overlays/prod-dark/ha-token.placeholder.yaml` | `verdify-ha-token` | prod (legacy app variant) |
| `overlays/prod-dark/hermes-secret.placeholder.yaml` | `verdify-hermes` | prod (legacy app variant) |

`verdify-hermes-slack` (optional Secret-backed channel config) and
`ghcr-jvallery-readonly` (image-pull, delivered out-of-band) have no
placeholder — the workloads reference them as `optional` / `imagePullSecrets`, so
`kustomize build` is complete without an in-repo stand-in. The required
`verdify-grafana-secrets` also has no placeholder: local rendering validates the
reference, while real pod startup intentionally fails closed until the
authorized secret-delivery workflow supplies the sealed prod Secret.

Every Kubernetes Secret object is secret-bearing in full even when a consumer
expects only channel configuration. Never inspect, print, or summarize any
`verdify-hermes-slack` field, value, or annotation.

## History

- 2026-06-01 (#30): `overlays/prod/secrets.placeholder.yaml` carried
  `config.kubernetes.io/local-config` as a **label**, leaking the placeholder
  `verdify-app-secrets` (5 fake keys) into `kustomize build overlays/prod` output —
  in the device-write env. Fixed to an annotation; added the missing `verdify-hermes`
  placeholder; codified the two invariants above. Every overlay now renders ZERO
  `kind: Secret`.

## Historical access / least-privilege matrix (#305, 2026-06-20; superseded)

This matrix records the retired GitHub Actions/laptop operating model and is
not current access authority. Repository Actions workflows and `prod-promote`
no longer exist; the current bounded Agent Fleet identities, credential names,
checkout blocker, Zot bindings, and namespace-scoped Kubernetes access are
inventoried in
[`docs/ci/fleet-cicd-convergence-2026-08-02.md`](../../docs/ci/fleet-cicd-convergence-2026-08-02.md).
Do not infer present permissions from the rows below.

Review of every standing token/credential that an agent or CI can use, vs what it
needs. **Hard invariant: no agent/CI principal may have device-write reach or
cluster-admin.** The device writer (ingestor `replicas:1` + the gated
`allow-ingestor-device-egress`) and firmware OTA stay Jason-gated regardless.

| Principal | Scope held | Scope needed | Device-write? | Cluster-admin? | Verdict |
|---|---|---|---|---|---|
| CI `GITHUB_TOKEN` (per-job) | `contents:read`+`packages:write` (publish jobs); `contents:write`+`packages:read` (prod-promote PR); `contents:read` (k8s-manifests) | same | no (CI never touches the cluster/device) | no | ✅ already least-privilege |
| `LAB_REPO_TOKEN` (external PAT) | pushes lab content to the lab repo | `contents:write` on the **one** lab repo, fine-grained | no | no | ⚠️ **confirm it's fine-grained + single-repo** (org-level check — Jason) |
| `ghcr-jvallery-readonly` | `.dockerconfigjson` image **pull** | same | no | no | ✅ read-only |
| `verdify-agent-secrets` / `agent_ro` DSN | `pg_read_all_data` (SELECT all, no write) | read prod | no | no | ✅ least-privilege (migration 184, #302) |
| Grafana datasource | connects as `verdify` **SUPERUSER** (`POSTGRES_PASSWORD`), read-only *by intent* | SELECT-only | no | no | ⚠️ **over-grant** — repoint to `agent_ro` (a panel could issue DML today) |
| Agent kubeconfig (context `vallery`) | full **cluster-admin** | namespaced read + `exec` into `verdify-db-0` | only if device-egress enabled (gated) | **yes** | ⚠️ **the real admin surface** — prefer the `agent_ro` DSN (#302) + a narrow RBAC `Role` over the admin kubeconfig |
| `verdify-firmware-ota` / `ota_password` | flash the ESP32 via `make firmware-deploy` | operator OTA | **yes (flash)** | no | 🔒 device-affecting — stays Jason-gated; not held by CI |

The historical conclusion was that GitHub Actions CI was least-privilege. It is
superseded and must not be used to assess the current Argo Workflow path. The
remaining recommendations from that snapshot were: **(a)** repoint the Grafana
datasource off the `verdify` superuser onto `agent_ro`/`pg_read_all_data`;
**(b)** replace broad legacy agent kubeconfigs with scoped access; and **(c)**
confirm `LAB_REPO_TOKEN` is fine-grained and single-repository. Current access
changes belong in the fleet registry, never a hand-applied Role.
