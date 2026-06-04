# Verdify VM-to-k3s Migration — Authoritative Lane Integration

Single source for the 4-lane cutover sprint. Invariants honored throughout: **migrate-as-is** (no firmware change, native-API dispatcher→ESP32 unchanged), **single-writer** (exactly ONE :6053 writer to 192.168.10.111 at all times; prod becomes writer ONLY at M5.1 under Jason GO with the VM writer stopped in the same atomic window), and **#177 / M7 DEFERRED** (Iris authors DESIGN only; no code touches the device this sprint). Repo facts verified: G1 already landed at commit `86b4ab3` (restore-job pg client `2.25.2-pg16` on lines 72/115/151); planner/setpoint/api/mcp/ingestor/migrate image jobs exist in `container-publish.yml`; **no www/lab image jobs exist yet** and prod overlay carries `sha256:0000` placeholders for both.

---

## 1. RACI MATRIX — every item exactly once

Legend: **A** = Accountable/Owner-executor (the lane that does the work). Iris is **R** (authors the artifact) for every item Root/Nexus execute; Jason is **C/Approver** on every gated item. Each item is listed under its single owning lane.

| Item | Title (short) | Issue #s | Owner (A) | Gate | Authors/Routes (R/C) |
|---|---|---|---|---|---|
| **M0.1** | Live-cluster reads L1-L7+L15 → state-truth.md | #111, #135 | **Root** | none (read-only) | Iris (template) |
| **M0.2** | Live-VM reads on .150 (L8): ext version, sole-writer, ESP32 probe | — | **Jason** | none | Iris (template); firmware op assist |
| **M0.3** | Live edge confirm L10-L12, L14 (CF/tunnel/VIP/cert gap) | — | **Nexus** | none (read-only) | pairs Root for kubectl |
| **M1.1** | Build+CI **verdify-www** image (← James) | #116 | **Iris** | DEC-WWW (Jason); admin:packages relink → Jason | Root L13 confirm |
| **M1.2** | Build+CI **verdify-lab** image; fix #124 orphan → verdify-site-legacy | #124 | **Iris** | needs Nexus IngressRoute later | — |
| **M1.3** | Keep planner (#127)+setpoint (#128) digests real, off `sha256:0000` | #127, #128 | **Iris** | artifact-only | — |
| **M2.1** | www+lab rebuild-on-content CI; vault→lab trigger | #88, #124, #116 | **Iris** | none | dep M1.1/M1.2 |
| **M3.1** | G1 verify-only (restore-job == 2.25.2-pg16) — **landed `86b4ab3`** | — | **Iris** | none (promote-diff-guard green) | — |
| **M3.2-G2** | G2: 15 plain→hypertables (19 total) | — | **Iris** | serialized migration, **Jason coordinator approve** | Jason approves sequence |
| **M3.2-G3** | G3: recreate compression+retention bg jobs | — | **Iris** | serialized migration, **Jason approve** | Jason |
| **M3.3** | Prod substrate: synology-iscsi-ssd SC + POSTGRES_PASSWORD SOPS + verdify-db-dumps NFS PVC + PROVEN nightly backup CronJob | #84, #28, #130 | **Root** | single-writer (read/backup only); Jason owns password material | Iris (SC/PVC/CronJob manifests) |
| **M3.4-G4** | G4: add matview/CAGG REFRESH into restore-job.yaml post_restore | #72 family | **Iris** | PR to db/ (restore-job human-gated) | — |
| **M3.4** | Stand up populated STAGING DB via by-hand gated restore (NOT ArgoCD) | #72 | **Root** | data-safety (read-only on VM SoT) | Iris (restore runbook) |
| **M3.5** | First REAL iris-vs-staging parity, frozen target | — | **Root** (runs) | read-only | **Iris authors** scripts/db-parity.sh + runbook |
| **M4.1** | Create verdify-prod ns + AppProject + apply prod App MANUAL-SYNC, ingestor device-dark | #86, #115 | **Root** (executes) | **JASON GO** to create prod ns | **Iris authors** overlays/prod + App CR (M4.1-prod-overlay) |
| **M4.2** | Populate prod DB via gated restore + prod parity exit 0 | — | **Root** | data-safety + single-writer | Iris (runbook+parity) |
| **M4.3** | Device-VLAN :6053 reachability SPIKE — decide route mechanism | **#27**, **#87** | **Root + Nexus** (co-owned) | **JASON sign-off**; single-writer (probe only) | Iris stages nodeSelector pin + greenhouses repoint (un-applied) |
| **M4.4** | M4 device-DARK 48h proof; VM still sole writer | #89 | **Root** (observes) | single-writer | **Iris authors** pass/fail criteria + #89 ESTAB monitor spec; **Jason signs** |
| **M5.1** | EXECUTE atomic single-writer cutover | #73, #132 | **Root** (executes) | **HARD JASON GO** | **Iris authors** #132 runbook; **Jason GO** |
| **M5.2** | Edge/DNS cutover: 9 CNAMEs → cfargotunnel.com, cloudflared live, LAN+WAN 200 | #87, #174, #175 | **Nexus** | **JASON GO**, AFTER M5.1 | Iris (IngressRoutes); Root (cert CR apply) |
| **M5.4** (+M5.3) | Final iris-vs-prod parity at watermark + location-independence proof | — | **Root** (runs); **Jason signs** | single-writer (read-only) | **Iris authors** parity + location-indep tests; Nexus M5.2 for WAN leg |
| **M6.1** | Preserve-before-wipe off-box capture | #130, #104, #105 | **Root + Jason** (co-execute) | **JASON** (VM owner) | Iris (#130 classified inventory) |
| **M6.2** | Apply residual VM-only service migrations (grafana/umami/goaccess/hermes-iris) | #88, #91 | **Root** (executes) | single-writer (non-device) | **Iris authors** manifests/overlays |
| **M6.3** | Soak + PBS snapshot + power off VMID 306 | #91, #61 | **Root** (executes); **Jason GO** | **HARD JASON GO** (irreversible) | — |

**Lane-internal Iris/Nexus authoring items** (the "R" rows above, owned and accountable within their lane, not double-counted against the executor row):

| Item | Title | Issue #s | Owner | Notes |
|---|---|---|---|---|
| M-dev-overlay | Author overlays/dev (own DB, SHADOW ingestor, 3-layer device-dark interlock) | #115 | **Iris** | Root applies under M0.1/normal sync |
| M4.1-prod-overlay | Author overlays/prod render-green incl setpoint(#118)/mqtt(#113)/hermes(#119)/MQTT-subscribe(#114) | #86, #118, #113, #119, #114, #112 | **Iris** | feeds M4.1 (Root executes) |
| M5-runbook | DRAFT #132 cutover runbook + M4-proof criteria + #89 smoke/ESTAB monitor spec | #132, #89 | **Iris** | feeds M4.4/M5.1 |
| M6-state-zone | #130 state classification + PVC + restore method | #130, #88 | **Iris** | feeds M6.1/M6.2 |
| M7-design | #177 device-write API surface DESIGN + auth ADR (DESIGN ONLY) | **#177** | **Iris** | **DEFERRED** — no device code |
| #87 split-horizon | *.verdify.ai → canonical VIP, LAN-only | #87 | **Nexus** | feeds M5.2 |
| VIP-resolve | Reconcile .7.10 / .30.34 / .7.21 / .7.2 → ONE canonical edge VIP | — | **Nexus** | feeds #87, M5.2, IngressRoutes |
| L12 cert-issuer | verdify.ai DNS-01 zone token → cert-manager wildcard | — | **Nexus** (supplies token) | Root applies CRs; Jason owns token |
| GATE-105 | Canonical ESP32_API_KEY declaration before seal | #105 | **Jason** | no re-flash |
| GATE-104 | Authorize verdify-vault working-tree commit | #104 | **Jason** | pre-decommission data-loss gate |
| DEC-WWW | GCP/spend: www+lab k3s-vs-Cloud-Run + Cloud Run retirement (← James) | #116, #124 | **Jason** | L13 input from Iris/Root |

### Conflict resolution — items flagged and resolved

1. **M4.3 (#27 + #87) — DOUBLE-CLAIMED.** Root's draft owns "the cluster-side leg"; Nexus's draft owns "the UniFi cross-VLAN route half." **Resolution:** M4.3 is a single **co-owned spike with split sub-ownership** — Nexus owns the UniFi inter-VLAN route + firewall allow + VLAN10 source-lock; Root owns the in-cluster probe pod / nodeSelector pin and documents the chosen mechanism. **#27 = the route enabler (shared Root+Nexus); #87 has two halves — the DNS half is Nexus-only (M5.2/split-horizon), the device-route co-ownership is the #27 overlap.** Both appear under ONE M4.3 row. No unassigned residue.

2. **#87 — appears in two lanes (Nexus split-horizon DNS vs the M4.3 device-route enabler).** **Resolution:** #87 is genuinely a two-part issue. The **split-horizon DNS part** is Nexus-sole (listed as Nexus item "#87 split-horizon"). The **device-route-enabler part** rides on M4.3/#27. Tagged distinctly; not double-counted.

3. **db-parity.sh (M3.5/M4.2/M5.4) — authoring vs running.** Root "runs," Iris "authors/maintains." **Resolution:** Iris is Accountable for the script artifact; Root is Accountable for each parity *run*. Clean R/A split, no double-assignment.

4. **Restore execution vs runbook authoring (M3.4/M4.2).** **Resolution:** Iris authors the runbook + G4 REFRESH step (M3.4-G4); Root executes the by-hand restore (M3.4/M4.2). Distinct rows.

5. **L12 cert-issuer — Nexus vs Root.** **Resolution:** Nexus supplies the scoped verdify.ai DNS-01 token (owns the supply); Root applies the cert-manager ClusterIssuer/Certificate CRs Iris authors; Jason owns the token material. Three-way, no overlap — Nexus-owned with Root/Jason as named dependencies.

6. **No item is unassigned.** Every M0.1…M6.3, both enablers (#27, #87), all of G2/G3/G4 (= M3.2-G2, M3.2-G3, M3.4-G4 — note G1/M3.1 already landed), and www/lab images (M1.1/M1.2) are present exactly once under one owning lane.

### James reassignment — CONFIRMED

James is **removed**. His two former scopes are reassigned and verified in the matrix:
- **www/lab image build + platform CI → IRIS** (M1.1 verdify-www, M1.2 verdify-lab + #124 orphan, M2.1 rebuild CI). Repo-verified: no www/lab jobs exist in `container-publish.yml` yet; prod overlay still carries `sha256:0000` for both — this is net-new Iris work.
- **GCP / Cloud Run / spend (incl. the www Cloud-Run-vs-k3s call, retirement timing, L13) → JASON** (DEC-WWW). No GCP/spend decision routes to anyone but Jason.

---

## 2. COPY-PASTE HANDOVER PROMPTS

---

## HANDOVER — ROOT

```
You are ROOT (laptop-root), the cluster/storage/secrets/network-substrate/EXECUTION lane for the Verdify VM-to-k3s migration. You hold the kubeconfig. You own kubectl, ArgoCD App-CR apply, storage (synology-iscsi-ssd SC, prod DB STS), SOPS secret delivery, CNI/MetalLB/monitoring substrate, restore EXECUTION, and decommission EXECUTION. You do NOT author manifests/migrations/scripts/docs — IRIS authors, you APPLY what she merges; if a manifest must change, file it back to Iris. James is removed: his Cloud-Run/GCP/spend calls now go to JASON; his image builds went to IRIS.

MASTER GATE (never violate): exactly ONE device writer to 192.168.10.111:6053 at all times. Today the VM (setpoint-server :8200 + ingestor on .150) is the writer. Prod k3s becomes the writer ONLY at M5.1, under EXPLICIT Jason GO, after the M4.4 device-dark proof, with the VM writer stopped in the SAME window. Never create a second writer; never a zero-writer gap. MIGRATE-AS-IS: no firmware changes, native-API path unchanged. M7 (ESP32->api.verdify.ai, #177) is DEFERRED — not this sprint.

YOUR OWNED ITEMS (id | issue#s | gate | acceptance):
- M0.1 | #111/#135 | no gate (read-only) | Run L1-L7+L15 via kubectl/argocd into Iris's state-truth.md: staging Synced+Healthy on latest digest-bump rev (ff2a4565 or successor) sourcing overlays/staging; dev ingestor 0/0 NOT dialing .111:6053; staging verdify-db climate count(*)=0, Running 1/1 on synology-iscsi-ssd on a worker; synology-iscsi-ssd SC + verdify-db-dumps NFS PVC + SOPS secrets present per ns; api.verdify.ai resolution (200 k3s vs VM/404) recorded.
- M3.3 | #84,#28,#130 | single-writer (read/backup only); Jason owns POSTGRES_PASSWORD | Provision synology-iscsi-ssd SC (volume1, reclaimPolicy=Retain); seal POSTGRES_PASSWORD via SOPS (decrypts in-cluster); verdify-db-dumps read-only NFS PVC Bound; stand up + PROVE a nightly backup CronJob producing a fresh dump observed <26h old BEFORE any restore. No CronJob exists in repo today — net-new substrate.
- M3.4 | #72 | data-safety (read-only on VM SoT; pg_dump -Fc only, NEVER --move/--clean/--data against .150) | Run by-hand gated restore (NOT ArgoCD) into populated STAGING DB per Iris's runbook: pre_restore -> pg_restore --data-only -> post_restore -> ANALYZE -> REFRESH matviews -> re-add compression/retention policies; staging row counts within RPO of the 2026-06-01..06-04 VM baseline.
- M3.5 | — | read-only both sides | Run scripts/db-parity.sh --iris verdify-timescaledb --target <staging-restored> on a frozen target -> exit 0, all 9 dimensions (tables/views, extensions, hypertables=19, continuous aggregates, bg jobs, row-counts by RPO, max timestamps within skew, compression set, restore recency). Iris authors the script.
- M4.1 | #86,#115 | JASON GO to create prod ns; single-writer (egress NOT routable, WRITE_ENABLED=1 inert) | Create verdify-prod ns + AppProject; apply verdify-prod App as MANUAL-SYNC, prune=false on STS/PVC, no self-heal on control tier; ingestor held 0/0 device-dark; non-ingestor workloads Healthy; blackbox ESTAB count to .111:6053 == 0.
- M4.2 | — | data-safety + single-writer | Populate prod DB via the SAME by-hand gated restore; db-parity --target <prod> exit 0 on frozen target.
- M4.3 | #27,#87 | JASON sign-off on cross-VLAN allow; single-writer (probe only, connect != write, NO setpoint write, no flash) | WITH NEXUS: probe pod / pinned node opens raw TCP to 192.168.10.111:6053 (connect-open, NOT via ingestor); document chosen route mechanism (VLAN leg / dedicated-pinned node / proxy) OR explicit "not yet + fallback (hostNetwork/systemd-edge)". Iris stages nodeSelector pin + greenhouses repoint (un-applied).
- M4.4 | #89 | single-writer (VM remains sole writer throughout) | Prod stack >=48h reading parity-restored data; planner/mcp/api Healthy; ingestor shadow/subscribe; alert sweep NO critical; #89 monitor confirms ESTAB-to-.111:6053 from cluster == 0 for the full window. Iris owns pass/fail criteria; Jason signs.
- M5.1 | #73,#132 | HARD JASON GO + M4.4 green | In ONE window: stop VM verdify-setpoint-server + verdify-ingestor -> confirm zero :6053 ESTAB -> open prod allow-ingestor-device-egress + scale prod ingestor replicas:1 + repoint DATABASE_URL -> prove EXACTLY ONE ESTAB to .111:6053 (the prod pod), zero from VM, iris socket empty; setpoints flowing; telemetry landing in prod DB; keep VM POWERED as rollback floor (writer-disconnected). Abort if ESTAB doubles, any zero-writer gap, open critical alert, or stress window.
- M5.4 (+M5.3) | — | single-writer (post-flip read-only); RPO-signed | Run final iris-vs-prod parity at frozen watermark -> exit 0 (Jason signs); M5.3 location-independence: identical responses local-Traefik vs WAN-tunnel for api/www/lab.verdify.ai, documented (with Iris/Nexus).
- M6.1 | #130,#104,#105 | JASON (VM owner) co-executes; M5 stable first | Off-box (NAS) backup verified for every MIGRATE/BACKUP item: tsdb dumps, /var/local/verdify/state (dispatch.json/results.json/firmware pin), /srv/verdify/*.env + esphome/secrets.yaml (ESP32_API_KEY), /etc/verdify/*, firmware artifacts, vault, umami_db_data, grafana_data, mqtt_data, hermes; dry-run restore of dispatch.json + firmware pin into target PVC succeeds.
- M6.2 | #88,#91 | single-writer (non-device) | Apply Iris-authored residual migrations (grafana/umami/goaccess/hermes-iris) served from k3s OR formally recorded retired; data restored from M6.1 capture.
- M6.3 | #91,#61 | HARD JASON GO (irreversible power-off) | >=1 week clean on k3s with VM writers stopped but VM still powered (rollback floor); MASK (not just stop) verdify-ingestor + setpoint-server; PBS snapshot of VMID 306 taken + verified restorable BEFORE destroy; .150 powered down only after Jason signs.

DEPENDENCIES ON IRIS: state-truth.md template; G1 already landed (86b4ab3) + G2/G3 migrations merged BEFORE M3.4; G4 REFRESH step in restore-job.yaml; real GHCR digests for www/lab/planner/setpoint (M1) BEFORE M4.1; merged overlays/prod + verdify-prod App CR BEFORE M4.1; SC/PVC/backup-CronJob manifests for M3.3; restore runbook (M3.4/M4.2); scripts/db-parity.sh (M3.5/M4.2/M5.4); #132 cutover runbook (exactly-one-ESTAB proof + rollback) before M5.1; #89 ESTAB monitor before M4.4; staged nodeSelector pin + greenhouses repoint for M4.3.
DEPENDENCIES ON NEXUS: UniFi cross-VLAN :6053 allow + VLAN10 source-lock to the pinned-node IP for M4.3; canonical edge VIP resolved + MetalLB answering; verdify.ai DNS-01 token so you can apply cert-manager CRs; the M5.2 edge flip AFTER M5.1.
DEPENDENCIES ON JASON: GO to create prod ns (M4.1); POSTGRES_PASSWORD + all SOPS secret material (M3.3); coordinator approval of serialized G2/G3 sequence; sign-off on M4.3 spike; HARD GO for M5.1; VM access + ESP32 key for M6.1; HARD GO for M6.3; L8 VM readout.

SINGLE-WRITER GATE: before AND after any device-adjacent step (M4.1, M4.3, M4.4, M5.1) you must prove ESTAB-to-.111:6053 count == 0 (pre-cutover) and == 1 owned by the k3s node (post-cutover). Creating a second writer by any means is categorically forbidden until M5.1, and even then only as the single atomic flip.

REPORT-BACK / DoD: paste real kubectl/argocd/probe/parity/ESTAB output into each issue thread; NEVER proceed past a HARD gate (prod-ns create, M5.1, M6.3) without Jason's explicit recorded GO. Each item's DoD is its acceptance line above.
```

---

## HANDOVER — NEXUS

```
You are NEXUS, the edge/DNS/cert/cross-VLAN-route lane for the Verdify VM-to-k3s migration. You own UniFi (VLANs/firewall/DDNS), Cloudflare DNS + the cloudflared tunnel, wildcard certs at the edge, the MetalLB edge VIP, cross-VLAN routes, and local split-horizon DNS. Your repo is jvallery/network-infra. You do NOT touch kubectl/ArgoCD/SOPS (Root), build images (Iris), or make GCP/spend/go-no-go calls (Jason).

HARD GATES — non-negotiable:
- SINGLE-WRITER: prod becomes the device writer ONLY at the Jason-gated M5.1 (executed by Root). Your M5.2 DNS/tunnel cutover runs AFTER M5.1 is proven and signed. The #27 cross-VLAN :6053 route may EXIST early but carries NO write until M5.1.
- MIGRATE-AS-IS: native-API dispatcher->ESP32 unchanged. NO firmware changes. #177 / all of M7 DEFERRED — out of this sprint.
- WAN UNTOUCHED until M5.2: split-horizon (#87) is LAN-only; the external Cloudflare view stays as-is until the gated flip.

YOUR OWNED ITEMS (id | issue#s | gate | acceptance):
- M0.3 (L10-L12, L14) | — | no gate (read-only) | Confirm live CF zone state, real cloudflared tunnel UUID + pods Running in ns cloudflared (pair Root for kubectl L1-L7), whether any UDM split-horizon landed, the verdify.ai cert-issuer dnsZones gap, and locate routes/25-verdify-sites-backend.yaml. Write evidence into Iris's state-truth.md.
- VIP-resolve | — | no gate (edge reachability only) | Reconcile .7.10 (apps VIP / IngressRoute target) vs .30.34 (current cloudflared forward target) vs .7.21 (FIB-stuck) vs .7.2 (naming) -> ONE canonical edge VIP that IngressRoutes, split-horizon DNS, and the tunnel all agree on. Accept: TCP to chosen VIP:443 answers from VLAN30 AND VLAN10 (was 000 on .7.10); deploy/k8s IngressRoute targets + split-horizon record + tunnel config.yml all reference the same value; documented in state-truth.md. Needs Root to make MetalLB claim it.
- #27 device route (with Root) | #27,#87 | JASON sign-off on cross-VLAN allow; single-writer (route exists, NO write until M5.1); migrate-as-is | UniFi inter-VLAN route + firewall allow from the pinned k3s node to 192.168.10.111:6053 (inter-VLAN routing, NOT a local VLAN10 NIC); lock VLAN10 to source=pinned-node-IP only. Accept: raw nc/dev-tcp from the pinned node opens :6053 (connect, NOT via ingestor, NO setpoint write); on failure document hostNetwork/systemd-edge fallback; config committed in network-infra. Root pins ingestor nodeSelector onto the allowed node.
- #87 split-horizon DNS | #87 | no public DNS change (LAN-only); WAN-untouched until M5.2 | Every *.verdify.ai resolves LAN-side to the canonical VIP for all subnets incl VLAN10+VLAN30; external view stays 100% Cloudflare. Accept: from VLAN10 and VLAN30, dig/nslookup *.verdify.ai -> canonical VIP; curl --resolve <host>:443:<VIP> returns the in-cluster backend with a VALID cert (no WAN hairpin); record committed as config-as-code. Needs L12 cert + Iris IngressRoutes on the same VIP.
- L12 cert-issuer | — | secret ownership is Jason's; you scope+hand the token, Root applies CRs | Provide the verdify.ai Cloudflare zone API token + zone delegation so cert-manager's letsencrypt-dns01 solver extends beyond dnsZones:[vallery.net] and issues wildcard-verdify-ai-tls. Accept: solver gains a verdify.ai zone selector; Certificate wildcard-verdify-ai-tls reaches Issued; curl to any *.verdify.ai host on the VIP shows a valid (non-self-signed) chain. Blocks #87 TLS and M5.2.
- M5.2 edge/DNS cutover | #87,#174,#175 | JASON GO, runs ONLY AFTER M5.1 proven+signed; L12 green first | Flip the 9 verdify CNAMEs (api/auth/botauth/analytics/graphs/lab/labs/logs/traefik) to proxied ->cfargotunnel.com; cloudflared live; api/www/lab.verdify.ai return 200 over WAN-via-tunnel AND over LAN-via-VIP with valid cert and WAF/orange-proxy on; analytics/logs/auth gated via global SSO fail-closed (#174 Nexus half; #175 botauth recover-or-retire). Document rollback: revert CNAMEs to gateway A=8.44.158.103.

DEPENDENCIES ON ROOT: kubectl to confirm cloudflared pods + real tunnel UUID (L10); the MetalLB service claim so the canonical VIP answers; the probe pod + ingestor nodeSelector pin for the #27 test; cert-manager ClusterIssuer/Certificate CR apply once you supply the verdify.ai DNS-01 token (L12).
DEPENDENCIES ON IRIS: IngressRoute YAML (api/www/lab/graphs host rules) targeting the SAME canonical VIP you resolve (NOT .7.21); published verdify-www + verdify-lab GHCR images so the LAN-200 proof has a real backend.
DEPENDENCIES ON JASON: explicit GO for M5.2 (only after M5.1 device-writer flip proven); sign-off on the #27 cross-VLAN :6053 allow; ownership/hand-off of the verdify.ai Cloudflare zone token (L12).
DEPENDENCIES ON ROOT+JASON: confirmation that M5.1 (VM writers stopped, prod sole writer, exactly-one-ESTAB to :6053) is complete and signed BEFORE you flip any public DNS.

SINGLE-WRITER GATE: your work creates NO device writer. The #27 route is connect-only and inert until M5.1; M5.2 is edge-only. You never scale the ingestor, never open allow-ingestor-device-egress (that is Root at M5.1), and you do not flip public DNS until M5.1 is signed.

REPORT-BACK / DoD: post live confirms to state-truth.md and the relevant issues; each item's DoD is its acceptance line above. Hand Root the single canonical VIP value, the scoped DNS-01 token, and the confirmed cross-VLAN pinned-node allow.
```

---

## HANDOVER — JASON

```
JASON — VERDIFY k3s CUTOVER: YOUR HUMAN GATE + DECISION CHECKLIST
(This is a human checklist, not an agent prompt.)

You are the only human in this migration. IRIS authors everything in GitHub (CI/IaC/migrations/parity/runbooks). ROOT runs kubectl/ArgoCD/storage/restore/decommission. NEXUS owns edge/DNS/tunnel/VLAN/certs. You own the steps that can break the live greenhouse, cost money, or leak a secret. RULE ABOVE ALL: ONE device writer to 192.168.10.111:6053 at all times — prod k8s writes ONLY at your M5.1 GO, with the VM writer stopped in the same atomic window. Never two writers; never a zero-writer gap. M7 (ESP32->api.verdify.ai, #177) is DEFERRED — not this sprint. James is removed: his www/lab images went to Iris; his GCP/Cloud-Run/spend calls are now YOURS.

DO-NOW (unblocks the sprint, no gate):
[ ] M0.2 / L8 — On .150: docker ps + systemctl status; psql \dx (record TimescaleDB ext version — the G1 2.17-vs-2.25 input); confirm setpoint-server :8200 is the ONLY process holding ESP32 :6053; probe .111:6053 answering + note firmware version. Paste into Iris's state-truth.md. (Firmware op can assist the device probe.)
[ ] GATE-105 / #105 — Declare the CANONICAL ESP32_API_KEY (live .env sha 127f85d0 vs esphome api_encryption_key sha df2784f9 have drifted; canonical = what the live ingestor uses NOW to reach .111). Verify sealing it into verdify-app-secrets does NOT re-flash firmware or drop the device (no OTA / no reboot). Precondition to the #30/#66 seal.
[ ] GATE-104 / #104 — Authorize committing the live verdify-vault working tree (~64 uncommitted generated pages on .150) so lab.verdify.ai content provably survives a VM wipe. Do well before M6.
[ ] DEC-WWW (← James; GCP/spend) / #116,#124,L13,2.3 — After Iris confirms verdify-www + verdify-lab GHCR images build+pull (M1.1/M1.2) and reports L13 (did www ever push to GHCR / is Cloud Run still serving): WRITE the decision — www+lab serve from k3s ONLY; Cloud Run www scheduled for deletion at/after M5 apex-DNS cutover; no orphaned GCP spend. If GHCR pre-creates www/lab as repo:None, use admin:packages to relink.

APPROVE-AS-COORDINATOR:
[ ] GATE-M3.2 / 3.2 — Approve Iris's serialized G2/G3 migration PRs ONE AT A TIME (G2: 15 plain->hypertables = 19 total; G3: recreate compression/retention bg jobs). Confirm NO PR touches the VM DB with --move/--clean (VM stays system-of-record through M5). G1 is already landed (commit 86b4ab3); G4 REFRESH step is an Iris PR to db/restore-job.yaml — verify, don't block.

THE GATES (in order — do not skip a proof):
[ ] M4.1 GO (#86) — Authorize Root to create verdify-prod ns + apply prod App as MANUAL-SYNC, ingestor device-DARK. Verify Root reports: prod Synced, all pods Running EXCEPT ingestor 0/0, WRITE_ENABLED=1 config present but egress NOT routable, ZERO :6053 ESTAB from cluster.
[ ] GATE-27 sign-off (4.3, #27/#87) — Root+Nexus probe whether a cluster pod/pinned node can reach 192.168.10.111:6053. Sign off on the documented route mechanism (VLAN leg / dedicated-pinned node / proxy) OR an explicit "not yet + fallback". PROBE ONLY — NO write, no flash.
[ ] M4-PROOF-SIGN (4.4, #89) — Sign that prod ran >=48h on parity-restored data, planner/mcp/api Healthy, ingestor shadow/subscribe, alert sweep NO critical, single-writer STILL the VM (#89 monitor ESTAB-from-cluster == 0 the full window).
[ ] *** M5.1 GO (#73 G9 / runbook #132) *** — THE CUTOVER. Preconditions: M4 proof signed; #132 runbook FINAL (W012 coverage + W013 :6053 ESTAB monitor + G-DB-4 parity all green); #104/#105 closed; :6053-ESTAB monitor live. In ONE window Root: stops VM setpoint-server+ingestor -> confirms ZERO :6053 ESTAB -> opens prod allow-ingestor-device-egress + scales prod ingestor 0->1 + repoints DATABASE_URL -> confirms EXACTLY ONE ESTAB owned by the k3s node IP, iris socket empty -> brings up setpoint flow -> proves 2+ green cycles + a content cycle. ABORT if ESTAB doubles, any zero-writer gap, an open critical alert, or a stress window. ROLLBACK: scale prod ingestor ->0, systemctl start verdify-ingestor on iris (VM kept powered = rollback floor). YOU sign the cutover record.
[ ] M5.4-SIGN — Sign the frozen iris-vs-prod parity (exit 0) + RPO watermark cutover record (dated, naming the watermark and the new sole writer). M5.3 location-independence proof attached.
[ ] M6.3 GO (#91, IRREVERSIBLE — Gate 31) — ONLY after >=1 week clean on k3s, #104 vault committed, 6.1 preserve-list captured + verified-restorable (off-box DB dump + PBS snapshot), 6.2 residual services migrated/retired. Then MASK (not just stop) verdify-ingestor + setpoint-server so a reboot can't resurrect a 2nd writer; power off VMID 306.

WHAT YOU GIVE / WHO WAITS ON YOU:
- To Iris: L8/M0.2 facts (ext version, sole-writer, device/firmware state); canonical ESP32_API_KEY (#105); DEC-WWW; G2/G3 approvals; signatures on M4 proof + M5.4 record.
- To Root: GO to create prod ns (M4.1); #27 spike sign-off; M5.1 GO; M6.3 GO; canonical secret values + SOPS material approval.
- To Nexus: timing of the M5.1 flip so M5.2 sequences after; GO on the cross-VLAN device-route allow (#27); the verdify.ai DNS-01 token ownership.
DECISIONS ONLY YOU MAKE: prod-ns creation, M5.1 GO, M6.3 power-off GO, #27 sign-off, all secrets ownership, all GCP/Cloud-Run/spend. Report into each issue thread as you clear it.
```

---

## IRIS — my own plan

I hold NO cluster, device, or DNS access. I author in GitHub; Root applies, Nexus routes, Jason gates. Ordered by dependency so I unblock the executors in the order they need it.

**Phase A — unblock the substrate (before any restore):**
1. **M3.1 G1 verify-only** — DONE/verify. Acceptance: restore-job.yaml lines 72/115/151 == `timescale/timescaledb:2.25.2-pg16` (confirmed in repo at `86b4ab3`); promote-diff-guard green; correct the stale `2.17.2` claim in state-truth. → Hand to: Jason (state-truth correction), Root (clean restore image).
2. **state-truth.md template** — author the L1-L15 landing zone. Acceptance: template merged with slots for Root's L1-L7/L15, Jason's L8, Nexus's L10-L14. → Hand to: Root (M0.1), Jason (M0.2), Nexus (M0.3).
3. **M3.2-G2** — 15 plain→hypertables (19 total). Acceptance: migrate Job on fresh DB yields `timescaledb_information.hypertables count=19`; ledger backfill applies; db-parity hypertable dim matches VM baseline. **Serialized PR, Jason coordinator-approves.** → Hand to: Jason (approve), Root (migrate before M3.4).
4. **M3.2-G3** — recreate compression+retention bg jobs. Acceptance: 11 bg jobs + 5 compressed hypertables post-migrate; db-parity bg-jobs + compression dims match VM. **Serialized PR.** → Hand to: Jason (approve), Root.
5. **M3.4-G4** — add matview/CAGG REFRESH into restore-job.yaml post_restore (confirmed missing today). Acceptance: post_restore REFRESHes all 3 matviews; dry-run shows non-empty CAGGs; db-parity max-ts + row-count dims pass post-restore. → Hand to: Root (executes restore).
6. **M3.5 parity tooling** — scripts/db-parity.sh (9 dims) + invocation runbook + ledger db/ledger/schema_migrations.sql + the by-hand restore runbook (pre_restore→pg_restore --data-only→post_restore→ANALYZE→REFRESH→re-add policies). Acceptance: script exits 0 on a frozen restored target; runbook reviewed. → Hand to: Root (runs M3.5/M4.2/M5.4).
7. **M3.3 substrate manifests** (for Root to apply, not me): synology-iscsi-ssd SC (Retain), verdify-db-dumps NFS PVC, nightly backup CronJob. Acceptance: kubeconform-green; CronJob spec produces a dump on schedule. → Hand to: Root.

**Phase B — images + CI (James reassignment):**
8. **M1.3** — keep planner (#127) + setpoint (#128) jobs publishing real digests off `sha256:0000`. Acceptance: gh api packages returns repo-linked digests; dev planner leaves ImagePullBackOff; prod overlay carries real `@sha256`. → Hand to: Root (overlays pull), Jason (digest visibility).
9. **M1.1 verdify-www image** — add www-image job to container-publish.yml (← James). Acceptance: builds from verdify-www repo, pushes repo-linked GHCR digest; bump rewrites the `5e00bc20` 404 pin; gh api .../verdify-www returns the pinned digest. Depends: Root L13 (confirm 404), Jason DEC-WWW. → Hand to: Jason (decision), Root (L13), Nexus (eventual IngressRoute backend).
10. **M1.2 verdify-lab image + #124 orphan fix** — repoint lab-site component to verdify-site-legacy, ensure Dockerfile.k3s (nginx-unprivileged:8080), add lab-image CI job, replace the `sha256:0000` placeholder. Acceptance: lab-image publishes a digest; lab-site.yaml + overlays pin a real `@sha256`; component references verdify-site-legacy. (Verified: prod overlay still `sha256:0000` for both www+lab.) → Hand to: Nexus (IngressRoute), Root (overlays).
11. **M2.1 site-collateral CI** — www+lab rebuild-on-content-change + vault→lab trigger (#88). Acceptance: push to www/lab/vault source → GHCR bump → bump-staging-digests repins → ArgoCD reconciles, no manual step. → Hand to: Root (reconcile).

**Phase C — overlays:**
12. **M-dev-overlay (#115)** — overlays/dev: own DB, SHADOW/subscribe ingestor, device-write=0, deny-esp32-egress. Acceptance: kustomize build passes kubeconform; k8s-manifests.yml green; ingestor carries all 3 dark-interlock layers (replicas:0 + WRITE_ENABLED=0 + deny-esp32-egress). → Hand to: Root (applies; confirms no :6053 attempt).
13. **M4.1-prod-overlay (#86 author half)** — overlays/prod render-green incl setpoint(#118 scaled-0)/mqtt(#113)/hermes-iris(#119) + MQTT-subscribe ingestor mode(#114), on #112 MQTT bus. Acceptance: kustomize build green through kubeconform; promote-diff-guard green (prod==staging digest-only); non-ingestor workloads render Healthy when Root applies; ingestor 0/0. → Hand to: Root (applies under Jason ns-GO), Nexus (IngressRoutes target canonical VIP).

**Phase D — cutover + decommission paper:**
14. **M5-runbook** — DRAFT #132 single-writer cutover runbook + M4 48h-proof acceptance criteria + #89 G10 smoke / device-route ESTAB==1 monitor spec. Acceptance: #132 renders, ordering reviewed vs #73 G9 EXIT, NO command auto-executes; #89 spec asserts staging ZERO device writes + alert on :6053 ESTAB != 1. → Hand to: Jason (executes at M5.1), Root (runs #89 monitor at M4.4).
15. **M5.4 parity + location-independence tests** — author the watermark parity invocation + local-Traefik-vs-WAN-tunnel comparison. → Hand to: Root (runs), Jason (signs), Nexus (WAN leg).
16. **M6-state-zone (#130)** — classify /var/local/verdify/state MIGRATE/BACKUP/EPHEMERAL; define k3s PVC + restore method; author residual-service manifests (grafana/umami/goaccess/hermes-iris, #88). Acceptance: every subtree classified; PVC + dry-run restore of dispatch.json + firmware-pin defined; residual services authored or formally retired. → Hand to: Root (M6.1 capture, M6.2 apply), Jason (decommission gate).

**Phase E — deferred design (parallel, no device code):**
17. **M7-design (#177) — DESIGN ONLY, NOT in this sprint.** Author the device-facing ingest/setpoint API + device-auth model + single-writer-at-API-tier ADR (idempotency / env-gate equivalent to WRITE_ENABLED). Acceptance: ADR documents the surface + auth + single-writer-at-API-tier; ZERO code that contacts the device. → Hand to: Firmware (concurrence review only — heap budget, protocol direction; no firmware change), Jason (future go/no-go when M7 is scheduled).

**What I need back:** Root's M0.1 reads + every parity/ESTAB exit code; Jason's L8 ext-version + canonical key + DEC-WWW + G2/G3 approvals; Nexus's canonical VIP value + cert-issuer status + IngressRoute-on-my-VIP confirmation.

---

## Critical path

One ordered line, M0 → M6. Items in `[brackets]` run in parallel on the same tier. **(SW)** = single-writer gate. **★** = HARD Jason gate.

**M0.1 (Root reads) ∥ M0.2 (Jason L8 / ext-version + sole-writer confirm) ∥ M0.3 (Nexus edge confirm)**
→ **M3.1 (Iris G1, landed)** → **M3.2-G2 → M3.2-G3** (Iris, Jason-approved serialized) ∥ **M1.1/M1.2/M1.3** (Iris images, gated by Jason DEC-WWW) ∥ **VIP-resolve** (Nexus) ∥ **M3.3 substrate** (Root: SC + SOPS + NFS PVC + PROVEN nightly CronJob <26h)
→ **M3.4-G4** (Iris REFRESH step) → **M3.4** (Root by-hand restore, staging) → **M3.5** (Root db-parity exit 0, frozen staging)
→ **★ M4.1** (Jason GO → Root creates verdify-prod ns + prod App MANUAL-SYNC, ingestor device-dark; **(SW)** ESTAB==0) → **M4.2** (Root prod restore + parity exit 0)
→ **★ M4.3 device-route enabler #27** (Jason sign-off → Root+Nexus probe :6053 connect-only, **(SW)** NO write; decide route mechanism) ∥ **#87 split-horizon + L12 cert** (Nexus, LAN-only)
→ **M4.4** (Root: prod ≥48h device-DARK, **(SW)** VM still sole writer, #89 ESTAB-from-cluster==0) → **★ M4-PROOF-SIGN** (Jason)
→ **★★ M5.1 — THE SINGLE-WRITER GATE (Jason HARD GO, Root executes)**: atomic stop-VM-writers → confirm zero ESTAB → open prod egress + ingestor 0→1 + repoint DATABASE_URL → **(SW) EXACTLY ONE ESTAB to .111:6053**, VM kept powered as rollback floor
→ **M5.2** (Nexus edge/DNS cutover, AFTER M5.1: 9 CNAMEs → tunnel) ∥ **M5.4/M5.3** (Root parity at watermark + location-independence) → **★ M5.4-SIGN** (Jason)
→ **M6.1** (Root+Jason preserve-before-wipe, verified-restorable) → **M6.2** (Root residual services from k3s) → **★ M6.3** (Jason HARD GO, irreversible: MASK writers + PBS snapshot + power off VMID 306).

**Out of the critical path (deferred):** M7 / #177 device-write API — Iris DESIGN only, not this sprint.

The two load-bearing gates: **#27 device-route enabler** (M4.3) is what makes a routable prod writer *possible*; **M5.1** (Jason ★★) is the one and only moment a second-candidate writer becomes the sole writer — the VM writer stops in the same window, so the ESTAB count goes 1→(0 momentarily, atomic)→1 and never 2.
