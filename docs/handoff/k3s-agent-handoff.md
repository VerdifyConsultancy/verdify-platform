# Verdify Platform — k3s-Agent Handoff & Operations

**As of 2026-06-18 this is the handoff from laptop-based development to autonomous
agents running IN the k3s cluster.** This document makes the repo self-sufficient
for that transition: the operating model, the dev loop runnable from any
kubectl-equipped host, what the repo is the source of truth for, the
firmware-OTA tribal knowledge (previously only in laptop project-memory), the
known blockers for *full* k3s autonomy, and the durable gotchas.

**Read this + `AGENTS.md` (→ `CLAUDE.md`) + `README.md` first.** Do not rely on
chat history or laptop-local memory — everything durable a future session needs
is in the repo (`docs/`), and this doc is the index for the handoff.

---

## 1. Operating model (post-laptop)

- **The agent(s) run in k3s** (Codex/Claude with kubectl, the in-cluster DB, and
  the prod secrets) and own: code, schemas, migrations (with the safety rules in
  `CLAUDE.md`), CI, k8s manifests, Grafana dashboards, and docs — landing on
  **`main`** and keeping CI green. (Retired: the laptop single-agent model and
  the earlier five-persistent-agents model.)
- **Jason is the human gate** for: firmware OTA, the prod ArgoCD sync that
  touches the live writer, device-VLAN actions, DB-destructive prod operations,
  credential rotation, and public DNS/edge/org settings.
- **The GitHub repo is the source of truth.** What's deployed is what's in git
  (manifests, image digests, dashboards, migrations, firmware source). Never
  hand-edit live cluster resources as the durable fix — change git, then sync.

---

## 2. The dev loop — runnable from any kubectl host (k3s-portable)

Everything below works from a kubectl-equipped pod/host; none of it needs the
laptop. (Firmware OTA is the exception — see §4.)

- **Build / test / lint:**
  - `make lint` (ruff) — required.
  - `make test` (pytest) — required; 1 pre-existing flaky timeout
    (`test_dew_point_risk_computes`) is tolerated, everything else must pass.
  - DB-backed targets default to `VERDIFY_DB_BACKEND=kube` (Makefile) → they hit
    the in-cluster DB. Python runs from the repo `.venv` or `python3` on PATH
    (the legacy `/srv/greenhouse/.venv` fallback is dead; ensure a usable venv).
- **Database access** (read-only is safe; destructive prod is Jason-gated):
  ```bash
  scripts/verdify-db.sh prod -c "SELECT count(*) FROM climate;"   # one-shot
  scripts/verdify-db.sh prod                                      # interactive psql
  scripts/verdify-db.sh prod --tunnel                             # local socket for psycopg/DBeaver
  ```
  It kubectl-execs `psql` in `verdify-db-0` (auth in-pod; never prints creds).
  **There is no `dev` DB anymore** — single-env prod only (`scripts/verdify-db.sh dev`
  and any "prefer dev for heavy analysis" guidance is dead).
  - GOTCHA: `scripts/verdify-db.sh prod < file.sql` does **not** apply (stdin is
    swallowed). Apply SQL files with
    `kubectl exec -i -n verdify-prod verdify-db-0 -c postgres -- psql -U verdify -d verdify -v ON_ERROR_STOP=1 < file.sql`.
- **Deploy / GitOps (single-env prod):**
  ```
  push main → container-publish.yml (GHCR digest-pinned :sha-<sha> + :branch-main)
            + ci.yml gates + k8s-manifests.yml (kubeconform)
  → prod-promote.yml (DISPATCH: gh workflow run prod-promote.yml -f mode=pull-request)
       resolves each promotable image's :branch-main digest from GHCR, surgically
       bumps overlays/prod/kustomization.yaml, opens a prod-promote PR
  → promote-diff-guard.yml (required check: digests-only change surface)
  → human merges the PR (git == intended)
  → GATED: argocd app sync verdify-prod-dark   (the ONLY step that touches the live writer)
  ```
  The gated sync, from any kubectl host:
  ```bash
  kubectl patch application verdify-prod-dark -n argocd --type merge \
    -p '{"operation":{"initiatedBy":{"username":"<agent>"},"sync":{"prune":false}}}'
  ```
  Pre-check: `kustomize build deploy/k8s/overlays/prod | kubectl diff -f -`; confirm
  the **ingestor Deployment** (the single device writer) only changes when intended.
  Promotable set = api/mcp/ingestor/migrate/planner (setpoint-server + lab are
  hand-pinned). ArgoCD app `verdify-prod-dark` is **manual-sync, prune:false**.
- **Grafana dashboards** (non-control-path, safe to iterate): edit
  `grafana/dashboards/*.json` → `python3 scripts/gen-grafana-dashboard-cms.py` →
  commit → SSA-apply the CM → the provisioner reloads on a **300 s** cycle (force
  with `kubectl -n verdify-prod rollout restart deploy/verdify-grafana`). Full
  recipe + gotchas: **`docs/grafana-graph-authoring.md`**.

