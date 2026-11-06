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
| `verdify-app-secrets` | `POSTGRES_PASSWORD` | db / migrate / planner / setpoint-server and the bounded runtime-role bootstraps; absent from the production API, ingestor, and MCP pods (`secretKeyRef`) | ✓ | ✓ | ✓ |
| `verdify-app-secrets` | `VERDIFY_WRITE_API_KEY` | api (`secretKeyRef`; write guard `api/main.py`) | ✓ | ✓ | ✓ |
| `verdify-app-secrets` | `MQTT_USER` | ingestor (`secretKeyRef`); the `verdify-mqtt` HAOS broker login of the Frigate occupancy subscriber (`ingestor/occupancy_mqtt.py`, #1370, read-only) | ✓ | ✓ | ✓ |
| `verdify-app-secrets` | `MQTT_PASS` | ingestor (`secretKeyRef`); the `verdify-mqtt` HAOS broker login of the Frigate occupancy subscriber (`ingestor/occupancy_mqtt.py`, #1370, read-only) | ✓ | ✓ | ✓ |
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
| `verdify-mcp-runtime-db` | `password` | production MCP and its dedicated PreSync role bootstrap (`secretKeyRef`; username is migration-owned `verdify_mcp_runtime_login`) | — | — | ✓ |
| `verdify-dashboard-runtime-db` | `password` | operator API `DB_PASS` (`secretKeyRef`, required; username is migration-owned `verdify_dashboard_runtime_login`) and the login's role bootstrap (`secretKeyRef`, optional); [operator API](#operator-api-verdify-operator-api-1148) | — | — | ✓ from #1143 |
| `verdify-operator-api` | `operator-api-token` | operator API `OPERATOR_API_TOKEN` (`secretKeyRef`, required); the same value as `verdify-dashboard-web`'s key; [operator API](#operator-api-verdify-operator-api-1148) | — | — | ✓ from #1143 |
| `verdify-mcp-agents` | one key per named agent (`<name>`), the value its MCP bearer token | MCP (optional read-only volume at `/etc/verdify/mcp-agents`, no `subPath`; re-read within 60 s, no restart; [named MCP agents](#named-mcp-agents-vd-59)) | — | — | opt |
| `verdify-hermes` | `OPENAI_API_KEY` | Hermes and v2 OpenAI selector provider adapters (`secretKeyRef`, optional while component capability is off) | — | — | req at activation |
| `verdify-ha-token` | `ha_token.txt` | setpoint-server (volume mount); **device-affecting** | — | — | ✓ |
| `verdify-hermes` | `OPENAI_API_KEY` | MCP semantic retrieval (`secretKeyRef`, required) | — | — | ✓ |
| `verdify-hermes` | `OPENAI_API_KEY`, `HERMES_MCP_URL`² | hermes-iris (`envFrom.secretRef`) | — | — | ✓ |
| `verdify-lab-publisher-s3` | `LAB_S3_BUCKET`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION`, optional `LAB_S3_ENDPOINT_URL` | lab-publisher (`envFrom.secretRef`) | ✓ | — | ✓ |
| `verdify-grafana-secrets` | `GRAFANA_RENDERER_TOKEN` | API saved-public-panel PNG adapter (`secretKeyRef`, optional; fails closed when absent) | — | — | ✓ |
| `verdify-grafana-secrets` | `GRAFANA_ADMIN_PASSWORD` | grafana (`secretKeyRef`, required; pod fails closed when absent) | — | — | ✓ |
| `verdify-grafana-secrets` | `GRAFANA_RENDERER_TOKEN` | grafana + image-renderer (`secretKeyRef`, required shared token; pod fails closed when absent) | — | — | ✓ |
| `ghcr-jvallery-readonly` | `.dockerconfigjson` | all workloads (`imagePullSecrets`) | ✓ | ✓ | ✓ |
| `verdify-agent-secrets` | `AGENT_RO_DSN` | dev/coding agent (read-only `agent_ro`/`pg_read_all_data`, migration 184; **read-only, no device path**) | — | — | ✓ |
| `verdify-frigate-vision-auth` | `token` | Vision CronJob and public API camera reader, mounted read-only at `/vfrigate/token`; existing GET-only two-camera snapshot credential, values owned by agents KSOPS | — | — | ✓ |
| `verdify-firmware-ota` | `ota_password` | `make firmware-deploy` ESPHome OTA upload + `firmware-rollback.sh`; **device-affecting** (flash gate) | — | — | ✓ |
| `verdify-hydro-probe` | `device-id`, `local-key` | ingestor, optional volume mount (`/etc/verdify/hydro-probe`; read-only YINMIK hydro reader, VD-70) | — | — | opt |

¹ dev/staging are device-dark: the ingestor runs `replicas: 0` and egress to the
device VLAN is denied. The key may be present in the sealed secret for shape parity
but is never exercised — those envs never connect to the live ESP32.

² `HERMES_MCP_URL` is the migration doc **R7** gate: it must point at
`verdify-mcp.verdify-prod.svc:8000` and is repointed at SEAL time, never committed.

The production overlay performs migration 217's API/ingestor role cutover and
migration 259's MCP cutover: all three workloads reference distinct ordinary
runtime credentials, and the database-owner credential is absent from their pods. The owner
credential remains bounded to database administration, migrations, and the
PreSync runtime-role bootstraps. The ordinary runtime Secrets must be reconciled before
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
its own exact official-OpenAI endpoint/model/identity gate.

`verdify-lab-publisher-s3` is non-device but required before enabling the
`verdify-lab-publisher` CronJob. The durable prod prefixes are
`s3://verdify-platform/lab/content`, `.../public`, and `.../state`; dev uses the
same bucket under `lab-dev/*` so the auto-syncing dev publisher cannot overwrite
the public prod tree. For the current S3-compatible endpoint, set
`LAB_S3_BUCKET=verdify-platform`, `AWS_DEFAULT_REGION=garage`, and
`LAB_S3_ENDPOINT_URL=https://s3.vallery.net` (the canonical Garage name under
ADR-012; `s3-hdd` is its retiring alias) with the Verdify-scoped key.

`verdify-grafana-secrets` is a prod-only, out-of-band prerequisite for the
Grafana Deployment. Root must seal and deliver the two named keys before an
ArgoCD sync; there is deliberately no in-repo placeholder, default password, or
default renderer token. Before any sync, verify only the Secret name and both
required key names (never their values), then run the task-scoped
`verdify-prod-dark` sync. A missing Secret or key leaves new pods
in `CreateContainerConfigError`; Kubernetes still accepts the Deployment object,
but its rollout cannot complete.

### Grafana global SSO (#174)

The staged admin SSO source also requires `GRAFANA_OAUTH_CLIENT_SECRET` in
a separate immutable `verdify-grafana-oauth` Secret, matching the dedicated Authentik confidential client
`verdify-grafana`. The public client ID is source-owned; the secret value stays
in the owning KSOPS lane in `jvallery/agents`. Provision and read back the exact
provider/callback/group boundary before deploying this configuration. Never
print the provider secret or store it in this repository.

## Named MCP agents (VD-59)

`verdify-mcp-agents` lives in the jvallery/agents KSOPS lane for `verdify-prod`
(jvallery/agents#5993), and only Jason seals its values. Each key is one agent's
name (`^[a-z0-9][a-z0-9_-]{0,62}$`, never `admin`) and its value is that agent's
bearer token: an RFC 6750 token distinct from every other agent's and from every
`VERDIFY_MCP_TOKEN_*` value. MCP mounts the Secret as an optional read-only
volume and re-reads it at most every 60 s; until the Secret exists no agent is
admitted and MCP runs unchanged. `/readyz` lists names only
(`auth_agents_configured`, `auth_agent_keys_rejected`); a rejected key admits
nothing and never makes MCP not ready. Values never appear in logs, errors,
`/readyz`, Git or a browser.

- **Onboard:** Jason names the agent and seals key `<name>` into the
  jvallery/agents KSOPS lane for `verdify-prod`; once delivered, MCP admits
  `agent:<name>` within 3 min with no restart, and the agent's pod carries the
  label `verdify.ai/mcp-agent: "true"` and reads its own key (a pod in
  `verdify-prod` mounts only that key, with `items`, never the whole Secret).
- **Revoke:** delete the key the same way; MCP refuses the agent within 3 min of
  the Secret syncing.
- **Rotate:** Jason replaces the value; within 3 min MCP accepts only the new
  value, with no restart; the agent picks it up from its mounted key file, or on
  restart if it reads the key from an environment variable.

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
| `overlays/prod/planner-cortex-secret.placeholder.yaml` | `verdify-planner-cortex` | prod |

`verdify-mcp-agents` (optional volume) and
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


## #643 prepared six-duty database credentials (not yet provisioned)

The full role-boundary source candidate uses six distinct immutable password-only
Secrets, owned exclusively by `jvallery/agents` central KSOPS. No Secret values or
Secret manifests belong to this product repository. Provision new credentials;
do not rotate or reuse existing API, ingestor, MCP, agent-ro or owner credentials.

| Secret | Required key | Fixed database login |
|---|---|---|
| verdify-grafana-runtime-db | password | verdify_grafana_runtime_login |
| verdify-planner-runtime-db | password | verdify_planner_runtime_login |
| verdify-setpoint-server-runtime-db | password | verdify_setpoint_server_runtime_login |
| verdify-ha-backfill-runtime-db | password | verdify_ha_backfill_runtime_login |
| verdify-lab-publisher-runtime-db | password | verdify_lab_publisher_runtime_login |
| verdify-vision-runtime-db | password | verdify_vision_runtime_login |

Workloads must not sync this prepared switch until the complete reserved268
migration is independently qualified, predecessor seals verified unchanged and
these credentials are delivered by their canonical owner. URL-composed DSNs
require the owner's established URL-safe password generation. Existing admin,
renderer, OAuth, experiment and owner Secret keys remain unchanged.

### Slack (retired, #1352)

No workload references a Slack Secret: alerts go to `alert_log` (the dashboard)
and page through Alertmanager, and Hermes has no messaging tool (its profile
disables the `messaging` toolset). The `verdify-slack` Secret is still delivered
by the agents KSOPS lane until its removal there (#1352 follow-up); nothing
mounts it. The Iris bot token is revoked only after the 7-day zero-Slack window
(about 2026-11-13), from the coordinator, on Jason's decision.

### Hydroponic probe (VD-70, #1183)

`verdify-hydro-probe` is owned by the agents KSOPS lane. It carries the YINMIK
meter's Tuya `device-id` and `local-key`. The single ingestor mounts it
read-only and optionally at `/etc/verdify/hydro-probe`; `hydro_probe_sync`
reads both files on every poll and, while either is absent, logs one
`hydro key missing` line and idles. The values never appear in env, Git or a
log line, and no placeholder renders.

Rotate: re-pairing the meter in the Tuya app changes the local key. Jason reads
the new key from the Tuya cloud (LocalTuya's device entry or
`python -m tinytuya wizard`) and reseals it in agents KSOPS; the ingestor uses
it at its next poll once the kubelet refreshes the mount, with no restart. A new
protocol version goes in `VERDIFY_HYDRO_PROBE_TUYA_VERSION`
(`deploy/k8s/overlays/prod/hydro-probe.patch.yaml`).

### #643 prepared maintenance duties (source proposal, not provisioned)

Two new password-only identities remove the remaining writer-watchdog and
materialized-view-refresh owner-password references. Values belong exclusively
to the `jvallery/agents` central KSOPS lane; this repository declares names/keys.

| Secret | Key | Fixed LOGIN | Ordinary consumer |
|---|---|---|---|
| `verdify-writer-watchdog-runtime-db` | `password` | `verdify_writer_watchdog_runtime_login` | `verdify-writer-watchdog` |
| `verdify-matview-refresh-runtime-db` | `password` | `verdify_matview_refresh_runtime_login` | `verdify-band-curve-refresh` |

The prepared wave4 bootstrap receives these two keys and the existing owner key
only for initial installation. It locks/rechecks NULL state, authenticates any
existing verifier without replacement, refuses mismatched custody, and never
changes an existing password. Runtime callers receive only their matching key.
No Secret object or value is rendered here. The migration/source native
qualification and explicit credential/delivery scope remain pending; do not
sync merely because these names now appear in source. Existing backup, database,
migration and other bootstrap owner credentials are unchanged.

## Public contact verification (#1008)

`verdify-contact-turnstile` / `VERDIFY_TURNSTILE_SECRET` is rendered by agents central KSOPS into `verdify-prod`, consumed only by `verdify-api`. The public widget sitekey belongs in Quartz; hostname `lab.verdify.ai` and action `contact` are checked at Siteverify. No secret is stored in this repository.

## Verdify local vision (#1021)

`verdify-vision-cortex` must contain `api_key.txt`, a dedicated inference-only Cortex identity limited to `llm.primary.longctx.mm`, owned and sealed in agents KSOPS. The vision CronJob mounts it at `/vcortex/api_key.txt`. The old `verdify-vision-key` Gemini Secret is retained without workload references; deleting or revoking it is outside this change.

## Cortex planner (#1350)

`verdify-planner-cortex` must contain `api_key.txt`, a dedicated inference-only Cortex identity limited to the text routes `llm.primary.longctx` and `llm.qwen38u.longctx` (any image request belongs to `llm.primary.longctx.mm` under its own identity). It is live in `verdify-prod`; its adoption into agents KSOPS is jvallery/agents#6079. `verdify-cortex-planner` (components/cortex-planner) mounts it, `optional`, at `/vcortex-planner/api_key.txt`: without it the pod logs `FAIL CLOSED`, raises one `planner_failed` alert and idles instead of crash-looping. Its database login is the existing planner duty (`verdify-planner-runtime-db` / `password`). Placeholder: `overlays/prod/planner-cortex-secret.placeholder.yaml`.

## Verdify knowledge embeddings (#1351)

`verdify-embedding-cortex` must contain `api_key.txt`, a dedicated Cortex identity (73) limited to `embed.main`, with no expiry. It is live in `verdify-prod`; its KSOPS adoption into the agents lane is jvallery/agents#6079. `verdify-mcp` mounts it at `/vembed/api_key.txt` (optional: if it is absent, `lessons_search` and `knowledge_search` fail closed with `embedding_credential_missing`), and so does the suspended `verdify-knowledge-embed` CronJob. `overlays/prod/embedding-cortex.placeholder.yaml` is local-config only, so the render emits no Secret. MCP no longer reads `OPENAI_API_KEY` from `verdify-hermes`.

## Greenhouse snapshot (#1346)

CronJob `verdify-greenhouse-brief` (components/greenhouse-brief) reuses `verdify-planner-cortex`, key `api_key.txt`: the inference-only Cortex identity limited to the text routes `llm.primary.longctx` and `llm.qwen38u.longctx`, live in `verdify-prod` (its adoption into agents KSOPS is jvallery/agents#6079; no new key was minted). It is mounted `optional` at `/vcortex-brief/api_key.txt`: without it the job calls nothing, records one `error` row (`FAIL CLOSED`) per run and exits 2. Its database login is the dashboard login (`verdify-dashboard-runtime-db` / `password`). A separate `verdify-brief-cortex` identity, so the snapshot's Cortex use can be attributed and revoked on its own, is a follow-up in jvallery/cortex-ai-compute and the agents KSOPS lane.

## Firmware artifact signing (#1009)

`verdify-firmware-signing` contains `cosign.key` and `password`. Agents owns the
single SOPS source and Secret-only reconciliation. Only the build Pod's signer
container mounts the private key; the compiler and flasher cannot read it.
The public key is product-owned `cosign.pub`, SHA256
`4a3b1fe0a863ff330a7ff938f827949ad28be846b1da549e090bf35935cc177e`.
Jason explicitly approved this new key and encrypted custody on 2026-10-23.
It grants no permission to flash, rotate OTA credentials or open a device
connection. See [signed-artifact operations](../../docs/runbooks/firmware-signed-artifacts.md).

## Dashboard web (`verdify-dashboard`) (#1142)

The dashboard BFF reads one Secret, delivered by the jvallery/agents KSOPS Application
`verdify-dashboard-secrets` (jvallery/agents#5993, `CreateNamespace=false`, so it syncs once Argo app
`verdify-dashboard` has created the namespace). The `verdify-dashboard` AppProject denies Secret, and
`kustomize build deploy/k8s/dashboard` emits none; there is no placeholder. Its target namespace is
`verdify-dashboard`, the same string as the kustomization's `namespace:`, the Namespace object and the
Application destination (invariant 2).

| Secret | Keys | Consumed by | Rotate |
|---|---|---|---|
| `verdify-dashboard-web` | `oidc-client-secret`, `cookie-secret`, `operator-api-token` | the BFF, Deployment `verdify-dashboard-web` (`secretKeyRef`, required: `OIDC_CLIENT_SECRET`, `COOKIE_SECRET`, `OPERATOR_API_TOKEN`) | Jason re-seals in the jvallery/agents KSOPS lane; `operator-api-token` changes together with `verdify-operator-api` in `verdify-prod`, in one agents PR. Once synced, delete the web pod, and the operator API pod for the token, so each restarts with the new value. A new `cookie-secret` voids only sign-ins in progress; sessions survive. |

A missing Secret or key leaves a new web pod in `CreateContainerConfigError`; the Deployment runs no pod
until R1's pin PR (#1143). Read it back by key name only:
`kubectl -n verdify-dashboard describe secret verdify-dashboard-web`. The image-pull Secret
`zot-origin-cluster-pull` is reflected into the namespace by jvallery/agents#5994. Operations:
[dashboard runbook](../../docs/runbooks/dashboard.md).

## Operator API (`verdify-operator-api`) (#1148)

The operator API, Deployment `verdify-operator-api` in `verdify-prod` (`deploy/k8s/components/operator-api`), reads
two Secrets from the jvallery/agents KSOPS lane for `verdify-prod` (jvallery/agents#5993); only Jason seals their
values. Neither has a placeholder, and `kustomize build deploy/k8s/components/operator-api` emits no Secret. R1's
wiring PR (#1143) put the component in `overlays/prod` once both Secrets existed. The pod refuses to
start unless `OPERATOR_API_TOKEN` has at least 32 printable characters (the BFF's rule) and `DB_USER` (ConfigMap
`verdify-operator-api`) is `verdify_dashboard_runtime_login`.

| Secret | Keys | Consumed by | Rotate |
|---|---|---|---|
| `verdify-operator-api` | `operator-api-token` | Deployment `verdify-operator-api` (`secretKeyRef`, required: `OPERATOR_API_TOKEN`), the bearer the BFF presents; the same value as `verdify-dashboard-web`'s `operator-api-token` | Jason reseals both copies in one jvallery/agents PR. Once both have synced, delete the web pod and the operator API pod so each restarts with the new value; until both have, the BFF's calls get 401. |
| `verdify-dashboard-runtime-db` | `password` | Deployment `verdify-operator-api` (`secretKeyRef`, required: `DB_PASS`, user `verdify_dashboard_runtime_login`), CronJob `verdify-greenhouse-brief` (`secretKeyRef`, required: `DB_PASS`, the same login; #1346) and Job `verdify-maintenance-runtime-role-bootstrap` (`secretKeyRef`, optional: `DASHBOARD_DB_PASSWORD`). A missing or empty value skips that login's duty; any other value must be 64 characters of `[A-Za-z0-9_-]`, or the PreSync hook fails closed and stops the sync. | An owner-run `ALTER ROLE verdify_dashboard_runtime_login PASSWORD …` sets the new value, then Jason reseals it in the KSOPS lane; once synced, restart `verdify-operator-api` (`kubectl -n verdify-prod delete pod -l app.kubernetes.io/component=operator-api`). The role bootstrap never rotates a password. |

A missing Secret or key leaves a new operator API pod in `CreateContainerConfigError`. Read both back by key name
only: `kubectl -n verdify-prod describe secret verdify-operator-api verdify-dashboard-runtime-db`.
