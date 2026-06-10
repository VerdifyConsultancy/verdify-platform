# Verdify k3s Migration & CI/CD Roadmap (authoritative)

_Owner: coordinator (verdify agent). Last replan: 2026-05-31. Supersedes scattered design/runbook notes for sequencing purposes; the master plan (`/mnt/agents/root/docs/verdify-k3s-migration-plan-2026-05-31.md`) remains the detailed technical reference._

## 0. Goal (Jason, 2026-05-31)

Be **GREEN in k3s** and have **reliable push-to-deploy CI/CD for BOTH staging and production**.

## 1. Authoritative ground truth (do not contradict)

- **Deploy branch / ArgoCD targetRevision = `live/platform-main`.** `main` is the GitHub default but NOT the deploy branch.
- **Image namespace = `ghcr.io/verdifyconsultancy/verdify-{api,mcp,ingestor,migrate}`** (in-org). `jvallery/*` retiring.
- **RACI:** the verdify agent OWNS building/bumping all containers, `deploy/k8s` manifests, CI, and migration execution. **laptop-root** owns the k3s substrate, ArgoCD Application CRs, SOPS keys, RBAC/kubeconfig. **James + Jason** own outward DNS/edge and irreversible deletes. git -> ArgoCD is the law of record.
- **Cluster:** 5-node k3s v1.35.5. `verdify-staging` LIVE+Healthy (api 1/1, mcp 1/1, db STS 1/1, **ingestor 0/0** two-writer pin). `verdify-prod` empty scaffold. ArgoCD `verdify-local-staging` Synced/Suspended sourcing `jvallery/agent-fleet-control` rev main; repoint to `verdify-platform/deploy/k8s/overlays/staging` PARKED at PR jvallery/agents#274.
- **Confirmed hazards:** TimescaleDB skew (cluster 2.17.2-pg16 vs live ~2.25.2 — downgrade hazard, #57); `VERDIFY_DEVICE_WRITE_ENABLED` reads back EMPTY; no `deny-esp32-egress`; all manifests still pin `jvallery/*`.

## 2. Definition of GREEN

**GREEN in k3s — STAGING:**
1. ArgoCD `verdify-local-staging` **Synced + Healthy** sourcing `verdify-platform/deploy/k8s/overlays/staging` @ `live/platform-main`.
2. api 1/1, mcp 1/1, verdify-db STS 1/1, **ingestor 0/0** (two-writer pin), all on `ghcr.io/verdifyconsultancy/verdify-*@sha256` digests (zero `jvallery/*`).
3. A merge to `live/platform-main` builds in-org GHCR digests and **auto-bumps `overlays/staging` with no human action**; PRs build-without-publish.
4. Secrets + 5 NetworkPolicies GitOps-managed via SOPS (`argocd-sops` CMP), no out-of-band drift.
5. Device-safety interlock declared: staging pins `VERDIFY_DEVICE_WRITE_ENABLED=0` + ships `deny-esp32-egress`; device-write-gate CI test PR-blocking.

**GREEN in k3s — PROD:**
1. `verdify-prod` ArgoCD app(s) reconcile; non-ingestor workloads Healthy; control+data tiers **manual-sync** (no self-heal/auto-prune); ingestor stays 0 until G9.
2. Prod is **never auto-rolled by a merge** — promotion is a deliberate `bump-prod` PR copying the SAME staging digests, CODEOWNERS(@jvallery)-gated, coordinator clicks Sync.
3. After G9: exactly ONE ESP32 writer (k3s node IP), 2+ green cycles, G10 smoke green (image==source at `/health/detailed`).

## 3. End-to-end CI/CD path

```
PR  ──ci.yml (G1 lint / G2 unit+schema+firmware / G3 strict-migration / G6 device-write-gate, all PR-blocking; PRs build-without-publish)
  │
merge to live/platform-main
  │
container-publish.yml ── builds + pushes ghcr.io/verdifyconsultancy/verdify-{api,mcp,ingestor,migrate}@sha256 (G4) + Trivy/SBOM (G5); #68 vendored the reusable wf in-org
  │
k8s-manifests.yml ── kustomize v5.4.3 + kubeconform -strict on deploy/k8s/** (renders Valid) AND digest-bump-back: kustomize edit set image into overlays/staging ONLY, commit with paths-ignore deploy/k8s/overlays/** (loop-broken). Never prod.
  │
ArgoCD verdify-local-staging ── auto-syncs staging to the new digest (G7)
  │
bump-prod PR (workflow_dispatch / make promote-prod SHA=<sha>) ── copies SAME staging digests into overlays/prod; promote-diff-guard + CODEOWNERS @jvallery (G8)
  │
coordinator clicks Sync on verdify-prod-control (manual) ── non-ingestor prod workloads roll
  │
G9 (Jason HARD STOP) ── atomic single-writer ESP32 handoff
  │
G10 ── post-deploy smoke + device-route ESTAB monitor (==1)
```
Rollback at any stage = `git revert` the bump commit -> OutOfSync -> Sync, or `kubectl rollout undo`.

## 4. Phases -> Gates -> Sprints -> Epics -> Issues

| Phase | Gates | Sprint | Sub-epic | Issues |
|---|---|---|---|---|
| **P0 Local plane** | 0a-0e | S3 (route prep can start earlier) | Prod cutover | NEW Phase-0 local plane |
| **P1 Real runner + iSCSI DB + secrets + access** | G-DB-1/2, G11 | S1 (secrets/version) + S2 (runner/iSCSI) | Staging parity, DB migration | #57, #66, #30, #64(done); NEW migrate image, iSCSI STS |
| **P2 DB migration** | G-DB-1..4, G3 | S2 | DB migration | #24, #28, #57; NEW DB load/validate runbook |
| **P3 / G9 Atomic single-writer** | G9 (HARD) | S3 | Prod cutover, Device-safety | #25, #27, #67; NEW device-write-gate, deny-egress NetPol |
| **P4 Web/content/obs local** | Gate 28 | S3 | Prod cutover, Observability | #58, #59, #60, #63; NEW web-tier into k3s |
| **P5 WAN edge** | Gate 29/30 | S4 | Prod cutover | #67; NEW Cloudflare WAN view |
| **P6 Decommission iris** | Gate 31 (HARD) | S4 | Decommission | #61, #62; NEW mask+decommission |
| **CI/CD pipeline** | G1-G5, G10 | S1 (core), S2 (prod) | CI/CD pipeline green | #22, #23, #26, #56(done); NEW digest-bump-back, CODEOWNERS+promote-guard, docker-build, e2e smoke |

## 5. Sprints (milestones)

- **Sprint 1 — Sprint 2026-06-01 (existing #1, due 2026-06-14): GREEN IN K3S STAGING.** Day 1: #54 labels. Then #56 close (verify GHCR push) + image-namespace switch + overlays restructure + #57 version bump + device-safety interlock + #22 triggers + digest-bump-back + #66 secrets -> #65 ArgoCD repoint. Parallel Track-A P0s held here so they aren't crowded out: **#36 (June heat)**, **#39 (human plant verification)**.
- **Sprint 2 — 2026-06-15..06-28: DB shell + load + prod overlay.** Real migrate image, iSCSI STS recreate, DB load/validate (G-DB-1..4), prod ArgoCD app, prod-promote mechanism + CODEOWNERS, #25 SHADOW_MODE, #58 /health/detailed.
- **Sprint 3 — 2026-06-29..07-12: Atomic single-writer cutover + local edge.** Phase-0 local plane, G9 handoff (Jason), G10 smoke, Phase-4 web/content local, #27/#67 edge.
- **Sprint 4 — 2026-07-13..07-26: WAN edge + iris decommission.** Phase-5 Cloudflare, Phase-6 mask+destroy (Gate 31), #61 host cleanup, #62 monorepo prune, #53 closed.

## 6. DB migration & cutover sequence (copy-not-move)

1. Bump TimescaleDB to >=2.25.2-pg16 in STS + both wait-for-db initContainers (#57) — **must precede any restore**.
2. Recreate STS on synology-iscsi (Retain), prune=false (G-DB-2).
3. Build real migrate image; prove idempotent on throwaway DB (G-DB-1).
4. Dress-rehearsal dump->restore->validate (off the device-dark path).
5. Open write-freeze window (Jason): `systemctl stop verdify-ingestor` on iris; confirm zero :6053 ESTAB.
6. Final `pg_dump -Fc` -> `timescaledb_pre_restore()` -> `pg_restore --exit-on-error jobs=1` -> `timescaledb_post_restore()`; stream node->node (never land on iris).
7. Re-assert 4 compression + 5 retention + 2 UDA jobs; `alembic stamp 0001_baseline`; flip migrate Job to PreSync (G-DB-3).
8. Run 8 validation queries; counts match iris (G-DB-4, HARD precondition for G9).
9. G9 atomic handoff: greenhouses repoint -> write gate ON + ingestor 0->1 on pinned node -> exactly one ESTAB -> setpoint-server up -> 2+ green cycles. Rollback: scale ->0, `systemctl start verdify-ingestor` on iris.

## 7. The 5 absolute HARD STOPS

G6 (staging can never write the device) · Gate 0b/Phase-1 firewall raw-socket proof (no ingestor connect) · G-DB-4 (no promotion vs unvalidated DB) · G9/Phase-3 (atomic single-writer handoff) · Gate 31/Phase-6 (irreversible repo/VM destroy).

## 8. RACI summary

| Area | R | A | C/I |
|---|---|---|---|
| Containers, deploy/k8s, CI, migration execution | verdify agent | verdify agent | laptop-root |
| k3s substrate, ArgoCD CRs, SOPS keys, RBAC/kubeconfig, iSCSI SC | laptop-root | laptop-root | verdify agent |
| Write-freeze window, G9 flip, irreversible destroy | Jason | Jason | verdify agent, laptop-root |
| Outward DNS / Cloudflare edge / repo deletes | James + Jason | Jason | laptop-root |
| nexus split-horizon DNS + cross-VLAN firewall | nexus + laptop-root | laptop-root | verdify agent |

## 9. Track-A (greenhouse) — parallel, non-blocking

Per CLAUDE.md "Track A outranks Track B": crop/firmware/planner work (#13/#14/#16 epics and their children, plus #36 heat, #39 human verification, #40/#41/#42 data-integrity, #43-52 hygiene) stays on its own track. **#36 and #39 are held P0 on Sprint 1** specifically so migration pressure does not deprioritize the 94-105F June-heat window and the top crop-safety gap. They run in parallel and do NOT block the migration critical path.


---

## Tracker index (executed 2026-05-31)

**Top-level epic:** #15 (P0)

**Sub-epics:** #69 CI/CD-green · #70 Staging-parity · #71 Device-safety · #72 DB-migration · #73 Prod-cutover · #74 Decommission · #75 Observability

**Sprints (milestones):** #1 Sprint 2026-06-01 (staging-green) · #2 2026-06-15 (DB+prod-overlay) · #3 2026-06-29 (cutover+local-edge) · #4 2026-07-13 (WAN-edge+decommission)

**Gap issues created:** #76 overlays-restructure · #77 image-namespace-switch · #78 digest-bump-back · #79 device-write-gate · #80 deny-esp32-egress NP · #81 first-real-docker-build · #82 CODEOWNERS+promote-guard · #83 migrate-image · #84 db-on-iscsi · #85 db-load+validate · #86 prod-ArgoCD-app · #87 local-plane DNS/TLS · #88 web-tier-into-k3s · #89 smoke+route-monitor · #90 WAN-edge · #91 decommission-VM · #92 e2e-green-smoke

_Closed: #53 (GCP SaaS superseded), #64 (kubectl access done). Kept open: #56 (PR #68 fixed only the workflow-load half; images still need Dockerfiles to publish)._
