# Runbook — verdify dev/prod ArgoCD Application handover

**Audience:** laptop-root (the operator with `argocd` namespace write).
**Author:** coordinator agent (FULL CRUD in `verdify-staging`, NO `argocd` write).
**Status as of 2026-05-31:** the ONLY live verdify ArgoCD Application is
`verdify-local-staging` (Synced + Healthy, syncing `deploy/k8s/overlays/staging`).
`overlays/dev` and `overlays/prod` are **inert** — they are validated target shapes
that never reconcile until the Application CRs below are applied.

## What this hands over

Two ready-to-apply `Application` manifests:

| File | Env | source.path | destination.namespace | syncPolicy |
|---|---|---|---|---|
| `deploy/k8s/argocd/apps/verdify-dev.yaml`  | dev  | `deploy/k8s/overlays/dev`  | `verdify-dev`  | `automated{selfHeal:true, prune:true}` |
| `deploy/k8s/argocd/apps/verdify-prod.yaml` | prod | `deploy/k8s/overlays/prod` | `verdify-prod` | **manual sync**, `prune` never on the DB |

Both mirror the live `verdify-local-staging` Application shape (read live from the
cluster 2026-05-31): `project: app-test`,
`repoURL: https://github.com/VerdifyConsultancy/verdify-platform.git`,
`targetRevision: live/platform-main`,
`destination.server: https://kubernetes.default.svc`,
`syncOptions:[CreateNamespace=false]`. Only `name`, labels/annotations
`…/environment`, `destination.namespace`, `source.path`, and `syncPolicy` differ.

### Why dev and prod differ in syncPolicy (deliberate, not a copy slip)

- **dev = automated{selfHeal, prune}.** dev is a read-only telemetry subscriber:
  `VERDIFY_DEVICE_WRITE_ENABLED=0`, `deny-esp32-egress` NetworkPolicy, ingestor
  `replicas:0`. Nothing in dev can touch the live ESP32, so full automation
  (including prune) is safe and gives a true GitOps env.
- **prod = manual sync.** `overlays/prod` is the SINGLE real device writer
  (`VERDIFY_DEVICE_WRITE_ENABLED=1`, `allow-ingestor-device-egress`, ingestor
  running). An automated `selfHeal` would bring the prod ingestor up as a second
  live-device writer the instant its image+secrets resolve — that must be an
  operator decision, not a controller reaction. So prod has **no
  `syncPolicy.automated` block**; sync is `argocd app sync verdify-prod` only.
  `prune` stays off the verdify-db StatefulSet + its iSCSI PVC (restored
  TimescaleDB data) under all circumstances.

## HARD PREREQUISITES — do NOT apply blind

1. **Secret delivery first.** SOPS-backed `verdify-app-secrets` must exist in the
   target namespace BEFORE the app first reconciles (exactly as staging). For
   prod additionally `verdify-ha-token` + the device PSK. `CreateNamespace=false`
   means the Namespace is created by the overlay's `namespace.yaml`; sequence the
   secret-sync arm (`local-k8s-secret-sync.yml`) for `verdify-dev` / `verdify-prod`
   ahead of the Application.

2. **Per-env DB StorageClass.** Both overlays retarget `verdify-db` to
   `synology-iscsi-ssd`. That SC EXISTS and iSCSI mounting is **verified working**
   (coordinator pod-mount test on node4 + the live staging `verdify-db-0` already
   Running on a `synology-iscsi-ssd` PVC, 50Gi, Bound, 2026-05-31). Each env gets
   its OWN TimescaleDB StatefulSet + its own iSCSI PVC.

3. **PROD device-write is a SEPARATE Jason gate.** Do NOT apply
   `verdify-prod.yaml` until BOTH: (a) the §3.4 device-VLAN reachability/latency
   spike is signed off, AND (b) the live VM control-loop cutover is explicitly
   approved. Even then, prod is manual-sync — first sync can be done with the
   ingestor still replica-pinned to validate api/mcp/db/www before enabling the
   writer. **Apply `verdify-dev.yaml` FIRST** (no device path) to shake out
   secrets/images.

4. **Unpublished images.** `overlays/dev` references `verdify-planner`
   (GHCR 404 as of 2026-05-31 → placeholder `sha256:0000…`); `overlays/prod` adds
   `verdify-setpoint-server` (placeholder). With dev's selfHeal+prune those
   Deployments ImagePullBackOff until the real digests are written back by
   container-publish. This does NOT block api/mcp/db/www (real, pullable digests),
   but the env is not fully Healthy until planner/setpoint publish.

5. **Edge/TLS residuals.** `*.k3s.verdify.ai` (dev/stage) and `*.verdify.ai`
   (prod) wildcard Certificates (cert-manager letsencrypt-dns01) + the
   cloudflared tunnel host-forwards are platform-layer prerequisites for WAN/LAN
   reach. In-cluster Traefik host-routing is the shape these overlays already
   deliver; the dev api is on `api.k3s.verdify.ai`, dev www on `www.k3s.verdify.ai`.

## Apply (laptop-root)

Prefer dropping these into the canonical agent-fleet gitops repo (so ArgoCD owns
them and does not self-prune). If applying directly to the `argocd` ns:

```sh
# dev first — no device path
kubectl apply -f deploy/k8s/argocd/apps/verdify-dev.yaml

# prod ONLY after the device gate (prereq 3) is signed off:
kubectl apply -f deploy/k8s/argocd/apps/verdify-prod.yaml
# prod is manual-sync — nothing reconciles until you run:
argocd app sync verdify-prod   # review the diff; do NOT prune verdify-db
```

## Validation performed by the author (pre-handover)

- `kustomize build overlays/{staging,dev,prod}` — all build clean.
- `kubeconform -strict -ignore-missing-schemas` — staging 20 Valid / 0 Invalid,
  dev 20 Valid / 0 Invalid, prod 31 Valid / 0 Invalid (skips = Traefik CRDs +
  SOPS placeholder, no Application CRD schema bundled).
- `kubectl apply --dry-run=server -k overlays/staging` — every workload object
  `configured`/`created`; the only error is the cluster-scoped Namespace patch,
  an RBAC artifact of the namespaced agent SA (ArgoCD applies the Namespace, not
  the agent), NOT a manifest defect.
- `kubectl apply --dry-run=client` on both Application CRs — accepted.
- Post-commit: `verdify-local-staging` remained Synced + Healthy; all staging
  pods (api/db/mcp) Running. The branch is unmerged, so the live app is untouched.
