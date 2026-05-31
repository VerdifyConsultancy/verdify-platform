# State of Verdify — Migration Report

_Prepared for Jason · 2026-05-31 · synthesized from 7 independent tracer probes of host `vm-docker-iris`, branch `origin/firmware/cicd-golden-path` (PR #55), and the GitHub org._

---

## 1. Headline & overall status

**The greenhouse is safe and the live control loop is healthy right now.** On `vm-docker-iris`, every Track-A component is fresh-to-the-second: `climate` and `climate_action_log` are 7s old, the ESP32 at `192.168.10.111:6053` is reachable, the planner delivered SUNRISE/SUNSET/MIDNIGHT/FORECAST_DEVIATION plans today, and there are **zero open critical/high alerts** (the firmware-deploy preflight block query returns 0).

**The migration is on-track but earlier-stage than a green PR #55 suggests.** The k3s/ArgoCD target is fully designed, manifests are CI-validated, and a `verdify-staging` instance appears to have been stood up on root's cluster — but **no production cutover has begun, the build->publish->deploy pipeline has never run end-to-end (container-publish fails at workflow load), and the two P0 device-safety prerequisites are unimplemented.** The migration poses **no current threat** to the loop because nothing has been cut over; the risk is concentrated entirely in the future Stage 5/6 DB-and-ingestor handoff.

**Overall: on-track-with-risks.** Track A is the strong story; Track B is well-architected scaffolding with several load-bearing gates still closed.

---

## 2. Deployment topology today — before / after

### BEFORE (authoritative production — confirmed by probe)

`vm-docker-iris` runs a **hybrid** stack. This is not a pure docker-compose world:

- **Real-time control plane = HOST systemd units** on venv `/srv/greenhouse/.venv`: `verdify-ingestor` (PID 3658), `verdify-setpoint-server` (PID 3424, `0.0.0.0:8200`, the only thing touching the ESP32), `verdify-mcp` (PID 3421, `0.0.0.0:8000`). **These are the plants-alive services and they are NOT containers.**
- **Stateful/edge = docker-compose** (~13–15 containers Up ~5h): `verdify-traefik` (TLS `:443`, Host()-routing), `verdify-timescaledb` (pg16, bound `127.0.0.1:5432`), `verdify-mqtt` (`:1883`), grafana(+proxy+renderer), `verdify-site` (Quartz/nginx), umami, goaccess, promtail, and the profile-gated `hermes-iris` planner gateway (`127.0.0.1:8642`).
- **Scheduled work** split across systemd timers (forecast-page 30m, site-poll 10s, render-cache-warm) and `jason.crontab` (1AM pg_dump to `/mnt/iris/backups`, daily snapshots/vault writers, frigate, slack, metrics every 1m, daily-plan publish 3x/day).
- **Deploy method:** manual `git merge` to `live/platform-main` + `systemctl restart` / `docker compose up` against the `/srv/verdify -> /mnt/iris/verdify` symlink. **No CD, no audit trail** — this is the documented root cause of the 2026-04-21 MCP staleness incident.

### AFTER (target — `deploy/k8s/` on PR #55, design/validation only)

A single Kustomize base deployed by **ArgoCD** into one pinned namespace **`verdify-staging`** (PodSecurity `restricted`): Deployments for api/mcp/ingestor, a StatefulSet for db, and a PreSync migration Job, fed by a non-secret ConfigMap + a SOPS/age-delivered Secret. Every pod hardened (non-root, dropped caps, read-only rootfs, seccomp). Device safety is fenced in git: **ingestor pinned `replicas:0`** in staging, and the device-VLAN egress NetworkPolicy is a **commented placeholder**.

### Is there a live k3s cluster + ArgoCD?

**Not on this host — confirmed.** `vm-docker-iris` has no `kubectl`/`k3s`/`helm`/`argocd` binary, no `k3s.service`, no kubeconfig, no `:6443` listener. The full compose+systemd stack is live.

**Off-box, the picture is more advanced than the runbooks admit** (this is the central doc-vs-reality drift): the ArgoCD Application `verdify-local-staging` is **MERGED** (`jvallery/agents` PR #263, `selfHeal:true prune:false`, source = `jvallery/agent-fleet-control/manifests/verdify-staging`), the registry secret-meta PR #8 is **MERGED**, and the merged registry manifests' **header comments claim** the instance was "reconstructed from the live known-good verdify-staging cluster state," that the `.7.21` VIP "already serves 200 intra-cluster," and that the in-cluster DB "already carries the 208-table schema." **None of this is confirmable from `vm-docker-iris`** — the 5-node cluster is root's, reachable only from laptop-root. So: **staging is claimed-alive by merged artifacts and manifest comments; its current Synced/Healthy/pod state is unverified.** The two frozen runbooks (`@ f350bcd`) still say "nothing executed" and are a stale PREP snapshot.

---

## 3. What's working (confirmed-healthy)

**Track A (greenhouse loop) — confirmed by probe:**
- TimescaleDB healthy; `climate` (7s, 289,954 rows, 71.5°F/50% RH), `climate_action_log` (7s), `setpoint_changes` (~90s, 126,404), `equipment_state` (~2min), `setpoint_snapshot` (7s, 6.14M) all fresh.
- ingestor / mcp / setpoint-server / hermes-iris all active ~5h; ESP32 TCP-reachable.
- Planner firing normally (SUNRISE/SUNSET/MIDNIGHT/2× FORECAST_DEVIATION today, all delivered).
- **Zero open critical/high alerts** → OTA deploy gate clear.
- Public web tier serving live via local Traefik probes: `lab.verdify.ai` 200 (286 pages rebuilt 09:11 today), `api.verdify.ai` `/health` ok, `graphs.verdify.ai` 200. Today's plan and forecast pages are fresh.

**Track B (migration) — confirmed:**
- PR #55 is `MERGEABLE`/`CLEAN`; **all 8 `ci.yml` gate jobs + the `k8s-manifests` render/validate job pass** (kustomize v5.4.3 + kubeconform; 16/16 overlay, 15/15 base). **This refutes issue #22 for the branch** — CI does fire and pass on PR #55.
- Manifests are coherent and safety-conscious: single pinned namespace, fail-closed api writes (omits `VERDIFY_ALLOW_UNAUTHENTICATED_WRITES`), correct probe choices (tcpSocket for FastMCP, not httpGet), ingestor `Recreate`/`replicas:0`, full NetworkPolicy set, ArgoCD PreSync migrate Job.
- All build inputs exist on-branch (4 Dockerfiles, requirements, `catalog-info.yaml`); the `container-publish` / `k8s-manifests` workflows are fully authored and wired to the accessible reusable workflow.
- Companion GitOps PRs **MERGED** (the runbook said "open"): `jvallery/agents#263`, `jvallery/agent-fleet-control#8`.

---

## 4. What's broken / blocking (ordered by severity)

| # | Severity | Issue/PR | What | Evidence |
|---|---|---|---|---|
| 1 | **P0 HARD** | G1 (cutover-readiness) | **TimescaleDB version skew/downgrade**: live DB is `latest-pg16` (~2.25.2); k3s db-statefulset + migrate pin `2.17.2-pg16`. Unsupported downgrade — corrupts/refuses real restore. | manifest `db-statefulset.yaml`; live `timescale/timescaledb:latest-pg16` |
| 2 | **P0** | #26 / PR #55 | **`container-publish.yml` fails at workflow load** — 0s, zero jobs, no check-run on every PR #55 run. **No image has ever been published to GHCR**; the whole deploy tail is unproven. | `gh run list` 5 runs all `failure`/0s; check-runs show only the 9 passing jobs, no publish check-run |
| 3 | **P0** | #24, #25 | **Device-safety prereqs unimplemented on branch**: no ingestor `SHADOW_MODE`, no `ingestor-healthz.py`, no `scripts/lib/psql-verdify.sh`. Raw `docker exec ... psql` everywhere. | `git grep SHADOW_MODE` empty; both files "No such file or directory" |
| 4 | **P0** | #22 | **Branch drift**: `live/platform-main` (aa6518c) is 4 ahead of `main` (fb17f43); prod-config CI only fires on `main`; PR #55/#12 based on `main`. PRs misreport coverage vs where code ships. | `rev-list 0 4`; `ci.yml` triggers |
| 5 | P1 | DoD #1 | **No `/health/detailed` with baked `VERDIFY_GIT_SHA`** — image==source is not verifiable. | `api/main.py:911` has only `/health` |
| 6 | P1 | #43 | **`site_content` RAG table 8 days stale** (max `updated_at` 2026-05-23, 165 rows); no scheduled refresh for `populate-site-content.py`. Degrades Iris retrieval silently (public HTML is fresh, so easy to miss). | DB query; grep finds no timer/cron/Makefile ref |
| 7 | P1 | #42 | **weather_station (Tempest) ~8.7d stale**; **esp32_logs ~13.8d stale**. Tempest staleness risks pre-cool decisions before the Jun 4-9 heat cluster. Live climate/equipment telemetry unaffected. | `max(ts)` 2026-05-22 / 2026-05-17 |
| 8 | P1 | — | **Two host units FAILED**: `verdify-forecast-page` + `verdify-plan-publish` (outbound HTTPS timeout in `publish-site-content.sh`). Today's content still published via the 09:11 rebuild, but path-triggered republish won't auto-fire until reset. | `systemctl status`; urllib URLError |
| 9 | P2 | — | **Grafana render-cache-warm timer dead** since 2026-05-25 (HTTP 500s); iOS homepage panels may be cold. | journal 500s; timer disabled |
| 10 | P2 | — | **Two `verdify-api` instances**: container `:8080` (public, Traefik-routed) + legacy host systemd `:8300` (unrouted, redundant attack surface). | `ss -tlnp`; docker labels |

---

## 5. In progress / staged-not-cut-over

- **PR #55** (`firmware/cicd-golden-path` -> `main`, OPEN, CLEAN, 123 files, +41175/-733): the entire additive k3s package — `deploy/k8s/{base,overlays/local-staging,diagnostics}`, 4 Dockerfiles, `container-publish.yml`, `k8s-manifests.yml`. Explicitly **no cutover**, no firmware semantic edits. Carries a 2.54% intentional firmware replay-diff divergence (THRESHOLD_PCT override) needing coordinator + Iris concurrence.
- **Image pins are placeholders** (`newTag: latest`); real immutable `ghcr.io/jvallery/verdify-<comp>:sha-<gitsha>` pins come from a cross-repo PR in `jvallery/agents` not yet wired.
- **`request-gitops-promotion` is a deliberate safe no-op** — exits 0 when `AGENT_FLEET_PROJECT_TOKEN` is unset (it is). Even once publish is fixed, the cross-repo dispatch won't fire until the token lands.
- **ArgoCD `targetRevision: main`** while production lives on `live/platform-main` — reconcile-before-reconciliation would deploy an older tree.
- **migrate Job is a no-op** (`suspend:true` in the live-shape registry manifest; its image was a perpetual `ImagePullBackOff`); schema loaded out-of-band; real copy-not-move restore is a separate gated runbook.
- **`verdify-www` (Astro) and `verdify-planner`** are split into their own repos with CI/deploy, but **the monorepo still carries the planner code and the full Quartz `site/` tree** — unpruned drift.
- **No manifests for `verdify-site` / grafana / umami / goaccess** — the "move stateless services first" plan is undelivered for the web tier; api-only cutover would split the web stack across VM and k3s.

---

## 6. CI/CD & GitOps reality — intended path vs what executes

**Intended:** push to a prod branch -> GitHub Actions builds+publishes per-service images to GHCR -> cross-repo dispatch asks `jvallery/agents` to open an image-pin GitOps PR -> ArgoCD (sole applier) reconciles pinned SHAs into `verdify-staging`. GitHub Actions never touches the cluster.

**What executes today:**
- ✅ **Build-validate half works**: `ci.yml` 8 gates + `k8s-manifests` render/validate run green on PR #55.
- ❌ **Publish never succeeds**: `container-publish.yml` dies at workflow load (0 jobs). **No image artifact has ever been produced.**
- ❌ **Promotion/reconcile tail never executed end-to-end**: blocked first by the publish failure, then by the unset `AGENT_FLEET_PROJECT_TOKEN` no-op, then by `targetRevision=main` ≠ production branch.
- The Makefile has **no** k8s/container/argocd/deploy/publish targets — the only deploy target is `firmware-deploy` (OTA, heavily gated). Python-layer deploys remain **100% manual** (`git merge` + `systemctl restart`).

**Net: the commit->pod path is wired on paper and validated for manifests, but the build->publish->deploy spine has zero successful end-to-end runs.** Believing "CD is wired" is the trap — three silent gates (publish load-failure, token no-op, branch mismatch) each independently prevent a deploy.

---

## 7. Risks to the live greenhouse from the migration (Track-A protection)

1. **Two-writer ESP32 hazard (gravest).** Stage 5/6 (DB write-handoff + ingestor cutover) is the moment of max device risk. A momentary two-writer condition corrupts control. Today's only guard is the git-tracked `replicas:0` staging pin + `Recreate` + ArgoCD honoring it. **A manual scale to 1, or a stray RollingUpdate, would double-write.** Flipping to `replicas:1` is the single most dangerous action and is Jason-gated.
2. **Safety nets not built (#24/#25).** SHADOW_MODE, healthz, and `psql-verdify.sh` don't exist. Without the psql abstraction, a DB move under cutover breaks firmware-deploy preflight, replay export, and the sensor-health sweep **simultaneously**.
3. **TimescaleDB downgrade (G1).** Restoring the live DB onto a 2.17.2 statefulset would corrupt/refuse.
4. **DB SPOF regression.** Base statefulset uses `storageClassName: local-path` for the live 2.3GB DB — the design doc itself says **do not** put the live DB on local-path (worse SPOF than the VM; no replication, pins to one node). Prefer Longhorn or ExternalName.
5. **Device-VLAN reachability unproven at scale.** Host reaches ESP32 via UniFi inter-VLAN routing, not a local VLAN-10 NIC; the egress NetworkPolicy is commented-out and enabling it touches firewall posture (Jason-gated). A 2026-05-30 laptop-root spike hit the ESP32 at ~8ms, but whether that was from a real pod and whether the route is enabled is unconfirmed.
6. **Dead replan cron fallback.** `jason.crontab` line 26 (5-min replan trigger) is empty; replan currently rides solely on the ingestor emitting FORECAST_DEVIATION. A naive cutover that stops the ingestor without restoring the cron path would **silently stop deviation-driven replanning** — dangerous with the Jun 4-9 heat cluster approaching while Tempest data is stale.
7. **Stale rollback target.** `last-good` OTA is 2026-05-17 (#35 promotion to aa6518c pending bake, no archive artifact yet).

**The migration's Track-A protections are sound by design** (copy-not-move, single-writer, ingestor-last, replicas:0). The exposure is that the **code enforcing them isn't built yet**, so cutover is further out than PR #55's green checks imply.

---

## 8. Repo / issue hygiene

- **Org:** 8 repos under `VerdifyConsultancy`. `verdify-platform` (public monorepo) is source of truth. `verdify-planner` and `verdify-www` are real split-out repos — but the monorepo **still runs/contains** the planner code and Quartz tree (drift; same staleness class the migration exists to fix).
- **Branch drift (load-bearing):** `live/platform-main` (aa6518c, prod) is **4 ahead of `main`** (fb17f43). PR #55 and PR #12 are based on `main`. **PR #12 is a trap** — its content already shipped to prod as aa6518c, it shows OPEN against `main` with zero CI, and merging it could re-introduce/conflict. **Close it.**
- **Cross-org dependency:** the ArgoCD Application and image-promotion live in `jvallery/*` (laptop-root), outside the VerdifyConsultancy org and outside this repo's CI — the migration cannot complete without coordination across a boundary the in-org tracers can't see into. Source-of-truth for the deploy shape was deliberately **moved** to `jvallery/agent-fleet-control/manifests/verdify-staging` (to escape an ArgoCD ComparisonError), which means **merging PR #55 does NOT change what ArgoCD reconciles** — a subtle footgun.
- **EPIC #15 (k3s/CICD):** sub-issues #22–#33. Open P0s include #22 (CI triggers), #24 (psql abstraction), #25 (SHADOW_MODE), plus #17/#20/#31. #27 (cluster ns + ESP32 reachability + storage), #30 (in-cluster secrets), #35 (OTA promote), #42/#43 (monitoring/RAG) all OPEN. K3S-0 hard gate (ArgoCD present? StorageClass? MetalLB CIDRs? GHCR PAT?) cannot be confirmed from `vm-docker-iris`.
- Issue tracking itself is well-structured (42 open issues, P0–P3 + owner/epic/theme labels, 4 EPICs).

---

## 9. What to do next — cutover critical path

### P0 blockers (must clear before any cutover)
1. **Reconcile branch drift (#22).** Decide canonical branch, converge `main` ↔ `live/platform-main`, re-point PR #55 base and ArgoCD `targetRevision`, land the `ci.yml` trigger fix so production pushes are gated. **Close PR #12.**
2. **Fix G1 DB version skew.** Bump db-statefulset + migrate image to `>=2.25.2-pg16` to match live before any Stage 0 restore. Revisit `local-path` for the live DB (Longhorn/ExternalName).
3. **Make the pipeline actually publish.** Debug `container-publish.yml` load failure -> get one image into GHCR; configure `AGENT_FLEET_PROJECT_TOKEN`; add `/health/detailed` (baked `VERDIFY_GIT_SHA`) for DoD #1.
4. **Build the device-safety nets (#24, #25):** `psql-verdify.sh` (and migrate all call sites), ingestor `SHADOW_MODE`/`DRY_RUN`, `ingestor-healthz.py`. No ingestor cutover without these.

### P1 — establish ground truth & protect Track A
5. **From laptop-root, get real cluster state**: `kubectl get applications -n argocd verdify-local-staging`, `argocd app get`, `kubectl get pods -n verdify-staging`. Confirm Synced/Healthy and pod reality; **update the frozen runbooks to match** (they understate how live staging is).
6. **Track-A hygiene (independent of migration):** reset the failed forecast-page/plan-publish units; wire `populate-site-content.py` into a daily timer (#43); diagnose Tempest staleness (#42) **before Jun 4-9 heat (#36)**; restore the replan cron fallback or confirm the ingestor path is the intended sole trigger; promote/bake the aa6518c OTA (#35).
7. **Resolve ESP32_API_KEY drift** with Jason confirming canonical value (must not trigger re-flash) — gates secret sealing (5/13 keys clean).

### P2 — cleanup
8. Decommission redundant host `verdify-api.service` (`:8300`) after confirming no consumers; prune planner + Quartz trees from the monorepo; re-enable or retire the grafana render-cache-warm timer; fix the no-op `After=verdify-timescaledb.service` ordering deps (DB is a container).

---

_Confidence notes: all "live-prod" Track-A facts and the absence of k3s on `vm-docker-iris` are **confirmed by direct probe**. The `verdify-staging` instance being alive on root's cluster is **claimed by merged GitOps artifacts + manifest header comments only** — its current Synced/Healthy/pod state is **unverified** (cluster unreachable from the probed host). The `container-publish.yml` root cause is **inferred, not extracted from a log** (the run emits no annotations)._
