# Verdify CI/CD Self-Refactor Program (Golden-Path)

> **SUPERSEDED 2026-07-11 — HISTORICAL RECORD ONLY.** This document plans a GitHub
> Actions pipeline (`container-publish.yml`, the `jvallery/agents` reusable
> container-build workflow, `ghcr.io/jvallery/verdify-<comp>` tags). **None of it
> exists.** All eight workflow files were deleted in `6c7abe14` and
> `.github/workflows/` is now deliberately empty and test-guarded. Publishing
> moved to the in-cluster zot origin (`registry.vallery.net`) per ADR-0021, and
> the merge gate is the commit status `Verdify Platform / Argo PR CI` produced by
> the `verdify-platform-pr-ci` Argo Events sensor. It also predates the
> 2026-06-10/06-16 single-branch simplification, so its `live/platform-main`
> ground-truth section describes a retired branch.
>
> **For the current pipeline read `docs/ci/zero-paid-runner-ledger.md`,
> `docs/runbooks/prod-promotion.md`, and `ARGOCD.md`.** Do not implement anything
> below.

**Status:** DESIGN ONLY — no cluster created, no images pushed, no production changed, no
device/secret/firmware touched. This is the P1 deliverable of the CI/CD golden-path
self-refactor, the Verdify-specific equivalent of the fleet's `gravity-k3s-program.md`.
**Authoritative input:** `/mnt/agents/root/docs/verdify-cicd-refactor-handoff.md` (P0–P9).
**Author:** firmware agent (planning, on `firmware/*`). **Date:** 2026-05-30.
**Repo:** `VerdifyConsultancy/verdify-platform` (worktree `/mnt/iris/verdify-worktrees/firmware`).

> **Single rule above everything:** **Track A (the greenhouse stays alive) > Track B (this
> refactor), always.** Plants are alive; the ESP32 is in a 5–10s loop. If any step could
> perturb the live ESP32, the firmware OTA path, the dispatcher, or the DB writers — **STOP
> and confirm with Jason.** Every step here is **additive + reversible**: k3s stands up
> *alongside* the running VM; the VM stays authoritative until a gated, per-service cutover.
> Companion doc `docs/design/k3s-argocd-migration.md` is the prior repo-grounded planning
> pass; this doc is the gravity-shaped program that supersedes its structure. Every
> device / secret / cluster step below is explicitly marked **[GATE: Jason]** or
> **[GATE: laptop-root]** and is teed up, **not executed**, by this program.

---

## 1. Ground truth

### 1.1 The live↔main branch drift (P0 — reconcile, do NOT auto-fix)

The authoritative handoff's two explicit ground-truth claims about the production branch
**name are FALSE on this box and on the GitHub remote** — flag for Jason/James as a P0
reconciliation **before** any P2+ work. The drift *count* matches; the *name* does not.

| Handoff says | Verified actual (on-box + `gh api`) |
|---|---|
| production branch is `origin/live` | production branch is **`origin/live/platform-main`**; `git rev-parse origin/live` → fatal, no such ref; `gh api .../branches/live` → **404** |
| "there is no `platform-main` ref" | the `platform-main` token **IS** the ref name |
| 4 ahead / 0 behind `main` | **confirmed:** `git rev-list --left-right --count origin/main...origin/live/platform-main` → `0  4` |

- The GitHub remote has exactly **three** branches: `main` (default), `live/platform-main`,
  `firmware/vanda-band-compliance-rearch`. No bare `live` branch exists anywhere.
- `/srv/verdify` → symlink → `/mnt/iris/verdify` (coordinator worktree) is checked out on
  **`live/platform-main` @ aa6518c**. **`git checkout main` does NOT reproduce production.**
- The 4 live-only commits are the Vanda work: `aa6518c` (sprint-3 close-out), `f2bad50`
  (sprint-2 IRR-3/4 misting), `e7781a3` (band/compliance rearch + companion firmware OTA
  bundle), `9b7eb80` (state audit + designs). **Three of four touch firmware/safety**, so
  any reconciliation PR carries the full firmware artifact set (replay-diff THRESHOLD_PCT=0,
  16 invariants, test-firmware delta) + coordinator(iris-dev) + Iris concurrence.
- **`firmware/vanda-band-compliance-rearch` points at the same SHA as `live/platform-main`
  (aa6518c)** — this worktree starts from production state, not from `main`.

**RECONCILIATION ACTION [GATE: Jason/James]:** before designing against `main`, (a) confirm
the canonical production branch name is `live/platform-main`, and (b) globally correct every
`origin/live` / `vm-verdify` reference in the handoff and downstream scripts (seal-secret
`--remote`, ArgoCD `targetRevision`, `git rev-list ...origin/live`) to `live/platform-main`,
or they silently resolve to nothing. **Do NOT rename or merge branches autonomously** —
`VerdifyConsultancy` is read-only, PR-only.

### 1.2 The current deploy reality (the gap this kills)

There is **no CD for the Python layer.** Three host systemd units run code straight off a
shared `/srv/greenhouse/.venv` via a symlink, plus a compose stack:

- **Host systemd (NOT in compose):** `verdify-api.service` (uvicorn `main:app --host
  0.0.0.0 --port 8300`), `verdify-mcp.service` (`python mcp/server.py`),
  `verdify-ingestor.service` (`python ingestor.py`, with an `ExecStartPre` pkill guard).
