# Verdify Migration — Authoritative Document (VM → 3-env k3s)
Prepared for Jason · 2026-06-07 · Owner model: **four swim lanes** (Cluster / Application / Observability / Networking). Old Iris/Root/Nexus/Jason agent owners are retired; human-only calls are consolidated in §5.

> **STATUS 2026-06-20 — MIGRATION COMPLETE · THIS DOC IS HISTORICAL.** The VM→k3s migration is done: Verdify runs fully on k3s (ns `verdify-prod`), the M5 single-writer cutover happened, and the **iris VM (VMID 306 / Proxmox `onyx` / `.150`) is decommissioned and destroyed** — M6 complete (#91). #104 (commit the VM's vault working tree pre-decom) is moot: the lab site content is now rendered in-cluster from the DB by the lab-publisher, not committed off the VM. The **3-env model below is superseded by single-env prod-only** (dev/staging decommissioned 2026-06-16). Retained as the historical migration record; for current state read the repo `CLAUDE.md` and `docs/handoff/k3s-agent-handoff.md`.

---

## 1. Executive status

The migration is **migrate-as-is** (ESP32 stays a native-API server on `192.168.10.111:6053`, the ingestor dials out, #177 device re-arch DEFERRED) and is at **STAGING-AUTHORED / CUTOVER-BLOCKED-ON-APPLY**. The entire Application authoring lane is **done**: G2 (#188) and G3 (#189) DB-parity merged 2026-06-05, all eight images carry real pullable `@sha256` digests (#190/#191/#192 www/lab/setpoint, #171 planner), G1 client-skew fixed (#178), prod-dark overlay + App-CRs authored (#194/#187), and the M5 cutover runbook + G10/device-monitor specs are written (#183). **The one-line truth: nothing Iris/coordinator authored is actually applied to the live cluster** — the live ArgoCD SoT (`jvallery/agent-fleet-control`) runs only a hand-reconstructed `verdify-staging` raw-manifest set that sources *itself* with stale `ghcr.io/jvallery/*:staging` images, TimescaleDB **2.17.2** (G1 skew vs platform 2.25.2), `local-path` storage, and a **suspended** migrate Job; the verdify VM is still the public terminator and the sole device writer. The real remaining work is **apply + Networking + Observability**, gated on a small set of laptop-root operations and a handful of hard Jason gates, with `state-truth.md` L1–L15 **entirely empty** (zero confirmed live reads) so most "applied" assertions are **needs-live-confirm**.

---

## 2. End-state architecture (target across all 4 lanes)

**Cluster.** One live `vallery-local-k3s` (v1.35.5+k3s1; 3 server VMs node1–3 on VLAN30 .31–.33) runs Verdify as **three ArgoCD-managed envs** sourced from `verdify-platform` `deploy/k8s/overlays/{dev,staging,prod}` plus a transient **`prod-dark`** overlay. dev = AUTOMATED selfHeal+prune (device-dark: ingestor `replicas:0`, `deny-esp32-egress`); staging = AUTOMATED additive validation (ingestor `replicas:0`); prod + prod-dark = **MANUAL-SYNC** (device-write gate), `prune:false` everywhere to protect the DB STS/PVC. Each env has its **own** single-replica `verdify-db` TimescaleDB 2.25.2-pg16 StatefulSet on the `synology-iscsi-ssd` SC (Synology CSI, /volume1 SSD, reclaim=Retain, WaitForFirstConsumer, expandable) bound on a **worker** node, with a nightly `pg_dump -Fc` CronJob (`17 2 * * *`, SELECT-only) landing on the `verdify-db-dumps` RWX NFS PVC. ArgoCD AppProject `app-test` + an app-of-apps root live in the agent-fleet GitOps SoT.

**Cutover sequence.** `prod-dark` adopts the existing `verdify-prod` ns + DB STS as a **provably device-dark read/serve** stack for the M4 48h parity proof → **M5** Jason-gated single-writer cutover (`overlays/prod` becomes the ONE ESP32 writer: `VERDIFY_DEVICE_WRITE_ENABLED=1`, `allow-ingestor-device-egress`, ingestor `replicas:1`, setpoint-server) executed atomically with the VM control loop stopped → **M6** mask + decommission the iris VM (VMID 306, Proxmox node `onyx`, currently `migrate_later`).

**Application.** Every service runs as a repo-linked, digest-pinned GHCR image reconciled by ArgoCD. Six Python services (api/mcp/ingestor/setpoint-server/planner/migrate) build from the `verdify-platform` monorepo; `verdify-www` (Astro) and `verdify-lab` (Quartz, from `verdify-site-legacy` + `verdify-vault` content) build from their own repos. `api.verdify.ai` served by the k3s `verdify-api` pod **off the VM**; www/lab served from k3s (Cloud Run retired). **Single-writer invariant** held until M5; **firmware migrate-as-is** (no on-device HTTP client, no `api.verdify.ai` refs).

**Observability.** k3s-native: a metrics substrate (Prometheus + kube-state-metrics + node-exporter + cAdvisor) in a monitoring ns with ServiceMonitor/PodMonitor (zero exist today); cluster Loki via Promtail/Alloy DaemonSet replacing the VM-docker-only promtail (goaccess retired, #88); `verdify-grafana` on `graphs.verdify.ai` (20 TimescaleDB dashboards, PVC on iSCSI); the **`:6053` ESTAB==1 device-route monitor** live as continuous alerting (3-state: 0=writer-down page, 1=green, 2+=multi-writer page, IRIS-W013/#89); G10 post-deploy smoke (`scripts/k3s-smoke.sh`) as a hard cutover gate; `alert-monitor.py` re-homed into k3s. Open architectural fork: **reuse fleet `jvallery/monitoring-stack`** (already LAN-Prometheus + Loki@.30.100:3100 + blackbox-probing `*.verdify.ai`) vs a Verdify-dedicated in-cluster ns.

**Networking.** Split-horizon with **one** front door at apps-pool MetalLB VIP **192.168.7.10** (ADR-15/Model B'), reachable cross-subnet via UDM BGP/ECMP. LAN: UDM-local authoritative records resolve all `*.verdify.ai` + `*.k3s.verdify.ai` → .7.10, working WAN-down. WAN: the in-cluster cloudflared tunnel (`vallery-homelab`, 2-replica) forwards Host-preserving/noTLSVerify to the **same** .7.10. TLS via a cert-manager `wildcard-verdify-ai` Certificate once a `verdify.ai` Cloudflare DNS-01 solver is added (today `dnsZones:[vallery.net]` only). Device plane: a single durable cross-VLAN UniFi allow from the pinned k3s node → `192.168.10.111:6053`, source-locked, opened only at M5.1. CNI stays **flannel/VXLAN** (NetworkPolicies are contract-only, enforced at the UniFi trust-zone boundary); **Cilium re-CNI is a DEFERRED/locked non-goal**.

---

## 3. Gaps report by swim lane

### 3.1 Cluster
**Done:** all image 404s resolved across overlays (prod/prod-dark/staging/dev, no `sha256:0000`); G2 #188 + G3 #189 merged; G1 restore-job bumped to 2.25.2 (#178); `synology-iscsi-ssd` SC is an in-repo artifact, proven against a real worker-bound PVC in staging (commit `4024f4d`); prod DB backup substrate authored (#184); prod-dark overlay + App-CR (#194); all three Application CRs authored (`deploy/k8s/argocd/apps/verdify-{dev,prod,prod-dark}.yaml`); three-env overlays fully authored; M5 runbook + G10 spec (#183); M6 landing zone (#186/#130); Proxmox inventory mapped.

**Remaining (implementation):**
- ★ **Apply the 3 ArgoCD App CRs** to the live `argocd` ns by committing into `agent-fleet-control`. Authored-inert today. Apply `verdify-dev` first (no device path). Blocker: laptop-root only (coordinator has no `argocd` write). *#86, #111.*
- ★ **Re-point live `verdify-local-staging` App** at `verdify-platform overlays/staging`. Live staging still runs OLD `ghcr.io/jvallery/*:staging`, TS 2.17.2 (G1 skew), `local-path`, suspended migrate Job. Risk: immutable STS selector/SC churn. **needs-live-confirm (L3).** *`agent-fleet-control/manifests/verdify-staging/20-db-statefulset.yaml`.*
- ★ **Apply cluster-scoped `synology-iscsi-ssd` SC + bind `verdify-db-dumps` NFS static PV.** `dumps-pvc.yaml` is `storageClassName:""` → Pending until Root supplies the matching PV (NFS IP/export are platform facts not in repo). **needs-live-confirm (L6).** *#84, #28, #130.*
- ★ **Stand up prod DB substrate + copy-NOT-move restore** (`pg_dump -Fc` from VM → fresh STS on iSCSI → idempotent restore Job → verify rows + G1/G2/G3 parity). Base STS is still `storageClassName: local-path` (prod overlay does NOT retarget — immutable-field reason). *#84, #28, #72, `base/db-statefulset.yaml`.*
- ★ **Fill `state-truth.md` L1–L15** (TEMPLATE/EMPTY): pod inventory (L1), ArgoCD sync/health/revision (L2), staging source path (L3), dev device-dark + zero `:6053` ESTAB (L4), staging DB empty+iSCSI+worker (L5), storage (L6), SOPS secrets per ns (L7), Proxmox VMID306 recovery floor (L9). *#111, #135.*
- ★ **Resolve flannel-vs-Cilium NetworkPolicy enforcement.** All default-deny + `deny-esp32-egress` are declarative-only on flannel; in-cluster enforcement waits on the Root-owned Cilium re-CNI (ADR-17, planned not done). *#71, #27.*
- ★ **Run M4 48h prod-dark proof** (adopt running `verdify-prod` ns + DB STS, read/serve parity data, ZERO `:6053` ESTAB). Risk: prod-dark base STS is `local-path` and must match the live prod STS SC or ArgoCD fights an immutable diff (**needs-live-confirm of live prod SC**). *#73, #194.*
- ★ **Apply `verdify-prod` App (writer shape) — the M5 cutover.** HARD Jason gate. *#73, #86, #132, #27.*
- **M6 mask/decommission iris VM** (irreversible, post-M5, PBS recovery floor verified). *#91.*
- **Author/confirm AppProject `app-test` + app-of-apps root** in the fleet SoT (CRs declare it; def not in repo). *#86.*
- **Reconcile dev-cluster targeting** vs planned VMs 320/321/322 (DNS `pending_dns_record`, jvallery/agents#122). *jvallery/agents#25/#122.*

**Platform blockers:** SoT decoupling (fleet runs stale self-sourced staging); flannel non-enforcement; empty `state-truth.md`; iSCSI SC + NFS PV are Root-only applies; M5/M6 hard Jason gates layered behind the §3.4 device-VLAN sign-off + M4 48h proof, none runnable until the prod DB is restored on iSCSI.

### 3.2 Application
**Done:** G1/G2/G3 all resolved (#178/#188/#189); all six Python images + www/lab/setpoint/planner build with verified digests; #102 planner_graph folded into monorepo (CLOSED); www/lab/setpoint image pipelines done (#190/#191/#192); prod-dark device-dark stack (#194); planner_graph self-heals its own schema at runtime; ADR-0001 device-write API design (#185); firmware migrate-as-is confirmed.

**Remaining (implementation):**
- ★ **#105 reconcile canonical ESP32_API_KEY before sealing** (live `.env` sha `127f85d0` vs esphome `api_encryption` sha `df2784f9`). Cutover-adjacent: a key skew silently breaks the single device connection at M5. Needs Jason/Root live read; must NOT trigger re-flash. *#105, #30, #104.*
- Migration **147 reward swap** (compliance_v2). OFF cutover path (EPIC #134). Blocked on re-baseline ≥90% anchor reproduction (live ~50.9%, #17) + ≥1 clean 146 dual-write day. *#13/#17/#18/#19/#20/#21.*
- **Retire Cloud Run www path** (M2.3); `verdify-www/deploy.yml` still deploys Cloud Run, DNS still CNAMEs `ghs.googlehosted.com`. Also remove the dead Cloud Run **Deploy** workflow that reds CI every run. DEC-WWW Jason gate. *#116, #88, #120.*
- **Close #116/#117/#124/#127/#128** (image+CI halves done; remaining IngressRoute/DNS tails overlap Networking; bookkeeping).
- **Wire verdify-vault → lab rebuild** (M2.2). Vault has ZERO CI; needs `repository_dispatch` + #104 (64 uncommitted pages). *#124, #104.*
- **Fold planner_graph DDL into the migration ledger** (hygiene; runtime auto-create means no functional gap). *#117, #102.*
- **Activate `schema_migrations` ledger** (designed, not applied). *#72.*
- **Digital-twin track** (EPIC #14: #31 P0 → #32/#33/#34); twin tables NULL today. Off-path; #32 touches firmware (full PR-artifact gates). 
- **Track-A agronomy/data-integrity**: #38 (prune lessons, fixes failing test), #49 (resolved_at backfill) are the only Sprint-1 no-device items; plus #40/#41/#42/#43/#44/#47/#51/#52.
- **Reset failed VM host units** verdify-forecast-page + verdify-plan-publish (#59), dead Grafana render-cache-warm timer (#60) — port to k3s (M6.2) or retire. *#186.*
- **Firmware OTA hygiene** (#35 promote last-good after 48h bake; #37 `irrig_center_start_hour` 10→6) under firmware-freeze; not cutover deps.
- **Stale-issue close** (~16 with merged fix-PRs): #38/#46/#59/#60/#40/#43/#44/#42/#41/#47/#34/#31/#32/#33/#21/#19.

**Platform blockers:** **NONE of its own.** Cutover is gated by other lanes — prod ns/App apply (Cluster), device-VLAN `:6053` reachability (Networking, flannel has no VLAN10 leg → Errno 111), `*.verdify.ai` wildcard cert (Networking, L12), and a populated-DB parity run that has never run (Cluster, db-parity has only self-smoked). Single-writer invariant; vault zero-CI + #104.

### 3.3 Observability
**Done:** #58 `/health/detailed` with baked `VERDIFY_GIT_SHA` (smoke dep satisfied); #183 device-route monitor + G10 SPEC authored; `scripts/k3s-smoke.sh` exists with `smoke` + `device-monitor` modes (read-only STUB, correct 0/1/2+ exit semantics); `verdify-grafana` component authored (PVC on iSCSI, renderer, 20 dashboards); all 20 dashboards uniformly TimescaleDB-backed (zero prometheus/loki refs); G2/G3 merged; `alert-monitor.py` working (VM-resident); fleet `monitoring-stack` already partially covers Verdify (blackbox split-horizon probes, Loki@.30.100:3100, postgres-exporter, docker-iris :9100).

**Remaining (implementation):**
- ★ **Wire the `:6053` ESTAB==1 monitor into continuous alerting.** STUB only prints; app image is non-root/read-only-rootfs and may lack `ss` (smoke "skips, not false-pass") → needs a **sidecar/exporter** exporting `verdify_esp32_writers{ns}` + Prometheus scrape + alert on `!=1` + iris-side `ss` check until decommission + independent blackbox TCP probe. Needs a metrics substrate that doesn't exist; cross-gated with Networking #87 (`:6053` firewall allow). IRIS-W013, M5-gating. *#89, #87, #183 §7.1.*
- ★ **Wire G10 smoke as a hard cutover gate** (ArgoCD post-sync hook / CI). Logic coded, not invoked. Needs the device-monitor live for sub-check #4. *#89, #183 §7.2.*
- **Stand up k3s metrics substrate** (Prometheus + kube-state + node-exporter + cAdvisor). Zero exists. Mostly M6 EXCEPT the device-route path which is M5-gating and could ride fleet Prometheus. *#75, #134, #89.*
- **ServiceMonitor/PodMonitor + `/metrics`** for api/mcp/ingestor/dispatcher (only `/health` exists; `/metrics` exporter would be an Application-lane code change). *#75, #134.*
- **Cluster log shipping** (Promtail/Alloy DaemonSet → Loki) replacing VM-docker promtail; retire goaccess. M6. *#88, #75.*
- **Wire `verdify-grafana` into overlays/prod** on `graphs.verdify.ai` (needs iSCSI SC + #87 local plane + tar-copy of Grafana SQLite — a MIGRATE item not in pg_dump/vault). M6. *#88, #130.*
- **Re-home `alert-monitor.py`** into k3s (CronJob/Deployment reading prod DB). M6. *#75.*
- **uptime-kuma / fleet blackbox** for `*.verdify.ai` (likely reuse; `botauth.verdify.ai` is a real 502, separate). *#87/#88.*
- **render-cache-warm timer (#60)** re-enable vs retire (retire likely). 
- **Extend `v_data_pipeline_health` + alert-monitor** to weather_station; annotate intentionally-dark sources. *#42.*
- ★ **Fleet-wide P1: BackupCriticallyOverdue** firing since 2026-05-29 (jvallery/agents#440) — a verified-restorable snapshot is an M6 decommission precondition, so a failing backup pipeline blocks the irreversible M6. *agents#440/#380; verdify #91.*
- ★ **3-plane obs collapse + void Alertmanager** (agents#427 epic, network-infra#153 P0 CUT runbook, agents#417 Alertmanager routes to a void, agents#384 snapshot .30.100 TSDB before shutdown). Gates VM decommission. *verdify #75.*

**Platform blockers:** NO k3s obs substrate exists at all (grep of `deploy/` = zero); the device-route monitor is a STUB and is the single M5-gating obs gap; it can't observe a real connection until Networking #87 delivers the `:6053` allow + nodeSelector-pinned ingestor; VM-resident obs (promtail/goaccess/alert-monitor/Grafana) does not survive decommission; no `/metrics` surface to scrape.

### 3.4 Networking
**Done:** canonical edge VIP settled at **192.168.7.10** (ADR-15/Model B'); all IngressRoutes authored+merged targeting .7.10 (naming `wildcard-verdify-ai-tls`); base NetworkPolicy contract authored; device-egress overlays authored (only ONE overlay grants the device allow); cloudflared tunnel as code (2-replica, fail-closed); MetalLB BGP staged as code (`bgp/metallb-bgp.yaml` + UDM FRR side); DNS/cert matrix specified (#67/#90 CLOSED 2026-06-01); cross-VLAN trust-zone redesign as code (PR #27 MERGED, **artifacts-only/apply-gated**); Iris authoring lane COMPLETE.

**Remaining (implementation):**
- ★ **KEYSTONE — land UDM BGP v2 `.conf`** (graceful-restart + preserve-fw-state + `maximum-paths 5` ECMP) via **GUI-only** upload to fix the apps-pool `192.168.7.0/24` **FIB blackhole**. Today .7.10/.7.21 return `curl 000` off-cluster. Without it, split-horizon, tunnel rewrite, cert validation, and the entire M5.2 edge cutover all target an unreachable VIP. Jason attended GUI action. *network-infra#10; HARD PREREQ for #53; #75.*
- ★ **Stand up live split-horizon** (UDM-local records for all `*.verdify.ai` + `*.k3s.verdify.ai` → .7.10 for all LAN subnets incl VLAN10; verify `dig @UDM`). No Pihole, no live UDM local zone today (TCP 000). verdify/verdify-staging have NO DNS record. Needs #10 first + a resolver path (Pihole HA on .7.53 #145/#146 or UDM static host_record). *verdify#87 (Gates 0a-0e); network-infra#53/#144/#145/#146.*
- ★ **Rewrite cloudflared tunnel origin** .30.34 / dying VM .30.100 → .7.10 and move connector into k3s. **DRIFT:** live IaC forwards every rule to `https://192.168.30.34:443` (NOT .7.10 as the Iris matrix claims), and a THIRD live systemd tunnel `verdify-gateway-cutover` runs on the dying .100 pinning 14 `*.verdify.ai` hosts to the VM being decommissioned. Tunnel name is `vallery-homelab` (not `vallery-edge`). *network-infra#53 item4; #131; #122.*
- ★ **Close the `verdify.ai` DNS-01 cert gap (L12).** Supply a `verdify.ai` Cloudflare Zone:DNS:Edit token (by name), add a `verdify.ai` solver to `letsencrypt-dns01` (today `dnsZones:[vallery.net]` only), then Root applies `wildcard-verdify-ai` (SANs `*.verdify.ai` + apex) + `*.k3s.verdify.ai`. **Conflicting signal:** network-infra#54 carries a `state:DEPLOYED` label but the issue is OPEN/blocked — **needs-live-confirm** the solver actually landed. *matrix §3/L12; gate:jason + needs:james.*
- ★ **Open the durable cross-VLAN UniFi allow** pinned-node → `192.168.10.111:6053` (ESPHome native API, no firmware change), source-locked, plus HA `.30.107:8123/1883` + Frigate `.30.142:5000/1984`. A pre-M5 **connect-only spike** (raw TCP, connect ≠ write, NOT via ingestor) proves reachability + 5–10s latency; flannel has NO device-VLAN leg today (Errno 111). The matching `allow-ingestor-device-egress` NetPol is synced by Root **only at the atomic M5.1**. *verdify#27 (P1 device-safety); #87 M4.3; #89; network-infra#69.*
- ★ **M5.2 WAN edge/DNS cutover** (strictly AFTER M5.1): flip 9 `*.verdify.ai` CNAMEs grey→proxied→`cfargotunnel.com`, prove 200 via tunnel AND LAN (M5.3 location-independence), kill-WAN drill. Today STAGED/NOT cut over: no CF CNAME points at the tunnel, 80/443 port-forwards live, iris VM Traefik still the public terminator. *verdify#90 (CLOSED, Gate29); network-infra#76/#166/#162.*
- ★ **verdify-staging datapath fix:** .7.21 VIP lost off-cluster reachability after pod reschedule / BGP-FIB re-advertise gap (agents#361 P1); AFC PR#13 open (fix VIP datapath + repoint ArgoCD to registry overlay); verdify-dev still **Degraded** (agents#432, planner Init:0/1). Lower envs aren't reliably green to rehearse M5. *agents#361/#432; AFC PR#13.*
- **Retire/scope stopgap VIPs** once .7.10 proven: close .7.21/.7.22 conflict; labelSelector/retire .30.34 ns-traefik (family-safety — an unscoped .30.34 could adopt the unauthenticated `api.verdify.ai` route onto the camera VIP). *network-infra#75/#131/#149.*
- **WAF/orange-cloud posture** at the M5.2 flip + DNS hygiene (grey→orange ~30 hosts). *network-infra#6/#20/#148/#70.*
- **Auth-rehome** Verdify admin → `auth.vallery.net` SSO after .100-Authentik stop; interim .100:443 backend (Authentik .100 is an auth **SPOF** blocking decommission). *verdify#174; network-infra#33/#41/#141; agents#426/#438.*
- **M4 IPv6/IPAM tail + Cilium target ratification** (deferred non-goal). *network-infra BACKLOG#8/#11/#13/#15/#18.*

**Platform blockers:** **#10 apps-pool BGP FIB blackhole is the single keystone** (VIP unreachable off-cluster, GUI-upload only); the `verdify.ai` DNS-01 cert (no zone solver, no token — and a contradictory `state:DEPLOYED` label, needs-live-confirm); the cross-VLAN `:6053` allow is bound to the single-writer invariant (opened only at atomic M5.1); flannel does not enforce NetworkPolicy or give Hubble visibility; **source-of-truth DRIFT** between the Iris matrix (.7.10 / `vallery-edge`) and live network-infra IaC (.30.34 / third tunnel on dying .100 / `vallery-homelab`) — reconcile before cutover or the WAN path lands on a decommissioned origin. `needs:nexus` cross-zone firewall (#42 `:6053`, #43 MQTT/HA) is the dominant device-path blocker and both are `status:blocked`.

---

## 4. Critical path (single ordered sequence to prod-cutover + VM-decommission)

Hard gates marked 🔒 (Jason human-only) and ⚠ (needs-live-confirm / live-vantage required).

1. ⚠ **M0 — Fill `state-truth.md` L1–L15** (laptop-root live reads: pod inventory, ArgoCD sync/health, live staging source, live prod DB SC, storage, SOPS secrets, Proxmox recovery floor). Everything downstream is asserted-on-paper until this runs. *Cluster.*
2. 🔒 **GO/NO-GO on the sanctioned path** (Jason confirms ordering before Root touches the live cluster) + **confirm target cluster** (existing live vs planned VMs 320/321/322). *§5.*
3. **Apply `verdify-dev` App** (no device path) to shake out images/secrets. *Cluster, #86.*
4. **Apply `synology-iscsi-ssd` SC + bind `verdify-db-dumps` NFS static PV** (Root supplies PV). *Cluster, #84/#28/#130.*
5. **Re-point live staging at `verdify-platform overlays/staging`** + **fix the .7.21 datapath / verdify-dev Degraded** (AFC PR#13, agents#361/#432) so a lower env is reliably green to rehearse on. *Cluster + Networking.*
6. **Stand up prod DB STS on iSCSI + copy-NOT-move restore + G1/G2/G3 parity verify** (first real populated-DB parity run, never yet executed). *Cluster, #84/#28/#72.*
7. ★ **Networking keystone block** (can parallel 3–6): 🔒 **#10 UDM BGP GUI-upload** → split-horizon resolves .7.10 → tunnel origin rewrite to .7.10 + retire the `verdify-gateway-cutover` tunnel on .100 → 🔒 **L12 `verdify.ai` CF token** → Root applies `wildcard-verdify-ai` cert. *Networking, #10/#53/#54/#131.*
8. **Apply `verdify-prod-dark` App + run the M4 48h device-dark proof** (read/serve parity data, **ZERO `:6053` ESTAB**). *Cluster, #73/#194.*
9. ★ **Stand up the M5-gating observability before cutover** — the `:6053` ESTAB==1 sidecar/exporter + alert (on fleet or minimal in-cluster Prometheus) and the G10 smoke gate wired to a post-sync hook. The runbook forbids an unobservable 1→0→1 cutover. *Observability, #89/#183.*
10. 🔒 **§3.4 device-VLAN sign-off** + ⚠ **#105 ESP32_API_KEY reconciliation** (live read; wrong key = silent break at M5). *Networking + Application + Jason.*
11. 🔒 **M5 / M5.1 atomic single-writer cutover** — `verdify-prod` App synced to writer shape **simultaneously** with stopping the VM ingestor/setpoint-server; device monitor must read steady **exactly 1**. The whole path converges here. *Cluster, #73/#132.*
12. **M5.2/M5.3 WAN edge cutover** — flip the 9 CNAMEs to the tunnel, prove 200 via tunnel + LAN, kill-WAN drill. *Networking, #90/#76.*
13. 🔒 **Backup precondition + M6 decommission** — resolve the active P1 BackupCriticallyOverdue (agents#440) and verify the VMID306 PBS recovery floor restorable, then **mask + decommission the iris VM** (irreversible), retire residual VM services / .152 / :8300. *Cluster + Observability, #91/#61.*
14. **M6 obs/web tail** — port promtail/Loki/Grafana/alert-monitor into k3s, full Prometheus parity, retire Cloud Run www + Google Sites, 3-plane obs collapse. *Observability + Application + Networking, off-path.*

**Hard gates summary:** M0 live-vantage (⚠), #10 BGP upload (🔒), L12 token (🔒), §3.4 device-VLAN (🔒), M5 atomic cutover (🔒), M6 decommission + backup-restorable (🔒).

---

## 5. Open questions needing Jason decisions

| # | Decision | Options | Recommendation |
|---|----------|---------|----------------|
| J1 | **Sanctioned critical path + ordering** | Confirm M0 reads → repoint staging → dev → prod DB restore → M4 48h → M5 → M6 | **Approve as in §4** before Root touches the live cluster. |
| J2 | **Target cluster** | Existing live `vallery-local-k3s` vs planned VMs 320/321/322 (DNS pending, agents#122) | **Use the existing live cluster** (planned VMs lack DNS); determines where overlays/prod DB + iSCSI SC land. |
| J3 | **Cilium re-CNI before cutover?** | Stay flannel (device-dark/single-writer enforced app-layer + UniFi only) vs schedule Cilium first | **Stay flannel through cutover**; accept `replicas:0`+`DEVICE_WRITE=0`+UniFi as sufficient. Cilium stays a deferred non-goal (ADR-17). |
| J4 | **§3.4 device-VLAN `:6053` allow** | Approve the third VLAN allowance (pinned-node → 192.168.10.111:6053) | **Approve the connect-only spike now; hold the write-path NetPol for the atomic M5.1.** |
| J5 | **M5 single-writer cutover GO + timing** | When do prod writes flip from iris to k3s | **Hard gate** — only after M4 48h proof + #105 reconciled + device monitor live. Schedule an attended window. |
| J6 | **M6 decommission + backup floor** | Confirm VMID306 PBS snapshot recent + restorable; resolve agents#440 first | **Block M6 until agents#440 cleared and a restore is verified.** Irreversible. |
| J7 | **#105 canonical ESP32_API_KEY** | live `.env` 127f85d0 vs esphome `df2784f9` | **Live read both, pick the one the device currently trusts; must NOT re-flash.** Cutover-adjacent. |
| J8 | **DEC-WWW Cloud Run retirement** | Retire Cloud Run www (k3s-only) vs keep Cloud Run as origin + k3s parity | **Retire Cloud Run** at M5 (flip www DNS off `ghs.googlehosted.com`); removes dead Deploy CI. Off device-path. |
| J9 | **Migration 147 reward swap gate** | Hold 147 until ≥90% anchor reproduction + 1 clean dual-write day vs re-calibrate the gate itself (#17 false-premise) | **Re-baseline #17 first** (live ~50.9% suggests the gate may be miscalibrated); do not apply 147 on cutover path. |
| J10 | **Obs REUSE vs DEDICATED** | Reuse fleet `monitoring-stack` (already partly wired) vs Verdify-dedicated in-cluster ns | **Reuse the fleet stack** for the M5-gating device-route alert (lower effort, already probing `*.verdify.ai`); revisit dedicated for full M6 parity. |
| J11 | **Obs CUT + void Alertmanager** | Pick ONE canonical stack (`monitoring` vs `observability` ns), fix Alertmanager (agents#417 routes to void) | **Decide the canonical ns and wire a real receiver** before relying on any k3s alerting; gates VM decommission. |
| J12 | **#10 BGP GUI-upload window** | Attended UniFi Settings→Routing→BGP upload (+ optional .7.20 ghost-prefix clear) | **Schedule the attended AM window** — everything edge-side waits on it. |
| J13 | **L12 `verdify.ai` CF token** | Jason + James scope a `verdify.ai` Zone:DNS:Edit token (by name) | **Scope+hand the token** so Root can extend the DNS-01 solver and issue the wildcard cert. |
| J14 | **WAF posture at M5.2** | WAF/managed rules on the 9 grey→orange `*.verdify.ai` hosts | **Enable WAF managed rules** (`api.verdify.ai` has strip-identity but no edge forward-auth); specify the rule set. |
| J15 | **Internal resolver for split-horizon** | Pihole HA on .7.53 (#145/#146) vs UDM static host_records | **UDM static host_records** as the minimal path for cutover; Pihole HA can follow. |
| J16 | **Repo consolidation** | Archive `verdify-planner` (folded #102), archive/keep site-legacy + secondary repos (agents#306), confirm out-of-scope strays | **Archive `verdify-planner` non-authoritative** (close site-legacy Dependabot #1/#3 without merge); see §6. |
| J17 | **Residual VM services at M6** | Port (#59 forecast/plan-publish, #60 cache-warm, umami/goaccess/hermes-iris) vs retire | **Retire goaccess + render-cache-warm; port forecast/plan-publish + umami into k3s** (M6.2 manifest scope). |
| J18 | **WAN port-forward cutover scope** | Disable UniFi 80/443 + tunnel-primary before or after the Verdify local-plane cutover | **After** — Verdify rides the local plane first (M5.1/M5.2), broad WAN cut is a separate blast-radius step. |
| J19 | **planner_graph schema ownership** | Leave self-creating at runtime vs fold into `db/migrations` ledger | **Fold into the ledger** so DB parity dim-1 reports clean (hygiene, not blocking). |
| J20 | **botauth.verdify.ai (.152:8788) + verdify-api.service :8300** | Recover vs retire (confirm no consumers) | **Retire both** (.152 slated for shutdown #441); confirm no consumers. |

---

## 6. Repos & consolidation

| Repo | Lane | Default | Status | Consolidation decision |
|------|------|---------|--------|------------------------|
| **VerdifyConsultancy/verdify-platform** | Application (+ overlays/argocd seed Cluster/Networking/Obs) | `main` (live `live/platform-main`) | Canonical monorepo; CI green (CI/Container Publish/K8s Manifests/Promote Diff Guard `success`; k8s-manifests repaired #126); planner_graph folded #102; all 8 images @sha; G2/G3 merged | **Keep — single source of truth.** |
| **VerdifyConsultancy/verdify-www** | Application (Networking tail for DNS) | `main` | Astro site; CI+Publish Image green; image 1/1 Running dev+staging; **dead Cloud Run Deploy job reds every run** | **Keep as build repo; remove the Cloud Run Deploy workflow** (DEC-WWW J8). |
| **VerdifyConsultancy/verdify-site-legacy** | Application | `v4` | Quartz lab engine → `verdify-lab` image (Publish Lab Image green); issues disabled; Dependabot PR#1/#3 open | **Keep `@v4` as the lab build engine; close Dependabot #1/#3 without merge** (no longer the deploy surface). Confirm vs agents#306 delete. |
| **VerdifyConsultancy/verdify-vault** | Application (content) | — | Lab content (Obsidian/Syncthing/NFS); **ZERO CI**; **64 uncommitted generated pages on the VM (#104)** | **Keep; commit working tree before M6 (★critical, #104); add a `repository_dispatch` to trigger lab rebuild (M2.2).** |
| **VerdifyConsultancy/verdify-planner** | Application | — | Python CI green; **redundant after #102 fold** | **Archive / mark non-authoritative** (J16) to prevent future divergence; optional eval sandbox. |
| **VerdifyConsultancy/verdify-agent-context** | Application (docs) | — | Runbooks/handoffs, not deployed; issue #2 open (org admin cleanup) | **Keep as docs; finish org cleanup #2.** |
| **jvallery/network-infra** | Networking | `main` | Actively pushed; 12+ open issues (#10/#53/#54/#75/#131/#150/#151/#153…); PR #27 merged but **apply-gated**; **BGP is live-config-only, not git-recoverable (#150)** | **Keep — Networking + edge SoT; capture UDM/FRR BGP as code (#150).** |
| **jvallery/monitoring-stack** | Observability | `main` | Full Prometheus/Grafana/Loki/Alertmanager/blackbox/uptime-kuma; already split-horizon-probes `*.verdify.ai`; no open issues | **Keep — candidate canonical obs (J10/J11); decide vs network-infra-staged stack (#153 P0).** |
| **jvallery/agent-fleet-control** | Cluster (GitOps SoT) | — | **The live ArgoCD SoT**; hosts ONLY a stale self-sourced `verdify-staging` raw-manifest set (old `jvallery/*:staging`, TS 2.17.2, suspended migrate); AFC PR#13 open | **Keep — but commit the platform App-CRs/AppProject here (#86) and repoint staging to verdify-platform overlays; this closing is the #1 cluster action.** |
| **jvallery/proxmox-infrastructure** | Cluster (substrate inventory) | — | Inventory only (`k3s-vms.yml`, VMID306 `migrate_later`) | **Keep — Proxmox/VM disposition SoT.** |
| **jvallery/agents** | Adjacent fleet (not Verdify-cutover) | — | Carries Verdify migration phase tickets (#300–#307, #389/#409/#422/#440…) | **Keep — cross-fleet tracking; not a Verdify repo.** |
| onyx-playground, backstage, sunshine_club, verdify-gravity, jvallery/gravity, cortex-ai-compute | — | — | Recent pushes but **no migration role** | **Confirm out-of-scope; archive/ignore (J16).** |
| k3s-cluster, infra-home, orchestrator | — | — | Already archived/legacy | **Stay archived.** |

---

## 7. Work re-allocation matrix (every remaining item → owning swim lane)

★ = critical-path (blocks prod cutover or VM decommission). 🔒 = Jason gate. ⚠ = needs-live-confirm.

| Item | Lane | ★/nice | Refs |
|------|------|--------|------|
| Fill `state-truth.md` L1–L15 (live reads) ⚠ | Cluster | ★ | #111, #135 |
| Apply 3 ArgoCD App CRs (dev first) | Cluster | ★ | #86, #111 |
| Re-point live staging → verdify-platform overlays/staging ⚠ | Cluster | ★ | L3, overlays/staging |
| Apply iSCSI SC + bind NFS dumps PV ⚠ | Cluster | ★ | #84, #28, #130 |
| Stand up prod DB STS + copy-NOT-move restore + parity verify | Cluster | ★ | #84, #28, #72 |
| Run M4 48h prod-dark proof (zero `:6053` ESTAB) ⚠ | Cluster | ★ | #73, #194 |
| Apply `verdify-prod` App = M5 writer shape 🔒 | Cluster | ★ | #73, #86, #132 |
| Recreate prod verdify-db on iSCSI ≥2.25.2 (Retain) | Cluster | ★ | #84, agents#389/#300 |
| Timescale physical-replica promote (compute-first/data-last) | Cluster | ★ | #28, agents#302 |
| Secrets-out-of-`.env` → in-cluster store | Cluster | ★ | #30 |
| State landing zone PVC + Root backup dest | Cluster | ★ | #72, #130 |
| M6 mask/decommission iris VM (+ .152, :8300) 🔒 irreversible | Cluster | ★ | #91, #61, agents#441 |
| Author AppProject `app-test` + app-of-apps root | Cluster | nice | #86 |
| Reconcile dev targeting vs planned VMs 320/321/322 | Cluster | nice | agents#25/#122 |
| Resolve flannel-vs-Cilium NetPol enforcement (ADR-17) | Cluster | ★(contract) | #71, #27 |
| GPU node + NVIDIA device-plugin; node rebalance/drain | Cluster | nice | agents#447/#446/#448/#358/#387 |
| Frigate/HAOS/immich whole-VM lifts (shared substrate) | Cluster | nice | agents#442/#443/#444 |
| #105 reconcile canonical ESP32_API_KEY (no re-flash) 🔒⚠ | Application | ★ | #105, #30 |
| Commit verdify-vault 64 uncommitted pages before M6 🔒 | Application | ★ | #104 |
| verdify-planner → k3s per-env (off Cloud Run, fold close-out) | Application | ★ | #117/#102 |
| EPIC three-env + MQTT fan-out bus | Application | ★ | #111/#112/#113/#114/#115 |
| Model setpoint-server as 2nd writer; 3-way single-writer interlock | Application | ★ | #71/#89/#118 |
| Migration 147 reward swap (compliance_v2) | Application | nice | #13/#17/#18/#19/#20/#21 |
| Retire Cloud Run www + remove dead Deploy CI 🔒(DEC-WWW) | Application | nice | #116/#88/#120 |
| Close #116/#117/#124/#127/#128 (image halves done) | Application | nice | those |
| Wire vault → lab rebuild (repository_dispatch) | Application | nice | #124/#104 |
| Fold planner_graph DDL into ledger; activate schema_migrations | Application | nice | #72/#117/#102 |
| Digital-twin track (#31 P0 gate) | Application | nice | #14/#31/#32/#33/#34 |
| Track-A agronomy/data-integrity (#38/#49 Sprint-1) | Application | nice | #38/#40/#41/#42/#43/#44/#47/#49/#51/#52 |
| Reset failed VM host units #59; port-or-retire #60 | Application | nice | #59/#60/#186 |
| Firmware OTA hygiene #35/#37 (freeze-gated) | Application | nice | #35/#37 |
| Bulk-close ~16 stale issues (merged fix-PRs) | Application | nice | #38/#46/#59/#60/#40/#43/#44/#42/#41/#47/#34/#31/#32/#33/#21/#19 |
| botauth/.152:8788 + :8300 recover-or-retire 🔒 | Application | nice | #175/#61/#441 |
| Org admin cleanup + monorepo consolidation/deletes 🔒 | Application | nice | agent-context#2, agents#306 |
| Charter/epic umbrellas (keep as indices) | Application | nice | #134/#135 |
| Wire `:6053` ESTAB==1 monitor sidecar+exporter+alert | Observability | ★ | #89/#87/#183 |
| Wire G10 smoke as hard cutover gate (post-sync hook) | Observability | ★ | #89/#183 |
| 3-plane obs collapse + fix void Alertmanager + snapshot .100 TSDB | Observability | ★ | agents#427/#417/#384; network-infra#153 |
| Resolve P1 BackupCriticallyOverdue (M6 precondition) | Observability | ★ | agents#440/#380; #91 |
| Stand up k3s metrics substrate (Prom/kube-state/node/cAdvisor) | Observability | nice(M6) | #75/#134 |
| ServiceMonitor/PodMonitor + `/metrics` surface | Observability | nice | #75/#134 |
| Cluster log shipping → Loki; retire goaccess | Observability | nice(M6) | #88/#75 |
| Wire `verdify-grafana` to prod (graphs.verdify.ai, SQLite tar-copy) | Observability | nice(M6) | #88/#130 |
| Re-home `alert-monitor.py` into k3s | Observability | nice(M6) | #75 |
| uptime-kuma / fleet blackbox for `*.verdify.ai` | Observability | nice | network-infra#152; #87/#88 |
| render-cache-warm re-enable vs retire 🔒 | Observability | nice | #60 |
| Extend pipeline-health to weather_station; annotate dark sources | Observability | nice | #42 |
| ★ #10 UDM BGP v2 GUI-upload (FIB blackhole) 🔒 KEYSTONE | Networking | ★ | network-infra#10 |
| Live split-horizon (UDM-local records → .7.10) | Networking | ★ | verdify#87; network-infra#53/#144/#145/#146 |
| Rewrite tunnel origin → .7.10; retire .100 tunnel; into k3s | Networking | ★ | network-infra#53/#131/#122 |
| Close `verdify.ai` DNS-01 cert gap (token + solver + wildcard cert) 🔒⚠ | Networking | ★ | matrix L12; network-infra#54 |
| Durable cross-VLAN `:6053` allow + connect-only spike 🔒 | Networking | ★ | verdify#27/#87/#89; network-infra#69/#42 |
| M5.2/M5.3 WAN edge/DNS cutover + kill-WAN drill | Networking | ★ | verdify#90; network-infra#76/#166/#162 |
| verdify-staging .7.21 datapath fix + verdify-dev Degraded | Networking | ★ | agents#361/#432; AFC PR#13 |
| Auth-rehome → auth.vallery.net (Authentik .100 SPOF) | Networking | ★ | verdify#174; network-infra#33/#41; agents#426/#438 |
| Pi-hole HA + split-horizon resolver flip 🔒 | Networking | ★(resolver) | network-infra#144/#145/#146/#24/#74 |
| MQTT/HA cross-zone firewall allow (`:1883`/`:8123`) | Networking | ★ | network-infra#43 |
| Retire/scope .7.21/.7.22 + .30.34 ns-traefik (family-safety) | Networking | nice | network-infra#75/#131/#149 |
| WAF/orange-cloud posture + DNS hygiene 🔒 | Networking | nice | network-infra#6/#20/#148/#70 |
| Capture UDM/FRR BGP as code (DR risk) | Networking | nice | network-infra#150 |
| traefik-apps allowCrossNamespace into durable Helm + :80→:443 | Networking | nice | network-infra#181/#149 |
| www/lab prod DNS cutover + Google Sites decommission 🔒 | Networking | nice | network-infra#151; #124 |
| M4 IPv6/IPAM tail; ratify Cilium target (deferred) 🔒 | Networking | nice | network-infra BACKLOG#8/#11/#13/#15/#18 |
| WAN port-forward cutover + tunnel-primary 🔒 | Networking | nice | network-infra#4/#34/#122/#136 |

**Bottom line for the new lanes:** the Application authoring lane is essentially complete; the long pole to cutover/decommission is now **Networking** (the #10 BGP keystone, L12 cert, split-horizon, source-of-truth drift, cross-VLAN `:6053`) and **Observability** (no k3s substrate, stub device-monitor, void Alertmanager, active P1 backup incident), with **Cluster** bottlenecked on laptop-root applies + the empty `state-truth.md` and the hard Jason cutover gates. Honor migrate-as-is, single-writer-at-M5.1, and #177-deferred throughout.