---

## 3. Repo == source of truth for what's deployed (and the drift watch)

| Surface | Source of truth in git | Drift watch |
|---|---|---|
| k8s manifests | `deploy/k8s/overlays/prod` + components | `kustomize build … \| kubectl diff` |
| Image digests | `overlays/prod/kustomization.yaml` (advanced only by `prod-promote`) | live pod `@sha256` == overlay pins |
| Grafana dashboards | `grafana/dashboards/*.json` → generated CMs | **UI edits do NOT sync back — always edit the JSON** |
| DB migrations | `db/migrations/` (sequential) | applied by the `verdify-migrate` Job; CI `migration-rollback-safety` gate |
| Firmware (source) | `firmware/**` | `diagnostics.firmware_version` == intended (see §4) |
| Secrets | SOPS-encrypted `deploy/k8s/*.sops.yaml` (age key in the fleet store) | sealing is partially pending — see §5 |

**Verify "live == main" end-to-end:** git clean + pushed; live pod digests ==
`overlays/prod`; `argocd get verdify-prod-dark` = Synced/Healthy; no open critical
`alert_log` rows; `diagnostics.firmware_version` == the intended build.

---

## 4. Firmware OTA (Jason-gated) — the laptop-tribal knowledge, captured

The OTA is the one workflow not yet portable to a k3s agent (toolchain +
device-VLAN + secrets). Until that's built (§5), the agent prepares the change +
the evidence (replay-diff, invariants, unit-test delta) and an operator runs the
flash. The procedure and its traps:

- **Device:** ESP32 `192.168.10.111` (OTA `:3232`, native API `:6053`). It
  currently runs **`2026.6.17.2042.dcc6078`** (the band-compliance firmware; pinch
  wired, `band_track_fraction=0.25` live).
