# Verdify k3s secret contract (per-env)

Owner: **Iris** (the CONTRACT — key names, per-env matrix, the two invariants, the
sealed-artifact shape). Delivery MECHANISM is **Root** (`needs:root`): the SOPS+age
sealing, the registry secret-meta, and the out-of-band apply step. Tracked under
the secrets-out-of-.env umbrella **#30**; the concrete SOPS structure + staging
seal + named prod inventory deliverable is **#66**.

This file is the single source of truth for **which Secret carries which key in
which env, and how the manifests reference it**. It contains **NO real secret
material** — keys/refs only. The deploy manifests reference every Secret BY NAME;
the protected source is owned under
`jvallery/agents/platform/gitops/secrets-ksops/verdify-prod/` and reconciled by
`jvallery/agents/.github/workflows/local-k8s-secret-sync.yml` BEFORE the ArgoCD
app reconciles. `kustomize` cannot decrypt SOPS, so the in-repo
`*.placeholder.yaml` files exist ONLY so `kustomize build` / `kubeconform`
render a complete, lint-able manifest in CI.

## Design basis

k3s/ArgoCD migration design **§2.4** re-scopes secrets from GCP Secret Manager to
**in-cluster secrets** (SOPS+age source-of-truth, a SOPS→reconciler / sealed-secrets
controller in-cluster). Do NOT invent a second secrets system — reuse the fleet
stack Root deploys for the other agents. Until that controller is confirmed live,
the interim is a one-time out-of-band apply per the seal list; the GitOps
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
   a missing key). The pinned namespaces are `verdify-dev`, `verdify-staging`,
   `verdify-prod`.

## Never

- **Never** commit a real secret value, key, token, or password to this repo.
- **Never** bake credentials into a container image (the manifests inject every
  credential via `secretKeyRef` / `secretRef` / a mounted Secret volume at runtime).
- **Never** set `DEVICE_WRITE=1` / an ESP32-egress allow in dev or staging — those
  envs are device-dark. The device-write path + `ESP32_API_KEY` use live ONLY in
  `verdify-prod`.
- **Never** rotate/seal `ESP32_API_KEY` without exact-target validation of the
  canonical value; it is the ESP32 Noise PSK and must never trigger a re-flash
  (handoff §6). The live `.env` and the esphome `secrets.yaml` have DRIFTED (two
  different shas); reconcile at source and seal from the canonical one.

## Secret inventory (env-injected references — no values)

Service → Secret → key wiring as authored in `deploy/k8s/{base,components}`:

