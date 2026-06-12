# Verdify Instance Final Report

Last updated: 2026-06-12

## What This Instance Owns

This provisional Verdify app lane owns the application repo
`VerdifyConsultancy/verdify-platform`: app source, app manifests, app-level
ConfigMaps and Secret schemas, app CI/CD workflows, app-specific runbooks, and
namespace-local app health evidence once a concrete namespace is assigned.

The lane does not own cluster infrastructure, DNS/Cloudflare, ingress
controllers, storage backends, secret values, shared monitoring platforms, or
device-affecting actions.

## Inventory Summary

- Repo inventory documented in `APP_INVENTORY.md`.
- Active repo-defined environments:
  - `verdify-dev`: device-dark proving environment.
  - `verdify-prod`: production target, manual-sync and device-write gated.
- Retired/historical environment:
  - `verdify-staging`.
- Core app workloads: API, MCP, ingestor, planner, DB, migrate job.
- Prod-only/app-adjacent workloads: setpoint-server, MQTT fan-out, Hermes Iris,
  DB backup/exporter, HA gap backfill, lab site, Grafana, tier-2 Traefik.
- Local `kustomize build` renders were used for repo-derived inventory; no live
  cluster state was queried.

## Access Granted Vs Missing

Granted/current:

- Local read/write access to the repo checkout.
- Local inspection of Git history, CI workflows, Makefile, manifests, and docs.
- Local `kustomize build` for repo overlays.

Missing/not assumed:

- Concrete assigned namespace and environment.
- Live namespace read access.
- Namespace-local Secret metadata verification.
- ArgoCD application ownership/current sync state for the assigned namespace.
- Storage/PVC live binding status.
- Public route/DNS/ingress live status.

## Secret Migration Status

- Existing detailed contract: `deploy/k8s/SECRETS.md`.
- Audit summary added in `SECRETS_AUDIT.md`.
- No values were read, printed, or committed.
- Repo placeholders are local-validation-only and should render zero Secret
  objects under kustomize.
- Real secrets are expected from fleet SOPS/Age delivery; Root/Orbit owns value
  sealing and delivery.

## CI/CD Status

Repo CI/CD definitions were inventoried:

- `ci.yml` covers lint, tests, schema/drift, device-write, firmware, replay,
  tunable, restart, and migration rollback guards.
- `k8s-manifests.yml` renders and validates manifests.
- `container-publish.yml` publishes images and pins dev digests.
- `prod-promote.yml` and `promote-diff-guard.yml` gate prod digest promotion.
- `lab-content-pipeline.yml` handles lab content build smoke and optional
  external lab image rebuild dispatch.

No workflows were dispatched in this pass.

## Coordination Requests Opened

Recorded in `COORDINATION_REQUESTS.md`:

- Resolve assigned namespace/environment.
- Confirm ArgoCD app ownership/current target.
- Verify namespace Secret metadata by name only.
- Confirm public route/ingress ownership.
- Confirm storage/PVC and backup policy status.
- Clarify monitoring ownership.
- Gate any device VLAN/device-write actions.
- Coordinate any secret value changes through Jason/Root.

## Risks

- The assignment prompt still contains placeholders, so live namespace inventory
  is intentionally unverified.
- `verdify-prod` includes live greenhouse/device-write surfaces; any prod sync,
  device VLAN, firmware OTA, HA write path, or secret rotation remains gated.
- `verdify-staging` manifests still exist but current repo guidance says staging
  is retired; future sessions should avoid treating staging as active without
  confirmation.
- Some rendered resources are cluster-scoped (`PriorityClass`,
  `ClusterRole`, `ClusterRoleBinding`) and exceed a strict namespace-scoped lane;
  those require platform-agent ownership.

## Recommended Next Actions

1. Jason/Orbit assigns the concrete namespace and environment for this lane.
2. With that namespace, run read-only live inventory for resources in that
   namespace only.
3. Verify Secret object presence by name and key schema without reading values.
4. Reconcile `APP_INVENTORY.md` with live namespace state.
5. Keep out-of-lane asks in `COORDINATION_REQUESTS.md`.
