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
  - `make setup` creates/updates the repo-local `.venv` from `pyproject.toml`
    (Python 3.12+ required; 3.13 preferred for parity).
  - `make lint` (ruff) — required.
  - `make test` (pytest) — required; 1 pre-existing flaky timeout
    (`test_dew_point_risk_computes`) is tolerated, everything else must pass.
  - DB-backed targets default to `VERDIFY_DB_BACKEND=kube` (Makefile) → they hit
    the in-cluster DB. Python tooling runs from the repo `.venv` by default;
    the legacy `/srv/greenhouse/.venv` is used only if it exists or `VENV=...`
    is passed explicitly.
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
- **Deploy / GitOps (one greenhouse environment, plus an isolated static Lab
  canary):**
  ```text
  merge main with make ci green
    → in-cluster verdify-platform-ci / repo-build WorkflowTemplate
       (Kaniko builds the exact revision in agent-fleet-ci)
    → zot origin registry.vallery.net/verdifyconsultancy/<image>@sha256:...
    → digest-only pin PR
    → prod: human merge + Jason-gated manual sync of verdify-prod-dark
    → Astro stage: reviewed lab-stage pin + stage-only rollout + T0/T+10
       acceptance, then restore the manual-sync posture
  ```
  GitHub Actions and GHCR publishing are retired; do not create new GHCR pins.
  See `docs/runbooks/prod-promotion.md` for the current Kaniko→zot procedure.
  The prod sync remains the only step that can touch the live writer.
  The gated sync, from any kubectl host:
  ```bash
  kubectl patch application verdify-prod-dark -n argocd --type merge \
    -p '{"operation":{"initiatedBy":{"username":"<agent>"},"sync":{"prune":false}}}'
  ```
  Pre-check: `kustomize build deploy/k8s/overlays/prod | kubectl diff -f -`; confirm
  the **ingestor Deployment** (the single device writer) only changes when intended.
  Prod promotable set = api/mcp/ingestor/migrate/planner; setpoint-server and
  the current Quartz lab images remain hand-pinned. ArgoCD app
  `verdify-prod-dark` is **manual-sync, prune:false**. The Astro canary is a
  static, no-device/no-DB surface, not a second greenhouse environment. Its
  reviewed zot digest is pinned in
  `deploy/k8s/overlays/lab-stage/kustomization.yaml`.
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

- **Device:** ESP32 `192.168.10.111` (OTA `:3232`, native API `:6053`). As of
  2026-07-13 it runs **`2026.7.10.1500.09ee886`** (the 2026-07-10 software-recovery
  release; pinch machinery still wired, `band_track_fraction` **`0.0` live** — the
  ADR-0004/#377 float, recorded in
  `docs/handoff/software-recovery-deploy-2026-07-10.md`). Any version string
  written in a doc rots — read the live value per the VERIFY bullet below before
  trusting this one.
- **Pinch resets on every flash (#413/#377, verified 2026-07-03):**
  `band_track_fraction` is `restore_value: no` in `firmware/greenhouse/globals.yaml`
  — an OTA/reboot **cold-starts it to the compiled `initial_value`, `0.0` on
  current `main`** (the ADR-0004 float flip), silently dropping any live
  planner-pushed nonzero value (this is how the pre-recovery live `0.25` was
  dropped). The `crop_band_anchors`→NVS reconcile does **NOT** cover it
  (it only re-asserts `restore_value: yes` band globals —
  `docs/CONTROL-ARCHITECTURE.md` §7), and no dispatcher path re-pushes it: the
  registry pins its bounds to `[0.0, 0.0]`, so MCP `set_tunable` rejects a nonzero
  value and the dispatcher clamps a direct `setpoint_plan` row back to 0.0.
  Post-OTA, execute the g-377 pinch decision (re-pin 0.25 — which first requires a
  registry-bounds change — vs accept float 0.0) per the checklist step in
  `docs/RELEASE-CHECKLIST.md` §B "Deploy + post".
- **VERIFY the running firmware from telemetry — `diagnostics.firmware_version`
  — NEVER from `firmware/artifacts/last-good.version`.** The authoritative read
  (any kubectl host):
  `scripts/verdify-db.sh prod -c "SELECT firmware_version, max(ts) FROM diagnostics WHERE firmware_version IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 1;"`
  (live `band_track_fraction`: latest `setpoint_snapshot` row). `last-good` is the
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
  6. **Pinch decision + bake record (#413):** the flash just cold-started
     `band_track_fraction` to `0.0` (see the pinch-reset bullet above). Execute the
     g-377 decision, then record in the bake report: `band_track_fraction`
     (readback proof from `setpoint_snapshot`), `dehum_vent_hold_enabled`
     (`cfg_*` readback; OFF-default flag from #410), and the **envelope config**
     (door screen-window open/closed — open ~2026-06-19 → fall per #412; never
     change it mid-bake). Full step: `docs/RELEASE-CHECKLIST.md` §B "Deploy + post".
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

1. **Firmware OTA in-cluster** — CLOSED 2026-07-13 (operator-directed).
   `deploy/k8s/components/firmware-builder/`: a suspended-CronJob job template
   (`kubectl -n verdify-prod create job --from=cronjob/verdify-firmware-builder …`)
   runs the official esphome image, assembles `secrets.yaml` from k8s Secrets
   (`verdify-firmware-ota`, `verdify-app-secrets/ESP32_API_KEY`,
   `verdify-firmware-wifi`, `verdify-github-token`), compiles with the
   toolchain cached on a PVC, archives to the `verdify-firmware-artifacts`
   PVC (**the rollback floor `last-good.ota.bin` now lives THERE, not on the
   laptop**), and — only with `FLASH=1`, which stays Jason-gated — uploads to
   192.168.10.111:3232 through its own scoped egress NetworkPolicy. The
   preflight/verify steps (`firmware-deploy-preflight.sh`,
   `wait-for-firmware-version.sh`, `make sensor-health`) already run
   kube-backend from any cluster pod.
2. **Secret sealing / source reconciliation** — `docs/runbooks/verdify-secret-sealing-plan.md`
   lists source path/name mismatches (MQTT_*, HERMES_IRIS_API_KEY,
   API_WRITE_TOKEN↔VERDIFY_WRITE_API_KEY) to reconcile before SOPS/age sealing.
   The age private key lives in the fleet store (root-agent owned).
3. **Lab Astro dynamic publishing / production cutover** — PARTIAL, tracked by
   #351 and `docs/plans/lab-astro-migration.md`. The Quartz publisher is
   S3-backed, and the accepted Astro stage hydrates a digest-bound sanitized
   snapshot before Kaniko; its static runtime needs no vault/NFS mount, PVC,
   Secret, DB, Grafana, or egress. Full autonomy and cutover still require the
   event-driven S3 release adapter, graph occurrence exporter/reporting tier,
   privacy-approved camera sanitizer/fallback, exact same-snapshot parity, and
   Jason-gated production cutover/Quartz retirement. Do not add a new
   `/mnt/iris` dependency.
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
- **The pinched band IS the device's control band** (pinch machinery wired since
  band-compliance `dcc6078`; since the 2026-07-10 recovery OTA `09ee886` the live
  `band_track_fraction` is `0.0` — the ADR-0004/#377 float — so the pinched band
  currently equals the full band). VPD sitting above the band with actuators
  idle = cooling-priority arbitration, not a wider tolerance.
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
