# Verdify — Authoritative State + Cutover Sprint

**For:** Jason · **By:** Iris · **Date:** 2026-06-03
**Tag legend:** `[verified]` = confirmed from repo/IaC reads · `[IaC]` = declared in IaC, runtime not probed · `[needs-LIVE-confirm: <owner>]` = requires a live vantage I don't have.

**One-line truth:** k3s is **additive staging**, not cut over. The greenhouse is still **100% run from the iris VM (.150)**. No prod k8s namespace, no prod DB, no historical data in-cluster, no device traffic via k8s. The edge still terminates everything at the VM. Everything below is concrete; anything I can't physically see is flagged with its owner.

---

# PART A — INVENTORY

## A1. Full container/service set + which env defines each + which are applied

**BASE (renders into every overlay)** `[verified]` — `deploy/k8s/base/`:
| Service | Kind | Port | Notes |
|---|---|---|---|
| verdify-api | Deployment + ClusterIP | 8080 | replicas 1, RollingUpdate |
| verdify-mcp | Deployment + ClusterIP | 8000 | MCP_HTTP_HOST=0.0.0.0 |
| verdify-ingestor | Deployment, **no Service** | — | connect-out worker, holds the single ESP32 conn, **Recreate** (single-writer invariant) |
| verdify-db | StatefulSet + Svc | 5432 | `timescale/timescaledb:2.25.2-pg16` |
| verdify-migrate | Job (PreSync hook) | — | schema-only replay |
| verdify-config | ConfigMap | — | |
| networkpolicy | NetworkPolicy | — | default-deny-ingress + 5 allows |

**COMPONENTS (kustomize Components, per-overlay opt-in, NOT in base)** `[verified]`: verdify-planner (8080), verdify-setpoint-server (8200, grow-light writer), verdify-www (8080, Astro/Node), verdify-lab (8080, Quartz/nginx), verdify-mqtt (eclipse-mosquitto:2, 1883), verdify-hermes-iris (8642 + 5Gi PVC).

**Per-env component wiring** `[verified]`: **dev** = planner + www + lab; **staging** = www **only**; **prod** = planner + mqtt + setpoint-server + hermes-iris + www + lab.

**What is actually APPLIED (live ArgoCD App):**
- **Applied & live in `verdify-local-staging`** (Synced+Healthy per runbook, 3-day-old): api, mcp, db, migrate (PreSync), www, config. **Ingestor renders but is pinned `replicas:0`** (never running). `[IaC]` / `[needs-LIVE-confirm: Root]` for current health.
- **Applied via `verdify-dev`** (manual-sync since 2026-06-02): api/mcp/db/www come up; ingestor pinned 0 out-of-band; **planner + lab `ImagePullBackOff`** (placeholder digests). `[IaC]` / `[needs-LIVE-confirm: Root]`.
- **Authored but NOT applied (no live App):** all prod-only workloads — setpoint-server, mqtt, hermes-iris, and the prod copies of planner/www/lab. The `verdify-prod` App CR exists only as `jvallery/agents .../_staged/verdify-prod.GATED-device-writer.STAGED.yaml`, not referenced by any app-of-apps root. `[verified]`
- **Unpublished images:** verdify-planner, verdify-setpoint-server, verdify-lab → GHCR 404 → placeholder `sha256:0000…` digests. `[verified]`

## A2. DB migration into k8s — where we are