- **Compose stack** (`docker-compose.yml`): `traefik`, `timescaledb`
  (`timescale/timescaledb:latest-pg16`, bound `127.0.0.1:5432`), `grafana`(+renderer+proxy),
  `mqtt`, `api` (a *second*, container runtime, uvicorn `:8080`, Traefik `api.verdify.ai`),
  `verdify-site` (nginx), `umami`(+db), `goaccess`(+site), `promtail`, `hermes-iris`
  (profile-gated, `127.0.0.1:8642`, reaches host MCP via `host.docker.internal`).
- **Deploy today = `git merge to live/platform-main` + manual `systemctl restart` /
  `docker compose up -d`.** No image build, no audit trail. `.github/workflows/` has **only
  `ci.yml`** (8 gate jobs: lint, site-guards, schemas, firmware, firmware-logic,
  firmware-replay-diff, no-new-fire-and-forget, service-restart-drift-guard) — **zero
  build/push/deploy jobs**. This staleness class caused the 2026-04-21 MCP incident.
- **No `deploy/k8s/` tree, no repo-root `catalog-info.yaml`** exist yet — the refactor is
  **net-new containerization/GitOps wiring**, not a lift-and-shift.

### 1.3 Secrets inventory (path/name/mode only — NO VALUES READ)

| Path | Owner | Mode | Note |
|---|---|---|---|
| `/srv/verdify/.env` (→ `/mnt/iris/verdify/.env`) | jason:users | `-rw-------` | DB pass, Grafana admin, contact SMTP |
| `/srv/verdify/ingestor/.env` | jason:jason | `-rw-------` | `ESP32_API_KEY`, DB creds |
| `/srv/verdify/api/.env` | jason:users | `-rw-------` | `VERDIFY_WRITE_API_KEY`, SMTP |
| `/srv/greenhouse/esphome/secrets.yaml` | jason:users | `-rw-rw----` | **ESP32 Noise PSK / OTA password — DEVICE-AFFECTING** |
| `/srv/greenhouse/.env` | — | absent | — |

Credential **files** (not in `.env`): HA token `/mnt/agents/shared/credentials/ha_token.txt`,
Gemini `.../gemini_api_key.txt`, OpenAI `.../openai_api_key.txt`. Stale backups
`ingestor/.env.bak-*` exist in the coordinator worktree (flag to coordinator, out of scope).
**Caveat:** the ESP32 Noise PSK (`esp32_api_key`) is read from **both** env **and** the DB
`greenhouses` table (DB overrides env, `ingestor.py:1987-1993`); sealing/rotating it is
device-affecting **[GATE: Jason]** — confirm rotate-at-seal vs carry-existing; never trigger
a re-flash as a side effect.

### 1.4 Stale-reference corrections to the handoff (verified via `gh`)

The handoff names several gravity artifacts that **no longer exist live**; use these:

| Handoff reference (stale) | Use instead (verified present) |
|---|---|
| `applications/local-staging/gravity.yaml` | `applications/local-staging/vast-cloud-tco.yaml` (+ `local-dev/`) |
| `registry/secrets/gravity-app-secrets.yaml` | `registry/secrets/backstage-secrets.yaml` (secret-meta template) |
| `scripts/update-gravity-gitops-images.rb` | generic `scripts/update-gitops-application-image.rb` |
| promotion `gravity-gitops-dev-test-promotion.yml` | `vast-gitops-dev-test-promotion.yml` |
| `local-k8s-secret-sync.yml` enum has gravity | enum has **only** `vast-cloud-tco-dev`; add a NET-NEW `case` arm |

The gravity **`deploy/k8s/` tree still exists** and remains the richest kustomize file-shape
exemplar; copy *file shapes* from gravity, *live wiring shapes* from vast-cloud-tco.

---

## 2. Target architecture

### 2.1 The golden path (mirror gravity/vast exactly)

```
registry (jvallery/agent-fleet-control)        ← source of truth, PR-gated
   make validate && make verify-reproducible    (both exit 0)
        │
        ▼  (push to app repo main)
GitHub Actions (verdify-platform)
   container-publish.yml  →  uses jvallery/agents/.github/workflows/reusable-container-build.yml@main
        --build-arg GIT_SHA=${{ github.sha }}
        →  ghcr.io/<owner>/verdify-<comp>:sha-<12char>   (+ :latest, GIT_SHA baked)
   k8s-manifests.yml  →  kustomize v5.4.3 | kubeconform -strict -ignore-missing-schemas
        │
        ▼  (cross-repo image-pin PR)
ArgoCD Application (jvallery/agents/platform/gitops/applications/local-staging/verdify.yaml)
   source.kustomize.images pins ghcr sha-<12char>;  prune:false  selfHeal:true
        │
        ▼  (ArgoCD ns argocd reconciles)
k3s (flannel CNI, MetalLB v0.15.3 L2 — NOT Cilium)
```

`image == source` is verifiable at a `/health/detailed` endpoint that returns the baked
`VERDIFY_GIT_SHA`. Rollback = revert the image-pin commit (ArgoCD re-syncs) or
`kubectl rollout undo`. Backstage catalog renders from the registry + a repo-root
`catalog-info.yaml`.

### 2.2 Two planes — APP (VLAN 7) vs DEV-AGENT (VLAN 64)

Never conflate them; they land on different VLANs with different blast radius.

