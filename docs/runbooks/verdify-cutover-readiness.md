# Verdify k3s Cutover Readiness — Master Doc

**Status:** PREP / DESIGN. This is the single "are we ready to cut over to k3s?" doc.
**Nothing in this doc has been executed.** No cluster apply, no `kubectl`/ArgoCD sync, no
PR merge, no secret seal/read, no firewall/route change, no device touch, no setpoint push,
no firmware flash, no live-service stop. Every action is teed up for a named human owner at a
named gate.

**Authored by:** Verdify `firmware` agent (synthesis of the four design tracks).
**Date:** 2026-05-30.
**Branch state:** `firmware/cicd-golden-path` @ `f350bcd` (== PR #55 into `VerdifyConsultancy/verdify-platform`).
**Authoritative plan:** `/mnt/agents/root/docs/verdify-cicd-refactor-handoff.md` (P0–P9 + §5 DoD + §6 guardrails).

> **The one rule above everything: Track A (the greenhouse stays alive) > Track B (this
> refactor), always.** Plants are alive; the ESP32 is in a 5–10s control loop. The k3s stack
> runs **alongside** the live VM compose/systemd stack — purely additive — until each piece is
> independently proven. The VM stack is the source of truth and is NOT stopped until a verified,
> per-service, Jason-confirmed cutover. The device-touching ingestor moves LAST or never. No
> firmware OTA path is EVER part of cutover.

## The four runbooks this master doc stitches together

| Runbook | Scope | Relative path |
|---|---|---|
| Device-VLAN spike | Prove a pod can reach ESP32/HA/MQTT within the 5–10s SLA (read-only) | [`./device-vlan-spike.md`](./device-vlan-spike.md) |
| DB copy-not-move | Copy TimescaleDB data into in-cluster `verdify-db` + verify, never move/stop live | [`./db-copy-not-move.md`](./db-copy-not-move.md) |
| Cutover sequence + DoD | The P9 service-by-service stop order + the 11-item Definition-of-Done | [`./k3s-cutover-sequence.md`](./k3s-cutover-sequence.md) |
| Secret sealing plan | SOPS+age secret inventory + pipe-only sealing + Jason/James/laptop-root split | [`./verdify-secret-sealing-plan.md`](./verdify-secret-sealing-plan.md) |
| (prior) Golden-path status | Per-PR validation matrix + DoD baseline | [`./verdify-cicd-golden-path-status-2026-05-30.md`](./verdify-cicd-golden-path-status-2026-05-30.md) |

---

## 1. STATUS — one screen

### DONE (additive artifacts, validated, on branch `firmware/cicd-golden-path` @ `f350bcd`)

- **k3s manifests:** `deploy/k8s/base/` (9: namespace, configmap, db-statefulset, migration-job,
  api/mcp/ingestor deployments, networkpolicy, kustomization) + `overlays/local-staging/` (4).
  `kustomize build …/local-staging | kubeconform -strict -ignore-missing-schemas` → **16/16 Valid**;
  base 15/15. `make lint` exit 0; `actionlint` exit 0.
- **Container images:** `api/Dockerfile`, `mcp/Dockerfile` (wraps `mcp/server.py`, zero tool-semantic
  change), `ingestor/Dockerfile` (net-new), **+ `db/Dockerfile.migrate`** (postgres:16-alpine replaying
  `db/schema.sql` + migration 000). All build locally, GIT_SHA baked, non-root uid 1000.
- **The 4 staging fixes (this branch):** (1) `verdify-migrate` schema image + `migration-job.yaml`
  ArgoCD PreSync; (2) MCP probe = `tcpSocket:8000` (FastMCP serves `/mcp`, not `/health`);
  (3) api probe = `/health` (`api/main.py:911`, correct); (4) **device safety** —
  `overlays/local-staging/ingestor-replicas-zero.yaml` pins the ingestor (the ONLY device-touching
  workload) to `replicas:0` so ArgoCD selfHeal cannot bring up a second live-ESP32 writer.
- **DoD #4 (gates green)** and **DoD #7 (firmware pipeline intact — CI never flashes/OTAs)** are DONE.
- **The four prep runbooks + their backing manifests** (`deploy/k8s/diagnostics/device-vlan-spike.yaml`
  throwaway probe, `db/restore-job.yaml` human-gated data restore) — all additive, validated, NOT
  wired into any kustomization/ArgoCD.

### STAGED / PR-OPEN (artifacts ready, not merged)

| PR | Repo | State | Owner to merge |
|---|---|---|---|
| **#55** | `VerdifyConsultancy/verdify-platform` (`firmware/cicd-golden-path` → `main`) | OPEN, MERGEABLE; 0-behind/7-ahead of `main`; carries the 4 Vanda commits + 3 CI/CD commits | **James** |
| **#8** | `jvallery/agent-fleet-control` (registry secret-metas, NO values) | OPEN, MERGEABLE | **laptop-root** (review); **James** (meta owner) |
| **#263** | `jvallery/agents` (ArgoCD Application + secret-sync arm) | OPEN, MERGEABLE | **laptop-root** |

### GATED — and on whom

| Area | Gate | Owner |
|---|---|---|
| §3.4 device-VLAN route + spike run | route posture is STOP-and-ask; apply is cluster | **Jason** (route) + **laptop-root** (apply) |
| TimescaleDB copy + verify | quiescence window; cluster apply | **Jason** (window) + **laptop-root** (apply/verify) + **coordinator** (G1–G4) |
| Secret sealing (5 clean keys + GHCR) | registry PR merged first | **laptop-root** |
| Secret sealing (ESP32 PSK) | DEVICE-AFFECTING — canonical-key confirm | **Jason** then **laptop-root** |
| Secret source reconciliations (§1.3 A/B/C) | source-of-truth edits | **James** |
| Every live VM service stop | irreversible-ish posture change | **Jason** (per service) |
| First live setpoint from a pod | DEVICE-AFFECTING | **Jason** |
| Single-writer ingestor cutover | DEVICE-AFFECTING (never two writers) | **Jason** + **laptop-root** |
| PR #55 / #8 / #263 merges | org/repo write | **James** (#55) / **laptop-root** (#8, #263) |

### Known blockers surfaced by audit (must resolve before execution)

- **G1 (HARD):** TimescaleDB version skew — live VM DB is **2.25.2**, but `db-statefulset.yaml` +
  `restore-job.yaml` pin **2.17.2-pg16** (a downgrade, unsupported). Bump the image to **≥2.25.2-pg16**
  before any restore. Editing `db-statefulset.yaml` is **coordinator/laptop-root** scope, not this prep.
- **G2/G3/G4 (topology fidelity):** only 4 of 20 live hypertables are repaired by migration 000;
  compression/retention policies and the 3 plain matviews are not recreated by `schema.sql`/migration 000.
  Functional copy works; DoD #11 (hypertable + compression parity) is not met until these are decided
  (serialized migration PRs — **coordinator**). See [`./db-copy-not-move.md`](./db-copy-not-move.md) §2.
- **Secret source drift (§1.3):** `VERDIFY_WRITE_API_KEY` is named `API_WRITE_TOKEN` on the VM;
  `MQTT_USER`/`MQTT_PASS`/`HERMES_IRIS_API_KEY` live in `ingestor/.env` not `/srv/verdify/.env`; 4
  contact-form keys are absent at source. Only **5 of 13** app-secret keys seal cleanly today. **James**
  reconciles before a clean `verdify-app-secrets` seal. See [`./verdify-secret-sealing-plan.md`](./verdify-secret-sealing-plan.md) §1.3.
- **`/health/detailed` (DoD #1):** baked `VERDIFY_GIT_SHA` endpoint not implemented (api has only
  `/health`). Api-scope-owner code change — not edited here.

---

## 2. End-to-end cutover CRITICAL PATH (ordered, owner-tagged)

This stitches the four runbooks into one ordered checklist. Each item: **owner** + **gate**. Strict
ascending device-risk order — site/api/mcp (zero device risk) move first; the ingestor/device loop is
dead last and separately gated.

### Phase A — Land the artifacts (no cluster, no device)

- [ ] **A1. Reconcile `live/platform-main` ↔ `main` branch name + drift.** Canonical production branch
  is `origin/live/platform-main` (the handoff's `origin/live` is a 404). Every downstream `origin/live`
  / `vm-verdify` reference must be corrected to `live/platform-main` / `vm-docker-iris`.
  — **Owner: Jason + James.** Gate: production-branch behavior; no autonomous rename/merge.
- [ ] **A2. Merge PR #8 (registry secret-metas)** — land first so namespace/secret names are canonical.
  — **Owner: laptop-root** (review), **James** (meta owner). Gate: `make validate` + `make verify-reproducible` green (already are).
- [ ] **A3. James lands §1.3 source reconciliations** (`API_WRITE_TOKEN` alias, MQTT/HERMES source path,
  contact-form keys confirm/drop). — **Owner: James.** Gate: source-of-truth edit.
- [ ] **A4. Merge PR #55 (app: deploy/k8s + Dockerfiles + CI + migrate image).** Reconciles `main` with
  `live/platform-main` (adds 4 Vanda + 3 CI/CD commits in one shot). — **Owner: James** (VerdifyConsultancy
  org write). Gate: org merge; `.github/**` shared-infra coordinator review.
- [ ] **A5. Merge PR #263 (ArgoCD Application + secret-sync arm).** — **Owner: laptop-root.** Gate: wires
  into the live cluster.
- [ ] **A6. (follow-on, separate) Fast-forward/merge the 3 CI/CD commits into `live/platform-main`** so
  production carries them. — **Owner: Jason + James.** Gate: production-branch behavior change.

### Phase B — Stand up staging (cluster-side, no device, no live-service stop)

- [ ] **B1. Create the `verdify-staging` namespace** (ArgoCD `CreateNamespace=false`; must pre-exist,
  byte-identical everywhere). — **Owner: laptop-root** (+ Jason authorizes new namespace). Gate: cluster apply.
- [ ] **B2. Seal `verdify-ghcr-pull` + `verdify-app-secrets` (5 clean keys; the seal renders all 13 and
  aborts on the 8 blocked until A3 lands).** Pipe-only `seal-secret.sh <id> --remote jason@vm-docker-iris…`
  — no value on CLI. — **Owner: laptop-root.** Gate: registry PR merged (A2) + A3 done; non-device.
- [ ] **B3. Extend `local-k8s-secret-sync.yml` with a `verdify-staging` arm + run the first sync into the
  new namespace** BEFORE ArgoCD reconciles. — **Owner: laptop-root.** Gate: first sync into a new namespace
  (confirm with laptop-root + Jason).
- [ ] **B4. ArgoCD reconcile — deploy the stack into k3s** against an empty DB. Pods come up Healthy;
  `verdify-migrate` PreSync Job builds the schema. **Ingestor stays `replicas:0` (device-safe).**
  — **Owner: laptop-root.** Gate: cluster apply. Rollback = revert image pin / delete Application (k3s-side only).

### Phase C — Device reachability proof (read-only; no device write)

- [ ] **C1. Confirm the pod-net → device-VLAN route is in place** (the 2026-05-30 L3 spike reached the
  ESP32 at ~8ms with no firewall change, so this may already be satisfied — confirm, do not assume).
  — **Owner: Jason** (network posture) + **laptop-root** (cluster/UniFi). Gate: **STOP-and-ask-Jason** —
  any firewall/router/VLAN/MetalLB/CNI change is device-network-affecting. See [`./device-vlan-spike.md`](./device-vlan-spike.md) Gate 0.
- [ ] **C2. Apply + run the throwaway `device-vlan-spike` Pod** (`deploy/k8s/diagnostics/device-vlan-spike.yaml`)
  in `verdify-staging`; read the per-target PASS/FAIL + RTT; delete the Pod. Read-only — no ESPHome session,
  no MQTT pub/sub, no secret mounted. — **Owner: laptop-root** (apply) / **Jason** (authorize C1 first). Gate: cluster apply.
- [ ] **C3. Record the cutover decision.** PASS → ingestor CAN move (Option 1), pending egress NetworkPolicy
  + single-writer choreography. FAIL on a required target → **ingestor stays VM-side permanently** (Option 2),
  which still satisfies DoD #6/#8. — **Owner: firmware agent + Jason.** Gate: reachability decision only; does
  NOT authorize a setpoint.

### Phase D — DB copy-not-move + verify (no live-DB write, no live-DB stop)

- [ ] **D1. Resolve G1 (version skew, HARD) + decide G2/G3/G4** before any restore. — **Owner: coordinator /
  laptop-root** (G1 image bump in `db-statefulset.yaml` + `restore-job.yaml`); **coordinator** (G2/G3 serialized
  migration PRs). Gate: schema decision.
- [ ] **D2. Take a consistent READ-ONLY `pg_dump -Fc` of the live VM DB** + record baseline counts +
  `max(ts)` watermark. — **Owner: firmware-coordinator.** Gate: read-only on live, timing coordinated with Jason.
- [ ] **D3. Provision the read-only NFS dump PV/PVC + set `restore-job.yaml DUMP_FILE`; apply the restore Job**
  (NOT ArgoCD-synced) → `pre_restore()` → `pg_restore --data-only` → `post_restore()` → ANALYZE → refresh matviews.
  — **Owner: laptop-root.** Gate: in-cluster apply.
- [ ] **D4. Run the V1–V11 verify checklist** (row-count parity per hypertable, chunk/compression parity,
  matview refresh, extension parity). All must pass. — **Owner: laptop-root** runs, **coordinator** confirms.
  Gate: trust gate — no cutover talk until parity proven. See [`./db-copy-not-move.md`](./db-copy-not-move.md) §4 step 6.

### Phase E — Stateless service cutovers (lowest device risk first; each VM stop is Jason-gated)

Pattern for each: k3s side comes up Healthy ALONGSIDE the VM service → prove parity → soak → **Jason
confirms** the VM-service stop (`docker compose stop` / `systemctl stop`, never `down -v`, never the VM).
Rollback is always `up -d` / `systemctl start` the untouched VM service.

- [ ] **E1. Stage 1 — `verdify-site` (Quartz/nginx):** prove parity, then stop VM site. — **Owner: Jason**
  (stop), **laptop-root** (ingress/LB). Gate: VM-service stop.
- [ ] **E2. Stage 2 — `verdify-api` (FastAPI, apps-pool LB VLAN 7):** prove on `.7.x` (real IP is a
  `[GATE: laptop-root]` reservation; `192.168.7.21` is a PLACEHOLDER), confirm the unauth-write hatch is
  CLOSED (`VERDIFY_ALLOW_UNAUTHENTICATED_WRITES` unset, `require_write_access()` fails closed), then stop
  VM api. — **Owner: Jason** (stop), **laptop-root** (LB IP). Gate: VM-service stop.
- [ ] **E3. Stage 3 — `verdify-mcp` (planner tool surface, ClusterIP DANGER):** point the planner at the
  k3s mcp by cluster DNS, prove read-only tools round-trip (NEVER fire `set_plan`/`set_tunable` as a verify),
  confirm the planner points at exactly one mcp, then stop VM mcp. — **Owner: Jason.** Gate: VM-service stop +
  planner re-point (no double-served writes).
- [ ] **E4. Stage 4 — grafana / umami / goaccess / traefik:** one at a time, traefik last so ingress never
  drops. — **Owner: Jason** (per service). Gate: VM-service stop.

### Phase F — DB write-ownership handoff (atomic; one writer only)

- [ ] **F1. Stage 5 — atomic DB write-ownership handoff:** at a quiescent moment, final incremental
  top-up (read-only on live, keyed on the D2 watermark), re-verify parity, then flip the writer. **Both
  stacks must NEVER write the same DB.** Coupled to Phase G (the VM ingestor is the last writer). —
  **Owner: Jason** (quiescence + writer flip). Gate: quiescence window.

### Phase G — Device-loop cutover (LAST, MOST GATED — touches the live ESP32)

Does NOT proceed unless ALL of C3=PASS, G1 resolved, secrets sealed, and the single-writer invariant
holds. If C3=FAIL, **STOP — the ingestor stays VM-side permanently** (recorded decision, DoD #6/#8 met).

- [ ] **G1. Egress NetworkPolicy `allow-ingestor-device-egress`** (today a commented `gated-§3.4`
  placeholder in `deploy/k8s/base/networkpolicy.yaml`) — enable only after a C2 PASS, via a separate
  reviewed PR (touches a third-VLAN allowance). — **Owner: Jason + laptop-root.** Gate: firewall/router posture.
- [ ] **G2. Seal `verdify-esp32-psk` / `ESP32_API_KEY` (DEVICE-AFFECTING).** Confirm `127f85d0` is canonical
  (the live healthy ingestor's runtime key), confirm rotate-at-seal vs carry-existing, confirm `ingestor/.env`
  == runtime/DB, confirm NO re-flash. Seal-source is the ingestor runtime env / `ingestor/.env`, NEVER the
  drifted esphome `secrets.yaml` (`df2784f9`). — **Owner: Jason** confirms, **laptop-root** seals. Gate:
  device-affecting (handoff §6/P2 STOP-and-ask).
- [ ] **G3. First live setpoint from a pod.** Scale the k3s ingestor to `replicas:1` (flip
  `overlays/local-staging/ingestor-replicas-zero.yaml` 0→1; base is `replicas:1` + `strategy:Recreate`,
  RollingUpdate FORBIDDEN) and confirm the FIRST real setpoint push to the ESP32. — **Owner: Jason.** Gate:
  device-affecting, explicit confirmation (handoff §6/P7).
- [ ] **G4. Single-writer cutover.** Stop the VM `verdify-ingestor.service` at the SAME INSTANT the k3s
  ingestor opens its connection — zero overlap (cleanest: stop VM unit first, confirm its aioesphomeapi
  connection released, then scale k3s to 1). **Never two writers.** Verify single-writer held + confirm-rate
  + band-change latency within baseline (~95% confirm, p50 37s / p95 81s) + occupancy→light in 5–10s.
  — **Owner: Jason** (+ laptop-root). Gate: device-affecting, explicit confirmation (handoff P9).

### Phase H — VM decommission (only when green + proven, service-by-service)

- [ ] **H1. Soak each migrated service**, then leave the VM stack intact for the rollback window. The VM
  is NEVER stopped wholesale; only individual services, one at a time, each Jason-confirmed (Phases E–G).
  DB PVCs are Retain on both sides; the VM DB stays intact for the soak. — **Owner: Jason** (per service).
  Gate: every source-service stop.

---

## 3. BLOCKED ON JASON — the shortlist (decisions only he can make)

These are the device-affecting / irreversible-posture / live-stop decisions. No one else can clear them.

1. **Canonical ESP32 key confirmation** — confirm `ESP32_API_KEY` sha `127f85d0` is the running ingestor's
   key (the live healthy ingestor proves it), confirm `ingestor/.env` == runtime/DB, confirm rotate-vs-carry,
   confirm **NO re-flash**. Blocks the `verdify-esp32-psk` seal (Phase G2). Reconcile-at-source, never re-flash.
2. **The device-VLAN route enable / confirm** — any firewall/router/VLAN/MetalLB/CNI change is a STOP-and-ask
   boundary. The 2026-05-30 L3 spike suggests the route is already in place; Jason confirms before C2 runs (Phase C1).
3. **Each live VM service stop** — site, api, mcp, grafana/umami/goaccess/traefik, the DB write-ownership flip,
   and the ingestor stop. One at a time, each explicitly confirmed (Phases E, F, G4).
4. **The first live setpoint from a pod** — the moment the refactor first touches the greenhouse (Phase G3).
5. **The DB quiescence window** — when to take the snapshot top-up and flip write-ownership (Phases D2, F1).
6. **Branch reconciliation** (with James) — fast-forward CI/CD commits into `live/platform-main` (Phase A1/A6).

(Also blocked, but on others: **James** owns the §1.3 secret source reconciliations + the PR #55 merge;
**coordinator/laptop-root** owns the G1 TimescaleDB version-skew image bump + G2/G3 migration decisions;
**laptop-root** owns every cluster apply.)

---

## 4. What I (firmware agent) have COMPLETED vs what is NOT mine to execute

**Completed (design/PREP, additive to this worktree, all validated):**

- The 4 k3s staging fixes on this branch (migrate image + Job, MCP `tcpSocket:8000` probe, api `/health`
  probe confirmed correct, `ingestor-replicas-zero.yaml` device-safety pin).
- The four prep runbooks + backing manifests: `device-vlan-spike.md` + the throwaway probe Pod;
  `db-copy-not-move.md` + `db/restore-job.yaml`; `k3s-cutover-sequence.md` (P9 order + 11-item DoD);
  `verdify-secret-sealing-plan.md` (secret inventory + pipe-only seal plan). And **this master readiness doc.**
- Read-only verification: the `live/platform-main` ↔ `main` reconciliation math, the live-DB ground-truth
  audit (G1–G4), the secret inventory (names/paths/owners only — no value read), and the ESP32 canonical-key
  finding (`127f85d0` runtime-canonical, `df2784f9` esphome drifted).
- All gates green for the artifacts: `make lint`, `kustomize | kubeconform` 16/16, `actionlint`, registry
  `make validate` / `make verify-reproducible`, local image builds.

**NOT mine to execute (by program design + the hard gates):**

- **No cluster apply / kubectl / argocd / namespace creation / secret-sync** — that is **laptop-root** on the
  protected runner.
- **No PR merge** — PR #55 is **James** (VerdifyConsultancy is read-only to me); PR #8/#263 are laptop-root.
- **No secret seal, no secret value read/echoed/logged** — sealing is laptop-root (pipe-only); the ESP32 PSK
  seal is gated on Jason's canonical confirmation.
- **No firewall/router/VLAN/MetalLB/CNI change** — STOP-and-ask-Jason; the route enable is Jason + laptop-root.
- **No device touch** — no ESPHome session, no setpoint push, no occupancy/MQTT pub, no OTA, no re-flash. The
  first live setpoint from a pod and the single-writer cutover are Jason-confirmed.
- **No live-service stop, no DB write, no DB/VM stop** — every VM-service stop and the DB write-ownership flip
  are Jason-gated, per service, with the VM left intact for rollback.
- **No `db-statefulset.yaml` edit** (the G1 image bump) and **no schema/migration PR** (G2/G3) — those are
  coordinator scope; I flagged them, I did not edit them. No `firmware/lib/**`, `greenhouse_logic.h`,
  `entity_map.py`, or `mcp/server.py` semantic edits.

**Bottom line:** the turnkey design + runbook + manifest package is complete and validated. Every remaining
step is a checklist item teed up for Jason (device/secret/stop confirmations), laptop-root (cluster apply),
James (org merges), or coordinator (schema/version decisions). Track A was never at risk; nothing live was
perturbed.

---

## ADR-15 / Model B′ alignment — apps-ingress (added 2026-05-30, post-workflow)

> The four design runbooks above were authored before **ADR-15 — "Apps ingress = single shared
> apps-ingress VIP on VLAN 7 (Model B′)"** (Accepted by Jason 2026-05-30,
> `/mnt/agents/root/docs/ADR-15-apps-ingress-vlan7.md`). This section reconciles the `verdify-api`
> exposure with that decision. The DB/spike/secret tracks are unaffected.

**Decision.** HTTP apps on VLAN 7 do **not** get a per-app `type: LoadBalancer`. They become
**ClusterIP + a host-routed `IngressRoute` + a strip-identity `Middleware`** behind **one shared,
registry-allocated apps-ingress VIP**, with central Authentik forward-auth + edge `X-Authentik-*`
stripping. ADR-15 explicitly names `gravity-ui .7.20` and **`verdify-api .7.21`** as the per-app-LB
**anti-pattern to retire**.

**Impact on this package.** `overlays/local-staging/api-loadbalancer.yaml` (the `.7.21` LB) is the
ADR-15 anti-pattern. It stays as the **interim serving path** only — ADR-15 (Consequences) records that
`verdify-staging` has **no IngressRoute and no strip Middleware today, so the `.7.21` LB is its only
serving path**, making its convergence an **add-then-drop onboarding** (a *fresh* onboarding, not
gravity's in-place flip): first **add** the ClusterIP-backed IngressRoute + strip Middleware on the
apps Traefik and prove `verdify` serves 200/302, **then drop** the LB. Dropping the LB first would
break it.

**Gate + owner.** This is gated on the **shared apps-ingress VIP / apps-Traefik existing first** —
get-well-plan **C1**, a HARD precondition that does **not exist yet** (`traefik-apps` ns absent). Per
ADR-15's **responsibility seam**, the apps-ingress VIP Service, the per-app IngressRoute/Middleware/
ClusterIP, the registry `app` record, and the reconciler are **laptop-root / registry-owned**
(reconciler-generated from the registry, *never* hand-mapped). So the firmware agent does **not** author
the verdify IngressRoute; it hands laptop-root the registry app-record inputs below. The base
`verdify-api` Service is already `ClusterIP` (the LB is overlay-only), so it is ADR-15-ready with no
base change.

**Registry `app`-record inputs for `verdify-api`** (for laptop-root/registry to add per ADR-15 §5 — the
net-new `app` kind + the `apps-ingress` VIP allocation on `apps-vlan.yaml`):

| Field | Value | Note |
|---|---|---|
| host | `verdify-k3s.vallery.net` | rides the live edge `k3s-wildcard` router (central Authentik + proxy already wired); the public vanity host (`verdify.ai` / `verdify.vallery.net`) is a separate edge/DNS decision (nexus, gated) |
| namespace | `verdify-staging` | matches the pinned overlay namespace |
| service / port | `verdify-api` / `8080` | the base ClusterIP Service port (`targetPort: http`) |
| auth | **DECISION NEEDED** | `verdify-api` has **public-read** endpoints (it backs the public site data) **and** a key-guarded write hatch (`VERDIFY_WRITE_API_KEY`). `auth: forward` would gate the public reads behind Authentik. Likely **`auth: none`** (public read) with the write endpoint key-guarded at the app layer — confirm with web/laptop-root before generating the router. |
| tls | edge `cloudflare` wildcard (`*.vallery.net`) | no per-app cert |
| strip | full 12-header `X-authentik-*` set | anti-spoof Middleware (ADR-15 §3); `gravity-strip-identity-headers` is the template |

**Net:** retire `.7.21` is **laptop-root's C1/C2 step** (add-then-drop, gated). My `api-loadbalancer.yaml`
carries an inline ADR-15 note pointing here. No `.7.x` is assumed allocated; reserving the **one shared
VIP** (e.g. `.7.2`) in the registry replaces both `.7.20` and `.7.21`.
