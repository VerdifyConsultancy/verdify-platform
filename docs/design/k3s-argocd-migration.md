# Verdify k3s + ArgoCD Migration & CI/CD Plan

**Status:** DESIGN ONLY — no cluster created, no images pushed, no production changed.
**Author:** firmware agent (planning). **Executors:** root (cluster/VLAN/storage/secrets infra) + coordinator (`/mnt/iris/verdify`, migrations, `.github/workflows/**`, `verdify_schemas/**`).
**Date:** 2026-05-30. **Repo:** `/mnt/iris/verdify-worktrees/firmware`.

> This document was synthesized from five design tracks and their independent technical critiques. Where a track's premise was factually wrong against the repo, it has been **corrected and the correction grounded** (see §0.1). Every claim cites a real file/line. Each recommendation is tagged **[buildable-now]** or **[aspirational]** so executors know what is safe to act on versus what needs a confirmation gate first.

---

## 1. Goal + why

### 1.1 The deploy gap this kills

Today there is **no CD for the Python layer.** The systemd units run code straight from a symlink:

- `systemd/verdify-ingestor.service`: `WorkingDirectory=/srv/verdify/ingestor`, `ExecStart=/srv/greenhouse/.venv/bin/python ingestor.py`
- `systemd/verdify-mcp.service`: `ExecStart=/srv/greenhouse/.venv/bin/python mcp/server.py`
- `systemd/verdify-setpoint-server.service`: `ExecStart=.../python3 /srv/verdify/scripts/setpoint-server.py`
- `systemd/verdify-api.service`: `EnvironmentFile=/srv/verdify/api/.env`, `ExecStart=.../uvicorn main:app --host 0.0.0.0 --port 8300`

`/srv/verdify` is a symlink to `/mnt/iris/verdify` (coordinator main worktree, branch `live/platform-main`). Code goes live only when the change merges after required checks pass to that branch and then runs `systemctl restart` — **coordinator-only, manual, no audit trail.** `.github/workflows/ci.yml` has **eight gate jobs and zero build/push/deploy jobs** (`lint`, `site-generated-guards`, `schemas`, `firmware`, `firmware-logic`, `firmware-replay-diff`, `no-new-fire-and-forget`, `service-restart-drift-guard`). A merge changes git; nothing reloads.

This gap is exactly why **PR #12 (the Vanda software backlog) cannot go live** without a hand-deploy, and it is the direct cause of the **2026-04-21 MCP-staleness incident** that the `service-restart-drift-guard` job (ci.yml:395-427) was written to prevent ("MCP ran 40+ hours with stale schema because nobody restarted it post-merge").

### 1.2 What GitOps gives us

**Merge → image build → manifest tag bump → ArgoCD reconciles → pod rolls.** No symlink, no manual `systemctl`. A schema change and the code that consumes it ship in one immutable image at one git SHA, structurally fixing the staleness class of bug. Rollback becomes `git revert` of a tag-bump commit. The operator has chosen **ArgoCD** as the CD layer.

### 1.3 The platform already exists — we are a tenant, not a builder

Per `/mnt/agents/root/BACKLOG.md` v6.160 (current-truth banner): the k3s cluster is **LIVE at 5 Ready nodes** (node4/oro, node5/opal added); MetalLB has an **agents-pool (192.168.64.64+)** and an **apps-pool (192.168.64.7+)**; a **Traefik apps-ingress is live**; and the **`gravity` agent was already cut over to a k3s pod** (ns `gravity`, attach LB `192.168.64.12:2222`, worktree PVC) **additively alongside its untouched VM**. Root's current top-3 is literally "(1) batch-migrate the remaining migratable agents into k3s namespaces (recipe proven on gravity) … (3) land … per-agent sealed secrets, PAT rotation, SOPS→reconciler."

**Verdify plugs into this cluster. We do not stand up a new one.** The gravity recipe — namespace + worktree PVC + MetalLB LB attach, additive parallel-run, validate, then flip — is the template.

---

## 0.1 Corrections to track premises (grounded — read before trusting any sketch below)

Two tracks built their networking on a control-flow premise that is **false against the firmware**. Corrected here because it changes the entire LAN design:

| Claimed | Actual (grounded) | Consequence |
|---|---|---|
| "The ESP32 polls the dispatcher `:8200` every 5 s; it needs a stable inbound LB IP." | **False.** `firmware/greenhouse.yaml:190` has an ESPHome native `api:` block and **no `http_request:` client.** The ESP32 is a passive ESPHome device. Setpoints are **pushed to it** by the ingestor over `aioesphomeapi` (`ingestor/esp32_push.py:38 push_to_esp32`, via `entity_map.SETPOINT_MAP`, to `ESP32_HOST:6053`). `scripts/setpoint-server.py:456` `/setpoints` is explicitly "current+next planned setpoints as key=value text for **diagnostics and recovery tooling**" — not the control loop. | **The dispatcher needs NO inbound-from-ESP32 LB.** The greenhouse-critical LAN path is **ingestor egress → `192.168.10.111:6053`**. The dispatcher's real outbound needs are HA (`http://192.168.30.107:8123`, setpoint-server.py:48) for grow-lights and the DB. |
| "MQTT broker `:1883` is on the ESP32 control loop; needs a LAN LB the ESP32 connects to." | **False.** `firmware/greenhouse.yaml` has **no `mqtt:` block.** The ESP32 uses native API only. The ingestor's `MQTT_HOST` defaults to **`192.168.30.107`** (HAOS broker, `ingestor/config.py:42`), not the compose mosquitto. The compose `mqtt` (docker-compose.yml:162) is for "ESP32 + Sentinel occupancy" but the firmware confirms the ESP32 does not speak MQTT. | The compose mosquitto is a normal stateless+small-PVC migration with **no ESP32-LAN constraint.** The ingestor needs egress to `192.168.30.107:1883` (a VLAN-30 service it already reaches). Do not put MQTT on the greenhouse-critical path. |

