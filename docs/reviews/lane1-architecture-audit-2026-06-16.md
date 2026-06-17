# Lane 1 — Greenhouse Stack Architecture Audit & Drift Check

**Date:** 2026-06-16 · **Lane:** L1 (issue #343), milestone **G0 — Controller Architecture Audit** · **Method:** read-only.

This is the actual-vs-intended architecture map for the Verdify greenhouse stack, the
drift findings, the dead/stale/obsolete inventory, the CI/CD + firmware HIL assessment,
the deployment failure modes + HA fallback design, and a phased simplification plan.

It **builds on and reconciles** `docs/reviews/data-path-adversarial-review-2026-06-16.md`
(the control-loop deep-dive). Where this audit corrects that doc, it is flagged in §11.

**How it was produced:** five parallel read-only investigators — deployed-state-vs-repo,
dead-code sweep, dashboards+lab, CI/CD+firmware-HIL, data-reliability+HA-fallback — plus
the existing data-path review. Live cluster facts were captured from `kubectl` (context
`vallery`, ns `verdify-prod`) on 2026-06-16/17. Items needing a live DB/device to confirm
magnitude are tagged **[unverified]**.

---

## 0. Executive summary

**The architecture intent is sound and mostly realized.** Deterministic DB band
(`crop_band_anchors`) + AI tactical tuning + single-writer dispatcher + offline-first
firmware under hard temp rails. The cluster is structurally clean: dev, staging, and the
inert CNPG cluster are **fully gone** (verified — no namespaces, no `Cluster` CR), and every
serving image matches its git pin.

**The gaps cluster in five places:**

1. **Reliability — the single biggest risk: no DB replica, no PITR.** CNPG was removed;
   the live DB is a single-replica StatefulSet on Longhorn RWO. RPO ≤ 24 h (nightly
   `pg_dump` only), RTO unpracticed. A node-7 volume-loss event = up to a day of data loss
   and an untested restore. (§7, §8)
2. **Reliability — the writer's own watchdog runs inside the writer.** There is no
   out-of-band "ingestor down / writer absent" alert emitted from this repo; the staleness
   monitor that would detect a dead writer runs *in* the ingestor. The single live writer
   has **no liveness probe**, sits on the **flaky node6** (observed two FailedMount rolls in
   ~50 min), and the writer-lease fence is **inert**. (§7, §8)
3. **CI/CD — the band-curve blind spot is not gated.** The documented failure class (a
   lumpy/wet-night curve shipping blind) is caught only by the `firmware-replay-band` check,
   which in CI is **informational-only** (`THRESHOLD_PCT=100 … || true`). The replay corpus is
   ~7 weeks stale with no freshness gate, and there is **no hardware-in-the-loop test** at all.
   (§6)
4. **Observability truth — dashboards plot the DB-derived band, not device truth.** The
   device's resolved band readback (`setpoint_snapshot`) exists in the DB but only **one**
   live dashboard reads it; the headline compliance panels re-derive the band from
   `fn_band_timeline`, so a failed NVS push or firmware/DB skew shows GREEN on the public
   homepage. (§3, §4, prior review F2)
5. **Single-source-of-truth — defaults live in ≥4 unreconciled stores.** There is no
   `config.yaml`; band/tunable defaults are copied across firmware `globals.yaml`, firmware
   `tunables.yaml`, `tunable_registry.py`, and DB `crop_band_anchors`, reconciled by only a
   partial CI guard. (§3, prior review §3/F1/F20)

**Headline deletables (high confidence, low risk):** 13 zero-reference scripts; the VM-era
dirs `systemd/ promtail/ goaccess/` + repo-root `traefik/ mqtt/` + `docker-compose.twins.yml`
+ `project-assets.yml` + `catalog-info.yaml`; the 145/146 deprecated band-function family now
orphaned by migration 171; 34 dead Grafana dashboards + 20 divergent `site-*.json` shadow
copies; `ingestor/templates.py`. (§4)

**Headline doc fixes:** `SERVICE_MAP.md` still documents the deleted Dev/Staging envs as live;
`db/schema.sql` is an internally-inconsistent stale dump; the BCDR doc references the
decommissioned `.150` VM. (§3)

---

## 1. Intended architecture (the design, from README/SERVICE_MAP)

```
ESP32 controller (8-state greenhouse_logic.h, 5 s loop, on-chip band from NVS anchors)
  ├── aioesphomeapi ──→ Ingestor (sole writer) ──→ TimescaleDB
  ├── MQTT ──→ Mosquitto (telemetry fan-out, NOT a control path)
  └── setpoints pushed back from Ingestor dispatcher every ≤5 min

TimescaleDB (system of record: telemetry, band anchors, views, scorecards, lessons)
  ├── Grafana   (graphs.verdify.ai — read-only dashboards)
  ├── FastAPI   (api.verdify.ai — crop catalog + legacy /setpoints export)
  ├── MCP        (mcp.verdify.ai — typed planner tools)
  └── Quartz lab (lab.verdify.ai — static site, embeds Grafana panels)

Iris planner (Hermes + GPT-5.5, MCP-only) → set_tunable/set_plan → setpoint_plan → dispatcher
```

Three control layers: **crop band** (the targets, from `crop_band_anchors`) → **AI planner**
(tactical tunables: how hard to chase the band) → **ESP32 state machine** (enforces, under
hard safety rails). The crops set targets, the AI tunes tactics, the controller enforces,
the telemetry proves.

---

## 2. Actual architecture — component ledger

**Verdict legend:** `AUTHORITATIVE` = system of record for its data · `FALLBACK` = secondary/
degraded-mode source · `LIVE-OK` = deployed and matches intent · `STALE` = live but drifted/
out-of-date · `DEAD` = unused, delete candidate · `DARK` = built but not deployed (gated) ·
`REWRITE` = live but needs redesign.

### 2.1 Firmware layer

| Component | Writes | Reads | Role | Verdict |
|---|---|---|---|---|
| ESP32 controller (`firmware/greenhouse/*`, `firmware/lib/greenhouse_logic.h`, `greenhouse_solar.h`) | relays (fans/heat/mist/fog/lights), telemetry + `cfg_*` readback over native API | NVS band anchors, pushed tunables, on-board sensors | **AUTHORITATIVE** real-time control; computes its own band on-chip every ~5 s (`sw_onchip_band_enabled=true`) — offline-first | LIVE-OK |
| Legacy firmware cascade (`greenhouse_logic.h` legacy path) | — | — | superseded by `determine_mode_band_first`; VPD safety rails referenced only here | **DEAD** (prior review F7) |
| `firmware-twin` (`deploy/k8s/components/firmware-twin/`, `twin/`) | `twin_decisions` (INSERT-only) when enabled | prod telemetry | read-only shadow; **not in prod overlay, not in CI** | **DARK** (gated) |

### 2.2 Services (k3s `verdify-prod`)

| Workload | Kind | Writes | Reads | Role / authority | Verdict |
|---|---|---|---|---|---|
| `verdify-ingestor` | Deploy `replicas:1`, Recreate | `climate`, `equipment_state`, `system_state`, `diagnostics`, `setpoint_changes`, `setpoint_snapshot`, `weather_forecast`, `alert_log`, delivery tables | DB band/zone/lighting policy fns; ESP32; HA; Open-Meteo; MQTT | **AUTHORITATIVE** — the sole ESP32 writer + the dispatcher + the alert engine | LIVE-OK (but see §8: on node6, no probe, lease inert) |
| `verdify-api` | Deploy 2× | `public_contact_submissions` | crop/topology tables + status views/fns | FastAPI crop catalog (`api.verdify.ai`); `/setpoints` export is **legacy, no live consumer** | LIVE-OK; `/setpoints` DEAD |
| `verdify-mcp` | Deploy 2× | `setpoint_plan` (via `set_tunable`/`set_plan`) | planner/control state | typed planner tool surface (`mcp.verdify.ai`) | LIVE-OK |
| `verdify-planner` | Deploy 2× | `planner_graph_runs`, `planner_memory_*` | run store, memory, forecast | planner run store; reached by Hermes + cron replan | LIVE-OK (hand-pinned, not auto-bumped) |
| `verdify-hermes-iris` | Deploy 1× | gateway state (PVC) | MCP tools, OpenAI | LLM planner gateway | LIVE-OK |
| `verdify-setpoint-server` | Deploy 1× | `setpoint_changes`, HA grow-light service calls | lighting policy fns, `equipment_state` | prod-only grow-light writer/diagnostics — **a second device-affecting path** (via HA, not ESP32 native) | LIVE-OK (note: not covered by the ESP32 single-writer invariant) |
| `verdify-mqtt` | Deploy 1× | — | — | Mosquitto fan-out; **secondary telemetry, not control** | LIVE-OK |
| `verdify-grafana` | Deploy 1× | — | TimescaleDB (read-only) | `graphs.verdify.ai` dashboards | LIVE-OK (but headline panels read DB-derived band — §4) |
| `verdify-lab` | Deploy 2× | — | lab cache PVC | static Quartz site (`lab.verdify.ai`) | LIVE-OK |
| `verdify-lab-publisher` | CronJob `*/10` | lab cache PVC, S3 prefixes | S3, TimescaleDB | regenerates lab content | LIVE-OK; **intermittent Error on node6** (§3) |
| `verdify-db` | StatefulSet 1× | system of record | — | TimescaleDB `2.25.2-pg16` | LIVE-OK but **no replica/PITR** (§7) |
| `verdify-traefik` | Deploy 2× | — | — | tier-2 in-ns edge | LIVE-OK |
| `verdify-migrate` | PreSync Job | replays schema + migration bootstrap | `db/migrations/` | ephemeral; not the prod data path | LIVE-OK |
| `verdify-band-curve-refresh` | CronJob `*/10` | `mv_band_curve` | `crop_band_anchors` | refreshes band matview (timer, not anchor-triggered) | LIVE-OK |
| `verdify-db-backup` | CronJob `02:17` | NFS dumps PVC | DB (read-only) | nightly `pg_dump -Fc`, 14 d retention | LIVE-OK (RPO ≤24 h) |
| `verdify-db-backup-exporter` | Deploy 1× | — | dumps PVC mtime | backup-freshness metric | LIVE-OK |
| `verdify-db-watchdog` | CronJob `*/2` | deletes `verdify-db-0` only on a narrow signature | DB endpoints/logs | remount-race healer (NOT DB HA) | LIVE-OK (narrow) |
| `verdify-ha-gap-backfill` | CronJob `:23` | `climate`, `diagnostics`, `setpoint_snapshot`, `energy`, `equipment_state`, `system_state` | HA recorder | telemetry gap reconciler (**analytics-only**, §7) | **STALE image** — runs old ingestor digest (§3) |
| `verdify-writer-exporter` (ns `observability`) | DaemonSet 7× | — | ESP32 conn estab | out-of-band single-writer oracle (`verdify_esp32_writer_estab`) | LIVE-OK |

**Deployed-but-undeclared:** none. **Declared-but-undeployed (intentional):** `firmware-twin`,
`umami` (component dirs present, not in `overlays/prod/kustomization.yaml`).

### 2.3 Config / default stores (the "source of truth" surface)

| Store | Authoritative for | Verdict |
|---|---|---|
| DB `crop_band_anchors` (4 solar anchors → `fn_crop_band_value` → `fn_band_setpoints`) | **the live band** the dispatcher syncs to the device | AUTHORITATIVE |
| `verdify_schemas/tunable_registry.py` (`REGISTRY`, `_FW2_*`) | Python control-plane default + bounds; **dispatcher fallback** | AUTHORITATIVE (control plane); `_FW2_*` band defaults **STALE** vs DB+firmware (prior review F1) |
| firmware `globals.yaml` `initial_value` | device cold-start / NVS seed | LIVE-OK (but not guarded against registry/DB) |
| firmware `tunables.yaml` clamp bounds | what the device will accept | LIVE-OK (CI-guarded vs registry clamps) |
| `crops.target_vpd_low/high` (mig 162) | nothing live | **DEAD** (stale 3rd copy of the per-zone band) |
| `config/zones.yaml`, `config/ai.yaml` | defaults only; `.dockerignore` excludes from images | STALE/INVESTIGATE (not shipped to prod) |
| `hermes/iris/config.yaml` (root, canonical) vs inline copy in `components/hermes-iris/hermes-config.yaml` | Hermes config | **duplicated, no guard** — drift risk |

### 2.4 External systems

| System | Used by | Authority | Verdict |
|---|---|---|---|
| ESP32 `192.168.10.111:6053` | ingestor, firmware validation | AUTHORITATIVE real-time controller | LIVE-OK |
| Home Assistant `192.168.30.107:8123` | ingestor (telemetry + backfill), setpoint-server (grow-light service calls) | **FALLBACK** telemetry source (analytics-only); grow-light actuation path | LIVE-OK (but not independent of ESP32 upstream — §7) |
| Tempest (via HA) | lighting decisions (outdoor lux) | authoritative outdoor lux (replaces dead indoor LDR) | LIVE-OK |
| Open-Meteo | ingestor forecast sync | authoritative forecast | LIVE-OK (degrades planner only) |
| MQTT/Mosquitto | ingestor fan-out | secondary telemetry | LIVE-OK |
| S3 | lab publisher | lab content/public/state | LIVE-OK |
| Cloudflare / shared Traefik / DNS | public hosts | edge (out-of-lane) | LIVE-OK |
| Slack | ingestor alerts, Hermes | alert emission | LIVE-OK |
| GHCR | all app images | image registry | LIVE-OK |
| NAS / NFS | DB dumps, PVCs | storage (out-of-lane) | LIVE-OK |

### 2.5 Layer ownership — what belongs where

| Concern | Belongs in | Reality |
|---|---|---|
| Real-time relay control, safety rails, on-chip band reconstruction | **Firmware** | ✓ correct |
| Single-writer dispatch, telemetry capture, alerting, confirmation loop | **Service (ingestor)** | ✓ correct (but the writer-watchdog wrongly lives *inside* the writer — §8) |
| Tactical tunables (how hard to chase the band) | **Service (planner/MCP)** | ✓ correct |
| The target band itself | **DB (`crop_band_anchors`)** | ✓ correct (but copied into 3 other stores — §3) |
| Live operational + compliance views | **Dashboard (Grafana)** | ⚠ headline panels read DB-derived band, not device truth (§4) |
| Narrative / evidence / plan pages | **Lab notebook (Quartz)** | ✓ but coupled to legacy dashboard UIDs (§4) |

---

## 3. Drift findings (actual ≠ intended / declared)

### Deployed drift
- **D1 — `verdify-ha-gap-backfill` runs a stale ingestor digest.** Live `verdify-ingestor@99efdf`,
  git pin `@f8e034`. Root cause: the gated `argocd app sync verdify-prod-dark` has not run since
  `main` HEAD bumped the ingestor pin (commits `291cf5c`/`950834a`). **Fix: operator runs the
  gated sync** (Jason gate — touches the device-write app).
- **D2 — ArgoCD `verdify-prod-dark` = OutOfSync/Degraded.** 5 resources: the D1 CronJob (real);
  `Deployment verdify-ingestor` (live image matches git; diff is the recently-added
  `verdify-ingestor-state` PVC volume wiring not yet reconciled); 3 PVCs (Bound + present;
  metadata/managed-field divergence from manual recreation). Health=Degraded is driven by the
  failed lab-publisher job, not a serving outage.
- **D3 — `verdify-lab-publisher` Error pods (node6).** Both failed attempts landed on
  `vm-k3s-node6` and exceeded the 20-min `activeDeadlineSeconds`; every success ran on node4 in
  ~3.5 min. **Not a code/image defect — node6 storage-mount (iSCSI/NFS) flakiness.** Same node
  is churning the ingestor pod.

### Doc drift
- **D4 — `docs/SERVICE_MAP.md` "Grafana band curve refresh" row was marked retired but the job is
  LIVE.** Line 69 read "Historical/retired — Dropped by PR #329; do not recreate," yet
  `components/grafana/band-curve-refresh-cronjob.yaml` exists, is referenced by
  `components/grafana/kustomization.yaml:31`, renders into the prod overlay, and the live CronJob
  ran at `2026-06-17T00:50Z` on `*/10`. Doc drift in the dangerous direction (operator would think
  a live every-10-min job is gone). **→ fixed in this audit pass (see §10).** (The Dev/Staging env
  rows, dev routes, and dev DB-restore references the dead-code/deployed-state investigators flagged
  had **already** been corrected to the single-prod-env model in a prior commit — no longer drift.)
- **D5 — `db/schema.sql` is an internally-inconsistent stale dump.** It carries mig-176 objects
  (`fn_lighting_*`) yet lacks 161/167/171/178 (`crop_band_anchors`, `mv_band_curve`,
  `v_band_device_divergence`) and still has the OLD clamped `fn_band_setpoints` body. Not a
  faithful snapshot of any point; misleads every reviewer. The migration chain (000→179) is the
  only authority. **→ regenerate from a post-179 dump.** (prior review F13, sharpened)
- **D6 — `docs/BCDR-AND-OPERATIONS.md` references the decommissioned `/mnt/iris` `.150` VM paths**
  for restore; the actual path is the `verdify-db-dumps` NFS PVC.

### Config drift (single-source violations)
- **D7 — defaults live in ≥4 stores** (no `config.yaml`): firmware `globals.yaml`, firmware
  `tunables.yaml`, `tunable_registry.py`, DB `crop_band_anchors` (+ stale `crops.target_vpd_*`).
  Only a partial CI guard reconciles registry↔`tunables.yaml` clamps; **no guard** for registry
  `default`↔firmware `initial_value` or registry band defaults↔DB anchors. (prior review §3/F20)
- **D8 — greenhouse coordinates exist in 4 slightly-different copies:** `ingestor/solar.py`
  (`40.167/-105.102`), `firmware/lib/greenhouse_solar.h` (matches solar.py), `ingestor/config.py`
  (`40.1672/-105.1019`), `config/zones.yaml` (`40.1672/-105.1019`, elevation `5003` vs README
  `5,090`). Immaterial physically; SoT violation.
- **D9 — hermes config duplicated** (root `hermes/iris/config.yaml` canonical vs inline k3s copy),
  no guard.
- **D10 — `set_band_anchor` service string** duplicated as two constants
  (`esp32_push.py:23` vs `band_anchors.py:165`); datasource UID `verdify-tsdb` + `'vallery'`
  greenhouse_id hardcoded across ~80 dashboard JSONs. (prior review F18)

### Dashboard / observability-truth drift
- **D11 — headline band/compliance panels plot the DB-derived band (`fn_band_timeline`), not the
  device readback (`setpoint_snapshot`).** The device-resolved band readback exists in the DB
  (≈117k rows, current) but is read by **only one** live dashboard (`site-climate-controller`).
  Compliance shows GREEN against a DB re-derivation; a failed NVS push or firmware/DB skew is
  invisible on the public homepage. (prior review F2, corrected severity)
- **D12 — `mv_band_curve` refresh is timer-based, not anchor-change-triggered.** A
  `crop_band_anchors` edit can be up to 10 min stale on the homepage; no freshness tile.
- **D13 — no env banner on any live dashboard** — prod `graphs.verdify.ai` is visually
  indistinguishable from the retired `graphs-dev`. (prior review F17)

---

## 4. Dead code / stale data paths / obsolete dashboards (deletion inventory)

### 4.1 `scripts/` — 13 zero-reference files (delete candidates)
No reference anywhere in repo (verified `rg -F`): `data-path-postdeploy-verify.sh`,
`firmware-replay-setpoint-coverage-check.sh`, `generate-plans-index.sh` (superseded by the `.py`),
`stage-rollback-floor-refresh.sh`, `transcode-launch-video.sh`, `backfill-nexus-infra-metrics.py`,
`compute-grow-light-daily.py`, `crop-parser.py`, `render-grafana-embed-audit.py`, `shelly-sync.py`,
`slack-post.py` (superseded by `slack_ops/`), `vault-harvest-writer.py`, `vault-treatment-writer.py`.

A second tier is referenced only by docs/migration-comments/tests (lower confidence) — see the
dead-code investigator output; treat as INVESTIGATE. Note `gen-grafana-dashboard-cms.py` has **0
refs but is NOT dead** — it is the live dashboard generator (invoked manually; a drift risk worth
wiring into CI/Make).

### 4.2 VM-era leftovers (delete)
| Path | Verdict | Evidence |
|---|---|---|
| `systemd/` (units, crontab, logrotate) | **DELETE** | zero live refs; k3s uses Deployments/CronJobs |
| `promtail/`, `goaccess/` | **DELETE** | no live ref |
| repo-root `traefik/` | **DELETE** | live edge is `overlays/prod/traefik/` |
| repo-root `mqtt/` | **DELETE** | live broker is `components/mqtt-broker/` |
| `docker-compose.twins.yml` | **DELETE** | only a comment in `twin/Dockerfile` |
| `project-assets.yml`, `catalog-info.yaml` | **DELETE** | Backstage descriptors, no live ref |
| `docker-compose.yml` | INVESTIGATE | not a build input; only a CI path-filter heuristic + porting comments |
| repo-root `grafana/dashboards/` | **KEEP** | source-of-truth read by `gen-grafana-dashboard-cms.py`; prune unused `grafana/custom/` + `grafana/provisioning/datasources/` |
| `config/`, `templates/` | INVESTIGATE | `.dockerignore` excludes from images; defaults-only |
| `slack_config.py`, `slack.yaml` | **KEEP** | COPY'd into ingestor/mcp/lab images |

### 4.3 Dead / suspect Python modules
`ingestor/templates.py` (no importers), `verdify_schemas/crop_profiles.py` (`CropTargetProfile`
superseded — grading-only now), `ingestor/ingestor-healthz.py` (intended probe but the ingestor
Deployment has **no probe configured** — see §8). Drift-guard-protected schema modules
(`forecast_ops`, `media`, `system_infra`, `views`) look readerless but **must not be deleted
blindly** — `test_drift_guards.py` covers them.

### 4.4 DB orphan objects (extending the prior review)
> **CORRECTED 2026-06-16 by a live prod dependency scan** (`pg_get_functiondef` ~ name match,
> read-only). The static dead-list below over-reached. Only **two** of the 145/146 band-function
> family are real leaf-orphans; the rest are either already gone or **still in the live band chain**
> — `fn_band_timeline`/`fn_band_trace`/`fn_setpoint_at`/`fn_band_setpoint_provenance` →
> `fn_house_vpd_control_band` → `fn_center_band_setpoints` → `fn_diurnal_interp`. Dropping those
> would have broken the band the dashboards + API compute (a P0 averted by verifying first).

| Object | Live-DB verdict |
|---|---|
| `fn_achievable_envelope`, `fn_active_noncenter_stress` | **0 refs, no app/Grafana caller → DROPPED in migration 180** |
| `fn_house_vpd_control_band` | **LIVE** (4 referencing fns incl. fn_band_timeline/trace) — keep |
| `fn_center_band_setpoints`, `fn_diurnal_interp` | **transitively LIVE** (in the band chain) — keep |
| `fn_target_band`, `fn_target_band_smooth`, `v_target_curve` | already absent in prod (no-op) |

Still worth a (separately-verified) look: `v_band_trace_latest`/`v_band_trace_recent` (API uses the
function directly), `v_daily_oscillation*` (retained, no reader), `crops.target_vpd_low/high` (stale
3rd band copy), and the never-applied `160-orchid-vpd-band-realign-PROPOSAL.sql`. **Dangling
reference:** `v_setpoint_compliance` was DROPped by mig 149 but
`grafana/provisioning/dashboards/json/greenhouse-hvac-climate.json` still queries it (a dead
dashboard, harmless, fix on cleanup). **Lesson:** verify DB-object liveness against the running DB,
not static grep — the band chain is more connected than the call sites suggest.

> Correction to the prior review: **`mv_band_curve` (F15) is NOT orphaned** — it is refreshed every
> 10 min by `band-curve-refresh-cronjob.yaml` and read live via `v_band_curve` by the live
> `site-home` dashboard. And **`GL_CIRCUIT_TARGETS` (F5) was removed 2026-06-16.** See §11.

### 4.5 Dashboards — 26 live, 34 dead, 20 shadow foot-guns
Grafana provisions only the JSON baked into `components/grafana/generated/dashboards-cm-{0..3}.yaml`
(generated by `gen-grafana-dashboard-cms.py`, which prefers `grafana/dashboards/` over
`grafana/provisioning/dashboards/json/`). **26 dashboards are LIVE; the other 34 JSONs in
`grafana/provisioning/dashboards/json/` never deploy.**
- **DELETE the 34 dead JSONs** (`canonical-*`, `role-*`, `control-loop`, `esp32-controller`,
  `homepage*`, etc.) — or formally mark that dir a staging library. Notably,
  `firmware-twin-divergence` and `data-trust-ledger` already exist there as JSON — **provision
  these** rather than building new divergence views.
- **DELETE the 20 `provisioning/json/site-*.json` shadow copies.** 17 of 20 diverge byte-for-byte
  from the live `grafana/dashboards/site-*.json`; editing the shadow changes nothing in prod — a
  live edit foot-gun.
- **REPOINT** `site-home` + the `fn_band_timeline` compliance panels to `setpoint_snapshot` device
  readback (D11); add an env banner (D13).

### 4.6 Stale lab-notebook generators
- `generate-baseline-vs-iris-page.py` — **frozen one-shot**, hardcodes the Apr 22–May 2 2026
  comparison windows; regenerates a stale April page forever. **Retire or parameterize.**
- `render-crop-profiles.py` / `render-zone-pages.py` — hardcode the crop universe, `'vallery'`
  greenhouse_id, and `STALE_SEEDLING_AFTER_DAYS=35` in two places.
- `generate-forecast-page.py` — hardcodes Grafana embed UID `greenhouse-weather` + panelIds; this
  coupling is the **only reason cm-3 legacy dashboards are kept alive**. Converge lab embeds onto
  the `site-*` UIDs so cm-3 can retire.
- `generate-ai-tunables-page.py` / `site-evidence-operations` — read `setpoint_changes` with no
  `source` filter → counts include frozen `source='band'` rows. **[unverified]** magnitude.

`site/content/` is **empty in the repo** — lab content is generators + S3, not git. No references
to the retired `.150` VM / `verdify-dev` / `live/platform-main` / sprint numbers were found (good).

---

## 5. What writes / reads / authoritative / fallback (one-glance)

- **Writes the device:** `verdify-ingestor` (sole ESP32 native-API writer, via `push_to_esp32`
  chokepoint) and `verdify-setpoint-server` (grow-light circuits via HA service calls — a *second*
  device-affecting path, outside the ESP32 single-writer invariant). The ESP32 itself writes
  relays.
- **Writes the DB:** ingestor (telemetry + control echo + alerts), MCP (`setpoint_plan` via
  planner), planner-graph (runs/memory), setpoint-server (lighting), HA-gap-backfill (telemetry
  only), lab-publisher (none — S3/PVC), API (`public_contact_submissions`).
- **Authoritative band:** DB `crop_band_anchors`. **Authoritative real-time control:** the ESP32
  on-chip band engine. **Authoritative tunable defaults (control plane):** `tunable_registry.py`.
- **Fallback:** HA recorder (telemetry backfill, **analytics-only**, not control); registry
  defaults (dispatcher fallback when DB unreadable — now **fail-closed** after the 2026-06-16 F1
  fix); firmware NVS band (offline-first when the dispatcher is silent).
- **Reads only:** Grafana, the lab site, the API crop catalog.

---

## 6. CI/CD model — current, gaps, and a firmware-aware target

### 6.1 Current pipeline
```
push main ─┬─ ci.yml                (lint · schemas+drift · device-write-gate · firmware compile/logic/replay/invariants · migration-rollback-safety)
           ├─ container-publish.yml  (image-impact → reusable build → smoke-import → GHCR digest, tags sha-<sha> + branch-main)
           ├─ k8s-manifests.yml      (kustomize build + kubeconform per overlay)
           └─ cnpg-image.yml         (CNPG operand image — paths-gated)
                          NO environment write-back
[human dispatch] prod-promote.yml → resolve :branch-main digests → surgical bump overlays/prod
                 → Device-Write-Safety-Gate (render-equality, digests-only) → open prod-promote PR
                          → promote-diff-guard.yml (change-surface containment) + ci re-run
                          → [human] merge (git only)
                          → [Jason-gated operator] argocd app sync verdify-prod-dark
FIRMWARE (separate, never in the image pipeline): local `make firmware-deploy` → preflight gates
   → esphome compile + OTA → 60 s wait → wait-for-version → sensor-health → pass: archive+pin /
   fail: auto firmware-rollback to last-good.ota.bin
```

### 6.2 CI gates inventory
| Gate | What it checks | Status |
|---|---|---|
| `migration-rollback-safety` | flags self-committing migrations touched in a PR | **strong** |
| `device-write-gate` | mocked aioesphomeapi → zero device writes when gate off | **strong** |
| drift guards | layer-boundary wire protocol vs CI Postgres | **strong** |
| `firmware-replay-diff` | THRESHOLD_PCT=0 mode/relay divergence | present, **path-scoped** — triggers only on `logic.h`/`types.h`/`controls.yaml`; a `greenhouse_solar.h` band-curve change does **not** trip it |
| band-curve behavioral diff (`firmware-replay-band`) | derives setpoints from the curve | **informational only** (`THRESHOLD_PCT=100 … \|\| true`) — the documented blind spot is **not gated** |
| firmware compile | `esphome config` (YAML validate, not a full ESP-IDF build) | **weak** |
| firmware invariants | 18+ invariants over the checked-in corpus; rc=2 → warning, exit 0 | present; **soft-skip** silently disables on a schema-mismatched corpus |
| `no-new-fire-and-forget` | new tunables need a `cfg_*` readback | present; **weak** (indentation-sensitive awk) |
| `service-restart-drift-guard` | schema-touch PR must mention restart | present; **weak** (keyword grep, PR-only — push-to-main bypasses) |
| corpus-freshness gate | — | **MISSING** (corpus ~7 weeks stale) |
| full `tests/` suite (incl. twin src-sync `test_19`) in CI | — | **MISSING** — CI runs only `test_02`, `device_write_gate`, `migration_rollback_safety` |
| registry↔firmware↔anchors default guards | — | **MISSING** (D7 / prior review F20) |

### 6.3 Hardware-in-the-loop assessment
**There is no automated HIL test.** Firmware validation is 100% offline replay against a recorded
corpus + native C++ unit tests of the shared logic. The only real-device contact is the
human-gated post-OTA `sensor-health` sweep — on **production hardware**, after the fact. The
`firmware-twin` shadow exists but is **merged-but-dark** (not in prod, not in CI). Risks: corpus
staleness, the un-gated band-curve blind spot (the exact wet-night-curve class), corpus missing
`eq_fertilizer_master`/`feed_hold_active` (invariants #18–22 vacuously pass), `esphome config` ≠
real compile, and OTA password not yet in a k3s secret (auto-rollback depends on the dead `.150`
source).

### 6.4 Proposed reliable CI/CD + HIL model (incremental, by ROI)
**A — close firmware blind spots (high ROI, low effort):**
1. Promote `firmware-replay-band` to a **blocking** gate when `greenhouse_solar.h`/anchor
   resolution changes (drop `|| true`, sane THRESHOLD_PCT, PR-body override for intentional curve
   changes — same pattern as `firmware-replay-diff`).
2. Add a **corpus-freshness gate** (fail/require-ack if corpus max-ts > ~21 days) and schedule
   `make replay-corpus-refresh` weekly on a fleet host → auto-PR the refreshed `.csv.gz`.
3. Remove the rc=2 soft-skip (or make it a hard fail with a "refresh corpus" instruction).
4. Export `eq_fertilizer_master` + `feed_hold_active` in `export-replay-overrides.sh` so
   invariants #18–22 enforce over real history.
5. Run the broad `tests/` suite (at minimum `test_19_firmware_twin_shadow_src_sync.py`) in CI.

**B — hardware-in-the-loop (medium effort):**
6. **Wire `firmware-twin` as a continuous shadow first** — stand it up INSERT-only in a non-device
   namespace reading prod telemetry, compare twin-vs-live decisions, surface a divergence metric +
   alert. This is "HIL without a second device" and catches the seasonal drift the static corpus
   misses. (DB schema bits stay Jason-gated.)
7. *(optional, higher effort)* a bench ESP32 on a self-hosted runner that takes the OTA candidate
   and runs a scripted scenario sweep before the live OTA.

**C — coordination / hardening:**
8. Strengthen `service-restart-drift-guard` to a structured required line
   (`Post-merge restart: verdify-mcp, verdify-ingestor`) checked against the actual changed
   schema surface, and fire it on push-to-main too.
9. Tighten `no-new-fire-and-forget` to parse YAML, not indentation.
10. Seal `ota_password` into a k3s secret so auto-rollback never depends on the dead `.150` host.

A draft **release checklist** for firmware + services + dashboards + docs is in
`docs/RELEASE-CHECKLIST.md`.

---

## 7. Deployment failure modes + HA fallback/backfill

### 7.1 Failure-mode table
| Failure | What happens now | Detected? latency | Recovery | Gap |
|---|---|---|---|---|
| ESP32 disconnects | keepalive/on_stop clears client; pushes no-op; firmware runs on-chip | ≤60 s (keepalive) | auto-reconnect | ≤60 s silent TCP death window |
| ESP32 reconnects | re-enumerates, force-push reconciles drift, immediate dispatch, logs `data_gaps` | yes | auto | gap telemetry not reconstructed here (HA job's job) |
| DB unreadable | writes try/except + continue; telemetry buffered in-memory (lost on restart); ESP32 keeps running on-chip | partial (the monitor needs the DB) | DB self-heal / watchdog | **no bounded buffer/WAL → permanent telemetry loss for the window**; `setpoint_listener` conn has **no reconnect** |
| Push partial/failed | `push_to_esp32` breaks on first exception, returns short count; dispatcher retries 3× then 1 warning alert | `esp32_push_failed` + `setpoint_unconfirmed` 5/15 min | auto next cycle | later params unpushed until next 300 s cycle |
| Ingestor pod rescheduled (Recreate) | mandatory zero-writer window; **RWO Longhorn PVC detach/reattach race amplifies to minutes** (observed FailedMount on node6) | `sensor_offline` 2× interval | auto, slow | **no liveness probe → wedged pod never restarted**; STATE_DIR is non-critical regenerable cache |
| MQTT down | reconnect loop | log only | auto | no dedicated alert |
| Open-Meteo down | timeout → None → no-op | `sensor_offline`(forecast) ≤300 s | auto hourly | degrades planner only |

### 7.2 HA fallback / backfill — what it really is
`ha-gap-backfill` (hourly, `backfill-ha-gaps.py`) reconciles **telemetry recorder gaps** into
`climate`, `diagnostics`, `setpoint_snapshot`, `energy`, `equipment_state`, `system_state`.
DB-first gap detection (per-table sampling buckets, gap ≥ max(10 min, 2× cadence)), idempotent,
advisory-locked, ≤12 windows/run. **It is genuinely well-built** for telemetry/analytics
continuity. **But:**
- **It is analytics-only — NOT a control fallback.** It explicitly excludes `climate_action_log`,
  `setpoint_changes`, `setpoint_plan`. A gap loses the *decision* record even when telemetry is
  reconstructed.
- **It is not independent of the primary failure domain.** Much of HA's greenhouse data originates
  from the *same* ESP32→MQTT path; if HA is down or its recorder purged (default ~10 d), there is
  nothing to backfill from. A correlated ESP32/VLAN outage blanks both.
- **Latency** up to ~1 h+; not real-time.
- A failed CronJob is currently **invisible** (no freshness alert on the job itself).

**How it SHOULD work:** (a) document explicitly that HA backfill is analytics-only, never control;
(b) add a freshness alert on the backfill job (mirror the db-backup exporter); (c) extend HA
recorder retention for greenhouse entities beyond 10 d; (d) treat the correlated ESP32+HA outage
as unrecoverable-by-design and rely on firmware autonomy for *control* during it.

### 7.3 DB reliability
Single-replica StatefulSet, **no streaming replica, no WAL archiving, no PITR** (CNPG removed).
- **RPO ≤ 24 h** — only the nightly `pg_dump -Fc` (14 d retention, NFS PVC). The dump itself is
  hardened (30× retry to beat the netpol race, atomic `.partial` rename); a *missed* backup alerts
  at >26 h, but a *bad/unrestorable* backup does not.
- **RTO ≈ 10 min claimed but never practiced.** A TimescaleDB `-Fc` hypertable restore needs
  `timescaledb_pre/post_restore` — untested.
- `db-watchdog` is a **remount-race healer only** (deletes `verdify-db-0` on a narrow
  I/O-config CrashLoop signature ≥3 restarts) — not DB HA. Does not cover corruption, OOM,
  hung-but-alive, or disk-full.

### 7.4 Alerting coverage (failure → alert? → latency)
All app alerts are produced by monitors running **inside the ingestor's task loop** (300 s) →
`alert_log` → Slack. Good coverage for sensor-offline, push-failed, setpoint-unconfirmed (5/15 min),
band-drift (the post-2026-06-15 detector), band device/DB divergence, forecast/planner staleness.
**Critical gap:** there is **no out-of-band "ingestor down / writer absent" alert from this repo** —
the monitor that detects a dead writer runs *in* the writer. The only out-of-band signal is the
`verdify-writer-exporter` DaemonSet metric, whose alert rule lives in the observability repo.

---

## 8. Reliability gaps — prioritized

**P0**
1. **No DB replica / no PITR (RPO ≤24 h, RTO unpracticed).** Single point of total data loss.
   → re-arm CNPG (1+2 sync + Barman PITR) *or* interim WAL archiving to the dumps PVC (RPO→~5 min)
   + a quarterly `restore-test.sh` drill. (storage-infra / Jason-gated)
2. **No out-of-band writer-absent alert.** → add a Prometheus alert on
   `sum(verdify_esp32_writer_estab)==0` and on `time()-max(climate.ts)`, Slack-routed independent
   of the ingestor.

**P1**
3. **Ingestor on node6 + RWO Longhorn PVC = recurring multi-minute write-gaps.** → pin ingestor off
   node6 (affinity) and replace the RWO state PVC with `emptyDir` (STATE_DIR is regenerable cache)
   to kill the detach/reattach race. (k8s manifest; the sync is the Jason device-write gate)
4. **Writer-lease fence inert** (`VERDIFY_WRITER_LEASE_ENABLED=0`); firmware `max_connections:20`
   means two pods *could* both connect. → arm `#240` (also buys SIGTERM fast-release). (Jason-gated)
5. **No liveness/readiness probe on the single writer** — `ingestor-healthz.py` exists but is wired
   to nothing. → add a freshness-based liveness exec with a generous initialDelay.

**P2**
6. `setpoint_listener` LISTEN conn has no reconnect → wrap in a reconnect loop.
7. No bounded telemetry buffer/WAL during a DB outage → accept-and-document or add a small spool.
8. HA backfill has no freshness alert and is analytics-only → add an exporter; document scope.
9. BCDR doc references the dead `.150` VM paths → update to the `verdify-db-dumps` PVC + the real
   restore procedure.

---

## 9. Recommended simplification plan (phased, with gates)

**Phase 0 — truth & dead weight (autonomous, docs + PRs, low risk)**
- Fix `SERVICE_MAP.md` Dev/Staging drift (D4 — done this pass). Update BCDR doc (D6).
- Regenerate `db/schema.sql` from a post-179 dump (D5).
- Delete the 13 zero-reference scripts (§4.1) and the VM-era dirs (§4.2) via one PR.
- Delete the 34 dead Grafana JSONs + 20 `site-*.json` shadow copies (§4.5); fix the dangling
  `v_setpoint_compliance` reference.
- Retire the orphaned 145/146 band-function family via a `DROP`-only migration (§4.4), classified
  by `make migration-rollback-safety`.
- Retire/parameterize `generate-baseline-vs-iris-page.py` (§4.6).

**Phase 1 — CI hardening (autonomous, PRs)** — the §6.4 A/C items: gate the band-curve replay,
add the corpus-freshness gate, remove the soft-skip, run the broad test suite in CI, add the
registry↔firmware↔anchors guards (D7), strengthen the two weak guards, seal the OTA password.

**Phase 2 — reliability (mix; cluster syncs are Jason-gated)** — pin ingestor off node6 +
`emptyDir` state, out-of-band writer-absent alert, liveness probe, arm the writer lease (#240),
DB PITR via CNPG re-arm or WAL archiving + restore drill.

**Phase 3 — single-source-of-truth (larger, confirm scope)** — generate firmware `globals.yaml`
band defaults + registry `_FW2_*` from the `crop_band_anchors` seed; repoint compliance dashboards
to `setpoint_snapshot` device truth + a real divergence alarm; generalize the mig-176 lux fix to
all lighting params; close the fire-and-forget switch (F9); alert on partial/zero pushes (F10);
add the dashboard env banner; stand up `firmware-twin` as a continuous shadow (§6.4 B).

**Gates:** anything that runs `argocd app sync verdify-prod-dark`, touches firmware/OTA, arms the
writer lease, or changes DB topology is **Jason-gated**. DB PITR/storage is a **storage-infra**
dependency. Everything else (docs, code, dead-code deletion via PR, CI, dashboards) lands
autonomously on `main` keeping CI green.

---

## 10. Changes made in this audit pass
- **`docs/SERVICE_MAP.md`** — corrected the "Grafana band curve refresh" row from
  "Historical/retired" back to LIVE (verified against git + the running CronJob). (D4)
- **`docs/reviews/lane1-architecture-audit-2026-06-16.md`** — this Lane 1 audit.
- **`docs/RELEASE-CHECKLIST.md`** — the firmware + services + dashboards + docs release checklist.

No code, manifest, firmware, schema, or cluster state was changed. The deletions and gates in §9
are recommendations to be executed as scoped PRs (Phase 0/1) and gated work (Phase 2/3).

---

## 11. Corrections folded back into the prior data-path review
The data-path review (`docs/reviews/data-path-adversarial-review-2026-06-16.md`) says "if this doc
and the code disagree, the code wins — update this doc." This audit found three items to correct:
- **F15 — `mv_band_curve` is NOT orphaned.** It is refreshed every 10 min by
  `band-curve-refresh-cronjob.yaml` and read live (`v_band_curve`) by the live `site-home`
  dashboard. The real (smaller) finding is D12: the refresh is timer-based, not anchor-triggered.
- **F5 — `GL_CIRCUIT_TARGETS` was removed 2026-06-16** (`band_anchors.py` now only a removal
  comment). Only the registry-960 vs DB-policy split remains.
- **F2 / F17 — the live/dead dashboard mapping was inverted.** The LIVE `site-home` (baked into
  cm-0) reads `mv_band_curve`; the `setpoint_snapshot`+`fn_band_timeline` copy is the dead shadow.
  The compliance dashboards F2 cited as live (`control-loop`, `canonical-climate-control`) are
  **dead** (not provisioned). The corrected finding (D11) stands: the *live* compliance panels read
  the DB-derived band, not device truth.

---

*Read-only audit, 2026-06-16. Evidence: `kubectl` (ctx `vallery`, ns `verdify-prod`), repo
`file:line`, and the five investigator reports. [unverified] items need a live DB/device to confirm
magnitude. If this doc and the code disagree, the code wins — update this doc.*