- **APP plane (VLAN 7, apps-pool `192.168.7.10-.250`, MetalLB L2 `vlan7`, autoAssign:false):**
  the runtime services people/devices talk to — `api` (FastAPI public), `mcp` (planner tool
  surface, ClusterIP), `ingestor`+dispatcher (device-touching — see §6), `timescaledb`
  (system of record), grafana/traefik/mosquitto/site/umami. Only the **user-facing surface**
  (api/site) gets an apps-pool LoadBalancer IP behind Traefik + Authentik; everything else is
  **ClusterIP**. The Verdify APP would be the **first** registry entry to use `network.vlan:
  apps` (gravity reserves its `.7.20` only in its repo overlay, not the registry).
- **DEV-AGENT plane (VLAN 64, agents-pool `192.168.64.10-95.254`):** the dev pods. Registry
  already reserves `verdify-saas` (owner james, `.64.36`) and `verdify-ingestor` (owner
  james, `.64.32` — **NOT `.33`** as the handoff says), both `placement.mode: vm` today.
  Target: flip to `pod`, develop via VSCode-Remote-SSH over in-pod sshd `:2222` on the
  agents-pool LB. (Handoff §0 also names `verdify-irrigation` `.64.33` — reconcile the
  actual registry entry names with Jason; the registry observed is `verdify-ingestor .64.32`.)

### 2.3 Namespace discipline

One environment string, byte-identical in **three** places (gravity's bug was a 3-way
mismatch): base `Namespace` object == ArgoCD `destination.namespace` == every
`registry/secrets/<id>.yaml` `target.namespace`. **Pin `verdify-staging`** everywhere
(matching the `<app>-staging` convention). A sealed Secret applied to the wrong namespace
silently never mounts.

### 2.4 Networking constraints (carry forward)

- Use `metallb.universe.tf/*` annotations only. **Do NOT use Cilium `lbipam.cilium.io/*`** —
  the live cluster does not honor them.