Other corrected facts:
- **`api/Dockerfile` does not bake `verdify_schemas`** (it is 5 lines, `COPY main.py .` only); compose compensates with the bind-mount `./verdify_schemas:/app/verdify_schemas:ro` (docker-compose.yml:207) and `api/main.py:59` injects `/app`, the worktree root, and `/mnt/iris/verdify` onto `sys.path`. None of those exist in a pod — **a real image must COPY the package in.**
- **`verdify_schemas/` has no standalone `pyproject.toml`.** The repo root `pyproject.toml` declares the package `verdify` with `[project.optional-dependencies]` (`planner`, `api`, …). So a build can `pip install ".[api]"` from repo-root context and `COPY` the source; it cannot `pip install verdify_schemas` as a separate distribution.
- **MCP port ambiguity is live:** `mcp/server.py:9` docstring says 8400; code default is **8000** (`mcp/server.py:249,2437`); the bind drop-in (`verdify-mcp.service.d/bind.conf`) sets `FASTMCP_HOST`/`MCP_HTTP_HOST=0.0.0.0` but **not** the port. Must be confirmed against the live `ss -tlnp` + `/etc/verdify/hermes-iris.env` before manifests land.
- **`docker exec verdify-timescaledb` appears in 12 files / 27 sites** (grep-verified): `scripts/{firmware-deploy-preflight.sh,sensor-health-sweep.sh,wait-for-firmware-version.sh,firmware-audit-traceability-proof.sh,export-replay-overrides.sh,export-replay-data.sh,export-public-sample-dataset.sh,validate-plan-coverage.sh,gather-plan-context.sh,generate-daily-plan.py}`, `api/main.py`, and `Makefile` (lines 212/217/447). Plus the **nightly DB backup cron** (`systemd/jason.crontab:6`). Separately, **~26 Python scripts hardcode `localhost:5432`** including `mcp/server.py` and `scripts/setpoint-server.py:241`.
- **VM naming:** the README and ingestor docs run the stack on `vm-docker-iris` (192.168.30.150 per Track 5). Root's INFRA-39 trim note lists "verdify 309 8→4G". These may be two different VMs or one re-labeled; **executors must confirm which VM/IP hosts the compose+systemd stack before any cutover** (Track 5 critique flagged VM 309 = a separate Verity agent). This plan uses the placeholder **`VM-VERDIFY` @ its confirmed LAN IP**.

---

## 2. Target architecture

### 2.1 Cluster + namespaces

Tenant of the existing 5-node k3s cluster. **[buildable-now]** once root provisions the namespaces/RBAC. Single namespace `verdify` keeps it simple (gravity used one ns); the per-tier table below is the logical grouping within it (split into `verdify` + `verdify-data` only if root prefers NetworkPolicy isolation):

| Tier | Workloads | Sync policy | Notes |
|---|---|---|---|
| **control** | `ingestor`, `setpoint-server` (dispatcher), `mcp`, `hermes-iris` | **manual-sync, no self-heal, no auto-prune** | the greenhouse write path; never reconciled out from under the live loop |
| **data** | `timescaledb` (StatefulSet), `mqtt` (StatefulSet), `umami-db` | **manual-sync, PVCs excluded from prune** | live data; see §3 |
| **web** | `api`, `grafana`+renderer+proxy, `verdify-site`, `umami`, `goaccess`(+site) | **auto-sync + self-heal OK** | not greenhouse-critical |
| **obs** | `promtail` (DaemonSet) | hand to nexus/observability | log topology changes in k8s; §4.4 |
| **jobs** | the CronJobs from systemd timers/crons (§4.3) | auto-sync | non-critical batch |

**The sync-policy asymmetry is the core safety model:** web + jobs auto-sync (developer velocity); the control + data tiers are safety-checked. A 5-second-cadence push loop must never be rolled by a passive git push.

### 2.2 Networking — the ESP32-LAN solution (rebased on §0.1)

| Flow | Direction | k8s networking | Decision |
|---|---|---|---|
| **ingestor → ESP32 `192.168.10.111:6053`** (aioesphomeapi push — THE control path) | pod **egress** to VLAN 10 | plain Deployment, **no inbound service**, `nodeSelector` pinning to a node with a confirmed route to `192.168.10.0/24`; **fallback `hostNetwork: true`** (it exposes no inbound port, so blast radius is contained) | **[aspirational until routing proven]** |
| **ingestor → MQTT `192.168.30.107:1883`** (HAOS broker) | pod egress to VLAN 30 | plain egress; same VLAN as the k3s nodes (oro/opal `.211/.212`) — already reachable | **[buildable-now]** |
| **setpoint-server → HA `192.168.30.107:8123`** (grow-light REST) + DB | pod egress VLAN 30 | plain egress; no inbound LB needed | **[buildable-now]** |
| **api / grafana / site / umami / goaccess** | public HTTP | existing cluster **Traefik apps-ingress** (IngressRoute CRDs), public via the web VM/Cloudflare | **[buildable-now]** |
| **mcp** | in-cluster (hermes) | ClusterIP `Service` on the confirmed port; replaces hermes' `host.docker.internal` hack | **[buildable-now]** |
| **compose `mqtt` :1883** (Sentinel occupancy, NOT the ESP32) | LAN clients (Sentinel/HAOS) | StatefulSet + small PVC; expose via **MetalLB LoadBalancer** with a **pinned IP equal to the broker's current LAN IP** so Sentinel needs no reconfig | **[aspirational]** — confirm which devices hardcode the broker IP before changing it |

**The single hard networking gate (route this to root before any control-tier pod is scheduled):** confirm the k3s pod CIDR (typically `10.42.0.0/16`) or a node's SNAT IP can reach `192.168.10.111:6053` the same way `VM-VERDIFY` reaches it today. The current host reaches the ESP32 via **inter-VLAN routing through the UniFi gateway**, not a local VLAN-10 NIC (Track 5 critique, grounded in `/mnt/agents/root/docs/network-audit-*`). The DOCKER-USER iptables analogy does **not** transfer to UniFi inter-VLAN firewall policy — these are different policy points. **Hard gate:** root runs a probe pod from `verdify` ns opening a TCP connection to `192.168.10.111:6053`. If it fails, `hostNetwork` on the ingestor (node-pinned) is the fallback; if that also can't be routed cleanly, **keep ingestor (and dispatcher) on `VM-VERDIFY` under systemd as a permanent edge** and move everything else (§7 Phase 4 option 3).

**Why MetalLB over hostNetwork/NodePort for the genuinely LAN-inbound services (mosquitto):** a stable L2 IP that floats across nodes is the clean analog of a VM IP for a hardcoded-IP client, plays with the existing MetalLB install, and avoids the node-pinning of hostNetwork. Note the existing pools (`.64`/`.7`) are on the **agents VLAN (192.168.64.0/18)** — whether that segment is reachable from the LAN clients is a root-owned routing question; a **VLAN-30 MetalLB pool may be required** if Sentinel/HAOS must reach the broker by an IP on their own segment.

### 2.3 Storage

| Workload | StorageClass | Rationale |
|---|---|---|
| **TimescaleDB** (~2.3 GB / 7.84M rows, LIVE; `timescale/timescaledb:latest-pg16`, docker-compose.yml:31) | **confirm with root first.** Preference: replicated block (Longhorn) if installed; else **keep the DB external on `VM-VERDIFY` and reach it via a k8s `ExternalName`/`Endpoints` Service** (see §3) | **[aspirational]** — Longhorn is **not confirmed** in the root backlog; `local-path` pins the pod to one node with no replication/snapshot, which would make the live DB a *worse* SPOF than the current VM. **Do not put the live DB on `local-path`.** |
| mqtt_data, grafana_data, promtail positions | local-path | regenerable / low-stakes |
| umami_db | local-path or Longhorn | analytics |
| vault + state (cron jobs) | RWX NFS PVC on the existing Synology (root-owned NAS) | shared by web-owned scripts; coordinate ownership |

