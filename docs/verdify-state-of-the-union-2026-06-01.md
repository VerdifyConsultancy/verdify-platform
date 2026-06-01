# Verdify — State of the Union (2026-06-01)

*Authoritative platform-migration status doc. Verified live this date against k3s (`verdify-agent.config`), the legacy VM (`vm-docker-iris`), GHCR, and GitHub Actions across the VerdifyConsultancy + jvallery orgs. Every "working" claim is evidence-grounded; unverified items are flagged.*

---

## 1. Executive Summary

Verdify is mid-migration from a single legacy VM (`vm-docker-iris`) to a 3-environment k3s platform. **The greenhouse is alive and fully under control — but 100% of production control, the authoritative database, and 100% of working observability still live on the legacy VM.** The k3s side has delivered a genuinely excellent, GitOps-managed, device-safe **staging** environment, but **dev and prod are authored-only or empty**, and the safety-critical pieces for a real cutover (planner/setpoint images, MQTT fan-out, device-route monitoring, prod data migration, twin-trust gating) are not yet running.

**Overall status: STAGING-COMPLETE, CUTOVER-BLOCKED.** Staging is the proof that the target shape works. Production cutover is blocked on two independent fronts: (1) **mine** — `verdify-planner` and `verdify-setpoint-server` container images do not exist (GHCR 404, both pinned to all-zero placeholder digests), so even if dev/prod were applied they would `ImagePullBackOff`; and (2) **out-of-lane** — the dev/prod ArgoCD Application CRs are authored but not applied (laptop-root gate), and the single-writer device hand-off + DNS/TLS/firewall are Jason/network-infra gated.

The honest critical path to cutover: **build planner + setpoint images → apply dev CR → migrate prod DB → establish twin-trust → Jason single-writer hand-off → decommission VM.** Nothing is at risk in the greenhouse today; the migration is well-architected but the long poles are real and the legacy VM cannot be touched until k3s has a home for control, data, *and* observability.

---

## 2. The Northstar

The target Jason is steering toward:

- **All of Verdify in k3s across 3 envs** — dev (integration) / stage (prod-mirror) / prod — each with its **own TimescaleDB copy** and its own **telemetry-ingesting ingestor**.
- **Single-writer invariant**: ONLY prod writes the ESP32.
- **Telemetry = MQTT fan-out**: prod publishes ALL sources; dev/stage subscribe read-only.
- **URLs**: prod `www/lab/api/graphs.verdify.ai`; dev/stage on `*.k3s.verdify.ai`.
- **Dual DNS, no SPOF**: Cloudflare + local split-horizon — Cloudflare-down still serves locally.
- **CI/CD**: a change to ANY Verdify repo (verdify-platform, verdify-www, verdify-planner, lab site, firmware) triggers full CI/CD into k3s (firmware → OTA).
- **The legacy VM (`vm-docker-iris`) fully DECOMMISSIONED.**

---

## 3. What's Working (confirmed-live)

### Staging environment in k3s — WORKING [MINE]
- `verdify-api` 1/1, `verdify-mcp` 1/1, `verdify-www` 1/1 (7h34m), `verdify-db` StatefulSet 1/1, all on `vm-k3s-node4`. `verdify-ingestor` is **0/0 by design** (device-safe). *(kubectl, verified 2026-06-01.)*
- `verdify-api` reachable via MetalLB LoadBalancer `EXTERNAL-IP 192.168.7.21`; mcp/www/db on ClusterIP. Endpoints populated.
- ArgoCD `verdify-local-staging` = **Synced + Healthy**, tracking `live/platform-main` path `overlays/staging`, selfHeal on. Staging is genuinely GitOps-driven.

### Database (staging) — WORKING, ahead of its own runbook [MINE]
- `verdify-db-0` 1/1, PVC `db-data-verdify-db-0` 50Gi **Bound on `synology-iscsi-ssd`** (iSCSI). Image `timescale/timescaledb:2.25.2-pg16` — **version-matched to live**, resolving the prior 2.17.2 skew.
- **Full-fidelity restore, not a `--data-only` copy**: all 19 hypertables present incl. `setpoint_snapshot` (6,105,931 rows), compression preserved at parity (climate 42/44, energy 42/44), extensions matched (timescaledb 2.25.2, vector 0.8.1, pgcrypto 1.3). 213 tables / 132 views.
- Staging data is a point-in-time restore frozen at `2026-05-31 01:47 UTC` (~35h stale) — **expected and correct** given the device-safe posture.

