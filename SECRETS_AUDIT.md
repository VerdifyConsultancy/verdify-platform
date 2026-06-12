# Verdify Secrets Audit

Last updated: 2026-06-12

This audit names Secret objects, keys, and consumers only. It does not include
secret values. Detailed existing contract: `deploy/k8s/SECRETS.md`.

## Secret Names And Locations

| Secret | Location / source of truth | Environments | Notes |
|---|---|---|---|
| `verdify-app-secrets` | Real values delivered out-of-band by fleet SOPS/Age; placeholders at `deploy/k8s/overlays/*/secrets.placeholder.yaml` | dev, staging, prod | Carries app DB/API/MQTT/ESP32/OpenAI keys by env. |
| `verdify-ha-token` | Real values delivered out-of-band by fleet SOPS/Age; prod placeholder at `deploy/k8s/overlays/prod/ha-token.placeholder.yaml` and prod-dark equivalent | prod; referenced in dev/staging shapes by base ingestor | Mounted as `ha_token.txt`; device-adjacent because setpoint-server can write lights through HA. |
| `verdify-hermes` | Real values delivered out-of-band by fleet SOPS/Age; prod placeholder at `deploy/k8s/overlays/prod/hermes-secret.placeholder.yaml` and prod-dark equivalent | prod/prod-dark | Hermes gateway env, including OpenAI key and MCP URL. |
| `verdify-hermes-slack` | Out-of-band optional Secret volume | prod/prod-dark optional | Slack channel/config mount for Hermes; treated as secret ref because it is mounted as a Secret. |
| `verdify-grafana-secrets` | Out-of-band Secret, no placeholder found in this repo | dev/prod when Grafana component renders | Grafana admin password. |
| `ghcr-jvallery-readonly` | Out-of-band image pull Secret | all rendered app workloads | Required to pull private GHCR images. |
| `verdify-umami-secrets` | Component reference only; Umami component is present but not in active dev/prod overlays | component-local | Not rendered by active dev/prod overlays in current kustomizations. |
| `verdify-twin-secrets` | Component reference only; firmware-twin component not in active dev/prod overlays | component-local | Not rendered by active dev/prod overlays in current kustomizations. |

## Workload Consumers

| Secret | Consuming workloads |
|---|---|
| `verdify-app-secrets` | `verdify-db`, `verdify-api`, `verdify-mcp`, `verdify-ingestor`, `verdify-migrate`, `verdify-planner`, `verdify-grafana`, `verdify-setpoint-server`, `verdify-db-backup`, `verdify-db-restore-from-prod`, `verdify-band-curve-refresh`, `verdify-ha-gap-backfill`. |
| `verdify-ha-token` | `verdify-ingestor`, `verdify-setpoint-server`, `verdify-ha-gap-backfill`. |
| `verdify-hermes` | `verdify-hermes-iris`, prod ingestor `HERMES_IRIS_API_KEY` in the gather-script patch. |
| `verdify-hermes-slack` | `verdify-hermes-iris` optional volume. |
| `verdify-grafana-secrets` | `verdify-grafana`. |
| `ghcr-jvallery-readonly` | Workloads pulling private `ghcr.io/verdifyconsultancy/*` images. |

## Observed Issues And Gaps

