# Laptop operator runbook — iterate, deploy, query, OTA from the MacBook

**Audience:** Jason / laptop-root (and any kubectl-equipped operator host).
**Since:** 2026-06-10 (branch unification); **SINGLE-ENV update 2026-06-16.**
**State of the world:** `main` is the single canonical branch. **`verdify-dev`
and staging are DECOMMISSIONED and DELETED — prod (`verdify-prod`, ArgoCD app
`verdify-prod-dark`, manual-sync behind the device-write gate) is the ONLY
environment** (serves lab/graphs/api.verdify.ai). Prod is advanced by
release-pin commits in `deploy/k8s/overlays/prod` (§2) and then the gated
sync; GHCR is retired per ADR-0021. Any section
below that mentions a dev environment, `overlays/dev`, dev DB restore, or the
dev proving flow is HISTORICAL — those resources no longer exist.

> **2026-06-18 handoff:** development moved off the laptop to k3s-resident
> agents. **Every command below is runnable from any kubectl-equipped host** (the
> title is historical). The k3s-agent operating model, the portable dev loop, and
> the firmware-OTA tribal knowledge are consolidated in
> [`k3s-operations.md`](./k3s-operations.md) — read that
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

## 1. Database access (prod only)

```bash
scripts/verdify-db.sh prod -c "SELECT count(*) FROM climate;"   # one-shot
scripts/verdify-db.sh prod                                      # interactive psql
scripts/verdify-db.sh prod --tunnel        # localhost:5433 for asyncpg/psycopg/DBeaver
```

Creds: k8s Secret `verdify-app-secrets/POSTGRES_PASSWORD` in `verdify-prod` (the
script never prints it; `--tunnel` prints the kubectl one-liner to export
`PGPASSWORD` yourself). kubectl exec/port-forward ride the API-server channel,
so the in-cluster default-deny NetworkPolicies don't apply. There is no dev DB;
heavy analysis should use a dump/snapshot or an explicitly provisioned
disposable copy, not the live prod writer database.

Historical derived-data reconciliation lives in
[`derived-history-reconcile.md`](./derived-history-reconcile.md). It dry-runs
by default; a production apply requires its explicit apply flag and technical
preflight.

## 2. CI/CD: merge publishes, promotion pins, the gated sync deploys (single-env)

