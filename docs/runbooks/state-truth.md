# state-truth.md — the L1–L15 live-vantage landing zone

**Issues:** #111, #135 (M0.1 live-cluster reads) — requested-by: iris
**Status:** TEMPLATE / EMPTY — awaiting live reads. No row below has been run yet.
**Author:** Iris (template only — I hold NO cluster, device, or DNS access; Root/Nexus/Jason fill the live results).

## Purpose

This is the single landing zone for the **15 facts the migration cannot proceed
without confirming live** (the "needs a live vantage" inventory from
`docs/verdify-state-and-cutover-sprint-2026-06-03.md`). Each L-item below has:

- **What to run** — the exact read-only command / observation.
- **Owner** — the lane accountable for running it.
- **Result + timestamp** — an empty slot to paste the live output into, with the
  date/time it was captured.

**This is a READ-ONLY landing zone.** Every command here observes; none scales,
patches, applies, syncs, restores, flips DNS, or touches the live VM / ESP32.
Filling a slot must never violate the **single-writer invariant** (exactly ONE
:6053 writer to `192.168.10.111` at all times — the VM today; prod k3s only at
the Jason-gated M5.1) and must honor **migrate-as-is** (no firmware change). The
L4 / L8 / L15 probes are connect/observe-only and must not push a setpoint.

## Owner legend

| Lane | Items |
|---|---|
| **Root** (laptop-root: kubectl / ArgoCD / SOPS / storage / Proxmox) | L1, L2, L3, L4, L5, L6, L7, L9, L12 |
| **Jason** (VM owner + secrets/GCP gate) | L8 (+ firmware op), L13 (+ James) |
| **Nexus** (edge: Cloudflare / UniFi / MetalLB / cert / tunnel) | L10 (+ Root for kubectl), L14 |
| **Mixed** | L11 (Nexus / Root), L13 (Jason / James), L15 (Jason / Root) |

> Owner-of-record per item is the canonical inventory in
> `docs/verdify-state-and-cutover-sprint-2026-06-03.md` (§"Needs a live vantage").
> The lane shorthand in `docs/handover/verdify-migration-lanes-2026-06-04.md`
> ("Root's L1-L7/L15, Jason's L8, Nexus's L10-L14") is the milestone grouping;
> where they differ, the per-item owner column below governs.

## How to fill a row