- **Staging in-cluster `verdify-db`** is **SCHEMA-ONLY, empty of telemetry.** The only thing that has run is the PreSync migrate Job (replays `db/schema.sql` + migration 000). Storage was moved off local-path onto `synology-iscsi-ssd` (PR #121 / 4024f4d, issue #84). Holds no rows. `[IaC]` / `[needs-LIVE-confirm: Root]` for `climate count(*)=0`.
- **No prod DB exists in-cluster.** Prod overlay is fully authored but inert; prod still inherits **base `local-path`** (iSCSI patch is staging-only, by design). `[verified]`
- **System of record = live VM TimescaleDB** (`verdify-timescaledb` on .150): timescaledb 2.25.2, **81 base tables, 132 views (3 matviews), 19 hypertables, 11 background jobs, 5 compressed hypertables, ~1601MB**; largest `setpoint_snapshot` ~6.35M rows. Read-only baseline captured 2026-06-01. `[verified]` (repo-recorded capture) / `[needs-LIVE-confirm: Root]` for today's exact counts.
- **Zero historical data has been loaded into any k8s DB.** `db/restore-job.yaml` (the human-gated, non-ArgoCD restore) has **never run** — `DUMP_FILE` is still `verdify-PLACEHOLDER.dump`. A 2026-05-31 dry-run validated the *mechanism* only (pg_dump -Fc → pre_restore → pg_restore → post_restore; gotcha: matviews must be `REFRESH`'d post-restore). `[verified]`
- **Parity method:** `scripts/db-parity.sh`, read-only, **9 dimensions** (tables/views, extensions, hypertables, continuous aggregates, background jobs, row-counts by RPO window, max timestamps with skew tolerance, compression set, restore recency). Exit 0 = full parity. Has run only in **self-parity smoke** + a divergent fixture — **never iris-vs-real-k8s-target** (no populated target exists). `[verified]`
- **Applied-migrations ledger:** designed (`db/ledger/schema_migrations.sql`, PK=(source,filename) because 157 migration files have duplicate numbers + gaps; 158 backfill stamps) but **not live in any DB.** `[verified]`
- **Known blockers (G1 is hard):** G1 — `db/restore-job.yaml` still pins client `2.17.2-pg16` (lines 72/115/151) while the StatefulSet is `2.25.2-pg16`; a 2.25→2.17 restore is unsupported, must reconcile. G2 — only 4 of 19 hypertables repaired by migration 000; the other 15 land as plain tables. G3 — compression/retention policy jobs not recreated. G4 — matviews empty post-restore (handled by REFRESH). `[verified]`

## A3. Which services made it over vs not

| Service | k8s authored? | Applied/live? | Status |
|---|---|---|---|
| verdify-api | yes (base) | staging+dev | real pullable digest `ce4ca79f` `[verified]` |
| verdify-mcp | yes (base) | staging+dev | real digest `98240200` `[verified]` |
| verdify-db | yes (base) | staging+dev | schema-only, on iSCSI (staging) `[IaC]` |
| verdify-migrate | yes (base) | staging+dev (PreSync) | real digest `7a39c3de`; **no longer orphaned** (#78 resolved) `[verified]` |
| verdify-www | yes (component) | staging+dev | **GHCR 404** on pinned `5e00bc20` → would ImagePullBackOff `[verified]` |
| verdify-ingestor | yes (base) | rendered, **pinned 0** | device-dark everywhere except prod `[verified]` |
| verdify-planner | yes (component) | dev (placeholder) | image unpublished → ImagePullBackOff `[verified]` |
| verdify-setpoint-server | yes (component) | **prod-only, not applied** | image unpublished (sha256:0000) `[verified]` |
| verdify-lab | yes (component) | dev/prod (placeholder) | **unbuildable orphan** (#124) — wrong repo ref `[verified]` |
| verdify-mqtt | yes (component) | **prod-only, not applied** | inert `[verified]` |
| verdify-hermes-iris | yes (component) | **prod-only, not applied** | inert; upstream digest preserved `[verified]` |

**Still VM-only / the real production control plane:** host systemd units on .150 — verdify-ingestor, verdify-setpoint-server (:8200, the *sole* ESP32 writer), verdify-mcp (:8000). k3s is additive-staging; **no prod cutover has occurred.** `[verified]`

## A4. prod / stage / dev — exact definitions `[verified]`

| Axis | **dev** (ns verdify-dev) | **staging** (ns verdify-staging) | **prod** (ns verdify-prod) |
|---|---|---|---|
| `VERDIFY_DEVICE_WRITE_ENABLED` | **0** | **0** | **1** (set here and ONLY here) |
| Ingestor replicas | file says 1 (subscribe mode) **but pinned 0 out-of-band** | **0** (two-writer safety pin) | base **1**, Recreate, no pin (the single real writer) |
| Ingest source | `mqtt-subscribe` | (staging default) | live ESP32 native-API |
| Device egress NetPol | `deny-esp32-egress` (drops 192.168.10.0/24) | `deny-esp32-egress` | `allow-ingestor-device-egress` (permits ESP32 :6053, HA :8123/:1883, Frigate :5000/:1984) |
| DB storage | synology-iscsi-ssd | synology-iscsi-ssd | **base local-path** (until gated cutover) |
| URLs | `*.k3s.verdify.ai` | `verdify.vallery.net` / `www-staging.vallery.net` | bare `*.verdify.ai` |
| MQTT publish | — | — | `VERDIFY_MQTT_PUBLISH_ALL=1` (fan-out publisher) |
| Applied? | manual-sync (06-02 device-safety override) | **live, automated selfHeal** | **not applied** (gated) |

All three pin namespace explicitly; base carries no namespace.

## A5. How this ties to IaC / CI `[verified]`

Push to `live/platform-main` (verdify-platform) → `container-publish.yml` builds+publishes the six Python images to GHCR → **`bump-staging-digests`** rewrites `overlays/staging/kustomization.yaml` image pins to exact `@sha256` (and repins the inert dev planner), commits back as `verdify-ci[bot]` with `[skip ci]` → ArgoCD `verdify-local-staging` (targetRevision `live/platform-main`, path `overlays/staging`) reconciles. Loop broken by `paths-ignore` of overlays. Prod digests are a **separate human-gated copy** guarded by `promote-diff-guard.yml` (prod must == staging, digest-only lines). `k8s-manifests.yml` renders every overlay through kustomize+kubeconform as a pre-Argo gate. `request-gitops-promotion` is a documented **safe no-op** (token unset).

**Important correction to the 2026-06-01 SotU:** the two "top MINE gaps" are **resolved** — the digest write-back **is now firing** (8+ `verdify-ci` commits, latest `7324af2`→`ff2a4565`) and **k8s-manifests.yml passes** on every recent push (#126 fix landed). `[verified]`

**Live GitOps SoT is `jvallery/agents`** (not verdify-platform's `argocd/apps/`, which are handoff copies). Two app-of-apps roots reconcile `applications/local-{staging,dev}`. `[verified]` / `[needs-LIVE-confirm: Root]` for live sync status.

## A6. What is STILL running on the iris VM (.150) `[verified]`

**Host:** `vm-docker-iris` = Proxmox VMID 306, **192.168.30.150**, on `onyx`, 8 GiB. `[IaC]` (2026-05-24 placement) / `[needs-LIVE-confirm: Root]`.

**14 docker-compose services:** traefik (:443), **timescaledb (authoritative, 127.0.0.1:5432)**, grafana + grafana-renderer + grafana-proxy, mqtt (:1883), api (FastAPI, :8080), verdify-site (Quartz), umami + umami-db, goaccess + goaccess-site, promtail, hermes-iris (profile-gated, :8642).

**13 host systemd units:** verdify-ingestor (ESP32→TSDB), verdify-mcp (:8000), verdify-api (:8300 uvicorn), **verdify-setpoint-server (:8200 — the SOLE ESP32 writer)**, plus forecast-page/render-cache/site-poll/site-build/plan-publish timers+oneshots; cron (db-backup 1AM, snapshots, vault writers, metrics) + logrotate.

**Authoritative prod here:** setpoint-server (sole writer to ESP32 .111), ingestor (telemetry + device-write gate), timescaledb, mqtt, mcp. **Note: the real 5 s control loop runs ON the ESP32 firmware**, not the VM — the VM only ingests + dispatches setpoints. `[needs-LIVE-confirm: Jason]` for live `docker ps`/`systemctl status` and whether hermes-iris (profile-gated) is up.

## A7. Where the ESP32 is currently pointed `[verified]`

**The ESP32 has NO outbound backend connection.** It runs the ESPHome native API as a **TLS-encrypted SERVER on 192.168.10.111:6053** (Noise PSK) on device VLAN 192.168.10.0/24. Direction is **ingestor → ESP32**: the VM ingestor (`aioesphomeapi` client) dials *out* to the device. Device speaks only ESPHome native API + ESPHome OTA + SNTP. **No on-device HTTP client. Zero references to api.verdify.ai / 192.168.7.x / http_request anywhere in firmware.** Endpoint is DB-overridable (`greenhouses.esp32_host/port`) but the `.111:6053` default is live. `[needs-LIVE-confirm: Root/Jason]` whether .111:6053 is answering now + current firmware version.

**Single-writer posture:** all device writes funnel through `push_to_esp32()` (`esp32_push.py`), guarded by SHADOW_MODE + the `_device_writes_enabled()` env gate (default-deny). staging/dev are device-dark via a **3-layer interlock** (replicas:0 + `WRITE_ENABLED=0` + `deny-esp32-egress` NetworkPolicy). Prod is the inverse (`WRITE_ENABLED=1` + `allow-ingestor-device-egress`). Only ever one of allow/deny applies. `[verified]`

## A8. Which is prod / stage / dev (env table)

See **A4**. Short form: **dev** = ephemeral mqtt-subscribe sandbox on `*.k3s.verdify.ai`, device-dark; **staging** = the only live-automated k8s env, www-only public surface on `verdify.vallery.net`, device-dark, schema-only DB; **prod** = the only device-writer (`WRITE_ENABLED=1`), bare `*.verdify.ai`, **not yet applied/gated**.

## A9. URL table — host → env → LAN target → WAN path

**WAN path for ALL today** = Internet → Cloudflare (grey/dns-only except `*.verdify.ai` orange) → A **8.44.158.103** (NextLight DHCP) → UDM portforward 80/443/3443 → **edge Traefik VM 192.168.30.100** → reverse-proxy to **iris .150:443**. The cloudflared tunnel is **NOT cut over.** `[verified]`

| Host | Env (live) | LAN target | WAN path |
|---|---|---|---|
| verdify.ai (apex) | prod (VM) | CF A 216.239.x.21 **Google Sites** (apex doesn't hit the house) | Cloudflare → Google |
| www.verdify.ai | prod (VM) | CF CNAME → ghs.googlehosted.com (**Google Sites / Cloud Run**) | Cloudflare → Google |
| api.verdify.ai | prod (VM) | edge .100 → **iris .150:443 → verdify-api docker :8080** | grey CNAME→gateway→.103→edge |
| auth.verdify.ai | prod (VM) | edge → authentik@docker (on .100) | grey → edge |
| botauth.verdify.ai | prod (VM) | edge → 192.168.30.152:8788 | grey → edge |
| analytics.verdify.ai | prod (VM) | edge → iris .150:443 (umami; Authentik-gated) | grey → edge |
| logs.verdify.ai | prod (VM) | edge → iris .150:443 (goaccess; Authentik) | grey → edge |
| graphs / lab / labs.verdify.ai | prod (VM) | edge wildcard → iris .150:443 | grey → edge |
| traefik.verdify.ai | prod (VM) | ops dashboard | deliberately never-WAN |
| mqtt.verdify.ai | prod (VM) | raw-TCP → .150 / device lane | grey → edge, not in HTTP tunnel |

**Dead-weight off the public path:** the k3s verdify-api LoadBalancer `.7.21` (ns verdify-staging) and the apps VIP `.7.10` are NOT on any live route. The k3s 3-env target table (www/api `.verdify.ai` prod, `*.k3s.verdify.ai` dev, `*.vallery.net` staging → traefik-apps `.7.10`) is **INERT-ON-MERGE.** `[verified]`

## A10. How verdify.ai DNS maps to Pihole / where the local zone lives `[verified]`

**There is NO Pihole and NO live UDM split-horizon/local zone for verdify.ai.** The "LAN → 192.168.7.10" split-horizon is a **target, not implemented** (audit decision #24, undecided). Authoritative DNS for both zones is **100% Cloudflare.** On the UDM-Pro-Max every VLAN has `dhcpd_dns_enabled=false`, no static host_record exists. **Correction to the prior Iris matrix:** the apps VIP `.7.10` (a.k.a. `.7.2` — naming itself unresolved) **does not exist yet** — verified TCP 000 from the edge; MetalLB apps-pool is allocated `autoAssign=false` but no service claims it. `[needs-LIVE-confirm: Nexus]` whether a local zone landed after the 2026-05-29 export.

## A11. How it syncs with Cloudflare `[verified]`

All records are **Cloudflare-authoritative; no local-only records.** A DDNS updater exists in code (`network-infra ddns/updater/ddns_updater.py`, 300s) but is **NOT live** (`--dry-run` default; UniFi `dynamicdns.json` empty). When enabled it PATCHes **only two grey A records**: `vallery.net` apex and `gateway.verdify.ai` (id 7b0e8ff8). Every verdify app CNAME (api/auth/botauth/analytics/graphs/lab/logs/traefik/mqtt) flattens through `gateway.verdify.ai A=8.44.158.103` — **one repoint of `gateway` moves the whole product surface.** Google-anchored + untouched by DDNS: apex A (Google Sites anycast), www→ghs, MX/SPF/DKIM/DMARC. **Only one verdify record is proxied/orange:** `*.verdify.ai CNAME→verdify.ai`; all 9 app CNAMEs are **grey → expose origin .103 directly (no WAF, audit P1).** `lab`/`labs` is a confirmed duplicate. `[needs-LIVE-confirm: Nexus]` for live CF state since 2026-05-29.

## A12. Current cloudflared tunnel config — BOTH domains `[IaC]`

**Tunnel name = `vallery-homelab`** (the prior matrix's `vallery-edge` is **stale/wrong**). Config-as-code (`network-infra ddns/tunnel/config.yml` = in-cluster ConfigMap), deployed as a **2-replica k3s Deployment in ns `cloudflared`** (image cloudflared:2025.5.0, token from Secret, podAntiAffinity, PDB minAvailable:1).

**Correction:** **EVERY ingress rule forwards to `https://192.168.30.34:443`** (the MetalLB **k3s-ingress** VIP / cluster Traefik) — **NOT `192.168.7.10`.** Nothing in the tunnel forwards to .7.10.

- **verdify.ai rules (9):** api, auth, botauth, analytics, graphs, lab, labs, logs, traefik → `.30.34:443`.
- **vallery.net rules (25):** agents, alerts, api, auth, backstage, cameras, chat, cortex, esphome, fleet, gravity, home, langfuse, monitoring, n8n, obsidian, ollama, onyx, orbit, pbs, photos, proxmox, sentinel, vault, www → `.30.34:443`.
- Fail-closed catch-all `http_status:404` last.
- **Excluded:** apex A + mail; ghs CNAMEs; vpn (WireGuard); mqtt.verdify.ai (raw TCP); `*.k3s.vallery.net`.

**STATUS: staged/declared, NOT cut over** — no CF CNAME points to `<UUID>.cfargotunnel.com`; all 34 hostnames are still grey to bare origin. Backup WAN = Starlink CGNAT 100.80.90.13 (inbound-incapable — the reason the tunnel exists). `[needs-LIVE-confirm: Root/Nexus]` whether the tunnel Deployment is actually running, and the real tunnel UUID.

## "Needs a live vantage to confirm" — concrete list + owner

| # | What | Owner |
|---|---|---|
| L1 | `kubectl get pods -n verdify-{staging,dev}` — actual Running/Crash/IPBO/0-0 counts | **Root** |
| L2 | `argocd app get verdify-local-staging verdify-dev` — Synced/Healthy + last revision (did it reconcile to `ff2a4565`?) | **Root** |
| L3 | Confirm staging really sources verdify-platform/overlays/staging (cutover) vs retired agent-fleet-control source | **Root** |
| L4 | Confirm dev ingestor is truly scaled 0 and not attempting :6053 to .111 | **Root** |
| L5 | Staging `verdify-db` truly empty (`climate count(*)=0`), Running 1/1 on iSCSI on a worker | **Root** |
| L6 | `synology-iscsi-ssd` SC on /volume1 + NFS PVC `verdify-db-dumps` exist; nightly backup CronJob producing fresh dumps | **Root** |
| L7 | SOPS secrets present in each ns (`verdify-app-secrets`, `verdify-ha-token`, `verdify-hermes`, `ghcr-jvallery-readonly`) | **Root** |
| L8 | Live VM `docker ps` / `systemctl status` on .150; is setpoint-server actively writing; ESP32 .111:6053 answering + firmware version; live `\dx` ext version (G1) | **Jason** (+ firmware op) |
| L9 | Proxmox VMID 306 placement still current; PBS/snapshot currency as recovery floor | **Root** |
| L10 | Live CF zone state since 2026-05-29; tunnel UUID; cloudflared pods Running in ns cloudflared; any UDM split-horizon landed | **Nexus** (+ Root for kubectl) |
| L11 | `.7.10` vs `.7.2` apps-VIP naming + whether MetalLB/BGP now answers from edge | **Nexus / Root** |
| L12 | cert-manager ClusterIssuer gained a `verdify.ai` DNS-01 solver (today `dnsZones:[vallery.net]` only → no `*.verdify.ai` wildcard cert can issue) | **Root** |
| L13 | Did verdify-www GHCR image ever push (overlays claim pullable 2026-05-31 but package 404s now); is www live on Cloud Run | **Jason / James** |
| L14 | Repo+branch hosting Nexus's `routes/25-verdify-sites-backend.yaml` (not in verdify-platform or network-infra) | **Nexus** |
| L15 | Does api.verdify.ai resolve to a live k3s pod (200) vs VM/404 | **Jason / Root** |

---

# PART B — NEXT SPRINT (to reach all 7 end-state goals)

**End-state goals:** (i) all services in k3s; (ii) iris VM decommissioned; (iii) parity-gated confidence all history moved; (iv) all context CI/CD incl. lab+www collateral; (v) all Python services off api.verdify.ai as containers; (vi) controllers re-pointed to api.verdify.ai with LOCAL route + WAN fallback; (vii) location-independence (Google or local, same experience).

**The master gate (single-writer invariant):** there is exactly ONE device writer at all times. Today it is the VM setpoint-server/ingestor. **Prod k8s becomes the device-writer only at the Jason-gated M5 cutover, after M4 proof, with the VM writer stopped in the same choreography.** No item below may create a second writer. Goal (vi) is a **real re-architecture**, not a re-point, and is sequenced last and on its own track.

**Owners:** Iris (me — repo/IaC/CI/docs/parity authoring, no cluster/device access), Root (laptop-root — kubectl/ArgoCD/SOPS/storage/network/cloudflared), Nexus (edge Traefik/UniFi/Cloudflare/DNS/MetalLB), Jason (gates + secrets/GCP owner), James (verdify-www + lab content/repos).

### Milestone M0 — Close the live-vantage gaps (unblock everything)
| # | Item | Owner | Gate/dep | Acceptance |
|---|---|---|---|---|
| 0.1 | Run L1–L7 + L15 live and record into a `state-truth.md` | Root | none | Pasted `kubectl`/`argocd` output for staging+dev; staging Synced+Healthy on `ff2a4565` confirmed or fixed |
| 0.2 | Run L8 on .150 (docker ps, systemctl, `\dx`, ESP32 probe) | Jason | none | Confirmed VM ext version (G1 decision input) + setpoint-server is sole live writer + .111:6053 answering |
| 0.3 | Run L10–L12, L14 live (CF/UDM/MetalLB/cert-issuer/Nexus repo) | Nexus + Root | none | `.7.10` vs `.7.2` resolved to one value; tunnel pod state known; cert-issuer verdify.ai gap confirmed |

**Iris-doable now:** author `state-truth.md` template + parity/ledger PRs. **Root/Nexus/Jason-gated:** all live reads.

### Milestone M1 — Fix the broken images so dev/prod can ever come up
| # | Item | Owner | Gate/dep | Acceptance |
|---|---|---|---|---|
| 1.1 | Build+push **verdify-www** to GHCR (resolve the 404 vs the `5e00bc20` pin); fold into platform CI so it stops being Cloud-Run-only | James (build) + Iris (CI wiring) | dep 0.1 (confirm 404) | `gh api .../verdify-www` returns the pinned digest; dev www pod Running |
| 1.2 | Fix **verdify-lab** orphan (#124): repoint component to real repo `verdify-site-legacy`, add `Dockerfile.k3s` (nginx-unprivileged:8080), build+push, replace `sha256:000…0` | James (repo) + Iris (component+CI) | none | `verdify-lab` package exists+pullable; dev/prod overlays pin a real digest |
| 1.3 | Publish **verdify-planner** + **verdify-setpoint-server** real digests; replace placeholders (planner stays artifact-only for dev; setpoint stays prod-only scaled-0) | Iris (CI) | none | dev planner pod leaves ImagePullBackOff; prod overlay carries real digests |

**Iris-doable:** 1.3 + CI for 1.1/1.2. **James-gated:** www build + lab repo.

### Milestone M2 — Site collateral fully CI/CD (goal iv)
| # | Item | Owner | Gate/dep | Acceptance |
|---|---|---|---|---|
| 2.1 | Add platform CI to build+push **www** + **lab** images on content change | Iris | dep 1.1, 1.2 | push to www/lab source → GHCR digest bumped → ArgoCD reconciles dev www/lab |
| 2.2 | Wire **verdify-vault → lab** rebuild trigger (vault has zero CI today) | Iris + James | dep 2.1 | vault content change rebuilds lab image (no manual step) |
| 2.3 | Decommission Cloud Run www path; www served from k3s only | James + Iris | dep 2.1, M5 (apex DNS) | www.verdify.ai served from k3s pod, Cloud Run service deleted |

### Milestone M3 — Prod DB substrate + historical-data dry-run (goal iii prep)
| # | Item | Owner | Gate/dep | Acceptance |
|---|---|---|---|---|
| 3.1 | **Fix G1**: reconcile `restore-job.yaml` client image `2.17.2-pg16`→`2.25.2-pg16` (lines 72/115/151) | Iris | none (PR) | restore-job image == StatefulSet 2.25.2-pg16; promote-diff-guard green |
| 3.2 | Land G2/G3 topology-fidelity migrations (15 plain→hypertables; recreate compression/retention jobs) as serialized coordinator migration PRs | Iris (author) + Jason (coordinator approve) | dep 3.1; one migration at a time | migrate Job produces 19 hypertables + policy jobs; ledger backfill applies |
| 3.3 | Provision prod substrate: `synology-iscsi-ssd` SC (volume1, Retain), `POSTGRES_PASSWORD` SOPS, read-only NFS PVC `verdify-db-dumps`, and a **proven nightly backup CronJob first** | Root | dep 0.6 | `kubectl get sc,pvc` healthy; fresh `/mnt/iris/backups/*.dump` <26h (db-parity dim 9) |
| 3.4 | Stand up a **populated staging DB** via the gated restore (apply restore-job by hand, NOT ArgoCD): pre_restore→pg_restore --data-only→post_restore→ANALYZE→REFRESH matviews→re-add policies | Root (run) + Iris (runbook) | dep 3.1–3.3 | staging DB row counts within RPO of VM baseline |
| 3.5 | First **real iris-vs-k8s parity run**: `db-parity.sh --iris verdify-timescaledb --target <staging-restored>` | Iris (author) + Root (run) | dep 3.4 | **exit 0 — all 9 dimensions match** on a frozen target |

**Gate:** no data work touches the VM DB with `--move`/`--clean`; VM stays SoT through M5.

### Milestone M4 — Prod k8s stack stood up, device-DARK proof (the proof before cutover)
| # | Item | Owner | Gate/dep | Acceptance |
|---|---|---|---|---|
| 4.1 | Create `verdify-prod` namespace + AppProject destination; apply `verdify-prod` ArgoCD App as **manual-sync, ingestor still device-dark** (`WRITE_ENABLED=1` config present but egress NOT yet routable) | Root | dep M1, M3; Jason gate to create ns | prod App Synced; all pods Running except ingestor held; **no :6053 traffic** |
| 4.2 | Populate prod DB via the same gated restore + parity (3.4/3.5 against prod target) | Root + Iris | dep 4.1, 3.5 | prod `db-parity.sh` exit 0 |
| 4.3 | Device-VLAN reachability **spike** (does a pod on the cluster have a route to 192.168.10.111:6053?) — currently flannel has no device-VLAN leg (Errno 111). Decide route mechanism (VLAN leg / dedicated node / proxy) | Root + Nexus | Jason sign-off on spike | Documented route exists or explicit "not yet"; **still no live write** |
| 4.4 | **M4 proof**: prod stack runs ≥48h reading parity-restored data, planner/mcp/api healthy, ingestor in shadow/subscribe, with VM still the sole writer | Root (observe) + Iris (criteria) | dep 4.1–4.3 | 48h clean; alert sweep no critical; single-writer still VM |

### Milestone M5 — **Jason-gated device-writer cutover** (goals i, ii, vii) — THE single-writer flip
| # | Item | Owner | Gate/dep | Acceptance |
|---|---|---|---|---|
| 5.1 | **Atomic single-writer cutover choreography**: in one window — stop VM `verdify-setpoint-server` + `verdify-ingestor`, open prod `allow-ingestor-device-egress`, bring prod ingestor to replicas:1, repoint DATABASE_URL. **Never two writers.** | Root (execute) + **Jason (explicit go)** | dep M4 proof; **honors single-writer invariant** | Exactly one process holds :6053 (the prod pod); VM writers stopped; setpoints flowing; telemetry landing in prod DB |
| 5.2 | Edge/DNS cutover: flip the 9 verdify CNAMEs to proxied `→<UUID>.cfargotunnel.com`; cloudflared tunnel live; api.verdify.ai → k3s ingress | Nexus | dep M1 www/lab, M5.1; cert-issuer fix (L12) | api/www/lab.verdify.ai 200 from k3s via tunnel AND LAN; WAF on |
| 5.3 | **Location-independence proof (vii)**: same endpoints/experience whether pod runs in Google or local | Iris (test) + Root | dep 5.2 | Identical responses local-Traefik vs WAN-tunnel; documented |
| 5.4 | Final iris-vs-prod parity at cutover instant (frozen, RPO-signed) | Iris + Root | dep 5.1 | exit 0 at the watermark; Jason signs the cutover record |

### Milestone M6 — VM decommission (goal ii)
| # | Item | Owner | Gate/dep | Acceptance |
|---|---|---|---|---|
| 6.1 | Capture preserve-before-wipe list: tsdb_data dumps, `/srv/verdify/*.env` + `esphome/secrets.yaml` (ESP32_API_KEY) + `/etc/verdify/*`, `/srv/verdify/state/` (firmware pins, dispatch), firmware artifacts, vault, umami_db_data, grafana_data, mqtt_data, hermes data | Jason + Root | dep M5 stable | Off-box backup of every item verified; analytics/dashboard volumes have a dump path |
| 6.2 | Migrate residual VM-only services (grafana, umami, goaccess, hermes-iris) into k3s or accept retirement | Iris (author) + Root (apply) | dep 6.1 | graphs/analytics/logs served from k3s or formally retired |
| 6.3 | Soak window with VM writers stopped but VM powered (rollback floor); then PBS snapshot + power off VMID 306 | Root + Jason | dep 6.1, 6.2 | ≥1 week clean on k3s; PBS recovery snapshot taken; .150 powered down |

### Milestone M7 — ESP32 → api.verdify.ai re-architecture (goal vi) — its OWN track, sequenced safely AFTER M5
**This is not a re-point; it is a protocol inversion (DEVICE→backend HTTP).** Today the device has zero HTTP client, sits isolated on 192.168.10.0/24, and the only API surface is **read-only GET /setpoints** with no device auth. Do NOT hand-wave it.

| # | Item | Owner | Gate/dep | Acceptance |
|---|---|---|---|---|
| 7.1 | Decide protocol direction + heap reality: ESPHome `http_request:` to api.verdify.ai costs heap (board already disabled Lutron TLS for heap exhaustion). Decide plaintext-to-local-proxy vs HTTPS vs hardware bump | Firmware + Iris | dep M5; **firmware-freeze rules** | ADR with heap budget; replay-diff plan |
| 7.2 | Build the **device-facing write/ingest API + auth model** on api.verdify.ai (today none exists) with single-writer re-established at the API tier (idempotency, conflict-resolution, env gate equivalent to `WRITE_ENABLED`) | Iris (api) | dep 7.1; schema PR first | POST ingest + GET setpoints with device auth; only-one-authority enforced |
| 7.3 | Networking for LOCAL route + WAN fallback: give device VLAN a route to the apps VIP (`.7.10`); split-horizon DNS the device can use; firmware fallback local-IP→public-host | Nexus (route/DNS) + Firmware (fallback) | dep 7.2; Jason gate on cross-VLAN allow | Device resolves+reaches api locally first, WAN on failover |
| 7.4 | Single-writer retirement of the native :6053 writer **or** explicit dual-path guard so the new HTTP path doesn't create a second writer | Firmware + Iris | **dep 7.1–7.3; Jason go; full firmware PR artifacts (replay-diff, invariants, 48h bake)** | Exactly one authoritative setpoint source post-change; invariant suite green |

**Iris-doable here:** 7.2 api surface + auth design, ADR authoring. **Firmware/Nexus/Jason-gated:** anything touching the device, the VLAN, or the :6053 writer (all under firmware-freeze + single-writer rules).

---

## Iris-doable vs gated (summary)
- **Iris alone (repo/CI/IaC/docs):** G1 fix (3.1), G2/G3 migration authoring (3.2), publish planner/setpoint digests (1.3), www/lab CI wiring (1.1/1.2 CI half, 2.1/2.2), parity script + runbooks (3.5/4.4 criteria), api device-write surface design (7.2), all ADRs/state-truth docs.
- **Root-gated (cluster/storage/network/SOPS):** all live reads (M0), prod substrate (3.3/3.4), ns+App apply (4.1), restore execution (3.4/4.2), cutover execution (5.1), decommission (6.x), device-VLAN spike (4.3/7.3).
- **Nexus-gated (edge/DNS/CF/VLAN):** DNS/tunnel cutover (5.2), MetalLB/VIP resolution, device-VLAN routing (7.3).
- **Jason-gated (the hard go/no-go):** create prod ns (4.1), **M5 device-writer flip (5.1)**, decommission power-off (6.3), every device-touching step in M7. Plus secrets/GCP ownership (1.1, 3.3).
- **James-gated:** verdify-www build (1.1), lab repo/Dockerfile.k3s (1.2), Cloud Run retirement (2.3).

## Critical-path ordering (single line)
**M0 (live truth) → 3.1 G1 fix + 1.1/1.2/1.3 images → M2 site CI → 3.3 prod substrate + 3.4/3.5 parity-proven staging restore → 4.1 prod stack device-DARK + 4.2 prod parity + 4.3 VLAN spike → 4.4 48h proof → [JASON GATE] M5.1 atomic single-writer cutover → 5.2 DNS/tunnel cutover + 5.3 location-independence → 6.x VM decommission → M7 (separate track) ESP32→api.verdify.ai re-architecture.**

The two device-touching flips — **M5.1** (prod becomes the sole writer) and **M7.4** (retire the native :6053 writer) — are the only steps that can break the greenhouse; both are Jason-gated, both honor the single-writer invariant, and neither runs until its proof (M4 / firmware bake) is green.
