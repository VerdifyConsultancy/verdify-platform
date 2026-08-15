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
  config-revision rollouts of api/mcp/ingestor/planner/setpoint-server onto
  the SAME image digests).
- PVC/Longhorn health: all PVCs Bound, no degraded Longhorn volumes.
- Public behavior: lab site + API endpoints respond normally.
- **Delayed stability proof**: +30 min re-probe of all of the above, plus one
  natural-cadence pass of the periodic jobs (backup/watchdogs/backfill).

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
