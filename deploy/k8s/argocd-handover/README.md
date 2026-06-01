# ArgoCD Application CRs — dev/prod handover (author here, laptop-root applies)

These two `Application` manifests are the **ready-to-apply handover** for the
`verdify-dev` and `verdify-prod` environments. The coordinator agent has FULL
CRUD in `verdify-staging` but **NO write to the `argocd` namespace**, so it
authors the manifests; **laptop-root applies them to the `argocd` ns**.

As of authoring (2026-05-31) the ONLY live verdify ArgoCD Application is
`verdify-local-staging` (Synced + Healthy), syncing `deploy/k8s/overlays/staging`.
`overlays/dev` and `overlays/prod` are INERT — these CRs are what makes them live.

## Provenance / shape

Both CRs are copied byte-for-byte in shape from the live `verdify-local-staging`
Application (read live from the cluster 2026-05-31), changing only the per-env
fields:

| field | local-staging (live) | dev (this) | prod (this) |
|---|---|---|---|
| metadata.name | verdify-local-staging | verdify-dev | verdify-prod |
| labels …/environment | local-staging | dev | prod |
| destination.namespace | verdify-staging | verdify-dev | verdify-prod |
| source.path | deploy/k8s/overlays/staging | deploy/k8s/overlays/dev | deploy/k8s/overlays/prod |

Unchanged from the live app: `project: app-test`,
`source.repoURL: https://github.com/VerdifyConsultancy/verdify-platform.git`,
`source.targetRevision: live/platform-main`,
`destination.server: https://kubernetes.default.svc`,
`syncPolicy.automated{prune:false, selfHeal:true}`, `syncOptions:[CreateNamespace=false]`.

## HARD PREREQUISITES before laptop-root applies (do NOT apply blind)

1. **Secret delivery first.** The SOPS-backed `verdify-app-secrets` (and, for prod,
   `verdify-ha-token` + the device PSK) must exist in `verdify-dev` / `verdify-prod`
   BEFORE the app reconciles, exactly as for staging. Add `verdify-dev` /
   `verdify-prod` arms to the `local-k8s-secret-sync.yml` flow. `CreateNamespace=false`
   means the Namespace object is created by the overlay's `namespace.yaml`, but the
   secret-sync step targets that namespace — sequence it ahead of the App.

2. **Per-env DB StorageClass.** Both overlays retarget verdify-db to
   `synology-iscsi-ssd`. That SC EXISTS and iSCSI mounting is VERIFIED working
   (coordinator pod-mount test on node4, 2026-05-31). Each env gets its OWN
   TimescaleDB StatefulSet + its own iSCSI PVC.

3. **PROD device-write is a SEPARATE human gate (Jason).** `overlays/prod` sets
   `VERDIFY_DEVICE_WRITE_ENABLED=1` and the `allow-ingestor-device-egress`
   NetworkPolicy — the SINGLE real ESP32 writer. Applying `verdify-prod.application.yaml`
   with `selfHeal:true` will bring the prod ingestor up as a device writer once its
   image+secrets resolve. Do NOT apply prod until the §3.4 device-VLAN spike is
   signed off AND the live VM control loop cutover is explicitly approved. Consider
   applying prod with the ingestor still replica-pinned (or apply dev first).

4. **Unpublished images.** `overlays/dev` and `overlays/prod` reference
   `verdify-planner` (GHCR package 404 as of 2026-05-31 → placeholder
   `sha256:0000…`) and prod also `verdify-setpoint-server` (placeholder). With
   `selfHeal:true` these Deployments will ImagePullBackOff until the real digests
   are written back by the container-publish flow. This does NOT block api/mcp/db/www
   (real digests) but the env will not be fully Healthy until the planner/setpoint
   images publish. Apply dev first to shake this out off the device path.

5. **Edge/TLS residuals.** The `*.k3s.verdify.ai` (dev/stage) and `*.verdify.ai`
   (prod) wildcard Certificates + the cloudflared tunnel host-forwards are
   platform-layer prerequisites for WAN/LAN reach (same gate noted in each overlay).
   In-cluster Traefik host-routing is the shape these CRs deliver.

## Apply (laptop-root, in the canonical gitops repo)

These are the literal CRs. In the live setup the App is owned by the agent-fleet
gitops repo (`platform/gitops/applications/<env>/verdify.yaml` /
`jvallery/agents` PR #263 pattern); drop these in there rather than `kubectl apply`
ad-hoc so ArgoCD does not self-prune them. If applying directly:

```sh
kubectl apply -f verdify-dev.application.yaml      # dev first (no device path)
# then, ONLY after the prod device gate is signed off:
kubectl apply -f verdify-prod.application.yaml
```
