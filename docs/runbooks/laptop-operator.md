# Laptop operator runbook — iterate, deploy, query, OTA from the MacBook

**Audience:** Jason / laptop-root (and any kubectl-equipped operator host).
**Since:** 2026-06-10 (branch unification); **SINGLE-ENV update 2026-06-16.**
**State of the world:** `main` is the single canonical branch. **`verdify-dev`
and staging are DECOMMISSIONED and DELETED — prod (`verdify-prod`, ArgoCD app
`verdify-prod-dark`, manual-sync behind the device-write gate) is the ONLY
environment** (serves lab/graphs/api.verdify.ai). Prod is advanced by the
`prod-promote` workflow off the published `:branch-main` image digests in GHCR
(no more `bump-dev-digests` / dev render / dev-equality guard). Any section
below that mentions a dev environment, `overlays/dev`, dev DB restore, or the
dev proving flow is HISTORICAL — those resources no longer exist.

> **2026-06-18 handoff:** development moved off the laptop to k3s-resident
> agents. **Every command below is runnable from any kubectl-equipped host** (the
> title is historical). The k3s-agent operating model, the portable dev loop, and
> the firmware-OTA tribal knowledge are consolidated in
> [`../handoff/k3s-agent-handoff.md`](../handoff/k3s-agent-handoff.md) — read that
> first. The only laptop-bound workflow is the firmware OTA toolchain itself (§3).

## 0. One-time host setup

```bash
cd ~/repos/verdify-platform
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python \
  asyncpg "aioesphomeapi>=24.0" python-dotenv pyyaml jinja2 httpx "pydantic>=2.8" \
  "anthropic>=0.90" "openai>=1.50" "fastapi>=0.115" "uvicorn>=0.34" \
  ruff pytest pytest-asyncio psycopg2-binary "esphome>=2026.1.4"
brew install libpq kustomize   # psql at /opt/homebrew/opt/libpq/bin/psql
```

The Makefile auto-prefers a repo-local `.venv` (falls back to the legacy
`/srv/greenhouse/.venv` on VM hosts). ESPHome secrets live at
`~/.verdify/esphome-secrets.yaml` (0600; recovered from the iris-VM PBS
backup 2026-06-10; also sealed into k3s as
`verdify-prod/verdify-firmware-ota`). `kubectl` uses the default context.

## 1. Database access (prod + dev)

```bash
scripts/verdify-db.sh prod -c "SELECT count(*) FROM climate;"   # one-shot
scripts/verdify-db.sh prod                                      # interactive psql
scripts/verdify-db.sh dev  -c "..."                             # the nightly prod copy
scripts/verdify-db.sh prod --tunnel        # localhost:5433 for asyncpg/psycopg/DBeaver
scripts/verdify-db.sh dev  --tunnel        # localhost:5434
```

Creds: k8s Secret `verdify-app-secrets/POSTGRES_PASSWORD` per namespace (the
script never prints it; `--tunnel` prints the kubectl one-liner to export
`PGPASSWORD` yourself). kubectl exec/port-forward ride the API-server channel,
so the in-cluster default-deny NetworkPolicies don't apply.
**Prefer `dev` for heavy analysis** — it's a nightly restored copy of prod
(see §4); prod shares its box with the live greenhouse write path.

Historical derived-data reconciliation lives in
[`derived-history-reconcile.md`](./derived-history-reconcile.md). It dry-runs
by default and should be run against dev first before any prod apply.

## 2. CI/CD: push publishes, dispatch promotes to prod (single-env)

- **Push to `main`** (or merge a PR): `container-publish.yml` builds the
  impacted images (api/mcp/ingestor/migrate/planner + artifact-only
  setpoint-server) and publishes them **digest-pinned to GHCR** (immutable
  `:sha-<sha>` + mutable `:branch-main`). There is **no environment write-back**
  — `bump-dev-digests` / dev auto-sync are removed (dev is gone). `ci.yml` (all
  gates), `k8s-manifests.yml` (kubeconform) and `cnpg-image.yml` fire on `main` too.
- **Full-pipeline button:** `gh workflow run container-publish.yml --ref main`
  — a manual dispatch builds + publishes ALL images.
- **Promote to prod:**
  `gh workflow run prod-promote.yml --ref main -f mode=pull-request`
  (or `mode=dry-run`). Resolves each promotable image's `:branch-main` digest
  from GHCR (imagetools), surgically bumps `overlays/prod/kustomization.yaml`,
  runs the Device-Write-Safety-Gate, opens a `prod-promote` PR.
  `promote-diff-guard` (required check) re-asserts a **digests-only** change
  surface. Merge = git change only; then the gated sync below.
  - Known race: `verdify-migrate` rebuilds on every publish, so a push that
    lands while a promote PR is open advances dev's migrate digest and fails
    the guard. Re-run prod-promote after the pipeline settles.
