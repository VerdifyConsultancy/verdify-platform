# Verdify Final Migration Design — Everything in k3s, Three Environments

**Status:** Authoritative final-push design. Supersedes the two-env framing in `docs/design/k3s-argocd-migration.md` (DESIGN-ONLY) and PR #93's roadmap.
**Date:** 2026-05-31. **Owner lane:** verdify-platform (coordinator). **Branch context:** `live/platform-main`.
**Prime directive (unchanged):** Track A (greenhouse alive, ESP32 in the loop every 5s) outranks Track B (k3s migration). Every device-affecting step is a HARD STOP for Jason.

---

## A. CURRENT-STATE INVENTORY (VM / k3s / GCP / repos / data)

### A.1 Legacy VM (`vm-docker-iris` — this host) — still PRODUCTION
The VM is still the **production ESP32 writer** and holds the **authoritative TimescaleDB**.

**Two live equipment writers (both on the VM, both safety-critical):**
1. `verdify-ingestor.service` (systemd, NOT compose) — the direct ESP32 setpoint writer. `ingestor/esp32_push.py` → native ESPHome API, `ESP32_HOST=192.168.10.111:6053`, Noise PSK via `ESP32_API_KEY`. Gated by `VERDIFY_DEVICE_WRITE_ENABLED=='1'` (default-deny). This is the single-writer the whole k3s safety design is built around (#79).
2. `verdify-setpoint-server.service` (systemd) — `scripts/setpoint-server.py` on `0.0.0.0:8200`. The ESP32 **polls IT** for grow-light commands; writes light state through Home Assistant (`HA_URL=http://192.168.30.107:8123`), logs to `equipment_state`. **A second live equipment writer NOT yet modeled in any k3s overlay — a gap (folds into ingestor pod or new deploy; tracked agents#304).**

**Other systemd units:** `verdify-mcp.service` (`mcp/server.py` :8000, DB writer for set_tunable/set_plan), `verdify-api.service` (uvicorn :8300 — to be retired #61), `verdify-forecast-page.timer` (30m lab regen), `verdify-site-poll.timer` (10s vault→rebuild), `verdify-plan-publish.path` (**currently FAILED** #59).

**docker-compose stack still on VM:** `timescaledb` (`timescale/timescaledb:latest-pg16`, `127.0.0.1:5432`, vol `tsdb_data` — **PRODUCTION DB, primary migration item**); `api` (`verdify-api` :8080); `mqtt` (`eclipse-mosquitto:2` :1883, vol `mqtt_data` — ESP32 + Sentinel occupancy, **not yet in k3s**); `grafana`+`grafana-proxy`+`grafana-renderer` (graphs.verdify.ai, vol `grafana_data`); `verdify-site` (nginx, lab.verdify.ai); `umami`+`umami-db` (analytics.verdify.ai, vol `umami_db_data`); `goaccess`+`goaccess-site` (logs.verdify.ai); `promtail`; `traefik:v3.6.7` (VM-local edge — **OUT-OF-LANE**, root `*.verdify.ai` edge is jvallery/network-infra); `hermes-iris` (`nousresearch/hermes-agent`, loopback `:8642`, sole Iris gateway, state `/var/lib/verdify/hermes/iris`, **no k3s manifest yet**).

**Cron fleet (jason, `/srv/verdify/scripts/*`):** nightly `pg_dump verdify` → `/mnt/iris/backups/verdify-YYYYMMDD.dump` (~140MB/day, the migration vehicle); `daily-summary-snapshot.py`; `vault-daily-writer.py`/`vault-crop-writer.py`/`generate-hydro-map.py`; `frigate-snapshot.py`; `checklist-to-slack.sh`; `verdify-metrics.py`; `slack-channel-archive.py`; `publish-daily-plan.sh`. All become CronJobs.

### A.2 Data locations (what must physically move/copy)
| Data | Path | Action |
|---|---|---|
| **TimescaleDB prod (authoritative)** | vol `tsdb_data` + `/mnt/iris/backups/*.dump` (~1.5GB, ~6.14M–7.84M rows, 81 tbl/19 hypertbl) | **COPY → k3s `verdify-prod` PVC**, the critical-path long pole (#28/#72/#84). Live staging already holds a 289,902-row `climate` snapshot (copy-not-move). |
| Planner state | `PLANNER_STORE_BACKEND=postgres` (uses prod DB) + repo `verdify-planner` + `migrations/001_planner_memory.sql` | Code split-only (#102, data-loss risk); runtime on GCP Cloud Run (project `buoyant-valve-496719-m0`). |
| Hermes Iris state | `/var/lib/verdify/hermes/iris` (slack.yaml + runs) | COPY if Hermes folds into k3s (agents#304). |
| State dir | `/var/local/verdify/state` (`expected-firmware-version`, `ha-sensor-sync-state.json`, dispatch.json) | COPY live operational JSONs; logs rotate. |
| Lab content | `/mnt/iris/verdify-vault/**` + repo `verdify-vault` (site-legacy `content/` is a symlink to it) | **64 uncommitted generated pages MUST be committed before VM decommission (#104, data-loss risk).** Baked into site image at build. |
| Grafana/Umami | vols `grafana_data` / `umami_db_data` | COPY (obs tier). |
| Secrets | `/etc/verdify/*`, `api/.env`, repo `.env` | **OUT-OF-LANE** SOPS/age (`agent-fleet-control`). |

### A.3 k3s current state
- Cluster: 5-node k3s v1.35.5. node1 (control/etcd Ready), node2/node3 (Ready, **SchedulingDisabled/cordoned**), node4/node5 (workers). Substrate unstable: agents#282 (etcd flapping), agents#284 (UDM FRR BGP).
- StorageClasses: `local-path` (default), `agent-worktree-nvme`, `nfs-rwx`/`nfs-rwx-v3` (nfs.csi), `synology-iscsi` (csi.san.synology.com, Retain, WaitForFirstConsumer, expandable — installed ~18h ago, DB NOT on it yet #84).
- MetalLB v0.15.3, BGP-advertised apps-pool `192.168.7.10-.250` on VLAN7. **Use `externalTrafficPolicy: Cluster`** (ETP=Local blackholes off-cluster on reschedule — the live `.7.21` incident agents#361/#308, PR #106).
- `verdify-staging` (LIVE, the only running env): `verdify-api` 1/1 (LoadBalancer `192.168.7.21:80`), `verdify-mcp` 1/1 (ClusterIP `:8000`), `verdify-db-0` StatefulSet 1/1 (`timescaledb:2.25.2-pg16`, PVC 50Gi on `local-path` — hazard #84), `verdify-ingestor` **0/0 (device-safety pin)**. No Ingress (raw LB IP). 6 NetworkPolicies incl. `deny-esp32-egress`. ArgoCD app `verdify-local-staging` Synced+Healthy (`prune:false, selfHeal:true`), source `VerdifyConsultancy/verdify-platform@live/platform-main` path `deploy/k8s/overlays/staging`.
- `verdify-prod` namespace exists (empty shell, only `kube-root-ca.crt`). **No ArgoCD prod app, no workloads, prod overlay carries placeholder digests** (`sha256:00d06…` resolves to the migrate build, never a real prod api). Bootstrap = #86.
- **`verdify-dev` does not exist at all** — no namespace, no `overlays/dev`, no ArgoCD app.

### A.4 GCP footprint (access BLOCKED — `iris-agent@verdify` SA deleted; reconstructed from repo + HTTP probe)
- **Cloud Run `verdify-www`** (`us-central1`, GAR `verdify-www`): CONFIRMED serving `www.verdify.ai` today (Astro→Caddy static). The ONLY verdify-lane item still on GCP. Decision #103: serve-from-k3s vs keep redirect.
- **Cloud Run planner** (`verdify-planner`, project `buoyant-valve-496719-m0`): LangGraph + OpenAI Responses, `gpt-5.5`, invoked by `verdify-planner-caller` SA. **Deliberate keep** (stateless, non-actuating) OR optional k3s fold — genai-lane decision.
- **Dead "Track B" SaaS mirror** (Cloud SQL `verdify-db`, GCE Mosquitto, Pub/Sub, Firebase Auth, Cloud Run ingestor/setpoints/grafana): **superseded, #53 closed.** NOT migrated. May still be billing — needs GCP access fix to confirm/clean (out-of-lane).

### A.5 Repos (6 + fleet)
| Repo | Default | Role | k3s target |
|---|---|---|---|
| **verdify-platform** | `main` (deploy=`live/platform-main`) | api/mcp/ingestor/dispatcher/schemas/firmware/db-migrations/`deploy/k8s/`/`verdify-site` | All 3 envs; CI WIRED |
| **verdify-www** | `main` | Astro consulting site, apex `www.verdify.ai` | New overlays → IngressRoute (#103); source split-only, data-loss risk |
| **verdify-planner** | `main` | `planner_graph/` + memory migration + evals | k3s service or keep Cloud Run; **fold into monorepo #102** |
| **verdify-site-legacy** | `v4` | Quartz lab render engine; `content/` symlink → vault | Superseded by platform `verdify-site/Dockerfile.k3s` (bakes Quartz+content) |
| **verdify-vault** | `main` | Sole lab.verdify.ai content | RWX/baked into site image; **commit 64 pages #104** |
| **verdify-agent-context** | `main` | Private runbooks | Not deployed; preserve |

---

## B. DELIVERED vs REMAINING

### B.1 DELIVERED (running, verifiable in `verdify-staging`)
- k3s staging cutover GREEN: api/mcp/db Running 0 restarts (PRs #94/#95/#96).
- DB restore into k3s (289,902-row snapshot; idempotent restore-aware `verdify-migrate` PreSync `sha-0537abc1`, #98/#83).
- **3-way device-safety interlock shipped & enforced:** code default-deny (`esp32_push.py:31`), staging `replicas:0` + base `replicas:1 Recreate`, `deny-esp32-egress` (staging) vs `allow-ingestor-device-egress` (prod only) + `VERDIFY_DEVICE_WRITE_ENABLED=1` prod only (#79/#84).
- In-org CI/CD: `container-publish.yml` + vendored `reusable-container-build.yml` (#56/#68), `k8s-manifests.yml` digest write-back (#78), `promote-diff-guard.yml` (#82), 8 PR-gates intact, firmware never CI-flashed.
- All 4 images `ghcr.io/verdifyconsultancy/verdify-{api,mcp,ingestor,migrate}@sha256`, digest-pinned, never mutable tag.
- `deploy/k8s/base` + `overlays/{staging,prod}` on disk; CODEOWNERS direct-execution safeguard dropped → autonomous prod-promote gated only by promote-diff-guard (`f332265`); G10 smoke + device-route monitor (`fea5247`, #89).
- Secrets contract: sealed `verdify-app-secrets` present + decrypting in staging.

### B.2 STAGED, not merged (PR #101, validated GREEN, nothing applied)
- **Ingestor `SHADOW_MODE`** (`ingestor/shadow_mode.py` `ShadowConnection` suppresses write-class execute at the pool/LISTEN chokepoint) + `healthz.py` — **the technical enabler for read-only dev/stage ingest.** Closes #25.
- psql call-site sweep → `scripts/lib/psql-verdify.sh` (#24).
- Web tier: `verdify-site` `Dockerfile.k3s` + `build-site-image.sh` + `site-deployment.yaml` + staging `site-ingressroute.yaml`.
- Obs tier: grafana-oss (datasource→in-cluster db), umami+db, goaccess, single-binary Loki (promtail authored but NOT wired — PSA conflict).
- Prod overlay completeness: `db-storage.yaml` (PVC → `synology-iscsi-ssd` 200Gi Retain, Pending #84); prod digests = staging-validated.

### B.3 REMAINING (the gap to 3 envs)
- **dev env entirely net-new** (no ns, no overlay, no DB copy, no ArgoCD app).
- prod stand-up (empty ns → workloads + ArgoCD app, #86).
- DB single-writer handoff (safety-checked, #72/#73).
- Per-repo CI/CD for www and planner (no deploy step today).
- IngressRoutes + split-horizon DNS + wildcard TLS (out-of-lane).
- planner_graph fold (#102), vault commit (#104), www decision (#103), VM decommission (#74/#91).

---

## C. TARGET ARCHITECTURE — Three Environments in k3s

### C.1 The three-env model (conforming to the existing fleet app-of-apps pattern)
| Jason's term | Fleet env | Namespace | ArgoCD app | Ingestor posture | Device write |
|---|---|---|---|---|---|
| **dev** (integration) | `local-dev` | `verdify-dev` | `verdify-local-dev` (NEW) | `replicas:1` + `SHADOW_MODE=1` (read-only ingest) | `VERDIFY_DEVICE_WRITE_ENABLED=0` + `deny-esp32-egress` |
| **stage** (prod mirror) | `local-staging` | `verdify-staging` (LIVE) | `verdify-local-staging` (live) | `replicas:1` + `SHADOW_MODE=1` (was `replicas:0` — upgrade once #25 lands) | `=0` + `deny-esp32-egress` |
| **prod** | `local-prod` | `verdify-prod` | `verdify-local-prod` (NEW) | `replicas:1` + `Recreate`, NO shadow | **`=1` (ONLY here)** + `allow-ingestor-device-egress` |

Each env runs its **own** db StatefulSet + api + mcp + ingestor; **all three ingest greenhouse telemetry** (read ESP32 :6053 / HA :8123 / MQTT :1883 / Frigate :5000), but **dev/stage run in SHADOW_MODE** (asyncpg `ShadowConnection` suppresses all write-class execute → zero DB writes from telemetry-write loops AND zero device writes) while **only prod writes setpoints**. The single-ESP32 invariant is enforced by three independent layers per env: ConfigMap flag, NetworkPolicy egress, and ingestor process gate.

**Read-only telemetry decision (open):** "dev/stage collect new telemetry" + "only prod writes the device" is satisfied two ways — (a) dev/stage ingestors connect read-only to ESP32/HA and write their OWN DB only (needs an egress-READ NetworkPolicy distinct from prod's write-allow, and 3 simultaneous native-API connections to one ESP32 — a device connection-count question), or (b) dev/stage subscribe to MQTT/HA only (no direct ESP32 native-API socket) and prod is the sole ESP32 socket holder. **Recommend (b)** to keep exactly one native ESPHome API connection (the safest single-writer posture) — see open_decisions.

### C.2 Per-env DB copy
- prod: copy-not-move from VM authoritative TSDB (pg_dump RO → restore + parity: row counts + hypertable + compression, G-DB-4 HARD gate #85). PVC on `synology-iscsi-ssd` 200Gi Retain (#84).
- stage: already holds a restored snapshot (50Gi local-path → migrate to synology-iscsi Retain #84).
- dev: seeded from the same dump (full or recent-window subset). Each env's SHADOW_MODE ingestor then appends its own fresh telemetry independently.
- TimescaleDB version pinned `≥2.25.2-pg16` everywhere (no downgrade hazard #57); the long-pole prod path is physical `pg_basebackup → promote` (#28) or dump-restore.

### C.3 URL scheme
**Prod (verdify.ai, already in Cloudflare):**
- `www.verdify.ai` → verdify-www (k3s overlay per #103 decision, else redirect-to-lab)
- `lab.verdify.ai` → platform `verdify-site` (Quartz+vault baked image)
- `api.verdify.ai` → `verdify-api` (prod)
- `graphs.verdify.ai` → grafana (+ renderer; fold proxy into IngressRoute)
- planner internal (→ Hermes/MCP), `mqtt.verdify.ai` raw-TCP (cannot ride HTTP tunnel — stays grey/direct)

**Dev/stage on `*.k3s.verdify.ai`** per Jason's target. **NOTE the existing fleet edge scheme is `*.k3s.vallery.net`** (`verdify.local-dev.k3s.vallery.net`, `verdify.local-staging.k3s.vallery.net`) with a live wildcard cert + HostRegexp router. Building `*.k3s.verdify.ai` is net-new out-of-lane edge work (new cert SAN set `*.k3s.verdify.ai`/`*.local-dev.k3s.verdify.ai`/`*.local-staging.k3s.verdify.ai` + HostRegexp router + Cloudflare records). **Open decision: reuse proven `*.k3s.vallery.net` (zero edge work) vs build parallel `*.k3s.verdify.ai` (consistent branding).** Target as stated = `*.k3s.verdify.ai`; cheapest path = `*.k3s.vallery.net`.

All user-facing services converge to **ClusterIP + host-routed IngressRoute behind the one shared apps-ingress VIP `192.168.7.10`** (ADR-15 Model B′). The per-app LB `.7.21` is the documented interim anti-pattern to retire (add IngressRoute, then drop the LB). Use `metallb.universe.tf/address-pool: apps-pool` + ETP `Cluster`.

### C.4 Dual DNS (no-SPOF: Cloudflare + local split-horizon)
- **External (WAN):** Cloudflare Tunnel (cloudflared, k3s ns `cloudflare`, 2 replicas, dials outbound → survives NextLight/Starlink failure). Add `*.verdify.ai` prod + `*.k3s.verdify.ai` hostnames to the tunnel ingress map → `https://192.168.7.10` Host-preserving (#293 adopt-not-mutate, snapshot-protected). `verdify.ai` apex stays decoupled (Google A `216.239.3x.21`) so the consulting front door survives a home outage.
- **Local (split-horizon, the "Cloudflare-down still serves locally" requirement):** **decision #24, NOT YET BUILT.** Local resolver (pihole ArgoCD app exists as substrate) answers `*.verdify.ai`/`*.k3s.verdify.ai` → `192.168.7.10` internally; cert-manager `wildcard-verdify-ai` (letsencrypt DNS-01, James CF token agents#323) provides local TLS. This is the single largest unbuilt piece of the no-SPOF DNS requirement — **entirely out-of-lane (network-infra #53/#54, nexus).**

### C.5 CI/CD per repo → k3s
Authoritative path (proven on platform): PR → `ci.yml` (lint/unit+schema+firmware/replay-diff THRESHOLD_PCT=0/strict-migration/device-write-gate, PR-blocking, build-without-publish) → merge to env branch → `container-publish.yml` builds+pushes `ghcr.io/verdifyconsultancy/*@sha256` + Trivy/SBOM → `k8s-manifests.yml` (kubeconform-strict + digest write-back into the overlay) → ArgoCD reconciles → promotion `workflow_dispatch` (dry-run default) opens a desired-state PR bumping the next env's digest, gated by promote-diff-guard (byte-identical) + GitHub environment protection.

| Repo | CI today | CI target |
|---|---|---|
| **verdify-platform** | WIRED (4 images, digest pin, ArgoCD staging) | Add dev+prod ArgoCD apps + promotion `verdify-gitops-dev-test-promotion.yml` (the dispatch target is BROKEN/UNBUILT in `jvallery/agents` today — out-of-lane) |
| **verdify-www** | Cloud Run via GAR | New GHCR build + overlay + ArgoCD (mirror platform); decision #103 |
| **lab site** | VM nginx + site-legacy GHCR | platform `verdify-site/Dockerfile.k3s` (bakes Quartz+vault); content-change → new image → GitOps (no live file-watch) |
| **verdify-planner** | test-only, no deploy | Add GHCR build + ArgoCD wiring (or stay Cloud Run); fold planner_graph into monorepo #102 |
| **firmware OTA** | `make firmware-deploy` safety-checked | Optional `workflow_dispatch` self-hosted-LAN runner; **CI MUST NOT flash** — all freeze rules (no-OTA-while-critical-alert, ≤1 OTA/week, 48h bake, replay-diff=0) preserved as the only OTA path |

---

## D. SWIM LANES — MINE vs OUT-OF-LANE

### D.1 MINE (verdify-platform owner)
- All `deploy/k8s/{base,overlays/{dev,staging,prod}}` kustomize: the NEW `overlays/dev`, the device-safety interlock per env (ConfigMap flag + NetworkPolicy YAML + ingestor `replicas`/`Recreate`/`SHADOW_MODE`), prod overlay real digests, PVC declarations.
- All app CI/CD inside each Verdify repo: `container-publish.yml`, `k8s-manifests.yml`, `promote-diff-guard.yml`, `ci.yml`, the promotion-dispatch workflow body, the IngressRoute YAML (declaration), the child ArgoCD Application YAML (authored in-repo/handed to fleet).
- Code: ingestor SHADOW_MODE/healthz, esp32_push device gate, psql abstraction, migrations, schemas.
- Web/obs manifests: `verdify-site` image+deploy, grafana/umami/goaccess/loki manifests, CronJobs from systemd timers, hermes-iris pod spec (declaration).
- Repo hygiene: fold planner_graph (#102), commit vault (#104), www decision+move (#103), retire host `verdify-api.service` (#61), fix FAILED units (#59).
- Firmware: keep `make firmware-deploy` the sole OTA path; never wire OTA into Actions.

### D.2 OUT-OF-LANE (network-infra / cluster / hardware / fleet — jvallery/*)
- ArgoCD AppProject whitelists (`verdify-platform.git` + ns `verdify-dev`/`verdify-prod`), `local-prod-apps.yaml` app-of-apps root, ArgoCD install/CRs (#86, agents#298/#301-307).
- `local-k8s-secret-sync.yml` enum arms for `verdify-dev`/`verdify-prod` (protected self-hosted runner), SOPS/age sealing + private-key custody (#30/#66/#105/#334).
- StorageClass `synology-iscsi-ssd` Retain (#84), NFS export fix for vault PV (#51/#52), node lifecycle (uncordon node2/3, etcd 3/3 agents#282), MetalLB IPAM reservation + BGP advertise (agents#284/#361).
- Root `*.verdify.ai` traefik edge, `*.k3s.verdify.ai` cert+router, Cloudflare DNS + tunnel ingress map, **local split-horizon DNS (#24, unbuilt)**, wildcard TLS cert (#53/#54, James token agents#323), DDNS.
- Cross-VLAN firewall: k3s node → ESP32 `192.168.10.111:6053` (#42/#68), MQTT/HA/Frigate flows (#43), WAN planner egress.
- Physical ESP32 + Noise PSK + the atomic single-writer setpoint cutover (network-infra#40, agents#303) — Jason hardware.
- GCP IAM fix (re-enable `iris-agent@verdify` SA) to inventory/clean residual GCP billing.

---

## E. THE FIVE HARD STOPS (Jason/operator-scoped)
G6 (staging never writes device) · Phase-1 firewall raw-socket proof · G-DB-4 (no promotion vs unvalidated DB, #85) · **G9 atomic single-writer ESP32 handoff** (rollback = scale k3s ingestor→0 + `systemctl start verdify-ingestor` on VM) · Gate 31 (irreversible VM destroy, #91).


---

## Appendix — structured backlog (machine index)

### Epics
- **[verdify] EPIC: Three-env model — author verdify-dev + finalize prod overlay shape** — Create deploy/k8s/overlays/dev mirroring staging safety posture (VERDIFY_DEVICE_WRITE_ENABLED=0, deny-esp32-egress, ingestor SHADOW_MODE+replicas:1), finalize prod overlay with real digests, and define per-env DB seeding. Replace staging's blunt replicas:0 with SHADOW_MODE=1 once PR #101 lands. This is the single largest design delta (dev does not exist anywhere today).
    - Author overlays/dev (namespace verdify-dev, device-write=0, deny-esp32-egress, ingestor SHADOW_MODE)
    - Upgrade staging ingestor replicas:0 -> replicas:1 + SHADOW_MODE=1 after #101 merge
    - Define dev/stage DB seeding (subset or full restore from VM dump)
    - Replace prod placeholder digests with promote-same-digest from staging (#86)
    - Author child ArgoCD Application YAML for verdify-local-dev and verdify-local-prod
- **[verdify] EPIC #69/#15: CI/CD pipeline green (commit -> in-org GHCR -> digest bump -> ArgoCD, all repos)** — Close the any-Verdify-code-change -> k3s loop for every repo. Platform path is wired (container-publish + k8s-manifests digest write-back + promote-diff-guard); remaining: the promotion-dispatch sink (verdify-gitops-dev-test-promotion.yml is BROKEN/unbuilt in jvallery/agents), www GHCR+ArgoCD path, planner deploy step, orphaned verdify-migrate package (#99).
    - Finish k8s-manifests digest write-back coverage (#78)
    - promote-diff-guard + autonomous prod-promote (#82, delivered f332265)
    - Delete orphaned verdify-migrate GHCR package, repo-link publish (#99)
    - Author verdify-www GHCR build + overlay + ArgoCD app (#103)
    - Add verdify-planner deploy step or designate Cloud Run keep
    - Author promotion workflow body (sink wiring is out-of-lane)
- **[verdify] EPIC #71/#73: Device-safety interlock + atomic single-writer prod cutover** — The 3-way single-writer guarantee (ConfigMap flag + NetworkPolicy egress + ingestor process gate) across 3 ingestors / 1 ESP32, then the atomic VM->k3s setpoint-write handoff. HARD STOP: only one process may hold VERDIFY_DEVICE_WRITE_ENABLED=1 at the instant of cutover. Also model the second writer verdify-setpoint-server :8200.
    - Make device-write-gate CI test PR-blocking (#79)
    - Re-run VLAN-10 egress spike under live load; uncomment allow-ingestor-device-egress (#87)
    - Model verdify-setpoint-server :8200 second writer in k3s (agents#304)
    - Reconcile canonical ESP32_API_KEY without re-flash (#105)
    - G9 atomic handoff choreography (safety-checked)
- **[verdify] EPIC #72/#28: DB migration (the long pole)** — Copy authoritative VM TimescaleDB into per-env k3s PVCs with parity validation (G-DB-4 HARD gate). Pin TimescaleDB >=2.25.2-pg16 everywhere (no downgrade #57). Move staging DB off local-path onto synology-iscsi Retain (#84).
    - psql abstraction scripts/lib/psql-verdify.sh (#24, in PR #101)
    - Recreate verdify-db StatefulSet on synology-iscsi Retain (#84, needs out-of-lane SC)
    - DB load+validate 8-query parity G-DB-4 (#85)
    - pg_basebackup physical replica -> promote OR dump-restore prod (#28)
- **[verdify] EPIC #88/#63: Web / content / observability tier into k3s** — lab.verdify.ai (verdify-site Quartz+vault baked image), graphs.verdify.ai (grafana+renderer), umami/goaccess/loki, hermes-iris pod, CronJobs from systemd timers, www.verdify.ai (#103). None in deploy/k8s today; web tier authored in PR #101.
    - Merge verdify-site Dockerfile.k3s + site-deployment + ingressroute (PR #101)
    - grafana/umami/goaccess/loki manifests (PR #101)
    - hermes-iris pod (PVC + Secrets + MCP ClusterIP DNS)
    - CronJobs from 8 systemd cron + 3 timer units; fix FAILED plan-publish (#59)
    - verdify-www decision + move (#103)
- **[verdify] EPIC #74: Repo consolidation + VM decommission (irreversible)** — Prevent data loss before VM dies: fold planner_graph (#102), commit 64 vault pages (#104), preserve verdify-agent-context, then compose-down/keep-volumes and retire VM (Gate 31, #91).
    - Fold verdify-planner planner_graph + memory migration + evals into monorepo (#102)
    - Commit verdify-vault working tree 64 pages (#104)
    - Retire host verdify-api.service :8300 (#61)
    - VM compose down (keep volumes), Gate 31 destroy (#91)
- **[out-of-lane] OUT-OF-LANE EPIC: Cluster substrate + storage + secrets backend** — Everything under jvallery/k3s-cluster + agent-fleet-control that the Verdify push sits on: synology-iscsi-ssd StorageClass Retain, NFS export fix, etcd 3/3, node uncordon, MetalLB IPAM+BGP, SOPS/age sealing+custody, secret-sync enum arms, ArgoCD AppProjects+roots+install.
    - synology-iscsi-ssd StorageClass volume1 Retain (#84)
    - Fix NFS export for vault PV (#51/#52)
    - etcd back to 3/3 + uncordon node2/3 (agents#282)
    - UDM FRR BGP fix for VIP reachability (agents#284/#361)
    - SOPS seal new keys incl ESP32 reconcile (#105/#66/#334)
    - local-k8s-secret-sync enum arms verdify-dev/verdify-prod
    - AppProject whitelists + local-prod-apps.yaml root + ArgoCD prod app (#86, agents#298)
- **[out-of-lane] OUT-OF-LANE EPIC: Edge + dual DNS (Cloudflare + local split-horizon, no-SPOF) + cross-VLAN firewall** — Root *.verdify.ai traefik edge, *.k3s.verdify.ai cert+router decision, Cloudflare tunnel ingress map, the UNBUILT local split-horizon DNS (#24), wildcard TLS DNS-01 (James token agents#323), and the cross-VLAN firewall allow k3s node -> ESP32 :6053 + MQTT/HA/Frigate flows.
    - Cross-VLAN firewall k3s node .35 -> ESP32 .10.111:6053 (network-infra#42/#68)
    - Cluster->Infra flows MQTT:1883 + HA:8123 + WAN planner (network-infra#43)
    - Split-horizon DNS *.verdify.ai/*.k3s.verdify.ai -> .7.10 (#53, decision #24, UNBUILT)
    - Wildcard cert cert-manager DNS-01 (network-infra#54, James CF token agents#323)
    - Add verdify.ai hostnames to cloudflared tunnel (#293)
    - DDNS + lower gateway.verdify.ai TTL
- **[out-of-lane] OUT-OF-LANE EPIC: Physical ESP32 single-writer cutover + GCP cleanup** — Hardware-owner Jason-run steps: the atomic device-write handoff (network-infra#40, agents#303), Noise PSK/OTA sealing without re-flash, and re-enabling iris-agent@verdify SA to inventory/clean residual GCP SaaS billing.
    - ESP32 single-writer device cutover (network-infra#40, never automated)
    - ESP32 PSK/OTA-password sealing (must not re-flash)
    - Re-enable iris-agent@verdify SA; inventory+clean dead Cloud SQL/GCE/PubSub billing

### Sprints

**Sprint 1 (now): Land staged work + author dev env — Merge the validated-but-staged PR #101 and create the third environment (verdify-dev) as code, so all three envs exist as kustomize overlays with the read-only-ingest enabler in place.**

Exit criteria:
- PR #101 merged (SHADOW_MODE + healthz + psql sweep + web/obs manifests + prod overlay completeness)
- deploy/k8s/overlays/dev authored (verdify-dev ns, device-write=0, deny-esp32-egress, ingestor SHADOW_MODE+replicas:1)
- Staging ingestor flipped replicas:0 -> replicas:1 + SHADOW_MODE=1 (read-only ingest proven, zero DB/device writes)
- PR #93 roadmap/state docs merged
- kustomize build + kubeconform-strict clean for all 3 overlays

Work items:
- Merge #101 (#25/#24/#88/#86 overlay), #93
- Write overlays/dev mirroring staging safety posture
- Upgrade staging replicas:0->SHADOW_MODE per #25
- Author child ArgoCD Application YAML for verdify-local-dev + verdify-local-prod (handoff to fleet)

**Sprint 2: Substrate readiness + storage (gate on out-of-lane) — Get the cluster substrate healthy enough to host stateful, durable per-env DBs and to deliver secrets. Mostly waiting on out-of-lane, with MINE producing the YAML declarations.**

Exit criteria:
- synology-iscsi-ssd StorageClass exists (out-of-lane #84); verdify-db StatefulSet recreated on it (Retain) in staging
- etcd 3/3 + node2/3 uncordoned (out-of-lane agents#282/#284)
- local-k8s-secret-sync enum gains verdify-dev/verdify-prod arms (out-of-lane); secrets seal+decrypt in those ns
- TimescaleDB pinned >=2.25.2-pg16 across overlays (#57)
- ESP32_API_KEY drift reconciled without re-flash (#105)

Work items:
- MINE: db-storage.yaml synology-iscsi-ssd for staging+dev+prod; psql-verdify.sh in use (#24)
- OUT-OF-LANE handoff: SC, etcd, secret-sync arms, SOPS sealing (#84/#105/#66)
- Author AppProject whitelist + local-prod-apps.yaml handoff doc

**Sprint 3: dev env live + per-repo CI/CD (www + planner) — Stand up verdify-dev with its own DB copy + read-only shadow ingestor, and bring www + planner onto the GHCR->ArgoCD pattern.**

Exit criteria:
- verdify-dev running: db (seeded copy) + api + mcp + ingestor(SHADOW_MODE) all Healthy; collecting fresh telemetry, zero writes
- ArgoCD verdify-local-dev app Synced+Healthy (out-of-lane AppProject+CR done)
- verdify-www builds GHCR image + has overlay + ArgoCD app (decision #103 resolved)
- verdify-planner has a deploy step OR is formally designated Cloud Run keep
- planner_graph folded into monorepo (#102); vault 64 pages committed (#104)

Work items:
- Seed dev DB from VM dump; bring up verdify-dev workloads
- verdify-www CI -> GHCR + overlay + IngressRoute (#103)
- verdify-planner CI deploy or keep-decision
- #102 planner_graph fold, #104 vault commit (data-loss prevention)

**Sprint 4: Edge + dual DNS + IngressRoutes (gate on out-of-lane) — Every *.verdify.ai (prod) and *.k3s.verdify.ai (dev/stage) serves through the shared apps-ingress VIP .7.10, resolving at BOTH Cloudflare and locally (no SPOF). MINE = IngressRoute YAML; the edge/DNS/TLS plane is out-of-lane.**

Exit criteria:
- IngressRoutes authored for api/lab/graphs/www behind .7.10; per-app LB .7.21 retired (ETP Cluster)
- Wildcard TLS cert issued (out-of-lane #54, James token agents#323)
- Split-horizon local DNS resolves *.verdify.ai/*.k3s.verdify.ai internally when Cloudflare is down (out-of-lane #24/#53 — UNBUILT today)
- Cloudflared tunnel ingress map carries verdify.ai hostnames (out-of-lane #293)
- Cross-VLAN firewall allows k3s -> ESP32:6053 + MQTT/HA/Frigate (out-of-lane #42/#43/#68)

Work items:
- MINE: IngressRoute manifests for all 4 prod URLs + dev/stage scheme; drop LB
- OUT-OF-LANE: split-horizon DNS, wildcard cert, tunnel map, cross-VLAN firewall (#53/#54/#42/#43)
- Decision: *.k3s.verdify.ai vs reuse *.k3s.vallery.net

**Sprint 5: prod stand-up (read-only) + DB copy + parity — Bring verdify-prod fully up EXCEPT device write: db (authoritative copy + G-DB-4 parity), api, mcp, ingestor still in shadow/write-disabled. Prod mirrors stage exactly minus the single-writer flip.**

Exit criteria:
- verdify-prod workloads Running; ArgoCD verdify-local-prod Synced+Healthy (manual-sync, prune:false)
- Prod DB = authoritative VM copy, G-DB-4 8-query parity PASS (#85)
- Prod ingestor running read-only (device-write=0 still); allow-ingestor-device-egress applied but NOT yet writing
- G10 smoke + device-route ESTAB monitor green (#89)
- promote-diff-guard confirms prod digests == staging-validated

Work items:
- pg_basebackup/dump-restore prod DB + validate (#28/#72/#85)
- Bootstrap prod workloads via ArgoCD (#86)
- Apply allow-ingestor-device-egress; verify reachability, NO write

**Sprint 6: Atomic single-writer cutover (HARD STOP, Jason-run) — Hand ESP32 setpoint-write ownership from the VM systemd ingestor to the prod k3s ingestor — exactly one writer at the instant of cutover. Track A safety dominates.**

Exit criteria:
- VM verdify-ingestor.service stopped (VERDIFY_DEVICE_WRITE_ENABLED unset)
- prod k3s ingestor flipped to VERDIFY_DEVICE_WRITE_ENABLED=1, replicas:1, writing setpoints
- Twin divergence trustworthy (#31/#34/#33/#32); first live setpoint push validated with exact readback and rollback staged
- verdify-setpoint-server :8200 second-writer re-homed or accounted for (agents#304)
- Rollback path proven: scale k3s ingestor->0 + systemctl start verdify-ingestor on VM

Work items:
- G9 atomic handoff choreography (safety-checked, network-infra#40, agents#303)
- Model/re-home setpoint-server :8200
- Twin trust gates #31/#34

**Sprint 7: web/obs/cron full cutover + VM decommission — Move the last VM workloads (grafana/umami/goaccess/loki, hermes-iris, cron fleet, lab/forecast pipeline) into k3s, then decommission the VM (irreversible Gate 31).**

Exit criteria:
- graphs.verdify.ai (grafana) + analytics/logs serving from k3s; grafana_data/umami_db copied
- hermes-iris pod live (state copied); 8 cron + 3 timer units are CronJobs; FAILED plan-publish fixed (#59)
- lab.verdify.ai served from baked verdify-site image; forecast/site-poll pipeline in k3s
- Host verdify-api.service :8300 retired (#61)
- VM compose down (volumes kept); Gate 31 VM destroy (#91, safety-checked)

Work items:
- Cutover obs tier + copy grafana/umami volumes
- hermes-iris pod + state copy; CronJobs (#59)
- Retire VM units; #91 decommission

### Out-of-lane handoffs
- **[laptop-root / jvallery/k3s-cluster + agent-fleet-control (#84)]** synology-iscsi-ssd StorageClass (volume1, Retain) + recreate verdify-db StatefulSet on it across dev/stage/prod — _needed for:_ Durable per-env DB; without it prod DB + obs PVCs stay Pending and staging DB sits on ephemeral local-path
- **[laptop-root / jvallery/agents (#86, agents#298/#301-307)]** ArgoCD AppProject whitelists for verdify-platform.git + ns verdify-dev/verdify-prod; create local-prod-apps.yaml app-of-apps root + app-prod AppProject; install/bootstrap verdify-local-dev and verdify-local-prod Application CRs (manual-sync, prune:false) — _needed for:_ dev + prod environments to be ArgoCD-managed at all
- **[laptop-root / jvallery/agent-fleet-control (#30/#66/#105/#334)]** Extend local-k8s-secret-sync.yml target enum with verdify-dev/verdify-prod arms (namespace + runtime_secret ghcr-verdify-readonly + image_pull_secret); SOPS/age sealing of new keys incl ESP32_API_KEY reconcile (#105 — must NOT trigger re-flash), GRAFANA_ADMIN_PASSWORD, UMAMI_DB_PASSWORD/APP_SECRET; age private-key custody — _needed for:_ App secrets to mount before ArgoCD reconciles in the new namespaces
- **[laptop-root / jvallery/k3s-cluster (agents#282/#284/#361/#308)]** Substrate health: etcd back to 3/3 (flapping), UDM FRR BGP fix, uncordon node2/node3, MetalLB apps-pool IP reservation (.7.21 is a placeholder) + BGP advertise with ETP Cluster — _needed for:_ Stable scheduling + durable off-cluster VIP reachability for api
- **[network-infra / nexus (#42/#43/#68)]** Cross-VLAN firewall allow: k3s node -> ESP32 192.168.10.111:6053; Cluster->Infra MQTT 192.168.30.107:1883 + HA :8123 + Frigate :5000; WAN planner egress; durable pinned route — _needed for:_ Prod ingestor to reach and write the device; all envs to ingest telemetry (NetworkPolicy YAML alone does not grant reachability)
- **[network-infra / nexus + laptop-root (#53/#54/#67/#90/#293); James CF DNS:Edit token (agents#323)]** Split-horizon local DNS (#24, UNBUILT — pihole substrate exists) resolving *.verdify.ai + *.k3s.verdify.ai -> 192.168.7.10 internally; *.k3s.verdify.ai wildcard cert (or reuse *.k3s.vallery.net); cert-manager wildcard-verdify-ai DNS-01; add verdify.ai hostnames to cloudflared tunnel; DDNS + lower gateway TTL — _needed for:_ The no-SPOF requirement (Cloudflare-down still serves locally) and the *.k3s.verdify.ai dev/stage scheme
- **[Jason (hardware) / network-infra#40 + agents#303]** Physical ESP32 single-writer device cutover (the live setpoint-write handoff) + Noise PSK / OTA-password sealing (must not re-flash) — _needed for:_ G9 atomic single-writer prod cutover
- **[laptop-root / jvallery/agents (#69)]** Build the promotion-dispatch sink: verdify-gitops-dev-test-promotion.yml in jvallery/agents + set AGENT_FLEET_PROJECT_TOKEN (the platform request-gitops-promotion job is a no-op today; the workflow does not exist) — _needed for:_ CI/CD to actually deploy dev->stage->prod (currently the dispatch self-skips)
- **[Jason / GCP project owner]** GCP IAM: re-enable iris-agent@verdify SA (or interactive jason@verdify.ai login); inventory + clean residual dead SaaS-mirror billing (Cloud SQL/GCE Mosquitto/PubSub/Firebase) — _needed for:_ Confirming/stopping stale GCP spend; the SAAS mirror is superseded (#53 closed)
- **[laptop-root (#99)]** Delete orphaned verdify-migrate GHCR package so it publishes repo-linked — _needed for:_ Clean digest automation for the migrate image

### Open decisions (Jason)
- DNS scheme for dev/stage: reuse the proven *.k3s.vallery.net edge (zero new cert/router/DNS work, already live) OR build the parallel *.k3s.verdify.ai edge as stated in the target (consistent branding, net-new wildcard cert SAN set + HostRegexp router + Cloudflare records, all out-of-lane).
- Read-only telemetry mechanism for dev/stage: (a) dev/stage ingestors open their OWN read-only native ESPHome API connections to the ESP32 (3 simultaneous sockets to one device — a connection-count risk) vs (b) prod alone holds the single ESP32 native-API socket and dev/stage ingest only via MQTT/HA. Recommend (b) for the safest single-writer posture; confirm.
- verdify-www (#103): serve the Astro consulting apex from k3s (new overlay + IngressRoute, full move off Cloud Run) OR keep it on Cloud Run and have www.verdify.ai redirect-to-lab. The apex currently decoupled-from-house (Google A record) survives home outages — moving it into k3s reintroduces home-dependency for the marketing front door.
- verdify-planner: fold planner_graph into the monorepo and run as a k3s service per-env, OR keep it on Cloud Run (project buoyant-valve-496719-m0, gpt-5.5) as a deliberate stateless remote gateway. The fold (#102) is required regardless to prevent data loss before VM decommission; the runtime location is the decision.
- DB cutover technique for prod: physical pg_basebackup -> promote replica (compute-first/data-last, #28) vs the simpler dump-restore snapshot already proven in staging. Long-pole risk vs simplicity.
- Whether to retire the per-env separate DB in favor of a single shared TimescaleDB with per-env schemas (Jason's target says each env has its OWN DB copy — assumed three separate StatefulSets; confirm this is intended over a shared instance for cost/storage).
- Timing of the G9 atomic single-writer ESP32 handoff and the irreversible VM destroy (Gate 31, #91) — both are hardware/Track-A HARD STOPS must be explicitly scheduled with rollback, contingent on twin-divergence trust (#31/#34) being established.
- Whether stale GCP SaaS-mirror resources (Cloud SQL/GCE Mosquitto/PubSub/Firebase) should be deleted now to stop billing — requires the GCP IAM access fix first; #53 is closed but resources may still exist.

---

## CONFIRMED DECISIONS (Jason, 2026-05-31) — these SUPERSEDE the recommendations above

1. **Telemetry = MQTT fan-out bus (single bidirectional device owner).** `api.verdify.ai` (prod, via the ingestor/dispatcher) is the ONLY thing that talks to the greenhouse controller **bidirectionally**. The **prod ingestor publishes ALL telemetry** — every source (ESP32, Tempest, Shelly, etc.), not just the ESP32 — as **MQTT topics**. **dev + stage SUBSCRIBE to the production MQTT topics** (read-only) for all their data. **No Home Assistant** in the dev/stage path.
   - *Build impact:* model the **MQTT broker in k3s** (prod, reachable cross-env); add a prod "publish-all-sources→MQTT" ingestor mode + a dev/stage "subscribe-from-prod-MQTT" ingest mode; the single bidirectional ESP32 path is prod-only. This replaces the earlier "dev/stage via MQTT/HA" rec — it's MQTT-only, and prod is the canonical publisher of *all* telemetry.
2. **verdify-www → MOVE INTO k3s** (off Cloud Run). `www.verdify.ai` served from the cluster: GHCR image + overlay + IngressRoute. (Accepts home-dependency for the marketing front door.)
3. **verdify-planner → RUN IN k3s, per-env** (off Cloud Run). Fold `planner_graph` into the monorepo (#102) **and** run it as a k3s service in each env.
4. **3 separate TimescaleDB StatefulSets** — one per env, each with its own DB copy.

**Implication: GCP fully EXITS for Verdify** once www + planner migrate → decommission the Cloud Run services + any GCP SaaS-mirror resources (requires the `gcloud` re-auth / IAM fix to enumerate + delete).

**Defaults locked (override anytime):** dev/stage URLs on `*.k3s.verdify.ai` (per the stated target, net-new edge = out-of-lane); prod DB cutover via **dump-restore** (proven in staging, simpler than pg_basebackup).