- **Never set both `spec.loadBalancerIP` AND the MetalLB annotation** → `AllocationFailed`.
- apps-pool `autoAssign:false`, so the `metallb.universe.tf/loadBalancerIPs: 192.168.7.<x>`
  annotation is **required**; `externalTrafficPolicy: Local`. The reserved `.7.x` is a
  laptop-root/Jason networking surface **[GATE: laptop-root]** — propose an IP inside
  `.7.10-.250` (not `.7.20`, gravity's repo-side value), confirm, then add to the registry.

---

## 3. CI/CD wiring (the workflows to add)

All workflow/Dockerfile/deploy edits are **PRs into `VerdifyConsultancy/verdify-platform`**
(James coordinates); the ArgoCD Application + registry are **PRs into the jvallery side**
(laptop-root reviews). Nothing merges autonomously.

### 3.1 `container-publish.yml` (app repo) — net-new

- Triggers on push / PR / workflow_dispatch to `main` (and, per handoff DoD/branch
  reconciliation, the production branch once renamed/confirmed).
- An **image-impact job** diffs changed files to decide which contexts rebuild (gravity uses
  `scripts/resolve-deploy-impact.rb`).
- **One job per image context** (`verdify-api`, `verdify-mcp`, `verdify-ingestor`) that
  **calls the shared reusable workflow**:
  `uses: jvallery/agents/.github/workflows/reusable-container-build.yml@main` with inputs
  `context` / `dockerfile` / `image-name` / `push:true` / `publish-branches:main` /
  `build-args: GIT_SHA=${{ github.sha }}`.
- A final **`request-gitops-promotion` job** (only on push to main + image_publish) computes
  `ghcr.io/jvallery/verdify-<comp>:sha-${GITHUB_SHA:0:12}` and `gh workflow run`-dispatches
  the promotion workflow in `jvallery/agents`, authenticating with
  `GH_TOKEN: ${{ secrets.AGENT_FLEET_PROJECT_TOKEN }}`.
- **PRs build-without-publish** (the reusable workflow's `push_allowed` gate is
  `PUSH_REQUESTED=true && event != pull_request && branch in publish-branches`). Mandatory
  tag is `sha-<12char>`.
- **Keep `ci.yml` as the unit gate** (lint/test/firmware/replay/drift-guards). Issue #22:
  CI currently does run on PRs via `ci.yml`'s `pull_request` trigger, but there is no
  build/deploy job — that is what this adds.

### 3.2 `k8s-manifests.yml` (app repo) — net-new

- On push/PR to `main` scoped to `deploy/k8s/**`. Installs **kustomize PINNED v5.4.3** (the
  base `labels:` transformer requires v5.x) + kubeconform v0.6.7.
- Runs `kustomize build deploy/k8s/base | kubeconform -strict -summary
  -ignore-missing-schemas` and the same over each `deploy/k8s/overlays/*/`. Broken manifests
  never reach ArgoCD.

### 3.3 Image-pin promotion (jvallery side)

- Verdify's promotion models on `vast-gitops-dev-test-promotion.yml`: it git-switches to a
  `gitops/verdify-local-staging` branch, runs
  `ruby scripts/update-gitops-application-image.rb --file <verdify app yaml> --source-revision
  <sha> --image-tag <ghcr tag> --environment local-staging --source-repo <url> --write`,
  commits, force-with-lease pushes, and `gh pr create`s. Auth is the workflow's own
  `github.token` (`GH_TOKEN: ${{ github.token }}`), **not** a named PAT.

### 3.4 Dockerfiles (mirror gravity `api/Dockerfile.prod`)

Multi-stage `python:3.12-slim` (matches `requires-python>=3.12`; gravity uses 3.12 too):
builder `pip install --prefix=/install`; final stage `COPY --from=builder /install
/usr/local` + the component dir + **`COPY verdify_schemas/`** (+ `slack_config.py`/`slack_ops`
for mcp & ingestor — they import from repo root). `useradd -m -u 1000 -s /usr/sbin/nologin
appuser`; `USER appuser`; `ARG GIT_SHA=unknown` → `ENV VERDIFY_GIT_SHA=$GIT_SHA`; `LABEL
org.opencontainers.image.revision=$GIT_SHA`; `HEALTHCHECK curl /health`. Per component:

- **verdify-api:** `CMD uvicorn main:app --host 0.0.0.0 --port 8080` (0.0.0.0 *inside* the
  container is fine — the fix is being ClusterIP, not a host bind). Minimal FastAPI image,
  **no agent toolchain** (gravity's image-split fix). Needs `curl` for the healthcheck.
  **Build gotcha:** the existing `api/Dockerfile` is a 5-line throwaway (`COPY main.py`) that
  would fail the `from verdify_schemas import ...` (`api/main.py:62`); the real image MUST
  copy the package and `pip install ".[api]"` from repo-root context (`verdify_schemas` is
  not a standalone distribution).
- **verdify-mcp:** `CMD python mcp/server.py` with `MCP_HTTP_HOST=0.0.0.0` /
  `MCP_HTTP_PORT` via ConfigMap. ClusterIP only, non-root, **auth fails CLOSED** (today
  there is NO transport auth — DANGER surface; see §4/§6). Wrap/containerize **WITHOUT
  changing tool semantics**. Note port drift: docstring says 8400, code default is 8000.
- **verdify-ingestor:** `CMD python ingestor.py`. **No inbound port** (connect-out worker);
  liveness via a process/heartbeat probe, not httpGet. **Dependency gap:** `paho-mqtt` is
  imported (`ingestor.py:30`) but is **NOT** in `pyproject.toml` or `ingestor/requirements.txt`
  (which lists only aioesphomeapi/asyncpg/python-dotenv); the image requirements must be
  reconstructed from actual imports (+ pydantic, httpx, paho-mqtt, repo-root modules) or the
  container crashes at import.

The reusable workflow passes `--build-arg GIT_SHA=${{ github.sha }}`; api surfaces it at
`/health/detailed` (today api has only a basic `/health` at `api/main.py:911` — adding the
detailed endpoint is the #1 cross-cutting requirement; **coordinate with the api scope
owner**, do not change tool semantics).

---

## 4. Security / SOPS

### 4.1 The secret-meta set (one `registry/secrets/<id>.yaml` each, NO values)

Copy `registry/secrets/backstage-secrets.yaml` (the real template; `gravity-app-secrets.yaml`
does not exist). Schema `secret.schema.json` requires `id`, `display_name`, `owner`,
`encrypted_payload{path,recipient}`, `target{kind,namespace,name,secret_type,keys[]}`;
`additionalProperties:false` everywhere (a value cannot be added by mistake). `recipient`
MUST equal the `.sops.yaml` age public key
`age1jd6c7lm7vhj56gve6dvj59mepwpukhnyyh8wyca9y7mrjfeyqs8qjvqd5k`. File stem == `id` == encrypted
payload stem (`secrets/encrypted/<id>.enc.yaml`). `source.format: dotenv`, `nas_path` = the
live VM `.env`. `target.namespace = verdify-staging` (single DNS label) in **every** file.

| Secret id | k8s keys (from source) | Notes |
|---|---|---|
| `verdify-db` | `POSTGRES_PASSWORD`, user/db | from `/srv/verdify/.env` |
| `verdify-api` | `VERDIFY_WRITE_API_KEY`, `VERDIFY_CONTACT_SMTP_*` | from `api/.env` |
| `verdify-ingestor` | `ESP32_API_KEY` (Noise PSK — **device-affecting [GATE: Jason]**), DB creds, `MQTT_PASS` | from `ingestor/.env` |
| `verdify-grafana` | `GRAFANA_ADMIN_PASSWORD` | from `/srv/verdify/.env` |
| `verdify-umami` | `UMAMI_DB_PASSWORD`, `UMAMI_APP_SECRET` | compose env |
| `verdify-hermes` | OpenAI key, Slack, `HERMES_IRIS_API_KEY` | files + env |
| `verdify-ha-token` | HA token (mount at the **same path** so `open(HA_TOKEN_FILE)` works) | `ha_token.txt` |
| `ghcr-pull` | `.dockerconfigjson` (read scope) | image-pull secret |

### 4.2 Delivery + namespace-match discipline

Sealing: `scripts/seal-secret.sh <id> --remote jason@vm-docker-iris.servers.vallery.net`
(the live VM — **NOT `vm-verdify`**, which is NXDOMAIN; confirm reachability first) pipes
plaintext over ssh stdin into `sops --encrypt` (only ciphertext lands; `.sops.yaml`
`encrypted_regex '^(data|stringData)$'` keeps the Secret skeleton readable). Delivery is the
protected workflow `jvallery/agents/.github/workflows/local-k8s-secret-sync.yml` on the
self-hosted `[local-secure-release]` runner — add a **NET-NEW `case` arm** mapping
`verdify-staging → namespace + runtime_secret + image_pull_secret` (the enum currently lists
only `vast-cloud-tco-dev`). App manifests reference Secrets **by name only**; an overlay
`secrets.placeholder.yaml` (labeled `config.kubernetes.io/local-config:"true"`,
`PLACEHOLDER_NOT_A_REAL_SECRET` values) exists only for local kustomize/kubeconform and is
excluded from real applies. **`target.namespace` byte-identical to ArgoCD
`destination.namespace` and the base Namespace** (§2.3).

### 4.3 Bind / auth fixes (close the gravity anti-patterns)

- **api:** retire the `0.0.0.0:8300` *host* bind (docstring + systemd `ExecStart`); the pod
  is ClusterIP. **Close the `VERDIFY_ALLOW_UNAUTHENTICATED_WRITES` escape hatch**
  (`api/main.py:633`, wired in `docker-compose.yml:187`) — hard-unset in the k3s ConfigMap
  (mirror gravity's `AUTH_MODE=optional` fix). `require_write_access()` already fails closed
  when the key is set.
- **mcp:** add a transport auth guard that **fails CLOSED** — today there is none (the 18+
  typed tools incl. `set_plan`/`set_tunable` are unguarded, gravity's "MCP failed open"
  anti-pattern). **WRAP without changing tool semantics**; any tool-surface edit needs the
  full firmware PR artifact set + coordinator(iris-dev) + Iris concurrence **[GATE: Jason]**.

### 4.4 Registry gates (must both exit 0)

Run with `.venv/bin/python3`: `make validate` (schemas) + `make verify-reproducible`
(re-transform + byte-compare all generated files incl. `registry/catalog/`). After any
source-field edit run `make transform` and commit; **never hand-edit generated files** (the
"Generated by build/transform_registry.py (reproducible)" header marks them).

---

## 5. Data migration (TimescaleDB copy-not-move runbook)

**System of record, ~2.5M+ rows, `timescale/timescaledb:latest-pg16`, bound
`127.0.0.1:5432` today, named volume `tsdb_data`.** Nightly `pg_dump` → `/mnt/iris/backups`
is the only current backup. The dispatcher/planner read **live views** — continuity matters.
**Copy, never move; verify before trust; reversible.**

**Target:** single-replica `StatefulSet` on a **Retain `local-path` PVC** (mirror gravity
`db-statefulset.yaml`: `runAsNonRoot` uid 999, fsGroup 999, drop ALL caps,
`readOnlyRootFilesystem:false` since postgres writes runtime files,
`PGDATA=/var/lib/postgresql/data/pgdata`, `POSTGRES_PASSWORD` via `secretKeyRef`,
`volumeClaimTemplates`, headless Service `clusterIP: None`). **Use a
`timescale/timescaledb-pg16` image — NOT `pgvector/pgvector` like gravity** — to preserve the
hypertable + compression extensions. PVC excluded from ArgoCD prune/self-heal
(`ignoreDifferences` on `volumeClaimTemplates`).

**Runbook (mirror gravity §4):**
1. **Source quiescent** — at a quiescent moment, stop DB writers cleanly (this is the only
   atomic-handoff point; do NOT let both stacks write the same DB) **[GATE: Jason]**.
2. `pg_dump -Fc` a consistent custom-format dump.
3. Fresh `StatefulSet` comes up empty in `verdify-staging`.
4. An **idempotent migration `Job`** (ArgoCD `PreSync` hook,
   `argocd.argoproj.io/hook-delete-policy: BeforeHookCreation`) restores the dump; app pods
   set a skip-migrations env so they never race the Job.
5. **Verify (not just rows):** re-run row-count queries inside the k3s DB and assert equality;
   assert the **hypertable list + compression policy state** survived the dump/restore;
   assert the migration version matches; run a continuity probe.
6. Only on full parity does the cutover hand DB-write ownership from the VM to k3s.

Dumps to NAS only — **never live DB files on NFS.**

---

## 6. The device-VLAN decision (§3.4 — the single biggest risk, GATED SPIKE)

### 6.1 The constraint

The control loop is pinned to the physical greenhouse LAN and **crosses VLANs**:

- ESP32 `192.168.10.111:6053` (ESPHome native API, Noise PSK) — telemetry read **and**
  setpoint/occupancy push target **and** OTA target.
- HA `192.168.30.107:8123` (REST: Shelly, Tempest, hydro, Lutron grow-lights, occupancy) +
  Sentinel MQTT bridge `192.168.30.107:1883`.
- Frigate/go2rtc `192.168.30.142:5000/1984` (occupancy + camera).
- Tempest weather = **direct UDP broadcast to the ESP32** — L2-local, **out of the pod's
  path entirely**; do not relay it, just confirm unaffected.
- DB `127.0.0.1:5432`.

The ingestor (`verdify-ingestor.service`) runs **5 concurrent async loops** (`esp32_loop`,
`flush_loop`, `task_loop`, `mqtt_loop`, `setpoint_listener`; `ingestor.py:2000-2005`) and
delivers via a **single shared `aioesphomeapi` native connection** paced by an `asyncio.Lock`
+ `_MIN_COMMAND_INTERVAL_S=2.0` to protect ESP32 heap. **A normal k3s pod on the apps/agents
VLANs cannot reach `192.168.10.0/24` or the services VLAN by default.**

### 6.2 The decision (RECOMMENDED — consistent with "Track A wins")

**The ingestor STAYS VM-side initially.** Move the **stateless / DB-read services first**
(api, mcp, site, grafana, umami, and the DB read path), in this order; the **device-touching
ingestor is the LAST thing to move — or deliberately never moves** if the latency/reachability
risk does not clear. This is fully "additive + reversible": k3s consumes the VM-side device
loop's output (DB rows) until the spike below proves a pod can own the loop.

- **Single-writer invariant (non-negotiable):** when/if the ingestor does move, it is a
  `Deployment replicas:1 strategy:Recreate` (**NOT RollingUpdate** — a second pod connecting
  mid-rollout would double-push and thrash the ESP32). No Service, no LB (connect-out only).
- The ESP32 still owns relay safety deterministically (8-state FSM, 5s loop). The ingestor
  only pushes bounded setpoints. **No safety logic moves cloud-side, ever.**

### 6.3 The concrete provable spike [GATE: Jason — touches firewall/router posture]

Treat "can a k3s pod reach `192.168.10.111:6053` within the 5–10s SLA" as a **discrete,
gated spike BEFORE committing to move the ingestor**. **Design only here; implement no
routing/firewall/NetworkPolicy egress change.** The spike, to be run by laptop-root/Jason:

1. **Reachability:** schedule a throwaway probe pod in `verdify-staging`, `nodeSelector`-pin
   it to a node with a candidate route to VLAN 10, and open a **read-only** TCP connection to
   `192.168.10.111:6053` (and `192.168.30.107:8123/1883`, `192.168.30.142:5000`). Pass = TCP
   handshake succeeds; **no setpoint write, no second ESP32 native-API session** (would
   collide with the live ingestor's single connection).
2. **Latency:** measure the round-trip; the occupancy→light path
   (Frigate→MQTT→ingestor→ESPHome push→5s tick→Lutron) must complete within **~5–10s**;
   compare to the audited baseline (p50 37s / p95 81s band-change, ~95% confirm).
3. **Egress design (paper only):** the NetworkPolicy egress allow for `192.168.10.0/24` +
   the HA/MQTT/Frigate subnet would be declared in the base `networkpolicy.yaml`; the route
   is an inter-VLAN UniFi-gateway policy point (the DOCKER-USER iptables analogy does **not**
   transfer). **STOP & ask Jason** before any such change.
4. **Decision outcome:** if reachability+latency clear under live load → schedule the
   ingestor move LAST in P9. If not → **record the explicit decision that the ingestor (and
   dispatcher) stay VM-side permanently as the device edge**, with k3s consuming the loop.
   Either outcome satisfies the DoD (§8.10).

The local mosquitto's placement is part of this decision (if the device path needs the local
broker, it may stay near the device VLAN); the ESP32 confirms it speaks native API only (no
`mqtt:` block), so the compose mosquitto is a normal stateless migration with no ESP32-LAN
constraint.

---

## 7. The partial-migration boundary (what moves vs stays)

| Component | Move? | Where | Why |
|---|---|---|---|
| `verdify-site` (nginx Quartz) | **MOVE (first)** | k3s, ClusterIP + apps ingress + read-only NFS source PV | stateless, no device dep |
| `api` (FastAPI) | **MOVE (early)** | k3s, ClusterIP + apps-pool LB behind Authentik | stateless DB-reader; close bind/auth |
| `mcp` | **MOVE (early)** | k3s, ClusterIP-only, fail-closed auth | planner tool surface; wrap, don't change semantics |
| `grafana`/`umami`/`goaccess` | **MOVE** | k3s, ClusterIP + ingress | not greenhouse-critical |
| `timescaledb` | **MOVE (gated, copy-not-move)** | k3s StatefulSet, Retain PVC | system of record; §5 atomic handoff |
| `traefik` | **MOVE** | k3s apps ingress | already Traefik-shaped |
| `mosquitto` | **conditional** | k3s OR stays near device VLAN | §6 decision |
| **`ingestor` + dispatcher** | **STAYS VM-side initially; LAST/maybe-never** | VM systemd | device-touching, single-writer, §6 spike-gated |
| **firmware OTA** | **NEVER via CI flash** | `make firmware-deploy` only | §3.5 handoff; CI builds artifacts only |

**Firmware framing:** CI may **build & validate firmware artifacts** (compile, 16 invariants,
replay-diff THRESHOLD_PCT=0, produce `.ota.bin`) — additive and safe. **CI MUST NOT flash/OTA
the device.** Do not wire `firmware-deploy` into Actions. No edits to `firmware/lib/**`,
`greenhouse_logic.h`, `entity_map.py`, `mcp/server.py` semantics without the full artifact set
+ coordinator(iris-dev) + Iris concurrence **[GATE: Jason]**.

---

## 8. The cutover gate + Definition of Done (11 points)

Cutover (P9) happens **only** when all 11 hold, each independently proven; stop migrated VM
services **service-by-service, never the VM**, with source intact through a soak/rollback
window. The device-touching ingestor is stopped LAST and only if §6 fully cleared.

1. **Repo-driven CI/CD:** push → tests → `ghcr.io/<owner>/verdify-<comp>:sha-<gitsha>` built+
   pushed, `GIT_SHA` baked, surfaced at `/health/detailed` (image==source). Manual
   `compose up`/`systemctl restart` retired for migrated services.
2. **ArgoCD auto-deploy:** an Application per deployable in `jvallery/agents` pins ghcr SHAs,
   selfHeals; rollback = revert the pin.
3. **Backstage:** each component has a repo-root `catalog-info.yaml`; registry renders;
   portal shows the entity graph + live k8s status + owner + `spec.system`.
4. **Gates green:** registry `make validate` + `make verify-reproducible`, and app-repo
   test/lint + `k8s-manifests` kubeconform, all exit 0.
5. **APP on apps subnet:** api/site reachable on **VLAN 7** (apps-pool, reserved `.7.x`,
   Traefik behind Authentik, identity headers not spoofable); mcp/db/metrics ClusterIP;
   NetworkPolicy default-deny + scoped allows.
6. **Dispatcher operational:** ingestor runs as `replicas:1 strategy:Recreate` (**or** the
   recorded decision it stays VM-side), confirm-rate + latency within baseline (~95%, p50
   37s / p95 81s), single-writer held.
7. **Firmware pipeline intact:** CI builds+validates artifacts (compile, 16 invariants,
   replay-diff THRESHOLD_PCT=0) but **never flashes**; `make firmware-deploy` with its
   preflight/bake/auto-rollback remains the only OTA path, unchanged.
8. **Device networking proven from k3s:** a pod demonstrably reaches ESP32/HA/MQTT/Frigate
   within the 5–10s SLA — **OR** the explicit recorded decision the device loop stays VM-side.
9. **Secrets SOPS-sealed:** every secret is a `registry/secrets/<id>.yaml` meta + age payload,
   delivered via `local-k8s-secret-sync.yml`, referenced by name. **No plaintext `.env` in the
   runtime contract.** ESP32 PSK/OTA password handled per Jason's device-affecting confirmation.
10. **VSCode-remote dev in k3s:** `verdify-saas` / `verdify-ingestor` (the dev-agent entries)
    develop from the pod via Remote-SSH `:2222`; `placement.mode: pod`; worktree on a Retain
    PVC.
11. **Source decommissioned only when green+proven:** migrated VM services stopped
    service-by-service (never the VM), source intact through a soak window; TimescaleDB
    migrated AND verified (row + hypertable + compression parity), copy-not-move.

---

## 9. P0–P9 phased plan (per-phase gate / rollback / STOP-and-ask)

Additive throughout; the live VM keeps running until the final gated cutover.

### P0 — Audit & safety (DONE, read-only)
- **Steps:** on-box audit complete (§1). Production = `live/platform-main` (4 ahead/0 behind),
  NOT `origin/live`; secrets inventoried (path/mode only); no deploy tree / catalog yet.
- **Gate:** drift reported (not auto-fixed); audit written. **Rollback:** n/a (read-only).
- **STOP & ask:** **[GATE: Jason/James]** confirm canonical branch name `live/platform-main`
  and correct the handoff's `origin/live`/`vm-verdify` references before any P2+ step.

### P1 — Design (THIS DOC)
- **Steps:** gravity-shaped program written; §6 device-VLAN decision front and center
  (ingestor stays VM-side initially); DB runbook + cutover gate + DoD defined.
- **Gate:** design reviewed; device-reachability spike is concretely provable; Jason signs off
  on any firewall/routing implication. **Rollback:** n/a.
- **STOP & ask:** **[GATE: Jason]** the device-VLAN routing approach; whether ingestor moves.

### P2 — Registry PR (jvallery/agent-fleet-control)
- **Steps:** source-field-only edits — APP cross-cutting contract (a registry entry with
  `network.vlan: apps` + reserved `.7.x`), one `registry/secrets/<id>.yaml` per secret
  (copy `backstage-secrets.yaml`, `target.namespace: verdify-staging`, no values). Run
  `make transform`, commit generated files, open PR.
- **Gate:** `make validate` && `make verify-reproducible` exit 0; CI green; PR merged.
- **Rollback:** revert the PR (additive metadata, nothing deployed).
- **STOP & ask:** **[GATE: Jason]** before sealing `ESP32_API_KEY` (Noise PSK) / OTA password
  — device-affecting; **[GATE: laptop-root]** the `.7.x` reservation.

### P3 — Containerize (PRs into VerdifyConsultancy)
- **Steps:** author `deploy/k8s/base` + `deploy/k8s/local-staging` kustomize trees; write
  Dockerfiles for api/mcp/ingestor (net-new, §3.4); bake `GIT_SHA`; apply security fixes
  (minimal images, non-root, drop caps, no `0.0.0.0` host bind, close unauth-write hatch,
  fail-closed mcp auth — wrap don't change semantics).
- **Gate:** `kustomize build` (v5.4.3) of every overlay passes `kubeconform -strict
  -ignore-missing-schemas`; images build locally; ingestor image makes an ESP32 connection in
  a **controlled** test (NOT the live device unless a gated probe).
- **Rollback:** PRs unmerged / images unpromoted; VM unchanged.
- **STOP & ask:** **[GATE: Jason]** before any test opening a *live* ESP32 connection or that
  could double-push; before editing shared infra (`docker-compose.yml`/`systemd/`/`mqtt/`/
  `.github/`) — coordinate scope ownership.

### P4 — CI/CD wire (Actions → ghcr)
- **Steps:** add `container-publish.yml` (reusable-build call) + `k8s-manifests.yml`; keep
  `ci.yml` as the unit gate.
- **Gate:** push to main produces `sha-<gitsha>` images in ghcr; manifest gate green; baked
  SHA shows at `/health/detailed`.
- **Rollback:** disable the workflow; nothing consumes the images yet.
- **STOP & ask:** **[GATE: Jason]** if any workflow would touch the firmware OTA path (it
  must not).

### P5 — ArgoCD Application (jvallery/agents)
- **Steps:** add `platform/gitops/applications/local-staging/verdify.yaml` (copy
  `vast-cloud-tco.yaml`): `source.repoURL` = verdify-platform, `path: deploy/k8s/local-staging`,
  pinned ghcr SHAs, `destination.namespace: verdify-staging`, `prune:false selfHeal:true`,
  `CreateNamespace=false`. Add the NET-NEW `local-k8s-secret-sync.yml` `case` arm. Seal
  secret(s).
- **Gate:** ArgoCD reconciles; pods Healthy in `verdify-staging` (empty DB initially); secrets
  present (sealed, not plaintext).
- **Rollback:** revert the image pin / delete the Application; k3s side only, VM untouched.
- **STOP & ask:** **[GATE: Jason/laptop-root]** namespace creation; first secret-sync run.

### P6 — Backstage entity
- **Steps:** add repo-root `catalog-info.yaml` (kind Component, `github.com/project-slug`,
  `spec.owner: group:james`, `spec.system: iris-verdify`); confirm registry renders into
  `registry/catalog/location.yaml` (reproducibility guard, never hand-edit).
- **Gate:** portal (https://backstage.vallery.net) shows the Verdify component(s) + live k8s
  status + entity graph + owner + system.
- **Rollback:** remove `catalog-info.yaml` / revert the render. **STOP & ask:** n/a.

### P7 — Deploy APP on apps subnet + data/device validation
- **Steps:** add the apps-pool LB Service (MetalLB annotations, reserved `.7.x`); Traefik
  apps ingress behind Authentik with auth-header stripping; ClusterIP everything else;
  NetworkPolicy default-deny + scoped allows + (paper) the §6 device-VLAN egress allow. Run
  the DB migration Job (copy-not-move, verify counts + hypertables + compression). **Run the
  §6 device-reachability spike for real.**
- **Gate:** endpoints reachable on `.7` behind Authentik; identity headers not spoofable; DB
  row/hypertable parity verified; pod proven to reach ESP32/HA/MQTT/Frigate within budget —
  OR the recorded decision the ingestor stays VM-side.
- **Rollback:** ArgoCD revert; VM stack still authoritative (never stopped).
- **STOP & ask:** **[GATE: Jason]** before pointing any *live device write* (a real setpoint
  push) at the ESP32 from a k3s pod — the moment the refactor first touches the greenhouse.

### P8 — VSCode-remote dev (DEV-AGENT plane)
- **Steps:** for `verdify-saas` / `verdify-ingestor`: stand up the agent pod (attach-v2 image,
  in-pod sshd `:2222`, agents-pool LB on `.64.x`), clone source into the `/work`
  `agent-worktree-nvme` PVC, prove Mac→`.64.x:2222` (watch the `externalTrafficPolicy:Local`
  Mac-routing issue), then flip `registry/agents/<id>.yaml placement.mode vm→pod`.
- **Gate:** Remote-SSH attach works; dev happens in the pod; worktree survives a pod restart.
- **Rollback:** flip `placement.mode` back to `vm`; VM worktrees still exist.
- **STOP & ask:** **[GATE: Jason]** retiring VM tmux lanes / worktrees (multi-agent scope).

### P9 — Gated cutover (the careful end)
- **Steps:** only when all 11 DoD points hold, stop migrated source services **service-by-
  service, never the VM**, keeping the source stack fully intact (no `down -v`, no data
  delete) for a soak/rollback window. The device-touching ingestor is stopped LAST and only
  if §6 fully cleared; otherwise it stays.
- **Gate:** the full §8 DoD, each item independently proven.
- **Rollback:** `systemctl start` / `docker compose up -d` the source service (data intact);
  ArgoCD scale-down the k3s side; the soak window exists for exactly this.
- **STOP & ask:** **[GATE: Jason]** **every** source-service stop is device-affecting-adjacent
  — confirm the gate is met and confirm the stop. **The firmware OTA path is NEVER part of
  cutover.**

---

## 10. Hard boundaries (this program TEES UP, does not execute)

- **Track A > Track B, always.** Nothing perturbs the live ESP32, the OTA path, the
  dispatcher, or the DB writers.
- **`VerdifyConsultancy` is READ-ONLY** → all app-repo changes are PRs into that org (James
  coordinates), never a direct push/merge. jvallery-owned deploy-wiring (registry, ArgoCD
  Application) → open PRs for review, do NOT merge.
- **NO cluster apply / kubectl / ArgoCD sync; NO secret sealing or reading secret values; NO
  device-VLAN / firewall / routing change; NO first live setpoint from a pod; NO stopping any
  live service; NO firmware build-and-flash.** All such steps are **[GATE: Jason]** /
  **[GATE: laptop-root]** handoffs, teed up here, not done.
- **No edits to `firmware/lib/**`, `greenhouse_logic.h`, `entity_map.py`, `mcp/server.py`
  semantics** (a Dockerfile + a `/health` wrapper only, with care; tool semantics unchanged).