| Secret | Key | Consumed by (ref type) | dev | staging | prod |
|---|---|---|---|---|---|
| `verdify-app-secrets` | `POSTGRES_PASSWORD` | db / mcp / migrate / planner / setpoint-server and the bounded runtime-role bootstrap; API/ingestor only outside the prod Gate-1 cutover (`secretKeyRef`) | ✓ | ✓ | ✓ |
| `verdify-app-secrets` | `VERDIFY_WRITE_API_KEY` | api (`secretKeyRef`; write guard `api/main.py`) | ✓ | ✓ | ✓ |
| `verdify-app-secrets` | `MQTT_USER` | ingestor (`secretKeyRef`) | ✓ | ✓ | ✓ |
| `verdify-app-secrets` | `MQTT_PASS` | ingestor (`secretKeyRef`) | ✓ | ✓ | ✓ |
| `verdify-app-secrets` | `ESP32_API_KEY` | ingestor (`secretKeyRef`); **device-affecting** | ref-only¹ | ref-only¹ | ✓ |
| `verdify-app-secrets` | `VERDIFY_API_RUNTIME_DB_USER`, `VERDIFY_API_RUNTIME_DB_PASSWORD` | production ordinary API database identity (migration 217 exact login; required before the Gate-1 sync) | opt | opt | ✓ |
| `verdify-app-secrets` | `VERDIFY_INGESTOR_RUNTIME_DB_USER`, `VERDIFY_INGESTOR_RUNTIME_DB_PASSWORD` | production ordinary ingestor and gather-subprocess database identity (migration 217 exact login; required before the Gate-1 sync) | opt | opt | ✓ |
| `verdify-app-secrets` | `VERDIFY_EXPERIMENT_LIFECYCLE_DB_USER`, `VERDIFY_EXPERIMENT_LIFECYCLE_DB_PASSWORD` | API v2 lifecycle/status surface (`secretKeyRef`, optional; exact function-only login required) | opt | opt | opt |
| `verdify-app-secrets` | `VERDIFY_EXPERIMENT_API_TOKEN`, `VERDIFY_EXPERIMENT_OPERATOR_TOKEN` | API v2 command and blinded-safe operator surfaces (`secretKeyRef`, optional; distinct authorization boundaries) | opt | opt | opt |
| `verdify-app-secrets` | `VERDIFY_EXPERIMENT_COMPONENT_DB_USER`, `VERDIFY_EXPERIMENT_COMPONENT_DB_PASSWORD` | ingestor confirmed-component executor (`secretKeyRef`, optional; exact function-only login required) | opt | opt | opt |
| `verdify-app-secrets` | `VERDIFY_EXPERIMENT_EQUIPMENT_SOURCE_COLLECTOR_DB_USER`, `VERDIFY_EXPERIMENT_EQUIPMENT_SOURCE_COLLECTOR_DB_PASSWORD` | ingestor append-only equipment source collector (`secretKeyRef`, optional; exact function-only login required) | opt | opt | opt |
| `verdify-app-secrets` | `OPENAI_API_KEY` | planner (`secretKeyRef`, `optional: true`) | ✓ | — | ✓ |
| `verdify-experiment-v2-shadow-scheduler-db` | `password` | v2 lifecycle scheduler (`secretKeyRef`, optional; username is the migration-owned exact login) | — | — | opt |
| `verdify-experiment-v2-randomizer-db` | `password` | v2 selector/randomizer (`secretKeyRef`, optional; username is the migration-owned exact login) | — | — | opt |
| `verdify-experiment-v2-outcome-freezer-db` | `password` | v2 outcome freezer (`secretKeyRef`, optional; username is the migration-owned exact login) | — | — | opt |
| `verdify-experiment-v2-selector-provider` | `api-key` | v2 selector provider adapter (`secretKeyRef`, optional; no endpoint means no network call) | — | — | opt |
| `verdify-ha-token` | `ha_token.txt` | setpoint-server (volume mount); **device-affecting** | — | — | ✓ |
| `verdify-hermes` | `OPENAI_API_KEY`, `HERMES_MCP_URL`² | hermes-iris (`envFrom.secretRef`) | — | — | ✓ |
| `verdify-hermes-slack` | slack channel config | hermes-iris (optional volume mount; **non-secret** channel cfg) | — | — | opt |
| `verdify-lab-publisher-s3` | `LAB_S3_BUCKET`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION`, optional `LAB_S3_ENDPOINT_URL` | lab-publisher (`envFrom.secretRef`) | ✓ | — | ✓ |
| `verdify-grafana-secrets` | `GRAFANA_ADMIN_PASSWORD` | grafana (`secretKeyRef`, required; pod fails closed when absent) | — | — | ✓ |
| `verdify-grafana-secrets` | `GRAFANA_RENDERER_TOKEN` | grafana + image-renderer (`secretKeyRef`, required shared token; pod fails closed when absent) | — | — | ✓ |
| `ghcr-jvallery-readonly` | `.dockerconfigjson` | all workloads (`imagePullSecrets`) | ✓ | ✓ | ✓ |
| `verdify-agent-secrets` | `AGENT_RO_DSN` | dev/coding agent (read-only `agent_ro`/`pg_read_all_data`, migration 184; **read-only, no device path**) | — | — | ✓ |
| `verdify-firmware-ota` | `ota_password` | `make firmware-deploy` ESPHome OTA upload + `firmware-rollback.sh`; **device-affecting** (flash gate) | — | — | ✓ |

¹ dev/staging are device-dark: the ingestor runs `replicas: 0` and egress to the
device VLAN is denied. The key may be present in the sealed secret for shape parity
but is never exercised — those envs never connect to the live ESP32.

² `HERMES_MCP_URL` is the migration doc **R7** gate: it must point at
`verdify-mcp.verdify-prod.svc:8000` and is repointed at SEAL time, never committed.

The production overlay performs migration 217's Gate-1 role cutover: API and
ingestor ordinary-process aliases reference their distinct runtime credentials,
and the database-owner credential is absent from both ordinary pods. The owner
credential remains bounded to database administration, migrations, and the
PreSync runtime-role bootstrap. The four runtime keys must be reconciled before
any sync. `deploy/k8s/overlays/prod-runtime-role-boundary` is a compatibility
review alias that renders byte-identically to the active `overlays/prod` source;
it is not a second activation layer.

Gate-2 experiment credential activation is separately owned by the
`experiment-v2-credential-bootstrap` component. Before that component is
merged/synced, the fleet authority must reconcile the three experiment
username/password pairs and two API tokens listed above in
`verdify-app-secrets`, plus the `password` key in each of the shadow-scheduler,
randomizer, and outcome-freezer Secrets. The wave-2 hook requires 64-character
lowercase-hex, pairwise-distinct activation values, installs all six database
SCRAM verifiers transactionally, and attests each actual TCP login. It never
reads or validates the optional selector-provider key; provider activation has
its own exact endpoint/CIDR/identity gate.

`verdify-lab-publisher-s3` is non-device but required before enabling the
`verdify-lab-publisher` CronJob. The durable prod prefixes are
`s3://verdify-platform/lab/content`, `.../public`, and `.../state`; dev uses the
same bucket under `lab-dev/*` so the auto-syncing dev publisher cannot overwrite
the public prod tree. For the current S3-compatible endpoint, set
`LAB_S3_BUCKET=verdify-platform`, `AWS_DEFAULT_REGION=garage`, and
`LAB_S3_ENDPOINT_URL=https://s3-hdd.vallery.net` with the Verdify-scoped key.

