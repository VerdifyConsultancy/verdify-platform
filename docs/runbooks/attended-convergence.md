# Attended convergence of verdify-prod-dark (one-time drift sync)

Prepared 2026-08-15 for #605 / #317; supports experiment rollout step 1
(epic #581, `docs/runbooks/experiment-rollout.md`). **This is a prepared
procedure for an ATTENDED operator run — nothing here is executed
automatically.**

## Why this exists

`verdify-prod-dark` (source `deploy/k8s/overlays/prod`, the REAL prod) last
Argo-synced 2026-06-23; live state since then was maintained by server-side
kubectl applies, leaving ~78 resources OutOfSync at main. Two broker
plan/apply attempts on 2026-08-15 reproduced **#317**: the submitted full-sync
operation is rewritten to a stale 2-resource selective scope
({CronJob verdify-db-backup, NetworkPolicy allow-db-from-backup}) — the
PreSync migrate hook runs (proven no-op; migrations 207–213 already ledgered),
the two stale-scope resources sync, everything else stays OutOfSync.

The unblock is **not** a bigger timeout; it is correcting the stale
selective-scope behavior (#317) and running ONE attended convergence.

## Preconditions (operator checks, in order)

1. No in-flight operation on the Application
   (`kubectl -n argocd get app verdify-prod-dark -o jsonpath='{.status.operationState.phase}'`
   must be terminal/absent).
2. Target revision = current `main` HEAD (record the exact 40-char SHA).
3. Fresh DB backup exists (< 24 h; `verdify-db-backup` CronJob output).
4. Ingestor is Ready with a stable restart count; note the count.
5. `scripts/experiment-verify.py ledger` passes (migrations current; no drift).

## The operation

Normal path first: submit a plain full sync (NO `--prune`, `resources`
**absent or empty**). Immediately verify the recorded operation:

- `kubectl -n argocd get app verdify-prod-dark -o jsonpath='{.status.operationState.operation.sync.resources}'`
  must be **absent or empty**.
- **If stale selectors appear or the result is narrow: STOP. Do not retry.**

Fallback (only after the STOP, reviewed separately): the #317
explicit-resource-vector workaround. Generate the vector from source with
`scripts/gen-sync-resource-vector.sh` (renders the overlay; no cluster
access), review it, and submit the operation with that explicit `resources:`
list per `docs/runbooks/laptop-operator.md` §2. Never include `--prune`.

## Success criteria (ALL required)

- Application at the **exact** recorded revision, **Synced + Healthy**.
- No in-flight operation remains.
- Broad result coverage: the operation result lists ~the full OutOfSync set,
  not a narrow subset.
- Single-writer invariant: exactly one ingestor pod (Recreate), writer lease
  held, ESP32 connection re-established.
- All workloads Ready; restart counts stable (allowing the expected one-time
  config-revision rollouts of api/mcp/ingestor/planner/setpoint-server).
  **These rollouts are NOT image-neutral**: the sync also advances all five
  app images from the 2026-07-11 builds still running live (pin `d4a8fafe`,
  source `707243cdf290`) to the 2026-07-28 builds pinned on main
  (pin `ae13b911`, source `03f433a9abfc`) — builds that passed CI at pin time
  but have **never run in prod**. Every pin between those two (incl.
  `eb6df638` 2026-07-11 and the setpoint-server re-pin era `20b367be`
  2026-07-13) was likewise never applied. See "Image delta at convergence"
  below before submitting the sync.
- Image-aware verification: for each of the five apps, the running pod's
  `.status.containerStatuses[].imageID` reports the **new** rendered digest
  (short forms in the table below), and per-service health holds:
  api endpoints respond, MCP is up and `experiment-verify.py config` reports
  `auth_mode`, ingestor holds the writer lease with ESP32 re-established,
  planner Deployment Ready with a clean log start, setpoint-server serves
  `/setpoints` from the DB backend. Grafana comes back on 12.4.5 (major
  upgrade from live 11.6.0) with dashboards rendering.
- PVC/Longhorn health: all PVCs Bound, no degraded Longhorn volumes.
- Public behavior: lab site + API endpoints respond normally.
- **Delayed stability proof**: +30 min re-probe of all of the above, plus one
  natural-cadence pass of the periodic jobs (backup/watchdogs/backfill).
  The re-probe repeats the image-aware checks: the five app pods still run
  the new digests with stable restart counts (no crash-loop on the
  never-run builds), the next ha-gap-backfill/vision runs complete on the
  new ingestor image, and Grafana 12.4.5 dashboards still render.

## Image delta at convergence (verified 2026-08-16)

Live spec images (READ-ONLY `kubectl get -n verdify-prod`) vs
`kubectl kustomize deploy/k8s/overlays/prod` at main `6cf99ee8`. Changed
rows (digests shortened; registry.vallery.net/verdifyconsultancy/ elided):

| Workload | Live image | Rendered at main | Delta |
| --- | --- | --- | --- |
| Deploy verdify-api | verdify-api@`984ba4936864` | verdify-api@`d28c45ce14c4` | **CHANGES** |
| Deploy verdify-mcp | verdify-mcp@`8cc8d7472f63` | verdify-mcp@`e3f37fc9ced6` | **CHANGES** |
| Deploy verdify-ingestor | verdify-ingestor@`fcd13ad7aa91` | verdify-ingestor@`083386240ff6` | **CHANGES** |
| Deploy verdify-planner | verdify-planner@`ff7de32d7de6` | verdify-planner@`735cb005d436` | **CHANGES** (rebuild; no source diff) |
| Deploy verdify-setpoint-server | verdify-setpoint-server@`f5ac817f42f9` | verdify-setpoint-server@`7278d148e398` | **CHANGES** (rebuild; schema-only) |
| Deploy verdify-grafana | grafana-oss:11.6.0 + renderer:3.12.6 | grafana:12.4.5@`26b8f35a9e4e` + renderer:v5.10.0@`c0eb7b915a18` | **CHANGES — major upgrade** |
| Deploy verdify-grafana-render-cache | (absent live) | nginx-unprivileged:1.29-alpine@`0c79d56aee56` | **NEW workload** |
| CronJob verdify-firmware-builder | esphome:2025.6.3 | esphome:2026.6.5 | **CHANGES** |
| CronJob verdify-ha-gap-backfill | verdify-ingestor@`fcd13ad7aa91` | verdify-ingestor@`083386240ff6` | **CHANGES** |
| CronJob verdify-vision | verdify-ingestor@`0ddced133253` | verdify-ingestor@`083386240ff6` | **CHANGES** |

Same on both sides (verified, not assumed): hermes-iris (`a7111ab1cc43`),
lab (baked `98ac23b6affa` x2 + lab-publisher-k3s `6450415420fb`), mqtt,
traefik v3.7.1, db-backup-exporter, db StatefulSet
(timescale 2.25.2-pg16), CronJobs band-curve-refresh / db-backup /
db-watchdog / writer-watchdog / lab-publisher, the PreSync migrate Job
(`71ff317bae58` — the 2026-08-15 broker attempts already ran the hook at
this digest), and every init container. No DaemonSets exist in the
namespace. The vision CronJob's live image (`0ddced133253`, source
`197460922aa9`, applied server-side ~2026-07-15) is *newer* than the app
deployments' builds but still behind main.

**Pin lineage.** Live app digests match pin `d4a8fafe` (2026-07-11, source
build `707243cdf290`) — the last pin actually applied to prod. Every later
pin on main was never synced (incl. `eb6df638` 2026-07-11, `20b367be`
2026-07-13); the current app pins come from `ae13b911` (2026-07-28, source
`03f433a9abfc`), lab-publisher-k3s from `c5607d9a` (2026-08-14, already
applied live). The `03f433a9abfc` builds passed CI at pin time but have
**never run in prod**.

**Per-service source delta** (`707243cdf290..03f433a9abfc`, scoped to each
image's build inputs per `.agent-fleet/ci.yaml` + Dockerfile COPY set):

- **api** (api/, verdify_schemas/, verdify_public/): `api/main.py` +~1.3k
  lines — public-output gating before atomic promotion (`f17e30ff`) with the
  new `verdify_public` package (atomic_directory, output_policy; fail-closed
  media/output validation); GPT-5.6 Sol model string surfaced (`cb2f57f7`);
  additive schema changes (ADR-0004 composite outcome score on
  ScorecardResponse `fa3a809e`, new stale-refresh alert envelope
  `c7afeee7`). Public API output paths behave differently under invalid
  content — fail closed instead of publishing.
- **mcp** (mcp/server.py, verdify_schemas/, slack_ops/): `outcome_kpi()`
  rewritten twice — concurrent fetches + one shared pinched/phase scan
  (#387 `bb915ded`), cycle counts reconciled against raw on-edges with
  partial/future days excluded (#389 `0a6ea4c2`) — **reported KPI numbers
  change**; scorecard moves to the db-202 single-day function killing the
  ~6 min floor (#498 `9d817935`; migration 202 already ledgered);
  ScorecardResponse wire contract pinned (#388). New image also reports
  `auth_mode` (the experiment-verify sub-check that is absent today).
- **ingestor** (ingestor/, scripts/, docs/, verdify_schemas/):
  `tasks/ha.py` site_content RAG corpus repointed off the dead iris-VM
  vault mount + stale-refresh alerting (#400 `c7afeee7`);
  `iris_planner.py` selects the GPT-5.6 Sol xhigh planner profile
  (`cb2f57f7`) — **planner model/profile changes ride this image, not the
  planner image**; large scripts/ + docs/ churn is baked in (vision script
  backport #436, public-output guards, lab publish tooling) and feeds the
  vision + ha-gap-backfill CronJobs, which exec from this image.
- **planner** (planner_graph/ only): **zero source diff** — pure rebuild
  (uv.lock frozen); lowest-risk of the five.
- **setpoint-server** (scripts/setpoint-server.py, verdify_schemas/):
  server script unchanged; only additive verdify_schemas changes ride
  along. The #447 DB-backend fence is already in the live build. Low risk.
- Non-app deltas rolling in the same sync: Grafana 11.6.0 → 12.4.5 major
  upgrade to a pinned runtime pair + new render-cache Deployment
  (#472 `37561dbb`); firmware-builder toolchain esphome 2025.6.3 → 2026.6.5
  (`6cf99ee8`) — first effect at the next scheduled build.

**Operator options** (decide before submitting the sync; evidence above):

- **(a) Accept the image advance as part of convergence**, with the
  image-aware verification in the success criteria. One operation; but the
  first prod run of the 2026-07-28 builds and the Grafana major upgrade
  land inside the same convergence window as ~78 resources of config drift.
- **(b) Make convergence image-neutral first**: pin main back to the live
  digests (`984ba493…`/`8cc8d747…`/`fcd13ad7…`/`ff7de32d…`/`f5ac817f…`,
  optionally grafana 11.6.0), converge on that revision, then advance the
  images as a separate, individually-verified step. Two operations; each
  change lands with a clean blast radius.
- **(c) Defer** until the image question is settled. Leaves #317/#605 and
  the experiment rollout blocked.

## Immediately after convergence

Resume the experiment rollout at the **first unproven runbook step** — do
NOT re-run proven steps (migrations 207–213 stay applied; the PreSync Job
verifying a current ledger is the expected no-op). Concretely:

1. `scripts/experiment-verify.py config` — must now PASS (config-revision
   annotations live, flags off, MCP auth_mode reported once the new images
   land; before new images the auth_mode field is absent — that sub-check
   documents itself).
2. Record evidence (exact revision, operation phase/result summary, verify
   output) on #605 and the epic #581.
3. Continue per `docs/runbooks/experiment-rollout.md` step 2 (shadow) once
   images exist (#599 / agents#3517, #606).