PVCs that hold irreplaceable data get `reclaimPolicy: Retain` and are excluded from ArgoCD prune/self-heal (`ignoreDifferences` on `volumeClaimTemplates`).

### 2.4 Secrets

**[aspirational — gate on root]** Adopt the fleet's in-flight stack: **SOPS+age for the Git source-of-truth, sealed-secrets / a SOPS→reconciler in-cluster** (root BACKLOG top-3 item; an `age` key already exists at `/mnt/agents/root/secrets/age`). **Do not invent a second secrets system** — reuse whatever root has deployed for gravity. Until that controller is confirmed live, the **interim is a one-time `kubectl create secret`** per the bootstrap list below (manual, not GitOps), with the sealed/SOPS migration as fast-follow.

Secret inventory (sources: README "Secrets referenced", docker-compose env, systemd EnvironmentFile):

| Secret (k8s) | Keys | Source |
|---|---|---|
| `verdify-db` | `POSTGRES_PASSWORD`, user/db | `/srv/verdify/.env` |
| `verdify-api` | `VERDIFY_WRITE_API_KEY`, SMTP block | `/srv/verdify/api/.env` (audit it — may hold keys beyond `.env.example`) |
| `verdify-ingestor` | `ESP32_API_KEY`, DB creds | `/srv/verdify/ingestor/.env` |
| `verdify-grafana` | `GF_SECURITY_ADMIN_PASSWORD` | `/srv/verdify/.env` |
| `verdify-umami` | `UMAMI_DB_PASSWORD`, `UMAMI_APP_SECRET` | compose env |
| `verdify-mqtt` | `password_file` | `mqtt/password_file` |
| `verdify-hermes` | OpenAI key, slack | `/etc/verdify/hermes-iris.env`, `/etc/verdify/slack` |
| `verdify-ha-token` | HA token | `/mnt/agents/shared/credentials/ha_token.txt` (mount at the **same path** so `setpoint-server.py:141 open(HA_TOKEN_FILE)` and `config.py` `load_token` keep working unchanged) |
| `ghcr-pull` | `.dockerconfigjson` | `/mnt/agents/root/secrets/ghcr_read_token.txt` (read scope is enough for pulls; hermes already pulls ghcr.io) |

The **`ESP32_API_KEY` is hardware-control-sensitive** — rotate it as part of the migration, not after.

---

## 3. Stateful data-layer migration (TimescaleDB)

**Live constraint:** ~2.3 GB / 7.84M rows, `timescale/timescaledb:latest-pg16`, bound `127.0.0.1:5432` (docker-compose.yml:34). Init from `db/init/01-schema.sql` (7 hypertables: `climate`, `equipment_state`, `system_state`, `setpoint_changes`, `diagnostics`, `energy`, `weather_forecast`; plus the continuously-refreshed mat-view `v_relay_stuck`). It is also the **firmware safety oracle** — the preflight queries the alerts table.

### 3.1 Phased decoupling — move compute first, data last

**[buildable-now]** **Phase A — pods point at the still-external DB.** Stand up the Python pods reading/writing the *existing* compose DB on `VM-VERDIFY` via a k8s `Service` of type `ExternalName` (or an `Endpoints` object pointing at `VM-VERDIFY:5432`). This proves the compute layer in-cluster **without touching the DB**, and is the lowest-risk sequencing. **Security note:** the DB is currently `127.0.0.1`-bound; exposing it to the pod CIDR requires either (preferred) a **stunnel/PgBouncer TLS sidecar on the VM** exposing only a TLS port to the k3s node CIDR, or a firewall-scoped bind change permitting only the pod/node CIDR. Do **not** unbind to `0.0.0.0` without that compensating control (this is a validated, coordinator-gated prod change).

**[aspirational]** **Phase B — move the DB into the cluster** only if root confirms replicated block storage. Otherwise the external-DB-on-VM end-state is acceptable and lower-risk.

### 3.2 Migration method (if Phase B proceeds): pg_basebackup physical streaming replica → promote

| Method | Downtime | Loss risk | Hypertable hazard | Verdict |
|---|---|---|---|---|
| **pg_basebackup physical replica → promote** | **~30–90 s** (final flip only) | **zero** (byte-identical: chunks, jobs incl. `v_relay_stuck`, all migrations) | none (physical copy ignores logical schema) | **CHOSEN** |
| pg_dump → pg_restore | 10–40+ min | low if quiesced, but long control gap | real (hypertable DDL/job/mat-view ordering) | **safety-net snapshot only** |
| logical replication | seconds | TimescaleDB warns hypertables/continuous-aggs don't decode cleanly | high | **rejected** |

Source and target are the **same image** → physical replication is valid. **Pin the exact image digest** in both (compose currently uses the mutable `latest-pg16`; `imagePullPolicy: IfNotPresent` + a digest pin prevents a silent minor-version bump on pod reschedule that could require `ALTER EXTENSION timescaledb UPDATE`).

### 3.3 Cutover sequence (the 30–90 s pause)

> **Prerequisite (hard gate, do FIRST):** abstract every `docker exec verdify-timescaledb` call site behind a network-psql wrapper (§5.4 / §6). Until done, the firmware preflight, sensor-health sweep, replay-corpus export, and **the nightly backup cron** silently break when the named container moves — and several of those are the freeze gates Track A depends on.

1. **Pre-window (no downtime):** provision empty StatefulSet on its PVC; take a `pg_dump -Fc` safety dump to NFS + a PBS snapshot; add a replication user + `pg_hba` entry via `SELECT pg_reload_conf()` (avoids a prod restart); seed via `pg_basebackup`; let the standby stream-catch-up for hours; verify per-hypertable row counts and `timescaledb_information.jobs`.
2. **Window — disable timers/path first:** `systemctl stop verdify-*.timer verdify-plan-publish.path`, blank the crontab, schedule at a known-quiet window (e.g. 02:00–04:00 MDT, away from any heat-stress window and any firmware OTA). Then quiesce writers in order: ingestor → setpoint-server → api/mcp.
3. **Zero-loss checkpoint:** confirm `pg_stat_activity` has no app sessions and source `pg_current_wal_lsn()` == standby `pg_last_wal_replay_lsn()`.
4. **Promote** (`SELECT pg_promote()`), re-point writers' `DB_HOST` to the in-cluster service, restart. Grafana's datasource is provisioned as `url: timescaledb:5432` — **name the in-cluster Service `timescaledb`** so it resolves with zero edit.
5. **Smoke:** `max(ts) FROM climate` advancing; dispatcher `/setpoints` serves; `make sensor-health SINCE='15 minutes'` → `FAIL: 0`.