- **VERIFY the running firmware from telemetry — `diagnostics.firmware_version`
  — NEVER from `firmware/artifacts/last-good.version`.** `last-good` is the
  **rollback floor**, which deliberately lags the running binary through the 48 h
  bake. (Misreading it caused a wrong "device is on the old firmware" conclusion
  mid-2026-06-18; don't repeat it.)
- **Secrets (reconstruct a `0600 secrets.yaml` OUTSIDE the repo):**
  - `ota_password` ← k3s secret `verdify-prod/verdify-firmware-ota`.
  - `api_encryption_key` ← `verdify-app-secrets/ESP32_API_KEY` (the Noise PSK the
    ingestor uses; validates as base64-32B).
  - `wifi_ssid` = **`devices.vallery.net`** (the IoT-Devices WLAN, VLAN 10 /
    192.168.10.0/24); `wifi_password` ← that WLAN's passphrase in the fleet net
    audit (`~/Agents/nexus/state/net-audit-*/raw/wlanconf.json` on the laptop;
    re-home this into a k8s Secret for k3s).
- **THE false-rollback gotcha:** `make firmware-deploy` passes
  `VERDIFY_DB_BACKEND=kube` only to the **preflight**, not to the post-OTA
  `wait-for-firmware-version.sh` / `sensor-health` calls. From a non-laptop host
  those default to docker-exec (no `verdify-timescaledb` container) → the version
  query returns empty → 180 s timeout → the `else` branch **flashes last-good
  back, rolling back a perfectly healthy OTA**. **Mitigation — run the steps by
  hand with the kube backend exported throughout:**
  1. `VERDIFY_DB_BACKEND=kube FIRMWARE_OTA_FREEZE_OVERRIDE_REASON="…" bash scripts/firmware-deploy-preflight.sh`
  2. `FW_VERSION="$(date +%Y.%-m.%-d.%H%M).$(git rev-parse --short HEAD)"; echo "$FW_VERSION" > firmware/artifacts/pending-fw-version.txt`
  3. `ESPHOME_BIN=<esphome> SECRETS_SRC=<secrets.yaml> scripts/firmware-esphome-worktree.sh -s fw_version "$FW_VERSION" compile && … upload --device 192.168.10.111`
  4. `VERDIFY_DB_BACKEND=kube bash scripts/wait-for-firmware-version.sh "$FW_VERSION" --timeout 180` then `VERDIFY_DB_BACKEND=kube EXPECTED_FW_VERSION="$FW_VERSION" make sensor-health SINCE='5 minutes'`
  5. `bash scripts/archive-firmware-artifacts.sh "$FW_VERSION"` — **NOT** `--promote-last-good` (last-good stays on the prior baked binary through the 48 h bake; promote only after).
- **The gates are real** (`scripts/firmware-deploy-preflight.sh`): no OTA while
  `alert_log` has unresolved critical/legacy-high rows; 48 h bake on
  `last-good.ota.bin` mtime; ≤1 OTA/calendar week; telemetry <300 s; action-log
  proof. Freeze overrides need the documented sign-off envs; the **critical-alert
  gate is a plant-safety gate — investigate the alert, don't blind-override it.**
- Full firmware iteration loop: `docs/runbooks/verdify-firmware-safe-iteration-loop.md`;
  freeze rules in `CLAUDE.md`.

---

## 5. Known blockers for FULL k3s autonomy (tracked)

These are why the repo is "self-documenting" but not yet "100% laptop-free." Most
are infra work, gated and tracked — do **not** try to fix them in a code session:

1. **Firmware OTA in-cluster** — needs the esphome/PlatformIO toolchain in a
   builder image, **device-VLAN reachability** (a gated network spike; the
   ingestor is the only sanctioned device-egress path), and the OTA secrets
   re-homed into a k8s Secret. Until then OTA stays operator-run (§4).
2. **Secret sealing / source reconciliation** — `docs/runbooks/verdify-secret-sealing-plan.md`
   lists source path/name mismatches (MQTT_*, HERMES_IRIS_API_KEY,
   API_WRITE_TOKEN↔VERDIFY_WRITE_API_KEY) to reconcile before SOPS/age sealing.
   The age private key lives in the fleet store (root-agent owned).
3. **Lab-site Quartz vault** — the lab build needs the Obsidian vault
   (`/mnt/iris/verdify-vault/website`, Syncthing-synced), which is **not in this
   repo**. A k3s agent needs it NFS-mounted or mirrored. (Lab is not the core
   greenhouse path.)
4. **Orbit context dump** (`/Users/jason/Orbit/context_dump/verdify-platform/`) —
   historical backlog/handoff/evidence, **not in git**. Treat as archive; the
   live tracker is GitHub issues and the durable knowledge is in `docs/`.
5. **Stale VM-era docs** — `docs/SYSTEM-ARCHITECTURE.md` and
   `docs/FOLDER-HIERARCHY.md` describe the retired docker-compose/NFS stack
   (banners added pointing here). Cleanup tracked (#322, #339).
6. **Agent memory** — laptop project-memory does not transfer. The durable,
   repo-relevant bits are captured in this doc + the `docs/` set; k3s agents
   should build their working memory from the repo.

**Tracking issue #381** consolidates these on GitHub, linked to the EPICs #335
(CI/CD hardening), #336 (ArgoCD/GitOps cleanup), #337 (decommission/residual),
and #322/#339 (retired-VM doc/test cleanup).

---

## 6. Durable gotchas (carry these — they were laptop tribal knowledge)

- **`ripgrep`/`rg` is unreliable in this repo** (silently returns empty/misses,
  especially `.sql`). Use `grep -rnE` or Python globs; cross-check any "zero hits."
- **Verify device firmware from `diagnostics.firmware_version`,** not
  `firmware/artifacts/last-good.version` (rollback floor, lags during the bake).
- **The pinched band IS the device's control band today** (device runs
  band-compliance dcc6078, pinch wired @ 0.25). VPD sitting above the band with
  actuators idle = cooling-priority arbitration, not a wider tolerance. ADR-0004
  direction = float (`band_track_fraction → 0`, #377).
- **Never wrap a self-committing migration in an outer `BEGIN..ROLLBACK`** (the
  2026-05-30 live-commit incident). Use `make migration-rollback-safety` +
  `scripts/check_migration_rollback_safety.py`.
- **Schema changes land first, consumers next; one migration at a time;** drift
  guards (`verdify_schemas/tests/test_drift_guards.py`) are the wire protocol.
- **Grafana:** provisioner reload is 300 s; verify from the public `/render`, not
  the localhost port-forward API (it lies); SSA-apply the ~750 KB dashboards CM
  (client-side apply exceeds the etcd annotation limit).

---

## 7. Authoritative doc map

- **This handoff** + `CLAUDE.md` (= `AGENTS.md`) + `README.md` — start here.
- Current control/graphs state: `docs/reviews/control-and-graphs-state-2026-06-18.md`.
- Firmware internals: `docs/firmware-fsm-spec.md`; decisions
  `docs/adr/0004-floating-corridor-control.md` (current) /
  `0003-band-compliance-track-the-target.md` (superseded); band SoT
  `docs/band-traceability-contract.md`.
- Graphs: `docs/grafana-graph-authoring.md` (+ `grafana-brand-system.md`,
  `grafana-panel-catalog.md`).
- Ops runbooks: `docs/runbooks/` — `laptop-operator.md` (the DB/deploy/OTA
  command reference; **commands are kubectl-host-portable** despite the title),
  `prod-promotion.md`, `verdify-firmware-safe-iteration-loop.md`,
  `verdify-secret-sealing-plan.md`.
- Tracker: GitHub issues on `VerdifyConsultancy/verdify-platform`.

---

## 8. State at the handoff (2026-06-18, verified)

Everything live is **end-to-end aligned with `main`**: git clean + pushed; all
prod app pods on the main-latest digests; ArgoCD `verdify-prod-dark` Synced +
Healthy; planner tunable drift cleared (0 violations); device on band-compliance
`dcc6078` (= main behaviorally; no OTA pending — the only firmware delta to HEAD
is a runtime-moot `globals.yaml` cold-start default). No dirty work. The
greenhouse is in production and healthy.