### Device-safety interlock (3-layer single-writer guard) — WORKING in staging [MINE]
Defense-in-depth verified live; any one layer failing still blocks a second writer:
1. **Code gate** — `ingestor/esp32_push.py`: default-deny, writes only when `VERDIFY_DEVICE_WRITE_ENABLED == '1'`.
2. **ConfigMap** — staging `verdify-config` has `VERDIFY_DEVICE_WRITE_ENABLED=0`; `=1` only in `overlays/prod`.
3. **Replicas:0** — `verdify-ingestor` pinned 0/0 in git so ArgoCD selfHeal can't revert a manual scale.
4. **NetworkPolicy** — `deny-esp32-egress` live in staging (allows `0.0.0.0/0 except 192.168.10.0/24`, blocking ESP32 at `.10.111:6053`).
- **RBAC reinforces it**: the verdify-agent SA is read-only in `verdify-prod` (cannot create deployments, cannot read secrets) — the agent physically cannot stand up a second prod writer.

### CI/CD partial — WORKING for 3/4 images + gates [MINE]
- In-org container-publish builds **api/mcp/ingestor** repo-linked to verdify-platform; CI gate jobs (lint, drift-guards, firmware compile/replay/invariants, no-new-fire-and-forget, service-restart-drift-guard) fire green on every push to `live/platform-main`.
- `image==source` verifiability: `/health/detailed` returns baked `git_sha=881d4d8…`, `git_ref=live/platform-main`, `db_reachable:true` (#58 closed, #100).
- Idempotent restore-aware migrate (`verify-not-rebuild`, no-op on populated DB) — proven on the live restored staging DB (no `alembic_version` row = verify path executed).
- `promote-diff-guard.yml` sound by design (asserts prod-promote PR only advances prod digests to match staging).

### Edge routing (staging) — WORKING [MINE + cluster]
- `*.vallery.net` + `api.verdify.ai` route through the shared apps Traefik (.7.10) after the ETP Local→Cluster fix and IngressRoute port fix (PRs #106–#109).

### Both dev + prod overlays render clean — WORKING [MINE, newly verified]
- `kustomize build deploy/k8s/overlays/prod` → **EXIT 0, 1393 lines**; `overlays/dev` → EXIT 0. **Correction to one reviewer's worry: the `components/mqtt-broker/` dir DOES exist** (`kustomization.yaml` + `mqtt-broker.yaml`), so the prod overlay does not fail to render on that account. The only render-time blocker is the placeholder image digests (see Gaps).

### Legacy VM — fully WORKING as production [authoritative, not yet decommissioned]
- **Live single ESP32 writer**: ESTAB `192.168.30.150:55940 → 192.168.10.111:6053` (ESP32 native API); `verdify-ingestor` + `verdify-setpoint-server` active.
- 13 docker services Up 26h (timescaledb healthy, traefik healthy, grafana, mqtt, promtail, goaccess, umami).
- Authoritative DB current: `climate` max ts `2026-06-01 12:24 UTC`, 1594 MB.
- **Firmware-deploy gate CLEAR**: 66 warning, **0 critical/high** open alerts.
- Full observability stack live and healthy (see §3 monitoring below).

### Observability (VM only) — WORKING [MINE]
- Grafana v12.4.1 (DB ok), `graphs.verdify.ai` HTTP 200, ~30 SQL-on-Timescale dashboards.
- Live in-process alert engine (`ingestor/tasks.py::alert_monitor`, every 300s): 58 new / 189 resolved in last 24h, Slack-integrated, auto-resolve, covers sensor/relay/vpd/temp/leak/esp32-health/planner/dispatcher.
- `v_data_pipeline_health` green across 8 sources; 54 `verdify_*` metrics to node-exporter textfile; promtail → remote Loki (`192.168.30.100:3100`); goaccess/umami up.

---

## 4. What's Delivered (build ledger)

**DONE-and-running (in staging / on VM):**
- In-org CI/CD publish for api/mcp/ingestor; CI gates; image==source health; idempotent migrate.
- `base/` manifests + `overlays/staging`; ArgoCD staging app Synced/Healthy.
- TimescaleDB 2.25.2-pg16 bump + 5 base NetworkPolicies + device-write interlock.
- DB on iSCSI; full-fidelity restore + validation runbook.
- Edge routing through shared apps Traefik; verdify-www in staging (ClusterIP).
- SOPS+age sealed-secret *structure* + `ghcr-jvallery-readonly` pull secret on all base pods.

**MERGED-but-inert (authored + merged, nothing running):**
- `overlays/prod` (namespace, device-write=1, allow-ingestor-device-egress, publish-all cm) — prod ns empty.
- MQTT fan-out broker component + ingestor publish-all/subscribe modes — no broker deployed anywhere.
- Firmware k3s golden-path (PR #55) — OTA still flashed from VM, not wired to CI.

**Authored-only (files present, never deployed, ns absent):**
- `overlays/dev` + `verdify-dev`/`verdify-prod` ArgoCD App CRs (`deploy/k8s/argocd/apps/`).
- `components/{planner, setpoint-server, www, hermes-iris}`.

**MISSING entirely:**
- `verdify-planner` + `verdify-setpoint-server` container images (GHCR 404).
- Any prod data migration to k3s.
- All k3s-native observability (metrics/logs/dashboards/alerts).
- Device-route / single-writer monitoring (#89).

---

## 5. Current Architecture / Topology

### Legacy VM `vm-docker-iris` — LEGACY PROD, fully live (authoritative)
```
ESP32 (192.168.10.111:6053)  <==WRITE==  verdify-ingestor.service (192.168.30.150)
                                          verdify-setpoint-server.service (2nd writer, grow-lights via HA)
verdify-timescaledb (docker, 1594MB, AUTHORITATIVE)  <-- ingestor writes telemetry+control
verdify-mqtt (mosquitto)  <-- real telemetry bus
Grafana / promtail / goaccess / umami / traefik (graphs/logs/analytics.verdify.ai)
Nightly pg_dump -> /mnt/iris/backups (NFS, unbounded retention)
/var/local/verdify/state -> planner state, firmware pins, dispatch (NO k3s landing zone)
/mnt/iris/verdify-vault -> lab/site content (40 uncommitted files)
```

### k3s — staging live; dev/prod pending
```
verdify-staging (LIVE):  api 1/1, mcp 1/1, www 1/1, db 1/1 (iSCSI 50Gi),
                          ingestor 0/0 (DEVICE_WRITE=0 + deny-esp32-egress + replicas:0)
                          ArgoCD verdify-local-staging Synced/Healthy
                          NO ESP32 path (device-dark by design)
verdify-prod  (EMPTY):    ns Active 19h, "No resources found"
verdify-dev   (ABSENT):   ns NotFound
ArgoCD apps:  verdify-local-staging (Synced/Healthy), verdify-edge (Unknown/BROKEN),
              verdify-dev/prod CRs authored but NOT applied (laptop-root gate)
```

### GCP — exiting
- `www` on Cloud Run being moved to k3s; Jason owns GCP teardown. (Out-of-lane.)

### Device-write path (single-writer reality)
- **TODAY**: VM ingestor is the *sole* live writer. k3s has **zero** device path live.
- **TARGET**: prod k3s ingestor (replicas:1, DEVICE_WRITE=1, allow-ingestor-device-egress) becomes sole writer; VM ingestor stopped atomically. Not yet started.

---

## 6. Gaps — Structured by Domain

### Services
- **MISSING** — verdify-dev ns absent; verdify-prod ns empty. [OUT-OF-LANE: apply = laptop-root; ns create = cluster.]
- **MISSING/BROKEN** — `verdify-planner` + `verdify-setpoint-server` images are **GHCR 404**, pinned to all-zero placeholder digests in BOTH prod and dev `kustomization.yaml`. Dev/prod cannot run control without these. [MINE — build, #117/#118.]
- **MERGED-but-inert** — MQTT broker + planner/setpoint/www/hermes-iris components authored, none deployed. [MINE.]

### Networking
- **MISSING/BLOCKED** — `*.k3s.verdify.ai` (dev/stage) and `lab`/`graphs`/`www.verdify.ai` (prod) DNS records + wildcard TLS cert don't exist; staging served on `*.vallery.net` with Traefik default cert. [OUT-OF-LANE: network-infra #53/#54, blocked on Cloudflare token.]
- **MISSING** — dual no-SPOF DNS (Cloudflare + split-horizon) — neither path live for verdify.ai. [OUT-OF-LANE.]
- **BLOCKED** — cross-VLAN k3s→ESP32 firewall allow not in place. [OUT-OF-LANE: network-infra #42.]
- **BROKEN** — `verdify-edge` ArgoCD app Unknown/Unknown: `InvalidSpecError`, ns `verdify-edge` not in AppProject `agent-fleet-management-migration` allowed destinations; sourced from `jvallery/agents`. [OUT-OF-LANE: agent-fleet, worth flagging.]
- *Note: NETWORKING reviewer report was rate-limited; networking findings above are corroborated from the cutover + monitoring + k3s reviewers, not from a dedicated networking pass — treat depth as partial.*

### Monitoring
- **MISSING** — no `/metrics` on api/mcp; no ServiceMonitor/PodMonitor/PrometheusRule/Grafana/Loki manifests anywhere in `deploy/`; the 54 `verdify_*` greenhouse metrics are VM-cron-only. [MINE.]
- **MISSING** — no alerting in ANY k3s env (alert engine lives in the ingestor, which is 0/0 in staging and absent in dev/prod). k3s is alert-dark. [MINE.]
- **MISSING** — device-route / single-writer monitor (#89): no blackbox TCP :6053 probe, no ESTAB==1 alert, no G10 post-deploy smoke gate. This is the highest-value missing safety piece for cutover. [MINE.]
- **BROKEN** — `allow-metrics-scrape` NetworkPolicy is doubly-dead (no `/metrics` behind the ports; targets `part-of=observability` but the live ns is labeled `part-of=agent-fleet`). [MINE.]
- **BROKEN** — Grafana render-cache-warm timer dead (#60), HTTP 500s since 2026-05-25. [MINE.]
- **OUT-OF-LANE** — `monitoring`/`observability` namespaces are agent-fleet-owned; verdify-agent SA is Forbidden to list pods/servicemonitors there. Needs RBAC grant to build a k3s-native stack.

### Logging
- **IMPROVABLE/partially-BROKEN** — VM promtail can't ship journald (image limitation), so the systemd logs of the most safety-critical services (ingestor/mcp/setpoint) are NOT in Loki — only their `state/*.log` files. [MINE.]
- **MISSING** — no in-cluster log pipeline (no promtail/vector DaemonSet, no fluent configmap) for verdify-* pods. [MINE.]
- **IMPROVABLE** — `scripts/alert-monitor.py` is a dead duplicate of the live `tasks.py::alert_monitor`; drift risk. Delete or make a thin shim. [MINE.]

### Data
- **BROKEN (data-loss risk)** — `/mnt/iris/verdify-vault` has **40 uncommitted files** (live lab CSVs, daily plans, vision snapshots, crop pages). If the VM is decommissioned before commit+push, this is lost. [MINE.]
- **HIGH (no landing zone)** — `/var/local/verdify/state` (planner state, firmware-version pins, dispatch dir) has **no k3s home**; planner continuity + firmware-pin history would be lost. [MINE.]
- **MISSING** — no prod data migrated to k3s (#28/#84 pg_basebackup STS). The long pole. [MINE + storage.]
- **IMPROVABLE** — `db/restore-job.yaml` is stale: hardcodes `timescale/timescaledb:2.17.2-pg16` + `--data-only` (a downgrade vs the 2.25.2 full-restore actually executed). If re-run for prod as-is it re-introduces the version skew. Reconcile runbook + restore-job. [MINE.]
- **IMPROVABLE** — nightly `pg_dump` is an inline VM root-crontab one-liner (not version-controlled, unbounded retention, no restore-verify), and writes to the VM being decommissioned. Re-home as a k3s CronJob. [MINE/Jason.]

### Security
- **BROKEN** — ESP32_API_KEY drift (#105): live ingestor key sha `127f85d0` ≠ esphome `api_encryption_key` sha `df2784f9`. Sealing correctly gated on Jason confirming canonical + rotate-vs-carry + no-reflash. [Jason-gated.]
- **MISSING** — the 14-key secret contract: live `verdify-app-secrets` holds only 5 keys; 8 of 13 app keys blocked on source reconciliation (name drift, path mismatch, absent source). A seal run today would abort. [James-gated.]
- **MISSING** — no CODEOWNERS file anywhere in the repo (CLAUDE.md references it; only the promote-guard exists). [MINE.]
- **IMPROVABLE** — SOPS/age delivery mechanism is designed + documented but **no Verdify secret has actually been sealed/synced**; the 5 live staging secrets were applied out-of-band. [MINE metas / out-of-lane runner.]

### CI/CD
- **BROKEN** — `k8s-manifests.yml` fails every run in 0s (GitHub "workflow file issue"). **Confirmed root cause**: the `push:` trigger declares BOTH `paths:` (line 26) and `paths-ignore:` (line 35) — GitHub Actions rejects both filters on one event. The manifest gate provides zero protection. **Fix: merge into a single `paths:` (or `paths-ignore:`) list.** [MINE, subset of #78.]
- **BROKEN** — digest-bump-back has **never fired** (`git log --author=verdify-ci` = 0 commits). The `bump-staging-digests` job gates on `migrate-image.result != 'failure'`, and migrate ALWAYS fails (#99 orphan) → bump skipped on every publish for ALL four images. Staging digests are pinned by hand. **Fix: gate per-image / treat migrate non-blocking.** [MINE.]
- **BROKEN** — `verdify-migrate` (and `verdify-www`, `verdify-site`) are orphaned GHCR packages (`repo:null`); CI `GITHUB_TOKEN` gets `permission_denied: write_package`, so migrate publish fails. Needs `admin:packages` to delete + republish repo-linked. [#99, Jason-gated.]
- **MISSING** — cross-repo GitOps promotion (jvallery/agents) is a documented no-op pending `AGENT_FLEET_PROJECT_TOKEN`. [OUT-OF-LANE.]
- **IMPROVABLE / decision needed** — firmware→OTA-on-commit conflicts with the deliberate Phase-0 freeze (48h bake, ≤1 OTA/week, critical-alert block, human `make firmware-deploy`). The northstar intent and the safety policy are in tension and need explicit reconciliation by Jason before anyone wires firmware CI to OTA.

---

## 7. Northstar Gap Matrix

| Northstar element | Current state | Gap | Owner |
|---|---|---|---|
| 3 envs (dev/stage/prod) in k3s | stage live; prod ns empty; dev ns absent | apply 2 App CRs; create dev ns | OUT (laptop-root/cluster) |
| Per-env TimescaleDB | stage db on iSCSI (point-in-time restore); prod/dev DBs absent | create prod/dev DB STS; migrate prod data (#28/#84) | MINE + storage |
| Per-env telemetry ingestor | stage 0/0; subscribe-mode code authored not shipped | dev/stage subscribe-from-MQTT (#114); SHADOW_MODE (#25) | MINE |
| MQTT fan-out (prod publishes, dev/stage subscribe) | broker + configmaps authored, render clean; not deployed | deploy broker (#113); ship subscribe code (#114) | MINE |
| Single-writer (only prod writes ESP32) | VM is sole live writer; prod overlay correct on paper | atomic VM→prod hand-off (#40), gated by twin-trust (#31) | OUT (Jason) gated by MINE |
| prod URLs www/lab/api/graphs.verdify.ai | only api.verdify.ai authored; lab/graphs/www absent | DNS+TLS + www/lab routes (#116/#124) | MINE + OUT (DNS) |
| dev/stage on *.k3s.verdify.ai | IngressRoutes authored; DNS+cert absent | wildcard cert + DNS records | OUT (network-infra #53/#54) |
| Dual no-SPOF DNS (Cloudflare + split-horizon) | neither path live; served on *.vallery.net | split-horizon (#53) + CF token (#54) | OUT-OF-LANE |
| Per-repo CI/CD → k3s | api/mcp/ingestor publish; k8s-manifests BROKEN; bump never fires | fix paths conflict; decouple bump from migrate; build planner/setpoint | MINE |
| Firmware → OTA in CI/CD | OTA human-gated by design (freeze policy) | reconcile northstar vs freeze — decision, not code | MINE/Jason decision |
| VM decommissioned | fully live (13 containers, authoritative DB+writer, all observability) | everything above first | OUT (Jason #91) |

---

## 8. Cutover Plan & Readiness

### Readiness checklist
| # | Gate | State | Lane |
|---|---|---|---|
| 1 | Staging green in k3s | WORKING | MINE |
| 2 | dev/prod overlays render-clean | WORKING (both EXIT 0) | MINE |
| 3 | dev/prod ArgoCD App CRs authored | WORKING | MINE |
| 4 | App CRs **applied** to argocd ns | MISSING | OUT (laptop-root) |
| 5 | verdify-dev ns created | MISSING | OUT (cluster) |
| 6 | planner + setpoint images on GHCR | **MISSING/BROKEN (404)** | MINE (build) |
| 7 | verdify-mqtt image | WORKING (eclipse-mosquitto:2 upstream) | MINE |
| 8 | Prod DB migrated VM→k3s | MISSING | MINE + storage |
| 9 | Firmware-deploy gate clear | WORKING (0 critical/high) | MINE |
| 10 | Twin-divergence trust gating handoff | MISSING (#31 P0 open) | MINE |
| 11 | Single-writer atomic hand-off | NOT STARTED | OUT (Jason #40) |
| 12 | Split-horizon DNS + *.verdify.ai TLS | MISSING/BLOCKED | OUT (network-infra) |
| 13 | Cross-VLAN k3s→ESP32 firewall | BLOCKED | OUT (network-infra #42) |
| 14 | VM decommission | NOT STARTED | OUT (Jason #91) |

### The hand-off sequence (when ready)
1. Build + publish planner/setpoint images; repin digests (MINE).
2. laptop-root applies dev App CR; verify dev comes up clean (no ImagePullBackOff).
3. Migrate prod DB (pg_basebackup → prod STS), apply prod App CR (prod stays manual-sync, no selfHeal — correct device-safety design).
4. Establish twin-trust (#31 setpoint-coverage) so divergence between VM control and k3s shadow is provably bounded.
5. Build #89 device-route monitor + G10 smoke gate (ESTAB==1, /health/detailed GIT_SHA parity, staging-asserts-zero-device-writes).
6. **Jason single-writer hand-off (#40)**: atomically stop VM ingestor + setpoint-server, flip prod DEVICE_WRITE=1 + scale ingestor to 1. ESTAB to :6053 must transfer, never double.
7. Re-home observability (Grafana/alerting/metrics/Loki/goaccess) in k3s.
8. Commit vault; migrate `/var/local/verdify/state`; re-home pg_dump.
9. Decommission VM.

### Safe vs HARD-STOP
- **SAFE now**: all staging work, building images, fixing CI, authoring manifests, building k3s observability — none touch the device path.
- **HARD-STOP** until twin-trust + device-route monitor + Jason sign-off: anything that flips prod DEVICE_WRITE=1, scales prod ingestor, or stops the VM ingestor. The single-writer invariant is the line that must never be crossed accidentally — and the current 3-layer interlock + read-only prod RBAC are what keep it safe.

---

## 9. Risks

### To the live greenhouse (Track A) — currently LOW, by design
- The VM is the sole writer and is healthy (0 critical/high alerts, DB current, ESP32 ESTAB present). k3s is device-dark. The risk is concentrated entirely at the **cutover moment** (#40) — a botched hand-off could produce two writers or zero. Mitigations: 3-layer interlock, read-only prod RBAC, prod manual-sync ArgoCD. **Do not cut over without the #89 device-route monitor and twin-trust in place.**

### Data-loss risks
- **HIGH** — 40 uncommitted vault files; commit+push before any VM teardown.
- **HIGH** — `/var/local/verdify/state` (planner state, firmware pins) has no k3s landing zone.
- **MEDIUM** — prod DB not yet migrated; staging copy is 35h stale (needs step-7 top-up at cutover).
- **MEDIUM** — `db/restore-job.yaml` stale (2.17.2/data-only) would lose hypertables/compression if re-run as-is.
- **LOW** — pg_dump backups healthy but live on the VM being decommissioned; re-home before teardown.

### Single points of failure
- **SPOF today: the legacy VM** — it is control + data + observability + telemetry bus all in one box. This is the entire reason for the migration; until k3s has homes for all four, the VM cannot be retired.
- **No dual DNS yet** — verdify.ai not served from either Cloudflare or split-horizon in the target shape; staging on *.vallery.net. Northstar no-SPOF DNS unproven.
- **CI manifest gate dead** — k8s-manifests.yml fails silently, so manifest regressions could merge unvalidated.

---

## 10. Prioritized Next Steps

**MINE (verdify-owner), in order:**
1. **P0** — Fix `k8s-manifests.yml`: remove the `paths`/`paths-ignore` conflict (single filter). Restores manifest validation. (Trivial, high value.)
2. **P0** — Build + publish `verdify-planner` and `verdify-setpoint-server` images (#117/#118); repin digests off the all-zero placeholders. Hard blocker for dev/prod.
3. **P0** — Decouple digest-bump-back from the migrate failure (gate per-image) so the loop runs for api/mcp/ingestor.
4. **P0** — Commit + push the 40 uncommitted vault files (#104) — data-loss prevention.
5. **P1** — Ship MQTT subscribe-from-MQTT (#114) + SHADOW_MODE (#25); deploy broker component (#113).
6. **P1** — Twin-trust: setpoint-coverage gap (#31) + TWIN-1/2/3/6 — gates the hand-off.
7. **P1** — Prod TimescaleDB STS + pg_basebackup migrate (#28/#84).
8. **P1** — Device-route monitor + G10 smoke gate (#89).
9. **P1** — Reconcile `db/restore-job.yaml` + runbook to 2.25.2 full-restore.
10. **P2** — k3s-native observability (/metrics, ServiceMonitors, in-cluster Loki, per-env Grafana); fix #60 render timer; add CODEOWNERS; migrate `/var/local/verdify/state`; re-home pg_dump as k3s CronJob.
11. **Decision (with Jason)** — reconcile northstar firmware-OTA-on-commit vs Phase-0 freeze policy.

**laptop-root:**
- Apply `verdify-dev` + `verdify-prod` ArgoCD App CRs into the agent-fleet gitops repo (gate 4). Do dev FIRST and only after planner/setpoint images exist, or dev will ImagePullBackOff.

**Jason (operator gates):**
- #99: delete orphaned `verdify-migrate`/`verdify-www`/`verdify-site` GHCR packages (`admin:packages`) → unblocks migrate publish + full bump loop.
- #105: confirm canonical ESP32_API_KEY (`127f85d0`) + rotate-vs-carry + no-reflash → unblocks secret sealing.
- #40/#71: single-writer device hand-off (after twin-trust + #89).
- #91/#35: VM decommission (last).
- GCP teardown (www off Cloud Run).

---

## 11. Out-of-Lane Handoffs (consolidated)

**Jason / laptop-root:**
- Apply dev/prod ArgoCD App CRs; create verdify-dev ns.
- #99 GHCR orphan deletion; #105 ESP32 key confirmation.
- #40 single-writer hand-off; #91 VM decommission; GCP teardown.

**network-infra / nexus (jvallery/network-infra):**
- #53 split-horizon DNS; #54 wildcard `*.verdify.ai` cert (both blocked on Cloudflare account token, root-gated).
- #42/#43 cross-VLAN firewall to ESP32/HA/MQTT.
- `verdify-edge` ArgoCD app: add `verdify-edge` to AppProject `agent-fleet-management-migration` allowed destinations (sourced from jvallery/agents).

**cluster RBAC (note for Jason):**
- verdify-agent SA is Forbidden in `verdify-dev`, `monitoring`, `observability`. To build k3s-native Verdify monitoring, the agent needs read on observability/monitoring + ability to create ServiceMonitor/PodMonitor CRs in verdify-* namespaces (network-infra #64 tracks this).

**James (source reconciliation):**
- Resolve the 8 blocked app-secret keys (name-drift / path-mismatch / absent-source) before any `verdify-app-secrets` seal.

---

### Evidence index
- k3s: `kubectl` via `verdify-agent.config` (staging 1/1 api/mcp/www, db on synology-iscsi-ssd; prod ns Active 19h empty; dev ns absent; ArgoCD verdify-local-staging Synced/Healthy, verdify-edge Unknown).
- VM: ESTAB `192.168.30.150:55940→192.168.10.111:6053`; `verdify-ingestor`/`setpoint-server` active; 13 docker services Up 26h; `climate` max ts 2026-06-01 12:24 UTC; alert_log = 66 warning / 0 critical-high.
- GHCR: api/mcp/ingestor/migrate/site/www present; **planner + setpoint-server 404**.
- Render: `kustomize build overlays/{prod,dev}` both EXIT 0 (mqtt-broker component present); placeholder all-zero digests at `overlays/prod/kustomization.yaml:79,85`.
- CI: `k8s-manifests.yml` push trigger has both `paths:` (L26) and `paths-ignore:` (L35).
- Vault: 40 uncommitted files at `/mnt/iris/verdify-vault`.
- *Caveat: VM-Services and Networking reviewer passes were rate-limited; those domains are synthesized from corroborating reviewers + my own live spot-checks, not a dedicated deep pass — flagged accordingly in §6 Networking.*

Key paths: `/mnt/iris/verdify/deploy/k8s/{base,overlays/{staging,dev,prod},components,argocd/apps}`, `/mnt/iris/verdify/.github/workflows/k8s-manifests.yml`, `/mnt/iris/verdify/db/restore-job.yaml`, `/mnt/iris/verdify/docs/runbooks/db-copy-not-move.md`, `/var/local/verdify/state`, `/mnt/iris/verdify-vault`.

---

# Appendix A — Full cross-board backlog reconciliation (2026-06-01)

The body of this report was scoped to the **`VerdifyConsultancy/verdify-platform`** board (70 open). A full sweep of **every** issue board, list, and backlog doc surfaced two additional boards that track the Verdify migration and were **not enumerated** above, plus several genuinely-new items. They are added here so the report is complete against all boards.

## A.0 — Tracking topology (the migration is tracked in THREE places — a coordination risk)
| Board | Open | Verdify scope |
|---|---|---|
| `VerdifyConsultancy/verdify-platform` | 70 | The in-lane build backlog (epics #15/#69–#75/#111/#112; work items) — covered in the report body |
| `jvallery/agents` (fleet command center) | 44 | **Theme C** = the fleet-authoritative Verdify migration phasing (#277 + #298–#307), several **owned by `verdify-agent` (me)** |
| `jvallery/network-infra` | 53 | `area:verdify` = the out-of-lane DNS/cert/firewall/storage/cutover gates (#40–#64 subset) |
| `verdify-www / verdify-planner / verdify-site-legacy / verdify-vault` | 0 each | no issues; code-only repos (consolidation pending, see A.3) |
| `jvallery/agent-fleet-control` | 0 | GitOps registry; carries the ArgoCD apps + sealed secrets, no issues |

**⚠️ The same migration work is duplicated across boards** (e.g. DB migration = platform #28/#84 ≈ agents #302 ≈ network-infra #44; device cutover = platform #71/#91 ≈ agents #303 ≈ network-infra #40; re-home setpoint/hermes = platform #118/#119 ≈ agents #304 ≈ network-infra #55). One source-of-truth should be designated to avoid drift.

## A.1 — `jvallery/agents` Theme C (Verdify) — fleet-authoritative phasing
| # | Item | Owner | Maps to report / platform |
|---|---|---|---|
| #277 | EPIC: Theme C — Verdify device-safe k3s migration | verdify-agent | umbrella for all below |
| #298 | Merge verdify-platform PR #55 (GitOps source) | jason (gated) | **DONE** (PR #55 merged 352f299) — issue stale, close |
| #300 | Phase 1 — real verdify-py migrate image + DB ≥2.25.2 on synology-iscsi | verdify-agent | ≈ platform #57/#83/#84 (largely done) |
| #301 | Phase 0 — local-first foundation (split-horizon DNS + wildcard cert + IngressRoutes + device allow) | verdify-agent | ≈ #87 + out-of-lane DNS/cert |
| #302 | Phase 2 — DB migration (pg_dump iris→restore + re-arm jobs + validate) | verdify-agent | ≈ #28/#85 |
| #303 | Phase 3 — ATOMIC single-writer device cutover (HARD STOP) | **jason** | ≈ #71/#73 (the G9 gate) |
| #304 | Re-home setpoint-server :8200 + hermes-iris as k3s Deployments | verdify-agent | = #118/#119 |
| #305 | Phase 4 — web/content/observability tier + repair 2 FAILED content units | verdify-agent | = #88/#124/#59/#60 + k3s observability |
| #306 | **monorepo consolidation + DELETE 5 secondary repos (www/planner/site-legacy/agent-context/vault) + sunshine_club** | verdify-agent (James owns the deletes) | **NEW — not in report; see A.3** |
| #307 | Phase 5/6 — external WAN split-horizon + relocate James-owned iris lanes + VM decommission (HARD STOP) | jason (gated) | ≈ #90/#91 |
| #325 | Gated-decision register (the human gates on the critical path) | jason | the consolidated Jason-gate list |
| #323 | Provide Cloudflare verdify.ai DNS:Edit token (blocks *.verdify.ai wildcard cert) | **james** | gates network-infra #54 / the prod URLs |
| #334/#330 | SOPS→reconciler per-agent sealed secrets at scale | laptop-root | the per-env secret delivery mechanism |
| #361/#359 | .7.21 VIP off-cluster reachability (BGP/FIB) + node4 systemd hung | laptop-root | edge/substrate (node4 since fixed) |

## A.2 — `jvallery/network-infra` `area:verdify` — out-of-lane gates
| # | Item | Owner |
|---|---|---|
| #40 | verdify single-writer ESP32 device cutover (live-plant handoff, never automated) | **jason** |
| #42 | net-new Cluster↔IoT firewall allow (k3s node .35 → ESP32 .10.111:6053) + keep iris pinned | nexus |
| #43 | **net-new Cluster→Infra flows: HAOS MQTT :1883 + HA :8123 + WAN planner egress** | nexus | 
| #44 | TimescaleDB prod data migration (1.5GB / 6.14M rows) iris→k3s on synology-iscsi | verdify+nexus |
| #45 | **build real migrate image + alembic baseline + harden CI/CD (G1–G11)** | verdify+nexus |
| #53 | split-horizon DNS *.verdify.ai → .7.10 + IngressRoutes + DNS records + rewrite tunnel | nexus (CF-blocked) |
| #54 | *.verdify.ai wildcard cert via cert-manager DNS-01 (James CF token) | root (CF-blocked) |
| #55 | containerize hermes/planner gateway (:8642) + setpoint-server (:8200) | nexus |
| #64 | retire dead services + migrate stateful web tier + **verdify-agent RBAC** | verdify |
| #50/#51/#52 | k3s node faults + durable NAS NFS export + deprecate hanging nfs-rwx SC | root/jason |

## A.3 — Genuinely-NEW items the report did not capture (now added)
1. **#306 (agents) — monorepo consolidation + IRREVERSIBLE delete of 5 secondary repos** (www/planner/site-legacy/agent-context/vault) + sunshine_club, into the single `verdify-platform` monorepo. James owns the VerdifyConsultancy deletes; Jason co-gates. Supersedes/extends platform #102/#103/#62. **A strategic decision not previously in this report.**
2. **#43 (network-infra) — net-new Cluster→Infra cross-zone flows: HAOS MQTT :1883, HA :8123, WAN planner egress.** The MQTT/HA reachability the prod ingestor + the MQTT fan-out bus actually need — a networking dependency the report's networking section under-specified.
3. **#45 (network-infra) — alembic baseline + CI/CD hardening G1–G11.** The "real migrate image" is done (idempotent restore-aware), but the **alembic baseline / migration-versioning** layer is a distinct, unbuilt item.
4. **#64 (network-infra) — verdify-agent RBAC** for the new namespaces (my SA currently lacks verdify-dev access — confirmed: `Forbidden` listing verdify-dev).
5. **#323/#325 (agents) — the James Cloudflare-token dependency and the consolidated Jason gated-decision register** as first-class tracked blockers.

## A.4 — Backlog docs / lists (not GitHub issues)
`docs/BACKLOG.md` + `docs/backlog/{cross-cutting,firmware,genai,ingestor,web,saas,refocus,launch}.md` + `docs/backlog/verdify-unified-backlog-2026-05-29.md` (and the network-infra `docs/BACKLOG-CONSOLIDATED.md` referenced by its issues) are the **consolidation SOURCES** that fed the GitHub boards on 2026-05-29/05-31. They are now **superseded by the live boards** above; no uncaptured items remain in them beyond what the boards track. (If kept, they should carry a "superseded — see GitHub boards" header.)

## A.5 — Cross-org issue-number collision (disambiguation)
Issue numbers repeat across orgs. Where the report body cites a bare `#NN` it means **`verdify-platform`** unless noted. The DNS/cert/firewall/cutover citations resolve to **`network-infra`**: split-horizon DNS = network-infra **#53**, wildcard cert = network-infra **#54**, cross-VLAN device firewall = network-infra **#42**, single-writer cutover = network-infra **#40** / agents **#303**. (verdify-platform #40/#42/#53/#54 are unrelated: soil-dryout / pipeline-health / closed-GCP / closed-labels.)
