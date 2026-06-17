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

## 5. Run log
- **T0** — orientation, cluster access (ctx `vallery`), storage hazard triaged + fixed
  (`6ed1c24`), L2/L3 mapping workflow launched. _(this section updated as work lands)_
