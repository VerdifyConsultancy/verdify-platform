# L2 + L3 Overnight Sprint — Execution & Decision Record (2026-06-17)

**Operator:** laptop-root (autonomous overnight). **Goal (Jason):** finish, implement,
deploy, test, CI/CD, validate, and prove out all of L1/L2/L3 work, working
autonomously overnight without intervention, with permission to deploy, update CI/CD,
update project-namespace config, and migrate data.

This is the living handoff doc for the run. It records the two structural conflicts the
goal contains, how they were resolved, what is being done autonomously vs. what stays
gated, and the per-lane plan + progress. Read this first in the morning.

---

## 1. Two structural conflicts in the goal — resolved (with rationale)

### C1 — "prove out in our dev environment", but **there is no dev environment**
The `verdify-dev` proving env AND the staging overlay were **decommissioned and deleted
2026-06-16** (ns/DB/PVC/PV/ArgoCD app gone; `overlays/{dev,staging}` removed). Verified:
single-env prod only. There is nothing to "prove out in dev."

**Resolution.** The handover thesis says *"the greenhouse itself should be treated as a
dev/lab environment, but the control plane still needs deterministic safety."* So the
proving ground for L2/L3 (firmware + climate) is the repo's **deterministic offline
harness** — `make test-firmware`, `make firmware-invariants`, `make firmware-replay[-band]`,
the `tests/` logic suite, and `kubectl kustomize` render checks — **not** an OTA to the
live device. This is the repo's own designed proving mechanism and is the only safe
reading: pushing a firmware rewrite to a live greenhouse full of living orchids overnight,
unattended, is exactly the failure mode the firmware-freeze rules exist to prevent
(2026-04-21 incident; the orchid overnight regression RH 39%→84%). The 48-hour bake gate
*also* makes "prove via OTA overnight" literally impossible.

### C2 — "full permission to deploy", but device/DB/NAS actions are codified hard gates
`AGENTS.md`/`CLAUDE.md` (which OVERRIDE defaults) make Jason the human gate for firmware
OTA, the prod ArgoCD sync that touches the live writer, destructive prod DB ops, and NAS
control-plane changes. `make firmware-deploy` itself aborts on open critical alerts and
enforces ≤1 OTA/week + 48h bake.

**Resolution — the autonomy boundary for this run:**

| Surface | Tonight |
|---|---|
| Firmware **code/logic/docs/tests**, climate model, CI gates, schemas/migrations (rollback-safe), k8s manifests, dashboards JSON, lab generators | **Autonomous** — land on `main`, CI green, proven offline |
| Non-device-affecting prod deploy (grafana dashboards, api/mcp/planner/lab, CronJobs, alerts) via `kubectl apply`/manual sync | **Autonomous** per the explicit grant (reversible, no writer/DB-data touch) |
| Firmware **OTA** to the live ESP32 | **GATED** — fully prepared (PR-ready + offline-proven), one-command go-button, NOT executed |
| Prod `argocd sync` that restarts the **single writer** (ingestor) / DB topology | **GATED** — staged + documented |
| DB-destructive ops, NAS/iSCSI control-plane, credential rotation, public DNS/edge | **GATED** |

Net: everything that can be *proven offline and is reversible* ships tonight; the
irreversible, plant-/data-affecting steps are taken to the edge of the gate with a clear
go-button. No loss of progress; the device/data risk is not taken unattended.

---

## 2. Storage hazard handled first (live-greenhouse safety) — DONE

A concurrent `laptop-root` session committed + pushed `ad4c755 "Move workload PVCs to
explicit storage tiers"`. Two of its edits were unsafe; I landed a **forward fix**
(`6ed1c24`, pushed) — no history rewrite, no live-cluster effect (manual-sync gated):

- **lab-publisher RWX→RWO node-local: reverted.** The prod lab cache is shared by the
  publisher CronJob + 2 hostname-spread nginx replicas; RWO node-local mounts on one node
  only → cross-node replicas can't mount → `lab.verdify.ai` degrades. Live PVC is also
  RWX + immutable, so the RWO manifest could never sync. Restored RWX.
- **DB → synology-iscsi-ssd: reverted.** The **live `verdify-db` STS template is
  `longhorn-nvme-rwo`** (the bound PVC was manually swapped to synology-iscsi-ssd, but the
  VCT is immutable + frozen). Pinning git to synology-iscsi-ssd makes ArgoCD try to patch
  the immutable field → **sync failure**. Git must stay `longhorn-nvme-rwo`. Retier needs a
  gated STS recreate.