`verdify-grafana-secrets` is a prod-only, out-of-band prerequisite for the
Grafana Deployment. Root must seal and deliver the two named keys before an
ArgoCD sync; there is deliberately no in-repo placeholder, default password, or
default renderer token. Before any sync, verify only the Secret name and both
required key names (never their values), then run the task-scoped
`verdify-prod-dark` sync. A missing Secret or key leaves new pods
in `CreateContainerConfigError`; Kubernetes still accepts the Deployment object,
but its rollout cannot complete.

## Protected secret-source ownership (Root delivers; Iris specifies)

The current fleet-owned source and delivery entry point are:

```
jvallery/agents/
├── platform/gitops/secrets-ksops/verdify-prod/
└── .github/workflows/local-k8s-secret-sync.yml
```

This contract intentionally records only the owning directory, workflow, Secret
names, and required key names. It does not assert an encrypted filename,
ciphertext contents, or reconciliation state. Root resolves the exact protected
artifact in the owning repository and verifies only target metadata and key
names before running the bounded sync workflow.

The in-repo placeholder resources remain local-config-only and never render into
an Argo application. Runtime delivery is exclusively through the protected
workflow above; an Argo application sync is not a substitute for Secret
reconciliation.

## In-repo placeholder files (local build only)

| File | Secret | Envs |
|---|---|---|
| `overlays/dev/secrets.placeholder.yaml` | `verdify-app-secrets` | dev |
| `overlays/staging/secrets.placeholder.yaml` | `verdify-app-secrets` | staging |
| `overlays/prod/secrets.placeholder.yaml` | `verdify-app-secrets` | prod |
| `overlays/prod/ha-token.placeholder.yaml` | `verdify-ha-token` | prod |
| `overlays/prod/hermes-secret.placeholder.yaml` | `verdify-hermes` | prod |