1. Run the **What to run** command from the live vantage on the owning lane.
2. Paste the raw output (trimmed to the load-bearing lines) into **Result**.
3. Stamp **Timestamp** with the capture time (`date -u`, e.g. `2026-06-04T18:22Z`).
4. If the result diverges from the expected/`[needs-LIVE-confirm]` claim in the
   inventory doc, note the divergence and the issue (#111/#135) it updates.

---

## L1 — staging/dev pod inventory

- **What to run:** `kubectl get pods -n verdify-staging` and `kubectl get pods -n verdify-dev` — record actual Running / CrashLoopBackOff / ImagePullBackOff / 0-0 counts per workload.
- **Owner:** Root
- **Expected (to confirm or correct):** staging app pods Running; dev ingestor `0/0`.
- **Result:**
  ```
  [ ]
  ```
- **Timestamp:** `[ ]`

## L2 — ArgoCD app sync/health + revision

- **What to run:** `argocd app get verdify-local-staging` and `argocd app get verdify-dev` — record Synced/Healthy state and last reconciled revision (did it land on `ff2a4565` or its successor digest-bump rev?).
- **Owner:** Root
- **Expected:** both Synced + Healthy on the latest digest-bump revision.
- **Result:**
  ```
  [ ]
  ```
- **Timestamp:** `[ ]`

## L3 — staging source-of-truth path

- **What to run:** Confirm `verdify-local-staging` actually sources `verdify-platform/overlays/staging` (the cutover source) and NOT the retired `agent-fleet-control` source. Check `argocd app get ... -o yaml | grep -A3 source` / the App spec `repoURL`+`path`.
- **Owner:** Root
- **Expected:** `repoURL` = verdify-platform, `path` = `overlays/staging`.
- **Result:**
  ```
  [ ]
  ```
- **Timestamp:** `[ ]`

## L4 — dev ingestor is device-dark (single-writer guard)

- **What to run:** Confirm dev ingestor `spec.replicas == 0` AND it holds NO ESTABLISHED connection to `192.168.10.111:6053`. `kubectl get deploy -n verdify-dev <ingestor> -o jsonpath='{.spec.replicas}'`; read-only socket inspect (`ss`/`netstat` listing existing sockets only — opens nothing) on any Running dev pod.
- **Owner:** Root
- **Single-writer note:** observe-only; this verifies the absence of a writer, never creates one.
- **Expected:** replicas `0`; zero `:6053` ESTAB.
- **Result:**
  ```
  [ ]
  ```
- **Timestamp:** `[ ]`

## L5 — staging verdify-db empty + placed on iSCSI

- **What to run:** Confirm staging `verdify-db` is truly empty (`climate` `count(*) = 0`) and the pod is `Running 1/1` on `synology-iscsi-ssd` on a **worker** node. `kubectl exec -n verdify-staging <db-pod> -- psql -c 'select count(*) from climate;'`; `kubectl get pod -n verdify-staging <db-pod> -o wide` for node placement.
- **Owner:** Root
- **Expected:** `count = 0`; `Running 1/1`; on a worker, on `synology-iscsi-ssd`.
- **Result:**
  ```
  [ ]
  ```
- **Timestamp:** `[ ]`

## L6 — storage substrate: SC + dumps PVC + nightly backup

- **What to run:** Confirm `synology-iscsi-ssd` StorageClass exists (on /volume1) and the NFS PVC `verdify-db-dumps` exists and is Bound; confirm a nightly backup CronJob is producing fresh dumps (a dump observed `<26h` old). `kubectl get sc synology-iscsi-ssd`; `kubectl get pvc -A | grep verdify-db-dumps`; `kubectl get cronjob -A` + the most-recent dump mtime.
- **Owner:** Root
- **Expected:** SC present; PVC Bound; CronJob present with a `<26h`-old dump.
- **Result:**
  ```
  [ ]
  ```
- **Timestamp:** `[ ]`

## L7 — SOPS secrets present per namespace

- **What to run:** Confirm the SOPS-managed secrets exist in each ns: `verdify-app-secrets`, `verdify-ha-token`, `verdify-hermes`, `ghcr-jvallery-readonly`. `kubectl get secret -n verdify-staging` (and `-n verdify-dev`) and check each by name (do NOT print secret values).
- **Owner:** Root
- **Expected:** all four secrets present per ns.
- **Result:**
  ```
  [ ]
  ```
- **Timestamp:** `[ ]`

## L8 — live VM (.150): writer, ESP32, ext version

- **What to run:** On the VM `192.168.x.150`: `docker ps` + `systemctl status` (which Verdify processes are up); confirm `verdify-setpoint-server` (:8200) is the **ONLY** process holding ESP32 `192.168.10.111:6053` (sole-writer); probe `.111:6053` answering + note firmware version; `psql ... \dx` to record the **live TimescaleDB extension version** (the G1 2.17-vs-2.25 decision input).
- **Owner:** Jason (+ firmware op can assist the device probe)
- **Single-writer / migrate-as-is note:** observe + connect-probe only; NO setpoint write, NO OTA, NO re-flash.
- **Expected:** setpoint-server is sole live writer; `.111:6053` answers; ext version recorded (corrects the stale `2.17.2` claim if it reads otherwise; restore-job already pins `2.25.2-pg16` at `86b4ab3`).
- **Result:**
  ```
  [ ]
  ```
- **Timestamp:** `[ ]`

## L9 — Proxmox VMID 306 placement + recovery floor

- **What to run:** Confirm Proxmox VMID 306 placement is still current and record PBS/snapshot currency as the rollback floor. `qm config 306` / Proxmox node placement; most-recent PBS snapshot timestamp for 306.
- **Owner:** Root
- **Expected:** VMID 306 placement current; a recent verified-restorable PBS snapshot exists.
- **Result:**
  ```
  [ ]
  ```
- **Timestamp:** `[ ]`

## L10 — Cloudflare zone + tunnel state

- **What to run:** Confirm live CF zone state (since 2026-05-29); the real cloudflared **tunnel UUID**; `cloudflared` pods `Running` in ns `cloudflared`; whether any UDM split-horizon DNS has landed. `kubectl get pods -n cloudflared` (Root pairs for kubectl); CF dashboard/API for zone + tunnel UUID.
- **Owner:** Nexus (+ Root for kubectl)
- **Expected:** tunnel UUID recorded; cloudflared pod state known; split-horizon presence/absence noted.
- **Result:**
  ```
  [ ]
  ```
- **Timestamp:** `[ ]`

## L11 — canonical apps-VIP reconciliation

- **What to run:** Reconcile the apps-VIP naming/value: `.7.10` vs `.7.2` (and the `.30.34` / `.7.21` candidates from VIP-resolve) and confirm whether MetalLB/BGP now answers from the edge. `kubectl get svc -A | grep LoadBalancer` for the assigned VIP; TCP to chosen VIP:443 from VLAN30 AND VLAN10.
- **Owner:** Nexus / Root
- **Expected:** ONE canonical edge VIP that IngressRoutes + split-horizon + tunnel all agree on; VIP:443 answers from both VLANs.
- **Result:**
  ```
  [ ]
  ```
- **Timestamp:** `[ ]`

## L12 — cert-manager verdify.ai DNS-01 solver gap

- **What to run:** Confirm whether the cert-manager ClusterIssuer gained a `verdify.ai` DNS-01 solver. Today it is `dnsZones:[vallery.net]` only → no `*.verdify.ai` wildcard cert can issue. `kubectl get clusterissuer -o yaml | grep -A5 dnsZones`; `kubectl get certificate -A | grep verdify-ai`.
- **Owner:** Root (Nexus supplies the verdify.ai DNS-01 token; Root applies the CRs)
- **Expected:** record whether the solver now includes a `verdify.ai` zone selector and whether `wildcard-verdify-ai-tls` is Issued (gap until L12 token lands).
- **Result:**
  ```
  [ ]
  ```
- **Timestamp:** `[ ]`

## L13 — verdify-www GHCR image + Cloud Run state

- **What to run:** Confirm whether the `verdify-www` GHCR image was ever pushed (overlays claim pullable 2026-05-31 but the package 404s now) and whether www is still live on Cloud Run. `gh api /user/packages/container/verdify-www/versions` (or the org path); GCP Cloud Run service list for the www service.
- **Owner:** Jason / James
- **Expected:** GHCR push state + the `5e00bc20` pin status recorded; Cloud Run www presence recorded (feeds DEC-WWW).
- **Result:**
  ```
  [ ]
  ```
- **Timestamp:** `[ ]`

## L14 — locate Nexus's sites-backend route

- **What to run:** Locate the repo + branch hosting Nexus's `routes/25-verdify-sites-backend.yaml` (confirmed NOT in verdify-platform or network-infra). Search the candidate repos / Nexus's edge config tree.
- **Owner:** Nexus
- **Expected:** repo + branch + path identified, so the route is config-as-code and not orphaned.
- **Result:**
  ```
  [ ]
  ```
- **Timestamp:** `[ ]`

## L15 — api.verdify.ai resolution (k3s vs VM/404)

- **What to run:** Confirm whether `api.verdify.ai` resolves to a live k3s pod (HTTP `200`) vs the VM / a `404`. `dig api.verdify.ai`; `curl -sS -o /dev/null -w '%{http_code}' https://api.verdify.ai/health` (and `--resolve` to the candidate VIP for the LAN view).
- **Owner:** Jason / Root (mixed)
- **Single-writer / migrate-as-is note:** GET-only health probe; no write path exercised.
- **Expected:** record `200`-from-k3s vs VM/`404`; this is the public-surface readiness fact.
- **Result:**
  ```
  [ ]
  ```
- **Timestamp:** `[ ]`

---

## Sign-off

Once all 15 rows carry a live Result + Timestamp, paste the same evidence into
the relevant issue threads (#111 / #135) per the M0.1 / M0.2 / M0.3 DoD, and the
live-vantage gaps are closed for the cutover sprint.

| Milestone | Items it closes | Owner |
|---|---|---|
| M0.1 (#111/#135) | L1–L7, L15 | Root |
| M0.2 | L8 | Jason (+ firmware op) |
| M0.3 | L10–L12, L14 | Nexus (+ Root for kubectl) |

> L9 (Proxmox recovery floor) is a Root read folded alongside M0.1.
> L11/L12 also feed Nexus's VIP-resolve + cert-issuer work (M0.3).
