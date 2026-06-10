# Verdify CI/CD Golden-Path — Gated Handoff Status (2026-05-30)

**Authored by:** Verdify `firmware` agent (autonomous run).
**Authoritative plan:** `/mnt/agents/root/docs/verdify-cicd-refactor-handoff.md` (803 lines, P0–P9).
**Program doc (this run's design deliverable):** `docs/design/verdify-cicd-program.md` (591 lines).
**Ethos:** additive + reversible, Track A (greenhouse alive) > Track B (refactor), prove before
cutover. **Nothing in this run perturbed the live ESP32, the OTA path, the dispatcher, the DB
writers, the cluster, or any secret value.** No firmware was flashed. No `kubectl`/ArgoCD apply ran
(no cluster access on this VM). No secrets were sealed or read.

---

## (a) What is PR-ready + validated (per-PR, with gate results)

Three PRs were teed up. **None are merged** — all merges are a gated Jason/laptop-root/James step.

### PR 1 — App repo: `deploy/k8s` + Dockerfiles + CI workflows + program doc + catalog-info
- **Repo / direction:** PR **into `VerdifyConsultancy/verdify-platform`** (READ-ONLY org; James coordinates).
- **Branch:** `firmware/cicd-golden-path`, cut off the production tip `aa6518c`
  (= `origin/live/platform-main`). **NOT** committed to `live/platform-main` or `main`.
- **Title:** `CI/CD golden-path: deploy/k8s + Dockerfiles + CI workflows + program doc (additive, no cutover)`
- **Contents (23 files):**
  - `deploy/k8s/base/` (9): namespace, configmap, db-statefulset, migration-job, api-deployment,
    mcp-deployment, ingestor-deployment, networkpolicy, kustomization.
  - `deploy/k8s/overlays/local-staging/` (3): api-loadbalancer (MetalLB apps-pool patch),
    secrets.placeholder, kustomization.
  - Dockerfiles (3): `api/Dockerfile` (multi-stage hardened; replaced the 5-line throwaway),
    `mcp/Dockerfile` (wraps `mcp/server.py`, zero tool-semantic change), `ingestor/Dockerfile`
    (net-new device-loop worker image). Plus `.dockerignore` and 3 build-input requirements files
    (`api/requirements.txt`, `mcp/requirements.txt`, `ingestor/requirements-image.txt`).
  - `.github/workflows/`: `container-publish.yml` (new), `k8s-manifests.yml` (new),
    `ci.yml` (modified — issue #22 fix: added `live/platform-main` to push + pull_request triggers).
  - `catalog-info.yaml` (repo-root Backstage Component), `docs/design/verdify-cicd-program.md`.
- **Gate results (all green, run 2026-05-30):**
  | Gate | Command | Result |
  |---|---|---|
  | Lint (unit gate) | `make lint` (ruff) | **PASS** exit 0, "All checks passed!" |
  | Manifest gate (authoritative) | `kustomize build deploy/k8s/overlays/local-staging \| kubeconform -strict -ignore-missing-schemas -summary` | **PASS** 16/16 Valid, 0 Invalid, exit 0 (kustomize v5.4.3, kubeconform v0.6.7) |
  | Base build | `kustomize build deploy/k8s/base \| kubeconform …` | **PASS** 15/15 Valid, exit 0 |
  | Invariants (rendered) | namespace / strategy / LB / cilium greps | **PASS** 15/15 namespaced objects `verdify-staging`; ingestor `strategy: Recreate`; 0 cilium; 0 `spec.loadBalancerIP`; 2 metallb annotations |
  | Workflow YAML | `python3 yaml.safe_load` ×3 | **PASS** all parse |
  | Workflow lint | `actionlint` ×3 | **PASS** exit 0 |
  | Images build locally | `docker build` api/mcp/ingestor (prior P3 run) | **PASS** all 3 built clean, GIT_SHA baked, non-root uid 1000, import-closure resolves |
- **Reconciliation applied this run:** `container-publish.yml` referenced `api/Dockerfile.prod` /
  `mcp/Dockerfile.prod` (gravity `.prod` convention) but P3 shipped plain `api/Dockerfile` /
  `mcp/Dockerfile`. Pointed the two `dockerfile:` references at the real shipped filenames so the
  build jobs resolve. The files ARE the hardened multi-stage images; `.prod` was cosmetic. Flagged
  in-workflow + in the PR body for coordinator awareness.

### PR 2 — Registry secret-meta: `jvallery/agent-fleet-control`
- **URL:** https://github.com/jvallery/agent-fleet-control/pull/8 — **OPEN, MERGEABLE, not merged.**
- **Branch:** `verdify/app-cicd-contract` → `main`.
- **Contents:** 3 NON-secret managed-secret meta files (NO values):
  `registry/secrets/verdify-app-secrets.yaml`, `verdify-esp32-psk.yaml` (device-affecting, kept
  separate + flagged GATED), `verdify-ghcr-pull.yaml`. `target.namespace: verdify-staging`
  (byte-identical to the ArgoCD destination + base Namespace).
- **Gate results (all green):** `make validate` exit 0 (all 3 secrets PASS + full registry PASS +
  catalog FK PASS); `make verify-reproducible` exit 0 (103 generated files match); `make transform`
  produced zero changes to generated files; value-scan CLEAN (no data/stringData/value blocks).
- **Deferred (needs-operator, intentionally NOT in PR #8):** the apps-VLAN-7 IP reservation — the
  registry IPAM (`enrich_agents()`) forces `network.vlan=agents` + `.64.x`, so an apps `.7.x`
  reservation can't be a source-field edit; it lives in the app-repo deploy overlay (`192.168.7.21`
  placeholder in `api-loadbalancer.yaml`) and, if also recorded in the registry, needs a schema +
  transform change (operator-gated).

### PR 3 — ArgoCD Application + secret-sync arm: `jvallery/agents`
- **URL:** https://github.com/jvallery/agents/pull/263 — **OPEN, MERGEABLE, not merged.**
- **Branch:** `verdify/argocd-app` → `main`.
- **Contents:** `platform/gitops/applications/local-staging/verdify.yaml` (Application
  `verdify-local-staging`, project `app-test`, `source.repoURL` = verdify-platform,
  `targetRevision: main`, `path: deploy/k8s/overlays/local-staging`, pinned ghcr SHA placeholders,
  `destination.namespace: verdify-staging`, `prune:false selfHeal:true`, `CreateNamespace=false`);
  a NEW `verdify-staging` arm in `local-k8s-secret-sync.yml`.
- **Gate results:** YAML parse PASS ×3; `kubeconform -strict -ignore-missing-schemas` PASS (1
  resource Skipped — Application is a CRD with no schema, expected, same as the live gravity/vast
  Applications); namespace byte-identity check PASS.
- **Stale-exemplar note:** the handoff's named `gravity.yaml` exemplar no longer exists in
  `jvallery/agents`; the live `vast-cloud-tco.yaml` shape was copied instead.
- **catalog-info.yaml** was written to the firmware worktree (now staged in PR 1).

### Validation matrix (consolidated)

| PR | Gate | Command | Result |
|---|---|---|---|
| 1 (app) | lint | `make lint` | exit 0 |
| 1 (app) | manifests | `kustomize build …/overlays/local-staging \| kubeconform -strict -ignore-missing-schemas` | 16/16 Valid, exit 0 |
| 1 (app) | manifests (base) | `kustomize build deploy/k8s/base \| kubeconform …` | 15/15 Valid, exit 0 |
| 1 (app) | workflows | `actionlint` ×3 | exit 0 |
| 1 (app) | images | `docker build` ×3 (P3) | all built, GIT_SHA baked |
| 2 (registry) | schema | `make validate` | exit 0 |
| 2 (registry) | reproducible | `make verify-reproducible` | exit 0 |
| 3 (argocd) | manifest | `kubeconform -strict -ignore-missing-schemas verdify.yaml` | exit 0 (1 Skipped CRD) |
| 3 (argocd) | namespace | byte-identity vs secret-sync arm | PASS |

---

## (b) GATED Jason / laptop-root / James TODO — IN ORDER

Each item below was NOT done this run (gates not crossed / no cluster access). Do them in order.

1. **Reconcile the `live/platform-main` ↔ `main` (↔ `platform-main`) branch drift [GATE: Jason/James].**
   Verified on-box AND on the remote 2026-05-30: production branch is **`live/platform-main`**, which
   is **4 ahead of `main`, 0 behind** (`git rev-list --left-right --count
   origin/main...origin/live/platform-main` → `0  4`). The handoff's literal `origin/live` is a 404
   (`gh api .../branches/live` → "Branch not found"); the `platform-main` token IS part of the ref.
   HEAD `aa6518c` == the production tip. **Do NOT autonomously rename/merge** — reconcile via a PR
   into `VerdifyConsultancy/verdify-platform`, James coordinating. After reconciliation, the CI
   triggers can be trimmed from `[main, live/platform-main]` to the single canonical branch.

2. **Review + merge the 3 PRs (in dependency order).** [GATE: respective owners]
   - **PR 2 registry** (#8, agent-fleet-control) — laptop-root reviews. Source-of-truth contract;
     land first so the namespace/secret names are canonical.
   - **PR 1 app** (firmware/cicd-golden-path → verdify-platform) — **James coordinates**; `.github/**`
     is shared infra, so coordinator reviews the workflow changes. RUNTIME-FAILURE-UNTIL-MERGE-CHAIN:
     the api/mcp/ingestor build jobs reference `jvallery/agents/.github/.../reusable-container-build.yml@main`
     and dispatch a `jvallery/agents` promotion workflow that does not exist yet (safe no-op until the
     token `AGENT_FLEET_PROJECT_TOKEN` is set). On PRs the build jobs build-without-publish.
   - **PR 3 argocd** (#263, jvallery/agents) — laptop-root reviews (wires into the live cluster).

3. **§3.4 DEVICE-VLAN reachability spike [GATE: Jason — touches firewall/router posture].**
   Prove a k3s pod reaches ESP32 `192.168.10.111:6053`, HA `192.168.30.107`, local MQTT, and Frigate
   `192.168.30.142` within the 5–10s occupancy→light SLA (baseline p50 37s / p95 81s band-change,
   ~95% confirm). Until proven, the device-touching **ingestor stays VM-side** (the recommended
   partial-migration boundary). The egress NetworkPolicy for `192.168.10.0/24` + HA/MQTT/Frigate is a
   COMMENTED `gated-§3.4` placeholder in `deploy/k8s/base/networkpolicy.yaml`, intentionally disabled.
   No routing/firewall change was made. Tempest UDP broadcast terminates at the ESP32 (L2-local, out
   of the pod path) — confirm it is unaffected, do not relay it.

4. **Seal the secrets via `seal-secret.sh` on the self-hosted runner [GATE: laptop-root; device creds GATE: Jason].**
   `scripts/seal-secret.sh <id> --remote jason@vm-docker-iris.servers.vallery.net` (confirm the VM
   hostname is reachable first; `vm-verdify` is NXDOMAIN). Seal `verdify-app-secrets` and
   `verdify-ghcr-pull`. **`verdify-esp32-psk` (ESP32 Noise PSK) is device-affecting** — confirm
   rotate-at-seal vs carry-existing with Jason; never trigger a re-flash as a side effect. The ESP32
   OTA password is in the same device-affecting class.

5. **Create the `verdify-staging` namespace [GATE: Jason/laptop-root].** `CreateNamespace=false` on
   the Application, so the namespace must pre-exist. Must be byte-identical `verdify-staging`
   everywhere (base Namespace object, ArgoCD destination, secret target, secret-sync arm) to avoid the
   gravity 3-way namespace-mismatch / silent-secret-non-mount gotcha.

6. **Run `local-k8s-secret-sync` for `verdify-staging` [GATE: laptop-root, protected runner].** The
   PR #263 arm extends the validate-request contract only; the sync job is still hardwired to VAST
   secret keys + `SECRET_SYNC_ALLOWED_NAMESPACES=vast-cloud-tco-dev` — wire the real Verdify keys +
   allowed-namespace before first sync. Confirm with laptop-root.

7. **ArgoCD reconcile — the deploy into k3s [laptop-root].** Once PRs merged + namespace + secrets
   present, ArgoCD selfHeals the manifests to k3s. Pods should come up Healthy against an empty DB
   first. Rollback = revert the image pin / delete the Application (k3s-side only, VM untouched).

8. **TimescaleDB copy-not-move migration + verify [GATE: Jason — quiescence window].** Consistent
   `pg_dump -Fc` → fresh StatefulSet → idempotent migration Job restore → assert row counts +
   hypertables + compression policies + migration version parity. The migration Job in this PR is a
   `[true]` no-op placeholder; the real restore runner is a separate gated step. Copy, never move; NAS
   gets dumps, never live DB files. Both stacks must NOT write the same DB.

9. **Prove device reachability for real (re-run #3 spike post-deploy) + record the partial-migration boundary decision.**

10. **First live setpoint push from a k3s pod [GATE: Jason — device-affecting].** The moment a pod
    first writes a real setpoint to the ESP32. Single-writer invariant (replicas:1, Recreate) must
    hold; only one process may own the ESP32 native-API connection. Confirm before this push.

11. **Service-by-service cutover [GATE: Jason — each stop confirmed].** Only when the §5 DoD holds,
    stop migrated VM services one at a time (never the VM; no `down -v`; soak/rollback window). The
    device-touching ingestor is stopped LAST, and only if §3.4 fully cleared — otherwise it stays.
    Firmware OTA path is NEVER part of cutover.

---

## (c) Definition of Done (11 items) — current state

| # | DoD item | State |
|---|---|---|
| 1 | Repo-driven CI/CD: push → tests → `ghcr.io/<owner>/verdify-<comp>:sha-<gitsha>`, GIT_SHA baked, surfaced at `/health/detailed` | **PARTIAL.** Workflows + Dockerfiles PR-ready (GIT_SHA baked in image env). `/health/detailed` endpoint NOT added — needs an api/mcp scope-owner code change (proposed, not edited). Publish fires only after PR 1 merge + jvallery promotion wiring. |
| 2 | ArgoCD Application pins SHAs + selfHeals | **PR-READY** (PR #263, not merged; reconcile not yet run). |
| 3 | Backstage catalog + live status | **PARTIAL.** `catalog-info.yaml` PR-ready (PR 1) + registry render PR-ready (PR #8). Live portal status appears only post-deploy. |
| 4 | Gates green (registry validate + verify-reproducible; app test/lint + kubeconform) | **DONE for the artifacts.** All gates exit 0 this run (see matrix). |
| 5 | APP on apps VLAN 7 (MetalLB apps-pool reserved .7.x, Traefik+Authentik, ClusterIP-private rest, NetworkPolicy default-deny) | **DESIGNED / PR-READY.** LB patch + NetworkPolicy in PR 1; `.7.21` is a PLACEHOLDER to reserve/confirm; Traefik+Authentik ingress + identity-header stripping NOT yet wired (post-deploy). |
| 6 | Dispatcher operational (single-replica Deployment OR recorded VM-side decision; real setpoints; baseline confirm/latency; single-writer held) | **DESIGNED.** Deployment replicas:1 Recreate PR-ready; recommended decision = ingestor stays VM-side until §3.4 spike clears. No real setpoint pushed (GATED #10). |
| 7 | Firmware pipeline intact (CI builds+validates artifacts, NEVER flashes; `make firmware-deploy` unchanged) | **DONE.** `container-publish.yml` publishes NO firmware image, never flashes/OTAs; `firmware/*` is doc-only in the change-impact resolver; existing ci.yml firmware gate jobs untouched. |
| 8 | Local device networking proven from k3s OR recorded VM-side decision | **NOT DONE — GATED SPIKE (#3).** Designed (commented egress placeholder); not implemented; not proven. |
| 9 | Secrets SOPS-sealed, no plaintext .env in runtime contract; ESP32 PSK/OTA per Jason | **PARTIAL.** Secret-meta PR-ready (PR #8, NO values); sealing is GATED (#4); ESP32 PSK device-affecting. |
| 10 | VSCode-remote dev in k3s (`placement.mode: pod`, Retain PVC) | **NOT STARTED** (P8, out of this run's scope). |
| 11 | Source decommissioned only when green+proven; TimescaleDB migrated+verified copy-not-move | **NOT DONE — GATED (#8, #11).** VM stack fully intact and authoritative; nothing stopped. |

---

## (d) Map to the GitHub issues

| Issue | Title | This run |
|---|---|---|
| **#15** | EPIC: k3s + ArgoCD migration & CI/CD (GitOps for the Python layer) | Umbrella — this run delivers the additive PR-ready slice (P0–P6 design + artifacts). |
| **#22** | Wire GitHub Actions CI to run on PRs and pushes (8 jobs, zero PR runs) | **FIX in PR 1:** `ci.yml` triggers gained `live/platform-main` (root cause: production lands on `live/platform-main`, triggers only named `main`, so production pushes + PRs targeting it never fired the 8 gate jobs). |
| #25 | K3S-2: ingestor SHADOW_MODE + ingestor-healthz for safe parallel-run | Related to §3.3/§3.4 + DoD #6; not in this run (code change). |
| #26 | Wire GitHub Actions CD: release.yml build/push/tag | Addressed by `container-publish.yml` in PR 1 (P4). |
| #27 | k3s sub-area: namespace + networking hard gate (ESP32 :6053 reachability) + storage | The §3.4 GATED SPIKE (#3 above) + namespace (#5) + storage (db-statefulset/local-path Retain in PR 1). |
| #28 | k3s sub-area: TimescaleDB cutover | DoD #11 / GATED (#8 above); db-statefulset PR-ready, restore is a separate gated runbook. |
| #29 | k3s sub-area: containerize the Python services | The 3 Dockerfiles in PR 1 (P3). |
| #30 | Secrets-out-of-.env, re-scoped to k3s in-cluster secrets | Secret-meta PR #8 (P2); sealing GATED (#4 above). |

---

## Status summary

**DONE (additive artifacts, PR-ready + validated):** 3 PRs teed up — app PR (branch
`firmware/cicd-golden-path` → verdify-platform), registry PR #8, ArgoCD PR #263. All gates green:
`make lint` exit 0, `kustomize build …/local-staging | kubeconform -strict -ignore-missing-schemas`
16/16 Valid exit 0, `make validate` + `make verify-reproducible` exit 0, actionlint exit 0, all 3
images build locally with GIT_SHA baked.

**GATED HANDOFF (NOT done this run — no cluster access, gates not crossed):** branch-drift
reconciliation; the 3 PR merges; the §3.4 device-VLAN reachability spike; secret sealing (esp. the
device-affecting ESP32 PSK/OTA); namespace creation; secret-sync; the ArgoCD reconcile (deploy into
k3s); the TimescaleDB copy-not-move + verify; the first live setpoint from a pod; the service-by-
service cutover.

**Explicit:** deployment into k3s was NOT performed in this run. It requires the gated
laptop-root/Jason steps above. No cluster apply, no secret seal/read, no device touch, no firmware
flash, no live-service stop occurred. Track A (greenhouse alive) was never at risk.