### 3.4 Rollback / backup

Three independent pre-cutover layers: (1) `pg_dump -Fc` to NFS, (2) Longhorn snapshot (if used), (3) PBS snapshot of `VM-VERDIFY`. **Rollback < 5 min:** re-point writers' `DB_HOST` back to the old compose DB (kept running, writer-disconnected, never pruned). Because every writer has exactly one DSN, there is **never split-brain** — writers are either all-old or all-new, stopped in between. Destroy the old `tsdb_data` only after ≥1 week clean + a verified in-cluster backup. The **nightly backup cron becomes a CronJob** using a `postgres:16-alpine` `pg_dump` over the network to the NFS PVC `/mnt/iris/backups` — and must be proven **before** Phase B.

---

## 4. Service containerization

### 4.1 Image strategy — one multi-stage `verdify-py` image, per-workload entrypoint

The systemd units already share one interpreter (`/srv/greenhouse/.venv/bin/python`) and one dependency closure (root `pyproject.toml`). Bake the repo + `verdify_schemas` in once; this co-versions schema and code (kills the staleness class). Build context = **repo root** (so `COPY verdify_schemas` works — fixing §0.1's Dockerfile gap).

```dockerfile
# build/Dockerfile  (SKETCH — context = repo root)
FROM python:3.13-slim AS base
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app
COPY pyproject.toml ./
RUN pip install --no-cache-dir ".[api]"          # core + fastapi/uvicorn

FROM base AS full                                  # ingestor/mcp/dispatcher/crons
RUN pip install --no-cache-dir ".[planner]"        # anthropic/openai/google-genai

FROM full AS app
COPY ingestor/ ingestor/  mcp/ mcp/  api/ api/  scripts/ scripts/ \
     verdify_schemas/ verdify_schemas/  templates/ templates/  config/ config/  Makefile pyproject.toml ./
RUN useradd -u 10001 -m verdify && chown -R verdify /app
USER 10001
ENTRYPOINT ["python"]                              # each Deployment overrides command/args

FROM base AS api                                   # slim api (no planner libs)
COPY api/ api/  verdify_schemas/ verdify_schemas/  pyproject.toml ./
RUN useradd -u 10001 -m verdify && chown -R verdify /app
USER 10001
WORKDIR /app/api
CMD ["uvicorn","main:app","--host","0.0.0.0","--port","8300"]
```

**CI must verify `python -c "from verdify_schemas import alerts"` succeeds inside the built image before push.** Tags are immutable git-SHA only (`ghcr.io/jvallery/verdify-py:full-<sha>`, `:api-<sha>`); never `:latest`. **Conscious tradeoff:** any `verdify_schemas/` change now requires an image rebuild (see §5.5 for the CI rule that enforces it).

### 4.2 Long-running services → Deployments

| Workload | Image/entrypoint | Port | LAN | Probe | Strategy |
|---|---|---|---|---|---|
| **ingestor** | `full` → `ingestor.py` | none inbound | **egress → ESP32:6053** (the control path) + MQTT/DB | **exec** `scripts/ingestor-healthz.py` (new, queries `max(ts) FROM climate`, reuses `api/main.py` freshness logic) with **`initialDelaySeconds:60, failureThreshold:5`** so transient DB latency doesn't restart it; or add an in-process `/healthz` flag (preferred — avoids a 2nd DB conn per probe) | **`Recreate`** (single ESP32 client; two ingestors would double-push) |
| **setpoint-server** (dispatcher) | `full` → `scripts/setpoint-server.py` | 8200 ClusterIP (diagnostics only — NOT ESP32-facing per §0.1) | egress HA:8123 + DB | `GET /health` (setpoint-server.py:453) | `Recreate` |
| **mcp** | `full` → `mcp/server.py` | **confirmed port** (8000/8400) ClusterIP | in-cluster | transport GET on MCP root | `RollingUpdate` |
| **api** | `api` slim → uvicorn :8300 | 8300 + IngressRoute `api.verdify.ai` | public | `GET /health` (api/main.py rich freshness check) | `RollingUpdate` |
| **hermes-iris** | `nousresearch/hermes-agent@sha256:a7111…` (digest pin **preserved**, no transformer) | 8642 ClusterIP | reaches mcp in-cluster (replaces `host.docker.internal`) | TCP 8642; `limits 4G/2cpu` (from compose `deploy.resources`) | normal |

**hermes coupling (grounded):** today hermes reaches MCP via `extra_hosts: host.docker.internal:host-gateway` (docker-compose.yml:381) and the ingestor defaults `HERMES_URL=http://127.0.0.1:8642` (config.py:58). In-cluster, both become ClusterIP DNS (`mcp.verdify.svc`, `hermes-iris.verdify.svc`) and `HERMES_URL` must be updated. hermes also mounts host paths `/var/lib/verdify/hermes/iris`, `/etc/verdify/{slack,hermes-iris.env}` → become PVC + Secrets/ConfigMap. **The MCP URL inside hermes lives in `/etc/verdify/hermes-iris.env` (not in the repo)** — read it on the VM and confirm it is reconfigurable before stopping the systemd MCP, or hermes silently fails at tool-call time (not at startup).

**[required pre-condition] The ingestor has no DRY_RUN/shadow mode today** (verified — `ingestor.py`/`config.py` have no such flag). Running a second ingestor against the live DB/ESP32 double-writes telemetry (corrupting the preflight `max(ts)` freshness checks) and risks double-actuation. **Before any parallel-run, add an explicit `SHADOW_MODE` flag** that suppresses all DB writes and all `aioesphomeapi`/MQTT state changes. This is a code deliverable, not a sketch.

### 4.3 systemd timers/crons → CronJobs (TZ `America/Denver`, `concurrencyPolicy: Forbid`)

| Source | Schedule | Entrypoint |
|---|---|---|
| `verdify-forecast-page.timer` | `*/30 * * * *` | `publish-site-content.sh --reason forecast` |
| DB backup (`jason.crontab:6`) | `0 1 * * *` | network `pg_dump` (NOT `docker exec`) → NFS |
| daily-summary, vault-daily/crop, hydro-map | `0 0 * * *` | respective scripts |
| frigate-snapshot | `0 12,16,20,0` | `frigate-snapshot.py` |
| checklist-to-slack | `0 13 * * *` | `checklist-to-slack.sh` |
| slack-channel-archive | `0 */6 * * *` | `slack-channel-archive.py` |
| publish-daily-plan ×3 | `0 7`,`15 20`,`30 0` | `publish-daily-plan.sh` |
| `replay-corpus-refresh` (Makefile:130) | weekly | normal DB-read CronJob; writes refreshed `.csv.gz` back as a PR artifact (**not** with the OTA job) |

**Retired, not ported** (issue #60): `verdify-grafana-render-cache-warm.timer`/`.service` + `scripts/warm-grafana-render-cache.py`. The timer had been dead since 2026-05-25 emitting HTTP 500s from the headless-Chromium `/render/d-solo/...` path; it was a pure cache-priming optimization (no dashboard depends on a warm cache — PNG/iframe embeds still render on first request) and the web tier is moving here with observability handed to nexus, so a VM-only warm loop is throwaway.

**Not CronJobs** (sub-minute / event):
- `verdify-site-poll.timer` (**every 10 s**) + `verdify-site-build.service` + `verdify-plan-publish.path` (inotify on `/var/local/verdify/state/plan-publish-trigger`). The README documents the 10 s poll exists *because inotify on NFS is unreliable*. In-cluster these become a small **`site-watcher` Deployment** loop, and the plan-publish trigger should become a **DB NOTIFY** the watcher LISTENs on (avoids cross-node PVC inotify). Also: `rebuild-site.sh` calls `docker restart verdify-site` — that fails in a pod; replace with a ConfigMap-hash annotation / `kubectl rollout restart`. **This is web-agent territory** — file a `requested-by: firmware` PR; keep the systemd path on the VM until the redesign lands.
- `verdify-metrics.py` (**every 1 min**) → a tiny long-running Deployment exposing `/metrics` (a per-minute CronJob would spawn 1440 pods/day). nexus owns Prometheus scrape.

### 4.4 Infra compose services

| Service | k8s object | Note |
|---|---|---|
| timescaledb | StatefulSet+PVC (or external, §3) | live data |
| mqtt (Sentinel occupancy) | StatefulSet + small PVC + MetalLB LB (pinned IP) | **not** the ESP32 loop (§0.1) |
| grafana(+renderer+proxy) | Deployments + grafana_data PVC; provisioning via ConfigMaps | datasource `timescaledb:5432` resolves zero-edit if Service named `timescaledb` |
| verdify-site | nginx Deployment + IngressRoute `lab.verdify.ai`; site content from a shared PVC written by the `site-watcher` | |
| umami + umami-db | Deployment + StatefulSet | lift-and-shift |
| goaccess(+site) | **defer / keep in compose** | reads `./traefik/logs` bind-mount; cluster Traefik logs go elsewhere (PVC/stdout) — low-stakes, migrate after nexus confirms log export |
| promtail | DaemonSet (hostPath `/var/log/pods`, `/var/log/containers`) — **nexus-owned** | log paths change from Docker (`/var/lib/docker/containers`) to CRI; **run the old compose promtail in parallel during transition** so logs aren't lost mid-migration |
| traefik | **retire** | k3s/cluster Traefik apps-ingress already exists; convert compose router labels → IngressRoute + Middleware CRDs (web/coordinator territory) |
| hermes-iris | Deployment (§4.2) | digest pin preserved |

### 4.5 The firmware-OTA special case — NOT a container, NOT in ArgoCD

`make firmware-deploy` (Makefile:346) runs `firmware-deploy-preflight.sh`, compiles ESPHome, `upload --device $(ESP32_DEVICE=192.168.10.111)`, sleeps 60 s, runs `sensor-health-sweep.sh`, and auto-rolls-back to `last-good.ota.bin` on failure (Makefile:372-385). It flashes physical hardware over the LAN. **It must never be a reconciled Deployment** (a Deployment would re-flash on every drift — the catastrophe the freeze rules prevent).

**Primary mechanism: keep OTA as an operator-scoped GitHub Actions
`workflow_dispatch` job on a self-hosted runner with LAN access to
`192.168.10.111`** (`runs-on: [self-hosted, verdify-lan]`), with a mandatory
`reason`, replay/invariant/compile checks, a last-good rollback image, and
post-flash health verification. A privileged hostNetwork Job that can flash
production remains a break-glass fallback: manually instantiated,
`syncPolicy: manual`, node-pinned, and `backoffLimit: 0`.

**The one code change OTA needs:** `firmware-deploy-preflight.sh:10` does `DB=(docker exec … verdify-timescaledb psql …)`. On a runner with no Docker socket / after the DB moves, this fails. Re-point it (and the other 26 call sites) to a network-psql wrapper (§5.4). **This is a Phase-0 / pre-condition deliverable** — without it the freeze gates do not exist on the runner.

---

## 5. CI/CD pipeline

### 5.1 Where manifests live: in-repo `deploy/`, Kustomize base + overlays

Keeps single-PR atomicity (code + manifest + gate results in one PR, which the freeze rules and `firmware-replay-diff` reason about). Image tags live only in `overlays/<env>/kustomization.yaml` so envs advance independently and CI write-back touches one file.

```
deploy/
  base/{ingestor,setpoint-server,mcp,api,mqtt,grafana,...,cronjobs}/
  overlays/{dev,stage,prod}/kustomization.yaml   # images: pinned here
  dockerfiles/                                    # build/Dockerfile + verdify-firmware-ota
  argocd/{project.yaml, app-of-apps.yaml, apps/{dev,stage,prod}-platform.yaml}
```

### 5.2 CI: keep all 8 gates as PR gates, ADD build/push/tag-write on merge

`ci.yml` is unchanged (the PR-only guards on `firmware-replay-diff`/`no-new-fire-and-forget`/`service-restart-drift-guard` are correct — they diff merge-base). A **new `.github/workflows/release.yml`** (separate file; `.github/workflows/**` is shared-territory → coordinator PR labeled `requested-by: firmware`):

- `gate-check`: require the CI workflow for this commit to be `success`. **Use the Checks API for the merge SHA** (`checks.listForRef`) or a `workflow_run` trigger — not a `head_sha` lookup, which misses squash-merge SHAs.
- `build-push` (matrix: build `verdify-py` `full`+`api` targets; third-party images stay upstream pins): push immutable `:<sha>` tags to GHCR with `packages: write`. **Confirm `github_pat.txt` has `write:packages`** (the `ghcr_read_token.txt` is read-only by name; push needs a write-scoped token as a GH Actions secret).
- `bump-stage-tag`: `kustomize edit set image` into `overlays/stage/` only, commit with **`paths-ignore: deploy/overlays/**` on the CI trigger** (surgical loop-break, better than `[skip ci]` which suppresses *all* future push workflows).

**CI bumps only stage, never prod.** Auto-promotion stops at stage — the greenhouse is never auto-rolled by a merge.

### 5.3 ArgoCD app-of-apps + image-tag flow + sync/rollback

```yaml
# deploy/argocd/apps/prod-platform.yaml (SKETCH)
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata: { name: verdify-prod-control, namespace: argocd }
spec:
  project: verdify
  source: { repoURL: https://github.com/jvallery/verdify.git, targetRevision: main, path: deploy/overlays/prod }
  destination: { namespace: verdify, server: https://kubernetes.default.svc }
  syncPolicy:                       # NO automated block → MANUAL sync (greenhouse safety)
    syncOptions: [ServerSideApply=true]
  ignoreDifferences:
    - { group: apps, kind: StatefulSet, jsonPointers: [/spec/volumeClaimTemplates] }
```

dev/stage are identical but `automated: {prune: true, selfHeal: true}`. **Split prod into `verdify-prod-control` (manual-sync) and `verdify-prod-web` (auto-sync OK).**

**Image-tag flow (chosen: CI write-back, NOT ArgoCD Image Updater).** Image Updater auto-advances tags from the registry = auto-deploy-to-greenhouse and divergence of git-from-live — both break the freeze posture (CLAUDE.md rule 4: every prod change PR-scoped) and the "git == live, revert == rollback" property. Flow: merge → gates green → build+push → stage auto-syncs (**deploy gap closed for non-prod**). **Prod promotion is a deliberate `bump-prod` PR** (via `workflow_dispatch` / `make promote-prod SHA=<sha>` that runs the §4.5 preflight first) copying stage tags into `overlays/prod/`; on merge ArgoCD shows OutOfSync; coordinator clicks **Sync**. **Rollback = `git revert` the bump commit → OutOfSync → Sync** (exact, immutable-SHA, auditable; strictly better than "edit symlink, hope, restart").

### 5.4 The `docker exec` abstraction (hard prerequisite for §3 and §4.5)

Introduce `scripts/lib/psql-verdify.sh` exposing `verdify_psql()` switched on `VERDIFY_DB_BACKEND=docker|dsn`: `docker` = today's `docker exec verdify-timescaledb` (unchanged behavior, CI/compose stay green); `dsn` = network `psql` via `VERDIFY_DB_DSN`/`PGHOST`. Replace **all 27 `docker exec verdify-timescaledb` sites** (the 12 files in §0.1) **and** fix the **~26 hardcoded `localhost:5432`** Python sites (`mcp/server.py:249`-style, `setpoint-server.py:241`) to env-first DSN. **Sub-phase it:** **A1** = freeze-rule-critical only (`firmware-deploy-preflight.sh`, `sensor-health-sweep.sh`, `wait-for-firmware-version.sh`, `export-replay-overrides.sh`, `setpoint-server.py`, `mcp/server.py`, **the backup cron**) — unblocks DB move; **A2** = the rest (trails by weeks). A1 must land + a CI test must run the preflight in a Docker-socket-less env before any DB move. (This touches `mcp/server.py` → the PR body must document the `verdify-mcp` restart per the drift-guard; it touches no firmware-logic files so `firmware-replay-diff` stays green at THRESHOLD_PCT=0.)

### 5.5 Firmware freeze in GitOps + the schema-bake rule + dev/stage/prod

- **All 8 gates survive verbatim** as PR gates; `build-push` runs only after they pass. The freeze rules (no-OTA-with-open-critical/legacy-high-alert, ≤1 OTA/week, 48 h bake on `last-good.ota.bin` mtime, replay-diff THRESHOLD_PCT=0, 16 invariants, 85F stress *warning*) live in `firmware-deploy-preflight.sh` + CI and gate the §4.5 runner job — **never delegated to ArgoCD auto-sync.**
- **`service-restart-drift-guard` is updated for GitOps, not removed** (institutional memory of 2026-04-21). In GitOps mode it must verify the schema-touching PR **also bumps the consuming images** (else a schema-only PR runs old-schema images until the next code change) and that rollout strategy is `RollingUpdate`-compatible. Accept `ArgoCD`/`reconcile`/`image tag bump` as restart-documentation signals; exclude the machine-generated bump PRs via label/branch.
- **dev/stage/prod:** prod = real greenhouse (manual-sync control tier). stage = auto-synced on merge, the live GitOps validation surface. dev = throwaway DB, branch deploys. **[aspirational] The "firmware digital-twin pod"** (an ESPHome-native-API simulator the stage dispatcher/ingestor exercise against the replay corpus) is a **multi-sprint firmware-agent project, not a CD deliverable** — the existing `firmware-logic`/`firmware-replay-diff` CI jobs already cover replay and **stay as CI jobs**; do not replace them with a pod.

---

## 6. The next-main deploy / Python-layer fix

### 6.1 Recommendation: ship PR #12 the **existing way NOW**, in parallel with building k3s

Do **not** make PR #12 the first GitOps deploy. PR #12 carries heat-critical work; the merge→symlink→`systemctl restart` path is the *current production deploy* and has shipped every prior change with zero new failure modes. Coupling a safety-relevant change to an unbuilt multi-week CD path is exactly the schedule risk the freeze rules exist to prevent. This **decouples** the safety deadline from the migration; k3s then proceeds as shadow/parallel-run with systemd as the proven fallback (the gravity pattern).

### 6.2 Bridge runbook (coordinator-run on `VM-VERDIFY`)

1. Confirm all 8 CI jobs green on PR #12.
2. Confirm PR #12 touches **no** firmware-logic files: `git diff --name-only origin/main...pr-12 -- firmware/lib/greenhouse_logic.h firmware/lib/greenhouse_types.h firmware/greenhouse/controls.yaml` → empty (else it becomes a firmware deploy under the freeze rules).
3. Confirm the PR body documents post-merge restarts (drift-guard enforces). Derive the set from changed paths: `ingestor/**`→`verdify-ingestor`; `mcp/server.py`→`verdify-mcp`; `api/**`→`verdify-api`; dispatcher→`verdify-setpoint-server`.
4. Extra safety dump: `docker exec verdify-timescaledb pg_dump -U verdify -Fc verdify > /mnt/iris/backups/verdify-precut-PR12-$(date +%Y%m%d-%H%M).dump`.
5. `git -C /mnt/iris/verdify fetch && merge --ff-only origin/live/platform-main`.
6. Apply any PR #12 migration first (serialized, coordinator-validated), validate against `verdify_schemas/tests/test_drift_guards.py`.
7. Restart **only** the documented services in dependency order: `sudo systemctl restart verdify-ingestor verdify-mcp verdify-api` (+ `verdify-setpoint-server` if dispatcher code changed) — explicit restart closes the 2026-04-21 class.
8. Verify: `make lint && make test` (tolerate the 1 flaky `test_dew_point_risk_computes` timeout); `make sensor-health SINCE='15 minutes'` → `FAIL: 0`; confirm fresh `climate`/`climate_action_log`; functional check the PR #12 behavior (pre-cool trigger emitted; any dual-write lands in both sinks); watch one diurnal peak.
**Rollback:** `git checkout <prev-sha>` + restart the same services (same mechanism as the forward deploy — why this path is low-risk under deadline).

### 6.3 How it becomes a GitOps deploy

Once §7 reaches Phase 2/3, the *same* PR-#12-class change ships as: merge → image build → stage auto-sync (validated) → `bump-prod` PR → coordinator Sync. The bridge mechanism (symlink+systemctl) is retired only at §7 Phase 5, after the in-cluster path has baked.

---

## 7. Phased cutover roadmap

Global **DRIVE-TO-GREEN** (per phase, against the new runtime): `make lint && make test` (1 flaky tolerated); `make sensor-health SINCE='15 minutes'` → `FAIL: 0`; probes green ≥ the bake window; for firmware-logic-touching phases `make firmware-invariants` + replay-diff THRESHOLD_PCT=0; shadow phases additionally require a **defined, achievable parity criterion** (same mode decision / within one setpoint band — **not** exact zero-divergence, which is unachievable for an async live system per the Track 5 critique).

| Phase | Entry | Work | Exit (green) | Rollback |
|---|---|---|---|---|
| **0 — Foundation** | k3s Ready (true); root creates `verdify` ns/RBAC/ArgoCD project | Write `deploy/`, `build/Dockerfile` (+verify schema import in image), `release.yml`; **Phase A1 docker-exec abstraction**; **add ingestor `SHADOW_MODE`**; bootstrap secrets; `ghcr-pull`; confirm StorageClass, MCP port, VM/IP, write-scoped GHCR PAT | `kustomize build` renders; a canary pod pulls a GHCR image; SealedSecrets/secrets resolve; nothing serves prod | delete ns |
| **1 — Web/observability** | P0 green | Migrate site/grafana/umami/goaccess + api shadow, pointing at the **external** DB via ExternalName; run **old promtail in parallel**; defer goaccess if needed | each route 200 via apps-ingress; grafana renders; DNS flipped, old compose stopped (not removed) 1 wk | flip DNS back; scale new to 0 |
| **2 — Python shadow** | P1 green; backup CronJob proven | ingestor/mcp/api/crons as pods in **`SHADOW_MODE`** alongside systemd (gravity additive); parity harness | parity criterion met ≥48 h (mirrors 48 h bake); api/mcp ingress flippable | scale shadow to 0; systemd never stopped |
| **3 — DB** | P2 green; replicated storage confirmed (else stay external); **all 27 docker-exec sites re-pointed** | §3 basebackup→promote in a quiet window; re-point firmware preflight + verify | row-count/drift-guard parity; first in-cluster backup; preflight dry-run green | re-point `DB_HOST` to old DB (< 5 min); old DB untouched 1 wk |
| **4 — Control tier (HIGHEST RISK)** | P3 green; **root signed off VLAN-10 routing** (or `hostNetwork`/edge fallback chosen); no open critical/legacy-high alert; not in a heat window; **ESP32 failure-mode confirmed** | dispatcher+ingestor pods (egress to ESP32, readiness probe that checks it can reach `192.168.10.111` before Ready); **leave systemd enabled as hot standby**; `tolerationSeconds≤30`, PDB `minAvailable:1` | ingestor pushes to ESP32 with no loop gaps ≥48 h; `sensor-health FAIL:0`; one clean diurnal cycle | restart systemd ingestor/dispatcher on VM (kept one `systemctl start` away) |
| **5 — Decommission** | P4 green ≥1 wk; verified backups; hermes MCP URL repointed | `systemctl disable --now` migrated units; `docker compose down` (keep volumes); update README/compose (coordinator); **retain** OTA path + edge dispatcher if option-3 chosen | old units off, `make check` green, OTA dry-run green from its permanent home | re-enable units / `compose up` (volumes retained) |

**Phase-4 SPOF mitigation (explicit):** k3s reschedules an evicted pod in ~300 s by default (unreachable taint) — 60 missed push cycles. The ESP32 holds its last setpoints (confirm in `greenhouse_logic.h` that the failsafe is "hold last," not "enter default mode"), but keep the systemd ingestor as a permanent hot-standby until the VLAN-10 route is proven across **all** schedulable nodes, set `tolerationSeconds≤30`, and gate pod Ready on actual ESP32 reachability.

---

## 8. Risks + mitigations

| # | Severity | Risk (grounded) | Mitigation |
|---|---|---|---|
| R1 | **critical** | **27 `docker exec verdify-timescaledb` sites** (12 files incl. all freeze-rule scripts + the nightly backup cron) break when the DB/container moves → freeze gates and the only prod backup silently fail | §5.4 wrapper, **Phase A1 before any DB move**, CI test in a Docker-socket-less env; backup cron in A1 |
| R2 | **critical** | VLAN-10 reachability from pods is **unproven** (host reaches ESP32 via UniFi inter-VLAN routing, not a local NIC; DOCKER-USER ≠ UniFi policy) | hard probe-pod gate (§2.2); `hostNetwork` node-pinned fallback; edge-on-VM option-3 |
| R3 | **critical** | Live-DB on `local-path` = worse SPOF; Longhorn **unconfirmed** | confirm StorageClass; default to **external-DB-on-VM via ExternalName**; never `local-path` for the live DB |
| R4 | **critical** | ESP32 control-flow premise was wrong in two tracks (poll vs push; MQTT) | corrected §0.1; dispatcher needs no inbound LB; ingestor egress is the real path; compose mosquitto is off the critical path |
| R5 | **high** | `verdify_schemas` not baked into images → `ModuleNotFoundError` in pods | repo-root build context + `COPY verdify_schemas`; CI import check; §5.5 schema-bake rule |
| R6 | **high** | No ingestor `SHADOW_MODE` → parallel-run double-writes/double-actuates | add the flag as a P0 code deliverable; never parallel-run without it |
| R7 | **high** | hermes `host.docker.internal`→MCP + `HERMES_URL` loopback break in pods; MCP URL hidden in `/etc/verdify/hermes-iris.env` | ClusterIP DNS; read+confirm the env file before stopping systemd MCP; PVC/Secrets for hermes host paths |
| R8 | **high** | Phase-4 single-node SPOF; ~300 s reschedule vs 5 s cadence | systemd hot-standby; `tolerationSeconds≤30`; PDB; ESP32-reachability readiness probe; confirm "hold last setpoints" failsafe |
| R9 | **high** | DB `127.0.0.1`-bound → exposing to pods is a prod security change | stunnel/PgBouncer TLS sidecar or CIDR-scoped firewall; never `0.0.0.0`; coordinator-gated |
| R10 | medium | ArgoCD/Longhorn/sealed-secrets/StorageClass **not confirmed in root backlog** (banner points to laptop-only `v3-as-built-architecture.md`); a Flux SOPS-reconciler may already own namespaces → dual-controller conflict | **Track-0 confirmation gate with root** before P1: ArgoCD present? Flux managing ns? StorageClass name? MetalLB CIDRs? |
| R11 | medium | `latest-pg16` mutable tag → silent minor bump on reschedule | pin digest, `imagePullPolicy: IfNotPresent` |
| R12 | medium | `service-restart-drift-guard` becomes false-confidence in GitOps | §5.5: require image bump on schema PRs; accept GitOps restart signals |
| R13 | medium | `release.yml` `gate-check` misses squash-merge SHA | Checks API / `workflow_run` trigger |
| R14 | medium | OTA runner needs LAN + DB but GH-hosted runners have neither | self-hosted LAN runner (primary) / privileged in-cluster Job (break-glass); preflight network-psql |
| R15 | low | site-build `docker restart`, 10 s poll, plan-publish inotify, promtail Docker paths, goaccess log path — all host-coupled | `site-watcher` Deployment + DB NOTIFY; rollout-restart; parallel promtail; defer goaccess; all web/nexus-owned |
| R16 | low | contested cluster ops during the June 4–9 heat window | negotiate a quiet change window for Phase 4; other agent migrations only during P0–P2 |

---

## 9. Ownership + proposed backlog

**Model:** firmware agent (this doc) **plans**; **root** executes cluster/VLAN/storage/secrets/ArgoCD; **coordinator** executes `/mnt/iris/verdify`, migrations, `.github/workflows/**`, `verdify_schemas/**`. Cross-territory work files `requested-by: firmware` PRs (web: site-watcher/IngressRoutes/vault PVC; genai: MCP probe/port; nexus: promtail/metrics scrape; ingestor: `SHADOW_MODE`+healthz).

| ID | Item | Owner | Priority | Gate |
|---|---|---|---|---|
| K3S-0 | **Track-0 confirmation** (ArgoCD present? Flux? StorageClass? MetalLB CIDRs? VM/IP? GHCR write PAT?) | root | **P0 blocker** | blocks everything past doc |
| K3S-1 | docker-exec→network-psql wrapper, **A1 freeze-critical + backup cron** | firmware→coordinator | **P0** | R1; precond for DB move + OTA-on-runner |
| K3S-2 | ingestor `SHADOW_MODE` + `ingestor-healthz.py` | ingestor (req-by firmware) | **P0** | R6; precond for parallel-run |
| K3S-3 | `build/Dockerfile` (repo-root, COPY schemas, import check) + `verdify-firmware-ota` image | firmware | P0 | R5 |
| K3S-4 | `release.yml` build/push + stage tag write-back (Checks API gate) | coordinator (req-by firmware) | P1 | R13; closes deploy gap (non-prod) |
| K3S-5 | ArgoCD project + app-of-apps + base/overlays | root+firmware | P1 | manual-sync control tier |
| K3S-6 | VLAN-10 probe-pod + routing decision (MetalLB/hostNetwork/edge) | root | **P1** | R2; gates Phase 4 |
| K3S-7 | StorageClass + external-DB ExternalName + TLS sidecar | root+coordinator | P1 | R3,R9 |
| K3S-8 | secrets bootstrap + sealed/SOPS migration + `ghcr-pull` + ESP32 key rotation | root | P1 | R10 |
| K3S-9 | OTA `workflow_dispatch` on self-hosted LAN runner (preflight re-pointed) | firmware→coordinator | P2 | R14; freeze rules preserved |
| K3S-10 | `service-restart-drift-guard` GitOps update (require image bump) | coordinator | P2 | R12 |
| K3S-11 | DB basebackup→promote runbook + drill | coordinator+root | P3 | R1 done first |
| K3S-12 | web migration: IngressRoutes, `site-watcher`+DB-NOTIFY, vault PVC | web (req-by firmware) | P2 | R15 |
| K3S-13 | promtail DaemonSet + verdify-metrics Deployment + scrape | nexus (req-by firmware) | P3 | R15 |
| K3S-14 | A2 docker-exec/localhost:5432 cleanup (remaining ~26 sites) | coordinator | P3 (trailing) | R1 |
| K3S-15 | hermes pod (PVC/Secrets, MCP DNS, env-file confirm) | firmware+genai | P3 | R7 |
| K3S-16 | [aspirational] firmware digital-twin pod | firmware (separate project) | backlog | not a CD deliverable |
| K3S-17 | [aspirational] move DB into cluster (Phase B) | root+coordinator | backlog | gated on replicated storage |

---

### Key file references
- `docker-compose.yml` — 14 services; DB `127.0.0.1:5432` (34); api `verdify_schemas` RO mount (207); mqtt :1883 (162); hermes ghcr digest + `host.docker.internal` (363,381); promtail hostNetwork (337-351).
- `.github/workflows/ci.yml` — 8 gates, no build/push; `firmware-replay-diff` THRESHOLD_PCT=0 (322-362); `service-restart-drift-guard` (395-427).
- `systemd/` — the deploy gap (`/srv/verdify` symlink, manual restart); `verdify-mcp.service.d/bind.conf` (host, not port); `jason.crontab:6` backup via docker exec; README "Secrets referenced" + "why polling not inotify".
- `firmware/greenhouse.yaml:190` — ESPHome native `api:`, **no** `http_request`/`mqtt` (proves push-not-poll).
- `ingestor/esp32_push.py:38`, `ingestor/config.py:28,42,58` — push path, ESP32:6053, MQTT 192.168.30.107, HERMES loopback.
- `scripts/setpoint-server.py:16,48,141,241,456` — :8200 diagnostics, HA:8123, HA token file, hardcoded localhost DSN, `/setpoints` "diagnostics and recovery."
- `scripts/firmware-deploy-preflight.sh:10` + `Makefile:346-393` — freeze gates, OTA flash + auto-rollback, sensor-health.
- `api/Dockerfile` — 5 lines, no schemas; `api/main.py:59` sys.path injection.
- `pyproject.toml` — package `verdify`, `[project.optional-dependencies]`; `verdify_schemas/` has no standalone pyproject.
- `/mnt/agents/root/BACKLOG.md` v6.160 banner — k3s 5 nodes LIVE, MetalLB pools, gravity recipe, sealed-secrets/SOPS in-flight; `/mnt/agents/root/secrets/{age,ghcr_read_token.txt,github_pat.txt}`.