`verdify-hermes-slack` (optional, non-secret channel config) and
`ghcr-jvallery-readonly` (image-pull, delivered out-of-band by Root) have no
placeholder — the workloads reference them as `optional` / `imagePullSecrets`, so
`kustomize build` is complete without an in-repo stand-in. The required
`verdify-grafana-secrets` also has no placeholder: local rendering validates the
reference, while real pod startup intentionally fails closed until Root delivers
the sealed prod Secret.

## History

- 2026-06-01 (#30): `overlays/prod/secrets.placeholder.yaml` carried
  `config.kubernetes.io/local-config` as a **label**, leaking the placeholder
  `verdify-app-secrets` (5 fake keys) into `kustomize build overlays/prod` output —
  in the device-write env. Fixed to an annotation; added the missing `verdify-hermes`
  placeholder; codified the two invariants above. Every overlay now renders ZERO
  `kind: Secret`.

## Access / least-privilege matrix (#305, 2026-06-20)

Review of every standing token/credential that an agent or CI can use, vs what it
needs. **Hard invariant: no agent/CI principal may have device-write reach or
cluster-admin.** The device writer (ingestor `replicas:1` + the gated
`allow-ingestor-device-egress`) and firmware OTA stay safety-checked regardless.

| Principal | Scope held | Scope needed | Device-write? | Cluster-admin? | Verdict |
|---|---|---|---|---|---|
| CI `GITHUB_TOKEN` (per-job) | `contents:read`+`packages:write` (publish jobs); `contents:write`+`packages:read` (prod-promote PR); `contents:read` (k8s-manifests) | same | no (CI never touches the cluster/device) | no | ✅ already least-privilege |
| `LAB_REPO_TOKEN` (external PAT) | pushes lab content to the lab repo | `contents:write` on the **one** lab repo, fine-grained | no | no | ⚠️ **confirm it's fine-grained + single-repo** (org-level check — Jason) |
| `ghcr-jvallery-readonly` | `.dockerconfigjson` image **pull** | same | no | no | ✅ read-only |
| `verdify-agent-secrets` / `agent_ro` DSN | `pg_read_all_data` (SELECT all, no write) | read prod | no | no | ✅ least-privilege (migration 184, #302) |
| Grafana datasource | connects as `verdify` **SUPERUSER** (`POSTGRES_PASSWORD`), read-only *by intent* | SELECT-only | no | no | ⚠️ **over-grant** — repoint to `agent_ro` (a panel could issue DML today) |
| Agent kubeconfig (context `vallery`) | full **cluster-admin** | namespaced read + `exec` into `verdify-db-0` | only if device-egress enabled (gated) | **yes** | ⚠️ **the real admin surface** — prefer the `agent_ro` DSN (#302) + a narrow RBAC `Role` over the admin kubeconfig |
| `verdify-firmware-ota` / `ota_password` | flash the ESP32 via `make firmware-deploy` | operator OTA | **yes (flash)** | no | 🔒 device-affecting — stays safety-checked; not held by CI |

**CI is already least-privilege** (built-in `GITHUB_TOKEN` + scoped per-job `permissions:`; no kube credential; the gated `argocd app sync` is run by a root executor outside CI). The two open hardening recommendations (own follow-ups): **(a)** repoint the Grafana datasource off the `verdify` superuser onto `agent_ro`/`pg_read_all_data`; **(b)** issue agents the `agent_ro` DSN + a narrow namespaced RBAC `Role` (`get/list` + `pods/exec` scoped to `verdify-db-0`) instead of the cluster-admin kubeconfig. **(c)** confirm `LAB_REPO_TOKEN` is a fine-grained single-repo PAT (Jason, org settings).