- **The gated prod sync (the ONLY step that touches the live writer):**
  ```bash
  kubectl patch application verdify-prod-dark -n argocd --type merge \
    -p '{"operation":{"initiatedBy":{"username":"laptop-root"},"sync":{"prune":false}}}'
  ```
  Pre-check with `kustomize build deploy/k8s/overlays/prod | kubectl diff -f -`
  and confirm the ingestor Deployment (strategy: Recreate — never two writers)
  changes only when you intend it. KNOWN ISSUE: unscoped sync operations on
  this app sometimes get rewritten to a stale selective scope — if the
  syncResult covers too few resources, submit the operation with an explicit
  `resources:` list built from the app's OutOfSync set (see issue tracker).

## 3. Firmware OTA from the laptop

Device: ESP32 at `192.168.10.111` (OTA :3232, native API :6053). **Running
firmware `2026.6.17.2042.dcc6078`** (band-compliance; pinch wired, live
`band_track_fraction=0.25`). **Verify the running version from
`diagnostics.firmware_version`, NOT `firmware/artifacts/last-good.version`**
(last-good is the rollback floor — it lags through the 48 h bake). The secrets
reconstruction (k3s sources) and the **false-rollback gotcha** (the post-OTA
checks default to the wrong DB backend off-laptop and can auto-rollback a
healthy OTA) are documented in
[`../handoff/k3s-agent-handoff.md`](../handoff/k3s-agent-handoff.md) §4 — read it
before flashing.

```bash
# Validate + compile (laptop venv esphome 2026.5.x):
SECRETS_SRC=$HOME/.verdify/esphome-secrets.yaml \
ESPHOME_BIN=$PWD/.venv/bin/esphome \
  scripts/firmware-esphome-worktree.sh config    # or: compile

# Preflight gates only (8 gates, DB-backed via kube backend):
VERDIFY_DB_BACKEND=kube bash scripts/firmware-deploy-preflight.sh

# The real deploy (compile + OTA + sensor-health + auto-rollback):
OTA_PW="$(kubectl -n verdify-prod get secret verdify-firmware-ota \
  -o jsonpath='{.data.ota_password}' | base64 -d)" \
SECRETS_SRC=$HOME/.verdify/esphome-secrets.yaml \
ESPHOME_BIN=$PWD/.venv/bin/esphome \
  make firmware-deploy
```

The gates are real: no OTA while `alert_log` has unresolved critical/high
rows, 48h bake on `last-good.ota.bin` mtime, ≤1 OTA/calendar week, telemetry
freshness <300s, action-log proof. Overrides need the documented
sign-off envs (see `scripts/firmware-deploy-preflight.sh`). Firmware policy:
hot-staged direct-to-prod (there is no dev device; dev never connects to any
device).

## 4. Environments

| | prod | dev |
|---|---|---|
| Namespace | `verdify-prod` | `verdify-dev` |
| ArgoCD app | `verdify-prod-dark` (manual-sync, gated) | `verdify-dev` (auto-sync) |
| Public URLs | verdify.ai, www, lab, labs, graphs, api, mcp (.verdify.ai) | lab/api/www/graphs **.k3s.verdify.ai** (via the `*.verdify.ai` wildcard DNS + the `*.k3s.verdify.ai` tunnel rule) |
| Database | live TimescaleDB (single writer: ingestor) | **nightly restored copy of prod** — CronJob `verdify-db-restore-from-prod` 03:47 UTC (after the 02:17 UTC prod pg_dump), full -Fc restore + freshness gate |
| Device | THE writer (ingestor replicas:1 + allow-ingestor-device-egress; setpoint-server = 2nd writer via HA) | **never** — ingestor replicas:0 + deny-esp32-egress + device-write=0; dev plans are wiped nightly and never replicate to prod |
| Grafana | graphs.verdify.ai | graphs.k3s.verdify.ai (same dashboards, dev datasource) |

Edge path for both: Cloudflare tunnel (`cloudflared` ns `cloudflare`, config
SoT `jvallery/agents platform/kubernetes/cloudflare/cloudflared-config.yaml`)
→ apps-Traefik VIP `192.168.7.10` → (prod: tier-2 `verdify-traefik`; dev:
tier-1 IngressRoutes).

## 5. Where things are

- ArgoCD app CRs: `verdify-dev` + `verdify-local-staging`(retired) in
  `jvallery/agents platform/gitops/applications/`; `verdify-prod-dark` is
  hand-applied (mirror in `deploy/k8s/argocd/apps/`). AppProject `app-test`
  (whitelists live in jvallery/agents `platform/gitops/projects/app-test.yaml`).
- Dumps NFS: NAS `192.168.30.126:/volume2/verdify` subDir `db-dumps/prod`
  (prod PV `verdify-db-dumps-prod` RWX; dev RO PV
  `verdify-db-dumps-prod-ro-dev` — both platform-applied, not ArgoCD-managed).
- CoreDNS override: `kube-system/coredns-custom` NXDOMAINs
  `*.{com,net,org,io,...}.servers.vallery.net` search-append junk (the public
  `*.vallery.net` wildcard otherwise poisons in-cluster resolution of
  external names — see the ConfigMap's annotation).