- **Incident root cause CORRECTED.** `synology-csi-controller-0` logs show
  `Number of target reach limit` — the **DSM iSCSI target-count cap is exhausted**, failing
  NEW provisioning on **BOTH** `/volume1` and `/volume2`. It is **NOT** a "/volume1 SSD
  died" hardware failure (the repo comment misdiagnosed it). Aggravated by ephemeral CI
  iSCSI churn (`agent-fleet-ci *-workdir` PVCs). **Operational consequence:** `verdify-db`
  is healthy only because its iSCSI session predates the cap; a reschedule could wedge.
  Filed storage-infra P0 (reclaim targets / raise cap) + gated DB-retier request in
  `COORDINATION_REQUESTS.md`; documented in `SERVICE_MAP.md`.

---

## 3. L1 status (context) — Done, with gated residuals
L1 (#343) is closed/Done (audit + Phase 0/1/2 + prod deploy). Residuals are all gated or
cross-lane: gated `argocd sync` for the ingestor-resilience patch + ha-gap-backfill image;
monitoring-stack writer-absent alert; DB PITR; writer-lease arm; and the iSCSI target-cap
fix above. These are tracked in `COORDINATION_REQUESTS.md` and the audit §8/§9.

---

## 4. L2 + L3 plan
A read-only mapping workflow (`l2-l3-firmware-climate-map`) is fanning out 9 investigators
over the firmware (~10k lines) + design docs to produce an evidence-backed actual-vs-
acceptance map, adversarially verified so I do **not** "fix" load-bearing live logic. The
synthesized work plan drives execution. Acceptance criteria:

- **L2 #344:** (1) responsibilities documented to climate/lighting/irrigation only;
  (2) relay transitions + safety override explicit; (3) 72h disconnected defined AND
  tested; (4) AI tunables cannot override hard rails or core FSM; (5) crop assumptions
  removed/isolated.
- **L3 #345:** (1) diurnal curve math formalized + tested; (2) bands + hysteresis
  documented; (3) mechanical transitions avoid energy-waste contradictions; (4) outdoor-air
  use explicit; (5) compliance distinguishes controller-miss vs physical-impossibility.

Progress + the work plan are appended below as the run proceeds.

---

## 4b. Turn-2 closeout (CI green + prod state) — 2026-06-17

**CI is GREEN on `main`.** Two failures (one mine, one pre-existing) were fixed
(`28a3050`): ruff-formatted the new contract tests, and de-flaked
`test_11_planner_milestones` (a time-dependent exact-set assertion that failed
near local midnight because `_compute_milestones` surfaces the `MIDNIGHT` trigger
only in its catch-up window — fixed to MIDNIGHT-optional). Verified
`success` on HEAD (`2052fc2`, `28a3050`).

**Prod is healthy and serving.** The concurrent storage operator COMPLETED their
live tiering migration (verified): `verdify-db` STS recreated onto
`synology-iscsi-ssd` (reusing the existing Bound PV — no new iSCSI target), lab
cache → `node-local-temp-rwo` RWO with the 2 nginx replicas co-locating (soft
spread) and **lab-publisher now succeeding** (3 recent jobs Complete ~2 min,
fixing the node6 failures). DB up, ingestor (sole writer) up 3h+/0 restarts,
hermes up. I reconciled **git == live** for DB + lab (`2052fc2`, git-only, no
cluster mutation, no DB roll) so those are no longer a real diff.

**ArgoCD `verdify-prod-dark` — DEPLOYED to Synced / Healthy (turn-3).** _(The
turn-2 "deliberately not force-synced" note is SUPERSEDED — on the user's repeated
explicit clearance to deploy, I drove the app to fully Synced/Healthy, safely,
verifying every step.)_ A `--dry-run` diff confirmed the changes were: the
`verdify-db` STS = **metadata-only (no roll)**; lab/ingestor-state PVCs = metadata
only; and crucially the **`verdify-ingestor` Deployment `emptyDir → durable PVC`**
(the L1 residual "revert the emptyDir stopgap → durable verdify-ingestor-state",
*deployed this turn*). Executed phased with datapath verification:
1. Synced the ingestor Deployment → writer rolled to the durable PVC (mounted the
   already-Bound `verdify-ingestor-state`, existing iSCSI target → cap-safe), came
   back **1/1 Ready**, datapath resumed (77s gap → ~20s fresh). Spool was drained
   (DB up) → no data loss. Closes the L1 emptyDir residual.
2. Realized the **hermes node-local** migration (the operator's intended tier;
   hermes is the planner gateway, NOT in the device path, run-state regenerable):
   scaled hermes→0, deleted the iscsi-ssd PVC (Retain → PV released), full sync
   created the node-local PVC + scaled back to 1; hermes re-seeded config + came up
   **1/1 Ready** on node-local (2.7 GB image re-pull on the new node took ~2 min).
3. Fixed the **`verdify-grafana` PDB** bug (the app's last Degraded resource): its
   `{component: grafana}` selector also matched the `verdify-band-curve-refresh`
   Job pods → `jobs.batch does not implement the scale subresource` →
   `DisruptionAllowed=False`. Added a `batch.kubernetes.io/job-name DoesNotExist`
   matchExpression (`36382e9`), delete+recreated the PDB → `DisruptionAllowed=True`.

**Result: `verdify-prod-dark` = Synced / Healthy**, all 16 workloads 1/1/2-2 Running
0 restarts, **greenhouse writer datapath fresh** throughout (DB never rolled — its
sync was metadata-only). The DB STS recreate + lab/hermes tiering are git==live.

### Remaining genuinely-gated (ready-to-execute; correctly held)
- **DB PITR (audit §8 P0).** WAL archiving to the `verdify-db-dumps` NFS PVC
  (`archive_mode=on` needs a DB **restart**) + pg_basebackup + a restore drill.
  Cap-unblocked (NFS). **Held**: a full PITR setup on the *single live greenhouse
  DB* (no replica) is a verify-as-you-go project where a slip fills disk and downs
  the data plane — an attended maintenance window, not an unattended overnight
  change. This is the one item where Track A correctly outranks the deploy clearance.
  Ready plan in `COORDINATION_REQUESTS.md`.
- **iSCSI target-count cap (P0).** Pressure REDUCED this turn (lab + hermes now on
  node-local, off iSCSI). Reclaiming orphaned DSM targets / raising the cap is NAS
  control-plane (CHANGE-GATING rule + shared-fleet blast radius) → storage-infra/Jason.
- **Out-of-band writer-absent alert.** Genuinely external — no Prometheus operator
  in THIS cluster; belongs to `jvallery/monitoring-stack`. Spec handed off.
- **Firmware OTA.** N/A — zero firmware-binary diff this sprint; nothing to OTA.

## 5. Run log
- **T0** — orientation, cluster access (ctx `vallery`), storage hazard triaged + fixed
  (`6ed1c24`), L2/L3 mapping workflow launched.
- **T1 — L2 #344 + L3 #345 COMPLETE.** The map (9 parallel investigators, adversarially
  verified) found the control core already correct; the gaps were docs + test rails, all
  offline-provable, zero firmware-logic change, zero device-gated work to reach acceptance.
  Delivered 9 work items:
  - **W3** invariants #25 (SAFETY_HEAT cold rail) / #26 (SENSOR_FAULT all-off) — `d8ed531`.
  - **W4** native 72h-disconnected determinism + fallback-phase + reboot-idempotence tests — `d8ed531`.
  - **W6** crop-agnostic firmware guard test; **W9** compliance-feasibility classifier test — `417531e`.
  - **W1/W8/W5/W6d/W7d** `docs/firmware-fsm-spec.md` (authoritative spec + AC traceability);
    **W2** 5s→~1s doc fix — `38c6e08`.
  - **CI** wired the two new contract tests into the `logic-tests` allow-list — `ffc89b9`.
  - **Validation (offline + live):** `make lint` clean; `make test-firmware` 222/0;
    `make firmware-invariants` 193,525 rows all pass; 13 new contract tests pass;
    `kustomize build overlays/prod` OK; `migration-rollback-safety` clean. Live prod
    read-only confirm: `fn_zone_band_grade` (L3-AC5) + `fn_crop_band_value` (L3-AC1)
    present and emitting feasibility labels on real data. `firmware-check` is
    env-limited locally (esphome CLI not in the laptop venv) — N/A, no firmware config changed.
  - **Tracking:** AGENT_LANE / EPICS / MILESTONES / SPRINTS / PROJECT_BOARD / HISTORY updated;
    issues #344/#345 closed with evidence; project memory updated.
  - **Gated remainder (NOT acceptance gates):** arming the new test rails on the live ESP32
    is a future Jason-gated OTA; the live SQL compliance eval over full history is DB-gated;
    the storage-infra iSCSI target-cap fix + gated DB-retier are filed in `COORDINATION_REQUESTS.md`.
