# STAGED — ArgoCD source flip for the verdify-prod device cutover (DO NOT auto-apply)

Status: STAGED for laptop-root + Jason gate. The device cutover is already LIVE
(applied imperatively 2026-06-07T17:15Z). This flip makes a future ArgoCD sync
REINFORCE the live armed shape instead of reverting it.

## Why a naive sync today is DANGEROUS

The live ArgoCD app is `verdify-prod-dark`, sourced from `overlays/prod-dark` and
currently `OutOfSync` (the namespace was armed imperatively). `argocd app sync
verdify-prod-dark` against the UNCHANGED dark overlay would REVERT the cutover:
- `deny-esp32-egress` re-adds `except: 192.168.10.0/24` → ESP32 unreachable
- `VERDIFY_DEVICE_WRITE_ENABLED` → 0
- ingestor replicas → 0
= re-dark the live greenhouse. NEVER sync verdify-prod-dark as-is.

## The flip (laptop-root performs; Jason-gated)

overlays/prod has now been reconciled (this PR) to the PROVEN live shape:
- `allow-ingestor-device-egress`: broad egress (0.0.0.0/0) + DNS — matches the live
  patched `deny-esp32-egress` (no regression vs the live ingestor's real egress)
- `VERDIFY_DEVICE_WRITE_ENABLED: "1"`, ingestor `replicas: 1`
- `/srv/verdify/state` writable emptyDir volume (#130) — fixes the dispatcher thrash

So a sync of an app pointing at overlays/prod REINFORCES the cutover.

### OPTION A (RECOMMENDED) — in-place repoint of the live app, then rename later

Lowest blast radius: keep the live `verdify-prod-dark` Application object (it already
owns the namespace + the verdify-db STS adoption) and just repoint its source path.
`deny-esp32-egress` is NOT in overlays/prod, so after the repoint a sync would try to
PRUNE it — but `prune` is false and these are MANUAL syncs, so it will show as
OutOfSync/extra, harmless; laptop-root removes `deny-esp32-egress` explicitly at sync
time (it is already functionally a no-op: live it is the broad-egress allow). The new
`allow-ingestor-device-egress` is created additively.

Exact live patch (laptop-root runs, AFTER this PR merges to main):

```
ssh jason@192.168.30.32 sudo k3s kubectl -n argocd patch application verdify-prod-dark \
  --type merge -p '{"spec":{"source":{"path":"deploy/k8s/overlays/prod"}}}'
```

Then a CONTROLLED, NON-PRUNING sync (review the diff first; never auto-prune the
verdify-db STS/PVC or the dumps PVC):

```
# Preview only — confirm it ADDS allow-ingestor-device-egress + state volume and
# does NOT scale ingestor to 0 / re-add the except:
argocd app diff verdify-prod-dark
# Apply additively (NO --prune). Removes the now-orphan deny-esp32-egress by hand:
argocd app sync verdify-prod-dark --resource '!networking.k8s.io:NetworkPolicy:deny-esp32-egress'
ssh jason@192.168.30.32 sudo k3s kubectl -n verdify-prod delete netpol deny-esp32-egress
```

Optional later (cosmetic): rename the App to `verdify-prod` via the OPTION B object.

### OPTION B — apply verdify-prod app CR, retire verdify-prod-dark (cleaner name)

The repo already carries `deploy/k8s/argocd/apps/verdify-prod.yaml` (source
overlays/prod, manual sync, prune:false), NOT yet applied to the cluster. To adopt
under the correctly-named app WITHOUT a destructive re-adopt, both apps must point at
the SAME live objects; ArgoCD would briefly contend for ownership. Sequence:

```
# 1. Apply the prod app (manual sync; it will show OutOfSync, do NOT sync yet)
ssh jason@192.168.30.32 sudo k3s kubectl apply -f deploy/k8s/argocd/apps/verdify-prod.yaml
# 2. Detach the dark app WITHOUT deleting cluster objects (orphan, not cascade):
ssh jason@192.168.30.32 sudo k3s kubectl -n argocd patch application verdify-prod-dark \
  --type merge -p '{"metadata":{"finalizers":null}}'
ssh jason@192.168.30.32 sudo k3s kubectl -n argocd delete application verdify-prod-dark --cascade=orphan
# 3. Controlled non-pruning sync of verdify-prod (same diff-review as Option A)
argocd app sync verdify-prod --resource '!networking.k8s.io:NetworkPolicy:deny-esp32-egress'
```

RISK: the `--cascade=orphan` + re-adopt window is fiddlier than Option A's single
patch. Prefer Option A unless Jason wants the clean `verdify-prod` name now.

## HARD pre-checks before EITHER flip (laptop-root)
1. This PR is merged into `main` (ArgoCD reads that ref).
2. `argocd app diff` reviewed: confirms ADD allow-ingestor-device-egress + state
   volume; confirms NO ingestor→0, NO except:192.168.10.0/24 re-added, NO DB prune.
3. Single-writer Recreate means the ingestor pod restarts ONCE (state volume) — the
   VM writer stays STOPPED, so still exactly one writer across the restart.
4. Re-probe ESP32 write path + DB freshness AFTER (durability gate, ≥10 min).
