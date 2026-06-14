# Verdify k3s secret contract (per-env)

Owner: **Iris** (the CONTRACT — key names, per-env matrix, the two invariants, the
sealed-artifact shape). Delivery MECHANISM is **Root** (`needs:root`): the SOPS+age
sealing, the registry secret-meta, and the out-of-band apply step. Tracked under
the secrets-out-of-.env umbrella **#30**; the concrete SOPS structure + staging
seal + named prod inventory deliverable is **#66**.

This file is the single source of truth for **which Secret carries which key in
which env, and how the manifests reference it**. It contains **NO real secret
material** — keys/refs only. The deploy manifests reference every Secret BY NAME;
the real value arrives out-of-band from the fleet SOPS+age backend
(`jvallery/agent-fleet-control`) BEFORE the ArgoCD app reconciles. `kustomize`
cannot decrypt SOPS, so the in-repo `*.placeholder.yaml` files exist ONLY so
`kustomize build` / `kubeconform` render a complete, lint-able manifest in CI.

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
- **Never** rotate/seal `ESP32_API_KEY` without explicit Jason confirmation of the
  canonical value; it is the ESP32 Noise PSK and must never trigger a re-flash
  (handoff §6). The live `.env` and the esphome `secrets.yaml` have DRIFTED (two
  different shas); reconcile at source and seal from the canonical one.

## Secret inventory (env-injected references — no values)

Service → Secret → key wiring as authored in `deploy/k8s/{base,components}`:

| Secret | Key | Consumed by (ref type) | dev | staging | prod |
|---|---|---|---|---|---|
| `verdify-app-secrets` | `POSTGRES_PASSWORD` | db / api / mcp / ingestor / migrate / planner / setpoint-server (`secretKeyRef`) | ✓ | ✓ | ✓ |
| `verdify-app-secrets` | `VERDIFY_WRITE_API_KEY` | api (`secretKeyRef`; write guard `api/main.py`) | ✓ | ✓ | ✓ |
| `verdify-app-secrets` | `MQTT_USER` | ingestor (`secretKeyRef`) | ✓ | ✓ | ✓ |
| `verdify-app-secrets` | `MQTT_PASS` | ingestor (`secretKeyRef`) | ✓ | ✓ | ✓ |
| `verdify-app-secrets` | `ESP32_API_KEY` | ingestor (`secretKeyRef`); **device-affecting** | ref-only¹ | ref-only¹ | ✓ |
| `verdify-app-secrets` | `OPENAI_API_KEY` | planner (`secretKeyRef`, `optional: true`) | ✓ | — | ✓ |
| `verdify-ha-token` | `ha_token.txt` | setpoint-server (volume mount); **device-affecting** | — | — | ✓ |
| `verdify-hermes` | `OPENAI_API_KEY`, `HERMES_MCP_URL`² | hermes-iris (`envFrom.secretRef`) | — | — | ✓ |
| `verdify-hermes-slack` | slack channel config | hermes-iris (optional volume mount; **non-secret** channel cfg) | — | — | opt |
| `verdify-lab-publisher-s3` | `LAB_S3_BUCKET`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION`, optional `LAB_S3_ENDPOINT_URL` | lab-publisher (`envFrom.secretRef`) | ✓ | — | ✓ |
| `ghcr-jvallery-readonly` | `.dockerconfigjson` | all workloads (`imagePullSecrets`) | ✓ | ✓ | ✓ |

¹ dev/staging are device-dark: the ingestor runs `replicas: 0` and egress to the
device VLAN is denied. The key may be present in the sealed secret for shape parity
but is never exercised — those envs never connect to the live ESP32.

² `HERMES_MCP_URL` is the migration doc **R7** gate: it must point at
`verdify-mcp.verdify-prod.svc:8000` and is repointed at SEAL time, never committed.

`DB_PASS` / `DB_PASSWORD` / `DB_DSN` / `VERDIFY_DB_DSN` are derived in-manifest from
`POSTGRES_PASSWORD` + the non-secret connection fields in the `verdify-config`
ConfigMap (`$(VAR)` interpolation); they are NOT separate secret keys.

`verdify-lab-publisher-s3` is non-device but required before enabling the
`verdify-lab-publisher` CronJob. The durable prod prefixes are
`s3://verdify-platform/lab/content`, `.../public`, and `.../state`; dev uses the
same bucket under `lab-dev/*` so the auto-syncing dev publisher cannot overwrite
the public prod tree. For the current S3-compatible endpoint, set
`LAB_S3_BUCKET=verdify-platform`, `AWS_DEFAULT_REGION=garage`, and
`LAB_S3_ENDPOINT_URL=https://s3-hdd.vallery.net` with the Verdify-scoped key.

## Sealed-artifact shape (Root delivers; Iris specifies)

Each Secret above maps to a fleet sealed artifact:

```
agent-fleet-control/
  registry/secrets/<id>.yaml            # meta: target.namespace + name + key list
  secrets/encrypted/<id>.enc.yaml       # SOPS+age ciphertext (NEVER decrypted by kustomize)
```

The canonical sealed source for `verdify-app-secrets` is
`agent-fleet-control/secrets/encrypted/verdify-app-secrets.enc.yaml`, sealed to the
fleet age key and applied by the GitOps secret-delivery step BEFORE the app
reconciles. As of 2026-05-31 it is ALREADY present in `verdify-staging` and decrypts
byte-identical (`POSTGRES_PASSWORD`, `VERDIFY_WRITE_API_KEY`, `ESP32_API_KEY`,
`MQTT_USER/PASS`) — re-applying is a no-op (avoid DB-auth drift). The dev + prod
sealed artifacts land with their envs at **M3** (`needs:root`).

For a REAL apply, drop the `- *.placeholder.yaml` lines from the overlay's
`kustomization.yaml`; the sealed Secret is already in-cluster from the delivery step.

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
`kustomize build` is complete without an in-repo stand-in.

## History

- 2026-06-01 (#30): `overlays/prod/secrets.placeholder.yaml` carried
  `config.kubernetes.io/local-config` as a **label**, leaking the placeholder
  `verdify-app-secrets` (5 fake keys) into `kustomize build overlays/prod` output —
  in the device-write env. Fixed to an annotation; added the missing `verdify-hermes`
  placeholder; codified the two invariants above. Every overlay now renders ZERO
  `kind: Secret`.
