# Verdify k3s Cutover Sequence + Definition-of-Done Checklist (P9)

**Status:** RUNBOOK — DESIGN/CHECKLIST ONLY. Nothing in this document has been
executed. No cluster apply, no `kubectl`/ArgoCD sync, no secret seal/read, no device
touch, no setpoint push, no firmware flash, no live-service stop occurred while
authoring it. Every action below is teed up for a human owner at a named gate.
**Authored by:** Verdify `firmware` agent (PREP only).
**Date:** 2026-05-30.
**Authoritative plan:** `/mnt/agents/root/docs/verdify-cicd-refactor-handoff.md` (P7–P9 + §5 Definition of Done + §6 guardrails).
**Program doc (P1 design):** `docs/design/verdify-cicd-program.md`.
**Branch state at authoring:** `firmware/cicd-golden-path` @ `f350bcd` (== PR #55 into `VerdifyConsultancy/verdify-platform`).

> **The single rule above everything:** **Track A (the greenhouse stays alive) > Track B
> (this refactor), always.** Plants are alive; the ESP32 is in a 5–10s control loop. The
> k3s stack runs **alongside** the live VM compose/systemd stack — purely additive — until
> each piece is independently proven. The VM stack is **NOT** stopped until a verified,
> gated, per-service cutover, and the device-touching ingestor is the LAST thing to move.
> No firmware OTA path is EVER part of cutover.

---

## 0. Gate-owner legend

| Owner | Confirms |
|---|---|
| **Jason** | every device-affecting / irreversible step: first live setpoint from a pod, the single-writer cutover, the DB quiescence window, the ESP32 PSK/OTA-password sealing, every source-service stop, any firewall/router/VLAN posture change. |
| **laptop-root** | every cluster-side apply: `kubectl`/ArgoCD reconcile, namespace creation, the `local-k8s-secret-sync` run on the protected self-hosted runner, the apps-VLAN-7 IP reservation. |
| **James (VerdifyConsultancy)** | every merge into the read-only `VerdifyConsultancy` org (PR #55 and any sibling app-repo PR). |

**Hard boundaries this runbook never crosses (handoff §6):** no `firmware/lib/**` /
`greenhouse_logic.h` / `entity_map.py` / `mcp/server.py` tool-semantic edits; CI never
flashes/OTAs (`make firmware-deploy` stays the only human-gated OTA path); no two ESP32
writers ever; no `down -v` / data delete; the VM itself is never stopped (only individual
services, one at a time).

---

## 1. The ordered cutover sequence (lowest device risk FIRST)

Cutover proceeds strictly in ascending device-risk order. **No service advances to the
next stage until its own verify passes AND a soak window clears.** Each stage runs the k3s
side ALONGSIDE the still-running VM service first (additive), proves it, and only then —
at a separately confirmed gate — stops the VM source service.

```
Stage 0  DB schema/data copy-not-move + verify     (no VM stop; copy only)   [GATE: Jason quiescence, laptop-root apply]
Stage 1  verdify-site  (Quartz/nginx)              stateless, no device dep  [GATE: Jason stop]
Stage 2  verdify-api   (FastAPI, apps-LB VLAN 7)   stateless DB-reader       [GATE: Jason stop]
Stage 3  verdify-mcp   (planner tool surface)      ClusterIP DANGER surface  [GATE: Jason stop]
Stage 4  grafana / umami / goaccess / traefik      supporting surfaces       [GATE: Jason stop, per service]
Stage 5  DB write-ownership handoff                atomic; one writer only   [GATE: Jason quiescence]
Stage 6  ingestor / setpoint dispatcher  ── LAST, SEPARATELY GATED ──        [GATE: Jason — §3.4 spike PASS + first-setpoint + single-writer stop]
```

Rationale for the order: site → api → mcp move zero device risk (stateless / read-only /
ClusterIP). The DB (Stage 0 copy, Stage 5 write-handoff) is split: the **copy** is
non-destructive and happens early; the **write-ownership handoff** is the atomic quiescent
moment and happens just before the device loop. The **ingestor is dead last** and is gated
separately because it is the only workload that touches the live ESP32.

---

## 2. Stage detail — precondition / action / verify / rollback / gate-owner

Every stage is **additive then cutover**: the k3s side comes up and is proven while the VM
service still runs; the VM service is stopped only at the named `[GATE: Jason]` after the
verify passes and a soak window clears. `systemctl start` / `docker compose up -d` of the
untouched VM service is always the rollback.

### Stage 0 — TimescaleDB schema + data copy-not-move (NO VM stop)

- **Precondition:**
  - PR #55 merged (James) so `deploy/k8s/**` exists on `main` for ArgoCD to track; registry
    secret-meta PR #8 merged; ArgoCD Application PR #263 merged; `verdify-staging` namespace
    created (laptop-root); `verdify-app-secrets` + `verdify-ghcr-pull` sealed and synced
    (laptop-root, protected runner). ESP32 PSK NOT needed yet (ingestor stays VM-side).
  - The `verdify-db` StatefulSet is up Healthy on a Retain `local-path` PVC in
    `verdify-staging`, empty. The `verdify-migrate` PreSync Job (`db/Dockerfile.migrate`,
    postgres:16-alpine replaying `db/schema.sql` + migration 000) has populated the schema.
- **Action (copy, never move — handoff §3.6):**
  1. `[GATE: Jason]` pick a quiescent moment (low write volume). This is a READ on the VM —
     no writer is stopped at Stage 0.
  2. Take a consistent custom-format dump on the VM: `pg_dump -Fc` of the live TimescaleDB
     (`127.0.0.1:5432`, named volume `tsdb_data`). Dump lands on local NVMe / NAS — **never
     a live DB file on NFS**.
  3. Restore the dump into the k3s `verdify-db` StatefulSet via an idempotent restore runner
     (a SEPARATE gated runbook — the in-cluster `verdify-migrate` Job is a schema-only
     placeholder, NOT the data restore; do not conflate them).
- **Verify (parity, not just "it ran" — handoff §3.6 careful bit):**
  - Row-count equality per hypertable: run the same `SELECT count(*)` queries inside the k3s
    DB and the VM DB; assert equal (~2.5M+ rows; ~58% is `setpoint_snapshot`).
  - Hypertable list survived: `SELECT * FROM timescaledb_information.hypertables` matches.
  - Compression policy state survived: `SELECT * FROM timescaledb_information.compression_settings`
    + job list matches (timescale image, NOT pgvector — verify the extensions came across).
  - Migration version / schema parity: `db/schema.sql` snapshot + migration 000 applied;
    no drift vs the VM schema.
- **Rollback:** drop the k3s DB contents and re-restore; the VM DB is untouched and remains
  authoritative (it never stopped writing). PVC is Retain, so no data loss on PVC churn.
- **Gate-owner:** **Jason** (quiescence window), **laptop-root** (cluster apply of the
  restore Job). DoD ties: #11 (migrated + verified), #4 (gates green).

### Stage 1 — `verdify-site` (Quartz/nginx)

- **Precondition:** Stage 0 DB parity verified (site reads nothing device-side, but shares
  the apps ingress path). k3s site pod Healthy in `verdify-staging`; read-only NFS source PV
  bound; image pinned to a `sha-<gitsha>` ghcr tag via the jvallery image-pin PR.
- **Action:** route the public site hostname at the k3s apps ingress (Traefik behind
  Authentik) ALONGSIDE the VM nginx; confirm content parity. Then `[GATE: Jason]` stop the
  VM `verdify-site` compose service (`docker compose stop verdify-site` — NOT `down -v`).
- **Verify:** site renders on the apps-VLAN-7 surface; baked `GIT_SHA` (where surfaced)
  matches the pinned image; no 404/SSL regressions; Authentik header stripping prevents
  `X-Authentik-*` spoofing.
- **Rollback:** `docker compose up -d verdify-site` on the VM; re-point ingress. Data-free,
  fully reversible.
- **Gate-owner:** **Jason** (the VM-service stop confirmation), **laptop-root** (ingress/LB).
  DoD ties: #5 (APP on apps subnet), #1/#2 (CI/CD + ArgoCD drive it).

### Stage 2 — `verdify-api` (FastAPI, apps-pool LoadBalancer, VLAN 7)

- **Precondition:** Stage 1 soaked clean. k3s `verdify-api` Deployment Healthy (ClusterIP +
  apps-pool LB patch, reserved `192.168.7.x` — `192.168.7.21` is a PLACEHOLDER, the real IP
  is a `[GATE: laptop-root]` registry reservation). `VERDIFY_WRITE_API_KEY` present from the
  synced secret; `VERDIFY_ALLOW_UNAUTHENTICATED_WRITES` hard-unset in the ConfigMap;
  `0.0.0.0:8300` host bind retired (pod is ClusterIP, container bind on `:8080` is fine).
- **Action:** bring the k3s api up alongside the VM `verdify-api.service` + compose `api`;
  prove it reads the migrated DB and serves on `.7`. Then `[GATE: Jason]` stop the VM api
  (`systemctl stop verdify-api.service` AND `docker compose stop api`).
- **Verify:**
  - `/health` returns 200 (current endpoint, `api/main.py:911`). **`/health/detailed` with
    the baked `VERDIFY_GIT_SHA` is NOT yet implemented** — it is the #1 cross-cutting
    requirement and an api-scope-owner code change (proposed, not edited here). Until it
    exists, verify image==source via the ghcr digest pinned in the ArgoCD Application, not
    via the endpoint.
  - Crop catalog / public data-health / planner-health endpoints return parity with the VM.
  - Write/admin endpoints fail CLOSED without the key (`require_write_access()`); confirm the
    unauth-write escape hatch is closed.
  - Identity headers (`X-Authentik-*`) are stripped at the ingress and not spoofable.
- **Rollback:** `systemctl start verdify-api.service` / `docker compose up -d api`; the VM
  api is stateless and DB-reading, so restart is instant and lossless.
- **Gate-owner:** **Jason** (stop), **laptop-root** (LB IP). DoD ties: #5, #1, #9 (no
  plaintext `.env`).

### Stage 3 — `verdify-mcp` (planner tool surface — ClusterIP DANGER surface)

- **Precondition:** Stage 2 soaked clean. k3s `verdify-mcp` Deployment Healthy, **ClusterIP
  only** (never an LB IP — handoff §3.2), non-root, transport auth that **fails CLOSED**
  (today there is NO transport auth — add a guard by WRAPPING, never by changing tool
  semantics; any tool-surface edit needs the full firmware PR artifact set +
  coordinator(iris-dev) + Iris concurrence — out of this runbook's scope).
  Probe is `tcpSocket:8000` (FastMCP serves `/mcp`, not `/health`, so an httpGet `/health`
  probe 404s and the pod never goes Ready — already encoded in `mcp-deployment.yaml`).
- **Action:** bring the k3s mcp up alongside the VM `verdify-mcp.service`. Point the planner
  (`iris_planner` / `verdify-planner` / hermes-iris) at the k3s mcp by **cluster DNS**, not
  `host.docker.internal`. Prove the 18+ typed tools answer identically. Then `[GATE: Jason]`
  stop the VM `verdify-mcp.service`.
  > **DANGER-SURFACE CARE:** mcp exposes `set_plan` / `set_tunable` — tools that change live
  > greenhouse behavior. Cutover must not double-serve writes. Confirm the planner points at
  > exactly one mcp (k3s OR VM, never both) at the instant of stop. NetworkPolicy: mcp ←
  > in-namespace only.
- **Verify:** tcpSocket probe Ready; planner round-trips a read-only tool (e.g. a status
  query) against the k3s mcp; NO `set_plan`/`set_tunable` is fired as a verify (those are
  device-affecting); `host.docker.internal` reference retired.
- **Rollback:** `systemctl start verdify-mcp.service`; re-point the planner back to the VM
  mcp. Stateless, reversible.
- **Gate-owner:** **Jason** (stop + planner re-point). DoD ties: #5 (ClusterIP-private), #1.

### Stage 4 — grafana / umami / goaccess / traefik (supporting surfaces)

- **Precondition:** Stages 1–3 soaked clean. k3s grafana/umami/goaccess Deployments Healthy
  (ClusterIP + ingress); k3s Traefik is the apps ingress. Per-service secrets synced
  (`verdify-grafana`, `verdify-umami`).
- **Action:** per service, prove the k3s instance, then `[GATE: Jason]` stop the matching VM
  compose service ONE AT A TIME (`docker compose stop grafana` / `umami` / `goaccess`).
  Traefik is cut last within this stage so ingress never drops.
- **Verify:** dashboards render with parity data; umami analytics ingest; goaccess serves;
  Traefik routes all migrated surfaces.
- **Rollback:** `docker compose up -d <service>` per service. Reversible.
- **Gate-owner:** **Jason** (per-service stop). DoD ties: #5, #2.
  > **mosquitto note:** the local broker's placement is part of the §3.4 device decision —
  > the ESP32 speaks native API only (no `mqtt:` block in `greenhouse.yaml`), but Sentinel
  > occupancy uses MQTT. If the device path needs the LOCAL broker, mosquitto stays near the
  > device VLAN and is NOT cut here — it moves (or stays) with Stage 6.

### Stage 5 — DB write-ownership handoff (the atomic quiescent moment)

- **Precondition:** Stages 0–4 done; only the device-touching ingestor still writes the DB
  from the VM. All k3s read services are proven against the migrated DB.
- **Action `[GATE: Jason — quiescence]`:** at a quiescent moment, hand DB-write ownership
  from the VM stack to the k3s DB **atomically**. Practically this is coupled to Stage 6:
  the VM ingestor (the remaining writer) is the thing that must stop writing the VM DB the
  instant the k3s ingestor starts writing the k3s DB. **Both stacks must NEVER write the
  same DB.** Do a final incremental `pg_dump`/replay to catch rows written since Stage 0,
  then re-verify parity (row counts + hypertable + compression) before flipping the writer.
- **Verify:** final row-count parity after the incremental catch-up; the migration version
  matches; a continuity probe (`max(ts) FROM climate`) shows no gap across the handoff.
- **Rollback:** re-point writers back to the VM DB (it was never deleted); the soak window
  exists for exactly this. PVC Retain on both sides.
- **Gate-owner:** **Jason** (quiescence + writer flip). DoD ties: #11, #6.

### Stage 6 — ingestor / setpoint dispatcher (LAST, SEPARATELY GATED)

This is the only stage that touches the live ESP32. It does NOT proceed until **all** of the
following independently hold. It is intentionally the final, separately gated step.

- **Precondition (ALL required):**
  1. **§3.4 device-VLAN reachability spike = PASS, re-run for real under live load.** A k3s
     pod demonstrably reaches ESP32 `192.168.10.111:6053` (ESPHome native API), HA
     `192.168.30.107:8123/1883`, local MQTT, and Frigate `192.168.30.142:5000/1984` within
     the **5–10s occupancy→light SLA** (compare to baseline ~95% confirm, p50 37s / p95 81s
     band-change). The handoff records a 2026-05-30 read-only spike that PASSED at ~8ms over
     plain L3 routing (no firewall change), but it must be re-confirmed under live load and
     with the egress NetworkPolicy declared (`192.168.10.0/24:6053` + HA/MQTT/Frigate). The
     egress allow is a `[GATE: Jason]` firewall/router-posture surface — currently a
     COMMENTED `gated-§3.4` placeholder in `deploy/k8s/base/networkpolicy.yaml`.
     **If the spike does NOT clear, the ingestor stays VM-side permanently** — that recorded
     decision ALSO satisfies DoD #6/#8.
  2. **ESP32 PSK sealed + synced** `[GATE: Jason — device-affecting]`. The
     `verdify-esp32-psk` secret-meta is kept separate and flagged; confirm rotate-at-seal vs
     carry-existing. **CANONICAL value lives in the VM ingestor runtime env (sha 127f85d0),
     reconcile-at-source — NEVER re-flash.** Never trigger an OTA as a side effect of sealing.
  3. **Single-writer invariant enforced in manifest.** Base `ingestor-deployment.yaml` is
     `replicas:1` + `strategy:Recreate` (RollingUpdate is FORBIDDEN — a second pod mid-rollout
     would double-push and thrash the ESP32 heap). Staging overlay pins `replicas:0`
     (`ingestor-replicas-zero.yaml`) precisely so ArgoCD selfHeal cannot bring up a second
     live-ESP32 writer before this gate. Flipping to `replicas:1` is itself a gated edit.
- **Action (the careful end):**
  1. `[GATE: Jason — first live setpoint from a pod]` Bring the k3s ingestor up to
     `replicas:1` and confirm the FIRST real setpoint push from the pod to the ESP32. This is
     the moment the refactor first touches the greenhouse — Jason confirms it explicitly.
  2. `[GATE: Jason — single-writer cutover]` Stop the VM `verdify-ingestor.service` at the
     **SAME INSTANT** the k3s ingestor scales to 1. **Never two writers.** The cleanest
     choreography: stop the VM unit FIRST (its single aioesphomeapi connection releases),
     confirm release, THEN scale the k3s pod to 1 so it opens the only connection — or a
     coordinated swap where the overlap window is zero. Jason confirms this stop.
- **Verify:**
  - Exactly one process holds the ESP32 native-API connection (single-writer held).
  - Confirm-rate + band-change latency within the audited baseline (~95% confirm, p50 37s /
    p95 81s); occupancy→light path completes in 5–10s.
  - Tempest UDP broadcast (L2-local, direct to the ESP32) confirmed unaffected — it is out of
    the pod's path entirely; do NOT relay it.
  - The ESP32 still owns relay safety deterministically (8-state FSM, 5s loop) — no safety
    logic moved cloud-side.
- **Rollback:** scale the k3s ingestor to 0; `systemctl start verdify-ingestor.service` on
  the VM (its `ExecStartPre` pkill guard ensures a clean single connection). The VM unit was
  never deleted; the soak window exists for exactly this.
- **Gate-owner:** **Jason** (every sub-step: spike sign-off, PSK seal, first setpoint,
  single-writer stop). DoD ties: #6, #8, #7 (firmware OTA NEVER part of this), #11.

---

## 3. Definition-of-Done checklist (all 11 — current state, accurate to `f350bcd`)

The refactor is DONE only when ALL 11 hold, each independently proven (handoff §5). Current
state reflects this branch (`firmware/cicd-golden-path` @ `f350bcd`) and the three open PRs
(verdify-platform PR #55, agent-fleet-control PR #8, jvallery/agents PR #263).

| # | DoD item | Current state | Gated on |
|---|---|---|---|
| **1** | Repo-driven CI/CD: push → tests → `ghcr.io/jvallery/verdify-<comp>:sha-<gitsha>`, `GIT_SHA` baked, surfaced at `/health/detailed`; manual `compose up`/`systemctl restart` retired for migrated services | **PARTIAL.** `container-publish.yml` + `k8s-manifests.yml` + 4 Dockerfiles (api/mcp/ingestor + `db/Dockerfile.migrate`) PR-open in PR #55; `GIT_SHA` baked into image env; all 3 images build locally. **`/health/detailed` NOT implemented** (api has only `/health`, `api/main.py:911`) — an api-scope-owner code change, proposed not edited. Publish fires only after PR #55 merge + the jvallery promotion-workflow + `AGENT_FLEET_PROJECT_TOKEN`. | **James** (PR #55 merge); api scope owner (`/health/detailed`); laptop-root (promotion wiring) |
| **2** | ArgoCD Application pins ghcr SHAs + selfHeals; rollback = revert the pin | **PR-OPEN.** PR #263 (`jvallery/agents`) — Application `verdify-local-staging`, `path: deploy/k8s/overlays/local-staging`, `destination.namespace: verdify-staging`, `prune:false selfHeal:true`, `CreateNamespace=false`. Not merged, not reconciled. | **laptop-root** (review + merge + reconcile) |
| **3** | Backstage: repo-root `catalog-info.yaml`; registry renders; portal shows entity graph + live k8s status + owner + `spec.system` | **PARTIAL.** `catalog-info.yaml` PR-open in PR #55; registry render PR-open in PR #8. Live portal status appears only post-deploy. | **James** (PR #55); **laptop-root** (PR #8); post-deploy reconcile |
| **4** | Gates green: registry `make validate` + `make verify-reproducible`; app-repo test/lint + `k8s-manifests` kubeconform | **DONE for the artifacts.** `make lint` exit 0; `kustomize build …/local-staging \| kubeconform -strict -ignore-missing-schemas` 16/16 Valid; base 15/15; `make validate` + `make verify-reproducible` exit 0; actionlint exit 0 (all run 2026-05-30). Re-runs in CI on PR #55. | (green; re-verified by CI on merge) |
| **5** | APP on apps VLAN 7 (MetalLB apps-pool reserved `.7.x`, Traefik+Authentik, identity headers not spoofable, ClusterIP-private rest, NetworkPolicy default-deny) | **DESIGNED / PR-OPEN.** api-loadbalancer patch (apps-pool, `192.168.7.21` PLACEHOLDER), mcp ClusterIP, NetworkPolicy default-deny + scoped allows all in PR #55. Real `.7.x` reservation + Traefik/Authentik ingress + header stripping wired post-deploy. | **laptop-root** (`.7.x` reservation + ingress); post-deploy |
| **6** | Dispatcher operational: single-replica Deployment (OR recorded VM-side decision); real setpoints; baseline confirm/latency; single-writer held | **DESIGNED.** Base `replicas:1 strategy:Recreate`; staging overlay pins `replicas:0` for device safety. Recommended boundary = ingestor stays VM-side until §3.4 spike clears. No real setpoint pushed. | **Jason** (Stage 6: spike + first setpoint + single-writer stop) |
| **7** | Firmware pipeline intact: CI builds+validates artifacts (compile, 16 invariants, replay-diff THRESHOLD_PCT=0) but NEVER flashes; `make firmware-deploy` unchanged | **DONE.** `container-publish.yml` publishes NO firmware image and never flashes/OTAs; `firmware/*` is doc-only in the change-impact resolver; existing `ci.yml` firmware gate jobs untouched; `make firmware-deploy` preflight/bake/auto-rollback path unchanged. | (held; no action) |
| **8** | Device networking proven from k3s (ESP32/HA/MQTT/Frigate within 5–10s SLA) OR recorded VM-side decision | **NOT DONE — GATED SPIKE.** A read-only 2026-05-30 spike PASSED (~8ms over plain L3, no firewall change), but must be re-run under live load with the egress NetworkPolicy declared. Egress allow is a COMMENTED `gated-§3.4` placeholder in `networkpolicy.yaml`, disabled. | **Jason** (firewall/router posture; spike sign-off) |
| **9** | Secrets SOPS-sealed; no plaintext `.env` in runtime contract; ESP32 PSK/OTA per Jason | **PARTIAL.** 3 secret-meta files PR-open in PR #8 (NO values); `verdify-esp32-psk` kept separate + flagged device-affecting. Sealing not run. ESP32 PSK canonical value is in the VM ingestor runtime env (sha 127f85d0); the esphome `secrets.yaml` key (sha df2784f9) is DRIFTED — reconcile-at-source, never re-flash. | **laptop-root** (seal/sync on protected runner); **Jason** (ESP32 PSK/OTA — device-affecting) |
| **10** | VSCode-remote dev in k3s (`placement.mode: pod`, Retain PVC, Remote-SSH `:2222`) | **NOT STARTED** (P8, out of this run's scope). Registry dev-agent entries (`verdify-saas`, `verdify-ingestor`) still `placement.mode: vm`. | **Jason** (retire VM tmux lanes — multi-agent scope); **laptop-root** (agent pod + agents-pool LB) |
| **11** | Source decommissioned only when green+proven; service-by-service (never the VM); TimescaleDB migrated + verified (row + hypertable + compression parity), copy-not-move | **NOT DONE — GATED.** VM stack fully intact and authoritative; nothing stopped. DB Job in PR #55 is a schema-only PreSync placeholder; the copy-not-move data restore + verify is a separate gated runbook (Stage 0 / Stage 5 above). | **Jason** (every source-service stop + quiescence); **laptop-root** (cluster apply) |

**Summary:** DoD #4 and #7 are DONE; #1/#3/#5/#9 are PARTIAL (PR-open artifacts, post-deploy
or scope-owner steps remain); #2 is PR-open; #6 is designed; #8 is a gated spike; #10 not
started; #11 not done (nothing stopped). No DoD item requires a step that has been or will
be taken autonomously — each remaining item is gated on Jason, laptop-root, James, or a
scope owner.

---

## 4. The `live/platform-main` ↔ `main` reconciliation note (PR #55)

**Verified on this branch 2026-05-30 (read-only `git rev-list` / `git merge-base` / `gh pr view`):**

| Comparison | Result | Reading |
|---|---|---|
| `origin/main...origin/live/platform-main` | `0  4` | `main` is 0 ahead, `live/platform-main` is **4 ahead** (the Vanda work) |
| `origin/main...firmware/cicd-golden-path` | `0  7` | the branch is 0 behind / **7 ahead** of `main` |
| `origin/live/platform-main...firmware/cicd-golden-path` | `0  3` | the branch is 0 behind / **3 ahead** of `live/platform-main` |
| `merge-base(cicd, live/platform-main)` | `aa6518c` | == the tip of `live/platform-main`; the branch is built **directly on top of production** |
| `merge-base(cicd, main)` | `fb17f43` | == the tip of `main` |

**Composition of the 7 commits PR #55 carries (oldest → newest):**

1. `9b7eb80` — docs: state audit + Vanda/band-compliance/digital-twin designs + backlog  ┐
2. `e7781a3` — Vanda band/compliance rearchitecture + companion firmware OTA bundle       │ the 4
3. `f2bad50` — Vanda sprint-2: IRR-3/4 misting bursts + tunable curation + learning-loop  │ live-only
4. `aa6518c` — Vanda sprint-3: software backlog close-out (146/149/150 staged, hygiene)   ┘ commits
5. `47ddf92` — CI/CD golden-path: deploy/k8s + Dockerfiles + CI workflows + program doc   ┐ the 3
6. `d9f9294` — CI/CD staging fixes: real migration runner + MCP probe + ESP32 key reconcile│ CI/CD
7. `f350bcd` — Device-safety: pin staging ingestor replicas:0 + overlay verdify-migrate    ┘ commits

All 4 live-only commits are confirmed **CONTAINED** in `firmware/cicd-golden-path` (verified
with `git merge-base --is-ancestor` per commit). The branch = `main` + the 4 Vanda commits +
the 3 CI/CD commits.

**Does merging PR #55 reconcile `main` with `live/platform-main`?** **YES.** PR #55
(`firmware/cicd-golden-path` → `main`, state OPEN, mergeable MERGEABLE per `gh pr view 55`)
carries the operational Vanda work (the 4 commits that make `main` 4-behind) AND the new
CI/CD golden-path work (3 commits) in one merge. Merging it brings `main` even with — in
fact 3 ahead of — `live/platform-main`:

- Before merge: `main` is 4 behind `live/platform-main`; `live/platform-main` does NOT yet
  contain the CI/CD work.
- After merge: `main` contains all 4 Vanda commits (no longer behind `live`) PLUS the 3
  CI/CD commits. `live/platform-main` would then be 3 BEHIND `main` (it lacks the CI/CD
  commits) until separately fast-forwarded/merged.

**Follow-on reconciliation (separate, NOT part of PR #55):** after PR #55 lands on `main`,
`live/platform-main` (production) still lacks the 3 CI/CD commits. Bringing the CI/CD work
to production is a SECOND gated step — a fast-forward/merge of `main` (or the 3 CI/CD
commits) into `live/platform-main`. That is deploy-behavior-changing on the production
branch (handoff P0 STOP-and-ask) and must be confirmed with Jason; the CI/CD commits are
additive (no `firmware/lib/**` / `greenhouse_logic.h` / `entity_map.py` / `mcp/server.py`
semantic edits), so the firmware-artifact gate is satisfied, but the merge itself is James's
call. The CI triggers in `ci.yml` were widened to `[main, live/platform-main]` so the 8 gate
jobs fire on the production branch; once reconciled, the triggers can be trimmed to the
single canonical branch.

**Branch-name correction (carry forward):** the handoff's literal `origin/live` is a 404
(`gh api .../branches/live` → Branch not found); the canonical production branch is
`origin/live/platform-main` (the `platform-main` token IS part of the ref). Every downstream
`origin/live` / `vm-verdify` reference (seal-secret `--remote`, ArgoCD `targetRevision`,
`git rev-list ...origin/live`) must use `live/platform-main` / `vm-docker-iris` or it
silently resolves to nothing.

**Merge owner:** **James (VerdifyConsultancy)**. `VerdifyConsultancy/verdify-platform` is
READ-ONLY to this agent; PR #55 is reviewed and merged by James (coordinator for the org).
`.github/**` is shared infra, so the workflow changes get coordinator review. Do NOT push
directly or merge autonomously.

---

## 5. What this runbook does NOT do (explicit non-actions)

- No `kubectl` / ArgoCD apply / sync. No namespace creation. No `local-k8s-secret-sync` run.
- No secret value read/echoed/sealed. No PR merge. No branch rename/merge.
- No device touch: no setpoint push, no ESP32 native-API session opened, no OTA, no flash.
- No firewall/router/VLAN posture change. No NetworkPolicy egress enabled (the §3.4 allow
  stays commented).
- No live-service stop. The VM stack is fully intact and authoritative throughout.
- No edit to `firmware/lib/**`, `greenhouse_logic.h`, `entity_map.py`, `mcp/server.py`
  semantics. Track A (greenhouse alive) was never at risk.