| Finding | Evidence | Status |
|---|---|---|
| Namespace assignment is missing for this audit pass. | Objective used `<NAMESPACE>` placeholder. | Cannot verify live Secret presence without concrete namespace. |
| Placeholder Secrets are present but excluded from render via local-config annotations. | `deploy/k8s/SECRETS.md` and local `kustomize build` output. | Expected; placeholders are for CI validation only. |
| `kustomize build` should render zero `kind: Secret`. | Secret contract requires it; placeholders carry `config.kubernetes.io/local-config: "true"`. | Re-verify in validation when touching manifests. |
| `verdify-grafana-secrets` has consumers but no placeholder manifest. | `deploy/k8s/components/grafana/grafana.yaml` references it. | Needs out-of-band Secret delivery in any namespace using Grafana. |
| `verdify-ha-token` is referenced by base ingestor, including dev/staging rendered shapes. | Base ingestor mounts `verdify-ha-token`; dev/staging pin ingestor replicas to `0`. | Shape parity only for device-dark envs; live Secret presence still namespace-dependent. |
| `ESP32_API_KEY` appears in non-prod Secret shape. | `deploy/k8s/SECRETS.md` says dev/staging are ref-only and device-dark. | Acceptable only while ingestor replicas `0` and ESP32 egress denied. |
| SOPS rule exists for `deploy/k8s/*.sops.yaml`, but active placeholders are not encrypted real secrets. | `.sops.yaml`; `deploy/k8s/SECRETS.md`. | Real encrypted artifacts are delivered by fleet secret system, not this repo's kustomize overlays. |
| Duplicate or stale live Secrets are unverified. | No concrete namespace was assigned, so no live Secret metadata was queried. | Requires namespace-local read-only Secret metadata check. |
| Plaintext real Secrets were not found in the new lane docs. | Narrow token/secret-pattern scan over the six new root docs and `docs/AGENT_STATE.md`. | Repo-wide historical placeholder files still contain fake placeholder strings by design; do not treat them as live credentials. |
| Overbroad Secret access cannot be assessed live. | RBAC and Secret delivery are platform-owned; this pass inspected manifest references only. | Requires platform/Root confirmation of namespace-local secret-delivery scope. |

## Target Kubernetes Secret Schema

`verdify-app-secrets`:

- `POSTGRES_PASSWORD`
- `VERDIFY_WRITE_API_KEY`
- `ESP32_API_KEY`
- `MQTT_USER`
- `MQTT_PASS`
- `OPENAI_API_KEY` where planner/Hermes use requires it

`verdify-ha-token`:

- `ha_token.txt`

`verdify-hermes`:

- `OPENAI_API_KEY`
- `HERMES_MCP_URL`
- `HERMES_IRIS_API_KEY` when consumed by ingestor gather path
- Any additional `HERMES_*` runtime keys documented by the Hermes component

`verdify-hermes-slack`:

- Slack config file key expected by the mounted path in
  `deploy/k8s/components/hermes-iris/hermes-iris.yaml`

`verdify-grafana-secrets`:

- `GRAFANA_ADMIN_PASSWORD`

`ghcr-jvallery-readonly`:

- `.dockerconfigjson`

## SOPS/Age Encryption Plan

- Keep real Secret manifests encrypted; never commit plaintext secret values.
- Use the repo rule in `.sops.yaml`: files matching `deploy/k8s/.*.sops.yaml`
  encrypt only `data` and `stringData`, leaving object metadata reviewable.
- Preserve namespace-match invariant from `deploy/k8s/SECRETS.md`: sealed Secret
  target namespace must match overlay namespace, Namespace object, and ArgoCD
  destination namespace.
- Preserve local-config invariant for placeholders: annotation only, never label.
- The SOPS/Age private key and secret delivery runner are Root/Orbit-owned; this
  app lane specifies schema and consumers, not values.

## CI/CD Secret-Injection Plan

- GitHub Actions use repo/org secrets only for CI operations such as lab
  cross-repo dispatch (`LAB_REPO_TOKEN`) and default `GITHUB_TOKEN`/packages
  permissions; app runtime secrets are not printed or embedded in images.
- Runtime workloads consume Kubernetes Secrets via `secretKeyRef`, `envFrom`, or
  mounted Secret volumes.
- Container builds must continue to avoid baking credentials into images.
- `kustomize build` and kubeconform should validate manifests without emitting
  real or placeholder Secret objects.
- Prod secret changes, device-write secret changes, and any ESP32/HA credential
  changes require Jason/Root coordination.