- **Merge to `main`:** a fleet Argo Event submits the exact revision to the
  in-cluster `repo-build` WorkflowTemplate (Kaniko → zot origin) for the images
  declared in `.agent-fleet/ci.yaml`. The `verdify-platform-ci` pin actuator
  then commits the api, mcp, ingestor, migrate and experiment-v2-orchestrator
  digests to the `images:` block of `overlays/prod/kustomization.yaml` as
  build candidates. They do not render: `overlays/prod/release-pins.yaml`, a
  `transformers:` entry applied after `images:`, holds the digests production
  runs, so `main` keeps rendering what is live (#808). This repo has no GitHub
  Actions workflows (`make ci` / `scripts/ci-local.sh` is the gate). Merge = git
  change only.
- **Promote:** in one attended session, run
  `python3 scripts/promote-release-pins.py` (it copies the candidate digests
  into `release-pins.yaml`, all five by default: the migrate hook carries the
  schema the others expect), commit that digest-only change, merge it, then run
  the gated sync below. Planner, setpoint-server and lab-publisher
  release pins are edited by hand in the `images:` block. Check the rendered
  change first:
  ```bash
  kustomize build deploy/k8s/overlays/prod | grep -o 'verdify-[a-z0-9-]*@sha256:[0-9a-f]*' | sort -u
  ```
- **The gated prod sync (the ONLY step that touches the live writer):**
  Pre-check with `kustomize build deploy/k8s/overlays/prod | kubectl diff -f -`
  and confirm the ingestor Deployment (strategy: Recreate — never two writers)
  changes only when you intend it. For **every new full sync**, submit the exact
  intended revision and clear the previous terminal operation state atomically,
  even when the previous operation was full and had no selectors:
  ```bash
  set -euo pipefail
  SYNC_REVISION='<exact-main-sha>'
  [[ "$SYNC_REVISION" =~ ^[0-9a-f]{40}$ ]]
  ARGO_OPERATION_SNAPSHOT="$(kubectl get application verdify-prod-dark -n argocd -o json)"
  printf '%s' "$ARGO_OPERATION_SNAPSHOT" | jq -e '
    .operation == null and
    (.status.operationState == null or
     (.status.operationState.phase | IN("Succeeded", "Failed", "Error")))'
  ARGO_OPERATION_RV="$(printf '%s' "$ARGO_OPERATION_SNAPSHOT" | jq -r '.metadata.resourceVersion')"
  kubectl patch application verdify-prod-dark -n argocd --type json -p "$(
    jq -n --arg rv "$ARGO_OPERATION_RV" --arg revision "$SYNC_REVISION" '
      [{op:"test",path:"/metadata/resourceVersion",value:$rv},
       {op:"add",path:"/status/operationState",value:null},
       {op:"add",path:"/operation",value:{
         initiatedBy:{username:"laptop-root"},
         sync:{revision:$revision,prune:false}}}]'
  )"
  ```
  Stop if the precondition or resourceVersion test fails; inspect the current
  operation before submitting another one. The current Application CRD has no
  status subresource, so status clearing and submission share the atomic patch.
  A prior selective operation can leak its resource scope ([#317](https://github.com/VerdifyConsultancy/verdify-platform/issues/317),
  [upstream #28701](https://github.com/argoproj/argo-cd/issues/28701)). A separate
  observed failure on 2026-10-02 also reused stale `syncResult` after a prior full
  operation: it reported Succeeded/126 resources at the intended new revision,
  while the migrate hook and running workloads still used the old digests.
  The original false-success receipt must remain preserved; revision/count alone
  are insufficient deployment evidence.

  Immediately verify that a **new** operation started and that
  `.status.operationState.operation.sync.resources` is absent or empty. Inspect
  actual migration/bootstrap hook Pod UIDs, start times and image digests against
  the promoted receipt, then actual running workload imageIDs/source identities.
  Require the owning ledger/readiness hooks and authenticated smoke checks; do
  not accept cached hook results, Succeeded, Synced/Healthy, or 126 resources as
  a substitute for those exact readbacks. If selectors, stale hook provenance,
  old running digests or too few resources appear, stop and preserve the original
  outcome before a documented fix-forward operation.

  The explicit `resources:` list (`scripts/gen-sync-resource-vector.sh`, reviewed
  first; see `attended-convergence.md`) is a fallback only: Argo CD skips every
  hook on a selective sync, including the `verdify-migrate` PreSync, and does not
  record it in history.

## 3. Firmware OTA from the laptop

Device: ESP32 at `192.168.10.111` (OTA :3232, native API :6053). **Running
firmware as of 2026-07-13: `2026.7.10.1500.09ee886`** (the 2026-07-10
software-recovery release; pinch machinery wired, live
`band_track_fraction=0.0` — the ADR-0004/#377 float). **Verify the running
version from `diagnostics.firmware_version`, NOT
`firmware/artifacts/last-good.version`** (last-good is the rollback floor — it
lags through the 48 h bake); the authoritative read from any kubectl host:

```bash
scripts/verdify-db.sh prod -c "SELECT firmware_version, max(ts) FROM diagnostics
  WHERE firmware_version IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 1;"
```

The secrets
reconstruction (k3s sources) and the **false-rollback gotcha** (the post-OTA
checks default to the wrong DB backend off-laptop and can auto-rollback a
healthy OTA) are documented in
[`k3s-operations.md`](./k3s-operations.md) §4 — read it
before flashing.

**Pinch resets on every flash (#413/#377):** `band_track_fraction` is
`restore_value: no` in `firmware/greenhouse/globals.yaml`, so an OTA/reboot
cold-starts it to the compiled `initial_value` — **`0.0` on current `main`**
(ADR-0004) — regardless of any live planner-pushed value (this is how the
pre-2026-07-10 live 0.25 was dropped). The
`crop_band_anchors`→NVS reconcile does **NOT** re-assert it (that path only
protects `restore_value: yes` band globals — `docs/CONTROL-ARCHITECTURE.md` §7),
and the registry pins its bounds to `[0.0, 0.0]`, so no current push path can
restore a nonzero pinch without a registry-bounds change first. Post-flash, execute the
g-377 pinch decision (re-pin vs accept float) and record `band_track_fraction` +
`dehum_vent_hold_enabled` (#410) + the envelope config (door screen-window
open/closed, #412) in the bake report — step-by-step in
`docs/RELEASE-CHECKLIST.md` §B "Deploy + post".

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
reason-bearing override envs (see `scripts/firmware-deploy-preflight.sh`). Firmware policy:
hot-staged direct-to-prod (there is no dev device; dev never connects to any
device).

## 4. Environment

| Surface | Current value |
|---|---|
| Namespace | `verdify-prod` |
| ArgoCD app | `verdify-prod-dark` (manual-sync, gated) |
| Public URLs | verdify.ai, www, lab, labs, graphs, api, mcp (.verdify.ai) |
| Database | live TimescaleDB (single writer: ingestor) |
| Device | THE writer (ingestor replicas:1 + allow-ingestor-device-egress; setpoint-server = second HA writer) |
| Grafana | graphs.verdify.ai |

Edge path: Cloudflare tunnel (`cloudflared` ns `cloudflare`, config SoT
`jvallery/agents platform/kubernetes/cloudflare/cloudflared-config.yaml`)
→ apps-Traefik VIP `192.168.7.10` → tier-2 `verdify-traefik`.

## 5. Where things are

- ArgoCD app CRs: `verdify-prod-dark` is mirrored in
  `deploy/k8s/argocd/apps/`; retired `verdify-dev` / `verdify-local-staging`
  records may still exist in historical agent-fleet-control state.
- Dumps NFS: NAS `192.168.30.126:/volume2/verdify` subDir `db-dumps/prod`
  (prod PV `verdify-db-dumps-prod` RWX; dev RO PV
  `verdify-db-dumps-prod-ro-dev` — both platform-applied, not ArgoCD-managed).
- CoreDNS override: `kube-system/coredns-custom` NXDOMAINs
  `*.{com,net,org,io,...}.servers.vallery.net` search-append junk (the public
  `*.vallery.net` wildcard otherwise poisons in-cluster resolution of
  external names — see the ConfigMap's annotation).
